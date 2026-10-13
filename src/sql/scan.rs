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
            let byte = match peek(reader).map_err(ScanError::Io)? {
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
