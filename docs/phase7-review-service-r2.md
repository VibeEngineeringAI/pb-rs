# Phase 7 service contract review, round 2

This is an independent, read-only re-review of the native shared service APIs,
the saved-apply profile policy, the auth HTTP error, deadline, redirect and
refresh semantics, and child FD limits. It is not phase acceptance. Device poll
ownership and lifecycle are being changed by their owner and are excluded here,
so they need a separate review.

- Worktree: `feat/phase7-interactive`, uncommitted state as of 2026-10-14.
  Base `4a812d5`. Go oracle `07af390` (Go source and module cache unchanged).
- Method: source reading with the file-read tool only. The agent shell failed
  with EDQUOT on every call, so **I compiled nothing and ran no tests**. I could
  not read the Go tree directly (`git show` needs a shell). Go behavior below
  comes from three sources: the round-1 citations, the parent's actual-Go probes
  reported to me in this round, or my knowledge of Go 1.25 `net/http`/`net/url`.
  Each claim says which.
- Files read: `src/api.rs` (all non-test code), `src/process.rs`,
  `src/sql/mod.rs` (dispatch and fetch), `src/lib.rs` (dispatch and
  preflight), `src/tui/sql_service.rs`, `src/tui/promql_service.rs`,
  `src/tui/sql.rs` (session construction), `src/tui/device.rs` (request/poll
  error mapping and browser launch only) and `tests/interactive_effects.rs`
  (whole fixture).
- Evidence quoted from the brief, not reproduced by me: 348 unit tests pass
  (this predates the latest edits); 14 device Go cases and the overflow
  candidate case pass; the effects suite has 34 ordinary, 4 deadline and
  2 OAuth cases, with 78 Go invocations. Candidate results for the final build
  are still pending, so **no acceptance is claimed**.

## Verdict

All six round-1 findings are **resolved in source**. None is closed by executed
candidate evidence yet. There is one new low finding (R2-1), a residual case of
S4. There is one validation question (R2-2) that needs an oracle trace but is
not a finding. I found no new medium or high issue in the reviewed scope. I
expect no regression in ordinary phase 1–5 behavior from these changes. The
reasoning is under "Regression risk".

## Prior-finding dispositions

| ID | Round-1 issue | Disposition | Evidence (source) | Remaining |
| --- | --- | --- | --- | --- |
| S1 | OAuth refresh state shared across interactive effects | **Resolved in source** | `ApiClient::effect_scope` (`api.rs:391-402`). Each scope gets a new `profile` mutex seeded from `initial_profile`, a new `refresh_gate` and a new metrics `OnceLock`. Only the ordinary `Arc` transport is shared. Every network effect takes its own scope: windows (`sql_service.rs:96`), schema GET+fallback in one scope (`:118`), datasets (`:175`), metrics results and cache (`promql_service.rs:144`, `both` = two `metrics_fetch` calls = two scopes), metric datasets (`:159`), builder (`:226`). The parent `Arc<ApiClient>` is used only for `profile()`/`profile_url()` and never sends, so `initial_profile` stays the session-start snapshot (`tui/sql.rs:88-93`). | Candidate run of the two `oauth_*` cases; R2-2 |
| S1 (related) | Per-call metrics transport; refresh through it | **Resolved in source** | A scope builds its own `Client::metrics()`. `refresh_uses_request_transport: true` sends the scoped refresh through the request's transport, which is the metrics transport for PromQL results. Ordinary clients keep `false` (refresh on `self.transport`), so phase 4/5 behavior is unchanged. | Not observable in the current fixtures (see the test notes) |
| S2 | Saved apply re-read persisted config after the menu | **Resolved in source** | `lib.rs:232-247` keeps the preflight `(ProfileStore, ProfileRecord)`. `lib.rs:285-288` passes it to `sql::execute_saved`, then to `fetch_query(.., Some(profile), ..)` (`sql/mod.rs:166-174`, `228-241`). The menu fetch still resolves the persisted default on its own. Ordinary `sql run` passes `None` and is unchanged. | Candidate run of `saved_profile_snapshot`. Trigger B (OAuth menu refresh) and the deleted-config variant are untested. |
| S3 | `post_json` checked scheme/host before the token, without the `url.Error` wrapper | **Resolved in source** | `api.rs:1248-1260`: JoinPath (`invalid orchestrator URL:` context) → `orchestrator_headers` (token) → `checked_http_url`, mapped to `Request{POST, raw, ..}`, giving `Post "ftp://x/…": unsupported protocol scheme "ftp"`. `device.rs:309-310` leaves only `Context` unprefixed. | Oracle item 4. **Correction to round 1:** for `http://`, Go `JoinPath`+`String()` produces `http://api/v1/cli/device/code` (empty host, so no separating slash), not `http:///…`. Rust's `append_url_string` copies that (`has_host` false at `api.rs:1171-1176`), so both dial host `api`. Round 1's expected text for that variant was probably wrong (Go knowledge, not run). |
| S4 | Deadline errors named the original URL, not the current hop | **Resolved in source for the header stage** | `post_json` passes `timeout_url`, which `send_cloud_request` updates at the top of each hop (`api.rs:1529-1531`), into `expired()` for both the client-timeout and context-first variants. This matches the parent's actual-Go probes (`device-redirect-oracle-1`, `-2`): device code `307→/slow` gives `Post "<current hop>": context deadline exceeded (Client.Timeout exceeded while awaiting headers)`; token `307→slow` with a 2 s expiry gives `cloud token request failed: Post "<current hop>": context deadline exceeded`. | Candidate run of the 16-case device harness. R2-1 below |
| S5 | A failed child `setrlimit` aborted the browser spawn | **Resolved in source** | `process.rs:65-70` ignores the `setrlimit` result and returns `Ok(())`. The test (`:102-118`) lowers the hard limit to 64 in an earlier `pre_exec`, so the restore fails with EPERM, and still asserts that the exec succeeds. | Unit test rerun on the final build |
| S6 | Go `time.Duration` overflow not reproduced | **Resolved (device owner)** | `docs/phase7-auth.md` L-AUTH-12: `wrapping_mul(1e9)`, a nonpositive expiry means already expired, a nonpositive interval means zero sleep. The poll loop takes the expired branch when `now+interval` is not before the deadline (`device.rs:345-352`). The Go device oracle and the candidate overflow case pass (brief). | Owner is reworking poll ownership, so re-check after that change |

## New findings

### R2-1 — LOW: a deadline that expires while draining a redirect body names the previous hop; Go names the next hop

- **Where:** `send_cloud_request` (`api.rs:1569`, `drain_redirect` before
  `current = next` at `:1586`). The `timeout_url` is only updated at the top of
  the next iteration. The same order exists in `ApiClient::follow_redirects`
  (`api.rs:684` vs `:642`/`:705`). That path predates phase 7 and is used by
  ordinary phase 1–5 requests.
- **Go (1.25 `Client.do`, from source knowledge; not oracle-run):** in the
  redirect branch, Go builds the new `req` for hop N+1 *before* it slurps up to
  2 KiB of the hop-N body and closes it. If the client deadline passes during
  that slurp, the next `c.send(req, deadline)` fails immediately and
  `uerr(err)` uses the new `req.URL`.
- **Trigger:** device-code POST → `307 Location: /next` with
  `Content-Length: 100`. Send 10 body bytes, then stall more than 30 s.
  - Go: `failed to create cloud device code: Post "http://h/next": context deadline exceeded (Client.Timeout exceeded while awaiting headers)`.
  - Rust: the same text naming `…/api/v1/cli/device/code`.
  - The context-first variant during polling differs the same way.
- **Proposed resolution:** in `send_cloud_request`, write `next` into
  `timeout_url` before `drain_redirect`. Add one fixture where the 307 body
  stalls. Do not change `follow_redirects` in this phase without a ledger entry,
  because that would change ordinary-command error text. Record it as a known
  pre-existing divergence instead. (`validate_api_key` still reports the
  original endpoint for header-stage timeouts after any redirect, at
  `api.rs:1335-1339`. Round 1 recorded this and it is unchanged.)

### R2-2 — VALIDATION (not a finding): token reuse inside one effect after a refresh

`fetch_schema` uses one scope for the schema GET and the query fallback, as Go
uses one client. After a refresh during the GET, Rust's fallback sends the
refreshed `S1`, because the scope's profile mutex was updated (`api.rs:792-796`,
then `prepare_with_policy` reads `self.profile()`). This matches Go only if
`cloudRefreshTransport` mutates the same `*config.Profile` that the fallback's
`AddAuthHeaders` later reads. Round 1 described the refresh as updating "that
request's copy", which leaves this open. **Trigger:** OAuth profile, schema GET
returns 401 for S0 and then 404 for S1, fallback query. Record the cookie on
the fallback in Go. This needs an OAuth variant of `sql_schema_fallback`.

## Test inspection (`tests/interactive_effects.rs`)

- **S1 coverage is real.** `oauth_promql_both_effects` asserts this sequence:
  range S0 → refresh(R0) → range S1 → instant S0 → refresh(R0) rejected →
  `cloud session refresh failed`. The fixture rotates, so it returns 200 once
  and then 401 for reused R0. The old shared-state bug would have sent S1 on
  the instant request and only one refresh, so this test tells the two
  behaviors apart. `oauth_sql_window_effects` does the same for window 0 and
  then window 1 (`S0,S1,S0` cookies, two refreshes). Both tests assert that
  the persisted tokens are exactly (S1, R1), which also shows that the failed
  second refresh persisted nothing.
- **Header casing is handled.** `header()` and `assert_oauth`/`assert_requests`
  compare lowercased names, so hyper's lowercase and Go's canonical spelling
  are equivalent. Bodies are compared as exact bytes where Go's encoding
  matters (saved apply).
- **The protocol assertion does not tell the transports apart.**
  `request['protocol'] == 'HTTP/1.1'` (line 691) holds for every client on
  cleartext loopback, because neither Go nor hyper uses h2c and
  `http.server` only speaks HTTP/1.x. It does not prove the HTTP/1-only
  metrics transport. It also cannot show which transport carried the scoped
  refresh POST. Only connection-reuse observation (for example, client port
  per request) could show that. Treat the related S1 note as unvalidated.
- **S2 coverage is real.** `saved_profile_snapshot` points profile B at
  `<url>/target-b` with a different key. It switches the default on disk after
  the menu list is drawn, so the menu fetch has finished. It then asserts the
  request path (through `assert_requests`), A's key on every request, and that
  config is not rewritten. Not covered: deleting the config while the menu is
  open, and Trigger B (OAuth refresh during the menu fetch, then apply sending
  stale S0 and refreshing again).
- **Running the OAuth cases.** Both are `#[ignore]` and need a cloud build with
  `PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN=harness-orchestrator-token`. If
  `PB_EFFECTS_CLOUD_CANDIDATE` and `PB_EFFECTS_CANDIDATE` are both unset, the
  Rust run falls back to `CARGO_BIN_EXE_pb`, which normally has no token. That
  run fails with an auth error rather than skipping. Set the cloud candidate
  explicitly when recording acceptance.

## Regression risk to ordinary phases 1–5 (source reasoning, not executed)

- `ApiClient::new` behavior is unchanged: `refresh_uses_request_transport:
  false` refreshes through `self.transport` as before, metrics requests still
  share one lazily created metrics transport, and `initial_profile` is an
  unused clone outside interactive scopes.
- `lib.rs` preflight resolution, error wrapping and its position are
  unchanged. The result is now kept instead of dropped. `ProfileStore` holds
  only a path (writes serialize on a process mutex during the write), so
  keeping it alive locks nothing. `sql run` still resolves its own profile at
  fetch time (`supplied_profile: None`).
- `fetch_query` keeps ordering, error prefixes, spinner gating and
  `save_after_query` inputs. `post_json` and `effect_scope` are new code and
  have no ordinary callers.
- `process.rs` keeps the phase-5 raise algorithm and Apple clamp. Only
  `restore_child_limits` changed, and it has no ordinary caller.
- Residual: the `follow_redirects` drain/deadline URL case (R2-1) already
  exists for ordinary commands. It is not new and is not changed here.
- Negligible: Rust stores the inherited limit even when it did not raise it,
  or when the raise failed. Go remembers it only after a successful raise. The
  two differ only if an external `prlimit` changes the soft limit of a process
  that started at `cur >= max-1` (Rust restores, Go does not). Record only.
- Assumption to confirm with the build: `pb sql list` has
  `requires_profile = true`. The `expect` at `lib.rs:287` would panic
  otherwise. Round 1 and the existing saved-apply cases imply it is true. I
  did not open `spec.rs`.

## Remaining validation (not run by this reviewer)

1. Run the final candidate through the full effects suite, including
   `saved_profile_snapshot` and both `oauth_*` cases against the cloud
   candidate, plus the 16-case device harness (S4 redirect deadlines).
2. Rerun the unit tests on the final build:
   `process::tests::child_restores_inherited_limit_without_lowering_parent`
   and the `api::tests` device and redirect cases.
3. Oracle item 4 (S3), with the `http://` expectation corrected as above. It
   is unclear whether that probe has already run.
4. R2-1 fixture (stalled 307 body during the device POST) on Go and the
   candidate.
5. R2-2 OAuth schema-fallback trace on Go.
6. Optional: an S2 Trigger B fixture, and a refresh-transport fixture using
   connection identity.
7. Retained phase 1–5 process gates on the final build.
