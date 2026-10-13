//! JSON decoding and presentation rules shared by native command handlers.
//! Syntax diagnostics follow encoding/json; numeric lexemes remain owned.
use serde_json::{Map, Number, Value};

#[derive(Debug)]
pub struct Document(Value);
impl Document {
    pub fn new(value: Value) -> Self {
        Self(value)
    }
    pub fn into_inner(mut self) -> Value {
        std::mem::take(&mut self.0)
    }
}
impl std::ops::Deref for Document {
    type Target = Value;
    fn deref(&self) -> &Value {
        &self.0
    }
}
impl std::ops::DerefMut for Document {
    fn deref_mut(&mut self) -> &mut Value {
        &mut self.0
    }
}
impl serde::Serialize for Document {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serialize_value(&self.0, serializer)
    }
}

/// Borrowed serialization protects each recursive Value edge, including map
/// entries whose serializer adapter does not perform its own stack check.
pub fn serialize_value<S: serde::Serializer>(
    value: &Value,
    serializer: S,
) -> Result<S::Ok, S::Error> {
    serde::Serialize::serialize(&SafeValue(value), serializer)
}
pub struct SafeValue<'a>(pub &'a Value);
impl serde::Serialize for SafeValue<'_> {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        stacker::maybe_grow(64 * 1024, 1024 * 1024, || {
            use serde::ser::{SerializeMap, SerializeSeq};
            match self.0 {
                Value::Array(values) => {
                    let mut sequence = serializer.serialize_seq(Some(values.len()))?;
                    for value in values {
                        sequence.serialize_element(&SafeValue(value))?;
                    }
                    sequence.end()
                }
                Value::Object(fields) => {
                    let mut map = serializer.serialize_map(Some(fields.len()))?;
                    for (key, value) in fields {
                        map.serialize_entry(key, &SafeValue(value))?;
                    }
                    map.end()
                }
                scalar => serde::Serialize::serialize(scalar, serializer),
            }
        })
    }
}
impl std::fmt::Display for Document {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&serde_json::to_string(self).map_err(|_| std::fmt::Error)?)
    }
}
impl PartialEq<Value> for Document {
    fn eq(&self, other: &Value) -> bool {
        &self.0 == other
    }
}
impl Drop for Document {
    fn drop(&mut self) {
        drop_value(std::mem::take(&mut self.0));
    }
}

#[derive(Debug)]
pub enum Node {
    Scalar(Value),
    Array(Vec<Node>),
    Object(Vec<(String, Node)>),
}
impl Node {
    pub fn as_object(&self) -> Option<&[(String, Node)]> {
        if let Self::Object(fields) = self {
            Some(fields)
        } else {
            None
        }
    }
    pub fn as_array(&self) -> Option<&[Node]> {
        if let Self::Array(values) = self {
            Some(values)
        } else {
            None
        }
    }
    pub fn scalar(&self) -> Option<&Value> {
        if let Self::Scalar(value) = self {
            Some(value)
        } else {
            None
        }
    }
    pub fn as_str(&self) -> Option<&str> {
        self.scalar().and_then(Value::as_str)
    }
    pub fn as_bool(&self) -> Option<bool> {
        self.scalar().and_then(Value::as_bool)
    }
    pub fn as_i64(&self) -> Option<i64> {
        self.scalar().and_then(Value::as_i64)
    }
    pub fn as_u64(&self) -> Option<u64> {
        self.scalar().and_then(Value::as_u64)
    }
    pub fn as_f64(&self) -> Option<f64> {
        self.scalar().and_then(Value::as_f64)
    }
    pub fn is_null(&self) -> bool {
        self.scalar().is_some_and(Value::is_null)
    }
    pub fn is_string(&self) -> bool {
        self.scalar().is_some_and(Value::is_string)
    }
    pub fn is_number(&self) -> bool {
        self.scalar().is_some_and(Value::is_number)
    }
    pub fn is_boolean(&self) -> bool {
        self.scalar().is_some_and(Value::is_boolean)
    }
    pub fn is_array(&self) -> bool {
        matches!(self, Self::Array(_))
    }
    pub fn is_object(&self) -> bool {
        matches!(self, Self::Object(_))
    }
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Array(_) => "array",
            Self::Object(_) => "object",
            Self::Scalar(Value::Null) => "null",
            Self::Scalar(Value::Bool(_)) => "bool",
            Self::Scalar(Value::Number(_)) => "number",
            Self::Scalar(Value::String(_)) => "string",
            Self::Scalar(Value::Array(_)) => "array",
            Self::Scalar(Value::Object(_)) => "object",
        }
    }
}
impl serde::Serialize for Node {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        // Protect recursive node traversal independently of the serializer's
        // container adapters, without nesting its generic wrapper at each node.
        stacker::maybe_grow(64 * 1024, 1024 * 1024, || self.serialize_inner(serializer))
    }
}
impl Node {
    fn serialize_inner<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::{SerializeMap, SerializeSeq};
        match self {
            Self::Scalar(value) => serialize_value(value, serializer),
            Self::Array(values) => {
                let mut sequence =
                    serde::Serializer::serialize_seq(serializer, Some(values.len()))?;
                for value in values {
                    sequence.serialize_element(value)?;
                }
                sequence.end()
            }
            Self::Object(fields) => {
                let mut map = serde::Serializer::serialize_map(serializer, Some(fields.len()))?;
                for (key, value) in fields {
                    map.serialize_entry(key, value)?;
                }
                map.end()
            }
        }
    }
}
impl std::fmt::Display for Node {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        struct Root<'a>(&'a Node);
        impl serde::Serialize for Root<'_> {
            fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
                self.0.serialize(serde_stacker::Serializer::new(serializer))
            }
        }
        f.write_str(&serde_json::to_string(&Root(self)).map_err(|_| std::fmt::Error)?)
    }
}
#[derive(Debug)]
pub struct OrderedDocument(Node);
impl OrderedDocument {
    pub fn new(node: Node) -> Self {
        Self(node)
    }
    pub fn into_inner(mut self) -> Node {
        std::mem::replace(&mut self.0, Node::Scalar(Value::Null))
    }
    pub fn into_map(self) -> Document {
        Document::new(node_to_value(self.into_inner()))
    }
}
impl std::ops::Deref for OrderedDocument {
    type Target = Node;
    fn deref(&self) -> &Node {
        &self.0
    }
}
impl std::ops::DerefMut for OrderedDocument {
    fn deref_mut(&mut self) -> &mut Node {
        &mut self.0
    }
}
impl serde::Serialize for OrderedDocument {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        self.0.serialize(serde_stacker::Serializer::new(serializer))
    }
}
impl Drop for OrderedDocument {
    fn drop(&mut self) {
        drop_node(std::mem::replace(&mut self.0, Node::Scalar(Value::Null)));
    }
}
pub fn drop_node(node: Node) {
    // Scalars have no recursive children. Reuse array storage as the worklist
    // instead of allocating a scratch vector for every leaf and empty guard.
    let mut pending = match node {
        Node::Scalar(value) => return drop_value(value),
        Node::Array(values) => values,
        Node::Object(fields) => fields.into_iter().map(|(_, value)| value).collect(),
    };
    while let Some(node) = pending.pop() {
        match node {
            Node::Scalar(value) => drop_value(value),
            Node::Array(values) => pending.extend(values),
            Node::Object(fields) => pending.extend(fields.into_iter().map(|(_, value)| value)),
        }
    }
}
fn node_to_value(node: Node) -> Value {
    stacker::maybe_grow(64 * 1024, 1024 * 1024, || match node {
        Node::Scalar(value) => value,
        Node::Array(nodes) => {
            let mut result = Document::new(Value::Array(Vec::new()));
            for node in nodes {
                let value = node_to_value(node);
                result.as_array_mut().unwrap().push(value);
            }
            result.into_inner()
        }
        Node::Object(fields) => {
            let mut result = Document::new(Value::Object(Map::new()));
            for (key, node) in fields {
                let value = node_to_value(node);
                if let Some(old) = result.as_object_mut().unwrap().insert(key, value) {
                    drop_value(old);
                }
            }
            result.into_inner()
        }
    })
}

pub fn drop_value(value: Value) {
    let mut pending = match value {
        Value::Array(values) => values,
        Value::Object(fields) => fields.into_values().collect(),
        _ => return,
    };
    while let Some(value) = pending.pop() {
        match value {
            Value::Array(values) => pending.extend(values),
            Value::Object(fields) => pending.extend(fields.into_values()),
            _ => (),
        }
    }
}

pub fn decode(body: &[u8]) -> Result<Document, String> {
    decode_ordered(body).map(OrderedDocument::into_map)
}
pub fn decode_ordered(body: &[u8]) -> Result<OrderedDocument, String> {
    let mut parser = Decoder {
        bytes: body,
        index: 0,
        depth: 0,
    };
    let value = OrderedDocument::new(parser.value()?);
    parser.space();
    if parser.index < body.len() {
        return Err(parser.invalid("after top-level value"));
    }
    Ok(value)
}

/// Retain original value fragments for RawMessage fields without serde's
/// stricter Unicode-string decoding. Callers validate the complete document.
pub fn raw_members(body: &str) -> Result<Vec<(String, &str)>, String> {
    let mut parser = Decoder {
        bytes: body.as_bytes(),
        index: 0,
        depth: 0,
    };
    if parser.peek()? != b'{' {
        return Err("expected JSON object".into());
    }
    parser.enter()?;
    let mut fields = Vec::new();
    if parser.peek()? == b'}' {
        return Ok(fields);
    }
    loop {
        if parser.peek()? != b'"' {
            return Err(parser.invalid("looking for beginning of object key string"));
        }
        let key = parser.string()?;
        if parser.peek()? != b':' {
            return Err(parser.invalid("after object key"));
        }
        parser.index += 1;
        parser.space();
        let start = parser.index;
        let node = parser.value()?;
        drop_node(node);
        fields.push((key, &body[start..parser.index]));
        match parser.peek()? {
            b'}' => return Ok(fields),
            b',' => parser.index += 1,
            _ => return Err(parser.invalid("after object key:value pair")),
        }
    }
}
pub fn raw_elements(body: &str) -> Result<Vec<&str>, String> {
    let mut parser = Decoder {
        bytes: body.as_bytes(),
        index: 0,
        depth: 0,
    };
    if parser.peek()? != b'[' {
        return Err("expected JSON array".into());
    }
    parser.enter()?;
    let mut values = Vec::new();
    if parser.peek()? == b']' {
        return Ok(values);
    }
    loop {
        parser.space();
        let start = parser.index;
        let node = parser.value()?;
        drop_node(node);
        values.push(&body[start..parser.index]);
        match parser.peek()? {
            b']' => return Ok(values),
            b',' => parser.index += 1,
            _ => return Err(parser.invalid("after array element")),
        }
    }
}

pub fn sorted(value: Value) -> Value {
    stacker::maybe_grow(64 * 1024, 1024 * 1024, || sorted_inner(value))
}
fn sorted_inner(value: Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut fields: Vec<_> = map.into_iter().collect();
            fields.sort_by(|a, b| a.0.cmp(&b.0));
            Value::Object(fields.into_iter().map(|(k, v)| (k, sorted(v))).collect())
        }
        Value::Array(items) => Value::Array(items.into_iter().map(sorted).collect()),
        other => other,
    }
}

pub fn compact(raw: &str) -> String {
    let mut output = String::with_capacity(raw.len());
    let (mut string, mut escape) = (false, false);
    for c in raw.chars() {
        if string {
            output.push(c);
            if escape {
                escape = false;
            } else if c == '\\' {
                escape = true;
            } else if c == '"' {
                string = false;
            }
        } else if c == '"' {
            string = true;
            output.push(c);
        } else if !matches!(c, ' ' | '\t' | '\r' | '\n') {
            output.push(c);
        }
    }
    output
}

struct Decoder<'a> {
    bytes: &'a [u8],
    index: usize,
    depth: usize,
}

/// encoding/json advances one byte when utf8.DecodeRune reports an invalid
/// encoding. Rust's lossy conversion groups some malformed prefixes into one
/// replacement, so append valid spans and advance only one byte per failure.
fn push_string_bytes(output: &mut String, mut bytes: &[u8]) {
    while !bytes.is_empty() {
        match std::str::from_utf8(bytes) {
            Ok(valid) => {
                output.push_str(valid);
                return;
            }
            Err(error) => {
                let valid = error.valid_up_to();
                output.push_str(std::str::from_utf8(&bytes[..valid]).expect("validated span"));
                output.push('\u{fffd}');
                bytes = &bytes[valid + 1..];
            }
        }
    }
}

impl Decoder<'_> {
    fn space(&mut self) {
        while self
            .bytes
            .get(self.index)
            .is_some_and(|b| matches!(b, b' ' | b'\t' | b'\r' | b'\n'))
        {
            self.index += 1;
        }
    }
    fn peek(&mut self) -> Result<u8, String> {
        self.space();
        self.bytes
            .get(self.index)
            .copied()
            .ok_or_else(|| "unexpected end of JSON input".into())
    }
    fn invalid(&self, context: &str) -> String {
        match self.bytes.get(self.index) {
            Some(byte) => format!("invalid character {} {context}", rune_quote(*byte)),
            None => "unexpected end of JSON input".into(),
        }
    }
    fn value(&mut self) -> Result<Node, String> {
        stacker::maybe_grow(64 * 1024, 1024 * 1024, || self.value_inner())
    }
    fn value_inner(&mut self) -> Result<Node, String> {
        match self.peek()? {
            b'{' => self.object(),
            b'[' => self.array(),
            b'"' => self.string().map(|s| Node::Scalar(Value::String(s))),
            b't' => self.literal(b"true", Value::Bool(true)),
            b'f' => self.literal(b"false", Value::Bool(false)),
            b'n' => self.literal(b"null", Value::Null),
            b'-' | b'0'..=b'9' => self.number(),
            _ => Err(self.invalid("looking for beginning of value")),
        }
    }
    fn enter(&mut self) -> Result<(), String> {
        self.depth += 1;
        if self.depth > 10_000 {
            return Err(self.invalid("exceeded max depth"));
        }
        self.index += 1;
        Ok(())
    }
    fn object(&mut self) -> Result<Node, String> {
        self.enter()?;
        let mut document = OrderedDocument::new(Node::Object(Vec::new()));
        if self.peek()? == b'}' {
            self.index += 1;
            self.depth -= 1;
            return Ok(document.into_inner());
        }
        loop {
            if self.peek()? != b'"' {
                return Err(self.invalid("looking for beginning of object key string"));
            }
            let key = self.string()?;
            if self.peek()? != b':' {
                return Err(self.invalid("after object key"));
            }
            self.index += 1;
            let value = self.value()?;
            if let Node::Object(fields) = &mut document.0 {
                fields.push((key, value));
            }
            match self.peek()? {
                b'}' => {
                    self.index += 1;
                    self.depth -= 1;
                    return Ok(document.into_inner());
                }
                b',' => {
                    self.index += 1;
                }
                _ => return Err(self.invalid("after object key:value pair")),
            }
        }
    }
    fn array(&mut self) -> Result<Node, String> {
        self.enter()?;
        let mut document = OrderedDocument::new(Node::Array(Vec::new()));
        if self.peek()? == b']' {
            self.index += 1;
            self.depth -= 1;
            return Ok(document.into_inner());
        }
        loop {
            let value = self.value()?;
            if let Node::Array(values) = &mut document.0 {
                values.push(value);
            }
            match self.peek()? {
                b']' => {
                    self.index += 1;
                    self.depth -= 1;
                    return Ok(document.into_inner());
                }
                b',' => {
                    self.index += 1;
                }
                _ => return Err(self.invalid("after array element")),
            }
        }
    }
    fn literal(&mut self, literal: &[u8], value: Value) -> Result<Node, String> {
        for expected in literal {
            match self.bytes.get(self.index) {
                Some(actual) if actual == expected => self.index += 1,
                _ => {
                    return Err(self.invalid(&format!(
                        "in literal {} (expecting {})",
                        String::from_utf8_lossy(literal),
                        rune_quote(*expected)
                    )));
                }
            }
        }
        Ok(Node::Scalar(value))
    }
    fn string(&mut self) -> Result<String, String> {
        self.index += 1;
        let mut output = String::new();
        let mut start = self.index;
        loop {
            let byte = *self
                .bytes
                .get(self.index)
                .ok_or_else(|| "unexpected end of JSON input".to_string())?;
            if byte == b'"' {
                push_string_bytes(&mut output, &self.bytes[start..self.index]);
                self.index += 1;
                return Ok(output);
            }
            if byte < b' ' {
                return Err(self.invalid("in string literal"));
            }
            if byte != b'\\' {
                self.index += 1;
                continue;
            }
            push_string_bytes(&mut output, &self.bytes[start..self.index]);
            self.index += 1;
            let escaped = *self
                .bytes
                .get(self.index)
                .ok_or_else(|| "unexpected end of JSON input".to_string())?;
            self.index += 1;
            match escaped {
                b'"' => output.push('"'),
                b'\\' => output.push('\\'),
                b'/' => output.push('/'),
                b'b' => output.push('\u{8}'),
                b'f' => output.push('\u{c}'),
                b'n' => output.push('\n'),
                b'r' => output.push('\r'),
                b't' => output.push('\t'),
                b'u' => {
                    let first = self.hex()?;
                    let point = if (0xd800..=0xdbff).contains(&first) {
                        if self.bytes.get(self.index..self.index + 2) == Some(b"\\u") {
                            let next_position = self.index;
                            self.index += 2;
                            let second = self.hex()?;
                            if (0xdc00..=0xdfff).contains(&second) {
                                0x10000 + ((first - 0xd800) << 10) + second - 0xdc00
                            } else {
                                self.index = next_position;
                                0xfffd
                            }
                        } else {
                            0xfffd
                        }
                    } else if (0xdc00..=0xdfff).contains(&first) {
                        0xfffd
                    } else {
                        first
                    };
                    output.push(char::from_u32(point).unwrap_or('\u{fffd}'));
                }
                _ => {
                    self.index -= 1;
                    return Err(self.invalid("in string escape code"));
                }
            }
            start = self.index;
        }
    }
    fn hex(&mut self) -> Result<u32, String> {
        let mut value = 0;
        for _ in 0..4 {
            let byte = *self
                .bytes
                .get(self.index)
                .ok_or_else(|| "unexpected end of JSON input".to_string())?;
            let digit = char::from(byte)
                .to_digit(16)
                .ok_or_else(|| self.invalid("in \\u hexadecimal character escape"))?;
            value = value * 16 + digit;
            self.index += 1;
        }
        Ok(value)
    }
    fn number(&mut self) -> Result<Node, String> {
        let start = self.index;
        if self.bytes[self.index] == b'-' {
            self.index += 1;
        }
        let byte = *self
            .bytes
            .get(self.index)
            .ok_or_else(|| "unexpected end of JSON input".to_string())?;
        match byte {
            b'0' => self.index += 1,
            b'1'..=b'9' => {
                while self.bytes.get(self.index).is_some_and(u8::is_ascii_digit) {
                    self.index += 1;
                }
            }
            _ => return Err(self.invalid("in numeric literal")),
        }
        if self.bytes.get(self.index) == Some(&b'.') {
            self.index += 1;
            if !self.bytes.get(self.index).is_some_and(u8::is_ascii_digit) {
                return Err(self.invalid("after decimal point in numeric literal"));
            }
            while self.bytes.get(self.index).is_some_and(u8::is_ascii_digit) {
                self.index += 1;
            }
        }
        if self
            .bytes
            .get(self.index)
            .is_some_and(|b| matches!(b, b'e' | b'E'))
        {
            self.index += 1;
            if self
                .bytes
                .get(self.index)
                .is_some_and(|b| matches!(b, b'+' | b'-'))
            {
                self.index += 1;
            }
            if !self.bytes.get(self.index).is_some_and(u8::is_ascii_digit) {
                return Err(self.invalid("in exponent of numeric literal"));
            }
            while self.bytes.get(self.index).is_some_and(u8::is_ascii_digit) {
                self.index += 1;
            }
        }
        let raw = std::str::from_utf8(&self.bytes[start..self.index]).expect("ASCII number");
        // The grammar above validates the token. Parsing through Number's
        // FromStr would normalize `1e3` to `1e+3`, changing Go UseNumber output.
        Ok(Node::Scalar(Value::Number(Number::from_string_unchecked(
            raw.to_owned(),
        ))))
    }
}

fn rune_quote(byte: u8) -> String {
    let c = char::from(byte);
    if c == '\'' {
        return "'\\''".into();
    }
    let quoted = crate::parser::quote(&c.to_string());
    format!("'{}'", quoted[1..quoted.len() - 1].replace("\\\"", "\""))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn syntax_and_raw_numbers() {
        assert_eq!(
            decode(b"<html>").unwrap_err(),
            "invalid character '<' looking for beginning of value"
        );
        assert_eq!(
            decode(b"{\"a\":}").unwrap_err(),
            "invalid character '}' looking for beginning of value"
        );
        assert_eq!(decode(b"[1").unwrap_err(), "unexpected end of JSON input");
        assert_eq!(
            decode(b"{} {}").unwrap_err(),
            "invalid character '{' after top-level value"
        );
        assert_eq!(
            decode(b"[1.2300e+04,18446744073709551615]")
                .unwrap()
                .to_string(),
            "[1.2300e+04,18446744073709551615]"
        );
    }
    #[test]
    fn numeric_tokens_keep_exponent_spelling() {
        let raw = b"[1.50e3,1.50E03,1e+03,-0,0e000]";
        assert_eq!(
            decode(raw).unwrap().to_string(),
            std::str::from_utf8(raw).unwrap()
        );
    }
    #[test]
    fn replacement_and_compaction() {
        assert_eq!(
            decode(br#""\ud800\u0041""#).unwrap(),
            Value::String("\u{fffd}A".into())
        );
        assert_eq!(
            decode(b"\"\xff\"").unwrap(),
            Value::String("\u{fffd}".into())
        );
        assert_eq!(
            compact(r#"{ "x": "\u0061", "n": 1.00e+2 }"#),
            r#"{"x":"\u0061","n":1.00e+2}"#
        );
    }
    #[test]
    fn invalid_utf8_replaces_each_byte_in_strings_and_keys() {
        for (bytes, expected) in [
            (&b"\xe2\x82"[..], "\u{fffd}\u{fffd}"),
            (&b"\xf0\x9f\x92"[..], "\u{fffd}\u{fffd}\u{fffd}"),
            (&b"\xe2\x82A"[..], "\u{fffd}\u{fffd}A"),
            (&b"\xc0\xaf"[..], "\u{fffd}\u{fffd}"),
            (&b"\xed\xa0\x80"[..], "\u{fffd}\u{fffd}\u{fffd}"),
            (&b"\xf4\x90\x80\x80"[..], "\u{fffd}\u{fffd}\u{fffd}\u{fffd}"),
            (&b"a\xe2\x82A\x80z"[..], "a\u{fffd}\u{fffd}A\u{fffd}z"),
        ] {
            let mut body = b"{\"".to_vec();
            body.extend_from_slice(bytes);
            body.extend_from_slice(b"\":\"");
            body.extend_from_slice(bytes);
            body.extend_from_slice(b"\"}");
            let ordered = decode_ordered(&body).unwrap();
            let fields = ordered.as_object().unwrap();
            assert_eq!(fields[0].0, expected, "{bytes:x?}");
            assert_eq!(fields[0].1.as_str(), Some(expected), "{bytes:x?}");
            assert_eq!(decode(&body).unwrap()[expected], expected);
        }
    }
    #[test]
    fn repaired_keys_keep_go_duplicate_semantics() {
        let body = b"{\"\xe2\":1,\"\xe2\x82\":2,\"\xef\xbf\xbd\xef\xbf\xbd\":3}";
        let ordered = decode_ordered(body).unwrap();
        let fields = ordered.as_object().unwrap();
        assert_eq!(fields.len(), 3);
        assert_eq!(fields[0].0, "\u{fffd}");
        assert_eq!(fields[1].0, "\u{fffd}\u{fffd}");
        assert_eq!(fields[2].0, "\u{fffd}\u{fffd}");
        let mapped = decode(body).unwrap();
        assert_eq!(mapped.as_object().unwrap().len(), 2);
        assert_eq!(mapped["\u{fffd}"], 1);
        assert_eq!(mapped["\u{fffd}\u{fffd}"], 3);
    }
    #[test]
    fn multibyte_prefixes_and_complete_utf8_have_distinct_replacement_rules() {
        for text in ["¢", "€", "😀", "\u{10ffff}", "\u{fffd}"] {
            for length in 1..=text.len() {
                let mut body = b"\"".to_vec();
                body.extend_from_slice(&text.as_bytes()[..length]);
                body.push(b'"');
                let expected = if length == text.len() {
                    text.to_owned()
                } else {
                    "\u{fffd}".repeat(length)
                };
                assert_eq!(decode(&body).unwrap(), Value::String(expected));
            }
        }
        // Network fragments are assembled before decoding a complete element;
        // a split inside a valid rune must not cause premature replacement.
        let body = [&b"\"\xe2"[..], &b"\x82"[..], &b"\xac\""[..]].concat();
        assert_eq!(decode(&body).unwrap(), Value::String("€".into()));
        assert_eq!(
            decode(b"\"\xe2\x82").unwrap_err(),
            "unexpected end of JSON input"
        );
    }
    #[test]
    fn raw_utf8_repair_preserves_escaped_surrogate_handling() {
        assert_eq!(
            decode(b"\"\xe2\x82\\ud800\\udc00\xe2\\ud800\"").unwrap(),
            Value::String("\u{fffd}\u{fffd}\u{10000}\u{fffd}\u{fffd}".into())
        );
        assert_eq!(
            decode("\"éπ€😀�\\ud800\\udc00\\ud800\\udfff\"".as_bytes()).unwrap(),
            Value::String("éπ€😀�\u{10000}\u{103ff}".into())
        );
        assert_eq!(
            decode(br#""\ud800\ud800\udc00\udfff""#).unwrap(),
            Value::String("\u{fffd}\u{10000}\u{fffd}".into())
        );
    }
    #[test]
    fn deep_documents_serialize_replace_and_drop_without_native_recursion_overflow() {
        for (open, close) in [("[", "]"), ("{\"x\":", "}")] {
            let nested = format!("{}0{}", open.repeat(10_000), close.repeat(10_000));
            let document = decode(nested.as_bytes()).unwrap();
            assert_eq!(document.to_string(), nested);
            drop(document);
        }
        let nested = format!("{}0{}", "[".repeat(9_999), "]".repeat(9_999));
        let duplicate = format!("{{\"x\":{nested},\"x\":0}}");
        assert_eq!(
            decode(duplicate.as_bytes()).unwrap().to_string(),
            "{\"x\":0}"
        );
        let malformed = format!("[{nested},]");
        assert!(decode(malformed.as_bytes()).is_err());
        let too_deep = format!("{}0{}", "[".repeat(10_001), "]".repeat(10_001));
        assert_eq!(
            decode(too_deep.as_bytes()).unwrap_err(),
            "invalid character '[' exceeded max depth"
        );
    }
    #[test]
    fn ordered_members_and_map_mode_have_distinct_duplicate_semantics() {
        let body = br#"{"version":7,"Version":"middle","version":"last"}"#;
        let ordered = decode_ordered(body).unwrap();
        let members = ordered.as_object().unwrap();
        assert_eq!(members.len(), 3);
        assert_eq!(members[0].1.as_i64(), Some(7));
        assert_eq!(members[2].1.as_str(), Some("last"));
        assert_eq!(
            serde_json::to_string(&ordered).unwrap(),
            std::str::from_utf8(body).unwrap()
        );
        let map = decode(body).unwrap();
        assert_eq!(map["version"], "last");
        assert_eq!(map["Version"], "middle");
    }
    #[test]
    fn deep_ordered_documents_serialize_and_drop_safely() {
        for (open, close) in [("[", "]"), ("{\"x\":", "}")] {
            let body = format!("{}0{}", open.repeat(10_000), close.repeat(10_000));
            let document = decode_ordered(body.as_bytes()).unwrap();
            assert_eq!(serde_json::to_string(&document).unwrap(), body);
            assert_eq!(document.to_string(), body);
            drop(document);
        }
    }
}
