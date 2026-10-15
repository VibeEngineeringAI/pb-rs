# Complete R6 finite measurement evidence review — Sol

**Accepted for scoped retained-evidence integrity. No genuine artifact, contract,
counting or summary blocker found. Comparative performance admission remains
blocked.** All twelve finite partitions were independently reduced, including the
ten new partitions. This supports proceeding to separately coordinated duration
screening under the existing policy. It does not establish duration adequacy,
speedup, equivalence, regression budgets, lifetime memory release, profiler
adequacy, build-cost acceptance or whole-Phase6 acceptance. Phase7 remains
user-paused.

This is an offline review in
/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail on 2026-10-14. It covers
campaign-r6-finite-{full,hash}-{01..06}, their sample artifacts and coordination
records, all twelve resources-offline.json files, the frozen parent, selected
small source/metadata identities, and the root's complete-finite evidence JSON.
The previous first-pair review supplied a method, not a substituted population:
all 1,704 current observations were rebound and independently checked here.
Prior independent strace capture/analysis and other profiler audits are separate.

Only this document and
/home/ajs/.cache/pb-phase6/finite-complete-review-sol/ were written. No native
binary, fixture, meter, profiler, build, process-spawning test, child agent,
network operation, real lock/token operation, live process inspection/control or
commit was performed. All reviewer scratch paths are under the owned cache;
/tmp was neither used nor manipulated. Existing artifacts and failed reviewer
attempts were preserved. Root-owned measurement prose and evidence JSON were
read, not edited.

The retained reducer and probes install an audit hook before analysis imports;
it denies process execution/control, locks and sockets, blocks unsafe imports,
replaces execution/kill/lock entry points, and confines writes to the owned cache
and review document. TMPDIR/TMP/TEMP point to that cache. No collector or fixture
module was imported. Their source was read to establish formulas and scope.
Reproduce the main reduction with:

    python3 -I -B -S /home/ajs/.cache/pb-phase6/finite-complete-review-sol/analyze.py

The final pass completed **671,893 counted checks, zero failures**. These are
retained-data assertions, not additional experimental observations. The input
inventory contains **25,743 files / 161,380,996 unique bytes**. Whole-file SHA256
and size inventories before and after are byte-identical. Bounds are 128 MiB per
input, 512 MiB aggregate, 3,000 files per partition, 10,000 records per JSONL and
1 MiB per JSONL line. The independent output oracle streams one row at a time;
it does not retain a 32.5 MB synthetic stdout file. Largest retained stdout prefix
is 65,536 bytes; stderr 157, visibility 9,046, proc observations 899 and batch
latencies 14,692 bytes. All fit their retention caps.

The initial completed reduction preserved 140 failed reviewer assertions in
analysis-attempt1.json/log and analyze-attempt1.py. It incorrectly equated paced
batch event spans with the narrower Send-call aggregate. Frozen fixture source
places sendStart after recording batch_emit_start: these intervals differ. The
corrected check binds live Send aggregates to the exact batch_send_call_ns
summary and bounds their total by the larger event spans. No capture/report was
changed. An earlier shape probe's missing parent fixture key is also retained
in probe.py/probe.log; it was a reviewer schema assumption, not bad evidence.

The frozen parent raw SHA256 is
a47093f4a3ea785a310f7ca1baa92444deaf7e21db5ba7db72dc897ddc3d047b;
its embedded contents, budget contents and canonical stable identity match every
report. Current small files, including the collector, parent, budget, fixture
source snapshots/metadata, bound runners and all 47 candidate source files,
were rehashed. The collector is still
e2e83fa118c58a96ff55700be88f95f9b8b6479a289a8c59f447f836389fde51.
Candidate metadata still binds normal_release, empty features and
cloud_synthetic_token_enabled=false. The 22 recorded end identities agree
across all twelve reports and all report end checks are passed with no changes.

Selected executable identities are **recorded and cross-bound**, not freshly
whole-file rehashed in this review:

- Go: fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314.
- Normal R6: f1fb7a9e294a8f96bd9835de4c6f32318b0adf01bb164db648c4fb9c0ad5140f.
- R5 fixture: c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314.

The native meter and large Go toolchain executable also remain recorded-only
identities. analysis.json enumerates those exclusions explicitly. This audit
does not turn earlier identity attestations into new executable measurements.

Each partition has **120 measured + 20 warmup journal entries**, with exactly
30 measured and five warmups per role/workload, plus **two separate Go contract
controls**. Totals are **1,440 measured, 240 excluded warmups and 24 controls**.
Every sample.json is bound to its journal/control record; capture and meter
fields agree. capture.json's valid=false/contract_validated=false is the saved
pre-validation snapshot; completed samples correctly carry true validation.
Initial manifest.json's false/false/empty-summaries state is likewise an initial
snapshot, with remaining fields agreeing with the final report.

The exact pairs are one-row/many-small, large/wide,
dictionary-finite/compressed-lz4, compressed-zstd/empty-batch,
paced/slow-finite and dictionary-large-lz4/dictionary-large-zstd. Definitions
were independently reconstructed, including schema, rows, batches, dictionary
settings, compression, status, sink delay and timeout. Seed is 2026, report
session index zero and TLS false. Derived workload seeds and every sequential
seeded AB/BA schedule agree with journals and across collector modes. Per-round
session indices equal their round, rather than the report's index zero.

Full collection is native_unprofiled; hash-framing is observer_control. Each
role/workload/mode retains its own population. Warmups, contract controls,
prior same-Go timing A/A and modes are never pooled for summary statistics.
Cross-mode checks here concern identity, schedule and deterministic contracts,
not a performance effect estimate. No fixture or observer time was subtracted
from native CPU or elapsed time.

Every one of the 1,704 observations passes the following retained contracts:
one About GET before one authorized DoGet; exact ticket {"stream":"logs"};
one subscription and no retry/reconnect; one auth and ticket check, with zero
invalid auth, ticket mismatches, unexpected requests or DoGet-before-About.
Raw HTTP/Flight credentials and request events corroborate exact summary
counters. Isolated config hashes before/after agree with normalized retained
config bytes. Complete contracts agree across roles, warmups and modes; all
24 Go controls share the one-row contract.

The expected terminal is deliberate remote InvalidArgument, message "fixture
stream complete", reason status and native exit 1. Every retained stderr is
whole-file rehashed and parses as COMMAND_FAILED with retryable=false.
This is a fatal-terminated finite observation, not cancellation or successful
stream completion. No timeout, sample validation error, fixture panic/stderr,
event loss, visibility loss or resource-log loss is recorded. Complete event
sequences, kinds, batch starts/dones, row totals, encoded/decoded accounting,
visibility correlations and batch latency arithmetic/trends/buckets agree.

An independent finite-domain serializer implements the frozen basic, wide and
dictionary value formulas, sorted JSON keys, Go HTML escaping, numeric spelling,
microsecond timestamps and batch LF framing. It reconstructs each workload's
whole-stream length and SHA256, then checks every observation against that
oracle and the fixture expectation. Empty-batch is exactly one LF, zero rows
and one batch; its first-record and CPU-per-row metrics correctly remain null.
The workload named wide actually uses empty payload strings under this exact
specification; it is not evidence of a large-payload wide-row stress case.

**724 observations retain their entire raw stdout**, which was rehashed and
compared byte-for-byte with the independent oracle. **980 retain capped prefixes**:
all 65,536 prefix bytes match the oracle; dropped-prefix counts equal total bytes
minus the cap; the collector's recorded whole-stream digest/length and fixture
whole-stream expectation both equal the independently reconstructed oracle.
Those 980 cannot be independently rehashed or reparsed in full from retained raw
stdout. This distinction is recorded per observation in whole-output-proofs.json.
Hash agreement is strong deterministic consistency evidence, not historical
capture authentication.

Both dictionary-large codecs independently reconstruct **32,506,837 stdout
bytes, 36 rows, nine batches**, with whole-stream SHA256
3ab3830e286d3c63776291df3cfd4f2381708c7ef782257758b3b0050b2ed06f.
Each retains 65,536 prefix bytes and records 32,441,301 unretained prefix bytes.
The cycle has exactly nine dictionary messages: one initial, six deltas, two
replacements and two resets, with maximum dictionary size 64. Exact bucket
counters cover all batches and rows without overflow; each batch records one
dictionary message. Every final batch is followed by the specified minimum
500 ms hold before terminal status. These are resource_pressure observations;
their producer throughput admission is false, and their wall time remains
schedule_bound_500ms_hold. Logical dictionary size/decoded-byte accounting and
peak RSS are not allocation, peak dictionary ownership or memory-release proofs.

Paced delivers exactly eight scheduled 32-row batches, stops by count with no
deadline/termination omission, and spans at least the prescribed 140 ms between
schedule start and completion. Its exact live summaries, schedule fields,
counts, and Send aggregate consistency pass. Some nanosecond aggregate operands
are only retained as exact fixture summaries, not as separately timed raw Send
start/end pairs; this review does not invent that missing independent granularity.
slow-finite retains its 100 ms initial read pause and 10 ms read delay in the
scenario/sink contract. Both workload scopes remain absolute-only. A Send span
includes marshaling/copy/flow control and does not identify pure blocking time or
prove application consumption.

The full collector records **2,755,842 JSON parse attempts** across all its
measured/warmup/control observations; hash-framing records **782**, one per
nonempty observation. Empty observations parse zero objects. Full sequence
errors are zero; hash-framing sequence_errors is null because later records
were not parsed during collection. Reconstructing deterministic oracle bytes
does not relabel the reduced collector's parsing coverage.

Every report's medians, minima, maxima, sample standard deviations, metric
availability and first-record-after-exit counts were recomputed from its valid
measured samples only. Every offline resource summary's measured/warmup counts,
availability/missing values, coverage totals, source flags, role paths,
restrictions and input report/journal hashes agree. Absolute rates, native CPU
sums, discovery/first-record/terminal timings and fixture encoding-wall deltas
match their operands. Native wait4 CPU/RSS/fault/context-switch/block-I/O and
proc I/O scopes remain distinct from observer RUSAGE_SELF and fixture lifetime
CPU differences. Expected-output encoding wall overlaps fixture CPU and is
never a subtraction operand for native CPU.

CPU screening was recomputed from raw host counter deltas and interval bounds,
using the unchanged 100 ms minimum, 100 Hz tick resolution, 20 CPUs, observer/
fixture busy fractions, all-core interval uncertainty, raw residual and clamped
derived residual range. Classification precedence and comparative/throughput
flags match. Among measured observations alone:

- Full mode: 440 resolution_limited, 63 ambiguous and 217 flagged; total 720.
- Hash-framing: 480 resolution_limited, 53 ambiguous and 187 flagged; total 720.

Warmups separately contain full 74/8/38 and hash 80/9/31 in that same
resolution_limited/ambiguous/flagged order. All 24 contract controls are
resolution_limited. There are no measured samples admitted as below_screen for
host interference. Resolution-limited cases have no residual admitted for
interpretation; ambiguous cases straddle the predeclared bound, while flagged
cases exceed its lower bound. They must not all be described as merely short
or clean. Below_screen potential fixture/observer CPU does not override a
flagged/ambiguous host screen.

All **192 candidate-over-Go metric entries remain null**. Each retains the exact
host-CPU quarantine reason; schedule, sink, pressure, throughput-attribution and
unavailable-metric exclusions were independently reconstructed per metric.
Comparable deterministic contracts do not lift CPU admission. Absolute values
therefore support no speedup, equivalence, ranking, practical relative budget,
observer-effect estimate, saturation attribution or interference-free claim.
The one-second host-after-finite05.json snapshot is outside campaign evidence;
it was not used to classify samples or attribute their host load. Its unrelated
Parseable/VM/gh/mise activity grants no process-stop authorization.

Across all observations, **1,245 raw proc records** and **1,831 missing proc
attempts** are retained. Live positive-RSS records precede native exit and all
fall before the five-second trend warmup. **Admitted trend samples are zero**;
RSS/FD/task/sampled-CPU first/last/min/max/slope values are null throughout.
Even the larger pressure windows last only about one second per native
observation. Native peak RSS and wait4 counters remain valid finite observations;
none demonstrates stable long-run memory, absence of leaks, release after
reuse, or bounded retention over a duration run.

Retained execution/cleanup chronology binds exact workload/role/mode argv and
token hashes, report hashes, zero collector exit, bounded launch/end, no recorded
outer timeout, PID-gone/empty-owned-process attestations and common-lock cleanup.
All native samples fit recorded execution bounds; no native intervals overlap
within or between partitions. New partitions run from 22:51:39.644512 through
23:03:01.241656 UTC. Per-window chronology and all report/journal/artifact hashes
are in the cache inventories and analysis timelines.

These are coordinator attestations: this audit did not witness historical
quietness, pauses, PIDs, locks or cleanup. The resumed Phase6/paused Phase7 and
own-lane coordination are retained context, not a host-wide idle claim. The
finite per-next-sample timeout/reserve admission must not be confused with the
upfront fit required for duration partitions. Root reports that the cached
launcher's unused timeout fallback was changed from SIGTERM to SIGINT for future
policy compliance. No execution record shows that fallback exercised; the
launcher was not run here and its edit changes no frozen capture identity.

The root's docs/evidence/phase6/finite-complete-r6.json snapshot is 211,215 bytes,
SHA256 5c1e9b3f7ad222564597644dfed56f5558197374a3d1061e9e856aaa655f2a4b.
All twelve unique partition records, 36 bound report/journal/resource artifacts,
summary projections, per-workload/label measured CPU classifications, new
execution/cleanup projections and global counts agree. Its then-pending review
status is administrative context, not acceptance evidence. Root's Complete
finite collection prose matches the checked scope and limitations; it remains
root-owned. Subsequent review-status edits create a new summary identity and
do not alter the capture verdict at this recorded snapshot.

The reducer also preserves 63 buffer/structure-only negative probes: altered
oracle contract fields and append/truncate/alter mutations of fully retained
small outputs fail the original predicates. They exercise no collector or native
path and add no causal or statistical adequacy claim. Schema 2 lacks a collector-
bound journal digest; byte-identical local inventories and cross-artifact/oracle
agreement establish scoped retained-data integrity, not authenticity against a
coordinated historical rewrite.

There is no remaining finite evidence-integrity blocker to the next staged
duration screening. Such screening still needs a fresh authorized bounded
window, existing upfront timeout/cleanup fit, exact duration schedule/churn
coverage, resource trend coverage after warmup, and unchanged CPU admission.
The present host screens block comparative interpretation; finite completeness
cannot waive that gate. Duration screening/soaks remain outstanding and no
readiness statement here resumes Phase7 or authorizes a measurement launch.

Audit artifact SHA256 values:

- analyze.py: deb231cc6c6f86629412a1995c13f2b731cdb2bfee9a1bd514258d95c10e745a.
- guard.py: d65524fbf9fc1f00ca3284ec15403e2df056da7c356fbe633480732d5e677216.
- analysis.json: 3ba61fd64854f6d6169d1de449b05ef74524f2b8f4057d2176d5e79bf66c8a65.
- analysis-attempt2.log: d5c01b310a83758481445873e4cd3c896c220cc445bc379b986a3d998f41d6dd.
- Both input inventories: 89d9382611ad0c6ee1055b95666a9a5c49f03f7636792941d2eb4e3470b0864a.
- independent-oracles.json: 8354b213050adbb594529e3a34a217a03c7b78fb2d811d77f477868b92059814.
- whole-output-proofs.json: 1a7170939eaa7ededde361a46841358a1371287a2f884a6a01e5c5b89966b5d8.
- negative-probes.json: 0759e4aa6fb303d33548f65e6218e182f1100493c7782441d9c4194f89daa494.
- root-summary-audit.json: 1a01e7260211469627a97b2a68cf11efe4d20a687d42227dc1bfb81982b3deeb.
