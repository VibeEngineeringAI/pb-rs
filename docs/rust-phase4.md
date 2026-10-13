# Phase 4: noninteractive metrics and saved SQL

This phase extends the native Rust CLI with PromQL queries, metrics metadata,
and saved SQL. The Go CLI remains the reference implementation and the complete
CLI during the port. Interactive SQL/PromQL, the saved-query picker, administration,
and Flight remain outside this phase. There is no new saved-query execution,
rename, or deletion verb.

The current allocation optimization and its three-way Go/before/after measurements
are in [Allocation optimization](#allocation-optimization). Earlier measurements
are retained as the pre-optimization baseline.

## Command behavior

PromQL supports range and instant `run`, `labels`, `label-values`, `series`, the
three `cardinality` operations, `active-queries`/`ps`, and `tsdb`, with text and
JSON output. Dataset defaults and positional overrides follow Go. Optional
invalid metadata times are omitted; instant queries ignore `--from` and `--step`.
Explicit time/step parsing preserves the existing Go grammar instead of imposing
new validation. Query keys are sorted while repeated selectors retain their order.

Metrics use the existing authenticated request machinery with a separate transport
policy: HTTP/1 only, no environment proxy, no standard dial/TLS timer, no automatic
JSON Content-Type, and a 120-second request/body deadline. Responses are collected
completely before output or HTTP/envelope error processing. This has proportional
memory growth and does not provide phase-three SQL's streaming memory bound.

JSON retains unknown fields, number spellings, sorted keys and last duplicate
values. Text uses command-specific typed decoding and Go formatting, including
the active-query display's potentially invalid UTF-8 truncation. Terminal output preserves the sampled Go color profiles and adaptive
light/dark palette, including the Unix OSC background probe and environment
controls. Seven foreground-PTY scenarios test the probe, timeout and termios
restoration; 16 additional pinned-Go comparisons matched exact output. Ordinary text
writer errors and JSON encoder errors have distinct policies; broken pipes keep
their process-level SIGPIPE behavior.

`sql list`/`ls` with a nonempty raw local output flag lists saved SQL; absent or
exactly empty output remains the deferred picker. Listing validates the entire
typed filter response, including builder records, and includes every non-null
SQL field regardless of filter type. `sql save QUERY` creates a saved query
without executing it. `sql run QUERY --save-as NAME` saves only after execution
succeeds; a save failure writes a warning and leaves execution successful.
Both save forms preserve their different name/SQL normalization behavior.
Successful save requires HTTP 200 and drops the unread response body. Each
request has its own 60-second deadline.

## Examples

With an existing active profile:

```sh
pb promql run 'up' --dataset otel_metrics --instant -o json
pb promql run 'rate(http_requests_total[5m])' --dataset otel_metrics --from 1h --to now --step 1m
pb promql series --dataset otel_metrics --match 'up{job="api"}'
pb promql tsdb --dataset otel_metrics --focus-label job --top 20
pb sql list -o json
pb sql save 'SELECT * FROM logs LIMIT 10' --name recent-logs --from 1h --to now
pb sql run 'SELECT * FROM logs LIMIT 10' --save-as recent-logs --from 1h --to now
```

## Validation and evidence

The source contract is the Mayfly `rust-port/phase-4` plan dated 2026-10-13.
The implementation branch starts at remote main `d960103`. A clean archive of
that revision supplies the pre-change Rust baseline; Go 1.25.4 supplies the
same pinned oracle toolchain as CI. Build identities and predeclared regression
budgets are retained with the local evidence in `target/phase4/baseline/`.

`harness/phase4.py` records exact stdout/stderr, ordered requests and raw body
bytes, termination, and configuration state using synthetic credentials and
loopback services. Its frozen Go manifest is separate from the candidate;
reference self-comparison precedes candidate comparison. Promoted operations
are removed from the old stub guards, while interactive/picker guards remain.
The phase-one through phase-three compatibility corpora continue to apply.

The frozen default Go corpus passes 153 cases in two reference runs and a
separate self-comparison; the candidate passes all 153 and four deferred guards. The real 120-second header and body deadline cases
also passed against the candidate; authenticated stored Go samples were reused
only after reconstructing and matching their original corpus digest.

Run the default gate with `make -f harness/Makefile phase4-check`; run the real
120-second header/body deadline tier with `phase4-deadline-check`. The latter
is intentionally separate from short default fixture timeouts. CI exposes an
opt-in deadline input. CI configuration does not establish a remote CI result.

`harness/phase4_workloads.py` defines vector/matrix, metadata, cardinality/TSDB,
saved listing and save/run-save workloads, plus representative delayed-header,
paced-body, gzip and slow-output variants. Freeze outputs against identical
hashed Go binaries before candidate timing. The measurement protocol uses five
excluded warmups and 30 samples, sequential seeded ordering, native process
metrics and disk-spooled output hashes. First useful result timing remains N/A
when the observer cannot identify it; first stdout is not a substitute.

The retained default gates passed 2,018 phase-one cases plus 27 guards, 219
phase-two cases plus six boundaries, and 675 phase-three cases plus eight
boundaries, with no skipped compatibility cases. All 103 large-workload smoke
cases passed exact output and request validation. These single-sample smoke
runs are correctness evidence, not performance results.

Reproduce the large-workload oracle and measurements with
`make -f harness/Makefile phase4-workloads-measure OUT=NEW_DIRECTORY REPETITIONS=30 WARMUP=5`.
Use an idle machine and the pinned toolchain; the target performs the Go
self-comparison before freezing and timing candidate output.

Rust validation passes 122 library tests and 60 integration tests (including
one test containing seven PTY scenarios), formatting, and all-target Clippy
with warnings denied. The Python harness passes 194 tests; its initially
skipped fixture-parity test was rerun successfully with `PB_PHASE3_FIXTURE`
pointing at the built fixture, and CI now sets that path. Go fixture tests pass.
A final focused SQL process run passes 20 signal/spinner cases after terminal
initialization changed.

A narrow terminal divergence is explicit: Go panics for an out-of-range numeric
`COLORFGBG` palette index, while Rust falls back to a dark background. Terminal
probing is validated on Unix, and extreme sample timestamps follow Go amd64
conversion behavior; other architectures remain unvalidated.

No live account mutation is necessary for fixture acceptance. This change does
not establish successful live metrics/SQL integration, cross-platform release
readiness, or a port-wide performance claim. Prior transport diagnostic and
non-UTF-8 argument divergences remain documented in the phase-three report.

The measurements below through the independent-seed confirmation describe the
initial implementation. The allocation optimization is evaluated separately at
the end of this report.

## Pre-optimization release identity

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Go 1.25.4 reference | 71,807,160 | `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314` |
| Pre-phase Rust from remote main | 7,124,600 | `67c869613cad8f83a564bc60c9c14063c541932beb2323edc3cf8c9b45eeaebf` |
| Phase-four Rust | 7,381,896 | `99800b0c1c95e5e1c36cb0a25ee106aa6fd7bf4cb199a8cdf1d8b4cf7ff84395` |

`target/phase4/provenance-final.json` records compiler versions, executable,
fixture and meter hashes, the base revision, source-file hashes and archived
runtime/test/harness sources. The branch is `feat/mayfly-phase4`, based on
remote main `d960103`. Reports and raw process artifacts remain local ignored
evidence under `target/phase4/`; this document records portable conclusions.
The predeclared comparison budgets are in `baseline/manifest.json`: investigate
shared-command median latency/CPU increases above both 10% and 1 ms, and RSS
increases above both 20% and 2 MiB. New commands require exact correctness and
report Go ratios without a universal speed threshold.

## Shared-command regression measurements

All 1,800 measured invocations and 300 excluded warmups passed across 15 shared
workloads and four labels: Go, prior Rust, candidate, and the identical candidate
as an A/A control. No candidate/prior-Rust median exceeded both parts of the
predeclared latency, CPU or RSS investigation budget. This is a practical-budget
result, not proof that every distribution is equivalent. For example, JSON SQL
help increased from 1.22 to 1.48 ms (about 21%), below the absolute 1 ms threshold.

| Shared workload | Wall median ms: Go / prior Rust / candidate | Candidate CPU ms | Candidate RSS MiB |
| --- | ---: | ---: | ---: |
| Version | 30.98 / 1.39 / 1.16 | 1.06 | 4.77 |
| Status | 32.36 / 2.26 / 2.20 | 1.77 | 6.08 |
| Dataset list | 29.62 / 2.06 / 2.06 | 1.75 | 6.04 |
| SQL 500 rows JSON | 34.59 / 4.44 / 4.76 | 4.59 | 6.21 |
| SQL 10,000 rows JSON | 114.92 / 41.62 / 39.94 | 40.92 | 6.05 |
| SQL 10,000 rows text | 32.48 / 4.71 / 5.02 | 4.26 | 6.41 |
| SQL 10,000 rows gzip JSON | 118.26 / 44.43 / 41.93 | 42.82 | 6.15 |
| SQL paced response | 273.45 / 245.14 / 244.99 | 55.35 | 6.16 |
| SQL slow reader | 428.93 / 401.49 / 401.61 | 49.69 | 6.55 |

`performance-shared/report.json` retains all samples, distributions, p95,
request/output validation, and Go-relative confidence intervals.
`shared-resource-comparisons.json` adds candidate/prior-Rust and candidate/A/A
comparisons for latency, CPU and RSS, including exploratory pointwise 95%
bootstrap ratio and absolute-delta intervals. Source, binary and collector
identities were fixed before timing; this thread ran no concurrent builds,
tests, profiling or agent implementation during the measurement window.

## Initial new-command measurements and memory tradeoff

All 6,180 measured invocations and 1,030 excluded warmups passed across the
103 phase-four workloads. Five warmups and 30 measured samples per binary used
seed 20261013 with sequential randomized implementation ordering. Every new
workload had a lower Rust median elapsed time in this run; this is an observation
about these fixtures, not a universal speed or statistical significance claim.
Paced and slow-reader cases include their deliberate delivery/consumer delays.

The shared Linux x86-64 machine has an i9-13900HK, 20 logical CPUs and the
powersave governor. Initial load averages were 5.62 / 8.16 / 9.17; unrelated
system/user activity was not controlled. These warm-cache, fresh-process local
measurements are advisory rather than isolated-machine release certification.
The p95 values from 30 samples below are descriptive, not reliable tail SLOs.
All paired columns show **Go / Rust**; CPU is child user plus system time.

| Workload | Wall median ms | Wall p95 ms | CPU median ms | Peak RSS median MiB |
| --- | ---: | ---: | ---: | ---: |
| Vector 1, JSON | 30.46 / 2.15 | 33.40 / 2.60 | 38.71 / 1.71 | 47.64 / 6.15 |
| Vector 10,000, JSON | 96.00 / 53.91 | 109.65 / 67.20 | 131.20 / 50.14 | 76.09 / 38.90 |
| Matrix 1,000 × 1,000, text | 1187.98 / 949.63 | 1230.60 / 1004.41 | 1458.04 / 905.21 | 209.61 / 670.43 |
| Matrix 1,000 × 1,000, JSON | 959.76 / 833.87 | 1020.07 / 863.25 | 1282.30 / 760.55 | 356.85 / 651.48 |
| Labels 100,000, text | 559.58 / 71.88 | 596.92 / 78.56 | 600.25 / 66.94 | 65.24 / 32.64 |
| Series 100,000 × 8 labels, text | 1357.16 / 626.06 | 1412.39 / 656.88 | 1489.75 / 584.33 | 161.75 / 300.20 |
| Series 100,000 × 8 labels, JSON | 779.43 / 602.18 | 802.96 / 640.94 | 993.35 / 552.37 | 314.16 / 403.11 |
| TSDB 4 × 10,000 entries, text | 2607.40 / 772.26 | 2717.60 / 810.61 | 2678.07 / 754.13 | 92.97 / 70.33 |
| Saved list 10,000 records, JSON | 69.40 / 42.11 | 80.12 / 52.84 | 90.94 / 38.14 | 61.21 / 32.98 |
| Standalone save | 29.67 / 1.97 | 33.60 / 2.21 | 38.42 / 1.64 | 47.60 / 5.94 |
| Query then save, JSON | 30.18 / 2.18 | 35.31 / 2.63 | 39.29 / 1.81 | 47.85 / 6.09 |
| TSDB paced body, JSON | 7173.36 / 6990.59 | 7196.62 / 7014.69 | 568.58 / 236.19 | 150.88 / 93.37 |

The initial largest matrix and series cases exposed a material memory cost. The million-
sample text matrix uses about 670 MiB peak RSS versus Go's 210 MiB (3.20×),
and its JSON form uses about 651 versus 357 MiB (1.83×). The 100,000-series
text/JSON cases use about 300/403 MiB versus Go's 162/314 MiB. Small responses
remain substantially smaller in Rust. Memory scales with response/sample size;
this path has no SQL-style streaming memory bound.

The initial implementation exposed that measured tradeoff. Source inspection showed
the buffered body is decoded for envelope validation, then decoded again for
rendering. Text projection temporarily owns both the ordered generic tree and
typed results; JSON converts and sorts owned nodes before producing a complete
output buffer. These overlapping representations are a concrete optimization
target. This is source-based analysis, not a heap-profile attribution, and the
extra Rust RSS is not presented as an unavoidable cost of compatibility.

| Largest case | Rust/Go wall ratio, exploratory 95% CI | Rust/Go RSS ratio, exploratory 95% CI |
| --- | ---: | ---: |
| Matrix 1,000 × 1,000, text | 0.80 [0.78, 0.81] | 3.20 [3.17, 3.30] |
| Matrix 1,000 × 1,000, JSON | 0.87 [0.84, 0.89] | 1.83 [1.58, 2.02] |
| Series 100,000 × 8 labels, text | 0.46 [0.45, 0.47] | 1.86 [1.85, 1.87] |
| Series 100,000 × 8 labels, JSON | 0.77 [0.76, 0.79] | 1.28 [1.24, 1.32] |

`performance-workloads/report.json` retains raw samples, per-case distributions,
validation and Go-relative intervals. `workload-resource-comparisons.json` adds
CPU and RSS ratio/absolute-delta intervals for every case. Intervals use the
existing unpaired pointwise bootstrap and remain exploratory; no simultaneous
inference or population-wide performance claim is made. Complete output hashes
establish count/order; command-specific first useful result timing remains N/A.
Fixture final-write-start observations are only a delivery lower bound, while
delayed-EOF and body-fault correctness fixtures separately establish buffering.
The collector spools output during execution and materializes it after timing;
its own memory is excluded from child peak RSS. Native CPU/RSS/fault/context-
switch counters and logical body/output byte counts are retained; missing
process-I/O metrics stay unavailable, and wire/NIC or heap-allocation attribution
is not claimed.

### Independent-seed confirmation

The four largest matrix/series cases were repeated with seed 20261014 using
five warmups and 30 measured samples per binary. All 240 measured invocations
and 40 warmups passed. The higher Rust RSS persists in the second run:

| Workload | Peak RSS MiB, Go / Rust | Rust/Go RSS ratio, exploratory 95% CI |
| --- | ---: | ---: |
| matrix 1000x1000 text | 210.05 / 670.31 | 3.19 [3.17, 3.20] |
| matrix 1000x1000 json | 321.00 / 651.42 | 2.03 [1.53, 2.08] |
| series 100000 text | 162.08 / 300.24 | 1.85 [1.83, 1.86] |
| series 100000 json | 319.46 / 403.31 | 1.26 [1.23, 1.32] |

Both runs are retained: `performance-memory-confirmation/report.json` and
`memory-confirmation-comparisons.json` supplement the original workload report.
Across shared commands, new workloads and this confirmation, **8,220 measured
invocations and 1,370 excluded warmups** passed. All prebuilt executable hashes
remained unchanged. No live-server, remote-CI or cross-platform release result
is implied by these local fixtures.


## Allocation optimization

Go's advantage was representational: its request-layer envelope decoder selects
three string fields and skips sample data, and its text decoder populates the
command structures directly. The initial Rust implementation constructed an
ordered generic JSON tree to check that envelope, constructed another tree for
rendering, and copied samples into typed values while retaining the generic tree.
It also retained the complete rendered output. This was an implementation cost,
not a language requirement or a more sophisticated query algorithm in Go.

The optimized implementation validates and skips JSON subtrees without allocating
nodes. Envelope checks decode only selected fields. Ordinary vector/matrix text
processes one sample at a time using borrowed response fragments; series metadata
processes one series at a time. JSON serialization sorts each object's borrowed
members and emits arrays without constructing a complete generic tree or a second
sorted tree. The existing Go-compatible scalar decoder still handles invalid UTF-8,
Unicode replacement and number spelling. Repeated enclosing text fields fall back
to the general decoder to preserve Go's slice-reuse and merge behavior; repeated
fields within one series use that decoder for the affected series.

The body and rendered output remain buffered, and point/element span arrays still
scale with element count. This is not constant-memory streaming. The optimization
keeps output private until validation succeeds, preserving the absence of partial
stdout on a late type error. Repeated containers and type-invalid responses can still take the
more expensive compatibility path. Deep JSON containers are rescanned by nesting
level; the depth-limit and exact-output checks still apply.

The workload suite now includes both 1,000 series × 1,000 samples and one series ×
1,000,000 samples, preventing a per-series allocation limit from hiding a large
single-series cost. All original 103 workload definitions are unchanged; the two
new shape cases have a separate pinned-Go self-comparison and frozen output/request
expectations.

### Optimization measurement protocol

The saved pre-optimization Rust executable is the original `99800b0c…` artifact;
the optimized executable is `f3495152f52bc2a4e3950ca6293371aeb670e56531d94e2137632c0386677f2b`.
The pinned Go executable remains `fae8411f…`. Fourteen workloads compare all
three in the same run, using 30 measured samples and five excluded warmups per
binary, seed 20261014 and sequential randomized implementation ordering. They
cover small/vector controls, both million-point matrix shapes, 100,000 labels,
100,000 series and large TSDB output. CPU time means child user plus system CPU
milliseconds; latency means elapsed process wall time; memory means native child
peak RSS. The predeclared investigation threshold is an increase over the old
Rust median above both 10% and 1 ms for either latency or CPU, with a target of
halving peak RSS in the original four largest matrix/series cases.

These remain warm-cache fresh-process loopback measurements on a shared host.
Unrelated activity is uncontrolled; this thread runs no builds, tests or agent
implementation during timing. The compared binaries and sources are hashed and
verified after measurement. Thirty-sample p95 values and pointwise bootstrap
intervals are descriptive/exploratory, not tail guarantees or simultaneous
statistical claims. Local provenance, the source archive, frozen cases, raw
samples and comparison summaries are under `target/phase4/optimization/`.

The final optimized binary passes 131 Rust library tests and 60 integration
tests, all-target Clippy with warnings denied, formatting, 153 phase-four cases
plus four deferred guards, 675 SQL cases plus eight boundaries, and all 105
large-workload correctness cases. The shared parser changes also passed the 219
phase-two cases and six boundaries before the final metrics-only refinement.
Python harness validation covers 196 tests; the Go diagnostic sidecar tests were
rerun with the pinned Go toolchain after the default shim failed to build them.
The temporary-directory quota requires `TMPDIR` under the workspace on this host.

Fresh oracle comparison uncovered two harness issues, both corrected: valid TOML
configuration must compare its normalized semantic content across random fixture
ports (raw baseline integrity and per-process mutation checks remain intact), and
the slow-reader sleep must clamp a deadline difference that can become negative
between clock reads. The passing SQL gate exercises the corrected slow-reader path.


### Optimized results

All **1,260 measured invocations and 210 excluded warmups** passed exact output
and request validation. No selected workload exceeded both parts of the declared
CPU/latency regression threshold against the saved Rust build. Across the original
four large matrix/series cases, median peak RSS fell **65–92%**, CPU time fell
**25–45%**, and elapsed latency fell **24–41%**. The optimized Rust medians were
also below Go for all three measures in these four cases. The one-series million-
sample stress cases confirm the improvement extends across matrix shapes.

All triples below are **Go / previous Rust / optimized Rust**. These are fresh
within-run comparisons, so Go and previous-Rust timings need not equal the older
report's measurements. Host load at the start was 10.87 / 9.67 / 8.17.

| Workload | Wall median ms | CPU median ms | Peak RSS median MiB |
| --- | ---: | ---: | ---: |
| Vector 1, JSON | 25.24 / 1.89 / 1.95 | 35.06 / 1.65 / 1.66 | 53.77 / 6.92 / 6.20 |
| Vector 10,000, text | 120.04 / 56.84 / 43.64 | 131.54 / 52.43 / 39.46 | 60.19 / 34.23 / 9.16 |
| Vector 10,000, JSON | 131.54 / 73.78 / 47.71 | 157.86 / 67.50 / 40.76 | 78.53 / 39.66 / 15.92 |
| Matrix 100 × 100, text | 59.84 / 16.34 / 15.01 | 67.65 / 14.33 / 12.94 | 50.06 / 13.86 / 6.68 |
| Matrix 1,000 × 1,000, text | 1247.54 / 1021.33 / 720.79 | 1510.08 / 975.22 / 679.03 | 213.41 / 671.38 / 56.85 |
| Matrix 1,000 × 1,000, JSON | 1089.04 / 859.78 / 637.68 | 1419.22 / 787.56 / 553.68 | 394.62 / 652.46 / 227.77 |
| Labels 100,000, text | 742.37 / 82.78 / 74.14 | 761.35 / 78.88 / 67.17 | 63.91 / 33.41 / 30.23 |
| Labels 100,000, JSON | 86.04 / 35.82 / 27.89 | 124.00 / 32.31 / 24.43 | 78.11 / 36.45 / 15.53 |
| Series 100,000 × 8 labels, text | 1420.52 / 646.42 / 492.70 | 1544.78 / 607.46 / 455.31 | 164.86 / 301.20 / 55.07 |
| Series 100,000 × 8 labels, JSON | 836.09 / 611.52 / 359.89 | 1050.27 / 562.96 / 311.14 | 318.94 / 404.13 / 124.09 |
| TSDB 4 × 10,000 entries, text | 2711.18 / 787.83 / 778.63 | 2759.04 / 772.96 / 764.53 | 94.71 / 71.96 / 69.98 |
| TSDB 4 × 10,000 entries, JSON | 322.50 / 130.07 / 128.32 | 411.39 / 112.16 / 110.30 | 201.01 / 96.31 / 59.80 |
| Matrix 1 × 1,000,000, text | 1196.61 / 972.95 / 713.93 | 1567.35 / 930.84 / 674.89 | 245.97 / 710.08 / 73.09 |
| Matrix 1 × 1,000,000, JSON | 948.29 / 882.20 / 575.28 | 1407.46 / 813.47 / 507.08 | 382.46 / 652.62 / 227.93 |

The small-vector latency increase (1.89 → 1.95 ms) is below the investigation
budget; this report does not claim every operation became faster. TSDB text and
JSON medians changed little. Peak RSS and CPU are measured independently of wall
time, and CPU is summed across threads; Go's CPU can therefore exceed elapsed time.

`optimization/performance/report.json` contains samples, p95 and Go-relative wall
comparisons; `optimization/summary.json` adds Go-relative and previous-Rust-relative
CPU, RSS and wall comparisons with exploratory pointwise bootstrap intervals.
`optimization/provenance.json` records the verified identities and source archive.
To reproduce the timed comparison from the retained artifacts:

```sh
TMPDIR="$PWD/target/phase4/tmp" python harness/run.py run \
  --binary go=bin/pb-go-pinned \
  --binary before=target/phase4/optimization/pb-before \
  --binary after=target/phase4/optimization/pb-after \
  --fixture bin/pb-fixture --meter bin/pb-meter \
  --suite target/phase4/optimization/suite-final.json \
  --repetitions 30 --warmup 5 --seed 20261014 --timeout 120 \
  --out NEW_DIRECTORY
```

For a new checkout, `phase4-workloads-measure` regenerates/freeze-checks the current
105-case suite against Go. The separate before-Rust binary is local retained
evidence rather than a committed executable. No live-service or remote-CI result
is implied.

## PR review fixes

The independent review of `59f18545ef25c08df0567efda23b88836c569543`
identified one P2 and four P3 issues. Regression tests were written and run
before the production changes: header resets, both minimum-duration paths,
ANSI state transitions, invalid/empty case selection, and the proxy fixture
all failed for the reported reasons. Adjacent-duration and body-reset controls
already passed. Five GPT-6.1-Sol and six Claude Opus 5.5 agents participated
in implementation/design verification and final independent review.

The fixes preserve Go's header-only reset rewrite and wrapping duration
negation, reproduce the pinned `x/ansi` control-state transitions, reject
unknown/gated/empty harness selections, and make the proxy case observe a
proxy-eligible local hostname. A standard SQL transport control proves that
the same controlled listener sees an absolute request target when a proxy is
used; the listener itself remains bound to loopback. The Go oracle was freshly
captured twice and frozen, with unchanged semantic signatures for all existing
cases. Raw capture metadata changes because fixture ports and paths change.

The additional ANSI review caught orphan UTF-8 continuation bytes after a
raw ST byte ends OSC/DCS data. Six pinned-Go process repros and permanent Rust
regressions cover that case as well as the original padding failures. Three
reset process repros match Go's complete stdout/stderr and exit status.

Local validation after fixes: 205 Rust tests; 206 Python harness tests without
skips; rustfmt; Clippy with warnings denied; Go fixture tests; phase two
219 cases plus six boundaries; phase three 675 cases plus eight boundaries;
phase four 153 cases plus four guards; and all 105 frozen large-workload cases.
The real 120-second deadline tier was not rerun for these fixes; earlier
successful results remain evidence, and the deadline implementation is unchanged.
No live-service or cross-platform validation is claimed.

Performance was predeclared before timing: 24 workloads, two immutable release
binaries, five excluded warmups and 30 measured samples per binary, sequential
seeded randomized order (`20261015`). All prior 14 optimization workloads are
included, plus width-heavy cardinality, saved SQL and ANSI-heavy text cases.
The latter were checked against pinned Go and their output hashes frozen before
timing. Parent and child builds/tests were stopped during measurement. Timing
uses native process wall/CPU and peak RSS; first useful result remains N/A.
Budgets flag median increases exceeding both 10% and 1 ms for wall/CPU, or both
20% and 2 MiB for peak RSS. Width-heavy text increases above 5% trigger a
follow-up even below budget. Flagged/watched cases are repeated with seed
`20261016` and a byte-identical baseline control to expose host noise.

The first correctness fix exposed a real performance regression in the two
ANSI-heavy stress cases: approximately 12% higher wall time and CPU in two
seeds, while a byte-identical baseline control stayed within 1% in wall time.
That result prompted a narrow OSC/DCS data-scan fast path. It searches directly
for the pinned terminators, preserving cancellation, ESC restarts and raw ST
bytes inside UTF-8. No new allocation or Unicode-width algorithm was added.
All correctness gates relevant to the revised metrics renderer were rerun.
The complete 24-workload final comparison uses seed `20261017`, with the ANSI
stress cases first; all initial and follow-up measurements remain retained.

The final run passed all 1,440 measured samples and 240 excluded warmups,
with no predeclared wall/CPU/RSS budget violations or width watch flags.
The ANSI cardinality and TSDB stress cases retain 2.7% and 4.3% wall-time
overhead (2.6% and 4.0% CPU); this is within the limits, not a claim of zero
cost. Small startup measurements remain sensitive to shared-host noise.
Peak RSS is stable, including the million-point text cases. P95 observations
are included below for visibility; the declared gates use medians.

Each cell is **before → final**. Wall/CPU are milliseconds; RSS is MiB.

| Workload | Median wall | P95 wall | Median CPU | Median peak RSS |
|---|---:|---:|---:|---:|
| p4x-ansi-cardinality-label-names-10000-text | 275.52 → 282.95 | 297.63 → 316.55 | 269.62 → 276.75 | 27.14 → 27.42 |
| p4x-ansi-tsdb-10000-text | 1052.22 → 1097.46 | 1133.57 → 1146.08 | 1027.58 → 1068.68 | 90.42 → 90.57 |
| p4-vector-1-json | 1.78 → 1.87 | 2.52 → 2.19 | 1.47 → 1.59 | 6.47 → 6.84 |
| p4-vector-10000-text | 35.44 → 33.32 | 44.35 → 46.20 | 32.08 → 30.60 | 9.34 → 9.58 |
| p4-vector-10000-json | 31.87 → 31.58 | 44.85 → 47.18 | 27.74 → 27.95 | 16.17 → 16.44 |
| p4-matrix-100x100-text | 9.97 → 10.17 | 13.10 → 13.12 | 8.96 → 9.05 | 6.90 → 7.10 |
| p4-matrix-1000x1000-text | 689.53 → 706.99 | 716.75 → 731.49 | 647.14 → 663.50 | 56.74 → 57.21 |
| p4-matrix-1000x1000-json | 592.18 → 591.01 | 628.23 → 678.81 | 518.79 → 521.12 | 227.79 → 227.92 |
| p4-labels-100000-text | 62.28 → 64.28 | 82.28 → 88.41 | 58.32 → 59.54 | 30.34 → 30.55 |
| p4-labels-100000-json | 28.44 → 28.88 | 38.60 → 39.93 | 23.98 → 24.16 | 15.85 → 16.13 |
| p4-series-100000-text | 475.65 → 485.45 | 520.51 → 517.06 | 434.63 → 442.18 | 55.21 → 55.32 |
| p4-series-100000-json | 339.61 → 337.68 | 375.80 → 363.08 | 293.74 → 293.63 | 124.28 → 124.54 |
| p4-tsdb-10000-text | 788.45 → 783.17 | 881.02 → 833.98 | 770.33 → 765.22 | 68.78 → 68.91 |
| p4-tsdb-10000-json | 131.92 → 130.59 | 145.41 → 146.79 | 112.69 → 111.53 | 59.72 → 59.53 |
| p4-matrix-1x1000000-text | 696.38 → 715.83 | 724.86 → 750.30 | 659.56 → 679.15 | 72.89 → 72.75 |
| p4-matrix-1x1000000-json | 560.70 → 555.26 | 574.89 → 601.12 | 500.26 → 496.21 | 228.48 → 229.28 |
| p4-cardinality-label-names-10000-text | 198.22 → 200.94 | 215.81 → 210.32 | 194.05 → 195.02 | 21.94 → 22.02 |
| p4-cardinality-label-values-10000-text | 201.69 → 201.51 | 214.52 → 216.65 | 195.58 → 195.95 | 21.93 → 21.92 |
| p4-cardinality-active-series-10000-text | 56.58 → 56.10 | 76.92 → 64.44 | 51.53 → 51.47 | 32.82 → 32.91 |
| p4-cardinality-label-names-20-text | 2.40 → 2.40 | 2.92 → 2.79 | 2.12 → 2.03 | 6.44 → 6.61 |
| p4-values-100000-text | 61.05 → 59.71 | 79.39 → 68.17 | 57.22 → 55.75 | 30.74 → 30.88 |
| p4-saved-create-1-default | 1.80 → 1.92 | 2.05 → 2.08 | 1.52 → 1.59 | 6.31 → 6.53 |
| p4-run-save-1-json | 2.12 → 2.31 | 2.45 → 2.52 | 1.72 → 1.91 | 6.51 → 6.72 |
| p4-saved-list-10000-json | 41.92 → 42.71 | 49.28 → 54.64 | 37.74 → 38.95 | 33.31 → 33.54 |

Baseline release SHA-256: `f3495152f52bc2a4e3950ca6293371aeb670e56531d94e2137632c0386677f2b`.
Final release SHA-256: `02b14a9adbc756303785c94a09f301f3dd3eae4c18967f23d59235a9eb07d3a2`.
Evidence is retained under `target/fixes/`: failing/passing test logs, exact
Go process repros, refreshed compatibility reports, workload correctness, and
`perf/{protocol.json,run,recheck,final}` with all samples and summaries.
The prepared suite and preparation/summary scripts are retained alongside the
reports. Shared-host measurements are evidence for these workloads, not a
guarantee for every service, platform, input, or tail-latency distribution.

The final run's saved-list P95 was 10.9% higher, despite a median inside budget.
An additional 30-sample follow-up (`20261018`, retained in
`perf/tail-recheck`) included the identical-baseline control: P95 was
50.82 ms baseline, 50.61 ms control, and 45.91 ms final; medians were
41.06 → 41.43 ms. The tail increase did not reproduce. This extra check was
prompted by the observed P95, rather than being part of the median gate.
