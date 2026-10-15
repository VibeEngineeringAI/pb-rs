# Phase6 profiling adapter — independent review r4

Reviewer: a fresh Opus 5.5 review sub-agent. No author, R1, R2 or R3 thread was reused.

**Scope:**

- the settled v4 adapter `harness/phase6_profiles.py`, its tests and `docs/phase6-profiling-plan.md`;
- the v4 sidecar, plans, logs and mutant runner in `profile-adapter-v4`;
- the frozen collector, `profile_external`, `run` and `manifest` APIs that v4 reuses;
- reviews R1, R2 and R3, and R3's cache (`review_r3.py`, mutant patterns).

This is not whole-phase acceptance. It accepts no measurement and no interpretive profile. It authorizes no window,
token or native run.

## Execution and identity (read first)

**Shell.** Claude Code's own temp directory was EDQUOT. Every command redirected its output into
`/home/ajs/.cache/pb-phase6/profile-review-r4/` and I read it back from there. Each "failed" or "EDQUOT" tool status
in this session is that echo loss, not a command failure; every result file exists. I did not touch `/tmp`.

**Runner.** `review_r4.py` (final `6b381eab…`). It installs its guards before any reviewed module is imported. These
hard-fail and are counted:

- `subprocess.*` including `_fork_exec`, and `_posixsubprocess.fork_exec`;
- in both `os.*` and `posix.*`: fork, forkpty, exec*, spawn*, posix_spawn[p], system, popen, fexecve, kill, killpg,
  `wait`/`waitpid`/`wait3`/`wait4`/`waitid`, and `pidfd_open`;
- in both `signal.*` and `_signal.*`: `pthread_kill`, `raise_signal`, `pidfd_send_signal`, `alarm` and `setitimer`;
- socket connect, bind, listen, `send*` and `sendfile`; `socket.create_connection/create_server`; and
  `ssl.create_connection`;
- `fcntl.flock/lockf` and the lock commands of `fcntl.fcntl`; `resource.prlimit`;
- any open of the real common lock;
- any write, mkdir, chmod, unlink or rename under the worktree or `/tmp`.

`resource.setrlimit` is recorded and never applied. `signal.signal` is recorded and passed through, because it only
installs handlers. Every run used `python3 -I -B -W error::ResourceWarning` with `TMPDIR`, `GOTMPDIR` and
`CLAUDE_CODE_TMPDIR` set to the R4 cache. `p6.TMP` was pointed at that cache too, so all fixture directories lived
there.

**Results: author's evidence and R4's executions are kept separate.**

| Run | Result | Evidence |
|---|---|---|
| Author, as claimed in the plan | 48/48 tests; 22/22 applicable R3 X-mutants killed; V01–V13 all killed except V12 | `profile-adapter-v4/tests-v4.log`, `mutants-summary.txt` (not re-derived by R4) |
| **R4:** author's unmodified suite under R4's stricter guards | **48/48 OK, 0 trips** | `logs/tests-1.json` `3e215909…` |
| **R4:** alias scan, R4 guard set | **empty**: no original process, signal, lock or socket callable is reachable under another name | `logs/scan-1.json` `fa440779…` |
| **R4:** offline plan arithmetic (four `plan.json`, small JSON reads only) | All four budgets recompute exactly: 1371 / 1330 / 1341 / 832 s. All `launched:false`. Plan harness = sidecar harness = files on disk. | `logs/facts-1.json` `1ddfe876…` |
| **R4:** own probes (only the author's mock-provenance fixtures are reused; assertions are R4's) | 17 run. **5 fail by design:** each asserts sound behaviour and fails on v4. These are findings R4-1…R4-4. | `logs/probes-3.json` `b65c9c2a…` |
| **R4:** R3's X01–X23 patterns, read verbatim from `review_r3.py`, against the author's suite | **22/22 applicable killed**, X15 pattern-absent, 0 trips. The author's claim is reproduced. | `logs/xmutants/summary.txt` `d29a568e…` |
| **R4:** 25 R4 mutants (V12, X15b, X15c, M01–M22) × {author suite, R4 probes} | see "Mutants" below; 0 trips | `logs/mutants/summary.txt` `5c377a70…` |

**Preserved failed attempts.** I deleted or overwrote nothing.

- `probes-1.json` (`83529926…`) had 4 failures:
  - 3 are real findings (R4-1 ×2, R4-2).
  - 1 was my own wrong assertion: I expected 1 heaptrack capture, but the author's `args()` default is
    `repetitions=2`. The value I observed, `[472, 472]`, is correct.
- `probes-2.json` (`d495a6ca…`) added the causal artifact probes. It had one runner bug: the V12 race probe indexed
  `entries[0]` when entries were legitimately empty.
- `review_r4.attempt1-plus-additions.py` (`62e872f8…`) is the runner as it stood before the attempt-2 and attempt-3
  fixes.
- The final runner gained one block **after** the M-matrix: a fallback that loads R3's X patterns. It does not
  change any M-mutant or probe.

**Not run:**

- `test_profile_external.py`;
- any native CLI, build, compiler, tool `--version`, readelf, perf, strace or heaptrack;
- any native helper, fixture or meter;
- any network, real flock, quiet token or toolchain hashing.

All statements about real tool formats, exit status, stderr or speed come from source, not observation.

**Before and after.** `hashset.sh` covers:

- 42 reviewed, frozen and preserved files: the adapter, tests, plan, R1–R3, the eight harness modules, `meter.c`,
  budgets, the parent, the v1–v4 sidecars, the v3/v4 logs, the four plans, the author's tools and R3's runner;
- the `harness/__pycache__` listing with mtimes;
- `stat` of the lock file (it is never opened);
- `git status`.

`hashes-before-full.txt` equals `hashes-after-full.txt` (`f6a416e9…`). No `p6-profiles-test-*` leftovers remain.
Every settled identity in the brief matches:

| File | SHA256 |
|---|---|
| `harness/phase6_profiles.py` (82,367 B) | `c6f1629cadd9953f49dc80586f64fcb54b6660cbc2648d5a954621898169cc36` |
| `harness/test_phase6_profiles.py` | `b7a932bf29616941fe2d0b84e2cdc85af3ccef8416686482f0a8d6fca5e70a14` |
| `docs/phase6-profiling-plan.md` | `ce279b87804b00bf500b8ef85d996437c8e41200102d3ede11944a70dfc53d93` |
| `profile-adapter-v4/profile-sidecar.json` | `90001fb08e9b87d25fd9d1ff2033765c2f5c085216b8f61469e48bdf76a561ac` |
| v4 `prepare-plan.log` | `36888e28…` (equals `hashes.txt`) |
| v3 `prepare-plan.log` | `abb2a113…` (the R3-7 correction holds) |
| `profile-adapter-v4/hashes.txt` | `7b9717dc…`. Every small-file line in it matches the files on disk. |

No binary or toolchain was rehashed. The sidecar's eight harness hashes were checked against the files on disk.
Its tool aliases resolve as recorded (`/bin/sh` → `/usr/bin/bash`; the rest are identity). Its evidence statuses are
two `current_supporting` files and the R4 file `historical_not_certifying`.

## Verdict by gate

### (1) Tiny functional probe — source readiness: **READY (no code blocker)**

This is **not** a quiet-window, token or lock authorization. Readiness covers a separately authorized one-row,
one-repetition run per mode, whose outputs are functional only.

**Re-verified by R4's own execution:**

- **Busy lock.** All four commands do zero opens, reads, hashes or mkdirs, exit 2 and leave no output: capture,
  plan, analyze and prepare.
- **Expiry.** No hash or read happens after the work deadline: in probes, in tool steps, in partial files, or after
  a tool expires.
- **Restoration.** `p6.file_info`, the `SIGINT/SIGTERM/SIGHUP` handlers and `RLIMIT_FSIZE/CORE` are restored on
  normal, interrupt and deadline-in-binder exits.
- **Journal.** It is atomic, and its states are monotonic. Over 27 saves: entries go skipped → in_progress →
  analyzed, steps go in_progress → completed, there is exactly one `final`, and no `.partial-*` file is left.
- **Report rewrites.** All 15 rewrite cases are refused before any tool, with no `captures/<entry>` mkdir.

**New findings.** All are LOW-MEDIUM or below. None affects lock order, deadlines, caps, child cleanup or the exit
code of a genuine one-row capture or analysis.

- R4-1 is the one that touches signal or expiry integrity. **The exit code stays correct (1)**, so a coordinator
  gating on exit status is safe.
- If root keeps the "all corrections first" policy, R4-1, R4-2 and R4-3 are each a few lines (fixes below). R4-4 and
  R4-5 are test-only.

### (2) Larger interpretation readiness: **BLOCKED**

Interpretation stays blocked until all of the following are in place:

1. **Functional probes have established the host facts:**
   - the real perf `--stats` / `--no-skip-empty` format;
   - `perf record -q` exit status and stderr;
   - strace and heaptrack exit status and stderr;
   - whether `heaptrack_print --version` is accepted;
   - `kptr_restrict` warnings;
   - perf artifact size and samples per capture at 999 Hz `dwarf,16384`;
   - `perf report` CPU time against the 180 s step timeout.
2. **Non-perf adequacy criteria have been proposed from those outputs and reviewed separately.** Today v4 correctly
   makes no claim (R3-4 closed).
3. **R4-1 is fixed.** A consumer reading `interpretation.status` or per-entry `interpretable` must never see a stale
   positive.
4. **R4-3 is fixed** (artifact name and count bound).
5. **The analysis budget has been sized for real work** (R4-6), once actual perf.data and heaptrack sizes are known.

## R3 dispositions

| ID | Status | R4 evidence (R4-executed unless marked "author" or "source") |
|---|---|---|
| R3-1 folder traversal / report rewrite | **Closed**; residuals R4-2, R4-3, R4-4 | Every rewrite gives exit 1, 0 tool calls and no entry mkdir: nested `a/b`, `.`, phase, role or repetition swap, a bool, negative or out-of-range index, an unknown mode, an unknown role in both schedule and entry, artifact traversal `…/../<dir>/perf.data`, an artifact in a nested subfolder, an artifact elsewhere (sidecar), a different sidecar SHA, report harness ≠ sidecar/running code, and a rewritten tool SHA. **The artifact cases are causal only when the forged report sits in the capture root.** The control is an unchanged copy there (exit 0, 8 tools). Tools execute from the sidecar re-verification (`:1250`), never from report fields. |
| R3-2 seven weak regressions | **Closed** | R3's verbatim X04, X08, X10, X13, X19, X20 and X21 are each killed by their new focused test (R4 run). The new gzip test advances the clock only inside `bounded_gzip`. |
| R3-3 tool failure / expiry evidence | **Closed**; related new R4-1 | **rc 129:** the step records `failed`, rc and hashed stderr (author test). **Expiry inside a tool** (R4 probe, 8-byte chunks): the step and entry are `deadline`; stdout, stderr and partial files are size-only with `hash_unavailable:'deadline passed'`; there are **0 opens in `analyze_one`/`analyze_report` at or after the work deadline**; the end check then completes inside the final reserve (49/49 passed). **Journal before the tool:** confirmed by snapshots. |
| R3-4 non-perf adequacy | **Closed per parent decision** | Matrix: heaptrack (content-free `\n`), strace and strace-writes each give **exit 3** with `analysis_complete_no_adequacy_criterion` and 0 interpretable. perf: all sufficient → 0; lost=1 → 3; Go low → 4 (2 interpretable). |
| R3-5 analysis reserves | **Closed in code**; realism residual R4-6 | `per_capture_s` = `2×180 + 30 + hash_s(2×artifact + 4×4 GiB)` = 472 per heaptrack capture (two captures); `final_reserve_s` = start rehash + 30; `required_s` = start + Σ per capture + final. At `required_s − 1`: exit 1, "not started", 0 tool calls. `TOOL_STEPS['perf'] = 2` is in effect (M-matrix V11 analog author-killed). |
| R3-6 probe hashing / parsing | **Closed** | With the clock moved past the work deadline inside probe 3: exit 1, 3 probes run, the record holds `DeadlineExceeded` and a size-only stdout, there are **0 opens, digests or SHA calls in `run_probe` after the work deadline**, and no 4th probe runs. |
| R3-7 log hash, busy prepare | **Closed** | v3 log `abb2a113…` and v4 log `36888e28…` both match. Busy `prepare` exits 2 with zero events and no sidecar. |
| R3-8 guard aliases | **Closed** | The alias scan under R4's larger guard set is empty. Trivial residual: the author's test guards omit `socket.send/sendall` (the mutant runner has them). This is irrelevant to reviewed code, which uses no sockets. |
| R3-9 cwd / discarded hash | **Closed** | Every analysis tool cwd is `out/captures/<entry>`. `interpreted.txt` is never passed to `bounded_sha256`; it is read only by the gzip copy. |
| X15 (superseded) | **Re-targeted; killed** | X15b (analysis target = sealed entry folder) is killed by 8 author tests and 2 R4 probes. X15c (analysis HOME = capture root) is killed by the author's env assertion. |
| V12 "equivalent" | **Agreed, with a caveat** | See "V12" below. |

## New findings (most severe first)

### R4-1 — LOW-MEDIUM (confirmed) · fix before interpretive use; not a functional-probe blocker
**An interrupt during the analysis end checks leaves a stale positive interpretation, and the last journal write
happens after the lock is released.**

- `end_analysis` (`:1354-1377`) catches only `OSError/ValueError/DeadlineExceeded` around `verify_harness`, and only
  `DeadlineExceeded` around `changed_identities`. Its `finally` then calls `finish_analysis`.
- `finish_analysis` (`:1144-1146`) derives `complete` from `failure`, `quarantined` and `overrun`. It does **not**
  check that the end checks passed.
- So a `KeyboardInterrupt` (SIGINT, SIGTERM or SIGHUP via `interrupt_signals`) raised inside either end check is
  evaluated while `failure` is still `None` and `quarantined` is `False`.
- The exception then skips `save()` (`:1218`), leaves the `with` (the lock is released), and is caught at
  `:1219-1224`. That code sets `failure`, `analysis_complete:false` and `exit_status:1`, and writes the journal
  **outside the lock**.

**Reproduction** (`r4_interrupt_during_end_identity_check`): a genuine perf capture, then an analysis with all 4
captures sufficient, and `KeyboardInterrupt('signal 2')` raised on the second `changed_identities` call (the end
check). The final `analysis.json` contains:

| Field | Value |
|---|---|
| `exit_status` | 1 (process exit 1) |
| `analysis_complete` | false |
| `failure` | `KeyboardInterrupt: signal 2` |
| `interpretation.status` | **`all_roles_interpretable`** |
| entries `interpretable` | **all true** |
| roles `interpretable_captures` | **2/2** |
| `quarantined` | false |
| `end_identity_check` | **absent** |
| `state` | final |
| last `write_json` | `lock held: false` |

The printed summary also says `"interpretation": "all_roles_interpretable"` beside `exit_status 1`. The same happens
when the end harness check is interrupted with signal 15 (`r4_interrupt_during_end_harness_check`).

**Impact:**

- The exit code is right. But any consumer citing the status or per-entry flags gets a positive claim for an analysis
  whose end identity was never verified.
- The post-lock write is a small I/O that can land inside another holder's quiet window.
- Capture is not affected: `finalize` (`:809-814`) records the failure before `evaluate`.
- R2 F8 ("analysis final write inside the lock") holds only on the non-signal path.

**Narrow fix** (about 6 lines):

1. In `end_analysis`, add `except BaseException as error: result.update(quarantined=True,
   failure=result['failure'] or f'end checks {type(error).__name__}: {error}'); raise` before the `finally`.
2. In `finish_analysis`, also require `result.get('end_identity_check', {}).get('passed')` and
   `result.get('analysis_harness_end', {}).get('passed')` for `complete`.
3. In `analyze_command`, write the final journal inside the lock even when end checks raise:
   `finally: try: end_analysis(...) finally: save()`.

**Test:** R4's probe, asserting no `interpretable:true` and a final write while the lock is held.

### R4-2 — LOW (confirmed) · before interpretive analysis
**`analyze --out` may sit inside the sealed capture tree, including inside an entry folder.**

- `analyze_command` (`:1186-1201`) requires only that `--out` is new.
- Capture's `new_output` (`:500-507`) refuses frozen provenance directories; analysis has no equivalent, and no
  disjointness check against `report_path.parent`.

**Reproduction** (`r4_r3_1_out_inside_capture_tree_not_refused`):

- `--out <capture>/analysis` and `--out <capture>/004-capture-go-symbolized-r0/analysis` both give **exit 0** with
  `all_roles_interpretable`.
- 34 new files appear under the capture tree. None of the existing capture files changed.

**Impact.** The plan's and journal's claim `capture_folders: 'read and hashed only, never written'` and the N7
guarantee hold only if the coordinator picks a disjoint `--out`. This is a natural mistake. It does not affect
artifact identities or exit semantics.

**Narrow fix:** refuse when `out == p` or `p in out.parents` for `p = report_path.parent.resolve()`. Also refuse the
frozen provenance directories, as `new_output` does.

### R4-3 — LOW (confirmed) · before interpretive analysis
**The artifact's name and count are not bound. Any file in the entry folder can stand in as the profile.**

- `verify_report_binding` (`:1272-1274`) checks only that each artifact's parent is the entry folder.
- `analyze_one` (`:1092`) analyzes `artifacts[0]`.

**Reproduction** (`r4_r3_1_forged_inside_capture_root`; a rewritten report placed in the capture root):

- With the artifact set to the entry's `launch` script and its real identity: **exit 0, `all_roles_interpretable`**,
  and `perf report -i …/launch` ran.
- `[launch, perf.data]` (two artifacts, launcher first) gives the same result.

**Precondition.** As with R3-1: the coordinator must supply the SHA of the rewritten report. Hence LOW.

**Narrow fix:**

- Require `len(entry['artifacts']) == 1`.
- Require `Path(path).name == ARTIFACT_NAMES[mode]`, or for heaptrack, `name.startswith('heaptrack')`. This mirrors
  `artifact_identity` (`:384`).

### R4-4 — LOW (tests; confirmed)
**The author's `artifact elsewhere` subtest is not causal.**

- `forge` writes forged reports under `self.base` (`test_phase6_profiles.py:933-936`), not under the capture root.
- `verify_report_binding` resolves an artifact's expected folder relative to the report's own location. So every
  report outside the capture root fails at the first capture artifact.
- R4 copied an **unchanged** report to `self.base` (`r4_author_artifact_elsewhere_case_is_location_only`). It fails
  with the identical "artifact is outside its capture folder", which is exactly the subtest's expected message
  (`:946-947`).
- The other subtests (traversal, absolute, tool, role binary, schedule, sidecar) fail before that loop, so they are
  causal.

**Fix:** forge into `self.root / 'out'`, and add an unchanged-copy control that must exit 0. The same placement would
also let a test cover R4-3.

### R4-5 — LOW (tests) · source acceptance
**Non-equivalent mutants that survive both the author suite and R4's probes.** The code is correct today in each
case; these are regression-protection gaps.

| Mutant | Reverts | Real effect if regressed |
|---|---|---|
| M10 | `interrupt_signals()` dropped from analyze (`:1194`) | SIGTERM or SIGHUP kills analysis with the journal `in_progress`, no end check (fail-safe but evidence-poor) |
| M11 | analysis `file_size_limit` removed (`:1096`) | tool outputs uncapped |
| M15 | capture `window_overrun` always false (`:812`) | an overrun capture can pass (the analysis twin X14 is protected) |
| M09 | per-entry analysis admission removed (`:1334`) | an entry starts without its reserve (the deadline still enforces) |
| M14 | per-probe admission removed (`:716`) | a probe can eat into the final reserve |
| M03 | report harness ≠ sidecar harness unchecked (`:1247`) | forged-report only |
| M08 | post-step `save()` removed (`:1015`) | the journal lags by one save |
| M18 | stats read uncapped (`:1115`) | memory only |

**Equivalent or masked, so no test is needed:**

| Mutant | Why it does not matter |
|---|---|
| M02 (sidecar re-read SHA) | Masked by the start rehash: the sidecar is tracked. |
| M04 (entry role/phase) | The entry's role is never used; the schedule item is. |
| M05 (index type) | The folder equality fails anyway. |
| M07 (tools from report) | The binding equality is exact. |
| M12 | A deadline at the end implies overrun. |
| M20 | `mkdir` refuses inside the lock. |

**Fix:** add three focused tests first, for M10, M11 and M15 (the ones with operational effect).

### R4-6 — INFO (source) · interpretive scale only
**Analysis reserve realism.**

- `analysis_entry_s` counts tool steps and hashing only. It omits:
  - the level-9 gzip of a heaptrack intermediate of up to 4 GiB, which typically runs far below the 200 MiB/s
    planning rate;
  - the CPU time `perf report` needs on a large dwarf perf.data. The fixed 180 s per step may simply time out.
- Both fail closed (`deadline` / `timed_out`).
- After an interrupt, the bounded hashing of step outputs and partial files, and the full end checks, still run.
  In the R4 probe that was 1,383 chunk reads after the interrupt, ending at the work deadline. This is intended
  (finalize inside the lock), but an operator's Ctrl-C does not stop quickly at multi-GiB scale.
- `os.statvfs` headroom does not see per-user quotas, and this host's `/tmp` is at quota now. On EDQUOT, `report.json`
  stays at its last atomic save and the process exits 1 with a traceback (fail-closed, stale evidence).
- None of this matters for `one-row`.

## V12 (author's "equivalent mutant")

The claim holds for every single-process input, with a caveat.

- `directory` is rebuilt from `schedule()` output: fixed role strings and integer repetitions.
- `out/captures` was created moments earlier by this process. So `(captures_root/directory).resolve().parent` can
  differ only if another process plants `out/captures/<entry>` between `mkdir` and the check.
- R4 simulated exactly that race (`r4_v12_concurrent_symlink_race_fails_closed`, with a symlink to an outside
  directory):

| | Exit | Result | Tool calls |
|---|---|---|---|
| Original | 1 | "would escape the analysis captures folder" | 0 |
| V12 | 1 | the affected entry `failed` with `FileExistsError` from `target.mkdir()`; the 3 other entries ran | 6 |

- In both cases **nothing was written outside**.
- **Caveat.** Under V12, `partial_files` would rglob, and bounded-hash, files reachable through such a symlink. That
  is read-only, but outside data enters the journal.
- **Verdict.** It is equivalent for safety and fail-closed. The check is genuine defense in depth. No test is needed
  beyond R4's race probe, which is optional.

## Mutants (R4-executed; a "kill" counts only probes that pass on the original)

| Mutant | Author suite | R4 probes |
|---|---|---|
| V12 | survived | survived (race probe fails closed both ways) |
| X15b sealed entry folder | **killed** (8 tests) | **killed** |
| X15c HOME in capture root | **killed** | survived |
| M01 artifact containment off | **killed** (only via the location-only subtest, R4-4) | **killed** |
| M06 schedule unchecked | **killed** | survived |
| M13 interrupt swallowed per entry | **killed** | survived |
| M16 entry not marked in_progress | **killed** | survived |
| M17 partial files hashed unbounded | **killed** | **killed** |
| M19 perf loss ignored | **killed** | **killed** |
| M21 start rehash skipped | **killed** | survived |
| M22 heaptrack plain kept | **killed** | survived |
| M02, M03, M04, M05, M07, M08, M09, M10, M11, M12, M14, M15, M18, M20 | survived | survived (see R4-5 for which matter) |

## Specific regression questions from the brief

- **Sidecar and report: same-read-byte identity and end binding.**
  - Both are parsed from the same bounded read that is hashed (`read_json_with_identity`): 256 MiB for the report,
    4 MiB for the sidecar.
  - The report SHA must equal `--report-sha256`. The sidecar SHA named by the report must equal `--sidecar-sha256`,
    and the re-read bytes must match it (`:1242-1246`).
  - The sidecar path is among the tracked identities, so it is rehashed at start and end.
  - `report.json` itself is not rehashed at the end, but its in-memory bytes are SHA-bound (source).
- **Schedule and role matching.** Covered by R3-1 above, including an unknown role and a tampered artifact. The
  residual is R4-3.
- **Capture immutability.** Holds for disjoint `--out` (author snapshot test, X15b). R4-2 is the exception.
- **Atomic checkpoints under BaseException.**
  - Every entry or step exception records its status and error before re-raising.
  - Interrupt and expiry partial fields are present (author tests; R4 expiry probe).
  - The exception is R4-1, the end-check window.
- **Budget.**
  - perf counts two tool steps.
  - Per-entry and final reserves are size-derived.
  - The analysis clock starts at command entry (`:1183`).
  - The capture clock is unchanged from R3 (X03 killed).
- **Helpers restored.** Yes on every exit R4 exercised.
- **Output caps.**
  - Per-file soft `RLIMIT_FSIZE`: 1 GiB for controls; 2 GiB for perf and heaptrack captures; 1 GiB for strace modes.
  - 256 MiB for probes, 4 GiB for analysis.
  - These are realistic for `one-row`. Disk headroom (`statvfs`) ignores quotas (R4-6).

## Recommended next functional step (if root authorizes; not authorized here)

**One mode per separately coordinated, bounded quiet window**, under the shared nonblocking lock with a fresh
coordinator token. Each analysis also executes native tools (perf report, heaptrack). The code takes only the lock for
it, without a token, so **root should also schedule analysis as its own coordinated slot**.

**Order** (cheapest and most diagnostic first):

1. strace, which validates the whole capture pipeline with no analysis tool;
2. perf, which carries the main host unknowns;
3. strace-writes;
4. heaptrack.

**Capture commands** (one per window; `<mode>` and budget from the table):

```
python3 harness/phase6_profiles.py capture --out /home/ajs/.cache/pb-phase6/profile-probe-<mode>-one-row-01 \
  --parent-sha256 a47093f4a3ea785a310f7ca1baa92444deaf7e21db5ba7db72dc897ddc3d047b \
  --sidecar /home/ajs/.cache/pb-phase6/profile-adapter-v4/profile-sidecar.json \
  --sidecar-sha256 90001fb08e9b87d25fd9d1ff2033765c2f5c085216b8f61469e48bdf76a561ac \
  --scenario one-row --mode <mode> --repetitions 1 --control-repetitions 1 \
  --window-budget-s <budget> --quiet-window-token <coordinator token>
```

**Analysis commands.** Use them only for a report with `passed:true`. `--out` must be **outside** the capture tree
(R4-2):

```
python3 harness/phase6_profiles.py analyze --report <capture>/report.json --report-sha256 <sha256 of it> \
  --sidecar-sha256 90001fb08e9b87d25fd9d1ff2033765c2f5c085216b8f61469e48bdf76a561ac \
  --out /home/ajs/.cache/pb-phase6/profile-probe-<mode>-one-row-01-analysis --budget-s <analysis budget>
```

**Budgets.** "Planned required" is the plan's recorded value (R4 recomputed it). "Analysis required" is R4's offline
estimate:

| Mode | Children | Capture planned required | Capture `--window-budget-s` | Analysis required (tiny artifact … at cap) | Analysis `--budget-s` | Expected analysis exit |
|---|---|---|---|---|---|---|
| strace | 4 + 2 | 1330 s | 1450 | 96 … 96 s | 200 | 3 |
| perf | 4 + 2 | 1371 s | 1500 | 978 … 1062 s | 1100 | 3 is likely: a `one-row` run is short, so each capture is probably `insufficient_samples` against the 1000-sample floor. 0 or 4 are possible. |
| strace-writes | 4 + 2 | 1341 s | 1450 | 96 … 116 s | 200 | 3, or 1 if traced FD 1 ≠ stdout bytes |
| heaptrack | 2 + 1 | 832 s | 950 | 506 … 547 s | 600 | 3 |

**Reading the results:**

- These budgets are caps, not expected durations. Each `one-row` child should finish in seconds against its 120 s
  timeout.
- In every mode, exit 1 means a functional failure. Its journal (probe records, step argv, rc, stderr) is the
  evidence to collect.
- A failing `heaptrack_print --version` probe aborts that capture before any child. That still answers the
  question.
- None of these outputs is interpretive. They establish formats, exit codes and sizes only.

## Unresolved objections

| ID | Item | Gate |
|---|---|---|
| R4-1 | Interrupt during analysis end checks leaves a stale positive interpretation; final write after lock release | interpretive (cheap; fix first if "all corrections first") |
| R4-2 | `analyze --out` may be inside the capture tree | interpretive (cheap) |
| R4-3 | Artifact name and count not bound | interpretive (cheap) |
| R4-4 | Author `artifact elsewhere` subtest non-causal | source acceptance |
| R4-5 | M10, M11, M15 (and M09, M14, M03, M08, M18) unprotected by tests | source acceptance |
| R4-6 | Analysis reserve omits gzip and `perf report` CPU; quota-blind headroom | interpretive scale |
| host | Real perf `--stats` format, `perf record -q` exit and stderr, strace/heaptrack exit and stderr, `heaptrack_print --version`, `kptr_restrict`, perf samples and size at 999 Hz `dwarf,16384`, `perf report` time | separately authorized tiny functional probes |
| non-perf adequacy | No criterion by design (parent decision) | separate review after probe outputs |
| — | Go source-to-commit equality rests on the Phase 7 attestation | attested, not verified here |
