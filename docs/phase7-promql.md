# Phase 7: interactive PromQL (`pb promql run -i`)

Baseline: Go `07af390` (`cmd/promql.go`, `pkg/model/promql.go`, `query.go`,
`timeinput.go`, `timerange.go`, `datetime/datetime.go`, `pkg/ui/{theme,chrome}.go`),
bubble-table v0.19.2, ntcharts v0.5.1, lipgloss v1.0.0, bubbles v0.20.0,
bubbletea v1.2.2 (all read from the module cache).

Owned files: `src/tui/promql.rs` (state, reducer, effects, rendering, runner)
and `src/tui/promql_service.rs` (concrete network service). Shared modules come
from the terminal/editor/style owners. Dispatch from `metrics::execute` belongs
to the parent.

Evidence status: this agent has no shell; the parent runs every command.
- Unit tests compile and pass in the central runs (unit4: 376/376, 0 failed).
- The parent's process/PTY harness `tests/interactive_effects.rs` exercises these cases
  against Go and Rust: `promql_result_policy`, `promql_builder_policy`, `promql_stale_result`,
  `promql_builder_metric_guard`, `promql_builder_label_guard`, `promql_dataset_cache_guard`
  and `promql_mode_transitions`, plus the opt-in `deadline_promql_120s` and
  `oauth_promql_both_effects`.
- The six failures in the early exploratory effects runs (including label_guard,
  dataset_cache_guard and mode_transitions) are **not** attributed to message order. Those
  runs used a binary that was still changing, and they assumed fixture readiness that the
  harness had not established. They therefore don't show that the stamped peek-then-accept
  merge fixed anything.
  - The parent later revised the fixture controls against actual Go, which validated
    readiness and the allowed startup request graphs.
  - The merge (see Message order below) has its own justification: it follows Bubble Tea's
    single `p.msgs` FIFO in the source. It does not depend on those runs.
- Immutable release1 passes all 34 ordinary, 4 OAuth and 4 genuine-deadline effects cases,
  which includes every PromQL case.
  - In the full styled corpus of 178 cases, the only reported issues are 2 SQL CJK cases and
    1 spinner case, none of them PromQL.
  - Every PromQL gate case in the current corpus passes, including the View panic and the
    double-TERM hang.
- The G4/G7/G10/G3 PTY fixtures have passed against actual Go (parent's
  `oracle-promql-gaps-2/report.json`).
  - G4 runs at 140x35, because at 100x30 the sidebar clips `UTC+00:00` to `UTC+00:`.
  - Their candidate replays against the post-fix source are **pending**.
- No benchmarks yet (G8).

## Structure

- `PromqlState` holds owned screen state. `update(Message) -> Vec<Effect>` is
  pure apart from the injected clock. `view() -> String` renders the frame as
  ANSI text through `tui::style`.
- A `Message` is a `Key`, a `Resize` or a `Done(Completion)`. Each `Effect`
  maps 1:1 to a Go `tea.Cmd`:
  - `Tick` / `TickAfter` (spinner)
  - `FetchResult` (an owned `ResultRequest` snapshot)
  - `FetchDatasets`
  - `FetchCacheMetrics`
  - `FetchLabels` / `FetchValues`
  - `Cancel { id }` (context.CancelFunc)
  - `ReadClipboard`
  - `Quit`
- The runner (`run_session`) owns `TerminalSession`, a current-thread Tokio
  runtime and `Tasks`. `Tasks` is a `JoinSet` plus a bounded mpsc of 64 with
  awaited sends (backpressure, no drops) and a watch-channel cancel per builder
  request. Effects spawn in Go batch order. Dropping `Tasks` on exit aborts
  everything that is still in flight. The final view is rendered and the
  session closed before `execute` returns.
- **Message order.** Bubble Tea delivers keys and command results through one FIFO channel
  (`p.msgs`). The runner compares one stamped head per source:
  - a terminal event is stamped when the reader pushes it onto the terminal queue;
  - a completion is stamped at handoff in `Outbox`, when its task has reserved channel
    capacity.
  The runner always processes the earlier head.
  - The terminal head is only *peeked* (`peek_event_stamp` / `event_ready`). It is accepted
    with `try_next_event_stamped` only when it wins. So a Quit or control key is not consumed,
    and the reader's one pending slot and the signal dispositions are not released, while an
    earlier completion is still processed.
  - Completions keep one head (`Inbox.done`); the rest stays in the 64-slot channel. No local
    queue grows.
  Each step starts with `yield_now`, so request and timer tasks progress on the current-thread
  runtime under continuous input. Finished `JoinSet` handles are reaped every iteration. There
  is still one View per Update (the textarea viewport is shared through View copies). No new
  stale guard or abort was added.
- **Resource ledger** (lifecycle review r2). Everything runs on one current-thread Tokio
  runtime owned by `execute`, so there are no worker threads; the terminal reader is owned by
  the terminal module.
  - Held at once: one completion head, at most 64 queued completions, one peeked (not
    accepted) terminal event, and one task per in-flight effect in the `JoinSet`.
  - When the channel is full, a finishing task waits inside `reserve()` without stamping or
    dropping its completion. It is stamped only once it gets capacity, which is when Go's
    goroutine would complete its send on `p.msgs`. Nothing else buffers it.
  - Each builder request has one watch-channel cancel, removed on completion or `Cancel`.
  - Exit drops `Tasks` (aborting every task, including a sender blocked on a full channel) and
    then the session. Spinner ticks are ordinary tasks (a 100 ms sleep), not a separate timer.
- **Panics.**
  - Update/View editor faults follow Bubble Tea `recoverFromPanic` on the event-loop
    goroutine: kill, `Caught panic:` on stdout, exit 0.
  - A panicked command task (JoinError) or `Event::Panicked` follows the command-goroutine
    path: kill, `Caught panic:`, then `program was killed: context canceled`, because Run
    returns ErrProgramKilled.
  - The result task recovers its own panic into `panic: <value>` (promql.go:2114-2119) and
    keeps running, using the terminal `recovered` scope (P2).
  - Go's stack trace on stderr, and its print racing Run's return, are recorded as gap G6.
- `PromqlSessionService` keeps the session-start `ApiClient`. Every Go client
  construction site gets its own `ApiClient::effect_scope()` (review S1):
  - each result request (the range and instant halves of `both` separately)
  - the metric-name prefetch
  - the Prism dataset list
  - each builder GET

## Network contract (matches Go)

| Operation | Endpoint | Transport/deadline | Errors |
| --- | --- | --- | --- |
| Result | `prometheus/api/v1/query_range` (`start,end,step`) or `/query` (`time`), plus `query,stream` | metrics transport (HTTP/1 only), 120 s | `HTTP <status line>: <trimmed body or status line>` for anything other than 200; reset → `server reset the connection — query timed out`; `failed to parse response: <json error>`; `<errorType>: <error>` |
| Both mode | range first (a non-increasing window becomes `[end-1h, end]`), then instant; table rows from instant, chart rows from range | as above | first failure wins |
| Metric prefetch | `prometheus/api/v1/label/__name__/values?stream=` | as result | the JSON error text without a prefix |
| Datasets | Prism home, filtered to `metrics`, title order | 15 s | `could not load datasets: …` |
| Builder labels/values | `labels` / `label/<PathEscape>/values` with `stream,start,end,match[]` (hand-built query, QueryEscape) | standard transport, 30 s, cancelable | a canceled request produces no message, but a response that already completed is still delivered (biased select) |

## Behavior preserved deliberately (no silent fixes)

- **No stale guard on results.** Completions apply in arrival order. A slow
  earlier run can replace a later one (test
  `results_have_no_stale_guard_and_arrive_in_completion_order`).
- **Builder guards kept.**
  - Labels and values completions always fill the cache. They update the
    lists only if the metric (and label) still match.
  - The prefetch applies only when its dataset is still the current dataset.
  - If the prefetch fails while the builder is waiting, the builder stays on
    `loading...` forever.
- `builderMetricsMsg` and the `(any)` values sentinel are unreachable in Go,
  so they are not modeled.
- `builderFilter.SetValue("")` does not refilter. The filtered lists persist
  after a column change.
- **Key precedence.** The full order:
  1. Global keys (no overlay): Ctrl+D, Ctrl+B, Ctrl+Q.
  2. The builder, dataset and time overlays. Ctrl+C does not quit inside any
     of them.
  3. Tab / Shift+Tab.
  4. Down/Up between the dataset and step panes.
  5. Enter on dataset, time or builder query. Alt+Enter on the dataset pane
     opens the picker.
  6. Ctrl+R or Alt+Enter to run. An empty dataset opens the picker with
     `select a dataset first`. The flag default `select-dataset` is not empty.
  7. Ctrl+@ / "ctrl+space" on the step pane: range → instant (end = now−1h,
     table view) → both (start = end + preset) → range (end = now).
  8. `t` on the table pane.
  9. `w` on the table pane in table view only. In chart view `w` reaches the
     table key map (row up).
  10. In chart view: Left/`[`, Right/`]`.
  11. Ctrl+C quits.
  12. Anything else goes to the focused editor.
- **Resize sets the VALUE column to the width of `VALUE`** (5 cells) until the
  next result (`updateTableColumns(0, 0)`).
- **Table filter.** `/` focuses bubble-table's filter input, which never blurs
  (its FilterBlur binding is empty). After that, every table key edits the
  invisible filter and resets the cursor to page one. Metric and value become
  filterable only after the first result.
- **Two page sizes.** The Update model's page size comes from resize, and the
  View recomputes its own page size on a copy, exactly as Go does.
- **Editor sizing.** The textarea is resized on the View copy, and the
  viewport is shared, as in Go. Go order: max height/width 0, height 1, no
  line numbers, EOB ' ', placeholder, pb keymap, prompt "  ", 400-cell limit.
- **No cursor blink.** PromqlModel routes no blink messages, so the editor
  cursors never blink.
- **Ctrl+V** in any focused editor (query, step, spotlight, builder filter,
  table filter) starts a clipboard read. The `pasteMsg` result is dropped,
  because PromqlModel has no case for it.
- **Spinner.** A Line spinner at 100 ms, tag-guarded. It advances only while
  loading.
- **Status error** is hidden while loading. The View clears it on its copy.
- **Editor faults** (`EditorFault`):
  - Panic: kill-restore with no final render. Stdout gets
    `Caught panic:\n\n<msg>\n\nRestoring terminal...\n\n` and the command
    exits 0. Bubble Tea v1.2.2 `recoverFromPanic` recovers, and `Run` returns
    `(nil, nil)`.
  - Hang: the runner stops forever with the session open, as Go stays stuck
    inside Update.
  - Both are explicit in `editor_fault`. The goroutine stack that Go prints to
    stderr is not reproduced (gap G6).

## Rendering ports

- **Layout.** Go composition is mirrored string for string: panes,
  `paneRule`/`paneBodyLines`, sidebar, bottom key grid, spotlight, builder,
  time picker and results pane. `ui.Active` colors are fixed `Color::active`
  values. Table header/rows and filter prompt/placeholder use `ui.Adaptive`
  (`Color::role`).
- **Table.** The bubble-table border styles (custom border, single-row vs
  multi, last-row borders, no inherited padding, `limitStr`) are built
  explicitly as the result of bubble-table's `Inherit` chain.
- **Chart.** The ntcharts timeserieslinechart subset is ported as a canvas.
  - The X view starts at now..now+1s and moves to [first, last] only when that
    range is non-empty.
  - Graph size and origin follow from the Y label widths.
  - Axes, Y labels and the X label row (8 labels) are drawn.
  - Series lines use Bresenham, ThinLineStyle and Go's line combination rules.
  - Points are bucketed per column, and the cursor line and point are drawn.
  - The nice Y scale and the compact value format are ported, along with the
    legend (+N more), inspector, title and the palette plus HSL colors for
    index 12 and up.
- **Values.** Go `%v` for floats (`'g'`, shortest, exponent ≥ 6 or < −4).
  `strconv.ParseFloat` is ported for chart values: specials, `underscoreOK`,
  hex floats through exact `readFloat`/`atofHex` (G5), and ±Inf overflow treated as
  failure. `formatCompactChartValue` prints `%.1f` specials as Go does
  (`+Inf`, `-Inf`, `NaN`).
- **Go compatibility helpers** (BSD notice; `src/LICENSE-Go.txt`):
  - `go_sort::{slice, slice_stable}`: Go 1.25.4 `sort.Slice` / `sort.SliceStable` (G10).
  - `go_format_unix`: `time.Unix` plus `absSec`/`absDays` formatting (G7).
  - `go_f64_to_i64`: Go `int64(f)` (G7).
  - `parse_hex_float`/`atof_hex` (G5).
- **Clipping.** query.go's `paneBodyLines`, timeinput's display row, input boxes and
  `timePickerJoin`, and bubble-table `limitStr` use muesli/reflow `truncate.StringWithTail`.
  They call the SQL owner's shared `sql::reflow_truncate`, so text after the cut is dropped
  as in Go.

## Tests (in `promql.rs` / `promql_service.rs`)

These are reducer and effect tests for key precedence, overlays, builder
guards, cancellation, mode cycling, missing-dataset run, Alt+Enter, resize
value width, clipboard, spinner tags and the chart cursor. Pure tests cover:
- step grammar/resolution
- range window
- `%v` floats and compact values
- ParseFloat
- labels/HMS/expressions
- the nice Y scale (values derived from the Go formula)
- line drawing
- the time picker
- month arithmetic
- view smoke tests
- service decoding, escapes and error text
- gap regressions:
  - `cli_time_location_is_kept_through_the_picker` (G4)
  - `equal_time_points_follow_go_pdqsort_order` and
    `go_sort_ports_sort_large_inputs_and_keep_slice_stable_stable` (G10)
  - `float_to_int_follows_go_on_this_target` and
    `numeric_timestamps_format_through_go_time_arithmetic` (G7)
  - `go_value_formatting` with `+InfM` (G7)
  - `hex_floats_round_exactly_like_atof_hex` (G5)

## Contract gaps G3/G4/G5/G7/G10: proofs, fixtures and fixes (2026-10-14)

These gaps are not waived and none is accepted. Each section below gives the source proof,
whether the gap is visible, the Go fixture and the fix.
- The parent ran six actual-Go PTY gap cases (`oracle-promql-gaps-2/report.json`), and all
  pass against Go: G4 Z and fixed zone at 140x35, G10 first/second Right, G7 +Inf plot and
  huge timestamp (linux/amd64), and G3 merged metric.
- Two G5 payload controls render identically in Go, as predicted (no visible difference).
- The fixes for G4/G5/G7/G10 are now in `src/tui/promql.rs` (owner: this agent). G3 is in
  `promql_service.rs` (delegated to the effects owner).
- **Not yet executed** on the post-fix source: rustfmt, build, unit tests, clippy and the
  candidate replay of the six gap cases. See R10 in `promql-requests.md`.

| # | Gap | Visible | Reach | Fix (status) |
|---|---|---|---|---|
| 1 | G4 CLI time location | yes: sidebar, picker, presets | any `--from/--to` with `Z` or an offset when local ≠ that offset | shared SQL `GoTime` + `parse_cli_time` (applied, unexecuted) |
| 2 | G10 equal-time point order | yes: chart cursor/inspector | queries merging series with equal label sets, more than 12 points | `go_sort::slice` pdqsort port; legend uses `go_sort::slice_stable` (applied, unexecuted) |
| 3 | G7 float→int conversion | yes on amd64: +Inf point position, timestamps out of range; `+InfM` | `+Inf` sample, or a timestamp beyond the i64 range | `go_f64_to_i64`, `go_format_unix`, `go_fixed1` (applied, unexecuted) |
| 4 | G3 decode slice/map reuse | yes: METRIC column text | response with duplicate `result` (or `data`) keys | in-place element reuse (effects owner) |
| 5 | G5 hex-float rounding | no PTY difference (Go controls identical); value only | hex sample values (servers emit decimal) | exact `readFloat`/`atofHex` port (applied, unexecuted) |

### G10: proof against Go 1.25.4 `sort/zsortfunc.go` (module cache toolchain)

**Where order matters.** `chartSeriesPoints` sorts with `sort.Slice` by `Time.Before`.
- *No effect:*
  - per-column buckets sum integer `y` (commutative);
  - time bounds;
  - Y min/max;
  - line drawing from bucketed points.
- *Order-dependent:*
  - `points[idx]` for the chart cursor, which feeds the cursor dot `y` and the inspector
    value;
  - `points[len-1].Value` as the "last value" that orders series, colors and legend
    (`SliceStable` over series, itself stable).

**When equal times occur.** `extractChartData` keys series by `promqlChartLabelSet` (the text
between braces). So `a{job="x"}` and `b{job="x"}`, or any query returning several metric
names with equal label sets, merge into **one** series with pairwise equal timestamps. One
merged series also enables the cursor/inspector, which needs `len(series) == 1`.

**Go algorithm.** `pdqsort_func` insertion-sorts when there are 12 or fewer elements. That is
stable, so it equals Rust's stable sort. For more than 12 it partitions.

Hand-run on 14 points `a0..a6, b0..b6` (equal `t_i` pairs):
1. `choosePivot`: i=3, j=7, k=10. `order2(3,7)` swaps once, so `swaps=1` and the hint is
   unknown. The pivot is index 3 (a3).
2. `partition`: the pivot swaps to index 0. Then swap(4,9), swap(5,8), swap(6,7), and the
   pivot returns to 6. The array is
   `[b0,a1,a2,a0,b2,b1,a3,a6,a5,a4,b3,b4,b5,b6]`.
3. leftLen 6 < rightLen 7: insertion sort `[0,6)` gives `b0 a0 a1 b1 a2 b2`.
4. Then `[7,14)` insertion sorts to `b3 a4 b4 a5 b5 a6 b6`.

| Run | Order |
|---|---|
| Go | `b0 a0 a1 b1 a2 b2 a3 b3 a4 b4 a5 b5 a6 b6` |
| Rust stable | `a0 b0 a1 b1 a2 b2 a3 b3 a4 b4 a5 b5 a6 b6` |

**Visible difference.** The first Right press sets `chartCursor = 1`:
- Go inspects **a0**; Rust inspects **b0**.
- With `a` values `1` and `b` values `100`, Go shows `value 1` and the dot at a's `y`. Rust
  shows `value 100`.
- `points[len-1]` is b6 in both, so the legend/color order is unchanged in this fixture. It
  can differ for other sizes.

**Go fixture.**
- Order oracle: `docs/evidence/phase7/promql-sort-oracle-main.go.txt`, with output in
  `promql-sort-oracle-result.txt` from the pinned go1.25.4 run. The output matches the
  hand-derived order, and the 12-point control is stable.
- Screen fixture `p7-promql-g10-equal-time-merge-dark` passes against actual Go. The first
  Right shows `value 1` and the second Right shows `value 1`.

**Fix (applied).** `go_sort` ports Go's generic `pdqsort_func` with its exact compare/swap
sequence:
- `insertionSort`
- `heapSort`/`siftDown`
- `breakPatterns` with `xorshift` seeded by length and `nextPowerOfTwo`
- `choosePivot`, `median`, `medianAdjacent`
- `reverseRange`, `partialInsertionSort`, `partition`, `partitionEqual`
- `limit = bits.Len(len)`

It is private to promql.rs. The unit test pins both oracle orders.
- The legend/color order uses `SliceStable`. It is ported too (`stable` insertion blocks plus
  `symMerge`/`rotate`), because a NaN last value makes Go's `Less` false in both directions.
- Rust's own sorts may order such input differently, or panic on a comparator that is not a
  total order.

### G4: CLI time location

**Go behavior.**
- `parseTimeStr` returns `time.Parse(time.RFC3339, s)` for absolute values. That keeps a `Z`
  value in UTC and any other offset in a fixed zone, or in Local when the offset matches the
  local zone. Relative values are `time.Now()` minus the duration, in Local.
- `datetime.SetTime` formats `localLayout` in *that* location, and so does
  `formatSQLControlTime`'s `t.Format(...)`.
- Presets do `end.Add(d)`, and AddDate/clamped months use the value's own location.
- A digit edit reparses in Local.

**release1 Rust.** It converted to Local at startup.

**Fixture** (`TZ=America/New_York`, `--from 2024-01-01T00:00:00Z --to 2024-01-01T00:10:00Z -i`).
It runs at 140x35, because at 100x30 actual Go clips the sidebar value to `UTC+00:`.

| | Sidebar TO |
|---|---|
| Go | `01 Jan 2024, 00:10 \| UTC+00:00` |
| release1 Rust | `31 Dec 2023, 19:10 \| UTC-05:00` |

The time picker values differed the same way. Both the Z and the fixed-zone (`+05:30`) runs
pass against actual Go.

**Fix (applied).** The parent decided to share the SQL owner's types:
- `Setup`, `DateTimeField` and `TimeInput` hold `sql::GoTime`.
- `execute` parses with `sql::parse_cli_time`, which keeps the `time_compat::parse_time`
  error text.
- Segment edits use `GoTime::{add, add_date, add_months_clamped}` in the value's own
  location.
- A digit edit reparses with `GoTime::from_naive(_, Zone::Local)`, which carries Go's
  `time.Date` normalization.
- Mode toggles and presets use `time.Now()` (Local).
- Comparisons use the instant.

The SQL source had only visibility changes (by the SQL owner).

### G7: float to int conversion is platform-defined in Go

**Go behavior.** Go compiles `int(f)` to `CVTTSD2SQ` on amd64, which yields `MinInt64` for
NaN, ±Inf and out-of-range values. On arm64 it is `FCVTZS`, which saturates (NaN → 0). Rust
`as` matches arm64 only.

**Where it is visible.**
1. `chartCursorCanvasY`. With any `+Inf` sample, `chartYAxisScale` yields NaN bounds. Go's
   `SetViewYRange` then keeps the view at 0..1 (`NaN < NaN` is false), and Rust does the same.
   Finite points clamp to the top. The `+Inf` point: amd64 gives `MinInt64` → clamp 0 → the
   **bottom** row. Rust and arm64 give `MaxInt` → the **top** row.
   Fixture: `query_range` `up` values `["1", "+Inf", "2"]`. Go amd64 draws a dip at the middle
   column; Rust draws a flat line.
2. `promqlModelFormatTS` does `sec, frac := math.Modf(v)` and then
   `time.Unix(int64(sec), int64(frac*1e9))`.
   - For `1e30`, frac is 0, so nsec = 0, and `int64(sec)` = MinInt64 on amd64.
   - Go formats through `absSec = uint64(sec + offset + unixToAbsolute)`. The addition wraps,
     so actual Go shows `292277026596-12-04T15:30:08Z`, and the TIME column shows `15:30:08`.
     This is the fixture `p7-promql-g7-huge-timestamp-amd64-dark` (instant `[1e30, "1"]`),
     plus a `time.Unix` scratch control.
   - release1 Rust printed the raw `%v` (`1e+30`).
   - Correction: an earlier hand derivation in this audit gave `08:29:52`. That was wrong,
     and actual Go is authoritative.
3. `formatCompactChartValue` uses Go `%.1f`, which prints `+Inf`. So the +Inf inspector shows
   `+InfM`; release1 Rust showed `infM`.

**Fix (applied).** Every Go `int(...)`/`int64(...)` site in the chart and timestamp code now
goes through `go_f64_to_i64`: `round_go`, `unix_time`, the X-label offset and
`promqlModelFormatTS`.
- On `x86_64` it applies the CVTTSD2SQ rule (MinInt64 for NaN, ±Inf and out-of-range
  values). Elsewhere it uses Rust's saturating `as`, which is arm64 FCVTZS. Only linux/amd64
  is oracle-tested; other targets are uncharacterized, not claimed.
- The metrics helper is private and formats differently, so it was not reused. Instead,
  `go_format_unix` ports Go 1.25.4 `time.Unix` normalization, `absSec`, `absDays.date`,
  `clock` and `appendInt`, keeping the Modf path. That gives `15:30:08`, and also unsigned
  years ≥ 10000 and `-0001` years as Go prints them.
- time.Local's offset comes from chrono for instants it can represent. Outside that range,
  the nearest representable instant's offset is used. That is exact for TZ=UTC (the fixture
  zone); other zones outside chrono's range are uncharacterized.
- `go_fixed1` prints `+Inf`/`-Inf`/`NaN`.
- NaN alone already matched (both reach the bottom row).

### G3: encoding/json slice and map reuse

**Go behavior.** `decode.go` `array()` reuses existing slice elements within capacity.
`d.value(v.Index(i))` decodes **into** the old struct, and `object()` reuses a non-nil map.
- A second `"result"` array therefore merges into the earlier elements: absent fields are
  kept, and `metric` maps gain keys. Then `SetLen(i)` truncates while the hidden capacity
  keeps the old elements.
- An empty array makes a fresh slice, and `null` zeroes the field.

**Fixture.** A body with `"result":[{"metric":{"__name__":"up","job":"a"},"values":[[1704067200,"1"]]}]`
followed by a second `"result":[{"metric":{"instance":"i"},"values":[[1704067200,"3"]]}]`:

| | METRIC column |
|---|---|
| Go | `up{instance="i", job="a"}` with value 3 |
| Rust | `{instance="i"}` |

**Fix.** Keep a storage `Vec<PromqlSeries>` plus a logical length:
- decode element `i` in place when `i < storage.len()`, otherwise push a default;
- truncate the logical length at the end;
- `[]` clears storage, and `null` clears too;
- `decode_series` merges into the existing struct: metric map entries overwritten or added,
  `value`/`values` replaced only when present.

This needs service-only changes plus a unit test using the fixture body.

### G5: hex-float rounding

**release1 Rust.** It did a u64→f64 conversion, then multiplied by `powi`. That is exact for
normal results. Subnormal results were rounded twice, or underflowed early in `powi`.

**Values.** `0x1.8p-1075`: Go `atofHex` gives `5e-324`; release1 Rust gave `0`.
- There is no visible screen difference. Tiny values always take the `padding = 1` path, so
  the Y scale, the rows and the `0` labels are identical.
- An earlier draft of this audit claimed the Y range changes. That was wrong.
- The parent's two actual-Go payload controls rendered identically, confirming this.
- Correctness is therefore pinned by unit tests and by the helper differential below.

**Reach.** It needs hex sample values; Prometheus emits decimal strings.

**Fix (applied).** An exact port of strconv `readFloat` (base 16) and `atofHex`:
- 16 hex mantissa digits with truncation sticky, leading-zero/dot exponent bookkeeping, a
  mandatory `p`, and the exponent capped at 10000;
- normalization to 54 bits, denormal shifting with sticky, round half to even;
- overflow becomes ErrRange (rejected), and `f64::from_bits` assembles the result.

## Helper differential against pinned Go (parent evidence)

The parent ran `docs/evidence/phase7/promql-helper-probe/`. It contains `report.json`,
`inputs.txt`, `go.txt`, `rust.txt`, `main.go.txt` and `reproduce.py`.
- **Method.** The current `go_sort` and `go_parse_float` (with `parse_hex_float`/`atof_hex`
  and `underscore_ok`) were extracted verbatim into a standalone rustc probe and compared
  with pinned Go on 654 cases, with **zero mismatches**:
  - 510 float inputs, including random hex, subnormal and overflow values;
  - 144 sort cases of lengths 0..1000, including duplicates, NaN, ±Inf and a stable
    comparator.
- **Scope.** The scoped extraction hashes are recorded in `report.json`. The probe covers
  those helpers only. It makes no claim about the whole CLI.
- **Keeping it valid.** Any later edit to those helpers needs the extraction hashes
  revalidated, or the probe rerun. The parenthesized `atof_hex` bit operations may have been
  written after the extraction. That edit is formatting-only, but the hashes still need
  checking.
- **Pending.** `go_format_unix`, `go_f64_to_i64` and `go_fixed1` are not part of that probe.
  They are pinned by unit tests and by the actual-Go G7 screens, and
  `docs/evidence/phase7/promql-numeric-oracle-main.go.txt` lists their Go expectations. Its
  result (`promql-numeric-oracle-result.txt`) is pending a parent run.

## Remaining recorded gaps

- **G1** Screen (cell) parity.
  - Immutable release1 passes every PromQL case in the 178-case styled corpus. The only
    corpus issues are SQL CJK and spinner cases.
  - The six new G3/G4/G7/G10 screens pass against actual Go. Their candidate replays on the
    post-fix source are pending, as is the expanded 190-case two-theme corpus.
- **G2** Request traces. Release1 passes the PromQL effects cases, the OAuth scope cases
  (including `oauth_promql_both_effects`) and the genuine-deadline cases (see "Evidence
  status"). The post-fix candidate must replay them.
- **G6** The goroutine stack trace Go prints to stderr after a caught panic is not
  reproducible. Go's print also races `Run` returning in the command-goroutine case.
- **G9 / L-PROMQL-1** Builder truncation now owns the byte slice through column
  layout, overlay placement and terminal emission; ordinary nonbuilder rendering
  retains its text path. Actual Go at 80×30 clips `abcdefghijkl测试_tail` to the
  ASCII prefix, raw E6 and three dots. Exact row styling/cursor/request controls
  are recorded in `phase7-effects.md`. The corrected source compiles and its
  library tests pass; rebuilt CLI replay is still required before closure.
- **Render faults.** These are reproduced through `style::ui_truncate`/`Theme::key_grid`
  (`GoPanic`) and are not a gap.
- **G8** Performance budgets are not measured. Benchmarks wait for the agreed quiet window.

## Shared immutable result ownership (2026-10-15)

The narrow ownership change following
`phase7-a1-retry04-sustained-investigation-r1.md` is implemented in
`src/tui/promql.rs`. The asserted production preimage SHA-256 was
`51387004ec5207391d8c60252d1eb62997ae200d8ec23a89237238a1f114cf55`.
The frozen implementation SHA-256 is
`9e186f17c9f61d81ce1d16a9ca0847ad0d44294be0fa9386f8831b17f8a572d2`.

`PromqlState::{data_rows, chart_rows}` and `ResultsTable::rows` now hold
`Arc<[Row]>`. Successful completion consumes each service `Vec<Row>` into
shared storage once. Table clones, column updates and the local render table
share the allocation rather than cloning every row and its four strings.
Chart rows use the same ownership scheme so cloning the state cannot retain a
second deep-copy path. The empty-chart matrix fallback shares `data_rows`.
Both-mode table and chart results remain distinct when supplied separately.
The existing service-side matrix `rows.clone()` is unchanged: this change
removes snapshot copies, not that one-time service construction copy.

Replacement and errors install new handles; old snapshots keep their original
rows. There is no mutable shared row access. Filter text, focus, cursor and
page fields remain owned by each table snapshot, and filtering still computes
references from that snapshot's rows and current filter. The highlighted-row
clone remains. Reducer ordering, stale-result policy, chart extraction,
filtering, selection, geometry, key handling, navigation and local render page
calculations are unchanged. In particular, the all-row `visible_rows`
reference-vector construction remains unchanged, including its repeated scans.

Five added unit tests cover allocation identity across state/table/view
snapshots, distinct Both-mode chart storage and matrix fallback, replacement
and error clearing with stable old frames, independent filters and selection,
metric detail, local render pagination and frame fixture paths. The dense test
uses 256,000 synthetic rows and the preserved forward-31 prefix: 18 individual
PgDown updates from zero-based cursor 7680 to 7950, displayed cursor 7951 and
`bench-series-0031`. It also checks End, wraparound, PgUp and Home. Its local
height-14 render preserves the existing empty-detail line count and eight-row
render page while leaving the 15-row update model unchanged. This is a unit
regression, not a replay of the historical native session.

Scoped validation, with artifacts under
`/home/ajs/.cache/pb-phase7/promql-shared-rows-r1/`:

- `cargo test --offline --locked --lib tui::promql`: **60 passed**, both with
  plain output and forced ANSI output. This filter includes PromQL service
  unit tests. No other unit or integration suite was run.
- `rustfmt --edition 2024 --check src/tui/promql.rs`: passed.
- `cargo clippy --offline --locked --lib -- -D warnings`: passed.
- **48 exact frame byte comparisons**: 12 fixture states each under plain and
  forced ANSI profiles with dark/light `PB_THEME` settings. Cases cover table,
  page, detail, metric/value/empty filters, two resizes, chart, chart fallback,
  replacement and error. `frame-parity.json` and retained `.bin` frames bind
  each comparison. Captures use unit rendering with UTC and non-TTY output;
  ANSI adaptive background defaults to dark. They do not establish truecolor,
  PTY, actual-Go or complete corpus parity. Temporary frame-printing test
  instrumentation was removed; the compared production code is identical to
  the frozen production code.

Failures are retained: the first command could not locate Cargo (exit 127;
fixed by supplying its installed bin directory in the check runner's PATH),
and one added assertion initially expected a nine-row height-14 render. That
assertion overlooked the existing one-line height of an empty detail string;
it was corrected to eight rows without changing production pagination. Final
plain and ANSI suites pass. Every command used an explicit timeout of at most
600 seconds with a five-second kill grace, and all validation and cleanup fit
within the user's strict 1800-second limit. Build outputs were confined to the
owned cache target directory, seeded by a reflink copy of existing artifacts.

This is a source ownership change with focused unit evidence. There was no
release/candidate build, native Go/Rust CLI run, functional campaign or
performance campaign, and no commit. No measured speedup, deadline resolution,
acceptance or performance admission is claimed. The historical failed A1-04
candidate and its artifacts remain frozen. Central checks, release build,
actual functional validation and fresh independent review remain with the root.

Independent Opus review accepted the source ownership change. Conversion from `Vec<Row>` into `Arc<[Row]>` allocates shared storage and moves row structs once per result; it does not clone their strings. Both buffers coexist briefly. The 48 frame comparisons include 36 distinct frame contents. Local render-table sharing is verified by source inspection, not a test hook; empty-handle pointer equality alone does not prove sharing. Root central format/all-target Clippy and 562 library tests passed (one existing ignored test). No actual deadline or performance result is established by these checks.
