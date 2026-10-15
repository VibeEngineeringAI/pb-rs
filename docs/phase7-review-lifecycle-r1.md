# Phase 7 lifecycle review, round 1 (independent, read-only)

Scope: `src/tui/terminal.rs`, `src/tui/input.rs`, `src/tui/clipboard.rs` and the task/shutdown
boundaries in `src/tui/{profile,sql,promql,saved,device}.rs` (plus `login.rs` where it drives
`run_inline`). Base `4a812d5`, Go oracle `07af390` (bubbletea v1.2.2 read from the module cache:
`tea.go`, `tty.go`, `tty_unix.go`). This is a focused lifecycle review. It is **not** phase-wide
acceptance.

Method: static reading only. No builds, tests or profiling were run. The reviewer's shell was
unusable (EDQUOT in the harness `/tmp`), so every claim here comes from reading source. Line
numbers are from the files as read during this round. `terminal.rs` changed by one line during the
review (its line-1 module doc), so its numbers may be off by about 1. Cite functions as well as lines.

## Verdict

**Changes requested. Not accepted.** N1 and N2 below are independent lifecycle defects with
concrete triggers. The new stamped-event boundary (section "Stamped merge boundary") is **not on
disk yet** and has not been reviewed: `terminal.rs` has no `next_event_stamped` /
`try_next_event_stamped`. `promql.rs:4489,4500` already calls `next_event_stamped`, so the tree as
read does not compile. That is consistent with the parent holding builds. The section below gives
the criteria the boundary will be reviewed against. It is not approval.

## Independent new findings

### N1 — Medium: panic hook tears down the terminal for *any* thread's panic, leaving a zombie session

- Code: `terminal.rs` `install_panic_hook` (~367–376) calls `restore_now(true)` unconditionally.
  The writer gate in `write_loop` (~450–454) then suppresses every later frame because `ACTIVE`
  is `None`.
- Trigger: a panic inside any spawned Tokio task. Examples: a service bug in `sql_service` or
  `promql_service`, or the clipboard task. Tokio catches the panic and the drivers ignore it:
  `sql.rs:185` (`Some(_) = tasks.join_next() => continue`), `promql.rs:4584` (`try_join_next`
  discarded), `profile.rs:112` (`Result::ok`), `saved.rs:139`. The session keeps running with
  termios cooked (echo and line buffering on), INT/TERM back at their defaults, cursor shown,
  the alternate screen exited, and no frames drawn. The input thread still reads, so the UI looks frozen.
- Go: every command goroutine has `defer p.recoverFromPanic()` (`tea.go:318–322`), which calls
  `shutdown(true)` and prints `Caught panic:` (`tea.go:701–707`). The context is canceled, the
  event loop returns (`tea.go:345–346`), and `Run` ends. The program never continues after a restore.
- Fix (either):
  1. Store a `Weak<Queue>` in `Restore`. After `restore_now` the hook calls `finish()`, so
     drivers see `None` and end. Add a distinct `Event::Killed` so drivers take the kill path
     rather than the Quit path.
  2. Make drivers treat `JoinError::is_panic()` as a fault: kill path, print the `Caught panic:`
     text, and return as for an `EditorFault`.

  Option 1 also covers panics on the input and writer threads. Add a unit test: spawn a task that
  panics while the session is open, then assert the driver ends and nothing is drawn afterwards.

### N2 — Low/Medium: INT/TERM dispositions are restored when the signal is *read*, not when Quit is *accepted*

- Code: `terminal.rs` `read_loop`, the signal branch (~1061–1071), calls
  `restore.signals.restore(&[SIGINT, SIGTERM])` before `queue.push(Event::Quit)`.
- Go: `handleSignals` does `p.msgs <- QuitMsg{}` on an **unbuffered** channel, then `return`.
  The deferred `signal.Stop` runs only after the event loop has received the message
  (`tea.go:253–270`).
- Trigger (oracle-checkable): editor fault F2 (Ctrl+Left in an empty SQL/PromQL editor, see
  `docs/phase7-editor.md`), then `kill -TERM` twice. Go's event loop never receives the
  QuitMsg, so `Notify` stays active and the second SIGTERM is swallowed. The process lives on, as
  the editor doc says: "ends only by SIGKILL/SIGQUIT". Rust dies by the second SIGTERM and leaves
  raw mode and the alternate screen set. The same divergence appears, more briefly, whenever the
  driver is busy between the read and the dequeue.
- Fix: keep `quit_sent` suppression in the reader, but restore the INT/TERM dispositions in the
  consumer when `Event::Quit` is dequeued. That point is `observe()` and the new stamped/try
  variants. Add a PTY oracle case: F2 hang plus two SIGTERMs, for Go and Rust.

### N3 — Low: input-read errors take the kill path in three workflows; Go takes the graceful path

- Go `tea.go:595–605`: `eventLoop` returns `(model, err)` from `p.errs`. `killed` depends only on
  `ctx.Err()`, so Go renders the final view and runs `shutdown(false)`: wait for the read loop,
  `renderer.stop()` flush, then restore.
- Rust: `profile.rs:120,155–158` (`other => drop(session)`), `saved.rs:146–149` (returns while
  `terminal` drops), and `device.rs:536–538` (`drop(session)`) use Kill. `sql.rs:114–116` and
  `promql.rs:4410,4438` are correct. The comment at `profile.rs:64–65` ("Go's kill path") is
  contradicted by `tea.go:596–601`.
- Visible effect: a pending, unflushed last frame is dropped. That matters mostly for the inline
  profile, login and device screens, which stay in scrollback. Fix: `session.close(&view)`, then
  return the error.

### N4 — Low: the signal handler can write into a reused descriptor

`on_signal` (~739–746) loads `SIGNAL_PIPE` and writes. `restore_all` stores -1 (~765), and then
`teardown` runs `self.io.close()` (~677). A handler on another thread that loaded the fd before
the store can write one byte after the close, into whatever reused that fd number. The window is
narrow. Fix: create the signal self-pipe once per process (static, never closed) and reuse it
across sessions. Alternatively, block INT/TERM/WINCH with `pthread_sigmask` around the
store-and-close, which still leaves other threads exposed. The process-lifetime pipe is the
recommended fix.

### N5 — Low (latent): non-atomic CLOEXEC on the session pipes

`pipe()` followed by `fcntl(F_SETFD, FD_CLOEXEC)` (~794–813) leaves a window in which a
concurrent `fork/exec` inherits both ends. In the reviewed flows nothing spawns concurrently with
`open`: clipboard and browser children start after acquisition. Fix: `pipe2(O_CLOEXEC)` (plus
`O_NONBLOCK` for the signal write end) on Linux and the BSDs, keeping `fcntl` as the macOS fallback.

### N6 — Info: acquisition order

Go installs signal handling (`tea.go:517–520`) before `initTerminal` (raw mode, `tea.go:534`).
Rust enters raw mode and then installs signals (`open_with` ~541–546), so an external SIGINT in
between kills Rust with raw mode left. Go is also racy, because `Notify` runs inside a goroutine,
so this is advisory. Fix: call `prepare_signals` before `make_raw`. Rollback is unchanged.

### N7 — Info, but it matters for the stamped design: signals and resizes share the key FIFO

In Go, QuitMsg comes from `handleSignals`, WindowSizeMsg from `listenForResize`/`checkResize`, and
keys from `readLoop`. They are independent senders on `p.msgs`, so a signal can overtake keys that
have not yet been handed off. The reader holds at most one pending send. In Rust, `Event::Quit` and
`Resize` queue behind up to 64 keys in the same FIFO.

### N8 — Info: inconsistent panic diagnostics

`saved.rs:86–90` writes a Rust `Backtrace::force_capture()` to stderr. With release `strip = true`
it is unsymbolized. `sql.rs:129–138` and `promql.rs:4461–4465` write nothing. Choose one behavior
and record it under L-EDITOR-4 / L-AUTH-10.

## Reviewed without objection (static)

- **RAII/rollback**: one `Restore` record, `take()`-idempotent. Partial failure at each step
  (input, raw, signals, modes, writer, reader) rolls back only what was recorded. Drop gives tea
  Kill. The panic-on-owner-thread ordering is right: the hook restores, then unwinding `Drop`
  writes nothing. N1 covers panics on other threads.
- **Writer**: `OUTPUT` serializes frames and restore bytes. `ACTIVE` is never held across a write.
  EINTR, EAGAIN-via-POLLOUT, 0-byte and EPIPE→SIGPIPE behavior matches `main.rs`
  `SignalWriter`/Go `epipecheck` (L-TERM-2 is preserved). A stalled stdout blocks close and the
  hook, as Go's renderer mutex does.
- **Reader**: wake-pipe stop. `queue.stop()` unblocks a full push. EOF ends input silently while
  signals stay live (Go `io.EOF`, `tty.go:95`). Read errors use the Go text. Rust joins the reader
  without Go's 500 ms `waitForReadLoop` cap (`tty.go:104–112`). That is acceptable because the read
  only follows poll readiness.
- **Teardown order** matches `shutdown()` (`tea.go:668–697`) and `restoreTerminalState`
  (`tty.go:41–60`). There is no output-state restore on Unix, which matches `tty_unix.go:15–30`.
- **Input** (`input.rs`): `detect_one`/`detect_sequence`/`detect_bracketed_paste` and the
  `feed(full_read)` leftover follow readAnsiInputs/detectOneMsg, including the full-read deferral,
  split UTF-8 as invalid bytes, paste waiting after a short read, and lone ESC at the end of a read.
  This was compared against the reviewer's reading of `key.go` and was **not** re-read from the
  module cache this round. The `SEQUENCES` table was not audited entry by entry.
- **Children**: clipboard uses stdin `/dev/null`, captured stdout/stderr, `kill_on_drop` and
  `restore_child_limits`. The browser uses null stdio and is never waited (as Go does). `/dev/tty`
  is opened with `O_CLOEXEC`. std resets SIGPIPE and the signal mask in children. No child can
  touch the session's terminal fds.
- **Task ownership at close**: every driver runs on a current-thread runtime and `close` is
  synchronous inside `block_on`, so no task can be polled during restoration. Profile joins first
  (`reads.shutdown()`), SQL aborts before close and drops via `shutdown_background()`, PromQL
  drops after close (`promql.rs:4438–4439`), saved aborts and joins first, and device drops the
  poll future after close. These differ, but none is observable, and Go leaks command goroutines.
  The F2 hang paths keep the session and tasks alive deliberately (`sql.rs:204–209`,
  `promql.rs:4456–4459`, `saved.rs:71–76`). Combined with N2, see the trigger above.

## Known issues (owner fixing; not re-litigated)

- **Hang-up unit test** (`terminal.rs` `hangup_ends_input_silently_like_go_eof`, `wait()` at
  ~1431): on disk the test now expects a timeout, then `Quit` after `raise(SIGTERM)`. Static
  reading found no reason for Quit to be lost: `raise` targets the calling thread and the handler
  writes the self-pipe. The owner should confirm with a run whether the failure was the old
  expectation (EOF vs EIO) or a lost Quit. This review does not count the test as passing.
- **SIGQUIT (L-TERM-3)**: Go exits 2 with a goroutine dump and no restore. Rust dies by signal 3.
  **Contract objection:** Go's SIGQUIT behavior is process-wide, covering every command in
  phases 1–6, not just interactive ones. A handler installed only inside `TerminalSession` would
  create a session-only divergence. Either decide a process-wide contract (a `main` handler that
  writes a fixed diagnostic and calls `_exit(2)`, async-signal-safe, with no restore), or keep it
  as a documented runtime difference. Note that the buffered `Outcome` stdout is lost either way,
  unlike Go.
- **Goroutine dumps**: an explicit diagnostic difference (L-AUTH-10, L-EDITOR-4, L-SQL-3).

## Stamped merge boundary (requested; not on disk at review time)

Go's ordering model: one unbuffered `p.msgs`. Each sender blocks in a FIFO send queue until the
loop accepts its message. The reader has at most **one** message pending (`readAnsiInputs` sends
each decoded message before decoding the next). Every command goroutine, the signal goroutine and
the resize goroutine are independent senders. Criteria for the new API and inboxes:

1. **Terminal stamp = handoff, not read.** Stamping at "enqueue capacity available" with the
   64-slot queue gives every key of one read burst roughly the read time. That still orders all of
   them before a completion that, in Go, would interleave after the first key. The equivalent of
   Go's unbuffered semantics is `stamp(k+1) = max(push_time(k+1), dequeue_time(k))`, assigned on
   the consumer side, or a reader capacity of 1 for stamping purposes. Show it with a test:
   a 3-key burst plus a completion that arrives while key 1 is processed must yield
   key1, completion, key2, key3.
2. **Signals and resizes keep their own arrival stamps** (N7) and may overtake keys that have not
   been handed off. At minimum, document the residual difference.
3. **Bounded inboxes.** `promql.rs` `Inbox::fill` (4486–4508) drains *every* ready completion
   (`try_recv` loop) and *every* ready terminal event into unbounded `VecDeque`s, then pops one
   per iteration. That defeats both the 64-slot terminal queue and the 64-slot completion
   channel, so the inbox grows without bound under key repeat with a slow View or bursty
   completions. A two-way merge needs only the **head** of each source. Hold at most one buffered
   item per source and refill only the source that was popped.
4. **Completion stamps.** Stamping at `reserve()` (`promql.rs:4542–4546`) is right while capacity
   is available. When the channel is full the stamp becomes the time capacity appeared, which can
   order it after later terminal events. That is acceptable only if documented. Alternatively,
   stamp at task completion and treat the channel as a transport.
5. **Cancel safety**: `next_event_stamped` must stay cancel-safe inside `select!`, and
   `try_next_event_stamped` must not await. Use it in place of the biased `select!` with
   `ready()` at 4497–4507. The N2 disposition restore belongs on these dequeue paths too.
6. **Ties and end of input**: break ties deterministically, events first as in `pop_earliest`,
   and document it. Order `None` (input stopped) after everything already stamped, as it is now.
7. **SQL** (`sql.rs:173–186`) currently uses an unbiased `tokio::select!`, which randomly
   reorders input against completions and against `join_next`. It must use the same merge.
   `join_next` should become housekeeping only, never an ordering source.
8. **No staleness suppression added**: this is ordering only. Window run guards stay as they
   are, and PromQL results and SQL schema keep no guard.

## Unresolved contract objections

1. Panics in background tasks (N1) need an explicit contract: Go ends the program. Rust must not
   continue after a restore.
2. Is SIGQUIT parity process-wide or ledgered? It must not be session-only.
3. Signal and resize precedence over queued keys (N7, criterion 2): accept the residual
   difference or model the separate senders.
4. Requested proof that the stamped merge reproduces Go's one-pending-message reader (criterion 1).
