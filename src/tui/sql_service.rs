//! Concrete network effects of the interactive SQL session.
//!
//! Each operation keeps the pinned Go policy of its own call site
//! (pkg/model/query.go): result windows use a 50s client deadline,
//! schema discovery 15s, and dataset discovery the shared 15s Prism policy.
//! Completions carry plain data back to the session driver; no operation
//! retries, and none touches session state.
use super::json::{ReusedSlice, json_field_is};
use crate::{
    api::{ApiClient, StreamingResponse},
    gojson::Node,
};
use http::Method;
use std::{collections::BTreeMap, sync::Arc, time::Duration};

pub(crate) const WINDOW_TIMEOUT: Duration = Duration::from_secs(50);
pub(crate) const SCHEMA_TIMEOUT: Duration = Duration::from_secs(15);

/// One decoded `interface{}` record value, reduced at decode time to what the
/// table observes: a Go string keeps its text (timestamp formatting and
/// column-width inference test for `string`), anything else keeps its
/// `fmt.Sprintf("%v", v)` text. No JSON tree outlives decoding.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Cell {
    Str(String),
    Value(String),
}

impl Cell {
    /// `fmt.Sprintf("%v", v)`.
    pub(crate) fn display(&self) -> &str {
        match self {
            Self::Str(text) | Self::Value(text) => text,
        }
    }
}

pub(crate) type Record = BTreeMap<String, Cell>;

/// `model.QueryData` after `json.Decoder.Decode`.
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct QueryData {
    pub(crate) fields: Vec<String>,
    pub(crate) records: Vec<Record>,
}

/// An owned snapshot of one window fetch (NewSQLWindowFetchTask).
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct WindowRequest {
    pub(crate) run_id: u64,
    pub(crate) window_index: usize,
    /// The wire query: base query with `LIMIT fetch OFFSET base+index*size`.
    pub(crate) query: String,
    pub(crate) start_utc: String,
    pub(crate) end_utc: String,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct WindowCompletion {
    pub(crate) run_id: u64,
    pub(crate) window_index: usize,
    pub(crate) result: Result<QueryData, String>,
}

/// `schemaMsg`: deliberately carries no dataset name (Go has no stale guard).
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct SchemaCompletion {
    pub(crate) columns: Vec<String>,
    pub(crate) error: String,
}

/// `datasetListMsg`.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct DatasetsCompletion {
    pub(crate) result: Result<Vec<String>, String>,
}

/// The session's only network boundary. Go builds a new HTTP client from
/// the immutable session-start profile inside every command closure
/// (`internalHTTP.DefaultClient(&profile)`), so an OAuth refresh in one
/// effect is never seen by another. Each operation therefore runs on its own
/// `effect_scope()` of the session-start client; only the transport pool is
/// shared.
#[derive(Clone)]
pub(crate) struct SqlSessionService {
    client: Arc<ApiClient>,
}

impl SqlSessionService {
    pub(crate) fn new(client: ApiClient) -> Self {
        Self {
            client: Arc::new(client),
        }
    }

    pub(crate) async fn fetch_window(&self, request: WindowRequest) -> WindowCompletion {
        let client = self.client.effect_scope();
        let result = fetch_raw(
            &client,
            &request.query,
            &request.start_utc,
            &request.end_utc,
            WINDOW_TIMEOUT,
        )
        .await;
        WindowCompletion {
            run_id: request.run_id,
            window_index: request.window_index,
            result,
        }
    }

    /// fetchStreamSchema: GET logstream schema, else a one-row query fallback.
    /// One Go client serves both attempts, so both share one scope. `stream`
    /// holds Go's bytes, which can be invalid UTF-8 (sql.rs L-SQL-4).
    pub(crate) async fn fetch_schema(&self, stream: Vec<u8>) -> SchemaCompletion {
        if stream.is_empty() {
            return SchemaCompletion::default();
        }
        let client = self.client.effect_scope();
        let profile_url = client.profile().url;
        // Go ignores the JoinPath error. An empty request URL can be
        // constructed, but sending it fails; the schema path then falls back.
        if let Ok(endpoint) = schema_endpoint(&profile_url, &stream) {
            match client.prepare_auth_get(endpoint, SCHEMA_TIMEOUT) {
                // AddAuthHeaders failure returns immediately with its message.
                Err(error) => {
                    return SchemaCompletion {
                        columns: Vec::new(),
                        error: error.to_string(),
                    };
                }
                Ok(request) => {
                    if let Ok(response) = client.send_streaming(request).await {
                        let status = response.status;
                        let body = read_all_lossy(response).await;
                        if status == 200
                            && let Some(names) = decode_arrow_schema_names(&body)
                        {
                            return SchemaCompletion {
                                columns: names,
                                error: String::new(),
                            };
                        }
                    }
                }
            }
        }
        // json.Marshal later replaces each invalid byte with U+FFFD; the
        // decoded request string is identical (see docs/phase7-sql.md L-SQL-4).
        let query = super::sql::prepare_fetch_query(&format!(
            "SELECT * FROM '{}' LIMIT 1",
            crate::error::go_string(&stream)
        ));
        // Go samples the fallback end time after the unsuccessful GET/body/decode,
        // immediately before fetchData, rather than when the schema effect starts.
        let now_utc = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
        match fetch_raw(
            &client,
            &query,
            "2000-01-01T00:00:00+00:00",
            &now_utc,
            SCHEMA_TIMEOUT,
        )
        .await
        {
            Ok(data) => SchemaCompletion {
                columns: data.fields,
                error: String::new(),
            },
            Err(error) => SchemaCompletion {
                columns: Vec::new(),
                error,
            },
        }
    }

    /// fetchAllStreams: Prism home datasets of type "logs", sorted by title.
    pub(crate) async fn fetch_datasets(&self) -> DatasetsCompletion {
        let client = self.client.effect_scope();
        let result = match crate::datasets::fetch_home(&client).await {
            Ok(items) => {
                let mut names: Vec<String> = items
                    .into_iter()
                    .filter(|item| item.dataset_type == "logs")
                    .map(|item| item.title)
                    .collect();
                names.sort();
                Ok(names)
            }
            Err(error) => Err(error.message),
        };
        DatasetsCompletion { result }
    }
}

/// fetchDataRaw: POST api/v1/query?fields=true with the caller's deadline.
async fn fetch_raw(
    client: &ApiClient,
    query: &str,
    start: &str,
    end: &str,
    timeout: Duration,
) -> Result<QueryData, String> {
    let body = query_body(query, start, end);
    let request = client
        .prepare_standard_query(
            Method::POST,
            "query",
            &[("fields", "true")],
            Some(body),
            timeout,
        )
        .map_err(|e| e.to_string())?;
    let mut response = client
        .send_streaming(request)
        .await
        .map_err(|e| e.to_string())?;
    if response.status != 200 {
        let status = status_text(&response);
        let body = read_all_lossy(response).await;
        let trimmed = String::from_utf8_lossy(&body).trim().to_owned();
        return Err(if trimmed.is_empty() { status } else { trimmed });
    }
    // json.Decoder.Decode: read only until the first value is complete
    // (or a syntax error is seen), then return. Dropping the response
    // closes the unread tail as `defer resp.Body.Close()` does.
    let mut reader = FirstValueReader::default();
    loop {
        if let Some(outcome) = reader.scan() {
            return outcome.and_then(|end| decode_query_value(&reader.buffer[..end]));
        }
        match response.chunk().await {
            Ok(Some(chunk)) => reader.buffer.extend_from_slice(&chunk),
            Ok(None) => {
                return reader
                    .finish()
                    .and_then(|end| decode_query_value(&reader.buffer[..end]));
            }
            Err(error) => return Err(error.to_string()),
        }
    }
}

/// Go URL path joining preserves invalid UTF-8 bytes until percent encoding.
fn schema_endpoint(base: &str, stream: &[u8]) -> Result<String, crate::api::ApiError> {
    crate::api::append_url_bytes(base, &[b"api/v1/logstream", stream, b"schema"])
}

fn status_text(response: &StreamingResponse) -> String {
    response.status_text.clone()
}

/// io.ReadAll whose error the Go call sites ignore: keep what arrived.
async fn read_all_lossy(mut response: StreamingResponse) -> Vec<u8> {
    let mut body = Vec::new();
    while let Ok(Some(chunk)) = response.chunk().await {
        body.extend_from_slice(&chunk);
    }
    body
}

/// json.Marshal(map[string]string{...}): sorted keys and HTML escaping.
pub(crate) fn query_body(query: &str, start: &str, end: &str) -> Vec<u8> {
    #[derive(serde::Serialize)]
    struct Body<'a> {
        #[serde(rename = "endTime")]
        end: &'a str,
        query: &'a str,
        #[serde(rename = "startTime")]
        start: &'a str,
    }
    serde_json::to_string(&Body { end, query, start })
        .expect("strings serialize")
        .replace('&', "\\u0026")
        .replace('<', "\\u003c")
        .replace('>', "\\u003e")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029")
        .into_bytes()
}

// ---------------------------------------------------------------------------
// encoding/json Decoder.readValue: the scanner state machine (scanner.go).
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq)]
enum Step {
    BeginValueOrEmpty,
    BeginValue,
    BeginStringOrEmpty,
    BeginString,
    EndValue,
    EndTop,
    InString,
    InStringEsc,
    EscU,
    EscU1,
    EscU12,
    EscU123,
    Neg,
    One,
    Zero,
    Dot,
    Dot0,
    E,
    ESign,
    E0,
    T,
    Tr,
    Tru,
    F,
    Fa,
    Fal,
    Fals,
    N,
    Nu,
    Nul,
    Error,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Op {
    Continue,
    BeginObject,
    BeginArray,
    EndObject,
    EndArray,
    End,
    Error,
}

const PARSE_OBJECT_KEY: u8 = 0;
const PARSE_OBJECT_VALUE: u8 = 1;
const PARSE_ARRAY_VALUE: u8 = 2;
const MAX_NESTING_DEPTH: usize = 10000;

/// Go's scanner, one byte at a time; nesting lives on a heap stack.
struct Scanner {
    step: Step,
    parse_state: Vec<u8>,
    error: Option<String>,
}

impl Default for Scanner {
    fn default() -> Self {
        Self {
            step: Step::BeginValue,
            parse_state: Vec::new(),
            error: None,
        }
    }
}

fn is_space(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\r' | b'\n')
}
fn is_hex(c: u8) -> bool {
    c.is_ascii_hexdigit()
}

impl Scanner {
    fn fail(&mut self, c: u8, context: &str) -> Op {
        self.step = Step::Error;
        self.error = Some(format!("invalid character {} {context}", quote_char(c)));
        Op::Error
    }
    fn push(&mut self, c: u8, state: u8, success: Op) -> Op {
        self.parse_state.push(state);
        if self.parse_state.len() <= MAX_NESTING_DEPTH {
            success
        } else {
            self.fail(c, "exceeded max depth")
        }
    }
    fn pop(&mut self) {
        self.parse_state.pop();
        self.step = if self.parse_state.is_empty() {
            Step::EndTop
        } else {
            Step::EndValue
        };
    }
    fn literal(&mut self, c: u8, expected: u8, next: Step, context: &str) -> Op {
        if c == expected {
            self.step = next;
            Op::Continue
        } else {
            self.fail(c, context)
        }
    }

    fn step(&mut self, c: u8) -> Op {
        use Step::*;
        match self.step {
            BeginValueOrEmpty => {
                if is_space(c) {
                    Op::Continue
                } else if c == b']' {
                    self.end_value(c)
                } else {
                    self.begin_value(c)
                }
            }
            BeginValue => self.begin_value(c),
            BeginStringOrEmpty => {
                if is_space(c) {
                    Op::Continue
                } else if c == b'}' {
                    *self.parse_state.last_mut().expect("inside object") = PARSE_OBJECT_VALUE;
                    self.end_value(c)
                } else {
                    self.begin_string(c)
                }
            }
            BeginString => self.begin_string(c),
            EndValue => self.end_value(c),
            EndTop => self.end_top(c),
            InString => {
                if c == b'"' {
                    self.step = EndValue;
                    Op::Continue
                } else if c == b'\\' {
                    self.step = InStringEsc;
                    Op::Continue
                } else if c < 0x20 {
                    self.fail(c, "in string literal")
                } else {
                    Op::Continue
                }
            }
            InStringEsc => match c {
                b'b' | b'f' | b'n' | b'r' | b't' | b'\\' | b'/' | b'"' => {
                    self.step = InString;
                    Op::Continue
                }
                b'u' => {
                    self.step = EscU;
                    Op::Continue
                }
                _ => self.fail(c, "in string escape code"),
            },
            EscU | EscU1 | EscU12 | EscU123 => {
                if is_hex(c) {
                    self.step = match self.step {
                        EscU => EscU1,
                        EscU1 => EscU12,
                        EscU12 => EscU123,
                        _ => InString,
                    };
                    Op::Continue
                } else {
                    self.fail(c, "in \\u hexadecimal character escape")
                }
            }
            Neg => {
                if c == b'0' {
                    self.step = Zero;
                    Op::Continue
                } else if (b'1'..=b'9').contains(&c) {
                    self.step = One;
                    Op::Continue
                } else {
                    self.fail(c, "in numeric literal")
                }
            }
            One => {
                if c.is_ascii_digit() {
                    Op::Continue
                } else {
                    self.zero(c)
                }
            }
            Zero => self.zero(c),
            Dot => {
                if c.is_ascii_digit() {
                    self.step = Dot0;
                    Op::Continue
                } else {
                    self.fail(c, "after decimal point in numeric literal")
                }
            }
            Dot0 => {
                if c.is_ascii_digit() {
                    Op::Continue
                } else if c == b'e' || c == b'E' {
                    self.step = E;
                    Op::Continue
                } else {
                    self.end_value(c)
                }
            }
            E => {
                if c == b'+' || c == b'-' {
                    self.step = ESign;
                    Op::Continue
                } else {
                    self.e_sign(c)
                }
            }
            ESign => self.e_sign(c),
            E0 => {
                if c.is_ascii_digit() {
                    Op::Continue
                } else {
                    self.end_value(c)
                }
            }
            T => self.literal(c, b'r', Tr, "in literal true (expecting 'r')"),
            Tr => self.literal(c, b'u', Tru, "in literal true (expecting 'u')"),
            Tru => self.literal(c, b'e', EndValue, "in literal true (expecting 'e')"),
            F => self.literal(c, b'a', Fa, "in literal false (expecting 'a')"),
            Fa => self.literal(c, b'l', Fal, "in literal false (expecting 'l')"),
            Fal => self.literal(c, b's', Fals, "in literal false (expecting 's')"),
            Fals => self.literal(c, b'e', EndValue, "in literal false (expecting 'e')"),
            N => self.literal(c, b'u', Nu, "in literal null (expecting 'u')"),
            Nu => self.literal(c, b'l', Nul, "in literal null (expecting 'l')"),
            Nul => self.literal(c, b'l', EndValue, "in literal null (expecting 'l')"),
            Error => Op::Error,
        }
    }

    fn begin_value(&mut self, c: u8) -> Op {
        use Step::*;
        if is_space(c) {
            return Op::Continue;
        }
        let next = match c {
            b'{' => {
                self.step = BeginStringOrEmpty;
                return self.push(c, PARSE_OBJECT_KEY, Op::BeginObject);
            }
            b'[' => {
                self.step = BeginValueOrEmpty;
                return self.push(c, PARSE_ARRAY_VALUE, Op::BeginArray);
            }
            b'"' => InString,
            b'-' => Neg,
            b'0' => Zero,
            b't' => T,
            b'f' => F,
            b'n' => N,
            b'1'..=b'9' => One,
            _ => return self.fail(c, "looking for beginning of value"),
        };
        self.step = next;
        Op::Continue
    }
    fn begin_string(&mut self, c: u8) -> Op {
        if is_space(c) {
            Op::Continue
        } else if c == b'"' {
            self.step = Step::InString;
            Op::Continue
        } else {
            self.fail(c, "looking for beginning of object key string")
        }
    }
    fn end_value(&mut self, c: u8) -> Op {
        let Some(&state) = self.parse_state.last() else {
            self.step = Step::EndTop;
            return self.end_top(c);
        };
        if is_space(c) {
            self.step = Step::EndValue;
            return Op::Continue;
        }
        let last = self.parse_state.len() - 1;
        match state {
            PARSE_OBJECT_KEY => {
                if c == b':' {
                    self.parse_state[last] = PARSE_OBJECT_VALUE;
                    self.step = Step::BeginValue;
                    Op::Continue
                } else {
                    self.fail(c, "after object key")
                }
            }
            PARSE_OBJECT_VALUE => {
                if c == b',' {
                    self.parse_state[last] = PARSE_OBJECT_KEY;
                    self.step = Step::BeginString;
                    Op::Continue
                } else if c == b'}' {
                    self.pop();
                    Op::EndObject
                } else {
                    self.fail(c, "after object key:value pair")
                }
            }
            _ => {
                if c == b',' {
                    self.step = Step::BeginValue;
                    Op::Continue
                } else if c == b']' {
                    self.pop();
                    Op::EndArray
                } else {
                    self.fail(c, "after array element")
                }
            }
        }
    }
    fn end_top(&mut self, c: u8) -> Op {
        if !is_space(c) {
            // Recorded for a later call; readValue stops at this scanEnd.
            self.step = Step::Error;
            self.error = Some(format!(
                "invalid character {} after top-level value",
                quote_char(c)
            ));
        }
        Op::End
    }
    fn zero(&mut self, c: u8) -> Op {
        if c == b'.' {
            self.step = Step::Dot;
            Op::Continue
        } else if c == b'e' || c == b'E' {
            self.step = Step::E;
            Op::Continue
        } else {
            self.end_value(c)
        }
    }
    fn e_sign(&mut self, c: u8) -> Op {
        if c.is_ascii_digit() {
            self.step = Step::E0;
            Op::Continue
        } else {
            self.fail(c, "in exponent of numeric literal")
        }
    }
}

/// scanner.go quoteChar.
fn quote_char(c: u8) -> String {
    match c {
        b'\'' => r"'\''".into(),
        b'"' => r#"'"'"#.into(),
        b'\\' => r"'\\'".into(),
        0x07 => r"'\a'".into(),
        0x08 => r"'\b'".into(),
        0x0c => r"'\f'".into(),
        b'\n' => r"'\n'".into(),
        b'\r' => r"'\r'".into(),
        b'\t' => r"'\t'".into(),
        0x0b => r"'\v'".into(),
        0x00..=0x1f | 0x7f => format!("'\\x{c:02x}'"),
        // string(byte) is the rune U+0080..U+00FF; strconv.Quote escapes the
        // C1 controls, NBSP and the soft hyphen, and prints the rest.
        0x80..=0xa0 | 0xad => format!("'\\u{c:04x}'"),
        _ => format!("'{}'", char::from(c)),
    }
}

/// The bytes of one `Decoder.Decode` call: grows by response chunks and is
/// scanned exactly once per byte.
#[derive(Default)]
struct FirstValueReader {
    buffer: Vec<u8>,
    scanned: usize,
    scanner: Scanner,
}

impl FirstValueReader {
    /// `Some(Ok(end))` once the first value ends at `end`, `Some(Err)` on a
    /// syntax error, `None` when more input is needed.
    fn scan(&mut self) -> Option<Result<usize, String>> {
        while self.scanned < self.buffer.len() {
            let c = self.buffer[self.scanned];
            match self.scanner.step(c) {
                // scanEnd is reported on the byte after the value.
                Op::End => return Some(Ok(self.scanned)),
                Op::EndObject | Op::EndArray => {
                    if self.scanner.end_value(b' ') == Op::End {
                        return Some(Ok(self.scanned + 1));
                    }
                }
                Op::Error => {
                    return Some(Err(self.scanner.error.clone().unwrap_or_default()));
                }
                _ => {}
            }
            self.scanned += 1;
        }
        None
    }
    /// io.EOF handling of readValue.
    fn finish(&mut self) -> Result<usize, String> {
        if self.scanner.step(b' ') == Op::End {
            return Ok(self.buffer.len());
        }
        if self.buffer.iter().any(|&b| !is_space(b)) {
            Err("unexpected EOF".into())
        } else {
            Err("EOF".into())
        }
    }
}

/// Decode a complete, syntactically valid value as `model.QueryData`.
/// Type mismatches report Go's first saved UnmarshalTypeError.
pub(crate) fn decode_query_value(value: &[u8]) -> Result<QueryData, String> {
    let node = crate::gojson::decode_ordered(value)?.into_inner();
    let result = query_data(&node);
    crate::gojson::drop_node(node);
    result
}

fn query_data(node: &Node) -> Result<QueryData, String> {
    if node.is_null() {
        return Ok(QueryData::default());
    }
    let Some(members) = node.as_object() else {
        return Err(format!(
            "json: cannot unmarshal {} into Go value of type model.QueryData",
            go_kind(node)
        ));
    };
    let mut first_error: Option<String> = None;
    let mut note = |message: String| {
        first_error.get_or_insert(message);
    };
    // A duplicate `fields`/`records` key decodes into the same slices
    // (decode.go `array`): earlier slots, including ones a shorter array
    // hid, are reused; null into a string slot is a no-op; an object merges
    // into the slot's map, and null resets it. `[]` and null drop all slots.
    let mut fields = ReusedSlice::<String>::default();
    let mut records = ReusedSlice::<Record>::default();
    for (key, value) in members {
        match field_name(key) {
            Some("fields") => {
                if value.is_null() {
                    fields.clear();
                    continue;
                }
                let Some(items) = value.as_array() else {
                    note(type_error(go_kind(value), "fields", "[]string"));
                    continue;
                };
                if items.is_empty() {
                    fields.clear();
                    continue;
                }
                for (index, item) in items.iter().enumerate() {
                    let slot = fields.slot(index, String::new);
                    if let Some(text) = item.as_str() {
                        *slot = text.to_owned();
                    } else if !item.is_null() {
                        note(type_error(go_kind(item), "fields", "string"));
                    }
                }
                fields.set_len(items.len());
            }
            Some("records") => {
                if value.is_null() {
                    records.clear();
                    continue;
                }
                let Some(items) = value.as_array() else {
                    note(type_error(
                        go_kind(value),
                        "records",
                        "[]map[string]interface {}",
                    ));
                    continue;
                };
                if items.is_empty() {
                    records.clear();
                    continue;
                }
                for (index, item) in items.iter().enumerate() {
                    let record = records.slot(index, Record::new);
                    if item.is_null() {
                        // null into a map sets it to nil.
                        record.clear();
                    } else if let Some(object) = item.as_object() {
                        for (key, value) in object {
                            // Each value is a fresh interface{}; the last
                            // occurrence of a key wins.
                            let cell = match value.as_str() {
                                Some(text) => Cell::Str(text.to_owned()),
                                None => Cell::Value(
                                    go_display(value)
                                        .map_err(|literal| {
                                            type_error(
                                                &format!("number {literal}"),
                                                "records",
                                                "float64",
                                            )
                                        })
                                        .unwrap_or_else(|message| {
                                            note(message);
                                            "<nil>".into()
                                        }),
                                ),
                            };
                            record.insert(key.clone(), cell);
                        }
                    } else {
                        note(type_error(
                            go_kind(item),
                            "records",
                            "map[string]interface {}",
                        ));
                    }
                }
                records.set_len(items.len());
            }
            _ => {}
        }
    }
    match first_error {
        Some(message) => Err(message),
        None => Ok(QueryData {
            fields: fields.finish(),
            records: records.finish(),
        }),
    }
}

/// Go struct field matching: each key maps by exact name, else by
/// encoding/json's `SimpleFold`-based fold.
fn field_name(key: &str) -> Option<&'static str> {
    ["fields", "records"]
        .into_iter()
        .find(|name| json_field_is(key, name))
}

fn type_error(value: &str, field: &str, target: &str) -> String {
    format!(
        "json: cannot unmarshal {value} into Go struct field QueryData.{field} of type {target}"
    )
}

fn go_kind(value: &Node) -> &'static str {
    if value.is_string() {
        "string"
    } else if value.is_number() {
        "number"
    } else if value.is_boolean() {
        "bool"
    } else if value.is_array() {
        "array"
    } else if value.is_object() {
        "object"
    } else {
        "null"
    }
}

/// `fmt.Sprintf("%v", v)` of a decoded `interface{}`; `Err(literal)` when a
/// number is out of float64 range (Go's convertNumber rejects it).
fn go_display(node: &Node) -> Result<String, String> {
    // json.Decoder converts every number in wire order, including values a
    // later duplicate key replaces. Formatting instead sorts and deduplicates
    // maps, so validate first, once per cell, before that information is lost.
    validate_interface_numbers(node)?;
    let mut out = String::new();
    write_display(node, &mut out)?;
    Ok(out)
}

fn validate_interface_numbers(node: &Node) -> Result<(), String> {
    // Reversed pushes preserve depth-first wire order without recursion at
    // Go's 10,000-level nesting limit.
    let mut pending = vec![node];
    while let Some(node) = pending.pop() {
        if node.is_number() {
            go_float64(node)?;
        } else if let Some(items) = node.as_array() {
            pending.extend(items.iter().rev());
        } else if let Some(members) = node.as_object() {
            pending.extend(members.iter().rev().map(|(_, value)| value));
        }
    }
    Ok(())
}

fn go_float64(node: &Node) -> Result<f64, String> {
    let literal = node.scalar().map(|v| v.to_string()).unwrap_or_default();
    let value: f64 = literal.parse().map_err(|_| literal.clone())?;
    if value.is_infinite() {
        Err(literal)
    } else {
        Ok(value)
    }
}

fn write_display(node: &Node, out: &mut String) -> Result<(), String> {
    stacker::maybe_grow(64 * 1024, 1024 * 1024, || {
        if node.is_null() {
            out.push_str("<nil>");
        } else if let Some(b) = node.as_bool() {
            out.push_str(if b { "true" } else { "false" });
        } else if let Some(text) = node.as_str() {
            out.push_str(text);
        } else if node.is_number() {
            out.push_str(&crate::metrics::go_float(go_float64(node)?));
        } else if let Some(items) = node.as_array() {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(' ');
                }
                write_display(item, out)?;
            }
            out.push(']');
        } else if let Some(members) = node.as_object() {
            // Go maps print sorted by key; duplicates keep the last value.
            let mut sorted: BTreeMap<&str, &Node> = BTreeMap::new();
            for (key, value) in members {
                sorted.insert(key, value);
            }
            out.push_str("map[");
            for (i, (key, value)) in sorted.into_iter().enumerate() {
                if i > 0 {
                    out.push(' ');
                }
                out.push_str(key);
                out.push(':');
                write_display(value, out)?;
            }
            out.push(']');
        }
        Ok(())
    })
}

/// `{"fields":[{"name":...}]}` via json.Unmarshal; any error means "absent".
fn decode_arrow_schema_names(body: &[u8]) -> Option<Vec<String>> {
    let node = crate::gojson::decode_ordered(body).ok()?.into_inner();
    let names = arrow_names(&node);
    crate::gojson::drop_node(node);
    names
}

fn arrow_names(node: &Node) -> Option<Vec<String>> {
    if node.is_null() {
        return None;
    }
    // Each `struct{ Name string }` element is reduced to its name. Like the
    // window decoder, a duplicate `fields` reuses earlier elements, so null
    // or `{}` keeps a name decoded before (and no fallback is taken).
    let mut fields = ReusedSlice::<String>::default();
    for (key, value) in node.as_object()? {
        if !json_field_is(key, "fields") {
            continue;
        }
        if value.is_null() {
            fields.clear();
            continue;
        }
        let items = value.as_array()?;
        if items.is_empty() {
            fields.clear();
            continue;
        }
        for (index, item) in items.iter().enumerate() {
            let name = fields.slot(index, String::new);
            if item.is_null() {
                continue;
            }
            for (k, v) in item.as_object()? {
                if json_field_is(k, "name") && !v.is_null() {
                    *name = v.as_str()?.to_owned();
                }
            }
        }
        fields.set_len(items.len());
    }
    let names: Vec<String> = fields
        .finish()
        .into_iter()
        .filter(|n| !n.is_empty())
        .collect();
    (!names.is_empty()).then_some(names)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn first_value(chunks: &[&[u8]]) -> (Result<usize, String>, usize) {
        let mut reader = FirstValueReader::default();
        for (consumed, chunk) in chunks.iter().enumerate() {
            reader.buffer.extend_from_slice(chunk);
            if let Some(outcome) = reader.scan() {
                return (outcome, consumed + 1);
            }
        }
        (reader.finish(), chunks.len())
    }

    #[test]
    fn decoder_stops_at_first_complete_object_without_reading_tail() {
        // The tail chunk is never requested once the object closes.
        let (outcome, chunks) =
            first_value(&[br#"{"fields":["a"],"#, br#""records":[]}"#, b"HELD"]);
        assert_eq!(outcome, Ok(br#"{"fields":["a"],"records":[]}"#.len()));
        assert_eq!(chunks, 2);
    }

    #[test]
    fn decoder_reports_invalid_prefix_before_tail() {
        let (outcome, chunks) = first_value(&[b"{x", b"never read"]);
        assert_eq!(
            outcome,
            Err("invalid character 'x' looking for beginning of object key string".into())
        );
        assert_eq!(chunks, 1);
        let (outcome, _) = first_value(&[b"<html>"]);
        assert_eq!(
            outcome,
            Err("invalid character '<' looking for beginning of value".into())
        );
    }

    #[test]
    fn decoder_eof_cases_match_read_value() {
        assert_eq!(first_value(&[b""]).0, Err("EOF".into()));
        assert_eq!(first_value(&[b" \n"]).0, Err("EOF".into()));
        assert_eq!(first_value(&[b"{\"a\":"]).0, Err("unexpected EOF".into()));
        // A top-level number ends only at a delimiter or EOF.
        assert_eq!(first_value(&[b"12"]).0, Ok(2));
        assert_eq!(first_value(&[b"null x"]).0, Ok(4));
    }

    #[test]
    fn values_print_like_fmt_v() {
        let data = decode_query_value(
            br#"{"fields":["a"],"records":[{"a":{"b":[1,"x",null,true],"a":{"z":2}},"s":"t","n":1000000}]}"#,
        )
        .unwrap();
        let record = &data.records[0];
        assert_eq!(record["a"].display(), "map[a:map[z:2] b:[1 x <nil> true]]");
        assert_eq!(record["s"], Cell::Str("t".into()));
        assert_eq!(record["n"], Cell::Value("1e+06".into()));
    }

    #[test]
    fn nested_duplicate_values_do_not_hide_numeric_overflow() {
        for nested in [
            r#"{"x":1e400,"x":"visible"}"#,
            r#"{"x":[{"n":1e400}],"x":null}"#,
            r#"[{"x":{"n":[1e400]},"x":{}}]"#,
        ] {
            let body = format!(r#"{{"fields":["result"],"records":[{{"result":{nested}}}]}}"#);
            assert_eq!(
                decode_query_value(body.as_bytes()).unwrap_err(),
                "json: cannot unmarshal number 1e400 into Go struct field QueryData.records of type float64",
                "overwritten nested value: {nested}"
            );
        }
    }

    #[test]
    fn nested_numeric_errors_follow_wire_order_before_map_sorting() {
        for nested in [
            r#"{"z":1e401,"a":1e400}"#,
            r#"{"z":[1e401],"z":null,"a":1e400}"#,
            r#"[1e401,1e400]"#,
        ] {
            let body = format!(r#"{{"records":[{{"result":{nested}}}]}}"#);
            assert_eq!(
                decode_query_value(body.as_bytes()).unwrap_err(),
                "json: cannot unmarshal number 1e401 into Go struct field QueryData.records of type float64",
                "first conversion error: {nested}"
            );
        }
        // A prior struct-field error still wins over nested conversion errors.
        assert_eq!(
            decode_query_value(br#"{"fields":[1],"records":[{"result":{"z":1e401,"a":1e400}}]}"#)
                .unwrap_err(),
            "json: cannot unmarshal number into Go struct field QueryData.fields of type string"
        );
    }

    #[test]
    fn valid_nested_duplicates_keep_last_values_and_sorted_display() {
        let data = decode_query_value(
            br#"{"records":[{"result":{"z":1,"a":[1,2],"z":{"b":true,"a":3}}}]}"#,
        )
        .unwrap();
        assert_eq!(
            data.records[0]["result"],
            Cell::Value("map[a:[1 2] z:map[a:3 b:true]]".into())
        );
    }

    #[test]
    fn deeply_nested_overwritten_numeric_overflow_is_stack_safe() {
        let depth = 9_000;
        let mut body = br#"{"records":[{"result":{"x":"#.to_vec();
        body.extend(std::iter::repeat_n(b'[', depth));
        body.extend_from_slice(b"1e400");
        body.extend(std::iter::repeat_n(b']', depth));
        body.extend_from_slice(br#", "x":null}}]}"#);
        assert_eq!(
            decode_query_value(&body).unwrap_err(),
            "json: cannot unmarshal number 1e400 into Go struct field QueryData.records of type float64"
        );
    }

    #[test]
    fn body_uses_sorted_keys_and_html_escapes() {
        let body = String::from_utf8(query_body("a<b & c>d", "s", "e")).unwrap();
        assert_eq!(
            body,
            // json.Marshal escapes HTML characters (built with an explicit
            // backslash so no tool or editor rewrites the escape).
            format!(
                "{{\"endTime\":\"e\",\"query\":\"a{b}u003cb {b}u0026 c{b}u003ed\",\"startTime\":\"s\"}}",
                b = '\\'
            )
        );
    }

    #[test]
    fn type_errors_use_go_text() {
        assert_eq!(
            decode_query_value(b"[]").unwrap_err(),
            "json: cannot unmarshal array into Go value of type model.QueryData"
        );
        assert_eq!(
            decode_query_value(br#"{"fields":[1]}"#).unwrap_err(),
            "json: cannot unmarshal number into Go struct field QueryData.fields of type string"
        );
        assert_eq!(
            decode_query_value(br#"{"records":"x"}"#).unwrap_err(),
            "json: cannot unmarshal string into Go struct field QueryData.records of type []map[string]interface {}"
        );
        assert_eq!(
            decode_query_value(br#"{"records":[{"a":1e400}]}"#).unwrap_err(),
            "json: cannot unmarshal number 1e400 into Go struct field QueryData.records of type float64"
        );
        assert_eq!(decode_query_value(b"null").unwrap(), QueryData::default());
        assert_eq!(
            decode_query_value(br#"{"FIELDS":["x"]}"#).unwrap().fields,
            vec!["x"]
        );
    }

    #[test]
    fn deep_nesting_within_go_limit_is_stack_safe() {
        let depth = 9_000;
        let mut body = br#"{"records":[{"a":"#.to_vec();
        body.extend(std::iter::repeat_n(b'[', depth));
        body.extend(std::iter::repeat_n(b']', depth));
        body.extend_from_slice(b"}]}");
        let (outcome, _) = first_value(&[&body]);
        assert_eq!(outcome, Ok(body.len()));
        let data = decode_query_value(&body).unwrap();
        assert_eq!(data.records[0]["a"].display().len(), 2 * depth);
    }

    #[test]
    fn nesting_beyond_go_limit_is_a_syntax_error() {
        let body = vec![b'['; MAX_NESTING_DEPTH + 1];
        assert_eq!(
            first_value(&[&body]).0,
            Err("invalid character '[' exceeded max depth".into())
        );
    }

    #[test]
    fn arrow_schema_requires_valid_document_and_nonempty_names() {
        assert_eq!(
            decode_arrow_schema_names(br#"{"fields":[{"name":"a"},{"name":""},{"name":"b"}]}"#),
            Some(vec!["a".into(), "b".into()])
        );
        assert_eq!(decode_arrow_schema_names(br#"{"fields":[]}"#), None);
        assert_eq!(
            decode_arrow_schema_names(br#"{"fields":[{"name":1}]}"#),
            None
        );
        assert_eq!(
            decode_arrow_schema_names(br#"{"fields":[{"name":"a"}]} x"#),
            None
        );
    }

    #[test]
    fn schema_endpoint_escapes_raw_invalid_bytes_like_go() {
        let mut stream = vec![0xb5, 0x8b];
        stream.extend_from_slice("\u{8bd5}".as_bytes());
        assert_eq!(
            schema_endpoint("http://h", &stream).unwrap(),
            "http://h/api/v1/logstream/%B5%8B%E8%AF%95/schema"
        );
        // Valid names are unchanged from the plain joiner.
        assert_eq!(
            schema_endpoint("http://h", b"my-logs").unwrap(),
            crate::api::append_url_string("http://h", &["api/v1/logstream", "my-logs", "schema"])
                .unwrap()
        );
        // Pinned Go net/url oracle: literal private-use characters cannot be
        // placeholders, punctuation follows EscapedPath, and JoinPath still
        // cleans dot segments and retains the original URL on a bad escape.
        let mut private_use = vec![0xb5];
        private_use.extend_from_slice("\u{1000b5}!".as_bytes());
        for (bytes, expected) in [
            (
                private_use,
                "http://h/prefix/api/v1/logstream/%B5%F4%80%82%B5%21/schema?x=1#f",
            ),
            (
                b"\xff/../a%2Fb?".to_vec(),
                "http://h/prefix/api/v1/logstream/a/b%3F/schema?x=1#f",
            ),
            (b"\xff%zz".to_vec(), "http://h/prefix?x=1#f"),
        ] {
            assert_eq!(
                schema_endpoint("http://h/prefix?x=1#f", &bytes).unwrap(),
                expected
            );
        }
    }

    fn record(pairs: &[(&str, &str)]) -> Record {
        pairs
            .iter()
            .map(|(key, value)| (key.to_string(), Cell::Str(value.to_string())))
            .collect()
    }

    // The expected values below come from the pinned go1.25.4 encoding/json
    // (decode.go `array`/`object`/`literalStore`) and model.QueryData; the
    // actual-Go probe is decoder-review-oracle/probe.go (docs/phase7-decoder-review-fixes.md).

    #[test]
    fn duplicate_records_merge_into_existing_maps() {
        // Review F1 trigger 1.
        let data = decode_query_value(
            br#"{"fields":["a","b"],"records":[{"a":"1"}],"records":[{"b":"2"}]}"#,
        )
        .unwrap();
        assert_eq!(data.records, [record(&[("a", "1"), ("b", "2")])]);
        // A later key replaces the old cell with a fresh interface{} value.
        let data =
            decode_query_value(br#"{"records":[{"a":{"x":1},"n":"s"}],"records":[{"a":[2]}]}"#)
                .unwrap();
        assert_eq!(data.records[0]["a"], Cell::Value("[2]".into()));
        assert_eq!(data.records[0]["n"], Cell::Str("s".into()));
    }

    #[test]
    fn duplicate_fields_keep_prior_strings_under_null() {
        // Review F1 trigger 2.
        let data = decode_query_value(br#"{"fields":["a","b"],"fields":[null,"x"],"records":[]}"#)
            .unwrap();
        assert_eq!(data.fields, ["a", "x"]);
        assert!(data.records.is_empty());
    }

    #[test]
    fn shorter_window_arrays_hide_slots_that_a_longer_one_reexposes() {
        let data = decode_query_value(
            br#"{"fields":["a","b","c"],"fields":["x"],"fields":[null,null,null,null],
            "records":[{"a":"1"},{"b":"2"},{"c":"3"}],"records":[{"d":"4"}],
            "records":[{},{"e":"5"},null]}"#,
        )
        .unwrap();
        assert_eq!(data.fields, ["x", "b", "c", ""]);
        assert_eq!(
            data.records,
            [
                record(&[("a", "1"), ("d", "4")]),
                record(&[("b", "2"), ("e", "5")]),
                Record::new()
            ]
        );
    }

    #[test]
    fn empty_and_null_window_arrays_discard_hidden_slots() {
        for reset in ["[]", "null"] {
            let body = format!(
                r#"{{"fields":["a","b"],"fields":{reset},"fields":[null,null],
                "records":[{{"a":"1"}},{{"b":"2"}}],"records":{reset},"records":[{{}},null]}}"#
            );
            let data = decode_query_value(body.as_bytes()).unwrap();
            assert_eq!(data.fields, ["", ""]);
            assert_eq!(data.records, [Record::new(), Record::new()]);
        }
        // A trailing `[]` is still an empty (not absent) result.
        let data = decode_query_value(
            br#"{"fields":["a"],"fields":[],"records":[{"a":"1"}],"records":[]}"#,
        )
        .unwrap();
        assert_eq!(data, QueryData::default());
        // null into an existing record sets the map to nil.
        let data = decode_query_value(
            br#"{"records":[{"a":"1"}],"records":[null],"records":[{"b":"2"}]}"#,
        )
        .unwrap();
        assert_eq!(data.records, [record(&[("b", "2")])]);
    }

    #[test]
    fn duplicate_window_keys_do_not_hide_the_first_go_error() {
        assert_eq!(
            decode_query_value(br#"{"fields":[1],"fields":["ok"]}"#).unwrap_err(),
            "json: cannot unmarshal number into Go struct field QueryData.fields of type string"
        );
        assert_eq!(
            decode_query_value(br#"{"records":["x"],"records":[{"a":"1"}]}"#).unwrap_err(),
            "json: cannot unmarshal string into Go struct field QueryData.records of type map[string]interface {}"
        );
        assert_eq!(
            decode_query_value(br#"{"records":[{"a":1e400}],"records":null}"#).unwrap_err(),
            "json: cannot unmarshal number 1e400 into Go struct field QueryData.records of type float64"
        );
    }

    #[test]
    fn window_field_names_fold_like_go_simple_fold() {
        // Review F3: U+017F folds with s; U+0131 (dotless i) folds only with
        // itself; ß is not expanded (SimpleFold is one rune to one rune).
        let data = decode_query_value(
            "{\"FIELD\u{17F}\":[\"x\"],\"record\u{17F}\":[{\"a\":\"1\"}]}".as_bytes(),
        )
        .unwrap();
        assert_eq!(data.fields, ["x"]);
        assert_eq!(data.records, [record(&[("a", "1")])]);
        for ignored in ["f\u{131}elds", "field\u{df}", "fields\u{17F}"] {
            let body = format!("{{\"{ignored}\":[\"x\"]}}");
            assert_eq!(
                decode_query_value(body.as_bytes()).unwrap(),
                QueryData::default()
            );
        }
    }

    #[test]
    fn arrow_schema_duplicate_fields_reuse_struct_slots() {
        // Review F1 trigger 3: Go keeps `a` and sends no fallback query.
        assert_eq!(
            decode_arrow_schema_names(br#"{"fields":[{"name":"a"}],"fields":[{}]}"#),
            Some(vec!["a".into()])
        );
        assert_eq!(
            decode_arrow_schema_names(
                br#"{"fields":[{"name":"a"},{"name":"b"}],"fields":[{"name":"c"}],"fields":[null,{"name":null}]}"#
            ),
            Some(vec!["c".into(), "b".into()])
        );
        for reset in ["[]", "null"] {
            let body = format!(r#"{{"fields":[{{"name":"a"}}],"fields":{reset},"fields":[{{}}]}}"#);
            assert_eq!(decode_arrow_schema_names(body.as_bytes()), None);
        }
        // A type error in an earlier duplicate still fails Unmarshal.
        assert_eq!(
            decode_arrow_schema_names(br#"{"fields":[{"name":1}],"fields":[{"name":"a"}]}"#),
            None
        );
        // Review F3 for the schema struct.
        assert_eq!(
            decode_arrow_schema_names("{\"Field\u{17F}\":[{\"NAME\":\"a\"}]}".as_bytes()),
            Some(vec!["a".into()])
        );
    }
}
