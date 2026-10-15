# Phase 6 contracts review — independent CW1–CW3 correction round

**Scoped disposition: accept the CW1–CW3 harness corrections. No unresolved
objection remains in this correction lane.** This does not grant production or
whole-phase acceptance. R5 remains pre-fix evidence for the separate W1/W2 and
F1/F2/F3 findings; an immutable successor R6 and focused replay remain required.
The original [review](phase6-review-wave-contracts-sol.md) is preserved unchanged.

## Scope and exact snapshot

Work was confined to `/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail`.
HEAD/base: `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`; Go oracle:
`07af390ab09bda8ac9e427ffc6310077bace0125`. Read the complete original contracts
review, cached final roadmap, implementation document, corrected harness and
tests/contracts, current worker/handoff source, relevant fixture/discovery and
keepalive source, immutable fixture source copies, prior measurement-review
limits, and the separate fresh decoder/transport findings. Prior conclusions
were checked against source and artifacts. The unfinished H2 capability-limit
module was not reviewed.

Reviewed SHA256 identities, unchanged through this round:

- `harness/phase6.py`: `444ac8853cc4d03b02d6ee6f24cedfe4b89f2c690d1b285c03f2b33752f81147`.
- `harness/test_phase6.py`: `3b7051d5fa2691ca2218465f02c7f64820dad64fe9b9cf07a1dcb2e0fddf465b`.
- `harness/phase6_blocked_writer.py`: `09d01c6c0cb6d59a5506307541da3b100513dbc9fdce7ea09d72d9b3b2540989`.
- `harness/test_phase6_blocked_writer.py`: `560d28cfd0518f73b9762d172d61642a15708163f2666d88812438637f9e0e27`.
- `harness/contracts/phase6.md`: `682daff27dc7a945aea1be2a64826784270beec19e5fa1af7ae4d219a1583b4c`.
- Original review: `32ce420cd64e63cb7a0c9f9c06b1563c7631c3b16cd4464b2cc072163f2b5d68`.

The independently hashed response `tmp/impl-cw-final-evidence.json` is
`d45140ec7291c85f3be13cb25bb1bdfd2b9d9ad49ae8057d9dcbbf686ba67099`.
The actual focused report `tmp/harness-cw-followup-r5-v2/report.json` is
`774697980c76321a90e3371a30a00ef6c3e624a00ec0bf329924c2d5e82f2151`.
Its runner identity equals the reviewed `phase6.py`, and its six cases bind
**12 actual captures**, not six synthetic result objects.

Rehashed executables and immutable fixture source/compiler chain:

- Go standard: `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`.
- R5 standard: `0a5877073979ee43e518b40908ae7274286144a9799137202d71a4310b68e825`.
- Fixture r5: `c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314`.
- Immutable fixture `phase6.go`: `8e77b9681cadb9bdc7a87fa1ebbbd6520d87fbca3b7ed2298f20af3ba9e12eb3`.
- Go 1.25.4 executable at `/home/ajs/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64/bin/go`:
  `06c386d4ead3c7e28565a544d0e7693d5998f060bc75029a5e86b52b4f824767`.

Source checks inventory 92 inputs in cache `before.json` and `after.json`.
Concurrent owner edits changed `docs/rust-phase6.md`, transport notes and
`src/tail/transport.rs`;
all correction inputs above, the original review, references, and worker source
remained identical. Transport moved from
`77dd92bb0bf34c620bcb2ca259347f0d6b27d83d72593a76e912984858165240` to
`8955ab57e1cea8fda4eaa4427ca099771518ac870d05f4a8e5259e00970fb681`.
That was an intermediate check; later concurrent transport revisions are recorded
in `finish-v3.json`. Those ongoing production changes receive no acceptance here.

## CW1 — closed: causal blocked-write evidence replaces the unsupported claim

At `harness/phase6.py:239` and `:314`, the followup controls pipe capacity using
F_SETPIPE_SZ/F_GETPIPE_SZ, leaves stdout undrained, observes FIONREAD, and scans
process tasks for an actual fd-1 write/writev syscall in `pipe_write` or
`anon_pipe_write`. The full pipe must persist at least 100 ms, expected output
must exceed capacity, and the process must be live. A full pipe or server Send
event alone cannot arm `pipe_blocked`. The state is rechecked while paused;
the final hold action asserts the writer is still blocked before resumption.

Independently checked all eight writer captures against raw bytes, status,
fixture scenario/events, independent output construction and current admission:

- Capacity is exactly **4,096**; large wide output is **16,413** bytes. Its SHA256
  is `f990cb9b41deacb48e63027132ff8301e70b67d88e354571c46ef05e99e82e51`.
- Go and R5 keepalive proofs follow full-pipe intervals of **103.794744 ms** and
  **103.843503 ms**. Their holds begin at the witness and last **1,801.157187 ms**
  and **1,801.401484 ms**. The saved policy is 1-second PING/300-ms timeout;
  no connection-end event precedes hold completion. Resume/release reaches the
  exact full output, one row/blank batch, InvalidArgument envelope and raw 256.
- INT/TERM actions follow real worker-write witnesses. Both binaries retain the
  identical first **4,096** bytes, SHA256
  `c49d34fc43c2faa77a22e76509070be8c3721cc1ac9cf9b11c329f07bbc4bce3`,
  containing **zero LF**, zero complete rows/batches and empty stderr. Raw wait
  statuses are **2/15**, not conventional numeric exits.
- The below-capacity control has **157** bytes pending, no blocked proof, and
  explicitly rejects blockage before resuming to exact output/fatal checks.

My separate small Python controls exercise the actual collector, without Go,
Rust or fixture execution: a full stdout pipe with no pending write is rejected;
a full stdout pipe while a task blocks writing a different fd is also rejected.
Both deliberately terminate as collector failures, never passing timeouts.
Non-main worker fd-1 `write` and `writev` each produce a valid witness before
resume, retaining exact output. These controls establish that the syscall/fd
condition contributes evidence beyond pipe fullness/readiness.

Limits are documented in `harness/contracts/phase6.md:282`: Linux x86-64 procfs,
fd-1 syscall/wait-channel observation; no universal or macOS claim, EAGAIN claim,
soak, pressure or resource claim. HTTP/2 progress is supported by survival and
successful completion under the configured real keepalive policy and its
immutable fixture control source; these captures do not contain a packet-level
PING/ACK count. Historical 21,975-byte paused-consumer captures remain historical
and do not acquire blocked-write proof. Reconnect signal events still do not
prove entry into the client's sleep. No new gate is imposed for these limits.

## CW2 — closed: the exemption binds every stderr byte

`harness/phase6.py:1178` constructs the two exact complete error envelopes and
compares decoded bytes, including outer keys, indentation, escaped message and
final LF. It removes only `stderr_contract_b64 differs` for the single named
case. It neither parses away the envelope nor changes other differences.

Replayed the actual saved Go/R5 garbage-header pair. The unchanged pair receives
the one declared diagnostic difference. Independently applied extra LF, compact
JSON and an extra outer field to **each side**: all six mutations are rejected,
with lengths/hashes updated consistently. Every mutation preserves the inner
error object, showing why the old inner-only check admitted it. The focused
unit suite also checks case/message near misses and unrelated differences.
No new diagnostic waiver or whitespace normalization is present.

## CW3 — closed: selected-port normalization, with honest historical limits

`harness/phase6.py:914` now requires the scenario-selected port. Discovery is
replayed from the immutable fixture banner and the observed about requests,
including response selection/auth/exhaustion, rather than inferred from client
authority or every offered endpoint. The capture saves both `fixture_info` and
`discovered_flight_port` before forming authorities.

Independently verified raw metadata for every fresh capture. Ordinary controls
select the banner gRPC port; the raw control selects its raw endpoint, excluding
the unused ordinary gRPC port. Wrong numeric ports remain literal and produce
`flight_authorities differs`; replayed mutations also reject unrelated offered
ports. Pure checks retain bracketed IPv6 host/port, unbracketed/bare IPv6,
bare TLS hostname and unknown-port behavior. Host and port-presence distinctions
remain observable. The public `sample` call signature is unchanged.

The fresh report is **six remaining-contract passes, zero strict passes, six
exact native-UA differences**. The exact grpc-go/tonic pair remains its own
wire ledger; it does not waive wrong authority, output or diagnostics.

## Saved evidence and verification

Independently rehashed all 582 Go/R5 historical stdout/stderr capture pairs,
checked raw versus decoded wait status, actions/failures, event/trace drop
counts, frozen Go signatures, binary identities, current independent
expectations and the exact UA/diagnostic split. Recomputed all three inventory
hashes and reproduced the saved reduction exactly:

**291 remaining-contract passes; 36 strict; 255 UA differences; one diagnostic
difference overlapping UA. Zero historical authorities newly certified.**
The reduction explicitly retains absent independent selected-port provenance
and `full_parity_passed=false`; it does not reconstruct banners from normalized
signatures. Historical reports, signatures and reference bytes are unchanged.
Reference SHA256 values remain:

- Main: `bee7263c606ea0cd68d3d4112e8e23e683866d995bd97204e33d7054bb1316d9`.
- Review3: `b307f62cc66b5dd2290754598f70050e06032f3343a0b557c858ed79cab2834d`.
- Trailer: `0fe45a9e441e852818dc3bc69113c4e12ad07999b1603bb36854cdc06b858edd`.

The successful saved reduction SHA256 is
`f5124a4032542c304aad2eb3c24afb9bdc6aae075c983904c9d509a7e9c61655`.
The failed owner v1 remains preserved: three failed cases, not admitted evidence.
Its obsolete exact `pipe_write` requirement was corrected for the observed
`anon_pipe_write` kernel symbol while retaining fd/syscall checks.

All new reviewer outputs are in
`/home/ajs/.cache/pb-phase6/review-wave-contracts-sol-r2`. Independent
[artifact identities](/home/ajs/.cache/pb-phase6/review-wave-contracts-sol-r2/artifact-identities.json)
bind 1,944 inputs/artifacts. Results are in
[audit.json](/home/ajs/.cache/pb-phase6/review-wave-contracts-sol-r2/audit.json),
[causality.json](/home/ajs/.cache/pb-phase6/review-wave-contracts-sol-r2/causality.json)
and [tests.json](/home/ajs/.cache/pb-phase6/review-wave-contracts-sol-r2/tests.json).
The independent focused suite passes **40 tests, zero failures/errors/skips**;
the owner's preserved 143-test log was inspected, not substituted for that run.

Commands executed from the named worktree:

```sh
export TMPDIR=/home/ajs/.cache/pb-phase6/review-wave-contracts-sol-r2/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
export PYTHONDONTWRITEBYTECODE=1
cache=/home/ajs/.cache/pb-phase6/review-wave-contracts-sol-r2
timeout 60s python3 "$cache/run_tests.py" > "$cache/tests.log" 2>&1
timeout 60s python3 "$cache/audit-v2.py" > "$cache/audit-v2.log" 2>&1
timeout 60s python3 "$cache/causality-v3.py" > "$cache/causality-v3.log" 2>&1
timeout 60s python3 "$cache/finish-v3.py" > "$cache/finish-v3.log" 2>&1
```

The scripts set `phase6.TMP` to the owned cache. Each completed within 60 seconds.
Earlier reviewer scripts/logs remain: the first audit overconstrained periodic
versus live fixture summaries; protocol fields agree after excluding documented
changing snapshot counters. The first causal script had a syntax typo; its next
attempt incorrectly required a cleanup/resume action to stay unmet after the
first action failed. The final check correctly requires the blocked action to
fail and collector failure to remain set. None was a harness false pass.

No agents, shared source/test edits, commits, builds, native client/fixture
reruns, production mutations, external posts, credentials, performance campaign,
quiet token, common lock, `/tmp` changes, rate limit or EDQUOT occurred. This new
document and the named cache are the only reviewer writes. W1/W2, F1/F2/F3,
capability limits, successor replay, final Opus review, live integration and
resource/performance acceptance remain separate parent-owned work.
