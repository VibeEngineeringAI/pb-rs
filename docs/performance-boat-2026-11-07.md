# Boat performance investigation — 2026-11-07

This experiment compares the Rust CLI at commit `14ea05aad9a798a096a27c50961042ef4c85af91` with the optimized Rust CLI in this worktree. The baseline is not the Go implementation. The targets are at least 50% lower CPU time and 50% lower peak resident memory for the same workflow. Results are workload-specific; they do not establish a blanket reduction for every command.

## Changes

- Decode the interactive PromQL response through borrowed JSON spans, materializing one series tree at a time. Validate the complete response before decoding concrete fields, preserving syntax-error precedence, Go field folding, duplicate fields, hidden slice slots, and first type errors.
- Share matrix results between the table and chart. Share the row `Vec` itself, avoiding the second full row-buffer allocation required by conversion to `Arc<[Row]>`.
- Share metric labels within each series and timestamp strings across repeated grids. The timestamp cache is bounded to 4,096 entries. Move decoded string values into rows and release consumed sample buffers as each series is formatted.
- Reserve decoded array lengths explicitly. The common two-element sample no longer allocates a four-element buffer. Reservation does not initialize slots or change duplicate-array reuse.
- Prepare chart series once per result and share that immutable cache with state snapshots. Invalidate it on every successful or failed result completion. Continue rebuilding geometry for the current dimensions, selected time range, and cursor.
- Borrow the unfiltered table's row slice directly for navigation and rendering. Filtering retains the existing Unicode and selection behavior.

The C signal probe now explicitly consumes the return value of `write`, allowing its existing strict build flags to work with Ubuntu's fortified headers. One equivalent SQL byte-array spelling satisfies the newer Rust 1.97 Clippy lint. Neither change modifies command behavior.

## Method

Two fresh Boat VMs were provisioned without account secrets or snapshots: `bx_v9rgr4yz` (8 vCPU, 16 GB) and `bx_g358j3k9` (4 vCPU, 8 GB). Both use Linux 6.8 and x86-64. Build both release binaries with Rust 1.97.1, the same checked-in Cargo.lock, and the existing release profile: thin LTO, one codegen unit, stripped symbols, default features. Transfer the same frozen release binaries to the confirmation VM; do not rebuild there during measurement.

`harness/perf_vm.py` reuses the existing fixtures, PTY interpreter, native child supervisor, strict functional validator, and styled-frame comparator. It records binary SHA-256 values, working-tree source hashes, host information, exact cases, every raw capture, and resource counters. The recorded working-tree hashes describe the collector checkout; the older baseline's source is separately retained in `baseline-source.tar.gz` and its commit record.

The existing Phase 7 collector requires Linux 6.9 pidfd group signaling. This separate experiment uses a scoped Linux 6.8 adapter: the native supervisor holds the CLI PID with WNOWAIT until release; the adapter checks its pidfd and original process identity before signaling that reserved process group, and refuses signals after release. The existing actual two-member group-delivery and zombie-reservation probe runs on this adapter. The Phase 7 admission rules and collector implementation are unchanged.

CPU time is the CLI's native `wait4` user plus system time. Peak RSS is its native `wait4.ru_maxrss`, converted from Linux KiB to bytes. The small native supervisor prevents the Python observer's inherited high-water RSS from becoming the CLI baseline. These are reported process counters, not whole-VM memory or instantaneous CPU utilization. Fixture and observer CPU are excluded. Absolute wall time includes scripted readiness holds and idle time and is reported separately.

The principal workload loads 1,000 series with 256 samples each, shows the chart and table, switches back to the chart, idles, and quits at 120×40. Each theme is separate. The confirmation VM runs seven measured randomized baseline/candidate pairs after one excluded warmup pair per theme; the first VM runs five measured pairs after one warmup pair. No builds or profilers run concurrently with those measurements on the same VM.

Reductions use the median of within-pair candidate/baseline ratios. A deterministic 2,000-draw paired bootstrap describes the 95% interval; the halving target is marked confirmed only for uninstrumented performance cases with at least five measured pairs and an interval upper bound at most 0.5. This bounded experiment is not the historical Phase 7 performance-admission campaign or a replacement for its outstanding gates.

## Profiling

Separate heaptrack runs record **37,616,413 → 6,522,351 allocation calls** (82.7% fewer) and **148.91 MB → 44.85 MB peak tracked heap** (69.9% lower). These are decimal profiler units. Heaptrack overhead is excluded from the primary CPU/RSS comparisons.

Separate optimized, symbolized builds support `perf record` sampling. Baseline stacks identify allocation, repeated chart-data extraction, hashing, and timestamp parsing; after the changes, chart drawing remains a prominent cost. Samples are diagnostic and are not substitutes for the paired resource measurements. A mistakenly reused preliminary symbolized artifact had the baseline's hash and was excluded; the final optimized symbolized artifact was rebuilt after source timestamp refresh and its distinct hash was checked.

## Reproduction

Create a fresh VM, copy this repository, and install `pyte==0.8.2` and `wcwidth==0.2.13` in a Python virtual environment. Freeze the baseline binary before editing or building the candidate. Use distinct output directories and preserve both artifacts unchanged.

```sh
python harness/perf_vm.py \
  --baseline /absolute/path/pb-baseline \
  --candidate /absolute/path/pb-optimized \
  --out /absolute/path/new-results \
  --repetitions 7 --warmups 1 \
  --case 'promql-dense-1000-120x40-*'
```

Repeat with `--theme light` and a new output directory. `--corpus --case 'p7-promql-*' --repetitions 1 --warmups 0` checks the existing functional workflows. `--profiler heaptrack` or `--profiler perf` collects separate diagnostic runs; perf requires noninteractive sudo on these VMs. The heaptrack path is the Ubuntu package's `/usr/lib/heaptrack/libheaptrack_preload.so`.

Raw experiment files and build/test logs are retained under `harness/results/boat-20261107/` (gitignored). The compact tracked record is [boat-20261107-performance.json](evidence/boat-20261107-performance.json).


## Final results

For the principal 256,000-sample workflow, both targets are confirmed on both VMs. The three independently reported theme/host populations remain separate:

| VM / theme | Measured pairs | CPU, before → after | Paired CPU reduction | RSS, before → after | Paired RSS reduction |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4 vCPU / dark | 7 | 1.379 s → 0.533 s | 62.8% | 186.1 MiB → 68.8 MiB | 63.0% |
| 4 vCPU / light | 7 | 1.329 s → 0.479 s | 63.3% | 186.1 MiB → 68.7 MiB | 63.1% |
| 8 vCPU / dark | 5 | 2.052 s → 0.647 s | 70.2% | 186.2 MiB → 68.5 MiB | 63.2% |

Every principal CPU ratio's bootstrap upper bound is below 0.409, and every RSS ratio's upper bound is below 0.371. Relative reductions use paired ratios, so they need not equal a ratio computed from the displayed marginal medians. The 8-vCPU host is an AMD Ryzen 9 9950X; the independent 4-vCPU host is an AMD Ryzen 7 9700X.

Other 8-vCPU workloads, each five measured pairs plus one warmup:

| Workload | Paired CPU reduction | Paired RSS reduction |
| --- | ---: | ---: |
| SQL, 20,000 rows | 0.6% | 0.0% |
| PromQL, 1 series | 14.6% | 0.0% |
| PromQL, 100 series | 51.3% | 48.2% |
| SQL editor | -3.1% | 0.0% |

Negative reductions mean an increase. SQL/editor CPU intervals include no change; their resident footprint is essentially unchanged. The 100-series case is close to, but does not confirm, the 50% memory target. The small-result cases retain a roughly 9–11 MiB fixed footprint.

A separate exploratory sustained-navigation pair passed all 70 styled checkpoints per role. CPU was 2,878.821 → 2,052.238 ms (28.7% lower), and RSS was 195,256,320 → 72,286,208 bytes (63.0% lower). One pair is descriptive and does not confirm either target statistically; sustained navigation remains below the CPU goal. The next useful work is reducing table-cell style/render allocation and the remaining chart drawing, timestamp conversion, and per-point hashing costs. This investigation therefore achieves the two targets for dense result load/switch workflows, not across every CLI workflow.

## Validation and retained evidence

- Final `cargo test --locked --all-targets`: **700 passed**, nine explicitly ignored opt-in/benchmark tests, zero failures.
- Final `cargo clippy --locked --all-targets -- -D warnings` and `cargo fmt --all --check`: pass.
- Existing benchmark/profiler harness tests plus the new reservation adapter tests: **158 passed** using the pinned Python environment. An initial run in the system Python lacked pyte; the successful reruns use the existing pinned virtual environment.
- Final functional PromQL corpus: **21 cases / 42 native roles**, zero comparison issues, including refresh, stale completion, duplicate fields, filters, mode transitions, Unicode, resizing, signals, and chart edge cases.
- Final performance matrix: **39 measured pairs plus seven excluded warmup pairs / 92 native roles**, zero functional comparison issues. Both principal targets pass with the frozen binary hashes on each host.

The baseline release SHA-256 is `49ec1c6b03c4f8fcf55b391c4ee9efb91288ac79044419ae94e4734a58ffa262`; optimized release is `c0166a201147b17c568deca91814338195edbe9faf8119f38b0b4df4e1991a20`. Complete product source hashes match the final collector checkouts on both VMs. The optimized symbolized SHA-256 is `7912db22dbfb75766d5b757068c19e190c67f29dca2fca201f75241a114b2ec3`, distinct from baseline symbolized `ae8000e69824ebf147e3e3817545826f13ffa4dec5fd5eb99d0059844ec217c6`.

Raw captures, exact definitions, native helper manifests, fixture traces, original compressed heaptrack streams, interpreted profiles, perf data, symbolized binaries, preliminary experiments, and build/test logs are retained in the two archives under `harness/results/boat-20261107/`. Their SHA-256 values are in the compact tracked record. The original uncompressed heaptrack streams are omitted from the archives because their byte-preserving `.original.gz` copies are included.

Both evidence archives were downloaded and independently SHA-256 verified before VM deletion was requested. Both created VMs were removed after collection; no pre-existing sandbox was changed.

## Additional hardware and distribution validation

The [fresh-VM portability validation](portability-boat-2026-11-07.md) repeats the measurements on 2-vCPU / 4-GB and 4-vCPU / 8-GB Boat machines, tests older emulated CPU models, and supplies a static artifact for older Linux distributions. It also records the original GNU artifact's glibc 2.39 requirement, one timing-sensitive parallel test failure, and the static runtime's CPU tradeoff.
