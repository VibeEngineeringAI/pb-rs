//! Blocking response rendering. The caller supplies a demand-driven BufRead
//! bridge; success after an array delimiter never asks it for the unread tail.
use std::{
    fmt,
    io::{self, BufRead, Read, Write},
    str::FromStr,
};

use crate::gojson::{self, Document, Node};
use serde_json::{Map, Number, Value};

#[path = "scan.rs"]
mod scan;

// Compiles to the operation alone in ordinary builds.
macro_rules! measured {
    ($stage:ident, $operation:expr) => {{
        #[cfg(feature = "diagnostics")]
        let _timer = crate::diagnostics::Timer::begin(crate::diagnostics::Stage::$stage);
        $operation
    }};
}

#[derive(Debug)]
pub enum OutputErrorKind {
    Io(io::Error),
    Decode(String),
}

#[derive(Debug)]
pub struct OutputError {
    pub context: &'static str,
    pub kind: OutputErrorKind,
}

impl fmt::Display for OutputError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: ", self.context)?;
        match &self.kind {
            OutputErrorKind::Io(error) => error.fmt(formatter),
            OutputErrorKind::Decode(error) => formatter.write_str(error),
        }
    }
}

impl std::error::Error for OutputError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match &self.kind {
            OutputErrorKind::Io(error) => Some(error),
            OutputErrorKind::Decode(_) => None,
        }
    }
}

fn io_error(context: &'static str, error: io::Error) -> OutputError {
    OutputError {
        context,
        kind: OutputErrorKind::Io(error),
    }
}

fn decode_error(error: impl Into<String>) -> OutputError {
    OutputError {
        context: "error decoding JSON response",
        kind: OutputErrorKind::Decode(error.into()),
    }
}

fn decode_io(error: io::Error) -> OutputError {
    io_error("error decoding JSON response", error)
}

fn prefix<R: BufRead>(reader: &mut R) -> io::Result<(Vec<u8>, bool)> {
    let mut bytes = Vec::new();
    loop {
        match scan::peek(reader)? {
            None => return Ok((bytes, true)),
            Some(byte) => {
                bytes.push(byte);
                reader.consume(1);
                if !scan::space(byte) {
                    return Ok((bytes, false));
                }
            }
        }
    }
}

fn empty_array<R: BufRead>(reader: &mut R) -> io::Result<(Vec<u8>, bool)> {
    let mut bytes = Vec::new();
    let mut closed = false;
    loop {
        let Some(byte) = scan::peek(reader)? else {
            return Ok((bytes, closed));
        };
        bytes.push(byte);
        reader.consume(1);
        if scan::space(byte) {
            continue;
        }
        if !closed && byte == b']' {
            closed = true;
        } else {
            return Ok((bytes, false));
        }
    }
}

/// Copy raw bytes, retaining Go's special treatment of whitespace-only bodies
/// and empty arrays. Body and writer failures during the copy share its context.
pub fn stream_text<R: BufRead, W: Write>(
    reader: &mut R,
    out: &mut W,
    status: &str,
) -> Result<(), OutputError> {
    let (prefix, empty) = measured!(ScanFraming, prefix(reader))
        .map_err(|error| io_error("failed to read response body", error))?;
    if empty {
        let _ = writeln!(out, "No response body returned (status: {status}).");
        let _ = out.flush();
        return Ok(());
    }
    let mut lookahead = Vec::new();
    if prefix.last() == Some(&b'[') {
        let (bytes, empty) = measured!(ScanFraming, empty_array(reader))
            .map_err(|error| io_error("failed to read response body", error))?;
        if empty {
            let _ = writeln!(out, "Query succeeded: no rows returned (status: {status}).");
            let _ = out.flush();
            return Ok(());
        }
        lookahead = bytes;
    }
    let context = "failed to stream response body";
    out.write_all(&prefix)
        .map_err(|error| io_error(context, error))?;
    let mut last = *prefix.last().expect("nonempty prefix");
    if !lookahead.is_empty() {
        out.write_all(&lookahead)
            .map_err(|error| io_error(context, error))?;
        last = *lookahead.last().unwrap();
    }
    loop {
        // Flush before waiting for a network chunk. Go's actual stdout is
        // unbuffered; this also makes a buffered test sink expose each chunk.
        let _ = out.flush();
        #[cfg(feature = "diagnostics")]
        let copy_timer = crate::diagnostics::Timer::begin(crate::diagnostics::Stage::ScanFraming);
        let bytes = reader
            .fill_buf()
            .map_err(|error| io_error(context, error))?;
        #[cfg(feature = "diagnostics")]
        drop(copy_timer);
        if bytes.is_empty() {
            break;
        }
        out.write_all(bytes)
            .map_err(|error| io_error(context, error))?;
        last = *bytes.last().unwrap();
        let length = bytes.len();
        reader.consume(length);
    }
    if last != b'\n' {
        let _ = out.write_all(b"\n");
    }
    let _ = out.flush();
    Ok(())
}

/// Render one element at a time. Write errors are ignored, as in Go's fmt
/// calls, while decoder/reader failures retain any already-emitted output.
pub fn stream_json<R: BufRead, W: Write>(
    reader: &mut R,
    out: &mut W,
    status: &str,
) -> Result<(), OutputError> {
    #[cfg(feature = "diagnostics")]
    let prefix_timer = crate::diagnostics::Timer::begin(crate::diagnostics::Stage::ScanFraming);
    let (prefix, empty) =
        prefix(reader).map_err(|error| io_error("failed to read response body", error))?;
    if empty {
        let _ = writeln!(out, "No response body returned (status: {status}).");
        let _ = out.flush();
        return Ok(());
    }
    let mut reader = io::Cursor::new(prefix).chain(reader);
    let first = scan::nonspace(&mut reader)
        .map_err(decode_io)?
        .ok_or_else(|| decode_error("EOF"))?;
    if first != b'[' {
        if first != b'{' {
            let bytes = scan::value(&mut reader).map_err(scan_error)?;
            let _ = float_document(&bytes)?;
        }
        return Err(decode_error("expected JSON array response"));
    }
    reader.consume(1);
    #[cfg(feature = "diagnostics")]
    drop(prefix_timer);
    let _ = out.write_all(b"[");
    let _ = out.flush();
    let mut first = true;
    let mut scanner = scan::Scanner::default();
    let mut encoded = Vec::new();
    let mut indented = Vec::new();
    loop {
        #[cfg(feature = "diagnostics")]
        let _sample = crate::diagnostics::ElementSample::begin();
        #[cfg(feature = "diagnostics")]
        let scan_timer = crate::diagnostics::Timer::begin(crate::diagnostics::Stage::ScanFraming);
        let byte = scan::nonspace(&mut reader)
            .map_err(decode_io)?
            .ok_or_else(|| decode_error("EOF"))?;
        if byte == b']' {
            reader.consume(1);
            #[cfg(feature = "diagnostics")]
            drop(scan_timer);
            let _ = out.write_all(if first { b"]\n" } else { b"\n]\n" });
            let _ = out.flush();
            return Ok(());
        }
        if byte == b'}' {
            return Err(decode_error(scan::invalid(
                byte,
                if first {
                    "looking for beginning of value"
                } else {
                    "after array element"
                },
            )));
        }
        if !first {
            if byte != b',' {
                return Err(decode_error("expected comma after array element"));
            }
            reader.consume(1);
        }
        scanner.value(&mut reader).map_err(scan_error)?;
        let bytes = &scanner.bytes;
        #[cfg(feature = "diagnostics")]
        drop(scan_timer);
        #[cfg(feature = "diagnostics")]
        crate::diagnostics::element(bytes.len());
        let value = float_document(bytes)?;
        measured!(Serialization, serialize_element(&value, &mut encoded));
        // Escaping now shares the indentation pass, so its time belongs to
        // IndentationCopy rather than Serialization. Keep the byte counters
        // comparable to the former escaped pretty output (including newline).
        let separator: &[u8] = if first { b"\n" } else { b",\n" };
        measured!(
            IndentationCopy,
            indent_element(&encoded, &mut indented, separator)
        );
        #[cfg(feature = "diagnostics")]
        {
            let indentation =
                separator.len() + 2 + 2 * encoded.iter().filter(|byte| **byte == b'\n').count();
            crate::diagnostics::serialized(indented.len() - indentation + 1);
            crate::diagnostics::indented(indented.len() - separator.len() - 2);
        }
        // Publish only a complete decoded row, including its preceding separator.
        // The opening '[' remains immediate; no output is buffered across rows.
        let _ = out.write_all(&indented);
        let _ = out.flush();
        first = false;
        #[cfg(feature = "diagnostics")]
        measured!(Drop, {
            drop(value);
        });
    }
}

/// Serialize directly into reusable storage. HTML and separator escaping is
/// applied together with array indentation below, avoiding repeated copies.
fn serialize_element(value: &Document, encoded: &mut Vec<u8>) {
    use serde::Serialize;
    encoded.clear();
    let mut serializer = serde_json::Serializer::pretty(encoded);
    value
        .serialize(serde_stacker::Serializer::new(&mut serializer))
        .expect("serializable output");
}

fn indent_element(encoded: &[u8], indented: &mut Vec<u8>, separator: &[u8]) {
    indented.clear();
    indented.extend_from_slice(separator);
    indented.extend_from_slice(b"  ");
    let mut start = 0;
    let mut index = 0;
    while index < encoded.len() {
        let replacement: Option<(&[u8], usize)> = match encoded[index] {
            b'\n' => Some((b"\n  ", 1)),
            b'&' => Some((b"\\u0026", 1)),
            b'<' => Some((b"\\u003c", 1)),
            b'>' => Some((b"\\u003e", 1)),
            0xe2 if encoded[index..].starts_with(&[0xe2, 0x80, 0xa8]) => Some((b"\\u2028", 3)),
            0xe2 if encoded[index..].starts_with(&[0xe2, 0x80, 0xa9]) => Some((b"\\u2029", 3)),
            _ => None,
        };
        if let Some((replacement, length)) = replacement {
            indented.extend_from_slice(&encoded[start..index]);
            indented.extend_from_slice(replacement);
            index += length;
            start = index;
        } else {
            index += 1;
        }
    }
    indented.extend_from_slice(&encoded[start..]);
}

fn scan_error(error: scan::ScanError) -> OutputError {
    match error {
        scan::ScanError::Io(error) => decode_io(error),
        scan::ScanError::Syntax(error) => decode_error(error),
    }
}

fn float_document(bytes: &[u8]) -> Result<Document, OutputError> {
    let mut document =
        measured!(OrderedDecode, gojson::decode_ordered(bytes)).map_err(decode_error)?;
    let result = measured!(
        ValueConversion,
        convert(&mut document)
            .map(Document::new)
            .map_err(decode_error)
    );
    #[cfg(feature = "diagnostics")]
    measured!(Drop, drop(document));
    result
}

fn convert(node: &mut Node) -> Result<Value, String> {
    stacker::maybe_grow(64 * 1024, 1024 * 1024, || match node {
        Node::Scalar(Value::Number(number)) => {
            let value = number
                .as_f64()
                .filter(|value| value.is_finite())
                .ok_or_else(|| {
                    format!("json: cannot unmarshal number {number} into Go value of type float64")
                })?;
            Ok(Value::Number(
                Number::from_str(&float(value)).expect("finite JSON number"),
            ))
        }
        Node::Scalar(value) => Ok(std::mem::take(value)),
        Node::Array(nodes) => {
            let mut result = Document::new(Value::Array(Vec::with_capacity(nodes.len())));
            for node in nodes {
                result.as_array_mut().unwrap().push(convert(node)?);
            }
            Ok(result.into_inner())
        }
        Node::Object(nodes) => {
            let mut result = Document::new(Value::Object(Map::with_capacity(nodes.len())));
            for (key, node) in nodes {
                let value = convert(node)?;
                if let Some(old) = result
                    .as_object_mut()
                    .unwrap()
                    .insert(std::mem::take(key), value)
                {
                    gojson::drop_value(old);
                }
            }
            result.as_object_mut().unwrap().sort_keys();
            Ok(result.into_inner())
        }
    })
}

// Match encoding/json's float64 exponent thresholds and shortest rounding.
fn float(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() { "-0" } else { "0" }.into();
    }
    if (1e-6..1e21).contains(&value.abs()) {
        return value.to_string();
    }
    let scientific = format!("{value:e}");
    let (mantissa, exponent) = scientific.split_once('e').expect("scientific float");
    let exponent: i32 = exponent.parse().expect("float exponent");
    format!("{mantissa}e{exponent:+}")
}

/// Return at most 65,536 preview bytes plus `...`. The extra byte establishes
/// truncation without reading or waiting for the remainder of the response.
pub fn read_error_preview<R: Read>(reader: &mut R) -> io::Result<Vec<u8>> {
    const LIMIT: usize = 65_536;
    let mut bytes = Vec::new();
    reader.take((LIMIT + 1) as u64).read_to_end(&mut bytes)?;
    let truncated = bytes.len() > LIMIT;
    if truncated {
        bytes.truncate(LIMIT);
    }
    let (mut start, mut end) = (0, bytes.len());
    while start < end {
        let length = utf8_length(bytes[start]);
        let Some(slice) = bytes.get(start..start + length) else {
            break;
        };
        if !std::str::from_utf8(slice)
            .ok()
            .and_then(|text| text.chars().next())
            .is_some_and(char::is_whitespace)
        {
            break;
        }
        start += length;
    }
    while end > start {
        let mut rune_start = end - 1;
        while rune_start > start && bytes[rune_start] & 0xc0 == 0x80 && end - rune_start < 4 {
            rune_start -= 1;
        }
        if !std::str::from_utf8(&bytes[rune_start..end])
            .ok()
            .and_then(|text| text.chars().next())
            .is_some_and(char::is_whitespace)
        {
            break;
        }
        end = rune_start;
    }
    let mut preview = bytes[start..end].to_vec();
    if truncated {
        preview.extend_from_slice(b"...");
    }
    Ok(preview)
}

fn utf8_length(byte: u8) -> usize {
    match byte {
        0..=0x7f => 1,
        0xc2..=0xdf => 2,
        0xe0..=0xef => 3,
        0xf0..=0xf4 => 4,
        _ => 1,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn json(body: &[u8]) -> (Vec<u8>, Result<(), OutputError>) {
        let mut out = Vec::new();
        let result = stream_json(&mut io::Cursor::new(body), &mut out, "200 OK");
        (out, result)
    }

    #[test]
    fn text_is_raw_and_empty_messages_are_distinct() {
        for (body, expected) in [
            (
                b" \t\n\r".as_slice(),
                b"No response body returned (status: 200 OK).\n".as_slice(),
            ),
            (
                b" [ \n ]\t",
                b"Query succeeded: no rows returned (status: 200 OK).\n",
            ),
            (b" [] tail", b" [] tail\n"),
            (b"\xff\xfe", b"\xff\xfe\n"),
            (b"raw\n", b"raw\n"),
        ] {
            let mut out = Vec::new();
            stream_text(&mut io::Cursor::new(body), &mut out, "200 OK").unwrap();
            assert_eq!(out, expected);
        }
    }

    #[test]
    fn pretty_json_rounds_sorts_and_escapes() {
        let (out, result) =
            json(br#"[{"z":1,"a":9007199254740993,"z":"<&>","n":-0,"f":1e-7},[true,null]]ignored"#);
        result.unwrap();
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "[\n  {\n    \"a\": 9007199254740992,\n    \"f\": 1e-7,\n    \"n\": -0,\n    \"z\": \"\\u003c\\u0026\\u003e\"\n  },\n  [\n    true,\n    null\n  ]\n]\n"
        );
        let (out, result) = json(b"[\"\xff\\ud800\"]");
        result.unwrap();
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "[\n  \"\u{fffd}\u{fffd}\"\n]\n"
        );
    }

    #[test]
    fn reusable_render_buffers_match_generic_renderer() {
        let mut encoded = Vec::new();
        let mut indented = Vec::new();
        for body in [
            br#"{"<&>":"literal \\u2028, escaped newline \n","nested":[[],{},"<&>"]}"#.as_slice(),
            "[\"é\u{2028}\u{2029}\",true,null,-0,1e21]".as_bytes(),
            br#""short""#,
        ] {
            let value = float_document(body).unwrap();
            serialize_element(&value, &mut encoded);
            indent_element(&encoded, &mut indented, b"");
            let generic = crate::render::json(&value, true);
            let expected = format!(
                "  {}",
                generic.strip_suffix('\n').unwrap().replace('\n', "\n  ")
            );
            assert_eq!(indented, expected.as_bytes());
        }
    }

    #[test]
    fn conversion_error_safely_drops_deep_remaining_nodes() {
        let deep = format!("{}0{}", "[".repeat(2_000), "]".repeat(2_000));
        for body in [
            format!("[[\"moved\",1e1000,{deep}]]"),
            format!("[[{deep},1e1000]]"),
            format!("[{{\"a\":\"moved\",\"a\":1e1000,\"z\":{deep}}}]"),
        ] {
            let (out, result) = json(body.as_bytes());
            assert_eq!(out, b"[");
            assert!(
                result
                    .unwrap_err()
                    .to_string()
                    .contains("cannot unmarshal number 1e1000")
            );
        }
    }

    #[test]
    fn malformed_tails_keep_completed_elements() {
        for (body, partial, error) in [
            (
                b"[1,]".as_slice(),
                b"[\n  1".as_slice(),
                "invalid character ']' looking for beginning of value",
            ),
            (b"[1 x]", b"[\n  1", "expected comma after array element"),
            (b"[1", b"[\n  1", "EOF"),
            (b"[{\"x\":1", b"[", "unexpected EOF"),
            (
                b"[truX",
                b"[",
                "invalid character 'X' in literal true (expecting 'e')",
            ),
            (
                b"[1e1000]",
                b"[",
                "json: cannot unmarshal number 1e1000 into Go value of type float64",
            ),
        ] {
            let (out, result) = json(body);
            assert_eq!(out, partial, "{}", String::from_utf8_lossy(body));
            assert_eq!(
                result.unwrap_err().to_string(),
                format!("error decoding JSON response: {error}")
            );
        }
    }

    #[test]
    fn scalar_and_container_boundaries_preserve_decoder_context() {
        let (out, result) = json(br#"[0,-1.25e2,"a,]}",false,null,{},[]]"#);
        result.unwrap();
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "[\n  0,\n  -125,\n  \"a,]}\",\n  false,\n  null,\n  {},\n  []\n]\n"
        );
        for body in [b"true".as_slice(), b"{}", b"\"x\""] {
            let (out, result) = json(body);
            assert!(out.is_empty());
            assert_eq!(
                result.unwrap_err().to_string(),
                "error decoding JSON response: expected JSON array response"
            );
        }
        let (out, result) = json(br#"[{"n":1e1000,"n":0}]"#);
        assert_eq!(out, b"[");
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("cannot unmarshal number 1e1000")
        );
    }

    struct RejectWrites;
    impl Write for RejectWrites {
        fn write(&mut self, _: &[u8]) -> io::Result<usize> {
            Err(io::Error::other("write fault"))
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn write_error_policy_matches_each_go_output_path() {
        stream_json(&mut io::Cursor::new(b"[1,2]"), &mut RejectWrites, "200 OK").unwrap();
        stream_text(&mut io::Cursor::new(b"[]"), &mut RejectWrites, "200 OK").unwrap();
        let error =
            stream_text(&mut io::Cursor::new(b"raw"), &mut RejectWrites, "200 OK").unwrap_err();
        assert_eq!(error.context, "failed to stream response body");
        assert!(matches!(error.kind, OutputErrorKind::Io(_)));
    }

    struct FaultTail {
        bytes: io::Cursor<Vec<u8>>,
        reads: usize,
    }
    impl Read for FaultTail {
        fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
            let bytes = self.fill_buf()?;
            let length = out.len().min(bytes.len());
            out[..length].copy_from_slice(&bytes[..length]);
            self.consume(length);
            Ok(length)
        }
    }
    impl BufRead for FaultTail {
        fn fill_buf(&mut self) -> io::Result<&[u8]> {
            self.reads += 1;
            if self.bytes.position() as usize == self.bytes.get_ref().len() {
                return Err(io::Error::other("tail fault"));
            }
            self.bytes.fill_buf()
        }
        fn consume(&mut self, amount: usize) {
            self.bytes.consume(amount);
        }
    }
    fn fault(bytes: &[u8]) -> FaultTail {
        FaultTail {
            bytes: io::Cursor::new(bytes.to_vec()),
            reads: 0,
        }
    }

    #[test]
    fn json_stops_at_close_and_rejects_invalid_prefix_before_tail() {
        let mut out = Vec::new();
        stream_json(&mut fault(b"[{}]"), &mut out, "200 OK").unwrap();
        assert_eq!(out, b"[\n  {}\n]\n");
        let error = stream_json(&mut fault(b"[truX"), &mut Vec::new(), "200 OK").unwrap_err();
        assert!(matches!(error.kind, OutputErrorKind::Decode(_)));
        let error = stream_text(&mut fault(b"[]"), &mut Vec::new(), "200 OK").unwrap_err();
        assert_eq!(error.context, "failed to read response body");
        let error = stream_json(&mut fault(b"[{"), &mut Vec::new(), "200 OK").unwrap_err();
        assert_eq!(error.context, "error decoding JSON response");
        assert!(matches!(error.kind, OutputErrorKind::Io(_)));
    }

    #[test]
    fn preview_caps_without_reading_tail_and_preserves_invalid_utf8() {
        let mut reader = fault(&vec![b'x'; 65_537]);
        let preview = read_error_preview(&mut reader).unwrap();
        assert_eq!(preview.len(), 65_539);
        assert!(preview.ends_with(b"..."));
        assert_eq!(
            read_error_preview(&mut io::Cursor::new(b" \xc2\xa0\xff\n\xe3\x80\x80")).unwrap(),
            b"\xff"
        );
        let mut bytes = vec![b'x'; 65_535];
        bytes.extend_from_slice(b"\xc3\xa9");
        assert!(
            read_error_preview(&mut io::Cursor::new(bytes))
                .unwrap()
                .ends_with(b"\xc3...")
        );
    }

    #[test]
    fn deep_decode_conversion_serialization_and_drop_are_safe() {
        let nested = format!("{}0{}", "[".repeat(1_000), "]".repeat(1_000));
        let (out, result) = json(format!("[{nested}]").as_bytes());
        result.unwrap();
        assert!(!out.is_empty());
        let too_deep = format!("[{}0{}]", "[".repeat(10_001), "]".repeat(10_001));
        let (out, result) = json(too_deep.as_bytes());
        assert_eq!(out, b"[");
        assert!(
            result
                .unwrap_err()
                .to_string()
                .ends_with("exceeded max depth")
        );
    }

    /// Run explicitly after application captures. This isolates framing from
    /// decoding/rendering, with no per-byte counters or stage clocks. The
    /// bounded producer's waiting/copy costs can affect wall time; only the
    /// consumer thread contributes to thread_cpu_ns. It cannot by itself prove
    /// which instruction inside Bytes::slice accounts for a difference.
    #[cfg(feature = "diagnostics")]
    #[test]
    #[ignore = "diagnostic microbenchmark; run in an otherwise idle measurement window"]
    fn diagnostic_scan_microbenchmark() {
        use crate::diagnostics::current_thread_cpu_ns;
        use bytes::Bytes;
        use std::time::Instant;

        const ROW: &[u8] = br#"{"a":1,"b":"synthetic ASCII payload for scanner attribution","c":[1,2,3],"d":{"z":true,"x":null}}"#;
        let rows = std::env::var("PB_DIAGNOSTICS_SCAN_ROWS")
            .ok()
            .and_then(|v| v.parse::<usize>().ok())
            .filter(|v| (1..=1_000_000).contains(v))
            .unwrap_or(100_000);
        let repetitions = std::env::var("PB_DIAGNOSTICS_SCAN_REPETITIONS")
            .ok()
            .and_then(|v| v.parse::<usize>().ok())
            .filter(|v| (1..=100).contains(v))
            .unwrap_or(5);
        let mut body = Vec::with_capacity((ROW.len() + 1) * rows);
        for _ in 0..rows {
            body.extend_from_slice(ROW);
            body.push(b'\n');
        }
        let source = Bytes::from(body);

        fn run(reader: &mut impl BufRead, expected_rows: usize) -> (u64, Option<u64>) {
            let wall_start = Instant::now();
            let cpu_start = current_thread_cpu_ns();
            let mut found = 0;
            let mut framed = 0;
            while scan::nonspace(reader).unwrap().is_some() {
                let bytes = scan::value(reader).unwrap();
                assert_eq!(bytes.as_slice(), ROW);
                framed += bytes.len();
                found += 1;
            }
            let cpu_end = current_thread_cpu_ns();
            let wall_ns = wall_start.elapsed().as_nanos() as u64;
            assert_eq!(found, expected_rows);
            assert_eq!(framed, ROW.len() * expected_rows);
            (
                wall_ns,
                cpu_start.zip(cpu_end).map(|(s, e)| e.saturating_sub(s)),
            )
        }
        let mut measurements = Vec::new();
        for repetition in 0..repetitions {
            // Alternate pair order to reduce systematic warm-cache bias.
            for mode in if repetition % 2 == 0 {
                ["cursor_buffered_8k", "body_reader_shared_bytes_8k"]
            } else {
                ["body_reader_shared_bytes_8k", "cursor_buffered_8k"]
            } {
                let (wall_ns, thread_cpu_ns) = if mode == "cursor_buffered_8k" {
                    run(
                        &mut io::BufReader::with_capacity(8192, io::Cursor::new(source.clone())),
                        rows,
                    )
                } else {
                    let (sender, receiver) = tokio::sync::mpsc::channel(1);
                    std::thread::scope(|scope| {
                        let bytes = source.clone();
                        let producer = scope.spawn(move || {
                            for start in (0..bytes.len()).step_by(8192) {
                                sender
                                    .blocking_send(Ok(
                                        bytes.slice(start..(start + 8192).min(bytes.len()))
                                    ))
                                    .unwrap();
                            }
                        });
                        let mut reader = super::super::BodyReader {
                            receiver,
                            current: Bytes::new(),
                            position: 0,
                            diagnostic_delivered_bytes: 0,
                        };
                        let measured = run(&mut reader, rows);
                        producer.join().unwrap();
                        measured
                    })
                };
                measurements.push(serde_json::json!({ "repetition": repetition, "reader": mode, "wall_ns": wall_ns, "thread_cpu_ns": thread_cpu_ns }));
            }
        }
        println!(
            "{}",
            serde_json::json!({
                "schema_version": 1, "diagnostic": "scan_microbenchmark", "complete": true,
                "rows": rows, "input_bytes": source.len(), "chunk_bytes": 8192,
                "includes": "framing, output Vec allocation/drop, row-byte validation; bounded producer can affect wall but not consumer thread CPU",
                "excludes": "HTTP, ordered decoding, conversion, rendering, output writes; no per-byte diagnostic counters or clocks",
                "measurements": measurements,
            })
        );
    }
}
