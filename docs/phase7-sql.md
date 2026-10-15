# Phase 7 — interactive SQL (`pb sql run -i`)

Owner: SQL sub-agent. Files: `src/tui/sql.rs` (state, reducer, view, driver, entry),
`src/tui/sql_service.rs` (network effects and Go-compatible decoding).
Baseline: Go `07af390` (`cmd/query.go`, `pkg/model/query.go`, `timeinput.go`,
`timerange.go`, `datetime/datetime.go`, `tablekeymap.go`, `textareakeymap.go`), bubbles
v0.20.0 (textarea/textinput/spinner/list), bubble-table v0.19.2, lipgloss v1.0.0,
Go toolchain 1.25.4 `encoding/json`. Statements are source-derived unless they cite oracle
or harness evidence. Historical evidence includes central unit4 (376 pass, 0 fail), the
functional effects suite, release1 styled results and the pinned-Go DST oracle (§4,
L-SQL-1). Newer central byte-frame evidence is scoped in L-SQL-SIDEBAR below; it does not
establish whole-corpus acceptance. Release2 is preserved and is not the final accepted
candidate. The F1 identity correction and its new reducer tests await central execution.

## 1. Shape

- `QueryState` owns all model state; `update(Msg, now) -> Vec<Effect>` is pure (no I/O,
  clock passed in); `view(now) -> Frame` renders with the shared `tui::style` helpers.
  `Frame::Text` holds valid text; `Frame::Bytes` retains raw bytes through the sidebar and
  final full-frame layout. The driver uses `draw_bytes`/`close_bytes` for byte frames.
- `Msg`: `Key`, `Resize`, `SpinnerTick(tag)`, `Window`, `Schema`, `Datasets` completions.
- `Effect`: `FetchWindow(WindowRequest)` (owned snapshot: run ID, window index, final wire
  query, locked UTC range), `FetchSchema(dataset)`, `FetchDatasets`,
  `SpinnerTick{tag, delayed}`, `ReadClipboard`, `Quit`.
- Driver (`drive`): one current-thread Tokio runtime and one message per iteration.
  - Each iteration yields once (`yield_now`), so network tasks and the I/O driver progress
    under continuous input.
  - It reaps finished tasks with `try_join_next`.
  - Completions travel through a bounded channel (capacity 16). A finished task reserves
    capacity before stamping and sending its completion. The driver holds at most one
    completion head.
  - The terminal head is only peeked (`peek_event_stamp`). `next_step` processes the older of
    the two heads (ties go to the terminal) and consumes the terminal event with
    `try_next_event_stamped` only when it wins. With nothing ready, the driver waits on
    `event_ready`, a completion, or a task finishing.
  - Every effect is an owned task. Leaving the loop drops the set, which aborts in-flight HTTP,
    timers and clipboard children.
  - Quit renders the final view, then closes with its text or byte payload (Go renders
    `View()` after `tea.Quit`). The
    runtime shuts down in the background (Go exits without waiting for goroutines).
  - No reducer state is shared.
- `SqlSessionService`: concrete adapter over `ApiClient`. Each Go client-construction site
  (every window fetch, dataset fetch, schema fetch; schema GET + fallback share one) runs on
  `ApiClient::effect_scope()` seeded with the session-start profile, so an OAuth refresh in
  one effect is not observed by another (Go builds `DefaultClient(&profile)` per closure).

Message order. Bubble Tea feeds key messages and command results through one channel in
arrival order, and command goroutines keep running while it renders. The stamped,
one-head-per-source merge above reproduces that. It also gives each source bounded storage and
backpressure: a later Quit or control key is never accepted, nor its reader lane freed, while
an earlier completion is processed. Unit tests
`older_completion_wins_without_accepting_a_later_quit_or_control_key`,
`older_terminal_event_is_taken_before_a_later_completion` and `empty_and_closed_sources`
cover this with a scripted source.

History (kept for context; not a proven root cause): the first driver `select!`ed over two
queues without yielding. In the early effects run (`effects-candidate1.log`), six cases failed,
three of them SQL: `sql_cache_eviction` and `sql_unknown_actual_total` showed window 1 "loading"
from row 457 to 2200, and `sql_stale_schema` processed a datasets completion before queued
keys. Those observations motivated the yield and the ordered merge. The parent's later fixture
investigation found that readiness and concurrent-startup assumptions in the fixtures also
contributed, so the early failures do not prove the driver ordering was their sole cause. The
current driver rests on its own lifecycle rationale (Bubble Tea's single queue, bounded heads,
no premature Quit acceptance) and on the tests above.

## 2. Contract preserved (with source refs)

Entry (`cmd/query.go:79-128`): no query-required check with `-i`; `--from` defaults to
`1h` when not explicitly set (`parsed.explicit("from")`), empty explicit → `1m`; empty
`--to` → `now`; the query passes `quoteStreamNames`, `quoteFieldsWithDots`,
`ensureDefaultLimit` **before** the editor (initial text carries ` LIMIT 500`); then
`--from` parse error `invalid --from value: …`, then `--to`; `-o` and `--save-as` are
ignored. `time.Parse` location is kept: `Z` → UTC, offset equal to Local at that instant →
Local, other offset → fixed zone; durations/`now` → Local. That location drives the
"local" display of result timestamps and the FROM/TO sidebar text.

Windows (`query.go:58-60, 464-805`): 500-row windows, at most 3 cached, LRU eviction that
spares the current window, `lastUsed` bumped per rendered row (clock increments per row, as
Go), unknown total (−1) until a short window, total clamped to the user LIMIT, user OFFSET
added to every window offset, `fetchLimit = min(500, limit − index*500)`, no request past
the limit, in-flight dedupe by index, prefetch of the next window at ≥ 90 % (local offset
≥ 450), `ensureCurrentSQLWindow` on every move, first-window failure vs later-window
failure (`query failed` / `window fetch failed`, footer `· fetch failed`), focus to table
on each successful window. Run ID guard: completions of an older run are dropped — the
only stale guard in this model.

Unguarded completions preserved (no new guards): `schemaMsg` (no dataset name; a late
schema for a previous dataset replaces the column list), `datasetListMsg` (every Ctrl+D
refetches; each completion resets the filtered list). Superseded requests are not aborted
(they run to 50 s / 15 s), matching Go; only session exit drops them.

Network (`query.go:2603-2728, 3041-3093`, `datasets.go:56-113`): window POST
`api/v1/query?fields=true`, body `json.Marshal(map[string]string)` (sorted keys
`endTime, query, startTime`, HTML escaping), 50 s; error text = trimmed body, else
`resp.Status`, else transport `err.Error()`. Decode uses `json.Decoder.Decode` semantics:
reads only until the first complete value (objects/arrays end at their closing byte;
top-level literals need one more byte or EOF), reports a syntax error as soon as the
offending byte arrives, `EOF` for empty/whitespace bodies, `unexpected EOF` for a truncated
value, ignores trailing bytes and never awaits the tail (implemented with a port of
`encoding/json/scanner.go`, heap nesting stack, max depth 10000). Type errors use Go
`UnmarshalTypeError` text, e.g. `json: cannot unmarshal number 1e400 into Go struct field
QueryData.records of type float64` (float64 overflow is rejected as Go does). Schema: GET
`api/v1/logstream/<stream>/schema` (auth headers only, 15 s, `json.Unmarshal` of the whole
body, non-empty names); otherwise fallback `SELECT * FROM '<stream>' LIMIT 1` over
`2000-01-01T00:00:00+00:00` .. now (UTC, sampled after the unsuccessful GET/body/decode,
immediately before the fallback) through
`fetchData`'s quoting/limit passes, 15 s. Datasets: Prism home, `datasetType == "logs"`,
titles sorted. R3-F1 moves only that fallback clock sample into the service;
GET and fallback retain one effect auth scope, a fresh 15-second deadline each, raw
dataset/URL identity, and unguarded schema completion. A successful schema GET returns
without querying. The process regressions `sql_schema_fallback_clock` and
`sql_schema_primary_no_fallback` hold the primary response across a measured later UTC
second, require fallback `endTime` at/after release with timestamp-dependent visible
columns, and retain the successful-primary/no-POST control. Both assert exact request
graphs/auth, exit 0, terminal restoration and unchanged config. See
[evidence](evidence/phase7/schema-fallback-clock-before-fix.json); successor execution
and central Rust validation remain pending.

Cells: JSON strings are kept as text (width inference and `p_timestamp` formatting only
apply to Go strings — numbers are float64 and never match `case int`, so numeric columns
get width `max(2, len(title)) + 1`); other values keep `fmt.Sprintf("%v")` text (shortest
`%g`: `1e+06`, maps with sorted keys, `<nil>`), computed once at decode with
stack-safe recursion (`stacker`), no JSON tree retained.

Keys (`query.go:929-1191`): dataset overlay captures every key, including Ctrl+C;
main view order: Ctrl+L → Ctrl+D → Enter on dataset/columns/time → Tab/Shift+Tab →
columns-drawer Up/Down/Runes/Backspace/Esc; time overlay Esc (discard) / Enter (apply +
run); then Ctrl+R or Alt+Enter (also from the time overlay, **without** applying the
draft); Ctrl+B (closes overlays, otherwise a no-op — editor Ctrl+B "left" never fires);
Ctrl+C quit; else editor / table navigation / table widget / time draft. Table bindings
from `tablekeymap.go` (`up/w`, `down/s`, `shift+up/W/pgup`, `shift+down/S/pgdown`,
`home/ctrl+y`, `end/ctrl+v`, `left/a`, `right/d`, `/`, `esc`). Enter on columns inserts
`"<name>" ` even when the drawer is closed. Selecting a dataset into an empty editor sets
`SELECT * FROM dataset LIMIT 100`; the selection is substituted only at run time
(`resolveDatasetPlaceholder` → `FROM '<name>'`).

Time picker: presets 10m/1h/5h/1d/3d, `SyncPreset` (nearest, first on ties, Go
`Round(time.Second)`), segment editing on `2006-Jan-02 15:04:05` (Up/Down adjust with
`AddDate`/clamped months/hours…, digits replace and reparse in Local, word/home/end
movement), start may not pass end, end may not pass now (instant mode code kept, unused by
SQL), display Local/UTC toggle, `[NOW]` badge within 2 s.

Spinner: `spinner.Line` frames at 100 ms; ticks only while loading; tag check reproduces
`spinner.Update` (a rerun's immediate tick supersedes the pending one).

## 3. Quirks deliberately preserved

| ID | Quirk | Evidence |
|---|---|---|
| Q1 | Editor geometry differs between Update (SetWidth 70 / SetHeight 10, CharLimit 400) and render (copy with SetWidth editorW−6 / SetHeight 8) because `View()` has a value receiver; shared viewport pointer kept via `TextArea::clone`. | query.go:367-371, 1236-1241 |
| Q2 | Initial query longer than 400 cells is truncated by the textarea CharLimit. | bubbles textarea defaultCharLimit |
| Q3 | Cursors never blink: Focus() cmds discarded, BlinkMsg has no Update case. | query.go:385, focusSelected |
| Q4 | Ctrl+V in editor/filters reads the clipboard but the paste result is dropped (QueryModel.Update has no pasteMsg case); the read itself still happens. | query.go Update switch |
| Q5 | Table filter (`/`) is invisible (footer hidden) and never blurs (FilterBlur unbound): once pressed, table keys type into it until restart; filter applies to the current page rows only. | query.go:365, tablekeymap.go:96 |
| Q6 | `overlayColumn` is never assigned; column spotlight unreachable (not represented). | query.go:192, 993 |
| Q7 | Time overlay: Ctrl+B closes without applying; Ctrl+R runs with the old range. | query.go:1146-1165 |
| Q8 | Datasets completion replaces a non-log initial dataset with the first log dataset. | resetInvalidDatasetSelection |
| Q9 | Missing table cells render `—` in Border color; header/cell colors use `ui.Adaptive` (background), panes use `ui.Active` (PB_THEME). | query.go:66-93 vs View |

## 4. Divergence / risk ledger (proposals or unknowns; none silently fixed)

- **L-SQL-1 (resolved, preserve):** wall times that DST skips or repeats, reached through
  digit edits (`ParseInLocation` in Local) and `AddDate`/clamped-month adjustments. Both go
  through go1.25 `time.Date` (time.go:1751-1763). That takes the offset at the wall time read
  as UTC; if it is non-zero and the corrected instant leaves that zone period, it re-looks up
  the offset at the corrected instant. `go_date_utc` reproduces this using chrono's Local
  offsets, and replaces the earlier "offset 12 h earlier" approximation. That approximation
  was visibly wrong:
  - New York skipped 2024-03-10 02:30: Go gives 06:30Z (01:30 EST); the approximation gave
    07:30Z (03:30 EDT).
  - Berlin repeated 2024-10-27 02:30: Go gives 01:30Z (the later CET occurrence); chrono's
    earliest gave 00:30Z.
  The other cases are: New York repeated 2024-11-03 01:30 → 05:30Z (EDT); Berlin skipped
  2024-03-31 02:30 → 01:30Z (03:30 CEST); London repeated 2024-10-27 01:30 → 01:30Z (offset 0
  at the wall time, so it is kept). Unit test: `go_date_resolves_skipped_and_repeated_wall_times`.
  [oracle] The pinned Go toolchain with real tzdata confirms all five exact results:
  `docs/evidence/phase7/sql-dst-oracle-main.go.txt` and
  `docs/evidence/phase7/sql-dst-oracle-results.txt`.
- **L-SQL-2 (internal, no visible change intended):** session exit aborts in-flight HTTP;
  Go process exit closes them implicitly.
- **L-SQL-3 (preserve, partly irreproducible):** editor faults (`EditorFault`) follow
  Bubble Tea `recoverFromPanic`: terminal restored without final render, stdout
  `Caught panic:\n\n<value>\n\nRestoring terminal...\n\n`, command returns success (Go
  `p.Run()` returns `nil, nil`; cmd/query.go returns the nil error). Go's
  `debug.PrintStack()` goroutine dump on stderr is not reproduced. Reachable: Alt+D /
  Alt+Delete on the last rune of the dataset filter or table filter; Ctrl+Left / Alt+B at
  (0,0) of an empty or whitespace-led editor = **hang** (input, completions and SIGINT are no
  longer processed; only SIGKILL/SIGQUIT end it) — preserved per L-EDITOR-2.
  The same Update/View outcome covers `ui.Truncate`'s reachable wide-rune slice panic. The
  sidebar dataset name, column names, column-filter text and pane titles go through the shared
  `style::ui_truncate -> Result<String, GoPanic>`. The panic is recorded (`RenderPanics`), the
  frame is not drawn, and the run exits 0 with the report.
  A command-goroutine panic is different, and it depends on the command:
  - **Window fetch:** Go's `NewSQLWindowFetchTask` defers its own `recover()`
    (query.go:2612-2617), which turns any panic into a failed window with
    `errMsg = "query failed"` for the same run and window, and the program continues.
    `recover_window` reproduces this. The fetch runs inside `terminal::recovered` in an inner
    `JoinSet` owned by the window task (aborting the task aborts the fetch). A panic
    `JoinError` maps to `WindowCompletion { run_id, window_index, Err("query failed") }`; a
    cancelled fetch sends nothing. Test:
    `window_fetch_panic_becomes_query_failed_for_the_same_window` (injected panic; run and
    window IDs kept; the reducer shows "query failed"). Final review F2. No Go-reachable panic
    is known on this path; the recover covers native panics.
  - **Schema and dataset fetches** (`fetchStreamSchema`, `fetchAllStreams`): Go has no
    recover there. A native panic arrives as `Event::Panicked(value)`, after the terminal
    owner's hook has restored the terminal. Bubble Tea's per-command `recoverFromPanic` prints
    the same stdout report and kills the program.
  `Run` then returns `ErrProgramKilled` wrapped with the context error (tea.go:598), and
  cmd/query.go returns it: error `program was killed: context canceled`, exit 1, no final
  view. The auth and PromQL owners confirmed this independently. Native panic diagnostics on
  stderr are runtime-specific and scoped by the terminal owner (lifecycle review N1).
- **L-SQL-4 (implemented, preserve Go; display parity pending a control):** Go runs
  `strings.Index(strings.ToLower(sql), " from ")` (and `"\nfrom "` in `extractDataset`), then
  slices the ORIGINAL `sql` bytes with that offset. Rust reproduces this (`go_lower_index`,
  `go_slice_from`, `go_trim_space`).
  - **Lowering:** `go_lower_rune` applies Go's simple lowercase. U+0130 maps to 'i'.
    U+A7CB and U+A7DC stay unchanged: Rust's newer tables lowercase them to U+0264 / U+019B
    (3→2 bytes), but go1.25.4 does not. Every other rune takes Rust's single-rune
    `to_lowercase`.
    [evidence] The parent's exhaustive probe over all 1,112,064 scalars found these two as the
    only mappings that change byte width or produce an ASCII result and are absent from pinned
    Go (153 Go rows): `docs/evidence/phase7/sql-lower-map/{report.json,go.txt,rust.txt,main.*.txt}`.
    Shifting examples: U+212A KELVIN SIGN 3→1 byte, U+0130 'İ' 2→1, U+023A 'Ⱥ' 2→3.
  - **Bytes:** slicing is byte-exact, including cuts inside a rune. `TrimSpace` stops at an
    invalid byte. Past the end, `[low:len]` becomes Go's `runtime error: slice bounds out of
    range [low:len]`.
  - **Panic outcomes by site:**
    - In View (empty dataset → sidebar `extractDataset`) or Update (Ctrl+L drawer, run via
      `resolveDatasetPlaceholder`), `tea.Program.Run` recovers it: no final view, stdout
      "Caught panic", exit 0.
    - In `NewQueryModel` (the CLI query, before `Run`) it is unrecovered:
      `new_model_preflight` runs before the terminal opens. stderr gets
      `panic: <value>\n\ngoroutine 1 [running]:\n`, and `execute` returns `Ok(2)` for the
      caller's exit status. The goroutine dump is not reproduced.
    - `execute` now returns `Result<i32, CliError>`; a normal run returns `Ok(0)`.
  - **Raw bytes are preserved where they are externally visible:**
    - `extract_dataset_bytes` keeps Go's bytes. `QueryState` carries them in the
      `dataset_raw` sidecar while the extracted name is current; choosing a valid title or
      replacing the selection clears it. Dataset-list matching, invalid-selection reset and
      picker change detection compare `candidate.as_bytes()` with the borrowed
      `dataset_identity_bytes()` slice. Its fallback is the valid `dataset` String's bytes;
      a replacement-decoded companion is never used as identity while the sidecar exists.
      Schema effects retain their owned `Vec<u8>` snapshots via `dataset_bytes()`.
    - `Effect::FetchSchema` carries `Vec<u8>`. The schema URL is built by `schema_endpoint`,
      which escapes each invalid byte as `%XX` as Go's `url.JoinPath`/`EscapedPath` does
      (test `schema_endpoint_escapes_raw_invalid_bytes_like_go`: `%B5%8B%E8%AF%95`). Invalid
      bytes travel through the shared byte-path joiner without Unicode placeholder substitution;
      literal private-use characters stay intact.
  - **JSON request bodies:** the run query (the dataset substituted by
    `resolveDatasetPlaceholder`) and the schema-fallback query carry U+FFFD where Go's
    `json.Marshal` writes the escape `\ufffd` per invalid byte. The decoded strings are
    identical. Both the functional effects suite and styled harness `request_contract` compare
    decoded JSON, so this lexical difference is within the existing explicit policy. Raw captures
    remain preserved; no byte-identity claim is made for JSON serialization.
  - **Historical display control:** `docs/evidence/phase7/sql-invalid-byte-display.json`
    compares unformatted raw bytes with replacement characters in a pure observer. It does
    not establish formatted sidebar parity: the later actual Go process found a wrap/layout
    difference. Current byte-frame source and bounded release2 evidence are described below.

  - **Examples (tests `go_lowering_*`, `extracted_raw_dataset_bytes_reach_the_schema_effect`):**
    - KELVIN: `select 'K' as k from dataset limit 5` → no substitution, `extract` = `m`.
    - 'İ': `...from'logs'`.
    - 'Ⱥ': no substitution, `extract` = `ataset`.
    - A mid-rune cut gives raw `B5 8B 试` (display `\u{fffd}\u{fffd}试`).
    - `select 'Ⱥ' from ` → panic `[18:17]`.
    - U+A7CB is left unchanged.
  - [oracle] The pinned Go source matches all 7 cases:
    `docs/evidence/phase7/sql-lower-oracle-{test.go.txt,results.txt}`.
- **L-SQL-SIDEBAR (byte-frame path implemented; bounded central control passed):**
  historical `sql-invalid-byte-actual-display.json` and
  `sql-invalid-byte-candidate-display.json` established the pre-fix sidebar mismatch.
  Go preserves `B5 8B E8 AF 95` through `ui.Truncate` and pane layout; x/ansi width is 2.
  The final positive-width Wrap emits `B5 C2 8B E8 AF 95`, adding one wrap unit and moving
  the right border to the next row. The historical candidate decoded early to U+FFFD
  U+FFFD `试`, width 4, and missed this layout change. Those captures remain historical
  evidence, not observations of the current byte implementation.
  - **Current source:** raw sidebar values use `ui_truncate_bytes`, byte style rendering,
    byte joins and `pane_body_lines_bytes`; `Frame::Bytes` carries the result through final
    Width rendering to `draw_bytes` and `close_bytes`. Reflow's rune replacement happens
    only when its Go clipping branch is reached. Ordinary text frames retain their text path.
  - **No sentinels were implemented.** The earlier SQL-only private-CSI/placeholder proposal
    was superseded by the shared byte-capable style, frame and terminal path. Current layout
    measures and copies the actual bytes; it performs no sentinel substitution.
  - **Central evidence read, not rerun here:**
    `docs/evidence/phase7/release2-focused-styled.json` records the bounded sidebar
    Go/release2 process control passing with zero cell differences and equal cursors for
    actual reads and one-chunk replay. Normalization covers only exact fixture-URL port
    digits. This does not establish arbitrary read-split, terminal, theme or whole-corpus
    parity. Release2 predates the F1 correction and remains a preserved candidate.
  - **F1 identity correction:** the raw extracted name `B5 8B E8 AF 95` differs from the
    valid UTF-8 title U+FFFD U+FFFD `试`. Matching and picker change detection now use the
    raw identity slice when present. A late `[logs, replacement-title]` completion selects
    index 0, clears the sidecar and requests `logs` schema when columns are idle/empty.
    Picker Enter on the valid replacement title takes the existing changed-selection branch:
    clears the sidecar, selected column, column filter and both column lists, then requests
    the valid title's schema even if the previous columns were populated.
  - **Executed Go-only characterization:**
    `docs/evidence/phase7/sql-byte-identity-go-process.json` records one bounded actual
    pinned `07af390` process using existing fixture controls. The initial list response is
    held; editing `select 'Ⱥ' from 测试` and reopening columns requests the raw-byte schema.
    An empty schema fallback completes, confirmed by a subsequent `no columns` frame.
    Releasing `[logs, replacement-title]` then produces a `logs` schema GET and displays
    `LOGS_SELECTED_SCHEMA`. Exit 0, config unchanged and terminal restored are asserted.
    This confirms Go request identity and causal completion, not candidate behavior or full
    styled-frame parity. The pinned model source independently confirms all three byte-string
    comparisons, including picker Enter. Reproducer and raw captures are under
    `/home/ajs/.cache/pb-phase7/sql-byte-identity/`.
  - **Central pre-fix release2 regression evidence, read not executed here:**
    `docs/evidence/phase7/sql-byte-identity-release2-pre-fix.json` preserves the
    coordinator's separate immutable-release2 process failure. After the same held list
    completes, release2 issues a second raw-byte schema GET and fallback POST instead of
    `logs/schema`; the expected `logs` request never appears. The failed trace, stdout,
    stderr and adapted probe remain under
    `/home/ajs/.cache/pb-phase7/sql-byte-identity-release2/`. This is evidence of the
    pre-F1 defect, not a result for the corrected source or successor candidate.
  - **Durable parameterized process runner:**
    `docs/evidence/phase7/sql-byte-identity-process.py` loads the embedded HTTP/PTY fixture
    from `tests/interactive_effects.rs`; it requires no private-cache module, builds or Rust
    test execution. Pass `--binary`, `--role go|candidate`, `--out` and optionally
    `--expect-sha256`. Each invocation asserts the characterized `logs` request, preserves
    successful/failed requests and raw captures, and checks the binary hash before/after.
    Run it with a bounded wrapper, e.g. `timeout 40s python3
    docs/evidence/phase7/sql-byte-identity-process.py --binary GO_BINARY --role go
    --out /home/ajs/.cache/pb-phase7/sql-byte-identity/durable-go`. It sets all three tmp
    variables to `/home/ajs/.cache/pb-phase7/tmp`. Successor candidate execution remains
    centrally owned; this F1 owner runs Go only.
    The durable runner's Go-only execution also passed (exit 0, unchanged binary/config,
    terminal restored); `docs/evidence/phase7/sql-byte-identity-durable-go-process.json`
    retains its fixture hashes, request trace and raw capture paths. No successor result is
    claimed here.
  - **New reducer regressions, not executed by this owner:**
    `late_dataset_list_does_not_alias_a_raw_name_to_its_replacement_title` verifies the
    idle/empty-column completion path and owned schema bytes `logs`.
    `dataset_picker_replacement_title_changes_raw_identity_and_resets_columns` verifies
    clearing populated raw schema/filter/selection state and requesting valid-title bytes;
    selecting that valid title again with populated columns keeps them and sends no schema
    effect. Builds, tests and candidate runs remain centrally owned.
- **L-SQL-5 (runtime unknown):** Prism dataset error text comes from `datasets::fetch_home`
  (`CliError.message`); exact equality with Go `err.Error()` for every failure class needs
  an oracle fixture.
- **L-SQL-6 (proposal, not applied):** stale schema/dataset suppression and aborting
  superseded windows.

## 4a. Text clipping (release1 `p7-sql-wide-dataset-truncation-dark`)

Go clips pane bodies, time-picker rows and table cells with `muesli/reflow` v0.3.0
`truncate.String(WithTail)`, not `x/ansi`. That clipper stops at the first rune that does not
fit and drops the rest. So the NUL fill that `ui.Truncate` takes from Go's zeroed rune buffer
(a 20-rune CJK title at 33 cells) never reaches the terminal. Using `x/ansi`-style clipping
leaked 12 NULs and overflowed the sidebar line. `sql.rs` now has a port of reflow:
`pub(crate) fn reflow_truncate(text: &str, width: usize, tail: &str) -> String` and
`pub(crate) fn reflow_width(text: &str) -> usize`.
- Widths come from go-runewidth via `style::bubbles_rune_width`.
- Terminators are `'@'..='Z' | 'a'..='z'`.
- `ansi.Writer` buffering is kept: an unterminated sequence is never emitted, and a nested ESC
  extends the open sequence.
- `ResetAnsi` is emitted after the tail only when a non-reset SGR was recorded and no reset
  was seen since.

All 7 SQL clip sites use it. The tests are `reflow_truncate_drops_zero_width_fill_after_the_cut`,
`reflow_terminators_include_at_sign`, `reflow_drops_unterminated_sequences`,
`reflow_nested_escape_extends_the_open_sequence` and
`reflow_reset_follows_tail_only_for_an_open_sgr`.

Third-party notice: the helper ports github.com/muesli/reflow v0.3.0 (MIT License,
Copyright (c) 2019 Christian Muehlhaeuser). The full license text is kept in the source comment
above `reflow_width`/`reflow_truncate` in `src/tui/sql.rs`.

## 5. Tests

Reducer/unit (in-file, `cargo test --lib tui::sql`): init effects and wire window;
empty-query idle; LIMIT/OFFSET window plan incl. partial remaining limit; stale run drop;
90 % prefetch, in-flight dedupe, ≤ 3 cached windows sparing current; first vs later window
failure and short-window total; Ctrl+D/Ctrl+B precedence over editor bindings; dataset
overlay swallowing Ctrl+C and placeholder insertion + run-time substitution; late schema
applied unguarded; time overlay Esc/Enter/apply-and-run; Ctrl+R in overlay without
applying; spinner tag; column drawer filter + insertion; Go helper parity (plan, inject,
placeholder, extract, field quoting, timestamps, datetime segments, CLI time location,
column order/widths). Service: first-value decoder (stops before held tail, early syntax
error, EOF/unexpected EOF, top-level literals), `%v` formatting, request body bytes, Go type
error texts incl. float overflow, depth 9000 stack safety, depth 10001 error, Arrow schema.

Historical status: central unit4 passed (376 pass, 0 fail); its earlier pending-rebuild
statement is superseded by later central checks. The byte-integration checkpoint
(`docs/evidence/phase7/source-byte-integration-checkpoint.json`) records 424 library passes,
clean Clippy, 426 diagnostic passes/1 ignored and a saved-config reread pass. That checkpoint's
all-target run was incomplete because of the reported quit-before-schema fixture race;
it is not an all-target acceptance claim. The coordinator subsequently reports pre-F1
round11 passing format/Clippy, 545 all-target tests, four OAuth controls and all four real
15/30/50/120-second deadlines, with production source unchanged through completion. These
are centrally executed pre-F1 results; the coordinator owns validation of the correction.
This F1 owner added the two identity regressions above but executed no Rust tests, crate
builds, candidate runs or timing campaigns. Source stayed frozen while the patch was
prepared; application waited for the coordinator's explicit SQL edit window.

PTY oracle scenarios (parent/effects harness; Go baseline `baselines/pb-go`, fixed 120×40,
TZ fixed, fixture HTTP with recorded requests):
1. `sql run -i "select * from logs"`: request order (datasets, window 0), body bytes,
   `fields=true`, first frame cells, Ctrl+C exit 0, restore.
2. 1200-row fixture: End → window 2 request `LIMIT 500 OFFSET 1000`; scroll to 450 →
   prefetch; footer `rows n/N · loading`.
3. Delayed window 0 of run 1 after Ctrl+R (run 2): screen shows run 2 only.
4. Delayed schema for dataset A after selecting B: column list shows A's schema (Go).
5. Empty editor + Ctrl+D + Enter: editor text, run query `FROM 'name'`.
6. Time overlay: Down, Enter → window start; Ctrl+R in overlay → old start.
7. Window 0 HTTP 500 body/empty body/garbage JSON/held-open tail after object.
8. Ctrl+V in editor (clipboard tool present/absent): nothing inserted.
9. Alt+D at end of dataset filter → "Caught panic" stdout, exit 0 (stderr dump differs).
10. Resize small/large, PB_THEME=light vs dark, Unicode cell widths.
