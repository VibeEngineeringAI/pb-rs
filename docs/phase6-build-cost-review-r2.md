# Phase 6 build-cost tooling review, round 2

This is a fresh, independent review of the R1 corrections to the build-cost
runner. It is **not** native-build admission, and it does not accept the whole
phase. The scope was offline and bounded: source reading, pure/mock execution,
and causal probes and mutants against simulated process, lock and clock calls.

I ran no Cargo, build, compiler or version probe, readelf, product, fixture,
meter, native helper, network request, real flock or quiet token. I did not
rehash the 1.3 GB toolchain, copy any archive, or extract any real cache. I
did not create a ready preparation or a real receipt, and I mint no ACK here.

Guards were installed before any production or test import. They cover
subprocess, `_posixsubprocess.fork_exec`, fork, exec, spawn, `posix_spawn`,
system, kill, `killpg`, `pidfd_open`, wait, `waitid`, `waitpid`, wait3/wait4,
setsid/setpgid, signal send and mask (including `pidfd_send_signal`), socket
and socketpair, flock/lockf/fcntl, and prlimit/setrlimit. They are stricter than
the delivered suite's own guards.

Artifacts and scratch space are in
`/home/ajs/.cache/pb-phase6/build-cost-review-r2` (`TMPDIR` points to its
`tmp/`). Nothing was written to `/tmp`. The harness's own `/tmp` hit EDQUOT, so
command output was redirected into the review cache and read back from there;
nothing was moved or symlinked.

**Decision: NOT ADMITTED for native supervisor control or any native build.**
N1, N2, N4 and N7–N12 are closed at the source+mock level. N5, N6 and N13 are
closed apart from low residuals. N3 is only partially closed: three new
cleanup defects (R2-A, R2-B, R2-C) can leave an owned, live build group
unkilled, or release the common lock while cleanup is unknown. These must be
fixed and narrowly re-reviewed before root authorizes the separate native
owned-helper control. The N14 receipt schema is feasible, and I recommend it
with amendments (see below).

## Identities (before = after, unchanged)

| File | SHA-256 |
|---|---|
| `harness/phase6_build_cost.py` (62,883 B) | `79c15a906716b8996a0ec1403fadcc185d24693001b9c9fdd814a75ae1dd2dd3` |
| `harness/test_phase6_build_cost.py` (41,888 B) | `1fd1b48f2a07489213c38a60175591d2d807f6978bbe3dcba78381e99e36867e` |
| `docs/phase6-build-cost-plan.md` (16,437 B) | `2de80592b41cb831a4e24040d595279db3f41e783e4fb5da1caf84cf6a65404c` |
| `docs/evidence/phase6/dependency-download-sizes-r6.json` | `adee9b5ddea718bfd6dac4b90347ab8c39fc3ce02b1a4f87c30f9c89092655d7` |
| `docs/evidence/phase6/candidate-review-r6.json` (frozen R6) | `a90917f86241c7df045015250ee69cc4d8090264285be46672bd0b865441fb3e` |
| `Cargo.lock` | `aa35f338bd6b6b803c5e970f021d59f54d2e7435823f75a5baee018d7a1ebb44` |
| `bin/pb-meter` / `harness/meter.c` | `17c3b734…99127c` / `5d7ce9dc…f070e9` |
| draft `prepared.json` (114,220 B, `state: draft`) | `7c6a4b9e1c75fb5c94d18555b46d53dcb77efaeb53deb088f176129712a71cf5` |
| historical ready `prepared-source/prepared.json` (11,277,965 B) | `75467a917c3b40a08892d0d6c2cbba1c7dce14c2cdc84f1e91779e7ef8f04357` |
| `r1-corrections/delivery-r1.json` | `5f792a674512dccc3df3f2c8b0206c1391b60dd2bbd05fe5a18b078f17d0b80e` |
| R1 review doc | `4bb147ac6ee1fc1fab6fd58d71e7e91c08e4902814b51385f37ecca579b0931b` |

The historical copies in `r1-corrections/historical/` hash to the R1 values
(`fbb8daf…`, `8f35b45…`, `90198dd…`, `9d2ff2a…`). The R1 review cache (17
entries) and the author's logs are untouched.

Review artifacts:

| File | SHA-256 |
|---|---|
| `guards.py` | `67c6e725…49d3e` |
| `run_tests.py` | `61a31617…c3900` |
| `probes.py` | `141417f2…f0f52` |
| `mutate.py` | `518e7c02…587a1` |
| `results/tests-run1.json` | `e36f61e9…2030c` |
| `results/probes-run1.json` | `b96042bd…82c31` |
| `results/mutants-summary.json` | `fe98c5df…fd9c8` |

Per-mutant `.py`, `.log` and `.json` files are in `mutants/`.

## Execution evidence

- **Delivered suite:** 48/48 pass under the R2 guards in 2.03 s on Python
  3.14.7. No forbidden call was made, either by R2's guards or by the suite's
  own.
- **Probes:** 15 independent causal probes (P01–P15). Zero forbidden calls.
- **Independent mutation sweep:** 38 mutants, each in its own guarded process
  with a 120 s timeout. The baseline passes. 21 mutants were killed and 17
  survived; there were no timeouts and zero forbidden calls. I read the
  author's M1–M6/M8 summary (`cf36bc13…`, all killed) but did not rerun it. My
  sweep does not reuse their substitutions.
- **Real-data checks (selected, bounded):**
  - `anchors()` on the real draft passes against the pinned 11 MB reviewed
    manifest and the 5.8 KB candidate manifest (P01). No tree or toolchain
    hashing was involved.
  - The draft is correctly rejected by `admission` because it is a draft.
  - The draft's `runner` equals the current runner.
  - The existing worktree `target/release` (aws-lc-sys and zstd-sys build dirs
    present) has 0 symlinks, 0 special files, 2,267 files and 609,658,232
    bytes. That fits `tree()` limits, so the R1 symlink concern does not show
    up in the observed graph.
  - `/proc` is mounted without `hidepid`. 559 entries were read, and 0 of them
    were undecodable at the time.

## Per-finding closure

| R1 | Status | Basis |
|---|---|---|
| N1 self-certifying manifest | **Closed** (source+mock) | Caller digest is checked before parsing (`read_json` 196-207, `run` 1160-1162). P01: the real draft passes `anchors`; all 7 tampers are rejected (version, meter SHA, candidate source hash, toolchain path, commit, features, reviewed-role SHA). M14, M35 and M36 were killed. A ready preparation is still pending; root must take `--prepared-sha256` from its own review of that ready file, never from the preparer's report. |
| N2 hashing before lock | **Closed** | P02: exactly 2 bounded reads (prepared ≤512 KiB, receipt ≤64 KiB), then LOCK_NB. Busy lock leaves no output and does no heavy work. M13 was killed. |
| N3 post-reap kill / PID reuse / spawn gap | **Partially closed** | See below. Masked spawn, PID journal, WNOWAIT pin, starttime/group/session rechecks, two complete scans, reap last and no post-reap signal all hold in mocks (M01, M02, M04, M05, M07, M08 killed). The kernel-owned fallback is sound because a `setsid` child's PGID cannot change and is pinned while unreaped (P09). **Open:** R2-A, R2-B and R2-C (blocking), R2-D and R2-E. Test gaps: M03, M06, M09–M12 and M32 survived. |
| N4 capture lost / non-dict metrics | **Closed** | Raw meter identity and metrics are checkpointed before the target hash (M20 killed). P10 uses real `meter.c` shapes: exit 0, exit 101, `{"signal":9}`/-9, truncated and missing are all classified correctly. Residual: M34 (`resource_source` check untested). |
| N5 `.pending` poison | **Closed**, low residual | Unique temp file, cleanup, masked write, retry (M21 killed). Residual R2-F, plus M19 (the durable checkpoint after expiry is untested on disk). |
| N6 deadline overrun | **Closed at chunk granularity** | All pre-launch hashing precedes the guard. The reserve is observed. There is a second spawn-time guard. No full rehash runs after expiry (the delivered tests plus M30 confirm this). Residual R2-N; M31 survived. R2-B is the interaction with cleanup. |
| N7 meter vs Cargo exit | **Closed** | P10. The no-op identity gate holds (M22 killed). Residual M25: the meter-rc/cleanup part of the readelf gate is untested. |
| N8 C toolchain overstated | **Closed** (documentation + bounded capture) | Plan 217-225 is accurate. `native_build_outputs` is bounded at 64 KiB/1 MiB. |
| N9 weak ELF | **Closed for the parser on mock text** | M23 was killed. M24 (empty-stderr requirement) survived. Parsing real `readelf` text is a native gate. |
| N10 private cache extras | **Closed** | M27 and M28 were killed. Low residual: `root/home` (HOME) is not checked to be empty or bound. |
| N11 partial extraction | **Closed** | M26 was killed. The poison-on-budget-expiry behavior is disclosed (plan 233-234). |
| N12 checksum vs member validity | **Closed** | M29 was killed. |
| N13 late error with `passed` | **Closed for transient failure** | Residual R2-G (persistent failure). |
| N14 token not window-bound | **Schema feasible; select with amendments** | See the receipt decision. M15, M17, M18 and M38 survived. |

## New findings

### R2-A — Medium-High (N3): a signal during cleanup aborts it before the kill

`orderly_signals` (769-780) installs handlers that raise `InterruptedError`.
`execute`'s `finally` cleanup (962-1026) is not signal-masked. A second
SIGINT/TERM/HUP during the ≤5 s cleanup, for example while the first `/proc`
scan runs, is caught by `except BaseException as cleanup` (1020). That exits
the loop with no SIGKILL sent.

Probe **P03**: a live member `40001`, a running leader, and events
`spawn … observe-exit, scan`. The result is `killed=false`, the live member
remains, `cleanup_status=unknown`, and the run continues. Combined with R2-C,
the build keeps running after the lock is released.

**Correction:**

- Run the whole cleanup, from the fallback through the final `process.json`
  journal, inside `blocked_signals()`.
- Record `signal.sigpending()` as `deferred_signals`. Let the pending signal
  fire only after the journal.
- **Test:** assert that `STOP_SIGNALS ⊆ mask` at every scan, kill and reap
  event, and that an injected interrupt at the first scan still yields a kill.

### R2-B — Medium (N3/N6): cleanup that starts past the window reserve never kills

`cleanup_deadline = min(deadline - REPORT_RESERVE_S, now + CLEANUP_S)` (985).
If cleanup begins at or after `deadline − 2`, the loop body never runs, and
`TimeoutError('group cleanup unknown')` follows with no signal sent. The same
happens if `leader_owned`/`proc_stat` raise `BudgetExpired`; they do because
`regular_bytes` calls `check_budget()` (137) under the active budget.

Probe **P04**: the launch guard is satisfied (10 s timeout + 17 s reserve). One
40 s runner stall happens during the sample (for example, memory pressure from
the build). Result: `TimeoutExpired`, then cleanup unknown, then
`process checkpoint: BudgetExpired`, with `killed=false` and the live member
still running. M37 (dropping the window coupling) survives, so this path is
untested.

**Correction:** the leader is unreaped, so its PGID is pinned and owned.

- On cleanup entry, send `killpg(pid, SIGKILL)` **unconditionally** after the
  `leader_owned` recheck. This is harmless for a zombie-only group, and it also
  removes the scan-miss race.
- Run cleanup `/proc` reads under `durable_capture()` with an independent
  `now + CLEANUP_S` bound.
- Record any window overrun as an error, never as a reason to skip the kill.
- **Test:** the P04 scenario must show a kill.

### R2-C — Medium-High (N3): unknown cleanup releases the common lock and loses the pin

On `group_empty_verified=false`, `run` does three things (P05 event order:
`lock-acquired, full-verify, full-verify, execute-unknown-cleanup, full-verify,
LOCK-RELEASED`):

1. It still performs the end full verify (gigabytes of reads).
2. It exits `quiet_lock()`, which calls `LOCK_UN`.
3. It returns.

When the process exits, init or a subreaper reaps the pinned zombie leader. The
recorded PGID is then **no longer pinned**, so a later root `killpg(pgid)` could
hit a reused group. Escaped session members are never signalled individually
(P06: one group kill, escaped `40003` still alive, unknown). Another
participant on the same lock (`phase6_workloads.py:28` uses the same
`pb-rs-performance.lock`) could then start while an orphaned compiler still
runs.

Plan 187-190 says "the leader is not intentionally reaped". That is true only
for the runner's lifetime, and the plan does not mention lock release.

**Correction (root selects; at least one of 1 or 2):**

1. **Hold.** On unknown cleanup, skip the end verify. Journal
   `cleanup_status: unknown-held`. Keep both the flock and the leader pin, and
   wait for an explicit root action (for example, a root-written release file),
   with periodic `/proc` observation.
2. **Durable quarantine.** Before any release, `O_EXCL`-create a quarantine
   marker beside the common lock (with PID, PGID, starttime and session). Every
   participant must refuse to proceed while it exists, even after acquiring
   LOCK_NB.
3. **Optional extra.** Pass the lock's open file description into the build
   group (`POSIX_SPAWN_DUP2` to a fixed fd, no explicit `LOCK_UN`), so any
   surviving member, including an escaped one, keeps the flock held.

Document that root must not signal a recorded PGID after runner exit unless a
fresh starttime/session match is observed. **Test:** an unknown outcome must
show neither lock release nor end verify unless a quarantine marker exists.

### R2-D — Low-Medium (N3 liveness): any unrelated unparsable `/proc` entry makes every scan incomplete

`proc_stat` decodes the whole stat file strictly (866). `group_members` treats
any non-`FileNotFoundError` failure as incomplete (895-896). Two things cause
that:

- A comm cut mid-UTF-8 by the kernel's 15-byte limit (`UnicodeDecodeError`).
- An exiting process (`ProcessLookupError`/ESRCH).

Either one fails every scan on the whole host. Probe **P07**: in a simulated
`/proc` whose group is actually empty (zombie leader only), cleanup stays
`unknown`, the leader is never reaped, and one harmless kill is sent. This
fails safe but blocks liveness, and it is latent today (0 of 559 live entries
undecodable).

**Correction:**

- Parse bytes (`rpartition(b')')`).
- Treat ENOENT/ESRCH as vanished.
- Keep EACCES and other errors as incomplete.
- Test both cases.

### R2-E — Low (N3): the timeout boundary raises a negative-sleep ValueError

`time.sleep(min(0.02, sample_deadline - time.monotonic()))` (958) re-reads the
clock after the deadline comparison. Real CPython raises
`ValueError: sleep length must be non-negative`. Probe **P08** (scripted
clock): the error is the ValueError and `timed_out=false`. Cleanup still works,
so this is misclassification only. **Correction:** compute `remaining` once,
raise the timeout if `remaining <= 0`, and otherwise sleep
`max(0, min(0.02, remaining))`.

### R2-F — Low (N5): the final `process.json` journal is not durable after budget exhaustion

`execute` calls `atomic_json(sample/'process.json')` (1024) under the active
budget. `BoundedWriter` checks the budget, so after expiry the journal is not
written. P04 records `process checkpoint: BudgetExpired`, and P12 confirms it
directly. This contradicts plan 154-156 ("bounded journal writes can finish
after work budget exhaustion"). `checkpoint()` is correct. **Correction:** wrap
both process journals in `durable_capture()`.

### R2-G — Low-Medium (N13): disk can say `passed: true` while `run` returns false

Sequence: the final checkpoint's `os.replace` succeeds with `passed: true`.
Then the directory `fsync` fails, and every retry fails (for example, EDQUOT).
`run` returns `passed=false`, but `report.json` on disk is `passed: true`
(probe **P11**). **Correction:** on final-checkpoint failure, invalidate the
on-disk verdict. Do this with a metadata-only `os.replace(report.json →
report.unverified.json)` or an `O_EXCL` `INVALID` marker that consumers check.
Alternatively, commit the pass as a separate marker written only after the
report and directory fsync succeed.

### R2-H — Low-Medium (measurement validity): window-internal warm extraction and pre-sample full verifies

Inside the lock, the order is `verify → warm-extract → verify → launch base`
(P15). Warm extraction writes the unpacked sources (~265 MB in the historical
receipt) without `syncfs`, so only the **base cold** sample can overlap that
dirty-page writeback. Each sample is also preceded by a full verify, which
reads the 1.3 GB toolchain and re-gunzips every archive, so every build starts
with that page cache hot. This is uniform across samples, but it must not be
read as "cold toolchain".

**Correction:**

- Move extraction into `prepare`, so the ready preparation includes verified
  extracted sources and "prewarmed" happens before the window. Otherwise,
  `syncfs` after warming and record how long it took.
- State the per-sample hot toolchain cache explicitly in the plan's
  interpretation.

### R2-I — Low (N14): receipt amendments

These were found with P14:

- Unknown extra fields (`features`, `output`, …) are admitted.
- One receipt admits any number of outputs within the window (replay).
- The ACK plaintext is passed on argv (`--quiet-window-token`, 1273), where it
  is visible in `/proc/*/cmdline`.
- No test covers the jobs/timeout/warm mismatch, future start, overlong window
  or window-ID length checks, so mutants M15, M17, M18 and M38 survived. P14
  shows those checks do work.

### R2-J — Low: inherited stdin and foreign file descriptors

`posix_spawn` only duplicates fds 1 and 2 (935-936). The build group inherits
the runner's stdin and any non-CLOEXEC fds from the runner's parent. An
orphaned member holding a coordinator pipe could keep that pipe open.
**Correction:** open fd 0 as `/dev/null` with `POSIX_SPAWN_OPEN`, and add
`POSIX_SPAWN_CLOSEFROM` from 3 (Python 3.13+; the host runs 3.14.7). Verify
glibc support natively.

### Lower-severity notes

- **R2-K Low (hygiene).** `run` never removes its owned scratch directory
  `TMP/build-cost-<24hex>` (1186-1187), and the delivered tests do not redirect
  `b.TMP`. Each suite run leaks 14 empty directories. The shared
  `/home/ajs/.cache/pb-phase6/tmp` already holds 749 of them; I left them
  untouched because they are not mine. **Fix:** tests patch `b.TMP`, and the
  runner removes its own empty scratch directory.
- **R2-L Low.** `prepare`'s `preparation-failed.json` is lost when the failure
  is `BudgetExpired`, because the marker write is budget-checked (611-613; P13:
  only `base` and `cargo-home` remain). Write the marker under
  `durable_capture()`.
- **R2-M Low.** `report['group_empty_verified']` is vacuously `true` when no
  sample launched (1248; P02, busy lock). `main` prints nothing for lock-busy
  or admission failure.
- **R2-N Low (N6).** The first launch's reserve uses the observed target-hash
  cost of an empty directory (about 0). Post-build hashing of about 610 MB /
  2,267 files is unreserved for that sample. This fails safe (chunk checks
  mark the run incomplete) but can waste a window. Seed a conservative minimum
  target reserve.

## N14 receipt proposal decision

**Recommendation: root should select the caller-bound receipt schema**, rather
than plain window arguments, with the amendments below. It is feasible and
safe as a binding:

- Bytes are bound by digest before parsing.
- `prepared_sha256`, jobs, the full timeout, the warm condition and the ACK
  digest are enforced (P14).
- UTC must be explicit (a `Z` suffix or `+00:00` offset; naive and non-UTC
  offsets are rejected).
- Expired, future, over-four-hour and too-short windows are rejected.
- UTC is converted to a monotonic deadline once (P14: offset 500.0 s as
  expected).
- The receipt identity is rechecked before each sample and at the end (M16
  killed).

The receipt is evidence of a coordinator decision, **not permission**. The
issuer field, token syntax and digests do not prove the host is quiet. The
coordinator does not need to hardcode the full policy in the runner beyond
these fields.

Required amendments (source+mock gate):

1. Exact key set; reject unknown fields.
2. Single use: either a `run_id` that must equal the output basename, or an
   `O_EXCL` per-receipt marker under `runs/`.
3. Take the ACK from a file or stdin, not argv, or drop the plaintext and keep
   only the coordinator-recorded digest.
4. Copy the receipt bytes into the run directory.
5. Add tests that kill M15, M17, M18 and M38.

## Gates

**Source+mock gate (before native control).**

- Fix R2-A, R2-B and R2-C. Fix R2-D through R2-G with the recommended lows.
- Add causal tests that kill these surviving mutants:
  - M03 (escaped members ignored);
  - M06 (fallback signal without kernel ownership);
  - M09 (PID journal after procfs: the final journal currently hides this);
  - M10 and M11 (SIGCHLD and single-thread preconditions);
  - M12 (child `setsigdef`);
  - M19 (durable checkpoint on disk after expiry);
  - M24 (ELF stderr);
  - M25 (artifact gate's meter-rc/cleanup terms);
  - M31 (spawn-time launch guard);
  - M32 (`group_members` escape tracking);
  - M34 (`resource_source`);
  - the N14 set listed above.
- Correct plan 154-156, 187-194 and 247-255 so they match.
- A narrow delta review of the supervisor, checkpoint and receipt changes is
  enough.

**Native protocol gate (only after that re-review, and only when root
separately authorizes it; none of this ran here).** Use a task-owned trivial
helper, never Cargo or pb. Targets:

1. `posix_spawn` with `setsid`: child `pgid == sid == pid`. `/proc/<pid>/status`
   `SigBlk` and `SigIgn` exclude INT/TERM/HUP.
2. WNOWAIT pin: the exited leader stays `Z` with a stable starttime.
   `killpg` on a zombie-only group succeeds.
3. A same-group grandchild that outlives the leader is found by the scan, then
   killed, then two clean scans, then reap. There is no post-reap signal.
4. An escaped (`setsid`) member gives unknown with no reap, plus the selected
   R2-C hold or quarantine behavior.
5. Timeout with a TERM-ignoring helper: kill succeeds and the meter metrics are
   absent. SIGTERM delivered to the runner during cleanup still produces a
   kill (R2-A).
6. The real `bin/pb-meter` (`17c3b734…`) wrapping a helper that exits 0, exits
   101 and is SIGKILLed, parsed byte-exactly.
7. `readelf -h -l -S -n` on the helper (DYN PIE) is accepted with empty stderr.
   A non-x86_64 or REL object is rejected.
8. Real LOCK_NB contention against a second owned holder: zero heavy work and
   no output.
9. Host `/proc` scan cost and complete-scan rate over repeated scans
   (ESRCH/decoding liveness).
10. Lock-fd and stdin inheritance match the chosen design.

Native success in these controls would still not admit a build-cost
measurement. It would only open the window for root's separate
ready-preparation and receipt decision.

## Reproduction

```sh
R=/home/ajs/.cache/pb-phase6/build-cost-review-r2
TMPDIR=$R/tmp PYTHONDONTWRITEBYTECODE=1 python3 -I -B $R/run_tests.py $R/results/tests-run1.json
TMPDIR=$R/tmp PYTHONDONTWRITEBYTECODE=1 python3 -I -B $R/probes.py > $R/results/probes-run1.json
for m in baseline M01_kill_without_ownership_recheck ...; do   # names: MUTANTS keys in mutate.py
  TMPDIR=$R/tmp timeout 120 python3 -I -B $R/mutate.py "$m"; done
```

Logs are in `$R/logs/`. The first suite attempt failed before any test ran.
It was a guard import-order error: `unittest.mock` imports `ssl`, and `ssl`
subclasses `socket.socket`, which the guard had replaced with a plain function.
That attempt's stdout file was overwritten by the rerun. Its traceback is
transcribed in `logs/06a-first-attempt-failure.txt`. The fix was to pre-import
`unittest.mock` and guard sockets with a subclass. No production code ran in
that failed attempt.

No forbidden call was recorded across 1 suite run, 15 probes and 39 mutant
processes. The owned runner, tests, plan and evidence bytes are unchanged.
