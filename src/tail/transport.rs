//! Flight endpoint discovery and the owned connection reused by every DoGet.
//!
//! Go reference: `cmd/tail.go` at 07af390 with grpc-go v1.65.0 defaults. HTTP
//! discovery reuses the shared `ApiClient` (deadline, redirects, OAuth refresh);
//! none of that policy reaches Flight. Failures that grpc-go reports only when
//! the RPC starts (metadata validation, dialing, TLS) are deferred into
//! `do_get`, so they keep Go's stage after the watching status line.
use crate::{
    ErrorCode,
    api::ApiClient,
    auth::Auth,
    config::ProfileStore,
    error::{CliError, go_string},
    gojson,
    parser::quote,
};
use arrow_flight::{FlightData, Ticket, flight_service_client::FlightServiceClient};
use base64::{Engine, engine::general_purpose::STANDARD};
use bytes::Bytes;
use http::{Method, Uri};
use hyper_util::rt::TokioIo;
use rustls::pki_types::ServerName;
use std::{
    fmt,
    future::Future,
    io,
    net::{IpAddr, SocketAddr},
    pin::Pin,
    sync::{Arc, OnceLock},
    task::{Context, Poll, ready},
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, ReadBuf},
    net::{TcpSocket, TcpStream},
    time::{Instant, Sleep, sleep_until, timeout_at},
};
use tokio_rustls::{TlsConnector, client::TlsStream};
use tonic::{
    Code, Request, Status, Streaming,
    metadata::{MetadataMap, MetadataValue},
    transport::{Channel, Endpoint},
};

/// grpc-go v1.65.0 `defaultClientMaxReceiveMessageSize`. tonic's default is
/// the same today; pin it so a dependency change cannot move the boundary.
const MAX_RECEIVE_MESSAGE_SIZE: usize = 4 * 1024 * 1024;
/// grpc-go's `grpcUA`, sent only on the proxy CONNECT request we write
/// ourselves. tonic's HTTP/2 user-agent is not overridable and stays native.
const GRPC_GO_USER_AGENT: &str = "grpc-go/1.65.0";
/// grpc-go `minConnectTimeout`: the connect deadline for one transport attempt.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
/// Go `net.Dialer` default Happy Eyeballs fallback delay.
const FALLBACK_DELAY: Duration = Duration::from_millis(300);
/// Go `partialDeadline` minimum per-address share.
const MIN_ADDRESS_TIMEOUT: Duration = Duration::from_secs(2);
/// grpc-go `defaultClientMaxHeaderListSize` (hyper would default to 16 KiB).
const MAX_HEADER_LIST_SIZE: u32 = 16 << 20;
/// grpc-go `readServerPreface` error prefix; also the marker that survives
/// h2's conversion of our watchdog's io::Error.
const PREFACE_ERROR: &str = "error reading server preface: ";
/// grpc-go `newHTTP2Client` preface write failure prefix.
const WRITE_PREFACE_ERROR: &str = "transport: failed to write client preface: ";
/// grpc-go `ClientHandshake` failure prefix.
const HANDSHAKE_ERROR: &str = "transport: authentication handshake failed: ";

/// One Flight subscription target: channel, credential snapshot and ticket.
/// Reconnects reuse all three; nothing here refreshes credentials. Failures
/// grpc-go reports from `DoGet` rather than while constructing are kept as
/// the status DoGet will return.
pub(super) struct FlightConnection {
    client: Result<FlightClient, FlightStatus>,
    metadata: Result<MetadataMap, FlightStatus>,
    ticket: Bytes,
}
impl fmt::Debug for FlightConnection {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("FlightConnection")
            .field("ticket", &self.ticket)
            .finish_non_exhaustive()
    }
}

/// A gRPC status as grpc-go reports it: the raw numeric code (unknown codes
/// print as `Code(N)`) and the message bytes after Go's percent-decoding,
/// which need not be UTF-8.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct FlightStatus {
    code: u32,
    message: Vec<u8>,
}
impl FlightStatus {
    pub(super) fn new(code: u32, message: impl Into<Vec<u8>>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
    pub(super) fn code(&self) -> u32 {
        self.code
    }
    /// The message bytes after Go's percent-decoding (not necessarily UTF-8).
    pub(super) fn message(&self) -> &[u8] {
        &self.message
    }
    /// grpc-go tail's `isStreamEnd` codes: OK, Canceled, Unavailable.
    pub(super) fn is_stream_end(&self) -> bool {
        matches!(self.code, 0 | 1 | 14)
    }
    /// Go `status.Error().Error()`: "rpc error: code = X desc = MSG".
    pub(super) fn go_error(&self) -> Vec<u8> {
        let mut bytes = format!("rpc error: code = {} desc = ", go_code(self.code)).into_bytes();
        bytes.extend_from_slice(self.message());
        bytes
    }
    fn unavailable(message: impl Into<Vec<u8>>) -> Self {
        Self::new(Code::Unavailable as u32, message)
    }
    fn internal(message: impl Into<Vec<u8>>) -> Self {
        Self::new(Code::Internal as u32, message)
    }
    fn dial(detail: impl fmt::Display) -> Self {
        Self::unavailable(connection_error(&format!(
            "transport: Error while dialing: {detail}"
        )))
    }
}

/// Go order: FetchAbout (shared HTTP, including OAuth refresh) -> transport
/// credentials (invalid URL / scheme) -> client -> tailAuthMetadata.
pub(super) async fn discover(
    store: &ProfileStore,
    dataset: &[u8],
) -> Result<FlightConnection, CliError> {
    let ticket = ticket(dataset);
    let profile = store
        .active_profile()
        .map_err(|err| CliError::new(err.code, err.message))?;
    let client = ApiClient::new(profile, store.clone())?;
    let port = grpc_port(&client).await?;
    // Go's tail reads the same profile value the HTTP client refreshed.
    let profile = client.profile();
    let target = FlightTarget::new(&profile.url, profile.cloud)
        .map_err(|message| CliError::new(ErrorCode::CommandFailed, message))?;
    let client = target.client(port, &ProxyEnvironment::from_env(), enforce_alpn());
    let auth =
        Auth::resolve(&profile).map_err(|err| CliError::new(ErrorCode::CommandFailed, err.0))?;
    Ok(FlightConnection {
        client,
        metadata: metadata(&auth),
        ticket,
    })
}

impl FlightConnection {
    /// One application DoGet with the same ticket and metadata and no
    /// deadline. Like grpc-go, a first attempt that the server provably did
    /// not process (refused before any message) is transparently re-issued
    /// once, here or inside the returned stream; every other reconnect
    /// decision belongs to the session.
    pub(super) async fn do_get(&mut self) -> Result<FlightStream, FlightStatus> {
        // grpc-go validates outgoing metadata before it waits for a transport.
        let metadata = self.metadata.clone()?;
        let client = self.client.as_mut().map_err(|status| status.clone())?;
        let request = DoGet {
            metadata,
            ticket: self.ticket.clone(),
        };
        let (grpc, _) = client.ready();
        match request.send(grpc).await {
            Ok((inner, exchange)) => Ok(FlightStream {
                state: StreamState::Reading(Box::new(inner)),
                exchange,
                received: false,
                retry: Some(Retry {
                    client: client.clone(),
                    request,
                }),
            }),
            Err(attempt) if attempt.unprocessed => {
                let (grpc, _) = client.ready();
                let (inner, exchange) = request.send(grpc).await.map_err(|second| second.status)?;
                Ok(FlightStream {
                    state: StreamState::Reading(Box::new(inner)),
                    exchange,
                    received: false,
                    retry: None,
                })
            }
            Err(attempt) => Err(attempt.status),
        }
    }
}

/// The channel the next DoGet uses, and how to replace it. grpc-go does not
/// start new streams on a transport that received GOAWAY; neither does this.
/// The slot is shared with a stream's transparent retry, so a channel that
/// retry builds is the one the next application DoGet reuses.
#[derive(Clone)]
struct FlightClient {
    factory: Arc<ClientFactory>,
    slot: Arc<std::sync::Mutex<(FlightServiceClient<GoChannel>, Wires)>>,
}
impl fmt::Debug for FlightClient {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("FlightClient").finish_non_exhaustive()
    }
}
impl FlightClient {
    fn new(factory: Arc<ClientFactory>) -> Self {
        let slot = Arc::new(std::sync::Mutex::new(factory.build()));
        Self { factory, slot }
    }

    /// The current channel, replaced first if its connection saw a GOAWAY.
    fn ready(&self) -> (FlightServiceClient<GoChannel>, Wires) {
        let mut current = self.slot.lock().expect("client slot lock");
        if current.1.current().is_some_and(|wire| wire.draining()) {
            *current = self.factory.build();
        }
        (current.0.clone(), current.1.clone())
    }
}

/// Builds a fresh channel (and so a fresh connection) for one target.
struct ClientFactory {
    endpoint: Endpoint,
    plan: Arc<DialPlan>,
}
impl ClientFactory {
    fn build(&self) -> (FlightServiceClient<GoChannel>, Wires) {
        let wires = Wires::default();
        let dialer = Dialer {
            plan: self.plan.clone(),
            wires: wires.clone(),
        };
        let channel = GoChannel {
            inner: self.endpoint.connect_with_connector_lazy(dialer),
            wires: wires.clone(),
        };
        let grpc =
            FlightServiceClient::new(channel).max_decoding_message_size(MAX_RECEIVE_MESSAGE_SIZE);
        (grpc, wires)
    }
}

/// One DoGet request, re-sendable for the transparent retry.
#[derive(Clone)]
struct DoGet {
    metadata: MetadataMap,
    ticket: Bytes,
}

/// A failed attempt, and whether grpc-go would call it unprocessed.
struct FailedAttempt {
    status: FlightStatus,
    unprocessed: bool,
}

impl DoGet {
    /// Sends one attempt. Each physical DoGet is one call of this function;
    /// the session's subscription count is per `do_get`, not per attempt.
    async fn send(
        &self,
        mut grpc: FlightServiceClient<GoChannel>,
    ) -> Result<(Streaming<FlightData>, Exchange), FailedAttempt> {
        let exchange = Exchange::default();
        let mut request = Request::new(Ticket {
            ticket: self.ticket.clone(),
        });
        *request.metadata_mut() = self.metadata.clone();
        // In-process only: the response layer reports Go's status here.
        request.extensions_mut().insert(exchange.clone());
        match grpc.do_get(request).await {
            Ok(response) => Ok((response.into_inner(), exchange)),
            Err(status) => {
                let unprocessed = exchange.unprocessed();
                let status = exchange.take().unwrap_or_else(|| classify(status));
                Err(FailedAttempt {
                    status,
                    unprocessed,
                })
            }
        }
    }
}

/// What the stream needs to re-issue its first attempt.
struct Retry {
    client: FlightClient,
    request: DoGet,
}

type AttemptFuture =
    Pin<Box<dyn Future<Output = Result<(Streaming<FlightData>, Exchange), FailedAttempt>> + Send>>;

enum StreamState {
    Reading(Box<Streaming<FlightData>>),
    Retrying(AttemptFuture),
    /// A grpc-go stream ends at its first error; tonic may yield more.
    Done,
}

/// The owned DoGet response stream. Every error is final: Go's header and
/// trailer semantics, connection-loss classification, raw message bytes and
/// the one transparent retry of an unprocessed first attempt are applied.
pub(super) struct FlightStream {
    state: StreamState,
    exchange: Exchange,
    /// A complete message was delivered: grpc-go's attempt is committed.
    received: bool,
    /// Present only while the first attempt may still be retried.
    retry: Option<Retry>,
}
impl fmt::Debug for FlightStream {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("FlightStream").finish_non_exhaustive()
    }
}
impl futures_core::Stream for FlightStream {
    type Item = Result<FlightData, FlightStatus>;

    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let this = self.get_mut();
        loop {
            match &mut this.state {
                StreamState::Done => return Poll::Ready(None),
                StreamState::Retrying(attempt) => match ready!(attempt.as_mut().poll(cx)) {
                    Ok((inner, exchange)) => {
                        this.state = StreamState::Reading(Box::new(inner));
                        this.exchange = exchange;
                    }
                    Err(failed) => {
                        this.state = StreamState::Done;
                        return Poll::Ready(Some(Err(failed.status)));
                    }
                },
                StreamState::Reading(inner) => match ready!(Pin::new(inner.as_mut()).poll_next(cx))
                {
                    Some(Ok(data)) => {
                        this.received = true;
                        this.retry = None;
                        return Poll::Ready(Some(Ok(data)));
                    }
                    // The response layer's status is authoritative: it came
                    // from the server's trailers or from the observed frames
                    // for this stream.
                    Some(Err(status)) => {
                        let unprocessed = this.exchange.unprocessed();
                        let status = this.exchange.take().unwrap_or_else(|| classify(status));
                        match this.retry.take() {
                            Some(retry) if unprocessed && !this.received => {
                                this.state = StreamState::Retrying(Box::pin(async move {
                                    let (grpc, _) = retry.client.ready();
                                    retry.request.send(grpc).await
                                }));
                            }
                            _ => {
                                this.state = StreamState::Done;
                                return Poll::Ready(Some(Err(status)));
                            }
                        }
                    }
                    None => {
                        this.state = StreamState::Done;
                        return Poll::Ready(None);
                    }
                },
            }
        }
    }
}

/// Per-request state shared by the attempt, the response layer and the
/// stream: Go's status once the layer has determined it, and whether
/// grpc-go would consider the stream unprocessed.
#[derive(Clone, Default)]
struct Exchange(Arc<std::sync::Mutex<ExchangeState>>);
#[derive(Default)]
struct ExchangeState {
    status: Option<FlightStatus>,
    unprocessed: bool,
}
impl Exchange {
    fn set(&self, status: FlightStatus) {
        self.0.lock().expect("exchange lock").status = Some(status);
    }
    fn record(&self, status: FlightStatus, unprocessed: bool) {
        let mut state = self.0.lock().expect("exchange lock");
        state.status = Some(status);
        state.unprocessed = unprocessed;
    }
    fn take(&self) -> Option<FlightStatus> {
        self.0.lock().expect("exchange lock").status.take()
    }
    fn unprocessed(&self) -> bool {
        self.0.lock().expect("exchange lock").unprocessed
    }
}

/// A tonic status raised locally (decoding, transport), classified as
/// grpc-go would: losing the transport (I/O failure, EOF, GOAWAY, closed
/// connection) closes the stream with Unavailable, and tonic's receive-cap
/// error becomes Go's ResourceExhausted text.
fn classify(status: Status) -> FlightStatus {
    if let Some(message) = connection_loss(&status) {
        return FlightStatus::unavailable(message);
    }
    if let Some((found, limit)) = receive_limit_exceeded(&status) {
        return FlightStatus::new(
            Code::ResourceExhausted as u32,
            format!("grpc: received message larger than max ({found} vs. {limit})"),
        );
    }
    FlightStatus::new(status.code() as u32, status.message())
}

fn connection_loss(status: &Status) -> Option<String> {
    // Our preface watchdog's text survives both hyper's direct I/O error and
    // h2's kind-and-text copy of it.
    let io_loss = |io: &io::Error| {
        let text = io.to_string();
        if text.starts_with(PREFACE_ERROR) || text.starts_with(WRITE_PREFACE_ERROR) {
            connection_error(&text)
        } else {
            status.message().to_owned()
        }
    };
    let mut source = std::error::Error::source(status);
    while let Some(error) = source {
        if let Some(io) = error.downcast_ref::<io::Error>() {
            return Some(io_loss(io));
        }
        if let Some(h2) = error.downcast_ref::<h2::Error>() {
            if let Some(io) = h2.get_io() {
                return Some(io_loss(io));
            }
            return h2.is_go_away().then(|| status.message().to_owned());
        }
        if error
            .downcast_ref::<hyper::Error>()
            .is_some_and(hyper::Error::is_closed)
        {
            return Some(status.message().to_owned());
        }
        source = error.source();
    }
    None
}

/// Go `codes.Code.String()`.
fn go_code(code: u32) -> String {
    let name = match code {
        0 => "OK",
        1 => "Canceled",
        2 => "Unknown",
        3 => "InvalidArgument",
        4 => "DeadlineExceeded",
        5 => "NotFound",
        6 => "AlreadyExists",
        7 => "PermissionDenied",
        8 => "ResourceExhausted",
        9 => "FailedPrecondition",
        10 => "Aborted",
        11 => "OutOfRange",
        12 => "Unimplemented",
        13 => "Internal",
        14 => "Unavailable",
        15 => "DataLoss",
        16 => "Unauthenticated",
        _ => return format!("Code({code})"),
    };
    name.into()
}

/// Exactly tonic 0.14's locally built decode-limit status, nothing looser.
/// Only local statuses reach this: server statuses arrive through the
/// response layer. The detail and metadata checks are kept as a guard.
fn receive_limit_exceeded(status: &Status) -> Option<(u64, u64)> {
    if status.code() != Code::OutOfRange
        || !status.details().is_empty()
        || !status.metadata().is_empty()
    {
        return None;
    }
    let rest = status
        .message()
        .strip_prefix("Error, decoded message length too large: found ")?;
    let (found, rest) = rest.split_once(" bytes, the limit is: ")?;
    let limit = rest.strip_suffix(" bytes")?;
    let number = |raw: &str| {
        (!raw.is_empty() && raw.bytes().all(|b| b.is_ascii_digit()))
            .then(|| raw.parse().ok())
            .flatten()
    };
    let (found, limit) = (number(found)?, number(limit)?);
    (limit == MAX_RECEIVE_MESSAGE_SIZE as u64 && found > limit).then_some((found, limit))
}

/// Go `json.Marshal(struct{Stream string `json:"stream"`})`, HTML-escaped.
fn ticket(dataset: impl AsRef<[u8]>) -> Bytes {
    let quoted = crate::error::json_string_bytes(dataset.as_ref());
    let quoted = quoted
        .replace('&', "\\u0026")
        .replace('<', "\\u003c")
        .replace('>', "\\u003e")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029");
    Bytes::from(format!("{{\"stream\":{quoted}}}"))
}

/// `analytics.FetchAbout`: only 200 decodes; every other status is an error
/// carrying the whole body.
async fn grpc_port(client: &ApiClient) -> Result<i64, CliError> {
    let response = client.standard(Method::GET, "about", None).await?;
    if response.status != 200 {
        let mut message = format!(
            "Request Failed\nStatus Code: {}\nResponse: ",
            response.status_text()
        )
        .into_bytes();
        message.extend_from_slice(&response.body);
        message.push(b'\n');
        return Err(CliError::http(
            response.status,
            go_string(&message),
            format!("request failed: HTTP {}", response.status),
        )
        .with_raw_message(message));
    }
    about_grpc_port(&response.body)
        .map_err(|message| CliError::new(ErrorCode::CommandFailed, message))
}

/// `json.Unmarshal` into `analytics.About`, keeping only grpcPort. Every known
/// field is type-checked in input order, as status's decoder does.
fn about_grpc_port(body: &[u8]) -> Result<i64, String> {
    let value = gojson::decode_ordered(body)?;
    if value.is_null() {
        return Ok(0);
    }
    let object = value.as_object().ok_or_else(|| {
        format!(
            "json: cannot unmarshal {} into Go value of type analytics.About",
            value.kind()
        )
    })?;
    let mut port = 0;
    for (name, value) in object {
        let lower = name.to_ascii_lowercase();
        let (canonical, expected) = match lower.as_str() {
            "version" => ("version", "string"),
            "uiversion" => ("uiVersion", "string"),
            "commit" => ("commit", "string"),
            "deploymentid" => ("deploymentId", "string"),
            "updateavailable" => ("updateAvailable", "bool"),
            "latestversion" => ("latestVersion", "string"),
            "llmactive" => ("llmActive", "bool"),
            "llmprovider" => ("llmProvider", "string"),
            "oidcactive" => ("oidcActive", "bool"),
            "mode" => ("mode", "string"),
            "staging" => ("staging", "string"),
            "hottier" => ("hotTier", "string"),
            "grpcport" => ("grpcPort", "int"),
            "queryengine" => ("queryEngine", "string"),
            "store" => ("store", "analytics.Store"),
            "analytics" => ("analytics", "analytics.Analytics"),
            _ => continue,
        };
        if value.is_null() {
            continue;
        }
        let valid = match expected {
            "string" => value.is_string(),
            "bool" => value.is_boolean(),
            "int" => value.as_i64().is_some(),
            _ => value.is_object(),
        };
        if !valid {
            let mut actual = value.kind().to_owned();
            if expected == "int" && value.is_number() {
                actual.push(' ');
                actual.push_str(&value.to_string());
            }
            return Err(format!(
                "json: cannot unmarshal {actual} into Go struct field About.{canonical} of type {expected}"
            ));
        }
        if lower == "grpcport" {
            port = value.as_i64().expect("integer was checked");
        }
        if let Some(nested) = value.as_object() {
            for (field, value) in nested {
                let canonical_field = match (lower.as_str(), field.to_ascii_lowercase().as_str()) {
                    ("store", "type") => "type",
                    ("store", "path") => "path",
                    ("analytics", "claritytag") => "clarityTag",
                    _ => continue,
                };
                if !value.is_string() && !value.is_null() {
                    return Err(format!(
                        "json: cannot unmarshal {} into Go struct field {}.{canonical}.{canonical_field} of type string",
                        value.kind(),
                        if lower == "store" {
                            "Store"
                        } else {
                            "Analytics"
                        }
                    ));
                }
            }
        }
    }
    Ok(port)
}

/// `tailAuthMetadata`: raw values, no HTTP cookie sanitization. grpc-go
/// rejects non-printable values when the stream starts (Go map order picks
/// which key is named if two are invalid; this checks in a fixed order).
fn metadata(auth: &Auth) -> Result<MetadataMap, FlightStatus> {
    let pairs: Vec<(&'static str, String)> = match auth {
        Auth::CloudApiKey { api_key, tenant_id } => vec![
            ("x-api-key", api_key.clone()),
            ("x-p-tenant", tenant_id.clone()),
        ],
        Auth::CloudOAuth {
            session_token,
            tenant_id,
        } => vec![
            ("cookie", format!("session={session_token}")),
            ("x-p-tenant", tenant_id.clone()),
        ],
        Auth::SelfHostedApiKey { api_key } => vec![("x-api-key", api_key.clone())],
        Auth::SelfHostedBasic { username, password } => vec![(
            "authorization",
            format!(
                "Basic {}",
                STANDARD.encode(format!("{username}:{password}"))
            ),
        )],
    };
    let mut map = MetadataMap::new();
    for (key, value) in pairs {
        let invalid = || {
            FlightStatus::internal(format!(
                "header key {} contains value with non-printable ASCII characters",
                quote(key)
            ))
        };
        if value.bytes().any(|b| !(0x20..=0x7e).contains(&b)) {
            return Err(invalid());
        }
        let mut value = MetadataValue::try_from(value.as_str()).map_err(|_| invalid())?;
        value.set_sensitive(key != "x-p-tenant");
        map.insert(key, value);
    }
    Ok(map)
}

/// The profile URL facts Go's tail uses: raw hostname and TLS selection.
#[derive(Debug, PartialEq)]
struct FlightTarget {
    host: String,
    tls: bool,
}
impl FlightTarget {
    /// `tailTransportCredentials`: url.Parse + Hostname, then scheme policy.
    fn new(raw: &str, cloud: bool) -> Result<Self, String> {
        let invalid = || format!("invalid profile URL {}", quote(raw));
        let (scheme, host) = go_url_scheme_host(raw).ok_or_else(invalid)?;
        if host.is_empty() {
            return Err(invalid());
        }
        let lower = scheme.to_ascii_lowercase();
        let tls = if cloud || lower == "https" {
            true
        } else if lower == "http" {
            false
        } else {
            // Go's url.Parse lowercases the scheme it reports.
            return Err(format!("unsupported profile URL scheme {}", quote(&lower)));
        };
        Ok(Self {
            host: host.into(),
            tls,
        })
    }

    /// `net.JoinHostPort(hostname, fmt.Sprint(port))`.
    fn address(&self, port: i64) -> String {
        if self.host.contains(':') {
            format!("[{}]:{port}", self.host)
        } else {
            format!("{}:{port}", self.host)
        }
    }

    /// Nothing here fails before DoGet; see the notes on Go's eager dial.
    fn client(
        &self,
        port: i64,
        proxy: &ProxyEnvironment,
        enforce_alpn: bool,
    ) -> Result<FlightClient, FlightStatus> {
        self.client_with(port, proxy, enforce_alpn, CONNECT_TIMEOUT)
    }

    fn client_with(
        &self,
        port: i64,
        proxy: &ProxyEnvironment,
        enforce_alpn: bool,
        connect_timeout: Duration,
    ) -> Result<FlightClient, FlightStatus> {
        let address = self.address(port);
        if !(0..=i64::from(u16::MAX)).contains(&port) {
            return Err(FlightStatus::dial(format!(
                "dial tcp: address {port}: invalid port"
            )));
        }
        // The endpoint URI is always http so tonic never layers its own TLS;
        // the dialer owns TLS to keep grpc-go's single connect deadline.
        let mut endpoint = Endpoint::from_shared(format!("http://{address}"))
            .map_err(|err| FlightStatus::dial(format!("dial tcp: address {address}: {err}")))?
            .http2_max_header_list_size(MAX_HEADER_LIST_SIZE)
            // grpc-go: 64 KiB initial windows grown by BDP up to 16 MiB.
            .http2_adaptive_window(true);
        let tls = self.tls.then(|| TlsPlan {
            host: self.host.clone(),
            enforce_alpn,
        });
        if self.tls {
            // grpc-go determineAuthority: TLS uses the creds ServerName (the
            // bare hostname) as :authority. An IPv6 literal needs brackets.
            let authority = if self.host.contains(':') {
                format!("[{}]", self.host)
            } else {
                self.host.clone()
            };
            let origin = Uri::try_from(format!("https://{authority}"))
                .map_err(|err| FlightStatus::dial(format!("dial tcp: address {address}: {err}")))?;
            endpoint = endpoint.origin(origin);
        }
        let factory = Arc::new(ClientFactory {
            endpoint,
            plan: Arc::new(DialPlan {
                address: address.clone(),
                proxy: proxy.route(&address),
                tls,
                connect_timeout,
            }),
        });
        Ok(FlightClient::new(factory))
    }
}

type BoxError = Box<dyn std::error::Error + Send + Sync>;

/// The response layer between tonic's channel and the generated client. It
/// applies grpc-go's `operateHeaders` rules to the initial headers and to
/// trailers, records Go's status in the request's `Exchange` (raw message
/// bytes included), and hands tonic only a sanitized numeric `grpc-status`.
/// tonic therefore never parses a remote `grpc-message` or `-bin` header,
/// so it cannot panic on one or change the code because of one.
#[derive(Clone)]
struct GoChannel {
    inner: Channel,
    wires: Wires,
}

impl fmt::Debug for GoChannel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("GoChannel").finish_non_exhaustive()
    }
}

/// The layer's own rejection; Go's status travels in the exchange.
#[derive(Debug)]
struct Rejected;
impl fmt::Display for Rejected {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Flight response rejected")
    }
}
impl std::error::Error for Rejected {}

impl tower_service::Service<http::Request<tonic::body::Body>> for GoChannel {
    type Response = http::Response<GoBody>;
    type Error = BoxError;
    type Future = Pin<Box<dyn Future<Output = Result<Self::Response, BoxError>> + Send>>;

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), BoxError>> {
        tower_service::Service::poll_ready(&mut self.inner, cx).map_err(Into::into)
    }

    fn call(&mut self, request: http::Request<tonic::body::Body>) -> Self::Future {
        let exchange = request.extensions().get::<Exchange>().cloned();
        let wires = self.wires.clone();
        let call = tower_service::Service::call(&mut self.inner, request);
        Box::pin(async move {
            let response = match call.await {
                Ok(response) => response,
                Err(error) => {
                    // A peer RST_STREAM or GOAWAY before the response headers.
                    if let Some(exchange) = &exchange
                        && let Some((status, unprocessed)) = remote_end(&error, false, 0)
                    {
                        exchange.record(status, unprocessed);
                    }
                    return Err(error.into());
                }
            };
            // The session runs one DoGet at a time, so the current connection
            // carried this response as its newest client stream.
            let binding = wires.current().map(|wire| {
                let stream = wire.opened();
                (wire, stream)
            });
            let (mut parts, body) = response.into_parts();
            let ended = hyper::body::Body::is_end_stream(&body);
            let status = match response_headers(parts.status, &parts.headers, ended) {
                Ok(status) => status,
                Err(status) => {
                    if let Some(exchange) = &exchange {
                        exchange.set(status);
                    }
                    return Err(Box::new(Rejected) as BoxError);
                }
            };
            // grpc-go records grpc-encoding and checks it per message, after
            // reading the whole message; tonic (with no decompressor) would
            // reject the response at once. The body layer applies Go's check.
            let encoding = parts
                .headers
                .get_all("grpc-encoding")
                .iter()
                .next_back()
                .map(|value| value.as_bytes().to_vec());
            // Go ignores a status in non-final headers; tonic must not parse
            // any remote status, message, binary field or encoding.
            for name in parts
                .headers
                .keys()
                .filter(|name| {
                    let name = name.as_str();
                    name == "grpc-status"
                        || name == "grpc-message"
                        || name == "grpc-encoding"
                        || name.ends_with("-bin")
                })
                .cloned()
                .collect::<Vec<_>>()
            {
                parts.headers.remove(name);
            }
            if let Some(status) = status {
                // Trailers-Only: tonic needs the code to end the call.
                parts
                    .headers
                    .insert("grpc-status", wire_code(status.code()));
                if let Some(exchange) = &exchange {
                    exchange.set(status);
                }
            }
            let body = GoBody {
                inner: body,
                exchange,
                binding,
                trailers: false,
                framing: Framing::default(),
                encoding,
                rejected: None,
                failed: false,
            };
            Ok(http::Response::from_parts(parts, body))
        })
    }
}

/// A response body that substitutes sanitized trailers, follows gRPC
/// message framing, and records how a stream ended when hyper hides it.
struct GoBody {
    inner: tonic::body::Body,
    exchange: Option<Exchange>,
    binding: Option<(Arc<Wire>, u32)>,
    trailers: bool,
    framing: Framing,
    /// The response's announced `grpc-encoding` (last value), if any.
    encoding: Option<Vec<u8>>,
    /// Go's status for a rejected message, emitted after every complete
    /// message forwarded before it.
    rejected: Option<FlightStatus>,
    failed: bool,
}

/// gRPC length-prefixed framing across DATA frames, as grpc-go's
/// `parser.recvMsg` reads it with `io.ReadFull`.
#[derive(Debug, Default)]
struct Framing {
    header: [u8; 5],
    /// Prefix bytes of the current message seen so far (0 between messages).
    filled: usize,
    remaining: u64,
    complete: u64,
    /// The current message has a nonzero payload-format flag: none of its
    /// bytes are forwarded to tonic.
    held: bool,
}
impl Framing {
    /// Follows one DATA chunk. Returns how many leading bytes tonic may see
    /// (whole flag-0 messages and flag-0 progress) and, once grpc-go would
    /// fail a nonzero-flag message, its status. grpc-go's order: the size
    /// gate when the prefix is complete, then the whole body, then the flag
    /// check; an end inside the message is `unexpected EOF` (`partial`).
    fn take(&mut self, data: &[u8], encoding: Option<&[u8]>) -> (usize, Option<FlightStatus>) {
        let mut forward = if self.held { 0 } else { data.len() };
        let mut index = 0;
        while index < data.len() {
            if self.filled < self.header.len() {
                if self.filled == 0 && data[index] != 0 {
                    self.held = true;
                    forward = index;
                }
                self.header[self.filled] = data[index];
                self.filled += 1;
                index += 1;
                if self.filled == self.header.len() {
                    self.remaining = u64::from(u32::from_be_bytes(
                        self.header[1..].try_into().expect("4 bytes"),
                    ));
                    if self.held && self.remaining > MAX_RECEIVE_MESSAGE_SIZE as u64 {
                        let status = FlightStatus::new(
                            Code::ResourceExhausted as u32,
                            format!(
                                "grpc: received message larger than max ({} vs. {MAX_RECEIVE_MESSAGE_SIZE})",
                                self.remaining
                            ),
                        );
                        return (forward, Some(status));
                    }
                }
            } else {
                let take = self.remaining.min((data.len() - index) as u64);
                self.remaining -= take;
                index += take as usize;
            }
            if self.filled == self.header.len() && self.remaining == 0 {
                self.filled = 0;
                if self.held {
                    return (forward, Some(payload_status(self.header[0], encoding)));
                }
                self.complete += 1;
            }
        }
        (forward, None)
    }

    /// Inside a message: an io.EOF here becomes `io.ErrUnexpectedEOF`.
    fn partial(&self) -> bool {
        self.filled > 0
    }
}

/// grpc-go `checkRecvPayload` for a nonzero payload-format flag, with no
/// decompressor registered (the pinned Go client registers none).
fn payload_status(flag: u8, encoding: Option<&[u8]>) -> FlightStatus {
    match (flag, encoding) {
        (1, None | Some(b"" | b"identity")) => {
            FlightStatus::internal("grpc: compressed flag set with identity or empty encoding")
        }
        (1, Some(encoding)) => FlightStatus::new(
            Code::Unimplemented as u32,
            format!(
                "grpc: Decompressor is not installed for grpc-encoding {}",
                go_quote_bytes(encoding)
            ),
        ),
        (flag, _) => {
            FlightStatus::internal(format!("grpc: received unexpected payload format {flag}"))
        }
    }
}

/// grpc-go's status when `io.ReadFull` meets io.EOF inside a message.
fn unexpected_eof() -> FlightStatus {
    FlightStatus::internal("unexpected EOF")
}

/// The Go status for an error a peer frame caused: RST_STREAM (io.EOF to
/// the reader, so `unexpected EOF` mid-message) or a GOAWAY that excludes
/// the stream (the drain error, unprocessed). Local and library h2 errors
/// return `None` and stay with `classify`.
fn remote_end(
    error: &(dyn std::error::Error + 'static),
    partial: bool,
    complete: u64,
) -> Option<(FlightStatus, bool)> {
    let mut source = Some(error);
    while let Some(error) = source {
        if let Some(h2) = error.downcast_ref::<h2::Error>() {
            if !h2.is_remote() {
                return None;
            }
            if h2.is_go_away() {
                return Some((refused(), complete == 0));
            }
            let code = u32::from(h2.reason()?);
            let unprocessed = code == REFUSED_STREAM && complete == 0;
            let status = if partial {
                unexpected_eof()
            } else {
                reset_status(code)
            };
            return Some((status, unprocessed));
        }
        source = error.source();
    }
    None
}

impl GoBody {
    fn record(&self, status: FlightStatus, unprocessed: bool) {
        if let Some(exchange) = &self.exchange {
            exchange.record(status, unprocessed);
        }
    }

    /// A clean end without trailers: hyper turns a GOAWAY or RST_STREAM with
    /// NO_ERROR into one, so the frames this stream received explain it.
    fn ended_cleanly(&self) {
        let Some((wire, stream)) = &self.binding else {
            return;
        };
        let partial = self.framing.partial();
        let unprocessed = self.framing.complete == 0;
        match wire.terminal(*stream) {
            Some(Terminal::Refused) => self.record(refused(), unprocessed),
            Some(Terminal::Reset(code)) => self.record(
                if partial {
                    unexpected_eof()
                } else {
                    reset_status(code)
                },
                code == REFUSED_STREAM && unprocessed,
            ),
            Some(Terminal::DataEnd) => self.record(
                if partial {
                    unexpected_eof()
                } else {
                    FlightStatus::internal("server closed the stream without sending trailers")
                },
                false,
            ),
            // The server's final HEADERS were observed but did not arrive as
            // trailers (h2 rejected the block): tonic and `classify` decide.
            Some(Terminal::Trailers) | None => {}
        }
    }
}
impl hyper::body::Body for GoBody {
    type Data = Bytes;
    type Error = Status;

    fn poll_frame(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<hyper::body::Frame<Bytes>, Status>>> {
        let this = self.get_mut();
        loop {
            if this.failed {
                return Poll::Ready(None);
            }
            if let Some(status) = this.rejected.take() {
                // Every message before the rejected one has been forwarded
                // and decoded by now, so this status cannot displace one.
                this.failed = true;
                this.record(status, false);
                return Poll::Ready(Some(Err(Status::internal("Flight message rejected"))));
            }
            let polled = ready!(Pin::new(&mut this.inner).poll_frame(cx));
            if let Some(Ok(frame)) = &polled
                && let Some(data) = frame.data_ref()
            {
                let data = data.clone();
                let (forward, rejected) = this.framing.take(&data, this.encoding.as_deref());
                this.rejected = rejected;
                if forward == 0 {
                    continue;
                }
                return Poll::Ready(Some(Ok(hyper::body::Frame::data(data.slice(..forward)))));
            }
            return Poll::Ready(this.finish(polled));
        }
    }

    fn is_end_stream(&self) -> bool {
        self.inner.is_end_stream()
    }

    fn size_hint(&self) -> hyper::body::SizeHint {
        self.inner.size_hint()
    }
}

impl GoBody {
    /// Trailers, errors and the end of the body.
    fn finish(
        &mut self,
        polled: Option<Result<hyper::body::Frame<Bytes>, Status>>,
    ) -> Option<Result<hyper::body::Frame<Bytes>, Status>> {
        let this = self;
        match polled {
            Some(Ok(frame)) => match frame.into_trailers() {
                Ok(trailers) => {
                    this.trailers = true;
                    // Trailers deliver io.EOF to grpc-go's reader whatever
                    // their status, so a partial message ends as unexpected EOF.
                    let status = if this.framing.partial() {
                        unexpected_eof()
                    } else {
                        trailers_status(&trailers)
                    };
                    let mut sanitized = http::HeaderMap::new();
                    sanitized.insert("grpc-status", wire_code(status.code()));
                    this.record(status, false);
                    Some(Ok(hyper::body::Frame::trailers(sanitized)))
                }
                Err(frame) => Some(Ok(frame)),
            },
            Some(Err(error)) => {
                if let Some((status, unprocessed)) =
                    remote_end(&error, this.framing.partial(), this.framing.complete)
                {
                    this.record(status, unprocessed);
                }
                Some(Err(error))
            }
            None => {
                if !this.trailers {
                    this.ended_cleanly();
                }
                None
            }
        }
    }
}

/// The numeric `grpc-status` handed to tonic: only zero versus non-zero
/// matters, since the exchange holds the real status.
fn wire_code(code: u32) -> http::HeaderValue {
    http::HeaderValue::from(if code <= 16 { code } else { 2 })
}

/// grpc-go `operateHeaders` for the initial response headers, in its
/// order. `Err` is Go's fatal status; `Ok(Some)` is a Trailers-Only status.
fn response_headers(
    http_status: http::StatusCode,
    headers: &http::HeaderMap,
    ended: bool,
) -> Result<Option<FlightStatus>, FlightStatus> {
    // A malformed grpc-status ends the field loop at once.
    let code = grpc_status(headers)?;
    let mut is_grpc = false;
    let mut content_type_err = Some("malformed header: missing HTTP content-type".to_owned());
    for value in headers.get_all(http::header::CONTENT_TYPE) {
        let value = value.as_bytes();
        let valid = value == b"application/grpc"
            || value
                .strip_prefix(b"application/grpc")
                .is_some_and(|rest| rest.starts_with(b"+") || rest.starts_with(b";"));
        if valid {
            content_type_err = None;
            is_grpc = true;
        } else {
            content_type_err = Some(format!(
                "transport: received unexpected content-type {}",
                go_quote_bytes(value)
            ));
        }
    }
    let http_err = (http_status != http::StatusCode::OK).then(|| {
        format!(
            "unexpected HTTP status code received from server: {} ({})",
            http_status.as_u16(),
            go_status_text(http_status.as_u16())
        )
    });
    if !is_grpc || http_err.is_some() {
        // grpc-go HTTPStatusConvTab; a status it lacks (including 200) is Unknown.
        let code = match http_status.as_u16() {
            400 => Code::Internal,
            401 => Code::Unauthenticated,
            403 => Code::PermissionDenied,
            404 => Code::Unimplemented,
            429 | 502 | 503 | 504 => Code::Unavailable,
            _ => Code::Unknown,
        };
        let message = [http_err, content_type_err]
            .into_iter()
            .flatten()
            .collect::<Vec<_>>()
            .join("; ");
        return Err(FlightStatus::new(code as u32, message));
    }
    metadata_errors(headers)?;
    Ok(ended.then(|| final_status(code, headers)))
}

/// grpc-go `istatus.NewWithProto(rawStatusCode, grpcMessage,
/// mdata["grpc-status-details-bin"])` for a final (trailer) field set whose
/// fields already passed `metadata_errors`.
fn final_status(code: Option<u32>, headers: &http::HeaderMap) -> FlightStatus {
    let code = code.unwrap_or(Code::Unknown as u32);
    let message = grpc_message(headers);
    let mut details = headers.get_all("grpc-status-details-bin").iter();
    let (Some(value), None) = (details.next(), details.next()) else {
        // Absent or repeated: ignored.
        return FlightStatus::new(code, message);
    };
    let Some(proto) = go_base64_decode(value.as_bytes())
        .and_then(|bytes| <RpcStatus as prost::Message>::decode(bytes.as_slice()).ok())
    else {
        // "Probably not a google.rpc.Status proto": ignored.
        return FlightStatus::new(code, message);
    };
    if proto.code == code as i32 {
        return FlightStatus::new(proto.code as u32, proto.message.into_bytes());
    }
    // The `%+v` prototext rendering is approximated (declared residue).
    FlightStatus::internal(format!(
        "grpc-status-details-bin mismatch: grpc-status={}, grpc-message={}, grpc-status-details-bin={}",
        go_code(code),
        go_quote_bytes(&message),
        proto.prototext()
    ))
}

/// `google.rpc.Status`, decoded with protobuf rules (proto3 strings must be
/// UTF-8, unknown fields are skipped) as protobuf-go's `proto.Unmarshal`.
#[derive(Clone, PartialEq, prost::Message)]
struct RpcStatus {
    #[prost(int32, tag = "1")]
    code: i32,
    #[prost(string, tag = "2")]
    message: String,
    #[prost(message, repeated, tag = "3")]
    details: Vec<prost_types::Any>,
}
impl RpcStatus {
    /// protobuf-go's single-line prototext (`%+v` of the message) as the
    /// pinned Go binary prints it: zero-valued fields omitted, fields in
    /// declaration order, `name:value`, nested messages in `{}`, and fields
    /// separated by one space plus the binary-seeded `detrand` space.
    /// `Any` is shown unexpanded and unknown fields are not shown (see the
    /// notes for both limits).
    fn prototext(&self) -> String {
        let mut fields = Vec::new();
        if self.code != 0 {
            fields.push(format!("code:{}", self.code));
        }
        if !self.message.is_empty() {
            fields.push(format!(
                "message:{}",
                prototext_string(self.message.as_bytes())
            ));
        }
        for any in &self.details {
            let mut inner = Vec::new();
            if !any.type_url.is_empty() {
                inner.push(format!(
                    "type_url:{}",
                    prototext_string(any.type_url.as_bytes())
                ));
            }
            if !any.value.is_empty() {
                inner.push(format!("value:{}", prototext_string(&any.value)));
            }
            fields.push(format!("details:{{{}}}", inner.join(PROTOTEXT_SEPARATOR)));
        }
        fields.join(PROTOTEXT_SEPARATOR)
    }
}

/// protobuf-go `text.Encoder.prepareNext` single-line field separator: one
/// space, plus one more because `detrand.Bool()` is true for the pinned Go
/// binary (seeded from its own executable hash; measured as two spaces).
const PROTOTEXT_SEPARATOR: &str = "  ";

/// protobuf-go `internal/encoding/text` `appendString` without ASCII output:
/// `\"`, `\\`, `\n`, `\r`, `\t`, other C0 controls, DEL and invalid UTF-8
/// bytes as `\xNN`, C1 controls as `\u00NN`, everything else as is.
fn prototext_string(bytes: &[u8]) -> String {
    let mut out = String::from("\"");
    for chunk in bytes.utf8_chunks() {
        for c in chunk.valid().chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                c if c < ' ' || c == '\u{7f}' => out.push_str(&format!("\\x{:02x}", c as u32)),
                c if ('\u{80}'..='\u{9f}').contains(&c) => {
                    out.push_str(&format!("\\u{:04x}", c as u32))
                }
                c => out.push(c),
            }
        }
        for byte in chunk.invalid() {
            out.push_str(&format!("\\x{byte:02x}"));
        }
    }
    out.push('"');
    out
}

/// Go `decodeBinHeader` output for a value `go_base64_error` accepts:
/// newlines are skipped and non-zero trailing bits are allowed.
fn go_base64_decode(src: &[u8]) -> Option<Vec<u8>> {
    use base64::engine::{DecodePaddingMode, GeneralPurpose, GeneralPurposeConfig};
    if go_base64_error(src).is_some() {
        return None;
    }
    let config = GeneralPurposeConfig::new()
        .with_decode_allow_trailing_bits(true)
        .with_decode_padding_mode(DecodePaddingMode::Indifferent);
    let engine = GeneralPurpose::new(&base64::alphabet::STANDARD, config);
    let stripped: Vec<u8> = src
        .iter()
        .copied()
        .filter(|b| !matches!(b, b'\n' | b'\r'))
        .collect();
    engine.decode(stripped).ok()
}

/// grpc-go `operateHeaders` for trailers: the stream already speaks gRPC,
/// so only status parsing and metadata decoding can fail.
fn trailers_status(trailers: &http::HeaderMap) -> FlightStatus {
    let parsed = grpc_status(trailers).and_then(|code| {
        metadata_errors(trailers)?;
        Ok(code)
    });
    match parsed {
        Ok(code) => final_status(code, trailers),
        Err(status) => status,
    }
}

/// `strconv.ParseInt(v, 10, 32)` over each `grpc-status`; the last wins and
/// the first malformed one is Internal `transport: malformed grpc-status`.
fn grpc_status(headers: &http::HeaderMap) -> Result<Option<u32>, FlightStatus> {
    let mut code = None;
    for value in headers.get_all("grpc-status") {
        let raw = value.as_bytes();
        let parsed = go_parse_int32(raw).map_err(|reason| {
            FlightStatus::internal(format!(
                "transport: malformed grpc-status: strconv.ParseInt: parsing {}: {reason}",
                go_quote_bytes(raw)
            ))
        })?;
        code = Some(parsed as u32);
    }
    Ok(code)
}

/// Go `strconv.ParseInt(s, 10, 32)` result or its error reason.
fn go_parse_int32(raw: &[u8]) -> Result<i32, &'static str> {
    const SYNTAX: &str = "invalid syntax";
    const RANGE: &str = "value out of range";
    let (negative, digits) = match raw.split_first() {
        None => return Err(SYNTAX),
        Some((b'+', rest)) => (false, rest),
        Some((b'-', rest)) => (true, rest),
        Some(_) => (false, raw),
    };
    if digits.is_empty() {
        return Err(SYNTAX);
    }
    // ParseUint(.., 32) stops at the first bad digit or the first overflow.
    let mut value: u64 = 0;
    for byte in digits {
        if !byte.is_ascii_digit() {
            return Err(SYNTAX);
        }
        value = value * 10 + u64::from(byte - b'0');
        if value > u64::from(u32::MAX) {
            return Err(RANGE);
        }
    }
    let cutoff = 1u64 << 31;
    match negative {
        false if value >= cutoff => Err(RANGE),
        true if value > cutoff => Err(RANGE),
        false => Ok(value as i32),
        true => Ok((value as i64).wrapping_neg() as i32),
    }
}

/// grpc-go `decodeMetadataHeader` over every non-reserved field: a `-bin`
/// value must decode as base64 (padded when its length is a multiple of 4,
/// raw otherwise). The last failure is reported.
fn metadata_errors(headers: &http::HeaderMap) -> Result<(), FlightStatus> {
    let mut error = None;
    for (name, value) in headers {
        let name = name.as_str();
        if !name.ends_with("-bin") {
            continue;
        }
        if let Some(index) = go_base64_error(value.as_bytes()) {
            error = Some(format!(
                "transport: malformed {name}: illegal base64 data at input byte {index}"
            ));
        }
    }
    error.map_or(Ok(()), |message| Err(FlightStatus::internal(message)))
}

/// The input index Go's `encoding/base64` reports for `decodeBinHeader`'s
/// choice of StdEncoding or RawStdEncoding, or `None` if it decodes.
fn go_base64_error(src: &[u8]) -> Option<usize> {
    let padded = src.len().is_multiple_of(4);
    let alphabet = |b: u8| b.is_ascii_alphanumeric() || b == b'+' || b == b'/';
    let newline = |b: u8| b == b'\n' || b == b'\r';
    let skip_newlines = |mut si: usize| {
        while si < src.len() && newline(src[si]) {
            si += 1;
        }
        si
    };
    let mut si = 0;
    loop {
        // One `decodeQuantum`.
        let mut j = 0;
        while j < 4 {
            if si == src.len() {
                return match j {
                    0 => None,
                    1 => Some(si - j),
                    _ if padded => Some(si - j),
                    _ => None,
                };
            }
            let byte = src[si];
            si += 1;
            if alphabet(byte) {
                j += 1;
                continue;
            }
            if newline(byte) {
                continue;
            }
            if !padded || byte != b'=' {
                return Some(si - 1);
            }
            match j {
                0 | 1 => return Some(si - 1),
                2 => {
                    si = skip_newlines(si);
                    if si == src.len() {
                        return Some(src.len());
                    }
                    if src[si] != b'=' {
                        return Some(si - 1);
                    }
                    si += 1;
                }
                _ => {}
            }
            si = skip_newlines(si);
            return (si < src.len()).then_some(si);
        }
    }
}

/// The last `grpc-message`, percent-decoded as grpc-go's
/// `decodeGrpcMessage` does, keeping the raw bytes.
fn grpc_message(headers: &http::HeaderMap) -> Vec<u8> {
    let Some(value) = headers.get_all("grpc-message").iter().next_back() else {
        return Vec::new();
    };
    let raw = value.as_bytes();
    let mut decoded = Vec::with_capacity(raw.len());
    let mut index = 0;
    while index < raw.len() {
        let byte = raw[index];
        // Go needs two bytes after '%' (`i+2 < len`) and two hex digits.
        if byte == b'%'
            && index + 2 < raw.len()
            && let Some(parsed) = std::str::from_utf8(&raw[index + 1..index + 3])
                .ok()
                .filter(|hex| hex.bytes().all(|b| b.is_ascii_hexdigit()))
                .and_then(|hex| u8::from_str_radix(hex, 16).ok())
        {
            decoded.push(parsed);
            index += 3;
            continue;
        }
        decoded.push(byte);
        index += 1;
    }
    decoded
}

/// Go 1.25 `net/http.StatusText`.
fn go_status_text(code: u16) -> &'static str {
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

/// The connection tonic's channel currently uses. Reconnects replace it; a
/// response binds to the one that carried it.
#[derive(Clone, Default)]
struct Wires(Arc<std::sync::Mutex<Option<Arc<Wire>>>>);
impl Wires {
    fn replace(&self, wire: Arc<Wire>) {
        *self.0.lock().expect("wires lock") = Some(wire);
    }
    fn current(&self) -> Option<Arc<Wire>> {
        self.0.lock().expect("wires lock").clone()
    }
}

/// HTTP/2 frame facts observed on one connection's plaintext (post-TLS)
/// bytes. hyper turns any h2 error with reason NO_ERROR into a clean end of
/// body, erasing GOAWAY(NO_ERROR) and RST_STREAM(NO_ERROR); these frames are
/// the only remaining evidence of how a stream ended without trailers.
#[derive(Debug, Default)]
struct Wire(std::sync::Mutex<WireState>);
#[derive(Debug, Default)]
struct WireState {
    /// Highest stream id our client opened (HEADERS written).
    opened: u32,
    /// A GOAWAY arrived: grpc-go starts no new stream on this transport.
    draining: bool,
    /// The first terminating frame observed for the opened stream, as Go's
    /// reader would act on it first.
    terminal: Option<(u32, Terminal)>,
}
#[derive(Clone, Copy, Debug, PartialEq)]
enum Terminal {
    /// GOAWAY whose last stream id excludes the stream.
    Refused,
    /// RST_STREAM with this error code.
    Reset(u32),
    /// DATA with END_STREAM and no trailers.
    DataEnd,
    /// HEADERS with END_STREAM: the server's final status, which hyper
    /// delivers as trailers. Recorded so that a later frame for the stream
    /// cannot be mistaken for how it ended.
    Trailers,
}

/// HTTP/2 REFUSED_STREAM: the server did not process the stream.
const REFUSED_STREAM: u32 = 0x7;

/// grpc-go's status for a stream that a GOAWAY excluded.
/// grpc-go hands the reader `errStreamDrain`, and that is what `RecvMsg`
/// reports (measured against the pinned Go client before and after headers).
fn refused() -> FlightStatus {
    FlightStatus::unavailable("the connection is draining")
}

/// grpc-go `handleRSTStream`: `http2ErrConvTab` code and the frame's code name.
fn reset_status(code: u32) -> FlightStatus {
    FlightStatus::new(
        go_reset_code(code),
        format!(
            "stream terminated by RST_STREAM with error code: {}",
            go_http2_code(code)
        ),
    )
}

impl Wire {
    fn opened(&self) -> u32 {
        self.0.lock().expect("wire lock").opened
    }
    fn draining(&self) -> bool {
        self.0.lock().expect("wire lock").draining
    }
    fn wrote(&self, frame: &FrameInfo<'_>) {
        if frame.kind == FRAME_HEADERS {
            let mut state = self.0.lock().expect("wire lock");
            state.opened = state.opened.max(frame.stream);
        }
    }
    fn read(&self, frame: &FrameInfo<'_>) {
        let mut state = self.0.lock().expect("wire lock");
        let opened = state.opened;
        let terminal = match frame.kind {
            // grpc-go handleGoAway: streams above the last id are refused,
            // whatever the error code.
            FRAME_GOAWAY if frame.prefix.len() >= 4 => {
                state.draining = true;
                let last = u32::from_be_bytes(frame.prefix[..4].try_into().expect("4 bytes"))
                    & 0x7fff_ffff;
                if opened <= last {
                    return;
                }
                Terminal::Refused
            }
            // Only the stream in use matters; a late frame for an older
            // stream must not displace its evidence.
            _ if frame.stream == 0 || frame.stream != opened => return,
            FRAME_DATA if frame.flags & FLAG_END_STREAM != 0 => Terminal::DataEnd,
            FRAME_HEADERS if frame.flags & FLAG_END_STREAM != 0 => Terminal::Trailers,
            FRAME_RST_STREAM if frame.prefix.len() == 4 => Terminal::Reset(u32::from_be_bytes(
                frame.prefix.try_into().expect("4 bytes"),
            )),
            _ => return,
        };
        if state.terminal.is_none_or(|(stream, _)| stream != opened) {
            state.terminal = Some((opened, terminal));
        }
    }
    /// The first terminating frame observed for `stream`, if any.
    fn terminal(&self, stream: u32) -> Option<Terminal> {
        let state = self.0.lock().expect("wire lock");
        state
            .terminal
            .filter(|(id, _)| *id == stream)
            .map(|(_, terminal)| terminal)
    }
}

/// grpc-go `http2ErrConvTab`.
fn go_reset_code(code: u32) -> u32 {
    let code = match code {
        0x0..=0x2 | 0x4..=0x6 | 0x9 | 0xa | 0xd => Code::Internal,
        0x3 | 0xb => Code::ResourceExhausted,
        0x7 => Code::Unavailable,
        0x8 => Code::Cancelled,
        0xc => Code::PermissionDenied,
        _ => Code::Unknown,
    };
    code as u32
}

/// x/net/http2 `ErrCode.String()`.
fn go_http2_code(code: u32) -> String {
    let name = match code {
        0x0 => "NO_ERROR",
        0x1 => "PROTOCOL_ERROR",
        0x2 => "INTERNAL_ERROR",
        0x3 => "FLOW_CONTROL_ERROR",
        0x4 => "SETTINGS_TIMEOUT",
        0x5 => "STREAM_CLOSED",
        0x6 => "FRAME_SIZE_ERROR",
        0x7 => "REFUSED_STREAM",
        0x8 => "CANCEL",
        0x9 => "COMPRESSION_ERROR",
        0xa => "CONNECT_ERROR",
        0xb => "ENHANCE_YOUR_CALM",
        0xc => "INADEQUATE_SECURITY",
        0xd => "HTTP_1_1_REQUIRED",
        _ => return format!("unknown error code 0x{code:x}"),
    };
    name.into()
}

const FRAME_DATA: u8 = 0x0;
const FRAME_HEADERS: u8 = 0x1;
const FRAME_RST_STREAM: u8 = 0x3;
const FRAME_GOAWAY: u8 = 0x7;
const FLAG_END_STREAM: u8 = 0x1;
/// The client connection preface precedes the first written frame.
const CLIENT_PREFACE_LEN: usize = 24;

/// One completed frame: header fields plus the payload prefix the observer
/// needs (4 bytes of RST_STREAM, 8 of GOAWAY).
struct FrameInfo<'a> {
    kind: u8,
    flags: u8,
    stream: u32,
    prefix: &'a [u8],
}

/// Incremental HTTP/2 frame-boundary scanner. It copies only frame headers
/// and short payload prefixes and skips payloads, so it costs O(frames).
#[derive(Default)]
struct FrameScanner {
    skip: usize,
    header: [u8; 9],
    filled: usize,
    payload_left: usize,
    prefix: [u8; 8],
    prefix_len: usize,
    prefix_need: usize,
    frames: u64,
}
impl FrameScanner {
    fn writer() -> Self {
        Self {
            skip: CLIENT_PREFACE_LEN,
            ..Self::default()
        }
    }

    fn scan(&mut self, mut bytes: &[u8], on_frame: &mut dyn FnMut(&FrameInfo<'_>)) {
        while !bytes.is_empty() {
            if self.skip > 0 {
                let n = self.skip.min(bytes.len());
                self.skip -= n;
                bytes = &bytes[n..];
                continue;
            }
            if self.filled < self.header.len() {
                self.header[self.filled] = bytes[0];
                self.filled += 1;
                bytes = &bytes[1..];
                if self.filled == self.header.len() {
                    let length =
                        u32::from_be_bytes([0, self.header[0], self.header[1], self.header[2]])
                            as usize;
                    self.payload_left = length;
                    self.prefix_len = 0;
                    self.prefix_need = match self.header[3] {
                        FRAME_RST_STREAM => 4,
                        FRAME_GOAWAY => 8,
                        _ => 0,
                    }
                    .min(length);
                    if length == 0 {
                        self.complete(on_frame);
                    }
                }
                continue;
            }
            let n = self.payload_left.min(bytes.len());
            let copy = (self.prefix_need - self.prefix_len).min(n);
            self.prefix[self.prefix_len..self.prefix_len + copy].copy_from_slice(&bytes[..copy]);
            self.prefix_len += copy;
            self.payload_left -= n;
            bytes = &bytes[n..];
            if self.payload_left == 0 {
                self.complete(on_frame);
            }
        }
    }

    fn complete(&mut self, on_frame: &mut dyn FnMut(&FrameInfo<'_>)) {
        let header = self.header;
        on_frame(&FrameInfo {
            kind: header[3],
            flags: header[4],
            stream: u32::from_be_bytes([header[5], header[6], header[7], header[8]]) & 0x7fff_ffff,
            prefix: &self.prefix[..self.prefix_len],
        });
        self.filled = 0;
        self.frames += 1;
    }
}

/// Go url.Parse scheme and url.Hostname() without the url crate's
/// normalization (case, IDNA, IPv4 forms and default-port removal).
fn go_url_scheme_host(raw: &str) -> Option<(&str, &str)> {
    let (scheme, rest) = raw.split_once(':')?;
    if scheme.is_empty()
        || !scheme.as_bytes()[0].is_ascii_alphabetic()
        || !scheme
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'+' | b'-' | b'.'))
    {
        return None;
    }
    let Some(rest) = rest.strip_prefix("//") else {
        return Some((scheme, ""));
    };
    let authority = &rest[..rest.find(['/', '?', '#']).unwrap_or(rest.len())];
    Some((scheme, hostname(authority)))
}

/// url.URL.Hostname for an authority that may carry userinfo.
fn hostname(authority: &str) -> &str {
    let host = authority.rsplit('@').next().unwrap_or_default();
    if let Some(rest) = host.strip_prefix('[') {
        return rest.split(']').next().unwrap_or_default();
    }
    match host.rsplit_once(':') {
        Some((name, port)) if port.bytes().all(|b| b.is_ascii_digit()) => name,
        _ => host,
    }
}

fn enforce_alpn() -> bool {
    std::env::var("GRPC_ENFORCE_ALPN_ENABLED").is_ok_and(|v| v.eq_ignore_ascii_case("true"))
}

/// grpc-go `connectionErrorf(...).Error()`.
fn connection_error(desc: &str) -> String {
    connection_error_bytes(desc.as_bytes())
}

/// `connection error: desc = %q` over a desc that need not be UTF-8.
fn connection_error_bytes(desc: &[u8]) -> String {
    format!("connection error: desc = {}", go_quote_bytes(desc))
}

/// Go `strconv.Quote` over arbitrary bytes: invalid UTF-8 bytes become
/// `\xNN`, everything else as `parser::quote`.
fn go_quote_bytes(bytes: &[u8]) -> String {
    let mut out = String::from("\"");
    for chunk in bytes.utf8_chunks() {
        let valid = quote(chunk.valid());
        out.push_str(&valid[1..valid.len() - 1]);
        for byte in chunk.invalid() {
            out.push_str(&format!("\\x{byte:02x}"));
        }
    }
    out.push('"');
    out
}

/// Go `httpproxy.FromEnvironment()` for grpc-go's `mapAddress`, which always
/// asks about an https URL, so HTTPS_PROXY applies even to plaintext Flight.
#[derive(Clone, Debug, Default, PartialEq)]
struct ProxyEnvironment {
    https_proxy: String,
    no_proxy: String,
}
impl ProxyEnvironment {
    fn from_env() -> Self {
        let any = |upper: &str, lower: &str| {
            std::env::var(upper)
                .ok()
                .filter(|v| !v.is_empty())
                .or_else(|| std::env::var(lower).ok())
                .unwrap_or_default()
        };
        Self {
            https_proxy: any("HTTPS_PROXY", "https_proxy"),
            no_proxy: any("NO_PROXY", "no_proxy"),
        }
    }

    fn route(&self, address: &str) -> Option<ProxyRoute> {
        if self.https_proxy.is_empty() {
            return None;
        }
        let (host, port) = split_host_port(address).ok()?;
        // Go's IsLoopback also covers IPv4-mapped IPv6 (::ffff:127.0.0.1).
        if host == "localhost"
            || host
                .parse::<IpAddr>()
                .is_ok_and(|ip| ip.to_canonical().is_loopback())
        {
            return None;
        }
        let port = port.parse::<u16>().ok()?;
        if crate::transport::no_proxy_matches(&self.no_proxy, host, port) {
            return None;
        }
        Some(ProxyRoute::parse(&self.https_proxy))
    }
}

/// grpc-go dials `proxyURL.Host` verbatim and always speaks plaintext HTTP
/// CONNECT, whatever the proxy URL scheme.
#[derive(Clone, Debug, PartialEq)]
enum ProxyRoute {
    Connect {
        host: String,
        authorization: Option<String>,
    },
    Invalid(String),
}
impl ProxyRoute {
    /// httpproxy `parseProxy`: retry with "http://" when the scheme or host
    /// is missing.
    fn parse(raw: &str) -> Self {
        let parsed = proxy_authority(raw)
            .filter(|(_, host)| !host.is_empty())
            .or_else(|| proxy_authority(&format!("http://{raw}")));
        match parsed {
            Some((userinfo, host)) => Self::Connect {
                host,
                authorization: userinfo.map(|userinfo| {
                    let (user, password) = userinfo
                        .split_once(':')
                        .map_or((userinfo.as_str(), ""), |(u, p)| (u, p));
                    let credentials = [
                        percent_decode(user),
                        b":".to_vec(),
                        percent_decode(password),
                    ]
                    .concat();
                    format!("Basic {}", STANDARD.encode(credentials))
                }),
            },
            None => Self::Invalid(format!("invalid proxy address {}", quote(raw))),
        }
    }
}

/// Raw userinfo and host[:port] from `scheme://authority`.
fn proxy_authority(raw: &str) -> Option<(Option<String>, String)> {
    let (scheme, rest) = raw.split_once("://")?;
    if scheme.is_empty() || !scheme.as_bytes()[0].is_ascii_alphabetic() {
        return None;
    }
    let authority = &rest[..rest.find(['/', '?', '#']).unwrap_or(rest.len())];
    Some(match authority.rsplit_once('@') {
        Some((userinfo, host)) => (Some(userinfo.to_owned()), host.to_owned()),
        None => (None, authority.to_owned()),
    })
}

fn percent_decode(raw: &str) -> Vec<u8> {
    let bytes = raw.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%'
            && let Some(byte) = raw
                .get(index + 1..index + 3)
                .and_then(|hex| u8::from_str_radix(hex, 16).ok())
        {
            decoded.push(byte);
            index += 3;
            continue;
        }
        decoded.push(bytes[index]);
        index += 1;
    }
    decoded
}

/// Go net.SplitHostPort messages for the dial target.
fn split_host_port(address: &str) -> Result<(&str, &str), String> {
    let missing = || format!("address {address}: missing port in address");
    if let Some(rest) = address.strip_prefix('[') {
        let (host, after) = rest
            .split_once(']')
            .ok_or_else(|| format!("address {address}: missing ']' in address"))?;
        let port = after.strip_prefix(':').ok_or_else(missing)?;
        return Ok((host, port));
    }
    let (host, port) = address.rsplit_once(':').ok_or_else(missing)?;
    if host.contains(':') {
        return Err(format!("address {address}: too many colons in address"));
    }
    Ok((host, port))
}

/// What tonic's lazy channel dials: the Flight address, possibly tunneled,
/// then TLS when the profile selects it.
#[derive(Debug)]
struct DialPlan {
    address: String,
    proxy: Option<ProxyRoute>,
    tls: Option<TlsPlan>,
    connect_timeout: Duration,
}

/// `credentials.NewTLS(&tls.Config{MinVersion: TLS12, ServerName: host})`.
#[derive(Debug)]
struct TlsPlan {
    host: String,
    enforce_alpn: bool,
}

/// TCP, CONNECT and TLS (inside any tunnel, as in grpc-go), returning an
/// observed stream whose reads are bounded until the server's HTTP/2
/// preface. Each connection registers its frame observer in `wires`.
#[derive(Clone)]
struct Dialer {
    plan: Arc<DialPlan>,
    wires: Wires,
}

/// Display is grpc-go's `connectionErrorf` text for the given desc bytes;
/// tonic surfaces it as the Unavailable status message.
#[derive(Debug)]
struct DialError(Vec<u8>);
impl fmt::Display for DialError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&connection_error_bytes(&self.0))
    }
}
impl std::error::Error for DialError {}

impl tower_service::Service<Uri> for Dialer {
    type Response = TokioIo<Observed<Socket>>;
    type Error = DialError;
    type Future = Pin<Box<dyn Future<Output = Result<Self::Response, DialError>> + Send>>;

    fn poll_ready(&mut self, _: &mut Context<'_>) -> Poll<Result<(), DialError>> {
        Poll::Ready(Ok(()))
    }

    fn call(&mut self, _: Uri) -> Self::Future {
        let plan = self.plan.clone();
        let wires = self.wires.clone();
        Box::pin(async move {
            let stream = plan.connect().await.map_err(DialError)?;
            wires.replace(stream.wire.clone());
            Ok(TokioIo::new(stream))
        })
    }
}

impl DialPlan {
    /// One grpc-go `newHTTP2Client`: a single connect deadline bounds
    /// dialing, the TLS handshake and the server preface. grpc-go applies
    /// none to the proxy CONNECT exchange.
    async fn connect(&self) -> Result<Observed<Socket>, Vec<u8>> {
        let deadline = Instant::now() + self.connect_timeout;
        let tcp = self
            .dial(deadline)
            .await
            .map_err(|detail| [b"transport: Error while dialing: ".as_slice(), &detail].concat())?;
        let addresses = connection_addresses(&tcp);
        let stream = match &self.tls {
            None => Socket::Plain(tcp),
            Some(tls) => Socket::Tls(Box::new(
                tls.handshake(tcp, deadline)
                    .await
                    .map_err(String::into_bytes)?,
            )),
        };
        Ok(Observed::new(stream, deadline, addresses))
    }

    async fn dial(&self, deadline: Instant) -> Result<TcpStream, Vec<u8>> {
        match &self.proxy {
            None => dial_tcp(&self.address, deadline)
                .await
                .map_err(String::into_bytes),
            Some(ProxyRoute::Invalid(message)) => Err(message.clone().into_bytes()),
            Some(ProxyRoute::Connect {
                host,
                authorization,
            }) => {
                let mut stream = dial_tcp(host, deadline).await.map_err(String::into_bytes)?;
                connect_handshake(&mut stream, &self.address, authorization.as_deref()).await?;
                Ok(stream)
            }
        }
    }
}

impl TlsPlan {
    /// grpc-go `tlsCreds.ClientHandshake` under the connect deadline, with
    /// its ALPN gate. Errors are complete connection-error descs.
    async fn handshake(
        &self,
        tcp: TcpStream,
        deadline: Instant,
    ) -> Result<TlsStream<TcpStream>, String> {
        let failed = |detail: &dyn fmt::Display| format!("{HANDSHAKE_ERROR}{detail}");
        let config = flight_tls_config().map_err(|e| failed(&e))?;
        let name = ServerName::try_from(self.host.clone()).map_err(|e| failed(&e))?;
        let stream = timeout_at(deadline, TlsConnector::from(config).connect(name, tcp))
            .await
            .map_err(|_| failed(&"context deadline exceeded"))?
            .map_err(|err| {
                // crypto/tls rejects a first record that is not a handshake
                // (a plaintext peer) with this text; rustls reports the same
                // condition as an invalid content type.
                let first_record = err
                    .get_ref()
                    .and_then(|inner| inner.downcast_ref::<rustls::Error>())
                    .is_some_and(|inner| {
                        matches!(
                            inner,
                            rustls::Error::InvalidMessage(
                                rustls::InvalidMessage::InvalidContentType
                            )
                        )
                    });
                if first_record {
                    failed(&"tls: first record does not look like a TLS handshake")
                } else {
                    failed(&err)
                }
            })?;
        // grpc-go 1.65 accepts a peer that selects no ALPN unless enforced.
        if self.enforce_alpn && stream.get_ref().1.alpn_protocol().is_none() {
            return Err(failed(
                &"credentials: cannot check peer: missing selected ALPN property",
            ));
        }
        Ok(stream)
    }
}

/// Go's system root pool (shared loader with the HTTP client), loaded once
/// per process at first use like `x509.SystemCertPool`, offering only h2.
/// Go's `tls.Config` has no session cache, so every connection performs a
/// full handshake and certificate verification.
fn flight_tls_config() -> Result<Arc<rustls::ClientConfig>, String> {
    static CONFIG: OnceLock<Result<Arc<rustls::ClientConfig>, String>> = OnceLock::new();
    CONFIG
        .get_or_init(|| {
            let mut config = crate::transport::load_tls_config().map_err(|e| e.to_string())?;
            config.alpn_protocols = vec![b"h2".to_vec()];
            config.resumption = rustls::client::Resumption::disabled();
            Ok(Arc::new(config))
        })
        .clone()
}

/// Go's `L->R` socket pair text for net.OpError messages.
fn connection_addresses(stream: &TcpStream) -> String {
    match (stream.local_addr(), stream.peer_addr()) {
        (Ok(local), Ok(peer)) => format!("{local}->{peer}"),
        _ => String::new(),
    }
}

/// Plain or TLS transport under the HTTP/2 connection.
enum Socket {
    Plain(TcpStream),
    Tls(Box<TlsStream<TcpStream>>),
}
impl AsyncRead for Socket {
    fn poll_read(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        match self.get_mut() {
            Self::Plain(stream) => Pin::new(stream).poll_read(cx, buf),
            Self::Tls(stream) => Pin::new(stream.as_mut()).poll_read(cx, buf),
        }
    }
}
impl AsyncWrite for Socket {
    fn poll_write(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        match self.get_mut() {
            Self::Plain(stream) => Pin::new(stream).poll_write(cx, buf),
            Self::Tls(stream) => Pin::new(stream.as_mut()).poll_write(cx, buf),
        }
    }
    fn poll_write_vectored(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bufs: &[io::IoSlice<'_>],
    ) -> Poll<io::Result<usize>> {
        match self.get_mut() {
            Self::Plain(stream) => Pin::new(stream).poll_write_vectored(cx, bufs),
            Self::Tls(stream) => Pin::new(stream.as_mut()).poll_write_vectored(cx, bufs),
        }
    }
    fn is_write_vectored(&self) -> bool {
        match self {
            Self::Plain(stream) => stream.is_write_vectored(),
            Self::Tls(stream) => stream.is_write_vectored(),
        }
    }
    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        match self.get_mut() {
            Self::Plain(stream) => Pin::new(stream).poll_flush(cx),
            Self::Tls(stream) => Pin::new(stream.as_mut()).poll_flush(cx),
        }
    }
    fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        match self.get_mut() {
            Self::Plain(stream) => Pin::new(stream).poll_shutdown(cx),
            Self::Tls(stream) => Pin::new(stream.as_mut()).poll_shutdown(cx),
        }
    }
}

/// The HTTP/2 byte stream as hyper sees it, observed in both directions.
///
/// - Reads: grpc-go `readServerPreface` under the connect deadline. Until the
///   server's first complete frame has been read, an expired deadline, EOF or
///   read error fails the connection with Go's preface text. After that the
///   reads are unbounded.
/// - Frames in both directions feed this connection's `Wire`.
/// - Empty DATA frames that grpc-go ignores are removed before h2 counts them
///   (`EmptyDataFilter`).
struct Observed<S> {
    inner: S,
    wire: Arc<Wire>,
    reads: FrameScanner,
    writes: FrameScanner,
    watch: Option<PrefaceWatch>,
    filter: EmptyDataFilter,
}

/// Reads per `poll_read` that may yield only discarded frames before the
/// task yields (woken at once), so even an always-ready source cannot hold
/// the executor.
const FILTER_READ_BUDGET: usize = 32;
const FLAG_PADDED: u8 = 0x8;
const FLAG_END_HEADERS: u8 = 0x4;
const FRAME_PUSH_PROMISE: u8 = 0x5;
const FRAME_CONTINUATION: u8 = 0x9;

/// Drops unpadded, zero-length DATA frames without END_STREAM on a nonzero
/// stream once the server's first frame has passed, outside an open header
/// block. grpc-go's `handleData`
/// does nothing for such a frame; h2 0.4.20 counts them per connection
/// lifetime and sends GOAWAY(ENHANCE_YOUR_CALM) past 100. They carry no data
/// and no flow-control weight. The first frame, padded or END_STREAM frames,
/// stream 0 and everything else pass unchanged, so h2 still enforces the
/// SETTINGS-first rule. While a HEADERS or PUSH_PROMISE block awaits its
/// CONTINUATION, nothing is dropped, so h2 (like x/net's `checkFrameOrder`)
/// still rejects a DATA frame interleaved there. Padded empty DATA frames
/// carry flow-control weight and stay with h2 (ledger CL-3).
///
/// A frame header is withheld until all 9 bytes have arrived, so a header
/// split across reads can still be dropped; payloads pass through in place.
#[derive(Default)]
struct EmptyDataFilter {
    header: [u8; 9],
    filled: usize,
    payload_left: usize,
    frames: u64,
    /// A kept HEADERS or PUSH_PROMISE without END_HEADERS awaits its final
    /// CONTINUATION.
    header_block_open: bool,
    /// Header bytes owed to the reader (a straddling header that is kept,
    /// or withheld bytes released at EOF).
    owed: [u8; 9],
    owed_start: usize,
    owed_end: usize,
}
impl EmptyDataFilter {
    /// A header just completed: whether it is dropped. Otherwise its payload
    /// follows.
    fn header_complete(&mut self) -> bool {
        let header = self.header;
        self.filled = 0;
        let length = u32::from_be_bytes([0, header[0], header[1], header[2]]) as usize;
        let stream = u32::from_be_bytes([header[5], header[6], header[7], header[8]]) & 0x7fff_ffff;
        let (kind, flags) = (header[3], header[4]);
        let dropped = self.frames > 0
            && !self.header_block_open
            && kind == FRAME_DATA
            && length == 0
            && flags & (FLAG_END_STREAM | FLAG_PADDED) == 0
            && stream != 0;
        if dropped {
            return true;
        }
        match kind {
            FRAME_HEADERS | FRAME_PUSH_PROMISE => {
                self.header_block_open = flags & FLAG_END_HEADERS == 0;
            }
            FRAME_CONTINUATION if flags & FLAG_END_HEADERS != 0 => {
                self.header_block_open = false;
            }
            _ => {}
        }
        self.payload_left = length;
        if length == 0 {
            self.frames += 1;
        }
        false
    }

    /// Filters a freshly read region in place and returns its kept length.
    /// Must start outside a header (`filled == 0`), so a kept header is
    /// always written back no later than where it was read.
    fn filter(&mut self, region: &mut [u8]) -> usize {
        debug_assert_eq!(self.filled, 0);
        let (mut read, mut kept) = (0, 0);
        while read < region.len() {
            if self.payload_left > 0 {
                let take = self.payload_left.min(region.len() - read);
                if kept != read {
                    region.copy_within(read..read + take, kept);
                }
                read += take;
                kept += take;
                self.payload_left -= take;
                if self.payload_left == 0 {
                    self.frames += 1;
                }
                continue;
            }
            self.header[self.filled] = region[read];
            self.filled += 1;
            read += 1;
            if self.filled == self.header.len() && !self.header_complete() {
                region[kept..kept + 9].copy_from_slice(&self.header);
                kept += 9;
            }
        }
        kept
    }

    /// Withheld bytes become owed (at EOF, so h2 sees the truncation).
    fn release_withheld(&mut self) {
        self.owed[..self.filled].copy_from_slice(&self.header[..self.filled]);
        (self.owed_start, self.owed_end) = (0, self.filled);
        self.filled = 0;
    }
}
struct PrefaceWatch {
    deadline: Pin<Box<Sleep>>,
    addresses: String,
    seen: usize,
    /// The first write error absorbed before the preface (see
    /// `Observed::write_failed`).
    absorbed: Option<io::Error>,
}
impl<S> Observed<S> {
    fn new(inner: S, deadline: Instant, addresses: String) -> Self {
        Self {
            inner,
            wire: Arc::default(),
            reads: FrameScanner::default(),
            writes: FrameScanner::writer(),
            watch: Some(PrefaceWatch {
                deadline: Box::pin(sleep_until(deadline)),
                addresses,
                seen: 0,
                absorbed: None,
            }),
            filter: EmptyDataFilter::default(),
        }
    }

    fn wrote(&mut self, bytes: &[u8]) {
        let wire = &self.wire;
        self.writes.scan(bytes, &mut |frame| wire.wrote(frame));
    }
}
impl PrefaceWatch {
    fn error(&self, kind: io::ErrorKind, detail: &str) -> io::Error {
        io::Error::new(kind, format!("{PREFACE_ERROR}{detail}"))
    }

    fn read_error(&self, detail: &str) -> String {
        format!("read tcp {}: {detail}", self.addresses)
    }

    /// Go's framer reports EOF before any frame byte and unexpected EOF
    /// inside one; crypto/tls reports a close without close_notify the same
    /// way at a record boundary.
    fn eof(&self) -> io::Error {
        let eof = if self.seen == 0 {
            "EOF"
        } else {
            "unexpected EOF"
        };
        self.error(io::ErrorKind::UnexpectedEof, eof)
    }

    /// The read side reached EOF. If an absorbed write already consumed a
    /// connection reset (SO_ERROR is reported once), grpc-go's reader, whose
    /// writes had landed in the socket buffer, would have read that reset:
    /// report it. Any other absorbed error leaves Go's EOF text.
    fn closed(&self) -> io::Error {
        match &self.absorbed {
            Some(err) if err.kind() == io::ErrorKind::ConnectionReset => {
                let detail = self.read_error(&format!("read: {}", os_message(err)));
                self.error(io::ErrorKind::ConnectionReset, &detail)
            }
            _ => self.eof(),
        }
    }
}
impl<S: AsyncRead + Unpin> Observed<S> {
    /// One read from the socket under the preface watchdog's rules.
    fn read_inner(&mut self, cx: &mut Context<'_>, buf: &mut ReadBuf<'_>) -> Poll<io::Result<()>> {
        let before = buf.filled().len();
        let result = Pin::new(&mut self.inner).poll_read(cx, buf);
        if let Some(watch) = &mut self.watch {
            match &result {
                Poll::Pending => {
                    ready!(watch.deadline.as_mut().poll(cx));
                    // grpc-go closes the conn when connectCtx expires.
                    let detail = watch.read_error("use of closed network connection");
                    return Poll::Ready(Err(watch.error(io::ErrorKind::TimedOut, &detail)));
                }
                Poll::Ready(Err(err)) if err.kind() == io::ErrorKind::UnexpectedEof => {
                    return Poll::Ready(Err(watch.closed()));
                }
                Poll::Ready(Err(err)) => {
                    let detail = watch.read_error(&format!("read: {}", os_message(err)));
                    return Poll::Ready(Err(watch.error(err.kind(), &detail)));
                }
                Poll::Ready(Ok(())) if buf.filled().len() == before && buf.remaining() > 0 => {
                    return Poll::Ready(Err(watch.closed()));
                }
                Poll::Ready(Ok(())) => watch.seen += buf.filled().len() - before,
            }
        }
        result
    }

    /// Bytes handed to h2: scanned for the `Wire`; the first complete frame
    /// ends the preface watch.
    fn delivered(&mut self, bytes: &[u8]) {
        let wire = &self.wire;
        self.reads.scan(bytes, &mut |frame| wire.read(frame));
        if self.reads.frames > 0 {
            self.watch = None;
        }
    }

    /// Owed header bytes, as much as fits.
    fn deliver_owed(&mut self, buf: &mut ReadBuf<'_>) {
        let filter = &mut self.filter;
        let take = (filter.owed_end - filter.owed_start).min(buf.remaining());
        let owed = filter.owed;
        let bytes = &owed[filter.owed_start..filter.owed_start + take];
        filter.owed_start += take;
        buf.put_slice(bytes);
        self.delivered(bytes);
    }
}
impl<S: AsyncRead + Unpin> AsyncRead for Observed<S> {
    fn poll_read(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        if buf.remaining() == 0 {
            return Poll::Ready(Ok(()));
        }
        if this.filter.owed_start < this.filter.owed_end {
            this.deliver_owed(buf);
            return Poll::Ready(Ok(()));
        }
        for _ in 0..FILTER_READ_BUDGET {
            if this.filter.filled > 0 {
                // Complete a header split across reads with small reads.
                let mut small = [0; 9];
                let want = 9 - this.filter.filled;
                let mut part = ReadBuf::new(&mut small[..want]);
                ready!(this.read_inner(cx, &mut part))?;
                let read = part.filled().len();
                if read == 0 {
                    // EOF inside a header: h2 must see the truncated frame.
                    this.filter.release_withheld();
                    this.deliver_owed(buf);
                    return Poll::Ready(Ok(()));
                }
                let filter = &mut this.filter;
                filter.header[filter.filled..filter.filled + read].copy_from_slice(&small[..read]);
                filter.filled += read;
                if filter.filled == 9 && !filter.header_complete() {
                    filter.owed = filter.header;
                    (filter.owed_start, filter.owed_end) = (0, 9);
                    this.deliver_owed(buf);
                    return Poll::Ready(Ok(()));
                }
                continue;
            }
            let before = buf.filled().len();
            ready!(this.read_inner(cx, buf))?;
            let after = buf.filled().len();
            if after == before {
                return Poll::Ready(Ok(()));
            }
            let kept = this.filter.filter(&mut buf.filled_mut()[before..after]);
            buf.set_filled(before + kept);
            if kept > 0 {
                let wire = &this.wire;
                this.reads
                    .scan(&buf.filled()[before..], &mut |frame| wire.read(frame));
                if this.reads.frames > 0 {
                    this.watch = None;
                }
                return Poll::Ready(Ok(()));
            }
        }
        // Only discarded (or withheld) bytes this time: yield, but stay ready.
        cx.waker().wake_by_ref();
        Poll::Pending
    }
}
impl<S: AsyncWrite + Unpin> AsyncWrite for Observed<S> {
    fn poll_write(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        let this = self.get_mut();
        if this.absorbing() {
            return Poll::Ready(Ok(buf.len()));
        }
        match ready!(Pin::new(&mut this.inner).poll_write(cx, buf)) {
            Ok(written) => {
                this.wrote(&buf[..written]);
                Poll::Ready(Ok(written))
            }
            Err(err) => this.write_failed(err, buf.len()),
        }
    }
    fn poll_write_vectored(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bufs: &[io::IoSlice<'_>],
    ) -> Poll<io::Result<usize>> {
        let this = self.get_mut();
        if this.absorbing() {
            return Poll::Ready(Ok(bufs.iter().map(|b| b.len()).sum()));
        }
        let written = match ready!(Pin::new(&mut this.inner).poll_write_vectored(cx, bufs)) {
            Ok(written) => written,
            Err(err) => return this.write_failed(err, bufs.iter().map(|b| b.len()).sum()),
        };
        let mut left = written;
        for slice in bufs {
            if left == 0 {
                break;
            }
            let take = left.min(slice.len());
            this.wrote(&slice[..take]);
            left -= take;
        }
        Poll::Ready(Ok(written))
    }
    fn is_write_vectored(&self) -> bool {
        self.inner.is_write_vectored()
    }
    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        if this.absorbing() {
            return Poll::Ready(Ok(()));
        }
        match ready!(Pin::new(&mut this.inner).poll_flush(cx)) {
            Err(err) => this.write_failed(err, ()),
            ok => Poll::Ready(ok),
        }
    }
    fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.get_mut().inner).poll_shutdown(cx)
    }
}

impl<S> Observed<S> {
    /// grpc-go's `newHTTP2Client` takes the connection's fate from its
    /// reader until the server preface arrives: its preface and SETTINGS
    /// writes land in the socket buffer, and a peer reset surfaces as
    /// `error reading server preface: read tcp …`. hyper may instead hit the
    /// reset on a later write. Until the preface, a write error is absorbed so
    /// the read side (bounded by the connect deadline) reports the failure.
    /// After the preface, write errors propagate unchanged. While absorbing,
    /// later writes are not attempted on the socket again, so nothing is
    /// queued or retried: the connection only waits for its read side, which
    /// the connect deadline bounds.
    fn write_failed<T>(&mut self, err: io::Error, absorbed: T) -> Poll<io::Result<T>> {
        match &mut self.watch {
            // grpc-go writes the 24-byte client preface synchronously first
            // and reports its failure itself.
            Some(watch) if self.writes.skip > 0 => {
                let detail = format!(
                    "{WRITE_PREFACE_ERROR}write tcp {}: write: {}",
                    watch.addresses,
                    os_message(&err)
                );
                Poll::Ready(Err(io::Error::new(err.kind(), detail)))
            }
            Some(watch) => {
                watch.absorbed.get_or_insert(err);
                Poll::Ready(Ok(absorbed))
            }
            None => Poll::Ready(Err(err)),
        }
    }

    /// A write error is being absorbed: skip the socket.
    fn absorbing(&self) -> bool {
        self.watch
            .as_ref()
            .is_some_and(|watch| watch.absorbed.is_some())
    }
}

/// Go `net.Dialer.DialContext("tcp", address)` as grpc-go configures it:
/// SO_KEEPALIVE with OS defaults, TCP_NODELAY, and Happy Eyeballs.
async fn dial_tcp(address: &str, deadline: Instant) -> Result<TcpStream, String> {
    let (host, port) = split_host_port(address).map_err(|e| format!("dial tcp: {e}"))?;
    let port: u16 = port
        .parse()
        .map_err(|_| format!("dial tcp: address {port}: invalid port"))?;
    let addresses: Vec<SocketAddr> = match host.parse::<IpAddr>() {
        Ok(ip) => vec![SocketAddr::new(ip, port)],
        Err(_) => timeout_at(deadline, resolve(host, port))
            .await
            .map_err(|_| format!("dial tcp: lookup {host}: i/o timeout"))?
            .map_err(|err| format!("dial tcp: lookup {host}: {}", os_message(&err)))?,
    };
    if addresses.is_empty() {
        return Err(format!("dial tcp: lookup {host}: no such host"));
    }
    // Go `partition`: the first address's family is primary.
    let primary_v4 = addresses[0].is_ipv4();
    let (primaries, fallbacks): (Vec<SocketAddr>, Vec<SocketAddr>) = addresses
        .into_iter()
        .partition(|address| address.is_ipv4() == primary_v4);
    dial_parallel(&primaries, &fallbacks, deadline, &connect).await
}

/// `getaddrinfo` on a detached thread rather than tokio's blocking pool, so
/// a resolver still running after the connect deadline cannot hold up the
/// runtime's shutdown (and the process exit) when the session ends.
async fn resolve(host: &str, port: u16) -> io::Result<Vec<SocketAddr>> {
    let (sender, receiver) = tokio::sync::oneshot::channel();
    let target = (host.to_owned(), port);
    std::thread::Builder::new()
        .name("pb-flight-resolve".into())
        .spawn(move || {
            let resolved = std::net::ToSocketAddrs::to_socket_addrs(&target).map(Iterator::collect);
            let _ = sender.send(resolved);
        })?;
    receiver
        .await
        .unwrap_or_else(|_| Err(io::Error::other("resolver thread stopped")))
}

/// Go `dialParallel`: the fallback family starts after 300 ms, or at once if
/// the primaries fail first. The first success wins; if both fail, the
/// primary error is reported.
async fn dial_parallel<C, F, S>(
    primaries: &[SocketAddr],
    fallbacks: &[SocketAddr],
    deadline: Instant,
    connect: &C,
) -> Result<S, String>
where
    C: Fn(SocketAddr) -> F,
    F: Future<Output = io::Result<S>>,
{
    use futures_util::future::{Either, select};
    if fallbacks.is_empty() {
        return dial_serial(primaries, deadline, connect).await;
    }
    let primary = std::pin::pin!(dial_serial(primaries, deadline, connect));
    let delay = std::pin::pin!(tokio::time::sleep(FALLBACK_DELAY));
    let primary = match select(primary, delay).await {
        Either::Left((Ok(stream), _)) => return Ok(stream),
        Either::Left((Err(primary_error), _)) => {
            return dial_serial(fallbacks, deadline, connect)
                .await
                .map_err(|_| primary_error);
        }
        Either::Right(((), primary)) => primary,
    };
    let fallback = std::pin::pin!(dial_serial(fallbacks, deadline, connect));
    match select(primary, fallback).await {
        Either::Left((Ok(stream), _)) | Either::Right((Ok(stream), _)) => Ok(stream),
        Either::Left((Err(primary_error), fallback)) => fallback.await.map_err(|_| primary_error),
        Either::Right((Err(_), primary)) => primary.await,
    }
}

/// Go `dialSerial`: each address gets its `partialDeadline` share; the first
/// error is the one reported.
async fn dial_serial<C, F, S>(
    addresses: &[SocketAddr],
    deadline: Instant,
    connect: &C,
) -> Result<S, String>
where
    C: Fn(SocketAddr) -> F,
    F: Future<Output = io::Result<S>>,
{
    let mut first = None;
    for (index, &address) in addresses.iter().enumerate() {
        let timeout = || format!("dial tcp {address}: i/o timeout");
        let now = Instant::now();
        // An expired context reports this address, not the first error.
        if now >= deadline {
            return Err(timeout());
        }
        let Some(until) = partial_deadline(now, deadline, addresses.len() - index) else {
            first.get_or_insert_with(timeout);
            break;
        };
        let error = match timeout_at(until, connect(address)).await {
            Ok(Ok(stream)) => return Ok(stream),
            Ok(Err(err)) => format!("dial tcp {address}: connect: {}", os_message(&err)),
            Err(_) => timeout(),
        };
        first.get_or_insert(error);
    }
    Err(first.expect("at least one address"))
}

/// Go `partialDeadline`, with a 2 s floor stolen from later addresses.
fn partial_deadline(now: Instant, deadline: Instant, remaining: usize) -> Option<Instant> {
    let left = deadline
        .checked_duration_since(now)
        .filter(|d| !d.is_zero())?;
    let mut share = left / remaining as u32;
    if share < MIN_ADDRESS_TIMEOUT {
        share = left.min(MIN_ADDRESS_TIMEOUT);
    }
    Some(now + share)
}

async fn connect(address: SocketAddr) -> io::Result<TcpStream> {
    let socket = if address.is_ipv4() {
        TcpSocket::new_v4()?
    } else {
        TcpSocket::new_v6()?
    };
    socket.set_keepalive(true)?;
    let stream = socket.connect(address).await?;
    stream.set_nodelay(true)?;
    Ok(stream)
}

/// Go syscall error text: "Connection refused (os error 111)" -> "connection refused".
fn os_message(error: &io::Error) -> String {
    let text = error.to_string();
    let mut text = text
        .split(" (os error ")
        .next()
        .unwrap_or_default()
        .to_owned();
    if let Some(first) = text.get_mut(..1) {
        first.make_ascii_lowercase();
    }
    text
}

/// grpc-go `doHTTPConnectHandshake` with Go `http.ReadResponse`:
/// Request.Write header order, the status line and each header line
/// validated as soon as it is read, only status 200 accepted, a
/// Content-Length body consumed (drained on success, dumped on rejection),
/// and nothing read beyond it. Messages keep the proxy's raw bytes.
async fn connect_handshake(
    stream: &mut TcpStream,
    address: &str,
    authorization: Option<&str>,
) -> Result<(), Vec<u8>> {
    let addresses = connection_addresses(stream);
    let request = connect_request(address, authorization);
    stream.write_all(request.as_bytes()).await.map_err(|err| {
        format!(
            "failed to write the HTTP request: failed to write the HTTP request: write tcp {addresses}: write: {}",
            os_message(&err)
        )
        .into_bytes()
    })?;
    let reading =
        |detail: Vec<u8>| [b"reading server HTTP response: ".as_slice(), &detail].concat();
    let mut head = Vec::new();
    let line = read_line(stream, &mut head, &addresses)
        .await
        .map_err(reading)?;
    let (status, code) = status_line(&line).map_err(reading)?;
    let mut fields = Vec::new();
    let mut pending: Option<Vec<u8>> = None;
    let mut first = true;
    loop {
        let line = read_line(stream, &mut head, &addresses)
            .await
            .map_err(reading)?;
        let folded = line.first().is_some_and(|b| matches!(b, b' ' | b'\t'));
        if first && folded {
            return Err(reading(
                [b"malformed MIME header initial line: ".as_slice(), &line].concat(),
            ));
        }
        first = false;
        if folded && let Some(joined) = &mut pending {
            joined.push(b' ');
            joined.extend_from_slice(mime_trim(&line));
            continue;
        }
        if let Some(joined) = pending.take() {
            fields.push(mime_field(joined).map_err(reading)?);
        }
        if line.is_empty() {
            break;
        }
        if !line.contains(&b':') {
            return Err(reading(
                format!(
                    "malformed MIME header: missing colon: {}",
                    go_quote_bytes(&line)
                )
                .into_bytes(),
            ));
        }
        pending = Some(mime_trim(&line).to_vec());
    }
    let body_length = content_length(code, &fields).map_err(reading)?;
    if code == 200 {
        // resp.Body.Close drains the declared body; its error is ignored.
        if body_length > 0 {
            let mut body = (&mut *stream).take(body_length);
            let _ = tokio::io::copy(&mut body, &mut tokio::io::sink()).await;
        }
        return Ok(());
    }
    // httputil.DumpResponse(resp, true) reads the whole declared body.
    let mut dump = head;
    let mut body = (&mut *stream).take(body_length);
    Err(match body.read_to_end(&mut dump).await {
        Ok(read) if read as u64 == body_length => format!(
            "failed to do connect handshake, response: {}",
            go_quote_bytes(&dump)
        )
        .into_bytes(),
        _ => [
            b"failed to do connect handshake, status code: ".as_slice(),
            &status,
        ]
        .concat(),
    })
}

fn connect_request(address: &str, authorization: Option<&str>) -> String {
    let authorization = authorization
        .map(|value| format!("Proxy-Authorization: {value}\r\n"))
        .unwrap_or_default();
    format!(
        "CONNECT {address} HTTP/1.1\r\nHost: {address}\r\nUser-Agent: {GRPC_GO_USER_AGENT}\r\n{authorization}\r\n"
    )
}

/// One `bufio.ReadLine` line (LF, optional CR before it), appended raw to
/// `head`. ReadResponse turns any EOF into unexpected EOF.
async fn read_line(
    stream: &mut TcpStream,
    head: &mut Vec<u8>,
    addresses: &str,
) -> Result<Vec<u8>, Vec<u8>> {
    let mut line = Vec::new();
    loop {
        match stream.read_u8().await {
            Ok(byte) => {
                head.push(byte);
                if byte == b'\n' {
                    if line.last() == Some(&b'\r') {
                        line.pop();
                    }
                    return Ok(line);
                }
                line.push(byte);
            }
            Err(err) if err.kind() == io::ErrorKind::UnexpectedEof => {
                return Err(b"unexpected EOF".to_vec());
            }
            Err(err) => {
                return Err(
                    format!("read tcp {addresses}: read: {}", os_message(&err)).into_bytes()
                );
            }
        }
    }
}

/// `http.ReadResponse` status-line checks: `resp.Status` and its code.
fn status_line(line: &[u8]) -> Result<(Vec<u8>, i64), Vec<u8>> {
    let bad = |what: &str, value: &[u8]| format!("{what} {}", go_quote_bytes(value)).into_bytes();
    let space = line
        .iter()
        .position(|b| *b == b' ')
        .ok_or_else(|| bad("malformed HTTP response", line))?;
    let (proto, status) = (&line[..space], &line[space + 1..]);
    let status = &status[status.iter().take_while(|b| **b == b' ').count()..];
    let code = status.split(|b| *b == b' ').next().unwrap_or_default();
    if code.len() != 3 {
        return Err(bad("malformed HTTP status code", code));
    }
    let value = go_atoi(code)
        .filter(|value| *value >= 0)
        .ok_or_else(|| bad("malformed HTTP status code", code))?;
    if !go_http_version(proto) {
        return Err(bad("malformed HTTP version", proto));
    }
    Ok((status.to_vec(), value))
}

/// Go `strconv.Atoi` on a 3-byte field: optional sign, then digits.
fn go_atoi(raw: &[u8]) -> Option<i64> {
    let digits = match raw.first() {
        Some(b'+' | b'-') => &raw[1..],
        _ => raw,
    };
    if digits.is_empty() || !digits.iter().all(u8::is_ascii_digit) {
        return None;
    }
    std::str::from_utf8(raw).ok()?.parse().ok()
}

/// Go `http.ParseHTTPVersion`: "HTTP/X.Y" with single digits.
fn go_http_version(proto: &[u8]) -> bool {
    proto.len() == 8
        && proto.starts_with(b"HTTP/")
        && proto[6] == b'.'
        && proto[5].is_ascii_digit()
        && proto[7].is_ascii_digit()
}

/// Go `textproto` `trim`: ASCII space, tab, CR and LF at both ends.
fn mime_trim(line: &[u8]) -> &[u8] {
    let space = |b: &u8| matches!(b, b' ' | b'\t' | b'\r' | b'\n');
    let start = line.iter().position(|b| !space(b)).unwrap_or(line.len());
    let end = line
        .iter()
        .rposition(|b| !space(b))
        .map_or(start, |i| i + 1);
    &line[start..end]
}

/// One `readMIMEHeader` field from its joined line: the key must be token
/// bytes (Go also accepts spaces, go.dev/issue/34540, without
/// canonicalizing), and the value must be valid header-value bytes.
fn mime_field(joined: Vec<u8>) -> Result<(Vec<u8>, Vec<u8>), Vec<u8>> {
    let malformed = |joined: &[u8]| [b"malformed MIME header line: ".as_slice(), joined].concat();
    let Some(colon) = joined.iter().position(|b| *b == b':') else {
        return Err(malformed(&joined));
    };
    let (key, value) = (&joined[..colon], &joined[colon + 1..]);
    let key_byte = |b: &u8| b.is_ascii_alphanumeric() || b"!#$%&'*+-.^_`|~ ".contains(b);
    let value_byte = |b: &u8| *b == b'\t' || (*b >= 0x20 && *b != 0x7f);
    if key.is_empty() || !key.iter().all(key_byte) || !value.iter().all(value_byte) {
        return Err(malformed(&joined));
    }
    let start = value
        .iter()
        .take_while(|b| matches!(b, b' ' | b'\t'))
        .count();
    Ok((key.to_vec(), value[start..].to_vec()))
}

/// `readTransfer`/`fixLength` for a CONNECT response: Content-Length
/// conflicts and syntax, and no body for 1xx, 204 and 304. A key with a space
/// is not canonicalized by Go, so it never matches Content-Length.
fn content_length(code: i64, fields: &[(Vec<u8>, Vec<u8>)]) -> Result<u64, Vec<u8>> {
    let lengths: Vec<&[u8]> = fields
        .iter()
        .filter(|(key, _)| !key.contains(&b' ') && key.eq_ignore_ascii_case(b"content-length"))
        .map(|(_, value)| mime_trim(value))
        .collect();
    let mut length = 0;
    if let Some(first) = lengths.first() {
        if lengths.iter().any(|value| value != first) {
            let quoted: Vec<String> = lengths.iter().map(|value| go_quote_bytes(value)).collect();
            return Err(format!(
                "http: message cannot contain multiple Content-Length headers; got [{}]",
                quoted.join(" ")
            )
            .into_bytes());
        }
        if first.is_empty() {
            return Err(b"invalid empty Content-Length \"\"".to_vec());
        }
        length = std::str::from_utf8(first)
            .ok()
            .filter(|text| text.bytes().all(|b| b.is_ascii_digit()))
            .and_then(|text| text.parse::<u64>().ok())
            .filter(|n| *n <= i64::MAX as u64)
            .ok_or_else(|| format!("bad Content-Length {}", go_quote_bytes(first)).into_bytes())?;
    }
    if code / 100 == 1 || code == 204 || code == 304 {
        length = 0;
    }
    Ok(length)
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::StreamExt;
    use tokio::net::TcpListener;

    fn runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
    }

    #[test]
    fn ticket_matches_go_json_marshal_with_html_escaping() {
        assert_eq!(
            ticket("backend_logs"),
            Bytes::from_static(br#"{"stream":"backend_logs"}"#)
        );
        assert_eq!(
            ticket("a<b>&\"c\\\u{2028}\u{2029}\n\t\u{8}\u{c}\u{1}\u{7f}é"),
            Bytes::from(
                "{\"stream\":\"a\\u003cb\\u003e\\u0026\\\"c\\\\\\u2028\\u2029\\n\\t\\b\\f\\u0001\u{7f}é\"}"
            )
        );
    }

    #[test]
    fn about_port_follows_go_unmarshal() {
        assert_eq!(about_grpc_port(br#"{"grpcPort":8001}"#).unwrap(), 8001);
        assert_eq!(about_grpc_port(br#"{"version":"v"}"#).unwrap(), 0);
        assert_eq!(about_grpc_port(b"null").unwrap(), 0);
        assert_eq!(
            about_grpc_port(br#"{"grpcPort":1,"GRPCPORT":2,"grpcport":null}"#).unwrap(),
            2
        );
        assert_eq!(about_grpc_port(br#"{"grpcPort":-1}"#).unwrap(), -1);
        assert_eq!(
            about_grpc_port(br#"{"grpcPort":"8001"}"#).unwrap_err(),
            "json: cannot unmarshal string into Go struct field About.grpcPort of type int"
        );
        assert_eq!(
            about_grpc_port(br#"{"grpcPort":8001.5}"#).unwrap_err(),
            "json: cannot unmarshal number 8001.5 into Go struct field About.grpcPort of type int"
        );
        assert_eq!(
            about_grpc_port(br#"[]"#).unwrap_err(),
            "json: cannot unmarshal array into Go value of type analytics.About"
        );
        assert_eq!(
            about_grpc_port(br#"{"mode":1,"grpcPort":8001}"#).unwrap_err(),
            "json: cannot unmarshal number into Go struct field About.mode of type string"
        );
        assert!(about_grpc_port(b"{").is_err());
    }

    #[test]
    fn target_uses_raw_hostname_and_scheme_policy() {
        let target = |raw, cloud| FlightTarget::new(raw, cloud);
        assert_eq!(
            target("http://Example.COM:8000/base", false).unwrap(),
            FlightTarget {
                host: "Example.COM".into(),
                tls: false
            }
        );
        assert!(target("HTTPS://h:443", false).unwrap().tls);
        assert!(target("http://h", true).unwrap().tls);
        let v6 = target("http://user:pw@[::1]:8000/", false).unwrap();
        assert_eq!(v6.host, "::1");
        assert_eq!(v6.address(8001), "[::1]:8001");
        assert_eq!(target("http://h:80", false).unwrap().address(80), "h:80");
        assert_eq!(
            target("ftp://h", false).unwrap_err(),
            "unsupported profile URL scheme \"ftp\""
        );
        assert_eq!(
            target("FTP://h", false).unwrap_err(),
            "unsupported profile URL scheme \"ftp\"",
            "url.Parse lowercases the scheme"
        );
        assert!(
            target("ftp://h", true).unwrap().tls,
            "cloud selects TLS before scheme validation"
        );
        assert_eq!(
            target("localhost:8000", false).unwrap_err(),
            "invalid profile URL \"localhost:8000\""
        );
        assert_eq!(
            target("http://", false).unwrap_err(),
            "invalid profile URL \"http://\""
        );
    }

    #[test]
    fn metadata_matches_tail_auth_metadata() {
        let map = metadata(&Auth::CloudOAuth {
            session_token: "a b,c".into(),
            tenant_id: "t".into(),
        })
        .unwrap();
        let text =
            |map: &MetadataMap, key: &str| map.get(key).unwrap().to_str().unwrap().to_owned();
        assert_eq!(text(&map, "cookie"), "session=a b,c");
        assert_eq!(text(&map, "x-p-tenant"), "t");
        let map = metadata(&Auth::SelfHostedBasic {
            username: "alice".into(),
            password: "p:a:ss".into(),
        })
        .unwrap();
        assert_eq!(text(&map, "authorization"), "Basic YWxpY2U6cDphOnNz");
        assert_eq!(map.len(), 1);
        let map = metadata(&Auth::CloudApiKey {
            api_key: "k".into(),
            tenant_id: "t".into(),
        })
        .unwrap();
        assert_eq!((text(&map, "x-api-key"), map.len()), ("k".to_owned(), 2));
        for bad in ["tab\there", "é", "del\u{7f}"] {
            let error = metadata(&Auth::SelfHostedApiKey {
                api_key: bad.into(),
            })
            .unwrap_err();
            assert_eq!(error.code(), Code::Internal as u32);
            assert_eq!(
                text_error(&error),
                "rpc error: code = Internal desc = header key \"x-api-key\" contains value with non-printable ASCII characters"
            );
        }
    }

    fn text_error(status: &FlightStatus) -> String {
        String::from_utf8(status.go_error()).unwrap()
    }

    fn status_message(status: &Status) -> String {
        text_error(&classify(status.clone()))
    }

    #[test]
    fn flight_status_keeps_go_codes_and_raw_bytes() {
        let status = FlightStatus::new(14, b"a\xe9b".to_vec());
        assert_eq!(
            status.go_error(),
            b"rpc error: code = Unavailable desc = a\xe9b"
        );
        assert!(status.is_stream_end());
        assert!(FlightStatus::new(0, "").is_stream_end());
        assert!(FlightStatus::new(1, "").is_stream_end());
        assert!(!FlightStatus::new(2, "").is_stream_end());
        assert_eq!(
            FlightStatus::new(20, "x").go_error(),
            b"rpc error: code = Code(20) desc = x"
        );
        assert_eq!(
            FlightStatus::new(u32::MAX, "").go_error(),
            b"rpc error: code = Code(4294967295) desc = "
        );
    }

    #[test]
    fn status_messages_use_go_code_names_and_exact_limit_mapping() {
        assert_eq!(
            status_message(&Status::cancelled("bye")),
            "rpc error: code = Canceled desc = bye"
        );
        assert_eq!(
            status_message(&Status::invalid_argument("done")),
            "rpc error: code = InvalidArgument desc = done"
        );
        let limit = "Error, decoded message length too large: found 4194305 bytes, the limit is: 4194304 bytes";
        assert_eq!(
            status_message(&Status::out_of_range(limit)),
            "rpc error: code = ResourceExhausted desc = grpc: received message larger than max (4194305 vs. 4194304)"
        );
        for other in [
            Status::resource_exhausted(limit),
            Status::out_of_range(format!("{limit}!")),
            Status::out_of_range(limit.replace("4194305", "x")),
            Status::out_of_range("past the end"),
            // Only our own cap, and only a size above it.
            Status::out_of_range(limit.replace("4194304", "1024")),
            Status::out_of_range(limit.replace("4194305", "4194304")),
            // Server statuses carrying details or metadata are left alone.
            Status::with_details(Code::OutOfRange, limit, Bytes::from_static(b"d")),
            Status::with_metadata(Code::OutOfRange, limit, {
                let mut map = MetadataMap::new();
                map.insert("x", "y".parse().unwrap());
                map
            }),
        ] {
            assert!(status_message(&other).starts_with(&format!(
                "rpc error: code = {} desc = ",
                go_code(other.code() as u32)
            )));
            assert!(!status_message(&other).contains("received message larger"));
        }
    }

    #[test]
    fn proxy_selection_follows_grpc_map_address() {
        let env = |proxy: &str, no: &str| ProxyEnvironment {
            https_proxy: proxy.into(),
            no_proxy: no.into(),
        };
        assert_eq!(env("", "").route("h:1"), None);
        assert_eq!(env("http://p:3128", "").route("localhost:1"), None);
        assert_eq!(env("http://p:3128", "").route("127.0.0.2:1"), None);
        assert_eq!(env("http://p:3128", "").route("[::1]:1"), None);
        assert_eq!(
            env("http://p:3128", "").route("[::ffff:127.0.0.1]:1"),
            None,
            "Go IsLoopback covers IPv4-mapped loopback"
        );
        assert!(
            env("http://p:3128", "")
                .route("[::ffff:10.0.0.1]:1")
                .is_some()
        );
        assert_eq!(env("http://p:3128", "h").route("h:1"), None);
        assert!(env("http://p:3128", "h:2").route("h:1").is_some());
        assert_eq!(
            env("p:80", "").route("h:1"),
            Some(ProxyRoute::Connect {
                host: "p:80".into(),
                authorization: None
            }),
            "scheme-less values retry with http:// and keep the explicit port"
        );
        assert_eq!(
            env("socks5://u%3A:p%40w@p", "").route("h:1"),
            Some(ProxyRoute::Connect {
                host: "p".into(),
                authorization: Some(format!("Basic {}", STANDARD.encode("u::p@w"))),
            }),
            "scheme ignored, no default port, userinfo decoded"
        );
        assert_eq!(
            env("http://u@p:1", "").route("h:1"),
            Some(ProxyRoute::Connect {
                host: "p:1".into(),
                authorization: Some(format!("Basic {}", STANDARD.encode("u:"))),
            })
        );
    }

    /// Port selects the fake outcome: 1 hangs, 2 succeeds, 3 is refused,
    /// 4 succeeds after 400 ms.
    async fn fake_connect(address: SocketAddr) -> io::Result<u16> {
        match address.port() {
            1 => std::future::pending().await,
            3 => Err(io::Error::from(io::ErrorKind::ConnectionRefused)),
            4 => {
                tokio::time::sleep(Duration::from_millis(400)).await;
                Ok(4)
            }
            port => Ok(port),
        }
    }

    #[test]
    fn happy_eyeballs_follows_go_dial_parallel() {
        let v4 = |port| SocketAddr::from(([192, 0, 2, 1], port));
        let v6 = |port| SocketAddr::from(([0x2001, 0xdb8, 0, 0, 0, 0, 0, 1], port));
        runtime().block_on(async {
            let deadline = || Instant::now() + CONNECT_TIMEOUT;
            // A hanging primary family yields to the fallback after 300 ms.
            let start = Instant::now();
            let result = dial_parallel(&[v4(1)], &[v6(2)], deadline(), &fake_connect).await;
            assert_eq!(result, Ok(2));
            let elapsed = start.elapsed();
            assert!(
                elapsed >= FALLBACK_DELAY && elapsed < Duration::from_secs(2),
                "{elapsed:?}"
            );
            // A failed primary starts the fallback at once.
            let start = Instant::now();
            assert_eq!(
                dial_parallel(&[v4(3)], &[v6(2)], deadline(), &fake_connect).await,
                Ok(2)
            );
            assert!(start.elapsed() < FALLBACK_DELAY);
            // A primary finishing after the fallback started still wins.
            assert_eq!(
                dial_parallel(&[v4(4)], &[v6(1)], deadline(), &fake_connect).await,
                Ok(4)
            );
            // Both failing reports the primary's first error.
            assert_eq!(
                dial_parallel(&[v4(3)], &[v6(3)], deadline(), &fake_connect).await,
                Err("dial tcp 192.0.2.1:3: connect: connection refused".into())
            );
            // Serial: the first error is kept while later addresses are tried.
            assert_eq!(
                dial_serial(&[v4(3), v6(3)], deadline(), &fake_connect).await,
                Err::<u16, _>("dial tcp 192.0.2.1:3: connect: connection refused".into())
            );
            assert_eq!(
                dial_serial(&[v4(3), v4(2)], deadline(), &fake_connect).await,
                Ok(2)
            );
            // An exhausted deadline is Go's i/o timeout for that address.
            let soon = Instant::now() + Duration::from_millis(50);
            assert_eq!(
                dial_serial(&[v6(1)], soon, &fake_connect).await,
                Err::<u16, _>("dial tcp [2001:db8::1]:1: i/o timeout".into())
            );
            // Go checks ctx.Done before each address: the expired address wins.
            let soon = Instant::now() + Duration::from_millis(50);
            assert_eq!(
                dial_serial(&[v4(1), v6(1)], soon, &fake_connect).await,
                Err::<u16, _>("dial tcp [2001:db8::1]:1: i/o timeout".into())
            );
        });
    }

    #[test]
    fn partial_deadline_matches_go() {
        let now = Instant::now();
        let deadline = now + CONNECT_TIMEOUT;
        assert_eq!(
            partial_deadline(now, deadline, 2),
            Some(now + Duration::from_secs(10))
        );
        assert_eq!(
            partial_deadline(now, deadline, 20),
            Some(now + MIN_ADDRESS_TIMEOUT)
        );
        let short = now + Duration::from_secs(1);
        assert_eq!(partial_deadline(now, short, 3), Some(short));
        assert_eq!(partial_deadline(now, now, 1), None);
    }

    #[test]
    fn connect_request_bytes() {
        assert_eq!(
            connect_request("[::1]:8001", Some("Basic eA==")),
            "CONNECT [::1]:8001 HTTP/1.1\r\nHost: [::1]:8001\r\nUser-Agent: grpc-go/1.65.0\r\nProxy-Authorization: Basic eA==\r\n\r\n"
        );
    }

    #[test]
    fn connect_response_parts_follow_go() {
        assert_eq!(
            status_line(b"HTTP/1.1 200 Connection established"),
            Ok((b"200 Connection established".to_vec(), 200))
        );
        assert_eq!(status_line(b"HTTP/1.0  200"), Ok((b"200".to_vec(), 200)));
        assert_eq!(
            status_line(b"HTTP/1.1 +20 OK"),
            Ok((b"+20 OK".to_vec(), 20))
        );
        let error = |line: &[u8]| String::from_utf8(status_line(line).unwrap_err()).unwrap();
        assert_eq!(error(b"garbage"), "malformed HTTP response \"garbage\"");
        assert_eq!(
            error(b"gar\xffbage"),
            "malformed HTTP response \"gar\\xffbage\""
        );
        assert_eq!(error(b""), "malformed HTTP response \"\"");
        assert_eq!(
            error(b"HTTP/1.1 20 OK"),
            "malformed HTTP status code \"20\""
        );
        assert_eq!(
            error(b"HTTP/1.1 -20 OK"),
            "malformed HTTP status code \"-20\""
        );
        assert_eq!(
            error(b"HTTQ/1.1 200 OK"),
            "malformed HTTP version \"HTTQ/1.1\""
        );
        assert_eq!(
            error(b"HTTP/1.10 200 OK"),
            "malformed HTTP version \"HTTP/1.10\""
        );

        let field = |joined: &[u8]| mime_field(joined.to_vec());
        assert_eq!(field(b"X: y"), Ok((b"X".to_vec(), b"y".to_vec())));
        // go.dev/issue/34540: a space in the key is accepted, not canonicalized.
        assert_eq!(
            field(b"Bad Key: v"),
            Ok((b"Bad Key".to_vec(), b"v".to_vec()))
        );
        assert_eq!(field(b"K: \xffv"), Ok((b"K".to_vec(), b"\xffv".to_vec())));
        assert_eq!(
            field(b"K: a\x01b").unwrap_err(),
            b"malformed MIME header line: K: a\x01b"
        );
        assert_eq!(
            field(b"K\x7f: v").unwrap_err(),
            b"malformed MIME header line: K\x7f: v"
        );
        assert_eq!(
            field(b": v").unwrap_err(),
            b"malformed MIME header line: : v"
        );

        let fields = |pairs: &[(&[u8], &[u8])]| {
            pairs
                .iter()
                .map(|(k, v)| (k.to_vec(), v.to_vec()))
                .collect::<Vec<_>>()
        };
        let length = |code, pairs: &[(&[u8], &[u8])]| content_length(code, &fields(pairs));
        assert_eq!(length(200, &[]), Ok(0));
        assert_eq!(
            length(407, &[(b"content-length", b"6"), (b"Content-Length", b"6")]),
            Ok(6)
        );
        assert_eq!(length(204, &[(b"Content-Length", b"6")]), Ok(0));
        assert_eq!(length(200, &[(b"Content-Length ", b"x")]), Ok(0));
        assert_eq!(
            length(200, &[(b"Content-Length", b"1"), (b"Content-Length", b"2")]).unwrap_err(),
            b"http: message cannot contain multiple Content-Length headers; got [\"1\" \"2\"]"
        );
        assert_eq!(
            length(200, &[(b"Content-Length", b"x")]).unwrap_err(),
            b"bad Content-Length \"x\""
        );
        assert_eq!(
            length(200, &[(b"Content-Length", b"+1")]).unwrap_err(),
            b"bad Content-Length \"+1\""
        );
        assert_eq!(
            length(200, &[(b"Content-Length", b"")]).unwrap_err(),
            b"invalid empty Content-Length \"\""
        );
    }

    #[test]
    fn dial_errors_use_go_text() {
        runtime().block_on(async {
            let deadline = Instant::now() + CONNECT_TIMEOUT;
            assert_eq!(
                dial_tcp("h", deadline).await.unwrap_err(),
                "dial tcp: address h: missing port in address"
            );
            assert_eq!(
                dial_tcp("h:70000", deadline).await.unwrap_err(),
                "dial tcp: address 70000: invalid port"
            );
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap();
            drop(listener);
            assert_eq!(
                dial_tcp(&address.to_string(), deadline).await.unwrap_err(),
                format!("dial tcp {address}: connect: connection refused")
            );
            assert_eq!(
                DialError(b"x \"y\" \xff".to_vec()).to_string(),
                "connection error: desc = \"x \\\"y\\\" \\xff\""
            );
            // Resolution runs off the runtime's blocking pool.
            assert!(!resolve("localhost", 1).await.unwrap().is_empty());
        });
    }

    #[test]
    fn go_quote_bytes_escapes_invalid_utf8_like_strconv() {
        assert_eq!(
            go_quote_bytes(b"a\xffb\"\n\xe2\x82"),
            "\"a\\xffb\\\"\\n\\xe2\\x82\""
        );
        assert_eq!(go_quote_bytes("é".as_bytes()), "\"é\"");
    }

    #[test]
    fn tunnel_sends_connect_and_keeps_following_bytes() {
        runtime().block_on(async {
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            let proxy = listener.local_addr().unwrap().to_string();
            let server = tokio::spawn(async move {
                let (mut stream, _) = listener.accept().await.unwrap();
                let mut head = Vec::new();
                while !head.ends_with(b"\r\n\r\n") {
                    head.push(stream.read_u8().await.unwrap());
                }
                stream
                    .write_all(b"HTTP/1.1 200 Connection established\r\nProxy Agent : x\r\n folded\r\nContent-Length: 2\r\n\r\nxxPRI")
                    .await
                    .unwrap();
                String::from_utf8(head).unwrap()
            });
            let plan = proxy_plan(proxy, Some("Basic dTpw"));
            let mut stream = plan.dial(Instant::now() + CONNECT_TIMEOUT).await.unwrap();
            let mut tail = [0; 3];
            stream.read_exact(&mut tail).await.unwrap();
            assert_eq!(&tail, b"PRI", "the declared body was drained, nothing more");
            assert_eq!(
                server.await.unwrap(),
                "CONNECT flight.test:8001 HTTP/1.1\r\nHost: flight.test:8001\r\nUser-Agent: grpc-go/1.65.0\r\nProxy-Authorization: Basic dTpw\r\n\r\n"
            );
        });
    }

    fn proxy_plan(host: String, authorization: Option<&str>) -> DialPlan {
        DialPlan {
            address: "flight.test:8001".into(),
            proxy: Some(ProxyRoute::Connect {
                host,
                authorization: authorization.map(Into::into),
            }),
            tls: None,
            connect_timeout: CONNECT_TIMEOUT,
        }
    }

    /// A proxy that answers the CONNECT head with `reply`, then closes, or
    /// stays open without sending more when `hold`.
    async fn proxy_replying(reply: &'static [u8], hold: bool) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let proxy = listener.local_addr().unwrap().to_string();
        tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") {
                head.push(stream.read_u8().await.unwrap());
            }
            stream.write_all(reply).await.unwrap();
            if hold {
                tokio::time::sleep(Duration::from_secs(30)).await;
            }
        });
        proxy
    }

    #[test]
    fn rejected_tunnel_reports_go_dump_or_status() {
        runtime().block_on(async {
            let deadline = || Instant::now() + CONNECT_TIMEOUT;
            for (reply, hold, expected) in [
                // The harness fixture's exact Go oracle text.
                (
                    &b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 6\r\n\r\ndenied"[..],
                    false,
                    &b"failed to do connect handshake, response: \"HTTP/1.1 407 Proxy Authentication Required\\r\\nContent-Length: 6\\r\\n\\r\\ndenied\""[..],
                ),
                (
                    b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n",
                    false,
                    b"failed to do connect handshake, response: \"HTTP/1.1 407 Proxy Authentication Required\\r\\n\\r\\n\"",
                ),
                (
                    b"HTTP/1.1 403 \xffForbidden\r\n\r\n",
                    false,
                    b"failed to do connect handshake, response: \"HTTP/1.1 403 \\xffForbidden\\r\\n\\r\\n\"",
                ),
                // DumpResponse fails on a short body: Go reports resp.Status.
                (
                    b"HTTP/1.1 403 Forbidden\xff\r\nContent-Length: 9\r\n\r\nshort",
                    false,
                    b"failed to do connect handshake, status code: 403 Forbidden\xff",
                ),
                (b"", false, b"reading server HTTP response: unexpected EOF"),
                (b"HTTP/1.1 200", false, b"reading server HTTP response: unexpected EOF"),
                (
                    b"HTTP/1.1 200 OK\r\nContent-Length: x\r\n\r\n",
                    false,
                    b"reading server HTTP response: bad Content-Length \"x\"",
                ),
                // Validated as read: a bad line fails while the proxy idles.
                (
                    b"garbage\r\n",
                    true,
                    b"reading server HTTP response: malformed HTTP response \"garbage\"",
                ),
                (
                    b"HTTP/1.1 200 OK\r\nnocolon\r\n",
                    true,
                    b"reading server HTTP response: malformed MIME header: missing colon: \"nocolon\"",
                ),
                (
                    b"HTTP/1.1 200 OK\r\n X: y\r\n",
                    true,
                    b"reading server HTTP response: malformed MIME header initial line:  X: y",
                ),
                (
                    b"HTTP/1.1 200 OK\r\nK: a\x01b\r\n\r\n",
                    false,
                    b"reading server HTTP response: malformed MIME header line: K: a\x01b",
                ),
            ] {
                let plan = proxy_plan(proxy_replying(reply, hold).await, None);
                let start = Instant::now();
                assert_eq!(plan.dial(deadline()).await.unwrap_err(), expected);
                assert!(start.elapsed() < Duration::from_secs(5));
            }
        });
    }

    #[test]
    fn construction_failures_are_deferred_to_do_get() {
        runtime().block_on(async {
            let target = FlightTarget {
                host: "h".into(),
                tls: false,
            };
            let error = target
                .client(70000, &ProxyEnvironment::default(), false)
                .unwrap_err();
            assert_eq!(
                text_error(&error),
                "rpc error: code = Unavailable desc = connection error: desc = \"transport: Error while dialing: dial tcp: address 70000: invalid port\""
            );
            let mut connection = FlightConnection {
                client: Err(error.clone()),
                metadata: metadata(&Auth::SelfHostedApiKey {
                    api_key: "\n".into(),
                }),
                ticket: ticket("d"),
            };
            // Metadata validation precedes dialing, and each call repeats it.
            for _ in 0..2 {
                assert_eq!(
                    connection.do_get().await.unwrap_err().code(),
                    Code::Internal as u32
                );
            }
            connection.metadata = Ok(MetadataMap::new());
            assert_eq!(connection.do_get().await.unwrap_err(), error);
        });
    }

    fn plaintext_connection(port: u16, connect_timeout: Duration) -> FlightConnection {
        let target = FlightTarget {
            host: "127.0.0.1".into(),
            tls: false,
        };
        FlightConnection {
            client: target.client_with(
                port.into(),
                &ProxyEnvironment::default(),
                false,
                connect_timeout,
            ),
            metadata: Ok(MetadataMap::new()),
            ticket: ticket("d"),
        }
    }

    /// A one-request h2 server that answers with one empty FlightData
    /// message, then either sends `trailers` or closes the socket (FIN).
    /// Returns the port and the request's :authority.
    async fn flight_server(
        trailers: Option<(&'static str, &'static str)>,
    ) -> (u16, tokio::task::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let task = tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let mut connection = h2::server::handshake(tcp).await.unwrap();
            let (request, mut respond) = connection.accept().await.unwrap().unwrap();
            let authority = request.uri().authority().unwrap().to_string();
            let response = http::Response::builder()
                .header("content-type", "application/grpc")
                .body(())
                .unwrap();
            let mut send = respond.send_response(response, false).unwrap();
            send.send_data(Bytes::from_static(&[0, 0, 0, 0, 0]), false)
                .unwrap();
            if let Some((code, message)) = trailers {
                let mut map = http::HeaderMap::new();
                map.insert("grpc-status", code.parse().unwrap());
                map.insert("grpc-message", message.parse().unwrap());
                send.send_trailers(map).unwrap();
            }
            // Drive the connection long enough to flush, then drop it
            // before the stream so no RST_STREAM is written: a bare FIN.
            let _ = timeout_at(
                Instant::now() + Duration::from_millis(200),
                connection.accept(),
            )
            .await;
            drop(connection);
            drop(send);
            authority
        });
        (port, task)
    }

    #[test]
    fn lost_connection_mid_stream_reads_as_unavailable() {
        runtime().block_on(async {
            let (port, server) = flight_server(None).await;
            let mut connection = plaintext_connection(port, CONNECT_TIMEOUT);
            let mut stream = connection.do_get().await.unwrap();
            assert!(stream.next().await.unwrap().is_ok());
            let status = stream.next().await.unwrap().unwrap_err();
            assert_eq!(status.code(), Code::Unavailable as u32, "{status:?}");
            assert!(status.is_stream_end());
            // Plaintext :authority is the dialed host:port, as in grpc-go.
            assert_eq!(server.await.unwrap(), format!("127.0.0.1:{port}"));
        });
    }

    #[test]
    fn server_statuses_pass_through() {
        runtime().block_on(async {
            for (code, expected) in [("2", 2), ("13", 13)] {
                let (port, server) = flight_server(Some((code, "boom"))).await;
                let mut connection = plaintext_connection(port, CONNECT_TIMEOUT);
                let mut stream = connection.do_get().await.unwrap();
                assert!(stream.next().await.unwrap().is_ok());
                let status = stream.next().await.unwrap().unwrap_err();
                assert_eq!(status, FlightStatus::new(expected, "boom"));
                server.await.unwrap();
            }
            // Local statuses without a transport source are untouched.
            assert_eq!(
                classify(Status::unknown("x")),
                FlightStatus::new(Code::Unknown as u32, "x")
            );
        });
    }

    const END_STREAM: u8 = 0x1;
    const END_HEADERS: u8 = 0x4;

    fn frame(kind: u8, flags: u8, stream: u32, payload: &[u8]) -> Vec<u8> {
        let length = payload.len() as u32;
        let mut out = length.to_be_bytes()[1..].to_vec();
        out.extend_from_slice(&[kind, flags]);
        out.extend_from_slice(&stream.to_be_bytes());
        out.extend_from_slice(payload);
        out
    }

    /// HPACK literals without indexing (`:status` by its static name index).
    fn hpack(fields: &[(&str, &[u8])]) -> Vec<u8> {
        let mut out = Vec::new();
        for (name, value) in fields {
            if *name == ":status" {
                out.push(0x08);
            } else {
                out.push(0x00);
                hpack_string(&mut out, name.as_bytes());
            }
            hpack_string(&mut out, value);
        }
        out
    }

    /// An HPACK string literal without Huffman coding: RFC 7541 5.1 length
    /// with a 7-bit prefix and continuation bytes.
    fn hpack_string(out: &mut Vec<u8>, bytes: &[u8]) {
        let mut length = bytes.len();
        if length < 127 {
            out.push(length as u8);
        } else {
            out.push(127);
            length -= 127;
            while length >= 128 {
                out.push((length % 128) as u8 | 0x80);
                length /= 128;
            }
            out.push(length as u8);
        }
        out.extend_from_slice(bytes);
    }

    #[test]
    fn hpack_helper_encodes_long_lengths() {
        let mut out = Vec::new();
        hpack_string(&mut out, &[b'x'; 139]);
        assert_eq!(&out[..2], &[0x7f, 0x0c]);
        let mut out = Vec::new();
        hpack_string(&mut out, &[b'x'; 127 + 300]);
        assert_eq!(&out[..3], &[0x7f, 0xac, 0x02]);
    }

    fn head_on(stream: u32, status: &str, content_type: &str) -> Vec<u8> {
        let mut fields: Vec<(&str, &[u8])> = vec![(":status", status.as_bytes())];
        if !content_type.is_empty() {
            fields.push(("content-type", content_type.as_bytes()));
        }
        frame(FRAME_HEADERS, END_HEADERS, stream, &hpack(&fields))
    }

    fn trailers_on(stream: u32, fields: &[(&str, &[u8])]) -> Vec<u8> {
        frame(
            FRAME_HEADERS,
            END_HEADERS | END_STREAM,
            stream,
            &hpack(fields),
        )
    }

    fn head(status: &str, content_type: &str) -> Vec<u8> {
        let mut fields: Vec<(&str, &[u8])> = vec![(":status", status.as_bytes())];
        if !content_type.is_empty() {
            fields.push(("content-type", content_type.as_bytes()));
        }
        frame(FRAME_HEADERS, END_HEADERS, 1, &hpack(&fields))
    }

    fn grpc_ok_head() -> Vec<u8> {
        head("200", "application/grpc")
    }

    fn empty_message(flags: u8) -> Vec<u8> {
        frame(FRAME_DATA, flags, 1, &[0, 0, 0, 0, 0])
    }

    fn trailer_frame(fields: &[(&str, &[u8])]) -> Vec<u8> {
        frame(FRAME_HEADERS, END_HEADERS | END_STREAM, 1, &hpack(fields))
    }

    fn goaway(last: u32, code: u32) -> Vec<u8> {
        let mut payload = last.to_be_bytes().to_vec();
        payload.extend_from_slice(&code.to_be_bytes());
        frame(FRAME_GOAWAY, 0, 0, &payload)
    }

    enum Step {
        Wait(u64),
        Send(Vec<u8>),
    }

    /// A raw HTTP/2 server for successive connections, one script each:
    /// SETTINGS and its ACK, then the steps once the client's request has
    /// arrived, then a FIN. Client bytes are drained. Counts connections.
    async fn raw_connections(
        connections: Vec<Vec<Step>>,
    ) -> (u16, Arc<std::sync::atomic::AtomicUsize>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let accepted = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let counter = accepted.clone();
        tokio::spawn(async move {
            for steps in connections {
                let (tcp, _) = listener.accept().await.unwrap();
                counter.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                tokio::spawn(async move {
                    let (mut read, mut write) = tcp.into_split();
                    tokio::spawn(async move {
                        let mut buffer = [0; 4096];
                        while matches!(read.read(&mut buffer).await, Ok(n) if n > 0) {}
                    });
                    write
                        .write_all(&[frame(4, 0, 0, &[]), frame(4, 1, 0, &[])].concat())
                        .await
                        .unwrap();
                    tokio::time::sleep(Duration::from_millis(150)).await;
                    for step in steps {
                        match step {
                            Step::Wait(ms) => tokio::time::sleep(Duration::from_millis(ms)).await,
                            Step::Send(bytes) => write.write_all(&bytes).await.unwrap(),
                        }
                    }
                    tokio::time::sleep(Duration::from_millis(300)).await;
                });
            }
        });
        (port, accepted)
    }

    async fn raw_server(script: Vec<Vec<u8>>) -> u16 {
        raw_connections(vec![vec![Step::Send(script.concat())]])
            .await
            .0
    }

    async fn collect(port: u16) -> Vec<Result<(), FlightStatus>> {
        let mut connection = plaintext_connection(port, CONNECT_TIMEOUT);
        let mut stream = match connection.do_get().await {
            Ok(stream) => stream,
            Err(status) => return vec![Err(status)],
        };
        let mut items = Vec::new();
        while let Some(item) = stream.next().await {
            items.push(item.map(|_| ()));
        }
        items
    }

    /// Every item the stream yields, with DoGet's own error first if any.
    async fn run(script: Vec<Vec<u8>>) -> Vec<Result<(), FlightStatus>> {
        let port = raw_server(script).await;
        let mut connection = plaintext_connection(port, CONNECT_TIMEOUT);
        let mut stream = match connection.do_get().await {
            Ok(stream) => stream,
            Err(status) => return vec![Err(status)],
        };
        let mut items = Vec::new();
        while let Some(item) = stream.next().await {
            items.push(item.map(|_| ()));
        }
        items
    }

    #[test]
    fn frames_explain_streams_that_end_without_trailers() {
        let draining = "the connection is draining";
        runtime().block_on(async {
            // GOAWAY leaving out our stream after a message: Go reconnects.
            // (Before any message it is a transparent retry; see below.)
            for code in [0, 2] {
                let items = run(vec![grpc_ok_head(), empty_message(0), goaway(0, code)]).await;
                assert_eq!(items, [Ok(()), Err(FlightStatus::new(14, draining))]);
            }
            // A GOAWAY that keeps our stream changes nothing.
            let items = run(vec![
                grpc_ok_head(),
                empty_message(0),
                goaway(1, 0),
                trailer_frame(&[("grpc-status", b"3"), ("grpc-message", b"x")]),
            ])
            .await;
            assert_eq!(items, [Ok(()), Err(FlightStatus::new(3, "x"))]);
            // These stay fatal, with Go's texts.
            let items = run(vec![
                grpc_ok_head(),
                empty_message(0),
                frame(FRAME_RST_STREAM, 0, 1, &0u32.to_be_bytes()),
            ])
            .await;
            assert_eq!(
                items,
                [
                    Ok(()),
                    Err(FlightStatus::new(
                        13,
                        "stream terminated by RST_STREAM with error code: NO_ERROR"
                    ))
                ]
            );
            let items = run(vec![
                grpc_ok_head(),
                empty_message(0),
                frame(FRAME_RST_STREAM, 0, 1, &3u32.to_be_bytes()),
            ])
            .await;
            assert_eq!(
                items,
                [
                    Ok(()),
                    Err(FlightStatus::new(
                        8,
                        "stream terminated by RST_STREAM with error code: FLOW_CONTROL_ERROR"
                    ))
                ]
            );
            let items = run(vec![grpc_ok_head(), empty_message(END_STREAM)]).await;
            assert_eq!(
                items,
                [
                    Ok(()),
                    Err(FlightStatus::new(
                        13,
                        "server closed the stream without sending trailers"
                    ))
                ]
            );
            // A trailer without grpc-status is Unknown with no message.
            let items = run(vec![
                grpc_ok_head(),
                empty_message(0),
                trailer_frame(&[("x", b"y")]),
            ])
            .await;
            assert_eq!(items, [Ok(()), Err(FlightStatus::new(2, ""))]);
            // Valid final trailers followed by GOAWAY(0) and FIN, or by FIN:
            // the server's status is kept with its raw message (OK ends).
            let message = [b'm'; 139];
            for code in [b"0".as_slice(), b"1", b"14", b"3"] {
                for after in [goaway(0, 0), Vec::new()] {
                    let items = run(vec![
                        grpc_ok_head(),
                        empty_message(0),
                        trailer_frame(&[("grpc-status", code), ("grpc-message", &message)]),
                        after,
                    ])
                    .await;
                    let code: u32 = std::str::from_utf8(code).unwrap().parse().unwrap();
                    let expected: Vec<Result<(), FlightStatus>> = if code == 0 {
                        vec![Ok(())]
                    } else {
                        vec![Ok(()), Err(FlightStatus::new(code, message.to_vec()))]
                    };
                    assert_eq!(items, expected, "code {code}");
                }
            }
            // A malformed trailer block is a local h2 connection error: Go
            // and Rust treat it as transport loss, never as the trailers.
            let items = run(vec![
                grpc_ok_head(),
                empty_message(0),
                frame(
                    FRAME_HEADERS,
                    END_HEADERS | END_STREAM,
                    1,
                    &[0x00, 0x01, b'x', 0x8b],
                ),
            ])
            .await;
            assert_eq!(items.len(), 2);
            assert_eq!(
                items[1].as_ref().unwrap_err().code(),
                Code::Unavailable as u32
            );
        });
    }

    #[test]
    fn trailers_keep_go_codes_and_raw_messages() {
        runtime().block_on(async {
            let status = |code: &[u8], message: &[u8]| {
                vec![
                    grpc_ok_head(),
                    empty_message(0),
                    trailer_frame(&[("grpc-status", code), ("grpc-message", message)]),
                ]
            };
            for (code, message, expected) in [
                (&b"14"[..], &b"a%E9b"[..], FlightStatus::new(14, b"a\xe9b".to_vec())),
                (b"14", b"a%E9%80b", FlightStatus::new(14, b"a\xe9\x80b".to_vec())),
                (b"1", b"%FF", FlightStatus::new(1, b"\xff".to_vec())),
                (b"3", b"%FF", FlightStatus::new(3, b"\xff".to_vec())),
                (b"3", b"100%", FlightStatus::new(3, "100%")),
                (b"20", b"x", FlightStatus::new(20, "x")),
                (b"-1", b"x", FlightStatus::new(u32::MAX, "x")),
            ] {
                let items = run(status(code, message)).await;
                assert_eq!(items, [Ok(()), Err(expected)]);
            }
            // OK ends the stream whatever the message: Go reads io.EOF.
            let items = run(status(b"0", b"%FF")).await;
            assert_eq!(items, [Ok(())]);
            // Malformed fields are Go's Internal, never a panic.
            let items = run(vec![
                grpc_ok_head(),
                empty_message(0),
                trailer_frame(&[("grpc-status", b"0"), ("grpc-status-details-bin", b"!!")]),
            ])
            .await;
            assert_eq!(
                items,
                [
                    Ok(()),
                    Err(FlightStatus::new(
                        13,
                        "transport: malformed grpc-status-details-bin: illegal base64 data at input byte 0"
                    ))
                ]
            );
            let items = run(status(b"abc", b"x")).await;
            assert_eq!(
                items,
                [
                    Ok(()),
                    Err(FlightStatus::new(
                        13,
                        "transport: malformed grpc-status: strconv.ParseInt: parsing \"abc\": invalid syntax"
                    ))
                ]
            );
        });
    }

    #[test]
    fn initial_headers_follow_go_operate_headers() {
        runtime().block_on(async {
            let items = run(vec![head("502", "text/html"), frame(FRAME_DATA, 0, 1, b"<html>")]).await;
            assert_eq!(
                items,
                [Err(FlightStatus::new(
                    14,
                    "unexpected HTTP status code received from server: 502 (Bad Gateway); transport: received unexpected content-type \"text/html\""
                ))]
            );
            let items = run(vec![head("200", ""), empty_message(0)]).await;
            assert_eq!(
                items,
                [Err(FlightStatus::new(2, "malformed header: missing HTTP content-type"))]
            );
            let items = run(vec![head("404", "application/grpc")]).await;
            assert_eq!(
                items,
                [Err(FlightStatus::new(
                    12,
                    "unexpected HTTP status code received from server: 404 (Not Found)"
                ))]
            );
            let items = run(vec![head("599", "application/grpc+proto")]).await;
            assert_eq!(
                items,
                [Err(FlightStatus::new(
                    2,
                    "unexpected HTTP status code received from server: 599 ()"
                ))]
            );
            // Trailers-Only responses carry Go's status from the headers.
            let only = |extra: &[(&str, &[u8])]| {
                let mut fields: Vec<(&str, &[u8])> =
                    vec![(":status", b"200"), ("content-type", b"application/grpc")];
                fields.extend_from_slice(extra);
                vec![frame(FRAME_HEADERS, END_HEADERS | END_STREAM, 1, &hpack(&fields))]
            };
            let items = run(only(&[("grpc-status", b"5"), ("grpc-message", b"%FF")])).await;
            assert_eq!(items, [Err(FlightStatus::new(5, b"\xff".to_vec()))]);
            let items = run(only(&[("grpc-status", b"0")])).await;
            assert_eq!(items, []);
            let items = run(only(&[("grpc-status", b"0"), ("x-bin", b"A")])).await;
            assert_eq!(
                items,
                [Err(FlightStatus::new(
                    13,
                    "transport: malformed x-bin: illegal base64 data at input byte 0"
                ))]
            );
        });
    }

    #[test]
    fn go_header_field_decoders() {
        for (input, expected) in [
            (&b""[..], None),
            (b"!!", Some(0)),
            (b"A", Some(0)),
            (b"QQ", None),
            (b"QQ==", None),
            (b"QQ=", Some(2)),
            (b"Q===", Some(1)),
            (b"QQ=x", Some(2)),
            (b"QQ==QQ==", Some(4)),
            (b"QUFB\nQUFB", None),
            (b"QUFBQ\xff==", Some(5)),
        ] {
            assert_eq!(go_base64_error(input), expected, "{input:?}");
        }
        for (input, expected) in [
            (&b"5"[..], Ok(5)),
            (b"+5", Ok(5)),
            (b"-1", Ok(-1)),
            (b"-2147483648", Ok(i32::MIN)),
            (b"abc", Err("invalid syntax")),
            (b"", Err("invalid syntax")),
            (b"+", Err("invalid syntax")),
            (b"1x", Err("invalid syntax")),
            (b"2147483648", Err("value out of range")),
            (b"99999999999x", Err("value out of range")),
        ] {
            assert_eq!(go_parse_int32(input), expected, "{input:?}");
        }
        let message = |raw: &'static [u8]| {
            let mut headers = http::HeaderMap::new();
            headers.insert("grpc-message", http::HeaderValue::from_bytes(raw).unwrap());
            grpc_message(&headers)
        };
        assert_eq!(message(b"a%E9b"), b"a\xe9b");
        assert_eq!(message(b"%4"), b"%4");
        assert_eq!(message(b"%zz1"), b"%zz1");
        assert_eq!(message(b"%41%"), b"A%");
        assert_eq!(message(b"%2541"), b"%41");
    }

    #[test]
    fn wire_binds_terminal_events_to_streams() {
        let mut writes = FrameScanner::writer();
        let wire = Wire::default();
        let mut client = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n".to_vec();
        client.extend(frame(4, 0, 0, &[0; 6]));
        client.extend(frame(FRAME_HEADERS, END_HEADERS, 1, b"x"));
        // Split anywhere: the scanner is incremental.
        for chunk in client.chunks(5) {
            writes.scan(chunk, &mut |f| wire.wrote(f));
        }
        assert_eq!(wire.opened(), 1);
        let mut reads = FrameScanner::default();
        let server = [goaway(1, 0), frame(FRAME_DATA, END_STREAM, 1, b"zz")].concat();
        for chunk in server.chunks(3) {
            reads.scan(chunk, &mut |f| wire.read(f));
        }
        assert_eq!(reads.frames, 2);
        assert_eq!(wire.terminal(1), Some(Terminal::DataEnd));
        assert_eq!(wire.terminal(3), None);
        assert!(wire.draining());
        // A later stream on the connection; the first terminal event wins.
        writes.scan(&frame(FRAME_HEADERS, END_HEADERS, 3, b""), &mut |f| {
            wire.wrote(f)
        });
        for event in [
            goaway(1, 0),
            frame(FRAME_RST_STREAM, 0, 3, &8u32.to_be_bytes()),
        ] {
            reads.scan(&event, &mut |f| wire.read(f));
        }
        assert_eq!(wire.terminal(3), Some(Terminal::Refused));
        assert_eq!(wire.terminal(1), None);
        // A late frame for an older stream never displaces the current one.
        reads.scan(
            &frame(FRAME_RST_STREAM, 0, 1, &0u32.to_be_bytes()),
            &mut |f| wire.read(f),
        );
        assert_eq!(wire.terminal(3), Some(Terminal::Refused));
    }

    /// Accepts one connection, reads the client preface, then either closes
    /// (`close`) or stays silent for `hold`.
    async fn silent_server(close: bool, hold: Duration) -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            let (mut tcp, _) = listener.accept().await.unwrap();
            // Drain everything the client sends first, so closing is a FIN
            // rather than a reset over unread data.
            let mut buffer = [0; 1024];
            while let Ok(Ok(read)) = timeout_at(
                Instant::now() + Duration::from_millis(100),
                tcp.read(&mut buffer),
            )
            .await
            {
                if read == 0 {
                    break;
                }
            }
            if !close {
                tokio::time::sleep(hold).await;
            }
        });
        port
    }

    #[test]
    fn server_preface_is_bounded_like_grpc_go() {
        runtime().block_on(async {
            let port = silent_server(true, Duration::ZERO).await;
            let status = plaintext_connection(port, CONNECT_TIMEOUT)
                .do_get()
                .await
                .unwrap_err();
            assert_eq!(
                text_error(&status),
                "rpc error: code = Unavailable desc = connection error: desc = \"error reading server preface: EOF\""
            );
            let port = silent_server(false, Duration::from_secs(5)).await;
            let start = Instant::now();
            let status = plaintext_connection(port, Duration::from_millis(300))
                .do_get()
                .await
                .unwrap_err();
            assert!(start.elapsed() < Duration::from_secs(3));
            let message = text_error(&status);
            assert_eq!(status.code(), Code::Unavailable as u32);
            assert!(
                message.starts_with(
                    "rpc error: code = Unavailable desc = connection error: desc = \"error reading server preface: read tcp 127.0.0.1:"
                ) && message.ends_with(&format!(
                    "->127.0.0.1:{port}: use of closed network connection\""
                )),
                "{message}"
            );
        });
    }

    #[test]
    fn refused_flight_dial_surfaces_as_unavailable_status() {
        runtime().block_on(async {
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            let port = listener.local_addr().unwrap().port();
            drop(listener);
            let target = FlightTarget {
                host: "127.0.0.1".into(),
                tls: false,
            };
            let mut connection = FlightConnection {
                client: target.client(port.into(), &ProxyEnvironment::default(), false),
                metadata: Ok(MetadataMap::new()),
                ticket: ticket("d"),
            };
            let status = connection.do_get().await.unwrap_err();
            assert_eq!(status.code(), Code::Unavailable as u32);
            assert_eq!(
                text_error(&status),
                format!(
                    "rpc error: code = Unavailable desc = connection error: desc = \"transport: Error while dialing: dial tcp 127.0.0.1:{port}: connect: connection refused\""
                )
            );
        });
    }
    #[test]
    fn partial_messages_end_as_go_unexpected_eof() {
        let eof = || FlightStatus::internal("unexpected EOF");
        runtime().block_on(async {
            let partial_prefix = frame(FRAME_DATA, 0, 1, &[0, 0, 0]);
            let partial_body = frame(FRAME_DATA, 0, 1, &[0, 0, 0, 0, 4, 1, 2]);
            for partial in [partial_prefix.clone(), partial_body.clone()] {
                // Trailers deliver io.EOF whatever their status.
                for code in [b"0".as_slice(), b"14", b"3"] {
                    let items = run(vec![
                        grpc_ok_head(),
                        empty_message(0),
                        partial.clone(),
                        trailer_frame(&[("grpc-status", code), ("grpc-message", b"x")]),
                    ])
                    .await;
                    assert_eq!(items, [Ok(()), Err(eof())]);
                }
                // So do DATA END_STREAM and RST_STREAM.
                let mut ended = partial.clone();
                ended[4] |= END_STREAM;
                let items = run(vec![grpc_ok_head(), empty_message(0), ended]).await;
                assert_eq!(items, [Ok(()), Err(eof())]);
                for code in [0u32, 2] {
                    let items = run(vec![
                        grpc_ok_head(),
                        empty_message(0),
                        partial.clone(),
                        frame(FRAME_RST_STREAM, 0, 1, &code.to_be_bytes()),
                    ])
                    .await;
                    assert_eq!(items, [Ok(()), Err(eof())]);
                }
                // A GOAWAY drain error is not an EOF: it stays Unavailable.
                let items = run(vec![
                    grpc_ok_head(),
                    empty_message(0),
                    partial.clone(),
                    goaway(0, 0),
                ])
                .await;
                assert_eq!(items, [Ok(()), Err(refused())]);
            }
            // A message split across DATA frames is complete, not partial.
            let items = run(vec![
                grpc_ok_head(),
                frame(FRAME_DATA, 0, 1, &[0, 0, 0]),
                frame(FRAME_DATA, 0, 1, &[0, 0]),
                trailer_frame(&[("grpc-status", b"3"), ("grpc-message", b"x")]),
            ])
            .await;
            assert_eq!(items, [Ok(()), Err(FlightStatus::new(3, "x"))]);
        });
    }

    fn details(code: i32, message: &str) -> Vec<u8> {
        let status = RpcStatus {
            code,
            message: message.into(),
            details: vec![prost_types::Any {
                type_url: "type.googleapis.com/x".into(),
                value: vec![1, 2],
            }],
        };
        STANDARD
            .encode(prost::Message::encode_to_vec(&status))
            .into_bytes()
    }

    #[test]
    fn status_details_follow_go_new_with_proto() {
        runtime().block_on(async {
            let with = |fields: &[(&str, &[u8])]| {
                vec![grpc_ok_head(), empty_message(0), trailer_frame(fields)]
            };
            let matched = details(14, "from proto");
            let items = run(with(&[
                ("grpc-status", b"14"),
                ("grpc-message", b"m"),
                ("grpc-status-details-bin", &matched),
            ]))
            .await;
            assert_eq!(items, [Ok(()), Err(FlightStatus::new(14, "from proto"))]);
            let mismatched = details(3, "p");
            let items = run(with(&[
                ("grpc-status", b"14"),
                ("grpc-message", b"m"),
                ("grpc-status-details-bin", &mismatched),
            ]))
            .await;
            let status = items[1].as_ref().unwrap_err();
            assert_eq!(status.code(), Code::Internal as u32);
            // The pinned Go oracle measured "code:3  message:\"proto message\"".
            assert_eq!(
                status.message(),
                b"grpc-status-details-bin mismatch: grpc-status=Unavailable, grpc-message=\"m\", grpc-status-details-bin=code:3  message:\"p\"  details:{type_url:\"type.googleapis.com/x\"  value:\"\\x01\\x02\"}"
            );
            // An empty proto has code 0: a mismatch for any non-zero status,
            // and for OK the status itself.
            let items = run(with(&[
                ("grpc-status", b"14"),
                ("grpc-message", b"m"),
                ("grpc-status-details-bin", b""),
            ]))
            .await;
            assert_eq!(items[1].as_ref().unwrap_err().code(), Code::Internal as u32);
            let items = run(with(&[("grpc-status", b"0"), ("grpc-status-details-bin", b"")])).await;
            assert_eq!(items, [Ok(())]);
            // OK with a mismatched proto is a fatal Internal in Go.
            let items = run(with(&[
                ("grpc-status", b"0"),
                ("grpc-status-details-bin", &mismatched),
            ]))
            .await;
            assert_eq!(items[1].as_ref().unwrap_err().code(), Code::Internal as u32);
            // Repeated, or not a google.rpc.Status: ignored.
            for fields in [
                vec![
                    ("grpc-status", b"14".as_slice()),
                    ("grpc-message", b"m"),
                    ("grpc-status-details-bin", &mismatched),
                    ("grpc-status-details-bin", &mismatched),
                ],
                vec![
                    ("grpc-status", b"14"),
                    ("grpc-message", b"m"),
                    ("grpc-status-details-bin", b"/w"),
                ],
            ] {
                let items = run(with(&fields)).await;
                assert_eq!(items, [Ok(()), Err(FlightStatus::new(14, "m"))]);
            }
            // Trailers-Only applies the same rule.
            let items = run(vec![frame(
                FRAME_HEADERS,
                END_HEADERS | END_STREAM,
                1,
                &hpack(&[
                    (":status", b"200"),
                    ("content-type", b"application/grpc"),
                    ("grpc-status", b"5"),
                    ("grpc-status-details-bin", &details(5, "proto")),
                ]),
            )])
            .await;
            assert_eq!(items, [Err(FlightStatus::new(5, "proto"))]);
        });
    }

    #[test]
    fn unprocessed_first_attempts_retry_once_like_grpc_go() {
        let done = || trailers_on(1, &[("grpc-status", b"3"), ("grpc-message", b"done")]);
        let served = || {
            vec![Step::Send(
                [grpc_ok_head(), empty_message(0), done()].concat(),
            )]
        };
        runtime().block_on(async {
            // GOAWAY before the response headers, and after them before any
            // message: one transparent re-issue on a new connection.
            for refusal in [vec![goaway(0, 0)], vec![grpc_ok_head(), goaway(0, 0)]] {
                let (port, accepted) =
                    raw_connections(vec![vec![Step::Send(refusal.concat())], served()]).await;
                let items = collect(port).await;
                assert_eq!(items, [Ok(()), Err(FlightStatus::new(3, "done"))]);
                assert_eq!(accepted.load(std::sync::atomic::Ordering::SeqCst), 2);
            }
            // RST_STREAM(REFUSED_STREAM) on a healthy connection: the retry
            // reuses it as stream 3.
            let (port, accepted) = raw_connections(vec![vec![
                Step::Send(frame(FRAME_RST_STREAM, 0, 1, &REFUSED_STREAM.to_be_bytes())),
                Step::Wait(200),
                Step::Send(
                    [
                        head_on(3, "200", "application/grpc"),
                        frame(FRAME_DATA, 0, 3, &[0, 0, 0, 0, 0]),
                        trailers_on(3, &[("grpc-status", b"3"), ("grpc-message", b"done")]),
                    ]
                    .concat(),
                ),
            ]])
            .await;
            let items = collect(port).await;
            assert_eq!(items, [Ok(()), Err(FlightStatus::new(3, "done"))]);
            assert_eq!(accepted.load(std::sync::atomic::Ordering::SeqCst), 1);
            // A second refusal surfaces.
            let (port, accepted) = raw_connections(vec![
                vec![Step::Send(goaway(0, 0))],
                vec![Step::Send(goaway(0, 0))],
            ])
            .await;
            let items = collect(port).await;
            assert_eq!(items, [Err(refused())]);
            assert_eq!(accepted.load(std::sync::atomic::Ordering::SeqCst), 2);
            // After a complete message the attempt is committed: no retry.
            let (port, accepted) = raw_connections(vec![
                vec![Step::Send(
                    [grpc_ok_head(), empty_message(0), goaway(0, 0)].concat(),
                )],
                served(),
            ])
            .await;
            let items = collect(port).await;
            assert_eq!(items, [Ok(()), Err(refused())]);
            assert_eq!(accepted.load(std::sync::atomic::Ordering::SeqCst), 1);
        });
    }

    #[test]
    fn resets_before_the_response_use_go_mapping() {
        runtime().block_on(async {
            let items = run(vec![frame(FRAME_RST_STREAM, 0, 1, &3u32.to_be_bytes())]).await;
            assert_eq!(
                items,
                [Err(FlightStatus::new(
                    8,
                    "stream terminated by RST_STREAM with error code: FLOW_CONTROL_ERROR"
                ))]
            );
        });
    }
    #[test]
    fn reset_before_preface_reports_the_read_like_grpc_go() {
        runtime().block_on(async {
            // A TLS server rejecting a plaintext client resets the
            // connection; hyper's later writes must not decide the error.
            for _ in 0..20 {
                let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
                let port = listener.local_addr().unwrap().port();
                tokio::spawn(async move {
                    let (mut tcp, _) = listener.accept().await.unwrap();
                    let mut buffer = [0; 5];
                    let _ = tcp.read_exact(&mut buffer).await;
                    // SO_LINGER 0 makes the close a reset (test-only).
                    #[allow(deprecated)]
                    tcp.set_linger(Some(Duration::ZERO)).unwrap();
                    drop(tcp);
                });
                let status = plaintext_connection(port, CONNECT_TIMEOUT)
                    .do_get()
                    .await
                    .unwrap_err();
                let message = text_error(&status);
                assert!(
                    message.starts_with(
                        "rpc error: code = Unavailable desc = connection error: desc = \"error reading server preface: read tcp 127.0.0.1:"
                    ) && message.ends_with(&format!(
                        "->127.0.0.1:{port}: read: connection reset by peer\""
                    )),
                    "{message}"
                );
            }
        });
    }

    #[test]
    fn prototext_strings_follow_protobuf_go() {
        assert_eq!(
            prototext_string(b"a\"b\\c\n\t\x01\x7f"),
            "\"a\\\"b\\\\c\\n\\t\\x01\\x7f\""
        );
        assert_eq!(
            prototext_string(b"\xff'\xc2\x85\xc3\xa9"),
            "\"\\xff'\\u0085\u{e9}\""
        );
        let status = RpcStatus {
            code: -1,
            message: String::new(),
            details: vec![prost_types::Any::default()],
        };
        assert_eq!(status.prototext(), "code:-1  details:{}");
    }
    /// A socket double: scripted reads (then `Pending` forever unless EOF)
    /// and writes that succeed `ok_writes` times and then fail with
    /// `write_error`, counting write attempts.
    struct MockSocket {
        reads: std::collections::VecDeque<io::Result<Vec<u8>>>,
        eof: bool,
        write_error: Option<i32>,
        ok_writes: usize,
        writes: Arc<std::sync::atomic::AtomicUsize>,
    }
    impl AsyncRead for MockSocket {
        fn poll_read(
            self: Pin<&mut Self>,
            _: &mut Context<'_>,
            buf: &mut ReadBuf<'_>,
        ) -> Poll<io::Result<()>> {
            let this = self.get_mut();
            match this.reads.pop_front() {
                Some(Ok(mut bytes)) => {
                    // Like a socket, never more than the caller's capacity.
                    let rest = bytes.split_off(bytes.len().min(buf.remaining()));
                    buf.put_slice(&bytes);
                    if !rest.is_empty() {
                        this.reads.push_front(Ok(rest));
                    }
                    Poll::Ready(Ok(()))
                }
                Some(Err(err)) => Poll::Ready(Err(err)),
                None if this.eof => Poll::Ready(Ok(())),
                None => Poll::Pending,
            }
        }
    }
    impl AsyncWrite for MockSocket {
        fn poll_write(
            self: Pin<&mut Self>,
            _: &mut Context<'_>,
            buf: &[u8],
        ) -> Poll<io::Result<usize>> {
            let attempt = self
                .writes
                .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Poll::Ready(match self.write_error {
                Some(code) if attempt >= self.ok_writes => Err(io::Error::from_raw_os_error(code)),
                _ => Ok(buf.len()),
            })
        }
        fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
            Poll::Ready(Ok(()))
        }
        fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
            Poll::Ready(Ok(()))
        }
    }

    const ECONNRESET: i32 = 104;
    const EPIPE: i32 = 32;
    const CLIENT_PREFACE: &[u8] = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n";

    fn observed(
        reads: Vec<io::Result<Vec<u8>>>,
        eof: bool,
        write_error: Option<i32>,
        ok_writes: usize,
        deadline: Duration,
    ) -> (Observed<MockSocket>, Arc<std::sync::atomic::AtomicUsize>) {
        let writes = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let socket = MockSocket {
            reads: reads.into(),
            eof,
            write_error,
            ok_writes,
            writes: writes.clone(),
        };
        let stream = Observed::new(socket, Instant::now() + deadline, "L->R".into());
        (stream, writes)
    }

    fn attempts(writes: &std::sync::atomic::AtomicUsize) -> usize {
        writes.load(std::sync::atomic::Ordering::SeqCst)
    }

    #[test]
    fn preface_stage_write_failures_follow_grpc_go() {
        runtime().block_on(async {
            let reset = format!("{PREFACE_ERROR}read tcp L->R: read: connection reset by peer");
            // The deterministic form of the harness race: the preface lands,
            // a later write consumes the reset, and the read then sees EOF.
            // Go's reader, whose writes had landed, reads the reset.
            let (mut stream, writes) =
                observed(vec![], true, Some(ECONNRESET), 1, CONNECT_TIMEOUT);
            stream.write_all(CLIENT_PREFACE).await.unwrap();
            stream.write_all(b"settings and headers").await.unwrap();
            stream.write_all(b"more").await.unwrap();
            stream.flush().await.unwrap();
            // The preface, the failing write, and no socket write after it.
            assert_eq!(attempts(&writes), 2);
            let err = stream.read_u8().await.unwrap_err();
            assert_eq!(err.to_string(), reset);
            let status = classify(Status::from_error(Box::new(err)));
            assert_eq!(
                text_error(&status),
                format!("rpc error: code = Unavailable desc = connection error: desc = \"{reset}\"")
            );
            // A broken pipe means the peer closed: Go's reader sees EOF.
            let (mut stream, _) = observed(vec![], true, Some(EPIPE), 1, CONNECT_TIMEOUT);
            stream.write_all(CLIENT_PREFACE).await.unwrap();
            stream.write_all(b"x").await.unwrap();
            let err = stream.read_u8().await.unwrap_err();
            assert_eq!(err.to_string(), format!("{PREFACE_ERROR}EOF"));
            // The read side's own error is the cause it reports.
            let (mut stream, _) = observed(
                vec![Err(io::Error::from_raw_os_error(ECONNRESET))],
                false,
                Some(EPIPE),
                1,
                CONNECT_TIMEOUT,
            );
            stream.write_all(CLIENT_PREFACE).await.unwrap();
            stream.write_all(b"x").await.unwrap();
            let err = stream.read_u8().await.unwrap_err();
            assert_eq!(err.to_string(), reset);
            // Writes fail and the read never becomes ready: bounded by the
            // connect deadline, without further socket writes or queueing.
            let (mut stream, writes) =
                observed(vec![], false, Some(ECONNRESET), 1, Duration::from_millis(100));
            stream.write_all(CLIENT_PREFACE).await.unwrap();
            for _ in 0..100 {
                stream.write_all(b"frame").await.unwrap();
            }
            let start = Instant::now();
            let err = stream.read_u8().await.unwrap_err();
            assert!(start.elapsed() < Duration::from_secs(2));
            assert_eq!(
                err.to_string(),
                format!("{PREFACE_ERROR}read tcp L->R: use of closed network connection")
            );
            assert_eq!(attempts(&writes), 2);
            // A failing write that still carries client preface bytes is
            // grpc-go's own synchronous preface write failure.
            let (mut stream, writes) =
                observed(vec![], true, Some(ECONNRESET), 0, CONNECT_TIMEOUT);
            let err = stream.write_all(CLIENT_PREFACE).await.unwrap_err();
            assert_eq!(
                err.to_string(),
                format!("{WRITE_PREFACE_ERROR}write tcp L->R: write: connection reset by peer")
            );
            assert_eq!(attempts(&writes), 1);
            assert_eq!(
                text_error(&classify(Status::from_error(Box::new(err)))),
                format!(
                    "rpc error: code = Unavailable desc = connection error: desc = \"{WRITE_PREFACE_ERROR}write tcp L->R: write: connection reset by peer\""
                )
            );
            // After the server preface, write errors propagate unchanged.
            let (mut stream, _) = observed(
                vec![Ok(frame(4, 0, 0, &[]))],
                false,
                Some(ECONNRESET),
                1,
                CONNECT_TIMEOUT,
            );
            stream.write_all(CLIENT_PREFACE).await.unwrap();
            let mut first = [0; 9];
            stream.read_exact(&mut first).await.unwrap();
            let err = stream.write_all(b"x").await.unwrap_err();
            assert_eq!(err.raw_os_error(), Some(ECONNRESET));
        });
    }
    fn encoded_head(encoding: Option<&[u8]>) -> Vec<u8> {
        let mut fields: Vec<(&str, &[u8])> =
            vec![(":status", b"200"), ("content-type", b"application/grpc")];
        if let Some(encoding) = encoding {
            fields.push(("grpc-encoding", encoding));
        }
        frame(FRAME_HEADERS, END_HEADERS, 1, &hpack(&fields))
    }

    fn wire_message(flag: u8, body: &[u8], declared: Option<u32>) -> Vec<u8> {
        let mut out = vec![flag];
        out.extend_from_slice(&declared.unwrap_or(body.len() as u32).to_be_bytes());
        out.extend_from_slice(body);
        out
    }

    #[test]
    fn payload_flags_follow_go_check_recv_payload() {
        let gzip = Some(b"gzip".as_slice());
        let unimplemented = || {
            FlightStatus::new(
                12,
                "grpc: Decompressor is not installed for grpc-encoding \"gzip\"",
            )
        };
        let identity =
            || FlightStatus::internal("grpc: compressed flag set with identity or empty encoding");
        let done = || trailer_frame(&[("grpc-status", b"3"), ("grpc-message", b"done")]);
        let data = |bytes: Vec<u8>| frame(FRAME_DATA, 0, 1, &bytes);
        runtime().block_on(async {
            // Announced gzip, flag-0 messages: streams (Go measured).
            let items = run(vec![encoded_head(gzip), empty_message(0), done()]).await;
            assert_eq!(items, [Ok(()), Err(FlightStatus::new(3, "done"))]);
            // Go's checkRecvPayload after the whole body (Go measured).
            for (encoding, flag, body, expected) in [
                (gzip, 1, b"abc".as_slice(), unimplemented()),
                (gzip, 1, b"", unimplemented()),
                (None, 1, b"abc", identity()),
                (Some(b"identity".as_slice()), 1, b"abc", identity()),
                (
                    gzip,
                    2,
                    b"abc",
                    FlightStatus::internal("grpc: received unexpected payload format 2"),
                ),
            ] {
                let items = run(vec![
                    encoded_head(encoding),
                    data(wire_message(flag, body, None)),
                    done(),
                ])
                .await;
                assert_eq!(items, [Err(expected)]);
            }
            // The size gate comes first, whatever the flag (Go measured).
            for flag in [0, 1] {
                let items = run(vec![
                    encoded_head(gzip),
                    data(wire_message(flag, b"", Some(4194305))),
                    done(),
                ])
                .await;
                assert_eq!(
                    items,
                    [Err(FlightStatus::new(
                        8,
                        "grpc: received message larger than max (4194305 vs. 4194304)"
                    ))]
                );
            }
            // An end inside the held message is unexpected EOF (Go measured).
            for partial in [wire_message(1, b"abc", Some(10)), vec![1, 0, 0]] {
                let items = run(vec![encoded_head(gzip), data(partial), done()]).await;
                assert_eq!(items, [Err(FlightStatus::internal("unexpected EOF"))]);
            }
            // A held message split across DATA frames (Go measured).
            let message = wire_message(1, b"abcdef", None);
            let items = run(vec![
                encoded_head(gzip),
                data(message[..4].to_vec()),
                data(message[4..].to_vec()),
                done(),
            ])
            .await;
            assert_eq!(items, [Err(unimplemented())]);
            // Complete messages before the rejected one, even in the same
            // DATA frame, are delivered first.
            let mut both = vec![0, 0, 0, 0, 0];
            both.extend(wire_message(1, b"abc", None));
            let items = run(vec![encoded_head(gzip), data(both), done()]).await;
            assert_eq!(items, [Ok(()), Err(unimplemented())]);
        });
    }

    #[test]
    fn retry_channel_serves_the_next_do_get() {
        runtime().block_on(async {
            let (port, accepted) = raw_connections(vec![
                vec![Step::Send([grpc_ok_head(), goaway(0, 0)].concat())],
                vec![
                    Step::Send(
                        [
                            grpc_ok_head(),
                            empty_message(0),
                            trailers_on(1, &[("grpc-status", b"0")]),
                        ]
                        .concat(),
                    ),
                    Step::Wait(300),
                    Step::Send(
                        [
                            head_on(3, "200", "application/grpc"),
                            frame(FRAME_DATA, 0, 3, &[0, 0, 0, 0, 0]),
                            trailers_on(3, &[("grpc-status", b"3"), ("grpc-message", b"done")]),
                        ]
                        .concat(),
                    ),
                ],
            ])
            .await;
            let mut connection = plaintext_connection(port, CONNECT_TIMEOUT);
            for expected in [
                vec![Ok(())],
                vec![Ok(()), Err(FlightStatus::new(3, "done"))],
            ] {
                let mut stream = connection.do_get().await.unwrap();
                let mut items = Vec::new();
                while let Some(item) = stream.next().await {
                    items.push(item.map(|_| ()));
                }
                assert_eq!(items, expected);
            }
            // The retry's connection carried stream 3: no third dial.
            assert_eq!(accepted.load(std::sync::atomic::Ordering::SeqCst), 2);
        });
    }

    #[test]
    fn empty_data_frames_do_not_reach_h2_limits() {
        let empty = || frame(FRAME_DATA, 0, 1, &[]);
        runtime().block_on(async {
            // 101 empty frames on one stream (Go measured: renders).
            let mut script = vec![grpc_ok_head()];
            script.extend((0..101).map(|_| empty()));
            script.extend([empty_message(0), trailer_frame(&[("grpc-status", b"3")])]);
            let items = run(script).await;
            assert_eq!(items, [Ok(()), Err(FlightStatus::new(3, ""))]);
            // 60 + 60 across two subscriptions on one connection.
            let empties = |stream: u32| -> Vec<u8> {
                (0..60)
                    .flat_map(|_| frame(FRAME_DATA, 0, stream, &[]))
                    .collect()
            };
            let (port, accepted) = raw_connections(vec![vec![
                Step::Send(
                    [
                        grpc_ok_head(),
                        empties(1),
                        empty_message(0),
                        trailers_on(1, &[("grpc-status", b"0")]),
                    ]
                    .concat(),
                ),
                Step::Wait(300),
                Step::Send(
                    [
                        head_on(3, "200", "application/grpc"),
                        empties(3),
                        frame(FRAME_DATA, 0, 3, &[0, 0, 0, 0, 0]),
                        trailers_on(3, &[("grpc-status", b"3")]),
                    ]
                    .concat(),
                ),
            ]])
            .await;
            let mut connection = plaintext_connection(port, CONNECT_TIMEOUT);
            for expected in [vec![Ok(())], vec![Ok(()), Err(FlightStatus::new(3, ""))]] {
                let mut stream = connection.do_get().await.unwrap();
                let mut items = Vec::new();
                while let Some(item) = stream.next().await {
                    items.push(item.map(|_| ()));
                }
                assert_eq!(items, expected);
            }
            assert_eq!(accepted.load(std::sync::atomic::Ordering::SeqCst), 1);
        });
    }

    struct CountingWaker(std::sync::atomic::AtomicUsize);
    impl std::task::Wake for CountingWaker {
        fn wake(self: Arc<Self>) {
            self.wake_by_ref();
        }
        fn wake_by_ref(self: &Arc<Self>) {
            self.0.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        }
    }

    /// Reads to EOF or error with an always-woken task; returns the bytes,
    /// any error, and how many polls yielded `Pending`.
    fn drain(stream: &mut Observed<MockSocket>) -> (Vec<u8>, Option<io::Error>, usize) {
        let waker = std::task::Waker::from(Arc::new(CountingWaker(Default::default())));
        let mut cx = Context::from_waker(&waker);
        let (mut out, mut pendings) = (Vec::new(), 0);
        for _ in 0..100_000 {
            let mut space = [0; 4096];
            let mut buf = ReadBuf::new(&mut space);
            match Pin::new(&mut *stream).poll_read(&mut cx, &mut buf) {
                Poll::Ready(Ok(())) if buf.filled().is_empty() => return (out, None, pendings),
                Poll::Ready(Ok(())) => out.extend_from_slice(buf.filled()),
                Poll::Ready(Err(err)) => return (out, Some(err), pendings),
                Poll::Pending => pendings += 1,
            }
        }
        panic!("the stream did not finish");
    }

    fn filtered(chunks: Vec<io::Result<Vec<u8>>>) -> (Vec<u8>, Option<io::Error>, usize) {
        let (mut stream, _) = observed(chunks, true, None, 0, CONNECT_TIMEOUT);
        drain(&mut stream)
    }

    #[test]
    fn empty_data_filter_is_exact_and_bounded() {
        runtime().block_on(async {
            let settings = frame(4, 0, 0, &[0; 6]);
            let empty = frame(FRAME_DATA, 0, 1, &[]);
            let data = frame(FRAME_DATA, 0, 1, b"ab");
            let expected = [settings.clone(), data.clone()].concat();
            // Dropped, whole or split at every header position.
            let whole = [settings.clone(), empty.clone(), data.clone()].concat();
            assert_eq!(filtered(vec![Ok(whole)]).0, expected);
            for split in 1..9 {
                let first = [settings.clone(), empty[..split].to_vec()].concat();
                let second = [empty[split..].to_vec(), data.clone()].concat();
                let (out, err, _) = filtered(vec![Ok(first), Ok(second)]);
                assert_eq!(
                    (out, err.is_none()),
                    (expected.clone(), true),
                    "empty split {split}"
                );
                // A kept header split the same way passes unchanged.
                let first = [settings.clone(), data[..split].to_vec()].concat();
                let (out, _, _) = filtered(vec![Ok(first), Ok(data[split..].to_vec())]);
                assert_eq!(out, expected, "kept split {split}");
                // Byte by byte.
                let bytes: Vec<_> = [settings.clone(), empty.clone(), data.clone()]
                    .concat()
                    .into_iter()
                    .map(|b| Ok(vec![b]))
                    .collect();
                assert_eq!(filtered(bytes).0, expected);
            }
            // Withheld header bytes reach h2 at EOF; an error returns as is.
            let truncated = [settings.clone(), data[..5].to_vec()].concat();
            let (out, err, _) = filtered(vec![Ok(truncated.clone())]);
            assert_eq!((out, err.is_none()), (truncated, true));
            let (out, err, _) = filtered(vec![
                Ok([settings.clone(), data[..4].to_vec()].concat()),
                Err(io::Error::from_raw_os_error(ECONNRESET)),
            ]);
            assert_eq!(out, settings);
            assert_eq!(err.unwrap().raw_os_error(), Some(ECONNRESET));
            // Untouched: the first frame, PADDED, END_STREAM and stream 0.
            let kept = [
                empty.clone(),
                settings.clone(),
                // Genuine padded empty DATA (pad length 0, then 1 byte of
                // padding), and the malformed length-0 PADDED form.
                frame(FRAME_DATA, FLAG_PADDED, 1, &[0]),
                frame(FRAME_DATA, FLAG_PADDED, 1, &[1, 0]),
                frame(FRAME_DATA, FLAG_PADDED, 1, &[]),
                frame(FRAME_DATA, END_STREAM, 1, &[]),
                frame(FRAME_DATA, 0, 0, &[]),
                frame(FRAME_HEADERS, END_HEADERS, 1, &[]),
            ]
            .concat();
            assert_eq!(filtered(vec![Ok(kept.clone())]).0, kept);
            // Inside an open header block nothing is dropped, whole or split;
            // after END_HEADERS the filter applies again.
            let open = [
                settings.clone(),
                frame(FRAME_HEADERS, 0, 1, b"h"),
                empty.clone(),
                frame(FRAME_CONTINUATION, 0, 1, b"c"),
                empty.clone(),
                frame(FRAME_CONTINUATION, FLAG_END_HEADERS, 1, b"c"),
            ]
            .concat();
            let after = [open.clone(), empty.clone(), data.clone()].concat();
            let expected_open = [open.clone(), data.clone()].concat();
            assert_eq!(filtered(vec![Ok(after.clone())]).0, expected_open);
            for split in 1..after.len() {
                let (out, err, _) = filtered(vec![
                    Ok(after[..split].to_vec()),
                    Ok(after[split..].to_vec()),
                ]);
                assert_eq!(
                    (out, err.is_none()),
                    (expected_open.clone(), true),
                    "split {split}"
                );
            }
            let push = [
                settings.clone(),
                frame(FRAME_PUSH_PROMISE, 0, 1, &[0, 0, 0, 2]),
                empty.clone(),
            ]
            .concat();
            assert_eq!(filtered(vec![Ok(push.clone())]).0, push);
            // HEADERS with END_HEADERS leaves no block open.
            let closed = [
                settings.clone(),
                frame(FRAME_HEADERS, FLAG_END_HEADERS, 1, b"h"),
                empty.clone(),
                data.clone(),
            ]
            .concat();
            let expected_closed = [
                settings.clone(),
                frame(FRAME_HEADERS, FLAG_END_HEADERS, 1, b"h"),
                data.clone(),
            ]
            .concat();
            assert_eq!(filtered(vec![Ok(closed)]).0, expected_closed);
            // The very first frame is never dropped, even inside the rules.
            let first = [empty.clone(), settings.clone()].concat();
            assert_eq!(filtered(vec![Ok(first.clone())]).0, first);
            // A zero-capacity read touches nothing.
            let (mut stream, _) =
                observed(vec![Ok(settings.clone())], true, None, 0, CONNECT_TIMEOUT);
            let waker = std::task::Waker::noop();
            let mut cx = Context::from_waker(waker);
            let mut buf = ReadBuf::new(&mut []);
            assert!(matches!(
                Pin::new(&mut stream).poll_read(&mut cx, &mut buf),
                Poll::Ready(Ok(()))
            ));
            assert_eq!(stream.inner.reads.len(), 1);
            // An always-ready run of empty frames yields within the budget
            // (woken, never an empty Ok) and then makes progress.
            let mut chunks = vec![Ok(settings.clone())];
            chunks.extend((0..1000).map(|_| Ok(empty.clone())));
            chunks.push(Ok(data.clone()));
            let (out, err, pendings) = filtered(chunks);
            assert_eq!((out, err.is_none()), (expected, true));
            assert!(pendings >= 1000 / FILTER_READ_BUDGET, "{pendings}");
        });
    }
    #[test]
    fn empty_data_inside_a_header_block_still_fails_in_h2() {
        runtime().block_on(async {
            // HEADERS without END_HEADERS, an empty DATA, then the final
            // CONTINUATION: an HTTP/2 protocol error (x/net checkFrameOrder,
            // h2 FramedRead). It must not be accepted as a valid stream.
            let block_start = hpack(&[(":status", b"200")]);
            let block_end = hpack(&[("content-type", b"application/grpc")]);
            let items = run(vec![
                frame(FRAME_HEADERS, 0, 1, &block_start),
                frame(FRAME_DATA, 0, 1, &[]),
                frame(FRAME_CONTINUATION, FLAG_END_HEADERS, 1, &block_end),
                empty_message(0),
                trailer_frame(&[("grpc-status", b"3"), ("grpc-message", b"done")]),
            ])
            .await;
            assert_eq!(items.len(), 1, "{items:?}");
            assert_eq!(
                items[0].as_ref().unwrap_err().code(),
                Code::Unavailable as u32
            );
            // Control: the same block without the interleaved DATA is valid.
            let items = run(vec![
                frame(FRAME_HEADERS, 0, 1, &block_start),
                frame(FRAME_CONTINUATION, FLAG_END_HEADERS, 1, &block_end),
                empty_message(0),
                trailer_frame(&[("grpc-status", b"3"), ("grpc-message", b"done")]),
            ])
            .await;
            assert_eq!(items, [Ok(()), Err(FlightStatus::new(3, "done"))]);
        });
    }

    /// CL-3 characterization: h2 0.4.20 counts padded DATA frames whose
    /// payload is empty toward its 100-per-connection lifetime cap; they keep
    /// flow-control weight, so the filter leaves them to h2 (owner decision).
    #[test]
    fn padded_empty_data_keeps_h2_lifetime_cap() {
        let done = || trailer_frame(&[("grpc-status", b"3"), ("grpc-message", b"done")]);
        runtime().block_on(async {
            // Valid padded empty DATA: length 1, pad length 0; and with one
            // byte of nonzero-length padding: length 2, pad length 1.
            for padded in [vec![0u8], vec![1, 0]] {
                for (count, accepted) in [(100, true), (101, false)] {
                    let mut script = vec![grpc_ok_head(), empty_message(0)];
                    script.extend((0..count).map(|_| frame(FRAME_DATA, FLAG_PADDED, 1, &padded)));
                    script.extend([empty_message(0), done()]);
                    let items = run(script).await;
                    if accepted {
                        assert_eq!(
                            items,
                            [Ok(()), Ok(()), Err(FlightStatus::new(3, "done"))],
                            "{count} x {padded:?}"
                        );
                    } else {
                        // h2's library GOAWAY(ENHANCE_YOUR_CALM): Unavailable
                        // after the first message, so the session would
                        // reconnect and lose the rest of this subscription.
                        assert_eq!(items.len(), 2, "{count} x {padded:?}: {items:?}");
                        assert_eq!(items[0], Ok(()));
                        assert_eq!(
                            items[1].as_ref().unwrap_err().code(),
                            Code::Unavailable as u32
                        );
                    }
                }
            }
        });
    }
}
