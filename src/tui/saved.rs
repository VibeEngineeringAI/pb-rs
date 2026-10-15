//! Owned saved-query menu. Apply/quit are dispatched before filter input and
//! browsing; there is deliberately no delete state or delete effect.
use super::{
    editor::{EditorFault, InputStyles, TextInput},
    input::{Event, Key},
    style::{Border, Color, Role, Style, active},
    terminal::{Screen, TerminalSession},
};
use crate::{error::CliError, saved_queries::SavedSelection};

fn session_error(error: impl std::fmt::Display) -> CliError {
    CliError::new(crate::ErrorCode::CommandFailed, error.to_string())
}
pub(crate) fn select(items: Vec<SavedSelection>) -> Result<Option<SavedSelection>, CliError> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(session_error)?;
    runtime.block_on(drive(items))
}

enum DriverEvent {
    Input(Option<Event>),
    Clipboard(Result<Result<String, String>, tokio::task::JoinError>),
    Tick,
}
type ClipboardTasks = tokio::task::JoinSet<Result<String, String>>;

async fn next_driver_event(
    terminal: &mut TerminalSession,
    tasks: &mut ClipboardTasks,
    deadline: Option<std::time::Instant>,
) -> DriverEvent {
    use std::{future::Future, task::Poll};
    let mut input = std::pin::pin!(terminal.next_event());
    let mut tick = deadline.map(|deadline| Box::pin(tokio::time::sleep_until(deadline.into())));
    std::future::poll_fn(|cx| {
        if let Poll::Ready(event) = input.as_mut().poll(cx) {
            return Poll::Ready(DriverEvent::Input(event));
        }
        if !tasks.is_empty()
            && let Poll::Ready(Some(result)) = tasks.poll_join_next(cx)
        {
            return Poll::Ready(DriverEvent::Clipboard(result));
        }
        if let Some(tick) = &mut tick
            && tick.as_mut().poll(cx).is_ready()
        {
            return Poll::Ready(DriverEvent::Tick);
        }
        Poll::Pending
    })
    .await
}

async fn stop_clipboards(tasks: &mut ClipboardTasks) {
    tasks.abort_all();
    while tasks.join_next().await.is_some() {}
}

fn recovered_panic_text(value: impl std::fmt::Display) -> String {
    format!("Caught panic:\n\n{value}\n\nRestoring terminal...\n\n")
}

async fn recovered_panic(
    terminal: TerminalSession,
    mut tasks: ClipboardTasks,
    value: impl std::fmt::Display,
    selection: Option<SavedSelection>,
) -> Result<Option<SavedSelection>, CliError> {
    stop_clipboards(&mut tasks).await;
    drop(terminal); // Bubble Tea shutdown(true), without a final render.
    use std::io::Write;
    let _ = std::io::stdout()
        .lock()
        .write_all(recovered_panic_text(value).as_bytes());
    // Go's runtime-specific debug.PrintStack is omitted consistently with
    // SQL/PromQL (L-EDITOR-4), rather than replaced with a Rust stack.
    Ok(selection)
}

async fn editor_fault(
    terminal: TerminalSession,
    tasks: ClipboardTasks,
    fault: EditorFault,
    selection: Option<SavedSelection>,
) -> Result<Option<SavedSelection>, CliError> {
    if fault == EditorFault::Hang {
        // Go's update never returns: retain the session and stop consuming
        // input/completions. The shared signal handler's Quit stays unread.
        std::future::pending::<()>().await;
        unreachable!();
    }
    // SavedQueryList has no type assertion after Run: zero global selection
    // produces a successful no-op, rather than the second panic of forms.
    recovered_panic(terminal, tasks, fault, selection).await
}

async fn command_panic(
    terminal: TerminalSession,
    mut tasks: ClipboardTasks,
    value: impl std::fmt::Display,
) -> Result<Option<SavedSelection>, CliError> {
    stop_clipboards(&mut tasks).await;
    drop(terminal); // Command-goroutine recovery: no final render.
    use std::io::Write;
    let _ = std::io::stdout()
        .lock()
        .write_all(super::profile::caught_panic_stdout(&value.to_string()).as_bytes());
    Err(session_error("program was killed: context canceled"))
}

async fn finish(
    terminal: TerminalSession,
    mut tasks: ClipboardTasks,
    state: &State,
    selection: Option<SavedSelection>,
) -> Result<Option<SavedSelection>, CliError> {
    let view = state.view();
    if let Some(fault) = state.take_fault() {
        return editor_fault(terminal, tasks, fault, selection).await;
    }
    stop_clipboards(&mut tasks).await;
    let _ = terminal.close_bytes(&view);
    Ok(selection)
}

async fn drive(items: Vec<SavedSelection>) -> Result<Option<SavedSelection>, CliError> {
    let mut terminal = TerminalSession::open(Screen::Fullscreen).map_err(session_error)?;
    let mut state = State::new(items);
    let mut tasks = ClipboardTasks::new();
    loop {
        let view = state.view();
        if let Some(fault) = state.take_fault() {
            return editor_fault(terminal, tasks, fault, None).await;
        }
        terminal.draw_bytes(&view);
        match next_driver_event(&mut terminal, &mut tasks, state.next_tick()).await {
            DriverEvent::Input(Some(Event::Key(key))) => match state.update(&key) {
                Some(Effect::Finish(selection)) => {
                    return finish(terminal, tasks, &state, selection).await;
                }
                Some(Effect::Fault(fault)) => {
                    return editor_fault(terminal, tasks, fault, None).await;
                }
                Some(Effect::MatchFault(fault)) => {
                    return command_panic(terminal, tasks, fault).await;
                }
                Some(Effect::ReadClipboard) => {
                    tasks.spawn(super::clipboard::read());
                }
                None => {}
            },
            DriverEvent::Clipboard(Ok(Ok(text))) => match state.paste(&text) {
                Some(Effect::Fault(fault)) => {
                    return editor_fault(terminal, tasks, fault, None).await;
                }
                Some(Effect::MatchFault(fault)) => {
                    return command_panic(terminal, tasks, fault).await;
                }
                _ => {}
            },
            DriverEvent::Clipboard(_) => {} // Go pasteErrMsg is ignored.
            DriverEvent::Tick => {
                state.filter.tick(std::time::Instant::now());
            }
            DriverEvent::Input(Some(Event::Resize(size))) => {
                state.resize(size.width as usize, size.height as usize)
            }
            DriverEvent::Input(Some(Event::InputError(error))) => {
                // A panic in Run's final View replaces the read error with
                // Run's recovered zero return, just as in the profile form.
                let view = state.view();
                if let Some(fault) = state.take_fault() {
                    return editor_fault(terminal, tasks, fault, None).await;
                }
                stop_clipboards(&mut tasks).await;
                let _ = terminal.close_bytes(&view);
                return Err(session_error(error));
            }
            DriverEvent::Input(Some(Event::Panicked(value))) => {
                return command_panic(terminal, tasks, value).await;
            }
            DriverEvent::Input(Some(Event::Quit) | None) => {
                return finish(terminal, tasks, &state, None).await;
            }
            _ => {}
        }
    }
}

// This screen uses ui.Active, while the inline administration screens use
// adaptive palette roles resolved against the terminal background.
fn palette_style(role: Role) -> Style {
    Style::new().fg(Color::hex(active().hex(role)))
}

#[derive(Debug, Default, PartialEq, Eq)]
enum FilterMode {
    #[default]
    Off,
    Editing,
    Applied,
}
pub(crate) struct State {
    items: Vec<SavedSelection>,
    visible: Vec<usize>,
    selected: usize,
    filter: TextInput,
    mode: FilterMode,
    width: usize,
    height: usize,
}
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Effect {
    Finish(Option<SavedSelection>),
    ReadClipboard,
    Fault(EditorFault),
    MatchFault(MatchFault),
}
impl State {
    pub(crate) fn new(items: Vec<SavedSelection>) -> Self {
        let mut filter = TextInput::new();
        filter.set_prompt("Filter: ");
        filter.set_char_limit(64);
        filter.focus();
        filter.disarm_blink();
        Self {
            visible: (0..items.len()).collect(),
            items,
            selected: 0,
            filter,
            mode: FilterMode::Off,
            width: 0,
            height: 0,
        }
    }
    pub(crate) fn resize(&mut self, width: usize, height: usize) {
        self.width = width;
        self.height = height;
        self.selected = self.selected.min(self.visible.len().saturating_sub(1));
    }
    fn per_page(&self) -> usize {
        // List body is height-9, each delegate is 3 rows plus one spacing row.
        // Its title/filter row remains allocated even with the title hidden.
        let title_height = if self.mode == FilterMode::Editing {
            2
        } else {
            1
        };
        (self
            .height
            .saturating_sub(9)
            .max(5)
            .saturating_sub(title_height + 1)
            / 4)
        .max(1)
    }
    fn selected(&self) -> Option<&SavedSelection> {
        self.visible
            .get(self.selected)
            .map(|&index| &self.items[index])
    }
    fn next_tick(&self) -> Option<std::time::Instant> {
        if self.mode == FilterMode::Editing {
            self.filter.blink_deadline()
        } else {
            None
        }
    }
    pub(crate) fn update(&mut self, key: &Key) -> Option<Effect> {
        let name = key.name();
        match name.as_str() {
            "b" | "ctrl+c" => return Some(Effect::Finish(None)),
            "a" | "enter" => return Some(Effect::Finish(self.selected().cloned())),
            _ => {}
        }
        if self.mode == FilterMode::Editing {
            if let Some(effect) = self.edit_filter(key) {
                return Some(effect);
            }
            if self.filter.take_paste_request() {
                return Some(Effect::ReadClipboard);
            }
            return None;
        }
        let count = self.visible.len();
        let page = self.per_page();
        match name.as_str() {
            "q" => return Some(Effect::Finish(None)),
            "esc" if self.mode == FilterMode::Off => return Some(Effect::Finish(None)),
            "esc" => self.clear_filter(),
            "/" if !self.items.is_empty() => {
                self.mode = FilterMode::Editing;
                self.filter.focus();
                self.filter.cursor_end();
                self.filter.start_blink();
                self.selected = 0;
            }
            "up" | "k" => self.selected = self.selected.saturating_sub(1),
            "down" | "j" => self.selected = (self.selected + 1).min(count.saturating_sub(1)),
            "home" | "g" => self.selected = 0,
            "end" | "G" => self.selected = count.saturating_sub(1),
            "left" | "h" | "pgup" | "u" if self.selected >= page => {
                self.selected -= page;
            }
            "right" | "l" | "pgdown" | "f" | "d"
                if self.selected / page < count.saturating_sub(1) / page =>
            {
                self.selected = (self.selected + page).min(count.saturating_sub(1));
            }
            _ => {}
        }
        None
    }
    fn clear_filter(&mut self) {
        self.mode = FilterMode::Off;
        self.filter.reset();
        self.visible = (0..self.items.len()).collect();
        self.selected = 0;
    }
    fn edit_filter(&mut self, key: &Key) -> Option<Effect> {
        match key.name().as_str() {
            "esc" => {
                self.clear_filter();
                return None;
            }
            "tab" | "shift+tab" | "ctrl+k" | "ctrl+j" | "up" | "down"
                if !self.filter.value().is_empty() =>
            {
                if self.visible.is_empty() {
                    self.clear_filter();
                } else {
                    self.mode = FilterMode::Applied;
                    self.filter.blur();
                }
                return None;
            }
            _ => {}
        }
        let before = self.filter.value();
        self.filter.update(key);
        if let Some(fault) = self.filter.take_fault() {
            return Some(Effect::Fault(fault));
        }
        if before != self.filter.value() {
            return self.refilter().err().map(Effect::MatchFault);
        }
        None
    }
    fn take_fault(&self) -> Option<EditorFault> {
        self.filter.take_fault()
    }
    fn refilter(&mut self) -> Result<(), MatchFault> {
        let pattern = self.filter.value();
        if pattern.is_empty() {
            self.visible = (0..self.items.len()).collect();
        } else {
            let mut matches = Vec::new();
            for (index, item) in self.items.iter().enumerate() {
                if let Some(score) = match_score(&pattern, &item.title)? {
                    matches.push((index, score));
                }
            }
            // PTY oracle: equal-score titles retain their original list order.
            matches.sort_by_key(|&(_, score)| std::cmp::Reverse(score));
            self.visible = matches.into_iter().map(|(i, _)| i).collect();
        }
        self.selected = 0;
        Ok(())
    }
    fn paste(&mut self, text: &str) -> Option<Effect> {
        // A pasteMsg is delivered to the current list/filter, without a
        // request generation check. Browsing mode never updates FilterInput.
        if self.mode == FilterMode::Editing && self.filter.focused() {
            self.filter.insert_paste(text);
            if let Some(fault) = self.filter.take_fault() {
                return Some(Effect::Fault(fault));
            }
            return self.refilter().err().map(Effect::MatchFault);
        }
        None
    }
    fn view(&self) -> Vec<u8> {
        if self.width == 0 || self.height == 0 {
            return Vec::new();
        }
        let list_width = self.width.saturating_sub(6).max(20);
        let list_height = self.height.saturating_sub(9).max(5);
        let title = if self.mode == FilterMode::Editing {
            let styles = InputStyles {
                prompt: Style::new().fg(Color::adaptive("#04B575", "#ECFD65")),
                cursor: Style::new().fg(Color::hex("#EE6FF8")),
                ..Default::default()
            };
            format!("  {}\n", self.filter.view(&styles))
        } else {
            String::new()
        };
        let title_height = if self.mode == FilterMode::Editing {
            2
        } else {
            1
        };
        let content_height = list_height.saturating_sub(title_height + 1);
        let mut rows = Vec::new();
        let per_page = self.per_page();
        let page = self.selected / per_page;
        for (visible_index, &index) in self
            .visible
            .iter()
            .enumerate()
            .skip(page * per_page)
            .take(per_page)
        {
            let item = &self.items[index];
            let selected = visible_index == self.selected;
            let prefix = if selected {
                format!(
                    "{}   ",
                    Style::new()
                        .bg(Color::hex(active().hex(Role::Active)))
                        .render(" ")
                )
            } else {
                "    ".into()
            };
            let title_style =
                palette_style(if selected { Role::Active } else { Role::Body }).bold(true);
            let mut row = prefix.into_bytes();
            row.extend(title_style.render_bytes(&clip(&item.title, list_width)));
            row.extend_from_slice(b"\n    ");
            row.extend(palette_style(Role::Faint).render_bytes(&clip(
                if crate::sql::rewrite::trim_sql_space(&item.sql).is_empty() {
                    "(empty query)"
                } else {
                    crate::sql::rewrite::trim_sql_space(&item.sql)
                },
                list_width,
            )));
            if !item.from.is_empty() || !item.to.is_empty() {
                row.extend_from_slice(b"\n    ");
                row.extend(palette_style(Role::Faint).render_bytes(&clip(
                    &format!("from {} · to {}", item.from, item.to),
                    list_width,
                )));
            }
            rows.push(row);
        }
        let populated = if self.visible.is_empty() && self.mode != FilterMode::Editing {
            Style::new()
                .fg(Color::adaptive("#909090", "#626262"))
                .render("No items.")
                .into_bytes()
        } else {
            rows.join(b"\n\n".as_slice())
        };
        let content = Style::new().height(content_height).render_bytes(&populated);
        let pages = self.visible.len().div_ceil(per_page);
        let pagination = if pages > list_width {
            format!(
                "  {}",
                Style::new()
                    .fg(Color::adaptive("#9B9B9B", "#5C5C5C"))
                    .render(&format!("{}/{pages}", page + 1))
            )
        } else if pages > 1 {
            let mut dots = String::from("  ");
            for index in 0..pages {
                let color = if index == page {
                    Color::adaptive("#847A85", "#979797")
                } else {
                    Color::adaptive("#DDDADA", "#3C3C3C")
                };
                dots.push_str(&Style::new().fg(color).render("•"));
            }
            dots
        } else {
            String::new()
        };
        let heading = palette_style(Role::Accent)
            .bold(true)
            .render("SAVED QUERIES");
        let inner = [
            heading.as_bytes(),
            b"\n\n",
            title.as_bytes(),
            b"\n",
            &content,
            b"\n",
            pagination.as_bytes(),
        ]
        .concat();
        let main = Style::new()
            .border(Border::NORMAL)
            .border_fg(Color::hex(active().hex(Role::Border)))
            .padding(&[1, 2])
            .width(self.width.saturating_sub(2))
            .render_bytes(&inner);
        let key = palette_style(Role::Accent).bold(true);
        let hint = palette_style(Role::Faint);
        let hints = format!(
            "{}{}{}{}{}{}{}{}",
            key.render("<a/enter>"),
            hint.render(" apply    "),
            key.render("<↑/↓>"),
            hint.render(" navigate    "),
            key.render("<→/←>"),
            hint.render(" pages    "),
            key.render("<b/ctrl-c>"),
            hint.render(" quit")
        );
        let footer_inner = Style::new()
            .width(self.width.saturating_sub(2))
            .padding(&[0, 1])
            .render(&hints);
        let footer = Style::new()
            .border(Border::NORMAL)
            .border_fg(Color::hex(active().hex(Role::Border)))
            .render(&footer_inner);
        [main.as_slice(), b"\n", footer.as_bytes()].concat()
    }
}

// The oracle's item delegate tests UTF-8 byte length before truncating.
fn clip(value: &str, list_width: usize) -> Vec<u8> {
    let width = list_width.saturating_sub(4).max(10);
    if value.len() <= width {
        value.as_bytes().to_vec()
    } else {
        // The boundary is bytes, including an incomplete UTF-8 prefix. ANSI
        // measurement and wrapping see those bytes before terminal decoding.
        [&value.as_bytes()[..width - 1], "…".as_bytes()].concat()
    }
}

#[path = "saved_fuzzy_unicode.rs"]
mod fuzzy_unicode;

// A local matcher over owned titles. Progress separates the score for the
// current pattern rune from the retained byte candidate. The latter deliberately
// survives an apply: actual pinned fuzzy controls reuse it after NUL boundaries
// and after rejecting a wrapped candidate score. No service state is captured.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct MatchFault {
    index: usize,
    len: usize,
}

impl std::fmt::Display for MatchFault {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "runtime error: index out of range [{}] with length {}",
            self.index, self.len
        )
    }
}

struct MatchProgress {
    consumed: usize,
    retained_byte: Option<usize>,
    best_score: i64,
    last_applied: Option<usize>,
    adjacent_bonus: i64,
    total: i64,
}

impl MatchProgress {
    fn new() -> Self {
        Self {
            consumed: 0,
            retained_byte: None,
            best_score: -1,
            last_applied: None,
            adjacent_bonus: 0,
            total: 0,
        }
    }

    fn consider(&mut self, byte: usize, rune: char, previous: Option<(usize, char)>) {
        let mut score: i64 = if byte == 0 { 10 } else { 0 };
        if let Some((prior_byte, prior)) = previous {
            if fuzzy_unicode::is_lower(prior) && fuzzy_unicode::is_upper(rune) {
                score += 20;
            }
            if "/-_ .\\".contains(prior) {
                score += 20;
            }
            if self.last_applied == Some(prior_byte) {
                let bonus = self.adjacent_bonus.wrapping_mul(2).wrapping_add(5);
                self.adjacent_bonus = self.adjacent_bonus.wrapping_add(bonus);
                score = score.wrapping_add(bonus);
            }
        }
        // This floor applies to the candidate, not to the final match total.
        if score > self.best_score {
            self.best_score = score;
            self.retained_byte = Some(byte);
        }
    }

    fn apply(&mut self) {
        if let Some(byte) = self.retained_byte {
            if self.consumed == 0 {
                self.total = self
                    .total
                    .wrapping_add((byte as i64).wrapping_mul(-5).max(-15));
            }
            self.total = self.total.wrapping_add(self.best_score);
            self.last_applied = Some(byte);
            self.consumed += 1;
            self.best_score = -1;
        }
    }
}

fn match_score(pattern: &str, title: &str) -> Result<Option<i64>, MatchFault> {
    let needle: Vec<_> = pattern.chars().collect();
    if needle.is_empty() {
        return Ok(None);
    }
    let mut progress = MatchProgress::new();
    let mut text = title.char_indices().peekable();
    let mut previous = None;
    while let Some((byte, rune)) = text.next() {
        let Some(&expected) = needle.get(progress.consumed) else {
            // NUL can consume the last pattern rune while title bytes remain.
            // Surface the command fault explicitly rather than panicking in
            // Update or returning a completed match before scanning the title.
            return Err(MatchFault {
                index: progress.consumed,
                len: needle.len(),
            });
        };
        if fold_equal(rune, expected) {
            progress.consider(byte, rune, previous);
        }
        let next_rune = text.peek().map_or('\0', |&(_, rune)| rune);
        let next_pattern = needle.get(progress.consumed + 1).copied().unwrap_or('\0');
        if next_rune == '\0' || fold_equal(next_pattern, next_rune) {
            progress.apply();
        }
        previous = Some((byte, rune));
    }
    Ok((progress.consumed == needle.len()).then(|| {
        progress
            .total
            .wrapping_add(progress.consumed as i64)
            .wrapping_sub(title.len() as i64)
    }))
}
fn fold_equal(a: char, b: char) -> bool {
    a == b || fuzzy_unicode::fold(a) == fuzzy_unicode::fold(b)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn press(state: &mut State, name: &str) -> Option<Effect> {
        let bytes = match name {
            "enter" => b"\r".as_slice(),
            "ctrl+c" => b"\x03",
            "ctrl+v" => b"\x16",
            "esc" => b"\x1b",
            "backspace" => b"\x7f",
            "delete" => b"\x1b[3~",
            "right" => b"\x1b[C",
            "left" => b"\x1b[D",
            "alt+d" => b"\x1bd",
            "down" => b"\x1b[B",
            _ => name.as_bytes(),
        };
        let event = super::super::input::parse(bytes)
            .into_iter()
            .next()
            .unwrap();
        let Event::Key(key) = event else {
            panic!("expected key");
        };
        state.update(&key)
    }
    fn item(id: &str, title: &str, sql: &str) -> SavedSelection {
        SavedSelection {
            id: id.into(),
            title: title.into(),
            sql: sql.into(),
            ..Default::default()
        }
    }
    #[test]
    fn alt_d_at_last_filter_rune_reports_go_fault_before_refilter_or_apply() {
        let mut state = State::new(vec![item("i", "xy", "select 1")]);
        press(&mut state, "/");
        press(&mut state, "xy");
        press(&mut state, "left");
        let fault = EditorFault::IndexOutOfRange { index: 2, len: 2 };
        assert_eq!(press(&mut state, "alt+d"), Some(Effect::Fault(fault)));
        assert_eq!(
            recovered_panic_text(fault),
            "Caught panic:\n\nruntime error: index out of range [2] with length 2\n\nRestoring terminal...\n\n"
        );
        assert_eq!(state.take_fault(), None, "fault consumed into the effect");
    }
    #[test]
    fn clipboard_completion_updates_current_editing_filter_without_generation_guard() {
        let mut state = State::new(vec![item("logs", "Logs", "select 1")]);
        press(&mut state, "/");
        assert_eq!(press(&mut state, "ctrl+v"), Some(Effect::ReadClipboard));
        press(&mut state, "esc");
        assert!(state.paste("ignored while browsing").is_none());
        assert_eq!(state.filter.value(), "");
        press(&mut state, "/");
        // A read started during an earlier editing session still inserts now.
        assert!(state.paste("Lo").is_none());
        assert_eq!(state.filter.value(), "Lo");
        assert_eq!(state.selected().unwrap().id, "logs");
        press(&mut state, "down");
        assert!(state.paste("ignored after filter acceptance").is_none());
        assert_eq!(state.filter.value(), "Lo");
    }
    #[test]
    fn equal_fuzzy_scores_retain_source_order_and_short_titles_rank_first() {
        let mut state = State::new(vec![
            item("first", "Logs", "select 0"),
            item("second", "Logs", "select 1"),
        ]);
        press(&mut state, "/");
        press(&mut state, "L");
        assert_eq!(state.selected().unwrap().id, "first");
        assert!(
            match_score("lg", "log").unwrap().unwrap()
                > match_score("lg", "Log Server").unwrap().unwrap()
        );
        assert!(fold_equal('Σ', 'ς'));
        assert!(fold_equal('ſ', 'S'));
        assert!(!fold_equal('İ', 'i'));
    }
    #[test]
    fn fuzzy_matches_pinned_go_simple_fold_orbits_without_full_case_folding() {
        for (a, b) in [('ẛ', 'ṡ'), ('ᲀ', 'в'), ('K', 'k'), ('Σ', 'ς'), ('ſ', 'S')] {
            assert!(fold_equal(a, b), "{a} / {b}");
            assert!(fold_equal(b, a));
            assert_eq!(fuzzy_unicode::fold(a), fuzzy_unicode::fold(b));
        }
        assert!(!fold_equal('İ', 'i'));
        assert!(!fold_equal('ı', 'I'));
        assert_eq!(match_score("ss", "ß"), Ok(None));

        for (pattern, title) in [("ṡ", "ẛ"), ("в", "ᲀ")] {
            let selected = item("orbit", title, "SELECT event.code FROM logs");
            let mut state = State::new(vec![item("decoy", "other", "select 0"), selected.clone()]);
            press(&mut state, "/");
            press(&mut state, pattern);
            assert_eq!(state.visible, vec![1]);
            assert_eq!(
                press(&mut state, "enter"),
                Some(Effect::Finish(Some(selected)))
            );
        }
    }
    #[test]
    fn fuzzy_camel_bonus_uses_pinned_ll_lu_categories_and_go_byte_scores() {
        // Scores from the actual sahilm/fuzzy v0.1.1 oracle, Go 1.25.4.
        for (pattern, title, score) in [
            ("ṡ", "ẛ", 8),
            ("в", "ᲀ", 8),
            ("A", "ªA", -12),
            ("A", "QA", -6),
            ("A", "xA", 14),
            ("Ⓐ", "xⒶ", -8),
            ("Ⓐ", "Ⓐ", 8),
            ("Ⅰ", "xⅠ", -8),
            ("Ⅰ", "Ⅰ", 8),
            ("lg", "Log Server", 2),
            ("lg", "log", 9),
            ("L", "Logs", 7),
        ] {
            assert_eq!(
                match_score(pattern, title),
                Ok(Some(score)),
                "{pattern} / {title}"
            );
        }
        assert!(!fuzzy_unicode::is_lower('ª'));
        assert!(!fuzzy_unicode::is_upper('Ⓐ'));
        assert!(!fuzzy_unicode::is_upper('Ⅰ'));
        assert!(fuzzy_unicode::is_lower('x'));
        assert!(fuzzy_unicode::is_upper('A'));
        for (pattern, first, second) in [("A", "ªA", "QA"), ("Ⓐ", "xⒶ", "Ⓐ"), ("Ⅰ", "xⅠ", "Ⅰ")]
        {
            let mut state = State::new(vec![
                item("first", first, "select 0"),
                item("second", second, "select 1"),
            ]);
            press(&mut state, "/");
            press(&mut state, pattern);
            assert_eq!(state.visible, vec![1, 0]);
            assert_eq!(state.selected().unwrap().sql, "select 1");
        }
    }
    #[test]
    fn fuzzy_nul_fault_is_a_command_fault_and_typed_a_still_applies() {
        let fault = MatchFault { index: 1, len: 1 };
        assert_eq!(match_score("a", "a\0b"), Err(fault));
        assert_eq!(match_score("a", "a\0"), Err(fault));
        assert_eq!(
            match_score("ab", "ab\0x"),
            Err(MatchFault { index: 2, len: 2 })
        );
        assert_eq!(match_score("", "a\0b"), Ok(None));
        assert_eq!(
            super::super::profile::caught_panic_stdout(&fault.to_string()),
            "Caught panic:\n\nruntime error: index out of range [1] with length 1\n\nRestoring terminal...\n\n"
        );
        let selected = item("nul", "a\0b", "select 1");
        let mut state = State::new(vec![selected.clone()]);
        press(&mut state, "/");
        assert_eq!(
            press(&mut state, "a"),
            Some(Effect::Finish(Some(selected.clone())))
        );
        assert_eq!(state.filter.value(), "");
        let mut state = State::new(vec![selected]);
        press(&mut state, "/");
        assert_eq!(press(&mut state, "ctrl+v"), Some(Effect::ReadClipboard));
        assert_eq!(state.paste("a"), Some(Effect::MatchFault(fault)));
        // A matching fault is distinct from EditorFault's successful no-op.
        let mut state = State::new(vec![item("nul", "a\0b", "select 1")]);
        press(&mut state, "/");
        assert_eq!(press(&mut state, "A"), Some(Effect::MatchFault(fault)));
    }

    #[test]
    fn fuzzy_nul_reuses_the_retained_byte_and_preserves_application() {
        assert_eq!(match_score("ab", "a\0x"), Ok(Some(8)));
        assert_eq!(match_score("abc", "a\0x\0y"), Ok(Some(6)));
        assert_eq!(match_score("a", "x\0a"), Ok(Some(-12)));
        assert_eq!(match_score("ab", "ax"), Ok(None));
        let selected = item("stale", "a\0x", "SELECT event.code FROM logs");
        let mut state = State::new(vec![item("decoy", "other", "select 0"), selected.clone()]);
        press(&mut state, "/");
        assert!(state.paste("ab").is_none());
        assert_eq!(state.visible, vec![1]);
        assert_eq!(
            press(&mut state, "enter"),
            Some(Effect::Finish(Some(selected)))
        );
    }

    #[test]
    fn fuzzy_overflow_uses_the_verified_candidate_floor_and_go_rankings() {
        // Actual Go int64 scores. At 40 runes the total wraps negative while
        // the candidate is positive; only the 43rd candidate is rejected.
        for (count, score) in [
            (40, -8_315_356_191_162_110_941),
            (41, -6_499_324_499_776_781_222),
            (42, -1_051_229_425_620_792_065),
            (43, -1_051_229_425_620_792_066),
            (44, -1_051_229_425_620_792_066),
            (45, -1_051_229_425_620_792_067),
            (64, 8_492_147_925_252_739_361),
        ] {
            let pattern = "a".repeat(count);
            assert_eq!(
                match_score(&pattern, &pattern),
                Ok(Some(score)),
                "count {count}"
            );
        }
        for count in [43, 44, 45, 64] {
            let pattern = "a".repeat(count);
            let titles = [
                pattern.clone(),
                format!("{}_a", "a".repeat(count - 1)),
                format!("_{pattern}"),
                format!("{pattern}a"),
            ];
            let mut state = State::new(
                titles
                    .iter()
                    .enumerate()
                    .map(|(i, title)| item(&i.to_string(), title, &format!("select {i}")))
                    .collect(),
            );
            press(&mut state, "/");
            assert!(state.paste(&pattern).is_none());
            assert_eq!(state.visible, vec![1, 2, 0, 3], "count {count}");
            press(&mut state, "down"); // accept filter, preserving selection
            press(&mut state, "down"); // navigate to the second Go rank
            assert_eq!(state.selected().unwrap().sql, "select 2");
        }
    }

    #[test]
    fn apply_and_quit_intercept_before_filtering() {
        for key in ["a", "enter", "b", "ctrl+c"] {
            let selected = item("i", "Fixture", "select 1");
            let mut state = State::new(vec![selected.clone()]);
            press(&mut state, "/");
            press(&mut state, "F");
            let expected = if key == "a" || key == "enter" {
                Some(selected)
            } else {
                None
            };
            assert_eq!(press(&mut state, key), Some(Effect::Finish(expected)));
            assert_eq!(state.filter.value(), "F");
        }
    }
    #[test]
    fn description_sql_is_selected_and_delete_key_is_unreachable() {
        let mut state = State::new(vec![item("i", "Fixture", "select real_sql")]);
        assert_eq!(press(&mut state, "delete"), None);
        assert_eq!(
            press(&mut state, "a"),
            Some(Effect::Finish(Some(item(
                "i",
                "Fixture",
                "select real_sql"
            ))))
        );
    }
    #[test]
    fn filtering_matches_title_and_preserves_empty_selection() {
        let mut state = State::new(vec![
            item("a", "Frontend", "different"),
            item("b", "Metrics", "Frontend"),
        ]);
        press(&mut state, "/");
        press(&mut state, "F");
        press(&mut state, "r");
        assert_eq!(state.selected().unwrap().id, "a");
        press(&mut state, "z");
        assert_eq!(press(&mut state, "enter"), Some(Effect::Finish(None)));
        press(&mut state, "esc");
        assert_eq!(state.visible.len(), 2);
        assert_eq!(press(&mut state, "esc"), Some(Effect::Finish(None)));
    }
    #[test]
    fn navigation_filter_acceptance_and_unicode_backspace() {
        let mut state = State::new(
            (0..10)
                .map(|i| item(&i.to_string(), "日志", "select 1"))
                .collect(),
        );
        state.resize(80, 24);
        press(&mut state, "right");
        assert_eq!(state.selected, 3);
        press(&mut state, "/");
        press(&mut state, "日");
        press(&mut state, "志");
        press(&mut state, "backspace");
        assert_eq!(state.filter.value(), "日");
        press(&mut state, "down");
        assert_eq!(state.mode, FilterMode::Applied);
        press(&mut state, "esc");
        assert_eq!(state.selected, 0);
    }
}
