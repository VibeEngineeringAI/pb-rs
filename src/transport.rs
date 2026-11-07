//! Concrete pooled HTTP transport with explicit Go-compatible wire policy.
use bytes::{BufMut, Bytes, BytesMut};
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
            connect::{Connected, Connection, HttpConnector, capture_connection},
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
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    task::{Context, Poll},
    time::Duration,
};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, ReadBuf};
use tower_service::Service;

type BoxError = Box<dyn Error + Send + Sync>;
type HttpClient = Client<Tracking<HttpsConnector<ProxyConnector>>, Full<Bytes>>;
type PlainClient = Client<Tracking<ProxyConnector>, Full<Bytes>>;
type TlsStore = Arc<tokio::sync::OnceCell<Arc<rustls::ClientConfig>>>;

pub struct Transport {
    plain: PlainClient,
    https: tokio::sync::OnceCell<HttpClient>,
    tls: TlsStore,
    proxy: ProxyPolicy,
    metrics: bool,
}
#[derive(Debug)]
pub struct TransportError {
    pub url: String,
    source: BoxError,
    body: bool,
    proxy: Option<Box<Uri>>,
    /// Go distinguishes closes before response headers that Hyper reports as
    /// one IncompleteMessage error, so the transport records which occurred.
    head_eof: Option<HeadEof>,
    /// Go's ReadResponse error for a received status line it rejects; Hyper
    /// reports these without the offending token.
    malformed_head: Option<String>,
}
#[derive(Clone, Copy, Debug, PartialEq)]
enum HeadEof {
    /// Close before any response byte: Go returns the peek error, io.EOF.
    Clean,
    /// TLS cut inside a record before any plaintext: io.ErrUnexpectedEOF.
    Truncated,
    /// Close inside the status line or headers. Go marks the connection
    /// broken before mapping the io.ErrUnexpectedEOF from ReadResponse.
    Partial,
}
impl TransportError {
    fn new(url: impl Into<String>, source: impl Into<BoxError>) -> Self {
        Self {
            url: url.into(),
            source: source.into(),
            body: false,
            proxy: None,
            head_eof: None,
            malformed_head: None,
        }
    }
    fn body(url: impl Into<String>, source: impl Into<BoxError>) -> Self {
        Self {
            url: url.into(),
            source: source.into(),
            body: true,
            proxy: None,
            head_eof: None,
            malformed_head: None,
        }
    }
    fn with_proxy(mut self, proxy: Option<Uri>) -> Self {
        self.proxy = proxy.map(Box::new);
        self
    }
    pub fn is_body(&self) -> bool {
        self.body
    }
    pub fn is_connection_reset(&self) -> bool {
        let mut cause: Option<&(dyn Error + 'static)> = Some(self.source.as_ref());
        while let Some(error) = cause {
            if error
                .downcast_ref::<io::Error>()
                .is_some_and(|error| error.kind() == io::ErrorKind::ConnectionReset)
            {
                return true;
            }
            cause = error.source();
        }
        false
    }
    /// Socket failures remain network errors even when they occur after
    /// response headers. Truncated HTTP framing and gzip errors are body
    /// format failures, and must not acquire this classification.
    pub fn is_connection_error(&self) -> bool {
        let mut cause: Option<&(dyn Error + 'static)> = Some(self.source.as_ref());
        while let Some(error) = cause {
            if error.downcast_ref::<io::Error>().is_some_and(|error| {
                matches!(
                    error.kind(),
                    io::ErrorKind::ConnectionRefused
                        | io::ErrorKind::ConnectionReset
                        | io::ErrorKind::ConnectionAborted
                        | io::ErrorKind::NotConnected
                        | io::ErrorKind::BrokenPipe
                        | io::ErrorKind::NetworkDown
                        | io::ErrorKind::NetworkUnreachable
                        | io::ErrorKind::HostUnreachable
                )
            }) {
                return true;
            }
            cause = error.source();
        }
        false
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
        match self.head_eof {
            Some(HeadEof::Clean) => return f.write_str("EOF"),
            Some(HeadEof::Truncated) => return f.write_str("unexpected EOF"),
            Some(HeadEof::Partial) => {
                return f
                    .write_str("net/http: HTTP/1.x transport connection broken: unexpected EOF");
            }
            None => {}
        }
        if let Some(message) = &self.malformed_head {
            return write!(
                f,
                "net/http: HTTP/1.x transport connection broken: {message}"
            );
        }
        let mut cause: Option<&(dyn Error + 'static)> = Some(self.source.as_ref());
        let mut last = self.source.to_string();
        while let Some(error) = cause {
            // Hyper attaches framing-specific descriptions to body EOFs,
            // including EOF while waiting for the next chunk size. Go exposes
            // io.ErrUnexpectedEOF for these. Header EOFs have separate policy.
            if self.body
                && error
                    .downcast_ref::<io::Error>()
                    .is_some_and(|error| error.kind() == io::ErrorKind::UnexpectedEof)
            {
                return f.write_str("unexpected EOF");
            }
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
        if last == "Invalid chunk size line: missing size digit" {
            return f.write_str("invalid byte in chunk length");
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
        Self::build(proxy, false)
    }
    pub fn metrics() -> Result<Self, TransportError> {
        Self::build(
            ProxyPolicy {
                matcher: Arc::new(Matcher::builder().build()),
                no_proxy: String::new(),
                cgi: false,
            },
            true,
        )
    }
    fn build(proxy: ProxyPolicy, metrics: bool) -> Result<Self, TransportError> {
        let tls = Arc::new(tokio::sync::OnceCell::new());
        let connector = ProxyConnector::with_timers(proxy.clone(), tls.clone(), !metrics);
        let plain = Client::builder(TokioExecutor::new())
            .pool_timer(TokioTimer::new())
            .timer(TokioTimer::new())
            .pool_idle_timeout(Duration::from_secs(90))
            .pool_max_idle_per_host(2)
            .retry_canceled_requests(false)
            .build(Tracking(connector));
        Ok(Self {
            plain,
            https: tokio::sync::OnceCell::new(),
            tls,
            proxy,
            metrics,
        })
    }
    async fn https_client(&self) -> Result<&HttpClient, BoxError> {
        self.https
            .get_or_try_init(|| async {
                let config = tls_config(&self.tls).await?;
                let connector = ProxyConnector::with_timers(
                    self.proxy.clone(),
                    self.tls.clone(),
                    !self.metrics,
                );
                let builder = HttpsConnectorBuilder::new()
                    .with_tls_config((*config).clone())
                    .https_or_http()
                    .enable_http1();
                let connector = if self.metrics {
                    builder.wrap_connector(connector)
                } else {
                    builder.enable_http2().wrap_connector(connector)
                };
                Ok(Client::builder(TokioExecutor::new())
                    .pool_timer(TokioTimer::new())
                    .timer(TokioTimer::new())
                    .pool_idle_timeout(Duration::from_secs(90))
                    .pool_max_idle_per_host(2)
                    .retry_canceled_requests(false)
                    .build(Tracking(connector)))
            })
            .await
    }
    /// Shares owned request bytes across redirects and authenticated replays.
    pub async fn send(
        &self,
        method: &Method,
        raw_url: &str,
        headers: &HeaderMap,
        body: &Option<Bytes>,
    ) -> Result<Response, TransportError> {
        let url_without_fragment = raw_url.split('#').next().unwrap_or(raw_url);
        let (uri_string, userinfo) = strip_userinfo(url_without_fragment);
        let uri_string = ascii_authority(&uri_string);
        let uri: Uri = uri_string
            .parse()
            .map_err(|error| TransportError::new(raw_url, error))?;
        let mut prepared = headers.clone();
        prepared.insert(USER_AGENT, HeaderValue::from_static("Go-http-client/1.1"));
        if !prepared.contains_key(AUTHORIZATION)
            && let Some(userinfo) = userinfo
        {
            use base64::Engine;
            let encoded = base64::engine::general_purpose::STANDARD.encode(userinfo);
            let mut value = HeaderValue::from_str(&format!("Basic {encoded}"))
                .expect("base64 is a valid header");
            value.set_sensitive(true);
            prepared.insert(AUTHORIZATION, value);
        }
        let gzip = method != Method::HEAD
            && !headers.contains_key(ACCEPT_ENCODING)
            && !headers.contains_key(RANGE);
        if gzip {
            prepared.insert(ACCEPT_ENCODING, HeaderValue::from_static("gzip"));
        }
        if method == Method::POST
            || method == Method::PUT
            || method == Method::PATCH
            || body.as_ref().is_some_and(|b| !b.is_empty())
        {
            let length = body.as_ref().map_or(0, Bytes::len);
            prepared.insert(
                CONTENT_LENGTH,
                HeaderValue::from_str(&length.to_string()).expect("integer is a valid header"),
            );
        }
        if uri.scheme_str() == Some("http")
            && let Some(intercept) = proxy_for(&self.proxy, &uri)
                .filter(|proxy| matches!(proxy.uri().scheme_str(), Some("http" | "https")))
            && let Some(auth) = intercept.basic_auth()
        {
            prepared.insert(PROXY_AUTHORIZATION, auth.clone());
        }
        let client = if uri.scheme_str() == Some("https") {
            Some(
                self.https_client()
                    .await
                    .map_err(|e| TransportError::new(raw_url, e))?,
            )
        } else {
            None
        };
        let payload = body.clone().unwrap_or_default();
        // Retries happen inside this future, so the caller's absolute request
        // deadline bounds every attempt together. Like Go there is no count
        // limit: each retry consumes a pooled connection that already
        // completed an exchange, and a failure on a fresh one is final.
        let (response, head) = loop {
            let mut request = Request::builder()
                .method(method.clone())
                .uri(uri.clone())
                .body(Full::new(payload.clone()))
                .map_err(|error| TransportError::new(raw_url, error))?;
            *request.headers_mut() = prepared.clone();
            let capture = capture_connection(&mut request);
            // Go's per-connection read loop notices an idle close as it
            // happens. Yielding lets the runtime deliver pending socket events
            // first, so the pool drops such connections instead of writing
            // this request into them.
            tokio::task::yield_now().await;
            let result = match client {
                Some(client) => client.request(request).await,
                None => self.plain.request(request).await,
            };
            let head = capture
                .connection_metadata()
                .as_ref()
                .and_then(ConnectionTracker::of)
                .and_then(|state| state.head_line());
            let error = match result {
                Ok(response) => break (response, head),
                Err(error) => error,
            };
            // Hyper reports Canceled only when it hands the request back
            // without having dequeued it for writing.
            let unsent = source_chain(&error).any(|cause| {
                cause
                    .downcast_ref::<HyperError>()
                    .is_some_and(HyperError::is_canceled)
            });
            let state = capture
                .connection_metadata()
                .as_ref()
                .and_then(|connected| AttemptState::observe(connected, unsent));
            if replayable_after(method, state, unsent, &error) {
                continue;
            }
            let mut error = TransportError::new(raw_url, error)
                .with_proxy(proxy_for(&self.proxy, &uri).map(|proxy| proxy.uri().clone()));
            let incomplete = source_chain(error.source.as_ref()).any(|cause| {
                cause
                    .downcast_ref::<HyperError>()
                    .is_some_and(HyperError::is_incomplete_message)
            });
            // Byte counts describe one exchange only on HTTP/1 connections.
            let http1 = state.filter(|state| !state.h2);
            // TLS cut inside a record surfaces as an I/O error instead.
            let truncated = http1.is_some()
                && source_chain(error.source.as_ref()).any(|cause| {
                    cause
                        .downcast_ref::<io::Error>()
                        .is_some_and(|error| error.kind() == io::ErrorKind::UnexpectedEof)
                });
            let received = http1.is_some_and(|state| state.received_head);
            error.head_eof = match (incomplete, truncated, received) {
                (true, _, false) => Some(HeadEof::Clean),
                (false, true, false) => Some(HeadEof::Truncated),
                (true, _, true) | (_, true, true) => Some(HeadEof::Partial),
                (false, false, _) => None,
            };
            // A status line Go accepts leaves Hyper's own parse diagnostic.
            let parse = source_chain(error.source.as_ref()).any(|cause| {
                cause
                    .downcast_ref::<HyperError>()
                    .is_some_and(HyperError::is_parse)
            });
            if parse && http1.is_some() {
                error.malformed_head = head.and_then(|line| go_status_line(&line).err());
            }
            return Err(error);
        };
        let (mut parts, body) = response.into_parts();
        let status_text = go_status(&parts, head.as_deref());
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
            // StreamReader already buffers each Hyper Bytes frame and implements
            // AsyncBufRead. Another BufReader allocates and copies the input.
            let mut decoder = async_compression::tokio::bufread::GzipDecoder::new(reader);
            decoder.multiple_members(true);
            (None, Some(decoder), Some(started))
        } else {
            (Some(body), None, None)
        };
        Ok(Response {
            status: parts.status,
            status_text,
            headers: parts.headers,
            body,
            pending: Bytes::new(),
            gzip,
            gzip_input,
            url: raw_url.into(),
        })
    }
}

/// Per-connection facts shared by the socket wrapper below TLS, the stream
/// wrapper above it, and `send`. Only the connection task updates them; a
/// failed attempt reads them after the connection has stopped.
struct ConnectionState {
    /// Destination TLS runs between the socket and the tracked stream.
    tls: bool,
    /// HTTP/1 requests that began writing on this connection, counted when
    /// the first request byte is offered, like Go's persistConn.nwrite.
    requests: AtomicU64,
    /// Plaintext response bytes read since the current request began; Go
    /// peeks for the first one above TLS.
    received: AtomicU64,
    /// Whether received ciphertext ends between TLS records.
    boundary: AtomicBool,
    /// The current exchange's final status line as received. Go keeps it as
    /// `Response.Status` and quotes it in status-line errors; Hyper exposes
    /// neither. Only HTTP/1 responses read it.
    head: std::sync::Mutex<HeadScan>,
    /// The scan found the final status line or gave up.
    head_done: AtomicBool,
}
/// A longer status line is reported with Hyper's fallback text instead.
const HEAD_LINE_LIMIT: usize = 8 << 10;
/// Follows one exchange's response heads. Go's transport, like Hyper,
/// discards informational (1xx except 101) responses, which have no body,
/// and reports the status line of the response that follows them.
#[derive(Default)]
struct HeadScan {
    /// The status line being read, up to and including its newline.
    line: Vec<u8>,
    /// Inside an informational response's header block.
    skipping: bool,
    /// Header-line bytes since the last newline, and whether the last was CR.
    pending: usize,
    carriage: bool,
}
impl HeadScan {
    /// Consumes received bytes; true once the final status line is complete
    /// (or unusable), after which no later byte can change it.
    fn feed(&mut self, mut bytes: &[u8]) -> bool {
        while !bytes.is_empty() {
            if self.skipping {
                let Some(end) = bytes.iter().position(|&byte| byte == b'\n') else {
                    self.pending += bytes.len();
                    self.carriage = bytes.last() == Some(&b'\r');
                    return false;
                };
                let blank = self.pending + end == 0
                    || (end == 0 && self.pending == 1 && self.carriage)
                    || (self.pending == 0 && end == 1 && bytes[0] == b'\r');
                bytes = &bytes[end + 1..];
                (self.pending, self.carriage) = (0, false);
                if blank {
                    self.skipping = false;
                    self.line.clear();
                }
                continue;
            }
            let end = bytes.iter().position(|&byte| byte == b'\n');
            let take = end.map_or(bytes.len(), |end| end + 1);
            if self.line.len() + take > HEAD_LINE_LIMIT {
                self.line.clear();
                return true;
            }
            self.line.extend_from_slice(&bytes[..take]);
            bytes = &bytes[take..];
            if end.is_none() {
                return false;
            }
            let informational = self
                .status_line()
                .and_then(|line| go_status_line(&line).ok().map(|(_, code)| code))
                .is_some_and(|code| (100..200).contains(&code) && code != 101);
            if !informational {
                return true;
            }
            self.skipping = true;
        }
        false
    }
    /// The complete line without its line ending, as textproto reads it.
    fn status_line(&self) -> Option<Vec<u8>> {
        let line = self.line.strip_suffix(b"\n")?;
        Some(line.strip_suffix(b"\r").unwrap_or(line).to_vec())
    }
}
impl ConnectionState {
    fn new(tls: bool) -> Self {
        Self {
            tls,
            requests: AtomicU64::new(0),
            received: AtomicU64::new(0),
            boundary: AtomicBool::new(true),
            head: std::sync::Mutex::new(HeadScan::default()),
            head_done: AtomicBool::new(false),
        }
    }
    /// HTTP/1 alternates whole requests and responses on a connection, so a
    /// write after response bytes, or the first write, starts a new request.
    fn begin_write(&self) {
        if self.requests.load(Ordering::Relaxed) == 0 || self.received.load(Ordering::Relaxed) > 0 {
            self.requests.fetch_add(1, Ordering::Relaxed);
            self.received.store(0, Ordering::Relaxed);
            *self.head.lock().expect("head line lock") = HeadScan::default();
            self.head_done.store(false, Ordering::Relaxed);
        }
    }
    fn record_head(&self, bytes: &[u8]) {
        if self.head_done.load(Ordering::Relaxed) {
            return;
        }
        if self.head.lock().expect("head line lock").feed(bytes) {
            self.head_done.store(true, Ordering::Relaxed);
        }
    }
    /// The final (or, while incomplete, current) status line, once complete.
    /// An informational line is never returned: its header block is still
    /// being skipped, or a following status line has replaced it.
    fn head_line(&self) -> Option<Vec<u8>> {
        let head = self.head.lock().expect("head line lock");
        if head.skipping {
            return None;
        }
        head.status_line()
    }
}

/// Go's `ReadResponse` status-line checks: the status after the protocol
/// (Go's `Response.Status`) and its code, or the error Go returns.
fn go_status_line(line: &[u8]) -> Result<(&[u8], u16), String> {
    use crate::argv::quote;
    let Some(space) = line.iter().position(|&byte| byte == b' ') else {
        return Err(format!("malformed HTTP response {}", quote(line)));
    };
    let (proto, status) = (&line[..space], &line[space + 1..]);
    let status = &status[status.iter().take_while(|&&byte| byte == b' ').count()..];
    let code = status
        .split(|&byte| byte == b' ')
        .next()
        .unwrap_or_default();
    // strconv.Atoi: an optional sign, then decimal digits; Go then rejects
    // only a negative value, so "-00" is code 0.
    let (negative, digits) = match code.split_first() {
        Some((b'-', digits)) => (true, digits),
        Some((b'+', digits)) => (false, digits),
        _ => (false, code),
    };
    let value = (code.len() == 3 && !digits.is_empty() && digits.iter().all(u8::is_ascii_digit))
        .then(|| {
            digits
                .iter()
                .fold(0, |value, digit| value * 10 + u16::from(digit - b'0'))
        })
        .filter(|&value| !negative || value == 0);
    let Some(value) = value else {
        return Err(format!("malformed HTTP status code {}", quote(code)));
    };
    // ParseHTTPVersion accepts HTTP/1.0, HTTP/1.1 and any HTTP/D.D.
    let version = proto.len() == 8
        && proto.starts_with(b"HTTP/")
        && proto[5].is_ascii_digit()
        && proto[6] == b'.'
        && proto[7].is_ascii_digit();
    if !version {
        return Err(format!("malformed HTTP version {}", quote(proto)));
    }
    Ok((status, value))
}

/// Go's `Response.Status`. HTTP/1 keeps the received status line after the
/// protocol, so an empty reason keeps its delimiter ("200 ") and a missing
/// one has none ("200"); HTTP/2 joins the code and Go's `StatusText`.
fn go_status(parts: &http::response::Parts, head: Option<&[u8]>) -> String {
    let code = parts.status.as_u16();
    if parts.version == http::Version::HTTP_2 {
        return format!("{code} {}", go_status_text(code));
    }
    // The code check guards against any line Hyper did not use as this head.
    if let Some((status, _)) = head
        .and_then(|line| go_status_line(line).ok())
        .filter(|&(_, value)| value == code)
    {
        return String::from_utf8_lossy(status).into_owned();
    }
    let reason = parts
        .extensions
        .get::<hyper::ext::ReasonPhrase>()
        .map(|reason| String::from_utf8_lossy(reason.as_bytes()).into_owned())
        .unwrap_or_else(|| parts.status.canonical_reason().unwrap_or_default().into());
    if reason.is_empty() {
        code.to_string()
    } else {
        format!("{code} {reason}")
    }
}

/// Go 1.25 `net/http.StatusText`.
pub(crate) fn go_status_text(code: u16) -> &'static str {
    match code {
        100 => "Continue",
        101 => "Switching Protocols",
        102 => "Processing",
        103 => "Early Hints",
        200 => "OK",
        201 => "Created",
        202 => "Accepted",
        203 => "Non-Authoritative Information",
        204 => "No Content",
        205 => "Reset Content",
        206 => "Partial Content",
        207 => "Multi-Status",
        208 => "Already Reported",
        226 => "IM Used",
        300 => "Multiple Choices",
        301 => "Moved Permanently",
        302 => "Found",
        303 => "See Other",
        304 => "Not Modified",
        305 => "Use Proxy",
        307 => "Temporary Redirect",
        308 => "Permanent Redirect",
        400 => "Bad Request",
        401 => "Unauthorized",
        402 => "Payment Required",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        406 => "Not Acceptable",
        407 => "Proxy Authentication Required",
        408 => "Request Timeout",
        409 => "Conflict",
        410 => "Gone",
        411 => "Length Required",
        412 => "Precondition Failed",
        413 => "Request Entity Too Large",
        414 => "Request URI Too Long",
        415 => "Unsupported Media Type",
        416 => "Requested Range Not Satisfiable",
        417 => "Expectation Failed",
        418 => "I'm a teapot",
        421 => "Misdirected Request",
        422 => "Unprocessable Entity",
        423 => "Locked",
        424 => "Failed Dependency",
        425 => "Too Early",
        426 => "Upgrade Required",
        428 => "Precondition Required",
        429 => "Too Many Requests",
        431 => "Request Header Fields Too Large",
        451 => "Unavailable For Legal Reasons",
        500 => "Internal Server Error",
        501 => "Not Implemented",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        504 => "Gateway Timeout",
        505 => "HTTP Version Not Supported",
        506 => "Variant Also Negotiates",
        507 => "Insufficient Storage",
        508 => "Loop Detected",
        510 => "Not Extended",
        511 => "Network Authentication Required",
        _ => "",
    }
}
#[derive(Clone)]
struct ConnectionTracker(Arc<ConnectionState>);
impl ConnectionTracker {
    fn of(connected: &Connected) -> Option<Arc<ConnectionState>> {
        let mut extensions = http::Extensions::new();
        connected.get_extras(&mut extensions);
        extensions.remove::<Self>().map(|tracker| tracker.0)
    }
}
#[derive(Clone, Copy)]
struct AttemptState {
    /// An earlier request completed on this connection (Go's pc.isReused).
    reused: bool,
    h2: bool,
    received_head: bool,
}
impl AttemptState {
    fn observe(connected: &Connected, unsent: bool) -> Option<Self> {
        let state = ConnectionTracker::of(connected)?;
        let requests = state.requests.load(Ordering::Relaxed);
        // An unsent request never began, so every counted request preceded
        // it; otherwise the latest counted request is this attempt.
        let earlier = if unsent {
            requests
        } else {
            requests.saturating_sub(1)
        };
        Some(Self {
            reused: earlier > 0,
            h2: connected.is_negotiated_h2(),
            received_head: !unsent && state.received.load(Ordering::Relaxed) > 0,
        })
    }
}
fn source_chain<'a>(
    error: &'a (dyn Error + 'static),
) -> impl Iterator<Item = &'a (dyn Error + 'static)> {
    std::iter::successors(Some(error), |&error| error.source())
}
/// Go's HTTP/1 transport retries a failed request only on a connection that
/// already completed an exchange, and then only when nothing was written
/// (any method: pb bodies are in memory, so Go's GetBody can rewind them) or,
/// for GET, HEAD, OPTIONS and TRACE, when no response byte arrived. pb never
/// sends Go's Idempotency-Key exception. First use of a fresh connection,
/// partial responses and HTTP/2 are never retried here.
///
/// Nothing-written is proved only by Hyper returning the request unsent. Go
/// also retries a mutation whose first socket write fails outright; Hyper
/// does not expose that, and byte counts cannot separate it from a server
/// that answered mid-request, so such a mutation fails instead of replaying.
fn replayable_after(
    method: &Method,
    state: Option<AttemptState>,
    unsent: bool,
    error: &(dyn Error + 'static),
) -> bool {
    let Some(state) = state else {
        return false;
    };
    if !state.reused || state.h2 {
        return false;
    }
    if unsent {
        return true;
    }
    if !matches!(
        *method,
        Method::GET | Method::HEAD | Method::OPTIONS | Method::TRACE
    ) || state.received_head
    {
        return false;
    }
    source_chain(error).any(|cause| {
        cause
            .downcast_ref::<HyperError>()
            .is_some_and(HyperError::is_incomplete_message)
            || cause.downcast_ref::<io::Error>().is_some_and(|error| {
                matches!(
                    error.kind(),
                    io::ErrorKind::ConnectionReset
                        | io::ErrorKind::ConnectionAborted
                        | io::ErrorKind::BrokenPipe
                        | io::ErrorKind::UnexpectedEof
                )
            })
    })
}

/// Wraps a connector so every connection is observed above TLS, where Go's
/// persistConn counts written request bytes and peeks for response bytes.
#[derive(Clone)]
pub struct Tracking<C>(C);
impl<C> Service<Uri> for Tracking<C>
where
    C: Service<Uri, Error = BoxError>,
    C::Response: hyper::rt::Read + hyper::rt::Write + Connection + Unpin + Send + 'static,
    C::Future: Send + 'static,
{
    type Response = TokioIo<TrackedStream<C::Response>>;
    type Error = BoxError;
    type Future = Pin<Box<dyn Future<Output = Result<Self::Response, Self::Error>> + Send>>;
    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.0.poll_ready(cx)
    }
    fn call(&mut self, destination: Uri) -> Self::Future {
        let connecting = self.0.call(destination);
        Box::pin(async move {
            let io = connecting.await?;
            let state = ConnectionTracker::of(&io.connected())
                .ok_or_else(|| io::Error::other("connection is missing its tracker"))?;
            Ok(TokioIo::new(TrackedStream {
                io: TokioIo::new(io),
                state,
            }))
        })
    }
}
pub struct TrackedStream<T> {
    io: TokioIo<T>,
    state: Arc<ConnectionState>,
}
impl<T: hyper::rt::Read + hyper::rt::Write + Unpin> AsyncRead for TrackedStream<T> {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let before = buf.filled().len();
        match Pin::new(&mut self.io).poll_read(cx, buf) {
            Poll::Ready(Ok(())) => {
                let count = buf.filled().len() - before;
                self.state
                    .received
                    .fetch_add(count as u64, Ordering::Relaxed);
                self.state.record_head(&buf.filled()[before..]);
                Poll::Ready(Ok(()))
            }
            // Rustls rejects a TCP close without close_notify; crypto/tls
            // accepts it as io.EOF when it falls between records.
            Poll::Ready(Err(error))
                if error.kind() == io::ErrorKind::UnexpectedEof
                    && self.state.tls
                    && self.state.boundary.load(Ordering::Relaxed) =>
            {
                Poll::Ready(Ok(()))
            }
            polled => polled,
        }
    }
}
impl<T: hyper::rt::Read + hyper::rt::Write + Unpin> AsyncWrite for TrackedStream<T> {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        if !buf.is_empty() {
            self.state.begin_write();
        }
        Pin::new(&mut self.io).poll_write(cx, buf)
    }
    fn poll_write_vectored(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bufs: &[io::IoSlice<'_>],
    ) -> Poll<io::Result<usize>> {
        if bufs.iter().any(|buf| !buf.is_empty()) {
            self.state.begin_write();
        }
        Pin::new(&mut self.io).poll_write_vectored(cx, bufs)
    }
    fn is_write_vectored(&self) -> bool {
        self.io.is_write_vectored()
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.io).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.io).poll_shutdown(cx)
    }
}
impl<T: hyper::rt::Read + hyper::rt::Write + Connection + Unpin> Connection for TrackedStream<T> {
    fn connected(&self) -> Connected {
        self.io.connected()
    }
}

/// Follows TLS record framing in received ciphertext, so an EOF can be
/// classified as crypto/tls does: clean only between records.
#[derive(Default)]
struct RecordFraming {
    /// Header bytes seen of the next record.
    header: usize,
    length: [u8; 2],
    /// Payload bytes still expected for the current record.
    payload: usize,
}
impl RecordFraming {
    fn feed(&mut self, mut bytes: &[u8]) -> bool {
        while let Some((&byte, rest)) = bytes.split_first() {
            if self.payload > 0 {
                let count = self.payload.min(bytes.len());
                self.payload -= count;
                bytes = &bytes[count..];
                continue;
            }
            if self.header >= 3 {
                self.length[self.header - 3] = byte;
            }
            self.header += 1;
            bytes = rest;
            if self.header == 5 {
                self.header = 0;
                self.payload = usize::from(u16::from_be_bytes(self.length));
            }
        }
        self.header == 0 && self.payload == 0
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
type GzipReader =
    async_compression::tokio::bufread::GzipDecoder<tokio_util::io::StreamReader<BodyStream, Bytes>>;
pub struct Response {
    status: StatusCode,
    status_text: String,
    headers: HeaderMap,
    body: Option<Incoming>,
    pending: Bytes,
    gzip: Option<GzipReader>,
    gzip_input: Option<Arc<std::sync::atomic::AtomicBool>>,
    url: String,
}
impl Response {
    pub fn status(&self) -> StatusCode {
        self.status
    }
    /// Go's `Response.Status`, which Go messages print verbatim.
    pub fn status_text(&self) -> &str {
        &self.status_text
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
        // A bounded read must also bound uncompressed Hyper frames, which can
        // be much larger than the requested preview or bridge capacity.
        if maximum == 0 {
            return Ok(Some(Bytes::new()));
        }
        if !self.pending.is_empty() {
            let count = maximum.min(self.pending.len());
            return Ok(Some(self.pending.split_to(count)));
        }
        if let Some(gzip) = &mut self.gzip {
            // Keep compressed reads small: the miniz backend initializes its
            // output slice on each decode, including fragmented input. BytesMut
            // avoids an additional initialization here, not inside the codec.
            let maximum = maximum.min(8192);
            let mut buffer = BytesMut::with_capacity(maximum);
            let count = match gzip.read_buf(&mut (&mut buffer).limit(maximum)).await {
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
            return Ok(Some(buffer.freeze()));
        }
        let Some(body) = self.body.as_mut() else {
            return Ok(None);
        };
        while let Some(frame) = body.frame().await {
            let frame = frame.map_err(|e: HyperError| TransportError::body(&self.url, e))?;
            if let Ok(mut data) = frame.into_data() {
                if data.is_empty() {
                    continue;
                }
                let count = maximum.min(data.len());
                let chunk = data.split_to(count);
                self.pending = data;
                return Ok(Some(chunk));
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
pub(crate) fn no_proxy_matches(no_proxy: &str, host: &str, port: u16) -> bool {
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
    state: Arc<ConnectionState>,
    records: RecordFraming,
    /// Local and peer socket addresses, which Go's net.OpError names.
    addresses: Option<(std::net::SocketAddr, std::net::SocketAddr)>,
}
/// Go reports a socket read failure as a net.OpError naming both ends;
/// crypto/tls returns it unchanged. Only a reset is mapped: it is the read
/// failure observed against the pinned Go client. Windows wording differs.
fn go_read_error(
    error: io::Error,
    addresses: Option<(std::net::SocketAddr, std::net::SocketAddr)>,
) -> io::Error {
    match addresses {
        Some((local, peer))
            if cfg!(unix)
                && error.kind() == io::ErrorKind::ConnectionReset
                && error.raw_os_error().is_some() =>
        {
            io::Error::new(
                io::ErrorKind::ConnectionReset,
                format!("read tcp {local}->{peer}: read: connection reset by peer"),
            )
        }
        _ => error,
    }
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
        let before = buf.filled().len();
        let polled = Pin::new(&mut *self.io).poll_read(cx, buf);
        if self.state.tls && buf.filled().len() > before {
            let this = &mut *self;
            let boundary = this.records.feed(&buf.filled()[before..]);
            this.state.boundary.store(boundary, Ordering::Relaxed);
        }
        match polled {
            Poll::Ready(Err(error)) => Poll::Ready(Err(go_read_error(error, self.addresses))),
            polled => polled,
        }
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
        Connected::new()
            .proxy(self.proxy)
            .extra(ConnectionTracker(self.state.clone()))
    }
}

#[derive(Clone)]
pub struct ProxyConnector {
    tcp: HttpConnector,
    matcher: ProxyPolicy,
    tls: TlsStore,
    timers: bool,
}
impl ProxyConnector {
    fn with_timers(matcher: ProxyPolicy, tls: TlsStore, timers: bool) -> Self {
        let mut tcp = HttpConnector::new();
        tcp.enforce_http(false);
        tcp.set_connect_timeout(timers.then(|| Duration::from_secs(30)));
        tcp.set_keepalive(Some(Duration::from_secs(30)));
        Self {
            tcp,
            matcher,
            tls,
            timers,
        }
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
        let timers = self.timers;
        Box::pin(async move {
            if cgi {
                return Err(io::Error::other("refusing to use HTTP_PROXY value in CGI environment; see golang.org/s/cgihttpproxy").into());
            }
            let connection = if timers {
                tokio::time::timeout(Duration::from_secs(30), tcp.call(connect_to.clone()))
                    .await
                    .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "i/o timeout"))??
            } else {
                tcp.call(connect_to.clone()).await?
            };
            let socket = connection.into_inner();
            let addresses = socket.local_addr().ok().zip(socket.peer_addr().ok());
            let mut io: Box<dyn AsyncIo> = Box::new(socket);
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
            let state = Arc::new(ConnectionState::new(
                destination.scheme_str() == Some("https"),
            ));
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
            let handshake = (timers && destination.scheme_str() == Some("https"))
                .then(|| Box::pin(tokio::time::sleep(Duration::from_secs(10))));
            Ok(TokioIo::new(ProxyStream {
                io,
                proxy,
                handshake: std::sync::Mutex::new(handshake),
                state,
                records: RecordFraming::default(),
                addresses,
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

pub(crate) fn load_tls_config() -> io::Result<rustls::ClientConfig> {
    #[cfg(target_os = "linux")]
    {
        let mut roots = rustls::RootCertStore::empty();
        // Bundles and hashed directory aliases repeat the same roots; parse each DER once.
        let mut seen = std::collections::HashSet::new();
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
                append_pem_roots(&mut roots, &mut seen, &bytes);
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
                        append_pem_roots(&mut roots, &mut seen, &bytes);
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
fn append_pem_roots(
    roots: &mut rustls::RootCertStore,
    seen: &mut std::collections::HashSet<Vec<u8>>,
    bytes: &[u8],
) {
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
            if seen.contains(&der) {
                continue;
            }
            let _ = roots.add(rustls::pki_types::CertificateDer::from(der.as_slice()));
            seen.insert(der);
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

    #[cfg(target_os = "linux")]
    #[test]
    fn pem_roots_skip_duplicate_der_and_malformed_blocks() {
        use base64::Engine;
        let pem = |der: &[u8]| {
            let encoded = base64::engine::general_purpose::STANDARD.encode(der);
            let lines: Vec<&str> = encoded
                .as_bytes()
                .chunks(64)
                .map(|line| std::str::from_utf8(line).unwrap())
                .collect();
            format!(
                "-----BEGIN CERTIFICATE-----\n{}\n-----END CERTIFICATE-----\n",
                lines.join("\n")
            )
        };
        let bundle = [
            pem(CA),
            pem(CA),
            "-----BEGIN CERTIFICATE-----\n!!!not base64!!!\n-----END CERTIFICATE-----\n".into(),
            pem(b"not a certificate"),
            pem(CERT),
            pem(b"not a certificate"),
            pem(CA),
            "-----BEGIN CERTIFICATE-----\nunterminated\n".into(),
        ]
        .concat();
        let mut roots = rustls::RootCertStore::empty();
        let mut seen = std::collections::HashSet::new();
        append_pem_roots(&mut roots, &mut seen, bundle.as_bytes());
        // A second file (e.g. a hashed alias of the same bundle) adds nothing new.
        append_pem_roots(&mut roots, &mut seen, bundle.as_bytes());
        append_pem_roots(&mut roots, &mut seen, pem(CA).as_bytes());

        let mut expected = rustls::RootCertStore::empty();
        expected.add(CertificateDer::from(CA)).unwrap();
        expected.add(CertificateDer::from(CERT)).unwrap();
        assert_eq!(roots.roots, expected.roots);
    }

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
    /// What the scripted keep-alive server does with each received request.
    #[derive(Clone, Copy, PartialEq)]
    enum Fault {
        Ok,
        /// Abrupt close before any response byte; TLS sends no close_notify.
        Close,
        /// Orderly close (TLS close_notify) before any response byte.
        CloseNotify,
        /// Close after the status line and part of a header line.
        PartialHead,
        /// TLS only: cut the response record one byte short, then close, so
        /// no plaintext reaches the client.
        TruncatedRecord,
        /// Respond, then close the idle connection when the test signals.
        OkThenIdleClose,
    }
    /// Server socket that can drop the final byte of its next write.
    struct Truncating {
        tcp: tokio::net::TcpStream,
        truncate: Arc<std::sync::atomic::AtomicBool>,
    }
    impl AsyncRead for Truncating {
        fn poll_read(
            mut self: Pin<&mut Self>,
            cx: &mut Context<'_>,
            buf: &mut ReadBuf<'_>,
        ) -> Poll<io::Result<()>> {
            Pin::new(&mut self.tcp).poll_read(cx, buf)
        }
    }
    impl AsyncWrite for Truncating {
        fn poll_write(
            mut self: Pin<&mut Self>,
            cx: &mut Context<'_>,
            buf: &[u8],
        ) -> Poll<io::Result<usize>> {
            if buf.len() > 1 && self.truncate.swap(false, Ordering::Relaxed) {
                let short = buf.len() - 1;
                return Pin::new(&mut self.tcp)
                    .poll_write(cx, &buf[..short])
                    .map_ok(|count| if count == short { buf.len() } else { count });
            }
            Pin::new(&mut self.tcp).poll_write(cx, buf)
        }
        fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
            Pin::new(&mut self.tcp).poll_flush(cx)
        }
        fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
            Pin::new(&mut self.tcp).poll_shutdown(cx)
        }
    }
    type Ledger = Arc<std::sync::Mutex<Vec<(usize, String)>>>;
    struct Scripted {
        scheme: &'static str,
        port: u16,
        ledger: Ledger,
        idle_close: Arc<tokio::sync::Notify>,
    }
    /// Serves scripted actions in arrival order over HTTP or HTTPS and records
    /// `(connection, request line)` for every request it fully receives.
    async fn scripted_server(scheme: &'static str, script: Vec<Fault>) -> Scripted {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let ledger = Ledger::default();
        let idle_close = Arc::new(tokio::sync::Notify::new());
        let script = Arc::new(std::sync::Mutex::new(
            script
                .into_iter()
                .collect::<std::collections::VecDeque<_>>(),
        ));
        let (log, signal) = (ledger.clone(), idle_close.clone());
        tokio::spawn(async move {
            for connection in 0.. {
                let Ok((tcp, _)) = listener.accept().await else {
                    return;
                };
                let (log, script, signal) = (log.clone(), script.clone(), signal.clone());
                tokio::spawn(async move {
                    let truncate = Arc::new(std::sync::atomic::AtomicBool::new(false));
                    let socket = Truncating {
                        tcp,
                        truncate: truncate.clone(),
                    };
                    let mut stream: Box<dyn AsyncIo> = if scheme == "https" {
                        let Ok(tls) = acceptor().accept(socket).await else {
                            return;
                        };
                        Box::new(tls)
                    } else {
                        Box::new(socket)
                    };
                    while let Ok(head) = headers(&mut *stream).await {
                        let length = head
                            .lines()
                            .find_map(|line| {
                                let (name, value) = line.split_once(':')?;
                                name.eq_ignore_ascii_case("content-length")
                                    .then(|| value.trim().parse::<usize>().ok())?
                            })
                            .unwrap_or(0);
                        let mut body = vec![0; length];
                        if stream.read_exact(&mut body).await.is_err() {
                            return;
                        }
                        let line = head.lines().next().unwrap_or_default().to_owned();
                        log.lock().unwrap().push((connection, line));
                        let fault = script.lock().unwrap().pop_front().unwrap_or(Fault::Ok);
                        let reply: &[u8] = match fault {
                            Fault::Close => return,
                            Fault::CloseNotify => {
                                let _ = stream.shutdown().await;
                                return;
                            }
                            Fault::PartialHead => b"HTTP/1.1 200 OK\r\nContent-Length: 2",
                            Fault::TruncatedRecord => {
                                truncate.store(true, Ordering::Relaxed);
                                b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
                            }
                            Fault::Ok | Fault::OkThenIdleClose => {
                                b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
                            }
                        };
                        if stream.write_all(reply).await.is_err()
                            || stream.flush().await.is_err()
                            || matches!(fault, Fault::PartialHead | Fault::TruncatedRecord)
                        {
                            return;
                        }
                        if fault == Fault::OkThenIdleClose {
                            signal.notified().await;
                            return;
                        }
                    }
                });
            }
        });
        Scripted {
            scheme,
            port,
            ledger,
            idle_close,
        }
    }
    impl Scripted {
        fn url(&self, path: &str) -> String {
            let host = if self.scheme == "https" {
                "localhost"
            } else {
                "127.0.0.1"
            };
            format!("{}://{host}:{}{path}", self.scheme, self.port)
        }
        fn requests(&self) -> Vec<(usize, String)> {
            self.ledger.lock().unwrap().clone()
        }
        fn count(&self, path: &str) -> usize {
            self.requests()
                .iter()
                .filter(|(_, line)| line.contains(path))
                .count()
        }
    }
    fn body_for(method: &Method) -> Option<Bytes> {
        (method == Method::POST || method == Method::PUT || method == Method::PATCH)
            .then(|| Bytes::from_static(b"[\"admin\"]"))
    }
    async fn exchange(
        transport: &Transport,
        server: &Scripted,
        method: Method,
        path: &str,
    ) -> Result<Bytes, TransportError> {
        let body = body_for(&method);
        let response = transport
            .send(&method, &server.url(path), &HeaderMap::new(), &body)
            .await?;
        let bytes = response.bytes().await?;
        // Let Hyper return the idle connection to the pool before the next
        // request, so the following exchange deterministically reuses it.
        tokio::time::sleep(Duration::from_millis(20)).await;
        Ok(bytes)
    }
    const SCHEMES: [&str; 2] = ["http", "https"];
    // Expected outcomes below were characterized against Go 1.25.4 net/http,
    // the toolchain that built the reference pb, using equivalent servers.
    #[test]
    fn fresh_connection_eof_before_headers_is_go_eof_without_retry() {
        runtime().block_on(async {
            for scheme in SCHEMES {
                for fault in [Fault::Close, Fault::CloseNotify] {
                    for method in [Method::GET, Method::DELETE, Method::POST] {
                        let server = scripted_server(scheme, vec![fault]).await;
                        let transport = trusted_transport(policy("", "", ""));
                        let error =
                            exchange(&transport, &server, method.clone(), "/api/v1/user/bob")
                                .await
                                .unwrap_err();
                        assert_eq!(error.to_string(), "EOF", "{scheme} {method}");
                        assert!(!error.is_body() && !error.is_connection_error());
                        let line = format!("{method} /api/v1/user/bob HTTP/1.1");
                        assert_eq!(server.requests(), [(0, line)], "{scheme} {method}");
                    }
                }
            }
        });
    }
    #[test]
    fn eof_inside_response_headers_is_connection_broken_without_retry() {
        runtime().block_on(async {
            // Fresh and reused connections alike: Go retries only when no
            // response byte arrived, and reports the broken connection.
            for scheme in SCHEMES {
                for reuse in [false, true] {
                    let mut script = vec![Fault::PartialHead];
                    if reuse {
                        script.insert(0, Fault::Ok);
                    }
                    let server = scripted_server(scheme, script).await;
                    let transport = trusted_transport(policy("", "", ""));
                    if reuse {
                        exchange(&transport, &server, Method::GET, "/api/v1/user")
                            .await
                            .unwrap();
                    }
                    let error = exchange(&transport, &server, Method::GET, "/api/v1/roles")
                        .await
                        .unwrap_err();
                    assert_eq!(
                        error.to_string(),
                        "net/http: HTTP/1.x transport connection broken: unexpected EOF",
                        "{scheme} reuse={reuse}"
                    );
                    assert!(!error.is_body());
                    assert_eq!(server.count("/roles"), 1, "{scheme} reuse={reuse}");
                }
            }
        });
    }
    #[test]
    fn tls_record_cut_before_plaintext_is_unexpected_eof_and_retried_when_reused() {
        runtime().block_on(async {
            let server = scripted_server("https", vec![Fault::TruncatedRecord]).await;
            let transport = trusted_transport(policy("", "", ""));
            let error = exchange(&transport, &server, Method::GET, "/api/v1/roles")
                .await
                .unwrap_err();
            assert_eq!(error.to_string(), "unexpected EOF");
            assert_eq!(server.count("/roles"), 1);
            // No plaintext arrived, so Go's first-byte peek failed: a GET on
            // a reused connection is retried, a mutation is not.
            let server = scripted_server("https", vec![Fault::Ok, Fault::TruncatedRecord]).await;
            let transport = trusted_transport(policy("", "", ""));
            exchange(&transport, &server, Method::GET, "/api/v1/user")
                .await
                .unwrap();
            let body = exchange(&transport, &server, Method::GET, "/api/v1/roles")
                .await
                .unwrap();
            assert_eq!(body.as_ref(), b"ok");
            assert_eq!(
                server.requests(),
                [
                    (0, "GET /api/v1/user HTTP/1.1".into()),
                    (0, "GET /api/v1/roles HTTP/1.1".into()),
                    (1, "GET /api/v1/roles HTTP/1.1".into()),
                ]
            );
            let server = scripted_server("https", vec![Fault::Ok, Fault::TruncatedRecord]).await;
            let transport = trusted_transport(policy("", "", ""));
            exchange(&transport, &server, Method::GET, "/api/v1/user")
                .await
                .unwrap();
            let error = exchange(&transport, &server, Method::POST, "/api/v1/user/carol")
                .await
                .unwrap_err();
            assert_eq!(error.to_string(), "unexpected EOF");
            assert_eq!(server.count("carol"), 1);
        });
    }
    #[test]
    fn reused_idle_connection_closed_before_headers_retries_read_once() {
        runtime().block_on(async {
            for scheme in SCHEMES {
                for fault in [Fault::Close, Fault::CloseNotify] {
                    let server = scripted_server(scheme, vec![Fault::Ok, fault]).await;
                    let transport = trusted_transport(policy("", "", ""));
                    exchange(&transport, &server, Method::GET, "/api/v1/user")
                        .await
                        .unwrap();
                    let body = exchange(&transport, &server, Method::GET, "/api/v1/roles")
                        .await
                        .unwrap();
                    assert_eq!(body.as_ref(), b"ok");
                    exchange(&transport, &server, Method::POST, "/api/v1/user/carol")
                        .await
                        .unwrap();
                    assert_eq!(
                        server.requests(),
                        [
                            (0, "GET /api/v1/user HTTP/1.1".into()),
                            (0, "GET /api/v1/roles HTTP/1.1".into()),
                            (1, "GET /api/v1/roles HTTP/1.1".into()),
                            (1, "POST /api/v1/user/carol HTTP/1.1".into()),
                        ],
                        "{scheme}"
                    );
                }
            }
        });
    }
    #[test]
    fn retried_read_failing_on_a_fresh_connection_is_not_retried_again() {
        runtime().block_on(async {
            for scheme in SCHEMES {
                let script = vec![Fault::Ok, Fault::Close, Fault::Close];
                let server = scripted_server(scheme, script).await;
                let transport = trusted_transport(policy("", "", ""));
                exchange(&transport, &server, Method::GET, "/api/v1/user")
                    .await
                    .unwrap();
                let error = exchange(&transport, &server, Method::GET, "/api/v1/roles")
                    .await
                    .unwrap_err();
                assert_eq!(error.to_string(), "EOF");
                assert_eq!(
                    server.requests(),
                    [
                        (0, "GET /api/v1/user HTTP/1.1".into()),
                        (0, "GET /api/v1/roles HTTP/1.1".into()),
                        (1, "GET /api/v1/roles HTTP/1.1".into()),
                    ],
                    "{scheme}"
                );
            }
        });
    }
    #[test]
    fn mutations_on_reused_connections_are_sent_exactly_once() {
        runtime().block_on(async {
            // The server received each mutation in full before closing, so it
            // may have committed; Go does not replay these methods.
            for scheme in SCHEMES {
                for method in [Method::POST, Method::PUT, Method::PATCH, Method::DELETE] {
                    let server = scripted_server(scheme, vec![Fault::Ok, Fault::Close]).await;
                    let transport = trusted_transport(policy("", "", ""));
                    exchange(&transport, &server, Method::GET, "/api/v1/roles")
                        .await
                        .unwrap();
                    let error =
                        exchange(&transport, &server, method.clone(), "/api/v1/role/unused")
                            .await
                            .unwrap_err();
                    assert_eq!(error.to_string(), "EOF", "{scheme} {method}");
                    assert_eq!(
                        server.requests(),
                        [
                            (0, "GET /api/v1/roles HTTP/1.1".into()),
                            (0, format!("{method} /api/v1/role/unused HTTP/1.1")),
                        ]
                    );
                }
            }
        });
    }
    #[test]
    fn requests_are_counted_when_writing_begins() {
        let state = Arc::new(ConnectionState::new(false));
        let connected = Connected::new().extra(ConnectionTracker(state.clone()));
        let observe = |unsent| AttemptState::observe(&connected, unsent).unwrap();
        // The first request fails on a fresh connection, written or not.
        assert!(!observe(true).reused);
        state.begin_write();
        state.begin_write();
        assert!(!observe(false).reused);
        state.received.fetch_add(17, Ordering::Relaxed);
        assert!(observe(false).received_head);
        // Reuse is known before the next request is dequeued, independent of
        // when the caller saw the previous response.
        let queued = observe(true);
        assert!(queued.reused && !queued.received_head);
        state.begin_write();
        state.begin_write();
        let written = observe(false);
        assert!(written.reused && !written.received_head);
        assert_eq!(state.requests.load(Ordering::Relaxed), 2);
    }
    #[test]
    fn unsent_requests_replay_for_any_method_only_on_reused_http1_connections() {
        runtime().block_on(async {
            // A genuine Hyper Canceled error: the request is handed back
            // because the connection stopped before dequeuing it. An idle
            // close racing a new request on the pool produces the same error,
            // but cannot be scheduled deterministically in a test.
            let (client, _server) = tokio::io::duplex(64);
            let (mut sender, connection) =
                hyper::client::conn::http1::handshake::<_, Full<Bytes>>(TokioIo::new(client))
                    .await
                    .unwrap();
            drop(connection);
            let error = sender
                .send_request(Request::new(Full::new(Bytes::new())))
                .await
                .unwrap_err();
            assert!(error.is_canceled());
            let state = |reused, h2| {
                Some(AttemptState {
                    reused,
                    h2,
                    received_head: false,
                })
            };
            for method in [
                Method::POST,
                Method::PUT,
                Method::PATCH,
                Method::DELETE,
                Method::GET,
            ] {
                assert!(replayable_after(&method, state(true, false), true, &error));
                assert!(!replayable_after(
                    &method,
                    state(false, false),
                    true,
                    &error
                ));
                assert!(!replayable_after(&method, state(true, true), true, &error));
                assert!(
                    !replayable_after(&method, state(true, false), false, &error),
                    "{method}: a written request is replayed only after a read failure"
                );
            }
        });
    }
    #[test]
    fn idle_close_arriving_while_the_runtime_is_busy_is_noticed_before_dispatch() {
        // The server closes the idle connection while the client runtime is
        // blocked by synchronous work. Go's read loop sees that close at once
        // and dials anew, so the mutation must not be written into the dead
        // connection, where it could neither succeed nor be replayed.
        let (ready, server) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            runtime().block_on(async {
                for scheme in SCHEMES {
                    let script = vec![Fault::OkThenIdleClose];
                    ready.send(scripted_server(scheme, script).await).unwrap();
                }
                std::future::pending::<()>().await;
            })
        });
        runtime().block_on(async {
            for scheme in SCHEMES {
                let server = server.recv().unwrap();
                let transport = trusted_transport(policy("", "", ""));
                exchange(&transport, &server, Method::GET, "/api/v1/roles")
                    .await
                    .unwrap();
                server.idle_close.notify_one();
                std::thread::sleep(Duration::from_millis(200));
                let body = transport
                    .send(
                        &Method::POST,
                        &server.url("/api/v1/user/carol"),
                        &HeaderMap::new(),
                        &body_for(&Method::POST),
                    )
                    .await
                    .unwrap()
                    .bytes()
                    .await
                    .unwrap();
                assert_eq!(body.as_ref(), b"ok");
                assert_eq!(
                    server.requests(),
                    [
                        (0, "GET /api/v1/roles HTTP/1.1".into()),
                        (1, "POST /api/v1/user/carol HTTP/1.1".into()),
                    ],
                    "{scheme}"
                );
            }
        });
    }
    #[test]
    fn healthy_keep_alive_exchanges_are_neither_duplicated_nor_retried() {
        runtime().block_on(async {
            for scheme in SCHEMES {
                let server = scripted_server(scheme, Vec::new()).await;
                let transport = trusted_transport(policy("", "", ""));
                for method in [Method::GET, Method::POST, Method::GET, Method::DELETE] {
                    exchange(&transport, &server, method, "/api/v1/query")
                        .await
                        .unwrap();
                }
                let lines = server.requests();
                assert_eq!(lines.len(), 4);
                assert!(
                    lines.iter().all(|(connection, _)| *connection == 0),
                    "{scheme} {lines:?}"
                );
            }
        });
    }
    #[test]
    fn record_framing_reports_boundaries_across_split_reads() {
        let mut framing = RecordFraming::default();
        assert!(framing.feed(&[]));
        assert!(!framing.feed(&[23, 3, 3]));
        assert!(!framing.feed(&[0, 3, b'a']));
        assert!(framing.feed(b"bc"));
        // An empty record and a record split inside its payload.
        assert!(framing.feed(&[21, 3, 3, 0, 0]));
        assert!(!framing.feed(&[23, 3, 3, 1, 0, 0]));
        assert!(!framing.feed(&[0; 254]));
        assert!(framing.feed(&[0]));
    }
    #[test]
    fn metrics_transport_has_no_proxy_or_connection_timers() {
        let transport = Transport::metrics().unwrap();
        assert!(transport.metrics);
        let connector =
            ProxyConnector::with_timers(transport.proxy.clone(), transport.tls.clone(), false);
        assert!(!connector.timers);
        assert!(
            proxy_for(
                &transport.proxy,
                &"http://external.example/path".parse().unwrap()
            )
            .is_none()
        );
    }
    #[test]
    fn metrics_tls_negotiates_http1_when_server_offers_http2() {
        runtime().block_on(async {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let port = listener.local_addr().unwrap().port();
            let mut config = rustls::ServerConfig::builder()
                .with_no_client_auth()
                .with_single_cert(
                    vec![CertificateDer::from(CERT.to_vec())],
                    PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(KEY.to_vec())),
                )
                .unwrap();
            config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
            let acceptor = tokio_rustls::TlsAcceptor::from(Arc::new(config));
            let task = tokio::spawn(async move {
                let (stream, _) = listener.accept().await.unwrap();
                let mut stream = acceptor.accept(stream).await.unwrap();
                let protocol = stream.get_ref().1.alpn_protocol().map(<[u8]>::to_vec);
                let request = headers(&mut stream).await.unwrap();
                stream
                    .write_all(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\nnull",
                    )
                    .await
                    .unwrap();
                (protocol, request)
            });
            let transport = Transport::metrics().unwrap();
            let mut roots = rustls::RootCertStore::empty();
            roots.add(CertificateDer::from(CA.to_vec())).unwrap();
            let config = rustls::ClientConfig::builder()
                .with_root_certificates(roots)
                .with_no_client_auth();
            transport.tls.set(Arc::new(config)).unwrap();
            let response = transport
                .send(
                    &Method::GET,
                    &format!("https://localhost:{port}/prometheus/api/v1/labels"),
                    &HeaderMap::new(),
                    &None,
                )
                .await
                .unwrap();
            assert_eq!(response.bytes().await.unwrap().as_ref(), b"null");
            let (protocol, request) = task.await.unwrap();
            // An HTTP/1-only client may omit ALPN entirely; it must never
            // advertise or negotiate h2 even when the server prefers it.
            assert!(protocol.is_none() || protocol.as_deref() == Some(b"http/1.1".as_slice()));
            assert!(request.starts_with("GET /prometheus/api/v1/labels HTTP/1.1"));
        });
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
    fn connection_reset_detection_uses_io_kind_through_api_wrappers() {
        use crate::api::ApiError;

        let reset = TransportError::new(
            "http://fixture/",
            io::Error::new(io::ErrorKind::ConnectionReset, "Socket fault"),
        );
        assert!(reset.is_connection_reset());
        let wrapped = ApiError::Refresh(Box::new(ApiError::Context {
            message: "request context".into(),
            source: Box::new(ApiError::Request {
                method: Method::GET,
                url: "http://fixture/".into(),
                source: Box::new(ApiError::Network(reset)),
            }),
        }));
        assert!(wrapped.is_connection_reset());
        let other = TransportError::new(
            "http://fixture/",
            io::Error::new(io::ErrorKind::ConnectionAborted, "connection reset"),
        );
        assert!(!other.is_connection_reset());
    }
    #[test]
    fn body_format_failures_are_distinct_from_connection_failures() {
        for kind in [io::ErrorKind::UnexpectedEof, io::ErrorKind::InvalidData] {
            let error = TransportError::body("http://fixture/", io::Error::new(kind, "body fault"));
            assert!(error.is_body());
            assert!(!error.is_connection_error());
        }
        let error = TransportError::body(
            "http://fixture/",
            io::Error::new(io::ErrorKind::ConnectionReset, "socket fault"),
        );
        assert!(error.is_body());
        assert!(error.is_connection_error());
    }
    #[test]
    fn chunk_size_eof_normalization_applies_only_to_body_errors() {
        let description = "unexpected EOF during chunk size line";
        let body = TransportError::body(
            "http://fixture/",
            io::Error::new(io::ErrorKind::UnexpectedEof, description),
        );
        assert_eq!(body.to_string(), "unexpected EOF");
        assert!(!body.is_connection_error());
        let header = TransportError::new(
            "http://fixture/",
            io::Error::new(io::ErrorKind::UnexpectedEof, description),
        );
        assert_eq!(header.to_string(), description);
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
    fn gzip_bytes(bytes: &[u8]) -> Vec<u8> {
        let mut encoder = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        std::io::Write::write_all(&mut encoder, bytes).unwrap();
        encoder.finish().unwrap()
    }
    async fn chunked_gzip_response(compressed: Vec<u8>, frame_size: usize) -> Response {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            headers(&mut stream).await.unwrap();
            stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n").await.unwrap();
            for chunk in compressed.chunks(frame_size) {
                stream
                    .write_all(format!("{:x}\r\n", chunk.len()).as_bytes())
                    .await
                    .unwrap();
                stream.write_all(chunk).await.unwrap();
                stream.write_all(b"\r\n").await.unwrap();
            }
            stream.write_all(b"0\r\n\r\n").await.unwrap();
        });
        Transport::with_policy(policy("", "", ""))
            .unwrap()
            .send(
                &Method::GET,
                &format!("http://127.0.0.1:{port}/"),
                &HeaderMap::new(),
                &None,
            )
            .await
            .unwrap()
    }
    #[test]
    fn gzip_streaming_handles_fragmented_members_and_bounded_reads() {
        runtime().block_on(async {
            // Incompressible input exercises frames beyond the former 8 KiB
            // buffer; small HTTP chunks split gzip headers, trailers and members.
            let mut state = 1_u32;
            let expected: Vec<u8> = (0..20000)
                .map(|_| {
                    state ^= state << 13;
                    state ^= state >> 17;
                    state ^= state << 5;
                    state as u8
                })
                .collect();
            let mut compressed = gzip_bytes(&expected[..10000]);
            compressed.extend(gzip_bytes(&[]));
            compressed.extend(gzip_bytes(&expected[10000..]));
            assert!(compressed.len() > 8192);
            for frame_size in [7, 65536] {
                let mut response = chunked_gzip_response(compressed.clone(), frame_size).await;
                assert!(!response.headers().contains_key(CONTENT_ENCODING));
                assert_eq!(response.content_length(), None);
                let mut actual = Vec::new();
                let mut limits = [1, 17, 8192, 65536].into_iter().cycle();
                loop {
                    assert!(response.chunk_limit(0).await.unwrap().unwrap().is_empty());
                    let limit = limits.next().unwrap();
                    let Some(chunk) = response.chunk_limit(limit).await.unwrap() else {
                        break;
                    };
                    assert!(chunk.len() <= limit.min(8192));
                    actual.extend_from_slice(&chunk);
                }
                assert_eq!(actual, expected, "HTTP chunk size {frame_size}");
            }
        });
    }
    #[test]
    fn gzip_streaming_retains_header_checksum_and_truncation_errors() {
        runtime().block_on(async {
            let valid = gzip_bytes(b"response body");
            let truncated = valid[..valid.len() - 4].to_vec();
            let mut checksum = valid.clone();
            checksum[valid.len() - 8] ^= 1;
            for (compressed, expected) in [
                (b"invalid gzip header".to_vec(), "gzip: invalid header"),
                (truncated, "unexpected EOF"),
                (checksum, "gzip: invalid checksum"),
            ] {
                for frame_size in [1, 65536] {
                    for limit in [8192, 65536] {
                        let mut response =
                            chunked_gzip_response(compressed.clone(), frame_size).await;
                        let error = loop {
                            match response.chunk_limit(limit).await {
                                Ok(Some(chunk)) => assert!(chunk.len() <= 8192),
                                Ok(None) => panic!("corrupt gzip response completed"),
                                Err(error) => break error,
                            }
                        };
                        assert!(error.is_body());
                        assert!(!error.is_connection_error());
                        assert_eq!(error.to_string(), expected);
                    }
                }
            }
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

    /// Serves one connection: for each request, reads its head and writes the
    /// scripted response in separate segments so the client sees split reads.
    async fn segmented_server(
        responses: Vec<Vec<&'static [u8]>>,
    ) -> (u16, tokio::task::JoinHandle<usize>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let task = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            stream.set_nodelay(true).unwrap();
            for segments in &responses {
                headers(&mut stream).await.unwrap();
                for segment in segments {
                    stream.write_all(segment).await.unwrap();
                    stream.flush().await.unwrap();
                    tokio::time::sleep(Duration::from_millis(5)).await;
                }
            }
            // Further connections would mean the pooled one was not reused.
            let extra = tokio::time::timeout(Duration::from_millis(50), listener.accept()).await;
            usize::from(extra.is_ok()) + 1
        });
        (port, task)
    }
    async fn status_text_of(transport: &Transport, port: u16) -> String {
        let response = transport
            .send(
                &Method::GET,
                &format!("http://127.0.0.1:{port}/"),
                &HeaderMap::new(),
                &None,
            )
            .await
            .unwrap();
        let text = response.status_text().to_owned();
        response.bytes().await.unwrap();
        text
    }
    #[test]
    fn status_text_keeps_the_received_go_status() {
        runtime().block_on(async {
            for (line, expected) in [
                (&b"HTTP/1.1 200 \r\n"[..], "200 "),
                (b"HTTP/1.1 200\r\n", "200"),
                (b"HTTP/1.1 299 \r\n", "299 "),
                (b"HTTP/1.1 299\r\n", "299"),
                (b"HTTP/1.1 200 OK\r\n", "200 OK"),
                (b"HTTP/1.1 500 Kaput Now\r\n", "500 Kaput Now"),
                (b"HTTP/1.1 200  Fine \r\n", "200  Fine "),
                (b"HTTP/1.0 503 \n", "503 "),
            ] {
                let (split, rest) = line.split_at(11);
                let (port, task) = segmented_server(vec![vec![
                    split,
                    rest,
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n",
                ]])
                .await;
                let transport = Transport::with_policy(policy("", "", "")).unwrap();
                assert_eq!(status_text_of(&transport, port).await, expected, "{line:?}");
                task.await.unwrap();
            }
        });
    }
    #[test]
    fn informational_responses_never_supply_the_final_status_text() {
        runtime().block_on(async {
            for (segments, expected) in [
                (
                    vec![
                        &b"HTTP/1.1 103 Early Hints\r\nLink: </a>; rel=preload\r\n"[..],
                        b"\r",
                        b"\nHTTP/1.1 200 \r\nContent-Length: 0\r\n\r\n",
                    ],
                    "200 ",
                ),
                (
                    vec![
                        b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 201",
                        b" Made\r\nContent-Length: 0\r\n\r\n",
                    ],
                    "201 Made",
                ),
                (
                    vec![
                        b"HTTP/1.1 103 \n\nHTTP/1.1 102 Processing\r\nX: 1\r\n\r\n",
                        b"HTTP/1.1 200\r\nContent-Length: 0\r\n\r\n",
                    ],
                    "200",
                ),
            ] {
                let (port, task) = segmented_server(vec![segments]).await;
                let transport = Transport::with_policy(policy("", "", "")).unwrap();
                assert_eq!(status_text_of(&transport, port).await, expected);
                task.await.unwrap();
            }
        });
    }
    #[test]
    fn pooled_requests_each_report_their_own_status_text() {
        runtime().block_on(async {
            let (port, task) = segmented_server(vec![
                vec![b"HTTP/1.1 200 First\r\nContent-Length: 2\r\n\r\nok"],
                vec![b"HTTP/1.1 200 \r\nContent-Length: 0\r\n\r\n"],
                vec![
                    b"HTTP/1.1 103 Early\r\n\r\n",
                    b"HTTP/1.1 404\r\nContent-Length: 0\r\n\r\n",
                ],
            ])
            .await;
            let transport = Transport::with_policy(policy("", "", "")).unwrap();
            assert_eq!(status_text_of(&transport, port).await, "200 First");
            assert_eq!(status_text_of(&transport, port).await, "200 ");
            assert_eq!(status_text_of(&transport, port).await, "404");
            assert_eq!(task.await.unwrap(), 1, "one pooled connection");
        });
    }
    #[test]
    fn oversized_status_lines_fall_back_to_hyper_reason() {
        let long = format!("HTTP/1.1 200 {}\r\n", "x".repeat(HEAD_LINE_LIMIT));
        for split in [None, Some(20)] {
            let mut scan = HeadScan::default();
            let done = match split {
                None => scan.feed(long.as_bytes()),
                Some(at) => {
                    assert!(!scan.feed(&long.as_bytes()[..at]));
                    scan.feed(&long.as_bytes()[at..])
                }
            };
            assert!(done);
            assert!(scan.line.is_empty() && scan.status_line().is_none());
        }
        let mut scan = HeadScan::default();
        let fits = format!("HTTP/1.1 200 {}\r\n", "x".repeat(HEAD_LINE_LIMIT - 15));
        assert_eq!(fits.len(), HEAD_LINE_LIMIT);
        assert!(scan.feed(fits.as_bytes()));
        assert_eq!(scan.status_line().unwrap(), fits.trim_end().as_bytes());
        // End to end, the empty-reason distinction is lost but nothing fails.
        runtime().block_on(async {
            let line: &'static [u8] = Box::leak(long.into_bytes().into_boxed_slice());
            let (port, task) = segmented_server(vec![vec![
                line,
                b"Content-Length: 0\r\nConnection: close\r\n\r\n",
            ]])
            .await;
            let transport = Transport::with_policy(policy("", "", "")).unwrap();
            let text = status_text_of(&transport, port).await;
            assert_eq!(text, format!("200 {}", "x".repeat(HEAD_LINE_LIMIT)));
            task.await.unwrap();
        });
    }
    #[test]
    fn rejected_status_lines_report_go_read_response_errors() {
        runtime().block_on(async {
            for (line, expected) in [
                (
                    &b"HTTP/1.1 abc OK\r\n"[..],
                    r#"malformed HTTP status code "abc""#,
                ),
                (
                    b"HTTP/1.1 1234 OK\r\n",
                    r#"malformed HTTP status code "1234""#,
                ),
                (
                    b"HTTP/1.1 -12 OK\r\n",
                    r#"malformed HTTP status code "-12""#,
                ),
                (b"FOO/1.1 200 OK\r\n", r#"malformed HTTP version "FOO/1.1""#),
                (b"HTTP/1.1\r\n", r#"malformed HTTP response "HTTP/1.1""#),
                (
                    b"HTTP/1.1 \xffbc OK\r\n",
                    r#"malformed HTTP status code "\xffbc""#,
                ),
            ] {
                let (port, task) = segmented_server(vec![vec![
                    line,
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n",
                ]])
                .await;
                let transport = Transport::with_policy(policy("", "", "")).unwrap();
                let error = transport
                    .send(
                        &Method::POST,
                        &format!("http://127.0.0.1:{port}/"),
                        &HeaderMap::new(),
                        &None,
                    )
                    .await
                    .err()
                    .unwrap();
                assert_eq!(
                    error.to_string(),
                    format!("net/http: HTTP/1.x transport connection broken: {expected}"),
                    "{line:?}"
                );
                task.await.unwrap();
            }
        });
    }
    #[test]
    fn go_status_line_accepts_what_go_accepts() {
        assert_eq!(
            go_status_line(b"HTTP/1.1   200 OK"),
            Ok((&b"200 OK"[..], 200))
        );
        assert_eq!(go_status_line(b"HTTP/2.0 099 x"), Ok((&b"099 x"[..], 99)));
        assert_eq!(go_status_line(b"HTTP/1.1 +20"), Ok((&b"+20"[..], 20)));
        assert_eq!(go_status_line(b"HTTP/1.1 -00 x"), Ok((&b"-00 x"[..], 0)));
        assert_eq!(go_status_line(b"HTTP/1.1 +00"), Ok((&b"+00"[..], 0)));
        for code in ["-01", "-1", "--0", "+-0", "-+0", "-", "+"] {
            assert_eq!(
                go_status_line(format!("HTTP/1.1 {code} x").as_bytes()),
                Err(format!(
                    "malformed HTTP status code {}",
                    crate::argv::quote(code.as_bytes())
                ))
            );
        }
    }
    #[test]
    fn http2_status_text_joins_go_status_text() {
        for (code, expected) in [
            (200, "200 OK"),
            (299, "299 "),
            (413, "413 Request Entity Too Large"),
        ] {
            let (parts, ()) = http::Response::builder()
                .status(code)
                .version(http::Version::HTTP_2)
                .body(())
                .unwrap()
                .into_parts();
            assert_eq!(go_status(&parts, Some(b"HTTP/1.1 200 ignored")), expected);
        }
    }
    #[cfg(unix)]
    #[test]
    fn body_reset_names_both_socket_ends_like_go() {
        runtime().block_on(async {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let port = listener.local_addr().unwrap().port();
            let task = tokio::spawn(async move {
                let (mut stream, peer) = listener.accept().await.unwrap();
                headers(&mut stream).await.unwrap();
                stream
                    .write_all(
                        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n6\r\nabcdef\r\n",
                    )
                    .await
                    .unwrap();
                tokio::time::sleep(Duration::from_millis(50)).await;
                // SO_LINGER 0 makes the close a reset (test-only).
                #[allow(deprecated)]
                stream.set_linger(Some(Duration::ZERO)).unwrap();
                drop(stream);
                peer
            });
            let transport = Transport::with_policy(policy("", "", "")).unwrap();
            let mut response = transport
                .send(
                    &Method::GET,
                    &format!("http://127.0.0.1:{port}/"),
                    &HeaderMap::new(),
                    &None,
                )
                .await
                .unwrap();
            assert_eq!(&response.chunk().await.unwrap().unwrap()[..], b"abcdef");
            let error = response.chunk().await.unwrap_err();
            let client = task.await.unwrap();
            assert!(error.is_connection_reset() && error.is_body());
            assert_eq!(
                error.to_string(),
                format!("read tcp {client}->127.0.0.1:{port}: read: connection reset by peer")
            );
        });
    }
}
