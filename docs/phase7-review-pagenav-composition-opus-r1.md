# Phase 7 review: root Go page-navigation control and the A1 geometry + fan-out composition (Opus R1)

Scope: a fresh, independent review of two things:
- root's actual Go page-navigation control (`promql-pagenav-root-preparation-r1/`, `promql-pagenav-go-r1/`);
- the still off-tree composition `a1-geometry-fanout-composition-r1/` (`composition.patch` `eb9fe2fd…6ff0e`).

I read these in full:
- `docs/phase7-review-promql-geometry-final-opus-r1.md` (`6fd12b22…1877`, as given);
- `docs/phase7-review-a1-fanout-opus-r1.md` (`ef8e9cac…8892`);
- `docs/evidence/phase7/a1-fanout-review-disposition.json` (`4b7e720f…6e03`);
- `docs/phase7-a1-composition-proposal.md` (`da9a208d…a69d`);
- the runner, the whole composition patch, the root receipts, and the Go/bubble-table pagination source.

What I did not do:
- I edited no live source, harness, test, contract or doc, and made no commit. The only outputs are this file and the new directory `/home/ajs/.cache/pb-phase7/review-pagenav-composition-opus-r1/`.
- I used no native child, helper, CLI, PTY, probe, compiler, build, network, common lock or nested agent, and made no Rust run. I made no performance or parity claim.
- I did not repeat the exhaustive 5,397-prefix sweep or any full historical suite. The full composed suite was not run either; it stays a root gate.

## Verdict

| Item | Disposition |
| --- | --- |
| **(a) Capture integrity and scoped observed step** | **Accepted.** The capture is causally clean and the predicates are not tautological. It establishes that, for the pinned Go reference at 120×40, PgDn/PgUp move the cursor by exactly **15** rows, using bubble-table pagination. The scope is narrow; see the step section and L1/L2. |
| **(b) Current composition, excluding any step change** | **Accepted as source** for root's live copy. There are no blocker findings. One medium durability item (M1) should be closed before root claims fan-out R2/R3 durability; three low wording items follow. |
| **(c) Outstanding root gates** | **Open:** the live copy, the root full suite, full rebinding, the step-15 implementation and its review, and a fresh actual 24-role preflight. See the end of this document. |

## My execution (two guarded pure runs; one attempt each; no retry)

Both runs used `harness-venv/bin/python -E -s -B` with `timeout 580`.
- **Guard:** `rguard.py` (`5c56218b…a565`) is installed before any harness import. It blocks:
  - processes, exec, kill, wait and pidfd signals;
  - PTYs, sockets, ctypes, and flock/lockf;
  - any write, rename or unlink outside my directory.
- **Backstop:** a 540 s alarm.

| Script | Exit | stderr | Wall | Guard trips | Tracked identities |
| --- | --- | --- | --- | --- | --- |
| `review.py` `793d5d4e…24b8` | 0 | empty | 22.9 s | 0 | 102, unchanged |
| `review2.py` `56192369…be7e` | 0 | empty | 3.8 s | 0 | 39, unchanged |

**Outputs:**
- `review-output.json` (`6e5d825c…6e55`) and `review2-output.json` (`dc6d103e…a07b`);
- `tests-*.txt`;
- `artifact-hashes.txt`, which hashes every file I wrote.

**Retained failure.** In run 1, the `B_case_map` check failed with `FileNotFoundError`. The cause: a composed module loaded from `proposed/` resolves `ROOT/harness/phase7.py` (`phase7_bench.py:406`), and that file does not exist there. This is an artifact of loading the module off-tree, not a defect. Run 2 rebound `ROOT` to the live checkout and the check passed.

**Processes:** none were left.

## (a) Root actual control: capture integrity

### Identities (equal to the claimed values)

| Item | sha256 |
| --- | --- |
| plan | `2e379878b2fe2dd3be889335328d2be0cf1fb67448e6171193d7ef154d16d518` |
| launcher | `71c312ea739cf76c911a6e994a5ae3e86f33e79cbe1fa5cacdb4c9b05a8a9adb` |
| runner | `afd54e06baa7f0531a7581d722c6537a30713f3b7d03f61758c2a969a457915f` |
| actual `report.json` | `010881265fb5a977b80ec47e04ae1a08e8d8c4e849a50d966c16205dda17d3c4` |
| `reference/session.json` | `719c1f870183792d7965ae9031cd9ec801e61c7d57f5023baf41c155f792fd0b` |
| `reference/execute/session.json` | `7769f3e53c14527b00f6e83e6b811f460ad3ab445b9515f56ca5d4ec74d0d5b3` |
| `stdout.bin` (both copies; 94,971 B) | `93527b4aba147b3e8530ec8e224a8646540bfe489c7873c92f0a327c85f0a322` |
| `quiet-ack.json` | `41552d80…a82359` |
| `coordination/receipt.json` | `15e3699f…d8088` |
| `supervision/report.json` | `284d672d…e93` |

**Bindings:**
- The ACK binds the plan, launcher, runner and the geometry review (`6fd12b22…`).
- The report's `runner_sha256` equals the file. `pins` equals `pins_after`, and all 9 pins match disk now.
- Every raw-artifact hash in the receipt matches disk.
- The two session files have equal common fields; the reference copy adds only the `sample` envelope.
- The coordinator's identity record contains `phase1`–`3`, `run.py`, `cases.py`, the pyte/wcwidth packages and the runner/geometry bytecode. The live `phase1`–`3`, `run.py` and `cases.py` hashes are recorded in it. This closes geometry **L3**.
- Receipt `actual_helper_checks` binds the resolved helper path, ELF hash and provenance. This closes **L2**.
- **L1** (run before composition) was honoured.
- **L4** (end receipt on post-sample failure) was not exercised, because no failure occurred.

### Case, inputs and causal clock [executed]

**Case rebuild:**
- `build_case()` reproduces source `b478e5cb…` and diagnostic `88247400…`. The session's `case` and the plan's 19 actions equal the rebuilt case.
- No navigation wait carries any footer token, so **no navigation value is predicted**.

**Inputs:** all 10 inputs are byte-exact. They are `11`, `1b5b5a` and `74`, then page-down `1b5b367e` ×3, end `1b5b46`, page-up `1b5b357e`, home `1b5b48` and ctrl-c `03`. Each was dispatched after its evaluation.

**Clock order:** for every navigation step, the order is strict:
1. prior ready;
2. key evaluated (the baseline is recorded here);
3. dispatch;
4. first match, which equals stable-since;
5. ready, at least 80 ms after stable-since;
6. next key evaluated.

Dispatch to first match took 9.0–24.7 ms.

**Recorded offsets:** each navigation checkpoint's `end_offset` equals the next key action's evaluation offset (for example, 76244 and 76244). So **no byte arrived between ready and the next dispatch**. Each next baseline also equals the prior checkpoint footer.

### Independent raw replay [executed]

I fed the raw stream into a fresh `p7.Terminal(120, 40, 'dark')` at every code-point-complete boundary and at every recorded offset.

**Reconstruction:**
- All 9 recorded checkpoint frames are reproduced exactly at their `end_offset`, and 8 of 9 also at `first_offset`. The exception is `setup-ready`, an anchor whose frame is stamped at the end of its stability window.
- The replayed final frame equals `final_frame`.
- There were zero stub transients.

**Readiness evaluations:** all 23 recorded readiness evaluations were recomputed from the replayed state:
- the recorded tty/trace;
- `useful`;
- output-after-action;
- the swapped-in geometry;
- for the navigation checkpoints, footer≠baseline.

The recomputation found **0 mismatches**. For the navigation checkpoints that is 10 evaluations: 4 rejections (no new bytes) and 6 acceptances.

**Settled results:**
- The settled checkpoint footers are 1, 16, 31, 46, 256, 241 and 1. The footer page always equals `(cursor−1)//14+1`.
- Each frame shows 14 contiguous data rows (4 on page 19), whose first value is `(page−1)·14`. The cursor row is visible.

**Transient states.** The byte-level replay also exposes transient footer states *inside* each repaint: 2 to 6 per key, 19 in total. Examples are `2/19 … 1 | 256 rows`, `2/19 … 11 |`, `3/19 … 36 |`, `19/19 … 246 |` and `1/19 … 41 |`.
- `review2` proves that each one equals `new[:k] + old[k:]` for the footer row. They are a left-to-right overwrite of the old row within one 104–125-byte repaint.
- **None** falls on a recorded read boundary.
- The settled row completes about 70 bytes before the checkpoint's first-match offset.
- So they are paint artefacts, not application states, and the reported values are the settled ones. See L1 for what this means for readiness claims.

### Non-tautology controls through the runner's own `NavigationReadiness` [executed, actual frames]

| Control | Result |
| --- | --- |
| baseline taken on the stable table-view frame | accepted, with the baseline row `1/19 … 1 \| 256 rows` |
| no new bytes after the key | rejected |
| new bytes repainting an identical footer | rejected |
| actual nav-1 first-match prefix | accepted |
| no baseline recorded | rejected |
| baseline on a chart frame (no table footer) | refused (it never dispatches blind) |

### Restoration, configuration and lifecycle [executed reads]

- `config_before == config_after`; `termios_restored`, and before equals after.
- The transitions are alt-enter, then before-alt-exit, then alt-exit (1049).
- `exit 0`; no `failure`, `exec_error`, native cleanup issue, survivor or unreaped child; no unsupported controls.
- **Requests:** exactly `query_range`, `names` and `home`, with the fixture auth. All were received about 239 ms before the first input, and `validate_issues` is `[]`.

### Root supervision and cleanup [read and cross-checked]

**Timeline:**
- ACK issued at 16:29:17Z. Root started at 16:29:43.643051Z, and the supervisor launched at 16:29:43.702860Z, 26.7 s after the ACK and within the 16:31:17 start deadline.
- Cleanup finished at 16:29:47.493513Z, inside the 16:34:17 hard end. The root total was 3.850467 s before the final write.

**Supervisor `events.jsonl`:**
- top exited with status 0, and the reserved group was then SIGKILLed (the reserved group, not a stray child);
- the reap returned 0;
- `echild_verified`, no children afterwards, 0 recorded descendants;
- verdict `ok`, exit 0.

**Lock:** state `free` before and after (passive inspection, same inode). The 98 supervisor hashes are equal before and after, and the coordinator's identities-before equals identities-after.

**Errors:** every receipt has `errors: []`.

## Does this establish step 15 at 120×40?

**Yes, narrowly.** For pinned Go `fae8411f…`, dense-1 (256 rows), dark, 120×40, single-key writes from page-aligned cursors:
- PgDn moves the cursor by +15, three times out of three;
- PgUp moves it by −15, once out of once;
- End goes to row 256 and Home to row 1.

**Uniqueness:**
- Under bubble-table v0.19.2 semantics (`pagination.go:54-102`: next or previous page start, `pageLast` = first row of the last page), **P=15 is the only page size that reproduces** `[1,16,31,46,256,241,1]`.
- P=14 gives `…,253,239,1`; P=16 gives `…,49,241,225,1`.

**Source explanation (read, not run):**
- In table focus, keys go straight to `m.table.Update` (`promql.go:1122`).
- The Update-time page size is `promqlPageSize(m.height, bh)`, where `bh` is the actual bottom-bar height (4 at 120 columns). It is set only on `WindowSizeMsg` (`:505-506`), which gives 40−13−4−3−5 = **15**.
- `View()` has a value receiver (`:1372`). Its `renderResultsTable` page size, `tableHeight−5` = 14 (`:1311-1319`), is therefore never persisted. That is why 14 rows are rendered per page while the cursor steps by 15.
- The bench's `page = max(1, max(3, max(6, H−13−3)−3)−5)` (`phase7_bench.py:298`) reproduces `promqlPageSize(H, 3)`, which is **16**, the hard-coded `bottomH=3`.
- Consequence: the first sustained target `257 | 256000 rows` is unreachable for Go, which shows 241 after 16 page-downs.

**What the control does not establish (source inference only):**
- **(L2)** End and PgUp at a non-aligned total. 255 = 17·15, so a last-row model and the bubble-table first-row-of-last-page model coincide at 256 rows. At 256,000 rows (255,999 mod 15 = 9) they diverge: 255,990 versus 255,999 (zero-based). The source (`promql.go:1122` → `pageLast`) supports 255,990, which is exactly the bench's existing End formula `((total−1)//page)·page`.
- Burst writes of 17–18 page-down sequences per write.
- Light theme.
- The 256,000-row case. The page size does not depend on the row count in source, and `bh` comes from the identical startup state.
- Any other height or width.
- Rust behaviour (`src/tui/promql.rs:3389` mirrors Go via `page_size_for(height, height_of(&bottom))`; not run).

**How narrowly the bench may change from 16:**
- Change only `page` in `dense_case`'s sustained branch, to the observed constant **15**, and only when `tuple(size) == (120, 40)`. Fail closed (`ValueError`) for any other size, like the existing `metric_points` precondition.
- Keep all target arithmetic unchanged: forward ceil bursts, End = `((total−1)//page)·page`, and the reverse ceil bursts.
- Do **not** generalize to `promqlPageSize(H, 4) = H−25`. The bottom-bar height depends on width and startup state, and no other geometry was observed.
- No readiness, geometry, wait-shape, fixture, metric-point or other-case change.

**Resulting targets** (pure arithmetic, checked):

| Phase | Bursts | Zero-based row | Footer token |
| --- | --- | --- | --- |
| `series-forward-1` | 18 | 270 | `271 \| 256000 rows` |
| `series-forward-2` | 17 | 525 | |
| `series-forward-3` | 17 | 780 | |
| `series-last` | | 255,990 | `255991 \| 256000 rows` |
| `series-reverse-998` | 17 | 255,735 | |

- The maximum burst is 18.
- Every forward (1–31) and reverse (998–968) target lands in its intended series.
- Only the 4 `promql-dense-1000-sustained` definitions change (dark and light, ×2 policies). Their hashes then differ from the composition map's `after` values, so root must re-derive them.

### Recommended step-15 scope (separate change and separate review; not applied here)

**Source:** `harness/phase7_bench.py` `dense_case` only. Add the size guard and `page = 15`, with a comment citing report `010881…` and this review.

**Tests** (in `harness/test_phase7_bench.py`, durable):
1. **Sustained 120×40 case arithmetic:** burst counts, selected rows that are multiples of 15, the tokens above, and series indices. This test kills 14 and 16.
2. **Fail-closed construction:** sustained construction at any size other than 120×40 raises the new `ValueError`.
3. **Durable actual evidence:**
   - Retain the Go navigation stdout plus the checkpoint offsets and frame digests as `docs/evidence/phase7/a1-pagenav-go.json.gz`, bound to report `010881…` and session `719c1f…`.
   - The test replays the stream to the 7 checkpoint frames and the cursors `[1,16,31,46,256,241,1]`.
   - It also asserts that the bubble-table model with the bench's `page` reproduces exactly that sequence, and that P=16 does not.
4. **Optional:** one actual mid-row transient frame (for example offset 80013, `36 | 256 rows`) passes geometry for its own token. This documents L1. No geometry change.

**Docs:** in the contract and measurement doc, replace "the sustained page step is not changed until an actual Go navigation control is reviewed" with the observed step 15 at 120×40 and the source-only scope (End at non-aligned totals, bursts, and Rust).

**Runner:** the runner is pinned to the pre-composition bench. A re-run would need a reviewed re-pin, and none is needed.

## (b) Composition review (excluding any step change)

### Provenance and AST [executed]

**Patch and post-images:**
- The post-images equal the claimed hashes: bench `9bbab67c…1239`, tests `ba2a8152…5776`, contract `67f241d0…a874`, measurement `b51e891f…3b40`.
- The `difflib` unified diff from live to the post-images is **byte-identical** to `composition.patch`.

**Bench AST** (top-level definitions; independently recomputed):

| Version | Changed definitions |
| --- | --- |
| composed | `dense_case`, `dense_table_geometry`, `editor_case`, `error_case`, `observation_issues`, `startup_fanout`, `startup_fanout_issues` |
| fan-out (Sol's `phase7_bench.proposed.py`) | the same list without `dense_table_geometry` |
| geometry (`final/patched`) | `dense_table_geometry` |

- The composed changes are exactly the disjoint union. Each definition is AST-equal to its source, and all non-definition statements are equal across the four versions.
- The composed `dense_table_geometry` is AST-equal to `promql_geometry.py`, which is what the actual control swapped in. So the control ran the exact predicate being composed.

**No loosening (all AST-unchanged from live):** `Readiness`, `Phases`, `Capture`, `FunctionalOwnership`, `FunctionalGuard`, `causal_requests`, `resize_policy_issues`, `campaign_cases`, `sql_case`, `sql_resize_geometry`, `raw_observed_offset`, `check_binding`, `check_sample_helper`, `source_snapshot`, `cached_spawn`, `extract_metrics`, `retained_sample`, `finite_number`, `Options`, `main` and `pool_definition`. `phase7.py` (validate/compare/ordered contract) is untouched.
- The only request-order relaxation is the reviewed `initial_fanout_*` metadata, now additionally gated by `startup_fanout_issues` inside `observation_issues`.
- Readiness, auth, method, body and lifecycle are unchanged.

**Test AST:**
- Relative to live, only `R5ObserverTests` (exactly `test_dense_partial_top_and_wrong_footer_rejected_before_full_target`), `A1RetryPromqlGeometryTests` and `A1RetryFanoutObservationTests` change.
- Relative to the reviewed geometry post-image, only `A1RetryFanoutObservationTests` changes.

**Durability:**
- The new classes import only `gzip` and `hashlib`, and contain no cache, `/home/` or `pb-phase7` string.
- Their fixtures resolve through `bench.ROOT / 'docs/evidence/phase7/…'`. When loaded from `proposed/`, `ROOT` was the proposed tree and the fixtures were found there.
- The fan-out fixture `05c0ca62…e2ce` has `origins.report_sha256 = 1dcd24f6…`. All 4 role sessions are hash-bound, and each `session_sha256` occurs in the actual A1 report.
- The geometry fixture equals the reviewed `12a14654…`.

### Selected tests and mutants [executed, composed module bound]

| Module bound | Tests | Result |
| --- | --- | --- |
| composed | 2 geometry + R5 synthetic + 2 fan-out wiring | **5/5 pass** |
| no `issues.extend(startup_fanout_issues(...))` (only `observation_issues` differs) | 2 fan-out | **2 failures** (killed) |
| `startup_fanout_issues` without the `initial != requests[:count]` prefix-order clause | 2 fan-out | **pass (survives)** |
| `causal_requests(result, before)` instead of `before − 1` (accepts an equal-instant receipt) | 2 fan-out | **pass (survives)** |
| missing-first-input-dispatch guard removed | 2 fan-out | **pass (survives)** |

I did not repeat the old-geometry mutant. The lane's `tests-mutant-old-geometry.txt` (3 failures) and the prior review's 12-mutant evidence stand.

### Case map [executed, run 2]

- For both the primary and resource-attribution profiles, I rebuilt 24 of 26 cases per profile with the A1 options (long-idle excluded) on live and composed.
- 14 change and 10 are unchanged. The changed set is dense-1/100/1000/1000-sustained, sql-editor, sql-query-error and promql-query-error, dark and light. The unchanged set is the sql-windowed cases.
- The changed cases differ only by the added `initial_fanout_*` keys.
- All 24 `before` hashes, `after` hashes and `changed` flags match `composed-case-hash-map.json` (`0b54725c…`).
- I did not rebuild the 4 long-idle cases or independently re-derive the 90 argv records.

### Geometry L5–L7 documentation resolution

| Item | Resolution |
| --- | --- |
| **L5** | **Resolved.** The measurement doc now says only the pure replay test catches the stub `IndexError`, and that the live path would abort. The old sentence is gone. |
| **L6** | **Resolved in substance.** Both docs declare the geometry for the 120-column dense profile only. Wording precision is covered under L3 below. |
| **L7** | **Resolved.** The contract quotes the exact full-row hint pattern, which is identical to the code. Both docs explicitly supersede the H−6 and "unchanged" statements, and the historical text is retained. The run-2 `unchanged_rejected` evidence label is off-tree and uncited, so no action is needed. |

## Findings

No blocker.

**M1 — the in-tree fan-out tests do not pin the gate's own branches (durability).**
- **What:** three semantic mutants of `startup_fanout_issues` survive every in-tree test:
  - dropping the prefix-order comparison;
  - accepting an equal-instant receipt;
  - removing the missing-dispatch guard.
- **Why it matters:** Sol's 13 focused tests cover them, but they stay in an off-tree cache file. Root's full suite (fan-out R3) will therefore never exercise these properties on the composed tree.
- **Fix:** before claiming durable R2/R3 closure, port at least these three focused cases into `A1RetryFanoutObservationTests` on the existing durable fixture, then rerun the mutants:
  - a receipt exactly at the dispatch instant is rejected;
  - an array/receipt-order inversion is rejected;
  - a missing dispatch fails closed.
- **Scope:** this is not a defect in the composed code, which is AST-equal to the reviewed source.

**L1 — complete-frame geometry does not prove paint completion for in-place footer updates.**
- **What:** the actual stream contains 19 mid-row overwrite states. 17 of them pass `navigation_footer`, and all 19 pass the composed `dense_table_geometry` for their own token. Some are page/cursor-inconsistent, for example `2/19 … 1 | 256 rows`.
- **Why the captured values are still sound:** they are protected by the 80 ms styled stability, the strict next-baseline equality and the zero bytes between ready and the next dispatch, not by geometry.
- **Effect on the bench:** a false ready would need an 80 ms stall mid-row, so readiness is not falsely satisfied. A `first_ever_match` stamp could land on a partial row that already reads the final text.
- **Docs:** the doc claim that a "stale or half-painted bar fails" is correct only for the toggle's H−3 hint row. Step-15 text must not claim that geometry proves navigation completeness.

**L2 — step-scope limits** (see the step section above).
- The control cannot discriminate End/PgUp semantics at non-aligned totals, and it does not cover bursts, the light theme, 256,000 rows, other sizes or Rust.
- Source supports the bench's existing End shape. These limits must be stated wherever 15 is adopted.

**L3 — L6 wording is slightly over-strong.**
- "Other widths … fail closed (timeout), never pass" is a source inference: no width guard exists, and `--size` accepts 80–240 columns.
- "The only dense width in the 52 cases" is true of the default campaign only.
- **Suggested wording:** "not established at other widths; no width guard; a differing layout is expected to fail closed".

**L4 — replay-scope wording.** The measurement doc says the "hash-pinned retained Go/Rust streams test every code-point-complete prefix". The in-tree test covers only the two dense-1 dark roles (reference and candidate), not all 8.

**Notes:**
- The runner's `summary.complete` means geometry-complete footers, not paint-complete ones (see L1).
- One raw byte before the nav-1 first-match offset is already acceptable: the trailing bytes after the footer are controls.
- The runner and geometry bytecode in `promql-geometry-implementation-r1/__pycache__` is readable under `-B`. It was recorded by root's coordinator, so it is bound.

## (c) Outstanding root gates

1. **Live copy.** Root copies the 4 post-images and 2 fixtures, then takes a fresh full-source freeze. The runner then refuses to start (L1, by design).
2. **Root full suite** on the composed tree (fan-out R3), ideally after M1. No clean full-suite claim exists; the known guard baseline is 18 failures and 1 error.
3. **Full rebinding.** Regenerate the 52-case and 90-argv bindings (fan-out N2). After step 15, the 4 sustained hashes change again.
4. **Step 15.** Implement the step-15 change scoped above, with its tests and docs, and have it independently reviewed.
5. **Preflight.** A fresh actual root 24-role Go/Rust preflight with matching dense table-view and sustained navigation. Until then nothing is accepted at runtime, and there is no performance or parity claim.
6. **Still open from earlier lanes:** L5 (the live path's `IndexError` abort) is pre-existing and unchanged. Fan-out N1 (a late legitimate receipt invalidates with no retry) still applies at preflight.

## Processes

Both of my scripts exited 0. A final `ps` showed no review, runner, Go or helper process from this lane.
