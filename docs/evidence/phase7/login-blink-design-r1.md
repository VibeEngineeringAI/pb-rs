# Login pending-blink routing: design r1 (prepared off-tree, not applied)

> Historical design record. The login/editor patch and the CredentialForm proposal (P1–P4)
> were later **applied** in authorized edit windows. See "Application record" at the end, which
> supersedes the "not applied", "unanalysed" and "proposal only" statements below.

Kind: implementation design and prepared patch from the editor/login lane. It is not an
independent review and makes no acceptance or performance claim. The lane edited no source
under `src/`. The patch is in `/home/ajs/.cache/pb-phase7/login-blink-fix/`:
`editor-edits.md` (E1–E7), `login.rs` (full post-image), `docs-edits.md` (D1–D3) and
`README.md`. The lane's shell failed with EDQUOT on its first attempt, so no hashes were
recomputed and nothing was built or run. Base hashes are the ones recorded in
`login-blink-investigation.json`: login.rs `7898c967…c301`, editor.rs `454da270…7244117`.

## Inputs read in full

* `pkg/model/login/login.go` (pin 07af390): every focus site returns `textinput.Blink` and
  discards the `Focus()` command. ProfileName Esc to ChooseCloudAuth returns `Blink` with no
  active input. Blur sites return nil. Non-key messages go to the active step's input only.
* bubbles v0.20.0 `cursor/cursor.go`: `New` leaves id 0 and there is no setter. `BlinkCmd`
  cancels the previous context synchronously through the `*blinkCtx` that all copies share,
  increments `blinkTag`, and returns a closure that reads `m.id`/`m.blinkTag` through a pointer
  to a copy nothing mutates after that `Update` returns. So the message carries the tag of its
  creation. `Focus` calls `BlinkCmd`. `Blur` only changes `focus`/`Blink`. A `BlinkMsg` is
  accepted iff focused, `id` equal and `tag` equal.
* `textinput.go`: `Update` returns early while blurred. A moved position triggers
  `Blink=false` and `BlinkCmd`. Ctrl+V returns before any blink work. `pasteMsg` inserts, and a
  move re-arms. Each `Update` calls `BlinkCmd` at most once.
* Native `src/tui/editor.rs`, `src/tui/login.rs`, `src/tui/profile.rs` (`run_inline`, picker,
  credential form), and the head of `src/tui/terminal.rs`.

## Verified cross-input contract (what the patch reproduces)

| Event | Go | Native after patch |
| --- | --- | --- |
| login focus change | `Focus()`: tag+1, old timer canceled, command discarded; then `initialBlinkMsg` to the active input: tag+1, armed | `focus_input` (`focus`+`disarm_blink`), then `initial_blink(now)` → `start_blink_at` on the active input |
| Blur | timer stays live | `BlinkCursor::blur` keeps `timer` |
| cursor move / paste | tag+1, replaces the timer | `moved` → `arm` |
| timer fires, active step has an input | `BlinkMsg{0, src tag}` → accepted iff the tag equals the active input's tag; accepting toggles it and replaces the active input's own timer | `take_pending_blink` on the source, then `deliver_blink(tag)` on the active input |
| timer fires at a selection step | dropped | taken and dropped |
| refocus a departed field | `Focus` cancels its timer | `focus` replaces it and `disarm` drops it |
| several departed timers | at most one per cursor, each delivered independently | at most one per field (5), one delivery per `tick` |

Dwell traces (one typed chunk = one key message; tags shown). 200 ms: username tag 3 armed at
type+530. Password focus→2, typed→3. The username timer fires about 76 ms after password
dispatch with tag 3, which equals the password's 3, so the caret turns plain and the password is
re-armed with tag 4 (its own deadline is canceled). This matches the Go late checkpoint plain at
263.6 ms. 650 ms: the username ticks while active (tag 4). The password ends at 3, so the stale
tag 4 is dropped and the caret stays reversed until the password's own deadline. This matches
both Go checkpoints reversed. Both cases are deterministic regressions in the prepared tests,
with explicit instants.

## Constraints vs deterministic assumptions

Go constraints reproduced exactly: tag arithmetic, cancel-on-call, blur keeps the timer,
active-step routing, selection-step drop, one live timer per cursor. Deterministic assumptions,
not Go guarantees: (a) `initialBlinkMsg` is delivered before the next key. A cursor-moving key
racing it ends at the same tag either way, since both increment, but a step-changing key racing
it would route it elsewhere. (b) Timers due together fire in field order. (c) The existing
driver prefers input events to timers (`first(event, …)`). (d) Instants are taken once per wake,
before the edit, instead of after it. The difference is microseconds and is not used as
measurement.

## Caller contract (r1 refinement, file-tool inventory)

**Visibility bound.** `BlinkCursor`, `TextInput.cursor` and `TextArea.cursor` are private to
`src/tui/editor.rs`, which has only its own `mod tests`. Nothing outside that file can touch
the timer or the tag except through the methods below. I read all 2,404 lines. The current
references to `cursor.deadline` are `BlinkCursor` itself (E2), `TextInput::blink_deadline` and
`TextInput::disarm_blink` (E3), and `TextArea::disarm_blink` (E6). After E1–E7 none remain.
TextArea only calls `cursor.focus`, `cursor.blur` (from `blur` and from blurred `update`),
`cursor.moved` (`finish_update`), `cursor.view`, and `disarm_blink`. The parent's central
search is a confirmation of this, not new scope.

**Polling contract** (`focus`, `blur`, `update`, `insert_paste`, `start_blink`,
`disarm_blink`, `blink_deadline`, `tick`, `view`): for the same call sequence, the patch
leaves the visible state (`hidden`), every `blink_deadline()` value and every `tick()` result
unchanged.
* While focused, `timer.map(due)` equals the old `deadline`, because every writer sets both
  the same way.
* The two differ only while blurred: the old code holds `None`, the new code may hold the kept
  timer. Every reader is focus-gated, and the only way back to focused is `focus()`, which
  overwrites the timer.
* `tick` accepts on the same condition as before. The tag check always passes because the
  timer's tag is the cursor's current tag (invariant).
* One limit: `update` and `insert_paste` now take `Instant::now()` before the edit instead of
  after it. That moves the re-arm earlier by the edit's duration (microseconds) and has no
  semantic effect.

**Routed contract** (`pending_blink`, `take_pending_blink`, `deliver_blink`, `*_at`).
1. Treat `pending_blink` as the input's single live Go timer.
2. Take it when it fires, before delivering.
3. Deliver its tag to the inputs that the Go parent's `Update` passes non-key messages to.
   Blurred inputs reject, so "broadcast to all" and "active input only" pick the same receiver.
   Neither stops a departed input's same-tag message from being accepted by that receiver.
4. Mirror the Go parent's handling of the `Focus()` command: if honored, call `focus()`; if
   discarded, call `focus()` then `disarm_blink()`. Deliver a returned `textinput.Blink` with
   `start_blink_at` to the receiving input.
5. Never combine `tick` with take/deliver on the same input. `tick` is own-timer delivery and
   would bypass routing.

Limits: cursor ids are not modelled (always 0 in pb). There is one delivery per wake. Ties are
resolved in a fixed order.

## Inventory and residuals

| Driver (Rust) | Go routing of non-key msgs | Policy after patch | Status |
| --- | --- | --- | --- |
| `tui::login` | active step's input only; `Focus()` command discarded + `textinput.Blink` | routed | corrected (this patch) |
| `CredentialForm` (profile.rs:793–972) | `updateInputs` broadcasts every non-key msg to both inputs, and a blurred input returns early in `textinput.Update`, so effectively the focused one (credential.go:166, 175–184). Nav keys return `Focus()` honored (`cmds[i] = …Focus()`, :144). `New` discards user `Focus()` (:81), and `Init` returns `textinput.Blink` (:105–107). | polling (unchanged) | **concrete gap that predates the patch, left exactly as it was; not corrected** (R1) |
| `ProfilePicker` filter (profile.rs:308–314, 768–789) | Go drops all non-key msgs (finding 1) | `focus`+`disarm_blink`, no `next_tick` | unaffected (timers inert) |
| saved menu filter (`saved.rs`, other owner) | single filter input (bubbles list); per docs/phase7-admin-saved.md it polls `blink_deadline`/`tick` | polling | unaffected by patch. With one input there is no cross-input route, and refocusing cancels its own timer in Go and natively. Saved parity is that lane's claim, not this one. |
| SQL/PromQL text inputs and textarea (other owners) | SQL Q3: Focus cmds discarded, no BlinkMsg case. The textarea never receives BlinkMsg (editor doc). | polling or none | unaffected by patch; not re-derived here |
| admin dataset/role screens | no editors (admin-saved doc) | — | n/a |

**R1, credential form:** concrete Go trace (one chunk = one key; Go tags):
1. `Init`: user tag 2, due t0+530.
2. User types "admin" at +10: tag 3, due +540.
3. Tab at +100: password Focus is honored, tag 1, due +630. The user's timer (540, 3) stays
   live.
4. Password chunks at +150 and +200: tag 3, due +730.
5. At +540 the user's `BlinkMsg{0,3}` is broadcast. The user is blurred and drops it; the
   password's tag is 3, so the password turns plain and re-arms with tag 4, due +1070, which
   cancels +730.

Rust's polling form ticks only the focused input's own timer. It shows the password caret
reversed until +730, and its next toggle back to reversed is at +1260, not +1070. Visible
differences: [540, 730) and [1070, 1260). The general condition is that the departed input's
tag equals the focused input's tag when the departed timer fires.

Broadcasting does not prevent the collision. It only means the focused input is the one that
can accept. Navigation adds one arm per focus (the `Focus()` command is honored). The
constructor adds two (`Focus()` discarded, then `Init`'s `Blink`). Login adds two per focus.
The full collision table (A–E) and the exact off-tree edits/tests are in
`/home/ajs/.cache/pb-phase7/login-blink-fix/credential-proposal.md`. That proposal is not
authorized, not applied, and not part of the combined login/editor patch. In short:
* `new()`: user `focus()` + `disarm_blink()` + `start_blink()`, so tags are Go-exact (today the
  native user tag is 1 lower).
* nav: keep `focus()` (honored).
* `next_tick`: minimum `pending_blink` over both inputs.
* `tick`: take the earliest due timer and call `deliver_blink(tag)` on both inputs (the
  blurred one rejects).

Paste and fault mapping would be unchanged.

* Residual R2: login process parity also depends on the native parser producing the same key
  message segmentation as Go, because tags count cursor-moving messages. The controls suggest it
  does for these cases, but nothing proves it in general.
* Residual R3: `login-blink-process.py` `FROZEN_FILES` include login.rs/editor.rs. After the patch
  is applied, its `sources_unchanged` binding no longer holds, and successor controls need a new
  binding.
* Residual R4: the URL and other departed fields now also route timers. Any login step after a
  field with an outstanding timer can change, so the full login corpus must be re-run, not only
  the password cases.

## Unresolved objections before application

None that block the login design. Open items:
* build/clippy (this lane has never compiled it);
* the parent's confirmation search for `cursor.deadline`, which should return nothing after
  E1–E7, and for callers of the new methods, which should be login only;
* the parent's disposition of R1. R1 is pre-existing, the patch doesn't change it, and a fix
  needs its own profile.rs window and a Go credential control.

## Application record (2026-10-14)

The root opened two windows: "login blink edit window open" and the credential profile edit
window. Before applying, the root verified that the original login/editor/profile hashes were
exactly the frozen ones and that the pinned Go credential/login bytes match 07af390
(`login-credential-blink-source-pin.json`). It also staged the combined candidate
`/home/ajs/.cache/pb-phase7/login-blink-root-staging/combined-proposed.patch` (sha256
f5008ef789128dbc672486d63bd0aad9ee7d0d787d86ac1ec43a69514fb1aa8a). That candidate has one
unformatted profile assert wrap, which is left to the root's rustfmt.

Before writing, this lane re-read the original source regions with file tools, and every
exact-match edit matched exactly once. Applied:
* `src/tui/editor.rs`: E1–E7 as in `editor-edits.md`.
* `src/tui/login.rs`: the formatted post-image (with the two rustfmt wraps).
* `src/tui/profile.rs`: P1–P4 as in `credential-proposal.md`, including the tie-safe E test.
  CredentialForm only.
* `docs/phase7-editor.md`: blink paragraph reconciled, and a section describing the polling
  policy and **both** routed policies appended.
* `docs/phase7-auth.md`: application section appended.

Superseded statements, kept above as history:
* R1 "not corrected/not analysed": the credential form is now on the routed broadcast policy.
* "Other policies unchanged": the polling contract still holds for the saved filter, picker
  filter, SQL/PromQL inputs and textarea. Login and the credential form are now routed.

Corroborating pre-fix evidence (`credential-blink-results.md`): bounded Go/release4 controls
in which A and B reproduce the single-cell early toggle and re-arm gap and C matches strictly.
They are pre-fix evidence only and make no successor claim.

Limits that remain:
* login's synchronous initial blink;
* the credential constructor arming at construction;
* one delivery per wake with ordered ties;
* key-message segmentation;
* no cursor ids.

The P4 refocus test covers only stale-tag rejection after Shift+Tab, not an equal-tag collision
E. Nothing was built, formatted, tested or executed by this lane, and no successor parity is
claimed.

## Post-application O5-1 scheduling qualification (source-only, R5)

The input-first assumption can affect tag accounting as well as an intermediate caret frame. If a key is selected while the owning field's blink is already due, that key can replace the timer without delivering its tag increment. In a Go execution where that blink message is delivered before the key, the field instead increments once for the blink and again for the key; the difference can change acceptance of a later departed-field message. Deadline order alone does not guarantee Go message delivery order. No actual process control establishes this collision window or its frequency. The native behavior remains unchanged; the precise qualification is under fresh independent R6 review. See `docs/phase7-review-final-opus-r5.md` and `docs/phase7-review-resolutions.md`.

## R6 qualification of O5-1

Go offers blink messages only after the timer expires and its command goroutine reaches the shared message channel; deadline order alone does not determine key/blink delivery order. Native inline drivers prefer a ready input event over a ready timer. Near a collision, a cursor-moving key can replace a due timer before its accepted-delivery tag increment; a navigation key can instead route that departed due tag immediately to the newly focused field. Relative to a Go execution that sends the blink first, either can alter a later cross-field match and the caret's blink phase. Values, requests, persistence and lifecycle are unaffected. The single-input saved filter has no persistent cross-field effect. This is a source-level scheduling limitation: no process control measures the collision window or establishes a numerical upper bound. Timer wake latency and loop work can both affect ordering; no new scheduler policy is implemented.
