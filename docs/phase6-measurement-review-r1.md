# Phase 6 measurement review r1 (independent, pre-campaign)

Scope: the native Flight tail measurement tooling and protocol, reviewed before
any quiet window is used. No benchmark, profiler, quiet window, soak, build or
production edit was performed. The only file written in the worktree is this
document. Probe artifacts live under `/home/ajs/.cache/pb-phase6/review-r1/`.
Nothing here is performance evidence.

## Reviewed sources (sha256, worktree `feat/phase6-flight-tail`, HEAD `4a812d5` + dirty tree)

| File | sha256 |
|---|---|
| docs/phase6-measurement-plan.md | `be5c55c11171387850d9868508275d830b6b88dec398be85c5e4030eda939023` |
| docs/evidence/phase6/performance-budgets.json | `6a1191aeec6eba4857bda48fc5e463b6da1af91813db6a8a090e24a2923fbe95` |
| harness/phase6_workloads.py | `eb793889efb5a8f467de1cde2f64dcaa7f50d89ddfedc2c799244f4ff8b96252` |
| harness/test_phase6_workloads.py | `fad9c257408c3774acb8091e6472acde83a45de44d71f2ee7887755eaaff1cd9` |
| harness/run.py (shared, read only) | `b280d1295a4b351e6f2d838c56acaac58b2247bb77fc9881587bb7bbe849a076` |
| harness/meter.c (shared, read only) | `5d7ce9dc2fa65b1ec7018fb863d36fd78d8b8535762acadaefc3f2f4e0f070e9` |
| harness/fixture/phase6.go | `60e0ac5e4ea45c22e9f2c9fd519f66f9dfbbcf14ef3d5a448d461716b47e3a98` |
| harness/fixture/main.go | `d9025f74429d96cae9d49ec3a4cb633770aad7b44cb974f686548aca9e37bdb0` |
| harness/phase6.py | `f9f8ed51b42aee896b6f2fc11ff6eb806c9243cfb75f49f99b10f77e83a1f445` |
| harness/test_phase6.py | `33b7e13d3cf0720296fbce284f8aa28eed4a0314ead65e4de0fb6a672dfe7cb4` |
| harness/profile_external.py | `a0f0d7a315772ed933eae235b0c55850ce8b23da25132c6ccbafc9a4e0256a3c` |
| src/tail/session.rs | `e5ed7218891f6ebc1de8b3242d2e7493001088d3858cf247f1b540ec36228c9b` |
| src/tail/resources.rs | `4dfc4ed412b550b94b2de2f2e06bc0533e8e0358b582cd6f80c8e3d55545e94f` |
| docs/evidence/phase6/harness-review-r3-tests.json | `d761cbb91fad5a35cba1f4f0a2fbf6653fc0387990ecbec7ff816d880ad982f7` |

Binaries observed (identity only, none executed for timing):
`bin/pb-meter` `17c3b734…7f`, `baselines/pb-go` `fae8411f…14`,
`bin/pb-fixture` = `tmp/fx/pb-fixture-r2` `2d76fd06…7b`, `pb-fixture-r3`
`fdecd359…c9`, `pb-fixture-r4` `8237e34e…f0`.

## Tests and probes actually run

1. `PYTHONPATH=harness python3 -m unittest -v test_phase6 test_phase6_workloads`
   with `TMPDIR=GOTMPDIR=CLAUDE_CODE_TMPDIR=/home/ajs/.cache/pb-phase6/tmp`, Python
   3.14.7: **89 tests, 89 ok, 0 skipped, 0 failed** (4.7 s). That includes the
   real Go + immutable r2 low-cap reconnect regression and the native-meter
   observer tests. Log `review-r1/unit-tests.log`, sha256
   `c8e5754d4b2a8515fb70208a4a1d7a587b373dffa5e9e787219df2c06f4bb77f`.
2. `review-r1/probes.py` (sha256 `d94c632f…90bd`). It is pure Python with mocked
   `run_sample` and `quiet_lock`. It starts no CLI or fixture and makes no
   timing claim. Output `review-r1/probes.json` (sha256 `ee4f00f3…dd45`). The
   findings below cite it as P1–P9.
3. Fixture Go tests (`phase6_test.go`) were **not** run. The `go` shim in this
   shell has no version selected, and no fixture source changed in this review.

Note: the Claude Code `/tmp` quota was exhausted by unrelated sessions.
All command output was redirected to the disk cache. No `/tmp` artifacts were created.

## Verified as sound (no change requested)

- **Native resource attribution.** `posix_spawn` starts `pb-meter`, and the
  meter forks and execs the CLI. Elapsed time ends at `waitid(WNOWAIT)` before
  `/proc/PID/io` reads and `wait4` collects the rusage. Peak RSS, faults,
  context switches and block I/O come from the CLI's own rusage. The `ObserverNative` RSS-isolation test passes.
- **First-complete-record semantics.** The timestamp is taken immediately
  after the `os.read` that delivers the LF of a complete, parsed object.
  Partial objects and first bytes are tracked separately. It uses the same
  `CLOCK_MONOTONIC` as the meter, and the fixture summary does not gate it (tested).
- **Signal versus exit.** `{'signal': N}` is distinct and fails the required
  `{'exit': 1}`. The fatal InvalidArgument diagnostic and the fixture terminal
  are both required. No path accepts a finite-fatal stream as a successful live
  completion.
- **Framing and hash.** Both collector modes hash every byte. The full
  expectation (SHA256, bytes, rows, batches) is compared with the fixture's
  incremental Go v13 rendering. A later frame altered under hash-framing fails
  (tested). Oversized rows are bounded, and invalid rows are counted.
- **Exact-summary admission.** The checks cover auth/ticket coverage equal to
  DoGets, zero for each invalid counter, about==1/http==1, and timestamp ordering.
  Retained events only corroborate. The fixture's `finish()` publishes the
  summary before the status reaches the client, so the post-exit summary is final.
- **procfs sampling.** Z/X/x states, missing VmRSS, mid-read exit and
  start-time changes are rejected rather than recorded as zero.
- **Zero-reference ratios.** For empty-batch and idle, `rows_per_second` = 0
  yields `undefined_zero_reference` and does not crash (P8).
- **Collector storage.** Storage is bounded: 64 KiB stdout prefix, 16 MiB
  record, 1 MiB stderr and 8 MiB logs. Reconnect and fixture sequences use
  global ordinals across subscriptions, so sequence and batch correlation stay valid.
- **Cleanup.** The fixture and meter each run in their own session. Both are
  SIGKILLed by group on timeout, exception or close, and the lock is released by
  its context manager.

## Findings (severity-ranked)

### High: fix before the corresponding quiet window

**H1. Duration "live" modes do not gate schedule completeness, so a
throughput-limited client can produce a *valid* but shorter steady/dictionary
session.**
`p6Run.live` (phase6.go:1400–1424) stops when `time.Since(start) >= duration`,
not after the scheduled batch count. Under backpressure, a slow client
receives fewer batches. `output_expectation_issues` (phase6_workloads.py:493)
compares stdout only with what the fixture *sent* (P9: a self-consistent
shortfall produces no issues). The sample stays valid. Its absolute CPU/RSS/row
totals then describe less work. The contract mismatch only quarantines the
ratio. Late-final-batch jitter can also randomly quarantine the steady ratio.
Schedule lateness exists only in sampled events, so no exact summary field
captures it. This is exactly the "fixture or producer versus client" distinction
the goal asks for.
*Required:*
- For steady and dictionary, admit a sample only when `stream.batches` equals
  the deterministic scheduled count (`ceil(duration_ms/interval_ms)`, which is
  90 000 for 1800 s, P3).
- Add exact fixture summary counters: deadline-cut batches, max/sum schedule
  lateness, and cumulative Send-blocked time.
- For slow-sink, report delivered/scheduled and the producer-deadline
  timestamp as sink-limited results, never as admission passes for throughput.

**H2. The dictionary soak is degenerate after about 5 seconds.**
Delta mode grows 16 entries per batch up to the 4096 cap, which takes 255
batches, about 5.1 s at 20 ms (P3; phase6.go:1892). That coincides with the
5-second trend warmup. For the remaining ~29.9 minutes the dictionary is
unchanged and no dictionary messages are sent. The trend window therefore
measures a stable-dictionary stream and cannot screen delta/replacement
retention.
*Required (fixture owner plus oracle):*
- Use a bounded *cyclic* scheme for the whole session, such as delta growth to
  the cap followed by a replacement reset, repeated. Alternatively, run the
  replace and delta variants as separate modes.
- Add an exact summary counter of dictionary messages per window so admission
  can verify continued churn.
- Do this before spending the 75-minute dictionary window.

**H3. Duration-tier statistics produce misleading intervals, and no tool
aggregates the replicated tier.**
`run.compare_samples` bootstraps a single session per side into zero-width
intervals: `ratio_ci95=[1.3,1.3]` and `delta_ci95=[30,30]` (P1). With n=3 the
interval is still artificially narrow (P1, `[1.277,1.323]`). The plan calls
these sessions descriptive, but the report prints CI fields.
Each soak window writes its own report with n=1, and nothing combines the
three-session tier (grep finds no aggregator).
*Required:*
- Suppress `ratio_ci95`/`delta_ci95` for non-finite tiers, or below a declared
  minimum n, and emit `interval_status: "not_estimable"`.
- Before the second soak window, add an offline aggregator. It must require
  identical binary, fixture, meter, collector, process-harness, manifest and
  budget hashes across windows. It must treat sessions as independent clusters
  and report per-session values plus a range, with no bootstrap at n≤3.

**H4. Predeclared budgets are not bound to the campaign.**
`campaign` only checks that a `budgets` key exists (phase6_workloads.py:757).
A manifest with `budgets: {}` runs and passes (P4). The plan's claim of
"predeclared thresholds" is therefore unverifiable from the report.
*Required:*
- Require `manifest.budgets` to equal the content of
  `docs/evidence/phase6/performance-budgets.json`, sha256 `6a1191ae…be95`, or
  to carry that sha256.
- Record the budget file identity in `report.json`.
- Fail before taking the lock on mismatch.

**H5. Crashes inside a window leave no report.**
Two paths abort with no `report.json` at all (P5, P6):
- Any exception other than `OSError`, `ValueError`, `RuntimeError` or
  `KeyError` during a measured sample. One example is a `TypeError` from an
  unexpected summary or event shape in `correlate` or `run_sample`.
- *Any* exception during the A/A loop, such as a fixture startup deadline. The
  A/A `run_sample` calls at :800 are unguarded.

The lock is released, but the negotiated window ends with no
`complete=false/passed=false` record. The coordinator then cannot apply the
"incomplete partition" rule from the artifacts.
*Required:*
- Wrap A/A and measured samples so that every exception records an invalid
  sample.
- In a `finally`, always write `report.json` with `complete=false`,
  `passed=false`, the failure text and quarantined ratios.

### Medium: fix before relying on throughput or long-run interpretation

**M1. Observer and fixture CPU ceilings are not measured.**
- *Collector CPU.* The collector's own CPU per sample is never recorded.
- *Fixture CPU.* `fixture_cpu_*` is the fixture's lifetime `RUSAGE_SELF`
  (phase6.go:736). It includes Go runtime and TLS-key startup and the idle time
  until the summary fetch, so it is not CPU during the stream.
- *Fixture critical path.* The expected-output `RecordToJSON` runs serially
  before each batch Send (phase6.go:1461), so the fixture is on the throughput
  critical path for `large` and the compressed workloads.
- *Hash-framing control.* This control still runs the per-line Python loop and
  logs per-batch events. It isolates `json.loads` cost only, not the whole
  observer.

*Required (harness only, outside client timing):*
- Record `getrusage(RUSAGE_SELF)` deltas around `collect`.
- Fetch one summary immediately before spawning the meter, then report
  fixture CPU as a delta.
- Report observer and fixture busy fractions relative to `wall_ms`.
- Mark throughput ratios as `fixture_or_observer_bound` above a predeclared
  fraction.

Optionally add a minimal byte-count/hash-only drain control.

**M2. Quiet-window interference is not detected, and identities are not
rechecked.**
Host metadata, including load, is captured once (`run.host_metadata`). There is
no per-sample check of system-wide CPU use or load. Binaries are hashed at start
only, but the release, fixture and meter paths are writable by builders.
*Required:*
- Record per-sample `/proc/stat` system busy deltas and `/proc/loadavg` at
  sample start and end, and flag samples above a declared non-campaign CPU
  threshold.
- Re-hash go, candidate, fixture and meter at window end, and fail the report
  on any change.
- Record a sha256 of the quiet token (L3).

**M3. Admission is per sample only, so a whole duration window can be lost.**
The soak tier defaults to `--repetitions 3` (phase6_workloads.py:883). Each
session needs 1920 + 30 s, so at a 4500 s budget only 2 of 6 sessions fit. The
run ends `complete=false` after ~65 minutes, and the plan then discards the
whole partition.
*Required:*
- For non-finite tiers, refuse to start unless the A/A controls plus every
  scheduled session's (timeout + reserve) fit the budget.
- Make the soak default `--repetitions 1`, or require it explicitly.

**M4. Run order is not counterbalanced.**
`schedule` reseeds `Random(seed)` for every workload, so all workloads in a
campaign share one order sequence (P2). For single-session windows, seeds
2026 and 2027 both run the candidate first and 2028 runs Go first (P2). Any
window drift (Phase7 resumes, thermal, page cache) then confounds the
comparison.
*Required:*
- Derive per-workload seeds from (seed, workload).
- For n=1 duration windows, prescribe explicit alternation (AB/BA/AB) in the
  plan and record it.

**M5. The A/A is a contract check only.**
Two one-row Go samples verify contract reproducibility, not timing noise.
Nothing calibrates how often Go-versus-Go crosses the ">10% and >2 ms"
thresholds.
*Required:*
- Rename it "Go A/A contract reproducibility".
- In the first finite partition (or its own window), run a timing A/A: the
  same Go binary as two labels at 30×30, reported separately, so that ratio
  spread under the null is known before declaring "practically slower".

**M6. Sink-gated and schedule-bound metrics still produce ratios.**
- *slow-finite.* `first_complete_record_ms` includes the deliberate 0.1 s reader
  pause, and slow-sink includes 10 s.
- *paced, steady and idle.* Wall time and throughput are set by the schedule.

All of these still get `candidate_over_go` entries.
*Required:* label these metrics `sink_gated`/`schedule_bound` per workload and
exclude them from ratio and budget evaluation. Keep the absolutes.

**M7. Fixture provenance is ambiguous.**
- `bin/pb-fixture` is r2, the plan's functional command uses `bin/pb-fixture`,
  diagnostics used r3, and r4 is newest.
- `phase6.go` was last modified at 06:47 and r4 was built at 06:51.
- The report records only the fixture binary hash, not its source.

*Required:*
- Build one frozen fixture from the hashed current sources (`60e0ac5e…`,
  `d9025f74…`) with the pinned Go 1.25.4 toolchain.
- Record source, toolchain and binary identities in the manifest, and update the
  plan's `bin/pb-fixture` references.
- Rerun the functional checks (all nine Go checks, plus Rust) against that
  binary before any window.

**M8. The diagnostics inflight counter is structural.**
The new RAII guard is created after `try_send` and dropped at acknowledgement
inside the sequential `deliver` (session.rs:156–171). With a capacity-one
channel and ack-before-next-send, `peak == 1` holds by construction. The
plan's snippet asserting `== 1` checks only that `deliver` is never
concurrent, not channel or transport buffering. That assertion is fine as a
regression check.
*Required:*
- Rerun it only on the settled diagnostics build and record that build's hash.
- Do not cite earlier sidecars.
- Keep the plan's "not read-ahead" caveat.

### Low

- **L1. Partial banner (declared limitation): the consequence is negligible.**
  The trusted fixture writes the banner with `json.NewEncoder(os.Stdout).Encode`
  (main.go:431), which is one unbuffered `write(2)` well under PIPE_BUF. That
  is atomic on a pipe, so it cannot produce a partial line. With the frozen
  fixture, worst-case per-sample overrun is about timeout_s + 10 s (select) +
  5 s per urllib operation + cleanup, which the 30 s reserve covers. Keep the
  declaration and do not modify shared startup code. The only residual
  unbounded wait is a nonconforming fixture, which is covered by the M7 freeze.
- **L2. The visibility log cap is too small for 60-minute sessions.** At 8 MiB
  it covers 1800 s of steady (7.1 MB, P3) but not the supported 3600 s
  (14.2 MB). Batch correlation would silently stop after about 35 minutes.
  Raise it to about 16 MiB or restrict sessions to 1800 s.
- **L3. The quiet token is not recorded** (P4). Store `sha256(token)` in
  `manifest.json` and `report.json`.
- **L4. One invalid sample (or warmup) in any workload nulls every workload's
  ratios in that partition** (P7). This is conservative and documented. A
  per-workload quarantine plus the campaign flag would retain more information.
- **L5. Flagged first records are not summarized.** Summaries should count
  `first_record_observed_after_native_exit` per group.
- **L6. The fixture's environment is not controlled.** It inherits the
  coordinator environment (GOMAXPROCS, GOGC, GODEBUG) without recording it.
  Pin it or record it.
- **L7. Fixture batch-event sampling is front-loaded.** After 20 000 events it
  samples 1/100, so steady batch latencies over-represent the first ~200 s.
  Report correlated-batch coverage over time.

## Required changes, by gate

- **Before any finite window:** H4, H5, M2, M7, the M1 instrumentation and the
  M6 labeling. Plan the M5 timing A/A for the first finite partition. Then
  rerun the unit suite and the frozen-fixture functional checks (Go and Rust).
- **Before the short duration tier:** H1 and the H3 interval suppression.
  Also M3 and M4.
- **Before any 75-minute soak window:** H2 (fixture plus oracle), the H3
  aggregator and L2.
- **After every change:** record the new source hashes and do not reuse earlier
  sidecars or functional results as evidence.

The shared `run.py` and `meter.c` need no change for these findings. H3's
interval suppression and the aggregator can live in `phase6_workloads.py`.
