# Phase 5: dataset, user, and role administration

The Rust CLI implements noninteractive dataset creation/deletion, self-hosted
user creation, user deletion and additive role assignment, user/role inventories,
and role deletion. This follows the Mayfly `rust-port/phase-5` plan dated
2026-10-13. The branch starts at `411e56f`, after Phase 4 merged.

## Command boundary

```sh
pb dataset add logs --type logs
pb dataset add metrics --type metrics
pb dataset add traces --type traces
pb dataset remove metrics --type metrics
pb dataset rm logs
pb user add alice --role reader
pb user set-role alice reader,writer
pb user list -o json
pb user rm alice
pb role list -o json
pb role rm writer
```

Dataset creation requires an explicit nonempty type: `logs`, `metrics`, or
`traces`, normalized by trimming and lowercasing. Omitted/blank creation types
and the entire interactive `role add` entry point remain deferred to Phase 7.
Dataset deletion without a type is unguarded. A supplied type first reads dataset
information and prevents deletion when the type differs. There is no confirmation
prompt, new privilege flag, or new identifier restriction.

`user add` rejects Cloud profiles before making a request. Self-hosted creation
requires an existing role. `user set-role` calls the additive endpoint, preserving
server-side assignments; it does not replace roles. Role input keeps order,
duplicates, and empty comma-separated components. Role deletion checks existence
first and prints sorted guidance for an absent name.

Mutations keep their existing text-only success output. In particular, user
creation writes the password response as bytes, including invalid UTF-8. The raw
argv JSON-error scan does not add an output flag to mutation commands. A successful
mutation is never retried because printing failed.

## Request and output policies

The services share the standard authenticated client, one eligible OAuth refresh
and replay, replayable bodies, and per-request 60-second deadlines. Dataset
creation sends telemetry headers with no request body. A successful dataset
create or any deletion drops the response body; user creation and additive role
assignment read it before checking status. Every mutation requires exactly HTTP
200. Other status responses are failures even when they are another 2xx code.

HTTP/1 stale-connection handling retries replayable reads only on a reused
connection before response bytes arrive. An unsent request returned by Hyper
can be retried without duplicating a mutation. Fresh-connection failures,
partial responses and committed mutations are not generally retried. Tracking
sits above TLS so encrypted handshake traffic is not mistaken for HTTP data.
The caller's absolute deadline bounds these attempts.

One conservative transport boundary remains: Go can retry a mutation when its
very first socket write fails without sending bytes. Hyper does not expose
that exact proof in every error path; Rust fails that ambiguous case rather
than replaying a possibly sent mutation. This is an explicit compatibility
limitation, separate from the tested unsent-request and committed-mutation cases.
At some cuts inside malformed HTTP headers, Hyper exposes only an incomplete
message where Go reports a more specific malformed-response error. An HTTPS
proxy closing a plain-HTTP destination tunnel without TLS shutdown can still
expose a rustls-specific error. These cases remain outside the scripted parity
claim; native HTTP/HTTPS tests cover the characterized retry and EOF boundaries.
The unsent retry uses a real Hyper cancellation error in a targeted test because
that scheduling race cannot be induced deterministically through a socket.

Inventory detail requests start together and retain indexed user order.
A provisional 64-request cap was rejected: the Go reference reached a fixture
barrier requiring 65 simultaneous detail requests, while the capped Rust build
hit the barrier timeout. The `p5-user-list-wide-gather` regression preserves this
boundary. This follows the plan's instruction to retain the characterized
baseline when a concurrency experiment changes observable behavior. No new
concurrency limit or command-wide timeout is introduced.

Map-derived role names use their first insertion order in the response in Rust;
Go's map iteration is random. The comparator checks these specific sequences as
multisets with multiplicity, preserving user order and per-role action order.
User output validation occurs after all detail reads; role output validation
occurs after the role-name read and before details. Text renders partial results
before returning an aggregate failure. JSON publishes no success document when
any detail fails. Aggregates retain the Go `COMMAND_FAILED` classification.
Role actions preserve missing/null versus empty arrays, both current `dataset`
and legacy `stream` resource fields, and assignment multiplicity.

The command adapter writes output before returning its final outcome. Ordinary
text write errors follow Go's ignored `fmt.Print` errors, while JSON encoding
errors fail the command. Broken pipes retain process-level SIGPIPE behavior.

## Validation and measurement

The independent contract and corpus live in `harness/contracts/phase5.md` and
`harness/phase5.py`. Tests use disposable loopback fixtures, synthetic credentials,
isolated HOME/config directories, request ledgers, and reset administration state.
No production mutation is required or performed. Interactive selectors remain
explicit boundary checks outside passing execution-parity counts.

The measurement protocol freezes Go `07af390` built with Go 1.25.4, prior Rust
from `411e56f`, and the release candidate using Rust 1.96.0 with identical
`v0.0.0-harness` public metadata. Compilation and fixture reset are excluded from
timings. Five warmups precede at least 30 fresh-process samples per binary, with
seeded randomized sequential order. Native process CPU/RSS and block-operation
counters remain distinct from output/network bytes. Unavailable metrics remain
unavailable; descriptive p95 and pointwise bootstrap intervals are exploratory.

The administration resource runner uses 52 workloads. Its Go A/A correctness
stage has separate repetition counts from the measured comparison. Each sample
gets a fresh HOME/config; by default a control endpoint resets the fixture only
after preceding handlers finish, then verifies initial state and an empty
ledger. `--fixture-reset process` provides fresh fixture processes instead.
TCP_NODELAY prevents artificial delayed-ACK stalls between fixture headers and
bodies. Reset, fixture startup and artifact collection remain outside timing.

Predeclared shared-command investigation budgets are increases exceeding both
10% and 1 ms for median elapsed/CPU time, or both 20% and 2 MiB for median RSS.
New administration commands report Go comparisons without a universal speed
threshold. Baseline identities and protocol are retained in
[the published manifest](evidence/phase5/candidate-final-manifest.json).

This phase does not establish live-server deletion/assignment semantics,
cross-platform release readiness, or successful live SQL/metrics integration.
Fixture role deletion behavior is a test assumption, not a server guarantee.

## Final release results

The final standard binary is SHA-256
`ec9765a4dbd4ad48fd758965cba8aaba50a525d6c7eded89b8c84c4cfcc31b0f`.
The Cloud binary and all baselines are recorded in the manifest. Nine Opus 5.5
delegated tasks implemented and reviewed the changes. Final validation passed:

- 251 Rust unit/integration tests; formatting and Clippy with warnings denied.
- 278 Python tests, plus the final 72-test administration rerun after tightening
  JSON-format, raw-error and refresh-header expectations.
- All 3,250 release compatibility cases: 2,018 Phase 1, 219 Phase 2, 675 Phase 3,
  153 Phase 4, and 185 Phase 5. All 32 deferred-boundary checks also passed.
- The separate real 60-second administration body deadline, against the final
  release binary and unchanged Go reference.
- All 4,470 primary measured samples across 52 administration and 15 shared
  workloads. Each binary/workload had five excluded warmups and 30 measured
  repetitions. The repeat contention investigation adds 60 measured samples.

[Raw reports, samples, identities and reproduction commands](evidence/phase5/README.md)
are committed with this change. The Go A/A controls passed before the candidate
comparisons. Prior Rust's 45 standard-build administration scope guards also
passed: those unsupported commands made no requests or state/config changes.
They are excluded from administration timing comparisons.

The host was an Intel i9-13900HK, Linux x86-64, 20 logical CPUs, with the
`powersave` governor. Other host services remained active; this was not a
dedicated benchmarking machine. Our builds, compatibility fixtures and profiling
were stopped during primary timing. All reported latency includes fresh process
startup and loopback service work; CPU and RSS describe the CLI process only.

Selected administration medians (all samples valid):

| Workload | Go / Rust elapsed ms | Rust/Go elapsed ratio, 95% interval | Go / Rust CPU ms | Go / Rust peak RSS MiB |
|---|---:|---:|---:|---:|
| Create logs dataset | 29.691 / 2.257 | 0.076× [0.068, 0.083] | 38.772 / 1.727 | 52.62 / 6.21 |
| Create user, small inventory | 29.909 / 3.051 | 0.102× [0.093, 0.108] | 38.013 / 2.038 | 52.73 / 6.11 |
| List 1,000 users, JSON | 521.044 / 512.285 | 0.983× [0.961, 0.997] | 448.967 / 282.108 | 103.33 / 35.83 |
| List 250 roles, JSON | 133.681 / 111.021 | 0.830× [0.816, 0.847] | 128.077 / 39.328 | 65.00 / 14.15 |
| Create user, delayed body | 180.191 / 153.277 | 0.851× [0.844, 0.854] | 38.026 / 2.177 | 53.06 / 6.10 |
| 1,000 users, refresh contention | 813.536 / 937.614 | 1.153× [1.031, 1.229] | 545.597 / 466.106 | 104.98 / 36.89 |

Intervals are pointwise exploratory bootstrap intervals, not simultaneous
confidence guarantees. The full reports retain variation, descriptive tail
percentiles, absolute deltas, resource counters, byte counts and timing
milestones. Parent-observed first stdout is not a measured first useful result.
Heap allocations, FD-specific writes and actual network traffic were not
measured; logical fixture bytes and native block-I/O counters are separate.

The 15 shared workloads crossed none of the predeclared investigation budgets
versus prior Rust. Median elapsed increases ranged from 0.013 to 0.783 ms and
CPU increases from 0.012 to 0.369 ms. Some relative increases exceed 10% on very
short commands; they remain below the required 1 ms absolute threshold. Median
peak RSS decreased by 0.34–0.91 MiB. These are measured changes, not a claim of
zero overhead. The release binary grew from 7,423,112 to 7,617,656 bytes
(+194,544 bytes, 2.62%).

## Confirmed refresh-contention tradeoff

The 1,000-user simultaneous-expiration workload is slower in Rust. The primary
run measured 937.614 versus 813.536 ms, a 1.153× ratio [1.031, 1.229]. A separate
30-sample confirmation measured 900.917 versus 786.758 ms, a 1.145× ratio
[1.093, 1.169], with all outputs, requests, token persistence and state checks
passing. Rust used 17.3% less CPU and 65.1% less peak RSS in that confirmation.

The [ledger investigation](evidence/phase5/contention-analysis.json) places the
delay before token refresh: confirmation median token-request arrival was
375.585 ms for Rust versus 84.533 ms for Go. Final-response-to-exit medians were
only 3.429 versus 6.609 ms. Rust sent all 1,000 stale detail requests in every
sample; Go sometimes refreshed before all detail requests started. Restricting
the diagnostic comparison to the full 2,002-request samples still leaves the
slowdown: 900.917 ms for Rust versus 809.760 ms for Go. All samples remain in
the primary reports; that subset is only an investigation.

This evidence localizes the difference to scheduling the fan-out and refresh,
not final rendering. It does not prove a specific CPU or socket bottleneck;
no profiler attribution is claimed. We retain full fan-out as an explicit
latency/resource tradeoff because the tested 64-request cap breaks the server
barrier contract. Correct request membership, output order, single refresh,
per-request deadlines and mutation safety take priority. Future work can
investigate how ready requests are scheduled without imposing a new cap. The
[confirmation report](evidence/phase5/contention-confirm.md) preserves this
regression rather than excluding it from the result.
