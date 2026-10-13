# CLI compatibility and performance harness

Compare **prebuilt executables**, independently of their implementation language.
The current Go CLI is the reference; add the Rust binary as it becomes available.
The runner uses Python 3.11+ standard library, a small POSIX C measurement
supervisor, and a Go HTTP/Arrow Flight fixture using this repository's existing
dependencies. No Python packages or Parseable account are required.

Linux and macOS are supported by the runner; Linux is verified in this change.
Windows measurement is not implemented. This harness is not a production client
or a complete replacement for integration tests against Parseable.

## Build once, measure separately

Requirements: Python 3.11+, a POSIX C compiler (`cc`), and a working Go toolchain
compatible with `go.mod`. From the repository root:

```sh
make -f harness/Makefile build
make -f harness/Makefile test

# Verify the measurement/compatibility setup against the identical binary.
make -f harness/Makefile self-check REPETITIONS=3

# Capture an initial Go baseline before changing the CLI.
python3 harness/run.py run \
  --binary go=bin/pb-go --fixture bin/pb-fixture \
  --out harness/results/go-baseline --warmup 3 --repetitions 30
```

`GO=/path/to/go`, `CC=clang`, and `PYTHON=/path/to/python3` may be supplied to make.
If `/tmp` has insufficient space, point `GOTMPDIR` at an existing build directory
on a filesystem with room. The harness never installs toolchains or changes
machine configuration. Only `build`/`tools`/`baseline` build binaries; `run` does not.

The baseline target uses optimized release-like Go flags and version
`0.0.0-harness`. Preserve a copy of the binary and its report before rebuilding;
`baseline` builds the current checkout, not an automatically pinned Git revision.
Build the Rust candidate in release mode with the same public version string.
Version-output mismatches are intentionally not normalized away.

## Compare Go and Rust in the same session

```sh
python3 harness/run.py run \
  --binary go=bin/pb-go --binary rust=/absolute/path/to/rust/pb \
  --fixture bin/pb-fixture \
  --out harness/results/go-vs-rust --warmup 5 --repetitions 30
```

The first binary is the reference. Executables run **sequentially**, with a seeded
random order inside each repetition, to reduce ordering bias without making the
two implementations compete for CPU. Fixtures, home/config and process state
are fresh for every sample, including warmups. Outputs are retained for every
sample. Result directories must be new; previous results are never overwritten.

Start with the implemented portion of the Rust port:

```sh
python3 harness/run.py list
python3 harness/run.py run --binary go=bin/pb-go --binary rust=/path/to/pb \
  --group startup --out harness/results/startup-comparison

# First data, throughput and slow-consumer behavior:
python3 harness/run.py run --binary go=bin/pb-go --binary rust=/path/to/pb \
  --fixture bin/pb-fixture --group flight --read-delay-ms 10 \
  --out harness/results/slow-consumer
```

`--case` and `--group` are repeatable. `--read-delay-ms` delays reading stdout
after each read (up to 64 KiB), leaving stderr independently drained. It is an
explicit backpressure workload, not a normal latency run. Do not compare it to
a report with a different delay. A ten-second per-sample timeout and 32 MiB output
limit bound accidental hangs/floods; `--timeout` can increase the time budget.

## What is measured

| Measurement | Definition |
|---|---|
| `wall_ms` | Native supervisor's monotonic time immediately before fork through reaping the CLI. Includes process creation, loader, runtime, CLI and fixture-response waits. Excludes supervisor/fixture startup and artifact writes. |
| First stdout/stderr | Parent-observed first bytes at each pipe, relative to the CLI launch marker. Not an internal application timestamp. |
| First record | First complete nonblank stdout line in a Flight case; row validity/count/order is checked separately. |
| First request | Fixture's first HTTP/Flight request timestamp minus launch real-time timestamp. Same host, but this correlation is sensitive to wall-clock adjustments. |
| Request to stdout | First observed stdout minus first request; includes fixture delay, protocol and rendering, not just server processing. |
| Output span | Observed last minus first stdout; small outputs may arrive in one read, giving zero. |
| CPU | Child's user/system CPU via `wait4`; excludes collector and fixture CPU. Can exceed wall time for a multithreaded runtime. |
| Peak RSS | Child `ru_maxrss`, converted to bytes on Linux/macOS. Kernel accounting, not a sampled heap size or total memory for the whole test setup. |
| Faults/context switches | Per-child minor/major faults and voluntary/involuntary context switches. |
| Throughput | Output MiB or known fixture rows divided by total CLI wall time, including startup. Not a steady-state-only rate. |
| Binary size/identity | Byte size and SHA-256 for each executable, fixture and meter. |

The C supervisor matters: directly spawning from a large Python process can
carry the launcher's inherited high-water RSS into `wait4`, making a tiny Rust
binary appear to use the collector's memory. The supervisor execs into a minimal
address space **before** forking the measured CLI. Its own resource use is not
the child's measurement; native fork/exec overhead remains included. A regression
test allocates 80 MiB in the collector and verifies it does not contaminate RSS.

Reports include all raw samples plus median, mean, minimum/maximum, p90/p95/p99,
standard deviation and median absolute deviation. Percentiles from small samples
are descriptive, especially p99. Median ratios include a seeded nonparametric
bootstrap 95% interval (2,000 independent resamples); it is not a guarantee that
environmental noise has been removed.

These are **warm-cache, fresh-process** measurements. Binary hashing itself
warms file caches; warmup runs are excluded from reported statistics. The harness
does not flush caches, disable ASLR, change governors, pin CPUs or request root.
Cold-cache/page-fault experiments must be run separately in a controlled VM or
host, and labeled separately. Compare on the same otherwise idle machine with
the same power mode. Host/kernel/CPU/affinity/governor/Python/load metadata is saved.
Short commands may be near the fork/exec/scheduling floor; do not report tiny
percentage differences as proof of faster application logic.

## Compatibility is checked before performance

Each sample checks its expected exit, request sequence and absence of unexpected
fixture endpoints. Flight cases also check exact row count and sequence. Failed
samples do not contribute metrics. Timeouts/floods fail even if both binaries do
the same thing. A faster binary with different output gets no speedup ratio.

Comparison covers:

- Exact stdout and stderr bytes, including JSON formatting, empty lines and numeric
  representation. Original and normalized files are both kept.
- Actual OS termination: `signal:2` differs from `exit:130`; `signal:13` differs
  from `exit:141`. Tail signal cases send SIGINT or close the reader only after
  a deterministic complete batch, avoiding arbitrary sleep-based interruption.
- Ordered HTTP/Flight requests, method/path, selected auth/content-type/accept
  headers, request JSON values and query parameters. JSON key order and query-key
  ordering are semantic; repeated query values retain their order. Raw request
  bodies and timestamps remain available for stricter checks.
- Parsed TOML configuration before/after, file presence and permission bits.
  Whitespace/comments/key order do not constitute config semantic differences;
  the raw final TOML is also saved.
- Stability across repetitions: a changing reference is a failure, not a license
  to normalize a candidate's differences.

The only byte substitutions are the exact per-run temporary HOME and fixture URL.
There is no blanket whitespace, date, number, warning or version normalization.
All credentials are synthetic. The CLI receives an allowlisted environment,
isolated HOME/XDG paths, fixed UTC/C locale, dark theme, no input, and pipes rather
than a terminal. This is isolation from personal config, **not an OS network or
filesystem security sandbox**; run trusted executables and reviewed suites.

## Fixture coverage and extension

The initial 44 cases cover startup/version/help/catalog, parser/config errors,
profile list/add/update/logout, all four auth-header modes, status success/failure,
dataset list/schema/delete, saved-query listing, SQL text/JSON at 1/500/10,000 rows,
empty/malformed/trailing/rate-limited responses, 50ms response delay and chunked
transfer, PromQL instant/range/labels, finite Arrow Flight streams, SIGINT and
broken pipes. Flight uses real gRPC and Arrow IPC with multiple batches, large
integers, floats, strings, nulls and timestamps. Its finite throughput cases
deliberately end with `InvalidArgument` (expected CLI exit 1) to prevent an
infinite reconnect loop; they are not successful live sessions.

The fixture is loopback-only and a new process per sample. No shared mutable
backend or real refresh token is replayed. HTTP routes are explicit; unknown
requests fail, rather than receiving a generic success response. Mutation cases
currently characterize requests and local config effects, not full server-state
semantics. It is not a permissive fake that claims full Parseable correctness.

```sh
# Export built-in cases, edit/add a narrowly specified scenario, and run it.
python3 harness/run.py list --json > /tmp/pb-suite.json
python3 harness/run.py run --suite /tmp/pb-suite.json \
  --binary go=bin/pb-go --fixture bin/pb-fixture \
  --out harness/results/custom
```

Each case specifies `name`, `group`, argv list, profile/auth, expected exit/request
sequence, optional known rows and a fixture plan. HTTP routes specify method,
path, response body/status, delay and chunk settings. Flight plans specify rows,
batch size/delay and optional hold-open. `cases.py` supplies readable examples.
Scenario hashes prevent saved reports with different workloads being compared.

Still to add as the port reaches them: PTY/TUI actions and screen comparisons,
OAuth device flows, broader TLS/redirect/proxy cases, stateful remote mutations,
Flight dictionary/schema/reconnect/loss tests, broader parser/time/Unicode cases,
and Windows resource measurement. Live Parseable load tests need a separate
disposable deployment; fixture speed is not server-production latency.

## Rust phase-two acceptance and workloads

The fixed phase-two corpus adds 219 differential cases for profiles, authentication,
OAuth refresh, status and dataset reads, plus six candidate-only deferred-interaction
checks. Its separate Cloud fixture builds use a public dummy token. See
[the scope contract](contracts/phase2.md) and [phase-two results](../docs/rust-phase2.md).

```sh
make -f harness/Makefile phase2-build
make -f harness/Makefile phase2-check OUT=harness/results/phase2-check
make -f harness/Makefile phase2-measure OUT=harness/results/phase2-check REPETITIONS=30
make -f harness/Makefile phase2-workloads-measure OUT=harness/results/phase2-check REPETITIONS=30
```

The first measurement target uses all 14 fixed command cases. The second exports
four additional fixed workloads: status with 50 ms server delay, dataset info with
four 20 ms delays, a 10,000-dataset list, and a 4,096-field schema delivered in
4 KiB chunks with 2 ms delays. These are separate from the 219-case acceptance
corpus; every measurement still checks compatibility. Reports go to `performance/`
and `workloads/`, respectively. Each target excludes five warmups by default.

Native Rust transport tests additionally cover TLS trust/hostname validation,
proxies, redirects, compression and deadlines. The fixture corpus and these tests
do not establish parity against every real deployment or OS certificate store.

## Saved comparisons and regression gates

During the conversion, add a fixture-backed case for each newly ported command
or behavior, then compare Go/Rust before accepting that slice. Keep the last
accepted Rust report and binary as a second reference: Go/Rust comparison protects
compatibility, while previous-Rust/current-Rust comparison catches performance
regressions after the initial speedup. Measure small/default/large responses and
error paths separately. Record deliberate compatibility changes explicitly rather
than adding broad output normalization to make a mismatch pass.

```sh
python3 harness/run.py compare \
  harness/results/go-baseline/report.json \
  harness/results/rust-candidate/report.json \
  --out harness/results/comparison.json

# Require both >10% and >1ms median regression, with the entire bootstrap
# ratio interval above 1.10. At least ten measured samples per side required.
python3 harness/run.py compare baseline/report.json candidate/report.json \
  --max-regression-percent 10 --min-regression 1
```

For multi-binary reports use `--reference-binary go` and
`--candidate-binary rust`; the same report may be supplied on both sides.
`--metric` can select wall time, first output/record/request, CPU or peak RSS.
`--min-regression` is in that metric's units (milliseconds or bytes), so use a
meaningful byte threshold for RSS. Performance gates reject differing hosts,
fixture/meter hashes or relevant measurement settings. Ungated ratios across
such reports are explicitly marked non-comparable. Case sets/specifications
must match; incomplete reports cannot be compared. Save exact binaries alongside
the reports when you need to reproduce an old result.

Select cases with the chosen metric when gating: for example, startup cases do
not have a first-record metric. A missing metric makes a requested gate fail as
a setup error rather than silently passing.

Exit statuses: `0` passes; `1` compatibility/fixture/stability failure; `2` harness
setup or invalid-comparison error; `3` performance regression. Compatibility
failure takes precedence over performance. Missing metrics are not zero and do
not receive a ratio. Hosted-CI performance is noisy; CI self-comparison checks
harness correctness and archives measurements without enforcing speed gates.

Artifacts: `report.md` for reading, `report.json` for full metadata/samples,
`samples.csv` for plotting, and per-run stdout/stderr/request/config/contract files
under `artifacts/`. Output lives in ignored `harness/results/` by default; nothing
is uploaded by a local invocation. The CI workflow uploads its explicitly chosen
result directory for review.
