# Phase 6 integration checkpoint — 2026-10-14

This is intermediate evidence, not phase acceptance. Source is an uncommitted
change on base `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`; Go is pinned to
`07af390ab09bda8ac9e427ffc6310077bace0125`. Final source/binary manifests and
retained-gate reruns must supersede this checkpoint.

## Review round 2 implementation checkpoint — 2026-10-14

This checkpoint supersedes earlier status counts below. It is not acceptance.
The immutable review-r1 release pair passes 3,256 retained cases and 30 guards;
its 182-case Flight comparison has five unresolved transport failures, 33 strict
matches and 177 remaining-contract matches after separating 144 native-user-agent
differences and one exact malformed-header diagnostic difference. Counts overlap
only where explicitly recorded in the comparison report.

Round-2 review also found malformed binary-metadata panic/code-loss paths and
additional IPC panic/nullable-field boundaries. The transport now compiles against
an owned raw-byte FlightStatus interface: a request-local response adapter validates
headers before tonic, while a bounded frame observer distinguishes refused GOAWAY,
RST_STREAM and clean DATA end without trailers. This preserves stage-specific
retry decisions without rewriting unrelated Unknown statuses. Regression tests
and a fresh independent round-3 transport review are still in progress; production
library compilation alone is not compatibility acceptance.

The decoder fixes are on disk: reject negative fixed-binary widths and overflowing
fixed-list lengths safely, guard implicit union type codes, retain Go nulls under
non-nullable declarations, and reject short compressed prefixes before reading.
The first two remain explicit Go/Rust safety divergences. The production session
now has 44-case value-corpus replay and ten decoder-stream tests (eight parity,
two divergences). Latest owner verification used a stub transport; central real
integration must rerun after transport tests settle. The ten Go decoder probes
were independently rerun with pinned Go 1.25.4 and bounded memory/time/output.
See `docs/phase6-decoder-review-r2.md`, `docs/phase6-transport-review-r2.md`,
`harness/decoder-oracle/` and `tests/fixtures/arrow-go-v13-decoder/`.

The r4 fixture adds raw HTTP/2 response scripting. All 37 new real Go PB cases
pass twice; the full 219-case Go freeze is running. Invalid grpc-message bytes
are raw in text and escaped per invalid byte in JSON (partial E9 80 yields two
ASCII `\ufffd` escapes). No lossy-text diagnostic waiver is accepted.

The collector has 46 passing central correctness tests, exact bounded summary
admission despite dropped event logs, separate hash/framing observer controls,
CPU per completed row, explicit ratio quarantine for incomparable contracts,
and bounded campaign-window admission. Eight diagnostic-build functional cases
pass with one application message in flight. A diagnostics-only correction also
counts schema Open handoff; its real schema-only process check remains pending.
No native timing, profiling or sustained soak has run. Final whole-phase review,
new immutable candidates, all retained gates, measurements and final manifests
remain required; no quiet window has been requested or granted.

## Central verification of the round-2 source

The real all-target builds now pass 334 normal tests and 338 diagnostics tests
(one ignored). Formatting and Clippy with warnings denied pass. Source hashes
and raw-log locations are in [central-review-r2-tests.json](central-review-r2-tests.json).
This supersedes the stub-verification caveat for compilation and unit tests;
new release process comparison and independent review remain open.

The schema-only diagnostic functional check passes with one received schema,
zero batches/rows and maximum application messages in flight equal to one.
See [schema-idle-diagnostics.json](schema-idle-diagnostics.json). This verifies
the Open accounting correction, not a timing or memory-performance result.

Independent transport review round 3 disproved the reported trailer-discard
residue. Its 20 valid-HPACK probes preserved trailers across GOAWAY, FIN and
RST; the transport unit helper encoded a 139-byte value with one length byte,
accidentally setting the Huffman flag. The resulting local protocol error was
misclassified by the new Trailers fallback. The helper and fallback need
correction; no parallel HPACK parser is justified. See
[transport review round 3](../../phase6-transport-review-r3.md).

Additional review findings require pinned Go process characterization before
acceptance: partial gRPC framing at trailers, valid status-details protobuf
handling, and transparent first-attempt unprocessed-stream retries. The review's
claim that every partial-message trailer produces unexpected EOF is provisional:
Go rpc_util.go preserves non-EOF read errors, so status 1/14/3 may survive while
status 0 becomes unexpected EOF. The harness is probing each case separately.
No blanket retry or diagnostic exemption is authorized by these predictions.

## Decoder round-3 follow-up in progress

[Decoder review round 3](../../phase6-decoder-review-r3.md) independently verifies
all round-2 fixes and finds no Rust panic in its 30 new bounded streams. New
corrections are underway for nonpositive FixedSizeList sizes, explicit union-ID
range, valid deep schemas, and run-end field nullability. The broader malformed
row-count rule is being documented as a deliberate rejection difference.

The old in-flight diagnostic used literal 1/0 values and could not independently
detect overlapping handoffs. Earlier max=1 observations are delivery markers,
not measured concurrency evidence. The parent replaced it with an increment on
successful schema/message send and an RAII decrement after ack/cancellation,
with a two-overlapping-handoff regression test. Central verification is pending:
the first focused test build encountered transport's in-progress helper changes.
The structural one-ack pipeline and causal blocked-output tests remain distinct
evidence of the application handoff bound.

## Retained review-r2 result

The immutable review-r2 standard/Cloud pair passes the unchanged default
phase 1–5 tiers: 3,256 cases and 30 guards, no failures or selected-case skips.
The parent independently checked every binary identity and all five report
hashes in [retained-review-r2.json](retained-review-r2.json). Explicit opt-in
real-60-second/120-second and known-divergence tiers are excluded; deferred
phase-7 workflows are not claimed as parity. This result belongs to the recorded
review-r2 pair, before the ongoing round-3 source fixes, and does not accept
Phase 6 or prove live-server delivery.

## Durable round-3 decoder oracle

The parent independently reran all 30 streams in
`tests/fixtures/arrow-go-v13-decoder-r3` with pinned Go 1.25.4 and the existing
4 GiB memory, 16 MiB output-file and 10-second bounds. All 120 stream/output
files match their recorded bytes and hashes. The nondefault regeneration
template now names its generator explicitly. Log:
`/home/ajs/.cache/pb-phase6/tmp/central-decoder-oracle-r3.log`.
This verifies Go evidence; Rust schema fixes are still integrating.

## Earlier checkpoint evidence

| Check | Result and scope |
|---|---|
| Central normal Rust test suite | 321 passed on settled worker-init source; formatting and clippy with warnings denied pass |
| Central diagnostics test suite | 325 passed, 1 ignored on settled worker-init source |
| Central IPC review-fix tests | 23 session tests passed, including all 64 body alignments for dense unions and truncated record/dictionary bodies |
| Go process freeze | r3: 182 cases × 2 passed; explicit fixture reset removes the one r2 FIN/RST diagnostic race |
| First Rust standard process comparison | 152 cases: 147 payload/auth/process matches after separating native h2 user-agent; five unresolved cases at that revision |
| Go value oracle | 44 generated IPC cases matched exact rendering bytes and panic messages on original and binary-rewritten string schemas; owner-reported test result |
| Collector functional integration | 9/9 tiny finite/one-second cases plus 4/4 low-cap/observer-control cases against Go; 40 collector tests; no performance claims |
| Fixture tests | Owner reports Go 1.25.4 race-enabled tests repeated twice, legacy and phase-6 cases; actual receive boundary 4,194,304 accepted / 4,194,305 rejected |
| Retained phase 1–5 release gates | 3,256 cases and 30 guards pass against old integration candidate; final-source rerun required |

Frozen 160-case fixture SHA-256:
`9dd1d200fec43dc5db0a6e381879c70e888c97378c62c83711d3e9e6c5f2953e`.
Frozen corpus SHA-256:
`670af70ebdac416aa2c0898ebe2866487106a8b5a68dd48869a0d31fa61243ed`.
These identify that checkpoint, not later corpus revisions.

Integration-only Rust binaries (before protocol review fixes):

- Standard: `42946cc3ae36727c012a211acdaba5efb9190f2bda10ec805854587737d29f35`.
- Cloud: `1b6146680bee7509812c023462bff7b50f298debe972f2694f066846165ae27e`.

They use `PB_VERSION=v0.0.0-harness`. They are not performance or release
acceptance candidates. Baseline identities remain in `baselines.json`.

## Findings and resolution state

The first process comparison found dense-union alignment and truncated-body
panics (Rust exit 101 versus Go's normal rendering/fatal error). The session
now places unaligned protobuf bodies in aligned Arrow-owned storage and checks
IPC buffer ranges before decoding. Unit regressions pass; release process
rechecking is pending. Missing initial dictionaries now preserve the Go
unknown-ID diagnostic for a record batch in the dictionary slot. Garbage
flatbuffer verifier text remains a declared diagnostic difference. CONNECT
407 response-body formatting now includes the declared body; process recheck is pending.

Invalid UTF-8 strings now decode through recursively rewritten Binary schemas,
keeping offset validation enabled and rendering from the original schema.
Renderer list scratch has an aggregate 1 MiB retained-capacity cap; output
staging is released above 4 MiB. Neither policy bounds peak memory or growing
dictionary deltas. Diagnostics report owner maxima, not unique heap or RSS.

The independent transport review identified connection-loss retry, TLS authority,
header-limit and preface-timeout differences. Round-1 fixes use a source-chain
transport-loss classifier and a dialer-owned TLS/preface deadline, with explicit
header/window policy and corrected CONNECT parsing. Go probes confirm the FIN,
RST, GOAWAY, large-header and real 20-second deadline behavior; the new candidate
still needs comparison. A fresh transport round-2 review is active.

Decoder review exposed malformed metadata paths that could panic in arrow-rs.
A pre-decode metadata walker now checks negative lengths, null counts, short
bitmaps and union buffers, and impossible compressed-size declarations. The IPC
dependency is pinned to 60.0.0 because the walk follows its layout rules. Invalid
metadata rejection is a safety divergence, not presumed Go diagnostic parity.
Valid-looking compressed sizes can still exhaust memory before decoding; there
is no fixed peak-memory guarantee. Negative-length Go behavior is being probed.
Initial schema/renderer construction now runs on the acknowledged worker; a
causal 20,000-field test demonstrates runtime progress while initialization waits. A separate final
whole-phase Opus review remains required.

The native tonic h2 user-agent differs from grpc-go. Reports must retain both
raw values and distinguish payload/auth comparison from strict wire parity.
CONNECT's explicit user-agent remains compared. No blanket header, diagnostic,
ANSI or whitespace normalization is permitted.

## Remaining acceptance

Finish expanded Go freeze and Rust comparison, resolve concrete review findings,
rerun all retained gates, and publish final source/build/dependency manifests.
No native timing, profiler, sustained soak or successful live-server Flight
campaign has run. Performance requires an explicit phase-7 quiet-window
agreement and `/home/ajs/.cache/pb-rs-performance.lock`. Cross-platform and
deployed authorization/delivery guarantees remain unverified.

Supporting repository documents: `docs/rust-phase6.md`,
`harness/contracts/phase6.md`, `docs/phase6-transport-notes.md`,
`docs/phase6-transport-review-r1.md`, `docs/phase6-values-notes.md`,
`docs/phase6-fixture-notes.md`, and `docs/phase6-measurement-plan.md`.

## Settled-source candidate comparison

`candidate-review-r1.json` records immutable standard/Cloud candidates and every
Rust source/Cargo hash. The full 182-case comparison identified five unresolved
transport mismatches: GOAWAY after schema and after a batch terminates with
tonic's missing-trailer Unknown instead of retrying; plaintext and TLS HTTP 502
responses have different status/diagnostic handling; and TLS-to-plaintext has
different handshake text. Transport corrections and independent review are
active. These are failures, not blanket diagnostic exemptions. IPC alignment,
truncated bodies, missing dictionaries, TLS authority, ALPN, large headers and
actual preface deadlines now match in the expanded comparison. Native h2 UA and
the exact garbage-header diagnostic exception remain separately counted.

The actual base and current lock both contain Hyper 1.12.0; no Hyper update was
made. A transient investigation hypothesis referring to cached Hyper 1.8.1 was
retracted after checking both lockfiles.

The pinned Go 1.25.4 negative-row probe passed all nine bounded cases, preserved
in `negative-row-go125.json` and `harness/tail-negative-oracle`. Real debug
sidecar validation passed eight compressed/dictionary/list workloads with
maximum application messages in flight equal to one. These observations are
functional only. Investigation thresholds are predeclared in
`performance-budgets.json`; no native timing/profile/soak campaign has begun.
