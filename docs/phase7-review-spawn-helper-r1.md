# Phase7 review: native spawn helper r1

Scope: `harness/phase7_spawn.c` (116 lines) and its integration in
`harness/phase7.py` (`spawn_helper`, `NativeChild`, the `execute()` lifecycle,
`proc_sample`/`descendants` as used against the CLI PID) and the related tests in
`harness/test_phase7.py`. Context read: `docs/phase7-harness.md` (measurement
review r1 response section) and
`/home/ajs/.cache/pb-phase7/harness-measurement-review-r1-response.json`.
The collector/bench/profiler lanes are out of scope and covered by a separate review.

**Execution disclosure:** nothing was executed for this review. Bash failed with
EDQUOT on the harness temp directory, so there was no compile, no test run, no
`git diff`, and no timing. Every finding below comes from reading the source, plus
reasoning about Linux kernel/glibc semantics where noted. I have not checked those
claims empirically on this host. No production or harness file was edited. This
document is the only file written.

## Verdict

The H1 design is sound: posix_spawn of a fresh native image, then fork+exec of
the actual CLI, a CLOEXEC readiness pipe, and an exact `wait4` on fd3. The doc's
central accounting claim is plausible. With glibc's `posix_spawn`
(`CLONE_VM|CLONE_VFORK`), the kernel's exec-time
`setmax_mm_hiwater_rss(&signal->maxrss, old_mm)` puts Python's high-water RSS into
the *supervisor's* `signal->maxrss`. `fork()` allocates a fresh `signal_struct` for
the CLI, so Python's image does not reach the CLI's `ru_maxrss`. What does reach it
is the forked supervisor copy's RSS: a small native floor of roughly a dynamically
linked glibc process. The 64 MiB test is the right empirical guard. Session/ctty
acquisition, fd hygiene and the handshake look correct.

**Not accepted yet.** One high-severity regression has to be fixed first: the
supervisor now reaps asynchronously while Python keeps signalling and sampling
the CLI by PID. Three medium items should be fixed or explicitly waived by the
coordinator before collector preflight.

## Findings

### H1 (High, regression): asynchronous native reap races Python's PID/PGID signalling and sampling

The supervisor calls `wait4` as soon as the CLI exits (`phase7_spawn.c:101`).
Python only learns this the next time `native.poll()` runs (`phase7.py:626-630`).
Within one loop iteration, `status is None` can be stale. During that window:

- **Unguarded `os.killpg(pid, …)` can raise `ProcessLookupError`.** This applies at
  `phase7.py:646` (continue_running), `:649` (session timeout), `:674` (output
  limit), `:731` (resize SIGWINCH) and `:738` (signal action). It happens once the
  native reap has removed the last group member. Previously Python reaped the
  child itself, so an exited CLI stayed a zombie and `kill`/`killpg` succeeded.
  The output-limit path is realistic: a CLI writes more than `max_output_bytes`
  and exits, and its data is still buffered in the PTY master after the reap. The
  `finally` block then runs, and `execute()` raises instead of returning a result
  with `failure='output limit exceeded'`. The deadline/timeout evidence path, which
  the doc says is preserved, has the same exposure.
- **PID/PGID reuse.** After the reap, `sampler(pid)` (`:632`), `descendants(pid)`
  (`:637`) and the `killpg` calls above can target an unrelated process that
  reused the number. `proc_sample` checks identity only *within* one sample (stat
  field 19 at `:387`), not against the CLI's start time captured at spawn. The
  probability is low with a large `pid_max`, but this contradicts the contract's
  "actual PID signal/lifecycle". It also reopens part of measurement review H2:
  the CLI is now almost never observed as a zombie, so a reused PID yields a
  "valid" sample.
- Minor C variant: `stop()` can `kill(pid)` between `wait4` returning and
  `cli_pid = 0` (`phase7_spawn.c:101-105`).

**Fix (helper-scoped, small):** keep the CLI as an unreaped zombie until Python
releases it.

1. The supervisor waits with `waitid(P_PID, pid, &info, WEXITED|WNOWAIT)`. It takes
   the monotonic stamp there and emits `{"event":"exited","pid":…,"exited_monotonic_ns":…}`.
2. The supervisor then blocks on a release fd: fd4, a pipe whose write end Python
   holds, or a read on fd3 if fd3 is made a socketpair. Either a release byte or
   **EOF** releases it, so observer death still releases.
3. Only then does it `wait4` and emit the rusage `exit` event, as today.
4. Python sends the release in `finally`, after its last `killpg`/sample/
   `descendants` call. A zombie group leader keeps the PID and PGID reserved, so
   all of Python's signals and samples stay well-defined (`proc_sample` already
   returns `None` for `Z`).
5. Use `exited_monotonic_ns`, not the gated reap time, for
   `observed_process_wall_ms`. The scripted 200 ms post-exit drain (`:650`) must key
   off `exited`.
6. As defence in depth, wrap the remaining `killpg` calls so that an ESRCH records
   the failure instead of raising.

Rusage is unchanged by the gating: the zombie's counters are final.

### M1 (Medium): partial-acquisition and error-path unwind gaps in `execute()`

- These are acquired before the `try` at `:623`: `openpty` (`:581`), stderr/stdin/
  stdout pipes (`:584-587`), `NativeChild` (`:589`), and the blocking
  `os.write(in_write, stdin)` (`:597`). If `NativeChild` raises (spawn timeout),
  master/slave and all pipes leak. If the CLI exits before reading stdin,
  `os.write` raises `BrokenPipeError`, because CPython ignores SIGPIPE. That leaks
  the fds and leaves the CLI/supervisor unreaped. Stdin larger than the pipe
  capacity blocks forever when the CLI does not read it.
- The `finally` block (`:744-768`) is not exception-safe. `native.wait()` raises
  when the supervisor died without an `exit` event. This happens if the supervisor
  is killed by SIGKILL, or by any signal it does not handle (e.g. SIGQUIT/SIGUSR1
  default-terminate it and orphan the CLI). It also raises on a protocol error.
  `native.close()` raises when the supervisor status is non-zero, e.g. 125 after a
  report write failure. In either case the descendant `killpg` (`:761`),
  `termios_state(slave)` and the fd closes (`:766-768`) are skipped. When
  `wait()` raises, `close()` is never called, so the supervisor is not reaped
  either. The original exception can also be masked.

**Fix:** use `contextlib.ExitStack` (or nested `try/finally`) so each acquired fd
is closed exactly once. Make `native.close(abort=True)` unconditional in an inner
`finally`, and bound `native.wait()` with a deadline. On expiry, SIGKILL the
supervisor and record a failure. Write stdin before the loop only through the
selector, or catch `BrokenPipeError` as evidence.

### M2 (Medium): exec/setup failure is indistinguishable from a real CLI exit 127, and a never-exec'd PID is published

On a failed `setsid`, `TIOCSCTTY`, `chdir` or `execv`, the child prints a
diagnostic and `_exit(127)`s (`phase7_spawn.c:75-84`). The CLOEXEC EOF looks the
same as a successful exec, so the supervisor emits `spawn` for a process that never
ran the CLI. The `exit` event then reports status 127, and the rusage belongs to
the small native child. A real CLI may legitimately exit 127, and a TIOCSCTTY
failure changes the PTY contract while still looking like an ordinary exit. All
setup failures are labelled "exec failure", and the message has no trailing
newline.

**Fix:** use the standard errno-over-CLOEXEC-pipe pattern. On failure, the child
writes `{stage, errno}` (stage = setsid/ctty/chdir/exec) to `ready[1]` before
`_exit(127)`. If the supervisor reads a non-empty payload, it emits
`{"event":"exec_error",…}` instead of `spawn`, followed by the `exit`. Python
should raise, or record `failure='cli setup failure: <stage>'`, and never treat
that run's rusage as CLI rusage. Keep the stderr diagnostic if wanted.

### M3 (Medium): CLI inherits non-default signal dispositions from the observer's environment

The CLI child resets only TERM/INT/HUP/PIPE (`phase7_spawn.c:71-74`).
`posix_spawn` is called with `setsigmask=()` but no `setsigdef` (`phase7.py:433`).
Ignored dispositions survive both execs. CPython ignores SIGXFSZ, and a harness
started as a background job of a non-interactive shell has SIGQUIT (and SIGINT)
ignored. SIGQUIT is now a strict gate (exit 2 and `SIGQUIT: quit`). Go reinstalls
its own handlers, but a Rust candidate's behaviour can depend on the inherited
disposition, so the result would vary with how the harness was launched. If the
previous path used `subprocess` defaults, `restore_signals=True` reset
SIGPIPE/SIGXFSZ, so this is a partial regression. I could not confirm the prior
code because git was unavailable.

**Fix:** in the C child, before `setsid`, set every catchable signal 1..NSIG-1 to
`SIG_DFL` and clear the mask. Optionally also pass `setsigdef` covering all
signals to `posix_spawn`.

### L1 (Low): PDEATHSIG race check is self-referential

`parent = getppid()` is captured *inside* the supervisor and then compared with
`getppid()` again (`phase7_spawn.c:61-62`). If Python died before the first
`getppid()`, the check passes. This mostly self-heals, because the `spawn` report
write gets EPIPE and that kills the CLI. Pass the observer PID as an argument (or
in the env) and compare against it. Also note that PDEATHSIG fires when the
spawning *thread* exits, so callers must spawn from a thread that outlives the
session. Please document this for the bench/collector lane if it uses threads.

### L2 (Low): native IO counters missing; `/proc` IO is sampled only

The contract lists IO, but the `exit` event carries no `ru_inblock`/`ru_oublock`
(`phase7_spawn.c:106-113`). The only IO evidence is the 50 ms `/proc/<pid>/io`
samples. These miss the interval between the last sample and exit, and they exclude
descendants reaped later. **Fix:** add `ru_inblock`/`ru_oublock`. With the H1
gating in place, the supervisor could also read `/proc/<pid>/io` of the zombie
before `wait4`. On the kernels I know, that reflects the final task + signal IO,
including waited-for children, but verify on the target kernel before relying on
it.

### L3 (Low, accounting boundary): waited-for descendants are mixed into CLI rusage

The doc states this. CPU/faults/context switches sum over children the CLI
`wait`s for, and `ru_maxrss` is the *max* across them. The clipboard `xclip`
helper is a full Python interpreter. If Go waits for it and the Rust candidate does
not (or the reverse), CPU and peak RSS differ for reasons unrelated to the CLI. The
collector should flag any session with observed descendants (`children`/
`clipboard_events`) as ineligible for RSS/CPU comparison, or report the
descendant set next to the metrics. No helper change is needed.

### L4 (Low): helper build cache and provenance

- The binary is keyed by source hash only (`phase7.py:407`). Changing the flags, or
  `cc` resolving to a different compiler through `PATH`, reuses a stale binary.
  The doc says `/usr/bin/cc`, but the code uses `shutil.which('cc')`. Fix: key on
  source hash + flags + `realpath(cc)` + `cc --version`, and record the version
  string.
- `peak_rss_bytes` keeps a `darwin` byte-unit branch (`:782`), but the helper is
  Linux-only (`prctl`, `PR_SET_PDEATHSIG`). Drop the branch, or raise a clear
  error on non-Linux, so the code does not imply portability it lacks.
- The observer floor is not yet measured. Acceptance for timing should include one
  recorded floor through this exact path: the same helper exec'ing a trivial static
  binary, reporting `ru_maxrss`, minflt and CPU. Owning that measurement belongs to
  the collector lane.

### L5 (Low): test coverage gaps for the new boundary

The existing tests cover the 64 MiB isolation, session/pgrp/cwd/env/fd3 absence,
exec127 in pipe mode, and reaping after an injected observer exception. Missing:

- **For H1:** the CLI exits while a signal action, timeout or output limit is
  dispatched. Example: a child prints more than `max_output_bytes` and exits
  immediately. Assert a returned result with the failure recorded and no
  `ProcessLookupError`.
- **For M2:** PTY-mode setup failure (e.g. a non-existent cwd), and a real CLI that
  exits 127 being distinguished from exec failure.
- **For M3:** with SIGQUIT and SIGXFSZ set to `SIG_IGN` in the test process, the CLI's
  `/proc/self/status` `SigIgn`/`SigBlk` are zero.
- **For M1:** supervisor killed by SIGKILL mid-session. Assert a raised or recorded
  failure, the CLI group killed, the supervisor reaped, and no leaked fds (compare
  `/proc/self/fd` before and after). Also add a spawn-handshake timeout through a
  stub helper.
- The 64 MiB test runs in pipe mode only and detects only a ≥32 MiB leak. Add a PTY
  variant. Consider asserting `peak_rss_bytes` below a small absolute bound derived
  from a trivial-exec floor run in the same test.

## Items checked and not flagged

- **Handshake.** `ready[1]` is CLOEXEC and closed by the parent before `read`. EOF
  arrives on exec or on child death, and the EINTR loop is correct. A signal
  between `fork` and `cli_pid = pid` is covered by the `if (stopped)` recheck. A
  pre-`setsid` `kill(-pid)` safely returns ESRCH.
- **FD hygiene.** fd3 is dup2'd (which clears CLOEXEC) and closed in the CLI. The
  supervisor closes 0/1/2 after fork, so it never holds the PTY slave or the stderr
  pipe. Python's other fds are non-inheritable (PEP 446) and are also closed
  through spawn actions. Theoretical edge case: a Python started with fds 0-2
  closed could get `write_fd` ≤ 2, which the dup2 order would clobber. Low
  relevance.
- **Session/ctty.** `setsid` succeeds because the forked child is not a group
  leader. glibc `openpty` opens the slave `O_NOCTTY`, so `TIOCSCTTY(0)` on a fresh
  PTY is valid. The CLI is the session leader, so its PGID is fixed and
  `killpg(pid)` covers its group. The CLI's group is orphaned (its parent is in
  another session), so tty-generated TSTP/TTIN/TTOU are discarded. As far as I can
  tell this matches a `start_new_session=True` launch and is not a regression.
- **Protocol parsing.** Bounded at 4 KiB; ordering is strict (`spawn` then `exit`
  with a matching pid). EOF without `exit` raises instead of inventing values,
  which is consistent with "no fake measurements".
- **No shell and no fallback rusage path.** Python never forks the measured
  process.
- **Signal semantics.** Raw `status` preserves signal vs numeric exit and the core
  flag. Note that `signal` actions use `killpg`, which is group-wide and
  pre-existing. The doc's "signals … at its actual PID" is satisfied only because
  the CLI is the group leader. Clipboard helpers in the group also receive the
  signal. This is unchanged behaviour, but the wording should say "process group
  led by the actual PID".

## Acceptance criteria for r2

1. H1 is fixed: the reap is gated behind a Python release, and the exit stamp
   comes from `WNOWAIT`. A regression test covers the exit-during-dispatch races.
2. M1: `execute()` unwinds all fds and processes on every raise path, including
   pre-`try` acquisition and supervisor death. There is a test with an fd-count
   check.
3. M2: a distinct `exec_error` event, with tests in PTY and pipe modes plus the
   real-127 discrimination.
4. M3: all dispositions are defaulted in the CLI, with a SigIgn/SigBlk test.
5. L2 IO fields added (or explicitly waived), L4 cache key and provenance
   tightened, and the doc wording corrected (process group signal delivery;
   Linux-only).
6. The owner reruns the owned suite and reports the count and log path. Still no
   timing or floor numbers are claimed until the collector lane records an
   observer floor through this helper.
