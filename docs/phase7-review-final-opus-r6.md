# Phase 7 final production review — independent Opus 5.5, round 6 (supplemental)

- Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`, 2026-10-14.
  Base `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`, Go pin `07af390ab09bda8ac9e427ffc6310077bace0125`.
- This is a fresh, independent supplemental round, not a continuation of the implementation. Its
  job is to close what R5 left out:
  - the nine earlier production reports and the resolution log;
  - the current docs;
  - the SQL/PromQL `TextInput` call sites;
  - device, admin and terminal boundaries;
  - an independent assessment of R5's O5-1;
  - the remaining evidence, including the newly supplied S-1 evidence.
- The R5 brief (`final-production-r5-assignment.json`) still applies, and I read it in full. Its
  original phase brief is carried forward unchanged: native owned reducers, effects and RAII,
  per-flow quirks, no blanket stale suppression, no new CLI or capture, and no prerequisite
  refactor.
- Excluded, and not reviewed: the collector, profiler and shared-deadline proposals (owned and
  reviewed separately), plus bench, helper and campaign tooling. I raise no blocker against
  them.

## Method and limits

- **Source-only, by instruction.** I made no shell attempt (known EDQUOT) and used no symlink
  workaround. I used only file read and write tools.
  - I ran no builds, tests, CLI/PTY sessions, profilers, timings, network requests or locks.
  - I computed no hashes. Every hash and count below is **[supplied]**.
- I wrote only this report and
  `/home/ajs/.cache/pb-phase7/review-production-opus-r6/review-record.json`.
- Labels:
  - **[source]** is my reading of current files plus causal reasoning; no execution is
    implied.
  - **[supplied]** is owner, parent or user records that I read but did not reproduce.
  - **[recollection]** is a stdlib or runtime fact I did not re-read this round. It is never the
    sole basis for a disposition.
  - **[unknown]** is not established.

### Read this round (exact)

**Reports and docs, read in full:**

- `final-production-r5-assignment.json`;
- `phase7-review-final-opus-r5.md` and `-r4.md`;
- the nine earlier reports: `-final-r1`, `-lifecycle-r1`, `-lifecycle-r2`, `-service-r1`,
  `-service-r2`, `-byte-interim-sol-r1`, `-sql-identity-interim-sol-r2`,
  `-final-contract-sol-r3` and `-final-opus-r3`;
- `phase7-review-resolutions.md`;
- `rust-phase7.md`;
- `phase7-{auth,editor,admin-saved,shared-contracts}.md`.

**Evidence, read in full:** `credential-blink-left-results.md`.

**Evidence, read in part:** `credential-blink-left-evidence.json:1-220`, `:440-806`
(identities, raw checks, strict differential, original investigation, limits).

**Rust source, read in part:**

- `editor.rs:300-720`: `BlinkCursor`, the `TextInput` blink and polling API, and
  `update_at`/`insert_paste_at`.
- `sql.rs`:
  - `:1-140`: the entry point;
  - `:140-439`: the driver, `next_step`, `recover_window` and dispatch;
  - `:439-598`: the `Msg` and `Effect` enums;
  - `:2440-2620`: the constructor, `focus_selected` and the identity helpers;
  - `:2740-3030`: the window guard and table navigation.
- `promql.rs:3330-3610` (`update`, `complete`, `key`) and `:5620-6091` (entry point, run loop,
  `Inbox`/`next_input`, `Outbox`, `Tasks::spawn`).
- `profile.rs:1-260`: `InlineModel`, `run_inline`, `first`, and the picker preamble.
- `login.rs:160-819`: the wizard reducer, `next_blink`/`blink`, `WizardSession`, `run` and
  `complete`.
- `saved.rs:1-175`: the driver, `next_driver_event`, and the panic and fault paths.
- `device.rs:490-709`: `PollTask`, `killed`, `wait_screen` and `profile_from_tokens`.
- `tui/admin.rs:1-110`: both blocking selectors.
- `terminal.rs:1-200` (module contract, lanes, `push_input`) and `:860-940` (`observe`,
  stamped, try and peek, `event_ready`, `next_event`).

**Go source, read:**

- `bubbles@v0.20.0/cursor/cursor.go`, in full;
- `bubbletea@v1.2.2/tea.go:170-239` (`NewProgram`, unbuffered `msgs`), `:240-466`
  (`handleSignals`, `handleCommands`, `eventLoop`) and `:468-479`;
- `bubbletea@v1.2.2/tty.go`, in full (`readLoop`, `checkResize`).

### Not read this round (no complete reread claimed)

- **Rust:**
  - `input.rs` (relied on R4's `:1-420` reading and lifecycle R1/R2);
  - `clipboard.rs`, `style.rs`, `json.rs` and `saved_fuzzy_unicode.rs`;
  - `sql_service.rs` and `promql_service.rs`;
  - the rest of `sql.rs`, `promql.rs`, `device.rs`, `terminal.rs` and `editor.rs`;
  - `login.rs:1-160`, `profile.rs:260-end` and `saved.rs:175-end`;
  - all non-TUI modules (`api.rs`, `lib.rs`, `process.rs`, `sql/mod.rs`, `saved_queries.rs`,
    `admin.rs`);
  - all tests.
- **Go:**
  - `pkg/model/{login,credential,promql,query,savedQueries}.go`;
  - bubbletea `key.go`/`readInputs`, `standard_renderer.go`, `textinput.go`;
  - Go `context`/`time`.
- **Evidence:**
  - `credential-blink-left-sealed.json`, `credential-blink-left-tests.py`, the new runner
    source, and the raw captures under `/home/ajs/.cache/...`;
  - `credential-blink-left-evidence.json:221-439`;
  - `release5-{build-manifest,focused-checkpoint}.json`, `blink-source-checks.json` and
    `release5-focused/report.json`;
  - the evidence cited only by docs (decoder, OAuth, device, styled and retained records).
- **Consequence:** for modules I did not read, I rely on the earlier source dispositions
  (R1–R5) and on the supplied statement that only `src/tui/{editor,login,profile,saved}.rs`
  changed since release4. I did not verify that statement.

## Verdict

**Bounded source verdict: no blocking finding.**

- **O5-1** is confirmed at **Low** with corrected mechanics. R5's statement that "Go's FIFO
  would deliver the BlinkMsg first" is too strong, and its consequence list is incomplete. A
  bounded, documented scheduler limitation is justified. No code change is required.
- **S-1** is **closed within the actual evidence bounds**. The supplied raw proof shows that the
  original Go cancel-ready checkpoint was stale (taken before the Left redraw), and a fresh
  strict Go/release5 pair is clean. The original cancel-ready difference was not a reducer or
  timer defect.
- **New Low finding, evidence wording only (E6-1).** The S-1 evidence contradicts itself about
  raw saved-config byte equality. Reconcile it before the record is cited as "raw bytes equal".
- **SQL/PromQL `TextInput` call sites:** verified in source. Neither has any blink delivery
  route, so the pending-blink correction cannot change their behavior.
- No earlier acceptance is invalidated by the four changed files, as far as the surfaces I read
  show.
- This is **not** whole-phase acceptance. Full styled 190, device 17, the genuine effect
  deadlines and final retained 60/120 s remain pending. Performance, non-Linux platforms and
  packaging are unknown.

## Priority 1 — earlier reports, docs and the R5 omissions

### Earlier production findings: still hold where re-read, none invalidated

| Origin | Item | Status this round |
|---|---|---|
| Final R1 | F1 decoder reuse, F3 SimpleFold | Not re-read. Sol R3 and Opus R3/R4 held them at source, and the supplied maps show no change since. |
| Final R1 | F2, window-local recover | **Holds [source].** `sql.rs:368-384`: an inner `JoinSet` under `terminal::recovered`, where a panic becomes the same run/window with `"query failed"`. Schema and datasets keep the kill path (`:407-418`). |
| Final R1 | F4, hang-up test | Not re-read (test code). |
| Final R1 | U1/U2 URL limits | Explicitly ledgered as generic, pre-existing limitations (`phase7-shared-contracts.md:45-55`). |
| Lifecycle R1/R2 | N2 | **Holds [source].** Quit dispositions are restored on acceptance in `observe` (`terminal.rs:872-875`). |
| Lifecycle R1/R2 | N7 lanes | **Holds [source].** One-slot input, initial-resize, resize and quit lanes; earliest stamp wins, with ties in lane order (`:115-143`). |
| Lifecycle R1/R2 | N1 Panicked delivery, R2-1 | **Holds [source].** The hook-to-Panicked contract is weakened to state the peek-time stamp and Go's `select` freedom, as the disposition required (`:23-28`). |
| Lifecycle R1/R2 | I-a read-ahead comment | **Holds [source]** (`:10-14`, `:117-118`). |
| Lifecycle R1/R2 | N3 read errors take the graceful path | **Holds [source]** in profile (`profile.rs:154-157`), device (`device.rs:642-648`), saved (`saved.rs:170-175`, the R2-3 final-View check) and admin (`tui/admin.rs:32-35`, `:62-65`). |
| Lifecycle R1/R2 | N4–N6, R2-2 SIGQUIT window, N8 | Not re-read this round. R5 read `terminal.rs:1-1672` and reported them consistent. |
| Service R1/R2 | S1–S6, R2-1, R2-2 | Not re-read. The resolution log records actual-Go confirmation of R2-2; Sol R3 and Opus R3 rechecked S1–S6. |
| Byte Sol R1 / identity Sol R2 | Raw identity | **Holds [source]:** `dataset_identity_bytes`/`dataset_bytes` at `sql.rs:2606-2615`. The cited lines have shifted by −2 since R3. |
| Sol/Opus R3 | R3-F1 clock | **Holds [source] at dispatch:** `Effect::FetchSchema(dataset)` passes only bytes (`sql.rs:407-411`). The service clock site was not re-read (R4 verified it). |
| Sol/Opus R3 | O3-1, O3-2 | R5 read `profile.rs`/`saved.rs` in full; not re-read here. |
| Sol/Opus R3 | I-c, input-first fairness | **Still [unknown].** The source precondition is reconfirmed in `profile.rs:107`, `saved.rs:37-52`, `device.rs:612-632` (input polled first, no `yield_now`). It is related to O5-1 below but is a different question. |
| R4 | O4-1 | R5 resolved it at source, and 16 saved controls pass **[supplied]**. Go's missing panic stdout remains **unwaived**. |

**Did a source change invalidate an earlier acceptance? No, in the surfaces read.** The four
changed files touch blink lifetime and routing only.

- `BlinkCursor::blur` now keeps the timer (`editor.rs:382-385`).
- The new API is `pending_blink`, `take_pending_blink`, `deliver_blink`, `start_blink_at`,
  `update_at` and `insert_paste_at`.
- Every polling reader is gated on focus (`blink_deadline`, `:587-593`). `tick` delivers only
  the cursor's own current-tag timer (`:409-414`). `focus` re-arms (`:374-378`).
- So a polling caller sees the same results as before for the same call sequence.
- The only behavioral shift outside the routed callers is that `update`/`insert_paste` sample
  `Instant::now()` before the edit. That moves a re-arm earlier by microseconds and is
  documented in `phase7-editor.md:199-201`.

### R5 omission: SQL/PromQL `TextInput` call sites, now verified [source]

- **SQL.**
  - `Msg` has only `Key`, `Resize`, `SpinnerTick`, `Window`, `Schema` and `Datasets`
    (`sql.rs:443-451`).
  - `Effect` is `FetchWindow`, `FetchSchema`, `FetchDatasets`, `SpinnerTick`, `ReadClipboard`
    and `Quit` (`:453-467`). `dispatch` matches it exhaustively with no blink arm
    (`:396-434`).
  - The driver waits only on terminal readiness, completions and `join_next` (`:248-255`).
    There is no deadline source.
  - The constructor and `focus_selected` call `focus()` plus `disarm_blink()` on the editor and
    the column filter, with an explicit comment (`:2484-2486`, `:2583-2603`).
  - **Result:** no SQL cursor timer is ever read or delivered, so the blur-keeps-timer change is
    unobservable.
- **PromQL.**
  - `update` has three arms: `Resize`, `Done` and `Key` (`promql.rs:3362-3371`).
  - `complete` drops `Completion::Paste` (`:3401`). `Completion::Tick` is the spinner tag only
    and touches no input (`:3402-3410`).
  - `Tasks::spawn` has no blink effect (`:5986-6089`), and the run loop's only sources are
    terminal events and completions (`:5681-5716`, `:5867-5905`).
  - `open_dataset_overlay` honors `spotlight_filter.focus()` (`:3337`), but the armed timer has
    no reader.
  - **Result:** PromQL inputs never blink either.
- **Bound.**
  - The Rust side is verified for the dispatch and update surfaces above. I did not search every
    `sql.rs`/`promql.rs` line for a stray `tick`/`deliver_blink` call (no grep). However, no
    message variant carries a blink, so a stray call would need a non-blink trigger.
  - That Go likewise never blinks these inputs rests on `phase7-editor.md:56-61` (`query.go:1176`,
    `promql.go:1114`) and on earlier styled gates. I did not re-read the Go models. That parity
    question predates R5 and is unchanged by it.
- **Saved and picker [source].**
  - `saved.rs:36` schedules only `state.next_tick()`, and `:164-166` calls `filter.tick` (the
    focus-gated polling API, with one input).
  - The profile picker has no `next_tick`; R5 confirmed the inert timers.
- **TextArea.** It has no tick API (R5; I did not re-read it).
- **Device and admin [source].**
  - The device screen owns no `TextInput`. Its `wait_screen` is input-first over the poll
    `JoinHandle`, the spinner frame and the browser timer (`device.rs:612-632`), which is the
    I-c precondition.
  - Ownership holds: Ctrl+C triggers `cancel` (`:637-640`); `close` is followed by `finish`
    (abort and join) on every exit (`:644-645`, `:653`, `:677-680`); `Drop` aborts (`:543-547`);
    and a poll panic takes the kill path (`:659-661`).
  - The admin selectors are synchronous `next_event_blocking` loops with no editor and no timer.
    `Panicked` drops with no final view, prints the caught report and returns the killed error
    (`tui/admin.rs:36-40`, `:66-70`). That matches `phase7-admin-saved.md:55`.

### Current-doc reconciliation (Info; no behavior change)

- **`rust-phase7.md:10`** still says credential A's "final cancellation checkpoint differs…
  unresolved" and "Opus production R5… active". Both are superseded: R5 is complete, and S-1 is
  dispositioned below. The resolution log also has no R5 entry yet.
- **`phase7-auth.md:31`** (Structure, inline driver) still says "Input errors drop the session
  (Go kill path, no final render)". That contradicts L-AUTH-14 (`:153`) and the source
  (`profile.rs:154-157`, final view then close).
- **The O5-1 ledger line.** `phase7-editor.md:243-244` and `phase7-auth.md:281-285` should carry
  the corrected statement under Priority 2. R5 pointed at `login-blink-design-r1.md`
  assumption (c), which I did not re-read.

## Priority 2 — O5-1, independent assessment

### What Go actually does [source]

- **BlinkCmd.** `BlinkCmd` (`cursor.go:161-183`) runs inside `Update`. It cancels the previous
  context, creates `context.WithTimeout(..., 530ms)`, and bumps `blinkTag`. The returned
  closure blocks on `<-ctx.Done()`, then returns `BlinkMsg{tag}` if the deadline was exceeded.
- **How the command runs.**
  - `eventLoop` hands the command to `handleCommands` (`tea.go:461-462`), which starts one
    goroutine per command.
  - That goroutine calls `cmd()` and then `p.Send(msg)` (`:318-325`).
  - `p.msgs` is **unbuffered** (`:213`).
- **How keys arrive.** Keys are sent by the `readLoop` goroutine (`tty.go:91-101`) on the same
  channel.
- **Therefore:** the BlinkMsg is offered only after the deadline passes, *and* the runtime wakes
  the waiting goroutine, *and* that goroutine reaches the send. Go orders the two messages by
  which send begins first (sendq FIFO, or direct handoff to a waiting receiver), **not** by the
  deadline.
  - A key whose bytes arrive shortly after the deadline, within the expiry-to-send latency, can
    be received **before** the BlinkMsg in Go too.
  - **[recollection]** The context timer fires through a `time.AfterFunc` callback goroutine,
    which adds one more scheduling hop. The conclusion above does not depend on this.
- **Correction to R5.** "Go's FIFO `p.msgs` would deliver the `BlinkMsg` first" holds only when
  the blink goroutine's send began first. That is typical, but not guaranteed, when the key
  arrives well after the deadline. It is nondeterministic for small gaps.

### What native does [source]

- **Driver.** `run_inline` (`profile.rs:91-115`) builds `first(event, first(timer, paste))`.
  `first` polls the event future before the others (`:204-220`).
  - It uses `session.next_event()`, which discards the lane stamp (`terminal.rs:932-934`), even
    though `push_input` records the offer instant (`:177-192`) and `peek_event_stamp` exists
    (`:914-916`).
- **Saved** is likewise input-first (`saved.rs:37-52`).
- **Result:** whenever a key and a due blink timer are both ready at the same poll, the key is
  handled first, regardless of order.
- **When both can be ready at once:**
  - (i) the key arrives while the loop is mid-iteration (`view`, draw handoff, reducer);
  - (ii) the key arrives after the deadline but before the timer future's wake has been
    processed. **[recollection]** Tokio's timer wheel has 1 ms resolution, so `sleep_until`
    can complete up to about 1 ms late.
- R5 named only (i). Case (ii) applies even to an idle loop.

### Feasible trace (login, self-hosted; tags from `login.rs:254-264` plus `editor.rs:366-407`)

1. **t0.** Enter at ChooseAuth focuses the username: `focus_input` arms tag 1 and disarms it,
   then `initial_blink` arms tag 2.
2. **t0+100 ms.** Typing `u` moves the cursor, giving tag 3, due D = t0+630.
3. **D+δ.** Enter bytes arrive. δ is larger than Go's expiry-to-send latency but within the
   native window, for example 0.3 ms with an idle native loop waiting on a timer that fires up
   to about 1 ms late.

**Go** (blink send first):

- `BlinkMsg{3}` reaches the focused username, and the tag matches. It toggles the caret and
  arms tag 4, due ≈ D+530.
- Then Enter blurs the username (its tag-4 timer stays live) and focuses the password: the
  discarded Focus gives tag 1, and Blink arms tag 2, due E+530.
- The user types two password characters at E+100 and E+200, taking the password to tag 4.
- At ≈ D+530, the username's tag-4 BlinkMsg goes to the active password. The tags are equal, so
  the password caret turns **plain** about 200 ms before its own deadline (E+730).

**Native** (key first):

- Enter is processed while the username's timer `{D, 3}` is already due. `blur` keeps it
  (`editor.rs:382-385`), and the password goes to tag 2.
- On the next wake, `login.blink` takes the username's timer and delivers tag 3 to the password
  (tag 2), which rejects it (`login.rs:397-423`). The username now has no timer.
- No collision ever occurs at D+530. The password caret stays reversed until E+730.

**Effect:**

- One masked-password caret cell differs for about one blink phase. This is the same cell class
  as the release4 login gap.
- Every later departed-timer comparison for that field is also shifted.

**The R5 case.** When the key moves the timer owner's own cursor, the native key re-arm replaces
the due timer. The final caret is the same, and the owner's tag is one lower than Go's.

**The credential form** is the same class with navigation +1. The broadcast in `profile.rs`
(per R5) does not change which focused input accepts.

**Saved** has a single polled input and no cross-input route. Key-first versus blink-first
differs only in an intermediate toggle frame; the final hidden state and the next due instant
are equal. **No persistent effect.**

### Severity and disposition

- **Low.**
  - The native-only outcome needs a key inside a window of roughly ≤ 1–2 ms (timer resolution
    plus at most one loop iteration). Only the portion beyond Go's own expiry-to-send latency
    differs from a Go-feasible outcome.
  - Effects are confined to the caret cell's blink phase and later tag collisions. There is no
    effect on input values, the validity latch, requests, persistence, cancellation, faults or
    lifecycle.
- **A bounded, documented scheduler limitation is justified.**
  - Go itself is nondeterministic in the sub-latency part of the window.
  - Universal equivalence is impossible without modelling Go's scheduler.
- An optional stamp comparison (deliver a due timer first when `due <= peek_event_stamp()`)
  would make the native choice match Go's typical order for most gaps. It still would not be
  exact, because Go orders by send start, not by deadline. **It is not required for this
  verdict.**
- No new stale handling is warranted, and none is proposed.

**Precise statement for root to document** (my wording; no code or protocol claim):

> Go sends a cursor `BlinkMsg` from its command goroutine only after the timer expires and that
> goroutine is scheduled; `readLoop` sends keys independently. On Go's unbuffered `p.msgs`, the
> earlier *send* wins, not the earlier deadline, so a key that arrives just after a blink deadline
> can precede the blink in Go as well. The native login, credential and saved drivers poll input
> before timers and do not compare the input stamp. Whenever a key and a due blink timer are ready
> at the same poll, the key is handled first. That happens when the key arrives during a loop
> iteration, or after the deadline but before the timer wake is processed (timer resolution about
> 1 ms).
>
> The outcomes agree when Go's key send also begins first. They can differ when the key arrives
> later than Go's expiry-to-send latency but within that native window. In login and credential,
> the due timer is then not accepted by its owner before the key:
>
> - a cursor-moving key on the owner leaves the owner's tag one lower;
> - a navigation key delivers the departed due tag to the newly active input at once, instead of
>   a successor timer about 530 ms later.
>
> Either can change a later departed-timer collision, and so one caret cell for up to one blink
> phase. Values, requests, persistence and lifecycle are unaffected. The saved menu (one input)
> shows no persistent difference. No control exercises this window.

## Priority 3 — evidence and coverage

### S-1 (credential A cancel-ready): closed within actual bounds

**[supplied]** `credential-blink-left-results.md` and `-evidence.json`:

- **Original Go run.** Left was dispatched at `…813673589` and cancel-ready was evaluated
  10.747 ms later with `bytes_since_action=0`. The first moved-caret redraw arrived 25.507 ms
  *after* the checkpoint (36.25 ms after Left) and before Ctrl+C. That full frame equals release5's
  moved-cursor frame exactly, and both final frames are equal.
  - A checkpoint with zero new output since the action cannot reflect that action. That is a
    stale observation, independent of any renderer hypothesis, and it settles the original
    difference **[supplied, causal]**.
  - It also agrees with R5's source reading that `character_backward` plus `moved` mirrors
    `textinput.go`. R5's "frame-ticker" mechanism remains a recollection that is no longer
    needed.
- **Runner change.** Only the owned runner changed. Cancel-ready now requires:
  - new output;
  - the reversed 14th bullet followed by a plain blank;
  - a post-dispatch, exact raw-frame witness.

  A failed guard yields `unknown`, not a pass. Readiness inspects cells without altering them,
  the strict comparison is unchanged, and no normalization is applied **[supplied]**. A
  state-based readiness predicate is acceptable here because both binaries must reach it and
  are then compared strictly. It cannot hide a candidate that never moves the caret.
- **The fresh pair** is one pinned Go and one release5 `9ad4f4fc…` session, with no retries.
  - Cancel-ready was taken 19.20 ms and 0.42 ms after Left; each saw 161 new bytes and passed
    all five causal guards.
  - The checkpoint frames are identical (`52b0347d…`).
  - All nine checkpoints, the final frame, the requests, termios, stderr, the semantic
    config-after and the cleanup fields are strict-equal **[supplied]**.

**Disposition:** the original A cancel-ready difference is closed as an observer-readiness
artifact, not a reducer or timer defect. The bounds are:

- one pair (n = 1 per binary);
- no tag instrumentation;
- B, C and D were not re-run under the new runner, and their old cancel-ready checkpoints could
  in principle have compared two pre-Left frames. Their strict final frames limit that gap;
- root's independent artifact verification is still in progress **[supplied by user]**;
- no full-UI claim.

### E6-1 — Low (evidence wording): conflicting raw saved-config byte claim in the S-1 record

`credential-blink-left-evidence.json:779` records `"raw_saved_config_bytes_equal": false`.

- The raw captures differ: `go-A/A/config-after.toml` is 91 bytes, `b444cfb7…`, and
  `release5-A/A/config-after.toml` is 80 bytes, `ecb69f15…` (`:634-664`).
- Yet `limits[2]` (`:803`) says "raw saved-config bytes compare equal", and
  `credential-blink-left-results.md:56` lists "raw saved-config bytes" among the agreeing
  fields.
- The semantic `config_after` field is equal (`:763`).
- The S-1 cell disposition does not depend on this, but the record contradicts itself.

**Needed:**

- say whether the raw difference is the pre-existing Go-versus-Rust config serialization that
  earlier phases accepted, or a per-run home or path substitution;
- correct the wording;
- do not normalize anything.

I did not read the TOML bytes themselves.

### Supplied status (not re-verified)

- **Release5:** normal `9ad4f4fc…def21`; source hashes `43dd1ea2…` (editor), `8c5b2198…`
  (login), `ebde62e7…` (profile), `d25a3358…` (saved).
- **Checks:** fmt and clippy pass; 441 lib and 443 diagnostic tests pass, plus 1 ignored.
- **Focused controls:** login 2/2, saved 16/16, credential B/C strict.
- **New this round:** all-targets and OAuth now pass. The genuine effect-deadline suite is still
  running.
- **Pending:** full styled 190, device 17, final retained 60/120.
- **Unwaived:** Go's absent saved-panic stdout nondeterminism.

## Must-run before any acceptance

1. Complete the genuine effect-deadline suite, full styled 190 (including the login corpus
   beyond the password; R5 item 3), device 17, and the final retained 60/120 s tiers, all on
   release5.
2. Root's independent verification of the S-1 artifacts. Then reconcile E6-1's raw-config
   wording.
3. Optionally, re-run the credential B/C/D cancel-ready checkpoints under the new readiness
   runner. This closes the possible pre-Left-on-both-sides gap; no normalization.
4. Document O5-1 with the corrected statement. Synchronize `rust-phase7.md:10`,
   `phase7-auth.md:31` and the resolution log.
5. Carry the remaining items: Go saved-panic stdout (unwaived), I-c fairness [unknown], the
   lifecycle R2 remaining tests (thread-panic injection, stalled-writer panic, multi-session
   SIGQUIT, Windows compile check), and final R1 I4 licensing. I saw no evidence closing them.

## Residual unknowns

- Arbitrary key segmentation (tags count cursor-moving messages).
- Equal-due order and a single delivery per wake.
- The synchronous initial blink.
- Go scheduler permutations, including the O5-1 sub-latency window.
- Whether Go blinks the SQL/PromQL inputs (not re-read).
- Full styled-frame parity and malformed-OSC final cells.
- macOS/BSD/Windows; TLS/HTTP2 and live SQL; arm64.
- Performance.
- Packaging, installers, licensing and rollback.
