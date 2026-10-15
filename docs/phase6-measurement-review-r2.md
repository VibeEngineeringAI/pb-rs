# Phase 6 measurement review r2 — independent, before native campaigns

Reviewed 2026-10-14 with GPT-6.1 Sol, in `/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail`, HEAD `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0` plus the existing dirty tree. This is a new measurement review, not an implementation continuation or the user's fresh final Opus whole-phase review.

**Decision: retain the corrected measurement design, but do not admit a native finite campaign through the current runner until N1–N3 below are resolved.** The original budget binding, atomic campaign report, schedule/churn, statistical suppression and CPU uncertainty corrections largely work. Four additional gaps were demonstrated with bounded mocks: selected executable provenance is not bound, some declared identities escape end checks, a post-capture exception loses completed observations from the sample, and replicated reports can mix TLS/plaintext. N4 blocks replicated aggregation, not the first standalone finite control. N5 is documentation cleanup. No measured speed, release resource envelope, successful production delivery or final Phase6 acceptance is asserted.

## Scope and constraints

Read the governing cache roadmap, original independent Opus r1 review, settled measurement plan/budgets, implementation resolution JSON, current collector/tests, fixture accounting/dictionary source, relevant diagnostic ownership source, and unchanged shared `run.py`, `meter.c` and `profile_external.py`. The resolution JSON was treated as a claim to check, not proof.

Only this review document and `/home/ajs/.cache/pb-phase6/measurement-review-r2/` were written. No production, harness, tests, fixtures or budgets were edited. No commits, agents, builds, native measurement/profiling/soak, pressure rerun, real quiet token or flock acquisition occurred. The existing phase1–5 regression runs and active separate source audit were not rerun or incorporated as measurement-admission proof.

## Snapshot identities

All five supplied primary source hashes matched. The full read-only inventory is `saved-inspection.json` in the review cache.

| Source | SHA256 |
| --- | --- |
| `harness/phase6_workloads.py` | `432232dd88ba8bbd7f1ac5ea9d24ab638f671e4ddf3ed2463f06be5be8510735` |
| `harness/test_phase6_workloads.py` | `a5657573862b08ba7b50583538c8574e94edb2f9a22ea1e7811a98bfa21ce1e0` |
| `docs/phase6-measurement-plan.md` | `22843bef0965ff5246710231d7e0788c20b58ca8d115d6290b63d8a3b7536d5e` |
| `docs/evidence/phase6/performance-budgets.json` (schema 2) | `76a57ecacf2fba39b1f278e385f1cf4ee410c49f29d2d0e7e7dd2e2713b5ae93` |
| `harness/fixture/phase6.go` | `8e77b9681cadb9bdc7a87fa1ebbbd6520d87fbca3b7ed2298f20af3ba9e12eb3` |
| `harness/fixture/main.go` | `d9025f74429d96cae9d49ec3a4cb633770aad7b44cb974f686548aca9e37bdb0` |
| `harness/fixture/phase6_test.go` | `3b42e6916bbf68170fdb9c2fc24a2557f7c6e58534a24bd5bed7161b6c6e7b07` |
| `harness/run.py` | `b280d1295a4b351e6f2d838c56acaac58b2247bb77fc9881587bb7bbe849a076` |
| `harness/meter.c` | `5d7ce9dc2fa65b1ec7018fb863d36fd78d8b8535762acadaefc3f2f4e0f070e9` |
| `harness/profile_external.py` | `a0f0d7a315772ed933eae235b0c55850ce8b23da25132c6ccbafc9a4e0256a3c` |
| `harness/phase6.py` | `f9f8ed51b42aee896b6f2fc11ff6eb806c9243cfb75f49f99b10f77e83a1f445` |
| `src/tail/session.rs` | `bc62d630339e2fd4423fc3813f8772490c5df55a60597ecad8d35874e293ca97` |
| `src/tail/resources.rs` | `8210efe689760620dd3536e2825a738acad28a51c5f17ac078933e3829d5473b` |
| Governing `/home/ajs/.cache/pb-roadmap/phase6-final.md` | `234229962bb38e7a6b2eb74775de02eea8ee565fb9be4e4bb607fa3206f252c9` |
| `docs/phase6-measurement-review-r1.md` | `ab265adc1359d62e426101d48de1f44e13f877a7b4b0ef9af721d1fc95d2f43d` |
| Cache `tmp/collector-review-r1-resolution.json` | `37a2059513fa26953fde460764b73dd5aab38f49e25d83ce265a4d50ef55d1c7` |

Independently rehashed frozen r5 executable `c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314`, adjacent manifest `40d53f0b019805fbc710d7bc41ab5e356b96a4dfc82e5ed6c3dee469f8075286`, and pinned Go executable `06c386d4ead3c7e28565a544d0e7693d5998f060bc75029a5e86b52b4f824767`. All seven source snapshots matched their manifest hashes and were read-only; the executable was read-only. The evidence copy of the manifest is byte-identical. Nothing was executed to establish these identities.

## Checks actually performed

The cache `run_pure_tests.py` selected `ObserverAudit`, `Collector`, `Workloads`, `Campaign` and `ReviewAdmission`: **70 tests passed, none skipped**. It omitted the actual lock test and did not load `ObserverNative` or `NativeMeter`. `fcntl.flock`, `subprocess.Popen`, `run.spawn_measured` and `os.posix_spawn` were guarded to raise if reached. Campaign host metadata was mocked to avoid `platform.processor()` invoking a helper process. Existing campaign mocks used their unit-only sentinel; no actual acknowledgement/token/window was created.

Every execution set `TMPDIR`, `GOTMPDIR` and `CLAUDE_CODE_TMPDIR` to the review disk cache and disabled Python bytecode writes. `p6.TMP` was redirected there for tests. An initial guard run reported 8 failures/9 errors because host metadata tried `Popen`; the guard prevented execution. That review-runner setup issue was corrected by mocking host metadata, then all 70 passed. Both logs are preserved; those initial results are not product failures.

Six independent pure/mock probes ran in `probes.py`; P1–P4 expose the findings below, P5 verifies stale-draft rejection, and P6 independently verifies finalization failure reporting. Native executables were only read/hashed, including `/bin/true` used as an existing test fixture. Every lock and workload in these probes was mocked. Numbers in them are synthetic, not performance observations.

Read-only `inspect_saved.py` independently checked:

- All **46** indexed artifacts in `fixture-r5-functional.json` and **10** in `diagnostics-large-r5-functional.json`, with no size/hash mismatch.
- **20** saved functional samples: four large dictionary cases, two tiny event-drop cyclic cases, and the supplied fourteen small Go/R4 samples. Current pure `output_expectation_issues`, `request_summary_issues` and `schedule_admission` gates found no issues. This is saved-artifact revalidation, not execution of current capture code.
- Both preserved large diagnostics samples and actual completed sidecars through `tail_resources`.

Reproduction, from this worktree, requires only the cache scripts and disk environment:

```sh
export TMPDIR=/home/ajs/.cache/pb-phase6/measurement-review-r2
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR" PYTHONDONTWRITEBYTECODE=1
python3 "$TMPDIR/run_pure_tests.py"
# probes.py intentionally uses fresh, non-overwriting cache case directories;
# the preserved probes.json is the executed result.
```

| Review artifact, relative to the review cache | SHA256 |
| --- | --- |
| `run_pure_tests.py` | `ac80a6115f54834b4ce8885762cdc91647220ed5a532f3cab7e3532ffc6900c2` |
| `pure-tests.log` | `4af0819ed1ce9ca2f4f1d6c3cd1acd573a2ebc0f7a461d69930150ff1d7925a1` |
| `probes.py` | `5fc44b0dd556bc3b059d49ecf9c6649c19897f6b5f450518467ebf9f406309a6` |
| `probes.json` | `4b0bfd8aca95732e4dfbb6a7667502f0b3e9f4fcbc2221f37223082f4ee216ae` |
| `inspect_saved.py` | `6122bad36d193c3d74d1fbffa157a8e12c8611edf15747574ac0d4ffba955cb0` |
| `saved-inspection.json` | `cb30e3a5a9b69d2a8b106416b10f81af14c3299f895a335143ff7fc87f844718` |

## Remaining findings

### N1 — High, before native finite admission: selected binaries are not bound to declared provenance

**Cause:** `bind_manifest` (`phase6_workloads.py:945–982`) validates the budget, nonempty provenance fields, declared runner hashes and fixture chain. `campaign` (`1097–1117`) independently hashes the selected Go, candidate and meter, but never checks them against the manifest's `baselines`, candidate release build entry or `meter`. The fixture is the exception: its executable is bound correctly. The prepared manifest generator checks frozen builds while preparing a file, but a later CLI selection can choose a different executable without changing that file.

**Executed mock P2:** a manifest explicitly declared different Go, candidate release and meter hashes/paths. The supplied arguments were the test's `/bin/true` inputs. The runner entered the mocked lock, returned 0 and reported `passed=true`. None of the declared inputs was executed. This demonstrates an admission gap, not an actual bad native campaign.

**Impact:** a wrong baseline, debug/diagnostics/symbolized candidate, or different meter can be reported with source/toolchain/build metadata belonging to another artifact. Rehashing the selected paths at the end does not establish the missing link. This matters even for same-Go timing A/A: Go and meter must bind to the declared inputs.

**Precise change:** before the lock, require an explicit measured-role mapping and compare each selected executable SHA256 to it. The existing manifest's `baselines.pb-go`, `build_flags.candidate.pb` and `meter` can supply those identities, or add a clearly required `inputs` object. Candidate-comparison mode must select the accepted normal release build and its flags/features; same-Go timing A/A binds both measured labels to the declared Go identity. Content-identical copied binaries can be allowed deliberately. Test each mismatch with no lock/child call. Do not infer compilation provenance from a file's executable bit or a nonempty `build_flags` object.

### N2 — Medium, before native finite admission: end checks omit declared runners and the fixture toolchain

**Cause:** the pre-lock loop at `phase6_workloads.py:953` verifies every `manifest.sources.runners` entry, but `tracked` at `1125–1127` adds only the current collector/shared process harness plus named binaries/manifests/budgets and fixture snapshots. Extra declared runner entries and the verified fixture toolchain executable are not added. Thus the advertised frozen chain is broader than the end check.

**Executed mock P1:** an extra cache runner was included and correctly hashed in `sources.runners`; the eighth mocked sample changed its contents. The campaign returned 0 with `end_identity_check.passed=true`, no changed paths, and nonnull synthetic wall/CPU comparisons.

**Impact:** the end-of-window report can certify stable identities while a source it explicitly binds has changed. This probe does not show changed client behavior: the additional runner was not executed. It shows incomplete provenance enforcement. The real manifest declares `meter.c`, `profile_external.py`, `phase6.py` and the workload tests in addition to the two tracked Python runners.

**Precise change:** include all successfully bound runner identity objects and the fixture toolchain identity in the same end-check set; deduplicate by resolved path. Keep existing mismatch handling and absolute journals. A bounded mock changing one extra cache runner must invalidate/quarantine the partition. Apply an equivalent check to any additional artifact required for N1's selected-build chain.

### N3 — Medium, before native finite admission: H5 preserves earlier samples, but loses a completed failing sample's observations

**Cause:** `run_sample` obtains `result = collect(...)` at `787`, then validates summary/production/CPU/events/config and only writes `sample.json` at `877`. An exception anywhere between capture completion and that write propagates. The campaign wrapper at `1142–1151` then writes a new error-only object. It has no access to the completed capture result.

**Executed mock P3:** A/A and warmups returned valid mocked samples. A measured sample's mocked capture completed with wall/CPU/RSS, hash/bytes/counts and first-record fields, and wrote a raw meter artifact. Its post-exit summary contained `streams: null`, triggering TypeError at `794`. The campaign correctly produced atomic `complete=false`, `passed=false`, quarantined every ratio and retained earlier samples. However, the failed `sample.json`/journal retained none of those nine completed observation fields. The raw meter artifact survived, so some resource values remain salvageable; it does not carry all collector-derived fields.

**Impact:** no false successful comparison is awarded, but the requested truthful absolute evidence is incomplete precisely on a collector/fixture failure. Earlier successful samples surviving is insufficient to close that part of H5.

**Precise change:** atomically persist the completed capture before post-capture validation, and preserve/merge it into the invalid sample on later exceptions. Alternatively catch post-capture exceptions inside `run_sample`, add failure/invalid flags to the existing result and return/persist it. Keep observed absolutes separate from unvalidated contracts, and never mark the failed sample valid. Add a mock that throws after `collect` returns and verifies first-record, hash/count and native resource fields survive. P6 shows report-finalizer exceptions already fail atomically with prior journal records preserved; that path needs no broader rewrite.

### N4 — Medium, before replicated aggregation: TLS/plaintext reports share an aggregate identity

**Cause:** `campaign` does not record `args.tls` as a top-level campaign identity. It is present in each sample's contract, so comparisons within a campaign enforce it. `identity_key` at `1035–1043` compares workloads/sinks and provenance but not the transport condition. `aggregate_reports` consumes summary medians and never reads the sample contracts.

**Executed mock P4:** two independently indexed, otherwise identical one-session duration reports were produced with contract TLS false and true. Both top-level reports lacked `tls`. The offline aggregator returned 0, `passed=true`, and combined both sessions' wall observations into one series.

**Impact:** independent session values/ranges can silently combine two experimental conditions, contrary to the plan's separate TLS/plaintext reports. This does not resurrect ratios, and does not block one standalone finite A/A/control campaign. It does block trusting replicated aggregates across conditions.

**Precise change:** record an explicit boolean `tls` in the campaign manifest/report and require it in the aggregate identity. Reject duration reports missing the condition rather than defaulting them to plaintext. Test false-versus-true rejection, same-condition acceptance, and legacy missing-condition rejection.

### N5 — Low, before following the corresponding examples: contradictory historical instructions remain runnable

`phase6-measurement-plan.md:274` still tells the second/third replicated windows to use “new seeds,” while the corrected instructions at `651` require the same scenario seed and the aggregator enforces identical workloads. At `559` the direct functional example still selects mutable `bin/pb-fixture` (the previously identified r2 artifact); the diagnostic reproduction at `422–454` still uses immutable r3 and a mutable debug path. The corrected appendix identifies r5 and explicitly says never mutable `bin/` for that fixture.

**Impact:** following the old replication prose prevents a common aggregate; following old functional commands produces evidence against a different fixture/build. The main future native campaign examples already use parent-provided `P6_FIXTURE`, so this is not a separate finite admission blocker.

**Precise change:** update the replication sentence to same-seed/fresh-index windows, with new-seed repetitions a separate series. Mark legacy reproduction blocks as historical and not current commands, or replace their fixture/debug paths with frozen parent-provided identities. No new run is needed to fix the prose.

## Independent disposition of every r1 objection

- **H1 — Closed for the current declared schedules.** Fixture `liveSchedule` uses integer ceil/min, and `beginLive`/`recordLive` track ops, scheduled/emitted batches, deadline/termination omissions, stop reason, last deadline, lateness and Send wall aggregates. Current `schedule_admission` rejects ordinary steady/dictionary/paced shortfalls; slow-sink delivered observations remain absolute and its throughput is suppressed. Unit/mock tests cover omission rejection; saved r5 steady/dictionary cases delivered 50/50 at one second, while slow-sink retained 26/50 and 24 omissions. Full-session behavior is source-only, not an executed soak. Send wall includes marshaling/copy/flow-control work, not pure blocking.
- **H2 — Closed as a measurement definition and bounded functional gate.** `dictionaryRecord` cycles deltas to the cap, replacement-resets to the initial cardinality, and repeats indefinitely within the configured bounded session. `countDictionary` and minute buckets are independent of event retention. Admission verifies initial/delta/replacement/reset/max-size totals and bucket coverage. Four saved pressure cases contain 9 messages, 1 initial, 6 deltas, 2 replacements/resets, cap64; two tiny event-drop cases also pass current gates. Source-only prediction: 90000 batches at 1800s/20ms produce 351 resets for cardinality16/cap4096. No last dictionary-owner release or long-run memory behavior is inferred.
- **H3 — Partially closed; N4 remains before replicated aggregation.** Duration tiers never invoke bootstrap, even at n30; finite n<20 emits descriptive values/ranges without CI. The offline aggregator checks frozen logical identities, identical workload/sink/collector definitions, one session per label/report and distinct session indices; it returns session values/ranges and never manufactures a ratio. Transport-condition binding is missing. N5 also fixes the stale replication seed prose.
- **H4 — Original budget objection closed; broader executable provenance has N1.** Exact current budget JSON content and SHA256 bind before lock and are rechecked at the end. Empty/mismatched budgets fail. Nonempty provenance fields are required; r5 manifest exact-object, source snapshot and Go executable hashes are verified. P5 independently rejected the old draft on the current collector hash, as required. Presence of metadata is not yet binding of selected Go/Rust/meter roles.
- **H5 — Report/finalizer paths closed; completed-sample preservation remains N3.** A/A, warmup/measured TypeError and finalizer failure produce atomic failed/incomplete reports. P6 retained six synthetic journal records, empty summaries and no partial report. N3 is the narrower remaining loss after a completed capture.
- **M1 — Closed as a conservative screen.** Collector `RUSAGE_SELF` capture deltas exclude children/post-analysis. Fixture pre-meter/post-exit summary CPU deltas retain RPC bounds. Expected encoding time is monotonic wall time, overlaps CPU and is never subtracted from client CPU. Busy cores use native elapsed; 0.8/100ms flags possible fixture/observer limits, not proven saturation. Hash-framing still isolates parsing rather than every observer cost; that limitation is explicit.
- **M2 — CPU/token/named-input handling closed; extra declared end identities remain N2.** Per-sample raw proc/load observations and native/observer/fixture interval bounds are retained. All-core mismatch plus tick uncertainty gives a 0.5-core residual envelope; short, ambiguous, unavailable or flagged observations suppress comparisons. Negative raw residual is retained and only derived bounds/estimate clamp. Named binaries, budget/parent/fixture manifests, collector/shared harness and fixture snapshots are rehashed. Token is SHA256 only. No inferred zero/quiescence is needed to admit valid absolute data.
- **M3 — Closed for declared duration partitions.** Whole duration partition timeout+30s-per-sample cleanup+contract-A/A reservation is checked before lock; duration defaults to one repetition, and soak rejects <1800s. Source-only arithmetic: the 1800s Go/Rust pair plus two120s A/A controls needs4200s and fits4500s; a60s pair needs720s and fits900s. Per-sample checks never shorten a session. Finite partitions intentionally retain per-sample admission and can finish incomplete; that is reported/quarantined, not automatically filled later.
- **M4 — Source implementation closed; N5 prose cleanup remains.** Stable workload-derived shuffle seeds and explicit duration AB/BA by session index are present and tested. Same-scenario-seed replication/new indices are enforced through frozen workload identity; new seeds form a separate series.
- **M5 — Closed as available tooling, not executed calibration.** Contract A/A is explicitly reproducibility. `--timing-aa` requires finite5 warmups/30 measured samples per same-Go label, labels both inputs identically and reports kind `timing_aa`. No candidate inference follows from it. Timing A/A and observer control remain separate future windows before practical performance interpretation.
- **M6 — Closed.** Affected schedule/sink/pressure wall, latency and throughput metrics are scoped and suppressed individually. Different output contracts suppress every ratio. Valid absolutes remain useful. Shared-command prior Rust comparisons remain separate; no prior Rust tail baseline is invented.
- **M7 — Frozen r5 chain/runtime controls closed; N1/N5 qualify usage.** Independently checked actual r5 binary, exact manifest/source/toolchain chain and read-only artifacts. Fixture runtime uses a minimal environment with GOMAXPROCS=observed CPU count, GOGC100, GODEBUG empty and GOMEMLIMIT off, recorded per sample. Saved Go/R4 functional evidence exists; it is not a runtime rerun of the exact settled capture source or an all-twelve-workload acceptance claim. Current campaign arguments require a fixture explicitly; old runnable examples need N5 cleanup.
- **M8 — Closed within the stated diagnostics scope.** Current `Outstanding` is a current/peak RAII counter; overlapping local guards can reach2, and dropping guards decrements. `Handoff::deliver` counts accepted Open/Message handoffs until acknowledgement/drop. Source includes the causal guard test; it was inspected, not rerun here. Both preserved R4 large sidecars report actual `max_application_messages_inflight=1`, not the old construction marker. This measures handoff ownership, not transport read-ahead, unique allocations or RSS.
- **L1 — Accepted unchanged limitation.** Trusted short fixture banner still uses one `json.Encoder.Encode` to stdout. The readability wait does not bound a nonconforming partial-line producer; the plan explicitly says so. Frozen-fixture trust, rather than an invented general startup deadline, is the stated boundary.
- **L2 — Closed.** Visibility cap is16MiB; the pure bound test covers180000 maximum-width planned batch events plus overhead for60min/20ms. Resource/correlation logs remain separately bounded with dropped bytes explicit.
- **L3 — Closed.** Quiet token is stored only as SHA256 in campaign manifest/report; unit check passed. No real token was made here.
- **L4 — Accepted.** Whole-partition quarantine after any invalid sample/warmup/incomplete partition is conservative and intentional. Valid absolute observations from earlier samples survive; N3 concerns the completed failing sample itself.
- **L5 — Closed.** Valid first-record observations after native exit are counted in group summaries; retained observations are not clipped to elapsed.
- **L6 — Closed.** Fixture build environment is recorded in immutable r5 manifest; runtime Go knobs are pinned/recorded rather than inherited.
- **L7 — Closed for truthful coverage reporting.** Correlation reports visible/correlated counts per minute, origin and bucket width, overall uncorrelated count and retained dropped bytes. Missing sampled or capped observations stay missing. It does not promise unsampled per-batch latency.

## Saved evidence and interpretation limits

The four saved Go/R4 pressure samples agree on SHA256 `3ab3830e286d3c63776291df3cfd4f2381708c7ef782257758b3b0050b2ed06f`, 32506837 stdout bytes,9 batches and36 rows, with exact repeated dictionary resets. FlightData protobuf maxima72896/2191 stay under4194304; these are not full network wire sizes. Event-drop cases demonstrate that exact churn/accounting survives missing logs. They were inspected only; pressure probes were not rerun.

Both large preserved diagnostics sidecars report peak handoff1, dictionary owner maximum67109124, batch owner maximum67109176, staging last capacity0, and zero post-warmup trend points. They do not expose last dictionary-owner size, allocator release, post-reset RSS release, comparative RSS, a sustained envelope or HTTP/2 read-ahead. The preserved diagnostic build identity is `1f251566bdcfd31d1af6d7b99f5c43dc65c34abcfc7863d7e193ab4e7b19ba0d`. Its report explicitly records historical collector `e8f2edc249ba9e5f5a94662c62eb26169545c916db0de6d55f65802364ccfc07`, not the settled collector hash above. The fourteen small samples likewise may precede final aggregator/admission changes; their raw gates were revalidated, not presented as execution of those final changes.

Meter source still attributes native elapsed/CPU/RSS/faults/context switches/block IO to the actual child, with process I/O observed before wait4 when available. Parent complete-LF observations and fixture-emission correlation retain their distinct scopes. Proc IO is not network traffic; decoded/output bytes are not encoded FlightData bytes; owner maxima are not unique or simultaneous allocation totals. Missing measures remain N/A. No change to the trusted shared tools is requested.

Conservative suppression of every fast finite ratio is acceptable. A valid absolute first-complete-record/resource observation can be useful even when all-core clock uncertainty leaves every comparison null. A quiet handshake and timing A/A do not repair quantization or prove quiescence. The next bounded campaign must not infer performance merely because its report says `passed`.

## Scoped next gate

Before a first bounded native finite A/A/control campaign, resolve N1 selected-input binding, N2 declared end identities and N3 completed-capture preservation; validate with pure/mock negative checks and record fresh source hashes. Regenerate the parent manifest after sources settle. The existing draft correctly fails, and must not be reused. No pressure rerun or performance proof is required to fix these admission issues. The unchanged-byte/current-r5 saved gates provide useful functional evidence, with the source/version limitations stated above.

Once those issues close, the measurement definitions are suitable for a coordinator-authorized, narrowly named finite same-Go timing A/A and matching full/hash-framing control, using frozen inputs and fresh output folders. Require the common nonblocking `/home/ajs/.cache/pb-rs-performance.lock` **and** explicit Phase7/other-builder pause-build/tests acknowledgements for the actual window; a token alone is not that handshake. This review creates neither authorization nor a reservation.

The first duration/long-soak tier is a later gate: review the finite/control artifacts, reserve the whole timeout/cleanup/A/A partition, preserve original1800s duration and apply schedule/churn/CPU coverage gates. N4 and same-seed N5 must close before replicated aggregation. Long retention, server-versus-client pressure attribution, release RSS/capacity envelope, profiles/build cost, deployed auth/TLS and production delivery remain unmeasured or separately owned. A finite InvalidArgument termination remains deliberate failure, never successful live-production delivery.
