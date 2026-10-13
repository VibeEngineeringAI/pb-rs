//! Concrete pooled HTTP transport with explicit Go-compatible wire policy.
use bytes::Bytes;
use http::{
    Method, Request, StatusCode, Uri,
    header::{
        ACCEPT_ENCODING, AUTHORIZATION, CONTENT_ENCODING, CONTENT_LENGTH, HeaderMap, HeaderValue,
        PROXY_AUTHORIZATION, RANGE, USER_AGENT,
    },
};
use http_body_util::{BodyDataStream, BodyExt, Full};
use hyper::{Error as HyperError, body::Incoming};
#[cfg(not(target_os = "linux"))]
use hyper_rustls::ConfigBuilderExt;
use hyper_rustls::{HttpsConnector, HttpsConnectorBuilder};
use hyper_util::{
    client::{
        legacy::{
            Client,
            connect::{Connected, Connection, HttpConnector},
        },
        proxy::matcher::{Intercept, Matcher},
    },
    rt::{TokioExecutor, TokioIo, TokioTimer},
};
use std::{
    error::Error,
    fmt,
    future::Future,
    io,
    net::IpAddr,
    pin::Pin,
    sync::Arc,
    task::{Context, Poll},
    time::Duration,
};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, ReadBuf};
use tower_service::Service;

type BoxError = Box<dyn Error + Send + Sync>;
type HttpClient = Client<HttpsConnector<ProxyConnector>, Full<Bytes>>;
type PlainClient = Client<ProxyConnector, Full<Bytes>>;
type TlsStore = Arc<tokio::sync::OnceCell<Arc<rustls::ClientConfig>>>;

pub struct Transport {
    plain: PlainClient,
    https: tokio::sync::OnceCell<HttpClient>,
    tls: TlsStore,
    proxy: ProxyPolicy,
}
#[derive(Debug)]
pub struct TransportError {
    pub url: String,
    source: BoxError,
    body: bool,
    proxy: Option<Box<Uri>>,
}
impl TransportError {
    fn new(url: impl Into<String>, source: impl Into<BoxError>) -> Self {
        Self {
            url: url.into(),
            source: source.into(),
            body: false,
            proxy: None,
        }
    }
    fn body(url: impl Into<String>, source: impl Into<BoxError>) -> Self {
        Self {
            url: url.into(),
            source: source.into(),
            body: true,
            proxy: None,
        }
    }
    fn with_proxy(mut self, proxy: Option<Uri>) -> Self {
        self.proxy = proxy.map(Box::new);
        self
    }
    pub fn is_body(&self) -> bool {
        self.body
    }
    pub fn is_timeout(&self) -> bool {
        let mut cause: Option<&(dyn Error + 'static)> = Some(self.source.as_ref());
        while let Some(error) = cause {
            if error
                .downcast_ref::<io::Error>()
                .is_some_and(|e| e.kind() == io::ErrorKind::TimedOut)
            {
                return true;
            }
            cause = error.source();
        }
        false
    }
}
impl fmt::Display for TransportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut cause: Option<&(dyn Error + 'static)> = Some(self.source.as_ref());
        let mut last = self.source.to_string();
        while let Some(error) = cause {
            if let Some(io) = error.downcast_ref::<io::Error>()
                && io.kind() == io::ErrorKind::ConnectionRefused
            {
                let uri = self.proxy.as_deref().cloned().or_else(|| {
                    ascii_authority(&strip_userinfo(&self.url).0)
                        .parse::<Uri>()
                        .ok()
                });
                let host = uri.as_ref().and_then(Uri::host).unwrap_or("<invalid host>");
                let port = uri.as_ref().and_then(Uri::port_u16).unwrap_or(
                    if uri.as_ref().and_then(Uri::scheme_str) == Some("https") {
                        443
                    } else {
                        80
                    },
                );
                let prefix = if self.proxy.is_some() {
                    "proxyconnect tcp: "
                } else {
                    ""
                };
                return write!(
                    f,
                    "{prefix}dial tcp {host}:{port}: connect: connection refused"
                );
            }
            last = error.to_string();
            cause = error.source();
        }
        if last.contains("UnknownIssuer") {
            return f.write_str(
                "tls: failed to verify certificate: x509: certificate signed by unknown authority",
            );
        }
        if last.contains("unexpected end of file")
            || last.contains("end of file before message length reached")
        {
            return f.write_str("unexpected EOF");
        }
        let lower = last.to_ascii_lowercase();
        if lower.contains("gzip") && lower.contains("header") {
            return f.write_str("gzip: invalid header");
        }
        if lower.contains("crc") || lower.contains("checksum") {
            return f.write_str("gzip: invalid checksum");
        }
        f.write_str(&last)
    }
}
impl Error for TransportError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        Some(self.source.as_ref())
    }
}

impl Transport {
    pub fn new() -> Result<Self, TransportError> {
        Self::with_policy(environment_proxy())
    }
    fn with_policy(proxy: ProxyPolicy) -> Result<Self, TransportError> {
        let tls = Arc::new(tokio::sync::OnceCell::new());
        let connector = ProxyConnector::new(proxy.clone(), tls.clone());
        let plain = Client::builder(TokioExecutor::new())
            .pool_timer(TokioTimer::new())
            .timer(TokioTimer::new())
            .pool_idle_timeout(Duration::from_secs(90))
            .pool_max_idle_per_host(2)
            .retry_canceled_requests(false)
            .build(connector);
        Ok(Self {
            plain,
            https: tokio::sync::OnceCell::new(),
            tls,
            proxy,
        })
    }
    async fn https_client(&self) -> Result<&HttpClient, BoxError> {
        self.https
            .get_or_try_init(|| async {
                let config = tls_config(&self.tls).await?;
                let connector = ProxyConnector::new(self.proxy.clone(), self.tls.clone());
                let connector = HttpsConnectorBuilder::new()
                    .with_tls_config((*config).clone())
                    .https_or_http()
                    .enable_http1()
                    .enable_http2()
                    .wrap_connector(connector);
                Ok(Client::builder(TokioExecutor::new())
                    .pool_timer(TokioTimer::new())
                    .timer(TokioTimer::new())
                    .pool_idle_timeout(Duration::from_secs(90))
                    .pool_max_idle_per_host(2)
                    .retry_canceled_requests(false)
                    .build(connector))
            })
            .await
    }
    pub async fn send(
        &self,
        method: &Method,
        raw_url: &str,
        headers: &HeaderMap,
        body: &Option<Vec<u8>>,
    ) -> Result<Response, TransportError> {
        let url_without_fragment = raw_url.split('#').next().unwrap_or(raw_url);
        let (uri_string, userinfo) = strip_userinfo(url_without_fragment);
        let uri_string = ascii_authority(&uri_string);
        let uri: Uri = uri_string
            .parse()
            .map_err(|error| TransportError::new(raw_url, error))?;
        let mut request = Request::builder()
            .method(method.clone())
            .uri(uri.clone())
            .body(Full::new(Bytes::from(body.clone().unwrap_or_default())))
            .map_err(|error| TransportError::new(raw_url, error))?;
        *request.headers_mut() = headers.clone();
        request
            .headers_mut()
            .insert(USER_AGENT, HeaderValue::from_static("Go-http-client/1.1"));
        if !request.headers().contains_key(AUTHORIZATION)
            && let Some(userinfo) = userinfo
        {
            use base64::Engine;
            let encoded = base64::engine::general_purpose::STANDARD.encode(userinfo);
            let mut value = HeaderValue::from_str(&format!("Basic {encoded}"))
                .expect("base64 is a valid header");
            value.set_sensitive(true);
            request.headers_mut().insert(AUTHORIZATION, value);
        }
        let gzip = method != Method::HEAD
            && !headers.contains_key(ACCEPT_ENCODING)
            && !headers.contains_key(RANGE);
        if gzip {
            request
                .headers_mut()
                .insert(ACCEPT_ENCODING, HeaderValue::from_static("gzip"));
        }
        if method == Method::POST
            || method == Method::PUT
            || method == Method::PATCH
            || body.as_ref().is_some_and(|b| !b.is_empty())
        {
            let length = body.as_ref().map_or(0, Vec::len);
            request.headers_mut().insert(
                CONTENT_LENGTH,
                HeaderValue::from_str(&length.to_string()).expect("integer is a valid header"),
            );
        }
        if uri.scheme_str() == Some("http")
            && let Some(intercept) = proxy_for(&self.proxy, &uri)
                .filter(|proxy| matches!(proxy.uri().scheme_str(), Some("http" | "https")))
            && let Some(auth) = intercept.basic_auth()
        {
            request
                .headers_mut()
                .insert(PROXY_AUTHORIZATION, auth.clone());
        }
        let response = if uri.scheme_str() == Some("https") {
            self.https_client()
                .await
                .map_err(|e| TransportError::new(raw_url, e))?
                .request(request)
                .await
        } else {
            self.plain.request(request).await
        }
        .map_err(|e| {
            TransportError::new(raw_url, e)
                .with_proxy(proxy_for(&self.proxy, &uri).map(|proxy| proxy.uri().clone()))
        })?;
        let (mut parts, body) = response.into_parts();
        let reason = parts
            .extensions
            .get::<hyper::ext::ReasonPhrase>()
            .map(|reason| String::from_utf8_lossy(reason.as_bytes()).into_owned())
            .unwrap_or_else(|| parts.status.canonical_reason().unwrap_or_default().into());
        let compressed = gzip
            && parts
                .headers
                .get(CONTENT_ENCODING)
                .is_some_and(|v| v.as_bytes().eq_ignore_ascii_case(b"gzip"));
        if compressed {
            parts.headers.remove(CONTENT_ENCODING);
            parts.headers.remove(CONTENT_LENGTH);
        }
        let (body, gzip, gzip_input) = if compressed {
            let started = Arc::new(std::sync::atomic::AtomicBool::new(false));
            let reader = tokio_util::io::StreamReader::new(BodyStream(
                BodyDataStream::new(body),
                started.clone(),
            ));
            let mut decoder = async_compression::tokio::bufread::GzipDecoder::new(
                tokio::io::BufReader::new(reader),
            );
            decoder.multiple_members(true);
            (None, Some(decoder), Some(started))
        } else {
            (Some(body), None, None)
        };
        Ok(Response {
            status: parts.status,
            reason,
            headers: parts.headers,
            body,
            gzip,
            gzip_input,
            url: raw_url.into(),
        })
    }
}

struct BodyStream(BodyDataStream<Incoming>, Arc<std::sync::atomic::AtomicBool>);
impl futures_core::Stream for BodyStream {
    type Item = io::Result<Bytes>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let polled = Pin::new(&mut self.0).poll_next(cx);
        if let Poll::Ready(Some(Ok(bytes))) = &polled
            && !bytes.is_empty()
        {
            self.1.store(true, std::sync::atomic::Ordering::Relaxed);
        }
        polled.map(|item| item.map(|result| result.map_err(io::Error::other)))
    }
}
type GzipReader = async_compression::tokio::bufread::GzipDecoder<
    tokio::io::BufReader<tokio_util::io::StreamReader<BodyStream, Bytes>>,
>;
pub struct Response {
    status: StatusCode,
    reason: String,
    headers: HeaderMap,
    body: Option<Incoming>,
    gzip: Option<GzipReader>,
    gzip_input: Option<Arc<std::sync::atomic::AtomicBool>>,
    url: String,
}
impl Response {
    pub fn status(&self) -> StatusCode {
        self.status
    }
    pub fn reason(&self) -> &str {
        &self.reason
    }
    pub fn headers(&self) -> &HeaderMap {
        &self.headers
    }
    pub fn content_length(&self) -> Option<u64> {
        self.headers
            .get(CONTENT_LENGTH)?
            .to_str()
            .ok()?
            .parse()
            .ok()
    }
    pub async fn chunk(&mut self) -> Result<Option<Bytes>, TransportError> {
        self.chunk_limit(8192).await
    }
    pub async fn chunk_limit(&mut self, maximum: usize) -> Result<Option<Bytes>, TransportError> {
        if let Some(gzip) = &mut self.gzip {
            let mut buffer = vec![0; maximum];
            let count = match gzip.read(&mut buffer).await {
                Err(error)
                    if error.kind() == io::ErrorKind::UnexpectedEof
                        && self.gzip_input.as_ref().is_some_and(|started| {
                            !started.load(std::sync::atomic::Ordering::Relaxed)
                        }) =>
                {
                    return Ok(None);
                }
                result => result.map_err(|error| TransportError::body(&self.url, error))?,
            };
            if count == 0 {
                return Ok(None);
            }
            buffer.truncate(count);
            return Ok(Some(buffer.into()));
        }
        let Some(body) = self.body.as_mut() else {
            return Ok(None);
        };
        while let Some(frame) = body.frame().await {
            let frame = frame.map_err(|e: HyperError| TransportError::body(&self.url, e))?;
            if let Ok(data) = frame.into_data() {
                return Ok(Some(data));
            }
        }
        Ok(None)
    }
    pub async fn bytes(mut self) -> Result<Bytes, TransportError> {
        let mut bytes = Vec::new();
        while let Some(chunk) = self.chunk().await? {
            bytes.extend_from_slice(&chunk);
        }
        Ok(bytes.into())
    }
}

fn env_value(upper: &str, lower: &str) -> String {
    std::env::var(upper)
        .ok()
        .filter(|v| !v.is_empty())
        .or_else(|| std::env::var(lower).ok())
        .unwrap_or_default()
}
fn go_proxy_url(raw: &str) -> String {
    let Some((scheme, rest)) = raw.split_once("://") else {
        return raw.into();
    };
    if ["http", "https", "socks5", "socks5h"]
        .iter()
        .any(|allowed| scheme.eq_ignore_ascii_case(allowed))
    {
        return raw.into();
    }
    // Go treats other schemes as plaintext HTTP proxies, with port zero when
    // no default is defined. Preserve that routing instead of matcher bypass.
    let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let authority = &rest[..end];
    let host = authority.rsplit('@').next().unwrap_or_default();
    let has_port = if host.starts_with('[') {
        host.find(']')
            .is_some_and(|end| host[end + 1..].starts_with(':'))
    } else {
        host.contains(':')
    };
    format!(
        "http://{authority}{}{}",
        if has_port { "" } else { ":0" },
        &rest[end..]
    )
}
#[derive(Clone)]
struct ProxyPolicy {
    matcher: Arc<Matcher>,
    no_proxy: String,
    cgi: bool,
}
fn environment_proxy() -> ProxyPolicy {
    ProxyPolicy {
        matcher: Arc::new(
            Matcher::builder()
                .http(go_proxy_url(&env_value("HTTP_PROXY", "http_proxy")))
                .https(go_proxy_url(&env_value("HTTPS_PROXY", "https_proxy")))
                .build(),
        ),
        no_proxy: env_value("NO_PROXY", "no_proxy"),
        cgi: std::env::var("REQUEST_METHOD").is_ok_and(|value| !value.is_empty()),
    }
}
fn proxy_for(policy: &ProxyPolicy, uri: &Uri) -> Option<Intercept> {
    let host = uri.host()?.trim_start_matches('[').trim_end_matches(']');
    if host.eq_ignore_ascii_case("localhost")
        || host.parse::<IpAddr>().is_ok_and(|ip| ip.is_loopback())
    {
        return None;
    }
    if no_proxy_matches(
        &policy.no_proxy,
        host,
        uri.port_u16()
            .unwrap_or(if uri.scheme_str() == Some("https") {
                443
            } else {
                80
            }),
    ) {
        return None;
    }
    policy.matcher.intercept(uri)
}
fn no_proxy_matches(no_proxy: &str, host: &str, port: u16) -> bool {
    let host = host.to_ascii_lowercase();
    for entry in no_proxy.split(',').map(str::trim).filter(|s| !s.is_empty()) {
        if entry == "*" {
            return true;
        }
        if let Some((network, prefix)) = entry.split_once('/') {
            if let (Ok(network), Ok(address), Ok(prefix)) = (
                network.parse::<IpAddr>(),
                host.parse::<IpAddr>(),
                prefix.parse::<u8>(),
            ) {
                let matches = match (network, address) {
                    (IpAddr::V4(n), IpAddr::V4(a)) if prefix <= 32 => {
                        prefix == 0
                            || u32::from(n) >> (32 - prefix) == u32::from(a) >> (32 - prefix)
                    }
                    (IpAddr::V6(n), IpAddr::V6(a)) if prefix <= 128 => {
                        prefix == 0
                            || u128::from(n) >> (128 - prefix) == u128::from(a) >> (128 - prefix)
                    }
                    _ => false,
                };
                if matches {
                    return true;
                }
            }
            continue;
        }
        let (name, required_port) = if entry.starts_with('[') {
            if let Some(end) = entry.find(']') {
                (&entry[1..end], entry[end + 1..].strip_prefix(':'))
            } else {
                (entry, None)
            }
        } else if entry.bytes().filter(|b| *b == b':').count() == 1 {
            let (name, port) = entry.rsplit_once(':').unwrap();
            (name, Some(port))
        } else {
            (entry, None)
        };
        if required_port.is_some_and(|required| required.parse::<u16>().ok() != Some(port)) {
            continue;
        }
        let name = name.to_ascii_lowercase();
        let name = name.strip_prefix('*').unwrap_or(&name);
        if name.starts_with('.') {
            if host.ends_with(name) {
                return true;
            }
        } else if host == name
            || host
                .strip_suffix(name)
                .is_some_and(|prefix| prefix.ends_with('.'))
        {
            return true;
        }
    }
    false
}
fn strip_userinfo(raw: &str) -> (String, Option<Vec<u8>>) {
    let Some(start) = raw.find("//").map(|i| i + 2) else {
        return (raw.into(), None);
    };
    let end = raw[start..]
        .find(['/', '?'])
        .map(|i| start + i)
        .unwrap_or(raw.len());
    let Some(at) = raw[start..end].rfind('@').map(|i| start + i) else {
        return (raw.into(), None);
    };
    fn decode(raw: &str) -> Vec<u8> {
        let mut decoded = Vec::new();
        let bytes = raw.as_bytes();
        let mut i = 0;
        while i < bytes.len() {
            if bytes[i] == b'%'
                && i + 2 < bytes.len()
                && let Ok(byte) = u8::from_str_radix(&raw[i + 1..i + 3], 16)
            {
                decoded.push(byte);
                i += 3;
                continue;
            }
            decoded.push(bytes[i]);
            i += 1;
        }
        decoded
    }
    let (username, password) = raw[start..at]
        .split_once(':')
        .unwrap_or((&raw[start..at], ""));
    let mut decoded = decode(username);
    decoded.push(b':');
    decoded.extend(decode(password));
    (
        format!("{}{}", &raw[..start], &raw[at + 1..]),
        Some(decoded),
    )
}
fn ascii_authority(raw: &str) -> String {
    let Some(start) = raw.find("//").map(|i| i + 2) else {
        return raw.into();
    };
    let end = raw[start..]
        .find(['/', '?'])
        .map(|i| start + i)
        .unwrap_or(raw.len());
    let authority = &raw[start..end];
    if authority.is_ascii() && !authority.contains('%') {
        return raw.into();
    }
    let Ok(url) = url::Url::parse(raw) else {
        return raw.into();
    };
    let Some(host) = url.host_str() else {
        return raw.into();
    };
    let port = authority
        .rsplit_once(':')
        .filter(|(_, port)| port.bytes().all(|b| b.is_ascii_digit()))
        .map(|(_, port)| format!(":{port}"))
        .unwrap_or_default();
    format!("{}{host}{port}{}", &raw[..start], &raw[end..])
}

trait AsyncIo: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> AsyncIo for T {}
pub struct ProxyStream {
    io: Box<dyn AsyncIo>,
    proxy: bool,
    handshake: std::sync::Mutex<Option<Pin<Box<tokio::time::Sleep>>>>,
}
impl ProxyStream {
    fn check_handshake(&mut self, cx: &mut Context<'_>) -> io::Result<()> {
        if self
            .handshake
            .get_mut()
            .unwrap()
            .as_mut()
            .is_some_and(|timer| timer.as_mut().poll(cx).is_ready())
        {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "net/http: TLS handshake timeout",
            ));
        }
        Ok(())
    }
}
impl AsyncRead for ProxyStream {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        self.check_handshake(cx)?;
        Pin::new(&mut *self.io).poll_read(cx, buf)
    }
}
impl AsyncWrite for ProxyStream {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        self.check_handshake(cx)?;
        Pin::new(&mut *self.io).poll_write(cx, buf)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut *self.io).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut *self.io).poll_shutdown(cx)
    }
}
impl Connection for ProxyStream {
    fn connected(&self) -> Connected {
        self.handshake.lock().unwrap().take();
        Connected::new().proxy(self.proxy)
    }
}

#[derive(Clone)]
pub struct ProxyConnector {
    tcp: HttpConnector,
    matcher: ProxyPolicy,
    tls: TlsStore,
}
impl ProxyConnector {
    fn new(matcher: ProxyPolicy, tls: TlsStore) -> Self {
        let mut tcp = HttpConnector::new();
        tcp.enforce_http(false);
        tcp.set_connect_timeout(Some(Duration::from_secs(30)));
        tcp.set_keepalive(Some(Duration::from_secs(30)));
        Self { tcp, matcher, tls }
    }
}
impl Service<Uri> for ProxyConnector {
    type Response = TokioIo<ProxyStream>;
    type Error = BoxError;
    type Future = Pin<Box<dyn Future<Output = Result<Self::Response, Self::Error>> + Send>>;
    fn poll_ready(&mut self, _: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        Poll::Ready(Ok(()))
    }
    fn call(&mut self, destination: Uri) -> Self::Future {
        let intercept = proxy_for(&self.matcher, &destination);
        let cgi = self.matcher.cgi
            && self.matcher.matcher.intercept(&destination).is_some()
            && destination.scheme_str() == Some("http");
        let mut connect_to = intercept
            .as_ref()
            .map(|i| i.uri().clone())
            .unwrap_or_else(|| destination.clone());
        if matches!(connect_to.scheme_str(), Some("socks5" | "socks5h"))
            && connect_to.port_u16().is_none()
        {
            connect_to = Uri::builder()
                .scheme(connect_to.scheme_str().unwrap())
                .authority(format!("{}:1080", connect_to.host().unwrap()))
                .path_and_query("/")
                .build()
                .expect("proxy URI was validated");
        }
        let mut tcp = self.tcp.clone();
        let tls = self.tls.clone();
        Box::pin(async move {
            if cgi {
                return Err(io::Error::other("refusing to use HTTP_PROXY value in CGI environment; see golang.org/s/cgihttpproxy").into());
            }
            let connection =
                tokio::time::timeout(Duration::from_secs(30), tcp.call(connect_to.clone()))
                    .await
                    .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "i/o timeout"))??;
            let mut io: Box<dyn AsyncIo> = Box::new(connection.into_inner());
            if intercept.is_some() && connect_to.scheme_str() == Some("https") {
                let name = rustls::pki_types::ServerName::try_from(
                    connect_to
                        .host()
                        .unwrap_or_default()
                        .trim_start_matches('[')
                        .trim_end_matches(']')
                        .to_owned(),
                )?;
                io = Box::new(
                    tokio::time::timeout(
                        Duration::from_secs(10),
                        tokio_rustls::TlsConnector::from(tls_config(&tls).await?).connect(name, io),
                    )
                    .await
                    .map_err(|_| {
                        io::Error::new(io::ErrorKind::TimedOut, "net/http: TLS handshake timeout")
                    })??,
                );
            }
            let socks = intercept.as_ref().is_some_and(|proxy| {
                matches!(proxy.uri().scheme_str(), Some("socks5" | "socks5h"))
            });
            let proxy = intercept.is_some() && !socks && destination.scheme_str() == Some("http");
            if let Some(intercept) = intercept {
                if socks {
                    socks_connect(&mut io, &destination, &intercept).await?;
                } else if destination.scheme_str() == Some("https") {
                    let host = destination.host().ok_or_else(|| {
                        io::Error::new(io::ErrorKind::InvalidInput, "missing destination host")
                    })?;
                    let authority = format!("{host}:{}", destination.port_u16().unwrap_or(443));
                    let authorization = intercept
                        .basic_auth()
                        .and_then(|value| value.to_str().ok())
                        .map(|value| format!("Proxy-Authorization: {value}\r\n"))
                        .unwrap_or_default();
                    io.write_all(format!("CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\nUser-Agent: Go-http-client/1.1\r\n{authorization}\r\n").as_bytes()).await?;
                    io.flush().await?;
                    let mut response = Vec::new();
                    while !response.ends_with(b"\r\n\r\n") {
                        if response.len() > 1024 * 1024 {
                            return Err(io::Error::new(
                                io::ErrorKind::InvalidData,
                                "proxy response headers too large",
                            )
                            .into());
                        }
                        response.push(io.read_u8().await?);
                    }
                    let status = response
                        .split(|byte| *byte == b' ')
                        .nth(1)
                        .unwrap_or_default();
                    if status != b"200" {
                        let response = String::from_utf8_lossy(&response);
                        let status = response
                            .lines()
                            .next()
                            .unwrap_or("proxy CONNECT failed")
                            .splitn(3, ' ')
                            .nth(2)
                            .unwrap_or("proxy CONNECT failed");
                        return Err(io::Error::other(status.to_owned()).into());
                    }
                }
            }
            let handshake = (destination.scheme_str() == Some("https"))
                .then(|| Box::pin(tokio::time::sleep(Duration::from_secs(10))));
            Ok(TokioIo::new(ProxyStream {
                io,
                proxy,
                handshake: std::sync::Mutex::new(handshake),
            }))
        })
    }
}

async fn socks_connect(
    io: &mut Box<dyn AsyncIo>,
    destination: &Uri,
    proxy: &Intercept,
) -> Result<(), BoxError> {
    let auth = proxy.raw_auth();
    io.write_all(if auth.is_some() {
        &[5, 2, 0, 2]
    } else {
        &[5, 1, 0]
    })
    .await?;
    let version = io.read_u8().await?;
    let method = io.read_u8().await?;
    if version != 5 {
        return Err(io::Error::other("invalid SOCKS version").into());
    }
    if method == 2 {
        let (username, password) =
            auth.ok_or_else(|| io::Error::other("SOCKS authentication is required"))?;
        if username.len() > 255 || password.len() > 255 {
            return Err(io::Error::other("SOCKS username or password is too long").into());
        }
        let mut credentials = vec![1, username.len() as u8];
        credentials.extend(username.as_bytes());
        credentials.push(password.len() as u8);
        credentials.extend(password.as_bytes());
        io.write_all(&credentials).await?;
        if io.read_u8().await? != 1 || io.read_u8().await? != 0 {
            return Err(io::Error::other("SOCKS authentication failed").into());
        }
    } else if method != 0 {
        return Err(io::Error::other("SOCKS proxy rejected authentication methods").into());
    }
    let host = destination
        .host()
        .unwrap_or_default()
        .trim_start_matches('[')
        .trim_end_matches(']');
    let mut connect = vec![5, 1, 0];
    match host.parse::<IpAddr>() {
        Ok(IpAddr::V4(ip)) => {
            connect.push(1);
            connect.extend(ip.octets());
        }
        Ok(IpAddr::V6(ip)) => {
            connect.push(4);
            connect.extend(ip.octets());
        }
        Err(_) => {
            if host.len() > 255 {
                return Err(io::Error::other("SOCKS destination hostname is too long").into());
            }
            connect.extend([3, host.len() as u8]);
            connect.extend(host.as_bytes());
        }
    }
    let port = destination
        .port_u16()
        .unwrap_or(if destination.scheme_str() == Some("https") {
            443
        } else {
            80
        });
    connect.extend(port.to_be_bytes());
    io.write_all(&connect).await?;
    let mut response = [0; 4];
    io.read_exact(&mut response).await?;
    if response[0] != 5 || response[1] != 0 {
        return Err(
            io::Error::other(format!("SOCKS connection failed: reply {}", response[1])).into(),
        );
    }
    let size = match response[3] {
        1 => 4,
        4 => 16,
        3 => io.read_u8().await? as usize,
        _ => return Err(io::Error::other("invalid SOCKS address type").into()),
    };
    let mut address = vec![0; size + 2];
    io.read_exact(&mut address).await?;
    Ok(())
}

async fn tls_config(store: &TlsStore) -> Result<Arc<rustls::ClientConfig>, BoxError> {
    let config = store
        .get_or_try_init(|| async {
            tokio::task::spawn_blocking(|| load_tls_config().map(Arc::new))
                .await
                .map_err(|error| Box::new(error) as BoxError)?
                .map_err(|error| Box::new(error) as BoxError)
        })
        .await?;
    Ok(config.clone())
}

fn load_tls_config() -> io::Result<rustls::ClientConfig> {
    #[cfg(target_os = "linux")]
    {
        let mut roots = rustls::RootCertStore::empty();
        let files = std::env::var_os("SSL_CERT_FILE")
            .filter(|value| !value.is_empty())
            .map(|file| vec![std::path::PathBuf::from(file)])
            .unwrap_or_else(|| {
                [
                    "/etc/ssl/certs/ca-certificates.crt",
                    "/etc/pki/tls/certs/ca-bundle.crt",
                    "/etc/ssl/ca-bundle.pem",
                    "/etc/pki/tls/cacert.pem",
                    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
                    "/etc/ssl/cert.pem",
                ]
                .into_iter()
                .map(Into::into)
                .collect()
            });
        for path in files {
            if let Ok(bytes) = std::fs::read(path) {
                append_pem_roots(&mut roots, &bytes);
                break;
            }
        }
        let directories = std::env::var_os("SSL_CERT_DIR")
            .filter(|value| !value.is_empty())
            .map(|value| std::env::split_paths(&value).collect::<Vec<_>>())
            .unwrap_or_else(|| vec!["/etc/ssl/certs".into(), "/etc/pki/tls/certs".into()]);
        for directory in directories {
            if let Ok(entries) = std::fs::read_dir(directory) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if entry.file_type().is_ok_and(|kind| kind.is_symlink())
                        && std::fs::read_link(&path)
                            .is_ok_and(|target| !target.to_string_lossy().contains('/'))
                    {
                        continue;
                    }
                    if let Ok(bytes) = std::fs::read(path) {
                        append_pem_roots(&mut roots, &bytes);
                    }
                }
            }
        }
        Ok(rustls::ClientConfig::builder()
            .with_root_certificates(roots)
            .with_no_client_auth())
    }
    #[cfg(not(target_os = "linux"))]
    {
        rustls::ClientConfig::builder()
            .with_native_roots()
            .map(|builder| builder.with_no_client_auth())
    }
}
#[cfg(target_os = "linux")]
fn append_pem_roots(roots: &mut rustls::RootCertStore, bytes: &[u8]) {
    use base64::Engine;
    let text = String::from_utf8_lossy(bytes);
    for block in text.split("-----BEGIN CERTIFICATE-----").skip(1) {
        let Some((certificate, _)) = block.split_once("-----END CERTIFICATE-----") else {
            continue;
        };
        let encoded: String = certificate
            .chars()
            .filter(|character| !character.is_ascii_whitespace())
            .collect();
        if let Ok(der) = base64::engine::general_purpose::STANDARD.decode(encoded) {
            let _ = roots.add(rustls::pki_types::CertificateDer::from(der));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
    const CA: &[u8] = include_bytes!("../tests/fixtures/tls/ca.der");
    const CERT: &[u8] = include_bytes!("../tests/fixtures/tls/server.der");
    const KEY: &[u8] = include_bytes!("../tests/fixtures/tls/server-key.der");

    fn runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
    }
    fn policy(http: &str, https: &str, no_proxy: &str) -> ProxyPolicy {
        ProxyPolicy {
            matcher: Arc::new(
                Matcher::builder()
                    .http(go_proxy_url(http))
                    .https(go_proxy_url(https))
                    .build(),
            ),
            no_proxy: no_proxy.into(),
            cgi: false,
        }
    }
    fn trusted_transport(policy: ProxyPolicy) -> Transport {
        let transport = Transport::with_policy(policy).unwrap();
        let mut roots = rustls::RootCertStore::empty();
        roots.add(CertificateDer::from(CA.to_vec())).unwrap();
        let config = rustls::ClientConfig::builder()
            .with_root_certificates(roots)
            .with_no_client_auth();
        transport.tls.set(Arc::new(config)).unwrap();
        transport
    }
    fn acceptor() -> tokio_rustls::TlsAcceptor {
        let config = rustls::ServerConfig::builder()
            .with_no_client_auth()
            .with_single_cert(
                vec![CertificateDer::from(CERT.to_vec())],
                PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(KEY.to_vec())),
            )
            .unwrap();
        tokio_rustls::TlsAcceptor::from(Arc::new(config))
    }
    async fn headers<S: AsyncRead + Unpin + ?Sized>(io: &mut S) -> io::Result<String> {
        let mut bytes = Vec::new();
        while !bytes.ends_with(b"\r\n\r\n") {
            bytes.push(io.read_u8().await?);
        }
        Ok(String::from_utf8(bytes).unwrap())
    }
    async fn tls_server() -> (u16, tokio::task::JoinHandle<Option<String>>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let task = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.ok()?;
            let mut stream = acceptor().accept(stream).await.ok()?;
            let request = headers(&mut stream).await.ok()?;
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
                .await
                .ok()?;
            Some(request)
        });
        (port, task)
    }
    #[test]
    fn refused_error_formatting_cannot_panic_on_raw_authorities() {
        for raw in ["http://%6c%6fcalhost:1", "http://café:1", "http://[invalid"] {
            let error = TransportError::new(
                raw,
                io::Error::new(io::ErrorKind::ConnectionRefused, "connection refused"),
            );
            assert!(error.to_string().contains("connect: connection refused"));
        }
    }
    #[test]
    fn empty_gzip_response_preserves_status_and_empty_body() {
        runtime().block_on(async {
            for (status, framing) in [(404, "Content-Length: 0\r\n"), (204, ""), (200, "Transfer-Encoding: chunked\r\n")] {
                let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap(); let port = listener.local_addr().unwrap().port();
                let task = tokio::spawn(async move {
                    let (mut stream, _) = listener.accept().await.unwrap(); headers(&mut stream).await.unwrap();
                    stream.write_all(format!("HTTP/1.1 {status} {}\r\nContent-Encoding: gzip\r\n{framing}Connection: close\r\n\r\n{}", StatusCode::from_u16(status).unwrap().canonical_reason().unwrap_or(""), if status == 200 { "0\r\n\r\n" } else { "" }).as_bytes()).await.unwrap();
                });
                let transport = Transport::with_policy(policy("", "", "")).unwrap();
                let response = transport.send(&Method::GET, &format!("http://127.0.0.1:{port}/"), &HeaderMap::new(), &None).await.unwrap();
                assert_eq!(response.status().as_u16(), status); assert!(response.bytes().await.unwrap().is_empty()); task.await.unwrap();
            }
        });
    }
    #[test]
    fn unrecognized_proxy_schemes_forward_http_like_go_without_bypass() {
        runtime().block_on(async {
            for scheme in ["socks4", "socks4a", "ftp", "unknown"] {
                let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
                let port = listener.local_addr().unwrap().port();
                let task = tokio::spawn(async move {
                    let (mut stream, _) = listener.accept().await.unwrap();
                    let request = headers(&mut stream).await.unwrap();
                    stream
                        .write_all(
                            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}",
                        )
                        .await
                        .unwrap();
                    request
                });
                let transport =
                    Transport::with_policy(policy(&format!("{scheme}://127.0.0.1:{port}"), "", ""))
                        .unwrap();
                transport
                    .send(
                        &Method::GET,
                        "http://fixture.invalid/path",
                        &HeaderMap::new(),
                        &None,
                    )
                    .await
                    .unwrap()
                    .bytes()
                    .await
                    .unwrap();
                assert!(
                    task.await
                        .unwrap()
                        .starts_with("GET http://fixture.invalid/path HTTP/1.1")
                );
            }
            assert_eq!(go_proxy_url("ftp://proxy.test"), "http://proxy.test:0");
        });
    }
    #[test]
    fn tls_verifies_trust_and_hostname() {
        runtime().block_on(async {
            let (port, task) = tls_server().await;
            let transport = trusted_transport(policy("", "", ""));
            let response = transport
                .send(
                    &Method::GET,
                    &format!("https://localhost:{port}/path"),
                    &HeaderMap::new(),
                    &None,
                )
                .await
                .unwrap();
            assert_eq!(response.bytes().await.unwrap().as_ref(), b"{}");
            assert!(
                task.await
                    .unwrap()
                    .unwrap()
                    .starts_with("GET /path HTTP/1.1")
            );
            let (port, task) = tls_server().await;
            let untrusted = Transport::with_policy(policy("", "", "")).unwrap();
            let error = untrusted
                .send(
                    &Method::GET,
                    &format!("https://localhost:{port}/"),
                    &HeaderMap::new(),
                    &None,
                )
                .await
                .err()
                .unwrap();
            assert!(error.to_string().contains("unknown authority"), "{error}");
            assert!(task.await.unwrap().is_none());
            let (port, task) = tls_server().await;
            let error = transport
                .send(
                    &Method::GET,
                    &format!("https://127.0.0.1:{port}/"),
                    &HeaderMap::new(),
                    &None,
                )
                .await
                .err()
                .unwrap();
            assert!(
                error.to_string().contains("not valid")
                    || error.to_string().contains("NotValidForName"),
                "{error}"
            );
            assert!(task.await.unwrap().is_none());
        });
    }
    #[test]
    fn gzip_decodes_body_and_preserves_go_header_policy() {
        runtime().block_on(async {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let port = listener.local_addr().unwrap().port();
            let mut gzip = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
            std::io::Write::write_all(&mut gzip, b"{\"version\":\"test\"}").unwrap(); let gzip = gzip.finish().unwrap();
            let task = tokio::spawn(async move {
                let (mut stream, _) = listener.accept().await.unwrap(); let request = headers(&mut stream).await.unwrap();
                stream.write_all(format!("HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", gzip.len()).as_bytes()).await.unwrap(); stream.write_all(&gzip).await.unwrap(); request
            });
            let transport = Transport::with_policy(policy("", "", "")).unwrap();
            let response = transport.send(&Method::GET, &format!("http://127.0.0.1:{port}/"), &HeaderMap::new(), &None).await.unwrap();
            assert!(!response.headers().contains_key(CONTENT_ENCODING));
            assert_eq!(response.bytes().await.unwrap().as_ref(), b"{\"version\":\"test\"}");
            let request = task.await.unwrap().to_ascii_lowercase();
            assert!(!request.contains("\r\naccept:")); assert!(request.contains("accept-encoding: gzip"));
            assert!(!transport.tls.initialized()); assert!(!transport.https.initialized());
        });
    }
    #[test]
    fn plain_http_proxy_uses_absolute_uri_and_proxy_auth() {
        runtime().block_on(async {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let port = listener.local_addr().unwrap().port();
            let task = tokio::spawn(async move {
                let (mut stream, _) = listener.accept().await.unwrap();
                let request = headers(&mut stream).await.unwrap();
                stream
                    .write_all(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}",
                    )
                    .await
                    .unwrap();
                request
            });
            let transport = Transport::with_policy(policy(
                &format!("http://user:pass@127.0.0.1:{port}"),
                "",
                "",
            ))
            .unwrap();
            let response = transport
                .send(
                    &Method::GET,
                    "http://fixture.test/path?query=1",
                    &HeaderMap::new(),
                    &None,
                )
                .await
                .unwrap();
            response.bytes().await.unwrap();
            let request = task.await.unwrap();
            assert!(request.starts_with("GET http://fixture.test/path?query=1 HTTP/1.1"));
            assert!(
                request
                    .to_ascii_lowercase()
                    .contains("proxy-authorization: basic dxnlcjpwyxnz")
            );
            assert!(!transport.tls.initialized());
        });
    }

    #[test]
    fn refused_proxy_names_the_proxy_instead_of_the_destination() {
        runtime().block_on(async {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let port = listener.local_addr().unwrap().port();
            drop(listener);
            for proxy in [format!("http://127.0.0.1:{port}"), format!("socks5h://127.0.0.1:{port}"), "ftp://127.0.0.1".into()] {
                let client = Transport::with_policy(policy(&proxy, "", "")).unwrap();
                let error = client.send(&Method::GET, "http://fixture.test:8000/about", &HeaderMap::new(), &None).await.err().expect("closed proxy must fail");
                let expected_port = if proxy.starts_with("ftp:") { 0 } else { port };
                assert_eq!(error.to_string(), format!("proxyconnect tcp: dial tcp 127.0.0.1:{expected_port}: connect: connection refused"));
            }
        });
    }
    async fn connect_proxy(encrypted: bool) -> (u16, tokio::task::JoinHandle<(String, String)>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let task = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut io: Box<dyn AsyncIo> = if encrypted {
                Box::new(acceptor().accept(stream).await.unwrap())
            } else {
                Box::new(stream)
            };
            let connect = headers(&mut *io).await.unwrap();
            io.write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                .await
                .unwrap();
            let mut target = acceptor().accept(io).await.unwrap();
            let request = headers(&mut target).await.unwrap();
            target
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
                .await
                .unwrap();
            (connect, request)
        });
        (port, task)
    }
    #[test]
    fn https_target_tunnels_through_http_and_https_proxies() {
        runtime().block_on(async {
            for encrypted in [false, true] {
                let (port, task) = connect_proxy(encrypted).await;
                let proxy = format!(
                    "{}://user:pass@localhost:{port}",
                    if encrypted { "https" } else { "http" }
                );
                let transport = trusted_transport(policy("", &proxy, ""));
                let mut auth = HeaderMap::new();
                auth.insert(
                    AUTHORIZATION,
                    HeaderValue::from_static("Bearer workspace-secret"),
                );
                let response = transport
                    .send(&Method::GET, "https://fixture.test/path", &auth, &None)
                    .await
                    .unwrap();
                assert_eq!(response.bytes().await.unwrap().as_ref(), b"{}");
                let (connect, request) = task.await.unwrap();
                assert!(connect.starts_with("CONNECT fixture.test:443 HTTP/1.1"));
                assert!(connect.contains("Proxy-Authorization: Basic dXNlcjpwYXNz"));
                assert!(!connect.contains("workspace-secret"));
                assert!(request.starts_with("GET /path HTTP/1.1"));
                assert!(request.contains("workspace-secret"));
                assert!(
                    !request
                        .to_ascii_lowercase()
                        .contains("proxy-authorization:")
                );
            }
        });
    }
    #[test]
    fn no_proxy_respects_domains_ports_ip_ranges_and_loopback() {
        assert!(no_proxy_matches("example.com", "sub.example.com", 443));
        assert!(!no_proxy_matches(".example.com", "example.com", 443));
        assert!(no_proxy_matches(".example.com:443", "sub.example.com", 443));
        assert!(!no_proxy_matches("example.com:80", "example.com", 443));
        assert!(no_proxy_matches("10.0.0.0/8", "10.0.3.4", 80));
        assert!(!no_proxy_matches("10.0.0.0/8", "11.0.3.4", 80));
        assert!(no_proxy_matches("[::1]:80", "::1", 80));
        let policy = policy("http://proxy.test", "", "");
        assert!(proxy_for(&policy, &"http://localhost/path".parse().unwrap()).is_none());
        assert!(proxy_for(&policy, &"http://127.0.0.1/path".parse().unwrap()).is_none());
        assert_eq!(strip_userinfo("http://u%3Av@host/path").1.unwrap(), b"u:v:");
    }
    #[test]
    fn cgi_proxy_rejection_precedes_no_proxy_and_loopback() {
        runtime().block_on(async {
            let mut proxy = policy("http://127.0.0.1:9", "", "*");
            proxy.cgi = true;
            let transport = Transport::with_policy(proxy).unwrap();
            let error = transport
                .send(
                    &Method::GET,
                    "http://localhost:9/path",
                    &HeaderMap::new(),
                    &None,
                )
                .await
                .err()
                .unwrap();
            assert!(error.to_string().contains("CGI environment"), "{error}");
        });
    }
    #[test]
    fn socks5_authenticates_and_resolves_destination_remotely() {
        runtime().block_on(async {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let port = listener.local_addr().unwrap().port();
            let task = tokio::spawn(async move {
                let (mut stream, _) = listener.accept().await.unwrap();
                let mut greeting = [0; 4];
                stream.read_exact(&mut greeting).await.unwrap();
                assert_eq!(greeting, [5, 2, 0, 2]);
                stream.write_all(&[5, 2]).await.unwrap();
                assert_eq!(stream.read_u8().await.unwrap(), 1);
                let size = stream.read_u8().await.unwrap() as usize;
                let mut username = vec![0; size];
                stream.read_exact(&mut username).await.unwrap();
                let size = stream.read_u8().await.unwrap() as usize;
                let mut password = vec![0; size];
                stream.read_exact(&mut password).await.unwrap();
                assert_eq!(username, b"user");
                assert_eq!(password, b"pass");
                stream.write_all(&[1, 0]).await.unwrap();
                let mut connect = [0; 4];
                stream.read_exact(&mut connect).await.unwrap();
                assert_eq!(connect, [5, 1, 0, 3]);
                let size = stream.read_u8().await.unwrap() as usize;
                let mut host = vec![0; size];
                stream.read_exact(&mut host).await.unwrap();
                assert_eq!(host, b"fixture.test");
                assert_eq!(stream.read_u16().await.unwrap(), 80);
                stream
                    .write_all(&[5, 0, 0, 1, 127, 0, 0, 1, 0, 80])
                    .await
                    .unwrap();
                let request = headers(&mut stream).await.unwrap();
                stream
                    .write_all(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}",
                    )
                    .await
                    .unwrap();
                request
            });
            let transport = Transport::with_policy(policy(
                &format!("socks5://user:pass@127.0.0.1:{port}"),
                "",
                "",
            ))
            .unwrap();
            let response = transport
                .send(
                    &Method::GET,
                    "http://fixture.test/path",
                    &HeaderMap::new(),
                    &None,
                )
                .await
                .unwrap();
            response.bytes().await.unwrap();
            let request = task.await.unwrap();
            assert!(request.starts_with("GET /path HTTP/1.1"));
            assert!(
                !request
                    .to_ascii_lowercase()
                    .contains("proxy-authorization:")
            );
        });
    }
}
