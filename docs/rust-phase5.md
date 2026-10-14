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
[the published manifest](evidence/phase5/candidate-final-manifest.json). The
review-fix run's binaries, budgets and acceptance rules are in
[its own manifest](evidence/phase5/review-fixes-manifest.json).

This phase does not establish live-server deletion/assignment semantics,
cross-platform release readiness, or successful live SQL/metrics integration.
Fixture role deletion behavior is a test assumption, not a server guarantee.

## Review fixes

Review of the `7cda2ad` release candidate found three defects. Commits
`4e301a3` and `4fcbd51` fix them, and the PR head is now `4fcbd51`. The fixed
standard binary is SHA-256
`9bd38b9db4f4f8e76cd43384cb024dddd4d122c6bc109d29a8afa483ae0e2c6d`
(7,617,800 bytes, +144 bytes over `7cda2ad`). The Cloud build is in
[the review-fix manifest](evidence/phase5/review-fixes-manifest.json).

- **Linear error-body trimming.** Rejected responses are trimmed of Unicode
  whitespace before they become the error message, like Go's `TrimSpace`. The
  old `trim_bytes` revalidated the entire remaining body for each rune it
  removed, so its cost was quadratic. Against a 400 response body padded with
  leading spaces, `user remove` took 3.06 s with 512 KiB of padding. With 1 MiB
  it hit the probe's 8 s timeout, while Go took about 35 ms. Each step now
  decodes at most four bytes. On the same probe, the fix took 2.7–11.0 ms from
  64 KiB to 1 MiB and Go took 26.6–39.4 ms. Exit status and the captured
  100-byte error prefix matched Go. Invalid UTF-8 still stops trimming on its side.
- **PromQL run error wrapper.** When a `promql run` error had its raw bytes
  cached, its text output lost Go's `request failed: ` prefix.
  `CliError::prefix_message` now prefixes both the message and the raw bytes.
  JSON output follows Go: typed HTTP failures keep the original public message,
  and untyped envelope errors include the wrapper. Six new Phase 4 cases cover
  typed HTTP failures, invalid response bytes and untyped envelopes, each in
  text and JSON. The Phase 4 gate is now 159 cases.
- **Open-file limit.** Inventory detail reads hold one connection each across
  the full fan-out. With the common inherited soft `RLIMIT_NOFILE` of 1024,
  `7cda2ad` failed 482 of 1,500 user detail reads with EMFILE and exited 1. Go
  succeeds there because its runtime raises the soft limit at startup
  (`syscall/rlimit.go`). `main` now does the same before any other work. If the
  hard limit is nonzero and the soft limit is below hard − 1, it raises the soft
  limit to hard − 1. On Apple targets it also clamps to `kern.maxfilesperproc`,
  as `rlimit_darwin.go` does. Errors are ignored, as in Go. One difference
  remains: Rust only raises the limit, but Go can lower it on Apple when
  `kern.maxfilesperproc` is below the inherited soft limit. Go restores the
  original limit only in processes it executes. Rust `pb` starts no child
  processes, so it keeps nothing to restore. Any future subprocess, such as a
  browser opener or `sw_vers`, must restore the limit itself. The 1,500-user
  probe now gives output byte-identical to Go. `tests/fd_limit.rs` runs the CLI
  with a soft limit of 64 against 200 detail reads that must all be open at
  once behind a strict barrier, and reads the raised limit from `/proc`.

The Apple `kern.maxfilesperproc` branch has not been compiled or run. Only the
Linux x86-64 target is installed, so the `sysctlbyname` call was checked by hand
against the locked `libc` 0.2.190 source. `tests/fd_limit.rs` has not run on
macOS either. Startup cost was measured with `pb --version`, 500 interleaved
runs per binary and condition. When the inherited soft limit already equals
the hard limit, only `getrlimit` runs. The median rose 0.031 ms (+2.4%, ratio
interval [1.008, 1.041]) over `7cda2ad`. With soft 1024, where `setrlimit` also
runs, it rose 0.010 ms (+0.8%, [0.994, 1.023]). Both compare complete release
builds, not the system calls alone.

### Review-fix validation

A delegated Codex (gpt-6.1-sol) task validated `4fcbd51`. It used the same Go
reference, toolchains, `v0.0.0-harness` metadata and host as the pre-fix run:

| Check | `7cda2ad` (pre-fix) | `4fcbd51` (review fixes) |
|---|---:|---:|
| Rust unit/integration tests; fmt and Clippy `-D warnings` | 251, pass | 256, pass |
| Python harness tests | 278 | 278 |
| Phase 1 / 2 / 3 / 4 / 5 cases | 2,018 / 219 / 675 / 153 / 185 | 2,018 / 219 / 675 / 159 / 185 |
| Release compatibility cases | 3,250 | 3,256 |
| Deferred-boundary guards | 32/32 | 32/32 |
| Real 60-second administration deadline | pass | pass |
| Primary measured samples (shared + administration) | 4,470 | 1,350 + 4,680 |

All cases, guards and samples passed or were valid. The first Python run picked
up an unpinned Go shim on `PATH`. The rerun with Go 1.25.4 had one skip, the
Phase 3 fixture-generator parity test, which passed in a targeted rerun with
`PB_PHASE3_FIXTURE`. No test was edited.

Both corpora measured Go, frozen `7cda2ad` and the fix in the same
interleaved sessions, with 5 excluded warmups and 30 samples each. Against
`7cda2ad`, no shared workload crossed a manifest budget. Shared median elapsed
changes ranged from −0.219 to +1.267 ms, CPU from −0.218 to +1.142 ms, and peak
RSS from −0.15 to +0.06 MiB. Administration elapsed changes ranged from −7.349
to +7.457 ms, and RSS from −0.65 to +0.06 MiB. The largest relative elapsed
increases were +14.3% on a 2 ms shared command and +8.0% on an administration
command.

A stricter 5% rule was also applied. Thirty workload/metric pairs (5 shared and
15 administration workloads) had a primary median increase above 5%. Each was
rerun alone in a fresh interleaved session. A pair would fail if its
confirmation median stayed above +5% and its whole ratio interval lay above
1.05. None did, so no regression above 5% is confirmed. Eight confirmation
point estimates stayed above +5%:

| Workload | Metric | Frozen / fix median ms | Change | Ratio interval |
|---|---|---:|---:|---:|
| `p3-bench-rows-1-json` | CPU | 1.631 / 1.719 | +5.4% | [0.995, 1.127] |
| `p5-user-set-role-large` | CPU | 5.576 / 5.887 | +5.6% | [0.972, 1.122] |
| `p5-refresh-user-list-small-json` | elapsed | 5.932 / 6.237 | +5.1% | [1.007, 1.119] |
| `p5-refresh-user-list-small-json` | CPU | 2.888 / 3.048 | +5.5% | [1.004, 1.170] |
| `p5-refresh-dataset-add` | elapsed | 2.873 / 3.108 | +8.2% | [1.005, 1.167] |
| `p5-refresh-dataset-add` | CPU | 1.962 / 2.091 | +6.6% | [0.989, 1.139] |
| `p5-refresh-rejected-no-commit` | elapsed | 2.309 / 2.584 | +11.9% | [1.010, 1.184] |
| `p5-refresh-rejected-no-commit` | CPU | 1.693 / 1.891 | +11.7% | [1.040, 1.213] |

Every one of these intervals includes 1.05, so none establishes a regression
above 5%. Some lower bounds exceed 1.0, especially in the small OAuth-refresh
workloads, so a small positive difference may be real. It amounts to 0.13–0.31
ms per invocation. The fixes do not change the refresh logic itself, so these
workloads got a separate A/B run, described in the
[refresh follow-up](#refresh-workload-ab-follow-up) below.

The Go comparisons are unchanged in character. The large refresh-contention
workload measured 835.556 ms for the fix, 840.456 ms for `7cda2ad` and
751.755 ms for Go (fix/Go 1.112× [1.083, 1.156]), so the
[confirmed tradeoff](#confirmed-refresh-contention-tradeoff) below still holds.
Error-trim and 1,500-user FD probes were rerun after timing, in a quiet window.
Reports, analyses, startup samples, probes and logs are listed in
[the evidence index](evidence/phase5/README.md). After the refresh follow-up,
the next two sections are the pre-fix release evidence for `7cda2ad` and are
kept as published.

#### Refresh workload A/B follow-up

A second delegated Codex (gpt-6.1-sol) task compared `7cda2ad` and the fix on
the three refresh workloads, large set-role and one shared SQL control. Two
independent sessions, each with 100 samples per binary, reproduced the refresh
difference. The fix took about 0.09–0.20 ms more elapsed time and 0.11–0.16 ms
more CPU, with 0.5–0.6 MiB lower peak RSS and 20–23 more minor faults. Two
controls tie this to the cached state of each executable file, not to the
source:

- A local rebuild of `7cda2ad` was byte-identical to the stored binary (same
  SHA-256) but was a new file. Compared with the stored file, it showed the
  same pattern: refresh-rejection CPU +9.7% (interval [+4.2%, +16.5%]), with
  similarly lower RSS.
- Next, both stored executables were given the same page-cache treatment
  (`POSIX_FADV_DONTNEED`, then a full sequential read). A 200-sample session
  then measured refresh CPU changes of −4.0%, −0.5% and −1.5%. Every interval
  includes 1.0 and every upper bound is below 1.05. The RSS gap fell to about
  0–0.2%.

`/proc/PID/smaps` attributes the RSS difference to mapped executable pages.
User-mode instruction counts are unchanged on all four administration
workloads. On these paths the fix adds one system call, a read of
`RLIMIT_NOFILE`. It makes no `setrlimit` call, because in these runs the
inherited soft limit already equals the hard limit.

One small residual remains. In three pinned-core `perf stat` sessions, large
set-role used 1.7–2.3% more user cycles for the same instruction count. Two of
the three intervals exclude zero. The residual stayed when the startup FD call
was removed and when the old trim was restored; successful set-role runs
neither the trim nor the prefix code. The likely cause is code layout: `.text`
starts 64 bytes later in the fix. The specific microarchitectural cause was
not identified. In native timing after cache treatment, the set-role CPU
change was +0.076 ms (interval [−0.136, +0.298] ms). The elapsed-time interval
still reaches +5.8%, so equality is not proven.

All three fixes stay, and no source performance change is warranted. This
does not prove equivalence. The intervals are pointwise bootstrap intervals,
measured on a busy hybrid-core host (load average about 6–8) against loopback
fixtures with warm caches. A strict sub-2% cycle budget would need separate
layout profiling. In this run, five process warmups did not equalize
executable cache state. Future sub-millisecond startup comparisons should
normalize it before timing, or include byte-identical new-file controls.
[The full A/B report](evidence/phase5/review-fixes-refresh-ab.md) has the
tables and methods. Its per-sample data and analyses are in
`review-fixes-refresh-ab.json.gz`.

## Pre-fix release results (`7cda2ad`)

These results describe the original release candidate, before the review fixes.
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
