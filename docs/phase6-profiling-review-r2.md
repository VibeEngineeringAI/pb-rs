# Phase6 profiling adapter — independent review r2

Reviewer: a fresh Opus 5.5 review sub-agent. No author or R1-reviewer thread was reused.

Scope:

- The v2 adapter `harness/phase6_profiles.py`, its tests and `docs/phase6-profiling-plan.md`.
- The v2 sidecar, plans and logs in `profile-adapter-v2`.
- The reused APIs in `phase6_workloads.py`, `profile_external.py`, `run.py`, `manifest.py` and `meter.c`.
- The preserved R1 review and root's R1 reproduction evidence.

This is not whole-phase acceptance, and it does not accept interpretive profiles.

## Execution and identity (read first)

**Shell.** Claude Code's own temp directory was EDQUOT. Each command's output was redirected
under `/home/ajs/.cache/pb-phase6/profile-review-r2/` and read from there. Commands did run;
only the tool's echo was lost. No `/tmp` manipulation, symlink or harness relocation was done.

**What I executed** (guarded, pure mock; runner `review_r2.py` `457f3b25…`):

- **Guards before import.** Before any reviewed module is imported, the runner hard-fails these
  entry points:
  - `subprocess.*` and `_posixsubprocess.fork_exec`;
  - `os.system/popen/fork/forkpty/posix_spawn[p]/exec*/spawn*/kill/killpg`;
  - `signal.pthread_kill/raise_signal/pidfd_send_signal`;
  - `fcntl.flock/lockf`, and the lock commands of `fcntl.fcntl`;
  - socket connect/bind/listen/sendto/sendmsg and `create_connection/create_server`;
  - `resource.prlimit`.

  `resource.setrlimit` is recorded and never applied. **Guard trips: 0.**
- **The author's unmodified suite:** **23/23 OK** (`tests-r2.log` `5eaa0b59…`). I ran it with
  `python3 -I -W error::ResourceWarning`, `TMPDIR` under the cache, and no bytecode writes.
- **Seven mutation checks (M1–M7):** each reverts one R1 fix in memory and re-runs the author's
  matching test. **All 7 were detected** (`mutations-r2.log` `9425ced5…`).
- **Sixteen independent causal probes:** these are my assertions. They reuse only the author's
  mock-provenance builder. **16/16 reproduce the behaviour they assert** (`probes-r2.log`
  `29c8155d…`; all values in `results-r2.json` `cb9cb3a7…`).
- **Leftovers:** none. There are no leftover `p6-profiles-test-*` folders and no new `__pycache__`
  in `harness/`.

**Not run:**

- `test_profile_external.py`.
- Any native helper, cargo/go, build, readelf or `--version`.
- perf, strace or heaptrack.
- Any product, fixture or meter.
- Any pressure, network, real lock, token or timing work.

Statements below about real tool formats, exit codes, stderr or speed are from source, not from
observation.

**Before and after hashes.** I took `sha256sum` of 30 reviewed, frozen and preserved files before
and after both runs, and they are byte-identical (`hashes-before.txt` = `hashes-after.txt`). All
of them match the brief:

| File | SHA256 |
|---|---|
| `docs/phase6-profiling-review-r1.md` | `0417965c8b1645705977b1c388644fa6d2d608464f36749b5d35f570f989f6b6` |
| `harness/phase6_profiles.py` | `ed9e36d86af944cf9bf018a90068efa0f674ff0b94a658a968458d397e456dc8` |
| `harness/test_phase6_profiles.py` | `f0dbb9f027c9ff08056c07f2abe30a6fc51984662dec001e6b1c1ca93a34f1e2` |
| `docs/phase6-profiling-plan.md` | `29f936a20270b64ca1e809e738e5b1bd22ff935aad979073e266ab8e028172d4` |
| `profile-adapter-v2/profile-sidecar.json` | `2392786ca4d7aa7450e0573bc4ac50ae6eb476787116d41e55999aecb6bfcc00` |
| `campaign-prepared-r6-v1/manifest.json` | `a47093f4a3ea785a310f7ca1baa92444deaf7e21db5ba7db72dc897ddc3d047b` |
| `harness/phase6_workloads.py` (collector) | `e2e83fa118c58a96ff55700be88f95f9b8b6479a289a8c59f447f836389fde51` |
| `harness/profile_external.py` | `a0f0d7a315772ed933eae235b0c55850ce8b23da25132c6ccbafc9a4e0256a3c` |
| `profile-review-r1/repro_r1.py` / `parent-repro.log` | `df4e3a59…62d23b` / `1cf4fc51…fd184` (preserved) |

The sidecar binds all eight harness modules at the hashes on disk. That includes
`phase6_profiles` `ed9e36d8…`, `run` `b280d129…`, `manifest` `f65de9a0…`, `diagnose` `169de15b…`,
`cases` `b892e1b5…` and `phase3_workloads` `4753b1d9…`. Its evidence statuses are:

- `pb-symbolized-review-r6.json`: current.
- `go-symbolized-prepared.json`: current.
- `profile-symbols-verification.json`: `historical_not_certifying`.

## Verdict by gate

**Tiny functional adapter admission** means a separately authorized probe on `one-row`, with
one repetition per mode, to learn the real perf/strace/heaptrack exit status, stderr and
`--stats` format.

- **One required correction: N1.** The full provenance hash of about 0.43 GB runs before the
  nonblocking lock. It is cheap to fix, and it belongs to the same class as build-cost R1 N2.
- **Alternative to fixing N1.** Root may instead rule an explicit operational precondition: no
  other lock holder can exist when the probe starts.
- **Everything else** is either fail-closed or bounded by the small artifacts of `one-row`.
- **Treat every functional output as functional only.** Analysis "passed" is not an
  interpretive result (N5).

**Interpretive profiles.** Blocked until:

- N1 and N3 are fixed (N3: in-window hashing is not deadline-bounded).
- N4 is fixed (analysis code identity).
- N5 is fixed or explicitly ruled (exit semantics).
- The real perf `--stats` format, profiler exit status and stderr contract are confirmed by the
  functional probes.

The R1 blockers F1 and F2 are closed in code (see below).

## R1 dispositions (F1–F15)

| ID | Disposition | Basis |
|---|---|---|
| F1 | **Closed** | `evaluate` applies global gates (`:459-508`), and `analyze_command` independently checks report `passed`, global gate, end check and quarantine (`:917-921`). **Probe F1:** a collector exception and a window stop each give analysis exit 1, all entries skipped and no tool call. **Independent analysis gate:** with `evaluate` mutated to pass a quarantined report, analysis still refuses (`end identity check failed`, `quarantined`). **M4** is detected. |
| F2 | **Closed in code; host format unverified** | Counts come only from the single `Aggregated stats:` block. A percent suffix is optional. A missing, duplicate, negative or unrecognized row is unknown, and so is a second aggregated block. Per-event and BPF rows are ignored. Stats run with `--no-skip-empty`. **Probe F2:** 9 edge cases behave as listed. **M1** (missing → 0) is detected. The fixtures are derived from perf source, and the perf source I recall (`events_stats__fprintf`, `"%20s events: %10d  (%4.1f%%)"`, header `Aggregated stats:%s`) agrees. Still, nothing was observed on this host. Residual: N5. |
| F3 | **Closed** | An invalid control stops at once (`:627-631`). Before any capture, the gate requires all planned controls to be valid, error-free and comparable (`:445-456`, `:581-586`). **Probe F3:** an invalid second control means 2 children and no capture. A control missing `requests` means 4 controls and no capture. **M3** is detected. |
| F4 | **Closed for capture; residual N4** | `verify_harness` requires the eight-module set exactly, and import = now = sidecar by path and hash (`:195-209`). `prepare` checks the parent runners. **M5** is detected. Residuals: analysis neither verifies nor records its own code (N4). An extra harness module imported lazily after `verify` is not rechecked (probe F4-lazy); `run.py:657` is not reachable from `p6.run_sample`, so this is hardening only. |
| F5 | **Closed** | The floor applies to each capture (`:807-813`). **Probe F5:** three captures of 400 per role give a role total of 1200 (descriptive), every capture `insufficient_samples`, and nothing interpretable. **M7** is detected. |
| F6 | **Closed as specified; residual N5** | `accounting` and `roles` cover every planned item. Analysis has one entry per planned capture and a row per role, and an empty set or one with skips exits 1. Residual: zero *interpretable* captures still gives exit 0 and `passed: true` (N5). |
| F7 | **Closed** | A traced FD 1 mismatch is an entry `error`, and `passed` requires no errors (`:853-856`, `:902-903`). The author's test covers this. |
| F8 | **Closed after the lock** | **Probe F8:** every capture rehash, evaluation and the final `report.json` write happen inside the lock, with no event after the lock is released. Analysis rehashes and its final write are also inside the lock. The mirror-image defect *before* the lock is still open (N1). |
| F9 | **Closed** | SIGINT, SIGTERM and SIGHUP are owned even when inherited as ignored, and the previous handlers are restored. **Probe F9:** SIGINT during the final rehash leaves a saved report inside the lock with `passed:false`, `failure: finalization KeyboardInterrupt`, and SIG_IGN restored. **M2** is detected. |
| F10 | **Partial** | The caps are correct; see "F10 detail" below. The bounded-work claim is not met: in-window hashing has no deadline (N3), analysis has no overrun record (N6), the strace cap scope is a pitfall (N8), and the limit is soft (N9). |
| F11 | **Closed; residuals disclosed or N4** | The same-byte v2 report and its schema are checked. All identities are rechecked at start and end inside the lock. The environment is controlled (PATH, HOME/XDG, `LANG=C`, `DEBUGINFOD_URLS=''`), and analysis output is never overwritten. Still open: a swap-and-restore of an input *during* a tool run cannot be seen by path rehashing. That is acceptable but not stated in the plan, so it should be added to the interpretation limits. |
| F12 | **Closed** | The sidecar marks the R4 file `historical_not_certifying`. |
| F13 | **Closed** | The probe record is appended before the run, filled with return code, timeout, error and stdout/stderr identities, and saved by `finalize` inside the lock. The author's test reads the report from disk, for both an rc 2 failure and a timeout. |
| F14 | **Closed by disclosure** | `UNBOUND` appears in every report, and `perf_event_paranoid` is re-read inside the lock. |
| F15 | **Mostly closed; residual N11** | The guards run at test-module import, before reviewed code. M1–M7 show the tests are causal. A few entry points are still unguarded (N11). |

### F10 detail (caps, descendants, budgets)

- **RLIMIT ordering.** Probe F10-rlimit used simulated `setrlimit`. `RLIMIT_FSIZE` is set
  before each child as follows:
  - 1 GiB for controls and 2 GiB for perf captures;
  - 256 MiB before each probe;
  - 4 GiB before each analysis tool.

  `RLIMIT_CORE` is set to 0, and the previous limits are restored afterwards.
- **Reach to real descendants (source).** `file_size_limit` wraps all of `sample()`, so the
  `Fixture` is spawned inside the cap (`Popen`, `start_new_session`, default `restore_signals`).
  The meter is spawned by `run.spawn_measured`, using `posix_spawn(setsid=True,
  setsigdef=(SIGPIPE, SIGXFZ, SIGXFSZ))`. rlimits are inherited across spawn/fork/exec, so the
  cap reaches meter → launcher shell → profiler → CLI. `meter.c` contains no
  `setrlimit`/`RLIMIT`. Probes and analysis tools use `subprocess.run`, which restores
  SIGXFSZ.
- **Python under the cap.** Python ignores SIGXFSZ, so the collector gets `EFBIG`. That goes to
  `collect` → `kill_group` → collector exception → window abort, which fails closed.
- **Not executed.** None of this was run natively.
- **Budget arithmetic reproduced.** I rebuilt all six v2 plans from the plan files' tracked
  sizes (348,090,831 B) and the binary sizes, and every plan matches:
  - perf × 3 scenarios: 2 + 390 + (4×211 + 6×221) + 94 = **2656 s**;
  - perf `large`: 2 + 390 + 1570 + 94 = **2056 s**;
  - strace-writes: 2 + 390 + 1272 + 37 = **1701 s**;
  - heaptrack: 2 + 260 + 1085 + 63 = **1410 s**.
- **What the plans are.** They are planning arithmetic only. There is no functional evidence for
  any custom scenario, profiler format, exit status or stderr.

**On root's unresolved F10 question: accepted OS limits versus avoidable unbounded work.**

- **Accepted, not avoidable here:**
  - one `readinto`/`read` syscall, and its page faults;
  - one zlib chunk;
  - each child process, which is bounded by the frozen collector timeout plus SIGKILL of its
    group;
  - each `perf report` or heaptrack step, which is bounded by its 180 s `subprocess` timeout;
  - the frozen collector's post-capture validation, inside the parent's 30 s reserve.
- **Avoidable in the adapter, with no frozen edit:**
  - The plan says "hashing, gzip and Python parsing cannot be preempted". That is inaccurate.
    `hashlib.file_digest` (Python 3.14.7) and `shutil.copyfileobj` are Python loops over
    256 KiB and 64 KiB chunks.
  - So per-chunk deadline checks are trivially possible. A whole-window overrun from hashing a
    2 GiB artifact, or about 13 GB in the final rehash, is a design choice, not an OS limit (N3).
  - The heaptrack gzip lives inside the frozen `profile_external.analyze`. It runs outside any
    quiet window, but it holds the common lock, so it should be *recorded* (N6).

## New findings (most severe first)

### N1 — MEDIUM-HIGH (confirmed) · required before any native run, including tiny functional probes
**The full provenance hash, about 0.43 GB, runs before the nonblocking lock.**

- **Source.** `capture_command` calls `preflight` → `verify` (`:658`, `:212-249`), then
  `new_output` and the first report write (`:660`, `:666`). Only after that does it try
  `quiet_lock` (`:673`).
- **What `verify` hashes.** Every tracked identity. Probe N1 counts hashing calls, and shows the
  release binaries are hashed twice before the lock (`run.binary_info` and `declared_identity`).
- **Real sizes.** 348,090,831 B tracked, plus second passes of `pb-go` (71.8 MB) and `pb`
  (10.7 MB), comes to **≈0.43 GB of reads plus SHA-256**. The tracked set includes the two
  symbolized binaries (109 MB and 99 MB).
- **When the lock is busy, which is when this matters:**
  - Probe N1, with a lock that raises `BlockingIOError`: all four executables and all 41
    tracked identities are read and hashed before the lock attempt. The output folder and
    `report.json` are left behind, and the exit code is 1.
  - So a capture started by mistake during another campaign's quiet window performs this I/O
    and CPU work inside that window before it fails.
- **Same class as build-cost R1 N2** (rated High there, at gigabyte scale).
- **Correction:**
  1. In `capture_command`, do only the cheap work before the lock: argument checks, the
     sidecar/parent read-with-identity (a few KB), output-path syntax and the token presence
     check.
  2. Then enter `interrupt_signals()` and `quiet_lock`, and start `WindowBudget`.
  3. Then run `preflight`/`verify` *inside* the lock. That pass becomes the entry rehash, so
     drop the duplicate at `:568`.
  4. Then check `fits` and create the output folder. If it does not fit, release and launch
     nothing.
- **For `plan`/`prepare`.** Either make them refuse when the lock is held (`LOCK_NB` attempt and
  immediate release, with no token), or state "outside any window" in the plan.

### N2 — LOW-MEDIUM (confirmed) · fold into N1
**The window deadline starts at lock acquisition, so pre-lock time is not charged.**

- `WindowBudget(args.window_budget_s)` is created after the lock (`:675`).
- **Probe N2:** with 2500 s of simulated pre-lock work and `--window-budget-s 2000`, the process
  ran for 2500 s, yet the report says `window_overrun:false, passed:true`.
- The real magnitude is the N1 hashing, about 2 s at the plan's rate. The cost is that the
  report is not truthful about the time the coordinator's window was actually consumed.
- **Correction:** start the budget clock before any work, at process start or right after the
  lock with all verification after it (as in N1). Record the process start in the report.

### N3 — MEDIUM (confirmed) · before interpretive (multi-GiB) captures; not needed for `one-row` functional probes
**In-window hashing has no deadline. An overrun is recorded after the fact, not prevented.**

- **Where the hashing happens:**
  - After each capture, `profiles.artifacts` → `manifest.file_identity` hashes an artifact of
    up to 2 GiB (perf/heaptrack) (`:619`).
  - `finalize` rehashes tracked + launchers + artifacts (`:641-648`). For the perf plans that is
    ≤ 348 MB + 6 × 2 GiB ≈ **13.2 GB**, and nothing checks the deadline.
- **Probe F10-end-rehash.** Each final hash was made to take a simulated 400 s:
  - 49 final hash calls ran, and **40 of them started after the deadline**.
  - Only then is `window_overrun:true` recorded, and the report fails.
  - This fails closed, but the adapter keeps holding the lock and the coordinator's window past
    the negotiated end. The planning rate (200 MiB/s) is an unmeasured assumption.
- **Correction (adapter-local; no frozen edit):**
  1. Add `bounded_file_info(path, deadline_ns, reserve_s)`. It reads 1 MiB chunks with
     `readinto` and checks `p6.clock_ns()` before each chunk.
  2. On expiry it raises `DeadlineExceeded`.
  3. Use it for the per-capture artifact identity: replicate the 4-line `artifacts` uniqueness,
     regular-file and non-empty checks, then hash with the bounded helper. Use it also for
     `finalize`, the entry rehash and the per-launch executable rehash.
  4. In `finalize`, catch `DeadlineExceeded`. Set `end_identity_check: {performed:true,
     passed:false, incomplete:'deadline'}` and `quarantined:true`, then evaluate and write
     (small).
  5. The worst-case overrun then becomes one chunk plus the report write.
  6. Optionally lower the perf/heaptrack artifact cap, because the cap directly sets the
     reserved hash work.
- Also correct the plan's claim that hashing and gzip "cannot be preempted".

### N4 — MEDIUM-LOW (confirmed) · before interpretive profiles
**Analysis neither verifies nor records the code that performs it.**

- `analyze_command` (`:906-987`) never calls `verify_harness`, and `analysis.json` has no
  harness field.
- **Probe F4-analysis.** I patched `IMPORTED['phase6_profiles']` to a different SHA and loaded
  an extra harness module. Then:
  - capture-side `verify` raises `ValueError`;
  - but analysis of a passed report exits 0 with `passed:true`, and records no code identity.
- **Why it matters.** The stack/loss interpretation is computed by `parse_perf_stats`,
  `capture_stack_status` and `finish_analysis`. An analysis run from a different checkout
  passes, because the report's tracked harness paths are unchanged.
- **Correction:**
  1. At analysis start, inside the lock, require `loaded_harness()` to be exactly
     `HARNESS_MODULES`.
  2. Require each module's `IMPORTED` and current path and SHA to equal the report's
     `provenance.harness` entries.
  3. Record them as `analysis_harness`.
  4. Also re-run the loaded-set check in `finalize` (this covers the lazy-import hardening).

### N5 — LOW-MEDIUM (confirmed) · fix or rule before interpretive profiles
**Analysis exits 0 with `passed:true` even when no capture is interpretable.**

- `passed` means "every planned capture was analyzed without error" (`:902-903`, `:987`).
- **Probe F6-residual.** If every capture's stats lack `LOST` rows (unknown loss), all 4
  entries are `unavailable` and none is interpretable, yet the exit code is 0 and
  `passed:true`.
- The same holds when all captures are `insufficient_samples`. The author's test asserts this
  on purpose.
- The per-entry flags are correct, but a coordinator that gates on the exit code would treat
  this as success.
- **Correction:**
  - Rename the field to `analysis_complete`.
  - Add `interpretable_captures` per role and in total.
  - Return a distinct non-zero code (for example 3) when the total is 0.
  - Or rule explicitly that only the per-entry `interpretable` flag may be cited.

### N6 — LOW (confirmed)
**Analysis records no elapsed time or overrun, and its gzip rate is an assumption.**

- **Probe F10-analysis.** A simulated 10⁶ s jump during the last tool call still gives
  `passed:true`, with no elapsed or overrun field.
- The budget is checked only before each capture (`:961-963`).
- The heaptrack gzip in `profile_external.analyze` uses `gzip.open` at the default
  `compresslevel=9`. Its 25 MiB/s planning rate (`:52`) is unmeasured for level 9 on a text
  intermediate of up to 4 GiB.
- **Correction:**
  - Record start, elapsed and `overrun` in `analysis.json`, and fail `passed`/`analysis_complete`
    on overrun.
  - Either derive the gzip rate from a future authorized measurement, or lower the heaptrack
    `ANALYSIS_FILE_BYTES` budget basis.

### N7 — LOW (source)
**Analysis writes into the sealed capture folders, and a partial analysis blocks re-analysis.**

- `analyze_one` writes to `report_path.parent / entry['directory']` (`:966`) and refuses if any
  output already exists (`:844`).
- **Consequence.** If, for example, the first functional probe shows perf rejecting
  `--no-skip-empty`, then `analysis.txt` and the stats files remain. Re-analysis of that capture
  is then impossible without hand cleanup, and the capture evidence folder has been changed
  after it was sealed (the artifact identities are unaffected).
- **Correction:** pass a per-analysis directory to `profiles.analyze`/`perf_stats`, such as
  `<out>.files/<entry directory>`.

### N8 — LOW (source)
**In plain-`strace` mode, the 16 MiB cap applies to every file in the sample phase.**

- `file_size_limit(cap)` wraps the whole `sample()`, not only the profiler artifact (`:615-616`).
- In `strace` mode the cap (16 MiB, `:44`) therefore also limits:
  - the fixture's `fixture.events.jsonl` (`event_limit` 20000; its byte size is not derived);
  - the collector's `visibility.jsonl`, whose retention cap is exactly 16 MiB
    (`VISIBILITY_LOG_BYTES`), equal to this limit.
- Python and Go writes that exceed it abort the window, which fails closed.
- No v2 plan uses plain `strace`.
- **Correction:** set each capture's cap to at least the collector and fixture maxima (for
  example `max(artifact cap, 64 MiB)`), and keep the artifact-cap check on the artifact. Or
  disclose the limitation.

### N9 — LOW (disclosure)
**The cap is a soft limit.**

- `file_size_limit` lowers only the soft `RLIMIT_FSIZE`, and leaves the hard limit (usually
  unlimited) (`:318-322`).
- Any descendant could raise it again. None of perf, strace, the heaptrack preload, meter or
  fixture is known to do so, but this was not verified.
- **Correction:** state this in the plan's limits. Optionally lower the hard limit as well
  inside a short-lived boundary (a child-only `preexec` is not available on the `posix_spawn`
  path, so disclosure is the proportionate fix).

### N10 — LOW
**The analysis tool versions for heaptrack are never probed or recorded.**

- Heaptrack capture probes cover only `readelf` and `env` (`CAPTURE_TOOLS`).
- Analysis records only `perf_version` (`:924`).
- `heaptrack_print` and `heaptrack_interpret` are bound by hash only.
- **Correction:** probe `heaptrack_print --version` inside the capture window (the interpreter
  has no version flag, so its hash suffices), and copy the version into the analysis.

### N11 — LOW (tests)
**Residual guard gaps in the author's test module (F15).**

- The following are not guarded:
  - `_posixsubprocess.fork_exec`;
  - `signal.pthread_kill/raise_signal/pidfd_send_signal`;
  - socket `bind/sendto/sendmsg`;
  - `fcntl.fcntl` lock commands;
  - `resource.prlimit`.
- My runner guarded all of them, and none tripped.
- **Correction:** add them to the module guard list.

### N12 — TRIVIAL (doc accuracy)
- The plan says repro I "now fails". `repro-r1-post-fix.log` shows `ERROR`, because `verify`
  raises. The conclusion is the same, but the plan should say error.
- The plan's "cannot be preempted" wording: see N3.

## Hypotheses checked and refuted or closed

- **Capture after cancellation or failure: closed.**
  - A signal or collector exception leaves the earlier samples and the interrupted entry's
    artifacts saved (probe preservation: 6 samples, artifact kept, `KeyboardInterrupt` recorded
    in the entry).
  - `finalize` runs inside the lock.
  - Fixture and meter groups are reaped by the frozen `run_sample` / `collect`.
  - Probe children are killed by `subprocess.run` on timeout or interrupt. They are non-forking
    tools.
- **A perf child orphaned by profiler SIGXFSZ (source): bounded.**
  - The CLI stays in the meter's session and keeps the stdout pipe open.
  - `collect` therefore drains until the finite fixture stream ends, or until the timeout
    triggers `kill_group(SIGKILL)`.
- **Same-format identities: closed.** `manifest.file_identity` and `p6.file_info` produce the
  same `{path, sha256, size_bytes}`, so artifact equality checks across the two are valid.
- **Contract coverage of controls: closed (inherited).** The comparison covers:
  - stdout SHA and bytes, rows, batches;
  - stderr SHA and `termination == {'exit': 1}`;
  - requests, ticket and request validation;
  - workload SHA, sink, TLS, and config before and after.

  Authorization and ticket bytes are checked against fixture events. Profiler stderr or a
  different exit status makes a capture non-interpretable.
- **Heaptrack is Rust-only, and the scenarios are separately named: closed.**
  - The schedule and probes skip Go under heaptrack.
  - The three profile-only scenarios have distinct names, identities and 180 s timeouts.
- **Atomic failure reports: closed.**
  - Lock busy or not fitting: a small report is written outside the lock and nothing is
    launched.
  - A second signal during finalization leaves the last atomic report on disk, which is
    `complete:false`.

## Unresolved objections

| ID | Item | Gate |
|---|---|---|
| N1 | Full hash (≈0.43 GB) and output creation before the nonblocking lock | **before any native run** (or explicit root operational ruling) |
| N3 | In-window artifact/end hashing not deadline-bounded (overrun recorded, not prevented) | interpretive (multi-GiB) |
| N4 | Analysis code identity neither verified nor recorded | interpretive |
| N5 | Analysis exit 0 / `passed` with zero interpretable captures | interpretive (fix or rule) |
| N2, N6–N12 | As above | before final profile evidence |
| F2 host | Real `perf report --stats --no-skip-empty` format, `perf record -q` exit status and stderr, strace/heaptrack exit and stderr, `kptr_restrict` warnings | tiny functional probes |
| — | perf artifact size at 999 Hz with `dwarf,16384` (about 16 KiB per sample, so 2 GiB ≈ 128 on-CPU thread-seconds) | functional probe; the cap fails closed |
| — | Go source-to-commit equality rests on the Phase 7 attestation | attested, not verified here |
