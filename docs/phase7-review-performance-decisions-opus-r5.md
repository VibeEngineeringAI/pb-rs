# Phase 7 performance decision protocol: independent Opus review, round 5

Reviewed 2026-10-15 in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (live HEAD `ea70f7a`, no commit). This is a fresh, narrow closure review of the R5 proposal. It covers only PD4-L1, PD4-L2, PD4-L3, PD4-L4 and I-R4-1 from `phase7-review-performance-decisions-opus-r4.md`. It inherits no acceptance from R1–R4. It does not review the root-owned under-30-minute schedule disposition.

**Subject. I computed these hashes and they match the supplied values:**
- `docs/phase7-performance-decisions.md`: `0c0fb616…20f01` (79,827 bytes). I read all 219 lines.
- In `/home/ajs/.cache/pb-phase7/performance-decisions-r5/`:
  - `decision_math.py`: `bb675f18…8538`. I read all 820 lines.
  - `test_low_corrections.py`: `65d34b95…c51c`;
  - `artifact-manifest.json`: `27fc14c9…7955`;
  - `declaration-pending.json`: `e485471d…e1d`. It binds the code, test and doc hashes above.
- `findings-response.json` binds my R4 review at its actual SHA-256, `c8a996c6…f514` (15,170 bytes). It also binds `docs/evidence/phase7/under30-root-disposition.json` at `ad6addff…29b8` (1,310 bytes). I checked both.

**What this review is not.** It accepts no protocol, evidence, preflight, admission, candidate, matrix or runtime binding. It authorizes no execution. Actual CPU, RSS and idle provenance, q_total, hz, snapshot and brackets all remain NULL and unassessed. I edited no author file, source file, harness file or frozen artifact.

## Verdict

**PD4-L2, PD4-L3, PD4-L4 and I-R4-1 are closed. PD4-L1 is closed for every failure mode R4 named. There is one new low residual in the same caller-omission class, PD5-L1. There is no medium or blocking finding.**

**PD5-L1.** `case_confirmation_trigger` treats the caller's `decision_rows` argument as the case's declared rows. It does not check that argument against the hash-bound ledger. If a caller drops a declared crossing row from `decision_rows`, from its evidence and from `original_rows` together, the companion rows pass.

**Same disposition as the R4 lows.** PD5-L1 does not block freezing the review proposal. Fix it, or record it as an explicit derived-record obligation, before an accepted declaration binds `decision_math.py` as coordinator code.

**Narrow conditional rules I accept:**
- the mandatory, recomputed trigger evidence;
- the ACTIVE-bound idle gate;
- the reproduce-components-before-totals CPU gate;
- the full-span definition of the strict 1,800-second limit.

I did not revisit the settled mathematics, the margins, the counts, the P1 target or the under-30 disposition, and I found no counterexample to any of them.

## Execution, honestly reported

**No EDQUOT occurred,** so the one-attempt rule never came into play. I made one bounded probe run, with exit 0 and empty stderr. Its environment:
- `TMPDIR`, `GOTMPDIR` and `CLAUDE_CODE_TMPDIR` redirected to my cache's `tmp/`, which is still empty;
- `PYTHONDONTWRITEBYTECODE=1`;
- `python3 -I -B`.

**What I executed:**
- **Hashing** of the subject and identity files.
- **Preservation and manifest check [E]:**
  - all 494 `r4-preservation.json` originals match their recorded sizes and SHA-256s;
  - all 67 `artifact-manifest.json` entries match.
- **One guarded probe script, `review_probes.py`**, in my own cache:
  - it loads the R5 `decision_math.py` by path after checking its hash;
  - an audit hook blocks subprocess, fork, exec, spawn, socket and ctypes calls, any `phase7`/`harness` import, and any write outside my cache;
  - **result: 44 of 44 checks behaved as expected, zero forbidden events, 1.22 s.**

**What I did not run:**
- the author's 92-check test or the 550-check finalizer;
- any historical 623- or 422-check suite;
- any native helper, CLI, probe, compiler, build, profiler, lock, network access, preflight or measurement.

I control and measure no root UI process.

**Labels:**
- [S] source or file as read;
- [E] executed by me, pure or synthetic;
- [O] supplied, not verified.

**Read in full:**
- my R4 review;
- the successor doc;
- the R5 `README.md`, `decision_math.py` and `findings-response.json`.

**Read in part:**
- `test_low_corrections.py`, lines 1–30, 90–200 and 255–282;
- `phase7_bench.py:1296-1312` (`paired_summary`);
- `phase7.py:1552-1580`;
- `matrix-and-confirmation-design.json`, the `run_limit` object only;
- the summary keys of `checks-02/checks.json` and `final-binding-checks.json`.

The ACTIVE pending file is still `evidence_kind=pending`, with `hz_actual`, `actual_hz`, correspondence and snapshot all null [E].

## Closure of the R4 findings

| R4 item | R5 status |
|---|---|
| **PD4-L1** | **Closed for the named failure modes** [E][S]. **Residual: PD5-L1.** |
| **PD4-L2** | **Closed** [E][S]. |
| **PD4-L3** | **Closed** [E][S]. |
| **PD4-L4** | **Closed** [S][E]. |
| **I-R4-1** | **Closed** [S][E]. |

**PD4-L1.** Each failure mode R4 named is now rejected or handled conservatively:
- **No trigger evidence.** `trigger_evidence` is now a required keyword argument (`:395`). Omitting it raises `TypeError`.
- **Altered summary.** Disposition recomputes `case_confirmation_trigger` and requires exact equality (`:404-407`). A summary with `confirmation_signal` flipped is rejected.
- **Declared row with no evidence.** This is rejected (`:674`).
- **Source flag alone.** A retained source `True` with unknown rounds now signals (`:664`).
- **Source `True` against zero-reference rounds.** This is rejected, not dropped (`:660`).
- **Flag on total CPU.** Any `retained_source_crossing` key on `native_cpu_total_ms` is rejected, whether `None`, `False` or `True` (`:642`). A control without the key is accepted.
- **Explicit unknown.** An explicit `unknown_reason` makes every companion row provisional and not empirically satisfied (`:409-415`).

On the six-row ledger case `sql-scroll-500` primary, a crossing `startup` row with complete evidence holds all six rows pending.

**PD4-L2.** `full_idle_envelope` (`:485-515`) runs through the hardened `require_idle_accounting_provenance` (`:451-482`):
- it requires `actual`, or explicit synthetic mode;
- it requires the exact three-way target equality with the hash-verified P1 file's `selected_target`;
- it requires the five assertions, including correspondence;
- it requires a single integer `hz_actual`; the `actual_hz` alias is rejected;
- it requires hash-bound semantics and correspondence evidence.

Each of the 60 retained `clock_ticks_per_second` values must equal `hz_actual`. All 60 roles are then bracketed before `bootstrap_envelopes(ratio=False)` with exactly 2,000 draws.

Probed [E], each of the following rejects:
- my R4 counterexample (`evidence_kind=pending`, four Booleans true);
- the pending ACTIVE file;
- synthetic provenance without explicit mode;
- one of 60 scales set to 1000;
- a missing role;
- 29 pairs;
- one role lacking an outer-after bracket;
- draws of 1,000;
- `actual_hz` present;
- null correspondence;
- target "physical CPU…";
- unbound semantics text.

The synthetic positive path returns `synthetic-test-only: ineligible` and carries the bound P1 target on each bracket.

**PD4-L3.** `cpu_total_envelope` (`:752-788`) requires both component summaries and checks each one along the source's own branch before deriving totals. That branch is `all(r > 0)` → paired; otherwise the exact `'undefined for nonpositive reference or signed growth'` delta-only branch. I verified this branch against `phase7_bench.py:1304-1310` [S].

To test it, I wrote an independent transcription of the source `paired_summary` and `session_statistics` (value-choice RNG, no import). Results [E]:
- **Positive branch:** both components reproduce, then the total is derived.
- **Zero `system_cpu_ms` reference:** the delta-only branch reproduces, while the total reference stays positive.
- **Rejected:**
  - a missing system summary;
  - seed-8 summaries against seed 7;
  - a paired-branch summary for a zero component, and the reverse;
  - a component `delta_ci95` upper bound shifted by 1e-9;
  - the pending ACTIVE file.

**PD4-L4.** The span is now stated in all of these places:
- the doc at `:147`, `:189` and `:214`;
- `run_limit` in the design: `span_start`, `span_end` including common-lock release, `equality_admitted=false` and `timer_or_process_enforcement_implemented_here=false`;
- the `require_run_limit` docstring.

`require_run_limit` rejects 1800, 1800.0, `Fraction(1800)`, 3600, 0 and `True`, and it accepts 1799.999 [E]. The doc correctly says this is a declared-budget gate only, not runtime enforcement.

**I-R4-1.**
- `--output-dir` is required.
- The I7 predicate now reads `'M1 source applied historically, P1 design selected'`, which both final idle ledger rows satisfy [E].
- The R5 test evidence truthfully records a focused 92-check run, 0 code mutants and no repeated historical suites [O for its execution; S for its record].

## Finding

### PD5-L1 — Low: the case's decision-row set is caller-defined, so omitting a whole row fails open [E][S]

`case_confirmation_trigger(row_evidence, *, decision_rows)` (`:583`) checks evidence completeness against `decision_rows`, as passed by the caller. `case_confirmation_disposition` then requires only that `row_classifications == original_rows`. Neither function checks `decision_rows` against the hash-bound coverage ledger's rows for that `(case, policy, definition_sha256, comparison)`.

**Demonstrated [E].** The ledger case `sql-scroll-500` primary has 6 decision rows:
- **All 6 rows supplied**, with `startup_first_expected_ms` crossing (100 → 120): every row comes back pending.
- **The same `startup` row dropped** from `decision_rows`, from `row_evidence` and from `original_rows`: the remaining 5 rows come back `empirical_satisfied=True`, and no error is raised.

The author's own fixture shows the same gap: it passes a 2-row subset (`[:2]`) of a ledger case as "complete" (`test_low_corrections.py:100`).

**Consequence.**
- This is the PD4-L1 caller-omission class, one level up. The doc's "EVERY declared decision row of the case/policy" (`:208`) is enforced only relative to the caller's list.
- The omitted row itself never passes. Step 7 reconciliation should also catch it as unaccounted, so administrative closure stays blocked.
- However, the companion labels would be wrong, which contradicts the doc's rule that every row of a triggered case stays provisional (`:57`).

**Minimal fix.** Pass the declaration, or the hash-bound ledger identity, into `case_confirmation_trigger`. Require `decision_rows` to equal exactly, in ledger order, the ledger's decision-family rows with that case identity. Reject any subset or superset. The 448-row ledger already carries the identity fields the code uses.

Alternatively, record this as an explicit derived-record obligation before code binding: the coordinator must source `decision_rows` from the hash-bound ledger, and the derived record must show that set's equality.

### Informational

- **I-R5-1: the trigger helper does not recompute raw CIs [S].** It structurally validates raw CIs and rounds, but it does not recompute the raw CIs from the rounds with the bound seed. The doc places that reproduction upstream (`:38`, `:208`), and `require_raw_ci_reproduction` exists for it. No new requirement is raised. The coordinator record should show that the CIs supplied to the trigger are the reproduced ones.
- **I-R5-2: provenance gates remain assertion checks [S].** A fixture labeled `evidence_kind='actual'` would pass the idle, CPU and RSS gates. As the doc says (`:185`, `:210`), this proves no kernel fact. Root's independent verification remains the only route to actual provenance.

## Remaining prerequisites (not defects, unchanged)

These remain open:
- actual Omarchy correspondence, and CPU/RSS/idle provenance;
- actual brackets;
- the combined candidate, plus source, runtime, final matrix and ID bindings;
- actual matching preflight;
- real-report CI reproduction;
- eligibility and sentinel evidence;
- a validated, enforced plan that keeps every run strictly under 1,800 seconds, with runtime enforcement;
- root adjudication.

Under `under30-root-disposition.json`, the 8 sustained schedules are infeasible, the current 9 long chunks are inadmissible and the proposed 35-chunk schedule is unadmitted. Those rows stay unassessed. No performance acceptance follows from this review.

## Files written

- this report;
- `/home/ajs/.cache/pb-phase7/review-performance-decisions-opus-r5/`, containing:
  - `review_probes.py` (`05bd7e5d…ba4b`);
  - `run-01/probe-results.json` (`d274d0f1…a8e6`), plus that run's synthetic fixtures;
  - `run-01.stdout.log` (`4768f08e…d832`);
  - an empty `run-01.stderr.log`;
  - `preservation.log` (`408c6bd5…f9`);
  - an empty `tmp/`.

No other edits, no commit and no messages. **Zero processes are active.**
