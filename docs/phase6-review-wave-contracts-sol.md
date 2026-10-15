# Phase 6 independent contracts, harness and evidence review — Sol

**Focused disposition: three concrete harness/evidence objections remain open.**
CW-1 does not force the blocked-writer condition it claims; CW-2 admits more
stderr changes than the declared garbage-header message difference; CW-3 hides
an incorrect numeric Flight authority port. None is a newly measured Rust
production mismatch, and none receives a waiver here. The historical accounting
and preserved artifacts are internally consistent. This report does **not**
grant whole-phase acceptance or replace the required fresh final Opus review.

## Scope, identities and work performed

Reviewed only `/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail`, HEAD/base
`4a812d5b9bc4c40882ab10897a73c3ce1e715ce0` plus dirty Phase 6. Go behavior is
pinned to `07af390ab09bda8ac9e427ffc6310077bace0125`. Read the cached final
roadmap, implementation report, Phase 6 contracts/reference, current harness
and tests, protocol-limit suite, fixture source/notes, relevant transport,
decoder and interim source reviews, settled measurement review r4, retained
Phase 1/3 diffs, durable oracle generators/manifests and supplied evidence.
Prior review conclusions were treated as claims to check.

No agent was launched. Only this named review document and
`/home/ajs/.cache/pb-phase6/review-wave-contracts-sol` were written. No shared
source/test edits, commit, build, product/Go-oracle/Flight-fixture execution,
live access, external post, credentials, timing/profiling/soak/pressure campaign,
performance lock or quiet token was used. Existing artifacts were read only.
The 52-test runner uses bounded Python child/loopback collector controls; these
are functional harness tests, not native tail trials or performance evidence.
All temporary variables and `phase6.TMP` pointed into the owned disk cache;
bytecode writes were disabled. `/tmp` was not modified. No rate-limit or EDQUOT
error occurred.

Exact reviewed harness identities:

- `harness/phase6.py`: `f9f8ed51b42aee896b6f2fc11ff6eb806c9243cfb75f49f99b10f77e83a1f445`.
- `harness/test_phase6.py`: `33b7e13d3cf0720296fbce284f8aa28eed4a0314ead65e4de0fb6a672dfe7cb4`.
- `harness/phase6_protocol_limits.py`: `9b3ae577d45be14a000b834a1d9b84fc6cb9d9840958c63cf6555c9b00b1c90c`.
- `harness/test_phase6_protocol_limits.py`: `15ab3f8767b9ece7ffaa3744d58a7d642d43573dc0a3f89ec11c20159afc4231`.
- `harness/fixture/phase6.go`: `8e77b9681cadb9bdc7a87fa1ebbbd6520d87fbca3b7ed2298f20af3ba9e12eb3` (current r5 source).
- `harness/contracts/phase6.md`: `078902436e30567bc09184e6a40842a05b75f6f81b2853ee3f67ed5b451ea099`.
- `harness/phase1.py`: `e0216062f0980168e8ea9708f819e773c68e4452a7750f689f9ca29e8c5dc6a6`.
- `harness/phase3.py`: `7f8deaf00185b94cd5934fbe6542e0393ded5f25aadd918012bd2f072d222f9d`.
- `harness/test_phase3.py`: `f11f07cd571fe9d8b3c364c20a93aaf6cbd81b999dc077b2126374c5488deb59`.

The independent digest inventory is
[artifact-identities.json](/home/ajs/.cache/pb-phase6/review-wave-contracts-sol/artifact-identities.json).
It binds 4,118 paths, including reviewed inputs, result files and untouched raw
captures. Recount/results are in
[artifact-audit.json](/home/ajs/.cache/pb-phase6/review-wave-contracts-sol/artifact-audit.json),
[supplemental-checks.json](/home/ajs/.cache/pb-phase6/review-wave-contracts-sol/supplemental-checks.json)
and [probes.json](/home/ajs/.cache/pb-phase6/review-wave-contracts-sol/probes.json).

The user announced IA1 source settlement during this review. Verified its
manifest/artifact provenance only: session
`d86a574fb7eb9500bffaf3fc0f0d0f2507d1390ade35598baed347371409866f`, tests
`7dad715f4b67e782d7e44745231b6d1510b32bd689ab800a3946d261ca9c07fe`.
These are the **only two** source differences from the complete R4 production
manifest. IA1 decoder correctness belongs to the separate Opus decoder review;
its owner-reported 101/103 tests were hash-verified, not rerun here. No R5
candidate was announced as built during these checks. All process and retained
results below remain associated with R4, not the settled source.

## CW-1 — Medium: paused stdout does not establish a blocked writer

**Measured independent pipe probe plus preserved R4 artifact evidence.**
Locations: `harness/phase6.py:490`, `:518`, `:527`; the default schema is at
`:424`. Current fixture `harness/fixture/phase6.go:2038` ignores `payload_bytes`
for `basic`; only `wide` uses it at `:2047`. The historical R4 captures directly
confirm the payload actually produced by that executable, independently of
the current fixture source.

`p6-blocked-writer-http2-progress` requests 128 rows with
`payload_bytes=16384`, but does not select `schema='wide'`. The basic rows retain
the small fixed message. Go and R4 Rust each produce exactly **21,975 bytes**,
128 rows and one complete batch, SHA256
`f795969c4eda081f99e3c58e82ad7c6648b3e1486d135a6b280417e21800d4a5`.
The current harness still reports no issues for these captures.

The independent probe created an ordinary pipe using the harness's `os.pipe`
primitive. `F_GETPIPE_SZ` returned **65,536 bytes**. With its reader left entirely
undrained and its writer set nonblocking, **one write accepted all 21,975 bytes**.
No renderer scheduling or network timing assumption is needed: this workload
fits in the pipe. The archived subprocess reports do not record their original
pipe capacity, so this review does not invent a historical measurement of it;
the source permits this directly reproduced no-block condition on this host.

The two write-stage signal cases use the same ineffective payload. Both Go and
R4 Rust retain all 128 rows and the complete batch separator before signal
death is collected. The `O_NONBLOCK` tail case also retains all 21,975 bytes;
it establishes inherited flags and ordinary output, not an exercised EAGAIN
path. These observations are in `probes.json`.

**Contract impact:** the 1.8-second keepalive hold currently exercises a paused
consumer with a workload that can already be fully buffered. It cannot prove
HTTP/2 PING/ACK progress while the tail writer is blocked. The write-signal
trigger is server `batch_emit_done`, which proves neither entry into the client
write nor blockage. This leaves the claims at `harness/contracts/phase6.md:51`
and `:65` unsupported in their blocked-write interpretation. The separate
`HeldWriter` unit test at `src/tail/session/tests.rs:449` does causally hold
the writer and observe runtime ticks/no next application read; it remains
meaningful evidence, but does not execute the real HTTP/2 keepalive path.

**Minimal correction:** retain all original 291 references/captures. Add a
separate bounded followup using an immutable existing fixture and the wide
schema (or another verified output exceeding an explicitly measured/controlled
pipe capacity). Establish client write progress with an undrained pipe
observation before starting the keepalive hold or sending the write-stage
signal; server Send completion alone is insufficient. Require the output-size
and pipe-capacity preconditions, exact final output/status and reached actions.
Use a small control that rejects a below-capacity payload. Correct the coverage
wording until that focused causal evidence exists. No production change,
pressure campaign or refreeze of old cases is necessary.

Witness inputs:
`/home/ajs/.cache/pb-phase6/tmp/harness-review-r4-full-v1/p6-blocked-writer-http2-progress/{go,candidate/repeat}/result.json`
and their `stdout.bin`; same run's `p6-signal-{int,term}-write` and
`p6-stdout-nonblocking`. `probes.py` reproduces the pipe/control results.

## CW-2 — Medium: garbage-header exemption removes unapproved stderr changes

**Measured pure comparator replay against captured R4 input.**
Locations: `harness/phase6.py:1066`–`:1081`; test
`harness/test_phase6.py:291`; declared byte/whitespace boundary
`harness/contracts/phase6.md:57` and `:101`.

`split_diagnostics` parses the stderr JSON, checks only `['error']`, then removes
the **entire** `stderr_contract_b64 differs` entry. It does correctly require
the single named case and exact inner code/message/retryability pair. However,
it does not require the outer envelope or surrounding byte framing to match.

The independent replay starts with the actual Go/R4
`p6-corrupt-garbage_header` results, leaves stdout, termination, requests,
configuration and user-agent untouched, and updates the mutated candidate
stderr hash/length consistently. Each of these becomes **zero unresolved
issues**, still counted as the accepted diagnostic difference:

- Append one additional LF to the candidate's stderr.
- Serialize its same error as compact JSON, removing established indentation
  and final LF.
- Add an outer `"unexpected":"unaccepted"` field beside `"error"`.

Negative controls changing the inner message, adding an inner error field or
changing the raw signal status remain rejected. Ordinary cases also reject
changed stderr. Thus the gap is precisely the single exemption; there is no
claim of a generic serializer bug or an actual changed R4 diagnostic.

**Contract impact:** extra terminal whitespace or an altered public error
envelope can silently pass as the one authorized Go slice-bounds versus Rust
alignment message difference. This is broader than the stated exact-pair
exception and contradicts the no-whitespace-normalization promise.

**Minimal correction:** bind the exception to the full two expected envelope
byte strings, including indentation, outer keys and final LF, or replace only
the exact encoded candidate message literal and compare every surrounding byte
with Go. Preserve the existing difference count and exact message pair. Add
mutation controls for the three independently reproduced false passes; do not
broaden the exception or normalize JSON generally.

Witness: `probes.json` → `garbage_header_exception`. The unchanged actual R4
pair passes, while the modified in-memory candidate results demonstrate the
unaccepted variants. No original result file was edited.

## CW-3 — Medium: authority normalization hides an incorrect port

**Measured pure comparator replay against captured R4 events.**
Locations: `harness/phase6.py:850`–`:854` and its use at `:901`; test
`harness/test_phase6.py:282`.

The function says it normalizes only the fixture-selected port, but it uses
`re.sub(r':\d+$', ':{{grpc_port}}', value)` without receiving or verifying
the fixture's actual selected port. Every numeric suffix is accepted.

The independent replay changes the raw R4 Basic DoGet event's authority to
`127.0.0.1:1`, leaving its actual recorded request, ticket, auth, output,
termination and every other event unchanged. It derives `flight_authorities`
with the same function used by `sample`. The normalized authority still equals
Go's `127.0.0.1:{{grpc_port}}`, and the complete current comparison reports
**zero unresolved issues**. A changed authority host is independently rejected.
The raw changed event remains visible in the probe design; the false pass is
caused by comparison normalization, not omission of the witness.

**Contract impact:** an ordinary incorrect HTTP/2 authority port can pass even
though that field participates in virtual-host routing. Host equality and
bare-host-versus-host/port checks survive, but the numeric port's relationship
to the discovered Flight endpoint is not checked. This is an unapproved wire
exemption, separate from the exact native user-agent ledger. No actual Rust
authority regression was found in R4.

**Minimal correction:** pass the expected authority/destination from the
fixture banner plus the selected scenario into the normalizer, and replace a
port only after it equals that scenario's discovered Flight endpoint port.
Do not accept every port offered by unrelated fixture endpoints. Preserve TLS
bare-host and IPv6 shape behavior. Add wrong-port controls for ordinary and
raw endpoints; all original good signatures can remain unchanged.

Witness: `probes.json` → `authority_port_exception`, using actual
`p6-auth-basic` captures.

## Evidence controls and honest coverage boundaries

**Original process inventories:** independently checked all 219 main, 56
framing/details/refusal and 16 trailer-followup entries. Their references still
match the corresponding full Go freezes. Checked **1,164 subprocess artifact
sets**: Go A, Go B, comparison Go and comparison R4 for each case. Rehashed
every raw stdout/stderr against its result, checked decoded versus raw OS
wait status, collector failure/action results, Go identities, frozen signatures,
candidate identities and fixture identity. Every saved event/trace drop count
is zero, so these bounded captures retain their request proof. Reapplied current
independent expectations and the exact UA/diagnostic split and matched the
published case-level ledgers.

The recount is 291 remaining-matcher passes, **36 strict**, **255 wire UA
differences**, **one diagnostic difference overlapping UA**; 36 + 255 = 291,
not 292. These are historical selected observations, not proof the harness
would reject every incompatible candidate (CW-1–CW-3).

Exact frozen reference hashes: main
`bee7263c606ea0cd68d3d4112e8e23e683866d995bd97204e33d7054bb1316d9`,
review3 `b307f62cc66b5dd2290754598f70050e06032f3343a0b557c858ed79cab2834d`,
trailer `0fe45a9e441e852818dc3bc69113c4e12ad07999b1603bb36854cdc06b858edd`.
R4 report hashes: main
`f8e1d12ee8b9c435e35dc3a142375908f58227b68ec9c4762baa15a6a3cdeff1`,
review3 `a89d0eec1b068a0f1f267c72ecf76e3ee67227efe45bbc68cea3441d2815a033`,
trailer `1328c0d918df8b4713469582d16f251abc6ec2224af7ded9ccae610150541061`.

**Byte/status/auth guards:** independent mutation controls reject wrong stdout,
ordinary stderr, exact ticket, metadata, authority host, timeout, raw wait status
and fixture panic. Current tests also reject missed actions, cap overflow,
changed/partial/wrong-mode freezes, wrong UA/version/count and raw SIGINT versus
exit 130. CONNECT head bytes never receive the native-UA exception. About/OAuth
sequence, configuration effects, request order/count and ticket escaping are
checked independently and compared to Go. The 68 status-stage captures also
passed separate independently constructed terminal-message, output-row/batch
and DoGet-count assertions: initialization OK means fatal EOF, established
OK/Canceled/Unavailable reach the gated second-subscription InvalidArgument,
and other codes retain their exact fatal diagnostic. The transparent-refusal
cases remain two physical DoGets with a sub-450-ms gate, distinct from the
450–2500-ms tail reconnect gate. Timeout is never used as passing completion.

**Signal-stage limit:** discovery/init gates causally hold their server
responses, and raw process signal outcomes are real. The reconnect signal is
armed only by a fixture `terminal(Unavailable)` event at `phase6.py:521`.
`finish` publishes that event **before** the status reaches the client
(`fixture/phase6.go:1410`–`:1442`). The capture proves signal death after the
server terminal event, not necessarily after client entry into its 500-ms
sleep. This is a source-supported evidence limitation, not a reproduced
production mismatch or a new general signal-hardening gate. Label it honestly;
a specifically claimed timer-stage probe needs a client-side causal boundary.

**Informational limits:** both protocol reports were rehashed, cached-Go/raw
streams revalidated and classification reapplied. Each has **two 200 controls**,
**two 103 limitations excluded from parity**, four UA differences, zero strict
passes and `full_parity_passed=false`. The fixed Go early Unknown/empty stdout
versus Rust established InvalidArgument/162-byte stdout is independently
asserted in both modes; equal exit 1 does not hide output or stage. This suite
is separate from the unchanged 291. It inherits CW-3's authority helper; its
exact stderr checks do not inherit CW-2's garbage exception. Non-native-endian
IPC is likewise explicitly unsupported where Go byte-swaps, not malformed-data
parity. These are already declared limits, not new objections from this lane.

**Retained corpus:** rehashed all five full R4 reports and their recorded binary,
runner, contract and completion-status identities. Recounted issue-free cases
and guards: Phase 1 2,018/9; Phase 2 219/6; Phase 3 684/8; Phase 4 161/4;
Phase 5 186/3. Total **3,268 cases and 30 guards**. Phase 3 includes nine actual
full60s cases, whose recorded Go/Rust runs have no collector failure and remain
in the real deadline window; its 13 known divergences are excluded. Phase 4's
full120s and Phase 5's full60s selection/signatures/report hashes were verified;
those report formats do not provide the same independently inspected durations.
No old long-timeout workload was rerun.

Phase 1 promotes only `tail` via `PHASE6_OPERATIONS` at `phase1.py:431`; the
interactive/login/dataset/SQL guards remain. The Phase 3 added bare-deadline
normalization is exact-case, exact second-hop Post URL and exact complete text
at `phase3.py:1487`, behind the preexisting `timeout_cause` option. The focused
tests verify near misses and retain request order, duration and termination
checks. No Phase 7 guard promotions are present. Normalization does not change
the ordinary cases or turn a collector timeout into a pass.

**Durable decoder evidence:** verified manifest inventory, sizes/digests and
generator/probe identities for values **44 cases/176 files**, r2 **10/40**,
r3 **30/120**, r4 **15/60**, and settled IA1 **25/101** (the IA1 file count
includes its generator fragment). Verified all 23 IA1 Go result entries against
durable stream/stdout/stderr/status files, with five recorded read errors,
and both original list-name controls unchanged from the interim audit.
IA1 manifest hash is
`d0b41729affb0dcf7580c45d7817419d14f494559e68c01cdec226a3087fe251`.
The decoder probe returns process 0 after read errors; docs/manifests correctly
use the final IPC read stage instead of treating that as successful decoding.
Also verified all nine saved negative-row outputs/diagnostics and probe/source
hashes. No durable oracle was regenerated or edited here. The complete-stream
tests and separate Go captures supply meaningful expectations; a schema-only
shared-ID success is not promoted into record-level compatibility.

**Binary/source chain:** standard Go
`fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`, Cloud Go
`622d4144b7e354c0ee4036244ba719cbbaf8357710804b1d733cd421a0dc227e`, R4 standard
`fe26926a98cba5e00774e26c5eab33c7c35ebd570ddaec7ceb38ddef663ecc43`, R4 Cloud
`1820a1fd6f2d96354778a2e63a47ad55d24bb8c1e3b911bfe236b4082e41bace`, fixture R4
`8237e34ee15b4a7d773945d3c30280752b2b780f1be3626d547b76cdea2ed4f0`, fixture R5
`c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314` were
rehashed. R4 standard/Cloud remain bound to the recorded complete source/build
manifest; settled source is explicitly different. The Go CLI commit association
is inherited from the preserved baseline manifests, not established by a new
rebuild. Read-only `go version -m` independently confirms Go 1.25.4, Arrow v13,
goccy v0.10.0 and grpc-go v1.65.0; its build info has no embedded VCS revision.
Verified fixture R5's immutable source copies, manifest, binary and exact
Go-toolchain hash. Current r5 source is not claimed to reconstruct fixture R4.

**Documentation:** acceptance, live Flight delivery/auth, continuity/exactly-once,
production integration, performance/resource envelope and macOS readiness remain
explicitly unclaimed. The implementation document has stale pending descriptions
for now-complete R4 retained gates and accepted scoped measurement tooling;
the linked evidence index has the later results. Those stale descriptions are
conservative, not false acceptance. Update them when assembling the final settled
report, retain R4's historical association, and correct CW-1's coverage claim.
The measurement tooling's scoped acceptance is not campaign admission. This
review neither reopens it with hypothetical hardening nor supplies a quiet window.

## Commands and verification limits

Executed from the named worktree with:

```sh
export TMPDIR=/home/ajs/.cache/pb-phase6/review-wave-contracts-sol/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
export PYTHONDONTWRITEBYTECODE=1
python3 /home/ajs/.cache/pb-phase6/review-wave-contracts-sol/run_tests.py
python3 /home/ajs/.cache/pb-phase6/review-wave-contracts-sol/probes.py
python3 /home/ajs/.cache/pb-phase6/review-wave-contracts-sol/audit_artifacts.py
python3 /home/ajs/.cache/pb-phase6/review-wave-contracts-sol/supplemental_checks.py
GOTOOLCHAIN=local GOPROXY=off \
  /home/ajs/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64/bin/go \
  version -m /home/ajs/.cache/pb-phase6/baselines/pb-go
```

Each command's stdout/stderr was redirected to its corresponding owned cache
log (`tests.log`, `probes.log`, `artifact-audit.log`, `supplemental-checks.log`,
`go-build-info.log`). The test runner selects all `test_phase6`, all
`test_phase6_protocol_limits`, `test_phase1.CorpusTests` and
`test_phase3.DeadlineTierTests`: **52 passed, zero failures/errors/skips**.
The separate probes require the three false-pass witnesses and their negative
controls to reproduce; artifact and supplemental assertion scripts exit 0.
Scripts and logs are retained so commands are inspectable and repeatable.

These executions do not rerun the 291 native trials, all-target Rust tests,
full retained native corpus, long deadlines, Go generators, TLS/live services or
performance campaigns. Hash checks verify preserved evidence and current pure
admission behavior; they do not establish universal protocol/decoder parity.
CW-1–CW-3 need focused owner corrections and independent verification before
their affected evidence claims can be accepted. Fresh settled-candidate
integration evidence and the required final whole-phase Opus review remain
separate parent-owned work.
