# Phase 7 performance decision protocol: independent Opus review, round 4

Reviewed 2026-10-15 in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (live HEAD `ea70f7a`, no commit). This is a fresh, narrow closure review of the R4 proposal. It covers the R3 findings PD3-L1/L2/L3, I-3/I-7 and root's P1 design disposition. It inherits no acceptance from R1–R3, and it does not review the separate root-owned under-30-minute execution design.

**Subject. I computed these hashes and they match the supplied values:**
- `docs/phase7-performance-decisions.md`: `cf0023f6…6c7f`. I read all 201 lines.
- In `/home/ajs/.cache/pb-phase7/performance-decisions-r4/`:
  - `artifact-manifest.json`: `482bcb6e…1605`;
  - `declaration-pending.json`: `c3ccd58f…dc27`;
  - `decision_math.py`, the tested code: `8161b57c…04e98a`.

**What this review is not.** It accepts no protocol, evidence, preflight, admission, candidate, matrix or runtime binding. It authorizes no execution. I edited no author file, source file, harness file or frozen artifact.

## Verdict

**Accept the R4 conditional protocol corrections as closing PD3-L1, L2 and L3, I-3, I-7 and P1 at the design level. There is no medium or blocking finding.**

**P1.** I accept the published adjusted reported-state target, X = A_u + A_s. The doc and the ACTIVE record state the following explicitly, and I found no overclaim:
- reads can advance publication, and publication can use stale or mixed execution inputs;
- the target is not physical idle CPU, and not a fresh simultaneous scheduler sum;
- correspondence to the running `7.2.5-3-omarchy` kernel is unverified;
- every actual CPU, RSS, idle, q, hz, snapshot and bracket field is NULL and fails closed.

I also accept the capture-scope separation. Descriptive functional capture and preflight, and truthful absolute reported counters, may proceed under their own controls. They give no budget pass, no noninferiority result and no ranking.

**Four new low findings.** They are hardening and wording items:
- **PD4-L1, L2, L3:** the same caller-error class as R3. An input the doc requires can be omitted, and the code then fails open.
- **PD4-L4:** a wording gap in the user's under-30-minute limit.

None of them changes the mathematics, the margins, the counts or the closed P1 target. Fix them, or record them as derived-record obligations, before an accepted declaration binds `decision_math.py` as coordinator code. They do not block the review-proposal freeze.

## Execution, honestly reported

**No EDQUOT occurred.** Every shell ran normally. All except the first used redirected `TMPDIR`/`GOTMPDIR`/`CLAUDE_CODE_TMPDIR` and `PYTHONDONTWRITEBYTECODE=1`. The first was an `ls`/`sha256sum`/`wc`/`df`/`git log` batch.

**What I executed:**
- **Hashing:** `sha256sum` of the subject files and key cache files.
- **Read-only inspection:** small `python3 -I -B` reads of `coverage-design.json`, `final-binding-checks.json` and `r3-preservation.json`.
- **Two bounded, guarded, pure-stdlib reviewer scripts**, written and run only inside my cache:
  - an audit hook blocked subprocess, fork, exec, socket and ctypes calls, and any writes outside my cache;
  - **result: exit 0, zero forbidden events, empty stderr.**

**What I did not run:**
- the author's 623-check test, which writes into the author cache;
- the 422-check finalizer;
- any repository or harness import, native helper, CLI, probe, compiler, build, profiler, common lock, timing, preflight or campaign.

**Labels:**
- [S] source or file as read;
- [E] executed by me, pure or synthetic;
- [A] manual arithmetic;
- [O] supplied, not verified.

**Read in full:**
- the subject doc;
- the R3 review;
- `phase7-accounting-prerequisites.md`;
- in the R4 cache: `README.md`, `decision_math.py`, `test_low_corrections.py`, `resource-provenance-active-pending.json`, `P1-root-design-disposition.json` and `findings-response.json`;
- `harness/phase7_bench.py:1296-1343` and `harness/phase7.py:1564-1580`.

**Read in part:**
- `finalize_review_proposal.py`, at its check lines;
- `matrix-and-confirmation-design.json:2125-2150`;
- `declaration-pending.json`, by grep and at lines 2660-2670 only;
- ledger structure and rows, by script.

**Not read:**
- the manifest body, beyond its hash;
- `P1-research-binding.json` contents;
- the kernel sources.

**Preservation [E].** All 354 `r3-preserved` copies match their recorded sizes and SHA-256s. 345 of the original paths are byte-identical today.

The 9 that differ are:
- the owned doc;
- `Cargo.lock`, `Cargo.toml`, `src/{lib,transport,diagnostics}.rs`;
- `harness/phase1.py`, `test_phase1.py`, `test_phase4.py`.

`phase7.py`, `phase7_bench.py` and `phase7_spawn.c` are unchanged since R3. This matches root's report that the collector and helper are unchanged while the overall snapshot moved [O].

## Closure of the R3 findings

| R3 item | R4 status |
|---|---|
| **PD3-L1** | **Closed for the R3 failure mode itself** [E]. A `potentially-slower` row now forces the whole case to pending, even with `triggered=False` (`decision_math.py:399`). `case_confirmation_trigger` (`:519-585`) ORs four signals before eligibility: canonical points, retained source AND, strict raw-lower and the potentially-slower label. Exact ties behave as the doc says: a raw lower bound of exactly 1/4 pp gives no idle trigger, and 1/4 + 10⁻¹² does trigger. A binary-float lower ratio of `1.1` counts as strictly above 11/10. All 50 ledger case/policy groups pass `_decision_margins`, and the 18 duplicate rows are rejected [E]. **Residual: PD4-L1.** |
| **PD3-L2** | **Closed** [E]. Both *final*-ledger screen rows resolve to exactly one same case/policy/hash/Go–Rust `full_idle_bracketed_cpu_pp` decision row. The signal fires at exactly 1/4 pp and not at 1/4 − 10⁻⁹. Unrelated descriptive and A/A evidence gains no scope. |
| **PD3-L3** | **Closed for free q** [E]. `cpu_total_envelope` has no q parameter (`:658`). `rss_envelope` derives its allowance from ACTIVE hash-bound provenance and enforces raw-CI parity (`:669-687`). The pending ACTIVE file fails CPU, RSS and idle closed. Requiring a positive CPU q, and allowing zero RSS allowance only with verified exact conversion, are acceptable conservative choices. **Residuals: PD4-L2 (idle) and PD4-L3 (CPU source parity).** |
| **I-3 / I-7** | **Closed** (`decisions.md:17`, `:187`). Note that both final idle-row statuses now read "M1 source applied historically, P1 design selected…" (see I-R4-1). |
| **P-1 / P1** | **Design disposition accepted.** See below. |

## Findings

### PD4-L1 — Low: trigger inputs are still optional or incomplete, so an omission fails open [S][E]

`case_confirmation_disposition` treats `trigger_evidence` as optional (`:395`, `:400`). Without it, only a `potentially-slower` label is derived. `case_confirmation_trigger` also accepts silently a decision row that has no evidence record: the row defaults to `unassessed` with no signal (`:535`).

Demonstrated [E]:
1. **No trigger evidence passed.** One row is `inconclusive` because it has a point crossing, R and D above their margins, while its CI spans the margin. With no `trigger_evidence` passed, every companion row comes back final and `empirical_satisfied`. Passing the same row's rounds triggers the confirmation instead.
2. **Crossing row's evidence omitted.** If that row's evidence record is left out of `case_confirmation_trigger`, the result is `confirmation_signal=False`, and no error is raised.
3. **Source flag alone is dropped.** A retained source `True` flag is discarded unless rounds or a ratio CI are also supplied (`:573-574`). However, the source sets `True` only when **every** reference is > 0 (`phase7_bench.py:1320-1321`), so that flag cannot mean a zero reference. The doc says source flags are "retained and acted upon" (`decisions.md:53`).

**Consequence.** These are the PD2-L4/PD3-L1 closure asymmetry again, reached through caller omission. The crossed row itself still cannot pass.

**Minimal fix:**
- Make `trigger_evidence` mandatory, at least whenever the status is `not-required`.
- In `case_confirmation_trigger`, require exactly one record per declared decision row. Each record must carry either the complete validated rounds or an explicit `unknown_reason`.
- For primary latency, either treat a retained source `True` as sufficient, or reject it when rounds are missing, rather than dropping it.
- Hygiene, conservative direction: reject `retained_source_crossing` on `native_cpu_total_ms`, because no source total flag exists.

### PD4-L2 — Low: the idle path did not get the L3/P1 hardening [S][E]

`require_idle_accounting_provenance` (`:438-451`) is unchanged from R3. It checks four Booleans, plus a truthy `kernel_counter_semantics_provenance` and an integer `hz_actual`. Unlike the R4 CPU and RSS gates, it does **not** check any of the following:
- that `evidence_kind` is `actual`;
- that the selected P1 target or `accounting_target_name` is present;
- that `actual_build_source_correspondence_verified` is True, a field the P1 disposition itself names as null;
- that any of the evidence is hash-bound.

**Demonstrated [E].** I built a record with `evidence_kind='pending'`, target "physical CPU executed during idle", null correspondence and an unbound text string, then set the four Booleans to True. The gate returned `hz=100`.

**Further gaps:**
- `idle_brackets(samples, start_ns, end_ns, hz)` takes a free `hz`, and there is no ACTIVE-provenance idle entrypoint. This is the idle analogue of PD3-L3's free q.
- The ACTIVE file carries two tick-scale fields, `hz_actual` and `actual_hz`; the gate reads only the first.

The current pending file still fails closed, because snapshot is null. This is future-record and caller hardening, not a present fail-open.

**Minimal fix.** Add `full_idle_envelope(role_samples, S, E, declaration, seed, draws, *, synthetic_test=False)` that:
1. reads ACTIVE provenance;
2. requires `evidence_kind=='actual'`, unless in explicit synthetic mode with no eligibility;
3. requires target = `accounting_target_name` = the P1 `selected_target`, with the P1 disposition identity hash-verified;
4. requires `actual_build_source_correspondence_verified is True`;
5. hash-binds the kernel semantics and correspondence evidence through `_bound_evidence`;
6. requires a single hz field, equal to each role's retained collector `SC_CLK_TCK`;
7. then runs `idle_brackets` for all 60 pool roles, followed by `bootstrap_envelopes(ratio=False)`.

### PD4-L3 — Low: the CPU entrypoint skips the source component-CI reproduction the doc puts first [S][E]

The doc says to compute total CPU "only after reproducing the SOURCE component CIs" (`decisions.md:38`). `rss_envelope` enforces raw parity, but `cpu_total_envelope` has no reproduced-summary input (`:658`) and never calls `require_raw_ci_reproduction` or `require_delta_ci_reproduction` for `user_cpu_ms` or `system_cpu_ms`.

**Consequence.** A provenance-gated CPU envelope can come from component rounds that do not reproduce the source report.

**Minimal fix.** Add a required `reproduced_source_component_summaries` argument and check each component along the source's own branch (`phase7_bench.py:1304-1310`):
- paired bootstrap when all references are > 0;
- otherwise, delta-only `session_statistics`.

### PD4-L4 — Low, wording: the strict under-1,800-second limit does not say it includes cleanup [S]

The user's limit is that every run finishes strictly under 1,800 seconds, **including cleanup**. The doc lists "preflight, diagnostic, campaign, A/A, confirmation and reducer work" (`:147`; also `:189`), and `run_limit` in `matrix-and-confirmation-design.json:2136-2143` has no span definition. Neither says that teardown, child reaping, end identity checks or lock release fall inside the bound.

**Minimal fix.** Define the measured span as running from the first approval-bound preparation action to completed cleanup, reaping, end checks and common-lock release. Any run reaching 1,800 seconds anywhere in that span is inadmissible.

### Informational

- **I-R4-1: the test evidence predates the final ledger [E].** The 623-check run predates P1: its I7 predicate, `'M1 remedy source applied' in status` (`test_low_corrections.py:210`), is **False for both final idle rows**. The script also cannot be rerun in place, because it writes the existing fixture names in `'x'` mode.
  - The code is byte-unchanged, so its behavioural checks still apply.
  - The 422-check finalizer re-verifies the screen links and the target on the final ledger (`finalize_review_proposal.py:105-110`), but not that predicate.
  - Suggestion: update the predicate and parameterize the output directory. This is non-blocking.
- **I-R4-2: P1 math re-derived [A][E].** The conditional chain holds:
  - for retained read j, the lock instant t_j ∈ [s_(j−1), s_j];
  - X is monotone;
  - n = ⌊A_u/q⌋ + ⌊A_s/q⌋ gives X/q − 2 < n ≤ X/q;
  - together these give `n_k−n_i−2 < Δ/q < n_a−n_b+2`, which is exactly the frozen L and U.

  **Randomized check [E].** 3,000 synthetic trials used monotone published step pairs, with reads inside serial call intervals and stamps after the reads. All 3,000 were assessed, and there were **0 violations**.

  **Discrimination [E].** The same model with the ±2 removed gives 362 violations, so the zero-violation result discriminates. My model did **not** produce a violation for the unsafe `s_a ≥ E` outer-after rule, so that rule's danger is shown by source reasoning only, not by this simulation.
- **I-R4-3: P1 wording accepted [S].** The doc (`:79`, `:193-201`) and the ACTIVE idle record name:
  - the published-state definition;
  - read and publication dependence, including other consumers;
  - whole-thread-group scope;
  - USER_HZ 100 only as a header candidate;
  - CONFIG_HZ 1000 as no bound;
  - unverified tag signature and Omarchy correspondence.

  Nothing promotes upstream v7.2.5 to a host fact. The design adopts no shifted bracket, staleness delta or q. This is a correct fail-closed outcome, and needs no further inventory.

## Remaining prerequisites (not defects, unchanged)

These remain open:
- actual Omarchy source/patch correspondence and CPU/RSS/idle provenance, including q_total, hz and snapshot;
- actual post-E brackets;
- fresh combined candidate, source, runtime and final matrix/ID bindings at the post-`ea70f7a` state;
- actual matching preflight;
- real-report raw-CI reproduction;
- eligibility and sentinel evidence;
- a validated, enforced plan that keeps every run strictly under 30 minutes;
- root adjudication.

Until these exist, CPU, RSS and idle rows stay unassessed. That is correct.

## Files written

- this report;
- `/home/ajs/.cache/pb-phase7/review-performance-decisions-opus-r4/`, containing:
  - `review_checks.py` (`8177c87d…a424`) and its output `review-checks.json` (`b2550817…420d`), with stdout and stderr logs;
  - `discrimination_check.py` (`b4b97b30…eb8a`) and its output `discrimination-check.json` (`940d2ed6…b2a1`);
  - `evidence.json`;
  - an empty `tmp/`.

No other edits, no commit and no messages. **Zero processes are active.**
