//! File ingestion owns a single durable outbox and a contiguous session sequence.
mod files;

use crate::{
    ErrorCode, Outcome,
    api::{ApiClient, ApiError},
    config::ProfileStore,
    error::CliError,
    parser::{FlagValue, ParsedInvocation},
    shoulder_state::StateFile,
    spec::CommandSpec,
};
use files::{FileCollector, FileFormat, FileOptions, FileState, InvalidJsonPolicy, StartPosition};
use serde::{Deserialize, Serialize};
use std::{io::Write, path::Path, time::Duration};

const MAX_UPLOAD: usize = 16 * 1024 * 1024;
const MAX_REQUEST: usize = 9 * 1024 * 1024;
// Parseable may infer a Float64 column for incoming JSON numbers.
const MAX_SEQUENCE: u64 = (1u64 << 53) - 1;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct Binding {
    endpoint: String,
    #[serde(default)]
    tenant_id: String,
    #[serde(default)]
    workspace_id: String,
    dataset: String,
    session: String,
    directory: String,
    includes: Vec<String>,
    excludes: Vec<String>,
    format: String,
}

#[derive(Serialize, Deserialize)]
struct Checkpoint {
    version: u32,
    binding: Binding,
    collector: String,
    host: String,
    next_sequence: u64,
    files: FileState,
    complete: bool,
    pending: Option<Outbox>,
}

#[derive(Serialize, Deserialize)]
struct Outbox {
    rows: Vec<serde_json::Value>,
    next_files: FileState,
    next_sequence: u64,
    complete: bool,
    skipped_lines: u64,
}

pub(super) fn execute(
    parsed: &ParsedInvocation,
    command: &CommandSpec,
    path: &str,
    structured: bool,
    stdout: &mut (dyn Write + Send),
    stderr: &mut (dyn Write + Send),
) -> Outcome {
    match run(parsed, stdout, stderr) {
        Ok(()) => Outcome::default(),
        Err(error) => error.render(command, path, structured),
    }
}

fn invalid(message: impl Into<String>) -> CliError {
    CliError::new(ErrorCode::InvalidInput, message)
}

fn count(parsed: &ParsedInvocation, name: &str, maximum: usize) -> Result<usize, CliError> {
    match parsed.flags.get(name).map(|flag| &flag.value) {
        Some(FlagValue::Int(value)) if *value > 0 && (*value as u64) <= maximum as u64 => {
            Ok(*value as usize)
        }
        _ => Err(invalid(format!("--{name} must be between 1 and {maximum}"))),
    }
}

fn run(
    parsed: &ParsedInvocation,
    stdout: &mut (dyn Write + Send),
    stderr: &mut (dyn Write + Send),
) -> Result<(), CliError> {
    let json = crate::output_format(parsed).map_err(invalid)? == "json";
    for index in 0..parsed.positionals.len() {
        std::str::from_utf8(parsed.positional_bytes(index))
            .map_err(|_| invalid("ingestion globs must be valid UTF-8"))?;
    }
    for values in parsed.raw.flags.values() {
        for value in values {
            std::str::from_utf8(value)
                .map_err(|_| invalid("ingestion flag values must be valid UTF-8"))?;
        }
    }
    for name in ["dataset", "session", "state-file"] {
        if parsed.text(name).trim().is_empty() {
            return Err(invalid(format!("--{name} is required")));
        }
    }
    if parsed.text("session").len() > 256 || parsed.text("dataset").len() > 256 {
        return Err(invalid(
            "dataset and session must each be at most 256 UTF-8 bytes",
        ));
    }
    let dataset_header = http::HeaderValue::from_str(parsed.text("dataset"))
        .map_err(|_| invalid("dataset is not a valid HTTP header value"))?;
    let follow = parsed.boolean("follow");
    let complete = parsed.boolean("complete");
    if follow && complete {
        return Err(invalid(
            "--complete cannot be used with --follow; stop the writer and follower, then run once with --complete",
        ));
    }
    let format = match parsed.text("format") {
        "jsonl" => FileFormat::Jsonl,
        "text" => FileFormat::Text,
        _ => return Err(invalid("--format must be jsonl or text")),
    };
    let from = match parsed.text("from") {
        "beginning" => StartPosition::Beginning,
        "end" => StartPosition::End,
        _ => return Err(invalid("--from must be beginning or end")),
    };
    let invalid_json = match parsed.text("on-invalid") {
        "error" => InvalidJsonPolicy::Error,
        "skip" => InvalidJsonPolicy::Skip,
        _ => return Err(invalid("--on-invalid must be error or skip")),
    };
    let poll = parsed.text("poll-interval");
    let poll_ms = if let Some(n) = poll.strip_suffix("ms") {
        n.parse::<u64>().ok()
    } else {
        poll.strip_suffix('s')
            .and_then(|n| n.parse::<u64>().ok())
            .and_then(|n| n.checked_mul(1000))
    }
    .filter(|n| (10..=300_000).contains(n))
    .ok_or_else(|| invalid("--poll-interval must be 10ms..300s (integer ms or s)"))?;
    let excludes = match parsed.flags.get("exclude").map(|flag| &flag.value) {
        Some(FlagValue::Texts(values)) => values.clone(),
        _ => Vec::new(),
    };
    let mut options = FileOptions {
        includes: parsed.positionals.clone(),
        excludes: excludes.clone(),
        format,
        from,
        invalid_json,
        max_line_bytes: count(parsed, "max-line-bytes", 4 * 1024 * 1024)?,
        max_batch_bytes: 4 * 1024 * 1024,
        max_events: count(parsed, "batch-size", 1000)?,
    };
    // Validate patterns before creating a checkpoint or network client.
    FileCollector::new(options.clone(), FileState::default()).map_err(invalid)?;
    let store = ProfileStore::from_env().map_err(|e| CliError::new(e.code, e.message))?;
    let profile = store
        .active_profile()
        .map_err(|e| CliError::new(e.code, e.message))?;
    let directory = std::env::current_dir().map_err(|e| {
        CliError::new(
            ErrorCode::CommandFailed,
            format!("read working directory: {e}"),
        )
    })?;
    let binding = Binding {
        endpoint: profile.url.clone(),
        tenant_id: profile.tenant_id.clone(),
        workspace_id: profile.workspace_id.clone(),
        dataset: parsed.text("dataset").into(),
        session: parsed.text("session").into(),
        directory: directory
            .to_str()
            .ok_or_else(|| invalid("ingestion working directory must be valid UTF-8"))?
            .into(),
        includes: options.includes.clone(),
        excludes,
        format: parsed.text("format").into(),
    };
    let state_path = Path::new(parsed.text("state-file"));
    let state = StateFile::lock(state_path)?;
    // A broad include must never collect this command's own checkpoint, lock,
    // or abandoned atomic-save temporary files.
    let parent = state_path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let parent = std::fs::canonicalize(parent)
        .map_err(|e| invalid(format!("resolve state directory: {e}")))?;
    let canonical_state = parent.join(state_path.file_name().expect("state path validated"));
    let canonical_state = canonical_state
        .to_str()
        .ok_or_else(|| invalid("state path must be valid UTF-8"))?;
    options
        .excludes
        .push(glob::Pattern::escape(canonical_state));
    options
        .excludes
        .push(glob::Pattern::escape(&format!("{canonical_state}.lock")));
    options.excludes.push(format!(
        "{}/.pb-state-*.tmp",
        glob::Pattern::escape(
            parent
                .to_str()
                .ok_or_else(|| invalid("state directory must be valid UTF-8"))?
        )
    ));
    let mut checkpoint = match state.load::<Checkpoint>()? {
        Some(saved) => {
            if saved.version != 1 || saved.binding != binding || saved.next_sequence == 0 {
                return Err(invalid(
                    "state file belongs to a different endpoint, dataset, session, working directory, glob set or format, or has an unsupported version",
                ));
            }
            saved
        }
        None => {
            let saved = Checkpoint {
                version: 1,
                binding,
                collector: uuid::Uuid::new_v4().to_string(),
                host: std::env::var("HOSTNAME")
                    .or_else(|_| std::env::var("COMPUTERNAME"))
                    .or_else(|_| hostname::get().map(|name| name.to_string_lossy().into_owned()))
                    .unwrap_or_else(|_| "unknown".into()),
                next_sequence: 1,
                files: FileState::default(),
                complete: false,
                pending: None,
            };
            state.save(&saved)?;
            saved
        }
    };
    if checkpoint.complete {
        if !complete {
            return Err(invalid(
                "session is already complete; use a new session and state file",
            ));
        }
        return report(stdout, json, 0, 0, true);
    }
    let client = ApiClient::new(profile, store).map_err(api_error)?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| CliError::new(ErrorCode::CommandFailed, format!("create runtime: {e}")))?;
    let mut uploaded = 0u64;
    let mut skipped = 0u64;
    loop {
        if let Some(pending) = checkpoint.pending.as_ref() {
            runtime.block_on(upload(&client, dataset_header.clone(), &pending.rows))?;
            let pending = checkpoint.pending.take().expect("pending batch was sent");
            uploaded += pending
                .rows
                .iter()
                .filter(|row| row["pb_kind"] == "event")
                .count() as u64;
            skipped += pending.skipped_lines;
            checkpoint.files = pending.next_files;
            checkpoint.next_sequence = pending.next_sequence;
            checkpoint.complete = pending.complete;
            // A crash before this save replays the exact persisted outbox.
            state.save(&checkpoint)?;
            if pending.skipped_lines != 0 {
                writeln!(
                    stderr,
                    "Skipped {} invalid JSONL lines (--on-invalid=skip).",
                    pending.skipped_lines
                )
                .map_err(|e| CliError::output("write ingestion diagnostics", e))?;
            }
            if checkpoint.complete {
                return report(stdout, json, uploaded, skipped, true);
            }
        }
        let mut collector =
            FileCollector::new(options.clone(), checkpoint.files.clone()).map_err(invalid)?;
        let batch = match collector.scan() {
            Ok(batch) => batch,
            Err(error) if follow && error.retryable() => {
                writeln!(stderr, "Source changed during scan; retrying: {error}")
                    .map_err(|e| CliError::output("write ingestion diagnostics", e))?;
                std::thread::sleep(Duration::from_millis(poll_ms));
                continue;
            }
            Err(error) => {
                let mut failure = CliError::new(
                    if error.retryable() {
                        ErrorCode::Conflict
                    } else {
                        ErrorCode::InvalidInput
                    },
                    error.to_string(),
                );
                failure.retryable = error.retryable();
                return Err(failure);
            }
        };
        if !batch.records.is_empty() {
            let collected_at = chrono::Utc::now().to_rfc3339();
            let mut sequence = checkpoint.next_sequence;
            let mut rows = Vec::with_capacity(batch.records.len());
            for record in &batch.records {
                let payload =
                    serde_json::to_string(&record.payload).map_err(|e| invalid(e.to_string()))?;
                rows.push(serde_json::json!({
                    "pb_session": checkpoint.binding.session,
                    "pb_collector": checkpoint.collector,
                    "pb_seq": sequence,
                    "pb_event_id": format!("{}:{}", checkpoint.collector, record.event_id),
                    "pb_kind": "event",
                    "pb_source": record.source_path,
                    "pb_generation": record.source_generation,
                    "pb_source_seq": record.source_sequence,
                    "pb_host": checkpoint.host,
                    "pb_collected_at": collected_at,
                    "pb_payload": payload,
                }));
                sequence = sequence
                    .checked_add(1)
                    .filter(|n| *n <= MAX_SEQUENCE)
                    .ok_or_else(|| invalid("session sequence exhausted"))?;
            }
            // Include source offsets in the outbox before HTTP, even on the
            // first batch: changed or removed source files cannot change replay.
            if serde_json::to_vec(&rows)
                .map_err(|e| invalid(e.to_string()))?
                .len()
                > MAX_UPLOAD
            {
                return Err(invalid(
                    "encoded ingestion batch exceeds 16 MiB; reduce --batch-size and retry (source offsets have not advanced)",
                ));
            }
            checkpoint.pending = Some(Outbox {
                rows,
                next_files: batch.next_state().clone(),
                next_sequence: sequence,
                complete: false,
                skipped_lines: batch.skipped_lines,
            });
            state.save(&checkpoint)?;
            continue;
        }
        if batch.has_state_changes() {
            collector.commit(&batch).map_err(invalid)?;
            checkpoint.files = collector.state().clone();
            state.save(&checkpoint)?;
        }
        skipped += batch.skipped_lines;
        if batch.skipped_lines != 0 {
            writeln!(
                stderr,
                "Skipped {} invalid JSONL lines (--on-invalid=skip).",
                batch.skipped_lines
            )
            .map_err(|e| CliError::output("write ingestion diagnostics", e))?;
        }
        if batch.scan_limit_reached {
            continue;
        }
        if complete {
            if batch.has_incomplete_lines {
                return Err(invalid(
                    "cannot complete while a source has an unfinished line; stop writers and append a newline first",
                ));
            }
            checkpoint.pending = Some(Outbox {
                rows: vec![serde_json::json!({
                    "pb_session": checkpoint.binding.session,
                    "pb_collector": checkpoint.collector,
                    "pb_seq": checkpoint.next_sequence,
                    "pb_event_id": format!("{}:complete", checkpoint.collector),
                    "pb_kind": "complete",
                    "pb_source": "",
                    "pb_generation": "",
                    "pb_source_seq": 0,
                    "pb_host": checkpoint.host,
                    "pb_collected_at": chrono::Utc::now().to_rfc3339(),
                    "pb_payload": "{}",
                })],
                next_files: checkpoint.files.clone(),
                next_sequence: checkpoint
                    .next_sequence
                    .checked_add(1)
                    .ok_or_else(|| invalid("session sequence exhausted"))?,
                complete: true,
                skipped_lines: 0,
            });
            state.save(&checkpoint)?;
            continue;
        }
        if !follow {
            return report(stdout, json, uploaded, skipped, false);
        }
        std::thread::sleep(Duration::from_millis(poll_ms));
    }
}

async fn upload(
    client: &ApiClient,
    dataset: http::HeaderValue,
    rows: &[serde_json::Value],
) -> Result<(), CliError> {
    let mut pieces = vec![rows];
    while let Some(piece) = pieces.pop() {
        let body = serde_json::to_vec(piece).map_err(|e| invalid(e.to_string()))?;
        if body.len() > MAX_REQUEST && piece.len() > 1 {
            let (left, right) = piece.split_at(piece.len() / 2);
            pieces.extend([right, left]);
            continue;
        }
        let mut request = client
            .prepare_standard(http::Method::POST, "ingest", Some(body))
            .map_err(api_error)?;
        request.set_header(http::HeaderName::from_static("x-p-stream"), dataset.clone());
        let mut response = client.send_streaming(request).await.map_err(api_error)?;
        let preview = response.read_limited(65_537).await.map_err(api_error)?;
        if response.status == 413 && piece.len() > 1 {
            // Split only requests; retain the original durable outbox until
            // every piece succeeds. A failure replays stable duplicate rows.
            let (left, right) = piece.split_at(piece.len() / 2);
            pieces.extend([right, left]);
            continue;
        }
        if !(200..300).contains(&response.status) {
            return Err(CliError::status(
                "ingest (pending batch retained; fix the destination and retry)",
                response.status,
                &response.status_text,
                &preview[..preview.len().min(65_536)],
            ));
        }
    }
    Ok(())
}

fn api_error(error: ApiError) -> CliError {
    let code = if error.is_timeout() {
        ErrorCode::Timeout
    } else {
        ErrorCode::ConnectionFailed
    };
    CliError::new(
        code,
        format!("ingestion request failed; pending batch retained for retry: {error}"),
    )
}

fn report(
    stdout: &mut (dyn Write + Send),
    json: bool,
    uploaded: u64,
    skipped: u64,
    complete: bool,
) -> Result<(), CliError> {
    if json {
        writeln!(
            stdout,
            "{}",
            serde_json::json!({"ingested":uploaded,"skipped":skipped,"session_complete":complete})
        )
    } else {
        writeln!(
            stdout,
            "Ingested {uploaded} events; skipped {skipped} lines; session {}.",
            if complete { "complete" } else { "open" }
        )
    }
    .and_then(|()| stdout.flush())
    .map_err(|e| CliError::output("write ingestion result", e))
}
