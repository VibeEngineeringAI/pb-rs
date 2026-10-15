# A1retry02 startup request fan-out proposal

Status: concrete off-tree successor, tested against retained captures; **not applied,
not accepted, and not a replacement preflight**. The four dense readiness failures
remain unresolved by this patch. Root owns combining this change with the separate
dense-table readiness proposal and reviewing the resulting source.

The only worktree write in this lane is this new document. Source, tests, accepted
styled workload definitions, historical metadata, and original captures were not
edited. No Go/Rust command, CLI, PTY, native helper, probe, compiler, build,
performance campaign, common lock, or nested agent was run. Work paused with zero
active processes on request and resumed only after the explicit RELEASE.

## Concrete deliverables

All off-tree artifacts live in
`/home/ajs/.cache/pb-phase7/a1retry-request-fanout-proposal/`:

- `request-fanout.patch`: bench-only source patch, SHA256
  `2d97ed82084e13930938fd3ee8cbe40c843052ce6f2a038c129776ae31fa87ce`.
- `phase7_bench.proposed.py`: complete proposed source for inspection; base
  `harness/phase7_bench.py` SHA256
  `e03e1d2bfce45d7123b67a1870e8e3e38c7d5a6ae92c99881c513f4965846c01`.
- `test_request_fanout.py`, `focused-tests-01.log`, and
  `focused-test-result.json`: 13 pure focused tests passed, zero errors/failures.
- `retained-a1retry02-analysis.json`, `retained-analysis-01.log`: all 12 actual
  cases / 24 roles, original and prospective independent/comparison results.
- `archived-go-aa-order-proof.json`, `source-oracle-bindings.json`: actual
  same-Go request-order proof and binding to current/pinned source.
- `authoritative-case-hash-map.json`, `prospective-preflight-hashes.json`,
  `bindings-02.log`: exact before/after maps for 52 cases and direct construction
  for each of the 90 recorded CLI argument sets.
- `original-manifest-before.json`, `original-manifest-after.json`, and
  `final-integrity.json`: original report/capture preservation and final scope
  checks. `patch-binding.json` and `final-artifact-bindings.json` bind proposal
  source, patch, scripts, tests, logs, and this document.
- `bindings-01.log` preserves a failed setup attempt: the initial local runner
  was named `run.py` and shadowed the harness module. It was renamed to
  `offline_runner.py`, then the same checks passed. `inspection-failures.log`
  records the earlier read-only report-shape mistake. Neither failure launched
  a child or changed evidence.

The tests deliberately remain off-tree. No live test file or comparator is edited.

## Actual failure and root cause

The original report is
`/home/ajs/.cache/pb-phase7/under30-combined-a1-primary-dark-02/report.json`,
SHA256 `1dcd24f6561568feabf83c5fc3c6cdfada4d30afcf3d56a9e23a5e2709678dd4`.
Its historical disposition remains invalid: seven SQL cases passed; four dense
PromQL cases timed out waiting for the table-view phase; the PromQL query-error
case failed only `ordered request contract differs`.

The error reference and candidate each issued exactly three requests. Go arrival
order was `query_range → home → names`; Rust was `query_range → names → home`.
Full existing request-contract objects agree after permuting those three entries:
method, complete target/query parameters, contractual auth/headers, parsed body,
and unexpected-request flags are unchanged. The error query uses its original
`step=1m`; dense queries retain `step=60s`. No numeric or query normalization was
introduced. Both error roles independently exited 0, restored the terminal, and
retained the expected error display/config state.

All four dense cases also issued exactly the same three startup requests per
role. Dense-1 and dense-100 happened to agree in arrival order. Dense-1000
reference was `query_range → home → names`, while candidate was
`names → home → query_range`. Sustained dense-1000 reference was
`query_range → names → home`, while candidate was `query_range → home → names`.
Together with PromQL query-error, these are three observed startup order
mismatches in the historical report.
Every dense role reached `startup` and `setup-ready`, then timed out at table-view
and was killed without graceful restoration. Removing startup-order sensitivity
does not remove those failures.

`corpus()` already attaches narrowly scoped startup metadata. Existing
`ordered_request_contract()` sorts only the declared initial prefix, and
`validate()` checks exact membership/multiplicity and dependencies before
comparison. The benchmark's `sql_case()` already declares the SQL graph.
`dense_case()`, `editor_case()`, and `error_case()` omit the corresponding
metadata. This construction gap, rather than a different request comparator,
causes the error-case mismatch and the extra dense-1000 mismatch.

## Go oracle evidence and source scope

Two archived **Go reference** sessions provide the exact counterexample without
running Go again:

- `/home/ajs/.cache/pb-phase7/styled-release5/p7-promql-table-chart-unwrap-light/reference/session.json`,
  SHA256 `40a062d39b14cbe317bf4742520e7fb86d17776098b6e5b9728303da028cc71d`:
  `query_range → names → home`.
- `/home/ajs/.cache/pb-phase7/styled-release6-combined/p7-promql-table-chart-unwrap-light/reference/session.json`,
  SHA256 `4041dfd1fde4afb8033aeac28243013b328e4793e7aca694339d68430badab08`:
  `query_range → home → names`.

These sessions have exactly equal case definitions, argv, fixtures, config
specifications, and binary identities, not merely similar endpoint lists. Both
use pinned Go SHA256
`fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`,
the actual A1retry02 reference binary. Each passes current independent functional
validation, has exactly three requests, and has all three receipts before first
input. Their raw request contracts differ; their existing accepted startup
contracts are equal. This is request-order evidence, not a new styled-screen
acceptance claim.

The existing contract in `harness/contracts/phase7.md`, “Normalization and effect
ordering,” already describes this explicit-dataset graph and the distinct
implicit-dataset URL dependency. `docs/phase7-harness.md` records the earlier SQL
Go A/A discovery; `docs/phase7-review-final-contract-sol-r3.md` preserves the same
bounded startup ordering scope. Relevant documents were read in full and copied
under the off-tree `read-docs/` directory for review.

Current Go `pkg/model/promql.go:466` batches the initial result, metric-name cache,
and dataset discovery commands. Bubble Tea v1.2.2 `commands.go:15` returns a
`BatchMsg`; `tea.go:414` dispatches it and `tea.go:297` runs commands in independent
goroutines. There is no home-before-names dependency for a configured metrics
dataset. Rust `src/tui/promql.rs:3261` creates the same three effects and dispatches
them as tasks. Go's `datasetListMsg` and Rust's `Completion::Datasets` only create
the auto-selected cache/query effects for absent/placeholder selection. This
patch never applies the explicit-dataset graph to that flow.

Default SQL is different: Go `pkg/model/query.go:456` batches discovery and the
optional initial query, then discovery leads to schema loading. Rust
`src/tui/sql.rs:2565` has the same split. SQL therefore retains `home → schema`;
only its initial query can commute with that chain. Current Go/Rust model source
hashes were checked against the actual A1retry02 report source maps; exact hashes
and pinned Bubble Tea file identities are in `source-oracle-bindings.json`.

## Exact proposed changes

`startup_fanout()` reuses the accepted metadata shapes, verified for equality
against the current functional corpus. It is called only by these constructors:

- Dense-1, dense-100, dense-1000, sustained dense-1000, and PromQL query-error:
  three members, `GET home`, `GET metric names`, `GET query_range`, with the
  explicit `--dataset metrics` already present in each existing argv.
- SQL editor: two members, `GET home`, `GET logs/schema`, constrained home first.
  This adds independent membership/dependency checking; it admits no additional
  ordering for that two-request chain.
- SQL query-error: three members, `GET home`, `GET logs/schema`, `POST query`,
  constrained home before schema, matching the already-declared windowed SQL
  graph and the accepted functional SQL error flow.

Windowed, delayed, sustained, and long-idle SQL constructors already have the
correct metadata and remain byte-for-byte equal as case definitions. No implicit
PromQL, instant/both mode, builder, rerun, or arbitrary argv inference is added.
The existing accepted corpus and comparator are unchanged.

The independent `startup_fanout_issues()` check uses **already recorded** dispatch
and fixture receipt timestamps; it records nothing new. It requires exactly the
declared prefix, with unique complete finite receipt evidence, strictly before
the first actual input/resize/signal dispatch. An equal timestamp is insufficient.
Missing dispatch provenance, a prefix request delayed until input, or any extra
pre-input request fails closed. Later requests remain outside the prefix and are
still compared in their full original order. This gate also checks existing SQL
fan-out declarations; all 24 actual roles pass it. The smallest retained margin
between last startup receipt and first input is 198,632,844 ns, descriptive
causality evidence only.

No request is removed or deduplicated. Raw arrays/captures are immutable. Auth,
body, target/query, repetition count, later ordering, and SQL/implicit-dataset
dependencies retain the existing contract treatment. Observer policies, action
scripts, readiness predicates, fixtures, timeout values/types, phase definitions,
measurement logic, and native helper configuration are unchanged.

## Validation and prospective bindings

All 12 historical case hashes and 24 stored case definitions were first matched
to the current baseline builders using the actual CLI-typed primary options.
Current independent observation results are identical before/after for every
role. The prospective request comparison removes only the three observed startup
order mismatches: dense-1000, sustained dense-1000, and PromQL query-error. The derived result is eight passing cases and the same four dense
failures. That derived count is not an admitted preflight and does not alter the
historical report's seven passing cases.

The 13 focused tests cover actual error captures; all six allowed explicit
PromQL startup permutations; all three valid and all three invalid SQL startup
permutations; ordered later effects and repetitions; missing/duplicate/wrong
members; auth/body/query/method/unexpected differences; delayed or equal-time
startup receipts; missing/duplicate/nonfinite receipt provenance; absent dispatch;
and an identical extra pre-input effect in both roles. The latter is rejected by
the independent boundary gate even when pairwise equality alone would agree.
The four actual dense timeout/lifecycle failures remain asserted as failures.

The authoritative binding input is the absolute retained
`/home/ajs/.cache/pb-phase7/postidle-bracket-applied/direct-typed-bindings.json`.
The current parser prefix is executed without mode dispatch. Each of its 90
recorded argv sets constructs its own before/after cases; pool-plan rows use the
current function's exact base-Options assignment. All original typed options and
baseline case hashes match. Supplied float options stay floats; omitted integer
defaults stay integers. No invented `60`/`60.0` or other numeric normalization is
used.

Within the full 52-case primary/resource-attribution dark/light profiles, exactly
28 case identities change: seven affected cases per theme/policy. Twenty-four
case definitions remain equal. The complete authoritative before/after map and
all 90 own-argv records are in `authoritative-case-hash-map.json`; the compact
expected successor maps are in `prospective-preflight-hashes.json`.

**Prospective rebinding is required.** The source freeze and full 52-case binding
package need a successor even though only 28 definition hashes change. Root must
recompute the final combined source, case maps, own-argv matrix/ledger/metadata
bindings, and fresh actual preflights after integrating the separate dense fix.
These off-tree maps are not valid for a later combined source. Historical maps,
reports, admission records, source snapshots, and accepted styled evidence must
remain historical and byte-preserved.

## Separate genuine Go page-down control

No fresh Go control is needed to establish startup commutativity. Page-down is a
separate unproven behavior here: 89 retained Go PromQL reference sessions from
styled releases5/6-combined and A1retry02 contain **zero dispatched PgDown byte
sequences**. `retained-page-down-search.json` binds every searched session. This
is a scoped absence finding, not a whole-cache claim; A1retry02 sustained dense
never reached its scrolling actions.

Root can separately prepare two actual pinned-Go sessions with new output paths,
using the unchanged dense-1 256-point fixture, 120×40 dark primary profile,
explicit metrics dataset, fixed range/step/auth, and the existing focus sequence
`Ctrl+Q`, `Shift+Tab`, `t`. Record a stable genuine table frame before navigation,
send exactly one `page-down` key (`ESC [ 6 ~`, hex `1b5b367e`), record the next
stable table frame, then `home`, record the restored first-row frame, and quit
with Ctrl+C. Require fresh output after each dispatched navigation; retain full
styled cells, footer/selected-row evidence, all raw bytes, and dispatch/receipt
provenance. Startup must still have exactly the three allowed requests; no
navigation-triggered extra query is allowed. Require exit0/restoration/config
integrity and the usual bounded-run cleanup/identity gates for each session.

This choice of key is source-grounded: `pkg/model/tablekeymap.go:68` binds
`pgdown`; `pkg/model/promql.go:1122` forwards results-pane keys to the table;
pinned bubble-table v0.19.2 `table/update.go:87` calls `pageDown()`.
`page-down-source-bindings.json` binds those exact files. Page size depends on
the actual rendered table height (`promql.go:1301`) and bottom-bar height, so no
guessed numeric page step is an acceptance predicate. The control must observe
the Go footer/highlight transition and repeat it in the second Go session before
root declares an exact numerical benchmark navigation contract. Before/after
selection must visibly advance and Home must restore selection; a mere output
change is insufficient. If a reviewed table-readiness predicate is not yet
available, keep this proposed root control pending rather than relaxing the
benchmark or inventing a row step.

Proposed separate root bound: two sessions with at most 10 seconds of observation
per session and a 120-second outer execution/cleanup envelope, under root's own
fresh coordination and existing bounded driver. A timeout is retained as failed;
do not retry or widen silently. This lane has not launched or authorized that
control, touched a quiet token, or used a lock. Its exact prepared case/source
binding must be reviewed separately from this fan-out patch.


## Opus r1 implementation follow-up

The full `docs/phase7-review-a1-fanout-opus-r1.md` was read. R1's factual prose is
corrected above: three order mismatches, including sustained dense-1000 Go
`query_range → names → home` versus Rust `query_range → home → names`. The
original proposal, reports, patch, scripts, logs, and pre-correction document are
preserved byte-for-byte in the new sibling
`/home/ajs/.cache/pb-phase7/a1retry-request-fanout-r1-fixes/preserved/`.
Original off-tree files remain unchanged.

R2 is implemented in the durable off-tree `test_observation_wiring.py` in that
sibling. Both tests call actual `observation_issues`, with only `p7.validate`
mocked as in the existing bench test helper. They first establish passing
unmodified retained controls, then prove that `p7.compare` alone returns `[]`
for a late home receipt and for an identical extra pre-input request in both
roles. Both altered roles must be rejected by the observation gate. The reviewed
source passes both tests. A compiled Python mutant removing only
`issues.extend(startup_fanout_issues(result, case))` fails both tests specifically
at those rejection assertions, with zero test errors. Logs and full assertion
traces are retained in `reviewed-baseline-tests.log` and
`wiring-removal-mutant-tests.log`; `wiring-regression-results.json` binds the
sources and outcomes. These are additional focused tests, not a rerun of the
original 13 tests or the full suite.

The reviewed proposed source and patch are frozen at their original hashes;
no N3 argv hardening or N4 request-index type change was made. The patch remains
unapplied. R3 remains a required parent step: execute the existing complete bench
suite against the final source after composing fan-out and geometry changes.
This implementation follow-up does not satisfy or preempt R3 and is not a new
review or runtime acceptance.

N1: a legitimate startup receipt arriving at or after first input makes that
capture invalid. Retain that failure; do not hide a retry, silently retry, widen
the boundary, or carry over passing evidence. The boundary rejects conservatively
and does not make screen readiness wait for every startup request.

N2: the 24 unchanged SQL-windowed case hashes still receive stricter acceptance
through the observation gate. Definition equality does not carry acceptance
forward. All 52 cases require the final full-source binding and fresh applicable
evidence after composition, even where individual case hashes are unchanged.
