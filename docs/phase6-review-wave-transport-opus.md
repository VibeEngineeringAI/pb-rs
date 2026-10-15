# Phase 6 review wave: Flight transport, protocol, auth and TLS (Opus)

This is an independent, focused lane review. It is not final whole-phase
acceptance. I edited no shared source, tests, harness or notes. This report and
the lane cache `/home/ajs/.cache/pb-phase6/review-wave-transport-opus` are the
only files I wrote.

## Snapshot

| Item | SHA-256 |
| --- | --- |
| `src/tail/transport.rs` (reviewed) | `77dd92bb0bf34c620bcb2ca259347f0d6b27d83d72593a76e912984858165240` |
| `src/transport.rs` (shared, read-only) | `d4b9d521eb726bb24601749b50369f1708d0027b9699874b518b5e7faa49dd7c` |
| `Cargo.toml` | `eb55b298c3158cf32c51a9876f53d3ebe220adbd7eaa51fd1af8fe4d1dc0c83c` |
| `Cargo.lock` | `aa35f338bd6b6b803c5e970f021d59f54d2e7435823f75a5baee018d7a1ebb44` |
| Frozen R4 candidate `candidate-review-r4/pb` | `fe26926a98cba5e00774e26c5eab33c7c35ebd570ddaec7ceb38ddef663ecc43` |
| Frozen Go 07af390 `baselines/pb-go` | `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314` |
| Immutable fixture r5 `tmp/fx/pb-fixture-r5` | `c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314` |

- Base `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`, with an uncommitted worktree.
- The first four hashes match the R4 build manifest
  (`candidate-review-r4/manifest.json`). The R4 binary is therefore
  representative of the transport behavior reviewed here. I rechecked the
  hashes at the end of the review.
- `src/tail/session.rs` was observed at `d86a574f…866f`. It belongs to the
  concurrent owner and is unsettled. I read only its use of `do_get` and
  `FlightStream`, and I make no judgment on it.

Locked dependency sources I read: h2 0.4.20, hyper 1.12.0, tonic 0.14.6 (the
`server` feature is enabled through `transport`), and arrow-flight 60.0.0
features. The Go oracle sources are grpc-go v1.65.0 from the module cache and
`cmd/tail.go` at 07af390 (`git show` in the main checkout). The frozen Go binary
reports go1.25.4, grpc v1.65.0, x/net v0.30.0 and arrow/go/v13 v13.0.0
(`go version -m`).

Evidence labels:

- **measured**: my own process probe against the frozen binaries above.
- **source**: read in the pinned or locked source.
- **inferred**: deduced, not run.

## What I ran

All probes are bounded functional process runs. The repository harness's
`phase6.sample()` drives the frozen Go and R4 binaries against the immutable r5
fixture's `raw_h2` scripted endpoint. The template is the existing
`p6-raw-message-ff-3` case; only the endpoint acts change.

- `TMP` was redirected to the lane cache.
- Each case runs once per binary, in JSON mode.
- No timing, pressure, soak or profiling was done. I did not rerun the
  291-case corpus or any central test.

Script: `probe/probe.py`, final sha `38f46117a4381807195336de491a6f94eab6c41ab1882103a57546c60abf9a91`.
Scenarios were added between runs; earlier scenario definitions were never
changed.

Each run used this command form, with lane-local temporary directories:

```
cd /home/ajs/.cache/pb-phase6/review-wave-transport-opus && \
  TMPDIR=$PWD/tmp GOTMPDIR=$PWD/tmp python3 -I probe/probe.py <run> <scenario…>
```

| Run | Report SHA-256 | Scenarios |
| --- | --- | --- |
| `runs/v1` | `2271711f…7cbe` | `enc-identity-control` |
| `runs/v1b` | `e2d542ad…e3cf` | `enc-gzip-uncompressed`, `empty-data-101`, `empty-data-100-control`, `goaway-after-headers-then-reconnect`, `goaway-before-headers-then-reconnect` |
| `runs/v1c` | `7d0d4e01…427e3d` | `tiny-frames-first-message`, `tiny-frames-control-one-frame` |
| `runs/v1d` | `25e19778…2c87a` | `empty-data-60x2-across-reconnect` |
| `runs/v1e` | `57249445…f79a5` | `content-length-mismatch`, `connection-specific-header` |
| `runs/v1f` | `04ef7013…ce68ab` | `close-after-headers` |

Full hashes are in `run-v1-f-summary.txt` in the lane cache. Each run directory
keeps both roles' raw `stdout.bin`, `stderr.bin` and `result.json`.

The pinned toolchain was used only for `go version -m` on the frozen Go binary.
That binary is stripped, so the request headers were used to check compressor
registration (F1).

## Findings

### F1. Medium (measured): `grpc-encoding` is left to tonic, which rejects the response where grpc-go streams

**Source:**

- `GoChannel::call` (`transport.rs:838-852`) removes only `grpc-status`,
  `grpc-message` and `-bin` fields. Its comment says that grpc-encoding stays
  with tonic.
- tonic `Grpc::create_response` (`client/grpc.rs:333`) calls
  `CompressionEncoding::from_encoding_header` (`codec/compression.rs:131-171`).
  With no tonic compression feature enabled, any value other than `identity` is
  rejected immediately. The result is Unimplemented `Content is compressed with
  \`X\` which isn't supported`, inside `do_get`, before any message.
- grpc-go 1.65 only records the value (`http2_client.go:1459`). It looks up a
  decompressor lazily (`stream.go:1070-1085`) and checks each message's flag
  after reading the whole message (`rpc_util.go:726-760` `checkRecvPayload`).
  So an uncompressed (flag 0) message is accepted whatever the announced
  encoding.
- The frozen Go binary registers no compressor. Its DoGet sends no
  `grpc-accept-encoding` (measured from the request metadata in
  `runs/v1b/enc-gzip-uncompressed-json/go/result.json`). R4 sends none either.

**Witness:** `enc-gzip-uncompressed`. Response headers are
`:status 200, content-type application/grpc, grpc-encoding gzip`, followed by a
schema, a one-row batch and trailers with status 3.

| Client | stdout | Outcome |
| --- | --- | --- |
| Go | 162 bytes (1 row, 1 batch), sha `50f93c83…` | InvalidArgument at the established read |
| R4 | 0 bytes | `rpc error: code = Unimplemented desc = Content is compressed with \`gzip\` which isn't supported`, at DoGet |

The control `enc-identity-control` matches byte for byte.

**Contract impact:** stdout, failure stage and status all differ. This is not
text-only, and it is not in the ledger. A fully compressed stream is fatal in
both clients, but the code, text and stage differ there too:

- Go: Unimplemented `grpc: Decompressor is not installed for grpc-encoding "gzip"`, at the first Recv;
- Rust: tonic's text, at DoGet.

The trigger is a peer or intermediary that announces an encoding the client did
not advertise and then sends flag-0 messages. The per-message flag allows this.
Conformant servers that honor accept-encoding cannot trigger it. **Likelihood
is low, but the effect is total output loss.**

**Minimal native fix (in the existing layer, no new dependency):**

1. Remove `grpc-encoding` from the initial headers handed to tonic. tonic has no
   decompressor enabled, so it can only reject. Keep the raw value in `GoBody`.
2. When `Framing` completes a 5-byte prefix whose flag is not zero, record Go's
   `checkRecvPayload` status in the exchange:
   - flag 1 with an empty or `identity` encoding: Internal
     `grpc: compressed flag set with identity or empty encoding`;
   - flag 1 with any other encoding: Unimplemented
     `grpc: Decompressor is not installed for grpc-encoding %q`;
   - any other flag: Internal `grpc: received unexpected payload format N`.

   tonic's own flag rejection then ends the stream, and `FlightStream` takes
   the exchange status first.
3. Declare the remaining ordering residue. Go checks the size limit before the
   flag and reads the whole body first; tonic checks the flag at the prefix. So
   a nonzero flag combined with an over-limit length, or a partial compressed
   message, keeps tonic's ordering.

**Tests:**

- raw-frame unit tests: gzip announced with flag 0 (renders), flag 1 with gzip,
  flag 1 without an encoding, and flag 2;
- the same as process cases.

### F2. Medium (measured): h2 0.4.20 enforces HTTP/2 rules that grpc-go 1.65 does not; Rust fails or silently reconnects where Go streams

This is one undeclared family, alongside the declared IA-2 (informational
responses). Each rule is enforced inside h2 before hyper, `GoBody` or
`GoChannel` sees the frame, so the response layer cannot correct it.

**(a) A lifetime cap of 100 empty DATA frames that do not end the stream, per
connection** (h2 `proto/mod.rs:40` `MAX_RECV_EMPTY_DATA_FRAMES`;
`streams/counts.rs:120-129`, documented as "over the lifetime of the
connection"; enforced in `streams.rs:651-663`).

When the cap is passed, h2 sends a library GOAWAY(ENHANCE_YOUR_CALM). Then:

- `classify`/`connection_loss` (`transport.rs:437-441`) maps the local GOAWAY to
  Unavailable;
- on an established read this is a reconnect after 500 ms, and data the server
  sent afterwards is lost;
- before the schema it is fatal.

The session reuses one connection across indefinite reconnects, so the count
accumulates across subscriptions.

| Witness | Go | R4 |
| --- | --- | --- |
| `empty-data-101`: schema, 101 empty DATA, 2-row batch, status 3; second subscription has a 1-row batch, status 3 | 333 B, 2 rows, 1 DoGet, 1 conn | 162 B: only the second subscription's row; 2 DoGets, 2 conns. **The first subscription's batch was silently lost.** |
| `empty-data-100-control` | 333 B, sha `6317abb0…` | identical |
| `empty-data-60x2-across-reconnect`: 60 empty frames, batch, OK; then 60 empty frames, 2-row batch, status 3 | 504 B, 3 rows in 2 batches, 2 DoGets, 1 conn | 162 B, 1 row, 3 DoGets, 2 conns. The terminal error also changed, to `fixture subscriptions exhausted`. |

**(b) Content-length enforcement.** In `content-length-mismatch`, the headers
carry `content-length: 1` and a normal body follows.

- Go renders the 1-row batch and then reports InvalidArgument.
- R4 resets the stream with RST(PROTOCOL_ERROR) and fails before any output:
  `rpc error: code = Internal desc = h2 protocol error: error reading a body from connection`.

**(c) Connection-specific response headers.** In `connection-specific-header`,
the headers carry `connection: keep-alive`.

- Go renders, then reports InvalidArgument.
- R4 fails with Internal `h2 protocol error: http2 error`, with empty stdout
  and RST(PROTOCOL_ERROR).

**(d) DATA framing-overhead budget (source only, not reproduced).**

- Rule: each frame below 256 bytes consumes budget, up to max(window/2, 25600),
  and consuming the data releases it (`counts.rs:120-147`,
  `connection.rs:99-110`).
- `tiny-frames-first-message` sends 208 one-byte DATA frames to a reader that
  consumes promptly. It matched Go, because frames were released between reads.
- The budget would trip only if many small frames were buffered and unconsumed,
  as under a slow sink. That is unmeasured: no pressure run was allowed.

**Contract impact:** stdout, stage, DoGet count and terminal error differ. In
(a) the outcome is silent loss plus a reconnect. These are not text-only
differences.

Triggers need peers or intermediaries that:

- send empty DATA frames over time;
- add `content-length` or hop-by-hop headers to an HTTP/2 gRPC response;
- or (inferred) send tiny frames to a slow sink.

**Minimal resolution.** The plan keeps the maintained stack, and IA-2 already
set the precedent against forking h2 or adding a second HPACK parser. Either:

1. add one explicit, enumerated capability limitation with its own ID. List
   (a)–(d) individually; this is not a broad waiver. Add the six witnesses
   above as process cases that are excluded from parity, with Go/R4 bytes
   recorded as IA-2 does in `harness/phase6_protocol_limits.py`; or
2. for (a) only, take an owner decision to drop unpadded, zero-length,
   non-END_STREAM DATA frames in `Observed::poll_read`. These frames carry no
   data and no flow-control weight; the frame scanner already finds their
   9-byte headers. Of the four rules, this is the only one with a bounded native
   workaround. (b) and (c) are decided while h2 decodes HPACK.

Until one of these is done, these are **unaccepted new mismatches**, not
declared limitations.

### F3. Low (measured): the client rebuilt for an in-stream transparent retry is thrown away, so the next reconnect dials another connection

**Source:**

- After the response headers, `FlightStream::poll_next` (`transport.rs:346-359`)
  retries on `retry.factory.build().0` when the bound wire is draining. It drops
  the new `Wires`.
- `FlightConnection.client` still holds the old, draining wire. So the next
  application `do_get` → `FlightClient::ready` (`transport.rs:209-215`) builds a
  third channel.
- The DoGet-stage retry (`transport.rs:182-191`) replaces `self.client`
  correctly.

**Witness:** `goaway-after-headers-then-reconnect`. The first attempt gets 200
headers, then GOAWAY(last below) and close. The retry ends with OK trailers.
After the reconnect, the stream ends with status 3.

| Client | Connections | (DoGet ordinal, stream id) |
| --- | --- | --- |
| Go | 2 | (0,1), (1,1), (2,3) |
| R4 | 3 | (0,1), (1,1), (2,1) |

stdout (334 bytes, sha `70227ef8…`) and stderr are identical.
`goaway-before-headers-then-reconnect` matches Go: 2 connections, stream ids
(1,1,3).

**Impact:**

- Wire and resource only: an extra TCP connection, CONNECT and full TLS
  handshake, and one more exposure to the 20 s connect deadline per event.
- Inferred: if that extra dial fails, Rust is fatal at the DoGet stage, where Go
  would keep using its live connection.
- No stdout or stderr contract difference was measured.

**Fix:** share the replaceable `(FlightServiceClient<GoChannel>, Wires)` slot
between `FlightClient` and `Retry`, for example behind
`Arc<std::sync::Mutex<…>>`, so that the stream's rebuild becomes the
connection's current client. Add a raw-frame unit test asserting two accepted
connections and stream id 3 on the following `do_get`.

### F4. Low (policy decision): the CONNECT request sends grpc-go's User-Agent

`GRPC_GO_USER_AGENT` (`transport.rs:48-50`) is written by `connect_request`
(`transport.rs:2597-2603`) as `User-Agent: grpc-go/1.65.0`. The HTTP/2
user-agent is native (`tonic/0.14.6`), and the ledger keeps it native only
because tonic cannot override it.

The lane brief asks for the native UA to be explicit and not spoofed, but
proxies see another client's identity. This is not a functional mismatch: the
CONNECT head bytes match Go by design. It needs an explicit recorded decision,
one of:

- keep it for proxy byte parity, with that rationale in the ledger;
- or send a native token and add a CONNECT-UA row to the UA ledger and the
  harness proxy comparison.

## Informational

- **I1 (measured, text-only, matches an existing ledger class).** In
  `close-after-headers` (200 headers then FIN), both clients fail with
  Unavailable and empty stdout. The text differs:
  - Go: `error reading from server: EOF`;
  - R4: `h2 protocol error: error reading a body from connection`.

  The ledger row (notes line 129) says "DoGet stage". Go's DoGet does not wait
  for headers, so the row should read "before the first message (Rust DoGet or
  reader initialization)", and cite this witness.
- **I2 (source, residual race, not a gate).** grpc-go retries transparently when
  stream creation fails on a transport that is closing
  (`NewStreamError{AllowTransparentRetry: true}`). With hyper, a request that
  races a peer's idle close surfaces as a closed or canceled error, which is
  fatal at DoGet. GOAWAY-driven drains are handled; only a bare FIN at the exact
  moment of reconnect is affected. I did not try to reproduce it.

## Checked; no new finding

| Area | Basis |
| --- | --- |
| Discovery before Flight; snapshot after any OAuth refresh; Go order (URL/scheme → client → auth mode); no gRPC refresh; the same metadata reused on every reconnect | `transport.rs:131-153`, `161-194`; `cmd/tail.go` `tail()`; source |
| Metadata for all four auth modes; raw cookie; printable-ASCII validation before dialing; Basic base64; HTML-escaped ticket | `transport.rs:504-513`, `623-661`; source. The 291-case owner evidence covers the bytes. |
| TLS: 1.2–1.3, raw-hostname SNI, h2 ALPN, Go-style roots loaded once, no resumption, ALPN gate, handshake bounded by the single 20 s deadline; plaintext `:authority host:port`; TLS origin is the bare host | `transport.rs:701-762`, `2015-2074`; plaintext authority measured in the probe request metadata |
| Dialing: Happy Eyeballs (300 ms delay, primary error reported), `partialDeadline` with the 2 s floor, expired context reports its address, keepalive/nodelay, detached resolver; proxy selection, mapped-loopback bypass, CONNECT outside the deadline (as in Go) | `transport.rs:1787-1916`, `2362-2493`, `2514-2746`; source against grpc-go/net |
| Limits: 4 MiB receive cap (exact tonic-local rewrite); 16 MiB header list; adaptive window capped at 16 MiB | `transport.rs:45-58`, `482-501`, `726-730`; source |
| Status/trailer rules: content-type and HTTP status tables, `grpc-status` parsing, `-bin` base64 indices, details code/message selection and mismatch, raw percent-decoded message, trailers authoritative and sanitized for tonic | `transport.rs:1066-1412`; source against `operateHeaders` 1.65 and `istatus.NewWithProto` |
| Terminal mapping: frame observations used only on a clean end or a remote h2 error; local/library errors go to `classify` (r3 B1 holds); partial message → Internal `unexpected EOF`; GOAWAY → `the connection is draining` | `transport.rs:925-1047`; h2 `connection.rs:472-558`, `streams.rs:782-831`, `state.rs:299-323`. Included streams receive BrokenPipe, not a remote GoAway, so they are never marked unprocessed (source). |
| Exactly one transparent retry; a second refusal surfaces; no retry after a delivered message; refusal before headers on a new connection | `transport.rs:161-194`, `315-375`; measured (dogets and stream ids match Go before headers) |
| Fused stream after an error; `Exchange` travels in in-process extensions and cannot be spoofed | `transport.rs:292-375`, `264-267` |
| Preface writes absorbed without a CPU spin: absorbing returns `Ok`, later writes skip the socket, reads are bounded by the deadline, the watch is dropped at the first server frame | `transport.rs:2220-2358`; source |
| Bounded state per reconnect: `Wire` is O(1) (opened, draining, one terminal); scanners and `Framing` are fixed-size; `PrefaceWatch` is dropped after the preface; old channels and connections are freed with their last `GoBody`; no per-frame history | `transport.rs:1483-1725`; source. h2's per-connection lifetime counters persist across subscriptions, which is the cause of F2(a). |

The prior major fixes are still present in this source: M1 partial-prefix /
body Internal unexpected EOF, M2 details code/message/mismatch, M3 transparent
refusal, terminal binding, the bad-HPACK test-claim correction, and the preface
reset diagnostic. I did not rerun the owner's 56 + 16 process probes. The frozen
R4 binary carries this exact transport source, and my refusal probes before and
after headers agree with Go on output and DoGet counts.

## Disposition

The transport lane is **not accepted** in this snapshot. Remaining work:

- F1: fix, or record an explicit decision with a process case.
- F2: an explicit, enumerated capability limitation with its witnesses, or the
  optional (a) filter plus declarations for (b)–(d). Until then these are
  unaccepted new mismatches.
- F3: fix, or explicitly accept it as a wire/resource-only difference.
- F4: a recorded decision.
- I1: correct the ledger wording.

Declared limitations not reopened: IA-2 (initial informational HTTP/2
responses), the non-native-endian decoder, the native HTTP/2 UA, eager vs lazy
dial, and the CONNECT and URL residues. No performance, production, live or
macOS claim is made. A fresh final whole-phase Opus review is still required
after settlement.
