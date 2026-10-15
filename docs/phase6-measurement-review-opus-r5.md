# Phase6 measurement review opus-r5 — fresh independent pre-timing review

**Disposition: no High defect. No unresolved prior objection was reopened.** The frozen collector is suitable for the scoped first finite same-Go timing A/A and its matching hash-framing control. That still requires accepted parent provenance and the actual quiet-window handshake. This review found one Medium readiness consequence, R5-1. It does not block that A/A, but it should be decided before the candidate finite windows: on this 20-CPU host the frozen per-sample interference screen cannot admit any ratio for a sample shorter than about 1.1 s. So every primary finite latency ratio, including the A/A's, will be null by construction. Three Low findings are scoped, non-blocking cleanups or procedure notes. This review grants no campaign admission, no whole-phase acceptance and no performance conclusion.

This is the fresh Opus measurement round. The earlier Opus R4 attempt performed no review. This round independently re-read the collector and its dependencies and ran its own guarded tests and probes. It does not reuse R4 Sol outcomes as proof. It is **not** the separately owned whole-phase production Opus review. It does not accept R6 source, the stale R5 campaign draft, or the separately owned I-1/I-2 adapter fixes in `phase6_dictionary_cases.py` and `phase6_h2_limits.py`.

## Scope, constraints and identities

Read in full:
- the governing `/home/ajs/.cache/pb-roadmap/phase6-final.md` measurement sections (SHA256 `234229962bb38e7a6b2eb74775de02eea8ee565fb9be4e4bb607fa3206f252c9`);
- reviews R1–R4, the measurement plan and the budgets;
- every line of the collector;
- the test layout and the Campaign/ReviewAdmission helpers;
- shared `run.py` helpers (`spawn_measured`, `kill_group`, `binary_info`, `compare_samples`, `isolated_env`) and all of `meter.c`;
- the fixture's live schedule, emission, Send, dictionary, bucket, event-writer and summary code;
- the optional prepared `campaign-prepared-r6-v1` manifest and `offline-binding.json`.

The only worktree write is this document. Scripts, logs and synthetic artifacts are confined to `/home/ajs/.cache/pb-phase6/measurement-review-opus-r5`. No client, fixture, meter, build, profiler, pressure run, soak, timing run, real quiet token, real performance lock, agent, commit or external message was used.

Inputs were identical before and after all checks (`source-hashes-before.txt` = `source-hashes-after.txt`, both `21e0646f…0efd`):

| Input | SHA256 |
| --- | --- |
| Collector `harness/phase6_workloads.py` | `e2e83fa118c58a96ff55700be88f95f9b8b6479a289a8c59f447f836389fde51` |
| Tests `harness/test_phase6_workloads.py` | `ac7f43fba1efd9e4a7e993ec9b20aaa498538a31f5cc40ece5d501ced336c322` |
| Plan `docs/phase6-measurement-plan.md` | `8d7bf088a1d62ec304a822a5dea952314f8c5c35e926a180b542bdfd66b313e1` |
| Budgets `docs/evidence/phase6/performance-budgets.json` | `76a57ecacf2fba39b1f278e385f1cf4ee410c49f29d2d0e7e7dd2e2713b5ae93` |
| Prior review `docs/phase6-measurement-review-r4.md` | `695cf9dc8882507b9851bec08af39b6abfbcde90e0652ffcad594daa71c9cd71` |
| Shared `harness/run.py` / `harness/meter.c` | `b280d129…a076` / `5d7ce9dc…70e9` |
| Fixture source `phase6.go` / `main.go` | `8e77b968…5beb` / `d9025f74…dbd0` |
| Immutable fixture r5 / its manifest | `c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314` / `40d53f0b019805fbc710d7bc41ab5e356b96a4dfc82e5ed6c3dee469f8075286` |
| Prepared parent `campaign-prepared-r6-v1/manifest.json` | `a47093f4a3ea785a310f7ca1baa92444deaf7e21db5ba7db72dc897ddc3d047b` |
| R6 candidate build manifest `candidate-review-r6.json` | `a90917f86241c7df045015250ee69cc4d8090264285be46672bd0b865441fb3e` |

## Verification actually performed (independent, not read from saved results)

Every runner imports `guard.py` (`e9318632…7e75`) before the harness. It points `TMPDIR`, `GOTMPDIR` and `CLAUDE_CODE_TMPDIR` at the review cache and disables bytecode. It redirects `p6.TMP` into the cache and mocks host metadata. It replaces the following with raising guards, each of which recorded **zero calls**: `fcntl.flock`, `fcntl.lockf`, `subprocess.Popen`, `run.spawn_measured`, `os.posix_spawn`, `os.posix_spawnp`, `os.fork`, `os.system`, `os.execv` and `os.execve`. The `/tmp` quota was exhausted by unrelated sessions, so all shell output was redirected to the cache.

1. **`run_pure_tests.py`: 84/84 tests passed**, with 0 failures, errors or skips. The classes were `ObserverAudit`, `Collector`, `Workloads`, `Campaign` and `ReviewAdmission`. `ObserverNative` and `NativeMeter` were not loaded because they are native. See `pure-tests.log` (`a110fc3b…5fe0`) and `pure-tests-result.json`. The suite includes the R3-A symlink-repoint and R3-B parent-replacement regressions, which execute against the frozen collector.
2. **`probes.py` (P1–P6), independent of the R2–R4 probe designs.** See `probes.log`/`probes.json`; P2 was rerun in `probes-p2-rerun.log`. In the first P2 attempt my synthetic sample builder passed a duplicate keyword, which is a reviewer bug. Merging the two dicts fixed it, and only P2 was rerun. The fix is behavior-identical for P1 and P3–P6, which pass no extra screen fields. The script was also given an optional probe-selection argument. The current `probes.py` hash is `495d27ba…0a77`. Synthetic numbers expose logic only.
3. **`saved_cpu_screen.py`: read-only geometry of 16 saved functional samples.** These are the 14 r5 small Go/R4 samples and the 2 large diagnostics samples; see `saved-cpu-screen.json`. Nothing was replayed, and these are not performance data.
4. **Prepared R6 manifest offline binding (P5).** `bind_manifest` and `bind_selected_roles` pass in both candidate and timing-A/A modes:
   - Go `fae8411f…2314`, candidate normal release `f1fb7a9e…5140f` and meter `17c3b734…127c`;
   - six declared runners, all matching the current files: collector, `run.py`, `meter.c`, `profile_external.py`, `phase6.py` and the collector tests;
   - budgets `76a57eca…ae93`, fixture r5 chain and stable manifest `2bee6d04…1bc4`.

   This is binding readiness only, not acceptance or admission.

## Findings

### R5-1 — Medium (readiness consequence; decide before candidate finite windows, not before the same-Go A/A)

**Cause.** `cpu_screen` (`phase6_workloads.py:699–720`) admits a sample only when the whole-host unattributed-busy upper bound is at most 0.50 cores. That bound includes `2000·cpus/ticks` ms of tick uncertainty plus `mismatch·cpus`. In addition, `summarize_campaign` (`:1148`) nulls every ratio of a workload if any one of its roughly 60 measured samples is not `comparative_admissible`.

**Witnessed evidence.**
- On this host `cpu_count=20` and `CLK_TCK=100`, so the floor is 400 ms before any interval mismatch.
- The saved functional samples show a real mismatch of 6.6–12.6 ms, giving 534–608 ms of uncertainty.
- P1 drove the actual function with zero background load. The smallest wall reaching `below_screen` is 800 ms at zero mismatch, 1080 ms at 7 ms and 1200 ms at 10 ms.
- A 4 ms or 31 ms one-row sample is always `resolution_limited` (below the 100 ms minimum).
- A perfectly quiet 1065 ms sample with the saved 9.6 ms mismatch is still `ambiguous`, with an upper bound of 0.554 cores.
- The allowed background load is 0 ms at 150, 500 and 1000 ms walls.
- P6 runs a mocked `--timing-aa` over one-row and many-small with 142 calls. It returns `passed=true, complete=true` with 30 absolutes retained per label, but every ratio is null with the quarantine "host CPU admission … resolution_limited".

**Consequence.**
- On this host, the frozen tool cannot report a Go/candidate or A/A ratio for one-row, many-small, wide, empty-batch, compressed, paced or slow-finite.
- `large` would need roughly ≥1.1 s on every sample.
- The pressure cases' eligible metrics need every sample to be ≥~1.1 s and quiet.
- The budgets' finite latency thresholds therefore cannot be applied to their declared primary workloads from report ratios.

This is consistent with the conservative-quarantine limit accepted in R2–R4, so it is not a correctness defect. Nothing false is admitted. The saved functional samples were collected under concurrent load and were correctly `flagged` at 5.2–8.6 unattributed cores, which is a positive check of the flag direction.

**Scoped recommendation.**
- Run the first same-Go timing A/A and its hash-framing control as planned, but predeclare that the all-null ratio outcome is expected and is not an A/A failure.
- Characterize noise offline from the retained per-sample absolutes, descriptively.
- Before spending F1–F6 candidate windows, the coordinator and owner should decide on A/A data only, never after viewing candidate data. Either accept absolutes-only finite reports, or adopt and re-review a pre-registered alternative such as a partition-level interference screen over the whole window, where the tick floor becomes negligible. That alternative would need a recorded budgets/controls change.
- This review does not impose either choice.

### R5-2 — Low: the ceiling screen suppresses throughput only, while output-heavy primary latency stays eligible

`potential_limit_status == 'fixture_or_observer_bound'` sets only `throughput_admissible=false` (`:683–686`, `:1156–1159`).

P2 (mocked `large`, observer busy 0.95 cores) shows the following ratios remain non-null: `wall_ms`, `first_complete_record_ms`, `fixture_to_first_complete_record_ms`, `cpu_ms`, `cpu_us_per_row` and `peak_rss_bytes`. Only the two throughput metrics are nulled. `large` is a declared primary latency workload. When the Python full parser or the fixture's serial expected-output encoding (`phase6.go:1669–1676`, before `batch_emit_start`) limits the pipe, `wall_ms` is limited in the same way and the ratio compresses toward 1.

Plan lines 144–146 already warn that collector, fixture or pipe can limit a Go advantage, and on this host R5-1 nulls these ratios anyway. **Fix if ratios are ever admitted for short output-heavy workloads:** extend the flag's suppression to `wall_ms` for non-schedule-bound finite workloads. Alternatively, require the matching hash-framing control before interpreting `large`/compressed wall ratios. Non-blocking.

### R5-3 — Low: SIGTERM/SIGHUP bypass every cleanup and report path

P3 confirms that the driver installs no handlers: `signal.signal` is called 0 times, and SIGTERM and SIGHUP keep their default dispositions. Only SIGINT maps to `KeyboardInterrupt`, which correctly flows through the `BaseException` paths: kill group, `fixture.close()`, invalid sample, atomic `complete=false` report.

On SIGTERM or SIGHUP, Python exits without running `finally`. No `report.json` is written. The fixture (`start_new_session=True`) and the meter/CLI (`posix_spawn setsid=True`) survive in their own sessions. Process death releases the flock, so a next window could acquire the lock while an orphaned idle fixture (100 ms flush and 1 s summary tickers) or a draining CLI still runs.

**Procedure, sufficient for the first window:** abort only with SIGINT. The parent's existing "confirm child and fixture cleanup" step (plan lines 228–229) must explicitly check for leftover `pb-fixture-r5`, `pb-meter` and measured `pb` processes before releasing the window. **Optional code fix:** raise `KeyboardInterrupt` from SIGTERM/SIGHUP handlers installed for the campaign's duration. Non-blocking.

### R5-4 — Low: some required resource quantities are retained raw but not summarized

The roadmap asks the report to include faults, context switches, block I/O and encoded/decoded/output byte rates. Each sample journal retains:
- the meter's `minor_faults`, `major_faults`, voluntary/involuntary context switches, `block_*_operations` and `proc_io_*`;
- `encoded_bytes_per_second` and `decoded_bytes_per_second`.

`METRICS` (`:36–37`) omits them, so per-label summaries and the duration aggregate carry no median or range for them. Nothing is lost, and no budget compares them, so parent reporting must derive these descriptive statistics offline from `samples.jsonl`. Per-batch emission-to-visible latency likewise has only per-sample trend extremes, with raw values in `batch-latencies.jsonl`. Non-blocking.

### Informational (no change requested)

- **Declared runners vs executing collector.** Code does not require the executing collector to appear in `sources.runners`. P4 shows a manifest with no runners binds, and still records and end-tracks the actual collector `e2e83fa1…`. The prepared R6 manifest does declare it, so the N2 intent holds for that manifest's content.
- **Event-reader race.** Batch events in the fixture's 64 KiB `bufio` writer can be split across writes. They are completed by the flush on the non-batch terminal event before the client exits, so `correlate`/`jsonl` read only after a consistent flush. Any residual partial line raises, which fails the sample and is fail-safe.
- **Tick-sampled host counters.** `/proc/stat` busy time is tick-sampled, so `below_screen` remains a screen and not a strict bound. This matches the existing "no quiescence claim".

## First-principles assessment of the collector

- **Admission boundaries.** Admission requires all of the following:
  - exact fixture-summary output SHA, bytes, rows and batches;
  - auth/ticket coverage equal to DoGets, zero invalid counters, and about/HTTP each exactly 1;
  - correct discovery-before-DoGet ordering;
  - deliberate InvalidArgument terminal with `{exit:1}` and the fatal stderr diagnostic;
  - unchanged config and no panic.

  Retained events corroborate only. Saved stderr is byte-identical across all 14 Go/R4 small samples (`7b14587c…0be0`), so the contract A/A and the Go/candidate `stderr_sha256` comparison are stable. `comparable_contracts` excludes all timing fields.
- **Cost attribution.**
  - The meter is a separate supervisor (`setsid`). It timestamps before `fork` and at `waitid(WNOWAIT)`, before the `/proc/PID/io` read and the `wait4` rusage. The CLI shares the meter's process group, so `kill_group` reaches it.
  - Collector `RUSAGE_SELF` CPU covers capture only. Fixture CPU comes from summary deltas with request bounds.
  - Encoding wall time is annotated and never subtracted. The busy-core denominators use native elapsed time.
- **Timed clock and first-useful causality.**
  - All stamps are CLOCK_MONOTONIC. `first_record_ns` is stamped immediately after the `os.read` that delivers the LF of a parsed object.
  - Fixture `batch_emit_start` and `FirstNonemptyBatchNS` are stamped after IPC preparation and expected-output encoding, immediately before `Send`, and are not gated by event retention.
  - After-exit observations are flagged, not clipped.
  - The proc sampling inside the read loop adds at most about 0.5 ms of observation jitter, symmetric across labels.
- **Slow sink, dictionary schedule and cohorts.**
  - Fixture `liveSchedule` uses integer ceil and min, and the counters cover scheduled, emitted, omitted (deadline or termination), stop reason, lateness and Send.
  - Collector admission rejects ordinary shortfalls. Slow-sink keeps its delivered observations absolute and throughput null.
  - The cyclic dictionary check derives `resets = (emitted−1)//ceil(cap/cardinality)` and requires exact deltas, replacements and max size. It also requires one dictionary message per production minute-bucket batch, bucket totals and zero overflow.
  - Cross-checked against the saved cap-64 pressure cases: 9 messages, 1 initial, 6 deltas, 2 resets.
- **Failures, cleanup and identity binding.**
  - `capture.json` and `sample.json` are written atomically right after `collect`, and post-capture exceptions merge into them. The wrapper reloads saved observations.
  - Contract-A/A, warmup or measured collector exceptions produce an atomic incomplete report.
  - Fixture and meter run from verified resolved paths. The parent manifest is parsed and hashed from one bounded read. All bound identities are deduplicated and rechecked inside the lock and again in `finally`.
- **Bounded storage, window and lock.** The caps are:
  - stdout prefix 64 KiB, record 16 MiB, stderr 1 MiB with overflow failing the sample;
  - visibility 16 MiB, resources 8 MiB, latencies 8 MiB, summaries 4 MiB, events 64 KiB per line;
  - fixture event and bucket caps.

  The flock is `LOCK_NB`, so it never waits. A sample starts only if its timeout plus 30 s fits. Duration partitions must fit whole before the lock is taken.
- **Report claims and ratios.**
  - No universal-speed or delivery claim is made. `NA` keeps wire bytes, allocations, read-ahead, server drops and cancellation unmeasured.
  - Schedule-bound, sink-gated and pressure metrics are scoped. Contract and partition failures quarantine every ratio.
  - Finite runs with n<20 and every duration tier get no confidence intervals. The aggregate requires same identity, TLS, seed and workloads plus distinct indices.
- **Commands, manifest and profiles.** Plan commands bind the meter by hash despite the mutable `bin/` path. Timing A/A requires exactly 5 warmups and 30 measured runs per label. Diagnostics and profiles live only in separate `run_sample` callers; `bind_selected_roles` rejects non-normal-release candidates.

## Independent closure map of prior objections

| Item | Opus-r5 independent status and basis |
| --- | --- |
| R1 H1 schedule, omissions, lateness, Send wall | Closed. Fixture `liveSchedule`/`recordLive`/`batch` and collector `schedule_admission` were read; `test_deadline_omission…`, `test_complete_schedule…` and `test_sink_shortfall…` pass. |
| R1 H2 continuous dictionary cycle and buckets | Closed. Cycle and reset arithmetic was re-derived; bucket and counter gates checked; `test_missing_churn…` and `test_pressure_definition…` pass. Full-session behavior is source-only. |
| R1 H3 statistics/aggregator, R2 N4 TLS identity | Closed. `compare_descriptive` has no CI for duration or n<20; `identity_key` requires boolean TLS; `test_tls_condition…` and `test_duration_*` pass. |
| R1 H4 exact budgets | Closed. Content and SHA are bound before the lock and end-tracked; P5 binds the prepared R6 budgets. |
| R1 H5 / R2 N3 atomic failure and completed capture | Closed. Code path read; `test_post_capture_typeerror…`, `test_exception_wrapper_merges…` and `test_aa_typeerror…` pass. R5-3 notes the uncatchable-signal edge. |
| R1 M1 observer/fixture CPU, M2 interference/identities | Closed as a conservative screen; see R5-1 for the quantified consequence and R5-2 for scope. |
| R1 M3–M8, L1–L7 | Closed or accepted as stated in R2. The banner partial-line limit, non-atomic procfs and whole-partition quarantine remain explicit limits. |
| R2 N1 selected roles and candidate build chain | Closed. `bind_selected_roles` read; `test_each_selected_role…` and `test_normal_candidate…` pass; P5 binds Go, R6 normal candidate `f1fb7a9e…` and meter to the prepared declarations. |
| R2 N2 end-hash all runners, toolchain, snapshots, roles | Closed for declared content; `test_all_bound_runner…` and `test_one_extra…` pass. See the informational note on runner coverage. |
| R2 N5 same-seed/provenance prose | Closed. Plan lines 274–276 and 650–667; `test_current_examples…` passes. |
| R3-A verified fixture/meter paths used | Closed. Line 1233 passes `fixture['path']` and `meter['path']`; `test_fixture_and_meter_symlink_repoints…` passes. The R4 in-memory revert control was not repeated. |
| R3-B parent bytes and identity from one read | Closed. Lines 92–100, 1183 and 1206; `test_parent_atomic_replacement…` and `test_parent_identity_uses_exact_read_bytes…` pass. |

Saved-only versus independently tested:
- **Not rerun here:** native observer and meter tests, functional or pressure samples, R6 debug diagnostics and the R4 causal revert controls. They are cited only as prior evidence.
- **Independently executed against the frozen sources:** the 84 tests and P1–P6.
- **Read only:** the saved-sample geometry.

None of the R4-saved diagnostics proves R6 memory release.

## Scoped recommendation: first finite same-Go A/A and full/hash-framing control

Tooling readiness is **yes, with the R5-1 expectation predeclared**. Before running:
- the coordinator must accept and freeze parent provenance;
- prepared r6-v1 binds offline, but is not admitted here. It must be rechecked if any bound runner changes; the I-1/I-2 adapter files are not among its runners;
- obtain explicit Phase7 and other-builder pause acknowledgements;
- provide a fresh token, a fresh output folder and a ≥900 s budget per partition;
- take the common nonblocking `/home/ajs/.cache/pb-rs-performance.lock`;
- abort only by SIGINT, and check for orphans before release (R5-3).

Expect `kind=timing_aa`, `passed/complete` true if all samples validate, retained absolutes, and null ratios. Review those artifacts, and the R5-1 decision, before the candidate finite pairs, the short duration tier or any 30-minute soak. Replicated aggregation and the full-control tiers remain later gates.

Carried limits are unchanged:
- Finite InvalidArgument with exit 1 is deliberate failure, never live delivery.
- Prior Rust applies only to shared commands.
- Proc I/O is not network traffic, and protobuf bytes exclude HTTP/2/TLS.
- Owner maxima are not RSS.
- Loopback is not deployed delivery.
- Server drops, read-ahead and cancellation remain N/A in this collector.
