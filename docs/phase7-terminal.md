# Phase 7 — terminal lifecycle and input

Scope of this document: `src/tui/mod.rs`, `src/tui/terminal.rs`, `src/tui/input.rs`.
Editor (`editor.rs`) and theme/style (`style.rs`) have separate owners and documents. The
shared contract for workflow screens is `/home/ajs/.cache/pb-phase7/terminal-api.md` (v1.3).

Baseline: Go `07af390` (bubbletea v1.2.2, x/term via charmbracelet/x/term v0.2.1,
charmbracelet/x/ansi v0.4.5). Evidence tiers below: **[Source]** read in the pinned Go module
cache; **[Oracle]** Go process captured by the parent's lifecycle/differential oracles
(`oracle-lifecycle-1`, `differential-debug1-full`); **[Unit]** Rust unit/PTY tests written here,
run centrally by the parent (this agent ran no command: the harness `/tmp` quota blocked shell
output).

## Design

One process-wide `TerminalSession` (RAII) per interactive program, created with
`TerminalSession::open(Screen::{Inline, Fullscreen})`. No ratatui: Bubble Tea's frame
semantics (line diff, top-line dropping, last-line erase at exit, cursor parking) are part of
the visible inline/scrollback contract, and an owned renderer reproduces them exactly.
crossterm is used only on Windows.

Threads, all owned and joined by the session:

* **input thread** (`pb-tui-input`) = tea `readLoop`: polls the input fd and the wake pipe,
  decodes each `read(2)` (256-byte buffer) with `input::Parser`, and hands off **one message
  at a time**, blocking until it is accepted before decoding the next or reading again.
* **signal thread** (`pb-tui-signals`) = tea `handleSignals` + `listenForResize`: polls the
  process signal pipe and the wake pipe; independent of the input thread.
* **writer thread** (`pb-tui-writer`): owns `Renderer`; `draw` only replaces the pending view
  (Go's buffer + ticker semantics: intermediate views may be skipped, the latest is always
  rendered), so neither the caller nor a Tokio driver ever blocks on the terminal.

### Message lanes and arrival order ([Source] tea.go eventLoop/handleSignals, key.go readAnsiInputs, signals_unix.go)

Go has one **unbuffered** `p.msgs` with independent senders; each blocked sender waits in the
channel's FIFO send queue, so the loop receives in offer order. Rust models each sender as a
lane holding at most one offered message, stamped with `Instant::now()` when offered:

| Lane | Go sender | Capacity / rule |
|---|---|---|
| input | `readLoop` (keys, unknown input, read error) | 1; the reader blocks until it is taken, so it reads no further meanwhile (bytes typed then arrive in the next chunk, as in Go) |
| initial resize | startup `go p.checkResize()` | one-shot |
| resize | `listenForResize` | 1 pending + Go's one-slot SIGWINCH buffer: a further SIGWINCH while pending sets a flag; at acceptance the size is re-queried and offered (stamped then) |
| quit | `handleSignals` | the single `QuitMsg`; later INT/TERM before acceptance are swallowed |

A take returns the earliest stamp (ties: input, initial resize, resize, quit). Taking is
acceptance: it releases the reader; taking the Quit restores INT/TERM (Go's deferred
`signal.Stop` runs after the send). Nothing is dropped or filtered; no staleness rules exist.

Consequences (tested): k2 of a burst is offered when k1 is accepted, so a completion arriving
while k1 is processed follows k2 (the review's "k1, completion, k2" holds only if the
completion predates that offer — Go source does not support it otherwise); a signal or resize
overtakes keys the reader has not handed off (N7). This is a model of Go's senders, not a
universal exact-interleaving guarantee: goroutine scheduling and the runner's own completion
stamps (taken at handoff) remain approximations.

Consumer API: `next_event_stamped().await` / `try_next_event_stamped()` (take = accept),
`peek_event_stamp()` / `event_ready().await` (inspect the head without accepting), plain
`next_event().await` / `next_event_blocking()` wrappers. A runner merging its completions must
compare heads with peek and take only the winner; pre-taking a head accepts it early (e.g. a
pre-taken Quit restores dispositions too soon). All async waits are cancel-safe.

### Acquisition ([Source] tea.go Run, tty_unix.go, standard_renderer.go)

| Step | Go | Rust |
|---|---|---|
| input | stdin if `IsTerminal`, else `os.Open("/dev/tty")`; failure → `could not open a new TTY: open /dev/tty: <errno>` | same text (`go_errno` = strerror, lowercased first letter) |
| signals | `handleSignals` before `initTerminal`; SIGWINCH only if stdout is a terminal | handlers installed before raw mode (N6): SIGINT, SIGTERM, SIGQUIT, SIGWINCH (stdout TTY); previous actions saved |
| raw mode | only if the input is a terminal; x/term MakeRaw → `error entering raw mode: …` | identical flag set, TCSANOW |
| output | `ESC[?25l`; alt: `ESC[?1049h ESC[2J ESC[H ESC[?25l`; `ESC[?2004h` | identical bytes, same order |
| size | initial `WindowSizeMsg` from its own goroutine | initial-resize lane (stdout TTY only) |

Signal self-pipe (N4): created once per process (`pipe2(O_CLOEXEC|O_NONBLOCK)`), never closed,
drained at each session start, so a handler still running elsewhere after teardown can never
write into a reused descriptor. Session wake pipes use `pipe2(O_CLOEXEC)` atomically on
Linux/Android/BSD (N5), `pipe`+`fcntl` on macOS.

[Oracle] non-TTY login (no controlling terminal): exit 1, stderr
`Error: could not open a new TTY: open /dev/tty: no such device or address` + usage — the
session error text matches; the `Error:`/usage framing belongs to the command adapter.

### Rendering ([Source] standard_renderer.go flush/write)

`Renderer::frame` reproduces `write` + `flush`: empty view → `" "`; identical view → nothing;
`CursorUp(lines-1)`; drop top lines beyond height; unchanged lines skipped with `ESC[B`; `\r`
before the first line of a fresh render; truncate to width (`style::truncate` = ansi.Truncate);
`ESC[0K` when narrower than the width; `ESC[0J` when shrinking; park with `ESC[<n>;0H` (alt)
or `CursorLeft(width)` (inline; `ESC[D` when width ≤ 1). Resize repaints on the next write.
Runners must draw after every event, as tea renders after every Update.

Views are Go strings, i.e. bytes. The renderer caches and diffs bytes (`frame(&[u8])`), and
`draw_bytes`/`close_bytes` accept raw byte views (the SQL raw sidebar's styled, possibly
non-UTF-8 cells) with no lossy conversion before the clip/width step; `draw`/`close` are
wrappers over the same path and cache. Per line, valid UTF-8 uses `style::truncate`/
`style::width` (every `&str` frame is byte-identical to before); other lines use the style
owner's `truncate_bytes`/`width_bytes`. With an unknown width nothing is clipped or measured
(Go's `StringWidth(line) < 0` is never true).

### Teardown ([Source] tea.go shutdown/restoreTerminalState; [Oracle] byte tail)

`close(final_view)`: stop and join the input and signal threads → restore INT/TERM/WINCH →
writer flushes the final view then `ESC[2K\r` (renderer.stop) → `ESC[?2004l ESC[?25h ESC[?1002l
ESC[?1003l ESC[?1006l` → alt: `ESC[?1049l ESC[?25h` + 10 ms → termios restore (error →
`error restoring console: <errno>`, returned; Go ignores it) → SIGQUIT disposition restored
last (R2-2). [Oracle] login cancel tail is
byte-identical: `…\x1b[0K\r\n\x1b[0K\x1b[100D\x1b[2K\r\x1b[?2004l\x1b[?25h\x1b[?1002l\x1b[?1003l\x1b[?1006l`.

`Drop` without `close` = tea Kill: no final frame, same sequence. Restoration is recorded in one
`Restore` record; every path `take()`s it, so it is idempotent. Partial acquisition failure
rolls back exactly the recorded steps. Frames and restoration bytes are serialized by
`OUTPUT`; the record lock is never held across a write; no frame is written after restoration.

### Panics (N1, P2)

Go: command goroutines and the event loop run under `recoverFromPanic` — `shutdown(true)`
(kill-restore), `Caught panic:` on stdout, stack on stderr; the program ends. Rust: the chained
hook (installed once) restores the terminal (kill semantics) and then marks the session's lanes
killed, so `Event::Panicked(message)` is delivered ahead of the terminal's own pending messages,
after which the lanes report `Closed`. Its stamp is the time it is peeked: a runner merging by
stamp may first process completions stamped earlier (R2-1). Go has the same freedom — its
eventLoop `select` may pick a pending message over `ctx.Done` — so this is documented
nondeterminism, not new urgent behaviour (L-TERM-9). The terminal is restored before the event is observable; the
terminal module prints nothing to stdout (workflows print Go's `Caught panic:` text and choose
the outcome, e.g. ErrProgramKilled for command goroutines). The previous hook still reports to
stderr (Go's stack trace analogue).

`recovered(future)` marks a poll scope whose panics the caller recovers itself (Go's local
`defer recover()`, e.g. `NewPromqlModeFetchTask`): inside it the hook does nothing at all — no
restore, no event, and no chained stderr report (Go's local recover prints nothing). The scope
is a thread-local depth counter around each poll, unwound by a drop guard. Limitation: the
hook is installed at the first session open; a recovered panic before any session ever opened
still reaches the default hook (not a reachable pb path).

### OS writer semantics

The renderer discards `Write` errors, but the descriptor contract underneath is preserved
(as `main.rs` `SignalWriter`): EINTR retry; EAGAIN on an inherited O_NONBLOCK stdout waits for
POLLOUT without touching flags (Go poller); a 0-byte write ends that write
(io.ErrUnexpectedEOF); EPIPE on fd 1/2 → default SIGPIPE + raise (Go `epipecheck`), exit 1 if
delivery fails; other errors abandon the rest of that frame. A stalled stdout blocks
`close`/panic restoration exactly like Go's renderer mutex.

### Input ([Source] key.go, key_sequences.go)

`Key { code, runes, alt, paste }` with `name()` == `tea.Key.String()`; `Key::is(&[..])` ==
`key.Matches`. Parser = detectOneMsg: X10/SGR mouse, whole-input focus reports, bracketed
paste (waits for the end marker even after a short read), longest-prefix `extSequences`
(ESC-prefixed alt variants, C0 controls except NUL/ESC, space, `alt+esc`), unknown CSI
(`^\x1b\[[0-?]*[ -/]*[@-~]`), standalone NUL (`ctrl+@`, Ctrl+Space), rune runs (one key per
read, one rune after ESC), lone ESC at end of read = `esc`, invalid byte → `Unknown`. Full reads
(256 bytes) defer a trailing rune run; like Go, a UTF-8 sequence split across reads still
decodes as invalid bytes.

### Signals and events

* SIGINT/SIGTERM → `Event::Quit` (tea.QuitMsg: end the loop without updating the model; close
  with the current view). INT/TERM dispositions are restored when the Quit is **accepted**
  (N2); until then further INT/TERM are swallowed, as by Go's blocked signal goroutine.
* [Oracle] login: Ctrl+C, SIGINT, SIGTERM → exit 0, termios restored; SIGHUP → signal 1, not
  restored (Rust: default disposition, same).
* SIGQUIT: [Oracle] Go writes `SIGQUIT: quit\nPC=0x… m=0 sigcode=0\n\ngoroutine 0 …` (24 KiB
  runtime dump) to stderr, exit 2, terminal left raw. Rust: handler writes `SIGQUIT: quit\n` to
  fd 2 and `_exit(2)` (only the first line; no PC/register/goroutine dump). Window = the whole
  acquired-terminal interval: installed in `prepare_signals` (before raw mode) and restored
  **last**, after the termios restore, on every path (`close`, `Drop`, partial-open rollback,
  panic hook) — R2-2. INT/TERM/WINCH are restored earlier, where Go's `handlers.shutdown` runs.
  `_exit(2)` discards Rust-buffered stdout; no flow writes stdout before or during a session.
  Outside sessions the pre-existing difference is ledgered by the parent (not a phase-7 change).
* Input EOF ends input silently (Go io.EOF); other read errors →
  `Event::InputError("error reading input: read /dev/stdin|/dev/tty: <errno>")`, sent once,
  then the reader ends. Terminal hang-up is a kernel race, not a Rust choice: Linux
  `pty_close` sets `TTY_OTHER_CLOSED` and wakes readers (`n_tty_read` → EIO) before
  `tty_vhangup` installs the hung-up fops (read → 0). [Source] Go's cancelreader
  (`cancelreader_linux.go`: epoll wait, then `file.Read`) and readLoop (silent only for
  `io.EOF`/`ErrCanceled`) see the same race, so Go also reports
  `error reading input: read /dev/stdin: input/output error` on the EIO side. Neither outcome
  is mapped onto the other (L-TERM-15).
* SIGWINCH size query failure → `Event::InputError(<errno>)` (Go returns the bare errno error).

## Divergence ledger (terminal)

| ID | Scenario | Go | Rust | Status |
|---|---|---|---|---|
| L-TERM-1 | acquisition fails after cursor/alt/paste were written | returns error, leaves modes set | rolls back recorded steps | internal change in failure-only path (practically unreachable) |
| L-TERM-2 | EPIPE on stdout while input is raw | dies by SIGPIPE, raw mode left | same (preserved) | preserve; proposal: restore termios before raising |
| L-TERM-3 | SIGQUIT during a session | `SIGQUIT: quit` + runtime dump, exit 2, no restore | `SIGQUIT: quit\n`, `_exit(2)`, no restore | termination preserved; dump = scoped diagnostic difference |
| L-TERM-4 | Ctrl+Z / SIGTSTP | raw mode: key `ctrl+z`; external SIGTSTP default stop | same | preserve |
| L-TERM-5 | frame pacing | 60 fps ticker | latest-view writer, flush when idle | internal; final cells equal |
| L-TERM-6 | panic diagnostics | `Caught panic:` stdout (workflow) + Go stack on stderr | workflow prints the text; Rust panic report on stderr; recovered scopes silent | diagnostic difference only |
| L-TERM-7 | Windows | coninput reader | crossterm events mapped to tea keys; one input lane only | uncharacterized; not built on Windows yet |
| L-TERM-8 | arrival order | goroutine scheduling on one unbuffered channel | per-sender lanes stamped at offer; runners merge by stamp | model, not universal exact interleaving. Exact head comparison depends on the **current-thread** runtimes SQL/PromQL use (a completion is stamped and sent within one poll on the driver thread; terminal messages are stamped under the lanes lock); it would not survive a multi-thread runtime |
| L-TERM-9 | panic vs pending completions (R2-1) | eventLoop `select` picks randomly between `ctx.Done` and a pending send | `Panicked` stamped at peek; earlier-stamped completions may be processed first (frames suppressed after restore) | documented nondeterminism, permitted by Go |
| L-TERM-10 | completion stamp under a full channel (C4) | sendq orders by when the send began | runner stamps at `reserve()`; with 16 (SQL) / 64 (PromQL) completions outstanding, a waiting completion sorts after terminal messages offered meanwhile | accepted residual (runner side); keeps head comparison self-consistent |
| L-TERM-11 | reader/writer/signal thread panic (I-1) | `readLoop`/renderer goroutines have no recover: process crash, exit 2, no restore | restore, `Panicked`, command-panic outcome (exit 1) | native design choice for native bugs with no Go-reachable trigger, not parity |
| L-TERM-12 | background panic during an F2 hang (I-3) | with a prior SIGTERM: shutdown blocks on the signal goroutine; otherwise restore + `Caught panic:` then linger | restore; `Panicked` queued but never taken by the hung runner; no `Caught panic:`; lingers | hypothetical (needs a native task panic during a hang); see L-EDITOR-2 |
| L-TERM-13 | reader after a hook restore (I-4) | kill path cancels the reader first | input thread may read one cooked line before teardown | window normally microseconds; unbounded only with L-TERM-12 |
| L-TERM-15 | terminal hang-up while reading | EOF → silent; EIO → Run returns `error reading input: …: input/output error` (kernel race) | identical mapping; same race | preserve; test accepts either OS outcome; probe `probes/pty-hangup` characterizes Go's split |
| L-TERM-14 | signal-pipe creation failure (I-5) | n/a | `OnceLock` caches the error (e.g. EMFILE) for the process; later sessions in the same process fail too | accepted; only multi-session flows (login → device) are affected |

## Review round 1 dispositions (`docs/phase7-review-lifecycle-r1.md`)

| Finding | Disposition |
|---|---|
| N1 background panic leaves zombie session | `Event::Panicked` after restoration; lanes close; `recovered` scope (P2) |
| N2 INT/TERM restored at read | restored at Quit acceptance (consumer side) |
| N3 input errors take kill path | workflow owners (not this module) |
| N4 handler may write reused fd | process-lifetime signal pipe, never closed, drained per session |
| N5 non-atomic CLOEXEC | `pipe2(O_CLOEXEC[\|O_NONBLOCK])` where available |
| N6 raw before signals | signals installed before raw mode |
| N7 controls behind 64 keys | one-message reader handoff + independent quit/resize lanes |
| N8 panic diagnostics | workflow owners |
| Stamped criteria 1, 2, 5, 6, 8 | implemented as above (handoff stamping via one-pending lane; independent controls; cancel-safe; ties documented; no staleness) |
| Stamped criteria 3, 4, 7 | runner-side (bounded heads, completion stamps, SQL merge) — owners |

## Review round 2 dispositions (`docs/phase7-review-lifecycle-r2.md`)

| Finding | Disposition |
|---|---|
| R2-1 `Panicked` not ahead of earlier completions | documented nondeterminism (L-TERM-9); docs in `terminal.rs`/`input.rs` reworded; unit test `panic_stamp_is_its_peek_time` |
| R2-2 SIGQUIT removed before final flush/termios | fixed: `restore_controls` (INT/TERM/WINCH) early, `restore_quit` after `restore_raw` in `restore_now`; process test `sigquit_during_close_exits_2_and_leaves_raw_mode` |
| R2-3 saved read-error after recovered View panic | `saved.rs` owner |
| C4, I-1, I-3, I-4, I-5, current-thread dependency | ledgered (L-TERM-8, -10 … -14) |
| I-6 `promql.rs` Inbox wording | PromQL owner |
| I-7 Windows compile | still unvalidated (L-TERM-7) |

## Tests ([Unit], run by parent)

`cargo test --lib tui::input` and `cargo test --lib tui::terminal::tests -- --test-threads=1`.
Central run unit4: 376 passed, 0 failed (including the PTY lifecycle tests below).

* input: tea key names, alt prefix rules, rune runs, bracketed paste incl. partial, full-read
  deferral and split UTF-8, unknown/mouse/focus input, utf8.DecodeRune parity.
* renderer: oracle final-frame tail, line skipping, shrink erase, empty view, height trimming,
  alt cursor parking, no-size frames, resize repaint.
* lanes: one-message reader handoff with the signal overtaking an un-handed-off key; next key
  offered at acceptance of the previous; panic delivered first then closure; resize coalescing;
  recovered scope nesting/unwinding.
* PTY lifecycle (Linux, serialized): raw flags and exact restore, Go byte order at open/close,
  per-message handoff within one read and later chunks read afterwards, fullscreen, Drop =
  kill, injected partial-failure rollback, single-session guard, owner-thread panic (one
  teardown), background panic → restored then `Panicked` then `Closed` with no later frames,
  recovered task panic keeps the session, Quit-acceptance disposition restore with a swallowed
  second signal, signal overtaking pending keys, resize coalescing/re-query, async/peek/
  cancel-safety, hang-up = the kernel's outcome (EOF silent, or EIO reported once).
* Hang-up history: unit2 observed EOF; unit7 observed EIO (`integration-unit7.log`) with no
  related source change. The earlier "EOF only" expectation was wrong; the test
  (`hangup_surfaces_the_kernel_outcome_like_go`) now accepts exactly those two outcomes, then
  requires the reader to have ended with signals still live. Final review F4: it synchronizes
  on the input thread's `JoinHandle::is_finished()` (bounded 5 s; a wedged reader fails), not
  on quiet intervals. Both outcomes end the reader without consumption (EOF returns; EIO is
  offered into the empty one-slot input lane, which cannot block). After that, the producer
  count is 1 (signal sender only), the lane holds either nothing (EOF) or the one EIO error,
  a second take yields nothing, and SIGTERM still produces `Quit`.
* [Oracle] Go raw-PTY probe (parent run): read blocked at master close → 198 EOF / 2 EIO of
  200; read started 5 ms after close → 200 EOF. Go exhibits the same race; the EIO side is the
  reported error, as in Rust.
* Go raw-PTY probe (parent runs; stdlib only, same epoll + `os.File.Read` path as cancelreader):
  `cd /home/ajs/.cache/pb-phase7/probes/pty-hangup && GOTMPDIR=$TMPDIR go run . 200` —
  reports EOF/EIO counts for a read blocked at master close (the TUI case) and one started 5 ms
  later, plus Go's error text for EIO.
* R2-2 process test: the test binary re-executes itself
  (`--exact tui::terminal::tests::pty::sigquit_during_close_child`, slave path in
  `PB_TUI_TEST_SIGQUIT_DURING_CLOSE_SLAVE`). The child opens a fullscreen session and closes it
  with a test-only trigger that raises SIGQUIT just before `restore_raw`. The parent asserts
  exit status 2 (not signal 3), `SIGQUIT: quit\n` on stderr, the PTY still raw, and the full
  restore byte tail already written. Without the env var the child test is a no-op. It fails on
  the pre-R2-2 code (SIGQUIT already default → death by signal 3).
* R2-1 unit: the panic stamp is its peek time (≥ an earlier completion stamp) and `Panicked`
  still precedes pending lane messages.

`next_event_timeout`, `Key::runes` and `input::parse` are `#[cfg(test)]`.

Still required (process level, parent harness): Rust-vs-Go PTY comparisons per flow, F2-hang +
two SIGTERM case (N2), signal termination per flow, non-TTY with/without controlling TTY,
O_NONBLOCK and broken stdout, panic outcome per workflow.
