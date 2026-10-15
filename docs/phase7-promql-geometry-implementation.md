# Phase 7 PromQL dense geometry correction: off-tree implementation (R1)

**Status: prepared off-tree and not applied. Defect D1 below is corrected in the final patch (see "Final freeze" at the end). The run-2 patch and evidence are preserved unchanged.**

This is an implementation hand-off, not a review acceptance. The live bench, tests and docs are untouched:
- `harness/phase7_bench.py`: `e03e1d2b…6c01`
- `harness/test_phase7_bench.py`: `244bd6e4…845c`

The parent combines this patch with Sol's fanout metadata (`2d97ed82…a87ce`, in `/home/ajs/.cache/pb-phase7/a1retry-request-fanout-proposal`), one after the other, followed by a fresh review. That composition is root-owned and kept separate.

All artifacts are in `/home/ajs/.cache/pb-phase7/promql-geometry-implementation-r1/`. The runs used no native session, CLI, helper, probe, compiler, lock, PTY or socket.

## Correction (`promql_geometry.py`, sha256 `a6b114324f0815a45e8f351f9d1379930d746b4074d4468ca8aab0cb95b08519`)

`dense_table_geometry(frame, wait)` is spliced verbatim into the bench patch. It requires all of the following:
- a full frame shape;
- the `N | M rows` wait token on zero-based row **H−8**:
  - ending exclusively at **W−3**,
  - preceded by a blank cell, which closes the `11 | N` vs `1 | N` slice alias,
  - followed by `  │`,
  - with `│` at column 0;
- rows **H−7** and **H−6** exactly `│` + blanks + `│`;
- row **H−5** exactly `└─…─┘`;
- the **complete** table-focus hint row on **H−3**, `  <t> Toggle chart/table +<ctrl-r> Run +<ctrl-c> Quit +` (full match). This is the last row repainted after the toggle.

**Source basis:** `docs/phase7-review-a1retry-promql-opus-r1.md` (`031c2b09…41c5fc53`). Go `promql.go` `View`/`renderResultsTable`/`renderPromqlResultsPane`, with Rust `promql.rs` mirroring them. Every dense case is 120×40.

**Case identity:** unchanged. The predicate code is not case data. The A1 `case_hashes` were rechecked, and the runner reconstructs dense-1 with the same hash, `b478e5cb…`.

## Patch (`promql-geometry.patch`, sha256 `a37516d15d6eca2ad8a582b1791aa79d98392b5bb4552ca456881384c21aedde`)

**AST scope, checked:**
- **bench:** only `dense_table_geometry` changes.
- **tests:** only `R5ObserverTests` (one replaced method) and the new `A1RetryPromqlGeometryTests` change.

**Files the patch touches:**

| File | Diff lines | Patched sha256 | Change |
| --- | --- | --- | --- |
| `harness/phase7_bench.py` | 36 | `4262df14…00fe` | function swap |
| `harness/test_phase7_bench.py` | 161 | `7657d5a5…74cb` | replaced synthetic H−6 test; new class |
| `harness/contracts/phase7.md` | 9 | `8c157103…b0f4` | additive superseding paragraph |
| `docs/phase7-measurement.md` | 10 | `67633576…7729` | additive section |

**New fixture:** `docs/evidence/phase7/a1retry-promql-retained.json.gz` (proposed in-tree path), built at `a1retry-promql-retained.json.gz`, sha256 `12a14654697b1c7b10f328d998382eddf2fb0e67c662fbc62f37ff30ff267b6b`, 68,633 bytes.
- It contains all 8 actual A1-retry raw streams: 4 dense cases × Go/Rust.
- Each stream is bound to the report (`1dcd24f6…`) and its session/stdout hashes.
- It also carries the hash-bound compiled old predicate source and, per role, the `t` offset, the setup and final cell digests, the complete offset and the stub-transient count.

**New tests (`A1RetryPromqlGeometryTests`):**
1. **Reconstruction.** All 8 roles reconstruct the setup and final cells exactly. On the final frames, the new predicate is true and the compiled old one false; the setup chart frame is false.
2. **Every code-point-complete prefix after `t`.** Covered for the dense-1 Go and Rust streams in-tree; the build scanned all 8.
   - These prefixes cover every screen state a real read could expose, because the decoder holds split code points and partial escapes.
   - The first qualifying prefix must be cell-identical to the final frame, and it must stay true afterwards.
   - The old predicate is never true.
   - A pyte wide-char stub transient (`IndexError` in `display`) counts as not ready; the shared parser is unchanged.
3. **Controls on every role, all false under the new predicate:**
   - footer on H−6 (the old layout) or H−7;
   - footer shifted by ±1 column;
   - bottom border missing;
   - stale text on H−7 or H−6;
   - left or right footer border missing;
   - stale chart hint bar;
   - half-painted hint row (the actual mid-paint state);
   - `11 | N` prefix alias;
   - malformed frame shape;
   - stale setup frame;
   - wrong cursor, wrong total, or prefixed target token.

   The old predicate accepts both the H−6 counterfeit and the `11`-vs-`1` alias.

## Validation

### Runs
- **Run 1 (primary):** `build_and_test.py` under the copied `guard.py` (`6b89ce4f…`) and `harness-venv` with `-E -s`. Exit 0 after 671 s.
  - It surfaced two actionable defects: the hint clause accepted a half-painted H−3, and a synthetic footer was painted 117 columns wide.
  - Logs are kept in `failed-1/`.
- **Run 2 (the single retry):** exit 0, empty stderr, 542.6 s. All tracked identities were unchanged before and after (live sources, report, sessions and streams, review doc).

### Boundary proof (all 8 roles)
- Each role was checked at 5,397 code-point-complete prefixes.
- The first qualifying prefix equals the final frame (`first_frame_equals_final` true) in every role, with no later flips.
- The old predicate is never true.
- Each role hits exactly one stub transient.
- The first qualifying prefix is 63 bytes before EOF; those bytes are only `\r\n ESC[B ESC[40;0H` cursor moves after the hint row.

### Discrimination
With the old predicate bound, every new-class test that checks the actual frames fails. With the new predicate, the reconstruction, boundary and controls tests pass.

### Mutants
All 12 mutants were killed by the new-class tests:
- no hint clause; hint checked as a substring only;
- no prefix space;
- footer on H−6 or H−7;
- no blank rows; no bottom border;
- no right border; no left border;
- end at W−2 or W−4;
- no shape check.

### Full suite under the same guard
| Suite | Run | Failures | Errors |
| --- | --- | --- | --- |
| live, unpatched | 119 | 18 | 1 |
| patched | 122 | 19 | 1 |

- The 18 live failures and the error are the lock and quiet-window tests. They trip the guard's forbidden-flock rule (24 trips) and are identical by name across both runs.
- The single extra patched failure is defect D1 below. That is why `full_failures_outside_new_tests_identical` is false.

### Known defect D1 (not executed after the fix; disclosed rather than hidden)
- The replaced synthetic test `R5ObserverTests.test_dense_partial_top_and_wrong_footer_rejected_before_full_target` paints only `'  <t> Toggle chart/table'` on row 37. The retry's tightened full-row hint clause correctly rejects that.
- **Required one-line correction to the patch text, not yet re-run:** change `paint(37,'  <t> Toggle chart/table')` to

```python
paint(37,'  <t> Toggle chart/table       <ctrl-r> Run                 <ctrl-c> Quit'+' '*47)
```

- **Status:** unexecuted. That row is the actual final hint row, which the retained frames confirm. The parent or the fresh review must apply this correction and re-run the focused tests before any live application.

## Root-executed diagnostic runner (`go_pagenav_runner.py`; never executed by me)

**Planned argv**, run under root's bounded supervisor with cwd at the worktree root, `TMPDIR`/`GOTMPDIR`/`CLAUDE_CODE_TMPDIR` set to `/home/ajs/.cache/pb-phase7/tmp`, and the lock free:

```sh
/home/ajs/.cache/pb-phase7/harness-venv/bin/python -E -s \
  /home/ajs/.cache/pb-phase7/promql-geometry-implementation-r1/go_pagenav_runner.py \
  --go /home/ajs/.cache/pb-phase7/baselines/pb-go \
  --out /home/ajs/.cache/pb-phase7/promql-pagenav-go-r1
```

`--plan` prints the derived case without taking the lock, preparing the helper or starting a child.

**Pins checked before and after the capture:**
- bench `e03e1d2b`, `phase7.py` `dc7574f0`, workloads `04404c96`, `phase7_spawn.c` `0279d5fb`;
- `promql.go` `7332d30d`;
- `promql_geometry.py` `a6b11432`;
- Go `fae8411f`;
- helper `3b684c7b` and its JSON `69e1067e`.

**Case:**
- The case comes from `campaign_cases` with the exact A1-retry options and must hash to `b478e5cb…`.
- The diagnostic case (`promql-dense-1-pagenav-diagnostic-120x40-dark`, sha256 `88247400fa9a3339d889d75fdcd26337644b7e9953350aa719fd5c411d9f54ba`) keeps the dense-1 fixture, argv and `ctrl-q`/`shift-tab`/`t` setup.
- After that come one key at a time: `page-down` ×3, `end`, `page-up`, `home`, then `ctrl-c`. That is 19 actions with a 45 s child timeout.

**Readiness (in memory only):**
- Table-view uses the corrected predicate.
- Each key action's own evaluation, just before the key is sent, records the stable prior footer row. It refuses to dispatch without a complete table frame.
- Each navigation checkpoint waits for output after the key, the complete-frame geometry, a footer row that differs from that baseline, and the existing 80 ms styled stability. No cursor or page value is predicted.
- A plan check against actual frames confirmed the logic: baseline accepted, unchanged footer rejected, a synthetic `16 | 256 rows` / `2/19` change accepted, stale hint bar rejected.

**Execution and output:**
- It takes the existing `FunctionalOwnership(LOCK)` first.
- Inside the lock it runs `require_seams`, resolves the helper with the no-build `cached_spawn(probe=True)`, then makes one `p7.sample` call through `bench.Capture`. The sample keeps wait4, termios restoration, config, requests, full frames and raw bytes, plus an archived `execute/` copy.
- It then runs `check_binding`, the helper fingerprint and re-resolve check, `p7.validate`, and the end pins.
- It writes only to the new `--out` directory and refuses an existing one.
- Its report has, per checkpoint, the footer row text, the parsed page/pages/cursor/total and the first data row (row 17), plus the cursor sequence.
- Failures are explicit (exit 1) and are never retried internally.

The observed step **must not** be adopted without separate review. An optional same-script Rust candidate role would be a separate root decision.

## Provenance gaps (paused before hashing)

Root's pause arrived right after the retry finished. These final file hashes were therefore **not computed**, and must be taken at freeze or release:
- `go_pagenav_runner.py` (edited after the build only to update its `promql_geometry.py` pin, which run 2 then checked);
- `build_and_test.py`;
- `proposed_test_class.py.txt`;
- `proposed_replacement_method.py.txt`;
- this doc.

Run 2's outputs are `build-stdout.txt`, `build-output.json`, `tests-*.txt` and `build-exit.txt`.

No acceptance is claimed. The next steps are a fresh independent review, the root-owned composition with Sol's patch, and root's actual preflight and Go navigation control.

## Final freeze (after root RELEASE)

### Two kinds of evidence
- **Readiness evidence** (run 2) is unchanged and preserved in `run-2/`: the build output, the run-2 patch `a37516d1…`, the fixture `12a14654…`, all `tests-*.txt` and the run-2 patched copies. It covers the boundary proof, the actual-frame tests, the controls, the 12 mutants and the full-suite baseline (18 failures plus 1 error from the guarded flock, identical to the live suite).
- **Failed-test fix (D1)** is the only change after run 2. It corrects one line in the proposed `R5ObserverTests` synthetic test, which now paints the full actual hint row:

```diff
-        footer_row(34);paint(35,'└'+'─'*118+'┘');paint(37,'  <t> Toggle chart/table')
+        footer_row(34);paint(35,'└'+'─'*118+'┘');paint(37,'  <t> Toggle chart/table       <ctrl-r> Run                 <ctrl-c> Quit'+' '*47)
```

  The new string is 120 characters and character-identical to the actual Go final row 37. The diff is retained at `final/run2-to-final-test.diff`.

### Focused run (`finalize.py`)
- One attempt under the same guard and venv: exit 0, empty stderr, 0.54 s, no guard trips, tracked files unchanged. No retry was needed. There was no raw-prefix replay and no full suite.
- It ran only the affected test:

| Predicate bound | Result |
| --- | --- |
| new (final patched bench) | passes |
| old (live) | fails: the H−6 layout is accepted |
| no-hint mutant | fails: the stale chart hint bar is accepted |
| hint-substring mutant | passes |

- The hint-substring mutant survives this synthetic test. It is killed only by `half-painted-hint-row` in `A1RetryPromqlGeometryTests`, as shown in run 2.

### Final patch: `final/promql-geometry.patch`, sha256 `bbdd22270629eb2c9db5b7f29aba87cf88232b0215f200fecc5fdefc27c6a486`
It is regenerated against the live files:

| Patched file (`final/patched/`) | sha256 | Compared with run 2 |
| --- | --- | --- |
| `harness/phase7_bench.py` | `4262df14…00fe` | unchanged |
| `harness/test_phase7_bench.py` | `a5eb4be8b722b2bcd7fc9949f27d8fbbf23bb54d9b9408d4658e9eca9b8fa689` | changed by D1 |
| `harness/contracts/phase7.md` | `8c157103…b0f4` | unchanged |
| `docs/phase7-measurement.md` | `67633576…7729` | unchanged |

The test AST still changes only `A1RetryPromqlGeometryTests` and `R5ObserverTests`.

The root-level `promql-geometry.patch` and `patched/` are the run-2 copies and are superseded by `final/`.

### Not re-run after D1
- The rest of the new class, which does not depend on the changed line.
- The full suite. The patched full-suite count is expected to drop to the live baseline of 18 failures plus 1 error, but that is unverified.

The remaining source, runner and doc hashes are recorded in `final/freeze-hashes.txt`.
