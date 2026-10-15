# Phase 6 review wave, round 2: Flight transport (Opus)

This is an independent follow-up to
[the round-1 transport review](phase6-review-wave-transport-opus.md). It
re-reviews the bounded correction for F1–F4 and I1–I2 recorded in
[the transport notes](phase6-transport-notes.md) ("Review wave (transport, Opus)
dispositions").

This is a lane review, not final acceptance. I edited no production source,
tests, harness, notes or shared evidence. The only files I wrote are this report
and `/home/ajs/.cache/pb-phase6/review-wave-transport-opus-r2/NOTES.md`. I did
not read or judge the values scope (`values.rs`, V1/V2), which is still moving.

## Snapshot and limits of this review

**No shell was available.** Every Bash invocation in this session failed before
running. Claude Code's own temporary directory under `/tmp` hit its disk quota
(`EDQUOT`), and the brief forbids `/tmp` surgery, so I did not clear it. As a
result:

- **I recomputed no hashes.** The figures below are quoted, with their source
  named.
- I ran no cargo, unit or harness command. The owner-reported counts are
  **not reproduced** here: 37 transport tests ×3, 290 lib / 294 diag / 1
  ignored, fmt and clippy passing, and the disabled-fix negative controls
  failing.
- Every finding in this report is **source-only**. Process evidence was read
  from saved artifacts and was not re-run.

| Item | Value | Source |
| --- | --- | --- |
| Settled `src/tail/transport.rs` | `09bb4c9d62be39905fee73530b8c7c9ff986ca1afe3f94bd2e7f3716df5348a0` | Root/owner brief. **Not recomputed.** I reviewed the file content on disk during this session. |
| Base | `4a812d5` | git status context |
| Frozen Go 07af390 `baselines/pb-go` | `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314` | Saved `binary_sha256` in `transport/probe/runs/cand-v1/report.json`; also the `probe.py` hash gate |
| Fixture r5 | `c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314` | `probe.py` hash gate |
| Debug candidate behind `cand-v1` | `6c4b5ec3dbd42a87413f3f0fc87a30e8b816922f310b6cb5ecbed0c6cd082108` | Saved `binary_sha256` in `cand-v1/report.json`. That it was built from `09bb…` is the owner's claim, which I did not verify. |
| Locked dependency sources read | h2 0.4.20, tonic 0.14.6, x/net v0.30.0, grpc-go v1.65.0 | cargo registry and Go module cache |

The debug candidate is not a release, so `cand-v1` is supporting evidence only.
The successor replay of the 291 original contracts, of
`harness/phase6_h2_limits.py` and of the new `harness/phase6_transport_wave.py`
is **still pending**. I noted the root's promotion of the 15 owner scenarios
(`docs/phase6-transport-wave-contracts.md`,
`docs/evidence/phase6/transport-wave-debug-reduction.json`). I read their
scope; I did not re-run or re-hash them. They support this review but add no
scope to it.

## Prior objections

### F1 announced `grpc-encoding`: closed (source plus saved measurement)

I checked each point the brief lists against the source.

- **The header is stripped, and the last value is kept.**
  - `GoChannel::call` (`transport.rs:833-838`) takes
    `get_all("grpc-encoding").next_back()`. This is the last value in wire
    order, which matches Go `operateHeaders`, where the last
    `s.recvCompress = hf.Value` wins.
  - It then removes the name before tonic sees the headers (`:841-855`).
    Trailers are rebuilt from `grpc-status` alone (`:1123-1126`), so tonic never
    sees an encoding.
- **The flag is held until the body is complete, with the size and partial
  checks first.** In `Framing::take` (`:915-956`):
  - A nonzero first prefix byte sets `held` and freezes `forward` at the start
    of that message.
  - Once the prefix is complete, a declared length over 4 MiB returns Go's
    ResourceExhausted text at once (`:931-940`). This is Go `recvMsg`'s order:
    the size check comes before the body and before `checkRecvPayload`.
  - Otherwise the status (`payload_status`, `:966-982`) is returned only when
    `remaining` reaches 0, that is, after the whole body.
  - An end inside the held message gives `unexpected EOF` on every path, because
    `filled` stays 5 until the message completes:
    - trailers: `:1118-1122`, and the sanitized trailer code is 13, so tonic
      errors;
    - DATA END_STREAM: `:1046-1053`;
    - RST: `:1009-1013`.
  - I checked one gap: a clean end with no trailers while the held bytes are not
    in tonic's buffer. tonic 0.14.6 `infer_grpc_status` (`status.rs:827-830`)
    returns an error for HTTP 200 without `grpc-status`, so `FlightStream` still
    consults the exchange. There is no silent clean end.
- **Earlier good messages in the same DATA frame still arrive first.**
  - `GoBody` forwards `data[..forward]`, which ends at a message boundary, and
    stores `rejected` (`:1085-1090`). It emits the error only on the next
    `poll_frame` (`:1073-1079`).
  - tonic `Streaming::poll_next` calls `decode_chunk` before it polls the body
    again (`decode.rs:410-414`). So every buffered complete message is yielded
    before the error.
  - Covered by the unit case at `:4811-4816`.
- **The work is bounded.** The `continue` loop over held chunks (`:1087-1089`)
  is bounded by the 4 MiB gate. It consumes the hyper body, so flow-control
  capacity is still released.
- **Saved measurement.** All 12 F1 cases in `cand-v1/report.json` match Go
  exactly on stderr contract, stdout hash, DoGet count, connections and stream
  IDs. The cases are gzip/identity/none/flag2/empty-body/overlimit (flags 0
  and 1), partial body and prefix, a batch then flag 1, flag 0 with gzip, and a
  split message.

No new residue. Go's client-side code for "Decompressor is not installed" is
measured as Unimplemented on the pinned binary, and Rust uses that code.

### F2(a) empty-DATA lifetime cap: closed for the unpadded scope; two residues (R2-1, R2-2)

I checked `EmptyDataFilter` and `Observed::poll_read` (`:2241-2515`) against the
brief.

- **The predicate is exact.** It drops a frame only when it is DATA, has length
  0, has neither END_STREAM nor PADDED, is on a nonzero stream, and the filter
  has passed at least one frame (`:2286-2290`). Unknown flag bits do not affect
  it. HEADERS, RST, GOAWAY, SETTINGS and frames of unknown type always pass.
- **Fragmented 9-byte headers.**
  - `filter()` withholds a header until all 9 bytes are present. It writes a
    kept header back at `kept <= read-9`, so unread bytes are never overwritten
    (`:2304-2330`).
  - A header that straddles reads is completed by reads of at most 8 bytes
    (`:2469-2492`). When it completes and is kept, its bytes are delivered
    through `owed`.
- **EOF, errors and a zero-length buffer.**
  - EOF inside a header releases the withheld bytes, so h2 still sees the
    truncation (`:2476-2481`).
  - A read error returns at once, and the withheld bytes are dropped. This is
    harmless because h2 fails the connection.
  - A zero-capacity buffer returns before any inner read (`:2461-2463`).
- **No illegal empty `Ok`.**
  - The main path returns `Ok` with nothing filled only when the inner reader
    itself reported EOF (`:2496-2498`).
  - After `FILTER_READ_BUDGET` = 32 reads that yield only discarded frames, the
    reader wakes its own task and returns `Pending` (`:2511-2513`).
  - Unit coverage is the 1000-frame always-ready run (`:5013-5018`).
- **The preface watch and Wire are unaffected.** The first frame is never
  dropped, so the h2 SETTINGS-first rule holds. `read_inner` still applies
  Go's preface texts, including inside the straddle reads. Kept bytes alone feed
  the `Wire` scanner, and `Wire::read` ignores DATA without END_STREAM, so the
  observations are unchanged.
- **Saved measurement.** `empty-data-101` and
  `empty-data-60x2-across-reconnect` match Go (333 B / 504 B, DoGets 1 / 2,
  1 connection, streams (0,1),(1,3)).

The disposition's wording, "h2 still enforces the SETTINGS-first rule and every
other check" (`:2261-2262` and the notes row), is not quite true. See R2-1.

### F2(b)/(c) H2-CL-1 and H2-CL-2: accepted as enumerated declarations

`docs/phase6-h2-limits.md` enumerates exactly these two triggers. It cites 24
R5 captures (`tmp/harness-h2-limits-r5-v1`) and grants no broad waiver. I read
the record, not the captures. The successor replay is pending, as the record
itself says.

One stale sentence: the record's lines 15-16 still call repeated-empty-DATA
loss "a separate production defect under correction". It should now point to
the F2(a) fix and to R2-2.

### F2(d) tiny-frame overhead budget: unknown (source-only); not a gate from this review

The notes describe it accurately. I am not adding a pressure prerequisite.

### F3 retry channel reuse: closed (source plus saved measurement)

- `FlightClient` now holds
  `Arc<Mutex<(FlightServiceClient<GoChannel>, Wires)>>` (`:199-223`).
- The do_get-stage retry (`:181`) and the stream-stage retry (`:353-356`) both
  call `ready()` on the same slot. A draining wire is replaced inside that slot,
  so the next application `do_get` reuses the retry's channel.
- The lock is held only across the synchronous `factory.build()`, with no
  await.
- Unit test `retry_channel_serves_the_next_do_get` (`:4820-4861`).
- Saved `goaway-after-headers-then-reconnect`: Go and the candidate both show
  3 requests, 2 connections, streams (0,1),(1,1),(2,3), and stdout `70227ef8…`.

### F4 CONNECT User-Agent: closed as a recorded decision

The notes keep `grpc-go/1.65.0` on CONNECT as a byte-compatibility token and
make no identity claim. The HTTP/2 `user-agent` stays native `tonic/0.14.6` in a
separate ledger row. The constant's comment (`:48-50`) agrees.

### I1: closed

Notes lines 129-136 now say "connection lost before the first message (Rust
DoGet or reader initialization)" and cite `close-after-headers`.

### I2: unchanged and unproven; agreed, not a gate

## New findings (source-only, not measured)

### R2-1. Low (source; introduced by the F2(a) filter): an empty DATA frame inside an open header block is dropped, so h2 no longer rejects the illegal interleave that Go rejects

**Trigger.** On an open stream, the server sends:

1. HEADERS without END_HEADERS (the response headers, or trailers with
   END_STREAM);
2. an unpadded, zero-length, non-END_STREAM DATA frame on any nonzero stream;
3. CONTINUATION with END_HEADERS.

**Behavior by client:**

- **Go (source).** With `ReadMetaHeaders` set, x/net v0.30.0
  `Framer.checkFrameOrder` (`http2/frame.go:551-557`) returns
  `ConnectionError(PROTOCOL_ERROR)`: "got DATA for stream N; expected
  CONTINUATION following HEADERS…". grpc-go 1.65 closes the transport. The
  stream gets Unavailable, which `tail` treats as a stream end and reconnects.
- **Rust before the fix (source).** h2 `FramedRead::decode_frame`
  (`codec/framed_read.rs:160-163`) sends a library
  GOAWAY(PROTOCOL_ERROR). `classify` maps it to Unavailable and the stream
  reconnects. That is the same outcome class as Go.
- **Rust now (inferred).** `EmptyDataFilter` drops step 2, so h2 assembles a
  legal header block. The stream continues and renders later batches on the
  same subscription. Go would instead have reconnected, so stdout and the DoGet
  count can differ.

**Scope.** This needs a malformed peer. It is narrow, and it affects no valid
HTTP/2 response. But it is a regression against the pre-fix behavior, and it
makes the filter's "every other check" claim false.

**Minimal native fix.**

- Track an open header block in `EmptyDataFilter`. A kept HEADERS (0x1) or
  PUSH_PROMISE (0x5) header without END_HEADERS (0x4) opens it. A CONTINUATION
  (0x9) with END_HEADERS closes it.
- Never drop a frame while a block is open. This costs O(1) state and needs no
  HPACK.
- Add a unit sequence (HEADERS without END_HEADERS, empty DATA, CONTINUATION)
  that must still end Unavailable through h2, plus a whole/split variant in
  `empty_data_filter_is_exact_and_bounded`.

### R2-2. Low (source): padded empty DATA frames still count toward h2's lifetime cap of 100, so the F2(a) loss remains for that frame form

**Source.**

- h2 `Streams::recv_data` (`streams.rs:651-663`) calls
  `record_data_frame(frame.payload().len())` for every DATA frame without
  END_STREAM. `payload()` excludes padding. The data.rs tests at `:179-185`
  show that `flow_controlled_len - payload.len` is the padding.
- `counts.rs:120-129` counts any `payload_len == 0` frame against
  `MAX_RECV_EMPTY_DATA_FRAMES = 100` (`proto/mod.rs:40`).
- So DATA frames with flags PADDED, length ≥ 1 and all padding are still
  counted.
- The filter deliberately keeps PADDED frames (`:2289`). They carry
  flow-control weight, and dropping them would leave h2 never releasing that
  window, so keeping them is correct.

**Behavior by client:**

- **Go (source).** `handleData` (`http2_client.go:1184-1188`) only returns the
  padding's window. It renders.
- **Rust (inferred).** The original F2(a) witness with 101 padded-empty frames
  (`length=1, pad=0`) in place of the empty ones should reproduce the original
  loss: GOAWAY(ENHANCE_YOUR_CALM), Unavailable, a reconnect, and the first
  subscription's batch lost.

**Existing coverage.** The kept-frame unit case uses
`frame(FRAME_DATA, FLAG_PADDED, 1, &[])`. That is a length-0 PADDED frame, which
is malformed, and it does not cover this form.

**Required.** No minimal native drop exists. Either:

- narrow the F2(a) "Fixed natively" disposition and record this residue as an
  enumerated, source-supported limit with its exact trigger (an h2-limits row or
  a notes row), plus a process witness when the harness can script padding; or
- take an explicit owner decision.

This is not a broad waiver request, and it does not block the measured
unpadded fix.

## Checked; no new finding

- `Exchange` binding.
- `remote_end` locality.
- Before-headers and after-headers retry gates (`complete == 0`; committed
  after a delivered message).
- The `received` flag.
- The fused stream.

All of these are unchanged in substance from round 1, and the correction's
edits do not disturb them.

## Disposition

**Closed:** F1, F3, F4, I1.

**Closed for its measured scope:** F2(a), unpadded only.

**Accepted as enumerated declarations:** F2(b)/(c), pending successor replay.

**Agreed as unknown or unproven, not gates:** F2(d) and I2.

**Open, both Low and source-only:**

- R2-1: an O(1) filter fix plus a test.
- R2-2: a declaration or decision, and a wording correction.

Hash verification and the reproduction of the owner's test counts were
**blocked in this session**, not passed. This review does not grant transport
lane acceptance. Successor replay of the 291 original contracts, H2-CL, and the
15 promoted transport-wave contracts is still required, and a final
whole-phase review still follows.
