//! `pb observe DATASET`: bounded, receipt-acknowledged reads of one session.
//!
//! The wire contract is shared with `pb ingest files`. Exactly ONE collector
//! writes a session. It assigns contiguous `pb_seq` values starting at 1 and
//! persists each outgoing payload before sending it, so a retry repeats the
//! identical row. Rows are flat: `pb_session`, `pb_collector` (UUID), `pb_seq`,
//! `pb_event_id`, `pb_kind` (`event` or `complete`), `pb_source`,
//! `pb_generation`, `pb_source_seq`, `pb_host`, `pb_collected_at` (RFC 3339)
//! and `pb_payload` (a serialized JSON object). The `complete` row takes the
//! sequence number after the last event and carries the payload `{}`.
//!
//! The server keeps no cursor. Progress lives in the caller's cursor file,
//! bound to one endpoint, dataset and session. A read never skips a missing
//! sequence number: rows that become visible out of order produce a `gap`
//! result until the missing row appears. A read stores its receipt before
//! writing stdout and replays that receipt byte for byte until `--ack TOKEN`
//! names it. Conflicting rows for one sequence number, or rows from a second
//! collector, fail rather than guess. Rows below the acknowledged sequence
//! are not re-read, so conflicts there go undetected.
use crate::{
    ErrorCode, Outcome,
    api::{ApiClient, ApiError},
    config::ProfileStore,
    error::CliError,
    parser::{FlagValue, ParsedInvocation},
    shoulder_state::StateFile,
    spec::CommandSpec,
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, value::RawValue};
use std::{io::Write, path::PathBuf, time::Duration};
use tokio::time::Instant;

const MAX_WAIT: Duration = Duration::from_secs(300);
/// The first query runs even with `--wait 0s`, so it gets an ordinary deadline.
const FIRST_REQUEST_TIMEOUT: Duration = Duration::from_secs(10);
const POLL_INTERVAL: Duration = Duration::from_millis(250);
const MIN_REQUEST_TIMEOUT: Duration = Duration::from_millis(100);
const MAX_LIMIT: i64 = 1000;
const MIN_MAX_BYTES: i64 = 1024;
const MAX_MAX_BYTES: i64 = 8 * 1024 * 1024;
// Even --limit 1 fetches a lookahead row. Two maximum 4 MiB ingestion
// payloads can expand past 16 MiB when escaped inside the query response.
const MAX_RESPONSE_BYTES: usize = 32 * 1024 * 1024;
const ERROR_PREVIEW_BYTES: usize = 64 * 1024;
const MAX_NAME_BYTES: usize = 256;
/// Larger sequence numbers cannot survive Parseable's float inference.
const MAX_EXACT_FLOAT: f64 = 9_007_199_254_740_992.0;
const STATE_VERSION: u32 = 1;
const EPOCH: &str = "1970-01-01T00:00:00Z";
const COLUMNS: &str = "pb_session, pb_collector, pb_seq, pb_event_id, pb_kind, pb_source, \
    pb_generation, pb_source_seq, pb_host, pb_collected_at, pb_payload";
const TOKEN_PLACEHOLDER: &str = "00000000-0000-0000-0000-000000000000";

pub(crate) fn execute(
    parsed: &ParsedInvocation,
    command: &CommandSpec,
    path: &str,
    structured: bool,
    stdout: &mut (dyn Write + Send),
) -> Outcome {
    let structured = structured || parsed.text("output") == "json";
    match run(parsed, stdout) {
        Ok(()) => Outcome::default(),
        Err(error) => error.render(command, path, structured),
    }
}

fn run(parsed: &ParsedInvocation, stdout: &mut (dyn Write + Send)) -> Result<(), CliError> {
    let json = crate::output_format(parsed).map_err(invalid)? == "json";
    let request = Request::parse(parsed)?;
    let store = ProfileStore::from_env().map_err(|e| CliError::new(e.code, e.message))?;
    let profile = store
        .active_profile()
        .map_err(|e| CliError::new(e.code, e.message))?;
    let binding = Binding {
        endpoint: profile.url.clone(),
        tenant_id: profile.tenant_id.clone(),
        workspace_id: profile.workspace_id.clone(),
        dataset: request.dataset,
        session: request.session,
    };
    let state = StateFile::lock(&request.cursor_file)?;
    let output = match request.action {
        Action::Ack(token) => {
            let ack = acknowledge(&state, &binding, &token)?;
            if json { json_line(&ack)? } else { ack.text() }
        }
        Action::Read(options) => {
            let client = ApiClient::new(profile, store)?;
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .map_err(|e| CliError::new(ErrorCode::CommandFailed, e.to_string()))?;
            let mut source = HttpSource {
                client: &client,
                dataset: &binding.dataset,
                session: &binding.session,
                max_response: MAX_RESPONSE_BYTES,
            };
            let document = runtime.block_on(read(&state, &binding, options, &mut source))?;
            if json {
                let mut bytes = document.get().as_bytes().to_vec();
                bytes.push(b'\n');
                bytes
            } else {
                render_text(&document)?
            }
        }
    };
    stdout
        .write_all(&output)
        .and_then(|()| stdout.flush())
        .map_err(|e| CliError::output("write observation", e))
}

struct Request {
    dataset: String,
    session: String,
    cursor_file: PathBuf,
    action: Action,
}
enum Action {
    Read(ReadOptions),
    Ack(String),
}
#[derive(Clone, Copy, Debug)]
struct ReadOptions {
    wait: Duration,
    limit: usize,
    max_bytes: usize,
}

impl Request {
    fn parse(parsed: &ParsedInvocation) -> Result<Self, CliError> {
        let dataset = name("dataset name", "a dataset name", parsed.positional_bytes(0))?;
        let session = name("--session", "--session", parsed.text_bytes("session"))?;
        let cursor = parsed.text_bytes("cursor-file");
        if cursor.is_empty() {
            return Err(invalid("--cursor-file is required"));
        }
        let cursor_file = path_from_bytes(cursor)?;
        let action = if parsed.explicit("ack") {
            let token = std::str::from_utf8(parsed.text_bytes("ack"))
                .map_err(|_| invalid("--ack must be valid UTF-8"))?;
            if token.is_empty() {
                return Err(invalid("--ack requires a receipt token"));
            }
            if let Some(flag) = ["wait", "limit", "max-bytes"]
                .into_iter()
                .find(|flag| parsed.explicit(flag))
            {
                return Err(invalid(format!("--ack cannot be combined with --{flag}")));
            }
            Action::Ack(token.to_owned())
        } else {
            let limit = int(parsed, "limit");
            if !(1..=MAX_LIMIT).contains(&limit) {
                return Err(invalid(format!(
                    "--limit must be between 1 and {MAX_LIMIT}"
                )));
            }
            let max_bytes = int(parsed, "max-bytes");
            if !(MIN_MAX_BYTES..=MAX_MAX_BYTES).contains(&max_bytes) {
                return Err(invalid(format!(
                    "--max-bytes must be between {MIN_MAX_BYTES} and {MAX_MAX_BYTES}"
                )));
            }
            Action::Read(ReadOptions {
                wait: parse_wait(parsed.text("wait"))?,
                limit: limit as usize,
                max_bytes: max_bytes as usize,
            })
        };
        Ok(Self {
            dataset,
            session,
            cursor_file,
            action,
        })
    }
}

fn int(parsed: &ParsedInvocation, name: &str) -> i64 {
    match parsed.flags.get(name).map(|flag| &flag.value) {
        Some(FlagValue::Int(value)) => *value,
        _ => 0,
    }
}

fn name(label: &str, required: &str, bytes: &[u8]) -> Result<String, CliError> {
    if bytes.is_empty() {
        return Err(invalid(format!("{required} is required")));
    }
    let text =
        std::str::from_utf8(bytes).map_err(|_| invalid(format!("{label} must be valid UTF-8")))?;
    if text.len() > MAX_NAME_BYTES || text.contains('\0') {
        return Err(invalid(format!(
            "{label} must be at most {MAX_NAME_BYTES} bytes without NUL characters"
        )));
    }
    Ok(text.to_owned())
}

fn path_from_bytes(bytes: &[u8]) -> Result<PathBuf, CliError> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        Ok(PathBuf::from(std::ffi::OsStr::from_bytes(bytes)))
    }
    #[cfg(not(unix))]
    {
        std::str::from_utf8(bytes)
            .map(PathBuf::from)
            .map_err(|_| invalid("--cursor-file must be valid UTF-8"))
    }
}

/// Go-style durations (`0`, `500ms`, `1m30s`), limited to 0 through 5 minutes.
fn parse_wait(text: &str) -> Result<Duration, CliError> {
    let error = || {
        invalid(format!(
            "invalid --wait {text:?}: expected a duration from 0s to 5m, such as 0s, 500ms or 30s"
        ))
    };
    let nanos = duration_nanos(text).ok_or_else(error)?;
    if nanos > MAX_WAIT.as_nanos() {
        return Err(error());
    }
    Ok(Duration::from_nanos(nanos as u64))
}

fn duration_nanos(text: &str) -> Option<u128> {
    if text == "0" {
        return Some(0);
    }
    if text.is_empty() {
        return None;
    }
    let mut rest = text;
    let mut total: u128 = 0;
    while !rest.is_empty() {
        let size = rest
            .bytes()
            .take_while(|b| b.is_ascii_digit() || *b == b'.')
            .count();
        if size == 0 {
            return None;
        }
        let (number, tail) = rest.split_at(size);
        // "ms" must be tried before "m" and "s".
        let (unit, scale) = [
            ("ns", 1u128),
            ("us", 1_000),
            ("µs", 1_000),
            ("ms", 1_000_000),
            ("s", 1_000_000_000),
            ("m", 60_000_000_000),
            ("h", 3_600_000_000_000),
        ]
        .into_iter()
        .find(|(unit, _)| tail.starts_with(unit))?;
        rest = &tail[unit.len()..];
        let (whole, fraction) = number.split_once('.').unwrap_or((number, ""));
        if (whole.is_empty() && fraction.is_empty()) || fraction.contains('.') {
            return None;
        }
        let whole: u128 = if whole.is_empty() {
            0
        } else {
            whole.parse().ok()?
        };
        let (mut numerator, mut denominator) = (0u128, 1u128);
        for digit in fraction.bytes().take(18) {
            numerator = numerator * 10 + u128::from(digit - b'0');
            denominator *= 10;
        }
        let value = whole
            .checked_mul(scale)?
            .checked_add(numerator * scale / denominator)?;
        total = total.checked_add(value)?;
    }
    Some(total)
}

/// What a cursor file observes. The endpoint is the active profile URL.
#[derive(Clone, Debug)]
struct Binding {
    endpoint: String,
    tenant_id: String,
    workspace_id: String,
    dataset: String,
    session: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CursorState {
    version: u32,
    endpoint: String,
    #[serde(default)]
    tenant_id: String,
    #[serde(default)]
    workspace_id: String,
    dataset: String,
    session: String,
    /// The first sequence number not yet acknowledged.
    next_seq: u64,
    /// Pinned by the first acknowledged receipt.
    collector: Option<String>,
    complete: bool,
    last_ack: Option<String>,
    pending: Option<Pending>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Pending {
    token: String,
    next_seq: u64,
    collector: Option<String>,
    complete: bool,
    /// The exact stdout document; replays write these bytes unchanged.
    document: Box<RawValue>,
}

impl CursorState {
    fn new(binding: &Binding) -> Self {
        Self {
            version: STATE_VERSION,
            endpoint: binding.endpoint.clone(),
            tenant_id: binding.tenant_id.clone(),
            workspace_id: binding.workspace_id.clone(),
            dataset: binding.dataset.clone(),
            session: binding.session.clone(),
            next_seq: 1,
            collector: None,
            complete: false,
            last_ack: None,
            pending: None,
        }
    }
}

fn load_state(file: &StateFile, binding: &Binding) -> Result<Option<CursorState>, CliError> {
    let Some(state) = file.load::<CursorState>()? else {
        return Ok(None);
    };
    if state.version != STATE_VERSION {
        return Err(invalid(format!(
            "unsupported cursor file version {}",
            state.version
        )));
    }
    if state.endpoint != binding.endpoint
        || state.tenant_id != binding.tenant_id
        || state.workspace_id != binding.workspace_id
        || state.dataset != binding.dataset
        || state.session != binding.session
    {
        // The endpoint may embed credentials, so only name the dataset and session.
        return Err(conflict(format!(
            "cursor file is bound to dataset {:?} and session {:?} on its original endpoint; \
             use a separate --cursor-file for each endpoint, tenant/workspace and observation",
            state.dataset, state.session
        )));
    }
    if state.next_seq == 0
        || state
            .pending
            .as_ref()
            .is_some_and(|p| p.next_seq <= state.next_seq)
    {
        return Err(invalid("invalid or corrupt cursor file sequence"));
    }
    Ok(Some(state))
}

#[derive(Debug, Serialize)]
struct Acknowledgement {
    dataset: String,
    session: String,
    acknowledged: String,
    next_seq: u64,
    session_complete: bool,
}
impl Acknowledgement {
    fn text(&self) -> Vec<u8> {
        let mut text = format!(
            "Acknowledged {}; next seq {}.",
            self.acknowledged, self.next_seq
        );
        if self.session_complete {
            text.push_str(" Session complete.");
        }
        text.push('\n');
        text.into_bytes()
    }
}

/// Only the pending receipt advances the cursor. Repeating the latest
/// acknowledgment is accepted until another read creates a new receipt.
fn acknowledge(
    file: &StateFile,
    binding: &Binding,
    token: &str,
) -> Result<Acknowledgement, CliError> {
    let Some(mut state) = load_state(file, binding)? else {
        return Err(conflict(
            "no receipt is pending for this cursor file; read before acknowledging",
        ));
    };
    match state.pending.take() {
        Some(pending) if pending.token == token => {
            state.next_seq = pending.next_seq;
            if pending.collector.is_some() {
                state.collector = pending.collector;
            }
            state.complete = pending.complete;
            state.last_ack = Some(pending.token);
            file.save(&state)?;
        }
        Some(_) => {
            return Err(conflict(
                "receipt token does not match the pending receipt; acknowledge the token from the latest read",
            ));
        }
        None if state.last_ack.as_deref() == Some(token) => {}
        None => {
            return Err(conflict(
                "receipt token is stale or unknown; no receipt is pending",
            ));
        }
    }
    Ok(Acknowledgement {
        dataset: state.dataset,
        session: state.session,
        acknowledged: token.to_owned(),
        next_seq: state.next_seq,
        session_complete: state.complete,
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
enum Status {
    /// A receipt holds events, the completion marker, or both.
    Ready,
    /// No row at or after the next sequence number is visible.
    Empty,
    /// Later rows are visible, but the next sequence number is not.
    Gap,
    /// Completion was already acknowledged.
    Complete,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Observation {
    dataset: String,
    session: String,
    status: Status,
    collector: Option<String>,
    from_seq: u64,
    next_seq: u64,
    count: u64,
    /// Compact length of this whole document, excluding the trailing newline.
    bytes: u64,
    has_more: bool,
    session_complete: bool,
    /// The receipt token to pass to `--ack`.
    next_cursor: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    missing_seq: Option<u64>,
    events: Vec<Event>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Event {
    seq: u64,
    event_id: String,
    payload: Box<RawValue>,
    collection: Collection,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Collection {
    collector: String,
    source: String,
    generation: String,
    source_seq: u64,
    host: String,
    collected_at: String,
}

impl Observation {
    fn new(state: &CursorState, status: Status) -> Self {
        Self {
            dataset: state.dataset.clone(),
            session: state.session.clone(),
            status,
            collector: state.collector.clone(),
            from_seq: state.next_seq,
            next_seq: state.next_seq,
            count: 0,
            bytes: 0,
            has_more: false,
            session_complete: state.complete,
            next_cursor: None,
            missing_seq: None,
            events: Vec::new(),
        }
    }

    /// An upper bound for a receipt with no events: every number at its
    /// widest, booleans as `false`, and a token present.
    fn receipt_overhead(&self) -> Result<usize, CliError> {
        let probe = Self {
            status: Status::Ready,
            from_seq: u64::MAX,
            next_seq: u64::MAX,
            count: u64::MAX,
            bytes: u64::MAX,
            has_more: false,
            session_complete: false,
            next_cursor: Some(TOKEN_PLACEHOLDER.into()),
            missing_seq: None,
            events: Vec::new(),
            ..self.clone()
        };
        Ok(serde_json::to_vec(&probe).map_err(serialize_error)?.len())
    }

    /// Serialize with `bytes` equal to the serialized length itself.
    fn finish(mut self) -> Result<Box<RawValue>, CliError> {
        self.count = self.events.len() as u64;
        self.bytes = 0;
        for _ in 0..8 {
            let text = serde_json::to_string(&self).map_err(serialize_error)?;
            if text.len() as u64 == self.bytes {
                return RawValue::from_string(text).map_err(serialize_error);
            }
            self.bytes = text.len() as u64;
        }
        Err(CliError::new(
            ErrorCode::CommandFailed,
            "could not determine observation size",
        ))
    }

    fn finish_bounded(self, max_bytes: usize) -> Result<Box<RawValue>, CliError> {
        let document = self.finish()?;
        if document.get().len() > max_bytes {
            return Err(invalid(format!(
                "observation needs {} bytes, exceeding --max-bytes {max_bytes}; \
                 rerun with a larger --max-bytes",
                document.get().len()
            )));
        }
        Ok(document)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Kind {
    Event,
    Complete,
}

#[derive(Clone, Debug, PartialEq)]
struct Row {
    session: String,
    collector: String,
    seq: u64,
    event_id: String,
    kind: Kind,
    source: String,
    generation: String,
    source_seq: u64,
    host: String,
    collected_at: String,
    payload: String,
}

impl Row {
    fn decode(value: Value) -> Result<Self, String> {
        let Value::Object(mut map) = value else {
            return Err("expected an object".into());
        };
        let kind = match text(&mut map, "pb_kind")?.as_str() {
            "event" => Kind::Event,
            "complete" => Kind::Complete,
            other => return Err(format!("unsupported pb_kind {other:?}")),
        };
        Ok(Self {
            session: text(&mut map, "pb_session")?,
            collector: text(&mut map, "pb_collector")?,
            seq: unsigned(&mut map, "pb_seq")?,
            event_id: text(&mut map, "pb_event_id")?,
            kind,
            source: text(&mut map, "pb_source")?,
            generation: text(&mut map, "pb_generation")?,
            source_seq: unsigned(&mut map, "pb_source_seq")?,
            host: text(&mut map, "pb_host")?,
            collected_at: text(&mut map, "pb_collected_at")?,
            payload: text(&mut map, "pb_payload")?,
        })
    }
}

fn text(map: &mut Map<String, Value>, name: &str) -> Result<String, String> {
    match map.remove(name) {
        Some(Value::String(value)) => Ok(value),
        None | Some(Value::Null) => Err(format!("missing {name}")),
        Some(_) => Err(format!("{name} must be a string")),
    }
}

/// Parseable may infer integers as floats or return them as strings.
fn unsigned(map: &mut Map<String, Value>, name: &str) -> Result<u64, String> {
    let error = || format!("{name} must be a non-negative integer");
    match map.remove(name) {
        Some(Value::Number(number)) => number
            .as_u64()
            .or_else(|| {
                number
                    .as_f64()
                    .filter(|f| f.fract() == 0.0 && (0.0..=MAX_EXACT_FLOAT).contains(f))
                    .map(|f| f as u64)
            })
            .ok_or_else(error),
        Some(Value::String(value)) => value.parse().map_err(|_| error()),
        None | Some(Value::Null) => Err(format!("missing {name}")),
        Some(_) => Err(error()),
    }
}

fn decode_rows(body: &[u8], requested: usize) -> Result<Vec<Row>, CliError> {
    let value: Value = serde_json::from_slice(body)
        .map_err(|e| invalid_response(format!("decode query response: {e}")))?;
    let records = match value {
        Value::Array(records) => records,
        Value::Object(mut object) => match object.remove("records") {
            Some(Value::Array(records)) => records,
            _ => return Err(invalid_response("query response has no records array")),
        },
        _ => return Err(invalid_response("query response is not an array")),
    };
    if records.len() > requested {
        return Err(invalid_response(format!(
            "query returned {} rows for LIMIT {requested}",
            records.len()
        )));
    }
    records
        .into_iter()
        .enumerate()
        .map(|(index, record)| {
            Row::decode(record)
                .map_err(|message| invalid_response(format!("query row {}: {message}", index + 1)))
        })
        .collect()
}

fn event(row: &Row) -> Result<Event, CliError> {
    let payload = serde_json::from_str::<Box<RawValue>>(&row.payload)
        .ok()
        .filter(|raw| raw.get().starts_with('{'))
        .ok_or_else(|| {
            invalid_response(format!(
                "event seq {} payload is not a serialized JSON object",
                row.seq
            ))
        })?;
    Ok(Event {
        seq: row.seq,
        event_id: row.event_id.clone(),
        payload,
        collection: Collection {
            collector: row.collector.clone(),
            source: row.source.clone(),
            generation: row.generation.clone(),
            source_seq: row.source_seq,
            host: row.host.clone(),
            collected_at: row.collected_at.clone(),
        },
    })
}

#[derive(Debug)]
enum Assembly {
    Ready {
        observation: Observation,
        complete: bool,
    },
    Waiting(Observation),
}

/// Validate every returned row, then consume the contiguous prefix that fits.
fn assemble(state: &CursorState, rows: &[Row], options: ReadOptions) -> Result<Assembly, CliError> {
    if rows.len() > options.limit + 1 {
        return Err(invalid_response(format!(
            "query returned {} rows for LIMIT {}",
            rows.len(),
            options.limit + 1
        )));
    }
    let mut collector = state.collector.clone();
    let mut completion: Option<u64> = None;
    let mut unique: Vec<&Row> = Vec::with_capacity(rows.len());
    for row in rows {
        if row.session != state.session {
            return Err(invalid_response(format!(
                "query returned a row for session {:?}",
                row.session
            )));
        }
        if row.seq < state.next_seq {
            return Err(invalid_response(format!(
                "query returned seq {} before requested seq {}",
                row.seq, state.next_seq
            )));
        }
        match &collector {
            Some(expected) if *expected != row.collector => {
                return Err(conflict(format!(
                    "session {:?} has rows from collectors {expected} and {}; \
                     observation requires exactly one collector per session",
                    state.session, row.collector
                )));
            }
            Some(_) => {}
            None => collector = Some(row.collector.clone()),
        }
        if let Some(previous) = unique.last() {
            if row.seq < previous.seq {
                return Err(invalid_response("query returned rows out of pb_seq order"));
            }
            if row.seq == previous.seq {
                // A retried identical row is harmless; anything else is ambiguous.
                if **previous == *row {
                    continue;
                }
                return Err(conflict(format!(
                    "session {:?} has conflicting rows for seq {}",
                    state.session, row.seq
                )));
            }
        }
        if let Some(done) = completion {
            return Err(conflict(format!(
                "session {:?} has seq {} after completion at seq {done}",
                state.session, row.seq
            )));
        }
        if row.kind == Kind::Complete {
            if row.payload.trim() != "{}" {
                return Err(invalid_response(format!(
                    "completion seq {} must have payload {{}}",
                    row.seq
                )));
            }
            completion = Some(row.seq);
        }
        unique.push(row);
    }

    let mut observation = Observation::new(state, Status::Ready);
    observation.collector = collector;
    let overhead = observation.receipt_overhead()?;
    if overhead > options.max_bytes {
        return Err(invalid(format!(
            "--max-bytes {} cannot hold receipt metadata of {overhead} bytes",
            options.max_bytes
        )));
    }
    let mut used = overhead;
    let mut expected = state.next_seq;
    let mut consumed = 0;
    let mut complete = false;
    for row in &unique {
        if row.seq != expected {
            break;
        }
        if row.kind == Kind::Event {
            // The completion marker is metadata and does not count against --limit.
            if observation.events.len() == options.limit {
                break;
            }
            let event = event(row)?;
            // One separator per event over-counts the first by one byte.
            let size = serde_json::to_vec(&event).map_err(serialize_error)?.len() + 1;
            if used + size > options.max_bytes {
                if observation.events.is_empty() {
                    return Err(invalid(format!(
                        "event seq {} needs {} bytes, exceeding --max-bytes {}; \
                         rerun with a larger --max-bytes",
                        row.seq,
                        overhead + size,
                        options.max_bytes
                    )));
                }
                break;
            }
            used += size;
            observation.events.push(event);
        } else {
            complete = true;
        }
        consumed += 1;
        expected = expected
            .checked_add(1)
            .ok_or_else(|| invalid_response("pb_seq overflow"))?;
        if complete {
            break;
        }
    }
    observation.next_seq = expected;
    observation.has_more = !complete && unique.len() > consumed;
    observation.session_complete = complete;
    if consumed > 0 {
        return Ok(Assembly::Ready {
            observation,
            complete,
        });
    }
    observation.status = if unique.is_empty() {
        Status::Empty
    } else {
        observation.missing_seq = Some(expected);
        Status::Gap
    };
    Ok(Assembly::Waiting(observation))
}

/// One bounded query from `from`; implemented over HTTP and by test tables.
trait Source {
    async fn fetch(
        &mut self,
        from: u64,
        rows: usize,
        timeout: Duration,
    ) -> Result<Vec<Row>, CliError>;
}

/// Replay a pending receipt, or query until a receipt is ready or `--wait`
/// expires. The receipt is durable before the caller writes it to stdout.
async fn read<S: Source>(
    file: &StateFile,
    binding: &Binding,
    options: ReadOptions,
    source: &mut S,
) -> Result<Box<RawValue>, CliError> {
    let mut state = match load_state(file, binding)? {
        Some(state) => state,
        None => {
            let state = CursorState::new(binding);
            file.save(&state)?;
            state
        }
    };
    if let Some(pending) = &state.pending {
        #[derive(Deserialize)]
        struct ReceiptCount {
            count: u64,
        }
        let receipt: ReceiptCount =
            serde_json::from_str(pending.document.get()).map_err(serialize_error)?;
        if receipt.count > options.limit as u64 || pending.document.get().len() > options.max_bytes
        {
            return Err(invalid(format!(
                "pending receipt requires --limit at least {} and --max-bytes at least {}; \
                 rerun with those bounds or acknowledge the original receipt with --ack {}",
                receipt.count.max(1),
                pending.document.get().len(),
                pending.token
            )));
        }
        return Ok(pending.document.clone());
    }
    if state.complete {
        return Observation::new(&state, Status::Complete).finish_bounded(options.max_bytes);
    }
    let deadline = Instant::now() + options.wait;
    let mut waiting: Option<Observation> = None;
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero()
            && let Some(previous) = waiting.take()
        {
            return previous.finish_bounded(options.max_bytes);
        }
        let timeout = if options.wait.is_zero() {
            FIRST_REQUEST_TIMEOUT
        } else {
            remaining.min(FIRST_REQUEST_TIMEOUT)
        };
        let rows = match source
            .fetch(state.next_seq, options.limit + 1, timeout)
            .await
        {
            Ok(rows) => rows,
            // A poll cut short by the wait deadline is not a failure.
            Err(error) if error.code == ErrorCode::Timeout && waiting.is_some() => {
                return waiting.expect("checked").finish_bounded(options.max_bytes);
            }
            Err(error) => return Err(error),
        };
        match assemble(&state, &rows, options)? {
            Assembly::Ready {
                mut observation,
                complete,
            } => {
                let token = uuid::Uuid::new_v4().to_string();
                observation.next_cursor = Some(token.clone());
                let next_seq = observation.next_seq;
                let collector = observation.collector.clone();
                let document = observation.finish_bounded(options.max_bytes)?;
                state.pending = Some(Pending {
                    token,
                    next_seq,
                    collector,
                    complete,
                    document: document.clone(),
                });
                file.save(&state)?;
                return Ok(document);
            }
            Assembly::Waiting(observation) => {
                if deadline.saturating_duration_since(Instant::now())
                    < POLL_INTERVAL + MIN_REQUEST_TIMEOUT
                {
                    return observation.finish_bounded(options.max_bytes);
                }
                waiting = Some(observation);
                tokio::time::sleep(POLL_INTERVAL).await;
            }
        }
    }
}

#[derive(Serialize)]
struct QueryRequest<'a> {
    query: &'a str,
    #[serde(rename = "startTime")]
    start: &'a str,
    #[serde(rename = "endTime")]
    end: &'a str,
}

fn query_sql(dataset: &str, session: &str, from: u64, rows: usize) -> String {
    format!(
        "SELECT DISTINCT {COLUMNS} FROM \"{}\" WHERE pb_session = '{}' AND pb_seq >= {from} \
         ORDER BY pb_seq, pb_event_id LIMIT {rows}",
        dataset.replace('"', "\"\""),
        session.replace('\'', "''"),
    )
}

struct HttpSource<'a> {
    client: &'a ApiClient,
    dataset: &'a str,
    session: &'a str,
    max_response: usize,
}

impl Source for HttpSource<'_> {
    async fn fetch(
        &mut self,
        from: u64,
        rows: usize,
        timeout: Duration,
    ) -> Result<Vec<Row>, CliError> {
        let query = query_sql(self.dataset, self.session, from, rows);
        let body = serde_json::to_vec(&QueryRequest {
            query: &query,
            start: EPOCH,
            end: "now",
        })
        .map_err(serialize_error)?;
        let request = self
            .client
            .prepare(
                http::Method::POST,
                &["api/v1/", "query"],
                Some(body),
                timeout,
            )
            .map_err(query_error)?;
        let mut response = self
            .client
            .send_streaming(request)
            .await
            .map_err(query_error)?;
        if response.status != 200 {
            let preview = response
                .read_limited(ERROR_PREVIEW_BYTES)
                .await
                .unwrap_or_default();
            return Err(CliError::status(
                "observe query",
                response.status,
                &response.status_text,
                &preview,
            ));
        }
        let body = response
            .read_limited(self.max_response + 1)
            .await
            .map_err(query_error)?;
        if body.len() > self.max_response {
            return Err(invalid_response(format!(
                "query response exceeds {} bytes; lower --limit",
                self.max_response
            )));
        }
        decode_rows(&body, rows)
    }
}

fn render_text(document: &RawValue) -> Result<Vec<u8>, CliError> {
    let observation: Observation = serde_json::from_str(document.get()).map_err(serialize_error)?;
    let session = &observation.session;
    let mut text = match observation.status {
        Status::Ready => {
            let mut text = format!(
                "{} event(s) for session {session} (seq {} to {}), {} bytes\n",
                observation.count,
                observation.from_seq,
                observation.next_seq - 1,
                observation.bytes
            );
            if let Some(token) = &observation.next_cursor {
                text.push_str(&format!(
                    "Receipt: {token} (acknowledge with --ack {token})\n"
                ));
            }
            if observation.has_more {
                text.push_str("More events are visible after this receipt.\n");
            }
            if observation.session_complete {
                text.push_str("Session complete.\n");
            }
            text
        }
        Status::Empty => format!(
            "No new events for session {session} (next seq {}).\n",
            observation.next_seq
        ),
        Status::Gap => format!(
            "Waiting for seq {} in session {session}; later events are visible.\n",
            observation.missing_seq.unwrap_or(observation.next_seq)
        ),
        Status::Complete => format!("Session {session} is complete.\n"),
    };
    for event in &observation.events {
        text.push_str(&format!(
            "{}\t{}\t{}\n",
            event.seq,
            event.event_id,
            event.payload.get()
        ));
    }
    Ok(text.into_bytes())
}

fn json_line<T: Serialize>(value: &T) -> Result<Vec<u8>, CliError> {
    let mut bytes = serde_json::to_vec(value).map_err(serialize_error)?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn query_error(error: ApiError) -> CliError {
    let mut cli = CliError::from(error);
    cli.message = format!("observe query failed: {}", cli.message);
    cli.public_message = cli.message.clone();
    cli
}
fn invalid(message: impl Into<String>) -> CliError {
    CliError::new(ErrorCode::InvalidInput, message)
}
fn conflict(message: impl Into<String>) -> CliError {
    CliError::new(ErrorCode::Conflict, message)
}
fn invalid_response(message: impl Into<String>) -> CliError {
    CliError::new(ErrorCode::InvalidResponse, message)
}
fn serialize_error(error: serde_json::Error) -> CliError {
    CliError::new(
        ErrorCode::CommandFailed,
        format!("serialize observation: {error}"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        collections::VecDeque,
        io::{BufRead, BufReader, Read},
        net::TcpListener,
        sync::atomic::{AtomicU64, Ordering},
        thread,
    };

    const SESSION: &str = "run-1";
    const COLLECTOR: &str = "6f1c1d1e-0000-4000-8000-000000000001";
    const OTHER_COLLECTOR: &str = "6f1c1d1e-0000-4000-8000-000000000002";
    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Dir(PathBuf);
    impl Dir {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "pb-observe-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            let _ = std::fs::remove_dir_all(&path);
            std::fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn cursor(&self) -> PathBuf {
            self.0.join("cursor.json")
        }
    }
    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn binding() -> Binding {
        Binding {
            endpoint: "http://fixture.invalid".into(),
            tenant_id: String::new(),
            workspace_id: String::new(),
            dataset: "app".into(),
            session: SESSION.into(),
        }
    }
    fn row(seq: u64, kind: Kind, payload: &str) -> Row {
        Row {
            session: SESSION.into(),
            collector: COLLECTOR.into(),
            seq,
            event_id: format!("event-{seq}"),
            kind,
            source: "logs/app.log".into(),
            generation: "gen-1".into(),
            source_seq: seq,
            host: "host-a".into(),
            collected_at: "2026-10-15T00:00:00Z".into(),
            payload: payload.into(),
        }
    }
    fn ev(seq: u64) -> Row {
        row(seq, Kind::Event, &format!(r#"{{"n":{seq}}}"#))
    }
    fn done(seq: u64) -> Row {
        row(seq, Kind::Complete, "{}")
    }
    fn options(limit: usize) -> ReadOptions {
        ReadOptions {
            wait: Duration::ZERO,
            limit,
            max_bytes: 262_144,
        }
    }

    /// An in-memory dataset answering the same filtered, ordered, distinct query.
    #[derive(Default)]
    struct Table {
        rows: Vec<Row>,
        /// Rows appended at the start of each successive query (late visibility).
        reveals: VecDeque<Vec<Row>>,
        calls: Vec<(u64, usize, Duration)>,
    }
    impl Table {
        fn new(rows: Vec<Row>) -> Self {
            Self {
                rows,
                ..Self::default()
            }
        }
    }
    impl Source for Table {
        async fn fetch(
            &mut self,
            from: u64,
            rows: usize,
            timeout: Duration,
        ) -> Result<Vec<Row>, CliError> {
            self.calls.push((from, rows, timeout));
            if let Some(batch) = self.reveals.pop_front() {
                self.rows.extend(batch);
            }
            let mut visible: Vec<Row> = self
                .rows
                .iter()
                .filter(|r| r.session == SESSION && r.seq >= from)
                .cloned()
                .collect();
            visible.sort_by(|a, b| (a.seq, &a.event_id).cmp(&(b.seq, &b.event_id)));
            visible.dedup();
            visible.truncate(rows);
            Ok(visible)
        }
    }

    fn read_with(
        file: &StateFile,
        binding: &Binding,
        table: &mut Table,
        options: ReadOptions,
    ) -> Result<String, CliError> {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        runtime
            .block_on(read(file, binding, options, table))
            .map(|document| document.get().to_owned())
    }
    fn read_doc(file: &StateFile, table: &mut Table, options: ReadOptions) -> String {
        read_with(file, &binding(), table, options).unwrap()
    }
    fn json(text: &str) -> Value {
        serde_json::from_str(text).unwrap()
    }
    fn seqs(document: &Value) -> Vec<u64> {
        document["events"]
            .as_array()
            .unwrap()
            .iter()
            .map(|e| e["seq"].as_u64().unwrap())
            .collect()
    }
    fn token(document: &Value) -> String {
        document["next_cursor"].as_str().unwrap().to_owned()
    }
    fn ack(file: &StateFile, token: &str) -> Result<Acknowledgement, CliError> {
        acknowledge(file, &binding(), token)
    }
    fn pending(file: &StateFile) -> Option<Pending> {
        file.load::<CursorState>().unwrap().unwrap().pending
    }

    #[test]
    fn sql_quotes_dataset_identifier_and_session_literal() {
        let sql = query_sql(r#"we"ird"#, "o'brien' OR '1'='1", 7, 11);
        assert_eq!(
            sql,
            "SELECT DISTINCT pb_session, pb_collector, pb_seq, pb_event_id, pb_kind, pb_source, \
             pb_generation, pb_source_seq, pb_host, pb_collected_at, pb_payload FROM \"we\"\"ird\" \
             WHERE pb_session = 'o''brien'' OR ''1''=''1' AND pb_seq >= 7 \
             ORDER BY pb_seq, pb_event_id LIMIT 11"
        );
    }

    #[test]
    fn pending_receipt_replays_identically_until_exact_ack() {
        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let mut table = Table::new((1..=3).map(ev).collect());
        let first = read_doc(&file, &mut table, options(2));
        let document = json(&first);
        assert_eq!(document["status"], "ready");
        assert_eq!(seqs(&document), [1, 2]);
        assert_eq!(document["has_more"], true);
        assert_eq!(document["session_complete"], false);
        assert_eq!(document["collector"], COLLECTOR);
        assert_eq!(document["events"][0]["payload"], json(r#"{"n":1}"#));
        assert_eq!(document["events"][0]["collection"]["host"], "host-a");
        assert_eq!(document["bytes"].as_u64().unwrap(), first.len() as u64);
        assert_eq!(table.calls, [(1, 3, FIRST_REQUEST_TIMEOUT)]);
        let receipt = token(&document);

        // New rows do not change the pending receipt, and replay needs no query.
        table.rows.push(ev(4));
        assert_eq!(read_doc(&file, &mut table, options(50)), first);
        assert_eq!(table.calls.len(), 1);

        let error = ack(&file, "not-the-token").unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(pending(&file).is_some());

        let acknowledged = ack(&file, &receipt).unwrap();
        assert_eq!(acknowledged.next_seq, 3);
        assert!(!acknowledged.session_complete);
        // Repeating the latest acknowledgment is idempotent.
        assert_eq!(ack(&file, &receipt).unwrap().next_seq, 3);

        let second = json(&read_doc(&file, &mut table, options(2)));
        assert_eq!(seqs(&second), [3, 4]);
        assert_eq!(second["from_seq"], 3);
        assert_eq!(second["has_more"], false);
        assert_eq!(table.calls[1].0, 3);
        assert_ne!(token(&second), receipt);
        // The old token is stale once a newer receipt is pending.
        assert_eq!(ack(&file, &receipt).unwrap_err().code, ErrorCode::Conflict);
        ack(&file, &token(&second)).unwrap();
        assert_eq!(
            ack(&file, "unknown-token").unwrap_err().code,
            ErrorCode::Conflict
        );
    }

    #[test]
    fn incompatible_replay_bounds_preserve_the_original_receipt() {
        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let payload = format!(r#"{{"padding":"{}"}}"#, "x".repeat(1200));
        let mut table = Table::new(vec![row(1, Kind::Event, &payload), ev(2)]);
        let first = read_doc(&file, &mut table, options(2));
        let saved = std::fs::read(dir.cursor()).unwrap();
        for bounded in [
            options(1),
            ReadOptions {
                max_bytes: 1024,
                ..options(2)
            },
        ] {
            let error = read_with(&file, &binding(), &mut table, bounded).unwrap_err();
            assert_eq!(error.code, ErrorCode::InvalidInput);
            assert!(error.message.contains("pending receipt requires"));
            assert!(error.message.contains("--ack"));
            assert_eq!(std::fs::read(dir.cursor()).unwrap(), saved);
        }
        assert_eq!(table.calls.len(), 1);
        let exact = ReadOptions {
            max_bytes: first.len(),
            ..options(2)
        };
        assert_eq!(read_doc(&file, &mut table, exact), first);
    }

    #[test]
    fn completed_cursor_obeys_a_lowered_byte_budget_without_querying() {
        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let binding = Binding {
            session: "\u{1}".repeat(MAX_NAME_BYTES),
            ..binding()
        };
        let mut state = CursorState::new(&binding);
        state.complete = true;
        state.next_seq = 2;
        state.collector = Some(COLLECTOR.into());
        file.save(&state).unwrap();
        let saved = std::fs::read(dir.cursor()).unwrap();
        let mut table = Table::default();
        let error = read_with(
            &file,
            &binding,
            &mut table,
            ReadOptions {
                max_bytes: 1024,
                ..options(1)
            },
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidInput);
        assert!(error.message.contains("exceeding --max-bytes"));
        assert!(table.calls.is_empty());
        assert_eq!(std::fs::read(dir.cursor()).unwrap(), saved);
        let document = read_with(&file, &binding, &mut table, options(1)).unwrap();
        assert_eq!(json(&document)["status"], "complete");
    }

    #[test]
    fn every_non_receipt_status_checks_its_actual_serialized_length() {
        let state = CursorState::new(&binding());
        for status in [Status::Empty, Status::Gap, Status::Complete] {
            let observation = Observation::new(&state, status);
            let length = observation.clone().finish().unwrap().get().len();
            assert!(observation.clone().finish_bounded(length - 1).is_err());
            assert_eq!(
                observation.finish_bounded(length).unwrap().get().len(),
                length
            );
        }
    }

    #[test]
    fn observation_validation_errors_default_to_structured_json() {
        let command = crate::spec::command_spec().child("observe").unwrap();
        for (output, structured) in [(None, true), (Some("text"), false)] {
            let mut words = vec!["app".to_owned()];
            if let Some(output) = output {
                words.extend(["--output".into(), output.into()]);
            }
            let parsed = crate::parser::parse(command, "pb observe", &words).unwrap();
            let outcome = execute(&parsed, command, "pb observe", false, &mut Vec::new());
            assert_ne!(outcome.exit_code, 0);
            assert_eq!(
                serde_json::from_slice::<Value>(&outcome.stderr).is_ok(),
                structured
            );
        }
    }

    #[test]
    fn separate_cursor_files_progress_independently() {
        let dir = Dir::new();
        let one = StateFile::lock(&dir.0.join("one.json")).unwrap();
        let two = StateFile::lock(&dir.0.join("two.json")).unwrap();
        let mut table = Table::new((1..=2).map(ev).collect());
        let first = json(&read_doc(&one, &mut table, options(1)));
        ack(&one, &token(&first)).unwrap();
        assert_eq!(seqs(&json(&read_doc(&one, &mut table, options(1)))), [2]);
        assert_eq!(seqs(&json(&read_doc(&two, &mut table, options(1)))), [1]);
        assert_eq!(
            acknowledge(&two, &binding(), &token(&first))
                .unwrap_err()
                .code,
            ErrorCode::Conflict
        );
    }

    #[test]
    fn gaps_never_skip_and_late_rows_are_consumed_in_order() {
        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let mut table = Table::new(vec![ev(1), ev(3)]);
        let first = json(&read_doc(&file, &mut table, options(10)));
        assert_eq!(seqs(&first), [1]);
        assert_eq!(first["next_seq"], 2);
        assert_eq!(first["has_more"], true);
        ack(&file, &token(&first)).unwrap();

        let gap = json(&read_doc(&file, &mut table, options(10)));
        assert_eq!(gap["status"], "gap");
        assert_eq!(gap["missing_seq"], 2);
        assert_eq!(gap["next_cursor"], Value::Null);
        assert_eq!(gap["count"], 0);
        assert!(seqs(&gap).is_empty());
        assert!(pending(&file).is_none());

        table.rows.push(ev(2));
        let late = json(&read_doc(&file, &mut table, options(10)));
        assert_eq!(seqs(&late), [2, 3]);
        assert_eq!(late["from_seq"], 2);
        assert_eq!(late["next_seq"], 4);
    }

    #[test]
    fn bounded_wait_polls_until_rows_are_visible() {
        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let mut table = Table {
            reveals: VecDeque::from([vec![], vec![], vec![ev(1)]]),
            ..Table::default()
        };
        let started = std::time::Instant::now();
        let waited = ReadOptions {
            wait: Duration::from_secs(5),
            ..options(10)
        };
        let document = json(&read_doc(&file, &mut table, waited));
        assert_eq!(seqs(&document), [1]);
        assert_eq!(table.calls.len(), 3);
        assert!(started.elapsed() < Duration::from_secs(3));
        // Later polls never exceed the first request deadline.
        assert!(
            table.calls[1..]
                .iter()
                .all(|c| c.2 <= FIRST_REQUEST_TIMEOUT && !c.2.is_zero())
        );
    }

    #[test]
    fn wait_expires_with_empty_result_and_zero_wait_queries_once() {
        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let mut table = Table::default();
        let document = json(&read_doc(&file, &mut table, options(10)));
        assert_eq!(document["status"], "empty");
        assert_eq!(document["has_more"], false);
        assert_eq!(table.calls.len(), 1);

        let started = std::time::Instant::now();
        let waited = ReadOptions {
            wait: Duration::from_millis(700),
            ..options(10)
        };
        let document = json(&read_doc(&file, &mut table, waited));
        assert_eq!(document["status"], "empty");
        let elapsed = started.elapsed();
        assert!(elapsed >= Duration::from_millis(250), "{elapsed:?}");
        assert!(elapsed < Duration::from_millis(1500), "{elapsed:?}");
        assert!(table.calls.len() >= 3);
        assert!(pending(&file).is_none());
    }

    #[test]
    fn identical_duplicates_collapse_and_conflicting_duplicates_fail() {
        let state = CursorState::new(&binding());
        let Assembly::Ready { observation, .. } =
            assemble(&state, &[ev(1), ev(1), ev(2)], options(10)).unwrap()
        else {
            panic!("expected events");
        };
        assert_eq!(observation.events.len(), 2);

        let mut changed = ev(1);
        changed.payload = r#"{"n":"other"}"#.into();
        let error = assemble(&state, &[ev(1), changed.clone()], options(10)).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(error.message.contains("conflicting rows for seq 1"));

        // A conflict fails the read without creating a receipt.
        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let mut table = Table::new(vec![ev(1), changed]);
        let error = read_with(&file, &binding(), &mut table, options(10)).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(pending(&file).is_none());
    }

    #[test]
    fn a_second_collector_fails_within_a_batch_and_after_pinning() {
        let state = CursorState::new(&binding());
        let mut foreign = ev(2);
        foreign.collector = OTHER_COLLECTOR.into();
        let error = assemble(&state, &[ev(1), foreign.clone()], options(10)).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(error.message.contains("exactly one collector"));

        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let mut table = Table::new(vec![ev(1)]);
        let first = json(&read_doc(&file, &mut table, options(10)));
        ack(&file, &token(&first)).unwrap();
        table.rows.push(foreign);
        let error = read_with(&file, &binding(), &mut table, options(10)).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
    }

    #[test]
    fn foreign_sessions_early_rows_and_rebound_cursors_fail() {
        let state = CursorState::new(&binding());
        let mut foreign = ev(1);
        foreign.session = "run-2".into();
        assert_eq!(
            assemble(&state, &[foreign], options(10)).unwrap_err().code,
            ErrorCode::InvalidResponse
        );
        let mut advanced = CursorState::new(&binding());
        advanced.next_seq = 5;
        assert_eq!(
            assemble(&advanced, &[ev(4)], options(10)).unwrap_err().code,
            ErrorCode::InvalidResponse
        );
        assert_eq!(
            assemble(&state, &[ev(2), ev(1)], options(10))
                .unwrap_err()
                .code,
            ErrorCode::InvalidResponse
        );
        assert_eq!(
            assemble(&state, &[ev(1), ev(2), ev(3)], options(1))
                .unwrap_err()
                .code,
            ErrorCode::InvalidResponse
        );

        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        read_doc(&file, &mut Table::default(), options(10));
        for other in [
            Binding {
                session: "run-2".into(),
                ..binding()
            },
            Binding {
                dataset: "other".into(),
                ..binding()
            },
            Binding {
                endpoint: "http://elsewhere.invalid".into(),
                ..binding()
            },
            Binding {
                tenant_id: "other-tenant".into(),
                ..binding()
            },
            Binding {
                workspace_id: "other-workspace".into(),
                ..binding()
            },
        ] {
            let error = read_with(&file, &other, &mut Table::default(), options(10)).unwrap_err();
            assert_eq!(error.code, ErrorCode::Conflict);
            assert_eq!(
                acknowledge(&file, &other, "token").unwrap_err().code,
                ErrorCode::Conflict
            );
        }
    }

    #[test]
    fn count_and_byte_bounds_set_has_more_and_report_exact_size() {
        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let padding = "x".repeat(100);
        let rows: Vec<Row> = (1..=10)
            .map(|seq| {
                row(
                    seq,
                    Kind::Event,
                    &format!(r#"{{"n":{seq},"pad":"{padding}"}}"#),
                )
            })
            .collect();
        let mut table = Table::new(rows.clone());
        let bounded = ReadOptions {
            max_bytes: 1024,
            ..options(10)
        };
        let text = read_doc(&file, &mut table, bounded);
        let document = json(&text);
        let count = seqs(&document).len();
        assert!((1..10).contains(&count), "{count}");
        assert_eq!(document["count"], count as u64);
        assert_eq!(document["has_more"], true);
        assert_eq!(document["bytes"].as_u64().unwrap(), text.len() as u64);
        assert!(text.len() <= 1024);

        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let document = json(&read_doc(&file, &mut Table::new(rows), options(3)));
        assert_eq!(seqs(&document), [1, 2, 3]);
        assert_eq!(document["has_more"], true);
    }

    #[test]
    fn an_event_larger_than_max_bytes_fails_without_advancing() {
        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let large = format!(r#"{{"blob":"{}"}}"#, "y".repeat(5000));
        let mut table = Table::new(vec![row(1, Kind::Event, &large), ev(2)]);
        let bounded = ReadOptions {
            max_bytes: 1024,
            ..options(10)
        };
        let error = read_with(&file, &binding(), &mut table, bounded).unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidInput);
        assert!(error.message.contains("event seq 1 needs"));
        let state = file.load::<CursorState>().unwrap().unwrap();
        assert!(state.pending.is_none());
        assert_eq!(state.next_seq, 1);
    }

    #[test]
    fn completion_requires_contiguity_and_then_needs_no_queries() {
        let dir = Dir::new();
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let mut table = Table::new(vec![ev(1), done(3)]);
        let first = json(&read_doc(&file, &mut table, options(10)));
        assert_eq!(seqs(&first), [1]);
        assert_eq!(first["session_complete"], false);
        ack(&file, &token(&first)).unwrap();

        table.rows.push(ev(2));
        let last = json(&read_doc(&file, &mut table, options(1)));
        // The marker rides along even when events fill --limit.
        assert_eq!(seqs(&last), [2]);
        assert_eq!(last["session_complete"], true);
        assert_eq!(last["has_more"], false);
        assert_eq!(last["next_seq"], 4);
        let acknowledged = ack(&file, &token(&last)).unwrap();
        assert!(acknowledged.session_complete);
        assert_eq!(acknowledged.next_seq, 4);

        let calls = table.calls.len();
        let complete = json(&read_doc(&file, &mut table, options(10)));
        assert_eq!(complete["status"], "complete");
        assert_eq!(complete["session_complete"], true);
        assert_eq!(complete["next_cursor"], Value::Null);
        assert_eq!(table.calls.len(), calls);

        let state = CursorState::new(&binding());
        assert_eq!(
            assemble(&state, &[done(1), ev(2)], options(10))
                .unwrap_err()
                .code,
            ErrorCode::Conflict
        );
        assert_eq!(
            assemble(&state, &[row(1, Kind::Complete, r#"{"x":1}"#)], options(10))
                .unwrap_err()
                .code,
            ErrorCode::InvalidResponse
        );
        let Assembly::Ready {
            observation,
            complete,
        } = assemble(&state, &[done(1)], options(10)).unwrap()
        else {
            panic!("expected completion receipt");
        };
        assert!(complete && observation.events.is_empty());
    }

    #[test]
    fn decoding_accepts_parseable_number_shapes_and_rejects_bad_rows() {
        let record = |seq: Value, payload: &str| {
            serde_json::json!({
                "pb_session": SESSION, "pb_collector": COLLECTOR, "pb_seq": seq,
                "pb_event_id": "e", "pb_kind": "event", "pb_source": "s",
                "pb_generation": "g", "pb_source_seq": "7", "pb_host": "h",
                "pb_collected_at": "2026-10-15T00:00:00.000", "pb_payload": payload,
                "p_timestamp": "ignored",
            })
        };
        let body =
            serde_json::to_vec(&[record(json("1.0"), "{}"), record(json("2"), "{}")]).unwrap();
        let rows = decode_rows(&body, 2).unwrap();
        assert_eq!((rows[0].seq, rows[1].seq, rows[0].source_seq), (1, 2, 7));
        assert_eq!(
            decode_rows(&body, 1).unwrap_err().code,
            ErrorCode::InvalidResponse
        );
        let wrapped = serde_json::to_vec(&serde_json::json!({
            "fields": [], "records": [record(json("3"), "{}")]
        }))
        .unwrap();
        assert_eq!(decode_rows(&wrapped, 1).unwrap()[0].seq, 3);
        for bad in [json("1.5"), json("-1"), json("\"x\""), Value::Null] {
            let body = serde_json::to_vec(&[record(bad, "{}")]).unwrap();
            assert_eq!(
                decode_rows(&body, 1).unwrap_err().code,
                ErrorCode::InvalidResponse
            );
        }
        let mut missing = record(json("1"), "{}");
        missing.as_object_mut().unwrap().remove("pb_collector");
        let body = serde_json::to_vec(&[missing]).unwrap();
        assert!(
            decode_rows(&body, 1)
                .unwrap_err()
                .message
                .contains("missing pb_collector")
        );
        assert!(decode_rows(b"{\"error\":1}", 1).is_err());

        let state = CursorState::new(&binding());
        for payload in ["[1]", "\"text\"", "{broken"] {
            let error = assemble(&state, &[row(1, Kind::Event, payload)], options(10)).unwrap_err();
            assert_eq!(error.code, ErrorCode::InvalidResponse, "{payload}");
        }
    }

    #[test]
    fn wait_durations_are_bounded() {
        for (text, expected) in [
            ("0", Duration::ZERO),
            ("0s", Duration::ZERO),
            ("500ms", Duration::from_millis(500)),
            ("1m30s", Duration::from_secs(90)),
            ("1.5s", Duration::from_millis(1500)),
            ("5m", MAX_WAIT),
        ] {
            assert_eq!(parse_wait(text).unwrap(), expected, "{text}");
        }
        for text in ["", "5", "-1s", "301s", "1h", "abc", "1..5s", ".s", "10x"] {
            assert_eq!(
                parse_wait(text).unwrap_err().code,
                ErrorCode::InvalidInput,
                "{text}"
            );
        }
    }

    #[test]
    fn finished_documents_report_their_own_length_and_render_as_text() {
        let state = CursorState::new(&binding());
        for status in [Status::Empty, Status::Gap, Status::Complete] {
            let document = Observation::new(&state, status).finish().unwrap();
            let value = json(document.get());
            assert_eq!(
                value["bytes"].as_u64().unwrap(),
                document.get().len() as u64
            );
        }
        let Assembly::Ready {
            mut observation, ..
        } = assemble(&state, &[ev(1), ev(2)], options(10)).unwrap()
        else {
            panic!("expected events");
        };
        observation.next_cursor = Some("receipt-1".into());
        let document = observation.finish().unwrap();
        let text = String::from_utf8(render_text(&document).unwrap()).unwrap();
        assert!(text.starts_with("2 event(s) for session run-1 (seq 1 to 2)"));
        assert!(text.contains("--ack receipt-1"));
        assert!(text.contains("1\tevent-1\t{\"n\":1}\n"));
    }

    /// Accept one HTTP/1.1 request per response, recording each request.
    fn serve(
        responses: Vec<(u16, &'static str, Vec<u8>)>,
    ) -> (String, thread::JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let handle = thread::spawn(move || {
            let mut requests = Vec::new();
            for (status, reason, body) in responses {
                let (stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut reader = BufReader::new(stream);
                let mut head = String::new();
                loop {
                    let mut line = String::new();
                    reader.read_line(&mut line).unwrap();
                    head.push_str(&line);
                    if line == "\r\n" {
                        break;
                    }
                }
                let lower = head.to_ascii_lowercase();
                let mut request_body = Vec::new();
                if let Some(length) = lower
                    .lines()
                    .find_map(|l| l.strip_prefix("content-length:"))
                {
                    request_body.resize(length.trim().parse().unwrap(), 0);
                    reader.read_exact(&mut request_body).unwrap();
                } else if lower.contains("transfer-encoding: chunked") {
                    loop {
                        let mut size = String::new();
                        reader.read_line(&mut size).unwrap();
                        let size = usize::from_str_radix(size.trim(), 16).unwrap();
                        let mut chunk = vec![0; size + 2];
                        reader.read_exact(&mut chunk).unwrap();
                        if size == 0 {
                            break;
                        }
                        request_body.extend_from_slice(&chunk[..size]);
                    }
                }
                requests.push(format!(
                    "{head}{}",
                    String::from_utf8(request_body).unwrap()
                ));
                let mut stream = reader.into_inner();
                let _ = write!(
                    stream,
                    "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n\
                     Content-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(&body);
            }
            requests
        });
        (endpoint, handle)
    }

    #[test]
    fn http_source_posts_escaped_sql_with_epoch_envelope_and_bounds_responses() {
        let rows = serde_json::to_vec(&serde_json::json!([{
            "pb_session": "o'brien", "pb_collector": COLLECTOR, "pb_seq": 1.0,
            "pb_event_id": "e1", "pb_kind": "event", "pb_source": "s",
            "pb_generation": "g", "pb_source_seq": 1, "pb_host": "h",
            "pb_collected_at": "2026-10-15T00:00:00Z", "pb_payload": "{\"a\":1}",
        }]))
        .unwrap();
        let (endpoint, server) = serve(vec![
            (200, "OK", rows),
            (404, "Not Found", b"stream not found".to_vec()),
            (200, "OK", vec![b' '; 200]),
        ]);
        let dir = Dir::new();
        let profile = crate::config::ProfileRecord {
            url: endpoint,
            api_key: "fixture-key".into(),
            ..Default::default()
        };
        let client = ApiClient::new(profile, ProfileStore::new(dir.0.join("config.toml"))).unwrap();
        let mut source = HttpSource {
            client: &client,
            dataset: "my\"data",
            session: "o'brien",
            max_response: 64 * 1024,
        };
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let timeout = Duration::from_secs(5);
        let fetched = runtime.block_on(source.fetch(4, 2, timeout)).unwrap();
        assert_eq!(fetched.len(), 1);
        assert_eq!(
            (fetched[0].seq, fetched[0].payload.as_str()),
            (1, "{\"a\":1}")
        );

        let error = runtime.block_on(source.fetch(4, 2, timeout)).unwrap_err();
        assert_eq!(error.code, ErrorCode::NotFound);
        assert!(
            error
                .message
                .contains("observe query failed: 404 Not Found"),
            "{}",
            error.message
        );

        source.max_response = 64;
        let error = runtime.block_on(source.fetch(4, 2, timeout)).unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidResponse);
        assert!(error.message.contains("exceeds 64 bytes"));

        let requests = server.join().unwrap();
        assert!(
            requests[0].starts_with("POST /api/v1/query HTTP/1.1\r\n"),
            "{}",
            requests[0]
        );
        let body = json(requests[0].split("\r\n\r\n").nth(1).unwrap());
        assert_eq!(body["query"], query_sql("my\"data", "o'brien", 4, 2));
        assert_eq!(body["startTime"], EPOCH);
        assert_eq!(body["endTime"], "now");
    }

    #[test]
    fn positive_wait_bounds_the_first_slow_http_request_without_a_receipt() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            thread::sleep(Duration::from_millis(750));
            let _ = stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n[]");
        });
        let dir = Dir::new();
        let profile = crate::config::ProfileRecord {
            url: endpoint,
            api_key: "fixture-key".into(),
            ..Default::default()
        };
        let client = ApiClient::new(profile, ProfileStore::new(dir.0.join("config.toml"))).unwrap();
        let mut source = HttpSource {
            client: &client,
            dataset: "app",
            session: SESSION,
            max_response: MAX_RESPONSE_BYTES,
        };
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let started = std::time::Instant::now();
        let error = runtime
            .block_on(read(
                &file,
                &binding(),
                ReadOptions {
                    wait: Duration::from_millis(250),
                    ..options(1)
                },
                &mut source,
            ))
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Timeout);
        assert!(started.elapsed() < Duration::from_secs(1));
        let state = file.load::<CursorState>().unwrap().unwrap();
        assert!(state.pending.is_none());
        assert_eq!(state.next_seq, 1);
        server.join().unwrap();
    }

    #[test]
    fn minimum_limit_can_fetch_two_maximum_escaped_payloads() {
        let empty = serde_json::to_string(&serde_json::json!({"message": ""})).unwrap();
        let slashes = (4 * 1024 * 1024 - empty.len()) / 2;
        let payload =
            serde_json::to_string(&serde_json::json!({"message": "\\".repeat(slashes)})).unwrap();
        assert_eq!(payload.len(), 4 * 1024 * 1024);
        let records: Vec<Value> = (1..=2)
            .map(|seq| {
                serde_json::json!({
                    "pb_session": SESSION, "pb_collector": COLLECTOR, "pb_seq": seq,
                    "pb_event_id": format!("event-{seq}"), "pb_kind": "event", "pb_source": "s",
                    "pb_generation": "g", "pb_source_seq": seq, "pb_host": "h",
                    "pb_collected_at": "2026-10-15T00:00:00Z", "pb_payload": payload,
                })
            })
            .collect();
        let response = serde_json::to_vec(&records).unwrap();
        assert!(response.len() > 16 * 1024 * 1024);
        assert!(response.len() < MAX_RESPONSE_BYTES);
        let (endpoint, server) = serve(vec![(200, "OK", response)]);
        let dir = Dir::new();
        let profile = crate::config::ProfileRecord {
            url: endpoint,
            api_key: "fixture-key".into(),
            ..Default::default()
        };
        let client = ApiClient::new(profile, ProfileStore::new(dir.0.join("config.toml"))).unwrap();
        let mut source = HttpSource {
            client: &client,
            dataset: "app",
            session: SESSION,
            max_response: MAX_RESPONSE_BYTES,
        };
        let file = StateFile::lock(&dir.cursor()).unwrap();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let document = runtime
            .block_on(read(
                &file,
                &binding(),
                ReadOptions {
                    max_bytes: MAX_MAX_BYTES as usize,
                    ..options(1)
                },
                &mut source,
            ))
            .unwrap();
        let value = json(document.get());
        assert_eq!(seqs(&value), [1]);
        assert_eq!(value["has_more"], true);
        assert!(document.get().len() <= MAX_MAX_BYTES as usize);
        server.join().unwrap();
    }
}
