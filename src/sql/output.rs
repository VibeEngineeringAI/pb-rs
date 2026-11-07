//! Blocking response rendering. The caller supplies a demand-driven BufRead
//! bridge; success after an array delimiter never asks it for the unread tail.
use std::{
    fmt,
    io::{self, BufRead, Read, Write},
};

use crate::gojson::{self, Node, OrderedDocument};
use serde_json::{Number, Value};

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
        // One pass emits the escaped, array-indented row; there is no
        // intermediate pretty buffer, so the whole pass counts as Serialization.
        let separator: &[u8] = if first { b"\n" } else { b",\n" };
        if bytes.len() <= SMALL_ROW_SOURCE_BYTES {
            // Even quadratic indentation plus float/string expansion for this
            // source bound fits below ROW_CHUNK_BYTES. Keep the direct Vec
            // renderer for tiny rows, without a chunk check on every append.
            measured!(
                Serialization,
                render_element(&value, &mut indented, separator)
            );
            debug_assert!(indented.len() <= ROW_CHUNK_BYTES);
            #[cfg(feature = "diagnostics")]
            {
                let body = &indented[separator.len() + 2..];
                let newlines = body.iter().filter(|&&byte| byte == b'\n').count();
                crate::diagnostics::serialized(body.len() - 2 * newlines + 1);
                crate::diagnostics::indented(body.len());
            }
            let _ = out.write_all(&indented);
        } else {
            let mut row = RowBuffer::new(&mut indented, out);
            measured!(Serialization, render_element(&value, &mut row, separator));
            #[cfg(feature = "diagnostics")]
            {
                // Include the full rendered row even if a write failed and its
                // remaining bytes were discarded.
                let body_bytes = row.rendered_bytes - separator.len() - 2;
                let body_newlines = row.newlines - 1;
                crate::diagnostics::serialized(body_bytes - 2 * body_newlines + 1);
                crate::diagnostics::indented(body_bytes);
            }
            row.finish();
        }
        // Decoding and all float validation finished before publishing any row
        // bytes. Small rows keep their single write; large rows use bounded
        // chunks, and a write failure discards only the rest of that row.
        let _ = out.flush();
        first = false;
        #[cfg(feature = "diagnostics")]
        measured!(Drop, {
            drop(value);
        });
    }
}

const ROW_CHUNK_BYTES: usize = 1024 * 1024;
const SMALL_ROW_SOURCE_BYTES: usize = 512;
// For n source bytes: <=6n token bytes, <=n newlines, <=n+2 indent
// bytes per newline, and <=4 prefix bytes. Allow twice the bound for
// geometric Vec growth; larger source rows always use the chunked sink.
const _: () = assert!(
    2 * (SMALL_ROW_SOURCE_BYTES * SMALL_ROW_SOURCE_BYTES + 9 * SMALL_ROW_SOURCE_BYTES + 4)
        <= ROW_CHUNK_BYTES
);

trait RenderSink {
    fn clear(&mut self);
    fn extend_from_slice(&mut self, bytes: &[u8]);
    fn spaces(&mut self, count: usize);

    fn push(&mut self, byte: u8) {
        self.extend_from_slice(&[byte]);
    }
}

impl RenderSink for Vec<u8> {
    fn clear(&mut self) {
        Vec::clear(self);
    }

    fn extend_from_slice(&mut self, bytes: &[u8]) {
        Vec::extend_from_slice(self, bytes);
    }

    fn spaces(&mut self, count: usize) {
        self.resize(self.len() + count, b' ');
    }

    fn push(&mut self, byte: u8) {
        Vec::push(self, byte);
    }
}

struct RowBuffer<'a, W> {
    buffer: &'a mut Vec<u8>,
    out: &'a mut W,
    failed: bool,
    #[cfg(any(feature = "diagnostics", test))]
    rendered_bytes: usize,
    #[cfg(any(feature = "diagnostics", test))]
    newlines: usize,
}

impl<'a, W: Write> RowBuffer<'a, W> {
    fn new(buffer: &'a mut Vec<u8>, out: &'a mut W) -> Self {
        Self {
            buffer,
            out,
            failed: false,
            #[cfg(any(feature = "diagnostics", test))]
            rendered_bytes: 0,
            #[cfg(any(feature = "diagnostics", test))]
            newlines: 0,
        }
    }

    fn publish(&mut self) {
        if !self.failed && !self.buffer.is_empty() {
            self.failed = self.out.write_all(self.buffer).is_err();
        }
        self.buffer.clear();
    }

    fn reserve(&mut self, length: usize) {
        let needed = self.buffer.len() + length;
        if needed > self.buffer.capacity() {
            // Vec's usual doubling can overshoot the chunk limit when a long
            // string follows a short prefix. Keep retained capacity bounded.
            let capacity = needed
                .max(8)
                .max(self.buffer.capacity().saturating_mul(2))
                .min(ROW_CHUNK_BYTES);
            self.buffer.reserve_exact(capacity - self.buffer.len());
        }
    }

    fn finish(mut self) {
        self.publish();
    }
}

impl<W: Write> RenderSink for RowBuffer<'_, W> {
    fn clear(&mut self) {
        self.buffer.clear();
        self.failed = false;
        #[cfg(any(feature = "diagnostics", test))]
        {
            self.rendered_bytes = 0;
            self.newlines = 0;
        }
    }

    fn extend_from_slice(&mut self, mut bytes: &[u8]) {
        #[cfg(any(feature = "diagnostics", test))]
        {
            self.rendered_bytes += bytes.len();
            self.newlines += bytes.iter().filter(|&&byte| byte == b'\n').count();
        }
        if self.failed {
            return;
        }
        if bytes.len() <= ROW_CHUNK_BYTES - self.buffer.len() {
            self.reserve(bytes.len());
            self.buffer.extend_from_slice(bytes);
            return;
        }
        while !bytes.is_empty() && !self.failed {
            if self.buffer.len() == ROW_CHUNK_BYTES {
                self.publish();
                if self.failed {
                    break;
                }
            }
            let count = bytes.len().min(ROW_CHUNK_BYTES - self.buffer.len());
            self.reserve(count);
            self.buffer.extend_from_slice(&bytes[..count]);
            bytes = &bytes[count..];
        }
    }

    fn spaces(&mut self, mut count: usize) {
        #[cfg(any(feature = "diagnostics", test))]
        {
            self.rendered_bytes += count;
        }
        if self.failed {
            return;
        }
        if count <= ROW_CHUNK_BYTES - self.buffer.len() {
            self.reserve(count);
            self.buffer.resize(self.buffer.len() + count, b' ');
            return;
        }
        while count != 0 && !self.failed {
            if self.buffer.len() == ROW_CHUNK_BYTES {
                self.publish();
                if self.failed {
                    break;
                }
            }
            let length = count.min(ROW_CHUNK_BYTES - self.buffer.len());
            self.reserve(length);
            self.buffer.resize(self.buffer.len() + length, b' ');
            count -= length;
        }
    }

    fn push(&mut self, byte: u8) {
        #[cfg(any(feature = "diagnostics", test))]
        {
            self.rendered_bytes += 1;
            self.newlines += usize::from(byte == b'\n');
        }
        if self.failed {
            return;
        }
        if self.buffer.len() == ROW_CHUNK_BYTES {
            self.publish();
            if self.failed {
                return;
            }
        }
        self.reserve(1);
        self.buffer.push(byte);
    }
}

/// Write one normalized row: the separator, then the element at the array's
/// indentation, matching serde_json's pretty layout followed by Go's
/// HTML/U+2028/U+2029 escaping, without building a Value tree.
fn render_element<S: RenderSink>(node: &Node, indented: &mut S, separator: &[u8]) {
    // Retain the diagnostics stage for staging the separator/base indentation.
    // Escaping and continuation indentation now happen in Serialization.
    #[cfg(feature = "diagnostics")]
    let prefix_timer = crate::diagnostics::Timer::begin(crate::diagnostics::Stage::IndentationCopy);
    indented.clear();
    indented.extend_from_slice(separator);
    indented.extend_from_slice(b"  ");
    #[cfg(feature = "diagnostics")]
    drop(prefix_timer);
    render(node, indented, 1);
}

fn render<S: RenderSink>(node: &Node, out: &mut S, depth: usize) {
    stacker::maybe_grow(64 * 1024, 1024 * 1024, || match node {
        Node::Scalar(value) => scalar(value, out, depth),
        Node::Array(nodes) if nodes.is_empty() => out.extend_from_slice(b"[]"),
        Node::Array(nodes) => {
            out.push(b'[');
            for (index, node) in nodes.iter().enumerate() {
                out.extend_from_slice(if index == 0 { b"\n".as_slice() } else { b",\n" });
                indent(out, depth + 1);
                render(node, out, depth + 1);
            }
            out.push(b'\n');
            indent(out, depth);
            out.push(b']');
        }
        Node::Object(fields) if fields.is_empty() => out.extend_from_slice(b"{}"),
        Node::Object(fields) => {
            out.push(b'{');
            for (index, (key, node)) in fields.iter().enumerate() {
                out.extend_from_slice(if index == 0 { b"\n".as_slice() } else { b",\n" });
                indent(out, depth + 1);
                string(key, out);
                out.extend_from_slice(b": ");
                render(node, out, depth + 1);
            }
            out.push(b'\n');
            indent(out, depth);
            out.push(b'}');
        }
    })
}

fn indent<S: RenderSink>(out: &mut S, depth: usize) {
    out.spaces(2 * depth);
}

fn scalar<S: RenderSink>(value: &Value, out: &mut S, depth: usize) {
    match value {
        Value::Null => out.extend_from_slice(b"null"),
        Value::Bool(true) => out.extend_from_slice(b"true"),
        Value::Bool(false) => out.extend_from_slice(b"false"),
        // Normalization already stored Go's float64 text.
        Value::Number(number) => out.extend_from_slice(number.as_str().as_bytes()),
        Value::String(text) => string(text, out),
        // decode_ordered represents containers as nodes; keep the former
        // generic serialization so this match stays total.
        container => {
            let mut encoded = Vec::new();
            serialize_element(container, &mut encoded);
            escape_indented(&encoded, out, depth);
        }
    }
}

// Bytes serde_json escapes inside strings, plus Go's `&`, `<`, `>` and the
// U+2028/U+2029 lead byte.
static SPECIAL: [bool; 256] = {
    let mut table = [false; 256];
    let mut byte = 0;
    while byte < 0x20 {
        table[byte] = true;
        byte += 1;
    }
    table[b'"' as usize] = true;
    table[b'\\' as usize] = true;
    table[b'&' as usize] = true;
    table[b'<' as usize] = true;
    table[b'>' as usize] = true;
    table[0xe2] = true;
    table
};

fn string<S: RenderSink>(text: &str, out: &mut S) {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let bytes = text.as_bytes();
    out.push(b'"');
    let mut start = 0;
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        if !SPECIAL[byte as usize] {
            index += 1;
            continue;
        }
        let (replacement, length): (&[u8], usize) = match byte {
            b'"' => (b"\\\"", 1),
            b'\\' => (b"\\\\", 1),
            b'\n' => (b"\\n", 1),
            b'\r' => (b"\\r", 1),
            b'\t' => (b"\\t", 1),
            0x08 => (b"\\b", 1),
            0x0c => (b"\\f", 1),
            b'&' => (b"\\u0026", 1),
            b'<' => (b"\\u003c", 1),
            b'>' => (b"\\u003e", 1),
            0xe2 if bytes[index..].starts_with(&[0xe2, 0x80, 0xa8]) => (b"\\u2028", 3),
            0xe2 if bytes[index..].starts_with(&[0xe2, 0x80, 0xa9]) => (b"\\u2029", 3),
            0xe2 => {
                index += 1;
                continue;
            }
            _ => {
                out.extend_from_slice(&bytes[start..index]);
                out.extend_from_slice(b"\\u00");
                out.push(HEX[usize::from(byte >> 4)]);
                out.push(HEX[usize::from(byte & 0xf)]);
                index += 1;
                start = index;
                continue;
            }
        };
        out.extend_from_slice(&bytes[start..index]);
        out.extend_from_slice(replacement);
        index += length;
        start = index;
    }
    out.extend_from_slice(&bytes[start..]);
    out.push(b'"');
}

/// Serialize a Value with serde_json's pretty layout (unescaped for Go).
fn serialize_element(value: &Value, encoded: &mut Vec<u8>) {
    encoded.clear();
    let mut serializer = serde_json::Serializer::pretty(encoded);
    gojson::serialize_value(value, serde_stacker::Serializer::new(&mut serializer))
        .expect("serializable output");
}

/// Append pretty output with Go's escapes, indenting continuation lines to `depth`.
fn escape_indented<S: RenderSink>(encoded: &[u8], out: &mut S, depth: usize) {
    let mut start = 0;
    let mut index = 0;
    while index < encoded.len() {
        let replacement: Option<(&[u8], usize)> = match encoded[index] {
            b'\n' => Some((b"\n", 1)),
            b'&' => Some((b"\\u0026", 1)),
            b'<' => Some((b"\\u003c", 1)),
            b'>' => Some((b"\\u003e", 1)),
            0xe2 if encoded[index..].starts_with(&[0xe2, 0x80, 0xa8]) => Some((b"\\u2028", 3)),
            0xe2 if encoded[index..].starts_with(&[0xe2, 0x80, 0xa9]) => Some((b"\\u2029", 3)),
            _ => None,
        };
        if let Some((replacement, length)) = replacement {
            out.extend_from_slice(&encoded[start..index]);
            out.extend_from_slice(replacement);
            if encoded[index] == b'\n' {
                indent(out, depth);
            }
            index += length;
            start = index;
        } else {
            index += 1;
        }
    }
    out.extend_from_slice(&encoded[start..]);
}

fn scan_error(error: scan::ScanError) -> OutputError {
    match error {
        scan::ScanError::Io(error) => decode_io(error),
        scan::ScanError::Syntax(error) => decode_error(error),
    }
}

fn float_document(bytes: &[u8]) -> Result<OrderedDocument, OutputError> {
    let mut document =
        measured!(OrderedDecode, gojson::decode_ordered(bytes)).map_err(decode_error)?;
    // On failure the document's own Drop releases deep remaining nodes.
    measured!(ValueConversion, normalize(&mut document)).map_err(decode_error)?;
    Ok(document)
}

/// Rewrite the decoded tree in place into the form the former Value conversion
/// produced: numbers become Go float64 text, and each object's members become
/// sorted unique keys holding the last duplicate. Every member, including an
/// overwritten duplicate, is checked in document order before any sorting.
fn normalize(node: &mut Node) -> Result<(), String> {
    stacker::maybe_grow(64 * 1024, 1024 * 1024, || match node {
        Node::Scalar(Value::Number(number)) => {
            if let Some(length) = plain_decimal(number.as_str()) {
                if length < number.as_str().len() {
                    let text = number.as_str()[..length].to_owned();
                    *number = Number::from_string_unchecked(text);
                }
            } else {
                let value = number
                    .as_f64()
                    .filter(|value| value.is_finite())
                    .ok_or_else(|| {
                        format!(
                            "json: cannot unmarshal number {number} into Go value of type float64"
                        )
                    })?;
                *number = Number::from_string_unchecked(float_text(value));
            }
            Ok(())
        }
        Node::Scalar(_) => Ok(()),
        Node::Array(nodes) => nodes.iter_mut().try_for_each(normalize),
        Node::Object(fields) => {
            for (_, node) in fields.iter_mut() {
                normalize(node)?;
            }
            sort_members(fields);
            Ok(())
        }
    })
}

/// Length of the lexeme prefix that is already Go's float64 text, for an
/// exponent-free number whose digits from the first nonzero through the last
/// kept digit number at most 15 and whose magnitude is zero or at least 1e-6.
///
/// Since 10^15 < 2^52, distinct decimals of at most 15 significant digits map
/// to distinct normal float64 values, so no other decimal of that length or
/// shorter round-trips: the shortest digits are the lexeme's own once trailing
/// fraction zeros (and a bare point) are dropped. The span bounds magnitude
/// below 1e15, and at most five leading fraction zeros keeps it at or above
/// 1e-6, so Go uses fixed notation. Zero keeps its sign as `0` or `-0`.
fn plain_decimal(lexeme: &str) -> Option<usize> {
    let bytes = lexeme.as_bytes();
    let sign = usize::from(bytes.first() == Some(&b'-'));
    let (integer, fraction, point) = match bytes[sign..].iter().position(|&b| b == b'.') {
        Some(offset) => {
            let point = sign + offset;
            (&bytes[sign..point], &bytes[point + 1..], Some(point))
        }
        None => (&bytes[sign..], &[][..], None),
    };
    if integer.is_empty()
        || !integer.iter().chain(fraction).all(u8::is_ascii_digit)
        || (point.is_some() && fraction.is_empty())
    {
        return None;
    }
    let kept = fraction.len() - fraction.iter().rev().take_while(|&&b| b == b'0').count();
    let fraction = &fraction[..kept];
    let span = if integer == b"0" {
        let zeros = fraction.iter().take_while(|&&b| b == b'0').count();
        if zeros > 5 {
            return None;
        }
        fraction.len() - zeros
    } else if integer[0] == b'0' {
        return None;
    } else {
        integer.len() + fraction.len()
    };
    (span <= 15).then(|| match point {
        Some(point) if kept > 0 => point + 1 + kept,
        Some(point) => point,
        None => bytes.len(),
    })
}

/// `float` as `Number::from_str` stored it: a positive exponent gains `+`.
fn float_text(value: f64) -> String {
    let mut text = float(value);
    if let Some(index) = text.find('e')
        && !matches!(text.as_bytes().get(index + 1), Some(b'-' | b'+'))
    {
        text.insert(index + 1, '+');
    }
    text
}

/// Stable sort keeps duplicates in document order, so the last of each run
/// wins as with Map::insert. Replaced values may be deep; drop them iteratively.
fn sort_members(fields: &mut Vec<(String, Node)>) {
    if fields.windows(2).all(|pair| pair[0].0 < pair[1].0) {
        return;
    }
    fields.sort_by(|left, right| left.0.cmp(&right.0));
    if fields.windows(2).all(|pair| pair[0].0 != pair[1].0) {
        return;
    }
    let mut unique: Vec<(String, Node)> = Vec::with_capacity(fields.len());
    for (key, node) in std::mem::take(fields) {
        match unique.last_mut() {
            Some(last) if last.0 == key => {
                gojson::drop_node(std::mem::replace(&mut last.1, node));
            }
            _ => unique.push((key, node)),
        }
    }
    *fields = unique;
}

// Rust's Display rounds shortest-decimal ties differently from Go. zmij uses
// the same tie choice as Go; only its notation needs adjustment for encoding/json.
fn float(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() { "-0" } else { "0" }.into();
    }
    let mut buffer = zmij::Buffer::new();
    let text = buffer.format_finite(value);
    if !(1e-6..1e21).contains(&value.abs()) {
        return text.into();
    }
    let Some((mantissa, exponent)) = text.split_once('e') else {
        return text.strip_suffix(".0").unwrap_or(text).into();
    };
    let exponent: i32 = exponent.parse().expect("finite float exponent");
    let negative = mantissa.starts_with('-');
    let digits = mantissa.trim_start_matches('-');
    let point = digits.find('.').unwrap_or(digits.len()) as i32 + exponent;
    let digits_len = digits.len() - usize::from(digits.contains('.'));
    // Move the decimal point without rounding again; fixed output needs at most 25 bytes.
    let mut output = String::with_capacity(28);
    if negative {
        output.push('-');
    }
    if point <= 0 {
        output.push_str("0.");
        for _ in point..0 {
            output.push('0');
        }
    }
    for (i, digit) in digits.bytes().filter(|b| *b != b'.').enumerate() {
        if point > 0 && i as i32 == point {
            output.push('.');
        }
        output.push(char::from(digit));
    }
    for _ in digits_len as i32..point {
        output.push('0');
    }
    output
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

    /// The former pipeline: Value conversion, serde_json pretty output, then a
    /// separate Go-escaping and array-indentation copy.
    fn oracle_convert(node: &mut Node) -> Result<Value, String> {
        use gojson::Document;
        use serde_json::Map;
        use std::str::FromStr;
        stacker::maybe_grow(64 * 1024, 1024 * 1024, || match node {
            Node::Scalar(Value::Number(number)) => {
                let value = number
                    .as_f64()
                    .filter(|value| value.is_finite())
                    .ok_or_else(|| {
                        format!(
                            "json: cannot unmarshal number {number} into Go value of type float64"
                        )
                    })?;
                Ok(Value::Number(
                    Number::from_str(&float(value)).expect("finite JSON number"),
                ))
            }
            Node::Scalar(value) => Ok(std::mem::take(value)),
            Node::Array(nodes) => {
                let mut result = Document::new(Value::Array(Vec::with_capacity(nodes.len())));
                for node in nodes {
                    result.as_array_mut().unwrap().push(oracle_convert(node)?);
                }
                Ok(result.into_inner())
            }
            Node::Object(nodes) => {
                let mut result = Document::new(Value::Object(Map::with_capacity(nodes.len())));
                for (key, node) in nodes {
                    let value = oracle_convert(node)?;
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

    fn oracle_document(bytes: &[u8]) -> Result<gojson::Document, String> {
        let mut document = gojson::decode_ordered(bytes)?;
        oracle_convert(&mut document).map(gojson::Document::new)
    }

    fn oracle(bytes: &[u8], separator: &[u8]) -> Result<Vec<u8>, String> {
        let value = oracle_document(bytes)?;
        let mut encoded = Vec::new();
        serialize_element(&value, &mut encoded);
        let mut indented = separator.to_vec();
        indented.extend_from_slice(b"  ");
        escape_indented(&encoded, &mut indented, 1);
        Ok(indented)
    }

    fn direct(bytes: &[u8], separator: &[u8]) -> Result<Vec<u8>, String> {
        let mut document = gojson::decode_ordered(bytes)?;
        normalize(&mut document)?;
        let mut indented = Vec::new();
        render_element(&document, &mut indented, separator);
        Ok(indented)
    }

    fn assert_matches_oracle(body: &[u8]) {
        for separator in [b"\n".as_slice(), b",\n"] {
            let (expected, actual) = (oracle(body, separator), direct(body, separator));
            assert!(
                expected == actual,
                "{}\nexpected: {:?}\nactual: {:?}",
                String::from_utf8_lossy(body),
                expected.map(|bytes| String::from_utf8_lossy(&bytes).into_owned()),
                actual.map(|bytes| String::from_utf8_lossy(&bytes).into_owned()),
            );
        }
    }

    #[test]
    fn reusable_render_buffers_match_generic_renderer() {
        let mut indented = Vec::new();
        for body in [
            br#"{"<&>":"literal \\u2028, escaped newline \n","nested":[[],{},"<&>"]}"#.as_slice(),
            "[\"é\u{2028}\u{2029}\",true,null,-0,1e21]".as_bytes(),
            br#""short""#,
        ] {
            let value = float_document(body).unwrap();
            render_element(&value, &mut indented, b"");
            let generic = crate::render::json(&oracle_document(body).unwrap(), true);
            let expected = format!(
                "  {}",
                generic.strip_suffix('\n').unwrap().replace('\n', "\n  ")
            );
            assert_eq!(indented, expected.as_bytes());
        }
    }

    #[test]
    fn direct_rendering_matches_former_pipeline() {
        let mut controls = String::from("\"");
        for byte in 0u8..0x20 {
            controls.push_str(&format!("\\u{byte:04x}"));
        }
        controls.push_str("\\u007f\\\"\\\\/\"");
        let cases = [
            // Duplicate keys: last wins, sorted, nested and repeated.
            r#"{"z":1,"a":2,"z":3,"m":{"b":1,"b":{"x":[1,2]},"a":0},"a":"last"}"#.to_owned(),
            r#"{"k":[1,2,3],"k":{},"k":[]}"#.to_owned(),
            r#"{"b":1,"a":2}"#.to_owned(),
            r#"{"a":1,"b":2,"c":3}"#.to_owned(),
            r#"{"":0,"\u0000":1,"A":2,"a":3,"é":4,"\u2028":5,"&":6}"#.to_owned(),
            // An overwritten duplicate must still fail, and the first failing
            // number in document order is the one reported.
            r#"{"a":1e1000,"a":1}"#.to_owned(),
            r#"{"a":1,"a":-1e1000}"#.to_owned(),
            r#"{"b":1e999,"a":1e1000}"#.to_owned(),
            r#"[{"z":[1e400]},{"a":2e400}]"#.to_owned(),
            // Escapes in values and keys.
            controls.clone(),
            format!("{{{controls}:{controls}}}"),
            "\"<&>\u{2028}\u{2029}\u{2027}\u{202a}€\u{e2}é日本🎉\"".to_owned(),
            r#"{"<a&b>":"\u2028\u2029\ud800\udc00\ud800"}"#.to_owned(),
            // Numbers through each notation boundary.
            r#"[0,-0,1,-1,0.0,-0.0,1.0,1E5,1e+5,12e-1,0.1,1e-6,1e-7,0.000001,1e20,1e21,-1e+21]"#
                .to_owned(),
            r#"[123456789012345,-123456789012345,999999999999999,1234567890123456,100000000000000]"#
                .to_owned(),
            r#"[9007199254740993,9223372036854775807,-9223372036854775808,18446744073709551616]"#
                .to_owned(),
            r#"[100000000000000000000,1.5e300,5e-324,2.2250738585072014e-308]"#.to_owned(),
            r#"[1.7976931348623157e308,-4.9e-324,0.30000000000000004]"#.to_owned(),
            r#"[1e1000]"#.to_owned(),
            r#"[[],{},[[]],[{}],{"a":{}},{"a":[]},true,false,null,"x"]"#.to_owned(),
        ];
        for body in cases {
            assert_matches_oracle(body.as_bytes());
        }
    }

    struct Random(u64);
    impl Random {
        fn next(&mut self) -> u64 {
            // xorshift64*: deterministic, dependency-free test input.
            self.0 ^= self.0 >> 12;
            self.0 ^= self.0 << 25;
            self.0 ^= self.0 >> 27;
            self.0.wrapping_mul(0x2545_f491_4f6c_dd1d)
        }
        fn below(&mut self, bound: u64) -> u64 {
            self.next() % bound
        }
    }

    fn random_finite(random: &mut Random) -> f64 {
        loop {
            let value = match random.below(4) {
                0 => f64::from_bits(random.next()),
                1 => (random.next() >> random.below(64)) as f64,
                2 => {
                    let scale = 10f64.powi(random.below(30) as i32);
                    -((random.next() >> random.below(64)) as f64) / scale
                }
                _ => f64::from_bits(random.next() & 0x800f_ffff_ffff_ffff),
            };
            if value.is_finite() {
                return value;
            }
        }
    }

    #[test]
    fn random_finite_floats_match_former_number_text() {
        use std::str::FromStr;
        let mut random = Random(0x9e37_79b9_7f4a_7c15);
        for _ in 0..20_000 {
            let value = random_finite(&mut random);
            let former = Number::from_str(&float(value)).unwrap();
            assert_eq!(float_text(value), former.as_str(), "{value:?}");
            // Several input spellings of the same float, plus integer lexemes
            // at the exact-integer fast path's boundary.
            let sign = if random.below(2) == 0 { 1 } else { -1 };
            let integer = (random.next() >> random.below(64)) as i128 * sign;
            let body = format!(
                "[{value:?},{value:e},{},{integer},{{\"v\":{value:?}}}]",
                float(value)
            );
            assert_matches_oracle(body.as_bytes());
        }
    }

    fn random_value(random: &mut Random, depth: usize, out: &mut String) {
        const STRINGS: [&str; 8] = [
            "",
            "a",
            "<&>",
            "\\u2028\\u2029",
            "\\n\\t\\u0001\\u001f",
            "é€\u{2027}",
            "\\\"\\\\/",
            "🎉\\ud800",
        ];
        const NUMBERS: [&str; 8] = [
            "0",
            "-0",
            "1e21",
            "1E-7",
            "0.1",
            "123456789012345678",
            "-12.5e3",
            "5e-324",
        ];
        match random.below(if depth == 0 { 4 } else { 6 }) {
            0 => out.push_str(["true", "false", "null"][random.below(3) as usize]),
            1 => out.push_str(NUMBERS[random.below(8) as usize]),
            2 => out.push_str(&format!("{:?}", random_finite(random))),
            3 => {
                out.push('"');
                out.push_str(STRINGS[random.below(8) as usize]);
                out.push('"');
            }
            4 => {
                out.push('[');
                for index in 0..random.below(4) {
                    if index > 0 {
                        out.push(',');
                    }
                    random_value(random, depth - 1, out);
                }
                out.push(']');
            }
            _ => {
                out.push('{');
                for index in 0..random.below(6) {
                    if index > 0 {
                        out.push(',');
                    }
                    // A small key alphabet forces duplicates and reordering.
                    out.push('"');
                    out.push_str(["b", "a", "<", "é", "", "aa"][random.below(6) as usize]);
                    out.push_str("\":");
                    random_value(random, depth - 1, out);
                }
                out.push('}');
            }
        }
    }

    /// Every lexeme's normalized text must equal the former parse-and-format
    /// text, whether or not the plain-decimal fast path accepts it.
    fn assert_decimal_matches_float(lexeme: &str) -> bool {
        let value: f64 = lexeme.parse().unwrap();
        let expected = float_text(value);
        let mut node = Node::Scalar(Value::Number(Number::from_string_unchecked(lexeme.into())));
        normalize(&mut node).unwrap();
        let Node::Scalar(Value::Number(actual)) = node else {
            unreachable!()
        };
        assert_eq!(actual.as_str(), expected, "{lexeme}");
        plain_decimal(lexeme).is_some()
    }

    fn random_digits(random: &mut Random, count: usize, out: &mut String) {
        for _ in 0..count {
            out.push(char::from(b'0' + random.below(10) as u8));
        }
    }

    #[test]
    fn plain_decimals_match_float_text() {
        let fast = [
            "0",
            "-0",
            "0.0",
            "-0.0",
            "-0.000000000000000000000000",
            "1.0",
            "1.50",
            "100.000",
            "0.000001",
            "-0.000001",
            "0.0000010000000000",
            "0.00000999999999999999",
            "999999999999999",
            "999999999999999.0",
            "99999999999999.9",
            "0.999999999999999",
            "123456789012345",
            "12345678.9012345",
            "0.1",
            "0.2",
            "0.3",
            "1.1",
        ];
        let slow = [
            "0.0000001",
            "0.00000099999999999999",
            "-0.00000000000000001",
            "1234567890123456",
            "1234567890123456.0",
            "0.1234567890123456",
            "9007199254740993",
            "1000000000000000.5",
            "100000000000000000000.0",
            "1000000000000000000000.0",
            "1e2",
            "1E-2",
            "-1.5e+3",
            "0.10000000000000000555",
        ];
        for lexeme in fast {
            assert!(assert_decimal_matches_float(lexeme), "{lexeme}");
        }
        for lexeme in slow {
            assert!(!assert_decimal_matches_float(lexeme), "{lexeme}");
        }
        let mut random = Random(0x2545_f491_4f6c_dd1d);
        let mut accepted = 0;
        for _ in 0..200_000 {
            let mut lexeme = String::new();
            if random.below(2) == 0 {
                lexeme.push('-');
            }
            if random.below(2) == 0 {
                lexeme.push('0');
            } else {
                lexeme.push(char::from(b'1' + random.below(9) as u8));
                let count = random.below(22) as usize;
                random_digits(&mut random, count, &mut lexeme);
            }
            if random.below(4) != 0 {
                lexeme.push('.');
                // Long leading zeros straddle the 1e-6 cutoff; long trailing
                // zeros and 15- versus 16-digit spans straddle the length cutoff.
                let zeros = match random.below(3) {
                    0 => random.below(9),
                    1 => 4 + random.below(4),
                    _ => 0,
                } as usize;
                lexeme.extend(std::iter::repeat_n('0', zeros));
                let count = random.below(18) as usize;
                random_digits(&mut random, count, &mut lexeme);
                let trailing = random.below(4).pow(2) as usize;
                lexeme.extend(std::iter::repeat_n('0', trailing));
                if lexeme.ends_with('.') {
                    lexeme.push('0');
                }
            }
            accepted += usize::from(assert_decimal_matches_float(&lexeme));
        }
        assert!(accepted > 50_000, "{accepted}");
        // Dense spans at the length cutoff and the 1e-6 boundary.
        for digits in 14..=16 {
            for leading in b'1'..=b'9' {
                for tail in [b'0', b'1', b'5', b'9'] {
                    let mut body = vec![leading];
                    body.resize(digits - 1, b'9');
                    body.push(tail);
                    let body = String::from_utf8(body).unwrap();
                    for point in 0..=digits {
                        for sign in ["", "-"] {
                            let lexeme = match point {
                                0 => format!("{sign}0.{body}"),
                                _ if point == digits => format!("{sign}{body}"),
                                _ => format!("{sign}{}.{}", &body[..point], &body[point..]),
                            };
                            assert_decimal_matches_float(&lexeme);
                            for zeros in 1..=7 {
                                let small = format!("{sign}0.{}{body}", "0".repeat(zeros));
                                assert_decimal_matches_float(&small);
                            }
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn random_documents_match_former_pipeline() {
        let mut random = Random(0x2545_f491_4f6c_dd1d);
        let mut body = String::new();
        for iteration in 0..5_000 {
            body.clear();
            random_value(&mut random, 5, &mut body);
            // Occasionally overwrite or bury an out-of-range number.
            if iteration % 7 == 0 {
                body = format!("{{\"k\":1e999,\"k\":{body}}}");
            } else if iteration % 11 == 0 {
                body = format!("[{body},{{\"x\":-1e400}}]");
            }
            assert_matches_oracle(body.as_bytes());
        }
    }

    #[test]
    fn deep_duplicates_and_rendering_match_former_pipeline() {
        let deep = |depth: usize| format!("{}0{}", "[".repeat(depth), "]".repeat(depth));
        let deep_object =
            |depth: usize| format!("{}1e1000{}", "{\"a\":".repeat(depth), "}".repeat(depth));
        assert_matches_oracle(deep(2_000).as_bytes());
        let pair = format!("{{\"b\":{},\"a\":{}}}", deep(1_500), deep(1_500));
        assert_matches_oracle(pair.as_bytes());
        // Overwritten deep duplicates are dropped iteratively, and an overflow
        // buried in one still fails the row.
        for (body, error) in [
            (format!("[{{\"a\":{},\"a\":0}}]", deep(9_000)), None),
            (
                format!("[{{\"a\":{},\"a\":0}}]", deep_object(9_000)),
                Some("1e1000"),
            ),
            (
                format!("[{{\"a\":0,\"a\":{}}}]", deep_object(9_000)),
                Some("1e1000"),
            ),
        ] {
            let (out, result) = json(body.as_bytes());
            match error {
                None => {
                    result.unwrap();
                    assert_eq!(out, b"[\n  {\n    \"a\": 0\n  }\n]\n");
                }
                Some(number) => {
                    assert_eq!(out, b"[");
                    assert!(result.unwrap_err().to_string().contains(number));
                }
            }
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

    #[derive(Default)]
    struct RowWrites {
        output: Vec<u8>,
        lengths: Vec<usize>,
        flushes: usize,
        partial_then_fail: bool,
    }

    impl Write for RowWrites {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.lengths.push(bytes.len());
            let count = if self.partial_then_fail {
                match self.lengths.len() {
                    3 => 7.min(bytes.len()),
                    4 => return Err(io::Error::other("mid-row write fault")),
                    _ => bytes.len(),
                }
            } else {
                bytes.len()
            };
            self.output.extend_from_slice(&bytes[..count]);
            Ok(count)
        }

        fn flush(&mut self) -> io::Result<()> {
            self.flushes += 1;
            Ok(())
        }
    }

    #[test]
    fn chunked_renderer_keeps_small_row_writes_and_flushes() {
        let mut out = RowWrites::default();
        stream_json(&mut io::Cursor::new(b"[1,2]"), &mut out, "200 OK").unwrap();
        assert_eq!(out.output, b"[\n  1,\n  2\n]\n");
        assert_eq!(out.lengths, [1, 4, 5, 3]);
        assert_eq!(out.flushes, 4);
    }

    #[test]
    fn small_source_fast_path_bounds_indentation_strings_and_float_expansion() {
        let nested = format!("{}0{}", "[".repeat(255), "]".repeat(255));
        let numbers = format!("[{}]", vec!["1e20"; 100].join(","));
        let mut repaired = vec![b'"'];
        repaired.extend(std::iter::repeat_n(0xff, 510));
        repaired.push(b'"');
        for body in [
            nested.into_bytes(),
            numbers.into_bytes(),
            format!("\"{}\"", "<".repeat(510)).into_bytes(),
            repaired,
        ] {
            assert!(body.len() <= SMALL_ROW_SOURCE_BYTES);
            let row = oracle(&body, b"\n").unwrap();
            assert!(row.len() < ROW_CHUNK_BYTES);
            let mut input = vec![b'['];
            input.extend_from_slice(&body);
            input.push(b']');
            let mut out = RowWrites::default();
            stream_json(&mut io::Cursor::new(input), &mut out, "200 OK").unwrap();
            assert_eq!(out.lengths, [1, row.len(), 3]);
            assert_eq!(&out.output[1..out.output.len() - 3], row);
        }
    }

    #[test]
    fn large_escaping_and_duplicates_match_oracle_in_bounded_chunks() {
        let text = "<&>\u{2028}\u{2029}\"\\\n日本".repeat(70_000);
        let escaped = serde_json::to_string(&text).unwrap();
        let body = format!(r#"{{"z":0,"<&>":{escaped},"z":{{"b":2,"a":1}}}}"#);
        let expected = oracle(body.as_bytes(), b"\n").unwrap();
        assert!(expected.len() > ROW_CHUNK_BYTES * 2);
        let value = float_document(body.as_bytes()).unwrap();
        let mut buffer = Vec::new();
        let mut out = RowWrites::default();
        let mut row = RowBuffer::new(&mut buffer, &mut out);
        render_element(&value, &mut row, b"\n");
        assert_eq!(row.rendered_bytes, expected.len());
        assert_eq!(
            row.newlines,
            expected.iter().filter(|&&byte| byte == b'\n').count()
        );
        row.finish();
        assert_eq!(out.output, expected);
        assert!(out.lengths.len() > 2);
        assert!(out.lengths.iter().all(|&length| length <= ROW_CHUNK_BYTES));
        assert!(buffer.capacity() <= ROW_CHUNK_BYTES);
    }

    #[test]
    fn deeply_indented_small_source_also_uses_bounded_chunks() {
        // Pretty indentation alone exceeds the chunk size: source length
        // cannot safely choose between a bounded and an unbounded renderer.
        let body = format!("{}0{}", "[".repeat(1_100), "]".repeat(1_100));
        let expected = oracle(body.as_bytes(), b",\n").unwrap();
        assert!(expected.len() > ROW_CHUNK_BYTES * 2);
        let value = float_document(body.as_bytes()).unwrap();
        let mut buffer = Vec::new();
        let mut out = RowWrites::default();
        let mut row = RowBuffer::new(&mut buffer, &mut out);
        render_element(&value, &mut row, b",\n");
        row.finish();
        assert_eq!(out.output, expected);
        assert!(out.lengths.iter().all(|&length| length <= ROW_CHUNK_BYTES));
        assert!(buffer.capacity() <= ROW_CHUNK_BYTES);
    }

    #[test]
    fn late_overflow_in_large_row_emits_neither_separator_nor_prefix() {
        let text = "x".repeat(ROW_CHUNK_BYTES * 2);
        for row in [
            format!(r#"{{"a":"{text}","z":1e1000}}"#),
            // Even an overwritten invalid duplicate must fail before output.
            format!(r#"{{"a":"{text}","z":1e1000,"z":0}}"#),
        ] {
            let (out, result) = json(format!("[0,{row}]").as_bytes());
            assert_eq!(out, b"[\n  0");
            assert!(result.unwrap_err().to_string().contains("number 1e1000"));
        }
    }

    #[test]
    fn midrow_partial_write_error_discards_remainder_and_attempts_next_row() {
        let body = format!(r#"["{}",{{"next":true}}]"#, "x".repeat(ROW_CHUNK_BYTES * 3));
        let mut out = RowWrites {
            partial_then_fail: true,
            ..RowWrites::default()
        };
        stream_json(&mut io::Cursor::new(body), &mut out, "200 OK").unwrap();
        let next = b",\n  {\n    \"next\": true\n  }\n]\n";
        assert_eq!(&out.output[1 + ROW_CHUNK_BYTES + 7..], next);
        assert_eq!(out.lengths.len(), 6);
        assert_eq!(out.lengths[1], ROW_CHUNK_BYTES);
        assert_eq!(out.lengths[2], ROW_CHUNK_BYTES);
        assert_eq!(out.lengths[3], ROW_CHUNK_BYTES - 7);
        assert_eq!(out.flushes, 4);
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
