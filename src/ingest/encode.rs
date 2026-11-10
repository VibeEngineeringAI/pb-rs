//! Compact JSON text for ingestion rows and payloads: byte for byte what
//! serde_json's compact formatter writes for the same strings and integers.
//!
//! serde_json escapes a string one byte at a time through a lookup table.
//! Log text is mostly bytes which need no escape, so this examines eight at
//! a time, copying each word as it goes and stopping at the first byte that
//! does need one.
//!
//! Output is appended to a byte buffer. Escaping replaces single ASCII bytes
//! with ASCII text, so the output is valid UTF-8 exactly when the input was.

const ONES: u64 = 0x0101_0101_0101_0101;
const HIGH: u64 = 0x8080_8080_8080_8080;

/// Whether a JSON string cannot hold `byte` as it is: a quote, a backslash,
/// or a C0 control. Everything else, including DEL and every byte of a
/// multi-byte character, is copied through.
fn needs_escape(byte: u8) -> bool {
    byte < 0x20 || byte == b'"' || byte == b'\\'
}

/// Index within `word` of its first byte needing an escape, if it has one.
#[inline]
fn first_escape(word: &[u8; 8]) -> Option<usize> {
    let word = u64::from_le_bytes(*word);
    // Each term sets the high bit of a byte which is below 0x20, a quote, or
    // a backslash. Its subtraction can also borrow into the bytes above such
    // a byte and flag them wrongly, but never into the bytes below, so the
    // lowest flag is exact, and the lowest is the first byte in memory.
    let quote = word ^ (ONES * b'"' as u64);
    let backslash = word ^ (ONES * b'\\' as u64);
    let flags = (word.wrapping_sub(ONES * 0x20) & !word
        | quote.wrapping_sub(ONES) & !quote
        | backslash.wrapping_sub(ONES) & !backslash)
        & HIGH;
    (flags != 0).then(|| (flags.trailing_zeros() / 8) as usize)
}

/// Append the bytes of `text` from `at` up to the first which needs an
/// escape, and return the index of that byte, or the length of `text`.
#[inline]
pub(super) fn copy_plain(out: &mut Vec<u8>, text: &[u8], mut at: usize) -> usize {
    while let Some(word) = text[at..].first_chunk::<8>() {
        // A whole word is one fixed-size store; the part from the escape
        // onwards is then dropped again.
        out.extend_from_slice(word);
        if let Some(plain) = first_escape(word) {
            out.truncate(out.len() - (8 - plain));
            return at + plain;
        }
        at += 8;
    }
    let tail = &text[at..];
    let plain = tail
        .iter()
        .position(|&byte| needs_escape(byte))
        .unwrap_or(tail.len());
    out.extend_from_slice(&tail[..plain]);
    at + plain
}

/// Append the escape for one byte for which `needs_escape` holds.
fn push_escape(out: &mut Vec<u8>, byte: u8) {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    out.extend_from_slice(match byte {
        b'"' => b"\\\"",
        b'\\' => b"\\\\",
        0x08 => b"\\b",
        0x09 => b"\\t",
        0x0a => b"\\n",
        0x0c => b"\\f",
        0x0d => b"\\r",
        _ => {
            let digits = [HEX[usize::from(byte >> 4)], HEX[usize::from(byte & 0xf)]];
            out.extend_from_slice(b"\\u00");
            out.extend_from_slice(&digits);
            return;
        }
    });
}

/// Append `text` as the contents of a JSON string, without its quotes.
pub(super) fn push_escaped(out: &mut Vec<u8>, text: &[u8]) {
    let mut at = 0;
    loop {
        at = copy_plain(out, text, at);
        let Some(&byte) = text.get(at) else {
            return;
        };
        push_escape(out, byte);
        at += 1;
    }
}

/// Append `text` as a JSON string.
pub(super) fn push_string(out: &mut Vec<u8>, text: &[u8]) {
    out.push(b'"');
    push_escaped(out, text);
    out.push(b'"');
}

/// Append `value` in decimal.
pub(super) fn push_u64(out: &mut Vec<u8>, mut value: u64) {
    // u64::MAX has twenty digits.
    let mut digits = [b'0'; 20];
    let mut first = digits.len();
    loop {
        first -= 1;
        digits[first] += (value % 10) as u8;
        value /= 10;
        if value == 0 {
            break;
        }
    }
    out.extend_from_slice(&digits[first..]);
}

#[cfg(test)]
#[path = "encode_tests.rs"]
mod tests;
