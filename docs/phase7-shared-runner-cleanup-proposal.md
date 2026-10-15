# Shared runner ownership cleanup proposal, R1

This is an **off-tree proposal, not an applied fix or an accepted regression result**. Live `harness/run.py` and all live tests remain unchanged while root's phases 1–5 run. The candidate has a **portability blocker**: it fails explicitly before acquiring a measurement child on macOS Python 3.11/3.12. Do not promote this candidate as preserving the current Python 3.11+, Linux/macOS contract without resolving that blocker. Root owns independent review, fresh Opus review, any edit window, and the subsequent whole-suite rerun.

Artifacts are in `/home/ajs/.cache/pb-phase7/shared-runner-owned-cleanup-r1/`:

- `before/harness/`: retained source inputs; `before-source-hashes.json` records full identities.
- `proposed/harness/run.py`: the sole proposed existing-source change.
- `proposal.patch`: LF unified diff with `a/harness/run.py` / `b/harness/run.py` labels. `git apply --check` against the retained before tree passed; no application was performed.
- `test_owned_cleanup.py`, `run_validation.py`: explicit, guarded, off-tree tests. They are not additions to live harness discovery or a proposal to change its inventory.
- `logs/`, `validation-results.json`, `source-integrity.json`, `expected-output-identities.json`: failures, final results, unchanged-source checks, and synthetic byte/resource expectations.

Identities:

| Artifact | SHA-256 |
| --- | --- |
| Live and retained before `harness/run.py` | `b280d1295a4b351e6f2d838c56acaac58b2247bb77fc9881587bb7bbe849a076` |
| Proposed `harness/run.py` | `462fe9067dfeec6cc7a5b0a108c80fb4fafdb4bc270f454b653ec18eb0545392` |
| LF `proposal.patch` | `5c86fdf3270e263fc177b11c44aa8d90c4b3e62dd343f3dbad4f6b95667d7aad` |

The brief's hash appears truncated; the complete live hash above was captured before work and verified afterwards. Existing unrelated worktree changes were left alone.

## Exact change and ownership argument

The original `measure()` calls `wait4(pid, WNOHANG)` before further group accesses, including its unconditional final `kill_group()`. Reaping releases the leader's identity; a subsequent numeric `killpg(pid, ...)` can address a recycled group. Normal EOF does not eliminate that risk. A descendant retaining a pipe also takes timeout/signal paths after the early reap.

The candidate changes only existing definitions `spawn_measured()` and `measure()`, adds four private ownership helpers and the needed contextlib import, and keeps shared `kill_group()` unchanged:

1. `measure()` observes its exact direct child with `waitid(P_PID, pid, WEXITED | WNOHANG | WNOWAIT)`. It saves the first observed exit time without reaping or setting `returncode`.
2. Every measurement group signal goes through `_signal_measured()`. It rejects nondefault SIGCHLD, checks that `returncode` is unset, and uses exact-child WNOWAIT observation before the numeric group operation. ECHILD raises before signaling. In this deliberately single-threaded runner, with no competing reaper, the live or zombie session leader reserves PID == PGID through every group operation.
3. Final cleanup signals that still-owned group **before** the sole final `wait4(pid, 0)`, then records its status and rusage. There is no group access after that reap. Interrupted waits retry without another signal. A kill directed at an already exited leader does not replace its saved exit status.
4. Ordinary SIGINT is masked across spawn/handle assignment and final signal/reap/status/pipe cleanup. The spawned supervisor still receives `setsigmask=()`. Pending KeyboardInterrupt is delivered after the ownership handoff or after final cleanup, where the enclosing unwind handles it.
5. Selector creation, incremental spool acquisition and pipe registration move inside the existing cleanup region. `spawn_measured()` unwinds a failed second pipe, spawn, cwd restoration or stream conversion; a returned PID is killed before reaping. Partial spool/selector acquisitions and both pipes close on capture exceptions, including KeyboardInterrupt and EOFError. Spools also close if materialization fails.

This retains the original `now` observation convention for fallback wall timing. Later final reaping is not substituted for an exit time already observed. The complete result construction, fallback labels, native JSON loading/override, native wall computation and resource mapping are byte-identical to the retained source; `source-integrity.json` verifies that fact. `meter.c` is unchanged. Native metrics still describe the actual CLI's native `wait4`, not Python or the supervisor; missing/invalid native metrics still fail and retain explicitly labeled supervisor fallback resources. Output bytes/hashes, caps, JSON validation, file/pipe sinks, record signals, timeout precedence and contracts are preserved. In particular, the existing shared runner assigns `timeout` before its descendant-held-pipe branch; this patch does not relabel that failure.

No CLI, scheduling, case selection, RNG, sample-count, report schema, production code, native meter protocol or historical shared signal caller changes are proposed. The AST check confirms all other existing functions/classes are unchanged. No pidfd or Phase7/Flight14 infrastructure was copied.

## Portability and concrete remaining sites

`os.waitid` became available on macOS in Python 3.13; Python 3.11/3.12 lack this API even though Darwin has a native waitid interface. See the [Python documentation](https://docs.python.org/3/library/os.html#os.waitid) and [CPython issue 113536](https://github.com/python/cpython/issues/113536). The candidate's explicit pre-acquisition capability error prevents an unsafe fallback but **does not preserve the old macOS version contract**. A pidfd-only solution would also lose macOS support. Linux and a Python exposing these POSIX APIs are the candidate's implemented path; macOS runtime behavior was not tested. Root must resolve a reviewed compatibility path or an explicitly authorized version-contract change before promotion. No ctypes ABI shim, kqueue observer, native helper redesign or fallback early reap was added in this lane.

`Fixture.close()` at retained `harness/run.py:513` is a **separate concrete defect**: first close signals before `Popen.wait()`, but leaves `self.proc` set; a second close numerically signals the already-reaped PID. A caller's prior `poll()`/`wait()` would have the same issue. The new pure model reproduces repeat-close risk without executing a fixture. It is reported, not silently repaired by changing the globally shared `kill_group()`. Fixture startup also catches `Exception`, not KeyboardInterrupt, and opens its log before Popen; those historical acquisition paths were read and left outside this measurement patch. Thus this proposal's ownership guarantee is specifically for `measure()` and its acquisition unwind, not every harness process owner.

`phase6_workloads.py` also calls `spawn_measured()` directly and has its own wait/signal lifecycle. Its successful spawn interface is unchanged; this lane does not claim to fix that caller's handoff, timeout, exception or reap policy. Root should review the shared helper's exception-path effect when considering promotion. The Phase7 NativeChild and Flight14 adapter were inspected as ownership context only.

The guarantee assumes the existing single-threaded runner, default SIGCHLD, no competing child reaper, and descendants remaining in the acquired process group. SIGKILL of the observer, SIGTERM's default process termination, OOM, competing reapers racing after ownership checks, and children deliberately escaping the group are not covered. A cleanup syscall failure propagates; it cannot authorize a later signal to a reaped ID. Final `wait4` retains the existing blocking cleanup policy; no new whole-run hard-bound claim is made.

## Tests actually executed

All tests below used mocks; **zero child processes were created and zero remain from this lane**. Each source import and each test ran with real subprocess, spawn, fork, exec, wait and kill entry points guarded. Resource/selector/pipe operations needed by a test were explicitly replaced by its model. No imported existing test module was executed. All temporary directories and spool files used `TMPDIR=/home/ajs/.cache/pb-phase7/tmp`.

The final command was:

```sh
TMPDIR=/home/ajs/.cache/pb-phase7/tmp PYTHONDONTWRITEBYTECODE=1 python /home/ajs/.cache/pb-phase7/shared-runner-owned-cleanup-r1/run_validation.py
```

- **29 new tests passed** (`logs/proposed-29-final.log`). Coverage includes exact native payload values versus deliberately different supervisor rusage, native wall and timestamp offset, missing/invalid payload failure, output/hash/JSON/file/spool behavior, record signals, timeout, held-pipe/EOF models, exact-child ECHILD rejection, nondefault SIGCHLD and missing-WNOWAIT rejection, partial acquisition, capture/callback exceptions, pending SIGINT at handoff/cleanup, and interrupted final wait. One test intentionally reproduces the separate unchanged Fixture repeat-close defect.
- **Two original-source controls failed as expected** (`logs/original-ownership-negative.log`), at `killpg` after the model's eager reap. The model independently makes a reap release the identity to an unrelated group, so success requires actual ordering rather than a mocked no-op signal.
- **Three targeted mutants each failed as expected**: post-reap group signal, replacing native CPU with supervisor CPU, and replacing observed exit time with final reap time. Source identities and failure counts are retained in `validation-results.json` and corresponding logs.
- Earlier standalone runs of this new suite: 25 tests, 24 passed/1 failed (`logs/proposed-first.log`), then 25/25 passed (`logs/proposed-final.log`). The first failure was a synthetic native start timestamp preceding the modeled observer start, contradicting the test's expected positive offset. The synthetic start/end pair was corrected together, retaining the exact `123.456789 ms` native interval. The failure log was retained.
- Static AST/source checks passed, all retained live input hashes matched, and `git apply --check` passed against the off-tree before tree. No patch was applied.

The complete original measure/spawn/Fixture paths and discovered `test_harness.py`, `test_test_runner.py`, `test_meter_io.py`, `test_phase6_process_cleanup.py`, `test_phase4_workloads.py`, and discovery runner were read. There are no `test_run.py` or `test_runner.py` files in this repository's hidden-inclusive inventory. Existing suites were not rerun: `ProcessTests` and `MeterIOTests` compile native helpers; other discovered tests can execute prebuilt binaries. Root alone owns the reviewed-live whole-suite rerun.

These tests establish causal ownership order in an adversarial state model and exact propagation of a **synthetic** native payload. They do not establish actual kernel WNOWAIT behavior, descendant termination on this host, native meter execution, measured resource accuracy, macOS compatibility, performance, or whole-suite acceptance. No Go/Rust CLI, fixture binary, meter binary, compiler, build, profile, shared lock or performance campaign ran.

## Root timing-plan correction

The sum of all worst-case per-command timeouts is not a necessary premise for attempting the proposed whole suite. Root may attempt the **entire 15-case suite once**, preserving its full RNG order and all existing warmup/repetition counts, under a separately validated hard wall bound and ownership cleanup. If it cannot complete within that bound, the attempt is failed/incomplete and unadmitted. No splitting, pooling, case reduction, count reduction or acceptance by partial completion follows. This is a planning clarification only: it changes no prior preparation document, schedule, declaration or acceptance status.
