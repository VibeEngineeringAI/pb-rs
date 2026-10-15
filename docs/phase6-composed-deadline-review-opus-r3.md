# Phase 6 review: composed P3 + P4 deadline normalization (Opus, round 3, executed)

Scope: a fresh, independent, **executed** review of root's final off-tree
composition of two already-accepted rules into one shared `harness/phase3.py`
change:

- the P3 body and lookahead entries, accepted in
  [Opus R2](phase6-combined-deadline-review-opus-r2.md) and by Sol
- the P4 `p4-deadline-header` whole-stream rule, accepted at source
  `ca32ffa8…` in [Opus R1](phase6-p4-header-deadline-review-opus-r1.md), with
  the v2 test amendments in
  [the proposal](phase6-p4-header-deadline-proposal.md) and
  [its evidence](evidence/phase6/p4-header-deadline-proposal-v2.json)

Composition root: `/home/ajs/.cache/pb-phase6/combined-deadlines-p3-p4-r1`. I
treated it, the P3 r2 and P4 v2 caches, both worktrees and every archive as
read-only.

This review covers the composition and the prior required amendments only. It
is **not** whole-phase, performance or release acceptance. It is not a native
replay, and it does not turn any original failing report into a pass.

## Verdict

**ACCEPT** both variants for immediate coordinated live application, each to
its own base, followed by a fresh focused native replay of the three cases.
**No blocking findings.** No source, rule or test change is required.

| Variant | Patch | `phase3.py` | `test_phase3.py` | `test_phase4.py` |
| --- | --- | --- | --- | --- |
| phase6 | `458036a6bf391ba758a641c8eafe2893bebc168aedd2c1a31e4967e8afde1ba8` | `01668670868218625371c386820ea18107c29b24a667b684ed1783be3f18eadd` | `812e96091659d8f8e4fabdf4bdeae537d4f2dc153f41a2cb0faa59289bf2e434` | `94f2d11cd3a1a6a5a815bb0f1676f9c800c4cd804299136957244cbea8a2eaa1` |
| phase7 | `b7b9e37c6e19442cd0c8c162218b4d349f4f118f8204095210c7c2fe645346e2` | `33be7605b2e3ea44712a8118b8909b129d00ef3f46c7f4d3932a522351e2b7bd` | `ef1a47dc263d18ed94fac7577a7e2597699973e74a4b42d3cbdc598cca9fab59` | `b1ed46ac5530a344f408303a1c09e4f4f0eaaaa50c2304b7f7984a592b7c34fb` |

Live bases at review start and end:

| Variant | `phase3.py` | `test_phase3.py` | `test_phase4.py` |
| --- | --- | --- | --- |
| phase6 | `7f8deaf0…` | `f11f07cd…` | `37782465…` |
| phase7 | `39d16e2b…` | `0bea853f…` | `1404a43d…` |

Application must stop if any live base hash differs from this table.

## Method

- **Shell.** Every Bash call hit EDQUOT on `/tmp` capture. All output went to
  `logs/` in my cache and was read from there. Nothing was moved to or edited
  in `/tmp`. `TMPDIR`/`HOME` for children pointed into the cache, and
  `PYTHONDONTWRITEBYTECODE=1` with `python3 -I -B` (3.14.7).
- **Writes.** Only this document and
  `/home/ajs/.cache/pb-phase6/composed-deadline-review-opus-r3/`. I made no live
  edits, no other-worktree writes and no commits. I spawned no agents.
- **No natives.** No Go or Rust CLI, build, fixture, lock, profiler, timing or
  long-deadline run. The only processes were `git apply`, GNU `patch --dry-run`
  and my own guarded Python children.
- **Guard.** Every test and mutant child installed an audit hook before
  importing any harness module. The hook raises on `subprocess.Popen`,
  `os.posix_spawn`, `os.exec`, `os.system`, `fork`, socket creation, connect,
  bind and getaddrinfo, `flock` and `lockf`. Guard events in targeted runs,
  mutants and reductions: **zero**.

| Script | SHA-256 | Purpose |
| --- | --- | --- |
| `scripts/structure.py` | `79793a69…` | Hashes, independent LF-only regeneration, exact apply, AST, method inventory, cross-variant, dependency provenance (198 checks) |
| `scripts/child.py` | `dc4f9a3a…` | Guarded unittest child with an import-path check |
| `scripts/mutants.py` | `7909dae4…` | Copies staged trees; runs targeted, full-guarded and 13 mutants per variant, normal and `-O` |
| `scripts/reduce.py` | `93fc875c…` | My R2 P3 reduction, repointed to live `7f8deaf0` vs composed `01668670` |
| `scripts/reduce_p4.py` | `ecc3538e…` | P4 archive reduction, actual-data controls, cross-case checks, synthetic matrix |

Results: `results/structure.json` (`ab71f6fc…`), `results/mutants-summary.json`
(`b5e6effe…`), `results/reduce.json` (`b246645e…`), `results/reduce-O.json`
(`144ae079…`). The `-O` reduction file differs only in its `optimize` field.

## Executed results

### 1. Identity, one patch per base, exact application

For each variant, the live bases, root staged files and root patch hash equal
the tables above.

- **Regeneration.** I rebuilt the diff from live and staged bytes with
  unconditional `split('\n')`. It is **byte-identical** to each root
  `combined.patch`. Neither patch contains a CR. Both `phase3.py` bases are
  free of Unicode separators. `test_phase3.py` contains U+2028 in both bases,
  and LF-only splitting handles it.
- **`git apply`.** On fresh cache copies of live bytes, with
  `GIT_CEILING_DIRECTORIES` set to my cache, `git apply --check --verbose` and
  `git apply --verbose` both exit 0 with "Applied … cleanly". There is no
  offset or fuzz text. The results are byte-equal to root `staged/`.
- **GNU `patch -p1 --dry-run --verbose`** succeeds with no offset or fuzz:

| Variant | `phase3.py` | `test_phase3.py` | `test_phase4.py` |
| --- | --- | --- | --- |
| phase6 | 1474 | 352, 364, 481 | 200 |
| phase7 | 1474 | 353, 365, 482 | 204 |

- **F7 closed.** Each base gets one regenerated LF-only diff. No sequential
  application of the P3 and P4 patches and no fuzz were used.

### 2. Accepted pieces are unchanged

**Source, P3 part.** Live → P3 staged is exactly two insertions, the accepted
lookahead and body `bare_context` entries, in both variants.

**Source, P4 part.** P4 v2 `phase3.before`→`proposed` (`7f8deaf0`→`ca32ffa8`)
is exactly one insertion.

- Composed source = P3 staged + exactly that insertion, in both variants.
- For phase6, composed = `ca32ffa8` + exactly the two P3 lines.
- The block sits once, after the `timeout_cause` opt-in return and before
  `bare_context`.
- `normalize_timeout` source is byte-identical across the two variants (SHA
  `5af4384f…`).
- Root's anchor slice hashes to `07d14ebc…`, which matches the manifest.
  `difflib` aligns the same insertion one `return data` line earlier, giving
  `01a10d37…`. This is the same bytes rotated, not a difference.

**`test_phase3.py`.** The composed file is **byte-equal** to the accepted P3 r2
staged file (`812e9609` and `ef1a47dc`). Method inventory against live:

| Change | Methods |
| --- | --- |
| Removed | `test_bare_deadline_cause_is_limited_to_two_exact_text_messages` |
| Added | the renamed legacy-negative method, the two lookahead methods and the two body methods |
| Changed | no other method |

**`test_phase4.py`.** Live → composed is exactly one insertion. It is
byte-identical to the v2 class addition (`d190de93…`) in both variants.
`HeaderDeadlineNormalizationTests` is added; nothing else is removed or changed.

**AST.** Top-level count is equal, and the only changed top-level definition
is `normalize_timeout`, in both variants.

**Per-phase guards.** The phase6↔phase7 opcode differences between the bases
are all preserved after composition. Those are the phase-7 interactive guard
lines (`deferred_boundaries() == []` and `historical_deferred_boundaries`) and
the `test_phase4` interactive test. Phase7 still defines
`historical_deferred_boundaries`. The only extra difference is the one inherited
blank line in F1.

**Dependency provenance.** Each variant's staged `phase1`, `phase2`, `phase4`,
`phase5`, `run`, `cases`, `test_phase5` and every `contracts/` file is
byte-equal to **its own** live worktree. Both `bin/pb-go` and
`bin/pb-go-pinned` link to the frozen `baselines/pb-go` (`fae8411f…`). This
closes the R2-F1 concern, in which the phase6 label ran Phase 7 dependencies.

### 3. Targeted guarded tests (my run)

I ran `DeadlineTierTests` (10 tests) and `HeaderDeadlineNormalizationTests`
(7 tests) on my copies of root's staged trees, with `PB_PHASE3_FIXTURE`
pointed at a nonexistent path.

- **Targeted:** 17/17 pass, normal and `-O`, in both variants. Zero failures,
  errors, skips and guard events. The import check confirmed `phase3`,
  `phase4`, `phase1`, `phase2`, `run`, `cases` and the test modules all came
  from the copied tree.
- **Full guarded test_phase3/4/5:**

| Variant | Run | Fail | Error | Skip |
| --- | --- | --- | --- | --- |
| phase6 | 101 | 0 | 18 | 1 |
| phase7 | 102 | 0 | 18 | 1 |

  Every error is a guard `PermissionError` (subprocess or socket), or the
  reference-check `report.md` that the guard prevented from being written. The
  skip is `FixtureTests.test_go_fixture_generate_parity`, because the fixture is
  absent. The same 19 native-dependent tests appear in both variants. All pure
  tests pass.

### 4. Causal mutants on the composed source

I ran 13 mutants per variant, each normally and under `-O`, against the 17
targeted tests. `-O` results were identical to normal in every case. Apart from
the one equivalent mutant (F3), every mutant was killed by **assertion failures
with zero errors**, in both variants.

| Mutant | Failing tests |
| --- | --- |
| drop P4 block | 5 P4 tests; all P3 pass |
| drop lookahead entry | the 2 lookahead tests only |
| drop body entry | the 2 body tests only |
| **`r-disable-differential-stderr`** (F1) | `test_differential_body_headers_status_output_and_config_gates` |
| **`r-disable-stderr-has`** (F2) | `test_independent_exit_stdout_request_and_duration_gates` |
| P4 block before the opt-in guard | wrong-case / opt-out test |
| P4 fallthrough to the legacy regex | 3 P4 tests |
| P4 case-agnostic (`if True`) | 8 P3 tests + 1 P4 test |
| P4 scope also covers the P3 lookahead | 2 lookahead tests + P4 wrong-case |
| P4 scope also covers the P3 body | body, legacy-cause and P4 wrong-case tests |
| P4 admits bare canceled | 2 P4 tests |
| P4 `endswith("-header")` (F5) | P4 wrong-case test |
| `bare_context` entry keyed to `p4-deadline-header` | **survives**, equivalent (F3) |

**v2 F1/F2 closure.** In R1, the two reviewer mutants survived the whole pure
suite. On the composed source of both bases, both now fail by assertion with
zero errors.

### 5. Archive reductions (pure, hash-verified)

Process launches were forbidden. Results were identical normally and under
`-O`.

**P3, 14 captures** (R4/R5/R6 lookahead; release4/oracle1 body), 166 explicit
checks:

- The live runner reproduces the original issue strings exactly:
  - R6 `51183fb7…`, 683/1: `byte 37 … 114/63`
  - release4 `a4439320…`, 683/1: `byte 39 … 116/65`
- On the composed runner:
  - every capture has empty independent issues
  - every non-self ordered pair is clean
  - each case collapses to one canonical form
- All 23 rejection controls per case still reject.
- Cross-case rejection holds.
- All 126 artifacts were unchanged after the run.

**P4, 9 captures** (R4/R5/R6 reference/repeat/candidate):

- **Inputs.** Every artifact hash (72 files) equals the v2 evidence index.
  Report hashes equal the index:
  - R4 `acd53a28…`, 0 failed
  - R5 `7035750e…`, 0 failed
  - R6 `5851790b…`, 1 failed
- **Live runner.** It reproduces each report's issue list exactly, including
  R6 `stderr differs at byte 78: candidate 526 bytes, reference 566 bytes`. 16
  of the 81 ordered pairs fail on live.
- **Composed runner.**
  - All three per-revision accumulations are empty.
  - 81/81 ordered pairs are clean.
  - Every capture has zero independent issues.
  - There is one canonical form.
  - `signature()` equals the archived report signature on both runners, so
    baseline compatibility holds.
- **Actual-data stderr controls** (R6 bare Go repeat vs R6 Rust candidate,
  mutated on each side in turn). Each control gives `stderr differs` **in both
  directions**:
  - Usage `--output`→`--format`
  - missing final LF
  - CRLF
  - bare canceled
  - unknown `i/o timeout` cause with the suffix
  - extra trailing line
  - leading line
  - the P3 lookahead bare stream

  Independently, bare canceled and the P3 stream also give
  `stderr lacks b'Client.Timeout'`.
- **Cross-case.**
  - P4 streams are unchanged under the P3 lookahead, body and header-stall
    cases.
  - All 14 P3 captures are rejected independently as `p4-deadline-header`, and
    their stderr is identity under it.
  - 12 of those 14 were previously rewritten by the legacy regex. This is the
    accepted R1 tightening, not a broadening.

### 6. No broadening (synthetic matrix)

I evaluated 1056 case definitions (phase3, phase4 and phase5 corpora plus
deferred boundaries) against 15 synthetic inputs, 15840 checks, comparing live
and composed `normalize_timeout` and `compare_stderr`.

The inputs were:

- 5 P4 forms: three admitted, bare canceled, and suffixed with an extra line
- bare, suffixed and canceled forms for each of the three body contexts
- one header-stall query form

Results:

- **Outside `p4-deadline-header`, exactly two pairs change**, and they are the
  two intended P3 acceptances:
  - `p3-deadline-text-empty-array-lookahead` × bare "failed to read response
    body"
  - `p3-deadline-body-stall-text` × bare "failed to stream response body"
- **Within `p4-deadline-header`:**
  - The bare-context P4 form is now admitted. The two suffixed forms already
    canonicalized and are unchanged.
  - Seven foreign or extra-line inputs that the legacy regex used to rewrite
    are now byte-identity. I confirmed this direction directly. It is the
    deliberate tightening.
- The other `timeout_cause` cases, including `p4-deadline-body` and
  `p5-user-add-stalled-body-deadline`, are unchanged.

### 7. Root full suites (inspected, not rerun)

Root ran four suites. All four logs are settled.

| Run | JSON SHA-256 | Tests | Fail | Error | Skip | Inputs unchanged |
| --- | --- | --- | --- | --- | --- | --- |
| phase6 normal | `561217ba…` | 101/101 | 0 | 0 | 0 | yes |
| phase6 `-O` | `97d9d384…` | 101/101 | 0 | 0 | 0 | yes |
| phase7 normal | `daa8b4c2…` | 102/102 | 0 | 0 | 0 | yes |
| phase7 `-O` | `4f14f863…` | 102/102 | 0 | 0 | 0 | yes |

- **Evidence index.** These hashes equal
  `docs/evidence/phase6/composed-deadlines-p3-p4-proposal.json` (`0c9e0b24…`).
- **Imports.** Every recorded import path is inside the variant's own
  `staged/harness`.
- **Pass status.** `run_suite.py` sets `passed` only if all of the following
  hold:
  - `wasSuccessful()`
  - no skips
  - inputs unchanged
  - `testsRun == countTestCases()`

  So no error or skip could count as a pass.
- **phase7's extra test.** The 102nd test is phase7's own
  `test_interactive_boundaries_promoted_to_phase7`.
- **The targeted tests.** All 17 appear as `ok` in the logs.

**Native coverage is meaningful.** My guard blocks exactly 19 tests. In root's
runs, all 19 executed and passed. Root's 57 audit events per run are:

| Process | Count |
| --- | --- |
| frozen Go `sql run` (GoOracle, 6 cases × `reference_runs=2`) | 12 |
| `--version` | 2 |
| `phase3.py serve` fixtures | 14 |
| `phase5.py` fixtures | 3 |
| `pb-go-pinned user …` lifecycle (GoSmoke) | 15 |
| `promql labels` | 1 |
| `sql list` | 1 |
| frozen fixture r4 `--scenario` and `--capabilities` | 2 |
| `/bin/sh` runner tests | 6 |
| `os.posix_spawn` record | 1 |

The `os.posix_spawn` record is the same `--capabilities` launch already counted
as a Popen (F4). There was no 60 s or 120 s deadline case.

The printed `phase3 setup error: reference fae8411f… is not the frozen
manifest's 0000…` comes from the reference-check test's deliberate mismatch.
That test passes.

## Findings

### F1 (info, cosmetic, inherited): phase6 `test_phase3.py` has one extra blank line that phase7 lacks

- **Where:** phase6 staged line 483, before
  `test_empty_array_lookahead_bare_deadline_exact_boundary`. There are two blank
  lines inside the class; phase7 has one.
- **Origin:** the accepted P3 r2 artifact `812e9609`. Root copied it unchanged,
  as it should.
- **Correction to my R2 record:** my R2 statement that the cross-variant
  differences were "identical before and after the patch" was not exact. It
  holds at method and AST level. At whitespace level, this one blank line also
  differs.
- **Impact:** no behavioral effect.
- **Fix:** none required. Changing it would alter an accepted hash.

### F2 (low, provenance wording): phase7 suites used the phase6 frozen fixture, not phase7's own fixture

- **What ran:** `PB_PHASE3_FIXTURE` was `/home/ajs/.cache/pb-phase6/tmp/fx/pb-fixture-r4`
  (`8237e34e…`), built from phase6's modified `harness/fixture/main.go`
  (`d9025f74…`).
- **What phase7 has:** its own `bin/pb-fixture` (`fa8b4711…`) and `main.go`
  (`740b25cc…`).
- **How they differ:** the phase6 source is an opt-in superset. It adds a
  `Phase6` plan field and a `"phase6"` capability, and states that when the
  field is absent, behavior is as before.
- **Who uses it:** only `test_phase3` `FixtureTests` (2 launches).
- **Impact:** none on normalization.
- **Fix:** none to the patch. Describe the phase7 record as "phase7
  harness/dependencies with the phase6 frozen fixture r4". Run phase7's
  post-application native replay with **phase7's** own runner and fixture.

### F3 (info, equivalent mutant)

- **Mutant:** an extra `bare_context` entry keyed to `p4-deadline-header`
  survives.
- **Why it is equivalent:** the P4 block always returns before `bare_context`
  for that case name, so the entry is dead.
- **What protects the key set:** the verified two-line P3 source delta, as with
  R2-F3.
- **Fix:** none required.

### F4 (info, accounting)

- **What it says:** the evidence index records `process_event_count` 57.
- **What actually ran:** 56 launches. The remaining record is an
  `os.posix_spawn` audit event that duplicates the fixture `--capabilities`
  Popen.
- **Byproducts:** the `-I` fixture children again left `.pyc` files under
  `staged/harness/__pycache__`. These are unhashed and outside the suite's input
  set, as in R2-F1.
- **Fix:** none required.

## Limits

- **No native runs.** I made no native or long-deadline replay. Each bare
  branch still rests on one actual capture plus pinned Go 1.25.4 and `07af390`
  source, as in R1 and R2.
- **Original reports unchanged.** P3 R6 683/1 (`51183fb7…`), P4 R6 160/161
  (`5851790b…`) and release4 683/1 (`a4439320…`) remain failing records.
- **Other suites not run.** I did not run `test_phase6*`, `test_harness`,
  `diagnose` or phase7-only suites on the composed tree. Structurally they
  cannot be affected:
  - `normalize_timeout` is the only changed definition.
  - The new paths fire only for the exact names `p4-deadline-header`,
    `p3-deadline-text-empty-array-lookahead` and `p3-deadline-body-stall-text`,
    and only with `timeout_cause` opted in.
  - For every other name, the composed code path equals live.
  - Grep finds no other module that names these cases.
- **Root suites inspected, not rerun.** I inspected root's full native suites
  and did not rerun them.
- **No new sweeps.** I did not repeat the 7650-stream sweep or the earlier
  per-proposal mutant sets.

## Gates remaining (not satisfied here)

1. **Live application.** Check the live base hashes in the Verdict tables, then
   apply each variant's own `combined.patch` with `git apply --check` followed
   by `git apply`. Confirm the post-hashes in the Verdict tables. Do not use
   offset, fuzz or sequential P3/P4 application.
2. **Native replay.** Run a fresh focused native replay of the three cases on
   each applied base:
   - `p3-deadline-text-empty-array-lookahead`
   - `p3-deadline-body-stall-text`
   - `p4-deadline-header`

   For phase7, use phase7's own runner and fixture. Keep the original failing
   reports unchanged.
3. **Runner-hash handshake.** The phase3 `runner_sha256` moves from
   `7f8deaf0` to `01668670` (phase6) and from `39d16e2b` to `33be7605`
   (phase7). Any consumer pinning the old runner hash needs the edit-window
   handshake.
4. **Independent Sol review.** The Sol 6.1 composed review remains a separate
   required gate.
