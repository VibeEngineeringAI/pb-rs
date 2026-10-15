# Phase 6 Flight transport review, round 1

Scope: `src/tail/transport.rs` and its notes (`docs/phase6-transport-notes.md`),
read against Go `07af390` `cmd/tail.go`, `pkg/http/http.go`, grpc-go v1.65.0,
arrow/go v13.0.0, Go 1.25.4 `net/http` (the oracle's toolchain), and the
locked tonic 0.14.6, hyper 1.12.0 and h2 0.4.20 sources. I only read source.
Nothing was built, run or timed, and nothing here is fixture evidence. This
report does not accept the phase.

The labels below mean:

- **New**: not in the declared divergence list.
- **Declared**: already in the ledger. I only comment where its wording needs
  tightening.
- **Predicted**: deduced from dependency source. A fixture must confirm it.

## Blocking or likely blocking (new)

### T1. Losing the connection mid-stream ends the session where Go reconnects (new, predicted)

Suppose a stream is established and the connection then drops. The server may
send a TCP FIN or RST, or close after a GOAWAY that leaves out our stream.
grpc-go then closes every active stream with `codes.Unavailable`
(`internal/transport/http2_client.go:1022-1030`; the reader calls
`t.Close(connectionErrorf(... "error reading from server: %v"))` at :1647).
`isStreamEnd` treats Unavailable as a stream end, so Go waits 500 ms and
reissues DoGet.

tonic takes a different path:

- h2 records a closed connection as an I/O error. `recv_eof` stores
  `BrokenPipe "stream closed because of a broken pipe"` (`h2 state.rs:309-321`,
  `connection.rs:620-621`). A reset stores the I/O error through
  `handle_error`. A GOAWAY that leaves out our stream gives `Kind::GoAway`
  with `NO_ERROR`.
- The body error is passed to `Status::map_error` (`tonic codec/decode.rs`).
  From there `find_status_in_source_chain` calls `from_hyper_error`, then
  `code_from_h2` (`tonic status.rs`):
  - I/O errors have no `reason()` and map to `Code::Unknown`.
  - GOAWAY or RST with `NO_ERROR` maps to `Code::Internal`.

`session.rs:280` restarts only on Cancelled, Unavailable or OK. So Rust
predictably ends with something like `rpc error: code = Unknown desc = h2
protocol error: error reading a body from connection`. That happens on
ordinary server restarts, load-balancer idle closes and rolling deploys,
which are the events the reconnect contract in `rust-phase6.md` exists for.

The error adapter belongs to the transport module, since `status_message`
lives there. A narrow fix would classify a status whose source chain holds an
`h2::Error` with `is_io()`, or `is_go_away()` with a non-remote-reset cause,
as Unavailable. That fix needs an explicit decision. Only the code matters for
the restart path. The text is visible only if the error is fatal.

To reproduce, add fixture cases that each send a schema and one batch, then:

1. close the socket (FIN),
2. close with `SO_LINGER=0` (RST),
3. send `GOAWAY(NO_ERROR, last_stream_id < stream)` and close,
4. do a graceful server shutdown.

For each case, check the DoGet count, the roughly 500 ms gap and the retained
stdout in both clients.

### T2. TLS `:authority` differs from Go (new, wire contract)

grpc-go 1.65 `determineAuthority` (`clientconn.go:1817-1830`) uses
`creds.Info().ServerName` when it is set. `tailTransportCredentials` sets
`ServerName` to the hostname, so Go's TLS requests carry `:authority:
<hostname>` with no port. tonic takes the authority from the endpoint URI
`https://host:port` (`transport.rs:409`). Plaintext matches: both send
`host:port`.

This matters to authority-routed ingresses and virtual hosts, which is the
Cloud path. A scoped fix is `Endpoint::origin("https://<hostname>")` for TLS
(`AddOrigin`, tonic `add_origin.rs`). An IPv6 TLS host cannot be matched
exactly: Go sends an unbracketed `::1`, which is not a valid `http::Uri`
authority. Declare that residue.

Fixture: a TLS server that records `:authority` with the hostname and with an
IP literal.

### T3. "Lazy dial" is not Go's behavior (new, notes are wrong)

arrow `NewClientWithMiddlewareCtx` calls `grpc.DialContext`
(`arrow/flight/client.go:294`). grpc 1.65 `DialContext` calls
`cc.idlenessMgr.ExitIdleMode()` before it returns (`clientconn.go:229-241`).
Go therefore starts resolving, dialing, the proxy CONNECT, TLS and the HTTP/2
preface in the background at client construction. That happens before
`tailAuthMetadata` runs and before the watching line is written.

Observable effects:

- With an invalid (non-printable) metadata value, Go has already connected,
  and a proxy has already seen a CONNECT, when DoGet fails with Internal.
  Rust never connects. The fixture request "invalid value with no connection
  attempted" (notes, Fixtures section) encodes the wrong Go expectation.
- Go's 20 s connect deadline starts at Dial, not at DoGet. This is negligible
  in practice.

The error text and stage are unaffected. Either declare this divergence and
fix the notes' "Lazy dial" row and the fixture expectation, or start a
background connect after discovery. I recommend declaring it.

## Medium (new)

### T4. HTTP/2 max header list size: 16 KiB versus Go's 16 MiB (predicted)

tonic leaves `http2_max_header_list_size` unset, so hyper's 16 KiB default
applies (`hyper-1.12.0 proto/h2/client.rs:52`). grpc-go uses
`defaultClientMaxHeaderListSize = 16 << 20` (`transport/defaults.go:47`).

If a response header block or trailer, such as a long `grpc-message`, exceeds
16 KiB, h2 rejects it as oversize (`h2 recv.rs:221-238`). By its comment it
resets the stream with REFUSED_STREAM, and tonic maps REFUSED_STREAM to
**Unavailable**. That can turn a fatal server status into a silent reconnect
loop: Go prints the status and exits, while Rust restarts every 500 ms. A
server that honors our SETTINGS may instead send RST INTERNAL.

Fix: `endpoint.http2_max_header_list_size(16 << 20)`. This is a one-line,
parity-restoring setting. Fixture: a trailer with a 20 KiB `grpc-message`,
both before and after batches.

### T5. HTTP/2 flow-control windows are an unpinned dependency default (resource)

hyper's defaults give a static 2 MiB stream window and a 5 MiB connection
window (`client.rs:48-49`), because tonic passes `None`. grpc-go starts at
64 KiB with BDP growth up to 16 MiB (`bdp_estimator.go:30`). This sets
read-ahead under a slow sink. The "one batch in flight" handoff does not
bound it.

These are not parity text, but the resource envelope in `rust-phase6.md`
should name them. They should also be set explicitly, either to the current
values or to `http2_adaptive_window(true)`, so a dependency bump cannot move
them silently.

### T6. HTTP-status and h2-reset diagnostics are undeclared (new, text only)

- An ingress or proxy answering DoGet with a non-gRPC HTTP status, such as
  502/503 HTML: tonic says `grpc-status header missing, mapped from HTTP status
  code N`. Go says `unexpected HTTP status code received from server: N (Text);
  transport: received unexpected content-type "text/html"`
  (`http2_client.go:1453,1489`). The code tables agree and both are fatal, so
  only the text differs. This case is plausible on Cloud.
- RST codes: FLOW_CONTROL is ResourceExhausted in Go and Internal in tonic.
  STREAM_CLOSED, FRAME_SIZE and HTTP_1_1_REQUIRED are Internal in Go and
  Unknown in tonic. All are fatal.
- An unknown numeric `grpc-status` such as 20 prints `Code(20)` in Go and
  `Unknown` in Rust.

Add all three to the ledger. The first should be a fixture.

## Low (new unless marked)

- **T7. Empty CONNECT response EOF text.** Go's `ReadResponse` always converts
  `io.EOF` to `io.ErrUnexpectedEOF`, for both the status line and the headers
  (Go 1.25.4 `net/http/response.go:161-166,188-193`). Rust reports `EOF` when
  the proxy closes before sending any byte (`transport.rs:831-837`). This is a
  concrete text bug with a trivial fix. There is no test for it.
- **T8. HTTP version strictness.** Go's `ParseHTTPVersion` accepts only
  `HTTP/X.Y` with single digits (`request.go:817-842`). `connect_status`
  accepts up to three digits per part (`transport.rs:882-889`). For example,
  `HTTP/1.10 200` gives a tunnel in Rust and `malformed HTTP version` in Go.
- **T9. CONNECT header block not validated.** Go's `ReadMIMEHeader` rejects
  malformed header lines (`malformed MIME header line`). Rust only scans for
  the blank line and accepts the 200. I have not checked how Go's
  `readTransfer`/`Body.Close` handles a 200 that carries `Content-Length`
  (whether it consumes tunnel bytes). Add it to the fixture list.
- **T10. CONNECT I/O error text.** Go wraps `net.OpError`, for example
  `write tcp a->b: write: broken pipe` and `read tcp a->b: read: connection
  reset by peer`. Rust prints only the OS message (`transport.rs:819-844`).
  This is not in the declared list.
- **T11. Proxy bypass for IPv4-mapped loopback.** Go's
  `net.ParseIP("::ffff:127.0.0.1").IsLoopback()` is true. Rust's
  `Ipv6Addr::is_loopback` is false (`transport.rs:518`). The shared
  `no_proxy_matches` CIDR matching also ignores 4-in-6. Declare this; no shared
  refactor needed.
- **T12. Unsupported-scheme message case.** Go `url.Parse` lowercases
  `Scheme`, so the message quotes `"ftp"` for `FTP://`. Rust quotes the raw
  scheme (`transport.rs:378`). This is probably unreachable: self-hosted
  discovery fails first on a non-HTTP scheme, and Cloud selects TLS.
- **T13. Profile host percent-escapes and IDNA.** Go `url.Parse` unescapes the
  host, and `Request.Write` punycodes the CONNECT host. The ledger declares
  undecoded escapes only for the proxy URL. Extend that to the profile host and
  the CONNECT `Host` header.
- **T14. Protocol-mismatch peers.**
  - Plaintext client to a TLS port, or TLS client to a plaintext port: Go
    reports Unavailable `error reading server preface` / handshake errors
    within 20 s. The Rust code and text are unknown, and the preface wait is
    unbounded.
  - The declared "does not bound the preface" item should say what that means
    in practice: a peer that accepts TCP/TLS but never speaks HTTP/2 hangs
    DoGet indefinitely, where Go fails with Unavailable after 20 s.

  Both are common user misconfigurations and need fixtures.
- **T15. Hostnames that are gRPC resolver schemes.** Informational. A host
  literally named `unix`, `dns` or `passthrough` makes Go parse `unix:8001` as
  a resolver target.

## Declared items: comments only

- The 20 s fresh TLS timeout, the user-agent, grpclog/ALPN warnings, and
  TLS/DNS text are acceptable as declared.
- Malformed proxy parse and the non-200 dump body are acceptable as declared.
- The local-cap ambiguity is acceptable as declared. The matcher is as narrow
  as tonic allows (`decode.rs:190-196` emits exactly this text with an empty
  `Status`).
- Leaving the CONNECT head uncapped matches Go's unlimited `ReadMIMEHeader`
  and the earlier decision.

## Verified against source (no finding)

- Ticket bytes and HTML/U+2028 escaping.
- Metadata:
  - keys and raw cookie;
  - Basic encoding;
  - the 0x20–0x7E validation text (`internal/metadata/metadata.go:128`);
  - Internal code, raised before transport wait in `newClientStream`.
- ALPN enforcement:
  - variable semantics (`envconfig.go:48`, `boolFromEnv` default false);
  - Go behavior (`credentials/tls.go:129-133`);
  - tonic `assume_http2` gate (`tls.rs:236`).
- Dial errors: `ConnectError` maps to Unavailable with our Display
  (`status.rs:653-655`). A lazy `Reconnect` stores the connect error instead of
  failing the `Buffer` (`reconnect.rs:243-256`), and reconnects from Idle after
  a lost connection.
- Receive limit: checked before buffering, `len > limit`, as Go does.
- Dialing:
  - `dialParallel`, `dialSerial` and `partialDeadline` match Go `net`,
    including the expired-context address;
  - `SO_KEEPALIVE` with OS defaults matches `tcp_keepalive_unix.go:36-53`;
  - `TCP_NODELAY`.
- Proxy:
  - selection via `mapAddress` with an https URL, applied even to plaintext;
  - `parseProxy` retry;
  - userinfo decoding;
  - verbatim host;
  - CONNECT byte order (`proxy.go:135-171`, `Request.Write` puts User-Agent
    before the other headers);
  - 200-only acceptance;
  - no over-read.
- Port range/negative texts match `LookupPort` (`address N: invalid port`).
- Credential snapshot: Go refreshes inside `cloudRefreshTransport` on the same
  `*Profile` that tail later reads (`pkg/http/http.go:46-59`).
  `ApiClient::profile()` is read after discovery.

## Test gaps

1. No TLS-path test. Nothing checks that `tls_config` is applied, the domain
   name, the `assume_http2` wiring, or `:authority`.
2. The over-limit mapping is tested only as a string pattern. No test drives
   tonic's decoder with a 4 MiB+1 length prefix, or exactly 4 MiB, so a tonic
   text change would go unnoticed.
3. No test of mid-stream connection-loss classification (T1) or of
   oversized-trailer behavior (T4).
4. Request capture compares auth metadata only. Compare every request header
   except `user-agent`: `:authority`, `content-type`, `te`, and
   `grpc-accept-encoding`. Go advertises gzip if any linked package registers
   it.
5. Proxy routing is not exercised through `FlightTarget::client`, for example
   with a bracketed IPv6 address. The `from_env` upper/lower fallback and
   `enforce_alpn` parsing are untested; they need serialized environment
   tests.
6. CONNECT edge cases are untested: an empty response (T7), `HTTP/1.10`
   (T8), malformed headers (T9), and a 200 with `Content-Length`.
7. `about_grpc_port` lacks the `1e3`, `1.0`, `-0` and `2^63` cases against
   Go's int unmarshal text.
