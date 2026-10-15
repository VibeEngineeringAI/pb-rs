# Phase 7 service contract review, round 1

Independent read-only contract review of the coordinator-owned service changes.
This is not phase acceptance. The final independent phase review is a separate task.

- Worktree: `feat/phase7-interactive`, uncommitted state as of 2026-10-14.
- Base: `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`. Oracle: Go
  `07af390ab09bda8ac9e427ffc6310077bace0125`, read from `git show 07af390:<path>`.
- Scope: the diff against base in `src/api.rs`, `src/process.rs`, `src/main.rs`,
  `src/datasets.rs`, `src/sql/mod.rs` and `src/lib.rs`. I read their callers in
  `src/tui/{device,sql_service,promql_service}.rs` and `src/saved_queries.rs`
  only to see how the adapters are used. I did not review those modules as
  finished code.
- Method: source reading only. I compiled and ran nothing, because builds are
  blocked on unfinished modules and the harness hits EDQUOT. I could not check
  the Go 1.25 net/http and syscall behavior against a local toolchain, since
  mise has no Go version set. Where a statement relies on my knowledge of Go
  1.25 stdlib internals, I say so; the oracle should confirm those points.
- The commands I am requesting are in
  `/home/ajs/.cache/pb-phase7/service-review-requests.md`.

## Verdict

**Not approvable yet.** There is one high-severity blocker and one medium
contract divergence. Four low findings should be fixed or recorded in the
ledger. The saved-menu print/LIMIT split, the literal default ranges, the
`URL.Query().Set` emulation, the per-request timeout choices and the
ordinary-SQL refactor all match the oracle as far as source reading can tell.

## Findings

### S1 — HIGH (blocker): OAuth refresh state is shared across the interactive session; Go refreshes per request from a stale profile copy

- **Where:** `src/api.rs:348-355` (`ApiClient` owns `profile: Mutex<ProfileRecord>`
  and `refresh_gate`). `src/api.rs:707-723` (`send_authenticated` compares the
  request token with the shared current token). The new adapters
  `prepare_standard_query`, `prepare_auth_get` and `prepare_builder_get`
  (`src/api.rs:422-490`) are all methods on one shared client. Consumers hold
  `Arc<ApiClient>` for the whole session: `src/tui/sql_service.rs:81-87` and
  `src/tui/promql_service.rs:34-40`.
- **Go:** each effect builds a new client over a by-value copy of the
  session-start profile. Examples: `NewSQLWindowFetchTask(profile config.Profile, …)`
  then `DefaultClient(&profile)` (`pkg/model/query.go:2603-2622`),
  `fetchStreamSchema` (`:3041-3048`), `fetchAllStreams` →
  `datasets.FetchHomeDatasets(profile)` (`pkg/datasets/datasets.go:59-66`),
  `promqlModelFetch` (`pkg/model/promql.go:2209-2221`) and `builderHTTPGetCtx`
  (`:2601-2603`). `m.profile` is never updated. `cloudRefreshTransport.refresh`
  (`pkg/http/refresh.go:85-131`) changes only that request's copy and persists
  the new tokens with `config.UpdateCloudTokens(old, …)`.
- **Trigger:** use a Cloud OAuth profile whose session token has expired and
  whose orchestrator rotates refresh tokens.
  1. `pb promql run -i` in both mode: the range request is followed by the
     instant request.
  2. Or `pb sql run -i`: first window, then any later window, schema, or dataset
     reload.
- **Expected Go behavior:** request 1 gets 401, refreshes with R0, persists
  (S1, R1), and replays. Request 2 still sends the session-start cookie S0, gets
  401, and refreshes again with R0. A rotating orchestrator rejects that, so the
  UI shows `cloud session refresh failed: cloud session refresh failed: 401 …`.
  A non-rotating orchestrator returns another refresh POST and another
  CAS-guarded persistence attempt. The startup batch (`fetchAllStreams` plus the
  first window, `query.go:457`/`533`) also runs two independent refreshes
  concurrently, each under its own mutex.
- **Rust behavior:** request 1 refreshes once, and every later request sends S1
  silently. With the shared gate, a concurrent second 401 sees the changed token
  and skips its refresh. The request trace has fewer refresh POSTs, and the
  error states shown differ.
- **Proposed resolution:** give `api.rs` a per-effect client scope. For example,
  `ApiClient::scoped(&self, profile: ProfileRecord) -> ApiClient`, which shares
  the transport but gets a new profile mutex and refresh gate. Seed each scope
  from the immutable session-start profile snapshot at each Go
  client-construction site. The schema GET and its query fallback share one
  scope, as they share one Go client. If the coordinator would rather keep the
  shared refresh, that is a behavior change. It then needs a divergence-ledger
  entry with the scenario above, approval, and a delayed-401 fixture. It must
  not be adopted silently. Note that `network.md` recorded concurrent refresh as
  underspecified, but the sequential cases above are deterministic in Go.
- **Related (low, recording only):** Go's PromQL result path builds a fresh
  `http.Transport` per call (`promql.go:2218`). That means no connection reuse,
  and the refresh POST for that path goes through that HTTP/1-only transport.
  Rust caches `metrics_transport` per client and refreshes through
  `self.transport`. Record this for request-trace and performance comparisons.

### S2 — MEDIUM: saved-menu apply re-reads the config after the menu; Go applies the `DefaultProfile` captured at PreRun

- **Where:** `src/sql/mod.rs:221-230`. `fetch_query` calls
  `ProfileStore::from_env()` and then `active_profile()` at execution time.
  `execute_saved` (`:135-183`) reaches it only after
  `saved_queries::select_menu` returns, and the menu can stay open
  indefinitely. The preflight at `src/lib.rs:231-239` resolves the profile and
  then throws it away.
- **Go:** `PreRunDefaultProfile` sets the global `DefaultProfile` before `RunE`
  (`cmd/pre.go:34-51`). After the menu, `savedQueryToPbQuery` uses
  `DefaultClient(&DefaultProfile)` (`cmd/queryList.go:149`), the in-memory
  copy taken before the menu. The menu's own fetch re-reads the file
  separately (`pkg/model/savedQueries.go:146-155`).
- **Trigger A:** with profile A as default, open the `pb sql list` menu. In
  another shell, run `pb profile default B`, or remove A, or delete the config.
  Then press Enter on an item.
  - Go: POST `A/api/v1/query` with A's credentials.
  - Rust: POST to B. If the config was deleted, Rust fails with
    `Error: selected saved query failed: failed to create new request: …`
    where Go executes the query.
- **Trigger B:** with a Cloud OAuth default and an expired session, the menu's
  `GET filters` refreshes and persists (S1, R1).
  - Go: apply sends the stale S0, gets 401, and refreshes again with R0. The
    S1 caveats about rotation apply.
  - Rust: apply re-reads S1 and succeeds without a refresh.
- **Proposed resolution:** pass the `ProfileRecord` resolved at preflight from
  `run_with_writers` into `sql::execute_saved`. Have `fetch_query` accept an
  optional supplied profile and build `ApiClient::new(profile, store)` from it,
  keeping the store only for token persistence. The ordinary `sql run` path
  stays unchanged, because nothing interactive happens between its preflight
  and its fetch.

### S3 — LOW: `post_json` checks scheme and host before the token, and its errors lack Go's `url.Error` wrapper

- **Where:** `src/api.rs:1229-1230`. `checked_http_url(&raw)?` returns a bare
  `ApiError::InvalidResponse` before `orchestrator_headers`.
- **Go order (`cmd/cloud.go:474-486`, `412-425`):**
  1. `url.JoinPath`, giving `invalid orchestrator URL: …`.
  2. `NewRequestWithContext` parse.
  3. `AddCloudOrchestratorAuth`, giving
     `cloud orchestrator auth token is not configured`.
  4. `client.Do`. Scheme and host failures here become a `url.Error`, for
     example `Post "ftp://x/api/v1/cli/device/code": unsupported protocol scheme "ftp"`.
- **Trigger:** the oracle and fixture builds set the orchestrator URL at build
  time (`config.CloudOrchestratorURL`). Build with `ftp://x` or `http://` and
  run the device login.
  - Go: `failed to create cloud device code: Post "ftp://x/api/v1/cli/device/code": unsupported protocol scheme "ftp"`.
    For `http://`, the error is `… Post "http:///api/v1/cli/device/code": http: no Host in request URL`.
  - Rust: `failed to create cloud device code: unsupported protocol scheme "ftp"`.
  - With an empty token and a bad scheme, Go reports the token error and Rust
    reports the scheme error.
- **Proposed resolution:** build the headers first. Then map the
  `checked_http_url` error to `ApiError::Request { method: POST, url: raw, source }`,
  which is what `validate_api_key` already does at `api.rs:1291-1298`.
  `device.rs::is_request_error` then classifies the error the way Go does. In
  the poll path this case cannot be reached, because the device-code request
  fails first.

### S4 — LOW: `post_json` deadline errors always name the original URL; Go names the current redirect hop

- **Where:** `src/api.rs:1236-1254`. `expired` always captures `raw`.
- **Go:** `Client.do`'s `uerr` uses the current hop's `req.URL`. On a send
  error, `resp` is reset to nil. This is Go 1.25 client internals and the oracle
  should confirm it.
- **Trigger:** device code POST, then `307 Location: /slow`, then a stall past
  30 seconds.
  - Go: `failed to create cloud device code: Post "http://h/slow": context deadline exceeded (Client.Timeout exceeded while awaiting headers)`.
  - Rust: names `…/api/v1/cli/device/code`. The same applies to the
    context-deadline variant during polling.
- **Proposed resolution:** track the current hop, as `follow_redirects` does
  with `timeout_url` (`api.rs:621`), by passing a `&Mutex<String>` into
  `send_cloud_request`. `validate_api_key` (`api.rs:1305-1308`) has the same
  pattern from before this change. Leave it alone unless it goes through the
  ledger, and record it.

### S5 — LOW: a failed child `setrlimit` aborts the browser spawn; Go ignores the failure and execs anyway

- **Where:** `src/process.rs:57-75` (the `pre_exec` closure in
  `restore_child_limits`). It returns `Err` when `setrlimit` fails,
  so `Command::spawn` fails and `open_browser` silently does nothing.
- **Go:** `forkAndExecInChild` calls `rawSetrlimit(RLIMIT_NOFILE, rlim)` and
  discards the result. Go also stores the original limit only when it actually
  raised (`Max > 0 && Cur < Max-1`), and drops it after an in-process
  `Setrlimit` or `prlimit`. Rust restores in every case, which gives the same
  value because pb never changes the limit again.
- **Trigger:** while the device screen is waiting, an external
  `prlimit --pid <pb> --nofile=<soft>:<hard below inherited hard>`. The child's
  restore then fails with EPERM.
  - Go: the browser still launches.
  - Rust: there is no browser and no message.
- **Proposed resolution:** in `pre_exec`, call `setrlimit` and always return
  `Ok(())`. The comment and the test stay valid.
- **Lifecycle check (no finding):** stdio is `/dev/null`, the child is never
  waited on (a zombie until exit, as in Go), and spawn errors are ignored. The
  terminal module installs handlers rather than `SIG_IGN`, and std resets the
  signal mask in the child, so neither leaks into the child.

### S6 — LOW (device.rs, login owner; deadline policy): Go's `time.Duration` overflow is not reproduced

- **Where:** `src/tui/device.rs:457-460`, `570-576` and `306-312`.
- **Go:** `time.Duration(ExpiresIn) * time.Second` and the same product for
  `Interval` wrap around in int64 nanoseconds (`cmd/cloud.go:291-295`,
  `364-367`).
- **Trigger:** a device-code response with `"expires_in": 9999999999` or
  `"interval": 9999999999`.
  - Go, `expires_in`: the context is already expired. The first
    `waitForCloudPoll` returns at once with `cloud device login expired or timed out`,
    and the browser timer also loses.
  - Go, `interval`: a negative timer fires immediately, so `authorization_pending`
    produces back-to-back POSTs.
  - Rust: waits for centuries, or sleeps until the deadline and then reports
    expiry.
- **Proposed resolution:** compute the duration with Go's arithmetic,
  `(secs as i64).wrapping_mul(1_000_000_000)`, and treat a value of 0 or less as
  already elapsed. Add one fixture for each field. This finding belongs to the
  device owner; it is recorded here because deadline policy is in this review's
  scope.

## Contract points checked with no finding

- **Saved apply (`lib.rs:272-290`, `sql/mod.rs:135-183`):**
  - The apply gate (`id ≠ "" || TrimSpace(sql) ≠ ""`) matches
    `queryList.go:90`. Rust's `str::trim` and `trim_sql_space` use the same
    Unicode White_Space set as Go's `TrimSpace`.
  - `Empty query selected.` goes to stdout.
  - `Query:` prints the quoted SQL without LIMIT. `ensure_default_limit` is
    applied once, to the wire query only, matching `fetchData`
    (`query.go:709`).
  - Missing ranges are sent as the literal strings `"1h"` and `"now"`.
  - The output format is text, matching `""` in Go.
  - Text-mode errors carry the `selected saved query failed: ` prefix, as
    cobra's `Error: %v` prints it. The JSON status path omits the prefix,
    matching `errorDetails` taking `PublicMessage`. The `Api` and `Output`
    paths keep it, matching `err.Error()`.
  - The spinner is gated on stderr being a TTY.
  - **Nit:** `lib.rs:275` repeats `SavedSelection::should_apply()`. Calling
    that method would keep a single predicate.
- **`URL.Query().Set` emulation (`set_query_values`):**
  - It follows Go 1.25 `ParseQuery`: split on `&`, skip any pair containing
    `;`, skip empty segments, keep an empty key, and drop pairs with bad escapes
    while keeping the rest.
  - Decoding is byte-based, with `+` turned into a space.
  - It replaces values on `Set` and encodes in byte-sorted key order with
    `QueryEscape` (uppercase hex, `~` unescaped).
  - `ForceQuery` and an empty base produce the same `?fields=true`. A JoinPath
    failure gives `""`, then `?fields=true`, then `unsupported protocol scheme ""`,
    as in Go.
  - The debug header listing shows the URL before the mutation, as Go does
    inside `NewRequest`.
  - **Recording only:** a profile URL ending in a bare `#` keeps the `#` in
    error URLs, where Go's `String()` drops an empty fragment. This was already
    the case for every phase-5 request (`append_url_string` keeps the raw
    suffix), so it is not new here.
- **Deadlines and headers:**
  - SQL windows: 50 s, standard headers including Content-Type and debug
    output.
  - Schema GET: 15 s, auth headers only, no debug output.
  - Prism home: 15 s, with Content-Type, the join error returned rather than
    ignored, and status `>= 400`.
  - PromQL result: 120 s on the metrics transport, auth headers only.
  - Builder: 30 s, auth headers only, standard transport.
  - Device: 30 s per POST. An earlier context deadline wins, and on a tie the
    context also wins (Go's `timeBeforeContextDeadline` uses strict `Before`).
    In that case there is no `Client.Timeout` suffix, and a body-stage expiry
    reads `context deadline exceeded`.
  - POST redirects: 301/302/303 switch to GET and drop the body; 307/308 replay
    the body; a missing or empty Location returns the response; the 10-hop
    error carries the raw Location; the error method is always the original
    method; the Authorization header is stripped permanently after a host
    change.
- **Ordinary operations:**
  - The `sql run` refactor is behavior-preserving: same ordering, the body
    built from the twice-limited query, `save_after_query` receiving the
    once-normalized query, and an empty prefix.
  - The `datasets list` extraction of `fetch_home` is behavior-preserving.
  - The `main.rs` move of the rlimit raise into `process.rs` keeps the
    algorithm identical, including the Apple clamp.
  - The `pb login` dispatch placement (before config access, with no
    preflight) matches `LoginCmd`, which has no PreRun.

## Test gaps (requested, not run)

1. Add an end-to-end saved apply fixture that asserts the stdout order: the
   `Query: …` line, then the result. Assert the exact request body with LIMIT
   500 and the literal `1h`/`now`. Assert the prefixed text error and the
   unprefixed JSON status error. Today only `application()` has a unit test.
2. Add `prepare_standard_query` cases for:
   - a JoinPath failure, expecting the error URL `"?fields=true"`;
   - a profile URL that already has a query;
   - the debug-header URL being the one from before the mutation.
3. Run an oracle check that Go 1.25 keeps `Content-Type` and `Accept` on a
   302 POST→GET hop. `device_posts_preserve_headers_and_redirect_method_policy`
   asserts that they are kept, and I could not confirm it without a toolchain.
4. Add the S1 and S2 fixtures: a sequential 401 refresh in a PromQL both-mode
   session or a SQL window, and a config change while the menu is open.
5. Add the S3 and S4 error-text fixtures.
