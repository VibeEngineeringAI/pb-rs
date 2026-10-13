//! Buffered Prometheus operations with explicit invocation and transport policy.
mod output;
mod raw_json;
pub(crate) fn initialize_process_theme() {
    output::style::initialize_process_theme();
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
            let end = time_compat::parse_time(parsed.text("to")).map_err(|e| {
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
                let start = time_compat::parse_time(parsed.text("from")).map_err(|e| {
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
            // Go validates the time range before constructing the deferred TUI.
            time_compat::parse_time(parsed.text("to")).map_err(|e| {
                CliError::new(ErrorCode::CommandFailed, format!("invalid --to: {e}"))
            })?;
            let from = if parsed.explicit("from") {
                parsed.text("from")
            } else {
                "10m"
            };
            time_compat::parse_time(from).map_err(|e| {
                CliError::new(ErrorCode::CommandFailed, format!("invalid --from: {e}"))
            })?;
            return Err(CliError::new(
                ErrorCode::CommandFailed,
                format!(
                    "command {} is not implemented in this Rust phase",
                    crate::parser::quote(path)
                ),
            ));
        }
        let operation_request = invocation(parsed, operation)?;
        let request_result = (|| -> Result<Vec<u8>, CliError> {
            let store = ProfileStore::from_env().map_err(|e| CliError::new(e.code, e.message))?;
            let profile = store
                .active_profile()
                .map_err(|e| CliError::new(e.code, e.message))?;
            let client = ApiClient::new(profile, store)?;
            let request =
                client.prepare_metrics(&operation_request.endpoint, &operation_request.pairs)?;
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .map_err(|e| CliError::new(ErrorCode::CommandFailed, e.to_string()))?;
            let response = send(&runtime, &client, request, stderr, terminal).map_err(|e| {
                if e.to_string().contains("connection reset") {
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
                e.message = format!("request failed: {}", e.message);
                // Wrapped typed HTTP errors retain their original public message.
                if e.http_status == 0 {
                    e.public_message = e.message.clone();
                }
            }
            e
        })?;
        let label = if operation == "tsdb" {
            parsed.text("focus-label")
        } else {
            parsed.positionals.first().map_or("", String::as_str)
        };
        let bytes = output::style::with_process_render(|| {
            output::render_styled(
                operation,
                &body,
                format == "json",
                label,
                &output::style::Terminal::from_process(),
            )
        })
        .map_err(|e| CliError::new(ErrorCode::InvalidResponse, e))?;
        if let Err(e) = stdout.write_all(&bytes)
            && format == "json"
        {
            return Err(CliError::output("failed to encode JSON output", e));
        }
        Ok(())
    })();
    match result {
        Ok(()) => Outcome::default(),
        Err(e) => e.render(command, path, structured),
    }
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
