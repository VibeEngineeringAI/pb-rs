# Phase 6 build-cost runner: independent limited delta review, round 7 (R5 corrections)

This is a fresh, independent review. R6 was paused and is **unfinished, not accepted**. I
read its progress file (`e68b7040…4543`) only for its tentative notes. I did not copy any
of its notes as a finding without checking it myself first.

The scope is limited to the R5 corrections (R5-1 to R5-4). The question is whether the
source and mocks are ready for the ten compile-free native helper controls. This review
does not cover native behaviour, build timing or release.

**Executed checks (guarded pure/mock only):**

- the author's suite;
- 10 focused probes;
- 13 of my own focused mutants;
- an independent re-run of all 18 of the author's R5 mutants.

**Nothing native ran.** That rules out Cargo, compilers, version probes, readelf, products,
the meter and native helpers. It also rules out real spawn, kill or wait, real signal masks
or flock, receipts and ACKs, preparation, network and agents. I made no commits and no
source edits.

**Where I wrote:** only `/home/ajs/.cache/pb-phase6/build-cost-review-r7/` and this file.

**Shell workaround:** the harness's `/tmp` task directory returned EDQUOT on every command.
I did not touch `/tmp`. Instead, every command redirected its output into the cache and I
read it back from there. `TMPDIR`, `GOTMPDIR` and `CLAUDE_CODE_TMPDIR` all pointed to
`r7/tmp`.

## Decision

**YES (source/mock readiness only) for the ten compile-free native helper controls on
runner `ede8595c…be57`, subject to the native-gate items below.**

- **R5-1 to R5-4 are closed in source and mock.** No High or Medium finding remains.
- **New findings: Low and Info only.**
  - None of them can wedge the helper controls.
  - None of them can produce a false pass.
  - None of them releases a known live child.
- **Recommended, not blocking:** fix R7-1 and the documentation error D1 in the same
  edit, before the controls bind a runner hash.
  - R7-1 needs a one-line guard.
  - If the runner changes, a re-check limited to that diff is enough.
- **Not authorized by this review:** native execution, preparation, receipt, quiet window,
  build or timing.
- **Native behaviour of CPython and glibc stays unproven by source and mocks.** That covers
  the posix_spawn failure/reap path, CLOSEFROM and mask behaviour. Targets 1 and 10 must
  establish it.

## Identities (before = after; `logs/00-before.txt`, `logs/10-after.txt`)

| File | SHA-256 |
|---|---|
| `harness/phase6_build_cost.py` (92,262 B) | `ede8595cf6612cd5d41b15065f067f4552cc39d4a80a668664437a2ec233be57` |
| `harness/test_phase6_build_cost.py` (120,437 B) | `f8a2f7a310aa9d95e73d36f100a4dded064c233554501617ffa6aeb48c8d3498` |
| `docs/phase6-build-cost-plan.md` (39,211 B) | `3816c9d4d284647296fe0416565b794d21de84c91a4529e8058cc8a2e20d6df8` |
| R5 review | `c4c522916becb02389105d065ccc8ddcc30123a1939f4317e9f3cf46de1840a7` |
| `r5-corrections/delivery-r5.json` | `6d1f1d11c2ba63e9fd8bc3178f0f0404b31f64b89ef7e2bee5a7eba89acefb4d` |
| historical R3 runner (read-only comparison) | `70bdef521fa285c475ab7c5eb89739ba23320bb6cb63f1cc835ddaa87ad0eef5` |

The author's `mock-fixtures/` directory held 0 entries before and after. My own fixture
directories were empty at the end, and the R6 cache was untouched.

## Executed evidence

**Author suite: 106/106 pass in 10.7 s on Python 3.14.7** (`results/tests-run1.json`
`116e24ac…`).

- 119 R7 guards were installed before any reviewed import, and the suite installed its own
  126 guards.
- 0 forbidden calls.
- The loaded runner and test hashes equal the settled hashes.
- `ctypes` was not imported.
- The guards are copied from R6 `348bc731…` with only path and label changes
  (`logs/01-tooling.txt`).

**Probes P1–P9: 10 probe methods ran, with 0 harness errors and 0 forbidden calls**
(`results/probes-run1.json` `ee45803c…`, digest in `logs/05`).

- Probes record observations; they do not assert.
- Every kernel call is simulated. Simulated `waitid(None)` raises `TypeError`, as CPython
  does.
- Every HOLD is ended by a labelled forced end that exists only in the probe.
- Two inherited author tests whose names begin `test_p` were also collected and passed.
  They are not counted as probes.

**My own mutants: 13 plus a baseline, each in its own guarded process running the full
106-test suite** (`mutants-run1/summary.json` `8acc4a58…`).

- 7 were killed causally. None was killed only by a guard, none hung, and there were 0
  forbidden calls.
- 6 survived. They are classified below.

**The author's mutants: all 18 re-run with the full suite each, using sites read from
`delivery-r5.json`** (`author-mutants-run1/summary.json` `b48cd245…`).

- All 18 were killed.
- In every case the delivery's named causal test is among the failures.
- Every mutated source hash equals the author's recorded hash.
- None was killed only by a guard, and there were 0 forbidden calls.

**Source-only checks:** the CPython and glibc rationale, which is reviewer knowledge and
was not fetched; the inducibility of the targets; and line-level exception ordering.

## R5 closure

### R5-1: closed in source and mock

**The `except OSError` boundary covers only the `os.posix_spawn(...)` statement** (lines
1417–1432). Inside it:

- the error is pinned once (`primary_error` and `spawn_error`);
- `spawn_started` is cleared;
- `spawn_outcome` and `cleanup_status` are set to `failed-no-child`;
- the exception is then re-raised.

**`pid` is assigned as the first statement after a successful return.**

**Later OSErrors are outside that boundary.** The restore `chdir`, the journal and procfs
errors cannot reach it.

**P1 (simulated spawn failure with EAGAIN, ENOENT, ENOMEM, EBADF, EACCES and ECHILD).**
In every case:

- the outcome is failed-no-child, and the errno is pinned on disk and in the report;
- there were 0 `waitid`, 0 `waitpid`, 0 `killpg`, 0 `cleanup_round` and 0 `hold_owned`
  calls;
- the lock was released exactly once;
- the mask was confirmed at the spawn and restored afterwards;
- 3 verifications ran, including the end verification, which passed;
- the report says `failed-no-child`, `group_empty_verified: null` and `held_ever: false`,
  and the run did not pass.

**P3a.** The spawn raises OSError and then the restore `chdir` also raises OSError. The
sample stays failed-no-child, and the primary error is still the spawn errno.

**P3b.** The spawn succeeds and the restore `chdir` raises EIO. The PID 31337 is kept, the
outcome is `child-pid-returned` and `spawn_error` stays null. The owned kill and reap
happen with the group verified.

**P4 (no-PID unknown, simulated by a MemoryError).**

- In 5 bounded rounds, the lock was held and the mask confirmed every round, with
  `spawn-outcome-unknown-held`.
- There were 0 `waitid` calls (including 0 with `None`), 0 kills and 0 reaps.
- The primary error was pinned on disk.
- The final report says `spawn-outcome-unknown-held` and `group_empty_verified: false`,
  and the end verification was skipped.

**The source rationale matches my understanding, but it is not runtime proof.**

- CPython converts arguments and runs the audit hook before the C call.
- A nonzero return from the C call becomes OSError.
- After success, the only failure left is building the PID object (a MemoryError).
- glibc's `__spawnix` reaps a failed auxiliary child before it returns the error.

### R5-2: closed in source and mock (see D1)

- **One formula is shared.** `launch_reserve` is used by both `Budget.launch` and
  `schedule_plan`. The planned launch guard is timeout + reserve + 2, which matches the
  runtime `limit = deadline - 2`.
- **P6, run against my own planned-cost world:**
  - a window equal to `required_s` is refused before consumption, with 0 markers and no
    output;
  - `required_s` + 0.25 s is admitted and completes, with 2 launches cold and 4 warm, and
    passes.
- **The new planning stages are causally witnessed.** My mutants MX7, MX8, MX9 and MX10
  were killed, as were the author's PLAN1–3.

### R5-3: closed for the final report; checkpoint residual is R7-2

- **The P7 matrix is as specified.**
  - An unknown spawn, with or without the marker, is never reported as `not_observed` or as
    verified.
  - A failed-no-child sample alone gives `failed-no-child` and `null`.
- **Kills.** MX12 and the author's SUM1 were killed.

### R5-4: closed

- All nine exact R5 substitutions were reproduced and are killed causally:
  C5b, C5c, SPW1, M12k, O2, O3, S12, A2c and M12j.
- **No assertion is swallowed.** The new tests place assertions outside production
  containment. Spawn and HOLD mocks only record. `kernel_world` records mock failures and
  bounds the HOLD to 8 rounds, then asserts outside.
- **One leftover in-production assertion.** The R3-era sleep mock in the repeated-stop
  test still asserts inside production (test lines 1883–1889). Later assertions in the same
  test would still fail it. Info.

## New findings

### R7-1: Low (defense-in-depth regression), the run-boundary `pin_primary` precedes journal recovery

**Location.** `_run`, lines 1760–1768. `pin_primary(record, primary, 'run-boundary')` now
runs before `safe_note` and before the reconcile try block. Unlike `safe_note`, it is not
guarded.

**What happens.** If `pin_primary` raises, the `(sample/'process.json').exists()` check
and `reconcile_process` are skipped. It raises on an allocation failure, or on an exception
whose `__str__` raises.

**P5b, with an executor that journals PID 31337 and then raises:**

| Case | HOLD | Record | Lock | End verification |
|---|---|---|---|---|
| Control: plain RuntimeError | entered | PID 31337 recovered | held until the forced end | skipped |
| Current runner: `__str__` raises | **none** | `pid: null`, `not_observed` | released | ran and passed |
| Current runner: `pin_primary` MemoryError | **none** | `pid: null`, `not_observed` | released | ran and passed |
| Historical R3 snapshot `70bdef52…`: same `__str__` failure | entered | PID 31337 recovered | held | skipped |

**Effect.**

- The plan's invariant fails in this case: unexpected exceptions should not lose the
  caller-visible PID, and ownership should be held before end verification or lock
  release.
- There is no false pass: the report is not passed.

**Why Low.**

- `execute` is total after its `try`, so it cannot raise after a spawn in the current code.
  The boundary is defense in depth.
- The triggers are catastrophic allocation or a pathological exception object.
- The same ordering inside `execute` (line 1457) is harmless: its `finally` keys on the
  PID, and the no-PID case is still held at the boundary.
- The catastrophic counterpart, P5a, over-holds conservatively. There `pin_primary` fails
  inside the OSError handler, and a definite failure becomes a held unknown.

**Fix.** Wrap both `pin_primary` calls in `try/except BaseException` that falls back to
`bookkeeping_failed`, or move the boundary call after the reconcile try. Add a test that
uses a raising `__str__`.

### D1: Low (documentation), the planning figures are 10 s low

**Location.** Plan lines 353–354 and the delivery R5-2 text say cold-only (V=30, T=10)
requires more than **502 s** and warm more than **1208 s**.

**Executed result (P6).** `schedule_plan` returns **512 s** and **1218 s**. Windows of
502.5, 503, 1208.5 and 1209 s are all refused before consumption.

**Cause.** The documented figures leave out the 10 s pre-run compiler probe, which the
source correctly includes as `V + PROBE_S`. This confirms R6's tentative arithmetic.

**Effect.** The runner fails closed and consumes nothing, but a root sizing a window from
the document would be refused.

**Fix.** Correct the text. Any test that pins numbers should pin 512 and 1218.

**Other values (V=30), for root window sizing:**

| Timeout | Cold | Warm |
|---|---|---|
| 600 s | 1692 s | 3578 s |
| 3600 s | 7692 s | 15578 s |

At T=3600, warm exceeds the 4 h window cap, so warm is admissible only for T < 3305.5 s.

### R7-2: Low (evidence), the on-disk report during a HOLD is the stale pre-launch checkpoint

**What P4 and P5a showed.** In every held round, `report.json` showed:

- `cleanup_status: not_observed`, `held_ever: false` and `group_empty_verified: null`;
- the sample's `spawn_outcome: not-started`;
- `last_checkpoint_complete: true`.

**Why.** No report checkpoint runs between the pre-launch checkpoint and the end of the
HOLD.

**Effect.** For a no-PID unknown spawn, which by design never resolves, `report.json`
permanently describes the spawn as `not_observed`. That is the description R5-3 meant to
eliminate. Meanwhile `process.json` is correct (`spawn-outcome-unknown-held`, PID null,
primary pinned).

**Sample level.** The record and journal keep `group_empty_verified: null` for no-PID
uncertainty. Plan line 182 reserves `null` for "no launched sample". Only the report-level
summary forces `false`.

**Untested claim.** Mutant MX5 removes `summarize_cleanup` from `checkpoint()`, and it
survives. The claim "every checkpoint derives…" is untested.

**Why Low.** The report is not passed or complete, consumers are already forbidden from
using it, and `process.json` is authoritative.

**Fix.**

- Set `group_empty_verified=False` on the no-PID unknown path.
- Either checkpoint the report once when the HOLD starts, or state in the plan that
  `process.json` is the only live HOLD authority.

### R7-3: Low (test gaps): surviving mutants

| Mutant | Classification |
|---|---|
| MX2: the failed-no-child path is no longer journaled by production | **Gap.** `test_r5_spawn_oserror…` rewrites `process.json` itself (test line 1949) before reading it, so the plan's "journals the error" claim is not witnessed. P1 shows that production does journal it. |
| MX5: `checkpoint()` no longer summarizes | **Gap** (R7-2). |
| MX11: `Budget.launch` ignores the observed verify cost (this line was refactored in R5) | **Gap.** The behaviour still fails closed: the end check raises BudgetExpired, with no false pass. |
| MX3: `execute` no longer sets the unknown status | Defense in depth. The HOLD sets it before the first pause, and only the transient journal differs. |
| MX4: the boundary drops the `spawn_outcome=='unknown'` clause | Defense in depth. It matters only if assigning `spawn_started` fails after `spawn_outcome` is set. |
| MX6: the plan's end-verification term is dropped | Equivalent. The launch guard dominates whenever V > 10; with V ≥ 30, `required_s` is unchanged. |

### Info

- **I-1: classification is by exception type.**
  - P2: `StopRequested`, `InterruptedError` and `TimeoutError` raised from the call are all
    classified failed-no-child, because all three are OSError subclasses.
  - This is correct as long as nothing inside the `try` can raise them after a successful
    native spawn.
  - The installed recording handler is total: P2 fed it bad values, and it neither raised
    nor set `failed`. No other Python-level handlers are in the contract.
  - Keep the boundary "call-only". Never move a stop or budget check inside it.
- **I-2: conversion errors are over-held.**
  - A non-OSError raised by argument conversion or an audit hook inside the call becomes
    `spawn-outcome-unknown-held`, an indefinite HOLD, even though no child exists. Examples:
    a TypeError from a non-str argv or env entry, or a ValueError from a NUL byte.
  - This is fail-safe. After `verify` it is practically unreachable.
  - Native control harnesses must pass well-formed `str` argv and env, or they will wedge
    until the root cap.
  - Optional fix: pre-validate types and NULs before setting `spawn_started`.
- **I-3: a brief unmasked gap on the no-PID path.**
  - For no-PID unknown, `execute` returns with the mask restored (P4:
    `mask_after_execute_return: []`). The run-boundary HOLD then masks again.
  - This is harmless with recording handlers, but plan line 211's "continuously masked" is
    slightly overstated for this path.
- **I-4: a narrow fail-closed band remains above `required_s`.**
  - P6: at `required_s` + 0.25 s, with 50 ms of unplanned write overhead, the run was
    admitted and consumed, then hit BudgetExpired after 1 launch.
  - At + 5 s, both cold and warm passed.
  - This is disclosed ("estimates, not guarantees"). Root should add a few seconds of
    margin, or the runner could add an explicit bookkeeping allowance.
- **I-5: report-level status can hide a failed spawn.**
  - P9: base verified, then the candidate is failed-no-child. The report says `observed`
    and `group_empty_verified: true`, and the run did not pass.
  - The sample keeps `failed-no-child`. This confirms R6's note, which is informational.
- **I-6: a stop arriving with a failed spawn is handled.**
  - P8: the stop pending at the failed spawn is recorded in `process.json`
    (`deferred_signals: [15]`) and in the report.
  - No later launch occurs, the end verification stops on StopRequested, and the lock is
    released.

## Native-gate blockers and the ten targets (source/mock readiness only)

**No source or mock blocker.** Each target still needs root authorization, a wall-clock
cap, a task-owned lock (except target 8), and an external cleanup.

| Target | Readiness | Note |
|---|---|---|
| 1 | Ready | **Gate item.** The nonexistent-helper ENOENT path must show failed-no-child, no HOLD, and the wrapper's `waitid(P_ALL,…WNOWAIT)` returning ECHILD/None. This is the first proof of the loaded glibc reap. Pass `str` argv/env only (I-2). |
| 2, 3, 6, 7, 9 | Ready | Unchanged by R5. |
| 4, 5 | Ready | HOLD observers must read `process.json`, not `report.json` (R7-2), and must record and stop rather than swallow. Unknown no-PID never self-releases. |
| 8 | Ready | Unchanged. |
| 10 | Ready, with a **protocol-wording item** | fd actions and mask restoration are testable. A CLOSEFROM failure is probably not inducible compile-free on this kernel (`close_range` does not fail in practice). Accept "not inducible" as an explicit, recorded limitation. The definite spawn-OSError path is covered by target 1's child-failure path. Do not count it as a pass. |

**Limits that stay explicit and unchanged:** procfs observation is non-atomic; an
unobserved escape or reparent is possible; there is no cgroup or subreaper; catastrophic
failure and default-terminating signals are uncovered; warm cache order is fixed; and each
condition is a single descriptive observation.

## Reproduction

```sh
R=/home/ajs/.cache/pb-phase6/build-cost-review-r7
export TMPDIR=$R/tmp GOTMPDIR=$R/tmp CLAUDE_CODE_TMPDIR=$R/tmp PYTHONDONTWRITEBYTECODE=1
python3 -I -B $R/run_tests.py $R/results/tests-run1.json
python3 -I -B $R/probes.py $R/results/probes-run1.json
for m in $(python3 -I -B $R/mutate.py --list); do timeout 150 python3 -I -B $R/mutate.py $R/mutants-run1 "$m"; done
for m in $(python3 -I -B $R/mutate_author.py --list); do timeout 150 python3 -I -B $R/mutate_author.py $R/author-mutants-run1 "$m"; done
```

| Artifact | SHA-256 |
|---|---|
| `guards.py` / `run_tests.py` | `0ec59e65…6261` / `4eabb1c6…f0ef` |
| `probes.py` / `mutate.py` / `mutate_author.py` | `c9621f84…c0a8` / `db1d3176…2b90` / `80f2e240…2de` |
| `results/tests-run1.json` / `probes-run1.json` | `116e24ac…b969` / `ee45803c…931b` |
| `mutants-run1/summary.json` / `author-mutants-run1/summary.json` | `8acc4a58…4cb4` / `b48cd245…aad4` |
| logs `00`–`08` | listed in `logs/10-after.txt` |

All runs completed. There were no hangs, no watchdog exits and no leftovers. No owned
process remains. This review makes no ranking, statistical, native or whole-phase claim.
