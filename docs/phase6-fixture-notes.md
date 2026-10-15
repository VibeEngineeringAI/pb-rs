# Phase 6 fixture notes (`pb-fixture` live-tail extensions)

`harness/fixture/phase6.go` extends the loopback HTTP/Arrow Flight fixture for the
native `pb tail` port. It is a disposable test server, not a Parseable
implementation, and it says nothing about deployed server delivery, authorization
or loss behavior.

Scenarios without a `phase6` key do not touch any of this code. Their HTTP
routes, Flight plan, banner and trace are unchanged. The only addition visible to
legacy callers is a `"phase6"` entry in `--capabilities`.

## Building and running

```sh
GOTOOLCHAIN=local GOPROXY=off <go1.25.4>/bin/go build -trimpath -buildvcs=false \
  -o /home/ajs/.cache/pb-phase6/tmp/fx/pb-fixture-r5 ./harness/fixture/
/home/ajs/.cache/pb-phase6/tmp/fx/pb-fixture-r5 --scenario scenario.json
```

The command line is still `--scenario FILE` only. Once ready, the fixture prints
one JSON line to stdout:

```json
{"url":"http://127.0.0.1:P","grpc_port":G}
```

When `phase6` is present, the banner also carries:

- `control_url`: always plaintext `http://127.0.0.1:C`. It serves only
  `/__harness/*`, including when the main URL uses HTTPS.
- `grpc_tls` and `http_tls`: booleans.
- `ca_path`: only when TLS is enabled.

With `http_tls`, `url` is `https://…`. With `listen: "::1"`, both listeners bind to
IPv6 loopback and `url` is `http://[::1]:P`.

The harness stops the fixture with SIGKILL, so the fixture writes everything it
needs to disk as it goes:

- The events file is flushed after every non-batch event and every 100 ms.
- The summary file is rewritten atomically (temp file plus rename):
  - at startup;
  - every second;
  - immediately before each subscription's terminal status is returned to the client.

Use a disk-backed directory for `events_path`, `summary_path` and `ca_path`, never
`/tmp`.

## Scenario schema

The legacy keys `routes`, `headers` and `flight` keep their meaning. The new
optional key is:

```jsonc
"phase6": {
  "events_path": "/abs/events.jsonl",   // optional bounded JSONL event log
  "summary_path": "/abs/summary.json",  // optional atomic summary
  "event_limit": 20000,                 // every event is written until this many lines…
  "event_hard_limit": 200000,           // …then a per-kind sample until this many, then none
  "batch_event_sample": 100,            // sample rate for batch_* kinds after event_limit
  "event_sample": 10,                   // sample rate for other kinds after event_limit
  "trace_limit": 4096,                  // cap on legacy /__harness/trace entries (overflow counted)
  "expected_output": false,             // Go-expected stdout hashing (below)
  "listen": "127.0.0.1",                // or "::1"
  "tls": {"grpc": true, "http": false, "ca_path": "/abs/ca.pem",
          "names": ["localhost", "127.0.0.1", "::1"],   // leaf SANs (default shown)
          "min_version": "1.0", "max_version": "1.3",   // "1.0" … "1.3"
          "no_alpn": false},
  "keepalive": {"time_ms": 1000, "timeout_ms": 300, "min_client_ping_ms": 1000},
  "flight_auth": {"x-api-key": "k", "x-p-tenant": "t"},
  "http": [{"method": "GET", "path": "/api/v1/about", "responses": [
      {"require": {"cookie": "session=new"}, "status": 200, "body": "{\"grpcPort\":{{grpc_port}}}",
       "headers": {}, "times": 0, "delay_ms": 0, "gate": ""},
      {"status": 401, "body": "expired"}]}],
  "streams": {"logs": {
      "schema": "basic", "compression": "", "payload_bytes": 0, "seed": 1,
      "dictionary": {"cardinality": 16, "mode": "stable", "value_bytes": 0, "max_entries": 4096},
      "subscriptions": [{"repeat": 1, "ops": [{"op": "batch", "rows": 3}]}]}}
}
```

The fixture validates the scenario at startup. An unknown schema kind, op, corrupt
kind, compression or dictionary mode, a status code outside 0–16, a `live` op with
no bound, or TLS without `ca_path` makes it exit 1 with the reason on stderr
before printing the banner.

### TLS

The fixture generates a fresh ECDSA CA and leaf at startup. It writes the CA PEM to
`ca_path`, and clients trust it through `SSL_CERT_FILE=ca_path`.

Both Go's system roots and rustls-native-certs honor that variable; confirm it
per build. Point the profile URL at a name in `names` (`localhost` by default) to
exercise SNI.

- An IP-literal URL sends no SNI, so `server_name` is empty in the ledger. That is
  standard TLS behavior, not a fixture choice.
- A cloud profile with an `http://` URL uses TLS for Flight only. To model it, set
  `tls.grpc: true` and `tls.http: false`.
- gRPC TLS uses a server-only credential:
  - It advertises ALPN `h2`, or nothing when `no_alpn` is set.
  - It records a `tls_handshake` event (SNI, ALPN, version, error) for every attempt, including failed ones such as a verification-name mismatch or a version below the client minimum.
- `reset_invalid_handshake` (default false) controls how a failed gRPC TLS
  handshake ends, for example when a plaintext client reaches the TLS port.
  - By default the fixture closes the connection. crypto/tls sends no alert for a
    non-TLS first record. Whether the client then sees a FIN (`error reading server
    preface: EOF`) or an RST (`connection reset by peer`) depends on whether its
    bytes were still unread.
  - With the option set, the fixture sets `SO_LINGER=0` before closing, so the client
    always gets an RST. The `tls_handshake` event records `reset` (true or false)
    for every failed gRPC handshake.
  - The option does not affect HTTPS or the endpoints.
- HTTPS serves HTTP/1.1 (ALPN `http/1.1`).
- Characterized result: a grpc-go 1.65 client completes a Flight call against `no_alpn` without errors.

### Auth

- If `flight_auth` is set, DoGet needs exactly one metadata value equal to each listed
  value. Otherwise it falls back to the legacy `headers` check, which compares the
  contract headers joined with commas.
- A request that fails auth gets Unauthenticated before any message is sent.
  - That is fatal in the Go client.
  - It is the place to test that no HTTP refresh follows a Flight Unauthenticated.
- Routes listed in `phase6.http` skip the legacy `headers` check. Instead, each
  response decides with `require`, which compares lowercase header names exactly
  against `http.Header.Get`.
- Requests that match none of the `phase6.http` routes still go through the legacy route table and its auth.

To model the OAuth refresh:

1. Set the profile's `orchestrator_url` to the fixture URL.
2. Make the about route answer 200 only with `require: {cookie: "session=NEW"}`, and 401 otherwise.
3. Add `POST /api/v1/cli/oauth/token` requiring `authorization: Bearer <build token>` and answering `{"access_token","refresh_token"}`. Use `times: 1` to detect a second refresh.
4. Set `flight_auth: {"cookie": "session=NEW", "x-p-tenant": …}` to show that Flight uses the snapshot taken after discovery.

### HTTP responses

For each request, the fixture picks the first response whose `require` headers
all match and whose `times` budget remains (`times: 0` means unlimited).

- If nothing matches, it answers 500 `fixture: no matching phase6 response`.
- `gate` holds the response until the gate is released. Use it to send a signal during discovery.
- `delay_ms` sleeps before sending the headers.
- `{{grpc_port}}` in the body is replaced with the gRPC port. To test edge cases, write a literal body instead (port missing, zero, a string, …).
- Milestones are recorded as for legacy routes.

### Streams and subscriptions

The ticket is parsed as `{"stream": X}`, and its bytes are recorded verbatim.

- If X has no entry under `streams`, the fixture returns NotFound `fixture: unknown stream` and the request is flagged `unexpected`.
- The exception: when a legacy `flight` plan is also configured, unknown streams use the legacy generator.

Each stream counts its DoGet calls from 0. Call N runs the Nth subscription after
`repeat` expansion. Once the list is exhausted, the call gets InvalidArgument
`fixture subscriptions exhausted` before any message. That is fatal in Go, so
every retry script is finite.

Each subscription gets a fresh IPC writer, schema state and dictionaries. The row
`sequence` and the batch ordinal are global to the stream and continue across
subscriptions.

### Ops

Ops run in order. If they run out without a terminal op, the terminal is
InvalidArgument `fixture stream complete`, the same description as the legacy
finite stream.

| op | fields | effect |
| --- | --- | --- |
| `schema` | `schema?` | Sends an explicit schema-only message. Without `schema` it uses the stream's kind and the writer won't send it again. A different kind (or a repeat) after batches is a mid-stream schema change. |
| `batch` | `rows`, `count` (1), `payload_bytes`, `target_message_bytes` | Sends `count` record batches, sending the schema first if not yet sent. `rows: 0` is an empty batch. `target_message_bytes` pads the record batch's FlightData `app_metadata` so its protobuf size is exactly that value. For `wide`, it also sizes the payload. |
| `live` | `rows_per_batch`, `batches`, `interval_ms`, `duration_ms` | Paced batches on an absolute schedule, stopping at `batches` or `duration_ms`, whichever comes first. One of them is required. Events carry `lateness_ns`. |
| `sleep` | `ms` | Idle. Client cancellation ends the run. |
| `gate` | `name` | Blocks until released (a counting semaphore). Emits `gate_wait` and `gate_pass`. |
| `hold` | — | Blocks until the client cancels. |
| `headers` | — | `SendHeader` with empty metadata, so a following status arrives after the response headers rather than as a trailers-only reply. |
| `status` | `code` 0–16, `message` | Terminal. `code: 0` returns cleanly, which the client sees as EOF/OK. |
| `eof` | — | Terminal clean return. |
| `metadata_only` | `app_metadata`, `count` | FlightData carrying only `app_metadata`. |
| `raw` | `header_b64`, `body_b64`, `app_metadata_b64` | Arbitrary FlightData. |
| `corrupt` | `kind`, `rows`, `schema` | See below. |
| `status` extras | `message_bytes`, `trailer_pad_bytes` | Pad `grpc-message` with ASCII `x` up to `message_bytes` bytes, and/or add an `x-fixture-pad` trailer of `trailer_pad_bytes`. Emits `trailer_sized`. |
| `conn_close` | `settle_ms` (50) | Terminal. After the settle delay, closes the stream's TCP socket (FIN), with no GOAWAY and no TLS close_notify. |
| `conn_reset` | `settle_ms` (50) | Terminal. Sets `SO_LINGER=0` and closes, sending an RST. |
| `goaway` | `last_stream_id` (0), `error_code` (0 = NO_ERROR), `debug`, `close_after_ms` (0), `settle_ms` (50) | Terminal. Writes a raw HTTP/2 GOAWAY frame on the stream's connection, above TLS when TLS is on, then closes the socket after `close_after_ms`. `last_stream_id` 0 excludes our stream. 2147483647 with `close_after_ms` gives a per-connection graceful drain. |

Where a terminal goes in the script decides its stage:

- **DoGet / first message:** first op, optionally after `headers`.
- **After the schema:** following `schema`.
- **After batches:** following batches.

#### How the connection-loss ops work

- **Connection registry:**
  - The gRPC listener is wrapped, so each accepted connection is registered under its remote address. That address is the DoGet `peer`.
  - Under TLS, the registry entry is replaced with the decrypted connection, so frames are written above TLS. Resets still use the TCP socket.
- **Frame safety:**
  - Writes on a registered connection are serialized, so a script's GOAWAY never interleaves with a gRPC write.
  - The settle delay lets grpc's writer flush and stop on a frame boundary first. `settle_ms: 0` skips that safety.
- **Reconnect:**
  - Only the one connection is broken; the listener keeps accepting.
  - A reconnecting client therefore opens a new connection, issues a new DoGet and gets the next subscription. End scripts with a fatal subscription.
- **Terminal outcome:** the server-side context ends, so the subscription's terminal is recorded as `Canceled` with reason `conn_close`, `conn_reset` or `goaway`.
- **Timing:** the terminal event can be stamped after the client has already started its reconnect timer. Assert gaps from the client-side DoGet times, not from the fixture terminal event.
- **Scope:** this is not a whole-server graceful stop.
- **`settle_ms: 0` is racy.** The GOAWAY can reach the client before the stream's
  data. grpc-go then treats the stream as unprocessed (id > last_stream_id, nothing
  received) and *transparently retries* it on a new connection. The fixture sees an
  extra DoGet that consumes the next subscription without any tail-level reconnect.
  Use it only to study that race, never in a deterministic case.

Corrupt kinds:

- `garbage_header`: a 12-byte non-flatbuffer header.
- `empty_message`: a FlightData with every field empty.
- `truncated_body`: the next batch with its record body cut in half. The schema is sent if needed. The sequence does not advance.
- `foreign_batch`: a record batch encoded for another kind (`schema`, default `dup`), with no schema message.
- `dictionary_before_schema` and `dictionary_only`: a lone dictionary-kind DictionaryBatch message.
- `missing_dictionary`: the dictionary-kind schema (unless already sent) plus a record batch whose dictionary was never sent.

### Schema kinds and deterministic data

The row index `i` used below is the stream-global `sequence`.

| kind | columns |
| --- | --- |
| `basic` | Legacy six columns (`sequence`, `large_integer`=9007199254740993+i, `value`=i/4, `message`, null `optional`, µs `p_timestamp`). |
| `wide` | `sequence` plus `payload`: a utf8 string of `payload_bytes`, a deterministic slice of a pattern seeded by `seed`. |
| `values` | `sequence` plus about 50 conversion-family columns: signed/unsigned extremes, float16/32/64 including −0, exponent thresholds, max and min-subnormal, string escaping (HTML, U+2028/9, controls, emoji), large utf8, binary, large and fixed binary, date32/64, time32/64, timestamps in each unit (with and without UTC, an IANA zone or an offset), durations, decimal128/256, all three interval types, null type, list, large list, fixed-size list, struct (keys out of order), map, and dictionary<int32,utf8>. Values cycle per column by `i`; nulls are included. |
| `dup` | Duplicate `zeta` (int64, then utf8), reordered fields. Go keeps the last column. |
| `nan` | `value` is NaN when i%4==1 and +Inf when i%4==3. Go's RecordToJSON stops the batch at the first such row. |
| `dictionary` | `sequence` plus `level` dictionary<int32,utf8> (null when i%7==6). `dictionary.mode` selects the behavior: `stable` reuses one dictionary per subscription, `replace` sends a new `g<batch>-level-N` dictionary each batch, and `delta` grows the dictionary by `cardinality` each batch up to `max_entries` and sends IPC deltas. `value_bytes` pads values with `a`, so compression produces a large decoded expansion from a small wire size. |
| `empty` | No columns; each row renders as `{}`. |
| `union` | Dense union<int32, utf8>. Probes types Go v13 may handle differently from arrow-rs. |
| `ree` | Run-end-encoded<int32, utf8>. Same purpose as `union`. |
| `invalid_utf8` | utf8 column containing bytes 0xff 0xfe. Go replaces them when encoding; arrow-rs validation may reject them. |

`compression: "lz4" | "zstd"` applies Arrow IPC body compression.

### Extra endpoints (protocol mismatches)

```jsonc
"endpoints": [
  {"name": "bh", "kind": "blackhole", "tls": false, "hold_ms": 60000},
  {"name": "html", "kind": "http_status", "tls": false, "status": 502,
   "content_type": "text/html", "body": "<html>bad gateway</html>"}]
```

Each endpoint gets its own listener. The banner reports the ports as
`"endpoints": {"bh": PORT, …}`, and phase6 route bodies may use `{{port:NAME}}`,
for example `{"grpcPort":{{port:bh}}}`. `tls: true` requires `phase6.tls.ca_path`
and uses the same CA. Generating the CA needs only `ca_path`; `grpc` and `http`
can stay false.

- **`blackhole`:**
  - Accepts TCP, and when `tls` is set completes the TLS handshake (ALPN h2) and records `tls_handshake` with protocol `blackhole`.
  - Then reads and discards everything and never writes, until `hold_ms` (default 60 s) or until the client closes.
  - Events: `endpoint_accept` and `endpoint_close` `{name, bytes_read, duration_ns, error}`.
  - This models a peer that never speaks HTTP/2 (T14).
- **`http_status`:**
  - Serves h2c, or h2 over TLS.
  - Answers every request with `status` (default 502), `content_type` and `body`.
  - Emits `endpoint_request` `{name, method, path, proto, content_type, authority}`.
  - This models a proxy or ingress that answers DoGet with an HTML error (T6).

- **`raw_h2`** is a scripted HTTP/2 gRPC responder, for status and header edge cases
  that grpc-go's server would sanitize (review r2 B1, M1, M2, M3).

  ```jsonc
  {"name": "raw", "kind": "raw_h2", "tls": false, "schema": "basic",
   "subscriptions": [{"repeat": 1, "acts": [
     {"act": "headers", "fields": [[":status", "200"], ["content-type", "application/grpc"]]},
     {"act": "batch", "rows": 1},
     {"act": "trailers", "fields": [["grpc-status", "14"], ["grpc-message", "%FF"]]}]}]}
  ```

  Connections:
  - The server reads the client preface and sends empty SETTINGS. It acks SETTINGS and PING.
  - It collects each request's HEADERS and DATA until END_STREAM, then runs the next subscription.
  - The subscription ordinal counts streams across all of the endpoint's connections, after `repeat` expansion.
  - Past the last entry it sends Trailers-Only `grpc-status: 3`, `fixture subscriptions exhausted`.

  Acts:

  | act | fields | effect |
  | --- | --- | --- |
  | `headers` | `fields`, `end_stream` | HEADERS (plus CONTINUATION if needed). Fields are `[[name, value], …]`, hpack-encoded verbatim and in order, with no validation. |
  | `schema` | — | A schema-only message, gRPC length-prefixed in DATA. |
  | `batch` | `rows` | A record batch. The row `sequence` continues across the endpoint's subscriptions. Counted in `expected_*`. |
  | `data` | `b64`, `end_stream` | Raw DATA bytes, split into frames of 16 KiB or less. |
  | `trailers` | `fields` | HEADERS with END_STREAM. |
  | `end_stream` | — | An empty DATA frame with END_STREAM and no trailers. |
  | `rst` | `code` | RST_STREAM. |
  | `goaway` | `last` (`"below"` = our stream id − 2, floored at 0; `"ours"`; or a number), `code`, `debug` | GOAWAY. The connection stays open unless `close` follows. |
  | `close` | — | Closes the TCP socket (FIN), with no TLS close_notify. |
  | `sleep` | `ms` | Waits. |

  Every script must end with a terminal act: `trailers`, `end_stream`, `rst`, `close`, or `headers`/`data` with `end_stream`.

  Limits and accounting:
  - Flow control is not modeled, so keep each stream's DATA under the client's initial 64 KiB window.
  - Each DoGet produces a legacy `/__harness/trace` entry and a `raw_h2_stream` event. The event carries `name`, `ordinal`/`sub`, `doget`, `stream_id`, `ticket`, `ticket_b64`, `ticket_matches`, all request header values as lists (including `:authority`, `:path` and `user-agent`), `authority`, `authorized` and `peer`. Its `t_ns` is the CLOCK_MONOTONIC receive time of the request headers.
  - Other events: `raw_h2_conn`, `raw_h2_conn_end`, `raw_h2_act`, `raw_h2_client_rst` and `terminal`.
  - Stats appear under `streams["endpoint:NAME"]` with the same fields as streams, including `request_validation`, `expected_stdout_*` and `connections`.
  - `last_terminal.code` is the gRPC code name for a numeric `grpc-status` that was sent. Otherwise it is `raw:<act>`, `raw:grpc-status=<value>` or `raw:<act>-without-grpc-status`.

The other mismatches already exist:

- A plaintext client against the TLS gRPC port: `tls.grpc: true` with an `http://` non-cloud profile.
- A TLS client against the plaintext port: a cloud profile with `tls.grpc: false`.

## Events (JSONL)

Every event has `seq`, `t_ns`, `kind` and, inside a subscription, `stream`, `sub`
and `doget`. `t_ns` is `CLOCK_MONOTONIC` in nanoseconds, the clock used by pb-meter
and the fixture milestones.

| kind | extra fields |
| --- | --- |
| `http` | `method`, `path`, `headers` (contract headers), `request` (milestone index) |
| `http_response` | `method`, `path`, `status`, `matched` (phase6 routes only) |
| `doget` | `ticket` (exact string), `ticket_b64`, `metadata` `{key: [values]}` (includes `:authority`, `user-agent`, `content-type`, `te`), `authorized`, `unexpected`, `tls` `{server_name, alpn, version}` or null, `peer`, `authority` (the HTTP/2 `:authority`; also counted per stream in the summary's `authorities`, at most 16 distinct values plus `<other>`) |
| `conn_action` | `action` (`conn_close`, `conn_reset`, `goaway`, `close_after_goaway`), `remote`, and for GOAWAY `last_stream_id`, `error_code`, `write_error` |
| `trailer_sized` | `grpc_message_bytes`, `trailer_pad_bytes` |
| `endpoint_accept` / `endpoint_close` / `endpoint_request` | see Extra endpoints |
| `schema_sent` | `schema`, `bytes`, `explicit`, `error` |
| `batch_emit_start` / `batch_emit_done` | `batch` (stream-global ordinal), `sub_batch`, `rows`, `seq_start`, `messages`, `dictionary_messages`, `encoded_bytes` (sum of FlightData protobuf sizes), `max_message_bytes`, `decoded_bytes` (Arrow buffer bytes, including referenced dictionaries), `lateness_ns` (live only); `_done` adds `send_ns` |
| `batch_emit_failed` | `batch`, `error` |
| `message_sent` | `op`, `bytes`, `error` (metadata/raw/corrupt messages) |
| `headers_sent`, `sleep`, `hold`, `gate_wait`, `gate_pass`, `gate_release` | `name`, `ms`, `n` as applicable |
| `terminal` | `code` (gRPC code name; `OK` for a clean return; `Canceled` for client cancellation), `message`, `reason` (`status`, `eof`, `complete`, `exhausted`, `unauthenticated`, `unknown_stream`, `client_cancel`, `send_error`, `fixture_error`) |
| `conn_begin` / `conn_end` | `remote` (gRPC transport lifetime, including keepalive closure) |
| `tls_handshake` | `protocol` (`grpc`/`http`), `remote`, `server_name`, `alpn`, `version`, `error` |
| `fixture_panic` | `panic`: a fixture bug. The RPC returns Internal `fixture panic: …`. Treat any of these as an invalid sample. |

`batch_emit_start` is stamped immediately before the first `Send` of the batch's
messages. Building, encoding and expected-output hashing all happen before it.

`batch_emit_done` is stamped after the last `Send` returns. A returned `Send` means
grpc-go accepted the message, which is not proof the client received or rendered
it. A slow sink shows up as a growing `send_ns` once HTTP/2 flow-control windows
fill.

### What stays bounded

- The disk log is bounded by `event_limit`/`event_hard_limit`. `events_dropped` counts what was left out.
- Per-kind counts, wait counters and summary counters are exact.
- In memory, the fixture keeps the last 1024 events and the most recent event per wait key.
- Legacy `/__harness/trace` entries are capped at `trace_limit` in phase6 mode, with `trace_dropped` counting the overflow.

Nothing is kept per batch, so soak runs do not grow the fixture.

## Summary and control API

`GET control_url/__harness/phase6/summary`, and the `summary_path` file, return:

```jsonc
{"streams": {"logs": {
   "dogets", "subscriptions", "batches" /* includes zero-row */, "zero_row_batches", "rows",
   "messages", "encoded_bytes", "decoded_bytes", "grpc_wire_bytes" /* grpc OutPayload wire length, counted at terminal */,
   "max_message_bytes", "next_sequence",
   "expected_stdout_sha256", "expected_stdout_bytes", "expected_rows", "expected_rendered_rows", "expected_output_ns",
   "terminals": {"InvalidArgument": 1}, "last_terminal": {"code", "message", "reason", "sub", "t_ns"}}},
 "dogets", "events_total", "events_written", "events_dropped", "event_kinds", "trace_dropped",
 "gates": {"g": {"waiting", "released", "tokens"}}, "fixture_cpu_user_ns", "fixture_cpu_system_ns", "t_ns"}
```

### Exact admission counters

Use these counters, not the retained events, when judging a long run. All of
them are updated under the fixture lock before the matching event is emitted, so
event sampling never changes them. They contain no credential values.

**Root:**
- `http_requests`: every non-control HTTP request. `/__harness/*` is never counted.
- `about_requests`: requests whose path is `/api/v1/about`.
- `first_about_ns` and `last_about_ns`: when the first and last of those requests were received.
- `about_completed_ns`: when the first `/api/v1/about` handler that answered 2xx returned, meaning headers and the full body had been written. This is the discovery milestone.

**Per stream:**
- `first_doget_ns`, `last_doget_ns`: when DoGet requests were received.
- `first_nonempty_batch_emit_ns`: the `batch_emit_start` time of the first batch with at least one row.
- `reconnect_gap_ns` `{count, min, max, sum}`: the server-side gap from the previous terminal to the next DoGet. It is approximate. Use client-side times for the 500 ms assertion.
- `request_validation`:
  - `auth_checks` and `ticket_checks` each equal `dogets` by construction.
  - `invalid_auth`.
  - `ticket_mismatches`: the ticket bytes differ from `expected_ticket`. When a stream doesn't set `expected_ticket`, the expected value is Go `json.Marshal({"stream": NAME})`, which escapes `<`, `>` and `&` as HTML.
  - `unexpected_requests`: an unknown stream.
  - `doget_before_about`: a DoGet received before `about_completed_ns` was set.

**Bounds:**
- DoGets naming unknown streams are aggregated under the single key `<unknown>`, which bounds the `streams` map.
- `authorities` is capped at 16 distinct values, plus `<other>`.

**Subscription ordinal:** it advances only on accepted DoGets, meaning an authorized request for a known stream. A rejected attempt does not consume a scripted subscription, and its `doget.sub` is the ordinal that would have been used. `doget` events also carry `ticket_matches`.

### Expected output

With `expected_output: true`, the fixture renders every batch whose `Send` returned
exactly as Go `pb tail` does: Arrow Go v13 `array.RecordToJSON` with its error
ignored, then `fmt.Println`. The resulting bytes feed the SHA-256 and byte count.
This includes the NaN partial-batch case and the blank line printed per batch.

- `expected_rows` counts the rows that were sent.
- `expected_rendered_rows` counts the JSON lines actually rendered.
- Without `expected_output`, the four `expected_stdout_*`/`expected_rendered_rows` fields are null.
- Hashing costs fixture CPU, reported in `expected_output_ns` and in the fixture CPU fields. Freeze the expectations in a separate run, or annotate the overhead in timing runs.
- The hash assumes one subscription at a time per stream, which matches the tail client.

Other control endpoints:

- `GET /__harness/phase6/events?since=SEQ`: the in-memory ring after `since`, as a JSON list.
- `GET /__harness/phase6/wait?kind=K[&stream=S][&name=N][&count=C]&timeout_ms=T`:
  - Returns 200 `{count, event}` once the exact counter for that key reaches C (default 1).
  - Returns 408 if the timeout passes first.
  - Uses exact counters, not the ring.
- `POST /__harness/phase6/release?gate=NAME[&n=1]`: adds tokens to the gate. A release sent before the wait is remembered.
- `/__harness/trace` and `/__harness/milestones` work as before.

## Keepalive

With `keepalive`, the gRPC server sends HTTP/2 PINGs after `time_ms` without
activity and closes the transport if no ack arrives within `timeout_ms`. grpc-go
clamps the time to at least 1 s. The closure appears as `conn_end`, plus a
`terminal` with `client_cancel` for the stream that was active.

The enforcement policy permits client pings every `min_client_ping_ms` (default
1 s), including without active streams.

`TestPhase6ServerKeepaliveRequiresClientPingProgress` uses a raw HTTP/2 client
against the real server:

- A client that acknowledges PINGs keeps an idle gated stream across at least two keepalive PINGs.
- A client that ignores them is disconnected after roughly time plus timeout.

This is the fixture-side signal for checking that a client runtime stays
responsive during a slow sink. Whether the Rust or Go `pb` clients keep acknowledging
PINGs during a blocked stdout is the job of process-harness cases. The unit test
does not show it.

## Go v13 reader outcomes observed in the unit tests

These come from the Go Arrow v13 Flight reader, called the way `cmd/tail.go` calls it, inside `go test`. They are not runs of the pb process. Process-level output and exit status come from the harness corpus.

- **Before any schema:**
  - A clean return (`eof`) fails inside `flight.NewRecordReader` with `EOF`. That is a fatal initialization error, not a reconnect.
  - A status before the first message, including one sent after `headers`, is likewise fatal at initialization.
- **Metadata and corrupt messages:**

  | Input | Go v13 reader result |
  | --- | --- |
  | Metadata-only message first | Reader construction fails with `index out of range [3] with length 0`, from inside Arrow. |
  | Metadata-only message after a batch | The read fails the same way. |
  | Empty message | The read fails the same way. |
  | `garbage_header` | Initialization fails with a slice-bounds panic recovered by Arrow. |
  | `truncated_body` | `arrow/ipc: unknown error while reading: EOF` |
  | `foreign_batch` | A slice-bounds error. |
  | Schema message after batches | `invalid message type (got=Schema, want=RecordBatch)` |
  | DictionaryBatch before the schema | `invalid message type (got=DictionaryBatch, want=Schema)` at initialization. |
  | `missing_dictionary` | `no dictionary type found with id: 1` |
  | An extra DictionaryBatch after the schema (`dictionary_only`) | Accepted. |

  None of these Arrow errors are gRPC Canceled/Unavailable/OK, so in tail they are all fatal.
- **Message size:** a FlightData of exactly 4194304 protobuf bytes is accepted. One byte more fails the read with ResourceExhausted (the grpc-go client's default receive limit), after the schema.
- **Value kinds:** every schema kind, including `union`, `ree` and `invalid_utf8`, decodes in Go v13 and renders the same bytes the fixture hashes. `nan` renders `{"sequence":0,"value":0}` and then the batch blank line, so the NaN row and the rest of the batch disappear.

### Transport-review (r1) outcomes from a grpc-go 1.65 client

These come from the Go Flight client inside `go test` and were not run with pb. Each scenario was a batch followed by the op, so the stream was already established.

| Server action | Go error | Go tail outcome |
| --- | --- | --- |
| FIN | `Unavailable: error reading from server: EOF` | Restart (`isStreamEnd`) |
| RST | `Unavailable: error reading from server: … connection reset by peer` | Restart |
| GOAWAY that excludes the stream | `Unavailable: the connection is draining` | Restart |
| Graceful GOAWAY, then close | `Unavailable: closing transport due to: … EOF, received prior goaway: code: NO_ERROR` | Restart |

In every case, the next DoGet arrived on a new connection, received the next
subscription and continued the sequence.

Other results:

- **Trailer size:**
  - A 20 KiB `grpc-message` arrives intact at DoGet stage, and so does a 64 KiB `grpc-message` plus an 8 KiB pad trailer after a batch. Go's client header-list limit is 16 MiB.
  - A Go client that advertises a 16 KiB header list instead gets `Internal: stream terminated by RST_STREAM with error code: INTERNAL_ERROR`, because the grpc-go server honors the client's limit.
  - The fixture cannot model a server that ignores the client's limit without a custom HTTP/2 server. That case is not covered.
- **`:authority`:**
  - Plaintext: `127.0.0.1:PORT`.
  - TLS with credential `ServerName: localhost`: `localhost`, with no port. This is review item T2.
- **Protocol mismatches:**
  - `blackhole`, plaintext or TLS: DoGet fails only at the caller's deadline (DeadlineExceeded at 1 s in the test).
  - `http_status` 502 `text/html`: `Unavailable: unexpected HTTP status code received from server: 502 (Bad Gateway); transport: received unexpected content-type "text/html"`. At DoGet or reader setup this is fatal in tail.

### Review r2 raw HTTP/2 outcomes from a grpc-go 1.65 client

These are results from `TestPhase6RawH2GoOracle`, inside `go test`. They are not runs of the pb binary.

| case | Go result |
| --- | --- |
| Trailers-Only `grpc-status 0` + `grpc-status-details-bin: !!` | init: Internal `transport: malformed grpc-status-details-bin: illegal base64 data at input byte 0` |
| Same in trailers after a batch | 1 batch, then the same Internal |
| `grpc-status 0` + `grpc-message %FF` after a batch | 1 batch, then EOF (OK; tail would reconnect) |
| `14` + `%FF` / `1` + `%FF` | Unavailable / Canceled with message byte `\xff` (code preserved; tail would reconnect) |
| `3` + `a%E9b` | InvalidArgument `a\xe9b` (fatal) |
| `grpc-status 2` + tonic's exact missing-trailer text | Unknown with that text (fatal; negative control for text-based mapping) |
| DATA END_STREAM, no trailers | Internal `server closed the stream without sending trailers` |
| Trailers without `grpc-status` | Unknown with an empty message |
| RST_STREAM(NO_ERROR), before the schema or after a batch | Internal `stream terminated by RST_STREAM with error code: NO_ERROR` |
| GOAWAY `below`, then close | Unavailable `the connection is draining`. The next DoGet runs the next subscription on a new connection, with the sequence continuing. |
| GOAWAY `ours`, then another batch and trailers `3` | Both batches, then InvalidArgument (the stream is unaffected) |
| `200` + `text/html` + HTML body | init: Unknown `transport: received unexpected content-type "text/html"` |
| `404` + `application/grpc` | init: Unimplemented `unexpected HTTP status code received from server: 404 (Not Found)` |
| `503`, no content-type, empty body | init: Unavailable `unexpected HTTP status code received from server: 503 (Service Unavailable); malformed header: missing HTTP content-type` |
| `599` + `text/plain` | init: Unknown `unexpected HTTP status code received from server: 599 (); transport: received unexpected content-type "text/plain"` |

Go's error strings keep raw bytes such as `\xff`. JSON-encoded copies of them,
for example in harness reports, show U+FFFD instead.

## Verification status

- `go test ./harness/fixture/` (go1.25.4) passes: all legacy tests, plus phase6 tests covering:
  - finite batches and the summary, and the stage matrix with exhaustion;
  - every schema kind round-tripping through the Go reader with matching expected output;
  - the three dictionary modes with compression expansion;
  - the 4 MiB boundary;
  - the Flight auth and metadata ledger;
  - gRPC TLS 1.3 with h2, and HTTPS discovery with gRPC TLS 1.2 and no ALPN;
  - gates and the control API, the conditional OAuth routes, and corruption without fixture crashes;
  - live pacing with bounded events, startup validation, IPv6 listen, and real HTTP/2 keepalive;
  - r1 connection loss followed by reconnect, oversized trailers, `:authority` capture, and the blackhole and http_status endpoints.

  The run used `-race -count=2`.
- The built binary was smoke-run for a legacy banner/trace, a phase6 HTTPS+gRPC TLS banner and summary, and rejection of an invalid script.
- **Not verified here:**
  - pb (Go or Rust) process behavior against any of these scripts;
  - TLS outcomes for a wrong name or old version against the real clients;
  - whether `SSL_CERT_FILE` is honored by each client build;
  - long soak resource behavior.

  These belong to the process harness and measurement layers.

## Measurement-review r1: additive r5 protocol

The agreed protocol is `/home/ajs/.cache/pb-phase6/tmp/fx/proto-r5.md`.
Existing stable, replace and capped delta generation, scripted stop rules, event
fields, and frozen r4 scenarios stay unchanged. `main.go` needs no r5 edit.
The r4 binary, `bin/`, and frozen references are never refreshed by this work.

`dictionary.mode: "cycle"` starts with `cardinality` entries of generation 0,
adds that many entries per batch up to `max_entries`, then replaces the dictionary
with the initial size in generation 1, and repeats. Values are `gN-level-i`,
padded to `value_bytes`; indices remain `sequence % current_size`, with nulls
at `sequence % 7 == 6`. Each subscription starts its own generation 0. Cycle
cardinality must not exceed the cap; zero options use the existing defaults
(16 entries, 4096 cap). Only the new cycle options gain this validation.

Each stream gains these exact counters, independent of event sampling:

- `live`: `ops`, `stopped_by_count`, `stopped_by_deadline`,
  `stopped_by_termination`, `scheduled_batches`, `unbounded_ops`,
  `emitted_batches`, `omitted_by_deadline`, `omitted_by_termination`,
  `lateness_ns` and `send_call_ns` (each `{count,sum,max}`), plus `last`
  (`scheduled`, `emitted`, `stop_reason`, `started_ns`, `deadline_ns`,
  `ended_ns`, `interval_ms`, `duration_ms`, `batches`). Ops and scheduled counts
  are recorded when the op starts; emitted counts advance after a whole batch
  succeeds. Stop totals and `last` are finalized when the live op returns.
- With a duration and positive interval, the schedule is
  `ceil(duration_ms/interval_ms)`, capped by `batches` when present. A count-only
  op schedules `batches`. Duration with zero interval is unbounded even with
  a batch cap: `scheduled_batches` is null permanently for that stream and
  `unbounded_ops` advances. Omission counters apply to bounded schedules.
- Exact lateness is first batch Send start minus the absolute due time, for
  completed batches. `send_call_ns` and `batch_send_call_ns` measure the span
  from immediately before the first batch Send until the last Send returns,
  including an error. Failed batches contribute Send duration but no emitted
  batch or lateness sample. Separate schema Sends are outside that span.
  The duration includes marshaling, copying, flow-control waits and scheduling;
  it cannot be interpreted as pure socket blocking. Existing event `lateness_ns`
  (before preparation) and `send_ns` (from the emit-start timestamp) retain their
  previous meanings; admission uses the exact summary aggregates.
- `dictionary`: `messages`, `initial`, `deltas`, `replacements`, `resets`,
  `max_size`. Each successful DictionaryBatch Send is counted, including one
  preceding a failed RecordBatch Send. Initial means the first dictionary of
  that subscription; resets count cycle replacements. Stable, replace and delta
  are covered too. Builder-made dictionaries start with initial and subsequently
  use replacement classification.
- `buckets`: `origin_ns` (first DoGet), `bucket_ms` (stream option
  `summary_bucket_ms`, default 60000), `overflow`, and `counts`. Each count has
  `batches`, `rows`, `dictionary_messages`, `dictionary_deltas`,
  `dictionary_replacements`. A batch uses its `batch_emit_start` timestamp;
  dictionary messages can be counted before a record fails. Completed batches
  supply rows and batch counts. At most 512 buckets are retained; overflow
  counts batch starts beyond that range once each. These Send counters describe
  regular Flight batch ops; raw HTTP/2 fault actions do not call ServerStream.Send.

Every summary, including startup and the write before terminal status, contains
`fixture_cpu_user_ns`, `fixture_cpu_system_ns` (RUSAGE_SELF), `fixture_start_ns`
(CLOCK_MONOTONIC), `expected_output_ns_total` (sum of stream totals), and `t_ns`.
Fetch baseline and final summaries to compute CPU deltas; server snapshot time
is not a substitute for the client's control-request uncertainty bounds.

R5 validation artifacts are in
`/home/ajs/.cache/pb-phase6/tmp/fx/r5-logs/`: `test.log`, `race.log`, `vet.log`,
`gofmt.log`, and `functional.json`. New tests use Send mocks and causal gates
for cancellation/deadline cuts, an independent full-byte cycle oracle with more
than two wraps per subscription and dropped events, exact dictionary counters
for all modes, partial-send accounting, 512-bucket boundaries/overflow, and
startup/terminal CPU and timestamp presence. These are functional checks;
none is a throughput, backpressure, timing or soak claim.

The immutable r5 binary is
`/home/ajs/.cache/pb-phase6/tmp/fx/pb-fixture-r5` (mode 0555). Its adjacent
`pb-fixture-r5.manifest.json` records schema version 1, binary path/hash/size,
exact fixture source and module hashes, pinned Go executable path/hash/version,
build flags/argv, build environment and embedded module build information.
The manifest is also read-only. No measurement window or profiling was run.
