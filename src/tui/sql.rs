//! Interactive SQL session (`pb sql run -i`): owned state, a pure reducer
//! that returns explicit effects, a renderer, and the session driver.
//!
//! Behavior follows pkg/model/query.go at the pinned Go revision, including
//! its quirks; docs/phase7-sql.md records each one with its evidence. The
//! reducer never performs I/O: network work leaves as [`Effect`] values and
//! returns as typed completions. Only result windows are guarded by a run ID,
//! exactly as in Go; schema and dataset completions are applied unguarded.
use super::{
    editor::{AreaStyles, EditorFault, InputStyles, PB_AREA_KEYS, TextArea, TextInput},
    input::{Event, Key, KeyCode},
    sql_service::{
        Cell, DatasetsCompletion, QueryData, Record, SchemaCompletion, SqlSessionService,
        WindowCompletion, WindowRequest,
    },
    style::{self, Align, Border, Color, GoPanic, Role, Style},
    terminal::{Closed, Screen, TerminalSession},
};
use crate::{
    ErrorCode, api::ApiClient, config::ProfileStore, error::CliError, parser::ParsedInvocation,
    sql::rewrite,
};
use chrono::{
    DateTime, Datelike, Duration as ChronoDuration, FixedOffset, Local, NaiveDate, NaiveDateTime,
    Offset, TimeZone, Utc,
};
use std::{cell::Cell as StdCell, collections::BTreeMap, io::Write, time::Instant};

const DATE_TIME_KEY: &str = "p_timestamp";
const TAG_KEY: &str = "p_tags";
const METADATA_KEY: &str = "p_metadata";
/// len("TIME [LOCAL]")
const DATE_TIME_WIDTH: usize = 12;
/// len("02 Jun 2026, 13:25 | UTC+05:30")
const SQL_CONTROL_TIME_DISPLAY_WIDTH: i64 = 30;
const SQL_CONTROLS_MIN_WIDTH: i64 = SQL_CONTROL_TIME_DISPLAY_WIDTH + 4;
const SQL_WINDOW_SIZE: i64 = 500;
const SQL_MAX_CACHED_WINDOWS: usize = 3;
const UNKNOWN_SQL_TOTAL: i64 = -1;
const SPOTLIGHT_WIDTH: i64 = 58;
const SPOTLIGHT_MAX_ITEMS: usize = 12;
const EDITOR_PLACEHOLDER: &str = "Write your queries here";
const EMPTY_EDITOR_QUERY: &str = "SELECT * FROM dataset LIMIT 100";
const SPINNER_FRAMES: [&str; 4] = ["|", "/", "-", "\\"];
pub(crate) const SPINNER_INTERVAL: std::time::Duration = std::time::Duration::from_millis(100);
const PARSEABLE_ASCII_ART: &str = " ____   _    ____  ____  _____    _    ____  _     _____
|  _ \\ / \\  |  _ \\/ ___|| ____|  / \\  | __ )| |   | ____|
| |_) / _ \\ | |_) \\___ \\|  _|   / _ \\ |  _ \\| |   |  _|
|  __/ ___ \\|  _ < ___) | |___ / ___ \\| |_) | |___| |___
|_| /_/   \\_\\_| \\_\\____/|_____/_/   \\_\\____/|_____|_____|";

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/// `pb sql run -i`: cmd/query.go RunE's interactive branch. `Ok(status)` is
/// the process exit status: 0, or 2 for Go's unrecovered NewQueryModel panic.
///
/// Returns after the terminal is restored. `stdout` receives only Bubble
/// Tea's panic report when an editor reaches a Go panic (see [`EditorFault`]).
pub(crate) fn execute(
    parsed: &ParsedInvocation,
    stdout: &mut (dyn Write + Send),
    stderr: &mut (dyn Write + Send),
) -> Result<i32, CliError> {
    let raw = parsed.positionals.first().map(String::as_str).unwrap_or("");
    let from = if !parsed.explicit("from") {
        "1h"
    } else if parsed.text("from").is_empty() {
        "1m"
    } else {
        parsed.text("from")
    };
    let to = if parsed.text("to").is_empty() {
        "now"
    } else {
        parsed.text("to")
    };
    // quoteStreamNames, quoteFieldsWithDots and ensureDefaultLimit run
    // before the editor, so the initial editor text carries "LIMIT 500".
    let query = rewrite::prepare_query(raw);
    let start = parse_cli_time(from).map_err(|e| failure(format!("invalid --from value: {e}")))?;
    let end = parse_cli_time(to).map_err(|e| failure(format!("invalid --to value: {e}")))?;
    // NewQueryModel runs before tea.Program.Run: a Go panic there (L-SQL-4)
    // is unrecovered. The runtime prints the value and the goroutine dump
    // (not reproducible; ledgered) to stderr and the process exits 2; the
    // caller turns `Ok(2)` into that exit status.
    if let Err(panic) = new_model_preflight(&query) {
        let _ = write!(stderr, "panic: {panic}\n\ngoroutine 1 [running]:\n");
        let _ = stderr.flush();
        return Ok(2);
    }
    let store = ProfileStore::from_env().map_err(|e| CliError::new(e.code, e.message))?;
    let profile = store
        .active_profile()
        .map_err(|e| CliError::new(e.code, e.message))?;
    let profile_url = profile.url.clone();
    let service = SqlSessionService::new(ApiClient::new(profile, store).map_err(CliError::from)?);
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| failure(e.to_string()))?;
    let mut session =
        TerminalSession::open(Screen::Fullscreen).map_err(|e| failure(e.to_string()))?;
    let size = session
        .size()
        .map(|s| (i64::from(s.width), i64::from(s.height)));
    let mut state = QueryState::new(profile_url, &query, start, end, size);
    let end = runtime.block_on(drive(&mut session, &mut state, service));
    let result = match end {
        DriveEnd::Quit => {
            // Go renders View() once more after tea.Quit, then restores;
            // tea.Program.Run's caller ignores restore errors.
            let final_view = state.view(Utc::now());
            match state.take_fault() {
                Some(fault) => report_panic(session, fault, stdout),
                None => {
                    let _ = close(session, &final_view);
                    Ok(())
                }
            }
        }
        DriveEnd::Panic(fault) => report_panic(session, fault, stdout),
        // A command (effect task) panic: Bubble Tea's per-command recover
        // prints the same report and kills the program; Run returns
        // ErrProgramKilled wrapped with the canceled context, which
        // cmd/query.go returns (exit 1).
        DriveEnd::Panicked(value) => {
            let _ = report_panic(session, value, stdout);
            Err(failure("program was killed: context canceled"))
        }
        DriveEnd::InputError(message) => {
            let _ = close(session, &state.view(Utc::now()));
            Err(failure(message))
        }
    };
    // Go exits without waiting for in-flight windows; do not either.
    runtime.shutdown_background();
    result.map(|()| 0)
}

/// Bubble Tea v1.2.2 recoverFromPanic: kill the program (restore without a
/// final render), print the panic report to stdout, and return zero values;
/// cmd/query.go then returns the nil error, so the command succeeds. Go also
/// writes debug.PrintStack() to stderr; that goroutine dump is not
/// reproducible and is ledgered (docs/phase7-sql.md L-SQL-3).
fn report_panic(
    session: TerminalSession,
    value: impl std::fmt::Display,
    stdout: &mut (dyn Write + Send),
) -> Result<(), CliError> {
    drop(session);
    let _ = write!(
        stdout,
        "Caught panic:\n\n{value}\n\nRestoring terminal...\n\n"
    );
    let _ = stdout.flush();
    Ok(())
}

fn failure(message: impl Into<String>) -> CliError {
    CliError::new(ErrorCode::CommandFailed, message)
}

// ---------------------------------------------------------------------------
// Driver: owns the event source, effect tasks and timers.
// ---------------------------------------------------------------------------

/// A Go fault reached by Update/View: recovered by Run (exit 0) unless it
/// is the editor hang, which never returns.
#[derive(Debug, PartialEq)]
pub(crate) enum Fault {
    Editor(EditorFault),
    Go(String),
}

impl std::fmt::Display for Fault {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Editor(fault) => fault.fmt(f),
            Self::Go(value) => f.write_str(value),
        }
    }
}

enum DriveEnd {
    Quit,
    /// A Go panic reached inside Update/View (editor or render helper).
    Panic(Fault),
    /// A native panic in an effect task (a Go command goroutine), reported
    /// by the terminal session after its hook restored the terminal.
    Panicked(String),
    InputError(String),
}

async fn drive(
    session: &mut TerminalSession,
    state: &mut QueryState,
    service: SqlSessionService,
) -> DriveEnd {
    // Completions are stamped when their task finishes; terminal events are
    // stamped when the input thread queued them. Processing the older of the
    // two reproduces Bubble Tea's single message channel (arrival order).
    // Bounded handoff: a finished task waits for capacity, then stamps its
    // completion, so the stamp orders it after everything already queued.
    let (sender, mut completions) =
        tokio::sync::mpsc::channel::<(Instant, Msg)>(COMPLETION_CAPACITY);
    // Every effect task stays owned here; leaving the loop drops the set,
    // which aborts in-flight requests, timers and clipboard reads.
    let mut tasks = tokio::task::JoinSet::new();
    let effects = state.init();
    if dispatch(&mut tasks, &service, &sender, effects) {
        return DriveEnd::Quit;
    }
    let view = state.view(Utc::now());
    if let Some(fault) = state.take_fault() {
        return fault_end(fault).await;
    }
    draw(session, &view);
    // The completion head is the only buffered message. The terminal head is
    // only peeked: it is consumed when it wins, so a later Quit/control event
    // is not accepted (nor its reader lane freed) while an earlier
    // completion is still being processed.
    let mut done: Option<(Instant, Msg)> = None;
    loop {
        // Yield once per message so spawned network tasks and the I/O
        // driver progress even while input keeps arriving (current-thread
        // runtime: an always-ready input queue would otherwise starve them).
        tokio::task::yield_now().await;
        // Reap finished tasks every iteration, whatever source wins.
        while tasks.try_join_next().is_some() {}
        if done.is_none() {
            done = completions.try_recv().ok();
        }
        let msg = match next_step(session, &mut done) {
            Step::Completion(msg) => msg,
            Step::Event(event) => match event {
                Event::Key(key) => Msg::Key(key),
                Event::Resize(size) => Msg::Resize(i64::from(size.width), i64::from(size.height)),
                // Go passes unknown input to Update, which ignores it.
                Event::Unknown => continue,
                // SIGINT/SIGTERM: tea.QuitMsg ends the loop without Update.
                Event::Quit => return DriveEnd::Quit,
                Event::InputError(message) => return DriveEnd::InputError(message),
                Event::Panicked(value) => return DriveEnd::Panicked(value),
            },
            Step::Closed => return DriveEnd::Quit,
            Step::Wait => {
                tokio::select! {
                    // Readiness only; the event stays queued for next_step.
                    _ = session.event_ready() => {}
                    Some(finished) = completions.recv() => done = Some(finished),
                    Some(_) = tasks.join_next(), if !tasks.is_empty() => {}
                }
                continue;
            }
        };
        let effects = state.update(msg, Utc::now());
        if let Some(fault) = state.take_fault() {
            return fault_end(fault).await;
        }
        if dispatch(&mut tasks, &service, &sender, effects) {
            return DriveEnd::Quit;
        }
        let view = state.view(Utc::now());
        if let Some(fault) = state.take_fault() {
            return fault_end(fault).await;
        }
        draw(session, &view);
    }
}

/// The terminal queue as the driver sees it: peek the head's stamp, take
/// the head only after it has won the merge.
trait EventSource {
    fn peek_stamp(&self) -> Result<Option<Instant>, Closed>;
    fn take_event(&mut self) -> Result<Option<(Instant, Event)>, Closed>;
}

impl EventSource for TerminalSession {
    fn peek_stamp(&self) -> Result<Option<Instant>, Closed> {
        self.peek_event_stamp()
    }
    fn take_event(&mut self) -> Result<Option<(Instant, Event)>, Closed> {
        self.try_next_event_stamped()
    }
}

#[derive(Debug)]
enum Step {
    Event(Event),
    Completion(Msg),
    /// Nothing is ready on either source.
    Wait,
    /// The terminal source stopped and no completion is pending.
    Closed,
}

/// One arrival-ordered step (Bubble Tea's single channel): the older of the
/// terminal head and the completion head wins; ties go to the terminal.
fn next_step(source: &mut impl EventSource, done: &mut Option<(Instant, Msg)>) -> Step {
    let completion = |done: &mut Option<(Instant, Msg)>| match done.take() {
        Some((_, msg)) => Step::Completion(msg),
        None => Step::Closed,
    };
    let key = match source.peek_stamp() {
        Ok(stamp) => stamp,
        Err(Closed) => return completion(done),
    };
    let terminal_wins = match (key, done.as_ref()) {
        (Some(pressed), Some((finished, _))) => pressed <= *finished,
        (Some(_), None) => true,
        (None, Some(_)) => false,
        (None, None) => return Step::Wait,
    };
    if !terminal_wins {
        return completion(done);
    }
    match source.take_event() {
        Ok(Some((_, event))) => Step::Event(event),
        Ok(None) => Step::Wait,
        Err(Closed) => completion(done),
    }
}

fn close(session: TerminalSession, frame: &Frame) -> Result<(), super::terminal::SessionError> {
    match frame {
        Frame::Text(text) => session.close(text),
        Frame::Bytes(bytes) => session.close_bytes(bytes),
    }
}

fn draw(session: &mut TerminalSession, frame: &Frame) {
    match frame {
        Frame::Text(text) => session.draw(text),
        Frame::Bytes(bytes) => session.draw_bytes(bytes),
    }
}

/// A Go panic ends the program; a Go hang never returns from Update, so no
/// further input, completion or signal is processed (ledger L-EDITOR-2).
async fn fault_end(fault: Fault) -> DriveEnd {
    if fault == Fault::Editor(EditorFault::Hang) {
        std::future::pending::<()>().await;
    }
    DriveEnd::Panic(fault)
}

/// Bounds completions waiting for the session loop; finished tasks wait
/// for capacity instead of growing a queue.
const COMPLETION_CAPACITY: usize = 16;

/// Reserve capacity first, then stamp: the stamp reflects when the message
/// actually entered the session's queue (Bubble Tea's channel send).
async fn send_stamped(sender: &tokio::sync::mpsc::Sender<(Instant, Msg)>, msg: Msg) {
    if let Ok(permit) = sender.reserve().await {
        permit.send((Instant::now(), msg));
    }
}

/// NewSQLWindowFetchTask's `defer recover()` (query.go:2612-2617): a panic
/// becomes a failed window with errMsg "query failed" for the same run and
/// window, and the session keeps running. The fetch runs in an inner set
/// owned by this task, so aborting the task (session exit) aborts the fetch;
/// `recovered` keeps the session's panic reaction (restore + Event::Panicked)
/// out of it. Schema and dataset fetches have no such recover in Go and keep
/// the command-goroutine kill path. `None` only for a cancelled fetch.
async fn recover_window(
    run_id: u64,
    window_index: usize,
    fetch: impl std::future::Future<Output = WindowCompletion> + Send + 'static,
) -> Option<WindowCompletion> {
    let mut inner = tokio::task::JoinSet::new();
    inner.spawn(super::terminal::recovered(fetch));
    match inner.join_next().await {
        Some(Ok(completion)) => Some(completion),
        Some(Err(error)) if error.is_panic() => Some(WindowCompletion {
            run_id,
            window_index,
            result: Err("query failed".into()),
        }),
        _ => None,
    }
}

/// Start each effect as an owned task; `true` means quit.
fn dispatch(
    tasks: &mut tokio::task::JoinSet<()>,
    service: &SqlSessionService,
    sender: &tokio::sync::mpsc::Sender<(Instant, Msg)>,
    effects: Vec<Effect>,
) -> bool {
    for effect in effects {
        let sender = sender.clone();
        let service = service.clone();
        match effect {
            Effect::Quit => return true,
            Effect::FetchWindow(request) => {
                tasks.spawn(async move {
                    let (run_id, window_index) = (request.run_id, request.window_index);
                    let fetch = async move { service.fetch_window(request).await };
                    if let Some(completion) = recover_window(run_id, window_index, fetch).await {
                        send_stamped(&sender, Msg::Window(completion)).await;
                    }
                });
            }
            Effect::FetchSchema(dataset) => {
                tasks.spawn(async move {
                    let completion = service.fetch_schema(dataset).await;
                    send_stamped(&sender, Msg::Schema(completion)).await;
                });
            }
            Effect::FetchDatasets => {
                tasks.spawn(async move {
                    let completion = service.fetch_datasets().await;
                    send_stamped(&sender, Msg::Datasets(completion)).await;
                });
            }
            Effect::SpinnerTick { tag, delayed } => {
                tasks.spawn(async move {
                    if delayed {
                        tokio::time::sleep(SPINNER_INTERVAL).await;
                    }
                    send_stamped(&sender, Msg::SpinnerTick(tag)).await;
                });
            }
            Effect::ReadClipboard => {
                // QueryModel.Update has no pasteMsg case: Go reads the
                // clipboard and then drops the result. Do the same.
                tasks.spawn(async move {
                    let _ = super::clipboard::read().await;
                });
            }
        }
    }
    false
}

// ---------------------------------------------------------------------------
// Messages and effects
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub(crate) enum Msg {
    Key(Key),
    Resize(i64, i64),
    SpinnerTick(u64),
    Window(WindowCompletion),
    Schema(SchemaCompletion),
    Datasets(DatasetsCompletion),
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Effect {
    FetchWindow(WindowRequest),
    /// The dataset's Go bytes (raw when extractDataset cut inside a rune).
    FetchSchema(Vec<u8>),
    FetchDatasets,
    /// spinner.Tick (immediate) or the spinner's own 100ms re-arm.
    SpinnerTick {
        tag: u64,
        delayed: bool,
    },
    /// textarea/textinput Paste command (Ctrl+V reaching an editor).
    ReadClipboard,
    Quit,
}

// ---------------------------------------------------------------------------
// Go time values with their location
// ---------------------------------------------------------------------------

/// A time.Location as far as this flow can observe it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum Zone {
    Local,
    Utc,
    Fixed(FixedOffset),
}

/// time.Time: an instant plus the location it formats in.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct GoTime {
    pub(crate) instant: DateTime<Utc>,
    pub(crate) zone: Zone,
}

impl GoTime {
    pub(crate) fn local(instant: DateTime<Utc>) -> Self {
        Self {
            instant,
            zone: Zone::Local,
        }
    }
    pub(crate) fn offset(&self) -> FixedOffset {
        match self.zone {
            Zone::Local => Local
                .offset_from_utc_datetime(&self.instant.naive_utc())
                .fix(),
            Zone::Utc => FixedOffset::east_opt(0).expect("zero offset"),
            Zone::Fixed(offset) => offset,
        }
    }
    pub(crate) fn zoned(&self) -> DateTime<FixedOffset> {
        self.instant.with_timezone(&self.offset())
    }
    pub(crate) fn format(&self, layout: &str) -> String {
        self.zoned().format(layout).to_string()
    }
    pub(crate) fn naive_local(&self) -> NaiveDateTime {
        self.zoned().naive_local()
    }
    /// time.Date in this time's location.
    pub(crate) fn from_naive(naive: NaiveDateTime, zone: Zone) -> Self {
        let instant = match zone {
            Zone::Utc => naive.and_utc(),
            Zone::Fixed(offset) => {
                (naive - ChronoDuration::seconds(offset.local_minus_utc().into())).and_utc()
            }
            Zone::Local => go_date_utc(naive, |utc| {
                i64::from(Local.offset_from_utc_datetime(&utc).local_minus_utc())
            })
            .and_utc(),
        };
        Self { instant, zone }
    }
    pub(crate) fn add(self, duration: ChronoDuration) -> Self {
        Self {
            instant: self.instant + duration,
            zone: self.zone,
        }
    }
    /// time.AddDate: calendar arithmetic with Go's overflow normalization.
    pub(crate) fn add_date(self, years: i32, months: i32, days: i64) -> Self {
        let naive = self.naive_local();
        let total = i64::from(naive.year()) * 12
            + i64::from(naive.month0())
            + i64::from(years) * 12
            + i64::from(months);
        let Some(first) = NaiveDate::from_ymd_opt(
            total.div_euclid(12) as i32,
            total.rem_euclid(12) as u32 + 1,
            1,
        ) else {
            return self;
        };
        let date = first + ChronoDuration::days(i64::from(naive.day()) - 1 + days);
        Self::from_naive(date.and_time(naive.time()), self.zone)
    }
    /// pkg/model/datetime addMonthsClamped.
    pub(crate) fn add_months_clamped(self, months: i32) -> Self {
        let naive = self.naive_local();
        let total = naive.year() * 12 + naive.month0() as i32 + months;
        let (year, month) = (total.div_euclid(12), total.rem_euclid(12) as u32 + 1);
        let Some(first) = NaiveDate::from_ymd_opt(year, month, 1) else {
            return self;
        };
        let next = if month == 12 {
            NaiveDate::from_ymd_opt(year + 1, 1, 1)
        } else {
            NaiveDate::from_ymd_opt(year, month + 1, 1)
        };
        let last_day = next.map_or(31, |n| (n - first).num_days() as u32);
        let day = naive.day().min(last_day);
        let date = NaiveDate::from_ymd_opt(year, month, day).unwrap_or(first);
        Self::from_naive(date.and_time(naive.time()), self.zone)
    }
}

/// time.Date's wall-clock to UTC step (go1.25 time.go Date): take the
/// offset at the wall time read as UTC; if it is non-zero and the corrected
/// instant leaves that zone period, use the offset at the corrected instant.
/// Comparing the corrected instant's offset is equivalent to Go's period
/// bounds check, since the same offset gives the same result.
/// A skipped wall time (spring forward) therefore moves back by the gap in
/// zones west of UTC and forward in zones east of it; a repeated wall time
/// resolves to the occurrence whose offset Go finds first (not always the
/// earlier one).
fn go_date_utc(wall: NaiveDateTime, offset_at: impl Fn(NaiveDateTime) -> i64) -> NaiveDateTime {
    let first = offset_at(wall);
    if first == 0 {
        return wall;
    }
    let utc = wall - ChronoDuration::seconds(first);
    let second = offset_at(utc);
    if second == first {
        utc
    } else {
        wall - ChronoDuration::seconds(second)
    }
}

/// cmd/query.go parseTimeStr, keeping time.Parse's location choice:
/// "Z" is UTC, an offset equal to Local's at that instant is Local, any
/// other offset is a fixed zone; now/durations are Local.
pub(crate) fn parse_cli_time(value: &str) -> Result<GoTime, String> {
    let instant = crate::time_compat::parse_time(value)?;
    if value == "now" || !value.contains('T') {
        return Ok(GoTime::local(instant));
    }
    if value.ends_with('Z') {
        return Ok(GoTime {
            instant,
            zone: Zone::Utc,
        });
    }
    let suffix = &value[value.len().saturating_sub(6)..];
    let sign = if suffix.starts_with('-') { -1 } else { 1 };
    let hours: i32 = suffix.get(1..3).and_then(|h| h.parse().ok()).unwrap_or(0);
    let minutes: i32 = suffix.get(4..6).and_then(|m| m.parse().ok()).unwrap_or(0);
    let seconds = sign * (hours * 3600 + minutes * 60);
    let local = Local.offset_from_utc_datetime(&instant.naive_utc()).fix();
    let zone = if local.local_minus_utc() == seconds {
        Zone::Local
    } else {
        FixedOffset::east_opt(seconds).map_or(Zone::Utc, Zone::Fixed)
    };
    Ok(GoTime { instant, zone })
}

fn format_rfc3339_utc(instant: DateTime<Utc>) -> String {
    instant.format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

/// time.Duration.Round(time.Second): halves away from zero.
fn round_to_second(duration: ChronoDuration) -> i64 {
    let millis_total = duration.num_nanoseconds().unwrap_or(i64::MAX);
    let second = 1_000_000_000i64;
    let quotient = millis_total / second;
    let remainder = millis_total % second;
    if remainder.abs() * 2 >= second {
        quotient + remainder.signum()
    } else {
        quotient
    }
}

// ---------------------------------------------------------------------------
// pkg/model/datetime: one segmented timestamp field
// ---------------------------------------------------------------------------

const LOCAL_LAYOUT: &str = "%Y-%b-%d %H:%M:%S";
const SEGMENT_STARTS: [usize; 6] = [0, 5, 9, 12, 15, 18];
const SEGMENT_ENDS: [usize; 6] = [4, 8, 11, 14, 17, 20];

#[derive(Clone, Debug)]
pub(crate) struct DateTimeInput {
    time: GoTime,
    value: Vec<char>,
    pos: usize,
    focused: bool,
}

impl DateTimeInput {
    fn new(time: GoTime) -> Self {
        let mut input = Self {
            time,
            value: Vec::new(),
            pos: 0,
            focused: false,
        };
        input.set_time(time);
        input
    }
    fn local_value(&self) -> String {
        self.value.iter().collect()
    }
    /// SetTime: textinput.SetValue keeps the cursor unless it was at 0 on an
    /// empty value or now lies past the end.
    fn set_time(&mut self, time: GoTime) {
        let empty = self.value.is_empty();
        self.time = time;
        self.value = time.format(LOCAL_LAYOUT).chars().collect();
        if (self.pos == 0 && empty) || self.pos > self.value.len() {
            self.pos = self.value.len();
        }
    }
    fn value_utc(&self) -> String {
        format_rfc3339_utc(self.time.instant)
    }
    fn focus_first_segment(&mut self) {
        self.pos = SEGMENT_STARTS[0].min(self.value.len());
    }
    fn update(&mut self, key: &Key) {
        if !self.focused {
            return;
        }
        match key.code {
            KeyCode::Right => return self.move_segment(1),
            KeyCode::Left => return self.move_segment(-1),
            KeyCode::Up => return self.adjust_segment(1),
            KeyCode::Down => return self.adjust_segment(-1),
            _ => {}
        }
        // textinput navigation keys pass through.
        if key.is(&["right", "ctrl+f"]) {
            return self.move_segment(1);
        }
        if key.is(&["left", "ctrl+b"]) {
            return self.move_segment(-1);
        }
        if key.is(&["alt+right", "ctrl+right", "alt+f"]) {
            return self.word_forward();
        }
        if key.is(&["alt+left", "ctrl+left", "alt+b"]) {
            return self.word_backward();
        }
        if key.is(&["home", "ctrl+a"]) {
            self.pos = 0;
            return;
        }
        if key.is(&["end", "ctrl+e"]) {
            self.pos = self.value.len();
            return;
        }
        // Replace the digit under the cursor and reparse in Local.
        if key.runes.len() == 1 && key.runes[0].is_ascii_digit() {
            let pos = self.pos;
            if pos >= self.value.len() || !self.value[pos].is_ascii_digit() {
                return;
            }
            let mut candidate = self.value.clone();
            candidate[pos] = key.runes[0];
            let text: String = candidate.iter().collect();
            if let Ok(naive) = NaiveDateTime::parse_from_str(&text, LOCAL_LAYOUT) {
                self.set_time(GoTime::from_naive(naive, Zone::Local));
                self.pos = pos.min(self.value.len());
            }
        }
    }
    fn word_forward(&mut self) {
        let len = self.value.len();
        if self.pos >= len || len == 0 {
            return;
        }
        let mut i = self.pos;
        while i < len && self.value[i].is_whitespace() {
            self.pos = (self.pos + 1).min(len);
            i += 1;
        }
        while i < len && !self.value[i].is_whitespace() {
            self.pos = (self.pos + 1).min(len);
            i += 1;
        }
    }
    fn word_backward(&mut self) {
        if self.pos == 0 || self.value.is_empty() {
            return;
        }
        let mut i = self.pos as i64 - 1;
        while i >= 0 && self.value[i as usize].is_whitespace() {
            self.pos = self.pos.saturating_sub(1);
            i -= 1;
        }
        while i >= 0 && !self.value[i as usize].is_whitespace() {
            self.pos = self.pos.saturating_sub(1);
            i -= 1;
        }
    }
    fn adjust_segment(&mut self, delta: i32) {
        let segment = segment_index(self.pos);
        let next = match segment {
            0 => self.time.add_date(delta, 0, 0),
            1 => self.time.add_months_clamped(delta),
            2 => self.time.add_date(0, 0, i64::from(delta)),
            3 => self.time.add(ChronoDuration::hours(delta.into())),
            4 => self.time.add(ChronoDuration::minutes(delta.into())),
            _ => self.time.add(ChronoDuration::seconds(delta.into())),
        };
        self.set_time(next);
        self.pos = SEGMENT_STARTS[segment].min(self.value.len());
    }
    fn move_segment(&mut self, direction: i64) {
        let segment = segment_index(self.pos) as i64 + direction;
        if segment < 0 || segment >= SEGMENT_STARTS.len() as i64 {
            return;
        }
        self.pos = SEGMENT_STARTS[segment as usize].min(self.value.len());
    }
}

fn segment_index(pos: usize) -> usize {
    for i in 0..SEGMENT_STARTS.len() {
        if pos >= SEGMENT_STARTS[i] && pos < SEGMENT_ENDS[i] {
            return i;
        }
    }
    if pos < SEGMENT_STARTS[0] {
        return 0;
    }
    SEGMENT_STARTS
        .iter()
        .position(|&start| pos < start)
        .unwrap_or(SEGMENT_STARTS.len() - 1)
}

// ---------------------------------------------------------------------------
// pkg/model/timeinput.go + timerange.go: the time-range picker
// ---------------------------------------------------------------------------

const RANGE_NAVIGATION: [&str; 4] = ["list", "start", "end", "display"];
/// (duration in seconds, label); timerange.go timeDurations.
const TIME_PRESETS: [(i64, &str); 5] = [
    (-10 * 60, "10 Minutes"),
    (-3600, "1 Hour"),
    (-5 * 3600, "5 Hours"),
    (-24 * 3600, "1 Day"),
    (-72 * 3600, "3 Days"),
];

#[derive(Clone, Debug)]
pub(crate) struct TimeInput {
    start: DateTimeInput,
    end: DateTimeInput,
    list_index: usize,
    focus: usize,
    instant: bool,
    display_utc: bool,
}

impl TimeInput {
    pub(crate) fn new(start: GoTime, end: GoTime) -> Self {
        let mut start = DateTimeInput::new(start);
        start.focused = true;
        Self {
            start,
            end: DateTimeInput::new(end),
            list_index: 0,
            focus: 0,
            instant: false,
            display_utc: false,
        }
    }
    pub(crate) fn start(&self) -> GoTime {
        self.start.time
    }
    pub(crate) fn end(&self) -> GoTime {
        self.end.time
    }
    pub(crate) fn start_value_utc(&self) -> String {
        self.start.value_utc()
    }
    pub(crate) fn end_value_utc(&self) -> String {
        self.end.value_utc()
    }
    pub(crate) fn display_utc(&self) -> bool {
        self.display_utc
    }
    fn current_focus(&self) -> &'static str {
        RANGE_NAVIGATION[self.focus]
    }
    fn end_time_allowed(&self, now: DateTime<Utc>) -> bool {
        if self.end.time.instant > now {
            return false;
        }
        self.instant || self.end.time.instant >= self.start.time.instant
    }
    /// SyncPreset: nearest preset to the current span (first on ties).
    pub(crate) fn sync_preset(&mut self, now: DateTime<Utc>) {
        let target = if self.instant {
            round_to_second(self.end.time.instant - now)
        } else {
            round_to_second(self.start.time.instant - self.end.time.instant)
        };
        let mut best = 0;
        let mut best_diff = (target - TIME_PRESETS[0].0).abs();
        for (i, (duration, _)) in TIME_PRESETS.iter().enumerate() {
            let diff = (target - duration).abs();
            if diff < best_diff {
                best = i;
                best_diff = diff;
            }
        }
        self.list_index = best;
    }
    fn focus_selected(&mut self) {
        self.start.focused = false;
        self.end.focused = false;
        match self.current_focus() {
            "start" => {
                self.start.focused = true;
                self.start.focus_first_segment();
            }
            "end" => {
                self.end.focused = true;
                self.end.focus_first_segment();
            }
            _ => {}
        }
    }
    fn navigate(&mut self, key: &Key) {
        let n = RANGE_NAVIGATION.len() as i64;
        let mut focus = self.focus as i64;
        match key.name().as_str() {
            "shift+tab" => {
                if focus == 0 {
                    focus = n;
                }
                focus -= 1;
                if self.instant && RANGE_NAVIGATION[focus as usize] == "start" {
                    if focus == 0 {
                        focus = n;
                    }
                    focus -= 1;
                }
            }
            "tab" => {
                if focus == n - 1 {
                    focus = -1;
                }
                focus += 1;
                if self.instant && RANGE_NAVIGATION[focus as usize] == "start" {
                    if focus == n - 1 {
                        focus = -1;
                    }
                    focus += 1;
                }
            }
            _ => return,
        }
        self.focus = focus as usize;
    }
    pub(crate) fn update(&mut self, key: &Key, now: DateTime<Utc>) {
        if matches!(key.code, KeyCode::Tab | KeyCode::ShiftTab) {
            self.navigate(key);
            self.focus_selected();
            return;
        }
        match self.current_focus() {
            "list" => {
                if !matches!(key.code, KeyCode::Up | KeyCode::Down) {
                    return;
                }
                let previous = TIME_PRESETS[self.list_index].0;
                // bubbles list: "up"/"k", "down"/"j" — alt+arrow names miss.
                if key.is(&["up"]) && self.list_index > 0 {
                    self.list_index -= 1;
                } else if key.is(&["down"]) && self.list_index + 1 < TIME_PRESETS.len() {
                    self.list_index += 1;
                }
                let duration = ChronoDuration::seconds(TIME_PRESETS[self.list_index].0);
                if self.instant {
                    let anchor = self.end.time.add(-ChronoDuration::seconds(previous));
                    self.end.set_time(anchor.add(duration));
                } else {
                    let start = self.end.time.add(duration);
                    self.start.set_time(start);
                }
            }
            "start" => {
                let previous = self.start.clone();
                self.start.update(key);
                if self.start.time.instant > self.end.time.instant {
                    self.start = previous;
                }
            }
            "end" => {
                let previous = self.end.clone();
                self.end.update(key);
                if !self.end_time_allowed(now) {
                    self.end = previous;
                }
            }
            _ => {
                if matches!(
                    key.code,
                    KeyCode::Left | KeyCode::Right | KeyCode::Up | KeyCode::Down | KeyCode::Space
                ) {
                    self.display_utc = !self.display_utc;
                }
            }
        }
    }

    /// TimeInputModel.View.
    pub(crate) fn view(&self, now: DateTime<Utc>, panics: &RenderPanics) -> String {
        const WIDTH: i64 = 72;
        const LEFT_W: i64 = 27;
        const GAP_W: i64 = 2;
        let right_w = WIDTH - 4 - LEFT_W - GAP_W;
        let border = Style::new().fg(act(Role::BorderHi));
        let title = Style::new().fg(act(Role::Accent)).bold(true);
        let label = Style::new().fg(act(Role::Faint)).bold(true);
        let mut lines = vec![pane_rule(
            panics,
            "┌",
            "┐",
            "QUERY TIME",
            WIDTH,
            &border,
            &title,
        )];
        let header = time_picker_join(
            &label.render("  PRESETS"),
            &label.render("CUSTOM RANGE"),
            LEFT_W,
            GAP_W,
            right_w,
        );
        let mut start_box = time_picker_input_box(
            panics,
            "start",
            &self.start,
            right_w,
            self.current_focus() == "start",
            false,
        );
        let end_title = if self.instant {
            "evaluation time"
        } else {
            "end"
        };
        let show_now = (now - self.end.time.instant).num_milliseconds().abs() <= 2000;
        let end_box = time_picker_input_box(
            panics,
            end_title,
            &self.end,
            right_w,
            self.current_focus() == "end",
            show_now,
        );
        if self.instant {
            start_box = vec![String::new(), String::new(), String::new()];
        }
        let display_rows = time_display_mode(
            self.display_utc,
            right_w,
            self.current_focus() == "display",
            self.end.time,
        );
        let mut rows = vec![header];
        for i in 0..9 {
            let left = if i < TIME_PRESETS.len() {
                time_picker_preset(i, self.list_index, self.current_focus() == "list")
            } else {
                String::new()
            };
            let right = match i {
                0..=2 => start_box[i].clone(),
                3..=5 => end_box[i - 3].clone(),
                6 | 7 => display_rows[i - 6].clone(),
                _ => String::new(),
            };
            rows.push(time_picker_join(&left, &right, LEFT_W, GAP_W, right_w));
        }
        let body = style::join_vertical(0.0, &rows.iter().map(String::as_str).collect::<Vec<_>>());
        lines.extend(pane_body_lines(&body, WIDTH, rows.len() as i64, &border));
        lines.push(border.render(&format!("└{}┘", "─".repeat((WIDTH - 2) as usize))));
        style::join_vertical(0.0, &lines.iter().map(String::as_str).collect::<Vec<_>>())
    }
}

fn time_picker_preset(index: usize, selected: usize, focused: bool) -> String {
    let label = TIME_PRESETS[index].1;
    if index != selected {
        return format!("  {}", Style::new().fg(act(Role::Body)).render(label));
    }
    if focused {
        let rail = Style::new().bg(act(Role::Active)).render(" ");
        let name = Style::new().fg(act(Role::Active)).bold(true).render(label);
        return format!("{rail} {name}");
    }
    format!(
        "  {}",
        Style::new().fg(act(Role::Accent)).bold(true).render(label)
    )
}

fn time_display_mode(utc: bool, width: i64, focused: bool, reference: GoTime) -> Vec<String> {
    let mut label = Style::new().fg(act(Role::Faint)).bold(true);
    if focused {
        label = label.fg(act(Role::Active));
    }
    let active = Style::new()
        .fg(act(Role::InvertText))
        .bg(act(Role::Active))
        .bold(true);
    let inactive = Style::new().fg(act(Role::Body));
    let local_label = format!(" Local {} ", reference.format("UTC%:z"));
    let (local, utc_part) = if utc {
        (inactive.render(&local_label), active.render(" UTC "))
    } else {
        (active.render(&local_label), inactive.render(" UTC "))
    };
    let mut line = format!("{local}  {utc_part}");
    if style::width(&line) as i64 > width {
        line = reflow_truncate(&line, width.max(0) as usize, "");
    }
    vec![label.render("DISPLAY RESULTS AS"), line]
}

fn time_picker_input_box(
    panics: &RenderPanics,
    title: &str,
    input: &DateTimeInput,
    width: i64,
    focused: bool,
    show_now: bool,
) -> Vec<String> {
    let (border_color, title_color) = if focused {
        (Role::Active, Role::Active)
    } else {
        (Role::Border, Role::Faint)
    };
    let border = Style::new().fg(act(border_color));
    let title_style = Style::new().fg(act(title_color)).bold(focused);
    let top = pane_rule(panics, "┌", "┐", title, width, &border, &title_style);
    let inner_w = width - 2;
    let value_w = (inner_w - 2).max(1);
    let mut value = segmented_time(&input.local_value(), input.pos, focused);
    if show_now {
        let now = Style::new()
            .fg(act(Role::Active))
            .bold(true)
            .render("[NOW]");
        let plain_w = (value_w - style::width(&now) as i64 - 1).max(1);
        if style::width(&value) as i64 > plain_w {
            value = reflow_truncate(&value, plain_w as usize, "");
        }
        let pad = (value_w - style::width(&value) as i64 - style::width(&now) as i64).max(1);
        value = format!("{value}{}{now}", " ".repeat(pad as usize));
    }
    if style::width(&value) as i64 > value_w {
        value = reflow_truncate(&value, value_w as usize, "");
    }
    let pad = (value_w - style::width(&value) as i64).max(0) as usize;
    let middle = format!(
        "{} {value}{} {}",
        border.render("│"),
        " ".repeat(pad),
        border.render("│")
    );
    let bottom = border.render(&format!("└{}┘", "─".repeat((width - 2).max(0) as usize)));
    vec![top, middle, bottom]
}

fn segmented_time(value: &str, cursor: usize, focused: bool) -> String {
    let normal = Style::new().fg(act(Role::Body));
    if !focused || value.is_empty() {
        return normal.render(value);
    }
    let runes: Vec<char> = value.chars().collect();
    let cursor = cursor.min(runes.len() - 1);
    let (start, end) = time_segment_bounds(&runes, cursor);
    let active = Style::new()
        .bg(act(Role::Active))
        .fg(act(Role::InvertText))
        .bold(true);
    let piece = |a: usize, b: usize| runes[a..b].iter().collect::<String>();
    format!(
        "{}{}{}",
        normal.render(&piece(0, start)),
        active.render(&piece(start, end)),
        normal.render(&piece(end, runes.len()))
    )
}

fn time_segment_bounds(runes: &[char], cursor: usize) -> (usize, usize) {
    if runes.is_empty() {
        return (0, 0);
    }
    let segment_char = |c: char| c.is_ascii_alphanumeric();
    let same = |a: char, b: char| {
        (a.is_ascii_digit() && b.is_ascii_digit())
            || (a.is_ascii_alphabetic() && b.is_ascii_alphabetic())
    };
    let mut cursor = cursor.min(runes.len() - 1);
    if !segment_char(runes[cursor]) {
        if cursor + 1 < runes.len() && segment_char(runes[cursor + 1]) {
            cursor += 1;
        } else if cursor > 0 && segment_char(runes[cursor - 1]) {
            cursor -= 1;
        }
    }
    let mut start = cursor;
    while start > 0 && same(runes[start - 1], runes[cursor]) {
        start -= 1;
    }
    let mut end = cursor + 1;
    while end < runes.len() && same(runes[end], runes[cursor]) {
        end += 1;
    }
    (start, end)
}

fn time_picker_join(left: &str, right: &str, left_w: i64, gap_w: i64, right_w: i64) -> String {
    let mut left = left.to_owned();
    let mut right = right.to_owned();
    if style::width(&left) as i64 > left_w {
        left = reflow_truncate(&left, left_w as usize, "");
    }
    if style::width(&right) as i64 > right_w {
        right = reflow_truncate(&right, right_w as usize, "");
    }
    let left_pad = (left_w - style::width(&left) as i64).max(0) + gap_w;
    let right_pad = (right_w - style::width(&right) as i64).max(0);
    format!(
        "{left}{}{right}{}",
        " ".repeat(left_pad as usize),
        " ".repeat(right_pad as usize)
    )
}

// ---------------------------------------------------------------------------
// SQL text helpers of pkg/model/query.go (distinct from cmd/query.go's)
// ---------------------------------------------------------------------------

fn is_ident_char(c: u8) -> bool {
    c.is_ascii_alphanumeric() || c == b'_'
}
fn is_sql_identifier_start(c: u8) -> bool {
    c.is_ascii_alphabetic() || c == b'_'
}
fn is_sql_space(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\n' | b'\r')
}

/// `strings.Index(strings.ToLower(s), needle)`: a byte offset into the
/// lowered text, which query.go then applies to the ORIGINAL bytes. Letters
/// whose lowercase has another UTF-8 length (KELVIN SIGN 3→1, 'İ' 2→1,
/// 'Ⱥ' 2→3) shift that offset, exactly as in Go.
fn go_lower_index(s: &str, needle: &str) -> Option<usize> {
    super::unicode::lower(s).find(needle)
}

/// Go `s[low:]`, including its runtime panic text when `low` is past the end.
fn go_slice_from(bytes: &[u8], low: usize) -> Result<&[u8], GoPanic> {
    bytes.get(low..).ok_or_else(|| {
        GoPanic(format!(
            "runtime error: slice bounds out of range [{low}:{}]",
            bytes.len()
        ))
    })
}

/// strings.TrimSpace on bytes that may start or end inside a rune: invalid
/// bytes are not space and stop trimming on that side.
fn go_trim_space(mut bytes: &[u8]) -> &[u8] {
    fn first(bytes: &[u8]) -> Option<char> {
        let head = &bytes[..bytes.len().min(4)];
        let valid = match std::str::from_utf8(head) {
            Ok(text) => text,
            Err(error) => std::str::from_utf8(&head[..error.valid_up_to()]).ok()?,
        };
        valid.chars().next()
    }
    fn last(bytes: &[u8]) -> Option<char> {
        let start = bytes.len().saturating_sub(4);
        (start..bytes.len()).find_map(|index| {
            let text = std::str::from_utf8(&bytes[index..]).ok()?;
            let mut chars = text.chars();
            let c = chars.next()?;
            chars.next().is_none().then_some(c)
        })
    }
    while let Some(c) = first(bytes).filter(|c| c.is_whitespace()) {
        bytes = &bytes[c.len_utf8()..];
    }
    while let Some(c) = last(bytes).filter(|c| c.is_whitespace()) {
        bytes = &bytes[..bytes.len() - c.len_utf8()];
    }
    bytes
}

/// extractDataset as display text: invalid bytes become one U+FFFD each.
pub(crate) fn extract_dataset(sql: &str) -> Result<String, GoPanic> {
    extract_dataset_bytes(sql).map(|bytes| crate::error::go_string(&bytes))
}

/// extractDataset's exact Go bytes, which can start or end inside a rune.
fn extract_dataset_bytes(sql: &str) -> Result<Vec<u8>, GoPanic> {
    let index = go_lower_index(sql, " from ").or_else(|| go_lower_index(sql, "\nfrom "));
    let Some(index) = index else {
        return Ok("—".as_bytes().to_vec());
    };
    let rest = go_trim_space(go_slice_from(sql.as_bytes(), index + 6)?);
    for quote in [b'"', b'\''] {
        if rest.first() == Some(&quote) {
            let mut out = Vec::new();
            let mut i = 1;
            while i < rest.len() {
                if rest[i] == quote {
                    if i + 1 < rest.len() && rest[i + 1] == quote {
                        out.push(quote);
                        i += 2;
                        continue;
                    }
                    return Ok(out);
                }
                out.push(rest[i]);
                i += 1;
            }
            return Ok(rest[1..].to_vec());
        }
    }
    if let Some(end) = rest.iter().position(|b| b" ,;\n\t)".contains(b))
        && end > 0
    {
        return Ok(rest[..end].to_vec());
    }
    Ok(rest.to_vec())
}

/// resolveDatasetPlaceholder: `FROM dataset` becomes `FROM '<selected>'`,
/// with Go's lowered-offset slicing of the original bytes.
pub(crate) fn resolve_dataset_placeholder(query: &str, dataset: &str) -> Result<String, GoPanic> {
    if dataset.is_empty() {
        return Ok(query.to_owned());
    }
    let Some(index) = go_lower_index(query, " from ") else {
        return Ok(query.to_owned());
    };
    let bytes = query.as_bytes();
    let rest = go_trim_space(go_slice_from(bytes, index + 6)?);
    // strings.ToLower(rest) has prefix "dataset" only when rest's first seven
    // bytes are ASCII letters of "dataset" (no other rune lowers into them).
    if rest.len() >= 7 && rest[..7].eq_ignore_ascii_case(b"dataset") {
        let after = &rest[7..];
        if after.is_empty() || !is_ident_char(after[0]) {
            let mut out = bytes[..index + 6].to_vec();
            out.push(b'\'');
            out.extend_from_slice(dataset.as_bytes());
            out.push(b'\'');
            out.extend_from_slice(after);
            return Ok(crate::error::go_string(&out));
        }
    }
    Ok(query.to_owned())
}

/// The panicking calls NewQueryModel makes before tea.Program.Run: Go has no
/// recover there, so a panic is an ordinary unrecovered runtime panic.
pub(crate) fn new_model_preflight(query: &str) -> Result<(), GoPanic> {
    if query.trim().is_empty() {
        return Ok(());
    }
    let extracted = extract_dataset(query)?;
    let dataset = initial_dataset(&extracted);
    resolve_dataset_placeholder(query, &dataset).map(drop)
}

/// A dataset name with its Go bytes when they are not valid UTF-8 (only
/// extractDataset can produce those). `text` shows one U+FFFD per invalid
/// byte; effects that put the name on the wire use the raw bytes.
fn split_name(bytes: Vec<u8>) -> (String, Option<Vec<u8>>) {
    match String::from_utf8(bytes) {
        Ok(text) => (text, None),
        Err(error) => {
            let bytes = error.into_bytes();
            (crate::error::go_string(&bytes), Some(bytes))
        }
    }
}

/// NewQueryModel's initial dataset from the extracted FROM target.
fn initial_dataset(extracted: &str) -> String {
    if extracted != "—" && !extracted.is_empty() && !extracted.eq_ignore_ascii_case("dataset") {
        extracted.to_owned()
    } else {
        String::new()
    }
}

const SQL_FIELD_KEYWORDS: [&str; 37] = [
    "all", "and", "as", "asc", "between", "by", "case", "cast", "desc", "distinct", "else", "end",
    "false", "from", "full", "group", "having", "in", "inner", "is", "join", "left", "like",
    "limit", "not", "null", "on", "or", "order", "outer", "right", "select", "then", "true",
    "when", "where", "",
];

fn quoted_copy_end(bytes: &[u8], start: usize) -> usize {
    let quote = bytes[start];
    let mut i = start + 1;
    while i < bytes.len() {
        let c = bytes[i];
        i += 1;
        if c == quote {
            if i < bytes.len() && bytes[i] == quote {
                i += 1;
                continue;
            }
            break;
        }
    }
    i
}

/// quoteUnsafeSQLFieldNames: like cmd's pass, but quoted identifiers honor
/// doubled `""` escapes (copySQLQuotedIdentifier).
pub(crate) fn quote_unsafe_sql_field_names(query: &str) -> String {
    let bytes = query.as_bytes();
    let n = bytes.len();
    let mut out = Vec::with_capacity(n);
    let mut i = 0;
    while i < n {
        let c = bytes[i];
        if c == b'\'' || c == b'"' {
            let end = quoted_copy_end(bytes, i);
            out.extend_from_slice(&bytes[i..end]);
            i = end;
            continue;
        }
        if is_sql_identifier_start(c) {
            let mut j = i + 1;
            while j < n && is_ident_char(bytes[j]) {
                j += 1;
            }
            let mut k = j;
            let mut has_dot = false;
            while k < n && bytes[k] == b'.' && k + 1 < n && is_ident_char(bytes[k + 1]) {
                has_dot = true;
                k += 1;
                while k < n && is_ident_char(bytes[k]) {
                    k += 1;
                }
            }
            let identifier = &bytes[i..k];
            if has_dot || should_quote_field(identifier, bytes, k) {
                out.push(b'"');
                out.extend_from_slice(identifier);
                out.push(b'"');
                i = k;
            } else {
                out.extend_from_slice(&bytes[i..j]);
                i = j;
            }
            continue;
        }
        out.push(c);
        i += 1;
    }
    String::from_utf8(out).expect("only ASCII inserted at UTF-8 boundaries")
}

fn should_quote_field(identifier: &[u8], query: &[u8], end: usize) -> bool {
    let lower = identifier.to_ascii_lowercase();
    if SQL_FIELD_KEYWORDS
        .iter()
        .any(|k| !k.is_empty() && k.as_bytes() == lower.as_slice())
    {
        return false;
    }
    if query[end..].iter().find(|b| !is_sql_space(**b)) == Some(&b'(') {
        return false;
    }
    identifier.iter().any(u8::is_ascii_uppercase) && identifier.iter().any(u8::is_ascii_lowercase)
}

/// fetchData's request preparation (schema fallback path).
pub(crate) fn prepare_fetch_query(query: &str) -> String {
    rewrite::ensure_default_limit(&quote_unsafe_sql_field_names(&rewrite::quote_stream_names(
        query,
    )))
}

fn keyword_at(bytes: &[u8], index: usize, keyword: &[u8]) -> bool {
    let end = index + keyword.len();
    if end > bytes.len() || !bytes[index..end].eq_ignore_ascii_case(keyword) {
        return false;
    }
    if index > 0 && is_ident_char(bytes[index - 1]) {
        return false;
    }
    end >= bytes.len() || !is_ident_char(bytes[end])
}

/// findTopLevelSQLLimitOffset.
fn find_top_level_limit_offset(query: &str) -> (Option<usize>, Option<usize>) {
    let bytes = query.as_bytes();
    let (mut limit, mut offset) = (None, None);
    let mut depth = 0usize;
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'\'' | b'"' => i = quoted_copy_end(bytes, i),
            b'-' if bytes.get(i + 1) == Some(&b'-') => {
                i += 2;
                while i < bytes.len() && bytes[i] != b'\n' {
                    i += 1;
                }
            }
            b'/' if bytes.get(i + 1) == Some(&b'*') => {
                i += 2;
                while i + 1 < bytes.len() {
                    if bytes[i] == b'*' && bytes[i + 1] == b'/' {
                        i += 2;
                        break;
                    }
                    i += 1;
                }
            }
            b'(' => {
                depth += 1;
                i += 1;
            }
            b')' => {
                depth = depth.saturating_sub(1);
                i += 1;
            }
            _ => {
                if depth == 0 {
                    if limit.is_none() && keyword_at(bytes, i, b"limit") {
                        limit = Some(i);
                        i += 5;
                        continue;
                    }
                    if offset.is_none() && keyword_at(bytes, i, b"offset") {
                        offset = Some(i);
                        i += 6;
                        continue;
                    }
                }
                i += 1;
            }
        }
    }
    (limit, offset)
}

/// readSQLClauseInt: digits after optional SQL spaces; -1 when absent or
/// outside Go's int range.
fn read_clause_int(query: &str, mut pos: usize) -> i64 {
    let bytes = query.as_bytes();
    while pos < bytes.len() && is_sql_space(bytes[pos]) {
        pos += 1;
    }
    let start = pos;
    while pos < bytes.len() && bytes[pos].is_ascii_digit() {
        pos += 1;
    }
    if start == pos {
        return -1;
    }
    query[start..pos].parse().unwrap_or(-1)
}

fn trim_go_space(s: &str) -> &str {
    s.trim()
}

/// stripTopLevelSQLLimitOffset.
fn strip_top_level_limit_offset(query: &str) -> (String, i64, i64) {
    let (limit_pos, offset_pos) = find_top_level_limit_offset(query);
    let mut limit = -1;
    let mut offset = 0;
    let mut strip = None;
    if let Some(pos) = limit_pos {
        strip = Some(pos);
        limit = read_clause_int(query, pos + 5);
    }
    if let Some(pos) = offset_pos {
        if strip.is_none_or(|s| pos < s) {
            strip = Some(pos);
        }
        let parsed = read_clause_int(query, pos + 6);
        if parsed >= 0 {
            offset = parsed;
        }
    }
    let Some(strip) = strip else {
        let base = trim_go_space(
            trim_go_space(query)
                .strip_suffix(';')
                .unwrap_or(trim_go_space(query)),
        );
        return (base.to_owned(), limit, offset);
    };
    let had_suffix = trim_go_space(query).ends_with(';');
    let mut base = trim_go_space(&query[..strip]);
    base = trim_go_space(base.strip_suffix(';').unwrap_or(base));
    if had_suffix {
        base = trim_go_space(base.strip_suffix(';').unwrap_or(base));
    }
    (base.to_owned(), limit, offset)
}

#[derive(Clone, Debug, PartialEq)]
struct QueryPlan {
    base: String,
    limit: i64,
    offset: i64,
}

/// buildSQLQueryPlan.
fn build_query_plan(query: &str, default_limit: i64) -> QueryPlan {
    let (mut base, mut limit, offset) = strip_top_level_limit_offset(query);
    if trim_go_space(&base).is_empty() {
        base = trim_go_space(query).to_owned();
    }
    if limit < 0 {
        limit = default_limit;
    }
    QueryPlan {
        base,
        limit,
        offset,
    }
}

/// injectSQLWindow.
fn inject_sql_window(query: &str, limit: i64, offset: i64) -> String {
    let (base, _, _) = strip_top_level_limit_offset(query);
    let trimmed = trim_go_space(&base);
    let base = trim_go_space(trimmed.strip_suffix(';').unwrap_or(trimmed));
    format!("{base} LIMIT {limit} OFFSET {}", offset.max(0))
}

/// parseTimestamp: RFC3339(Nano), then zone-less layouts read as UTC.
fn parse_timestamp(value: &str) -> Option<DateTime<FixedOffset>> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    if value.as_bytes().get(10) == Some(&b'T')
        && let Ok(t) = DateTime::parse_from_rfc3339(value)
    {
        return Some(t);
    }
    for layout in ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%d %H:%M:%S%.f"] {
        if let Ok(naive) = NaiveDateTime::parse_from_str(value, layout) {
            return Some(naive.and_utc().fixed_offset());
        }
    }
    None
}

/// trimTimestampToHMS.
fn trim_timestamp_to_hms(value: &str) -> String {
    let Some(t) = value.find('T').or_else(|| value.find(' ')) else {
        return value.to_owned();
    };
    if t + 1 >= value.len() {
        return value.to_owned();
    }
    let rest = &value[t + 1..];
    if let Some(i) = rest.find(['.', 'Z', '+', '-']) {
        return rest[..i].to_owned();
    }
    if rest.len() >= 8 {
        return String::from_utf8_lossy(&rest.as_bytes()[..8]).into_owned();
    }
    rest.to_owned()
}

/// formatTimestampToDisplayHMS.
fn format_timestamp_hms(value: &str, location: Zone, utc: bool) -> String {
    match parse_timestamp(value) {
        Some(t) => {
            let time = GoTime {
                instant: t.with_timezone(&Utc),
                zone: if utc { Zone::Utc } else { location },
            };
            time.format("%H:%M:%S")
        }
        None => trim_timestamp_to_hms(value),
    }
}

fn format_result_time_label(utc: bool) -> &'static str {
    if utc { "[UTC]" } else { "[LOCAL]" }
}

/// formatSQLControlTime.
fn format_control_time(time: GoTime, utc: bool) -> String {
    if utc {
        GoTime {
            instant: time.instant,
            zone: Zone::Utc,
        }
        .format("%d %b %Y, %H:%M | UTC")
    } else {
        time.format("%d %b %Y, %H:%M | UTC%:z")
    }
}

fn sql_page_size(total_h: i64, bottom_h: i64) -> i64 {
    const TOP_H: i64 = 12;
    let available = (total_h - TOP_H - bottom_h).max(6);
    let inner = (available - 3).max(3);
    (inner - 5).max(1)
}

// ---------------------------------------------------------------------------
// bubble-table v0.19.2 subset with SQL's custom border
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
struct Column {
    key: String,
    title: String,
    width: i64,
    filterable: bool,
}

type Row = BTreeMap<String, Cell>;

#[derive(Clone)]
struct Table {
    columns: Vec<Column>,
    rows: Vec<Row>,
    focused: bool,
    /// The filter input is never displayed (footer hidden) and, with
    /// FilterBlur unbound, keeps focus once '/' is pressed.
    filter: TextInput,
    cursor: i64,
    page: i64,
    page_size: i64,
    max_total_width: i64,
    scroll: i64,
    max_scroll: i64,
}

#[derive(Clone, Copy, PartialEq)]
enum Part {
    Left,
    Inner,
    Right,
}

/// Edge strings of one lipgloss bordered cell: (top l,m,r), left, right,
/// (bottom l,m,r).
struct Edges {
    top: Option<(&'static str, &'static str, &'static str)>,
    left: &'static str,
    right: &'static str,
    bottom: Option<(&'static str, &'static str, &'static str)>,
}

impl Table {
    fn new(page_size: i64, max_total_width: i64) -> Self {
        let mut filter = TextInput::new();
        filter.set_prompt("/");
        let mut table = Self {
            columns: vec![Column {
                key: "Id".into(),
                title: "Id".into(),
                width: 5,
                filterable: false,
            }],
            rows: Vec::new(),
            focused: false,
            filter,
            cursor: 0,
            page: 0,
            page_size,
            max_total_width,
            scroll: 0,
            max_scroll: 0,
        };
        table.recalculate();
        table
    }
    fn visible_rows(&self) -> Vec<&Row> {
        let filter = self.filter.value();
        if filter.is_empty() {
            return self.rows.iter().collect();
        }
        let needle = super::unicode::lower(&filter);
        self.rows
            .iter()
            .filter(|row| {
                let mut checked = false;
                for column in self.columns.iter().filter(|c| c.filterable) {
                    checked = true;
                    if let Some(cell) = row.get(&column.key)
                        && super::unicode::lower(cell.display()).contains(&needle)
                    {
                        return true;
                    }
                }
                !checked
            })
            .collect()
    }
    fn max_pages(&self) -> i64 {
        let total = self.visible_rows().len() as i64;
        if self.page_size == 0 || total == 0 {
            1
        } else {
            (total - 1) / self.page_size + 1
        }
    }
    fn expected_page(&self, index: i64) -> i64 {
        if self.page_size == 0 {
            0
        } else {
            index / self.page_size
        }
    }
    fn page_first(&mut self) {
        self.page = 0;
        self.cursor = 0;
    }
    fn page_last(&mut self) {
        self.page = self.max_pages() - 1;
        self.cursor = self.page * self.page_size;
    }
    fn set_rows(&mut self, rows: Vec<Row>) {
        self.rows = rows;
        if self.cursor >= self.rows.len() as i64 {
            self.cursor = self.rows.len() as i64 - 1;
        }
        if self.cursor < 0 {
            self.cursor = 0;
        }
        if self.page_size != 0 && self.max_pages() <= self.page {
            self.page_last();
        }
    }
    fn set_highlighted(&mut self, index: i64) {
        let visible = self.visible_rows().len() as i64;
        self.cursor = index.min(visible - 1).max(0);
        self.page = self.expected_page(self.cursor);
    }
    fn set_page_size(&mut self, size: i64) {
        self.page_size = size;
        let max = self.max_pages();
        if self.page >= max {
            self.page = max - 1;
        }
    }
    fn set_columns(&mut self, columns: Vec<Column>) {
        self.columns = columns;
        self.recalculate();
    }
    fn set_max_total_width(&mut self, width: i64) {
        self.max_total_width = width;
        self.recalculate();
    }
    /// recalculateWidth + recalculateLastHorizontalColumn (no freeze).
    fn recalculate(&mut self) {
        let total: i64 =
            self.columns.iter().map(|c| c.width).sum::<i64>() + self.columns.len() as i64 + 1;
        if self.columns.is_empty() || total <= self.max_total_width {
            self.max_scroll = 0;
            return;
        }
        let mut visible = 1 + 2;
        self.max_scroll = self.columns.len() as i64 - 1;
        let mut i = self.columns.len() as i64 - 1;
        while i >= 0 && visible <= self.max_total_width {
            visible += self.columns[i as usize].width + 1;
            if visible <= self.max_total_width {
                self.max_scroll = i;
            }
            i -= 1;
        }
    }
    /// table.Update for a focused table.
    fn update(&mut self, key: &Key) {
        if !self.focused {
            return;
        }
        if self.filter.focused() {
            self.filter.update(key);
            self.page_first();
            return;
        }
        let visible = self.visible_rows().len() as i64;
        if key.is(&["down", "s"]) {
            self.cursor += 1;
            if self.cursor >= visible {
                self.cursor = 0;
            }
            self.page = self.expected_page(self.cursor);
        }
        if key.is(&["up", "w"]) {
            self.cursor -= 1;
            if self.cursor < 0 {
                self.cursor = visible - 1;
            }
            self.page = self.expected_page(self.cursor);
        }
        if key.is(&["shift+down", "S", "pgdown"]) && self.page_size != 0 && visible > self.page_size
        {
            self.page += 1;
            if self.page > self.max_pages() - 1 {
                self.page = 0;
            }
            self.cursor = self.page * self.page_size;
        }
        if key.is(&["shift+up", "W", "pgup"]) && self.page_size != 0 && visible > self.page_size {
            self.page -= 1;
            if self.page < 0 {
                self.page = self.max_pages() - 1;
            }
            self.cursor = self.page * self.page_size;
        }
        if key.is(&["home", "ctrl+y"]) {
            self.page_first();
        }
        if key.is(&["end", "ctrl+v"]) {
            self.page_last();
        }
        if key.is(&["/"]) {
            self.filter.focus();
            self.filter.disarm_blink();
        }
        if key.is(&["esc"]) {
            self.filter.reset();
        }
        if key.is(&["right", "d"]) && self.scroll < self.max_scroll {
            self.scroll += 1;
        }
        if key.is(&["left", "a"]) && self.scroll > 0 {
            self.scroll -= 1;
        }
    }

    fn header_edges(&self, part: Part) -> Edges {
        let has_rows = !self.visible_rows().is_empty();
        if self.columns.len() == 1 {
            let bottom = if has_rows {
                (" ", "─", " ")
            } else {
                ("─", "─", "─")
            };
            return Edges {
                top: Some((" ", " ", " ")),
                left: " ",
                right: " ",
                bottom: Some(bottom),
            };
        }
        match part {
            Part::Left => Edges {
                top: Some((" ", " ", " ")),
                left: " ",
                right: "│",
                bottom: Some(if has_rows {
                    (" ", "─", "─")
                } else {
                    ("─", "─", "─")
                }),
            },
            Part::Inner => Edges {
                top: Some(("", " ", " ")),
                left: "",
                right: "│",
                bottom: Some(("", "─", "─")),
            },
            Part::Right => Edges {
                top: Some(("", " ", " ")),
                left: "",
                right: " ",
                bottom: Some(if has_rows {
                    ("", "─", " ")
                } else {
                    ("", "─", "─")
                }),
            },
        }
    }
    fn row_edges(&self, part: Part, last: bool) -> Edges {
        if self.columns.len() == 1 {
            return Edges {
                top: None,
                left: " ",
                right: " ",
                bottom: last.then_some(("─", "─", "─")),
            };
        }
        let (left, right, bottom) = match part {
            Part::Left => (" ", "│", ("─", "─", "─")),
            Part::Inner => ("", "│", ("", "─", "─")),
            Part::Right => ("", " ", ("", "─", "─")),
        };
        Edges {
            top: None,
            left,
            right,
            bottom: last.then_some(bottom),
        }
    }

    /// Render one bordered cell block (lines) around styled `content`.
    fn block(edges: &Edges, content: &str) -> Vec<String> {
        let width = style::width(content);
        let mut lines = Vec::new();
        let edge = |(l, m, r): (&str, &str, &str)| format!("{l}{}{r}", m.repeat(width));
        if let Some(top) = edges.top {
            lines.push(edge(top));
        }
        lines.push(format!("{}{content}{}", edges.left, edges.right));
        if let Some(bottom) = edges.bottom {
            lines.push(edge(bottom));
        }
        lines
    }

    /// Shared column walk of renderHeaders / renderRowData: the left
    /// overflow marker, scrolled-out columns and right overflow cut.
    fn render_line(
        &self,
        edges_for: impl Fn(Part) -> Edges,
        content_for: impl Fn(&Column, Option<i64>) -> String,
    ) -> String {
        let mut blocks: Vec<Vec<String>> = Vec::new();
        let mut total = 0i64;
        let count = self.columns.len();
        for (index, column) in self.columns.iter().enumerate() {
            if self.scroll > 0 && index == 0 {
                let marker = Column {
                    key: "___overflow_l__".into(),
                    title: "<".into(),
                    width: 1,
                    filterable: false,
                };
                let block = Self::block(&edges_for(Part::Left), &content_for(&marker, None));
                total += block.iter().map(|l| style::width(l)).max().unwrap_or(0) as i64;
                blocks.push(block);
            }
            if (index as i64) < self.scroll {
                continue;
            }
            let part = if blocks.is_empty() {
                Part::Left
            } else if index < count - 1 {
                Part::Inner
            } else {
                Part::Right
            };
            let block = Self::block(&edges_for(part), &content_for(column, None));
            if self.max_total_width != 0 {
                let rendered = block.iter().map(|l| style::width(l)).max().unwrap_or(0) as i64;
                let target = if index == count - 1 {
                    self.max_total_width
                } else {
                    self.max_total_width - 2
                };
                if total + rendered > target {
                    let overflow = self.max_total_width - total - 1;
                    let marker = Column {
                        key: "___overflow_r___".into(),
                        title: ">".into(),
                        width: overflow,
                        filterable: false,
                    };
                    blocks.push(Self::block(
                        &edges_for(Part::Right),
                        &content_for(&marker, Some(overflow)),
                    ));
                    break;
                }
                total += rendered;
            }
            blocks.push(block);
        }
        let height = blocks.iter().map(Vec::len).max().unwrap_or(0);
        (0..height)
            .map(|line| {
                blocks
                    .iter()
                    .map(|block| {
                        // JoinHorizontal(Bottom): pad shorter blocks at the top.
                        let skip = height - block.len();
                        if line < skip {
                            " ".repeat(style::width(&block[0]))
                        } else {
                            block[line - skip].clone()
                        }
                    })
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn render_headers(&self) -> String {
        self.render_line(
            |part| self.header_edges(part),
            |column, overflow| {
                let style = Style::new().fg(ad(Role::Accent)).bold(true);
                cell_content(&style, &column.title, column.width, overflow)
            },
        )
    }

    fn render_row(&self, row: &Row, highlighted: bool, last: bool) -> String {
        self.render_line(
            |part| self.row_edges(part, last),
            |column, overflow| {
                let mut style = Style::new().fg(ad(Role::Body));
                if highlighted {
                    style = style.bg(ad(Role::SelRow)).fg(ad(Role::Accent)).bold(true);
                }
                let text = match column.key.as_str() {
                    "___overflow_r___" => ">".to_owned(),
                    "___overflow_l__" => "<".to_owned(),
                    key => match row.get(key) {
                        Some(cell) => cell.display().to_owned(),
                        None => {
                            // WithMissingDataIndicatorStyled: Border fg wins.
                            style = style.fg(ad(Role::Border));
                            "—".to_owned()
                        }
                    },
                };
                cell_content(&style, &text, column.width, overflow)
            },
        )
    }

    /// table.View().
    fn view(&self) -> String {
        if self.columns.is_empty() {
            return String::new();
        }
        let visible = self.visible_rows();
        let total = visible.len() as i64;
        let start = self.page_size * self.page;
        let end = (start + self.page_size - 1).min(total - 1);
        let mut parts = vec![self.render_headers()];
        let mut index = start;
        while index <= end {
            let row = visible[index as usize];
            let highlighted = self.focused && index == self.cursor;
            parts.push(self.render_row(row, highlighted, index == end));
            index += 1;
        }
        style::join_vertical(0.0, &parts.iter().map(String::as_str).collect::<Vec<_>>())
    }
}

/// renderRowColumnData content: limitStr then the column's Width; overflow
/// markers right-align inside their border width.
fn cell_content(style: &Style, text: &str, width: i64, overflow: Option<i64>) -> String {
    match overflow {
        Some(w) if w > 0 => (*style)
            .width(w as usize)
            .align(Align::Right)
            .render(&limit_str(text, w)),
        Some(0) => style.render(""),
        Some(_) => style.render(text),
        None if text == "<" && width == 1 => style.render("<"),
        None => {
            if width <= 0 {
                return style.render("");
            }
            (*style)
                .width(width as usize)
                .render(&limit_str(text, width))
        }
    }
}

// reflow_width / reflow_truncate port github.com/muesli/reflow v0.3.0
// (ansi/ansi.go, ansi/buffer.go, ansi/writer.go, truncate/truncate.go):
//
// MIT License
//
// Copyright (c) 2019 Christian Muehlhaeuser
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.

/// reflow ansi.IsTerminator: '@'..='Z' or 'a'..='z'.
fn is_reflow_terminator(c: char) -> bool {
    matches!(c, '\u{40}'..='\u{5a}' | '\u{61}'..='\u{7a}')
}

/// reflow ansi.PrintableRuneWidth: go-runewidth widths; from ESC up to a
/// terminator nothing counts.
pub(crate) fn reflow_width(text: &str) -> usize {
    let mut width = 0;
    let mut in_sequence = false;
    for c in text.chars() {
        if c == '\x1b' {
            in_sequence = true;
        } else if in_sequence {
            if is_reflow_terminator(c) {
                in_sequence = false;
            }
        } else {
            width += style::bubbles_rune_width(c);
        }
    }
    width
}

/// reflow truncate.StringWithTail (`truncate.String` is `tail = ""`), the
/// clipper of query.go paneBodyLines, timeinput.go and bubble-table limitStr.
///
/// Unlike x/ansi Truncate it stops at the first rune that overflows and drops
/// everything after it, so zero-width runes such as ui.Truncate's NUL fill
/// never reach the terminal. Escape bytes are buffered by ansi.Writer until a
/// terminator: an unterminated sequence is never emitted, and a nested ESC
/// extends the open sequence. After the tail, "\x1b[0m" follows only when a
/// non-reset SGR was recorded and no reset has been seen since (ResetAnsi).
pub(crate) fn reflow_truncate(text: &str, width: usize, tail: &str) -> String {
    let tail_width = reflow_width(tail);
    if width < tail_width {
        return tail.to_owned();
    }
    let width = width - tail_width;
    let mut out = String::new();
    // truncate.Writer state.
    let mut current = 0;
    let mut in_sequence = false;
    // ansi.Writer state.
    let mut writer_in_sequence = false;
    let mut sequence = String::new();
    // Only whether a non-reset SGR remains active affects the trailing reset;
    // retain no redundant history of every prior escape sequence.
    let mut open_style = false;
    for c in text.chars() {
        if c == '\x1b' {
            in_sequence = true;
        } else if in_sequence {
            if is_reflow_terminator(c) {
                in_sequence = false;
            }
        } else {
            current += style::bubbles_rune_width(c);
        }
        if current > width {
            out.push_str(tail);
            if open_style {
                out.push_str("\x1b[0m");
            }
            return out;
        }
        if c == '\x1b' {
            writer_in_sequence = true;
            sequence.push(c);
        } else if writer_in_sequence {
            sequence.push(c);
            if is_reflow_terminator(c) {
                writer_in_sequence = false;
                if sequence.ends_with("[0m") {
                    open_style = false;
                } else if c == 'm' {
                    open_style = true;
                }
                out.push_str(&sequence);
                sequence.clear();
            }
        } else {
            out.push(c);
        }
    }
    out
}

/// bubble-table limitStr.
fn limit_str(text: &str, max: i64) -> String {
    if max == 0 {
        return String::new();
    }
    let text = match text.find('\n') {
        Some(i) => format!("{}…", &text[..i]),
        None => text.to_owned(),
    };
    if max > 0 && reflow_width(&text) as i64 > max {
        return reflow_truncate(&text, max as usize, "…");
    }
    text
}

/// Go panics raised while rendering; the first one is reported after the
/// view returns (Bubble Tea's Run-level recover covers View).
#[derive(Debug, Default)]
pub(crate) struct RenderPanics(std::cell::RefCell<Option<String>>);

impl RenderPanics {
    fn record(&self, panic: GoPanic) {
        let mut slot = self.0.borrow_mut();
        if slot.is_none() {
            *slot = Some(panic.to_string());
        }
    }
    fn take(&self) -> Option<String> {
        self.0.borrow_mut().take()
    }
}

/// ui.Truncate. Its reachable wide-rune slice panic is recorded and the
/// frame is abandoned by the caller, as Go never finishes that View.
fn ui_truncate(text: &str, n: i64, panics: &RenderPanics) -> String {
    style::ui_truncate(text, n as isize).unwrap_or_else(|panic| {
        panics.record(panic);
        String::new()
    })
}

// ---------------------------------------------------------------------------
// Colors: ui.Active (PB_THEME-aware) versus ui.Adaptive (background-aware)
// ---------------------------------------------------------------------------

/// `p := ui.Active; p.<Role>`.
fn act(role: Role) -> Color {
    Color::hex(style::active().hex(role))
}
/// `ui.Adaptive(func(p) { return p.<Role> })`.
fn ad(role: Role) -> Color {
    Color::role(role)
}

// ---------------------------------------------------------------------------
// The session model
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq)]
enum Focus {
    Query,
    Time,
    Dataset,
    Columns,
    Table,
}
const FOCUS_ORDER: [Focus; 5] = [
    Focus::Query,
    Focus::Time,
    Focus::Dataset,
    Focus::Columns,
    Focus::Table,
];

/// Go's overlayColumn (3) is never assigned in the SQL model; it is not
/// representable here (docs/phase7-sql.md).
#[derive(Clone, Copy, Debug, PartialEq)]
enum Overlay {
    None,
    Time,
    Dataset,
}

#[derive(Clone, Debug)]
/// sqlWindow; Go also stores each window's schema but never reads it.
struct Window {
    data: Vec<Record>,
    last_used: u64,
}

#[derive(Clone, Debug, Default)]
struct Status {
    info: String,
    error: String,
}

pub(crate) struct QueryState {
    width: i64,
    height: i64,
    table: Table,
    editor: TextArea,
    time: TimeInput,
    time_edit: TimeInput,
    profile_url: String,
    status: Status,
    spinner_frame: usize,
    spinner_tag: u64,
    loading: bool,
    has_queried: bool,
    overlay: Overlay,
    focused: usize,
    data_rows: Vec<Row>,
    fetch_error: String,
    table_rows_base: i64,

    window_size: i64,
    global_offset: i64,
    viewport_start: i64,
    query_limit: i64,
    query_base_offset: i64,
    actual_total: i64,
    base_query: String,
    run_id: u64,
    locked_start_utc: String,
    locked_end_utc: String,
    windows: BTreeMap<i64, Window>,
    loading_windows: BTreeMap<i64, ()>,
    cache_clock: u64,
    window_fetch_error: String,

    dataset: String,
    /// The Go bytes of `dataset` when they are not valid UTF-8 (L-SQL-4).
    dataset_raw: Option<Vec<u8>>,
    spotlight_filter: TextInput,
    all_datasets: Vec<String>,
    filtered_datasets: Vec<String>,
    dataset_index: usize,
    datasets_loading: bool,

    selected_column: String,
    column_filter: TextInput,
    all_columns: Vec<String>,
    filtered_columns: Vec<String>,
    column_index: i64,
    columns_loading: bool,
    columns_drawer_open: bool,

    schema: Vec<String>,
    /// A Go panic reached while rendering (editor views run on copies).
    render_fault: StdCell<Option<EditorFault>>,
    render_panics: RenderPanics,
}

fn new_filter_input(placeholder: &str) -> TextInput {
    let mut input = TextInput::new();
    input.set_placeholder(placeholder);
    input.set_prompt("> ");
    input.set_width((SPOTLIGHT_WIDTH - 8) as usize);
    input.blur();
    input
}

fn filter_input_styles() -> InputStyles {
    InputStyles {
        prompt: Style::new().fg(ad(Role::Accent)).bold(true),
        placeholder: Style::new().fg(ad(Role::Ghost)).italic(true),
        ..InputStyles::default()
    }
}

/// filterDatasets: case-insensitive substring, all entries for "".
fn filter_names(all: &[String], query: &str) -> Vec<String> {
    if query.is_empty() {
        return all.to_vec();
    }
    let needle = super::unicode::lower(query);
    all.iter()
        .filter(|name| super::unicode::lower(name).contains(&needle))
        .cloned()
        .collect()
}

impl QueryState {
    /// NewQueryModel. `size` is term.GetSize(stdout) (absent off a TTY).
    pub(crate) fn new(
        profile_url: String,
        query: &str,
        start: GoTime,
        end: GoTime,
        size: Option<(i64, i64)>,
    ) -> Self {
        let (width, height) = size.unwrap_or((0, 0));
        let mut editor = TextArea::new();
        editor.set_max_height(0);
        editor.set_max_width(0);
        editor.set_size(70, 10);
        editor.set_show_line_numbers(true);
        editor.set_end_of_buffer(' ');
        editor.set_value(query);
        editor.set_placeholder(EDITOR_PLACEHOLDER);
        editor.set_keymap(&PB_AREA_KEYS);
        editor.set_prompt("  ");
        // NewQueryModel discards query.Focus()'s blink command.
        editor.focus();
        editor.disarm_blink();
        let has_query = !query.trim().is_empty();
        let mut dataset = String::new();
        let mut dataset_raw = None;
        let mut construction_panic = None;
        if has_query {
            // execute() runs new_model_preflight first, so a Go panic here is
            // only recorded for direct (test) construction.
            match extract_dataset_bytes(query) {
                Ok(bytes) => {
                    let (text, raw) = split_name(bytes);
                    dataset = initial_dataset(&text);
                    if !dataset.is_empty() {
                        dataset_raw = raw;
                    }
                }
                Err(panic) => construction_panic = Some(panic),
            }
        }
        let mut state = Self {
            width,
            height,
            table: Table::new(sql_page_size(height, 3).max(5), width),
            editor,
            time: TimeInput::new(start, end),
            time_edit: TimeInput::new(start, end),
            profile_url,
            status: Status::default(),
            spinner_frame: 0,
            spinner_tag: 0,
            loading: has_query,
            has_queried: has_query,
            overlay: Overlay::None,
            focused: 0,
            data_rows: Vec::new(),
            fetch_error: String::new(),
            table_rows_base: 0,
            window_size: SQL_WINDOW_SIZE,
            global_offset: 0,
            viewport_start: 0,
            query_limit: 0,
            query_base_offset: 0,
            actual_total: UNKNOWN_SQL_TOTAL,
            base_query: String::new(),
            run_id: 0,
            locked_start_utc: String::new(),
            locked_end_utc: String::new(),
            windows: BTreeMap::new(),
            loading_windows: BTreeMap::new(),
            cache_clock: 0,
            window_fetch_error: String::new(),
            dataset,
            dataset_raw,
            spotlight_filter: new_filter_input("filter datasets"),
            all_datasets: Vec::new(),
            filtered_datasets: Vec::new(),
            dataset_index: 0,
            datasets_loading: false,
            selected_column: String::new(),
            column_filter: new_filter_input("filter columns"),
            all_columns: Vec::new(),
            filtered_columns: Vec::new(),
            column_index: 0,
            columns_loading: false,
            columns_drawer_open: true,
            schema: Vec::new(),
            render_fault: StdCell::new(None),
            render_panics: RenderPanics::default(),
        };
        if let Some(panic) = construction_panic {
            state.render_panics.record(panic);
        } else if has_query {
            state.prepare_run();
        }
        state
    }

    /// QueryModel.Init: spinner tick, dataset list, and window 0 when a
    /// query was given (tea.Batch: concurrent, unordered).
    pub(crate) fn init(&mut self) -> Vec<Effect> {
        let mut effects = vec![
            Effect::SpinnerTick {
                tag: self.spinner_tag,
                delayed: false,
            },
            Effect::FetchDatasets,
        ];
        if !self.editor.value().trim().is_empty() {
            effects.extend(self.fetch_window(0));
        }
        effects
    }

    fn current_focus(&self) -> Focus {
        FOCUS_ORDER[self.focused.min(FOCUS_ORDER.len() - 1)]
    }

    fn focus_selected(&mut self) {
        self.editor.blur();
        self.table.focused = false;
        self.spotlight_filter.blur();
        self.column_filter.blur();
        // focusSelected discards every Focus() blink command, and the
        // BlinkMsg of later cursor moves has no case in QueryModel.Update:
        // the SQL cursors never blink.
        match self.current_focus() {
            Focus::Query => {
                self.editor.focus();
                self.editor.disarm_blink();
            }
            Focus::Table => self.table.focused = true,
            Focus::Columns => {
                self.column_filter.focus();
                self.column_filter.disarm_blink();
            }
            _ => {}
        }
    }

    /// The current dataset identity as Go holds it, before replacement decoding.
    fn dataset_identity_bytes(&self) -> &[u8] {
        self.dataset_raw
            .as_deref()
            .unwrap_or(self.dataset.as_bytes())
    }

    /// An owned snapshot for schema effects.
    fn dataset_bytes(&self) -> Vec<u8> {
        self.dataset_identity_bytes().to_vec()
    }

    /// The Go panic or hang reached by the last update/view, if any.
    pub(crate) fn take_fault(&self) -> Option<Fault> {
        if let Some(value) = self.render_panics.take() {
            return Some(Fault::Go(value));
        }
        self.render_fault
            .take()
            .or_else(|| self.editor.take_fault())
            .or_else(|| self.spotlight_filter.take_fault())
            .or_else(|| self.column_filter.take_fault())
            .or_else(|| self.table.filter.take_fault())
            .map(Fault::Editor)
    }

    fn focus_pane(&mut self, pane: Focus) {
        self.focused = FOCUS_ORDER.iter().position(|f| *f == pane).unwrap_or(0);
        self.focus_selected();
    }

    fn insert_selected_column(&mut self) {
        if self.filtered_columns.is_empty() {
            return;
        }
        self.column_index = self
            .column_index
            .clamp(0, self.filtered_columns.len() as i64 - 1);
        self.selected_column = self.filtered_columns[self.column_index as usize].clone();
        let escaped = self.selected_column.replace('"', "\"\"");
        self.editor.insert_str(&format!("\"{escaped}\" "));
        self.focus_pane(Focus::Query);
    }

    fn toggle_columns_drawer(&mut self) -> Vec<Effect> {
        self.columns_drawer_open = !self.columns_drawer_open;
        if !self.columns_drawer_open {
            self.focus_pane(Focus::Columns);
            return Vec::new();
        }
        let mut selected = self.dataset_bytes();
        if selected.is_empty() {
            match extract_dataset_bytes(&self.editor.value()) {
                Ok(bytes) if bytes != "—".as_bytes() && !bytes.is_empty() => {
                    selected = bytes.clone();
                    (self.dataset, self.dataset_raw) = split_name(bytes);
                }
                Ok(_) => {}
                // Update panics end the program (recovered by tea.Program.Run).
                Err(panic) => {
                    self.render_panics.record(panic);
                    return Vec::new();
                }
            }
        }
        self.focus_pane(Focus::Columns);
        if !selected.is_empty() && self.all_columns.is_empty() && !self.columns_loading {
            self.columns_loading = true;
            return vec![Effect::FetchSchema(selected)];
        }
        Vec::new()
    }

    /// prepareSQLWindowRun; `false` when Go panics inside it.
    fn prepare_run(&mut self) -> bool {
        let query = match resolve_dataset_placeholder(&self.editor.value(), &self.dataset) {
            Ok(query) => query,
            Err(panic) => {
                self.render_panics.record(panic);
                return false;
            }
        };
        let query = quote_unsafe_sql_field_names(&rewrite::quote_stream_names(&query));
        let plan = build_query_plan(&query, SQL_WINDOW_SIZE);
        self.run_id += 1;
        self.base_query = plan.base;
        self.query_limit = plan.limit;
        self.query_base_offset = plan.offset;
        self.actual_total = UNKNOWN_SQL_TOTAL;
        self.global_offset = 0;
        self.viewport_start = 0;
        self.table_rows_base = 0;
        self.data_rows.clear();
        self.windows.clear();
        self.loading_windows.clear();
        self.cache_clock = 0;
        self.fetch_error.clear();
        self.window_fetch_error.clear();
        self.locked_start_utc = self.time.start_value_utc();
        self.locked_end_utc = self.time.end_value_utc();
        self.loading = true;
        self.has_queried = true;
        true
    }

    fn start_run(&mut self) -> Vec<Effect> {
        if !self.prepare_run() {
            return Vec::new();
        }
        self.fetch_window(0).into_iter().collect()
    }

    fn reset_invalid_dataset_selection(&mut self) {
        if self.dataset.is_empty() || self.all_datasets.is_empty() {
            return;
        }
        if let Some(i) = self
            .all_datasets
            .iter()
            .position(|d| d.as_bytes() == self.dataset_identity_bytes())
        {
            self.dataset_index = i;
            return;
        }
        self.dataset = self.all_datasets[0].clone();
        self.dataset_raw = None;
        self.dataset_index = 0;
    }

    /// fetchSQLWindow with its offset/limit guards and in-flight dedupe.
    fn fetch_window(&mut self, index: i64) -> Option<Effect> {
        if index < 0 || self.base_query.is_empty() {
            return None;
        }
        if self.query_limit >= 0 && index * self.window_size >= self.query_limit {
            return None;
        }
        if self.windows.contains_key(&index) || self.loading_windows.contains_key(&index) {
            return None;
        }
        let mut fetch_limit = self.window_size;
        if self.query_limit >= 0 {
            let remaining = self.query_limit - index * self.window_size;
            if remaining <= 0 {
                return None;
            }
            fetch_limit = fetch_limit.min(remaining);
        }
        self.loading_windows.insert(index, ());
        Some(Effect::FetchWindow(WindowRequest {
            run_id: self.run_id,
            window_index: index as usize,
            query: inject_sql_window(
                &self.base_query,
                fetch_limit,
                self.query_base_offset + index * self.window_size,
            ),
            start_utc: self.locked_start_utc.clone(),
            end_utc: self.locked_end_utc.clone(),
        }))
    }

    /// completeSQLWindowFetch: the only run-ID guard in this model.
    fn complete_window(&mut self, completion: WindowCompletion) -> Vec<Effect> {
        if completion.run_id != self.run_id {
            return Vec::new();
        }
        let index = completion.window_index as i64;
        self.loading_windows.remove(&index);
        self.loading = self.windows.is_empty() && !self.loading_windows.is_empty();
        self.status.info.clear();
        let data = match completion.result {
            Err(mut message) => {
                if message.is_empty() {
                    message = "query failed".into();
                }
                if self.windows.is_empty() {
                    self.data_rows.clear();
                    self.table.set_rows(Vec::new());
                    self.fetch_error = message;
                    self.status.error = "query failed".into();
                    self.reset_invalid_dataset_selection();
                    return Vec::new();
                }
                self.window_fetch_error = message;
                self.status.error = "window fetch failed".into();
                self.refresh_visible_rows();
                return Vec::new();
            }
            Ok(data) => data,
        };
        let QueryData { fields, records } = data;
        self.fetch_error.clear();
        self.window_fetch_error.clear();
        self.status.error.clear();
        self.cache_clock += 1;
        let count = records.len() as i64;
        self.windows.insert(
            index,
            Window {
                data: records,
                last_used: self.cache_clock,
            },
        );
        self.evict_windows();
        if !fields.is_empty() {
            self.schema = fields.clone();
            let sample = self
                .windows
                .get(&index)
                .map(|w| w.data.clone())
                .unwrap_or_default();
            self.update_table_columns(&fields, &sample);
        }
        if count < self.window_size {
            self.actual_total = index * self.window_size + count;
        }
        if self.query_limit >= 0 && self.actual_total >= 0 && self.actual_total > self.query_limit {
            self.actual_total = self.query_limit;
        }
        let total = self.current_total();
        if total == 0 {
            self.global_offset = 0;
        } else if self.global_offset >= total {
            self.global_offset = total - 1;
        }
        self.clamp_viewport();
        self.refresh_visible_rows();
        self.focus_pane(Focus::Table);
        self.prefetch_if_needed().into_iter().collect()
    }

    /// evictSQLWindows: least recently used, sparing the current window.
    fn evict_windows(&mut self) {
        while self.windows.len() > SQL_MAX_CACHED_WINDOWS {
            let current = self.current_window_index();
            let victim = self
                .windows
                .iter()
                .filter(|(index, _)| **index != current)
                .min_by_key(|(_, w)| w.last_used)
                .or_else(|| self.windows.iter().min_by_key(|(_, w)| w.last_used))
                .map(|(index, _)| *index);
            match victim {
                Some(index) => {
                    self.windows.remove(&index);
                }
                None => return,
            }
        }
    }

    fn current_window_index(&self) -> i64 {
        if self.window_size <= 0 {
            0
        } else {
            self.global_offset / self.window_size
        }
    }

    fn current_total(&self) -> i64 {
        if self.actual_total >= 0 {
            self.actual_total
        } else if self.query_limit >= 0 {
            self.query_limit
        } else {
            0
        }
    }

    fn refresh_visible_rows(&mut self) {
        let total = self.current_total();
        if total == 0 {
            self.data_rows.clear();
            self.table.set_rows(Vec::new());
            self.viewport_start = 0;
            return;
        }
        let page_size = self.table_page_size().max(1);
        self.clamp_viewport();
        let start = self.viewport_start;
        let mut rows = Vec::new();
        let mut absolute = start;
        while absolute < total && (rows.len() as i64) < page_size {
            let window_index = absolute / self.window_size;
            let local = (absolute % self.window_size) as usize;
            let present = self
                .windows
                .get(&window_index)
                .is_some_and(|w| local < w.data.len());
            if present {
                self.cache_clock += 1;
                let clock = self.cache_clock;
                let window = self.windows.get_mut(&window_index).expect("present");
                window.last_used = clock;
                let record = window.data[local].clone();
                rows.push(self.format_row(&record));
            } else {
                rows.push(self.placeholder_row(absolute));
            }
            absolute += 1;
        }
        self.table_rows_base = start;
        let row_count = rows.len() as i64;
        self.data_rows = rows.clone();
        let mut highlight = (self.global_offset - start).max(0);
        if highlight >= row_count && row_count > 0 {
            highlight = row_count - 1;
        }
        self.table.set_rows(rows);
        self.table.set_highlighted(highlight);
    }

    fn clamp_viewport(&mut self) {
        let total = self.current_total();
        let page_size = self.table_page_size().max(1);
        if total <= 0 {
            self.viewport_start = 0;
            return;
        }
        self.global_offset = self.global_offset.clamp(0, total - 1);
        let max_start = (total - page_size).max(0);
        self.viewport_start = self.viewport_start.min(max_start).max(0);
        if self.global_offset < self.viewport_start {
            self.viewport_start = self.global_offset;
        }
        if self.global_offset >= self.viewport_start + page_size {
            self.viewport_start = self.global_offset - page_size + 1;
        }
    }

    /// tablePageSize: the bottom bar is always three lines tall.
    fn table_page_size(&self) -> i64 {
        if self.height == 0 {
            return 10;
        }
        sql_page_size(self.height, 3).max(1)
    }

    fn current_table_page(&self) -> i64 {
        self.global_offset / self.table_page_size().max(1) + 1
    }

    fn max_table_pages(&self) -> i64 {
        let total = self.current_total();
        if total <= 0 {
            return 1;
        }
        let page_size = self.table_page_size().max(1);
        ((total + page_size - 1) / page_size).max(1)
    }

    /// formatSQLRow: only a string p_timestamp is reformatted.
    fn format_row(&self, record: &Record) -> Row {
        let mut row = record.clone();
        if let Some(Cell::Str(value)) = row.get(DATE_TIME_KEY) {
            let formatted =
                format_timestamp_hms(value, self.time.start().zone, self.time.display_utc());
            row.insert(DATE_TIME_KEY.into(), Cell::Str(formatted));
        }
        row
    }

    fn placeholder_row(&self, absolute: i64) -> Row {
        let mut row = Row::new();
        for column in &self.schema {
            row.insert(column.clone(), Cell::Str("loading...".into()));
        }
        if row.is_empty() {
            row.insert("Id".into(), Cell::Str((absolute + 1).to_string()));
        }
        row
    }

    fn prefetch_if_needed(&mut self) -> Option<Effect> {
        let window = self.current_window_index();
        let local = self.global_offset % self.window_size;
        if local < (self.window_size as f64 * 0.9) as i64 {
            return None;
        }
        self.fetch_window(window + 1)
    }

    fn ensure_current_window(&mut self) -> Option<Effect> {
        self.fetch_window(self.current_window_index())
    }

    fn move_cursor(&mut self, delta: i64) -> Vec<Effect> {
        let total = self.current_total();
        if total == 0 {
            return Vec::new();
        }
        let next = (self.global_offset + delta).clamp(0, total - 1);
        if next == self.global_offset {
            return Vec::new();
        }
        self.global_offset = next;
        self.clamp_viewport();
        self.refresh_visible_rows();
        let mut effects: Vec<Effect> = self.ensure_current_window().into_iter().collect();
        effects.extend(self.prefetch_if_needed());
        effects
    }

    /// handleSQLTableNavigation; `None` when the key belongs to the table.
    fn table_navigation(&mut self, key: &Key) -> Option<Vec<Effect>> {
        if self.table.filter.focused() || self.data_rows.is_empty() {
            return None;
        }
        let page = self.table_page_size();
        if key.is(&["up", "w"]) {
            return Some(self.move_cursor(-1));
        }
        if key.is(&["down", "s"]) {
            return Some(self.move_cursor(1));
        }
        if key.is(&["shift+up", "W", "pgup"]) {
            return Some(self.move_cursor(-page));
        }
        if key.is(&["shift+down", "S", "pgdown"]) {
            return Some(self.move_cursor(page));
        }
        if key.is(&["home", "ctrl+y"]) {
            self.global_offset = 0;
            self.viewport_start = 0;
            self.refresh_visible_rows();
            return Some(self.ensure_current_window().into_iter().collect());
        }
        if key.is(&["end", "ctrl+v"]) {
            let total = self.current_total();
            if total > 0 {
                self.global_offset = total - 1;
                self.viewport_start = total - page;
                self.refresh_visible_rows();
                return Some(self.ensure_current_window().into_iter().collect());
            }
            return Some(Vec::new());
        }
        None
    }

    /// UpdateTableColumns: timestamp first, p_tags/p_metadata last, widths
    /// inferred from up to 100 sample rows and scaled to the terminal width.
    fn update_table_columns(&mut self, schema: &[String], sample: &[Record]) {
        if schema.is_empty() {
            return;
        }
        self.schema = schema.to_vec();
        struct Spec {
            key: String,
            title: String,
            width: i64,
            filterable: bool,
            fixed: bool,
        }
        let mut specs = Vec::new();
        let label = format_result_time_label(self.time.display_utc());
        if schema.iter().any(|c| c == DATE_TIME_KEY) {
            specs.push(Spec {
                key: DATE_TIME_KEY.into(),
                title: format!("time {label}"),
                width: DATE_TIME_WIDTH as i64,
                filterable: false,
                fixed: true,
            });
        }
        for title in schema {
            if title == DATE_TIME_KEY || title == TAG_KEY || title == METADATA_KEY {
                continue;
            }
            specs.push(Spec {
                key: title.clone(),
                title: title.clone(),
                width: infer_column_width(title, sample, 100, 100) + 1,
                filterable: true,
                fixed: false,
            });
        }
        for key in [TAG_KEY, METADATA_KEY] {
            if schema.iter().any(|c| c == key) {
                specs.push(Spec {
                    key: key.into(),
                    title: key.into(),
                    width: infer_column_width(key, sample, 100, 80),
                    filterable: true,
                    fixed: false,
                });
            }
        }
        if self.width > 0 && !specs.is_empty() {
            const MIN_READABLE: i64 = 8;
            let available = self.width - (specs.len() as i64 + 1);
            let total: i64 = specs.iter().map(|s| s.width).sum();
            let fixed: i64 = specs.iter().filter(|s| s.fixed).map(|s| s.width).sum();
            if total > available {
                let scalable_available = available - fixed;
                let scalable_total = total - fixed;
                let scalable = specs.iter().filter(|s| !s.fixed).count() as i64;
                if scalable_total > 0
                    && scalable_available > 0
                    && scalable > 0
                    && scalable_available / scalable >= MIN_READABLE
                {
                    for spec in specs.iter_mut().filter(|s| !s.fixed) {
                        spec.width =
                            (spec.width * scalable_available / scalable_total).max(MIN_READABLE);
                    }
                }
            }
        }
        let columns = specs
            .into_iter()
            .map(|s| Column {
                key: s.key,
                title: super::unicode::upper(&s.title),
                width: s.width,
                filterable: s.filterable,
            })
            .collect();
        self.table.set_columns(columns);
        self.table.set_max_total_width(self.width);
    }

    // -- reducer -----------------------------------------------------------

    /// QueryModel.Update.
    pub(crate) fn update(&mut self, msg: Msg, now: DateTime<Utc>) -> Vec<Effect> {
        match msg {
            Msg::SpinnerTick(tag) => {
                if !self.loading || tag != self.spinner_tag {
                    return Vec::new();
                }
                self.spinner_frame = (self.spinner_frame + 1) % SPINNER_FRAMES.len();
                self.spinner_tag += 1;
                vec![Effect::SpinnerTick {
                    tag: self.spinner_tag,
                    delayed: true,
                }]
            }
            Msg::Resize(width, height) => {
                self.width = width;
                self.height = height;
                self.table.set_max_total_width(width);
                self.table.set_page_size(sql_page_size(height, 3));
                if !self.windows.is_empty() {
                    self.refresh_visible_rows();
                }
                Vec::new()
            }
            // schemaMsg has no dataset and no guard: a late schema for a
            // previously selected dataset still replaces the column list.
            Msg::Schema(schema) => {
                self.columns_loading = false;
                if schema.error.is_empty() && !schema.columns.is_empty() {
                    self.all_columns = schema.columns.clone();
                    self.filtered_columns = schema.columns;
                    self.column_index = 0;
                }
                Vec::new()
            }
            Msg::Datasets(datasets) => self.complete_datasets(datasets),
            Msg::Window(window) => self.complete_window(window),
            Msg::Key(key) => self.key(key, now),
        }
    }

    fn complete_datasets(&mut self, completion: DatasetsCompletion) -> Vec<Effect> {
        self.datasets_loading = false;
        let datasets = match completion.result {
            Err(message) => {
                self.status.error = format!("could not load datasets: {message}");
                return Vec::new();
            }
            Ok(datasets) => datasets,
        };
        self.all_datasets = datasets.clone();
        self.filtered_datasets = datasets;
        self.dataset_index = 0;
        if self.dataset.is_empty()
            && let Some(first) = self.all_datasets.first()
        {
            self.dataset = first.clone();
            self.dataset_raw = None;
        }
        if let Some(i) = self
            .filtered_datasets
            .iter()
            .position(|d| d.as_bytes() == self.dataset_identity_bytes())
        {
            self.dataset_index = i;
        }
        self.reset_invalid_dataset_selection();
        if self.columns_drawer_open
            && !self.dataset.is_empty()
            && self.all_columns.is_empty()
            && !self.columns_loading
        {
            self.columns_loading = true;
            return vec![Effect::FetchSchema(self.dataset_bytes())];
        }
        Vec::new()
    }

    fn run_effects(&mut self) -> Vec<Effect> {
        self.status = Status::default();
        self.loading = true;
        self.has_queried = true;
        let mut effects = vec![Effect::SpinnerTick {
            tag: self.spinner_tag,
            delayed: false,
        }];
        effects.extend(self.start_run());
        effects
    }

    fn open_dataset_overlay(&mut self) -> Vec<Effect> {
        self.overlay = Overlay::Dataset;
        self.spotlight_filter.focus();
        self.spotlight_filter.disarm_blink();
        self.datasets_loading = true;
        vec![Effect::FetchDatasets]
    }

    fn key(&mut self, key: Key, now: DateTime<Utc>) -> Vec<Effect> {
        // Dataset spotlight: captures every key, Ctrl+C included.
        if self.overlay == Overlay::Dataset {
            match key.code {
                KeyCode::Esc => {
                    self.overlay = Overlay::None;
                    self.spotlight_filter.set_value("");
                    self.spotlight_filter.blur();
                    self.focus_selected();
                    return Vec::new();
                }
                KeyCode::Enter => {
                    let mut effects = Vec::new();
                    if let Some(selected) = self.filtered_datasets.get(self.dataset_index).cloned()
                    {
                        let changed = selected.as_bytes() != self.dataset_identity_bytes();
                        let needs_schema = changed || self.all_columns.is_empty();
                        if changed {
                            self.dataset = selected.clone();
                            self.dataset_raw = None;
                            self.selected_column.clear();
                            self.column_filter.set_value("");
                            self.all_columns.clear();
                            self.filtered_columns.clear();
                        }
                        if needs_schema {
                            self.columns_loading = true;
                            effects.push(Effect::FetchSchema(selected.into_bytes()));
                        }
                        // The placeholder query names "dataset"; the run
                        // substitutes the selection later.
                        if self.editor.value().trim().is_empty() {
                            self.editor.set_value(EMPTY_EDITOR_QUERY);
                        }
                    }
                    self.overlay = Overlay::None;
                    self.spotlight_filter.set_value("");
                    self.spotlight_filter.blur();
                    self.focus_pane(Focus::Query);
                    return effects;
                }
                KeyCode::Up => {
                    self.dataset_index = self.dataset_index.saturating_sub(1);
                    return Vec::new();
                }
                KeyCode::Down => {
                    if self.dataset_index + 1 < self.filtered_datasets.len() {
                        self.dataset_index += 1;
                    }
                    return Vec::new();
                }
                _ => {
                    let previous = self.spotlight_filter.value();
                    self.spotlight_filter.update(&key);
                    let mut effects = Vec::new();
                    if self.spotlight_filter.take_paste_request() {
                        effects.push(Effect::ReadClipboard);
                    }
                    if self.spotlight_filter.value() != previous {
                        self.filtered_datasets =
                            filter_names(&self.all_datasets, &self.spotlight_filter.value());
                        self.dataset_index = 0;
                    }
                    return effects;
                }
            }
        }

        if self.overlay == Overlay::None {
            if key.name() == "ctrl+l" {
                return self.toggle_columns_drawer();
            }
            // Ctrl+D precedes the editor's delete-forward binding.
            if key.code == KeyCode::Ctrl(4) {
                return self.open_dataset_overlay();
            }
            if key.code == KeyCode::Enter && self.current_focus() == Focus::Dataset {
                return self.open_dataset_overlay();
            }
            if key.code == KeyCode::Enter && self.current_focus() == Focus::Columns {
                self.insert_selected_column();
                return Vec::new();
            }
            if key.code == KeyCode::Enter && self.current_focus() == Focus::Time {
                self.time_edit = self.time.clone();
                self.time_edit.sync_preset(now);
                self.overlay = Overlay::Time;
                return Vec::new();
            }
            if key.code == KeyCode::Tab {
                self.focused = (self.focused + 1) % FOCUS_ORDER.len();
                self.focus_selected();
                return Vec::new();
            }
            if key.code == KeyCode::ShiftTab {
                self.focused = (self.focused + FOCUS_ORDER.len() - 1) % FOCUS_ORDER.len();
                self.focus_selected();
                return Vec::new();
            }
            if self.current_focus() == Focus::Columns && self.columns_drawer_open {
                match key.code {
                    KeyCode::Up => {
                        if self.column_index > 0 {
                            self.column_index -= 1;
                        }
                        return Vec::new();
                    }
                    KeyCode::Down => {
                        if self.column_index < self.filtered_columns.len() as i64 - 1 {
                            self.column_index += 1;
                        }
                        return Vec::new();
                    }
                    KeyCode::Runes => {
                        let mut value = self.column_filter.value();
                        value.extend(key.runes.iter());
                        self.column_filter.set_value(&value);
                        self.filtered_columns = filter_names(&self.all_columns, &value);
                        self.column_index = 0;
                        return Vec::new();
                    }
                    KeyCode::Backspace => {
                        let mut value: Vec<char> = self.column_filter.value().chars().collect();
                        if value.pop().is_some() {
                            let value: String = value.into_iter().collect();
                            self.column_filter.set_value(&value);
                            self.filtered_columns = filter_names(&self.all_columns, &value);
                            self.column_index = 0;
                        }
                        return Vec::new();
                    }
                    KeyCode::Esc if !self.column_filter.value().is_empty() => {
                        self.column_filter.set_value("");
                        self.filtered_columns = self.all_columns.clone();
                        self.column_index = 0;
                        return Vec::new();
                    }
                    _ => {}
                }
            }
        }

        if self.overlay == Overlay::Time {
            if key.code == KeyCode::Esc {
                self.overlay = Overlay::None;
                self.focus_selected();
                return Vec::new();
            }
            if key.code == KeyCode::Enter {
                self.time = self.time_edit.clone();
                self.overlay = Overlay::None;
                self.focus_selected();
                return self.run_effects();
            }
        }

        // Ctrl+R / Alt+Enter run from the main view or the time overlay
        // (the latter without applying the draft).
        if key.code == KeyCode::Ctrl(18) || (key.alt && key.code == KeyCode::Enter) {
            self.overlay = Overlay::None;
            return self.run_effects();
        }

        // Ctrl+B is intercepted for the never-set queryIterator, so the
        // editor's Ctrl+B "left" binding never fires and the time overlay
        // closes without applying.
        if key.code == KeyCode::Ctrl(2) {
            self.overlay = Overlay::None;
            return Vec::new();
        }

        if key.code == KeyCode::Ctrl(3) {
            return vec![Effect::Quit];
        }

        let mut effects = Vec::new();
        match self.overlay {
            Overlay::None => match self.current_focus() {
                Focus::Query => {
                    self.editor.update(&key);
                    if self.editor.take_paste_request() {
                        effects.push(Effect::ReadClipboard);
                    }
                }
                Focus::Table => {
                    if let Some(navigation) = self.table_navigation(&key) {
                        effects = navigation;
                    } else {
                        self.table.update(&key);
                        if self.table.filter.take_paste_request() {
                            effects.push(Effect::ReadClipboard);
                        }
                    }
                }
                _ => {}
            },
            Overlay::Time => self.time_edit.update(&key, now),
            Overlay::Dataset => {}
        }
        effects
    }

    // -- view ----------------------------------------------------------------

    /// QueryModel.View. A frame is bytes only when the sidebar shows a raw
    /// extracted dataset (L-SQL-SIDEBAR); every other frame is text.
    pub(crate) fn view(&self, now: DateTime<Utc>) -> Frame {
        if self.width == 0 || self.height == 0 {
            return Frame::Text(String::new());
        }
        // View works on a copy: loading hides status text, mode reads "SQL".
        let mut status = self.status.clone();
        if self.loading {
            status = Status::default();
        }
        let bottom = self.bottom_bar(&status, self.width);
        let bottom_height = bottom.split('\n').count() as i64;

        let mut sidebar_w = SQL_CONTROLS_MIN_WIDTH;
        if self.width >= 140 {
            sidebar_w = SQL_CONTROLS_MIN_WIDTH + 3;
        }
        let body_h = (self.height - bottom_height).max(12);
        const EDITOR_H: i64 = 12;
        let full_height_controls = self.columns_drawer_open;
        let mut editor_w = self.width - sidebar_w - 1;
        if editor_w < 30 {
            editor_w = 30;
            sidebar_w = self.width - editor_w - 1;
        }
        // Render-time geometry on a copy; Update keeps SetWidth(70)/SetHeight(10).
        let mut editor = self.editor.clone();
        editor.set_size(
            (editor_w - 6).max(0) as usize,
            (EDITOR_H - 4).max(1) as usize,
        );
        let editor_pane = titled_pane(
            &self.render_panics,
            "EDITOR",
            &{
                let view = editor.view(&AreaStyles::pb_editor());
                if let Some(fault) = editor.take_fault() {
                    self.render_fault.set(Some(fault));
                }
                view
            },
            editor_w,
            EDITOR_H,
            self.current_focus() == Focus::Query,
        );

        // Prefer the selected dataset; fall back to the FROM clause. Go keeps
        // the extracted bytes even when they are not UTF-8.
        let mut dataset = DatasetValue::Text(self.dataset.clone());
        if let Some(raw) = &self.dataset_raw {
            dataset = DatasetValue::Raw(raw.clone());
        } else if self.dataset.is_empty() {
            match extract_dataset_bytes(&self.editor.value()) {
                Ok(bytes) if bytes != "—".as_bytes() && !bytes.is_empty() => {
                    dataset = match String::from_utf8(bytes) {
                        Ok(text) => DatasetValue::Text(text),
                        Err(error) => DatasetValue::Raw(error.into_bytes()),
                    };
                }
                Ok(_) => {}
                Err(panic) => self.render_panics.record(panic),
            }
        }
        if matches!(&dataset, DatasetValue::Text(text) if text.is_empty()) {
            dataset = DatasetValue::Text("select-dataset".into());
        }
        let controls_h = if full_height_controls {
            body_h
        } else {
            EDITOR_H
        };
        let controls = self.controls_box(&dataset, sidebar_w, controls_h);
        let raw_controls = match &controls {
            Block::Text(_) => None,
            Block::Bytes(bytes) => Some(bytes.clone()),
        };
        let controls = match controls {
            Block::Text(text) => text,
            // Only consulted when an overlay hides the sidebar.
            Block::Bytes(bytes) => crate::error::go_string(&bytes),
        };

        let results_h = (body_h - EDITOR_H).max(6);
        let results_w = if full_height_controls {
            editor_w
        } else {
            self.width
        };
        let inner_h = (results_h - 3).max(3);
        let inner_w = (results_w - 4).max(10);
        let page_size = (inner_h - 5).max(1);
        let mut table = self.table.clone();
        table.set_page_size(page_size);
        table.set_rows(self.data_rows.clone());
        table.set_max_total_width(inner_w);
        let cursor = table.cursor;
        table.set_highlighted(cursor);

        let p_style = |role| Style::new().fg(act(role));
        let mut inner = if !self.has_queried {
            let wordmark = p_style(Role::Accent).bold(true).render(PARSEABLE_ASCII_ART);
            style::place(inner_w as usize, inner_h as usize, 0.5, 0.5, &wordmark)
        } else if self.loading {
            let frame = Style::new()
                .fg(act(Role::Accent))
                .render(SPINNER_FRAMES[self.spinner_frame]);
            let content = Style::new()
                .fg(act(Role::Accent))
                .render(&format!("{frame} fetching..."));
            style::place(inner_w as usize, inner_h as usize, 0.5, 0.5, &content)
        } else if !self.fetch_error.is_empty() {
            let rendered = Style::new()
                .padding(&[1, 2])
                .fg(act(Role::Err))
                .width(inner_w as usize)
                .render(&self.fetch_error);
            rendered
                .split('\n')
                .take(inner_h as usize)
                .collect::<Vec<_>>()
                .join("\n")
        } else if self.data_rows.is_empty() {
            let message = p_style(Role::Faint).render("no results for this query");
            style::place(inner_w as usize, inner_h as usize, 0.5, 0.5, &message)
        } else {
            self.table_body(&table, inner_w, inner_h)
        };
        inner = inner
            .split('\n')
            .take(inner_h as usize)
            .collect::<Vec<_>>()
            .join("\n");
        let results = titled_pane(
            &self.render_panics,
            "RESULTS",
            &inner,
            results_w,
            results_h,
            self.current_focus() == Focus::Table,
        );

        let top_gap = vec![" "; EDITOR_H as usize].join("\n");
        if let (Some(controls), Overlay::None) = (&raw_controls, self.overlay) {
            // Go's bytes all the way: joins, final Width render (ansi.Wrap
            // turns C1 Execute bytes into runes and counts them) and emission.
            let body = if full_height_controls {
                let left = style::join_vertical(0.0, &[&editor_pane, &results]);
                let side_gap = vec![" "; body_h as usize].join("\n");
                style::join_horizontal_bytes(
                    0.0,
                    &[left.as_bytes(), side_gap.as_bytes(), controls.as_slice()],
                )
            } else {
                let top = style::join_horizontal_bytes(
                    0.0,
                    &[
                        editor_pane.as_bytes(),
                        top_gap.as_bytes(),
                        controls.as_slice(),
                    ],
                );
                style::join_vertical_bytes(0.0, &[top.as_slice(), results.as_bytes()])
            };
            let all = style::join_vertical_bytes(0.0, &[body.as_slice(), bottom.as_bytes()]);
            return Frame::Bytes(Style::new().width(self.width as usize).render_bytes(&all));
        }
        let mut body = {
            let top = style::join_horizontal(0.0, &[&editor_pane, &top_gap, &controls]);
            style::join_vertical(0.0, &[&top, &results])
        };
        if full_height_controls {
            let left = style::join_vertical(0.0, &[&editor_pane, &results]);
            let side_gap = vec![" "; body_h as usize].join("\n");
            body = style::join_horizontal(0.0, &[&left, &side_gap, &controls]);
        }
        let main_h = (self.height - bottom_height).max(0) as usize;
        let main = match self.overlay {
            Overlay::None => body,
            Overlay::Time => style::place(
                self.width as usize,
                main_h,
                0.5,
                0.5,
                &self.time_edit.view(now, &self.render_panics),
            ),
            Overlay::Dataset => style::place(
                self.width as usize,
                main_h,
                0.5,
                0.5,
                &self.dataset_spotlight(),
            ),
        };
        let all = style::join_vertical(0.0, &[&main, &bottom]);
        Frame::Text(Style::new().width(self.width as usize).render(&all))
    }

    fn table_body(&self, table: &Table, inner_w: i64, inner_h: i64) -> String {
        let rendered = table.view();
        let mut lines: Vec<&str> = rendered.split('\n').collect();
        while lines.last() == Some(&"") {
            lines.pop();
        }
        // Peel off the closing rule so "--" rows land inside the frame.
        let bottom_rule = lines.pop().unwrap_or("").to_owned();
        let body_h = lines.len() as i64 + 1;
        let padding = (inner_h - 1 - body_h).max(0);
        let dash = Style::new()
            .fg(act(Role::Ghost))
            .width(inner_w as usize)
            .render(" --");
        let total = self.current_total();
        let mut left = String::new();
        let mut right = String::new();
        if total > 0 {
            left = format!("{}/{}", self.current_table_page(), self.max_table_pages());
        }
        if !self.data_rows.is_empty() && total > 0 {
            right = format!("rows {}/{}", self.global_offset + 1, total);
            if !self.loading_windows.is_empty() {
                right.push_str(" · loading");
            }
            if !self.window_fetch_error.is_empty() {
                right.push_str(" · fetch failed");
            }
        }
        let faint = Style::new().fg(act(Role::Faint));
        let (left, right) = (faint.render(&left), faint.render(&right));
        let gap = (inner_w - 2 - style::width(&left) as i64 - style::width(&right) as i64).max(1);
        let footer = Style::new()
            .width(inner_w as usize)
            .padding(&[0, 1])
            .render(&format!("{left}{}{right}", " ".repeat(gap as usize)));
        let mut parts = vec![lines.join("\n")];
        for _ in 0..padding {
            parts.push(dash.clone());
        }
        parts.push(bottom_rule);
        parts.push(footer);
        parts.join("\n")
    }

    /// renderSQLControlsBox: TIME, DATASET, COLUMNS sections.
    fn controls_box(&self, dataset: &DatasetValue, width: i64, height: i64) -> Block {
        let focus = self.current_focus();
        let inner_w = (width - 2).max(4);
        let rail = Style::new().bg(act(Role::Active)).render(" ");
        let dim = Style::new().fg(act(Role::Faint));
        let body = Style::new().fg(act(Role::Body));
        let active = Style::new().fg(act(Role::Active)).bold(true);
        let section_focused = matches!(focus, Focus::Time | Focus::Dataset | Focus::Columns);
        let border = Style::new().fg(act(if section_focused {
            Role::BorderHi
        } else {
            Role::Border
        }));
        let title_style = |on: bool| {
            if on {
                Style::new().fg(act(Role::Accent)).bold(true)
            } else {
                Style::new().fg(act(Role::Faint))
            }
        };
        let value_w = (inner_w - 2).max(4);
        let utc = self.time.display_utc();
        let row = |label: &str, value: &str, truncate: bool| {
            let (label_style, prefix) = if focus == Focus::Time {
                (active, format!("{rail} "))
            } else {
                (dim, "  ".to_owned())
            };
            let display = if truncate {
                ui_truncate(value, value_w, &self.render_panics)
            } else {
                value.to_owned()
            };
            vec![
                format!("{prefix}{}", label_style.render(label)),
                format!("{prefix}{}", body.render(&display)),
            ]
        };
        let truncate_time = value_w < SQL_CONTROL_TIME_DISPLAY_WIDTH;
        let mut time_lines = row(
            "FROM",
            &format_control_time(self.time.start(), utc),
            truncate_time,
        );
        time_lines.extend(row(
            "TO",
            &format_control_time(self.time.end(), utc),
            truncate_time,
        ));
        time_lines.push(String::new());

        let (dataset_prefix, dataset_style) = if focus == Focus::Dataset {
            (format!("{rail} "), active)
        } else {
            ("  ".to_owned(), body)
        };
        let dataset_line_count: i64 = 2;
        let dataset_body: Option<Vec<Vec<u8>>> = match dataset {
            DatasetValue::Text(_) => None,
            DatasetValue::Raw(raw) => {
                // ui.Truncate keeps the raw bytes when they fit; its []rune
                // path (when they do not) yields valid text.
                let display =
                    style::ui_truncate_bytes(raw, value_w as isize).unwrap_or_else(|panic| {
                        self.render_panics.record(panic);
                        Vec::new()
                    });
                let mut line = dataset_prefix.as_bytes().to_vec();
                line.extend(dataset_style.render_bytes(&display));
                let joined = style::join_vertical_bytes(0.0, &[line.as_slice(), b"".as_slice()]);
                Some(pane_body_lines_bytes(
                    &joined,
                    width,
                    dataset_line_count,
                    &border,
                ))
            }
        };
        let dataset_lines = match dataset {
            DatasetValue::Text(text) => vec![
                format!(
                    "{dataset_prefix}{}",
                    dataset_style.render(&ui_truncate(text, value_w, &self.render_panics))
                ),
                String::new(),
            ],
            DatasetValue::Raw(_) => vec![String::new(); dataset_line_count as usize],
        };

        let columns = &self.filtered_columns;
        let columns_title = format!("COLUMNS ({})", columns.len());
        let section_h = (height - 4 - time_lines.len() as i64 - dataset_lines.len() as i64).max(1);
        let mut list_available = section_h;
        let mut footer = false;
        if self.columns_drawer_open && list_available > 1 {
            footer = true;
            list_available -= 1;
        }
        let list_available = list_available.max(1);
        let mut column_lines = Vec::new();
        let mut index = self.column_index;
        if !self.columns_drawer_open {
            column_lines.push(format!("  {}", dim.render("Show columns")));
        } else if self.columns_loading {
            column_lines.push(format!("  {}", dim.render("loading columns...")));
        } else if columns.is_empty() {
            column_lines.push(format!("  {}", dim.render("no columns")));
        } else {
            index = index.clamp(0, columns.len() as i64 - 1);
            let list_h = list_available.max(1);
            let mut start = 0;
            if index >= list_h {
                start = index - list_h + 1;
            }
            if start + list_h > columns.len() as i64 {
                start = (columns.len() as i64 - list_h).max(0);
            }
            let highlight = Style::new()
                .bg(ad(Role::SelRow))
                .fg(ad(Role::Accent))
                .bold(true)
                .width(value_w as usize);
            let mut i = start;
            while i < start + list_h && i < columns.len() as i64 {
                let name = ui_truncate(&columns[i as usize], value_w, &self.render_panics);
                if focus == Focus::Columns && i == index {
                    column_lines.push(highlight.render(&name));
                } else {
                    column_lines.push(format!("  {}", body.render(&name)));
                }
                i += 1;
            }
        }
        while (column_lines.len() as i64) < list_available {
            column_lines.push(String::new());
        }
        if footer {
            column_lines.push(self.column_filter_footer(
                index,
                columns.len(),
                value_w,
                focus == Focus::Columns,
            ));
        }

        let join = |lines: &[String]| {
            style::join_vertical(0.0, &lines.iter().map(String::as_str).collect::<Vec<_>>())
        };
        let mut lines = vec![pane_rule(
            &self.render_panics,
            "┌",
            "┐",
            &format!("TIME RANGE {}", format_result_time_label(utc)),
            width,
            &border,
            &title_style(focus == Focus::Time),
        )];
        lines.extend(pane_body_lines(
            &join(&time_lines),
            width,
            time_lines.len() as i64,
            &border,
        ));
        lines.push(pane_rule(
            &self.render_panics,
            "├",
            "┤",
            "DATASET <ctrl-d>",
            width,
            &border,
            &title_style(focus == Focus::Dataset),
        ));
        let dataset_at = lines.len();
        lines.extend(pane_body_lines(
            &join(&dataset_lines),
            width,
            dataset_lines.len() as i64,
            &border,
        ));
        lines.push(pane_rule(
            &self.render_panics,
            "├",
            "┤",
            &columns_title,
            width,
            &border,
            &title_style(focus == Focus::Columns),
        ));
        lines.extend(pane_body_lines(
            &join(&column_lines),
            width,
            section_h,
            &border,
        ));
        lines.push(border.render(&format!("└{}┘", "─".repeat(inner_w as usize))));
        let Some(dataset_body) = dataset_body else {
            return Block::Text(join(&lines));
        };
        let mut byte_lines: Vec<Vec<u8>> = lines.into_iter().map(String::into_bytes).collect();
        for (offset, line) in dataset_body.into_iter().enumerate() {
            byte_lines[dataset_at + offset] = line;
        }
        Block::Bytes(style::join_vertical_bytes(
            0.0,
            &byte_lines.iter().map(Vec::as_slice).collect::<Vec<_>>(),
        ))
    }

    /// renderColumnFilterFooter.
    fn column_filter_footer(&self, index: i64, total: usize, width: i64, focused: bool) -> String {
        let dim = Style::new().fg(act(Role::Faint));
        let body = Style::new().fg(act(Role::Body));
        let count = if total > 0 {
            format!("{}/{}", (index + 1).clamp(1, total as i64), total)
        } else {
            "0/0".into()
        };
        let (cursor, cursor_w) = if focused {
            (
                Style::new()
                    .bg(act(Role::Cursor))
                    .fg(act(Role::InvertText))
                    .render(" "),
                1,
            )
        } else {
            (String::new(), 0)
        };
        let prefix = "filter: ";
        let filter_w = (width - prefix.len() as i64 - count.len() as i64 - cursor_w - 1).max(0);
        let left = format!(
            "{}{}{cursor}",
            dim.render(prefix),
            body.render(&ui_truncate(
                &self.column_filter.value(),
                filter_w,
                &self.render_panics
            ))
        );
        let right = dim.render(&count);
        let pad = (width - style::width(&left) as i64 - style::width(&right) as i64).max(1);
        format!("{left}{}{right}", " ".repeat(pad as usize))
    }

    /// renderSQLSpotlight.
    fn dataset_spotlight(&self) -> String {
        let inner_w = (SPOTLIGHT_WIDTH - 6).max(20);
        let title_left = Style::new()
            .fg(act(Role::Accent))
            .bold(true)
            .render("SELECT DATASET");
        let count = if self.datasets_loading {
            String::new()
        } else {
            format!("{} datasets", self.filtered_datasets.len())
        };
        let title_right = Style::new().fg(act(Role::Faint)).render(&count);
        let gap =
            (inner_w - style::width(&title_left) as i64 - style::width(&title_right) as i64).max(1);
        let header = format!("{title_left}{}{title_right}", " ".repeat(gap as usize));
        let rule = Style::new()
            .fg(act(Role::BorderSoft))
            .render(&"─".repeat(inner_w as usize));
        let search = Style::new()
            .width(inner_w as usize)
            .render(&self.spotlight_filter.view(&filter_input_styles()));
        let mut list = Vec::new();
        let notice = |text: String| {
            Style::new()
                .fg(act(Role::Faint))
                .width(inner_w as usize)
                .padding(&[1, 0])
                .render(&text)
        };
        if self.datasets_loading {
            let frame = Style::new()
                .fg(act(Role::Accent))
                .render(SPINNER_FRAMES[self.spinner_frame]);
            list.push(notice(format!("  {frame} loading…")));
        } else if self.filtered_datasets.is_empty() {
            list.push(notice("  no datasets found".into()));
        } else {
            let limit = self.filtered_datasets.len().min(SPOTLIGHT_MAX_ITEMS);
            let start = if self.dataset_index >= SPOTLIGHT_MAX_ITEMS {
                self.dataset_index - SPOTLIGHT_MAX_ITEMS + 1
            } else {
                0
            };
            let rail = Style::new().bg(act(Role::Active)).render(" ");
            for i in start..(start + limit).min(self.filtered_datasets.len()) {
                let name = &self.filtered_datasets[i];
                if i == self.dataset_index {
                    let label = Style::new()
                        .fg(act(Role::Active))
                        .bold(true)
                        .width((inner_w - 2) as usize)
                        .render(name);
                    list.push(format!("{rail} {label}"));
                } else {
                    let label = Style::new()
                        .fg(act(Role::Body))
                        .width((inner_w - 2) as usize)
                        .render(name);
                    list.push(format!("  {label}"));
                }
            }
        }
        let list = style::join_vertical(0.0, &list.iter().map(String::as_str).collect::<Vec<_>>());
        let body = style::join_vertical(0.0, &[&header, &rule, &search, &rule, &list]);
        Style::new()
            .border(Border::Rounded)
            .border_fg(act(Role::Accent))
            .padding(&[0, 2])
            .render(&body)
    }

    /// buildBottomBar: hints, hairline, connection/status line.
    fn bottom_bar(&self, status: &Status, width: i64) -> String {
        let key_style = Style::new().fg(act(Role::Accent)).bold(true);
        let label_style = Style::new().fg(act(Role::Faint));
        let sep_style = Style::new().fg(act(Role::BorderSoft));
        let inner_w = (width - 2).max(1);
        let mut parts = Vec::new();
        let mut used = 0i64;
        for (key, label) in self.hints() {
            let k = key.strip_prefix('<').unwrap_or(key);
            let k = k.strip_suffix('>').unwrap_or(k);
            let part = format!(
                "{}{}",
                key_style.render(&format!("<{k}>")),
                label_style.render(&format!(" {}", label.to_lowercase()))
            );
            let mut need = style::width(&part) as i64;
            if used > 0 {
                need += 4;
            }
            if used + need > inner_w {
                break;
            }
            parts.push(part);
            used += need;
        }
        let shortcuts = format!(" {}", parts.join("    "));
        let divider = sep_style.render(&"─".repeat(width.max(0) as usize));
        let conn_left = style::join_horizontal(
            1.0,
            &[
                &Style::new()
                    .fg(act(Role::Accent))
                    .bold(true)
                    .render("Parseable"),
                "  ",
                &label_style.render(&self.profile_url),
            ],
        );
        let mut right_parts = Vec::new();
        if !status.error.is_empty() {
            right_parts.push(
                Style::new()
                    .fg(act(Role::Err))
                    .bold(true)
                    .render(&status.error),
            );
            right_parts.push(sep_style.render(" │ "));
        }
        right_parts.push(label_style.render("QUERY"));
        right_parts.push(" ".into());
        right_parts.push(key_style.render("SQL"));
        let conn_right = style::join_horizontal(
            1.0,
            &right_parts.iter().map(String::as_str).collect::<Vec<_>>(),
        );
        let gap =
            (inner_w - style::width(&conn_left) as i64 - style::width(&conn_right) as i64).max(1);
        let status_line = format!(" {conn_left}{}{conn_right} ", " ".repeat(gap as usize));
        style::join_vertical(0.0, &[&shortcuts, &divider, &status_line])
    }

    /// queryKeysForFocus.
    fn hints(&self) -> Vec<(&'static str, &'static str)> {
        let common = [
            ("<tab>", "Next pane"),
            ("<shift+tab>", "Prev pane"),
            ("<ctrl-r>", "Run"),
            ("<ctrl-c>", "Quit"),
        ];
        if self.overlay == Overlay::Time {
            let up_down = match self.time_edit.current_focus() {
                "list" => "Preset",
                "display" => "Display time",
                _ => "Adjust",
            };
            return vec![
                ("<↑/↓>", up_down),
                ("<tab>", "Switch pane"),
                ("<enter>", "Apply"),
                ("<esc>", "Cancel"),
            ];
        }
        let mut hints: Vec<(&str, &str)> = match self.current_focus() {
            Focus::Dataset => vec![("<enter>", "Open selector"), ("<ctrl-d>", "Open selector")],
            Focus::Columns if self.columns_drawer_open => vec![
                ("<↑/↓>", "Column"),
                ("<enter>", "Insert"),
                ("<type>", "Filter"),
            ],
            Focus::Columns => vec![("<enter>", "Show columns")],
            Focus::Time => vec![("<enter>", "Open picker")],
            Focus::Table => vec![("<↑/↓>", "Row"), ("<shift+↑/↓>", "Page"), ("</>", "Filter")],
            Focus::Query => vec![],
        };
        hints.extend(common);
        hints
    }
}

/// inferWidthForColumns: only Go strings count (JSON numbers decode to
/// float64, which the `case int` never matches).
fn infer_column_width(column: &str, sample: &[Record], max_records: usize, max_width: i64) -> i64 {
    let mut width = 2;
    for record in sample.iter().take(max_records) {
        let w = match record.get(column) {
            Some(Cell::Str(text)) => text.len() as i64,
            _ => 0,
        };
        if w > width {
            if w < max_width {
                width = w;
            } else {
                return max_width;
            }
        }
    }
    width.max(column.len() as i64)
}

/// renderTitledPane.
fn titled_pane(
    panics: &RenderPanics,
    title: &str,
    body: &str,
    width: i64,
    height: i64,
    focused: bool,
) -> String {
    let (border_role, title_role) = if focused {
        (Role::BorderHi, Role::Accent)
    } else {
        (Role::Border, Role::Faint)
    };
    let inner_w = (width - 2).max(4);
    let inner_h = (height - 2).max(3);
    let border = Style::new().fg(act(border_role));
    let title_style = Style::new().fg(act(title_role)).bold(focused);
    let mut lines = vec![pane_rule(
        panics,
        "┌",
        "┐",
        title,
        width,
        &border,
        &title_style,
    )];
    lines.extend(pane_body_lines(body, width, inner_h, &border));
    lines.push(border.render(&format!("└{}┘", "─".repeat(inner_w as usize))));
    style::join_vertical(0.0, &lines.iter().map(String::as_str).collect::<Vec<_>>())
}

/// paneRule.
fn pane_rule(
    panics: &RenderPanics,
    left: &str,
    right: &str,
    title: &str,
    width: i64,
    border: &Style,
    title_style: &Style,
) -> String {
    let width = width.max(2);
    if title.is_empty() {
        return border.render(&format!(
            "{left}{}{right}",
            "─".repeat((width - 2) as usize)
        ));
    }
    let prefix = format!("{left}──");
    let mut label = format!(" {title} ");
    let mut fill = width
        - style::width(&prefix) as i64
        - style::width(&label) as i64
        - style::width(right) as i64;
    if fill < 0 {
        fill = 0;
        let title_w =
            (width - style::width(&prefix) as i64 - style::width(right) as i64 - 2).max(1);
        label = format!(" {} ", ui_truncate(title, title_w, panics));
    }
    format!(
        "{}{}{}",
        border.render(&prefix),
        title_style.render(&label),
        border.render(&format!("{}{right}", "─".repeat(fill as usize)))
    )
}

/// One rendered frame: text, or Go's bytes when a raw extracted dataset is
/// on screen (the only SQL content that can be invalid UTF-8).
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Frame {
    Text(String),
    Bytes(Vec<u8>),
}

/// A rendered block that may carry raw bytes.
enum Block {
    Text(String),
    Bytes(Vec<u8>),
}

/// The sidebar dataset as Go holds it.
enum DatasetValue {
    Text(String),
    Raw(Vec<u8>),
}

/// reflow truncate over Go bytes: `for _, c := range string(b)` yields
/// RuneError (width 1, re-encoded as U+FFFD) for each invalid byte, which is
/// `error::go_string`'s per-byte replacement, so the text port applies.
fn reflow_truncate_bytes(text: &[u8], width: usize, tail: &str) -> Vec<u8> {
    reflow_truncate(&crate::error::go_string(text), width, tail).into_bytes()
}

/// paneBodyLines over Go bytes (lipgloss.Width is x/ansi StringWidth).
fn pane_body_lines_bytes(body: &[u8], width: i64, height: i64, border: &Style) -> Vec<Vec<u8>> {
    let inner_w = (width - 2).max(1);
    let text_w = (inner_w - 2).max(1);
    let mut lines: Vec<Vec<u8>> = body
        .split(|b| *b == b'\n')
        .take(height.max(0) as usize)
        .map(<[u8]>::to_vec)
        .collect();
    while (lines.len() as i64) < height {
        lines.push(Vec::new());
    }
    let edge = border.render("│");
    lines
        .into_iter()
        .map(|mut line| {
            if style::width_bytes(&line) as i64 > text_w {
                line = reflow_truncate_bytes(&line, text_w as usize, "");
            }
            let pad = (text_w - style::width_bytes(&line) as i64).max(0) as usize;
            let mut out = edge.as_bytes().to_vec();
            out.push(b' ');
            out.extend(line);
            out.extend(std::iter::repeat_n(b' ', pad));
            out.push(b' ');
            out.extend(edge.as_bytes());
            out
        })
        .collect()
}

/// paneBodyLines.
fn pane_body_lines(body: &str, width: i64, height: i64, border: &Style) -> Vec<String> {
    let inner_w = (width - 2).max(1);
    let text_w = (inner_w - 2).max(1);
    let mut lines: Vec<String> = body
        .split('\n')
        .take(height.max(0) as usize)
        .map(str::to_owned)
        .collect();
    while (lines.len() as i64) < height {
        lines.push(String::new());
    }
    lines
        .into_iter()
        .map(|mut line| {
            if style::width(&line) as i64 > text_w {
                line = reflow_truncate(&line, text_w as usize, "");
            }
            let pad = (text_w - style::width(&line) as i64).max(0) as usize;
            format!(
                "{} {line}{} {}",
                border.render("│"),
                " ".repeat(pad),
                border.render("│")
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(code: KeyCode) -> Key {
        Key {
            code,
            runes: Vec::new(),
            alt: false,
            paste: false,
        }
    }
    fn ctrl(c: char) -> Key {
        key(KeyCode::Ctrl(c as u8 - b'a' + 1))
    }
    fn runes(text: &str) -> Key {
        Key {
            code: KeyCode::Runes,
            runes: text.chars().collect(),
            alt: false,
            paste: false,
        }
    }
    fn fixed_now() -> DateTime<Utc> {
        DateTime::parse_from_rfc3339("2026-10-14T12:00:00Z")
            .unwrap()
            .with_timezone(&Utc)
    }
    fn state(query: &str) -> QueryState {
        let end = GoTime {
            instant: fixed_now(),
            zone: Zone::Utc,
        };
        let start = end.add(ChronoDuration::hours(-1));
        QueryState::new(
            "http://fixture".into(),
            &rewrite::prepare_query(query),
            start,
            end,
            Some((120, 40)),
        )
    }
    fn data(fields: &[&str], rows: usize) -> QueryData {
        QueryData {
            fields: fields.iter().map(|f| f.to_string()).collect(),
            records: (0..rows)
                .map(|i| {
                    let mut r = Record::new();
                    r.insert("a".into(), Cell::Value(i.to_string()));
                    r
                })
                .collect(),
        }
    }
    fn windows(effects: &[Effect]) -> Vec<WindowRequest> {
        effects
            .iter()
            .filter_map(|e| match e {
                Effect::FetchWindow(r) => Some(r.clone()),
                _ => None,
            })
            .collect()
    }
    fn complete(s: &mut QueryState, request: &WindowRequest, rows: usize) -> Vec<Effect> {
        s.update(
            Msg::Window(WindowCompletion {
                run_id: request.run_id,
                window_index: request.window_index,
                result: Ok(data(&["a"], rows)),
            }),
            fixed_now(),
        )
    }

    #[test]
    fn pr5_ui_unicode_dataset_and_column_filters_use_go_simple_lowercase() {
        for (name, query, matches) in [
            ("İstanbul", "ist", true),
            ("istanbul", "İST", true),
            ("ΟΣ", "οσ", true),
            ("οσ", "ΟΣ", true),
            ("ΟΣ", "ος", false),
            ("İstanbul", "i\u{307}st", false),
        ] {
            let mut s = state("");
            s.update(
                Msg::Datasets(DatasetsCompletion {
                    result: Ok(vec![name.to_owned()]),
                }),
                fixed_now(),
            );
            s.update(Msg::Key(ctrl('d')), fixed_now());
            s.update(Msg::Key(runes(query)), fixed_now());
            assert_eq!(
                !s.filtered_datasets.is_empty(),
                matches,
                "dataset {name:?}/{query:?}"
            );

            s.update(Msg::Key(key(KeyCode::Esc)), fixed_now());
            s.columns_drawer_open = true;
            s.all_columns = vec![name.to_owned()];
            s.filtered_columns = s.all_columns.clone();
            s.focus_pane(Focus::Columns);
            s.update(Msg::Key(runes(query)), fixed_now());
            assert_eq!(
                !s.filtered_columns.is_empty(),
                matches,
                "column {name:?}/{query:?}"
            );
        }
    }

    #[test]
    fn pr5_ui_unicode_sql_rows_use_go_simple_lowercase() {
        let mut table = Table::new(5, 80);
        table.set_columns(vec![Column {
            key: "value".into(),
            title: "VALUE".into(),
            width: 30,
            filterable: true,
        }]);
        for (value, query, matches) in [
            ("İstanbul", "ist", true),
            ("istanbul", "İST", true),
            ("ΟΣ", "οσ", true),
            ("οσ", "ΟΣ", true),
            ("ΟΣ", "ος", false),
            ("İstanbul", "i\u{307}st", false),
        ] {
            table.set_rows(vec![BTreeMap::from([(
                "value".into(),
                Cell::Value(value.into()),
            )])]);
            table.filter.set_value(query);
            assert_eq!(
                !table.visible_rows().is_empty(),
                matches,
                "row {value:?}/{query:?}"
            );
        }
    }

    #[test]
    fn init_requests_spinner_datasets_and_first_window_with_wire_window() {
        let mut s = state("select * from logs");
        let effects = s.init();
        assert_eq!(
            effects[..2],
            [
                Effect::SpinnerTick {
                    tag: 0,
                    delayed: false
                },
                Effect::FetchDatasets
            ]
        );
        let w = windows(&effects);
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].query, "select * from logs LIMIT 500 OFFSET 0");
        assert_eq!(w[0].start_utc, "2026-10-14T11:00:00Z");
        assert_eq!(w[0].end_utc, "2026-10-14T12:00:00Z");
        assert!(s.loading && s.has_queried);
    }

    #[test]
    fn empty_query_starts_idle_without_a_window() {
        let mut s = state("");
        assert!(windows(&s.init()).is_empty());
        assert!(!s.loading && !s.has_queried);
    }

    #[test]
    fn user_limit_and_offset_bound_windows() {
        let mut s = state("select * from logs limit 700 offset 10");
        let first = windows(&s.init());
        assert_eq!(first[0].query, "select * from logs LIMIT 500 OFFSET 10");
        // Full first window: total stays unknown, limited by the user limit.
        let effects = complete(&mut s, &first[0], 500);
        assert!(windows(&effects).is_empty());
        assert_eq!(s.current_total(), 700);
        // Jump to the end: second window fetches only the remaining 200 rows.
        let effects = s.update(Msg::Key(key(KeyCode::End)), fixed_now());
        let second = windows(&effects);
        assert_eq!(second[0].query, "select * from logs LIMIT 200 OFFSET 510");
        assert_eq!(second[0].window_index, 1);
    }

    #[test]
    fn stale_run_completion_is_dropped_but_current_run_applies() {
        let mut s = state("select * from logs");
        let first = windows(&s.init());
        let rerun = s.update(Msg::Key(ctrl('r')), fixed_now());
        let second = windows(&rerun);
        assert_eq!(second[0].run_id, first[0].run_id + 1);
        assert!(complete(&mut s, &first[0], 3).is_empty());
        assert!(s.windows.is_empty() && s.loading);
        complete(&mut s, &second[0], 3);
        assert_eq!(s.current_total(), 3);
        assert_eq!(s.current_focus(), Focus::Table);
    }

    #[test]
    fn prefetch_at_ninety_percent_and_cache_keeps_three_windows() {
        let mut s = state("select * from logs limit 5000");
        let mut pending = windows(&s.init());
        complete(&mut s, &pending.remove(0), 500);
        s.global_offset = 449;
        let effects = s.update(Msg::Key(key(KeyCode::Down)), fixed_now());
        let w = windows(&effects);
        assert_eq!(w.len(), 1, "prefetch at local offset 450");
        assert_eq!(w[0].window_index, 1);
        // In-flight windows are not requested twice.
        assert!(windows(&s.update(Msg::Key(key(KeyCode::Down)), fixed_now())).is_empty());
        complete(&mut s, &w[0], 500);
        for index in 2..5 {
            s.global_offset = index * 500 - 1;
            s.clamp_viewport();
            let effects = s.update(Msg::Key(key(KeyCode::Down)), fixed_now());
            let w = windows(&effects);
            complete(&mut s, &w[0], 500);
            assert!(s.windows.len() <= SQL_MAX_CACHED_WINDOWS);
            assert!(s.windows.contains_key(&s.current_window_index()));
        }
    }

    #[test]
    fn partial_window_fixes_total_and_errors_split_first_and_later_windows() {
        let mut s = state("select * from logs limit 5000");
        let first = windows(&s.init());
        s.update(
            Msg::Window(WindowCompletion {
                run_id: first[0].run_id,
                window_index: 0,
                result: Err(String::new()),
            }),
            fixed_now(),
        );
        assert_eq!(s.fetch_error, "query failed");
        assert_eq!(s.status.error, "query failed");

        let mut s = state("select * from logs limit 5000");
        let first = windows(&s.init());
        complete(&mut s, &first[0], 500);
        s.global_offset = 499;
        let next = windows(&s.update(Msg::Key(key(KeyCode::Down)), fixed_now()));
        s.update(
            Msg::Window(WindowCompletion {
                run_id: next[0].run_id,
                window_index: next[0].window_index,
                result: Err("boom".into()),
            }),
            fixed_now(),
        );
        assert_eq!(s.window_fetch_error, "boom");
        assert_eq!(s.status.error, "window fetch failed");
        assert!(s.fetch_error.is_empty());

        let mut s = state("select * from logs");
        let first = windows(&s.init());
        complete(&mut s, &first[0], 7);
        assert_eq!(s.actual_total, 7);
    }

    #[test]
    fn ctrl_d_and_ctrl_b_take_precedence_over_editor_bindings() {
        let mut s = state("select * from logs");
        s.init();
        let before = s.editor.value();
        assert_eq!(
            s.update(Msg::Key(ctrl('d')), fixed_now()),
            vec![Effect::FetchDatasets]
        );
        assert_eq!(s.overlay, Overlay::Dataset);
        assert_eq!(s.editor.value(), before);
        s.update(Msg::Key(key(KeyCode::Esc)), fixed_now());
        assert!(s.update(Msg::Key(ctrl('b')), fixed_now()).is_empty());
        assert_eq!(s.editor.value(), before);
    }

    #[test]
    fn dataset_overlay_swallows_ctrl_c_and_selection_inserts_placeholder_query() {
        let mut s = state("");
        s.init();
        s.update(
            Msg::Datasets(DatasetsCompletion {
                result: Ok(vec!["alpha".into(), "beta".into()]),
            }),
            fixed_now(),
        );
        s.update(Msg::Key(ctrl('d')), fixed_now());
        assert!(
            !s.update(Msg::Key(ctrl('c')), fixed_now())
                .contains(&Effect::Quit)
        );
        s.update(Msg::Key(key(KeyCode::Down)), fixed_now());
        let effects = s.update(Msg::Key(key(KeyCode::Enter)), fixed_now());
        assert_eq!(effects, vec![Effect::FetchSchema(b"beta".to_vec())]);
        assert_eq!(s.editor.value(), EMPTY_EDITOR_QUERY);
        assert_eq!(s.dataset, "beta");
        // The run substitutes the placeholder with the quoted selection.
        let run = windows(&s.update(Msg::Key(ctrl('r')), fixed_now()));
        assert_eq!(run[0].query, "SELECT * FROM 'beta' LIMIT 100 OFFSET 0");
    }

    #[test]
    fn late_schema_for_previous_dataset_still_applies() {
        let mut s = state("");
        s.update(
            Msg::Datasets(DatasetsCompletion {
                result: Ok(vec!["a".into(), "b".into()]),
            }),
            fixed_now(),
        );
        s.update(Msg::Key(ctrl('d')), fixed_now());
        s.update(Msg::Key(key(KeyCode::Down)), fixed_now());
        s.update(Msg::Key(key(KeyCode::Enter)), fixed_now());
        s.update(
            Msg::Schema(SchemaCompletion {
                columns: vec!["from_a".into()],
                error: String::new(),
            }),
            fixed_now(),
        );
        assert_eq!(s.dataset, "b");
        assert_eq!(s.all_columns, vec!["from_a".to_string()]);
    }

    #[test]
    fn time_overlay_enter_applies_and_runs_escape_discards() {
        let mut s = state("select * from logs");
        s.init();
        s.focused = 1;
        s.focus_selected();
        s.update(Msg::Key(key(KeyCode::Enter)), fixed_now());
        assert_eq!(s.overlay, Overlay::Time);
        assert_eq!(s.time_edit.list_index, 1, "1 hour preset");
        s.update(Msg::Key(key(KeyCode::Down)), fixed_now());
        s.update(Msg::Key(key(KeyCode::Esc)), fixed_now());
        assert_eq!(s.time.start_value_utc(), "2026-10-14T11:00:00Z");
        s.update(Msg::Key(key(KeyCode::Enter)), fixed_now());
        s.update(Msg::Key(key(KeyCode::Down)), fixed_now());
        let effects = s.update(Msg::Key(key(KeyCode::Enter)), fixed_now());
        assert_eq!(s.time.start_value_utc(), "2026-10-14T07:00:00Z");
        assert_eq!(windows(&effects)[0].start_utc, "2026-10-14T07:00:00Z");
    }

    #[test]
    fn ctrl_r_inside_time_overlay_runs_without_applying_draft() {
        let mut s = state("select * from logs");
        s.init();
        s.focused = 1;
        s.update(Msg::Key(key(KeyCode::Enter)), fixed_now());
        s.update(Msg::Key(key(KeyCode::Down)), fixed_now());
        let effects = s.update(Msg::Key(ctrl('r')), fixed_now());
        assert_eq!(s.overlay, Overlay::None);
        assert_eq!(windows(&effects)[0].start_utc, "2026-10-14T11:00:00Z");
    }

    #[test]
    fn spinner_accepts_current_tag_only_while_loading() {
        let mut s = state("select * from logs");
        s.init();
        assert_eq!(
            s.update(Msg::SpinnerTick(0), fixed_now()),
            vec![Effect::SpinnerTick {
                tag: 1,
                delayed: true
            }]
        );
        assert!(s.update(Msg::SpinnerTick(0), fixed_now()).is_empty());
        s.loading = false;
        assert!(s.update(Msg::SpinnerTick(1), fixed_now()).is_empty());
    }

    #[test]
    fn column_drawer_typing_filters_and_enter_inserts_quoted_name() {
        let mut s = state("");
        s.all_columns = vec!["Status".into(), "host".into()];
        s.filtered_columns = s.all_columns.clone();
        s.focused = 3;
        s.focus_selected();
        s.update(Msg::Key(runes("ho")), fixed_now());
        assert_eq!(s.filtered_columns, vec!["host".to_string()]);
        s.update(Msg::Key(key(KeyCode::Enter)), fixed_now());
        assert_eq!(s.editor.value(), "\"host\" ");
        assert_eq!(s.current_focus(), Focus::Query);
    }

    #[test]
    fn plan_and_window_injection_match_go_helpers() {
        assert_eq!(
            build_query_plan("select 1 from t limit 20 offset 5;", 500),
            QueryPlan {
                base: "select 1 from t".into(),
                limit: 20,
                offset: 5
            }
        );
        assert_eq!(
            build_query_plan("select (select 1 limit 2) from t", 500).limit,
            500
        );
        assert_eq!(
            inject_sql_window("select * from t limit 9;", 500, 1000),
            "select * from t LIMIT 500 OFFSET 1000"
        );
        assert_eq!(
            resolve_dataset_placeholder("select * from dataset limit 1", "a-b").unwrap(),
            "select * from 'a-b' limit 1"
        );
        assert_eq!(
            resolve_dataset_placeholder("select * from datasets", "x").unwrap(),
            "select * from datasets"
        );
        assert_eq!(
            extract_dataset("select * from \"a\"\"b\" x").unwrap(),
            "a\"b"
        );
        assert_eq!(extract_dataset("select 1").unwrap(), "—");
        assert_eq!(
            quote_unsafe_sql_field_names("select StatusCode, \"a\"\"B\", count(X) from t"),
            "select \"StatusCode\", \"a\"\"B\", count(X) from t"
        );
    }

    #[test]
    fn timestamps_display_in_selected_zone() {
        assert_eq!(
            format_timestamp_hms("2026-10-14T10:11:12.5Z", Zone::Utc, false),
            "10:11:12"
        );
        assert_eq!(
            format_timestamp_hms(
                "2026-10-14 10:11:12",
                Zone::Fixed(FixedOffset::east_opt(3600).unwrap()),
                false
            ),
            "11:11:12"
        );
        assert_eq!(trim_timestamp_to_hms("2026-10-14Tbogus"), "bogus");
    }

    #[test]
    fn datetime_segments_edit_like_go() {
        let t = GoTime {
            instant: fixed_now(),
            zone: Zone::Utc,
        };
        let mut input = DateTimeInput::new(t);
        assert_eq!(input.local_value(), "2026-Oct-14 12:00:00");
        assert_eq!(input.pos, 20);
        input.focused = true;
        input.focus_first_segment();
        input.update(&key(KeyCode::Right));
        input.update(&key(KeyCode::Up));
        assert_eq!(input.local_value(), "2026-Nov-14 12:00:00");
        input.update(&key(KeyCode::Ctrl(5)));
        assert_eq!(input.pos, 20);
        assert_eq!(segment_index(20), 5);
    }

    #[test]
    fn cli_time_keeps_parse_location() {
        assert_eq!(
            parse_cli_time("2026-01-01T00:00:00Z").unwrap().zone,
            Zone::Utc
        );
        let fixed = parse_cli_time("2026-01-01T00:00:00+05:30").unwrap();
        if Local
            .offset_from_utc_datetime(&fixed.instant.naive_utc())
            .local_minus_utc()
            != 19800
        {
            assert_eq!(
                fixed.zone,
                Zone::Fixed(FixedOffset::east_opt(19800).unwrap())
            );
        }
        assert_eq!(parse_cli_time("1h").unwrap().zone, Zone::Local);
        assert!(parse_cli_time("bogus").is_err());
    }

    #[test]
    fn sql_result_headers_use_pinned_go_simple_uppercase() {
        for (field, expected) in [
            ("ɤ", "ɤ"),
            ("ƛ", "ƛ"),
            ("\u{10d70}", "\u{10d70}"),
            ("http_status", "HTTP_STATUS"),
            ("straße", "STRAßE"),
            ("ﬀ", "ﬀ"),
            ("ςσ", "ΣΣ"),
            ("ǅ", "Ǆ"),
        ] {
            let mut s = state("select * from logs");
            let request = windows(&s.init()).remove(0);
            s.update(
                Msg::Window(WindowCompletion {
                    run_id: request.run_id,
                    window_index: request.window_index,
                    result: Ok(QueryData {
                        fields: vec![field.into()],
                        records: vec![Record::from([(field.into(), Cell::Str("sample".into()))])],
                    }),
                }),
                fixed_now(),
            );
            assert_eq!(s.table.columns[0].title, expected, "header for {field:?}");
            assert!(
                s.table.render_headers().contains(expected),
                "rendered header for {field:?}"
            );
            assert_eq!(
                s.table.columns[0].key, field,
                "column lookup keeps the original name"
            );
        }
    }

    #[test]
    fn columns_put_timestamp_first_and_tags_last() {
        let mut s = state("");
        let mut record = Record::new();
        record.insert("msg".into(), Cell::Str("hello world".into()));
        s.update_table_columns(
            &[
                "p_tags".into(),
                "msg".into(),
                "p_timestamp".into(),
                "n".into(),
            ],
            &[record],
        );
        let titles: Vec<_> = s.table.columns.iter().map(|c| c.title.clone()).collect();
        assert_eq!(titles, ["TIME [LOCAL]", "MSG", "N", "P_TAGS"]);
        let widths: Vec<_> = s.table.columns.iter().map(|c| c.width).collect();
        assert_eq!(widths, [12, 12, 3, 6]);
    }

    #[test]
    fn go_float_display_through_shared_formatter() {
        assert_eq!(crate::metrics::go_float(1_000_000.0), "1e+06");
    }

    /// Scripted terminal queue counting how often the head was consumed.
    struct ScriptedSource {
        queue: std::collections::VecDeque<(Instant, Event)>,
        closed: bool,
        taken: usize,
    }
    impl EventSource for ScriptedSource {
        fn peek_stamp(&self) -> Result<Option<Instant>, Closed> {
            match self.queue.front() {
                Some((stamp, _)) => Ok(Some(*stamp)),
                None if self.closed => Err(Closed),
                None => Ok(None),
            }
        }
        fn take_event(&mut self) -> Result<Option<(Instant, Event)>, Closed> {
            match self.queue.pop_front() {
                Some(event) => {
                    self.taken += 1;
                    Ok(Some(event))
                }
                None if self.closed => Err(Closed),
                None => Ok(None),
            }
        }
    }
    fn scripted(events: Vec<(Instant, Event)>) -> ScriptedSource {
        ScriptedSource {
            queue: events.into(),
            closed: false,
            taken: 0,
        }
    }

    #[test]
    fn older_completion_wins_without_accepting_a_later_quit_or_control_key() {
        let base = Instant::now();
        let later = base + std::time::Duration::from_millis(5);
        for event in [Event::Quit, Event::Key(ctrl('c'))] {
            let mut source = scripted(vec![(later, event.clone())]);
            let mut done = Some((base, Msg::SpinnerTick(7)));
            assert!(matches!(
                next_step(&mut source, &mut done),
                Step::Completion(Msg::SpinnerTick(7))
            ));
            // The terminal head was only peeked: still queued, never taken.
            assert_eq!(source.taken, 0);
            assert_eq!(source.queue.front().map(|(_, e)| e), Some(&event));
            // With the completion consumed, the terminal head now wins.
            assert!(matches!(next_step(&mut source, &mut done), Step::Event(e) if e == event));
            assert_eq!(source.taken, 1);
        }
    }

    #[test]
    fn older_terminal_event_is_taken_before_a_later_completion() {
        let base = Instant::now();
        let later = base + std::time::Duration::from_millis(5);
        let mut source = scripted(vec![(base, Event::Key(runes("b")))]);
        let mut done = Some((later, Msg::SpinnerTick(1)));
        assert!(matches!(
            next_step(&mut source, &mut done),
            Step::Event(Event::Key(_))
        ));
        assert!(done.is_some(), "the completion head stays pending");
        // Equal stamps go to the terminal.
        let mut source = scripted(vec![(later, Event::Quit)]);
        assert!(matches!(
            next_step(&mut source, &mut done),
            Step::Event(Event::Quit)
        ));
    }

    #[test]
    fn empty_and_closed_sources() {
        let mut source = scripted(Vec::new());
        let mut done = None;
        assert!(matches!(next_step(&mut source, &mut done), Step::Wait));
        source.closed = true;
        let mut done = Some((Instant::now(), Msg::SpinnerTick(2)));
        assert!(matches!(
            next_step(&mut source, &mut done),
            Step::Completion(Msg::SpinnerTick(2))
        ));
        assert!(matches!(next_step(&mut source, &mut done), Step::Closed));
        assert_eq!(source.taken, 0);
    }

    #[test]
    fn reflow_truncate_drops_zero_width_fill_after_the_cut() {
        // ui.Truncate of a 20-rune CJK title at 33 cells keeps 12 NUL runes
        // from Go's zeroed rune buffer; the pane clip must not emit them.
        let wide = "\u{6d4b}".repeat(20);
        let fill = format!("  {wide}{}\u{2026}", "\0".repeat(12));
        let clipped = reflow_truncate(&fill, 33, "");
        assert!(!clipped.contains('\0'));
        assert_eq!(clipped, format!("  {}", "\u{6d4b}".repeat(15)));
        assert_eq!(reflow_truncate("abc", 5, ""), "abc");
        assert_eq!(
            reflow_truncate("\x1b[1mabcdef", 3, "…"),
            "\x1b[1mab…\x1b[0m"
        );
        assert_eq!(reflow_width("\x1b[31mab\x1b[0m"), 2);
    }

    #[test]
    fn reflow_terminators_include_at_sign() {
        // ESC [ 2 @ (ICH) ends at '@', so "abc" counts and is kept.
        assert_eq!(reflow_width("\x1b[2@abc"), 3);
        assert_eq!(reflow_truncate("\x1b[2@abcdef", 2, ""), "\x1b[2@ab");
        // '[' (0x5b) is not a terminator; '`' (0x60) neither.
        assert_eq!(reflow_width("\x1b[`x"), 0);
    }

    #[test]
    fn reflow_drops_unterminated_sequences() {
        // ansi.Writer buffers escape bytes until a terminator: a dangling CSI
        // at the end of input is never emitted.
        assert_eq!(reflow_truncate("ab\x1b[3", 5, ""), "ab");
        // A dangling sequence open at the cut is also dropped.
        assert_eq!(reflow_truncate("ab\x1b[3", 1, "~"), "~");
    }

    #[test]
    fn reflow_nested_escape_extends_the_open_sequence() {
        let clipped = reflow_truncate("\x1b[\x1b[1mabc", 2, "");
        assert_eq!(clipped, "\x1b[\x1b[1mab\x1b[0m");
        assert_eq!(reflow_width("\x1b[\x1b[1mabc"), 3);
    }

    #[test]
    fn reflow_reset_follows_tail_only_for_an_open_sgr() {
        // Reset seen before the cut: no trailing reset.
        assert_eq!(
            reflow_truncate("\x1b[31ma\x1b[0mbc", 2, ""),
            "\x1b[31ma\x1b[0mb"
        );
        // A non-SGR sequence alone records nothing to reset.
        assert_eq!(reflow_truncate("\x1b[Kabc", 2, ""), "\x1b[Kab");
        // Tail wider than the limit: the tail alone.
        assert_eq!(reflow_truncate("abc", 0, "…"), "…");
    }

    /// A two-period zone: `before` seconds east of UTC until `switch` (UTC),
    /// `after` from then on.
    fn zone(switch: &str, before: i64, after: i64) -> impl Fn(NaiveDateTime) -> i64 {
        let switch = NaiveDateTime::parse_from_str(switch, "%Y-%m-%d %H:%M").unwrap();
        move |utc| if utc < switch { before } else { after }
    }
    fn wall(text: &str) -> NaiveDateTime {
        NaiveDateTime::parse_from_str(text, "%Y-%m-%d %H:%M").unwrap()
    }

    #[test]
    fn go_date_resolves_skipped_and_repeated_wall_times() {
        // America/New_York 2024: EST -5h until 03-10 07:00Z, EDT -4h until 11-03 06:00Z.
        let ny_spring = zone("2024-03-10 07:00", -5 * 3600, -4 * 3600);
        let ny_fall = zone("2024-11-03 06:00", -4 * 3600, -5 * 3600);
        // Skipped 02:30 -> 06:30Z (01:30 EST), not 07:30Z.
        assert_eq!(
            go_date_utc(wall("2024-03-10 02:30"), &ny_spring),
            wall("2024-03-10 06:30")
        );
        // Repeated 01:30 -> 05:30Z (the EDT occurrence).
        assert_eq!(
            go_date_utc(wall("2024-11-03 01:30"), &ny_fall),
            wall("2024-11-03 05:30")
        );
        // Europe/Berlin 2024: CET +1h until 03-31 01:00Z, CEST +2h until 10-27 01:00Z.
        let berlin_spring = zone("2024-03-31 01:00", 3600, 2 * 3600);
        let berlin_fall = zone("2024-10-27 01:00", 2 * 3600, 3600);
        // Skipped 02:30 -> 01:30Z (03:30 CEST).
        assert_eq!(
            go_date_utc(wall("2024-03-31 02:30"), &berlin_spring),
            wall("2024-03-31 01:30")
        );
        // Repeated 02:30 -> 01:30Z: the LATER (CET) occurrence, unlike chrono's earliest.
        assert_eq!(
            go_date_utc(wall("2024-10-27 02:30"), &berlin_fall),
            wall("2024-10-27 01:30")
        );
        // Europe/London repeated 01:30: offset 0 at the wall time, so Go keeps it (GMT).
        let london_fall = zone("2024-10-27 01:00", 3600, 0);
        assert_eq!(
            go_date_utc(wall("2024-10-27 01:30"), &london_fall),
            wall("2024-10-27 01:30")
        );
        // Ordinary times are unaffected.
        assert_eq!(
            go_date_utc(wall("2024-06-01 12:00"), &ny_fall),
            wall("2024-06-01 16:00")
        );
    }

    #[test]
    fn go_lowering_offsets_slice_the_original_bytes() {
        // KELVIN SIGN lowers 3 -> 1 byte: Go's offset lands 2 bytes early, so
        // rest is "m dataset limit 5": no substitution, extract returns "m".
        let kelvin = "select '\u{212a}' as k from dataset limit 5";
        assert_eq!(resolve_dataset_placeholder(kelvin, "logs").unwrap(), kelvin);
        assert_eq!(extract_dataset(kelvin).unwrap(), "m");
        // 'İ' lowers 2 -> 1: substitution happens without the space.
        assert_eq!(
            resolve_dataset_placeholder("select '\u{130}' from dataset", "logs").unwrap(),
            "select '\u{130}' from'logs'"
        );
        // 'Ⱥ' lowers 2 -> 3: the offset lands 1 byte late ("ataset").
        let grow = "select '\u{23a}' from dataset";
        assert_eq!(resolve_dataset_placeholder(grow, "logs").unwrap(), grow);
        assert_eq!(extract_dataset(grow).unwrap(), "ataset");
        // ASCII stays aligned.
        assert_eq!(
            resolve_dataset_placeholder("SELECT * FROM Dataset", "logs").unwrap(),
            "SELECT * FROM 'logs'"
        );
    }

    #[test]
    fn go_lowering_cut_inside_a_rune_yields_one_replacement_per_byte() {
        // The late offset starts rest on the 2nd byte of U+6D4B: two invalid
        // bytes, each one U+FFFD (json.Marshal's per-byte replacement).
        let query = "select '\u{23a}' from \u{6d4b}\u{8bd5}";
        assert_eq!(extract_dataset(query).unwrap(), "\u{fffd}\u{fffd}\u{8bd5}");
    }

    #[test]
    fn go_lowering_past_the_end_panics_like_go() {
        // Trailing " from " with one growing rune: Go slices sql[18:] of 17 bytes.
        let query = "select '\u{23a}' from ";
        let panic = extract_dataset(query).unwrap_err();
        assert_eq!(
            panic.to_string(),
            "runtime error: slice bounds out of range [18:17]"
        );
        assert_eq!(
            resolve_dataset_placeholder(query, "logs")
                .unwrap_err()
                .to_string(),
            "runtime error: slice bounds out of range [18:17]"
        );
        // Reached in View (empty dataset) it is a recovered Update/View panic.
        let mut s = state("");
        s.editor.set_value(query);
        s.dataset.clear();
        let _ = s.view(fixed_now());
        assert_eq!(
            s.take_fault(),
            Some(Fault::Go(
                "runtime error: slice bounds out of range [18:17]".into()
            ))
        );
        // Before tea.Program.Run (NewQueryModel) it is unrecovered.
        assert!(new_model_preflight(query).is_err());
        assert!(new_model_preflight("select '\u{23a}' from dataset limit 1").is_ok());
    }

    #[test]
    fn go_lowering_keeps_runes_newer_than_go_unicode_tables() {
        // Rust maps these (Unicode 16) but go1.25.4 does not; their byte
        // width would otherwise shift Go's offset (3 -> 2 bytes).
        assert_eq!(crate::tui::unicode::lower_rune('\u{a7cb}'), '\u{a7cb}');
        assert_eq!(crate::tui::unicode::lower_rune('\u{a7dc}'), '\u{a7dc}');
        assert_eq!(crate::tui::unicode::lower_rune('\u{130}'), 'i');
        assert_eq!(crate::tui::unicode::lower_rune('\u{212a}'), 'k');
        let query = "select '\u{a7cb}' from dataset";
        assert_eq!(
            resolve_dataset_placeholder(query, "logs").unwrap(),
            "select '\u{a7cb}' from 'logs'"
        );
    }

    #[test]
    fn extracted_raw_dataset_bytes_reach_the_schema_effect() {
        // Ctrl+L with no dataset: extractDataset cuts inside U+6D4B, so the
        // schema request must carry Go's raw bytes, not U+FFFD.
        let mut s = state("");
        s.editor.set_value("select '\u{23a}' from \u{6d4b}\u{8bd5}");
        s.columns_drawer_open = false;
        let effects = s.update(Msg::Key(ctrl('l')), fixed_now());
        let mut expected = vec![0xb5, 0x8b];
        expected.extend_from_slice("\u{8bd5}".as_bytes());
        assert_eq!(effects, vec![Effect::FetchSchema(expected.clone())]);
        assert_eq!(s.dataset, "\u{fffd}\u{fffd}\u{8bd5}");
        assert_eq!(s.dataset_bytes(), expected);
        // A list completion replacing the name drops the raw bytes.
        s.update(
            Msg::Datasets(DatasetsCompletion {
                result: Ok(vec!["logs".into()]),
            }),
            fixed_now(),
        );
        assert_eq!(s.dataset, "logs");
        assert_eq!(s.dataset_bytes(), b"logs".to_vec());
    }

    #[test]
    fn late_dataset_list_does_not_alias_a_raw_name_to_its_replacement_title() {
        let mut s = state("");
        s.editor.set_value("select '\u{23a}' from \u{6d4b}\u{8bd5}");
        s.columns_drawer_open = false;
        let raw = vec![0xb5, 0x8b, 0xe8, 0xaf, 0x95];
        assert_eq!(
            s.update(Msg::Key(ctrl('l')), fixed_now()),
            vec![Effect::FetchSchema(raw.clone())]
        );
        let replacement_title = "\u{fffd}\u{fffd}\u{8bd5}";
        assert_eq!(s.dataset, replacement_title);
        assert_eq!(s.dataset_raw.as_deref(), Some(raw.as_slice()));
        // A completed empty schema leaves the drawer open and idle before
        // the outstanding dataset list arrives (schemaMsg has no guard).
        assert!(
            s.update(
                Msg::Schema(SchemaCompletion {
                    columns: Vec::new(),
                    error: String::new(),
                }),
                fixed_now(),
            )
            .is_empty()
        );
        assert!(s.columns_drawer_open && !s.columns_loading && s.all_columns.is_empty());
        let titles = vec!["logs".to_owned(), replacement_title.to_owned()];
        let effects = s.update(
            Msg::Datasets(DatasetsCompletion {
                result: Ok(titles.clone()),
            }),
            fixed_now(),
        );
        assert_eq!(s.all_datasets, titles);
        assert_eq!(s.filtered_datasets, titles);
        assert_eq!(s.dataset_index, 0);
        assert_eq!(s.dataset, "logs");
        assert!(s.dataset_raw.is_none());
        assert_eq!(s.dataset_bytes(), b"logs".to_vec());
        assert_eq!(effects, vec![Effect::FetchSchema(b"logs".to_vec())]);
        assert!(s.columns_loading && !s.datasets_loading);
    }

    #[test]
    fn dataset_picker_replacement_title_changes_raw_identity_and_resets_columns() {
        let mut s = state("");
        s.editor.set_value("select '\u{23a}' from \u{6d4b}\u{8bd5}");
        s.columns_drawer_open = false;
        s.update(Msg::Key(ctrl('l')), fixed_now());
        s.update(
            Msg::Schema(SchemaCompletion {
                columns: vec!["raw_a".into(), "raw_b".into()],
                error: String::new(),
            }),
            fixed_now(),
        );
        s.selected_column = "raw_b".into();
        s.column_filter.set_value("raw_b");
        s.filtered_columns = vec!["raw_b".into()];
        let replacement_title = "\u{fffd}\u{fffd}\u{8bd5}";
        assert_eq!(s.dataset, replacement_title);
        assert!(s.dataset_raw.is_some());
        // Ctrl+D leaves the existing picker list available during its fetch.
        s.all_datasets = vec!["logs".into(), replacement_title.into()];
        s.filtered_datasets = s.all_datasets.clone();
        assert_eq!(
            s.update(Msg::Key(ctrl('d')), fixed_now()),
            vec![Effect::FetchDatasets]
        );
        s.update(Msg::Key(key(KeyCode::Down)), fixed_now());
        assert_eq!(s.dataset_index, 1);
        let effects = s.update(Msg::Key(key(KeyCode::Enter)), fixed_now());
        assert_eq!(s.dataset, replacement_title);
        assert!(s.dataset_raw.is_none());
        assert_eq!(s.dataset_bytes(), replacement_title.as_bytes());
        assert!(s.selected_column.is_empty());
        assert!(s.column_filter.value().is_empty());
        assert!(s.all_columns.is_empty() && s.filtered_columns.is_empty());
        assert!(s.columns_loading);
        assert_eq!(
            effects,
            vec![Effect::FetchSchema(replacement_title.as_bytes().to_vec())]
        );
        assert_eq!(s.overlay, Overlay::None);
        assert_eq!(s.current_focus(), Focus::Query);

        // Once this valid title is current, choosing it again keeps its
        // populated schema and sends no redundant schema effect.
        s.update(
            Msg::Schema(SchemaCompletion {
                columns: vec!["valid_column".into()],
                error: String::new(),
            }),
            fixed_now(),
        );
        s.update(Msg::Key(ctrl('d')), fixed_now());
        assert!(
            s.update(Msg::Key(key(KeyCode::Enter)), fixed_now())
                .is_empty()
        );
        assert_eq!(s.all_columns, vec!["valid_column".to_owned()]);
        assert!(!s.columns_loading && s.dataset_raw.is_none());
    }

    #[test]
    fn window_fetch_panic_becomes_query_failed_for_the_same_window() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        async fn injected_panic() -> WindowCompletion {
            panic!("injected window fetch panic")
        }
        let recovered = runtime.block_on(recover_window(7, 3, injected_panic()));
        assert_eq!(
            recovered,
            Some(WindowCompletion {
                run_id: 7,
                window_index: 3,
                result: Err("query failed".into()),
            })
        );
        // A completed fetch passes through unchanged.
        let done = WindowCompletion {
            run_id: 7,
            window_index: 4,
            result: Ok(QueryData::default()),
        };
        let expected = done.clone();
        assert_eq!(
            runtime.block_on(recover_window(7, 4, async move { done })),
            Some(expected)
        );
        // The reducer then treats it as Go's failed window: run guard kept.
        let mut s = state("select * from logs");
        let first = windows(&s.init());
        let failed = runtime
            .block_on(recover_window(first[0].run_id, 0, injected_panic()))
            .unwrap();
        s.update(Msg::Window(failed), fixed_now());
        assert_eq!(s.fetch_error, "query failed");
        assert_eq!(s.status.error, "query failed");
    }

    #[test]
    fn raw_extracted_dataset_renders_a_byte_frame_with_go_bytes() {
        // Empty selection: the sidebar falls back to extractDataset, which
        // cuts inside U+6D4B. Go keeps B5 8B through the sidebar layout; the
        // shared byte render (ansi.Wrap) then decides C1 handling.
        let mut s = state("");
        s.editor.set_value("select '\u{23a}' from \u{6d4b}\u{8bd5}");
        let Frame::Bytes(frame) = s.view(fixed_now()) else {
            panic!("raw dataset must produce a byte frame");
        };
        // Sol's actual Go value bytes: raw B5, U+008B from ansi.Wrap, then 试.
        let value = [0xb5, 0xc2, 0x8b, 0xe8, 0xaf, 0x95];
        assert!(frame.windows(value.len()).any(|w| w == value));
        assert!(!frame.windows(3).any(|w| w == [0xef, 0xbf, 0xbd]));
        // A valid dataset stays on the ordinary text path.
        let mut s = state("");
        s.editor.set_value("select * from logs");
        assert!(matches!(s.view(fixed_now()), Frame::Text(_)));
        // Overlays hide the sidebar: text.
        let mut s = state("");
        s.editor.set_value("select '\u{23a}' from \u{6d4b}\u{8bd5}");
        s.overlay = Overlay::Dataset;
        assert!(matches!(s.view(fixed_now()), Frame::Text(_)));
    }

    #[test]
    fn pane_body_bytes_pad_by_go_width_and_clip_like_reflow() {
        let border = Style::new();
        // B5 8B count 0 in x/ansi width; only the CJK rune counts 2.
        let mut line = b"  ".to_vec();
        line.extend([0xb5, 0x8b]);
        line.extend("\u{8bd5}".as_bytes());
        let out = pane_body_lines_bytes(&line, 10, 1, &border);
        // text width 6: "  " + 2 cells = 4, pad 2.
        let mut expected = "│ ".as_bytes().to_vec();
        expected.extend(&line);
        expected.extend("   │".as_bytes());
        assert_eq!(out[0], expected);
        // Clipping goes through reflow, which re-encodes invalid bytes.
        assert_eq!(
            reflow_truncate_bytes(&[0xb5, b'a', b'b'], 2, ""),
            "\u{fffd}a".as_bytes()
        );
    }
}
