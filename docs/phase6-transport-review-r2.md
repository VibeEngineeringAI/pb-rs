# Phase 6 Flight transport review, round 2

This is an independent, read-only review. I did not edit implementation source.
It does not accept the phase, and the independent whole-phase final review is
still to come.

## Snapshot and grounding

The review read:

- `src/tail/transport.rs` (sha256 `a1c83ae5…680c7e`)
- `src/tail/session.rs` (sha256 `ab06a1b2…919963`)
- `Cargo.lock` (sha256 `9d47e7eb…d35`)

All were taken at 2026-10-14T06:40 -05:00. At that point the source contained
no GOAWAY, 502 or TLS-text fix. Owner fixes that land after this snapshot
must be re-verified against the follow-up criteria below.

Oracle: Go `07af390`. Dependency sources read:

- grpc-go v1.65.0, from the module cache
- Go 1.25.4 standard library, from the cached `go1.25.4` toolchain
- the resolved Rust versions in both the current and the base `Cargo.lock`:
  tonic 0.14.6, hyper 1.12.0, hyper-util 0.1.21, h2 0.4.20, tokio 1.53.2,
  rustls 0.23.45, base64 0.22.1, percent-encoding 2.3.2

The registry also holds hyper 1.8.1, but it is not locked, and no dependency
update is proposed here.

Evidence labels used below:

- **measured**: from the coordinator's Go/candidate fixture comparison, or from
  my own probe.
- **source**: deduced from the locked source.
- **predicted**: needs a fixture.

## Tests and probes I ran

- **Out-of-tree probe** (`/home/ajs/.cache/pb-phase6/tmp/r2rev/probe`, separate
  `CARGO_TARGET_DIR`, `--offline`). Its own lock resolved tonic 0.14.6,
  http 1.5.0, base64 0.22.1 and percent-encoding 2.3.2, the same versions the
  worktree locks for tonic's dependencies. It calls the public
  `tonic::Status::from_header_map`:
  - `grpc-status: 0` with `grpc-status-details-bin: !!` **panics** at
    `tonic-0.14.6/src/status.rs:479` with `Invalid status header, expected
    base64 encoded value: InvalidByte(0, 33)`.
  - `grpc-status` 0, 14 or 1 with `grpc-message` `%FF` or `a%E9b` all give
    **`Code::Unknown`** and the message `Error deserializing status message
    header: invalid utf-8 sequence …`.
  - `grpc-status: abc` gives `Unknown` with an empty message.
- **No repository tests.** I ran no `cargo test`, harness, fixture or
  benchmark. The test counts quoted by the owner (18 transport tests, three
  times) and the fixture results are theirs. I relay them; I did not
  reproduce them.

## Blocking

### B1. A GOAWAY that leaves out an established stream is fatal in Rust; Go retries (measured symptom, source-derived root cause)

**Measured.** Fixture `p6-connection-schema-goaway`, plus the companion case
after a batch. The server sends the schema, waits 50 ms, then sends
`GOAWAY(NO_ERROR, last_stream_id=0)` and closes.

- Go makes 2 DoGets and ends with the gated `InvalidArgument`.
- The candidate makes 1 DoGet and fails fatally with `rpc error: code = Unknown
  desc = protocol error: missing grpc-status trailer, stream was terminated
  without a final status (possible truncation by a proxy or load balancer)`.

**Root cause (source, every step read in the locked versions):**

1. h2 0.4.20 `streams.rs:812-828`, `recv_go_away`: each local stream with
   `id > last_stream_id` gets `handle_error(remote GoAway(NO_ERROR))`, which
   gives `state.rs:299-304` `Closed(Cause::Error(..))`.
2. hyper 1.12.0 `body/incoming.rs:193-198` (data) and `:216-221` (trailers):
   any h2 error whose `reason()` is `NO_ERROR` becomes `Poll::Ready(None)`, a
   clean EOF. The `GoAway` kind is dropped. CANCEL is not swallowed in 1.12.0,
   only NO_ERROR. The swallowing exists for RST_STREAM(NO_ERROR) early
   responses, but it also catches a remote GOAWAY with NO_ERROR.
3. tonic 0.14.6 `status.rs:827-830` (`infer_grpc_status`): HTTP 200 with no
   `grpc-status` becomes `Status::unknown("protocol error: missing grpc-status
   trailer…")` with **no source**. tonic's own comment at `:812-818` documents
   that the reset reason is lost.
4. Our `connection_loss` (`transport.rs:151-182`) walks the source chain, finds
   nothing, and passes the status through. `session.rs:304-312` then treats
   Unknown as fatal.

Go: `handleGoAway` closes streams above the GOAWAY id with `statusGoAway`,
which is Unavailable `the stream is rejected because server is draining the
connection` (`http2_client.go:1369`, `transport.go:806`). `isStreamEnd` then
retries.

**Not acceptable as a fix.** Do not map the tonic missing-trailer status, or
Unknown, to Unavailable. In grpc-go 1.65 a missing status alone is terminal:

- END_STREAM on DATA without trailers is Internal `server closed the stream
  without sending trailers` (`http2_client.go:1199-1202`).
- A trailer HEADERS without `grpc-status` keeps `rawStatusCode = Unknown`
  (`:1440`).
- RST_STREAM(NO_ERROR) is Internal.

All three are fatal. A text mapping would turn real proxy truncations into
silent reconnect loops.

**Narrow fix direction (the owner should pick and probe one):**

- *Connection-level GOAWAY observation (recommended).* The dialer already owns
  the post-TLS byte stream (`Preface<FlightStream>`). Extend that wrapper into a
  cheap HTTP/2 frame-header tracker over *all* reads: a 9-byte header, then
  skip the payload, except for the first 4 payload bytes of type `0x7` (GOAWAY)
  and the stream id of the server's HEADERS/DATA frames. Publish "GOAWAY seen
  with last_stream_id L" through an `Arc` that the `FlightConnection` holds for
  the current connection.

  Classify as Unavailable only when all of these hold:
  1. the item is tonic's sourceless missing-trailer status (exact text, Unknown,
     no details or metadata);
  2. this connection observed a GOAWAY whose `L` is below the subscription's
     stream id;
  3. there is no RST_STREAM for that stream.

  Ordering is safe: the wrapper sees the GOAWAY bytes before h2 parses them, and
  h2 closes the stream inside `recv_go_away`.

  Because reconnects can replace the connection, the flag must belong to the
  connection that carried this stream, not to the process. Also cover
  `last_stream_id` ≥ ours, where Go keeps reading; the stream must not be
  rewritten in that case.
- *Body adapter alone is insufficient.* A tower layer around `Channel` sees the
  same `None`, so it cannot tell GOAWAY from RST(NO_ERROR) or END_STREAM
  without trailers. It is still the right place for M1–M3 below.
- A vendored/patched hyper is possible but heavier. No hyper version changes
  this behavior: 1.12.0 still swallows NO_ERROR.

**Follow-up verification.**

- Unit level: an h2 test server sends GOAWAY(NO_ERROR, 0) after a message and
  closes (Unavailable/restart). It also sends:
  - RST_STREAM(NO_ERROR) with no trailers;
  - END_STREAM DATA with no trailers;
  - a trailer without `grpc-status`.
  Those three must stay fatal. Add GOAWAY with `last ≥ ours` followed by normal
  trailers.
- Process level: both GOAWAY fixtures must match Go's DoGet count, ~500 ms gap
  and retained stdout. The drain, FIN and RST cases must still match.

## Medium

### M1. A non-gRPC HTTP response (502 HTML) gets the wrong code and text (measured; the ledger entry is wrong)

**Measured** for both plaintext and TLS:

- Go: `rpc error: code = Unavailable desc = unexpected HTTP status code
  received from server: 502 (Bad Gateway); transport: received unexpected
  content-type "text/html"`
- Rust: `rpc error: code = Internal desc = protocol error: received message
  with invalid compression flag: 60 (valid flags are 0 and 1) while receiving
  response with status: 502 Bad Gateway`

tonic does not validate `:status` or `content-type` on the initial headers. It
decodes `<` (60) of the HTML as a gRPC frame flag (`codec/decode.rs:175-178`).
r1's T6 prediction (`grpc-status header missing, mapped from …`) and the
notes' ledger copy of it hold only for an empty body. The code also differs
(Internal vs Unavailable), so this is not text-only.

Both clients fail fatally in this stage, because the error appears at DoGet in
Rust and at reader initialization in Go. The remaining difference is a wrong
code and text, not a lifecycle change.

**Narrow fix:** add a response layer between `Channel` and
`FlightServiceClient`. On the *initial* response HEADERS only, apply Go 1.65
`operateHeaders` (`http2_client.go:1430-1528`):

- Initial `contentTypeErr` is `malformed header: missing HTTP content-type`.
  It clears for a valid `application/grpc[+…|;…]` and otherwise becomes
  `transport: received unexpected content-type %q`.
- Without `:status` the text is `malformed header: missing HTTP status`. For a
  status other than 200 it is `unexpected HTTP status code received from
  server: N (StatusText)`. An unparsable value gives Internal `transport:
  malformed http-status: …`.
- When the content type is not gRPC or the status is not 200, the code comes
  from `HTTPStatusConvTab` (400→Internal, 401→Unauthenticated,
  403→PermissionDenied, 404→Unimplemented, 429/502/503/504→Unavailable, other
  present status→Unknown, absent status→Internal). The message joins the
  status text and the content-type text with `"; "`.

Do not apply this to trailers or to an accepted gRPC response. `StatusText`
must be Go's table, not `http`'s canonical reason. Add 200+`text/html`,
404+`application/grpc`, 503 with an empty body, and a status with no reason
text. Fix the ledger wording either way.

### M2. A remote `grpc-status-details-bin` that is not base64 panics the client (probe-measured; new)

tonic calls `Status::from_header_map` on every response's headers
(`client/grpc.rs:339`) and trailers (`status.rs:782`). An invalid
`grpc-status-details-bin` reaches `.expect(…)` at `status.rs:479`. My probe
reproduced the panic.

The driver runs this inside `runtime.block_on` with no `catch_unwind`
(`session.rs:81`). The process therefore dies with a Rust panic message and
exit 101.

Go 1.65 decodes the header through `decodeMetadataHeader`, using StdEncoding
when `len % 4 == 0` and RawStdEncoding otherwise. On failure it sets
`headerError`, which becomes a fatal Internal `transport: malformed
grpc-status-details-bin: illegal base64 data at input byte N`
(`http2_client.go:1497-1534`).

Any server, ingress or plaintext MITM can trigger this. It is a remote crash,
not memory corruption.

**Fix:** in the M1 layer, validate the header in response headers and in
trailer frames with Go's decoding rule. On failure, replace it with Go's
Internal status: strip the header and synthesize `grpc-status: 13` with
`grpc-message` percent-encoded. Fixture: an invalid value in Trailers-Only
headers, and in trailers after a batch.

### M3. A `grpc-message` that is not UTF-8 changes the status code (probe-measured; the ledger is wrong)

tonic turns an undecodable message into `Code::Unknown` whatever code was sent
(`status.rs:492-498`). The probe confirmed this for 0, 14 and 1. So:

- OK or Unavailable after batches is a retry in Go, but becomes a fatal
  Unknown in Rust. This is a lifecycle flip.
- The notes say "decoded lossily", which is wrong.

Go `decodeGrpcMessage` keeps the raw bytes and the code.

**Fix (same layer):** percent-decode Go-style. If the result is not UTF-8,
re-encode a lossy UTF-8 form before tonic parses it, so the code survives.
Byte-exact stderr is then a narrow, declared residue. Fixture: status 14 or 0
with `%FF` after a batch (retry), and status 3 with `%FF` (fatal text).

## Low (new)

- **L1. TLS client to a plaintext port: text (measured).** Go: `tls: first
  record does not look like a TLS handshake`. Rust: `received corrupt message
  of type InvalidContentType`. Narrow fix in `TlsPlan::handshake`: if the
  handshake fails with `rustls::Error::InvalidMessage(InvalidContentType)`
  before any valid server record, emit Go's text. Other rustls texts stay
  individually declared; no blanket exemption.
- **L2. CONNECT header keys containing a space.** Go 1.25
  `canonicalMIMEHeaderKey` accepts spaces (go.dev/issue/34540,
  `textproto/reader.go:758-765`). The Rust `mime_header` rejects them, and the
  unit test at `transport.rs:1672-1675` asserts the wrong Go expectation.
  `Proxy-Agent : x` or `Bad Key: v` with 200 is a tunnel in Go and a
  `malformed MIME header line` failure in Rust. Fix: allow `' '` in the key
  token check, and correct the test.
- **L3. CONNECT validation order.** Go validates the status line and each
  header line as it reads them (`response.go:161-193`). Rust reads the whole
  head first. Two consequences:
  - A malformed first line followed by close reads `unexpected EOF` in Rust,
    instead of Go's `malformed HTTP response …`.
  - A malformed line followed by an idle proxy hangs Rust, where Go fails at
    once.

  Fix by validating incrementally, or declare it. The notes' "Same" is too
  strong.
- **L4. TLS peer closes before SETTINGS without close_notify.** rustls reports
  `UnexpectedEof` with a long URL text. Go `tls.Conn` reports `EOF`. In
  `Preface::poll_read`, map `ErrorKind::UnexpectedEof` to the `EOF` /
  `unexpected EOF` logic (based on `seen`). This is a common TLS-terminating
  proxy case.
- **L5. Slow DNS delays exit.** `lookup_host` runs in `spawn_blocking`. After
  the 20 s deadline fires, dropping the runtime at the end of `session::run`
  waits indefinitely for that blocking thread (tokio 1.53.2
  `blocking/pool.rs:244-263`, `runtime.rs:461`), so exit is delayed until
  `getaddrinfo` returns. Use `runtime.shutdown_background()`, or
  `shutdown_timeout(0)`, after the scope.
- **L6. TLS session resumption.** The cached rustls config keeps the default
  in-memory resumption store. Go's `tls.Config` has no `ClientSessionCache`,
  so it never resumes and re-verifies the certificate on every reconnect. Set
  `config.resumption = Resumption::disabled()` for parity.
- **L7. Diagnostics to declare explicitly, one by one:**
  - An HTTP/1.x peer on the Flight port. Go says `error reading server
    preface: http2: frame too large`; h2 instead reports a FRAME_SIZE_ERROR
    GOAWAY text, already Unavailable through `is_go_away`.
  - Connection loss at the DoGet or initialization stage. Go says `error
    reading from server: …`; Rust keeps the tonic/h2 text.
  - A malformed `grpc-status`. Go gives Internal `transport: malformed
    grpc-status: …`; tonic gives Unknown with an empty message. The M1 layer
    could fix this too.
  - RST(NO_ERROR) with no trailers. Go gives Internal; Rust gives tonic's
    Unknown missing-trailer status.

## r1 items (T1–T15)

| Item | Status | Basis |
| --- | --- | --- |
| T1 connection loss | **Partly resolved.** FIN, RST, drain and I/O paths are fixed and match in fixtures (owner-measured). The GOAWAY-excluded stream is **unresolved (B1)**. The classifier correctly leaves server statuses and RST mappings alone. | source + measured |
| T2 TLS `:authority` | Resolved. `Endpoint::origin("https://host")` through tonic `AddOrigin` (`add_origin.rs:61-79`); the connector checks only the http endpoint URI (`connector.rs:55-68`). The authority fixture matches (owner-measured). IPv6 bracketing is declared. | source + measured |
| T3 eager dial | Resolved as declared; the notes are corrected. | doc |
| T4 header list | Resolved: `http2_max_header_list_size(16 MiB)`. The owner reports a large-header fixture match. Confirm it exceeds 16 KiB; the 8 KiB trailer fixture would not distinguish the fix. | source |
| T5 windows | Resolved: `http2_adaptive_window(true)` pins the behavior. Read-ahead is still unmeasured. | source |
| T6 non-gRPC status / RST / unknown code | **Unresolved.** The 502 ledger text is wrong and the code differs (M1). The RST and unknown-code rows are fine as declared. | measured |
| T7 empty CONNECT EOF | Resolved, and tested (`unexpected EOF`). | source + test |
| T8 HTTP version | Resolved (`go_http_version`, tested with `HTTP/1.10`). | source + test |
| T9 CONNECT headers / Content-Length | Mostly resolved. New mismatches L2 (space in key) and L3 (ordering). | source |
| T10 CONNECT I/O text | Resolved (`read/write tcp L->R: …`). | source |
| T11 mapped loopback | Resolved (`to_canonical().is_loopback()`, tested). CIDR 4-in-6 is declared. | source + test |
| T12 scheme case | Resolved (lowercased, tested). | test |
| T13 escapes / IDNA | Declared. Pure-ASCII host case matches Go (`idnaASCII` keeps ASCII). | source |
| T14 protocol mismatch / preface | Preface bound resolved, and the 20 s blackholes match (owner-measured). TLS client to plaintext gives the wrong text (**L1, unresolved**). The HTTP/1.x peer text is L7. | measured |
| T15 resolver-scheme hosts | Declared. | doc |

## Test gaps still open

1. No TLS unit test in the transport module: ALPN gate, `:authority`, SNI.
   Process fixtures cover it.
2. No unit tests for the B1 negative cases (RST(NO_ERROR), END_STREAM without
   trailers, missing `grpc-status`), which keep any fix from over-mapping.
3. No fixtures for M2 or M3.
4. Over-limit 4 MiB+1 and exactly 4 MiB through tonic's decoder: confirm the
   fixture exists.

## Objections

**Unresolved:** B1, M1, M2, M3 and L1. L2 is a concrete wrong test oracle. L3
through L7 need a fix or an explicit per-item ledger entry; blanket diagnostic
exemptions are not acceptable.

**Resolved:** T2, T3, T5, T7, T8, T10, T11, T12, and the non-GOAWAY part of T1.
T4 is resolved in source but needs the >16 KiB confirmation.

**Declared and acceptable:** T13, T15, native user-agent, IPv6 TLS authority
brackets, Go eager dial, CONNECT chunked and close-delimited bodies.
