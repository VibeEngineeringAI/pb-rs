# Phase 6 build-cost tooling review, round 3 (delta)

This is a fresh, independent delta review of the R2 corrections to the build-cost
runner. It is **not** native admission, and it does not accept the whole phase.
It covers source reading, guarded pure/mock execution of the author's suite,
independent causal probes, and an independent mutation sweep. The
[profiling review](phase6-profiling-plan.md) runs separately and is not covered here.

Nothing real ran: no Cargo, build, compiler or version probe, readelf, product,
fixture, meter, native helper, profiler, network request, real flock, token,
real signal, real wait or process scan. I did not hash the 1.3 GB toolchain or
copy or extract any real cache. I created no ready preparation, no real
receipt and no ACK.

Guards were installed before any reviewed import, and stack under the suite's
own guards. They cover 117 entry points:

- subprocess, including `_fork_exec`, and `_posixsubprocess.fork_exec`;
- the `os` and `posix` aliases of fork, exec\*, spawn\*, `posix_spawn[p]`,
  system/popen, kill/killpg, `pidfd_open`, wait/waitpid/waitid/wait3/wait4,
  setsid/setpgid/setpgrp, tcsetpgrp, setns, unshare and abort;
- `signal` and `_signal` signal/pthread_sigmask/pthread_kill/raise_signal/alarm/
  setitimer/pidfd_send_signal/sigwait\*/sigtimedwait/siginterrupt/
  set_wakeup_fd/sigpending;
- fcntl flock/lockf/fcntl/ioctl and resource prlimit/setrlimit;
- the `socket` and `_socket` classes and their resolvers, and ssl contexts and
  wrapping.

`unittest.mock`, ssl and socket were pre-imported before the guards were
installed. The hang watchdog is a profile hook that calls `_exit` on this
process. It uses no thread and no signal.

Scratch and evidence are in `/home/ajs/.cache/pb-phase6/build-cost-review-r3`.
`TMPDIR`, `GOTMPDIR` and `CLAUDE_CODE_TMPDIR` all point to its `tmp/`. The
harness `/tmp` returned EDQUOT on every command, so all output was redirected
into the cache and read back. `/tmp` was never modified, moved or symlinked.
The author's suite hardcodes its fixture directory in their cache; I redirected
`TemporaryDirectory` into mine, so their `mock-fixtures/` stayed empty and
unwritten. I wrote only this file and that cache.

**Decision: SOURCE+MOCK DOES NOT YET PERMIT authorizing the native
owned-helper protocol on this source hash.**

- R2-A, R2-B and R2-C are closed within the regions they named.
- HOLD kept the lock and the leader pin under every in-loop fault I injected:
  every `BaseException` class, journal failure, ownership loss, transient
  procfs error and the exact deadline.
- Three new defects remain in the **stop-signal delivery model** next to A/C
  (R3-1 to R3-3), plus a small cleanup gap (R3-4) and 21 surviving test-gap
  mutants.
- The correction changes how the supervisor handles signals. That is what
  native targets 3–5 would exercise, and the ready preparation binds the
  runner hash. So fix first, then do a **narrow** delta review. A full review
  is not needed.
- Even a successful native helper run would not authorize a build, a ready
  preparation, hash work or a quiet window.

## Identities (before = after, `ids-before.json` = `ids-after.json`, `e6693023…df51`)

| File | SHA-256 |
|---|---|
| `harness/phase6_build_cost.py` (74,080 B) | `1bd3a6cb550277767bf0934ced2db41b812c1ed2a48b4758eced0042df7d46f1` |
| `harness/test_phase6_build_cost.py` (71,964 B) | `ab1a8706cbde6517ae08363b4bf88d60bcb5eee358b527d11436392a03982494` |
| `docs/phase6-build-cost-plan.md` (20,442 B) | `f3c464c5b8c7ffbde10455fcd7e0a6e18cc183e72321b707b26cb6d8c63d3a27` |
| `r2-corrections/delivery-r2.json` | `f7a5206e6774a950a758c687c256db3fc1218e4a0467188189dd54609adaf90c` |
| R2 review / R1 review | `e815bdbb…94fa` / `4bb147ac…931b` |
| historical ready `prepared-source/prepared.json` (OLD, not admitted) | `75467a917c3b40a08892d0d6c2cbba1c7dce14c2cdc84f1e91779e7ef8f04357` |
| R1 settled draft (draft, not admitted) | `7c6a4b9e1c75fb5c94d18555b46d53dcb77efaeb53deb088f176129712a71cf5` |
| candidate R6 / dependency evidence / `Cargo.lock` | `a90917f8…` / `adee9b5d…` / `aa35f338…` |
| `bin/pb-meter` / `harness/meter.c` | `17c3b734…` / `5d7ce9dc…` |
| author driver `mutation_driver_run5.py` / `mutations-run5/summary.json` | `5e0a5afb…` / `a99e7d32…` |
| `r2-corrections/historical/*` (R2-era runner/tests/plan) | `79c15a90…` / `1fd1b48f…` / `2de80592…` |

The R1 and R2 review caches are unchanged. No fresh ready preparation exists.

## Execution evidence

- **Author suite:** 70/70 pass under R3 guards in 5.2 s on Python 3.14.7.
  Zero forbidden calls by either guard set. The loaded runner hash equals the
  settled hash. The author's fixture directory was empty before and after.
- **Probes:** 22 independent causal probes (P01–P16, with sub-probes). Attempt 1
  ran 21; 20 completed. P09 was invalid because of a probe arity bug: my patched
  `proc_stat` lambda took one argument. P14b raised from `run()`, which is
  itself a finding. Attempt 2 reran the corrected P09, P11b and P14b and added
  P10b. Both attempts and the attempt-1 script are preserved. Zero forbidden
  calls.
- **Independent mutation sweep:** 65 single-site mutants plus a baseline, each
  in its own guarded process, each running the **full** 70-test suite. Every
  site count was asserted to be exactly 1.
  - Baseline passes. 43 killed causally; none were killed only by a guard. 21
    survived. One (C8) hung until the 90 s watchdog. Zero forbidden calls.
  - The substitutions are mine, not the author's. Where I re-hit an author
    site (M06j, M32j), I note it.

## R2 closure

| R2 | Status (source+mock) | Basis |
|---|---|---|
| A (second stop aborts kill) | **Closed within the masked region.** New adjacent R3-1/2/3. | `finish_owned` masks the whole cleanup, HOLD and final journal. Deferred signals are journaled before unmask (P10: the journal at delivery is `verified` with all three deferred). Mutants A3 and A5 killed. Survived: A1 (hold fallback unmasked), A2 and A4 (`except Exception`). The code is right (P08a), but untested. |
| B (expired window skips cleanup) | **Closed.** Residual R3-4. | Independent `now + 5 s` clock and `durable_capture`. Unconditional pre-loop kill after the kernel and leader recheck. Overrun is recorded as an error. P08c: at the exact deadline the result is a kill, `unknown` and no reap, and `group_members` at the deadline raises. Mutants M37i and B2 killed. **B1 survived:** a pre-kill observation failure that skips the kill is untested. |
| C (unknown cleanup unlocks) | **Closed for in-loop faults.** R3-2 escapes are outside the loop. | P08a: 401 hold rounds through `KeyboardInterrupt`, `SystemExit`, `GeneratorExit`, `MemoryError`, `RecursionError`, `InterruptedError`, EDQUOT, ECHILD, `TimeoutError`, `BudgetExpired`, `ValueError` and 50 failed journals. Each round is exactly one `sleep(1)`, there is no tight loop, and it returns only after injected proof. P08b: during ECHILD rounds, 0 kills, no reap, lock held, no end verify, never passed. C1, C3, C6 and C10 killed. **C2, C4, C5, C7, C9, C11 and C12 survived.** |
| D (proc parsing/liveness) | **Closed.** | D1–D4 and M03i/M32i/M32j killed. P09: the disclosed limits behave as described (see R3-10). |
| E (negative sleep) | **Closed.** | E1 killed. |
| F (journal after expiry) | **Closed.** | M19i/M19j killed. |
| G (replaced pass persists) | **Closed, with disclosed limit.** | G1 and G2 killed. P14a: if the invalidation rename also fails, the API returns false but the disk keeps `passed: true`. That is the documented pathological case, and consumers need API success. P14b: a stop during invalidation makes `run()` raise, which fails closed (informational). |
| H (window extraction) | **Closed.** | P15 runs real mock extraction through `prepare`: ready, verified, and both a deleted and a reformatted `.cargo-checksum.json` are rejected by the extracted-tree receipt. `run` never extracts (author mutant). |
| I / N14 receipt | **Mostly closed.** Residual R3-6/7/8. | Exact keys, `run_id`/output binding, ACK file ≤4096 B with digest-only reporting, and a same-byte receipt copy. P11c: consumption precedes verify and child. P11a: a symlinked receipt alias maps to the same marker and is refused. A planted marker symlink is refused by `no_links`, and the victim is untouched. R1, R5, R6, R7, R10, M15i, M17i, M18i and M38i killed. **R2, R3, R4, R8 and R9 survived.** |
| J (stdin/fds) | **Closed at source.** | J1 killed. The host Python exposes `POSIX_SPAWN_CLOSEFROM`; glibc behavior is still a native question. |
| K, L, M, N | **Closed.** | K1, L1, M1 and N2 killed. My suite run left no fixtures. P10b adds a report-level residual to M (R3-9). |
| R2 survivors | 15 of 17 sites killed by independent variants. | M03, M09, M11, M12 (sigdef), M15, M17, M18, M19, M24, M25 (both halves), M31, M32, M34, M37 and M38 are killed. **M06i** (fallback without its own kernel recheck) and **M10i** (SIGCHLD check pointed at SIGINT) survive. M06j (the author's site) is killed. The author's M06/M09/M32/M37 adaptations are equivalent in intent. |

## New findings

### R3-1 — Medium (A, stop contract): a stop delivered at a checkpoint's unmask is swallowed, and the runner keeps launching builds

**Locations:** `atomic_json` 264 (`blocked_signals`), `checkpoint` 1278-1290 and
`orderly_signals` 833-844.

**Mechanism:**

1. A SIGINT, SIGTERM or SIGHUP arrives during a checkpoint's masked
   write/fsync.
2. It is delivered at the unmask after a successful replace. The handler raises
   `InterruptedError` out of `atomic_json`.
3. `checkpoint` treats that as a write failure, retries, succeeds and returns
   normally.
4. The stop becomes only `report['errors']`. It is not a sample error, so the
   schedule continues.

**P01a/P01b:** I injected SIGTERM inside the pre-launch checkpoint and inside the
post-capture checkpoint. In both cases, both builds launched and each sample
`passed`. Only the report failed. Checkpoints happen several times per sample,
and fsync can take milliseconds or more, so this is reachable by a single
operator stop.

`invalidate_report` has the same shape. P14b shows the exception escaping
`run()`, which fails closed.

**Correction:**

- Use handlers that set a stop flag, with no asynchronous raise.
- Check the flag before each launch, in the polling loop, and after each
  checkpoint.
- At minimum, `checkpoint` must re-raise the stop after its retry succeeds.

**Tests:** P01a and P01b must show zero launches after the stop.

### R3-2 — Medium-Low (A/C, structural): handler raises before the cleanup mask can escape `execute`, and the run boundary cannot see the PID

**Locations:** `execute` 1197-1208, `finish_owned` 1139, `hold_owned`
1125-1134, and `run` 1429-1440.

Cleanup is masked only once `finish_owned`'s `pthread_sigmask(SIG_BLOCK)`
completes. Before that, every Python check point can raise. CPython runs all
tripped handlers at the first check. The first raise leaves the rest tripped,
and they raise at later checks.

**What escapes:**

- **Burst.** Up to three distinct stop signals in flight are absorbed safely:
  in the polling sleep, in the `except` at 1197, and at `finish_owned` entry,
  which is caught at 1203. A fourth delivery inside the microsecond window
  before the hold fallback's block reaches `run`.
  - **P02** (raises modelled at the next block calls): 0 kills; lock released
    with the group live, unkilled and unreaped; the end full verify ran (3
    verifies); STOP left blocked.
  - Only `process.json` (`unknown`, PID present) records the child.
- **Bookkeeping failure.** `cleanup_journal`'s `sigpending`/`note_error` lines
  sit outside the hold `try`.
  - **P03:** a persistent `MemoryError` there escapes HOLD. The lock is
    released with an escaped member alive and the leader unreaped (its pin is
    lost at process exit), and the end verify ran.
- **Post-proof residual.** The second tripped handler raises in `note_error`
  at 1204, after reap.
  - **P10b:** the report says `cleanup_status: not_observed` and
    `group_empty_verified: null` for a launched sample, while disk says
    `verified`. This is safe, but the evidence is wrong (R3-9).
- **Root cause in `run`.** Its defensive boundary (1432) inspects `record`
  only after `record.update(execute(...))`, so a raising `execute` leaves no
  PID to act on.

**Correction:**

1. Use the flag model from R3-1, and supervise permanently masked: poll with
   `sigtimedwait`/`sigpending` and act on the flag instead of on async raises.
2. Pass a caller-owned outcome into `execute`, so the PID is visible to `run`
   even when `execute` raises.
3. `run` must treat any sample whose `process.json` shows a PID without
   verification as held (lock retained, no end verify).
4. Put the entire hold-loop body, including `cleanup_journal`, inside
   `try/except BaseException`, and make the outer fallback non-escaping.

**Tests:** kill A1, C4 and C5; P02, P03 and P10b invariants.

### R3-3 — Low-Medium (needs native confirmation): a `SIG_BLOCK` that raises leaves stop signals blocked permanently

**Locations:** `blocked_signals` 107-113.

CPython's `signal_pthread_sigmask_impl` calls `PyErr_CheckSignals()` after
**every** successful call, including `SIG_BLOCK`. A handler tripped just before
the block therefore raises after the mask is applied, from the line *before* the
generator's `try`. The restore never runs.

**P05:** the raise is absorbed by the R3-1 retry, both builds launch with
INT/TERM/HUP blocked, and the final mask is blocked. The window is tiny. The
consequence is that the runner can then be stopped only by SIGKILL, which loses
the lock and pin with a build live.

**P16:** an inherited STOP-blocked mask from the parent is not refused either.

This depends on reviewer knowledge of the CPython source. I did not execute it
natively.

**Correction:**

- Use the flag model (R3-1/2).
- Otherwise, wrap the block call so it restores on exception.
- `run` should refuse to start if STOP signals are blocked or their handlers
  are not the runner's own.

### R3-4 — Low (B): a transient procfs failure on a known leader suppresses every kill during HOLD

**Locations:** `signal_owned` 1015-1019 and `cleanup_round` 1069.

With `initial` known, `leader_owned` must re-read procfs before every killpg. An
`OSError` (EIO/ENOMEM) aborts the round before any kill. Every hold round
repeats this.

**P04:** the live group ran unkilled for 5 hold rounds, until procfs recovered.
The lock was held throughout, so this fails safe, but it contradicts "observation
failure never skips the owned group kill". The kernel-ownership argument that
justifies the `initial is None` fallback applies equally here: the setsid leader
is unreaped and its PGID is pinned.

**Correction:**

- On a read failure (as opposed to an identity mismatch), use the fresh
  kernel-owned fallback kill.
- Keep refusing to signal on a starttime/group/session mismatch.

**Test:** kill B1, which survived.

### R3-5 — Low: receipt admission

- **R3-6, duplicate keys.** Duplicate JSON keys are accepted, and the last one
  wins. P06: a receipt whose first `output`/`jobs` are decoys passes. The exact
  receipt copy preserves the ambiguous bytes. Reject duplicates with
  `object_pairs_hook`, for the receipt and the prepared manifest.
- **R3-7, remaining-window check.** Admission's check (793) uses
  `timeout+17 s` per sample but omits the 148 s initial target reserve and the
  verify reserve.
  - P07: a 59 s window is admitted and its receipt consumed, the output is
    created, verify runs, and the launch can never happen. R9 also survived.
  - Require the first launch's full reserve before consumption.
- **R3-8, byte-keyed single use.** Single use is keyed by receipt bytes.
  - P11b: if consumption succeeds but output creation fails, a
    whitespace-variant receipt for the same `run_id` is admitted and runs.
  - Receipts are unauthenticated coordination records, so this hardens only
    against accidental reuse. Add a second O_EXCL marker keyed on canonical
    `(window_id, run_id)`.
- **Test gaps:** R2 (re-admission under lock), R3 (copy from a re-read rather
  than from the hashed bytes), R4 (marker fsync) and R8 (whitespace ACK with a
  matching digest) survived.

### R3-9 — Low (evidence): report-level cleanup fields can drop a launched sample

This is P10b, above. It is fixed by R3-2's caller-owned outcome.

### R3-10 — Low/disclosed (procfs topology, P09)

| Simulated `/proc` | Result |
|---|---|
| Unobserved setsid daemon, reparented before the first scan | `verified`, reaped, the daemon survives. This is the **disclosed** limit. |
| Same-session daemon with another PGID | `unknown`, HOLD, never individually signalled |
| Observed setsid child still linked | `unknown`, HOLD |
| Zombie member awaiting reap by init or a subreaper | `unknown`, HOLD, **252 killpg in 5 s** |

The plan (198-204) and the delivery limits accurately say procfs is
observation, not kernel containment. I found no exhaustive-containment claim.
The field names `group_empty_verified` and `verified-before-leader-reap` are
narrower than they read. I suggest adding
`containment_scope: "procfs pgid/session/descendant observation"` to
`process.json` and the report.

For this graph (cc-crate C builds, no sccache, an allowlisted environment),
daemonizing descendants are unlikely. A cgroup scope or
`PR_SET_CHILD_SUBREAPER` would make attribution kernel-backed. That is optional,
not demanded here.

Zombie members should not trigger a re-kill every 20 ms. Native target 9 must
confirm that the runner's actual subreaper reaps orphans.

### R3-11 — Low (disclosure): other default-terminating signals end HOLD

SIGQUIT, SIGUSR1, SIGUSR2, SIGALRM, SIGXCPU and similar signals still terminate
the runner, and that releases the lock and pin. Either list them with SIGKILL
and crash in the plan's limits, or ignore or block them for the supervised
lifetime.

### Informational

- Python starts with SIGPIPE and SIGXFSZ set to `SIG_IGN` (host check,
  `logs/13`). `setsigdef` resets only INT/TERM/HUP. `meter.c:90-92` restores
  SIGPIPE to default for Cargo, so only SIGXFSZ is still inherited as ignored.
  With an unlimited `RLIMIT_FSIZE` this has no practical effect. Adding
  SIGPIPE and SIGXFSZ to `setsigdef` would keep the environment exactly
  normal, and native target 1 should check `SigIgn`.
- Prepared registry sources contain `.cargo-checksum.json`, which Cargo does not
  write under `registry/src`. It is harmless to Cargo but is a layout deviation
  worth one plan sentence.
- Refusals for `POSIX_SPAWN_CLOSEFROM` and SIGCHLD happen only at spawn, after
  consumption and a full verify. Check them at admission.

## Test-gap mutants (survived; full suite, causal)

| Mutant | Gap |
|---|---|
| M06i | Fallback site kills without its own kernel recheck (the round-top recheck masks this) |
| M10i | The precondition test cannot tell SIGCHLD from another signal |
| M12i | The child inherits the parent mask. There is no test with an inherited blocked mask. |
| A1 / A2 / A4 | Hold fallback unmasked; hold or journal catching only `Exception` |
| B1 | A pre-kill observation failure skips the kill |
| C2 | Held sample artifact skip, so "held never passed" rests on other gates |
| C4 / C5 | The `execute` hold fallback and the `run` defensive finish are untested |
| C7 / C11 / C12 | Reap without exit observation; waitid/waitpid identity checks |
| C9 | Hold round turns into a 5 s scan storm |
| R2 / R3 / R4 / R8 / R9 | See R3-5 |
| BND1 / BND2 | Error and observation bounds untested |

C8 (removing the hold `sleep(1)`) was detected only by hanging. Add a direct
assertion of exactly one `sleep(1)` per hold round; P08a shows the current code
satisfies it. Use real-shaped injections rather than tests that would fail on
guards; none of my kills was guard-only.

## Native protocol plan (for a later authorization; nothing ran)

The ten targets in plan 287-296 are realistic and bounded, with these
amendments:

- **Helpers:** use helpers that need no compilation (`python3 -c` or
  `/bin/sh`), the prebuilt `bin/pb-meter` (`17c3b734…`), and existing ELF
  inputs. For example, `/usr/bin/true` as a PIE DYN and `/usr/lib/Scrt1.o` as
  REL. A wrong-architecture input can be crafted as header bytes in Python.
- **Instrumentation:** record every `killpg`/`waitpid`/`waitid` through
  pass-through wrappers to prove "no post-reap signal" without strace.
- **Target 1:** also check that `SigIgn` is empty in the helper.
- **Target 4:** needs a bounded external end. The root harness kills the
  escaped helper by fresh `(pid, starttime)`, then expects release. It also
  needs a wall-clock cap after which root SIGKILLs the runner, accepting the
  documented pin loss for a trivial helper.
- **Target 5:** add the R3-1 single-stop-at-checkpoint case. Bursts and
  pre-block races are not natively reproducible, so they must be closed by the
  flag design, not by native testing.
- **Lock:** use a task-owned lock path for targets 1–7 and 9–10. Only target 8
  touches the common lock, briefly, and only with LOCK_NB.
- **Target 9:** report the subreaper identity and zombie reap latency.
- **Every target:** a per-target wall-clock cap and a root-owned cleanup
  script.

## Next gate

1. **Source+mock correction, narrow.**
   - Flag-based stop model with permanently masked supervision (R3-1/2/3).
   - A caller-visible outcome and a `process.json`-aware run boundary (R3-2/9).
   - Kernel-owned fallback kill on procfs read failure (R3-4).
   - Duplicate-key rejection, the full reserve at admission, and a semantic
     single-use marker (R3-6/7/8).
   - Startup refusal of a blocked stop mask (P16).
   - Disclosure of other terminating signals, or blocking them (R3-11).
   - Tests that kill the 21 survivors and C8 directly, plus P01/P02/P03/P04/
     P05/P10b as regressions.
2. **Narrow independent delta review** of only those changes.
3. **Then** root may separately authorize the owned-helper protocol, amended as
   above.

A fresh ready preparation, its hash work, a root receipt and ACK, the quiet
window and the build itself all remain separately gated. This review makes no
ranking, statistical, native or whole-phase claim.

## Reproduction and artifacts

```sh
R=/home/ajs/.cache/pb-phase6/build-cost-review-r3
export TMPDIR=$R/tmp GOTMPDIR=$R/tmp CLAUDE_CODE_TMPDIR=$R/tmp PYTHONDONTWRITEBYTECODE=1
python3 -I -B $R/ids.py $R/results/ids-before.json
python3 -I -B $R/run_tests.py $R/results/tests-run1.json
python3 -I -B $R/probes.py $R/results/probes-run1.json          # attempt 1 used probes-attempt1.py
python3 -I -B $R/probes.py $R/results/probes-run2-subset.json p09,p10b,p11b,p14b
for m in $(python3 -I -B $R/mutate.py --list); do python3 -I -B $R/mutate.py $R/mutants-run1 "$m"; done
```

| Artifact | SHA-256 |
|---|---|
| `guards.py` | `9976a6fb0f06da3adbc14435593cf82373b6244eec4d4ed4b80e595800bcb486` |
| `run_tests.py` | `696ebdc905011770c2113b7e717e0ef60434775e782a262a6b1f32db1e65427c` |
| `probes-attempt1.py` (run 1) / `probes.py` (run 2) | `7e5b40b1…1529` / `250b1f10bdc9de2e6085655d90eafa8acfe98eb46c65de5d7848733f430d5f9c` |
| `mutate.py` | `c834c3459c197d02ad3727a239d774771fd151720db018c33abc3e20210ccf4e` |
| `results/tests-run1.json` | `770596f6a4b4b00d3412fb60199a46f10ce206805becd91581c80f5af6ba8872` |
| `results/probes-run1.json` / `probes-run2-subset.json` | `8f5ab331…39dd` / `040d9efb…d6ea` |
| `logs/15-mutants-summary.txt` / `logs/12-mutants-run1.rc` | `ab8eef6e…2d4d` / `94008703…29e6` |
| `results/ids-before.json` = `ids-after.json` | `e66930231e4be3545adb43adb713f0daf259bf9ff8d484a9550d876e7df4df51` |

Per-mutant `.py`, `.json` and `.log` files are in `mutants-run1/`. C8 has no
`.json`; its `.started` file and `logs/16-c8.txt` show watchdog exit 124. Every
command log is in `logs/`, numbered 00–20, and none was overwritten.
