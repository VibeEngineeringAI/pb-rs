//! Dataset administration: creation and optionally type-guarded
//! removal. Mutations succeed only on HTTP 200 and release the success body
//! unread; each request owns its own standard deadline.
use crate::{
    ErrorCode,
    api::{ApiClient, ApiError, StreamingResponse},
    datasets::decode_type,
    error::CliError,
    parser::{ParsedInvocation, quote},
};
use http::{
    Method,
    header::{HeaderName, HeaderValue},
};

const TELEMETRY_TYPE: HeaderName = HeaderName::from_static("x-p-telemetry-type");
const LOG_SOURCE: HeaderName = HeaderName::from_static("x-p-log-source");

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DatasetKind {
    Logs,
    Metrics,
    Traces,
}
impl DatasetKind {
    /// Accepts an already normalized value, as Go validateDatasetType does.
    fn parse(normalized: &str) -> Result<Self, CliError> {
        match normalized {
            "logs" => Ok(Self::Logs),
            "metrics" => Ok(Self::Metrics),
            "traces" => Ok(Self::Traces),
            _ => Err(command_error(format!(
                "invalid dataset type {} (use: logs, metrics, traces)",
                quote(normalized)
            ))),
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Logs => "logs",
            Self::Metrics => "metrics",
            Self::Traces => "traces",
        }
    }
    fn log_source(self) -> Option<&'static str> {
        match self {
            Self::Logs => None,
            Self::Metrics => Some("otel-metrics"),
            Self::Traces => Some("otel-traces"),
        }
    }
}

/// An omitted or blank `dataset add --type` requires the terminal selector.
pub fn deferred(parsed: &ParsedInvocation) -> bool {
    parsed.command_path == "pb dataset add" && requested_type(parsed).is_empty()
}

pub async fn execute(parsed: &ParsedInvocation, client: &ApiClient) -> Result<Vec<u8>, CliError> {
    if deferred(parsed) {
        return Err(command_error(format!(
            "command {} is not implemented in this Rust phase",
            quote(&parsed.command_path)
        )));
    }
    match parsed.command_path.as_str() {
        "pb dataset add" => create(parsed, client).await,
        "pb dataset remove" => remove(parsed, client).await,
        path => Err(command_error(format!(
            "unsupported dataset command {}",
            quote(path)
        ))),
    }
}

async fn create(parsed: &ParsedInvocation, client: &ApiClient) -> Result<Vec<u8>, CliError> {
    let kind = DatasetKind::parse(&requested_type(parsed))?;
    create_selected(client, parsed.positional_bytes(0), kind).await
}

/// Shared mutation for explicit types and the interactive selection. Selection
/// happens before entering the runtime, so terminal input cannot block HTTP.
pub(crate) async fn create_selected(
    client: &ApiClient,
    name: &[u8],
    kind: DatasetKind,
) -> Result<Vec<u8>, CliError> {
    let mut request = client.prepare_standard_bytes(
        Method::PUT,
        &[b"logstream/".as_slice(), name].concat(),
        None,
    )?;
    request.set_header(TELEMETRY_TYPE, HeaderValue::from_static(kind.as_str()));
    if let Some(source) = kind.log_source() {
        request.set_header(LOG_SOURCE, HeaderValue::from_static(source));
    }
    let response = client.send_streaming(request).await?;
    if response.status != 200 {
        return Err(status_failure("create dataset", response).await);
    }
    drop(response);
    use crate::metrics::output::{line_width, style};
    Ok(style::with_process_render(|| {
        let terminal = style::Terminal::from_process();
        [
            format!(
                "Created {} dataset ",
                style::SELECTED.render(kind.as_str(), &terminal, line_width)
            )
            .as_bytes(),
            &style::BOLD.render_bytes(name, &terminal),
            b"\n",
        ]
        .concat()
    }))
}

async fn remove(parsed: &ParsedInvocation, client: &ApiClient) -> Result<Vec<u8>, CliError> {
    let name = parsed.positional_bytes(0);
    let expected = requested_type(parsed);
    // A blank expectation is an unguarded deletion, not a selector.
    if !expected.is_empty() {
        let expected = DatasetKind::parse(&expected)?.as_str();
        let actual = fetch_type(client, name).await?;
        if actual != expected {
            return Err(command_error(format!(
                "dataset {} is {actual}, not {expected}",
                crate::argv::quote(name)
            )));
        }
    }
    let request = client.prepare_standard_bytes(
        Method::DELETE,
        &[b"logstream/".as_slice(), name].concat(),
        None,
    )?;
    let response = client.send_streaming(request).await?;
    if response.status != 200 {
        return Err(status_failure("delete dataset", response).await);
    }
    drop(response);
    use crate::metrics::output::style;
    Ok([
        b"Successfully deleted dataset ".as_slice(),
        &style::BOLD.render_bytes(name, &style::Terminal::from_process()),
        b"\n",
    ]
    .concat())
}

/// Go strings.ToLower(strings.TrimSpace(value)): per-rune lowercase without
/// context-sensitive or multi-rune mappings.
fn requested_type(parsed: &ParsedInvocation) -> String {
    parsed
        .text("type")
        .trim()
        .chars()
        .map(|c| match c {
            '\u{130}' => 'i',
            c => {
                let mut lower = c.to_lowercase();
                match (lower.next(), lower.next()) {
                    (Some(single), None) => single,
                    _ => c,
                }
            }
        })
        .collect()
}

/// Non-200 mutation bodies are read under the same request deadline; read
/// failures surface unwrapped, as Go returns the io.ReadAll error.
async fn status_failure(action: &str, response: StreamingResponse) -> CliError {
    match response.collect().await {
        Ok(response) => CliError::status(
            action,
            response.status,
            &response.status_text(),
            &response.body,
        ),
        Err(error) => error.into(),
    }
}

fn command_error(message: impl Into<String>) -> CliError {
    CliError::new(ErrorCode::CommandFailed, message)
}
/// fmt.Errorf("%s: %w") keeps the wrapped error's classification.
fn wrapped(prefix: &'static str) -> impl Fn(ApiError) -> CliError {
    move |error| {
        let mut error = CliError::from(error);
        error.message = format!("{prefix}: {}", error.message);
        error.public_message = format!("{prefix}: {}", error.public_message);
        error
    }
}

/// Go fetchInfo: GET logstream/NAME/info, reading the body before checking
/// the status, then preferring a nonempty telemetryType over streamType.
async fn fetch_type(client: &ApiClient, name: &[u8]) -> Result<String, CliError> {
    let request = client
        .prepare_standard_bytes(
            Method::GET,
            &[b"logstream/".as_slice(), name, b"/info"].concat(),
            None,
        )
        .map_err(wrapped("failed to create request"))?;
    let response = client
        .send_streaming(request)
        .await
        .map_err(wrapped("request execution failed"))?
        .collect()
        .await
        .map_err(wrapped("failed to read response body"))?;
    match response.status {
        200 => decode_type(&response.body),
        404 => {
            let message = format!("dataset {} not found", crate::argv::quote(name));
            Err(CliError::http(404, &message, &message))
        }
        status => {
            let mut message =
                format!("Request Failed\nStatus Code: {status}\nResponse: ").into_bytes();
            message.extend_from_slice(&response.body);
            message.push(b'\n');
            Err(CliError::http(
                status,
                crate::error::go_string(&message),
                format!("request failed: {}", response.status_text()),
            )
            .with_raw_message(message))
        }
    }
}

/// lipgloss Render without a color profile: tabs widen and multi-line text
/// pads to its widest line.
#[cfg(test)]
fn plain_style(text: &str) -> String {
    use crate::metrics::output::{line_width, style};
    style::BOLD.render(text, &style::Terminal::PLAIN, line_width)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        config::{ProfileRecord, ProfileStore},
        parser::{FlagValue, ParsedFlag},
    };
    use std::{
        collections::BTreeMap,
        io::{Read, Write},
        net::TcpListener,
        sync::{Arc, Mutex, mpsc},
        thread,
        time::{Duration, Instant},
    };

    struct Reply {
        status: &'static str,
        body: &'static str,
        /// Declared length beyond `body`; the tail is withheld until the
        /// client closes the connection.
        stalled_tail: usize,
    }
    fn reply(status: &'static str, body: &'static str) -> Reply {
        Reply {
            status,
            body,
            stalled_tail: 0,
        }
    }
    type Captured = Arc<Mutex<Vec<String>>>;

    fn server(replies: Vec<Reply>) -> (String, Captured, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let captured = Captured::default();
        let requests = captured.clone();
        let task = thread::spawn(move || {
            for reply in replies {
                let (mut stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut bytes = Vec::new();
                let mut buffer = [0; 2048];
                while !bytes.windows(4).any(|w| w == b"\r\n\r\n") {
                    let n = stream.read(&mut buffer).unwrap();
                    assert!(n > 0);
                    bytes.extend_from_slice(&buffer[..n]);
                }
                requests
                    .lock()
                    .unwrap()
                    .push(String::from_utf8(bytes).unwrap());
                let response = format!(
                    "HTTP/1.1 {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    reply.status,
                    reply.body.len() + reply.stalled_tail,
                    reply.body
                );
                stream.write_all(response.as_bytes()).unwrap();
                if reply.stalled_tail > 0 {
                    // Returns once the client releases the unread body.
                    let _ = stream.read(&mut buffer);
                }
            }
        });
        (endpoint, captured, task)
    }

    fn invocation(path: &str, name: &str, kind: Option<&str>) -> ParsedInvocation {
        let mut flags = BTreeMap::new();
        if let Some(kind) = kind {
            flags.insert(
                "type".into(),
                ParsedFlag {
                    value: FlagValue::Text(kind.into()),
                    explicit: true,
                },
            );
        }
        ParsedInvocation {
            raw: Default::default(),
            command_path: path.into(),
            positionals: vec![name.into()],
            flags,
        }
    }

    fn run(url: &str, parsed: &ParsedInvocation) -> Result<Vec<u8>, CliError> {
        let profile = ProfileRecord {
            url: url.into(),
            api_key: "fixture-key".into(),
            ..Default::default()
        };
        // The store is only written on OAuth rotation, which these profiles lack.
        let store = ProfileStore::new(std::path::PathBuf::from("/nonexistent/pb/config.toml"));
        let client = ApiClient::new(profile, store).unwrap();
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(execute(parsed, &client))
    }
    fn header<'a>(request: &'a str, name: &str) -> Option<&'a str> {
        request.lines().find_map(|line| {
            let (key, value) = line.split_once(':')?;
            key.eq_ignore_ascii_case(name).then(|| value.trim())
        })
    }

    #[test]
    fn creation_sends_typed_headers_and_prints_kind() {
        for (input, kind, source) in [
            ("logs", "logs", None),
            (" Metrics\t", "metrics", Some("otel-metrics")),
            ("TRACES", "traces", Some("otel-traces")),
        ] {
            let (url, requests, task) = server(vec![reply("200 OK", "")]);
            let output = run(&url, &invocation("pb dataset add", "app", Some(input))).unwrap();
            task.join().unwrap();
            assert_eq!(output, format!("Created {kind} dataset app\n").into_bytes());
            let requests = requests.lock().unwrap();
            assert_eq!(requests.len(), 1);
            let request = &requests[0];
            assert!(request.starts_with("PUT /api/v1/logstream/app HTTP/1.1\r\n"));
            assert_eq!(header(request, "x-p-telemetry-type"), Some(kind));
            assert_eq!(header(request, "x-p-log-source"), source);
            assert_eq!(header(request, "content-type"), Some("application/json"));
            assert_eq!(header(request, "content-length"), Some("0"));
            assert_eq!(header(request, "x-api-key"), Some("fixture-key"));
        }
    }

    #[test]
    fn invalid_or_missing_types_issue_no_request() {
        // Port 9 is never contacted: validation precedes request construction.
        let error = run(
            "http://127.0.0.1:9",
            &invocation("pb dataset add", "app", Some(" Bogus ")),
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::CommandFailed);
        assert_eq!(
            error.message,
            "invalid dataset type \"bogus\" (use: logs, metrics, traces)"
        );
        assert_eq!(error.http_status, 0);
        let error = run(
            "http://127.0.0.1:9",
            &invocation("pb dataset remove", "app", Some("İ")),
        )
        .unwrap_err();
        assert_eq!(
            error.message,
            "invalid dataset type \"i\" (use: logs, metrics, traces)"
        );
        for kind in [None, Some(""), Some(" \t ")] {
            let parsed = invocation("pb dataset add", "app", kind);
            assert!(deferred(&parsed));
            let error = run("http://127.0.0.1:9", &parsed).unwrap_err();
            assert_eq!(
                error.message,
                "command \"pb dataset add\" is not implemented in this Rust phase"
            );
        }
        assert!(!deferred(&invocation("pb dataset remove", "app", None)));
        assert!(!deferred(&invocation(
            "pb dataset add",
            "app",
            Some("logs")
        )));
    }

    #[test]
    fn mutation_success_is_exactly_200_with_trimmed_text_body() {
        let (url, _, task) = server(vec![reply("201 Created", "  made \n")]);
        let error = run(&url, &invocation("pb dataset add", "app", Some("logs"))).unwrap_err();
        task.join().unwrap();
        assert_eq!(error.code, ErrorCode::CommandFailed);
        assert_eq!(error.http_status, 201);
        assert!(!error.retryable);
        assert_eq!(error.message, "create dataset failed: 201 Created: made");
        assert_eq!(error.public_message, "create dataset failed: 201 Created");

        let (url, _, task) = server(vec![reply("403 Forbidden", "denied")]);
        let error = run(&url, &invocation("pb dataset remove", "app", None)).unwrap_err();
        task.join().unwrap();
        assert_eq!(error.code, ErrorCode::PermissionDenied);
        assert_eq!(
            error.message,
            "delete dataset failed: 403 Forbidden: denied"
        );
        assert_eq!(error.public_message, "delete dataset failed: 403 Forbidden");

        let (url, _, task) = server(vec![reply("503 Service Unavailable", "")]);
        let error = run(&url, &invocation("pb dataset remove", "app", None)).unwrap_err();
        task.join().unwrap();
        assert_eq!(error.code, ErrorCode::ServerError);
        assert!(error.retryable);
        assert_eq!(
            error.message,
            "delete dataset failed: 503 Service Unavailable"
        );
    }

    #[test]
    fn status_only_success_releases_stalled_body_without_reading() {
        for parsed in [
            invocation("pb dataset add", "app", Some("traces")),
            invocation("pb dataset remove", "app", None),
        ] {
            let (url, _, task) = server(vec![Reply {
                status: "200 OK",
                body: "partial",
                stalled_tail: 1 << 20,
            }]);
            let (done, wait) = mpsc::channel();
            let started = Instant::now();
            thread::spawn(move || done.send(run(&url, &parsed)).unwrap());
            let output = wait.recv_timeout(Duration::from_secs(5)).unwrap().unwrap();
            assert!(started.elapsed() < Duration::from_secs(5));
            assert!(output.ends_with(b" app\n"));
            task.join().unwrap();
        }
    }

    #[test]
    fn unguarded_removal_issues_only_delete() {
        for kind in [None, Some(""), Some("  ")] {
            let (url, requests, task) = server(vec![reply("200 OK", "ok")]);
            let output = run(&url, &invocation("pb dataset remove", "app", kind)).unwrap();
            task.join().unwrap();
            assert_eq!(output, b"Successfully deleted dataset app\n");
            let requests = requests.lock().unwrap();
            assert_eq!(requests.len(), 1);
            assert!(requests[0].starts_with("DELETE /api/v1/logstream/app HTTP/1.1\r\n"));
            assert_eq!(
                header(&requests[0], "content-type"),
                Some("application/json")
            );
            assert_eq!(header(&requests[0], "x-p-telemetry-type"), None);
        }
    }

    #[test]
    fn guarded_removal_reads_info_then_deletes_matching_type() {
        for (info, kind) in [
            (
                r#"{"streamType":"logs","telemetryType":"metrics"}"#,
                "metrics",
            ),
            (r#"{"streamType":"traces","telemetryType":""}"#, "traces"),
            (r#"{"streamType":"traces","telemetryType":null}"#, "traces"),
            (r#"{"STREAMTYPE":"logs","unknown":[1]}"#, "logs"),
            (r#"{"telemetryType":"logs","telemetryType":null}"#, "logs"),
        ] {
            let (url, requests, task) = server(vec![reply("200 OK", info), reply("200 OK", "")]);
            let output = run(&url, &invocation("pb dataset remove", "app", Some(kind))).unwrap();
            task.join().unwrap();
            assert_eq!(output, b"Successfully deleted dataset app\n", "{info}");
            let requests = requests.lock().unwrap();
            assert!(requests[0].starts_with("GET /api/v1/logstream/app/info HTTP/1.1\r\n"));
            assert!(requests[1].starts_with("DELETE /api/v1/logstream/app HTTP/1.1\r\n"));
        }
    }

    #[test]
    fn guarded_removal_stops_before_delete_on_mismatch_or_info_failure() {
        let cases: [(&str, &str, ErrorCode, u16, &str, &str); 6] = [
            (
                "200 OK",
                r#"{"streamType":"logs"}"#,
                ErrorCode::CommandFailed,
                0,
                "dataset \"app\" is logs, not metrics",
                "dataset \"app\" is logs, not metrics",
            ),
            (
                "200 OK",
                "null",
                ErrorCode::CommandFailed,
                0,
                "dataset \"app\" is , not metrics",
                "dataset \"app\" is , not metrics",
            ),
            (
                "404 Not Found",
                "missing",
                ErrorCode::NotFound,
                404,
                "dataset \"app\" not found",
                "dataset \"app\" not found",
            ),
            (
                "500 Internal Server Error",
                "boom",
                ErrorCode::ServerError,
                500,
                "Request Failed\nStatus Code: 500\nResponse: boom\n",
                "request failed: 500 Internal Server Error",
            ),
            (
                "200 OK",
                r#"{"telemetryType":7}"#,
                ErrorCode::CommandFailed,
                0,
                "failed to unmarshal response: json: cannot unmarshal number into Go struct field .telemetryType of type string",
                "failed to unmarshal response: json: cannot unmarshal number into Go struct field .telemetryType of type string",
            ),
            (
                "200 OK",
                "[]",
                ErrorCode::CommandFailed,
                0,
                "failed to unmarshal response: json: cannot unmarshal array into Go value of type struct { StreamType string \"json:\\\"streamType\\\"\"; TelemetryType string \"json:\\\"telemetryType\\\"\" }",
                "failed to unmarshal response: json: cannot unmarshal array into Go value of type struct { StreamType string \"json:\\\"streamType\\\"\"; TelemetryType string \"json:\\\"telemetryType\\\"\" }",
            ),
        ];
        for (status, body, code, http_status, message, public) in cases {
            // Only one reply: a DELETE would fail to connect and change the error.
            let (url, requests, task) = server(vec![reply(status, body)]);
            let error = run(
                &url,
                &invocation("pb dataset remove", "app", Some("metrics")),
            )
            .unwrap_err();
            task.join().unwrap();
            assert_eq!(requests.lock().unwrap().len(), 1);
            assert_eq!(error.code, code, "{body}");
            assert_eq!(error.http_status, http_status);
            assert_eq!(error.message, message);
            assert_eq!(error.public_message, public);
        }
    }

    #[test]
    fn info_transport_failure_keeps_classification_under_go_prefix() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        drop(listener);
        let error = run(&url, &invocation("pb dataset remove", "app", Some("logs"))).unwrap_err();
        assert_eq!(error.code, ErrorCode::ConnectionFailed);
        assert!(error.retryable);
        assert!(
            error.message.starts_with(&format!(
                "request execution failed: Get \"{url}/api/v1/logstream/app/info\": "
            )),
            "{}",
            error.message
        );
        assert_eq!(error.message, error.public_message);
    }

    #[test]
    fn names_render_like_lipgloss_without_color() {
        assert_eq!(plain_style("a\tb"), "a    b");
        assert_eq!(plain_style("ab\r\nc"), "ab\nc ");
        assert_eq!(plain_style("界\nab c"), "界  \nab c");
    }
}
