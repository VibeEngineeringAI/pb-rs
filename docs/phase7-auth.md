# Phase 7: interactive authentication and profile workflows

Owner: auth/profile lane. Files: `src/tui/login.rs`, `src/tui/device.rs`, `src/tui/profile.rs`,
`src/profile.rs`. Baseline: Go `07af390` (worktree copy; bubbles v0.20.0, bubbletea v1.2.2,
lipgloss v1.0.0, x/ansi v0.4.5, sahilm/fuzzy v0.1.1 read from the module cache).

**Status (2026-10-14): implemented; built and exercised by the parent.** The agent shell was
unavailable (harness tmp EDQUOT), so builds, tests and oracle runs were executed by the parent
(`/home/ajs/.cache/pb-phase7/auth-requests.md`). The code targets terminal-api.md v1 plus the
accepted editor/style APIs.

## Evidence (parent-run, 2026-10-14)

| Evidence | Source | Result |
| --- | --- | --- |
| Picker filter never narrows (finding 1) | Go PTY oracle, `auth-oracle/go-picker.json`, 12 runs with randomized map order | chosen == first drawn item in all runs; never narrowed |
| Empty-picker Enter (L-AUTH-3) | same | Go exit 2, config unchanged, stderr `panic: interface conversion: tea.Model is nil, not defaultprofile.Model` |
| Alt+D faults (L-AUTH-11) | `auth-oracle/{go,rust}-picker-faults.json`, Go and Rust debug build | login exit 0 and no config write; credential form exit 2; picker exit 2; fatal text matches; only goroutine stack traces differ (L-AUTH-10) |
| Device flow, Go oracle | `harness/phase7_device.py`, 14 cases | all pass, including expiry/interval overflow (L-AUTH-12) and 302-redirect header cases |
| Cloud API-key login, Rust candidate | same harness | pass |
| Device authorization screen, Rust candidate | `device-differential-1` | normal cases differ only in the spinner glyph (animation phase, L-AUTH-13; parent normalizes valid phases). `device-sigint` final frame differed (Rust drew the success line); fixed in code (L-AUTH-4), **awaiting re-run**. Ctrl+C stderr: Go `expired or timed out` vs Rust `aborted` is the known L-AUTH-2 race. |

Not yet covered by executed evidence: PTY cell comparisons for the wizard, credential form
and picker views at fixed sizes and both themes; clipboard paste (L-AUTH-5); the Ctrl+C race set
(L-AUTH-2).

## Structure

| Piece | Go source | Rust |
| --- | --- | --- |
| Inline driver | `tea.NewProgram(m).Run()` | `tui::profile::run_inline` + `InlineModel` (keys, blink ticks, view). Uses `TerminalSession` only; no private raw/alt/panic code. Input errors render the final view and close gracefully, then return the read error; quit/`Event::Quit` also close with the final view. Panics take the kill path without a final render. |
| Default/replacement/logout picker | `pkg/model/defaultprofile` + `bubbles/list` | `ProfilePicker` (owned page/cursor/filter state, binding enable flags updated exactly where bubbles calls `updateKeybindings`) |
| Credential form | `pkg/model/credential` + `button` | `CredentialForm` |
| Cooked confirmation | `cmd/logout.go confirmLogout` | `logout_prompt` (phase-4 lipgloss styles via `metrics::output::style`) + `read_confirmation` |
| Login wizard | `pkg/model/login` | `LoginWizard` reducer → `Effect::{None, Quit, CheckName}`; `name_checked` completion |
| Login command | `cmd/login.go` | `tui::login::run` / `profile::login` |
| Device flow | `cmd/cloud.go cloudProfileFromDeviceLogin` | `tui::device::login`, `DeviceScreen`, `poll_tokens`, `CloudAuthClient::post_json` |
| Command policy | `cmd/profile.go`, `cmd/logout.go` | `profile::execute_with(parsed, store, &mut dyn Prompts)`; production `TerminalPrompts` |

`Prompts` is the single seam (real sessions vs scripted tests): `say`, `select_profile`,
`credentials`, `confirm_logout`.

## Per-flow contracts implemented

### `pb profile add NAME URL [USER]` (no `--api-key`, < 4 positionals)
* Fresh form; a third positional username does not seed it.
* `Values()` is **both-or-neither**: returned only when username is non-empty, has no space, and
  password is non-empty *at exit*; otherwise `("", "")`. Esc/Ctrl+C/Submit all quit and the caller
  saves whatever `Values()` returns (so: valid-but-unsubmitted fields are saved; username-only saves
  two empty strings). The `Invalid` latch clears once valid and never re-arms, so Submit can exit
  with empty values after the password is erased.
* Enter on the `[ X ]` button while invalid cycles focus to the username (the form intercepts Enter
  before the button). Tab/Down/Enter advance, Shift+Tab/Up go back, wrapping over 3 stops.
* Program error → `error reading credentials: <err>`, nothing written.
* Save: login policy (`save_login_profile`: overwrite after any read error, set default).

### `pb profile default` (no name)
* Picker over profiles; cancel (q, Esc, Ctrl+C) prints nothing and writes nothing.
* Program error returned raw (Go `return err`).

### `pb profile remove NAME` (removing the default with ≥2 survivors)
* Prints `Select a new default profile:` before the picker; choice prints
  `'<c>' is now set as the default profile` before `Deleted profile NAME`.
* Cancel still removes NAME and writes an empty default.
* Program error → `error selecting new default profile: <err>` **before** any write.

### `pb logout`
* No usable default and >1 profiles (without `--yes`/`-o json`): `Select profile to logout:` +
  picker; cancel → `Logout canceled` (stdout, exit 0). Error → `error selecting profile to logout: …`.
* Confirmation is a cooked line prompt outside any session:
  `Logout from profile '<n>' (<url>)?` / muted explanation / `Continue? [y/N]: ` with SelectedStyle /
  StandardStyleAlt (same renderer as phase-4 output). Only `y`/`yes` (trimmed, case-insensitive) on
  a newline-terminated line confirms; EOF without newline or a read error declines.
* After confirming with ≥2 survivors (no `--yes`): `Select a new default profile:` + picker;
  cancel still removes the profile and clears the default (no "is now set" line).
* `--yes` paths unchanged (first sorted survivor).

### `pb login`
* Steps, keys, required-field messages, Esc back-navigation and duplicate-name confirmation exactly
  as `pkg/model/login`. URL/username/API key/profile name trimmed; password not trimmed (whitespace
  is accepted, empty rejected). No URL syntax validation (Go stores the trimmed text).
* Input limits: URL 256, username/password/profile 64, API key 512 runes; password and API key
  masked with `•` (width-based). Profile name starts as `default`.
* The duplicate check reads config only at Enter on the profile-name step; any read error means
  "not existing". Replace keeps the name; Change name returns to the field with its value.
* Ctrl+C anywhere: wizard quits, final frame stays, command succeeds silently with no write.
* Self-hosted: the "✓ profile saved" card is the final frame *before* saving; a save error follows
  as `failed to save profile: <err>`.
* Cloud API key: card shows both AUTH lines, then `validate_cloud_api_key(release URL, key)`,
  then save (orchestrator URL stored untrimmed, as Go's `config.CloudOrchestratorURL`).
* Cloud device: wizard final view is `""` (previous card stays — needs `draw("")` no-op, A7), then
  the device flow, then save and the three success lines on stdout.
* `$HOME` missing: the wizard runs; the duplicate check treats it as absent; saving fails.

### Cloud device flow
* Empty release orchestrator URL → `cloud orchestrator URL is not configured` (no terminal use).
* `POST api/v1/cli/device/code` body `{"client_id":"pb-cli"}`, 30 s client timeout, no context
  deadline, before any terminal state change. Errors: URL join unprefixed
  (`invalid orchestrator URL: …`); everything else `failed to create cloud device code: …`
  (`<status line>: <trimmed body>` / `failed to decode response: <json err>` / transport).
  Missing `device_code`/`user_code`, then blank `verification_uri`, are separate errors.
* Context deadline = response `expires_in` seconds or 5 min; poll interval = `interval` or 5 s,
  both as Go int64 nanoseconds with wraparound (L-AUTH-12).
* Poll: wait interval first (deadline first → `cloud device login expired or timed out`), then
  `POST api/v1/cli/oauth/token` with the device-code grant, 30 s client timeout and the context
  deadline. 2xx → decode (`failed to decode cloud token response: …`); non-2xx with an OAuth
  `error`: only `authorization_pending` repeats, `expired_token` → `cloud device code expired; run
  login again`, others → `cloud device login failed: <error>[: <description>]`; otherwise
  `cloud token request failed: <status line>: <trimmed body>`. Do-level errors get the
  `cloud token request failed: ` prefix; body-read errors are raw.
* Decoding follows encoding/json: case-insensitive keys, later keys win, null keeps zero, first
  type mismatch in document order (nested `workspace` included) reported with Go's
  `json: cannot unmarshal … into Go struct field …` text.
* Screen: title, 60 `─`, URL, code, Dot spinner (100 ms frames), status
  waiting/✓/✗; final frame stays. Ctrl+C → `cloud device login aborted`.
* Browser: at +5 s if the screen is still open and the deadline is later; `xdg-open` / `open` /
  `rundll32 url.dll,FileProtocolHandler`, stdio null, `restore_child_limits` before spawn,
  never waited, failures ignored.
* Ending the screen drops the poll future (Go's deferred cancel).
* Profile: requires non-blank access/refresh token and workspace id/url/tenant; stores session +
  refresh tokens, tenant, workspace id/url, orchestrator URL (no workspace name).

## Findings from Go sources that differ from the plan's wording

1. **Picker filtering never narrows.** `defaultprofile.Model.Update` forwards only `KeyMsg` to the
   list, so bubbles' asynchronous `FilterMatchesMsg` (and textinput blink msgs) are dropped. The
   visible set stays the full snapshot taken when `/` is pressed; after accepting a filter, Enter
   selects the item under the cursor in the unfiltered order. The filter cursor never blinks.
   Reproduced; test `picker_filter_never_narrows_because_matches_are_not_forwarded`.
   **Confirmed by the pinned Go PTY oracle** (`/home/ajs/.cache/pb-phase7/auth-oracle/picker_oracle.py`,
   result `go-picker.json`): 12 runs with randomized first item (alpha/beta/gamma), the choice
   after `/ g Enter Enter` always equalled the first drawn item; the list never narrowed.
2. **Credential "partial values"** are not saved: `Values()` is both-or-neither (see above),
   confirmed independently by the effects agent.
3. **Full help is reachable** (`?`): the model disables it, then `SetFilteringEnabled(true)`
   re-enables it when >1 other binding is enabled; toggling changes page size (5 items: 4/page →
   2/page). bubbles' column-separator condition compares the group index with the group length;
   reproduced.
4. **Exit erases the last line** for views not ending in `\n` (picker help line, form button):
   Bubble Tea `stop()` erases the current line after the final flush (A6).

## Divergence ledger (auth lane)

| ID | Scenario | Go | Rust | Status |
| --- | --- | --- | --- | --- |
| L-AUTH-1 | Profile order in pickers | Go map iteration (random per run) | sorted (`BTreeMap`) — one Go permutation | preserve-as-permutation; oracle must normalize order |
| L-AUTH-2 | Device Ctrl+C before first poll result | race: `aborted`, or the cancelled poll's `expired or timed out` / `cloud token request failed: … context canceled` (parent oracle observed `expired or timed out`) | always `cloud device login aborted` | runtime race; parent characterizing allowed set; no fix chosen |
| L-AUTH-3 | Enter in a picker with zero profiles (`profile default` on an existing empty config) | `nil.FilterValue()` panics in Update; bubbletea v1.2.2 `recoverFromPanic` kills the session (no final render), prints `Caught panic:\n\nruntime error: invalid memory address or nil pointer dereference\n\nRestoring terminal...\n\n` to stdout and a stack to stderr; `Run` returns `(nil, nil)`; `_m.(defaultprofile.Model)` panics → stderr `panic: interface conversion: tea.Model is nil, not defaultprofile.Model` + trace, **exit 2**, config unchanged (PTY oracle confirmed) | picker returns `PickerOutcome::Panicked`, driver takes the kill path (`RunEnd::Panicked`); `ProfileError{stdout: Caught panic…, fatal: Some("panic: interface conversion: tea.Model is nil, not defaultprofile.Model\n")}`; lib (landed) prints stdout, raw stderr, exit 2 | preserve; traces → L-AUTH-10 |
| L-AUTH-10 | Go goroutine stack traces after a recovered/fatal panic | `debug.PrintStack()` of the recovered panic + Go runtime fatal trace on stderr | not reproduced: only the first `panic: …` line is written | scoped diagnostic difference (addresses/goroutine ids are not reproducible) |
| L-AUTH-11 | bubbles textinput faults reachable in auth flows: Alt+D (`deleteWordForward`) with the cursor one rune before the end of an EchoNormal field indexes `value[len]` → `runtime error: index out of range [len] with length len`. Masked fields (password, API keys) use `deleteAfterCursor` and cannot fault. | Fields: login URL, username, profile name; credential username; picker filter while filtering. Go recovers the panic (kill path, `Caught panic:\n\n<value>\n\nRestoring terminal...\n\n` stdout + stack stderr), then per caller: **login** (`m, ok :=` comma-ok) → returns nil, exit 0, nothing saved; **profile add** (`_m.(credential.Model)`) → fatal `panic: interface conversion: tea.Model is nil, not credential.Model`, exit 2, nothing written; **picker** callers (`profile default`, `profile remove`, `logout`) → fatal `… not defaultprofile.Model`, exit 2, nothing written (remove/logout already printed their `Select …:` line). | editor `take_fault()` → `InlineModel::take_panic` (checked after every Update and View, and on the final view) → `RunEnd::Panicked`; same stdout/fatal/exit mapping per caller | preserve; **PTY oracle confirmed** for login, profile add and picker filter (Go and Rust debug build match exit status, config effects and fatal text; only stack traces differ) |
| L-AUTH-12 | Device `expires_in`/`interval` overflow | `time.Duration(n) * time.Second` wraps int64 nanoseconds; a wrapped nonpositive expiry makes the context already expired (no browser, poll returns `cloud device login expired or timed out` — Go selects randomly if the zero-delay interval timer is also ready, the other branch failing with `cloud token request failed: … context deadline exceeded`); a wrapped nonpositive interval fires immediately | `go_seconds` = `wrapping_mul(1e9)`; nonpositive timeout → deadline = now; nonpositive interval → zero sleep; tests with 9999999999 s | preserve (random select ⇒ Rust picks the expired branch deterministically; race documented) |
| L-AUTH-4 | SIGINT/SIGTERM during device screen | tea QuitMsg ends Run without calling Update: `done` stays false, so the final frame keeps the spinner + `Waiting for authorization...` line; nil tokens → `cloud token response missing access_token` (oracle confirmed) | `Event::Quit` → `Outcome::Quit`, rendered as the waiting line (fixed after `device-differential-1/device-sigint` showed Rust drawing the success line), same error | preserve; re-run device-sigint |
| L-AUTH-14 | Lifecycle exits of the inline programs (review N3 + background panics) | bubbletea v1.2.2 `Run`: (a) **input read error** — `eventLoop` returns the error, `killed` depends only on the context, so Run renders the final view and `shutdown(false)`; callers wrap the error (`error reading credentials: …`, `error selecting new default profile: …`, `error selecting profile to logout: …`, raw for `profile default` and `login`, `failed to render cloud device login: …`). (b) **Update/View panic** — Run's deferred `recoverFromPanic`: kill path, `Caught panic:` stdout, Run returns `(nil, nil)` → per-caller outcome of L-AUTH-3/L-AUTH-11. (c) **command-goroutine panic** — that goroutine's `recoverFromPanic` kills the session and prints `Caught panic:` to stdout; the canceled context makes Run return `(model, "program was killed: context canceled")`, which callers wrap like (a); nothing saved. The stdout print races process exit in Go. | (a) `close(final view)` then `Err(read error)` in `run_inline` and the device screen (was a kill-path drop); (b) unchanged; (c) `Event::Panicked(value)` (terminal owner, session already restored) → print `Caught panic:` to stdout, `Err("program was killed: context canceled")` through the same caller wrapping (device: `failed to render cloud device login: program was killed: context canceled`). **Device poll** (authorized correction) now runs as an owned Tokio task (`PollTask`, Go: the poll command goroutine): spawned with Init after the first render is queued; Ctrl+C aborts it at once (Go `cancel()` in Update, dropping the in-flight request); every other exit aborts and joins it after the terminal is restored (Go's deferred cancel); dropping the owner aborts. A task panic (`JoinError::is_panic`) takes (c): kill-path drop, `Caught panic:\n\n<payload>\n\nRestoring terminal...\n\n` on stdout, `failed to render cloud device login: program was killed: context canceled`, exit 1, nothing saved. Non-string payloads print `Box<dyn Any>` (Go prints the value with `%s`). Timing, request/expiry races and the Ctrl+C race set (L-AUTH-2) are unchanged; no staleness guard. | (a)/(c) source-derived, oracle not yet run |
| L-AUTH-13 | Device spinner phase | `Init` batches `spinner.Model.Tick`, an immediate TickMsg: frame 1 follows the first render, then `tea.Tick(100ms)` per frame scheduled when each tick is handled | first frame tick is due immediately, later ones 100 ms after handling | preserve; remaining glyph differences are animation phase (parent normalizes valid phases) |
| L-AUTH-5 | Ctrl+V in text fields | clipboard read (atotto) → pasteMsg routed like any non-key msg | editor `take_paste_request` → owned `clipboard::read()` task in `run_inline`'s JoinSet; success → `apply_paste` (wizard: current step's input; form: focused input + latch re-check; picker: read runs, result dropped as Go drops it); failure ignored; reads aborted/joined at exit | preserve; needs clipboard fixture |
| L-AUTH-6 | Non-UTF-8 error bodies | raw bytes in message | lossy UTF-8 | minor; record |
| L-AUTH-7 | Unicode case folding of JSON keys | Go SimpleFold | ASCII case-insensitive | minor; record |
| L-AUTH-8 | Credential save after cancel | saves `Values()` (possibly empty) | same | preserve (ledgered defect) |
| L-AUTH-9 | `ExpiresIn=1, Interval=1` | timer vs deadline race (oracle saw `cloud token request failed … context deadline exceeded`) | interval < deadline wins deterministically | runtime race; oracle uses Interval 2 |

## Unit tests

* `tui::profile::tests`: picker selection/paging/quit keys, filter non-narrowing, full-help page
  size, empty picker; credential both-or-neither, sticky latch, Enter cycling; confirmation parsing.
* `tui::login::tests`: self-hosted trimming, required fields + backtracking, duplicate
  replace/change name, cloud Esc paths, device final view `""`.
* `tui::device::tests`: device-code/token/OAuth-error decoding and Go error text, token→profile
  validation, session-name normalization, screen states and Ctrl+C.
* `profile::interactive_tests`: config before/after and stdout ordering for add/default/remove/
  logout including cancel paths, program errors, Go fatal panics (empty picker, form fault).
* Fault reachability: Alt+D before the last rune in wizard, form and picker filter; masked fields
  do not fault. Device durations: wrapping int64 nanoseconds, 9999999999 s cases.

## Open items

* Re-run `device-sigint` after the L-AUTH-4 view fix and confirm spinner-phase normalization
  covers the remaining device frame differences.
* PTY cell comparisons for wizard/form/picker views at fixed sizes and both themes; clipboard
  paste fixture.
* `TerminalSession::draw("")` must leave the previous frame on screen (A7); the device-login
  card handoff depends on it.
* L-AUTH-2 (Ctrl+C race set) remains a characterized runtime race; no fix chosen.

## Opus final R3 O3-1 resolution (2026-10-14; successor validation pending)

CredentialForm now reproduces the non-intercepted button path: Alt+Enter is
KeyType Enter, requires focused Submit and the pre-message Invalid latch to be
clear, then requires currently valid credentials as credential's Pressed handler
does. Plain Enter still uses the existing sticky latch alone. Esc/Ctrl+C still
save both values or neither; no cancellation or partial-value quirk is corrected.
This is scoped to CredentialForm, not a blanket Alt+Enter binding.

Actual immutable Go/release3 controls use
`docs/evidence/phase7/opus-r3-low-process.py`, loading the unchanged embedded
PROCESS_FIXTURE from `tests/interactive_effects.rs`. Before Alt+Enter, the
120×35 terminal frame proves username/password content, button label/latch and
focus by the button's accent color. Go exits and saves valid focused credentials;
release3 remains in the identical form and saves them only after cleanup Ctrl+C.
Both binaries preserve valid-but-unfocused Alt+Enter, the invalid latch,
cleared-latch/current-invalid Alt+Enter, plain Enter with current-invalid values,
partial cancellation and valid cancellation. All seven processes per binary
restore termios, emit no HTTP request and preserve the expected config semantics.
See `opus-r3-low-go-process.json` and `opus-r3-low-release3-process.json` in the
same evidence directory; raw stdout/stderr and per-case records are retained
under `/home/ajs/.cache/pb-phase7/opus-r3-low/` with hashes.

The new owned reducer regression parses actual ESC CR and distinguishes focus,
the latch and current validity. Its valid-values/invalid-latch subcase is a
constructed reducer guard control, not a claimed reachable PTY state. Rustfmt
passes. No Rust build, crate test or successor process was run by this lane;
parent central validation and the same runner against a hash-bound successor
remain required. The release3 failure does not imply successor acceptance.

## Pending-blink routing correction: login wizard and credential form (2026-10-14)

Status: applied in the authorized login and credential/profile edit windows. Files:
`src/tui/editor.rs`, `src/tui/login.rs`, `src/tui/profile.rs` (CredentialForm only). This
lane ran no build, rustfmt, test, CLI or process. The parent owns formatting, central tests,
hashes and fresh hash-bound successor controls, and an independent review follows. No
successor parity is claimed. The editor-side contract is in docs/phase7-editor.md ("Pending
blink timers and caller routing"). The design history, including the earlier off-tree proposal
that treated the credential form as unanalysed and then as proposal-only, is in
`docs/evidence/phase7/login-blink-design-r1.md`. Those proposal stages are superseded by this
application.

Findings (pre-fix):
* **Login:** release4 styled cases differed in one cell, zero-based row 6/column 21 (the blank
  after 14 password bullets). Go shows a plain blank (fg default, reverse false); Rust showed a
  reversed accent block. Pinned-Go dwell controls attribute this to the departed username's
  timer:
  - 200 ms dwell: the caret turns plain before the password's own 530 ms deadline;
  - 650 ms dwell: the username ticks first and the caret stays reversed.

  Matched release4 controls (`docs/evidence/phase7/login-blink-process-release4.json`) show
  the 200 ms gap at only the late checkpoint cell; the 650 ms control matches.
* **Credential form** (`pb profile add`): bounded Go/release4 controls
  (`docs/evidence/phase7/credential-blink-results.md`): collisions A and B reproduce a
  single-cell early toggle and re-arm gap; C matches strictly.

No checkpoint normalization is used, because it would hide the lifetime difference.

Go mechanisms (pinned 07af390, `docs/evidence/phase7/login-credential-blink-source-pin.json`;
ids/tags inferred from source, not instrumented). In both models all cursors have id 0,
`Blur()` cancels nothing, and a focused input accepts a departed input's `BlinkMsg` whenever
the tags are equal. Accepting it calls that input's `BlinkCmd`, which cancels its own pending
timer and arms tag+1.

* **Login (+2 per focus).** Every focus change calls `x.Focus()`, discards its command and
  returns `textinput.Blink`. `login.Update` forwards non-key messages to the active step's input
  only, and selection steps drop them. Returning to a field cancels its departed timer through
  `Focus()`. The Esc from profile name to the cloud auth choice returns `Blink` with no active
  input, so it is dropped.
* **Credential (constructor +2, navigation +1).** `New` discards the username's `Focus()`
  command and `Init` returns `textinput.Blink`. Navigation returns the `Focus()` command itself
  (one arm). `updateInputs` broadcasts every non-key message to both inputs, and the blurred one
  drops it. Broadcast does not remove the collision: it only fixes which input can accept.
  Collision A (empty username, Tab, one password chunk) and collision B (one username chunk,
  two password chunks) are equal-tag acceptances. C (one chunk each) is stale. D (button
  focused) drops everything.

Native:
* **Login:**
  - `focus_input` = `focus()` + `disarm_blink()`;
  - `initial_blink(now)` delivers the returned `Blink` to the input active at delivery;
  - `next_tick` = the earliest outstanding timer of all five fields (at most one each);
  - `tick` takes one due timer and delivers its tag to the active input, or drops it at a
    selection step;
  - keys and pastes use the wake instant (`update_at`/`paste_at`).
* **Credential form:**
  - constructor: `focus()` + `disarm_blink()` + `start_blink()`, which gives Go's user tag 2;
  - navigation keeps the honored `focus()`;
  - `next_tick` = the earliest outstanding timer of both inputs;
  - `tick` (`blink`) takes one due timer and calls `deliver_blink` on both inputs.

  Keys, paste, the validity latch, Alt+Enter and the fault mapping are unchanged. The latch
  re-check Go runs after a blink cannot change, because values are unchanged.

The clipboard read lifecycle (L-AUTH-5), fault/panic mapping (L-AUTH-11/14), CheckName and every
step/key contract above are unchanged.

Limits, not Go guarantees:
* login's initial blink is delivered synchronously, before the next key;
* the credential constructor arms at construction rather than at `Init` delivery;
* there is one delivery per wake, and simultaneous timers fire in field/input order;
* tag equality assumes Go-identical key-message segmentation.

Tests (added, not executed by this lane):
* `tui::login::tests`: dwell-200/dwell-650, several departed timers, refocus cancellation, a
  selection-step drop through `WizardSession`, and paste re-arm, all with explicit instants.
* `tui::profile::tests`: collision A, B, C and D, and Shift+Tab refocus. The refocus test proves
  only that the user's own timer is canceled and the departed password's stale tag is rejected;
  the equal-tag collision E is not exercised.
* `tui::editor::tests::input_blink_timer_survives_blur_and_matches_by_tag`.

The parent should re-run the full login and profile-add corpora, because every step that
follows a field with an outstanding timer can change, not only the password.

## Independent production R6 scheduling qualification

Go offers blink messages only after the timer expires and its command goroutine reaches the shared message channel; deadline order alone does not determine key/blink delivery order. Native inline drivers prefer a ready input event over a ready timer. Near a collision, a cursor-moving key can replace a due timer before its accepted-delivery tag increment; a navigation key can instead route that departed due tag immediately to the newly focused field. Relative to a Go execution that sends the blink first, either can alter a later cross-field match and the caret's blink phase. Values, requests, persistence and lifecycle are unaffected. The single-input saved filter has no persistent cross-field effect. This is a source-level scheduling limitation: no process control measures the collision window or establishes a numerical upper bound. Timer wake latency and loop work can both affect ordering; no new scheduler policy is implemented.

See [R6 review](phase7-review-final-opus-r6.md). Source review is bounded; final styled/device/retained successor gates and non-Linux behavior remain separate.
