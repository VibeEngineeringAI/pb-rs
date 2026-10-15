# Phase 7 review: A1 SQL geometry correction R1 (Opus R1)

Scope: a fresh, independent, narrow review of the A1 SQL collector correction. I read these in full:

- the original diagnosis `docs/phase7-review-a1-functional-failure-opus-r1.md` (SHA `5638a0b6…7746f68`, verified);
- `docs/evidence/phase7/a1-geometry-root-disposition.json`;
- `a1-geometry-fix-r1.md`, `a1-geometry-fix-r1-freeze.json` and `a1-geometry-fix-r1-four-files.patch`.

I edited no source, contract, doc or test file. The only outputs are this new file and the unique cache `/home/ajs/.cache/pb-phase7/a1-geometry-fix-review-opus-r1`. No native session, Go, Rust, helper, probe, compiler, PTY, socket, build, profile, common lock or nested delegate was used. This is not a timing or performance claim.

## Verdict: ACCEPT for the root's actual Go/Rust preflight

I found no blocking defect.

- The corrected `sql_resize_geometry` matches Go and Rust source and the actual retained Go frames, column for column.
- The secondary reporting change keeps abort, validation order and no-subset behavior.
- Case definitions are unchanged.

What this accepts: the corrected collector may proceed to the new 24-role actual preflight. What it does not accept: Rust behavior, a second resize, other SQL variants, PromQL geometry, budget feasibility and any latency. All of those remain for that preflight and later review.

## Source formula (independently re-read; hashes match the original review)

**Go, `pkg/model/query.go`:**
- `:57`: `sqlControlsMinWidth = len("02 Jun 2026, 13:25 | UTC+05:30") + 4 = 34`.
- `:447`: `columnsDrawerOpen: true`.
- `:1217-1220`: `sidebarW = 34`, `+3` when `m.width >= 140`.
- `:1227`: `fullHeightControls := m.columnsDrawerOpen`.
- `:1231-1235`: `editorW = W − sidebarW − 1`, floored at 30.
- `:1280-1283`: `resultsW = editorW` when the drawer is open.
- `:1398-1401`: the body is `leftSection | 1-col sideGap | controlsPane`.

**Rust, `src/tui/sql.rs`:** mirrors Go.
- `:35-36` constants, `:2550` initial `columns_drawer_open: true`, `:3446-3456` width logic.
- `:3516-3520` `results_w`, and `:3580`/`:3605` the one-column `side_gap`.

**Drawer toggle:** the only toggle in each is `toggleColumnsDrawer` / `toggle_columns_drawer`. Its single caller is the ctrl+l key (`query.go:1042`, `sql.rs:3298`). None of the 52 generated case JSONs contains `ctrl-l`, `ctrl+l` or a form feed.

**Footer and marker:**
- The footer is the rightPart `rows N/T`, right-aligned with one column of padding inside `resultsInnerW = resultsW − 4`. It ends exclusively at `resultsW − 3`.
- During loading, ` · loading` is appended, so the exact-end clause correctly stays false until the load settles.

The implementation matches all of this. The `max(30, …)` floor and the `results_width + 1 >= width` guard only matter for widths below 65. All 320 geometry waits use 120×40 or 140×45.

## Fixture fidelity (independently checked against the original retained files)

`a1-geometry-retained-go.json.gz` (`c713340e…`) was checked against the original retained session:

**Raw stream and frames:**
- `stdout_hex` is byte-identical to the retained `stdout.bin`: 38,198 bytes, and both copies hash to `2524289b…`.
- The `120x40` frame equals the retained `session.json` `return-first` frame.
- The `140x45` frame equals `final_frame` in both `reference/session.json` and `reference/execute/session.json`. The frame digests match.

**Offsets and origins:**
- The resize offset 23905 equals `decoder_offsets.actions[7].byte_offset`.
- Retained `actions` show only `[7, [140,45]]`, with `failure: "session timeout"` and termination `{signal: 9, core_dumped: false}`.
- `old_predicate_source` equals the AST segment of `sql_resize_geometry` in the pre-fix bench (`bf630d9a…`, the hash the original review recorded), and its SHA is `d1344041…`.
- `case_sha256` equals both the session case digest and the review's `0b189aaf…`.

**Replay:** feeding the original raw bytes into `p7.Terminal` reproduces both full styled frames exactly.

## Independent geometry measurement (actual cells, not the predicate)

| Frame | footer row | footer end | `│` columns on row H−6 | gap column blank | marker row / column | formula |
| --- | --- | --- | --- | --- | --- | --- |
| 120×40 | 34 = H−6 | 82 | 0, 84, 86, 119 | 85 ✓ | 16 / 13 | sidebar 34, resultsW 85 → 82; bars 0/84/86/119 |
| 140×45 | 39 = H−6 | 99 | 0, 101, 103, 139 | 102 ✓ | 16 / 13 | sidebar 37, resultsW 102 → 99; bars 0/101/103/139 |

Row 16 has the same bars plus an internal table separator at column 12. The predicate checks only the five specified positions, so that separator is irrelevant.

**Predicate outcomes:**
- The new predicate is true on both actual targets; the old one is false on both.
- On the retained checkpoints, with the row-zero target: `startup` and `return-first` are new-true/old-false, while `row-down` and `page-down` are false for both (different footer).
- The `startup` result is expected, since the layout is the same at 120×40. Resize phases still carry the unchanged `snapshot_resize`/`resize_rerender`/`output_after_action`/dimensions/corner/stability gates (`phase7_bench.py:173-176, 496-518`).

**Transient scan:** a 16-byte-stride scan of the post-resize stream (14,293 bytes) found the new predicate false until offset 36257, which is 12,352 bytes after the resize. After that it was true with no flips. There was no early partial-repaint pass.

## Scope / AST / patch

- **Bench AST:** current vs pre-fix (`bf630d9a…`) changes exactly `_functional_preflight` and `sql_resize_geometry`.
- **Test AST:** adds `A1GeometryTests` and `A1CaptureOutcomeTests`. In `R4Tests` it changes only `test_top_border_first_stale_footer_rejected_growth_shrink_and_identical_row_repaint`, whose synthetic rows moved from closed-drawer to open-drawer geometry.
- **Patch:** regenerating the unified diff from the author's preserved before-copies (hashes equal the freeze "before" values) to the current four files matches the frozen patch hunk for hunk.
- **Case definitions:** recomputed directly from the root's `postidle-bracket-applied/preflight-expected-case-hashes.json` typed Options, the primary and resource-attribution profiles give 26 + 26 hashes, all equal to the expected values. The original A1 `report.json` `case_hashes` (12) are an exact subset of the recomputed primary hashes.
- **End preservation:** all 28 tracked identities were unchanged from start to end of my run. They are the owned files, sources, original review and diagnostic cache, retained A1 session/raw/report/supervision files, and the `a1-geometry-*` evidence.

## Tests and causal controls (guarded, pure)

**Guard:** `guard.py` installs before any harness import. It blocks:
- `os` fork/spawn/exec/system/popen/openpty/kill;
- `subprocess.*`, `pty.*`, socket factories, and `fcntl.flock`/`lockf`;
- socket construction, via a raising `socket.socket` subclass (SSL-compatible).

`TMPDIR` and `p7.TMP` point at the review cache.

**Focused run:** `A1GeometryTests` (3), `A1CaptureOutcomeTests` (1) and `R4Tests` (9) ran before and after the mutants. 12 of 13 passed both times. The one failure is `R4Tests.test_functional_admission_contention_prevents_preparation_and_releases`:
- It takes a real `fcntl.flock`, which my guard deliberately forbids. That accounts for all 15 guard trips, one per R4Tests run.
- It is outside the patch. The author passed it with an emulated lock; I did not re-emulate it, and I did not repeat the 119-test suite.

**Geometry mutants** (my own edits to the current function, patched in for `A1GeometryTests` + `R4Tests`): every one is killed by a test other than the lock-guarded one.

| Mutant | Actual frames pass? | Killed by |
| --- | --- | --- |
| sidebar without +3 / threshold `>140` | 120 yes, 140 no | actual-frame + growth/shrink + R4 |
| `W − sidebar` (no −1) / end −2 / footer row H−7 | no | actual-frame tests |
| no gap check / no sidebar borders / no results right border | yes | mutation test (border/gap controls) |
| footer anywhere on row | yes | footer-column ±1 controls |
| marker anywhere on row 16 | yes | marker-only-in-sidebar control |
| no frame-shape check | yes | malformed-row control |
| no row-16 edge check | yes | row-16 border/gap controls |
| original compiled predicate | — | actual-frame + growth/shrink + R4 |

The actual-frame negatives use the observed widths 85/102 hard-coded, not the formula. So they are anchored to the frames rather than mirroring the implementation. The modified R4 synthetic test does recompute the formula; it is a secondary control only.

**Preflight mutants** (compiled into the bench module so the mocks apply), all killed by `A1CaptureOutcomeTests`:
- assignments moved after `check_binding`;
- catch-and-continue around `check_binding`;
- observation validation reordered before binding;
- assignments dropped.

The unmodified test proves three things about the real `check_binding`:
- it still raises the original `ValueError` and the report stays invalid;
- the candidate stays unrun, and `observation_issues`/`compare` are never called;
- `issues` stays `['not completed']`, while `capture_failure` and `termination` are retained.

`validate_preflight` has no per-sample key allowlist, so the new informational keys cannot reject a future valid report. Nothing admits a sample on them.

## Measurement boundary (honest assessment)

The two new assignments sit between the return of `p7.sample` and `sample_finished = time.monotonic_ns()`. Their cost (a dict `get` and a `deepcopy` of a termination dict of at most two scalars, or `None`) therefore moves from `owned_postprocessing_wall_ms` into `capture_and_sample_wall_ms`. `scheduling_wall_ms` is unaffected.

Neither field is read by any gate, validator, budget or extraction; their only uses are at `phase7_bench.py:1669-1684`. Both are explicitly labelled descriptive scheduling cost, not CLI performance. `FunctionalGuard` budgeting is unaffected.

I judge the shift immaterial; I measured nothing, and I make no latency claim either way. Placing the assignments after `sample_finished` would have kept the old boundary bit for bit. That is an optional nit, not a blocker.

## Non-blocking notes

1. **Tautological test block.** In `test_actual_frames_reconstruct_exactly_and_kill_compiled_old_predicate`, the `mock.patch` + `assertRaises(AssertionError)` block only re-asserts `old_predicate(actual)` is false, which the line above already checks. It is harmless.
2. **Rust is source inference only.** Rust geometry, including the `side_gap`, is not actual proof. The second actual resize (shrink to 120×40) has never been natively observed; the test's shrink path is an emulator refeed.
3. **PromQL unchanged by design.** I spot-checked that PromQL `dense_table_geometry` still uses W−3. Go `promql.go:1462,1520` and Rust `promql.rs:5229,5279` size results from the full terminal width. That is source inference only; the actual PromQL preflight remains pending.

## Runs and disclosures

**Run 1:** `/usr/bin/python3 -E -s review.py`. It exited 1 at the first `p7.Terminal`, because system Python lacks the pinned `pyte`. Only identity hashing and fixture JSON comparisons had run; no test executed. The logs are preserved in `failed-1/` (stderr `8f2e5baa…`).

**Run 2:** `harness-venv/bin/python -E -s review.py`, exit 0, empty stderr. There was no retry loop and no temp-file moves beyond preserving run 1's logs.

| Artifact | sha256 |
| --- | --- |
| `guard.py` | `6b89ce4f05061d377ca29b521cc24df237b03b475c8230c0b22547aae9823845` |
| `review.py` | `e799292fed8838100c8dba3b237161d11ba912d3416bdeafaad037c0967b5908` |
| `review-output.json` | `8a6e51664ec6f0ba8ab14eef507359e30144415eccfa2e15a32462b4301b2e9c` |
| `review-stdout.txt` | `cd00c55dc5cd4e0895bba595df97ac70b32e136d35c9ab4eeb05a211f5d0fc06` |

**Verified current identities:**

| File | sha256 |
| --- | --- |
| bench | `e03e1d2bfce45d7123b67a1870e8e3e38c7d5a6ae92c99881c513f4965846c01` |
| tests | `244bd6e40432711a4e0c93c9144d9d2e8115cf5b2953815ead01af0e2b0a845c` |
| `phase7-measurement.md` | `b9c6b991…` |
| `contracts/phase7.md` | `19f4412a…` |
| fixture | `c713340e3df43b3d2cc96ade055a8f8da535ff855b75c3ec2ca75b247e59ae2c` |
| patch | `853d686a…` |
| case-hashes | `cd55e7a6…` |

`phase7.py`, `phase7_workloads.py`, `phase7_spawn.c`, `query.go`, `sql.rs`, `promql.go` and `promql.rs` match the original review and the freeze.

Frozen for root.
