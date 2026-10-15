# Phase 7 review — bounded execution driver r1 (Opus, independent, fresh round)

## Verdict

**NOT ACCEPTED as-is for root's first combined-source preflight** (primary, dark, 24 roles). The driver needs **two small code fixes (B1, B2) and one doc fix (B3)** first. No redesign is needed.

The process-ownership core passed review with no defect found: pidfd, held WNOWAIT, subreaper, partial-acquisition and abort paths, reap races, and the ECHILD end proof (§3). Once B1–B3 are fixed, a narrow delta re-review of only those lines is enough; root decides.

This review admits no performance, protocol, matrix or admission result. It relaxes no gate. It is not a run of the real helper, CLI, fixture or bench.

## 1. Inputs (hashes verified at review time)

| File | SHA-256 |
| --- | --- |
| `docs/evidence/phase7/bounded-execution.py` | `70474bf32f9b1ad80f434bddeffa933745eb3d0162c3ffd61d5f9fdc927062f6` |
| `docs/evidence/phase7/bounded-execution-test.py` | `cbb18d499f40d0fe33d86df284209d8ea550678c031c5a6b255b76c5fbabc664` |
| `docs/phase7-bounded-execution.md` (read in full) | `82cde174e54d4d3130b23a8b122ad0508cb6164737e623bbd09ab6eb29db7052` |
| `docs/phase7-under30-execution-plan.md` (read in full) | `295392ac18f8294209274ab364c026c8fe3e41000a22964b599729c47dbb458a` |
| `harness/phase7_bench.py` (frozen, lifecycle paths read) | `bf630d9ac4dd4e615f221e218cfce7cbb99438c2de1bf5cfdc388941eef013c2` |
| `harness/phase7.py` (frozen, `Fixture`, `NativeChild`, SIGCHLD check) | `dc7574f058b9ff6ae7b77a912847fdbe02d8b68baab8728237d6ef040e6a44fa` |
| `harness/phase7_spawn.c` (frozen, `stop`, `main` supervisor path) | `0279d5fb06e4f38adc32004c152fc42c94194d59f234ee5a9c5694f74a6d31f4` |

- Host: kernel `7.2.5-3-omarchy`, Python 3.14.7.
- The bench lifecycle paths I read:
  - `phase7_bench.py:602-628`: `FunctionalOwnership` and `functional_preflight`;
  - `:640-700`: `QuietWindow`;
  - `phase7.py:107-125`: `Fixture`;
  - `:413`: SIGCHLD == SIG_DFL;
  - `:483-681`: `NativeChild`, including `close`;
  - `phase3.py:1061`: `Fixture.close`;
  - `phase7_spawn.c:23-30` and `:146-235`.

## 2. Tests actually run (total test-command wall ≈ 23.5 s, all < 90 s)

**Cache:** `/home/ajs/.cache/pb-phase7/review-bounded-execution-opus-r1/`, with `TMPDIR=<cache>/tmp`.

- I ran no product, Go, fixture, native helper, compiler, build, profiler, common lock or campaign.
- There was no EDQUOT.

**1. Author suite, run once:**

```
PB_BOUNDED_TEST_DIR=<cache>/tests nice -n 10 python3 -I docs/evidence/phase7/bounded-execution-test.py -v
```

- Result: `Ran 23 tests in 21.192s OK`, real 21.26 s, user 1.77 s.
- Log: `tests/run.log`. Results: `tests/results-1792065439-2441052.json`. Hashes: `tests/hashes.txt`.

**2. My probes:** `probes/probes.py`, SHA-256 `bb7749393e974088edeef4a77c950e3db95b508941cdf7ff9fc0992e9416407b`.

- What they are:
  - pure in-process calls into the driver module with a fake clock;
  - one forked, sleeping session-leader control;
  - three driver invocations that never launch a command.
- Run: `probes/results.json`, real 1.71 s. A follow-up of two more non-launching anchor invocations is in `probes/p4b.txt`, about 0.06 s each.
- **Retained failure — my first probe attempt (0.42 s):**
  - It failed with `KeyError: 'capabilities'` because my probe did not initialise `report['capabilities']`. The stderr is retained in `probes/run1-stderr-failed.txt`.
  - That failed attempt **leaked one of my own control processes**:
    - pid 2358581, start_ticks 107675509, sid = pid;
    - it was reparented to pid 1222 and blocked in `anon_pipe_read`, because my probe's forked child kept its own copy of `go_w`;
    - the driver's real child closes that copy through `closerange`, so this was a probe bug, not a driver bug.
  - I confirmed its identity (argv and sid) through a pidfd, SIGKILLed it, and confirmed it was gone after about 1 min 50 s asleep; it used no CPU. Record: `probes/run1-leak-cleanup.txt`.
  - A final `pgrep` over the cache path was empty.

## 3. Process ownership: checked, no defect found

- **Group signal identity.**
  - `PIDFD_SIGNAL_PROCESS_GROUP` signals the process group *whose ID is the pidfd's struct pid*.
  - The command is a verified session and group leader (`bounded-execution.py:454-456`). A session leader cannot change its pgid, and only processes in its own session can join its group. The scope therefore stays exactly the reserved group.
  - The struct pid is pinned by the held pidfd and by the WNOWAIT-unreaped leader. A recycled numeric PID can never be addressed.
- **Single reaper.**
  - SIGCHLD has a Python handler, so the kernel never auto-reaps (`:276`).
  - The reaps are:
    - `waitid(P_PID, own child)` (`:557`);
    - `wait4(self.child)` (`:641`);
    - `waitpid(self.child)` (`:679`).
  - There is no `P_ALL` reap without `WNOWAIT`. A pidfd opened on a listed own child is therefore reuse-safe.
- **Coverage.**
  - Every thread's `children` list is read. Orphans can be adopted by any thread, and the faulthandler thread exists after launch.
  - The final `waitid(P_ALL, WNOHANG|WNOWAIT)` → ECHILD (`:656`) is authoritative. Even if the `/proc` children list were momentarily incomplete, a missed child would turn into `cleanup_unverified` (70), never into `ok`.
- **Partial acquisition and abort paths:**
  - fork fails → `abort_unlaunched` with no child;
  - `pidfd_open` fails after fork → the setup child is SIGKILLed through `os.kill` on an own, unreaped, never-exec'd child, which is safe (`:675`);
  - the child dies before ready → POLLHUP on `ready_r` → precondition failure;
  - the driver dies before go → the child's `go_r` read reaches EOF (`go_w` is closed by `closerange`) → `_exit(126)`, plus PDEATHSIG and the `getppid` check;
  - writing go fails after `go_sent` → full cleanup (`:463`, `:851-853`).
- **Reparent-during-reap race.**
  - Children are reparented before the dying parent becomes waitable.
  - The re-list after reaping (`:631`) closes the window.
  - A process that is still exiting stays listed as not-Z and keeps the loop open.
- **Leak attribution.**
  - When the top exits on its own, every live remnant has an own-child ancestor, because the top's children were already adopted.
  - So any remnant produces an `alive_after_command_exit` own child → `leaked`.
  - Group members killed by the group SIGKILL under a live own child are not listed one by one; limitation 3 already discloses this.
- **Topology fit with the frozen sources:**
  - the supervisor's `getppid() == expected_parent` checks refer to the bench, and are unaffected by the subreaper;
  - the CLI keeps `PDEATHSIG=SIGKILL`; a zombie reparented to the driver is reaped by the driver instead of systemd;
  - `phase7.py:413` (SIGCHLD SIG_DFL) holds because the child signals are reset (`:389-391`);
  - no bench, phase7, phase3, run or spawn file uses `/dev/tty`, `getsid`, `getpgrp`, `isatty` or a nested subreaper (grep), so the new session and the closed fds ≥ 3 do not change any checked assumption.

## 4. Blockers

### B1 — a skipped end hash still yields `ok` (root check 1, CONFIRMED)

**Where:** `end_checks`, `bounded-execution.py:700-704`.

- At or after the cutoff `(reap_by+finalize_by)/2`, each post hash is recorded as `{'skipped': 'finalize budget'}` and the loop continues. No reason or issue is added.
- A hash that **errors** fails closed (`None != sha` → `identity_drift`). A **skipped** hash does not.

**Probe P1:**

- Fake clock at 5.01, cutoff 5.0, `finalize_by` 5.5.
- Result: `exit_code 0`, `verdict "ok"`, `reasons []`, `post: [{"skipped": "finalize budget"}]`.

**Consequence:** the end identity of the hashed bench, phase7 and spawn sources (and of argv[0]) can be unverified while the verdict is `ok`. That violates the fail-closed end-hash evidence root requires.

**Minimal fix:** on a skip, append an issue such as `'post hash skipped: <path>'`, which gives 70. Alternatively append `identity_drift`. Either way a non-verified end identity can never be `ok`.

**Related:** `sha256_file` has no deadline inside a single file. A large or stalled file can therefore overrun `finalize_by` (→ `late`) or reach the backstop (→ 71). Both are fail-closed; this needs only a mention in the doc.

### B2 — the final verdict and exit code are fixed before the final write; write failure and lateness are not reassessed (root check 2, CONFIRMED)

**Where:** `finish`, `:713-747`.

- `timing` is computed at `:723`.
- The verdict and `exit_code` are fixed at `:729-731`.
- `cleanup_summary()` copies `issues` at `:732` and `:756`.
- After that come `event('final')` (`:733`), `write_report` (`:734`), `fsync` (`:740`, where errors are ignored) and `print`.
- A write failure appends to `issues` **after** the verdict. The return code stays the old one.

**Probe P2 (out directory made read-only for the final write):**

- `exit_code 0`, `verdict "ok"`.
- `issues: ["report write failed: [Errno 13] …"]`, but `summary_issues: []`.
- `report_exists: false`.
- In a real run, the provisional `running` report from earlier would be left in place, alongside exit 0.
- This contradicts the driver doc's §4 table: "report or event write failure → 70".

**Probe P3 (the final write takes driver time 5.0 → 7.0, with `finalize_by` 5.5):**

- `exit_code 0`.
- The written report says `final_report_elapsed 5.0` and `within_finalize_by true`.
- The final write's actual completion time exists only as `report_written_elapsed` in the stdout summary. It is never compared with any deadline.

**Minimal fix:**

1. Record the number of issues before the final event, write and fsync.
2. Afterwards:
   - if issues grew (write, event or fsync failure; also count fsync errors), return **70**;
   - else if `clock.now() >= finalize_by`, return **72**.
3. Print the stdout summary with that final code and the post-write elapsed time.

**Doc fix:**

- The process exit code is authoritative.
- `report.json` cannot record its own write completion.
- A report saying `ok` with exit code 70/72/71/1 means unverified.

This keeps the design; it moves only the decision point.

### B3 — honest-guarantee wording for the strict bound (root check 3, doc only)

The strict-bound claim in doc §1 ("whole driver lifetime ends strictly before `--limit`"; "CLOCK_BOOTTIME … so it is conservative") is qualified only later, in §6.4. It omits two cases.

**Suspend.**

- The deadline *checks* use CLOCK_BOOTTIME, but every *wait* runs on CLOCK_MONOTONIC, which stops during suspend:
  - the `poll` timeouts (`:311`);
  - `setitimer(ITIMER_REAL)` (`:203`);
  - faulthandler's timed lock wait (`:208`).
- After a suspend inside a wait, the driver and both backstops wake late by the length of the suspend.
- The verdict then fails closed (`late`), but the bound itself is exceeded.
- This conclusion comes from reading the source and the kernel's clock semantics. I did not suspend the host to test it.

**SIGSTOP/SIGTSTP of the driver.** A stopped driver stops every backstop, including the watchdog thread.

**Required wording:** §1 must state the bound together with its preconditions:

- a running scheduler;
- no D-state;
- no suspend;
- the driver is not stopped;
- the driver is not SIGKILLed.

Outside these, the outcome is `late`, 71 or 1, or no report at all, and the run is retained as failed. Remove the unqualified "conservative" wording for waits.

Root already listed D-state and scheduling as known limits. Suspend and stop are not disclosed anywhere, which is why this is a blocker for the doc rather than for the code.

## 5. Non-blocking findings (fix if cheap; otherwise disclose)

**N1 — the explicit anchor is not validated (root check 3, CONFIRMED, fails closed; no launch observed).** At `:807-811` the anchor has no `isfinite` check and no plausibility check.

| Anchor | Result | Report |
| --- | --- | --- |
| `nan` | exit **71** in 0.064 s | left provisional (`running`) |
| `=-inf` | exit **71** | report says `final`/`late` (precondition launch_by), so the report and the exit code disagree |
| `=-1e9` | exit **71** | same as `-inf` |
| `inf` | **125** | — |
| `-inf` without `=` | 125 (argparse) | — |

- How the NaN case happens: `min(nan, start)` returns `nan`, and the 1 ms alarm fires (`max(1e-3, nan)` → `1e-3`).
- No command was launched in any case. The outcome is misclassified as a backstop rather than 125.
- **Fix:** before arming the alarm, return 125 if the anchor is non-finite or older than `driver_start − launch_by`.

**N2 — the exec-confirmation wait does not act on interrupts (root check 4, CONFIRMED, delay only).**

- At `:466-480`, a signal wake leads to `continue`, and `pending` is not consulted until exec is confirmed or `graceful_at` is reached.
- **Probe P5:**
  - control: an own session leader whose "exec" takes 1.0 s;
  - a SIGINT sent at 0.2 s was still pending when `release_child` returned at 1.001 s;
  - no interrupts had been taken.
- Normally the interrupt is acted on at the first `supervise` iteration, so it is delayed but not lost.
- With a hung `execve` there is no response until `graceful_at` (1710 s). Escalation then goes through `exec_unconfirmed` → 70, so the run stays bounded and fails closed. Doc §3's "first interrupt → group SIGINT" is not true during this window.
- **Fix:** on a wake with `pending`, return `'running'` and let `supervise` apply the policy. Group signalling is safe both before and after exec, because the group is reserved. Alternatively, disclose the window.

**N3 — the report's exit code can be superseded.** Probe P4b shows it: if the alarm fires after the final write, the process exits 71 while the report says something else. This is conservative; the B2 doc wording (exit code is authoritative) covers it.

**N4 — the precondition and abort path have no thread watchdog** (it is armed only after a successful launch, `:859-860`). Only SIGALRM bounds the rest of that path. This is minor.

**N5 — signals after cleanup are lost.** Signals that arrive during `end_checks` or `finish` stay in `pending` and are never recorded in `interrupts`.

**N6 — doc nit:** `watchdog.txt` is always created, and empty unless the watchdog fires (`:859`). §4 says it is "written only if the thread backstop fires".

## 6. Explicit limits accepted as disclosed (not blockers)

- **Driver killed by SIGKILL or a hard self-exit (71/1):**
  - the command dies through PDEATHSIG;
  - the CLI dies through its own PDEATHSIG;
  - the setsid fixture and any adopted PDEATHSIG-less orphan escape **unreaped** to the next subreaper.
- **Inherited signal reset** in the child. This is the intended behaviour and was verified by the author tests.
- **Reach exclusions:** a nested subreaper, IPC-started services and ptrace. ECHILD proves only what is within reach.
- **The lock check is passive** and never acquires the lock. `FunctionalOwnership`'s `LOCK_NB` (`phase7_bench.py:607-613`) remains authoritative.
- **The bench's actual SIGINT interop is unverified.** For this preflight in particular: `functional_preflight` writes its report only on success, inside `FunctionalOwnership` (`:624-628`). A graceful-SIGINT preflight therefore leaves only the driver's evidence, and it must be retained as failed.
- **D-state and scheduling** have no unconditional guarantee. Suspend and stop are to be added under B3.

## 7. Notes for root's planned preflight (not findings)

- Pass `--anchor-boottime` captured at the approval/registry write.
- Add `--hash` entries for:
  - the reference and candidate binaries;
  - the prepared `phase7_spawn` helper binary;
  - the three sources.

  This makes the driver's end identity cover what the run actually executed. After B1, a skipped hash fails closed.
- `--budget-seconds 1620` (FunctionalGuard) sits inside the 1710 s graceful point. A1's lower bound is about 76 s.
- Read the process exit code first (B2), then `report.json`, then whether the lock is free, before anything reuses the lock.

## 8. Objection summary (reproducible)

| ID | Class | Source location (SHA `70474bf3…`) | Reproduction |
| --- | --- | --- | --- |
| B1 | blocker | `:700-704` | `probes.py` P1 |
| B2 | blocker | `:713-747` | P2, P3 |
| B3 | doc blocker | doc §1 lines 23-37; code `:203`, `:208`, `:311` | source reading |
| N1 | non-blocking | `:807-813` | P4, `p4b.txt` |
| N2 | non-blocking | `:466-480` | P5 |
| N3–N6 | non-blocking | as cited | P4b; source reading |

The process-ownership core (§3) is accepted with no objection.
