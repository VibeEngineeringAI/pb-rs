# Phase 3: external CPU, syscall and allocation profiling

The optimized symbolized Go and Rust builds and capture runner are prepared.
Installed tools are perf 7.2.3-1, strace 7.0 and heaptrack 1.5.0. User-cycle
sampling works without sudo or changing kernel permissions. The 11-invocation
external smoke run passed, followed by 66 passing primary invocations and
an 11-invocation final smoke test. Eight focused runner tests passed.

## Build and run

Arch package installation (completed; includes `heaptrack_print`):

```sh
sudo pacman -S --needed perf strace heaptrack
```

Keep baseline releases, symbolized builds and manually instrumented diagnostic
builds separate. Run from the repository root with the pinned Go 1.25.4 toolchain:

```sh
GOTOOLCHAIN=go1.25.4 CARGO_BUILD_JOBS=2 make -f harness/Makefile profiling-build
python3 harness/profile_external.py \
  --out harness/results/external-profiles-01 --repetitions 3
```

The normal `bin/pb-go`, `target/release/pb`, fixture and native meter must already
exist; build them with the existing harness targets if needed. Output directories
must be new. Stop other builds and benchmark jobs before capturing.

Rust's custom `profiling` profile inherits release optimization, thin LTO and
single codegen unit, adds full DWARF (`debug = 2`), and disables stripping. It
**does not enable the `diagnostics` feature**. Its binary is
`target/symbolized/profiling/pb`. Go retains normal optimization and omits only
`-s -w`; its binary is `bin/pb-go-symbolized`.

The runner verifies `.debug_info`, `.debug_line` and `.symtab` in both binaries.
Debug information supports source attribution but optimized functions may inline
or disappear. DWARF unwinding avoids changing release code generation to force
frame pointers. Stack completeness and unresolved symbols must still be inspected.

## Experiments and interpretation

Default cases are 100,000 JSON rows and one 8 MiB JSON item, the two workloads
previously slower in Rust. Each mode gets three independent CLI processes per
applicable language. Controls compare stripped releases and symbolized builds;
order alternates each repetition. There is no warmup exclusion: these are short
exploratory captures, not a statistically conclusive release ranking.

- **perf**: samples user CPU cycles at 999 Hz, with 16 KiB DWARF stack snapshots.
  Source/inline stacks can locate scanning, reference counting, JSON formatting
  and allocator work. Sampling does not measure time spent waiting on a socket.
  Short runs may contain too few samples for stable percentages; inspect sample
  counts, lost events and repeatability before drawing conclusions.
- **strace summary**: follows threads and counts syscalls. Its syscall time is
  kernel time measured under tracing, not ordinary end-to-end or network latency.
- **strace writes**: follows `write`/`writev` with raw arguments, allowing FD 1
  (stdout) to be distinguished from sockets and wake-up descriptors without
  recording payload buffers. Match unfinished/resumed calls before totaling
  return values; do not count resumed records as new calls. `/proc` write counts
  alone could not distinguish these descriptors in the previous investigation.
- **heaptrack**: directly preloads the installed allocation library into the Rust
  CLI, saves raw records, then interprets/compresses them after all timed captures.
  This bypasses the 1.5.0 launcher, whose stdout chatter and lack of a quiet flag
  conflict with exact CLI output checking. Profiler environment variables are
  scoped to that one invocation. It profiles the Rust native allocator and reports allocation call
  stacks, temporary allocations and peak tracked heap. This is neither RSS nor
  bytes actually copied by a realloc. Go's managed allocator is not meaningfully
  measured by malloc interception; use the existing Go native allocation pprof
  captures for that implementation.

Every invocation uses synthetic local HTTP and an isolated profile. Captures
validate complete JSON row count/order, stdout/stderr hashes, termination,
requests and config state against the unprofiled Go reference. Profiler warnings
that alter CLI output cause a contract failure. Missing tools, missing/empty
artifacts, decoder failures and binary changes fail the report. Profile files
are hashed before and after decoding. The report also records binary/tool
identities, source snapshot, workload definitions and actual commands.

External profiler wall/CPU/RSS/I/O measurements include profiling overhead
and, for perf/strace, wrapper processes. The direct heaptrack capture has no
concurrent interpreter/compressor; its raw file writes and allocation hooks still
perturb the CLI. **Do not rank Go and Rust using those numbers.**
Use the separate unprofiled control samples for CLI CPU, RSS, I/O, startup/request
and completion latency. `/proc` I/O syscall counters have read/recv and write/send
coverage differences across the implementations; they are not comparable network
byte totals. Local fixtures isolate client work but do not represent live service
or TLS latency. No credentials or production data are required.

Decoding runs after all captures. `perf report --stdio --no-children` and
`heaptrack_print` output are retained beside raw profiles. The runner now uses
`-m 0` to avoid misleading merged per-location heap peaks. The primary capture
retains its original merged reports plus supplementary unmerged reports for
repetition 0 of each workload; the final smoke validates the updated runner. Self and inclusive
percentages must not be mixed when interpreting stacks.

## Focused commands

```sh
# Controls only; works before installing external tools.
python3 harness/profile_external.py --mode control \
  --out harness/results/symbolized-controls-01 --repetitions 3

# A smaller smoke run, then a single attribution mode.
python3 harness/profile_external.py --case p3-1-json --repetitions 1 \
  --out harness/results/external-smoke-01
python3 harness/profile_external.py --mode perf --repetitions 5 \
  --out harness/results/perf-01
```

The default event is `cycles:u`. If hardware PMU access fails, retain the failed
report and explicitly select `--perf-event cpu-clock:u` in a fresh output
directory. That uses a different sampling basis and must be labeled accordingly.
No system-wide perf permissions are changed automatically; software events can
also be denied by host policy.

Installed perf/strace and the direct heaptrack preload path were checked to
preserve a child exit code of 7. The heaptrack check also preserved distinct
stdout/stderr bytes. Before trusting new profiler versions, check that their launchers preserve an
intentional child nonzero exit and that quiet modes leave stdout/stderr intact.
The runner's wrapper quoting, contract rejection, missing/ambiguous profile
handling and analysis failures have focused tests:

```sh
python3 -m unittest discover -s harness -p test_profile_external.py -v
```

References: [Cargo build profiles](https://doc.rust-lang.org/cargo/reference/profiles.html),
[perf record](https://github.com/torvalds/linux/blob/master/tools/perf/Documentation/perf-record.txt),
[heaptrack](https://github.com/KDE/heaptrack), [strace](https://strace.io/).

## Initial controls before installation (2026-10-13)

`harness/results/phase3-symbolized-controls/report.json` contains 24 passing
invocations: two workloads × four builds × three repetitions. All complete
contracts matched the Go release. Five focused profiler-runner tests and Cargo
format checks passed. The missing-tool preflight correctly rejected an attempted
external capture at that time. The installed tools subsequently passed the
11-invocation smoke test and all seven updated runner tests.

The three-run medians (milliseconds) were:

| Workload | Go release | Go symbols | Rust release | Rust symbols |
| --- | ---: | ---: | ---: | ---: |
| 100,000 JSON rows | 577.70 | 613.88 | 682.23 | 609.32 |
| One 8 MiB JSON item | 144.93 | 142.90 | 186.48 | 180.57 |

The many-row variation is too large to treat these three-run controls as an
optimization result or evidence that debug symbols change speed. No production
code optimization was made. The large-item Rust gap remains visible. The completed external investigation
below strengthens the earlier scanner/allocation hypotheses.

## Completed external investigation

This section describes the preserved pre-optimization binaries and source snapshot.
Source locations are historical. See [the optimization follow-up](rust-phase3-optimizations.md)
for subsequent implementation and same-run Go comparisons.

Primary artifacts: `harness/results/phase3-external-profiles-01/report.json`;
`findings.json` binds derived findings to reports/profile identities;
`write-analysis.json` contains descriptor-specific syscall counts. The final
runner smoke is `harness/results/phase3-external-smoke-final/report.json`.
All 66 primary invocations passed their complete contracts. All 12 perf and six
heaptrack captures decoded successfully. All 12 raw write traces were complete,
and their successful FD 1 byte totals matched captured stdout exactly.

### CPU: per-byte ownership work is a major Rust cost

`bytes::bytes_mut::shared_v_clone` and `shared_v_drop` together account for
**59.18–61.58%** of estimated sampled user cycles on the 8 MiB item, across three
runs; on 100,000 rows they account for **25.21–26.34%**. These are self percentages,
weighted by each event's estimated cycles across the hybrid CPU's `cpu_atom` and
`cpu_core` events. They are approximate sampled cycle shares, not wall-time
fractions or promised speedups. All perf reports show zero lost samples.

The source explains why this work scales with input bytes:
`src/sql/scan.rs:307` consumes one byte per scanner iteration, and
`src/sql/mod.rs:59` implements `BodyReader::consume` by replacing the current
`Bytes` with a new slice. The clone symbol's annotated instructions include a
locked reference-count increment; most samples in that function land immediately
after it. Instruction sampling can skid, so this is not an exact per-instruction
cost or evidence of cross-thread contention. Many optimized caller stacks are
incomplete even with DWARF; symbol-level self attribution and source inspection
are stronger here than a reconstructed full call graph.

**First optimization experiment:** keep a cursor into the current chunk, avoiding
clone/drop per byte; keep ownership changes at chunk boundaries. This is proposed
work only. Preserve bounded streaming, error behavior and response-consumption
contracts, then rerun unprofiled performance and compatibility checks.

### Syscalls: extra stdout writes are confirmed

| Workload | Go stdout writes | Rust stdout writes | Identical stdout bytes |
| --- | ---: | ---: | ---: |
| 100,000 JSON rows | 300,003 | 400,002 | 17,044,453 |
| One 8 MiB JSON item | 6 | 6 | 8,388,657 |

These counts repeated exactly in all three captures. Rust's other writes were
1,529–1,530 for the many-row case and 1,058–1,063 for the large item; these are
separate from stdout. Descriptor-specific tracing resolves the ambiguity in the
previous `/proc` counts. There were no stdout write errors.

The many-row path writes separators, indentation and serialized content through
a line-buffered stdout and flushes each element (`src/sql/output.rs:243`).
**Second experiment:** reduce these writes while preserving when partial output
becomes visible, including errors and broken pipes. The large-item case has the
same stdout write count in both languages, so its slowdown is not explained by
extra stdout calls.

### Allocations: frequent short-lived work versus large-item buffers

For 100,000 rows, heaptrack recorded **6,306,094 allocation calls** in each run,
about **1.905 million temporary allocations**, and a **1.45 MB tracked heap peak**
(decimal units printed by heaptrack). High allocation churn coexists with a small
live heap. Unmerged call stacks include:

- 500,000 calls through the iterative `drop_value` scratch vector
  (`src/gojson.rs:265`).
- 500,000 through cloned object keys during conversion
  (`src/sql/output.rs:310`).
- 500,000 through decoded string growth (`src/gojson.rs:408`).
- 400,000 reallocations growing framed element buffers
  (`src/sql/scan.rs:306`).

These are individual observed allocation stacks, not disjoint stage totals.
For the 8 MiB item, the tracked global heap peak was **67.56–68.13 MB**. Unmerged
stacks show 16.78 MB capacities for framing and a serializer buffer, plus several
8.39 MB string/copy buffers. This supports a later experiment in buffer reuse and
fewer representation copies. Heap peaks are allocator-requested live storage,
not resident physical memory; profiler-injected library initialization is also
included. Heaptrack's temporary-allocation definition is not a measured object
lifetime distribution. Go allocation comparisons still use native pprof.

### Unprofiled controls and limits

The new three-run release medians were 631.79 ms Go / 644.40 ms Rust for 100,000
rows and 139.05 ms Go / 176.95 ms Rust for the 8 MiB item. Symbolized medians were
606.42 / 601.48 ms and 122.71 / 172.10 ms respectively. The many-row ordering varies
with build/run noise; do not claim a stable speed difference from three runs.
The large-item gap remains clear in these controls. Profiling identifies likely
causes, but only an optimized A/B experiment can quantify removable latency.

This investigation changed profiling tools and documentation only. No production
optimization, live-server benchmark, commit or push was performed.
