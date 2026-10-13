# Rust port: phase one

The Rust executable is a development candidate for the offline CLI surface.
The Go executable remains the complete CLI. Build and invoke the Rust candidate
by its explicit path; it is not installed over `pb` and does not delegate work to
Go.

## Scope

Phase one implements command parsing, help, version output, machine-readable
command discovery, the agent catalog, and compatible invocation errors. The
command specification describes the reachable Go command tree, including
commands whose execution will be implemented in later phases. Those operations
must fail explicitly rather than report success.

HTTP requests, authentication, configuration mutations, SQL/PromQL execution,
live tail and interactive screens remain subsequent work. The catalog describes
the compatibility target; its entries are not evidence that every operation
already executes in the Rust candidate.

This acceptance gate covers noninteractive, plain output. Source and PTY
characterization confirmed that Go's root help adds color/bold styling and
terminal background/cursor probes when a controlling terminal is present;
the visible help text is unchanged. Terminal styling/probing and the hidden
shell-completion script/protocol operations are recorded follow-ups, alongside
the interactive terminal work. The development candidate is not a drop-in
replacement for an installed Go CLI or its completion scripts yet.
The hidden `__complete` and `__completeNoDesc` protocol entry points currently
return an unknown-command error; completion script operations return the
explicit deferred-operation error.

Missing-profile checks cover an isolated, nonempty HOME/XDG environment.
Reading existing, unreadable or malformed configurations, unset/empty HOME,
and Windows configuration-directory conventions remain outside this phase.

The first parser implementation accepts UTF-8 argument strings. Unix byte
arguments that are not valid UTF-8 are rejected explicitly rather than panicking.
Go can accept those bytes, so byte-valued argv parity remains an unresolved
compatibility item, not an approved change to the eventual replacement. Supporting
it generally requires preserving OS strings through parsing and byte-valued
diagnostics through output; lossy decoding is not a compatibility solution.

The design follows the Mayfly Rust-port overview and its cross-review decisions:
one Cargo package, a small executable entry point, an owned command
specification, explicit flag-presence tracking, typed results, and separate
compatibility rendering. Parser/library defaults do not define the public
interface. The pinned Go behavior does.

The initial clap adapter passed the first corpus, but independent review found
that its normalization lost the original failing token and validation order.
Examples included `--top=x --top`, repeated unknown short flags, and a collision
between a user flag and an internal diagnostic marker. Matching these behaviors
required a second tokenizer alongside clap. Following the wiki's fallback rule,
the implementation now uses one sequential parser driven by the owned command
specification. It retains typed values and explicit presence without synthetic
flags or duplicated validation.

Diagnostic quoting uses Unicode 15 printable-range data derived from Go 1.25.4,
with attribution and the Go BSD license in [src/LICENSE-Go.txt](../src/LICENSE-Go.txt).
That notice must accompany redistribution of those materials, including future
binary packaging. The parser and application organization remain native Rust;
the range table is declarative compatibility data.

## Build and compare

Use Rust 1.96.0 (including `rustfmt` and `clippy`), Go 1.25.4 (the `go.mod`
toolchain pin), Python 3.11+, and a C compiler. The phase-one CI pins that exact
Go version: a newer toolchain can change diagnostic Unicode escaping even at
the same source commit. Run from the repository root:

```sh
GOTOOLCHAIN=go1.25.4 make -f harness/Makefile build rust-build
make -f harness/Makefile rust-test test

# Each comparison directory must be new.
make -f harness/Makefile phase1-check OUT=harness/results/phase1
make -f harness/Makefile phase1-measure OUT=harness/results/phase1 REPETITIONS=30

target/release/pb version
target/release/pb help sql run -o json
```

`CARGO=/path/to/cargo` and `GO=/path/to/go` can select existing toolchains.
`rust-build` uses `PB_VERSION` to match the harness Go version. A direct
`cargo build --release` produces development version metadata; match versions
explicitly when comparing version output.

The compatibility command writes exact process transcripts, a report, and a
`benchmark-suite.json` containing the smaller offline measurement subset.
The measurement command places results in its `performance/` subdirectory.
Keep these artifacts with the executable hashes when evaluating subsequent
changes. The [phase-one workflow](../.github/workflows/rust-phase1.yaml) runs
the same checks on Linux and retains its reports.

## Compatibility rules

- Preserve command-local flags, aliases, accepted argument counts, defaults,
  validation order, and the distinction between absent and explicit flags.
- Preserve stdout and stderr independently, including text errors, JSON error
  selection from raw arguments, and exit status.
- Preserve deterministic help and discovery bytes. A source declaration alone
  does not make a command reachable from the root.
- Keep unfinished operations outside the passing performance comparison.
- Record differences explicitly; do not normalize away whitespace, JSON
  ordering, diagnostic text, or numeric representations to manufacture parity.

## Measurement

Use the [comparison harness](../harness/README.md) with prebuilt, optimized Go
and Rust executables. Match their public version metadata. Measure both in the
same run with fresh isolated process environments and randomized execution
order. Compatibility must pass before a speed ratio is meaningful.

The first measurements cover process startup, first output, total elapsed time,
user/system CPU, peak resident memory, and binary size for the implemented
offline operations. They are warm-cache, fresh-process measurements. They say
nothing yet about SQL latency, streaming throughput, interactive responsiveness,
or the size of the eventual complete Rust CLI.

Run a short compatibility sweep while iterating. Capture a longer performance
comparison only after the relevant behavior passes and the binaries are stable.
Shared-runner CI should enforce correctness and retain performance artifacts;
strict performance thresholds belong on a controlled host.
The five-sample CI run is a smoke measurement, not evidence of a stable
performance change. Publish longer-run results with the host conditions and
absolute timings, and compare the Rust candidate against earlier Rust builds
as the remaining dependencies and operations are added.

## Verified local baseline — 2026-10-12

The final optimized Rust candidate passed 2,018 exact process compatibility
cases against the pinned Go 1.25.4 reference, plus 82 separately counted
deferred-operation guards. The corpus includes 45 bounded argument chunks
covering every Unicode scalar from U+00A0 upward. All 22 Rust tests, 40 Python
harness tests, Go fixture tests, formatting and strict Clippy checks passed.
Final Astra and Opus 5.5 review findings were fixed and independently verified.
The GitHub workflow is configured; these results are from local execution.

Local artifacts (ignored by Git):

- `harness/results/phase1-go-pinned/`: frozen Go transcripts.
- `harness/results/phase1-acceptance-pinned/`: Rust comparison, guards and suite.
- `harness/results/phase1-acceptance-pinned/performance/`: report, JSON,
  sample CSV and process transcripts.

These supersede the earlier captures made with Go 1.27.1. Artifact identity:

| Binary | Toolchain | SHA-256 |
|---|---|---|
| Go reference | Go 1.25.4 | `9ae884772ab92c52d9e226961c11db082da608dc59b378731f0689c7d8ca9e75` |
| Rust candidate | Rust 1.96.0 | `fdb6387a6c8f15b89d61c5d08b57e9228eebb075dc8204b0fa83dc50375833fb` |

The 13 fixed offline benchmarks each passed 30 measured samples per binary
after five warmups: 780 valid measured samples total. Execution order was
randomized with seed 2026; the report includes p95 values and descriptive
bootstrap intervals. Selected medians:

| Measurement | Go reference | Rust phase one |
|---|---:|---:|
| `pb version` elapsed | 31.213 ms | 0.958 ms |
| `pb version` first stdout | 29.198 ms | 0.879 ms |
| `pb version` CPU | 40.481 ms | 0.879 ms |
| `pb version` peak RSS | 46.664 MiB | 2.566 MiB |
| `pb --help` elapsed | 31.137 ms | 0.999 ms |
| Root JSON command tree elapsed | 32.255 ms | 1.485 ms |
| Executable size | 71,807,160 bytes | 761,328 bytes |

This was a shared Linux x86-64 host with an i9-13900HK, 20 logical CPUs,
the powersave governor, and starting load averages of 17.42/20.35/15.11.
It was not an idle or controlled benchmark host. These measurements establish
an exploratory baseline for the implemented offline surface, comparing the
complete Go CLI with the small Rust candidate. They do not predict the final
port's footprint, query latency or streaming performance. Re-measure as each
operational phase adds dependencies and functionality.

The exact measurement invocation was:

```sh
python3 harness/run.py run \
  --suite harness/results/phase1-acceptance-pinned/benchmark-suite.json \
  --binary go=bin/pb-go --binary rust=target/release/pb \
  --out harness/results/phase1-acceptance-pinned/performance \
  --warmup 5 --repetitions 30
```

Use a new output directory when repeating it.
