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
        let buffer = reader.fill_buf()?;
        let count = buffer.iter().take_while(|&&byte| space(byte)).count();
        let next = buffer.get(count).copied();
        let done = next.is_some() || buffer.is_empty();
        reader.consume(count);
        if done {
            return Ok(next);
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
    pub(super) fn value<R: BufRead>(&mut self, reader: &mut R) -> Result<(), ScanError> {
        self.bytes.clear();
        self.frames.clear();
        let bytes = &mut self.bytes;
        let frames = &mut self.frames;
        let mut lexeme = Lexeme::Value;
        loop {
            // Scan runs within the current buffer, leaving syntax-sensitive bytes
            // to the state machine below. In particular, controls and escapes
            // must be checked before requesting any more input.
            let buffer = reader.fill_buf().map_err(ScanError::Io)?;
            let count = match lexeme {
                Lexeme::String { .. } => buffer
                    .iter()
                    .position(|&byte| byte < 0x20 || byte == b'"' || byte == b'\\')
                    .unwrap_or(buffer.len()),
                Lexeme::Value => buffer.iter().take_while(|&&byte| space(byte)).count(),
                _ => 0,
            };
            if count != 0 {
                bytes.extend_from_slice(&buffer[..count]);
                reader.consume(count);
                continue;
            }
            let byte = match buffer.first().copied() {
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
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Expose fixed-size fragments and optionally fail at the next refill.
    struct Fragments<'a> {
        input: &'a [u8],
        position: usize,
        width: usize,
        fail_at_end: bool,
        fills: usize,
    }

    impl io::Read for Fragments<'_> {
        fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
            let buffer = self.fill_buf()?;
            let count = output.len().min(buffer.len());
            output[..count].copy_from_slice(&buffer[..count]);
            self.consume(count);
            Ok(count)
        }
    }

    impl BufRead for Fragments<'_> {
        fn fill_buf(&mut self) -> io::Result<&[u8]> {
            self.fills += 1;
            if self.position == self.input.len() && self.fail_at_end {
                return Err(io::Error::new(io::ErrorKind::TimedOut, "deadline"));
            }
            let end = (self.position + self.width).min(self.input.len());
            Ok(&self.input[self.position..end])
        }

        fn consume(&mut self, count: usize) {
            self.position += count;
        }
    }

    fn fragments(input: &[u8], width: usize, fail_at_end: bool) -> Fragments<'_> {
        Fragments {
            input,
            position: 0,
            width,
            fail_at_end,
            fills: 0,
        }
    }

    #[test]
    fn framing_is_independent_of_fragment_boundaries() {
        let cases: &[(&[u8], usize)] = &[
            (
                r#"  {"a":"hello\nworld\u1234é","a":[true,false,null,-12.34e+5]}tail"#.as_bytes(),
                0,
            ),
            (br#""hello\\\"world"!"#, 0),
            (b" \t\r\n[ 1 , { \"key\" : \"text\" } ]tail", 0),
            (b"123.5e-2,", 8),
        ];
        for &(input, explicit_end) in cases {
            let expected_end = if explicit_end == 0 {
                if input.ends_with(b"tail") {
                    input.len() - 4
                } else {
                    input.len() - 1
                }
            } else {
                explicit_end
            };
            for width in 1..=input.len() {
                let mut reader = fragments(input, width, false);
                let scanned = value(&mut reader).unwrap();
                assert_eq!(scanned, &input[..expected_end], "width {width}");
                assert_eq!(reader.position, expected_end, "width {width}");
            }
        }
    }

    #[test]
    fn string_errors_leave_the_offending_byte_unconsumed() {
        for control in 0..=31 {
            let input = [b'"', b'a', control];
            for width in 1..=input.len() {
                let mut reader = fragments(&input, width, true);
                let mut scanner = Scanner::default();
                let error = scanner.value(&mut reader).unwrap_err();
                assert!(matches!(error, ScanError::Syntax(ref message)
                    if message == &invalid(control, "in string literal")));
                assert_eq!(scanner.bytes, b"\"a");
                assert_eq!(reader.position, 2);
            }
        }
        for (input, context) in [
            (&b"\"abc\\q"[..], "in string escape code"),
            (&b"\"abc\\u12q"[..], "in \\u hexadecimal character escape"),
        ] {
            for width in 1..=input.len() {
                let mut reader = fragments(input, width, true);
                let error = value(&mut reader).unwrap_err();
                assert!(matches!(error, ScanError::Syntax(ref message)
                    if message == &invalid(b'q', context)));
                assert_eq!(reader.position, input.len() - 1);
            }
        }
    }

    #[test]
    fn eof_and_read_errors_after_bulk_spans_preserve_framing() {
        for input in [&b"\"ordinary text"[..], &b" \t\r\n"[..]] {
            for width in 1..=input.len() {
                let mut reader = fragments(input, width, true);
                assert!(matches!(value(&mut reader), Err(ScanError::Io(error))
                    if error.kind() == io::ErrorKind::TimedOut));
                let mut reader = fragments(input, width, false);
                let expected = if input[0] == b'"' {
                    "unexpected EOF"
                } else {
                    "EOF"
                };
                assert!(matches!(value(&mut reader), Err(ScanError::Syntax(message))
                    if message == expected));
            }
        }
        // A closed container returns without a refill; a root string still
        // requests its established one-byte lookahead, propagating failure.
        let mut reader = fragments(b"[\"abc\"]", 32, true);
        assert!(value(&mut reader).is_ok());
        let mut reader = fragments(b"\"abc\"", 32, true);
        assert!(matches!(value(&mut reader), Err(ScanError::Io(_))));
    }

    #[test]
    fn ordinary_spans_use_buffer_sized_reads() {
        let input = format!("[{}\"{}\"]", " ".repeat(4096), "x".repeat(8192));
        let mut reader = fragments(input.as_bytes(), 1024, false);
        assert_eq!(value(&mut reader).unwrap(), input.as_bytes());
        assert!(reader.fills < 32, "{} refill calls", reader.fills);
        let input = format!("{}x", " \t\r\n".repeat(1024));
        let mut reader = fragments(input.as_bytes(), 128, false);
        assert_eq!(nonspace(&mut reader).unwrap(), Some(b'x'));
        assert_eq!(reader.position, input.len() - 1);
        assert!(reader.fills < 40);
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
