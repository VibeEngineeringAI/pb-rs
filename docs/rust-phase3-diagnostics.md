# Phase 3 performance attribution

The harness now separates release performance from diagnostic evidence. Its
purpose is to explain the two JSON workloads where Go led Rust, while preserving
all command, output, request and configuration contracts. It does not change
normal CLI algorithms.

## Reproduce

Build the usual `bin/pb-go`, `target/release/pb`, `bin/pb-fixture` and
`bin/pb-meter` first using `harness/Makefile`. Keep the same version and Cloud
build configuration for both releases and both diagnostic binaries.

```sh
make -f harness/Makefile diagnostics-build
python3 harness/diagnose.py --out harness/results/sql-attribution \
  --repetitions 5 --diagnostic-repetitions 3 --warmup 1
```

Run builds and tests before measurement, with no concurrent agent workload.
The runner interleaves variants in seeded order within each workload and round.
Each sample gets a fresh process, local fixture and disposable profile; OS caches
are warm. The default set covers one row, 100k rows in JSON and text, a single
8 MiB item in JSON and text, and 1 MiB of leading whitespace.

The Go overlay is pinned to the exact original source hashes, records its
replacement source and toolchain, and refuses drift. It builds an optimized
binary with the same baseline flags. Rust uses the same release profile with
`--features diagnostics --target-dir target/diagnostics`. Instrumentation is
compiled out of normal Rust builds. Preserve the binaries and a source manifest
with `harness/manifest.py`; current source does not prove how an old binary was
built.

## Measurements and controls

- **Release, pipe and file:** whole-invocation latency, user/system CPU, peak
  RSS, faults, context switches, block I/O operations, output bytes and fixture
  request/body metrics. On Linux, `/proc/PID/io` is read after `waitid(WNOWAIT)`
  reports exit and before `wait4` reaps the child. It provides read/write-family
  call counts, character counts and storage byte counters. The character/call
  counters exclude recv/send paths: runtimes use different socket APIs, so
  cross-language network-read conclusions from these fields are invalid.
  These counters cover multiple file descriptors, not stdout alone. Diagnostic totals include sidecar
  and profile I/O, so use release controls for syscall comparisons. Unsupported
  or inaccessible counters are null with a reason. Pipe first-output/first-element timings are observer
  timestamps. File output is a cached regular-file experiment, not a null sink
  or a durable-storage benchmark; first-output timestamps are unavailable.
  The output cap bounds retained captures and triggers process termination;
  file growth can exceed the threshold between polling intervals.
- **Diagnostic binary, disabled:** isolates the cost of compiling/linking hooks
  and altered code layout. Compare this with its own language's release.
- **Timing and sampled timing:** Rust reports per-thread exclusive stage wall
  and CPU time; Go reports inclusive stage wall time and command process CPU.
  Sampled totals cover actual selected elements only, with no extrapolation.
  Go renderer overlaps every stage except request_headers; body_read partially
  overlaps decode and decoder_control. Do not sum those totals. Rust thread
  spans overlap too. Rust closing framing inherits the final loop sampling
  selection; only initial framing is always timed.
- **Allocation mode:** Rust counts requested allocator calls/bytes on registered
  threads, including realloc requests; these are not heap size or bytes copied.
  Go records command MemStats deltas and a sampled cumulative allocation
  profile. These scopes differ and are not an apples-to-apples memory ranking.
- **Go CPU profile:** native pprof samples exclude runtime/package initialization
  before main. Its shutdown/flush can dominate short invocations. Whole-process
  timings include that cost; sidecars record command and flush time separately.
  Aggregate repeated profiles before interpreting small hotspot percentages.
  Profile decoding runs after the entire measurement matrix, with Go network
  downloads disabled.

Every variant must match the reference output, stderr, request sequence,
termination and before/after config. Sidecars must be complete and internally
consistent. Missing profiling data is a failure, never zero. Raw diagnostic
metadata lives in `report.json`; individual sidecars and pprof files remain
under each sample directory. No keys, query text or result values enter the
sidecars. The usual captures contain synthetic fixture data only.

The runner reports diagnostic-versus-disabled overhead separately from
release-versus-disabled overhead. Stage attribution is suggestive when overhead
is material relative to the release gap. Sampled clocks reduce overhead but do
not remove all counter, allocator-wrapper or code-layout effects. Rust thread-CPU clocks
require system calls on this host, increasing diagnostic system CPU. Use
normal builds for CPU comparisons.

Linux I/O fields follow the [kernel proc documentation](https://www.kernel.org/doc/Documentation/filesystems/proc.txt),
section 3.3; they do not provide comprehensive syscall tracing.

Go profile interpretation follows [runtime/pprof](https://pkg.go.dev/runtime/pprof)
and [runtime.MemStats](https://pkg.go.dev/runtime#MemStats). Rust allocator
counts follow [GlobalAlloc](https://doc.rust-lang.org/std/alloc/trait.GlobalAlloc.html):
observed optimized allocator calls, not a guarantee that a source operation
allocated. Profiles and allocation totals are distinct from native process RSS.

## Results: 2026-10-13

All 252 invocation samples matched output, stderr, requests, termination and
configuration, and passed sidecar validation: 201 measured samples plus 51
excluded warmups. Six synthetic workloads, five release repetitions per sink,
three repetitions per diagnostic variant and one warmup per variant. The shared
Linux i9-13900HK host used warm caches and its existing powersave governor;
no agent builds/tests overlapped measurement. These small-sample estimates are
advisory diagnosis, not new performance gates or live-service latency claims.

| Workload, pipe output | Go elapsed ms | Rust elapsed ms | Go CPU ms | Rust CPU ms | Go / Rust RSS MiB |
|---|---:|---:|---:|---:|---:|
| One JSON row | 25.43 | 1.68 | 34.81 | 1.41 | 54.96 / 6.06 |
| 100k JSON rows | 587.46 | 618.16 | 681.68 | 610.94 | 59.90 / 6.26 |
| Single 8 MiB JSON item | 139.12 | 177.96 | 158.73 | 172.30 | 124.88 / 55.55 |
| Same 100k rows, text | 46.22 | 23.53 | 44.04 | 15.90 | 54.88 / 6.80 |
| Same 8 MiB item, text | 42.11 | 21.82 | 41.61 | 12.18 | 54.78 / 6.58 |

Normal file-output controls preserved the JSON gap: 100k rows took Go 561.75 ms
and Rust 594.78 ms; the 8 MiB item took Go 135.04 ms and Rust 172.51 ms. Removing
pipe-reader backpressure did not remove the regressions. Text uses the same
response bodies and is faster in Rust, which narrows the issue to the JSON path.

### Scanner and reader path: strongest current lead

For the large item, Rust full-timing medians were 123.13 ms wall / 122.96 ms
thread CPU in scan/framing, 7.61 ms ordered decoding, 2.84 ms conversion,
21.03 ms serialization, 4.05 ms indentation copying and 8.25 ms output writes.
Body-reader wait was 1.47 ms wall / 1.16 ms CPU, including channel bookkeeping.
These are diagnostic stage observations, not separately measured release costs.

The isolated scanner test removes HTTP, decoding, conversion, serialization and
stdout. It scans and validates the same 100k synthetic elements (9.8 MB), using
five alternating-order repetitions with whole-loop clocks. Median consumer CPU
was **45.53 ms through an 8 KiB buffered cursor versus 165.97 ms through the
actual shared-Bytes BodyReader**, about **3.65×**. Median wall was 46.11 versus
166.44 ms. The bounded producer contributes to wall time but not consumer CPU.

This localizes a substantial cost to the reader/scanner combination. The
per-byte `Bytes::slice` in `BodyReader::consume`, reference-count traffic and
reader abstraction overhead are the leading code-level hypothesis. The test
does not separately prove which instruction or ownership operation dominates.
No reader optimization has been implemented.

### Writes and allocation churn

Normal 100k JSON runs recorded median `syscw` 300,006 in Go and 401,532 in Rust.
These read/write-family counters include non-stdout descriptors and exclude
send/recv APIs. Rust's sidecar shows 300,002 logical writes and 400,004 explicit
flush calls: logical writes are not kernel writes. Together with the normal
build counters and `SignalWriter`/line-buffer behavior, this supports investigating
extra flushes. It does not assign the full elapsed gap to stdout syscalls.

The socket-accounting asymmetry was directly visible: Rust `rchar` was about
8 KiB despite consuming the complete 12.5 MB response, while Go's covered the
response. This is why these fields cannot compare network-read efficiency.

For the 8 MiB item, Rust requested about 32 MiB during framing, 8 MiB decoding,
8 MiB conversion, **80 MiB serialization**, and **24 MiB indentation copying**.
These include realloc requests and are not bytes copied or peak memory. They
identify buffer/copy investigation targets despite Rust's lower process RSS.
The Go CPU/allocation profiles are retained with native call stacks; merged CPU
samples show JSON scanning/indentation/copying for the large item, and syscall,
JSON and GC work for 100k rows. Profiles from three measured runs are combined;
the large-item CPU profile contains only 360 ms of sampled CPU and is coarse.

### Instrumentation overhead limits

At 100k rows, Rust disabled diagnostics took 619.21 ms versus release 618.16 ms;
full timing took 1,215.42 ms and sampled timing 657.15 ms. Full timing adds about
96% over disabled, sampled timing about 6%. Even sampled overhead exceeds the
30.70 ms release gap, so these stage totals cannot precisely apportion that gap.
Go disabled was 599.30 ms, full timing 661.79 ms and sampled timing 607.42 ms.

One-element timings are less intrusive, but the shared host and three samples
still produce variation, including negative measured overhead. That is not an
instrumentation speedup. Use release controls for elapsed/CPU/RSS comparisons,
and combine diagnostic stages with the independent scanner and sink experiments.

### Artifacts and validation

- [Validated report](../harness/results/phase3-diagnostics-verified/report.md)
  and [full JSON](../harness/results/phase3-diagnostics-verified/report.json).
- Original measurements, captures, workloads and build/source provenance:
  `harness/results/phase3-diagnostics-final/`.
- Scanner measurements: `phase3-diagnostics-final/scanner-isolation.txt`.
- Merged Go profiles and extracted medians:
  `harness/results/phase3-diagnostics-analysis/`.
- 161 Python suite tests passed; the final diagnostic-only suite has 14 passing
  tests after adding reanalysis integrity checks. All 144 normal Rust tests,
  two diagnostic unit tests, two diagnostic subprocess tests, the isolated
  scanner test, formatting and feature-enabled Clippy passed.

The first external profile-decoding pass failed because Go's toolchain
dispatcher rejected its checksum configuration. No CLI invocation was rerun; only the external profile-decoding failure
was cleared. All 24 native profiles were subsequently
decoded using the installed Go 1.25.4 executable with toolchain/module downloads
disabled. The original failed analysis remains intact; the validated report
links it through a source-report hash. Original profile hashes were first
recorded during analysis, not collection; a subsequent pass verified those
hashes. Future runs now hash profiles immediately after collection and reject
changed profiles during analysis.

### Next optimization experiments

1. Replace per-byte shared-buffer slicing with chunk ownership plus a cursor;
   test exact output, bounded memory, cancellation, truncation and streaming.
2. Investigate repeated serialization/escaping/indentation allocations while
   preserving Go's numbers, key ordering, escaping and malformed-input behavior.
3. Test bounded output buffering with explicit first-element and cancellation
   checks; measure syscall reductions separately from elapsed-time improvements.

These are proposed follow-ups. This change adds measurement tools and evidence;
it does not optimize the production CLI or alter the phase-three feature scope.


## Isolated scanner and profile recovery commands

```sh
PB_VERSION=v0.0.0-harness PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN= \
  cargo test --locked --release --features diagnostics --target-dir target/diagnostics \
  --lib diagnostic_scan_microbenchmark -- --ignored --nocapture --test-threads=1

# Retry only external profile decoding into a new directory; preserve original data.
python3 harness/diagnose.py --reanalyze harness/results/sql-attribution/report.json \
  --out harness/results/sql-attribution-verified --go-tool /path/to/installed/go
```

Build the scanner test before concurrent measurements, then run its emitted test
executable directly to avoid compilation in the measurement window. Profile
reanalysis clears only the explicit decoder failure, preserves all contract and
sidecar failures, verifies existing profile hashes, and confines artifacts to the
new output directory. With a normal run, `--provenance` accepts a manifest whose
binary labels are `go`, `rust`, `go-diagnostics`, and `rust-diagnostics`.
