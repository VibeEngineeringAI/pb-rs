//! Incremental framing for a single JSON value. Syntax is checked as bytes
//! arrive, so a malformed prefix does not wait for a closing delimiter.
use std::io::{self, BufRead};

#[derive(Debug)]
pub(super) enum ScanError {
    Io(io::Error),
    Syntax(String),
}

pub(super) fn peek<R: BufRead>(reader: &mut R) -> io::Result<Option<u8>> {
    Ok(reader.fill_buf()?.first().copied())
}

pub(super) fn space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\r' | b'\n')
}

pub(super) fn nonspace<R: BufRead>(reader: &mut R) -> io::Result<Option<u8>> {
    loop {
        match peek(reader)? {
            Some(byte) if space(byte) => reader.consume(1),
            other => return Ok(other),
        }
    }
}

pub(super) fn invalid(byte: u8, context: &str) -> String {
    let quoted = if byte == b'\'' {
        "'\\''".to_owned()
    } else {
        let quoted = crate::parser::quote(&char::from(byte).to_string());
        format!("'{}'", quoted[1..quoted.len() - 1].replace("\\\"", "\""))
    };
    format!("invalid character {quoted} {context}")
}

#[derive(Clone, Copy)]
enum Frame {
    ArrayFirst,
    ArrayValue,
    ArrayEnd,
    ObjectFirst,
    ObjectKey,
    ObjectColon,
    ObjectValue,
    ObjectEnd,
}

#[derive(Clone, Copy)]
enum NumberState {
    Sign,
    Zero,
    Integer,
    Dot,
    Fraction,
    Exponent,
    ExponentSign,
    ExponentDigits,
}

#[derive(Clone, Copy)]
enum Lexeme {
    Value,
    String { key: bool },
    Escape { key: bool },
    Hex { key: bool, remaining: u8 },
    Literal { word: &'static [u8], index: usize },
    Number(NumberState),
    End,
}

/// Containers may nest this deep. Opening one more is rejected, as it is by
/// Go's decoder.
const MAX_DEPTH: usize = 10_000;

/// What one buffered slice did to the value being framed.
enum Step {
    /// Every byte of the slice belongs to the value and the reader must be
    /// asked again: the value is still open, or it is a root scalar whose
    /// lookahead byte has not arrived.
    Pending,
    /// The value ended inside the slice. Bytes that were not accepted stay
    /// in the reader.
    Complete,
    /// The first byte that was not accepted cannot continue the value.
    Invalid(String),
}

/// Root objects/arrays complete at their closing delimiter; other roots need
/// one byte of lookahead, which stays in the reader for the outer-array parser.
pub(super) fn value<R: BufRead>(reader: &mut R) -> Result<Vec<u8>, ScanError> {
    let mut scanner = Scanner::default();
    scanner.value(reader)?;
    Ok(scanner.bytes)
}

/// Keep the largest element and nesting capacities for subsequent rows.
#[derive(Default)]
pub(super) struct Scanner {
    pub(super) bytes: Vec<u8>,
    frames: Vec<Frame>,
}

impl Scanner {
    /// Frame one value from whatever the reader has buffered, one slice at a
    /// time. `advance` asks for more only by accepting a whole slice, so the
    /// reader is refilled exactly where a byte-at-a-time scan would refill it
    /// and never while it still holds a byte this value does not own.
    pub(super) fn value<R: BufRead>(&mut self, reader: &mut R) -> Result<(), ScanError> {
        self.bytes.clear();
        self.frames.clear();
        let mut lexeme = Lexeme::Value;
        loop {
            let available = reader.fill_buf().map_err(ScanError::Io)?;
            if available.is_empty() {
                return self.end_of_input(lexeme);
            }
            let (accepted, step) = advance(&mut self.frames, &mut lexeme, available);
            self.bytes.extend_from_slice(&available[..accepted]);
            reader.consume(accepted);
            match step {
                Step::Pending => (),
                Step::Complete => return Ok(()),
                Step::Invalid(message) => return Err(ScanError::Syntax(message)),
            }
        }
    }

    /// Only a root scalar can end at EOF: a string or literal that was
    /// waiting for lookahead, or a number in a state that may terminate.
    fn end_of_input(&self, lexeme: Lexeme) -> Result<(), ScanError> {
        let complete = match lexeme {
            Lexeme::End => true,
            Lexeme::Number(
                NumberState::Zero
                | NumberState::Integer
                | NumberState::Fraction
                | NumberState::ExponentDigits,
            ) => self.frames.is_empty(),
            _ => false,
        };
        if complete {
            return Ok(());
        }
        Err(ScanError::Syntax(
            if self.bytes.iter().all(|byte| space(*byte)) {
                "EOF".into()
            } else {
                "unexpected EOF".into()
            },
        ))
    }
}

/// Run the state machine over one buffered slice. Returns how many leading
/// bytes the value accepted and why the run stopped; `lexeme` and `frames`
/// carry the position within the value over to the next slice.
fn advance(frames: &mut Vec<Frame>, lexeme: &mut Lexeme, input: &[u8]) -> (usize, Step) {
    // A local copy keeps the state out of memory inside the loop.
    let mut state = *lexeme;
    let mut index = 0;
    let step = loop {
        let Some(&byte) = input.get(index) else {
            break Step::Pending;
        };
        match state {
            // The lookahead byte of a root scalar: seen, never accepted.
            Lexeme::End => break Step::Complete,
            Lexeme::String { key } => {
                // Ordinary string bytes do not change scanner state, so the
                // whole run is accepted at once. Back-to-back escapes and
                // empty strings have no run to look for.
                if !special(byte) {
                    index += ordinary_run(&input[index..]);
                }
                let Some(&byte) = input.get(index) else {
                    break Step::Pending;
                };
                state = match byte {
                    b'"' if key => {
                        *frames.last_mut().expect("object key") = Frame::ObjectColon;
                        Lexeme::Value
                    }
                    b'"' => after_value(frames),
                    b'\\' => {
                        // A buffered, valid escape is accepted whole. One that
                        // is malformed or cut short by the slice is left to
                        // the transitions below, which reject or resume it.
                        let length = buffered_escape(&input[index..]);
                        if length != 0 {
                            index += length;
                            continue;
                        }
                        Lexeme::Escape { key }
                    }
                    _ => break Step::Invalid(invalid(byte, "in string literal")),
                };
            }
            Lexeme::Escape { key } => {
                state = match byte {
                    _ if simple_escape(byte) => Lexeme::String { key },
                    b'u' => Lexeme::Hex { key, remaining: 4 },
                    _ => break Step::Invalid(invalid(byte, "in string escape code")),
                };
            }
            Lexeme::Hex { key, remaining } => {
                if !byte.is_ascii_hexdigit() {
                    break Step::Invalid(invalid(byte, "in \\u hexadecimal character escape"));
                }
                state = if remaining == 1 {
                    Lexeme::String { key }
                } else {
                    Lexeme::Hex {
                        key,
                        remaining: remaining - 1,
                    }
                };
            }
            Lexeme::Literal { word, index: at } => {
                if byte != word[at] {
                    break Step::Invalid(invalid(
                        byte,
                        &format!(
                            "in literal {} (expecting '{}')",
                            String::from_utf8_lossy(word),
                            char::from(word[at])
                        ),
                    ));
                }
                state = if at + 1 == word.len() {
                    after_value(frames)
                } else {
                    Lexeme::Literal {
                        word,
                        index: at + 1,
                    }
                };
            }
            Lexeme::Number(number) => {
                use NumberState::*;
                let next = match (number, byte) {
                    (Sign, b'0') => Zero,
                    (Sign, b'1'..=b'9') => Integer,
                    (Sign, _) => break Step::Invalid(invalid(byte, "in numeric literal")),
                    (Integer, b'0'..=b'9') => Integer,
                    (Zero | Integer, b'.') => Dot,
                    (Zero | Integer | Fraction, b'e' | b'E') => Exponent,
                    (Dot | Fraction, b'0'..=b'9') => Fraction,
                    (Dot, _) => {
                        break Step::Invalid(invalid(
                            byte,
                            "after decimal point in numeric literal",
                        ));
                    }
                    (Exponent, b'+' | b'-') => ExponentSign,
                    (Exponent | ExponentSign | ExponentDigits, b'0'..=b'9') => ExponentDigits,
                    (Exponent | ExponentSign, _) => {
                        break Step::Invalid(invalid(byte, "in exponent of numeric literal"));
                    }
                    (Zero | Integer | Fraction | ExponentDigits, _) => {
                        // This byte ends the number without belonging to it;
                        // the state that follows the number decides its fate.
                        state = after_value(frames);
                        continue;
                    }
                };
                state = Lexeme::Number(next);
            }
            // Whitespace is legal in every structural position.
            Lexeme::Value if space(byte) => (),
            Lexeme::Value => {
                let frame = frames.last().copied();
                let closes = match frame {
                    Some(Frame::ObjectFirst | Frame::ObjectKey) => {
                        if byte == b'}' && matches!(frame, Some(Frame::ObjectFirst)) {
                            true
                        } else if byte == b'"' {
                            state = Lexeme::String { key: true };
                            false
                        } else {
                            break Step::Invalid(invalid(
                                byte,
                                "looking for beginning of object key string",
                            ));
                        }
                    }
                    Some(Frame::ObjectColon) => {
                        if byte != b':' {
                            break Step::Invalid(invalid(byte, "after object key"));
                        }
                        *frames.last_mut().unwrap() = Frame::ObjectValue;
                        false
                    }
                    Some(Frame::ObjectEnd) => match byte {
                        b',' => {
                            *frames.last_mut().unwrap() = Frame::ObjectKey;
                            false
                        }
                        b'}' => true,
                        _ => {
                            break Step::Invalid(invalid(byte, "after object key:value pair"));
                        }
                    },
                    Some(Frame::ArrayEnd) => match byte {
                        b',' => {
                            *frames.last_mut().unwrap() = Frame::ArrayValue;
                            false
                        }
                        b']' => true,
                        _ => break Step::Invalid(invalid(byte, "after array element")),
                    },
                    Some(Frame::ArrayFirst) if byte == b']' => true,
                    Some(Frame::ArrayFirst | Frame::ArrayValue | Frame::ObjectValue) | None => {
                        state = match byte {
                            b'[' | b'{' => {
                                if frames.len() == MAX_DEPTH {
                                    break Step::Invalid(invalid(byte, "exceeded max depth"));
                                }
                                frames.push(if byte == b'[' {
                                    Frame::ArrayFirst
                                } else {
                                    Frame::ObjectFirst
                                });
                                Lexeme::Value
                            }
                            b'"' => Lexeme::String { key: false },
                            b't' => Lexeme::Literal {
                                word: b"true",
                                index: 1,
                            },
                            b'f' => Lexeme::Literal {
                                word: b"false",
                                index: 1,
                            },
                            b'n' => Lexeme::Literal {
                                word: b"null",
                                index: 1,
                            },
                            b'-' => Lexeme::Number(NumberState::Sign),
                            b'0' => Lexeme::Number(NumberState::Zero),
                            b'1'..=b'9' => Lexeme::Number(NumberState::Integer),
                            _ => {
                                break Step::Invalid(invalid(
                                    byte,
                                    "looking for beginning of value",
                                ));
                            }
                        };
                        false
                    }
                };
                if closes {
                    frames.pop();
                    // A root container needs no lookahead: accept its closing
                    // delimiter and leave whatever follows in the reader.
                    if matches!(after_value(frames), Lexeme::End) {
                        index += 1;
                        break Step::Complete;
                    }
                }
            }
        }
        index += 1;
    };
    *lexeme = state;
    (index, step)
}

/// A value just ended: its container now expects a separator or its closing
/// delimiter. With no container the value was the root, and scanning is over
/// apart from a scalar's lookahead.
fn after_value(frames: &mut [Frame]) -> Lexeme {
    match frames.last_mut() {
        Some(frame @ (Frame::ArrayFirst | Frame::ArrayValue)) => *frame = Frame::ArrayEnd,
        Some(frame @ Frame::ObjectValue) => *frame = Frame::ObjectEnd,
        None => return Lexeme::End,
        _ => unreachable!("completed JSON value in structural position"),
    }
    Lexeme::Value
}

/// Inside a string, only a quote, a backslash or a control character changes
/// scanner state (the last by being rejected).
fn special(byte: u8) -> bool {
    byte < 0x20 || byte == b'"' || byte == b'\\'
}

/// The characters that complete a two-byte escape.
fn simple_escape(byte: u8) -> bool {
    matches!(byte, b'"' | b'\\' | b'/' | b'b' | b'f' | b'n' | b'r' | b't')
}

/// Length of the complete, valid escape sequence at the start of `bytes`,
/// which begins with a backslash; zero if it is invalid or not all buffered.
fn buffered_escape(bytes: &[u8]) -> usize {
    match bytes {
        [_, byte, ..] if simple_escape(*byte) => 2,
        [_, b'u', hex @ ..] if hex.len() >= 4 && hex[..4].iter().all(u8::is_ascii_hexdigit) => 6,
        _ => 0,
    }
}

/// Length of the leading run of bytes that leave string state unchanged.
fn ordinary_run(bytes: &[u8]) -> usize {
    const BLOCK: usize = 16;
    // Reducing a fixed-width block to one flag has no early exit, which lets
    // the compiler use vector compares. The block holding the first special
    // byte, and the final partial block, are finished a byte at a time.
    let mut run = 0;
    for block in bytes.chunks_exact(BLOCK) {
        if block.iter().fold(false, |any, &byte| any | special(byte)) {
            break;
        }
        run += BLOCK;
    }
    run + bytes[run..]
        .iter()
        .position(|&byte| special(byte))
        .unwrap_or(bytes.len() - run)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The scanner this module shipped before it was driven over buffered
    /// slices, kept as the oracle: one `fill_buf`, push and `consume` per
    /// byte. `BULK_STRINGS` selects its later variant that copied ordinary
    /// string runs; `false` is the plain one-byte state machine.
    fn reference<R: BufRead, const BULK_STRINGS: bool>(
        scanner: &mut Scanner,
        reader: &mut R,
    ) -> Result<(), ScanError> {
        scanner.bytes.clear();
        scanner.frames.clear();
        let bytes = &mut scanner.bytes;
        let frames = &mut scanner.frames;
        let mut lexeme = Lexeme::Value;
        loop {
            let next = if BULK_STRINGS && matches!(lexeme, Lexeme::String { .. }) {
                let available = reader.fill_buf().map_err(ScanError::Io)?;
                // Ordinary string bytes do not change scanner state. Copy a
                // whole buffered run, leaving escapes, quotes and invalid
                // control bytes to the original state machine below. A run
                // reaching the buffer boundary still refills on the next turn.
                let count = available
                    .iter()
                    .position(|&byte| byte < 0x20 || byte == b'"' || byte == b'\\')
                    .unwrap_or(available.len());
                if count != 0 {
                    bytes.extend_from_slice(&available[..count]);
                    reader.consume(count);
                    continue;
                }
                available.first().copied()
            } else {
                peek(reader).map_err(ScanError::Io)?
            };
            let byte = match next {
                Some(byte) => byte,
                None => {
                    if matches!(lexeme, Lexeme::End)
                        || matches!(
                            lexeme,
                            Lexeme::Number(
                                NumberState::Zero
                                    | NumberState::Integer
                                    | NumberState::Fraction
                                    | NumberState::ExponentDigits
                            )
                        ) && frames.is_empty()
                    {
                        return Ok(());
                    }
                    return Err(ScanError::Syntax(if bytes.iter().all(|b| space(*b)) {
                        "EOF".into()
                    } else {
                        "unexpected EOF".into()
                    }));
                }
            };
            let mut consumed = true;
            let mut finished = false;
            let mut container_end = false;
            match lexeme {
                Lexeme::End => return Ok(()),
                Lexeme::String { key } => match byte {
                    b'"' => {
                        if key {
                            *frames.last_mut().expect("object key") = Frame::ObjectColon;
                            lexeme = Lexeme::Value;
                        } else {
                            finished = true;
                        }
                    }
                    b'\\' => lexeme = Lexeme::Escape { key },
                    0..=31 => return Err(ScanError::Syntax(invalid(byte, "in string literal"))),
                    _ => (),
                },
                Lexeme::Escape { key } => match byte {
                    b'"' | b'\\' | b'/' | b'b' | b'f' | b'n' | b'r' | b't' => {
                        lexeme = Lexeme::String { key }
                    }
                    b'u' => lexeme = Lexeme::Hex { key, remaining: 4 },
                    _ => return Err(ScanError::Syntax(invalid(byte, "in string escape code"))),
                },
                Lexeme::Hex { key, remaining } => {
                    if !byte.is_ascii_hexdigit() {
                        return Err(ScanError::Syntax(invalid(
                            byte,
                            "in \\u hexadecimal character escape",
                        )));
                    }
                    lexeme = if remaining == 1 {
                        Lexeme::String { key }
                    } else {
                        Lexeme::Hex {
                            key,
                            remaining: remaining - 1,
                        }
                    };
                }
                Lexeme::Literal { word, index } => {
                    if byte != word[index] {
                        return Err(ScanError::Syntax(invalid(
                            byte,
                            &format!(
                                "in literal {} (expecting '{}')",
                                String::from_utf8_lossy(word),
                                char::from(word[index])
                            ),
                        )));
                    }
                    if index + 1 == word.len() {
                        finished = true;
                    } else {
                        lexeme = Lexeme::Literal {
                            word,
                            index: index + 1,
                        };
                    }
                }
                Lexeme::Number(state) => {
                    use NumberState::*;
                    let next = match (state, byte) {
                        (Sign, b'0') => Some(Zero),
                        (Sign, b'1'..=b'9') => Some(Integer),
                        (Sign, _) => {
                            return Err(ScanError::Syntax(invalid(byte, "in numeric literal")));
                        }
                        (Integer, b'0'..=b'9') => Some(Integer),
                        (Zero | Integer, b'.') => Some(Dot),
                        (Zero | Integer | Fraction, b'e' | b'E') => Some(Exponent),
                        (Dot | Fraction, b'0'..=b'9') => Some(Fraction),
                        (Dot, _) => {
                            return Err(ScanError::Syntax(invalid(
                                byte,
                                "after decimal point in numeric literal",
                            )));
                        }
                        (Exponent, b'+' | b'-') => Some(ExponentSign),
                        (Exponent | ExponentSign | ExponentDigits, b'0'..=b'9') => {
                            Some(ExponentDigits)
                        }
                        (Exponent | ExponentSign, _) => {
                            return Err(ScanError::Syntax(invalid(
                                byte,
                                "in exponent of numeric literal",
                            )));
                        }
                        _ => None,
                    };
                    if let Some(next) = next {
                        lexeme = Lexeme::Number(next);
                    } else {
                        finished = true;
                        consumed = false;
                    }
                }
                Lexeme::Value => {
                    if space(byte) {
                        // Whitespace is legal in every structural position.
                    } else {
                        let frame = frames.last().copied();
                        match frame {
                            Some(Frame::ObjectFirst | Frame::ObjectKey) => {
                                if byte == b'}' && matches!(frame, Some(Frame::ObjectFirst)) {
                                    frames.pop();
                                    finished = true;
                                    container_end = true;
                                } else if byte == b'"' {
                                    lexeme = Lexeme::String { key: true };
                                } else {
                                    return Err(ScanError::Syntax(invalid(
                                        byte,
                                        "looking for beginning of object key string",
                                    )));
                                }
                            }
                            Some(Frame::ObjectColon) => {
                                if byte != b':' {
                                    return Err(ScanError::Syntax(invalid(
                                        byte,
                                        "after object key",
                                    )));
                                }
                                *frames.last_mut().unwrap() = Frame::ObjectValue;
                            }
                            Some(Frame::ObjectEnd) => match byte {
                                b',' => *frames.last_mut().unwrap() = Frame::ObjectKey,
                                b'}' => {
                                    frames.pop();
                                    finished = true;
                                    container_end = true;
                                }
                                _ => {
                                    return Err(ScanError::Syntax(invalid(
                                        byte,
                                        "after object key:value pair",
                                    )));
                                }
                            },
                            Some(Frame::ArrayEnd) => match byte {
                                b',' => *frames.last_mut().unwrap() = Frame::ArrayValue,
                                b']' => {
                                    frames.pop();
                                    finished = true;
                                    container_end = true;
                                }
                                _ => {
                                    return Err(ScanError::Syntax(invalid(
                                        byte,
                                        "after array element",
                                    )));
                                }
                            },
                            Some(Frame::ArrayFirst) if byte == b']' => {
                                frames.pop();
                                finished = true;
                                container_end = true;
                            }
                            _ => match byte {
                                b'[' | b'{' => {
                                    if frames.len() == 10_000 {
                                        return Err(ScanError::Syntax(invalid(
                                            byte,
                                            "exceeded max depth",
                                        )));
                                    }
                                    frames.push(if byte == b'[' {
                                        Frame::ArrayFirst
                                    } else {
                                        Frame::ObjectFirst
                                    });
                                }
                                b'"' => lexeme = Lexeme::String { key: false },
                                b't' => {
                                    lexeme = Lexeme::Literal {
                                        word: b"true",
                                        index: 1,
                                    }
                                }
                                b'f' => {
                                    lexeme = Lexeme::Literal {
                                        word: b"false",
                                        index: 1,
                                    }
                                }
                                b'n' => {
                                    lexeme = Lexeme::Literal {
                                        word: b"null",
                                        index: 1,
                                    }
                                }
                                b'-' => lexeme = Lexeme::Number(NumberState::Sign),
                                b'0' => lexeme = Lexeme::Number(NumberState::Zero),
                                b'1'..=b'9' => lexeme = Lexeme::Number(NumberState::Integer),
                                _ => {
                                    return Err(ScanError::Syntax(invalid(
                                        byte,
                                        "looking for beginning of value",
                                    )));
                                }
                            },
                        }
                    }
                }
            }
            if consumed {
                bytes.push(byte);
                reader.consume(1);
            }
            if finished {
                match frames.last_mut() {
                    Some(frame @ (Frame::ArrayFirst | Frame::ArrayValue)) => {
                        *frame = Frame::ArrayEnd
                    }
                    Some(frame @ Frame::ObjectValue) => *frame = Frame::ObjectEnd,
                    None => {
                        if container_end {
                            return Ok(());
                        }
                        lexeme = Lexeme::End;
                        continue;
                    }
                    _ => unreachable!("completed JSON value in structural position"),
                }
                lexeme = Lexeme::Value;
            }
        }
    }

    /// The scanner under test and the two generations of its oracle.
    #[derive(Clone, Copy, Debug)]
    enum Framer {
        Slices,
        Bytewise,
        BulkStrings,
    }
    impl Framer {
        fn scan<R: BufRead>(self, scanner: &mut Scanner, reader: &mut R) -> Result<(), ScanError> {
            match self {
                Self::Slices => scanner.value(reader),
                Self::Bytewise => reference::<_, false>(scanner, reader),
                Self::BulkStrings => reference::<_, true>(scanner, reader),
            }
        }
    }

    /// Hands out the input in fragments whose sizes cycle through `sizes`,
    /// recording every position at which it was asked to refill.
    struct Fragmented<'a> {
        input: &'a [u8],
        position: usize,
        end: usize,
        sizes: &'a [usize],
        turn: usize,
        error_at: Option<usize>,
        refills: Vec<usize>,
    }
    impl<'a> Fragmented<'a> {
        fn new(input: &'a [u8], sizes: &'a [usize], error_at: Option<usize>) -> Self {
            Self {
                input,
                position: 0,
                end: 0,
                sizes,
                turn: 0,
                error_at,
                refills: Vec::new(),
            }
        }
    }
    impl io::Read for Fragmented<'_> {
        fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
            let available = self.fill_buf()?;
            let count = output.len().min(available.len());
            output[..count].copy_from_slice(&available[..count]);
            self.consume(count);
            Ok(count)
        }
    }
    impl BufRead for Fragmented<'_> {
        fn fill_buf(&mut self) -> io::Result<&[u8]> {
            if self.position == self.end {
                self.refills.push(self.position);
                if self.error_at == Some(self.position) {
                    return Err(io::Error::new(io::ErrorKind::TimedOut, "injected read"));
                }
                let size = self.sizes[self.turn % self.sizes.len()];
                self.turn += 1;
                self.end = self.position.saturating_add(size).min(self.input.len());
                if let Some(error_at) = self.error_at {
                    self.end = self.end.min(error_at);
                }
            }
            Ok(&self.input[self.position..self.end])
        }
        fn consume(&mut self, count: usize) {
            assert!(count <= self.end - self.position);
            self.position += count;
        }
    }

    #[derive(Debug, PartialEq)]
    enum Outcome {
        Complete,
        Syntax(String),
        Io(io::ErrorKind, String),
    }
    fn outcome(result: Result<(), ScanError>) -> Outcome {
        match result {
            Ok(()) => Outcome::Complete,
            Err(ScanError::Syntax(error)) => Outcome::Syntax(error),
            Err(ScanError::Io(error)) => Outcome::Io(error.kind(), error.to_string()),
        }
    }

    /// Everything a caller or the reader can observe about one scan.
    #[derive(Debug, PartialEq)]
    struct Observed {
        outcome: Outcome,
        bytes: Vec<u8>,
        position: usize,
        refills: Vec<usize>,
        depth: usize,
    }
    fn observe(framer: Framer, input: &[u8], sizes: &[usize], error_at: Option<usize>) -> Observed {
        let mut reader = Fragmented::new(input, sizes, error_at);
        let mut scanner = Scanner::default();
        let outcome = outcome(framer.scan(&mut scanner, &mut reader));
        Observed {
            outcome,
            bytes: scanner.bytes,
            position: reader.position,
            refills: reader.refills,
            depth: scanner.frames.len(),
        }
    }
    fn differential(input: &[u8], sizes: &[usize], error_at: Option<usize>) {
        let actual = observe(Framer::Slices, input, sizes, error_at);
        for oracle in [Framer::Bytewise, Framer::BulkStrings] {
            assert_eq!(
                actual,
                observe(oracle, input, sizes, error_at),
                "{oracle:?}, input={:?}, sizes={sizes:?}, error_at={error_at:?}",
                String::from_utf8_lossy(input)
            );
        }
    }

    fn next_random(state: &mut u32) -> u32 {
        *state ^= *state << 13;
        *state ^= *state >> 17;
        *state ^= *state << 5;
        *state
    }

    const WHOLE: &[usize] = &[usize::MAX];
    const FRAGMENTS: [&[usize]; 9] = [
        &[1],
        &[2],
        &[3],
        &[7],
        &[16],
        &[31],
        &[33],
        &[8192],
        // Uneven fragments move the 16-byte blocks off any fixed alignment.
        &[5, 1, 19, 2, 64, 3],
    ];

    #[test]
    fn slices_match_bytewise_scanning_for_all_bytes_and_fragment_boundaries() {
        let mut corpus = Vec::new();
        for byte in 0..=255 {
            for (prefix, suffix) in [
                (b"\"ordinary".as_slice(), b"tail\",unread".as_slice()),
                (b"{\"ordinary", b"tail\":1}unread"),
                (b"[\"ordinary\\", b"tail\"]unread"),
                (b"[\"ordinary\\u0", b"00tail\"]unread"),
                (b"[\"ordinary\",", b"]unread"),
                (b"[12", b"]unread"),
                (b"{\"k\":", b"}unread"),
                (b"{\"k\":1", b"\"z\":2}unread"),
                (b"[tru", b"e]unread"),
                (b"-", b"1,unread"),
                (b"1.", b"5e"),
                (b"1e", b"5 unread"),
                (
                    b"\"a long ordinary run of thirty-two+ bytes, yes",
                    b" and a tail that is also fairly long......\"x",
                ),
            ] {
                corpus.push([prefix, &[byte], suffix].concat());
            }
        }
        let mut state = 0x1234_5678;
        for _ in 0..4096 {
            let length = (next_random(&mut state) % 300) as usize;
            let mut input = Vec::with_capacity(length + 32);
            input.extend_from_slice(b"[\"prefix");
            for _ in 0..length {
                // Long ordinary runs, interspersed with arbitrary malformed
                // bytes, exercise successful and rejected prefixes alike.
                let byte = next_random(&mut state) as u8;
                input.push(if byte.is_multiple_of(8) {
                    byte
                } else {
                    byte | 0x20
                });
            }
            input.extend_from_slice(b"\",null,1.2e+3]unread");
            corpus.push(input);
        }
        for input in corpus {
            for sizes in FRAGMENTS {
                differential(&input, sizes, None);
            }
        }
    }

    #[test]
    fn slices_match_bytewise_scanning_for_token_sequences() {
        // Mostly well-formed streams whose tokens land in every structural
        // position, valid or not.
        let tokens: [&[u8]; 28] = [
            b"{",
            b"}",
            b"[",
            b"]",
            b",",
            b":",
            b"\"k\"",
            b"\"va\\nlue\"",
            b"\"\\u00e9\"",
            b"\"\\ud83d\\ude00\"",
            b"true",
            b"false",
            b"null",
            b"0",
            b"-0",
            b"12",
            b"1.5",
            b"1e9",
            b"-2.5E-3",
            b" ",
            b"\n",
            b"\"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\"",
            b"\"\\q\"",
            b"01",
            b"\"\\u12G4\"",
            b"\"\xc3\xa9\xe2\x82\xac\xf0\x9f\x98\x80\"",
            b"nul",
            b"\"\\\\\\\"\\/\\b\\f\\r\\t\"",
        ];
        let mut state = 0x9e37_79b9;
        for _ in 0..20_000 {
            let count = 1 + (next_random(&mut state) % 14) as usize;
            let mut input = Vec::new();
            for _ in 0..count {
                input.extend_from_slice(tokens[next_random(&mut state) as usize % tokens.len()]);
            }
            for sizes in FRAGMENTS {
                differential(&input, sizes, None);
            }
        }
    }

    const DOCUMENTS: [&[u8]; 28] = [
        b"\"ordinary string\"",
        b"\"escaped\\u1234\\\"tail\"",
        b"[\"ordinary string\"]",
        b"{\"ordinary key\":\"ordinary value\"}",
        b"\"ordinary string\",unread",
        b"true",
        b"false",
        b"null",
        b"12.3e-4",
        b"[true,false,null,12.3e-4]tail",
        b"\"invalid\x01tail",
        b"\"invalid\\qtail",
        b"\"invalid\\u12Qtail",
        b"\"unfinished",
        b"\"escape\\",
        b"[]unread",
        b"{}unread",
        b" \t\r\n",
        b"",
        b"123,unread",
        b"{\"a\":{\"b\":[1,2,{\"c\":null}]}}tail",
        b"  {\"a\" : 1 , \"b\" : [ ] }  x",
        b"{\"k\\u00e9y\":\"\\\"\\\\\\/\\b\\f\\n\\r\\t\\u0000\\ud83d\\ude00\"},",
        b"[\"\xc3\xa9\xe2\x82\xac\xf0\x9f\x98\x80 caf\xc3\xa9 \xf0\x9f\x98\x80\xf0\x9f\x98\x80\"]",
        b"[\"\\u00e9\\u20ac\\ud83d\\ude00\xe2\x82\xac\\n\xf0\x9f\x98\x80\\\\\"]x",
        b"\"sixteen ordinary\\\"sixteen ordinary\\u0041sixteen ordinary\"",
        b"\"lone continuation \x80 and truncated lead \xf0\x9f\"]",
        b"[-0.0e+0, 1E5,2e-7 ,0.5,-12]",
    ];

    #[test]
    fn slices_preserve_refill_errors_scalar_lookahead_and_container_boundaries() {
        // The reader fails at every position, including one byte past the
        // value: the scan must fail, or finish first, exactly as before.
        for input in DOCUMENTS {
            for error_at in 0..=input.len() {
                for sizes in [&[1][..], &[2], &[7], &[16], &[8192], &[5, 1, 19]] {
                    differential(input, sizes, Some(error_at));
                }
            }
        }
        // A root scalar must attempt one more refill even after its closing
        // quote/literal. Root containers return before reading any tail byte.
        for (input, expected) in [
            (b"\"text\"".as_slice(), false),
            (b"true", false),
            (b"[]", true),
            (b"{}", true),
        ] {
            let mut reader = Fragmented::new(input, &[8192], Some(input.len()));
            assert_eq!(Scanner::default().value(&mut reader).is_ok(), expected);
            assert_eq!(reader.refills.len(), if expected { 1 } else { 2 });
        }
        let mut reader = Fragmented::new(b"123,unread", &[8192], None);
        let mut scanner = Scanner::default();
        scanner.value(&mut reader).unwrap();
        assert_eq!(scanner.bytes, b"123");
        assert_eq!(reader.fill_buf().unwrap(), b",unread");
        assert_eq!(reader.refills, [0]);
    }

    #[test]
    fn escapes_and_multibyte_text_straddle_every_buffer_boundary() {
        for input in DOCUMENTS {
            for split in 0..=input.len() + 1 {
                // One boundary at `split`, then a boundary every `split`.
                differential(input, &[split.max(1), usize::MAX], None);
                differential(input, &[split.max(1)], None);
                // Two boundaries inside one escape or one UTF-8 sequence.
                differential(input, &[split.max(1), 1, 2, usize::MAX], None);
            }
        }
    }

    #[test]
    fn truncated_and_corrupted_documents_fail_as_before() {
        let replacements = [
            0x00, 0x1f, b' ', b'"', b'\\', b'/', b',', b':', b'[', b']', b'{', b'}', b'0', b'9',
            b'-', b'+', b'.', b'e', b'E', b'u', b'n', b't', b'f', b'x', b'G', 0x7f, 0x80, 0xff,
        ];
        for input in DOCUMENTS {
            for cut in 0..=input.len() {
                for sizes in [WHOLE, &[1], &[3], &[16]] {
                    differential(&input[..cut], sizes, None);
                }
            }
            let mut corrupted = input.to_vec();
            for index in 0..input.len() {
                for replacement in replacements {
                    corrupted[index] = replacement;
                    for sizes in [WHOLE, &[1], &[5, 1, 19]] {
                        differential(&corrupted, sizes, None);
                    }
                }
                corrupted[index] = input[index];
            }
        }
    }

    #[test]
    fn nesting_depth_limit_is_unchanged() {
        for depth in [MAX_DEPTH - 1, MAX_DEPTH, MAX_DEPTH + 1] {
            let arrays = format!(
                "{}\"{}\"{}tail",
                "[".repeat(depth),
                "x".repeat(20000),
                "]".repeat(depth)
            );
            let objects = format!("{}1{}tail", "{\"k\":".repeat(depth), "}".repeat(depth));
            let mixed = format!("{}{}", "[{\"k\":".repeat(depth / 2 + 1), "}]".repeat(depth));
            for input in [arrays, objects, mixed] {
                for sizes in [&[1][..], &[7], &[8192], &[65536]] {
                    differential(input.as_bytes(), sizes, None);
                }
            }
        }
        let mut reader = io::Cursor::new("[".repeat(MAX_DEPTH + 1));
        let Err(ScanError::Syntax(error)) = Scanner::default().value(&mut reader) else {
            panic!("nesting beyond the limit was accepted");
        };
        assert_eq!(error, "invalid character '[' exceeded max depth");
        assert_eq!(reader.position(), MAX_DEPTH as u64);
    }

    fn rows(shape: &str, count: usize) -> Vec<u8> {
        let escapes = [
            "\\\"",
            "\\\\",
            "\\/",
            "\\b",
            "\\f",
            "\\n",
            "\\r",
            "\\t",
            "\\u0000",
            "\\u001f",
            "\\u00e9",
            "\\u20ac",
            "\\ud83d\\ude00",
            "a ",
        ];
        let mut body = b"[".to_vec();
        for row in 0..count {
            if row != 0 {
                body.extend_from_slice(if row % 5 == 0 { b" ,\n" } else { b"," });
            }
            let text = match shape {
                "tiny" => "x".repeat(18 - row % 3),
                "escaped" => (0..40)
                    .map(|index| escapes[(row + index) % escapes.len()])
                    .collect(),
                "ascii" => (0..250 + row % 9)
                    .map(|index| char::from(b' ' + ((row + index) % 95) as u8))
                    .filter(|character| !matches!(character, '"' | '\\'))
                    .collect(),
                _ => format!(
                    "caf\u{e9} \u{20ac}{} \u{1f600}",
                    "\u{1f600}".repeat(row % 11)
                ),
            };
            body.extend_from_slice(
                format!("{{\"i\":{row},\"s\":\"{text}\",\"n\":[null,true,-1.5e3,{{}}]}}")
                    .as_bytes(),
            );
        }
        body.extend_from_slice(b"]\n");
        body
    }

    /// The element loop of `stream_json`, with one scanner reused per body.
    fn frame_rows(framer: Framer, body: &[u8], sizes: &[usize]) -> (Vec<Vec<u8>>, Vec<usize>) {
        let mut reader = Fragmented::new(body, sizes, None);
        assert_eq!(nonspace(&mut reader).unwrap(), Some(b'['));
        reader.consume(1);
        let mut scanner = Scanner::default();
        let mut framed = Vec::new();
        loop {
            let byte = nonspace(&mut reader).unwrap().expect("closing delimiter");
            if byte == b']' {
                reader.consume(1);
                return (framed, reader.refills);
            }
            if !framed.is_empty() {
                assert_eq!(byte, b',');
                reader.consume(1);
            }
            framer.scan(&mut scanner, &mut reader).unwrap();
            framed.push(scanner.bytes.clone());
        }
    }

    #[test]
    fn whole_bodies_frame_the_same_rows_at_the_same_refill_points() {
        for shape in ["tiny", "escaped", "ascii", "unicode"] {
            let body = rows(shape, 300);
            for sizes in [&[8192][..], &[1000], &[17], &[1], &[5, 1, 19, 2, 64, 3]] {
                let actual = frame_rows(Framer::Slices, &body, sizes);
                assert_eq!(actual.0.len(), 300);
                for oracle in [Framer::Bytewise, Framer::BulkStrings] {
                    assert_eq!(
                        actual,
                        frame_rows(oracle, &body, sizes),
                        "{oracle:?} {shape} {sizes:?}"
                    );
                }
            }
            // The body is itself one value: an array of rows.
            for sizes in [&[8192][..], &[4096], &[17], &[3]] {
                differential(&body, sizes, None);
                differential(&body[1..], sizes, None);
            }
        }
    }

    #[test]
    fn ordinary_runs_stop_at_the_first_special_byte() {
        let stops = [0x00, 0x1f, b'"', b'\\'];
        let ordinary = [0x20, 0x21, 0x23, 0x5b, 0x5d, 0x7f, 0x80, 0xc3, 0xff];
        for length in 0..=70 {
            for filler in ordinary {
                let mut bytes = vec![filler; length];
                assert_eq!(ordinary_run(&bytes), length);
                for index in 0..length {
                    for stop in stops {
                        bytes[index] = stop;
                        assert_eq!(ordinary_run(&bytes), index, "{length} {index} {stop}");
                        // A later special byte does not move the first one.
                        let kept = std::mem::replace(&mut bytes[length - 1], b'"');
                        assert_eq!(ordinary_run(&bytes), index.min(length - 1));
                        bytes[length - 1] = kept;
                    }
                    bytes[index] = filler;
                }
            }
        }
    }

    #[test]
    fn only_complete_valid_escapes_are_accepted_whole() {
        for (bytes, length) in [
            (b"\\n".as_slice(), 2),
            (b"\\\"tail", 2),
            (b"\\\\", 2),
            (b"\\/x", 2),
            (b"\\u00e9", 6),
            (b"\\uABcd\"", 6),
            // Left to the byte-at-a-time transitions: incomplete or invalid.
            (b"\\", 0),
            (b"\\u", 0),
            (b"\\u00e", 0),
            (b"\\u00eg", 0),
            (b"\\ug0e9", 0),
            (b"\\q", 0),
            (b"\\U00e9", 0),
            (b"\\\x00", 0),
        ] {
            assert_eq!(buffered_escape(bytes), length, "{bytes:?}");
        }
    }

    #[test]
    fn reused_scanner_keeps_capacity_and_leaves_delimiters_unread() {
        let mut scanner = Scanner::default();
        let large = format!("[\"{}\"]tail", "x".repeat(4096));
        let mut reader = io::Cursor::new(large.as_bytes());
        scanner.value(&mut reader).unwrap();
        assert_eq!(scanner.bytes, &large.as_bytes()[..large.len() - 4]);
        assert_eq!(peek(&mut reader).unwrap(), Some(b't'));
        let capacity = scanner.bytes.capacity();
        let frame_capacity = scanner.frames.capacity();
        let mut reader = io::Cursor::new(b"123,tail");
        scanner.value(&mut reader).unwrap();
        assert_eq!(scanner.bytes, b"123");
        assert_eq!(peek(&mut reader).unwrap(), Some(b','));
        assert_eq!(scanner.bytes.capacity(), capacity);
        assert_eq!(scanner.frames.capacity(), frame_capacity);
    }
}
