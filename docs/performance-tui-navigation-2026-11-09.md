# TUI navigation performance — 2026-11-09

This change reduces the CPU cost of building interactive SQL and PromQL frames. It addresses
[#13](https://github.com/VibeEngineeringAI/pb-rs/issues/13) and the chart part of
[#14](https://github.com/VibeEngineeringAI/pb-rs/issues/14). The target was at least 25% lower
native CPU for sustained navigation against `main` at `5a49d43`, with byte-identical styled
frames. Only `src/tui/` changes; non-interactive commands run the same code as before.

## Result

Five measured randomized pairs plus one excluded warmup pair per workload, 120×40. CPU is the
CLI's native wait4 user+system time. Reductions are medians of paired ratios with a 2,000-draw
paired bootstrap 95% interval, so they need not equal the ratio of the displayed medians.
Every capture passed the strict styled-frame, request and configuration comparator.

| Workload (final branch, dark) | CPU, baseline → candidate | Paired CPU reduction (95% interval) | `instructions:u` |
| --- | ---: | ---: | ---: |
| SQL scroll, 20,000 rows, sustained | 4,134 → 1,770 ms | **57.1%** (56.7–57.4%) | −53.6% |
| PromQL dense, 1,000 × 256, sustained | 2,408 → 1,046 ms | **56.6%** (55.6–57.5%) | −53.5% |

| Workload (pre-fix build, light) | CPU, baseline → candidate | Paired CPU reduction (95% interval) |
| --- | ---: | ---: |
| SQL scroll, 20,000 rows, sustained | 4,446 → 1,841 ms | 56.5% (53.7–59.9%) |
| PromQL dense, 1,000 × 256, sustained | 2,655 → 1,116 ms | 59.3% (55.4–63.0%) |

Other dark workloads, pre-fix build, three measured pairs plus one warmup each (directional):

| Workload | CPU, baseline → candidate | Paired CPU reduction |
| --- | ---: | ---: |
| SQL scroll 500 / 1,500 / 20,000 rows | 33 → 20 / 154 → 66 / 203 → 85 ms | 44% / 57% / 58% |
| SQL scroll 20,000 rows, delayed response | 200 → 91 ms | 55% |
| PromQL dense 1 / 100 / 1,000 series | 30 → 16 / 112 → 46 / 639 → 254 ms | 48% / 58% / 60% |
| SQL editor | 21 → 11 ms | 51% |
| SQL / PromQL query error | 16 → 8 / 13 → 7 ms | 49% / 47% |

The pre-fix build differs from the final branch only by the PromQL padding correction below,
which runs only in the PromQL table.

### Memory

Peak RSS in the pre-fix runs rose 0.2–1.1 MB (0.4–3.8%) across all workloads. The final
branch's sustained runs show SQL 11.77 → 13.57 MB and PromQL 71.93 → 74.00 MB. A direct
pre-fix/final comparison reproduced about 1.5 MB more SQL RSS for the final binary at equal
CPU, while `--help` peaks at an identical 13.9 MB for both. Heaptrack on the sustained SQL
workload shows the same 2.26 MB peak heap and 5.26 million allocation calls for both, so the
difference is not heap growth. The correction adds 768 bytes of `.text`; the remaining
difference is attributed to which executable pages the SQL path touches, consistent with the
executable-residency sensitivity recorded in [the CLI campaign](cli-performance.md). It does
not scale with result size. The pre-fix release build is byte-identical whether built in the
integration worktree or from the linear branch.

## Where the time went

Baseline LBR call-graph profiles (zero lost samples) put 92% (SQL) and 96% (PromQL) of
sampled user cycles in view construction, and over half in text primitives in
`src/tui/style.rs`: `grapheme_class` 22.9% / 20.7%, `get_lines_bytes` 14.2% / 11.1%,
`first_cluster_bytes` 7.8% / 10.7%, `decode_rune` 5.2% / 5.7%. Heaptrack recorded 17.7 million
(SQL) and 13.3 million (PromQL) allocation calls, dominated by `Style::render_impl` and its
helpers, chrome composition (`controls_box`, `titled_pane`, `pane_body_lines`) and PromQL
`build_chart`.

## Changes

| Commit | Change |
| --- | --- |
| `adb5eef` | Cache each style's SGR prefix/suffix per (style, environment); emit plain cells directly; skip inactive layout copies. |
| `ae84a11` | ASCII fast paths for width measurement, a Unicode page index for grapheme classes, and reused line widths in vertical joins. |
| `97e2df4` | Hand finished views to the terminal writer without copying; diff against reusable byte ranges of the previous frame. |
| `b8201a7` | PromQL table: cached filtered indices, per-frame styles, borrowed cell text, one output buffer. |
| `695d285` | PromQL chart: cached geometry keyed on series, size, range, display and clock; dense x buckets; cached styled glyphs. |
| `fa76d9e` | SQL table: reusable row buffers, per-frame styles and edges, no rebuild for cursor-only moves. |
| `3edeaab` | Bounded caches for SQL/PromQL controls and pane bodies; streamlined pane composition. |
| `56cf8bd` | Review correction: measure finished PromQL table lines before skipping JoinVertical padding. |

Every new cache is bounded (256, 16, 2 or 1 entries, or one per table), lives on the TUI thread,
and keys on every input its original code path reads. Each change carries differential tests
against the original implementation.

## Review

An independent review of the combined diff fuzzed every cache and fast path against the
original code (300,000 random styles across all four colour profiles and both themes, 40,000
SQL tables, 6,000 chart builds, 3,000 sixty-frame terminal sequences, plus exhaustive existing
code-point tests). It found one low-severity divergence. Without colour (`Profile::Ascii`), a
PromQL table whose only row has a cell ending inside an escape sequence lost padding, because
the uniform-width shortcut summed separately measured cell widths. Real Prometheus data cannot
produce this. `56cf8bd` measures the finished lines instead, as the SQL table already did. Its
regression test fails on the pre-fix code and passes after the fix.

## Functional validation

- Rust: **888 passed, zero failed, 16 ignored** on the final branch. `cargo fmt --check` and
  `cargo clippy --all-targets -- -D warnings` pass.
- Phase-seven functional corpus, baseline versus candidate, strict styled comparison:
  - Dark, pre-fix build: **95/95**.
  - Light, pre-fix build, one case per invocation: **95/95 on the first attempt**.
  - Dark, final build: all **43 SQL and PromQL cases** pass.
- Python harness: 1,086 tests across all modules. `test_go_diagnostics` failed setup because the
  `mise` Go shim is not configured in that directory; rerun with
  `PB_DIAGNOSTIC_GO=…/go/1.27.1/bin/go`, all 13 pass.

Two earlier whole-corpus light runs stopped at `masked-password` in login cases. The single
differing cell was row 6, column 21, the cursor after 14 password bullets: drawn in one capture
and blank in the other. [phase7-auth.md](phase7-auth.md) documents this cell's dependence on the
dwell between username and password. Across those runs and six focused reruns, each binary
captured both states (baseline blank once, candidate blank twice), and no login or editor
source changed. The case-by-case light run then passed all 95 cases first time.

## Not adopted

- Navigation update and data path changes: no end-to-end instruction change (0.996 / 0.999)
  and about 1.5 MB more SQL RSS.
- mimalloc and jemalloc: higher RSS and small-command regressions.
- Fat LTO: 6.75% smaller binary, no meaningful navigation gain.
- Profile-guided optimization: 10.85% smaller binary, but small-command RSS rose 10–32%.
- `panic = "abort"`: conflicts with existing unwind-based terminal restoration.
- Skipping superseded frames: the native writer has no frame ticker, so it would change frame
  timing and message acceptance.

## Method and limitations

Host: Intel Core i9-13900HK (20 logical CPUs), Linux 7.2.5, Rust 1.96.0, release profile
(thin LTO, one codegen unit, stripped). This is a shared development machine. Concurrent builds
ran during measurement, so absolute CPU is noisy: an identical-binary pair showed 14.5% SQL CPU
role bias, while instructions differed by 0.16%. Measurements took a shared lock, pinned four
performance cores and randomized pair order. `instructions:u` was collected by attaching
`perf stat` after spawn, so it misses the brief startup window. The paired intervals
describe this host, not an idle VM.

The harness compares checkpoint frames, not raw PTY byte streams. Faster rendering lets more
intermediate frames be superseded before the asynchronous writer drains its slot, so SQL emits
fewer total bytes (for example 1.21 MB → 0.83 MB in a quick run). After masking digits, the
set of emitted line shapes is identical in both roles.

macOS and Windows were not run locally; `rust-native-platforms.yaml` covers them in CI.

Frozen artifacts (SHA-256):

- Baseline `5a49d43` release: `8a407e1d16987858e8c7594fd3b669a0606cbdcc66b879b433ce2176cbd7b462`
- Pre-fix candidate (`3edeaab`): `e45fcd26a75c76e454516d32b1d49d2003e489386d1771acdc7e5dc07415e342`
- Final candidate (`56cf8bd`): `9543a9e1d50b80f63034733c7a74b359358ba74efb7ac26ff3fa7828f461d453`

Raw reports, captures, profiles and heaptrack traces are retained locally under
`~/.cache/pb-perf-campaign/` and are not published.

## Reproduction

```sh
python harness/perf_vm.py \
  --baseline /absolute/path/pb-5a49d43 --candidate /absolute/path/pb-candidate \
  --out /absolute/path/new-results --repetitions 5 --warmups 1 --theme dark \
  --case sql-scroll-20000-sustained-120x40-dark-resource-attribution \
  --case promql-dense-1000-sustained-120x40-dark-resource-attribution

python harness/perf_vm.py --corpus --theme light \
  --baseline /absolute/path/pb-5a49d43 --candidate /absolute/path/pb-candidate \
  --out /absolute/path/corpus-light --repetitions 1 --warmups 0 --case 'p7-*'
```

Use the pinned Python environment with `pyte==0.8.2` and `wcwidth==0.2.13`.
