# First native same-Go A/A evidence review — Sol

**Accepted for scoped retained-evidence integrity. No actual data or interpretation defect found.**
The full and matching hash-framing reports are complete, valid same-Go observations.
All ratios correctly remain null under the predeclared CPU-resolution policy. This
acceptance establishes neither statistical equivalence nor a speed/budget result.
No Rust candidate was measured; its declaration in the parent/CLI arguments does
not make it a measured role. No new procedural gate is imposed by this review.

## Scope and reproducibility

Fresh offline review on 2026-10-14 of:

- `campaign-r6-timing-aa-full-01/` and `campaign-r6-timing-aa-hash-01/` under
  `/home/ajs/.cache/pb-phase6/`, including report, initial manifest, journal,
  contract controls, offline resources and every retained sample;
- the prepared parent manifest and each execution/process-snapshot JSON;
- the measurement plan, operating decisions, Opus measurement R5 and resource R2 reviews.

Only this document and the new cache
`/home/ajs/.cache/pb-phase6/first-aa-review-sol/` were written. No collector import,
native execution, test, build, fixture, profiler, performance-lock operation or
agent was used. Focused format/formula reads do not reopen resolved tooling/source reviews.

Independent standard-library Python analysis finished with **122,900 consistency
checks, zero failures**, covering **4,301 input files / 136,705,062 bytes**.
The complete before/after inventories are identical. Bounds were 128 MiB/file,
512 MiB/inventory, 10,000 JSONL records/file and 1 MiB/line; an audit hook denied
process/lock operations and writes outside the new cache.

Reproduction command:

```sh
python3 -I -B /home/ajs/.cache/pb-phase6/first-aa-review-sol/analyze.py > /home/ajs/.cache/pb-phase6/first-aa-review-sol/analysis.log 2>&1
```

`commands.md` records execution and exploratory read methods. The first analysis
incorrectly assumed every short sample lacked a raw procfs observation. Its three
failures were reviewer assumptions, corrected after reading the exact records;
the original failed results remain as `analysis-attempt1.*`. A subsequent passing
log is also retained. The final version adds sequence/mode/provenance checks.

| Review artifact | SHA256 |
|---|---|
| `analyze.py` | `79635ecb7e6acf401f5eb48f1325b826d1a99bfb9ad1dfb6095dc85d49fa2a68` |
| `analysis.json` | `d77085a2c38b26f9cbe7c5cf905b5f69899953fed7f5d481f07954b7491e5bbc` |
| `analysis.log` | `a30fb5fddc0961e3a8a9c3b16ac7693cd7600bd1b9ac7ee2baa794e7bec35aa0` |
| Both input inventories | `57560f82d0f2c2a352b48c0f0a3032d4e14982840f3e6606e3b8cc225b0f6c3d` |

## Provenance and source accounting

The actual collector is unchanged at
`e2e83fa118c58a96ff55700be88f95f9b8b6479a289a8c59f447f836389fde51`;
budgets remain
`76a57ecacf2fba39b1f278e385f1cf4ee410c49f29d2d0e7e7dd2e2713b5ae93`.
Both embedded parents equal the bytes parsed from prepared manifest SHA256
`a47093f4a3ea785a310f7ca1baa92444deaf7e21db5ba7db72dc897ddc3d047b`.
The independently recomputed canonical stable-manifest hash is
`2bee6d04f6e7486c518b9f788f98e16828af619fecfff53c7e36cd0c17d81bc4`.

Both labels select Go SHA256
`fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`.
The selected fixture is immutable r5 `c85f63f9…f5ce314`, and meter
`17c3b734…899127c`. Every one of the 20 recorded end identities per report was
rehashed independently, including the six declared runners, fixture source
snapshots/toolchain, parent, budgets and measured executables. Current bytes
match their recorded identities; both reports retain passed end checks with no changes.

| Retained input | Full SHA256 | Hash-framing SHA256 |
|---|---|---|
| `report.json` | `9165e4a8af9f67bc33bdf12670184b210dd0d572208196394203ef4a1f3fa500` | `b197e2d261057ef3d97fba0cc7ebc210c82da8dc1bf550f9f78fe8d01db0fc90` |
| `manifest.json` | `d79cd3ba0c4628ce05e09d668a650a4e3ad48d86267ae2ce3681cbc233fd3401` | `d47c9c4d5ce70cf2fab23da77db2a99c4b570b4f23caca9f80861ed08fed4300` |
| `samples.jsonl` | `66870f29834405ad883b8ddc785d669029f3625f9356ff3ad3c10ad6f0be3c77` | `9509c717c72ffcdb3170579c0ab927c4bd51b89acd4fb6443a881bb2ed6df697` |
| `go-aa.json` | `8a153f9bb463dd995a2f5c00bbf9b17e1ce3fb4e023ad519c0672ce859f11e60` | `d2a2bba482de46ad9d38e4fcacb8b90c3c97697ae03f64dc94c224a6c368a69b` |
| `resources-offline.json` | `b2ff57c6c70d84ca6da7567c221fab8610b5989a73a2630238f6af31ee6a5774` | `f5f79325f574858db8c886692d0b0b77d6fb736d3f01d889b0a2065a0d5a21e7` |

`manifest.json` is the initial snapshot: `passed=false`, `complete=false`, empty
summaries. Its other fields agree with the final report; it is not a contradictory
final verdict. `resources-offline.json` binds the actual report/journal bytes and
resolved paths, copies restrictions, separates warmups, and declares
`acceptance=not_assessed`. Schema 2 has no collector-bound journal digest:
these retained-file hashes establish present consistency, not original timing
authenticity or protection against a coordinated historical rewrite.

## Counts, contracts and chronology

Each report contains 140 journal records: **120 measured + 20 excluded warmups**.
Each workload/label has 30 measured and five warmups, all valid. Two separate
`go-aa.json` controls bring each native partition to 142 samples. All 284 sample
files match their journal/control records; capture observations and every meter
field match too. Contract controls are correctly absent from the journal and
offline resource populations, rather than silently counted there.

Seed 2026, both exact scenario streams, derived workload seeds, all round/session
indices and randomized AB/BA order independently reconstruct and match across
full/control. All measured and warmup contracts match across labels and modes;
all four one-row contract controls match the timing one-row contract.

- One-row: 1 row, 1 batch, **162 stdout bytes**, SHA256
  `50f93c839dfdd227382e2ecd88ab7be87e83ce8855794437fcfb781c780d04a9`.
- Many-small: 128 rows, 128 batches, **22,102 stdout bytes**, SHA256
  `2ead794a980c862ced2a1b1ce151ae32cbf6a9317ea98b57131fbdea33c6f457`.

All stdout fits the saved prefix, permitting an independent whole-output rehash,
LF/batch recount and JSON/sequence check of **18,064 objects** across both reports.
Every output agrees with the independent fixture's exact hash/count expectation.
Actual collector parses per sample are full **1/128**, hash-framing **1/1**
(one-row/many-small). Hash-framing correctly records `sequence_errors=null`;
its full-stream byte/hash requirement still holds. This review's offline reparsing
does not change the native control's parse count or validate it by partial hash.

Each sample has exactly one HTTP/about and one DoGet/subscription; exact auth and
ticket checks each equal one, with all violation counters zero. Captured request
evidence covers the same request and terminal. About completion precedes DoGet.
The reconstructed stable contract includes full stdout/stderr, termination,
requests/ticket, workload, sink/TLS and normalized unchanged config. Fixture panic
counters/events are zero. Every exit is the deliberate **InvalidArgument / exit 1**
with the identical fatal diagnostic; these are failure-terminated finite streams.

Coordinator records show separate ACK tokens and exit 0 for:

- Full: **17:31:16.076852Z–17:31:24.783970Z**, about 8.707 s.
- Hash-framing: **17:33:18.936599Z–17:33:27.741985Z**, about 8.805 s.

Token hashes match; launch deadlines, hard ends and reservations hold. All native
intervals are sequential, within their execution records, and the partitions do
not overlap. Known-path before/after process snapshots are empty and bracket
every native sample. The user/coordinator supplies the separate quiet ACK and
lock/cleanup verification; this offline reviewer did not witness or reacquire a lock.

The retained `partition_budget.required_s=21300` is the sum of worst-case deadlines,
versus `reserved_s=900`. The frozen finite policy admits each next full 120 s child
timeout plus 30 s reserve; upfront whole-partition fit applies to duration tiers.
This field is not actual elapsed time or a 21,300 s window admission. The observed
finite partitions completed within their reservation; no new gate follows.

## Descriptive distributions and CPU attribution

Every cell is **median [minimum, maximum] in milliseconds**, with **n=30 per cell**.
Labels and collector modes remain separate. First record is the parent observation
of a complete object plus LF, relative to native start. Encoding is fixture wall
time; the three CPU columns are separate populations/intervals.

| Mode | Workload | Label | Native wall | First record | Client CPU | Observer CPU | Fixture CPU | Fixture encoding wall |
|---|---|---|---|---|---|---|---|---|
| Full | one-row | go-a | 30.197 [26.406, 39.454] | 28.480 [26.241, 36.745] | 38.669 [34.605, 44.660] | 0.803 [0.632, 1.334] | 2.407 [1.912, 3.216] | 0.057 [0.032, 0.135] |
| Full | one-row | go-b | 31.526 [25.875, 42.099] | 29.370 [25.456, 39.997] | 40.514 [34.364, 48.494] | 0.764 [0.676, 1.343] | 2.413 [1.857, 2.831] | 0.064 [0.035, 0.161] |
| Full | many-small | go-a | 38.677 [33.308, 46.849] | 29.311 [24.908, 35.727] | 44.720 [37.914, 54.766] | 4.558 [3.848, 6.125] | 13.814 [12.036, 18.529] | 0.766 [0.463, 1.169] |
| Full | many-small | go-b | 39.046 [33.293, 53.304] | 29.159 [26.397, 40.241] | 44.326 [40.474, 52.374] | 4.924 [2.472, 14.102] | 15.536 [11.914, 19.375] | 0.745 [0.463, 1.246] |
| Hash | one-row | go-a | 31.399 [26.227, 39.641] | 29.322 [25.155, 38.871] | 40.346 [33.407, 47.308] | 0.821 [0.697, 1.266] | 2.387 [1.899, 2.991] | 0.050 [0.027, 0.148] |
| Hash | one-row | go-b | 31.260 [25.823, 39.312] | 29.255 [25.603, 37.568] | 40.237 [32.995, 48.797] | 0.819 [0.651, 1.361] | 2.405 [1.873, 5.179] | 0.054 [0.032, 0.153] |
| Hash | many-small | go-a | 38.221 [32.206, 44.956] | 29.999 [24.816, 36.228] | 45.836 [37.342, 55.920] | 3.195 [2.549, 4.855] | 14.030 [11.215, 19.311] | 0.649 [0.424, 1.731] |
| Hash | many-small | go-b | 38.301 [32.267, 45.827] | 29.259 [25.583, 34.854] | 43.973 [39.678, 52.695] | 3.246 [2.386, 14.172] | 14.475 [12.109, 18.768] | 0.702 [0.461, 1.335] |

Native elapsed/CPU and first-result arithmetic reconstruct exactly within floating
precision. Client CPU is native wait4 user+system, including startup/discovery and
terminal processing; parallel CPU can exceed wall time. Observer CPU covers
`RUSAGE_SELF` around capture, excluding children/post-capture validation. Fixture
CPU is the final-minus-baseline lifetime counter difference, not the final lifetime
counter alone. Its request bounds encompass a wider interval than native timing.
The retained timestamps respect those interval boundaries; raw host tick deltas,
host elapsed and busy-time calculations also reconstruct. Encoding wall overlaps
fixture CPU. No CPU or wall overhead was subtracted.

Many-small observer CPU medians are descriptively lower in hash-framing mode, while
fixture encoding remains enabled in both. Separate sequential windows and
quarantined CPU evidence do not establish a causal overhead estimate or an
unconstrained throughput result. Both reports remain `kind=timing_aa`; the
hash-framing collector mode and per-sample `kind=observer_control` identify the
matching control.

All samples are below the predeclared 100 ms observation minimum. Both CPU screens
correctly say `resolution_limited`; comparative/throughput admission is false.
Every metric ratio is null with its restrictions preserved. There is no basis for
confidence/equivalence claims, relative budget acceptance, or reconstructing ratios
from these descriptive medians. Quiet ACKs do not repair per-sample quantization.

## Resources, coverage and caps

Every reported native absolute n/median/range/stdev and after-exit count was
recomputed from measured journal records, excluding warmups. Every offline
resource metric, available/missing count, coverage total and source-flag tally
was independently recomputed for both measured and warmup groups. This includes
faults, context switches, block/procfs I/O, native CPU/RSS and output/encoded/decoded
rates. Full precision descriptive results for each label are in `analysis.json`;
no missing values were imputed or invalid records admitted.

All groups have 30/30 available native resource and byte-rate values. Major faults,
block I/O and procfs read/write/cancelled-write byte counters are zero throughout;
character/syscall I/O is retained separately and is not socket/network traffic.
The rates divide bytes by full failure-terminated native elapsed. Encoded
FlightData protobuf and decoded Arrow buffer bytes are distinct from stdout and
network wire traffic.

There are only **two raw procfs observations in full mode and one in hash mode**:
full `one-row/warmup-003-go-b`, full `many-small/sample-021-go-a`, and hash
`one-row/warmup-003-go-b`. The other captures have one missing proc observation.
All raw observations precede the five-second trend warmup, so every trend has
zero points and null extrema/slopes. Native wait4 peak RSS remains independently
available. These short records support no RSS/FD/thread growth or release claim.

Every batch is correlated with a retained emission and separator: 1 or 128 per
sample, zero uncorrelated or dropped batch observations. Latency rows reconstruct
from their actual timestamps; there are no missing observations represented as zero.
No first record was observed after native exit. Request/event/visibility/resource
drop counters are zero.

Maximum retained sizes across all samples are stdout **22,102 B** (64 KiB cap),
stderr **157 B** (1 MiB), visibility **9,046 B** (16 MiB), resource JSONL **174 B**
(8 MiB), and batch latencies **14,656 B** (8 MiB). Fixture event logs are at most
65,997 B. Both offline reports are below 99 KiB, source reports below 119 KiB,
journals below 1.1 MB and longest journal lines below 7,851 B, within declared
offline limits. Original reports, raw records and all retained files were preserved.
