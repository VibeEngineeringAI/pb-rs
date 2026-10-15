# Phase6 profiling adapter — independent review r3

Reviewer: a fresh Opus 5.5 review sub-agent. No author, R1 or R2 thread was reused.

**Scope:**

- the v3 adapter `harness/phase6_profiles.py`, its tests and `docs/phase6-profiling-plan.md`;
- the v3 sidecar, plans and logs in `profile-adapter-v3`;
- the reused frozen APIs in `phase6_workloads.py`, `profile_external.py`, `run.py` and `manifest.py`;
- reviews R1 and R2, read in full.

This is not whole-phase acceptance. It does not accept the source, any measurement or any interpretive profile.

## Execution and identity (read first)

**Shell.** Claude Code's own temp directory was EDQUOT. Each command redirected its output into
`/home/ajs/.cache/pb-phase6/profile-review-r3/`, and I read it back from there. The commands did run;
only the tool's echo was lost. I did not touch `/tmp`.

**Runner.** `review_r3.py` (`bd8d6bff…`). It installs guards before any reviewed module is imported.
These hard-fail and are counted:

- `subprocess.*`, **including `subprocess._fork_exec`**, and `_posixsubprocess.fork_exec`;
- `os.*` **and `posix.*`**: system/popen/fork/forkpty/posix_spawn[p]/exec*/spawn*/fexecve/kill/killpg;
- `signal.pthread_kill/raise_signal/pidfd_send_signal`;
- `fcntl.flock/lockf`, and the lock commands of `fcntl.fcntl`;
- socket connect/connect_ex/bind/listen/send/sendall/sendto/sendmsg, and `create_connection/create_server`;
- `resource.prlimit`;
- any open of the real common lock file `/home/ajs/.cache/pb-rs-performance.lock`;
- any write, mkdir, chmod, unlink or rename under the worktree or `/tmp`.

`resource.setrlimit` is recorded and never applied. Runs used `python3 -I -B -W error::ResourceWarning` with
`TMPDIR`, `GOTMPDIR` and `CLAUDE_CODE_TMPDIR` set to `/home/ajs/.cache/pb-phase6/tmp`.

**Results.**

| Run | Result | Log |
|---|---|---|
| Author's unmodified suite | **34/34 OK**, 0 guard trips | `tests-final.log` `cc2cdb4a…` |
| R3's own probes (independent assertions; only the author's mock-provenance builder is reused) | **21/21 OK**, 0 trips | `probes-final.log` `7c6a9fd3…` |
| 23 in-memory source mutants, each run against both suites in its own guarded process | author suite detects **16/23**; R3 probes detect **20/23**; together **23/23**; 0 trips | `final-summary.txt` `d5184c55…`, `mutants/` |
| Guard alias scan | see R3-8 | `scan-final.log` `f1c8eaae…` |

Mutated source lived only in memory: it was compiled with the original path as filename and never written.

**Preserved failed attempts:**

- `probes-1.log` (`4bd74db4…`) had 4 failures. The cause was in my harness: `self.args()` hashed the parent and
  sidecar while my recorder was on. I moved argument construction outside the recording and kept the old runner
  as `review_r3.attempt1.py` (`95385e79…`).
- `probes-2.log` and `probes-3.log` are intermediate runs.
- `mutants-pass1/` holds the first mutant matrix, run with the 19-probe runner.

**Not run:**

- `test_profile_external.py`.
- Any native helper, readelf, `--version`, perf, strace, heaptrack, build, CLI, fixture or meter.
- Any pressure, network, real lock, token or timing work.

All statements about real tool formats, exit codes, stderr or speed come from source, not observation.

**Before and after.** `hashset.sh` covers:

- 33 reviewed, frozen and preserved files;
- the `harness/__pycache__` listing with mtimes;
- the lock file's mtime;
- `p6-profiles-test-*` leftovers;
- `git status`.

`hashes-before.txt` equals `hashes-after.txt` (`36e306ca…`). Every identity in the brief matches:

| File | SHA256 |
|---|---|
| `harness/phase6_profiles.py` | `a45555df7a93bf57f418c206c3c5c7eb73e34f23070f0e7d91cf475b011b5cae` |
| `harness/test_phase6_profiles.py` | `945647aca5b75bec46af7fc23300bfb575aa7c8feff35e161661d40ebe1c952d` |
| `docs/phase6-profiling-plan.md` | `b51d5d7376b9100b1f9ca9c97417233e1da0c1702188738a2e2d0f94b56f1aaf` |
| `profile-adapter-v3/profile-sidecar.json` | `bedc788740c258c1eb49310c252c9dd1e9627bd92bd78288d5220271db89d57a` |
| `campaign-prepared-r6-v1/manifest.json` | `a47093f4a3ea785a310f7ca1baa92444deaf7e21db5ba7db72dc897ddc3d047b` |
| collector `phase6_workloads.py` / `profile_external.py` | `e2e83fa1…fde51` / `a0f0d7a3…6a3c` |
| R1 / R2 reviews | `0417965c…` / `6018d16d…` |

I also checked the sidecar and the six plans:

- The sidecar's eight harness hashes all equal the files on disk.
- Its evidence statuses are: R6 symbolized evidence `current_supporting`, and the R4 file `historical_not_certifying`.
- All six plan files have `launched:false`, and their harness entries equal the files on disk.
- Plan hashes match the plan doc. **Exception:** `prepare-plan.log` (see R3-7).

## Verdict by gate

**Tiny functional adapter probe.** This means a separately authorized and quietly coordinated run on `one-row`,
one repetition per mode, whose outputs are treated as functional only.

- **No code blocker in v3.** R2's blocker N1 is closed: nothing reads, hashes or writes before the lock. So are
  N2 and N3: every owned hash before launches and at the end is deadline-checked per chunk.
- **New findings R3-1…R3-9 are all LOW or below.** None affects the safety of a genuine one-row capture: lock
  order, deadlines, caps, cleanup and failure capture.
- **If root keeps R2's "all corrections first" policy**, the cheap ones are R3-2 (tests), R3-1 (directory
  validation), R3-3 (failure evidence), R3-8 (guards) and R3-7 (doc). Each has a minimal correction below.

**Interpretive profiles.** Still blocked until:

1. the functional probes establish the real perf `--stats` format, the exit status and stderr of `perf record -q`,
   strace and heaptrack, whether `heaptrack_print` accepts `--version`, and perf artifact size and sample adequacy
   at 999 Hz `dwarf,16384`;
2. R3-4 is fixed or ruled (non-perf modes have no adequacy criterion);
3. R3-5 is fixed or ruled (analysis budget sizing);
4. R3-2 is fixed (regression tests for the N3/N4/N6 claims that no author test protects).

## R2 dispositions (N1–N12)

| ID | Disposition | Evidence (R3's own probes unless marked) |
|---|---|---|
| N1 | **Closed** | **Capture with a busy lock:** the full event trace (every `open`, `os.open`, `hashlib.file_digest`, `hashlib.sha256`, bounded chunk) is exactly `['lock-attempt']`, exit 2, no output, and stdout says `lock_acquired:false`. **Provenance refused inside the lock** (wrong parent SHA): exit 2, no output folder, the first event is `lock-enter` and the last is `lock-exit`, with all 28 reads between them. **`plan` and `prepare` with a busy lock:** each trace is exactly `['lock-attempt']`. `plan` exits 2. `prepare` raises `BlockingIOError` (traceback, exit 1) and creates no sidecar. **Before the lock**, only import-time `IMPORTED` hashing runs: 8 modules, 273,273 B. `main`'s `scenarios()` is pure computation (`phase6_workloads.py:400-442`). Mutants X01 and X02 are detected by both suites. |
| N2 | **Closed** | The clock jumps inside the first JSON read of verification. **Jump of 1980 s with a 2000 s budget:** exit 2, `DeadlineExceeded`, no output, and no chunk starts at or after `deadline − 30 s`. **Jump of 1800 s:** exit 1, `verified_s` 1800.008, and "needs 1709s but 200.0s remain; no child started". X03 is detected by both suites. |
| N3 | **Closed in code; residual R3-6** | See the N3 detail below. |
| N4 | **Closed** | A report whose `provenance.harness.run` differs from the running code gives exit 1, "harness module run differs", **0 analysis tool calls** and `analysis_harness_end.passed:false`. X11 and X22 are author-detected. **X10** (bind analysis to `IMPORTED` instead of the report) is **missed by the author suite** and caught only by R3 (R3-2). |
| N5 | **Closed for perf; residual R3-4** | perf: all lost → 3; Go lost and Rust sufficient → 4; all sufficient → 0; a failing role → 1. X12 is detected by both suites. |
| N6 | **Closed in code; residuals R3-2, R3-3, R3-5** | **Tool timeout:** clipped from 180 to 49.9998 s when 50 s remain before the work deadline. **Expiry during a tool:** exit 1, and the end check is still performed inside the lock. **gzip:** 3 reads, none at or after the deadline, the plain intermediate removed, and a partial `interpreted.gz` left in the analysis tree. **Write trace:** the deadline is checked per record (101 clock checks for deadline +100.5 s). **Overrun:** recorded (author test; X14). X13, X20 and X21 are **missed by the author suite** (R3-2). |
| N7 | **Closed for genuine reports; residual R3-1** | perf and heaptrack analyses leave the capture folders byte-identical (strace-writes is author-tested). An existing `--out` is refused with exit 2 (author). X15 is detected by both suites. |
| N8 | **Closed** | Recorded caps by mode. Controls: 1 GiB. Captures: perf and heaptrack 2 GiB; strace and strace-writes 1 GiB, since `max(16 MiB` or `512 MiB, 1 GiB)`. Probes: 256 MiB. Each limit is restored afterwards. X16 is detected by both suites. |
| N9 | **Closed (disclosure)** | "RLIMIT_FSIZE is a soft limit…" is in `UNBOUND`, which every report and analysis carries (`:72-77`). |
| N10 | **Closed in code; acceptance unverified** | `heaptrack_print --version` is planned and recorded, and the interpreter is bound by SHA. Whether the real tool accepts `--version` is still for a functional probe (plan says so). X17 is author-detected. |
| N11 | **Mostly closed; residual R3-8** | The author's guard list now matches R2's. |
| N12 | **Mostly closed; residual R3-7** | Both R2 wording corrections are made. |

### N3 detail

- **Frozen binders.** `bind_manifest`, `bind_selected_roles` and `declared_identity` hash only through the module
  global `file_info` (`phase6_workloads.py:968-1054`). They never call `run.binary_info` or
  `manifest.file_identity`. `read_json` and `read_json_with_identity` are size-capped reads of at most 4 MiB.
  `bounded_binary_info` has the same semantics as `run.binary_info` (`run.py:765-771`). No module aliases
  `p6.file_info`.
- **Verification is bounded.** `verify` makes **0** unbounded `file_digest` calls. 33 files go through the bounded
  reader, including the budgets file hashed by the frozen `bind_manifest`.
- **Expiry inside a frozen binder.** `DeadlineExceeded` escapes the binder with 0 reads after the deadline.
- **The global is restored** after a normal return, a `ValueError` in `verify_go`, a `KeyboardInterrupt` in
  `bind_selected_roles` and `DeadlineExceeded`. It is the original object during probes.
- **Artifact hash.** If the clock passes the work deadline as a capture child returns, that artifact gets **0** reads.
  The run fails with `DeadlineExceeded`, and the final reserve still completes the end check (42/42 passed).
- **Pre-launch rehash.** If the clock passes the work deadline after admission, there is no launch: still 5 launches.
- **Final rehash.** It made 3 reads, all before the deadline, and **0 reads after expiry**. Exactly one
  `report.json` write followed, inside the lock (53,051 B). The check is `incomplete:'deadline'` (1/49 checked),
  with `quarantined` and `window_overrun` both true.
- **SIGINT during the final rehash** leaves a saved report inside the lock with
  `finalization KeyboardInterrupt: signal 2` and `passed:false`, and SIGINT is restored.
- **Inventory of every hash in a full capture.** Unbounded hashing happens only in `run_probe` (12 probe
  stdout/stderr files) and in `launcher` (the tiny launch script). There is none in verification,
  `changed_identities`, `artifact_identity` or `finalize`, and no event outside the lock.
- **Mutants.** X06, X07, X09 and X23 are author-detected. **X04** (binder swap a no-op) and **X08** (pre-launch
  rehash unbounded) are **missed by the author suite** (R3-2).

## New findings (most severe first)

### R3-4 — LOW-MEDIUM (confirmed) · fix or rule before interpretive heaptrack/strace profiles
**Non-perf modes have no content or adequacy criterion, so their exit 0 means "complete", not "interpretable".**

- `finish_analysis` marks an entry interpretable when `entry.get('stack_status', 'sufficient_for_descriptive_stacks')`
  equals that default (`:1070-1071`). Only perf entries have a `stack_status`.
- So for heaptrack, strace and strace-writes, every analyzed entry is interpretable, and exits 3 and 4 cannot occur.
- **Probe:**
  - A heaptrack analysis whose `heaptrack_print` output is the two bytes `x\n` gives **exit 0,
    `all_roles_interpretable`**.
  - strace (`-c`) analysis does no parse at all: the record is just the artifact identity and a note (`:1029-1030`).
- This is the N5 hazard again for the non-perf modes. A coordinator gating on exit 0 would cite a content-free
  heaptrack report as an interpretable profile.
- **Minimal correction, either of:**
  - **Rule and rename.** For non-perf modes, report a status such as `analysis_complete_no_adequacy_criterion` and
    keep `interpretable` false. Or rule that only perf `interpretable` may be cited.
  - **Add minimal adequacy checks once a functional probe shows the real formats.** For example, the
    `heaptrack_print` summary must report a nonzero allocation-call count, and the strace `-c` table must have a
    `total` row with nonzero calls.

### R3-1 — LOW (confirmed) · before interpretive analysis
**Analysis uses the report's `entry['directory']` unvalidated, so it can write outside `--out`.**

- `analyze_one` is given `out / 'captures' / entry['directory']` (`:1197`) and runs `target.mkdir(parents=True)`
  (`:1016`).
- **Probe.** A report whose `directory` was rewritten to `../../r3-escaped` (SHA recomputed) gave **exit 0**.
  `analysis.txt`, `analysis.stderr`, `perf-stats.txt` and `perf-stats.stderr` were created in a sibling of the
  analysis directory.
- An absolute value would replace the base path entirely, by pathlib semantics. `../../<capture>/<new>` would create a
  new folder **inside a sealed capture folder**, which defeats the N7 guarantee.
- **Precondition.** The coordinator-supplied report SHA is the only trust anchor. The adapter itself writes only
  `folder.name` (`:706-708`). So this needs tampered report bytes before they are hashed; hence LOW.
- **Minimal correction:**
  1. Require `entry['directory'] == f"{index:03d}-capture-{role}-r{repetition}"`, which can be rebuilt from
     `report['schedule']`.
  2. Require `target.resolve().parent == (out / 'captures').resolve()`.
- **Optional hardening.** Re-read the sidecar by the identity recorded in the report, and require the report's tools
  and executables to equal it. Today analysis executes `report['provenance']['tools'][...]['path']` on the strength
  of the report SHA alone.

### R3-2 — LOW (confirmed) · test coverage and doc accuracy; fix before source acceptance
**Seven of 23 targeted mutants survive the author's suite. One plan claim about a test is false.**

| Mutant | What it reverts | Author suite | R3 probe |
|---|---|---|---|
| X04 | N3: `bounded_hashing` swap becomes a no-op, so the frozen binders hash unbounded | passes | caught |
| X08 | N3: pre-launch rehash unbounded | passes (masked by the artifact check in the same test) | caught |
| X10 | N4: analysis binds to `IMPORTED`, not the report's harness | passes | caught |
| X13 | N6: tool timeout not clipped (always 180 s) | passes | caught |
| X19 | Local `write_counts` drops `+++` exit lines (276/3000 fuzz traces differ) | passes | caught |
| X20 | Local `write_counts` loses its per-record deadline | passes | caught |
| X21 | gzip copy loses its per-chunk deadline | passes | caught |

**The X21 claim.** The plan says (`:161-163`) that `test_analysis_records_overrun_and_stops_gzip_at_the_deadline`
"checks that no gzip read starts after the deadline". **That is not what the test exercises.**

- I replayed its exact configuration (4-byte chunks, +400 s per read of `interpreted.txt`).
- All 3 reads come from `run_tool`'s hash of the interpreter output (`:948`), and **0 come from `bounded_gzip`**.
  The failure is "deadline reached while hashing …interpreted.txt".
- With X21, the gzip copy does 98 reads after the work deadline, and the author suite still passes.

The code is correct in all seven cases; these are regression-protection gaps for the R2 fixes.

**Minimal correction:**

- Add seven small tests; R3's probes can be adapted. In particular, advance the clock only inside `bounded_gzip`.
  For `write_counts`, use a corpus with `+++ exited` lines and EOF-without-newline lines.
- Correct the plan's description of the gzip test.

### R3-3 — LOW (confirmed) · before interpretive analysis
**A tool killed at the deadline, or a failing tool, leaves evidence only on disk, not in `analysis.json`.**

- **Expiry during a tool.** Each record starts as `status:'skipped'` (`:1163`). `DeadlineExceeded` is not caught per
  entry (`:1200`).
  - **Probe:** after expiry, the in-progress entry stays `status:'skipped'` with **no reason or error**.
  - Its four partial files (`analysis.txt/.stderr`, `perf-stats.txt/.stderr`) remain under `captures/<entry>/`,
    but `analysis.json` does not mention them.
- **Failing tool.** The return code survives only inside the error string ("returncode 129"). stdout and stderr
  identities are not recorded (`:946-947`). This is the analysis-side twin of R1 F13, which was fixed for probes.
- **Minimal correction:**
  1. Set `record['status'] = 'in_progress'` before `analyze_one`.
  2. On any `BaseException`, record the status (`failed` or `interrupted`), the error, and size-only or bounded
     identities of the files in the entry's target, then re-raise.
  3. In `run_tool`, attach `returncode` and the stdout/stderr identities to the raised error, as `run_probe` does.

### R3-5 — LOW (source) · fix or rule before interpretive analysis
**The analysis budget and its final reserve ignore hashing work.**

- `required = len(pending) × (tools × 180 + 30)` (`:1009-1011`, `:1182`). It excludes the following hashing:
  - the start rehash of tracked identities and artifacts: about 348 MB plus up to 6 × 2 GiB for perf;
  - each entry's artifact rehash before and after;
  - output hashes of up to 4 GiB each.
- The end rehash (`:1216`) has only `FINAL_FLOOR_S` = 30 s after the work deadline. At the plan's own 200 MiB/s, 30 s
  covers about 6 GiB, against up to about 12.4 GB.
- **Effect:** fail-closed. The end check becomes `incomplete:'deadline'`, the analysis is quarantined with exit 1, and
  the offline lock is held up to the deadline.
- Capture has no such gap: `final_reserve_s` is size-derived (`:542-544`).
- **Minimal correction:**
  - Use a size-derived analysis final reserve: `hash_s(sum of analysis_identities sizes) + 30`, used for both
    `work_deadline` and admission.
  - Include start and per-entry hash estimates in `required`.
  - Or state a minimum `--budget-s` sizing rule in the plan.

### R3-6 — LOW (confirmed) · disclose or bound
**Probe post-processing is bounded by size, not by the deadline.**

- `run_probe` hashes probe stdout and stderr with the unbounded `p6.file_info` (`:423`). It parses `--syms` output
  line by line with no deadline check (`:440-446`).
- **Probe:** with the clock moved past the window deadline inside the first probe, two unbounded hashes still ran
  (`readelf-version.stdout/.stderr`).
- The next probe was then refused, and the end check stopped at once (0/41).
- **Worst case:** the 256 MiB probe cap, about 1.3 s of hashing at the planning rate, plus a regex pass. The window
  deadline is still enforced by `finalize`.
- The plan's N3 list does not claim probes, but its "outside these chunk checks" list omits them.
- **Minimal correction:** pass `work_deadline` into `run_probe` and use `bounded_info` there, with a deadline check
  every N lines in the symbols loop. Or add probes to the plan's "outside chunk checks" list.

### R3-8 — LOW (tests; confirmed) · N11 residual
**The author's guards miss three alias families.**

- **`subprocess._fork_exec`.** In CPython 3.14 it is bound at import as an alias of `_posixsubprocess.fork_exec`, so
  the author's `_posixsubprocess.fork_exec` guard does not cover subprocess's own path. `Popen` itself is guarded.
- **`posix.*` originals** of the guarded `os.*` functions.
- **`_signal.pthread_kill/raise_signal/pidfd_send_signal`.** My alias scan, run with my guards, still finds these.

None is referenced by any reviewed module: grep finds no `posix.`, `_signal`, `_fork_exec`, `ctypes` or
`from subprocess import`. My runner guarded the first two families and recorded 0 trips.

**Minimal correction:** add these names to the guard list, or assert that an alias scan is empty, as `review_r3.py`
does.

### R3-7 — TRIVIAL (doc)

- **`prepare-plan.log` hash.** The plan lists `e935cccf…`. That hash is self-referential: the log contains its own
  hash line, written before the log was complete. The file on disk is `abb2a113…`.
- **The gzip-test sentence:** see R3-2.
- **`prepare` with a busy lock** propagates `BlockingIOError` as a traceback (exit 1), not a clean refusal. That is
  consistent with the API table's "0, or error", but the N1 text says it "refuses".

### R3-9 — INFO

- **Discarded hash.** `run_tool` hashes the heaptrack plain intermediate, up to 4 GiB, and then
  `interpret.pop('output')` discards that hash (`:948`, `:1053`). That is one extra full, deadline-bounded read.
  `stat` would suffice.
- **Working directory.** Analysis tools inherit the caller's working directory (no `cwd=`; probe kwargs are
  `env, stderr, stdin, stdout, timeout`). Neither `perf report` nor `heaptrack_print` is known to write relative
  files. `cwd=target` would make that independent of where the coordinator runs.

## Specific regression questions from the brief

- **Do frozen binders bypass the `file_info` swap?** No. See the N3 detail. `read_json` and
  `read_json_with_identity` are separate reads capped at 4 MiB; that is inherited and covered by the end rehash.
- **Is the import-time hash before the lock heavy?** No: 273,273 B across the eight harness modules.
- **Is the swapped global restored on every path?** Yes: normal return, `ValueError`, `KeyboardInterrupt` and
  `DeadlineExceeded`, and it is the original object during probes. Mutant X05 is caught by both suites.
- **Deadlines in the parse, gzip and regex loops:**
  - `write_counts` checks per record, and gzip per chunk.
  - The perf `--stats` read is capped at 16 MiB without a deadline check (`:1039-1042`), which is size-bounded.
  - The symbol parse is R3-6.
  - No regex here can backtrack catastrophically: all are anchored and linear.
- **Failure capture and finalization.** SIGINT during the final rehash and expiry are both covered above.
- **Minimal report write after expiry.** One 53 KB atomic write, inside the lock.
- **Drift in the local helpers:**
  - `bounded_write_counts` versus the frozen `write_counts`: **3000/3000** fuzz traces are identical, in both result
    and error text. The fuzz covered unfinished/resumed lines, `---`/`+++` lines, hex/decimal/negative/`?` results,
    blank lines and EOF without a newline.
  - The local heaptrack steps match the frozen `profile_external.analyze`: same argv, the same
    returncode/empty-output rule, a level-9 gzip, and the same input re-identity before and after.
  - The deliberate differences are an isolated environment, deadlines, exclusive (`x`) modes, and removal of the
    plain intermediate on failure as well.
- **Cleanup of capped descendants, same collector (source).** Unchanged from R2 apart from the cap values.
  - The cap is set before `run_sample` spawns the fixture (`Popen`) and the meter (`posix_spawn` with SIGXFSZ default).
  - A profiler killed by SIGXFSZ leaves the CLI in the meter's process group. `collect` drains the finite stream
    until EOF, or until the timeout triggers `kill_group(SIGKILL)` (`phase6_workloads.py:289-336`).
  - Python's EFBIG becomes a collector exception, which aborts the window.

## Unresolved objections

| ID | Item | Gate |
|---|---|---|
| R3-4 | Non-perf `interpretable`/exit 0 without any adequacy criterion | interpretive heaptrack/strace (fix or rule) |
| R3-5 | Analysis budget and final reserve ignore hashing (fail-closed) | interpretive analysis (fix or rule) |
| R3-2 | 7 mutants survive the author suite; the gzip-test claim is wrong | source acceptance |
| R3-1 | Unvalidated `entry['directory']` lets analysis write outside `--out` | interpretive analysis |
| R3-3 | Expired or failed tool evidence is absent from `analysis.json` | interpretive analysis |
| R3-6, R3-7, R3-8, R3-9 | As above | before final profile evidence |
| host | Real perf `--stats` format, `perf record -q` exit and stderr, strace/heaptrack exit and stderr, `heaptrack_print --version`, perf artifact size and sample adequacy at 999 Hz `dwarf,16384`, `kptr_restrict` warnings | separately authorized tiny functional probes |
| — | Go source-to-commit equality rests on the Phase 7 attestation | attested, not verified here |
