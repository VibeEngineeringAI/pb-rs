//! Authenticated HTTP operations with owned streaming bodies and one deadline.
pub use crate::transport::TransportError;
use crate::transport::{Response as RawResponse, Transport as Client};
use crate::{
    auth::{Auth, AuthError, orchestrator_headers},
    config::{ConfigError, ProfileRecord, ProfileStore},
};
use bytes::Bytes;
use http::{
    Method, StatusCode,
    header::{
        ACCEPT, AUTHORIZATION, CONTENT_TYPE, COOKIE, HeaderMap, HeaderValue, LOCATION, REFERER,
    },
};
use serde::{Deserialize, de::DeserializeOwned};
use std::{
    error::Error,
    fmt,
    sync::{
        Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tokio::{
    sync::Mutex as AsyncMutex,
    time::{Instant, timeout_at},
};
use url::Url;

/// `Url` supplies origin metadata while the original URI supplies wire paths.
/// Parsing a Url alone would collapse percent-encoded dot path segments.
#[derive(Clone, Debug)]
struct Endpoint {
    url: Url,
    raw: String,
}
impl std::ops::Deref for Endpoint {
    type Target = Url;
    fn deref(&self) -> &Url {
        &self.url
    }
}
impl fmt::Display for Endpoint {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.raw)
    }
}
impl Endpoint {
    fn new(raw: &str) -> Result<Self, ApiError> {
        let url =
            Url::parse(raw).map_err(|e| ApiError::InvalidUrl(format!("parse {raw:?}: {e}")))?;
        Ok(Self {
            url,
            raw: raw.into(),
        })
    }
    fn authority_end(&self) -> usize {
        let start = self.raw.find("//").map_or(0, |i| i + 2);
        self.raw[start..]
            .find(['/', '?', '#'])
            .map(|i| start + i)
            .unwrap_or(self.raw.len())
    }
    fn join(&self, reference: &str) -> Result<Self, ApiError> {
        let resolved =
            crate::url_reference::resolve(&self.raw, reference).map_err(ApiError::InvalidUrl)?;
        Self::new(&resolved)
    }
    fn referer(&self) -> String {
        let start = self.raw.find("//").map_or(0, |i| i + 2);
        let end = self.authority_end();
        if let Some(at) = self.raw[start..end].rfind('@') {
            format!("{}{}", &self.raw[..start], &self.raw[start + at + 1..])
        } else {
            self.raw.clone()
        }
    }
}

pub const METRICS_TIMEOUT: Duration = Duration::from_secs(120);
pub const STANDARD_TIMEOUT: Duration = Duration::from_secs(60);
pub const PRISM_TIMEOUT: Duration = Duration::from_secs(15);
pub const CLOUD_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug)]
pub enum ApiError {
    Auth(AuthError),
    InvalidUrl(String),
    Network(TransportError),
    Request {
        method: Method,
        url: String,
        source: Box<ApiError>,
    },
    Context {
        message: String,
        source: Box<ApiError>,
    },
    Timeout {
        method: Method,
        url: String,
        body: bool,
    },
    BodyCanceled,
    HttpStatus {
        status: u16,
        reason: String,
        body: String,
        context: String,
    },
    Decode {
        source: serde_json::Error,
        context: String,
    },
    Refresh(Box<ApiError>),
    Persistence(ConfigError),
    InvalidResponse(String),
    Redirect(String),
}
impl ApiError {
    pub fn is_connection_reset(&self) -> bool {
        matches!(self.root_cause(), Self::Network(error) if error.is_connection_reset())
    }
    pub fn is_request_error(&self) -> bool {
        match self {
            Self::Request { .. } => true,
            Self::Network(error) => !error.is_body(),
            Self::Context { source, .. } | Self::Refresh(source) => source.is_request_error(),
            _ => false,
        }
    }
    pub fn root_cause(&self) -> &Self {
        match self {
            Self::Refresh(source) | Self::Request { source, .. } | Self::Context { source, .. } => {
                source.root_cause()
            }
            _ => self,
        }
    }
    pub fn is_timeout(&self) -> bool {
        match self {
            Self::Timeout { .. } | Self::BodyCanceled => true,
            Self::Network(e) => e.is_timeout(),
            Self::Refresh(e)
            | Self::Request { source: e, .. }
            | Self::Context { source: e, .. } => e.is_timeout(),
            _ => false,
        }
    }
    pub fn status_code(&self) -> Option<u16> {
        match self {
            Self::HttpStatus { status, .. } => Some(*status),
            Self::Refresh(e)
            | Self::Request { source: e, .. }
            | Self::Context { source: e, .. } => e.status_code(),
            _ => None,
        }
    }
}
impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Auth(e) => e.fmt(f), Self::InvalidUrl(e) | Self::InvalidResponse(e) | Self::Redirect(e) => f.write_str(e),
            Self::Network(e) => {
                e.fmt(f)
            }
            Self::Request { method, url, source } => write!(f, "{} {:?}: {source}", go_method(method), redact_url_password(url)),
            Self::Context { message, source } => write!(f, "{message}: {source}"),
            Self::Timeout { body: true, .. } => f.write_str("context deadline exceeded (Client.Timeout or context cancellation while reading body)"),
            Self::BodyCanceled => f.write_str("net/http: request canceled (Client.Timeout or context cancellation while reading body)"),
            Self::Timeout { method, url, .. } => write!(f, "{} {:?}: context deadline exceeded (Client.Timeout exceeded while awaiting headers)", go_method(method), redact_url_password(url)),
            Self::HttpStatus { status, reason, body, context } => {
                if context.is_empty() { write!(f, "HTTP {status}: {}", body.trim()) }
                else { write!(f, "{context}: {status} {reason}: {}", body.trim()) }
            }
            Self::Decode { source, context } => {
                if !context.is_empty() { write!(f, "{context}: ")?; }
                if source.is_eof() { f.write_str("unexpected end of JSON input") } else { source.fmt(f) }
            }
            Self::Refresh(e) => write!(f, "cloud session refresh failed: {e}"),
            Self::Persistence(e) => write!(f, "failed to persist refreshed cloud tokens: {e}"),
        }
    }
}
impl Error for ApiError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Auth(e) => Some(e),
            Self::Network(e) => Some(e),
            Self::Decode { source, .. } => Some(source),
            Self::Refresh(e)
            | Self::Request { source: e, .. }
            | Self::Context { source: e, .. } => Some(e.as_ref()),
            Self::Persistence(e) => Some(e),
            _ => None,
        }
    }
}
impl From<AuthError> for ApiError {
    fn from(e: AuthError) -> Self {
        Self::Auth(e)
    }
}
impl From<TransportError> for ApiError {
    fn from(e: TransportError) -> Self {
        Self::Network(e)
    }
}

#[derive(Debug)]
pub struct ApiResponse {
    pub status: u16,
    pub reason: String,
    pub body: Vec<u8>,
}
impl ApiResponse {
    pub fn status_text(&self) -> String {
        format!("{} {}", self.status, self.reason)
    }
    pub fn json<T: DeserializeOwned>(&self) -> Result<T, ApiError> {
        serde_json::from_slice(&self.body).map_err(|source| ApiError::Decode {
            source,
            context: String::new(),
        })
    }
    pub fn status_error(&self) -> ApiError {
        ApiError::HttpStatus {
            status: self.status,
            reason: self.reason.clone(),
            body: String::from_utf8_lossy(&self.body).into_owned(),
            context: String::new(),
        }
    }
}

/// A replayable small request. Preparation performs no network operations;
/// the command can start its spinner after constructing this value.
#[derive(Debug)]
pub struct PreparedRequest {
    method: Method,
    raw_endpoint: String,
    headers: HeaderMap,
    body: Option<Vec<u8>>,
    timeout: Duration,
    metrics: bool,
}

/// The final response owns its body and retains the deadline established by
/// sending the request. Dropping it cancels the unread body without draining.
pub struct StreamingResponse {
    pub status: u16,
    pub reason: String,
    response: Option<RawResponse>,
    deadline: Instant,
    method: Method,
    url: String,
}
impl fmt::Debug for StreamingResponse {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("StreamingResponse")
            .field("status", &self.status)
            .field("reason", &self.reason)
            .finish_non_exhaustive()
    }
}
impl StreamingResponse {
    pub(crate) fn deadline(&self) -> Instant {
        self.deadline
    }
    pub(crate) fn cancel_body(&mut self) -> ApiError {
        self.response.take();
        ApiError::BodyCanceled
    }
    pub async fn chunk(&mut self) -> Result<Option<Bytes>, ApiError> {
        self.chunk_limit(8192).await
    }
    /// Read at most `maximum` decompressed bytes, retaining any frame remainder
    /// for the next read. Zero requests no data and does not indicate EOF.
    pub async fn chunk_limit(&mut self, maximum: usize) -> Result<Option<Bytes>, ApiError> {
        if maximum == 0 {
            return Ok(Some(Bytes::new()));
        }
        // timeout_at polls its inner future first. A ready Hyper frame or
        // decompressor buffer would otherwise bypass an expired deadline.
        if Instant::now() >= self.deadline || self.response.is_none() {
            return Err(self.cancel_body());
        }
        let result = timeout_at(
            self.deadline,
            self.response
                .as_mut()
                .expect("body checked")
                .chunk_limit(maximum),
        )
        .await;
        match result {
            Ok(chunk) => chunk.map_err(ApiError::Network),
            Err(_) => {
                self.response.take();
                Err(ApiError::Timeout {
                    method: self.method.clone(),
                    url: self.url.clone(),
                    body: true,
                })
            }
        }
    }
    /// Stop at the byte limit without probing or draining an unread tail. Error
    /// previews request 65,537 bytes to distinguish an exact-size body from a
    /// truncated one while keeping only 65,536 bytes for rendering.
    pub async fn read_limited(&mut self, maximum: usize) -> Result<Vec<u8>, ApiError> {
        let mut bytes = Vec::new();
        while bytes.len() < maximum {
            let Some(chunk) = self.chunk_limit((maximum - bytes.len()).min(8192)).await? else {
                break;
            };
            bytes.extend_from_slice(&chunk);
        }
        Ok(bytes)
    }
    pub async fn collect(mut self) -> Result<ApiResponse, ApiError> {
        let mut body = Vec::new();
        while let Some(chunk) = self.chunk().await? {
            body.extend_from_slice(&chunk);
        }
        Ok(ApiResponse {
            status: self.status,
            reason: self.reason,
            body,
        })
    }
}

/// One command/session owns a client; concurrent requests share only token state
/// and the refresh gate. Construction creates no runtime and starts no requests.
pub struct ApiClient {
    transport: Client,
    metrics_transport: std::sync::OnceLock<Client>,
    profile: Mutex<ProfileRecord>,
    store: ProfileStore,
    refresh_gate: AsyncMutex<()>,
    orchestrator_token: String,
}
impl fmt::Debug for ApiClient {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ApiClient").finish_non_exhaustive()
    }
}
impl ApiClient {
    pub fn new(profile: ProfileRecord, store: ProfileStore) -> Result<Self, ApiError> {
        Self::with_orchestrator_token(profile, store, release_orchestrator_token())
    }
    pub fn with_orchestrator_token(
        profile: ProfileRecord,
        store: ProfileStore,
        token: impl Into<String>,
    ) -> Result<Self, ApiError> {
        Ok(Self {
            transport: transport()?,
            metrics_transport: std::sync::OnceLock::new(),
            profile: Mutex::new(profile),
            store,
            refresh_gate: AsyncMutex::new(()),
            orchestrator_token: token.into(),
        })
    }
    pub fn profile(&self) -> ProfileRecord {
        self.profile.lock().expect("profile mutex poisoned").clone()
    }

    pub async fn standard(
        &self,
        method: Method,
        path: &str,
        body: Option<Vec<u8>>,
    ) -> Result<ApiResponse, ApiError> {
        self.request_with_policy(method, &["api/v1/", path], body, STANDARD_TIMEOUT, true)
            .await
    }
    pub async fn prism(
        &self,
        method: Method,
        path: &str,
        body: Option<Vec<u8>>,
    ) -> Result<ApiResponse, ApiError> {
        self.request(method, &["api/prism/v1/", path], body, PRISM_TIMEOUT)
            .await
    }
    pub async fn request(
        &self,
        method: Method,
        paths: &[&str],
        body: Option<Vec<u8>>,
        timeout: Duration,
    ) -> Result<ApiResponse, ApiError> {
        self.request_with_policy(method, paths, body, timeout, false)
            .await
    }
    /// Prepare a standard endpoint for a streamed operation.
    pub fn prepare_standard(
        &self,
        method: Method,
        path: &str,
        body: Option<Vec<u8>>,
    ) -> Result<PreparedRequest, ApiError> {
        self.prepare_with_policy(method, &["api/v1/", path], body, STANDARD_TIMEOUT, true)
    }
    pub fn prepare(
        &self,
        method: Method,
        paths: &[&str],
        body: Option<Vec<u8>>,
        timeout: Duration,
    ) -> Result<PreparedRequest, ApiError> {
        self.prepare_with_policy(method, paths, body, timeout, false)
    }
    /// Metrics uses the Go custom transport and url.Values query encoding.
    pub fn prepare_metrics(
        &self,
        path: &str,
        pairs: &[(String, String)],
    ) -> Result<PreparedRequest, ApiError> {
        let profile = self.profile();
        let mut raw_endpoint = append_url_string(&profile.url, &[path])?;
        if !pairs.is_empty() {
            // Go appends to the serialized JoinPath result, including an
            // existing query or fragment. Preserve that observable quirk.
            raw_endpoint.push('?');
            raw_endpoint.push_str(&encode_query_pairs(pairs));
        }
        Ok(PreparedRequest {
            method: Method::GET,
            raw_endpoint,
            headers: Auth::resolve(&profile)?.headers()?,
            body: None,
            timeout: METRICS_TIMEOUT,
            metrics: true,
        })
    }
    fn prepare_with_policy(
        &self,
        method: Method,
        paths: &[&str],
        body: Option<Vec<u8>>,
        timeout: Duration,
        ignore_join_error: bool,
    ) -> Result<PreparedRequest, ApiError> {
        let profile = self.profile();
        let raw_endpoint = match append_url_string(&profile.url, paths) {
            Ok(endpoint) => endpoint,
            Err(_) if ignore_join_error => String::new(),
            Err(error) => return Err(error),
        };
        let mut headers = Auth::resolve(&profile)?.headers()?;
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        if ignore_join_error {
            debug_headers(&method, &raw_endpoint, &headers);
        }
        Ok(PreparedRequest {
            method,
            raw_endpoint,
            headers,
            body,
            timeout,
            metrics: false,
        })
    }
    /// Start one absolute deadline spanning redirects, OAuth refresh/replay,
    /// response headers, and all subsequent body reads.
    pub async fn send_streaming(
        &self,
        request: PreparedRequest,
    ) -> Result<StreamingResponse, ApiError> {
        let PreparedRequest {
            method,
            raw_endpoint,
            headers,
            body,
            timeout,
            metrics,
        } = request;
        let endpoint = checked_http_url(&raw_endpoint).map_err(|source| ApiError::Request {
            method: method.clone(),
            url: raw_endpoint.clone(),
            source: Box::new(source),
        })?;
        let deadline = Instant::now() + timeout;
        let headers_received = AtomicBool::new(false);
        let timeout_url = Mutex::new(endpoint.to_string());
        let selected_transport = if metrics {
            if self.metrics_transport.get().is_none() {
                let _ = self.metrics_transport.set(Client::metrics()?);
            }
            self.metrics_transport
                .get()
                .expect("metrics transport initialized")
        } else {
            &self.transport
        };
        let call = self.follow_redirects(
            selected_transport,
            method.clone(),
            endpoint.clone(),
            headers,
            body,
            &headers_received,
            &timeout_url,
        );
        let response = timeout_at(deadline, call)
            .await
            .map_err(|_| ApiError::Timeout {
                method: method.clone(),
                url: timeout_url
                    .lock()
                    .expect("timeout URL mutex poisoned")
                    .clone(),
                body: headers_received.load(Ordering::Relaxed),
            })?
            .map_err(|source| {
                if matches!(&source, ApiError::Request { .. }) {
                    source
                } else {
                    ApiError::Request {
                        method: method.clone(),
                        url: raw_endpoint.clone(),
                        source: Box::new(source),
                    }
                }
            })?;
        Ok(StreamingResponse {
            status: response.status().as_u16(),
            reason: response.reason().to_owned(),
            response: Some(response),
            deadline,
            method,
            url: raw_endpoint,
        })
    }
    async fn request_with_policy(
        &self,
        method: Method,
        paths: &[&str],
        body: Option<Vec<u8>>,
        timeout: Duration,
        ignore_join_error: bool,
    ) -> Result<ApiResponse, ApiError> {
        let request = self.prepare_with_policy(method, paths, body, timeout, ignore_join_error)?;
        self.send_streaming(request).await?.collect().await
    }

    // Redirect state and deadline observations stay explicit across both policies.
    #[allow(clippy::too_many_arguments)]
    async fn follow_redirects(
        &self,
        transport: &Client,
        method: Method,
        endpoint: Endpoint,
        headers: HeaderMap,
        body: Option<Vec<u8>>,
        headers_received: &AtomicBool,
        timeout_url: &Mutex<String>,
    ) -> Result<RawResponse, ApiError> {
        let original_method = method.clone();
        let mut current_url = endpoint.clone();
        let mut current_method = method;
        let mut current_body = body;
        let mut current_headers = headers.clone();
        let mut stripped = false;
        for hop in 0..10 {
            *timeout_url.lock().expect("timeout URL mutex poisoned") = current_url.to_string();
            headers_received.store(false, Ordering::Relaxed);
            let response = self
                .send_authenticated(
                    transport,
                    &current_method,
                    &current_url,
                    &current_headers,
                    &current_body,
                )
                .await
                .map_err(|source| ApiError::Request {
                    method: original_method.clone(),
                    url: current_url.to_string(),
                    source: Box::new(source),
                })?;
            let status = response.status();
            if !matches!(status.as_u16(), 301 | 302 | 303 | 307 | 308)
                || response.headers().get(LOCATION).is_none()
            {
                headers_received.store(true, Ordering::Relaxed);
                return Ok(response);
            }
            let location = crate::url_reference::location(response.headers()[LOCATION].as_bytes());
            if location.is_empty() {
                headers_received.store(true, Ordering::Relaxed);
                return Ok(response);
            }
            let next_url = current_url.join(&location).map_err(|e| ApiError::Request {
                method: original_method.clone(),
                url: current_url.to_string(),
                source: Box::new(ApiError::InvalidUrl(format!(
                    "failed to parse Location header {location:?}: {e}"
                ))),
            })?;
            if hop == 9 {
                return Err(ApiError::Request {
                    method: original_method.clone(),
                    url: location,
                    source: Box::new(ApiError::Redirect("stopped after 10 redirects".into())),
                });
            }
            drain_redirect(response).await;
            if matches!(status.as_u16(), 301..=303) {
                if current_method != Method::GET && current_method != Method::HEAD {
                    current_method = Method::GET;
                }
                current_body = None;
            }
            // Go 1.25 copies headers from the initial request. Its custom
            // x-api-key and tenant headers also survive cross-host redirects.
            current_headers = headers.clone();
            stripped |= !redirect_credentials_allowed(&endpoint, &next_url);
            if stripped {
                for header in [AUTHORIZATION, COOKIE, http::header::WWW_AUTHENTICATE] {
                    current_headers.remove(header);
                }
            }
            if !(current_url.scheme() == "https" && next_url.scheme() == "http")
                && let Ok(value) = HeaderValue::from_str(&current_url.referer())
            {
                current_headers.insert(REFERER, value);
            }
            current_url = next_url;
        }
        unreachable!("redirect loop always returns")
    }

    async fn send_authenticated(
        &self,
        transport: &Client,
        method: &Method,
        endpoint: &Endpoint,
        headers: &HeaderMap,
        body: &Option<Vec<u8>>,
    ) -> Result<RawResponse, ApiError> {
        let response = send_raw(transport, method, endpoint, headers, body).await?;
        let profile = self.profile();
        if response.status() != StatusCode::UNAUTHORIZED
            || !same_origin_raw(&profile.url, &endpoint.raw)
            || profile.refresh_token.is_empty()
            || profile.orchestrator_url.is_empty()
            || !matches!(Auth::resolve(&profile), Ok(Auth::CloudOAuth { .. }))
        {
            return Ok(response);
        }
        let _gate = self.refresh_gate.lock().await;
        let profile = self.profile();
        let request_token = headers
            .get(COOKIE)
            .and_then(|v| v.to_str().ok())
            .and_then(session_cookie)
            .unwrap_or_default();
        if request_token == profile.session_token {
            self.refresh(&profile)
                .await
                .map_err(|error| ApiError::Refresh(Box::new(error)))?;
        }
        let mut response = response;
        while response.chunk().await?.is_some() {}
        let mut retry_headers = headers.clone();
        retry_headers.remove(COOKIE);
        retry_headers.extend(Auth::resolve(&self.profile())?.headers()?);
        // Exactly one replay, deliberately through the unwrapped transport.
        send_raw(transport, method, endpoint, &retry_headers, body).await
    }

    async fn refresh(&self, profile: &ProfileRecord) -> Result<(), ApiError> {
        let raw_endpoint =
            append_url_string(&profile.orchestrator_url, &["api/v1/cli/oauth/token"])?;
        let mut headers = orchestrator_headers(&self.orchestrator_token)?;
        let endpoint = checked_http_url(&raw_endpoint)?;
        headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        let payload = serde_json::to_vec(&serde_json::json!({"grant_type":"refresh_token","refresh_token":profile.refresh_token})).expect("string payload serializes");
        let response = send_raw(
            &self.transport,
            &Method::POST,
            &endpoint,
            &headers,
            &Some(payload),
        )
        .await?;
        let response = buffer_response(response).await?;
        if !(200..300).contains(&response.status) {
            return Err(ApiError::HttpStatus {
                status: response.status,
                reason: response.reason,
                body: String::from_utf8_lossy(&response.body).into_owned(),
                context: "cloud session refresh failed".into(),
            });
        }
        let result = cloud_object(&response.body, "cloudRefreshResponse", "")?;
        validate_cloud_fields(
            &result,
            "cloudRefreshResponse",
            "",
            &["access_token", "refresh_token"],
            &[],
        )?;
        let access_token = cloud_string(&result, "access_token", "cloudRefreshResponse", "")?;
        let refresh_token = cloud_string(&result, "refresh_token", "cloudRefreshResponse", "")?;
        if access_token.is_empty() || refresh_token.is_empty() {
            return Err(ApiError::InvalidResponse(
                "cloud session refresh response missing access_token or refresh_token".into(),
            ));
        }
        {
            let mut session = self.profile.lock().expect("profile mutex poisoned");
            session.session_token = access_token.clone();
            session.refresh_token = refresh_token.clone();
        }
        self.store
            .update_cloud_tokens(&profile.refresh_token, &access_token, &refresh_token)
            .map_err(ApiError::Persistence)
    }
}

fn transport() -> Result<Client, ApiError> {
    Ok(Client::new()?)
}
fn release_orchestrator_token() -> &'static str {
    option_env!("PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN").unwrap_or("")
}
async fn send_raw(
    client: &Client,
    method: &Method,
    endpoint: &Endpoint,
    headers: &HeaderMap,
    body: &Option<Vec<u8>>,
) -> Result<RawResponse, ApiError> {
    Ok(client.send(method, &endpoint.raw, headers, body).await?)
}
async fn buffer_response(response: RawResponse) -> Result<ApiResponse, ApiError> {
    let status = response.status().as_u16();
    let reason = response.reason().to_owned();
    Ok(ApiResponse {
        status,
        reason,
        body: response.bytes().await?.to_vec(),
    })
}
async fn drain_redirect(mut response: RawResponse) {
    if response
        .content_length()
        .is_some_and(|length| length > 2048)
    {
        return;
    }
    let mut drained = 0;
    while let Ok(Some(chunk)) = response.chunk_limit(2048 - drained).await {
        drained += chunk.len();
        if drained >= 2048 {
            break;
        }
    }
}
fn go_method(method: &Method) -> String {
    let raw = method.as_str();
    format!("{}{}", &raw[..1], raw[1..].to_ascii_lowercase())
}
fn redact_url_password(raw: &str) -> String {
    let Some(authority_start) = raw.find("//").map(|index| index + 2) else {
        return raw.into();
    };
    let authority_end = raw[authority_start..]
        .find(['/', '?', '#'])
        .map(|index| authority_start + index)
        .unwrap_or(raw.len());
    let authority = &raw[authority_start..authority_end];
    let Some(at) = authority.rfind('@') else {
        return raw.into();
    };
    let Some(colon) = authority[..at].find(':') else {
        return raw.into();
    };
    format!(
        "{}***{}",
        &raw[..authority_start + colon + 1],
        &raw[authority_start + at..]
    )
}
fn same_origin_raw(a: &str, b: &str) -> bool {
    fn authority(raw: &str) -> &str {
        let Some(start) = raw.find("//").map(|i| i + 2) else {
            return "";
        };
        raw[start..]
            .split(['/', '?', '#'])
            .next()
            .unwrap_or_default()
            .rsplit('@')
            .next()
            .unwrap_or_default()
    }
    url_scheme(a)
        .unwrap_or_default()
        .eq_ignore_ascii_case(url_scheme(b).unwrap_or_default())
        && authority(a).eq_ignore_ascii_case(authority(b))
}
fn redirect_credentials_allowed(initial: &Url, destination: &Url) -> bool {
    let initial = initial.host_str().unwrap_or_default();
    let destination = destination.host_str().unwrap_or_default();
    destination == initial
        || (!destination.contains([':', '%'])
            && destination
                .strip_suffix(initial)
                .is_some_and(|prefix| prefix.ends_with('.')))
}
fn session_cookie(raw: &str) -> Option<String> {
    raw.split(';').find_map(|cookie| {
        cookie
            .trim()
            .strip_prefix("session=")
            .map(|value| value.trim_matches('"').into())
    })
}

/// Append path elements rather than resolve a relative reference: Go JoinPath
/// retains prefixes, queries and fragments and cleans literal dots/slashes.
pub fn append_url(base: &str, paths: &[&str]) -> Result<Url, ApiError> {
    let raw = append_url_string(base, paths)?;
    Url::parse(&raw).map_err(|e| ApiError::InvalidUrl(format!("parse {raw:?}: {e}")))
}
fn url_scheme(raw: &str) -> Option<&str> {
    let (scheme, _) = raw.split_once(':')?;
    if scheme.is_empty()
        || !scheme.as_bytes()[0].is_ascii_alphabetic()
        || !scheme
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'+' | b'-' | b'.'))
    {
        return None;
    }
    Some(scheme)
}
fn checked_http_url(raw: &str) -> Result<Endpoint, ApiError> {
    let scheme = url_scheme(raw).unwrap_or_default().to_ascii_lowercase();
    if !matches!(scheme.as_str(), "http" | "https") {
        return Err(ApiError::InvalidResponse(format!(
            "unsupported protocol scheme {scheme:?}"
        )));
    }
    let rest = &raw[scheme.len() + 1..];
    let host = rest
        .strip_prefix("//")
        .map(|authority| {
            authority
                .split(['/', '?', '#'])
                .next()
                .unwrap_or_default()
                .rsplit('@')
                .next()
                .unwrap_or_default()
        })
        .unwrap_or_default();
    if host.is_empty() {
        return Err(ApiError::InvalidResponse(
            "http: no Host in request URL".into(),
        ));
    }
    if let Some((_, port)) = host.rsplit_once(':')
        && port.bytes().all(|b| b.is_ascii_digit())
        && !port.is_empty()
        && port.parse::<u16>().is_err()
    {
        return Err(ApiError::InvalidResponse(format!(
            "dial tcp: address {port}: invalid port"
        )));
    }
    Endpoint::new(raw)
}
fn append_url_string(base: &str, paths: &[&str]) -> Result<String, ApiError> {
    let parse_error = |message: String| ApiError::InvalidUrl(format!("parse {base:?}: {message}"));
    if base.bytes().any(|b| b < 32 || b == 127) {
        return Err(parse_error(
            "net/url: invalid control character in URL".into(),
        ));
    }
    if base.starts_with(':') {
        return Err(parse_error("missing protocol scheme".into()));
    }
    let path_end = base.find(['?', '#']).unwrap_or(base.len());
    let root = &base[..path_end];
    let suffix = &base[path_end..];
    let scheme = url_scheme(root);
    let authority_start = if let Some(scheme) = scheme {
        let rest = &root[scheme.len() + 1..];
        if !rest.starts_with('/') {
            return Ok(base.into());
        } // opaque URL paths do not serialize
        if rest.starts_with("//") {
            Some(scheme.len() + 3)
        } else {
            None
        }
    } else if root.starts_with("//") {
        Some(2)
    } else {
        None
    };
    let path_start = if let Some(start) = authority_start {
        let authority_end = root[start..]
            .find('/')
            .map(|end| start + end)
            .unwrap_or(root.len());
        let authority = root[start..authority_end]
            .rsplit('@')
            .next()
            .unwrap_or_default();
        let bytes = authority.as_bytes();
        for index in 0..bytes.len() {
            if bytes[index] == b'%' {
                let end = (index + 3).min(bytes.len());
                let escape = String::from_utf8_lossy(&bytes[index..end]);
                let valid = bytes.get(index + 1).is_some_and(u8::is_ascii_hexdigit)
                    && bytes.get(index + 2).is_some_and(u8::is_ascii_hexdigit);
                if !valid || (bytes[index + 1] < b'8' && &bytes[index + 1..index + 3] != b"25") {
                    return Err(parse_error(format!("invalid URL escape {escape:?}")));
                }
            }
        }
        if let Some(character) = authority.chars().find(|c| {
            matches!(
                c,
                ' ' | '"' | '<' | '>' | '{' | '}' | '|' | '\\' | '^' | '`'
            )
        }) {
            return Err(parse_error(format!(
                "invalid character {:?} in host name",
                character.to_string()
            )));
        }
        if authority.starts_with('[') && !authority.contains(']') {
            return Err(parse_error("missing ']' in host".into()));
        }
        if let Some((_, port)) = authority.rsplit_once(':')
            && !port.contains(']')
            && !port.bytes().all(|b| b.is_ascii_digit())
        {
            return Err(parse_error(format!(
                "invalid port {:?} after host",
                format!(":{port}")
            )));
        }
        authority_end
    } else if let Some(scheme) = scheme {
        scheme.len() + 1
    } else {
        0
    };
    let original_path = &root[path_start..];
    let bytes = original_path.as_bytes();
    for index in 0..bytes.len() {
        if bytes[index] == b'%'
            && (bytes.get(index + 1).is_none_or(|b| !b.is_ascii_hexdigit())
                || bytes.get(index + 2).is_none_or(|b| !b.is_ascii_hexdigit()))
        {
            let end = (index + 3).min(bytes.len());
            return Err(parse_error(format!(
                "invalid URL escape {:?}",
                String::from_utf8_lossy(&bytes[index..end])
            )));
        }
    }
    if let Some((_, fragment)) = suffix.split_once('#') {
        let bytes = fragment.as_bytes();
        for index in 0..bytes.len() {
            if bytes[index] == b'%'
                && (bytes.get(index + 1).is_none_or(|b| !b.is_ascii_hexdigit())
                    || bytes.get(index + 2).is_none_or(|b| !b.is_ascii_hexdigit()))
            {
                return Err(parse_error(format!(
                    "invalid URL escape {:?}",
                    String::from_utf8_lossy(&bytes[index..(index + 3).min(bytes.len())])
                )));
            }
        }
    }
    let absolute_path = original_path.starts_with('/');
    let mut combined = original_path.to_owned();
    for path in paths {
        combined.push('/');
        combined.push_str(path);
    }
    // JoinPath ignores setPath's failure for an invalid escape introduced by
    // an element, retaining the original URL instead of the appended path.
    let bytes = combined.as_bytes();
    if (0..bytes.len()).any(|index| {
        bytes[index] == b'%'
            && (bytes.get(index + 1).is_none_or(|b| !b.is_ascii_hexdigit())
                || bytes.get(index + 2).is_none_or(|b| !b.is_ascii_hexdigit()))
    }) {
        return Ok(base.into());
    }
    let trailing = combined.ends_with('/');
    let mut clean = Vec::new();
    for part in combined.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                clean.pop();
            }
            _ => clean.push(part),
        }
    }
    let mut path = format!(
        "{}{}",
        if absolute_path { "/" } else { "" },
        clean.join("/")
    );
    if trailing && !path.ends_with('/') {
        path.push('/');
    }
    fn valid_raw_byte(byte: u8) -> bool {
        byte.is_ascii_alphanumeric()
            || matches!(
                byte,
                b'-' | b'_'
                    | b'.'
                    | b'~'
                    | b'/'
                    | b':'
                    | b'@'
                    | b'&'
                    | b'='
                    | b'+'
                    | b'$'
                    | b','
                    | b';'
                    | b'%'
                    | b'!'
                    | b'\''
                    | b'('
                    | b')'
                    | b'*'
                    | b'['
                    | b']'
            )
    }
    let valid_raw = path.bytes().all(valid_raw_byte);
    let mut decoded = Vec::new();
    if !valid_raw {
        let bytes = path.as_bytes();
        let mut index = 0;
        while index < bytes.len() {
            if bytes[index] == b'%' {
                decoded.push(
                    u8::from_str_radix(&path[index + 1..index + 3], 16)
                        .expect("percent escapes were validated"),
                );
                index += 3;
            } else {
                decoded.push(bytes[index]);
                index += 1;
            }
        }
    }
    let mut encoded = String::new();
    for byte in if valid_raw { path.as_bytes() } else { &decoded } {
        let byte = *byte;
        if (valid_raw && valid_raw_byte(byte))
            || (!valid_raw
                && (byte.is_ascii_alphanumeric()
                    || matches!(
                        byte,
                        b'-' | b'_'
                            | b'.'
                            | b'~'
                            | b'/'
                            | b':'
                            | b'@'
                            | b'&'
                            | b'='
                            | b'+'
                            | b'$'
                            | b','
                            | b';'
                    )))
        {
            encoded.push(byte as char);
        } else {
            encoded.push_str(&format!("%{byte:02X}"));
        }
    }
    let prefix = &root[..path_start];
    let has_host = authority_start.is_some_and(|start| path_start > start);
    let slash = if has_host && !encoded.is_empty() && !encoded.starts_with('/') {
        "/"
    } else {
        ""
    };
    let mut result = format!("{prefix}{slash}{encoded}{suffix}");
    if let Some(scheme) = scheme {
        result.replace_range(..scheme.len(), &scheme.to_ascii_lowercase());
    }
    Ok(result)
}
fn debug_headers(method: &Method, endpoint: &str, headers: &HeaderMap) {
    if std::env::var_os("PB_DEBUG_HTTP_HEADERS").is_none_or(|v| v.is_empty()) {
        return;
    }
    eprintln!("pb debug request: {method} {endpoint}");
    for (name, value) in headers {
        let title = name
            .as_str()
            .split('-')
            .map(|part| format!("{}{}", part[..1].to_ascii_uppercase(), &part[1..]))
            .collect::<Vec<_>>()
            .join("-");
        let value = if matches!(name.as_str(), "authorization" | "x-api-key" | "cookie") {
            "<redacted>"
        } else {
            value.to_str().unwrap_or("<invalid>")
        };
        eprintln!("pb debug header: {title}: {value}");
    }
}

#[derive(Debug, Default, Deserialize)]
pub struct CloudWorkspace {
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub tenant_id: String,
    #[serde(default)]
    pub workspace_id: String,
    #[serde(default)]
    pub workspace_name: String,
}
pub async fn validate_cloud_api_key(
    orchestrator_url: &str,
    api_key: &str,
) -> Result<CloudWorkspace, ApiError> {
    CloudAuthClient::new()?
        .validate_api_key(orchestrator_url, api_key)
        .await
}
pub struct CloudAuthClient {
    transport: Client,
    token: String,
}
impl CloudAuthClient {
    pub fn new() -> Result<Self, ApiError> {
        Self::with_token(release_orchestrator_token())
    }
    pub fn with_token(token: impl Into<String>) -> Result<Self, ApiError> {
        Ok(Self {
            transport: transport()?,
            token: token.into(),
        })
    }
    pub async fn validate_api_key(
        &self,
        orchestrator_url: &str,
        api_key: &str,
    ) -> Result<CloudWorkspace, ApiError> {
        if orchestrator_url.trim().is_empty() {
            return Err(ApiError::InvalidResponse(
                "cloud orchestrator URL is not configured".into(),
            ));
        }
        let raw_endpoint = append_url_string(orchestrator_url, &["api/v1/cli/apikey/validate"])
            .map_err(|source| ApiError::Context {
                message: "invalid orchestrator URL".into(),
                source: Box::new(source),
            })?;
        let mut headers = orchestrator_headers(&self.token)?;
        let mut key = HeaderValue::from_str(api_key)
            .map_err(|_| AuthError("invalid HTTP header value".into()))?;
        key.set_sensitive(true);
        headers.insert("x-api-key", key);
        let endpoint = checked_http_url(&raw_endpoint).map_err(|source| ApiError::Context {
            message: "failed to validate cloud api key".into(),
            source: Box::new(ApiError::Request {
                method: Method::GET,
                url: raw_endpoint,
                source: Box::new(source),
            }),
        })?;
        let deadline = Instant::now() + CLOUD_TIMEOUT;
        let response = timeout_at(
            deadline,
            send_cloud_redirects(&self.transport, &endpoint, &headers),
        )
        .await
        .map_err(|_| ApiError::Timeout {
            method: Method::GET,
            url: endpoint.to_string(),
            body: false,
        })
        .and_then(|result| {
            result.map_err(|source| {
                if matches!(&source, ApiError::Request { .. }) {
                    source
                } else {
                    ApiError::Request {
                        method: Method::GET,
                        url: endpoint.to_string(),
                        source: Box::new(source),
                    }
                }
            })
        })
        .map_err(|source| ApiError::Context {
            message: "failed to validate cloud api key".into(),
            source: Box::new(source),
        })?;
        let response = timeout_at(deadline, buffer_response(response))
            .await
            .map_err(|_| ApiError::Timeout {
                method: Method::GET,
                url: endpoint.to_string(),
                body: true,
            })??;
        if !(200..300).contains(&response.status) {
            return Err(ApiError::HttpStatus {
                status: response.status,
                reason: response.reason,
                body: String::from_utf8_lossy(&response.body).into_owned(),
                context: "cloud api key validation failed".into(),
            });
        }
        let context = "failed to decode cloud api key validation response";
        let value = cloud_object(&response.body, "cloudAPIKeyValidationResponse", context)?;
        validate_cloud_fields(
            &value,
            "cloudAPIKeyValidationResponse",
            context,
            &[
                "org_id",
                "org_name",
                "workspace_id",
                "workspace_name",
                "tenant_id",
                "url",
                "state",
            ],
            &["multi_tenant"],
        )?;
        let result = CloudWorkspace {
            url: cloud_string(&value, "url", "cloudAPIKeyValidationResponse", context)?,
            tenant_id: cloud_string(
                &value,
                "tenant_id",
                "cloudAPIKeyValidationResponse",
                context,
            )?,
            workspace_id: cloud_string(
                &value,
                "workspace_id",
                "cloudAPIKeyValidationResponse",
                context,
            )?,
            workspace_name: cloud_string(
                &value,
                "workspace_name",
                "cloudAPIKeyValidationResponse",
                context,
            )?,
        };
        if result.url.is_empty() || result.tenant_id.is_empty() || result.workspace_id.is_empty() {
            return Err(ApiError::InvalidResponse(
                "cloud api key validation response missing url, tenant_id, or workspace_id".into(),
            ));
        }
        Ok(result)
    }
}

fn cloud_decode_error(context: &str, detail: String) -> ApiError {
    ApiError::InvalidResponse(if context.is_empty() {
        detail
    } else {
        format!("{context}: {detail}")
    })
}
fn json_kind(value: &crate::gojson::Node) -> &str {
    value.kind()
}
fn cloud_object(
    body: &[u8],
    typename: &str,
    context: &str,
) -> Result<crate::gojson::OrderedDocument, ApiError> {
    let value = crate::gojson::decode_ordered(body)
        .map_err(|detail| cloud_decode_error(context, detail))?;
    if !value.is_object() && !value.is_null() {
        return Err(cloud_decode_error(
            context,
            format!(
                "json: cannot unmarshal {} into Go value of type cmd.{typename}",
                json_kind(&value)
            ),
        ));
    }
    Ok(value)
}
fn cloud_string(
    value: &crate::gojson::Node,
    key: &str,
    typename: &str,
    context: &str,
) -> Result<String, ApiError> {
    let field = value.as_object().and_then(|fields| {
        fields
            .iter()
            .rfind(|(name, value)| name.eq_ignore_ascii_case(key) && !value.is_null())
            .map(|(_, value)| value)
    });
    match field {
        None => Ok(String::new()),
        Some(value) if value.is_string() => Ok(value.as_str().expect("string was checked").into()),
        Some(value) => Err(cloud_decode_error(
            context,
            format!(
                "json: cannot unmarshal {} into Go struct field {typename}.{key} of type string",
                json_kind(value)
            ),
        )),
    }
}
fn validate_cloud_fields(
    value: &crate::gojson::Node,
    typename: &str,
    context: &str,
    strings: &[&str],
    bools: &[&str],
) -> Result<(), ApiError> {
    if let Some(fields) = value.as_object() {
        for (name, value) in fields {
            if value.is_null() {
                continue;
            }
            let expected = if strings.iter().any(|key| key.eq_ignore_ascii_case(name)) {
                Some("string")
            } else if bools.iter().any(|key| key.eq_ignore_ascii_case(name)) {
                Some("bool")
            } else {
                None
            };
            if let Some(expected) = expected
                && json_kind(value) != expected
            {
                return Err(cloud_decode_error(
                    context,
                    format!(
                        "json: cannot unmarshal {} into Go struct field {typename}.{} of type {expected}",
                        json_kind(value),
                        name.to_ascii_lowercase()
                    ),
                ));
            }
        }
    }
    Ok(())
}

async fn send_cloud_redirects(
    client: &Client,
    endpoint: &Endpoint,
    headers: &HeaderMap,
) -> Result<RawResponse, ApiError> {
    let mut current = endpoint.clone();
    let mut current_headers = headers.clone();
    let mut stripped = false;
    for hop in 0..10 {
        let response = send_raw(client, &Method::GET, &current, &current_headers, &None)
            .await
            .map_err(|source| ApiError::Request {
                method: Method::GET,
                url: current.to_string(),
                source: Box::new(source),
            })?;
        if !matches!(response.status().as_u16(), 301 | 302 | 303 | 307 | 308)
            || response.headers().get(LOCATION).is_none()
        {
            return Ok(response);
        }
        let location = crate::url_reference::location(response.headers()[LOCATION].as_bytes());
        if location.is_empty() {
            return Ok(response);
        }
        let next = current.join(&location).map_err(|e| ApiError::Request {
            method: Method::GET,
            url: current.to_string(),
            source: Box::new(ApiError::InvalidUrl(format!(
                "failed to parse Location header {location:?}: {e}"
            ))),
        })?;
        if hop == 9 {
            return Err(ApiError::Request {
                method: Method::GET,
                url: location,
                source: Box::new(ApiError::Redirect("stopped after 10 redirects".into())),
            });
        }
        drain_redirect(response).await;
        stripped |= !redirect_credentials_allowed(endpoint, &next);
        current_headers = headers.clone();
        if stripped {
            current_headers.remove(AUTHORIZATION);
        }
        if !(current.scheme() == "https" && next.scheme() == "http")
            && let Ok(value) = HeaderValue::from_str(&current.referer())
        {
            current_headers.insert(REFERER, value);
        }
        current = next;
    }
    unreachable!("redirect loop always returns")
}

/// Go QueryEscape leaves tilde alone and preserves repeated-value insertion order.
pub fn encode_query_pairs(pairs: &[(String, String)]) -> String {
    fn escape(value: &str) -> String {
        let mut out = String::new();
        for byte in value.bytes() {
            match byte {
                b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                    out.push(byte as char)
                }
                b' ' => out.push('+'),
                _ => {
                    use std::fmt::Write;
                    write!(out, "%{byte:02X}").unwrap();
                }
            }
        }
        out
    }
    let mut sorted: Vec<_> = pairs.iter().collect();
    sorted.sort_by(|a, b| a.0.cmp(&b.0));
    sorted
        .into_iter()
        .map(|(k, v)| format!("{}={}", escape(k), escape(v)))
        .collect::<Vec<_>>()
        .join("&")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use std::{
        collections::BTreeMap,
        io::{Read, Write},
        net::{TcpListener, TcpStream},
        sync::{
            Arc,
            atomic::{AtomicUsize, Ordering},
        },
        thread,
    };

    #[test]
    fn metrics_preparation_uses_sorted_go_query_and_own_policy() {
        let profile = ProfileRecord {
            url: "http://example.com/base".into(),
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        let pairs = vec![
            ("stream".into(), "a b".into()),
            ("match[]".into(), "a=~\"x.*\"".into()),
            ("match[]".into(), "b+z".into()),
        ];
        let request = client
            .prepare_metrics("prometheus/api/v1/series", &pairs)
            .unwrap();
        assert_eq!(
            request.raw_endpoint,
            "http://example.com/base/prometheus/api/v1/series?match%5B%5D=a%3D~%22x.%2A%22&match%5B%5D=b%2Bz&stream=a+b"
        );
        assert_eq!(request.timeout, METRICS_TIMEOUT);
        assert!(request.metrics);
        assert!(!request.headers.contains_key(CONTENT_TYPE));
        assert!(client.metrics_transport.get().is_none());
        let standard = client.prepare_standard(Method::GET, "query", None).unwrap();
        assert!(!standard.metrics);
        assert_eq!(standard.timeout, STANDARD_TIMEOUT);
        assert!(standard.headers.contains_key(CONTENT_TYPE));
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }

    static NEXT: AtomicUsize = AtomicUsize::new(0);
    struct Reply {
        status: u16,
        reason: Option<&'static str>,
        body: &'static str,
        location: Option<String>,
        delay: Duration,
    }
    impl Reply {
        fn new(status: u16, body: &'static str) -> Self {
            Self {
                status,
                reason: None,
                body,
                location: None,
                delay: Duration::ZERO,
            }
        }
    }
    type Captured = Arc<Mutex<Vec<String>>>;

    #[test]
    fn redirects_preserve_wire_path_segments_and_raw_utf8() {
        for (location, expected) in [
            ("/a//b", "/a//b"),
            ("/a/.", "/a/"),
            ("/a/../b", "/b"),
            ("/a/%2e%2e/b", "/a/%2e%2e/b"),
            ("/café", "/caf%C3%A9"),
            ("next?key=value", "/api/v1/next?key=value"),
        ] {
            let mut redirect = Reply::new(302, "");
            redirect.location = Some(location.into());
            let (endpoint, requests, task) = server(vec![redirect, Reply::new(200, "{}")]);
            let profile = ProfileRecord {
                url: endpoint,
                api_key: "fixture-key".into(),
                ..Default::default()
            };
            let store = store(&profile);
            let client = ApiClient::new(profile, store.clone()).unwrap();
            let response = runtime()
                .block_on(client.standard(Method::GET, "about", None))
                .unwrap();
            assert_eq!(response.status, 200);
            task.join().unwrap();
            assert!(
                requests.lock().unwrap()[1].starts_with(&format!("GET {expected} HTTP/1.1\r\n")),
                "{location}"
            );
            std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
        }
    }

    fn server(replies: Vec<Reply>) -> (String, Captured, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let captured = Arc::new(Mutex::new(Vec::new()));
        let requests = captured.clone();
        let task = thread::spawn(move || {
            for reply in replies {
                let (mut stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                let mut bytes = Vec::new();
                let mut buffer = [0; 2048];
                let end = loop {
                    let n = stream.read(&mut buffer).unwrap();
                    assert!(n > 0);
                    bytes.extend_from_slice(&buffer[..n]);
                    if let Some(end) = bytes.windows(4).position(|s| s == b"\r\n\r\n") {
                        break end + 4;
                    }
                };
                let header = String::from_utf8_lossy(&bytes[..end]).into_owned();
                let length: usize = header
                    .lines()
                    .find_map(|line| {
                        line.to_ascii_lowercase()
                            .strip_prefix("content-length:")
                            .map(|value| value.trim().parse().unwrap())
                    })
                    .unwrap_or(0);
                while bytes.len() < end + length {
                    let n = stream.read(&mut buffer).unwrap();
                    assert!(n > 0);
                    bytes.extend_from_slice(&buffer[..n]);
                }
                requests
                    .lock()
                    .unwrap()
                    .push(String::from_utf8(bytes).unwrap());
                thread::sleep(reply.delay);
                let location = reply
                    .location
                    .map(|location| format!("Location: {location}\r\n"))
                    .unwrap_or_default();
                let response = format!(
                    "HTTP/1.1 {} {}\r\nContent-Length: {}\r\nConnection: close\r\n{location}\r\n{}",
                    reply.status,
                    reply
                        .reason
                        .unwrap_or_else(|| StatusCode::from_u16(reply.status)
                            .unwrap()
                            .canonical_reason()
                            .unwrap_or("")),
                    reply.body.len(),
                    reply.body
                );
                let _ = stream.write_all(response.as_bytes());
            }
        });
        (endpoint, captured, task)
    }
    fn runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
    }
    fn read_test_headers(stream: &mut TcpStream) {
        let mut bytes = Vec::new();
        while !bytes.ends_with(b"\r\n\r\n") {
            let mut byte = [0];
            stream.read_exact(&mut byte).unwrap();
            bytes.push(byte[0]);
        }
    }
    fn store(profile: &ProfileRecord) -> ProfileStore {
        let path = std::env::temp_dir()
            .join(format!(
                "pb-http-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ))
            .join("config.toml");
        let store = ProfileStore::new(path);
        store
            .write(&Config {
                profiles: BTreeMap::from([("cloud".into(), profile.clone())]),
                default_profile: "cloud".into(),
            })
            .unwrap();
        store
    }
    fn oauth(url: String, orchestrator_url: String) -> ProfileRecord {
        ProfileRecord {
            url,
            cloud: true,
            tenant_id: "tenant".into(),
            session_token: "old-session".into(),
            refresh_token: "old-refresh".into(),
            orchestrator_url,
            ..Default::default()
        }
    }
    #[test]
    fn streaming_headers_and_bounded_reads_complete_before_tail_is_released() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let (release, wait) = std::sync::mpsc::channel();
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_test_headers(&mut stream);
            stream.write_all(b"HTTP/1.1 200 Query Ready\r\nContent-Length: 20\r\nConnection: close\r\n\r\nabcdefghij").unwrap();
            // Completion of the client reads causes this tail to be released;
            // buffering or draining before returning would deadlock the test.
            if wait.recv_timeout(Duration::from_secs(2)).is_ok() {
                let _ = stream.write_all(b"klmnopqrst");
            }
        });
        let profile = ProfileRecord {
            url: endpoint,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        runtime().block_on(async {
            let request = client.prepare_standard(Method::GET, "query", None).unwrap();
            let mut response = client.send_streaming(request).await.unwrap();
            assert_eq!(response.status, 200);
            assert_eq!(response.reason, "Query Ready");
            assert!(response.read_limited(0).await.unwrap().is_empty());
            assert_eq!(response.read_limited(3).await.unwrap(), b"abc");
            assert_eq!(response.chunk_limit(2).await.unwrap().unwrap(), b"de"[..]);
            assert_eq!(response.read_limited(5).await.unwrap(), b"fghij");
            drop(response);
            release.send(()).unwrap();
        });
        task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn dropping_stream_does_not_wait_for_stalled_tail() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let (closed, wait_closed) = std::sync::mpsc::channel();
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_test_headers(&mut stream);
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\n[]")
                .unwrap();
            let mut byte = [0];
            let result = stream.read(&mut byte);
            closed.send(result.is_ok_and(|count| count == 0)).unwrap();
        });
        let profile = ProfileRecord {
            url: endpoint,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        runtime().block_on(async {
            let request = client.prepare_standard(Method::GET, "query", None).unwrap();
            let mut response = client.send_streaming(request).await.unwrap();
            assert_eq!(response.read_limited(2).await.unwrap(), b"[]");
            drop(response);
            // Drive Hyper's task after dropping the reader so the server can
            // confirm cancellation while it still withholds the remaining body.
            for _ in 0..100 {
                if let Ok(closed) = wait_closed.try_recv() {
                    assert!(closed);
                    return;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            panic!("server did not observe body cancellation");
        });
        task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn streamed_body_retains_deadline_while_consumer_is_idle() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_test_headers(&mut stream);
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\na")
                .unwrap();
            thread::sleep(Duration::from_millis(200));
        });
        let profile = ProfileRecord {
            url: endpoint,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        runtime().block_on(async {
            let request = client
                .prepare(
                    Method::GET,
                    &["api/v1/query"],
                    None,
                    Duration::from_millis(40),
                )
                .unwrap();
            let mut response = client.send_streaming(request).await.unwrap();
            assert_eq!(response.read_limited(1).await.unwrap(), b"a");
            tokio::time::sleep(Duration::from_millis(80)).await;
            // Waiting between reads must not restart the body deadline.
            let error = response.chunk().await.unwrap_err();
            assert!(matches!(error, ApiError::BodyCanceled));
            assert!(!error.is_request_error());
        });
        task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn expired_deadline_rejects_an_already_buffered_frame() {
        let (endpoint, _, task) = server(vec![Reply::new(200, "abcdef")]);
        let profile = ProfileRecord {
            url: endpoint,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        runtime().block_on(async {
            let request = client.prepare(Method::GET, &["api/v1/query"], None, Duration::from_secs(1)).unwrap();
            let mut response = client.send_streaming(request).await.unwrap();
            assert_eq!(response.read_limited(1).await.unwrap(), b"a");
            response.deadline = Instant::now() - Duration::from_millis(1);
            let error = response.chunk().await.unwrap_err();
            assert!(matches!(error, ApiError::BodyCanceled));
            assert!(error.is_timeout());
            assert!(!error.is_request_error());
            assert_eq!(error.to_string(), "net/http: request canceled (Client.Timeout or context cancellation while reading body)");
            assert!(response.response.is_none());
        });
        task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn streamed_body_deadline_includes_oauth_refresh_and_redirect() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let final_url = format!("http://{}", listener.local_addr().unwrap());
        let final_task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_test_headers(&mut stream);
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\nx")
                .unwrap();
            thread::sleep(Duration::from_millis(350));
        });
        let mut redirect = Reply::new(307, "");
        redirect.location = Some(final_url);
        let (workspace, requests, workspace_task) = server(vec![Reply::new(401, ""), redirect]);
        let mut refreshed = Reply::new(
            200,
            r#"{"access_token":"new-session","refresh_token":"new-refresh"}"#,
        );
        refreshed.delay = Duration::from_millis(70);
        let (orchestrator, _, refresh_task) = server(vec![refreshed]);
        let profile = oauth(workspace, orchestrator);
        let store = store(&profile);
        let client =
            ApiClient::with_orchestrator_token(profile, store.clone(), "release-token").unwrap();
        runtime().block_on(async {
            let request = client
                .prepare(
                    Method::POST,
                    &["api/v1/query"],
                    Some(b"{}".to_vec()),
                    Duration::from_millis(180),
                )
                .unwrap();
            let mut response = client.send_streaming(request).await.unwrap();
            assert_eq!(response.read_limited(1).await.unwrap(), b"x");
            // More than the remaining original budget, less than a freshly
            // started response-body deadline: the next read must fail at once.
            tokio::time::sleep(Duration::from_millis(130)).await;
            let started = Instant::now();
            let error = response.chunk().await.unwrap_err();
            assert!(matches!(error, ApiError::BodyCanceled));
            assert!(started.elapsed() < Duration::from_millis(50));
        });
        workspace_task.join().unwrap();
        refresh_task.join().unwrap();
        final_task.join().unwrap();
        assert_eq!(requests.lock().unwrap().len(), 2);
        assert_eq!(client.profile().session_token, "new-session");
        assert_eq!(
            store.read().unwrap().profiles["cloud"].refresh_token,
            "new-refresh"
        );
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn streaming_body_fault_keeps_received_prefix_and_body_error_stage() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let (close, wait) = std::sync::mpsc::channel();
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_test_headers(&mut stream);
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\nprefix",
                )
                .unwrap();
            let _ = wait.recv_timeout(Duration::from_secs(2));
        });
        let profile = ProfileRecord {
            url: endpoint,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        runtime().block_on(async {
            let request = client.prepare_standard(Method::GET, "query", None).unwrap();
            let mut response = client.send_streaming(request).await.unwrap();
            assert_eq!(response.read_limited(6).await.unwrap(), b"prefix");
            close.send(()).unwrap();
            let error = response.chunk().await.unwrap_err();
            assert!(matches!(&error, ApiError::Network(source) if source.is_body() && !source.is_connection_error()));
            assert_eq!(error.to_string(), "unexpected EOF");
            assert!(!error.is_request_error());
            let classified = crate::error::CliError::from(error);
            assert_eq!(classified.code, crate::ErrorCode::CommandFailed);
            assert!(!classified.retryable);
        });
        task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[cfg(unix)]
    #[test]
    fn streaming_connection_reset_keeps_network_cause_after_headers() {
        use std::os::fd::AsRawFd;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let (reset, wait) = std::sync::mpsc::channel();
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_test_headers(&mut stream);
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\nprefix",
                )
                .unwrap();
            if wait.recv_timeout(Duration::from_secs(2)).is_ok() {
                let linger = libc::linger {
                    l_onoff: 1,
                    l_linger: 0,
                };
                assert_eq!(
                    unsafe {
                        libc::setsockopt(
                            stream.as_raw_fd(),
                            libc::SOL_SOCKET,
                            libc::SO_LINGER,
                            (&linger as *const libc::linger).cast(),
                            std::mem::size_of_val(&linger) as libc::socklen_t,
                        )
                    },
                    0
                );
            }
        });
        let profile = ProfileRecord {
            url: endpoint,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        runtime().block_on(async {
            let request = client.prepare_standard(Method::GET, "query", None).unwrap();
            let mut response = client.send_streaming(request).await.unwrap();
            assert_eq!(response.read_limited(6).await.unwrap(), b"prefix");
            reset.send(()).unwrap();
            let error = response.chunk().await.unwrap_err();
            assert!(matches!(&error, ApiError::Network(source) if source.is_body() && source.is_connection_error()));
            assert!(!error.is_request_error());
            let classified = crate::error::CliError::from(error);
            assert_eq!(classified.code, crate::ErrorCode::ConnectionFailed);
            assert!(classified.retryable);
        });
        task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn chunked_text_and_json_prefixes_preserve_partial_body_before_eof() {
        for prefix in [&b"abcdef"[..], &b"[] \n"[..], &br#"[{"a":1},{"b":"#[..]] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let endpoint = format!("http://{}", listener.local_addr().unwrap());
            let (close, wait) = std::sync::mpsc::channel();
            let task = thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                read_test_headers(&mut stream);
                stream.write_all(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n").unwrap();
                stream
                    .write_all(format!("{:x}\r\n", prefix.len()).as_bytes())
                    .unwrap();
                stream.write_all(prefix).unwrap();
                stream.write_all(b"\r\n").unwrap();
                // Omit the terminating zero chunk only after the client has
                // received the text/JSON prefix, preserving partial output.
                let _ = wait.recv_timeout(Duration::from_secs(2));
            });
            let profile = ProfileRecord {
                url: endpoint,
                api_key: "key".into(),
                ..Default::default()
            };
            let store = store(&profile);
            let client = ApiClient::new(profile, store.clone()).unwrap();
            runtime().block_on(async {
                let request = client.prepare_standard(Method::GET, "query", None).unwrap();
                let mut response = client.send_streaming(request).await.unwrap();
                assert_eq!(response.read_limited(prefix.len()).await.unwrap(), prefix);
                close.send(()).unwrap();
                let error = response.chunk().await.unwrap_err();
                assert!(matches!(&error, ApiError::Network(cause) if cause.is_body()));
                assert_eq!(error.to_string(), "unexpected EOF");
                let classified = crate::error::CliError::from(error);
                assert_eq!(classified.code, crate::ErrorCode::CommandFailed);
                assert!(!classified.retryable);
            });
            task.join().unwrap();
            std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
        }
    }
    #[test]
    fn malformed_chunk_length_keeps_received_prefix_and_go_body_message() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let (close, wait) = std::sync::mpsc::channel();
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_test_headers(&mut stream);
            stream.write_all(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n3\r\nabc\r\n").unwrap();
            if wait.recv_timeout(Duration::from_secs(2)).is_ok() {
                let _ = stream.write_all(b"x\r\n");
            }
        });
        let profile = ProfileRecord {
            url: endpoint,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        runtime().block_on(async {
            let request = client.prepare_standard(Method::GET, "query", None).unwrap();
            let mut response = client.send_streaming(request).await.unwrap();
            assert_eq!(response.read_limited(3).await.unwrap(), b"abc");
            close.send(()).unwrap();
            let error = response.chunk().await.unwrap_err();
            assert_eq!(error.to_string(), "invalid byte in chunk length");
            assert!(!error.is_timeout());
            let classified = crate::error::CliError::from(error);
            assert_eq!(classified.code, crate::ErrorCode::CommandFailed);
            assert!(!classified.retryable);
        });
        task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn preparation_and_execution_failures_remain_distinct() {
        let profile = ProfileRecord {
            url: "http://example.com".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        let error = client
            .prepare_standard(Method::POST, "query", Some(b"{}".to_vec()))
            .unwrap_err();
        assert!(!error.is_request_error());
        assert_eq!(error.to_string(), "self-hosted profile missing credentials");
        let profile = ProfileRecord {
            url: "ftp://example.com".into(),
            api_key: "key".into(),
            ..Default::default()
        };
        let client = ApiClient::new(profile, store.clone()).unwrap();
        let request = client
            .prepare_standard(Method::POST, "query", Some(b"{}".to_vec()))
            .unwrap();
        let error = runtime()
            .block_on(client.send_streaming(request))
            .unwrap_err();
        assert!(error.is_request_error());
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn path_append_preserves_prefix_and_base_query() {
        let url = append_url(
            "https://example.com/base/prefix?foo=bar#fragment",
            &["api/v1/", "/logstream/name%2Fencoded/../info/"],
        )
        .unwrap();
        assert_eq!(
            url.as_str(),
            "https://example.com/base/prefix/api/v1/logstream/info/?foo=bar#fragment"
        );
        assert_eq!(
            append_url(
                "https://example.com/base",
                &["api/v1", "name%2Fencoded?x=y"]
            )
            .unwrap()
            .as_str(),
            "https://example.com/base/api/v1/name/encoded%3Fx=y"
        );
        assert_eq!(
            append_url_string(
                "https://example.com",
                &["api/v1/", "logstream/a b/c%2F/schema"]
            )
            .unwrap(),
            "https://example.com/api/v1/logstream/a%20b/c//schema"
        );
    }
    #[test]
    fn relative_and_invalid_standard_urls_match_go_execution_errors() {
        for (base, joined, scheme) in [
            ("relative", "relative/api/v1/about", ""),
            ("/prefix", "/prefix/api/v1/about", ""),
            ("", "api/v1/about", ""),
            ("example.com:8000", "example.com:8000", "example.com"),
            (
                "ftp://example.com/base",
                "ftp://example.com/base/api/v1/about",
                "ftp",
            ),
            ("http://host/%zz", "", ""),
            ("http://[::1", "", ""),
            ("://bad", "", ""),
            ("https://host:abc/base", "", ""),
            ("http:// host/base", "", ""),
            ("http://a|b/base", "", ""),
            ("http://host/base#%zz", "", ""),
            ("http://%6c%6fcalhost:1", "", ""),
        ] {
            let profile = ProfileRecord {
                url: base.into(),
                api_key: "key".into(),
                ..Default::default()
            };
            let store = store(&profile);
            let client = ApiClient::new(profile, store.clone()).unwrap();
            let error = runtime()
                .block_on(client.standard(Method::GET, "about", None))
                .unwrap_err();
            assert_eq!(
                error.to_string(),
                format!("Get {joined:?}: unsupported protocol scheme {scheme:?}"),
                "base={base}"
            );
            assert!(error.is_request_error());
            std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
        }
    }
    #[test]
    fn encoded_dot_metadata_does_not_change_wire_path() {
        let raw = append_url_string(
            "https://example.com/base/%2e%2e/prefix",
            &["api/v1", "about"],
        )
        .unwrap();
        assert_eq!(raw, "https://example.com/base/%2e%2e/prefix/api/v1/about");
        // Origin metadata can normalize dots; the HTTP URI must retain them.
        assert_eq!(Url::parse(&raw).unwrap().path(), "/prefix/api/v1/about");
        let (endpoint, requests, task) = server(vec![Reply::new(200, "{}")]);
        let profile = ProfileRecord {
            url: format!("{endpoint}/base/%2e%2e/prefix"),
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        assert_eq!(
            runtime()
                .block_on(client.standard(Method::GET, "about", None))
                .unwrap()
                .status,
            200
        );
        task.join().unwrap();
        let requests = requests.lock().unwrap();
        assert!(requests[0].starts_with("GET /base/%2e%2e/prefix/api/v1/about HTTP/1.1"));
        assert!(!requests[0].to_ascii_lowercase().contains("\r\naccept:"));
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn cloud_validation_uses_separate_release_and_user_credentials() {
        let (endpoint, calls, task) = server(vec![Reply::new(
            200,
            r#"{"url":"https://workspace","tenant_id":"tenant","workspace_id":"id","workspace_name":"Workspace"}"#,
        )]);
        let client = CloudAuthClient::with_token("release-token").unwrap();
        let result = runtime()
            .block_on(client.validate_api_key(&format!("{endpoint}/prefix"), "user-key"))
            .unwrap();
        assert_eq!(result.workspace_name, "Workspace");
        task.join().unwrap();
        let calls = calls.lock().unwrap();
        assert!(calls[0].starts_with("GET /prefix/api/v1/cli/apikey/validate HTTP/1.1"));
        assert!(calls[0].contains("authorization: Bearer release-token"));
        assert!(calls[0].contains("x-api-key: user-key"));
        assert!(!calls[0].contains("cookie:"));
        assert!(!calls[0].contains("x-p-tenant:"));
    }
    #[test]
    fn cloud_json_errors_and_null_fields_match_go() {
        assert_eq!(
            cloud_object(b"{", "cloudRefreshResponse", "")
                .unwrap_err()
                .to_string(),
            "unexpected end of JSON input"
        );
        assert_eq!(
            cloud_object(b"[]", "cloudRefreshResponse", "")
                .unwrap_err()
                .to_string(),
            "json: cannot unmarshal array into Go value of type cmd.cloudRefreshResponse"
        );
        let value = cloud_object(
            br#"{"access_token":42,"refresh_token":null}"#,
            "cloudRefreshResponse",
            "",
        )
        .unwrap();
        assert_eq!(
            cloud_string(&value, "access_token", "cloudRefreshResponse", "")
                .unwrap_err()
                .to_string(),
            "json: cannot unmarshal number into Go struct field cloudRefreshResponse.access_token of type string"
        );
        assert_eq!(
            cloud_string(&value, "refresh_token", "cloudRefreshResponse", "").unwrap(),
            ""
        );
        assert_eq!(
            redact_url_password("http://user:secret@host/path"),
            "http://user:***@host/path"
        );
        assert_eq!(
            append_url_string("http://user:secret@host/prefix", &["api/v1", "about"]).unwrap(),
            "http://user:secret@host/prefix/api/v1/about"
        );
        assert!(checked_http_url("http://user:secret@host/prefix/api/v1/about").is_ok());
        assert!(!same_origin_raw("http://host:80", "http://host"));
        assert!(same_origin_raw("HTTP://HOST:80", "http://host:80"));
    }
    #[test]
    fn refresh_failure_preserves_custom_server_reason_phrase() {
        let (workspace, _, workspace_task) = server(vec![Reply::new(401, "")]);
        let mut expired = Reply::new(401, "expired");
        expired.reason = Some("Token Expired");
        let (orchestrator, _, refresh_task) = server(vec![expired]);
        let profile = oauth(workspace, orchestrator);
        let store = store(&profile);
        let client =
            ApiClient::with_orchestrator_token(profile, store.clone(), "release-token").unwrap();
        let error = runtime()
            .block_on(client.standard(Method::GET, "about", None))
            .unwrap_err();
        assert!(error.to_string().contains("cloud session refresh failed: cloud session refresh failed: 401 Token Expired: expired"), "{error}");
        workspace_task.join().unwrap();
        refresh_task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn cloud_typed_duplicates_keep_earlier_errors_and_ignore_null() {
        let value = cloud_object(
            br#"{"access_token":42,"access_token":"later","refresh_token":"refresh"}"#,
            "cloudRefreshResponse",
            "",
        )
        .unwrap();
        assert_eq!(
            validate_cloud_fields(
                &value,
                "cloudRefreshResponse",
                "",
                &["access_token", "refresh_token"],
                &[]
            )
            .unwrap_err()
            .to_string(),
            "json: cannot unmarshal number into Go struct field cloudRefreshResponse.access_token of type string"
        );
        let value = cloud_object(br#"{"access_token":"first","ACCESS_TOKEN":"middle","access_token":"last","access_token":null}"#, "cloudRefreshResponse", "").unwrap();
        validate_cloud_fields(&value, "cloudRefreshResponse", "", &["access_token"], &[]).unwrap();
        assert_eq!(
            cloud_string(&value, "access_token", "cloudRefreshResponse", "").unwrap(),
            "last"
        );
    }
    #[test]
    fn invalid_gzip_401_body_rotates_tokens_then_fails_without_replay() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_test_headers(&mut stream);
            stream.write_all(b"HTTP/1.1 401 Unauthorized\r\nContent-Encoding: gzip\r\nContent-Length: 16\r\nConnection: close\r\n\r\nthis is not gzip").unwrap();
        });
        let (orchestrator, _, refresh_task) = server(vec![Reply::new(
            200,
            r#"{"access_token":"new-session","refresh_token":"new-refresh"}"#,
        )]);
        let profile = oauth(endpoint, orchestrator);
        let store = store(&profile);
        let client =
            ApiClient::with_orchestrator_token(profile, store.clone(), "release-token").unwrap();
        let error = runtime()
            .block_on(client.standard(Method::GET, "about", None))
            .unwrap_err();
        assert!(
            error.to_string().contains("gzip: invalid header"),
            "{error}"
        );
        assert!(error.is_request_error());
        assert_eq!(client.profile().session_token, "new-session");
        assert_eq!(
            store.read().unwrap().profiles["cloud"].refresh_token,
            "new-refresh"
        );
        task.join().unwrap();
        refresh_task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn body_deadline_preserves_go_message_and_stage() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_test_headers(&mut stream);
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\nx")
                .unwrap();
            thread::sleep(Duration::from_millis(150));
        });
        let profile = ProfileRecord {
            url: endpoint,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        let error = runtime()
            .block_on(client.request(
                Method::GET,
                &["api/v1/about"],
                None,
                Duration::from_millis(60),
            ))
            .unwrap_err();
        assert!(error.is_timeout());
        assert_eq!(
            error.to_string(),
            "context deadline exceeded (Client.Timeout or context cancellation while reading body)"
        );
        task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn empty_redirect_location_returns_original_response() {
        let mut redirect = Reply::new(302, "original");
        redirect.location = Some(String::new());
        let (url, calls, task) = server(vec![redirect]);
        let profile = ProfileRecord {
            url,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        let response = runtime()
            .block_on(client.standard(Method::GET, "about", None))
            .unwrap();
        assert_eq!(response.status, 302);
        assert_eq!(response.body, b"original");
        task.join().unwrap();
        assert_eq!(calls.lock().unwrap().len(), 1);
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn redirect_limit_and_failing_hop_report_the_go_url() {
        let replies = (1..=10)
            .map(|hop| {
                let mut reply = Reply::new(302, "");
                reply.location = Some(format!("/loop/{hop}"));
                reply
            })
            .collect();
        let (url, _, task) = server(replies);
        let profile = ProfileRecord {
            url,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        let error = runtime()
            .block_on(client.standard(Method::GET, "about", None))
            .unwrap_err();
        assert_eq!(
            error.to_string(),
            "Get \"/loop/10\": stopped after 10 redirects"
        );
        task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn gzip_redirect_drain_is_bounded_before_end_of_body() {
        use std::io::Write;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let mut encoder = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        encoder.write_all(&vec![b'x'; 8192]).unwrap();
        let mut gzip = encoder.finish().unwrap();
        gzip.truncate(gzip.len() - 8);
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_test_headers(&mut stream);
            stream.write_all(b"HTTP/1.1 302 Found\r\nLocation: /ok\r\nContent-Encoding: gzip\r\nTransfer-Encoding: chunked\r\n\r\n").unwrap();
            stream
                .write_all(format!("{:x}\r\n", gzip.len()).as_bytes())
                .unwrap();
            stream.write_all(&gzip).unwrap();
            stream.write_all(b"\r\n").unwrap();
            let held = thread::spawn(move || {
                thread::sleep(Duration::from_millis(150));
                drop(stream);
            });
            let (mut stream, _) = listener.accept().unwrap();
            read_test_headers(&mut stream);
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
                .unwrap();
            held.join().unwrap();
        });
        let profile = ProfileRecord {
            url: endpoint,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        let response = runtime()
            .block_on(client.request(
                Method::GET,
                &["api/v1/about"],
                None,
                Duration::from_millis(80),
            ))
            .unwrap();
        assert_eq!(response.status, 200);
        task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn oauth_refresh_persists_and_replays_identical_body_once() {
        let (workspace, calls, workspace_task) =
            server(vec![Reply::new(401, "unauthorized"), Reply::new(200, "{}")]);
        let (orchestrator, refresh, refresh_task) = server(vec![Reply::new(
            200,
            r#"{"access_token":"new-session","refresh_token":"new-refresh"}"#,
        )]);
        let profile = oauth(
            format!("{workspace}/prefix"),
            format!("{orchestrator}/orch"),
        );
        let store = store(&profile);
        let client =
            ApiClient::with_orchestrator_token(profile, store.clone(), "release-token").unwrap();
        let response = runtime()
            .block_on(client.standard(
                Method::POST,
                "query",
                Some(b"{\"sql\":\"select 1\"}".to_vec()),
            ))
            .unwrap();
        assert_eq!(response.status, 200);
        workspace_task.join().unwrap();
        refresh_task.join().unwrap();
        let calls = calls.lock().unwrap();
        assert_eq!(calls.len(), 2);
        assert!(calls[0].starts_with("POST /prefix/api/v1/query HTTP/1.1"));
        assert!(calls[0].contains("session=old-session"));
        assert!(calls[1].contains("session=new-session"));
        assert_eq!(
            calls[0].split("\r\n\r\n").nth(1),
            calls[1].split("\r\n\r\n").nth(1)
        );
        let refresh = refresh.lock().unwrap();
        assert!(refresh[0].starts_with("POST /orch/api/v1/cli/oauth/token HTTP/1.1"));
        assert!(refresh[0].contains("Bearer release-token"));
        assert!(refresh[0].contains("\"refresh_token\":\"old-refresh\""));
        assert!(!refresh[0].contains("x-p-tenant"));
        assert_eq!(client.profile().session_token, "new-session");
        assert_eq!(
            store.read().unwrap().profiles["cloud"].refresh_token,
            "new-refresh"
        );
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn failed_persistence_retains_rotated_tokens_without_replay() {
        let (workspace, calls, workspace_task) = server(vec![Reply::new(401, "")]);
        let (orchestrator, _, refresh_task) = server(vec![Reply::new(
            200,
            r#"{"access_token":"new-session","refresh_token":"new-refresh"}"#,
        )]);
        let profile = oauth(workspace, orchestrator);
        let mut other = profile.clone();
        other.refresh_token = "different".into();
        let store = store(&other);
        let client =
            ApiClient::with_orchestrator_token(profile, store.clone(), "release-token").unwrap();
        let err = runtime()
            .block_on(client.standard(Method::GET, "about", None))
            .unwrap_err();
        assert!(err.to_string().contains("cloud session refresh failed: failed to persist refreshed cloud tokens: cloud profile for refresh token not found"));
        assert_eq!(client.profile().session_token, "new-session");
        workspace_task.join().unwrap();
        refresh_task.join().unwrap();
        assert_eq!(calls.lock().unwrap().len(), 1);
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn header_timeout_reports_the_current_redirect_url() {
        let mut redirect = Reply::new(302, "");
        redirect.location = Some("/next".into());
        let mut delayed = Reply::new(200, "{}");
        delayed.delay = Duration::from_millis(180);
        let (endpoint, _, task) = server(vec![redirect, delayed]);
        let expected_url = format!("{endpoint}/next");
        let profile = ProfileRecord {
            url: endpoint,
            api_key: "key".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        let error = runtime()
            .block_on(client.request(
                Method::POST,
                &["api/v1/query"],
                Some(b"{}".to_vec()),
                Duration::from_millis(60),
            ))
            .unwrap_err();
        assert!(
            matches!(&error, ApiError::Timeout { method, url, body: false } if *method == Method::POST && *url == expected_url)
        );
        task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn original_deadline_covers_refresh() {
        let (workspace, _, workspace_task) = server(vec![Reply::new(401, "")]);
        let mut slow = Reply::new(
            200,
            r#"{"access_token":"new-session","refresh_token":"new-refresh"}"#,
        );
        slow.delay = Duration::from_millis(150);
        let (orchestrator, _, refresh_task) = server(vec![slow]);
        let profile = oauth(workspace, orchestrator);
        let store = store(&profile);
        let client =
            ApiClient::with_orchestrator_token(profile, store.clone(), "release-token").unwrap();
        let err = runtime()
            .block_on(client.request(
                Method::GET,
                &["api/v1/about"],
                None,
                Duration::from_millis(60),
            ))
            .unwrap_err();
        assert!(err.is_timeout());
        assert_eq!(client.profile().session_token, "old-session");
        workspace_task.join().unwrap();
        refresh_task.join().unwrap();
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn redirect_privacy_matches_go_custom_header_behavior() {
        let (destination, received, destination_task) = server(vec![Reply::new(401, "")]);
        let mut redirect = Reply::new(302, "");
        redirect.location = Some(format!("{destination}/redirected"));
        let (initial, _, initial_task) = server(vec![redirect]);
        // localhost -> numeric loopback changes the hostname, not just port.
        let profile = ProfileRecord {
            url: initial.replace("127.0.0.1", "localhost"),
            cloud: true,
            api_key: "key".into(),
            tenant_id: "tenant".into(),
            ..Default::default()
        };
        let store = store(&profile);
        let client = ApiClient::new(profile, store.clone()).unwrap();
        let response = runtime()
            .block_on(client.standard(Method::GET, "about", None))
            .unwrap();
        assert_eq!(response.status, 401);
        initial_task.join().unwrap();
        destination_task.join().unwrap();
        let received = received.lock().unwrap();
        assert!(received[0].contains("x-api-key: key"));
        assert!(received[0].contains("x-p-tenant: tenant"));
        assert!(!received[0].contains("authorization:"));
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn unrelated_redirect_strips_cookie_and_does_not_refresh() {
        let (destination, received, destination_task) = server(vec![Reply::new(401, "")]);
        let mut redirect = Reply::new(302, "");
        redirect.location = Some(format!("{destination}/redirected"));
        let (initial, _, initial_task) = server(vec![redirect]);
        let profile = oauth(
            initial.replace("127.0.0.1", "localhost"),
            "http://127.0.0.1:9".into(),
        );
        let store = store(&profile);
        let client =
            ApiClient::with_orchestrator_token(profile, store.clone(), "release-token").unwrap();
        assert_eq!(
            runtime()
                .block_on(client.standard(Method::GET, "about", None))
                .unwrap()
                .status,
            401
        );
        initial_task.join().unwrap();
        destination_task.join().unwrap();
        assert!(!received.lock().unwrap()[0].contains("cookie:"));
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
    #[test]
    fn refresh_does_not_retry_second_unauthorized() {
        let (workspace, calls, workspace_task) = server(vec![
            Reply::new(401, ""),
            Reply::new(401, "still unauthorized"),
        ]);
        let (orchestrator, _, refresh_task) = server(vec![Reply::new(
            200,
            r#"{"access_token":"new-session","refresh_token":"new-refresh"}"#,
        )]);
        let profile = oauth(workspace, orchestrator);
        let store = store(&profile);
        let client =
            ApiClient::with_orchestrator_token(profile, store.clone(), "release-token").unwrap();
        assert_eq!(
            runtime()
                .block_on(client.standard(Method::GET, "about", None))
                .unwrap()
                .status,
            401
        );
        workspace_task.join().unwrap();
        refresh_task.join().unwrap();
        assert_eq!(calls.lock().unwrap().len(), 2);
        std::fs::remove_dir_all(store.path().parent().unwrap()).unwrap();
    }
}
