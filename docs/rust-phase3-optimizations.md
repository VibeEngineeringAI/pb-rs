# Phase 3: measured SQL optimizations

This pass preserves the existing Go CLI contracts while removing costs identified
by perf, strace and heaptrack. It does not implement deferred commands or change
the backend. Performance results below cover synthetic client workloads; they
do not establish live Parseable service latency or a universal Rust speed claim.

## Implementation

- **Chunk cursor:** `BodyReader` retains one `Bytes` chunk and advances an index.
  It changes ownership only when refilling, eliminating clone/drop per scanned
  byte. The bounded channel, deferred read errors and early completion remain.
- **Reusable row storage:** framing bytes, nesting frames, serializer output and
  formatted row storage reuse their capacities. The memory bound now includes
  the largest capacities encountered in the stream, not only the current row.
- **Moved values:** conversion takes strings and keys from a mutable ordered
  document instead of cloning. The document continues owning unconverted nodes,
  and partial converted documents remain guarded for iterative error cleanup.
- **One formatting pass:** serde's pretty output goes into reusable storage;
  HTML/separator escaping and array indentation share one pass. Numeric/duplicate
  key policies and stack protection retain their existing behavior.
- **One write per complete row:** the row buffer contains its preceding separator
  and indentation. The opening `[` is still published immediately. No rows are
  buffered together, and no next-row separator is emitted before decoding that
  row succeeds. Unix streaming stdout uses a borrowed fd 1 syscall, with existing
  EINTR/EAGAIN handling and SIGPIPE policy; it neither owns nor changes the fd.
  Other platforms retain the previous standard stdout path.
- **Cheaper cleanup:** scalar values need no scratch vector. Arrays reuse their
  own storage as the iterative drop worklist. Deep children never fall through
  to unguarded recursive destruction.

The diagnostic counters retain comparable logical input/output byte definitions.
Escaping now belongs to the fused indentation stage rather than serialization,
so stage durations are not interchangeable across the two implementations.
Uninstrumented release builds remain the source of latency comparisons.

## Compatibility and review

Sol 6.1 implemented/reviewed the reader, renderer and process regressions. Opus
5.5 reviewed the contract risks and final production delta. Review caught a
missing cursor initializer in the diagnostics-only microbenchmark; it was fixed
and tested. A stronger subprocess regression was added for cleanup near the
10,000-level nesting limit, both before and after a converted deep sibling.

Completed checks:

- Offline gate: 2,018/2,018 cases and 50/50 deferred-command guards.
- Profiles/auth/API reads: 219/219 cases and 6/6 boundaries.
- SQL gate: 675/675 cases and 11/11 boundaries, no skips.
- Four actual 60-second regressions against Go: JSON body stall, text empty-array
  lookahead, per-chunk deadline persistence and an incomplete error preview.
- Fifteen process tests against both Go and Rust, including read-only stdout,
  merged stdout/stderr ordering, gated partial output, nonblocking pipes,
  SIGPIPE/signals, `/dev/full`, deep cleanup and terminal spinner behavior.
- Rust normal/diagnostic tests, diagnostic-feature Clippy, formatting, and 49
  focused harness tests passed. The scanner microbenchmark stays opt-in.

Existing phase-three known divergences and deferred capabilities remain listed
in [the phase-three implementation report](rust-phase3.md). This pass does not
turn them into verified parity.

## Measurement method

Before edits, the original release, symbolized and diagnostic Rust binaries and
source delta were saved. Same-run measurements compare pinned Go 1.25.4,
pre-optimization Rust and optimized Rust 1.96.0. Every measured invocation checks
complete output, termination, requests and config effects against the Go contract.

The focused two-case pipe experiment uses 30 measured repetitions and five
excluded warmups per binary. The 53-case broad sweep and focused file-output
experiment use 15 measured repetitions and three excluded warmups per binary.
Order is seeded and shuffled within each round. All timed commands use fresh
processes/profiles and warm OS caches; no agent builds or tests run concurrently.
CPU affinity is unchanged, so hybrid-core scheduling and normal host noise remain.
Bootstrap intervals are descriptive uncertainty estimates, not a universal SLO.

The file mode is now exposed as `harness/run.py run --stdout-sink file`. It keeps
full output validation but reports first-output/element timing unavailable.
Comparisons refuse to apply a performance gate across different sink settings.
The pipe's incremental first-element observer can add backpressure; the file
experiment helps distinguish client work from that cost. Neither file output nor
profiler runs should be conflated with ordinary pipe latency.

Capture results, binary hashes, source provenance and raw output are under
`harness/results/phase3-optimization/` (ignored local artifacts). Reproduce the
saved suites with:

```sh
python3 harness/run.py run \
  --suite harness/results/phase3-optimization/focus-suite.json \
  --binary go=bin/pb-go \
  --binary before=bin/pb-rust-before-optimization \
  --binary rust=target/release/pb --fixture bin/pb-fixture \
  --out harness/results/optimization-focus-repeat \
  --repetitions 30 --warmup 5 --timeout 30 --seed 20261013
```

## Focused pipe results

The completed, compatible 30-repetition pipe experiment measured:

| Workload | Go median / p95 ms | Previous Rust median / p95 ms | Optimized Rust median / p95 ms | Rust / Go, bootstrap 95% interval |
|---|---:|---:|---:|---:|
| 100,000 JSON rows | 613.47 / 669.95 | 629.84 / 692.03 | 284.13 / 360.39 | 0.463× (0.445–0.474) |
| One 8 MiB JSON item | 153.55 / 162.63 | 189.06 / 207.91 | 79.41 / 92.42 | 0.517× (0.504–0.553) |

| Workload | Go CPU ms / peak RSS MiB | Previous Rust CPU ms / peak RSS MiB | Optimized Rust CPU ms / peak RSS MiB |
|---|---:|---:|---:|
| 100,000 JSON rows | 708.43 / 60.91 | 628.85 / 6.70 | 279.56 / 6.01 |
| One 8 MiB JSON item | 170.17 / 120.97 | 181.73 / 53.82 | 70.96 / 37.54 |

CPU and RSS cells are medians of native per-child measurements. RSS is the
process high-water mark, not heaptrack's tracked heap. Focused and broad-sweep
payloads differ; compare binaries within each workload, not between tables.

## Broad pipe sweep

All 53 workloads passed compatibility. Optimized Rust had a lower wall-time
median than Go in every case, and every Rust/Go bootstrap interval was below
1.0. This is a result for this fixture matrix and host, not proof that Rust is
always faster. The smallest advantage was the slow-reader text workload:
0.944× Go elapsed time (95% interval 0.939–0.946).

Paced and slow-reader cases are largely unchanged relative to previous Rust;
waiting dominates them. Two tiny non-SQL comparisons were slower than previous
Rust in this sweep: SQL JSON help 1.16 → 1.45 ms (Go 28.57 ms), and seeded profile
listing 1.28 → 1.46 ms (Go 29.30 ms). Their intervals excluded parity in this run,
but median deltas were under 0.3 ms (delta interval upper bounds at most 0.42 ms)
on a shared hybrid-core host. These are 53 exploratory comparisons without a
multiplicity correction; isolated small differences on unchanged paths need a
controlled repeat before attributing them to the code change. These results
do not demonstrate an improvement to those unchanged paths and are not hidden
in an aggregate average. The full per-case results are in
`measure-broad/report.md` and `summary.json`.

The first file experiment failed before producing a sample because the `/tmp`
tmpfs user quota prevented the supervisor from writing its metrics. Its failed
output directory contained no sample artifacts; its console log and a note of
the reproduced `metrics close: Disk quota exceeded` error are now preserved in
`measure-file-failed-tmpfs`. The file rerun uses
`TMPDIR=$PWD/target/tmp`, a disk-backed filesystem; it is a separate cached-file
experiment, with no fsync/durability claim. The harness now treats unreadable or
invalid JSON meter output as an invalid sample rather than an uncaught exception.
The failure-path fix changes the runner hash; the successful pipe captures retain
their original hash, and all comparisons here are within each completed run.

## Cached-file results

This separate 15-repetition experiment also passed all Go contracts:

| Workload | Go median ms | Previous Rust median ms | Optimized Rust median ms | Rust / Go, bootstrap 95% interval |
|---|---:|---:|---:|---:|
| 100,000 JSON rows | 810.43 | 883.81 | 344.58 | 0.425× (0.416–0.452) |
| One 8 MiB JSON item | 137.16 | 182.76 | 73.72 | 0.537× (0.485–0.550) |

Across the three completed experiments there were 2,655 valid measured CLI
invocations and 525 excluded warmups. No incompatibility was found. The file
experiment shows that the advantage persists with a different stdout sink;
it is not a direct performance comparison against the pipe experiment.

## Profiler confirmation

All 66 controls/profiler captures passed output contracts and binary-identity
checks. Three repetitions per case used optimized symbolized binaries with
`perf cycles:u`, strace, and Rust heaptrack. Instrumented elapsed times are not
used as speed results. Raw profiles, analyses and hash identities are under
`external-profiles/`; derived figures are in its `findings.json`.

| Cost | Previous Rust | Optimized Rust | Go |
|---|---:|---:|---:|
| FD 1 writes, 100,000 JSON rows | 400,002 | 100,002 | 300,003 |
| FD 1 writes, one 8 MiB item | 6 | 3 | 6 |
| Allocation calls, 100,000 rows | ~6.306 million | ~2.346 million | Different allocator; not measured by heaptrack |
| Temporary allocations, 100,000 rows | ~1.905 million | ~0.245 million | — |
| Tracked peak heap, one 8 MiB item | 67.56–68.13 MB | 59.17 MB | — |

All three write traces per binary/case agree, with exactly 17,044,453 stdout
bytes for the rows case and 8,388,657 for the large item. This is syscall count,
not a change in the user-visible bytes. Allocation calls fell about 63%; the
100,000-row tracked peak heap remained about 1.45 MB. Heaptrack's printed MB are
decimal and exclude other process memory; they must not be substituted for the
native peak RSS above. Baseline profiler figures come from the saved,
hash-identified `phase3-external-profiles-01` captures.

The old `Bytes` shared clone/drop routines accounted for 25.21–26.34% of
sampled user cycles in the row case and 59.18–61.58% for the large item. Fresh
unfiltered self-symbol reports show 0.054–0.179% for rows and no attributed
samples for the large item. That means the hotspot disappeared from these
samples, not that reference counting has literally zero cost. Percentages are
weighted by estimated cycles across the hybrid-core perf events; they are not
wall-time percentages. No lost samples were reported. Caller stacks remain
incomplete, so these conclusions rely on self symbols and direct write/
allocation measurements rather than reconstructed call-tree totals.

Remaining visible CPU work includes bytewise framing, `BodyReader::fill_buf`,
string decoding and numeric formatting. Bulk scanning or a carefully tested
numeric fast path are possible later investigations; neither is implemented in
this pass. Changes would still need the same malformed-input, deep-cleanup,
partial-output and deadline checks.

## Review closure

Sol 6.1 implemented and checked the changes and the capture-failure regression.
Opus 5.5 approved the production delta, verified closure of the added Go-oracle
process regressions, and independently checked the completed pipe numbers and
meter failure handling. Its statistical and artifact-provenance caveats are
included above. Sol also independently verified the final profiler numbers
against the raw analyses. Production code stayed fixed throughout measurement; the later change was
confined to failure handling in the harness.

No live Parseable service timing was measured in this pass. No implementation
for deferred CLI commands was added. The earlier known phase-three divergences
remain documented; passing this matrix is not a claim of complete CLI parity.
