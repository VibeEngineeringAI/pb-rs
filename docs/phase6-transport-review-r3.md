# Phase 6 Flight transport review, round 3

This is an independent, read-only review. I did not edit implementation source.
It is not the whole-phase acceptance review, which is still pending after all
measurements.

## Snapshot and grounding

Final source read and tested (sha256):

- `src/tail/transport.rs` `558c7ed96f3cb7f11b023ca2c7bf16d2fcadf005e19ae62684f79db22faea769`
- `src/tail/session.rs` `827ee65808657dc6fc6b7d9c6ef00849b68272be91f2c68db310521f0d2afef5`
- `src/tail/session/tests.rs` `8fad4417…2b32015`
- `Cargo.lock` `9d47e7eb…d35` (unchanged since r2)

The source changed twice while I was reviewing. I diffed both changes:

1. `Terminal::Trailers` now maps to a fatal Unknown status (`transport.rs`
   `ended_without_trailers`). See R3-B1.
2. The diagnostics `inflight(1/0)` were added around the session `Open` event.

Oracle: Go `07af390`, grpc-go v1.65.0 (module cache), Go 1.25.4. Locked Rust
stack: tonic 0.14.6, hyper 1.12.0, h2 0.4.20, tokio 1.53.2.

Evidence labels:

- **measured**: my own test run or probe.
- **source**: read in the locked or pinned source.
- **predicted**: needs a fixture.
- **owner**: reported by the owner or coordinator. I relay it but did not
  reproduce it.

## Tests and probes I ran

- `cargo test --offline --lib tail::` with a separate target dir
  (`/home/ajs/.cache/r3rev/target`), against the hashes above: **78 passed, 0
  failed**. This includes all 25 transport tests. I did not run the whole crate,
  the harness, the fixtures or any benchmark. The owner's 262/334/338 counts and
  the Go PB r4 results are **owner** evidence.
- **h2/hyper trailer-retention probe** (`/home/ajs/.cache/r3rev/probe`, locked
  hyper 1.12.0, h2 0.4.20, hyper-util 0.1.21, tokio 1.53.2). A raw TCP server
  sends:
  - HEADERS(200, `application/grpc`);
  - one DATA message;
  - trailer HEADERS(END_STREAM, `grpc-status: 14`);
  - then one of: GOAWAY(last=0)+FIN, GOAWAY(last=1)+FIN, FIN alone,
    GOAWAY(last=0) with the connection held open, or RST_STREAM(NO_ERROR) after
    the trailers.

  Each shape ran with the request half closed and with it open. The client
  starts reading the body at once, or 300 ms after the connection is gone.

  **Result: all 20 valid-HPACK cases return DATA, then TRAILERS(14), then end.**
  Trailers are never lost. Then I sent a 139-byte `grpc-message` encoded the way
  the module's test helper encodes it: one length byte, with no HPACK integer
  continuation. In all 8 cases, with or without a GOAWAY, h2 fails the body with
  `GoAway(PROTOCOL_ERROR, Library)` and the trailers never appear. With correct
  encoding (`0x7f 0x0c`), all 4 cases keep the trailers.

## Blocking

### R3-B1. The "h2 discards queued trailers" residue is a test-encoder artifact, and the new `Trailers` mapping is a regression (measured probe + source)

**Source.** I agree with the parent's reading of h2 0.4.20:

- `Recv::poll_data` (`recv.rs:1211`) and `poll_trailers` (`:1239`) pop
  `pending_recv` before they consult the stream state.
- `recv_trailers` (`:417-438`) queues the `Event::Trailers` after `recv_close`.
- `State::handle_error` and `State::recv_eof` (`state.rs:299-323`) do nothing to
  a stream that is already `Closed`.
- `recv_go_away`, `handle_error` and `recv_eof` (`streams.rs`) only change
  state, notify and clear send/accept queues.
- The receive buffer is cleared only by `clear_recv_buffer`. That runs when the
  `RecvStream` is dropped (`share.rs:487`) or when a released stream is
  cleaned up (`recv.rs:519`). It never runs while hyper's `Incoming` owns the
  stream.

hyper 1.12.0 `Incoming::poll_frame` (`body/incoming.rs:186-232`) polls data,
then trailers. So once h2 has parsed and queued the trailers, they are delivered
whatever GOAWAY, FIN or RST(NO_ERROR) follows.

**Exact cause of the observed loss.** The module test helper `hpack()`
(`transport.rs` tests, around line 2962) writes `value.len() as u8` as a single
length byte. The test at `frames_explain_streams_that_end_without_trailers`
(around lines 3115-3128) sends a 139-byte `grpc-message`, the tonic text. Byte
`0x8b` sets the HPACK Huffman bit with length 11, so h2 rejects the header block
as a connection `PROTOCOL_ERROR` (Library GOAWAY). My probe reproduces this.

The frame scanner records `Terminal::Trailers` from the 9-byte frame header
*before* h2 parses the block. `GoBody::poll_frame` then gets `Some(Err)`,
`observed_end` applies `Trailers`, and the new code turns the result into
Unknown `protocol error: missing grpc-status trailer…`. The test passes only
because it picked a server message equal to that text.

**Consequence.** For a real HPACK or other connection error raised while the
trailer block is parsed:

- Before this change, `classify` saw the local h2 GOAWAY and mapped it to
  Unavailable.
- Go treats a framer connection error as a transport close: Unavailable
  `error reading from server: …`, then a reconnect after an established
  stream. This is **predicted**; the PB process fixture should confirm it.
- Now Rust reports a fatal Unknown with misleading text. That is a lifecycle
  flip in the wrong direction.

The scanner's read-ahead affects every terminal kind, not only trailers. A frame
h2 later rejects (a flow-control-violating DATA END_STREAM, for example) is
recorded too.

**Not acceptable:** the ledger entry "h2 may discard unread trailers (declared
residue)". No maintained-stack policy is needed, because nothing is discarded.
A parallel HPACK parser is unjustified.

**Narrow fix:**

1. Revert `Terminal::Trailers` to "no observation" (`None`), so tonic and
   `classify` decide.
2. In `observed_end`, apply wire observations only to a clean end (`None`), or
   to an `Err` whose h2 error is remote: `h2::Error::is_remote()`, or a
   `Reset`/`GoAway` that the frames show. For a local or library h2 error,
   `classify` stays authoritative.
3. Fix `hpack()` to emit HPACK prefix integers (7-bit name and value lengths
   with continuation, as in RFC 7541 §5.1). Audit the other raw-h2 helpers in
   the harness fixture for the same shortcut.

**Required tests:**

- A valid 139-byte trailer `grpc-message` followed by GOAWAY(0)+FIN, for status
  0, 1, 14 and 3. Expect the server status with raw bytes (0 ends the stream;
  1 and 14 reconnect).
- The same trailer with a deliberately malformed header block. Expect
  Unavailable from the connection error, and a matching Go PB fixture.

The coordinator's isolated 16-case Go PB probe (schema/batch × status
0/1/14/3 × trailers+FIN vs trailers+GOAWAY-below+FIN) is the right process
check. **It must use valid HPACK for long values.** Go should act on the
trailers in all 16 cases. With the fix, the candidate should too.

## Medium

### R3-M1. A truncated gRPC message followed by trailers ends cleanly in Rust; Go reports fatal Internal `unexpected EOF` (source)

- **tonic** `codec/decode.rs:280-290` stores the trailers and returns
  `Ok(None)` without looking at `self.buf`. `Streaming::poll_next`
  (`:414-417`) then returns the trailer status: `None` for 0, or the error for
  1, 14, 3 and so on. The partial 5-byte prefix or partial body is dropped
  silently. The "Unexpected EOF decoding stream." branch (`:269-276`) runs only
  when the body ends with no trailers at all.
- **grpc-go 1.65** `parser.recvMsg` (`rpc_util.go:613-638`) reads the header
  and then the body through `io.ReadFull`. On the trailers' `io.EOF` it returns
  `io.ErrUnexpectedEOF`. `csAttempt.recvMsg` passes it to `toRPCErr`, which
  gives Internal `unexpected EOF` (`rpc_util.go:916-917`). That is fatal.

**Effect:** with trailers of status 0, 1 or 14 after a truncated message, Rust
reconnects and Go exits. This is the "proxy truncation must not become a silent
reconnect loop" concern from r2. Also, DATA(END_STREAM) after a partial message
is Internal `unexpected EOF` in Go (the stream closes with `io.EOF`). Rust
reports Internal `server closed the stream without sending trailers`. Both are
fatal, so that part is text only.

**Fix (bounded, in `GoBody`):** count gRPC framing across DATA frames, using
the 5-byte prefix and then the remaining length. When the body ends or trailers
arrive in the middle of a message, set the exchange to Internal `unexpected EOF`
and hand tonic `grpc-status: 13`.

**Fixtures:**

- Trailers after 3 of 5 prefix bytes, for status 0, 14 and 3.
- Trailers after the prefix plus a partial body.
- DATA(END_STREAM) after a partial body.

### R3-M2. A valid `grpc-status-details-bin` is ignored; Go uses it through `NewWithProto` (source)

grpc-go 1.65 `operateHeaders` builds the final status as
`istatus.NewWithProto(rawStatusCode, grpcMessage, mdata["grpc-status-details-bin"])`
(`http2_client.go`, after the field loop). From `internal/status/status.go:49-73`:

| `grpc-status-details-bin` | Go 1.65 status |
| --- | --- |
| Absent, or more than one value | The header code and `grpc-message` |
| One value that does not unmarshal as `google.rpc.Status` (including a proto3 `message` that is not valid UTF-8) | The header code and `grpc-message` |
| One value whose proto code equals `grpc-status` | The **proto's** code and **message**. `grpc-message` is ignored. |
| One value whose proto code differs from `grpc-status` | **Internal** `grpc-status-details-bin mismatch: grpc-status=…, grpc-message=%q, grpc-status-details-bin=%+v` |

Rust validates only the base64 and then uses `grpc-status` and `grpc-message`.
Concrete flips:

- `grpc-status: 14` with details code 3 is a fatal Internal in Go and a
  reconnect in Rust.
- The empty value `grpc-status-details-bin:` (empty proto, code 0) with any
  non-zero status is a fatal Internal in Go.
- A matching code with a different proto message changes the stderr text.

`%+v` uses protobuf-go's prototext, whose spacing is deliberately unstable, so
that text cannot be byte-matched. The **code** can be matched.

**Fix:** decode base64, then a bounded protobuf read of field 1 (varint code)
and field 2 (UTF-8 string). Skip or validate field 3 (`Any`) as protobuf-go
would. Fail closed to "ignore" on any parse error. Declare the mismatch text
residue explicitly.

**Fixtures:** the matched, mismatched, empty and duplicate cases. Use the Go PB
oracle bytes; they are not in the r4 evidence I saw.

### R3-M3. A refused stream on the first attempt is transparently retried in Go; Rust fails fatally (source)

grpc-go marks a stream *unprocessed* in three places:

- a GOAWAY whose last id excludes it (`http2_client.go:1358-1369`);
- RST_STREAM(REFUSED_STREAM) (`:1210-1213`);
- a stream created on a draining transport (`:750-766`).

`csAttempt.shouldRetry` (`stream.go:622-641`) retries transparently when the
stream is uncommitted, on its first attempt and unprocessed. The Flight DoGet
calls only `SendMsg` and `CloseSend`, which do not commit. The first successful
`RecvMsg` commits (`withRetry` `onSuccess`, `stream.go:787-789`). So a refusal
before the first message is invisible in Go: a new attempt and DoGet, with no
500 ms wait.

Rust, by case:

- **Refusal before the response headers:** `client.do_get` fails. The
  `GoChannel` has no binding yet, so `classify` gives Unavailable, which is
  fatal at the DoGet stage.
- **Refusal after the headers, before the schema:** `observed_end` gives
  Unavailable at reader initialization, which is also fatal.

This window is narrow, but it is the rolling-deploy and ingress-overload path
(Envoy and nginx refuse streams).

**Fix:** allow exactly one transparent re-issue when the bound stream's wire
event is `Refused` or `Reset(REFUSED_STREAM)` and no message has been received.
Bind the wire in the error path too: `wires.current()` plus `opened()` when
`call.await` fails. A second refusal must surface, because Go's `firstAttempt`
is false then.

**Fixture:** the server reads the stream-1 HEADERS and answers
GOAWAY(last=0)+FIN, or RST(REFUSED_STREAM); the next connection serves
normally. Go makes 2 DoGets with a gap of about 0 ms and no fatal error.

## Low

- **R3-L1. The single terminal slot can be overwritten by another stream's
  frame.** `Wire::read` replaces `terminal` whenever the event's stream differs
  (`is_none_or(stream != event.0)`). A late RST or DATA for an older stream,
  arriving after a `Refused` or `Reset` for the current stream, erases the
  current stream's evidence. Its NO_ERROR end then falls back to tonic's
  Unknown. Record only events for `stream == opened` (GOAWAY is already keyed to
  `opened`). State stays O(1). (source)
- **R3-L2. Mid-stream HEADERS without END_STREAM, and 1xx responses.**
  - For HEADERS without END_STREAM, Go reports Internal `a HEADERS frame cannot
    appear in the middle of a stream`. h2 resets with PROTOCOL_ERROR and tonic
    says Internal with h2 text.
  - h2 ignores informational 1xx headers. Go treats a 1xx initial `:status` as a
    non-200 and fails.

  Both are fatal in each client; declare the texts. (source)
- **R3-L3. A `-bin` failure index with interleaved repeated keys.** `HeaderMap`
  groups values by key, so with keys A, B, A all invalid, Rust reports B and Go
  reports the last A. Declare it. (source)
- **R3-L4. The L1 TLS text is applied to any `InvalidContentType`,** not only to
  the first record. A mid-handshake bad record would get Go's "first record"
  text. Declare it, or check that no server record has been processed. (source)
- **R3-L5. Pre-header RST mapping.** An RST before the response headers has no
  binding, so it keeps tonic's code mapping rather than `go_reset_code`. For
  example, FLOW_CONTROL gives Internal, not ResourceExhausted. Both are fatal;
  this is text and code only. The binding change in M3 would fix it. (source)

## Bound, binding and spoofing checks (no finding)

- **Request binding.** `Exchange` travels in `http::Request` extensions,
  in-process, and the server cannot set it. One DoGet is in flight at a time:
  the session drops the stream before the 500 ms wait. `Wires` is replaced only
  when tonic's lazy `Reconnect` dials, which happens on a request. The bound
  `(wire, opened)` is therefore the response's connection and stream.
- **Memory.** Each `Wire` holds two words, each scanner a few dozen bytes, each
  `Exchange` one status. The old `Wire` is freed with its connection and its
  last `GoBody`. No per-frame or per-stream accumulation.
- **Frame scanner chunking.** The 9-byte header is built one byte at a time.
  The payload is skipped and only the 4/8-byte prefix is copied (clamped to the
  frame length). Zero-length frames complete at once. Writes are scanned only up
  to the bytes the inner writer accepted, including vectored writes. The 24-byte
  client preface is skipped once.
- **Trailer precedence.** A server trailer status is set before tonic sees the
  sanitized `grpc-status`. `FlightStream` takes the exchange first, and
  `failed` stops later items. tonic never parses a remote `grpc-message`,
  `grpc-status` or `-bin`, so there is no panic and no code change. Fixed by
  M2 and M3 of r2.
- **GOAWAY with `last` ≥ ours** records nothing. The stream continues; the unit
  test covers this.

## Prior objections

| Item | Status | Basis |
| --- | --- | --- |
| r2 B1 GOAWAY excluding an established stream | **Resolved at unit level.** The unit test passes in my run. Process re-run is owner evidence. The new R3-B1 must not regress it: R3-B1 touches only `Trailers` and local errors. | measured + owner |
| r2 M1 non-gRPC HTTP status | Resolved: Go conversion table, `StatusText`, content-type text; unit test passes | measured |
| r2 M2 invalid `-bin` panic | Resolved: tonic never sees it; Go index text; unit-tested | measured |
| r2 M3 non-UTF-8 `grpc-message` | Resolved: code kept, raw bytes kept, Go percent rule (`i+2 < len`) | measured |
| r2 L1 TLS to a plaintext port | Resolved, with the residue R3-L4 | source |
| r2 L2 space in a CONNECT key | Resolved (`key_byte` allows a space; Content-Length is not canonicalized) | source |
| r2 L3 CONNECT validation order | Resolved: per-line reads and validation, folded first line | source |
| r2 L4 TLS `UnexpectedEof` | Resolved, mapped to EOF / `unexpected EOF` | source |
| r2 L5 slow DNS delays exit | Resolved: detached resolver thread | source |
| r2 L6 resumption | Resolved: `Resumption::disabled()` | source |
| r2 L7 malformed `grpc-status`, RST(NO_ERROR) | Resolved, with Go texts | measured |
| r2 L7 HTTP/1.x peer and stage-specific connection-loss texts | Still declared-only; keep them as separate ledger rows | doc |
| r1 T6 502 / RST / unknown code | Resolved for observed RSTs. Pre-header RST is R3-L5. | measured |
| The owner's "trailers discarded" ledger entry | **Rejected** (R3-B1) | measured probe |

**Unresolved:** R3-B1 (blocking), R3-M1, R3-M2, R3-M3. Each Low item needs a
fix or its own ledger row.

`docs/phase6-transport-notes.md` is stale. It still says the 502 text is
tonic's, that non-UTF-8 messages are "decoded lossily", and that the T1 row
uses `read_status`. Update it together with the fixes.

## Not verified here

- The candidate's JSON rendering of raw message bytes. Go r4 renders
  `a\xe9\x80b` as `a��b`, one U+FFFD per invalid byte, not one per
  maximal subpart. The session path goes through `gojson::push_string_bytes`. I
  did not test that function with this input; the process fixture should
  compare bytes.
- Every Go PB and process outcome named above.
