//! Owned single-line and multi-line editors with the observable behavior of
//! bubbles v0.20.0 `textinput` and `textarea` (plus pb's `textAreaKeyMap`).
//!
//! Both editors are plain reducers: `update(&Key)` mutates owned buffers and
//! cursor state, `view(&styles)` renders with the shared lipgloss-compatible
//! style module. Cursor blinking is a deadline the caller's event loop polls
//! (`blink_deadline`/`tick`) instead of a Bubble Tea command chain; callers
//! that route Go's blink messages themselves (login) use the tagged timer
//! API (`pending_blink`/`take_pending_blink`/`deliver_blink`).
//! Divergences from Go (all unreachable crashes/hangs or the system clipboard)
//! are listed in `docs/phase7-editor.md`.

use std::cell::Cell;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use super::input::Key;
use super::style::{self, Color, Role, Style};

/// bubbles cursor `defaultBlinkSpeed`.
pub(crate) const BLINK_SPEED: Duration = Duration::from_millis(530);

/// textinput `EchoMode` together with its `EchoCharacter`. bubbles'
/// `EchoNone` is not used anywhere in pb, so it has no variant.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum EchoMode {
    Normal,
    /// Each display cell of the value is shown as this character (Go `'•'` in pb).
    Password(char),
}

/// Key bindings of a `TextInput` (bubbles `textinput.KeyMap`, `key.WithKeys` lists).
#[derive(Debug)]
pub(crate) struct InputKeyMap {
    pub(crate) character_forward: &'static [&'static str],
    pub(crate) character_backward: &'static [&'static str],
    pub(crate) word_forward: &'static [&'static str],
    pub(crate) word_backward: &'static [&'static str],
    pub(crate) delete_word_backward: &'static [&'static str],
    pub(crate) delete_word_forward: &'static [&'static str],
    pub(crate) delete_after_cursor: &'static [&'static str],
    pub(crate) delete_before_cursor: &'static [&'static str],
    pub(crate) delete_character_backward: &'static [&'static str],
    pub(crate) delete_character_forward: &'static [&'static str],
    pub(crate) line_start: &'static [&'static str],
    pub(crate) line_end: &'static [&'static str],
    pub(crate) paste: &'static [&'static str],
    pub(crate) next_suggestion: &'static [&'static str],
    pub(crate) prev_suggestion: &'static [&'static str],
}

/// `textinput.DefaultKeyMap`.
pub(crate) const INPUT_KEYS: InputKeyMap = InputKeyMap {
    character_forward: &["right", "ctrl+f"],
    character_backward: &["left", "ctrl+b"],
    word_forward: &["alt+right", "ctrl+right", "alt+f"],
    word_backward: &["alt+left", "ctrl+left", "alt+b"],
    delete_word_backward: &["alt+backspace", "ctrl+w"],
    delete_word_forward: &["alt+delete", "alt+d"],
    delete_after_cursor: &["ctrl+k"],
    delete_before_cursor: &["ctrl+u"],
    delete_character_backward: &["backspace", "ctrl+h"],
    delete_character_forward: &["delete", "ctrl+d"],
    line_start: &["home", "ctrl+a"],
    line_end: &["end", "ctrl+e"],
    paste: &["ctrl+v"],
    next_suggestion: &["down", "ctrl+n"],
    prev_suggestion: &["up", "ctrl+p"],
};

/// Key bindings of a `TextArea` (bubbles `textarea.KeyMap`).
#[derive(Debug)]
pub(crate) struct AreaKeyMap {
    pub(crate) character_backward: &'static [&'static str],
    pub(crate) character_forward: &'static [&'static str],
    pub(crate) delete_after_cursor: &'static [&'static str],
    pub(crate) delete_before_cursor: &'static [&'static str],
    pub(crate) delete_character_backward: &'static [&'static str],
    pub(crate) delete_character_forward: &'static [&'static str],
    pub(crate) delete_word_backward: &'static [&'static str],
    pub(crate) delete_word_forward: &'static [&'static str],
    pub(crate) insert_newline: &'static [&'static str],
    pub(crate) line_end: &'static [&'static str],
    pub(crate) line_next: &'static [&'static str],
    pub(crate) line_previous: &'static [&'static str],
    pub(crate) line_start: &'static [&'static str],
    pub(crate) paste: &'static [&'static str],
    pub(crate) word_backward: &'static [&'static str],
    pub(crate) word_forward: &'static [&'static str],
    pub(crate) input_begin: &'static [&'static str],
    pub(crate) input_end: &'static [&'static str],
    pub(crate) uppercase_word_forward: &'static [&'static str],
    pub(crate) lowercase_word_forward: &'static [&'static str],
    pub(crate) capitalize_word_forward: &'static [&'static str],
    pub(crate) transpose_character_backward: &'static [&'static str],
}

/// `textarea.DefaultKeyMap`.
pub(crate) const AREA_KEYS: AreaKeyMap = AreaKeyMap {
    character_forward: &["right", "ctrl+f"],
    character_backward: &["left", "ctrl+b"],
    word_forward: &["alt+right", "alt+f"],
    word_backward: &["alt+left", "alt+b"],
    line_next: &["down", "ctrl+n"],
    line_previous: &["up", "ctrl+p"],
    delete_word_backward: &["alt+backspace", "ctrl+w"],
    delete_word_forward: &["alt+delete", "alt+d"],
    delete_after_cursor: &["ctrl+k"],
    delete_before_cursor: &["ctrl+u"],
    insert_newline: &["enter", "ctrl+m"],
    delete_character_backward: &["backspace", "ctrl+h"],
    delete_character_forward: &["delete", "ctrl+d"],
    line_start: &["home", "ctrl+a"],
    line_end: &["end", "ctrl+e"],
    paste: &["ctrl+v"],
    input_begin: &["alt+<", "ctrl+home"],
    input_end: &["alt+>", "ctrl+end"],
    capitalize_word_forward: &["alt+c"],
    lowercase_word_forward: &["alt+l"],
    uppercase_word_forward: &["alt+u"],
    transpose_character_backward: &["ctrl+t"],
};

/// pb `pkg/model/textareakeymap.go` `textAreaKeyMap` (SQL/PromQL editors).
/// `InputBegin` is commented out in Go, so it is an empty binding here.
pub(crate) const PB_AREA_KEYS: AreaKeyMap = AreaKeyMap {
    character_forward: &["right", "ctrl+f"],
    character_backward: &["left", "ctrl+b"],
    word_forward: &["ctrl+right", "alt+f"],
    word_backward: &["ctrl+left", "alt+b"],
    line_next: &["down", "ctrl+n"],
    line_previous: &["up", "ctrl+p"],
    delete_word_backward: &["ctrl+backspace", "ctrl+w"],
    delete_word_forward: &["ctrl+delete", "alt+d"],
    delete_after_cursor: &["ctrl+k"],
    delete_before_cursor: &["ctrl+u"],
    insert_newline: &["enter", "ctrl+m"],
    delete_character_backward: &["backspace", "ctrl+h"],
    delete_character_forward: &["delete", "ctrl+d"],
    line_start: &["home", "ctrl+a"],
    line_end: &["end", "ctrl+e"],
    paste: &["ctrl+v"],
    input_begin: &[],
    input_end: &["ctrl+end"],
    capitalize_word_forward: &["alt+c"],
    lowercase_word_forward: &["alt+l"],
    uppercase_word_forward: &["alt+u"],
    transpose_character_backward: &["ctrl+t"],
};

/// Styles of a `TextInput` (bubbles `PromptStyle`, `TextStyle`,
/// `PlaceholderStyle`, `Cursor.Style`, `Cursor.TextStyle`).
#[derive(Clone)]
pub(crate) struct InputStyles {
    pub(crate) prompt: Style,
    pub(crate) text: Style,
    pub(crate) placeholder: Style,
    /// Visible cursor block; rendered with `reverse(true)` added, like bubbles.
    pub(crate) cursor: Style,
    /// Character under the cursor during the hidden blink phase / when blurred.
    pub(crate) cursor_text: Style,
}

impl Default for InputStyles {
    /// `textinput.New()` defaults: placeholder foreground ANSI 240, others unset.
    fn default() -> Self {
        Self {
            prompt: Style::new(),
            text: Style::new(),
            placeholder: Style::new().fg(Color::ansi256(240)),
            cursor: Style::new(),
            cursor_text: Style::new(),
        }
    }
}

/// One focus state of bubbles `textarea.Style`.
#[derive(Clone, Default)]
pub(crate) struct AreaStateStyles {
    pub(crate) base: Style,
    pub(crate) cursor_line: Style,
    pub(crate) cursor_line_number: Style,
    pub(crate) end_of_buffer: Style,
    pub(crate) line_number: Style,
    pub(crate) placeholder: Style,
    pub(crate) prompt: Style,
    pub(crate) text: Style,
}

impl AreaStateStyles {
    fn cursor_line(&self) -> Style {
        self.cursor_line.inherit(&self.base).inline(true)
    }
    fn cursor_line_number(&self) -> Style {
        self.cursor_line_number
            .inherit(&self.cursor_line)
            .inherit(&self.base)
            .inline(true)
    }
    fn end_of_buffer(&self) -> Style {
        self.end_of_buffer.inherit(&self.base).inline(true)
    }
    fn line_number(&self) -> Style {
        self.line_number.inherit(&self.base).inline(true)
    }
    fn placeholder(&self) -> Style {
        self.placeholder.inherit(&self.base).inline(true)
    }
    fn prompt(&self) -> Style {
        self.prompt.inherit(&self.base).inline(true)
    }
    fn text(&self) -> Style {
        self.text.inherit(&self.base).inline(true)
    }

    /// `textarea.DefaultStyles()` blurred half.
    fn bubbles_blurred() -> Self {
        Self {
            base: Style::new(),
            cursor_line: Style::new().fg(Color::adaptive("245", "7")),
            cursor_line_number: Style::new().fg(Color::adaptive("249", "7")),
            end_of_buffer: Style::new().fg(Color::adaptive("254", "0")),
            line_number: Style::new().fg(Color::adaptive("249", "7")),
            placeholder: Style::new().fg(Color::ansi256(240)),
            prompt: Style::new().fg(Color::ansi256(7)),
            text: Style::new().fg(Color::adaptive("245", "7")),
        }
    }
}

/// Focused/blurred styles plus the cursor block style of a `TextArea`.
#[derive(Clone)]
pub(crate) struct AreaStyles {
    pub(crate) focused: AreaStateStyles,
    pub(crate) blurred: AreaStateStyles,
    /// `Cursor.Style`; rendered with `reverse(true)` added. (`Cursor.TextStyle`
    /// is overwritten by the textarea view in Go, so it has no field here.)
    pub(crate) cursor: Style,
}

impl Default for AreaStyles {
    /// `textarea.DefaultStyles()` and an unset cursor style.
    fn default() -> Self {
        Self {
            focused: AreaStateStyles {
                base: Style::new(),
                cursor_line: Style::new().bg(Color::adaptive("255", "0")),
                cursor_line_number: Style::new().fg(Color::adaptive("240", "")),
                end_of_buffer: Style::new().fg(Color::adaptive("254", "0")),
                line_number: Style::new().fg(Color::adaptive("249", "7")),
                placeholder: Style::new().fg(Color::ansi256(240)),
                prompt: Style::new().fg(Color::ansi256(7)),
                text: Style::new(),
            },
            blurred: AreaStateStyles::bubbles_blurred(),
            cursor: Style::new(),
        }
    }
}

impl AreaStyles {
    /// pb `applyEditorStyles` (pkg/model/query.go): `ui.Active` palette hexes,
    /// shared by the SQL and PromQL editors. Callers also set the prompt to
    /// `"  "` (see `TextArea::set_prompt`). Go's `PaddingRight(1)` on the line
    /// number styles is omitted: inline rendering ignores padding and
    /// `Inherit` never copies it, so it has no visible effect.
    pub(crate) fn pb_editor() -> Self {
        let p = style::active();
        let c = |role: Role| Color::hex(p.hex(role));
        Self {
            focused: AreaStateStyles {
                base: Style::new().fg(c(Role::Mute)),
                text: Style::new().fg(c(Role::Mute)),
                line_number: Style::new().fg(c(Role::Faint)),
                cursor_line: Style::new(),
                cursor_line_number: Style::new().fg(c(Role::Accent)).bold(true),
                placeholder: Style::new().fg(c(Role::Ghost)).italic(true),
                prompt: Style::new().fg(c(Role::Accent)),
                end_of_buffer: Style::new(),
            },
            blurred: AreaStateStyles {
                base: Style::new().fg(c(Role::Mute)),
                text: Style::new().fg(c(Role::Mute)),
                line_number: Style::new().fg(c(Role::Ghost)),
                cursor_line: Style::new(),
                cursor_line_number: Style::new().fg(c(Role::Ghost)),
                placeholder: Style::new().fg(c(Role::Ghost)).italic(true),
                prompt: Style::new().fg(c(Role::Faint)),
                end_of_buffer: Style::new(),
            },
            cursor: Style::new().bg(c(Role::Cursor)),
        }
    }
}

/// A point where bubbles v0.20.0 panics or never returns. The editors stop at
/// that point and report it instead of crashing or spinning; the session
/// runner reproduces the Go program-level outcome (see docs/phase7-editor.md
/// "Go faults"). No editor method should be called after a fault except
/// `take_fault`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum EditorFault {
    /// Go `runtime error: index out of range [index] with length len`.
    IndexOutOfRange { index: usize, len: usize },
    /// Go `runtime error: slice bounds out of range [:high] with length len`.
    SliceOutOfRange { high: usize, len: usize },
    /// Go spins forever inside `Update` (textarea `wordLeft` at the start of
    /// a buffer whose first cell is blank); the program stops reading input.
    Hang,
}

impl std::fmt::Display for EditorFault {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::IndexOutOfRange { index, len } => {
                write!(
                    f,
                    "runtime error: index out of range [{index}] with length {len}"
                )
            }
            Self::SliceOutOfRange { high, len } => {
                write!(
                    f,
                    "runtime error: slice bounds out of range [:{high}] with length {len}"
                )
            }
            Self::Hang => f.write_str("update never returns"),
        }
    }
}

/// One outstanding bubbles `BlinkCmd`: when its `BlinkMsg` is due and the
/// `blinkTag` it carries. bubbles v0.20.0 never assigns cursor ids (all are
/// 0), so the tag alone decides whether a receiving cursor accepts it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct BlinkTimer {
    pub(crate) due: Instant,
    pub(crate) tag: u64,
}

/// bubbles `cursor.Model` in `CursorBlink` mode. `hidden` is Go's `Blink`
/// field: true shows the plain character, false shows the reversed block.
#[derive(Clone, Debug)]
struct BlinkCursor {
    focus: bool,
    hidden: bool,
    /// Go `blinkTag`: bumped by every `BlinkCmd`, including commands the
    /// caller discards.
    tag: u64,
    /// The cursor's only live timer. Each `BlinkCmd` cancels the previous
    /// one (Go's shared `blinkCtx`), so a new timer replaces it; `Blur` does
    /// not cancel it. When set, its tag is the current `tag`.
    timer: Option<BlinkTimer>,
}

impl BlinkCursor {
    fn new() -> Self {
        Self {
            focus: false,
            hidden: true,
            tag: 0,
            timer: None,
        }
    }
    /// `BlinkCmd`: cancel the live timer, bump the tag, arm a fresh one.
    fn arm(&mut self, now: Instant) {
        self.tag = self.tag.wrapping_add(1);
        self.timer = Some(BlinkTimer {
            due: now + BLINK_SPEED,
            tag: self.tag,
        });
    }
    /// `Focus()` plus running the returned `BlinkCmd`.
    fn focus(&mut self, now: Instant) {
        self.focus = true;
        self.hidden = false;
        self.arm(now);
    }
    /// `Blur()`. The live timer stays outstanding, as in Go; polling callers
    /// never observe it (`blink_deadline` and `tick` require focus, and
    /// `focus` replaces it).
    fn blur(&mut self) {
        self.focus = false;
        self.hidden = true;
    }
    /// Cursor moved: `Blink = false` and a fresh `BlinkCmd`.
    fn moved(&mut self, now: Instant) {
        self.hidden = false;
        self.arm(now);
    }
    /// `initialBlinkMsg` (textinput.Blink / textarea.Blink).
    fn start(&mut self, now: Instant) {
        if self.focus {
            self.arm(now);
        }
    }
    /// A `BlinkMsg` carrying `tag` reaches this cursor, whichever cursor's
    /// timer sent it: accepted only while focused and with the current tag;
    /// then the cursor toggles and arms its next timer.
    fn deliver(&mut self, tag: u64, now: Instant) -> bool {
        if !self.focus || tag != self.tag {
            return false;
        }
        self.hidden = !self.hidden;
        self.arm(now);
        true
    }
    /// The cursor's own timer, once due.
    fn tick(&mut self, now: Instant) -> bool {
        match self.timer {
            Some(timer) if now >= timer.due => self.deliver(timer.tag, now),
            _ => false,
        }
    }
    fn view(&self, ch: &str, cursor: &Style, text: &Style) -> String {
        if self.hidden {
            text.inline(true).render(ch)
        } else {
            cursor.inline(true).reverse(true).render(ch)
        }
    }
}

/// go `unicode.IsSpace`: identical to the Unicode White_Space property.
fn is_space(c: char) -> bool {
    c.is_whitespace()
}

/// bubbles' `uniseg.StringWidth(string(runes))` (grapheme widths). Its
/// per-rune `rw.RuneWidth` calls use `style::bubbles_rune_width` instead.
fn text_width(chars: &[char]) -> usize {
    style::width(&chars.iter().collect::<String>())
}

/// bubbles `runeutil` sanitizer: drops U+FFFD and control characters,
/// replaces each CR/LF and tab with the given strings.
fn sanitize(runes: &[char], newline: &str, tab: &str) -> Vec<char> {
    let mut out = Vec::with_capacity(runes.len());
    for &r in runes {
        match r {
            '\u{FFFD}' => {}
            '\r' | '\n' => out.extend(newline.chars()),
            '\t' => out.extend(tab.chars()),
            c if c.is_control() => {}
            c => out.push(c),
        }
    }
    out
}

/// bubbles v0.20.0 `textinput.Model` (suggestions disabled, as everywhere in pb).
#[derive(Clone, Debug)]
pub(crate) struct TextInput {
    prompt: String,
    placeholder: String,
    echo: EchoMode,
    char_limit: usize,
    width: usize,
    keys: &'static InputKeyMap,
    value: Vec<char>,
    focus: bool,
    pos: usize,
    offset: usize,
    offset_right: usize,
    cursor: BlinkCursor,
    /// Ctrl+V seen: Go returned its clipboard `Paste` command.
    paste_requested: bool,
    /// Set by `update` or `view` where Go panics.
    fault: Cell<Option<EditorFault>>,
}

impl Default for TextInput {
    fn default() -> Self {
        Self::new()
    }
}

impl TextInput {
    /// `textinput.New()`: prompt `"> "`, no limit, no width, blurred.
    pub(crate) fn new() -> Self {
        Self {
            prompt: "> ".to_string(),
            placeholder: String::new(),
            echo: EchoMode::Normal,
            char_limit: 0,
            width: 0,
            keys: &INPUT_KEYS,
            value: Vec::new(),
            focus: false,
            pos: 0,
            offset: 0,
            offset_right: 0,
            cursor: BlinkCursor::new(),
            paste_requested: false,
            fault: Cell::new(None),
        }
    }

    /// True once per Ctrl+V: the runner should start `clipboard::read()` and
    /// deliver a successful result with `insert_paste` (failures are ignored,
    /// as Go only stores them in the never-displayed `Err`).
    pub(crate) fn take_paste_request(&mut self) -> bool {
        std::mem::take(&mut self.paste_requested)
    }
    /// The Go panic reached by the last `update`/`view`, if any.
    pub(crate) fn take_fault(&self) -> Option<EditorFault> {
        self.fault.take()
    }

    pub(crate) fn set_placeholder(&mut self, s: &str) {
        self.placeholder = s.to_string();
    }
    pub(crate) fn set_prompt(&mut self, s: &str) {
        self.prompt = s.to_string();
    }
    /// `CharLimit` field: 0 means unlimited; existing text is not truncated.
    pub(crate) fn set_char_limit(&mut self, n: usize) {
        self.char_limit = n;
    }
    /// `Width` field: 0 disables horizontal scrolling. Takes effect at the
    /// next edit/cursor move, as in Go.
    pub(crate) fn set_width(&mut self, n: usize) {
        self.width = n;
    }
    pub(crate) fn set_echo(&mut self, mode: EchoMode) {
        self.echo = mode;
    }

    /// `Focus()` with its blink command honored (the cursor shows and blinks).
    pub(crate) fn focus(&mut self) {
        self.focus = true;
        self.cursor.focus(Instant::now());
    }
    pub(crate) fn blur(&mut self) {
        self.focus = false;
        self.cursor.blur();
    }
    pub(crate) fn focused(&self) -> bool {
        self.focus
    }

    pub(crate) fn value(&self) -> String {
        self.value.iter().collect()
    }
    #[cfg(test)]
    fn position(&self) -> usize {
        self.pos
    }

    /// `SetValue`: tabs/newlines become spaces, controls are dropped, the char
    /// limit truncates; the cursor only moves when it was at 0 on an empty
    /// value or is past the new end.
    pub(crate) fn set_value(&mut self, s: &str) {
        let runes = sanitize(&s.chars().collect::<Vec<_>>(), " ", " ");
        self.set_value_internal(runes);
    }

    fn set_value_internal(&mut self, mut runes: Vec<char>) {
        let empty = self.value.is_empty();
        if self.char_limit > 0 && runes.len() > self.char_limit {
            runes.truncate(self.char_limit);
        }
        self.value = runes;
        if (self.pos == 0 && empty) || self.pos > self.value.len() {
            self.set_cursor(self.value.len());
        }
        self.handle_overflow();
    }

    pub(crate) fn set_cursor(&mut self, pos: usize) {
        self.pos = pos.min(self.value.len());
        self.handle_overflow();
    }
    pub(crate) fn cursor_start(&mut self) {
        self.set_cursor(0);
    }
    pub(crate) fn cursor_end(&mut self) {
        self.set_cursor(self.value.len());
    }
    /// `Reset()`: empty value, cursor at 0.
    pub(crate) fn reset(&mut self) {
        self.value.clear();
        self.set_cursor(0);
    }

    /// Next instant at which `tick` toggles the cursor, if blinking.
    pub(crate) fn blink_deadline(&self) -> Option<Instant> {
        if self.focus {
            self.cursor.timer.map(|timer| timer.due)
        } else {
            None
        }
    }
    /// Deliver a due blink; returns whether the view changed.
    pub(crate) fn tick(&mut self, now: Instant) -> bool {
        self.cursor.tick(now)
    }
    /// The outstanding blink timer, focused or not (Go `Blur` cancels
    /// nothing). For callers that route blink messages themselves.
    pub(crate) fn pending_blink(&self) -> Option<BlinkTimer> {
        self.cursor.timer
    }
    /// The outstanding timer fired: its message is in flight, so nothing
    /// stays scheduled for this cursor.
    pub(crate) fn take_pending_blink(&mut self) -> Option<BlinkTimer> {
        self.cursor.timer.take()
    }
    /// Go `BlinkMsg` with `tag` routed to this input by its parent model:
    /// ignored while blurred or unless `tag` is this cursor's current tag;
    /// otherwise the cursor toggles and arms its next timer (replacing its
    /// own). Returns whether the view changed.
    pub(crate) fn deliver_blink(&mut self, tag: u64, now: Instant) -> bool {
        self.focus && self.cursor.deliver(tag, now)
    }
    /// `textinput.Blink` (initial blink message) returned by a parent model.
    pub(crate) fn start_blink(&mut self) {
        self.start_blink_at(Instant::now());
    }
    /// `start_blink` delivered at `now`.
    pub(crate) fn start_blink_at(&mut self, now: Instant) {
        self.cursor.start(now);
    }
    /// For callers whose Go model discarded the `Focus()` command: the cursor
    /// stays visible until the next movement restarts blinking. The tag that
    /// discarded command bumped stays bumped.
    pub(crate) fn disarm_blink(&mut self) {
        self.cursor.timer = None;
    }
    #[cfg(test)]
    pub(crate) fn cursor_visible(&self) -> bool {
        !self.cursor.hidden
    }

    /// Go `pasteMsg` (clipboard text delivered to the model).
    pub(crate) fn insert_paste(&mut self, text: &str) {
        self.insert_paste_at(text, Instant::now());
    }
    /// `insert_paste` delivered at `now` (a cursor move re-arms the blink).
    pub(crate) fn insert_paste_at(&mut self, text: &str, now: Instant) {
        if !self.focus {
            return;
        }
        let old = self.pos;
        self.insert_runes(&text.chars().collect::<Vec<_>>());
        self.after_update(old, now);
    }

    fn insert_runes(&mut self, v: &[char]) {
        let mut paste = sanitize(v, " ", " ");
        if self.char_limit > 0 {
            let avail = self.char_limit.saturating_sub(self.value.len());
            if avail == 0 {
                return;
            }
            paste.truncate(avail);
        }
        let mut value: Vec<char> = self.value[..self.pos].to_vec();
        value.extend_from_slice(&paste);
        self.pos += paste.len();
        value.extend_from_slice(&self.value[self.pos - paste.len()..]);
        self.set_value_internal(value);
    }

    fn after_update(&mut self, old_pos: usize, now: Instant) {
        if old_pos != self.pos {
            self.cursor.moved(now);
        }
        self.handle_overflow();
    }

    /// bubbles `Update` for a key with the default keymap. Ignored while blurred.
    pub(crate) fn update(&mut self, key: &Key) {
        self.update_at(key, Instant::now());
    }
    /// `update` delivered at `now` (a cursor move re-arms the blink).
    pub(crate) fn update_at(&mut self, key: &Key, now: Instant) {
        if !self.focus {
            return;
        }
        let k = self.keys;
        let old = self.pos;
        if key.is(k.delete_word_backward) {
            self.delete_word_backward();
        } else if key.is(k.delete_character_backward) {
            if !self.value.is_empty() && self.pos > 0 {
                self.value.remove(self.pos - 1);
                self.set_cursor(self.pos - 1);
            }
        } else if key.is(k.word_backward) {
            self.word_backward();
        } else if key.is(k.character_backward) {
            if self.pos > 0 {
                self.set_cursor(self.pos - 1);
            }
        } else if key.is(k.word_forward) {
            self.word_forward();
        } else if key.is(k.character_forward) {
            if self.pos < self.value.len() {
                self.set_cursor(self.pos + 1);
            }
        } else if key.is(k.line_start) {
            self.cursor_start();
        } else if key.is(k.delete_character_forward) {
            if !self.value.is_empty() && self.pos < self.value.len() {
                self.value.remove(self.pos);
            }
        } else if key.is(k.line_end) {
            self.cursor_end();
        } else if key.is(k.delete_after_cursor) {
            self.delete_after_cursor();
        } else if key.is(k.delete_before_cursor) {
            self.delete_before_cursor();
        } else if key.is(k.paste) {
            // Go returns the clipboard `Paste` command (no overflow/blink work).
            self.paste_requested = true;
            return;
        } else if key.is(k.delete_word_forward) {
            self.delete_word_forward();
            if self.fault.get().is_some() {
                return;
            }
        } else if key.is(k.next_suggestion) || key.is(k.prev_suggestion) {
            // Suggestions are disabled: the keys are consumed without effect.
        } else {
            self.insert_runes(&key.runes);
        }
        self.after_update(old, now);
    }

    fn delete_before_cursor(&mut self) {
        self.value.drain(..self.pos);
        self.offset = 0;
        self.set_cursor(0);
    }

    fn delete_after_cursor(&mut self) {
        self.value.truncate(self.pos);
        self.set_cursor(self.value.len());
    }

    fn delete_word_backward(&mut self) {
        if self.pos == 0 || self.value.is_empty() {
            return;
        }
        if self.echo != EchoMode::Normal {
            self.delete_before_cursor();
            return;
        }
        let old = self.pos;
        self.set_cursor(self.pos - 1);
        while is_space(self.value[self.pos]) {
            if self.pos == 0 {
                break;
            }
            self.set_cursor(self.pos - 1);
        }
        while self.pos > 0 {
            if !is_space(self.value[self.pos]) {
                self.set_cursor(self.pos - 1);
            } else {
                self.set_cursor(self.pos + 1);
                break;
            }
        }
        let end = old.min(self.value.len());
        self.value.drain(self.pos..end);
    }

    fn delete_word_forward(&mut self) {
        if self.pos >= self.value.len() || self.value.is_empty() {
            return;
        }
        if self.echo != EchoMode::Normal {
            self.delete_after_cursor();
            return;
        }
        let old = self.pos;
        self.set_cursor(self.pos + 1);
        // Go indexes value[pos] before checking the end: with the cursor on
        // the last rune (reachable: alt+d / alt+delete in any normal-echo
        // field) bubbles panics. Report it (EditorFault) instead.
        if self.pos == self.value.len() {
            self.fault.set(Some(EditorFault::IndexOutOfRange {
                index: self.pos,
                len: self.value.len(),
            }));
            return;
        }
        while is_space(self.value[self.pos]) {
            self.set_cursor(self.pos + 1);
            if self.pos >= self.value.len() {
                break;
            }
        }
        while self.pos < self.value.len() {
            if !is_space(self.value[self.pos]) {
                self.set_cursor(self.pos + 1);
            } else {
                break;
            }
        }
        let end = self.pos.min(self.value.len());
        self.value.drain(old..end);
        self.set_cursor(old);
    }

    fn word_backward(&mut self) {
        if self.pos == 0 || self.value.is_empty() {
            return;
        }
        if self.echo != EchoMode::Normal {
            self.cursor_start();
            return;
        }
        let mut i = self.pos as isize - 1;
        while i >= 0 && is_space(self.value[i as usize]) {
            self.set_cursor(self.pos - 1);
            i -= 1;
        }
        while i >= 0 && !is_space(self.value[i as usize]) {
            self.set_cursor(self.pos - 1);
            i -= 1;
        }
    }

    fn word_forward(&mut self) {
        if self.pos >= self.value.len() || self.value.is_empty() {
            return;
        }
        if self.echo != EchoMode::Normal {
            self.cursor_end();
            return;
        }
        let mut i = self.pos;
        while i < self.value.len() && is_space(self.value[i]) {
            self.set_cursor(self.pos + 1);
            i += 1;
        }
        while i < self.value.len() && !is_space(self.value[i]) {
            self.set_cursor(self.pos + 1);
            i += 1;
        }
    }

    /// Horizontal scrolling window `[offset, offset_right)` when `width` is set.
    fn handle_overflow(&mut self) {
        let width = self.width;
        if width == 0 || text_width(&self.value) <= width {
            self.offset = 0;
            self.offset_right = self.value.len();
            return;
        }
        self.offset_right = self.offset_right.min(self.value.len());
        if self.pos < self.offset {
            self.offset = self.pos;
            let runes = &self.value[self.offset..];
            let (mut w, mut i) = (0usize, 0usize);
            while i < runes.len() && w <= width {
                w += style::bubbles_rune_width(runes[i]);
                if w <= width + 1 {
                    i += 1;
                }
            }
            self.offset_right = self.offset + i;
        } else if self.pos >= self.offset_right {
            self.offset_right = self.pos;
            let runes = &self.value[..self.offset_right];
            let mut w = 0usize;
            let mut i = runes.len() as isize - 1;
            while i > 0 && w < width {
                w += style::bubbles_rune_width(runes[i as usize]);
                if w <= width {
                    i -= 1;
                }
            }
            let kept = (runes.len() as isize - 1 - i).max(0) as usize;
            self.offset = self.offset_right - kept.min(self.offset_right);
        }
    }

    fn echo_transform(&self, chars: &[char]) -> String {
        match self.echo {
            EchoMode::Normal => chars.iter().collect(),
            EchoMode::Password(mask) => mask.to_string().repeat(text_width(chars)),
        }
    }

    /// bubbles `View`.
    pub(crate) fn view(&self, styles: &InputStyles) -> String {
        if self.value.is_empty() && !self.placeholder.is_empty() {
            return self.placeholder_view(styles);
        }
        let text = styles.text.inline(true);
        let end = self.offset_right.min(self.value.len());
        let start = self.offset.min(end);
        let value = &self.value[start..end];
        let pos = self.pos.saturating_sub(self.offset).min(value.len());
        let mut v = text.render(&self.echo_transform(&value[..pos]));
        if pos < value.len() {
            let ch = self.echo_transform(&value[pos..=pos]);
            v += &self.cursor.view(&ch, &styles.cursor, &styles.cursor_text);
            v += &text.render(&self.echo_transform(&value[pos + 1..]));
        } else {
            v += &self.cursor.view(" ", &styles.cursor, &styles.cursor_text);
        }
        let value_width = text_width(value);
        if self.width > 0 && value_width <= self.width {
            let mut padding = self.width - value_width;
            if value_width + padding <= self.width && pos < value.len() {
                padding += 1;
            }
            v += &text.render(&" ".repeat(padding));
        }
        styles.prompt.render(&self.prompt) + &v
    }

    fn placeholder_view(&self, styles: &InputStyles) -> String {
        let p: Vec<char> = self.placeholder.chars().collect();
        let style = styles.placeholder.inline(true);
        let mut v = self.cursor.view(
            &p[..1].iter().collect::<String>(),
            &styles.cursor,
            &styles.placeholder,
        );
        if self.width == 0 && p.len() <= 1 {
            return styles.prompt.render(&self.prompt) + &v;
        }
        if self.width > 0 {
            let mut min_width = style::width(&self.placeholder) as isize;
            let mut avail = self.width as isize - min_width + 1;
            if avail < 0 {
                min_width += avail;
                avail = 0;
            }
            // Go slices runes by display width (p[1:minWidth]); a placeholder
            // with wide runes panics there. pb's placeholders are ASCII, but
            // the fault is still reported rather than clamped silently.
            let high = min_width.max(1) as usize;
            if high > p.len() {
                self.fault
                    .set(Some(EditorFault::SliceOutOfRange { high, len: p.len() }));
            }
            let end = high.min(p.len());
            v += &style.render(&p[1..end].iter().collect::<String>());
            v += &style.render(&" ".repeat(avail as usize));
        } else {
            v += &style.render(&p[1..].iter().collect::<String>());
        }
        styles.prompt.render(&self.prompt) + &v
    }
}

/// Which `textarea.Style` the Go `style` pointer refers to: `New()` points it
/// at bubbles' default blurred styles until `Focus`/`Blur` is first called.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum AreaStyleState {
    Initial,
    Focused,
    Blurred,
}

/// bubbles `textarea.LineInfo`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct LineInfo {
    width: usize,
    char_width: usize,
    height: usize,
    start_column: usize,
    column_offset: usize,
    row_offset: usize,
    char_offset: usize,
}

const AREA_MIN_HEIGHT: usize = 1;
const AREA_DEFAULT_HEIGHT: usize = 6;
const AREA_DEFAULT_WIDTH: usize = 40;
const AREA_DEFAULT_CHAR_LIMIT: usize = 400;
const AREA_DEFAULT_MAX_HEIGHT: usize = 99;
const AREA_DEFAULT_MAX_WIDTH: usize = 500;

/// bubbles v0.20.0 `textarea.Model`.
#[derive(Clone, Debug)]
pub(crate) struct TextArea {
    prompt: String,
    placeholder: String,
    show_line_numbers: bool,
    end_of_buffer: char,
    keys: &'static AreaKeyMap,
    char_limit: usize,
    max_height: usize,
    max_width: usize,
    prompt_width: usize,
    width: usize,
    height: usize,
    value: Vec<Vec<char>>,
    focus: bool,
    style_state: AreaStyleState,
    col: usize,
    row: usize,
    last_char_offset: usize,
    /// Go keeps the viewport behind a pointer, so value copies of the model
    /// share it: `clone()` here shares it too. `View` (even on a copy) stores
    /// the rendered line count and may clamp the scroll offset, `SetWidth`/
    /// `SetHeight` on a copy resize it, and the next `Update` scrolls against
    /// that state. pb's SQL view renders a resized copy and relies on this.
    viewport: Arc<Mutex<Viewport>>,
    cursor: BlinkCursor,
    paste_requested: bool,
    fault: Cell<Option<EditorFault>>,
}

/// bubbles `viewport.Model` state used by the textarea.
#[derive(Debug, Default)]
struct Viewport {
    width: usize,
    height: usize,
    y_offset: usize,
    /// Line count of the last `SetContent`.
    lines: usize,
}

impl Default for TextArea {
    fn default() -> Self {
        Self::new()
    }
}

impl TextArea {
    /// `textarea.New()`: 400-cell limit, max 99 rows / 500 columns, `"┃ "`
    /// prompt, line numbers, height 6, width 40, blurred.
    pub(crate) fn new() -> Self {
        let mut area = Self {
            prompt: "\u{2503} ".to_string(),
            placeholder: String::new(),
            show_line_numbers: true,
            end_of_buffer: ' ',
            keys: &AREA_KEYS,
            char_limit: AREA_DEFAULT_CHAR_LIMIT,
            max_height: AREA_DEFAULT_MAX_HEIGHT,
            max_width: AREA_DEFAULT_MAX_WIDTH,
            prompt_width: 0,
            width: 0,
            height: 0,
            value: vec![Vec::new()],
            focus: false,
            style_state: AreaStyleState::Initial,
            col: 0,
            row: 0,
            last_char_offset: 0,
            viewport: Arc::new(Mutex::new(Viewport::default())),
            cursor: BlinkCursor::new(),
            paste_requested: false,
            fault: Cell::new(None),
        };
        area.set_height(AREA_DEFAULT_HEIGHT);
        area.set_width(AREA_DEFAULT_WIDTH);
        area
    }

    /// `Prompt` field. As in Go, call `set_width` afterwards to re-reserve its width.
    pub(crate) fn set_prompt(&mut self, s: &str) {
        self.prompt = s.to_string();
    }
    pub(crate) fn set_placeholder(&mut self, s: &str) {
        self.placeholder = s.to_string();
    }
    /// `ShowLineNumbers` field (re-reserved by the next `set_width`).
    pub(crate) fn set_show_line_numbers(&mut self, show: bool) {
        self.show_line_numbers = show;
    }
    pub(crate) fn set_end_of_buffer(&mut self, c: char) {
        self.end_of_buffer = c;
    }
    pub(crate) fn set_keymap(&mut self, keys: &'static AreaKeyMap) {
        self.keys = keys;
    }
    /// `CharLimit` (display cells incl. newlines); 0 = unlimited. pb keeps
    /// the default 400, so only tests change it.
    #[cfg(test)]
    fn set_char_limit(&mut self, n: usize) {
        self.char_limit = n;
    }
    /// `MaxHeight` rows; 0 = unlimited. Also sets the line-number digit count.
    pub(crate) fn set_max_height(&mut self, n: usize) {
        self.max_height = n;
    }
    /// `MaxWidth` columns; 0 = unlimited.
    pub(crate) fn set_max_width(&mut self, n: usize) {
        self.max_width = n;
    }

    /// `SetHeight` then `SetWidth`.
    pub(crate) fn set_size(&mut self, width: usize, height: usize) {
        self.set_height(height);
        self.set_width(width);
    }

    /// `SetWidth`: the total width including prompt and line-number gutter.
    pub(crate) fn set_width(&mut self, w: usize) {
        self.prompt_width = style::width(&self.prompt);
        // Base-style frames are never set by pb, so no outer reservation.
        let mut reserved_inner = self.prompt_width;
        if self.show_line_numbers {
            reserved_inner += 4;
        }
        let min_width = reserved_inner + 1;
        let mut input_width = w.max(min_width);
        if self.max_width > 0 {
            input_width = input_width.min(self.max_width);
        }
        self.vp().width = input_width;
        self.width = input_width.saturating_sub(reserved_inner);
    }

    pub(crate) fn set_height(&mut self, h: usize) {
        let h = if self.max_height > 0 {
            h.clamp(AREA_MIN_HEIGHT, self.max_height)
        } else {
            h.max(AREA_MIN_HEIGHT)
        };
        self.height = h;
        self.vp().height = h;
    }

    fn vp(&self) -> MutexGuard<'_, Viewport> {
        self.viewport.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Text width available to the buffer (Go `Width()`).
    #[cfg(test)]
    fn width(&self) -> usize {
        self.width
    }
    #[cfg(test)]
    fn height(&self) -> usize {
        self.height
    }

    pub(crate) fn value(&self) -> String {
        let mut out = String::new();
        for (i, line) in self.value.iter().enumerate() {
            if i > 0 {
                out.push('\n');
            }
            out.extend(line.iter());
        }
        out
    }

    /// `SetValue`: `Reset` then `InsertString`.
    pub(crate) fn set_value(&mut self, s: &str) {
        self.reset();
        self.insert_str(s);
    }

    /// `InsertString` at the cursor (sanitized, limited, may add rows).
    pub(crate) fn insert_str(&mut self, s: &str) {
        self.insert_runes(&s.chars().collect::<Vec<_>>());
    }

    /// `Reset`: one empty row, cursor and scroll at the top.
    pub(crate) fn reset(&mut self) {
        self.value = vec![Vec::new()];
        self.col = 0;
        self.row = 0;
        self.vp().y_offset = 0;
        self.set_cursor(0);
    }

    /// Display-cell length plus newlines (Go `Length`).
    pub(crate) fn length(&self) -> usize {
        self.value.iter().map(|row| text_width(row)).sum::<usize>() + self.value.len() - 1
    }
    /// `(row, col)` in runes.
    #[cfg(test)]
    fn cursor(&self) -> (usize, usize) {
        (self.row, self.col)
    }

    /// `Focus()`; the returned Go blink command is honored.
    pub(crate) fn focus(&mut self) {
        self.focus = true;
        self.style_state = AreaStyleState::Focused;
        self.cursor.focus(Instant::now());
    }
    pub(crate) fn blur(&mut self) {
        self.focus = false;
        self.style_state = AreaStyleState::Blurred;
        self.cursor.blur();
    }
    /// True once per Ctrl+V. pb's SQL/PromQL models forward only key
    /// messages to the textarea, so Go's `pasteMsg` (like its cursor
    /// `BlinkMsg`) never reaches it: the clipboard read runs and its result is
    /// dropped. Hence no textarea `insert_paste` or blink tick API.
    pub(crate) fn take_paste_request(&mut self) -> bool {
        std::mem::take(&mut self.paste_requested)
    }
    /// The Go panic/hang reached by the last `update`/`view`, if any.
    pub(crate) fn take_fault(&self) -> Option<EditorFault> {
        self.fault.take()
    }
    /// For callers whose Go model discarded the `Focus()` command (pb's SQL
    /// and PromQL constructors do).
    pub(crate) fn disarm_blink(&mut self) {
        self.cursor.timer = None;
    }

    pub(crate) fn set_cursor(&mut self, col: usize) {
        self.col = col.min(self.value[self.row].len());
        self.last_char_offset = 0;
    }
    pub(crate) fn cursor_start(&mut self) {
        self.set_cursor(0);
    }
    pub(crate) fn cursor_end(&mut self) {
        self.set_cursor(self.value[self.row].len());
    }

    fn insert_runes(&mut self, runes: &[char]) {
        let mut runes = sanitize(runes, "\n", "    ");
        if self.char_limit > 0 {
            let avail = self.char_limit.saturating_sub(self.length());
            if avail == 0 {
                return;
            }
            runes.truncate(avail);
        }
        let mut lines: Vec<Vec<char>> = runes.split(|&c| c == '\n').map(<[char]>::to_vec).collect();
        if self.max_height > 0 && self.value.len() + lines.len() - 1 > self.max_height {
            let allowed = (self.max_height + 1).saturating_sub(self.value.len());
            lines.truncate(allowed);
        }
        if lines.is_empty() {
            return;
        }
        let tail = self.value[self.row].split_off(self.col);
        let mut lines = lines.into_iter();
        let first = lines.next().unwrap_or_default();
        self.col += first.len();
        self.value[self.row].extend(first);
        for line in lines {
            self.row += 1;
            self.col = line.len();
            self.value.insert(self.row, line);
        }
        self.value[self.row].extend(tail);
        self.set_cursor(self.col);
    }

    fn finish_update(&mut self, before: (usize, usize)) {
        if (self.cursor_line_number(), self.col) != before {
            self.cursor.moved(Instant::now());
        }
        self.reposition_view();
    }

    /// bubbles `Update` for a key. Callers intercept their own keys first
    /// (SQL Ctrl+B/Ctrl+D, Tab, Ctrl+R ...). Ignored while blurred.
    pub(crate) fn update(&mut self, key: &Key) {
        if !self.focus {
            self.cursor.blur();
            return;
        }
        let before = (self.cursor_line_number(), self.col);
        let k = self.keys;
        if key.is(k.delete_after_cursor) {
            self.col = self.col.min(self.value[self.row].len());
            if self.col >= self.value[self.row].len() {
                self.merge_line_below(self.row);
            } else {
                self.value[self.row].truncate(self.col);
                self.cursor_end();
            }
        } else if key.is(k.delete_before_cursor) {
            self.col = self.col.min(self.value[self.row].len());
            if self.col == 0 {
                self.merge_line_above(self.row);
            } else {
                self.value[self.row].drain(..self.col);
                self.set_cursor(0);
            }
        } else if key.is(k.delete_character_backward) {
            self.col = self.col.min(self.value[self.row].len());
            if self.col == 0 {
                self.merge_line_above(self.row);
            } else if !self.value[self.row].is_empty() {
                self.value[self.row].remove(self.col - 1);
                self.set_cursor(self.col - 1);
            }
        } else if key.is(k.delete_character_forward) {
            let len = self.value[self.row].len();
            if len > 0 && self.col < len {
                self.value[self.row].remove(self.col);
            }
            if self.col >= self.value[self.row].len() {
                self.merge_line_below(self.row);
            }
        } else if key.is(k.delete_word_backward) {
            if self.col == 0 {
                self.merge_line_above(self.row);
            } else {
                self.delete_word_left();
            }
        } else if key.is(k.delete_word_forward) {
            self.col = self.col.min(self.value[self.row].len());
            if self.col >= self.value[self.row].len() {
                self.merge_line_below(self.row);
            } else {
                self.delete_word_right();
            }
        } else if key.is(k.insert_newline) {
            if self.max_height > 0 && self.value.len() >= self.max_height {
                return;
            }
            self.col = self.col.min(self.value[self.row].len());
            self.split_line(self.row, self.col);
        } else if key.is(k.line_end) {
            self.cursor_end();
        } else if key.is(k.line_start) {
            self.cursor_start();
        } else if key.is(k.character_forward) {
            self.character_right();
        } else if key.is(k.line_next) {
            self.cursor_down();
        } else if key.is(k.word_forward) {
            self.word_right();
        } else if key.is(k.paste) {
            // Go returns the clipboard `Paste` command before any viewport,
            // blink or reposition work.
            self.paste_requested = true;
            return;
        } else if key.is(k.character_backward) {
            self.character_left(false);
        } else if key.is(k.line_previous) {
            self.cursor_up();
        } else if key.is(k.word_backward) {
            self.word_left();
            if self.fault.get().is_some() {
                return;
            }
        } else if key.is(k.input_begin) {
            self.row = 0;
            self.set_cursor(0);
        } else if key.is(k.input_end) {
            self.row = self.value.len() - 1;
            self.cursor_end();
        } else if key.is(k.lowercase_word_forward) {
            self.do_word_right(|_, c| simple_lower(c));
        } else if key.is(k.uppercase_word_forward) {
            self.do_word_right(|_, c| simple_upper(c));
        } else if key.is(k.capitalize_word_forward) {
            self.do_word_right(|i, c| if i == 0 { simple_title(c) } else { c });
        } else if key.is(k.transpose_character_backward) {
            self.transpose_left();
        } else {
            self.insert_runes(&key.runes);
        }
        self.finish_update(before);
    }

    fn merge_line_below(&mut self, row: usize) {
        if row + 1 >= self.value.len() {
            return;
        }
        let below = self.value.remove(row + 1);
        self.value[row].extend(below);
    }

    /// Moves the cursor to the join point without resetting the vertical
    /// navigation offset, as Go does.
    fn merge_line_above(&mut self, row: usize) {
        if row == 0 {
            return;
        }
        self.col = self.value[row - 1].len();
        self.row -= 1;
        let line = self.value.remove(row);
        self.value[row - 1].extend(line);
    }

    fn split_line(&mut self, row: usize, col: usize) {
        let tail = self.value[row].split_off(col);
        self.value.insert(row + 1, tail);
        self.col = 0;
        self.row += 1;
    }

    fn transpose_left(&mut self) {
        let len = self.value[self.row].len();
        if self.col == 0 || len < 2 {
            return;
        }
        if self.col >= len {
            self.set_cursor(self.col - 1);
        }
        self.value[self.row].swap(self.col - 1, self.col);
        if self.col < len {
            self.set_cursor(self.col + 1);
        }
    }

    fn delete_word_left(&mut self) {
        if self.col == 0 || self.value[self.row].is_empty() {
            return;
        }
        let old = self.col;
        self.set_cursor(self.col - 1);
        while is_space(self.value[self.row][self.col]) {
            if self.col == 0 {
                break;
            }
            self.set_cursor(self.col - 1);
        }
        while self.col > 0 {
            if !is_space(self.value[self.row][self.col]) {
                self.set_cursor(self.col - 1);
            } else {
                self.set_cursor(self.col + 1);
                break;
            }
        }
        let end = old.min(self.value[self.row].len());
        let col = self.col;
        self.value[self.row].drain(col..end);
    }

    fn delete_word_right(&mut self) {
        let len = self.value[self.row].len();
        if self.col >= len || len == 0 {
            return;
        }
        let old = self.col;
        while self.col < len && is_space(self.value[self.row][self.col]) {
            self.set_cursor(self.col + 1);
        }
        while self.col < len && !is_space(self.value[self.row][self.col]) {
            self.set_cursor(self.col + 1);
        }
        let col = self.col;
        self.value[self.row].drain(old..col);
        self.set_cursor(old);
    }

    fn character_right(&mut self) {
        if self.col < self.value[self.row].len() {
            self.set_cursor(self.col + 1);
        } else if self.row + 1 < self.value.len() {
            self.row += 1;
            self.cursor_start();
        }
    }

    fn character_left(&mut self, inside_line: bool) {
        if self.col == 0 && self.row != 0 {
            self.row -= 1;
            self.cursor_end();
            if !inside_line {
                return;
            }
        }
        if self.col > 0 {
            self.set_cursor(self.col - 1);
        }
    }

    fn word_left(&mut self) {
        loop {
            let before = (self.row, self.col);
            self.character_left(true);
            let line = &self.value[self.row];
            if self.col < line.len() && !is_space(line[self.col]) {
                break;
            }
            // At (0,0) on an empty buffer or a blank first cell Go loops
            // forever (reachable: ctrl+left/alt+b in an empty SQL/PromQL
            // editor). Report it instead of spinning.
            if (self.row, self.col) == before {
                self.fault.set(Some(EditorFault::Hang));
                return;
            }
        }
        while self.col > 0 {
            if is_space(self.value[self.row][self.col - 1]) {
                break;
            }
            self.set_cursor(self.col - 1);
        }
    }

    fn word_right(&mut self) {
        self.do_word_right(|_, c| c);
    }

    fn do_word_right(&mut self, f: impl Fn(usize, char) -> char) {
        loop {
            let line = &self.value[self.row];
            if !(self.col >= line.len() || is_space(line[self.col])) {
                break;
            }
            if self.row == self.value.len() - 1 && self.col == line.len() {
                break;
            }
            self.character_right();
        }
        let mut idx = 0;
        while self.col < self.value[self.row].len() {
            let c = self.value[self.row][self.col];
            if is_space(c) {
                break;
            }
            self.value[self.row][self.col] = f(idx, c);
            self.set_cursor(self.col + 1);
            idx += 1;
        }
    }

    fn line_info(&self) -> LineInfo {
        line_info_at(&self.value[self.row], self.width, self.col as isize)
    }

    fn cursor_down(&mut self) {
        let li = self.line_info();
        let char_offset = self.last_char_offset.max(li.char_offset);
        self.last_char_offset = char_offset;
        let col: isize = if li.row_offset + 1 >= li.height && self.row + 1 < self.value.len() {
            self.row += 1;
            0
        } else {
            ((li.start_column + li.width + 2) as isize).min(self.value[self.row].len() as isize - 1)
        };
        let nli = line_info_at(&self.value[self.row], self.width, col);
        self.col = nli.start_column;
        if nli.width == 0 {
            return;
        }
        let mut offset = 0;
        while offset < char_offset {
            let line = &self.value[self.row];
            if self.col >= line.len() || offset + 1 >= nli.char_width {
                break;
            }
            offset += style::bubbles_rune_width(line[self.col]);
            self.col += 1;
        }
    }

    fn cursor_up(&mut self) {
        let li = self.line_info();
        let char_offset = self.last_char_offset.max(li.char_offset);
        self.last_char_offset = char_offset;
        let col: isize = if li.row_offset == 0 && self.row > 0 {
            self.row -= 1;
            self.value[self.row].len() as isize
        } else {
            li.start_column as isize - 2
        };
        let nli = line_info_at(&self.value[self.row], self.width, col);
        self.col = nli.start_column;
        if nli.width == 0 {
            return;
        }
        let mut offset = 0;
        while offset < char_offset {
            let line = &self.value[self.row];
            if self.col >= line.len() || offset + 1 >= nli.char_width {
                break;
            }
            offset += style::bubbles_rune_width(line[self.col]);
            self.col += 1;
        }
    }

    /// Display row of the cursor including soft wraps.
    fn cursor_line_number(&self) -> usize {
        let above: usize = self.value[..self.row]
            .iter()
            .map(|l| wrap(l, self.width).len())
            .sum();
        above + self.line_info().row_offset
    }

    /// `repositionView` against the line count of the last rendered view.
    fn reposition_view(&mut self) {
        let row = self.cursor_line_number();
        let mut vp = self.vp();
        let min = vp.y_offset;
        let max = min + vp.height.max(1) - 1;
        let max_offset = vp.lines.saturating_sub(vp.height);
        // viewport LineUp/LineDown: no-ops at the top/bottom or without
        // content, otherwise SetYOffset clamps to [0, maxYOffset].
        if row < min {
            if min > 0 && vp.lines > 0 {
                vp.y_offset = row.min(max_offset);
            }
        } else if row > max && min < max_offset && vp.lines > 0 {
            vp.y_offset = (min + (row - max)).min(max_offset);
        }
    }

    fn format_line_number(&self, n: &str) -> String {
        let digits = self.max_height.to_string().len();
        format!(" {n:>digits$} ")
    }

    fn state_styles<'a>(
        &self,
        styles: &'a AreaStyles,
        initial: &'a AreaStateStyles,
    ) -> &'a AreaStateStyles {
        match self.style_state {
            AreaStyleState::Initial => initial,
            AreaStyleState::Focused => &styles.focused,
            AreaStyleState::Blurred => &styles.blurred,
        }
    }

    /// bubbles `View`: soft-wrapped rows, prompt, line numbers, cursor,
    /// end-of-buffer filler, scrolled to the cursor.
    pub(crate) fn view(&self, styles: &AreaStyles) -> String {
        let initial = AreaStateStyles::bubbles_blurred();
        let st = self.state_styles(styles, &initial);
        if self.value().is_empty() && self.row == 0 && self.col == 0 && !self.placeholder.is_empty()
        {
            return self.placeholder_view(st, &styles.cursor);
        }
        let cursor_line = st.cursor_line();
        let text = st.text();
        let prompt_style = st.prompt();
        let line_number = st.line_number();
        let cursor_line_number = st.cursor_line_number();
        let line_info = self.line_info();
        let mut s = String::new();
        let mut widest_line_number = 0;
        for (l, line) in self.value.iter().enumerate() {
            let wrapped = wrap(line, self.width);
            let style = if self.row == l { &cursor_line } else { &text };
            for (wl, wrapped_line) in wrapped.iter().enumerate() {
                s += &style.render(&prompt_style.render(&self.prompt));
                let mut ln = String::new();
                if self.show_line_numbers {
                    let number = if wl == 0 {
                        (l + 1).to_string()
                    } else {
                        " ".to_string()
                    };
                    let ln_style = if self.row == l {
                        &cursor_line_number
                    } else {
                        &line_number
                    };
                    ln = style.render(&ln_style.render(&self.format_line_number(&number)));
                    s += &ln;
                }
                widest_line_number = widest_line_number.max(style::width(&ln));
                let mut wrapped_line: &[char] = wrapped_line;
                let str_width = text_width(wrapped_line);
                let mut padding = self.width as isize - str_width as isize;
                if str_width > self.width {
                    if wrapped_line.last() == Some(&' ') {
                        wrapped_line = &wrapped_line[..wrapped_line.len() - 1];
                    }
                    padding -= self.width as isize - str_width as isize;
                }
                if self.row == l && line_info.row_offset == wl {
                    if line_info.column_offset > wrapped_line.len() {
                        self.fault.set(Some(EditorFault::SliceOutOfRange {
                            high: line_info.column_offset,
                            len: wrapped_line.len(),
                        }));
                    }
                    let co = line_info.column_offset.min(wrapped_line.len());
                    s += &style.render(&wrapped_line[..co].iter().collect::<String>());
                    if self.col >= line.len() && line_info.char_offset >= self.width {
                        s += &self.cursor.view(" ", &styles.cursor, &cursor_line);
                    } else {
                        // Go indexes wrappedLine[ColumnOffset]: a missing cell
                        // is a Go panic, reported as a fault (drawn as a space).
                        if co >= wrapped_line.len() && self.fault.get().is_none() {
                            self.fault.set(Some(EditorFault::IndexOutOfRange {
                                index: co,
                                len: wrapped_line.len(),
                            }));
                        }
                        let ch = wrapped_line
                            .get(co)
                            .map_or(" ".to_string(), |c| c.to_string());
                        s += &style.render(&self.cursor.view(&ch, &styles.cursor, &cursor_line));
                        let rest = wrapped_line.get(co + 1..).unwrap_or(&[]);
                        s += &style.render(&rest.iter().collect::<String>());
                    }
                } else {
                    s += &style.render(&wrapped_line.iter().collect::<String>());
                }
                s += &style.render(&" ".repeat(padding.max(0) as usize));
                s.push('\n');
            }
        }
        let eob = st.end_of_buffer();
        for _ in 0..self.height {
            s += &prompt_style.render(&self.prompt);
            let gutter = self.end_of_buffer.to_string();
            let gap = (self.width + widest_line_number) as isize - style::width(&gutter) as isize;
            s += &eob.render(&(gutter + &" ".repeat(gap.max(0) as usize)));
            s.push('\n');
        }
        self.viewport_view(&s, st)
    }

    fn placeholder_view(&self, st: &AreaStateStyles, cursor_style: &Style) -> String {
        let style = st.placeholder();
        let wrapped = hardwrap(&wordwrap(&self.placeholder, self.width), self.width);
        let plines: Vec<&str> = wrapped.trim().split('\n').collect();
        let mut s = String::new();
        for i in 0..self.height {
            let (line_style, ln_style) = if plines.len() > i {
                (st.cursor_line(), st.cursor_line_number())
            } else {
                (st.placeholder(), st.line_number())
            };
            s += &line_style.render(&st.prompt().render(&self.prompt));
            if self.show_line_numbers && (i == 0 || plines.len() > i) {
                let ln = if i == 0 { "1" } else { "" };
                s += &line_style.render(&ln_style.render(&self.format_line_number(ln)));
            }
            if i == 0 {
                let first = plines[0];
                // Go takes the first *byte* of the line (pb placeholders are
                // ASCII); an all-blank placeholder makes it index an empty string.
                if first.is_empty() {
                    self.fault
                        .set(Some(EditorFault::IndexOutOfRange { index: 0, len: 0 }));
                }
                let mut chars = first.chars();
                let head = chars.next().map_or(" ".to_string(), |c| c.to_string());
                let rest: String = chars.collect();
                s += &line_style.render(&self.cursor.view(&head, cursor_style, &st.placeholder()));
                let pad = (self.width as isize - style::width(first) as isize).max(0) as usize;
                s += &line_style.render(&style.render(&(rest + &" ".repeat(pad))));
            } else if plines.len() > i {
                let pad = (self.width as isize - style::width(plines[i]) as isize).max(0) as usize;
                s += &line_style.render(&style.render(&(plines[i].to_string() + &" ".repeat(pad))));
            } else {
                s += &st.end_of_buffer().render(&self.end_of_buffer.to_string());
            }
            s.push('\n');
        }
        self.viewport_view(&s, st)
    }

    /// viewport `SetContent` + `View`, then the base style.
    fn viewport_view(&self, content: &str, st: &AreaStateStyles) -> String {
        let lines: Vec<&str> = content.split('\n').collect();
        let mut vp = self.vp();
        vp.lines = lines.len();
        if vp.y_offset > lines.len() - 1 {
            vp.y_offset = lines.len().saturating_sub(vp.height);
        }
        let top = vp.y_offset;
        let bottom = (top + vp.height).clamp(top, lines.len());
        let visible = lines[top..bottom].join("\n");
        let (w, h) = (vp.width, vp.height);
        drop(vp);
        let contents = Style::new()
            .width(w)
            .height(h)
            .max_height(h)
            .max_width(w)
            .render(&visible);
        st.base.render(&contents)
    }
}

/// `LineInfo` for a cursor column (Go evaluates it with transient negative columns).
fn line_info_at(line: &[char], width: usize, col: isize) -> LineInfo {
    let grid = wrap(line, width);
    let mut counter: isize = 0;
    for (i, wrapped) in grid.iter().enumerate() {
        let len = wrapped.len() as isize;
        if counter + len == col && i + 1 < grid.len() {
            return LineInfo {
                char_offset: 0,
                column_offset: 0,
                height: grid.len(),
                row_offset: i + 1,
                start_column: col.max(0) as usize,
                width: grid[i + 1].len(),
                char_width: text_width(wrapped),
            };
        }
        if counter + len >= col {
            let upto = (col - counter).max(0) as usize;
            return LineInfo {
                char_offset: text_width(&wrapped[..upto.min(wrapped.len())]),
                column_offset: (col - counter).max(0) as usize,
                height: grid.len(),
                row_offset: i,
                start_column: counter as usize,
                width: wrapped.len(),
                char_width: text_width(wrapped),
            };
        }
        counter += len;
    }
    LineInfo::default()
}

/// bubbles textarea soft wrap: words move whole to the next row, whitespace
/// becomes ASCII spaces, and every result carries a trailing space cell.
fn wrap(runes: &[char], width: usize) -> Vec<Vec<char>> {
    let mut lines: Vec<Vec<char>> = vec![Vec::new()];
    let mut word: Vec<char> = Vec::new();
    let mut row = 0;
    let mut spaces = 0usize;
    for &r in runes {
        if is_space(r) {
            spaces += 1;
        } else {
            word.push(r);
        }
        if spaces > 0 {
            if text_width(&lines[row]) + text_width(&word) + spaces > width {
                row += 1;
                lines.push(Vec::new());
            }
            lines[row].append(&mut word);
            lines[row].extend(std::iter::repeat_n(' ', spaces));
            spaces = 0;
        } else if let Some(&last) = word.last()
            && text_width(&word) + style::bubbles_rune_width(last) > width
        {
            // A double-width last rune may not fit: start a new row unless
            // this one is empty.
            if !lines[row].is_empty() {
                row += 1;
                lines.push(Vec::new());
            }
            lines[row].append(&mut word);
        }
    }
    if text_width(&lines[row]) + text_width(&word) + spaces >= width {
        lines.push(word);
        lines[row + 1].extend(std::iter::repeat_n(' ', spaces + 1));
    } else {
        lines[row].append(&mut word);
        lines[row].extend(std::iter::repeat_n(' ', spaces + 1));
    }
    lines
}

/// x/ansi v0.4.5 `Wordwrap(s, limit, "")` for plain (escape-free) text.
fn wordwrap(s: &str, limit: usize) -> String {
    if limit < 1 {
        return s.to_string();
    }
    let mut buf = String::new();
    let mut word = String::new();
    let mut space = String::new();
    let (mut cur, mut word_len) = (0usize, 0usize);
    fn add_word(
        buf: &mut String,
        word: &mut String,
        space: &mut String,
        cur: &mut usize,
        word_len: &mut usize,
    ) {
        if word.is_empty() {
            return;
        }
        *cur += space.len();
        buf.push_str(space);
        space.clear();
        *cur += *word_len;
        buf.push_str(word);
        word.clear();
        *word_len = 0;
    }
    for c in s.chars() {
        if c.is_ascii() {
            if c == '\n' {
                if word_len == 0 {
                    if cur + space.len() > limit {
                        cur = 0;
                    } else {
                        buf.push_str(&space);
                    }
                    space.clear();
                }
                add_word(&mut buf, &mut word, &mut space, &mut cur, &mut word_len);
                buf.push('\n');
                cur = 0;
                space.clear();
            } else if is_space(c) {
                add_word(&mut buf, &mut word, &mut space, &mut cur, &mut word_len);
                space.push(c);
            } else if c == '-' {
                cur += space.len();
                buf.push_str(&space);
                space.clear();
                add_word(&mut buf, &mut word, &mut space, &mut cur, &mut word_len);
                buf.push(c);
                cur += 1;
            } else {
                word.push(c);
                word_len += 1;
                if cur + space.len() + word_len > limit && word_len < limit {
                    buf.push('\n');
                    cur = 0;
                    space.clear();
                }
            }
        } else if is_space(c) && c != '\u{00A0}' {
            add_word(&mut buf, &mut word, &mut space, &mut cur, &mut word_len);
            space.push(c);
        } else {
            word.push(c);
            word_len += style::width(c.encode_utf8(&mut [0; 4]));
            if cur + space.len() + word_len > limit && word_len < limit {
                buf.push('\n');
                cur = 0;
                space.clear();
            }
        }
    }
    add_word(&mut buf, &mut word, &mut space, &mut cur, &mut word_len);
    buf
}

/// x/ansi v0.4.5 `Hardwrap(s, limit, true)` for plain text.
fn hardwrap(s: &str, limit: usize) -> String {
    if limit < 1 {
        return s.to_string();
    }
    let mut buf = String::new();
    let mut cur = 0usize;
    for c in s.chars() {
        if c == '\n' {
            buf.push('\n');
            cur = 0;
            continue;
        }
        // ASCII bytes always reserve one cell for the wrap check; only
        // printable ones advance the width.
        let (needed, advance) = if c.is_ascii() {
            (1, usize::from(!c.is_ascii_control()))
        } else {
            let w = style::width(c.encode_utf8(&mut [0; 4]));
            (w, w)
        };
        if cur + needed > limit {
            buf.push('\n');
            cur = 0;
        }
        buf.push(c);
        cur += advance;
    }
    buf
}

/// Go `unicode.ToUpper` (simple case mapping).
fn simple_upper(c: char) -> char {
    let mut up = c.to_uppercase();
    match (up.next(), up.next()) {
        (Some(u), None) => u,
        _ => c,
    }
}

/// Go `unicode.ToLower` (simple case mapping; U+0130 maps to `i`).
fn simple_lower(c: char) -> char {
    c.to_lowercase().next().unwrap_or(c)
}

/// Go `unicode.ToTitle`: uppercase except the Latin digraph title forms.
fn simple_title(c: char) -> char {
    match c {
        '\u{01C4}'..='\u{01C6}' => '\u{01C5}',
        '\u{01C7}'..='\u{01C9}' => '\u{01C8}',
        '\u{01CA}'..='\u{01CC}' => '\u{01CB}',
        '\u{01F1}'..='\u{01F3}' => '\u{01F2}',
        _ => simple_upper(c),
    }
}

#[cfg(test)]
mod tests {
    use super::super::input::{Event, parse};
    use super::*;

    fn keys(bytes: &[u8]) -> Vec<Key> {
        parse(bytes)
            .into_iter()
            .filter_map(|e| match e {
                Event::Key(k) => Some(k),
                _ => None,
            })
            .collect()
    }

    fn feed(t: &mut TextInput, chunks: &[&[u8]]) {
        for chunk in chunks {
            for k in keys(chunk) {
                t.update(&k);
            }
        }
    }

    fn feed_area(t: &mut TextArea, chunks: &[&[u8]]) {
        for chunk in chunks {
            for k in keys(chunk) {
                t.update(&k);
            }
        }
    }

    fn input() -> TextInput {
        let mut t = TextInput::new();
        t.focus();
        t
    }

    fn strip_ansi(s: &str) -> String {
        let mut out = String::new();
        let mut chars = s.chars().peekable();
        while let Some(c) = chars.next() {
            if c == '\x1b' && chars.peek() == Some(&'[') {
                chars.next();
                for d in chars.by_ref() {
                    if ('@'..='~').contains(&d) {
                        break;
                    }
                }
            } else {
                out.push(c);
            }
        }
        out
    }

    #[test]
    fn input_typing_and_char_limit() {
        let mut t = input();
        t.set_char_limit(5);
        feed(&mut t, &[b"ab", b"cdefg"]);
        assert_eq!(t.value(), "abcde");
        assert_eq!(t.position(), 5);
        feed(&mut t, &[b"\x1b[D", b"\x1b[D", b"\x7f"]);
        assert_eq!(t.value(), "abde");
        assert_eq!(t.position(), 2);
    }

    #[test]
    fn input_blurred_ignores_keys() {
        let mut t = TextInput::new();
        feed(&mut t, &[b"abc"]);
        assert_eq!(t.value(), "");
    }

    #[test]
    fn input_paste_sanitizes_tabs_newlines_controls() {
        let mut t = input();
        feed(&mut t, &[b"\x1b[200~a\tb\r\nc\x07d\x1b[201~"]);
        assert_eq!(t.value(), "a b  cd");
    }

    #[test]
    fn input_set_value_cursor_rules() {
        let mut t = input();
        t.set_value("hello");
        assert_eq!(t.position(), 5);
        t.set_cursor(0);
        t.set_value("world!");
        assert_eq!(t.position(), 0, "non-empty old value keeps cursor 0");
        t.set_cursor(6);
        t.set_value("ab");
        assert_eq!(t.position(), 2);
    }

    #[test]
    fn input_word_motion_and_deletion() {
        let mut t = input();
        t.set_value("foo  bar baz");
        feed(&mut t, &[b"\x1bb"]);
        assert_eq!(t.position(), 9);
        feed(&mut t, &[b"\x1bb"]);
        assert_eq!(t.position(), 5);
        feed(&mut t, &[b"\x1bf"]);
        assert_eq!(t.position(), 8);
        feed(&mut t, &[b"\x05", b"\x17"]);
        assert_eq!(t.value(), "foo  bar ");
        feed(&mut t, &[b"\x17"]);
        assert_eq!(t.value(), "foo  ");
        feed(&mut t, &[b"\x01", b"\x1bd"]);
        assert_eq!(t.value(), "  ");
    }

    #[test]
    fn input_delete_word_forward_on_last_char() {
        let mut t = input();
        t.set_value("ab");
        feed(&mut t, &[b"\x1b[D", b"\x1bd"]);
        // Go: "runtime error: index out of range [2] with length 2" (oracle).
        assert_eq!(
            t.take_fault().map(|f| f.to_string()).as_deref(),
            Some("runtime error: index out of range [2] with length 2")
        );
        assert_eq!(t.value(), "ab");
        assert_eq!(t.take_fault(), None);
    }

    #[test]
    fn input_password_masks_word_ops() {
        let mut t = input();
        t.set_echo(EchoMode::Password('\u{2022}'));
        t.set_value("ab cd");
        feed(&mut t, &[b"\x1b[D", b"\x17"]);
        assert_eq!(t.value(), "d");
        assert_eq!(t.position(), 0);
        feed(&mut t, &[b"\x1bf"]);
        assert_eq!(t.position(), 1);
    }

    #[test]
    fn input_unicode_is_rune_indexed() {
        let mut t = input();
        feed(&mut t, &["日本e\u{301}".as_bytes()]);
        assert_eq!(t.position(), 4);
        feed(&mut t, &[b"\x7f"]);
        assert_eq!(t.value(), "日本e");
    }

    #[test]
    fn input_ctrl_k_u_and_alt_rune_inserts() {
        let mut t = input();
        t.set_value("hello world");
        t.set_cursor(5);
        feed(&mut t, &[b"\x0b"]);
        assert_eq!(t.value(), "hello");
        feed(&mut t, &[b"\x1b[D", b"\x15"]);
        assert_eq!(t.value(), "o");
        feed(&mut t, &[b"\x1bx"]);
        assert_eq!(
            t.value(),
            "xo",
            "unbound alt+rune inserts its rune, as in Go"
        );
    }

    #[test]
    fn input_horizontal_scroll_window() {
        let mut t = input();
        t.set_width(5);
        feed(&mut t, &[b"abcdefgh"]);
        assert_eq!((t.offset, t.offset_right), (3, 8));
        assert_eq!(strip_ansi(&t.view(&InputStyles::default())), "> defgh ");
        feed(&mut t, &[b"\x01"]);
        assert_eq!((t.offset, t.offset_right), (0, 6));
        // bubbles shows six cells in a width-5 field here; kept.
        assert_eq!(strip_ansi(&t.view(&InputStyles::default())), "> abcdef");
    }

    #[test]
    fn input_view_layout() {
        let mut t = input();
        t.set_width(6);
        t.set_value("abc");
        assert_eq!(strip_ansi(&t.view(&InputStyles::default())), "> abc    ");
        t.set_cursor(1);
        assert_eq!(strip_ansi(&t.view(&InputStyles::default())), "> abc    ");
        t.set_echo(EchoMode::Password('*'));
        assert_eq!(strip_ansi(&t.view(&InputStyles::default())), "> ***    ");
        let mut p = TextInput::new();
        p.set_placeholder("filter");
        p.set_width(10);
        assert_eq!(
            strip_ansi(&p.view(&InputStyles::default())),
            "> filter     "
        );
    }

    #[test]
    fn input_blink_cycle() {
        let mut t = TextInput::new();
        assert!(t.blink_deadline().is_none());
        t.focus();
        let d = t.blink_deadline().expect("focus arms blink");
        assert!(!t.cursor.hidden);
        assert!(!t.tick(d - Duration::from_millis(1)));
        assert!(t.tick(d));
        assert!(t.cursor.hidden);
        feed(&mut t, &[b"a"]);
        assert!(!t.cursor.hidden, "movement shows the cursor");
        t.blur();
        assert!(t.blink_deadline().is_none());
    }

    /// bubbles cursor lifetime: one live timer per cursor, replaced by each
    /// BlinkCmd, kept by Blur, accepted only with the current tag.
    #[test]
    fn input_blink_timer_survives_blur_and_matches_by_tag() {
        let t0 = Instant::now();
        let ms = |n: u64| t0 + Duration::from_millis(n);
        let mut t = TextInput::new();
        // Focus() whose command is discarded, then textinput.Blink.
        t.focus();
        t.disarm_blink();
        assert_eq!(t.pending_blink(), None);
        t.start_blink_at(ms(0));
        assert_eq!(
            t.pending_blink(),
            Some(BlinkTimer {
                due: ms(530),
                tag: 2
            })
        );
        // A move cancels the live timer and arms the next tag.
        t.update_at(&keys(b"a")[0], ms(10));
        assert_eq!(
            t.pending_blink(),
            Some(BlinkTimer {
                due: ms(540),
                tag: 3
            })
        );
        // Blur hides the cursor and keeps the timer; polling callers and
        // every delivery ignore it while blurred.
        t.blur();
        assert!(t.cursor.hidden);
        assert_eq!(t.blink_deadline(), None);
        assert_eq!(t.pending_blink().map(|p| p.tag), Some(3));
        assert!(!t.tick(ms(540)));
        assert!(!t.deliver_blink(3, ms(540)));
        assert!(t.cursor.hidden);
        // Refocusing cancels it even when Focus's command is discarded.
        t.focus();
        t.disarm_blink();
        assert_eq!(t.pending_blink(), None);
        t.start_blink_at(ms(600));
        assert_eq!(t.pending_blink().map(|p| p.tag), Some(5));
        // A stale tag is dropped and leaves the timer alone.
        assert!(!t.deliver_blink(4, ms(610)));
        assert!(!t.cursor.hidden);
        assert_eq!(t.pending_blink().map(|p| p.tag), Some(5));
        // The current tag toggles and re-arms, whichever timer carried it.
        assert!(t.deliver_blink(5, ms(620)));
        assert!(t.cursor.hidden);
        assert_eq!(
            t.pending_blink(),
            Some(BlinkTimer {
                due: ms(1150),
                tag: 6
            })
        );
        // A fired timer is taken: nothing stays scheduled.
        assert_eq!(t.take_pending_blink().map(|p| p.tag), Some(6));
        assert_eq!(t.pending_blink(), None);
        assert_eq!(t.blink_deadline(), None);
        assert!(!t.tick(ms(2000)));
    }

    fn area() -> TextArea {
        let mut t = TextArea::new();
        t.set_max_height(0);
        t.set_max_width(0);
        t.set_keymap(&PB_AREA_KEYS);
        t.focus();
        t
    }

    #[test]
    fn area_defaults_match_bubbles() {
        let t = TextArea::new();
        assert_eq!((t.width(), t.height()), (34, 6));
        assert_eq!(t.char_limit, 400);
        assert_eq!(t.value(), "");
    }

    #[test]
    fn area_newlines_merge_and_split() {
        let mut t = area();
        feed_area(&mut t, &[b"ab", b"\r", b"cd"]);
        assert_eq!(t.value(), "ab\ncd");
        assert_eq!(t.cursor(), (1, 2));
        feed_area(&mut t, &[b"\x01", b"\x7f"]);
        assert_eq!(t.value(), "abcd");
        assert_eq!(t.cursor(), (0, 2));
        feed_area(&mut t, &[b"\x05", b"\x0b"]);
        assert_eq!(t.value(), "abcd");
    }

    #[test]
    fn area_paste_keeps_newlines_and_expands_tabs() {
        let mut t = area();
        feed_area(&mut t, &[b"\x1b[200~a\tb\r\nc\x1b[201~"]);
        assert_eq!(t.value(), "a    b\n\nc");
        assert_eq!(t.cursor(), (2, 1));
    }

    #[test]
    fn area_char_limit_counts_cells_and_newlines() {
        let mut t = area();
        t.set_char_limit(5);
        // Go truncates by rune count against a cell-width budget, so wide
        // runes can overshoot the limit.
        t.insert_str("日本\nabc");
        assert_eq!(t.value(), "日本\nab");
        assert_eq!(t.length(), 7);
    }

    #[test]
    fn area_word_left_at_blank_start_terminates() {
        let mut t = area();
        t.set_value("  x");
        t.set_cursor(0);
        feed_area(&mut t, &[b"\x1bb"]);
        assert_eq!(t.cursor(), (0, 0));
        assert_eq!(t.take_fault(), Some(EditorFault::Hang));
        let mut empty = area();
        feed_area(&mut empty, &[b"\x1bb"]);
        assert_eq!(empty.take_fault(), Some(EditorFault::Hang));
    }

    #[test]
    fn ctrl_v_requests_clipboard_and_paste_inserts() {
        let mut t = input();
        t.set_value("ab");
        feed(&mut t, &[b"\x16"]);
        assert!(t.take_paste_request());
        assert!(!t.take_paste_request());
        t.insert_paste("x\ny");
        assert_eq!(t.value(), "abx y");
        let mut a = area();
        feed_area(&mut a, &[b"\x16"]);
        assert!(a.take_paste_request());
        assert_eq!(a.value(), "", "Ctrl+V itself inserts nothing");
        t.blur();
        t.insert_paste("z");
        assert_eq!(t.value(), "abx y", "blurred editors ignore paste, like Go");
    }

    #[test]
    fn area_word_motion_crosses_lines() {
        let mut t = area();
        t.set_value("select a\n  from b");
        t.row = 0;
        t.set_cursor(7);
        feed_area(&mut t, &[b"\x1bf"]);
        assert_eq!(t.cursor(), (0, 8));
        feed_area(&mut t, &[b"\x1bf"]);
        assert_eq!(t.cursor(), (1, 6));
        feed_area(&mut t, &[b"\x1bb", b"\x1bb"]);
        assert_eq!(t.cursor(), (0, 7));
    }

    #[test]
    fn area_case_and_transpose() {
        let mut t = area();
        t.set_value("straße ab");
        t.set_cursor(0);
        feed_area(&mut t, &[b"\x1bu"]);
        assert_eq!(t.value(), "STRAßE ab");
        feed_area(&mut t, &[b"\x1bc"]);
        assert_eq!(t.value(), "STRAßE Ab");
        feed_area(&mut t, &[b"\x14"]);
        assert_eq!(t.value(), "STRAßE bA");
    }

    #[test]
    fn area_soft_wrap_grid() {
        let grid = wrap(&"hello world foo".chars().collect::<Vec<_>>(), 10);
        let rows: Vec<String> = grid.iter().map(|r| r.iter().collect()).collect();
        assert_eq!(rows, ["hello ", "world foo "]);
        let grid = wrap(&"abcdefghij".chars().collect::<Vec<_>>(), 10);
        let rows: Vec<String> = grid.iter().map(|r| r.iter().collect()).collect();
        assert_eq!(rows, ["abcdefghij", " "]);
    }

    #[test]
    fn area_vertical_motion_keeps_column() {
        let mut t = area();
        t.set_value("abcdef\nab\nabcdef");
        t.row = 0;
        t.set_cursor(5);
        feed_area(&mut t, &[b"\x1b[B"]);
        assert_eq!(t.cursor(), (1, 2));
        feed_area(&mut t, &[b"\x1b[B"]);
        assert_eq!(t.cursor(), (2, 5));
    }

    #[test]
    fn area_view_has_height_rows_and_placeholder() {
        let mut t = TextArea::new();
        t.set_max_height(0);
        t.set_height(3);
        t.set_width(20);
        t.set_placeholder("Write your queries here");
        t.focus();
        let view = strip_ansi(&t.view(&AreaStyles::default()));
        let rows: Vec<&str> = view.split('\n').collect();
        assert_eq!(rows.len(), 3);
        assert!(rows[0].starts_with("\u{2503}  1 Write your"), "{rows:?}");
        t.set_value("select 1");
        let view = strip_ansi(&t.view(&AreaStyles::default()));
        let rows: Vec<&str> = view.split('\n').collect();
        assert_eq!(rows.len(), 3);
        assert!(rows[0].starts_with("\u{2503}  1 select 1"), "{rows:?}");
        assert!(rows.iter().all(|r| style::width(r) == 20), "{rows:?}");
    }

    /// Expected strings captured from bubbles v0.20.0 (docs/phase7-editor.md, oracle).
    #[test]
    fn area_view_matches_go_oracle() {
        let mut t = TextArea::new();
        t.set_max_height(0);
        t.set_height(3);
        t.set_width(20);
        t.set_placeholder("Write your queries here");
        t.focus();
        let styles = AreaStyles::default();
        assert_eq!(
            strip_ansi(&t.view(&styles)),
            "┃  1 Write your     \n┃    queries here   \n┃                   "
        );
        t.set_value("select 1");
        assert_eq!(
            strip_ansi(&t.view(&styles)),
            "┃  1 select 1       \n┃                   \n┃                   "
        );
    }

    #[test]
    fn area_scrolls_with_cursor_like_go() {
        let styles = AreaStyles::default();
        let mut t = area();
        t.set_height(2);
        t.set_width(30);
        let _ = t.view(&styles);
        for line in ["l1", "l2", "l3", "l4"] {
            feed_area(&mut t, &[line.as_bytes(), b"\r"]);
            let _ = t.view(&styles);
        }
        assert_eq!(
            strip_ansi(&t.view(&styles)),
            "┃  4 l4                       \n┃  5                          "
        );
        feed_area(&mut t, &[b"\x1b[A", b"\x1b[A", b"\x1b[A", b"\x1b[A"]);
        assert_eq!(
            strip_ansi(&t.view(&styles)),
            "┃  1 l1                       \n┃  2 l2                       "
        );
    }

    #[test]
    fn area_clone_shares_viewport_like_go() {
        let styles = AreaStyles::default();
        let mut t = area();
        t.set_size(30, 10);
        let mut copy = t.clone();
        copy.set_size(30, 2);
        let _ = copy.view(&styles);
        assert_eq!(
            t.vp().height,
            2,
            "SetHeight on a Go copy resizes the shared viewport"
        );
        assert_eq!(t.height(), 10);
    }

    // Width-source tests. Oracle (style-width-oracle.txt): "〰" uniseg 1 /
    // go-runewidth 2; "☺️" (U+263A U+FE0F) uniseg 2 / go-runewidth 1. They
    // assume a non-CJK locale (go-runewidth DefaultCondition), as in CI.

    /// handleOverflow gates on uniseg width but sizes the window with
    /// go-runewidth per rune: uniseg rune widths would give (1, 4).
    #[test]
    fn input_overflow_window_uses_runewidth() {
        let mut t = input();
        t.set_width(3);
        feed(&mut t, &["〰〰〰〰".as_bytes()]);
        assert_eq!((t.offset, t.offset_right), (3, 4));
        let mut fits = input();
        fits.set_width(4);
        feed(&mut fits, &["〰〰〰〰".as_bytes()]);
        assert_eq!(
            (fits.offset, fits.offset_right),
            (0, 4),
            "uniseg width 4 fits"
        );
    }

    /// The password mask repeats the echo char by uniseg width (2 for an
    /// emoji-presentation sequence), not by rune or runewidth count.
    #[test]
    fn input_password_mask_uses_uniseg_width() {
        let mut t = input();
        t.set_echo(EchoMode::Password('*'));
        t.set_value("\u{263A}\u{FE0F}");
        assert_eq!(strip_ansi(&t.view(&InputStyles::default())), "> ** ");
    }

    /// textarea `Length` (char limit budget) is uniseg-based.
    #[test]
    fn area_length_uses_uniseg_width() {
        let mut t = area();
        t.insert_str("\u{263A}\u{FE0F}\n〰");
        assert_eq!(t.length(), 2 + 1 + 1);
    }

    /// Soft wrap measures rows with uniseg but the double-width carry with
    /// go-runewidth: uniseg-only would give ["〰〰〰", " "].
    #[test]
    fn wrap_carry_uses_runewidth() {
        let grid = wrap(&"〰〰〰".chars().collect::<Vec<_>>(), 3);
        let rows: Vec<String> = grid.iter().map(|r| r.iter().collect()).collect();
        assert_eq!(rows, ["〰〰", "〰 "]);
    }

    /// CursorDown keeps the uniseg char offset (2 for "ab") but walks the
    /// next line with go-runewidth widths: one "〰" already covers it.
    #[test]
    fn area_vertical_motion_walks_runewidth() {
        let mut t = area();
        t.set_value("abcd\n〰〰〰");
        t.row = 0;
        t.set_cursor(2);
        feed_area(&mut t, &[b"\x1b[B"]);
        assert_eq!(t.cursor(), (1, 1));
    }

    #[test]
    fn wordwrap_matches_ansi_for_placeholder() {
        assert_eq!(
            wordwrap("Write your queries here", 10),
            "Write your\nqueries\nhere"
        );
        assert_eq!(hardwrap("abcdef", 4), "abcd\nef");
    }
}
