# Review: Linux-only generated shared measurement runner R2 (Opus, round 1)

Fresh independent review, 2026-10-15. This is a review of the ownership adaptation and its stated use context only. **No benchmark, performance, or suite acceptance is claimed.** No code, source, patch, or packet file was modified. No nested agents were used. Review artifacts are in `/home/ajs/.cache/pb-phase7/review-shared-linux-adapter-opus-r1/`.

**Verdict: no blocking findings** for the declared use: one whole 15-case suite, three labels, 5 warmups and 30 repetitions, `--seed 2026`, explicit `--suite`/`--fixture`/`--meter`/binary paths, run under `/usr/bin/flock --nonblock --no-fork` and the reviewed bounded driver on Linux/Python 3.14.7. Both concrete root defects are fixed: the measurement reap-before-later-`killpg`, and the Fixture repeat-close/prior-poll signal to a released PID. Nine nonblocking findings follow, ranked by importance.

## Inputs read

- In full: `docs/phase7-shared-runner-linux-ownership-r2.md` (`6c4899b9…`), `docs/phase7-shared-runner-cleanup-proposal.md`, `docs/phase7-shared-performance-preparation.md`, and `docs/evidence/phase7/shared-performance-preparation-disposition.json`.
- The complete R2 patch, the generator, the native control, and the relevant regions of the generated runner. I read the generated runner's `kill_group`, the new helpers, `spawn_measured`, `measure`, `Fixture`, `sample`, `host_metadata`, `run` and `main`.
- `harness/meter.c`, read only: its signal and fd behavior.
- The use-context `plan.json` (`9b294a1d…`) and `bounded-execution.py` (`fc814f72…`). For `bounded-execution.py` I read only the child pre-exec signal/fd setup and the graceful-signal target. This is not a new generic launcher review.

## Identity and integrity (independently recomputed)

| Item | Result |
| --- | --- |
| Live `harness/run.py` | `b280d129…a849a076` unchanged before and after the review. Git blob `a840e4bc…` equals `ea70f7a:harness/run.py`. |
| `harness/cases.py` | `b892e1b5…`, blob `61561583…`, equals the commit blob. |
| `artifact-freeze.json` | `11b7453f…d9c098b`. All 40 listed packet files and 7 worktree files match bytes and SHA-256. No unlisted file is present. |
| Generated `recipe-generated/run.py` | `d65cf16d…2f5e`. `generated/run.py` is identical. Files are 0444 and the directory is 0555. |
| Patch | `62e1b8c9…ef4b`, LF, 11 hunks. Applied independently with my own exact-context applier, with no fuzz, it gives exactly `d65cf16d…`. |
| `r1-to-r2.patch` | Applied independently to the R1 proposed runner (`462fe906…`), it gives exactly `d65cf16d…`. |
| R1 packet | `artifact-freeze.json` `20392f42…` matches `r1-freeze-before.json`, and all 34 entries match. The R1 proposal `5c86fdf3…` is intact. |
| Suite | `docs/evidence/phase5/regression-suite.json` `3c731724…92bc` has 15 cases. 10 have fixtures. None has a `phase4`, `spool_output`, `signal_after_lines`, `close_stdout_after_lines`, `read_delay_ms` or `jsonl_rows` key. |

## Static checks (own script, `scripts/static_checks.py`, `logs/static-checks.json`)

- **Added definitions:** `_require_measured_ownership`, `_defer_measured_interrupt`, `_signal_measured`, `_finish_measured` and `_fixture_acquisition_handoff`.
- **Changed definitions:** `spawn_measured`, `measure`, `Fixture` (only `__init__` and `close`), `run` and `main`. Nothing was removed. Besides the docstring, the only new top-level statement is the `contextlib` import.
- **`run()`:** the body is AST-identical to the base after dropping the generated first statement `_require_measured_ownership()`. This preserves the single `Random(seed)`, case order, warmup/repetition rounds, insertion-order shuffles and checkpoints.
- **Result construction and native override:** the 7-statement tail of `measure()` after spool materialization is AST-identical. This covers the fallback labels, the native JSON override, the native wall/CPU/RSS/IO mapping and the failure precedence.
- **Unchanged pieces:** the `wall = (exited - started) / 1e6` statement is unchanged. In the select/read block, only the two `kill_group` calls changed, to `_signal_measured`. `kill_group`, `Fixture.trace`/`milestones`, `sample`, `summarize`, `compare_samples`, `host_metadata` and `compare` are unchanged.

## Tests actually executed

1. **Author's durable pure module rerun, unchanged:** 39/39 passed. It created zero processes; the output is byte-identical to `logs/pure-final.log` (`357a3837…`). This closes the R2 note that the final module had not been fully repeated.
2. **My own independent kernel-model suite** (`scripts/review_model_test.py` `d9bb9ebb…`): **17/17 passed** against the canonical runner (`logs/review-model-canonical.log`), with zero real process calls. The kernel model reuses PID==PGID for an unrelated group as soon as a child is reaped, and treats a blocking wait on a live child as a deadlock. Coverage:
   - **Ordinary exit 19:** the sole `wait4` is last, preceded by one `killpg` to the still-zombie leader. The fallback wall equals the observed-exit clock, not the reap clock. CPU and RSS come from the final `wait4`. The child gets `setsid=True` and an empty `setsigmask`, while the parent's SIGINT is blocked during the handoff.
   - **Native payload:** it overrides supervisor rusage.
   - **Exit status:** signal 15 stays distinct from exit 143.
   - **Interrupts and failures:**
     - `KeyboardInterrupt` during the drain kills the group, then reaps, closes the pipes and both spools, and restores the mask.
     - An external reaper causes ECHILD with zero signals.
     - A timeout on a live leader produces a single reap.
     - Nondefault SIGCHLD is rejected before spawn.
     - If `fdopen` fails after spawn, the group is killed before the reap and all raw fds are closed.
   - **Fixture:**
     - Repeat close produces exactly `waitid, killpg, wait4`, then no-ops. Popen `returncode` is -9, ownership is cleared, and the child sees a non-blocked SIGINT mask.
     - After a prior `poll()` reap, zero signals are sent.
     - External-reap ECHILD drops ownership, and a second close sends no signal.
     - EPERM keeps the unreaped reference; the retry kills, then reaps.
     - A real SIGINT inside the Popen handoff results in an owned child, which is cleaned and re-raised as `KeyboardInterrupt`.
     - A bad banner gives the `RuntimeError` wrapper and is cleaned up.
     - Nondefault SIGCHLD is rejected before Popen.
     - With no scenario, nothing is created.
3. **Mutants** (`scripts/mutants.py`, `scratch/mutants/mutants.json` `0a1947d4…`): **all 9 were killed.**

   | Mutant | Failures/errors |
   | --- | --- |
   | M1 reap-before-kill | 6F/5E |
   | M2 original eager `wait4(WNOHANG)` reap in loop | 5E: ECHILD at the later ownership check |
   | M3 exit time = reap time | 1F |
   | M4 no `returncode`/WNOWAIT ownership checks | 4F/1E, including the recycled-group signal |
   | M5 no fixture SIGINT deferral | 2F |
   | M6 close keeps `proc` and has no `returncode` guard | 1F/2E |
   | M7 ownership check without WNOWAIT | 10F |
   | M8 no SIGINT handoff mask in `measure` | 1F |
   | M9 no SIGCHLD guard | 2F |

   Mutant sources and logs are retained with their hashes.
4. **One bounded own-child native control.** I read its ownership, cleanup and isolation first: Python-only exec targets, a 45 s alarm, a subreaper, WNOWAIT-checked cleanup, a new output path, and no meter, fixture, pb binary or lock. I ran it once under `timeout -k 5 60`; output `logs/native-control-review.json` (`eeef6326…`).
   - **Overall:** passed in **0.4465 s**. It created **9** children (PIDs 2629819…2631481), with **0 remaining** and the subreaper restored. None of those PIDs exist afterwards.
   - **Traces:** every group signal happened while the leader was still waitable, followed by a sole final `wait4`. The cleanup records show `test_cleanup_signaled=false`. The held-pipe path sent 3 signals, all while waitable, and is labeled `timeout`, which is the retained precedence.
   - **Scope:** this is supervisor-fallback evidence only, not native-meter evidence.

My lane's process totals: **9 children created, 9 reaped, 0 remaining; nothing active.** No Go/Rust CLI, fixture, meter, compiler, build, profiler, PTY, lock or campaign ran.

## Ownership review conclusions

- **WNOWAIT and kill-before-reap.** `measure` only observes exit with `waitid(P_PID, WEXITED|WNOHANG|WNOWAIT)` and saves `now`. Every group signal goes through `_signal_measured`. That function returns early if `returncode` is set, checks the SIGCHLD and platform guard, then confirms exact-child WNOWAIT ownership before the numeric `killpg`. `_finish_measured` signals and then performs the only `wait4(pid, 0)`, with no group access afterwards. Since the leader is normally already a zombie, the final SIGKILL cannot change its saved status or rusage, so the mapping is preserved.
- **SIGINT acquisition.** The measurement spawn is masked and the child gets `setsigmask=()`. The Fixture uses a temporary Python handler rather than a mask, so the Popen child does not inherit a blocked SIGINT. The handler replays after `self.proc` is assigned. `close()` and the final measurement cleanup are masked.
- **Fixture.** `close()` returns without signaling when `returncode` is set. It clears `proc` after a reap and clears ownership on ECHILD. On EPERM it keeps the unreaped reference. It is safe to call repeatedly.
- **Partial acquisition.** The pipes, the selector, the spools, a stream conversion failure, and a failed `chdir` back are all unwound. A PID that was returned is killed before it is reaped.
- **Remaining assumptions.** These match R2's stated limits: single-threaded runner, no competing reaper, descendants staying in their group, and nothing covered for observer SIGKILL, default SIGTERM, or failures inside Popen before it returns. Python's `subprocess._cleanup` reaps only Popen objects that were garbage-collected while unreaped. In this flow that can only happen after a `close()` error, which aborts the run with exit 2 before any later Popen.

## Use-context check (flock `--no-fork` / bounded driver / default signals)

There is **no concrete incompatibility**:

- **Signals at exec.** The driver's child sets SIG_DFL for INT, TERM, HUP, QUIT, PIPE, XFSZ, CHLD, ALRM and the others, sets an empty mask and calls `setsid`, then execs flock. With `--no-fork`, flock execs `/usr/bin/python3` directly; it resolves to `python3.14` `d78f9cf7…` as bound in the plan. Python therefore starts with SIGCHLD at SIG_DFL, so the guard passes. If a SIG_IGN SIGCHLD were ever inherited, the guard fails closed before acquisition.
- **SIGINT handling.** SIGINT starts at SIG_DFL, so Python installs `default_int_handler`; the handoff's callable replay path applies.
- **Graceful stop.** The driver sends its graceful SIGINT to the command's own group, which is Python. The meter and fixture are in their own sessions, so the runner's own `KeyboardInterrupt` cleanup kills and reaps them. If the driver escalates to SIGKILL, cleanup is left to the driver's subreaper, as the R2 limits state.
- **Python not a grandchild.** With `--no-fork`, Python is the driver's direct child, which is correct.

## Findings

**Blocking:** none.

**Nonblocking**, most important first:

1. **N1 – latent `sys.exc_info()` spool defect.** In `measure()`'s `finally`, `if sys.exc_info()[0] is not None: spool_cleanup.close()` also fires on a normal return when `measure()` is called from inside an active `except` handler. With `spool_output=True`, the spools are closed before materialization. My probe reproduced it: `ValueError('seek of closed file')`.
   - It is **unreachable in the declared use**: no suite case sets `spool_output`, and `sample()`/`run()` never call `measure()` inside an `except`.
   - It fails closed and has no ownership effect.
   - Fix before any wider reuse: use a flag set by `except BaseException` instead of `sys.exc_info()`.
2. **N2 – mislabeled static-check evidence.** `logs/scope-final.log` is byte-identical to `logs/scope-first.log` (`6570fff9…`). Both contain the failing `TypeError: expected AST, got 'list'` traceback. No log of the passing final static check is retained; only its output, `validation-summary.json`, exists. The R2 doc's "appropriate changed checks passed" is therefore not log-backed for the static check. My independent static checks above reproduce every substantive claim.
3. **N3 – the lock fd is inherited by every measured process.** This comes from the man page; I did not execute flock. `--close` is incompatible with `--no-fork`, so Python inherits the lock fd. `posix_spawn` file actions do not close it, and `meter.c` does not close fds, so every pb-meter and measured CLI holds the lock fd. The fixture and the `uname` helper do not, because they use `close_fds=True`.
   - The effect is the same for all labels and is fail-safe: an escaped measured descendant would keep the lock held after Python exits, and the passive after-check would see it.
   - Record it as an environment difference from runs that were not wrapped in flock.
4. **N4 – ambiguous flock conflict exit code.** `flock --nonblock` exits 1 on conflict, silently without `--verbose`. That equals the runner's exit 1 for "completed with invalid samples". Use `-E <distinct>`, or disambiguate by whether the report directory exists. Both outcomes are non-admission either way.
5. **N5 – the Python launch must keep the script-directory import.** On Python 3.14.7, `-I` sets `safe_path=True`, and so would `-P` or `PYTHONSAFEPATH`. `from cases import suite` would then not resolve to the generated copy. The plan's argv is correct. Root should keep it that way and require `PYTHONSAFEPATH`, `PYTHONHOME` and `PYTHONPATH` to be unset or empty. The driver records `PYTHONPATH` only.
6. **N6 – child count precision.** The plan's `native_children: 1575` counts direct pb-meter spawns only. Additionally there are 1050 fixture Popen children (10 fixture cases × 105), 1575 CLI grandchildren, and one PATH-resolved `uname -p` from `platform.processor()`, which is evaluated eagerly as the `next()` default. These are unchanged harness behavior, not ownership hazards.
7. **N7 – Python version label.** `generation.json` says `"python": "3.11+"`, but only 3.14.7 was exercised, as the R2 doc admits. The plan binds 3.14.7.
8. **N8 – first native trace lacks a field.** `validation/native-first.json` lacks `test_cleanup_signaled`, so only the final run and my run evidence that cleanup gave no assistance. This is consistent with R2's description and is informational.
9. **N9 – optional dependencies are not reached.**
   - `phase4_workloads` is imported only for cases with a `phase4` key; there are none.
   - The `HERE/contracts` file is read only under `--phase4-oracle-self-check`, which the plan does not use.
   - The relocated `DEFAULT_METER` is unused because `--meter` is explicit.
   - `cases` resolves from `sys.path[0]` to the generated copy.
   - Everything else is standard library.

   No unsupported optional dependency is reached. The macOS/shared contract is unchanged: `harness/run.py` is byte-identical, and the generated runner rejects non-Linux in both `main` and the acquisition guards.

## Not established here

This review does not establish native pb-meter mapping on real hardware, fixture binary behavior, a whole-run hard bound, the outcome of a full 15-case attempt, A/A noise, a quiet host, or any performance or gate result. Root owns the next disposition.
