# Phase 7 bounded execution driver — r2 (ROOT only)

Status: **r2 implements the fixes for the r1 review** (`docs/phase7-review-bounded-execution-opus-r1.md`):

- B1, B2 and B3 are fixed;
- the cheap N1, N2 and N5 fixes are in;
- N3, N4 and N6 are disclosed (§6).

It is natively tested with tiny self-owned control processes only. It has not been run against the phase7 helper, a CLI, a fixture, Go, a build, a profiler or the common lock.

**Fresh independent Opus R2 review accepts the driver code for this scope.** Root applied the required D1 wording correction below and checked it against the full review; [review](phase7-review-bounded-execution-opus-r2.md), [disposition](evidence/phase7/bounded-execution-r2-disposition.json). This closes the tooling prerequisite only, not actual collector interoperability or performance admission.

This document accepts no protocol, matrix, admission or performance result, and it relaxes no gate. **Admission rule (root):** a run counts only when all of the following hold:

- the process exit code is 0;
- `report.json` says `verdict: ok`;
- every hash and cleanup check passes.

Anything else is retained as failed. Incomplete and failed reports are kept, never deleted.

Files owned by this lane:

- `docs/evidence/phase7/bounded-execution.py`: the driver.
- `docs/evidence/phase7/bounded-execution-test.py`: its native tests.
- this document;
- the caches:
  - `/home/ajs/.cache/pb-phase7/bounded-execution-r1/`: the r1 test runs and logs, preserved unchanged;
  - `/home/ajs/.cache/pb-phase7/bounded-execution-r2/`: r2 runs, logs and results, the r1 source copies, and the causal run of the r2 tests against r1.

The reviewer's cache `/home/ajs/.cache/pb-phase7/review-bounded-execution-opus-r1/` was read only. That includes its retained failed probe and its leak-cleanup record.

No production, harness, capture, comparator, pool, contract or protocol file was edited. These inputs were read and are unchanged:

| File | SHA-256 |
| --- | --- |
| `harness/phase7_bench.py` | `bf630d9a…eef013c2` |
| `harness/phase7.py` | `dc7574f0…6a44fa` |
| `harness/phase7_spawn.c` | `0279d5fb…31f4` |
| `docs/phase7-under30-execution-plan.md` | `295392ac…458a` |

## 1. What it guarantees, and under which conditions

`bounded-execution.py run` runs **one** command. `--limit` can never exceed 1,800.

**The bound is conditional.** The whole driver lifetime ends strictly before `--limit` seconds from the launch anchor **only if** all of the following hold:

- the scheduler keeps running the driver;
- neither the driver nor the processes it must reap are stuck in uninterruptible (D) sleep;
- the host is **not suspended** during the run;
- the driver is **not stopped** (SIGSTOP, SIGTSTP, SIGTTIN/SIGTTOU or a ptrace stop);
- the driver is **not killed by SIGKILL or any unhandled fatal signal**, including Ctrl-\ / SIGQUIT or SIGUSR1/2. An external SIGALRM fires the backstop early and exits71.

This is a conditional software guarantee, not a physical or OS guarantee. Outside these conditions the outcome is one of:

- exit 72 (`late`);
- exit 71 (SIGALRM backstop);
- exit 1 (thread watchdog);
- a signal exit with a provisional, final or absent report, if SIGKILL or an unhandled fatal signal terminated the driver; detached descendants can remain for the next subreaper. No such run is admitted.

Each of these is retained as failed.

Under the conditions above, the lifetime covers:

- the driver's own interpreter start;
- preparation and capability probes;
- the command;
- graceful interruption and forced kill;
- reaping;
- the empty-tree verification;
- the lock and identity end checks;
- report output.

**The anchor** is the driver process's own start time: the `/proc/self/stat` starttime in CLOCK_BOOTTIME clock ticks, floored, which is conservative.

- `--anchor-boottime T` moves it earlier, so time the coordinator spent before the driver (for example writing the approval) is also charged.
  - T must be finite.
  - T must not be later than the driver's start (one clock tick of tolerance).
  - T must not be older than `launch_by` before the driver's start.
  - Otherwise the driver exits 125 **before** arming any alarm and before creating `--out` (N1).
- **Deadline checks** compare CLOCK_BOOTTIME elapsed values. That clock is monotonic and counts suspend.
- **Waits run on CLOCK_MONOTONIC, which stops during suspend.** This covers:
  - the `poll` timeouts;
  - the `ITIMER_REAL` SIGALRM backstop;
  - faulthandler's timed wait.

  After a host suspend inside a wait, the driver and both backstops wake late by the length of the suspend. The checks then classify the run as `late` (fail closed), but the wall bound itself has been exceeded. Nothing on the host was suspended or changed to test this; the conclusion comes from reading the source and the documented clock semantics.

**Default schedule** (seconds from the anchor). The order is enforced strictly, and each value can be reduced to tiny values for tests:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `launch_by` | 30 | All preparation must finish before the command may exec. Otherwise the run fails closed without exec. |
| `graceful_at` | 1710 | SIGINT to the command's process group (Ctrl-C semantics). |
| `kill_at` | 1735 | SIGKILL to the reserved group, then the own-children kill loop. |
| `reap_by` | 1755 | The kill/reap loop must converge by this point. Otherwise the run is `cleanup_unverified`. |
| `finalize_by` | 1770 | Both the verdict decision **and** the completed final write/fsync/summary must happen before this point. Otherwise the process exits 72 (`late`), see §4. |
| `hard_exit_at` | 1780 | SIGALRM backstop: best-effort marker, then `_exit(71)`. |
| watchdog | 1790 | faulthandler thread backstop: traceback, then `_exit(1)`. This works even if the main thread blocks SIGALRM or is stuck holding the GIL. |
| `limit` | 1800 | The strict bound. The validator rejects anything above 1800. |
| `interrupt_grace` | 20 | Time from an external SIGINT/SIGTERM/SIGHUP to escalation. It is capped by `kill_at`. |

**Relation to the root plan** (`phase7-under30-execution-plan.md` §3), which proposed SIGTERM at 1740 and SIGKILL at 1770:

- These defaults are earlier: graceful at 1710 and SIGKILL at 1735. That leaves 65 s for verified cleanup, end checks and output before 1800.
- Combined with the plan's 1,680 s approval lifetime and its budgets of 1680 (QuietWindow modes) and 1620 (FunctionalGuard modes), the bench's own expiry handling and post-guard finalization get about 30 s before the graceful signal.
- The driver does **not** write approvals and does not check the bench's `--budget-seconds`. The plan's registry rule (§3.4) remains root's job.

**Why SIGINT and not SIGTERM for the graceful stop:**

- `phase7_bench.py` installs no SIGTERM or SIGINT handler (checked with grep). SIGTERM would therefore kill the Python coordinator without running any `finally`/context-manager cleanup: no `NativeChild.close`, no `Fixture.close`, no report.
- SIGINT raises `KeyboardInterrupt`, which unwinds through those cleanups. The **actual** SIGINT interoperation with the bench is untested (§6, item 5).

## 2. Process-tree ownership (after reviewing phase7_spawn.c, NativeChild, Fixture)

The phase7 topology is:

- the Python bench coordinator;
- a `posix_spawn` native supervisor in the coordinator's process group. It has `PDEATHSIG=SIGTERM`, and its SIGINT/TERM/HUP handler SIGKILLs the CLI group while the CLI is held by WNOWAIT;
- a `setsid` CLI with `PDEATHSIG=SIGKILL` (relative to the supervisor) and its own children;
- a `start_new_session` Python fixture without PDEATHSIG;
- short `cc --version` or `git` subprocesses in the coordinator's group.

The driver's mechanism follows the existing pidfd / held-WNOWAIT / subreaper policy:

1. **Child subreaper.** The driver sets `PR_SET_CHILD_SUBREAPER` and confirms it with `PR_GET`. It fails closed if either step fails, or if it already has children. Every orphan in the tree becomes the driver's **own child** instead of being reparented to systemd/init. That covers:
   - a fixture whose coordinator died;
   - a CLI zombie whose supervisor died;
   - a double-forked setsid helper.
2. **Reserved command.** The command is forked, then made a fresh session/group leader (`setsid`) with `PDEATHSIG=SIGKILL` and a `getppid` check.
   - It is held at a pipe barrier until the driver has:
     - verified `ppid == driver` and `pgid == sid == pid`;
     - opened its pidfd;
     - probed `pidfd_send_signal(..., PIDFD_SIGNAL_PROCESS_GROUP)` with signal 0. This needs Linux 6.9 or later; the run fails closed if the probe fails.
   - Only then does it `execve` the exact argv. Exec success is confirmed through a CLOEXEC error pipe.
   - The command's exit is observed with `waitid(P_PIDFD, WEXITED|WNOHANG|WNOWAIT)`. It is **reaped last**, so its PID, and with it the group's anchor, cannot be recycled while the driver still signals the group.
3. **Signals go only to:**
   - (a) the reserved pidfd, optionally with the group flag;
   - (b) a pidfd opened on one of the driver's **own still-unreaped** children.

   Only the driver can reap its children. SIGCHLD has a Python handler, never `SIG_IGN` or `SA_NOCLDWAIT`, so the kernel never auto-reaps them, even if SIGCHLD was inherited as ignored. That makes (b) reuse-safe.

   The driver never sends a numeric `killpg`, never `kill(-1)`, never matches by name, and never scans `/proc`. It reads only `/proc/<driver>/task/*/children` and the stat/cmdline of its own unreaped children.
4. **Kill loop (top-down):**
   - record the identity of every own child;
   - SIGKILL the reserved group;
   - SIGKILL the command;
   - then repeat until the deadline: list own children → record → SIGKILL the live ones → reap the zombies.

   Killing a parent hands its children to the driver, so separate sessions are reached generation by generation. The loop converges only when:
   - the command has exited;
   - no other own child remains after a **re-list that follows the reaps**, which closes the reparent-during-reap race.

   The command is then reaped with `wait4` (its rusage is recorded). Finally `waitid(P_ALL, WNOHANG|WNOWAIT)` must return **ECHILD**. With the driver as subreaper, ECHILD proves that no descendant remains in the driver's reach.
5. **During the run,** zombie orphans adopted by the driver are reaped promptly, as init would reap them. Live adopted orphans are recorded with their identities.
6. **When the command exits on its own,** any still-alive own child is a **leak**. It is recorded (`alive_after_command_exit`), then killed and reaped, and the verdict is `leaked` (exit 3).

**How each phase7 layer is handled (by design reasoning, not by a run of the real helper):**

| Layer | Graceful SIGINT to the group | Forced kill |
| --- | --- | --- |
| Bench Python | Receives SIGINT and unwinds through `KeyboardInterrupt`. | Group SIGKILL. |
| Native supervisor | Its stop handler SIGKILLs the CLI group immediately, as with a terminal Ctrl-C. | Group SIGKILL, or the driver's loop once it is adopted. |
| CLI | Killed by the supervisor. | PDEATHSIG SIGKILL when the supervisor dies. Its zombie is reaped by the driver. |
| CLI descendants | — | Adopted by the driver and SIGKILLed. |
| Fixture | Closed by the bench's own `Fixture.close`. | If the bench died first, it is adopted and killed. |
| Common flock | — | Released when the bench's last fd closes. |

The pure-Python mirror tests (§5) exercise these exact shapes.

## 3. Interruption policy

- **First** external SIGINT, SIGTERM or SIGHUP: record it, send the group SIGINT, then escalate at `min(now + interrupt_grace, kill_at)`.
- **Second** external signal: SIGKILL immediately.
- **Before launch:** an interrupt aborts without exec. The setup child, if one was forked, is SIGKILLed and reaped as the driver's own unreaped child.
- **After `go`, while waiting for exec confirmation** (N2): an interrupt stops the wait at once, and the policy above is applied through `supervise`.
  - This is safe because the reserved group is signal-safe both before and after exec.
  - The report records `child.exec_confirmation: abandoned …`.
  - An exec failure after that point is seen only as the command's exit status (for example 127, or SIGPIPE on the closed error pipe), not as an exec-stage record.
- **During cleanup:** signals are only recorded.
- **During end checks:** signals still pending are recorded (phase `end_checks`) **before** the final decision, so they produce `interrupted` (N5).
- **After the decision:** signals are recorded only as events (phase `post_decision`) and do not change the outcome.
- The command is in its own session, so a terminal Ctrl-C (SIGINT) reaches only the driver, which applies this policy. Ctrl-\ (SIGQUIT) is an unhandled fatal signal and does **not** follow the Ctrl-C cleanup policy. SIGUSR1/2 and other unhandled fatal signals likewise terminate it; an external SIGALRM invokes the early backstop exit71.
- **Do not kill the driver with SIGKILL or any unhandled fatal signal** (see limitation1).

The child's signal state is reset regardless of what the driver inherited:

- INT, TERM, HUP, QUIT, PIPE, XFSZ, CHLD, ALRM, USR1/2 and TSTP/TTIN/TTOU are set to their default dispositions;
- the signal mask is empty;
- all fds ≥ 3 are closed;
- stdin is `/dev/null`.

This holds even when the driver itself inherited SIGINT or SIGCHLD as ignored, or SIGTERM as blocked, as happens in a background shell job. The bench's `SIGCHLD == SIG_DFL` requirement is therefore met.

## 4. Evidence, verdicts and exit codes

`--out` must be a new directory; its parent must already exist. The driver writes:

- `events.jsonl`: raw, timestamped from the anchor.
- `report.json`: provisional (`status: running…`) while running, then replaced atomically with the final version. The final version is written to a temp file, fsynced, renamed, and the directory is fsynced.
- `child.stdout` and `child.stderr`: the raw output, unmodified.
- `watchdog.txt`: created at launch for every launched run. It stays empty unless the thread backstop fires (N6).

A one-line JSON summary goes to stdout. The last event in `events.jsonl` is a best-effort `exit` record carrying the exit code assessed immediately before that event. Completion can cross the deadline while writing it, so it can understate the actual process exit code. It is not fsynced.

**The process exit code is authoritative (B2).** If stdout was already closed when Python started, `sys.stdout` may be `None`: the current final flush then exits1 even if the written report says ok. This optional R2 finding is disclosed without a code change; it remains failed and unadmitted. Exit0 establishes completion of final write/fsync/print before `finalize_by`, not process termination by that earlier checkpoint; the still-armed backstop governs final teardown.

1. The driver decides the verdict and exit code, writes them into `report.json`, and only then performs the final event write, the report write and fsync, the events fsync and the summary print.
2. The report **cannot record its own write completion.** Its `timing.decision_*` fields and `decision_scope` say so explicitly.
3. After those steps the driver reassesses:
   - if any of them failed, the exit code becomes **70**;
   - otherwise, if completion is at or after `finalize_by`, it becomes **72**.
4. The stdout summary carries the actual `verdict` and `exit_code`, the report's `report_verdict` and `report_exit_code`, `completion_elapsed` and `post_decision_issues`.

A report saying `ok` together with any non-zero exit code (70, 71, 72 or 1) means the run is **unverified**. If the final write failed, the earlier provisional `running` report is left in place, which is incomplete, and the exit code is 70 (N3).

**The report records:**

- the exact argv as strings and as hex bytes, for both the command and the driver;
- the cwd;
- the environment, as a key list and a sha256, plus the values of PATH, TMPDIR, GOTMPDIR, CLAUDE_CODE_TMPDIR, HOME, PYTHONPATH, LANG and LC_ALL;
- the inherited signal dispositions and mask;
- the capabilities;
- the anchor;
- the pre/post sha256 of argv[0] and of every `--hash` file (regular files only, opened `O_NONBLOCK`, so a FIFO cannot block preparation).
  - A post hash skipped because the run is past the midpoint between `reap_by` and `finalize_by` is an **issue → 70** (B1).
  - A post hash that errors or differs gives `identity_drift` (73).
  - There is no deadline *inside* hashing one file. A huge or stalled file can overrun to `late` (72) or to a backstop (71/1), both of which fail closed.
- the passive `--lock-path` state before and after, read from `/proc/locks` using the dev from fdinfo `mnt_id` → mountinfo, which handles btrfs;
- the command's identity, exit status and rusage;
- every adopted descendant: pid, start_ticks, ppid, pgid, sid, comm, cmdline, signals sent, reap status;
- the timeline;
- `process_cleanup_state`;
- `timing`: `decision_elapsed`, `decision_within_finalize_by` and `decision_strictly_under_limit`, plus a `scope` note saying these are measured before the report's own write.

**Exit codes** (the verdict precedence follows the order listed):

| Verdict | Exit code | Meaning |
| --- | --- | --- |
| `cleanup_unverified` | 70 | Any issue: no ECHILD, no convergence by `reap_by`, lock still held after a launched run, a skipped post hash, a driver fault, or any event/report write, fsync or summary-print failure, including those **after** the decision. |
| `late` | 72 | Decision, or completion of the final write/fsync/print, at or after `finalize_by`. |
| `identity_drift` | 73 | A hashed file changed. |
| `precondition_failed` | 125 | Includes the launch-budget overrun, bad parameters and an existing `--out`. |
| `interrupted` | 130 | — |
| `deadline` | 124 | — |
| `exec_failed` | 127 | — |
| `leaked` | 3 | — |
| `child_failed` | 2 | — |
| `ok` | 0 | — |

Codes outside the table:

- **71:** SIGALRM backstop.
- **1:** the faulthandler watchdog or a Python crash.

In both cases cleanup is **unverified**. The report is provisional, or (N3) it is a final report that the exit code has superseded.

`--lock-path` only observes the lock; it is never acquired. A lock that is still held after a launched run gives 70, because root schedules exclusive use. `--require-lock-free` refuses to launch if the lock is held. That check is inherently time-of-check/time-of-use; the bench's own nonblocking acquire remains the authoritative check.

## 5. Native tests

```
PB_BOUNDED_TEST_DIR=/home/ajs/.cache/pb-phase7/bounded-execution-r2/tests \
  nice -n 10 python3 -I docs/evidence/phase7/bounded-execution-test.py -v
```

### r2 results

There is one final full run, bound to source hashes that were recorded before the run and re-checked after it with `sha256sum -c`.

- Driver hash: `fc814f72…bc0dc`.
- Test hash: `8ba5d278…bef2`.
- Result: **32 tests, OK**, in 21.6 s of wall time, with 1.8 s user and 0.5 s sys CPU.
- Files: `final-run.log`, `final-run-source-hashes.txt`, `tests/results-1792065955-197351.json`.

Earlier runs:

- One focused development run of the r2 tests and the updated anchor test: 10 tests OK in 0.6 s (`focused-run-1.log`).
- A causal check of the r2 tests against the unchanged r1 driver copy (`causal-r1/run.log`): **all 9 failed** (8 failures and 2 errors; N2 is listed twice).
  - The second N2 entry is the subreaper tearDown. On r1 the N2 control is never reaped by the driver, so tearDown recorded it, SIGKILLed it and reaped it.
  - That control was pid 68338, start_ticks 107726068. Its identity was confirmed gone afterwards.

Total test-command wall time for this lane's r2 work was about 23 s.

**r2 tests added.** They are pure in-process tests with a fake clock unless noted:

- **B1:** a post hash skipped at the cutoff → 70, with the issue recorded.
- **B2** (each → 70 unless noted):
  - the final report write fails: the provisional `running` report is kept, and the summary has `report_verdict ok`;
  - the final write completes after `finalize_by` → **72**. The written report says `ok/0` with `decision_scope`, and the last event is `exit 72`;
  - the events fsync fails (a pipe gives EINVAL);
  - the final event write fails (EBADF);
  - the summary print fails (BrokenPipeError).
- **N5:** a SIGTERM pending at end checks → 130, recorded with phase `end_checks`.
- **N1:** subprocess runs with anchors of `nan`, `=-inf`, `=-1e9` and now − 100 s each exit 125, with no `--out` and no command run.
- **N2:** a tiny owned forked session leader whose stand-in "exec" takes 1 s. A SIGINT at 0.2 s:
  - makes `release_child` return in under 0.8 s;
  - `supervise` then sends the group SIGINT (`external SIGINT`) and the control ends by SIGINT;
  - `cleanup` reaps it, and ECHILD is verified.
- **Updated anchor test:** a coordinator anchor 0.97 s old is accepted with `launch_by` 1.0, and preparation is still charged, failing closed.

The driver-run assertions now treat the exit code and stdout summary as authoritative: summary exit code = process exit code, no post-decision issues, and completion before `finalize_by`.

### r1 baseline (re-run unchanged inside the final r2 run)

There are 23 tests. The r1 runs took 21 s of wall time and ≈1.7 s of user CPU. Control processes only sleep, and each bounds itself to 60 s. The test process is itself a child subreaper: any process a broken driver leaked would become its own child, and tearDown would record it, kill it and fail the test.

Every driver run asserts:

- the externally measured wall < limit;
- the summary's exit code equals the process exit code, with no post-decision issues;
- completion and decision both before `finalize_by`;
- the report's exit code equals the process exit code.

What the tests cover:

- **ok case:** exact argv (spaces, an empty argument, UTF-8, a newline, `--`, non-UTF-8 `\xff`), raw stdout and stderr, session leader identity.
- **Child failure.**
- **Stuck child:** SIGINT ignored → graceful at ≥ `graceful_at` → SIGKILL at ≥ `kill_at` → verified before `reap_by`.
- **Graceful deadline exit** with no kill.
- **Parent exit leaving orphans:** a same-group member and a double-forked setsid daemon. Both are recorded as leaks, killed and reaped.
- **Group plus separate-session tree** killed on the deadline. The mid and leaf processes are reachable only through adoption.
- **External signals:**
  - an external SIGINT handled gracefully;
  - an external SIGTERM with a stuck child, escalating after `interrupt_grace`;
  - a second interrupt, which kills immediately.
- **Exec failure:** a bad interpreter gives `ENOENT`, reported at the exec stage.
- **Preparation charged to the launch bound:** with `launch_by` = 1 ms, the child is never forked. In r2, a coordinator `--anchor-boottime` 0.97 s earlier with `launch_by` 1.0 also fails closed, and a future anchor is refused.
- **Refusals before launch:** an existing `--out`, a limit of 1800.5, a broken order, a FIFO `--hash` (no hang) and argparse errors all give 125.
- **Inherited signal state:** ignored SIGINT/SIGCHLD and blocked SIGTERM/SIGUSR1 are reset in the child. The test proves SIGINT is not ignored, because the deadline SIGINT terminates the child.
- **Pending pre-launch interrupt:** a SIGINT pending across exec aborts the run before launch.
- **Self-owned lock file** (asserted not to be the common lock):
  - held before launch → refused;
  - held across the run → 70;
  - a leaked orphan holding the lock is killed → lock free after.
- **Backstops:** the SIGALRM backstop exits 71. The thread backstop exits 1 with the alarm blocked.
- **Production defaults:** they validate, the watchdog fires before 1800, `graceful_at` ≤ 1740, `kill_at` ≤ 1770, and a limit of 1800.001 is rejected.
- **Phase7 topology mirror** (pure Python: a coordinator, a `posix_spawn`ed supervisor with PDEATHSIG and a WNOWAIT-held setsid CLI with a child, and a `start_new_session` fixture), in three modes:
  - **graceful:** the coordinator's own cleanup runs and no kill is needed;
  - **stuck:** the supervisor kills the CLI on SIGINT, and the driver kills the coordinator and adopts and kills the fixture;
  - **abandon:** the coordinator exits; the supervisor and fixture are adopted, and the CLI dies via PDEATHSIG and is reaped by the driver.

  All five identities are dead in every mode.

**Cleanup state, r1:** across the five r1 invocations, all 98 recorded control identities, checked as (pid, start_ticks), were gone.

**Cleanup state, r2:**

- Across the focused run and the final run in `bounded-execution-r2/tests`, all 29 recorded control identities are gone.
- All 22 subprocess-driver reports are final, and every launched one has ECHILD verified.
- The in-process B2 write-failure test deliberately leaves a provisional report, in `r2-out`.
- No product, Go, fixture, helper, build, compiler, profiler or common-lock process or file was touched.

## 6. Known limitations (no overclaim)

Section 1 states the preconditions of the bound. Items 1 and 4 below expand on them.

1. **Driver killed by SIGKILL, any unhandled fatal signal, or a backstop exit.**
   - Ctrl-\ / SIGQUIT, SIGUSR1/2 and other unhandled fatal signals bypass cleanup just like SIGKILL. An external SIGALRM invokes the backstop early (71). The independent reviewer demonstrated SIGUSR1 termination and adopted/reaped the escaped control processes; this was not a product run.
   - The command dies through PDEATHSIG, and the native supervisors then kill the CLI through their own PDEATHSIG handler.
   - The setsid fixture and any other orphan instead go to the next subreaper (systemd --user or init) and are **not cleaned**. Their identities may be in `events.jsonl`.
   - Never SIGKILL the driver. Treat exit 71 or 1 as unverified and inspect before reusing the lock.
2. **Reach.** Processes outside the subreaper's reach are not owned:
   - a nested `PR_SET_CHILD_SUBREAPER` inside the tree;
   - services started through IPC (systemd-run, D-Bus activation, agent autostart);
   - ptrace tricks.

   ECHILD proves only that nothing remains *within the driver's reach*.
3. **The descendant record is not a census.** It contains only processes that became the driver's own children. Grandchildren reaped by their own living parents never appear.
4. **Convergence is bounded, not guaranteed.**
   - Uninterruptible (D-state) processes or a fork storm can prevent convergence by `reap_by`, which gives 70.
   - A thread stuck in D state can also delay even `_exit`.
   - The 20 s slack after `hard_exit_at`, and the 10 s after the watchdog, assume a functioning scheduler, no host suspend (waits use CLOCK_MONOTONIC) and a driver that is not stopped. A stopped driver stops every backstop, including the watchdog thread.
   - **N4:** on the precondition and abort paths, before or without a launch, only the SIGALRM backstop is armed. The thread watchdog starts only after the fork, by design: there is no thread before the fork.
   - **N3:** a backstop can fire after the final report is written. The exit code (71/1) then supersedes the report.
5. **The graceful stop is Ctrl-C semantics for the command's group.** The real native supervisors SIGKILL the CLI as soon as they receive SIGINT. **Actual SIGINT interoperation with the bench is untested**, because the helper and bench were not launched.

   These statements come from reading the frozen source, `phase7_bench.py` `bf630d9a…`:
   - `functional_preflight` (`:621-628`) adds `admission` and writes `report.json` only after `_functional_preflight` returns.
   - `_functional_preflight` itself writes `report.json` incrementally after each case (`:1678`) and again in its `finally` (`:1685-1693`, write at `:1693`).
   - That `finally` also runs on `KeyboardInterrupt`, which its `except Exception` (`:1683`) does not catch.
   - When `source_after` is absent, that `finally` calls `final_identities` before writing (`:1691-1692`). The call may take time, or may itself be cut short by a second signal or by the forced kill.

   **So a partial bench `report.json`, without `admission`, may exist after a graceful SIGINT or an interrupted run; or none may exist.** Such a report is always failed and unadmitted. Admission requires an actual success, a valid report, verified identities, driver exit 0 and a driver report saying `ok`.

   Anything before the driver's exec is charged only when `--anchor-boottime` is given.
6. **Subreaper side effects.** Orphans see the driver as their parent, and the driver reaps them instead of systemd. The driver sleeps until the next deadline and wakes only on its own children's SIGCHLD or on a signal. That is negligible but non-zero work alongside timed runs, and it is a disclosed environment difference.
7. **The lock check is passive and flock-only.** `/proc/locks` is matched on major:minor:inode. A lock held by something foreign after the run is indistinguishable from a leak, and is treated as 70.
8. **Disk.**
   - The size of `child.stdout` and `child.stderr` is not bounded.
   - An EDQUOT on event or report writes is recorded and gives 70.
   - No symlink workaround is used.
9. **Environment.** The environment is passed unchanged. Values outside the recorded keys appear only in the hash.

## 7. Root usage (after independent review only)

Example: wrapping the plan's A1 preflight. The environment line comes from `phase7-measurement.md:12`; the bench argv is exact and unchanged.

```bash
/usr/bin/python3 -I docs/evidence/phase7/bounded-execution.py run \
  --out /home/ajs/.cache/pb-phase7/<run-id>-bounded \
  --lock-path /home/ajs/.cache/pb-rs-performance.lock --require-lock-free \
  --hash harness/phase7_bench.py --hash harness/phase7.py --hash harness/phase7_spawn.c \
  -- /home/ajs/.cache/pb-phase7/harness-venv/bin/python harness/phase7_bench.py preflight ... --budget-seconds 1620 ...
```

- Capture `time.clock_gettime(time.CLOCK_BOOTTIME)` when the approval or registry is written, and pass it as `--anchor-boottime`. It must be at most `launch_by` older than the driver start.
- Add `--hash` entries for the reference and candidate binaries and for the prepared `phase7_spawn` helper binary, so the end identity covers what actually ran. A skipped post hash now fails closed.
- Before anything reuses the lock, check in this order:
  1. the **process exit code**, which is authoritative;
  2. the stdout summary (`post_decision_issues`, `completion_elapsed`);
  3. `report.json` (`verdict`, `process_cleanup_state`, the hashes, `lock_post`).
- Admission requires exit 0, a report saying `ok`, and every hash and cleanup check passing. Anything else is retained as failed, and the plan's stop rules apply.
