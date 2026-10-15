//! Parseable Cloud device authorization for `pb login` (cmd/cloud.go
//! cloudProfileFromDeviceLogin): device code, inline waiting screen with a
//! delayed browser launch, token polling, and profile derivation.
use crate::{
    ErrorCode,
    api::{ApiError, ApiResponse, CLOUD_TIMEOUT, CloudAuthClient},
    config::ProfileRecord,
    error::CliError,
    gojson::Node,
    tui::{
        input::Event,
        profile::{PROGRAM_KILLED, adaptive, caught_panic_stdout},
        style::Role,
        terminal::{Screen, TerminalSession},
    },
};
use std::{sync::Arc, time::Duration};
use tokio::{
    task::{JoinError, JoinHandle},
    time::{Instant, sleep_until},
};

const CLIENT_ID: &str = "pb-cli";
/// cloudDefaultPollInterval / cloudDefaultLoginTimeout in nanoseconds.
const DEFAULT_POLL_INTERVAL_NANOS: i64 = 5_000_000_000;
const DEFAULT_LOGIN_TIMEOUT_NANOS: i64 = 300_000_000_000;

/// `time.Duration(n) * time.Second`: Go int64 nanoseconds wrap on overflow.
fn go_seconds(n: i64) -> i64 {
    n.wrapping_mul(1_000_000_000)
}
/// Login context timeout: response expiry when positive, else five minutes.
fn login_timeout_nanos(expires_in: i64) -> i64 {
    if expires_in > 0 {
        go_seconds(expires_in)
    } else {
        DEFAULT_LOGIN_TIMEOUT_NANOS
    }
}
/// Poll interval: response interval when positive, else five seconds. A
/// wrapped nonpositive value makes Go's timer fire immediately.
fn poll_interval_nanos(interval: i64) -> i64 {
    if interval > 0 {
        go_seconds(interval)
    } else {
        DEFAULT_POLL_INTERVAL_NANOS
    }
}
fn nonnegative(nanos: i64) -> Duration {
    Duration::from_nanos(u64::try_from(nanos).unwrap_or(0))
}
/// context.WithTimeout: a nonpositive timeout is already expired.
fn context_deadline(now: Instant, timeout_nanos: i64) -> Instant {
    if timeout_nanos <= 0 {
        return now;
    }
    now.checked_add(nonnegative(timeout_nanos))
        .unwrap_or_else(|| now + Duration::from_secs(u64::from(u32::MAX)))
}
const BROWSER_DELAY: Duration = Duration::from_secs(5);
/// bubbles spinner.Dot.
const SPINNER: [&str; 8] = ["⣾ ", "⣽ ", "⣻ ", "⢿ ", "⡿ ", "⣟ ", "⣯ ", "⣷ "];
const SPINNER_FRAME: Duration = Duration::from_millis(100);

fn failed(message: impl Into<String>) -> CliError {
    CliError::new(ErrorCode::CommandFailed, message)
}

// ── Wire decoding with encoding/json semantics ─────────────────────────────

#[derive(Clone, Copy)]
enum Kind {
    Str,
    Int,
}

#[derive(Clone, Debug, PartialEq)]
enum Field {
    Str(String),
    Int(i64),
}

/// Decode `node` into the named Go struct fields: case-insensitive keys,
/// later keys win, nulls leave the zero value, unknown keys ignored, and
/// the first type mismatch in document order is the error.
fn decode_fields(
    node: &Node,
    path: &str,
    fields: &[(&str, Kind)],
) -> Result<Vec<Option<Field>>, String> {
    let mut values = vec![None; fields.len()];
    let mut first_error = None;
    for (key, value) in node.as_object().unwrap_or_default() {
        match decode_entry(key, value, path, fields) {
            Some(Ok((index, field))) => values[index] = Some(field),
            Some(Err(error)) if first_error.is_none() => first_error = Some(error),
            _ => {}
        }
    }
    first_error.map_or(Ok(values), Err)
}

/// One object member: None when it maps to no field or is null.
fn decode_entry(
    key: &str,
    value: &Node,
    path: &str,
    fields: &[(&str, Kind)],
) -> Option<Result<(usize, Field), String>> {
    let index = fields
        .iter()
        .position(|(name, _)| name.eq_ignore_ascii_case(key))?;
    if value.is_null() {
        return None;
    }
    let (name, kind) = fields[index];
    let decoded = match kind {
        Kind::Str => value.as_str().map(|s| Field::Str(s.to_owned())),
        Kind::Int if value.is_number() => value.as_i64().map(Field::Int),
        Kind::Int => None,
    };
    Some(decoded.map(|field| (index, field)).ok_or_else(|| {
        let (go_type, found) = match kind {
            Kind::Str => ("string", value.kind().to_owned()),
            Kind::Int if value.is_number() => ("int", format!("number {value}")),
            Kind::Int => ("int", value.kind().to_owned()),
        };
        format!(
            "json: cannot unmarshal {found} into Go struct field {path}.{name} of type {go_type}"
        )
    }))
}

fn document(body: &[u8], type_name: &str) -> Result<crate::gojson::OrderedDocument, String> {
    let value = crate::gojson::decode_ordered(body)?;
    if !value.is_object() && !value.is_null() {
        return Err(format!(
            "json: cannot unmarshal {} into Go value of type cmd.{type_name}",
            value.kind()
        ));
    }
    Ok(value)
}

fn text(values: &[Option<Field>], index: usize) -> String {
    match &values[index] {
        Some(Field::Str(s)) => s.clone(),
        _ => String::new(),
    }
}
fn int(values: &[Option<Field>], index: usize) -> i64 {
    match values[index] {
        Some(Field::Int(n)) => n,
        _ => 0,
    }
}

#[derive(Debug, Default, PartialEq)]
struct DeviceCode {
    device_code: String,
    user_code: String,
    verification_uri: String,
    expires_in: i64,
    interval: i64,
}

fn decode_device_code(body: &[u8]) -> Result<DeviceCode, String> {
    let doc = document(body, "cloudDeviceCodeResponse")?;
    let v = decode_fields(
        &doc,
        "cloudDeviceCodeResponse",
        &[
            ("device_code", Kind::Str),
            ("user_code", Kind::Str),
            ("verification_uri", Kind::Str),
            ("verification_uri_complete", Kind::Str),
            ("expires_in", Kind::Int),
            ("interval", Kind::Int),
        ],
    )?;
    Ok(DeviceCode {
        device_code: text(&v, 0),
        user_code: text(&v, 1),
        verification_uri: text(&v, 2),
        expires_in: int(&v, 4),
        interval: int(&v, 5),
    })
}

#[derive(Debug, Default, PartialEq)]
pub(crate) struct Tokens {
    access_token: String,
    refresh_token: String,
    workspace_id: String,
    workspace_url: String,
    tenant_id: String,
}

fn decode_tokens(body: &[u8]) -> Result<Tokens, String> {
    const TYPE: &str = "cloudDeviceTokenResponse";
    const OUTER: [(&str, Kind); 5] = [
        ("access_token", Kind::Str),
        ("token_type", Kind::Str),
        ("refresh_token", Kind::Str),
        ("expires_in", Kind::Int),
        ("id_token", Kind::Str),
    ];
    const WORKSPACE: [(&str, Kind); 3] = [
        ("id", Kind::Str),
        ("url", Kind::Str),
        ("tenant_id", Kind::Str),
    ];
    let doc = document(body, TYPE)?;
    let mut outer = vec![None; OUTER.len()];
    let mut workspace = vec![None; WORKSPACE.len()];
    let mut first_error = None;
    // Members decode in document order, nested workspace included; a
    // repeated workspace object merges into the same struct.
    for (key, value) in doc.as_object().unwrap_or_default() {
        let result = if key.eq_ignore_ascii_case("workspace") {
            if value.is_null() {
                continue;
            }
            if value.is_object() {
                let path = format!("{TYPE}.workspace");
                let mut nested_error = None;
                for (key, value) in value.as_object().unwrap_or_default() {
                    match decode_entry(key, value, &path, &WORKSPACE) {
                        Some(Ok((index, field))) => workspace[index] = Some(field),
                        Some(Err(error)) if nested_error.is_none() => nested_error = Some(error),
                        _ => {}
                    }
                }
                nested_error.map_or(Ok(()), Err)
            } else {
                Err(format!(
                    "json: cannot unmarshal {} into Go struct field {TYPE}.workspace of type cmd.cloudDeviceWorkspaceRef",
                    value.kind()
                ))
            }
        } else {
            match decode_entry(key, value, TYPE, &OUTER) {
                Some(Ok((index, field))) => {
                    outer[index] = Some(field);
                    Ok(())
                }
                Some(Err(error)) => Err(error),
                None => Ok(()),
            }
        };
        if let Err(error) = result
            && first_error.is_none()
        {
            first_error = Some(error);
        }
    }
    if let Some(error) = first_error {
        return Err(error);
    }
    Ok(Tokens {
        access_token: text(&outer, 0),
        refresh_token: text(&outer, 2),
        workspace_id: text(&workspace, 0),
        workspace_url: text(&workspace, 1),
        tenant_id: text(&workspace, 2),
    })
}

/// Non-2xx token body: cloudOAuthError, or None when it does not decode or
/// carries no `error`.
fn decode_oauth_error(body: &[u8]) -> Option<(String, String)> {
    let doc = document(body, "cloudOAuthError").ok()?;
    let v = decode_fields(
        &doc,
        "cloudOAuthError",
        &[("error", Kind::Str), ("error_description", Kind::Str)],
    )
    .ok()?;
    let error = text(&v, 0);
    (!error.is_empty()).then(|| (error, text(&v, 1)))
}

/// json.Marshal string escaping for request payloads.
fn go_json(value: &serde_json::Value) -> Vec<u8> {
    crate::profile::go_json_escape(value.to_string()).into_bytes()
}

fn trimmed_body(response: &ApiResponse) -> String {
    String::from_utf8_lossy(&response.body).trim().to_owned()
}

/// Errors Go's http.Client.Do returns (url.Error) versus later body reads.
fn is_request_error(error: &ApiError) -> bool {
    matches!(
        error,
        ApiError::Request { .. } | ApiError::Timeout { body: false, .. }
    )
}

// ── Network effects ─────────────────────────────────────────────────────────

async fn request_device_code(
    client: &CloudAuthClient,
    orchestrator_url: &str,
) -> Result<DeviceCode, CliError> {
    let body = go_json(&serde_json::json!({ "client_id": CLIENT_ID }));
    let prefix = "failed to create cloud device code";
    let response = match client
        .post_json(
            orchestrator_url,
            "api/v1/cli/device/code",
            body,
            CLOUD_TIMEOUT,
            None,
        )
        .await
    {
        Ok(response) => response,
        // url.JoinPath fails before doCloudJSON and is returned unprefixed.
        Err(error @ ApiError::Context { .. }) => return Err(failed(error.to_string())),
        Err(error) => return Err(failed(format!("{prefix}: {error}"))),
    };
    if !(200..300).contains(&response.status) {
        return Err(failed(format!(
            "{prefix}: {}: {}",
            response.status_text(),
            trimmed_body(&response)
        )));
    }
    let device = decode_device_code(&response.body)
        .map_err(|e| failed(format!("{prefix}: failed to decode response: {e}")))?;
    if device.device_code.is_empty() || device.user_code.is_empty() {
        return Err(failed(
            "cloud device authorization response missing device_code or user_code",
        ));
    }
    Ok(device)
}

/// pollCloudDeviceToken: wait first, then POST; only authorization_pending
/// repeats. `deadline` is the login context (expiry or five minutes). Runs
/// as an owned task (Go: a command goroutine), so it owns its inputs.
async fn poll_tokens(
    client: Arc<CloudAuthClient>,
    orchestrator_url: String,
    device_code: String,
    interval: Duration,
    deadline: Instant,
) -> Result<Tokens, String> {
    let orchestrator_url = orchestrator_url.as_str();
    let payload = go_json(&serde_json::json!({
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
    }));
    loop {
        let wake = Instant::now().checked_add(interval);
        match wake {
            Some(wake) if wake < deadline => sleep_until(wake).await,
            _ => {
                sleep_until(deadline).await;
                return Err("cloud device login expired or timed out".into());
            }
        }
        let response = match client
            .post_json(
                orchestrator_url,
                "api/v1/cli/oauth/token",
                payload.clone(),
                CLOUD_TIMEOUT,
                Some(deadline),
            )
            .await
        {
            Ok(response) => response,
            Err(error) if is_request_error(&error) => {
                return Err(format!("cloud token request failed: {error}"));
            }
            Err(error) => return Err(error.to_string()),
        };
        if (200..300).contains(&response.status) {
            return decode_tokens(&response.body)
                .map_err(|e| format!("failed to decode cloud token response: {e}"));
        }
        let Some((error, description)) = decode_oauth_error(&response.body) else {
            return Err(format!(
                "cloud token request failed: {}: {}",
                response.status_text(),
                trimmed_body(&response)
            ));
        };
        match error.as_str() {
            "authorization_pending" => continue,
            "expired_token" => return Err("cloud device code expired; run login again".into()),
            _ if description.is_empty() => {
                return Err(format!("cloud device login failed: {error}"));
            }
            _ => return Err(format!("cloud device login failed: {error}: {description}")),
        }
    }
}

/// openExternalBrowser: started, never waited; output to the null device.
fn open_browser(url: &str) {
    use std::process::{Command, Stdio};
    let mut command = if cfg!(target_os = "macos") {
        let mut c = Command::new("open");
        c.arg(url);
        c
    } else if cfg!(windows) {
        let mut c = Command::new("rundll32");
        c.args(["url.dll,FileProtocolHandler", url]);
        c
    } else {
        let mut c = Command::new("xdg-open");
        c.arg(url);
        c
    };
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    crate::process::restore_child_limits(&mut command);
    let _ = command.spawn();
}

// ── Waiting screen (cloudDeviceLoginModel) ─────────────────────────────────

#[derive(Debug, PartialEq)]
pub(crate) enum Outcome {
    Waiting,
    Authorized(Tokens),
    Failed(String),
    /// tea.QuitMsg without a result (external signal): Go returns a nil
    /// error and nil tokens.
    Quit,
}

pub(crate) struct DeviceScreen {
    verification_url: String,
    user_code: String,
    frame: usize,
    outcome: Outcome,
}

impl DeviceScreen {
    pub(crate) fn new(verification_url: String, user_code: String) -> Self {
        Self {
            verification_url,
            user_code,
            frame: 0,
            outcome: Outcome::Waiting,
        }
    }

    fn done(&self) -> bool {
        self.outcome != Outcome::Waiting
    }

    /// Ctrl+C cancels the login context.
    pub(crate) fn key(&mut self, name: &str) {
        if name == "ctrl+c" {
            self.outcome = Outcome::Failed("cloud device login aborted".into());
        }
    }

    pub(crate) fn polled(&mut self, result: Result<Tokens, String>) {
        self.outcome = match result {
            Ok(tokens) => Outcome::Authorized(tokens),
            Err(error) => Outcome::Failed(error),
        };
    }

    pub(crate) fn view(&self) -> String {
        let status = match &self.outcome {
            // tea.QuitMsg (signal) ends the program without touching the
            // model, so `done` stays false and the waiting line remains.
            Outcome::Waiting | Outcome::Quit => format!(
                "{} Waiting for authorization... [Ctrl+C to abort]",
                SPINNER[self.frame % SPINNER.len()]
            ),
            Outcome::Authorized(_) => "✓ Authorization successful!".into(),
            Outcome::Failed(_) => "✗ Authorization failed.".into(),
        };
        format!(
            "\n  {}\n  {}\n\n  To authenticate, please visit:\n  → {}\n\n  Enter this Confirmation Code:\n  ❯ {}\n\n  {}\n",
            adaptive(Role::Accent).bold(true).render("PARSEABLE LOGIN"),
            "─".repeat(60),
            self.verification_url,
            self.user_code,
            status,
        )
    }
}

enum Wake {
    Event(Option<Event>),
    Polled(Result<Result<Tokens, String>, JoinError>),
    Frame,
    Browser,
}

/// The login context's token poll (Go: the model's poll command running in
/// its own goroutine under `ctx`). Canceling aborts the task, which drops the
/// in-flight request; `finish` also joins it. Dropping without `finish`
/// still aborts, so no poll outlives the screen.
struct PollTask {
    handle: Option<JoinHandle<Result<Tokens, String>>>,
}

impl PollTask {
    fn spawn(
        client: Arc<CloudAuthClient>,
        orchestrator_url: String,
        device_code: String,
        interval: Duration,
        deadline: Instant,
    ) -> Self {
        Self {
            handle: Some(tokio::spawn(poll_tokens(
                client,
                orchestrator_url,
                device_code,
                interval,
                deadline,
            ))),
        }
    }
    /// context.CancelFunc: no result is consumed after this.
    fn cancel(&self) {
        if let Some(handle) = &self.handle {
            handle.abort();
        }
    }
    /// Deferred cancel at the end of cloudProfileFromDeviceLogin, then join.
    async fn finish(mut self) {
        if let Some(handle) = self.handle.take() {
            handle.abort();
            let _ = handle.await;
        }
    }
}

impl Drop for PollTask {
    fn drop(&mut self) {
        self.cancel();
    }
}

/// Go prints a recovered panic value with `%s`.
fn panic_text(error: JoinError) -> String {
    let payload = error.into_panic();
    if let Some(text) = payload.downcast_ref::<&str>() {
        (*text).to_owned()
    } else if let Some(text) = payload.downcast_ref::<String>() {
        text.clone()
    } else {
        "Box<dyn Any>".to_owned()
    }
}

/// A background panic: the session is (or is now) restored on the kill path,
/// recoverFromPanic's text goes to stdout and Run returns ErrProgramKilled.
fn killed(session: TerminalSession, value: &str) -> CliError {
    drop(session);
    use std::io::Write;
    let mut stdout = std::io::stdout();
    let _ = stdout.write_all(caught_panic_stdout(value).as_bytes());
    let _ = stdout.flush();
    failed(format!(
        "failed to render cloud device login: {PROGRAM_KILLED}"
    ))
}

async fn wait_screen(
    client: Arc<CloudAuthClient>,
    orchestrator_url: &str,
    device: &DeviceCode,
    verification_url: String,
    deadline: Instant,
) -> Result<Outcome, CliError> {
    let interval = nonnegative(poll_interval_nanos(device.interval));
    let mut session = TerminalSession::open(Screen::Inline)
        .map_err(|e| failed(format!("failed to render cloud device login: {e}")))?;
    let mut screen = DeviceScreen::new(verification_url.clone(), device.user_code.clone());
    let started = Instant::now();
    // Init batches spinner.Model.Tick, which returns its TickMsg at once:
    // frame 1 follows the initial render and later frames come every 100 ms.
    let mut next_frame = started;
    // waitForCloudPoll(ctx, 5s) loses to an earlier context deadline.
    let mut browser_at = (started + BROWSER_DELAY < deadline).then_some(started + BROWSER_DELAY);
    // Started with Init, after the first render is queued (the task first
    // runs when this driver next yields).
    let mut poll = PollTask::spawn(
        client,
        orchestrator_url.to_owned(),
        device.device_code.clone(),
        interval,
        deadline,
    );
    session.draw(&screen.view());
    while !screen.done() {
        let wake = {
            let mut event = std::pin::pin!(session.next_event());
            let mut frame = std::pin::pin!(sleep_until(next_frame));
            let mut browser = std::pin::pin!(async {
                match browser_at {
                    Some(at) => sleep_until(at).await,
                    None => std::future::pending().await,
                }
            });
            let handle = &mut poll.handle;
            std::future::poll_fn(|cx| {
                use std::task::Poll::Ready;
                if let Ready(event) = event.as_mut().poll(cx) {
                    return Ready(Wake::Event(event));
                }
                // A JoinHandle is polled only until it yields its result.
                if let Some(task) = handle.as_mut()
                    && let Ready(result) = std::pin::Pin::new(task).poll(cx)
                {
                    *handle = None;
                    return Ready(Wake::Polled(result));
                }
                if let Ready(()) = frame.as_mut().poll(cx) {
                    return Ready(Wake::Frame);
                }
                if let Ready(()) = browser.as_mut().poll(cx) {
                    return Ready(Wake::Browser);
                }
                std::task::Poll::Pending
            })
            .await
        };
        match wake {
            Wake::Event(Some(Event::Key(key))) => {
                screen.key(&key.name());
                if screen.done() {
                    // Ctrl+C: Update calls cancel() before tea.Quit runs.
                    poll.cancel();
                }
            }
            Wake::Event(Some(Event::InputError(message))) => {
                // Read errors end Run gracefully: final render, then close.
                let _ = session.close(&screen.view());
                poll.finish().await;
                return Err(failed(format!(
                    "failed to render cloud device login: {message}"
                )));
            }
            Wake::Event(Some(Event::Panicked(value))) => {
                // Background panic reported by the session (already restored).
                let error = killed(session, &value);
                poll.finish().await;
                return Err(error);
            }
            Wake::Event(Some(Event::Quit) | None) => screen.outcome = Outcome::Quit,
            Wake::Event(Some(Event::Resize(_) | Event::Unknown)) => {}
            Wake::Polled(Ok(result)) => screen.polled(result),
            Wake::Polled(Err(error)) if error.is_panic() => {
                // The poll command panicked: Go's goroutine recoverFromPanic.
                return Err(killed(session, &panic_text(error)));
            }
            // Cancellation is only requested once the screen is done.
            Wake::Polled(Err(_)) => {}
            Wake::Frame => {
                screen.frame += 1;
                // tea.Tick schedules from the time the tick is handled.
                next_frame = Instant::now() + SPINNER_FRAME;
            }
            Wake::Browser => {
                browser_at = None;
                open_browser(&verification_url);
            }
        }
        session.draw(&screen.view());
    }
    let _ = session.close(&screen.view());
    // Go's deferred cancel runs when cloudProfileFromDeviceLogin returns,
    // after Run has restored the terminal.
    poll.finish().await;
    Ok(screen.outcome)
}

/// cloudProfileFromDeviceTokens.
fn profile_from_tokens(
    orchestrator_url: &str,
    tokens: Option<Tokens>,
) -> Result<ProfileRecord, CliError> {
    let tokens = tokens.unwrap_or_default();
    if tokens.access_token.trim().is_empty() {
        return Err(failed("cloud token response missing access_token"));
    }
    if tokens.refresh_token.trim().is_empty() {
        return Err(failed("cloud token response missing refresh_token"));
    }
    if tokens.workspace_id.trim().is_empty()
        || tokens.workspace_url.trim().is_empty()
        || tokens.tenant_id.trim().is_empty()
    {
        return Err(failed(
            "cloud token response missing workspace id, url, or tenant_id",
        ));
    }
    Ok(ProfileRecord {
        url: tokens.workspace_url,
        cloud: true,
        session_token: tokens.access_token,
        refresh_token: tokens.refresh_token,
        tenant_id: tokens.tenant_id,
        workspace_id: tokens.workspace_id,
        orchestrator_url: orchestrator_url.to_owned(),
        ..Default::default()
    })
}

/// cloudProfileFromDeviceLogin. The device code is requested before any
/// terminal state changes.
pub(crate) fn login(
    runtime: &tokio::runtime::Runtime,
    orchestrator_url: &str,
) -> Result<ProfileRecord, CliError> {
    if orchestrator_url.trim().is_empty() {
        return Err(failed("cloud orchestrator URL is not configured"));
    }
    // Shared with the poll task, which outlives no part of this call.
    let client = Arc::new(CloudAuthClient::new()?);
    runtime.block_on(async {
        let device = request_device_code(&client, orchestrator_url).await?;
        let verification_url = device.verification_uri.trim().to_owned();
        if verification_url.is_empty() {
            return Err(failed(
                "cloud device authorization response missing verification URI",
            ));
        }
        let deadline = context_deadline(Instant::now(), login_timeout_nanos(device.expires_in));
        match wait_screen(
            client,
            orchestrator_url,
            &device,
            verification_url,
            deadline,
        )
        .await?
        {
            Outcome::Authorized(tokens) => profile_from_tokens(orchestrator_url, Some(tokens)),
            Outcome::Failed(error) => Err(failed(error)),
            Outcome::Quit | Outcome::Waiting => profile_from_tokens(orchestrator_url, None),
        }
    })
}

/// cloudProfileNameFromSession.
pub(crate) fn profile_name_from_session(profile: &ProfileRecord) -> String {
    let mut name = profile.workspace_name.trim();
    if name.is_empty() {
        name = profile.workspace_id.trim();
    }
    if name.is_empty() {
        name = "cloud";
    }
    name.to_lowercase().replace(' ', "-")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expiry_and_interval_use_wrapping_go_durations() {
        // 9999999999 s * 1e9 wraps int64 to a negative duration.
        assert_eq!(go_seconds(9_999_999_999), -8_446_744_074_709_551_616);
        assert_eq!(
            login_timeout_nanos(9_999_999_999),
            -8_446_744_074_709_551_616
        );
        let now = Instant::now();
        assert_eq!(
            context_deadline(now, login_timeout_nanos(9_999_999_999)),
            now
        );
        // A wrapped negative interval polls immediately.
        assert_eq!(
            nonnegative(poll_interval_nanos(9_999_999_999)),
            Duration::ZERO
        );
        // Nonpositive response values select the defaults before multiplying.
        assert_eq!(login_timeout_nanos(0), 300_000_000_000);
        assert_eq!(login_timeout_nanos(-1), 300_000_000_000);
        assert_eq!(poll_interval_nanos(-7), 5_000_000_000);
        assert_eq!(nonnegative(poll_interval_nanos(2)), Duration::from_secs(2));
        // The largest non-wrapping value stays a far-future deadline.
        let far = context_deadline(now, login_timeout_nanos(9_223_372_036));
        assert_eq!(far - now, Duration::from_secs(9_223_372_036));
        assert_eq!(context_deadline(now, 0), now);
    }

    #[test]
    fn device_code_decoding_follows_encoding_json() {
        let code = decode_device_code(
            br#"{"Device_Code":"d","user_code":"u","user_code":null,"verification_uri":"v","expires_in":9,"interval":2,"extra":[1]}"#,
        )
        .unwrap();
        assert_eq!(
            code,
            DeviceCode {
                device_code: "d".into(),
                user_code: "u".into(),
                verification_uri: "v".into(),
                expires_in: 9,
                interval: 2,
            }
        );
        assert_eq!(
            decode_device_code(br#"{"interval":1.5,"device_code":3}"#).unwrap_err(),
            "json: cannot unmarshal number 1.5 into Go struct field cloudDeviceCodeResponse.interval of type int"
        );
        assert_eq!(
            decode_device_code(b"[]").unwrap_err(),
            "json: cannot unmarshal array into Go value of type cmd.cloudDeviceCodeResponse"
        );
        assert_eq!(decode_device_code(b"null").unwrap(), DeviceCode::default());
    }

    #[test]
    fn token_and_oauth_error_decoding() {
        let tokens = decode_tokens(
            br#"{"access_token":"a","refresh_token":"r","workspace":{"id":"w","url":"https://x","tenant_id":"t"}}"#,
        )
        .unwrap();
        assert_eq!(tokens.workspace_url, "https://x");
        assert_eq!(
            decode_tokens(br#"{"workspace":{"id":1},"access_token":2}"#).unwrap_err(),
            "json: cannot unmarshal number into Go struct field cloudDeviceTokenResponse.workspace.id of type string"
        );
        assert_eq!(
            decode_tokens(br#"{"access_token":2,"workspace":"x"}"#).unwrap_err(),
            "json: cannot unmarshal number into Go struct field cloudDeviceTokenResponse.access_token of type string"
        );
        assert_eq!(
            decode_oauth_error(br#"{"error":"slow_down","error_description":"later"}"#),
            Some(("slow_down".into(), "later".into()))
        );
        assert_eq!(decode_oauth_error(br#"{"error":""}"#), None);
        assert_eq!(decode_oauth_error(br#"{"error":1}"#), None);
        assert_eq!(decode_oauth_error(b"oops"), None);
    }

    #[test]
    fn tokens_must_carry_session_and_workspace() {
        let full = || Tokens {
            access_token: "a".into(),
            refresh_token: "r".into(),
            workspace_id: "w".into(),
            workspace_url: "https://x".into(),
            tenant_id: "t".into(),
        };
        let profile = profile_from_tokens("https://o", Some(full())).unwrap();
        assert!(
            profile.cloud
                && profile.session_token == "a"
                && profile.orchestrator_url == "https://o"
        );
        assert!(profile.api_key.is_empty() && profile.workspace_name.is_empty());
        let missing = |tokens| profile_from_tokens("o", tokens).unwrap_err().message;
        assert_eq!(missing(None), "cloud token response missing access_token");
        assert_eq!(
            missing(Some(Tokens {
                refresh_token: " ".into(),
                ..full()
            })),
            "cloud token response missing refresh_token"
        );
        assert_eq!(
            missing(Some(Tokens {
                tenant_id: String::new(),
                ..full()
            })),
            "cloud token response missing workspace id, url, or tenant_id"
        );
        assert_eq!(
            profile_name_from_session(&ProfileRecord {
                workspace_id: " My WS ".into(),
                ..Default::default()
            }),
            "my-ws"
        );
        assert_eq!(
            profile_name_from_session(&ProfileRecord::default()),
            "cloud"
        );
    }

    #[test]
    fn screen_states_and_ctrl_c() {
        let mut screen = DeviceScreen::new("https://v".into(), "ABCD".into());
        let waiting = screen.view();
        assert!(waiting.contains("  → https://v\n") && waiting.contains("  ❯ ABCD\n"));
        assert!(waiting.ends_with("⣾  Waiting for authorization... [Ctrl+C to abort]\n"));
        screen.key("q");
        assert!(!screen.done());
        screen.key("ctrl+c");
        assert_eq!(
            screen.outcome,
            Outcome::Failed("cloud device login aborted".into())
        );
        assert!(screen.view().ends_with("  ✗ Authorization failed.\n"));
        let mut screen = DeviceScreen::new("u".into(), "c".into());
        screen.polled(Ok(Tokens::default()));
        assert!(screen.view().ends_with("  ✓ Authorization successful!\n"));
        // A signal QuitMsg leaves the model untouched: the waiting line stays.
        let mut screen = DeviceScreen::new("u".into(), "c".into());
        screen.frame = 1;
        screen.outcome = Outcome::Quit;
        assert!(screen.done());
        assert!(
            screen
                .view()
                .ends_with("⣽  Waiting for authorization... [Ctrl+C to abort]\n")
        );
    }
}
