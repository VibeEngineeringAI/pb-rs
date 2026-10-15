# Phase 7 review: A1 composition + step-15 successor (Opus R3)

Scope: a fresh, independent, narrow review of the applied successor described in `docs/phase7-a1-composition-step15-implementation-r2.md` (`641eaf6e…21cd`). It is not a continuation of the failed R2 attempt. That attempt hit EDQUOT on the Claude task stdout before it reviewed anything, and it left no review document, acceptance or test result.

Read in full:
- `docs/phase7-review-pagenav-composition-opus-r1.md` (`aaecaa48…983d`);
- the implementation handoff;
- `composition-to-successor.patch`;
- the live `dense_case`, `startup_fanout_issues`, `causal_requests`, `observation_issues` and `campaign_cases`;
- the new and changed test classes;
- the lane's `guard.py`, `make_fixture_r2.py`, `scoped_tests.py` and `rebind.py`.

What I did not do:
- I edited no source, harness, test, contract, fixture or prior doc, and made no commit. I deleted no tmp content.
- The only outputs are this file and `/home/ajs/.cache/pb-phase7/review-a1-step15-successor-opus-r3/`.
- I used no native child, helper, CLI, PTY, probe, compiler, build, socket, network, common lock or nested agent, and ran no Rust.
- I did not rerun the full suite, the exhaustive prefix sweep or the old Go runner.
- I make no performance or parity claim.

## Verdict

| Item | Disposition |
| --- | --- |
| Identity and provenance: 7 owned files, shared `phase7.py`/workloads/C, freeze, actual Go report and fixture | **Verified.** All values equal the claimed ones, before and after my runs. |
| Step-15 source change | **Accepted.** `dense_case` is the only changed bench definition. Reverting exactly the size guard and `page = 15` gives the composition AST. Target arithmetic is unchanged. |
| Durable page-navigation fixture and model | **Accepted.** It is byte-exact to the actual Go control, hash-bound to the report, session and receipt, and independently replayed. 14 and 16 are killed. |
| **M1** | **Closed for the three R1 mutants.** All three are killed. One residual Low test-shape point remains (N2). |
| **L1–L4** documentation | **Resolved.** |
| 52-case / 90-argv prospective map | **Reproduced** for all 90 records and 140 case entries, with 0 mismatches. These are static expected hashes, not runtime acceptance. |
| New findings | **No blocker or Medium.** Two Low findings: N1 (campaign-wide refusal at other sizes is undisclosed) and N2 (the M1 missing-dispatch "first and last" subtests are degenerate). |
| Root gates still open | Actual fresh 24-role Go/Rust preflight, with its runtime source and binary binding. Root's full suite is reported passed (see below); I did not re-run it. |

## My execution (two guarded pure runs, one attempt each, no retry)

How the runs were made:
- Both used `harness-venv/bin/python -E -s -B` under `timeout 580`, with TMPDIR, GOTMPDIR and CLAUDE_CODE_TMPDIR set to `/home/ajs/.cache/pb-phase7/tmp`.
- Stdout and stderr were redirected into my directory. The Bash tool's own stdout hit EDQUOT on every call, but each command did execute; I read every result back from the files.
- **Guard:** `rguard.py` (`6f6d0081…75a4`) is imported before any harness module. It blocks:
  - process creation, exec, kill, wait and pidfd calls;
  - PTYs, sockets, ctypes, and flock/lockf;
  - any write, rename, unlink or mkdir outside my directory.
- **Backstop:** a 540 s alarm.

| Script | sha256 | Exit | stderr | Wall | Guard trips |
| --- | --- | --- | --- | --- | --- |
| `review.py` | `14951b09…15c5` | 0 | empty | 89.7 s | 0 |
| `review2.py` | `8a62c085…cbaa` | 0 | empty | 1.4 s | 0 |

**Outputs:**
- `review-output.json` (`650eb161…4074`) and `review2-output.json` (`47f8c608…5f47`);
- `tests-live-scope.txt` and `tests-mutant-*.txt`;
- `artifact-hashes.txt`, which lists every file in my directory except itself and the last shell capture.

**Retained failure.** In run 1, my own `ast_patch_audit` section raised an `AttributeError`: my helper keyed `ast.Assign` nodes by `.name`. This was a bug in my script, not in the code under review. I corrected it in `review2.py` and ran it once; the `review.py` output is kept unchanged. A trivial `python3 -I` stdin helper (`s7.out`) produced nothing that I use.

**Processes:** a final `ps` showed no review, `phase7_bench`, `pb-go` or `pb-normal` process.

## Source identity (before = after)

These hashes were checked before and after both runs; `review.py` also checked them inside its own run:

| File | sha256 |
| --- | --- |
| `harness/phase7_bench.py` | `b874ac0a…0372` |
| `harness/test_phase7_bench.py` | `d760ccba…6d86` |
| `harness/contracts/phase7.md` | `075b1323…b36d` |
| `docs/phase7-measurement.md` | `65fadf0c…b901` |
| `a1-pagenav-go.json.gz` | `d4a02506…39df` |
| `a1retry-fanout-observation-pairs.json.gz` | `05c0ca62…e2ce` |
| `a1retry-promql-retained.json.gz` | `12a14654…67b6b` |
| shared `phase7.py` | `dc7574f0…a44fa` |
| workloads | `04404c96…6178` |
| `phase7_spawn.c` | `0279d5fb…31f4` |

**Repository files:**
- All 1,668 files listed in `final-source-freeze.json` (`f4ba9886…0adc`) were hashed before and after `review.py`, and none changed during the run.
- Two files differ from the freeze: `docs/evidence/phase7/resume-task-ledger.json` and `docs/phase7-combined-status.md`. Both are root's document ledger, which root is permitted to write.

**Patches and stages:**
- `final/` equals the live tree.
- The `composition-stage/` post-images equal the R1-reviewed composed hashes: bench `9bbab67c…1239`, tests `ba2a8152…5776`, contract `67f241d0…a874`, measurement `b51e891f…3b40`.
- A `difflib` regeneration reproduces all three patches byte for byte:
  - original→stage, which equals R1's `composition.patch` `eb9fe2fd…6ff0e`;
  - stage→live: `composition-to-successor.patch` `08183e23…86db`;
  - original→live: `original-to-final.patch` `46fbf56b…2cb9`.
- The two composition fixtures equal R1's `proposed/` copies.

## Step 15: source and model [executed]

**Source:**
- **Bench AST:** stage→live changes only `dense_case`. All non-definition statements are equal.
- The two added statements are exactly:
  - `if tuple(size) != (120, 40): raise ValueError('…established only at 120x40')`;
  - `page = 15`, which replaces `max(1, max(3, max(6, size[1] - 13 - 3) - 3) - 5)`, the formula that evaluates to 16 at 120×40.
- Removing the guard and restoring the old `page` expression gives an AST equal to the composition `dense_case`.
- The guard runs before the existing metric-points precondition, and the precondition still fires (`metric_points=15` at 120×40).
- The forward ceil bursts, End `((total−1)//page)·page` and reverse ceil bursts are untouched.

**Case delta (independently rebuilt from live and composition):** exactly 4 of the 52 profile cases change: `promql-dense-1000-sustained-120x40-{dark,light}`, each with and without `-resource-attribution`. Each differs only in:
- `actions` (139 actions in both versions);
- `bench.phases`, where only `series-scroll` phases change, phase names are equal and every non-series phase is equal.

The arithmetic matches R1's table:

| Value | Live | Composition |
| --- | --- | --- |
| forward-1 row / bursts | 270 / 18 | 256 / 16 |
| `series-last` row | 255,990 | 255,984 |

The other 48 cases have no difference at all.

**Mutants** (each AST-edited in one definition and run against the whole 9-test scope listed in the next section):

| Mutant | Result |
| --- | --- |
| `page = 14` | killed (2 failures: arithmetic and actual-model) |
| `page = 16` | killed (2 failures: arithmetic and actual-model) |
| size guard removed | killed (6 subtest failures) |
| width-only guard `size[0] != 120` | killed (the 120×39 and 120×41 subtests) |
| End = `total − 1` | killed (`255999 != 255990`) |
| forward bursts `floor` instead of `ceil` | killed (arithmetic test and the pre-existing R4 test) |

## Durable Go page-navigation fixture [executed, independent of the lane's test]

**Raw stream:**
- The stream is 94,971 bytes, sha256 `93527b4a…0a322`.
- It is equal to `reference/stdout.bin`, to `reference/execute/stdout.bin` and to `session.stdout_hex`.
- The live fixture is byte-equal to the lane copy.

**Origins:**

| Origin | Check |
| --- | --- |
| report | `010881…3c4`, equal to disk |
| session | `719c1f…0fd0b`, equal to disk |
| root receipt | `15e3699f…8088`, equal to disk, `errors: []` |
| runner | `afd54e06…57915f`, equal to `report.plan.runner_sha256` and to the value R1 verified |
| binary | equal to the session's (`pb-go`, `fae8411f…2314`) |
| source and diagnostic case hashes | equal to the report plan |

**Checkpoints (from the session's own data, not the receipt):**
- For all 7 checkpoints, these equal the session's values:
  - `first_offset` and `end_offset` match `session.decoder_offsets`;
  - the frame and cell digests match `session.frames`;
  - the first-match, stable and ready stamps match the session frames.
- Each key was derived independently: from the single session input dispatched between the previous ready and this first match.
  - The inputs are `1b5b367e`×3, `1b5b46`, `1b5b357e` and `1b5b48`.
  - They map to page-down×3, end, page-up and home, which equals the fixture's `key`.
- Stable-to-ready gaps are 81.6–109.6 ms.
- The receipt cursors agree.

**Replay:**
- I used one incrementally fed `p7.Terminal(120,40,'dark')` over ascending offsets. This is a different method from the test, which uses a fresh terminal per prefix.
- All 14 first/end frames reproduce their digests.
- The footer on zero-based row 32 reads cursors `[1,16,31,46,256,241,1]`, with pages 1/2/3/4/19/18/1 of 19. Each page equals `(cursor−1)//14+1`.
- `dense_table_geometry` accepts each frame for its own token.
- The final frame equals `session.final_frame`.

**Test model:**
- `test_actual_seven_checkpoints_and_bench_page_model` derives the bench step from the generated `series-forward-1` phase, which gives 270/18 = 15. It does not duplicate the constant.
- It asserts the bubble-table model with that step reproduces the observed sequence, and that 14 and 16 do not.

## M1 [executed]

**AST:** stage→live adds exactly three methods to `A1RetryFanoutObservationTests`. All existing members, including the two `observation_issues` wiring tests and `setUpClass`, are AST-identical. The only other test change is the new `A1PageNavigationTests` class. The new test code contains no `/home/`, `pb-phase7`, `.cache`, `subprocess` or runner reference.

**Live scope: 9/9 pass in 4.2 s.** The scope is:
- the 3 `A1PageNavigationTests`;
- the 5 `A1RetryFanoutObservationTests`;
- the pre-existing `R4Tests.test_dense_sustained_distinct_series_selection_and_single_request`.

**Mutants:**

| Mutant (`startup_fanout_issues` / `observation_issues`) | Result |
| --- | --- |
| `causal_requests(result, before)`, which accepts a receipt at the same instant | killed (assertion failure, exact-at-dispatch test) |
| prefix-order clause dropped | killed (assertion failure, non-prefix test) |
| missing-dispatch guard removed | killed as **4 `KeyError` errors**, not assertion failures |
| guard checks only `inputs[0]` (my added mutant) | **survives** (see N2) |
| `observation_issues` wiring removed | killed (both original wiring tests) |

**On the dispatch-guard kill:** this matches the handoff's disclosure. With the guard removed, a missing key crashes rather than being rejected explicitly. That is still a test failure, so it counts as a kill.

**Probe:** with a `NaN` dispatch instead of a removed key, the live gate returns the explicit `missing first-input dispatch boundary`. The guard-removed mutant returns the prefix issue instead. So a `NaN` variant would turn this kill into an assertion failure. This is optional.

**Precondition checked:** `milestones[0].request == 0`, which is inside the 3-request prefix. So the exact-at-dispatch edit is meaningful, and the non-prefix test asserts its own `len(initial) == count` precondition.

## L1–L4 documentation (contract and measurement, read in full)

| Item | Disposition |
| --- | --- |
| **L1** | **Resolved.** Both docs now confine the half-painted claim to the toggle's H−3 hint row. They state that geometry does not establish paint completion for in-place navigation footer updates. They attribute the actual values to 80 ms styled stability, strict next-baseline equality and zero bytes between ready and the next dispatch. No readiness code changed. |
| **L2** | **Resolved.** Actual evidence is scoped to pinned Go, dense-1 (256 rows), dark, single-key writes at 120×40. End/PgUp at non-aligned totals, bursts, the light theme, 256,000 rows and Rust are named as source-only extrapolations. The End formula is stated as unchanged and source-supported. No performance or parity claim is made. |
| **L3** | **Resolved.** The old "fail closed (timeout) and never passes" wording is gone (grep confirmed). The docs now say "Other widths are not established; `dense_table_geometry` has no width guard, and a differing layout is expected to fail closed", plus "the default 52-case campaign". The construction guard is explicitly distinguished from the geometry predicate. |
| **L4** | **Resolved.** The in-tree exhaustive prefix test is scoped to the two dense-1 dark roles. The eight-role evidence is cited as earlier and off-tree. |

The stale "page step is not changed until…" and "remains blocked on an actual Go…" sentences are gone. I found no new unsupported runtime claim; the one wording gap is N1.

## 52-case / 90-argv map [executed]

**Map identity:** `successor-case-hash-map.json` is `17ed96c0…2c2c`. Its `successor` source hash equals the live bench.

**Rebuild:**
- I rebuilt `campaign_cases` on the live module for all 90 records, from each record's recorded `actual_builder_options`. These are typed values, so the parser was not re-run; the parser and argv derivation were bound by earlier lanes.
- **Result:** 140 of 140 case hashes and every case-name set match, with 0 mismatches.
- Every record is 120×40.

**Profiles:**
- For the two full-functional-preflight profiles (52 cases), the composition-stage hashes equal the map's `composition` values and R1's `composed-case-hash-map.json` `after` values (52 of 52).
- 28 cases carry composition metadata changes, 24 are unchanged, and exactly the 4 sustained cases change for step 15.

**Static versus runtime bindings:**
- The case hash binds only static inputs: definitions, fixtures, actions, phases and `dispatch_source_sha256`. That last value is the live `phase7.py` hash, `dc7574f0…`, and it is the same for all 52 cases.
- Binary identity, Git HEAD, the `source_snapshot()` groups, quiet and lock receipts, and actual frames and requests are bound only at runtime by the preflight.
- These are prospective expected hashes. They confer no runtime acceptance.

## Findings

No blocker and no Medium.

**N1 (Low): every campaign at a non-120×40 size now aborts, and the docs do not say so.**
- **Cause:** `campaign_cases` builds `dense_case(..., sustained=True)` for every configured size (`phase7_bench.py:421`) *before* `--case` filtering. The new guard therefore raises for the whole campaign at any other size.
- **Executed comparison:**

  | Options | Live | Composition |
  | --- | --- | --- |
  | `tests.options(sizes=((100,30),))` | `ValueError` | 12 cases |
  | the same with `case_filters=('sql-editor-*',)` | `ValueError` | 1 case |
  | `(120,40)+(100,30)` | `ValueError` | 24 cases |
  | `phase7_profile.plan`'s exact `bench.Options` shape at 100×30 (the profiler never uses the sustained case) | `ValueError` | 12 cases |
  | the profiler shape at 120×40 | 12 cases | 12 cases |

- **Impact:** this fails closed and cannot produce a false acceptance. The default campaign, all 90 recorded argv sets and the planned 24-role preflight are all 120×40, and root's 130-test full suite passed.
- **Disclosure gap:** the docs say "Only `dense_case` sustained construction … raises `ValueError` otherwise". That wording does not tell a reader that `plan`/`preflight`/`run --size` and `phase7_profile --size` now refuse every other size outright.
- **Suggested fix:** one sentence in the contract and measurement doc, for example: "Because the campaign always builds the sustained case, any non-120×40 `--size` (including `phase7_profile`) is refused at construction." The alternative is a separately reviewed per-case skip. Neither blocks the preflight.

**N2 (Low): the M1 missing-dispatch test's "first and last input" subtests test the same event.**
- **Cause:** each retained `promql-error` role has exactly **one** input, ctrl-c `03`. So `inputs[0]` and `inputs[-1]` are the same action.
- **Consequence:** the handoff's "missing dispatch rejection on first and last inputs" overstates coverage. The guard's every-input clause is not pinned: my `inputs[0]`-only mutant passes all 9 tests.
- **Live code is correct.** On the retained `sql-editor` capture, which has 4 inputs, popping the *last* dispatch makes the live gate return the explicit rejection. The `inputs[0]`-only mutant raises `KeyError` instead.
- **Suggested fix (optional):** add one subtest on the `sql-editor` pair with the last input's dispatch removed (or set to `NaN`). This would kill that mutant. Otherwise, correct the wording.
- The M1 closure for R1's three named mutants is unaffected.

**Notes:**
- The ≥80 ms assertion in the actual-model test reads recorded stamps from the fixture rather than recomputing them. I confirmed those stamps equal the hash-bound session's frame stamps.
- The failed `make_fixture.py` attempt (wrong `runner_sha256` lookup) and its retained log do not affect the fixture. The fixture's `origins.runner_sha256` equals `report.plan.runner_sha256`.

## Root full suite (reported by root; not re-run by me)

**Root's report:** `/home/ajs/.cache/pb-phase7/a1-step15-root-full-suite/report.json` (sha256 `830baa55…0a5f`) records:
- 130 tests, success, 0 failures, 0 errors, 0 skipped;
- 219.78 s;
- `guard_trips: []` and `source_unchanged: true`.

**Running process:** at 16:59Z I saw root's `run.py` running with process, network and PTY blocked and the common performance lock prohibited. That is consistent with root's description.

I read only the summary fields and did not audit `bench.log`.

## Remaining root gates

1. **Optional before preflight:** N1 wording and the N2 subtest. Either change would need a re-hash of the touched files, and the N2 test change would need a re-run of the scoped tests. Neither changes case hashes.
2. **Preflight:** a fresh actual root 24-role Go/Rust preflight at 120×40, including matching dense table-view and sustained step-15 navigation. It must have runtime source, binary and HEAD binding against the prospective map. Until then nothing is accepted at runtime.
3. **Old runner:** the old Go control runner stays pinned to pre-composition source and must not be rerun without a reviewed re-pin.
4. **Still open from earlier lanes:** fan-out N1 (a late legitimate receipt invalidates the run with no retry) and the pre-existing live-path pyte `IndexError` gap.
5. **No performance or parity claim** follows from this review.
