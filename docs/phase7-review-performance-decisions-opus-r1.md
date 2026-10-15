# Phase 7 performance decision protocol: independent Opus review, round 1

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (base `4a812d5`, Go pin `07af390`).

**Subject.** The proposed coordinator protocol `docs/phase7-performance-decisions.md`:
- declared SHA `a5a16114…08274` and 31,142 bytes. Both are **supplied [O]; I computed no hash.**
- its static design evidence under `/home/ajs/.cache/pb-phase7/performance-decisions-r1/`.

**What this review is.** A fresh review of the DECISION PROTOCOL only. It is not a code audit, a source-freeze acceptance, a phase acceptance or a readiness statement.

**What it is not.**
- It grants no admission, window, approval or self-permission.
- The R8 source-owner edits are in progress and not frozen. Nothing here implies acceptance of the final source.
- I edited no proposal, source, test, contract or historical file.

## Verdict

**The skeleton is sound and executable in principle**, and I would accept it with corrections:
- the ordered classification;
- the paired-median estimands;
- AND-conjunction non-inferiority on upper bounds;
- strict lower-bound slowing confirmed by exactly one fixed-seed confirmation;
- missing, ineligible or invalid rows labeled unassessed, never passed;
- a new hash-bound derived record that leaves the collector's `budget_pass=None` / `accepted_trigger=False` untouched;
- the +10,000,000 confirmation offset, with whole-pool shift for long idle;
- A/A conditioning by the four dark SQL-editor sentinels, with no calibration claim.

Root's stated directions are adopted faithfully, and none of them is a defect.

**Four medium findings** are about the author's added details. Each is a doc/ledger-only correction to make before declaration. **None needs a bench change**, so the preflight `source_snapshot` is unaffected.
- **PD-M1:** the 18 long-idle latency "obligations" invent a permanent closure blocker outside the authorized scope.
- **PD-M2:** the CPU uncertainty model targets the wrong quantity, the per-component user/system split. That likely makes all 26 CPU rows unassessable.
- **PD-M3:** the full-idle coverage envelope `100·C·g/T` is sound but probably vacuous on a multi-core host.
- **PD-M4:** the confirmation trigger set and the post-confirmation scope are underspecified. Uncrossed rows in a confirmed case could be closed while ignoring adverse confirmation data.

**Five low findings and some informational notes** follow below.

**Recommended prospective family scope** (PD-M1): 394 latency + 52 session-resource + 2 long-idle CPU = **448** decision rows. The 18 long-idle latency phases are retained as declared descriptive rows.

## Execution and evidence limits

**Execution:**
- I made **one** shell attempt, `wc` on the roadmap file. I did **not** prefix it with the instructed `TMPDIR`/`GOTMPDIR`/`CLAUDE_CODE_TMPDIR` exports. That is a procedural deviation, and I disclose it.
- It failed with EDQUOT on the Claude Code task temp directory, and its output was lost. I made no retry and used no symlink workaround.
- After that I used file tools only.

**Not run:**
- no tests, pure, mock or otherwise;
- no `static_design_checks.py` (read only);
- no arithmetic script, native, helper, CLI, build, probe, profiler, timing or lock.

**Not computed:** no SHA-256. Every digest in this report is **supplied [O]**.

**Root pause:** after root's coordinated execution-pause request, I confirmed zero active owned processes. I remain paused until root explicitly releases execution.

**Labels:** [S] current source as read; [K] kernel or Python semantics, not exercised; [O] supplied; [U] unknown; [I] inherited from a prior review or disposition; [A] my manual arithmetic, not executed.

**Supplied, not verified by me [O]:**
- R7 root central checks: 99 bench and 24 profile.
- R8 source-only result.
- release5 compatibility counts: 3268+6, 160+30, 17, 564+4+4.
- frozen production and helper unchanged.
- the author's 90 static passes (I tallied 90 `check` call sites in the script by reading; I did not run it).
- manifest and file digests verified by root.

These are separate evidence. They are not performance evidence.

### Read scope

**Read in full:**
- `docs/phase7-performance-decisions.md`
- `/home/ajs/.cache/pb-roadmap/phase7-final.md`. Its historical starting state is superseded by the merged phases 1–5 and the current implementation.
- `docs/phase7-review-measurement-opus-r8.md`
- `docs/evidence/phase7/tooling-r8-disposition.json`
- `docs/evidence/phase7/tooling-r7-disposition.json`
- the original brief `/home/ajs/.cache/pb-phase7/tooling-r6-review-brief-prepared.txt`
- from `performance-decisions-r1/`: `README.md`, `declaration-pending.json`, `static_design_checks.py` and `decision-record-template.md`

**Read in part:**
- `performance-decisions-r1/matrix-and-confirmation-design.json`: lines 1–110 (base counts, all four A/A sentinels, the first confirmations).
- `performance-decisions-r1/coverage-design.json`: lines 1–140 (header and the first nine phase inventories).
- `performance-decisions-r1/arithmetic-checks.json`: lines 1–12. I tallied the count of 90 against the script's `check` calls by reading.
- `/cache/collector-r7-corrections/command-matrix.json`: lines 1–200, 330–459, 15900–16019, 17400–17819 and 18300–18500. These cover preflights, pool plans, the first campaign, interpret and diagnostic entries, `optional_descriptive` flags, the coordination rule, not-applicable slots and `latency_disposition`.
- `harness/phase7_bench.py` (current, in-progress bytes):
  - 1–399: options, `POOL_COUNTS`, `Phases`, all case builders, `campaign_cases`;
  - 880–1124: `observation_issues`, `distribution`, `resource_interval`, `extract_metrics` head;
  - 1240–1369: `host_snapshot`, `paired_summary`, `descriptive_latency_decision`, `summaries`, control-RNG head.
- `harness/phase7.py`:
  - 1–80;
  - 900–1009: the `execute` loop, showing the 50 ms sampler cadence;
  - 1480–1619: `load_session`, `session_statistics`, `paired_session_statistics`.
- `docs/phase7-measurement.md`: 1–240. This includes the historical coordinator investigation policy at 202–239.
- `docs/phase7-harness.md`: 1–120.
- `docs/phase7-review-measurement-r1.md`: 1–80 (H1–H3, M1).
- `docs/phase7-review-measurement-opus-r7.md`: 1–90 (verdict and scope).

**Not read, so no coverage is claimed:**
- Prior reviews: `phase7-review-measurement-interim-sol-r2.md`, `-opus-r3.md` to `-opus-r6.md`, the rest of R1 and R7, and the spawn-helper reviews.
- Responses: `tooling-r6-disposition.json`, every `docs/evidence/phase7/collector-*` response or freeze file, and the cache response JSONs.
- From `performance-decisions-r1/`: `source-read-identities.json`, `execution.json`, `artifact-manifest.json`, `static-check-initial-failure.json`, `draft-first-static-pass/`, and the remaining lines of the two design JSONs.
- The remainder of the matrix, so the full 84-seed list is [O] (author extraction).
- `phase7_spawn.c`, so the six-decimal serialization is [O].
- The rest of `phase7.py`, `phase7_bench.py` and `measurement.md`; all tests; `phase7-profiling.md`; `rust-phase5.md`; the contracts.

**Inherited [I]:**
- R1 native RSS floor, zombie and request-poll fixes.
- R2 provenance, failure and resource fixes.
- R3 dispatch origin.
- R4 resize, A/A and pooling.
- R5 actual-session decoder.
- R6 streaming and screen thresholds (≤1 ms absolute and matched, equal counts, pilot, finite budgets).
- R7 cap/cap and nine chunks.
- R8 M1/M2/L1–L3 status.

All of these come from the brief, R8 and the R7/R8 dispositions. I re-checked them only where the lines listed above touch them. Known helper limitations are preserved and not re-litigated: fatal-supervisor unknowns, escaped descendants, TOCTOU, cross-platform.

## Findings (most severe first)

### PD-M1 — Medium, scope: the 18 long-idle latency "obligations" are an invented, permanently unsatisfiable closure blocker [S]

**What the protocol says:** "All 412 are obligations; the 18 long-idle latency rows currently have no eligibility path" (`decisions.md:26`). They are a "closure blocker" (`:73`, `:121`). Step 7 forbids even administrative budget closure while any required row is unassessed (`:110`).

**Why these rows are not a separate obligation:**
- **Same workload as an existing case [S].**
  - The long-idle case is `sql_case(largest, …, idle=options.long_idle_seconds)` (`bench:392`).
  - All nine of its latency phases (`startup … resize-original`) are built by exactly the same calls as `sql-scroll-20000`, with the same fixture and zero delay (`bench:212-250`).
  - The only differences are the trailing `phases.idle(...)` (`:251`) and the timeout.
  - No measured input follows the idle; only `quit` (`:252`). There is no post-idle wake latency to measure.
  - Every one of the 18 rows therefore duplicates a primary row of `sql-scroll-20000-120x40-{dark,light}`, which carries its own 5+30 campaign and potential diagnostic.
- **Contrary to declared scope [S][I]:**
  - The R7 matrix marks all 16 long-idle primary diagnostics and interprets `optional_descriptive: true` (matrix 15916–16019 and 17523–17673).
  - The R5 approved design made the long-idle pool latency explicitly `latency_trigger_eligible=False`.
  - The original policy says "Add separately labeled five-minute idle sessions" for idle and leak observation (`measurement.md:210-211`).
  - The roadmap asks to "exercise … long idle sessions" alongside idle CPU and RSS (`phase7-final.md:71`), not for a separate latency budget.
  - Root states that no new pooled latency interpretation is authorized.

**Trigger:** any execution of the protocol as written.
**Consequence:**
- The latency family can never reach closure, whatever the results.
- Root would be forced into an "explicit tradeoff" for rows that measure nothing new, or into a later amendment that looks like post-hoc deletion.
- Under PD-M4's ambiguity, a descriptive pooled-latency crossing could also schedule a whole nine-chunk pool confirmation.

**Minimal fix, a prospective family-scope alternative declared before any data:**
1. The latency decision family is the primary Go–Rust `*_first_expected_ms` of the **24 non-long variants**: 197 per theme, 394 rows.
2. The 18 long-idle pre-idle latency rows stay in the ledger as `descriptive: duplicate pre-idle workload of sql-scroll-20000-120x40-<theme>, whose own row carries the obligation; long-idle pool is a resource experiment (R5/R7 matrix optional_descriptive)`.
3. Their crossings open a labeled descriptive investigation note only: no confirmation and no closure dependency.
4. Long-idle session CPU and RSS (4 rows) and long-idle idle CPU (2 rows) remain obligations.
5. Total decision rows: 394 + 52 + 2 = **448**.

Because this is fixed before measurement and keyed by case-definition hash, it is a scope declaration, not result-driven deletion. `coverage-design.json`, the doc phrases checked by the static script ("412 total", "466 decision rows", "all 18 long-idle latency") and the template must be regenerated into a new directory. The script uses exclusive create.

**Informational consequence:** under either wording, the two **primary** long-idle pools (18 of the 84 campaigns) carry no decision row. Keeping them, as the frozen program requires, is root's choice. The protocol must not silently drop them.

**Root disposition:** not acceptable as written. Accept with the 448-row alternative.

### PD-M2 — Medium, resource resolution: CPU uncertainty targets the per-component user/system split, not the total being judged [S][K]

**What the protocol says:**
- Root must bind "an independently justified upper error per reported user and system component" (`:57`).
- `q` is "the sum of the two component error bounds" (`:59`).
- It explicitly refuses to treat reported precision as accounting resolution.

**Why this targets the wrong quantity [K], to be confirmed from the actual kernel configuration:**
- On Linux tick-based and VIRT_GEN accounting, `wait4` child rusage comes from `thread_group_cputime_adjusted` → `cputime_adjust` (`kernel/sched/cputime.c`).
- That function scales the tick-sampled user/system proportion so that `utime + stime = sum_exec_runtime`. The **split** carries tick-sampling error. The **total** is the scheduler's runtime, reported with microsecond `timeval` truncation per component.
- The helper's six-decimal seconds add ≤1 µs per component [O].
- So the total's representation error is a few µs. The per-component split error is not tightly boundable at all: for a short process it can approach the whole total.

**Trigger:** root follows `:57` as written.
**Consequence [A]:**
- Even an optimistic one-kernel-tick bound per component (4 ms at `CONFIG_HZ=250`) gives q = 8 ms. That is up to 16 ms of paired-delta widening, 16× the 1 ms margin.
- No CPU row could ever be within margin, and the "no defensible bound → unassessed" branch would likely fire for all 26 CPU rows.
- The asymmetry is also inconsistent: RSS is judged as a reported counter target with zero representation error (`:59`), but CPU is judged against unknown physical truth.

**Minimal fix (doc-only):**
- Define the CPU target as the kernel-reported `wait4` total, `user + system`.
- Bind `q_total` from documentation provenance for the actual kernel's accounting mode: config option plus the `cputime_adjust` source reference for kernel 7.2.5. That gives timeval truncation plus serialization, about ≤4 µs.
- Keep the user and system components descriptive.
- If the actual configuration does not preserve the sum (for example, an unexpected accounting mode), CPU stays unassessed.
- This is documentation provenance, not a new calibration experiment.

**Root disposition:** "CPU kernel-bound prerequisite" is acceptable in principle. Its target must be the total.

### PD-M3 — Medium, idle coverage: the full-interval envelope `100·C·g/T` is sound but probably makes both idle rows structurally inconclusive [S][A]

**Formula check:** the formula is correct and conservative.
- `u`, `t` and `e = 200/(CLK_TCK·t)` match `resource_interval` (`bench:1021-1046`).
- The two-tick endpoint bound is right because `user_ticks` and `system_ticks` are each floored before summing, giving a difference error in (−2, 2) ticks.
- `T` is the ready-to-ready span; `g = T − t = unobserved_start_ms + unobserved_end_ms`, both retained.
- C·g bounds the unsampled CPU.

**Why it is probably vacuous [S]:**
- The sampler cadence is `next_sample = now + 50_000_000` inside a `select(0.01)` loop (`phase7.py:927-937`). So the period P is ≥50 ms, and g ≈ P on average and up to about 2P.
- C is the logical CPUs available to the role, recorded per pair by `host_snapshot` (`bench:1255-1260`). The host's C is [U] to me.

Coverage term alone, at T ≈ 300 s [A]:

| g \ C | 1 | 4 | 8 | 16 |
| --- | --- | --- | --- | --- |
| 0.06 s | 0.02 pp | 0.08 | 0.16 | **0.32** |
| 0.12 s | 0.04 | 0.16 | **0.32** | **0.64** |

- On top of that come 2e ≈ 0.0133 pp and the raw upper delta.
- With C ≥ 16, "within 0.25 pp" is impossible. At C = 8 it is marginal.
- The term enters through the candidate role's upper endpoint only, so a quiet Go reference does not offset it.

**Consequence:** both idle obligations are likely permanently inconclusive because of the analysis bound, not the evidence.

**Minimal fix (prospective; uses only retained fields).** Replace the C·g term with **bracketing samples** from the retained `proc_samples`. These run for the whole session, not only the idle interval.
- **Lower bound:** inner samples, as now: `(Σticks(last_in) − Σticks(first_in) − 2)/hz/T`, floored at 0. This equals the doc's `max(0, u−e)·t/T`.
- **Upper bound:** outer samples: `(Σticks(first_at_or_after_end) − Σticks(last_at_or_before_start) + 2)/hz/T`.
  - This deliberately over-includes up to one sample period of the adjacent stable-hold and quit activity.
- **If no sample follows idle-end** (the `quit` is sent immediately and exit may precede the next sample), declare the row unassessed, or declare beforehand a `wait4`-total end bracket. That bracket depends on proc ticks and `wait4` sharing the same adjusted-runtime basis [K] and must be root-bound.
- **Stamp vs read time:** check that each sample's `monotonic_ns` precedes its `/proc` read, or otherwise account for the read-after-stamp ordering [U].
- **Alternative if root keeps C·g:** compute the worst-case term from the declared P and C **before** declaration, and record that the idle family is expected to be inconclusive on this host.

**Root disposition:** "full-idle coverage uncertainty" is sound but not useful as written. Accept with bracketing, or with an explicit prospective feasibility statement.

### PD-M4 — Medium, confirmation semantics: the trigger set and post-confirmation scope are underspecified [S]

**(a) Which crossings schedule a confirmation campaign?**
- `:47` says "Every descriptive two-arm crossing … requires the fixed-seed confirmation, regardless of eligibility".
- The next sentence says stable-ready, attribution-latency and pooled-latency crossings open "a labeled observer/measurement investigation".
- Step 6 issues "one conditional confirmation per crossed case/policy or whole pool".
- The source (R8-L2 change in progress) labels those contexts "descriptive investigate note … no candidate-regression decision" (`bench:1307-1316`).
- **Trigger:** a stable-ready crossing on a dense case, an attribution-latency crossing, a primary CPU/RSS crossing, or a pooled-latency crossing.
- **Consequence:** it is ambiguous whether this books a confirmation campaign, or a nine-chunk pool. Across about 1,000 descriptive series, the conditional workload is unbounded and possibly near the whole program.

**(b) What happens to the uncrossed rows of a confirmed case or policy?**
- The two-run rule is phrased per crossed metric (`:40`, `:49`). Yet a confirmation campaign yields fresh data for every row of that case and policy.
- **Trigger:** `sql-scroll-20000-sustained-120x40-dark` (83 phases) crosses at `window-3`. Its confirmation shows `window-7` potentially slower, even though `window-7` was within margin in the original.
- **Consequence:** under the current text, `window-7` closes from the original alone and the adverse confirmation evidence is ignored. That contradicts "keep both runs rather than discarding the inconvenient one" (`phase7-final.md:108`).
- Similarly, an interrupted or invalid confirmation is inconclusive only for "the decision". Uncrossed rows could still be closed from the original, so a functional failure in the confirmation could be silently sidelined.

**Minimal fix (doc-only):**
1. Only **decision-family** crossings schedule a confirmation: primary first-expected latency regardless of eligibility, attribution session CPU/RSS, and long-idle attribution idle CPU (whole pool). All other crossings, including descriptive primary CPU/RSS, stable-ready, attribution and pooled latency, open a recorded investigation note only.
2. Once a confirmation campaign or pool exists for a case and policy, **every** decision row of that case and policy is classified by the two-run rule.
3. A crossing that appears only in the confirmation makes its row inconclusive, opens an investigation, and allows no third run without a prospective amendment.
4. A failed, interrupted or invalid confirmation leaves **all** decision rows of that case and policy inconclusive.
5. Any compatibility failure in a confirmation is reported as compatibility evidence, never discarded.

### PD-L1 — Low, category agreement is stricter than the budget question requires [S][A]

- "faster" implies "within margin": `upper95(R) < 1 ≤ 1.10` and `upper95(D) < 0 ≤ A`. The categories are nested, and NI and "potentially slower" are disjoint.
- Yet `:49` and `combine()` (`static_design_checks.py:67-70`) make faster-vs-within-margin "inconclusive" for the budget.
- **Consequence:** combined with the PD-M4 fix, an uncrossed row of a confirmed case that shows NI in both runs (one of them faster) fails its budget for no inferential reason.
- **Fix:**
  - The budget is satisfied when both runs satisfy the NI inequalities.
  - The label "faster" requires both runs to be faster; otherwise the label is "within margin".
  - "Confirmed slower" still requires strict lower bounds in both runs.
- **Root disposition:** strict agreement is acceptable for the faster and slower labels, but not for NI budget satisfaction.

### PD-L2 — Low, the bootstrap seed and draws for coordinator-derived quantities are not bound [S]

- The source bootstraps with `options.seed` (the campaign seed) and `options.bootstrap_draws` (default 2000) (`bench:1286-1295`, `117`). For pool reports it uses the analysis seed, base + 100,000.
- The protocol says only "seeded random.Random … Record seed" (`:32`). The CPU total and the envelope bounds are computed by the coordinator, not the source, so the coordinator could choose the seed.
- **Fix:**
  - Bind the seed to the validated report's `definition.options.seed`, or the pool analysis seed, with draws from options; the shifted seeds apply for confirmations.
  - Use index draws from `random.Random(seed).choices(range(30), k=30)`, which equals `choices(rounds, k)`.
  - Before computing any envelope, reproduce the source's raw latency CIs bit-exactly from the same report.

### PD-L3 — Low, declaration integrity: the declaration binds only the doc and the matrix [S]

- `declaration-pending.json` binds the doc and matrix identities only. It omits:
  - the coverage ledger, which is the authority for obligations;
  - the confirmation and seed design;
  - the coordinator computation implementation and its Python version;
  - the CPU and RSS provenance records.
- Also, `:28` says additional roadmap obligations "remain separately enumerated", but they are not yet enumerated (`coverage-design.json` says root "must" enumerate them).
- **Fix:**
  - Bind all of those identities in the declaration before any timing.
  - Reword `:28` to "NOT yet enumerated; root must enumerate before any coverage-completeness claim".
  - Keep internal-stage timing (event receipt, reducer, render, write) listed as an unimplemented original requirement. Do not invent a validation for it.

### PD-L4 — Low, RSS "zero representation error" needs kernel-specific provenance [K][U]

- The KiB→bytes conversion is exact. But since about kernel 6.2, mm rss counters are per-CPU counters, and hiwater updates may use approximate reads.
- The error can reach per-CPU batch × CPUs pages per counter, about 2 MiB at 16 CPUs. It could differ systematically between a widely multithreaded Go process and Rust.
- Whether kernel 7.2.5 still behaves this way is [U] to me.
- **Fix:**
  - Root's RSS provenance (`:59`, already a declared prerequisite) must address this for the actual kernel. Otherwise RSS stays unassessed.
  - State that decisions concern the kernel-reported `maxrss` counter, which the doc already notes for physical peaks.

### PD-L5 — Low, scope of the sentinel release [S]

- A/A sentinels run in their own windows, while the 84 campaigns span many windows.
- `:86` lets one fresh non-crossing control release the block after an explicitly resolved investigation.
- **Fix:** add three sentences:
  1. The release does not validate campaigns run under an identified host or observer cause; their decisions stay withheld absent prospective re-collection.
  2. A crossing fresh control keeps the block, with no further run without amendment.
  3. Sentinel non-crossing has no temporal reach beyond its window; per-campaign host snapshots and floor controls remain the cross-window evidence.
- No new controls are demanded.

### Informational

- **Multiplicity.**
  - "Pointwise exploratory" honors the roadmap's "until implemented" clause (`phase7-final.md:108`).
  - For the **conjunctive** closure claim, the intersection–union principle means requiring every row to pass its own approximately 2.5% one-sided test needs no adjustment. That holds subject to the bootstrap's approximate coverage at n = 30.
  - Multiplicity exposure is real for **individual "faster" labels**: about 2.5% per row under no difference, so roughly 10 spurious labels across about 400 rows. Their count must not be presented as speed evidence.
  - "Confirmed slower" is protected by the second run.
  - Wording at `:115` could say this. It is not a defect.
- **CPU AND-margin consequence [A].** With session CPU of hundreds of ms, the 1 ms absolute arm makes NI practically attainable only when Rust is about equal with tight spread, or faster. Expect many inconclusive CPU rows that need root tradeoff decisions. This follows root's direction and is not a defect.
- **Equality and float semantics.** "Unresolved rounding boundary" (`:45`) is undefined and could become a discretionary lever. Prospectively define comparisons as exact rationals of the computed IEEE values against exact decimal margins (11/10, 10, 1/4, 2,097,152), with no tolerance. Exact ties are practically negligible.
- **Bootstrap endpoint convention.** Indices 50 and 1950 leave 50 and 49 draws outside the interval. This is the inherited source convention; keep it.
- **Seed reuse [A].**
  - The pool analysis seeds 162,000 and 175,000 equal the attribution bases. This is disclosed correctly and is harmless.
  - The campaign seed also drives role order and the bootstrap, and seed+101 drives the controls (`bench:1361`).
  - A modular check, 9k ≡ ±101 (mod 1000) ⇒ k ≡ 789 or 211, finds no seed+101 collision among bases ×1000 and chunk offsets 1009k with k ≤ 8.
  - Confirmation bases (10,062,000 / 10,075,000 / 10,162,000 / 10,175,000) and A/A fresh seeds (12,000,001 … 12,010,002) are distinct.
  - Only the entries I read were checked. The full 84-seed list is [O].
- **Counts check out [A].**
  - 26 × 35 × 2 = 1,820 roles; 42 × 12 = 504 controls; 2 × 2,324 = 4,648 children.
  - 2 × 26 × 30 × 2 = 3,120 measured roles.
  - Diagnostics: 24 potential + 56 optional (24 attribution + 16 + 16 long chunks 2–9) = 80, plus 4 warm-only N/A.
  - Phases: 6 + 8 + 9 + 3 + 3 + 3 + 4 + 9 + 83 + 67 + 1 + 1 = 197, plus 9 long = 206 per theme. This matches `phases()` and source `bench:212-337`.
- **Idle example wording.** "0.24 becomes 0.253333…" (`:67`) is the maximum widening. With a reference below e, `max(0, ·)` shrinks it. Say "up to".
- **Confirmation IDs.** Whether the `--confirm-r1` / `--diagnostic-r1` suffixes satisfy any source campaign-ID pattern is [U]. I did not read the validator; check it before declaring.

## Specific checklist

| Item | Assessment |
| --- | --- |
| Confirmations and order | Sound: one fixed seed, whole pool, no shopping, failure → inconclusive. Gaps: PD-M4 trigger set and scope; PD-L1 nested agreement. |
| Ordered classification | Categories are disjoint (lower ≤ upper); faster ⊂ NI; equality semantics coherent. |
| Zeros and missingness | Consistent with source (`paired_summary` defines a ratio only for all-positive references). Absolute-only cannot pass. Correct. |
| CPU and RSS units | ms and bytes, 2 MiB = 2,097,152, idle in pp of one core. Correct. CPU error target: PD-M2. RSS provenance: PD-L4. |
| Idle T vs coverage, gap C bound | Formula correct; probably vacuous (PD-M3). Retained fields suffice for bracketing. |
| Resolution | Two-tick idle bound correct [K]. CPU and RSS: PD-M2 and PD-L4. |
| CI precision and multiplicity | Pointwise and exploratory, honestly labeled; IUT note informational; seed binding PD-L2. |
| Source-hash protocol | Correctly refuses to treat moving R8 files as frozen; requires a final freeze and rebinding. |
| New decision-record integrity | Append-only, externally hashed, non-self-referential. Missing ledger and implementation bindings: PD-L3. |
| Cross-purpose seed reuse | Disclosed accurately; no global-distinctness claim. Acceptable. |
| Partial confirmation | Underspecified for uncrossed rows: PD-M4(b). |
| Exact program | 84/4,648/104/3,120/80 (24+56+4 N/A) preserved; conditional confirmations kept outside the counts. Correct. |
| A/A | Four dark editor sentinels only; necessary-not-sufficient conditioning; no light or workload calibration claim. Correct. |
| Internal stages | Kept as an unimplemented original requirement and not conjured. Correct (wording: PD-L3). |

## Must-fix vs scoped limitations

**Must fix before declaration (doc/ledger only; no bench, so the preflight `source_snapshot` is unaffected):**
- PD-M1 (448-row family scope);
- PD-M2 (total-CPU target);
- PD-M3 (bracketing or explicit prospective infeasibility);
- PD-M4 (trigger set and confirmed-case scope);
- PD-L2 and PD-L3.

PD-L1, PD-L4 and PD-L5 are recommended, not blocking.

**Acceptable explicit scoped limitations, not defects:**
- internal-stage timing unimplemented;
- dark-only A/A sentinels;
- unknown diagnostic eligibility rates, hence possibly many unassessed latency rows;
- unbounded observer bias;
- descendant and TOCTOU limits;
- pointwise exploratory inference;
- tails descriptive at 30 sessions;
- the CPU AND-margin consequence;
- primary long-idle pools carrying only descriptive data;
- no live, platform or release claim.

**Never:** a phase, readiness, admission or universal-speed claim follows from this review.

## Files written

- this report;
- `/home/ajs/.cache/pb-phase7/review-performance-decisions-opus-r1/evidence.json`.

No other file was edited. No process, lock, thread or message was created by me, beyond the single failed `wc` disclosed above. Execution remains paused pending root's explicit release.
