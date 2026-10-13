# Phase 3: noninteractive SQL and streaming output

The Rust candidate now implements `pb sql run QUERY` with text and JSON output,
including `--from`, `--to`, `--interactive=false`, and absent or exactly empty
`--save-as`. The final SQL fixture gate passes; performance and live-server limits are
recorded below.
The Go CLI remains the complete implementation during the port.

## Scope and behavior

Ordinary runs require nonblank SQL, validate the output format, then reject a
nonempty save name before making a request. Interactive SQL, saved-query
commands, PromQL, administration, mutations, Flight, and the TUI remain deferred.
A whitespace-only save name is nonempty. Existing phase-one and phase-two
commands retain their own validation and error policies.

The SQL service quotes table/field identifiers and inserts the default limit in
the order established by Go. An explicit top-level limit is preserved, including
limits above 500. Time strings are forwarded unchanged; only exactly empty values
become `1m` and `now`. A query sends one `POST /api/v1/query` request without field
discovery. Authentication, redirects, proxy/TLS handling, gzip, and the single
OAuth refresh/replay policy use the existing API service. Only HTTP 200 succeeds.

Text output preserves response bytes, including invalid UTF-8, and appends LF
when needed. Empty/ASCII-whitespace bodies and genuinely empty arrays have
different human messages. JSON renders an outer array one element at a time,
with sorted object keys, last duplicate values, float64 rounding, Go escaping,
and matching indentation. A whitespace-only body still produces the human
message in JSON mode. Malformed later elements can leave partial stdout.

Non-200 previews read at most 65,537 decompressed bytes, keep at most 65,536,
trim whitespace, and append `...` when truncated. Text stderr preserves preview
bytes; structured errors omit the preview. JSON completes at the closing array
delimiter and previews complete at their cap, without waiting for an unread
HTTP tail. Text empty-array lookahead must reach EOF and observes read faults.

## Architecture and limits

One native SQL module owns invocation policy and rewriting. The API returns
response metadata plus an owned streaming body that retains the absolute
60-second request/body deadline through redirects and refresh. Buffered status
and dataset operations collect through that same streaming API.

A blocking rendering worker consumes a bounded bridge from the current-thread
async network driver. The bridge queues one chunk, the renderer keeps one current chunk, and the
producer may hold one chunk awaiting space. Delivered chunks are at most 8 KiB;
the HTTP stack can additionally retain a frame remainder. Worker completion cancels pending
reads of irrelevant trailing content. Stdout backpressure does not block the
async driver, and the network deadline is not a deadline on an OS output write.
Output outcomes own bytes on both streams and retain stdout already emitted
when an operation later fails.

JSON framing uses an incremental grammar scanner, then the existing deep-safe
decoder plus a SQL float64 conversion policy for each complete element. Invalid
syntax is detected as it arrives. Deep values are decoded, serialized, replaced,
and dropped safely, with the Go nesting limit. Memory for fixed-size rows scales
with the bridge and current element; one enormous element and long leading
whitespace/empty-array lookahead require additional memory. This is not a claim
of constant memory for arbitrary input.

The stderr spinner runs only on a terminal, starts after request construction,
and clears after response headers arrive. Process tests cover output before a
gated response finishes, JSON/preview completion before HTTP EOF, text empty
arrays waiting for EOF, invalid UTF-8, real SIGPIPE, SIGINT/SIGTERM during header
and body waits, inherited nonblocking stdout, Linux `/dev/full`, and PTY spinner
start/cleanup. The inherited nonblocking-stdout regression passed against both
Go and Rust. JSON ignores ordinary writer errors as Go does; text copy errors
remain failures. Special empty-response messages ignore ordinary write errors.
Broken output pipes still
terminate by SIGPIPE.

## Known differences and unverified protocols

- SIGQUIT is runtime-specific: Go's runtime diagnostic output and Rust's signal
  behavior do not match. SIGINT/SIGTERM parity does not imply SIGQUIT parity.
- Hyper does not preserve non-ASCII or explicitly empty HTTP/1 reason phrases;
  messages can use a canonical reason instead. Ordinary ASCII custom reasons
  are preserved.
- A malformed HTTP chunk terminator can produce different partial output and
  error timing because the HTTP stacks validate framing at different points.
- Premature EOF during HTTP response headers has ambiguous diagnostics: Hyper
  uses `connection closed before message completed` for cases Go distinguishes
  as `EOF`, `malformed HTTP response`, or
  `transport connection broken: unexpected EOF`. These message differences
  remain unresolved; the command still fails before rendering a body. Malformed
  status-code diagnostics likewise omit the raw status token available to Go.
- Connection resets preserve `CONNECTION_FAILED` and retryability, but their
  OS/transport messages differ: Go includes socket addresses and Rust retains
  the underlying OS error. Exact error-text parity is not claimed for these
  cases; the corpus records them in its explicit divergence tier.
- The inherited non-UTF-8 argv limitation remains: Go accepts byte-valued
  arguments, while Rust rejects them explicitly. Response-body bytes are a
  separate boundary and retain their documented behavior.
- No HTTP/2 SQL fixture or successful live SQL validation has been completed.
  On 2026-10-13, a replacement API key made `status` succeed in both Go and Rust;
  the server reports `v3.2.5`. Dataset listing still returns HTTP 500 with
  `Error: No authentication method supplied`. SQL still returns HTTP 502 with
  an empty response body. Sequential text retries confirm the same failures;
  both binaries have identical stdout/stderr in these probes. The original key
  had failed status authentication and initially returned SQL HTTP 403.
  Status connectivity is now validated, but live dataset/query validation remains
  blocked. These probes are not a controlled performance measurement.

These limits are separate from the explicitly deferred CLI commands. The
candidate remains a partial CLI rather than a replacement release.

## Verification and measurement

The acceptance gates retain the offline and phase-two corpora and add a fixed
SQL differential corpus with independently checked Go expectations. Synthetic
credentials, isolated configuration, and loopback fixtures keep account state
out of tests. Full 60-second deadline cases need their dedicated opt-in gate;
ordinary short case timeouts cannot establish that deadline.

`make -f harness/Makefile phase3-check` runs the default fixture gate;
`phase3-deadline-check` separately opts into the real deadline tier. The default
report lists 13 known divergence cases separately (HTTP/OS diagnostics and
non-UTF-8 argv); none count as a pass. CI uses synthetic fixtures and archives the default gate
and SQL measurements; its manual deadline input is off by default.

Measurements use prebuilt, hashed binaries and fresh CLI processes, sequential
seeded ordering, five excluded warmups, and 30 measured samples per case/binary.
Keep Go, saved phase-two Rust for common commands, and the last accepted
phase-three Rust build as separate references. Record first complete SQL element
separately from first stdout byte: `[` is not a result. Size workloads also check
element count/order and include paced chunks, a slow reader, a single large item,
and long leading whitespace. First-element timing is unavailable when explicitly
suppressed for large-item and leading-whitespace measurements; validation still runs.
Older checkpoints with different measurement-runner, fixture, or meter identities
cannot establish a performance trend. Use comparisons within the same measured
run when historical metadata differs.

Local source validation passes 144 Rust tests, including 12 process tests
exercised against both binaries; 127 Python harness tests; Go fixture tests;
formatting and Clippy. Final compatibility results use the release binaries:

| Gate | Result |
| --- | --- |
| Offline CLI | 2,018/2,018; 50/50 deferred guards |
| Profiles/auth/status/dataset reads | 219/219; 6/6 configuration boundaries |
| Noninteractive SQL | 675/675; 11/11 deferred-command guards; zero skips |
| Real 60-second deadlines | 9/9; two Go runs and one Rust run per case |
| Known SQL divergences | 13 separately listed, excluded from passing parity counts |

The deadline fixtures cover stalled headers/body, preview reads, empty-array
lookahead, paced chunks, redirects and refresh. Two cases first failed the
harness because repeated Go runs varied the timeout diagnostic suffix. Their
exact observed text forms now have narrow comparison rules, independently
reviewed; both real cases were rerun successfully. Timing, partial output,
request and error-context checks remain enforced. See
[the deadline contract](../harness/contracts/phase3.md#the-60-second-deadline-tier).
The frozen manifest covers the default and known-divergence tiers; the real
clock-driven deadline tier is checked against live Go runs and source pins.

A separate 65-second blocked-reader probe produced identical timeout errors in
Go and Rust with valid partial output. The retained prefixes differed by 12 KiB,
consistent with different read-ahead behavior; exact prefix-length equality is
not claimed. Independent Sol harness review and Opus source reviews approved
the scoped change. CI wiring and local tests do not establish a remotely
executed CI run or a successful live server result.

Raw local acceptance artifacts are under `harness/results/phase3-final/`:
`offline-gate-final`, `phase2-gate-final`, `sql-gate`, and
`deadline-gates-final` plus the two corrected-comparator reruns in
`deadline-two-recheck`. Initial failures are retained. Results directories are
ignored by Git; repository documentation is the portable summary.

## Final performance results

These are the original phase-three acceptance measurements. The later
[SQL optimization report](rust-phase3-optimizations.md) records subsequent changes
and comparisons against the preserved baseline.

All **4,230 measured invocations** passed compatibility and validation across
57 workload definitions in five batches. There were also 705 excluded warmup
invocations and 34 successful workload/gzip smoke samples. Each measured pair
used 30 samples per binary, five warmups and seed 3003, with binaries interleaved
in seeded order. Common offline and online commands also include the saved
phase-two Rust executable. No agent builds or tests overlapped this measurement
window. This was a shared Linux x86-64 host (i9-13900HK, 20 logical CPUs,
powersave governor, warm OS caches), so results are advisory local measurements.
The figures below are medians; CPU is child user plus system CPU, and RSS is
peak child resident memory. Network rows use HTTP loopback fixtures.

| Workload | Wall ms, Go / Rust | CPU ms, Go / Rust | Peak RSS MiB, Go / Rust | First complete element ms, Go / Rust |
| --- | ---: | ---: | ---: | ---: |
| Version/startup | 28.68 / 1.30 | 37.22 / 1.22 | 47.28 / 4.76 | N/A / N/A |
| Status, loopback | 29.60 / 2.10 | 38.17 / 1.79 | 48.64 / 5.98 | N/A / N/A |
| SQL 1 row, JSON | 27.79 / 2.13 | 36.48 / 1.84 | 48.87 / 6.03 | 26.16 / 1.88 |
| SQL 10,000 rows, JSON | 91.31 / 75.36 | 116.52 / 76.19 | 63.27 / 6.09 | 26.00 / 2.34 |
| SQL 100,000 rows, JSON | 586.75 / 604.07 | 679.97 / 607.68 | 65.31 / 6.34 | 32.88 / 7.66 |
| SQL 100,000 rows, text | 49.92 / 25.66 | 45.48 / 15.81 | 53.55 / 6.62 | 33.67 / 9.11 |
| SQL 10,000 rows, gzip JSON | 106.76 / 85.98 | 135.94 / 87.35 | 63.47 / 6.09 | 24.78 / 1.90 |
| SQL 10,000 rows, slow reader | 295.76 / 270.05 | 112.92 / 67.77 | 62.96 / 6.33 | 31.24 / 12.47 |
| Single 8 MiB JSON item | 142.13 / 176.60 | 164.43 / 173.11 | 126.56 / 53.80 | N/A / N/A |
| 1 MiB leading whitespace | 39.84 / 21.00 | 52.15 / 20.45 | 55.94 / 6.60 | N/A / N/A |

Rust reduces startup cost and usually uses less CPU and memory in these fixtures,
but it is not uniformly faster. At 100,000 JSON rows its median elapsed time is
about 3% above Go, despite lower CPU and RSS. A single 8 MiB element is about
24% slower, using 53.80 MiB peak RSS versus Go's 126.56 MiB. These are concrete
future profiling targets. First-element timing is deliberately unavailable for
the large-element and leading-whitespace cases so the timing collector does not
throttle those inputs; full output validation still runs after process exit.
Slow-reader measurements include the specified consumer delay. These are fresh
process measurements with warm caches, not cold-cache starts or pure server
execution times. A collector observation timestamp is not the exact write time.

Common-command comparisons also show small elapsed-time increases versus saved
phase-two Rust: `version` 1.27 to 1.30 ms; Basic status JSON 1.85 to 2.10 ms;
Cloud-key status JSON 1.69 to 2.13 ms (about 0.44 ms / 26%); root command-tree
output 1.65 to 1.86 ms. These same-run comparisons remain visible; historical
checkpoints made with different collectors are not treated as a performance
trend. The current release executable is 7,125,000 bytes, versus Go's
71,807,160 and phase-two Rust's 6,971,728 bytes.

The native collector recorded CPU user/system time, peak RSS, page faults,
context switches and block input/output operation counts in every measured
sample. All 4,230 samples reported zero block operations in these warm-cache,
pipe-output fixtures. That does **not** mean no config, pipe or network I/O
occurred. Logical stdout/stderr and request payload bytes are recorded; declared
fixture response sizes are available where specified. No NIC/TLS wire-byte
attribution, allocation profile or successful live-server performance is claimed.
Missing metrics remain unavailable instead of being filled with zero.

### Reproduction and evidence

Build identities are pinned to Go 1.25.4 and Rust 1.96.0. Go uses
`CGO_ENABLED=0`, `-trimpath -buildvcs=false`, `-tags kqueue`, and the Makefile's
version/link flags. Suppressing VCS stamping makes the unchanged Go reference
reproducible across Rust commits. The standard Rust build uses
`PB_VERSION=v0.0.0-harness`, an empty Cloud orchestrator token, and
`cargo build --locked --release`. Separate Cloud test builds use the public
fixture token only.

| Artifact | SHA-256 |
| --- | --- |
| Go reference | `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314` |
| Rust phase three | `3c580f6fb90f6e9ce1c2de9145a9180bfaca3081338e4f5b6135c0efa30c8141` |
| Saved Rust phase two | `ac84e6d6d1291457826998cc92813732e411ad7c89740660f68f1c1abfe52db9` |

Local `harness/results/phase3-final/measure-{offline,online,sql,workloads,gzip}`
directories contain the JSON/Markdown reports, raw samples, binary/collector/
fixture hashes, request traces and output artifacts. `provenance-final.json`
archives the source delta against `f8fd830` and explicit build notes. Reports
include latency distributions and comparison intervals; the small local sample
set is not a production SLO. Existing Make targets reproduce the gates and
workload exports; use `REPETITIONS=30 WARMUP=5` for this sample count. The raw
results are local ignored artifacts, while this document records the portable
findings. No commit, push or remote CI run was performed for phase three.
