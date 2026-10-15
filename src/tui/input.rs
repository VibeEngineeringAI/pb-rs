//! Terminal input events with Bubble Tea v1.2.2 key semantics.
//!
//! Keys keep tea.Key's shape (type, runes, alt, paste) and `Key::name` is
//! exactly tea.Key.String(), so bubbles `key.Binding` string matches port
//! one to one. `Parser` reproduces readAnsiInputs/detectOneMsg: decoding is
//! per read chunk, so a lone ESC at the end of a read is `esc` while ESC
//! followed by a rune in the same read is `alt+<rune>`, and consecutive
//! printable runes in one read become a single multi-rune key.

use super::terminal::Size;
#[cfg(any(unix, test))]
use std::collections::HashMap;
#[cfg(any(unix, test))]
use std::sync::OnceLock;

/// One message delivered to a session runner.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Event {
    Key(Key),
    /// tea.WindowSizeMsg; only produced when stdout is a terminal.
    Resize(Size),
    /// SIGINT/SIGTERM: tea.QuitMsg. End the loop without updating the model,
    /// then close the session with the current view. Not produced on
    /// Windows, where raw-mode Ctrl+C arrives as a key.
    #[cfg_attr(windows, allow(dead_code))]
    Quit,
    /// Error that makes tea.Program.Run return, e.g. "error reading input: ...".
    InputError(String),
    /// A thread panicked outside a `terminal::recovered` scope. The terminal
    /// is already restored when this is delivered (ahead of the terminal's
    /// own pending messages; stamped at peek time, so a runner merging by
    /// stamp may first take completions stamped earlier, as Go's eventLoop
    /// `select` may); no frame can be drawn any more. The payload is the panic
    /// message (Go's recovered value). Workflows choose the Go outcome
    /// (tea recoverFromPanic: kill, `Caught panic:` text, ErrProgramKilled
    /// for command goroutines). After it, the queue reports closure.
    Panicked(String),
    /// Mouse, focus, unknown CSI or invalid input byte. Go hands these to the
    /// model, whose screens ignore them.
    Unknown,
}

/// tea.Key.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Key {
    pub(crate) code: KeyCode,
    /// Non-empty for `Runes` and `Space`.
    pub(crate) runes: Vec<char>,
    pub(crate) alt: bool,
    /// Text delivered by bracketed paste.
    pub(crate) paste: bool,
}

/// tea.KeyType.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) enum KeyCode {
    Runes,
    Space,
    /// A C0 control byte without a dedicated variant (0..=31 except 9, 13
    /// and 27); 0 is "ctrl+@" (Ctrl+Space on most terminals).
    Ctrl(u8),
    Tab,
    Enter,
    Esc,
    /// DEL (0x7f).
    Backspace,
    Up,
    Down,
    Right,
    Left,
    ShiftTab,
    Home,
    End,
    PgUp,
    PgDown,
    CtrlPgUp,
    CtrlPgDown,
    Delete,
    Insert,
    CtrlUp,
    CtrlDown,
    CtrlRight,
    CtrlLeft,
    CtrlHome,
    CtrlEnd,
    ShiftUp,
    ShiftDown,
    ShiftRight,
    ShiftLeft,
    ShiftHome,
    ShiftEnd,
    CtrlShiftUp,
    CtrlShiftDown,
    CtrlShiftLeft,
    CtrlShiftRight,
    CtrlShiftHome,
    CtrlShiftEnd,
    /// F1..=F20.
    F(u8),
}

impl KeyCode {
    /// The KeyType whose value is a control byte.
    pub(crate) fn from_control(byte: u8) -> KeyCode {
        match byte {
            9 => KeyCode::Tab,
            13 => KeyCode::Enter,
            27 => KeyCode::Esc,
            127 => KeyCode::Backspace,
            other => KeyCode::Ctrl(other),
        }
    }

    /// keyNames.
    fn name(self) -> std::borrow::Cow<'static, str> {
        use KeyCode::*;
        let name = match self {
            Runes => "runes",
            Space => " ",
            Tab => "tab",
            Enter => "enter",
            Esc => "esc",
            Backspace => "backspace",
            Ctrl(0) => "ctrl+@",
            Ctrl(byte @ 1..=26) => {
                return format!("ctrl+{}", char::from(b'a' + byte - 1)).into();
            }
            Ctrl(28) => "ctrl+\\",
            Ctrl(29) => "ctrl+]",
            Ctrl(30) => "ctrl+^",
            Ctrl(31) => "ctrl+_",
            Ctrl(_) => "",
            Up => "up",
            Down => "down",
            Right => "right",
            Left => "left",
            ShiftTab => "shift+tab",
            Home => "home",
            End => "end",
            PgUp => "pgup",
            PgDown => "pgdown",
            CtrlPgUp => "ctrl+pgup",
            CtrlPgDown => "ctrl+pgdown",
            Delete => "delete",
            Insert => "insert",
            CtrlUp => "ctrl+up",
            CtrlDown => "ctrl+down",
            CtrlRight => "ctrl+right",
            CtrlLeft => "ctrl+left",
            CtrlHome => "ctrl+home",
            CtrlEnd => "ctrl+end",
            ShiftUp => "shift+up",
            ShiftDown => "shift+down",
            ShiftRight => "shift+right",
            ShiftLeft => "shift+left",
            ShiftHome => "shift+home",
            ShiftEnd => "shift+end",
            CtrlShiftUp => "ctrl+shift+up",
            CtrlShiftDown => "ctrl+shift+down",
            CtrlShiftLeft => "ctrl+shift+left",
            CtrlShiftRight => "ctrl+shift+right",
            CtrlShiftHome => "ctrl+shift+home",
            CtrlShiftEnd => "ctrl+shift+end",
            F(n @ 1..=20) => return format!("f{n}").into(),
            F(_) => "",
        };
        name.into()
    }
}

impl Key {
    pub(crate) fn new(code: KeyCode) -> Key {
        Key {
            code,
            runes: if code == KeyCode::Space {
                vec![' ']
            } else {
                Vec::new()
            },
            alt: false,
            paste: false,
        }
    }

    #[cfg(test)]
    pub(crate) fn runes(text: &str) -> Key {
        Key {
            code: KeyCode::Runes,
            runes: text.chars().collect(),
            alt: false,
            paste: false,
        }
    }

    /// tea.Key.String().
    pub(crate) fn name(&self) -> String {
        let mut name = String::new();
        if self.alt {
            name.push_str("alt+");
        }
        if self.code == KeyCode::Runes {
            if self.paste {
                name.push('[');
            }
            name.extend(&self.runes);
            if self.paste {
                name.push(']');
            }
            return name;
        }
        let code = self.code.name();
        if code.is_empty() {
            return String::new();
        }
        name.push_str(&code);
        name
    }

    /// bubbles key.Matches against one binding's key list.
    pub(crate) fn is(&self, names: &[&str]) -> bool {
        let name = self.name();
        names.iter().any(|candidate| *candidate == name)
    }
}

#[cfg(any(unix, test))]
/// tea's `sequences` map.
const SEQUENCES: &[(&[u8], KeyCode, bool)] = {
    use KeyCode::*;
    &[
        (b"\x1b[A", Up, false),
        (b"\x1b[B", Down, false),
        (b"\x1b[C", Right, false),
        (b"\x1b[D", Left, false),
        (b"\x1b[1;2A", ShiftUp, false),
        (b"\x1b[1;2B", ShiftDown, false),
        (b"\x1b[1;2C", ShiftRight, false),
        (b"\x1b[1;2D", ShiftLeft, false),
        (b"\x1b[OA", ShiftUp, false),
        (b"\x1b[OB", ShiftDown, false),
        (b"\x1b[OC", ShiftRight, false),
        (b"\x1b[OD", ShiftLeft, false),
        (b"\x1b[a", ShiftUp, false),
        (b"\x1b[b", ShiftDown, false),
        (b"\x1b[c", ShiftRight, false),
        (b"\x1b[d", ShiftLeft, false),
        (b"\x1b[1;3A", Up, true),
        (b"\x1b[1;3B", Down, true),
        (b"\x1b[1;3C", Right, true),
        (b"\x1b[1;3D", Left, true),
        (b"\x1b[1;4A", ShiftUp, true),
        (b"\x1b[1;4B", ShiftDown, true),
        (b"\x1b[1;4C", ShiftRight, true),
        (b"\x1b[1;4D", ShiftLeft, true),
        (b"\x1b[1;5A", CtrlUp, false),
        (b"\x1b[1;5B", CtrlDown, false),
        (b"\x1b[1;5C", CtrlRight, false),
        (b"\x1b[1;5D", CtrlLeft, false),
        (b"\x1b[Oa", CtrlUp, true),
        (b"\x1b[Ob", CtrlDown, true),
        (b"\x1b[Oc", CtrlRight, true),
        (b"\x1b[Od", CtrlLeft, true),
        (b"\x1b[1;6A", CtrlShiftUp, false),
        (b"\x1b[1;6B", CtrlShiftDown, false),
        (b"\x1b[1;6C", CtrlShiftRight, false),
        (b"\x1b[1;6D", CtrlShiftLeft, false),
        (b"\x1b[1;7A", CtrlUp, true),
        (b"\x1b[1;7B", CtrlDown, true),
        (b"\x1b[1;7C", CtrlRight, true),
        (b"\x1b[1;7D", CtrlLeft, true),
        (b"\x1b[1;8A", CtrlShiftUp, true),
        (b"\x1b[1;8B", CtrlShiftDown, true),
        (b"\x1b[1;8C", CtrlShiftRight, true),
        (b"\x1b[1;8D", CtrlShiftLeft, true),
        (b"\x1b[Z", ShiftTab, false),
        (b"\x1b[2~", Insert, false),
        (b"\x1b[3;2~", Insert, true),
        (b"\x1b[3~", Delete, false),
        (b"\x1b[3;3~", Delete, true),
        (b"\x1b[5~", PgUp, false),
        (b"\x1b[5;3~", PgUp, true),
        (b"\x1b[5;5~", CtrlPgUp, false),
        (b"\x1b[5^", CtrlPgUp, false),
        (b"\x1b[5;7~", CtrlPgUp, true),
        (b"\x1b[6~", PgDown, false),
        (b"\x1b[6;3~", PgDown, true),
        (b"\x1b[6;5~", CtrlPgDown, false),
        (b"\x1b[6^", CtrlPgDown, false),
        (b"\x1b[6;7~", CtrlPgDown, true),
        (b"\x1b[1~", Home, false),
        (b"\x1b[H", Home, false),
        (b"\x1b[1;3H", Home, true),
        (b"\x1b[1;5H", CtrlHome, false),
        (b"\x1b[1;7H", CtrlHome, true),
        (b"\x1b[1;2H", ShiftHome, false),
        (b"\x1b[1;4H", ShiftHome, true),
        (b"\x1b[1;6H", CtrlShiftHome, false),
        (b"\x1b[1;8H", CtrlShiftHome, true),
        (b"\x1b[4~", End, false),
        (b"\x1b[F", End, false),
        (b"\x1b[1;3F", End, true),
        (b"\x1b[1;5F", CtrlEnd, false),
        (b"\x1b[1;7F", CtrlEnd, true),
        (b"\x1b[1;2F", ShiftEnd, false),
        (b"\x1b[1;4F", ShiftEnd, true),
        (b"\x1b[1;6F", CtrlShiftEnd, false),
        (b"\x1b[1;8F", CtrlShiftEnd, true),
        (b"\x1b[7~", Home, false),
        (b"\x1b[7^", CtrlHome, false),
        (b"\x1b[7$", ShiftHome, false),
        (b"\x1b[7@", CtrlShiftHome, false),
        (b"\x1b[8~", End, false),
        (b"\x1b[8^", CtrlEnd, false),
        (b"\x1b[8$", ShiftEnd, false),
        (b"\x1b[8@", CtrlShiftEnd, false),
        (b"\x1b[[A", F(1), false),
        (b"\x1b[[B", F(2), false),
        (b"\x1b[[C", F(3), false),
        (b"\x1b[[D", F(4), false),
        (b"\x1b[[E", F(5), false),
        (b"\x1bOP", F(1), false),
        (b"\x1bOQ", F(2), false),
        (b"\x1bOR", F(3), false),
        (b"\x1bOS", F(4), false),
        (b"\x1b[1;3P", F(1), true),
        (b"\x1b[1;3Q", F(2), true),
        (b"\x1b[1;3R", F(3), true),
        (b"\x1b[1;3S", F(4), true),
        (b"\x1b[11~", F(1), false),
        (b"\x1b[12~", F(2), false),
        (b"\x1b[13~", F(3), false),
        (b"\x1b[14~", F(4), false),
        (b"\x1b[15~", F(5), false),
        (b"\x1b[15;3~", F(5), true),
        (b"\x1b[17~", F(6), false),
        (b"\x1b[18~", F(7), false),
        (b"\x1b[19~", F(8), false),
        (b"\x1b[20~", F(9), false),
        (b"\x1b[21~", F(10), false),
        (b"\x1b[17;3~", F(6), true),
        (b"\x1b[18;3~", F(7), true),
        (b"\x1b[19;3~", F(8), true),
        (b"\x1b[20;3~", F(9), true),
        (b"\x1b[21;3~", F(10), true),
        (b"\x1b[23~", F(11), false),
        (b"\x1b[24~", F(12), false),
        (b"\x1b[23;3~", F(11), true),
        (b"\x1b[24;3~", F(12), true),
        (b"\x1b[1;2P", F(13), false),
        (b"\x1b[1;2Q", F(14), false),
        (b"\x1b[25~", F(13), false),
        (b"\x1b[26~", F(14), false),
        (b"\x1b[25;3~", F(13), true),
        (b"\x1b[26;3~", F(14), true),
        (b"\x1b[1;2R", F(15), false),
        (b"\x1b[1;2S", F(16), false),
        (b"\x1b[28~", F(15), false),
        (b"\x1b[29~", F(16), false),
        (b"\x1b[28;3~", F(15), true),
        (b"\x1b[29;3~", F(16), true),
        (b"\x1b[15;2~", F(17), false),
        (b"\x1b[17;2~", F(18), false),
        (b"\x1b[18;2~", F(19), false),
        (b"\x1b[19;2~", F(20), false),
        (b"\x1b[31~", F(17), false),
        (b"\x1b[32~", F(18), false),
        (b"\x1b[33~", F(19), false),
        (b"\x1b[34~", F(20), false),
        (b"\x1bOA", Up, false),
        (b"\x1bOB", Down, false),
        (b"\x1bOC", Right, false),
        (b"\x1bOD", Left, false),
    ]
};

#[cfg(any(unix, test))]
struct SequenceTable {
    keys: HashMap<Vec<u8>, Key>,
    /// Distinct sequence lengths, longest first.
    lengths: Vec<usize>,
}

#[cfg(any(unix, test))]
/// tea's extSequences/seqLengths: sequences plus ESC-prefixed alt variants,
/// control bytes (except NUL and ESC) with alt variants, space, alt+esc.
fn table() -> &'static SequenceTable {
    static TABLE: OnceLock<SequenceTable> = OnceLock::new();
    TABLE.get_or_init(|| {
        let mut keys = HashMap::new();
        let mut add = |bytes: Vec<u8>, code: KeyCode, alt: bool| {
            let mut key = Key::new(code);
            key.alt = alt;
            keys.insert(bytes, key);
        };
        for &(sequence, code, alt) in SEQUENCES {
            add(sequence.to_vec(), code, alt);
            if !alt {
                add([b"\x1b".as_slice(), sequence].concat(), code, true);
            }
        }
        for byte in (1..=31u8).chain([127]) {
            if byte == 27 {
                continue;
            }
            add(vec![byte], KeyCode::from_control(byte), false);
            add(vec![0x1b, byte], KeyCode::from_control(byte), true);
        }
        add(b" ".to_vec(), KeyCode::Space, false);
        add(b"\x1b ".to_vec(), KeyCode::Space, true);
        add(b"\x1b\x1b".to_vec(), KeyCode::Esc, true);
        let mut lengths: Vec<usize> = keys.keys().map(Vec::len).collect();
        lengths.sort_unstable_by(|a, b| b.cmp(a));
        lengths.dedup();
        SequenceTable { keys, lengths }
    })
}

#[cfg(any(unix, test))]
/// tea's read buffer size; a read that fills it may continue in the next read.
pub(crate) const READ_BUFFER: usize = 256;

#[cfg(any(unix, test))]
/// Incremental readAnsiInputs decoder. Feed each read() result in order.
#[derive(Default)]
pub(crate) struct Parser {
    leftover: Vec<u8>,
}

#[cfg(any(unix, test))]
impl Parser {
    pub(crate) fn new() -> Parser {
        Parser::default()
    }

    /// Decode one read. `full_read` is `numBytes == len(buf)`: the end of
    /// the chunk may not be an event boundary, so a trailing partial rune
    /// run is kept for the next read.
    pub(crate) fn feed(&mut self, read: &[u8], full_read: bool) -> Vec<Event> {
        let mut bytes = std::mem::take(&mut self.leftover);
        bytes.extend_from_slice(read);
        let mut events = Vec::new();
        let mut index = 0;
        while index < bytes.len() {
            match detect_one(&bytes[index..], full_read) {
                Some((width, event)) => {
                    events.push(event);
                    index += width;
                }
                None => {
                    self.leftover = bytes[index..].to_vec();
                    break;
                }
            }
        }
        events
    }
}

/// Decode one complete chunk (a short read); reducer tests use it to build keys.
#[cfg(test)]
pub(crate) fn parse(chunk: &[u8]) -> Vec<Event> {
    Parser::new().feed(chunk, false)
}

#[cfg(any(unix, test))]
/// detectOneMsg: Some((consumed, event)), or None when more input is needed.
fn detect_one(b: &[u8], can_have_more: bool) -> Option<(usize, Event)> {
    // X10 and SGR mouse events.
    if b.len() >= 6 && b[0] == 0x1b && b[1] == b'[' {
        match b[2] {
            b'M' => return Some((6, Event::Unknown)),
            b'<' => {
                if let Some(end) = find_sgr_mouse(&b[3..]) {
                    return Some((end + 3, Event::Unknown));
                }
            }
            _ => {}
        }
    }
    // Focus reports compare the whole remaining input.
    if b == b"\x1b[I" || b == b"\x1b[O" {
        return Some((3, Event::Unknown));
    }
    if let Some(found) = detect_bracketed_paste(b) {
        return found;
    }
    if let Some(found) = detect_sequence(b) {
        return Some(found);
    }

    let mut alt = false;
    let mut i = 0;
    if b[0] == 0x1b {
        alt = true;
        i += 1;
    }
    if i < b.len() && b[i] == 0 {
        let mut key = Key::new(KeyCode::Ctrl(0));
        key.alt = alt;
        return Some((i + 1, Event::Key(key)));
    }
    let mut runes = Vec::new();
    while i < b.len() {
        let (rune, width) = decode_rune(&b[i..]);
        match rune {
            Some(r) if (r as u32) > 31 && r != '\u{7f}' && r != ' ' => {
                runes.push(r);
                i += width;
                if alt {
                    break;
                }
            }
            _ => break,
        }
    }
    if i >= b.len() && can_have_more {
        return None;
    }
    if !runes.is_empty() {
        return Some((
            i,
            Event::Key(Key {
                code: KeyCode::Runes,
                runes,
                alt,
                paste: false,
            }),
        ));
    }
    if alt && b.len() == 1 {
        return Some((1, Event::Key(Key::new(KeyCode::Esc))));
    }
    Some((1, Event::Unknown))
}

#[cfg(any(unix, test))]
/// detectSequence: longest known prefix, else an unknown CSI sequence.
fn detect_sequence(input: &[u8]) -> Option<(usize, Event)> {
    let table = table();
    for &length in &table.lengths {
        if length > input.len() {
            continue;
        }
        if let Some(key) = table.keys.get(&input[..length]) {
            return Some((length, Event::Key(key.clone())));
        }
    }
    // ^\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]
    if input.len() >= 2 && input[0] == 0x1b && input[1] == b'[' {
        let mut i = 2;
        while i < input.len() && (0x30..=0x3f).contains(&input[i]) {
            i += 1;
        }
        while i < input.len() && (0x20..=0x2f).contains(&input[i]) {
            i += 1;
        }
        if i < input.len() && (0x40..=0x7e).contains(&input[i]) {
            return Some((i + 1, Event::Unknown));
        }
    }
    None
}

#[cfg(any(unix, test))]
/// detectBracketedPaste: None if not a paste; Some(None) when the end marker
/// has not arrived yet (Go waits for more input even after a short read).
fn detect_bracketed_paste(input: &[u8]) -> Option<Option<(usize, Event)>> {
    const START: &[u8] = b"\x1b[200~";
    const END: &[u8] = b"\x1b[201~";
    let rest = input.strip_prefix(START)?;
    let Some(index) = rest.windows(END.len()).position(|window| window == END) else {
        return Some(None);
    };
    let mut paste = &rest[..index];
    let mut runes = Vec::new();
    while !paste.is_empty() {
        let (rune, width) = decode_rune(paste);
        if let Some(rune) = rune {
            runes.push(rune);
        }
        paste = &paste[width..];
    }
    Some(Some((
        START.len() + index + END.len(),
        Event::Key(Key {
            code: KeyCode::Runes,
            runes,
            alt: false,
            paste: true,
        }),
    )))
}

#[cfg(any(unix, test))]
/// `(\d+);(\d+);(\d+)([Mm])` searched anywhere in `b`; returns the match end.
fn find_sgr_mouse(b: &[u8]) -> Option<usize> {
    let digits = |mut i: usize| {
        let start = i;
        while i < b.len() && b[i].is_ascii_digit() {
            i += 1;
        }
        (i > start).then_some(i)
    };
    (0..b.len()).find_map(|start| {
        let mut i = digits(start)?;
        for _ in 0..2 {
            if b.get(i) != Some(&b';') {
                return None;
            }
            i = digits(i + 1)?;
        }
        matches!(b.get(i), Some(b'M' | b'm')).then_some(i + 1)
    })
}

#[cfg(any(unix, test))]
/// utf8.DecodeRune: `(None, 1)` for invalid or truncated input; an encoded
/// U+FFFD also reports RuneError, so it is treated as invalid like Go.
pub(crate) fn decode_rune(b: &[u8]) -> (Option<char>, usize) {
    let length = match b[0] {
        0x00..=0x7f => 1,
        0xc2..=0xdf => 2,
        0xe0..=0xef => 3,
        0xf0..=0xf4 => 4,
        _ => return (None, 1),
    };
    match b.get(..length).map(std::str::from_utf8) {
        Some(Ok(text)) => {
            let rune = text.chars().next().expect("one char");
            if rune == char::REPLACEMENT_CHARACTER {
                (None, length)
            } else {
                (Some(rune), length)
            }
        }
        _ => (None, 1),
    }
}

/// Windows console events (crossterm) mapped to tea keys. Bubble Tea uses
/// its own coninput translation there; parity is not yet characterized.
#[cfg(windows)]
pub(crate) fn from_crossterm(event: crossterm::event::Event) -> Option<Event> {
    use crossterm::event::{Event as C, KeyCode as K, KeyEventKind, KeyModifiers as M};
    let key = match event {
        C::Key(key) if key.kind != KeyEventKind::Release => key,
        C::Resize(width, height) => return Some(Event::Resize(Size { width, height })),
        C::Paste(text) => {
            return Some(Event::Key(Key {
                code: KeyCode::Runes,
                runes: text.chars().collect(),
                alt: false,
                paste: true,
            }));
        }
        C::Key(_) => return None,
        _ => return Some(Event::Unknown),
    };
    let ctrl = key.modifiers.contains(M::CONTROL);
    let shift = key.modifiers.contains(M::SHIFT);
    let alt = key.modifiers.contains(M::ALT);
    let code = match key.code {
        K::Char(' ') if ctrl => KeyCode::Ctrl(0),
        K::Char(' ') => KeyCode::Space,
        K::Char(c) if ctrl && c.is_ascii_alphabetic() => {
            KeyCode::from_control(c.to_ascii_lowercase() as u8 - b'a' + 1)
        }
        K::Char('@') if ctrl => KeyCode::Ctrl(0),
        K::Char('\\') if ctrl => KeyCode::Ctrl(28),
        K::Char(']') if ctrl => KeyCode::Ctrl(29),
        K::Char('^') if ctrl => KeyCode::Ctrl(30),
        K::Char('_') if ctrl => KeyCode::Ctrl(31),
        K::Char(c) => {
            return Some(Event::Key(Key {
                code: KeyCode::Runes,
                runes: vec![c],
                alt,
                paste: false,
            }));
        }
        K::Enter => KeyCode::Enter,
        K::Tab => KeyCode::Tab,
        K::BackTab => KeyCode::ShiftTab,
        K::Backspace => KeyCode::Backspace,
        K::Esc => KeyCode::Esc,
        K::Delete => KeyCode::Delete,
        K::Insert => KeyCode::Insert,
        K::PageUp if ctrl => KeyCode::CtrlPgUp,
        K::PageUp => KeyCode::PgUp,
        K::PageDown if ctrl => KeyCode::CtrlPgDown,
        K::PageDown => KeyCode::PgDown,
        K::Up => arrow(
            ctrl,
            shift,
            [
                KeyCode::Up,
                KeyCode::ShiftUp,
                KeyCode::CtrlUp,
                KeyCode::CtrlShiftUp,
            ],
        ),
        K::Down => arrow(
            ctrl,
            shift,
            [
                KeyCode::Down,
                KeyCode::ShiftDown,
                KeyCode::CtrlDown,
                KeyCode::CtrlShiftDown,
            ],
        ),
        K::Left => arrow(
            ctrl,
            shift,
            [
                KeyCode::Left,
                KeyCode::ShiftLeft,
                KeyCode::CtrlLeft,
                KeyCode::CtrlShiftLeft,
            ],
        ),
        K::Right => arrow(
            ctrl,
            shift,
            [
                KeyCode::Right,
                KeyCode::ShiftRight,
                KeyCode::CtrlRight,
                KeyCode::CtrlShiftRight,
            ],
        ),
        K::Home => arrow(
            ctrl,
            shift,
            [
                KeyCode::Home,
                KeyCode::ShiftHome,
                KeyCode::CtrlHome,
                KeyCode::CtrlShiftHome,
            ],
        ),
        K::End => arrow(
            ctrl,
            shift,
            [
                KeyCode::End,
                KeyCode::ShiftEnd,
                KeyCode::CtrlEnd,
                KeyCode::CtrlShiftEnd,
            ],
        ),
        K::F(n) if (1..=20).contains(&n) => KeyCode::F(n),
        _ => return Some(Event::Unknown),
    };
    let mut mapped = Key::new(code);
    mapped.alt = alt;
    Some(Event::Key(mapped))
}

#[cfg(windows)]
fn arrow(ctrl: bool, shift: bool, codes: [KeyCode; 4]) -> KeyCode {
    codes[usize::from(ctrl) * 2 + usize::from(shift)]
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(bytes: &[u8]) -> Vec<String> {
        parse(bytes)
            .into_iter()
            .map(|event| match event {
                Event::Key(key) => key.name(),
                other => format!("{other:?}"),
            })
            .collect()
    }

    #[test]
    fn control_and_named_keys_match_tea_strings() {
        assert_eq!(names(b"\x03"), ["ctrl+c"]);
        assert_eq!(names(b"\r"), ["enter"]);
        assert_eq!(names(b"\t"), ["tab"]);
        assert_eq!(names(b"\x7f"), ["backspace"]);
        assert_eq!(names(b"\x08"), ["ctrl+h"]);
        assert_eq!(names(b"\x0a"), ["ctrl+j"]);
        assert_eq!(names(b"\x00"), ["ctrl+@"]);
        assert_eq!(names(b"\x1b\x00"), ["alt+ctrl+@"]);
        assert_eq!(
            names(b"\x1c\x1d\x1e\x1f"),
            ["ctrl+\\", "ctrl+]", "ctrl+^", "ctrl+_"]
        );
        assert_eq!(names(b"\x1b[Z"), ["shift+tab"]);
        assert_eq!(names(b"\x1b[A\x1b[B"), ["up", "down"]);
        assert_eq!(names(b"\x1bOA"), ["up"]);
        assert_eq!(names(b"\x1b[1;2A"), ["shift+up"]);
        assert_eq!(names(b"\x1b[5~\x1b[6~"), ["pgup", "pgdown"]);
        assert_eq!(names(b"\x1b[H\x1b[F"), ["home", "end"]);
        assert_eq!(names(b"\x1b[3~"), ["delete"]);
        assert_eq!(names(b"\x1bOP\x1b[24~"), ["f1", "f12"]);
    }

    #[test]
    fn escape_prefix_is_alt_within_one_read() {
        assert_eq!(names(b"\x1b"), ["esc"]);
        assert_eq!(names(b"\x1b\x1b"), ["alt+esc"]);
        assert_eq!(names(b"\x1b\r"), ["alt+enter"]);
        assert_eq!(names(b"\x1ba"), ["alt+a"]);
        assert_eq!(names(b"\x1bab"), ["alt+a", "b"]);
        assert_eq!(names(b"\x1b "), ["alt+ "]);
        assert_eq!(names(b"\x1b\x1b[A"), ["alt+up"]);
        assert_eq!(names(b"\x1b[1;3C"), ["alt+right"]);
    }

    #[test]
    fn runes_in_one_read_form_one_key() {
        assert_eq!(names(b"abc"), ["abc"]);
        assert_eq!(names(b"a b"), ["a", " ", "b"]);
        assert_eq!(names("é日本".as_bytes()), ["é日本"]);
        let events = parse(b" ");
        assert_eq!(events, [Event::Key(Key::new(KeyCode::Space))]);
        let events = parse(b"jk");
        let Event::Key(key) = &events[0] else {
            panic!()
        };
        assert_eq!(
            (key.code, key.runes.as_slice()),
            (KeyCode::Runes, &['j', 'k'][..])
        );
        assert!(Key::runes("k").is(&["up", "k"]));
    }

    #[test]
    fn bracketed_paste_is_one_bracketed_key_and_waits_for_end() {
        assert_eq!(names(b"\x1b[200~hi there\x1b[201~x"), ["[hi there]", "x"]);
        let mut parser = Parser::new();
        assert!(parser.feed(b"\x1b[200~par", false).is_empty());
        let events = parser.feed(b"tial\x1b[201~", false);
        let Event::Key(key) = &events[0] else {
            panic!()
        };
        assert!(key.paste);
        assert_eq!(key.runes.iter().collect::<String>(), "partial");
        // Pasted control characters stay literal runes; invalid bytes drop.
        assert_eq!(names(b"\x1b[200~a\r\xffb\x1b[201~"), ["[a\rb]"]);
    }

    #[test]
    fn full_reads_defer_trailing_runes_to_the_next_read() {
        let mut parser = Parser::new();
        assert!(parser.feed(&[b'x'; READ_BUFFER], true).is_empty());
        let events = parser.feed(b"y", false);
        let Event::Key(key) = &events[0] else {
            panic!()
        };
        assert_eq!(key.runes.len(), READ_BUFFER + 1);
        assert_eq!(key.runes.last(), Some(&'y'));
        // Go only defers when the rune run reaches the end of the buffer: a
        // UTF-8 sequence split across reads still decodes as invalid bytes.
        let mut parser = Parser::new();
        let mut first = vec![b'x'; READ_BUFFER - 1];
        first.push(0xc3);
        let events = parser.feed(&first, true);
        assert_eq!(events.len(), 2);
        assert_eq!(events[1], Event::Unknown);
        assert_eq!(parser.feed(&[0xa9], false), [Event::Unknown]);
    }

    #[test]
    fn unknown_input_is_reported_without_desynchronizing() {
        assert_eq!(names(b"\x1b[99X a"), ["Unknown", " ", "a"]);
        assert_eq!(names(b"\xffa"), ["Unknown", "a"]);
        assert_eq!(names(b"\x1b[I"), ["Unknown"]);
        assert_eq!(names(b"\x1b[M !!q"), ["Unknown", "q"]);
        assert_eq!(names(b"\x1b[<0;10;5Mq"), ["Unknown", "q"]);
        // ESC followed by an invalid byte: the ESC is an unknown byte.
        assert_eq!(names(b"\x1b\xff"), ["Unknown", "Unknown"]);
    }

    #[test]
    fn decode_rune_matches_go() {
        assert_eq!(decode_rune(b"a"), (Some('a'), 1));
        assert_eq!(decode_rune("€".as_bytes()), (Some('€'), 3));
        assert_eq!(decode_rune(b"\xe2\x82"), (None, 1));
        assert_eq!(decode_rune("\u{fffd}".as_bytes()), (None, 3));
        assert_eq!(decode_rune(b"\xc0\x80"), (None, 1));
    }
}
