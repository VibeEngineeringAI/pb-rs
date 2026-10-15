# Phase6 profiling adapter — independent review r1

Reviewer: Opus 5.5 review sub-agent. Scope: `harness/phase6_profiles.py`,
`harness/test_phase6_profiles.py`, `docs/phase6-profiling-plan.md`, the
`profile-adapter-v1` cache (sidecar, guarded runner, log, one plan read in full),
the symbolized build manifests, and the reused APIs in `phase6_workloads.py`,
`profile_external.py`, `run.py` and `manifest.py`.

This is not whole-phase acceptance, and it is not an acceptance of the adapter.

## Execution limits (read first)

- **Nothing was executed.** Every Bash call failed with EDQUOT on Claude Code's own
  temp directory (`/tmp/claude-1000/.../tasks`). The brief forbids manipulating
  `/tmp`, so I did not clean it. As a result I ran no `sha256sum`, no Python, no
  unit test and no mock probe. I did not run `test_profile_external.py`, any
  product/fixture/meter/native helper, cargo/go, perf/strace/heaptrack/readelf,
  `--version`, any lock or token, or any timing or pressure work.
- **Hashes were not independently recomputed.** The settled hashes are taken from
  the brief. The only corroboration is that
  `profile-adapter-v1/plan-heaptrack-profile-dictionary-zstd/plan.json` (written
  by the author's guarded run) records `phase6_profiles.py` `4bb8a2d4…`,
  `phase6_workloads.py` `e2e83fa1…`, `profile_external.py` `a0f0d7a3…`,
  `run.py` `b280d129…`, the sidecar `52a3f01c…`, the parent `a47093f4…`, the
  fixture `c85f63f9…`, Rust release `f1fb7a9e…` and Go release `fae8411f…`. That
  plan lists exactly **47** tracked identities, which matches the plan doc.
- **Before/after:** I used Read only on every reviewed or shared file. My only
  writes are this file and `/home/ajs/.cache/pb-phase6/profile-review-r1/`
  (`README.md`, `repro_r1.py`).
- **Reproductions are prepared, not run.** `repro_r1.py` installs hard-fail guards
  on `subprocess.*`, `os.system/popen/fork/forkpty/posix_spawn[p]/exec*/spawn*/kill/killpg`
  and `fcntl.flock/lockf` **before** importing reviewed code. It reuses the
  author's mock fixtures, but none of the author's tests. Each repro asserts the
  defective behaviour traced below. A pass confirms the finding.
- **Tests:** by source count, the author's suite has **15** test methods. I did
  not run them, so I cannot independently confirm "15/15 pass".
- No quiescence request was received during this review.

## Verdict by gate

**Before tiny functional adapter probes** (for example, one capture per mode on
`one-row` to learn the real perf/strace/heaptrack exit status, stderr and
`--stats` format): **no code blocker**. Four operational preconditions apply:

1. **Abort signal.** Use SIGTERM for abort, or make sure SIGINT is not inherited
   as ignored (F9).
2. **Window release.** Do not release the window or issue another token until the
   process has exited, because the final rehash runs after the lock is released (F8).
3. **Interpretation.** Treat every probe report as functional only. Analysis
   admission is unsafe (F1).
4. **Control failures.** Expect that a failed control does not stop the window (F3).

**Before interpretive profiles:** F1 and F2 are blockers. F3–F6 must be fixed or
explicitly ruled on.

## Findings (most severe first)

### F1 — HIGH (confirmed by source) · blocker before interpretive profiles
**Quarantined, incomplete or failed captures are still analyzed, labelled sufficient, and exit 0.**

- `evaluate` (`phase6_profiles.py:349-350`) sets the per-entry `interpretable` flag
  from phase, contract match, errors and artifacts only. It never consults
  `complete`, `quarantined`, `end_identity_check` or the failed controls.
- The end-identity failure sets `quarantined` and `failure` (`:476-479`), but no
  entry is changed.
- `analyze_command` admits entries by `e.get('interpretable')` alone (`:603`).
  It records `capture_passed`/`capture_complete` (`:608`), but the exit code
  (`:634`) and `availability` (`:631`, `:637-655`) ignore both.
- **Causal path:** a tracked file (for example, the Rust build log) changes during
  the window. The end check quarantines the report (`passed=false`). Analyze
  still processes all 4 captures, reports `sufficient_for_descriptive_stacks`
  and returns 0. The same path applies after a window stop or a collector
  exception that leaves earlier captures interpretable.
- **Repro:** `repro_A`.
- **Minimal fix:** refuse to analyze (or label the whole output `quarantined`/
  non-interpretable, with non-zero exit) unless `report['passed']` is true. A
  narrower option requires `complete`, a passed end check, and a valid control
  for the same role. Re-verify the report's `tracked_identities` at analysis start.

### F2 — HIGH (plausible; format must be confirmed by a functional probe) · blocker before interpretive profiles
**`parse_perf_stats` can turn real lost events into zero.**

- The regex `^\s*{kind} events:\s*(\d+)\s*$` (`:552`) requires the line to end
  right after the count.
- From my reading of perf source (not executed here), recent perf has two
  relevant behaviours:
  - It prints aggregated rows as `SAMPLE events: N  (x.x%)`, then per-event blocks
    (`cycles:u stats:`) that have a bare `SAMPLE` count and, if nonzero,
    `LOST_SAMPLES`. Ring-buffer `LOST` appears only in the aggregated block.
  - It skips zero rows by default (`--skip-empty`).
- **Consequence:** the aggregated `SAMPLE` row with its percent suffix does not
  match, so the per-event bare row is used silently. The aggregated `LOST` row
  with its percent suffix never matches, giving `lost_events=None`. Then
  `availability` treats `None` as 0 (`:650`) and can report
  `sufficient_for_descriptive_stacks` despite nonzero chunk loss.
- Chunk loss at 999 Hz with `dwarf,16384` stacks is realistic. With `-q`, it is
  not guaranteed to change the capture's stderr contract.
- **Repro:** `repro_D`.
- The test fixture text (`test_phase6_profiles.py:338-339`, `:375`) uses an
  invented suffix-free format, so it does not exercise this.
- **Minimal fix:**
  - Parse only the `Aggregated stats:` block, up to the first `… stats:` header.
  - Accept an optional `\(\s*[\d.]+%\)` suffix.
  - Treat an absent `LOST` row as **unknown**, not zero, unless you pass
    `--no-skip-empty` (if that perf version rejects it, fail closed).
  - Save the exact `perf --version` with the stats output.
- If the first functional probe shows a different format, this finding narrows,
  but the "absent ⇒ 0" rule remains unsafe.

### F3 — MEDIUM (confirmed) · doc/code mismatch; fix or rule before interpretive profiles
**A failed control neither stops the window nor blocks interpretation.**

- The plan says (`phase6-profiling-plan.md:117-118`): "If its first release
  control fails, that window stops at controls."
- The code aborts only on `collector_exception` (`phase6_profiles.py:448-449`).
  An invalid control continues through all captures. For profile-only scenarios
  that is up to 2490 s of a negotiated window.
- `evaluate` takes the reference from the **first valid control of any role**
  (`:341-342`). So with `go-release` invalid, Go captures that match the
  `rust-release` contract are still `interpretable`. Analysis then admits them
  (see F1).
- This is the "controls silently waived" case from the brief.
- **Repro:** `repro_B`.
- **Minimal fix:**
  - After the control phase, stop before captures unless every planned control is
    valid and their contracts are mutually comparable.
  - Alternatively, correct the doc and gate `interpretable` on the same-role
    control.

### F4 — MEDIUM (confirmed) · before interpretive profiles
**Adapter and harness code identity is recorded, not bound.**

- The sidecar has no harness field (`:521-524`, and the sidecar file itself).
- `harness_identities()` (`:162-164`) hashes whatever bytes are on disk when
  `verify` runs. Only changes *during* the run are detected (`:403-405`, `:476`).
- So a capture run with an unreviewed edit of `phase6_profiles.py`, the collector,
  `profile_external.py`, `run.py`, `manifest.py` or `diagnose.py` passes. The
  report only records the different hash.
- Transitively imported `cases.py` (`run.py:26`) and `phase3_workloads.py`
  (`diagnose.py:17`) are not hashed at all.
- The hashed file is also not proven to be the code that was imported (low).
- **Repro:** `repro_I`.
- **Minimal fix:**
  - Bind expected harness SHA256s (the reviewed set) in the sidecar, or take them
    via a CLI argument, and compare them in `verify`.
  - Add `cases.py` and `phase3_workloads.py`.
- Until then, the coordinator must compare `tracked_identities` against the
  reviewed hashes by hand before accepting any report.

### F5 — MEDIUM (confirmed by source) · interpretation contract ambiguity
**The availability floor is per role in aggregate, but stacks are per capture.**

- `availability` sums `SAMPLE` events across a role's captures (`:649`) and
  compares the sum with 1000 (`:653`).
- The only stack outputs are separate per-capture `perf report` files (`:589`).
- **Example:** three captures of 400 samples each are labelled sufficient
  (1200 ≥ 1000), yet no artifact contains ≥1000 samples. Interpreting any one
  report would be an "insufficient stack interpretation".
- **Minimal fix:** apply the floor per capture, or produce one bounded aggregate
  report per role and label only that report. Also document which applies.

### F6 — MEDIUM (confirmed) · report availability and exit code
**Missing roles disappear and an empty analysis succeeds.**

- Non-interpretable captures are dropped from `analysis.json` without a reason
  (`:603`). `availability` only has rows for roles with at least one analyzed
  entry (`:641-648`).
- A role whose captures all failed has no row at all, rather than `unavailable`
  (`repro_E`).
- With zero interpretable entries, analysis is `complete=true` and exits 0
  (`:627`, `:634`; `repro_C`).
- **Minimal fix:**
  - Emit a row for every planned capture role, including `analyzed=0` and the
    planned/executed/interpretable counts.
  - Record each skipped entry and its reason.
  - Return non-zero when nothing was analyzable.

### F7 — LOW-MEDIUM (confirmed)
**A `strace-writes` FD1/stdout mismatch is only a flag.**

- In `profile_external.py:235-236`, a mismatch was a hard error. In
  `phase6_profiles.py:584-585`, it is a boolean, and the exit code (`:634`)
  ignores it.
- A real FD1 mismatch is an anomaly that should not leave the run looking clean.
- **Repro:** `repro_H`.
- **Minimal fix:** record the mismatch as the entry's `error`.

### F8 — LOW-MEDIUM (confirmed)
**The final identity rehash runs after the lock is released.**

- `quiet_lock` exits at the end of the `with` block (`:469-471`). The end check
  in `finally` (`:476`) then hashes about 47 files, including two ~100 MB
  symbolized binaries and up to two ~70–100 MB release binaries, plus the
  launchers, outside the lock.
- The parent campaign rehashes inside the lock (`phase6_workloads.py:1289`).
- **Effect:** IO/CPU can land in a following window, and a mutation in the gap
  is attributed to this run.
- **Repro:** `repro_G`.
- **Minimal fix:** run the end check inside the lock, as the parent does, and
  keep the `finally` recheck.

### F9 — LOW-MEDIUM (confirmed by source and CPython semantics) · operational
**SIGINT abort can be silently disabled.**

- `interrupt_signals` remaps only SIGTERM and SIGHUP (`:315`).
- CPython leaves SIGINT ignored if it was ignored at startup. That is the case
  for a background job started from a non-interactive shell.
- In that case the documented "abort only with SIGINT" (plan `:162`; report
  ownership note `:391-392`) does nothing.
- **Repro:** `repro_F`.
- **Minimal fix:** also install the SIGINT handler in `interrupt_signals`, or
  document SIGTERM as the abort signal.

### F10 — LOW-MEDIUM (confirmed)
**Output bounds are checked only after the fact, and some window and analysis time is unbudgeted.**

- **Artifact size.** It is checked after capture (`:443`). Free disk space is
  checked only before launch (`:417-422`). Nothing stops perf, strace or
  heaptrack from writing more than 4 GiB / 1 GiB / 4 GiB while running; only the
  180 s child timeout bounds them. That matters on a shared cache filesystem
  while Phase7 deadlines are active.
  - A small, possible mitigation: `ulimit -f` in the launcher (`:255`). The CLI
    writes only to pipes, so only the profiler artifact would be capped
    (fail-closed through SIGXFSZ).
- **Unbudgeted in-window work.** `profiles.artifacts` hashes artifacts of up to
  4 GiB (`:442`), and there is a 100 MB executable rehash per launch (`:425`).
  The per-sample 30 s reserve absorbs these only implicitly.
- **Unbounded analysis work.** For heaptrack, interpreting and then gzip-copying
  in Python (`profile_external.py:113-120`) writes an unbounded plain-text
  intermediate. Neither the gzip time nor the disk use is budgeted
  (`phase6_profiles.py:610`).
- **Probes.** stdout is checked only after the fact (`:267`, 256 MiB), and stderr
  is neither bounded nor recorded (`:262`). This is low risk for readelf.

### F11 — LOW
**Analysis provenance is narrower than capture provenance.**

- **Checked once, not at the end.** Tools and symbolized binaries are checked
  once at start (`:611-613`), and nothing is rechecked at the end. perf report
  symbolizes from the binary path, so a mid-analysis replacement is undetected.
- **Path-based artifact checks.** Artifact checks are before/after by path
  (`:578`, `:582`, `profile_external.py:107/124`). A swap-and-restore during a
  tool run is not detected.
- **Non-isolated environment.** perf/heaptrack analysis inherits `os.environ`
  (`:563`, `profile_external.py:115/123`). That includes the user's `HOME`
  (`~/.perfconfig` and the `~/.debug` build-id cache) and `PATH`.
- **Report schema.** The report `schema` is not checked; only `kind` is (`:600`).

### F12 — LOW (confirmed)
**One bound evidence file describes a different Rust binary.**

- The sidecar's evidence `docs/evidence/phase6/profile-symbols-verification.json`
  (`469d1f35…`) certifies Rust **r4** `b819c8aa…` (manifest `43cba6ed…`), not the
  r6 `841d2a0c…` that is profiled.
- It is hashed but not used logically, and the live probe re-checks r6. Even so,
  binding it implies verification that it does not provide.
- **Fix:** remove it from the evidence list, or annotate it as historical.

### F13 — LOW
**Failed-probe evidence is incomplete in the report.**

- `run_probe` raises before the record is appended (`:267-268`). The report
  therefore lacks the failing probe's return code and stdout identity; only
  files remain on disk.
- A `TimeoutExpired` also leaves no record.
- **Fix:** append the record with `returncode` and `error`, then raise.

### F14 — LOW (disclosure)
**Tool identity covers only the executable file.**

- Shared libraries are not bound: perf's libdw/libunwind/libtraceevent/libpython,
  the libunwind used by heaptrack preload, and readelf's compression libraries.
- `perf_event_paranoid` is read only at preflight (`:374`), not inside the lock.
- These are acceptable if the plan's limits section states them explicitly.

### F15 — LOW (tests)
**Test guards and coverage have gaps.**

- Guards are installed in `setUp` after import (`test_phase6_profiles.py:17-18`,
  `:49-53`).
- `os.system`, `os.exec*`, `os.spawn*` and `os.posix_spawnp` are not guarded.
- No test covers F1–F9.
- The perf-stats fixtures are not in the real perf format (F2).

## Hypotheses refuted or closed

- **Capture after cancellation: refuted.** In `sample`, the BaseException catch
  (`:327-336`) converts the interrupt into `collector_exception`, and `captures`
  then raises (`:448-449`). Collector cleanup runs first (`phase6_workloads.py:333-336`,
  `:902-903`). Probe children are killed by `subprocess.run` on interrupt. The
  only exception is the ignored-SIGINT case (F9).
- **Resolved-path execution: closed.**
  - Tools are bound by alias, resolved path and hash (`:150-159`).
  - Probes, the perf/strace argv, the heaptrack `env` swap (`:246-248`), the
    `LD_PRELOAD` path and the launcher shebang all use resolved paths.
  - The executable, shell and capture tools are rehashed before every launch
    (`:424-427`).
- **Same-byte JSON binding: closed for the adapter's inputs.** The sidecar,
  parent, both build manifests, Go provenance and release provenance go through
  `read_json_with_identity` (`:79`, `:169`, `:172`). Remaining second reads are
  low risk:
  - `saved_sections` (`:101-102`).
  - Frozen `bind_manifest`/`bind_selected_roles` (budgets, fixture manifest,
    candidate manifest). These are inherited and covered by the end rehash.
- **Selected role binding: closed.** Release controls go through
  `bind_selected_roles` against the parent `pb-go` and `build_flags.candidate.pb`.
  - Rust symbolized:
    - sources and rustc must equal the parent;
    - the candidate manifest copy is hashed;
    - argv is exactly `--profile profiling`;
    - `features == []` and `debug_symbols` are required.
    - `Cargo.toml` `[profile.profiling]` inherits release and adds `debug=2`. It
      uses the system allocator, so heaptrack can see allocations.
  - Go symbolized:
    - the commit must equal the parent oracle;
    - the toolchain must match `go1.25.4`;
    - the release and symbolized builds must share the same source tree SHA;
    - provenance must be embedded byte-equal.
    - The recorded command is `-trimpath -tags kqueue`, with no `-N -l`.
- **Scenarios: closed.**
  - Frozen definitions are unchanged. The added `scenario_kind` is outside
    `workload_sha256`, which hashes stream, `duration_s` and sink
    (`phase6_workloads.py:870-872`).
  - The profile-only definitions are deep copies with distinct names:
    - `profile-output-heavy`: 4096×128 rows.
    - `profile-dictionary-lz4` and `profile-dictionary-zstd`: replace mode,
      cardinality 64, 256 B values, 2048×64 rows.
    - All three have a 180 s timeout and are bounded.
  - Auth, ticket, framing and request validation reuse the unchanged `Fixture`
    and `run_sample`.
  - The output oracle is the fixture's independent expected-output hash
    (`output_expectation_issues`).
  - Functional acceptance of replace mode plus compression is still unproven, as
    the author disclosed.
- **Profiler exit status and stderr: fail-closed.** `run_sample` requires
  `termination == {'exit': 1}` and the COMMAND_FAILED/InvalidArgument stderr, and
  the contract compares `stderr_sha256` with every control.
  - If perf returns a different status or prints anything, the capture is
    invalid or contract-mismatched, never interpretable.
  - From source memory, `perf record` propagates the workload's exit status. That
    is unverified, and only a functional probe settles it.
- **Budget arithmetic: correct.** I recomputed 2490 / 1890 / 1650 / 1320 s from
  `preflight` (`:370-371`) and the schedule/probe counts, and they match the plan
  table.
- **Atomic incomplete reports: closed.**
  - `write_json` uses temp-then-`os.replace`, and output folders and launchers
    are created exclusively.
  - On a window stop, `complete=false` is set and samples are kept.
  - Finalization errors are captured (`:481-482`).
- **Dependencies: closed.** The adapter is stdlib-only, and no new dependency was
  added.

## Unresolved objections (current)

| ID | Item | Gate |
|---|---|---|
| F1 | Analysis ignores capture passed/quarantine/complete | interpretive (blocker) |
| F2 | perf `--stats` parsing; absent loss treated as zero | interpretive (blocker); resolve with functional probe output |
| F3 | Failed control does not stop window or block interpretation; doc says otherwise | interpretive; doc correction before any window |
| F4 | Harness code identity not bound to reviewed hashes | interpretive (or manual coordinator check) |
| F5 | Per-role aggregate floor vs per-capture stacks | interpretive |
| F6 | Missing roles absent; empty analysis exits 0 | interpretive |
| F7–F15 | As above | before final profile evidence |
| — | Real perf/strace/heaptrack exit, stderr, `--stats` format and `perf_event_paranoid`/`kptr_restrict` warnings under `-q` on this host | needs tiny functional probes |
| — | Go source-to-commit equality rests on the Phase7 attestation (no git run) | attested, not verified here |
| — | Hashes and 15 tests not re-run by this reviewer; `repro_r1.py` not executed | re-run when a shell is available |
