# PR #7: remaining SQL/HTTP and Flight differences

This audit covers the twelve SQL cases that PR #6 left in the phase-three
`known-divergence` tier and the two Flight differences the retained suites
declared: the wire user-agent and the `p6-corrupt-garbage_header` diagnostic.
Each one was re-run against the pinned Go oracle and traced to the pinned Go
sources (`go1.25.4` `net/http`, `github.com/google/flatbuffers` v23.1.21,
`github.com/apache/arrow/go/v13` v13.0.0). Every difference was then either
fixed with regression tests or recorded below as an intentional exception.

## Outcome

| Area | Before | After |
| --- | --- | --- |
| Phase-three `known-divergence` tier | 12 cases outside the gate | Empty. All 12 are in the gate with unchanged Go pins |
| Flight `p6-corrupt-garbage_header` diagnostic | Waived (`P6-DIAG-GARBAGE-HEADER`) | Matches Go byte for byte. Waiver removed from live comparison |
| Flight wire user-agent | Declared, exact waiver | **Unchanged intentional exception** (see below) |

## Case-by-case dispositions

Evidence binaries are listed under [Evidence](#evidence). The "Go output" column
shows the pinned reference bytes; `<C>`/`<F>` are the client and fixture
`host:port` values the harness normalizes.

| Case | Go output (pinned) | Frozen PR #6 Rust (`976f293a…`) | Cause | Disposition |
| --- | --- | --- | --- | --- |
| `p3-text-status-empty-reason-empty` | `No response body returned (status: 200 ).` | `… (status: 200).` | Go's `Response.Status` is the raw status line after the protocol (`strings.TrimLeft`), so `HTTP/1.1 200 ` keeps its delimiter. Hyper exposes only the code and reason, so `200 ` and `200` were indistinguishable | **Fixed**, gate |
| `p3-text-status-empty-reason-no-rows` | `Query succeeded: no rows returned (status: 200 ).` | `… (status: 200).` | Same | **Fixed**, gate |
| `p3-json-status-empty-reason-empty` | `No response body returned (status: 200 ).` | `… (status: 200).` | Same | **Fixed**, gate |
| `p3-status-empty-reason-text` | `Error: query failed: server returned 500 : boom` | `… returned 500: boom` | Same | **Fixed**, gate |
| `p3-status-empty-reason-json` | `"message": "query failed: server returned 500 "` | `… returned 500"` | Same | **Fixed**, gate |
| `p3-status-empty-reason-empty-body` | `… server returned 503  with an empty response body` | `… 503 with …` | Same | **Fixed**, gate |
| `p3-status-299-text` | `… server returned 299 : server says <no> é` | `… 299: …` | The fixture writes `HTTP/1.1 299 ` (no standard reason). Same cause | **Fixed**, gate |
| `p3-status-299-json` | `"message": "query failed: server returned 299 "` | `… 299"` | Same | **Fixed**, gate |
| `p3-text-reset-mid-body` | `… failed to stream response body: read tcp <C>-><F>: read: connection reset by peer` | `… Connection reset by peer (os error 104)` | Go returns the socket's `net.OpError` (crypto/tls passes it through unchanged) | **Fixed**, gate |
| `p3-json-reset-mid-body` | `"message": "error decoding JSON response: read tcp <C>-><F>: read: connection reset by peer"` | `… Connection reset by peer (os error 104)` | Same | **Fixed**, gate |
| `p3-text-close-before-headers` | `… Post "http://<F>/api/v1/query": EOF` | Identical | Already fixed before PR #6 by the head-EOF classification; the exclusion was stale | **Promoted**, no source change |
| `p3-text-malformed-status-line` | `… Post "…": net/http: HTTP/1.x transport connection broken: malformed HTTP status code "abc"` | `… invalid HTTP status-code parsed` | Go's `ReadResponse` quotes the rejected token (`badStringError`); Hyper's error carries no token | **Fixed**, gate |
| Flight `p6-corrupt-garbage_header` | `… arrow/ipc: unknown error while reading: runtime error: slice bounds out of range [4022250974:12]` | `… Type \`i32\` at position 4022250974 is unaligned.\n\n` | Go reads the unverified root offset and then `Message.HeaderType()`. `t.Bytes[pos:]` panics, and the reader recovers the panic as text. Rust ran the flatbuffers verifier first | **Fixed**; waiver removed |
| Flight wire `user-agent` (every Flight case that opens a stream) | `grpc-go/1.65.0` | `tonic/0.14.6` | See intentional exception 1 | **Intentional exception** (exact waiver retained) |

## Implementation

### HTTP status text (`src/transport.rs`)

- The tracked plaintext stream (above TLS, where Go's `persistConn` reads)
  feeds received bytes into a per-exchange `HeadScan`, which restarts at each
  new request. It records the final response's status line. Non-terminal
  informational heads (1xx except 101) are skipped through their blank-line
  terminator, exactly as both Go and Hyper discard them, so an interim
  `103`/`100` line can never supply the final status. Lines may span any
  number of reads.
- `Response::status_text()` is Go's `Response.Status`:
  - HTTP/1: the recorded line after the protocol, leading spaces trimmed. It is
    used only when Go's checks accept it and its code equals the response's
    code.
  - HTTP/2: `code + " " + StatusText(code)`, matching Go's `h2_bundle`. Go's
    `StatusText` table now lives in the shared transport and is reused by Flight.
  - Otherwise (no usable line): code plus Hyper's reason, as before.
- `ApiResponse`, `StreamingResponse` and `ApiError::HttpStatus` carry this
  `status_text` instead of a reason. Every caller that rebuilt Go's
  `resp.Status` from code and reason (`sql`, saved queries, TUI SQL, API
  errors) now prints it verbatim. One side effect: `ApiError::HttpStatus`
  previously printed `500 ` for a response with no reason, where Go prints `500`.
- On a Hyper parse error on an HTTP/1 connection, the recorded line goes through
  Go's `ReadResponse` status-line checks: `malformed HTTP response`,
  `malformed HTTP status code`, `malformed HTTP version`, with `strconv.Atoi`
  sign handling (`-00` is accepted as code 0; only negative values are
  rejected). A rejected line yields
  `net/http: HTTP/1.x transport connection broken: <Go error>`. A line Go
  accepts keeps Hyper's diagnostic (exception 4).
- The socket wrapper below TLS records the local/peer addresses. On Unix, a
  real `ECONNRESET` read error becomes
  `read tcp <local>-><peer>: read: connection reset by peer`. The
  `ConnectionReset` kind is preserved, so retry and `CONNECTION_FAILED`
  classification are unchanged.

### Flight header (`src/tail/session.rs`)

`ipc_message` now reproduces Go's unverified reads before verification:
`GetRootAsMessage(buf, 0)` and then `HeaderType()` through
`Table.Offset(6)`. That covers each `t.Bytes[off:]` slice, the fixed-width
getters' first bounds check (`_ = buf[3]` / `buf[1]` / `buf[0]`), and Go's
`UOffsetT`/`SOffsetT` wrapping arithmetic. Every Go message path (schema,
dictionary, record batch) calls `msg.Type()` first. A header that survives these
reads continues to the existing verifier, so valid input is unaffected. The
existing short-header `index out of range [3] with length N` result is the
first step of the same emulation.

## Intentional exceptions and limitations

These are explicit; this PR does **not** claim blanket HTTP reason or Flight
wire parity.

1. **Flight user-agent.** tonic 0.14.6's channel stack always inserts
   `tonic/<version>`. Its `UserAgent` layer appends to any caller value and sits
   below every caller-controlled layer. Matching `grpc-go/1.65.0` requires
   replacing tonic's `Channel` (lazy connect, reconnect, buffering, HTTP/2
   settings and error shapes) with a hand-built Hyper HTTP/2 service. That is
   feasible, but it touches every retained Flight case, so it is deferred to a
   dedicated follow-up and was not attempted here. The harness waiver
   (`split_wire_ua`) still accepts only the exact pair
   `['grpc-go/1.65.0']` / `['tonic/0.14.6']`. The proxy `CONNECT` request,
   which Rust writes itself, already sends `grpc-go/1.65.0`.
2. **Non-UTF-8 reason bytes.** Go prints the raw status-line bytes. Rust's
   message strings convert them lossily (`U+FFFD`), as before this PR.
3. **Status lines over 8 KiB** (including the line ending) are not recorded.
   They fall back to code plus Hyper's reason. Non-empty reasons still match, but
   the empty-versus-absent reason distinction and Go's malformed-line quoting
   are lost for such lines. Regression tests cover the bound for newline-bearing
   and split chunks.
4. **Status lines Go accepts but Hyper rejects** fail in Rust with Hyper's parse
   diagnostic, while Go succeeds. Examples: several spaces before the code, code
   `099`/`+20`/`-00`, or `HTTP/2.0` on an HTTP/1 connection. Unit tests pin Go's
   acceptance of these lines; no Rust success path is claimed.
5. **Malformed header lines** after a valid status line keep Hyper's text
   (for example `invalid HTTP header parsed`), not Go's `textproto` errors. These
   are not in the oracle corpus.
6. **A status line rejected before its newline arrives.** Hyper can reject a
   bad status token from a partial read; Go waits for the full line. With no
   complete line recorded, Hyper's diagnostic remains. The pinned fixture case
   arrives in one segment.
7. **Connection-reset wording** is mapped only for reads, only on Unix, and only
   for `ECONNRESET` (the observed oracle case). Windows Go reports
   `wsarecv: An existing connection was forcibly closed by the remote host.`.
   Write-side resets, other errnos, and IPv6 zone names (Go prints the interface
   name, Rust the index) keep Rust's text.
8. **HTTP/2 status text** follows Go's source (`h2_bundle.go`) and is
   unit-tested. The phase-three oracle fixtures are HTTP/1 only, so no oracle
   run exercises it.
9. **Deeper malformed Flight metadata.** Only the root and `HeaderType()` reads
   are emulated. Metadata that survives them but is otherwise invalid still gets
   arrow-rs verifier or decoder text. This is the pre-existing documented
   difference, narrowed.
10. **Concurrency scope.** The CLI uses a current-thread runtime. The recorded
    line is read immediately after Hyper returns the response head, with no
    intervening await, so another task cannot reuse the connection between
    those operations. Sequential pooled requests are regression-tested. This
    does not establish correctness for a future multithreaded transport API,
    where response metadata would need to remain bound to its exchange.

## Evidence

All runs used `/home/ajs/.cache/pb-phase7/baselines/pb-go` (sha256
`fae8411f…`) and, where needed, `pb-go-cloud` (`622d4144…`), with
`TMPDIR` outside the full `/tmp`.

| Run | Candidate | Command | Result |
| --- | --- | --- | --- |
| Baseline | frozen PR #6 `candidate-r4/pb-normal` `976f293a…` / `pb-cloud` `16143072…` | `phase3.py compare … --include-known-divergence --case <12>` | 1 pass (`p3-text-close-before-headers`), 11 fail (bytes above). `~/.cache/pr7audit/base-r4/` |
| Interim | local release `cand1` `939c4767…` / `bb452255…` (all fixes except the later `-00` correction) | same | 12/12 pass. `~/.cache/pr7audit/cand1-p3/` |
| Final SQL | parent frozen `harness/results/pr7/frozen/pb-normal` `d22a2c55…` | `phase3.py compare --reference pb-go --candidate <frozen> --meter pb-meter --case <12>` (gate tier, refrozen manifest verified) | **12/12 pass** as gate cases. `~/.cache/pr7audit/frozen-p3/` |
| Flight baseline | `candidate-r4` | `phase6.py compare --suite main --case p6-corrupt-garbage_header` | `diagnostic_issues: [P6-DIAG-GARBAGE-HEADER …]` plus UA wire waiver. `~/.cache/pb-phase6/tmp/pr7-audit/p6-garbage-r4/` |
| Flight interim | `cand1` | same | `diagnostic_issues: []`; UA waiver only. `…/p6-garbage-cand1/` |
| Flight final | parent frozen `pb-normal` `d22a2c55…` | `phase6.py compare --suite main` with the 5 `p6-corrupt-*` cases (`garbage_header`, `truncated_body`, `empty_message`, `foreign_batch`, `dictionary_before_schema`), waiver removed | **5/5 payload/auth pass**, `failed 0`, `diagnostic_divergent 0`, `wire_ua_divergent 5`. `…/p6-frozen-corrupt/` |

The full 25-gate replay of these frozen PR #7 binaries subsequently passed.
See the [integrated validation report](pr7-validation.md) for retained manifests,
test logs, exact Flight counts and the distinction between strict wire parity
and payload/auth passes with the user-agent exception.

### Tests added or changed

- `src/transport.rs`:
  - `status_text_keeps_the_received_go_status`: empty, absent, custom and
    double-space reasons; 299; `HTTP/1.0` with bare LF; status lines split
    across reads.
  - `informational_responses_never_supply_the_final_status_text`: `103`
    with headers and a CR/LF split across segments, `100` followed by a split
    final line, and chained `103`/`102` with bare LF.
  - `pooled_requests_each_report_their_own_status_text`: three sequential
    requests on one pooled connection, including an informational head.
  - `oversized_status_lines_fall_back_to_hyper_reason`: same-chunk and split
    oversized lines, the exact boundary, and end-to-end fallback.
  - `rejected_status_lines_report_go_read_response_errors`: `abc`, `1234`,
    `-12`, bad version, no space, invalid byte.
  - `go_status_line_accepts_what_go_accepts`: includes the `-00`/`+00`
    acceptance and signed rejections.
  - `http2_status_text_joins_go_status_text`.
  - `body_reset_names_both_socket_ends_like_go` (Unix).
- `src/tail/session/tests.rs`: `garbage_headers_reproduce_go_header_type_panics`
  covers the fixture shape, a short root table, vtable out of range and short,
  slot out of range, the header byte at the end, a short vtable read as no
  header, and valid schema/batch headers.
- `harness/test_phase3.py`: the 12 cases must stay in the gate and must
  have manifest `classification_changes` entries.
- `harness/test_phase6.py`:
  - `split_diagnostics` is gone.
  - With the exact UA pair, the old Rust diagnostic remains a compare failure
    (`stderr_contract_b64 differs`).
  - The historical-only matcher in `phase6_blocked_writer.py` still accepts only
    the retained exact pair.

Local runs during implementation:
- `cargo test --lib transport::`: 74 passed.
- `cargo test --lib tail::`: 125 passed, 1 pre-existing ignore.
- `python -m unittest test_phase3 test_phase6 test_phase6_blocked_writer`:
  83 run, 81 passed and 2 skipped.

The integration owner reports full `cargo test --locked` and strict all-target,
all-feature clippy passing on the final source.

## Harness and replay notes

- `harness/contracts/phase3-reference.json` was refrozen in the same way as the
  earlier `p3-argv-invalid-utf8-sql` promotion. No Go pin changed. Only
  `corpus_sha256` (`f6f2896e…` → `0182ce6a…`) and `selected_sha256`
  (`d85f08db…` → `b807fcbc…`) changed, and 12 `classification_changes` entries
  record the previous hashes and reasons.
- The default phase-three gate is now 688 cases instead of 676. The opt-in
  `known-divergence` tier is empty.
- `harness/final_replay.py` needs no change; it does not pin per-gate case
  counts. Expect Flight-main to report `diagnostic_divergent: 0` and the
  unchanged UA wire count.
