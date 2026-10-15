# Phase 7 performance decision protocol: independent Opus review, round 3

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (base `4a812d5`, Go pin `07af390`, no commit). This is a fresh review of the R3 proposal. It is not a continuation of R1 or R2, and it inherits no acceptance from them.

**Subject (all digests supplied [O]; I computed no hash):**
- `docs/phase7-performance-decisions.md`: SHA `86402302…c069`, 58,328 bytes. I read 171 lines in full, which matches root's line count.
- R3 evidence under `/home/ajs/.cache/pb-phase7/performance-decisions-r3/`:
  - manifest `b37987f6…1a`, with 354 file identities verified by root;
  - `declaration-pending.json` `16c628c2…e07e`;
  - `decision_math.py` `e9def897…0702`.
- **String agreement only.** The declaration's own doc, math, freeze and bench identities equal the supplied strings (read by eye).

**What this review is not.** It accepts no protocol, measurement, preflight, admission or phase. It does not accept the collector source or the applied freeze. It authorizes no execution. I edited no author document, evidence, source, test, contract or frozen artifact.

## Verdict

**Yes: R3 is fit as a prospective decision protocol, subject to the named prerequisites below.** It remains a proposal until root adjudicates it.

All seven correction items are closed at protocol and design level:
- the five R2 lows;
- PD2-M1, via root's selected and applied remedy;
- the post-idle PI1 binding items.

**Mathematics.** I re-derived these from source and code, and they are correct:
- the ordered categories and exact rational inequalities;
- the envelope dominance argument;
- the conditional ±2-tick serial-read idle bound;
- bit-exact source parity, for the bound stdlib.

**No medium or blocking finding.** There are three new **low** findings. All are about fidelity between the bound code and the doc; none is a math or source defect. Each lets a caller error fail *open* on an obligation that the doc states clearly:
- **PD3-L1:** `case_confirmation_disposition` does not derive or enforce the confirmation trigger from its own rows.
- **PD3-L2:** the ledger labels the source-idle screen rows `descriptive-…`, while `confirmation_required` keys only on `decision-family`.
- **PD3-L3:** idle has a provenance code gate, but CPU and RSS envelopes accept a caller-supplied `q` with no provenance gate.

The doc's text prevails in every case, so these are hardening corrections, not protocol defects. Fix them before an accepted declaration binds `decision_math.py` as the coordinator, or record that the derived record must show the trigger and provenance derivation independently.

**Prerequisite risk P-1 (not a defect) [K][U].** The snapshot-within-read-interval prerequisite may not hold strictly for whole-process `/proc` ticks of a running thread. If it fails, both idle rows are again structurally unassessed. I give a bounded-staleness amendment that root can choose **before data**.

**Expect many unassessed rows.** Absent actual provenance, the honest expected outcome is many unassessed rows:
- CPU, until `q_total` is bound;
- RSS, until `maxrss` provenance is bound;
- idle, until hz, accounting and snapshot provenance and actual brackets exist;
- latency rows that are not interpret-eligible.

That is correct protocol behaviour, not a reason to relax rules.

## Execution, evidence and scope

**Execution:**
- **One shell attempt.** It first exported `TMPDIR`/`GOTMPDIR`/`CLAUDE_CODE_TMPDIR` to `/home/ajs/.cache/pb-phase7/tmp` and set `PYTHONDONTWRITEBYTECODE=1`. Then it ran `mkdir -p` of that directory and of my evidence directory, `git rev-parse HEAD`, `wc -c`, `sha256sum` of the doc, manifest, declaration and math, and an `ls`.
- **Result:** the harness reported **EDQUOT** on its own task temp directory, with exit code 1 and the output lost. There was no redirected record, so **whether any part ran is unknown**.
- **No retry.** No echo probe, temp-move or symlink. File tools only from then on.

**Not run:** no pure, mock or arithmetic test; no hash; no repository import; no existing suite; no native helper, Go/Rust CLI, subprocess oracle, compiler, probe, readelf, build, profiler, common lock, preflight or campaign. All arithmetic below is manual [A].

**Pause:** no quiet pause arrived. **Zero processes are active.**

**Labels:**
- [S] source or file as read;
- [K] Python or Linux semantics, not exercised on this host;
- [A] my manual arithmetic;
- [O] supplied;
- [U] unknown.

**Supplied, not verified by me [O]:**
- the author's 122 targeted pure checks, 8 focused follow-ups and 1,572 static binding checks. I summed the 1,572 composition table from `final-binding-checks.json:4-22` and it totals 1,572 [A]. Summing a table is not executing anything.
- root's 354-identity manifest verification;
- root's central closure: 114 + 1 focused pass, not a single clean 115/115 run;
- the owner's 5 scoped tests and compiled type-guard mutant;
- all applied freeze and bench hashes.

**None of these is independent execution or real-report CI reproduction.**

### Read scope

**Read in full:**
- the subject doc;
- R3: `README.md`, `findings-response.json`, `decision_math.py`, `resource-provenance-active-pending.json`, `runtime-prerequisites-pending.json`, `root-central-closure-binding.json`, `focused-followup-checks.json`;
- applied: `protocol-owner-handoff.md`;
- prior reviews: `phase7-review-performance-decisions-opus-r1.md`, `-opus-r2.md` and `phase7-review-postidle-opus-r1.md`;
- dispositions: `performance-decisions-opus-r1-disposition.json`, `-opus-r2-disposition.json` and `postidle-opus-r1-disposition.json`;
- `performance-decisions-r3-parent-check.json`.

**Read in part [S]:**
- `declaration-pending.json`:
  - lines 1–894 (all named identities and the start of the source-identity list);
  - lines 1820–1909 (runtime, coverage, pending fields, flags).
  - Not read: 895–1819, which is the remaining source-review identity list.
- `coverage-design.json`: lines 1–90, 10640–10840, 11250–11620, 12040–12190 and 12440–12994. These cover:
  - header;
  - first latency rows;
  - the last resource rows;
  - the **light** attribution `full_idle_bracketed_cpu_pp` decision row;
  - descriptive duplicates;
  - phase-inventory head and tail;
  - **both** source-idle screen rows;
  - profile table tail;
  - gate fields and all 52 typed hashes.
  - **The dark attribution idle decision row itself was not read.**
- `final-binding-checks.json`: lines 1–60.
- `ordered-pool-argv-registry-pending.json`: lines 1–90 (dark pool template).
- `harness/phase7.py`:
  - 375–400 (`proc_sample`);
  - 900–1146 (the whole `_execute` loop and metrics);
  - 1552–1580 (`session_statistics`, `paired_session_statistics`).
- `harness/phase7_bench.py`:
  - 90–185 (`check_snapshot`, `Options`, `Phases`);
  - 212–275 (`sql_case`, including the hold);
  - 395–428 (`campaign_cases` tail, `schedule`);
  - 718–781 (`native_proc_sample`, `Capture`);
  - 888–1061 (`observation_issues`, including the hold check, and `resource_interval`);
  - 1080–1154 (`extract_metrics`);
  - 1296–1371 (`paired_summary`, `descriptive_latency_decision`, `summaries`);
  - 2061–2151 (`require_pool_feasibility`, `pool_definition`, pool contracts);
  - 2158–2320 (`pool_reports`);
  - 2830–2938 (`main` argparse).

**Not read, so no coverage is claimed:**
- the original roadmap and `phase7-measurement.md`/`phase7-profiling.md`/contracts this round (historical context only);
- `matrix-and-confirmation-design.json`, `applied-direct-argv-registry.json`, `direct-typed-bindings.json`, expected-table files, `source-snapshot-real-HEAD.json`;
- `static_revision_checks.py`, `bind_central.py`, `finalize_proposal.py`, `rebind_applied.py`, `prepare_draft.py`, `source_reference_statistics.py`, intermediates, `decision-record-template.md`;
- applied `response.json` and `freeze.json`;
- the R10 and R11 tooling reviews (scope noted only);
- tests, `phase7_spawn.c` and kernel source.

## Findings

### PD3-L1 — Low, bound code: the confirmation lifecycle does not enforce its own trigger [S]

**Where:**
- `case_confirmation_disposition` returns each original row as final, non-provisional and `empirical_satisfied` when `triggered=False` and `status='not-required'` (`decision_math.py:394-396`).
- It never checks whether a row is itself `potentially-slower`.
- The doc says such a row is "Potentially practically slower, **confirmation pending**" and that "A strict decision-family lower-bound crossing also schedules the same single confirmation" (`decisions.md:44`, `:51`).
- The bound code has no helper that derives a strict lower-bound crossing from **raw** CIs independently of eligibility. `classify` returns `unassessed` for ineligible rows, and the doc says "Eligibility cannot suppress these signals."

**Trigger:** the coordinator computes `triggered` from point screens only, or omits it.

**Consequence:**
- Rows of the same case/policy are published as within-margin or faster, final, with no confirmation.
- A `potentially-slower` row is published non-provisional.
- That reopens the PD2-L4 closure asymmetry through a caller omission, not a rule. It cannot produce a faster or NI label for the crossed row itself.

**Minimal fix (code or record):**
1. In `case_confirmation_disposition`, set `triggered |= any(v == 'potentially-slower' for v in original_rows.values())`, or raise if they disagree.
2. Add one bound `case_confirmation_trigger(...)` that ORs:
   - every decision-row point screen (canonical and retained source);
   - the raw-CI strict lower-bound crossing, `lower95(R)>1+r AND lower95(D)>A` (idle: `lower>1/4`), evaluated **regardless of eligibility**;
   - any `potentially-slower` classification.

   The derived record retains each input.

### PD3-L2 — Low, ledger/code: the source-idle screen's scope label versus `confirmation_required` [S]

**Where:**
- Both `source_idle_screen_rows` carry `"family_scope": "descriptive-source-idle-screen"` and `"confirms_decision_metric": "full_idle_bracketed_cpu_pp"` (`coverage-design.json:12124-12172`).
- `confirmation_required` returns True only for `scope == 'decision-family'` (`decision_math.py:77-79`).
- The doc makes the source-idle crossing (paired median delta ≥ 1/4 pp) an investigation **and confirmation** screen for the idle decision family (`decisions.md:51`, `:67`, `:105`).

**Trigger:** a coordinator passes the screen row's own `family_scope` to `confirmation_required`.

**Consequence:** an adverse source-idle screen signal schedules no whole-pool confirmation. The full-interval row could then close from the original alone. It is a fail-open on the confirmation obligation, not on classification.

**Minimal fix:** state in the ledger, or encode in code, that the screen's `confirmation_signal` is evaluated under the scope of its `confirms_decision_metric` row. For example, pass the linked decision row's scope, or have `confirmation_required` accept the screen scope when it links to a decision-family metric.

### PD3-L3 — Low, bound code: CPU and RSS envelopes have no provenance gate in code [S]

**Where:**
- Idle has an explicit code gate: `require_idle_accounting_provenance`, combined with `read_bound_active_provenance` (`decision_math.py:417-438`).
- `cpu_total_envelope(component_rounds, q_totals, …)` accepts any caller-supplied `q_totals` (`:275-288`). `bootstrap_envelopes` likewise accepts RSS role intervals with any allowance.
- Yet the active provenance file has `q_total_ms: null`, `q_total_basis: null`, `kernel_identity: null` and RSS `kernel_source_unit_provenance: null` (`resource-provenance-active-pending.json:7-12`, `:26`).
- The doc says that without that provenance, CPU and RSS stay unassessed (`decisions.md:63`, `:71`).

**Trigger:** the coordinator passes q = 0 placeholders while the provenance is pending.

**Consequence:** the union then equals the raw CI, and CPU or RSS rows could be labeled within-margin without the bound reported-counter provenance the doc requires.

**Minimal fix:** add `require_cpu_accounting_provenance` and `require_rss_accounting_provenance`. These read the declaration's ACTIVE provenance (non-null kernel identity, accounting basis and `q_total_ms`/basis; RSS unit and approximation disclosure). Have the envelope functions derive q from that record rather than from a free argument.

### Informational

- **I-1, conditional ±2 proof re-derived [S][A].**
  - **Source facts:**
    - `proc_sample` reads the initial stat (`phase7.py:379`), then status, io, fd and the final stat (`:382-390`). The dict literal evaluates `time.monotonic_ns()` only after all of those reads (`:391`).
    - The sampler is called synchronously, once per iteration, before `select` (`:927-937`).
    - `native_proc_sample` is exactly `p7.proc_sample` (`bench:719-721`).
    - Failed or zombie reads return None and are not appended (`:380`, `:389`, `:929`).
    - **Therefore** the read instant r_j lies in [s_(j−1), s_j] for every retained j ≥ 1.
  - **Bounds:** with r_b ≤ S ≤ r_i ≤ r_k ≤ E ≤ r_a and X − 2 < n ≤ X, the bounds `n_k−n_i−2 < X(E)−X(S) < n_a−n_b+2` follow. `idle_brackets` (`:184-195`) implements exactly `decisions.md:83-85`, `:99-101`.
  - **Selection order:** b < i < k < a is automatic once both outer brackets and two inner endpoints exist, and the code also checks it. Requiring two inner endpoints is conservative: one would give the valid lower bound 0.
- **I-2, source-parity re-read [S][K].**
  - `source_index_bootstrap` and `source_index_delta_statistics` mirror `paired_session_statistics` and `session_statistics` (`phase7.py:1552-1580`):
    - same `Random(seed)`;
    - one `choices(…, k=30)` per draw;
    - same generator order into `statistics.median`;
    - separate sorts;
    - indices `int(draws*.025)` and `int(draws*.975)`.
  - The delta-only dictionary, including the `unit` and `inference` strings, equals the source's, so the whole-dict bit-pattern comparison is well formed.
  - The pool summary uses `Options(sessions=30, warmups=5, seed=analysis_seed, …)`, which defaults to `bootstrap_draws=2000` (`bench:117`, `:2311-2312`). The CLI has no draws flag (`:2830-2866`).
- **I-3, the PD2-L3 argv wording.**
  - `--chunk-report` is `action='append'` (`bench:2847`). argparse itself also accepts `--chunk-report=PATH` and unique-prefix abbreviations [K].
  - The rejection is therefore a **coordinator registry rule**, which the code implements (`decision_math.py:361-370`). Abbreviations fail closed through the path-list mismatch.
  - `check_snapshot` requires `identity['path'] == resolve(strict=True)` (`bench:95`), so registered paths must be canonical. The pending template uses absolute paths.
  - Suggest wording: "argparse would accept; the registry rejects".
- **I-4, ordering gate fields match source [S].**
  - Pool pairs are deep-copied chunk pairs, validated against `schedule()` keys `case/purpose/round/order` with `status=='valid'` (`bench:2257-2259`, `:2288-2291`, `:427`).
  - `result['chunks'][*]['identity']` is the chunk snapshot (`:2191`).
  - `_pool_pair_signature` and the chunk-identity comparison therefore use real fields.
  - The source reducer checks the chunk-index **set** (`:2195`, `:2293`), not its order, as the doc says.
- **I-5, hold remedy as applied [S].**
  - The hold applies only when `idle is not None and observer_policy=='resource-attribution'` (`bench:252`).
  - It is an anchor with `min_elapsed 170`, the default stable 80, int `duration_ms=250` and `start_checkpoint='idle-end'` (`:254-257`).
  - The timeout gains `+0.25` (`:265`).
  - The retained check is `ready − E.ready ≥ 250_000_000` with an int type guard (`:950-958`).
  - Extraction skips anchors (`:1090-1091`), and S/E stay the `idle` phase's own frames (`:1094-1097`).
- **I-6, `pool_definition` first assignment [S].** `base = Options(**{…, 'sessions': max(1, options.sessions)})` precedes `campaign_cases(base)` (`bench:2094-2095`). Pool-plan validity also checks `pref['case_hashes'][name] == digest(case)` (`:2102`). Deriving the 4 pool-plan identities from this exact statement is faithful execution semantics, not a validation bypass. The 90-record direct computation itself is [O].
- **I-7, ledger counts [A] and wording.**
  - Ledger arithmetic:
    - 448 + 18 + 2 = 468 row references;
    - selected-case occurrences: 2×26 + 84 + 4 = 140;
    - argv records: 86 + 4 = 90;
    - campaign index layout: 2–85 cover 84 campaigns; long chunk index sets 14–22, 35–43, 56–64 and 77–85; pool plans 86–89.
    - Example ledger indices agree: `[1,75]`, `[1,76]` and `[1,77…85,89]`.
  - Stale wording: the idle decision row's status still says "M1 root disposition … pending" (`coverage-design.json:10796`). Root has since selected and applied M1. The row is still correctly unassessed.

## Prerequisite risk P-1 (future must-run, not a defect) [K][U]

**The issue:**
- The idle proof needs each returned tick total to equal the target X at an instant *inside* its read interval (`decisions.md:77`).
- On Linux, whole-process `/proc/PID/stat` times for another process are derived from per-thread scheduler runtime via an adjusted split. A thread **running on another CPU at read time** may have its runtime refreshed only at its next tick or context switch.
- If the actual kernel behaves this way, the reported value can correspond to an instant up to about one scheduler tick **before** the read call began.
- I assert this for no particular kernel. It is a plausible failure of the property as stated, and the protocol would then leave both idle rows unassessed.

**Staleness direction [A]:**
- Staleness moves the effective instant earlier: r′ ≤ r.
- For the outer-before endpoint b and the inner-last endpoint k, that is harmless (conservative).
- It can invalidate only:
  - **inner-first** i, where it needs r_i′ ≥ S;
  - **outer-after** a, where it needs r_a′ ≥ E.

**Prospective amendment root may choose before any data:** if provenance establishes a staleness bound δ, select inner-first with `s_(i−1) ≥ S+δ` and outer-after with `s_(a−1) ≥ E+δ`, with everything else unchanged.
- With a cadence of 50 ms or more and the 250 ms hold, availability is barely affected for δ of a few ms.
- This adds no observation, cadence change or subtraction.
- It must be declared **before** collection or value examination. Otherwise it is a post-data rule change.

## Prior finding closure (individually)

| Finding | R3 status |
| --- | --- |
| **PD2-M1** | **Closed at protocol and source-design level.** Root's decision was recorded before freeze and preflight. The applied hold matches the reviewed design (I-5). The doc truthfully states that two retained post-E samples are needed, keeps the 40–110 ms figure a heuristic, makes no availability guarantee, and does not reinterpret old pools (`:87-91`). **Residual [U]:** actual brackets across 60 roles per pool, hz, accounting and snapshot (P-1). |
| **PD2-L1** | **Closed.** The distinct `full_idle_bracketed_cpu_pp` decision row has the serial-read `interval_policy` and `source_point_estimate: null` (light row read). The two source rows are screen/descriptive, and doc `:26` and `:105` match. Residual: PD3-L2 scope label. |
| **PD2-L2** | **Closed.** Exact `Fraction(u)+Fraction(s)` centers versus IEEE `float(u)+float(s)` coordinator-raw totals (`:239-247`). The raw CI uses the source bootstrap with bound seed and 2000 draws, and the total is labeled coordinator-raw (`:261-272`). The union applies; the total-CPU screen uses IEEE totals (`:317-322`). There is no source-total claim. |
| **PD2-L3** | **Closed.** The gate runs after validation and recomputation. It enforces exact order 1..9, chunk-identity order, the split-form argv path order, and that the pair signature equals the chunk concatenation. It does not repair (`:346-387`). Wording note: I-3. |
| **PD2-L4** | **Closed in doc and lifecycle code.** Triggered, scheduled, pending, declined, infeasible or unexecuted keeps every row provisional; failed or invalid makes all rows inconclusive; a tradeoff is never empirical (`:390-414`). Residual: PD3-L1 (trigger not enforced). |
| **PD2-L5** | **Closed.** The ACTIVE provenance identity is declared and read by hash (`:417-422`). The R2 "331/10 mutants" is corrected to 180 synthetic parity checks and 10 discriminating cases (`decisions.md:165`). The author states 0 executed mutants. I did not execute the checks. |
| **PI1-M1** | **Closed in binding text and design.** All 52 typed hashes are present (`coverage-design.json:12939-12991`). Every row type I read carries the typed digest plus a historical integer digest. The future gate requires own-argv = ledger = REAL validated preflight (`:12916`; `decisions.md:95`); expected tables are explicitly not evidence. Actual preflight is UNRUN. |
| **PI1-L1** | **Closed [O].** The handoff reports the `250.0` negative plus a compiled drop-guard control (`handoff:26`). I did not read the test file. |
| **PI1-L2** | **Closed [O]/[S].** There are 90 direct records (86 raw, 4 derived via `pool_definition`'s first `base` assignment, I-6). Historical integer hashes are retained and nothing is normalized. The 52 hash values themselves are [O]. |
| **R1 PD-M1…M4, PD-L1…L5** | **No regression found.** The 448 + 18 scope holds. The CPU target is the reported total and q is pending. Brackets use the previous/current stamp. Confirmation applies to the whole case/policy. Faster+NI gives NI. Seed and draws are bound. Stages remain UNIMPLEMENTED. maxrss is the reported target. Sentinel limits hold. |

**Unresolved objections from earlier rounds:** none remain open as protocol defects.

## Exact-rule checks [S][A]

| Item | Assessment |
| --- | --- |
| Inequalities | Faster is strict `<1`, `<0`. NI is `<=` with AND. Slower is strict `>` with AND. Idle uses 1/4 with no ratio. Comparisons are exact `Fraction` against `11/10`, `6/5`, `1`, `10`, `2097152` and `1/4`. Categories are disjoint and faster implies NI. Correct (`decision_math.py:26-55`). |
| Zero and ratio | Any reference ≤ 0 makes the ratio undefined, hence unassessed. CPU raises when the exact reference is ≤ 0. The exact sum is 0 if and only if the IEEE sum is 0 for nonnegative components [K]. The delta-only source path is preserved. Correct. |
| Envelopes | Per-draw medians of lower ≤ true ≤ upper; dominance survives sorting; the ratio uses b−/a+ and b+/a− with a− > 0. Union with the raw CI only widens. Correct. |
| Confirmation | Faster+faster gives faster; mixed NI gives within-margin; slower+slower gives confirmed slower; anything else, or a missing or mismatched key, gives inconclusive. No third run. Correct; trigger enforcement is PD3-L1. |
| Correlation and tails | The doc requires a replay-cost correlation and tail note per latency label. This is a derived-record obligation, not code. Acceptable. |
| Idle proof | Valid conditionally (I-1). Its prerequisites are explicit in the active provenance, and the code fails closed while they are null. Risk: P-1. |

## Must-run prerequisites (unknown, not defects)

1. Actual CPU kernel, accounting and `q_total`; RSS `maxrss` unit and approximation; idle hz, two-floor model, monotone basis and snapshot property (P-1).
2. Actual brackets in all 60 roles of each attribution pool.
3. Collector, reducer and coordinator runtime identities, and stdlib parity.
4. Final selected matrix, ID registry and actual reducer argv.
5. REAL independently validated preflight `case_hashes` equal to the own-argv digests and the ledger.
6. Real-report bit-exact raw-CI reproduction.
7. Interpret/diagnostic eligibility and sentinel/control evidence.
8. Root adjudication.

## Fitness

**Fit as a prospective decision protocol, subject to:**
- the must-run prerequisites above;
- PD3-L1 to L3, applied to the bound code or explicitly recorded as derived-record obligations;
- root deciding P-1's staleness treatment before any data.

The rules cannot convert missing, ineligible, invalid, zero-reference or unprovenanced evidence into a pass when followed as written. PD3-L1 to L3 harden the code against caller error. This review is not acceptance and must not be forwarded as an accepted hash. Admission remains false.

## Files written

- this report;
- `/home/ajs/.cache/pb-phase7/review-performance-decisions-opus-r3/evidence.json`.

The failed shell may or may not have created `/home/ajs/.cache/pb-phase7/tmp` or the evidence directory [U]. No other edits, no messages, no commit. **Zero processes are active.**
