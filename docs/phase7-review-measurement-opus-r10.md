# Phase 7 measurement tooling: independent Opus source review, round 10

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (base `4a812d5`, Go pin `07af390`). This is a new review round of the frozen R8 Python corrections.

**Scope:**
- R8-M2: pool budget and single full feasibility per reduction, including the exact cap table and option binding.
- R8-L1: the no-false-pass and AND-arm tests.
- R8-L2: the code part, decision context labels.
- A regression check of the inherited observer/provenance paths.

**Out of scope:** acceptance of the decision protocol (R8-M1), the R8-L3 doc work and the I-1/I-2 rules. A separate Opus R1 review and Sol own those.

It accepts no phase, release, candidate or measured result. It authorizes no preflight, campaign, pilot, decoder run, profiler run, quiet window or lock acquisition.

**R9 status:** R9 ended only to acknowledge a companion quiet pause. It produced no report, findings or evidence, so nothing is inherited from it as review or acceptance. Reports R1–R8 and their failed snapshots are unchanged.

## Verdict

- **R8-M2 is closed at source, backed by my own executed pure/mock tests and causal mutants.**
  - The reduction budget is finite, non-bool and 0 < b ≤ 3600. It is checked before any evidence read and recorded, along with elapsed time, even on failure.
  - Full-profile feasibility runs once per reduction.
  - The live validator is always fresh.
  - The exact cap table is `[20]*8+[18]`, and feasibility uses the maximum cap.
  - Every allocation-affecting option is bound.
  - Actual reducer wall time remains [U].
- **R8-L1 is closed.**
  - Eligible ∧ non-crossing gives `budget_pass is None`.
  - The absolute and relative arms are crossed independently.
  - The test's own in-file `and→or` and `budget_pass` mutants are asserted to fail.
  - The interpret 5 + 30 positive test now asserts `latency_decision`.
- **R8-L2 (code part) is implemented, with one residual: R10-L1 (Low).** Summaries of an individual long-idle pool chunk are still labelled as primary first-expected potential regressions.
- **No inherited observer/provenance regression was found** in the lines I read (scope below).
- **Admissions:**
  - **1, functional CLI preflight:** fit at source. Settle R10-L1 first, either as a bench change or as a recorded protocol-only residual.
  - **2, first truthful bounded A/A and controls:** fit at source, unchanged.
  - **3, resource/primary/diagnostic/interpret/pool:** source-correct. Actual fit is [U], and decision use depends on the separate protocol.
  - **4, profiler preparation and interoperability:** not ready. The profiler source is unchanged since R7.
- **The review is unfinished only in the sense recorded below.** A root execution pause arrived after my single shell attempt had already completed. I sent the ACK, and the rest of this report was written with file tools only.

## Execution, evidence and pause

**Execution, one bounded shell attempt:**
- **Command:** `TMPDIR`/`GOTMPDIR`/`CLAUDE_CODE_TMPDIR=/home/ajs/.cache/pb-phase7/tmp PYTHONDONTWRITEBYTECODE=1 timeout 580 <cache venv python> -B -I <probe.py>`, with output redirected to the cache.
- **Result:** the harness reported EDQUOT on the Claude Code task temp directory and lost the console output. The exit code is unknown.
- **Why the run still counts:** the script's own redirected outputs were fully written and include its final `source_after` record:
  - `/home/ajs/.cache/pb-phase7/review-measurement-opus-r10/probe.log`
  - `/home/ajs/.cache/pb-phase7/review-measurement-opus-r10/execution.json`

  So I treat that run as completed **executed** evidence [P].
- **No further shell attempt.** I used no symlink workaround.

**What the probe did (pure/mock only):**
- **Boundaries inspected first:**
  - `PoolTests` mocks `source_snapshot`, `campaign_cases`, `validate_preflight`, `p7.validate` and `p7.compare`.
  - The pool CLI test mocks `FunctionalOwnership`.
  - `binary_identity` only hashes files (`phase1.py:346-350`).
  - `check_helper_files` only hashes; the synthetic helper bytes are never executed.
- **Not run:** no native helper, CLI, fixture server, probe, compiler, build, profiler, timing, lock, preflight or campaign. `-B` ensured no `__pycache__` was written into the worktree.
- **SHA-256 verification [P]:** I computed the hashes myself. All six match the supplied values, and sources were identical before and after:
  - `phase7_bench.py`: `b59cf46e…`
  - `test_phase7_bench.py`: `66520ee0…`
  - `phase7_profile.py`: `cd064da4…`
  - `test_phase7_profile.py`: `75bb1422…`
  - shared `phase7.py`: `dc7574f0…`
  - `phase7_spawn.c`: `0279d5fb…`

  The profile pair equals the R7-recorded identities (`collector-r7-response.json:247-256`), so the profiler source is unchanged since R7. I did not hash the helper ELF.
- **Baseline [P]:** 13/13 passed in 88.6 s. These were all 10 `PoolTests` plus three pure `R7Tests`:
  - `descriptive_crossing_inconclusive…`
  - `latency_no_false_pass…`
  - `summary_crossing_contexts…`
- **Reviewer mutants [P]:** each mutant was compiled into a private namespace with bench globals and patched in-process. Files and module attributes were never written. All six were killed:

  | Mutant | Change | Killed by |
  | --- | --- | --- |
  | M1 | feasibility on every chunk (`if binding is None` → `if True`) | `fresh.call_count` 9 ≠ 1 |
  | M2 | drop the `common_options` check | late budget, bootstrap, network-delay and preflight-path drift all become valid |
  | M3 | exclude `budget_seconds` from common options | late budget drift becomes valid |
  | M4 | drop the ≤ 3600 upper bound | 3601 reaches the evidence read |
  | M5 | feasibility uses the min cap | plan 20 > 18 raises; killed through a fixture error, not an assertion |
  | M6 | guard constructed after the first spec read | `FunctionalGuard` never called |

- **Chunk-label probe [P]:** a synthetic pure `summaries()` call (see R10-L1).

**Supplied, not mine [O]:**
- The owner's 106 bench passes in 130.740 s and 23 pure profile passes in 1.313 s, with the native analysis child excluded. I read `final-checks.json`, `run-final-checks.py` and both logs, but did not rerun them.
- The root's verification of the four sources and two logs.
- The historical R7 root run of 99 bench + 24 full profile tests.
- Helper77, the native resize control, and the release5 compatibility counts (`3268+6`, `160+30`, `17`, `564+4+4`).

Passing tests are not acceptance.

**Pause:** a root pause (companion ≤ 230 s saved-strace analysis) arrived after my shell attempt had exited. I sent the ACK to `mcp:65600223-…` (message `opus-r10-pause-ack-1`): zero active owned processes, execution paused until explicit root release. After that, I did only file-tool reads and writes, with no shell, test, hash or probe.

**Labels:** [S] current source read; [P] executed by me as above; [O] supplied; [U] unknown or unmeasured; [I] inherited; [K] Python semantics, not exercised.

## Read, unread and inherited scope

**Read in full:**
- Reviews: `phase7-review-measurement-r1.md`, `-interim-sol-r2.md`, and `-opus-r3.md` through `-opus-r8.md`.
- Dispositions and responses in `docs/evidence/phase7/`: `tooling-r6-disposition.json`, `tooling-r7-disposition.json`, `tooling-r8-disposition.json`, `collector-r7-response.json` and `collector-r8-owner-checks-verified.json`.
- The cache `collector-r8-corrections/{final-checks.json,run-final-checks.py,bench-final4.log,profile-final2.log}`.

**Read in part:**
- `harness/phase7_bench.py` (2,927 lines), lines:
  - 1–260: constants, `Options`, `Phases`, start of the SQL case;
  - 540–739: geometry tail, offsets, A/A artifacts, `FunctionalOwnership`, `QuietWindow`, `Capture` head;
  - 1130–2927: extraction tail, `source_snapshot`, artifact and helper checks, `paired_summary`, `descriptive_latency_decision`, `summaries`, controls, `plan`, `run_campaign`, `FunctionalGuard`, preflight and `validate_preflight`, `retained_sample`, clone and replay, `decode_phase`, the decoder diagnostic, authorization, the whole pool block, `session_work`, `campaign_sessions`, the end check, the diagnostic plan, allocations, `campaign_diagnostics`, eligibility, stamping, `interpret_campaign`, `campaign_feasibility` and `main`.
- `harness/test_phase7_bench.py`, lines 1–80, 820–1652. This covers the R6 interpret 5 + 30 test, R6 mutation and CLI tests, all of `R7Tests` and `PoolTests`, and the head of `QuietTests`.
- `harness/phase1.py`, lines 330–369.

**Not read, so no coverage is claimed:**
- `phase7_bench.py` lines 261–539 (remaining case builders, `Readiness`, geometry) and 740–1129 (`Capture` body, `ReplayCapture`, `observation_issues`, `extract_metrics`).
- `test_phase7_bench.py` lines 81–819 and 1653 to the end.
- `phase7_profile.py` and its test. These are unchanged since R7 by my hash match.
- `phase7.py` and `phase7_spawn.c`. Both are unchanged by my hash match; their content is [I].
- `phase7_workloads.py` and `profile_external.py`.
- All docs: `phase7-measurement.md`, `phase7-profiling.md`, `phase7-harness.md`, the contract, `rust-phase7.md`, `phase7-performance-decisions.md`, and the plan `/home/ajs/.cache/pb-roadmap/phase7-final.md`.
- The command matrix.
- The spawn-helper R1 and interim R2 reviews, and `harness-native-helper-r2-response.json`.
- Earlier freezes, diffs and logs.
- `docs/evidence/phase7/` was not enumerated.

**Inherited [I]:**
- **Helper C-level lifecycle:** WNOWAIT, exact `wait4`, errno handshake and signal policy, from R4's full read and Sol R2's executed checks. Only the C hash is mine.
- **Known limitations, preserved:**
  - fatal supervisor death leaves CLI resources and status unknown;
  - escaped `setsid`/`setpgid` and short-lived descendants;
  - path and check-to-exec TOCTOU;
  - spawning-thread lifetime and SIGCHLD ownership;
  - Linux-only, with cross-platform behaviour unknown;
  - native internal stages are unimplemented and unknown.

## R8 corrections: independent closure

### R8-M2: closed [S][P]

**Budget** (`bench:2143-2157`, CLI `:2885-2887`):
- `finite_number` rejects bool, NaN, ±Inf and overflowing ints. `0 < b ≤ 3600` is checked before `FunctionalGuard` is created, and the guard is created before the first `json_snapshot`.
- `budget_seconds` (or its `repr`), `declared_budget_type` and `elapsed_seconds` are written on every path (`:2150-2153`, `:2303-2304`).
- The CLI forwards `--budget-seconds`.
- Tests at `test:1397-1430`. Mutants M4 and M6 were killed.

**Single feasibility** (`:2187-2195`):
- `require_pool_feasibility` runs only for the first validated chunk and must equal `provisional_chunk_feasibility`. Its result is retained as `validated_pool_feasibility`.
- Preflight validations per reduction:
  - one selected-case `validate_preflight` (`:2167`);
  - one full-profile `offline_preflight_check` (`:2051`);
  - one selected-case check inside `campaign_feasibility` (`:2777`).

  That replaces R8's 9 × (26 + 2).
- The test asserts one call and two `offline_preflight_check` calls (`test:1343-1345`). Mutant M1 gave 9 ≠ 1.
- `source_snapshot()` still runs once per chunk inside `_validate_pool_contract`. That is acceptable and does not repeat the preflight.

**Why one feasibility suffices:**
- `campaign_feasibility` output contains no seed, session, warmup or campaign-ID-dependent field (`:2797-2804`). Its inputs are forced: sessions 4, warmups 0, max cap.
- So identical bound options imply identical allocations. The test checks this for all nine chunk options (`test:1359-1362`).

**Option binding** (`:2067-2073`, `:2114-2128`):
- Every field is bound except `sessions`, `warmups`, `seed`, `campaign_id`, `pool_chunk` and `pool_spec`. These are checked separately:
  - schedule index, seed and counts (`:2126-2128`);
  - distinct retained approvals (`:2183-2186`);
  - `pool_spec_identity` equality (`:2197-2198`).
- `max_sessions` is normalized only after the exact per-chunk check (`:2122`).
- Mutants M2 and M3 were killed for budget, bootstrap, network delay and preflight path. `max_sessions` drift is caught separately by the cap check.

**Caps** (`:2062-2064`):
- The formula is 2·(w + s) + 4·floor·themes·sizes, giving `[20]*8+[18]` at floor 3 with one theme and one size.
- Worst-case feasibility uses `max(caps)` with 4 pairs (`:2053-2055`). The actual chunk-9 run cap stays 18 (`test:1338`, `:1350`, `:1374`).
- Mutant M5 was killed.
- Arithmetic re-derived: 12·30 + 60 + 8·(300 + 1.5x + 8) + (30 + 5·4) = 2934 + 12x, which is 3600 at x = 55.5, consistent with `test:1493-1527`.

**Live admission** (`:2131-2140`): the validator has no bypass parameter and always recomputes feasibility. The test calls it twice and observes two calls (`test:1356-1357`). The cap, count, control and profile spec mutations are rejected (`test:1363-1373`).

**Retained per-chunk checks** (`:2173-2277`, end `:2278-2293`), unchanged in kind:
- approvals and role boundaries;
- source, artifact, helper and metadata end bindings;
- controls with raw rehash;
- schedule and lifecycle uniqueness;
- contract, comparator and extraction rerun from raw;
- final rehash of the spec, preflight, every chunk, controls and raw.

**Failure behaviour:**
- **Expiry:** fails closed with inputs byte-identical (`test:1432-1450`).
- **Residual [U]:** the actual reducer wall time, and bounded validation that is not internally interruptible (R7-I4).

### R8-L1: closed [S][P]

- `test:1154-1169` uses (Δ5, eligible) → `budget_pass None`, Δ20 on reference 400 → no crossing, and Δ8 on reference 50 → no crossing. It then asserts that source-level `or` and `budget_pass=True` mutants fail. That test passed in my run.
- Interpret 5 + 30 asserts the full `latency_decision` (`test:856-861`, `:875-876`). I read this but did not run it.
- Python precedence [K]: `delta > 10 and relative > .10 if relative is not None else None` parses as `(…and…) if … else None`, as intended (`:1306`).

### R8-L2, code part: implemented, with residual R10-L1 [S][P]

- `descriptive_latency_decision` (`:1299-1327`) assigns the context in this order: A/A sentinel, pooled, attribution, stable-ready, then primary first-expected.
- `material_potential_regression` and `latency_trigger_eligible` are candidate-only. `accepted_trigger` is always False and `budget_pass` always None.
- All six contexts are tested (`test:1171-1192`), and that test passed.

### Root objection, preflight end binding: holds [S]

- `campaign_end_check` rechecks every `file_identity` record (`:2446-2450`).
- `campaign_sessions` puts `pref_identity` first in the records (`:2386`).
- `end_bindings` is written only after a successful end check (`:2624`). Interpret recomputes it and requires equality (`:2752-2754`).
- The mutant test asserts the exact error, and that filtering the preflight record flips the result (`test:1038-1053`). I read this but did not run it.

### Inherited observer/provenance paths: no regression in the lines read [S]

- **QuietWindow:** approval before a nonblocking lock, the companion `campaign_id`, and `Exception`-wrapped reload (`:630-700`).
- **FunctionalOwnership:** `:596-612`.
- **Capture binding and checks:** `check_command`, `check_binding`, `check_artifact`, `final_identities` (`:1176-1252`).
- **`run_campaign` order:** preflight, then pool spec, then QuietWindow, feasibility and `approval_allocation`, controls, then per-role helper and artifact checks (`:1451-1576`).
- **Retained revalidation:** `validate_preflight` and `retained_sample` (`:1682-1768`).
- **Replay:** clone and replay (`:1794-1860`) and `decode_phase` (`:1862-1951`).
- **Diagnostics and interpretation:**
  - `campaign_sessions` (`:2348-2436`);
  - the pilot cap-on-both-sides rule (`:2336-2345`, `:2511-2540`);
  - `observer_eligibility` (`:2652-2666`);
  - interpret checks (`:2680-2766`).
- **Diff scope:** I did not diff against the R7 freeze. The R8 owner diff and `owned.diff` are unread, so lines 261–539 and 740–1129 are not covered.

**Boundaries:**
- JSON evidence is single-read and digested (`:81-96`).
- Raw sessions are contained and non-symlink, hashed, parsed and rehashed, then rehashed by path at the end (`:1748-1768`, `:2289-2293`).
- No atomic or adversarial guarantee is claimed. Ordinary A→B→A check-to-use TOCTOU remains.

## Findings

### R10-L1 — Low, labelling residual of R8-L2: per-chunk long-idle summaries carry candidate-regression wording [S][P]

**Where:**
- `run_campaign` calls `summaries(report, options)` (`bench:1556`).
- `interpret_campaign` calls `summaries({...}, options)` (`:2756`).
- The untrusted `reduce` CLI (`:2914`) does the same.
- None passes `pooled`. Only `pool_reports` passes `pooled=True` (`:2296-2297`).

**Trigger:**
- A primary-policy long-idle pool chunk 2–9 completes with 3 or 4 measured pairs, and its paired median first-expected delta exceeds 10 ms and 10%.
- The chunk report (and any optional chunk interpret) then records:
  - `context: primary_first_expected_potential_regression`;
  - `material_potential_regression: true`;
  - "fresh-seed confirmation AND investigation".
- The plan and `pool_reports` itself state "pooled latency descriptive only; constituent campaign diagnostic eligibility is never inherited".
- Confirmed by my synthetic pure probe [P]:
  - with chunk options (`pool_id`, chunk 2, 3 sessions), `summaries` returned exactly those fields;
  - with `pooled=True` it returned `descriptive_pooled_latency` and `material_potential_regression: null`.
- Attribution-policy chunks are unaffected, because the policy check precedes the stable/primary checks.

**Effect:**
- There is no false pass: `latency_trigger_eligible` is False, `accepted_trigger` is False and `budget_pass` is None.
- But a 3–4-pair sub-sample can raise a candidate potential-regression flag that any consumer keyed on `material_potential_regression` would act on. That could create spurious confirmation obligations or seed-selection pressure.
- Whether the separate decision protocol already excludes chunk reports is outside my scope and unread [U].

**Minimal correction (pick one before the preflight):**
- **Bench change.** Pass `pooled=bool(options.pool_id)` at `:1556`, `:2756` and `:2914`, and add one assertion per call site. `source_snapshot` binds `harness/*.py` (`:1148`), so this must land before the functional preflight.
- **Protocol/doc disposition, no source change.** Record that per-chunk capture/interpret summaries are never decision inputs, and that only `pool_reports` output, descriptive, is consumed.

### Informational

- **I-1, pool CLI default budget.** If `--budget-seconds` is omitted, `pool` runs at the argparse default of 600 (`:2843`, `:2887`). The value is recorded, and the reduction is offline and rerunnable under a larger declared budget without a source change, so R8-M2 does not regress. The matrix should state the budget explicitly (matrix unread).
- **I-2, pooled bootstrap draws.** The pooled summary uses the `Options` default of 2000 bootstrap draws (`:2297`), not the chunk-bound `bootstrap_draws`. It is deterministic and declared by code. Note it if the matrix uses another value.
- **I-3, test message specificity.** The late-option test asserts only that errors are non-empty (`test:1391-1395`). My M2/M3 mutants show it is causal for the `common_options` binding.
- **I-4, check ordering.** For the first chunk, feasibility runs before `_validate_pool_contract` and `pool_spec_identity` (`:2187-2198`). Any drift still fails closed afterwards; only the cost order differs.
- **I-5, stable-ready screen label.** On `_ready_stable_ms`, `observer_screen_eligible` mirrors the first-expected screen, which replays only up to the first match, not the stability hold. Since `latency_trigger_eligible` is False there, there is no decision consequence; it is a label only.
- **I-6, overflowing integer budget.** A `10**10000` API budget fails `repr` under Python's integer-to-string limit, so `budget_seconds` stays None while `declared_budget_type='int'` and the failure are retained. This is not reachable from the CLI, where `float()` gives `inf`, which is rejected.
- **I-7, guard message.** The guard message reads "functional preflight budget exhausted" in the pool context. Cosmetic.

## Admissions: must-run validations versus source defects

**Source decision before the preflight:** R10-L1, as a bench change or a recorded residual. No other source defect was found in scope.

**1. Functional CLI preflight (no timing): fit at source.** Must run:
1. A no-commit freeze with HEAD `4a812d5`.
2. The parent verifies the owned, consumed and helper hashes, including the ELF, which I did not hash.
3. `cached_spawn(probe=True)` inside its own agreed window with the lock free (R7-L1).
4. Both 26-variant / 52-role preflights:
   - into new directories;
   - with budget ≤ 3600.
5. Each preflight reaches 52/52 roles, 26/26 pairs and dry extraction, with failures retained. This also confirms the H−6 footers and row 16 [U].

**2. First truthful bounded A/A and controls: fit at source.** Must run:
1. A matching A/A preflight.
2. `feasibility` fits.
3. A fresh campaign-bound approval and companion.
4. `budget_evidence` stays false. A/A crossings are labelled as host/harness-bias sentinels.

**3. Timed captures, diagnostics, interpretation and pools:** source-correct.
- **Captures:** actual fit [U].
- **Diagnostics and interpret:** pilot fit [U].
- **Long-idle pools:**
  - The pool plan must pass the actual full-profile feasibility at unchanged reserves.
  - Run nine chunks with the exact caps `[20]*8+[18]`, distinct IDs and approvals.
  - Then reduce with an explicit declared budget.
  - Reducer wall time is [U].
- **Decision value** depends on the separately reviewed protocol, which I did not audit.

**4. Profiler preparation and interoperability: not ready**, unchanged. Must run:
1. The driver's own `prepare-symbols`.
2. A bounded real smoke test covering:
   - FD3 / `profiler.stderr` separation;
   - exact heaptrack stderr;
   - raw naming;
   - strace `= ?` parsing;
   - perf `$HOME/.debug` side effects;
   - symbol resolution.
3. Valid controls.

**Never:**
- subtract floors, holds or replay work;
- treat attribution, A/A, pooled or per-chunk latency as trigger evidence;
- adopt rules after seeing results;
- infer six-platform, installer, rollback or live HTTP/2 readiness from Linux loopback or helper evidence;
- claim universal speed.

**Files written:**
- this document;
- `/home/ajs/.cache/pb-phase7/review-measurement-opus-r10/{probe.py,probe.log,execution.json,evidence.json}`.

**Changes and activity:**
- I edited no production, harness, test, contract, historical report or other worktree, and made no commit.
- I sent one message: the pause ACK.
- No processes are active.
- Execution remains paused pending explicit root release.
