# Phase 6 Flight transport notes

Scope: `src/tail/transport.rs` (discovery, Flight channel, metadata, ticket,
status text, read-status classification). Oracle: Go `07af390` `cmd/tail.go`,
grpc-go v1.65.0, arrow/go v13.0.0, Go 1.25.4 `net/http`. Candidate:
arrow-flight 60.0.0, tonic 0.14.6, hyper 1.12.0 and h2 0.4.20 (all locked), and
rustls with aws-lc. Facts below come from reading dependency sources plus
the unit tests in the module. Rows marked *fixture* still need process
evidence. Review round 1 is in `phase6-transport-review-r1.md`; its item numbers
(T1–T15) are referenced below.

## Preserved behavior (source-matched)

| Boundary | Go source | Rust |
| --- | --- | --- |
| Discovery | `analytics.FetchAbout` through the shared HTTP client. Only 200 decodes; any other status returns `Request Failed\nStatus Code: S\nResponse: BODY\n` (public JSON `request failed: HTTP N`). | `ApiClient::standard(GET, "about")`, the same client policy, refresh, deadline and redirects |
| About decoding | `json.Unmarshal` into `About`; a missing or null `grpcPort` is 0 | Same field table and type checks as `status.rs`; integer port is int64 |
| Credential snapshot | Profile value after any discovery OAuth refresh | `ApiClient::profile()` after discovery; Flight never refreshes |
| Address | `net.JoinHostPort(url.Hostname(), port)` | Raw hostname parsing (no url-crate case, IDNA or default-port normalization). The unsupported-scheme message quotes the lowercased scheme, as `url.Parse` does (T12). |
| TLS | `Cloud \|\| scheme == https` gives `credentials.NewTLS{MinVersion: TLS12, ServerName: hostname}` over Go's system root pool; `ClientHandshake` runs under the connect deadline; `http` is insecure | The dialer owns TLS (tokio-rustls; tonic never sees an https URI). Settings: TLS 1.2 and 1.3, ServerName = raw hostname, ALPN offer `h2`. Roots come from the crate's Go-compatible system-pool loader (`load_tls_config`), loaded once per process at first handshake. The handshake is bounded by the remainder of the single 20 s connect deadline. Errors read `connection error: desc = "transport: authentication handshake failed: …"`. |
| ALPN | grpc-go 1.65 accepts a peer that selects no ALPN unless `GRPC_ENFORCE_ALPN_ENABLED=true` (case-insensitive) | Same gate and variable; the enforced text is `credentials: cannot check peer: missing selected ALPN property` |
| `:authority` (T2) | `determineAuthority`: TLS uses the creds ServerName (bare hostname); plaintext uses `host:port` | TLS sets `Endpoint::origin("https://hostname")`, giving `:scheme https` and a bare hostname. Plaintext keeps `host:port` (unit-tested against an h2 server). |
| Server preface (T14) | `newHTTP2Client` waits for the server's first frame; the connect-context monitor closes the conn at the 20 s deadline. Failures are Unavailable `connection error: desc = "error reading server preface: …"` | A `Preface` wrapper bounds reads until one complete HTTP/2 frame arrives, using the same deadline. EOF, read errors and expiry give Go's texts (`EOF`, `unexpected EOF`, `read tcp L->R: read: …`, `read tcp L->R: use of closed network connection`). Unit-tested for EOF and expiry. |
| Preface-stage writes | `newHTTP2Client` writes the 24-byte client preface synchronously (failure: `transport: failed to write client preface: write tcp L->R: write: …`), then lets its reader decide: a peer reset surfaces as `error reading server preface: read tcp L->R: read: connection reset by peer` (measured for plaintext to a TLS port). | Until the server preface, a failing write that still carries client preface bytes reports Go's preface-write text. A later write failure is absorbed, and later writes skip the socket. The read side (bounded by the connect deadline) reports, using an absorbed reset when the read then only sees EOF, because the write consumed the socket error that Go's reader would have read. After the preface, write errors propagate unchanged. Deterministic socket-double tests cover reset-then-EOF, broken pipe, the read's own error, a never-ready read (deadline, two socket writes) and the preface-write failure. A real-socket reset test is a smoke check only (loopback timing rarely reproduces the race). |
| HTTP/2 limits (T4, T5) | `defaultClientMaxHeaderListSize` 16 MiB. Initial windows 64 KiB, grown by the BDP estimator up to 16 MiB. | `http2_max_header_list_size(16 MiB)`. `http2_adaptive_window(true)`: hyper resets both windows to 65 535 and runs its BDP estimator, capped at 16 MiB. The estimator algorithms are similar in design; read-ahead under a slow sink is *unmeasured*. |
| Connection loss (T1) | Transport read failure, EOF or GOAWAY closes active streams with Unavailable, which `isStreamEnd` turns into a reconnect | `classify` (internal) maps a local status whose source chain holds an `io::Error`, an `h2::Error` that is I/O or GOAWAY, or a closed hyper channel to Unavailable, keeping the message. Server statuses arrive through the response layer and are never reclassified. Unit-tested: FIN mid-stream gives Unavailable; server Unknown and Internal pass through. |
| Metadata | `metadata.New`: `x-api-key`, `x-p-tenant`, raw `cookie: session=TOKEN`, `authorization: Basic b64(u:p)`. Values outside 0x20..0x7E produce Internal `header key "K" contains value with non-printable ASCII characters` at stream start. | Same keys and values. Validation is deferred to `do_get` and runs before dialing. |
| Ticket | `json.Marshal` with HTML escaping, `\b`/`\f` short escapes and U+2028/2029 escapes | serde_json plus the same replacements |
| Receive cap | `defaultClientMaxReceiveMessageSize` = 4 MiB; over-limit is ResourceExhausted `grpc: received message larger than max (N vs. L)` | Pinned at 4 MiB. tonic's local status is rendered as Go's code and text only when all of these hold: OutOfRange, the exact tonic text, limit = 4194304, found > limit, and no details or metadata. |
| Status value (r2) | `status.Error()`: `rpc error: code = <Go name or Code(N)> desc = <msg bytes>`. The message keeps raw bytes after `decodeGrpcMessage`. `isStreamEnd` codes are OK, Canceled and Unavailable. | `FlightStatus { code: u32, message: Vec<u8> }` with `go_error()` bytes and `is_stream_end()`. `do_get` returns `Result<FlightStream, FlightStatus>`, and `FlightStream` yields `Result<FlightData, FlightStatus>`, fused after its first error as Go's stream is. Session renders the bytes through `CliError::with_raw_message`: raw in text mode, error.rs per-byte U+FFFD escapes in JSON. Unit-tested for codes 0/1/14/3/20/-1 with `%FF`, `a%E9b` and `a%E9%80b`. |
| TCP dial | `minConnectTimeout` 20 s connect deadline covering resolution and dialing. `net.Dialer` behavior: primary family = first address; fallback after 300 ms or at once when the primaries fail; the primary error is reported when both fail; serial `partialDeadline` shares with a 2 s floor; the expired-context address is reported; `i/o timeout` text. `SO_KEEPALIVE` with OS defaults, `TCP_NODELAY`. | Same algorithm (`dial_parallel`/`dial_serial`/`partial_deadline`), unit-tested with an injected connector. Used for the Flight host and the proxy host. |
| Proxy selection | `mapAddress`: `HTTPS_PROXY`/`https_proxy` and `NO_PROXY`/`no_proxy` for an `https://host:port` request, even for plaintext Flight. `localhost` and loopback (including IPv4-mapped, T11) are bypassed. The proxy host is dialed verbatim (scheme ignored, no default port). | Same selection: the shared `no_proxy_matches` plus a local `to_canonical().is_loopback()` check |
| Response headers and trailers (r2 M1–M3, L7) | `operateHeaders`. Initial headers: a malformed `grpc-status` (`strconv.ParseInt` text) is Internal at once. Non-gRPC content-type or non-200 `:status` gives `HTTPStatusConvTab` (other statuses Unknown) and joins `unexpected HTTP status code received from server: N (StatusText)` with the content-type error. Any `-bin` field that fails Go base64 (`StdEncoding` when len%4==0, else `RawStdEncoding`) is Internal `transport: malformed K: illegal base64 data at input byte N`. Trailers apply the same status and `-bin` rules. A missing `grpc-status` is Unknown. | A tower layer (`GoChannel`/`GoBody`) between tonic's channel and the generated client implements these rules, with Go 1.25's StatusText table and Go's `decodeQuantum` error indices. It records Go's status in a per-request `Exchange`, carried in in-process request extensions so it can't be spoofed. tonic only sees a sanitized numeric `grpc-status`, never a remote `grpc-message` or `-bin`, so it can't panic (M2) or rewrite the code (M3). Trailers-Only works the same way. Unit-tested over raw HTTP/2 frames: 502 HTML, 200 without content-type, 404 gRPC, 599, Trailers-Only, `!!` details, `abc` status. |
| Streams ending without trailers (r2 B1, T6 RST) | `handleGoAway` closes streams above the last id with Unavailable (any error code), so they retry. The reader receives `errStreamDrain`, and `RecvMsg` reports `the connection is draining` (measured: the pinned Go client's fatal second refusal, before and after headers). RST_STREAM gives `http2ErrConvTab` code and `stream terminated by RST_STREAM with error code: NAME`. DATA END_STREAM without trailers is Internal `server closed the stream without sending trailers`. | hyper turns h2 NO_ERROR errors (GOAWAY or RST) into a clean EOF, so the transport observes HTTP/2 frame headers itself, on post-TLS bytes and in both directions (`Wire`). Each response binds to the connection that carried it and that connection's newest client stream id (the session runs one DoGet at a time). Body end, or a body error, uses the first terminal frame for that stream. Server trailers are always authoritative. Observations apply only to a clean end, or to an h2 error the peer caused (`is_remote`); local or library h2 errors (for example a malformed trailer block) stay with `classify`, which gives Unavailable as Go's connection error does (r3 B1). Only frames for the opened stream are recorded (r3 L1). Unit-tested over raw frames: GOAWAY(0) with NO_ERROR and with INTERNAL after a message, GOAWAY right after headers, GOAWAY(1) followed by trailers, RST NO_ERROR and FLOW_CONTROL, DATA END_STREAM, a trailer without status, and a server status equal to tonic's text. |
| Partial gRPC messages (r3 M1) | `parser.recvMsg` reads the 5-byte prefix and body with `io.ReadFull`. Trailers (any status), RST_STREAM and DATA END_STREAM hand the reader io.EOF (`closeStream` at http2_client.go:1584/1229/1202), so a message cut short ends as Internal `unexpected EOF`. A GOAWAY hands it `errStreamDrain` instead, which stays Unavailable. | `GoBody` tracks gRPC framing across DATA frames. A partial message ended by trailers, DATA END_STREAM or RST becomes Internal `unexpected EOF`; GOAWAY or connection loss keeps Unavailable. Unit-tested for 3-of-5 prefix bytes and prefix plus partial body, with trailer codes 0/14/3, RST 0/2, DATA END and GOAWAY. A message split across DATA frames is complete. *The R6 process matrix passes; see the 56-case followup in the R6 compatibility evidence.* |
| Status details (r3 M2) | `istatus.NewWithProto`: one `grpc-status-details-bin` that unmarshals as `google.rpc.Status` with the header's code gives the proto's code and message. A different code (OK included) gives Internal `grpc-status-details-bin mismatch: grpc-status=%v, grpc-message=%q, grpc-status-details-bin=%+v`. Absent, repeated or non-proto values are ignored. | prost `RpcStatus{code, message, details: Any}` (proto3 UTF-8 rules), after Go base64 decoding. Same selection and code. The `%+v` prototext part is approximated (declared residue below). Unit-tested: matched, mismatched, empty for 14 and 0, OK mismatched, repeated, non-proto, and Trailers-Only. |
| Transparent retry (r3 M3) | `csAttempt.shouldRetry`: a first, uncommitted attempt whose stream is unprocessed (excluded by a GOAWAY, RST REFUSED_STREAM, or a draining transport) is re-issued once at once. A new stream never uses a transport that received GOAWAY. | `do_get` and `FlightStream` re-issue once when the exchange is marked unprocessed and no complete message was delivered. The marks come from a GOAWAY excluding the bound stream (frames, or a remote h2 GOAWAY error before or after the headers) or a remote RST REFUSED_STREAM. A connection that saw a GOAWAY is replaced by a fresh channel before the next attempt. A second refusal surfaces. Unit-tested: GOAWAY before or after headers retries on a new connection (2 accepted); REFUSED_STREAM retries on the same connection (stream 3); two refusals are fatal; a refusal after a message is not retried. *The R6 process matrix passes; see the 56-case followup in the R6 compatibility evidence.* |
| Pre-header resets (r3 L5) | `handleRSTStream` mapping at any stage | A remote RST before the response headers is mapped in the layer's error path with Go's code and text. Unit-tested with FLOW_CONTROL. |
| TLS details (r2 L1, L4, L6) | crypto/tls `first record does not look like a TLS handshake`. A close without close_notify reads as EOF at a record boundary. No `ClientSessionCache`. | rustls `InvalidContentType` is rendered with Go's text, other rustls texts are declared. `UnexpectedEof` before the preface follows Go's EOF/unexpected-EOF rule. Resumption is disabled. |
| Resolver shutdown (r2 L5) | Process exit does not wait for `getaddrinfo`. | Resolution runs on a detached thread, not tokio's blocking pool, so runtime drop never waits for it. |
| CONNECT (T7–T10, r2 L2/L3) | Plaintext `CONNECT host:port` with `Host`, `User-Agent: grpc-go/1.65.0`, and `Proxy-Authorization: Basic` when userinfo is present. Go `http.ReadResponse` rules: any EOF reads `unexpected EOF`; `HTTP/X.Y` must be single-digit; `ReadMIMEHeader` validates the initial line, missing colons, key tokens, value bytes and continuations; Content-Length conflicts and syntax are checked; 1xx, 204 and 304 carry no body. Only 200 is accepted, and `Body.Close` drains a declared body. A rejection dumps head and body (`failed to do connect handshake, response: %q`), or reports `status code: <Status>` if the body is short. I/O errors read `read/write tcp L->R: …`. Keys may contain spaces (go.dev/issue/34540; not canonicalized). | Same, validated line by line as read and kept as bytes (Go `%q` with `\xNN` for invalid UTF-8). Unit-tested, including the harness 407 oracle text with its `denied` body. Nothing past the declared body is read. |

## Divergences and unknowns (ledger; not parity)

- HTTP/2 `user-agent` is `tonic/0.14.6`; Go sends `grpc-go/1.65.0`. tonic always
  appends its own token, so the native value is kept. The harness compares
  request headers excluding only this one.
- Go dials eagerly (T3). `grpc.DialContext` leaves idle mode and starts
  resolving, the proxy CONNECT, TLS and the preface at client construction,
  before the auth metadata is built and before the watching line. Rust connects
  at the first DoGet. With invalid metadata, Go may already have connected or
  sent a CONNECT when DoGet fails with Internal. Whether that happened depends
  on timing, so fixtures must not assert either way. The 20 s deadline also
  starts at Dial in Go, which is negligible in practice. Error text and stage
  are unaffected.
- grpclog: with `GRPC_GO_LOG_SEVERITY_LEVEL=warning|info`, Go writes a
  timestamped warning when a peer has no ALPN, plus other library logs. Rust
  emits none. The default level (ERROR) normally prints nothing for these paths.
  *fixture*
- TLS, DNS and certificate failure details come from rustls and the system
  resolver (inside Go's wrappers), not Go x509/net. Address ordering comes from
  `getaddrinfo`, not Go's resolver. *fixture with dual-stack names and a
  blackholed primary*
- A TLS `:authority` for an IPv6 literal is `[::1]`; Go sends the unbracketed
  `::1`, which is not a valid `http::Uri` authority.
- TLS-layer read errors during the preface, other than a close (L4), use the TCP-style text. Go's
  `tls.Conn` may report alert-specific errors instead. A first frame that is
  not SETTINGS is rejected by h2 with its own text, where Go says `initial
  http2 frame from server is not a settings frame: …`.
- Over-limit rewrite limitation: tonic gives no local/remote marker on a
  `Status`. A server that sends exactly tonic's OutOfRange limit text with our
  limit, a larger size and no details or metadata would also be rendered as
  ResourceExhausted. Go would print it verbatim as OutOfRange. Both are fatal.
- Status-details mismatch text: the `grpc-status-details-bin=%+v` tail is
  protobuf-go single-line prototext. Its field separator is one space plus a
  `detrand` space seeded from the Go executable's own hash, which is two spaces
  for the pinned binary (measured: `code:3  message:"proto message"`); Rust
  emits that separator and protobuf-go's string escaping, so `code`, `message`
  and plain `Any` fields match byte for byte. Remaining limits:
  - Go expands an `Any` whose type URL is registered in the Go binary as
    `[type.googleapis.com/…]:{…}`; Rust always prints `type_url`/`value`, which
    matches only unregistered types.
  - Go's `Format` emits unknown fields (by number); prost drops them, so a
    details proto with unknown fields prints without them.
  - A rebuilt Go binary may hash to a one-space separator; Rust follows the
    pinned binary.
  - Go and prost may disagree on exotic malformed protobuf (for example
    groups in unknown fields); either way the details are ignored or decoded
    consistently with the code.
- Mid-stream HEADERS without END_STREAM (r3 L2): Go says Internal
  `a HEADERS frame cannot appear in the middle of a stream`; h2 resets with
  PROTOCOL_ERROR (tonic Internal with h2 text). Both are fatal, with different
  diagnostics.
- **Informational HTTP/2 Flight responses are unsupported (IA-2).** This is
  an output/stage/outcome difference, not a text-only exception. An actual
  103 followed by 200, schema, batch and fatal trailers makes Go terminate
  immediately with Unknown `unexpected HTTP status code received from server:
  103 (Early Hints)` and empty stdout. Rust skips 103, renders the batch and
  reaches the later status. A subsequent unbounded stream could therefore
  continue in Rust where Go has terminated (source inference, not a soak).
  The locked h2 0.4.20 API exposes `ResponseFuture::poll_informational`, but
  Hyper 1.12.0's HTTP/2 adapter does not call it; Hyper's `on_informational`
  callback is implemented only for HTTP/1. The current tonic Channel adapter
  cannot intercept these headers. The explicit phase decision is to retain
  this limitation rather than fork the dependency or introduce a second HPACK
  parser. These cases are excluded from parity, and the earlier assertion
  that both clients always fail at the same stage is withdrawn. See
  [the independently reproduced audit](phase6-interim-source-audit.md).
- A `-bin` failure index with interleaved repeated keys (r3 L3): http's
  `HeaderMap` groups values by key, so with keys A, B, A all malformed Rust
  reports B and Go reports the last A.
- TLS `first record` text (r3 L4) is applied to any rustls
  `InvalidContentType` during the handshake. A bad record after a valid first
  server record would get Go's first-record text instead of Go's
  record-specific error.
- Diagnostics: the session's subscription count is per application `do_get`.
  A transparent retry is a second physical DoGet inside one subscription;
  fixture request captures count physical attempts. No per-attempt counter is
  exposed.
- Preface-stage write failures after the client preface (for example the
  initial SETTINGS in its own write) are left to the read side; grpc-go would
  report `transport: failed to write initial settings frame: …` if its own
  buffered SETTINGS flush failed first. hyper normally writes the preface and
  SETTINGS together, so the preface-write text applies.
- Response-layer ordering: Go reports the first malformed `grpc-status` and the
  last malformed `-bin` field in wire order. Rust iterates http's `HeaderMap`,
  which groups repeated names, and differs only when several fields are
  malformed.
- A connection lost before the first message (Rust DoGet or reader
  initialization; Go's DoGet does not wait for headers) keeps tonic/h2 text
  under Unavailable, where Go says `error reading from server: …`. Witness:
  review-wave `close-after-headers` (200 headers then FIN): Go
  `error reading from server: EOF`, Rust `h2 protocol error: error reading a
  body from connection`; same code, stage outcome and empty stdout (I1). An HTTP/1.x peer on the Flight
  port gives h2's FRAME_SIZE GOAWAY text, where Go says `error reading server
  preface: http2: frame too large`. Both are fatal in both clients.
- After an established stream ends, grpc-go may be in connection backoff, so
  the next DoGet can fail fast with the last connection error. tonic dials
  again on each call. Both are fatal on failure, but the exact timing and text
  of a failed reconnect are *fixture* items.
- CONNECT, still divergent:
  - Go reads a close-delimited 200 body (HTTP/1.0 without Content-Length, or
    `Connection: close`) to EOF, which hangs until the proxy closes and then
    breaks the tunnel. Rust uses the tunnel.
  - `Transfer-Encoding` (chunked bodies, or unsupported-encoding errors) is not
    interpreted.
  - A rejection dump uses the raw head bytes. Go re-serializes it, which differs
    only for non-canonical header spelling or order.
  - A continued header field is validated once the next line is complete; Go
    peeks one byte of that line.
  - Neither client bounds the CONNECT exchange in time, and neither caps the
    head size.
- Proxy and profile URL parsing: Go `url.Parse` failures (`invalid proxy
  address %q: …`) are only approximated for values without a usable authority.
  Host percent-escapes and IDNA are not decoded or punycoded, in the proxy URL,
  the profile host or the CONNECT `Host` (T13).
- The shared `no_proxy_matches` CIDR matching ignores IPv4-in-IPv6 forms.
- A host literally named `unix`, `dns` or `passthrough` is parsed by Go as a
  resolver target (T15); Rust treats it as a hostname.
- With two invalid metadata values, Go's map iteration order picks which key is
  named in the error. Rust always names the first in fixed order.
- A Trailers-Only error appears at `do_get` in Rust and at the first `Recv` in
  Go. Both stages are fatal, so the outcome is the same.
- Read-ahead and retained memory under a slow sink depend on hyper's BDP
  estimator versus grpc-go's. *measure*

## Review wave (transport, Opus) dispositions

Evidence: `docs/phase6-review-wave-transport-opus.md` and its lane cache;
owner probes `/home/ajs/.cache/pb-phase6/tmp/transport/probe/probe.py`
(frozen Go `fae8411f…`, fixture r5 `c85f63f9…`; runs `go-v1` and `cand-v1`,
the latter against a debug candidate built from this source). In `cand-v1`
all 15 cases match Go on stdout hash, final error bytes, DoGet count and
connection count.

| Item | Disposition |
| --- | --- |
| F1 announced `grpc-encoding` | **Fixed.** grpc-go records the encoding and applies `checkRecvPayload` per message after reading the whole body; tonic (no decompressor) rejected the response at once. The response layer now hides `grpc-encoding` from tonic and follows each message's flag: flag-0 bytes go to tonic unchanged (its size gate and the exact rewrite still apply); from a nonzero flag byte on, nothing more is forwarded. In Go's measured order: a declared length over 4 MiB is ResourceExhausted at the prefix; a body that ends early is `unexpected EOF`; a complete body gives Unimplemented `grpc: Decompressor is not installed for grpc-encoding %q`, Internal `grpc: compressed flag set with identity or empty encoding`, or Internal `grpc: received unexpected payload format N`. The status is set only when the error is emitted, after every earlier complete message (even in the same DATA frame) has been forwarded, so it can neither displace nor precede one. 12 Go-measured cases match (`go-v1`/`cand-v1`); raw-frame unit test `payload_flags_follow_go_check_recv_payload`. No decompressor or codec feature was added (the pinned Go client registers none and advertises no `grpc-accept-encoding`). |
| F2(a) empty DATA lifetime cap | **Fixed natively for unpadded frames only.** `Observed` drops unpadded, zero-length DATA frames without END_STREAM on a nonzero stream after the server's first frame and outside an open header block, before h2 counts them (h2 0.4.20 sends GOAWAY(ENHANCE_YOUR_CALM) after 100 per connection lifetime). grpc-go's `handleData` does nothing for such frames, and they carry no data or flow-control weight. The first frame, PADDED, END_STREAM, stream 0, every other frame type, and any frame while a HEADERS/PUSH_PROMISE block awaits its END_HEADERS CONTINUATION pass unchanged, so h2 still enforces SETTINGS-first and still rejects DATA interleaved in a header block (review r2 R2-1; Go's x/net `checkFrameOrder` rejects DATA inside an open HEADERS block too; PUSH_PROMISE differs as clarified below). The open-block state is one flag, set by a kept HEADERS or PUSH_PROMISE without END_HEADERS and cleared by CONTINUATION with END_HEADERS. Frame headers are withheld until complete (straddling headers finish with reads of at most 8 bytes; withheld bytes reach h2 at EOF, and are dropped on a read error). Each `poll_read` performs at most 32 reads that yield only discarded frames, then wakes itself and returns `Pending`; it never returns an empty `Ok`. Unit test `empty_data_filter_is_exact_and_bounded` (every split position, byte-by-byte, EOF/error with withheld bytes, zero-capacity buffer, genuine padded frames (length 1 pad 0; length 2 pad 1) and the malformed length-0 PADDED form untouched, open header block whole and split at every byte, PUSH_PROMISE, a closed HEADERS, first-frame strictness, a 1000-frame always-ready run); raw-frame tests `empty_data_frames_do_not_reach_h2_limits` and `empty_data_inside_a_header_block_still_fails_in_h2` (the interleave ends Unavailable through h2's protocol error; the same block without it streams); with the open-block check disabled both filter tests fail (scratch control). Process cases `empty-data-101` and `empty-data-60x2-across-reconnect` match Go (unpadded). |
| R2-2 CL-3 padded empty DATA lifetime cap | **Declared capability limit (owner decision: keep h2 flow-control enforcement; no padded-byte dropping, HPACK parser, second HTTP/2 stack or fork).** A PADDED DATA frame whose payload is empty (length 1 with pad length 0, or more padding) carries flow-control weight, so the filter leaves it to h2, which counts it toward the same 100-per-connection lifetime cap (`Streams::recv_data` → `record_data_frame` with the unpadded payload length; `counts.rs`). Past 100 such frames on one connection h2 sends GOAWAY(ENHANCE_YOUR_CALM); the measured after-message case ends Unavailable, so the session reconnects and loses the rest of that subscription. The cap can also be reached before the first complete message; at that stage an Unavailable is fatal during initialization, not an established-read retry. That earlier-stage outcome is source-inferred, not measured by this unit probe. grpc-go (source: `handleData` only returns the padding's window) keeps streaming. Rust behavior reproduced at unit level with a byte-level raw HTTP/2 server (`padded_empty_data_keeps_h2_lifetime_cap`: 100 frames accepted, 101 → Unavailable, for both length 1/pad 0 and length 2/pad 1). Go side source-only: not run (the fixture cannot script padding); no measured Go claim. The root's limits record carries the capability table and process evidence. |
| F2(b) CL-1 `content-length` mismatch | **Declared capability limit.** h2 enforces RFC 9113 content-length against the DATA total and resets the stream (RST PROTOCOL_ERROR); grpc-go 1.65 ignores the field. Rust fails before output (Internal h2 text) where Go renders. A header-level HTTP/2 violation decided inside h2's HPACK/stream state; not corrected here (no h2 fork, no second HPACK parser). Process evidence and controls are owned by the harness limit record. |
| F2(c) CL-2 connection-specific header | **Declared capability limit.** h2 rejects connection-specific response fields (`connection: keep-alive`) as malformed (RST PROTOCOL_ERROR); grpc-go accepts them. Rust fails before output where Go renders. Same reasoning and evidence ownership as CL-1. |
| F2(d) tiny-frame overhead budget | **Unknown, source only.** h2 charges frames under 256 bytes against a budget of max(window/2, 25600) that consuming data releases (`counts.rs`, `connection.rs`). A prompt reader did not trip it (review witness `tiny-frames-first-message`); whether a slow sink buffering many tiny frames trips it is unmeasured (no pressure runs). Not asserted as a tested limit. |
| F3 retry channel reuse | **Fixed.** The replaceable `(client, wires)` slot is shared between `FlightConnection` and a stream's transparent retry, so the channel a post-header retry builds is the one the next application DoGet uses. Unit test `retry_channel_serves_the_next_do_get` (2 accepted connections, next DoGet on stream 3); process case `goaway-after-headers-then-reconnect` matches Go (2 connections, streams (0,1),(1,1),(2,3)). The before-headers refusal control is unchanged. |
| F4 CONNECT User-Agent | **Decision: keep `User-Agent: grpc-go/1.65.0` on the proxy CONNECT request** as a compatibility token. The CONNECT head is a byte contract with the pinned client that proxies and the harness compare. This makes no claim about client identity: the HTTP/2 `user-agent` stays tonic's native value (declared above), and both rows are kept separate. |
| I1 | Ledger wording corrected (connection loss before the first message). |
| I2 | **Unproven source race, no change.** grpc-go transparently retries a stream whose creation fails on a closing transport; a request racing a bare peer FIN at reconnect would surface from hyper as closed/canceled and be fatal at DoGet in Rust. GOAWAY-driven drains are covered. No fixture reproduces it, so no broader retry was added. |

## Fixtures requested from the harness

- TLS server that records `:authority` (hostname and IP literal), negotiates no
  ALPN, with and without `GRPC_ENFORCE_ALPN_ENABLED=true`.
- TLS or plaintext protocol mismatch, and a peer that accepts but never sends
  SETTINGS (Go fails at 20 s).
- Connection loss after batches: FIN, RST (`SO_LINGER=0`), GOAWAY with a last
  stream id below ours, and graceful shutdown. Check the DoGet count, the
  roughly 500 ms gap and the retained stdout.
- A trailer with a 20 KiB `grpc-message`, before and after batches.
- CONNECT proxy capture covering both plaintext and TLS Flight:
  - request bytes, auth/no-auth, a 407 response, and a proxy URL without a port
  - a 200 with `Content-Length` and tunnel bytes, and `HTTP/1.0 200`
  - `NO_PROXY` bypass and loopback bypass
- All request headers except `user-agent` in all four auth modes.
- Refused port, port 0, `grpcPort` of -1 or 70000, and missing `grpcPort`. Each
  should give the exact final stderr in text and JSON modes.
- Over-limit message (4 MiB + 1) and exactly at the limit.

### Round-3 documentation clarification

The filter conservatively tracks both HEADERS and PUSH_PROMISE blocks. The
source comment grouping them under x/net parity is broader than the measured
claim: x/net tracks an open HEADERS block; for PUSH_PROMISE followed by DATA
and CONTINUATION it rejects the later unexpected CONTINUATION rather than the
interleaved DATA. Both paths terminate the connection, but identical rejection
stage is not claimed. This is a documentation erratum for the frozen R6 source
comment, not a source change or an additional verified process case.

The fresh [Opus round-3 review](phase6-review-wave-transport-opus-r3.md) passes
39 transport tests three times and closes the open-header-block correction.
Full R6 process evidence subsequently passes the unchanged 291 contracts and
15 transport-wave cases; explicit capability limits remain excluded.

### Whole-source R6 hostname edge (L-3)

The local Flight hostname extractor preserves the raw host; Go's `url.Parse`
percent-unescapes accepted host bytes before `Hostname` and TLS/dial use.
Reachability depends on successful discovery through the shared HTTP URL
parser. Percent-encoded non-ASCII hostnames that it accepts may therefore
produce a different Flight destination or dial diagnostic. This is an
unprobed, source-derived URL-parser compatibility edge, not measured failure
parity or a guaranteed failure for both clients. A future shared Go-compatible
host parser must consider this boundary; no broad URL/HTTP refactor is part
of Phase6. See L-3 in the whole-source Opus review.
