# Phase 6 build-cost runner: independent review, round 5 (fresh, delta on R3 corrections)

This review replaces the interrupted R4 attempt. R4 finished UNREVIEWED and was not
accepted. Its progress file is `build-cost-review-r4/PROGRESS-UNFINISHED.md`
(`6a5dd520…a5b3`, unchanged). R4 ran no tests. Its two source-only concerns are
re-derived and probed here, not assumed.

Scope:

- Source reading.
- Guarded pure/mock execution: the author's suite, independent probes and an
  independent mutation sweep.

Nothing native ran. Specifically: no Cargo, compiler, toolchain or version probe,
readelf, product, fixture, meter, native helper, real child of the reviewed code,
real signal or kill, real wait, real flock, receipt, ACK or token, quiet window,
preparation, tree copy or extraction, or network. No other agents ran and no
commits were made.

I wrote only the cache `/home/ajs/.cache/pb-phase6/build-cost-review-r5/` and this
file. `TMPDIR`, `GOTMPDIR` and `CLAUDE_CODE_TMPDIR` pointed into that cache's
`tmp/`. The harness's own `/tmp` task directory returned EDQUOT on every command,
so every command redirected its output into the cache and I read it back from
there. `/tmp` was never written, moved or symlinked by me.

## Decision

**NOT YET for the 10 native compile-free helper controls on runner `70bdef52…eef5`.**

- **One Medium finding (R5-1).** A failed `posix_spawn` turns a definite *no-child*
  outcome into a permanent HOLD.
  - The HOLD keeps the common lock and ignores INT/TERM/HUP.
  - It loops on a `TypeError` and has no way to end.
  - After about 128 rounds it evicts its own root-cause error from the bounded
    evidence.
  - This sits in exactly the spawn region that native targets 1 and 10 exercise.
- **Why fix first.** Any native spawn error during those controls would wedge the
  control until the root's wall-clock cap. A later fix would also change the runner
  hash the control evidence binds to.
- **Required gate before the controls:**
  1. A narrow source+test correction of R5-1. R5-2 and R5-3 can ride along.
  2. A delta check limited to the spawn-failure path.
- **Closed in source+mock.** Everything else that R3 required is closed here:
  - the stop model (R3-1/2/3);
  - the caller-visible outcome and run boundary;
  - the procfs I/O fallback (R3-4);
  - duplicate keys and semantic single use (R3-6/8).
- **Still needs native confirmation:** CPython and glibc behavior (mask apply/raise,
  CLOSEFROM, failed-exec reaping).
- **Not authorized by this review:** any build, ready preparation, hash work,
  receipt, ACK, quiet window or build timing.

## Identities (before = after; `logs/00-before.txt`, `logs/15-ids-after.txt`)

| File | SHA-256 |
|---|---|
| `harness/phase6_build_cost.py` (87,989 B) | `70bdef521fa285c475ab7c5eb89739ba23320bb6cb63f1cc835ddaa87ad0eef5` |
| `harness/test_phase6_build_cost.py` (104,036 B) | `4cfcd271b69c21e9143e99839ef7ca3e49203593c967ebe24cb95ade3f837fc6` |
| `docs/phase6-build-cost-plan.md` | `ef96db2add2df6ad367108b04133047af7b05d84f62b3a3d8d5931c55b073289` |
| `r3-corrections/delivery-r3.json` | `05b67e0c1d9a4167d42be0e871510ca4564b4e8f85b6388917c8893a0f7df48f` |
| R3 / R2 / R1 review | `064d2a7e…ef11` / `e815bdbb…94fa` / `4bb147ac…931b` |

The author's `mock-fixtures/` directory was empty before and after.

## Execution evidence (verified, not trusted)

- **Author suite, reproduced: 94/94 pass.**
  - Ran in 7.1 s on Python 3.14.7.
  - Guards were installed before any reviewed import: 119 entry points of my own,
    stacked under the suite's 126 installed guards.
  - 0 forbidden calls under either guard set.
  - The loaded runner and test hashes equal the settled hashes.
  - `ctypes` was not imported.
  - Artifacts: `results/tests-run1.json` `95782a93…` and `.log` `ace2e751…`.
- **Independent probes: 15 ran, the harness was OK and 0 calls were forbidden.**
  - Files: `results/probes-run1.json` `4c32f56b…` (14 probes) and
    `probes-run2-eviction.json` `8b432cd6…` (1 probe).
  - Probes record observations; they do not assert. They reuse only the suite's
    fixture world (`setUp`, `kernel_world`, `mock_run`), and every kernel call is
    simulated.
  - The simulated `os.waitid` raises `TypeError` for `pid=None`, as CPython's
    `id_t` conversion does. The real function is never called.
- **Independent mutation sweep: 93 mutants plus a baseline.**
  - Each mutant ran in its own guarded process against the full 94-test suite, with
    a 90 s profile-hook watchdog and an outer `timeout 150`.
  - Every site was asserted to occur exactly once, and all mutants compile.
  - The substitutions are mine, re-targeted at the current source.
  - **Result:** the baseline passes. 67 killed causally, 0 killed only by a guard,
    19 survived, and 7 ended at the watchdog (hang). Each of the 7 hangs also has
    fast, direct failing witnesses; see the per-test locator below.
  - Summary: `mutants-run1/summary.json` `83df85e0…`.
- **Sweep interruption (disclosed).** Mutant SN1 lets a test's simulated
  `KeyboardInterrupt` escape `safe_note`.
  - That aborted unittest itself. CPython then re-delivered SIGINT to *its own*
    interpreter process at exit, a process-local self-signal, not reviewed code.
  - My bash loop ended with exit 130 (`logs/06-mutants-run1.txt`, kept intact).
  - I amended the driver (v2, `523c8ab4…`) to record such crashes as a causal kill
    and to accept an optional test list, then resumed from SN1
    (`logs/06c-mutants-run1-resume.txt`).
  - v1 (baseline through SPW1) had identical mutation and run logic. I did not
    record its hash before the edit.
- **Per-test hang locator.** For each of the 7 hung mutants I ran the 50
  `n3_/r2_/r3_` tests one at a time with a 30 s cap (`logs/11` and `logs/13`).
  - Six mutants hang only in `test_r2_c_escaped_members_hold_lock_pin_and_skip_all_end_verification`.
    M37i hangs only in `test_n3_timeout_retains_leader_until_cleanup_verified`.
  - Every one of the 7 fails fast in other direct tests. C8 is killed directly by
    `test_r3_hold_contains_all_bookkeeping_base_exceptions_and_exact_sleep`, which
    closes R3's "C8 detected only by hanging".
  - Seven `tmp/mutant-fixtures/test-*` directories were left behind by
    timeout-killed processes inside my cache. I retained them.

## R4 source-only concerns: resolved

| R4 concern | R5 status |
|---|---|
| (1) A failed `posix_spawn` sets `spawn_started` with no PID, so `run` holds forever | **Confirmed by mock; Medium (R5-1).** See below. |
| (2) The first-launch reserve omits the in-window verifies | **Confirmed and wider than R4 estimated; Low (R5-2).** |

### R5-1 — Medium (liveness/evidence): a definite spawn failure becomes an endless, unresolvable no-child HOLD

**Locations:**

- `execute` 1378-1384: `spawn_started = True` before `os.posix_spawn`.
- The `except BaseException` at 1407 records the error. The `finally` at 1410 skips
  cleanup because there is no PID, so `execute` returns normally.
- The `_run` boundary at 1697-1709 holds on `spawn_started`.
- `finish_owned`, then `hold_owned`, then `cleanup_round` with `pid=None`. There,
  `kernel_owned(None)` raises `TypeError`, which is caught, and the loop repeats.

**Probes P-SPAWNFAIL-EAGAIN and P-SPAWNFAIL-ENOENT (`logs/04`):**

- `posix_spawn` was simulated raising `OSError(EAGAIN)` and `FileNotFoundError`.
- `execute` returned normally with `pid=None`, `spawn_started=True`,
  `cleanup_status='not_observed'` and the mask restored.
- `run` then entered HOLD for 25 rounds, at which point a probe-only escape hatch
  ended it. The production loop has no exit.
- The lock was held every round, the mask was blocked every round, and each round
  had exactly one `sleep(1)`.
- 0 `killpg`, 0 `waitpid`, and 25 `waitid(None)` that raised `TypeError`.
- The receipt was consumed: both markers exist.
- **Protected stops are ignored.** SIGTERM (pending) and SIGINT (recorded) arrived
  in round 3, and 22 more rounds followed.
- Disk `process.json`: `pid: null`, `cleanup_status: unknown-held`.
- Report: `cleanup_status: not_observed` and `group_empty_verified: null` for a
  held sample. See R5-3.

**Probe P-SPAWNFAIL-EVICTION-140 (`logs/07`):**

- After 140 rounds, both `process.json` and the report's sample keep 128 errors, and
  14 have been dropped.
- The only distinct error left is `cleanup: TypeError: 'NoneType' object cannot be
  interpreted…`.
- The primary `SIMULATED-PRIMARY-SPAWN-ERROR` is gone from both. Natively, an
  operator arriving about 2 minutes later cannot see why the runner is held.

**Semantics (reviewer knowledge, not natively executed):**

- In glibc ≥ 2.24, `__spawnix` uses `CLONE_VM|CLONE_VFORK`. If the auxiliary child
  fails before or at `execve`, the parent calls `__waitpid(new_pid, NULL, 0)` before
  it returns the error.
- A failure before `clone` creates no child at all.
- CPython's `os.posix_spawn` raises `OSError` only from that return code. Argument
  conversion errors (`TypeError`/`ValueError`) happen before the call.
- **Conclusion:** an `OSError` from `posix_spawn` means *no child exists*. Only a
  failure after a successful return (for example, `MemoryError` while building the
  PID object) is genuinely unknown.
- **P-SPAWN-UNKNOWN-MEMORYERROR** shows that case takes the same endless path. The
  plan accepts this by design (plan 220-221).

**Coverage:** no test exercises `spawn_started` at all.

- **Survived** (no test detects them): C5b (boundary ignores `spawn_started`), C5c
  (the journal never marks spawn) and SPW1 (`spawn_started` never set).
- The suite does reach this path by accident: M12j, O1, CF1 and J1 make the
  simulated spawn raise an `AssertionError`, and `test_r2_c…` then hangs in exactly
  this endless no-PID HOLD.
- A related test-design weakness: assertions inside hold observers are swallowed by
  the deliberate `BaseException` containment, so a broken invariant shows up as a
  hang rather than a failure.

**Why it matters before native controls:**

- Native targets 1 and 10 validate spawn attributes and glibc fd actions, including
  CLOSEFROM.
- Any EAGAIN/ENOMEM/EINVAL/ENOENT there wedges the runner until the root's
  wall-clock cap. The control is then incomplete, with its evidence evicted.
- For a real window, the same failure consumes the receipt and holds the common lock
  until SIGKILL.
- It is fail-safe in the sense that matters most: no false pass, no lock release
  with a known live child, and no PID-reuse signal. It is not conservative
  *reporting*, though, and it contradicts "a definite failure is recorded, then
  cleanup proceeds".

**Correction (narrow):**

1. In `execute`, catch `OSError` **from the `posix_spawn` call only**.
   - Record `spawn_error` (errno and text) in a dedicated, non-rotating field.
   - Set `spawn_outcome: 'failed-no-child'` and clear `spawn_started`.
   - Do not hold. The sample fails, and the end verification may run.
2. Keep HOLD only for a non-`OSError` raised after the call has started
   (`spawn_outcome: 'unknown'`).
   - In that state, do not call `cleanup_round` with `pid=None`.
   - Journal a distinct `cleanup_status: 'spawn-outcome-unknown-held'` once per
     paced round.
   - Pin the first/primary error in a non-rotating field.
3. Count `spawn_started` or `spawn_outcome` samples as "launched" in the report-level
   cleanup fields (R5-3).
4. Tests:
   - spawn `OSError` → no hold, no kill or reap, lock released, error preserved;
   - unknown spawn → held with the primary error retained after more than 128
     rounds;
   - kill C5b, C5c and SPW1;
   - make the HOLD-observer tests fail fast, for example with a bounded round
     counter that records and aborts, instead of hanging.
5. Native protocol: add a compile-free control to target 1.
   - Spawn a nonexistent helper path through the runner, expect `OSError`, then
     prove there is no child.
   - The proof is a single `waitid(P_ALL, WEXITED|WNOHANG|WNOWAIT)` with
     ECHILD/None, recorded by the pass-through wrapper.

### R5-2 — Low (fail-closed waste of a root window): admission still admits windows that cannot launch

R3-7 is partially closed. `admission` 903-906 requires
`timeout + 148 + 2·V + 10 + 5 + 4`, plus `count·(timeout + 17)`. Two gaps remain:

- **First launch.** `_run` spends two full verifications (pre-run and per-sample)
  plus the rustc probe *before* the first `Budget.launch`. That check then needs
  `timeout + 148 + 2·V' + 17` remaining.
- **Later samples.** The schedule term omits the per-sample verification and the
  launch reserve of every later sample.

**P-RESERVE-BAND (`logs/04`).** Setup: V = 30 s (observed per verify), timeout 10 s,
cold-only schedule.

| Window (s) | Result |
|---|---|
| 237 | refused before consumption |
| 238, 260, 297 | **admitted, both markers consumed, output created, 0 launches**, `BudgetExpired` |
| 298, 299, 310 | base launched; the candidate's launch guard refuses (`BudgetExpired`), so not passed |
| 238 with zero-cost verify | passes with 2 launches, which isolates the cause |

By arithmetic, a full pass needs about 328 s here. There is no false pass and no
shortened timeout. The cost is a consumed receipt for an impossible schedule.

**Correction:**

- Admission should require
  `2·V + PROBE_S + Σ_samples(timeout + launch_reserve + V) + V_end`, or
  equivalently simulate the schedule.
- Add a test that would have caught this: an admitted window must reach its first
  launch.

This is not relevant to helper controls.

### R5-3 — Low (evidence): report-level cleanup fields

- **No-PID held sample.** A held sample that has no PID (R5-1) is reported as
  `cleanup_status: not_observed` and `group_empty_verified: null`, because the
  "launched" test is PID-only (1749-1752).
- **Top-level `held_ever`.** When the HOLD happened inside `execute` (P02),
  `report.held_ever` is `null`. It is set only by the `_run` boundary path. The
  sample's `held_ever` is true, and end verification is correctly skipped.

**Correction:** derive both report fields from the samples.

### R5-4 — Low (test gaps): surviving mutants

| Mutant | Why it survives | Classification |
|---|---|---|
| C5b, C5c, SPW1 | No test covers spawn without a PID | **Gap, R5-1** |
| M12k (child defaults lose SIGPIPE/SIGXFSZ) | Tests compare `setsigdef` to `b.CHILD_DEFAULTS` itself, a mirrored assertion | Gap; pin the literal set. Native target 1 checks `SigIgn`. |
| O2 (reconcile downgrades in-memory proof), O3 (conflicting caller/journal PID accepted) | Untested | Gap. O2 would turn a reaped leader into an ECHILD endless HOLD. |
| A2c (hold pause catches only `Exception`) | No test raises a `BaseException` from `sleep` | Gap. Unreachable with recording handlers. |
| S12 (no stop check immediately before spawn) | The wait-loop stop (S13, killed) kills a just-launched build | Gap. A stop after the last checkpoint launches, then kills. |
| A1, A1f, A1g, C4b, F1, A2b, S6, S6b, S7, S14, G2 | Inner masks are redundant under the execute lifetime mask or the flag model. Single-attempt/early-return variants are caught by the next boundary (execute loop or `_run`/`run`). The double precondition checks (S6c, both removed, is killed), the redundant stop points and `run()`'s own invalidation make these equivalent or defense in depth. | Equivalent / defense in depth |

## R3 closure (source + mock)

| R3 | Status | Basis |
|---|---|---|
| R3-1 (stop swallowed by checkpoint retry) | **Closed** | **P01:** a stop during the sample-2 pre-launch verify, either pending (masked model) or delivered (handler records), gives exactly 1 launch, no end verify, disk not passed and the mask restored. **P01b:** a stop pending during the first verify, or arriving at the pre-launch checkpoint's masked file `fsync`, gives 0 launches. Mutants S1, S3, S3b, S11 and S13 are killed. |
| R3-2 / R3-9 (escape before mask; PID invisible) | **Closed except R5-1/R5-3** | **P02 (run level):** an escaped member kept for 4 held rounds, with all stops pending each round and 3 failed held journals. The lock was held and masked every round, and released only after reap with no escape. There were 2 verifies (no end verify), no kill after reap, all 3 stops recorded, and the mask and handlers restored. **P10b:** stops arriving in the final masked journal give `deferred_signals = [1,2,15]` on disk; the report says `observed` and verified, with the run not passed. C5, C5d, C1, A1L, A2, A4, A5 and SN1 are killed. |
| R3-3 (mask apply raise) | **Closed in mock; native pending** | **P05:** a fault after the block was applied at `execute`'s lifetime block gives 0 spawns, `control_failed: true`, the mask restored and no hold. S4, S5, S8 and S10 are killed. Inherited STOP refusal is checked in two places: S6c (both removed) is killed. |
| R3-4 (procfs I/O suppresses kill) | **Closed** | **P04:** a known leader with persistent EIO still gets the kernel-owned fallback kill in round 1. K1, K2, M06i, M06k, M06j and B1 are killed. Residual: while procfs keeps failing, a late live member is not re-killed. The HOLD is retained, which is fail-safe. |
| R3-6 duplicate keys | **Closed** | A nested duplicate `sha256` inside the prepared `tools` is refused. RC1 is killed. |
| R3-7 remaining window | **Partially closed** | R5-2 |
| R3-8 semantic single use | **Closed** | A key-reordered variant receipt for the same `(window_id, run_id)` after the output was moved away is refused by `FileExistsError` on the semantic marker, with 0 launches. An exact replay is refused because the output exists. RC2, RC3, R4, R4b and R5 are killed. Keying on `run_id` alone would be stricter (informational). |
| R3-10 / R3-11 disclosure | **Closed (documented)** | Plan 260-270. **P-ZOMBIE:** zombie-only members cause 1 kill. The first round makes 252 scans over a bounded 5 s with no re-kill; held rounds make 2 scans each. Z1 and C9 are killed. |
| R3 survivors (21 + C8) | **20 killed, A1 equivalent** | M06i, M10i, M12i, A2, A4, B1, C2, C4 (hang plus direct witnesses), C5, C7, C9, C11, C12, R2, R3, R4, R8, R9 (also R9b/c/d), BND1 and BND2 are killed. C8 is killed directly (locator). A1 is redundant under the lifetime mask. |

Also verified:

- **Late stop at `orderly_signals` exit:** the inner report passed, but the outer
  report and the disk show not passed, with SIGTERM recorded.
- **Receipt input checks:** an ACK of 4097 bytes is refused, and `jobs=True` is
  refused.

## Native protocol amendments (nothing ran; not authorized here)

After R5-1 is fixed and delta-checked:

- **Target 1:** add the spawn-failure, no-child control described under R5-1.
- **Target 10:** make CLOSEFROM failure behavior explicit. It must fail closed
  without a HOLD.
- **Every target:** keep the root wall-clock cap. For targets 4 and 5, a HOLD
  observer must record and stop, never swallow and hang.

The other R3 amendments stand.

## Reproduction and artifacts

```sh
R=/home/ajs/.cache/pb-phase6/build-cost-review-r5
export TMPDIR=$R/tmp GOTMPDIR=$R/tmp CLAUDE_CODE_TMPDIR=$R/tmp PYTHONDONTWRITEBYTECODE=1
python3 -I -B $R/run_tests.py $R/results/tests-run1.json
python3 -I -B $R/probes.py $R/results/probes-run1.json
python3 -I -B $R/probes.py $R/results/probes-run2-eviction.json test_p_spawnfail_evidence_eviction
python3 -I -B $R/mutate.py --check-sites
for m in $(python3 -I -B $R/mutate.py --list); do timeout 150 python3 -I -B $R/mutate.py $R/mutants-run1 "$m"; done
python3 -I -B $R/summarize_mutants.py $R/mutants-run1
```

| Artifact | SHA-256 |
|---|---|
| `guards.py` / `run_tests.py` | `def32642…d3e5` / `59d7ee48…10cd` |
| `probes.py` (final; the eviction probe was added before run 2) | `3ef5586d83a4edfe85bbda9b4ed4669c7e08f51f039b802b4b156dbbb5e17aeb` |
| `mutate.py` v2 / `summarize_mutants.py` | `523c8ab40be0fe3ce4c98e9fd95fb750a65e2ade7a09017af341ca431ea043a3` / `cdbb762b…c6ea` |
| `results/tests-run1.json` / `.log` | `95782a93…e2d5` / `ace2e751…7d75` |
| `results/probes-run1.json` / `probes-run2-eviction.json` | `4c32f56b…0ea0` / `8b432cd6…5c2b` |
| `mutants-run1/summary.json` | `83df85e09a5166edb4d4de8c95862d93775889bfef00f8df848cd882acee6b31` |
| `logs/06` / `06c` (sweep) | `8b658ab9…2484` / `081dcecf…6714` |
| `logs/11` / `13` (per-test locator) | `645de5eb…d940` / `1413bbe3…9486` |

Every command log is in `logs/` and none was overwritten. The locator deleted only
its own per-test transient `.json` between runs; its verdict lines are preserved in
`logs/11` and `logs/13`. No owned process remains.

This review makes no ranking, statistical, native or whole-phase claim.
