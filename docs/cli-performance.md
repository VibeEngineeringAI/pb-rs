# CLI resource efficiency campaign

This document retains the first campaign and its original measurements below.
The earlier CPU/heap investigation is recorded in
[the second campaign](#second-campaign-cpu-and-heap-profiling).
The latest implementation and streaming investigation are recorded in
[the third campaign](#third-campaign-streaming-and-durable-ingestion).

Baseline: `dd436da` (unchanged binary saved before edits). Both revisions use
Rust 1.96.0, the locked dependencies, `PB_VERSION=v0.0.0-harness`, and the existing
release profile: optimization level 3, thin LTO, one codegen unit, stripped.
Linux measurements were collected on the shared development machine on
2026-10-17/18. Seventeen delegated Opus 5.5 tasks covered six initial audits,
implementation, benchmark tooling and independent compatibility reviews.

## Measured results

The first fixed-suite run meets the 10% CPU target: **13.3% less CPU**, with a
bootstrap 95% interval of **11.6–15.6% less**. Wall time is 12.1% lower (interval
9.5–14.8% lower). Aggregate peak RSS is 0.7% higher (interval 0.2–1.1% higher),
so this is a CPU-efficiency result, not an across-the-board memory reduction.
All nineteen workload contracts match.

The second seed confirms the result: **13.5% less CPU** (95% interval
**12.2–15.3% less**) and 12.5% lower wall time (10.9–14.5% lower). Aggregate
peak RSS is 0.4% higher, with an interval spanning parity. The CPU ratio's
upper confidence bound stays below 0.90 in both independent runs. See the
[confirmation summary](evidence/cli-perf/seed2027-summary.json).

SQL JSON uses 28.4% less CPU at 10,000 rows and 28.2% less at 100,000 rows.
The single 8 MiB item uses 19.4% less peak RSS. No individual case has a
material regression under the harness rule: ratio interval entirely above
1.10 plus an absolute median increase over 1 ms or 1 MiB, as appropriate.
The executable grows by 38,720 bytes (0.29%). Full per-case medians and intervals
are in [the first run summary](evidence/cli-perf/seed2026-summary.json).

### Ingestion and HTTPS

With 20,000 records of 500 bytes and 1,000-event batches (seven measured
paired rounds after one warmup), JSONL uses **17.3% less peak RSS** (95% interval
16.1–18.7% less) and 11.4% less CPU (6.6–15.7% less). Text ingestion uses
15.2% less CPU (8.9–17.9% less). Wall time improves only about 2.4–2.5%; the
local fixture and durable checkpoint work dominate it. Both workload resource
gates pass with exact normalized wire bytes and all resume, completion, 503
replay and 413 split probes enabled. See [ingestion evidence](evidence/cli-perf/ingest-summary.json).
The ingestion CPU intervals do not independently prove a full 10% reduction;
the fixed CLI suite and JSONL peak-memory result provide that stronger evidence.

The system-root HTTPS status supplement is **neutral**: CPU ratio 1.015
(95% interval 0.953–1.082), RSS ratio 0.992 (0.980–1.007), wall ratio 0.990
(0.979–1.030). DER deduplication removes duplicate trust entries but has no
established end-to-end resource gain in this fixture. It is not counted as
proof of the 10% target. See [HTTPS evidence](evidence/cli-perf/tls-summary.json).

## Changes

- Generate the owned command specification from `spec.json` at build time.
  The public types, deserialization support and the JSON source remain intact;
  field-by-field parity tests compare the generated tree with runtime decoding.
- Construct the completion protocol command without cloning and discarding the
  full command tree. A parity test retains the former construction as an oracle.
- Serialize command discovery directly, avoiding repeated intermediate JSON
  trees. Escape generic JSON in a single pass and reuse its buffer when there
  is nothing to escape.
- Serialize each collected JSONL/text payload once for both byte budgeting and
  the durable upload queue. Preserve numeric lexemes, duplicate-key handling,
  field order, batch boundaries, checkpoints, fsyncs and acknowledgement order.
- Normalize SQL rows in their decoded tree and render directly into one reusable
  output buffer. Avoid a second value/map tree, redundant number parsing and an
  intermediate pretty buffer. Preserve sorted keys, last duplicate wins, checking
  overwritten values for overflow, float64 formatting and deep-tree cleanup.
  The opening bracket, per-complete-row write/flush, partial output, 8 KiB bridge
  bound and unread-tail cancellation remain unchanged.
- Deduplicate identical Linux DER trust roots within each load. Certificate
  sources, environment overrides, file/symlink selection and reload behavior
  remain unchanged; no process-global cache is introduced.

Diagnostics retain schema version 1, stage names, sampling and byte counters.
SQL `indentation_copy` now measures separator/base-indent staging; the fused body
pass belongs to `serialization`. In-place `value_conversion` can allocate zero
bytes. One remaining tree produces one timed drop per row instead of two.
Cross-revision stage timings therefore need this interpretation.

## Measurement method

The existing `harness/run.py` and native `pb-meter` compare fresh processes in
seeded randomized order, checking stdout/stderr, exit status, HTTP/Flight
requests and configuration effects for every sample. Neither tool was changed.
The fixed 19-case corpus covers startup/discovery, configuration, HTTP reads,
SQL at 500/10,000/100,000 rows, an 8 MiB SQL item, PromQL and Flight tailing.
Each comparison uses three warmups and twenty measured repetitions per binary.
A second seed confirms the same fixed suite. CPU is the primary resource;
wall time and peak RSS are also reported. Aggregate ratios are equally weighted
geometric means of per-case median candidate/baseline ratios; bootstrap intervals
resample observations independently within each case (2,000 draws).

A same-binary A/A control measured SQL CPU ratio 0.981 and RSS ratio 1.001;
`version` CPU ratio was 1.077. Short-command timings have a material noise floor. A second A/A control on the
final fixed CPU set measured CPU ratios 1.023 for `version` and 0.980 for
10,000-row SQL JSON, well below the claimed improvement magnitude. See
[the control summary](evidence/cli-perf/aa-summary.json).
Pinned-core `perf stat` (CPU 2, twenty repetitions) provides a separate instruction
count check. Startup `version` fell from 1,840,535 to 918,121 user instructions
(50.1% fewer; both counts had about 0.01% relative standard error).

Ingestion uses the new `harness/ingest_perf.py` with fixed 20,000 × 500-byte
JSONL and text corpora, 1,000-event batches and randomized paired runs. Its strict
wire-byte and checkpoint gates are enabled, together with unmeasured resume,
completion, 503 replay and 413 split probes. No resource claim uses profiled or
instrumented binaries.

Other unrelated workloads were active on this host. Final measurement processes
are pinned to CPUs 2,4,6,8 to avoid hybrid-core migration. The full synthetic
harness finished as timing started; no builds run during these windows.
Shared-host effects remain. These are warm-cache client-side
measurements against loopback fixtures, not production server latency or an
all-platform guarantee. Improvements apply to the measured workloads, not a
promise that every command improves by the same percentage.

## Verification

- `cargo fmt --check`, `git diff --check`.
- `cargo clippy --locked --all-targets --all-features -- -D warnings`.
- `cargo test --locked`: 843 passed, nine existing opt-in tests ignored.
- `cargo test --locked --features diagnostics --test diagnostics_process`: two passed.
- Additional Go completion parity replay passed. The interactive
  `sql_duplicate_first_error` replay passes for both Go and Rust with the
  corpus-pinned Go 1.25.4 reference. An initial Go 1.27.1 build caused Unicode
  quoting and interactive-reference differences; rebuilding with the cached
  pinned toolchain resolved the reference mismatch.
- Pinned Go parser/help replay: 2,760/2,760 cases passed (parity-only scope;
  deferred-command guards are not part of this run).
- Full Python harness: 986 tests across 35 modules, zero failures, using the
  existing pinned PTY virtual environment and an explicit Go executable.
- SQL differential checks include 5,000 random documents, 20,000 random finite
  floats, duplicate/overflow ordering, escaping, and deep/overwritten values.
- The Python harness needs its existing pinned PTY virtual environment and an
  explicit Go executable; the system Python/Go shim run failed prerequisites.

## Further candidates

The first audits also identified request-body copies across redirects/retries,
redundant gzip buffering, repeated profile decoding, typed ingestion outbox rows,
and schema-aware Flight body alignment. The second campaign below investigates
these paths with additional workload and compatibility checks. Profile decoding
remains a future candidate.

## Reproduce the fixed suite

Run from the repository root with prebuilt baseline/candidate executables and
`bin/pb-fixture`, `bin/pb-meter`. Recreate the exact suite from existing workload
definitions and the recorded case order:

```sh
python3 - <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, 'harness')
import cases, phase3_workloads
plan = json.loads(Path('docs/evidence/cli-perf/plan.json').read_text())
by_name = {case['name']: case for case in cases.suite() + phase3_workloads.suite()}
Path('/tmp/pb-cli-perf-suite.json').write_text(
    json.dumps([by_name[name] for name in plan['case_names']], indent=2) + '\n')
PY
flock -n ~/.cache/pb-rs-performance.lock taskset -c 2,4,6,8 \
  python3 harness/run.py run --suite /tmp/pb-cli-perf-suite.json \
  --binary base=bin/pb-perf-baseline --binary final=bin/pb-perf-final \
  --fixture bin/pb-fixture --meter bin/pb-meter \
  --warmup 3 --repetitions 20 --seed 2026 \
  --out harness/results/cli-perf-new-seed2026
python3 docs/evidence/cli-perf/summarize.py \
  harness/results/cli-perf-new-seed2026/report.json final /tmp/cli-perf-summary.json
```

Repeat with seed 2027 and a new output directory. Choose an available fixed CPU
set on another machine. Native reports retain full contracts and raw captures
under `harness/results/perf-campaign`; compact committed evidence retains metric
summaries, binary identities, the plan and verification results.

## Second campaign: CPU and heap profiling

The follow-up uses Sol 6.1 implementation/audit rounds and Opus 5.5 independent
reviews. Its primary reference is the previous optimized executable
(`pb-perf-final`, SHA-256 `6be0db3a7c5f145ed75d0522b288c8ff69289c5f0bf8fe34c1622fcb180fe37c`).
The original `dd436da` binary remains a separate reference for total gains.
The current standard executable is `pb-perf2-final`, SHA-256
`8124d157236e215d2068ce4dadf97ebe08043a1a1c327d5dc3a69806785e12a1`.
The frozen [source hashes](evidence/cli-perf2/sources.json) identify the final
implementation, including test sources. No dependency versions changed.

### Follow-up results

The final matched-placement control establishes **17.2% less CPU than the
original build** across the unchanged 19-case suite (95% interval 15.2–19.2%
less). This clears the original 10% CPU target. Against the previous optimized
build, however, the two matched-placement aggregates are approximately flat;
this round does **not** establish another suite-wide 10% improvement. Large SQL
items and ingestion retain substantial additional gains. Both placement
conditions are reported below because executable placement affects the results.

On the original Btrfs executable paths, the 19-case suite uses
**19.8% less CPU than the original build** in
seed 2031 (95% interval 17.7–21.9% less) and **19.3% less** in seed 2032
(17.6–21.6% less). Both intervals establish the original 10% CPU target. Against
the previous optimized build, the additional reduction is **10.7%** (8.3–13.1%)
and **8.7%** (6.8–11.1%); these do not establish another full 10% across the suite.
No case has a material CPU or wall-time regression under the retained rule.
See the [first incremental summary](evidence/cli-perf2/seed2031-base-fixed.json),
[confirmation](evidence/cli-perf2/seed2032-base-fixed.json), and total-gain summaries
for [2031](evidence/cli-perf2/seed2031-original-fixed.json) and
[2032](evidence/cli-perf2/seed2032-original-fixed.json).

Relative to the previous optimized build:

- The 8 MiB SQL item uses **45.7–46.3% less CPU** across the two seeds; its gzip
  counterpart uses 45.0–46.3% less. Wall time for the plain item falls about
  36–37%. User CPU falls from roughly 39–40 ms to 13–15 ms, while system CPU
  remains around 14–16 ms.
- SQL JSON at 10,000 rows uses 9.6–10.0% less CPU; at 100,000 rows it uses
  8.0–11.0% less. The larger case retains roughly 101,532 write syscalls and
  49–52 ms of system CPU; savings come primarily from user CPU, with row
  publication unchanged. Large Flight timings show smaller, variable gains.
- Ordinary JSONL ingestion uses **43.3% less CPU** (interval 40.5–46.5%) and
  **18.6% less native peak RSS** (17.2–19.4%). Text uses **50.8% less CPU**
  (44.9–53.1%) and **19.4% less RSS** (17.4–20.1%). These are nine paired rounds
  after two warmups on the original executable paths.
- Escape-heavy JSONL uses **51.2% less CPU** and **19.2% less RSS**; escape-heavy
  text uses **65.5% less CPU** and **24.8% less RSS**. All four ingestion workloads
  have CPU and RSS upper ratio bounds below 0.90. Exact normalized wire bytes, durable state,
  resume, completion, 503 replay and 413 split checks pass. See
  [ordinary](evidence/cli-perf2/ingest-final.json) and
  [escape-heavy](evidence/cli-perf2/ingest-escaped-final.json) evidence.

All 26 workload contracts match in both seeds. Additional retained checks also
match unprojected normalized stdout/stderr and raw request-body strings, including
the discovery cases whose standard comparator projects Rust-only extensions.
Raw CPU, RSS, wall time, fault and syscall samples are retained for
[2031](evidence/cli-perf2/seed2031-samples.json) and
[2032](evidence/cli-perf2/seed2032-samples.json).

### Matched-placement resource controls

After the RSS investigation, byte-identical copies of the frozen binaries were
placed together on tmpfs. These are additional uninstrumented controls, not a
replacement for the original Btrfs results or a deployment recommendation.
The [control plan](evidence/cli-perf2/memory-control-plan.json) retains the
prospective settings and the amendment adding the original build. Each run uses
the same 19 cases, 20 repetitions, three warmups and CPU affinity. Owned builds,
tests, profilers and agent diagnostic executions had finished before these runs.

| Comparison | CPU change | 95% CPU ratio interval | Wall change | Peak RSS change |
| --- | ---: | ---: | ---: | ---: |
| Final / previous, seed 2033 | +0.25% | 0.9785–1.0258 | −0.23% | −0.38% |
| Final / previous, seed 2034 | −1.14% | 0.9686–1.0115 | −0.44% | −0.63% |
| Final / original, seed 2034 | **−17.20%** | **0.8083–0.8479** | **−15.13%** | −5.49% |

These are geometric means of per-case median ratios, with the same bootstrap
method used above. The incremental CPU intervals include parity; this is not a
formal equivalence test. Several small commands have higher CPU point estimates
in the controls, even though none crosses the retained material-regression rule
(an interval above 1.10 and more than 1 ms CPU/wall or 1 MiB RSS). The original-path
9–11% incremental aggregate should therefore not be presented as portable.

The 8 MiB SQL item still uses **44.3–44.9% less CPU** than the previous build;
100,000-row SQL uses **8.2–12.0% less**. The former's RSS rises about 1.2–1.3%, so
this round is not a memory win for every workload. Against the original build,
the confirmation shows 50.9% less CPU and 19.6% less RSS for the 8 MiB case.
All 19 contracts, unprojected normalized outputs and raw request bodies match.
See the incremental summaries for
[2033](evidence/cli-perf2/memory-seed2033-base-fixed.json) and
[2034](evidence/cli-perf2/memory-seed2034-base-fixed.json), the
[original-build comparison](evidence/cli-perf2/memory-seed2034-original-fixed.json),
and raw samples for [2033](evidence/cli-perf2/memory-seed2033-samples.json) and
[2034](evidence/cli-perf2/memory-seed2034-samples.json).

The matched-placement ingestion control repeats the ordinary 20,000-record,
500-byte corpus with nine rounds and two warmups. JSONL uses **44.0% less CPU**
(95% interval 39.3–47.2%) and **23.2% less RSS** (21.9–24.5%); text uses
**48.8% less CPU** (46.9–51.4%) and **24.9% less RSS** (24.7–26.9%). Each CPU and
RSS interval is wholly beyond the 10% reduction target. Strict wire-byte,
checkpoint, resume, completion, 503 replay and 413 split checks pass again.
See [matched ingestion evidence](evidence/cli-perf2/ingest-memory-control.json).

### Startup RSS investigation

The first two original-path captures report **18.5% higher aggregate peak RSS**
against the previous optimized build, with 15 fixed cases crossing the retained
memory-regression threshold. Those results are retained above; they are not
relabelled as memory passes. The unchanged baseline's `version` RSS had itself
fallen from 8.33 MiB in an earlier capture to 6.41 MiB, despite the same bytes.

Natural-write snapshots identify the existing-path difference in executable
file mappings: one comparison has 1,516 KiB more precise RSS, including 1,408 KiB
more executable RX pages, while anonymous memory is 4 KiB lower and heap RSS
is 64 KiB in both. Byte-identical private copies change RSS by over 2 MiB and
reverse the ordering. Thus the existing-path ranking cannot be attributed to
added heap use or binary size; the final executable is 16,832 bytes smaller.

Matched tmpfs copies remove the startup gap: across twelve diagnostic runs per
variant, precise RSS is 5,200 KiB for baseline and 5,226 KiB for final; native
post-exit peak RSS medians are 5,384 and 5,324 KiB. Anonymous RSS is 488 versus
484 KiB; heap is 64 KiB each. No anonymous or file huge-page counters are nonzero.
The original paths measured alongside those copies retain their larger gap.

A private same-inode write/read-cache crossover, with `mincore` confirming
resident → evicted → resident pages, did **not** reproduce the earlier large
reversal. The exact kernel/filesystem mechanism remains unresolved; do not claim
a particular large-folio cause. No global cache flush or frozen-file mutation
was used. Detailed measurements and identities are in
[the RSS attribution summary](evidence/cli-perf2/rss-summary.json) and
[raw diagnostic samples](evidence/cli-perf2/rss-samples.csv).

One reviewer ran `ldd` against the final binary during the second original-path
capture, touching its loader/page-cache state. That provenance is retained;
the later matched-placement runs took place after diagnostic activity ended.

All 225 successful snapshots have matching `smaps`, status RSS and high-water
values at that write boundary; the initial nine diagnostic failures were a
Btrfs device-ID classification bug and are excluded explicitly. Native lifetime
peak RSS differs from the snapshot by −8 to +692 KiB because execution continues
after release and the measurements have different semantics. These blocked-write
observations are attribution only, not ranked CPU/latency samples. Linux documents
`smaps` as the precise page-table snapshot; see the
[kernel proc documentation](https://www.kernel.org/doc/html/latest/filesystems/proc.html).

### Changes and compatibility constraints

- Transfer request `Vec<u8>` bodies into shared `Bytes` internally. Redirects,
  retries and OAuth replay clone the shared handle; public Vec-based interfaces
  remain supported. Remove the redundant gzip `BufReader`, retaining bounded
  transport flow, fragmented/multiple members and error propagation.
- Cache each typed ingestion event as its serialized JSON, using that same byte
  length for budgeting and those same bytes for upload. Existing version-one
  outboxes still decode through their original JSON normalization path. Keep
  durable writes, acknowledgement order, batch boundaries, duplicate keys,
  numeric lexemes and full-outbox replay after partial failures.
- Borrow existing command flags for parsing, rendering and discovery. Completion
  adds its synthetic root child through a borrowed overlay instead of cloning
  the full tree. Public owned command/flag types remain available.
- Copy ordinary JSON string runs from buffered slices. Quotes, escapes, controls,
  scalar lookahead, malformed prefixes and unread delimiters still use the
  original scanner state machine. Differential tests exercise fragmented reads,
  every byte value and injected I/O errors.
- Skip allocating an iterative drop worklist only for objects whose children
  are all non-container scalars. Mixed/deep trees retain iterative destruction,
  including scalar wrappers containing JSON arrays or objects.
- Format a restricted plain-decimal subset directly from its lexeme. The path
  has at most 15 significant digits and stays inside the float formatter's
  fixed-decimal range; exponents, wider decimals and boundary cases retain the
  prior float64 conversion. Negative zero and overflow behavior remain intact.
- Share validated Flight bodies aligned to four bytes. Arrow recursively aligns
  fixed-width buffers when necessary; dense-union offsets retain their explicit
  four-byte validation. Tests cover every base offset modulo 64, compressed and
  uncompressed bodies, dictionaries, unions and malformed buffers.

SQL still writes and flushes each complete row. No cross-row batching, larger
transport bridge, deferred validation, checkpoint relaxation or global cache
was introduced to obtain these gains.

### CPU and allocation attribution

The [before profiles](evidence/cli-perf2/profiles-before.json) and
[final profiles](evidence/cli-perf2/profiles-final.json) retain binary identities,
analysis hashes and two captures per workload. `perf` samples user CPU;
`heaptrack` observes Rust allocation calls. Instrumented timings and RSS are
excluded from release-performance comparisons. The existing profiling harness
also checks output/request contracts against pinned Go and release controls.
Both revisions use the same profiling Cargo settings and locked dependencies;
the recorded hashes distinguish the before/after source builds despite different
executable paths.

For 100,000 SQL rows, allocation calls fall from 1,401,267 in both baseline
captures to 1,226,245 and 1,226,415: approximately **12.5% fewer allocations**.
Peak tracked heap varies with buffering and is not evidence of a corresponding
process-RSS improvement. The 8 MiB string's scanner/reader work dominated the
baseline CPU samples; after bulk copying, string decoding and output escaping
are more prominent. These short profiles have small sample populations, so
symbol percentages describe hotspots rather than precise speedup estimates.
The 8 MiB workload's tracked peak heap rises from 42.40 MB to 42.85 MB; the
allocation profiles do not establish a memory improvement for that workload.

### Verification and evidence scope

The final source passes 861 Rust tests (nine existing opt-in ignores), two
diagnostic-process tests, formatting and Clippy with all targets/features and
warnings denied. The Python harness runs 986 tests across 35 modules: 981 pass,
five skip, none fail. [Verification identities](evidence/cli-perf2/verification.json)
retain the logs' hashes.

A deterministic corpus of 33,272 finite numeric lexemes in 1,040 rows, plus four
overflow/partial-output cases, matches the actual pinned Go executable. This is
additional to random-float and restricted-decimal differential unit tests; see
[numeric replay evidence](evidence/cli-perf2/number-verification.json).

All 23 retained Go replay groups pass with zero requirement skips, including all
12 real 60/120-second deadlines. The coverage includes parser/help/completion,
HTTP, SQL, metrics, administration, Flight, decoder proofs, root help and selected
dark/light interactive SQL/saved-query sessions. Across the different case/check
units, 4,181 strict checks pass; 255 Flight cases retain the pre-existing native
tonic User-Agent difference, and eight terminal observations remain documentary
rather than strict passes. Payload/authentication checks pass for all 291 Flight
cases. No comparator or manifest was relaxed. The
[Go verification summary](evidence/cli-perf2/go-verification.json) records every
gate's count, unit and report hash.

This is evidence of no new incompatibilities in the retained corpus, not a proof
of every possible Go behavior. Non-SQL/saved interactive workflows, device login
and interactive-effects replay are outside this run; historical deferred-stub
guards and six future-terminal phase-two cases retain their original exclusions.
Rust ingestion/observation extensions retain the existing discovery projections;
same-Rust unprojected output checks accompany the performance captures.

Final resource runs follow the prospective [plan](evidence/cli-perf2/plan.json).
The original 19 cases retain their own aggregate; seven supplements cover gzip,
completion, nested SQL and larger Flight batches. Two execution seeds use 20
repetitions and three warmups, with the same bootstrap method as campaign one.
Ingestion separately checks ordinary and escape-heavy corpora with strict wire,
checkpoint, resume, completion, 503 replay and 413 splitting probes. Its built-in
resource gate requires a 10% point estimate and an interval below parity; an
interval wholly below 0.90 is a stronger statement and is reported separately.

Reproduce with frozen binaries, the existing fixture/meter, and no concurrent
owned builds, tests or profiler jobs:

```sh
flock -n ~/.cache/pb-rs-performance.lock taskset -c 2,4,6,8 \
  bash docs/evidence/cli-perf2/measure.sh harness/results/perf2-reproduction
python3 docs/evidence/cli-perf2/analyze.py \
  harness/results/perf2-reproduction/final-seed2031/report.json \
  /tmp/perf2-incremental.json --reference base --case-set fixed
```

Repeat analysis for seed 2032, reference `original`, and `--case-set supplement`.
The script deliberately reuses the unchanged harness and requires new output
directories. Shared-host activity, warm caches, loopback servers and sub-ms
command noise still limit generalization to other machines or production.

To reproduce the placement control, make private byte-identical copies on an
executable tmpfs mount, verify their hashes against the control evidence, then
use those paths with the same fixture and meter:

```sh
flock -n ~/.cache/pb-rs-performance.lock taskset -c 2,4,6,8 \
  python3 harness/run.py run \
  --suite harness/results/perf-campaign/suite.json \
  --binary base=/dev/shm/pb-perf2-rss-controls-2037/base \
  --binary original=/dev/shm/pb-perf2-rss-controls-2037/original \
  --binary final=/dev/shm/pb-perf2-rss-controls-2037/final \
  --fixture bin/pb-fixture --meter bin/pb-meter \
  --warmup 3 --repetitions 20 --seed 2034 --timeout 60 \
  --out harness/results/perf2-memory-reproduction
python3 docs/evidence/cli-perf2/analyze.py \
  harness/results/perf2-memory-reproduction/report.json \
  /tmp/perf2-memory-total.json --reference original --case-set fixed
```

## Stress and sustained-load characterization

The new stress harness exercises the frozen Go and Rust builds from the earlier
comparison. No product sources changed during this campaign. Commands and profile
boundaries are in [the harness guide](../harness/README.md#streaming-stress-large-datasets-and-sustained-ingestion);
[interactive results](evidence/cli-stress/results.html),
[reduced measurements](evidence/cli-stress/results.json) and
[validation results](evidence/cli-stress/tests.json) retain the evidence.

Coverage includes 1M/10M tiny JSON records, 1M escaped records, an 8 MiB JSON item,
roughly 1/10 GiB raw text, fragmented UTF-8/gzip, malformed and truncated prefixes,
HTTP 503, slow/paused consumers, broken pipes, cancellation and the real 60-second
SQL deadline. Deadline validation compares error/exit/request contracts, the first
4096 output bytes and complete-object prefix framing; it does not claim identical
lengths or full content for timing-dependent partial output. Repeated SQL queries
ran for at least 60 seconds per workload: 40 Go/Rust pairs for tiny records and
8 paced pairs. They start fresh processes and do not establish single-process
leak behavior.

Scale figures below are medians of three measured invocations after one warmup;
10 GiB uses one measured invocation. Resource runs use `/dev/null` only after
separate whole-output Go/Rust digest comparisons. The independent raw text drain
must also match output plus its trailing newline. Native write accounting covers
at least the validated output byte count in all recorded measured null-sink invocations;
this additional post-capture check is retained in the reduced evidence and is now
a future-run admission gate. It is a lower bound on all writes, not an stdout
hash. CPU affinity was 2,4,6,8, with separate fixture and collector processes.
Other work shared the host, so these are descriptive local measurements.

| Workload | Go CPU | Rust CPU | Go peak RSS | Rust peak RSS |
| --- | ---: | ---: | ---: | ---: |
| 1M tiny JSON rows | 2,728 ms | 577 ms | 59.0 MiB | 8.3 MiB |
| 10M tiny JSON rows | 25,118 ms | 5,700 ms | 59.3 MiB | 8.3 MiB |
| 1M escaped JSON rows | 6,342 ms | 3,063 ms | 59.0 MiB | 8.5 MiB |
| One 8 MiB JSON item | 183 ms | 33 ms | 133.5 MiB | 33.6 MiB |
| ~1 GiB raw text | 494 ms | 944 ms | 49.0 MiB | 9.0 MiB |
| ~10 GiB raw text | 4,586 ms | 9,236 ms | 48.5 MiB | 8.9 MiB |

The raw-text results are excluded from throughput admission because they approach
the fixture/Python-drain calibration ceiling. Their native CPU measurements still
expose a useful difference: Rust spends more CPU on raw text despite using much
less memory. In the 1 GiB runs, Rust has roughly 261k voluntary context switches
versus Go's 34k; most of the CPU gap is kernel time. The Rust transport sends at
most 8 KiB chunks through a capacity-one channel to a renderer thread. A handoff
per chunk is a plausible explanation, supported by source and counters but not
yet confirmed by a syscall trace. `/proc` read/write call counters do not count
all socket operations; they must not be interpreted as complete syscall totals.
Per-thread scheduling traces and a faster/pre-generated producer are useful next
profiles. JSON rendering remains substantially cheaper in Rust in these tests.

The Rust-only ingestion harness verifies contiguous records, content, exact
outbox replay after 503 and acknowledgement loss, actual 413 splitting, same-state
lock rejection, durable offsets, restart/completion, idempotent completion and
cleanup. It uses append-only real-disk source/state files. It does not cover
rotation, copytruncate, many-file discovery, different-state concurrency or every
possible crash point. There is no Go ingestion equivalent to compare.

The five-minute run generated and accepted all 1,500,000 records, at an average
4,999.99 records/sec. Every behavior, checkpoint and cleanup check passed. **The
run failed steady-input admission:** the writer's maximum schedule lag was
405.5 ms, above its 250 ms tolerance. This result is retained; it is not a Rust
compatibility failure or evidence of a maximum ingestion rate. Observed backlog
peaked at 13,754 records and drained before final completion. Histogram p50/p95/p99
upper bounds were 50/200/2,000 ms; latency includes the writer, append, polling,
upload and validating sink on this shared host.

The follower used 11.10 CPU seconds over 305 seconds including drain time, about
3.64% of one core, and peaked at 9.69 MiB RSS. First/last 30-second live-window
medians after startup were 8.82/9.21 MiB RSS, 10/10 file descriptors and 1/1
threads. This bounded observation does not prove absence of a leak.

For 96 MB of source records, the CLI accumulated 2.56 GB of charged write bytes
and 865 MB of write-family payload accounting. These counters are not physical
storage writes or SSD wear. Pending rows include repeated event metadata, and
each batch saves pending state before upload and saves its acknowledgement after
upload; both saves sync a temporary file, rename it and sync the directory.
`--batch-size 1000` is a ceiling, not a fill target. Actual batch sizes, checkpoint
serialization and sync latency are the next ingestion efficiency profiles, with
the same durability and replay guarantees retained.

A separate 60-second repeat at the same offered rate passed all checks, including
the producer schedule bound, and accepted all 300,000 records. It supplements the
five-minute observation; it does not replace that run or remove its pacing miss.


## Third campaign: streaming and durable ingestion

This campaign compares the frozen second-campaign Rust binary with
`bin/pb-perf3-final`, and separately compares the final binary with Go.
[Final candidate identities](evidence/cli-perf3/final-candidate.json) record the
release, profiling and cloud-fixture binaries and 93 source hashes. The
[accepted patch](evidence/cli-perf3/accepted.patch) isolates this campaign from
preexisting uncommitted changes. More than twenty Opus 5.5 delegated rounds plus Sol 6.1
implementation and evidence work are recorded in
[the review ledger](evidence/cli-perf3/agents.json). The runtime allowed four
active agents including the coordinator, so these were focused waves, not
20 simultaneous agents.

### Changes and rejected experiments

Successful plain SQL text responses now cross the capacity-one async/render
bridge in chunks of at most 128 KiB, up from 8 KiB. Ready partial frames are
forwarded immediately; this does not wait to fill a chunk. Error previews and
JSON retain their earlier chunk sizing. Backpressure and deadline cancellation
remain in place. The bound is on logical chunks, not all resident transport
memory: slices can retain larger backing allocations. Gzip output stays capped
at 8 KiB per read. `BytesMut` replaces the explicitly zero-filled vector, but
the active miniz decoder still initializes output internally; this is not a
zero-initialization-free path or an independently established CPU gain.

Large decoded JSON rows now render through a reusable 1 MiB buffer. Every row
is fully decoded and float-validated before its separator or content reaches
stdout, including overwritten duplicate fields with invalid values. Small rows
(up to 512 source bytes) retain the direct vector path, with a compile-time
expansion bound. Large rows can involve multiple writes; a failed write drops
the remainder of that row and the following row is still attempted. Scanner
storage and the decoded tree still scale with the largest source item; this
change does not establish constant total memory for arbitrary items.

Checkpoint serialization uses a 64 KiB write buffer instead of 8 KiB. Pending
and acknowledged saves, flush, file sync, rename and directory sync retain
their original ordering. Deferring acknowledged saves was rejected because it
would widen the replay window after interruption. Lower syscall counts do not
imply fewer encoded bytes, fewer syncs, lower physical storage writes or less
SSD wear.

An initial bounded-renderer variant increased small-row CPU cost; the final
version restores the direct small-row path and fast contiguous appends. A bulk
JSON string-scanning experiment passed semantic review but showed no reliable
benefit in direct on/off comparisons and was removed. A prepared-body text
fixture separates bridge overhead from per-row response generation; it is a
controlled HTTP/1.0 Content-Length diagnostic, distinct from the generated
HTTP/1.1 chunked fixture. Its Go validation checks full output bytes before
Rust-vs-Rust null-sink resource measurement.

The [decision ledger](evidence/cli-perf3/decisions.json) preserves accepted,
rejected and deferred options. No compatibility or durability concession was
accepted to obtain a performance number.

### Validation and measurement limits

The frozen candidate passed 867 default Rust tests, 873 with diagnostics, 24
release-mode SQL output tests, formatting and all-target/all-feature clippy.
The Python harness passed 107 tests after correcting a test-only assumption
that the fixture's `python3` launcher must be the parent's Python executable.
The launcher is resolved from PATH; the initial failed attempt and exact fix
are retained in [tests.json](evidence/cli-perf3/tests.json).

The initial full Go replay passed 21 of 23 groups, including all 688 ordinary
SQL cases. Two long-deadline cases failed because the Go reference itself
produced different timeout strings across repeated executions. A single
bounded repeat passed both cases; separate prior-Rust/final-Rust diagnostics
also passed both, with no skips. The original failures remain visible. This
supports no detected new behavior regression in these cases; it is not an
unqualified 23/23 first-attempt pass or a universal compatibility guarantee.
No macOS or Windows run was performed in this campaign.

Every headline resource comparison uses five randomized paired rounds plus a
warmup, the same CPU affinity, serialized owned work, and native child CPU and
lifetime peak RSS. Reported changes are medians of paired ratios, which can
differ from ratios of the two displayed medians. Ranges describe observed
samples on this shared host; they are not population confidence intervals.
Null-sink timing runs follow separate full-output pipe hash comparisons.
Producer-limited runs do not establish maximum throughput.

The [first complete measurement](evidence/cli-perf3/round4-results/results.json)
found about 1 MiB higher RSS on small/streaming workloads and failed the ingest
resource gate, despite matching output and durable state. This result is not
discarded. [Mapping snapshots](evidence/cli-perf3/rss-attribution.json) located
most of that difference in executable-file pages. A
[byte-identical copy control](evidence/cli-perf3/rss-identical-copy.json) then
showed about 1.3 MiB lower RSS for a copy of the very same executable in all
three pairs, also dominated by executable-file residency. The realized order happened to be the same in all three pairs. A later
[six-pair balanced-order control](evidence/cli-perf3/rss-identical-copy-balanced.json)
did **not** reproduce that gap: both copies were around 8.7–9.0 MB RSS.
These observations establish sensitivity to residency state, not that copying
caused the difference or that an optimization removed heap usage. The original
small SQL gap cannot be attributed solely to source changes. These interrupted snapshots
do not measure lifetime peak, prove absence of a leak, or identify the kernel's
underlying page-cache mechanism.

The final controlled comparison uses fresh copies of Go and both Rust binaries,
all materialized with the same 64 KiB writes, flush, fsync, permissions and full
SHA-256 read. Their bytes are identical to the already validated executables;
the [artifact manifest](evidence/cli-perf3/controlled-artifacts.json) records the
identities and method. Equal copying policy does not prove equal residency.
Native RSS is reported unchanged, without subtracting file pages or replacing
it with tracked heap. Both the original and controlled campaigns are retained.

### Profiling and rejected inlining

The [104-session profile campaign](evidence/cli-perf3/profile_plan.json) combines
Go-checked controls, user-space cycle samples and Rust heap tracking. For
100,000 small JSON rows, both Rust versions had about 1.46 MB peak tracked heap
and 1.226 million allocations. For a single 8 MiB row, tracked heap fell from
38.34–38.65 MB to 22.35–26.55 MB. These are instrumented allocator observations,
not native RSS or release performance measurements.

A separate [escape-heavy profile](evidence/cli-perf3/profiles-stress-escaping.json)
put 8.84–8.95% of sampled self overhead in the bounded buffer's append helper.
Each capture had only 239–264 samples, so this identifies a candidate hotspot,
not a complete explanation for repeated large-row CPU cost. The fixture implies
roughly 4.3 million prefix/escape appends; that count is derived, not measured.
Text profiles had too few samples for confident symbol-level claims. Native
user/system CPU and the syscall traces are stronger evidence for fewer text
writes and scheduler handoffs; traced timings are not used as speedup evidence.

An isolated four-attribute `inline(always)` experiment passed the full-output
checks but was rejected. Across five pairs per case, every CPU ratio range
crossed 1; repeated large rows improved only 3.3% at the median while gzip rows
regressed 3.1%. Its apparent small-row memory gain was also subject to the
identical-copy residency confounder. The
[on/off results](evidence/cli-perf3/inline-on-off-results/results.json) remain
available. No inline attributes from that experiment were adopted.

### Final measured comparisons

The ≥10% target is met for text-stream CPU and large-row peak memory,
with material tradeoffs elsewhere. These are workload-specific results, not
a claim that every CLI operation is 10% cheaper. Negative changes mean lower
resource use. The [interactive dashboard](evidence/cli-perf3/dashboard.html),
[full tables](evidence/cli-perf3/results.html) and
[machine-readable evidence](evidence/cli-perf3/results.json) retain paired ranges,
absolute medians, binary identities, admission checks and source report hashes.

| Prior Rust → selected Rust | Original CPU Δ | Controlled CPU Δ | Original RSS Δ | Controlled RSS Δ |
|---|---:|---:|---:|---:|
| One million escaped rows | +0.4% | -2.3% | +11.5% | +1.4% |
| Single 8 MiB row | +0.6% | -0.6% | -21.4% | -23.3% |
| 1 GiB raw text | -55.7% | -57.1% | +12.5% | -0.2% |
| One million tiny rows | -3.5% | -0.7% | +12.6% | +0.4% |
| Eight 8 MiB rows | +10.2% | +5.8% | -21.2% | -23.3% |
| Eight 8 MiB rows, gzip | -1.8% | +9.1% | -22.1% | -23.6% |
| Prepared 1 GiB text body | -77.4% | -71.3% | +16.1% | +13.9% |
| JSONL ingestion | -7.4% | -7.2% | +8.1% | +6.8% |
| Text ingestion | -11.1% | -11.8% | +7.6% | +7.1% |

The text CPU and large-row RSS gains occur in both campaigns. Small CPU
differences whose paired ranges cross parity are not established improvements.
Repeated large-row rendering remains a CPU/memory tradeoff: the original
plain 8×8 MiB CPU increase was +10.2% with all five ratios above parity; the
controlled increase was +5.8%, with a range spanning parity. Gzip CPU was noisy
across campaigns. Cached-text RSS and ingestion RSS increases persist in the
controlled run and remain unresolved; the small SQL mapping control does not
attribute those other workloads.

| Go → selected Rust, controlled artifacts | CPU Δ | Peak RSS Δ |
|---|---:|---:|
| Single 8 MiB row | -84.5% | -81.0% |
| Eight 8 MiB rows | -80.6% | -84.4% |
| Eight 8 MiB rows, gzip | -81.0% | -85.1% |
| One million escaped rows | -49.7% | -85.0% |
| One million tiny rows | -80.3% | -85.3% |
| 10 GiB raw text | -7.3% | -81.3% |

The 10 GiB text comparison used less CPU in all five pairs in both campaigns:
−11.2% paired median originally and −7.3% with controlled artifacts. Native
peak RSS was about 81% lower than Go in the controlled run. These null-sink
runs do not establish an end-to-end throughput advantage.

Both real-btrfs ingestion comparisons pass exact normalized wire/state parity
and all 503 replay and 413 split probes. Write-call accounting falls about 87%;
text ingestion uses about 12% less CPU in the controlled paired comparison.
**The overall ingestion resource gate fails in both campaigns** because its
RSS regression guard is exceeded. The controlled guard reports approximately
7–9% higher RSS using its ratio-of-medians statistic. Buffered encoding keeps
the same durable saves and syncs, and charged storage bytes remain broadly flat.

The original 60-second sustained run passed producer pacing and every behavior
check at 5,000 records/sec. The controlled repeat also generated and accepted
all 300,000 records and passed ordering, content, checkpoints, restart/completion,
and cleanup checks, but its producer briefly lagged 503 ms, exceeding the 250 ms
schedule tolerance. **That repeat fails steady-input admission.** No retry was
run to replace it. Separate 503, lost-acknowledgement and 413 fault runs passed
in both campaigns. Lost acknowledgements can intentionally replay duplicates;
this is not an exactly-once claim or proof of maximum sustainable throughput.

The controlled sustained run also had materially worse observed backlog and
latency: maximum backlog 27,252 versus 352 records, mean latency 571 versus
30 ms, and maximum latency 5.74 seconds versus 77 ms. Its p95/p99 histogram
upper bounds were 5/10 seconds, versus 100/100 ms originally. It drained in
359 ms after generation stopped. The CLI used 2.13 CPU seconds over its
65-second follow phase and peaked at 10.70 MiB RSS. The binaries have identical
bytes; these runs do not isolate the source of the timing difference, and the
producer admission failure prevents a steady-throughput conclusion. The
[process evidence](evidence/cli-perf3/controlled-process-summary.json) preserves
both observations rather than selecting the better one.

Reproduction starts with the pinned artifacts and a fresh output prefix:

```sh
flock ~/.cache/pb-rs-performance.lock taskset -c 2,4,6,8 \
  python3 docs/evidence/cli-perf3/run_selected.py \
  --candidate bin/pb-perf3-controlled-candidate \
  --baseline bin/pb-perf3-controlled-baseline \
  --go bin/pb-perf3-controlled-go \
  --manifest docs/evidence/cli-perf3/final-candidate.json --prefix fresh-run
```

The driver retains nonzero resource/pacing gates and returns failure after
collecting the remaining diagnostic evidence. It refuses to overwrite prior
runs. Use the report commands and hashes for the exact fixture configurations.
Further priorities are a narrower append fast path, attribution of cached-text
and ingestion RSS, and longer sustained runs on a quieter host. No deferred
optimization is represented as implemented.
