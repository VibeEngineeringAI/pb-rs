# First Go versus normal R6 candidate finite evidence review — Sol

**Accepted for scoped retained-evidence integrity. No artifact or interpretation defect found.**
This covers the newly completed one-row and many-small partitions: full collector
native_unprofiled and separate hash-framing observer_control. Both retain valid,
complete native observations and matching contracts. Every comparative ratio
correctly remains null because CPU admission is resolution_limited. This is not
performance, budget, causal-effect, ranking, profiler, build-cost or whole-Phase6
acceptance.

## Scope, safeguards and reproduction

Fresh offline audit on 2026-10-14 in
/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail. Inputs include every file
under /home/ajs/.cache/pb-phase6/campaign-r6-finite-{full,hash}-01/, their prepared
execution/cleanup/process-snapshot records and logs, the frozen parent, all bound
end identities, normal candidate metadata/source hashes, and focused reads of
the frozen collector/fixture formulas and measurement policy.

Only this new document and
/home/ajs/.cache/pb-phase6/first-candidate-finite-review-sol/ were written.
Analysis used bounded standard-library Python reads, hashes and reductions.
No native program, collector, meter, fixture, test, build, profiler, agent,
commit, network operation, performance-lock operation or live process
inspection/control was run. TMPDIR/TMP/TEMP point to the owned disk cache.

Before analysis-library imports, an audit hook denies process execution,
fork/spawn/system/kill, lock and socket operations. OS execution/control methods
are replaced; fcntl.flock and lockf are replaced before importing pathlib.
Subprocess, multiprocessing, socket, ctypes and pty imports are denied.
Writes are restricted to the owned paths. The previous A/A analyzer supplied
methods only: current roles, normal candidate, 22 end identities, modes,
execution records, oracle bytes and populations were explicitly rebound and
checked. No old report population was reused.

Reproduce with:

    python3 -I -B -S /home/ajs/.cache/pb-phase6/first-candidate-finite-review-sol/analyze.py

The final run performed **131,653 counted consistency checks, zero failures**.
These are assertions/reductions over retained data, not independent statistical
observations or executed tests. The inventories cover **4,359 input files /
148,889,349 unique bytes**, including **2,135 files per native partition**.
Each partition has 2,130 files inside its 142 sample directories plus five
top-level evidence files. Every inventoried file has size and whole SHA256
before and after; the two inventories are byte-identical.

Bounds: 128 MiB/file, 512 MiB total unique input inventory, 3,000 files/partition,
10,000 JSONL records/file, 1 MiB/JSONL line. Largest retained stdout: 22,102 bytes;
stderr: 157; visibility: 9,046; batch latencies: 14,692; resources: 173.
All fit their declared retention caps.

The cache's commands.md, earlier analyzer versions, logs and attempt-2 reductions
preserve reviewer corrections. The first exploratory hardguard blocked
Python 3.14 pathlib's fcntl import; allowing the import while denying its lock
methods resolved that prerequisite without operating a lock. Analysis attempt 1
stopped on metadata-shape assumptions; attempt 2's 284 sequence failures came
from assuming fixture events start at zero. Frozen R5 increments before recording
(lines 658–662); final events correctly require 1..N. These were reviewer
assumptions, not evidence defects.

Audit artifact SHA256:

- analyze.py: 4440d780aae566e5a716985343ce72f464d9ecde1fca9739d496db92f5bb8c0d
- analysis.json: b2183d62958ad7dd0fcaf2137d30f497ede16412f41d98a47aea3e41cc00d6d2
- analysis.log: b1e01b54e7b7bd20a246cf0aed377350d19775a0577cb32672dba03937c158ee
- Both inventories: 6d59f6b5d258bb1eccead17e62f4ef430ee34aac04d81310a3523b8cca4ee4c6
- whole-stdout-proofs.json: 4f493eaae3643124445588905ad602aca3622bf34ad75d3a1656bf05dc7b7fe4
- negative-reductions.json: d2989f3a343cf1a11657bb925f7b481298531dac979c9a15296a9a4c3d934d5e

## Provenance and raw-file identities

Full partition:

- report.json: 7ad989c28bb676f75a977952f90c1d078bd66b5f211c0496d1f0e2c5e7c4b3a9
- samples.jsonl: 33de2bac6b4f666560745f2e8090dac30fd4a5743ba63a5eac7a33002d52c225
- manifest.json: 4ecd92d4404d7a59ced7dd49d15dafe6bbc41454dd85709a8b12b89f7aa9d30c
- go-aa.json: baa3f9bb732efd85fa8fb8003394c1336828c5217b6757396cedc5a0f6f8eb83
- resources-offline.json: e0a9087797dd11a0469fb9bb3eaf8696ee5247490f4f315d1ab07f8921d000d8

Hash-framing partition:

- report.json: b74aa047f000a427f69b3830ee7492701d1eaa0b82d3831e4924e65b38af34a2
- samples.jsonl: 97092ab34a5578a808e1fdf08430aa27e9e34fd9066e5e5324c4f3b3e19ad136
- manifest.json: 2e6ef9b28fa023968e9cdfb4a702297badafa46d09df0d4fc61a1912525bc151
- go-aa.json: 5f63cc9fc78333e92175e7877a99579d0955fe7c7def0cd3df4464f136869450
- resources-offline.json: 6034372b83ddc251bdff988c70204d51f54c0a30451d6c14f46c21ea47cbf6aa

Both reports embed the parsed frozen parent whose raw SHA256 is
a47093f4a3ea785a310f7ca1baa92444deaf7e21db5ba7db72dc897ddc3d047b.
Independent canonical hashing, excluding only timing_aa, yields stable identity
2bee6d04f6e7486c518b9f788f98e16828af619fecfff53c7e36cd0c17d81bc4.
Budget contents match the parent and current budget file
76a57ecacf2fba39b1f278e385f1cf4ee410c49f29d2d0e7e7dd2e2713b5ae93.

Selected and declared role bindings agree:

- Go: fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314
- Normal R6: f1fb7a9e294a8f96bd9835de4c6f32318b0adf01bb164db648c4fb9c0ad5140f
- Immutable R5 fixture: c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314
- Native meter: 17c3b73491b2a31e465f58eda9f210000b5cd886a4ba9fc0764dc3576899127c
- Unchanged collector: e2e83fa118c58a96ff55700be88f95f9b8b6479a289a8c59f447f836389fde51

All 22 recorded end identities per report were independently rehashed:
selected executables, six runners, fixture metadata, immutable fixture source
snapshots/toolchain, parent, budgets and candidate metadata. Current bytes match.
Candidate metadata binds the selected executable to normal_release, empty
features and cloud_synthetic_token_enabled=false; all 47 candidate source hashes
match the parent and current source bytes. This checks provenance without
executing a build or accepting build costs.

The initial per-partition manifest records passed=false, complete=false and empty
summaries; other fields agree with the final report. The full request JSON is a
pre-ACK/pre-quiescence snapshot, not a launch verdict. Final reports and cleanup
records retain passed=true, complete=true, contracts passed, no failure,
no unstarted workloads, and passed end identity with no changes.
Offline resource files bind actual report/journal hashes and resolved paths,
role-path projections, source restrictions and source status. Their
acceptance=not_assessed remains descriptive rather than a competing verdict.

## Retained native observations

Each journal has **140 records: 120 measured plus 20 warmups**.
Each workload/role contributes exactly **30 measured and five warmups**.
Two separate Go contract-reproducibility controls make **142 native observations
per partition, 284 total**. Controls are outside timing journals and resource
populations. Full, hash and earlier same-Go timing A/A populations remain separate.

All sample JSONs match journal/control records; capture observations and all
meter fields bind to validated samples. Capture JSON's valid=false and
contract_validated=false is its pre-validation snapshot; capture_complete=true
and subsequent sample validation agree.

Exact workloads use basic schema, seed 2026, one row/batch, respectively one or
128 batches, followed by status code 3, message "fixture stream complete".
Sink is empty and TLS false. Report session index is zero; per-round session
indices equal the round index. Derived seeds, sequential seeded AB/BA schedules
and exact stream definitions reconstruct and agree across modes.

Every sample, including warmups and controls, retains:

- Exactly one About GET then one authorized DoGet, exact ticket
  {"stream":"logs"}, one subscription, and no retry/reconnect.
- Auth checks 1, invalid auth 0; ticket checks 1, mismatches 0;
  unexpected requests 0 and DoGet-before-About 0. Raw HTTP/Flight authorization
  agrees with summary counters.
- All expected batch emit-start/done and visible events, no uncorrelated batches.
  Event counts and sequences match fixture summaries.
- Deliberate remote InvalidArgument, reason status, message "fixture stream
  complete"; native termination {"exit":1}. Whole stderr parses as COMMAND_FAILED
  with retryable=false. This is expected remote failure, not exit success or
  cancellation.
- No timeout, sample errors, fixture panic, fixture stderr, retained-output
  truncation, dropped visibility/resource bytes or dropped fixture events.
- Native CPU user/system, peak RSS, faults, context switches and block I/O
  from native_child_wait4; proc I/O fields from
  linux_proc_pid_io_before_wait4, available in every sample.

Configuration before/after hashes match normalized retained config bytes.
Each workload has one complete contract across roles, warmups and modes;
all four Go controls share the one-row contract.

Whole stdout proof:

- One-row: **162 bytes**, one object, one LF-separated batch;
  SHA256 50f93c839dfdd227382e2ecd88ab7be87e83ce8855794437fcfb781c780d04a9.
- Many-small: **22,102 bytes**, 128 objects and 128 LF-separated batches;
  SHA256 2ead794a980c862ced2a1b1ce151ae32cbf6a9317ea98b57131fbdea33c6f457.

Every stdout fits entirely in its prefix file. Offline whole rehashes match
sample, contract and fixture SHA256/lengths. These are whole-stream proofs, not
prefix hashes. whole-stdout-proofs.json retains a separate proof for all 284.

## Independent offline reductions

Frozen R5 basic-schema formulas reconstruct every complete row: sequence i,
integer 9007199254740993+i, value i/4, constant UTF-8 message, null optional,
and microsecond timestamp 1704067200000000+i. An independent serializer for this
finite domain reconstructs sorted JSON, Go HTML escaping, trimmed fractional
timestamps, numeric spelling and two LF bytes after every row. Whole reconstructed
bytes and every parsed object match native captures and fixture expectations.

Full collection recorded **9,032 JSON parse attempts** across measured, warmup
and control samples; hash-framing recorded **142**, one first object per sample.
Measured timing populations alone account for **7,740 versus 120** attempts.
Hash sequence-errors is null because later records were not parsed during
collection. Offline analysis parsed all **18,064 retained objects**, checking
complete values/sequences and exact bytes in both modes. This confirms reduced
mode's retained contract without relabeling its native parsing coverage.

Every report summary was recomputed from each role's 30 valid measured samples.
Counts, medians, minima, maxima, sample standard deviations and first-record-
after-exit counts match; the latter counts are all zero. Every offline resource
metric, availability/missing count, coverage total and source-flag distribution
matches the journals, with measured and warmup populations checked separately.

Derived wall/CPU/row rates, first-record/discovery/terminal timings, encoded and
decoded rates, fixture expected-output accounting and batch latencies match raw
operands. Raw schema plus batch message accounting matches encoded bytes;
per-batch decoded bytes and row counts agree. These arithmetic rates remain
absolute observations rather than admitted comparative results.

Fourteen offline byte-buffer changes (truncation, append, altered byte and
many-small reorder) fail original whole-output digest predicates. Eight in-memory
contract changes (row count, termination, ticket hash and stdout hash for each
workload) fail complete-contract equality. These 22 reductions leave raw files
untouched and execute no collector/test/native path. They establish sensitivity
of exact integrity checks, not causal effects.

## CPU admission and absolute distributions

Predeclared CPU-ceiling and interference screens both require **100 ms**.
All 284 native observations and host envelopes fall below the threshold.
Among the 140 journal records per mode, full native wall is
2.507859–45.248288 ms and host envelope 4.365665–65.428638 ms;
hash native wall is 2.601736–53.277811 ms and host envelope
4.179918–72.250533 ms. Quietness does not follow from these durations.

All samples retain both screen statuses resolution_limited,
comparative_admissible=false and throughput_admissible=false.
Finite production's own throughput flag is true but cannot override CPU admission.
The short-duration branch precedes residual/saturation admission; no derived clean
residual is available to interpret. Host raw counters/deltas, 100 Hz resolution,
CPU count, busy time, envelopes and ordered snapshot boundaries agree.

All **32 candidate-over-Go metric entries** are null with the exact host CPU
quarantine reason; throughput metrics additionally retain fixture/observer-or-
schedule attribution restrictions. Contract comparability cannot lift the gate.
Distinct role/mode absolutes support no speed ratio, ranking, observer-effect
estimate, saturation attribution, interference-free claim, practical budget
classification or inferred causality.

Native client CPU is wait4 user+system. Observer CPU is collector RUSAGE_SELF
around capture, excluding children and post-capture validation. Fixture CPU is
the lifetime user/system difference between pre-meter and post-exit summary
snapshots. Envelopes differ. Expected-output encoding is monotonic wall time
overlapping fixture CPU and is never subtracted from native CPU. All raw
components, differences, sums, envelopes and encoding-wall deltas were checked.

Absolute measured distributions below use **n=30 per role/workload/mode**,
warmups/controls excluded. Values are milliseconds, median [min, max], rounded
only for display. They are descriptive observations.

Full collector:

- One-row Go: fixture CPU 2.442 [1.784, 3.917]; observer CPU 0.771 [0.680, 1.300];
  encoding wall 0.060514 [0.031035, 0.116879].
- One-row candidate: fixture CPU 2.018 [1.606, 3.143];
  observer CPU 0.744 [0.627, 1.260]; encoding wall 0.036654 [0.031586, 0.151721].
- Many-small Go: fixture CPU 14.428 [11.429, 18.012];
  observer CPU 4.562 [2.192, 6.315]; encoding wall 0.818246 [0.445254, 1.311451].
- Many-small candidate: fixture CPU 13.770 [11.400, 16.348];
  observer CPU 4.485 [3.268, 14.355]; encoding wall 0.819075 [0.482499, 1.302504].

Separate hash-framing observer control:

- One-row Go: fixture CPU 2.480 [2.108, 4.821];
  observer CPU 0.8025 [0.671, 1.612]; encoding wall 0.055531 [0.031421, 0.350842].
- One-row candidate: fixture CPU 2.194 [1.794, 2.770];
  observer CPU 0.7325 [0.608, 1.324]; encoding wall 0.064420 [0.031700, 0.132423].
- Many-small Go: fixture CPU 15.881 [12.452, 18.767];
  observer CPU 3.602 [2.341, 4.737]; encoding wall 0.885201 [0.497427, 1.482409].
- Many-small candidate: fixture CPU 13.1645 [11.419, 18.140];
  observer CPU 3.3615 [2.351, 14.479]; encoding wall 0.712916 [0.428663, 1.154206].

Unrounded distributions, all native absolute metrics and per-population coverage
remain in analysis.json. No fixture/observer CPU subtraction or cross-mode effect
estimate was performed.

## Proc trend coverage and recorded quiet-window chronology

Journals retain only three raw proc observations:

- Full: one-row candidate warmup round 2; one observation.
  Measured full timing samples therefore retain zero raw proc observations.
- Hash: one-row candidate measured round 19 and Go measured round 24;
  one observation each.

Other journal captures record missing proc attempts: **139 full**, **138 hash**.
All four Go controls lack raw proc observations. Each retained record is live,
positive-RSS, within its native interval and before the five-second trend warmup.
**All 284 samples have zero admitted proc trend observations**;
RSS/FD/task/sampled-CPU first/last/min/max/slope fields are null. This supports
neither a stable-resource trend nor absence of growth, leak or long-run pressure.
Native peak RSS and wait4 counters remain actual observations even where proc
trend coverage is absent.

Prepared records retain independent ACKs and exact argv/token, start deadline,
hard end, quiescence, cleanup and process snapshots:

- Full ACK 18:29:47.239028Z; launch 18:30:56.540849Z; end 18:31:03.241041Z;
  cleanup 18:31:14.490538Z; recorded PID 912324.
- Hash ACK 18:31:53.293152Z; launch 18:32:25.285730Z; end 18:32:32.259677Z;
  cleanup 18:32:55.030441Z; recorded PID 1230981.

Both launches precede explicit start deadlines and bounded hard ends.
Exact argv selects normal candidate, workloads, mode, warmups, repetitions, seed,
output directory, frozen parent and ACK token; its SHA matches the report.
Execution exit is zero, own-lane quiescence explicit, known executable-path
process snapshots empty before/after, and cleanup records zero remaining owned
processes plus lock reacquisition/release. Samples fit execution records, are
sequential, and partitions do not overlap.

These are retained coordinator attestations, including the supplied PID-gone/
cleanup acknowledgment; this audit did not inspect PIDs, reacquire locks or
independently witness quietness. partition_budget.required_s=21300 is the full
worst-case estimate; finite execution checks the next 120-second timeout plus
30-second cleanup reserve against the 900-second window. It is not duration-tier
upfront fit or permission to exceed the window.

Schema 2 has no collector-bound journal digest. Present whole-file hashes,
byte-identical inventories, cross-artifact agreement and reconstructed oracle
establish scoped retained-data consistency. They cannot prove historical timing
authenticity against a coordinated rewrite. No new procedural gate is imposed.
Parent evidence-document updates are outside this review's ownership.
