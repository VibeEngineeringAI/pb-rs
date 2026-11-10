//! Canonical text of a JSONL record, produced in one pass over the line.
//!
//! The collector's payload is defined by serde_json: parse the line to a
//! `Value` (with this crate's `preserve_order` and `arbitrary_precision`
//! features) and serialize the object compactly. That builds and tears down
//! a tree for every line although the answer is nearly always the line with
//! its whitespace removed.
//!
//! [`object`] returns that same text for the lines it can vouch for and
//! `None` for everything else. `None` is never a verdict on the line: the
//! caller then takes the serde_json route, which stays the only author of
//! error messages and of every normalization not listed here.
//!
//! Accepted, and what is done to it:
//! - a valid UTF-8 line holding one object, with optional JSON whitespace
//!   around any token: the whitespace is dropped;
//! - objects and arrays nested at most [`MAX_DEPTH`] deep, with keys in
//!   source order;
//! - `true`, `false` and `null`;
//! - numbers without an exponent, other than the integer `-0`: copied, as
//!   `arbitrary_precision` keeps their text whatever their size;
//! - strings without raw control characters: copied, except that `\/`
//!   becomes `/` and a `\uXXXX` escape (or a valid surrogate pair) becomes
//!   the character it names, escaped again only if JSON requires it.
//!
//! Deferred to serde_json:
//! - anything it rejects, including invalid UTF-8, raw control characters,
//!   unpaired surrogates, unknown escapes and trailing text;
//! - a line whose value is not an object;
//! - an object with two equal keys (it keeps the first position and the
//!   last value);
//! - a number with an exponent (it rewrites `1E5` as `1e+5`) and `-0`;
//! - a key in its private `$serde_json::private::` namespace, which makes
//!   it read the object as a number or as raw JSON;
//! - nesting deeper than [`MAX_DEPTH`], well inside its own limit of 128.

use super::encode;

/// Deepest nesting of objects and arrays copied here, counting the record.
const MAX_DEPTH: usize = 64;

/// Objects with more keys than this are checked for duplicates by sorting.
const PAIRWISE_KEYS: usize = 16;

/// serde_json reads an object whose first key is one of its private tokens
/// as that token's type instead of as an object.
const PRIVATE_KEYS: &[u8] = b"$serde_json::private::";

pub(super) fn object(line: &[u8]) -> Option<String> {
    let mut scanner = Scanner {
        line,
        at: 0,
        // Nothing accepted here is longer in canonical form than in source.
        out: Vec::with_capacity(line.len()),
        keys: Vec::new(),
    };
    scanner.whitespace();
    if scanner.peek()? != b'{' {
        return None;
    }
    scanner.object(MAX_DEPTH - 1)?;
    scanner.whitespace();
    if scanner.at != line.len() {
        return None;
    }
    // The one UTF-8 check. Outside strings only ASCII is accepted, and inside
    // them every byte is copied except ASCII escapes, each replaced by ASCII
    // or one whole character. So this text is valid exactly when the line is.
    String::from_utf8(scanner.out).ok()
}

struct Scanner<'a> {
    line: &'a [u8],
    at: usize,
    out: Vec<u8>,
    /// Where in `out` each key of every open object lies, between its
    /// quotes. The innermost open object owns the tail of this list.
    keys: Vec<(usize, usize)>,
}

impl Scanner<'_> {
    fn peek(&self) -> Option<u8> {
        self.line.get(self.at).copied()
    }

    fn whitespace(&mut self) {
        while let Some(b' ' | b'\t' | b'\n' | b'\r') = self.peek() {
            self.at += 1;
        }
    }

    /// Copy one value. `deeper` is how many more levels may open inside it.
    fn value(&mut self, deeper: usize) -> Option<()> {
        match self.peek()? {
            b'"' => self.string(),
            b'{' => self.object(deeper.checked_sub(1)?),
            b'[' => self.array(deeper.checked_sub(1)?),
            b't' => self.literal(b"true"),
            b'f' => self.literal(b"false"),
            b'n' => self.literal(b"null"),
            b'-' | b'0'..=b'9' => self.number(),
            _ => None,
        }
    }

    fn object(&mut self, deeper: usize) -> Option<()> {
        self.at += 1;
        self.out.push(b'{');
        self.whitespace();
        if self.peek()? == b'}' {
            self.at += 1;
            self.out.push(b'}');
            return Some(());
        }
        let first_key = self.keys.len();
        loop {
            if self.peek()? != b'"' {
                return None;
            }
            let key = self.out.len() + 1;
            self.string()?;
            let key = (key, self.out.len() - 1);
            if self.out[key.0..key.1].starts_with(PRIVATE_KEYS) {
                return None;
            }
            self.keys.push(key);
            self.whitespace();
            if self.peek()? != b':' {
                return None;
            }
            self.at += 1;
            self.out.push(b':');
            self.whitespace();
            self.value(deeper)?;
            self.whitespace();
            match self.peek()? {
                b',' => {
                    self.at += 1;
                    self.out.push(b',');
                    self.whitespace();
                }
                b'}' => break,
                _ => return None,
            }
        }
        self.at += 1;
        self.out.push(b'}');
        if has_duplicate(&self.out, &mut self.keys[first_key..]) {
            return None;
        }
        self.keys.truncate(first_key);
        Some(())
    }

    fn array(&mut self, deeper: usize) -> Option<()> {
        self.at += 1;
        self.out.push(b'[');
        self.whitespace();
        if self.peek()? == b']' {
            self.at += 1;
            self.out.push(b']');
            return Some(());
        }
        loop {
            self.value(deeper)?;
            self.whitespace();
            match self.peek()? {
                b',' => {
                    self.at += 1;
                    self.out.push(b',');
                    self.whitespace();
                }
                b']' => break,
                _ => return None,
            }
        }
        self.at += 1;
        self.out.push(b']');
        Some(())
    }

    fn literal(&mut self, text: &[u8]) -> Option<()> {
        if !self.line[self.at..].starts_with(text) {
            return None;
        }
        self.at += text.len();
        self.out.extend_from_slice(text);
        Some(())
    }

    /// Copy a run of digits and return its length.
    fn digits(&mut self) -> usize {
        let start = self.at;
        while let Some(digit @ b'0'..=b'9') = self.peek() {
            self.out.push(digit);
            self.at += 1;
        }
        self.at - start
    }

    fn number(&mut self) -> Option<()> {
        let negative = self.peek()? == b'-';
        if negative {
            self.out.push(b'-');
            self.at += 1;
        }
        // A leading zero stands alone: `01` is not a number.
        let zero = self.peek()? == b'0';
        if zero {
            self.out.push(b'0');
            self.at += 1;
            if let Some(b'0'..=b'9') = self.peek() {
                return None;
            }
        } else if self.digits() == 0 {
            return None;
        }
        let fraction = self.peek() == Some(b'.');
        if fraction {
            self.out.push(b'.');
            self.at += 1;
            if self.digits() == 0 {
                return None;
            }
        }
        if let Some(b'e' | b'E') = self.peek() {
            return None;
        }
        // What is left to exclude is the integer `-0`.
        (!(negative && zero) || fraction).then_some(())
    }

    /// Copy the string which opens at the current quote.
    fn string(&mut self) -> Option<()> {
        self.out.push(b'"');
        let mut at = self.at + 1;
        loop {
            at = encode::copy_plain(&mut self.out, self.line, at);
            match *self.line.get(at)? {
                b'"' => {
                    self.out.push(b'"');
                    self.at = at + 1;
                    return Some(());
                }
                b'\\' => match *self.line.get(at + 1)? {
                    // serde_json writes these back as the same two bytes.
                    escape @ (b'"' | b'\\' | b'b' | b'f' | b'n' | b'r' | b't') => {
                        self.out.extend_from_slice(&[b'\\', escape]);
                        at += 2;
                    }
                    b'/' => {
                        self.out.push(b'/');
                        at += 2;
                    }
                    b'u' => {
                        let (character, end) = unicode_escape(self.line, at)?;
                        let mut utf8 = [0; 4];
                        encode::push_escaped(
                            &mut self.out,
                            character.encode_utf8(&mut utf8).as_bytes(),
                        );
                        at = end;
                    }
                    _ => return None,
                },
                // A raw control character.
                _ => return None,
            }
        }
    }
}

/// Decode the `\uXXXX` escape whose backslash is at `at`, together with the
/// trailing surrogate which must follow a leading one. Returns the character
/// and the index after the escape; `None` for anything serde_json rejects.
fn unicode_escape(line: &[u8], at: usize) -> Option<(char, usize)> {
    let first = hex4(line.get(at + 2..at + 6)?)?;
    if !(0xd800..=0xdbff).contains(&first) {
        // An unpaired trailing surrogate is not a character.
        return Some((char::from_u32(first)?, at + 6));
    }
    let pair = line.get(at + 6..at + 12)?;
    if !pair.starts_with(b"\\u") {
        return None;
    }
    let second = hex4(&pair[2..])?;
    if !(0xdc00..=0xdfff).contains(&second) {
        return None;
    }
    let code = 0x1_0000 + ((first - 0xd800) << 10 | (second - 0xdc00));
    Some((char::from_u32(code)?, at + 12))
}

/// Four hexadecimal digits of either case.
fn hex4(digits: &[u8]) -> Option<u32> {
    digits.iter().try_fold(0, |value, &digit| {
        Some(value << 4 | char::from(digit).to_digit(16)?)
    })
}

/// Whether two of `keys` hold the same text in `out`. Keys are compared in
/// canonical form, which is one-to-one with the strings they denote, so
/// differently escaped spellings of one key are found equal.
fn has_duplicate(out: &[u8], keys: &mut [(usize, usize)]) -> bool {
    let text = |&(start, end): &(usize, usize)| &out[start..end];
    if keys.len() <= PAIRWISE_KEYS {
        return keys
            .iter()
            .enumerate()
            .any(|(index, key)| keys[..index].iter().any(|other| text(other) == text(key)));
    }
    // The order of `keys` no longer matters once their object has closed.
    keys.sort_unstable_by(|left, right| text(left).cmp(text(right)));
    keys.windows(2).any(|pair| text(&pair[0]) == text(&pair[1]))
}

#[cfg(test)]
#[path = "canonical_tests.rs"]
mod tests;
