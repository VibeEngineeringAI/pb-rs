# Phase-five administration contract

`harness/phase5.py` characterizes the reachable dataset, user and role
administration commands of the pinned Go reference (`07af390`, public version
`v0.0.0-harness`) and compares a candidate against it. `phase5-reference.json`
is a frozen, complete, passing Go capture. It holds per-case signatures and
binary hashes for `bin/pb-go-pinned` and `bin/pb-go-cloud-pinned`, which match
the phase-four frozen oracles. This harness authorizes no real server mutation.
Every request goes to a disposable loopback fixture, using synthetic
identities, credentials and datasets.

```sh
# Independent unit tests (the Go smoke tests run when bin/pb-go-pinned exists)
python3 -m unittest harness/test_phase5.py -v
python3 harness/phase5.py list

# Go characterization plus comparator self-validation (3 extra Go runs per case)
python3 harness/phase5.py check-reference --reference bin/pb-go-pinned \
  --cloud-reference bin/pb-go-cloud-pinned --repeat 3 --out OUT/go
python3 harness/phase5.py freeze OUT/go          # rewrites contracts/phase5-reference.json

# Candidate gate: frozen Go signatures + fresh Go run + candidate, then deferred boundaries
python3 harness/phase5.py compare --reference bin/pb-go-pinned \
  --cloud-reference bin/pb-go-cloud-pinned --candidate target/release/pb \
  --cloud-candidate target/harness-cloud/release/pb --out OUT/rust

# Opt-in real 60-second body deadline (user add reads its body to EOF)
python3 harness/phase5.py compare ... --include-full60s --case p5-user-add-stalled-body-deadline --out OUT/deadline
```

Outputs must be new directories. Set `TMPDIR` to disk-backed storage with
enough quota, because every sample creates and removes a temporary HOME.
The command exits 0 only if every selected case passes.
Skipped cases, such as a missing Cloud binary, count as failures.
`--case` and `--group` produce partial reports that cannot be frozen. The
standard gate does not run deferred boundaries for partial or `--self-check`
reports.

## Case model

A case has one fixture lifecycle, one HOME, one or more ordered CLI steps and
a seeded fixture state. Each binary gets its own fresh fixture with the
identical declared state. Seeding and reset happen out of band and are never
part of a transcript or timing. Each step records:

- termination, stdout and stderr
- the raw request ledger: method, raw target, every request header except
  `User-Agent`, body bytes and framing
- the fixture state and commit ledger after the step

The final HOME tree is compared semantically, as in phases 2 to 4.

Expectations come from this file's own model, not from a capture. They cover:

- ordered request lines, JSON request bodies and selected headers. Examples are
  `X-P-Telemetry-Type`, `X-P-Log-Source` (absent for logs), JSON
  `Content-Type` on bodyless requests, `Content-Length` and Cloud cookie/tenant.
- rendered user and role tables, using a Go/lipgloss width model:
  - East Asian wide = 2
  - tabs measured as zero and rendered as four spaces
  - `…` truncation at 64 (users), 42 (roles) and 48 (dataset cells)
- JSON documents, success strings and error substrings
- public JSON error codes, commit counts and wall-time bounds
- the final fixture state and config effects

The Go capture must satisfy them. Repeated Go runs must then match each other
under the comparator below.

## Stateful fixture

`phase5.py serve` extends the phase-three byte-scripted server with an
administration model:

| Endpoint | Model |
|---|---|
| `GET user` | `[{id, method}]` in insertion order |
| `GET user/ID/role` | `{roles: {R: {actions}}, group_roles: {G: {R: {actions}}}}` |
| `POST user/ID` | JSON string array; duplicate user or unknown role → 400; response body is the password bytes |
| `PATCH user/ID/role/add` | additive set union that keeps existing direct and group assignments |
| `DELETE user/ID` | 404 if missing |
| `GET roles` | `{name: actions}` |
| `GET role/NAME` | `{actions: ...}`, `null` preserved |
| `DELETE role/NAME` | 404 if missing |
| `PUT logstream/NAME` | records `X-P-Telemetry-Type`/`X-P-Log-Source`; duplicate → 400 |
| `DELETE logstream/NAME` | 404 if missing |
| `GET logstream/NAME/info` | `{createdAt, streamType, telemetryType}` or a declared raw body |
| `POST /orch/api/v1/cli/oauth/token` | validates the bearer token, headers and refresh grant, then rotates tokens once |

Unmodeled paths return 404 and are flagged as outside the modeled API, unless a
case explicitly allows them (`../` path cleaning, URL path prefixes). Fixture
assumptions that are not CLI-proven:

- Role deletion while the role is assigned is rejected with 400.
- `POST user` deduplicates stored roles but records the request body verbatim.
- Error bodies use simple synthetic text.

Scripted routes take precedence over the model, with `times`, header matching,
`path_re`, delays, chunk scripts, stalls, aborts, `close_before_headers` and
`content_length`. Two extra decorators apply:

- `model: true` runs the model first, so its commit is real, and then delivers
  the response through the route's fault options (`force_status`,
  `force_body`, delays, stalls, disconnects). This expresses "committed, then
  the connection failed".
- `gather: N` holds matching requests until N have arrived, with a 3-second
  limit. A timeout is recorded and fails the case. This makes concurrent 401s
  within a fan-out deterministic.

`/__harness/state` returns the state and the ordered commit ledger
(request index and operation).

## Declared nondeterminism (the only normalization)

Go fans out unbounded goroutines for `GET user/ID/role` and `GET role/NAME`.
Role names come from map iteration, and user direct/group roles come from maps.
The comparator therefore:

1. **Requests.** It sorts each maximal contiguous run of GET requests whose
   path matches the case's declared fan-out pattern. Everything else stays
   exact and in place:
   - list-before-detail barriers
   - token refresh position
   - membership, multiplicity, headers and bodies
   - non-GET requests

   A refresh POST inside a fan-out run, or a replay that crosses the refresh,
   is a difference.
2. **User list text.** It sorts the comma-separated roles cell of each row
   (multiplicity kept). User row order stays exact.
3. **User list JSON.** It sorts each `roles` array (multiplicity kept). User
   order stays exact. Indentation, final newline, object-field order, Unicode
   and HTML escaping are checked before this rule is applied.
4. **Role list text.** It sorts role blocks. A block is the role's first row
   plus continuation rows and the lipgloss-padded lines of a multi-line error
   cell. Action order within a role stays exact. Role list JSON needs no rule,
   because Go marshals map keys sorted.
5. **Role list aggregate error.** It sorts the `; `-joined per-role causes, in
   text and JSON envelopes. User-list aggregate errors follow user order and
   are not normalized.
6. **Debug headers.** Cases with `PB_DEBUG_HTTP_HEADERS` compare stderr lines
   unordered, because `http.Header` map iteration is random.

`check-reference --repeat N` validates these rules against Go itself. The
report lists `order_variation_cases`, the cases whose raw request order
differed between Go runs. Those same cases must still compare equal after
canonicalization. Rust may not rely on a rule that Go self-comparison has not
exercised.

## Characterized Go behavior pinned by the corpus

- **Dataset type.** The `--type` value is trimmed and lowercased.
  - Only `logs`, `metrics` and `traces` are accepted. Anything else is a plain
    error (`COMMAND_FAILED`) with no request.
  - Metrics and traces also send `X-P-Log-Source: otel-TYPE`.
  - `add` never reads first.
  - Typed `remove` reads `info` first. A non-empty `telemetryType` wins over
    `streamType`. JSON keys match case-insensitively and the last duplicate
    wins. The actual type is not lowercased. `null` gives an empty type.
    A mismatch or 404 stops before DELETE.
  - A blank `--type` means unguarded deletion.
- **User add.**
  - Cloud profiles (API key or session) are rejected before any request.
  - Duplicate detection (`GET user`) happens before the role read.
  - The outer role text is trimmed. It is split on commas and each element is
    trimmed. Duplicates and order are kept, and an empty component fails
    validation.
  - The first missing role stops the command. Guidance lists roles sorted.
  - The password is the raw response body bytes, including invalid UTF-8 and
    NUL.
- **Set-role.**
  - The local argument check (`requires at least 2 arguments`) runs after
    profile initialization, so a missing profile wins.
  - Extra arguments are ignored.
  - The original second argument is printed unmodified.
  - A missing user stops before the role read. The Cloud message differs.
- **List validation timing.**
  - `user list` validates `-o` after all detail requests. JSON failure emits no
    document.
  - Text mode prints partial rows, then fails with a `COMMAND_FAILED`
    aggregate, even when a detail failed with 403.
  - `role list` validates `-o` after `GET roles` and before details.
- **Response consumption.**
  - `dataset add/remove`, `user remove` and `role remove` close a 200 body
    without reading it. A stalled success body still exits 0 quickly.
  - `user add` and `set-role` read to EOF. A slow body delays completion, a
    truncated body fails with `unexpected EOF` after the commit, and a stalled
    body hits the 60-second deadline (tier `full60s`).
  - Non-200 2xx responses (201, 204) are failures, and the body is trimmed into
    the text message.
- **Commit points.**
  - A disconnect before or after the commit of DELETE or POST is not retried.
  - A GET whose reused keep-alive connection closes before headers is retried
    once by Go's transport (`p5-reused-connection-read-retry`). A candidate
    must reproduce this or record it as a deviation.
  - Output failure after a commit keeps the committed state. Mutation success
    text ignores `/dev/full` (exit 0). A closed stdout gives SIGPIPE after the
    commit. List JSON propagates `failed to encode JSON output`.
- **OAuth refresh.**
  - A 401 before the commit triggers exactly one refresh and one replay with a
    byte-identical body and the same telemetry headers. The commit happens
    once, and the rotated tokens are persisted.
  - Concurrent detail 401s, gathered, share one refresh followed by N replays.
  - Refresh rejection or persistence failure ends with no replay, no commit and
    an unchanged config.
  - Profiles without a refresh token surface the 401.
- **Parsing.** Mutations have no `-o` flag, so `-o json` is an unknown-flag
  parse error. The raw argv scan still selects the JSON error envelope.

## Deferred boundaries (candidate only)

`role add NAME` (interactive privilege selector, including its preceding
duplicate read), `dataset add NAME`, and `dataset add NAME --type '  '` (type
picker) belong to Phase 7. The candidate must exit 1, print the explicit
`is not implemented in this Rust phase` stub, make no request and leave HOME
unchanged. These are not parity cases.

## Limitations

- Loopback HTTP/1.1 only, against a synthetic model. No live server
  administration evidence is claimed.
- No timing or resource measurements are produced here. Workload definitions
  and performance runs belong to the parent measurement plan, with
  reset-per-sample state.
- User/role names containing `, ` would defeat the user-text role-cell sort.
  The corpus avoids them in multi-role rows.
- Phase-five interactive selectors, PTY behavior and cancellation remain
  Phase 7.

## Raw server-error bytes

The corpus also checks invalid UTF-8, NUL, tabs and multiline server-error
bodies. Text preserves the response bytes through status errors and partial
inventory tables. JSON aggregate errors escape each invalid byte as `\ufffd`
while retaining valid replacement characters. Typed HTTP JSON errors retain
the shorter public status message. Mutation commands do not acquire an output
flag just to expose JSON errors.

The opt-in `p5-user-add-stalled-body-deadline` also normalizes the two observed
Go timer-race messages: bare `context deadline exceeded` and the same error
annotated with `Client.Timeout or context cancellation while reading body`.
Only that case's exact first error line is normalized; its usage, requests,
commit, output and elapsed-time requirement remain checked. Raw captures remain
available. Earlier deadline normalization covers the corresponding annotated
`net/http: request canceled` variant.

## Concurrency experiment

The provisional limit of 64 in-flight inventory reads failed the
`p5-user-list-wide-gather` fixture: Go reaches its 65-request barrier and the
capped candidate times out at the barrier. The shipped path therefore starts all
detail reads, matching Go. Results remain indexed by listing position. The
comparison still permits sibling arrival order to vary; it preserves request
membership, multiplicity and barriers. Rust presents map-derived role names in
first-insertion order from the response, compared against Go's random map order.
