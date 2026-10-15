# Phase 7 administration and saved SQL

Implementation lane scope: `src/tui/admin.rs`, `src/tui/saved.rs`, `src/admin.rs`, `src/admin_datasets.rs`, `src/saved_queries.rs`, this document. Parent owns module registration, Cargo and SQL application/dispatch. No commits, mutations of other worktrees, benchmarks, profilers or live requests were made by this lane.

Read the current phase7 plan and terminal/network/validation/architecture notes before editing. `git diff 07af390 -- cmd pkg main.go` is empty: actual command/model Go source remains pinned. Added historical Go harness fixtures are the only `.go` differences against the pin. The current worktree starts from merged phase1–5 base `4a812d5`.

## Implementation

Administration keeps its existing `execute` boundary and noninteractive branches. `AdminService` borrows the existing `ApiClient`; role-name preflight reuses `admin_inventory::role_names`. The administration terminal drivers run outside Tokio `block_on`, after preflight and before mutation. The saved driver owns a current-thread runtime to handle shared async clipboard completions and blink timers. The driver owns one shared `TerminalSession`, closes it before returning an owned selection, and does not implement private raw-mode, alternate-screen or panic handling.

Dataset selection clamps logs/metrics/traces in that order; up/k and down/j move, Enter selects, Esc/Ctrl+C return cancellation. Cancellation produces exactly `Dataset creation canceled\n` on stdout and no PUT. `create_selected` shares the existing explicit-type mutation, type/log-source headers, HTTP-200 requirement and unread-success-body release. The explicit-type and deletion logic is unchanged.

Role selection starts at admin; right/left clamp over admin/editor/ingestor/reader/writer when the privilege row is focused. Tab/Enter/down and Shift+Tab/up toggle focus. Enter on Submit finishes; Esc is ignored. Role dispatch uses normalized KeyCode, preserving Alt+Enter/arrows, while dataset dispatch uses Key::name. Ctrl+C produces exactly `aborted by user\n` on stdout after a GET preflight but without PUT. Existing names fail before acquiring terminal. The PUT body is a one-element array containing only `privilege`; role creation reads the entire response even on success, requires HTTP 200, and prints `Added role NAME` without a newline.

Saved selection is an owned `SavedSelection`, with SQL from `desc`, exposed as `sql`; `dataset` is retained for data fidelity and is never executed. `select_menu` rereads persisted configuration and directly selects its persisted default, independently of the parent command's explicitly resolved application profile. Fetch completes before terminal acquisition. `fetch_menu` deliberately ignores HTTP status, shares the existing Go-compatible filter decoding, prints stage-specific errors on stderr and still opens an empty menu after request/body/decode failures. Explicit-output listing continues to check 2xx. No delete state, command or shortcut is introduced.

Saved menu apply/quit interception precedes filter and browsing logic: a/Enter apply the selected item; b/Ctrl+C quit, even while filtering. Shared `TextInput` handles filter editing and character limits, rather than a private editor. The list retains its inherited browsing/filter keybindings and pagination. Rendering uses the shared palette/styles and session renderer. Saved-filter blinking uses shared TextInput deadlines/ticks polled alongside async TerminalSession::next_event. Ctrl+V produces a ReadClipboard effect, spawning shared clipboard::read in an owned JoinSet. Completions insert_paste into the current Editing/focused filter without generation guards; errors are ignored. Every read aborts and joins before quit/error restoration. No private editor, clipboard process policy or terminal mode implementation is used. Inline colors are Color::role (terminal-background adaptive), saved palette colors are active().hex via Color::hex (PB_THEME), matching the oracle distinction.

`SavedSelection::application()` quotes stream names then dotted fields without adding a default LIMIT. Missing ranges become 1h/now. Blank SQL returns None; the parent must print `Empty query selected.\n` for an applied blank selection. The parent distinguishes this from outer None (menu quit), prints exactly `Query: QUERY\n`, and invokes ordinary SQL fetch through `sql::execute_saved`; the wire fetch adds LIMIT. No surrounding newline is added by the menu exit. SQL validation, spinner, result/error presentation and explicit application profile remain parent responsibilities.

## Actual oracle characterization

Loopback HTTP fixtures and independent disposable XDG configs were used with `/home/ajs/.cache/pb-phase7/baselines/pb-go`. Replay and artifacts are in `/home/ajs/.cache/pb-phase7/admin-saved-oracle.py` and `/home/ajs/.cache/pb-phase7/admin-saved-evidence/*.json`; each JSON retains argv, initial/intermediate raw styled frames, stdout/stderr, ordered request bodies/headers, exit and termios restoration. Dimensions are 80×24, truecolor, dark theme, dummy basic credentials. No production server or credentials were used.

The first fixture attempt did not answer Go's package-initialization OSC query and stalled before any UI; those failed fixture outputs were replaced after adding synthetic black-background and cursor-position responses. The inherited harness NO_COLOR=1 was removed in the styled pass. Coalescing `jj` in one PTY read yields one Go Runes key named `jj`, not two `j` keys; the traces case now sends separate key events.

Four dataset cases passed: Esc and Ctrl+C cancellation exit 0, restore termios and emit no request; metrics and traces selections exit 0, restore termios and produce the correct telemetry/log-source headers. Three role cases passed: cancellation makes only the GET and prints stdout abort; ingestor selection makes GET then exactly `PUT role/new` with `[{"privilege":"ingestor"}]`; duplicate-name GET fails with exit 1 and no terminal acquisition/PUT. Successful creation has no final newline.

Seven saved cases passed: status-500 decodable array still applies; filtering-active Enter and a apply; filtering-active b and Ctrl+C quit; malformed body prints `Error unmarshalling response: invalid character 'o' looking for beginning of value\n` and opens an empty menu; applied blank SQL prints `Empty query selected.\n` without POST. All restore termios and exit 0. The original fourteen-case suite was replayed with per-key frame capture and configuration-change tracking: every case leaves configuration unchanged. Applying prints `Query: select "obj.key" from "app-logs"\n` immediately after alt-screen restoration; the subsequent request is SQL from description (the fixture dataset field is deliberately different), with wire `LIMIT 500`, startTime=1h and endTime=now.

## Validation status and remaining gates

Owned rustfmt and diff whitespace checks pass. There are 35 retained Go PTY cases: all restore termios; 26 track configuration changes and none change configuration. The second central compile log has no owned admin/saved errors; compilation of all shared/workflow modules is still incomplete. Focused reducer and real-loopback service tests have been added for selection/cancellation, key precedence while filtering, description SQL, no delete effect, single-element PUT, menu non-2xx decode and printed-vs-wire SQL/ranges. Parent compiles centrally; this lane has not started cargo builds while integration is pending. Rust/Go process comparison and final shared-style/API convergence remain pending, so the oracle results establish Go behavior rather than complete Rust parity.

Corrected reread contract: pinned `ReadConfigFromFile` returns a non-nil empty config on read/parse failure. An actual FIFO-to-malformed-file process control confirms the saved menu prints `Error reading Default Profile`, then `Error creating request: self-hosted profile missing credentials`, then attempts the empty menu. The prior nil-pointer panic claim was incorrect. Rust now continues with the empty config; `tests/saved_menu_reread.rs` synchronizes replacement after the preflight descriptor opens. Evidence is in `docs/evidence/phase7/saved-config-reread/`; the saved-config reread process regression passes in the central source check. Unicode byte-clipping now retains raw bytes through saved-menu layout and terminal emission; the focused release2 saved-title control passes exact bytes, styled cells, cursor and request/config effects. See `docs/evidence/phase7/release2-focused-styled.json`. Additional oracle cases confirmed q/Esc quit, Esc clears a filter, empty-ID/blank-SQL selection is silent, coalesced aa is one unhandled key, down accepts a nonempty filter, equal fuzzy-score titles retain original order, short title log wins the lg ranking fixture, role Alt+Enter submits, and inline colors ignore PB_THEME=light on a dark terminal while saved colors follow it. Successful and failed clipboard utility fixtures also restore termios and exit 0; errors are silently ignored and application remains reachable. A 36×12 saved-menu fixture also restores termios. All five privileges were exercised against the real Go binary: each sends the corresponding one-element privilege-only PUT; Unicode-blank dataset type opens the picker and cancels without mutation. A saved from-only range `then` passes unchanged to the wire with default to=now, confirming parent application must bypass CLI time validation. Rapid input versus asynchronous Go fuzzy-match completion and obscure Unicode folding need additional cases. Terminal signals, blink/cursor, resizing, narrow/large cells and styled comparisons remain shared acceptance gates. No performance or release-readiness claim is made.

## Editor API v2 fault integration

Pinned Go PTY case `saved-alt-d-panic`: open saved list, `/`, type `xy`, Left (cursor on last rune), Alt+D. Go recovers the textinput panic using the terminal kill path, restores termios/alternate screen and writes exactly `Caught panic:\n\nruntime error: index out of range [2] with length 2\n\nRestoring terminal...\n\n` on stdout. The goroutine stack is on stderr. The process exits **0**, configuration is unchanged, and the only request is the initial GET filters. It does **not** execute SQL or trigger a second unrecovered panic: SavedQueryList reads global selections after Run rather than asserting the returned nil model. The fixture's subsequent apply key is not consumed because the command has already exited.

Saved driver now consumes EditorFault immediately after update/paste and after every view, including the final quit view. Update faults become a Fault effect before any further editor access/refiltering. All owned clipboard reads cancel/join, TerminalSession Drop performs the shared kill restoration without a final render, the exact panic text is written on stdout, and Go runtime-specific stack output is omitted consistently with SQL/PromQL (L-EDITOR-4). It returns a successful no-selection result for an update panic. An already selected item survives a hypothetical final-view panic, matching Go's global selection persistence. Go debug.PrintStack is omitted (shared ledger L-EDITOR-4); stderr is not claimed byte-identical on panic. Hang faults park without further input/redraw/completions, as API v2 directs; this variant is unreachable in these TextInput-only owned screens. Dataset/role screens contain no editors and need no fault handling.

An owned reducer test covers the reachable Alt+D fault and exact diagnostic text. No local build was started; parent central compilation/test ownership remains in force. Retained Go PTY case count is now 36, all restoring termios.

## Central clippy follow-up

Reviewed `/home/ajs/.cache/pb-phase7/clippy1.log` against current sources. The three owned diagnostics are in saved.rs: collapsible_match in page navigation, unnecessary_sort_by in fuzzy ranking, and collapsible_if in matcher scoring. Fixed them without lint suppressions: guarded page-navigation arms, stable sort_by_key with Reverse(score), and a short-circuit let chain that consumes the candidate only at the existing boundary. Equal-score source ordering and candidate retention are unchanged. No admin/service findings are present. Owned rustfmt and diff whitespace checks pass; no local build/clippy command was run, so the parent rerun remains required for a clean result.

## Lifecycle review N3/N8

Input-read errors now gracefully close the final view before returning the error, matching Go eventLoop/shutdown(false), including the administration pickers. Saved editor panics omit Rust force_capture; Go runtime stack omission is consistent with SQL/PromQL and remains L-EDITOR-4. Shared Event::Panicked(String) drops the terminal without final render and returns saved-menu success/zero selection after the caught-panic stdout diagnostic. Admin background panic returns a typed panic outcome; the owned command boundary emits the caught diagnostic, then the nil-model assertion message and exit 2 (role.Model or cmd.datasetTypePicker), with no mutation. Admin screens have no asynchronous application effects; this concerns shared reader/writer panic notification. No terminal code or local builds changed.

Background-panic correction: Bubble Tea command-goroutine recovery cancels the program context; Run builds `program was killed: context canceled` (tea.go:595–605). SavedQueryList returns that error before reading global selections, so this path exits 1 rather than the Update/View recovery exit 0. Event::Panicked now kills/drops the restored session, cancels/joins clipboard reads and returns that error without a caught-panic stdout message. Admin selectors return the same program-killed error; no nil-model assertion occurs because their callers check Run's error first. This supersedes the generic-background mapping in the preceding lifecycle note. Runtime hook/Go stack diagnostic differences remain ledgered. Editor Update/View handling is unchanged.

Final background diagnostic disposition: Event::Panicked keeps its payload. After cancel/join and terminal Drop (no final view), saved/admin print profile::caught_panic_stdout(payload), then return `program was killed: context canceled` so the caller exits 1. The deterministic stdout prefix is coordinator-selected because Go command-goroutine recovery's report races Run's error return; the Go callers check that error before selection/assertions. No Rust backtrace is added; runtime stack diagnostics remain ledgered. This supersedes the previous "without a caught-panic stdout message" sentence. Production edits are now held for the parent's final build; measurement collector implementation continues only in its three new owned files. No local builds run.

## Byte-clipped saved labels (candidate replay pending)

The menu now retains clipped UTF-8 bytes through shared style layout and terminal
emission. At 40×20, the actual Go title `saved_` + 22 ASCII `a` characters +
`测试_tail` is cut after the first byte E6 of 测 and followed by an ellipsis.
The styled row/cursor and unchanged selection identity/request expectations are
recorded in `phase7-effects.md`. No broad byte normalization is used. The source
compiles; full emitted-frame acceptance awaits the new immutable candidate.

## Opus final R3 O3-2 resolution (2026-10-14; successor validation pending)

The saved matcher now uses a saved-only native Rust helper,
`src/tui/saved_fuzzy_unicode.rs`, included from saved.rs. It canonicalizes each
SimpleFold orbit to its smallest scalar by binary search and tests category Ll/Lu
through sorted stride ranges. ASCII has direct fast paths. Generated data is
pinned to Go 1.25.4 / Unicode 15.0.0: 1,428 non-ASCII noncanonical fold entries,
163 Ll ranges and 149 Lu ranges. This replaces the incomplete lowercasing table
and Rust's derived Lowercase/Uppercase classification without introducing a
dependency, runtime Go invocation, shared Unicode framework or product CLI.
Scoring, byte-length penalties, stable tie order, filter interception and the
existing synchronous filter behavior are otherwise unchanged. Async Go filter
races remain outside this correction; no generation/staleness guard is added.

Durable generation/oracle sources are
`docs/evidence/phase7/opus-r3-low-unicode-{generate.py,oracle.go.txt}`. Offline
generation guards the module graph, Go unicode sources and actual fuzzy/list
module-cache sources against the recorded hashes. A separate actual-Go scalar
oracle and Python interpretation of the emitted table verify all 1,112,064
Unicode scalars for canonical folding, idempotence and Ll/Lu membership;
regeneration is byte-identical. This executes Go and the verification script,
not Rust. Source hashes, counts and results are in
`opus-r3-low-unicode-generation.json`; actual fuzzy scores and DefaultFilter
rankings are in `opus-r3-low-unicode-controls.json`.

Actual Go PTY filtering/application controls establish both new orbit matches
(`ẛ`/`ṡ`, `ᲀ`/`в`) and three ranking differences (`ªA` versus `QA`, `xⒶ` versus
`Ⓐ`, `xⅠ` versus `Ⅰ`). Distinct SQL descriptions make selection observable in
the printed Query and POST body. Release3 emits no POST for either orbit pair
and executes logs_0 rather than Go's logs_1 in all three category controls.
Nine additional saved controls pass on both binaries: ASCII ranking and ties,
sigma and long-s orbits, dotted-I separation, a/b interception while filtering,
Esc clearing and Down acceptance. All 14 saved processes per binary restore
termios and leave configuration unchanged. Combined with O3-1, Go passes 21/21;
release3 passes 15/21 with exactly the six expected pre-fix failures. Full
records are `opus-r3-low-{go,release3}-process.json`; raw captures live under
`/home/ajs/.cache/pb-phase7/opus-r3-low/` and are hash-bound.

Two owned reducer regressions cover orbit membership/application, exact Go byte
scores and category ranking. Rustfmt passes; they have not been compiled or run.
No Rust build, candidate rebuild, crate test or measurement campaign was run by
this lane. Parent central tests and a hash-bound successor replay remain
required. This resolves the previously unnamed obscure-Unicode gap in source;
successor runtime parity is still pending.

## Opus final R4 O4-1 resolution (successor validation pending)

The saved matcher uses a small native MatchProgress state and an explicit
Result<Option<i64>, MatchFault>. Verified Go quirks are preserved: NUL lookahead
is an apply boundary, the candidate byte survives applies, best_score resets to
-1 and rejects only candidate scores at or below that floor, and title scanning
continues after pattern exhaustion so remaining bytes surface an explicit fault.
Iterator lookahead and Option byte positions replace Go's byte decoder loop and
negative index sentinel. No product Go invocation or new dependency is added.
Matching faults use the existing command-panic cleanup semantics: abort/join
clipboards, drop the terminal without a final view, print caught_panic_stdout,
return "program was killed: context canceled" and exit 1. Editor Update/View faults retain their
separate successful no-selection handling. Key priority, asynchronous completion
policy, stable tie order, Unicode helper, byte rendering and clipboard ownership
are otherwise unchanged.

Actual immutable Go/release4 controls use
`docs/evidence/phase7/opus-r4-saved-process.py`. Ctrl+V invokes a controlled held
xclip helper, whose release delivers exact a/ab/long patterns to the editor.
Typed lowercase a is an application interception control, not a filter input;
uppercase A is an independently reachable NUL crash control. Release4 has nine
observed mismatches across 16 controls: four crash cases instead execute SQL,
two stale-index matches issue no POST, and three overflow ranking cases execute
the wrong saved SQL. Seven controls agree. All 32 primary processes restore
termios and preserve config. Go strict process checks are 14/16: all four crash
cases exit 1, send no POST and emit the exact killed error, but two return before
Caught panic is printed. Two other crash cases emit the exact diagnostic with
PTY CRLF bytes. Bounded retained followups also observe absent diagnostics. No
stdout comparator waiver was introduced; Go command-report timing remains an
explicit race, and the existing deterministic command-panic report is retained.

The actual unmodified sahilm/fuzzy/list oracle has 106 controls, including
contiguous lengths 1 through 64 and complete rankings for four title variants.
A scratch module copy adds observational hooks; returned scores/indexes/ranks
and panic values exactly equal the unmodified oracle. The adjacency accumulator
first wraps at the 40th rune, while that candidate remains positive. The first
rejected candidate is the 43rd rune (byte 42): -2102458851241584145. Go reuses
byte 41 and applies -1, producing total -1051229425620792066. Negative final
match totals at lengths 40-42 remain valid; the floor is not a final-score filter.
Stale NUL membership scores are 8 for ab/a\0x and 6 for abc/a\0x\0y. Exact
scores, rankings and byte positions are in `opus-r4-saved-go-oracle.json`, with
a hash-bound full trace retained under the owned cache.

Three owned reducer regressions cover command-fault/interception distinction,
stale membership/application and exact overflow scores/rankings. A Python
interpretation of the proposed MatchProgress agrees with all 106 actual-Go
controls; this is source/design verification, not execution of Rust. Rustfmt
parsing/format checks pass. This lane ran no Rust build, crate test, candidate
rebuild, profiling or performance campaign. Parent central compilation/tests
and hash-bound successor replay remain required. Source changes do not inherit
release4 acceptance; full styled/race/platform parity remains unestablished.
