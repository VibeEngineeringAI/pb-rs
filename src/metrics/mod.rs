//! Buffered Prometheus operations with explicit invocation and transport policy.
pub(crate) mod output;
pub(crate) use output::go_float;
mod raw_json;
pub(crate) fn initialize_process_theme() {
    output::style::initialize_process_theme();
}

pub(crate) fn has_dark_background() -> bool {
    output::style::process_dark()
}

use crate::{
    ErrorCode, Outcome,
    api::{ApiClient, ApiError},
    config::ProfileStore,
    error::CliError,
    parser::{FlagValue, ParsedInvocation},
    spec::CommandSpec,
    time_compat,
};
use std::io::Write;

pub(crate) fn is_operation(path: &str) -> bool {
    matches!(
        path,
        "pb promql run"
            | "pb promql labels"
            | "pb promql label-values"
            | "pb promql series"
            | "pb promql cardinality label-names"
            | "pb promql cardinality label-values"
            | "pb promql cardinality active-series"
            | "pb promql active-queries"
            | "pb promql tsdb"
    )
}

struct Operation {
    endpoint: String,
    pairs: Vec<(String, String)>,
}

fn invocation(parsed: &ParsedInvocation, operation: &str) -> Result<Operation, CliError> {
    let positional = |i| parsed.positionals.get(i).map(String::as_str);
    let mut pairs = Vec::new();
    let mut set = |key: &str, value: &str| pairs.push((key.to_owned(), value.to_owned()));
    let endpoint = match operation {
        "run" => {
            let end = time_compat::parse_time_bytes(parsed.text_bytes("to")).map_err(|e| {
                CliError::new(ErrorCode::CommandFailed, format!("invalid --to: {e}"))
            })?;
            let expr = positional(0).unwrap_or("");
            if expr.trim().is_empty() {
                return Err(CliError::new(
                    ErrorCode::InvalidInput,
                    "PromQL expression is required; example: pb promql run \"http_requests_total\" --dataset otel_metrics",
                ));
            }
            set("query", expr);
            set("stream", parsed.text("dataset"));
            if parsed.boolean("instant") {
                set("time", &time_compat::format_time(end));
                "query".to_owned()
            } else {
                let start =
                    time_compat::parse_time_bytes(parsed.text_bytes("from")).map_err(|e| {
                        CliError::new(ErrorCode::CommandFailed, format!("invalid --from: {e}"))
                    })?;
                set("start", &time_compat::format_time(start));
                set("end", &time_compat::format_time(end));
                set(
                    "step",
                    &time_compat::resolve_step(parsed.text("step"), start, end),
                );
                "query_range".to_owned()
            }
        }
        "active-queries" => "status/active_queries".to_owned(),
        other => {
            let stream_index = if other == "label-values" { 1 } else { 0 };
            set(
                "stream",
                positional(stream_index).unwrap_or_else(|| parsed.text("dataset")),
            );
            if matches!(other, "labels" | "label-values" | "series") {
                for (flag, param) in [("from", "start"), ("to", "end")] {
                    if !parsed.text(flag).is_empty()
                        && let Ok(time) = time_compat::parse_time(parsed.text(flag))
                    {
                        set(param, &time_compat::format_time(time));
                    }
                }
            }
            if other.starts_with("cardinality ") {
                for name in ["lookback", "limit"] {
                    if let Some(FlagValue::Int(value)) = parsed.flags.get(name).map(|f| &f.value) {
                        set(name, &value.to_string());
                    }
                }
            }
            match other {
                "labels" => "labels".to_owned(),
                "label-values" => format!("label/{}/values", positional(0).unwrap_or("")),
                "series" => {
                    let matches = match parsed.flags.get("match").map(|f| &f.value) {
                        Some(FlagValue::Texts(values)) if !values.is_empty() => values,
                        _ => {
                            return Err(CliError::new(
                                ErrorCode::InvalidInput,
                                "at least one --match selector is required",
                            ));
                        }
                    };
                    for value in matches {
                        set("match[]", value);
                    }
                    "series".to_owned()
                }
                "cardinality label-names" | "cardinality active-series" => {
                    let selector = if other.ends_with("active-series") {
                        positional(1).unwrap_or_else(|| parsed.text("selector"))
                    } else {
                        parsed.text("selector")
                    };
                    if !selector.is_empty() {
                        set("selector", selector);
                    }
                    format!(
                        "cardinality/{}",
                        other.trim_start_matches("cardinality ").replace('-', "_")
                    )
                }
                "cardinality label-values" => {
                    let label = positional(1).unwrap_or_else(|| parsed.text("label"));
                    if !label.is_empty() {
                        set("label_name", label);
                    }
                    "cardinality/label_values".to_owned()
                }
                "tsdb" => {
                    if let Some(FlagValue::Int(value)) = parsed.flags.get("top").map(|f| &f.value) {
                        set("topN", &value.to_string());
                    }
                    for (flag, param) in [("date", "date"), ("focus-label", "focusLabel")] {
                        if !parsed.text(flag).is_empty() {
                            set(param, parsed.text(flag));
                        }
                    }
                    "status/tsdb".to_owned()
                }
                _ => unreachable!("operation checked at dispatch"),
            }
        }
    };
    Ok(Operation {
        endpoint: format!("prometheus/api/v1/{endpoint}"),
        pairs,
    })
}

// URL values retain byte identity. The String invocation above is also used
// for validation and JSON-facing output; it must not decide URL escaping.
fn raw_request(
    parsed: &ParsedInvocation,
    operation: &str,
    request: &Operation,
) -> (Vec<u8>, Vec<(String, Vec<u8>)>) {
    let positional_or = |index, flag| {
        if parsed.positionals.get(index).is_some() {
            parsed.positional_bytes(index)
        } else {
            parsed.text_bytes(flag)
        }
    };
    let mut matches = 0;
    let pairs = request
        .pairs
        .iter()
        .map(|(key, value)| {
            let raw = match key.as_str() {
                "query" if operation == "run" => parsed.positional_bytes(0),
                "stream" if operation == "run" => parsed.text_bytes("dataset"),
                "stream" => positional_or(usize::from(operation == "label-values"), "dataset"),
                "match[]" => {
                    let result = parsed
                        .raw
                        .flags
                        .get("match")
                        .and_then(|values| values.get(matches))
                        .map(Vec::as_slice)
                        .unwrap_or(value.as_bytes());
                    matches += 1;
                    result
                }
                "selector" if operation == "cardinality active-series" => {
                    positional_or(1, "selector")
                }
                "selector" => parsed.text_bytes("selector"),
                "label_name" => positional_or(1, "label"),
                "date" => parsed.text_bytes("date"),
                "focusLabel" => parsed.text_bytes("focus-label"),
                _ => value.as_bytes(),
            };
            (key.clone(), raw.to_vec())
        })
        .collect();
    let path = if operation == "label-values" {
        [
            b"prometheus/api/v1/label/".as_slice(),
            parsed.positional_bytes(0),
            b"/values",
        ]
        .concat()
    } else {
        request.endpoint.as_bytes().to_vec()
    };
    (path, pairs)
}

/// Spinner lifetime ends at headers, while collection retains the request deadline.
fn send(
    runtime: &tokio::runtime::Runtime,
    client: &ApiClient,
    request: crate::api::PreparedRequest,
    stderr: &mut (dyn Write + Send),
    terminal: bool,
) -> Result<crate::api::StreamingResponse, ApiError> {
    if !terminal {
        return runtime.block_on(client.send_streaming(request));
    }
    std::thread::scope(|scope| {
        let (stop, stopped) = std::sync::mpsc::channel();
        let spinner = scope.spawn(move || {
            let mut frame = 0;
            loop {
                if stopped.try_recv().is_ok() {
                    break;
                }
                let _ = write!(stderr, "\r{} fetching...", ['|', '/', '-', '\\'][frame % 4]);
                let _ = stderr.flush();
                frame += 1;
                std::thread::sleep(std::time::Duration::from_millis(100));
                if stopped.try_recv().is_ok() {
                    break;
                }
            }
            let _ = stderr.write_all(b"\r\x1b[K");
            let _ = stderr.flush();
        });
        let result = runtime.block_on(client.send_streaming(request));
        let _ = stop.send(());
        let _ = spinner.join();
        result
    })
}

pub(crate) fn execute(
    parsed: &ParsedInvocation,
    command: &CommandSpec,
    path: &str,
    structured: bool,
    stdout: &mut (dyn Write + Send),
    stderr: &mut (dyn Write + Send),
    terminal: bool,
) -> Outcome {
    let operation = path.trim_start_matches("pb promql ");
    let result = (|| -> Result<(), CliError> {
        let format =
            crate::output_format(parsed).map_err(|e| CliError::new(ErrorCode::InvalidInput, e))?;
        if parsed.boolean("interactive") {
            // Go validates the time range before constructing the interactive session.
            time_compat::parse_time_bytes(parsed.text_bytes("to")).map_err(|e| {
                CliError::new(ErrorCode::CommandFailed, format!("invalid --to: {e}"))
            })?;
            let from = if parsed.explicit("from") {
                parsed.text("from")
            } else {
                "10m"
            };
            time_compat::parse_time_bytes(if parsed.explicit("from") {
                parsed.text_bytes("from")
            } else {
                from.as_bytes()
            })
            .map_err(|e| CliError::new(ErrorCode::CommandFailed, format!("invalid --from: {e}")))?;
            return crate::tui::promql::execute(parsed, stdout, stderr);
        }
        let operation_request = invocation(parsed, operation)?;
        let request_result = (|| -> Result<Vec<u8>, CliError> {
            let store = ProfileStore::from_env().map_err(|e| CliError::new(e.code, e.message))?;
            let profile = store
                .active_profile()
                .map_err(|e| CliError::new(e.code, e.message))?;
            let client = ApiClient::new(profile, store)?;
            let request = if parsed.raw.positionals.is_empty() && parsed.raw.flags.is_empty() {
                client.prepare_metrics(&operation_request.endpoint, &operation_request.pairs)?
            } else {
                let (path, pairs) = raw_request(parsed, operation, &operation_request);
                client.prepare_metrics_bytes(&path, &pairs)?
            };
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .map_err(|e| CliError::new(ErrorCode::CommandFailed, e.to_string()))?;
            let response = send(&runtime, &client, request, stderr, terminal).map_err(|e| {
                if e.is_connection_reset() {
                    CliError::new(
                        ErrorCode::CommandFailed,
                        "server reset the connection — query timed out",
                    )
                } else {
                    CliError::from(e)
                }
            })?;
            let response = runtime.block_on(response.collect())?;
            if !(200..300).contains(&response.status) {
                return Err(CliError::status(
                    "PromQL request",
                    response.status,
                    &response.status_text(),
                    &response.body,
                ));
            }
            if let Some(message) = envelope_error(&response.body) {
                return Err(CliError::new(ErrorCode::CommandFailed, message));
            }
            Ok(response.body)
        })();
        let body = request_result.map_err(|mut e| {
            if operation == "run" {
                e.prefix_message("request failed: ");
                // Wrapped typed HTTP errors retain their original public message.
                if e.http_status == 0 {
                    e.public_message = e.message.clone();
                }
            }
            e
        })?;
        if format == "json" {
            return write_json(&body, stdout);
        }
        let label = if operation == "tsdb" {
            parsed.text_bytes("focus-label")
        } else {
            parsed.positional_bytes(0)
        };
        let bytes = output::style::with_process_render(|| {
            output::render_styled_bytes(
                operation,
                &body,
                false,
                label,
                &output::style::Terminal::from_process(),
            )
        })
        .map_err(|e| CliError::new(ErrorCode::InvalidResponse, e))?;
        // Go ignores text write errors.
        let _ = stdout.write_all(&bytes);
        Ok(())
    })();
    match result {
        Ok(()) => Outcome::default(),
        Err(e) => e.render(command, path, structured),
    }
}

/// JSON output is the same for every operation. The status and envelope
/// checks have already accepted `body`; the complete-body validation below is
/// the last failure that is not a write error, so nothing reaches stdout
/// unless the whole response is accepted. Rendering then streams through a
/// bounded buffer instead of holding a second, larger copy of the response.
fn write_json(body: &[u8], stdout: &mut (dyn Write + Send)) -> Result<(), CliError> {
    output::validate_raw_json(body).map_err(|e| CliError::new(ErrorCode::InvalidResponse, e))?;
    raw_json::write_validated(body, stdout)
        .map_err(|e| CliError::output("failed to encode JSON output", e))
}

fn envelope_error(body: &[u8]) -> Option<String> {
    // Go's string fields accept null and case-insensitive keys, but any type
    // mismatch prevents the request-layer envelope check.
    // Like Go's small envelope struct, validate but skip unselected subtrees.
    // In particular, never allocate a generic tree for all metric samples.
    let fields = crate::gojson::raw_members_bytes(body).ok()?;
    let mut status = String::new();
    let mut error = String::new();
    let mut kind = String::new();
    for (key, value) in fields {
        let folded: String = key
            .chars()
            .map(|c| match c {
                'ſ' => 's',
                'K' => 'k',
                c => c.to_ascii_lowercase(),
            })
            .collect();
        let target = if folded == "status" {
            &mut status
        } else if folded == "error" {
            &mut error
        } else if folded == "errortype" {
            &mut kind
        } else {
            continue;
        };
        let value = crate::gojson::decode_ordered(value).ok()?;
        if value.is_null() {
            continue;
        }
        *target = value.as_str()?.to_owned();
    }
    (status == "error").then(|| {
        if kind.is_empty() {
            format!("PromQL error: {error}")
        } else {
            format!("PromQL error ({kind}): {error}")
        }
    })
}

#[cfg(test)]
mod json_output_tests {
    use super::*;

    fn written(body: &[u8]) -> (Result<(), CliError>, Vec<u8>) {
        let mut stdout = Vec::new();
        let result = write_json(body, &mut stdout);
        (result, stdout)
    }

    fn large_prefix(points: usize) -> String {
        let values = (0..points)
            .map(|index| format!("[{index},\"{index}\"]"))
            .collect::<Vec<_>>()
            .join(",");
        format!(
            r#"{{"status":"success","data":{{"resultType":"matrix","result":[{{"metric":{{}},"values":[{values}]}}]}}}}"#
        )
    }

    #[test]
    fn late_failures_publish_nothing_to_stdout() {
        let complete = large_prefix(20_000);
        let (result, stdout) = written(complete.as_bytes());
        assert!(result.is_ok());
        assert_eq!(
            stdout,
            output::render("run", complete.as_bytes(), true, "").unwrap()
        );
        let truncated = &complete[..complete.len() - 1];
        for (body, message) in [
            (
                format!("{complete} {{}}"),
                "server returned multiple JSON values",
            ),
            (
                format!("{complete}x"),
                "server returned invalid JSON: invalid character 'x' looking for beginning of value",
            ),
            (
                truncated.to_owned(),
                "server returned invalid JSON: unexpected EOF",
            ),
            (
                complete.replacen("[19999,", "[19999,,", 1),
                "server returned invalid JSON: invalid character ',' looking for beginning of value",
            ),
        ] {
            let (result, stdout) = written(body.as_bytes());
            let error = result.unwrap_err();
            assert_eq!(error.code, ErrorCode::InvalidResponse);
            assert_eq!(error.message, message);
            assert!(stdout.is_empty(), "{message}");
        }
    }

    #[test]
    fn json_write_errors_keep_output_error_policy() {
        struct Closed;
        impl Write for Closed {
            fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
                Err(std::io::Error::new(
                    std::io::ErrorKind::BrokenPipe,
                    crate::OutputWriteError {
                        stream: "stdout",
                        source: std::io::ErrorKind::BrokenPipe.into(),
                    },
                ))
            }
            fn flush(&mut self) -> std::io::Result<()> {
                Ok(())
            }
        }
        let error = write_json(b"[1]", &mut Closed).unwrap_err();
        assert_eq!(error.code, ErrorCode::ConnectionFailed);
        assert!(
            error.message.starts_with("failed to encode JSON output: "),
            "{}",
            error.message
        );
    }
}
