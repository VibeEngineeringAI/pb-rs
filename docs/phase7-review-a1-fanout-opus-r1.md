# Review: A1retry02 startup request fan-out proposal (Opus r1)

Scope: a fresh, independent, narrow review of
`docs/phase7-a1retry-request-fanout-proposal.md` and the off-tree patch
`/home/ajs/.cache/pb-phase7/a1retry-request-fanout-proposal/request-fanout.patch`.
Nothing here is runtime acceptance. The patch is not applied, there is no
preflight admission, and A1retry02 stays historically invalid. Dense footer
geometry, the control runner, and the Go page-down control proposal belong to
other lanes; they were not reviewed or touched here.

**Disposition:** I accept the source change as a narrow, bench-only
correction, with **two required items before it is applied** (R1 and R2 below),
a **required root step during integration** (R3), and **non-blocking notes**
(N1–N4). Nothing I found makes the gate hide a later effect, a duplicate, a wrong
auth/query/body/method, an unexpected request, or a dependency violation. Nothing
extends the implicit-dataset graph.

## What I executed vs. what was source-only

Executed: three guarded pure Python scripts in my own directory
`/home/ajs/.cache/pb-phase7/a1-fanout-review-opus-r1/`. They ran under the harness
venv `python -I -S -B` with an audit hook and monkeypatches. These denied
subprocess/socket/ctypes/pty/fork/exec/spawn/kill/flock and any write or mutation
outside my directory, plus a 100 s alarm and a 120 s outer `timeout`. Each script
ran exactly once (one primary attempt). All exited with rc=0, so no retry was
needed or made. No Go/Rust/CLI/native helper/compiler/build/probe/profile/lock/
Git-mutation/network was used. Logs went only to my directory.

| Script (sha256) | Log / output | Result |
|---|---|---|
| `rv_review.py` `cc8a17e2…46a5` | `review-01.log` `ac3afc65…d28b`, `review-analysis.json` `5c916e3d…2672` | 12 cases / 24 roles re-derived from raw sessions; Go A/A; 13 own negative tests, 13 OK |
| `rv_hashes.py` `8375ce1f…0b26` | `hashes-01.log` `20858208…d59d`, `review-hashes.json` `20d68da9…89ee` | 52-case rebuild, before and after |
| `rv_wiring.py` `a9790268…3214` | `wiring-01.log` `2623ae33…f5ff`, `review-wiring.json` `30c49373…26ca` | gate wiring and a wiring-removal mutation |

Source-only (not executed by me): the proposal's own 13 focused tests (I read
`test_request_fanout.py` but did not run it from the proposal's directory); the
existing `harness/test_phase7_bench.py` suite (119 tests) against the proposed
source; independent recomputation of all 90 own-argv records; the Go/Rust/Bubble
Tea effect source (read, not built or run).

## Identities (before = after)

- Base `harness/phase7_bench.py` `e03e1d2b…6c01`: unchanged.
- Patch `2d97ed82…ce6f2a038c129776ae31fa87ce`: matches. I independently
  recomputed `diff -u base proposed.py`, and its hunks equal the patch hunks
  (`recomputed.diff` `f57ef19e…3422`). `phase7_bench.proposed.py` is `5041f07d…4b84`.
- `harness/phase7.py` `dc7574f0…44fa`, `harness/phase7_workloads.py`, and
  `harness/contracts/phase7.md` `19f4412a…3006`: unchanged.
- Original report `1dcd24f6…78dd4`: unchanged. All 24 role sessions were loaded
  through `retained_sample`, which checks the recorded session/stdout/stderr
  SHA256 values.
- Every file in the proposal directory is byte-identical before and after
  (`before-identities.txt` `22b00eff…22be` vs `after-identities.txt` `af83f476…4868`).
- One line differs: the `git status --porcelain` digest. It changed because
  other lanes wrote these files concurrently, not because of anything I did:
  `docs/phase7-combined-status.md`,
  `docs/phase7-shared-rss-investigation.md`,
  `docs/phase7-review-shared-confirmations-sol-r1.md`, and five
  `docs/evidence/phase7/shared-*`/`resume-task-ledger.json` files. My only
  worktree write is this document.
- Zero processes from me remain active.

## Verified claims (executed, from raw evidence)

1. **Historical failure reproduced exactly.** Recomputing with the base source
   gives seven SQL passes and four dense `session timeout`/SIGKILL/termios/
   alt-screen failures. `promql-query-error` fails only on `ordered request
   contract differs`. Go roles use pb-go `fae8411f…2314`; Rust roles use
   release6-combined `6d80054f…8443`.
2. **Prospective change is limited to request order.** Under the proposed
   metadata, `compare` changes only by losing `ordered request contract differs`.
   That happens on `promql-query-error`, `promql-dense-1000` and
   `promql-dense-1000-sustained`; see objection R1. For every role,
   `observation_issues` is identical before and after (zero added, zero removed).
   The derived count is 8 passing cases. All four dense failures remain with
   identical issue lists.
3. **Boundary facts match the proposal for all 24 roles.**
   - My re-derived pre-input margins and gate results are equal to the proposal's
     values: `mismatch_vs_proposal = []`.
   - In every role, the pre-input request set is exactly the index prefix
     `[0..count)`.
   - The minimum margin is 198,632,844 ns (`sql-scroll-20000-sustained`, Go).
   - Every startup member was received *before the startup checkpoint's first
     qualifying screen*, by 5.2 ms to 1210 ms. The tightest is the Rust editor
     schema at 5.2 ms.
4. **Go A/A holds, independently checked.**
   - The `styled-release5` (`40a062d3…badab08`) and `styled-release6-combined`
     (`4041dfd1…ed02b`) reference sessions have entire stored case dicts equal
     (digest `ced56e45…7440`) and an equal binary (pb-go `fae8411f…`).
   - Raw orders are `query_range→names→home` and `query_range→home→names`. The raw
     request contracts differ; the ordered contracts are equal; `compare` gives `[]`.
   - All three receipts precede first input (265 ms and 255 ms margins), and
     `validate` gives `[]` for both.
   - The stored accepted corpus metadata equals `startup_fanout('promql',
     has_query=True)`.
5. **Metadata equals the accepted corpus rule.** For all 12 actual argv, the
   proposed (or existing SQL) metadata equals my verbatim reimplementation of
   `phase7_workloads.py:484-508`. The argv contain `--dataset metrics`, no
   `--instant`, and the expected positional query, or no query for the editor.
6. **Case-hash map is independent of the proposal's parser.**
   - Options were built from the *pre-existing*
     `postidle-bracket-applied/direct-typed-bindings.json` (`8a205017…c4e8ba`)
     typed records 0 (primary) and 1 (resource-attribution).
   - Base `campaign_cases` reproduces all 52 origin hashes. The proposed build
     reproduces all 52 proposal `after` hashes.
   - Exactly **28 change**: dense-metrics, editor and error, 7 per theme/policy.
     **24 are unchanged**, all `sql-windowed`, which already carried fan-out
     metadata.
   - In every case, only `initial_fanout_*` keys differ.
   - The 140 per-record rows in the 90 own-argv records are internally consistent
     with the 52-case map (0 mismatches). That is a consistency check only, not an
     independent recomputation of the 90.
7. **Timestamp provenance is sound in the direction used.**
   - The fixture stamps `received` with `CLOCK_MONOTONIC` after reading the
     complete request (`phase3.py:773`) and appends trace and milestone under one
     lock (`:830-839`). So `milestones[i].request == i`.
   - The driver stamps `dispatch_monotonic_ns` immediately *before*
     `os.write`/winsize ioctl/signal (`phase7.py:1040,1046,1058`).
   - Therefore `received < dispatch` implies that the request bytes existed before
     any input bytes, which is sound causal exclusion.
   - The gate adds no new clock assumption. `observation_issues` already compares
     the same two clocks via `causal_requests(result, first)`
     (`phase7_bench.py:446-450`, used at base `:948`).
8. **Effect source agrees** (read only).
   - Go `promql.go:466-478` batches result, cache and datasets independently when
     the dataset is set. `datasetListMsg` auto-selects only for `""`/`select-dataset`
     (`:510-535`).
   - Rust `promql.rs:3261-3274` mirrors this.
   - Go `query.go:456-462` batches discovery plus the optional window query.
     Schema follows discovery.
9. **All bench validity paths combine the gate with `compare`.**
   `observation_issues` and `p7.compare` are paired at `phase7_bench.py:1548/1553`,
   `1676/1688`, `1752/1760`, `2293/2296`, `2449/2459`. Functional-corpus
   `compare` is unchanged; the test `test_accepted_corpus_and_comparator_source_unchanged`
   asserts this, and `phase7.py` is identical.

## Own causal negatives (executed, 13/13 pass on real retained pairs)

These were built on the actual promql-error, dense-1, editor and sql-error pairs
with proposed metadata:

- An input-caused repeat of a startup member (names re-fetched 1 ns after first
  input): the gate passes because it is a later effect, and `compare` reports
  `ordered request contract differs`. Not hidden.
- A startup member received *exactly at* the dispatch instant: the gate rejects.
- An identical extra pre-input request in *both* editor roles: `compare` gives
  `[]` and the gate rejects both roles.
- Array order vs. receipt order inversion: a pre-input request stored after a
  post-input prefix member is rejected.
- A prefix `stream=other` query or a wrong `authorization` header: the gate
  passes, and `compare` reports a difference.
- `schema` before `home` in both SQL-error roles: a dependency violation.
- Misapplying the explicit graph to an implicit-style flow (a fourth pre-input
  names fetch) is rejected by both the gate and the comparison. It fails closed.
- `unexpected` on a prefix member is reported. A missing dispatch on any input
  fails closed.
- Wiring (`rv_wiring.py`): a late home receipt (+1 ms after input) is reported by
  the proposed `observation_issues` and not by the base. **`p7.compare` alone
  returns `[]` for that pair.** With the single wiring line
  (`proposed.py:942`) removed, the gate issue disappears.

## Objections

**R1 (required, document): incorrect factual prose.**
- Proposal lines 63-66 say "Dense-1, dense-100, and sustained dense-1000 happened
  to agree in arrival order".
- Line 166 says the comparison "changes only the two observed startup order
  issues".
- The original report records `ordered request contract differs` for **three**
  cases: `promql-dense-1000`, `promql-dense-1000-sustained`, and
  `promql-query-error`.
- Sustained dense-1000 raw orders are: Go `query_range→names→home`, Rust
  `query_range→home→names`.

The proposal's own `retained-a1retry02-analysis.json` already contains these
orders, so the numbers (8 derived, 4 dense failures) are unaffected. The prose
must be corrected before root cites it.

**R2 (required before apply, tests): the gate's integration is untested.**
- All proposal tests call `startup_fanout_issues` directly.
- No proposal test or existing test kills removal of
  `issues.extend(startup_fanout_issues(result, case))` from `observation_issues`
  (`proposed.py:942`). I demonstrated that this mutation survives.
- Because `p7.compare` by itself accepts a late startup receipt (shown above), that
  line is the whole safety property.
- Add one test through `observation_issues` (with `p7.validate` mocked, as in the
  existing `issues()` helper at `test_phase7_bench.py:2021-2023`) for a late
  startup receipt and for an identical extra pre-input effect. Optionally add a
  source-count guard in the existing `test_float_duration_control…` style.

**R3 (required root step): the existing suite was not run against the proposed source.**
- Neither the proposal nor this review ran `harness/test_phase7_bench.py` against
  it.
- My source scan found no test that pins changed case hashes, asserts the case key
  set, or feeds fan-out-bearing cases into `observation_issues`.
  - `synthetic()` has no metadata, so the gate returns `[]`.
  - `test_all_runtime_waits_are_screen_only` (`:132-136`) is unaffected, because
    the patch adds no wait keys.
- Root must still execute the suite on the combined source under its own
  procedure.

## Non-blocking notes

**N1: the boundary relies on timing, not on readiness. Failure mode: false
invalid, never false pass.**
- Runtime waits are screen-only. `Phases.checkpoint` pops `request_paths`
  (`phase7_bench.py:161-165`).
- Readiness therefore never waits for home/names (dense, PromQL error) or
  home/schema (editor, all SQL). Post-hoc effect checks cover only `query_range` or
  `/api/v1/query`.
- The pre-input guarantee for the other members rests on the observed delay
  between the first qualifying screen and first input: 80 ms `STABLE_MS` plus
  evaluation latency, at least 198.6 ms observed.
- Under load, a delayed home/schema/names receipt would be rejected with
  `initial fan-out is not the exact pre-input request prefix`. Root should record
  that rejection as failed, not retry silently or widen.
- An optional diagnostic improvement: include the offending indexes and margin in
  the message.

**N2: unchanged hashes do not mean unchanged acceptance.**
- The gate also applies to the 24 `sql-windowed` cases whose definitions are
  unchanged. This is strictly tighter; all 20 retained SQL roles pass it.
- Retained reports cannot carry over anyway: `source_snapshot()` binding
  (`phase7_bench.py:1763`, `:2012`, `:2468`) rejects any source change, so full rebinding is
  required, as the proposal states.
- A1retry01 has no hashed retained session (`not completed`), so it adds no
  evidence.

**N3: the metadata is asserted by the caller, not derived from argv.**
- `startup_fanout(entry, has_query)` never inspects argv. A future argv edit (for
  example, removing `--dataset` or adding `--instant`) would not update it.
- I showed such misuse fails closed (count/membership plus the gate). Deriving
  from argv, or asserting `--dataset` / no `--instant` in the dense and error
  constructors, would be cheap hardening.

**N4: the request-index check accepts a bool.**
`{m.get('request')}` treats `True` as `1`. The fixture always emits ints, so this
is informational only.

## Not claimed

- No runtime, styled, or performance acceptance.
- Nothing about dense readiness, footer geometry, H-8/H-6, or the sustained page
  step, which still awaits actual Go.
- No validation of the proposed Go page-down control.
- No admission or rebinding.
