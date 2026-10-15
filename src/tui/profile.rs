//! Inline profile pickers, the self-hosted credential form and the cooked
//! logout confirmation used by `pb profile` and `pb logout`.
//!
//! Each screen owns its state and exposes a pure key reducer plus a view
//! that returns the frame string Bubble Tea would have rendered. Terminal
//! lifetime belongs to `TerminalSession`; `run_inline` is the only driver.
use crate::{
    config::ProfileRecord,
    tui::{
        editor::{EchoMode, InputStyles, TextInput},
        input::{Event, Key, KeyCode},
        style::{self, Border, Color, Role, Style},
        terminal::{Screen, TerminalSession},
    },
};
use std::{collections::BTreeMap, time::Instant};

// ── Shared inline driver ─────────────────────────────────────────────────────

/// One inline Bubble Tea program: keys and timer ticks in, frames out.
pub(crate) trait InlineModel {
    /// Returns true when the model issued `tea.Quit`.
    fn key(&mut self, key: &Key) -> bool;
    /// Earliest pending timer (cursor blink, spinner), if any.
    fn next_tick(&self) -> Option<Instant> {
        None
    }
    fn tick(&mut self, _now: Instant) {}
    /// True once after a focused editor saw its Ctrl+V binding (Go's
    /// textinput.Paste command).
    fn take_paste_request(&mut self) -> bool {
        false
    }
    /// Go pasteMsg delivery: Update routes it like any non-key message.
    fn apply_paste(&mut self, _text: &str) {}
    /// The Go panic value reached by the last Update or View, if any.
    fn take_panic(&mut self) -> Option<String> {
        None
    }
    fn view(&self) -> String;
}

/// How `tea.Program.Run` ended.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum RunEnd {
    Quit,
    /// Update/View panicked: bubbletea v1.2.2 `recoverFromPanic` restored the
    /// terminal on the kill path (no final render) and Run returned a nil
    /// model and nil error. The value is the panic text (`%s` of the value).
    Panicked(String),
}

/// What recoverFromPanic prints to stdout after restoring the terminal.
/// Its `debug.PrintStack()` stderr trace is not reproduced (L-AUTH-10).
pub(crate) fn caught_panic_stdout(value: &str) -> String {
    format!("Caught panic:\n\n{value}\n\nRestoring terminal...\n\n")
}

/// Go's nil interface method call in the empty picker.
pub(crate) const NIL_DEREFERENCE: &str =
    "runtime error: invalid memory address or nil pointer dereference";

/// `tea.NewProgram(model).Run()` for inline programs without network work.
/// `Err` carries the Run error text: an input read error (after a graceful
/// final render and close) or `PROGRAM_KILLED` after a background panic.
pub(crate) fn run_inline(model: &mut impl InlineModel) -> Result<RunEnd, String> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| e.to_string())?;
    runtime.block_on(drive(model))
}

enum Wake {
    Event(Option<Event>),
    Tick,
    Paste(Option<String>),
}

async fn drive(model: &mut impl InlineModel) -> Result<RunEnd, String> {
    let mut session = TerminalSession::open(Screen::Inline).map_err(|e| e.to_string())?;
    // Owned clipboard reads; completions arrive in finish order like Go's
    // command goroutines, with no staleness guard.
    let mut reads = tokio::task::JoinSet::new();
    let view = model.view();
    if let Some(value) = model.take_panic() {
        drop(session);
        return Ok(RunEnd::Panicked(value));
    }
    session.draw(&view);
    let result = loop {
        let wake = {
            let deadline = model.next_tick();
            let event = session.next_event();
            let timer = async move {
                match deadline {
                    Some(at) => tokio::time::sleep_until(at.into()).await,
                    None => std::future::pending().await,
                }
            };
            let paste = async {
                if reads.is_empty() {
                    std::future::pending::<()>().await;
                }
                reads.join_next().await
            };
            match first(event, first(timer, paste)).await {
                Either::Left(event) => Wake::Event(event),
                Either::Right(Either::Left(())) => Wake::Tick,
                // Failed reads (pasteErrMsg) change nothing visible.
                Either::Right(Either::Right(done)) => {
                    Wake::Paste(done.and_then(Result::ok).and_then(Result::ok))
                }
            }
        };
        let mut quit = false;
        match wake {
            Wake::Event(Some(Event::Key(key))) => quit = model.key(&key),
            Wake::Event(Some(Event::Resize(_))) | Wake::Event(Some(Event::Unknown)) => {}
            Wake::Event(Some(Event::InputError(message))) => break End::InputError(message),
            Wake::Event(Some(Event::Panicked(value))) => break End::Killed(value),
            Wake::Event(Some(Event::Quit) | None) => break End::Run(RunEnd::Quit),
            Wake::Tick => model.tick(Instant::now()),
            Wake::Paste(Some(text)) => model.apply_paste(&text),
            Wake::Paste(None) => {}
        }
        if let Some(value) = model.take_panic() {
            break End::Run(RunEnd::Panicked(value));
        }
        if model.take_paste_request() {
            reads.spawn(crate::tui::clipboard::read());
        }
        // The event loop renders after every message, the quitting one too.
        let view = model.view();
        if let Some(value) = model.take_panic() {
            break End::Run(RunEnd::Panicked(value));
        }
        session.draw(&view);
        if quit {
            break End::Run(RunEnd::Quit);
        }
    };
    reads.shutdown().await;
    match result {
        // Update/View panic: Run's own deferred recover takes the kill path.
        End::Run(RunEnd::Panicked(value)) => {
            drop(session);
            Ok(RunEnd::Panicked(value))
        }
        // eventLoop returned normally (quit) or with a read error: `killed`
        // depends only on the context, so Go renders the final view and
        // shuts down gracefully before Run returns the error.
        End::Run(RunEnd::Quit) => Ok(close_with_final_view(session, model)),
        End::InputError(message) => match close_with_final_view(session, model) {
            RunEnd::Quit => Err(message),
            panicked => Ok(panicked),
        },
        // A background (command goroutine) panic: recoverFromPanic kills the
        // session and prints to stdout; the canceled context then makes Run
        // return the model with ErrProgramKilled. Callers wrap that error as
        // they wrap any program error, and nothing is saved.
        End::Killed(value) => {
            drop(session);
            use std::io::Write;
            let mut stdout = std::io::stdout();
            let _ = stdout.write_all(caught_panic_stdout(&value).as_bytes());
            let _ = stdout.flush();
            Err(PROGRAM_KILLED.into())
        }
    }
}

/// Run's final `renderer.write(model.View())` and `shutdown(false)`. A panic
/// in that View is recovered by Run's deferred handler (kill path).
fn close_with_final_view(session: TerminalSession, model: &mut impl InlineModel) -> RunEnd {
    let view = model.view();
    if let Some(value) = model.take_panic() {
        drop(session);
        return RunEnd::Panicked(value);
    }
    // Go ignores restoration failures here.
    let _ = session.close(&view);
    RunEnd::Quit
}

/// tea.ErrProgramKilled wrapped with the canceled context (tea.go Run).
pub(crate) const PROGRAM_KILLED: &str = "program was killed: context canceled";

enum End {
    Run(RunEnd),
    InputError(String),
    /// Event::Panicked: a background thread/task panicked; the terminal has
    /// already been restored by the session.
    Killed(String),
}

pub(crate) enum Either<A, B> {
    Left(A),
    Right(B),
}

/// Poll two futures, preferring the first; the loser is dropped. Used only
/// with cancel-safe futures (session event receive, timers, owned requests).
pub(crate) async fn first<A: std::future::Future, B: std::future::Future>(
    a: A,
    b: B,
) -> Either<A::Output, B::Output> {
    let mut a = std::pin::pin!(a);
    let mut b = std::pin::pin!(b);
    std::future::poll_fn(|cx| {
        if let std::task::Poll::Ready(value) = a.as_mut().poll(cx) {
            return std::task::Poll::Ready(Either::Left(value));
        }
        if let std::task::Poll::Ready(value) = b.as_mut().poll(cx) {
            return std::task::Poll::Ready(Either::Right(value));
        }
        std::task::Poll::Pending
    })
    .await
}

/// `lipgloss.NewStyle().Foreground(ui.Adaptive(role))`: the palette side is
/// chosen by the terminal background, not by PB_THEME.
pub(crate) fn adaptive(role: Role) -> Style {
    Style::new().fg(Color::role(role))
}

/// `lipgloss.Height`.
fn height(s: &str) -> usize {
    s.matches('\n').count() + 1
}

// ── Default-profile picker (pkg/model/defaultprofile + bubbles/list v0.20) ──

const LIST_WIDTH: usize = 80;
const LIST_HEIGHT: usize = 19;
/// itemDelegate Height()+Spacing().
const ITEM_ROWS: usize = 4;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum FilterState {
    Unfiltered,
    Filtering,
    FilterApplied,
}

/// `key.Binding` enabled flags, updated exactly where bubbles calls
/// `updateKeybindings` or toggles individual bindings.
#[derive(Clone, Copy, Debug)]
struct Bindings {
    up: bool,
    down: bool,
    next_page: bool,
    prev_page: bool,
    start: bool,
    end: bool,
    filter: bool,
    clear_filter: bool,
    cancel: bool,
    accept: bool,
    quit: bool,
    show_full: bool,
    close_full: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum PickerOutcome {
    Continue,
    Chosen(String),
    Canceled,
    /// Update or View panicked in Go: Enter with no selectable item (nil
    /// list.Item, L-AUTH-3) or a filter-input fault (L-AUTH-11). Bubble Tea
    /// recovers it; every picker caller's type assertion then panics fatally.
    Panicked(String),
}

struct PickerItem {
    title: String,
    url: String,
    user: String,
}

/// Profile order is Go map iteration order (random per run). Rust uses the
/// sorted `BTreeMap` order, which is one of the permutations Go can show.
pub(crate) struct ProfilePicker {
    items: Vec<PickerItem>,
    page: usize,
    per_page: usize,
    total_pages: usize,
    cursor: usize,
    filter_state: FilterState,
    filter: TextInput,
    show_all_help: bool,
    bindings: Bindings,
    outcome: PickerOutcome,
}

impl ProfilePicker {
    pub(crate) fn new(profiles: &BTreeMap<String, ProfileRecord>) -> Self {
        let items = profiles
            .iter()
            .map(|(name, profile)| PickerItem {
                title: name.clone(),
                url: profile.url.clone(),
                user: profile.username.clone(),
            })
            .collect();
        let mut filter = TextInput::new();
        filter.set_prompt("Filter: ");
        filter.set_char_limit(64);
        filter.focus();
        // The picker model drops every non-key message, so the filter cursor
        // never receives a blink and stays visible.
        filter.disarm_blink();
        let mut picker = Self {
            items,
            page: 0,
            per_page: 1,
            total_pages: 1,
            cursor: 0,
            filter_state: FilterState::Unfiltered,
            filter,
            show_all_help: false,
            bindings: Bindings {
                up: true,
                down: true,
                next_page: true,
                prev_page: true,
                start: true,
                end: true,
                filter: true,
                clear_filter: true,
                cancel: true,
                accept: true,
                quit: true,
                show_full: true,
                close_full: true,
            },
            outcome: PickerOutcome::Continue,
        };
        // list.New then SetShowStatusBar/SetShowTitle each recompute
        // pagination; only the final layout is observable. The model then
        // disables full help, and SetFilteringEnabled(true) re-enables it
        // only when more than one other binding is enabled.
        picker.update_pagination();
        picker.update_keybindings();
        picker.bindings.show_full = false;
        picker.bindings.close_full = false;
        picker.update_keybindings();
        picker
    }

    pub(crate) fn outcome(&self) -> &PickerOutcome {
        &self.outcome
    }

    fn len(&self) -> usize {
        // The picker never forwards FilterMatchesMsg to the list (its Update
        // only handles KeyMsg), so the filtered set stays the full snapshot
        // taken when filtering starts: every state shows all items.
        self.items.len()
    }

    fn index(&self) -> usize {
        self.page * self.per_page + self.cursor
    }

    fn items_on_page(&self) -> usize {
        let n = self.len();
        if n < 1 {
            return 0;
        }
        let start = self.page * self.per_page;
        let end = (start + self.per_page).min(n);
        end.saturating_sub(start)
    }

    fn update_pagination(&mut self) {
        let index = self.index();
        let mut avail = LIST_HEIGHT as isize;
        avail -= height(&self.title_view()) as isize;
        avail -= height(&self.pagination_view()) as isize;
        avail -= height(&self.help_view()) as isize;
        self.per_page = (avail / ITEM_ROWS as isize).max(1) as usize;
        let items = self.len().max(1);
        self.total_pages = items.div_ceil(self.per_page);
        self.page = index / self.per_page;
        self.cursor = index % self.per_page;
        if self.page + 1 >= self.total_pages {
            self.page = self.total_pages.saturating_sub(1);
        }
    }

    fn update_keybindings(&mut self) {
        let b = &mut self.bindings;
        if self.filter_state == FilterState::Filtering {
            b.up = false;
            b.down = false;
            b.next_page = false;
            b.prev_page = false;
            b.start = false;
            b.end = false;
            b.filter = false;
            b.clear_filter = false;
            b.cancel = true;
            b.accept = !self.filter.value().is_empty();
            b.quit = false;
            b.show_full = false;
            b.close_full = false;
            return;
        }
        let has_items = !self.items.is_empty();
        b.up = has_items;
        b.down = has_items;
        let has_pages = self.total_pages > 1;
        b.next_page = has_pages;
        b.prev_page = has_pages;
        b.start = has_items;
        b.end = has_items;
        b.filter = has_items;
        b.clear_filter = self.filter_state == FilterState::FilterApplied;
        b.cancel = false;
        b.accept = false;
        b.quit = true;
        if self.show_all_help {
            b.show_full = true;
            b.close_full = true;
        } else {
            let enabled = [
                b.up,
                b.down,
                b.next_page,
                b.prev_page,
                b.start,
                b.end,
                b.filter,
                b.clear_filter,
                b.accept,
                b.cancel,
                b.quit,
                b.close_full,
            ];
            let min_help = enabled.iter().filter(|e| **e).count() > 1;
            b.show_full = min_help;
            b.close_full = min_help;
        }
    }

    fn reset_filtering(&mut self) {
        if self.filter_state == FilterState::Unfiltered {
            return;
        }
        self.filter_state = FilterState::Unfiltered;
        self.filter.reset();
        self.update_pagination();
        self.update_keybindings();
    }

    fn cursor_up(&mut self) {
        if self.cursor == 0 {
            if self.page == 0 {
                return;
            }
            self.page -= 1;
            self.cursor = self.items_on_page().saturating_sub(1);
            return;
        }
        self.cursor -= 1;
    }

    fn cursor_down(&mut self) {
        let items_on_page = self.items_on_page();
        self.cursor += 1;
        if self.cursor < items_on_page {
            return;
        }
        if self.page + 1 < self.total_pages {
            self.page += 1;
            self.cursor = 0;
            return;
        }
        if self.cursor > items_on_page {
            self.cursor = 0;
            return;
        }
        self.cursor = items_on_page.saturating_sub(1);
    }

    /// defaultprofile.Model.Update.
    pub(crate) fn update(&mut self, key: &Key) -> &PickerOutcome {
        if key.code == KeyCode::Ctrl(3) {
            self.outcome = PickerOutcome::Canceled;
        } else if key.code == KeyCode::Enter && self.filter_state != FilterState::Filtering {
            self.outcome = match self.items.get(self.index()) {
                Some(item) => PickerOutcome::Chosen(item.title.clone()),
                None => PickerOutcome::Panicked(NIL_DEREFERENCE.into()),
            };
        } else if self.filter_state == FilterState::Filtering {
            self.handle_filtering(key);
        } else if self.handle_browsing(key) {
            self.outcome = PickerOutcome::Canceled;
        }
        if let Some(fault) = self.filter.take_fault() {
            self.outcome = PickerOutcome::Panicked(fault.to_string());
        }
        &self.outcome
    }

    /// list.handleBrowsing; true means the list returned tea.Quit.
    fn handle_browsing(&mut self, key: &Key) -> bool {
        let b = self.bindings;
        if b.clear_filter && key.is(&["esc"]) {
            self.reset_filtering();
        } else if b.quit && key.is(&["q", "esc"]) {
            return true;
        } else if b.up && key.is(&["up", "k"]) {
            self.cursor_up();
        } else if b.down && key.is(&["down", "j"]) {
            self.cursor_down();
        } else if b.prev_page && key.is(&["left", "h", "pgup", "b", "u"]) {
            self.page = self.page.saturating_sub(1);
        } else if b.next_page && key.is(&["right", "l", "pgdown", "f", "d"]) {
            if self.page + 1 < self.total_pages {
                self.page += 1;
            }
        } else if b.start && key.is(&["home", "g"]) {
            self.page = 0;
            self.cursor = 0;
        } else if b.end && key.is(&["end", "G"]) {
            self.page = self.total_pages - 1;
            self.cursor = self.items_on_page().saturating_sub(1);
        } else if b.filter && key.is(&["/"]) {
            self.page = 0;
            self.cursor = 0;
            self.filter_state = FilterState::Filtering;
            self.filter.cursor_end();
            self.filter.focus();
            self.filter.disarm_blink();
            self.update_keybindings();
            return false;
        } else if (b.show_full || b.close_full) && key.is(&["?"]) {
            self.show_all_help = !self.show_all_help;
            self.update_pagination();
        }
        let items_on_page = self.items_on_page();
        if self.cursor + 1 > items_on_page {
            self.cursor = items_on_page.saturating_sub(1);
        }
        false
    }

    /// list.handleFiltering.
    fn handle_filtering(&mut self, key: &Key) {
        let b = self.bindings;
        if b.cancel && key.is(&["esc"]) {
            self.reset_filtering();
            self.bindings.filter = true;
            self.bindings.clear_filter = false;
        } else if b.accept
            && key.is(&[
                "enter",
                "tab",
                "shift+tab",
                "ctrl+k",
                "up",
                "ctrl+j",
                "down",
            ])
            && !self.items.is_empty()
        {
            self.filter.blur();
            self.filter_state = FilterState::FilterApplied;
            self.update_keybindings();
            if self.filter.value().is_empty() {
                self.reset_filtering();
            }
        }
        let before = self.filter.value();
        self.filter.update(key);
        if self.filter.value() != before {
            // filterItems' FilterMatchesMsg is produced but never delivered.
            self.bindings.accept = !self.filter.value().is_empty();
        }
        self.update_pagination();
    }

    fn filter_styles() -> InputStyles {
        InputStyles {
            prompt: Style::new().fg(Color::adaptive("#04B575", "#ECFD65")),
            cursor: Style::new().fg(Color::hex("#EE6FF8")),
            ..InputStyles::default()
        }
    }

    fn title_view(&self) -> String {
        if self.filter_state != FilterState::Filtering {
            return String::new();
        }
        let view = self.filter.view(&Self::filter_styles());
        if view.is_empty() {
            return view;
        }
        Style::new().padding(&[0, 0, 1, 2]).render(&view)
    }

    fn pagination_view(&self) -> String {
        if self.total_pages < 2 {
            return String::new();
        }
        let mut dots = String::new();
        for page in 0..self.total_pages {
            dots.push_str(if page == self.page { "● " } else { "○ " });
        }
        if style::width(&dots) > LIST_WIDTH {
            dots = Style::new()
                .fg(Color::adaptive("#9B9B9B", "#5C5C5C"))
                .render(&format!("{}/{}", self.page + 1, self.total_pages));
        }
        Style::new().margin_left(1).render(&dots)
    }

    fn help_bindings(&self) -> [(bool, &'static str, &'static str); 13] {
        let b = &self.bindings;
        [
            (b.up, "↑/k", "up"),
            (b.down, "↓/j", "down"),
            (b.next_page, "→/l/pgdn", "next page"),
            (b.prev_page, "←/h/pgup", "prev page"),
            (b.start, "g/home", "go to start"),
            (b.end, "G/end", "go to end"),
            (b.filter, "/", "filter"),
            (b.clear_filter, "esc", "clear filter"),
            (b.accept, "enter", "apply filter"),
            (b.cancel, "esc", "cancel"),
            (b.quit, "q", "quit"),
            (b.show_full, "?", "more"),
            (b.close_full, "?", "close help"),
        ]
    }

    fn help_view(&self) -> String {
        let key_style = Style::new().fg(Color::adaptive("#909090", "#626262"));
        let desc_style = Style::new().fg(Color::adaptive("#B2B2B2", "#4A4A4A"));
        let sep_style = Style::new().fg(Color::adaptive("#DDDADA", "#3C3C3C"));
        let all = self.help_bindings();
        let help = if self.show_all_help {
            // FullHelp groups: navigation, list-level, quit/close.
            let groups: [&[usize]; 3] = [&[0, 1, 2, 3, 4, 5], &[6, 7, 8, 9], &[10, 12]];
            let sep = sep_style.render("    ");
            let mut out: Vec<String> = Vec::new();
            for (i, group) in groups.iter().enumerate() {
                let enabled: Vec<_> = group.iter().map(|&j| all[j]).filter(|b| b.0).collect();
                if enabled.is_empty() {
                    continue;
                }
                let keys: Vec<_> = enabled.iter().map(|b| b.1).collect();
                let descs: Vec<_> = enabled.iter().map(|b| b.2).collect();
                let key_column = key_style.render(&keys.join("\n"));
                let gap = key_style.render(" ");
                let desc_column = desc_style.render(&descs.join("\n"));
                let column = style::join_horizontal(
                    0.0,
                    &[key_column.as_str(), gap.as_str(), desc_column.as_str()],
                );
                out.push(column);
                // bubbles compares the group index with the group's own length.
                if i + 1 < group.len() {
                    out.push(sep.clone());
                }
            }
            let refs: Vec<&str> = out.iter().map(String::as_str).collect();
            style::join_horizontal(0.0, &refs)
        } else {
            // ShortHelp: up, down, filter, clear, accept, cancel, quit, more.
            let mut line = String::new();
            for &j in &[0usize, 1, 6, 7, 8, 9, 10, 11] {
                let (enabled, key, desc) = all[j];
                if !enabled {
                    continue;
                }
                if !line.is_empty() {
                    line.push_str(&sep_style.inline(true).render(" • "));
                }
                line.push_str(&key_style.inline(true).render(key));
                line.push(' ');
                line.push_str(&desc_style.inline(true).render(desc));
            }
            line
        };
        Style::new().margin_left(1).render(&help)
    }

    fn render_item(&self, index: usize, item: &PickerItem) -> String {
        let selected = index == self.index();
        let (title, desc) = if selected {
            (adaptive(Role::Accent), adaptive(Role::Accent2))
        } else {
            (adaptive(Role::Body), adaptive(Role::Mute))
        };
        let text = format!(
            "{}\n{}\n{}",
            title.render(&item.title),
            desc.render(&item.url),
            desc.render(&item.user)
        );
        if selected {
            // BorderStyle(NormalBorder()).BorderLeft(true): only the left side.
            Style::new()
                .border_style(Border::NORMAL)
                .border_left(true)
                .border_fg(Color::role(Role::Accent))
                .render(&text)
        } else {
            text
        }
    }

    fn populated_view(&self) -> String {
        if self.items.is_empty() {
            if self.filter_state == FilterState::Filtering {
                return String::new();
            }
            return Style::new()
                .fg(Color::adaptive("#909090", "#626262"))
                .render("No items.");
        }
        let start = self.page * self.per_page;
        let end = (start + self.per_page).min(self.items.len());
        let mut out = String::new();
        for (offset, item) in self.items[start.min(end)..end].iter().enumerate() {
            out.push_str(&self.render_item(start + offset, item));
            if start + offset + 1 != end {
                out.push_str("\n\n");
            }
        }
        let on_page = self.items_on_page();
        if on_page < self.per_page {
            out.push_str(&"\n".repeat((self.per_page - on_page) * ITEM_ROWS));
        }
        out
    }

    /// defaultprofile.Model.View: PaddingLeft(1) around list.View().
    pub(crate) fn view(&self) -> String {
        let title = self.title_view();
        let pagination = self.pagination_view();
        let help = self.help_view();
        let avail = LIST_HEIGHT as isize
            - height(&title) as isize
            - height(&pagination) as isize
            - height(&help) as isize;
        let content = Style::new()
            .height(avail.max(0) as usize)
            .render(&self.populated_view());
        let list = style::join_vertical(
            0.0,
            &[
                title.as_str(),
                content.as_str(),
                pagination.as_str(),
                help.as_str(),
            ],
        );
        Style::new().padding_left(1).render(&list)
    }
}

impl InlineModel for ProfilePicker {
    fn key(&mut self, key: &Key) -> bool {
        *self.update(key) != PickerOutcome::Continue
    }
    /// The filter's Ctrl+V still runs the clipboard read, but the picker
    /// model drops the resulting pasteMsg (`apply_paste` stays a no-op).
    fn take_paste_request(&mut self) -> bool {
        self.filter.take_paste_request()
    }
    fn take_panic(&mut self) -> Option<String> {
        if let Some(fault) = self.filter.take_fault() {
            self.outcome = PickerOutcome::Panicked(fault.to_string());
        }
        match &self.outcome {
            PickerOutcome::Panicked(value) => Some(value.clone()),
            _ => None,
        }
    }
    fn view(&self) -> String {
        ProfilePicker::view(self)
    }
}

// ── Self-hosted credential form (pkg/model/credential) ─────────────────────

pub(crate) struct CredentialForm {
    focus: usize,
    inputs: [TextInput; 2],
    focused_style: [bool; 2],
    button_focused: bool,
    /// Starts true and is cleared, never set again, once both values are
    /// valid after any non-navigation key.
    invalid: bool,
}

impl Default for CredentialForm {
    fn default() -> Self {
        Self::new()
    }
}

impl CredentialForm {
    pub(crate) fn new() -> Self {
        let mut user = TextInput::new();
        user.set_char_limit(32);
        user.set_placeholder("username");
        user.set_prompt("user: ");
        // New discards Focus()'s command (tag 1, nothing armed); Init's
        // textinput.Blink then reaches both inputs and arms the focused user
        // (tag 2). Armed at construction, as before.
        user.focus();
        user.disarm_blink();
        user.start_blink();
        let mut pass = TextInput::new();
        pass.set_char_limit(64);
        pass.set_placeholder("password");
        pass.set_prompt("pass: ");
        pass.set_echo(EchoMode::Password('•'));
        Self {
            focus: 0,
            inputs: [user, pass],
            focused_style: [true, false],
            button_focused: false,
            invalid: true,
        }
    }

    fn valid(&self) -> bool {
        let username = self.inputs[0].value();
        let password = self.inputs[1].value();
        !(username.contains(' ') || username.is_empty() || password.is_empty())
    }

    /// credential.Model.Values: both values only when currently valid.
    pub(crate) fn values(&self) -> (String, String) {
        if self.valid() {
            (self.inputs[0].value(), self.inputs[1].value())
        } else {
            (String::new(), String::new())
        }
    }

    /// Returns true on tea.Quit (submit, Esc or Ctrl+C alike).
    pub(crate) fn update(&mut self, key: &Key) -> bool {
        let name = key.name();
        match name.as_str() {
            "ctrl+c" | "esc" => return true,
            "tab" | "shift+tab" | "enter" | "up" | "down" => {
                if name == "enter" && self.focus == 2 && !self.invalid {
                    return true;
                }
                if name == "up" || name == "shift+tab" {
                    self.focus = if self.focus == 0 { 2 } else { self.focus - 1 };
                } else {
                    self.focus = if self.focus >= 2 { 0 } else { self.focus + 1 };
                }
                for (i, (input, styled)) in self
                    .inputs
                    .iter_mut()
                    .zip(self.focused_style.iter_mut())
                    .enumerate()
                {
                    *styled = i == self.focus;
                    if *styled {
                        input.focus();
                    } else {
                        input.blur();
                    }
                }
                self.button_focused = self.focus == 2;
                return false;
            }
            _ => {}
        }
        // Non-intercepted Enter (Alt+Enter) reaches button.Update by KeyType,
        // then credential's Pressed handler checks current validity. Plain
        // Enter above deliberately quits on the sticky latch alone.
        let pressed = self.button_focused && key.code == KeyCode::Enter && !self.invalid;
        for input in &mut self.inputs {
            if input.focused() {
                input.update(key);
            }
        }
        if self.valid() {
            self.invalid = false;
        }
        pressed && self.valid()
    }

    /// pasteMsg: updateInputs reaches only the focused input, then the
    /// validity latch is re-evaluated as for any other message.
    pub(crate) fn paste(&mut self, text: &str) {
        for input in &mut self.inputs {
            if input.focused() {
                input.insert_paste(text);
            }
        }
        if self.valid() {
            self.invalid = false;
        }
    }

    /// One due blink timer fires (earliest; ties in input order). Go's
    /// updateInputs hands its BlinkMsg to both inputs: a blurred input drops
    /// it and the focused one accepts only its own current tag, so a departed
    /// input's timer can toggle the focused caret (Go quirk kept). The
    /// latch re-check Go runs afterwards cannot change: values are unchanged.
    fn blink(&mut self, now: Instant) {
        let due = self
            .inputs
            .iter()
            .enumerate()
            .filter_map(|(index, input)| {
                input
                    .pending_blink()
                    .filter(|timer| timer.due <= now)
                    .map(|timer| (timer.due, index))
            })
            .min();
        let Some((_, source)) = due else {
            return;
        };
        let Some(timer) = self.inputs[source].take_pending_blink() else {
            return;
        };
        for input in &mut self.inputs {
            input.deliver_blink(timer.tag, now);
        }
    }

    fn input_styles(&self, i: usize) -> InputStyles {
        let focused = adaptive(Role::Accent);
        let (prompt, text) = if self.focused_style[i] {
            (focused, focused)
        } else {
            (Style::new(), Style::new())
        };
        InputStyles {
            prompt,
            text,
            cursor: focused,
            ..InputStyles::default()
        }
    }

    pub(crate) fn view(&self) -> String {
        let mut out = String::new();
        for (i, input) in self.inputs.iter().enumerate() {
            out.push_str(&input.view(&self.input_styles(i)));
            out.push('\n');
        }
        let label = if self.invalid { "X" } else { "Submit" };
        let label = if self.button_focused {
            adaptive(Role::Accent).render(label)
        } else {
            adaptive(Role::Mute).render(label)
        };
        out.push_str("[ ");
        out.push_str(&label);
        out.push_str(" ]");
        out
    }
}

impl InlineModel for CredentialForm {
    fn key(&mut self, key: &Key) -> bool {
        self.update(key)
    }
    /// Both inputs' outstanding timers: Go's Blur cancels neither.
    fn next_tick(&self) -> Option<Instant> {
        self.inputs
            .iter()
            .filter_map(TextInput::pending_blink)
            .map(|timer| timer.due)
            .min()
    }
    fn tick(&mut self, now: Instant) {
        self.blink(now);
    }
    fn take_paste_request(&mut self) -> bool {
        let user = self.inputs[0].take_paste_request();
        let pass = self.inputs[1].take_paste_request();
        user || pass
    }
    fn apply_paste(&mut self, text: &str) {
        self.paste(text);
    }
    /// The username field is EchoNormal, so bubbles' word-delete faults are
    /// reachable there (L-AUTH-11).
    fn take_panic(&mut self) -> Option<String> {
        let user = self.inputs[0].take_fault();
        let pass = self.inputs[1].take_fault();
        user.or(pass).map(|fault| fault.to_string())
    }
    fn view(&self) -> String {
        CredentialForm::view(self)
    }
}

// ── Cooked logout confirmation (cmd/logout.go confirmLogout) ──────────────

/// Prompt text exactly as Go prints it before reading a line from stdin.
pub(crate) fn logout_prompt(name: &str, url: &str) -> String {
    use crate::metrics::output::style::{MUTED, SELECTED, Terminal, with_process_render};
    with_process_render(|| {
        let terminal = Terminal::from_process();
        let width: fn(&str) -> usize = style::width;
        format!(
            "Logout from profile {} {}?\n{}\n{} ",
            SELECTED.render(&format!("'{name}'"), &terminal, width),
            MUTED.render(&format!("({url})"), &terminal, width),
            MUTED.render(
                "This will remove the saved URL and credentials from config",
                &terminal,
                width
            ),
            SELECTED.render("Continue? [y/N]:", &terminal, width),
        )
    })
}

/// bufio.Reader.ReadString('\n'): a line without its terminator (EOF) or a
/// read error declines, as in Go.
pub(crate) fn read_confirmation(input: &mut impl std::io::BufRead) -> bool {
    let mut line = Vec::new();
    match input.read_until(b'\n', &mut line) {
        Ok(_) if line.last() == Some(&b'\n') => {}
        _ => return false,
    }
    let text = String::from_utf8_lossy(&line);
    matches!(text.trim().to_lowercase().as_str(), "y" | "yes")
}

// ── Terminal-backed prompts used by src/profile.rs ─────────────────────────

/// Production prompts: real inline sessions and the process stdin/stdout.
pub(crate) struct TerminalPrompts;

impl crate::profile::Prompts for TerminalPrompts {
    fn say(&mut self, text: &str) {
        use std::io::Write;
        let mut stdout = std::io::stdout();
        let _ = stdout.write_all(text.as_bytes());
        let _ = stdout.flush();
    }
    fn select_profile(
        &mut self,
        profiles: &BTreeMap<String, ProfileRecord>,
    ) -> Result<PickerOutcome, String> {
        let mut picker = ProfilePicker::new(profiles);
        Ok(match run_inline(&mut picker)? {
            RunEnd::Panicked(value) => PickerOutcome::Panicked(value),
            RunEnd::Quit => match picker.outcome() {
                PickerOutcome::Continue => PickerOutcome::Canceled,
                other => other.clone(),
            },
        })
    }
    fn credentials(&mut self) -> Result<crate::profile::FormOutcome, String> {
        let mut form = CredentialForm::new();
        Ok(match run_inline(&mut form)? {
            RunEnd::Panicked(value) => crate::profile::FormOutcome::Panicked(value),
            RunEnd::Quit => {
                let (username, password) = form.values();
                crate::profile::FormOutcome::Values(username, password)
            }
        })
    }
    fn confirm_logout(&mut self, name: &str, url: &str) -> bool {
        self.say(&logout_prompt(name, url));
        read_confirmation(&mut std::io::stdin().lock())
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// A Bubble Tea key as the shared input parser would deliver it.
    pub(crate) fn key(name: &str) -> Key {
        let (code, runes) = match name {
            "enter" => (KeyCode::Enter, vec![]),
            "esc" => (KeyCode::Esc, vec![]),
            "tab" => (KeyCode::Tab, vec![]),
            "shift+tab" => (KeyCode::ShiftTab, vec![]),
            "up" => (KeyCode::Up, vec![]),
            "down" => (KeyCode::Down, vec![]),
            "left" => (KeyCode::Left, vec![]),
            "alt+d" => {
                return Key {
                    code: KeyCode::Runes,
                    runes: vec!['d'],
                    alt: true,
                    paste: false,
                };
            }
            "backspace" => (KeyCode::Backspace, vec![]),
            "ctrl+c" => (KeyCode::Ctrl(3), vec![]),
            " " => (KeyCode::Space, vec![' ']),
            text => (KeyCode::Runes, text.chars().collect()),
        };
        Key {
            code,
            runes,
            alt: false,
            paste: false,
        }
    }

    pub(crate) fn typed(text: &str) -> Vec<Key> {
        text.chars().map(|c| key(&c.to_string())).collect()
    }

    fn profiles(names: &[&str]) -> BTreeMap<String, ProfileRecord> {
        names
            .iter()
            .map(|name| {
                (
                    name.to_string(),
                    ProfileRecord {
                        url: format!("http://{name}"),
                        ..Default::default()
                    },
                )
            })
            .collect()
    }

    fn pick(names: &[&str], keys: &[&str]) -> PickerOutcome {
        let mut picker = ProfilePicker::new(&profiles(names));
        for name in keys {
            if *picker.update(&key(name)) != PickerOutcome::Continue {
                break;
            }
        }
        picker.outcome().clone()
    }

    #[test]
    fn picker_selects_by_cursor_and_cancels_on_quit_keys() {
        let names = ["a", "b", "c"];
        assert_eq!(pick(&names, &["enter"]), PickerOutcome::Chosen("a".into()));
        assert_eq!(
            pick(&names, &["down", "j", "enter"]),
            PickerOutcome::Chosen("c".into())
        );
        assert_eq!(
            pick(&names, &["down", "down", "down", "enter"]),
            PickerOutcome::Chosen("c".into())
        );
        assert_eq!(
            pick(&names, &["G", "k", "enter"]),
            PickerOutcome::Chosen("b".into())
        );
        for quit in ["q", "esc", "ctrl+c"] {
            assert_eq!(pick(&names, &[quit]), PickerOutcome::Canceled);
        }
    }

    #[test]
    fn picker_filter_never_narrows_because_matches_are_not_forwarded() {
        // "/c" then accept: Go keeps the full snapshot, so Enter picks the
        // item under the cursor (the first), not the only match "c".
        assert_eq!(
            pick(&["a", "b", "c"], &["/", "c", "enter", "enter"]),
            PickerOutcome::Chosen("a".into())
        );
        // Quit keys are text while filtering; Esc cancels the filter first.
        assert_eq!(
            pick(&["a", "b"], &["/", "q", "esc", "enter"]),
            PickerOutcome::Chosen("a".into())
        );
        assert_eq!(
            pick(&["a", "b"], &["/", "q", "esc", "esc"]),
            PickerOutcome::Canceled
        );
        // An applied filter is cleared by Esc before Esc can quit.
        assert_eq!(
            pick(&["a", "b"], &["/", "x", "enter", "esc", "down", "enter"]),
            PickerOutcome::Chosen("b".into())
        );
        // Accept with an empty filter is disabled; Enter stays in the input.
        let mut picker = ProfilePicker::new(&profiles(&["a", "b"]));
        picker.update(&key("/"));
        assert_eq!(*picker.update(&key("enter")), PickerOutcome::Continue);
        assert_eq!(picker.filter_state, FilterState::Filtering);
    }

    #[test]
    fn picker_pages_four_items_and_full_help_changes_page_size() {
        let names = ["a", "b", "c", "d", "e"];
        let mut picker = ProfilePicker::new(&profiles(&names));
        assert_eq!((picker.per_page, picker.total_pages), (4, 2));
        assert_eq!(
            pick(&names, &["down", "down", "down", "down", "enter"]),
            PickerOutcome::Chosen("e".into())
        );
        assert_eq!(
            pick(&names, &["l", "enter"]),
            PickerOutcome::Chosen("e".into())
        );
        picker.update(&key("?"));
        // Six navigation rows of full help leave 11 lines: two items per page.
        assert_eq!((picker.per_page, picker.total_pages), (2, 3));
        let two = ProfilePicker::new(&profiles(&["a", "b"]));
        assert!(!two.bindings.next_page && two.bindings.show_full);
    }

    #[test]
    fn empty_picker_enter_is_the_go_nil_item_case() {
        assert_eq!(
            pick(&[], &["enter"]),
            PickerOutcome::Panicked(NIL_DEREFERENCE.into())
        );
        assert_eq!(pick(&[], &["q"]), PickerOutcome::Canceled);
        let empty = ProfilePicker::new(&profiles(&[]));
        assert!(!empty.bindings.show_full && !empty.bindings.filter);
    }

    fn form(keys: &[Key]) -> (bool, (String, String)) {
        let mut form = CredentialForm::new();
        let mut quit = false;
        for key in keys {
            if form.update(key) {
                quit = true;
                break;
            }
        }
        (quit, form.values())
    }

    #[test]
    fn credential_values_are_both_or_neither() {
        let mut keys = typed("admin");
        keys.push(key("tab"));
        keys.extend(typed("pw"));
        keys.push(key("esc"));
        assert_eq!(form(&keys), (true, ("admin".into(), "pw".into())));

        // Username only: Esc still quits and the caller saves empty values.
        let mut keys = typed("admin");
        keys.push(key("ctrl+c"));
        assert_eq!(form(&keys), (true, (String::new(), String::new())));

        // A space in the username invalidates both values.
        let mut keys = typed("ad min");
        keys.push(key("down"));
        keys.extend(typed("pw"));
        keys.push(key("esc"));
        assert_eq!(form(&keys), (true, (String::new(), String::new())));
    }

    fn blink_tag(input: &TextInput) -> Option<u64> {
        input.pending_blink().map(|timer| timer.tag)
    }

    #[test]
    fn credential_departed_username_timer_toggles_the_password_on_equal_tags() {
        // Collision A: Init's blink (user tag 2), Tab (password Focus
        // honored: 1), one password chunk (2).
        let mut form = CredentialForm::new();
        assert_eq!(blink_tag(&form.inputs[0]), Some(2));
        form.update(&key("tab"));
        form.update(&key("p"));
        assert_eq!(blink_tag(&form.inputs[1]), Some(2));
        let user = form.inputs[0].pending_blink().expect("Blur keeps it");
        assert!(form.inputs[1].cursor_visible());
        assert_eq!(form.next_tick(), Some(user.due));
        form.tick(user.due);
        assert!(!form.inputs[1].cursor_visible(), "accepted by the password");
        assert_eq!(form.inputs[0].pending_blink(), None);
        let pass = form.inputs[1].pending_blink().expect("re-armed");
        assert_eq!(
            (pass.due, pass.tag),
            (user.due + crate::tui::editor::BLINK_SPEED, 3)
        );
    }

    #[test]
    fn credential_departed_timer_with_a_stale_tag_or_no_focused_input_is_dropped() {
        // Collision C: user chunk (3) vs one password chunk (2): dropped.
        let mut form = CredentialForm::new();
        form.update(&key("admin"));
        form.update(&key("tab"));
        form.update(&key("p"));
        let user = form.inputs[0].pending_blink().unwrap();
        let own = form.inputs[1].pending_blink().unwrap();
        form.tick(user.due);
        assert!(form.inputs[1].cursor_visible());
        assert_eq!(form.inputs[1].pending_blink(), Some(own));
        assert_eq!(form.inputs[0].pending_blink(), None);
        // Collision B: a second password chunk makes the tags equal.
        let mut form = CredentialForm::new();
        form.update(&key("admin"));
        form.update(&key("tab"));
        form.update(&key("p"));
        form.update(&key("q"));
        let user = form.inputs[0].pending_blink().unwrap();
        form.tick(user.due);
        assert!(!form.inputs[1].cursor_visible());
        // D: button focused, both blurred: the timer is consumed and dropped.
        let mut form = CredentialForm::new();
        form.update(&key("tab"));
        form.update(&key("tab"));
        let user = form.inputs[0].pending_blink().unwrap();
        let pass = form.inputs[1].pending_blink().unwrap();
        form.tick(user.due.max(pass.due));
        form.tick(user.due.max(pass.due));
        assert_eq!(form.next_tick(), None);
        assert!(!form.inputs[0].cursor_visible() && !form.inputs[1].cursor_visible());
    }

    #[test]
    fn credential_refocus_cancels_the_departed_timer_and_arms_once() {
        // E: Shift+Tab back honors Focus(): cancels the user's own timer, +1.
        let mut form = CredentialForm::new();
        form.update(&key("admin")); // user 3
        form.update(&key("tab")); // password 1
        form.update(&key("shift+tab")); // user 4
        assert_eq!(blink_tag(&form.inputs[0]), Some(4));
        assert_eq!(blink_tag(&form.inputs[1]), Some(1));
        // The two dues can tie on a coarse clock, so check routing directly:
        // the focused user rejects the departed password's tag 1.
        let pass = form.inputs[1].take_pending_blink().unwrap();
        assert!(!form.inputs[1].deliver_blink(pass.tag, pass.due), "blurred");
        assert!(!form.inputs[0].deliver_blink(pass.tag, pass.due), "stale");
        assert!(form.inputs[0].cursor_visible());
        assert_eq!(blink_tag(&form.inputs[0]), Some(4));
    }

    #[test]
    fn credential_submit_latch_is_sticky_and_enter_cycles_when_invalid() {
        // Enter on the X button while invalid moves focus back to the user field.
        let mut f = CredentialForm::new();
        for k in [key("tab"), key("tab"), key("enter")] {
            assert!(!f.update(&k));
        }
        assert_eq!(f.focus, 0);

        // Once valid, Submit stays enabled even after the password is erased.
        let mut keys = typed("a");
        keys.push(key("tab"));
        keys.extend(typed("b"));
        keys.push(key("backspace"));
        keys.push(key("tab"));
        keys.push(key("enter"));
        assert_eq!(form(&keys), (true, (String::new(), String::new())));
    }

    #[test]
    fn credential_alt_enter_obeys_button_focus_latch_and_current_validity() {
        let Event::Key(alt_enter) = super::super::input::parse(b"\x1b\r")
            .into_iter()
            .next()
            .unwrap()
        else {
            panic!("expected Alt+Enter key");
        };
        assert_eq!(alt_enter.code, KeyCode::Enter);
        assert!(alt_enter.alt);

        let mut f = CredentialForm::new();
        f.update(&key("u"));
        f.update(&key("tab"));
        f.update(&key("p"));
        assert!(!f.invalid && f.valid());
        assert!(!f.update(&alt_enter)); // valid, password focused
        assert_eq!(f.focus, 1);
        f.update(&key("tab"));
        assert!(f.update(&alt_enter)); // valid, Submit focused
        assert_eq!(f.values(), ("u".into(), "p".into()));

        let mut f = CredentialForm::new();
        f.update(&key("tab"));
        f.update(&key("tab"));
        assert!(f.invalid);
        assert!(!f.update(&alt_enter)); // invalid latch does not cycle focus
        assert_eq!(f.focus, 2);
        // Even valid values cannot bypass a still-invalid button. Navigation
        // returns before the validity recheck in the Go reducer.
        f.inputs[0].set_value("u");
        f.inputs[1].set_value("p");
        assert!(!f.update(&alt_enter));
        assert!(!f.invalid); // this message clears the latch for the next key
        assert!(f.update(&alt_enter));

        let mut f = CredentialForm::new();
        for k in [key("u"), key("tab"), key("p"), key("backspace"), key("tab")] {
            assert!(!f.update(&k));
        }
        assert!(!f.invalid && !f.valid());
        assert!(!f.update(&alt_enter)); // Pressed rejects current invalidity
        assert_eq!(f.focus, 2);
        assert_eq!(f.values(), (String::new(), String::new()));
        assert!(f.update(&key("enter"))); // preserve plain Enter's quirk
    }

    #[test]
    fn credential_paste_reaches_only_the_focused_input_and_clears_latch() {
        let mut f = CredentialForm::new();
        f.paste("admin");
        assert!(f.invalid);
        f.update(&key("tab"));
        f.paste("pw");
        assert!(!f.invalid);
        assert_eq!(f.values(), ("admin".into(), "pw".into()));
        // A Ctrl+V on the picker filter requests a read but the result is dropped.
        let mut picker = ProfilePicker::new(&profiles(&["a"]));
        picker.update(&key("/"));
        let ctrl_v = Key {
            code: KeyCode::Ctrl(22),
            runes: vec![],
            alt: false,
            paste: false,
        };
        picker.update(&ctrl_v);
        assert!(InlineModel::take_paste_request(&mut picker));
        assert!(!InlineModel::take_paste_request(&mut picker));
        picker.apply_paste("zz");
        assert_eq!(picker.filter.value(), "");
    }

    #[test]
    fn alt_d_before_the_last_rune_panics_in_form_and_filter() {
        // bubbles deleteWordForward indexes value[pos+1] == value[len].
        let expected = "runtime error: index out of range [2] with length 2";
        let mut f = CredentialForm::new();
        for k in typed("ab") {
            f.update(&k);
        }
        f.update(&key("left"));
        f.update(&key("alt+d"));
        assert_eq!(InlineModel::take_panic(&mut f).as_deref(), Some(expected));

        let mut picker = ProfilePicker::new(&profiles(&["a", "b"]));
        for name in ["/", "a", "b", "left", "alt+d"] {
            picker.update(&key(name));
        }
        assert_eq!(*picker.outcome(), PickerOutcome::Panicked(expected.into()));
        assert_eq!(
            InlineModel::take_panic(&mut picker).as_deref(),
            Some(expected)
        );
    }

    #[test]
    fn logout_confirmation_needs_a_terminated_yes_line() {
        for (input, expected) in [
            ("y\n", true),
            (" YES \r\n", true),
            ("yes", false),
            ("n\n", false),
            ("\n", false),
            ("", false),
        ] {
            assert_eq!(
                read_confirmation(&mut std::io::Cursor::new(input.as_bytes())),
                expected,
                "{input:?}"
            );
        }
    }
}
