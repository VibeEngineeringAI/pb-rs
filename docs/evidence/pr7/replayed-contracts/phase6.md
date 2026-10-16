# Phase6 tail subprocess contract

This suite targets native `pb tail DATASET [-o text|json]` from Rust base
`4a812d5`. Its oracle is Go `07af390`, Arrow Go v13 and grpc-go v1.65.0.
The final Phase6 roadmap supersedes historical streaming proposals. This
suite adds no product command, filter, capture, ingestion or resume behavior.

## Evidence and acceptance

`harness/phase6.py` starts the real prebuilt Go and Rust executables against
independent real HTTP/Arrow Flight fixture processes. Every run gets a fresh
HOME/config, synthetic credentials, fixture scenario and request ledger.
Standard and Cloud baseline hashes are checked against the preserved
Phase6 binaries. The fixture is supplied by the parent; the runner never
builds executables or runs benchmarks/profilers.

The checked-in `phase6-reference.json` is an evidence manifest. A completed
freeze requires the full inventory, independent contract checks and two Go
runs per case. Partial characterization reports remain in the cache and
cannot replace the accepted reference. `compare` re-runs Go before Rust;
oracle drift, fixture drift, corpus changes and incomplete references fail.
Candidate and fixture hashes are fixed before the comparison, and executable
changes during a run invalidate it. Immutable cache copies are recommended.
Compilation, fixture setup, causal correctness delays and OS wait status
observations here do not establish latency or resource comparisons.

## Independent assertions

- Discovery is an authenticated GET about before Flight. Its expected request
  count/order is separate from the measured oracle; no rediscovery happens
  on a Flight reconnect. Non-200 and malformed about replies are fatal.
- Tickets are exact Go JSON bytes, including HTML and U+2028/U+2029 escaping.
  Basic, self-hosted API key, Cloud API key and Cloud OAuth metadata are
  checked individually. Cloud Flight uses TLS even with a plaintext HTTP
  profile URL. OAuth discovery refresh must persist the new session token,
  snapshot it for Flight and never refresh on Flight Unauthenticated.
- Output hashes/byte counts must match the fixture's separate Arrow Go
  `RecordToJSON` plus one LF per batch. Empty batches, duplicate fields,
  partial NaN batches and dictionary updates retain their actual framing.
  Complete rows and blank batch separators are counted incrementally; the
  next batch can be withheld until the first complete batch is visible.
- All 17 status codes are exercised before response headers, after response
  headers during reader initialization, after schema and after batches.
  Before initialization finishes they are fatal. Established EOF/OK,
  Canceled and Unavailable reconnect; all other codes are fatal. Retry
  scenarios deliberately end in InvalidArgument on the second subscription.
  They assert two DoGets, identical tickets/auth and a 500 ms delay, with a
  declared 450–2500 ms scheduler tolerance measured from fixture terminal
  event to subsequent DoGet. A timeout never establishes reconnect parity.
- Process actions wait for fixture stage events or a complete output batch.
  SIGINT and SIGTERM cover discovery/initialization/idle server gates and
  write/reconnect-associated server events. The historical write event does
  not prove client blockage; the reconnect terminal event precedes client
  receipt and does not prove entry into its 500 ms sleep. The runner owns `wait4` and compares both the
  raw wait status and decoded signal/exit. Exit 130/141 cannot substitute
  for signal death. SIGPIPE is exercised using a pipe with no reader.
- Writer cases use actual `/dev/full`, closed fd 1, inherited O_NONBLOCK,
  broken pipes and raw PTYs. Text stderr is checked through both pipes and
  PTYs. No ANSI, whitespace, blank-line or JSON-format normalization exists.
- TLS/ALPN and plaintext/Cloud-TLS/HTTPS CONNECT proxies use disposable
  certificates and a synthetic hostname. The local proxy forwards to the
  loopback fixture, so no DNS or system hosts changes occur. It records exact
  CONNECT request bytes, including proxy Basic auth and grpc user-agent.
- HTTP/2 `:authority` retains the Go TLS bare-host versus plaintext host/port
  distinction. FIN, RST, excluded-stream GOAWAY and graceful drain follow
  schema/batch delivery and reconnect to a deliberately fatal subscription.
  Large trailers carry a 64 KiB message plus 8 KiB extra metadata. The
  historical paused-consumer test with 1-second PING/300-ms ACK timeout keeps
  the connection alive for 1.8 seconds, but its 21,975-byte basic output can
  fit inside a pipe; its captures do not record capacity or prove blockage.
- Preface blackholes have separate signal and actual connection-deadline
  cases. Deadline cases send no signal, allow 30 seconds to collect the
  real result and require fatal exit within 18–26 seconds. The four initial
  Go runs exited normally after approximately 20.0 seconds in plaintext
  and TLS; a collector timeout is always a failure.

## Capture bounds and normalization

Each stream is spooled to disk with an incremental SHA256. Stdout has a hard
32 MiB limit and stderr a hard 1 MiB limit; memory prefixes are 64 KiB each.
Line counting carries only the current line's byte count, never the whole
record. Fixture event reads are bounded by its 1024-event ring. Proxy request
heads are limited to 64 KiB and its ledger to 32 requests. Overflow, timeout,
unreached causal actions, startup failure and descendant-held pipes fail.

Only explicitly identified disposable HOME and fixture endpoints are replaced
in configuration/diagnostics. CONNECT targets replace the two ephemeral
ports. Socket errors replace only source socket identities recorded by
fixture events, preserving connection ordinals, and destinations identified
by the startup banner. Error text is otherwise unchanged. Original stream
byte hashes and raw artifact files remain available.
Request comparison retains order, count, auth and ticket bytes; platform
user-agent events are asserted as `grpc-go/1.65.0` and included in the frozen
signature. No credentials or capability
URLs from live environments enter the repository.

Known diagnostic differences must be declared per scenario and kept outside
parity counts. A generic transport-error or ANSI-stripping exemption is not
permitted. The parent explicitly declared tonic's HTTP/2 user-agent as a
separate wire difference: raw event values and both signatures remain
available, while `payload_auth_passed` excludes only that header. Such cases
increment `wire_ua_divergent` and never `parity_passed`, which remains the
strict count. CONNECT user-agent bytes are always compared exactly. No other
diagnostic exemption is accepted except `P6-DIAG-GARBAGE-HEADER`, approved by
the parent for the single `p6-corrupt-garbage_header` scenario. Go's exact
slice-bounds diagnostic and Arrow-rs's exact safe-verifier alignment diagnostic
are checked as a fixed pair; code, termination, stdout, requests and config
still must match. It increments `diagnostic_divergent`, never strict parity.
The exemption now checks the complete two envelope byte strings, including
outer keys, indentation, escaped message, and final LF. Extra LF, compact JSON
and extra outer fields remain failures. Changed diagnostics or other malformed
scenarios remain failures.
Version-specific deployed authorization, delivery
loss and continuity are independent integration questions that a loopback
fixture cannot answer.

## Repeatable commands

All generated artifacts live under the cache, never `/tmp`. Use a new output
directory for each run. The parent coordinates fixture/candidate builds and
quiet windows; this runner should stop launching work during those windows.

```sh
export TMPDIR=/home/ajs/.cache/pb-phase6/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
python -m unittest discover -s harness -p test_phase6.py -v
python harness/phase6.py list
python harness/phase6.py check-reference \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go \
  --go-cloud /home/ajs/.cache/pb-phase6/baselines/pb-go-cloud \
  --fixture "$TMPDIR/pb-fixture-harness-r4" --out "$TMPDIR/p6-characterize" \
  --case p6-auth-basic --case p6-framing-empty-mixed
python harness/phase6.py freeze \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go \
  --go-cloud /home/ajs/.cache/pb-phase6/baselines/pb-go-cloud \
  --fixture "$TMPDIR/pb-fixture-harness-r4" --out "$TMPDIR/p6-freeze"
python harness/phase6.py compare \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go \
  --go-cloud /home/ajs/.cache/pb-phase6/baselines/pb-go-cloud \
  --fixture "$TMPDIR/pb-fixture-harness-r4" --candidate /absolute/path/to/pb \
  --candidate-cloud /absolute/path/to/pb-cloud \
  --out "$TMPDIR/p6-compare"
```

## Characterized boundaries

The first 160-case Go A/A run completed against the typed-nil-fixed fixture.
158 cases passed their independent assertions. Two exposed incorrect harness
assumptions: fixture Send completion does not prove that the reader consumed
that batch. The corrected cases pin these observed outcomes explicitly:

- One initial metadata-only message is skipped; a subsequent schema/batch
  renders normally. Two initial empty headers fail reader initialization with
  no stdout and `arrow/flight: could not create flight reader: arrow/ipc:
  unknown error while reading: runtime error: index out of range [3] with
  length 0`. There is one DoGet and no retry.
- Metadata-only after the first batch retains that batch, then fails with
  `arrow/ipc: unknown error while reading: runtime error: index out of range
  [3] with length 0`. There is one DoGet and no retry; later sent batches
  never become stdout.

The expanded freeze covers 12 rows of the values schema, exercising
every fixture table entry, and four NaN-schema rows, proving that later rows
in an invalid-number batch are discarded. The full manifest reports the
freeze outcome, fixture identity and exact signatures. The initial 164-case
Go freeze passed all 328 subprocess captures. The transport review extends
the inventory to 182 cases; intermediate reports remain separate cache
artifacts and only a successful full run replaces the checked-in manifest.
The final r3 freeze passed all 182 cases (364 subprocess captures) against
fixture SHA256
`fdecd359edf1461f892197830ca46fde2855ce87b7d9179cc3520e3ddae83fc9`.
That historical evidence remains under
`/home/ajs/.cache/pb-phase6/tmp/harness-go-r3-v1`. The current checked-in
manifest is the full **219-case r4 Go A/A freeze**, 438 subprocess captures,
against immutable fixture SHA256
`8237e34ee15b4a7d773945d3c30280752b2b780f1be3626d547b76cdea2ed4f0`.
Raw captures remain under
`/home/ajs/.cache/pb-phase6/tmp/harness-go-r4-full-v1`.

The added 37 cases use a real scripted HTTP/2 endpoint. They preserve raw
invalid-UTF-8 diagnostic bytes, invalid details-bin, absent/malformed status,
RST(NO_ERROR), GOAWAY and HTTP status/content-type combinations. Go's JSON
errors replace each malformed message byte with an ASCII `\\ufffd` escape;
text errors preserve the invalid bytes. A literal UTF-8 replacement character
or a replacement grouped across multiple malformed bytes fails comparison.

The immutable review-r2 candidate comparison completed all 219 cases:
207 standard and 12 Cloud. It reports 218 passing remaining contracts,
35 strict passes, 183 explicit wire user-agent differences and the one
malformed-header diagnostic difference (overlapping a wire difference).
The sole failure is plaintext-to-TLS preface reset text: Go reports the
server-preface TCP connection reset; Rust reports `transport error`.
No exemption is applied. Complete raw evidence is in
`/home/ajs/.cache/pb-phase6/tmp/harness-review-r2-full-v1`.
The candidate identities are standard SHA256
`729146e310912337655a1046a38aca71909cacd285873e3957f91b14a27ddc0b`
and Cloud SHA256
`eaedfd8b082d49851fb014729c674ff57283be6dca31048d831fcfa8714521c5`.

Two additional inventories remain separate from the 219-case reference:

- `--suite trailer-followup`: 16 schema/batch × status 0/1/14/3 × immediate
  FIN or GOAWAY-below+FIN cases. The fixture uses Go's HPACK encoder and
  continues its script after trailers. All Go A/A cases and the immutable
  review-r2 candidate pass the remaining contract; all 16 retain the explicit
  wire user-agent difference. No trailer-loss exception exists.
- `--suite review3-followup`: 56 cases, all passing Go A/A. Thirty send 3/5
  prefix bytes, a full prefix declaring an absent 8-byte body, or 3/8 body
  bytes. They cover initialization and an established batch, trailers with
  statuses 0/1/14/3 and DATA END_STREAM. Every case makes one DoGet and exits
  with Internal `unexpected EOF`; established cases retain the complete first
  batch. Eighteen exercise matching protobuf status messages, mismatched
  status codes, empty and duplicate details-bin, malformed protobuf and
  invalid UTF-8 inside a protobuf string. Eight exercise GOAWAY-below and
  RST(REFUSED_STREAM) before headers and after headers before any message.
  Go transparently retries exactly once: two DoGets, with observed gaps
  0.249–0.777 ms in the initial 16 captures. Two refusals stop after the second;
  the runner rejects a third attempt or a tail reconnect delay of 450 ms or
  longer. These gaps are causal correctness evidence, not performance data.

Followup suites require an explicit cache reference, preventing replacement
of the main freeze. The review-r2 candidate comparison of review3 probes fails 46 of 56
cases; those failures remain evidence and receive no additional exemption.
The protobuf mismatch diagnostic is retained byte-for-byte, including its
prototext spacing. A Go A/A change in that text invalidates the freeze.

```sh
python harness/phase6.py freeze --suite review3-followup \
  --reference "$TMPDIR/review3-followup-reference-r4.json" \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go \
  --fixture "$TMPDIR/pb-fixture-harness-r4" --out "$TMPDIR/p6-review3-freeze"
python harness/phase6.py compare --suite review3-followup \
  --reference "$TMPDIR/review3-followup-reference-r4.json" \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go \
  --candidate /absolute/path/to/pb --fixture "$TMPDIR/pb-fixture-harness-r4" \
  --out "$TMPDIR/p6-review3-compare"
```

Use the same commands with `--suite trailer-followup` and a distinct cache
reference/output directory for the 16 trailer cases.

The immutable **review-r3 intermediate** standard candidate passes all 56
review3 probes and all 16 trailer probes against their unchanged frozen r4
references. Each case still records the explicit wire user-agent difference;
there are no new diagnostic exclusions. Its SHA256 is
`90932a37560eda457dd469496b312f742d166a547b63e5023c76a05fce103578`.
Reports and raw captures are retained in
`/home/ajs/.cache/pb-phase6/tmp/harness-review-r3-review3-v1` and
`/home/ajs/.cache/pb-phase6/tmp/harness-review-r3-trailer-v1`.
The full review-r3 main comparison also passes all 219 remaining contracts
(207 standard, 12 Cloud). It reports 36 strict passes, 183 wire user-agent
differences and the single exact malformed-header diagnostic difference,
which overlaps a wire difference. The plaintext-to-TLS reset diagnostic
and real plaintext/TLS 20-second preface deadline cases pass. Cloud SHA256 is
`0f82900fd19ed4b28798f3a0762ef0c5d34a20a978e90b0737bfd69378974653`.
The main report is
`/home/ajs/.cache/pb-phase6/tmp/harness-review-r3-full-v1/report.json`;
the combined 291-case summary is
`/home/ajs/.cache/pb-phase6/tmp/harness-review-r3-combined-summary.json`.
All old failure reports and all three Go references remain unchanged.

These bounded corpus results do not establish final acceptance of decoder
cases outside the inventories, sustained operation or performance. The fresh
decoder review's big-endian and conflicting-dictionary cases are outside
these inventories and are being fixed separately by their owner.

The initial r2 plaintext-to-TLS probe showed a fixture close race: Go received
either server-preface EOF or TCP connection reset, depending on unread
preface bytes when the TLS handshake failed. Both raw outcomes remain in
the cache. The final fixture uses opt-in `tls.reset_invalid_handshake` only
for that case, forcing an RST and making the diagnostic deterministic.
Neither outcome is hidden by a broad transport-error exemption.

## Remaining evidence

Go characterization and a frozen reference do not establish Rust parity;
the candidate comparison must pass independently. Sustained slow-sink progress,
dictionary resource growth, cross-platform signals and deployed Flight
authorization/delivery remain separate acceptance tiers. The measurement
peer owns the resource and sustained-operation runner.


## Contracts review CW1–3 corrections and separate causal evidence

The original 291 inventories, references, captures and reports are preserved.
Their historical writer/nonblocking cases use basic schema, whose fixed message
ignores `payload_bytes`: 128 rows produce 21,975 bytes. Their paused consumer
and server Send signals do not prove a blocked client writer or EAGAIN. The
original reconnect signal proves signal death after a server terminal event,
not necessarily during the client's sleep. No timer-stage claim is made.

`phase6_blocked_writer.py` adds four separate writer cases against immutable
fixture r5, plus two fresh authority controls. Wide schema uses the actual
payload size and an independent Go uint64 xorshift/JSON model. The stdout pipe
is set to 4,096 bytes with F_SETPIPE_SZ and measured with F_GETPIPE_SZ before
launch. All probes record requested/actual capacity and pending-byte observations.
The large row/batch is exactly 16,413 bytes. Before the hold or signal, the
reader remains undrained, FIONREAD equals capacity for at least 100 ms, the
process is alive, and a worker is observed in `pipe_write` or `anon_pipe_write`
with Linux x86-64 write/writev syscall on fd 1. This is a client-side blockage
witness; server Send completion alone never arms these actions. The recorded
proof precedes every action timestamp.

The keepalive case then holds the same blocked writer for 1.8 seconds under
1-second PING/300-ms ACK policy, requires the writer still blocked and no
connection end, resumes stdout and releases the server gate. Its exact full
output, one row/blank batch and fatal InvalidArgument envelope pass against Go
and R5. SIGINT/SIGTERM are sent only after the blocked witness; both retain
exactly the first 4,096 output bytes, zero complete rows/batches, empty stderr
and raw signal statuses 2/15. The 157-byte below-capacity control records queued
bytes below capacity and explicitly rejects the blocked interpretation before
resuming to the same exact fatal/output checks. An erroneously requested
blocked action for a small payload remains a failed/missed action, not a pass.
All cases retain independent request/ticket/auth/config/status gates.

The focused R5 report is
`/home/ajs/.cache/pb-phase6/tmp/harness-cw-followup-r5-v2/report.json`.
It passes six remaining contracts, all with the separate exact native-UA
ledger. The first attempt is preserved in `harness-cw-followup-r5-v1`: requiring
the obsolete exact kernel name `pipe_write` failed the proof and actions, even
though the pipes filled. The parent-owned kernel witness showed
`anon_pipe_write`; the corrected test retains the fd-1 syscall check and never
turns a timeout/missed action into passing evidence. These are small functional
probes, not performance or resource measurements. The R5 production binary has
separate newly found missing-dictionary failures; this focused pass is not final
source or phase acceptance, and a final immutable successor needs replay.

Authority normalization now takes the scenario-selected actual discovery port.
`sample` replays the selected about route/auth rules against the immutable
fixture banner and trace, storing `fixture_info` and `discovered_flight_port`.
Only an exact matching suffix becomes `:{{grpc_port}}`; bare TLS hosts, bracketed
IPv6 and wrong hosts/ports retain their shapes. Other offered fixture ports are
not accepted. The ordinary and raw controls preserve valid frozen signatures
and reject wrong-port mutations, including the unused ordinary gRPC port for a
raw endpoint. `sample`'s call signature stays unchanged.

The separate `saved-reduction.json` rechecks all 291 saved R5 cases with the
corrected complete-envelope matcher and retains their original count/ledger.
Those historical captures have no independent banner/destination-port
provenance, so the reduction explicitly does not newly certify their old
normalized authorities. Fresh selected-port controls supply that missing
focused evidence. Old references/signatures remain unchanged. No corpus is
refrozen and no generic JSON or endpoint normalization is added.

```sh
export TMPDIR=/home/ajs/.cache/pb-phase6/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
python harness/run_tests.py
python harness/phase6_blocked_writer.py \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go \
  --candidate /home/ajs/.cache/pb-phase6/candidate-review-r5/pb \
  --fixture "$TMPDIR/fx/pb-fixture-r5" \
  --reduce-saved "$TMPDIR/harness-review-r5-campaign-v1" \
  --out "$TMPDIR/harness-cw-followup-fresh"
```

Use a fresh cache output directory and a new immutable candidate for replay.
The Linux fd-1 syscall/wait-channel probe is intentionally platform-specific;
no macOS or universal signal/blocked-writer support is established.
