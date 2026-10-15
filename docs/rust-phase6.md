# Phase 6: native Arrow Flight tail

Implementation is ready for draft review; whole-phase acceptance remains open.
Finite and short live-mode evidence is complete, with comparative metrics withheld
under the predeclared gates. Long-term stability and cross-platform readiness are
not established. See [current measurements](phase6-measurements.md).

## PR #4 follow-up corrections

The reviewed decoder edge now loads dictionary IDs declared below primitive
fields while resolving records only through reachable column paths. Compatible
orphan occurrences cannot override a live column's nested dictionary bindings.
Five new Rust tests cover the reported failures, nested/shared-ID cases,
conversion equivalence and safety checks. Fourteen full Flight comparisons of
the corrected release against pinned Go match stdout, stderr and exit status in
text and JSON modes. Malformed dictionary buffers, including unused orphan
values, remain subject to Rust's safety validation. Schema-time safety checks
also apply to leaf-child subtrees, even when Go would never render them.

The finite harness checks the Go oracle identity before sampling and records
pre-execution input hashes, rejecting changes detected afterward. These are
path-based checkpoints; they do not detect a replacement restored between
checks. Capture cleanup retains the waitable leader until its process group is
terminated, then reaps its original exit status. Descendants that deliberately
leave that group remain outside the ownership contract. Nine Python regressions
cover identity replacement, early rejection, descendant cleanup and raw status.

CI and the harness Makefile now run each Python test module in a fresh
interpreter, preserving the profiling suites' process-wide safety guards and
source-admission inventory. Four runner tests cover discovery, environment
inheritance and failure propagation. Historical R6 artifacts and acceptance
limits remain unchanged; these corrections do not establish new performance,
soak or cross-platform acceptance.

## Implementation review status — R6

Immutable R6 normal/Cloud candidates pass central checks: 367 normal tests
(one ignored), 373 diagnostics tests (two ignored), fmt and Clippy. The
unchanged 291-case Flight corpus passes all remaining contracts: 36 strict,
255 explicit native-UA differences and one exact malformed-header diagnostic
exception overlapping UA. No new waivers were added. The separate focused
suites pass 88 remaining-contract configurations and verify four explicitly
excluded H2 limitations. These cover the dictionary and values corrections in
both output modes, transparent retries, encoding flags, empty DATA frames,
actual blocked-worker output and selected destination ports. See
[R6 identities](evidence/phase6/candidate-review-r6.json),
[checks](evidence/phase6/central-review-r6-tests.json),
[retained Flight replay](evidence/phase6/compatibility-review-r6.json) and
[focused replay](evidence/phase6/focused-review-r6.json). R5 remains immutable
historical evidence.

Fresh Opus decoder, transport and values correction reviews close their scoped
source objections. The values review independently repeats the float vectors
with zero differences and measures the previously source-only map panic-message
nondeterminism. The transport review independently passes 39 tests three times;
its low documentation clarifications are recorded in the transport notes.
The fresh whole-phase Opus source review now finds no blocker, high or medium
production defect in its bounded inspected surfaces. Its three low findings
are ledgered: malformed initial-dictionary text, pinned receive-allocation
ownership and an unprobed hostname-unescaping edge. It accepts source readiness
for measurement subject to the remaining admission gates, not phase completion.

The independent Sol integration review found two adapter issues: verified corpus
bytes were reread, and H2 per-case labels could remain verified after a failed
repeat. A fresh Sol correction review closes both with 17 independent tests and
three causal disabled-fix controls. A bounded native smoke passes 11 remaining-contract configurations and verifies
four excluded H2 limitations across 51 captures; original raw reports remain intact. The provenance race was reproduced with mocks, not
observed corruption of the earlier native captures.

CW1–CW3 remain independently accepted within their scope. Historical 291
captures lack destination-port provenance and are not retroactively certified;
R6 captures record it. H2-CL-3 remains a valid-wire capability limit: Rust's
padded-empty-frame 100/101 boundary is unit-measured, while Go behavior is
source-only. Nonnative-endian IPC, the other enumerated producer limits and
unmeasured tiny-frame pressure remain explicit.

R6 retained phases 1–2 pass. Phase 3 completes 683/684: the sole failure is
Go A/A instability in the exact empty-array-lookahead deadline diagnostic.
The failed report is preserved. The combined body/lookahead proposal has
fresh executed Opus and Sol scoped acceptance. Opus independently passes the
full 37-test suite normally and under `-O` against Phase6 dependencies and
the frozen fixture; [dispositions](evidence/phase6/combined-deadline-review-r2-acceptance.json)
retain source and artifact identities.
Phase 4 completes 160/161: its header-deadline Go A/A pair differs only in the
timeout suffix; R6 matches the suffixed Go capture byte-for-byte after URL
normalization. Its [independent Opus review](phase6-p4-header-deadline-review-opus-r1.md)
accepts the exact rule with two required test amendments. Those amendments
now pass 32 guarded tests and kill all 20 causal mutants, including disabled
stderr comparison and disabled independent timeout checks. The source rule is
unchanged. The [v2 evidence](evidence/phase6/p4-header-deadline-proposal-v2.json)
also records the corrected documentation and preserved original failures.
Phase 5 completes 186/186 plus three historical guards. All retained
inventories have run: 3266/3268 cases and 30 guards pass; [the aggregate](evidence/phase6/retained-review-r6-original-complete.json)
does not relabel the two A/A failures.
The three exact-case changes are composed into one LF-only patch per worktree's
actual base, with exact cache application and phase-specific guards preserved.
Full shared phase3/4/5 suites pass normally and under `-O` for both variants,
with each variant's own copied dependencies and the Phase6 frozen fixture R4,
no errors or skips. Fresh executed Opus composition review accepts both exact
patches with no blocking finding. The independent final Sol cross-check also
accepts them after 507 checks, with all 250 reviewed inputs unchanged. [Composition evidence](evidence/phase6/composed-deadlines-p3-p4-proposal.json)
records all hashes and test counts. Phase6 applied its exact own-base patch
without offsets or fuzz, verified all three postimages, and started fresh Go A/A
and R6 replays of the two 60-second cases and the 120-second header case.
[Application evidence](evidence/phase6/composed-deadlines-applied.json) preserves
the before/after identities. All three fresh actual Go A/A and R6 replays pass, with unchanged end identities.
The first P4 launch rejected an unsupported coordinator argument before any
client ran; that failure is preserved separately from the corrected passing
run. [Focused native closure](evidence/phase6/retained-review-r6-deadline-closure.json)
closes the retained diagnostic issues without relabeling the original reports
or claiming a new full-suite execution. Fresh Opus
measurement review accepts tooling readiness for the first same-Go A/A and
matching observer control, subject to provenance and a quiet handshake. Its
medium readiness consequence is explicit: fast finite ratios remain null under
the conservative CPU screen. The coordinator keeps that screen and predeclares
descriptive absolute interpretation for quarantined finite results. Three low
operating/reporting findings are addressed in the
[operating decisions](phase6-measurement-operating-decisions.md).
The first offline resource-summary Opus review reran 26 guarded tests and the
actual collector finalizer with retained sample shapes. It found a high tooling
defect: the original reporter rejected valid zero-row records with a nullable
first-record flag. Corrections and portable regressions now pass 35 author-run guarded
tests, including import isolation and blocked access to the former host-cache
inputs. Fresh executed Opus correction review accepts the reporter for offline
descriptive use after its own discovery, cleanup and collector-shaped probes.
[Acceptance and low-limit dispositions](evidence/phase6/resource-summary-review-r2-acceptance.json)
do not grant timing or budget acceptance. No collector or budget change was made. The retained failures are now closed through the scoped reviewed rules and
fresh actual deadline replays. The first same-Go A/A and matching hash-framing observer-control campaigns
now pass all 120 measured samples each. Both used explicit separate quiet
acknowledgements and the common lock, verified end identities, and released
only after owned-process cleanup. All ratios remain null under the predeclared
fast-sample CPU uncertainty screen. [Initial measurements](phase6-measurements.md)
report absolute distributions only. Fresh independent Sol data review accepts
the retained evidence integrity with no defect, after 122,900 checks and 4,301
unchanged input files. Subsequent collection now includes 1,440 finite measured
samples and 20 short live-mode sessions. All comparisons remain withheld; the
slow-sink hash pair also has different delivered counts. Long soaks, interpretive
profiles and build-cost measurements remain pending. The [current measurement
report](phase6-measurements.md) records those later results. The sections below
preserve earlier checkpoints and limitations.

The separate [source-origin inventory](phase6-source-attribution.md) identifies
narrow compatibility algorithms adapted within the native Rust design. The
[notice mapping](phase6-source-notices.md) retains the applicable Go, Arrow,
gRPC, goccy and protobuf texts and Hinnant algorithm attribution. Author
clarifications and primary-source checks close the inventory's uncertain
origins. These additions leave every R6 compiled source and binary unchanged;
future packaging remains separate.

The earlier interim independent source audit found a batch-loss case outside
the 291-case corpus: two Go-equal shared dictionary list types with different
element names initialize but fail during Rust record decoding. The first fix
canonicalizes Go-ignored annotations in dictionary value types and passes
98 normal/100 diagnostics tail tests, including complete-record regressions
that fail against R4. Its additional probes found another output
mismatch: a shared parent dictionary with distinct nested dictionary contents
selects later child values in Go and earlier values in Rust. The continuation
now uses Go's last compatible dictionary layout and binds nested keys to the
current memo at record time. Its 23 continuation streams match pinned Go;
101 normal/103 diagnostics tail tests pass. Central R5 checks pass 357 normal
and 363 diagnostics tests (one ignored), plus fmt and Clippy. Immutable R5
standard/Cloud binaries replay the existing 291 Flight cases without new
mismatches. Full retained replay and a fresh independent Opus decoder/lifecycle
review are running; this is not final acceptance.
The audit also measured the informational
HTTP/2 response limitation described in the transport notes: Rust can render
after an initial 103 where Go terminates before output. This is explicitly
outside parity, not a text-only diagnostic difference. The existing R4 evidence
does not close these findings. Earlier required Opus review attempts failed
because of provider rate limiting; fresh Opus transport and decoder reviews are
now active. A final whole-phase Opus review remains required.

The fresh [Sol contracts review](phase6-review-wave-contracts-sol.md) found
three harness gaps: a purported blocked-write payload fits in the pipe, the
garbage-header exception does not bind the full stderr envelope, and authority
normalization accepts an incorrect numeric port. Focused corrections and a
separate causal blocked-write followup are in progress. Original reports and
references remain preserved; their counts do not close these objections.

The fresh [Opus decoder review](phase6-review-wave-decoder-opus.md) independently
accepts the IA-1 binding semantics within its scope, but found W-1: repeated
opening dictionary messages can leave another ID absent, and Arrow's flat
decoder substitutes empty values. Go fails before output, even for null keys
or zero rows; a real R5 process reproduced the extra-row mismatch. W-2 reports
missing IDs in numeric rather than schema order. The source correction checks
presence and unresolved nested deltas in schema order before record decoding,
using a set to deduplicate IDs without quadratic scans. Eleven independently
generated Go probe streams are durable in `tests/fixtures/arrow-go-v13-decoder-wave`;
central regeneration matches every captured byte. Tail tests pass 102 normal
and 104 diagnostics cases. These corrections require a successor release build
and fresh review; R5 remains preserved as pre-fix evidence.

The additive `harness/phase6_dictionary_cases.py` sends the durable IPC streams
through the maintained raw Flight fixture, without computing expected values.
All 25 IA-1 cases pass actual pinned Go A/A and R5 process captures with the
existing wire-UA difference kept separate. The W-1/W-2 process corpus is a
separate 11-case selection; its fixed-candidate replay is pending.

The fresh [Opus transport review](phase6-review-wave-transport-opus.md) found
three additional transport behaviors: eager rejection of an advertised encoding
on uncompressed messages, loss after many empty DATA frames, and discard of the
channel created by an in-stream transparent retry. The owner is correcting
those paths in the existing transport layer. The CONNECT user-agent retains
the pinned byte contract, with an explicit compatibility rationale separate
from the native h2 UA ledger. Two HTTP/2-invalid response rules remain explicit
[capability limits](phase6-h2-limits.md): content-length mismatch and
connection-specific headers. Twenty-four native captures verify their exact
outcomes plus controls; four limitation cases are excluded from parity.
None of these findings is closed by the earlier 291-case pass.

CW1–CW3 harness corrections now pass 143 focused tests and six native R5
controls, including causal worker-write blockage before HTTP/2 keepalive and
signals. A fresh Sol correction review is active. Archived 291 captures lack
independent discovered-port provenance; the fresh selected-port controls do
not retroactively certify it. All original evidence remains preserved.

The separate [informational-response process suite](phase6-protocol-limits.md)
now verifies two 200 controls and two 103 limitations in text and JSON against
the frozen Go/R4 binaries and fixture r5. The limitations retain the differing
stdout, status and failure stage and are excluded from full parity.

[Independent measurement review r2](phase6-measurement-review-r2.md) found
three additional admission gaps: selected executable identities were not bound
to declared build provenance, end checks omitted some declared inputs, and a
post-capture exception could lose completed observations. Corrections are
settled, together with TLS identity for replicated aggregation and stale
reproduction instructions; 81 scoped pure/mock tests pass. Fresh independent
[measurement review r3](phase6-measurement-review-r3.md) closes those findings
but reproduces two remaining races: execution uses the original fixture/meter
symlinks rather than verified resolved paths, and the parent manifest can change
between parsing and hashing. Narrow corrections now pass 84 scoped pure/mock
tests. Fresh [measurement review r4](phase6-measurement-review-r4.md) independently
closes both races and reports no unresolved objection in the scoped tooling.
Its Opus attempt failed immediately with the same provider rate limit; the
completed Sol review does not satisfy final whole-phase Opus review. A corrected
release candidate, fresh provenance and actual quiet-window agreement are still
required before timing. The prior
116-test result remains evidence for its recorded source, not acceptance of
these newly identified paths.

The base is merged phases 1–5 at `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`.
The Go behavior oracle is `07af390ab09bda8ac9e427ffc6310077bace0125`.
[Baseline identities](evidence/phase6/baselines.json) record copies preserved
before rebuilding, including the accepted phase-5 review-fix Rust artifact.

## Scope and contracts

Implement the existing `pb tail DATASET [-o text|json]` command. There is no new
filter, ingestion, capture, replay, resume, observer, or CLI option. Discovery
uses authenticated HTTP `/api/v1/about`; Flight uses the discovered separate
port and snapshots credentials after successful discovery, including any HTTP
OAuth refresh. Flight reconnects reuse that snapshot without refreshing it.

The subscription stages remain distinct. A DoGet failure or reader-initialization
failure is fatal. Established-read EOF, Canceled, Unavailable and OK restart
after 500 ms; other failures terminate. Reconnection creates fresh schema and
dictionary state and carries no delivery cursor.

Stdout retains a newline per encoded row and an additional newline per batch.
Both output modes share those bytes. Text adds the existing stderr status
sequences even when stderr is a pipe. Go's ignored conversion error can truncate
a batch after preceding complete rows; this behavior requires oracle fixtures.
Arrow Go v13 uses goccy/go-json v0.10.0 on this build, so standard-library JSON
formatting is not sufficient evidence for the Arrow output adapter.

## Ownership and integration

New `tail` modules separate transport/discovery, subscription lifecycle and
Arrow value encoding. Existing profile/auth services remain concrete and owned.
The current `ApiClient::profile()` accessor already supplies the updated
credential snapshot; no shared auth/HTTP refactor is required for this seam.

The implemented pipeline moves one owned Flight message and its subscription
decoder to a scoped worker, then awaits its acknowledgment. Both channels have
capacity one. The worker decodes dictionaries and records, renders and writes;
the current-thread runtime continues servicing HTTP/2. Schema verification, conversion and renderer-plan construction also run on the
worker. Causal schema-initialization and blocked-output tests verify runtime
progress and no next application-message read; real HTTP/2 keepalive evidence
also exists in the 182-case process corpus.

A queue count is not a byte bound: encoded frames, decompressed buffers,
dictionaries, schema, renderer scratch and staged output each own memory.
Dictionary deltas can grow without bound within a subscription. Reconnection
drops the decoder. The worker releases each decoded batch before writing and
releases output staging above 4 MiB. The renderer releases aggregate list scratch
above 1 MiB after each batch, including conversion errors and panics. These are
retention policies, not peak-memory limits. Diagnostic-only owner counters and
native RSS observations remain distinct; resource acceptance still requires
large/compressed/dictionary measurements.

Aligned dictionary bodies can retain a shared Tonic receive allocation beyond
the visible slice counted by Arrow diagnostics. R6 keeps that ownership policy;
owner maxima can both omit backing capacity and double-count shared buffers.
The [resource interpretation addendum](phase6-resource-interpretation.md) records
this source-derived mechanism and prohibits treating those counters as unique
heap or proof of release. Alignment frequency and pinned capacity remain unmeasured.

Go accepts invalid UTF-8 in Arrow strings. The IPC decoder therefore uses a
recursive wire schema with Utf8/LargeUtf8 represented as Binary/LargeBinary,
while the renderer uses the original schema. Their buffer layouts match, so
offset and buffer validation remains enabled. No unsafe validation bypass is
used. Tests cover nested and dictionary strings and hostile offsets. Rejection
of malformed offsets is a safety difference where Go can panic or read invalid
data; exact malformed-data diagnostics are not presumed compatible.

Arrow Flight 60.0.0 and tonic 0.14.6 are the maintained protocol implementation
in use. IPC LZ4/Zstandard support is enabled. TLS shares the existing
AWS-LC provider and native trust roots, with separate Flight policy. Dependency
size/build cost remain acceptance measurements. The receive cap is explicitly
4 MiB, matching the pinned grpc-go default. It does not cap decompressed IPC
buffers. [Transport notes](phase6-transport-notes.md) record policy and diagnostic
differences, including CONNECT, ALPN, native roots and dial deadlines.

## Validation and measurement status

Four Opus implementation assignments cover transport, values, lifecycle and real
Flight fixtures. Three Sol assignments cover differential/process tests, bounded
latency/resource collectors and retained gates. Three independent transport and
four decoder review rounds have produced concrete corrections. The review-r4
candidate includes the resulting decoder and resource-accounting fixes and
the measured transport diagnostics. Final whole-phase review remains required
after integration and measurement.

The immutable review-r4 source snapshot passed 351 normal tests and 357 diagnostics
tests (one ignored), formatting and clippy with warnings denied. Its hashes and
commands are in [central test evidence](evidence/phase6/central-review-r4-tests.json).
The standard and Cloud review-r4 builds preserve that exact source snapshot,
including the round-4 decoder corrections. Their 291-case process replay passes
the remaining contracts; resource and performance acceptance is still pending.

The durable Go value corpus has 44 cases replayed through the production worker
and renderer. Additional decoder corpora preserve malformed-stream behavior:
round 2 has eight matching cases and two declared safety differences; round 3
has 23 matching cases, five row-count rejections where Go renders, and two
matching fatal stages with different error text. The decoder enforces Go's
64-level array-load limit while allowing schema verification to table depth 128.
Schemas beyond that bound fail initialization where Go can accept them. See
[value notes](phase6-values-notes.md) and the decoder review reports for remaining
uncharacterized malformed-schema cases; this is not universal IPC parity.

The fixture-r4 Go process freeze passes 219 cases twice. Immutable review-r4
standard/Cloud binaries pass all 219 remaining contracts (36 strict parity,
183 separately counted native h2 user-agent differences, one exact malformed
header diagnostic exception overlapping UA cases). The preface reset diagnostic
and actual 20-second deadlines pass. A further 56 framing/status-details/refusal
cases and 16 trailer-close cases also pass their remaining contracts. Combined:
291 cases, zero unaccepted mismatches, 36 strict, 255 UA differences, one exact
diagnostic exception. See [comparison evidence](evidence/phase6/compatibility-review-r4.json).
No new waiver was added. Historical failing reports remain preserved. These
inventories exclude the newly characterized round-4 decoder cases.

Retained phases 1–5 pass 3,256 cases plus 30 guards on the review-r2 binaries,
with no failures or skips in that selected scope. Opt-in real 60/120-second and
known-divergence suites are excluded from this count. The review-r4 retained
replay is running with the real 60/120-second deadline cases; phases 1 and 2
have completed successfully. The narrowly reviewed phase-3 Go redirect-deadline
normalization is applied; phase-7 feature guard promotions are not applied.
The complete replay result is still pending.

The combined harness/collector suite now passes 116 tests after measurement
review corrections; independent measurement re-review is pending. This includes
eight targeted observer tests: live procfs sampling rejects exited/racing records,
missing live RSS stays absent, and complete-record timing follows the complete
LF-framed object independently of fixture trace updates. A native-meter probe
shows inflated Python observer memory is not charged to child peak RSS. These
are functional checks, not timings or sustained soaks. Admission uses exact
bounded fixture counters for all auth/ticket/request checks even when event
logs drop records. Hash/framing observer controls remain separate from primary
results and validate the entire output checksum and framing.

Fixture-r5 functional checks pass for Go and review-r4 Rust on large LZ4 and
Zstandard dictionary streams. Each produces 36 rows in nine batches, the same
32,506,837 output bytes and whole-stream hash, with six deltas and two replacement
resets reaching 64 MiB dictionaries twice. Maximum encoded messages are 72,896
bytes for LZ4 and 2,191 for Zstandard, below the 4 MiB receive limit. Two small
cyclic-dictionary cases also preserve exact output while event records are
dropped. Parent verified the six sample artifacts and summary counters;
[functional evidence](evidence/phase6/fixture-r5-functional.json) preserves their
identities. These runs validate workloads and output, not comparative memory,
latency or throughput. Collector corrections and independent re-review remain
pending before campaign admission.

The preserved R4 debug diagnostics build also passes both large compressed
workloads against fixture-r5. Sidecars report one outstanding application
handoff, 67,109,124 bytes of maximum dictionary buffers and terminal output
staging capacity zero. The batch and dictionary maxima can count shared Arrow
buffers and must not be summed as unique heap. There is no last-dictionary-size
or release counter, and the short runs have zero post-warmup trend samples;
neither dictionary RSS release nor sustained memory stability follows from
these checks. [Large diagnostic evidence](evidence/phase6/diagnostics-large-r5-functional.json)
records exact binary, sidecar and sample identities, separately from timings.

Earlier diagnostic max-in-flight=1 observations came from a construction marker,
not a measured counter. Current source uses an atomic RAII counter from each
successful application handoff until its acknowledgment is consumed; an overlap
test requires the counter to reach two under deliberately overlapping counter guards.
Eight small process workloads now validate the actual counter with peak one,
exact output and 24 rows in three batches each; see
[diagnostics evidence](evidence/phase6/diagnostics-real-counter-r4.json). These
observations do not alone prove guard lifetime or absence of transport read-ahead.
The standalone overlap test exercises counter guards, not simultaneous session
handoffs; a real worker-gate lifetime regression now passes in the owner scratch build
and fails when its guard is deliberately dropped early. The central diagnostics suite also passes that lifetime test. Dictionary, renderer-scratch
and output-staging measurements retain their separate owner meanings.

Native release timing will use hashed prebuilt Go/candidate binaries, plus prior
Rust only on already implemented shared commands. Builds and profiling remain
outside those timing windows. Every campaign requires the common advisory lock
and an explicit quiet-window agreement with phase 7. Samples must validate rows,
framing, requests and termination before admission. Complete-record latency,
per-batch latency, CPU, RSS, faults, switches and I/O are distinct metrics.

Finite streams ending in InvalidArgument are failure-terminated workloads, not
successful live streaming. Bounded steady, idle, reconnect, dictionary and
slow-sink runs will separately inspect RSS/FD trends and fixture/server buffering
with bounded collection. No exactly-once or continuity guarantee follows from
these fixtures. Successful real-server SQL/HTTP2, Flight authorization/delivery,
macOS and full cross-platform readiness remain unverified.

## Malformed negative row counts

The bounded probe in `harness/tail-negative-oracle` was rerun with the pinned
Go 1.25.4 toolchain against the repository module graph. All nine controls and
negative cases passed their assertions; exact outputs and provenance are in
`docs/evidence/phase6/negative-row-go125.json`. Null and field-less Struct
columns whose batch/node lengths share the negative value produce no rows and
one batch newline in Go. Rust preserves precisely that characterized shape
without constructing an enormous unsigned row count. Int64 is fatal in both,
with a declared decode-diagnostic difference. Other malformed negative layouts
remain safety rejections; these probes establish no general malformed-IPC parity.

Practical performance investigation thresholds were recorded before candidate
timing in `docs/evidence/phase6/performance-budgets.json`. No native campaign
results are available yet.

## Decoder round-4 corrections and limits

The independent round-4 review was source-only because its agent shell could
not write temporary output. The implementation owner then ran 15 bounded probes
with Go 1.25.4. Go correctly byte-swaps big-endian numeric, compressed, string
and dictionary streams; pre-fix Rust silently printed swapped numeric values.
Current source rejects non-native byte order during reader initialization with
an explicit diagnostic. **Non-native-endian IPC remains unsupported:** Go accepts
these streams. This is a capability limitation, not parity or a malformed-data
classification. Native-order controls pass. No custom byte-swapping was added.

The probes also confirm six shared-dictionary-ID conflicts and three compatible
cases. Current schema checks compare original value types in Go's child-first
order before the wire schema rewrites strings or nullability. List element
names are ignored while element nullability and struct names matter. Conflicts
now use Go's initialization stage, path wrapping and diagnostic. All 15 probe streams and Go outputs are durable. Parent regeneration verifies
every output and manifest byte; central tests pass the corresponding generated
stream checks. See [probe verification](evidence/phase6/decoder-r4-parent-verification.json).

Malformed-producer differences remain for empty/duplicate union IDs, decimal
precision/scale beyond Rust's metadata range, and absent schema fields. The
flatbuffer verifier requires valid UTF-8 and NUL termination for every header
string, including metadata and timezone. The unknown-union-mode diagnostic now
matches Go. Mixed-fault ordering and invalid temporal-unit diagnostics remain
explicit limits. The main corpus does not cover every malformed producer or
architecture.

## Pre-campaign measurement corrections

The independent [measurement review](phase6-measurement-review-r1.md) ran the
89-test harness/collector suite and bounded mocked probes. Before any quiet
window, the owners are correcting incomplete-duration-schedule admission,
dictionary churn that stops after warmup, single-session bootstrap intervals,
budget provenance and missing reports on exceptions. Additive fixture accounting
will distinguish scheduled/emitted/omitted batches, lateness and Send-call wall
time, and a bounded cyclic dictionary mode will retain churn throughout a soak.
Collector changes will bind predeclared budgets, preserve incomplete reports,
report observer/fixture CPU and interference uncertainty, require whole-duration
partitions to fit, and keep sink/schedule-controlled metrics absolute-only.
These are pending corrections, not validated measurement capabilities yet.
The practical regression thresholds are unchanged; schema-2 controls were added
before any native timing to make their provenance and interpretation explicit.
