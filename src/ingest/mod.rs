//! File ingestion owns a single durable outbox and a contiguous session sequence.
mod canonical;
mod encode;
mod files;
#[cfg(test)]
mod fuzz;

use crate::{
    ErrorCode, Outcome,
    api::{ApiClient, ApiError},
    config::ProfileStore,
    error::CliError,
    parser::{FlagValue, ParsedInvocation},
    shoulder_state::{JsonDocument, StateFile},
    spec::CommandSpec,
};
use bytes::Bytes;
use files::{
    FileCollector, FileFormat, FileOptions, FileRecord, FileState, InvalidJsonPolicy, StartPosition,
};
use serde::{Deserialize, Serialize};
use serde_json::value::RawValue;
use std::{io::Write, ops::Range, path::Path, time::Duration};

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
    rows: OutboxRows,
    next_files: FileState,
    next_sequence: u64,
    complete: bool,
    skipped_lines: u64,
}

/// The durable batch, held as the one JSON document `[row,…]` that is both
/// written into the checkpoint and sent as the request body. Rows are encoded
/// once; budgeting, checkpointing and every upload attempt reuse those bytes.
/// The checkpoint representation remains an array of ordinary JSON values.
struct OutboxRows {
    json: Bytes,
    /// Where each row begins in `json`. A row ends one byte before its
    /// successor begins, or one byte before the closing bracket.
    starts: Vec<usize>,
    events: usize,
}

impl OutboxRows {
    fn len(&self) -> usize {
        self.starts.len()
    }

    /// Every row, as the piece an upload starts from.
    fn whole(&self) -> Range<usize> {
        0..self.len()
    }

    /// The rows of `piece` and the commas between them, without brackets.
    fn span(&self, piece: &Range<usize>) -> &[u8] {
        if piece.is_empty() {
            return &[];
        }
        let end = match self.starts.get(piece.end) {
            Some(next) => next - 1,
            None => self.json.len() - 1,
        };
        &self.json[self.starts[piece.start]..end]
    }

    /// Size of the request body which carries exactly the rows of `piece`.
    fn encoded_len(&self, piece: &Range<usize>) -> usize {
        self.span(piece).len() + 2
    }

    /// The request body for `piece`. The whole batch shares the checkpointed
    /// buffer; only a piece split off after HTTP 413 is copied.
    fn body(&self, piece: &Range<usize>) -> Bytes {
        if *piece == self.whole() {
            return self.json.clone();
        }
        let span = self.span(piece);
        let mut body = Vec::with_capacity(span.len() + 2);
        body.push(b'[');
        body.extend_from_slice(span);
        body.push(b']');
        body.into()
    }
}

struct RowsBuilder {
    json: Vec<u8>,
    starts: Vec<usize>,
    events: usize,
}

impl RowsBuilder {
    fn with_capacity(rows: usize, bytes: usize) -> Self {
        let mut json = Vec::with_capacity(bytes.saturating_add(2));
        json.push(b'[');
        Self {
            json,
            starts: Vec::with_capacity(rows),
            events: 0,
        }
    }

    fn begin_row(&mut self, is_event: bool) {
        if !self.starts.is_empty() {
            self.json.push(b',');
        }
        self.starts.push(self.json.len());
        self.events += usize::from(is_event);
    }

    fn push(&mut self, row: &EventRow<'_>) {
        self.begin_row(row.pb_kind == "event");
        row.encode(&mut self.json);
    }

    /// Size of the finished document, including the bracket not yet written.
    fn encoded_len(&self) -> usize {
        self.json.len() + 1
    }

    fn finish(mut self) -> OutboxRows {
        self.json.push(b']');
        // Saving copies this text into the checkpoint unexamined.
        debug_assert!(serde_json::from_slice::<&RawValue>(&self.json).is_ok());
        OutboxRows {
            json: self.json.into(),
            starts: self.starts,
            events: self.events,
        }
    }
}

impl Serialize for OutboxRows {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        // A borrowed RawValue is how serde_json copies existing JSON text
        // into its output, at the price of parsing that text to check it.
        // Saving a checkpoint avoids the price: see `Checkpoint::write_json`.
        serde_json::from_slice::<&RawValue>(&self.json)
            .map_err(serde::ser::Error::custom)?
            .serialize(serializer)
    }
}

impl JsonDocument for Checkpoint {
    /// The derived serialization, byte for byte, except that pending rows
    /// are copied as the text they already are. Serializing them would parse
    /// the whole batch again merely to copy it.
    fn write_json<W: Write>(&self, out: &mut W) -> serde_json::Result<()> {
        fn field<W: Write, T: Serialize + ?Sized>(
            out: &mut W,
            lead: &str,
            value: &T,
        ) -> serde_json::Result<()> {
            out.write_all(lead.as_bytes())
                .map_err(serde_json::Error::io)?;
            serde_json::to_writer(out, value)
        }

        // Naming every field keeps this in step with both structs: a new
        // field does not compile until it is written here too.
        let Checkpoint {
            version,
            binding,
            collector,
            host,
            next_sequence,
            files,
            complete,
            pending,
        } = self;
        let Some(Outbox {
            rows,
            next_files,
            next_sequence: pending_sequence,
            complete: pending_complete,
            skipped_lines,
        }) = pending
        else {
            return serde_json::to_writer(out, self);
        };
        field(out, "{\"version\":", version)?;
        field(out, ",\"binding\":", binding)?;
        field(out, ",\"collector\":", collector)?;
        field(out, ",\"host\":", host)?;
        field(out, ",\"next_sequence\":", next_sequence)?;
        field(out, ",\"files\":", files)?;
        field(out, ",\"complete\":", complete)?;
        out.write_all(b",\"pending\":{\"rows\":")
            .and_then(|()| out.write_all(&rows.json))
            .map_err(serde_json::Error::io)?;
        field(out, ",\"next_files\":", next_files)?;
        field(out, ",\"next_sequence\":", pending_sequence)?;
        field(out, ",\"complete\":", pending_complete)?;
        field(out, ",\"skipped_lines\":", skipped_lines)?;
        out.write_all(b"}}").map_err(serde_json::Error::io)
    }
}

impl<'de> Deserialize<'de> for OutboxRows {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct Rows;

        impl<'de> serde::de::Visitor<'de> for Rows {
            type Value = OutboxRows;

            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("a sequence")
            }

            fn visit_seq<A: serde::de::SeqAccess<'de>>(
                self,
                mut rows: A,
            ) -> Result<OutboxRows, A::Error> {
                // Older version-1 checkpoints allowed any JSON row. Decode
                // through the same Value representation to retain
                // duplicate-key normalization, numeric lexemes, field order,
                // and the existing recursion limit/errors.
                let mut builder = RowsBuilder::with_capacity(rows.size_hint().unwrap_or(0), 0);
                while let Some(row) = rows.next_element::<serde_json::Value>()? {
                    builder.begin_row(row["pb_kind"] == "event");
                    serde_json::to_writer(&mut builder.json, &row)
                        .map_err(serde::de::Error::custom)?;
                }
                Ok(builder.finish())
            }
        }

        deserializer.deserialize_seq(Rows)
    }
}

// Field order matches the former json! rows, including pb_payload appended last.
// The derived serialization is the former encoder, kept as the test oracle.
#[cfg_attr(test, derive(Serialize))]
struct EventRow<'a> {
    pb_session: &'a str,
    pb_collector: &'a str,
    pb_seq: u64,
    pb_event_id: &'a str,
    pb_kind: &'a str,
    pb_source: &'a str,
    pb_generation: &'a str,
    pb_source_seq: u64,
    pb_host: &'a str,
    pb_collected_at: &'a str,
    pb_payload: &'a str,
}

impl EventRow<'_> {
    /// Append this row as compact JSON, exactly as serde_json serializes it.
    fn encode(&self, out: &mut Vec<u8>) {
        let string = |out: &mut Vec<u8>, lead: &str, value: &str| {
            out.extend_from_slice(lead.as_bytes());
            encode::push_string(out, value.as_bytes());
        };
        let number = |out: &mut Vec<u8>, lead: &str, value: u64| {
            out.extend_from_slice(lead.as_bytes());
            encode::push_u64(out, value);
        };
        string(out, "{\"pb_session\":", self.pb_session);
        string(out, ",\"pb_collector\":", self.pb_collector);
        number(out, ",\"pb_seq\":", self.pb_seq);
        string(out, ",\"pb_event_id\":", self.pb_event_id);
        string(out, ",\"pb_kind\":", self.pb_kind);
        string(out, ",\"pb_source\":", self.pb_source);
        string(out, ",\"pb_generation\":", self.pb_generation);
        number(out, ",\"pb_source_seq\":", self.pb_source_seq);
        string(out, ",\"pb_host\":", self.pb_host);
        string(out, ",\"pb_collected_at\":", self.pb_collected_at);
        string(out, ",\"pb_payload\":", self.pb_payload);
        out.push(b'}');
    }
}

/// A slightly generous guess at one encoded row, so that reserving a batch
/// document up front usually avoids regrowing (and recopying) it.
fn row_size_hint(checkpoint: &Checkpoint, record: &FileRecord) -> usize {
    // Field names, punctuation, both sequence numbers and the timestamp.
    const FIXED: usize = 256;
    FIXED
        + checkpoint.binding.session.len()
        + 2 * checkpoint.collector.len()
        + checkpoint.host.len()
        + record.event_id.len()
        + record.source_path.len()
        + record.source_generation.len()
        // The payload is JSON text, whose quotes are escaped once more.
        + record.payload.len()
        + record.payload.len() / 8
}

/// Split a request in two. The upload stack pops the left half first.
fn halves(piece: &Range<usize>) -> [Range<usize>; 2] {
    let middle = piece.start + piece.len() / 2;
    [middle..piece.end, piece.start..middle]
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
            state.save_document(&saved)?;
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
            uploaded += pending.rows.events as u64;
            skipped += pending.skipped_lines;
            checkpoint.files = pending.next_files;
            checkpoint.next_sequence = pending.next_sequence;
            checkpoint.complete = pending.complete;
            // A crash before this save replays the exact persisted outbox.
            state.save_document(&checkpoint)?;
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
        let mut batch = match collector.scan() {
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
            // Serialize borrowed metadata and payload directly into the batch
            // document, without building a per-row JSON map or row buffer.
            let records = std::mem::take(&mut batch.records);
            let mut rows = RowsBuilder::with_capacity(
                records.len(),
                records.iter().fold(0, |bytes, record| {
                    bytes.saturating_add(row_size_hint(&checkpoint, record))
                }),
            );
            // Every event ID is the collector, a colon, and the source's own.
            let mut event_id = format!("{}:", checkpoint.collector);
            let collector_prefix = event_id.len();
            for record in records {
                event_id.truncate(collector_prefix);
                event_id.push_str(&record.event_id);
                rows.push(&EventRow {
                    pb_session: &checkpoint.binding.session,
                    pb_collector: &checkpoint.collector,
                    pb_seq: sequence,
                    pb_event_id: &event_id,
                    pb_kind: "event",
                    pb_source: &record.source_path,
                    pb_generation: &record.source_generation,
                    pb_source_seq: record.source_sequence,
                    pb_host: &checkpoint.host,
                    pb_collected_at: &collected_at,
                    pb_payload: &record.payload,
                });
                sequence = sequence
                    .checked_add(1)
                    .filter(|n| *n <= MAX_SEQUENCE)
                    .ok_or_else(|| invalid("session sequence exhausted"))?;
            }
            // Include source offsets in the outbox before HTTP, even on the
            // first batch: changed or removed source files cannot change replay.
            if rows.encoded_len() > MAX_UPLOAD {
                return Err(invalid(
                    "encoded ingestion batch exceeds 16 MiB; reduce --batch-size and retry (source offsets have not advanced)",
                ));
            }
            checkpoint.pending = Some(Outbox {
                rows: rows.finish(),
                next_files: batch.next_state().clone(),
                next_sequence: sequence,
                complete: false,
                skipped_lines: batch.skipped_lines,
            });
            state.save_document(&checkpoint)?;
            continue;
        }
        if batch.has_state_changes() {
            collector.commit(&batch).map_err(invalid)?;
            checkpoint.files = collector.state().clone();
            state.save_document(&checkpoint)?;
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
            let mut rows = RowsBuilder::with_capacity(1, 0);
            rows.push(&EventRow {
                pb_session: &checkpoint.binding.session,
                pb_collector: &checkpoint.collector,
                pb_seq: checkpoint.next_sequence,
                pb_event_id: &format!("{}:complete", checkpoint.collector),
                pb_kind: "complete",
                pb_source: "",
                pb_generation: "",
                pb_source_seq: 0,
                pb_host: &checkpoint.host,
                pb_collected_at: &chrono::Utc::now().to_rfc3339(),
                pb_payload: "{}",
            });
            checkpoint.pending = Some(Outbox {
                rows: rows.finish(),
                next_files: checkpoint.files.clone(),
                next_sequence: checkpoint
                    .next_sequence
                    .checked_add(1)
                    .ok_or_else(|| invalid("session sequence exhausted"))?,
                complete: true,
                skipped_lines: 0,
            });
            state.save_document(&checkpoint)?;
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
    rows: &OutboxRows,
) -> Result<(), CliError> {
    let mut pieces = vec![rows.whole()];
    while let Some(piece) = pieces.pop() {
        if rows.encoded_len(&piece) > MAX_REQUEST && piece.len() > 1 {
            pieces.extend(halves(&piece));
            continue;
        }
        let mut request = client
            .prepare_standard_shared(http::Method::POST, "ingest", rows.body(&piece))
            .map_err(api_error)?;
        request.set_header(http::HeaderName::from_static("x-p-stream"), dataset.clone());
        let mut response = client.send_streaming(request).await.map_err(api_error)?;
        let preview = response.read_limited(65_537).await.map_err(api_error)?;
        if response.status == 413 && piece.len() > 1 {
            // Split only requests; retain the original durable outbox until
            // every piece succeeds. A failure replays stable duplicate rows.
            pieces.extend(halves(&piece));
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

#[cfg(test)]
mod outbox_tests;
