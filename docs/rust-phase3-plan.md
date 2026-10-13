# Phase 3 plan: noninteractive SQL and streaming measurements

Status: implementation and independent source/harness reviews complete; fixture acceptance and performance measurements complete. See [phase-three results](rust-phase3.md) for the final evidence and limitations. Branch
`phase3/sql-query-plan` starts at `f8fd830`, the
committed phases-one-and-two implementation. The final SQL fixture gate passes 675/675 cases and 11/11 deferred guards. Live status now succeeds with the replacement key in both binaries; dataset listing and SQL still fail identically, so successful live SQL validation is not claimed. The unchanged Go source at `07af390` remains
the behavioral reference. Two Sol 6.1 specialists and an Opus 5.5 architecture
review contributed to this plan; the coordinator records the synthesis in Mayfly.

## Scope

Implement `pb sql run QUERY` with text/JSON output and the existing `--from` and
`--to` options. Include `--interactive=false` and an absent or exactly empty
`--save-as`. Include the ordinary command's stderr-TTY spinner and process/pipe
behavior; this does not require the interactive table UI.

Keep `-i`, nonempty `--save-as`, saved-query commands, PromQL, administration,
mutations, Flight and the TUI deferred. Ingestion/capture/observer is a separate
feature. Nonempty includes a whitespace-only save name. Guard deferred operations
before requests; do not execute a query and then report saving as unimplemented.
These combinations are explicit partial-scope exceptions, not Go parity claims.

The user supplied a self-hosted HTTPS instance for future validation. Its API key
is not part of this document, source control, Mayfly, test fixtures or agent
prompts. Connectivity and certificate trust worked for read-only probes. A replacement key tested on 2026-10-13 makes status succeed in both binaries; the server reports v3.2.5. Dataset listing still returns HTTP 500 (`Error: No authentication method supplied`), and SELECT 1 returns HTTP 502 with an empty body. Sequential retries agree in Go and Rust. The user expects all operations to be permitted; successful dataset/query validation remains blocked. Dataset and historical time window remain unverified. These probes are not performance measurements.

## Freeze the observable contract first

The primary source is `cmd/query.go`, especially command dispatch, SQL rewrites,
`fetchData`, `readLimitedErrorPreview`, and the two streaming renderers. Use
`cmd/query_test.go`, `cmd/errors.go`, `pkg/http`, `main.go` and `harness/cases.py`
as supporting evidence. Characterize source-derived edge cases with the pinned
Go executable before treating them as measured contracts.

- Preserve parsing/help/arity, active-profile preflight and command-specific
  error precedence. Ordinary run rejects absent/blank SQL before output validation.
  Interactive mode remains a deferred branch; it must not accidentally acquire
  ordinary-run output validation. For ordinary runs, place the nonempty-save
  guard after required-SQL/output validation and before request execution.
- Forward time strings unchanged. Only exactly empty values become `1m`/`now`.
  Do not introduce date parsing, UTC normalization or a clock dependency here.
  Fixed RFC3339 ranges are a test methodology, not a new CLI restriction.
- Apply table quoting, field quoting and default-limit insertion in the existing
  order. Preserve explicit top-level limits, including limits above 500. Cover
  comments, quoted identifiers/literals, mixed-case/dotted fields, joins,
  subqueries, semicolons and malformed SQL quirks. Use a small tested lexical
  boundary; a general SQL AST would risk rewriting accepted inputs differently.
- Send `POST` to the profile base plus `api/v1/query`, with `query`, `startTime`
  and `endTime`. No `fields=true` or additional discovery requests. Reuse existing
  auth, redirects, proxy, TLS, gzip and one-refresh/replay policy. Only status 200
  succeeds. Preserve the 60-second network/body deadline across redirects and
  refresh; do not reset it for each chunk or introduce general retries.
  Pin request-body bytes as well as decoded fields, including Go's HTML and
  Unicode line-separator escaping. Keep request-construction errors distinct
  from request-execution errors.
- Text output is raw bytes, including invalid UTF-8. Append LF only when needed.
  Empty/ASCII-whitespace bodies and genuinely empty arrays have distinct human
  messages. An empty array with non-whitespace trailing content is raw text.
- JSON consumes an outer array one element at a time. Preserve sorted object
  keys, duplicate handling, float64 conversion/rounding, HTML and Unicode line
  separator escaping, exact indentation and trailing-content acceptance. Empty
  response bodies still produce the human message even with JSON selected.
  Malformed later elements/body errors can leave partial stdout; do not buffer
  the whole result to make errors appear atomic. SQL decode errors use the SQL
  command's error classification, not the dataset decoder's policy.
- Non-200 previews read at most 65,537 decompressed bytes, keep 65,536, trim and
  append `...` if truncated. Preserve status/reason and distinct error contexts.
  Structured errors exclude the response-body preview. Keep stdout/stderr and
  exit/signal behavior separate. Text previews can contain invalid UTF-8 or a
  multibyte character cut at the limit; preserve stderr bytes too, without lossy
  conversion through a string-only error message.

## Native Rust structure

Continue with one package and concrete modules. Introduce a small `sql` service
with owned invocation/request types, pure rewrite functions, and separate text
and JSON renderers. Keep compatibility quirks at these boundaries rather than
copying Go's command globals or package layout. No Go execution or forwarding.

The first enabling change is a streaming response operation in `src/api.rs`.
`src/transport.rs` already exposes chunks, but the authenticated API currently
buffers the final response. Return response metadata plus an owned body reader
that retains the absolute deadline and error context. Share the existing
auth/redirect/refresh implementation; retain buffered convenience operations for
status/datasets. A streaming SQL request must not bypass credential rotation or
lose the deadline after headers arrive. A replayable small request body is
separate from the potentially large response body.
Separate request preparation from sending to retain those error contexts and the
spinner's start boundary; buffered operations can collect the same body reader.

The second enabling change is a byte-oriented execution/output boundary.
`Outcome.stdout: String` followed by writes in `main` cannot represent raw bytes,
partial output or first-result latency. Introduce an explicit output sink for
streaming commands while retaining convenient captured output for offline tests.
Command failure metadata must coexist with stdout already emitted. Avoid a
repository-wide rewrite of stable command rendering.
Captured outcomes must also support bytes on both streams. Characterize flushing
and non-EPIPE write failures independently for text and JSON; Go ignores some JSON
writer errors. Include Linux `/dev/full` probes before choosing a uniform sink
error policy. Any batching must flush before waiting for more network input and
preserve the characterized partial-output/error behavior.

Keep blocking decoding/stdout work off the sole async network driver. The preferred
prototype uses one owned blocking render worker connected to the network reader
by a bounded, demand-driven byte bridge. Bound bytes rather than merely message
count, split oversized frames, and retain explicit worker/body ownership. Test
backpressure, early drop and shutdown before fixing this internal design. Do not
add unbounded queues or a general event framework. Slow stdout must not starve
TLS/HTTP progress; a network deadline is not a new deadline on a blocked OS write.
The renderer owns completion: JSON can succeed immediately after the closing
array delimiter and previews can stop after their bounded read. Drop/cancel the
unread body without awaiting a stalled tail or replacing success with a producer
error from unread data. Text empty-array lookahead instead waits for EOF and must
observe a fault encountered there. Add causal early-completion tests for all three.

Use a dedicated SQL JSON policy rather than the phase-two number-preserving
decoder unchanged. Reuse proven float formatting/escaping where possible; decode
and format one array element at a time with depth/drop safety. The target for
fixed-size rows is memory proportional to a bounded bridge plus the current
element, not the full result. One enormous element and Go's leading-whitespace /
empty-array lookahead are explicit exceptions requiring measurement. Do not claim
constant memory, cap accepted input, or silently alter output to resolve them.
The incremental decoder must detect an invalid byte without waiting for a later
delimiter or EOF. Pin separator errors, EOF versus unexpected EOF, invalid
UTF-8/surrogates, depth limits and float overflow/underflow against Go. A resumable
scanner is an implementation candidate; neither a bracket counter nor a default
Rust float formatter establishes parity. Reuse existing decoding infrastructure
where it passes the corpus and add the smallest necessary compatibility adapter.

Implement the small spinner without a TUI dependency: stderr TTY only, start after
request construction, clear after headers before body consumption. Preserve
SIGPIPE handling and characterize actual signal termination during header/body
waits. Do not add a new Ctrl+C error envelope.

## Implementation sequence and review gates

1. **P3.0 — Contract and baseline.** Save the phase-two release executable/hash.
   Inventory fixed SQL cases, deferred combinations and known divergences. Capture
   Go oracle output, request bodies, errors and signals using disposable fixtures.
2. **P3.1 — Streaming foundation.** Add API response streaming and a byte-capable
   output boundary. Prove inherited auth/redirect/refresh/deadline behavior, then
   rerun phase-two acceptance before building query behavior on it.
3. **P3.2 — Query service.** Implement lexical request preparation, raw time
   forwarding, text streaming, incremental compatible JSON, errors and spinner.
   Build these as small reviewable changes with boundary tests.
4. **P3.3 — Differential and performance harness.** Extend the existing harness,
   promote ordinary SQL from deferred guards, and retain zero-request guards for
   excluded combinations. Freeze the corpus before running candidate acceptance.
5. **P3.4 — Live read-only verification.** Run the small protocol below after
   fixture gates pass; record live mismatches separately from fixture failures.
6. **P3.5 — Independent review and evidence.** Sol reviews implementation and
   measurement; Opus reviews contracts/architecture. Review fixes receive focused
   regressions before final release builds, full acceptance and serial benchmarks.
   Publish actual counts, limitations and measurements to Mayfly and repository
   documentation. Phase three stays a partial CLI, not a replacement release.

Steps 1–4 are local and can proceed without a live account. Implementation was
authorized after the design reviews; these steps now guide the active work.

## Compatibility and measurement harness

Retain the 2,018 offline and 219 phase-two differential cases and existing tests.
Update only explicitly promoted deferred guards; preserve the remaining guards
and six phase-two deferred-interaction checks. Cover standard and Cloud fixture
builds even though live validation targets self-hosted API-key auth.

Add SQL cases for rewrite/validation order, all output forms and JSON element
types, precision/escaping, duplicates, malformed/truncated arrays, ignored trailing
content, invalid UTF-8 text, empty/whitespace bodies, error-preview limits, statuses,
custom reason phrases, gzip, redirects, OAuth replay, delayed headers/chunks,
body stalls, slow consumers, broken pipes and signals. Basic PTY tests assert
spinner activation/cleanup rather than an exact scheduler-dependent frame count.
Include a missing reason phrase and mode-specific output-device failures. Seeded
rewrite and valid/malformed JSON generation should supplement named regression
cases; record the seed and compare request bytes, error timing and partial output.

The existing fixtures support delayed/chunked responses but need explicit body
faults, header flush/stall boundaries and event timestamps. Compare requests and
configuration side effects as well as exact deterministic output/termination.
Normalize only named fixture URLs/paths and declared nondeterminism. Self-compare
Go to detect unstable cases before judging Rust.
Real 60-second deadline comparisons need a dedicated harness timeout above that
deadline plus cleanup margin; the existing 10-second default cannot establish
this contract. Short injected-deadline unit tests supplement that dedicated gate.

Extend measurement with **first complete SQL element**, separate from first byte:
`[` is not a result, and the existing first-record metric counts JSONL lines.
For JSON-array fixtures, use incremental element-boundary tracking; report N/A
for unsuitable raw text. Independently verify element count and expected order;
a configured row count is not evidence that those rows were emitted.
Record fixture header/first-body/final-body milestones
to prove output is observable before response completion. Validate this causally
with a gated fixture, not only a fragile wall-clock threshold.

Use small, medium and large deterministic arrays (for example 1, 500, 10,000 and
100,000 fixed-size rows), both output modes, 50 ms delayed headers, paced chunks,
one large element, long leading whitespace and a slow reader. Use explicit SQL
limits so default LIMIT 500 does not invalidate workload claims. State both wire
and rendered byte sizes. The current runner's 32 MiB output cap and phase-two
runner's 4 MiB cap are harness limits, not CLI behavior. Before larger workloads,
add bounded capture/spooling and incremental hashes or predeclare appropriate
case limits; do not benchmark capture OOM or compare truncated output as success.

Measure wall median/p95, first request, first stdout, first complete element,
CPU, peak child RSS, throughput, output bytes and executable size. Use pinned
prebuilt Go and Rust binaries; include the saved phase-two Rust binary on common
offline/online commands. SQL has no phase-two functional baseline. Run sequential,
seeded randomized binary order, independent config/server state, five excluded
warmups and at least 30 measured samples per fixture workload. Record binary
hashes, compiler/profile, host load/governor and warm-cache assumptions.
Use comparable same-host monotonic timestamps for new fixture milestones where
possible; existing launch/request wall-clock correlation can be skewed by clock
adjustments. Do not invent first-request measurements for the live server.

Hard gates are contract equality, no forbidden side effects, bounded bridge,
output-before-completion and correct failure/cancellation behavior. Timing and
memory comparisons remain advisory until thresholds are declared in advance;
flag regressions rather than inventing tolerances after results. Check RSS scaling
with fixed-size rows separately from the documented whitespace/large-item cases.
Do not claim network/server latency improvements from local CLI startup gains.

### Continuous Go/Rust performance comparisons

Performance is a deliverable at each implementation milestone, not only the final
phase-three report. Keep three explicit references: the pinned Go executable,
the saved phase-two Rust executable for already supported commands, and the last
accepted phase-three Rust executable for SQL once it exists. Archive hashes and
build settings before replacing any baseline. Every result identifies the source
revision and any uncommitted patch so it can be reproduced.

| Checkpoint | Required comparison |
| --- | --- |
| P3.0, before implementation | Re-measure Go and phase-two Rust on common offline/status/dataset cases; collect Go SQL baselines on the frozen SQL fixtures. |
| P3.1, after transport/output changes | Compare common commands against Go and saved phase-two Rust; inspect startup, CPU, RSS and I/O costs before adding SQL. |
| P3.2, as text and JSON become functional | Measure each supported SQL mode against Go, including first complete element and memory growth; explicitly mark remaining cases unsupported. Save the first compatible Rust SQL baseline. |
| P3.3 and subsequent performance-sensitive changes | Compare the affected fixed workloads against Go and the last accepted Rust build, using identical inputs. Run the common-command regression set when shared code changes. |
| P3.4, live integration | Collect a small sequential Go/Rust sample on the same stable historical queries, including client wall/first-output/CPU/RSS; record sample count and server/network conditions. No load test. |
| P3.5, final review | Run the complete compatible fixture matrix against Go and applicable Rust baselines; publish trends, regressions and unresolved limitations. |

Fixture checkpoints use the declared five warmups and at least 30 measured samples
per binary/case. Small developer probes may use fewer runs but must be labeled
exploratory, never substituted for checkpoint evidence. A new Rust build only
becomes an accepted baseline after its correctness gate and performance review;
retain older results so a sequence of small regressions is still visible. Do not
require rerunning unrelated benchmarks for documentation-only changes.

The primary perspective is the **CLI process experienced by its caller**:

| Dimension | Measurement and meaning | Current harness / planned addition |
| --- | --- | --- |
| Startup | Launch to exit for help/version; launch to first observed request for online fixtures. Includes process startup, config/auth and connection work as applicable. | Offline wall and fixture first-request timing exist. Preserve clock and observation-point labels. |
| End-to-end latency | Launch to process exit, plus first stdout/stderr, first complete SQL element and last stdout. | Wall and output timestamps exist; SQL element tracking is planned. |
| Response progress | Request/header/first-body/final-body milestones for controlled fixtures; output progress and stalls under a slow consumer. | Add fixture milestones and causal barriers. Server receipt is not client request-send time; collector observation is not an exact write timestamp. |
| CPU | Process user time, system time and total CPU; optionally CPU per row or output MiB. | Native child resource accounting exists. Keep fixture, supervisor and collector CPU outside CLI CPU. CPU divided by wall is average core usage, not a network-latency estimate. |
| Memory | Peak child RSS, page faults and RSS growth as fixed-size result sets scale; separate one-large-element/lookahead cases. | Peak RSS and minor/major faults exist. Optional sampled RSS/heap profiles are separate diagnostics and may miss short-lived peaks. |
| Logical I/O | stdout/stderr bytes, HTTP request payload bytes and compressed/decompressed response payload bytes, rows and useful throughput. | Output bytes exist; add clearly scoped HTTP payload counters. Do not label payload counts as NIC/wire bytes or include TLS framing without measuring it. |
| Storage I/O | OS-reported process read/write activity and block-I/O counters, with platform-specific units and coverage; distinguish actual storage traffic from cached/logical reads. | Add counters to the resource collector where available. Block-I/O counts are not byte counts. Missing counters are N/A, not zero. |
| Scheduling/syscalls | Voluntary/involuntary context switches; targeted read/write/socket syscall counts when diagnosing a regression. | Context switches exist. Syscall profiling is a separate fixture-only diagnostic run because tracing perturbs timing. |
| Network and size | Request/replay/connection counts where observable, fixed payload sizes, transfer throughput, executable bytes. | Request traces and executable size exist. Add connection/payload accounting; use isolated network measurement only if actual per-process wire totals are needed. |

Treat the HTTP service as part of what the user waits for, while keeping attribution
honest. Client wall time includes startup, DNS/connect/TLS where applicable, server
processing, transfer, decoding and output. Those activities can overlap. Do not
subtract CPU time from wall time and call the remainder network latency, or claim
DNS/TLS/server timing from first-request and first-output timestamps alone.
Optional phase timings need equivalent instrumentation for both implementations
and must be reported separately from minimally instrumented CLI comparisons.

Maintain three labeled environments: offline commands isolate startup/config cost;
loopback HTTP/HTTPS fixtures with fixed delays and payloads reveal client overhead
and streaming behavior; the real self-hosted endpoint measures end-to-end user
experience. Test plain versus TLS, compressed versus uncompressed, small versus
large responses, and fast versus throttled output as separate cases. Keep proxy,
TLS trust, auth mode, output destination and consumer speed identical per pair.
Ordinary invocations are fresh processes; do not present warm OS caches as reused
HTTP sessions or cold starts. Document server query-cache warmup and alternated
binary order for live runs; live p95 is not meaningful with only a few samples.

Use lightweight resource collection for the principal comparisons. Detailed
allocation, syscall or network profiling belongs in separate diagnostic runs on
synthetic data, with overhead and permissions documented. Validate any new I/O
collector against known read/write fixtures, including cached versus uncached
behavior where controllable; very short processes may require exit-time accounting
rather than polling. Report collection coverage and errors explicitly. Never use
machine-wide I/O counters to attribute unrelated background traffic to the CLI.

At each checkpoint, publish a compact Go/current-Rust/previous-Rust comparison
with absolute values, percentage deltas, sample counts and uncertainty, accompanied
by compatibility status and artifact identities. Keep raw synthetic samples in
the ignored evidence directory or CI artifacts and update the Mayfly progress log
and wiki with sanitized summaries. Report failures/timeouts separately; never
claim a speedup for incompatible, truncated or failed output. Track regressions
even when Rust remains faster than Go. Hard numerical budgets require a declared,
calibrated baseline; CI can enforce correctness and collection validity while
shared-runner performance remains advisory.

## Live self-hosted verification

Use a dedicated temporary profile and private credential entry at execution time.
Never run credential-bearing profiles through the default evidence snapshotter:
it archives config contents. Keep credentials and raw live records out of logs,
Mayfly, CI artifacts and source control; synthetic fixtures use dummy credentials.
Unset `PB_DEBUG_HTTP_HEADERS` for live checks.

First verify connectivity/TLS, server version, status and accessible dataset/schema
using the existing Go/Rust commands. Then select a small historical window and a
dataset the key can query. Prefer a minimal projection or aggregate, explicit
small LIMIT and deterministic ordering with a suitable tie-breaker. Sequence the
two CLIs against the same stable window. A timestamp alone may not uniquely order
rows; continuing ingestion/retention can invalidate equality even in a past range.
If a stable sample cannot be established, record live support/semantic evidence
without claiming byte parity.
Freeze dataset, exact SQL, explicit UTC start/end, schema/server identity and
binary identities in a credential-free manifest before comparison. Compare raw
live output only in private temporary storage or memory.

Exercise successful and empty SQL results, text/JSON output and one benign invalid
query. Use fixture tests for permission/status failures, malformed responses,
large-load tests and Cloud auth. No dataset writes, ingestion, saved filters,
administration, parallel load or stress benchmark against the real instance.
Publish only sanitized summaries, counts, server version and clearly separated
end-to-end observations. Remove the temporary profile after use. No live test has
been run as part of this plan. Remove the entire private capture/profile directory,
not just the config file, after retaining sanitized evidence.

## Review synthesis and decisions

The Sol contract and harness reviews agree with Opus on the core scope, raw time
forwarding, byte output, per-element JSON semantics and authenticated streaming.
Sol's draft cross-review added early completion without EOF, raw stderr previews,
a harness timeout above the real 60-second deadline, and private capture cleanup.
Opus added request serialization, preparation/send error boundaries, incremental
syntax-error timing, output-device failures and absent status-reason coverage.
All of these are included above; unmeasured edge cases remain oracle work in P3.0.

The coordinator resolved these proposal differences:

- Preserve rewrite/output bytes through tests; do not mandate line-by-line Go
  translation, a wholesale scanner port, or a no-new-dependencies rule. Native
  Rust internals remain the design objective, with dependencies justified by need.
- Include the small SQL spinner and basic PTY lifecycle checks now. The full TUI
  stays deferred; unit tests alone do not establish terminal behavior.
- Keep timing/RSS/size advisory. Opus's suggested 5 ms first-byte, 2 MiB RSS and
  5% size targets are not adopted as hard gates without calibrated evidence.
  The causal first-complete-element test and declared memory exceptions remain.
- Keep the date/duration parser with saved queries or PromQL when those slices
  begin. Do not implement unused future-phase machinery here.

The original design reviews were source-only. Their planning work implemented no
code, ran no builds/tests, used no live credentials and produced no measurements.
Implementation and measured evidence are tracked in the progress section below. The plan is saved locally on the new branch and published to Mayfly;
it has not been committed or pushed.

## Implementation progress

- Sol 6.1 implemented authenticated streaming responses with one absolute deadline,
  byte-capable SQL text/JSON output and request rewriting. The coordinator wired
  byte outcomes, command ordering, a bounded bridge and stderr-TTY spinner.
- The query normalizer is deliberately applied twice where Go does so. A bare
  trailing `--` proves default-limit insertion is not universally idempotent.
  Seven rewrite tests and 10,000 seeded source-function comparisons passed.
- First integrated release: all 13 pre-existing SQL smoke cases match Go; offline
  2,018/2,018 plus 50/50 remaining guards; phase two 219/219 plus 6/6 deferred
  interaction checks. Ten causal process tests pass against both Rust and Go,
  including early completion, raw bytes, SIGPIPE, output-device errors and PTY.
- The resource harness now measures first complete SQL element, validates declared
  row counts/order, reports native block-I/O operations and logical request bytes,
  and bounds capture with per-case limits. Unavailable metrics remain N/A.
- Pre-change 30-sample baselines are in `harness/results/phase3-baseline`. The first
  three-way common-command and Go/Rust SQL checkpoint is being collected under
  `harness/results/phase3-checkpoint`. These are shared-host observations.
- Expanded Opus SQL contract/harness work and independent audit are still in
  progress. Live self-hosted validation and final review remain pending.


## Phase-three completion

The implementation and independent reviews are complete. Final gates pass:
2,018 offline cases plus 50 guards; 219 phase-two cases plus six configuration
boundaries; 675 SQL cases plus 11 guards; nine real 60-second deadline cases.
All 144 Rust tests and 127 Python harness tests pass. Thirteen known SQL
mismatches remain explicit, excluded from passing parity counts.

All 4,230 final measured invocations passed output/contract validation. The
[results document](rust-phase3.md) records latency, CPU, RSS, I/O coverage, binary
identities and regressions, including the large-element and 100,000-row JSON
cases where Rust is slower, and small common-command increases versus phase two.
Use those measured cases for future optimization work; no new optimization or
feature scope is started here. Live status now succeeds with the replacement key. Dataset/query validation
remains blocked by matching HTTP 500/502 responses in Go and Rust. This is a validated noninteractive SQL slice, not a complete CLI replacement.
Work remains uncommitted on `phase3/sql-query-plan`.
