# Phase 7 measurement, helper and tooling: independent Opus review, round 8

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (base `4a812d5`, Go pin `07af390`). This is a fresh review round of the frozen R7 correction successor (freeze `97aa53cb…`, response `1ff10d92…`, matrix `5b99e77f…`, bench `32f71650…`; all supplied). It accepts no phase, release, candidate or measured result. It authorizes no preflight, campaign, pilot, decoder run, profiler run, quiet window or lock acquisition. Earlier reports (R1–R7) and their evidence are unchanged.

## Verdict

- **R7 corrections: implemented at source as dispositioned.** I checked each against the current lines (table below).
  - **R7-M2 (pilot cap/actual) is fixed.** The new regression is causal: the pre-fix code would fail it.
  - **R7-M1 is a disposition.** Its source rule holds on every path: no accepted trigger, no budget pass, and no zero substitution for primary, attribution, A/A, interpret or pool.
  - **Nine-chunk pooling** is exactly bound, and its formula is arithmetically right.
  - The **R7-L1 coordination rule** is recorded in the docs, contract and matrix.
  - The **root's preflight end-binding objection** still holds, and its mutant test is causal.
- **Two new medium findings and three low:**
  - **R8-M1 (decision rules):** after R7-M1, no instrument output or predeclared rule can ever produce a latency budget pass, so eligibility has no operational consequence. The plan requires budgets and a faster / non-inferior / slower / inconclusive classification, predeclared before results.
  - **R8-M2 (liveness/sequencing):** the long-idle pool reducer runs under a hard-coded 600 s guard and repeats the full-profile preflight revalidation for each of the nine chunks. If that does not fit, the only fix is a source change, which invalidates every pool chunk and both preflights.
  - **R8-L1 to R8-L3:** a test gap on the core no-false-pass property; crossing semantics for A/A, attribution and stable-ready metrics; and stale "current" doc sections.
- **Admission 1, functional CLI preflight: fit at source.** **Sequencing caveat:** `source_snapshot` binds every `harness/*.py`, test files included (`bench:1148`). R8-M2 and any R8-L1/L2 code or test change must therefore land, or be explicitly declined, **before** the preflight.
- **Admission 2, first truthful bounded A/A and controls: fit at source.** A/A latency is descriptive. How an A/A crossing is to be used is undefined (R8-L2).
- **Admission 3, primary, resource, diagnostic, interpret and pools:**
  - Captures: fit at source. Resource-attribution CPU/RSS is non-long only.
  - Diagnostic and interpret: source-correct after R7-M2, with practical fit unknown. They have **no decision value** until R8-M1 is settled.
  - Long idle: the design formula has room (x ≤ 55.5 s), but the actual fit is unknown, and R8-M2 must be settled first.
- **Admission 4, profiler preparation and interoperability: not ready.** This is unchanged. The driver's own `prepare-symbols` and the bounded real interoperability smoke test have not run.

## Scope, execution and evidence limits

**Execution:**
- My single shell attempt, with the disk-backed `TMPDIR`/`GOTMPDIR`/`CLAUDE_CODE_TMPDIR` exports, failed with EDQUOT on the Claude Code task temp directory. Its output was lost. As instructed, I made no further shell attempt and used file tools only.
- I ran **no tests** (pure, mock or otherwise), and no CLI, helper, compiler, probe, profiler, decoder, timing, build or lock.
- I computed **no SHA-256**, so I report **no hash verification of my own**.
- I used no symlink workaround and did no cleanup. I edited no source, test, contract, historical report or other worktree.
- **Every finding below is source-only [S].**

**Supplied and not verified by me [O]:**
- **Freeze, response and matrix digests:** the freeze, response, matrix, bench and other digests in `freeze.json`.
- **Owner tests:** 99 bench and 23 pure profile passes.
- **Root central successor:** 99 bench passes in 111.309 s and 24 full profile passes in 1.465 s, including the authorized native analysis leader/worker cleanup. Sources are `docs/evidence/phase7/collector-r7-central-tests.json` and `collector-r7-parent-binding.json`. I read both JSONs; I did not read the logs. The binding JSON's own `status` field still reads "central tests in progress", so it records the pre-test identity check, while `collector-r7-central-tests.json` records the results.
- **Historical root evidence:** the R6 replay-clone evidence, helper77, the native resize control, and the release5 compatibility counts (`3268+6`, `160+30`, `17`, `564+4+4`).

Supplied logs are not my execution evidence, and passing tests are not acceptance.

**Read in full this round:**
- `harness/phase7_bench.py` (2,863 lines) and `harness/phase7_profile.py` (776 lines).
- Reviews:
  - `phase7-review-measurement-r1.md`, `-interim-sol-r2.md`, and `-opus-r3.md` through `-opus-r7.md`;
  - `phase7-review-spawn-helper-r1.md` and `-interim-sol-r2.md`.
- Dispositions and responses:
  - `docs/evidence/phase7/tooling-r6-disposition.json` and `tooling-r7-disposition.json`;
  - `collector-opus-r4-disposition.json`, `collector-opus-r3-response.json` and `collector-r5-response.json`;
  - `collector-r7-central-tests.json` and `collector-r7-parent-binding.json`.
- From the cache: `/home/ajs/.cache/pb-phase7/collector-r7-corrections/{freeze,response}.json` and `/home/ajs/.cache/pb-phase7/harness-native-helper-r2-response.json`.

**Read in part:**
- `harness/test_phase7_bench.py`: lines 1–120, 600–1499 (R5 observer, R6, R7, Pool and Quiet tests) and 1900–2202.
- `docs/phase7-measurement.md`: lines 180–409.
- `harness/contracts/phase7.md`: lines 200–299.
- `docs/phase7-harness.md`: lines 1–140 and 300–499.
- `harness/phase7.py`: lines 1380–1579 (`compare`, `load_session`, session and paired statistics).
- `command-matrix.json`: lines 1–360, 17600–17999 and 18330–18501.
- `/home/ajs/.cache/pb-roadmap/phase7-final.md`: lines 55–123.

**Not read, so no coverage is claimed:**
- **Test and shared harness files:**
  - `test_phase7_bench.py` lines 121–599 and 1500–1899;
  - `test_phase7_profile.py`, `test_phase7.py`, `phase7_workloads.py`, `phase7_spawn.c` and `profile_external.py`;
  - the rest of `phase7.py`.
- **Docs and plan:**
  - `docs/phase7-profiling.md` (so I cannot confirm it carries the R7-L1 rule; see I-1);
  - `docs/rust-phase7.md`;
  - `measurement.md` lines 1–179;
  - the rest of the contract and of `phase7-harness.md`.
- **Correction artifacts:**
  - from `collector-r7-corrections`: `actual-checks.json`, `source-snapshot.json`, `owned.diff`, `finalize.py`, `build-matrix.py`, `matrix-result.json` and every log;
  - the R6 cache response;
  - the R3 and R4 cache response JSONs other than those named above.
- **Matrix:** the rest of the matrix, so the per-run chunk argv, the A/A templates and seed distinctness are [O].
- **Evidence directory:** `docs/evidence/phase7/` could not be enumerated without a shell, so any file there whose name I did not know is unread.

**Inherited, and labeled as such [I]:**
- **Helper C-level lifecycle:** WNOWAIT reservation, exact `wait4`, errno handshake, the narrow signal policy and pidfd group signalling. This rests on the Sol R2 executed checks and R4's full read; the C hash is supplied as unchanged [O]. I re-read only `phase7-harness.md` 400–499 on this.
- **Known helper limitations, preserved and not re-litigated:**
  - fatal supervisor death leaves CLI resources and status unknown;
  - escaped `setsid`/`setpgid` and short-lived descendants;
  - path and check-to-exec TOCTOU;
  - spawning-thread lifetime and SIGCHLD ownership;
  - cross-platform behaviour unknown.

**Labels:** [S] current source; [K] kernel, pyte or Python semantics, not exercised; [O] supplied; [U] unknown or unmeasured; [I] inherited.

The evidence record is `/home/ajs/.cache/pb-phase7/review-measurement-opus-r8/evidence.json`.

## R7 corrections: independent disposition [S]

| Item | Status | Basis (current lines) and residual |
| --- | --- | --- |
| **R7-M2** (cap vs actual) | **Fixed; causal test** | See note 1. Actual W is [U]. |
| **R7-M1** (eligibility coupled to latency) | **Disposition implemented** | See note 2. Residuals: R8-M1, R8-L1, R8-L2. |
| **I3** (tails) | **Implemented** | See note 3. |
| **R7-L1** (prep before QuietWindow) | **Rule recorded; no source change** | See note 4. Residual: I-1. |
| **R7-L2** (nine chunks) | **Exactly bound** | See note 5. Residual: R8-M2. |
| **Warm-only chunks** | **NOT APPLICABLE, explicit** | See note 6. |
| **Root objection** (preflight end binding) | **Holds** | See note 7. |
| **I4 and I5** | **Documented** | `measurement.md:403` and `:409`, `contracts:298`, matrix 18498–18499. |

**Notes:**

1. **R7-M2.**
   - `repetition_work` sets `serialization_bytes` to the cap (`bench:2272-2281`).
   - The pilot rows store `repetition_work(sample['work'], …)` (`:2548`, `:2556`). `pilot_allocation` applies the same transform to the remaining sessions (`:2459`), so identical sessions give ratio 1.
   - Actual evidence + progress bytes are recorded *before* the `> cap` raise (`:2547-2552`), so they are retained on failure.
   - The regression `test:1076-1092` drives real `campaign_diagnostics` with real `pilot_allocation` and asserts `maximum_work_ratio == 1` for every admission. Under the pre-fix code (actual bytes on the pilot side, cap on the remaining side) that assertion fails, so the test is causal.
   - `test:1094-1102` checks 174·W + 704 for W ∈ {0.1, 1, 2, 10}. `test:1104-1116` checks retention of actual bytes when the cap is exceeded.
   - My own arithmetic agrees: 58 × 8 + (30 + 5 × 30) + 60 = 704.
2. **R7-M1.**
   - `descriptive_latency_decision` (`:1299-1314`) is attached to every `_first_expected_ms` and `_ready_stable_ms` metric in `summaries` (`:1334-1341`).
   - `accepted_trigger` is always `False` and `budget_pass` is always `None`.
   - Nonfinite values raise. A nonpositive reference gives `None`, not `False`. A failed or incomplete campaign gives no summary (`:1318-1319`).
   - The crossing uses the median of per-pair (b − a)/a. That is exactly `median_ratio − 1` of `p7.paired_session_statistics` (`phase7.py:1571`), so the flag and the reported paired ratio cannot disagree.
   - **Capture:** stamps every phase ineligible (`:1527-1529`).
   - **Interpret:** stamps only from `observer_eligibility` (`:2605-2613`, `:2687`). `observer_eligibility` (`:2588-2602`) fixes A/A and attribution as ineligible.
   - **Pool:** never stamps (`:2104-2105`, `:2235`).
   - I found no path where eligibility filters which pairs, sessions or phases enter a summary.
3. **I3.** `role_session_observer_work_distribution_ms` uses `distribution()` (max, p95, p99) (`:2592`, `:2599`). Tested at `test:1143-1150`. It is descriptive only, as stated.
4. **R7-L1.**
   - `run` still calls `validate_preflight`, which reaches `cached_spawn()` with probe=True (`:1689`), and `validate_pool_spec` before `QuietWindow` (`:1446-1452` vs `:1473`).
   - The rule is at matrix line 18460, `measurement.md:407` and `contracts:298`.
5. **R7-L2.**
   - The counts are declared at `POOL_COUNTS` (`:35`). `Options.validate` binds index and counts, and allows a zero-measured chunk only in a pool (`:130-137`).
   - `validate_pool_chunks` checks the full index, seed, count and analysis-seed table (`:2072-2081`).
   - The reducer requires exactly nine paths, every chunk 1–9, 5 + 30 pairs, distinct campaign IDs, sessions and lifecycle starts (`:2109`, `:2128-2134`, `:2189-2199`, `:2217`).
   - Feasibility is computed for 4 pairs against the FULL same-profile preflight (`:2032-2044`).
   - **Formula check:** 12 × 30 + 60 + 8 × (300 + 1.5x + 8) + (30 + 5 × 4) = 2934 + 12x. At x = 55.5 the total is exactly 3600 (`test:1323-1357`).
   - **Counts check:** 26 variants × 35 pairs = 910 pairs, i.e. 1,820 CLI roles per policy. 24 + 2 × 9 = 42 campaigns × 12 controls = 504. Two policies give 2 × (1,820 + 504) = 4,648 children and 2 × 780 × 2 = 3,120 measured-role replays. 2 × (24 + 2 × 8) = 80 runnable diagnostics. These recompute.
   - Seed distinctness across all 84 campaigns is [O].
6. **Warm-only chunks.** `diagnostic_plan` and `campaign_diagnostics` reject fewer than 2 measured sessions with "NOT APPLICABLE" (`:2403-2404`, `:2497-2498`). The matrix has `executable_template: null` (18461–18489).
7. **Root objection.**
   - `pref_identity` is the first end-check record (`:2322`) and is returned in `file_identities` (`:2393`).
   - `end_bindings` is written only after a successful end check (`:2560`). `interpret` recomputes it and requires equality (`:2688-2690`).
   - Tests: `test:758-773` asserts the exact end-check error, no `end_bindings` and all sessions retained. `test:1027-1042` shows that filtering only the preflight record flips the result to valid, which proves causality.
   - The validated-identity equality is at `:2316`.

## New findings (most severe first)

### R8-M1 — Medium, decision semantics and process: after R7-M1 the instrument has no pass path, and the rules that would turn results into budget decisions are not predeclared [S], incidence certain, consequence [U]

**What the source and docs now say:**
- Every latency decision has `budget_pass: None` and `accepted_trigger: False` regardless of eligibility (`bench:1309-1310`). `eligible` changes only the `decision_scope` string (`:1311`).
- The docs confirm that "ineligible non-crossing also accepts no budget" and that the flag "never marks an accepted trigger or budget pass" (`measurement.md:397`; `contracts:292`; matrix `latency_disposition`, 18491–18497).
- CPU, RSS and idle-CPU triggers have no decision function at all. Summaries give paired statistics only (`:1281-1296`), so applying the numeric triggers (`measurement.md:211-218`) is a manual post-hoc step.

**What the plan requires:**
- "Freeze numerical regression budgets … before acceptance" (`phase7-final.md:71`).
- Phase completion requires "performance budgets" (`:73`).
- "Set practical per-workload … regression budgets before examining candidate results; statistically uncertain results are inconclusive" (`:94`).
- "Distinguish faster, within a declared non-inferiority margin, practically slower and inconclusive … confirm material regressions with a new seed; keep both runs" (`:108`).
- In the docs I read (`measurement.md` 180–409), the numbers are *investigation triggers*, not budgets. I found no declared non-inferiority margin, no declared confirmation-seed rule, and no rule for how an A/A result conditions Go–Rust interpretation. Docs I did not read could contain them [U].

**Trigger:** the coordinator books the 24 potential-primary 3,600 s diagnostic windows (plus interpret, up to 3,600 s each under the common lock). Every reachable outcome is one of:
- ineligible, hence inconclusive;
- a crossing, hence "requires confirmation";
- eligible without a crossing, hence still "no budget acceptance".

No outcome can advance the performance-budget requirement for phase completion. Any rule chosen *after* seeing results would be the retrospective relaxation the plan forbids. Confirmation runs also have no predeclared seed or ID, which leaves room for seed shopping.

**Minimal correction (doc/matrix only; `docs/*.md` and the matrix are not in `source_snapshot`, so the preflight is unaffected):** before any primary campaign, the coordinator predeclares one of:
- **(a)** A pass rule for eligible phases, for example: eligible ∧ no crossing ∧ an upper pointwise ratio bound below the declared margin means "no practical regression detected (exploratory, pointwise)". Also declare:
  - what ineligible phases mean for phase completion (for example "latency budget unassessed by this instrument");
  - the CPU/RSS/idle decision rules;
  - the confirmation seed/ID rule (for example a fixed offset from the campaign base, both runs retained);
  - the A/A conditioning rule (see R8-L2).
- **(b)** That latency budgets are not assessable by this instrument. In that case, downgrade the 24 diagnostic windows to optional and record how the phase-completion requirement is met instead.

If the rule is implemented in code instead, that is a bench change, and it must precede the preflight.

### R8-M2 — Medium, liveness and sequencing: the pool reducer has a hard-coded 600 s guard and repeats the full-profile preflight revalidation for each of the nine chunks [S], magnitude [U]

**Where:**
- `pool_reports` creates `FunctionalGuard(600)` (`bench:2117`). The `pool` CLI forwards no `--budget-seconds` (`:2821-2823`), unlike `interpret` (`:2809`) and `diagnostic-plan` (`:2815`), whose hard-coded budget R6-M2 removed.
- Inside the guarded loop, each chunk calls `validate_pool_spec` (`:2135`), which calls `require_pool_feasibility` (`:2091`). That re-runs:
  - `offline_preflight_check` over the **full** same-theme profile (13 cases, 26 roles; `:2035-2038`);
  - `campaign_feasibility`, which revalidates the long case again (`:2039-2040`, `:2713`).
- Each `validate_preflight` reloads and twice-hashes every retained session, reruns `observation_issues`, dry extraction and the styled comparator, and calls `source_snapshot()` twice (`:1685`, `:1722`). `source_snapshot()` hashes all Go/Rust/harness sources and runs `git rev-parse HEAD` (`:1142-1157`).

**Structural count (source-derived) for one reduction:**
- 9 × (26 + 2) = 252 preflight-role revalidations, which include the sustained SQL session (85 checkpoints) and the sustained dense session (67).
- About 300 full executable hashes, about 45 source snapshots, 70 pooled-role revalidations, 108 control raws, and a final rehash of every raw.
- The per-unit cost is unmeasured [U]. It is plausibly hundreds of seconds, the same order as the fixed 600 s.

**Trigger and effect:**
- The reduction overruns, `guard.check()` raises, and the result is `valid: False` with no summary. This fails closed.
- But the reducer and every chunk bind `spec.source == source_snapshot()` (`:2109`, `:2138`). Raising the budget afterwards is a source change. That invalidates the pool spec, all nine chunks per pool (36 quiet-window campaigns across the four pools), and both functional preflights.
- The long-idle attribution pool is the **only** idle-CPU trigger path (`:1083-1085`). A non-fitting reducer would therefore leave the idle-CPU trigger permanently without evidence after all collection is spent.
- The preflight's postprocessing walls could calibrate this, but only *after* the preflight, by which point the source is bound.

**Minimal correction (bench-only; decide before the preflight):**
1. Plumb a declared, recorded `--budget-seconds` (0 < b ≤ 3600) into `pool_reports`, as `interpret` does.
2. Compute `require_pool_feasibility` once per reduction rather than once per chunk. Verify `public_value(allocation)` against the spec once, and keep the per-chunk identity checks.
3. Add a regression that counts `offline_preflight_check` calls in a nine-chunk reduction, and one that rejects a nonfinite or >3600 budget.

Each long-idle pool still remains descriptive for latency.

### R8-L1 — Low, test coverage: the central no-false-pass property is true in source but not guarded by tests [S]

**The gap:**
- `test:1118-1141` uses the cases (Δ20, ineligible) → crossing, (Δ5, ineligible) → none, (Δ20, eligible) → crossing, and (Δ10, ineligible) → boundary, none.
- It has **no eligible ∧ non-crossing case**. So a mutant `budget_pass = True if eligible and not crossing else None` passes every test. That is exactly the false pass R7-M1 rules out.
- Every case moves the absolute and relative arms together (reference 100). So replacing `and` with `or` at `:1306` is not killed either. That mutant over-flags, which is fail-safe.
- The R6 full-pipeline positive test (`test:829-869`) asserts eligibility only, not `latency_decision`.

**Correction:** add the case (Δ5, eligible) → `budget_pass is None` and `accepted_trigger is False`. Add Δ20 on reference 400 (5%) → no crossing, and Δ8 on reference 50 (16%) → no crossing. Assert `latency_decision` in the interpret 5 + 30 positive test.

**Sequencing:** `test_phase7_bench.py` is inside `source_snapshot` (`:1148`), so add these before the preflight or accept the gap as a recorded residual.

### R8-L2 — Low, semantics: crossing flags carry candidate-regression wording outside the predeclared trigger family [S]

**Where:** `summaries` attaches the same decision, including `material_potential_regression` and "fresh-seed confirmation AND investigation", to:
- **A/A campaigns.** There is no candidate; a crossing there indicates harness or host bias.
- **Resource-attribution and pooled summaries.** Their latency is descriptive by policy.
- **`_ready_stable_ms`.** The trigger is defined for first-useful/input latency only (`measurement.md:211`).

`descriptive_latency_decision` receives no comparison or policy context (`:1341`).

**Effect:** the labeling is conservative and creates no false pass. But no rule says that an A/A crossing makes the same-profile Go–Rust crossings and eligibility uninterpretable pending investigation, which is the main purpose of the plan's A/A control (`phase7-final.md:108`).

**Correction:** a coordinator disposition covering:
- an A/A crossing means a harness-bias failure, not a regression, and it conditions the Go–Rust interpretation of that profile;
- attribution and pool crossings are descriptive "investigate" notes;
- `ready_stable` crossings are outside the trigger family.

Optionally, pass comparison and policy into the decision record. That is a bench change, so it must precede the preflight.

### R8-L3 — Low, documentation drift: superseded sections still read as current [S]

**Stale text:**
- `measurement.md:271` heads "R4 implementation and **current** execution contract". It still instructs:
  - seven chunks with `--budget-seconds 3300` (`:265`, `:302`);
  - `pool` as "offline, no native/probe/lock" (`:312`), which the code (`:2821-2823`) and `:389` contradict;
  - `--max-sessions 82 --budget-seconds 3000` (`:263`).
- `contracts:266` says "requires all seven chunks".

Later sections supersede these (`measurement.md:395-409`, `contracts:290-298`), and the matrix is authoritative.

**Correction:** retitle the R4 heading as historical, or add a one-line "superseded by R7" marker. This is doc-only and does not affect the preflight.

### Informational

- **I-1, profiler preparation outside the lock.**
  - `prepare-symbols` runs `cached_spawn()`, which probes, and launches `readelf` through `NativeChild`, with no `FunctionalOwnership` (`profile:514-548`).
  - The profiler's `run_campaign` probes and verifies symbol evidence before its `QuietWindow` (`profile:581-603`).
  - The R7-L1 root rule ("ALL preparation") covers both. I did not read `phase7-profiling.md`, so I cannot confirm the rule is stated there.
- **I-2, `reduce` trusts the report.** It runs `summaries` on an unvalidated `json.loads` of the report (`:2843-2851`). Its crossing flags must never be used for decisions. `interpret` and `pool` recompute from raw.
- **I-3, multiplicity.** The sustained SQL and dense cases emit about 80 and 65 change phases × 2 metrics of crossing flags per case. Expect a confirmation workload. Fold the confirmation-seed rule into R8-M1.
- **I-4, practical diagnostic fit after R7-M2.**
  - At the first admission, 174·W + 704 ≤ remaining, where remaining = 3600 − issuance lead − full original-campaign revalidation − pilot elapsed. So W < (3600 − 704)/174 ≈ 16.6 s even with zero lead and zero revalidation.
  - W, the revalidation cost and the lead are all [U]. The result fails closed, with no false eligibility.
- **I-5, pool-plan against a both-themes preflight.** The pool-plan matrix entries pass the both-themes 26-case preflight with `--theme dark|light`. `validate_preflight` accepts that superset, and FULL means the 13 same-theme cases (`:2035-2038`). This is consistent [S].

## The ≤ 1 ms screen after full accounting

The screen covers only replayed feed, predicate and recorder work. It bounds neither total observer-induced bias (R6-M1) nor per-session or tail bias (I3), and it is coupled to response latency through evaluation counts (R7-M1). With the R7 disposition it is a defensible **interpretability label**, not a native acceptance threshold. Its only remaining effect is the `decision_scope` string. Whether it ever matters is R8-M1's decision.

## Campaign, path and manifest boundaries [S]

These are unchanged from R7 and rechecked:
- **JSON evidence:** single-read parse and digest (`:81-90`), with an end recheck via resolve-strict, size and rehash (`:93-96`).
- **Raw sessions:** contained, non-symlink, capped and stream-equal, hashed before and after parse (`:1734-1754`). They are rehashed by path at the end without a containment recheck (`:2382-2389`).
- **Preflight:** bound both in the end records and in validated-identity equality (`:2316`, `:2322`).
- **Executables and helper:** ordinary drift checks (`:1176-1187`, `:1762-1770`).
- **Residual:** ordinary check-to-use TOCTOU (A→B→A between reads). There is no atomic or adversarial guarantee, and none is claimed here.

## Admissions: must-run validations versus source defects

**Source decisions to settle before the preflight** (all `harness/*.py` are bound):
- R8-M2 (bench).
- R8-L1 (tests), or record it as a residual.
- Any code part of R8-L2.

R8-M1, R8-L2 (doc part) and R8-L3 are doc/matrix decisions. They do not bind the preflight, but they must precede primary campaigns.

**1. Functional CLI preflight (no timing): fit at source.** Must run:
1. Settle the bench and test changes above, then a no-commit freeze. HEAD stays at `4a812d5`.
2. The parent independently verifies the owned, consumed and helper hashes.
3. Prepare the helper with `cached_spawn(probe=True)`, inside an agreed window with the lock free (R7-L1).
4. Run both 26-variant / 52-role preflights:
   - into new directories;
   - with budget ≤ 3600.
5. Each preflight reaches 52/52 valid roles, 26/26 valid pairs and valid dry extraction, with failures retained. This confirms the H−6 SQL and dense footers and row 16 [U].

**2. First truthful bounded A/A and controls: fit at source.** Must run:
1. A matching A/A preflight with the identical canonical artifact and provenance. I did not locate A/A templates in the matrix portions I read [U].
2. `feasibility` fits, including the 60 s allowance and actual approval lifetime.
3. A fresh campaign-bound approval with a companion `campaign_id`.
4. `budget_evidence` stays false. Predeclare the use of A/A crossings (R8-L2).

**3. Timed campaigns and interpretation:**
- **Resource-attribution CPU/RSS (non-long):** capture fit at source. Requires a matching attribution preflight, 5 + 30, A/A, fit and fresh approvals. CPU/RSS trigger application is manual (R8-M1).
- **Primary latency capture:** fit, and descriptive by construction.
- **Diagnostic and interpret:** must run diagnostic-plan → a separately approved campaign-decode whose pilot fits → root hash binding after process exit → interpret.
  - The source is correct after R7-M2. Practical fit is [U] (I-4).
  - The outcome has no decision value until R8-M1 is settled.
- **Long idle:**
  - The nine-chunk formula admits x ≤ 55.5 s minus issuance lead. The actual full-profile fit is [U].
  - R8-M2 must be settled before the preflight.
  - Pooled latency is descriptive. The idle-CPU trigger uses only the attribution pool with eligible, empty descendants (`:1083-1085`).

**4. Profiler preparation and interoperability: not ready.** Must run:
1. The driver's own `prepare-symbols` against `profiling/release5/artifacts.json`, in an agreed window with the lock free (I-1).
2. A bounded real smoke test covering:
   - perf, strace and heaptrack FD3 / `profiler.stderr` separation;
   - exact heaptrack CLI stderr;
   - raw naming;
   - strace `= ?` parsing;
   - perf report and `$HOME/.debug` side effects;
   - symbol resolution.
3. All controls valid.

**Never:**
- subtract floors, holds or replay work;
- treat attribution, A/A or pooled latency as trigger evidence;
- adopt a pass rule after seeing results;
- infer six-platform, installer, rollback or live HTTP/2 readiness from Linux loopback or helper evidence;
- claim universal speed.

Files written: this document and `/home/ajs/.cache/pb-phase7/review-measurement-opus-r8/evidence.json`. No other file was edited, and no process, lock, thread or message was created.
