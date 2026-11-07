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
        self.value_impl::<R, true>(reader)
    }

    fn value_impl<R: BufRead, const BULK_STRINGS: bool>(
        &mut self,
        reader: &mut R,
    ) -> Result<(), ScanError> {
        self.bytes.clear();
        self.frames.clear();
        let bytes = &mut self.bytes;
        let frames = &mut self.frames;
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
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fragmented<'a> {
        input: &'a [u8],
        position: usize,
        end: usize,
        fragment: usize,
        error_at: Option<usize>,
        refills: Vec<usize>,
    }
    impl<'a> Fragmented<'a> {
        fn new(input: &'a [u8], fragment: usize, error_at: Option<usize>) -> Self {
            Self {
                input,
                position: 0,
                end: 0,
                fragment,
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
                self.end = (self.position + self.fragment).min(self.input.len());
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
    fn differential(input: &[u8], fragment: usize, error_at: Option<usize>) {
        let mut original_reader = Fragmented::new(input, fragment, error_at);
        let mut optimized_reader = Fragmented::new(input, fragment, error_at);
        let mut original = Scanner::default();
        let mut optimized = Scanner::default();
        // Disabling the run copy keeps the former one-byte state machine.
        let expected = outcome(original.value_impl::<_, false>(&mut original_reader));
        let actual = outcome(optimized.value(&mut optimized_reader));
        assert_eq!(
            actual, expected,
            "input={input:?}, fragment={fragment}, error_at={error_at:?}"
        );
        assert_eq!(optimized.bytes, original.bytes);
        assert_eq!(optimized_reader.position, original_reader.position);
        assert_eq!(optimized_reader.refills, original_reader.refills);
        assert_eq!(optimized.frames.len(), original.frames.len());
    }
    fn next_random(state: &mut u32) -> u8 {
        *state ^= *state << 13;
        *state ^= *state >> 17;
        *state ^= *state << 5;
        *state as u8
    }

    #[test]
    fn string_runs_match_bytewise_scanning_for_all_bytes_and_fragment_boundaries() {
        let mut corpus = Vec::new();
        for byte in 0..=255 {
            for (prefix, suffix) in [
                (b"\"ordinary".as_slice(), b"tail\",unread".as_slice()),
                (b"{\"ordinary", b"tail\":1}unread"),
                (b"[\"ordinary\\", b"tail\"]unread"),
                (b"[\"ordinary\\u0", b"00tail\"]unread"),
                (b"[\"ordinary\",", b"]unread"),
                (b"[12", b"]unread"),
            ] {
                corpus.push([prefix, &[byte], suffix].concat());
            }
        }
        let mut state = 0x1234_5678;
        for _ in 0..2048 {
            let length = usize::from(next_random(&mut state));
            let mut input = Vec::with_capacity(length + 16);
            input.extend_from_slice(b"[\"prefix");
            for _ in 0..length {
                // Long ordinary runs, interspersed with arbitrary malformed
                // bytes, exercise successful and rejected prefixes alike.
                let byte = next_random(&mut state);
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
            for fragment in [1, 2, 3, 7, 31, 8192] {
                differential(&input, fragment, None);
            }
        }
        for depth in [9999, 10000, 10001] {
            let input = format!(
                "{}\"{}\"{}tail",
                "[".repeat(depth),
                "x".repeat(20000),
                "]".repeat(depth)
            );
            for fragment in [1, 7, 8192, 65536] {
                differential(input.as_bytes(), fragment, None);
            }
        }
    }

    #[test]
    fn string_runs_preserve_refill_errors_scalar_lookahead_and_container_boundaries() {
        for input in [
            b"\"ordinary string\"".as_slice(),
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
        ] {
            for error_at in 0..=input.len() {
                for fragment in [1, 2, 7, 8192] {
                    differential(input, fragment, Some(error_at));
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
            let mut reader = Fragmented::new(input, 8192, Some(input.len()));
            assert_eq!(Scanner::default().value(&mut reader).is_ok(), expected);
        }
        let mut reader = Fragmented::new(b"123,unread", 8192, None);
        let mut scanner = Scanner::default();
        scanner.value(&mut reader).unwrap();
        assert_eq!(scanner.bytes, b"123");
        assert_eq!(reader.fill_buf().unwrap(), b",unread");
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
