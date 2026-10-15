# Phase7 interactive process and effect tests

Owned files: `tests/interactive_effects.rs` and this document. The tests run real
subprocesses with a controlling PTY and an independent HTTP fixture, rather than
calling production reducers or reproducing implementation decisions. They cover
persistence, request effects, cancellation and terminal restoration. The separate
phase7 harness owns terminal-cell/layout comparison.

## Integration and dependencies

The integration test embeds its Python fixture. It requires Linux PTYs and Python
3.11 or newer with the standard library (`pty`, `termios`, `tomllib`, threaded HTTP).
It needs no new Cargo dependency, production injection seam, CLI flag, or private
cache script. `PB_EFFECTS_PYTHON` can select the test interpreter. Set
`PB_EFFECTS_CANDIDATE` to an immutable prebuilt candidate path; otherwise the test
uses Cargo's `CARGO_BIN_EXE_pb`. If `PB_GO_ORACLE` is set, each ordinary case runs
against it first, then against the candidate. The ignored OAuth cases can instead
use `PB_GO_ORACLE_CLOUD` and `PB_EFFECTS_CLOUD_CANDIDATE`. An oracle failure stops
that case before the candidate runs. Both invocations get fresh HTTP servers and separate HOME/XDG
configuration trees containing only synthetic credentials.

`TMPDIR` chooses the temporary parent. For this coordinated phase7 session every
command and subprocess uses `/home/ajs/.cache/pb-phase7/tmp` for TMPDIR, GOTMPDIR
and CLAUDE_CODE_TMPDIR. Proxy and credential-selection environment variables are
removed from subprocesses. The tests fix timezone, theme and terminal dimensions
(UTC, dark, 120 columns by 35 rows). stdout is captured from the PTY; stderr is a
separate pipe. The terminal starts with canonical input and echo enabled.

No production API changes are needed to execute these cases. The parent owns
candidate builds and integration; candidate tests must wait for its readiness
signal. An eventual coordinated invocation is:

```sh
export TMPDIR=/home/ajs/.cache/pb-phase7/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
export PB_GO_ORACLE=/home/ajs/.cache/pb-phase7/baselines/pb-go
export PB_EFFECTS_CANDIDATE=/absolute/path/to/stable/prebuilt/pb
cargo test --test interactive_effects -- --test-threads=1
```

This is a functional command, not a benchmark. The parent has run an exploratory
candidate suite; the artifact may have changed during that run, so it is not a
stable acceptance gate. This test agent runs no local builds. Only the owned Rust
test file is passed to rustfmt.

## Assertions

All cases check normal exit status (including exact exit 1 for role conflict) and
restoration of the complete slave termios structure. Relevant cancel cases require
unchanged config bytes, not just equivalent parsed records. HTTP traces require
the expected request multiset/count, exact authentication, and operation-specific
method/path/body/parameters. Ordered effects are checked in causal order;
independent startup requests may arrive in either order. Extra calls and mutation
retries fail the assertions.

- `credentials_valid_cancel`: enter both valid fields, cancel before Submit, and
  require both persisted values, the new default, preserved existing profile,
  mode 0600, the success summary, and no plaintext password in terminal output.
- `credentials_username_only_cancel`: entering only a username and canceling saves
  the new profile with **both credential fields empty**.
- `credentials_positional_ignored`: a third positional username does not seed the
  fresh form; cancellation persists neither credential.
- `default_cancel`: cancellation leaves the configuration bytes unchanged.
- `replacement_cancel`: removing the default and canceling replacement removes
  that profile, retains both survivors, and persists an empty default.
- `logout_decline`: the `Continue? [y/N]:` prompt is canonical with echo enabled;
  `n` leaves configuration bytes unchanged, succeeds, and prints `Logout canceled`
  on stdout with empty stderr.
- `dataset_cancel`: cancellation succeeds with the stdout summary and no request
  or configuration write.
- `role_abort`, `role_submit`, `role_conflict`: prefetch names before prompting;
  abort has no PUT; submit selects editor and sends one PUT containing precisely
  `[{"privilege":"editor"}]`; an existing name fails before displaying Submit.
- `saved_apply_unchecked`: a decodable HTTP 500 filter array populates the menu.
  Press `/` then `a`: apply takes precedence over active filtering. The alternate
  screen is left before the printed query. Dotted fields are rewritten in both
  representations, only the wire query gains `LIMIT 500`, and missing times are
  transmitted as the literal `1h`/`now` strings.
- `saved_filter_quit`: `/` then `b` quits while filtering, with no query execution
  or DELETE. This also exercises the unchecked-status menu fetch.
- `sql_window_snapshot`: preserve the user limit/offset plan, use a first window
  of 500 at offset 17, include `fields=true`, and preserve explicit run times.
  Startup includes Prism discovery and the successful schema GET.
- `sql_window_stale`: hold the old SQL window, rerun, display the newer response,
  then release the old response. Its unique value must never render. The old
  request must remain open until completion; superseded-request abortion is not
  silently accepted. Both requests use the same explicit time snapshot.
- `sql_window_paging`: serve 500 then 250 rows for a 750-row query; End triggers
  the second window at offset 517 with limit 250. Home/End revisits cached rows
  without duplicate requests. Both windows share the fixed run times.
- `sql_schema_fallback`: a schema 404 produces a single fallback query after the
  GET, using `SELECT * FROM 'logs' LIMIT 1`, `fields=true`, the all-time lower
  bound `2000-01-01T00:00:00+00:00`, and a bounded current UTC upper bound.
- `sql_schema_fallback_clock`: hold an unusable primary schema GET across two UTC
  seconds measured with `time.time()`. Release at a recorded second and require the
  fallback's second-resolution `endTime` to be at/after that boundary and no later than
  its POST observation. The response's unique visible column depends on that comparison,
  so a pre-GET sample produces `CLOCK_BEFORE_GET` and fails after graceful cleanup.
  Exact GET→POST graph, auth/headers, all-time lower bound, quoting, normal exit,
  full termios restoration and unchanged config are checked. There is no tolerance
  that can accept a pre-release sample.
- `sql_schema_primary_no_fallback`: the same held primary returns a usable schema;
  `CLOCK_PRIMARY_OK` must render with exactly discovery GET then schema GET and no POST.
  R3-F1 retains the shared schema/fallback auth scope and each request's 15-second
  deadline. The durable parameterized runner is
  `docs/evidence/phase7/schema-fallback-clock-process.py`; pass `--binary`, `--role`,
  `--out`, `--expect-sha256`, and optionally `--case` to select one of these two cases.
  It loads only the embedded fixture, preserves failed raw captures, and needs no
  cache-only module. [Pre-fix evidence](evidence/phase7/schema-fallback-clock-before-fix.json)
  records pinned Go and immutable release3; successor validation remains centrally owned.
- `sql_prefix_before_eof`: the fixture flushes a complete QueryData object, then
  holds an advertised trailing body segment behind a release gate. The unique row
  must render while that gate is still closed and before the handler completes.
  Accepting the first JSON value with trailing bytes is part of this contract.
- `sql_invalid_prefix_before_eof`: the fixture flushes `!` and holds the remaining
  body behind the same kind of gate. The JSON invalid-character error must render
  before release. Whole-body buffering fails both prefix cases. Bounded screen
  waits detect hangs; there is no input-to-frame performance threshold.
- `sql_numeric_overflow`: a record containing numeric `1e400` must render the Go
  decode error (`cannot unmarshal number 1e400`, `QueryData.records`, `type float64`),
  rather than a row with zero or infinity. A query error leaves the interactive
  process alive; Ctrl+C still exits successfully and restores termios.
- `promql_result_policy`: result execution uses HTTP/1.1, the Prometheus route,
  `stream`, RFC3339 start/end strings and `30s` step; startup independently fetches
  Prism datasets and metric-name cache metadata.
- `promql_builder_policy`: use the prefetched metric cache, then fetch labels and
  values in response to successive confirmations. Requests retain stream/time/
  match parameters and auth-only headers. Explicitly select `fixture-host` and
  run `up{job="fixture-host"}` only after value selection. Opening the builder before
  startup cache completion permits one additional cache fetch, separately proven
  by the pending-cache oracle; more than two total cache requests fail.
- `promql_stale_result`: a newer result renders before a delayed older result;
  the latter is still allowed to render, as in the pinned Go baseline. The earlier
  request stays open until response completion. Adding result generation guards
  or superseded-request abortion would fail this preservation test.
- `promql_builder_metric_guard`: hold labels for metric A, move back to the metrics
  column and choose B without confirming it, then release A's completion. It must
  not populate the visible labels. Confirming B fetches its labels and the eventual
  result request must use B. Navigation alone keeps the earlier request open.
- `promql_builder_label_guard`: hold values for label A, return to the labels
  column and choose B before releasing A. The old values must not render; the
  subsequent B value selection must generate the B matcher in the wire query.
- `promql_builder_pending_cache`: hold the initial metric-name response, open the
  builder, and require its independent second fetch before releasing the first.
  This is Go's existing pending-cache path. Builder tests that do not observe the
  hidden completion admit exactly one or two cache fetches, not unbounded retries.
- `promql_dataset_cache_guard`: hold the initial dataset's metric cache, switch
  datasets and load the newer cache, then release the old response. Opening the
  builder must show the new metric and reuse that cache without a third fetch.
  Dataset navigation does not abort the old cache request.
- `sql_stale_schema`: hold dataset A's schema, select B, display B's columns, then
  release A. The baseline permits A's columns to overwrite the selected dataset's
  columns. This is intentionally different from the guarded window/builder cases;
  there is no schema generation guard or request abortion to introduce silently.
- `sql_cache_eviction`: walk through four distinct 500-row windows of a 2,200-row
  query. Return to the first window and require a new fetch, proving eviction from
  the three-window cache through real process effects. Requests retain fixed times.
- `sql_unknown_actual_total`: a full first window leaves the actual count unknown
  and displays the query's 2,200-row bound. A 123-row second response establishes
  the actual total as 623. End navigation uses that total without requesting another
  window. A truly unbounded total is not reachable through the audited CLI plan,
  which applies a user or default limit; this case covers the pending actual count.
- `promql_mode_transitions`: replay range→instant→both→range using NUL on the step
  panel. Initial explicit RFC3339 range/step are preserved; instant moves evaluation
  to approximately now minus one hour; both fetches range **then** instant with the
  same evaluation time. Entering instant selects the one-hour preset, so both uses
  that duration despite the initial explicit ten-minute range. Returning to range
  resets end to now while retaining the start from both. The resulting final range
  is approximately two hours. The fixture freezes explicit inputs and bounds only
  the baseline's unavoidable wall-clock transitions.
- `saved_apply_result_order`: require the exact restored-PTY suffix containing
  the printed Query line followed by the compact text result and final CRLF. The
  exact wire body contains one LIMIT 500 and literal `1h`/`now` ranges.
- `saved_apply_text_status`, `saved_apply_json_status`: a failed applied query
  retains the printed Query line. Text stderr includes `selected saved query failed:`
  plus the server body; structured stderr uses the unprefixed public status message
  with SERVER_ERROR, HTTP 500 and retryable true. For the latter, raw argv includes
  a JSON option followed by an empty final output, keeping the menu reachable while
  preserving the existing raw-argv error-format scan. This adds no production flag.
- `saved_profile_snapshot`: open under profile A, externally change the file's
  default to B while the menu is displayed, and apply. Both fetch and execution use
  A's origin/key and the external B default remains unchanged on disk. This proves
  preservation of the preflight snapshot across the menu. The fixture does not
  inject a write between preflight and the menu's separate initial config read.
- Ignored `oauth_promql_both_effects` and `oauth_sql_window_effects`: the first
  result/window sends session-0, refreshes refresh-0, persists session-1/refresh-1,
  and replays with session-1. The subsequent instant/window still sends session-0
  and refreshes refresh-0 again; a rotating fixture rejects that second grant. The
  exact sequential traces and persisted first rotation are asserted. Startup Prism,
  schema and cache responses intentionally succeed to isolate these sequential
  effects from concurrent refresh/persistence races.
- Ignored `oauth_schema_fallback_scope`: with an empty SQL editor, discovery
  triggers schema S0→401, refresh R0→S1, schema retry S1→404, then the fallback
  query sends **S1** and succeeds. Exactly one refresh is permitted. The schema
  requests have auth-only headers; fallback adds JSON content type and
  `fields=true`, sends `SELECT * FROM 'logs' LIMIT 1` and the all-time lower bound,
  displays the returned column, and leaves S1/R1 persisted. This is one effect's
  client scope, shared between its schema GET and query fallback.
- Ignored `oauth_saved_menu_apply_scope`: menu filters S0→401, refresh R0→S1,
  filters retry S1→200. Applying still sends the command-preflight **S0**, even
  though config already contains S1/R1. The second refresh POST uses R0 and is
  rejected by the rotating fixture. No query replay follows; config retains the
  first rotation. The printed Query line precedes failure, wire SQL has LIMIT 500
  and literal `1h`/`now`, and stderr retains the saved-query failure prefix.

The fixture never talks to a production server. Each child is killed/reaped on a
test failure, and fixture gates are released during teardown. Terminal waits and
request waits have bounded deadlines. Concurrent fixture handlers record request
arrival and completion under a condition variable.
Every failure also emits `EFFECT_FAILURE_CONTEXT` into the test log with argv,
request bodies/headers, response status/preview/size, terminal tail, stderr and
synthetic config. Bare assertions therefore retain actual queries and results.

## Oracle evidence and corrections

Production pin verification is `git diff --exit-code 07af390 -- main.go cmd pkg`;
it is empty. Harness-only Go additions are outside this production pin comparison.
The existing preserved oracle binaries have SHA-256:

- `pb-go`: `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`
- `pb-go-cloud`: `622d4144b7e354c0ee4036244ba719cbbaf8357710804b1d733cd421a0dc227e`

Characterization corrected these initial fixture assumptions; none was normalized
away as a candidate allowance:

1. `credential.Values()` returns two empty strings unless both fields are valid.
   The plan's “partial credentials” wording cannot justify persisting a lone
   username. Valid but unsubmitted fields are saved on cancellation.
2. Role discovery is `/api/v1/roles` and returns a map of role names to details;
   the submit body contains the privilege only, without an invented resource.
3. SQL rewriting leaves a safe bare dataset name unchanged. Dotted-field quoting
   is independently exercised by the saved-apply case.
4. Saved apply sends raw range defaults, not resolved timestamps.
5. Interactive SQL startup can issue a schema GET and then a fallback query;
   this is a separate request family from windows. The tests explicitly separate
   successful-schema and fallback scenarios.
6. PromQL TUI result parameters are RFC3339 strings plus `stream` and a duration
   step, rather than Unix seconds, `dataset`, and a numeric step.
7. The builder values picker initially chooses `(any)` on this path. A visible
   fixture value must be selected explicitly before expecting an equality matcher.
8. Entering instant mode selects the one-hour preset. The first transition test
   incorrectly expected both to retain the explicit ten-minute initial duration;
   the observed one-hour range and retained-start transition are now asserted.

Early failure records remain in the private cache as `effects-go-results.txt`,
`effects-go-corrections.txt`, and `effects-go-r3.txt` through `effects-go-r5.txt`.
Captured stdout/stderr and request/config records are retained under
`/home/ajs/.cache/pb-phase7/effects-captures`. Setting `PB_EFFECTS_CAPTURE_DIR`
enables these optional observer artifacts; it does not alter production behavior.
`effects-go-final.txt` records the final replay of the embedded repository fixture
against both oracle variants: all original 19 cases pass on each (38 subprocess
invocations). `effects-go-streaming.txt` and `effects-go-streaming-corrections.txt`
record the three additional decode cases on each variant. All three now pass on
both (six additional invocations); the initial numeric assertion attempted to
inspect an unrelated footer before the complete frame arrived and was corrected.
`effects-extra-r1.txt` retains the initial transition mismatch; `effects-cache-r1.txt`
and `effects-extra-final.txt` establish all seven additional short cases on both
oracle variants. There are **29 short oracle-backed cases**, with **58 passing
short oracle invocations** at that stage. The S1/S2 additions pass in
`effects-review-r1.txt` and `effects-review-go.txt`. The revised completion-barrier
cases and pending-cache proof pass on both variants in `effects-sync-go-r1.txt`
and `effects-sync-cloud.txt`. The suite now has **34 ordinary cases**, **four
ignored deadline cases**, and **four ignored OAuth scope cases**. Per-case oracle
evidence covers 68 ordinary invocations, eight genuine-deadline invocations, and
four OAuth invocations against the configured cloud variant: **80 passing oracle
acceptance invocations**, excluding exploratory repetitions and retained failures.
No local build or Rust test was run by this test agent.
`effects-review-header-check.txt` rechecks the OAuth/profile cases after correcting
their fixture header lookups to HTTP's case-insensitive rules; raw spellings are
retained in captures. Lower-case Hyper headers are accepted without altering
credential-value assertions.
`effects-review-r2-scope-go.txt` and corresponding raw captures record the real Go
schema/fallback and saved-menu/apply sequences above. Both new cases pass against
the configured `pb-go-cloud`. They reused the existing fixture; no local build,
deadline replay, timing run, or device harness change was performed for this slice.

## Candidate1 triage and fixture synchronization

The parent's completed exploratory `effects-candidate1.log` reports 23 passing,
six failing and four ignored tests, using the earlier 29-case ordinary suite.
The candidate artifact may have changed during that run. The triage and rerun
instructions are in `/home/ajs/.cache/pb-phase7/effects-requests.md`.

The six failures must not all be labeled production bugs from that run:

- Builder label guard reached the right query but made two startup metadata
  requests. The pending-cache Go test proves that second fetch is legitimate
  when the builder opens before the hidden completion.
- Dataset-cache and stale-schema selections could race their dataset reload.
  Revised fixtures introduce beta only on the reload and wait for it to become
  visible before selecting. PromQL auto-selection makes the initial alpha cache
  request evidence that initial discovery reached the reducer.
- A mode label could render before the result refocused the table. Replies now
  carry unique result markers, and the next focus/toggle sequence waits for them.
- Batched paging could skip an intermediate frame or request another window
  before a partial response established its total. Paging now sends one action,
  waits for the changed row counter, and waits for the partial-total completion
  before End. Window 3 is observed before the eviction check returns to window 0.

Both Go variants pass the revised fixtures. Candidate1 alone does not prove a
remaining production defect. The parent must replay against an immutable path
using `PB_EFFECTS_CANDIDATE`; failure diagnostics now include actual requests and
response previews. Broader scheduling permutations remain scoped unknowns, not
permission to add request deduplication, cancellation or stale-response suppression.

## Opt-in genuine deadline gate

Four ignored Rust tests, `deadline_schema_15s`, `deadline_builder_30s`,
`deadline_sql_50s` and `deadline_promql_120s`, run real subprocesses and leave the
fixture's response headers behind a release gate until the client times out. There
is no shortened production timeout or new CLI flag. Ordinary suite invocations
skip these four tests. The parent can run them separately after candidate readiness:

```sh
export TMPDIR=/home/ajs/.cache/pb-phase7/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
export PB_GO_ORACLE=/home/ajs/.cache/pb-phase7/baselines/pb-go
export PB_EFFECTS_CAPTURE_DIR=/home/ajs/.cache/pb-phase7/effects-captures
cargo test --test interactive_effects deadline_ -- --ignored --test-threads=4
```

Each test invokes the oracle first and the candidate second. With four test
threads, expect roughly four minutes for the longest case when both binaries run.
The ignored annotation and test filter are test-runner controls, not production
speed flags. No candidate gate has been executed by this test agent.

Schema timeout is observed through its fallback request; builder timeout through
its `(any)` failure state; SQL/PromQL through their displayed expiry result. In
every case the response gate must still be closed, and the timed-out connection
must have closed before the fixture sends a response. Request counts exclude
retries. Observation must fall between the documented deadline minus one second
and deadline plus twelve seconds: the lower bound rejects an incorrect shorter
policy, and the upper bound is a hang allowance for client/render scheduling.
These checks establish request deadlines; they are not performance measurements.

The original SQL observer incorrectly searched for a contiguous phrase that wrapped
across panel lines. Go also produced two timeout descriptions under otherwise
identical held fixtures: `context deadline exceeded` and `net/http: request canceled
(Client.Timeout exceeded while awaiting headers)`. Their exact choice remains a
scoped timing-race unknown. The gate looks for the shared expiry word, verifies
time and connection closure, and retains complete frames. It does not claim exact
timeout-message byte parity or silently normalize those captures. Initial failed
records remain in `effects-deadlines-go-r1.txt` and `effects-deadlines-go-cloud.txt`;
correction runs have separate files.
All four gates pass on both Go variants. The observed expiry values are roughly
15, 30, 50 and 120 seconds as expected. These records establish policy only.

## Opt-in synthetic OAuth scope gate

The four ignored `oauth_*` cases require the release-style cloud bearer-token
fixture build. The preserved `pb-go-cloud` supplies
`harness-orchestrator-token`; the candidate must be built by the parent with
`PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN=harness-orchestrator-token`. This setting is
compile-time fixture configuration, not a new runtime option.

```sh
export TMPDIR=/home/ajs/.cache/pb-phase7/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
export PB_GO_ORACLE_CLOUD=/home/ajs/.cache/pb-phase7/baselines/pb-go-cloud
export PB_EFFECTS_CLOUD_CANDIDATE=/absolute/path/to/stable/cloud-fixture/pb
cargo test --test interactive_effects oauth_ -- --ignored --test-threads=1
```

The rotating fixture makes client scopes observable. Independent result/window
effects and saved menu/apply send two refresh POSTs with the original token; the
second is rejected, so no second replay or configuration rotation occurs. The
schema/fallback case instead requires one refresh and reuses the rotated session
within that effect. Concurrent startup refresh/persistence ordering is deliberately
excluded from these sequential tests and remains a separate scenario. Likewise,
the saved-profile case changes the default after
the menu opens; it does not claim a deterministic injection point between the
command preflight and the menu's separate config read.

## G3 service decoder: duplicate result reuse

The test/service agent owns the narrow G3 change in `src/tui/promql_service.rs`
and its inline tests. The PromQL owner retains `promql.rs` G4/G5/G7/G10; saved.rs
R2-3 belongs to its separate owner. No Cargo, network-policy or public result-type
changes are made for G3.

The old decoder created a fresh series on each duplicate `data.result` field,
losing earlier maps and omitted sample fields. The new decoder keeps owned
initialized slots plus a logical active length for series and sample slices.
It decodes series in place and exposes the final public vectors only after the
ordered document completes. Rust allocation capacity does not determine reuse.

Actual pinned Go PTYs establish these contracts:

- Duplicate result arrays reuse matching series slots. Absent metric/value/values
  fields retain the earlier values. A null series element is a no-op on a concrete
  struct. Shorter arrays truncate the visible length but preserve hidden slots for
  re-expansion later in the same JSON document.
- A null or empty result array discards those slots. A null data struct instead
  leaves existing result state intact; duplicate data objects continue decoding
  into the same state.
- Metric objects merge keys; `{}` preserves old keys, while metric null resets
  the map. A null map value overwrites that key with an empty string rather than
  retaining its earlier label.
- Value/values slices truncate and clear according to their typed array fields.
  A null matrix point clears that point. Objects and arrays inside an interface
  sample element are fresh values, so they replace prior nested objects/arrays;
  interface null clears the old element. Typed-slice reuse does not justify
  merging these interface values.
- The first decode error survives later duplicate repairs or null resets. Wrong
  matrix point types still fail with `[]interface {}`. The oracle exposed an
  existing float64-overflow diagnostic mismatch in the same decoder: Go includes
  `.data.result.value` or `.data.result.values`; Rust previously omitted the field
  path. This explicitly recorded correction preserves the actual error context,
  including nested interface objects, instead of yielding zero/infinity or success.

Both parent controls use raw JSON with duplicate `result` keys inside `data`:
first metric `{"__name__":"up","job":"a"}`, then metric `{"instance":"i"}`.
Vector/instant uses `value:[1704067200,"1"]` then value 3; matrix/range uses the
corresponding `values:[[...]]`. Both render `up{instance="i", job="a"}` and 3.
For a single matrix point Go first shows no numeric chart data; the private
fixture waits for the result-focused footer and toggles to table. The parent's
independent instant control is recorded in
`oracle-promql-gaps-2/p7-promql-g3-duplicate-result-merge-dark/session.json`, with
separate dark/light workloads and explicit metric/value cell assertions. Parsing
the raw response into a normal JSON object before replay would erase the duplicate
keys and invalidate either control.

Durable characterization is in `/home/ajs/.cache/pb-phase7/g3-oracle/`: **27 Go
PTY cases pass**, covering null/empty/shorter/re-expanded arrays, duplicate data
and metrics, nested interface replacement, and error preservation. Named JSON
records include the exact raw body, response trace, terminal and stderr; raw
stdout/stderr files are separate. `provenance.json` pins source response types,
source hash, baseline commit and executable hash. Production Go pin remains
unchanged. Seven new inline Rust contract tests cover these semantics; the owned
service passes rustfmt checks. The parent owns Rust compilation/testing. No local
build, candidate session, deadline replay or timing campaign was run for this fix.

## SQL Unicode process contracts

Three focused cases are registered in `tests/interactive_effects.rs`: current
groups are **37 ordinary cases, four ignored real deadlines, and four ignored
OAuth cases**. They run real subprocesses on controlling PTYs against the embedded
independent local HTTP fixture. No production change, dependency, public flag,
candidate invocation or local build is part of this assignment.

The source control is `docs/evidence/phase7/sql-lower-oracle-test.go.txt` and its
`sql-lower-oracle-results.txt` output. Pinned Go `07af390` uses the byte index from
`strings.ToLower` to slice the original SQL in `extractDataset` and
`resolveDatasetPlaceholder`. `cmd/query.go` quotes unsafe stream names and adds
the default LIMIT before `NewQueryModel`; its constructor extracts the dataset
before creating the Bubble Tea program. These paths must be distinguished from
an already running editor. The production Go files remain diff-empty against
the pin.

- `sql_unicode_schema_raw_bytes` starts an empty editor with no discovered
  datasets, bracketed-pastes `select 'Ⱥ' from 测试`, and closes/reopens the columns
  drawer with Ctrl+L. Actual Go requests exactly
  `/api/v1/logstream/%B5%8B%E8%AF%95/schema`: the raw dataset bytes are
  `b'\xb5\x8b\xe8\xaf\x95'`, rather than UTF8 replacement characters. The test
  verifies both the encoded path and decoded bytes, auth-only schema headers,
  no query POST, unchanged config, and the returned `RAW_BYTE_SCHEMA` column.
  Starting this SQL directly on the CLI would first quote the Unicode table,
  changing the helper input and invalidating this particular control.
- `sql_unicode_placeholder_wire` starts empty, waits for discovered `logs` and
  its schema, bracketed-pastes `select 'İ' from dataset`, then runs with Ctrl+R.
  Actual Go sends `select 'İ' from'logs' LIMIT 500 OFFSET 0`, including the missing
  space before `'logs'`, with `fields=true`, JSON content type and exact fixed UTC
  bounds. A visible `IDOT_RESULT` proves that the request completed; the exact
  request sequence and unchanged config are also checked. This case exercises
  the shrinking lowercase byte offset independently of CLI quoting.
- `sql_unicode_preflight_panic` passes `select '` + twenty `Ⱥ` + `' from ` as the
  CLI SQL. Default-LIMIT insertion leaves 64 original bytes, while the lowercase
  slice start becomes 75. Actual Go exits **2**, with exact first stderr line
  `panic: runtime error: slice bounds out of range [75:64]`. No HTTP occurs.
  The terminal settings remain unchanged; stdout may contain only package-init
  color/cursor-position probes, with no editor, alternate screen or terminal
  setup output. The Go runtime stack dump after that first line is explicitly
  scoped: addresses, stack detail and the candidate's dump tail are not compared.

All three final embedded-fixture cases pass against
`/home/ajs/.cache/pb-phase7/baselines/pb-go` (SHA256
`fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`). Durable
logs are `sql_unicode_schema_raw_bytes-go.log`,
`sql_unicode_placeholder_wire-go.log`, and
`sql_unicode_preflight_panic-go-corrected.log` under the phase7 cache. Raw
stdout/stderr, request/config records are in `sql-unicode-suite-captures/`;
exploratory controls and source/binary provenance are in `sql-unicode-oracle/`.
The concrete SQL-owner handoff is appended to `effects-requests.md` in that cache.
Parent owns final Rust compilation and candidate-suite acceptance.

Earlier failed captures remain intact. Unbracketed typing exposed incomplete
`select 'Ⱥ' from ` to `View`, which independently panicked at `[18:17]`; Bubble Tea
recovered and exited 0. Bracketed paste submits the whole intended SQL and avoids
that separate render-time state. One first preflight assertion rejected an
observed package-init cursor probe; the corrected assertion permits that bounded
probe without accepting UI setup. The stripped capture decoded the dataset bytes
as U+FFFD followed by U+008B and `试`; that text alone did not establish visible
cells. The ANSI/styled-cell investigation below resolves the emitted-byte
contract and demonstrates the replacement-display mismatch. Runtime dump tails
remain scoped; neither changes the exact request or panic-prefix contract.

### Actual invalid-byte sidebar formatting and styled cells

`docs/evidence/phase7/sql-invalid-byte-actual-display.json` records a new bounded
Go PTY control at 120 columns by 35 rows, dark theme, `TERM=xterm-256color`,
`COLORTERM=truecolor`, and `NO_COLOR` unset. It retains the stdout path/hash,
actual read boundaries, byte contexts, source hashes, styled-cell crops and
explicitly labelled observer controls. Raw stdout is
`/home/ajs/.cache/pb-phase7/sql-invalid-display-live/go.stdout`; the invocation
summary is `sql-invalid-display-live.log`. The independent schema fixture checks
pass. No build was run; the separately authorized candidate control is below.

The actual Go path is `QueryModel.View` -> `renderSQLControlsBox` -> `ui.Truncate`
-> body `Style.Render` -> `paneBodyLines`/Lip Gloss joins -> final
`lipgloss.NewStyle().Width(m.width).Render(render)`. At this width `ui.Truncate`
returns the original invalid dataset bytes **`B5 8B E8 AF 95`**. Lip Gloss v1.0.0
uses x/ansi v0.4.5 for width/wrap:

- `ansi.StringWidth` ignores raw `B5` (no printable/UTF8 transition) and `8B`
  (ExecuteAction); only `试` contributes two cells. Thus sidebar width/padding
  uses **2**, whereas two replacements plus `试` have width **4**.
- Final `ansi.Wrap` preserves `B5` with `WriteByte`. Byte `8B` takes ExecuteAction
  and `WriteRune(rune(b[i]))`, becoming **`C2 8B`** and incrementing the wrapping
  word length. This extra wrap contribution can move the right border to a new
  line and change the viewport.
- Actual terminal-emitted value bytes are **`B5 C2 8B E8 AF 95`**, preceded by
  `ESC[38;2;227;227;231m` and followed by `ESC[0m` in the truecolor control. The
  captured line then has 28 spaces before CRLF, with the right border moved by
  wrapping. Both occurrences have these exact value bytes. Original noncolor
  captures contain the same transform.

The phase7 `Terminal` uses surrogateescape then converts undecodable bytes to
U+FFFD before feeding pyte 0.8.2. `B5` becomes one replacement, but `C2 8B` is the
valid nonprinting **U+008B**, not a second replacement. `pyte.Screen.draw` breaks
its current draw call when `wcwidth` is negative, dropping the following `试`
within that text token. Plain stripped text therefore differs from visible cells.

At the last actual pre-exit frame, with the recorded process read boundaries,
one-based **row 7, column 91** contains U+FFFD, foreground `e3e3e7`, default
background, and all style flags false. Columns 92–94 are default-style spaces;
there is no second replacement or `试` there. The evidence preserves surrounding
layout/cursor. One-chunk replay produces the same complete frame as actual reads.
An explicitly labelled control substitutes `EF BF BD EF BF BD E8 AF 95` for just
the actual value bytes: columns 91–94 then contain U+FFFD, U+FFFD, `试`, and its
wide-character stub, all body-colored. It changes 227 styled cells. This is not a
Rust process run and does not recompute Rust padding; it establishes that the
replacement decode cannot stand in for Go formatting. The parent's earlier
`sql-invalid-byte-display.json` compared **unformatted** raw `B5 8B` with two
replacements, a different byte stream.

Minimal SQL-owner/parent contract: retain raw dataset identity for requests;
preserve raw-byte width/padding/wrap behavior for the sidebar before emission;
emit the equivalent of **`B5 C2 8B E8 AF 95`** at that display site. The current
`error::go_string`/`extract_dataset` display string loses both the C1 distinction
and the original width. A local SQL display adapter must address both. Keep
`go_string`'s global per-invalid-byte replacement: it is correct for Go JSON/error
strings. Substituting U+FFFD/U+008B late into a layout computed with two
replacements does not establish matching padding/wrapping/cells. Parent should
gate the narrow fix with actual emitted bytes and full styled-frame comparison.

After parent explicitly made the current prebuilt binary available, one bounded
functional run through the same embedded PTY fixture and pure `Terminal` observer
confirmed the candidate mismatch. No `phase7.py` execute/native helper was used.
`docs/evidence/phase7/sql-invalid-byte-candidate-display.json` records binary
SHA256 `5b14cbd7e1bc3c00ba5d70147b53c07351564de97ce4fabed28e41a2851afb7d`,
verified unchanged before/after the invocation. Actual candidate value bytes are
`EF BF BD EF BF BD E8 AF 95` (two occurrences). Its last pre-exit styled frame
shows U+FFFD/U+FFFD/`试`/wide stub at **row 8, columns 91–94**, foreground
`e3e3e7`, default background, all flags false. Its line retains a right border;
Go's extra wrapping shifted the corresponding value to row 7. Both processes
exit 0 and pass the same raw-byte schema request/result/config contract, isolating
the remaining failure to sidebar display/layout. Candidate raw stdout, read
boundaries and full frame are durable under
`/home/ajs/.cache/pb-phase7/sql-invalid-display-candidate/`; the log is
`sql-invalid-display-candidate.log`. No fix or post-fix validation is claimed.

Scoped observer limitation: byte-by-byte replay of this same capture differs,
because a subsequent draw call after U+008B can render `试`. The evidence stores
that control separately. Expected bytes are deterministic; the stated cells are
tied to actual recorded reads and matching one-chunk replay. Do not generalize
one pyte draw-token boundary to every possible kernel read split or normalize
the C1 mismatch away.

## Final review F1/F3 process controls

Twelve additional ordinary cases in the owned suite pass actual pinned Go.
Current groups are **49 ordinary, four ignored deadlines, four ignored OAuth**.
`docs/evidence/phase7/final-decoder-process-go.json` retains exact duplicate-key
response text, requests, per-case stdout paths/hashes, pin and fixture provenance.
Logs are `<case>-go-final-r1.log`; raw/config/request captures are in
`/home/ajs/.cache/pb-phase7/final-decoder-suite-captures/`. Production edits and
Rust compilation remain with the parent/decoder owner; no new dependencies,
production flags, builds or candidate decoder runs were used here.

- `sql_duplicate_record_merge` verifies the review's exact repeated-record
  document: the rendered row has `a=1,b=2`, not a missing first value.
  `sql_duplicate_fields_reuse` adds one record to the review's empty-record
  example so retained field `a` is observable: null reuses it alongside renamed
  `x`, with both `FIELD_A_REUSED` and `FIELD_X` in the row.
- `sql_duplicate_hidden_slots` shortens and re-expands both fields and records
  inside one document. It verifies `ACTIVE_A/—` and `RESTORED_A/HIDDEN_B`, retaining
  the hidden second map. `sql_duplicate_record_null_reset` verifies that null
  resets a record map before a later duplicate supplies `FINAL_B`.
  `sql_duplicate_first_error` verifies that a numeric field still errors after
  a valid duplicate repairs it; no repaired record is displayed.
- `sql_schema_duplicate_struct_reuse` uses the exact schema
  `{"fields":[{"name":"a"}],"fields":[{}]}`: retained `a` renders and no fallback
  POST occurs. `sql_schema_duplicate_array_reset` instead clears the array with
  `[]`, then supplies `{}`; the empty new name requires exactly one all-time
  `SELECT * FROM 'logs' LIMIT 1` fallback and displays its returned field.
- `promql_label_duplicate_data_reuse` retains `F1_LABEL_A` from a duplicate
  `data:[null]`, then proves the selected label survives into the value GET and
  wire query `up{F1_LABEL_A="F1_VALUE"}`. Fresh value replies select `(any)`, so
  the fixture explicitly moves down before running. The original exploratory
  unselected-value trace (`up{F1_LABEL_A!=""}`) remains retained.
  `promql_label_duplicate_array_reset` proves `[]` discards the old string before
  `[null]`. `promql_label_duplicate_first_error` proves a repaired numeric label
  remains a decode failure. Both run bare `up` without any label-value GET. The
  response-applied `(any)` frame is the input barrier; no blind delay is used.
- `sql_unicode_decoder_keys` folds `fieldſ` and `recordſ` into typed fields and
  displays `F3_FIELD/F3_SQL_ROW`. `promql_unicode_decoder_error` folds `ſtatus`
  and displays `bad_data: F3_RESULT_ERROR`, preserving error handling rather than
  treating the response as empty success.

All cases also check request paths/counts/auth, exact query/range parameters,
terminal restoration and unchanged config. The server response bodies are bytes,
never normal dictionaries that erase duplicate keys. Twelve private acceptance
records plus sixteen earlier exploratory payload controls remain durable. The
additional private schema/label hidden-slot, schema first-error and Unicode
label-error controls are source-owner evidence without expanding the registered
suite into an exhaustive decoder matrix. Exact payloads and owner handoff are in
`/home/ajs/.cache/pb-phase7/final-decoder-effects-contract.md`.

Round10's preserved `integration-alltargets10.log` exposed a fixture race in
`sql_duplicate_record_null_reset`: the correct `—/FINAL_B` row rendered, but
Ctrl+C preceded home completion scheduling the asserted schema GET. The new SQL
decoder group now explicitly waits for that schema request and response before
quitting. Decoder row assertions and exact graph counts are unchanged; no
production defect is inferred. All twelve new cases pass targeted Go and current
debug candidate replay after this causal barrier. Candidate SHA256 stayed
`5945c460e942629c7aaaa995715206ebc56be4eee8c5f27c6df9e268bc7aedd4` before/after.
`docs/evidence/phase7/final-decoder-causal-readiness.json` preserves the 24 replay
records and the original parent failure log path. The test source is frozen again
for central round11; this is targeted functional acceptance, not a full central
suite claim.

The sidebar comparator is now a separate reproducible functional gate:
`/home/ajs/.cache/pb-phase7/sql_sidebar_styled_compare.py GO_BINARY [CANDIDATE_BINARY]`,
run with the cache `harness-venv/bin/python` and all three required tmp variables.
It uses the owned embedded fixture and a frozen copy of the pure phase7
`Terminal`/dependency declarations (`effects_terminal_snapshot.py` plus
provenance); no concurrently edited execute/native helper is imported. Each run
retains raw stdout/stderr, actual read boundaries, complete pre-exit styled frames
and failures under `sql-sidebar-styled-process/`. Go-only acceptance passes in
`sql-sidebar-styled-go-final-r1.log`. Exact value bytes `B5 C2 8B E8 AF 95` are
required and two-replacement bytes are rejected. The candidate gate compares
complete frames for actual reads and separately for one-chunk replay; only
digits in the exact ephemeral fixture URL are normalized, keeping style/layout
cells and character counts intact. The C1 draw-call/read-split limitation remains
explicit. No post-adapter candidate parity is claimed.

## G9 builder and saved clip styled process controls

Two narrowly bounded Go controls pass exact emitted-byte, visible-row,
styled-cell and cursor assertions. They use the owned independent PTY fixture
with dimensions set before spawning and the frozen pure `Terminal` observer;
no execute/native helper, production edit, build, timing campaign or candidate
invocation is involved. `tests/interactive_effects.rs` remains quiescent for the
parent's central checks. These are standalone styled gates, not extra ordinary
Python-dependency requirements for the embedded suite.

`docs/evidence/phase7/builder-saved-byte-clip-process-go.json` records complete
clip rows/styles, cursor, response/request trace, raw and full-frame paths/hashes,
actual read boundaries, Go pin and observer provenance. The replay of each raw
checkpoint in one chunk exactly matches its actual-read full styled frame.

- Builder: dark truecolor **80 columns × 30 rows**, empty PromQL query, explicit
  `metrics` dataset, metadata containing only `abcdefghijkl测试_tail`.
  `builderColWidth=(80-14)/3=22`, `innerW=20`, `maxLen=16`, and the raw byte cut is
  `maxLen-3=13`. Thus Go emits `abcdefghijkl` + **raw `E6`** + `...`, splitting
  the first Chinese rune. Selected metric **row 11, columns 8–19** contains the
  ASCII prefix, column **20** is U+FFFD, and **21–23** are dots. Every value cell
  has foreground `38bdf8`, default background, bold true and all other style flags
  false. The entire visible row is asserted, including neighboring columns and
  borders. The VT cursor is zero-based **(0,29), hidden, shape 0**. The full
  original metric appears in the built expression; no result/label/value request
  occurs. Initial metric-cache scheduling remains bounded to the established
  one-or-two GET policy.
- Saved menu: dark truecolor **40 columns × 20 rows**, one SQL item with title
  `saved_` + **22 `a` characters** + `测试_tail`, query `SELECT 1`, no time
  metadata. `listWidth=40-6=34`, `clipWidth=max(10,34-4)=30`, and the raw cut is
  `clipWidth-1=29`. Go emits the 28-byte ASCII prefix + **raw `E6`** + ellipsis
  `E2 80 A6`. Selected title **row 5, columns 8–35** contains the prefix,
  **36** is U+FFFD and **37** is `…`, with the same active/bold styling. The full
  row is asserted; right border column **40** has foreground `49495c`. VT cursor
  is zero-based **(0,19), hidden, shape 0**. Exactly one filter GET occurs; `b`
  quits without an apply/delete POST or config rewrite.

Both source cuts use UTF8 byte length, not rune or display width. The decoder
turns the clipped initial `E6` into one replacement; these two controls do not
contain the sidebar's valid U+008B that interrupts a pyte draw call. No broad
read-split or palette equivalence is inferred beyond the recorded controls.
Clipping changes the display, while the full original selection identity stays
intact. The parent owns the byte-frame production integration and final candidate
acceptance; current pre-fix output was not used to relax expectations.

Reproducer: cache `harness-venv/bin/python clip_styled_oracle.py GO_BINARY
builder|saved`, with all three required tmp variables. The helper sets truecolor
and unsets `NO_COLOR` only in its process environment. Raw stdout/stderr, complete
checkpoint frames, read boundaries, requests, and earlier exploratory records
are in `/home/ajs/.cache/pb-phase7/clip-styled-process/`; asserted logs are
`clip-styled-builder-go-asserted.log` and `clip-styled-saved-go-asserted.log`.
`clip-styled-contract.md` and `effects-requests.md` contain the concrete integration
handoff. Scope is two dark-theme controls, not a corpus or release verdict.

The three-screen runner is now durable at
`docs/evidence/phase7/styled_effect_controls.py`. Exact release2 invocation lines
are in `docs/evidence/phase7/styled-effect-commands.md`; they need no cache-only
Python script. It loads the embedded fixture and only pure `Terminal` declarations,
frozen per invocation, never the execute/native helper. All three Go-only modes
pass in `styled-durable-go/`. It retains failed runs, requires unchanged executable
hashes, strict raw bytes and complete frame comparisons, and explicitly records
the sidebar C1/read-boundary limitation. Release2 styled invocations wait for the
native-helper freeze announcement; no result is claimed before that gate.

## L-STYLE-1 single reachable CLI control and observer limit

One actual Go saved-menu title probe uses the exact helper input
`x\x1b]0;t✅y z`, at dark truecolor 40×20, with query `SELECT 1`. Its 12 UTF8 bytes
fit the 30-byte item clip width, so this is not a clip-induced invalid string.
The fixture performs one filter GET, quits with `b`, exits 0, preserves config
and restores termios. No candidate, extra C1 cases or production changes were run.

Go really emits title bytes `78 1B 5D 30 3B 74 E2 9C C2 85 79 20 7A`: partial
`E2 9C` replaces the original valid `E2 9C 85` checkmark sequence. x/ansi's
OSC parser consumes raw `9C` as C1 ST inside the rune, then `Wrap` re-encodes the
remaining raw `85` via ExecuteAction/WriteRune as U+0085 (`C2 85`). The specific
helper limit 3 is not reached by this row width, but the same byte transform is
reachable during the actual positive-width rendering path without that limit.

The phase7 observer supports BEL or ESC-backslash OSC termination, not this
malformed C1-ST byte sequence. The literal input also has no explicit supported
OSC terminator. Its live parser therefore buffers the remainder of the screen;
final replay reports `incomplete terminal sequence`. The supported prefix has
an active/bold `x` at row 5/column 8, foreground `38bdf8`, default background,
and cursor zero-based (8,4), hidden, shape 0. Those are explicitly a **partial
prefix**, not expected final screen/cursor cells. Real-terminal consequences and
string-facade styled equivalence remain unknown. This evidence rules out treating
the byte transform as unreachable; it does not justify normalizing it, changing
all facades, or claiming supported-cell parity.

`docs/evidence/phase7/style-c1-st-saved-process-go.json` retains exact input/output
contexts, requests, process/pin hashes, partial frame path, read boundaries and
the observer rejection. Full raw stdout/stderr and the one probe are preserved
under `/home/ajs/.cache/pb-phase7/style-c1-st-cli/`, with
`style-c1-st-cli-go.log` and private reproducer `style_c1_st_cli_oracle.py`.

## Remaining effect gates

This suite checks effects and selected visible content, not fixed-size terminal
cell parity, Unicode editing, resize, palette equivalence, or scrollback. Those
belong to the separately owned harness. The ANSI stripping used to locate content
does not normalize request/config/error contracts. The explicit alternate-screen
ordering check applies to saved-query execution.

Timeout policy is covered by the separate opt-in gate; header-wait expiry does not
establish every body-read/refresh/redirect deadline combination. Builder cache reuse
after revisiting prior metrics/labels, more complex cache-eviction navigation, and
race permutations beyond the explicitly gated schedules remain additional cases.
Exact Go timeout wording remains the scoped unknown above. Login/device
authorization is outside this test agent's implemented cases. No remote-account,
cross-platform, release, benchmark, or profiler result is claimed.
