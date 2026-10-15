# Phase6 profiling adapter — independent review r5 (focused R4 delta)

Reviewer: a fresh Opus 5.5 review sub-agent. No author, R1, R2, R3 or R4 thread was reused.

**Scope.** This review covers the v5 delta that closes R4-1…R4-6. That means:

- `harness/phase6_profiles.py`, `harness/test_phase6_profiles.py` and `docs/phase6-profiling-plan.md`;
- the v5 sidecar, plans, logs and mutant runner in `profile-adapter-v5`;
- R4's review and its cache.

Code that did not change is not re-reviewed (see "Delta identity" below). This is not whole-phase acceptance. It
accepts no measurement and no interpretive profile. It authorizes no quiet window, token, lock use or native run.

## Execution and identity (read first)

**Shell.** Claude Code's temp directory is EDQUOT. Every command wrote its output into
`/home/ajs/.cache/pb-phase6/profile-review-r5/`, and I read it back from there. Each "failed" or "EDQUOT" tool status
in this session is that echo loss, not a command failure. I did not touch, move or symlink `/tmp`.

**Runner.** `review_r5.py` (`92837dc6…`). Its guard layer is adapted from R4's runner, with `sigqueue` added. All
guards are installed **before** any reviewed module is imported. These hard-fail and are counted:

- `subprocess.*`, including `_fork_exec`, and `_posixsubprocess.fork_exec`;
- in both `os.*` and `posix.*`: fork, forkpty, exec\*, spawn\*, posix_spawn[p], system, popen, fexecve, kill,
  killpg, `wait*` and `pidfd_open`;
- in both `signal.*` and `_signal.*`: `pthread_kill`, `raise_signal`, `pidfd_send_signal`, `sigqueue`, `alarm` and
  `setitimer`;
- socket connect, bind, listen, `send*` and `sendfile`; `create_connection` and `create_server`; and
  `ssl.create_connection`;
- `fcntl.flock`/`lockf` and the lock commands of `fcntl.fcntl`; `resource.prlimit`;
- any open of the real common lock;
- any write, mkdir, chmod, unlink or rename under the worktree or `/tmp`.

`resource.setrlimit` is recorded and never applied. Signal "delivery" in the probes is a direct call to the adapter's
own installed `interrupt_signals` handler, `signal.getsignal(n)(n, None)`. No signal is ever sent. Every run used
`python3 -I -B -W error::ResourceWarning`, with `TMPDIR`, `GOTMPDIR` and `CLAUDE_CODE_TMPDIR` set to the R5 cache, and
`p6.TMP` pointed at that cache. `test_profile_external.py` was not run.

**Results.** The author's evidence and R5's executions are kept separate.

| Run | Result | Evidence |
|---|---|---|
| Author, as claimed | 62/62 tests; 68 guarded mutant processes, 58 kills, 0 trips | `tests-v5.log` `27a86037…`, `mutants-summary.txt` `8a9d85b5…` (hashes verified; not re-derived) |
| **R5:** author's unmodified suite under R5 guards | **62/62 OK, 0 trips** | `logs/tests-1.json` `1f22afe7…` |
| **R5:** alias scan | **empty** | `logs/scan-1.json` `4f79ab26…` |
| **R5:** small-JSON facts (four v5 plans, v5/v4 sidecars) | All budgets recompute exactly (below) | `logs/facts-1.json` `2db6ede9…` |
| **R5:** 7 own probes (only the author's mock-provenance fixtures are reused) | **7/7 pass, 0 trips.** One probe is characterization and records findings R5-1 and R5-2. | `logs/probes-1.json` `ba7e4901…` |
| **R5:** mutants against the author's suite: R4's 25 and R3's X01–X23 (patterns read verbatim from `review_r4.py` and `review_r3.py`), plus 17 new R5 Y-mutants | Clean baseline first; 0 trips in every run (see "Mutants") | `logs/mutants/summary.txt` `c8222c4a…` |
| **R5:** the 17 Y-mutants against R5's probes | 8 killed (see "Mutants") | same file; `probe-detail.txt` `9d56b55b…` |

**Delta identity.**

- `reverse_delta.py` reverses the six `swap()` edits of the author's `edit_r4.py`, in memory, on the v5 source. The
  result hashes **exactly** to R4's settled v4 SHA: `c6f1629c…`, 82,367 B (`logs/reverse-delta.json` `c4e56668…`).
- So these six edits are the complete v4→v5 source change. Every other line is byte-identical to what R4 reviewed.
- The edits are:
  - R4-1: `finish_analysis` completion rule, the `end_analysis` except clause plus `seal_incomplete`, and the
    `analyze_command` finalization;
  - R4-2: the `disjoint_output` call and its function;
  - R4-3: the artifact count and name binding.

**Preserved failed attempts.** I deleted or overwrote nothing.

1. **`run_mutants.sh` attempt 1** (`logs/mutants-run.log` `06935690…`). The Y-name listing used `ast.literal_eval`.
   That fails because `Y_MUTANTS` references named constants, so the attempt ran no Y-mutants. The R4 and R3 lists
   ran normally. Y-mutants were then run by `run_mutants_y.sh` (`logs/mutants-run-y.log` `e004dbe5…`).
2. **Y04 × probes attempt 1** (`logs/mutants/probes/Y04-v4-lifecycle-restored.log` `48ca3e60…`) exited 130. Y04
   restores the v4 post-unlock write, and that write re-entered my final-save fault injector, so the interrupt
   escaped the runner. This is a runner artefact, **not counted as a kill**.
   - Attempt 2 omits that one probe (`logs/mutants/probes-Y04-attempt2.json` `9d3dc628…`).
   - In attempt 2, all 8 end-check cases flag the post-unlock write `held:false`, with 0 trips. That is a genuine
     kill.

**Not run:**

- any native CLI, helper, tool `--version`, readelf, compiler, build, perf, strace or heaptrack;
- any fixture, meter, network, real flock, real signal, wait or token;
- any heavy-binary or toolchain hashing.

Every statement here about real tool formats or speed comes from source, not from observation.

**Before and after.** `hashset.sh` (`04ca5e85…`) covers:

- the adapter, its tests, the plan and R1–R4;
- the eight harness modules, `meter.c`, the budgets and the parent;
- the v1–v5 sidecars;
- the v3 and v4 logs, and **every file** in `profile-adapter-v5`, including its plans, tools, logs and mutant outputs;
- R3's and R4's runners, R4's README and all R4 logs;
- the `harness/__pycache__` listing;
- `stat` of the lock file;
- `git status`.

`hashes-before-full.txt` equals `hashes-after-full.txt` (`004750c0…`, 457 lines). No `p6-profiles-test-*` directory
remains. The lock file's mtime and atime are unchanged, so it was never opened.

| File | SHA256 (verified) |
|---|---|
| `harness/phase6_profiles.py` (86,099 B) | `d28454d66c7452f076fe7b5261a25d58cb907b390d1cc9c31b92e45525acb777` |
| `harness/test_phase6_profiles.py` | `6c8ecf7f3d422e7de1fc559a32d66a5289c091d85124a7c33d6e868fe2219b27` |
| `docs/phase6-profiling-plan.md` | `7964ad73ed11b5936e0b38e8809c071527458f7c1e9891d6513e53d7044ff580` |
| `profile-adapter-v5/profile-sidecar.json` | `65a3f285d01febf23ce0d3cd2ed107a128938ebc75781765a8a00be294a8b602` |
| `profile-adapter-v5/hashes.txt` | `015589d3…`. Every line matches the files on disk. |
| R4 review | `cb5a18be…` (unchanged) |

**Sidecar source binding (small JSON only).**

- The v5 sidecar's eight harness hashes equal the files on disk. `phase6_profiles` is `d28454d6…`.
- Against v4, the **only** differing top-level key is `harness`, and within it only `phase6_profiles`.
- The schema is still `pb-phase6-profile-sidecar-v4`. The source declares sidecar, report and analysis schemas v4.
- Tool aliases are unchanged (`/bin/sh` → `/usr/bin/bash`; the rest are identity). Evidence: two
  `current_supporting`, one `historical_not_certifying`.
- All four v5 plans have `launched:false`. Each plan's harness equals the sidecar's, and each names the v5 sidecar
  SHA.
- Budgets recompute exactly:
  - perf 1371 = 2 + 390 + 926 + 53;
  - strace 1330 = 2 + 390 + 906 + 32;
  - strace-writes 1341 = 2 + 390 + 912 + 37;
  - heaptrack 832 = 2 + 325 + 463 + 42.
- The schedules are 4 controls + 2 captures, or 2 + 1 for heaptrack (Rust only).

## Verdict

### (A) Tiny functional probe — SOURCE + MOCK readiness: **READY (no code blocker)**

Readiness covers a separately authorized one-row, one-repetition run per mode, with functional outputs only. This is
**not** a quiet-window, token or lock authorization.

All R4 findings are closed in code and tests (table below). R5-1…R5-4 are new, and all are LOW or below. None of them
affects any of the following:

- lock ordering or the busy-lock zero-work refusal;
- deadlines, caps or child cleanup;
- the exit code;
- the final journal written inside the lock;
- the capture side.

If root keeps its "all corrections first" policy, R5-1 and R5-2 are two to three lines each, and R5-4 is one subtest.

### (B) Larger interpretation readiness: **still BLOCKED**

Interpretation stays blocked until all of the following hold:

1. **Functional probes have established the host facts:**
   - real perf `--stats` / `--no-skip-empty` format;
   - `perf record -q` exit status and stderr;
   - strace and heaptrack exit status and stderr;
   - whether `heaptrack_print --version` is accepted;
   - `kptr_restrict`;
   - perf artifact size and samples at 999 Hz `dwarf,16384`;
   - `perf report` CPU time against the 180 s step.
2. **Non-perf adequacy criteria have been proposed from real outputs and reviewed separately.** Until then, non-perf
   modes intentionally give exit 3 and are never interpretable. That is correct.
3. **The analysis reserves have been scaled from real sizes:** gzip and `perf report` CPU, and quota-blind headroom
   (R4-6, documented only).
4. **R5-1 and R5-2 are fixed.** They are cheap. Today, positive status text can still appear in stdout (R5-1) or in a
   saved role row (R5-2) in two narrow post-end-check failure windows.

## R4 dispositions (R5-executed unless marked "author")

| ID | Status | R5 evidence |
|---|---|---|
| **R4-1** end-check interrupt; post-unlock write | **Closed** (residuals R5-1, R5-2 lie outside the end-check window) | See "R4-1 detail" below. |
| **R4-2** `--out` inside the capture tree | **Closed** | See "R4-2 detail" below. |
| **R4-3** artifact name and count unbound | **Closed**; INFO residual R5-3; test gap R5-4 | See "R4-3 detail" below. |
| **R4-4** non-causal "artifact elsewhere" | **Closed** | Both author tests now forge into `self.root/'out'`, with an unchanged-copy control that exits 0. R5: an unchanged copy **inside** the root gives exit 0; the same copy **outside** still fails "outside its capture folder" (placement matters); "elsewhere" inside the root fails on the rewrite. R4's M01 is now killed by **both** "artifact elsewhere" subtests. |
| **R4-5** surviving regressions | **Closed** | R4's verbatim patterns: **M10, M11, M15, M09, M14, M03, M08 and M18 are each killed by exactly the focused test the plan names** (0 trips; the M18 kill comes from the read-size witness `test_stats_read_requests_at_most_the_cap`). R4's survivors are unchanged: M02, M04, M05, M07, M12, M20 and V12, as R4 classified them. M21 is now also killed by the R4-1 test. |
| **R4-6** reserve realism, quota | **Documented, not engineered (agreed)** | The plan's R4-6 text adds no invented rate or adequacy rule. EDQUOT does produce a non-zero exit with a non-positive journal (confirmed). But the plan's claim that the printed summary is "taken from the sealed state" is not true on that path: see R5-1. |
| R3 boundaries | **No regression** | R3's X01–X23: **22/22 applicable killed**, X15 pattern-absent, X15b and X15c killed. Busy-lock analyze: exit 2, zero events, no output. |

### R4-1 detail

The end-check matrix ran 8 cases (`r5_r4_1_end_check_interrupt_matrix`):

- the real installed handler for SIGINT, SIGTERM and SIGHUP, on both end checks;
- a `RuntimeError` and an `OSError` raised inside the end identity check;
- an end check followed by finish #1 interrupted;
- two cases where finish #1 and #2 are both interrupted, reaching the fallback.

**Every case gives:**

- exit 1;
- printed `analysis_incomplete` with exit 1;
- `state:final`, `quarantined:true`, failure evidence present;
- every entry `interpretable:false`; role counts 0 or absent; `interpretable_captures` 0;
- `end_identity_check.passed:false` present;
- exactly **one** `final` write, made **with the lock held**;
- **zero writes after lock exit**, and no `.partial-*` file left.

**Normal path:** journal states go `in_progress` → `final`, with one `final`, written while held.

**The completion rule is genuine defence in depth.** Y01, Y03, Y05, Y06, Y07 and Y17 are each killed by an author
test. Y02 is killed by two. The v4 lifecycle restored (Y04) is killed by four author subtests, and by R5's matrix
(attempt 2).

### R4-2 detail

`r5_r4_2_out_disjoint_matrix` ran 14 refused cases:

- a child of the capture root, of an entry folder, and a deep child of an entry folder;
- symlink alias to the root, symlink alias to an entry folder, and a chained alias;
- an alias to the root's parent, then down into the capture tree;
- a `..` path;
- the sidecar, parent, fixture, meter, rust-symbolized and go-symbolized folders.

**Every refused case gives:**

- exit 2, with a "disjoint" error;
- **no mkdir** and **0 tools**;
- refusal inside the lock, after only the report read. The probe's `reads` column also lists `sidecar.json`, but that
  is R5's own argument hashing, not the adapter.

After all of them, the capture tree's bytes and listing are unchanged. Controls: a disjoint sibling, and a sibling
reached through an alias of the root's parent, both give exit 0. Y09, Y11 and Y12 are killed by the author's tests.
Y10 is equivalent; see "Mutants".

### R4-3 detail

`r5_r4_3_artifact_binding_matrix` ran across all four modes, with every forge placed inside the capture root.

- **Refused, each with exit 1 and 0 tools:**
  - the launcher script as the artifact;
  - the same artifact listed twice;
  - another capture entry's artifact;
  - a capture artifact copied onto a control.
- **Controls** (unchanged copy): perf exit 0, heaptrack 3, strace 3, strace-writes 3.

## New findings (most severe first)

### R5-1 — LOW (confirmed) · fix before interpretive use; not a tiny-probe blocker
**A failure at or after the final save can still print a positive interpretation.**

**Where:** `analyze_command`, final `save()` at `:1231`, outer except at `:1232-1237`, print at `:1238-1242`.

- The printed status is guarded by `complete or state == 'final'`.
- But `state` is set to `final` in memory by `finish_analysis` **before** the final `save()`.
- So if that save raises, the outer except sets only `failure` and `exit_status:1`, and the print still shows the
  positive status. The save can raise from EDQUOT or ENOSPC, or from a signal that lands after a clean `end_analysis`
  returns and before or inside `write_json`.
- Y08 (status printed unconditionally) **survives both suites**. It is equivalent: the guard never changes the output,
  because whenever an interpretation exists, `state` is already `final`.

**Reproduction** (`r5_r4_1_signal_after_interpretation_before_save`; perf, all captures sufficient, end checks
passed):

| Fault at the `final` write | Exit | `analysis.json` on disk | Printed |
|---|---|---|---|
| SIGINT through the installed handler | 1 | `state:in_progress`, no interpretation | `"exit_status":1, "interpretation":"all_roles_interpretable", "failure":"KeyboardInterrupt: signal 2"` |
| `OSError(122, EDQUOT)` | 1 | `state:in_progress`, no interpretation | `"exit_status":1, "interpretation":"all_roles_interpretable", "failure":"OSError: [Errno 122] Disk quota exceeded"` |

**Impact:**

- The exit code and the on-disk journal are honest. Both end checks did pass here, so this is an **unpersisted**
  positive, not an unverified one.
- But it contradicts the plan's R4-1 statement that "the printed summary is taken from the sealed state, so a later
  failure shows `analysis_incomplete`".
- A coordinator that scrapes stdout instead of the exit code or the journal would cite a result that has no final
  journal behind it.
- The quota case is real on this host class (R4-6).

**Narrow fix** (about 3 lines): set `sealed = False` before the `with`, and `sealed = True` immediately after the final
`save()`. Print `result['interpretation']['status']` only `if sealed`, otherwise `analysis_incomplete`.

**Test:** make `p6.write_json` raise `OSError(122)` on the `final` write. Expect exit 1, printed `analysis_incomplete`
and `.partial-*` absent.

### R5-2 — INFO (confirmed; two-signal race) · cheap; before interpretive use
**The repeated-interrupt fallback does not reset `roles`.**

**Where:** `analyze_command`, `:1225-1230`.

- The fallback rewrites `state`, `analysis_complete`, `exit_status`, `interpretation`, and every entry's
  `interpretable`.
- It leaves `result['roles']` from any earlier `finish_analysis` that **completed**.

**Reproduction** (same probe):

- First, `finish_analysis` #1 (inside `end_analysis`'s `finally`) runs fully with both end checks passed. It sets
  `roles[*].interpretable_captures = 2`.
- Then the installed SIGINT handler fires before `end_analysis` returns.
- Then `seal_incomplete`'s `finish_analysis` #2 is itself interrupted.

The final journal, written inside the lock, has:

- exit 1, `analysis_incomplete`, entries all false, `quarantined:true`;
- but **`roles: {go-symbolized: 2, rust-symbolized: 2}` interpretable captures**.

The author's test "every interpretation attempt interrupted" interrupts before #1 runs, so `roles` is absent there.

With #2 allowed to run, the reseal is fully negative (also in the same probe), so `seal_incomplete` itself is sound.

**Impact.** This needs two signals in microsecond windows after a fully verified analysis. The top-level status,
entries and exit are all negative. Only a role-row reader is misled.

**Fix:** one line in the fallback: `result['roles'] = {}`, or zero each row's `interpretable_captures`. Extend the
author's case with `after={1}` semantics.

### R5-3 — INFO (source/doc precision)
**An interpretable capture with zero artifacts passes the binding.**

**Where:** `:1303-1305`.

- The rule is `artifacts and (…)`, so an empty list passes.
- `analysis_entry_s` (`:1083`, called at `:1356`) then fails with `IndexError: list index out of range`.
- All four modes: exit 1, 0 tools, `analysis_incomplete`.

This fails closed. But the plan says "a capture entry names **exactly one** artifact". Today it is "at most one",
and a forged report yields a traceback-class message.

**Optional fix:** require `len(artifacts) == 1` when `item['phase'] == 'capture' and entry.get('interpretable')`.

### R5-4 — LOW (tests) · source acceptance
**The heaptrack name rule is untested.**

- Y16 makes the `heaptrack` prefix check always pass. It **survives all 62 author tests**, because every R4-3 forge is
  perf.
- R5's heaptrack "launcher script" forge kills it: today that forge gives exit 1, "artifact name 'launch' is not the
  heaptrack artifact".

**Fix:** add one heaptrack subtest to `test_artifact_must_be_the_single_mode_artifact`.

## Mutants (R5-executed; a kill requires a clean baseline, 0 guard trips, and a failing test that passes on baseline)

**Author suite.** The baseline is 62/0/0. Totals: **54 killed, 10 survived, 1 pattern-absent (X15).**

| Group | Result |
|---|---|
| R4's 25 | 18 killed. Survivors M02, M04, M05, M07, M12, M20 and V12 match R4's equivalent or masked set. |
| R3's X01–X23 | 22 killed; X15 pattern-absent. |
| R5 Y01–Y17 | 14 killed; Y08, Y10 and Y16 survive. |

**R5 Y-mutants:**

| Mutant | Author suite | R5 probes | Note |
|---|---|---|---|
| Y01 end except removed | killed | survived | Masked end-to-end by the completion rule; the unit-level test kills it. |
| Y02 / Y03 completion ignores identity / harness end check | killed | survived | Unit test. |
| Y04 v4 lifecycle restored | killed (4) | killed (attempt 2) | |
| Y05 seal without reinterpretation | killed | killed | |
| Y06 fallback keeps entry flags | killed | survived | The author's kill is incidental: a KeyError on an absent `interpretable`. |
| Y07 no repeat fallback | killed | killed | |
| Y08 printed status unconditional | survived | survived | **Equivalent** (no behaviour difference; the guard never changes output). See R5-1. |
| Y09 disjoint equality only | killed | killed | |
| Y10 disjoint uses unresolved `out` | survived | survived | **Equivalent:** `p6.safe_path` already resolves `--out` (`:1188`), so alias protection rests on the frozen `safe_path`, and `resolve()` here is defence in depth. |
| Y11 disjoint capture root only | killed | killed | |
| Y12 disjoint after mkdir | killed | killed | |
| Y13 count unbound | killed | killed | |
| Y14 control artifact allowed | killed | survived | The parent check still refuses it, with a different message. |
| Y15 name unbound | killed | killed | |
| Y16 heaptrack prefix unbound | **survived** | killed | R5-4 |
| Y17 end except does not quarantine | killed | survived | Unit test. |

## Next smallest operational proposal — PLANNING ONLY, not authorized, not run

This is unchanged in shape from R4. Only the sidecar changes, to v5.

**Capture.** One mode per separately coordinated, bounded quiet window, under the shared nonblocking lock with a
fresh coordinator token. Order: strace, then perf, then strace-writes, then heaptrack.

```
python3 harness/phase6_profiles.py capture --out /home/ajs/.cache/pb-phase6/profile-probe-<mode>-one-row-01 \
  --parent-sha256 a47093f4a3ea785a310f7ca1baa92444deaf7e21db5ba7db72dc897ddc3d047b \
  --sidecar /home/ajs/.cache/pb-phase6/profile-adapter-v5/profile-sidecar.json \
  --sidecar-sha256 65a3f285d01febf23ce0d3cd2ed107a128938ebc75781765a8a00be294a8b602 \
  --scenario one-row --mode <mode> --repetitions 1 --control-repetitions 1 \
  --window-budget-s <budget> --quiet-window-token <coordinator token>
```

**Analysis.** Run it **as its own separately coordinated lock slot**, because it executes `perf report` and the
heaptrack tools, takes the lock, and has no token. Only analyze a report with `passed:true`. `--out` must lie
**outside** the capture tree and provenance folders: v5 now refuses it otherwise, with exit 2. Judge the result by its
**exit code and `analysis.json`**, not by the printed summary (R5-1).

```
python3 harness/phase6_profiles.py analyze --report <capture>/report.json --report-sha256 <sha256 of it> \
  --sidecar-sha256 65a3f285d01febf23ce0d3cd2ed107a128938ebc75781765a8a00be294a8b602 \
  --out /home/ajs/.cache/pb-phase6/profile-probe-<mode>-one-row-01-analysis --budget-s <analysis budget>
```

**Budgets.** The capture's required time is the v5 plan's figure, recomputed by R5. The analysis range is R5's offline
estimate, from a tiny artifact up to an artifact at its cap.

| Mode | Capture required | `--window-budget-s` | Analysis required (tiny … cap) | `--budget-s` | Expected analysis exit |
|---|---|---|---|---|---|
| strace | 1330 s | 1450 | 96 … 96 s | 200 | 3 |
| perf | 1371 s | 1500 | 978 … 1062 s | 1100 | 3 likely (one-row is probably below the 1000-sample floor); 0 or 4 possible |
| strace-writes | 1341 s | 1450 | 96 … 116 s | 200 | 3, or 1 if traced FD 1 ≠ stdout bytes |
| heaptrack | 832 s | 950 | 506 … 547 s | 600 | 3 |

These outputs are functional only: formats, exit codes, stderr and sizes. In every mode, exit 1 is a functional
failure, and its journal is the evidence.

## Unresolved objections

| ID | Item | Gate |
|---|---|---|
| R5-1 | The printed summary can show a positive status when the final save fails or is interrupted (exit and journal honest) | interpretive (about 3 lines) |
| R5-2 | The repeated-interrupt fallback keeps the `roles` interpretable counts | interpretive (1 line) |
| R5-3 | A zero-artifact interpretable capture passes the binding and fails later with `IndexError` | optional precision |
| R5-4 | The heaptrack artifact-name rule is untested (Y16 survives) | source acceptance (1 subtest) |
| R4-6 | gzip and `perf report` CPU; quota-blind headroom | interpretive scale, after real sizes |
| host | Real perf, strace and heaptrack formats, exit codes and stderr; `heaptrack_print --version`; `kptr_restrict`; samples and size; `perf report` time | separately authorized tiny functional probes |
| non-perf adequacy | No criterion by design | separate review after real outputs |
| — | Go source-to-commit equality rests on the Phase 7 attestation | attested, not verified here |
