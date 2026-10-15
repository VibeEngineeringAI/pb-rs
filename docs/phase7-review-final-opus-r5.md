# Phase 7 final production review — independent Opus 5.5, round 5

- Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`, 2026-10-14.
  Base `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`, Go pin `07af390ab09bda8ac9e427ffc6310077bace0125`.
- This is a fresh delegated review round, not a continuation of the implementation. It covers
  the O4-1 saved fuzzy matcher correction and the login/credential pending-blink correction.
  Those are the only production changes since release4 (`src/tui/{editor,login,profile,saved}.rs`).
- Excluded: the moving collector, bench, profile, helper, campaign tooling and the shared
  harness deadline rules, which are reviewed separately. Performance, platform and release
  gates are separate from this verdict.

## Method and limits

- **No shell.** As instructed (known EDQUOT), I made no shell attempt and used no symlink
  workaround. I used file tools only, so:
  - I ran no builds, tests, CLI/PTY sessions, profilers, timings or network requests;
  - I computed no hashes. Every hash below is **[supplied]**.
- I wrote only this report and the cache record
  `/home/ajs/.cache/pb-phase7/review-production-opus-r5/review-record.json`.
- Evidence labels:
  - **[source]**: my reading of current files plus causal reasoning. No execution is implied.
  - **[supplied]**: owner or parent execution records that I read but did not reproduce.
  - **[unknown]**: not established by adequate evidence.
  - **[recollection]**: an upstream Go fact I did not re-read this round. It is not used as a
    basis for any disposition.

### Files read this round

**Read in full:**

- `docs/phase7-review-final-opus-r4.md`;
- current `src/tui/saved.rs` (961 lines), `src/tui/editor.rs` (2538), `src/tui/login.rs` (1153)
  and `src/tui/profile.rs` (1471).

**Read in part:**

- `src/tui/terminal.rs:1-1672`: lanes and stamps, the renderer, restoration, the panic hook,
  the writer, the session, `teardown`/`Drop`, the Unix reader and signals, and the Windows
  stubs. The test module was not read.

**Go source read:**

- `pkg/model/login/login.go` and `pkg/model/credential/credential.go`, in full;
- `pkg/model/savedQueries.go:60-349`: `Item`, the delegate `Render`, `FilterValue = title`, the
  key intercepts and `fetchFilters`;
- `sahilm/fuzzy@v0.1.1/fuzzy.go`, in full;
- `bubbles@v0.20.0/cursor/cursor.go`, in full;
- `bubbles@v0.20.0/textinput/textinput.go:535-697` (`Update` and `View`).

**Evidence read:**

- `opus-r4-saved-handoff.md`, `saved-matcher-rust-central.json`,
  `credential-blink-results.md`, `blink-applied-parent-check.json`,
  `login-blink-design-r1.md` (full, including the application record),
  `login-blink-process-release4.json:1-150`, and
  `/home/ajs/.cache/pb-phase7/release5-focused/report.json:1-200` (the input hash map and the
  first three check commands).

**Not read this round** (stated so that no complete reread is claimed, contrary to the brief's
reading list):

- **Prior reports:** `phase7-review-{final-r1,lifecycle-r1,lifecycle-r2,service-r1,service-r2,byte-interim-sol-r1,sql-identity-interim-sol-r2,final-contract-sol-r3,final-opus-r3}.md`
  and `phase7-review-resolutions.md`. I rely only on their dispositions as relayed in R4, which
  I read in full.
- **Current docs:** `rust-phase7.md` and `phase7-{auth,editor,admin-saved,shared-contracts}.md`.
- **Evidence:** `opus-r4-saved-applied-freeze.json`, `saved-matcher-central-checkpoint.json`,
  `saved-matcher-rust-oracle.py`, `login-blink-implementation-assignment.json`,
  `credential-blink-{evidence,sealed}.json`, `credential-blink-process.py`,
  `credential-blink-parent-verification.json`, the source-pin JSON, and the remainder of
  `report.json`. The blink-results document refers to a `credential-blink-verification.json`;
  the brief names a `parent-verification.json`. I read neither.
- **Go:** `bubbles list.go`, the rest of `textinput.go`, and `bubbletea tea.go` and its
  renderer.
- **Rust:** `sql.rs`, `promql.rs`, `device.rs`, `admin.rs`, `style.rs`, `input.rs`,
  `clipboard.rs`, `saved_queries.rs`, `saved_fuzzy_unicode.rs` and every non-TUI module.

## Verdict

**Bounded source verdict: no blocking finding.**

- O4-1 is resolved in source. The pending-blink correction is faithful to the Go cursor and
  model source I read.
- One new Low item (O5-1) needs a ledger clarification, not a code change.
- One supplied process difference (credential A, cancel-ready) remains **unresolved** and is
  not dispositioned here (see S-1).
- This is not whole-phase acceptance:
  - the successor full-style, device, all-targets, effects/OAuth/real-deadline and retained
    60/120 gates are pending;
  - performance and platform are unknown.

## O4-1 (saved fuzzy matcher): resolved in source

I compared `saved.rs:540-655` line by line against `fuzzy.go:108-205` **[source]**.

| Go (`FindFromNoSort` inner loop) | Rust | Equal? |
|---|---|---|
| `runes[patternIndex]` evaluated at the top of each byte iteration, so it panics after exhaustion when bytes remain (`:136`) | `needle.get(consumed)` else `Err(MatchFault{consumed, len})` (`:627-635`) | yes. The message is `index out of range [N] with length N` (`:550-558`). |
| `score = 0`, `+10` when `j==0`, camel `IsLower(last)&&IsUpper(c)` with `last=0` at `j==0`, separator when `j!=0` (`:137-146`) | `consider` (`:581-589`): the camel and separator checks run only when `previous` is `Some`, which is exactly `j!=0`. `last=0` can never be lower case, and an embedded NUL as `last` is neither lower nor a separator. | yes |
| The adjacency accumulator advances for **every** candidate when `len(MatchedIndexes)>0 && lastMatch==lastIndex`, whether or not the candidate is selected. The arithmetic wraps as int64. (`:147-154`, `:233-238`) | `last_applied == Some(prior_byte)`, where `last_applied` is the last applied (possibly stale) byte. It is updated with `wrapping_*` inside `consider` before selection. | yes |
| `score > bestScore` from -1. `matchedIndex` is never reset. (`:155-158`, `:127`) | `best_score: -1`, and `retained_byte` is never cleared (`:597-600`) | yes |
| `nextp = runes[pi+1]` or 0, and `nextc` = next rune or 0 (end or NUL). Apply when `equalFold(nextp,nextc) \|\| nextc==0`, gated on `matchedIndex>-1`. (`:160-179`) | `next_pattern`/`next_rune` default to `'\0'` (`:639-643`). `apply` is gated on `retained_byte`. `fold('\0')` is `'\0'` on the ASCII path, so `fold_equal('\0',x)` holds only when `x=='\0'`. | yes |
| The first apply adds `max(idx*-5,-15)`. Every apply adds `bestScore` (-1 when stale), appends `matchedIndex` and resets `bestScore=-1`. (`:180-188`) | `apply` (`:603-615`) uses wrapping arithmetic | yes |
| Final `+ len(MatchedIndexes) - len(bytes)`. Accept only when `len(MatchedIndexes)==len(runes)`. (`:195-199`) | `consumed == needle.len()`, then `total + consumed - title.len()` with wrapping (`:646-651`) | yes |

Notes:

- I hand-checked the reducer test values against `fuzzy.go`:
  - `("a","a\0")` and `("a","a\0b")` fault at `[1]/1`;
  - `("ab","ab\0x")` faults at `[2]/2`;
  - `("ab","a\0x")` gives 8 (stale index at the end, -1);
  - the 43→44→45 pattern is: the 43rd candidate is rejected (stale, -1); the 44th has
    adjacency broken by the stale index, so score 0 is accepted; the 45th wraps and is
    rejected.
- **[supplied]** The actual 106 Go controls match the extracted Rust matcher
  (`saved-matcher-rust-central.json`, compiled with `overflow-checks=yes`).
- The plain `+=` in `consider` is bounded at 50, so overflow checks cannot trip.
- Matched indexes are not needed: the saved delegate (`savedQueries.go:93-142`) renders no
  match highlighting, and `FilterValue` is the title (`:188`). Only the last index matters, for
  adjacency.
- **Fault routing [source]:** `refilter` returns at the first faulting item in list order, as
  Go's iteration panics at the first such item. The panic text does not depend on the item.
  - `Effect::MatchFault` is produced only from `edit_filter` (after a value change) and from
    `paste`. The driver maps it to `command_panic` (`saved.rs:146-148`, `:158-160`).
  - `command_panic` aborts and joins the clipboard tasks, then drops the session.
    `teardown(None)` ends with `Stop::Kill` (erase line plus CR, no final frame), then runs
    `restore_now`. After that it writes `caught_panic_stdout` and returns
    `Err("program was killed: context canceled")`. This is the same path as `Event::Panicked`.
  - The editor fault path (`editor_fault`, `Ok(selection)` no-op) is untouched.
  - An editor fault takes precedence over refiltering in the same message (`:343-348`), which
    matches Go, where an Update panic precedes any command.
- **Reach:** lowercase `a` is intercepted before filtering (`:275`). Uppercase `A` and
  Ctrl+V paste reach the matcher. That matches the supplied correction in the handoff and the
  tests at `:803-832`.
- **Go's missing panic report.** The handoff says Go sometimes omits the `Caught panic` stdout:
  two of four primary crashes, plus later follow-ups. That is a Go race between the
  command-goroutine `recoverFromPanic` print and `Run` returning the killed error (bubbletea
  internals **[recollection]**). Rust prints it every time, which is one of the outcomes Go
  allows. The harness retains the absent-output cases as failures rather than waiving them,
  which is the correct handling.
- **No universal suppression and no new scheduler policy.** Synchronous refiltering is the
  pre-existing ordering assumption (Go's `filterItems` is an async command).

**[supplied]** Release5 replay: all 16 saved controls passed independently
(`report.json` and the parent's update). I did not read the per-case records.

## Pending-blink correction: faithful to bubbles v0.20.0 and the pinned models

**Cursor primitive [source]** (`editor.rs:332-422` against `cursor.go`):

- **`BlinkCmd` maps to `arm`.** Go cancels the shared `*blinkCtx` synchronously when the
  command is *called*, even if its command is later discarded, and then increments
  `blinkTag`. The closure reads the tag through a pointer to a copy that nothing mutates after
  that `Update` returns. So each message carries its creation tag, and each cursor has at most
  one live timer.
  - Rust: `arm` bumps the tag and replaces the single `Option<BlinkTimer>`. The invariant
    "a set timer's tag is the current tag" holds at every writer (`arm`, `disarm_blink`,
    `take_pending_blink`).
- **`Focus` maps to `focus`**, which shows the cursor and arms.
- **`Blur` maps to `blur`**, which hides the cursor and does not cancel the timer.
- **`initialBlinkMsg` maps to `start`**, which arms only when focused.
- **A `BlinkMsg` is accepted iff focused and `tag` equal.** Ids are always 0, so `id` is
  vacuous. Accepting toggles the cursor and arms again.
- **Each textinput `Update` calls `BlinkCmd` at most once.** `textinput.go:633-642` runs
  `Cursor.Update(msg)` first, then the "moved" re-arm. A message that moves the cursor is never
  a `BlinkMsg`.
- Rust `update_at` and `insert_paste_at` return early while blurred (`:640`, `:677`), as
  `textinput.go:553` does. Ctrl+V returns before any blink work, as at `:609-610`.

**Login [source]** (`login.rs` against `login.go`):

- Every focus site discards `Focus()` and returns `textinput.Blink`. `focus_input` plus
  `initial_blink` mirror that at `:186-187`, `:202-207`, `:233-234`, `:239-244`, `:262-263`,
  `:270-271`, `:283-284`, `:305-306`, `:317-327`, `:343-344` and `:349-350`.
- Esc from ProfileName to ChooseCloudAuth returns `Blink` with no active input. Rust's
  `initial_blink` finds no active input and drops it (`:326-327`).
- Blur sites return nil, and Rust arms nothing there.
- Non-key messages go only to the active step's input (`login.go:385-399`). `blink` takes the
  source field's timer and delivers its tag only to `active_input` (`:397-423`).
- Selection steps consume and drop the timer.
- `next_blink` takes the minimum over all five fields, including departed ones. That is right,
  because Go's departed timers stay live goroutines.
- A departed timer can toggle the active field and replace that field's own timer, which is
  Go's cancel-on-call. I verified this against the `BLINK_SPEED` arithmetic in the tests at
  `:996-1151`. Their tag traces agree with my own Go trace for 200 ms and 650 ms dwell,
  multiple departed timers, refocus and paste.

**Credential [source]** (`profile.rs:793-1005` against `credential.go`):

- **Construction.** `New` discards the user's `Focus()` (tag 1), and Init's `Blink` reaches
  `updateInputs`, where only the focused user arms (tag 2). Rust does
  `focus`/`disarm_blink`/`start_blink` (`:818-820`).
- **Navigation.** It honors `Focus()` for the newly focused input (`credential.go:144`), and
  Rust calls `input.focus()` (`:871-873`). With three positions and ±1 steps, the newly focused
  input was always blurred before, so no double-arm question arises.
- **Non-key messages** reach both inputs in index order through `updateInputs`, and a blurred
  `textinput.Update` returns early.
  - Rust's `blink` delivers to both (`:933-935`), and only the focused input can accept.
  - The latch recheck that Go runs after a blink cannot change state. In every reachable flow,
    values change only through non-navigation key or paste messages, which recheck the latch
    themselves.
  - When the button is focused, both inputs reject, and the timers are taken and dropped (the
    D test).

**Other callers [source], bounded:**

- **Saved menu.** `saved.rs` is byte-identical to the prepared O4-1 file (**[supplied]** the
  same `d25a3358…` hash in the handoff and the release5 map). It uses only the polling API.
  - `blink_deadline` is gated on focus, so a blurred filter never schedules a tick.
  - `tick` delivers only the input's own timer, whose tag is always current.
  - Refocusing on `/` replaces the timer.
  - With a single input there is no cross-input route, so the polling contract is unchanged.
- **Profile picker.** It still does `focus` plus `disarm_blink` and has no `next_tick`
  (`profile.rs:308-314`, `:532-540`), so its timers are inert, as before.
- **TextArea** exposes no tick or deadline API (`editor.rs:1180-1206`). Its timer field is
  written but never read for delivery, so the SQL/PromQL editors can receive no blink.
- **SQL/PromQL `TextInput` call sites** were not read this round. Their safety rests on the
  focus-gated polling invariant above and on the design's caller inventory **[supplied]**.

**Already-fired versus replaced timers.** This is the "bounded `Option` loses a queued Go event"
question. A field's timer is replaced only while that field is focused (by `focus`, a move,
an accepted delivery or `start`).

- If the replaced timer was already due, Go's in-flight message carrying the old tag would
  have gone to the same, now re-tagged, field (or to blurred inputs) and been rejected. So
  replacement loses nothing *when the key really preceded the deadline*.
- The remaining divergence comes from input-first selection, not from the `Option`; see O5-1.

## New item

### O5-1 — Low (ledger clarification): input-first selection can order a later key ahead of an already-due blink, which shifts tag accounting in login and credential

**[source]** `run_inline` selects with `first(event, first(timer, paste))` (`profile.rs:91-115`),
and `saved.rs:29-54` likewise prefers input.

Input lane messages carry the instant they were offered (`terminal.rs:177-192`), and stamp
comparison is available (`peek_event_stamp`, `:914-916`, which SQL uses). Blink ticks ignore
it, so a key offered *after* a blink deadline is processed first whenever both are ready at the
same poll.

If that key re-arms the field that owns the due timer (a cursor move, or an accepted delivery),
the due timer is replaced rather than delivered. Go's FIFO `p.msgs` would deliver the
`BlinkMsg` first: it is accepted, toggles, and arms tag+1, and then the key re-arms tag+2.

- The final caret state is the same, but Rust's tag count for that field ends **one lower**
  than Go's.
- In login and credential, tags decide later cross-field acceptance. So the offset can flip a
  later departed-timer collision; it is not merely a skipped intermediate frame.
- The window is one consumer-loop iteration (view plus a non-blocking draw handoff). That is a
  scheduler-class limit, not a contract loss in normal operation.

**Disposition.** `login-blink-design-r1.md` lists assumption (c), "the driver prefers input
events to timers". Extend that ledger line to state the persistent tag-offset consequence for
routed callers.

- A fix (comparing the blink due instant with the head's stamp before taking input) is
  optional, and it is not required for this bounded verdict.
- No controls exercise the window **[unknown]**.

## Supplied release5 evidence: dispositions

All of the following is **[supplied]**: the parent's update and `report.json`. I re-verified
none of it.

- **Release5 normal binary:** `9ad4f4fc…def21`. Cloud `4923a466…` and login `60152f5c…` appear
  in the input map.
- The map binds `editor.rs 43dd1ea2…`, `login.rs 8c5b2198…`, `profile.rs ebde62e7…` and
  `saved.rs d25a3358…`, the same values as the applied freeze in the brief.
- **Root central checks:** fmt and clippy pass; 441 lib tests and 443 diagnostic tests pass,
  plus 1 ignored.
- **Login:** the 200 and 650 ms dwell controls are strictly clean.
- **Saved:** 16 out of 16 pass.
- **Credential:** B and C are strictly clean. In A, the before-old, after-old, before-own and
  re-arm checkpoints are strictly clean.

### S-1 — unresolved: credential A cancel-ready cursor position

The supplied observation:

- only row 1, columns 19/20 differ;
- Go shows the caret before the Left key, and Rust shows it after the Left move;
- the Go checkpoint was taken about 10.75 ms after the Left dispatch.

**Source reading [source]:** Rust Left on the focused password calls `character_backward`, then
`moved`/`arm`, exactly as `textinput.go:586-589` and `:639-642` do. That is not a reducer
divergence.

A plausible observer cause is Go's renderer flushing on its frame ticker **[recollection]**,
while the Rust writer thread renders each view as soon as it is handed off
(`terminal.rs:680-717`). A checkpoint inside one Go frame interval can precede Go's flush.

This is a hypothesis only. Do not disposition it, normalize it or waive it until paired actual
controls with explicit moved-cursor readiness exist, as the parent requires. The difference
stays open on the successor list.

## Older obligations

I did not re-read them this round beyond what is listed above. The R4 table stands as R4 wrote
it. The terminal lifecycle I read here (`terminal.rs:1-1672`) is consistent with R4's I-a
comment and with the command-panic and kill path used by O4-1:

- `teardown` stops the reader and signals, restores controls, then stops the writer (Kill), then
  restores raw mode, with SIGQUIT last;
- the panic hook restores before it delivers.

## Must-run successor gates (pending)

1. Full styled 190, device 17, all-targets, effects with OAuth and real deadlines, and the
   retained 60/120 s tiers, all on release5.
2. A paired actual credential A retry with explicit moved-cursor readiness for S-1, with no
   normalization.
3. A login corpus re-run beyond the password cases: departed URL and other fields now route
   (design R4).
4. If desired, a control that exercises the O5-1 window. Otherwise add the ledger line.

## Residual unknowns

- Arbitrary key segmentation; tags count cursor-moving messages (design R2).
- Equal-due field order and a single delivery per wake.
- The synchronous initial blink, and the credential constructor arming before Init delivery.
- Go's absent panic report frequency.
- Full styled-frame parity.
- Scheduler permutations.
- macOS/BSD/Windows.
- Performance.
- Packaging and rollback.
