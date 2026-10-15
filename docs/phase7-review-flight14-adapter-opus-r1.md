# Phase 7 review: merged Flight PR4 decoder-14 adapter (Opus, round 1)

Scope: an independent review of
`docs/evidence/phase7/merged-flight-pr4-replay.py`
(SHA-256 `c794230d1f2b688e26938683b4dd7e70a2061c8296b5e97c9975854e4b5b86a7`,
390 lines, read in full). I also read its seven fixtures and manifest, the
pinned `docs/evidence/phase6/pr4-fixes-validation.json` (`a92a6ab0…`), and the
lane cache `~/.cache/pb-phase7/merged-flight-pr4-adapter-r1/`: `handoff.txt`,
`source-freeze.{json,sha256}`, `run-after-review.sh`, `test_replay.py`,
`pure-tests.log`, and the audit copies of the original `verify_decoder_flight.py`,
`probe.py` and `proof.json`. As a secondary item, I confirmed the O1 closure.

## What was executed and what was not

- **Executed:** `sha256sum` on the adapter, `sha256sum -c source-freeze.sha256`
  (all 17 entries OK), and Python `-I` reads of the validation JSON. I made one
  run of my own guarded pure-mock file,
  `~/.cache/pb-phase7/review-flight14-adapter-opus-r1/test_review_mocks.py`
  (`e7478985…`), and redirected its output to `mocks.log` (`91394ae9…`). That
  file replaces `Popen`, `killpg`, `kill`, `waitpid`, `waitid`, `fork`, `exec*`
  and `posix_spawn` with guards that fail on any call. No process was started
  and no signal was sent.
- **Mock results (one attempt, no retry):** 5 ok, 2 ERROR. Both errors are a
  bug in my review harness, not evidence about the adapter.
  `object.__new__(subprocess.Popen)` ran after my own guard had replaced
  `Popen` with a `MagicMock`. The two claims those tests targeted (D3's
  ECHILD→0 path and S1) are therefore **source-only**. They rest on reading
  CPython 3.14.7 `subprocess.py:2039-2075`.
- **Not executed:** the adapter itself, the author's pure tests, the native
  fixture, the Go or Rust CLI, the probe, any compiler, build, lock or
  performance step, and the Phase 4 Python suite.

## Verdict

**Not ready for root execution yet. Two blockers (B1, B2) need small fixes.**
Provenance, case selection and order, the scenario, environment and config,
and the exact no-waiver comparison all match the original. Every failure mode I
found makes the run fail, never pass falsely, except S1. B1 and B2 are
ownership and cleanup defects: they can signal a process the adapter does not
own, or leak one it does.

## B1 (blocker): numeric `killpg` after the CLI leader has been reaped

`capture()` (lines 262-264) calls `p.wait(timeout=CLI_TIMEOUT)`, which reaps
the CLI when it exits normally. The `finally` block then calls
`kill_group(p)`, and `kill_group` (lines 227-234) runs
`os.killpg(proc.pid, SIGKILL)` unconditionally.

The CLI was started with `start_new_session=True`, so its PGID equals its PID.
Once the leader is reaped, and if no group member is left (the normal case:
the prior integration review found Rust `src/tail` spawns nothing; Go was
not checked), PID/PGID N is free. An unrelated process that
recycles PID N and becomes a group or session leader can receive this SIGKILL.
That includes shells, systemd units, or any `setsid` child.

`pid_max` is 4194304 and allocation is cyclic, so the chance is very small.
But this is the wrong invariant, and the source states the wrong one:

- The docstring at line 13 says "Owned process groups receive SIGKILL even if
  their leader has already exited."
- `handoff.txt` says the same.
- The pure test `test_kill_group_even_when_leader_already_exited` asserts the
  unsafe behaviour (`Mock(returncode=1)` → `killpg(123)`).

Mock evidence (`mocks.log`, `test_normal_cli_exit_is_reaped_before_numeric_killpg`):

```
[('wait', 125, 8), ('killpg', 125, 'leader-reaped'), ('wait', 125, 2),
 ('killpg', 124, 'leader-reserved'), ('wait', 124, 2)]
```

The fixture path (pid 124) is safe because nothing reaps the fixture before
`kill_group`. A zombie leader keeps its PID, and so the PGID, reserved. The
original `probe.py` never signalled the CLI group after `communicate()`, so
the adapter introduced this defect.

**D3 (same root cause, source-only):** CPython's `Popen._wait` with a timeout
calls `waitpid(WNOHANG)` and then `_handle_exitstatus`. If the watchdog or
SIGINT handler raises between those two steps, the leader has been reaped but
`returncode` is still `None`. `kill_group` then sends a numeric `killpg` to a
freed PID. Its `proc.wait()` gets ECHILD, and `_try_wait` maps that to `sts=0`,
so the run records `exit: 0, reaped: true`. The run still fails because it was
interrupted, but the process record is false.

**Minimal correction.** Observe exit without reaping, signal the group while
the leader is still an unreaped zombie, and reap last. Use
`waitid(WNOWAIT)`. A pidfd is equivalent, but `killpg` still needs the
reserved numeric PGID, so a pidfd adds nothing here.

```python
def exited(proc, timeout):
    # Observe exit without reaping: the zombie keeps PID == PGID reserved
    # until kill_group has signalled the group and then reaps it.
    deadline = time.monotonic() + timeout
    while os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        time.sleep(min(0.01, remaining))


def kill_group(proc):
    # Numeric killpg is only safe while this unreaped leader reserves the PGID.
    if proc.returncode is not None:
        raise RuntimeError(f'leader {proc.pid} already reaped; refusing killpg')
    ...unchanged...
```

In `capture()`, replace `p.wait(timeout=CLI_TIMEOUT)` with
`exited(p, CLI_TIMEOUT)` and set only `complete=True` there. Set
`record['exit']` after the cleanup loop, from the reaped CLI:
`processes[-1].returncode` when `complete`. A reap failure already shows up in
`cleanup_errors` and fails the run.

SIGKILL sent to a zombie leader does not change its stored status, so the
recorded exit is exactly the CLI's real exit. `exited()` raises
`ChildProcessError` if SIGCHLD is ignored (see S1), so that case now fails
closed. The mocks `test_exited_never_reaps_and_bounds_timeout`,
`test_exited_fails_closed_when_sigchld_ignored` and
`test_guarded_kill_group_signals_before_reap_and_refuses_reaped` pass against
these helpers.

Test changes:

- Replace `test_kill_group_even_when_leader_already_exited` with a
  refusal test.
- Add an ordering test (`killpg` before the reaping `wait` on the normal-exit
  path).
- Point the timeout and interruption tests at `m.exited` / `os.waitid`
  instead of `cli.wait`.
- Fix the docstring and handoff wording to "leader exited but not yet reaped".

## B2 (blocker): signals delivered during cleanup can skip SIGKILL of a live group

The SIGALRM/SIGINT/SIGTERM handler that raises `InterruptedError` stays
installed while `capture()`'s `finally` block runs. A second SIGINT, or the
1200 s watchdog firing during cleanup of a timed-out CLI, raises inside
`kill_group` before `killpg`. The `except Exception` records a cleanup error
and moves on to the fixture, so the timed-out CLI's process group is never
killed and stays running after the adapter exits. `pb tail` may also keep
reconnecting once the fixture is gone.

Mock evidence: `test_signal_during_cleanup_skips_sigkill_of_timed_out_cli_group`
gives `killpg` calls `[125(raised), 124]` and process record
`{'pid': 125, 'exit': None, 'reaped': False}`.

**Minimal correction.** Make the whole `finally` body of `capture()` a
signal-masked critical section. It is bounded by 2 × `REAP_TIMEOUT` plus one
save.

```python
    finally:
        old = signal.pthread_sigmask(signal.SIG_BLOCK,
                                     {signal.SIGALRM, signal.SIGINT, signal.SIGTERM})
        try:
            ...existing cleanup loop, secs, stdout/stderr, save(capture.json)...
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)  # pending signal raises here
```

Restore the mask only after `capture.json` is saved, so the deferred
`InterruptedError` cannot skip the checkpoint. Do **not** mask around `Popen`.
The signal mask is inherited across `fork`/`exec`, so the fixture and CLI
would start with SIGINT and SIGTERM blocked, and that changes the system under
test. A residual window remains: a signal between `Popen` returning and
`processes.append` would orphan that child. It lasts a few bytecodes. Document
it next to the existing "descendants escaping their group" limit.

## S1 (should fix, closed by B1's fix): inherited `SIGCHLD=SIG_IGN` makes the exit check vacuous

This finding is source-only. The mock attempt errored, as explained above.

`SIG_IGN` for SIGCHLD survives `execve`. If the root launcher passes it on,
the kernel auto-reaps both CLIs, and CPython's `_try_wait` turns ECHILD into
exit 0 for both Go and Rust. `compare()` then reports `same['exit'] == True`
even though Phase 6 pins exit **1** for all 14 cases. Stdout and stderr are
still compared, so this is a partial vacuity, not a total false pass.

B1's `waitid` raises `ChildProcessError` in this situation, so the run fails
closed. Two optional additions:

- `signal.signal(signal.SIGCHLD, signal.SIG_DFL)` at the start of `main` (I
  checked this shell: SigIgn is `0x4`, SIGQUIT only).
- Record each live exit next to the pinned validation `exit` as a diagnostic.

## Checked and found consistent

- **Provenance.** The validation JSON is pinned by SHA. There is exactly one
  `full_flight_decoder_pairs` check with `passed == 14` and the exact 14-name
  set. Every stream's SHA is derived from the validation cases, read once,
  hashed, and checked against the manifest's SHA and byte count before it is
  parsed. The fixture bytes match `source-freeze.sha256`. The adapter hashes
  itself at start, and the root wrapper checks the freeze before it `exec`s.
  All inputs are re-hashed at the end, and any change fails the run.
- **Case order.** The original `sorted(sources)` puts
  `/…/pb-pr4-fixes/go-contract/*` before `/…/pr4-opus-decoder/streams/*`
  (`b` < `r`). Within go-contract the order is `o1_` < `o2_` < `orphan_*`. This
  gives exactly `STREAMS` and JSON-then-text, which matches the
  `original-proof.json` key order. Each mode runs Go first, then Rust, as in
  `probe.BINS`.
- **Framing.** `messages()` uses the same field (`vtable + 10`, bodyLength) as
  the original and adds bounds checks. It adds no normalization. Each stream
  gets `status 3 / probe-end` appended exactly once.
- **Scenario, env and config.** These are byte-identical in content to
  `probe.start_fixture`/`env`/`seed`, apart from JSON indentation in
  `scenario.json`, which the fixture parses. Each role gets its own fixture,
  HOME and XDG roots, and no ambient environment is inherited.
- **Comparison.** Exit, stdout and stderr bytes are compared exactly, with no
  waivers. The original appended `<<TIMEOUT>>` on a timeout and still
  compared; here any timeout, error, missing key or cleanup error fails the
  run. This is stricter than the original.
- **Readiness.** A chunked `os.read` against one deadline replaces the
  original's unbounded `readline` after `select`. Partial banners are kept,
  and only a loopback HTTP URL is accepted. The fixture's stdout pipe is not
  drained after readiness, as in the original. If the fixture filled that
  pipe, the run would fail by timing out, never pass falsely.
- **Bounds.** Worst case per capture is 10 + 8 + 2 × 2 = 22 s. 28 captures
  take at most 616 s, under the 1200 s watchdog. Adding the 60 s finalization
  gives ≤ 1264 s, under 30 minutes.
- **Interruption of finalization.** If interrupted, the last checkpoint stays
  `incomplete` or `failed`. `passed` is only written by the final save, so
  this is fail-safe.
- **Residual parity notes (no action).** The CLI's stdout and stderr go to
  regular files here, where the original used pipes. Both roles get identical
  conditions. Stdin is still inherited, as in the original; I did not verify
  that neither `pb tail` reads it.

## O1 closure confirmation (source-only; suite not re-run)

- The current `harness/test_phase4.py` hashes to `0d611525…`, which equals the
  closure's `test_source_sha256`.
- The diff from `HEAD` does two things. Line 13 keeps Phase 7's
  `deferred_boundaries() == []` assertion, plus `len(historical) == 4`.
  `test_deferred_stub_guard_remains_active` (lines 385-400) now iterates
  `historical_deferred_boundaries()` after `assertTrue(checks)`.
- `harness/phase4.py:359-363` defines four historical boundaries: three text
  and one structured. Both stub-envelope branches are therefore exercised.
- The network assertion runs four times against `phase3.py:2756-2757`
  (`result["trace"]` → "network requests made"). It is no longer vacuous.
- The cache log `phase6-merged-integration/test-phase4-o1-fixed.log`
  (`caa01ae5…`) shows 29 tests OK, including this guard.
- The closure's three controls fit the source. With `baseline`, all checks
  pass. With `empty-historical`, only `assertTrue(checks)` can fail (1
  failure). With `drop-network`, only the `any('network' …)` assertion can
  fail (1 failure).
- **Provenance gap (non-blocking):** the closure JSON keeps no mutant diff,
  command, or log hash for these controls, and I found no raw control logs in
  the cache. The controls are a credible claim, but they cannot be audited from
  the retained evidence. Adding the mutant patches and log SHAs would fix that.

**O1 is closed at source level.**

## Required before root runs `run-after-review.sh`

1. B1: add a non-reaping `exited()` and a guarded `kill_group`, take the exit
   from the final reap, and fix the docstring, handoff and test.
2. B2: wrap `capture()`'s `finally` in a signal mask, without masking around
   spawn.
3. Re-freeze `source-freeze.json`/`.sha256` and run a fresh pure-test pass.
   The current freeze binds `c794230d…` and will no longer match.
