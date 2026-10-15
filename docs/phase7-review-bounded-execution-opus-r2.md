# Phase 7 review — bounded execution driver r2 (Opus, independent, fresh round)

## Verdict

**The code is accepted for the bounded-execution scope. The doc needs one required doc-only fix (D1) before root uses it.**

- **B1 is closed** (executed). A skipped end hash can no longer produce `ok`.
- **B2 is closed** (executed). Every failure or lateness after the decision makes the exit code 70 or 72. The report does not claim its own write completion.
- **B3 is mostly closed.** The conditional bound and the waits on CLOCK_MONOTONIC are now disclosed honestly.
- **N1, N2 and N5 are closed** (N1 and N5 executed independently; N2 by the author's test, which I re-ran, and by source).
- **N3, N4 and N6 are disclosed correctly.**

**D1 (required, doc only).** §1 lists "not SIGKILLed" as the only kill condition, and §3 says a terminal Ctrl-C reaches only the driver. But **any signal the driver does not handle and whose default action is fatal** has the same effect as SIGKILL:

- Ctrl-\ (SIGQUIT);
- SIGUSR1 and SIGUSR2;
- others with a fatal default (SIGPWR, SIGVTALRM, SIGPROF, SIGIO, SIGXCPU, SIGSYS, …).

An external SIGALRM fires the backstop early and exits 71. I executed this with SIGUSR1 (§3, X1): the driver died by the signal, `report.json` stayed `running`, the top went to the next subreaper as a zombie, and a live detached setsid grandchild escaped to the next subreaper.

The outcome fails closed: the exit status is a signal, so the run is never admitted. Only the wording is incomplete. **Fix:** in §1 (the conditions and the outcome list), §3 (the terminal paragraph) and §6.1, say "not killed by SIGKILL **or by any unhandled fatal signal**, for example Ctrl-\ / SIGQUIT or SIGUSR1/2; an external SIGALRM gives 71". No code change is required, and the code needs no further review; root can check D1 against the text alone. Adding SIGQUIT to the handled set would be an optional alternative, but I do not require it.

**What this verdict does not cover.** It is prerequisite acceptance only. It admits no performance, protocol, matrix or admission result, and it is not a run of the real bench, helper, CLI or fixture. The actual SIGINT interoperation with the bench remains **UNRUN**.

## 1. Inputs and hashes (verified at start and again after all runs)

| File | SHA-256 |
| --- | --- |
| `docs/evidence/phase7/bounded-execution.py` | `fc814f72a976152623093c056ca80b04b50da042585d750d7dbab364fbbbc0dc` |
| `docs/evidence/phase7/bounded-execution-test.py` | `8ba5d27886711c34b4681719ce662bf207b99c18199b66963f4e5da1cc60bef2` |
| `docs/phase7-bounded-execution.md` | `e9d7d9aa4e784a32f14abd6659afd8f864b40d6cff5db6c91f9aca0511b4065e` |
| `harness/phase7_bench.py` (frozen) | `bf630d9ac4dd4e615f221e218cfce7cbb99438c2de1bf5cfdc388941eef013c2` |
| `harness/phase7.py` (frozen) | `dc7574f058b9ff6ae7b77a912847fdbe02d8b68baab8728237d6ef040e6a44fa` |
| `harness/phase7_spawn.c` (frozen) | `0279d5fb06e4f38adc32004c152fc42c94194d59f234ee5a9c5694f74a6d31f4` |

- **Hash records:** the start hashes are in `hashes-start.txt`. `sha256sum -c` passed after all runs.
- **What I read in full:**
  - the prior review r1;
  - the r2 doc;
  - `bounded-execution-r2/{r2-driver.diff, r2-test.diff (r2 tests read in the source), freeze.txt, final-run.log, final-run-source-hashes.txt}`;
  - the tail of `causal-r1/run.log`.
- **Driver source:** I read all of the changed code, plus the unchanged code it depends on: `launch`, `release_child`, `supervise`, `cleanup`, `verify_empty`, `abort_unlaunched`, `end_checks`, `finish`, `reassess`, `main` and `__main__`.
- **Bench source** (source only): `phase7_bench.py:621-628` and `:1670-1694`.

## 2. Correction of the prior r1 source assertion (source only)

R1 §6 said that `functional_preflight` writes its report only on success, so a graceful SIGINT leaves only the driver's evidence. **That is wrong.**

- `functional_preflight` (`:621-628`) only adds `admission` and rewrites `report.json` after a successful `_functional_preflight`.
- `_functional_preflight` writes `report.json` incrementally after each case (`:1678`). Its `finally` (`:1685-1693`) writes it again, and that `finally` also runs on `KeyboardInterrupt`, which `except Exception` does not catch.
- Before that write, if `source_after` is absent, it calls `final_identities` (`:1691-1692`).
- **Result:** after an interrupted run, a **partial bench report without `admission`** may exist, or no report at all (for example after a forced kill during `final_identities`). Either way the run is **always unadmitted**.

r2 doc §6.5 already states this correctly.

## 3. Tests and probes actually run

**Cache:** `/home/ajs/.cache/pb-phase7/review-bounded-execution-opus-r2/`, with `TMPDIR=/home/ajs/.cache/pb-phase7/tmp`.

- No product, Go, fixture, helper, build, compiler, profiler, common lock or campaign was run.
- No host suspend or stop was used.
- There was no EDQUOT.
- Every shell command was under `timeout -s KILL` (85 s or 60 s).
- The probe processes made themselves child subreapers. Every orphan was therefore their own child, identified through `/proc`, SIGKILLed through a pidfd and reaped, and ECHILD was asserted at the end.

### Runs

| Run | Command / file | Result | Wall |
| --- | --- | --- | --- |
| P | `probes/probes.py`, SHA-256 in `probes/probes.sha256` | `probes/run1.json`, rc 0 | 1.09 s |
| P2 | `probes/p2.py` (re-run of one probe bug, below) | `probes/run2.json`, rc 0 | not timed; bounded by `timeout 60`; each driver run < 0.1 s |
| T | the author's tests filtered to `-k test_r2_` (the 9 r2 tests only; I did not run the whole suite of 32) | `tests/r2-only.log`: `Ran 9 tests in 0.465s OK` | 0.53 s |

**Retained probe failure (my bug, not a driver bug).** In run1, `N1_anchor_refusals.accepted_0.5s_old` returned 125 with `cannot create new --out …: No such file or directory`. My probe created that `--out` parent only later. The 125 is correct driver behaviour. I re-ran it correctly in P2. No process leaked from this.

### Executed results (in-process probes use a fake clock and the real `Driver` methods)

| ID | Control | Observed |
| --- | --- | --- |
| B1-a | Post hash taken at exactly the cutoff `(reap_by+finalize_by)/2` | **70**; on disk `final`/`cleanup_unverified`; post entry is `skipped` |
| B1-b | Two hashes; the first pushes the clock past the cutoff (still below `finalize_by`) | **70**. The first hash is verified, the second is skipped, and a skip issue is recorded |
| B1-c | The hashed file is replaced by a FIFO after the pre hash | **73** `identity_drift`, with the error `EINVAL not a regular file`. It took 0.0 s (no hang) |
| B1-d | Real driver runs: `--hash` of a stable file / the command modifies the hashed file | rc **0**, `ok`, ECHILD, matching post hashes / rc **73** |
| B2-a | Provisional report written, then the out dir set read-only for the final write | **70**. The summary has `report_verdict ok` and `post_decision_issues` = `[report write failed EACCES]`. The disk still holds the provisional `running` report |
| B2-b | The final write completes at exactly `finalize_by` (the boundary) | **72**. Disk report is `ok`/0. Timing keys are only `decision_*` plus `scope`/`limit`/`finalize_by`, so there is no completion claim. The last event is `exit 72` |
| B2-c | Events fsync on `/dev/null` (EINVAL) | **70**; disk report says `ok` |
| B2-d | Summary print to a closed stream | **70**; issue `summary print failed` |
| B2-e | The clock crosses `finalize_by` only while the `exit` event is written | **72**. The `exit` event itself says `0` (see N-b) |
| PREC-1 | Report verdict `interrupted`, then the final write fails | **70** (`cleanup_unverified` takes precedence) |
| PREC-2 | Report verdict `identity_drift`, then the write completes late | **72** (`late` takes precedence over drift) |
| N5 | SIGHUP pending before `finish` | **130**; `interrupts[0].phase == end_checks`; disk report says `interrupted` |
| PD | SIGINT arriving during the final write (after the decision) | **0**. It is recorded only as an event and an `interrupts` entry with phase `post_decision`. This matches doc §3 |
| N1 | Real driver with `--anchor-boottime` = `nan`, `inf`, `-inf`, now−100, now+50 | each **125** with an exact stderr reason; `--out` not created; ≤ 0.08 s |
| N1' (P2) | Anchor 0.5 s / 1.5 s / 2.5 s old, with `launch_by` 2 | **0**/ok/ECHILD, **0**/ok/ECHILD, **125** "older than launch_by" |
| N2 | The author's test `test_r2_n2_…`, re-run by me (inside T) | OK. The source asserts that `release_child` returns in < 0.8 s, `supervise` sends the group SIGINT `external SIGINT` in < 0.8 s, the control ends by SIGINT, and ECHILD holds. The author's causal run against r1 failed (the control was leaked to the tearDown subreaper and then reaped) |
| X1 (D1) | Real driver; the control double-forks a detached setsid grandchild (self-bounded to 20 s), then the driver gets **SIGUSR1** | driver rc **−10**; `report.json` `status: running`. The top is a zombie and the live grandchild is reparented to **my probe** (the next subreaper). Both were then SIGKILLed, reaped and ECHILD confirmed (`probe_cleanup` in run1.json) |
| X2 | Real driver started with **fd 1 closed** | `report.json` `ok`/0, but process rc **1**: `AttributeError: 'NoneType' … flush` at `__main__` `sys.stdout.flush()` (see N-a) |
| X2' | In-process `finish` with `sys.stdout = None` | returns **0** with no issue, because `print` is a no-op (see N-a) |

### Source-only checks (not executed)

- **SIGQUIT (Ctrl-\):** it has no handler (`install_signals` handles only INT/TERM/HUP/CHLD, with ALRM from the backstop), so it behaves like X1 and also dumps core. Not sent, to avoid a host core dump.
- **Ctrl-Z / SIGTTOU:** these stop the driver, as disclosed.
- **Host suspend:** disclosed, not tested.

## 4. Closure analysis

**B1.**

- `end_checks` appends `post hash skipped (finalize budget): <path>` for every skip. Issues give `cleanup_unverified` at the decision, so 70.
- A pre hash that fails before launch is a precondition failure (`main`'s `except Exception`).
- A post hash that errors or differs gives 73.
- There is no path where `--hash` was given, the command launched, and `post` holds fewer verified entries than `pre` with the verdict still `ok`. The only way to end the loop early is an exception, and that is a Python crash, exit 1.

**B2.**

- **Order of steps:** decision → `report.update` → `before = len(issues)` → `event('final')` → `write_report(durable=True)` (temp file write+flush+fsync, `replace`, directory fsync; any `OSError` becomes an issue) → events fsync (an error becomes an issue) → `reassess` → summary print (`OSError`/`ValueError` becomes an issue) → `take_interrupts('post_decision')` → `reassess` → `exit` event → `reassess` → return.
- **`reassess` is monotone:** it only adds `cleanup_unverified` or `late`, then applies PRECEDENCE. So the result can only get worse, and 70 takes precedence over 72.
- **Exit 0 implies all of these:**
  - the final report is durably on disk and says `ok`/0;
  - the events were fsynced;
  - the summary print raised nothing;
  - every step up to and including the `exit` event finished before `finalize_by`.
- **The reverse can fail:** a report saying `ok` can come with an exit code other than 0. The doc states that the exit code is authoritative.
- **No self-stamping:** the report carries no completion field. `completion_elapsed` is only in the stdout summary.

**N1.** The finite, future and too-old checks all run before `arm_alarm_backstop` and before `--out` is created. `arm_alarm_backstop` also refuses a non-finite delay (defence in depth; unreachable once the anchor is validated).

**N2.** On a signal wake with `pending`, `release_child` returns `'running'`, and `supervise` applies the policy at its first iteration.

- The go/pending race is closed: the wakeup fd persists across `poll`, and the Python handler runs before `pending` is checked.
- A later exec failure surfaces only as the command's exit status (SIGPIPE or 127). That is disclosed, and the verdict precedence keeps `interrupted` (130) or worse.

**N5.** `finish` calls `take_interrupts('end_checks')` before the decision. After the decision, signals become events only. This is accepted: the command has already been reaped and verified.

## 5. Non-blocking findings (disclose or fix cheaply; not required)

**N-a — stdout absent at start.**

- If the driver starts with fd 1 closed, `sys.stdout` is `None`, so `print` is a no-op and no issue is recorded (`finish` returns 0).
- The real process then exits **1**, through an `AttributeError` at `__main__`'s `sys.stdout.flush()`, after writing a report that says `ok`.
- This fails closed, but by accident. Doc §4's "summary-print failure → 70" does not describe it.
- **Cheap fix:** treat `sys.stdout is None` as a print failure, and guard the two `flush` calls. Alternatively, disclose it.

**N-b — the `exit` event can understate the code.** If completion crosses `finalize_by` while the `exit` event is being written, the event says 0 and the process exits 72 (B2-e). The code comment admits this, but doc §4 says the event carries "the actual exit code". Suggested wording: "the exit code as assessed just before the event; the process exit code is authoritative".

**N-c — the step after the last `reassess`.** `cancel_dump_traceback_later`, `watchdog.close`, two flushes and `os._exit` are not checked against `finalize_by`.

- The SIGALRM backstop is still armed, so under the §1 conditions the process ends before `hard_exit_at` (< `limit`) or exits 71.
- Exit 0 therefore means that the write, fsync and print finished before `finalize_by`, **not** that the process terminated before `finalize_by`.
- The doc's table is consistent with this; noted for precision.

## 6. Limitations accepted as disclosed

Once D1 is added, these are disclosed in the r2 doc and acceptable:

- the bound is conditional: running scheduler, no D-state, no suspend, driver not stopped, driver not killed;
- waits run on CLOCK_MONOTONIC;
- a SIGKILL or unhandled fatal signal lets the setsid fixture and other orphans escape (X1 shows exactly this);
- reach exclusions: a nested subreaper, IPC-started services, ptrace;
- the descendant record is not a census;
- convergence is bounded, not guaranteed;
- N3: a backstop can supersede a final report;
- N4: the precondition path is guarded by the alarm only;
- N6: `watchdog.txt` is always created;
- the lock check is passive;
- `child.*` sizes are not bounded;
- the environment is passed through unchanged;
- the actual bench SIGINT interop is **UNRUN**.

## 7. Required vs optional (summary)

| ID | Class | Action |
| --- | --- | --- |
| D1 | **required, doc only** | Name the unhandled fatal signals (Ctrl-\/SIGQUIT, SIGUSR1/2, …) alongside SIGKILL in §1, §3 and §6.1, and say that an external SIGALRM gives 71 |
| N-a | optional | Make a `None` stdout a print failure (70), or disclose that the exit is 1 |
| N-b | optional, doc | Qualify the "actual exit code" wording for the `exit` event |
| N-c | note | none |

## 8. Cleanup and zero active processes

- `probe_cleanup` in `run1.json` lists two adopted processes:
  - pid 2422235, start_ticks 107787546, a zombie;
  - pid 2422270, start_ticks 107787547, the sleeping grandchild `control.py`.
- Both were SIGKILLed through a pidfd and reaped (status 9). ECHILD was true afterwards. P2 also ended with ECHILD true. The test filter T passed its subreaper tearDown leak checks.
- `zero-active.txt`:
  - both pids are gone;
  - a `ps` scan for the cache path, `bounded-execution(-test).py`, `control.py` and `usr1-mark` matched only the scan's own `tee`.
- I removed no artifacts. All runs, the retained failed probe output, and the r1 and author caches are preserved.
- The only new files are this document and the review cache.
