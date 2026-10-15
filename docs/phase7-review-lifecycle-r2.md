# Phase 7 lifecycle review, round 2 (independent, read-only)

Scope: the same lifecycle surface as [round 1](phase7-review-lifecycle-r1.md), re-read on disk:
`src/tui/terminal.rs`, `input.rs` (the `Event` contract only), `clipboard.rs`, `src/process.rs`
(the child boundary only), and the driver/task boundaries in `src/tui/{sql,promql,profile,saved,device,login}.rs`.
Base `4a812d5`, Go oracle `07af390`. Go was read directly on disk (`cmd/query.go`,
`pkg/model/promql.go`), together with bubbletea v1.2.2 `tea.go` from the module cache. The
parent's dispositions are in [phase7-review-resolutions.md](phase7-review-resolutions.md).

Method: static reading only. The reviewer ran no builds, tests, timings or PTY runs. The
reviewer's shell was unusable (EDQUOT on the harness temp directory), so every claim below comes
from reading source with file tools. The parent reports 376 passing terminal/unit tests, an
integration log (`integration-unit4.log`, all passing, 11.44 s) and actual-Go PTY controls. That
evidence is **reported, not verified** by this review. The final-candidate Rust PTY evidence is
still pending.

This is a focused lifecycle review. It is **not** phase-wide acceptance. `src/admin.rs` (the
shared panic prefix, still being finished by its owner) was **not** reviewed in this round.

## Verdict

**No medium or high lifecycle defect remains in the code read.** N1–N8 are resolved or
correctly dispositioned in source (table below). This round found three new Low items and a few
Info items. None of them blocks this focused review, but each needs a disposition: fix it or
ledger it. Acceptance still depends on the final-candidate PTY evidence listed at the end.

## Prior findings: dispositions

| ID | Round-1 finding | Round-2 status | Evidence on disk |
|----|-----------------|----------------|------------------|
| N1 | A panic hook restore left a zombie session | **Resolved** | `Restore.queue: Weak<Queue>`. The hook upgrades it, calls `restore_now(true)` and then `Queue::kill`. `take()` delivers `Event::Panicked` once and then `Closed`. Every driver handles `Panicked`: `sql.rs` `DriveEnd::Panicked`, `promql.rs` `command_panic` (also via `Tasks::reap` on a `JoinError`), `profile.rs` `End::Killed`, `saved.rs`, and `device.rs` `killed`. Each prints `Caught panic:` and returns `program was killed: context canceled` (device wraps it with its prefix). Update/View faults keep their separate exit-0 path. |
| N1-P2 | PromQL query panic must stay local (Go `promql.go:2114-2119`) | **Resolved** | `FetchResult` runs `service.query` inside `terminal::recovered` in an inner `JoinSet` owned by the outer task. The thread-local depth is set per poll, so the hook neither restores, nor kills, nor chains the stderr hook. A `JoinError::is_panic` becomes `Err("panic: …")`, which matches Go's `fmt.Sprintf("panic: %v", r)`. Aborting the outer task drops the inner set. |
| N2 | INT/TERM restored on read, not on acceptance | **Resolved** | The reader keeps `quit_sent` suppression (`spawn_signals`). `observe(Event::Quit)` calls `restore_interrupts()` only when the Quit is taken, on every take path (`next_event_stamped`, `try_next_event_stamped`, `next_event_blocking`, test `next_event_timeout`). SQL and PromQL peek and take only the winner. The F2 hang never takes the pending Quit (`sql.rs` `fault_end`, `promql.rs`/`saved.rs` `editor_fault` hold the session in a `pending()` await), so the handlers stay installed and later INT/TERM are swallowed. That matches Go's blocked `p.msgs <- QuitMsg{}` (`tea.go:268`). |
| N3 | Read errors took the kill path | **Resolved** | `profile.rs` (`End::InputError` → `close_with_final_view`, with a View-panic fallback to `Ok(Panicked)`), `saved.rs` (`finish` then `Err`) and `device.rs` (`close(&view)` then `finish`). SQL and PromQL were already correct. This matches `tea.go:595-605`, where `killed` depends only on `ctx.Err()`. |
| N4 | Handler could write to a reused fd | **Resolved** | `signal_pipe()` is process-lifetime: a `OnceLock` holds both ends via `ManuallyDrop`, and they are never closed. `prepare_signals` drains stale bytes before installing handlers. Per-session pipes hold only the wake pair. |
| N5 | Non-atomic CLOEXEC | **Resolved (Linux/BSD)** | `pipe2(O_CLOEXEC [| O_NONBLOCK])` on linux, android, freebsd, netbsd, openbsd and dragonfly. On macOS and others, `pipe` + `fcntl` remains a documented fallback window. |
| N6 | Signals installed after raw mode | **Resolved** | `open_with`: `open_input` → `prepare_signals` → `make_raw` → modes → writer → initial resize → reader → signal thread. That follows the Go order `openInputTTY` → `handleSignals` → `initTerminal` (`tea.go:479-534`). Rollback is unchanged because every step is recorded in `Restore`. |
| N7 | Signals and resize queued behind keys | **Resolved** | `Lanes` keeps separate one-slot lanes: `input`, `initial_resize`, `resize` (with `resize_again`, which models Go's one-slot SIGWINCH channel and re-queries the size on acceptance), and `quit`. `earliest()` picks by offer stamp, with ties broken by lane order. The reader blocks in `push_input` until its one pending message is taken. That is Go's unbuffered handoff. |
| N8 | Inconsistent panic diagnostics | **Resolved** | The forced backtrace in `saved.rs` is gone (comment at `recovered_panic`). No workflow prints a Rust stack. Go's `debug.PrintStack` stays a ledgered runtime difference. |

### Round-1 criteria for the stamped merge

- **C1 (burst order `key1, completion, key2, key3`): withdrawn. I accept the parent's
  objection.** In Go, once key1 is accepted the reader goroutine begins sending key2 at once
  (`readAnsiInputs` sends each message before decoding the next). A completion that finishes
  while key1's Update runs therefore queues *behind* key2 in the channel's FIFO `sendq`. The
  Rust stamp is the time the message is offered, after the lane frees (`push_input`). Stamps
  that order by first offer reproduce that FIFO. What must hold, and does hold on disk: exactly
  one pending reader message; Quit, initial resize and resize as independent senders; a runner
  that accepts only the winner; and no new staleness guards.
- **C2 (independent signal/resize stamps): met** (N7).
- **C3 (bounded inboxes): met.** SQL holds one completion head (`done`), and PromQL holds one
  (`Inbox::done`). The terminal is only peeked. The completion channels are bounded (16 for SQL,
  64 for PromQL), and the terminal lanes hold one item each. Neither runner drains a source into
  a local queue.
- **C4 (completion stamp under a full channel): accepted residual.** Stamping at `reserve()` puts
  a completion that waited for capacity after terminal events offered meanwhile. Go's `sendq`
  would order it by when its send began. Stamping before `reserve` would let a stamped but
  invisible completion lose to a later terminal head. So neither choice is exact once the
  channel is full, and the reserve stamp keeps head comparison self-consistent. The SQL
  comment documents this. It needs a ledger line, because the condition takes 16 or 64
  completions outstanding behind a slow driver.
- **C5 (cancel safety): met.** `next_event_stamped` and `event_ready` create `notified()`
  before checking. `Notify::notify_one` stores a permit, and Tokio forwards a notification from
  a dropped `Notified`. `try_next_event_stamped` and `peek_event_stamp` never await.
- **C6 (ties and end of input): met.** Terminal ties win in both runners: SQL uses
  `pressed <= finished`, and PromQL uses strict `done_at < event_at`. `Closed` needs zero
  producers and empty lanes, so it is ordered after everything stamped.
- **C7 (SQL unbiased `select!`): met.** The `select!` is now a readiness wait only. Ordering
  comes from `next_step`. `join_next` is housekeeping, and panics reach the runner through the
  hook, not through join results.
- **C8 (no added staleness suppression): met** in the drivers read.

The "comparing heads is exact" claim (`promql.rs` `Inbox` doc) holds **on the current-thread
runtimes used by SQL and PromQL**. A completion is stamped and sent within one task poll on the
driver thread, and a terminal message is stamped and published under the lanes lock. The claim
would not survive a move to a multi-thread runtime. Keep that dependency written down.
Universal Go-scheduler equivalence is **not** claimed. Go's random `select` between `ctx.Done`,
`p.errs` and `p.msgs` (`tea.go:343-351`) has no model, and none is needed.

### Scoped SIGQUIT disposition (contract objection 2 from round 1)

**I accept the scoped disposition, with one boundary correction (R2-2 below).** On disk,
`prepare_signals` installs `on_quit` (async-signal-safe `write("SIGQUIT: quit\n")` + `_exit(2)`,
with no restore and no unwinding) for the life of a phase-7 session. Outside sessions, the
pre-existing Rust behavior (death by signal 3) remains and is ledgered as a previous-phase
runtime difference. Round 1 objected to a session-only divergence. That objection is resolved by
the explicit ledger entry: the process-wide fix would change phases 1–6, and this phase does not
authorize it. I do not require a broad prerequisite.

What the ledger must state precisely:

1. Only the first line of Go's output is reproduced. The PC, register and goroutine dump is not.
2. The covered window. As written, the window opens at `prepare_signals` and closes at
   teardown's `restore_all`. See R2-2: it currently closes **before** the final flush and
   termios restore.
3. `_exit(2)` discards anything still buffered in Rust-side stdout writers. In the flows read,
   nothing is written to stdout before or during a session, so nothing observable is lost. The
   login → device flow, which runs two sessions in one process, should be confirmed by the owner
   (see "Remaining tests").

## New findings (round 2)

### R2-1 — Low: `Panicked` is not actually "ahead of everything else" for merging runners

- Code: `terminal.rs` `Queue::peek`. When `killed` is set, it returns `Ok(Some(Instant::now()))`.
  `sql.rs` `next_step` and `promql.rs` `next_input` then compare that stamp with the completion
  head. Every completion stamped before the peek is older than "now", so it **wins**.
- Trigger: a task completes and its message is the held `done` head, or it sits in the channel.
  Then a different task panics outside a recovered scope. The hook restores the terminal and
  kills the queue. The runner then processes the earlier completion: it runs Update, may spawn
  new effects (network or clipboard children), and draws a frame the writer gate suppresses.
  Only after that does it take `Panicked`. With up to 16 or 64 completions queued, this repeats
  for each one stamped before the peek. If such an Update or View reaches a modeled Go fault,
  the run ends on the **editor-fault path** (exit 0, or a hang with the terminal already
  restored) instead of the command-panic path (exit 1).
- Go comparison: Go is nondeterministic here. Its `eventLoop` `select` picks randomly between
  `ctx.Done` and a pending `msgs` send. So this is not a parity defect. It is a contradiction
  of the code's own contract: the `terminal.rs` module doc says "delivers `Event::Panicked`
  ahead of everything else", and the `input.rs` `Event::Panicked` doc says "ahead of anything
  else queued". It is also a source of avoidable post-restore work.
- Fix (small): have `peek()` report the kill as urgent. Either make it a `Peek::Urgent` that
  runners take unconditionally, or return a stamp that cannot lose (for example the session's
  open instant). Otherwise, weaken both doc comments and ledger the nondeterminism. Add a unit
  test: a completion is held as `done`, `Queue::kill` runs, and the next step yields `Panicked`.

### R2-2 — Low: the SIGQUIT handler is removed before the final flush and termios restore

- Code: `terminal.rs` `teardown` calls `restore.signals.restore_all()` (INT, TERM, **QUIT**,
  WINCH) before `writer.stop(..)` and `restore_now(..)`. `restore_now` also starts with
  `restore_all`, then writes the restore bytes, sleeps 10 ms after `EXIT_ALT` and only then
  restores termios. The panic-hook path uses the same `restore_now`.
- Trigger: `kill -QUIT` during the close of any fullscreen session. That includes the 10 ms
  sleep, which happens on every close, and a final flush to slow or stalled output (for example
  a non-terminal stdout pipe whose reader is not draining). Rust then dies by signal 3 (status
  131, possibly a core dump) with raw mode still set. Go's runtime always handles SIGQUIT (exit
  2, `SIGQUIT: quit` prefix, no restore). The terminal is raw in both, so this falls inside the
  "active session" scope the parent adopted.
- Fix: restore INT, TERM and WINCH where they are restored now (Go's `signal.Stop` happens in
  `handlers.shutdown`, before the renderer stops). Restore SIGQUIT **last**, after
  `restore_raw` in `restore_now`. Then the covered window equals the acquired-terminal window.
  The panic hook path gets the same change for free.

### R2-3 — Low (unreachable today): `saved.rs` read-error path can report the read error after a recovered final-View panic

- Code: `saved.rs` `Event::InputError` → `finish(..).await?; return Err(session_error(error))`.
  When the final `state.view()` faults, `finish` → `editor_fault` → `recovered_panic` returns
  `Ok(None)` after printing `Caught panic:`. The code then **also** returns the read error.
- Go comparison: when Run's final `renderer.write(model.View())` panics (`tea.go:601`), the
  deferred `recoverFromPanic` takes over and Run returns zero values (nil error).
  `profile.rs` handles this correctly (`End::InputError` → `match close_with_final_view { Quit
  => Err, panicked => Ok(panicked) }`).
- Reachability: today it cannot happen. The loop renders the same state at the top of every
  iteration and returns on a fault, and a read error changes no state. Align it with
  `profile.rs` anyway, so a future time-dependent View cannot produce exit 1 together with a
  `Caught panic` report.

### Info items (no action required beyond a ledger line where noted)

- **I-1, reader, writer and signal thread panics.** On disk these become `Panicked` and take the
  command-panic outcome (exit 1, terminal restored). Teardown is safe: `FinishOnDrop` keeps the
  producer count right, joins ignore `Err`, and the writer gate suppresses output after
  `ACTIVE` is cleared. The Go analogue is different: `readLoop` and the renderer's `listen`
  goroutine have no `recover`, so Go would crash the whole process (exit 2, no restore). These
  are native bugs with no Go-reachable trigger, so the Rust choice is acceptable. Ledger it as a
  native design choice, not as parity.
- **I-2, panic while output is stalled.** The hook takes `ACTIVE`, restores INT/TERM/QUIT, and
  then blocks on `OUTPUT` until the writer's stalled `write` returns. On a current-thread runtime
  the panicking task runs on the driver thread, so the driver blocks too. That is equivalent to
  Go: `recoverFromPanic` → `shutdown(true)` → `renderer.kill()` waits on the renderer mutex, and
  Run's own `shutdown(true)` does the same. No deadlock exists: the writer never takes `OUTPUT`
  while holding `ACTIVE` across a write (the `if lock(&ACTIVE).is_some()` temporary is dropped
  before the block), and the hook never touches `lanes`. While stalled, INT and TERM have default
  dispositions, as in Go after `signal.Stop`. SIGQUIT also has its default disposition, which is
  covered by R2-2.
- **I-3, background panic during an F2 hang.** The hook restores the terminal and queues
  `Panicked`, but the hung runner never takes it, so no `Caught panic:` is printed and the
  process lingers. Go is the same when a SIGTERM was sent first: `handlers.shutdown` waits
  forever on the signal goroutine blocked on `p.msgs`. Without a prior SIGTERM, Go restores,
  prints `Caught panic:` from the goroutine and lingers. This needs a native task panic during a
  hang, which is hypothetical. Ledger it under L-EDITOR-2.
- **I-4, the reader is not cancelled by the hook.** After the hook restores cooked mode, the
  input thread can still read one cooked line before the runner tears down. Go's kill path
  cancels the reader first. The window is normally microseconds. It is unbounded only together
  with I-3.
- **I-5, `signal_pipe()` caches a creation error for the life of the process** (`OnceLock<Result<..>>`).
  An `EMFILE` on the first session makes every later session in the same process fail. Only
  multi-session flows are affected. Acceptable, but say so.
- **I-6, stale doc wording.** The `promql.rs` `Inbox` doc says "terminal read time". The stamp is
  now the offer or handoff time (`terminal.rs` `Stamped`). Update it.
- **I-7, Windows compile surface (not validated; no Windows claim made).** The read found no
  obvious unix-only symbol leaking into `#[cfg(windows)] mod sys`. `Output` there is a unit
  struct and `Endpoints::terminal` is `cfg(all(test, unix))`. `crossterm` and `libc` are
  target-gated in `Cargo.toml`. `process.rs` consumes `command` on non-unix, and `clipboard.rs`
  has a Windows `select`. Remaining risks: `input::from_crossterm` was not inspected, the
  `terminal.rs` test module's cfg gating was not inspected, and no `cargo check --target *-windows-*`
  has been run.

## Reviewed without objection (static, round 2)

- **Lock discipline.** The locks are `ACTIVE`, `OUTPUT`, `lanes`, `panic_message`, the writer
  `slot`, `Notify` and the condvars. The only nesting is `OUTPUT → ACTIVE (brief)` in the
  writer. `restore_now` takes `ACTIVE` and drops it before `OUTPUT`. `take` nests
  `lanes → panic_message` only after observing `killed`, and the hook writes `panic_message` with
  `try_lock` before setting `killed`, so the message is never lost to the consumer's lock. No
  call that can panic runs under `ACTIVE` or `OUTPUT` on the same thread, so the hook cannot
  self-deadlock. Poisoning is ignored deliberately (`lock()`).
- **Boundedness.** Lanes hold one item each. Completion channels hold 16 (SQL) or 64 (PromQL),
  with one held head each. Spinner and tick tasks re-arm once per handled tick. PromQL `cancels`
  is removed on completion or cancel. The signal pipe is nonblocking and a full pipe drops
  bytes. `KILL_POLL` (100 ms) bounds the lost-wakeup race only for the blocking `pop_until`; the
  async paths use `Notify` permits.
- **Producer and EOF counters.** `spawn_producer` increments before spawn. A failed spawn drops
  the closure and its `FinishOnDrop`, so the count balances. EOF (`n == 0`) ends input silently
  while the signal thread keeps the queue open, which matches Go `io.EOF`. Read errors are sent
  once on the input lane after every earlier message is accepted, which matches Go: `readLoop`
  sends on `p.errs` only after `readAnsiInputs` has handed everything off. `Closed` requires zero
  producers and empty lanes.
- **Shutdown.** `teardown` stops the queue (unblocking `push_input`), wakes both poll loops via
  the shared level-triggered wake pipe, joins them, restores signals, stops the writer (with a
  final frame on quit and none on kill) and restores the terminal. It is idempotent via
  `torn_down` and `ACTIVE.take()`. Drop during unwinding after a hook restore writes nothing.
  No join can block indefinitely except on a stalled stdout, which is Go-equivalent.
- **Driver task ownership.** SQL `JoinSet` + `shutdown_background()` after close. PromQL
  `Tasks` dropped after close, or before kill on the panic paths. Profile and saved abort and
  join clipboard reads before close or kill. In the device flow, `PollTask` is aborted on Ctrl+C,
  aborted and joined by `finish()`, and aborted on `Drop`, so no poll outlives the screen.
  `Wake::Polled(Err(panic))` takes the kill path, and an already-queued `Panicked` event wins
  first because the event is polled first.
- **Clipboard and browser children.** Clipboard: stdin `/dev/null`, captured stdout and stderr,
  `kill_on_drop`, `restore_child_limits`. Browser: null stdio, never waited. Both are spawned
  only after acquisition. Session fds are CLOEXEC on Linux and the BSDs.
- **Phase boundary.** With no session active, the hook behaves as before: no queue, nothing to
  restore, then the previous hook runs. `recovered` suppresses the chained report only while its
  own future is being polled on that thread.

## Remaining tests (the parent runs these; the reviewer ran none)

1. Unit test for R2-1, whichever fix is chosen: a held completion plus `kill` must yield `Panicked`
   next (or the documented alternative).
2. Unit or PTY test for R2-2: SIGQUIT during the close of a fullscreen session (insert a
   test-only pause after `restore_all`, or use the 10 ms alt-exit sleep) must give exit 2,
   the `SIGQUIT: quit` prefix and raw mode left set.
3. Final-candidate PTY, Rust against the existing Go controls: a hung empty SQL/PromQL Ctrl+Left
   survives two SIGTERMs, then SIGQUIT gives exit 2 and no restore. A normal fullscreen SIGQUIT
   gives the same result.
4. Background command panic for each workflow (SQL, PromQL, profile, login, saved, device, and
   admin once finished): exit 1, `Caught panic:` on stdout, terminal restored, no frame bytes
   after the restore sequence, and no stdout writes lost to buffering order.
5. PromQL recovered query panic: `panic: …` is shown in the results pane, the session continues,
   nothing is printed to stderr, and the terminal is not restored.
6. Reader, writer and signal thread panic injection (unit): `Panicked` is delivered, teardown
   joins without hanging, and nothing is written after the restore.
7. Panic while the writer is blocked on a non-draining pipe output (unit, non-tty output):
   the hook waits, completes once drained, and nothing deadlocks.
8. Multi-session process (login wizard, then device screen): no stale Quit or resize from the
   first session's pipe bytes, and SIGQUIT in the second session loses no earlier stdout.
9. Status of the known hang-up test (`hangup_ends_input_silently_like_go_eof`) in the final run.
10. `cargo check --target x86_64-pc-windows-msvc` (or gnu) with and without `--tests`, as a
   compile-only signal. It makes no Windows behavior claim.

## Objections still open

1. **R2-1 contract.** Either make the doc claim ("`Panicked` ahead of everything else") true for
   merging runners, or withdraw the claim and ledger Go's nondeterminism.
2. **R2-2 boundary.** The scoped SIGQUIT disposition is accepted only if its window covers the
   whole acquired-terminal interval (restore SIGQUIT after termios), or if the ledger names the
   final-flush/alt-sleep gap explicitly.
3. **Ledger lines needed:** C4 (completion stamp under a full channel), I-1 (native thread-panic
   outcome), I-3 (panic during hang) and I-5 (cached signal-pipe error). Also record that exact
   head comparison depends on the current-thread runtime.
