//! PromQL response rendering. JSON output follows cmd.writeRawJSON; text
//! output decodes the command-specific Go structs with encoding/json's
//! tolerance (case-insensitive fields, null retention, slice element reuse)
//! and reproduces cmd/promql.go's formatting byte for byte, including
//! active-query truncation that can split UTF-8 sequences.
use crate::{
    gojson::{self, Node},
    parser::quote,
};
use serde_json::Value;
use std::collections::BTreeMap;

#[path = "style.rs"]
pub(crate) mod style;
use style::{ITEM_OUTER, RULE, SELECTED, STANDARD, Terminal};

#[path = "text_stream.rs"]
mod text_stream;

/// Render a successful (2xx, non-error-envelope) PromQL body for `operation`,
/// the command path after `pb promql `. `label` is the label-values label
/// (first positional) or the tsdb --focus-label value; other operations ignore it.
/// Every error is a decode failure (Go newInvalidResponseError).
/// Text is rendered as for a non-terminal stdout; see `render_styled`.
#[cfg(test)]
pub(super) fn render(
    operation: &str,
    body: &[u8],
    json: bool,
    label: &str,
) -> Result<Vec<u8>, String> {
    render_styled(operation, body, json, label, &Terminal::PLAIN)
}

/// `render` with cmd/style.go's lipgloss styles for `terminal` (Go detects
/// it from os.Stdout and the environment; see `Terminal::detect`).
pub(super) fn render_styled(
    operation: &str,
    body: &[u8],
    json: bool,
    label: &str,
    t: &Terminal,
) -> Result<Vec<u8>, String> {
    if json {
        return raw_json(body).map(String::into_bytes);
    }
    match operation {
        "run" => text_stream::run(body).map_or_else(|| run(body), Ok),
        "labels" => names(body, "labels", "No labels found.\n", None, t),
        "label-values" => names(
            body,
            "label-values",
            "No label values found.\n",
            Some(label),
            t,
        ),
        "series" => text_stream::series(body, t).map_or_else(|| series(body, t), Ok),
        "cardinality label-names" => cardinality(body, "LABEL", "DISTINCT VALUES", t),
        "cardinality label-values" => cardinality(body, "VALUE", "SERIES COUNT", t),
        "cardinality active-series" => active_series(body, t),
        "active-queries" | "ps" => active_queries(body),
        "tsdb" => tsdb(body, label, t),
        other => Err(format!("unsupported PromQL operation {}", quote(other))),
    }
}

// ---------------------------------------------------------------------------
// Raw JSON (json.Decoder with UseNumber, single value, sorted re-encoding)
// ---------------------------------------------------------------------------

fn raw_json(body: &[u8]) -> Result<String, String> {
    let invalid = |e: String| format!("server returned invalid JSON: {}", stream_error(e));
    match gojson::validate(body) {
        Ok(()) => (),
        Err(error) if error.ends_with(" after top-level value") => {
            // The first value is complete; Decoder.Decode then reads a second
            // value from the remainder instead of rejecting the trailing byte.
            let end = value_end(body);
            return Err(match gojson::validate(&body[end..]) {
                Ok(_) => "server returned multiple JSON values".into(),
                Err(error) if error.ends_with(" after top-level value") => {
                    "server returned multiple JSON values".into()
                }
                Err(error) => invalid(error),
            });
        }
        Err(_) if body.iter().all(|b| is_space(*b)) => {
            return Err("server returned invalid JSON: EOF".into());
        }
        Err(error) => return Err(invalid(error)),
    };
    Ok(super::raw_json::render_validated(body))
}

/// json.Decoder reports truncated values as io.ErrUnexpectedEOF.
fn stream_error(error: String) -> String {
    if error == "unexpected end of JSON input" {
        "unexpected EOF".into()
    } else {
        error
    }
}

fn is_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\r' | b'\n')
}

/// End offset of the first top-level value in a body whose first value is
/// already known to be syntactically valid. Numbers end where Go's scanner
/// stops accepting the number grammar, so `0123` is the value `0` then `123`.
fn value_end(body: &[u8]) -> usize {
    let mut index = 0;
    while index < body.len() && is_space(body[index]) {
        index += 1;
    }
    let digits = |mut index: usize| {
        while body.get(index).is_some_and(u8::is_ascii_digit) {
            index += 1;
        }
        index
    };
    match body[index] {
        b'{' | b'[' => {
            let (mut depth, mut string, mut escape) = (0usize, false, false);
            while index < body.len() {
                let byte = body[index];
                index += 1;
                if string {
                    if escape {
                        escape = false;
                    } else if byte == b'\\' {
                        escape = true;
                    } else if byte == b'"' {
                        string = false;
                    }
                    continue;
                }
                match byte {
                    b'"' => string = true,
                    b'{' | b'[' => depth += 1,
                    b'}' | b']' => {
                        depth -= 1;
                        if depth == 0 {
                            return index;
                        }
                    }
                    _ => (),
                }
            }
            index
        }
        b'"' => {
            index += 1;
            let mut escape = false;
            while index < body.len() {
                let byte = body[index];
                index += 1;
                if escape {
                    escape = false;
                } else if byte == b'\\' {
                    escape = true;
                } else if byte == b'"' {
                    return index;
                }
            }
            index
        }
        b't' | b'n' => index + 4,
        b'f' => index + 5,
        _ => {
            if body[index] == b'-' {
                index += 1;
            }
            index = if body.get(index) == Some(&b'0') {
                index + 1
            } else {
                digits(index)
            };
            if body.get(index) == Some(&b'.') {
                index = digits(index + 1);
            }
            if body.get(index).is_some_and(|b| matches!(b, b'e' | b'E')) {
                index += 1;
                if body.get(index).is_some_and(|b| matches!(b, b'+' | b'-')) {
                    index += 1;
                }
                index = digits(index);
            }
            index
        }
    }
}

// ---------------------------------------------------------------------------
// encoding/json typed decoding helpers
// ---------------------------------------------------------------------------

const LABELS_TYPE: &str = "struct { Status string \"json:\\\"status\\\"\"; Data []string \"json:\\\"data\\\"\"; Error string \"json:\\\"error,omitempty\\\"\" }";
const SERIES_TYPE: &str = "struct { Status string \"json:\\\"status\\\"\"; Data []map[string]string \"json:\\\"data\\\"\"; Error string \"json:\\\"error,omitempty\\\"\" }";
const CARDINALITY_TYPE: &str = "struct { Status string \"json:\\\"status\\\"\"; Data []cmd.cardinalityEntry \"json:\\\"data\\\"\"; Error string \"json:\\\"error,omitempty\\\"\" }";
const ACTIVE_SERIES_DATA_TYPE: &str = "struct { TotalActiveSeries int \"json:\\\"total_active_series\\\"\"; Series []map[string]string \"json:\\\"series\\\"\" }";
const ACTIVE_QUERY_TYPE: &str = "struct { Query string \"json:\\\"query\\\"\"; Stream string \"json:\\\"stream\\\"\"; StartedAt string \"json:\\\"started_at\\\"\"; ElapsedMs int \"json:\\\"elapsed_ms\\\"\" }";
const TSDB_ENVELOPE_TYPE: &str = "struct { Status string \"json:\\\"status\\\"\"; Data json.RawMessage \"json:\\\"data\\\"\"; Error string \"json:\\\"error,omitempty\\\"\" }";
const TSDB_DATA_TYPE: &str = "struct { TotalSeries int \"json:\\\"totalSeries\\\"\"; TotalLabelValuePairs int \"json:\\\"totalLabelValuePairs\\\"\"; SeriesByMetric []cmd.cardinalityEntry \"json:\\\"seriesCountByMetricName\\\"\"; SeriesByLabel []cmd.cardinalityEntry \"json:\\\"seriesCountByLabelName\\\"\"; SeriesByFocusLabel []cmd.cardinalityEntry \"json:\\\"seriesCountByFocusLabelValue\\\"\"; LabelValueCount []cmd.cardinalityEntry \"json:\\\"labelValueCountByLabelName\\\"\" }";

fn active_series_type() -> String {
    format!(
        "struct {{ Status string \"json:\\\"status\\\"\"; Data {ACTIVE_SERIES_DATA_TYPE} \"json:\\\"data\\\"\"; Error string \"json:\\\"error,omitempty\\\"\" }}"
    )
}
fn active_queries_type() -> String {
    format!(
        "struct {{ Status string \"json:\\\"status\\\"\"; Data []{ACTIVE_QUERY_TYPE} \"json:\\\"data\\\"\"; Error string \"json:\\\"error,omitempty\\\"\" }}"
    )
}

/// encoding/json field lookup falls back to Unicode simple case folding.
fn key_is(key: &str, expected: &str) -> bool {
    let fold = |text: &str| -> String {
        text.chars()
            .map(|c| match c {
                '\u{17f}' => 's',
                '\u{212a}' => 'k',
                _ => c.to_ascii_lowercase(),
            })
            .collect()
    };
    fold(key) == fold(expected)
}

/// UnmarshalTypeError text. `numeric` includes the literal as Go does for
/// numeric destinations (int, float64).
fn type_error(value: &Node, target: &str, structure: &str, path: &str, numeric: bool) -> String {
    let actual = if numeric && value.is_number() {
        format!("number {value}")
    } else {
        value.kind().into()
    };
    if path.is_empty() {
        format!("json: cannot unmarshal {actual} into Go value of type {target}")
    } else {
        format!(
            "json: cannot unmarshal {actual} into Go struct field {structure}.{path} of type {target}"
        )
    }
}

fn top_object<'a>(value: &'a Node, target: &str) -> Result<&'a [(String, Node)], String> {
    match value {
        Node::Object(fields) => Ok(fields),
        Node::Scalar(Value::Null) => Ok(&[]),
        _ => Err(type_error(value, target, "", "", false)),
    }
}

/// Struct-valued field: null leaves the destination untouched.
fn struct_fields<'a>(
    value: &'a Node,
    target: &str,
    structure: &str,
    path: &str,
) -> Result<&'a [(String, Node)], String> {
    match value {
        Node::Object(fields) => Ok(fields),
        Node::Scalar(Value::Null) => Ok(&[]),
        _ => Err(type_error(value, target, structure, path, false)),
    }
}

fn decode_string(
    value: &Node,
    out: &mut String,
    structure: &str,
    path: &str,
) -> Result<(), String> {
    match value {
        Node::Scalar(Value::Null) => Ok(()),
        Node::Scalar(Value::String(text)) => {
            out.clone_from(text);
            Ok(())
        }
        _ => Err(type_error(value, "string", structure, path, false)),
    }
}

fn decode_int(value: &Node, out: &mut i64, structure: &str, path: &str) -> Result<(), String> {
    match value {
        Node::Scalar(Value::Null) => Ok(()),
        Node::Scalar(Value::Number(number)) => {
            *out = number
                .to_string()
                .parse()
                .map_err(|_| type_error(value, "int", structure, path, true))?;
            Ok(())
        }
        _ => Err(type_error(value, "int", structure, path, true)),
    }
}

type StringMap = Option<BTreeMap<String, String>>;

/// map[string]string: objects merge into an existing map, null resets it,
/// and null member values store "".
fn decode_map(
    value: &Node,
    out: &mut StringMap,
    structure: &str,
    path: &str,
) -> Result<(), String> {
    match value {
        Node::Scalar(Value::Null) => {
            *out = None;
            Ok(())
        }
        Node::Object(fields) => {
            let map = out.get_or_insert_with(BTreeMap::new);
            for (key, value) in fields {
                let mut text = String::new();
                decode_string(value, &mut text, structure, path)?;
                map.insert(key.clone(), text);
            }
            Ok(())
        }
        _ => Err(type_error(
            value,
            "map[string]string",
            structure,
            path,
            false,
        )),
    }
}

/// Typed slice decoding. Repeated arrays decode into existing elements, a
/// shorter array keeps later elements in the backing array for reuse, and
/// empty arrays or null discard the backing array.
fn decode_slice<T: Default>(
    value: &Node,
    storage: &mut Vec<T>,
    length: &mut usize,
    target: &str,
    structure: &str,
    path: &str,
    mut element: impl FnMut(&Node, &mut T) -> Result<(), String>,
) -> Result<(), String> {
    match value {
        Node::Scalar(Value::Null) => {
            storage.clear();
            *length = 0;
            Ok(())
        }
        Node::Array(items) => {
            if items.is_empty() {
                storage.clear();
            }
            for (index, item) in items.iter().enumerate() {
                if index == storage.len() {
                    storage.push(T::default());
                }
                element(item, &mut storage[index])?;
            }
            *length = items.len();
            Ok(())
        }
        _ => Err(type_error(value, target, structure, path, false)),
    }
}

/// A decoded interface{} value (json.Unmarshal without UseNumber).
#[derive(Debug, Clone, PartialEq)]
enum GoAny {
    Nil,
    Bool(bool),
    Float(f64),
    Text(String),
    List(Vec<GoAny>),
    Map(BTreeMap<String, GoAny>),
}

fn decode_any(value: &Node, structure: &str, path: &str) -> Result<GoAny, String> {
    stacker::maybe_grow(64 * 1024, 1024 * 1024, || match value {
        Node::Scalar(Value::Null) => Ok(GoAny::Nil),
        Node::Scalar(Value::Bool(flag)) => Ok(GoAny::Bool(*flag)),
        Node::Scalar(Value::String(text)) => Ok(GoAny::Text(text.clone())),
        Node::Scalar(Value::Number(number)) => number
            .to_string()
            .parse::<f64>()
            .ok()
            .filter(|f| f.is_finite())
            .map(GoAny::Float)
            .ok_or_else(|| type_error(value, "float64", structure, path, true)),
        Node::Array(items) => items
            .iter()
            .map(|item| decode_any(item, structure, path))
            .collect::<Result<_, _>>()
            .map(GoAny::List),
        Node::Object(fields) => {
            let mut map = BTreeMap::new();
            for (key, value) in fields {
                map.insert(key.clone(), decode_any(value, structure, path)?);
            }
            Ok(GoAny::Map(map))
        }
        Node::Scalar(_) => Ok(GoAny::Nil),
    })
}

/// []interface{}: null resets, arrays replace every element.
fn decode_any_list(
    value: &Node,
    target: &str,
    structure: &str,
    path: &str,
) -> Result<Vec<GoAny>, String> {
    match value {
        Node::Scalar(Value::Null) => Ok(Vec::new()),
        Node::Array(items) => items
            .iter()
            .map(|item| decode_any(item, structure, path))
            .collect(),
        _ => Err(type_error(value, target, structure, path, false)),
    }
}

impl std::fmt::Display for GoAny {
    /// fmt's %v.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Nil => f.write_str("<nil>"),
            Self::Bool(flag) => write!(f, "{flag}"),
            Self::Float(value) => f.write_str(&go_float(*value)),
            Self::Text(text) => f.write_str(text),
            Self::List(items) => {
                f.write_str("[")?;
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        f.write_str(" ")?;
                    }
                    write!(f, "{item}")?;
                }
                f.write_str("]")
            }
            Self::Map(map) => {
                f.write_str("map[")?;
                for (index, (key, item)) in map.iter().enumerate() {
                    if index > 0 {
                        f.write_str(" ")?;
                    }
                    write!(f, "{key}:{item}")?;
                }
                f.write_str("]")
            }
        }
    }
}

/// %v for float64: strconv 'g' with shortest digits, which switches to %e
/// when the decimal exponent is below -4 or at least 6.
fn go_float(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0".into()
        } else {
            "0".into()
        };
    }
    let absolute = value.abs();
    if (1e-4..1e6).contains(&absolute) {
        return value.to_string();
    }
    let representation = format!("{value:e}");
    let (mantissa, exponent) = representation.split_once('e').expect("scientific exponent");
    let exponent: i32 = exponent.parse().expect("integer exponent");
    format!(
        "{mantissa}e{}{:02}",
        if exponent < 0 { '-' } else { '+' },
        exponent.abs()
    )
}

// ---------------------------------------------------------------------------
// Terminal presentation helpers (lipgloss measurement and padding)
// ---------------------------------------------------------------------------

/// ansi.StringWidth: escape sequences and C0 controls are zero width.
pub(crate) fn line_width(text: &str) -> usize {
    line_width_bytes(text.as_bytes())
}

pub(crate) fn line_width_bytes(bytes: &[u8]) -> usize {
    #[derive(Clone, Copy)]
    enum State {
        Ground,
        Escape,
        EscapeIntermediate,
        CsiEntry,
        CsiParam,
        CsiIntermediate,
        DcsEntry,
        DcsParam,
        DcsIntermediate,
        DcsString,
        Osc,
        Text,
    }
    let (mut state, mut width, mut index) = (State::Ground, 0, 0);
    while index < bytes.len() {
        let byte = bytes[index];
        // Keep the ordinary printable ASCII path independent of the escape
        // transitions. The rules below follow x/ansi v0.4.5's pinned table.
        if matches!(state, State::Ground) && (0x20..=0x7e).contains(&byte) {
            width += 1;
            index += 1;
            continue;
        }
        if matches!(state, State::Osc | State::DcsString) {
            // String data has no width and overrides all other byte rules.
            // Scan it together, including UTF-8, until a table terminator.
            let terminator = if matches!(state, State::Osc) {
                bytes[index..]
                    .iter()
                    .position(|b| matches!(b, 0x07 | 0x18 | 0x1a | 0x1b | 0x9c))
            } else {
                bytes[index..]
                    .iter()
                    .position(|b| matches!(b, 0x18 | 0x1a | 0x1b | 0x9c))
            };
            let Some(offset) = terminator else {
                break;
            };
            index += offset;
            state = if bytes[index] == 0x1b {
                State::Escape
            } else {
                State::Ground
            };
            index += 1;
            continue;
        }
        if (0xc2..=0xf4).contains(&byte) {
            let start = index;
            while index < bytes.len() && bytes[index] >= 0x80 {
                index += 1;
            }
            // Measure the run together so emoji sequences count once. UTF-8
            // starts a printable grapheme in every state except OSC/DCS data,
            // and StringWidth then returns to ground.
            let run: String = String::from_utf8_lossy(&bytes[start..index])
                .chars()
                .filter(|c| !('\u{80}'..='\u{9f}').contains(c))
                .collect();
            width += unicode_width::UnicodeWidthStr::width(run.as_str());
            state = State::Ground;
            continue;
        } else if matches!(byte, 0x18 | 0x1a | 0x9c) {
            state = State::Ground;
        } else if byte == 0x1b {
            // DCS entry uniquely treats ESC as passthrough data.
            state = if matches!(state, State::DcsEntry) {
                State::DcsString
            } else {
                State::Escape
            };
        } else if byte >= 0x80 {
            // A string's raw ST byte can terminate it inside UTF-8. Remaining
            // continuation bytes use table transitions, not replacement runes.
            state = match byte {
                0x90 => State::DcsEntry,
                0x98 | 0x9e | 0x9f => State::Text,
                0x9b => State::CsiEntry,
                0x9d => State::Osc,
                _ => State::Ground,
            };
        } else {
            state = match state {
                State::Ground => State::Ground,
                State::Escape => match byte {
                    b'[' => State::CsiEntry,
                    b']' => State::Osc,
                    b'P' => State::DcsEntry,
                    b'X' | b'^' | b'_' => State::Text,
                    0x20..=0x2f => State::EscapeIntermediate,
                    0x00..=0x1f | 0x7f => State::Escape,
                    _ => State::Ground,
                },
                State::EscapeIntermediate => match byte {
                    0x00..=0x2f | 0x7f => State::EscapeIntermediate,
                    _ => State::Ground,
                },
                State::CsiEntry | State::CsiParam => match byte {
                    0x00..=0x1f | 0x7f => state,
                    0x20..=0x2f => State::CsiIntermediate,
                    0x30..=0x3f => State::CsiParam,
                    _ => State::Ground,
                },
                State::CsiIntermediate => match byte {
                    0x00..=0x2f | 0x7f => State::CsiIntermediate,
                    _ => State::Ground,
                },
                State::DcsEntry => match byte {
                    0x08..=0x0d | 0x40..=0x7e => State::DcsString,
                    0x00..=0x1f | 0x7f => State::DcsEntry,
                    0x20..=0x2f => State::DcsIntermediate,
                    0x30..=0x3f => State::DcsParam,
                    _ => State::Ground,
                },
                State::DcsParam => match byte {
                    0x00..=0x1f | 0x30..=0x3f | 0x7f => State::DcsParam,
                    0x20..=0x2f => State::DcsIntermediate,
                    0x40..=0x7e => State::DcsString,
                    _ => State::Ground,
                },
                State::DcsIntermediate => match byte {
                    0x00..=0x2f | 0x7f => State::DcsIntermediate,
                    0x30..=0x7e => State::DcsString,
                    _ => State::Ground,
                },
                State::Osc if byte == 0x07 => State::Ground,
                State::Osc | State::DcsString | State::Text => state,
            };
        }
        index += 1;
    }
    width
}

/// lipgloss.Width: the widest newline-separated line.
pub(crate) fn width(text: &str) -> usize {
    text.split('\n').map(line_width).max().unwrap_or(0)
}

/// ItemOuter.Render(SelectedStyle.Render("•") + " " + StandardStyle.Render(text)).
fn bullet(out: &mut Vec<u8>, text: &str, t: &Terminal) {
    let item = format!(
        "{} {}",
        SELECTED.render("•", t, line_width),
        STANDARD.render(text, t, line_width)
    );
    out.extend_from_slice(ITEM_OUTER.render(&item, t, line_width).as_bytes());
    out.push(b'\n');
}

fn pad_right(text: &str, column: usize) -> String {
    format!("{text}{}", " ".repeat(column.saturating_sub(width(text))))
}

/// cmd.truncateCell measured with lipgloss.Width.
fn truncate_cell(text: &str, limit: usize) -> String {
    if width(text) <= limit {
        return text.into();
    }
    if limit <= 1 {
        return "…".into();
    }
    let mut out = String::new();
    let mut previous_lines = 0;
    let mut line_start = 0;
    for c in text.chars() {
        let mut next_line = &out[line_start..];
        let joined;
        if c == '\n' {
            previous_lines = previous_lines.max(line_width(next_line));
            joined = String::new();
            next_line = &joined;
        } else {
            joined = format!("{next_line}{c}");
            next_line = &joined;
        }
        if previous_lines.max(line_width(next_line)) + 1 > limit {
            break;
        }
        out.push(c);
        if c == '\n' {
            line_start = out.len();
        }
    }
    out + "…"
}

/// Go's %-Ns pads by rune count; invalid UTF-8 bytes count one rune each.
fn pad_runes(out: &mut Vec<u8>, bytes: &[u8], column: usize) {
    out.extend_from_slice(bytes);
    let mut runes = 0;
    let mut rest = bytes;
    while !rest.is_empty() {
        match std::str::from_utf8(rest) {
            Ok(valid) => {
                runes += valid.chars().count();
                break;
            }
            Err(error) => {
                let valid = error.valid_up_to();
                runes += std::str::from_utf8(&rest[..valid])
                    .expect("valid prefix")
                    .chars()
                    .count()
                    + 1;
                rest = &rest[valid + 1..];
            }
        }
    }
    out.extend(std::iter::repeat_n(b' ', column.saturating_sub(runes)));
}

// ---------------------------------------------------------------------------
// run
// ---------------------------------------------------------------------------

#[derive(Default)]
struct Series {
    metric: StringMap,
    value: Vec<GoAny>,
    values: Vec<Vec<GoAny>>,
}

fn run(body: &[u8]) -> Result<Vec<u8>, String> {
    let context = |e: String| format!("failed to decode PromQL response: {e}");
    let document = gojson::decode_ordered(body).map_err(context)?;
    let (mut status, mut error, mut error_type, mut result_type) =
        (String::new(), String::new(), String::new(), String::new());
    let (mut result, mut length) = (Vec::<Series>::new(), 0);
    (|| -> Result<(), String> {
        for (key, value) in top_object(&document, "cmd.promqlResponse")? {
            if key_is(key, "status") {
                decode_string(value, &mut status, "promqlResponse", "status")?;
            } else if key_is(key, "error") {
                decode_string(value, &mut error, "promqlResponse", "error")?;
            } else if key_is(key, "errorType") {
                decode_string(value, &mut error_type, "promqlResponse", "errorType")?;
            } else if key_is(key, "data") {
                for (key, value) in
                    struct_fields(value, "cmd.promqlData", "promqlResponse", "data")?
                {
                    if key_is(key, "resultType") {
                        decode_string(value, &mut result_type, "promqlData", "data.resultType")?;
                    } else if key_is(key, "result") {
                        decode_slice(
                            value,
                            &mut result,
                            &mut length,
                            "[]cmd.promqlSeries",
                            "promqlData",
                            "data.result",
                            series_fields,
                        )?;
                    }
                }
            }
        }
        Ok(())
    })()
    .map_err(context)?;
    drop(document);
    result.truncate(length);
    if status == "error" {
        // Unreachable after the request-layer envelope check; kept for parity.
        return Err(format!("query error ({error_type}): {error}"));
    }
    let mut out = Vec::new();
    if result.is_empty() {
        out.extend_from_slice(b"No data returned.\n");
        return Ok(out);
    }
    for series in &result {
        out.extend_from_slice(format_labels(series.metric.as_ref()).as_bytes());
        out.push(b'\n');
        match result_type.as_str() {
            "vector" => {
                if series.value.len() == 2 {
                    sample(&mut out, &series.value);
                }
            }
            "matrix" => {
                for point in &series.values {
                    if point.len() == 2 {
                        sample(&mut out, point);
                    }
                }
            }
            _ => (),
        }
        out.push(b'\n');
    }
    out.extend_from_slice(
        format!("result_type={result_type}  series={}\n", result.len()).as_bytes(),
    );
    Ok(out)
}

fn series_fields(value: &Node, series: &mut Series) -> Result<(), String> {
    for (key, value) in struct_fields(value, "cmd.promqlSeries", "promqlData", "data.result")? {
        if key_is(key, "metric") {
            decode_map(
                value,
                &mut series.metric,
                "promqlSeries",
                "data.result.metric",
            )?;
        } else if key_is(key, "value") {
            series.value =
                decode_any_list(value, "[]interface {}", "promqlSeries", "data.result.value")?;
        } else if key_is(key, "values") {
            series.values = match value {
                Node::Scalar(Value::Null) => Vec::new(),
                Node::Array(points) => points
                    .iter()
                    .map(|point| {
                        decode_any_list(
                            point,
                            "[]interface {}",
                            "promqlSeries",
                            "data.result.values",
                        )
                    })
                    .collect::<Result<_, _>>()?,
                _ => {
                    return Err(type_error(
                        value,
                        "[][]interface {}",
                        "promqlSeries",
                        "data.result.values",
                        false,
                    ));
                }
            };
        }
    }
    Ok(())
}

fn sample(out: &mut Vec<u8>, point: &[GoAny]) {
    let timestamp = match &point[0] {
        GoAny::Float(value) => unix_utc(go_f64_to_i64(*value)),
        other => other.to_string(),
    };
    out.extend_from_slice(format!("  {timestamp}  {}\n", point[1]).as_bytes());
}

/// cmd.formatPromqlLabels: sorts complete key="value" strings.
fn format_labels(metric: Option<&BTreeMap<String, String>>) -> String {
    let Some(metric) = metric else {
        return String::new();
    };
    let name = metric.get("__name__").map_or("", String::as_str);
    let mut labels: Vec<String> = metric
        .iter()
        .filter(|(key, _)| *key != "__name__")
        .map(|(key, value)| format!("{key}=\"{value}\""))
        .collect();
    labels.sort();
    if labels.is_empty() {
        name.into()
    } else if name.is_empty() {
        format!("{{{}}}", labels.join(", "))
    } else {
        format!("{name}{{{}}}", labels.join(", "))
    }
}

/// Go's int64(f) on amd64: truncation, with out-of-range values becoming MinInt64.
fn go_f64_to_i64(value: f64) -> i64 {
    if (-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0).contains(&value) {
        value as i64
    } else {
        i64::MIN
    }
}

/// time.Unix(sec, 0).UTC().Format("2006-01-02T15:04:05Z") using Go 1.25's
/// absolute-epoch calendar arithmetic, including its wrapping at the extremes.
fn unix_utc(seconds: i64) -> String {
    const ABSOLUTE_YEARS: i64 = 292_277_022_400;
    const INTERNAL_TO_ABSOLUTE: i64 = (ABSOLUTE_YEARS * 365 + ABSOLUTE_YEARS / 4
        - ABSOLUTE_YEARS / 100
        + ABSOLUTE_YEARS / 400
        + 306)
        * 86_400;
    const UNIX_TO_INTERNAL: i64 = (1969 * 365 + 1969 / 4 - 1969 / 100 + 1969 / 400) * 86_400;
    let absolute = seconds.wrapping_add(UNIX_TO_INTERNAL + INTERNAL_TO_ABSOLUTE) as u64;
    let days = absolute / 86_400;
    let d = 4 * days + 3;
    let century = d / 146_097;
    let cd = (d % 146_097) as u32 | 3;
    let product = 2_939_745u64 * u64::from(cd);
    let cyear = (product >> 32) as i64;
    let ayday = (product as u32) / 2_939_745 / 4;
    let month_day = 2141 * ayday + 197_913;
    let amonth = i64::from(month_day >> 16);
    let day = 1 + (month_day & 0xffff) / 2141;
    let jan_feb = i64::from(ayday >= 306);
    let year = (century
        .wrapping_mul(100)
        .wrapping_sub(ABSOLUTE_YEARS as u64) as i64)
        .wrapping_add(cyear)
        .wrapping_add(jan_feb);
    let month = amonth - jan_feb * 12;
    let clock = absolute % 86_400;
    let mut text = String::new();
    if year < 0 {
        text.push('-');
    }
    text.push_str(&format!("{:04}", year.unsigned_abs()));
    text.push_str(&format!(
        "-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        clock / 3600,
        clock % 3600 / 60,
        clock % 60
    ));
    text
}

// ---------------------------------------------------------------------------
// labels / label-values
// ---------------------------------------------------------------------------

fn names(
    body: &[u8],
    kind: &str,
    empty: &str,
    label: Option<&str>,
    t: &Terminal,
) -> Result<Vec<u8>, String> {
    let context = |e: String| format!("failed to decode PromQL {kind} response: {e}");
    let document = gojson::decode_ordered(body).map_err(context)?;
    let (mut status, mut error) = (String::new(), String::new());
    let (mut data, mut length) = (Vec::<String>::new(), 0);
    (|| -> Result<(), String> {
        for (key, value) in top_object(&document, LABELS_TYPE)? {
            if key_is(key, "status") {
                decode_string(value, &mut status, "", "status")?;
            } else if key_is(key, "error") {
                decode_string(value, &mut error, "", "error")?;
            } else if key_is(key, "data") {
                decode_slice(
                    value,
                    &mut data,
                    &mut length,
                    "[]string",
                    "",
                    "data",
                    |value, item| decode_string(value, item, "", "data"),
                )?;
            }
        }
        Ok(())
    })()
    .map_err(context)?;
    data.truncate(length);
    if status == "error" {
        return Err(error);
    }
    let mut out = Vec::new();
    if data.is_empty() {
        out.extend_from_slice(empty.as_bytes());
        return Ok(out);
    }
    for item in &data {
        bullet(&mut out, item, t);
    }
    if let Some(label) = label {
        out.extend_from_slice(format!("\nlabel={label}  total={}\n", data.len()).as_bytes());
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// series / active-series
// ---------------------------------------------------------------------------

fn print_series(out: &mut Vec<u8>, series: Option<&BTreeMap<String, String>>, t: &Terminal) {
    let empty = BTreeMap::new();
    let series = series.unwrap_or(&empty);
    let name = series.get("__name__").map_or("", String::as_str);
    bullet(
        out,
        if name.is_empty() {
            "(unnamed series)"
        } else {
            name
        },
        t,
    );
    for (key, value) in series.iter().filter(|(key, _)| *key != "__name__") {
        out.extend_from_slice(format!("  {key}={}\n", quote(value)).as_bytes());
    }
}

fn series(body: &[u8], t: &Terminal) -> Result<Vec<u8>, String> {
    let context = |e: String| format!("failed to decode PromQL series response: {e}");
    let document = gojson::decode_ordered(body).map_err(context)?;
    let (mut status, mut error) = (String::new(), String::new());
    let (mut data, mut length) = (Vec::<StringMap>::new(), 0);
    (|| -> Result<(), String> {
        for (key, value) in top_object(&document, SERIES_TYPE)? {
            if key_is(key, "status") {
                decode_string(value, &mut status, "", "status")?;
            } else if key_is(key, "error") {
                decode_string(value, &mut error, "", "error")?;
            } else if key_is(key, "data") {
                decode_slice(
                    value,
                    &mut data,
                    &mut length,
                    "[]map[string]string",
                    "",
                    "data",
                    |value, item| decode_map(value, item, "", "data"),
                )?;
            }
        }
        Ok(())
    })()
    .map_err(context)?;
    drop(document);
    data.truncate(length);
    if status == "error" {
        return Err(error);
    }
    let mut out = Vec::new();
    if data.is_empty() {
        out.extend_from_slice(b"No series found.\n");
        return Ok(out);
    }
    for series in &data {
        print_series(&mut out, series.as_ref(), t);
    }
    out.extend_from_slice(format!("\ntotal={}\n", data.len()).as_bytes());
    Ok(out)
}

fn active_series(body: &[u8], t: &Terminal) -> Result<Vec<u8>, String> {
    let context = |e: String| format!("failed to decode PromQL active-series response: {e}");
    let document = gojson::decode_ordered(body).map_err(context)?;
    let (mut status, mut error, mut total) = (String::new(), String::new(), 0i64);
    let (mut data, mut length) = (Vec::<StringMap>::new(), 0);
    (|| -> Result<(), String> {
        for (key, value) in top_object(&document, &active_series_type())? {
            if key_is(key, "status") {
                decode_string(value, &mut status, "", "status")?;
            } else if key_is(key, "error") {
                decode_string(value, &mut error, "", "error")?;
            } else if key_is(key, "data") {
                for (key, value) in struct_fields(value, ACTIVE_SERIES_DATA_TYPE, "", "data")? {
                    if key_is(key, "total_active_series") {
                        decode_int(value, &mut total, "", "data.total_active_series")?;
                    } else if key_is(key, "series") {
                        decode_slice(
                            value,
                            &mut data,
                            &mut length,
                            "[]map[string]string",
                            "",
                            "data.series",
                            |value, item| decode_map(value, item, "", "data.series"),
                        )?;
                    }
                }
            }
        }
        Ok(())
    })()
    .map_err(context)?;
    drop(document);
    data.truncate(length);
    if status == "error" {
        return Err(error);
    }
    let mut out = format!(
        "{}\ntotal={total}\n\n",
        SELECTED.render("ACTIVE SERIES", t, line_width)
    )
    .into_bytes();
    if data.is_empty() {
        out.extend_from_slice(b"No active series found.\n");
        return Ok(out);
    }
    for series in &data {
        print_series(&mut out, series.as_ref(), t);
        out.push(b'\n');
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// cardinality / tsdb
// ---------------------------------------------------------------------------

#[derive(Default)]
struct Entry {
    name: String,
    value: i64,
}

fn entries(
    value: &Node,
    storage: &mut Vec<Entry>,
    length: &mut usize,
    structure: &str,
    path: &str,
) -> Result<(), String> {
    let name_path = format!("{path}.name");
    let value_path = format!("{path}.value");
    decode_slice(
        value,
        storage,
        length,
        "[]cmd.cardinalityEntry",
        structure,
        path,
        |value, entry| {
            for (key, value) in struct_fields(value, "cmd.cardinalityEntry", structure, path)? {
                if key_is(key, "name") {
                    decode_string(value, &mut entry.name, "cardinalityEntry", &name_path)?;
                } else if key_is(key, "value") {
                    decode_int(value, &mut entry.value, "cardinalityEntry", &value_path)?;
                }
            }
            Ok(())
        },
    )
}

fn entry_table(
    out: &mut Vec<u8>,
    name_header: &str,
    value_header: &str,
    entries: &[Entry],
    t: &Terminal,
) {
    const MAX_NAME_WIDTH: usize = 64;
    let mut name_width = width(name_header);
    for entry in entries {
        name_width = name_width.max(width(&entry.name).min(MAX_NAME_WIDTH));
    }
    let name_column = name_width + 4;
    let header = |text: &str| SELECTED.render(text, t, line_width);
    let rule = |text: &str| RULE.render(text, t, line_width);
    let body = |text: &str| STANDARD.render(text, t, line_width);
    out.extend_from_slice(header(&pad_right(name_header, name_column)).as_bytes());
    out.extend_from_slice(header(value_header).as_bytes());
    out.push(b'\n');
    out.extend_from_slice(rule(&"─".repeat(name_column)).as_bytes());
    out.extend_from_slice(rule(&"─".repeat(width(value_header))).as_bytes());
    out.push(b'\n');
    for entry in entries {
        let cell = truncate_cell(&entry.name, MAX_NAME_WIDTH);
        let leader = name_column.saturating_sub(width(&cell) + 1).max(2);
        out.extend_from_slice(
            format!(
                "{} {} {}\n",
                body(&cell),
                rule(&".".repeat(leader)),
                body(&entry.value.to_string())
            )
            .as_bytes(),
        );
    }
    out.push(b'\n');
}

fn cardinality(
    body: &[u8],
    name_header: &str,
    value_header: &str,
    t: &Terminal,
) -> Result<Vec<u8>, String> {
    let context = |e: String| format!("failed to decode PromQL cardinality response: {e}");
    let document = gojson::decode_ordered(body).map_err(context)?;
    let (mut status, mut error) = (String::new(), String::new());
    let (mut data, mut length) = (Vec::<Entry>::new(), 0);
    (|| -> Result<(), String> {
        for (key, value) in top_object(&document, CARDINALITY_TYPE)? {
            if key_is(key, "status") {
                decode_string(value, &mut status, "", "status")?;
            } else if key_is(key, "error") {
                decode_string(value, &mut error, "", "error")?;
            } else if key_is(key, "data") {
                entries(value, &mut data, &mut length, "", "data")?;
            }
        }
        Ok(())
    })()
    .map_err(context)?;
    data.truncate(length);
    if status == "error" {
        return Err(error);
    }
    let mut out = Vec::new();
    entry_table(&mut out, name_header, value_header, &data, t);
    Ok(out)
}

fn tsdb(body: &[u8], focus_label: &str, t: &Terminal) -> Result<Vec<u8>, String> {
    let document = gojson::decode_ordered(body)
        .map_err(|e| format!("failed to decode PromQL TSDB response: {e}"))?;
    let (mut status, mut error) = (String::new(), String::new());
    // json.RawMessage keeps the last data member verbatim, including null.
    let mut data: Option<&Node> = None;
    (|| -> Result<(), String> {
        for (key, value) in top_object(&document, TSDB_ENVELOPE_TYPE)? {
            if key_is(key, "status") {
                decode_string(value, &mut status, "", "status")?;
            } else if key_is(key, "error") {
                decode_string(value, &mut error, "", "error")?;
            } else if key_is(key, "data") {
                data = Some(value);
            }
        }
        Ok(())
    })()
    .map_err(|e| format!("failed to decode PromQL TSDB response: {e}"))?;
    if status == "error" {
        return Err(error);
    }
    let data_error = |e: String| format!("failed to decode PromQL TSDB data: {e}");
    let Some(data) = data else {
        return Err(data_error("unexpected end of JSON input".into()));
    };
    if data.is_null() || data.as_array().is_some_and(<[Node]>::is_empty) {
        return Ok(b"No TSDB stats found. Pass a metrics dataset name, for example: pb promql tsdb <dataset>\n".to_vec());
    }
    let (mut total_series, mut total_pairs) = (0i64, 0i64);
    let mut lists: [(Vec<Entry>, usize); 4] = Default::default();
    const LISTS: [&str; 4] = [
        "seriesCountByMetricName",
        "seriesCountByLabelName",
        "seriesCountByFocusLabelValue",
        "labelValueCountByLabelName",
    ];
    (|| -> Result<(), String> {
        let fields = match data {
            Node::Object(fields) => fields,
            _ => return Err(type_error(data, TSDB_DATA_TYPE, "", "", false)),
        };
        for (key, value) in fields {
            if key_is(key, "totalSeries") {
                decode_int(value, &mut total_series, "", "totalSeries")?;
            } else if key_is(key, "totalLabelValuePairs") {
                decode_int(value, &mut total_pairs, "", "totalLabelValuePairs")?;
            } else if let Some(index) = LISTS.iter().position(|name| key_is(key, name)) {
                let (storage, length) = &mut lists[index];
                entries(value, storage, length, "", LISTS[index])?;
            }
        }
        Ok(())
    })()
    .map_err(data_error)?;
    for (storage, length) in &mut lists {
        storage.truncate(*length);
    }
    let mut out = format!("\n{}\n", SELECTED.render("TSDB SUMMARY", t, line_width)).into_bytes();
    for (label, value) in [
        ("Total Series", total_series),
        ("Total Label Pairs", total_pairs),
    ] {
        let leader = 24usize.saturating_sub(width(label)).max(2);
        out.extend_from_slice(
            format!(
                "{} {} {}\n",
                STANDARD.render(label, t, line_width),
                RULE.render(&".".repeat(leader), t, line_width),
                STANDARD.render(&value.to_string(), t, line_width)
            )
            .as_bytes(),
        );
    }
    out.push(b'\n');
    let focus_title = format!("Series by {focus_label} value");
    let sections = [
        ("Top metrics by series count", "METRIC", "SERIES"),
        ("Top labels by series count", "LABEL", "SERIES"),
        (focus_title.as_str(), "VALUE", "SERIES"),
        ("Distinct values per label", "LABEL", "DISTINCT VALUES"),
    ];
    for ((title, name_header, value_header), (entries, _)) in sections.into_iter().zip(&lists) {
        if entries.is_empty() {
            continue;
        }
        out.push(b'\n');
        out.extend_from_slice(SELECTED.render(title, t, line_width).as_bytes());
        out.push(b'\n');
        entry_table(&mut out, name_header, value_header, entries, t);
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// active-queries
// ---------------------------------------------------------------------------

#[derive(Default)]
struct ActiveQuery {
    query: String,
    stream: String,
    started_at: String,
    elapsed_ms: i64,
}

fn active_queries(body: &[u8]) -> Result<Vec<u8>, String> {
    let context = |e: String| format!("failed to decode PromQL active-queries response: {e}");
    let document = gojson::decode_ordered(body).map_err(context)?;
    let (mut status, mut error) = (String::new(), String::new());
    let (mut data, mut length) = (Vec::<ActiveQuery>::new(), 0);
    let element_type = ACTIVE_QUERY_TYPE;
    let slice_type = format!("[]{ACTIVE_QUERY_TYPE}");
    (|| -> Result<(), String> {
        for (key, value) in top_object(&document, &active_queries_type())? {
            if key_is(key, "status") {
                decode_string(value, &mut status, "", "status")?;
            } else if key_is(key, "error") {
                decode_string(value, &mut error, "", "error")?;
            } else if key_is(key, "data") {
                decode_slice(
                    value,
                    &mut data,
                    &mut length,
                    &slice_type,
                    "",
                    "data",
                    |value, query| {
                        for (key, value) in struct_fields(value, element_type, "", "data")? {
                            if key_is(key, "query") {
                                decode_string(value, &mut query.query, "", "data.query")?;
                            } else if key_is(key, "stream") {
                                decode_string(value, &mut query.stream, "", "data.stream")?;
                            } else if key_is(key, "started_at") {
                                decode_string(value, &mut query.started_at, "", "data.started_at")?;
                            } else if key_is(key, "elapsed_ms") {
                                decode_int(value, &mut query.elapsed_ms, "", "data.elapsed_ms")?;
                            }
                        }
                        Ok(())
                    },
                )?;
            }
        }
        Ok(())
    })()
    .map_err(context)?;
    data.truncate(length);
    if status == "error" {
        return Err(error);
    }
    if data.is_empty() {
        return Ok(b"No active queries.\n".to_vec());
    }
    let mut out = Vec::new();
    out.extend_from_slice(
        format!(
            "{:<50}  {:<15}  {:<22}  {}\n",
            "QUERY", "STREAM", "STARTED", "ELAPSED"
        )
        .as_bytes(),
    );
    out.extend_from_slice("-".repeat(100).as_bytes());
    out.push(b'\n');
    for query in &data {
        let text = query.query.as_bytes();
        // Byte slicing, as in Go, may split a multi-byte character.
        let truncated = if text.len() > 48 {
            [&text[..45], b"..."].concat()
        } else {
            text.to_vec()
        };
        pad_runes(&mut out, &truncated, 50);
        out.extend_from_slice(b"  ");
        pad_runes(&mut out, query.stream.as_bytes(), 15);
        out.extend_from_slice(b"  ");
        pad_runes(&mut out, query.started_at.as_bytes(), 22);
        out.extend_from_slice(format!("  {}ms\n", query.elapsed_ms).as_bytes());
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(operation: &str, body: &str) -> String {
        String::from_utf8(render(operation, body.as_bytes(), false, "job").unwrap()).unwrap()
    }

    #[test]
    fn ansi_width_csi_intermediate_parameter_returns_to_ground() {
        // charmbracelet/x/ansi v0.4.5: CSI intermediate + parameter is an
        // IgnoreAction to GroundState, so the following A and x print.
        assert_eq!(line_width("\x1b[ 1Ax"), 2);
    }

    #[test]
    fn ansi_width_escape_restarts_and_c0_preserves_state() {
        // ESC restarts EscapeState anywhere; ordinary C0 executes without
        // leaving Escape/CSI/intermediate. DEL is ignored in those states.
        for input in [
            "\x1b\x1bAx",
            "\x1b\x07Ax",
            "\x1b\x7fAx",
            "\x1b[1\x07Ax",
            "\x1b \x07Ax",
        ] {
            assert_eq!(line_width(input), 1, "{input:?}");
        }
        assert_eq!(line_width("a\x00\x07\x7fb"), 2);
    }

    #[test]
    fn ansi_width_can_and_sub_cancel_sequences() {
        // CAN and SUB return to ground, including OSC and DCS string states.
        for prefix in [
            "\x1b",
            "\x1b ",
            "\x1b[",
            "\x1b[1",
            "\x1b[ ",
            "\x1b]title",
            "\x1bP",
            "\x1bP1",
            "\x1bP ",
            "\x1bPqdata",
            "\x1bXdata",
            "\x1b^data",
            "\x1b_data",
        ] {
            for cancel in ['\x18', '\x1a'] {
                let input = format!("{prefix}{cancel}Ax");
                assert_eq!(line_width(&input), 2, "{input:?}");
            }
        }
    }

    #[test]
    fn ansi_width_utf8_enters_ground_except_in_osc_and_dcs_strings() {
        // StringWidth handles Utf8State as a grapheme and then resets ground.
        // OSC and DCS string byte ranges override the anywhere UTF-8 rule.
        for prefix in [
            "\x1b", "\x1b ", "\x1b[", "\x1b[1", "\x1b[ ", "\x1bP", "\x1bP1", "\x1bP ", "\x1bX",
            "\x1b^", "\x1b_",
        ] {
            let input = format!("{prefix}日本");
            assert_eq!(line_width(&input), 4, "{input:?}");
        }
        assert_eq!(line_width("\x1b]日本\x07x"), 1);
        assert_eq!(line_width("\x1b]日本\x1b\\x"), 1);
        assert_eq!(line_width("\x1bPq日本\x1b\\x"), 1);
        assert_eq!(line_width("a日本e\u{301}👩‍💻z"), 9);
    }

    #[test]
    fn ansi_width_orphan_continuations_follow_byte_transitions() {
        // Pinned Go CLI widths: raw 0x9c inside string data terminates it;
        // the next continuation can start another control sequence.
        for (input, expected) in [
            ("\x1b]本x", 1),
            ("\x1bPq本x", 1),
            ("\x1b]✜x", 1),
            ("\x1b]✝x\x07y", 1),
            ("\x1b]✛1Ax", 1),
            ("\u{009b}1Ax", 3),
        ] {
            assert_eq!(line_width(input), expected, "{input:?}");
        }
    }

    #[test]
    fn ansi_width_regressions_control_table_padding() {
        // Exact uncolored rows from the pinned Go CLI differential fixtures.
        for (name, dots) in [("\x1b[ 1Ax", 6), ("\x1b\x1bAx", 7), ("\x1b[日本", 4)] {
            let body = format!(
                r#"{{"data":[{{"name":{},"value":1}}]}}"#,
                serde_json::to_string(name).unwrap()
            );
            let output = text("cardinality label-names", &body);
            assert!(
                output.ends_with(&format!("{name} {} 1\n\n", ".".repeat(dots))),
                "{output:?}"
            );
        }
    }

    #[test]
    fn raw_json_sorts_preserves_numbers_and_rejects_extra_values() {
        assert_eq!(
            render("run", br#"{"b":1.50e3,"a":"<&>","b":2}"#, true, "").unwrap(),
            b"{\n  \"a\": \"<&>\",\n  \"b\": 2\n}\n"
        );
        let error = |body: &[u8]| render("labels", body, true, "").unwrap_err();
        assert_eq!(error(b""), "server returned invalid JSON: EOF");
        assert_eq!(error(b" \n"), "server returned invalid JSON: EOF");
        assert_eq!(error(b"{"), "server returned invalid JSON: unexpected EOF");
        assert_eq!(error(b"{} {}"), "server returned multiple JSON values");
        assert_eq!(error(b"0123"), "server returned multiple JSON values");
        assert_eq!(
            error(b"{}x"),
            "server returned invalid JSON: invalid character 'x' looking for beginning of value"
        );
        assert_eq!(
            error(b"{} {\"a\""),
            "server returned invalid JSON: unexpected EOF"
        );
        assert_eq!(
            render("labels", b"[1] \n", true, "").unwrap(),
            b"[\n  1\n]\n"
        );
    }

    #[test]
    fn run_vector_and_matrix_text() {
        let body = r#"{"status":"success","data":{"resultType":"vector","result":[
            {"metric":{"__name__":"up","job":"api","a.b":"x","a":"y"},"value":[1700000000.9,"1"]},
            {"metric":{},"value":[1,2,3]},
            {"metric":{"x":"q\"z"},"value":["ts",true]}]}}"#;
        assert_eq!(
            text("run", body),
            "up{a.b=\"x\", a=\"y\", job=\"api\"}\n  2023-11-14T22:13:20Z  1\n\n\n\n{x=\"q\"z\"}\n  ts  true\n\nresult_type=vector  series=3\n"
        );
        let body = r#"{"data":{"resultType":"matrix","result":[{"metric":{"__name__":"m"},"values":[[0,1e21],[1,[1,"a",null]],[2],[3,{"b":1,"a":0.0001}]]}]}}"#;
        assert_eq!(
            text("run", body),
            "m\n  1970-01-01T00:00:00Z  1e+21\n  1970-01-01T00:00:01Z  [1 a <nil>]\n  1970-01-01T00:00:03Z  map[a:0.0001 b:1]\n\nresult_type=matrix  series=1\n"
        );
        assert_eq!(text("run", "null"), "No data returned.\n");
        assert_eq!(
            text("run", r#"{"data":{"result":[]}}"#),
            "No data returned.\n"
        );
    }

    #[test]
    fn run_decode_errors_follow_encoding_json() {
        let error = |body: &str| render("run", body.as_bytes(), false, "").unwrap_err();
        assert_eq!(
            error(r#"{"data":{"resultType":"scalar","result":[1700000000,"1"]}}"#),
            "failed to decode PromQL response: json: cannot unmarshal number into Go struct field promqlData.data.result of type cmd.promqlSeries"
        );
        assert_eq!(
            error("[]"),
            "failed to decode PromQL response: json: cannot unmarshal array into Go value of type cmd.promqlResponse"
        );
        assert_eq!(
            error(r#"{"data":{"result":[{"value":[1e1000,"1"]}]}}"#),
            "failed to decode PromQL response: json: cannot unmarshal number 1e1000 into Go struct field promqlSeries.data.result.value of type float64"
        );
        assert_eq!(
            error(r#"{"data":{"result":[{"metric":{"a":1}}]}}"#),
            "failed to decode PromQL response: json: cannot unmarshal number into Go struct field promqlSeries.data.result.metric of type string"
        );
        assert_eq!(
            error(r#"{"data":{"result":[{"values":[1]}]}}"#),
            "failed to decode PromQL response: json: cannot unmarshal number into Go struct field promqlSeries.data.result.values of type []interface {}"
        );
        assert_eq!(
            error(r#"{"data":3}"#),
            "failed to decode PromQL response: json: cannot unmarshal number into Go struct field promqlResponse.data of type cmd.promqlData"
        );
        assert_eq!(
            error("<html>"),
            "failed to decode PromQL response: invalid character '<' looking for beginning of value"
        );
    }

    #[test]
    fn duplicate_members_reuse_go_slice_elements() {
        let body = r#"{"data":{"resultType":"vector","result":[{"metric":{"a":"1"},"value":[0,"x"]},{"metric":{"b":"2"}}],
            "result":[{"metric":{"c":"3"}}],"result":[{},{}]}}"#;
        assert_eq!(
            text("run", body),
            "{a=\"1\", c=\"3\"}\n  1970-01-01T00:00:00Z  x\n\n{b=\"2\"}\n\nresult_type=vector  series=2\n"
        );
        assert_eq!(
            text(
                "labels",
                r#"{"data":["a","b"],"DATA":["z"],"data":[null,null]}"#
            ),
            " • z\n • b\n"
        );
    }

    #[test]
    fn timestamps_match_go_time_including_overflow() {
        assert_eq!(unix_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(unix_utc(-1), "1969-12-31T23:59:59Z");
        assert_eq!(unix_utc(i64::MIN), "292277026596-12-04T15:30:08Z");
        assert_eq!(unix_utc(-62_135_596_801), "0000-12-31T23:59:59Z");
        assert_eq!(unix_utc(253_402_300_800), "10000-01-01T00:00:00Z");
        let floats: Vec<String> = [
            1e21, 1e6, 999_999.0, 123_456.5, 0.0001, 0.00009, 1e-7, 1.5e300,
        ]
        .into_iter()
        .map(go_float)
        .collect();
        assert_eq!(
            floats.join("|"),
            "1e+21|1e+06|999999|123456.5|0.0001|9e-05|1e-07|1.5e+300"
        );
        assert_eq!(go_f64_to_i64(1e300), i64::MIN);
        assert_eq!(go_f64_to_i64(-1.9), -1);
    }

    #[test]
    fn labels_series_and_values_text() {
        assert_eq!(text("labels", r#"{"data":[]}"#), "No labels found.\n");
        assert_eq!(
            text("labels", r#"{"data":["a\tb","x\ny"]}"#),
            " • a    b\n • x\n y  \n"
        );
        assert_eq!(
            text("label-values", r#"{"data":["v"]}"#),
            " • v\n\nlabel=job  total=1\n"
        );
        assert_eq!(
            text(
                "series",
                r#"{"data":[{"__name__":"up","z":"\"q\"","a":"é"},null]}"#
            ),
            " • up\n  a=\"é\"\n  z=\"\\\"q\\\"\"\n • (unnamed series)\n\ntotal=2\n"
        );
        assert_eq!(
            text(
                "cardinality active-series",
                r#"{"data":{"total_active_series":2,"series":[{"x":"1"}]}}"#
            ),
            "ACTIVE SERIES\ntotal=2\n\n • (unnamed series)\n  x=\"1\"\n\n"
        );
        assert_eq!(
            render("labels", br#"{"data":[1]}"#, false, "").unwrap_err(),
            "failed to decode PromQL labels response: json: cannot unmarshal number into Go struct field .data of type string"
        );
    }

    #[test]
    fn cardinality_tables_use_display_widths() {
        let long = "x".repeat(70);
        let body =
            format!(r#"{{"data":[{{"name":"job","value":12}},{{"name":"{long}","value":-1}}]}}"#);
        let output = text("cardinality label-names", &body);
        let column = 68;
        let mut expected = format!("{:<column$}DISTINCT VALUES\n", "LABEL");
        expected += &format!("{}{}\n", "─".repeat(column), "─".repeat(15));
        expected += &format!("job {} 12\n", ".".repeat(column - 4));
        expected += &format!("{}… {} -1\n", "x".repeat(63), ".".repeat(3));
        expected += "\n";
        assert_eq!(output, expected);
        assert_eq!(
            text(
                "cardinality label-values",
                r#"{"data":[{"name":"日本","value":1}]}"#
            ),
            "VALUE    SERIES COUNT\n─────────────────────\n日本 .... 1\n\n"
        );
        assert_eq!(
            render(
                "cardinality label-values",
                br#"{"data":[{"value":1.5}]}"#,
                false,
                ""
            )
            .unwrap_err(),
            "failed to decode PromQL cardinality response: json: cannot unmarshal number 1.5 into Go struct field cardinalityEntry.data.value of type int"
        );
    }

    #[test]
    fn anonymous_struct_errors_match_go_type_strings() {
        // Expected strings come from Go 1.25.4 encoding/json with these types.
        let error = |operation: &str, body: &str| {
            render(operation, body.as_bytes(), false, "").unwrap_err()
        };
        assert_eq!(
            error("labels", "3"),
            format!(
                "failed to decode PromQL labels response: json: cannot unmarshal number into Go value of type {LABELS_TYPE}"
            )
        );
        assert_eq!(
            error("series", r#"{"data":[1]}"#),
            "failed to decode PromQL series response: json: cannot unmarshal number into Go struct field .data of type map[string]string"
        );
        assert_eq!(
            error("cardinality label-names", r#"{"data":[3]}"#),
            "failed to decode PromQL cardinality response: json: cannot unmarshal number into Go struct field .data of type cmd.cardinalityEntry"
        );
        assert_eq!(
            error(
                "cardinality active-series",
                r#"{"data":{"total_active_series":"x"}}"#
            ),
            "failed to decode PromQL active-series response: json: cannot unmarshal string into Go struct field .data.total_active_series of type int"
        );
        assert_eq!(
            error("cardinality active-series", r#"{"data":3}"#),
            format!(
                "failed to decode PromQL active-series response: json: cannot unmarshal number into Go struct field .data of type {ACTIVE_SERIES_DATA_TYPE}"
            )
        );
        assert_eq!(
            error("active-queries", r#"{"data":[{"elapsed_ms":"x"}]}"#),
            "failed to decode PromQL active-queries response: json: cannot unmarshal string into Go struct field .data.elapsed_ms of type int"
        );
        assert_eq!(
            error("active-queries", r#"{"data":3}"#),
            format!(
                "failed to decode PromQL active-queries response: json: cannot unmarshal number into Go struct field .data of type []{ACTIVE_QUERY_TYPE}"
            )
        );
        assert_eq!(
            error("tsdb", "3"),
            format!(
                "failed to decode PromQL TSDB response: json: cannot unmarshal number into Go value of type {TSDB_ENVELOPE_TYPE}"
            )
        );
        assert_eq!(
            error("tsdb", r#"{"data":{"totalSeries":1.5}}"#),
            "failed to decode PromQL TSDB data: json: cannot unmarshal number 1.5 into Go struct field .totalSeries of type int"
        );
        assert_eq!(
            error(
                "tsdb",
                r#"{"data":{"seriesCountByMetricName":[{"name":1}]}}"#
            ),
            "failed to decode PromQL TSDB data: json: cannot unmarshal number into Go struct field cardinalityEntry.seriesCountByMetricName.name of type string"
        );
    }

    #[test]
    fn tsdb_shapes() {
        let none = "No TSDB stats found. Pass a metrics dataset name, for example: pb promql tsdb <dataset>\n";
        assert_eq!(text("tsdb", r#"{"data":null}"#), none);
        assert_eq!(text("tsdb", r#"{"data":[]}"#), none);
        assert_eq!(
            render("tsdb", b"{}", false, "").unwrap_err(),
            "failed to decode PromQL TSDB data: unexpected end of JSON input"
        );
        assert!(render("tsdb", br#"{"data":[1]}"#, false, "")
            .unwrap_err()
            .starts_with("failed to decode PromQL TSDB data: json: cannot unmarshal array into Go value of type struct { TotalSeries int"));
        let output = text(
            "tsdb",
            r#"{"data":{"totalSeries":5,"totalLabelValuePairs":7,"seriesCountByFocusLabelValue":[{"name":"a","value":1}]}}"#,
        );
        assert_eq!(
            output,
            format!(
                "\nTSDB SUMMARY\nTotal Series {} 5\nTotal Label Pairs {} 7\n\n\nSeries by job value\nVALUE    SERIES\n───────────────\na {} 1\n\n",
                ".".repeat(12),
                ".".repeat(7),
                ".".repeat(7)
            )
        );
    }

    #[test]
    fn active_queries_truncate_bytes_and_pad_runes() {
        let query = format!("{}€tail-to-make-it-long", "q".repeat(43));
        let body = format!(
            r#"{{"data":[{{"query":"{query}","stream":"s","started_at":"t","elapsed_ms":5}}]}}"#
        );
        let output = render("active-queries", body.as_bytes(), false, "").unwrap();
        let mut expected = format!(
            "{:<50}  {:<15}  {:<22}  {}\n",
            "QUERY", "STREAM", "STARTED", "ELAPSED"
        )
        .into_bytes();
        expected.extend_from_slice("-".repeat(100).as_bytes());
        expected.push(b'\n');
        expected.extend_from_slice("q".repeat(43).as_bytes());
        expected.extend_from_slice(&"€".as_bytes()[..2]);
        expected.extend_from_slice(b"...");
        // 43 + two invalid bytes + 3 = 48 runes.
        expected.extend_from_slice(&[b' '; 2]);
        expected.extend_from_slice(format!("  {:<15}  {:<22}  5ms\n", "s", "t").as_bytes());
        assert_eq!(output, expected);
        assert_eq!(
            text("active-queries", r#"{"data":[]}"#),
            "No active queries.\n"
        );
    }
}
