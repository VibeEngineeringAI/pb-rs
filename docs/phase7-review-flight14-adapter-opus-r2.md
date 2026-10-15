Flight14 adapter R2 — fresh independent closure review (r2b), 2026-10-15
=========================================================================

VERDICT: CLOSED. B1, B2, D3, S1 and the local finalization flag-handoff gap are
closed in the frozen source. This exact freeze is READY for root execution of
run-after-review.sh. No blockers. Four non-blocking notes (N1-N4) below.

This is a fresh round. It is not a continuation of the cancelled delegated task
(…phase7-flight14-adapter-r2-closure-opus-20261015-1), whose output I neither
used nor edited. ../closure-review/ (that task's partial artifacts) is untouched.

Inputs read and hashed
----------------------
worktree HEAD                       ea70f7ad7d501d59b26cf6397f6a0b1f28143fc4
adapter (worktree, read in FULL)    bc99b29175a00335bd92c8946504e1eb42e10c29342adb4620c8072b50a46e51 (492 lines)
  == cache adapter-r2.py == proposed-adapter-handoff.py (cmp identical)
r1 review doc (read in FULL)        d6d1c8e1b2956005fa92804ba01d75e68df35e87848cb625126407eade2b7c4e
source-freeze.json                  bb8573a78c372ad0fb6d04e3ddf4014b6373e15a9f04b8742686ee11469cd7b5
adapter-r1-to-r2.patch (read FULL)  456ee2b81160bdfd89cb11eaadf3c13473130f0e71c32467de8bf486106640d4
test_replay.py == proposed-tests.py 2bec9d784f0b6a57a696fd315e00387fbc5817d079424690e81b982124876aeb
pure-tests.log (38 ok)              57d93389258d1673f6966099947259268dfaaa57a2cb9fd2820fee66638b3439
handoff.txt                         4b2c02650da2b08ec25f7c6243f6bc219391541137e69e0aad90d832442086f6
run-after-review.sh                 0eab9119529bc38a7708c214a380181eda6bf6576cec7edf4b1986a584be099e
r1-preservation.json                7640d04eaa4860ce9a6f84b8fc7d1ad53eadbaa35f4d2d249619b90fb221672c
late-signal-local-audit.json        c6eeb981d011f56d7b2fa968616dd62d029cec59b7bef86dfc997f89f349155f
late-signal-before-fix.log          982abcfeb7ea6540212742f0bddbc7c8a80c49577fe5aaf719b4cdba807dd7a0

Freeze/provenance checks (executed: sha256sum / python -I reads only)
- `sha256sum -c source-freeze.sha256`: all 92 entries OK.
- The r1 original freeze fails on exactly one entry: the live adapter path, which is
  now r2. This is the documented and expected mismatch. Every other r1 entry is OK.
  r1-preservation.json: all 19 r1-snapshot files match. The r1 originals in
  merged-flight-pr4-adapter-r1/ are present and were not modified.
- Binaries were only hashed, never executed. All three match:
  pb-normal         6d80054f5d87b081bfe51bed014a6f64c8e9a01c2c7f7fcb4c6434e221868443
  pb-go             fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314
  flight-fixture-r2 2d76fd06c8175fe6ede701008643b412b858106da0eca59dc52fb1ef892b3a7b
- run-after-review.sh checks the freeze, then execs the adapter with exactly these
  three paths and SHAs. Default --out is r2/decoder14-normal-r2, and that path does
  not exist yet.
- The original verifier (e27354c8…) and helper (24b2324b…) were only hashed and
  never executed. The proof (64a69e39…) is retained, and its hash matches.

Tests executed (pure, guarded; no actual adapter run, no child process)
-----------------------------------------------------------------------
1. Author suite, run on a COPY so its temp dirs stay in my directory:
   author-copy/test_replay.py (2bec9d78…) and the read-only inputs it needs
   (original-*, r1-preservation.json, adapter-r1.py; their hashes match the freeze).
   `python3 -I -B test_replay.py`. Result: 38 tests, OK.
   Log: author-tests.log  d955707f24b4a66c647bd8dd30607eeac69b6795b80945d36ac39e4adfde88a9
   Guards: Popen, waitid, waitpid, killpg, kill, fork and posix_spawn fail on use.
   Process-local note: this suite still calls the real signal.signal and setitimer
   inside main(). That is harmless, because the timer is cancelled and no child exists.

2. My independent suite: test_closure_r2b.py
   f5d04442bd22c70c277fa709c44612369959e6cf8f5d92ac421a7b123ab81ac7
   Guards: subprocess.Popen, os.{waitid,waitpid,wait,wait3,wait4,killpg,kill,fork,
   forkpty,posix_spawn,posix_spawnp,exec*,system}, signal.pthread_kill and setitimer.
   Each test swaps in a fake kernel. The only real signal anywhere is
   signal.raise_signal(SIGINT) sent to the test process itself.
   Final run: 5 tests, OK.
     closure-tests.log  9a5e69e70291778951ddcb2dee3846d0929455aad369954f823387d894055a8c
   The first attempt failed because of bugs in my own harness. Its log is kept:
     closure-tests-attempt1-harness-bugs.log
     99d30128ff0cda7980831147664a38922689a3c47f47abd1c07a3286499381f6
   The two bugs, neither of which is an adapter defect:
   (a) The main sweep injected the flag before main installs its handlers. A real
       SIGINT there is KeyboardInterrupt, and line 407's reset is intended.
   (b) The capture sweep required capture.json even when the injection came at line
       309, before the try block and before any child existed.
   After fixing (a) and (b), no adapter-side change was needed.

   a) CaptureSweep.test_every_line_interrupt_in_capture (TESTED)
      Injects SIGINT at every one of 197 adapter line events reached from capture(),
      covering capture, exited, observe, kill_group, file_sha256 and save. A signal
      that lands inside a masked region is held pending and delivered when unmasked.
      The fake kernel refuses killpg of a reaped or unknown PGID and gives ECHILD on
      reaped pids.
      Invariants checked at all 197 points: no leaked child; each pid gets exactly one
      killpg strictly before exactly one reap; capture.json is durable whenever a
      child exists; cleanup_errors are empty; the recorded exit is never fabricated
      (always 1 or absent); the mask is restored; no spawn happens under the mask;
      the interrupt is never lost. Result: 0 failures.
   b) Sensitivity control: the same sweep with an r1-style RAISING handler fails 4 of
      197 points. Examples: capture:328 leaks fixture 124, and capture:339 leaks CLI
      125. These are exactly the Popen-return/registration windows. So the sweep is
      sensitive, and the non-raising flag is what closes them.
   c) MainSweep.test_every_line_interrupt_in_main (TESTED)
      Fake captures replay the 14 historical exact pairs. The baseline returns
      rc=0 / passed / 14. Then SIGINT is injected at all 2149 points from handler
      install onward: every main-frame line, plus every adapter line event in the
      finally block, covering end_bindings, the artifact hashing loop and both
      terminal saves. Every point before the handler-restore loop (line 485) ends
      in rc=1 and status=failed. Only signals arriving after line 485 leave
      "passed", which is the documented linearization point.
   d) RealMask.test_real_mask_defers_and_fails (TESTED with the REAL CPython
      pthread_sigmask)
      At line 455 the real mask is confirmed to block SIGINT. A self-raised SIGINT
      stays pending, is delivered on unmask through CPython's PyErr_CheckSignals in
      pthread_sigmask, sets the flag, and the next safe point fails. Result: rc=1,
      status failed, and an "interrupted by signal 2" error recorded.
      Afterwards the default SIGINT handler and an empty mask are restored.
   e) RealMask.test_real_unmasked_mutant_loses_signal (control)
      Removing only the handoff mask reproduces the old false pass (rc=0, passed).
      This independently confirms the author's late-signal-before-fix finding and
      shows the fix is load-bearing.

Finding-by-finding
------------------
B1 CLOSED (source + tested)
   exited() and observe() only use waitid(WEXITED|WNOWAIT|WNOHANG) and never reap.
   kill_group checks four things, in order, before killpg: identity in the owned map
   (`owned.get(pid) is proc`), returncode None, SIGCHLD still SIG_DFL, and a
   successful exact-PID waitid. ECHILD raises before any signal.
   Between that observation and killpg, the child is unreaped: it is either live or
   a zombie that keeps PID==PGID reserved. Cleanup signals are masked at that point,
   and there is no other thread or reaper.
   The final reap is waitpid(pid, WNOHANG) on the exact pid, bounded at 2s. Its
   status gives the exit, sets returncode and removes ownership. The exit comes only
   from that reap, so SIGKILL sent to a zombie leaves the CLI's true status
   unchanged. There is no use of Popen.wait/poll/communicate, and grep confirms none.
   A session leader cannot setpgid/setsid away, so the leader cannot leave its PGID.
D3 CLOSED (tested)
   The reap and its status bookkeeping run under the mask, and the handler cannot
   raise. Sweep (a) covers every line of kill_group. ECHILD on the exact reap
   becomes a cleanup error, never exit 0.
B2 CLOSED (tested)
   The whole finally block is masked: both group signals, both reaps, stdout/stderr
   collection and the atomic save of capture.json. The original mask is restored
   only after that, and check_interrupt follows. Popen is called only outside every
   mask. Sweep (a) asserts there is no spawn under the mask, and the author's
   lifecycle test does the same.
S1 CLOSED (tested + source)
   main rejects SIG_IGN, custom or unknown (None) SIGCHLD before claiming --out.
   capture() rechecks before each Popen, and kill_group rechecks before killpg. The
   disposition is never reset; the author test asserts signal.signal is not called.
   Source note: Linux execve clears sa_flags, so SA_NOCLDWAIT cannot be inherited
   behind a SIG_DFL disposition.
Local finalization handoff gap CLOSED (tested, including with the real kernel mask)
   The flag test, its reset and the FINAL_TIMEOUT re-arm happen under the mask, so a
   signal in that window is held and raises at the next safe point. A pending
   TOTAL_TIMEOUT SIGALRM in that window fails conservatively. A late signal during
   the first terminal save is caught by the post-save check and rewritten as failed.
   Sweep (c) has zero pre-restore failures.

Policy preservation (tested by author suite AST check + my read of the patch)
   The AST of messages, load_streams, env, scenario, seed and compare is identical
   to r1. Also unchanged: the 14 cases and their order, Go before Rust, the
   status-3 probe-end appended exactly once, the exact exit/stdout/stderr comparison
   with output_waivers [], and the fixtures. No production or harness file is
   touched by this lane, and the patch only changes the adapter.

Declared residual limits: honest and narrow
   - Standalone, single-thread, no competing reaper. A foreign SIGCHLD mutation or
     reaper is unsupported, and a detected loss (ECHILD) fails closed with no
     numeric signal. Honest.
   - SIGKILL of the runner, interpreter/memory failure, Popen internals before a
     handle is returned, and a Popen or filesystem syscall that never returns are
     all outside the guarantee. Honest. Because the handler no longer raises, the
     watchdog cannot preempt a hung syscall (PEP 475 retries it). Every syscall in
     the normal path is bounded: select ≤50ms, sleep ≤10ms, os.read only after
     readiness, and regular-file I/O.
   - Escaping descendants and path replacement that is restored between hash
     checkpoints are unchanged from r1, and are documented in the docstring.
   - Source-only (not tested): CPython's Popen.__del__ and subprocess._active can
     reap a still-unreaped Popen object. That only happens after a cleanup failure,
     and main stops on any cleanup_errors before another Popen (and its _cleanup())
     can run, so it cannot affect an owned child of a later capture.

Non-blocking notes (no change required before the root run)
   N1 (source-only) Terminal two-save. The first save writes "passed". If a late
      signal is then seen and the second save fails with an I/O error, the file still
      says "passed", although the exception gives a non-zero exit. The same is true if
      the runner is SIGKILLed between the two saves. Optional hardening: check the
      flag before the first save as well.
   N2 (tested, sweep c) A signal after the handler-restore loop at line 485 leaves
      proof "passed". A default SIGINT there can still produce a non-zero exit. Root
      must require BOTH exit 0 and status passed; the handoff already says this.
   N3 (source-only) proc.stdout.close() runs inside the per-process finally. If it
      raised, the loop over the remaining owned processes would stop. For a pipe read
      end this is practically impossible, and the capture would fail rather than
      falsely pass.
   N4 This review does not show that the live Go/Rust/fixture processes behave as
      modelled; only the root run can. All lifecycle results above come from a fake
      kernel, except the real-mask delivery test.

Not executed
   The adapter as a program, the CLIs, the fixture, the probe, the original verifier
   and helper, run-after-review.sh, and any build, compiler, lock, capture, profile,
   performance or broad old suite. There was no nested delegation.

Writes: only this directory (closure-review-r2b/). The test temp dirs were removed.

Root instruction: run `bash .../merged-flight-pr4-adapter-r2/run-after-review.sh`
once. Accept only exit 0 together with status passed, complete true, passed 14, no
errors, 14 cases with same exit/stdout/stderr all true, input end hashes unchanged
and artifact hashes present. Hash proof.json externally. On failure, keep the output
and retry only with a new --out.
