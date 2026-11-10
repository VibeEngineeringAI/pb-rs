//! Pretty JSON for PromQL responses, written in one pass over the body.
//!
//! The pass validates Go's JSON grammar, writes cmd.writeRawJSON's output
//! (sorted keys, last duplicate member, verbatim number lexemes, Go string
//! decoding followed by its non-HTML escaping) and collects the request
//! layer's error-envelope fields, so no byte of a sample is scanned twice.
//! Object members are written in input order. A key that sorts before a few
//! small members already written has those set aside and appended again after
//! its own value, so the usual `status`-before-`data` envelope never moves
//! the result; any other unsorted or repeated key has the object's rendered
//! members permuted when it closes. Nesting is tracked on an explicit stack,
//! so the depth limit needs no native recursion.
use crate::gojson;

/// encoding/json's nesting limit, as enforced by `gojson::validate`.
const MAX_DEPTH: usize = 10_000;

/// Short tokens and indentation are appended as one fixed-size block that is
/// then cut to length, which compiles to a few register moves instead of a
/// `memcpy` call per token.
const BLOCK: usize = 32;
/// Nesting whose line break and indentation fit one block of this size.
const INDENT_BLOCK: usize = 64;
const INDENT_LEVELS: usize = (INDENT_BLOCK - 2) / 2;
const NEWLINE: [u8; INDENT_BLOCK] = {
    let mut bytes = [b' '; INDENT_BLOCK];
    bytes[0] = b'\n';
    bytes
};
const COMMA_NEWLINE: [u8; INDENT_BLOCK] = {
    let mut bytes = [b' '; INDENT_BLOCK];
    bytes[0] = b',';
    bytes[1] = b'\n';
    bytes
};
const OPEN_NEWLINE: [u8; INDENT_BLOCK] = {
    let mut bytes = COMMA_NEWLINE;
    bytes[0] = b'[';
    bytes
};

/// Most rendered bytes that may be set aside for one out-of-order key.
const HOLD_LIMIT: usize = 1024;

/// String bytes that are their own decoding and their own encoding: printable
/// ASCII other than the quote and the backslash.
const PLAIN: [bool; 256] = {
    let mut table = [false; 256];
    let mut byte = 0x20;
    while byte < 0x80 {
        table[byte] = byte != b'"' as usize && byte != b'\\' as usize;
        byte += 1;
    }
    table
};

/// serde_json's string escapes: 0 for bytes written as they are, `u` for
/// control bytes without a short form, otherwise the short form's letter.
const ESCAPE: [u8; 256] = {
    let mut table = [0; 256];
    let mut byte = 0;
    while byte < 0x20 {
        table[byte] = b'u';
        byte += 1;
    }
    table[0x08] = b'b';
    table[0x09] = b't';
    table[0x0a] = b'n';
    table[0x0c] = b'f';
    table[0x0d] = b'r';
    table[b'"' as usize] = b'"';
    table[b'\\' as usize] = b'\\';
    table
};

/// A body that is exactly one JSON value.
pub(super) struct Rendered {
    /// Two-space indented output with a trailing newline.
    pub(super) output: Vec<u8>,
    /// The request layer's `status: "error"` envelope message, if the body
    /// carries one. Go checks it before printing, so it outranks `output`.
    pub(super) envelope_error: Option<String>,
}

/// What one pass over a body came to.
pub(super) enum Pass {
    Rendered(Rendered),
    /// Not a single valid JSON value; the caller derives the diagnostic from
    /// `gojson::validate`, which rejects exactly those bodies.
    Invalid,
    /// Abandoned, with the body's validity undecided: putting members in key
    /// order was moving far more output than the body produces. The caller
    /// renders such a body with the [`former`] pipeline instead.
    Costly,
}

/// Reordering may move this many times the output written so far, plus
/// `REORDER_SLACK`, before the pass gives up.
///
/// An object out of key order moves its largest member once when it closes,
/// so objects nested `n` deep that are all out of order move the innermost
/// one `n` times. Responses have a handful of levels: an envelope, its data,
/// a series, its labels. A body built to nest thousands of unsorted objects
/// would otherwise cost time cubic in its depth, because indentation makes
/// each level's rendering proportional to the depth as well.
const REORDER_FACTOR: usize = 8;
/// Small bodies are never worth abandoning.
const REORDER_SLACK: usize = 64 * 1024;

pub(super) fn render(body: &[u8]) -> Pass {
    render_within(body, REORDER_FACTOR)
}

/// The single pass alone, however much reordering the body takes.
#[cfg(test)]
fn render_unbounded(body: &[u8]) -> Pass {
    render_within(body, usize::MAX)
}

fn render_within(body: &[u8], reorder_factor: usize) -> Pass {
    // Compact sample arrays grow about threefold when indented. Capacity
    // that is never written costs no memory, and a refused estimate only
    // means the output grows as it is written.
    let mut out = Vec::new();
    let _ = out.try_reserve_exact(body.len().saturating_mul(13) / 4 + 16);
    let mut writer = Writer {
        body,
        out,
        open: Vec::new(),
        objects: Vec::new(),
        members: Vec::new(),
        keys: Vec::new(),
        text: String::new(),
        held: Vec::new(),
        moved: Vec::new(),
        order: Vec::new(),
        reordered: 0,
        reorder_factor,
        costly: false,
        envelope: Envelope::default(),
    };
    if writer.document().is_none() {
        return if writer.costly {
            Pass::Costly
        } else {
            Pass::Invalid
        };
    }
    let envelope_error = writer.envelope.message();
    Pass::Rendered(Rendered {
        output: writer.out,
        envelope_error,
    })
}

/// An open object.
struct Object {
    /// The members written so far are in increasing key order.
    sorted: bool,
    /// Lengths of `members` and `keys` when the object opened.
    members: usize,
    keys: usize,
    /// Members set aside until the current member's value ends: their count
    /// and where their rendered bytes start in `Writer::held`.
    held_members: usize,
    held: usize,
}

/// One rendered member of an open object.
struct Member {
    /// The decoded key: a span of the body when its bytes need no decoding,
    /// otherwise a span of `Writer::keys`.
    key: usize,
    key_len: usize,
    decoded: bool,
    /// Output span from the member's newline to the end of its value,
    /// excluding the separating comma.
    start: usize,
    end: usize,
}

/// Fields of Go's request-layer envelope struct, matched like encoding/json:
/// case-insensitive keys, null leaves a field alone, the last value wins and
/// any other type fails the decode, which disables the check.
#[derive(Default)]
struct Envelope {
    fields: [String; 3],
    mismatch: bool,
}

impl Envelope {
    const NAMES: [&'static str; 3] = ["status", "error", "errortype"];

    fn field(key: &[u8]) -> Option<usize> {
        let key = std::str::from_utf8(key).ok()?;
        Self::NAMES.iter().position(|name| {
            key.chars()
                .map(|c| match c {
                    '\u{17f}' => 's',
                    '\u{212a}' => 'k',
                    c => c.to_ascii_lowercase(),
                })
                .eq(name.chars())
        })
    }

    fn message(&self) -> Option<String> {
        let [status, error, kind] = &self.fields;
        (!self.mismatch && status == "error").then(|| {
            if kind.is_empty() {
                format!("PromQL error: {error}")
            } else {
                format!("PromQL error ({kind}): {error}")
            }
        })
    }
}

struct Writer<'a> {
    body: &'a [u8],
    out: Vec<u8>,
    /// Open containers, outermost first: true for an object, which also has
    /// an entry in `objects`.
    open: Vec<bool>,
    objects: Vec<Object>,
    /// Members of every open object, innermost last.
    members: Vec<Member>,
    /// Decoded keys of open objects that differ from their input bytes.
    keys: Vec<u8>,
    /// The string being decoded.
    text: String,
    /// Rendered members set aside by open objects, innermost last.
    held: Vec<u8>,
    /// Members displaced while a closing object is put in key order.
    moved: Vec<u8>,
    order: Vec<usize>,
    /// Output bytes moved by `reorder` so far, and how many times the output
    /// they may come to before `document` stops with `costly` set.
    reordered: usize,
    reorder_factor: usize,
    costly: bool,
    envelope: Envelope,
}

fn is_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\r' | b'\n')
}

/// Offset of the first non-space byte at or after `index`; `None` at the end.
#[inline]
fn skip_space(body: &[u8], mut index: usize) -> Option<(usize, u8)> {
    loop {
        let byte = *body.get(index)?;
        if !is_space(byte) {
            return Some((index, byte));
        }
        index += 1;
    }
}

/// Offset after the digits at `index`, which may be the end of the body.
#[inline]
fn digits(body: &[u8], mut index: usize) -> usize {
    while body.get(index).is_some_and(u8::is_ascii_digit) {
        index += 1;
    }
    index
}

/// Offset of the first byte at or after `index` that is not plain; `None`
/// when the body ends inside the string.
#[inline]
fn plain_end(body: &[u8], mut index: usize) -> Option<usize> {
    while PLAIN[usize::from(*body.get(index)?)] {
        index += 1;
    }
    Some(index)
}

/// End of the number starting at `index`, following encoding/json's scanner:
/// `0123` is the number `0` followed by something else.
#[inline]
fn number_end(body: &[u8], mut index: usize) -> Option<usize> {
    if body[index] == b'-' {
        index += 1;
    }
    index = match *body.get(index)? {
        b'0' => index + 1,
        b'1'..=b'9' => digits(body, index + 1),
        _ => return None,
    };
    if body.get(index) == Some(&b'.') {
        if !body.get(index + 1)?.is_ascii_digit() {
            return None;
        }
        index = digits(body, index + 2);
    }
    if matches!(body.get(index), Some(b'e' | b'E')) {
        index += 1;
        if matches!(body.get(index), Some(b'+' | b'-')) {
            index += 1;
        }
        if !body.get(index)?.is_ascii_digit() {
            return None;
        }
        index = digits(body, index + 1);
    }
    Some(index)
}

fn hex(body: &[u8], index: usize) -> Option<u32> {
    let mut value = 0;
    for byte in body.get(index..index + 4)? {
        value = value * 16 + char::from(*byte).to_digit(16)?;
    }
    Some(value)
}

/// Append `body[start..end]`.
#[inline(always)]
fn copy(out: &mut Vec<u8>, body: &[u8], start: usize, end: usize) {
    if end - start <= BLOCK
        && let Some(block) = body[start..].first_chunk::<BLOCK>()
    {
        let length = out.len() + (end - start);
        out.extend_from_slice(block);
        out.truncate(length);
    } else {
        out.extend_from_slice(&body[start..end]);
    }
}

#[inline(always)]
fn newline(out: &mut Vec<u8>, depth: usize) {
    if depth <= INDENT_LEVELS {
        let length = out.len() + 1 + 2 * depth;
        out.extend_from_slice(&NEWLINE);
        out.truncate(length);
    } else {
        out.push(b'\n');
        indent(out, depth);
    }
}

/// Append the first byte of `lead`, a line break and `depth` levels.
#[inline(always)]
fn line(out: &mut Vec<u8>, lead: &[u8; INDENT_BLOCK], depth: usize) {
    if depth <= INDENT_LEVELS {
        let length = out.len() + 2 + 2 * depth;
        out.extend_from_slice(lead);
        out.truncate(length);
    } else {
        out.extend_from_slice(&lead[..2]);
        indent(out, depth);
    }
}

#[cold]
fn indent(out: &mut Vec<u8>, depth: usize) {
    out.resize(out.len() + 2 * depth, b' ');
}

/// A JSON string literal for already decoded text: serde_json's escapes, plus
/// U+2028 and U+2029 as Go's encoder writes them even without HTML escaping.
fn quote(out: &mut Vec<u8>, text: &[u8]) {
    out.push(b'"');
    let mut start = 0;
    let mut index = 0;
    while index < text.len() {
        let byte = text[index];
        if byte == 0xe2 {
            if text.get(index + 1) == Some(&0x80)
                && let Some(last @ (0xa8 | 0xa9)) = text.get(index + 2)
            {
                out.extend_from_slice(&text[start..index]);
                out.extend_from_slice(if *last == 0xa8 {
                    b"\\u2028"
                } else {
                    b"\\u2029"
                });
                index += 3;
                start = index;
            } else {
                index += 1;
            }
            continue;
        }
        let escape = ESCAPE[usize::from(byte)];
        if escape != 0 {
            out.extend_from_slice(&text[start..index]);
            if escape == b'u' {
                const HEX: &[u8; 16] = b"0123456789abcdef";
                out.extend_from_slice(&[
                    b'\\',
                    b'u',
                    b'0',
                    b'0',
                    HEX[usize::from(byte >> 4)],
                    HEX[usize::from(byte & 0xf)],
                ]);
            } else {
                out.extend_from_slice(&[b'\\', escape]);
            }
            start = index + 1;
        }
        index += 1;
    }
    out.extend_from_slice(&text[start..]);
    out.push(b'"');
}

impl Writer<'_> {
    fn document(&mut self) -> Option<()> {
        let body = self.body;
        let mut index = 0;
        'value: loop {
            let (at, byte) = skip_space(body, index)?;
            index = at;
            match byte {
                b'"' => index = self.string(index + 1)?,
                b'-' | b'0'..=b'9' => {
                    let end = number_end(body, index)?;
                    copy(&mut self.out, body, index, end);
                    index = end;
                }
                b'[' => {
                    if self.open.len() == MAX_DEPTH {
                        return None;
                    }
                    let (at, byte) = skip_space(body, index + 1)?;
                    index = at;
                    if byte == b']' {
                        index += 1;
                        self.out.extend_from_slice(b"[]");
                    } else {
                        self.open.push(false);
                        line(&mut self.out, &OPEN_NEWLINE, self.open.len());
                        continue 'value;
                    }
                }
                b'{' => {
                    if self.open.len() == MAX_DEPTH {
                        return None;
                    }
                    let (at, byte) = skip_space(body, index + 1)?;
                    index = at;
                    match byte {
                        b'}' => {
                            index += 1;
                            self.out.extend_from_slice(b"{}");
                        }
                        b'"' => {
                            self.open.push(true);
                            self.objects.push(Object {
                                sorted: true,
                                members: self.members.len(),
                                keys: self.keys.len(),
                                held_members: 0,
                                held: 0,
                            });
                            self.out.push(b'{');
                            index = self.member(index)?;
                            continue 'value;
                        }
                        _ => return None,
                    }
                }
                b't' => index = self.literal(index, b"true")?,
                b'f' => index = self.literal(index, b"false")?,
                b'n' => index = self.literal(index, b"null")?,
                _ => return None,
            }
            // A value has just ended; close or continue its containers.
            loop {
                let Some(object) = self.open.last() else {
                    break 'value;
                };
                let (at, byte) = skip_space(body, index)?;
                index = at + 1;
                if !object {
                    match byte {
                        b',' => {
                            line(&mut self.out, &COMMA_NEWLINE, self.open.len());
                            continue 'value;
                        }
                        b']' => {
                            self.open.pop();
                            newline(&mut self.out, self.open.len());
                            self.out.push(b']');
                        }
                        _ => return None,
                    }
                } else {
                    self.members.last_mut().expect("open object member").end = self.out.len();
                    if self.objects.last().expect("open object").held_members != 0 {
                        self.release();
                    }
                    match byte {
                        b',' => {
                            let (at, byte) = skip_space(body, index)?;
                            if byte != b'"' {
                                return None;
                            }
                            self.out.push(b',');
                            index = self.member(at)?;
                            continue 'value;
                        }
                        b'}' => {
                            self.open.pop();
                            let object = self.objects.pop().expect("open object");
                            if !object.sorted {
                                self.reorder(object.members);
                                let allowed = self
                                    .out
                                    .len()
                                    .saturating_mul(self.reorder_factor)
                                    .saturating_add(REORDER_SLACK);
                                if self.reordered > allowed {
                                    self.costly = true;
                                    return None;
                                }
                            }
                            self.members.truncate(object.members);
                            self.keys.truncate(object.keys);
                            newline(&mut self.out, self.open.len());
                            self.out.push(b'}');
                        }
                        _ => return None,
                    }
                }
            }
        }
        if skip_space(body, index).is_some() {
            return None;
        }
        self.out.push(b'\n');
        Some(())
    }

    #[inline]
    fn literal(&mut self, index: usize, literal: &[u8]) -> Option<usize> {
        let end = index + literal.len();
        if self.body.get(index..end)? != literal {
            return None;
        }
        self.out.extend_from_slice(literal);
        Some(end)
    }

    /// Write the string whose content starts at `start`; returns the offset
    /// after its closing quote.
    #[inline]
    fn string(&mut self, start: usize) -> Option<usize> {
        let body = self.body;
        let end = plain_end(body, start)?;
        if body[end] == b'"' {
            copy(&mut self.out, body, start - 1, end + 1);
            return Some(end + 1);
        }
        self.string_slow(start, end)
    }

    /// Strings with escapes, non-ASCII bytes or errors. `index` is the first
    /// byte that is not plain.
    #[cold]
    fn string_slow(&mut self, start: usize, mut index: usize) -> Option<usize> {
        let body = self.body;
        // Without a backslash, valid UTF-8 decodes to itself and only the
        // line separators need escaping.
        loop {
            let byte = *body.get(index)?;
            if byte == b'"' {
                if let Ok(text) = std::str::from_utf8(&body[start..index]) {
                    quote(&mut self.out, text.as_bytes());
                    return Some(index + 1);
                }
                break;
            }
            if byte == b'\\' {
                break;
            }
            if byte < b' ' {
                return None;
            }
            index += 1;
        }
        let end = self.decode(start)?;
        quote(&mut self.out, self.text.as_bytes());
        Some(end)
    }

    /// Decode the string whose content starts at `start` into `self.text` as
    /// encoding/json does: one U+FFFD per invalid UTF-8 byte and per unpaired
    /// surrogate escape. Returns the offset after the closing quote.
    fn decode(&mut self, start: usize) -> Option<usize> {
        let body = self.body;
        let text = &mut self.text;
        text.clear();
        let (mut run, mut index) = (start, start);
        loop {
            let byte = *body.get(index)?;
            if byte == b'"' {
                gojson::push_string_bytes(text, &body[run..index]);
                return Some(index + 1);
            }
            if byte < b' ' {
                return None;
            }
            if byte != b'\\' {
                index += 1;
                continue;
            }
            gojson::push_string_bytes(text, &body[run..index]);
            let escaped = *body.get(index + 1)?;
            index += 2;
            match escaped {
                b'"' => text.push('"'),
                b'\\' => text.push('\\'),
                b'/' => text.push('/'),
                b'b' => text.push('\u{8}'),
                b'f' => text.push('\u{c}'),
                b'n' => text.push('\n'),
                b'r' => text.push('\r'),
                b't' => text.push('\t'),
                b'u' => {
                    let first = hex(body, index)?;
                    index += 4;
                    let point = if (0xd800..=0xdbff).contains(&first) {
                        if body.get(index..index + 2) == Some(b"\\u") {
                            // A malformed second escape is an error here; a
                            // well-formed one that is not a low surrogate is
                            // decoded again on the next iteration.
                            let second = hex(body, index + 2)?;
                            if (0xdc00..=0xdfff).contains(&second) {
                                index += 6;
                                0x10000 + ((first - 0xd800) << 10) + second - 0xdc00
                            } else {
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
                    text.push(char::from_u32(point).unwrap_or('\u{fffd}'));
                }
                _ => return None,
            }
            run = index;
        }
    }

    /// Write the member whose key's opening quote is at `index`, up to and
    /// including the colon; returns the offset after the colon.
    fn member(&mut self, index: usize) -> Option<usize> {
        let body = self.body;
        let depth = self.open.len();
        let key_start = index + 1;
        let mut end = plain_end(body, key_start)?;
        let plain = body[end] == b'"';
        let (key, key_len) = if plain {
            end += 1;
            (key_start, end - 1 - key_start)
        } else {
            end = self.decode(key_start)?;
            let key = self.keys.len();
            self.keys.extend_from_slice(self.text.as_bytes());
            (key, self.text.len())
        };
        let mut member = Member {
            key,
            key_len,
            decoded: !plain,
            start: 0,
            end: 0,
        };
        let object = self.objects.last().expect("open object");
        if object.sorted
            && self.members.len() > object.members
            && key_bytes(
                body,
                &self.keys,
                self.members.last().expect("previous member"),
            ) >= key_bytes(body, &self.keys, &member)
            && !self.hold(&member)
        {
            self.objects.last_mut().expect("open object").sorted = false;
        }
        member.start = self.out.len();
        newline(&mut self.out, depth);
        if plain {
            copy(&mut self.out, body, index, end);
        } else {
            quote(&mut self.out, self.text.as_bytes());
        }
        let (colon, byte) = skip_space(body, end)?;
        if byte != b':' {
            return None;
        }
        self.out.extend_from_slice(b": ");
        if depth == 1
            && let Some(field) = Envelope::field(key_bytes(body, &self.keys, &member))
        {
            match skip_space(body, colon + 1) {
                Some((value, b'"')) => {
                    self.decode(value + 1)?;
                    self.envelope.fields[field].clone_from(&self.text);
                }
                Some((_, b'n')) | None => (),
                Some(_) => self.envelope.mismatch = true,
            }
        }
        self.members.push(member);
        Some(colon + 1)
    }

    /// `member`'s key sorts before the last member written to a so far sorted
    /// object. If the members that sort after it are small, set them aside so
    /// that it is written in their place; `release` appends them again after
    /// its value. Returns false, leaving the output alone, for a repeated key
    /// or when more than `HOLD_LIMIT` bytes would have to be set aside.
    fn hold(&mut self, member: &Member) -> bool {
        let key = key_bytes(self.body, &self.keys, member);
        let first = self.objects.last().expect("open object").members;
        // The output ends with the comma that follows the last member.
        let tail = self.out.len() - 1;
        let mut at = self.members.len();
        while at > first {
            let written = &self.members[at - 1];
            match key_bytes(self.body, &self.keys, written).cmp(key) {
                std::cmp::Ordering::Less => break,
                std::cmp::Ordering::Equal => return false,
                std::cmp::Ordering::Greater if tail - written.start > HOLD_LIMIT => return false,
                std::cmp::Ordering::Greater => at -= 1,
            }
        }
        let start = self.members[at].start;
        let object = self.objects.last_mut().expect("open object");
        object.held_members = self.members.len() - at;
        object.held = self.held.len();
        self.held.extend_from_slice(&self.out[start..tail]);
        // What remains ends with the comma before the first held member, or
        // with the object's opening brace.
        self.out.truncate(start);
        true
    }

    /// The value of the member written in place of held members has ended:
    /// append them after it.
    fn release(&mut self) {
        let object = self.objects.last_mut().expect("open object");
        let count = std::mem::take(&mut object.held_members);
        let held = object.held;
        // The member just written is last; it belongs before the held ones.
        let first = self.members.len() - 1 - count;
        self.members[first..].rotate_right(1);
        self.out.push(b',');
        // It started where the first held member used to start.
        let shift = self.out.len() - self.members[first].start;
        self.out.extend_from_slice(&self.held[held..]);
        self.held.truncate(held);
        for member in &mut self.members[first + 1..] {
            member.start += shift;
            member.end += shift;
        }
    }

    /// Put the closing object's rendered members in key order, keeping only
    /// the last member of each key. The largest kept member moves within the
    /// output; only the others pass through `self.moved`, so an envelope
    /// around a large result costs one move and no second copy of it.
    #[cold]
    fn reorder(&mut self, first: usize) {
        let (body, keys) = (self.body, &self.keys);
        let members = &self.members[first..];
        let key = |index: &usize| key_bytes(body, keys, &members[*index]);
        self.order.clear();
        self.order.extend(0..members.len());
        // Stable, so equal keys stay in input order and the last one wins.
        self.order.sort_by(|a, b| key(a).cmp(key(b)));
        let mut kept = 0;
        for position in 0..self.order.len() {
            let last_of_key = self
                .order
                .get(position + 1)
                .is_none_or(|next| key(next) != key(&self.order[position]));
            if last_of_key {
                self.order[kept] = self.order[position];
                kept += 1;
            }
        }
        self.order.truncate(kept);
        let length = |index: &usize| members[*index].end - members[*index].start;
        let largest = (0..kept)
            .max_by_key(|position| length(&self.order[*position]))
            .expect("object has a member");
        let region = members[0].start;
        self.moved.clear();
        for index in &self.order[..largest] {
            self.moved
                .extend_from_slice(&self.out[members[*index].start..members[*index].end]);
            self.moved.push(b',');
        }
        let before = self.moved.len();
        for index in &self.order[largest + 1..] {
            self.moved.push(b',');
            self.moved
                .extend_from_slice(&self.out[members[*index].start..members[*index].end]);
        }
        let stays = &members[self.order[largest]];
        let (source, size) = (stays.start..stays.end, stays.end - stays.start);
        let target = region + before;
        self.out.copy_within(source, target);
        self.out[region..target].copy_from_slice(&self.moved[..before]);
        self.out.truncate(target + size);
        self.out.extend_from_slice(&self.moved[before..]);
        self.reordered = self
            .reordered
            .saturating_add(size)
            .saturating_add(self.moved.len());
        if self.moved.capacity() > 1 << 20 {
            self.moved = Vec::new();
        }
    }
}

fn key_bytes<'a>(body: &'a [u8], keys: &'a [u8], member: &Member) -> &'a [u8] {
    let source = if member.decoded { keys } else { body };
    &source[member.key..member.key + member.key_len]
}

/// The former renderer: a serde serializer over validated input spans that
/// re-slices each container's members and decodes each scalar on its own.
/// It renders the bodies the single pass abandons as [`Pass::Costly`], and is
/// the oracle the differential tests compare that pass with.
pub(super) mod former {
    use crate::{gojson, render};
    use serde::Serialize;
    use std::collections::BTreeMap;

    /// The caller validates the complete body and supplies single-value input.
    /// Syntax and trailing-value diagnostics remain the caller's responsibility.
    pub(in crate::metrics) fn render_validated(body: &[u8]) -> String {
        render::json(&RawValue(body), false)
    }

    struct RawValue<'a>(&'a [u8]);

    impl Serialize for RawValue<'_> {
        fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
            stacker::maybe_grow(64 * 1024, 1024 * 1024, || self.serialize_inner(serializer))
        }
    }

    impl RawValue<'_> {
        fn serialize_inner<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
            use serde::ser::{SerializeMap, SerializeSeq};
            let first = self
                .0
                .iter()
                .find(|byte| !matches!(byte, b' ' | b'\t' | b'\r' | b'\n'))
                .expect("validated JSON has a value");
            match first {
                b'{' => {
                    let members = gojson::raw_members_bytes(self.0)
                        .expect("validated JSON object has valid members");
                    let mut sorted = BTreeMap::new();
                    for (key, value) in members {
                        sorted.insert(key, value);
                    }
                    let mut map = serializer.serialize_map(Some(sorted.len()))?;
                    for (key, value) in sorted {
                        map.serialize_entry(&key, &RawValue(value))?;
                    }
                    map.end()
                }
                b'[' => {
                    let elements = gojson::raw_elements_bytes(self.0)
                        .expect("validated JSON array has valid elements");
                    let mut sequence = serializer.serialize_seq(Some(elements.len()))?;
                    for value in elements {
                        sequence.serialize_element(&RawValue(value))?;
                    }
                    sequence.end()
                }
                _ => {
                    // Decode only this scalar to retain Go's Unicode replacement
                    // rules and owned, unnormalized numeric lexemes. A generic
                    // serde decoder would reject malformed UTF-8 strings.
                    let scalar = gojson::decode_ordered(self.0)
                        .expect("validated JSON scalar can be decoded");
                    scalar.serialize(serializer)
                }
            }
        }
    }
}

#[cfg(test)]
#[path = "raw_json_tests.rs"]
mod tests;
