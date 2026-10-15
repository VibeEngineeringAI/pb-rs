# Phase 7 A1 composition proposal: dense geometry + startup fan-out (off-tree, R1)

**Status:** off-tree preparation only. This is **not applied, not reviewed as a composition, and not acceptance**.
- The live bench, tests and docs are unchanged.
- Copying the post-images and fixtures into the live tree is root-owned.
- **Sequencing:** the reviewed Go page-navigation runner (`go_pagenav_runner.py` `afd54e06…`) is pinned to the current unpatched tree (geometry review L1). Root runs that control **before** any live composition. This lane did not edit or invoke the runner.
- No page step (15 or otherwise) is adopted.

All artifacts are in `/home/ajs/.cache/pb-phase7/a1-geometry-fanout-composition-r1/`.

**No processes or side effects:**
- no child, native session, CLI, helper, probe, compiler, build or network;
- no common lock, Git or commit;
- no nested agent.

## Inputs (pinned; unchanged before and after)

| Input | sha256 |
| --- | --- |
| reviewed geometry final patch (`promql-geometry-implementation-r1/final/promql-geometry.patch`) | `bbdd22270629eb2c9db5b7f29aba87cf88232b0215f200fecc5fdefc27c6a486` |
| its reviewed bench post-image | `4262df14…00fe` |
| its reviewed test post-image | `a5eb4be8…a689` |
| its reviewed contract post-image | `8c157103…b0f4` |
| its reviewed measurement post-image | `67633576…7729` |
| `promql_geometry.py` | `a6b11432…08519` |
| geometry fixture | `12a14654…267b6b` |
| reviewed fan-out source patch (`a1retry-request-fanout-proposal/request-fanout.patch`) | `2d97ed82084e13930938fd3ee8cbe40c843052ce6f2a038c129776ae31fa87ce` |
| its full source `phase7_bench.proposed.py` | `5041f07d…4b84` |
| Sol's authoritative map | `076d10c2…3388` |
| typed-bindings origin `postidle-bracket-applied/direct-typed-bindings.json` | `8a205017…c4e8ba` |
| A1-retry `report.json` | `1dcd24f6…78dd4` |
| live `phase7_bench.py` | `e03e1d2b…6c01` |
| live `test_phase7_bench.py` | `244bd6e4…845c` |
| live `contracts/phase7.md` | `19f4412a…3006` |
| live `phase7-measurement.md` | `b9c6b991…6313` |
| live `phase7.py` | `dc7574f0…44fa` |
| live workloads | `04404c96…6178` |
| fan-out proposal doc | `2b4de8f9…afcc` |
| fan-out review | `ef8e9cac…8892` |

**Documents read in full:**
- `docs/phase7-review-promql-geometry-final-opus-r1.md` (no blocker or medium; L1–L7);
- `docs/phase7-review-a1-fanout-opus-r1.md`;
- `docs/evidence/phase7/a1-fanout-review-disposition.json`;
- the corrected fan-out proposal doc;
- Sol's `test_request_fanout.py`, `bindings.py` and `proposal_support.py`;
- the R1 fixes `implement_r1.py`, `test_observation_wiring.py` and `r1-prose-correction.json`.

**Preservation:** the prior trees were byte-identical before and after (manifest equality):
- `promql-geometry-implementation-r1`: 90 files;
- `a1retry-request-fanout-proposal`: 59 files;
- `a1retry-request-fanout-r1-fixes`: 74 files.

## Composition (`composition.patch`, sha256 `eb9fe2fd9b5dceed18bef4059b009087dee052423ccde3c0bde652823576ff0e`)

The patch is a live → `proposed/` unified diff of 4 text files (360 lines). It adds two binary fixtures, listed separately below.

| Post-image `proposed/…` | sha256 |
| --- | --- |
| `harness/phase7_bench.py` | `9bbab67c8c6fa89aff5c84729717f5049b515c4c4ec1b4bffd73f49c1f901239` |
| `harness/test_phase7_bench.py` | `ba2a8152d8f625e74e75caf6f9c93e1e497aeefb7bf13108a0785d06a8db5776` |
| `harness/contracts/phase7.md` | `67f241d0cfca8fdab1fd461940a53377bb4cbd55e4a1425a10481a23bc18a874` |
| `docs/phase7-measurement.md` | `b51e891f4457b6256cf2f1012db0c6e6546cbc0d7ebbc98679a390cbc93a3b40` |
| `docs/evidence/phase7/a1retry-promql-retained.json.gz` (byte copy of the reviewed fixture) | `12a14654697b1c7b10f328d998382eddf2fb0e67c662fbc62f37ff30ff267b6b` |
| `docs/evidence/phase7/a1retry-fanout-observation-pairs.json.gz` (new, 96,528 bytes) | `05c0ca626ad8f1adb00f4c583d222f23b03fcd1ca8cd9cdb978e1d96da69e2ce` |

### Bench: semantic union only (`ast-union-audit.json`)

**How the post-image is built:** it is Sol's reviewed proposed source with `dense_table_geometry` replaced by the reviewed geometry function. That function is source-identical to `promql_geometry.py`, which the runner swaps in memory.

**AST audit**, top-level definitions compared with the live base:

| Version | Changed definitions |
| --- | --- |
| fan-out | `startup_fanout` (new), `dense_case`, `editor_case`, `error_case`, `startup_fanout_issues` (new), `observation_issues` |
| geometry | `dense_table_geometry` |
| composed | exactly the union of the two, which are disjoint |

**What else the audit confirms:**
- Each fan-out definition in the composed source is AST-equal to Sol's, and the geometry definition is AST-equal to the reviewed geometry version.
- Every other definition equals the base. That includes `main` and `pool_definition`, so the CLI parser and the pool base are unchanged.
- All non-definition statements are identical across all four versions.

**Text check:** the unified-diff hunk bodies from base to composed equal the union of the two reviewed patch files' hunks, comparing `request-fanout.patch` and the bench section of `bbdd2227…`. They also equal the same-tool `difflib` union.

**Nothing beyond the two reviewed patches:**
- no parser, lifecycle or readiness broadening;
- no live-path `IndexError` handling;
- no page-step change;
- no N3/N4 hardening;
- no runner edit.

### Tests: reviewed geometry tests plus durable R2 fan-out tests

- **Base:** the post-image is the reviewed geometry test post-image `a5eb4be8…` (the D1-fixed R5 synthetic test and `A1RetryPromqlGeometryTests`).
- **Addition:** one new class, `A1RetryFanoutObservationTests`. The test AST relative to the reviewed geometry post-image changes only that class. Relative to live, only `R5ObserverTests`, `A1RetryPromqlGeometryTests` and the new class change.
- **R2 is now durable in-tree, not a cache import:**
  - The two R1-fixes `observation_issues` integration tests are carried over with identical semantics: a late `home` receipt on the PromQL query-error pair, and an identical extra pre-input `home` on the SQL editor pair.
  - Each test first asserts that the unmodified retained pair passes. It then asserts that `p7.compare` alone returns `[]`, and finally that `observation_issues` (with only `p7.validate` mocked) returns `['initial fan-out is not the exact pre-input request prefix']` for both roles.
  - The fixture holds the exact retained `session.json` text of the four roles. It is bound to the report's `session_sha256` values and decoded exactly as `p7.load_session` does. The metadata comes from `bench.startup_fanout`.
- **Sol's 13 focused fan-out tests:** they stay in his reviewed off-tree file and were not imported. Root's full suite is still required (fan-out R3).

## Targeted guarded tests (one primary attempt; no retry needed)

**Run:** `compose.py` (`cguard.py` installed first) under `harness-venv/bin/python -E -s -B` with `timeout 580` and a 540 s alarm.
- **Result:** exit 0, empty stderr, 79.5 s, zero guard trips.
- **Guard:** it blocks processes, exec, PTY, sockets and flock, plus any write outside the composition directory.

**What did not run:**
- the exhaustive 5,397-prefix scan, whose evidence is preserved in the geometry lane;
- the whole bench suite (the guard baseline remains 18 failures plus 1 error; the root suite is pending).

**Results**, using the composed test post-image bound to each module:

| Module bound | Tests | Result |
| --- | --- | --- |
| **composed** | 2 geometry actual-frame/control tests + R5 synthetic + 2 fan-out wiring tests | **5/5 pass** (8.0 s) |
| composed minus `issues.extend(startup_fanout_issues(result, case))` (AST: only `observation_issues` differs) | 2 fan-out wiring tests | 2 assertion failures (`observation_issues must reject …`), 0 errors |
| composed with the historical `dense_table_geometry` (AST: only that definition differs) | 3 geometry tests | all fail (actual frames ×8, `old-H-6-layout` ×8, R5), 0 errors |

## Case identity map (`composed-case-hash-map.json` `0b54725c…7739`)

**Method:** Sol's reviewed `bindings.py` method was re-executed against the composed module.
- The live CLI parser prefix is used; its AST sha256 equals the origin's `parser_prefix_ast_sha256`.
- For each of the **90 own-argv records**, the typed options and actual builder options equal the origin. The base `campaign_cases` reproduces each record's own historical case hashes.
- The composed `campaign_cases` is compared case by case with Sol's reviewed `after` map, on the after hash, changed flag, fixture hash, typed timeout and `initial_fanout_*` metadata.
- In addition: each changed case equals the base once the metadata is stripped, and fixture, actions and bench are equal.

**Result:** **0 mismatches**. The composed profiles equal Sol's `prospective-preflight-hashes.json` for both the primary and resource-attribution profiles.
- **52 cases: 28 change and 24 are unchanged, as before.**
- **Changed:** the 28 are dense-1, dense-100, dense-1000, dense-1000-sustained, sql-editor, sql-query-error and promql-query-error, each in dark and light, for both primary and resource-attribution. Only the `initial_fanout_*` keys change.
- **Unchanged:** the 24 `sql-windowed` cases (scroll 500/1500/20000, delayed, sustained, long-idle).

The geometry change contributes no case-data difference. The original 28-change map and every historical map, report and binding remain unchanged and historical. **Root must regenerate** the full-source freeze, the 52-case and 90-argv bindings and fresh actual preflights after live composition (fan-out N2). This map is a prospective template only.

## Documents: overlaps resolved explicitly

**Rewording:** only the reviewed geometry's additive paragraphs are re-worded, in the contract after the SQL drawer paragraph and in the measurement doc's new section. Historical text is not rewritten; it is explicitly superseded. The superseded reviewed text is retained verbatim in `ast-union-audit.json` → `docs`.

| Item | Resolution |
| --- | --- |
| **L5** | The measurement doc now says that only the pure replay test catches the pyte wide-char stub `IndexError`. The live capture path does not, so a live read on that prefix would still abort; this is pre-existing, unchanged and not observed in A1. No live-path fix is made. |
| **L6** | Contract and measurement now declare the geometry **only for the 120-column dense profile** (the only dense width in the 52 cases). Other widths fail closed (timeout), never pass. No code width guard is added (no source broadening). |
| **L7** | The contract states the **complete** H−3 hint row with its exact full-match pattern. Both docs explicitly supersede the historical H−6 statements (contract dense R5 line; measurement R5-L3) and the "PromQL `dense_table_geometry` is unchanged" statements. |
| **Overlap** | The reviewed geometry text said case identities were unchanged. The composed text says the geometry alone changes no case data, and that composition with the reviewed fan-out metadata changes 28 of 52 definitions (`initial_fanout_*` only). The cross-reference is `docs/phase7-a1retry-request-fanout-proposal.md`. |

Fan-out adds no contract or measurement text beyond that cross-reference, consistent with its reviewed bench-only scope. The fan-out proposal doc (R1 already corrected) is not touched.

## Failed attempts and environment

- **No compose attempt failed;** no retry was used.
- **Claude temp quota:** Claude's own task-output directory in `/tmp` returned EDQUOT for several shell calls at the start of this lane. The `/tmp` quota is consumed by directories not owned by this lane (for example `/tmp/pr14-review` at 13G and `/tmp/pr15-review-full-target`), and nothing was deleted or moved.
  - I made the one bounded redirect attempt, inspected disk usage once (`disk-inspect.txt`), and from then on redirected every command's output to files in the composition directory, read back with file tools.
  - No command's effect was lost. All logs are retained: `survey-1.txt`, `survey-2.txt`, `compose-01-stdout.txt`, `compose-01-stderr.txt` (empty), `compose-01-exit.txt` and `tests-*.txt`.

## Ready for

A fresh independent composition review is ready, **after** root's actual Go page-navigation control and its review. After that come the root-owned live copy, root's full suite (fan-out R3) and fresh actual 24-role preflight, with full rebinding. No acceptance is claimed here. Hashes of all lane files, including this document's predecessor inputs, are in `freeze-hashes.txt` in the composition directory.
