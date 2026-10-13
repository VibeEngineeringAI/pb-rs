# Phase 2: configuration and first online commands

This slice adds native Rust configuration, authentication, and HTTP services to
`pb`, beginning with `status` and dataset reads. The implementation covers the
phase-two scope below and has passed local release validation. Go remains the complete CLI; this candidate is not a replacement
for its SQL, PromQL, administration, Flight, and interactive workflows yet.

## Design

One package owns its command declarations, compatibility parser, configuration,
authentication, HTTP services, command-specific output, and typed errors. It
neither executes Go nor translates the original package tree. Profiles are
explicit values; a permissive persisted record is separate from the runtime
credential enum. Commands create a current-thread Tokio runtime only for online
work. Offline help, version, discovery, and local profile operations do not start
networking.

The HTTP service uses Hyper and Rustls with an owned credential enum and typed
transport errors. TLS roots are loaded lazily for HTTPS. Response decoding and
command rendering are separate, so wire compatibility does not dictate the
internal domain model. The JSON boundary preserves number spelling where Go
does; it also bounds nesting and safely releases deeply nested values.

The unchanged Go executable, built from baseline `07af390` with Go 1.25.4, is the
behavioral reference. Compatibility covers argument handling, streams and exits,
JSON details, endpoint paths, request ordering, authentication, and configuration
effects. Internal organization, ownership, and implementation can improve without
changing that contract.

The initial scope is `status`, `dataset list`, `dataset info`, `dataset schema`,
noninteractive profile setup/list/update/default/remove, logout with `--yes`,
and Cloud API-key profile setup. Basic, self-hosted API keys, Cloud keys with
tenants, and existing Cloud OAuth sessions are included. OAuth refresh retries
once and persists rotated credentials; no general retry policy is added.
Interactive login, device authorization, credential forms, and profile selection
remain explicit deferred operations.

## Compatibility details

- Profile loading stays permissive. A malformed credential combination can still
  be inspected or edited; authentication is checked when making a request.
- Existing `Profiles` and `DefaultProfile` TOML data remain interoperable. API-key
  aliases resolve by nonempty `apiKey`, then `api_key`, then `token`.
- Public profile output excludes credentials; config files keep Unix mode 0600.
- Status failures in JSON mode own stdout and exit 1, without a duplicate stderr
  error envelope.
- Dataset list and info validate output format after their network work. Schema
  text can emit earlier rows before failing on a later field.
- Standard API and Prism requests retain distinct endpoint prefixes and deadlines.
- Config saves retain Go's truncating writes. Atomic replacement would change
  symlink/inode/failure behavior and is a separate decision.

## Verification and measurement

Preserve the phase-one 2,018-case offline corpus. Explicitly promote implemented
commands from deferred guards to online acceptance; never select tests based on
whether the candidate happens to pass. Test each implementation with independent
temporary HOME/config and fixture state. Compare exact deterministic stdout,
stderr and termination, plus semantic HTTP requests and config effects. Include
Go-write/Rust-read and Rust-write/Go-read transitions and failure cases.

Measure fixed cases with prebuilt binaries and fresh processes: wall time, first
output, first request, CPU, peak RSS, and binary size. Include controlled server
delays and compare offline behavior to the saved phase-one Rust binary as well
as Go. Separate warmups from measured samples; retain executable hashes, host
metadata, and all transcripts. Shared-host results are exploratory rather than
controlled release performance guarantees.

The Mayfly wiki page `rust-port/phase-2` records this scope and the recommendations.

### Verified results

The final standard and Cloud release builds passed:

- **2,018/2,018** unchanged offline differential cases and **52/52** remaining
  deferred guards ([report](../harness/results/phase2-offline-verified/report.md)).
- **219/219** phase-two differential cases, no skips, plus **6/6** candidate-only
  deferred-interaction checks ([report](../harness/results/phase2-verified/report.md)).
- **97 Rust tests**, **69 Python harness tests**, Go fixture tests, `cargo fmt`
  and Clippy with warnings denied.

Sol 6.1 implementation work and Opus 5.5 harness work received independent Opus
and Astra review. Their final bounded reviews found no remaining blockers.
Review-driven fixes include credential-safe URL diagnostics, config key casing,
duplicate/null JSON behavior, deeply nested serialization/drop safety, redirect
resolution, gzip and deadline handling. A final nonblocking Opus finding about
proxy-refusal diagnostics was subsequently fixed and checked against Go for three
proxy forms. A Cloud release probe also matched Go's custom OAuth HTTP reason
phrase. Review reports and supplemental probe/test evidence are retained under
[`phase2-verified/evidence`](../harness/results/phase2-verified/evidence/).

The CI workflow builds both variants, runs the acceptance gates and archives
performance evidence. It has not been run remotely for these uncommitted changes.

### Release measurements

All **2,250 measured samples** passed compatibility and stability checks: 13 offline
cases with Go/phase-one Rust/phase-two Rust, 14 online cases with Go/Rust, and
four additional delay/size workloads with Go/Rust. Each binary/case has 30 measured
runs after five excluded warmups. Binaries ran sequentially in seeded randomized
order, with fresh processes/config/fixture state. Builds and suites did not run
concurrently with one another.

Linux x86-64, Intel i9-13900HK, powersave governor; this was a shared machine with
background load, not an idle controlled benchmark host. These are warm-cache
observations, not cold-start or production-server promises. The Rust candidate
still contains fewer features than the full Go binary.

Each cell below is **Go / Rust phase two**. CPU and RSS are medians; wall includes
process creation, CLI execution and any fixture waits.

| Workload | Wall median ms | Wall p95 ms | CPU ms | Peak RSS MiB |
|---|---:|---:|---:|---:|
| Version | 26.75 / 1.23 | 30.09 / 1.40 | 36.04 / 1.13 | 52.73 / 4.60 |
| Status JSON | 25.77 / 2.01 | 31.37 / 2.21 | 35.16 / 1.74 | 53.54 / 5.78 |
| Dataset info JSON | 28.73 / 2.42 | 32.21 / 2.79 | 37.68 / 1.93 | 53.66 / 6.05 |
| Status + 50 ms delay | 76.66 / 52.32 | 81.07 / 52.54 | 36.60 / 1.83 | 53.91 / 5.71 |
| Info + four 20 ms delays | 108.68 / 83.56 | 113.62 / 83.93 | 37.49 / 2.19 | 53.70 / 5.96 |
| List 10,000 datasets | 44.69 / 17.17 | 50.37 / 23.05 | 64.45 / 15.76 | 62.47 / 19.11 |
| Schema 4,096 fields, delayed chunks | 161.38 / 132.67 | 165.34 / 135.14 | 54.73 / 9.22 | 57.99 / 9.01 |

For status JSON, the median first request arrived at **24.83 / 1.60 ms**, and
first stdout at **25.02 / 1.84 ms**. The large list emitted 1,170,003 identical
stdout bytes; the chunked schema emitted 372,757. These commands buffer finite
responses before rendering, so chunked input does not imply streaming results.

The same-run phase-one comparison exposes a cost of the added functionality:
version median rose from **0.92 to 1.23 ms**, CPU from **0.82 to 1.13 ms**, and peak
RSS from **2.52 to 4.60 MiB**. Across all 13 offline cases, phase-two medians rose
by about **0.29–0.46 ms**. Retain this baseline for future regression checks rather
than attributing every comparison to the Go speedup. Sub-millisecond differences
remain sensitive to host scheduling and loading.

Standard release binary sizes: Go **71,807,160 bytes**, Rust phase one **761,328
bytes**, Rust phase two **6,971,728 bytes**. Identity of the final phase-two binaries:

- Standard Rust: `ac84e6d6d1291457826998cc92813732e411ad7c89740660f68f1c1abfe52db9`.
- Cloud fixture Rust: `545a448011c07f3734db88c82c86aab76b384482d228c00590c577a862ce1f05`.

Full reports, raw samples, metadata, binary hashes and per-run transcripts:
[offline](../harness/results/phase2-offline-verified/performance/report.md),
[online](../harness/results/phase2-verified/performance/report.md), and
[delay/size workloads](../harness/results/phase2-verified/workloads/report.md).
These artifacts are local and ignored by Git; the CI workflow retains its own runs.

## Reproduce

Use Rust 1.96 and the pinned Go 1.25.4 oracle. These commands build separate
test binaries; they do not install or replace `pb`:

```sh
make -f harness/Makefile phase2-build
PB_GO_ORACLE="$PWD/bin/pb-go" make -f harness/Makefile rust-test test
make -f harness/Makefile phase1-check OUT=harness/results/offline-check
make -f harness/Makefile phase2-check OUT=harness/results/online-check
make -f harness/Makefile phase1-measure OUT=harness/results/offline-check REPETITIONS=30
make -f harness/Makefile phase2-measure OUT=harness/results/online-check REPETITIONS=30
make -f harness/Makefile phase2-workloads-measure OUT=harness/results/online-check REPETITIONS=30
```

The Cloud fixture binaries use a deliberately public test token. Ordinary builds
keep that token unset and retain the missing-release-configuration behavior.
The benchmark targets exclude five warmups per case. To include the saved
phase-one binary in the offline measurement, call `harness/run.py run` with the
same exported suite and three `--binary` arguments: `go=bin/pb-go`,
`rust-phase1=bin/pb-rust-phase1`, and `rust=target/release/pb`.
Set `TMPDIR` and
`GOTMPDIR` to existing directories with sufficient space when needed.

## Boundaries

The phase-two gate has 219 fixed differential cases and six candidate-only
checks for explicit deferred interactive operations. Six Go terminal cases
remain available with `--include-future-terminal`; excluding them is an explicit
scope boundary, not a parity claim. See [the corpus contract](../harness/contracts/phase2.md).

Validation uses local fixtures and dummy credentials, not a live Parseable or
Cloud account. Linux is the exercised platform; the native certificate-store
fallback on other operating systems is not yet verified. OS-dependent DNS, socket and TLS error wording can differ from Go, while the
command error policy is retained. Exact diagnostics for every malformed input
are not claimed.
A Go panic on TOML date/time values assigned to string fields is deliberately
not reproduced: the Rust decoder returns an error. Interactive workflows,
SQL/PromQL, mutations, administration and Flight remain deferred.

## Suggested next slice

Use this configuration and transport foundation for noninteractive SQL queries
next, with fixed time ranges, result/error compatibility and large-response
measurements. Add saved-query behavior and PromQL in separately gated slices.
Keep terminal UI and Arrow Flight work separate: they need PTY, cancellation,
streaming and backpressure contracts beyond these finite HTTP reads. The proposed
ingestion/observer workflow remains a new feature, outside the compatibility port.
