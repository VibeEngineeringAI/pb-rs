# Phase 7 performance decision protocol: independent Opus review, round 2

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (base `4a812d5`, Go pin `07af390`). This is a fresh review of the R2 successor proposal. It is not a continuation of R1.

**Subject:**
- `docs/phase7-performance-decisions.md`, declared SHA `5b843481…c202`, 45,820 bytes.
- Its evidence under `/home/ajs/.cache/pb-phase7/performance-decisions-r2/`: manifest `ba9c1157…a16b` (47 entries) and `declaration-pending.json` `52c191ab…3b` (44 dependencies).
- All digests are **supplied [O]. I computed no hash.**

**What this review is not:**
- It grants no admission, window, approval, measurement acceptance or phase acceptance.
- It does not accept the collector source freeze.
- It does not review the R10-L1 three-call label fix. Root reports that R11 closed it, and that the bench is now frozen at `bf4adf5f…6b` [O]. That value equals the bench identity the R2 author recorded in `final-source-and-ast-identities.json`, so I see no bench rebind mismatch, but this is supplied, not hashed by me.
- I edited no author document, evidence, source, test or contract.

## Verdict

**The protocol is fit prospectively as a proposal, subject to explicit prerequisites and the corrections below.** It remains a proposal until root accepts it.

**What is sound:**
- The decision mathematics: ordered classification, exact rational inequalities, the faster ⊂ non-inferior nesting, and disjoint non-inferior and slower categories.
- The envelope bootstrap: it is a conservative dominance construction (shown below).
- Bit-exact source parity of the raw CIs: valid for the bound stdlib, by reading.
- The confirmation rules.
- Zero and missing handling.
- The A/A conditioning.
- The 448 + 18 coverage scope.

**The ±2-tick idle bound is mathematically valid under its stated conditions** (proof below). It removes R1's vacuity problem, because the upper bound now widens only by CPU actually observed at the boundaries, not by a worst-case C·g.

**One medium finding:**
- **PD2-M1 (feasibility and truthfulness).** Under the actual source, the "outer-after" bracket requires the CLI to stay a non-zombie for at least about 40 ms after ctrl-c, in all 60 pool roles.
  - The doc calls a missing bracket an "honest *possible*" outcome. Source structure makes it *plausibly typical*.
  - So both idle decision rows are plausibly structurally unassessed.
  - Root must declare this before the freeze or preflight. Any remedy is a case-definition (bench) change, and the brief excludes a quit delay or observer change.

**Five low findings:**
- **PD2-L1:** stale idle text in the bound ledger, and a naming conflict.
- **PD2-L2:** the total-CPU arithmetic and its "raw CI" are underspecified.
- **PD2-L3:** the order of pooled pairs is not bound.
- **PD2-L4:** a declined confirmation leaves a closure loophole.
- **PD2-L5:** the static-evidence wording and one binding gap.

All five are doc or evidence corrections. None requires a source change.

**Resource provenance is a prerequisite, not a mathematical defect:**
- CPU `q_total` and the accounting mode;
- RSS `maxrss` semantics;
- idle `hz`, the two-floor model and a monotone basis;
- the actual brackets.

These are unknown and correctly held pending. **The honest expected outcome, absent that provenance, is that many decision rows will be unassessed:**
- CPU, until `q_total` is bound;
- latency rows that are not observer-eligible;
- idle, per PD2-M1.

That is not a defect, but root should expect it before booking.

## Execution, evidence and scope

**Execution:**
- I made **one** shell attempt. It set the required `TMPDIR`/`GOTMPDIR`/`CLAUDE_CODE_TMPDIR` to my own cache directory and `PYTHONDONTWRITEBYTECODE=1`, then ran `mkdir`, `ls` and `sha256sum` of the inputs.
- The harness reported **EDQUOT** on its task temp directory, with exit code 1 and the output lost. **Whether the `mkdir`, `ls` or `sha256sum` actually ran is unknown.**
- I made no retry and used no temp-move or symlink workaround. After that I used file tools only.

**Not run:**
- no tests (pure, mock or otherwise);
- no independent arithmetic script (I had no shell left for one);
- no repository import, native helper, CLI, probe, compiler, build, profiler, lock or campaign.

**Not computed:** no hashes. Every digest here is **[O]**.

**Pause:** no quiet pause arrived. **Zero processes are active.**

**Labels:**
- [S] current source or file as read;
- [K] kernel or Python semantics, not exercised;
- [O] supplied;
- [A] my manual arithmetic, not executed;
- [U] unknown;
- [I] inherited.

**Supplied and not verified by me [O]:**
- the author's 331 static checks, "10 mutants" and 36 binding checks;
- root's verification of the 47 artifacts;
- the R10 and R11 results and root's four R11 route tests;
- the release5 compatibility counts.

I tallied the 331 checks by reading `static_design_checks.py`, and the tally agrees [A] (breakdown in PD2-L5). Tallying is not executing.

### Read scope

**Read in full:**
- `phase7-performance-decisions.md` (the subject);
- `phase7-review-performance-decisions-opus-r1.md`;
- `performance-decisions-opus-r1-disposition.json`;
- `performance-decisions-r2-parent-check.json`;
- `phase7-review-measurement-opus-r8.md`;
- `phase7-review-measurement-opus-r10.md`;
- `tooling-r10-disposition.json`;
- from R2: `README.md`, `findings-response.json`, `decision_math.py`, `source_reference_statistics.py`, `static_design_checks.py`, `final-source-and-ast-identities.json`, `resource-provenance-final-pending.json`, `declaration-pending.json`, `python-runtime.json`, `execution.json`, `final-binding-checks.json`, `original-stage-obligations.json` and `decision-record-template.md`.

**Read in part:**
- `matrix-and-confirmation-design.json`: lines 1–160 and 1240–1495 (A/A, first confirmations, attribution pools, bootstrap and ID policy).
- `coverage-design.json`: lines 1–120, 5300–5500, 5640–5760 and 6215–6261 (header, resource and idle rows, the descriptive duplicates, inventory tail).
- `harness/phase7.py`:
  - 330–419: `proc_sample`;
  - 560–1147: `NativeChild` tail, `useful`, and the whole `_execute` loop and metrics.
- `harness/phase7_bench.py`:
  - 90–409: `Options`, `Phases`, all case builders;
  - 410–889: `schedule`, `Readiness`, `Capture`, `native_proc_sample`;
  - 990–1389: proc validation, `resource_interval`, `extract_metrics`, `paired_summary`, `descriptive_latency_decision`, `summaries`;
  - 2143–2307: `pool_reports`;
  - 2780–2928: `main`.
- `command-matrix.json`: 330–459.
- `phase7-final.md`: 55–123.
- `phase7-measurement.md`: 195–254 and 380–443. Both historical and current sections were read and distinguished.
- `harness/contracts/phase7.md`: 280–318.
- `/usr/lib/python3.14/random.py`: 440–489 (`choices`).

**Not read, so no coverage is claimed:**
- From R2: `prepare_design.py`, `revise_document.py`, `freeze_r2.py`, `r1-preservation.json`, `artifact-manifest.json` itself, `source-and-ast-identities.json`, `resource-provenance-pending.json`, and both draft directories.
- The rest of the ledger, design JSON, `arithmetic-checks.json` and the matrix. This includes the pool-reducer `--chunk-report` order (PD2-L3).
- `phase7_spawn.c`, so the helper's rusage serialization is [U].
- `phase7-profiling.md`.
- The R11 review and the root R11 report.
- All tests.

**Inherited [I]:**
- the helper lifecycle (WNOWAIT, exact `wait4`, signal policy);
- R1–R10 tooling closures as recorded;
- known limitations: escaped descendants, TOCTOU, Linux-only behaviour, internal stages unimplemented.

## Findings (most severe first)

### PD2-M1 — Medium, idle feasibility and truthfulness: the outer-after bracket is structurally improbable under current source [S], magnitude [U]

**Protocol:**
- Outer-after requires the earliest retained sample `a` whose **previous** retained stamp is at or after E. So two retained samples must be stamped at or after E (`decisions.md:78`; `decision_math.py:182`).
- Every role of all 30 pairs is required, with no subset (`:69`, `:81`).
- A missing bracket is called "an honest possible unassessed outcome, not authorization to add observation or delay quit" (`:81`).

**Source:**
1. `idle-end` is followed immediately by `quit = {'key': 'ctrl-c', 'stable_ms': 0}`, which has no wait (`bench:183-184`, `:251-252`). `Readiness._qualify` with an empty wait returns True (`bench:489-515`; `p7.useful:777-788`).
   - So ctrl-c is written in the first loop iteration after E reaches action evaluation.
   - That is after at most one `select(0.01)`, about 10 ms (`phase7.py:951`, `:992-1041`).
2. The sampler runs at most once per iteration and only when `now >= next_sample`, with `next_sample = now + 50 ms` (`:927-937`). Two retained samples after E are therefore at least about 50 ms apart.
3. `proc_sample` returns None for `Z`, `X` or `x` states (`:380`, `:389`). Sampling stops once `exited_at` is set (`:927`).
4. Best case: the first post-E sample lands in the very iteration that dispatches ctrl-c. Even then, the second retained sample needs the CLI alive and not a zombie for **at least about 40 ms after ctrl-c**. Otherwise it needs about δ + 50 ms, where δ is in [0, ~60] ms.

**Trigger:** either binary exits within about 40–110 ms of ctrl-c in any one of the 60 attribution-pool roles. Graceful TUI exit times are [U]; I measured nothing.

**Consequence:**
- Both idle decision rows are likely unassessed, so the idle budgets cannot close.
- The design moves R1's PD-M3 problem from an infeasible bound to an infeasible bracket. It does not resolve whether the idle family can be assessed.
- If this is discovered after collection, any fallback would be chosen after data existed. The doc forbids that only implicitly ("separately proposed").

**Minimal fix (doc/declaration; no source change by this review):**
1. Replace "possible" with a prospective feasibility statement. Give the exact source requirement above, and say: "both idle rows are expected to be unassessed unless both binaries remain alive ≥ ~40 ms after ctrl-c in every pool role."
2. Root decides **before the source freeze and functional preflight** between two options:
   - **(a)** accept a probable unassessed idle family, with idle budgets left open or handled by explicit tradeoff; or
   - **(b)** separately propose a case-definition change. An example is an unmeasured post-idle hold checkpoint before quit. That is a bench change, it is outside this lane, the brief currently excludes it, and it would need its own review and re-freeze.
3. State that any later idle fallback applies only to data collected after its prospective declaration, never to already-collected pools.

Optionally, root may authorize a values-blind structural count (present or absent) of post-E retained samples in existing functional evidence. That is root's call: post-E stamps carry exit-latency information.

### PD2-L1 — Low, bound ledger and doc conflict on the idle estimand [S]

**Where:**
- `coverage-design.json` (byte-identical across the draft, before-final and final versions, `4a3c247a…` [O]) still gives the idle decision rows `"interval_policy": "full observed idle interval; tick and unsampled-tail envelope"` (ledger line 5384). "Unsampled-tail envelope" is the R1 C·g method, which R2 explicitly rejects (`:91`).
- The row's metric is the source field `idle_cpu_percent_one_core`. That is the sampled-subinterval estimand.
- `decisions.md:24` defines the family as "`idle_cpu_percent_one_core` plus its conservatively propagated full-interval uncertainty". But `:91` correctly says the source value "is not a point estimate of this possibly differently bracketed full interval".

**Consequence:** the ledger is the declared authority for obligations. As written, it could be read as authorizing the rejected method, or as letting the sampled value stand in for the full-interval target.

**Fix:**
- Regenerate the ledger into a new namespace with a distinct derived metric, for example `full_idle_bracketed_cpu_pp`, and an `interval_policy` that points to the serial-read bracket rule. Keep the source field as a descriptive and screen row.
- Reword `:24` to match.
- Rebind the declaration.

### PD2-L2 — Low, total-CPU arithmetic and its "raw source CI" are not defined [S]

**Where:**
- The source reports `user_cpu_ms` and `system_cpu_ms` only (`bench:1100-1103`, `phase7.py:1112`). No source total, and no total CI, exists.
- Yet `:67` requires the CPU envelope to be unioned with "its reproduced raw **source** CI".
- `bootstrap_envelopes(ratio=True)` refuses to run without one (`decision_math.py:138-139`).
- The bound calculation code has no function for:
  - forming the total (IEEE `u+s` or exact `Fraction(u)+Fraction(s)`);
  - computing the raw total CI;
  - the decision-family point-crossing screen;
  - the idle screen.

**Consequence:** these become implementation choices made after data. They are boundary-only in magnitude, but they are a degree of freedom the protocol exists to remove.

**Fix (prospective):**
- Declare x = `Fraction(user_cpu_ms) + Fraction(system_cpu_ms)` exactly for envelopes.
- Declare the "raw" total CI as `source_index_bootstrap` over IEEE `user_cpu_ms + system_cpu_ms` (in that order), using the bound campaign or pool seed and draws, and label it coordinator-raw, not source.
- Add these functions and the point-crossing and idle-screen functions to the bound code before declaration.

### PD2-L3 — Low, pooled pair order is not enforced by the source [S]

**Where:**
- `pool_reports` appends pairs in `--chunk-report` argv order (`bench:2173`, `:2276`, CLI `:2832`, `:2887`).
- It checks only the set of chunk indices (`:2180-2182`, `:2278`), not their order.
- The doc says the pool is taken "in chunk order" (`:15`).

**Consequence:** a reducer invoked out of order bootstraps a different order. The bit-exact gate then fails closed and the pool is withheld. That is safe, but it is an avoidable loss of a whole pool.

**Fix:**
- Bind the ordered population to the validated pool report's retained `pairs` (measured subset).
- Require it to match chunk-index order 1..9.
- Record the exact reducer argv in the ID registry.

### PD2-L4 — Low, a declined or unexecuted confirmation leaves a closure asymmetry [S]

**Where:**
- A failed, interrupted or invalid confirmation makes every decision row of the case and policy inconclusive (`:53`, `:124`).
- But confirmations are "subject to separate root authorization/feasibility" (`:124`). The doc says nothing about the uncrossed rows of a case whose scheduled confirmation is **never run**.

**Consequence:**
- Uncrossed rows close from the original alone, while running the confirmation could only add adverse evidence.
- That is a mild incentive not to run it. It is the same class of problem as R1's PD-M4.
- Step 7's "metric budget" blocking may cover it at budget level, but row labels would still publish as within-margin.

**Fix:** a scheduled decision-family confirmation that is declined, infeasible or pending keeps every decision row of that case and policy provisional, "inconclusive: confirmation pending", until it runs or root records an explicit tradeoff.

### PD2-L5 — Low, static-evidence wording and binding gaps [S][A]

**Binding gap:** `static_design_checks.py:227-229` checks the **superseded** `resource-provenance-pending.json`, not the declared active `resource-provenance-final-pending.json`.

**The "10 killed unsafe mutants":** these are discriminating cases compared with hand-written alternative outcomes. Examples: `:202-206` and `:217` compare literals. They are not executed mutations of `decision_math.py`. Only `:240`, `:246`, `:247` and `:260` compute their alternative.

**Composition of the 331 checks [A]:**
- **Parity checks (180):** pure copy-versus-copy synthetic parity of `choices(range)` against `choices(rows)` on one dataset across 180 seeds. They demonstrate index equivalence, not source behaviour on real reports.
- **Doc-phrase checks:** substring tests only.

**Fix:**
- Check the active provenance file in the accepted declaration's verification.
- Describe these as "discriminating boundary cases", not mutation testing, in any acceptance record.

### Informational

- **I-1, the ±2-tick conditional proof.** For a read at instant r inside its interval [s_(j−1), s_j]:
  - **Assume** U(r), V(r) ≥ 0 and U(r) + V(r) = X(r) for **one** nondecreasing target X, with n(r) = ⌊U⌋ + ⌊V⌋.
  - **Quantization:** then X − 2 < n ≤ X.
  - **Read order:** r_b ≤ S ≤ r_i ≤ r_k ≤ E ≤ r_a, given b < i < k < a.
  - **Lower bound:** X(E) − X(S) ≥ X(r_k) − X(r_i) > n_k − n_i − 2.
  - **Upper bound:** X(E) − X(S) ≤ X(r_a) − X(r_b) < n_a − n_b + 2.
  - **Valid** as written in `:85-87` and `decision_math.py:189-191`.
  - **Precise conditions:**
    - only the **sum** must equal the target; individual components need not be monotone;
    - the kernel value must correspond to X at some instant within the read call (a "snapshot-within-interval" property root's provenance must cover);
    - the target's name ("tick-accounted") must follow the actual accounting provenance. Under some accounting modes the summed `/proc` fields may track scheduler runtime rather than sampled ticks [K][U]. I assert neither for this host.
  - It bounds no delayed accounting or physical CPU.
  - `idle_brackets` additionally rejects decreasing components. That is stricter than the proof needs, matches source validation (`bench:993-998`) and is harmless.
- **I-2, the upper bracket is data-driven [A].** It widens only by CPU actually recorded within about 2 sampler periods on each side, including any quit work, plus 4 ticks. At hz = 100 [U] and T ≈ 300 s, 4 ticks add 0.0133 pp per role. 100 ms of boundary CPU adds about 0.033 pp. This is not vacuous against 0.25 pp, unlike R1's C·g.
- **I-3, draws.** The CLI has **no** `--bootstrap-draws` flag (`bench:2816-2851`). CLI-launched campaigns are therefore necessarily at 2000, and the pool uses `Options(...)` without draws (`:2297`). This strengthens L2.
- **I-4, runtime.** `random.choices` without weights uses only `len(population)`: `floor(random()*n)` (`random.py:467-473`). So index draws equal row draws for this stdlib [S].
  - `python-runtime.json` binds `/usr/bin/python3.14`, used for the static checks. The matrix runs the collector under `harness-venv/bin/python`.
  - Bind the collector and reducer runtime too. A mismatch fails closed through the bit-exact gate.
- **I-5, crossing precision.** The source flag uses the median of `(b−a)/a` with `relative > .10` (float), while the protocol uses R = median(b/a) against the exact 11/10. R8's "exactly median_ratio − 1" holds in exact arithmetic, not in IEEE floats. The doc's OR rule (`:49`) correctly acts on either.
- **I-6, first-sample exclusion.** Excluding the first retained sample as outer-before is unnecessary, because its read precedes its own stamp. It is conservative and harmless.
- **I-7, rerunning the static checks.** `static_design_checks.py` uses exclusive create (`:323`). A rerun needs a new namespace, as the README says.

## Specific verification

| Item | Assessment |
| --- | --- |
| Definitions and margins | Latency 0.10 / 10 ms, CPU 0.10 / 1 ms, RSS 0.20 / 2,097,152, idle 1/4 pp absolute-only. All exact rationals (`decision_math.py:30-52`). Correct. |
| Inequalities | Faster: strict `<1`, `<0`. Non-inferior: `<=` with AND. Slower: strict `>` with AND. Idle analogues. Categories are disjoint (rl ≤ ru), and faster implies non-inferior. Ties behave as declared. IEEE 1.1 > 11/10. Correct [S][A]. |
| Envelope | Per draw, median(lower_i) ≤ median(true_i) ≤ median(upper_i). Sorting preserves the dominance, so the envelope CI contains the CI that would be computed on the true target values with the same draws. The ratio uses b−/a+ and b+/a− with a− > 0. Union with raw handles IEEE rounding. Valid [A]. Total-CPU raw input: PD2-L2. |
| Source IEEE parity | One `choices` call per draw in both the source and the copy. Same `statistics.median` generator ops. Delta-only path for zero references. Synthetic only; real-report reproduction pending. |
| Zero and missing | A reference ≤ 0 makes the ratio undefined, hence unassessed. Nonfinite or Boolean values are rejected. No subset, imputation or epsilon. Missing confirmation rows make the whole case inconclusive (`combine_case`). Correct. |
| Confirmation | Decision-family only; ineligibility cannot suppress a crossing; whole case/policy scope; +10,000,000 seed offset; whole-pool shift. Faster+non-inferior is labeled non-inferior; faster requires both runs faster; slower requires strict bounds in both; no third run. Correct. Gap: PD2-L4. |
| Coverage | 197 phases per theme re-derived from `sql_case` and `dense_case` [A]: 6+8+9+3+3+3+4+9+83+67+1+1. 394+52+2 = 448; 18 duplicates mapped to exact primary metrics; all 84 campaigns retained. Stale idle text: PD2-L1. |
| Eligibility and controls | Matches source fields (`bench:1083-1086`, `:1137`). A/A has four dark editor sentinels; release cannot cleanse, has no temporal reach, and a crossing keeps the block. Correct. |
| Declaration binding | 44 dependencies bound; pending fields explicit; not self-referential; `admission:false`. Gaps: PD2-L1, PD2-L5. The final freeze, ID registry and provenance are null and pending. |
| Truthfulness | No measurement or acceptance claimed; stages UNIMPLEMENTED; pointwise exploratory; no kernel fact adopted. Overstatements: "possible" (PD2-M1) and "mutants" (PD2-L5). |

## Prior finding closure

| Finding | Status in R2 |
| --- | --- |
| **PD-M1** | **Closed.** 448 decision rows + 18 descriptive rows with exact mappings; all 84 campaigns kept; no closure dependency on the 18. |
| **PD-M2** | **Closed in design.** The target is the reported `wait4` total; no split-sum and no reviewer kernel claim. `q_total` and provenance are an explicit prerequisite. Residual: PD2-L2. |
| **PD-M3** | **Mathematically closed.** Ordering proof [S]; read intervals use previous/current stamp; outer-after uses the previous stamp; conditional ±2 valid (I-1); vacuity removed (I-2). **Residual feasibility moves to PD2-M1**, and ledger text to PD2-L1. |
| **PD-M4** | **Closed.** Decision-family-only trigger, all case rows use both runs, invalid confirmation makes everything inconclusive, no third run. Residual: PD2-L4. |
| **PD-L1** | **Closed** (`combine_row`). |
| **PD-L2** | **Closed.** Campaign and pool seeds and actual 2000 draws; bit-exact gate before envelopes. Residual: PD2-L3. Strengthened by I-3. |
| **PD-L3** | **Closed in substance.** Ledger, stage, design, code, Python and provenance bound; four stages UNIMPLEMENTED; other coverage NOT enumerated. Residuals: PD2-L1, PD2-L2, PD2-L5. |
| **PD-L4** | **Closed as prerequisite.** Reported `maxrss` target, physical error unknown, no invented bound. |
| **PD-L5** | **Closed.** |
| **R1 informational: confirmation IDs** | **Closed at QuietWindow**, which requires a nonblank ID ≤ 128 characters (`bench:651-653`). Other validators unread [U]. |

## Required corrections versus unknown must-run provenance

**Doc and evidence corrections before any accepted declaration (no source change):**
- PD2-M1: a prospective feasibility statement and root's decision, recorded **before the freeze and preflight**.
- PD2-L1 to PD2-L5.

New files go into a new namespace, because the frozen files use exclusive create.

**Source:** this review requires none. A PD2-M1 option (b) would be a bench and case change. That is outside this lane, it is currently excluded by the brief, and it would need its own review and re-freeze before the preflight.

**Unknown must-run provenance (prerequisites, not defects):**
- **CPU:** actual kernel, accounting mode and `q_total` for the reported total, plus helper and float conversion. Otherwise all 26 CPU rows are unassessed.
- **RSS:** `maxrss` semantics and unit, and helper serialization (`phase7_spawn.c` unread).
- **Idle:** actual `hz`, the two-floor sum model, a monotone basis, the snapshot-within-interval property, and actual bracket availability (PD2-M1).
- **Sources:** the final reviewed collector freeze, and recomputation of the ledger `definition_sha256` against it.
- **Runtime:** collector, reducer and coordinator Python identities (I-4).
- **IDs:** the actual ID registry.
- **Raw CIs:** real-report bit-exact raw-CI reproduction.
- **Gates:** sentinel and control evidence; latency diagnostic and interpret eligibility.

## Fitness

**Yes, prospectively fit as a proposal, subject to:**
- the corrections above;
- root's explicit PD2-M1 decision before the freeze;
- the listed provenance prerequisites.

Its rules cannot produce a false pass from missing, ineligible, invalid or zero-reference evidence. A truthful run may end with many unassessed rows. That outcome must be reported as such, never repaired by changing rules after results.

This is not root acceptance. It must not be forwarded to the collector owner as an accepted binding. Admission remains false.

## Files written

- this report;
- `/home/ajs/.cache/pb-phase7/review-performance-decisions-opus-r2/evidence.json`.

The failed shell may or may not have created `…/review-performance-decisions-opus-r2/tmp` [U]. No commit was made and no other file was edited. **Zero processes are active.**
