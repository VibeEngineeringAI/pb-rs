//! Terminal session lifetime with Bubble Tea v1.2.2 (`tea.Program.Run`)
//! acquisition, rendering and restoration semantics.
//!
//! * Input is stdin when it is a terminal, otherwise `/dev/tty` (Go opens it
//!   too). Only a terminal input is put in raw mode (x/term MakeRaw).
//! * Output is always stdout; size and SIGWINCH are used only when stdout is
//!   a terminal. Frames go through a dedicated writer thread that applies the
//!   standard renderer's diff algorithm, so `draw` never blocks the caller
//!   (and never the Tokio driver) on the terminal.
//! * Messages follow Go's senders on the unbuffered `p.msgs`: the input
//!   reader offers one decoded message at a time. It may read ahead and hold
//!   the next message while awaiting acceptance; the signal sender (Quit), the
//!   initial size check and the resize sender are independent and may overtake keys the reader
//!   has not yet handed off. Every message carries the instant it entered
//!   its lane so runners can merge their own completions in arrival order.
//! * SIGINT/SIGTERM become `Event::Quit` (tea.QuitMsg); their dispositions
//!   are restored when the Quit is accepted, as Go's deferred signal.Stop
//!   runs after the send. SIGQUIT reproduces the Go runtime's termination
//!   (`SIGQUIT: quit`, exit 2, no restoration). SIGHUP and the rest keep
//!   their default disposition (characterized by the lifecycle oracle).
//! * Every acquired step is recorded in one process-wide `Restore` record.
//!   `close` restores in Go's shutdown order and reports the termios error;
//!   `Drop` (tea Kill: no final frame) and the chained panic hook restore on
//!   a best-effort, idempotent basis. A panic outside a `recovered` scope
//!   restores first and then delivers `Event::Panicked` ahead of the
//!   terminal's pending messages; its stamp is the time it is peeked, so a
//!   runner merging by stamp may first process earlier-stamped completions
//!   (Go's eventLoop `select` has the same freedom). SIGQUIT stays handled
//!   until termios is restored. RAII cannot help after SIGKILL/abort.

use super::input::Event;
use std::cell::Cell;
use std::fmt;
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError, Weak};
use std::task::{Context, Poll};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

/// Screen mode: inline (tea default) or the alternate screen (tea.WithAltScreen()).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Screen {
    Inline,
    Fullscreen,
}

/// Terminal dimensions in cells.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Size {
    pub(crate) width: u16,
    pub(crate) height: u16,
}

/// A lifecycle failure whose Display is the text tea.Program.Run returns.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct SessionError {
    message: String,
}

impl SessionError {
    fn new(message: impl Into<String>) -> SessionError {
        SessionError {
            message: message.into(),
        }
    }
}

impl fmt::Display for SessionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for SessionError {}

/// Every producer has stopped and every pending message was taken.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Closed;

/// Go's syscall.Errno text: strerror without the Rust " (os error N)" suffix
/// and with a lowercase first letter (matches Linux and macOS tables for the
/// errors seen here).
pub(crate) fn go_errno(error: &std::io::Error) -> String {
    let text = error.to_string();
    let mut message = text
        .split(" (os error ")
        .next()
        .unwrap_or(&text)
        .to_string();
    if let Some(first) = message.get_mut(..1) {
        first.make_ascii_lowercase();
    }
    message
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

// ---------------------------------------------------------------------------
// Message lanes: Go's independent senders on one unbuffered channel.

/// A message with the instant its sender offered it (entered Go's send
/// queue). Runners merge their own completions, stamped at handoff, by
/// taking the earliest head; this approximates, and does not universally
/// guarantee, Go's interleaving.
pub(crate) type Stamped = (Instant, Event);

/// How often blocking consumers re-check the panic flag (the hook cannot
/// take the lanes lock safely, so its wakeup may race a consumer's wait).
const KILL_POLL: Duration = Duration::from_millis(100);

#[derive(Default)]
struct Lanes {
    /// readLoop: one decoded message awaiting acceptance. The reader may hold
    /// one further decoded message while blocked on this occupied lane.
    input: Option<Stamped>,
    /// The one-shot startup `go p.checkResize()`.
    initial_resize: Option<Stamped>,
    /// listenForResize: one pending WindowSizeMsg...
    resize: Option<Stamped>,
    /// ...plus Go's one-slot SIGWINCH channel: a further SIGWINCH while the
    /// resize is pending triggers one more size check after acceptance.
    resize_again: bool,
    /// handleSignals: the single QuitMsg.
    quit: Option<Stamped>,
    producers: usize,
    stopping: bool,
    panic_delivered: bool,
}

impl Lanes {
    /// Index of the earliest pending lane; ties keep lane order
    /// (input, initial resize, resize, quit).
    fn earliest(&self) -> Option<(usize, Instant)> {
        [&self.input, &self.initial_resize, &self.resize, &self.quit]
            .iter()
            .enumerate()
            .filter_map(|(index, slot)| slot.as_ref().map(|(at, _)| (index, *at)))
            .min_by_key(|&(index, at)| (at, index))
    }
}

struct Queue {
    lanes: Mutex<Lanes>,
    readable: Condvar,
    writable: Condvar,
    notify: tokio::sync::Notify,
    killed: AtomicBool,
    panic_message: Mutex<Option<String>>,
    /// Output to re-query for a coalesced SIGWINCH (stdout terminal only).
    resize_source: Option<sys::Output>,
}

impl Queue {
    fn new(resize_source: Option<sys::Output>) -> Queue {
        Queue {
            lanes: Mutex::default(),
            readable: Condvar::new(),
            writable: Condvar::new(),
            notify: tokio::sync::Notify::new(),
            killed: AtomicBool::new(false),
            panic_message: Mutex::new(None),
            resize_source,
        }
    }

    fn wake_consumer(&self) {
        self.readable.notify_all();
        self.notify.notify_one();
    }

    /// Reader handoff: blocks until the previous input message was taken.
    /// The stamp is taken once the lane is free, when Go's send would begin.
    fn push_input(&self, event: Event) -> bool {
        let mut lanes = lock(&self.lanes);
        while lanes.input.is_some() && !lanes.stopping {
            lanes = self
                .writable
                .wait(lanes)
                .unwrap_or_else(PoisonError::into_inner);
        }
        if lanes.stopping {
            return false;
        }
        lanes.input = Some((Instant::now(), event));
        drop(lanes);
        self.wake_consumer();
        true
    }

    fn push_initial_resize(&self, event: Event) {
        lock(&self.lanes).initial_resize = Some((Instant::now(), event));
        self.wake_consumer();
    }

    /// Never blocks: a SIGWINCH while one resize is pending is coalesced.
    /// (The Windows reader delivers console resizes as input instead.)
    #[cfg(any(unix, test))]
    fn push_resize(&self, event: Event) {
        let mut lanes = lock(&self.lanes);
        if lanes.resize.is_some() {
            lanes.resize_again = true;
            return;
        }
        lanes.resize = Some((Instant::now(), event));
        drop(lanes);
        self.wake_consumer();
    }

    /// SIGINT/SIGTERM; Windows has no such sender yet.
    #[cfg(any(unix, test))]
    fn push_quit(&self) {
        lock(&self.lanes).quit = Some((Instant::now(), Event::Quit));
        self.wake_consumer();
    }

    fn producer_started(&self) {
        lock(&self.lanes).producers += 1;
    }

    fn producer_done(&self) {
        lock(&self.lanes).producers -= 1;
        self.wake_consumer();
    }

    fn stop(&self) {
        lock(&self.lanes).stopping = true;
        self.writable.notify_all();
    }

    /// Panic hook: never blocks (the panicking thread may hold any lock).
    fn kill(&self, message: String) {
        if let Ok(mut slot) = self.panic_message.try_lock() {
            *slot = Some(message);
        }
        self.killed.store(true, Ordering::Release);
        self.wake_consumer();
    }

    fn take(&self, lanes: &mut Lanes) -> Result<Option<Stamped>, Closed> {
        if self.killed.load(Ordering::Acquire) {
            if lanes.panic_delivered {
                return Err(Closed);
            }
            lanes.panic_delivered = true;
            let message = lock(&self.panic_message).take().unwrap_or_default();
            return Ok(Some((Instant::now(), Event::Panicked(message))));
        }
        let Some((index, _)) = lanes.earliest() else {
            return if lanes.producers == 0 {
                Err(Closed)
            } else {
                Ok(None)
            };
        };
        let taken = match index {
            0 => {
                let taken = lanes.input.take();
                self.writable.notify_all();
                taken
            }
            1 => lanes.initial_resize.take(),
            2 => {
                let taken = lanes.resize.take();
                if std::mem::take(&mut lanes.resize_again)
                    && let Some(output) = self.resize_source
                {
                    // Go's resize goroutine reads its buffered SIGWINCH and
                    // checks the size right after the send is accepted.
                    lanes.resize = Some((Instant::now(), sys::size_event(output)));
                }
                taken
            }
            _ => lanes.quit.take(),
        };
        Ok(taken)
    }

    fn try_pop(&self) -> Result<Option<Stamped>, Closed> {
        let mut lanes = lock(&self.lanes);
        self.take(&mut lanes)
    }

    fn peek(&self) -> Result<Option<Instant>, Closed> {
        if self.killed.load(Ordering::Acquire) {
            let lanes = lock(&self.lanes);
            return if lanes.panic_delivered {
                Err(Closed)
            } else {
                Ok(Some(Instant::now()))
            };
        }
        let lanes = lock(&self.lanes);
        match lanes.earliest() {
            Some((_, at)) => Ok(Some(at)),
            None if lanes.producers == 0 => Err(Closed),
            None => Ok(None),
        }
    }

    fn pop_until(&self, deadline: Option<Instant>) -> Result<Option<Stamped>, Closed> {
        let mut lanes = lock(&self.lanes);
        loop {
            if let Some(popped) = self.take(&mut lanes).transpose() {
                return popped.map(Some);
            }
            let wait = match deadline {
                None => KILL_POLL,
                Some(deadline) => {
                    let now = Instant::now();
                    if now >= deadline {
                        return Ok(None);
                    }
                    (deadline - now).min(KILL_POLL)
                }
            };
            lanes = self
                .readable
                .wait_timeout(lanes, wait)
                .unwrap_or_else(PoisonError::into_inner)
                .0;
        }
    }
}

/// Marks a producer finished when its thread exits, including by panic, so
/// consumers observe `Closed` instead of waiting forever.
struct FinishOnDrop(Arc<Queue>);

impl Drop for FinishOnDrop {
    fn drop(&mut self) {
        self.0.producer_done();
    }
}

/// Spawn a producer thread counted by the queue.
fn spawn_producer(
    name: &str,
    queue: &Arc<Queue>,
    body: impl FnOnce(&Queue) + Send + 'static,
) -> std::io::Result<JoinHandle<()>> {
    queue.producer_started();
    let finish = FinishOnDrop(Arc::clone(queue));
    std::thread::Builder::new()
        .name(name.into())
        .spawn(move || {
            let finish = finish;
            body(&finish.0);
        })
        .inspect_err(|_| {
            // The closure (and its FinishOnDrop) was dropped by the failed
            // spawn, which already balanced the count.
        })
}

// ---------------------------------------------------------------------------
// Bubble Tea standard renderer (flush only; pb never uses Println/scroll areas).

fn cursor_up(n: usize) -> String {
    if n > 1 {
        format!("\x1b[{n}A")
    } else {
        "\x1b[A".to_owned()
    }
}

fn cursor_left(n: usize) -> String {
    if n > 1 {
        format!("\x1b[{n}D")
    } else {
        "\x1b[D".to_owned()
    }
}

const ERASE_LINE_RIGHT: &str = "\x1b[0K";
const ERASE_SCREEN_BELOW: &str = "\x1b[0J";
const ERASE_ENTIRE_LINE: &str = "\x1b[2K";
const CURSOR_DOWN: &str = "\x1b[B";
const HIDE_CURSOR: &str = "\x1b[?25l";
const SHOW_CURSOR: &str = "\x1b[?25h";
const ENTER_ALT: &str = "\x1b[?1049h";
const EXIT_ALT: &str = "\x1b[?1049l";
const CLEAR_HOME: &str = "\x1b[2J\x1b[H";
const PASTE_ON: &str = "\x1b[?2004h";
const PASTE_OFF: &str = "\x1b[?2004l";
const MOUSE_OFF: &str = "\x1b[?1002l\x1b[?1003l\x1b[?1006l";

#[derive(Default)]
pub(crate) struct Renderer {
    /// The last view's bytes (a Go string; not necessarily UTF-8).
    last_render: Vec<u8>,
    last_lines: Vec<Vec<u8>>,
    lines_rendered: usize,
    width: usize,
    height: usize,
    alt: bool,
}

impl Renderer {
    pub(crate) fn new(alt: bool) -> Renderer {
        Renderer {
            alt,
            ..Renderer::default()
        }
    }

    /// WindowSizeMsg handling: new dimensions and a full repaint.
    pub(crate) fn resize(&mut self, size: Size) {
        self.width = usize::from(size.width);
        self.height = usize::from(size.height);
        self.repaint();
    }

    fn repaint(&mut self) {
        self.last_render.clear();
        self.last_lines.clear();
    }

    /// ansi.Truncate(line, width, "") over Go string bytes. Valid UTF-8
    /// lines use the `&str` primitive (unchanged output for every `&str`
    /// frame); other lines use the style owner's byte primitive.
    fn clip(&self, line: &[u8]) -> Vec<u8> {
        match std::str::from_utf8(line) {
            Ok(text) => super::style::truncate(text, self.width, "").into_bytes(),
            Err(_) => super::style::truncate_bytes(line, self.width, b""),
        }
    }

    /// ansi.StringWidth over Go string bytes (same split as `clip`).
    fn cells(line: &[u8]) -> usize {
        match std::str::from_utf8(line) {
            Ok(text) => super::style::width(text),
            Err(_) => super::style::width_bytes(line),
        }
    }

    /// write(view) followed by flush() over the view's bytes (a Go string):
    /// the bytes to send, or None when the view equals the last rendered one.
    pub(crate) fn frame(&mut self, view: &[u8]) -> Option<Vec<u8>> {
        let view = if view.is_empty() {
            b" ".as_slice()
        } else {
            view
        };
        if view == self.last_render.as_slice() {
            return None;
        }
        let mut out = Vec::new();
        if self.lines_rendered > 1 {
            out.extend_from_slice(cursor_up(self.lines_rendered - 1).as_bytes());
        }
        let mut lines: Vec<&[u8]> = view.split(|byte| *byte == b'\n').collect();
        if self.height > 0 && lines.len() > self.height {
            lines.drain(..lines.len() - self.height);
        }
        let last = lines.len() - 1;
        for (i, line) in lines.iter().enumerate() {
            if self
                .last_lines
                .get(i)
                .is_some_and(|previous| previous.as_slice() == *line)
            {
                if i < last {
                    out.extend_from_slice(CURSOR_DOWN.as_bytes());
                }
                continue;
            }
            if i == 0 && self.last_render.is_empty() {
                out.push(b'\r');
            }
            // Without a known width nothing is clipped or measured (Go's
            // `StringWidth(line) < 0` is never true).
            if self.width > 0 {
                let line = self.clip(line);
                out.extend_from_slice(&line);
                if Renderer::cells(&line) < self.width {
                    out.extend_from_slice(ERASE_LINE_RIGHT.as_bytes());
                }
            } else {
                out.extend_from_slice(line);
            }
            if i < last {
                out.extend_from_slice(b"\r\n");
            }
        }
        if self.lines_rendered > lines.len() {
            out.extend_from_slice(ERASE_SCREEN_BELOW.as_bytes());
        }
        self.lines_rendered = lines.len();
        if self.alt {
            out.extend_from_slice(format!("\x1b[{};0H", self.lines_rendered).as_bytes());
        } else {
            out.extend_from_slice(cursor_left(self.width).as_bytes());
        }
        self.last_lines = lines.iter().map(|line| line.to_vec()).collect();
        self.last_render = view.to_vec();
        Some(out)
    }
}

// ---------------------------------------------------------------------------
// Process-wide restoration record shared with the panic hook.

#[derive(Default)]
struct Restore {
    output: sys::Output,
    raw: Option<sys::RawState>,
    /// Hide-cursor (and possibly further mode sequences) were written.
    display: bool,
    alt: bool,
    paste: bool,
    signals: sys::SavedSignals,
    /// Where the panic hook delivers `Event::Panicked`.
    queue: Weak<Queue>,
}

static ACTIVE: Mutex<Option<Restore>> = Mutex::new(None);

/// Serializes terminal output like the renderer mutex in Go: a frame is
/// never interleaved with restoration bytes and is never written after them.
/// `ACTIVE` is only held briefly (never across a write), so a writer blocked
/// on a stalled stdout cannot wedge the record; restoration then waits for
/// that write exactly as Go's shutdown waits on the renderer mutex.
static OUTPUT: Mutex<()> = Mutex::new(());

/// Restore everything still recorded. `erase_line` is renderer.kill()'s
/// "erase current line + CR" when no writer thread performed stop().
fn restore_now(erase_line: bool) -> Result<(), SessionError> {
    let Some(mut restore) = lock(&ACTIVE).take() else {
        return Ok(());
    };
    restore.signals.restore_controls();
    if restore.display {
        let _output = lock(&OUTPUT);
        let mut bytes = String::new();
        if erase_line {
            bytes.push_str(ERASE_ENTIRE_LINE);
            bytes.push('\r');
        }
        if restore.paste {
            bytes.push_str(PASTE_OFF);
        }
        bytes.push_str(SHOW_CURSOR);
        bytes.push_str(MOUSE_OFF);
        if restore.alt {
            bytes.push_str(EXIT_ALT);
            bytes.push_str(SHOW_CURSOR);
        }
        sys::write_all(restore.output, bytes.as_bytes());
        if restore.alt {
            // "give the terminal a moment to catch up"
            std::thread::sleep(Duration::from_millis(10));
        }
    }
    #[cfg(all(test, unix))]
    if tests::RAISE_QUIT_BEFORE_RAW_RESTORE.load(Ordering::Relaxed) {
        // SAFETY: self-signal; the session's SIGQUIT handler must still own it.
        unsafe { libc::raise(libc::SIGQUIT) };
    }
    let result = match restore.raw.take() {
        Some(raw) => sys::restore_raw(&raw)
            .map_err(|e| SessionError::new(format!("error restoring console: {}", go_errno(&e)))),
        None => Ok(()),
    };
    // R2-2: SIGQUIT stays Go-runtime-shaped until the terminal is restored.
    restore.signals.restore_quit();
    result
}

thread_local! {
    /// Depth of `recovered` polls on this thread.
    static RECOVERED: Cell<u32> = const { Cell::new(0) };
}

/// Run `future` in a scope whose panics its caller recovers itself (Go code
/// with its own `defer recover()`, e.g. NewPromqlModeFetchTask): the panic
/// hook neither restores the terminal, nor delivers `Event::Panicked`, nor
/// calls the previous (stderr-reporting) hook for panics raised while it is
/// being polled. The caller reads the panic from the task's JoinError.
pub(crate) fn recovered<F: Future>(future: F) -> impl Future<Output = F::Output> {
    struct Recovered<F>(Pin<Box<F>>);

    impl<F: Future> Future for Recovered<F> {
        type Output = F::Output;

        fn poll(self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<F::Output> {
            struct Depth;
            impl Drop for Depth {
                fn drop(&mut self) {
                    RECOVERED.with(|depth| depth.set(depth.get() - 1));
                }
            }
            RECOVERED.with(|depth| depth.set(depth.get() + 1));
            let _depth = Depth;
            self.get_mut().0.as_mut().poll(context)
        }
    }

    Recovered(Box::pin(future))
}

fn panic_text(info: &std::panic::PanicHookInfo<'_>) -> String {
    let payload = info.payload();
    payload
        .downcast_ref::<&str>()
        .map(|text| (*text).to_owned())
        .or_else(|| payload.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "Box<dyn Any>".to_owned())
}

fn install_panic_hook() {
    static HOOK: std::sync::Once = std::sync::Once::new();
    HOOK.call_once(|| {
        let previous = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            // Go's local `defer recover()` prints nothing: inside a recovered
            // scope the caller owns the panic, so no restore, no event and no
            // chained report.
            if RECOVERED.try_with(Cell::get).unwrap_or(0) > 0 {
                return;
            }
            let queue = lock(&ACTIVE).as_ref().and_then(|r| r.queue.upgrade());
            // Restored before the event can be observed.
            let _ = restore_now(true);
            if let Some(queue) = queue {
                queue.kill(panic_text(info));
            }
            previous(info);
        }));
    });
}

// ---------------------------------------------------------------------------
// Writer thread.

enum Stop {
    /// tea quit: flush the final view, then erase the cursor line.
    Quit(Vec<u8>),
    /// tea kill: erase the cursor line only.
    Kill,
}

#[derive(Default)]
struct Slot {
    view: Option<Vec<u8>>,
    resize: Option<Size>,
    stop: Option<Stop>,
}

struct Writer {
    slot: Arc<(Mutex<Slot>, Condvar)>,
    thread: Option<JoinHandle<()>>,
}

impl Writer {
    fn spawn(output: sys::Output, alt: bool) -> std::io::Result<Writer> {
        let slot: Arc<(Mutex<Slot>, Condvar)> = Arc::default();
        let shared = Arc::clone(&slot);
        let thread = std::thread::Builder::new()
            .name("pb-tui-writer".into())
            .spawn(move || write_loop(&shared, output, alt))?;
        Ok(Writer {
            slot,
            thread: Some(thread),
        })
    }

    fn update(&self, change: impl FnOnce(&mut Slot)) {
        change(&mut lock(&self.slot.0));
        self.slot.1.notify_one();
    }

    fn stop(mut self, stop: Stop) {
        self.update(|slot| slot.stop = Some(stop));
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn write_loop(slot: &(Mutex<Slot>, Condvar), output: sys::Output, alt: bool) {
    let mut renderer = Renderer::new(alt);
    loop {
        let (resize, view, stop) = {
            let mut guard = lock(&slot.0);
            while guard.view.is_none() && guard.resize.is_none() && guard.stop.is_none() {
                guard = slot.1.wait(guard).unwrap_or_else(PoisonError::into_inner);
            }
            (guard.resize.take(), guard.view.take(), guard.stop.take())
        };
        if let Some(size) = resize {
            renderer.resize(size);
        }
        let mut bytes = Vec::new();
        match &stop {
            Some(Stop::Quit(view)) => bytes.extend(renderer.frame(view).unwrap_or_default()),
            Some(Stop::Kill) => {}
            None => bytes.extend(
                view.and_then(|view| renderer.frame(&view))
                    .unwrap_or_default(),
            ),
        }
        if stop.is_some() {
            bytes.extend_from_slice(ERASE_ENTIRE_LINE.as_bytes());
            bytes.push(b'\r');
        }
        if !bytes.is_empty() {
            // Never write after restoration (panic hook or teardown).
            let _output = lock(&OUTPUT);
            if lock(&ACTIVE).is_some() {
                sys::write_all(output, &bytes);
            }
        }
        if stop.is_some() {
            return;
        }
    }
}

// ---------------------------------------------------------------------------
// Session.

/// Where a session reads and writes. Production uses the process streams;
/// tests substitute a pseudo-terminal.
pub(crate) struct Endpoints {
    input: sys::InputSource,
    output: sys::Output,
    #[cfg(test)]
    fail_before_threads: bool,
}

impl Endpoints {
    // Windows' Output is a unit struct; Unix's defaults to stdout.
    #[allow(clippy::default_constructed_unit_structs)]
    fn process() -> Endpoints {
        Endpoints {
            input: sys::InputSource::Process,
            output: sys::Output::default(),
            #[cfg(test)]
            fail_before_threads: false,
        }
    }

    /// One terminal descriptor for input and output (a PTY slave in tests).
    #[cfg(all(test, unix))]
    fn terminal(fd: std::os::fd::RawFd, fail_before_threads: bool) -> Endpoints {
        Endpoints {
            input: sys::InputSource::Fd(fd),
            output: sys::Output(fd),
            fail_before_threads,
        }
    }

    /// `terminal`, but the reader waits with select(2) as for macOS /dev/tty.
    #[cfg(all(test, unix))]
    fn terminal_select(fd: std::os::fd::RawFd) -> Endpoints {
        Endpoints {
            input: sys::InputSource::SelectFd(fd),
            output: sys::Output(fd),
            fail_before_threads: false,
        }
    }
}

/// An acquired terminal (tea.Program between startup and shutdown).
pub(crate) struct TerminalSession {
    queue: Arc<Queue>,
    writer: Option<Writer>,
    reader: Option<JoinHandle<()>>,
    signals: Option<JoinHandle<()>>,
    io: sys::SessionIo,
    output: sys::Output,
    output_terminal: bool,
    torn_down: bool,
}

impl TerminalSession {
    /// tea.Program.Run startup for `screen`.
    pub(crate) fn open(screen: Screen) -> Result<TerminalSession, SessionError> {
        TerminalSession::open_with(screen, Endpoints::process())
    }

    pub(crate) fn open_with(
        screen: Screen,
        endpoints: Endpoints,
    ) -> Result<TerminalSession, SessionError> {
        use std::io::Write;
        let _ = std::io::stdout().flush();
        install_panic_hook();
        let output = endpoints.output;
        let output_terminal = sys::is_terminal(output);
        let queue = Arc::new(Queue::new(output_terminal.then_some(output)));
        {
            let mut active = lock(&ACTIVE);
            if active.is_some() {
                return Err(SessionError::new("terminal session already active"));
            }
            *active = Some(Restore {
                output,
                queue: Arc::downgrade(&queue),
                ..Restore::default()
            });
        }
        let mut session = TerminalSession {
            queue,
            writer: None,
            reader: None,
            signals: None,
            io: sys::SessionIo::default(),
            output,
            output_terminal,
            torn_down: false,
        };
        // Any early return drops `session`, whose teardown rolls back every
        // step recorded so far.
        let input = sys::open_input(endpoints.input, &mut session.io)?;
        // Go installs signal handling before initTerminal (raw mode).
        sys::prepare_signals(&mut session.io, output_terminal).map_err(|e| {
            SessionError::new(format!("error creating cancelreader: {}", go_errno(&e)))
        })?;
        if let Some(raw) = sys::make_raw(&input)? {
            record(|r| r.raw = Some(raw));
        }
        let mut modes = String::from(HIDE_CURSOR);
        if screen == Screen::Fullscreen {
            modes.push_str(ENTER_ALT);
            modes.push_str(CLEAR_HOME);
            modes.push_str(HIDE_CURSOR);
        }
        modes.push_str(PASTE_ON);
        record(|r| {
            r.display = true;
            r.alt = screen == Screen::Fullscreen;
            r.paste = true;
        });
        {
            let _output = lock(&OUTPUT);
            sys::write_all(output, modes.as_bytes());
        }
        #[cfg(test)]
        if endpoints.fail_before_threads {
            return Err(SessionError::new("injected failure"));
        }
        let thread_error = |e: std::io::Error| {
            SessionError::new(format!("error creating cancelreader: {}", go_errno(&e)))
        };
        session.writer =
            Some(Writer::spawn(output, screen == Screen::Fullscreen).map_err(thread_error)?);
        if output_terminal {
            // tea sends the initial size from its own goroutine after startup.
            session.queue.push_initial_resize(sys::size_event(output));
        }
        session.reader =
            Some(sys::spawn_reader(input, &mut session.io, &session.queue).map_err(thread_error)?);
        session.signals =
            sys::spawn_signals(&mut session.io, &session.queue, output).map_err(thread_error)?;
        Ok(session)
    }

    /// Queue `view` as the next frame (tea renderer.write). Non-blocking.
    pub(crate) fn draw(&mut self, view: &str) {
        self.draw_bytes(view.as_bytes());
    }

    /// `draw` for a view held as Go string bytes (not necessarily UTF-8),
    /// rendered without any lossy conversion. Shares the renderer cache with
    /// `draw`.
    pub(crate) fn draw_bytes(&mut self, view: &[u8]) {
        if let Some(writer) = &self.writer {
            writer.update(|slot| slot.view = Some(view.to_vec()));
        }
    }

    /// Side effects of accepting a message, in Go's order: the renderer
    /// handles WindowSizeMsg before Update; accepting the QuitMsg lets the
    /// signal goroutine return and run its deferred signal.Stop.
    fn observe(&self, event: Event) -> Event {
        match &event {
            Event::Resize(size) => {
                if let Some(writer) = &self.writer {
                    let size = *size;
                    writer.update(|slot| slot.resize = Some(size));
                }
            }
            Event::Quit => {
                if let Some(restore) = lock(&ACTIVE).as_mut() {
                    restore.signals.restore_interrupts();
                }
            }
            _ => {}
        }
        event
    }

    fn observe_stamped(&self, (at, event): Stamped) -> Stamped {
        (at, self.observe(event))
    }

    /// Next message with its offer instant; None once every producer stopped
    /// and nothing is pending. Cancel-safe: a message is taken only by the
    /// poll that returns it, so dropping the future (e.g. losing a
    /// `select!`) never loses one.
    pub(crate) async fn next_event_stamped(&mut self) -> Option<Stamped> {
        loop {
            let notified = self.queue.notify.notified();
            match self.queue.try_pop() {
                Ok(Some(stamped)) => return Some(self.observe_stamped(stamped)),
                Err(Closed) => return None,
                Ok(None) => notified.await,
            }
        }
    }

    /// Non-blocking: Ok(None) when nothing is pending, Err(Closed) after
    /// every producer stopped and nothing is pending.
    pub(crate) fn try_next_event_stamped(&mut self) -> Result<Option<Stamped>, Closed> {
        Ok(self
            .queue
            .try_pop()?
            .map(|stamped| self.observe_stamped(stamped)))
    }

    /// The offer instant of the message the next take would return, without
    /// taking it. Taking is acceptance (the reader then decodes/reads on, the
    /// Quit restores dispositions), so a merging runner that compares heads
    /// with this and takes only the winner accepts exactly when Go would.
    pub(crate) fn peek_event_stamp(&self) -> Result<Option<Instant>, Closed> {
        self.queue.peek()
    }

    /// Resolves with the head's offer instant once a message is pending,
    /// without taking it. Cancel-safe.
    pub(crate) async fn event_ready(&self) -> Result<Instant, Closed> {
        loop {
            let notified = self.queue.notify.notified();
            match self.queue.peek() {
                Ok(Some(at)) => return Ok(at),
                Err(Closed) => return Err(Closed),
                Ok(None) => notified.await,
            }
        }
    }

    /// Next message; None once every producer stopped and nothing is pending.
    pub(crate) async fn next_event(&mut self) -> Option<Event> {
        self.next_event_stamped().await.map(|(_, event)| event)
    }

    /// Blocking `next_event`. Must not run on a Tokio runtime thread that
    /// drives network work.
    pub(crate) fn next_event_blocking(&mut self) -> Option<Event> {
        self.queue
            .pop_until(None)
            .ok()
            .flatten()
            .map(|(_, event)| self.observe(event))
    }

    /// Blocking wait bounded by `timeout`: Ok(None) on timeout, Err(Closed)
    /// after the producers stopped. Test-only until a blocking flow needs
    /// ticks; async flows combine `next_event` with Tokio timers.
    #[cfg(all(test, unix))]
    pub(crate) fn next_event_timeout(
        &mut self,
        timeout: Duration,
    ) -> Result<Option<Event>, Closed> {
        let stamped = self.queue.pop_until(Some(Instant::now() + timeout))?;
        Ok(stamped.map(|(_, event)| self.observe(event)))
    }

    /// Current size when stdout is a terminal.
    pub(crate) fn size(&self) -> Option<Size> {
        if self.output_terminal {
            sys::size(self.output).ok()
        } else {
            None
        }
    }

    /// tea quit: render `final_view`, then restore in Go's shutdown order.
    pub(crate) fn close(self, final_view: &str) -> Result<(), SessionError> {
        self.close_bytes(final_view.as_bytes())
    }

    /// `close` with a final view held as Go string bytes.
    pub(crate) fn close_bytes(mut self, final_view: &[u8]) -> Result<(), SessionError> {
        self.teardown(Some(final_view))
    }

    fn teardown(&mut self, final_view: Option<&[u8]>) -> Result<(), SessionError> {
        if self.torn_down {
            return Ok(());
        }
        self.torn_down = true;
        // Signal handlers and the input reader stop first (handlers.shutdown,
        // cancelReader.Cancel), then the renderer, then terminal state.
        self.queue.stop();
        self.io.wake();
        for thread in [self.reader.take(), self.signals.take()]
            .into_iter()
            .flatten()
        {
            let _ = thread.join();
        }
        if let Some(restore) = lock(&ACTIVE).as_mut() {
            restore.signals.restore_controls();
        }
        let wrote_stop = match self.writer.take() {
            Some(writer) => {
                writer.stop(match final_view {
                    Some(view) => Stop::Quit(view.to_vec()),
                    None => Stop::Kill,
                });
                true
            }
            None => false,
        };
        let result = restore_now(!wrote_stop);
        self.io.close();
        result
    }
}

impl Drop for TerminalSession {
    fn drop(&mut self) {
        let _ = self.teardown(None);
    }
}

fn record(change: impl FnOnce(&mut Restore)) {
    if let Some(restore) = lock(&ACTIVE).as_mut() {
        change(restore);
    }
}

// ---------------------------------------------------------------------------
// Unix: termios, /dev/tty, self-pipe signals, poll-based producers.

#[cfg(unix)]
mod sys {
    use super::super::input::{Parser, READ_BUFFER};
    use super::{Event, Queue, SessionError, Size, go_errno, spawn_producer};
    use std::fs::File;
    use std::io;
    use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
    use std::sync::atomic::{AtomicI32, Ordering};
    use std::sync::{Arc, OnceLock};

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub(super) struct Output(pub(super) RawFd);

    impl Default for Output {
        fn default() -> Output {
            Output(libc::STDOUT_FILENO)
        }
    }

    pub(super) enum InputSource {
        Process,
        #[cfg(test)]
        Fd(RawFd),
        /// A descriptor waited on with select(2), as macOS /dev/tty is.
        #[cfg(test)]
        SelectFd(RawFd),
    }

    /// How the reader waits for input alongside the wake pipe. Go's
    /// cancelreader uses epoll/kqueue, except for a file named /dev/tty on
    /// BSD/macOS: kqueue (and Darwin's kqueue-backed poll, which reports
    /// POLLNVAL) cannot wait on it, so it falls back to select.
    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub(super) enum Readiness {
        Poll,
        Select,
    }

    impl Readiness {
        /// cancelreader_bsd.go `NewReader`'s choice for a file of this name.
        pub(super) fn for_name(name: &str) -> Readiness {
            if cfg!(target_os = "macos") && name == "/dev/tty" {
                Readiness::Select
            } else {
                Readiness::Poll
            }
        }
    }

    pub(super) struct Input {
        fd: RawFd,
        name: &'static str,
        readiness: Readiness,
    }

    pub(super) struct RawState {
        fd: RawFd,
        original: libc::termios,
    }

    /// Signal dispositions replaced by the session, restored in order.
    #[derive(Default)]
    pub(super) struct SavedSignals {
        saved: Vec<(libc::c_int, libc::sigaction)>,
    }

    /// Write end of the process-lifetime signal self-pipe. It is created
    /// once and never closed, so a handler still running on another thread
    /// after a session ends can only write into this pipe (drained by the
    /// next session), never into a reused descriptor.
    static SIGNAL_PIPE: AtomicI32 = AtomicI32::new(-1);

    extern "C" fn on_signal(signal: libc::c_int) {
        let fd = SIGNAL_PIPE.load(Ordering::Relaxed);
        if fd >= 0 {
            let byte = signal as u8;
            // SAFETY: write(2) is async-signal-safe; the pipe is nonblocking.
            unsafe { libc::write(fd, (&byte as *const u8).cast(), 1) };
        }
    }

    /// Go's runtime SIGQUIT handling (the oracle: stderr begins
    /// `SIGQUIT: quit\n`, then a PC/goroutine dump, exit status 2, terminal
    /// left raw). Only the stable first line is reproduced; the runtime dump
    /// is a scoped diagnostic difference. No restoration, no unwinding.
    extern "C" fn on_quit(_: libc::c_int) {
        const MESSAGE: &[u8] = b"SIGQUIT: quit\n";
        // SAFETY: write(2) and _exit(2) are async-signal-safe.
        unsafe {
            libc::write(libc::STDERR_FILENO, MESSAGE.as_ptr().cast(), MESSAGE.len());
            libc::_exit(2);
        }
    }

    impl SavedSignals {
        /// signal.Stop for the QuitMsg sender.
        pub(super) fn restore_interrupts(&mut self) {
            self.restore(&[libc::SIGINT, libc::SIGTERM]);
        }

        /// handlers.shutdown: INT/TERM/WINCH, before the renderer stops.
        pub(super) fn restore_controls(&mut self) {
            self.restore(&[libc::SIGINT, libc::SIGTERM, libc::SIGWINCH]);
        }

        /// SIGQUIT last, once termios is restored: the Go runtime handles it
        /// for the whole acquired-terminal interval.
        pub(super) fn restore_quit(&mut self) {
            self.restore(&[libc::SIGQUIT]);
        }

        fn restore(&mut self, signals: &[libc::c_int]) {
            self.saved.retain(|(signal, action)| {
                if !signals.contains(signal) {
                    return true;
                }
                // SAFETY: restoring an action previously returned by sigaction.
                unsafe { libc::sigaction(*signal, action, std::ptr::null_mut()) };
                false
            });
        }
    }

    /// File descriptors owned by one session.
    #[derive(Default)]
    pub(super) struct SessionIo {
        tty: Option<File>,
        wake_read: Option<OwnedFd>,
        wake_write: Option<OwnedFd>,
        /// Read end of the process-lifetime signal pipe (not owned).
        signal_read: Option<RawFd>,
    }

    impl SessionIo {
        pub(super) fn wake(&mut self) {
            if let Some(fd) = &self.wake_write {
                let byte = 0u8;
                // SAFETY: valid owned descriptor.
                unsafe { libc::write(fd.as_raw_fd(), (&byte as *const u8).cast(), 1) };
            }
        }

        pub(super) fn close(&mut self) {
            *self = SessionIo::default();
        }
    }

    /// A close-on-exec pipe; atomically where pipe2 exists.
    fn pipe(nonblocking: bool) -> io::Result<(OwnedFd, OwnedFd)> {
        let mut fds = [0; 2];
        #[cfg(any(
            target_os = "linux",
            target_os = "android",
            target_os = "freebsd",
            target_os = "netbsd",
            target_os = "openbsd",
            target_os = "dragonfly"
        ))]
        {
            let flags = libc::O_CLOEXEC | if nonblocking { libc::O_NONBLOCK } else { 0 };
            // SAFETY: fds has room for two descriptors.
            if unsafe { libc::pipe2(fds.as_mut_ptr(), flags) } != 0 {
                return Err(io::Error::last_os_error());
            }
        }
        #[cfg(not(any(
            target_os = "linux",
            target_os = "android",
            target_os = "freebsd",
            target_os = "netbsd",
            target_os = "openbsd",
            target_os = "dragonfly"
        )))]
        {
            // SAFETY: fds has room for two descriptors.
            if unsafe { libc::pipe(fds.as_mut_ptr()) } != 0 {
                return Err(io::Error::last_os_error());
            }
            for fd in fds {
                // SAFETY: fresh descriptors from pipe(2).
                unsafe {
                    libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC);
                    if nonblocking {
                        let flags = libc::fcntl(fd, libc::F_GETFL);
                        libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK);
                    }
                }
            }
        }
        // SAFETY: two fresh descriptors we now own.
        Ok(unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) })
    }

    /// The process-lifetime signal pipe (read end), created on first use.
    fn signal_pipe() -> io::Result<RawFd> {
        static PIPE: OnceLock<Result<RawFd, i32>> = OnceLock::new();
        let created = PIPE.get_or_init(|| match pipe(true) {
            Ok((read, write)) => {
                // Leaked on purpose: never closed (see SIGNAL_PIPE).
                SIGNAL_PIPE.store(
                    std::mem::ManuallyDrop::new(write).as_raw_fd(),
                    Ordering::Relaxed,
                );
                Ok(std::mem::ManuallyDrop::new(read).as_raw_fd())
            }
            Err(error) => Err(error.raw_os_error().unwrap_or(libc::EIO)),
        });
        created.map_err(io::Error::from_raw_os_error)
    }

    /// Discard signal bytes left by a previous session's late handlers.
    fn drain(fd: RawFd) {
        let mut buffer = [0u8; 64];
        // SAFETY: valid buffer; the read end is nonblocking.
        while unsafe { libc::read(fd, buffer.as_mut_ptr().cast(), buffer.len()) } > 0 {}
    }

    pub(super) fn is_terminal(output: Output) -> bool {
        // SAFETY: isatty accepts any descriptor value.
        unsafe { libc::isatty(output.0) == 1 }
    }

    pub(super) fn open_input(
        source: InputSource,
        io: &mut SessionIo,
    ) -> Result<Input, SessionError> {
        match source {
            InputSource::Process => {
                // SAFETY: isatty accepts any descriptor value.
                if unsafe { libc::isatty(libc::STDIN_FILENO) } == 1 {
                    return Ok(Input {
                        fd: libc::STDIN_FILENO,
                        name: "/dev/stdin",
                        readiness: Readiness::for_name("/dev/stdin"),
                    });
                }
                let tty = File::open("/dev/tty").map_err(|e| {
                    SessionError::new(format!(
                        "could not open a new TTY: open /dev/tty: {}",
                        go_errno(&e)
                    ))
                })?;
                let fd = tty.as_raw_fd();
                io.tty = Some(tty);
                Ok(Input {
                    fd,
                    name: "/dev/tty",
                    readiness: Readiness::for_name("/dev/tty"),
                })
            }
            #[cfg(test)]
            InputSource::Fd(fd) => Ok(Input {
                fd,
                name: "/dev/stdin",
                readiness: Readiness::Poll,
            }),
            #[cfg(test)]
            InputSource::SelectFd(fd) => Ok(Input {
                fd,
                name: "/dev/tty",
                readiness: Readiness::Select,
            }),
        }
    }

    /// x/term MakeRaw when the input is a terminal.
    pub(super) fn make_raw(input: &Input) -> Result<Option<RawState>, SessionError> {
        // SAFETY: isatty accepts any descriptor value.
        if unsafe { libc::isatty(input.fd) } != 1 {
            return Ok(None);
        }
        let failed =
            |e: io::Error| SessionError::new(format!("error entering raw mode: {}", go_errno(&e)));
        let mut termios = std::mem::MaybeUninit::<libc::termios>::uninit();
        // SAFETY: termios is written by tcgetattr on success.
        if unsafe { libc::tcgetattr(input.fd, termios.as_mut_ptr()) } != 0 {
            return Err(failed(io::Error::last_os_error()));
        }
        // SAFETY: initialized by the successful tcgetattr.
        let original = unsafe { termios.assume_init() };
        let mut raw = original;
        raw.c_iflag &= !(libc::IGNBRK
            | libc::BRKINT
            | libc::PARMRK
            | libc::ISTRIP
            | libc::INLCR
            | libc::IGNCR
            | libc::ICRNL
            | libc::IXON);
        raw.c_oflag &= !libc::OPOST;
        raw.c_lflag &= !(libc::ECHO | libc::ECHONL | libc::ICANON | libc::ISIG | libc::IEXTEN);
        raw.c_cflag &= !(libc::CSIZE | libc::PARENB);
        raw.c_cflag |= libc::CS8;
        raw.c_cc[libc::VMIN] = 1;
        raw.c_cc[libc::VTIME] = 0;
        // SAFETY: raw is a valid termios.
        if unsafe { libc::tcsetattr(input.fd, libc::TCSANOW, &raw) } != 0 {
            return Err(failed(io::Error::last_os_error()));
        }
        Ok(Some(RawState {
            fd: input.fd,
            original,
        }))
    }

    pub(super) fn restore_raw(raw: &RawState) -> io::Result<()> {
        // SAFETY: original came from tcgetattr.
        if unsafe { libc::tcsetattr(raw.fd, libc::TCSANOW, &raw.original) } != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    /// The session's wake pipe, the drained process signal pipe and
    /// handlers for SIGINT/SIGTERM/SIGQUIT, plus SIGWINCH when stdout is a
    /// terminal. Each replaced disposition is recorded for restoration.
    pub(super) fn prepare_signals(io: &mut SessionIo, winch: bool) -> io::Result<()> {
        let (wake_read, wake_write) = pipe(false)?;
        io.wake_read = Some(wake_read);
        io.wake_write = Some(wake_write);
        let signal_read = signal_pipe()?;
        drain(signal_read);
        io.signal_read = Some(signal_read);
        let forward = on_signal as extern "C" fn(libc::c_int);
        let mut signals = vec![
            (libc::SIGINT, forward),
            (libc::SIGTERM, forward),
            (libc::SIGQUIT, on_quit as extern "C" fn(libc::c_int)),
        ];
        if winch {
            signals.push((libc::SIGWINCH, forward));
        }
        for (signal, handler) in signals {
            // SAFETY: zeroed sigaction is valid; handlers are async-signal-safe.
            let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
            action.sa_sigaction = handler as libc::sighandler_t;
            action.sa_flags = libc::SA_RESTART;
            // SAFETY: valid signal set pointer.
            unsafe { libc::sigemptyset(&mut action.sa_mask) };
            // SAFETY: zeroed sigaction is a valid out-parameter.
            let mut previous: libc::sigaction = unsafe { std::mem::zeroed() };
            // SAFETY: valid pointers.
            if unsafe { libc::sigaction(signal, &action, &mut previous) } != 0 {
                return Err(io::Error::last_os_error());
            }
            super::record(|r| r.signals.saved.push((signal, previous)));
        }
        Ok(())
    }

    pub(super) fn size(output: Output) -> io::Result<Size> {
        // SAFETY: winsize is written by TIOCGWINSZ on success.
        let mut size: libc::winsize = unsafe { std::mem::zeroed() };
        if unsafe { libc::ioctl(output.0, libc::TIOCGWINSZ, &mut size) } != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(Size {
            width: size.ws_col,
            height: size.ws_row,
        })
    }

    /// checkResize: a WindowSizeMsg, or the bare errno error Go sends to p.errs.
    pub(super) fn size_event(output: Output) -> Event {
        match size(output) {
            Ok(size) => Event::Resize(size),
            Err(e) => Event::InputError(go_errno(&e)),
        }
    }

    /// One renderer Write to os.Stdout: the renderer discards the returned
    /// error, but the OS-writer contract underneath is preserved like the
    /// main.rs SignalWriter: EINTR retries, EAGAIN on a launcher-provided
    /// O_NONBLOCK descriptor waits for POLLOUT without changing its shared
    /// flags (Go's poller), a zero-length write ends the write
    /// (io.ErrUnexpectedEOF), EPIPE on stdout/stderr dies by SIGPIPE (Go
    /// epipecheck; the terminal is not restored, as in Go), and any other
    /// error abandons the rest of this frame.
    pub(super) fn write_all(output: Output, mut bytes: &[u8]) {
        while !bytes.is_empty() {
            // SAFETY: bytes is a valid buffer; the count fits the signed result.
            let n = unsafe {
                libc::write(
                    output.0,
                    bytes.as_ptr().cast(),
                    bytes.len().min(isize::MAX as usize),
                )
            };
            if n > 0 {
                bytes = &bytes[n as usize..];
                continue;
            }
            if n == 0 {
                return;
            }
            let error = io::Error::last_os_error();
            match error.kind() {
                io::ErrorKind::Interrupted => {}
                io::ErrorKind::WouldBlock => {
                    if !wait_writable(output.0) {
                        return;
                    }
                }
                io::ErrorKind::BrokenPipe
                    if output.0 == libc::STDOUT_FILENO || output.0 == libc::STDERR_FILENO =>
                {
                    sigpipe()
                }
                _ => return,
            }
        }
    }

    fn wait_writable(fd: RawFd) -> bool {
        let mut descriptor = libc::pollfd {
            fd,
            events: libc::POLLOUT,
            revents: 0,
        };
        loop {
            // SAFETY: one valid pollfd.
            if unsafe { libc::poll(&mut descriptor, 1, -1) } >= 0 {
                return true;
            }
            if io::Error::last_os_error().kind() != io::ErrorKind::Interrupted {
                return false;
            }
        }
    }

    /// Rust starts with SIGPIPE ignored; restore the default and deliver it,
    /// keeping a failure exit if delivery somehow fails (as main.rs does).
    fn sigpipe() -> ! {
        // SAFETY: plain signal disposition change and self-signal.
        unsafe {
            libc::signal(libc::SIGPIPE, libc::SIG_DFL);
            libc::raise(libc::SIGPIPE);
        }
        std::process::exit(1)
    }

    /// Wait for `fd` or the wake pipe; false when woken (or the wait failed).
    pub(super) fn wait_readable(fd: RawFd, wake: RawFd, readiness: &mut Readiness) -> bool {
        if *readiness == Readiness::Select {
            // A descriptor beyond FD_SETSIZE cannot be waited on safely by
            // this backend. End the producer rather than admit a blocking read.
            return select_readable(fd, wake).unwrap_or(false);
        }
        loop {
            let mut fds = [
                libc::pollfd {
                    fd: wake,
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd,
                    events: libc::POLLIN,
                    revents: 0,
                },
            ];
            // SAFETY: two valid pollfd entries.
            if unsafe { libc::poll(fds.as_mut_ptr(), 2, -1) } < 0 {
                if io::Error::last_os_error().kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                return false;
            }
            if fds[0].revents != 0 {
                return false;
            }
            // Darwin's poll reports POLLNVAL for an open descriptor whose
            // device it cannot wait on (/dev/tty, even when it is stdin);
            // reading would then block past cancellation, so select instead.
            // A closed descriptor (F_GETFD fails) keeps the read error path.
            #[cfg(target_os = "macos")]
            // SAFETY: F_GETFD only queries the descriptor.
            if fds[1].revents & libc::POLLNVAL != 0
                && unsafe { libc::fcntl(fd, libc::F_GETFD) } >= 0
            {
                *readiness = Readiness::Select;
                return select_readable(fd, wake).unwrap_or(false);
            }
            if fds[1].revents != 0 {
                return true;
            }
        }
    }

    /// select(2) form of `wait_readable`: Some(false) when woken or select
    /// failed, Some(true) when `fd` is readable, None when a descriptor is
    /// outside FD_SETSIZE.
    pub(super) fn select_readable(fd: RawFd, wake: RawFd) -> Option<bool> {
        let fits = |d: RawFd| (0..libc::FD_SETSIZE as RawFd).contains(&d);
        if !fits(fd) || (wake >= 0 && !fits(wake)) {
            return None;
        }
        loop {
            // SAFETY: both descriptors are within FD_SETSIZE; the set is
            // initialized by FD_ZERO before use.
            let (ready, woken, readable) = unsafe {
                let mut set = std::mem::MaybeUninit::<libc::fd_set>::uninit();
                libc::FD_ZERO(set.as_mut_ptr());
                let mut set = set.assume_init();
                libc::FD_SET(fd, &mut set);
                if wake >= 0 {
                    libc::FD_SET(wake, &mut set);
                }
                let ready = libc::select(
                    fd.max(wake) + 1,
                    &mut set,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                );
                (
                    ready,
                    wake >= 0 && libc::FD_ISSET(wake, &set),
                    libc::FD_ISSET(fd, &set),
                )
            };
            if ready < 0 {
                if io::Error::last_os_error().kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                return Some(false);
            }
            if woken {
                return Some(false);
            }
            if readable {
                return Some(true);
            }
        }
    }

    /// readLoop/readAnsiInputs: read a chunk, hand off each decoded message
    /// one at a time (blocking until accepted), then read again. EOF ends
    /// the loop silently; another read error is sent once, then it ends.
    pub(super) fn spawn_reader(
        input: Input,
        io: &mut SessionIo,
        queue: &Arc<Queue>,
    ) -> io::Result<std::thread::JoinHandle<()>> {
        let wake = io.wake_read.as_ref().map_or(-1, AsRawFd::as_raw_fd);
        spawn_producer("pb-tui-input", queue, move |queue| {
            let mut parser = Parser::new();
            let mut buffer = [0u8; READ_BUFFER];
            let mut readiness = input.readiness;
            while wait_readable(input.fd, wake, &mut readiness) {
                // SAFETY: valid buffer.
                let n = unsafe { libc::read(input.fd, buffer.as_mut_ptr().cast(), buffer.len()) };
                if n > 0 {
                    let n = n as usize;
                    for event in parser.feed(&buffer[..n], n == READ_BUFFER) {
                        if !queue.push_input(event) {
                            return;
                        }
                    }
                } else if n == 0 {
                    return;
                } else {
                    let error = io::Error::last_os_error();
                    if matches!(
                        error.kind(),
                        io::ErrorKind::Interrupted | io::ErrorKind::WouldBlock
                    ) {
                        continue;
                    }
                    let message = format!(
                        "error reading input: read {}: {}",
                        input.name,
                        go_errno(&error)
                    );
                    queue.push_input(Event::InputError(message));
                    return;
                }
            }
        })
    }

    /// handleSignals and listenForResize: independent of the input reader.
    pub(super) fn spawn_signals(
        io: &mut SessionIo,
        queue: &Arc<Queue>,
        output: Output,
    ) -> io::Result<Option<std::thread::JoinHandle<()>>> {
        let wake = io.wake_read.as_ref().map_or(-1, AsRawFd::as_raw_fd);
        let signals = io.signal_read.unwrap_or(-1);
        spawn_producer("pb-tui-signals", queue, move |queue| {
            let mut quit_sent = false;
            while wait_readable(signals, wake, &mut Readiness::Poll) {
                let mut received = [0u8; 16];
                // SAFETY: valid buffer; the read end is nonblocking.
                let n =
                    unsafe { libc::read(signals, received.as_mut_ptr().cast(), received.len()) };
                for &signal in received.iter().take(n.max(0) as usize) {
                    match libc::c_int::from(signal) {
                        libc::SIGWINCH => queue.push_resize(size_event(output)),
                        // Later INT/TERM before acceptance are swallowed, as
                        // by Go's blocked handler goroutine.
                        _ if quit_sent => {}
                        _ => {
                            quit_sent = true;
                            queue.push_quit();
                        }
                    }
                }
            }
        })
        .map(Some)
    }
}

// ---------------------------------------------------------------------------
// Windows: crossterm console mode and events. Not yet characterized against
// Bubble Tea's coninput reader; compile/run on Windows is outstanding.

#[cfg(windows)]
mod sys {
    use super::{Event, Queue, SessionError, Size, go_errno, spawn_producer};
    use std::io::{self, Write};
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::Duration;

    #[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
    pub(super) struct Output;

    pub(super) enum InputSource {
        Process,
    }

    pub(super) struct Input;

    pub(super) struct RawState;

    #[derive(Default)]
    pub(super) struct SavedSignals;

    impl SavedSignals {
        pub(super) fn restore_interrupts(&mut self) {}

        pub(super) fn restore_controls(&mut self) {}

        pub(super) fn restore_quit(&mut self) {}
    }

    #[derive(Default)]
    pub(super) struct SessionIo {
        stop: Arc<AtomicBool>,
    }

    impl SessionIo {
        pub(super) fn wake(&mut self) {
            self.stop.store(true, Ordering::Relaxed);
        }

        pub(super) fn close(&mut self) {}
    }

    pub(super) fn is_terminal(_: Output) -> bool {
        use std::io::IsTerminal;
        io::stdout().is_terminal()
    }

    pub(super) fn open_input(_: InputSource, _: &mut SessionIo) -> Result<Input, SessionError> {
        Ok(Input)
    }

    pub(super) fn make_raw(_: &Input) -> Result<Option<RawState>, SessionError> {
        crossterm::terminal::enable_raw_mode()
            .map(|()| Some(RawState))
            .map_err(|e| SessionError::new(format!("error entering raw mode: {}", go_errno(&e))))
    }

    pub(super) fn restore_raw(_: &RawState) -> io::Result<()> {
        crossterm::terminal::disable_raw_mode()
    }

    pub(super) fn prepare_signals(_: &mut SessionIo, _: bool) -> io::Result<()> {
        Ok(())
    }

    pub(super) fn size(_: Output) -> io::Result<Size> {
        let (width, height) = crossterm::terminal::size()?;
        Ok(Size { width, height })
    }

    pub(super) fn size_event(output: Output) -> Event {
        match size(output) {
            Ok(size) => Event::Resize(size),
            Err(e) => Event::InputError(go_errno(&e)),
        }
    }

    pub(super) fn write_all(_: Output, bytes: &[u8]) {
        let mut stdout = io::stdout().lock();
        let _ = stdout.write_all(bytes);
        let _ = stdout.flush();
    }

    pub(super) fn spawn_reader(
        _: Input,
        io: &mut SessionIo,
        queue: &Arc<Queue>,
    ) -> io::Result<std::thread::JoinHandle<()>> {
        let stop = Arc::clone(&io.stop);
        spawn_producer("pb-tui-input", queue, move |queue| {
            while !stop.load(Ordering::Relaxed) {
                let failed = |e: io::Error| {
                    Event::InputError(format!("error reading input: {}", go_errno(&e)))
                };
                match crossterm::event::poll(Duration::from_millis(50)) {
                    Ok(false) => continue,
                    Ok(true) => {}
                    Err(e) => {
                        queue.push_input(failed(e));
                        return;
                    }
                }
                match crossterm::event::read() {
                    Ok(event) => {
                        if let Some(event) = super::super::input::from_crossterm(event)
                            && !queue.push_input(event)
                        {
                            return;
                        }
                    }
                    Err(e) => {
                        queue.push_input(failed(e));
                        return;
                    }
                }
            }
        })
    }

    pub(super) fn spawn_signals(
        _: &mut SessionIo,
        _: &Arc<Queue>,
        _: Output,
    ) -> io::Result<Option<std::thread::JoinHandle<()>>> {
        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Test-only: `restore_now` raises SIGQUIT just before the termios
    /// restore (the R2-2 window). Set only in the re-executed child process.
    #[cfg(unix)]
    pub(super) static RAISE_QUIT_BEFORE_RAW_RESTORE: AtomicBool = AtomicBool::new(false);

    fn frames(renderer: &mut Renderer, view: &str) -> String {
        String::from_utf8(renderer.frame(view.as_bytes()).unwrap_or_default()).unwrap()
    }

    /// Raw byte views reach the terminal unchanged: no lossy conversion
    /// before the clip/width step, and the same cache serves both paths.
    #[test]
    fn byte_frames_are_not_lossy_and_share_the_cache() {
        let mut renderer = Renderer::new(false);
        // Unknown width: no clip/measure; invalid UTF-8 passes verbatim.
        assert_eq!(
            renderer.frame(b"a\xff\x1b[1mb\nok"),
            Some(b"\ra\xff\x1b[1mb\r\nok\x1b[D".to_vec())
        );
        assert_eq!(renderer.frame(b"a\xff\x1b[1mb\nok"), None);
        // The `&str` view of an unchanged line diffs against the byte cache.
        assert_eq!(frames(&mut renderer, "a\nok"), "\x1b[Aa\r\n\x1b[D");
    }

    #[test]
    fn inline_frames_follow_the_standard_renderer() {
        let mut renderer = Renderer::new(false);
        renderer.resize(Size {
            width: 100,
            height: 30,
        });
        // Matches the Go login oracle's final-frame tail.
        assert_eq!(
            frames(&mut renderer, "a\nb\n"),
            "\ra\x1b[0K\r\nb\x1b[0K\r\n\x1b[0K\x1b[100D"
        );
        assert_eq!(renderer.frame(b"a\nb\n"), None);
        // Unchanged lines are skipped with cursor-down moves.
        assert_eq!(
            frames(&mut renderer, "a\nc\n"),
            "\x1b[2A\x1b[Bc\x1b[0K\r\n\x1b[100D"
        );
        // Shrinking erases the leftover lines.
        assert_eq!(
            frames(&mut renderer, "x"),
            "\x1b[2Ax\x1b[0K\x1b[0J\x1b[100D"
        );
        // An empty view renders a single space.
        assert_eq!(frames(&mut renderer, ""), " \x1b[0K\x1b[100D");
    }

    #[test]
    fn frames_drop_top_lines_beyond_height_and_position_alt_cursor() {
        let mut renderer = Renderer::new(true);
        renderer.resize(Size {
            width: 10,
            height: 2,
        });
        assert_eq!(
            frames(&mut renderer, "1\n2\n3"),
            "\r2\x1b[0K\r\n3\x1b[0K\x1b[2;0H"
        );
        // Without a known size nothing is truncated or erased.
        let mut renderer = Renderer::new(false);
        assert_eq!(frames(&mut renderer, "a\nb"), "\ra\r\nb\x1b[D");
        // A resize forces a full repaint.
        renderer.resize(Size {
            width: 5,
            height: 5,
        });
        assert_eq!(
            frames(&mut renderer, "a\nb"),
            "\x1b[A\ra\x1b[0K\r\nb\x1b[0K\x1b[5D"
        );
    }

    fn key(text: &str) -> Event {
        Event::Key(crate::tui::input::Key::runes(text))
    }

    fn pop(queue: &Queue) -> Event {
        queue.try_pop().unwrap().expect("pending").1
    }

    /// Go's reader blocks on each unbuffered send: k2 is offered only when
    /// k1 is accepted, and the independent signal sender overtakes it.
    #[test]
    fn reader_hands_off_one_message_and_controls_are_independent() {
        let queue = Arc::new(Queue::new(None));
        queue.producer_started();
        assert!(queue.push_input(key("k1")));
        let reader = {
            let queue = Arc::clone(&queue);
            std::thread::spawn(move || {
                let k2 = queue.push_input(key("k2"));
                let k3 = queue.push_input(key("k3"));
                (k2, k3)
            })
        };
        std::thread::sleep(Duration::from_millis(20));
        // k2 is still blocked in the reader: only k1 is pending.
        assert!(lock(&queue.lanes).input.is_some());
        queue.push_quit();
        let quit_at = queue.peek().unwrap().unwrap();
        assert_eq!(pop(&queue), key("k1"));
        // Accepting k1 lets the reader offer k2, stamped after the Quit.
        let mut order = Vec::new();
        while order.len() < 3 {
            if let Some((at, event)) = queue
                .pop_until(Some(Instant::now() + Duration::from_secs(5)))
                .unwrap()
            {
                if event == key("k2") {
                    assert!(at > quit_at);
                }
                order.push(event);
            }
        }
        assert_eq!(order, [Event::Quit, key("k2"), key("k3")]);
        assert_eq!(reader.join().unwrap(), (true, true));
        assert_eq!(queue.try_pop(), Ok(None));
        queue.producer_done();
        assert_eq!(queue.try_pop(), Err(Closed));
    }

    /// A completion that arrives while k1 is being processed comes after k2
    /// when Go's reader offered k2 at k1's acceptance (the reviewer's
    /// "k1, completion, k2" holds only if the completion predates that offer).
    #[test]
    fn next_key_is_offered_at_acceptance_of_the_previous_one() {
        let queue = Arc::new(Queue::new(None));
        queue.producer_started();
        assert!(queue.push_input(key("k1")));
        let reader = {
            let queue = Arc::clone(&queue);
            std::thread::spawn(move || queue.push_input(key("k2")))
        };
        std::thread::sleep(Duration::from_millis(10));
        let (_, first) = queue.try_pop().unwrap().unwrap();
        assert_eq!(first, key("k1"));
        assert!(reader.join().unwrap());
        // The runner now processes k1; a completion arrives meanwhile.
        let completion_at = Instant::now();
        let k2_at = queue.peek().unwrap().unwrap();
        assert!(k2_at <= completion_at);
        queue.stop();
        assert!(!queue.push_input(key("late")));
    }

    #[test]
    fn panics_are_delivered_first_then_the_queue_closes() {
        let queue = Queue::new(None);
        queue.producer_started();
        assert!(queue.push_input(key("pending")));
        queue.push_quit();
        queue.kill("boom".into());
        assert!(queue.peek().unwrap().is_some());
        let (_, event) = queue.pop_until(None).unwrap().unwrap();
        assert_eq!(event, Event::Panicked("boom".into()));
        assert_eq!(queue.try_pop(), Err(Closed));
        assert_eq!(queue.peek(), Err(Closed));
    }

    /// R2-1 (documented nondeterminism): the panic's stamp is the time it is
    /// peeked, so a runner merging by stamp may first process a completion
    /// stamped before the kill — as Go's eventLoop `select` may pick a pending
    /// message over `ctx.Done`. Within the terminal's own lanes it is first.
    #[test]
    fn panic_stamp_is_its_peek_time() {
        let queue = Queue::new(None);
        queue.producer_started();
        let completion_at = Instant::now();
        assert!(queue.push_input(key("pending")));
        queue.kill("boom".into());
        let panic_at = queue.peek().unwrap().unwrap();
        assert!(panic_at >= completion_at);
        assert_eq!(pop(&queue), Event::Panicked("boom".into()));
    }

    #[test]
    fn resize_sender_coalesces_and_keeps_its_own_lane() {
        let queue = Queue::new(None);
        queue.producer_started();
        let size = |width| Event::Resize(Size { width, height: 1 });
        queue.push_initial_resize(size(1));
        queue.push_resize(size(2));
        queue.push_resize(size(3)); // pending: coalesced (no source to re-query)
        assert_eq!(pop(&queue), size(1));
        assert_eq!(pop(&queue), size(2));
        assert_eq!(queue.try_pop(), Ok(None));
    }

    #[test]
    fn recovered_scopes_nest_and_unwind() {
        let depth = || RECOVERED.with(Cell::get);
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        assert_eq!(depth(), 0);
        let inner = runtime.block_on(recovered(async { RECOVERED.with(Cell::get) }));
        assert_eq!(inner, 1);
        let unwound = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            runtime.block_on(recovered(async { panic!("recovered panic") }))
        }));
        assert!(unwound.is_err());
        assert_eq!(depth(), 0);
    }

    #[test]
    fn errno_text_matches_go() {
        assert_eq!(
            go_errno(&std::io::Error::from_raw_os_error(enxio())),
            "no such device or address"
        );
    }

    #[cfg(unix)]
    fn enxio() -> i32 {
        libc::ENXIO
    }
    #[cfg(not(unix))]
    fn enxio() -> i32 {
        6
    }

    /// Go's cancelreader picks select only for macOS /dev/tty.
    #[cfg(unix)]
    #[test]
    fn readiness_follows_cancelreader_per_platform() {
        use sys::Readiness;
        assert_eq!(Readiness::for_name("/dev/stdin"), Readiness::Poll);
        let tty = if cfg!(target_os = "macos") {
            Readiness::Select
        } else {
            Readiness::Poll
        };
        assert_eq!(Readiness::for_name("/dev/tty"), tty);
    }

    #[cfg(unix)]
    #[test]
    fn select_waits_for_input_or_wake_and_rejects_large_descriptors() {
        use std::os::fd::AsRawFd;
        let pair = || {
            let mut fds = [0; 2];
            // SAFETY: fds has room for two descriptors, owned below.
            assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0);
            // SAFETY: fresh descriptors from pipe(2).
            unsafe {
                use std::os::fd::FromRawFd;
                (
                    std::os::fd::OwnedFd::from_raw_fd(fds[0]),
                    std::os::fd::OwnedFd::from_raw_fd(fds[1]),
                )
            }
        };
        let put = |fd: &std::os::fd::OwnedFd| {
            // SAFETY: valid one-byte buffer.
            assert_eq!(
                unsafe { libc::write(fd.as_raw_fd(), b"x".as_ptr().cast(), 1) },
                1
            );
        };
        let (input, input_write) = pair();
        let (wake, wake_write) = pair();
        put(&input_write);
        assert_eq!(
            sys::select_readable(input.as_raw_fd(), wake.as_raw_fd()),
            Some(true)
        );
        // The wake pipe wins even when input is also readable.
        put(&wake_write);
        assert_eq!(
            sys::select_readable(input.as_raw_fd(), wake.as_raw_fd()),
            Some(false)
        );
        // A blocked select returns once woken from another thread.
        let (idle, _idle_write) = pair();
        let (wake, wake_write) = pair();
        let waiter = std::thread::spawn({
            let (idle, wake) = (idle.as_raw_fd(), wake.as_raw_fd());
            move || sys::select_readable(idle, wake)
        });
        std::thread::sleep(Duration::from_millis(50));
        assert!(!waiter.is_finished());
        put(&wake_write);
        assert_eq!(waiter.join().unwrap(), Some(false));
        drop((idle, wake));
        let limit = libc::FD_SETSIZE as i32;
        assert_eq!(sys::select_readable(limit, -1), None);
        assert_eq!(sys::select_readable(0, limit), None);
    }

    #[cfg(unix)]
    #[test]
    fn select_backend_never_admits_high_descriptor_reads() {
        use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
        // Darwin commonly starts below FD_SETSIZE. Raise only the soft limit
        // for this test, restoring it even if an assertion fails.
        struct Limit(libc::rlimit);
        impl Drop for Limit {
            fn drop(&mut self) {
                // SAFETY: restoring the successfully read process limit.
                unsafe {
                    libc::setrlimit(libc::RLIMIT_NOFILE, &self.0);
                }
            }
        }
        let limit = libc::FD_SETSIZE as libc::rlim_t;
        let mut saved = std::mem::MaybeUninit::uninit();
        // SAFETY: valid output storage.
        assert_eq!(
            unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, saved.as_mut_ptr()) },
            0
        );
        // SAFETY: initialized by getrlimit.
        let saved = unsafe { saved.assume_init() };
        let _restore = Limit(saved);
        if saved.rlim_cur <= limit {
            assert!(
                saved.rlim_max > limit,
                "high-descriptor test needs a sufficient hard limit"
            );
            let raised = libc::rlimit {
                rlim_cur: limit + 1,
                rlim_max: saved.rlim_max,
            };
            // SAFETY: raises the soft limit within its hard limit.
            assert_eq!(unsafe { libc::setrlimit(libc::RLIMIT_NOFILE, &raised) }, 0);
        }
        let mut pipe = [0; 2];
        // SAFETY: valid two-descriptor storage.
        assert_eq!(unsafe { libc::pipe(pipe.as_mut_ptr()) }, 0);
        // SAFETY: fresh pipe descriptors become owned here.
        let (read, write) =
            unsafe { (OwnedFd::from_raw_fd(pipe[0]), OwnedFd::from_raw_fd(pipe[1])) };
        // SAFETY: duplicate a valid descriptor into a select-inexpressible slot.
        let high = unsafe { libc::fcntl(read.as_raw_fd(), libc::F_DUPFD, limit as i32) };
        assert!(high >= limit as i32, "{}", std::io::Error::last_os_error());
        // SAFETY: the successful duplicate is uniquely owned.
        let high = unsafe { OwnedFd::from_raw_fd(high) };
        // Make poll report readable: an accidental poll fallback would admit
        // this read, whereas a select-only backend must reject the descriptor.
        // SAFETY: valid one-byte buffer and open pipe.
        assert_eq!(
            unsafe { libc::write(write.as_raw_fd(), b"x".as_ptr().cast(), 1) },
            1
        );
        assert!(!sys::wait_readable(
            high.as_raw_fd(),
            -1,
            &mut sys::Readiness::Select
        ));
        // A high wake descriptor is equally inexpressible, even with low input.
        assert!(!sys::wait_readable(
            read.as_raw_fd(),
            high.as_raw_fd(),
            &mut sys::Readiness::Select
        ));
        let mut byte = [0];
        // SAFETY: the byte remains pending because readiness rejected the read.
        assert_eq!(
            unsafe { libc::read(read.as_raw_fd(), byte.as_mut_ptr().cast(), 1) },
            1
        );
        assert_eq!(byte, *b"x");
    }

    /// Lifecycle tests share the process-wide restoration record and signal
    /// dispositions, so they run one at a time on a private pseudo-terminal.
    #[cfg(unix)]
    mod pty {
        use super::super::*;
        use crate::tui::input::{Key, KeyCode};
        use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};

        static SERIAL: Mutex<()> = Mutex::new(());
        #[cfg(not(target_os = "linux"))]
        static PTY_NAME: Mutex<()> = Mutex::new(());

        const TEARDOWN: &[u8] = b"\x1b[2K\r\x1b[?2004l\x1b[?25h\x1b[?1002l\x1b[?1003l\x1b[?1006l";
        const ALT_EXIT: &[u8] = b"\x1b[?1049l\x1b[?25h";

        struct Pty {
            master: OwnedFd,
            slave: OwnedFd,
            path: std::ffi::CString,
        }

        impl Pty {
            fn new(width: u16, height: u16) -> Pty {
                // SAFETY: standard pseudo-terminal allocation; every
                // descriptor is checked and owned.
                unsafe {
                    let master = libc::posix_openpt(libc::O_RDWR | libc::O_NOCTTY);
                    assert!(master >= 0);
                    let master = OwnedFd::from_raw_fd(master);
                    assert_eq!(libc::grantpt(master.as_raw_fd()), 0);
                    assert_eq!(libc::unlockpt(master.as_raw_fd()), 0);
                    let mut name = [0 as libc::c_char; 128];
                    #[cfg(target_os = "linux")]
                    assert_eq!(
                        libc::ptsname_r(master.as_raw_fd(), name.as_mut_ptr(), name.len()),
                        0
                    );
                    #[cfg(not(target_os = "linux"))]
                    {
                        // Serialized by PTY_NAME: ptsname's buffer is static.
                        let _name = lock(&PTY_NAME);
                        let shared = libc::ptsname(master.as_raw_fd());
                        assert!(!shared.is_null());
                        let bytes = std::ffi::CStr::from_ptr(shared).to_bytes_with_nul();
                        assert!(bytes.len() <= name.len());
                        for (slot, byte) in name.iter_mut().zip(bytes) {
                            *slot = *byte as libc::c_char;
                        }
                    }
                    let slave = libc::open(name.as_ptr(), libc::O_RDWR | libc::O_NOCTTY);
                    assert!(slave >= 0);
                    let flags = libc::fcntl(master.as_raw_fd(), libc::F_GETFL);
                    libc::fcntl(master.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK);
                    let pty = Pty {
                        master,
                        slave: OwnedFd::from_raw_fd(slave),
                        path: std::ffi::CStr::from_ptr(name.as_ptr()).to_owned(),
                    };
                    pty.resize(width, height);
                    pty
                }
            }

            fn resize(&self, width: u16, height: u16) {
                let size = libc::winsize {
                    ws_row: height,
                    ws_col: width,
                    ws_xpixel: 0,
                    ws_ypixel: 0,
                };
                // SAFETY: valid winsize pointer.
                assert_eq!(
                    unsafe { libc::ioctl(self.master.as_raw_fd(), libc::TIOCSWINSZ, &size) },
                    0
                );
            }

            fn slave(&self) -> RawFd {
                self.slave.as_raw_fd()
            }

            fn type_bytes(&self, bytes: &[u8]) {
                // SAFETY: valid buffer.
                let n = unsafe {
                    libc::write(self.master.as_raw_fd(), bytes.as_ptr().cast(), bytes.len())
                };
                assert_eq!(n, bytes.len() as isize);
            }

            /// Everything the session wrote so far (bounded: stops after
            /// 50 ms without new bytes).
            fn drain(&self) -> Vec<u8> {
                let mut out = Vec::new();
                let mut buffer = [0u8; 4096];
                let mut idle = 0;
                while idle < 5 {
                    // SAFETY: valid buffer; master is nonblocking.
                    let n = unsafe {
                        libc::read(
                            self.master.as_raw_fd(),
                            buffer.as_mut_ptr().cast(),
                            buffer.len(),
                        )
                    };
                    if n > 0 {
                        out.extend_from_slice(&buffer[..n as usize]);
                        idle = 0;
                    } else {
                        idle += 1;
                        std::thread::sleep(Duration::from_millis(10));
                    }
                }
                out
            }

            fn flags(
                &self,
            ) -> (
                libc::tcflag_t,
                libc::tcflag_t,
                libc::tcflag_t,
                libc::tcflag_t,
            ) {
                let mut termios = std::mem::MaybeUninit::<libc::termios>::uninit();
                // SAFETY: written by tcgetattr on success.
                unsafe {
                    assert_eq!(libc::tcgetattr(self.slave(), termios.as_mut_ptr()), 0);
                    let t = termios.assume_init();
                    (t.c_iflag, t.c_oflag, t.c_cflag, t.c_lflag)
                }
            }
        }

        fn handler(signal: libc::c_int) -> libc::sighandler_t {
            // SAFETY: query only.
            unsafe {
                let mut action: libc::sigaction = std::mem::zeroed();
                libc::sigaction(signal, std::ptr::null(), &mut action);
                action.sa_sigaction
            }
        }

        fn open(pty: &Pty, screen: Screen) -> TerminalSession {
            TerminalSession::open_with(screen, Endpoints::terminal(pty.slave(), false)).unwrap()
        }

        fn wait(session: &mut TerminalSession) -> Event {
            session
                .next_event_timeout(Duration::from_secs(5))
                .expect("producers alive")
                .expect("event before timeout")
        }

        /// Wait (bounded) until a message is pending, without taking it.
        fn wait_pending(session: &TerminalSession) {
            for _ in 0..500 {
                if session.peek_event_stamp().unwrap().is_some() {
                    return;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            panic!("no message became pending");
        }

        #[test]
        fn inline_session_acquires_raw_mode_and_restores_in_go_order() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(100, 30);
            let before = pty.flags();
            let mut session = open(&pty, Screen::Inline);
            let (iflag, oflag, _, lflag) = pty.flags();
            assert_eq!(
                lflag & (libc::ICANON | libc::ECHO | libc::ISIG | libc::IEXTEN),
                0
            );
            assert_eq!(oflag & libc::OPOST, 0);
            assert_eq!(iflag & (libc::ICRNL | libc::IXON), 0);
            assert_eq!(
                wait(&mut session),
                Event::Resize(Size {
                    width: 100,
                    height: 30
                })
            );
            assert_eq!(
                session.size(),
                Some(Size {
                    width: 100,
                    height: 30
                })
            );
            pty.type_bytes(b"\x1b[A");
            assert_eq!(wait(&mut session), Event::Key(Key::new(KeyCode::Up)));
            pty.type_bytes(b"\x03");
            let Event::Key(key) = wait(&mut session) else {
                panic!()
            };
            assert_eq!(key.name(), "ctrl+c");
            session.draw("first\n");
            session.close("done\n").unwrap();
            assert_eq!(pty.flags(), before);
            let out = pty.drain();
            assert!(out.starts_with(b"\x1b[?25l\x1b[?2004h"), "{out:?}");
            assert!(out.windows(10).any(|w| w == b"done\x1b[0K\r\n"), "{out:?}");
            assert!(
                out.ends_with(&[b"\x1b[100D".as_slice(), TEARDOWN].concat()),
                "{out:?}"
            );
            assert!(lock(&ACTIVE).is_none());
        }

        /// One read's keys are handed off one at a time: the reader decodes
        /// "ab\x1b[A" into two messages and holds the second until the first
        /// is taken; a chunk typed meanwhile is read only afterwards.
        #[test]
        fn one_read_is_handed_off_message_by_message() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let mut session = open(&pty, Screen::Inline);
            assert!(matches!(wait(&mut session), Event::Resize(_)));
            pty.type_bytes(b"ab\x1b[A");
            wait_pending(&session);
            std::thread::sleep(Duration::from_millis(20));
            // Typed while "ab" is pending: Go's reader is blocked, so these
            // bytes are read later as their own chunk.
            pty.type_bytes(b"cd");
            assert_eq!(wait(&mut session), Event::Key(Key::runes("ab")));
            assert_eq!(wait(&mut session), Event::Key(Key::new(KeyCode::Up)));
            assert_eq!(wait(&mut session), Event::Key(Key::runes("cd")));
            session.close("").unwrap();
        }

        #[test]
        fn fullscreen_session_enters_and_leaves_the_alternate_screen() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(80, 24);
            let session = open(&pty, Screen::Fullscreen);
            session.close("x").unwrap();
            let out = pty.drain();
            assert!(
                out.starts_with(b"\x1b[?25l\x1b[?1049h\x1b[2J\x1b[H\x1b[?25l\x1b[?2004h"),
                "{out:?}"
            );
            // No resize was observed, so the frame is neither truncated nor
            // line-erased (Go's first flush can also precede the size).
            assert!(out.windows(8).any(|w| w == b"\rx\x1b[1;0H"), "{out:?}");
            assert!(out.ends_with(&[TEARDOWN, ALT_EXIT].concat()), "{out:?}");
        }

        #[test]
        fn drop_without_close_restores_without_a_final_frame() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let before = pty.flags();
            let mut session = open(&pty, Screen::Inline);
            session.draw("frame");
            let mut out = Vec::new();
            for _ in 0..50 {
                out.extend(pty.drain());
                if out.windows(5).any(|w| w == b"frame") {
                    break;
                }
            }
            assert!(out.windows(5).any(|w| w == b"frame"), "{out:?}");
            drop(session);
            assert_eq!(pty.drain(), TEARDOWN);
            assert_eq!(pty.flags(), before);
        }

        #[test]
        fn partial_acquisition_rolls_back_completed_steps() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let before = pty.flags();
            let int_before = handler(libc::SIGINT);
            let error =
                TerminalSession::open_with(Screen::Inline, Endpoints::terminal(pty.slave(), true))
                    .err()
                    .expect("injected failure");
            assert_eq!(error.to_string(), "injected failure");
            assert_eq!(pty.flags(), before);
            assert_eq!(handler(libc::SIGINT), int_before);
            assert_eq!(
                pty.drain(),
                [b"\x1b[?25l\x1b[?2004h".as_slice(), TEARDOWN].concat()
            );
            assert!(lock(&ACTIVE).is_none());
            // The record is free for the next session.
            open(&pty, Screen::Inline).close("").unwrap();
        }

        #[test]
        fn only_one_session_is_active() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let session = open(&pty, Screen::Inline);
            let error =
                TerminalSession::open_with(Screen::Inline, Endpoints::terminal(pty.slave(), false))
                    .err()
                    .expect("second session refused");
            assert_eq!(error.to_string(), "terminal session already active");
            session.close("").unwrap();
        }

        #[test]
        fn owner_thread_panic_restores_the_terminal_once() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let before = pty.flags();
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let _session = open(&pty, Screen::Fullscreen);
                panic!("injected panic");
            }));
            assert!(result.is_err());
            assert_eq!(pty.flags(), before);
            assert!(lock(&ACTIVE).is_none());
            let out = pty.drain();
            // The hook (kill semantics) restored first; the unwinding Drop
            // found nothing left to restore and wrote nothing more.
            assert!(out.ends_with(&[TEARDOWN, ALT_EXIT].concat()), "{out:?}");
            assert_eq!(
                out.windows(TEARDOWN.len())
                    .filter(|w| *w == TEARDOWN)
                    .count(),
                1
            );
        }

        /// N1: a background panic restores the terminal, then reaches the
        /// driver as Event::Panicked ahead of pending input; nothing is drawn
        /// afterwards and the queue then closes.
        #[test]
        fn background_panic_is_delivered_after_restoration() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let before = pty.flags();
            let mut session = open(&pty, Screen::Inline);
            assert!(matches!(wait(&mut session), Event::Resize(_)));
            pty.type_bytes(b"x");
            wait_pending(&session);
            let worker = std::thread::spawn(|| panic!("background boom"));
            assert!(worker.join().is_err());
            assert_eq!(pty.flags(), before, "restored before delivery");
            assert_eq!(
                wait(&mut session),
                Event::Panicked("background boom".into())
            );
            assert_eq!(
                session.next_event_timeout(Duration::from_millis(20)),
                Err(Closed)
            );
            let restored = pty.drain();
            assert!(restored.ends_with(TEARDOWN), "{restored:?}");
            session.draw("never drawn");
            drop(session);
            assert_eq!(pty.drain(), b"");
        }

        /// P2: panics inside `recovered` are left to their caller.
        #[test]
        fn recovered_task_panics_keep_the_session() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let mut session = open(&pty, Screen::Inline);
            assert!(matches!(wait(&mut session), Event::Resize(_)));
            let runtime = tokio::runtime::Builder::new_current_thread()
                .build()
                .unwrap();
            let joined = runtime.block_on(async {
                tokio::spawn(recovered(async { panic!("recovered boom") })).await
            });
            assert!(joined.unwrap_err().is_panic());
            assert!(lock(&ACTIVE).is_some());
            assert_eq!(
                session.next_event_timeout(Duration::from_millis(50)),
                Ok(None)
            );
            session.close("").unwrap();
        }

        /// N2: INT/TERM dispositions return when the Quit is accepted, not
        /// when it is read; SIGQUIT stays handled until teardown.
        #[test]
        fn sigterm_dispositions_return_when_quit_is_accepted() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let (int_before, term_before) = (handler(libc::SIGINT), handler(libc::SIGTERM));
            let quit_before = handler(libc::SIGQUIT);
            let mut session = open(&pty, Screen::Inline);
            assert!(matches!(wait(&mut session), Event::Resize(_)));
            assert_ne!(handler(libc::SIGTERM), term_before);
            assert_ne!(handler(libc::SIGQUIT), quit_before);
            // SAFETY: the session's handler is installed.
            unsafe { libc::raise(libc::SIGTERM) };
            wait_pending(&session);
            // Read but not yet accepted: still handled, and a second one is
            // swallowed like Go's blocked signal goroutine.
            assert_ne!(handler(libc::SIGTERM), term_before);
            // SAFETY: as above.
            unsafe { libc::raise(libc::SIGINT) };
            assert_eq!(wait(&mut session), Event::Quit);
            assert_eq!(handler(libc::SIGTERM), term_before);
            assert_eq!(handler(libc::SIGINT), int_before);
            assert_ne!(handler(libc::SIGQUIT), quit_before);
            assert_eq!(
                session.next_event_timeout(Duration::from_millis(50)),
                Ok(None)
            );
            session.close("").unwrap();
            assert_eq!(handler(libc::SIGQUIT), quit_before);
        }

        /// N7: a signal overtakes keys the reader has not handed off.
        #[test]
        fn signals_overtake_keys_not_yet_handed_off() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let mut session = open(&pty, Screen::Inline);
            assert!(matches!(wait(&mut session), Event::Resize(_)));
            pty.type_bytes(b"a\x1b[A");
            wait_pending(&session);
            std::thread::sleep(Duration::from_millis(20));
            // SAFETY: the session's handler is installed.
            unsafe { libc::raise(libc::SIGTERM) };
            for _ in 0..500 {
                if lock(&session.queue.lanes).quit.is_some() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            assert_eq!(wait(&mut session), Event::Key(Key::runes("a")));
            assert_eq!(wait(&mut session), Event::Quit);
            assert_eq!(wait(&mut session), Event::Key(Key::new(KeyCode::Up)));
            session.close("").unwrap();
        }

        /// Initial size, one pending SIGWINCH and one coalesced SIGWINCH,
        /// re-queried at acceptance like Go's resize goroutine.
        #[test]
        fn resizes_are_independent_and_coalesced() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let winch_before = handler(libc::SIGWINCH);
            let mut session = open(&pty, Screen::Inline);
            assert!(
                session
                    .next_event_timeout(Duration::from_millis(20))
                    .ok()
                    .flatten()
                    .is_some()
            );
            pty.resize(120, 40);
            // Exactly two signals: seeing resize_again proves both were
            // consumed. A third could arrive after acceptance and legitimately
            // create another resize, making the old test timing-dependent.
            for _ in 0..2 {
                // SAFETY: the session's handler is installed.
                unsafe { libc::raise(libc::SIGWINCH) };
            }
            for _ in 0..500 {
                if lock(&session.queue.lanes).resize_again {
                    break;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            pty.resize(80, 20);
            assert_eq!(
                wait(&mut session),
                Event::Resize(Size {
                    width: 120,
                    height: 40
                })
            );
            assert_eq!(
                wait(&mut session),
                Event::Resize(Size {
                    width: 80,
                    height: 20
                })
            );
            assert_eq!(
                session.next_event_timeout(Duration::from_millis(50)),
                Ok(None)
            );
            session.close("").unwrap();
            assert_eq!(handler(libc::SIGWINCH), winch_before);
        }

        #[test]
        fn async_consumers_receive_events_and_are_cancel_safe() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let mut session = open(&pty, Screen::Inline);
            let runtime = tokio::runtime::Builder::new_current_thread()
                .build()
                .unwrap();
            let first = runtime.block_on(session.next_event());
            assert_eq!(
                first,
                Some(Event::Resize(Size {
                    width: 40,
                    height: 10
                }))
            );
            pty.type_bytes(b"q");
            let second = runtime.block_on(session.next_event());
            assert_eq!(second, Some(Event::Key(Key::runes("q"))));
            assert_eq!(session.try_next_event_stamped(), Ok(None));
            {
                let mut pending = std::pin::pin!(session.next_event_stamped());
                let mut context = Context::from_waker(std::task::Waker::noop());
                assert!(pending.as_mut().poll(&mut context).is_pending());
            }
            let before = Instant::now();
            pty.type_bytes(b"a");
            let ready = runtime.block_on(session.event_ready()).unwrap();
            assert_eq!(session.peek_event_stamp(), Ok(Some(ready)));
            let (at, event) = runtime.block_on(session.next_event_stamped()).unwrap();
            assert_eq!((at, event), (ready, Event::Key(Key::runes("a"))));
            assert!(at >= before);
            pty.type_bytes(b"\x1b[B");
            wait_pending(&session);
            let (later, event) = session.try_next_event_stamped().unwrap().unwrap();
            assert_eq!(event, Event::Key(Key::new(KeyCode::Down)));
            assert!(later >= at);
            session.close("").unwrap();
        }

        /// Environment variable naming the PTY slave for the re-executed child.
        const QUIT_CHILD: &str = "PB_TUI_TEST_SIGQUIT_DURING_CLOSE_SLAVE";

        /// Child half of `sigquit_during_close_exits_2_and_leaves_raw_mode`;
        /// a no-op in an ordinary test run.
        #[test]
        fn sigquit_during_close_child() {
            let Some(path) = std::env::var_os(QUIT_CHILD) else {
                return;
            };
            let path =
                std::ffi::CString::new(std::os::unix::ffi::OsStrExt::as_bytes(path.as_os_str()))
                    .unwrap();
            // SAFETY: valid C string; the descriptor lives until process exit.
            let fd = unsafe { libc::open(path.as_ptr(), libc::O_RDWR | libc::O_NOCTTY) };
            assert!(fd >= 0);
            let session =
                TerminalSession::open_with(Screen::Fullscreen, Endpoints::terminal(fd, false))
                    .unwrap();
            super::RAISE_QUIT_BEFORE_RAW_RESTORE.store(true, Ordering::Relaxed);
            let _ = session.close("x");
            // Reached only if SIGQUIT was not handled as Go's runtime does.
            std::process::exit(99);
        }

        /// R2-2: SIGQUIT while closing a fullscreen session — after the
        /// display bytes and the alt-screen sleep, before the termios restore
        /// — still takes Go's path: `SIGQUIT: quit`, exit status 2 (not death
        /// by signal 3), terminal left raw.
        #[test]
        fn sigquit_during_close_exits_2_and_leaves_raw_mode() {
            use std::os::unix::process::ExitStatusExt;
            let pty = Pty::new(40, 10);
            let before = pty.flags();
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "tui::terminal::tests::pty::sigquit_during_close_child",
                    "--test-threads=1",
                ])
                .env(
                    QUIT_CHILD,
                    <std::ffi::OsStr as std::os::unix::ffi::OsStrExt>::from_bytes(
                        pty.path.as_bytes(),
                    ),
                )
                .stdin(std::process::Stdio::null())
                .output()
                .unwrap();
            let stderr = String::from_utf8_lossy(&output.stderr);
            assert_eq!(
                (output.status.code(), output.status.signal()),
                (Some(2), None),
                "stderr: {stderr}"
            );
            assert!(stderr.contains("SIGQUIT: quit\n"), "stderr: {stderr}");
            let (_, _, _, lflag) = pty.flags();
            assert_eq!(lflag & (libc::ICANON | libc::ECHO), 0, "raw mode left set");
            assert_ne!(pty.flags(), before);
            let out = pty.drain();
            assert!(out.ends_with(&[TEARDOWN, ALT_EXIT].concat()), "{out:?}");
        }

        /// The select reader (macOS /dev/tty) delivers keys and is cancelled
        /// by close with nothing typed, like cancelreader's select path.
        #[test]
        fn select_reader_reads_and_close_cancels_it() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let before = pty.flags();
            let mut session =
                TerminalSession::open_with(Screen::Inline, Endpoints::terminal_select(pty.slave()))
                    .unwrap();
            assert!(matches!(wait(&mut session), Event::Resize(_)));
            pty.type_bytes(b"\x1b[A");
            assert_eq!(wait(&mut session), Event::Key(Key::new(KeyCode::Up)));
            // The reader is now blocked in select with no input pending.
            std::thread::sleep(Duration::from_millis(20));
            // Bounded: a reader that ignores the wake would hang close.
            let (done, closed) = std::sync::mpsc::channel();
            std::thread::spawn(move || done.send(session.close("")).unwrap());
            let result = closed
                .recv_timeout(Duration::from_secs(5))
                .expect("close did not cancel the select reader");
            result.unwrap();
            assert_eq!(pty.flags(), before);
            // Nothing was consumed after the cancellation.
            // The restored terminal is canonical again, so send a full line.
            pty.type_bytes(b"z\n");
            let mut leftover = [0u8; 8];
            // SAFETY: valid buffer; the slave is blocking but has a line.
            let n =
                unsafe { libc::read(pty.slave(), leftover.as_mut_ptr().cast(), leftover.len()) };
            assert_eq!(&leftover[..n.max(0) as usize], b"z\n");
        }

        /// Environment variable naming the PTY slave for the /dev/tty child.
        const DEV_TTY_CHILD: &str = "PB_TUI_TEST_DEV_TTY_SLAVE";

        /// Child half of `process_input_from_dev_tty_reads_and_cancels`: stdin
        /// is not a terminal, so the production path opens /dev/tty (the
        /// controlling PTY the parent assigned). A no-op in ordinary runs.
        #[test]
        fn dev_tty_child() {
            if std::env::var_os(DEV_TTY_CHILD).is_none() {
                return;
            }
            let mut session = TerminalSession::open(Screen::Inline).unwrap();
            assert_eq!(wait(&mut session), Event::Key(Key::runes("x")));
            let started = Instant::now();
            session.close("").unwrap();
            let elapsed = started.elapsed();
            assert!(elapsed < Duration::from_secs(2), "close took {elapsed:?}");
            eprintln!("dev-tty-child: closed in {elapsed:?}");
        }

        /// The real /dev/tty input path (Go opens it when stdin is not a
        /// terminal): a key typed on the controlling terminal arrives, and
        /// close cancels the idle reader instead of waiting for another
        /// key. On macOS this is the select fallback; elsewhere poll.
        #[test]
        fn process_input_from_dev_tty_reads_and_cancels() {
            use std::os::unix::process::CommandExt;
            let pty = Pty::new(40, 10);
            let before = pty.flags();
            let path = pty.path.clone();
            let mut command = std::process::Command::new(std::env::current_exe().unwrap());
            command
                .args([
                    "--exact",
                    "tui::terminal::tests::pty::dev_tty_child",
                    "--test-threads=1",
                    "--nocapture",
                ])
                .env(DEV_TTY_CHILD, "1")
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped());
            // SAFETY: only async-signal-safe calls between fork and exec.
            unsafe {
                command.pre_exec(move || {
                    if libc::setsid() < 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                    let fd = libc::open(path.as_ptr(), libc::O_RDWR);
                    if fd < 0 || libc::ioctl(fd, libc::TIOCSCTTY as _, 0) < 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                    libc::close(fd);
                    Ok(())
                });
            }
            let mut child = command.spawn().unwrap();
            // Type once the child has made its /dev/tty raw.
            let deadline = Instant::now() + Duration::from_secs(20);
            while pty.flags().3 & libc::ICANON != 0 {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    panic!(
                        "child never entered raw mode: {:?}",
                        child.wait_with_output()
                    );
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            pty.type_bytes(b"x");
            let deadline = Instant::now() + Duration::from_secs(20);
            while child.try_wait().unwrap().is_none() {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    panic!(
                        "/dev/tty reader was not cancelled: {:?}",
                        child.wait_with_output()
                    );
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            let output = child.wait_with_output().unwrap();
            let stderr = String::from_utf8_lossy(&output.stderr);
            let stdout = String::from_utf8_lossy(&output.stdout);
            assert!(
                output.status.success(),
                "stdout: {stdout}\nstderr: {stderr}"
            );
            assert!(
                stderr.contains("dev-tty-child: closed in"),
                "stderr: {stderr}"
            );
            assert!(stdout.contains("1 passed"), "stdout: {stdout}");
            assert_eq!(pty.flags(), before);
        }

        /// Closing the master races two kernel states on Linux: pty_close sets
        /// TTY_OTHER_CLOSED and wakes readers (n_tty_read → EIO) before
        /// tty_vhangup installs the hung-up fops (read → 0). Go's cancelreader
        /// (epoll + file.Read) sees the same race: EOF ends readLoop silently,
        /// EIO is sent to p.errs and Run returns it. Both are asserted as the
        /// OS outcome; neither is mapped onto the other.
        #[test]
        fn hangup_surfaces_the_kernel_outcome_like_go() {
            let _serial = lock(&SERIAL);
            let pty = Pty::new(40, 10);
            let mut session = open(&pty, Screen::Inline);
            assert!(matches!(wait(&mut session), Event::Resize(_)));
            let Pty { master, slave, .. } = pty;
            drop(master);
            // Synchronize on the reader thread itself ending, not on a quiet
            // interval: both outcomes end it without consumption (EOF returns;
            // EIO is offered into the empty input lane, which never blocks,
            // then returns). Bounded so a wedged reader fails the test.
            let reader = session.reader.as_ref().expect("reader running");
            let deadline = Instant::now() + Duration::from_secs(5);
            while !reader.is_finished() {
                assert!(
                    Instant::now() < deadline,
                    "reader did not finish after hang-up"
                );
                std::thread::sleep(Duration::from_millis(5));
            }
            // Its only possible output is now already in its lane, and only
            // the signal sender remains.
            assert_eq!(lock(&session.queue.lanes).producers, 1);
            match session.try_next_event_stamped() {
                // EOF (hung up first): no event.
                Ok(None) => {}
                // EIO (other side closed first): Go's error text, sent once.
                Ok(Some((_, Event::InputError(message)))) => assert_eq!(
                    message,
                    "error reading input: read /dev/stdin: input/output error"
                ),
                other => panic!("unexpected hang-up outcome: {other:?}"),
            }
            // Nothing further can come from input, and the queue stays open
            // because the signal sender is live.
            assert_eq!(session.try_next_event_stamped(), Ok(None));
            // SAFETY: the session's handler is still installed.
            unsafe { libc::raise(libc::SIGTERM) };
            assert_eq!(wait(&mut session), Event::Quit);
            // Restoring a hung-up terminal may fail; the failure is reported.
            if let Err(error) = session.close("") {
                assert!(
                    error.to_string().starts_with("error restoring console: "),
                    "{error}"
                );
            }
            drop(slave);
        }
    }
}
