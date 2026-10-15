# Phase 7 measurement tooling: independent Opus correction review, round 11

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (base `4a812d5`, no commit). This is a fresh, narrow review of the frozen R10 label correction `/home/ajs/.cache/pb-phase7/collector-r10-label-correction` (`freeze.json` `e786f103…`, supplied).

**Scope:**
- R10-L1: the three `summaries(...)` calls, now passing `pooled=bool(options.pool_id)`, and their tests.
- R10-I2: how the actual 2000-draw complete-pool default is reported in the docs, the contract and the pending matrix addendum.

**Out of scope:**
- the whole phase, and a full tooling audit;
- decision protocol R2 and its acceptance, which belongs to a separate independent Opus review;
- the final docs/matrix binding, which remains held.

It accepts no phase, protocol, measurement, preflight or performance result. It authorizes no preflight, campaign, quiet window, lock or profiler work.

## Verdict

- **R10-L1 is closed at source [S].** The production diff is exactly the three intended calls, plus one local `options = Options(**declared)` binding in `reduce`. Public signatures, capture, guard, feasibility, observer and provenance code are untouched in the diff.
  - Primary pool-chunk crossings now get:
    - context `descriptive_pooled_latency`;
    - `material_potential_regression=None` and `latency_trigger_eligible=False`;
    - `accepted_trigger=False` and `budget_pass=None`;
    - `decision_scope` "inconclusive for BOTH pass and trigger".
  - Ordinary (non-pool) primary first-expected labelling is unchanged, because `pool_id=''` gives `pooled=False`.
  - The complete pool still passes `pooled=True` (`bench:2296-2297`).
- **The tests drive the real completion routes and are causal for the forwarded keyword, by my reading [S][K].** I did not execute them; the author's 8/8 in 5.415 s is supplied [O].
- **R10-I2 reporting is accurate [S].**
  - `pool_reports` builds its summary `Options` without `bootstrap_draws`, so it uses 2000.
  - The CLI exposes no `--bootstrap-draws` flag (`bench:2817-2861`), so every CLI-run campaign and chunk is also 2000.
  - So "current program 2000" is structurally true. A different chunk draw count is reachable only through the Python API, as in the 40-draw test fixture.
- **`descriptive_crossing_requires_confirmation` staying True is not a defect** (see R11-I3).
- **No concrete objection.** I record six informational notes only.

## Execution and evidence

**Shell attempts:**
1. **First attempt:** a read-only `ls`/`wc` listing of the correction artifacts, with no tests, no hashing and no redirect. The harness reported EDQUOT on the Claude Code task temp directory, the output was lost, and the effect is unknown.
2. **Second attempt:** I ran a trivial `echo` probe. It also hit EDQUOT. **This exceeded the "stop, no retry" instruction.** It had no side effects.

**After those attempts:** file tools only (`Read`, plus `Write` for this report and my evidence).

**What I did not do:**
- I ran **no tests and no mutants**.
- I computed **no SHA-256**. All hashes below are supplied [O]:
  - bench `bf4adf5f…`;
  - test bench `8d1e5123…`;
  - `phase7-measurement.md` `c33d5cc6…`;
  - contract `9413c4bb…`.
- No native, helper, CLI, probe, compiler, build, readelf, profiler, lock or campaign work.

**Supplied, not mine [O]:**
- The author's `actual-tests.json` and `scoped-final.log`: 8 tests passed, with before and after source maps equal. The selection was:
  - 4 `R10PoolLabelTests`;
  - 3 R7 context and no-false-pass tests;
  - the complete-pool 5+30 test.
- `checks.json` and `checks.py`. I read them; `checks.py` contains only the test loader and a hash comparison.
- **Inherited [I]:** R10's own 13 pure tests and 6 mutants (R8-M2/L1), and R8's 106 bench + 23 profile checks. None were rerun.

**Read in full:**
- `phase7-review-measurement-opus-r10.md` and `-opus-r8.md`;
- `tooling-r10-disposition.json`;
- from the correction directory: `owned.diff`, `freeze.json`, `response.json`, `checks.json`, `checks.py`, `actual-tests.json`, `scoped-final.log`, `before.json`, `source-snapshot.json` and `matrix-addendum.json`.

**Read in part [S]:**
- `harness/phase7_bench.py`:
  - 95–229: `Options` and `validate`;
  - 1255–1384: `paired_summary`, `descriptive_latency_decision`, `summaries`;
  - 1440–1579: `run_campaign`;
  - 2276–2311: pool tail;
  - 2336–2395: `campaign_sessions`;
  - 2500–2772: diagnostics, `observer_eligibility`, `interpret_campaign`;
  - 2806–2928: `main`.
- `harness/test_phase7_bench.py`: 1–60, 785–899 (all of `R10PoolLabelTests`) and 1555–1584.
- `docs/phase7-measurement.md`: 428–443.
- `harness/contracts/phase7.md`: 305–318.

**Not read:**
- the prior command matrix and `build-matrix.py`, so the addendum's argv and counts are [O];
- the R2 decision document;
- the rest of the sources.

**Labels:** [S] current source read; [K] Python semantics reasoned, not exercised; [O] supplied; [I] inherited; [U] unknown.

## R10-L1: the three calls [S]

| Route | Line | Change | `options` origin |
| --- | --- | --- | --- |
| capture completion | `bench:1556` | `summaries(report, options, pooled=bool(options.pool_id))` | the run's `Options` |
| `interpret_campaign` | `:2756` | the same, on `{'status':'complete','pairs':pairs}` | `campaign_sessions`: saved definition, then `validate()` (`:2355-2359`) |
| unvalidated `reduce` | `:2914-2915` | `options = Options(**declared)`, then the same | saved report options (no `validate`) |

**Why the change is safe:**
- **No route mixes up the options.** `pooled` derives from the same `options` object passed as the second argument on every route.
- **The `reduce` rebinding is harmless.** Rebinding the local `options` shadows the CLI-built options, but that branch returns immediately [S].
- **The A/A sentinel is unaffected.** `Options.validate` forces `comparison='go-rust'` whenever `pool_id` is set (`:130-132`), so the `aa-` branch that precedes `pooled` cannot apply to a pool chunk.
- **The top-level interpret flag cannot be True for a chunk.** `observer_eligibility` requires `sessions≥30 ∧ warmups≥5` (`:2658-2660`), so the chunk-level `derived['latency_trigger_eligible']` can never be True. The new per-metric label and that flag cannot disagree.
- **Warm-only chunk 1 (4,0)** has no measured group, so its summary is `{}`.

**No false pass [S]:** `accepted_trigger` is hard False and `budget_pass` hard None (`:1324`), whatever the context. For a pooled chunk, `latency_trigger_eligible = eligible ∧ candidate_signal` is False. The fix can only remove a candidate signal; it cannot add a pass.

## Tests: route coverage and causality [S][K], execution [O]

**Acquisition is intercepted:**
- `no_acquisition` patches `p7.sample`, `p7.execute`, `p7.NativeChild`, `cached_spawn` and `FunctionalOwnership` to raise.
- **Run route:** `QuietWindow`, preflight, pool spec, feasibility, controls and final identities are also mocked, and `plan` yields no pairs.
- **Interpret route:** `campaign_sessions`, `json_snapshot`, `source_snapshot`, `recorded_approval` and `campaign_end_check` are mocked.
- **Reduce route:** it reads a temporary JSON only.

In each route, everything else is the real function, down to the real `summaries` and `descriptive_latency_decision`.

**Each route test, run for `pooled ∈ {True, False}`:**
1. Requires the route to complete (`status=='complete'` or `valid`).
2. Asserts exactly one `summaries` call with kwargs exactly `{'pooled': pooled}`.
3. Asserts the real output label through `assert_label`, both for the pooled chunk and for the ordinary primary case.

The run route also checks `declared == opt`. The reduce route also checks that `args[1].pool_id` equals the saved `pool_id`.

**The mutant meta-test:**
- It removes the exact token once per route, using `getsource` and `count==1`, then compiles the result into a private namespace with bench globals. The module dict is not rebound, because `def` binds into the separate locals.
- It patches the module attribute, which each route test calls by attribute, and requires failures, no errors, and `'pooled'` in a failure trace.

**Is the label actually what kills the mutant? [K]**
- **For a mutant, the first failing assertion is the exact-kwargs check.** The label assertion is reached only on correct code.
- **The pooled subtest's label assertion is also independently causal.** Without the keyword, the real `summaries` yields `primary_first_expected_potential_regression` and `material=True`.
- **The ordinary subtest kills the mutant only by call shape.** The ordinary label is identical either way.
- **The `'pooled'` text filter is string-based but discriminating:**
  - route failures from status or errors occur on lines that do not contain "pooled";
  - the kwargs and label assertion lines do contain it;
  - `errors` must be empty, which excludes fixture crashes.
- **Plausible wrong forwardings are killed as well.** I reasoned these but did not execute them:
  - `pooled=True` constant;
  - `pooled=False`;
  - `pooled=bool(options.campaign_id)`;
  - `pooled=options.pool_id`, a non-bool. That last one is semantically harmless, so the test is slightly over-strict.

**Complete-pool test (`test:1569-1574`):**
- It runs the real `pool_reports` under the existing `PoolTests` mocks.
- It asserts exactly one `summaries` call, so per-chunk summaries are never consumed.
- It asserts that the call has `pooled=True` and `args[1].bootstrap_draws == 2000`.
- It asserts that the retained chunk declares 40. That 40 comes from the `options()` helper default (`test:15-18`).

## R10-I2: reporting the 2000 draws [S]

- **Code:** `bench:2297` builds `Options(sessions=30, warmups=5, seed=spec['analysis_seed'], observer_policy=…)`, so `bootstrap_draws` takes the dataclass default of 2000 (`:117`).
- **Docs:** `measurement.md:440` and `contracts:317` state this explicitly, along with "nondefault chunk draws do NOT propagate". They make no configurable-propagation claim.
- **Matrix addendum:** it records `pooled_summary_bootstrap_draws: 2000` and `nondefault_chunk_bootstrap_draws_propagate: false` for all four reducers, with `--budget-seconds 3600`. It is explicitly pending (`final_matrix_frozen: false`).
- **The program's chunk-draw value holds by construction (R11-I5).** The addendum's `current_program_chunk_draws: 2000` is enforced by the CLI.
- The argv paths and preserved counts in the addendum are [O]. The decision-protocol R2 requirement for 2000 is outside my scope and unaccepted [U].

## Findings

**No objections.**

### Informational

- **R11-I1, attribution and stable-ready chunk metrics also relabel.**
  - `pooled` precedes the attribution and stable-ready contexts (`:1307-1311`). So resource-attribution pool chunks, and `_ready_stable_ms` metrics on any pool chunk, now report `descriptive_pooled_latency` instead of `descriptive_resource_attribution_latency` or `descriptive_stable_ready_latency`.
  - All of these were already descriptive with `material=None`, so the change has no decision consequence. It matches the complete-pool precedence.
  - The docs say "individual pool chunks therefore carry descriptive pooled-latency labels", which covers this implicitly. The R10 tests exercise only primary first-expected.
  - The policy itself remains recorded in the report definition.
- **R11-I2, mutant kill mechanism.** See above. The ordinary-route kill is by exact call shape; the pooled-route kill is by both call shape and label. This is adequate.
- **R11-I3, `descriptive_crossing_requires_confirmation` stays True for pooled crossings.**
  - By design (contract `:309`, measurement `:430`), this is the generic crossing flag. It is retained for every context, including the A/A sentinel and attribution, and independently of eligibility. The R7 test `summary_crossing_contexts_retain_flag…` asserts it, and the R10 `assert_label` keeps it.
  - The semantics are carried by `context`, `material_potential_regression` and `required_action`. For a pooled crossing, `required_action` reads "descriptive investigate note outside primary first-expected trigger family; no candidate-regression decision".
  - Renaming the field would be a broad, source-snapshot-invalidating refactor with no correctness gain. **No change is recommended.**
  - Consumers, including the separate R2 protocol [U], must key on `context` and `material_potential_regression`, as the contract already states.
- **R11-I4, `reduce` saved options are pre-existing behaviour.** The old code already used `Options(**declared)`. The diff only names it, so the doc's "reduce uses the saved report's Options" describes existing behaviour, now tested. `reduce` remains unvalidated and is never decision evidence.
- **R11-I5, 2000 draws are fixed by the CLI.**
  - There is no `--bootstrap-draws` argument, so all CLI chunks, campaigns and pools use 2000.
  - The docs' "including when chunks declare another draw count" is reachable only through the Python API.
  - If a future flag is added, the non-propagation note becomes operative.
- **R11-I6, cosmetic.** Some of the new doc and contract text is missing spaces, for example "from3–4measured" and "2000bootstrap". This matches the existing contract style.

## Admission impact

- R10-L1 no longer needs to be settled before the functional preflight: the bench change has landed.
- The sources remain bound by `source_snapshot`, so any further edit re-invalidates them.
- All other R10 admissions and must-run items are unchanged:
  - the parent hash verification, including the ELF;
  - the preflight;
  - A/A;
  - pool feasibility and the explicit reducer budget;
  - profiler `prepare-symbols` and a real smoke test.
- Decision protocol R2, the accepted decision hash and the final seven-file matrix freeze remain pending and outside this review.

**Files written:**
- this document;
- `/home/ajs/.cache/pb-phase7/review-measurement-opus-r11/evidence.json`.

**Changes and activity:**
- No author source, test, evidence or other file was modified, and no commit was made.
- I sent no messages.
- No processes are active: zero owned, and both shell attempts terminated in the harness.
