# Phase 7 — shared editors and clipboard

Owner: editor sub-agent. Files: `src/tui/editor.rs`, `src/tui/clipboard.rs`, this document.
Baseline: Go `07af390` (no diff in `pkg/model`, `pkg/ui`, `go.mod`, `go.sum` against the pin),
bubbles v0.20.0, bubbletea v1.2.2, lipgloss v1.0.0, x/ansi v0.4.5, atotto/clipboard v0.1.4.

## Scope

Native owned reimplementations of bubbles `textinput.Model` and `textarea.Model`, plus pb's
`textAreaKeyMap` (`pkg/model/textareakeymap.go`) and `applyEditorStyles` (`pkg/model/query.go`).
They are reducers: `update(&Key)` mutates owned buffers (`Vec<char>` / `Vec<Vec<char>>`, rune
indexed like Go `[]rune`), `view(&styles)` renders through the shared `tui::style` module. There is
no widget framework, trait, or global state. The published API is in
`~/.cache/pb-phase7/terminal-requests.md` ("ACCEPTED — editor owner" and "Editor API v2").

Users in pb (from the Go source): login/credential fields (`CharLimit` 32/64, `EchoPassword` with
`'•'`), list filter inputs (bubbles `list`), SQL/PromQL dataset/column/builder filters, the PromQL
step input (`Width` 4, empty prompt), the datetime input (`Width` 20, `SetCursor`, keymap
matching), and the SQL/PromQL query `textarea` (`MaxHeight`/`MaxWidth` 0, pb keymap, editor
styles, line numbers on for SQL / off for PromQL, default `CharLimit` 400 kept).

## Behaviour reproduced (Go-exact)

TextInput: default keymap and its match order (delete-word-backward is checked before backspace,
etc.); unbound keys insert `Key.runes` (so an unbound `alt+x` inserts `x`, tab/up/down insert
nothing); sanitizer (tabs and each CR/LF → one space, controls and U+FFFD dropped); char limit
counted in runes; `SetValue` cursor rule (moves only if it was 0 on an empty value or is past the
end); masked word motion/deletion jump to start/end; `handleOverflow` horizontal window with
go-runewidth per-rune widths (including bubbles' six-cells-in-width-5 window after Home);
view padding rules, password mask repeated by display width, placeholder layout.

TextArea: `New()` defaults (prompt `"┃ "`, width 40 → text width 34, height 6, CharLimit 400,
MaxHeight 99, MaxWidth 500); soft wrap algorithm (whitespace → ASCII spaces, trailing space cell,
double-width carry); `LineInfo`, vertical motion with remembered char offset (and Go's transient
negative column at the first row); merge/split semantics that do not reset the remembered offset;
word motion across lines; Alt+U/L/C with Go simple case mapping (ß unchanged); Ctrl+T transpose;
char limit counting display cells plus newlines but truncating by rune count (wide runes can
overshoot — Go quirk kept); `MaxHeight` row cap; placeholder word/hard wrap; line-number format
`" %*v "` with digits of `MaxHeight` (1 digit when 0); end-of-buffer filler; viewport scrolling.

Go value-copy semantics that matter: the textarea viewport is a pointer in Go, so copies share it.
`TextArea::clone()` shares it too (`Arc<Mutex<Viewport>>`): the SQL view renders a resized copy and
the next `Update` scrolls against that copy's height/line count, exactly as Go. `Update` scrolls
using the line count of the *last rendered* view. The Go `style` pointer starts at bubbles'
default blurred styles until the first `Focus`/`Blur`; reproduced (`AreaStyleState::Initial`).

Cursor blink (bubbles `cursor`, 530 ms): `focus()` shows the cursor and arms a deadline (the Go
`Focus()` command run); moves re-arm and show; `tick(now)` toggles; blurred → no deadline *visible
through the polling API* (`blink_deadline()`/`tick()`). The outstanding timer itself is no longer
discarded on blur. See "Pending blink timers and caller routing" below, which corrects the earlier
reading of this line. Callers whose Go model discarded the `Focus()` command (SQL/PromQL
constructors, the profile picker filter, the login wizard, the credential form's constructor)
call `disarm_blink()`. `start_blink()` = `textinput.Blink`.

API kept to what the active screens use (cleanup after the first central build): bubbles'
`EchoNone`, accessor getters and the textarea paste/blink delivery are not provided. pb's
SQL/PromQL models forward only key messages to the textarea (`query.go:1176`, `promql.go:1114`),
so in Go a textarea never receives `pasteMsg` or the cursor `BlinkMsg`: Ctrl+V in the query
editor starts a clipboard read whose result is dropped, and the cursor stays visible after a
move. The textarea therefore exposes only `take_paste_request()` (drivers spawn and discard the
read) and no blink tick.

## Width sources (audited against the Go call sites)

| Go call | Where in bubbles | Rust |
|---|---|---|
| `rw.RuneWidth` (go-runewidth v0.0.16, DefaultCondition incl. CJK locale) | textinput `handleOverflow` window; textarea `CursorDown`/`CursorUp` offset walk; `wrap` double-width carry | `style::bubbles_rune_width` |
| `uniseg.StringWidth` | textinput overflow gate, password mask count, view padding; textarea `SetWidth` prompt, `Length`, `LineInfo`, `wrap` row widths, view padding, placeholder padding | `style::width` (via `text_width`) |
| `lipgloss.Width` / x/ansi `Wordwrap`/`Hardwrap` | textinput placeholder width; textarea line-number gutter, end-of-buffer gap, placeholder wrap | `style::width` |

Tests pin the paths where the two disagree (style-width-oracle: "〰" uniseg 1 / runewidth 2,
"☺️" uniseg 2 / runewidth 1): overflow window, password mask, `Length`, wrap carry and vertical
motion. They assume a non-CJK locale (runewidth's DefaultCondition reads LC_ALL/LC_CTYPE/LANG).

## Clipboard (Ctrl+V)

Go: Ctrl+V returns bubbles' `Paste` command, which runs `clipboard.ReadAll()` in a goroutine and
delivers `pasteMsg` (inserted via the same sanitizing path) or `pasteErrMsg` (stored in `Err`,
never displayed by pb). Rust: `update` sets a pending flag (`take_paste_request()`), the driver
runs `clipboard::read()` as an owned task (aborted on quit; `kill_on_drop`), and delivers `Ok`
text with `insert_paste` to the editor focused at completion (blurred editors ignore it, as Go's
`Update` does). Errors are dropped.

`clipboard::read` follows atotto v0.1.4: Unix selection once per process in `init` order —
`wl-paste --no-newline` (only with non-empty `WAYLAND_DISPLAY` and both `wl-copy`/`wl-paste`),
`xclip -out -selection clipboard`, `xsel --output --clipboard`, `termux-clipboard-get` (needs the
set/get pair), `powershell.exe Get-Clipboard` (needs `clip.exe` too) with `trimDos` (drop the last
two bytes when longer than one byte), else unsupported. `exec.LookPath` semantics: `$PATH`
entries, empty = `.`, regular file with an execute bit, relative matches rejected (Go `ErrDot`).
macOS: `pbpaste`. stdin `/dev/null`, stderr captured/discarded, non-zero exit = error, invalid
UTF-8 → U+FFFD → dropped by the sanitizer (Go drops `utf8.RuneError` the same way). The child gets
`process::restore_child_limits`.

Windows gap (ledger L-EDITOR-CLIP-WIN): atotto reads `CF_UNICODETEXT` via user32/kernel32;
not implemented — Ctrl+V behaves like a failed read on Windows.

## Go faults (panics/hang) — preserved via `EditorFault`, not fixed

Bubbles reaches Go runtime panics or an infinite loop on some inputs. The editors stop at that
point (no further mutation) and report `take_fault()`; drivers reproduce the program outcome
(duties in terminal-requests.md "Editor API v2"). Fixes would be ledger proposals.

| Id | Trigger | Reachable? | Go outcome |
|---|---|---|---|
| F1 | TextInput `deleteWordForward` (alt+d / alt+delete), normal echo, cursor on the last rune | **Yes** — any focused normal-echo field (login URL/username/profile name, filters). Oracle: `index out of range [2] with length 2` for "ab" | panic → bubbletea `recoverFromPanic`: kill renderer, restore terminal, stdout `Caught panic:\n\n<msg>\n\nRestoring terminal...\n\n`, stack on stderr, `Run` returns `(nil, nil)`; caller behaviour per flow |
| F2 | TextArea `wordLeft` (pb: ctrl+left / alt+b) reaching (0,0) when the buffer is empty or its first cell is whitespace | **Yes** — e.g. ctrl+left in an empty SQL/PromQL editor | `Update` never returns: UI frozen, Ctrl+C unread, SIGINT/SIGTERM QuitMsg undeliverable; ends only by SIGKILL/SIGQUIT |
| F3 | TextInput placeholder with wide runes (`p[1:minWidth]` slices by cells) | No: all pb textinput placeholders are ASCII literals ("filter datasets", "filter columns", "filter", login fields) | slice panic (reported as `SliceOutOfRange`) |
| F4 | TextArea view `wrappedLine[ColumnOffset]` past a trimmed over-wide row | Not demonstrated; analysis incomplete (needs differential fuzz) | index panic (reported if hit) |
| F5 | TextArea placeholder that is all whitespace (`plines[0][0]`) | No: pb placeholder is "Write your queries here" | index panic (reported) |

## Ledger items

- L-EDITOR-CLIP-WIN — native Windows clipboard read not implemented (above).
- L-EDITOR-2 (proposal only) — F2 hang; proposed fix: stop at (0,0). Not applied.
- L-EDITOR-3 (proposal only) — F1 panic; proposed fix: stop at end of value. Not applied.
- L-EDITOR-4 — bubbles' `debug.PrintStack` output after F1 cannot be reproduced byte-for-byte.
- Cursor-blink deadlines are driven by the session loop instead of tea commands; observable cell
  timing should match (530 ms) but is unmeasured.

## Evidence

Go oracle `~/.cache/pb-phase7/editor-oracle/main.go` (bubbles v0.20.0 driven with the same key
sequences; built offline with Go 1.27.1 against pb's module graph) — output
`~/.cache/pb-phase7/tmp/editor-oracle.txt`:

```
limit1 "abcde" 5 / limit2 "abde" 2 / paste "a b  cd" / setvalue 5 0 2
words 9 5 8 "foo  bar " "foo  " "  " / dwf-last PANIC runtime error: index out of range [2] with length 2
password "d" 0 1 / unicode 4 "日本e" / ku "hello" "o" "xo"
scroll "> defgh " "> abcdef" / view "> abc    " "> abc    " "> ***    " "> filter     "
area-default 34 6 400 / area-nl "ab\ncd" 1,2 "abcd" 0,2 "abcd" / area-paste "a    b\n\nc" 2,1
area-limit "日本\nab" 7 / area-words 0,8 1,6 0,7 / area-case "STRAßE ab" "STRAßE Ab" "STRAßE bA"
area-vert 1,2 2,5 / wordwrap "Write your\nqueries\nhere" "abcd\nef"
area-ph "┃  1 Write your     \n┃    queries here   \n┃                   "
area-view "┃  1 select 1       \n┃                   \n┃                   "
area-scroll "┃  4 l4 …" / area-scroll-up "┃  1 l1 …\n┃  2 l2 …"
```

Each value is asserted by a unit test in `editor.rs` (28 tests incl. fault/paste tests;
`clipboard.rs` has a selection-order test). Central build 1 (parent): the lib tests compile and
all editor tests pass; its dead-code warnings were resolved by the API cleanup above (not yet
re-built). Earlier characterization runs (before the central
integration loop) compiled `editor.rs` unchanged in an isolated scratch crate
(`~/.cache/pb-phase7/editor-check`) with stub `input`/`style` modules (plain Ascii rendering,
`unicode-width` for widths): 26/26 tests passed, clippy clean except a stub-only warning. That is
characterization only, NOT integrated parity: real `style` rendering (SGR, `inherit`, go-runewidth
`rune_width`) and the real `input::parse` are pending the central build, which the parent owns.
Fault/paste tests and `clipboard.rs` were added after that run and have not been compiled yet.

Open: differential fuzz (random key sequences, Go vs Rust per-step value/cursor/view) to close F4
and widen coverage; PTY cell comparison of the SQL editor once integrated.

## Pending blink timers and caller routing (login + credential correction, 2026-10-14)

Status: applied to source by the editor/login lane in the authorized edit window. The lane has
not built, formatted or tested it. The parent owns rustfmt, the central build/tests, hashes and
successor controls. A fresh independent review follows. No successor parity is claimed.

This corrects the "blurred → no deadline" design above. That line described the polling API
correctly, but `BlinkCursor::blur` also discarded the cursor's outstanding timer. Pinned bubbles
does not: `cursor.Blur()` only clears focus and shows the plain character, and the
`context.WithTimeout` started by the last `BlinkCmd` stays live. Evidence for the observable
consequence:
* release4 styled login captures and the login dwell controls
  (`docs/evidence/phase7/login-blink-investigation.json`, `login-blink-process-release4.json`);
* bounded Go/release4 credential controls (`docs/evidence/phase7/credential-blink-results.md`).
  Collisions A and B reproduce a single-cell early toggle and re-arm gap; C matches strictly.
  These are pre-fix controls.

The design record, including the earlier proposal stages, is
`docs/evidence/phase7/login-blink-design-r1.md`.

Pinned semantics (bubbles v0.20.0 `cursor.go`/`textinput.go`; Go sources pinned to 07af390,
`docs/evidence/phase7/login-credential-blink-source-pin.json`):
* Every pb cursor has id 0 (`cursor.New` leaves it, and bubbles has no setter). A `BlinkMsg`
  is accepted by whichever focused cursor receives it if the tag equals that cursor's
  `blinkTag`.
* `BlinkCmd` increments `blinkTag` and cancels the cursor's previous timer when it is called
  (one `blinkCtx` shared by all copies of a cursor). So `Focus()` bumps the tag and cancels the
  old timer even when the parent discards its command. Each cursor has at most one live timer.
* `initialBlinkMsg` (`textinput.Blink`), a cursor move and an accepted `BlinkMsg` each call
  `BlinkCmd` while focused. `Blur()` changes neither the timer nor the tag.

Native model: `BlinkCursor` keeps `tag` and `timer: Option<BlinkTimer { due, tag }>`.
* Every arm bumps the tag and replaces the timer, so the timer's tag is always the current one.
* `blur` keeps the timer.
* `disarm_blink` drops it and keeps the tag.

The single `Option` is the bounded live generation per cursor: canceled generations are replaced
and are never queued.

`BlinkCursor` and the `cursor` fields are private to this file, so callers reach this state only
through the methods below. After the change, nothing in `editor.rs` reads `cursor.deadline`.

Caller policies:
1. **Polling** (`focus`, `blur`, `update`, `insert_paste`, `start_blink`, `disarm_blink`,
   `blink_deadline`, `tick`). For the same call sequence it gives the same `hidden`,
   `blink_deadline()` and `tick()` results as before. The old deadline and the new timer differ
   only while blurred; every reader is focus-gated, and `focus()` overwrites the kept timer.
   One limit: `update`/`insert_paste` now sample `Instant::now()` before the edit rather than
   after it, which moves the re-arm earlier by microseconds. Users:
   * the saved menu filter: a single polled input, so there is no cross-input route;
   * the profile picker filter: `focus` + `disarm_blink`, and the picker has no tick because
     Go drops its non-key messages;
   * SQL/PromQL inputs: no blink delivery in Go (SQL Q3);
   * the textarea: no tick API.
2. **Routed: login** (`tui::login`). Go forwards non-key messages to the active step's input
   only. Every focus change discards `Focus()`'s command (+1, nothing armed) and returns
   `textinput.Blink` (+1, armed): **+2 per focus**. Native code:
   * `focus_input` = `focus` + `disarm_blink`;
   * `initial_blink` = `start_blink_at` on the input active at delivery (dropped at selection
     steps);
   * `next_tick` = the earliest `pending_blink` of all five fields;
   * `tick` takes one due timer and calls `deliver_blink` on the active input only.
3. **Routed: credential form** (`tui::profile::CredentialForm`). Go's `updateInputs` hands every
   non-key message to both inputs. The blurred one drops it in `textinput.Update`, and the
   focused one accepts a departed input's same-tag message. Broadcast does not remove the
   collision. The tag counts are:
   * constructor: `Focus()`'s command discarded, then `Init`'s `textinput.Blink`, so **+2**
     (native: `focus` + `disarm_blink` + `start_blink`);
   * navigation onto an input: the `Focus()` command is returned and run, so **+1** (native:
     `focus()`);
   * button focus: both inputs blurred.

   `next_tick` = the earliest `pending_blink` of both inputs. `tick` takes one due timer and
   calls `deliver_blink` on both inputs (the blurred one rejects).

Rules for routed callers:
* take the timer before delivering it;
* deliver only to the inputs the Go parent's `Update` passes non-key messages to;
* mirror the Go disposition of each `Focus()` command (honored: `focus()`; discarded:
  `focus()` + `disarm_blink()`) and of each returned `textinput.Blink` (`start_blink_at` /
  `start_blink`);
* never mix `tick` with take/deliver on one input.

`update_at`, `insert_paste_at` and `start_blink_at` are the instant-explicit forms;
`cursor_visible` is test-only.

Limits, not Go guarantees:
* Login delivers its initial blink synchronously, before the next key; Go uses a command
  goroutine.
* The credential constructor arms at construction rather than at `Init` delivery.
* There is one timer delivery per wake, and timers due together fire in field/input order. Go's
  goroutine order is unspecified, and the existing driver prefers input events.
* Tag equality depends on Go-identical key-message segmentation, because tags count
  cursor-moving messages.
* Cursor ids are not modelled.

Tests, not executed by this lane:
* `editor::tests::input_blink_timer_survives_blur_and_matches_by_tag`;
* login: the dwell-200/650 regressions, several departed timers, refocus cancellation, a
  selection-step drop through `WizardSession`, and paste re-arm;
* credential: collision A (equal-tag acceptance and re-arm); C (stale drop) and B (equal tag)
  plus D (button focus, both dropped); and a refocus test. The refocus test covers only
  cancellation of the user's own timer and stale-tag rejection after Shift+Tab. It does **not**
  exercise an equal-tag collision E.

## Independent R6 scheduler limitation

Go offers blink messages only after the timer expires and its command goroutine reaches the shared message channel; deadline order alone does not determine key/blink delivery order. Native inline drivers prefer a ready input event over a ready timer. Near a collision, a cursor-moving key can replace a due timer before its accepted-delivery tag increment; a navigation key can instead route that departed due tag immediately to the newly focused field. Relative to a Go execution that sends the blink first, either can alter a later cross-field match and the caret's blink phase. Values, requests, persistence and lifecycle are unaffected. The single-input saved filter has no persistent cross-field effect. This is a source-level scheduling limitation: no process control measures the collision window or establishes a numerical upper bound. Timer wake latency and loop work can both affect ordering; no new scheduler policy is implemented.

The earlier claim of a single-loop-iteration bound is superseded; exact elapsed timing is unmeasured. See [R6 review](phase7-review-final-opus-r6.md).
