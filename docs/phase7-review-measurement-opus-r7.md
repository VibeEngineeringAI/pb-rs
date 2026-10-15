# Phase 7 measurement, helper and tooling: independent Opus review, round 7

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (base `4a812d5`, Go pin `07af390`). This is a fresh review round of the frozen R6 correction successor (freeze `d606e10f…`, response `de997693…`, matrix `8bba8f12…`, bench `db1d7ee6…`). It accepts no phase, release, candidate or measured result. It authorizes no preflight, campaign, pilot, decoder run, profiler run, quiet window or lock acquisition. All earlier reports (R1–R6) and their evidence are preserved unchanged.

## Verdict

- **R6 corrections:** M1, M2, L1–L6, I1–I5 and the root's historical-line-default clone fix are implemented at source as dispositioned. I checked them against the current lines (table below). The clone is exact for the state classes I could trace, including default factories, per-line defaults, closures, codec state, pending ANSI, queries and private modes.
- **Two new medium findings concern the approved design itself:**
  - **R7-M1:** the eligibility screen is structurally coupled to the latency it screens.
  - **R7-M2:** a concrete pilot-extrapolation defect inflates every post-pilot allocation by the cap/actual serialization ratio, which I estimate at about 8–28×.
  - Both fail closed. Neither creates false eligibility.
- **Admission 1, functional CLI preflight: fit at source.** Sequencing caveat: `source_snapshot` binds every `harness/*.py`. Fixing R7-M2, or any bench-side R7-M1 change, after the preflight invalidates both 52-role preflights. Decide them first.
- **Admission 2, first truthful bounded A/A and controls: fit at source.** Unchanged from R6. A/A latency stays descriptive. Optional A/A diagnostics would also hit R7-M2.
- **Admission 3, primary, resource, diagnostic and interpret:**
  - **Resource-attribution CPU/RSS (non-long):** fit at source.
  - **Primary capture:** fit, and descriptive by construction.
  - **Diagnostic and interpret:** source-correct and fail-closed, but **not practically admissible** until R7-M2 is fixed and R7-M1 is dispositioned. Expect few or no eligible phases even after both.
  - **Long idle:** pooled latency is correctly descriptive. Pool booking very likely fails its own feasibility formula (R7-L2), and the fallback must be predeclared before the preflight.
- **Admission 4, profiler preparation and interoperability: not ready.** Source changed only by one `Readiness(case)` line. The driver's own `prepare-symbols` and the bounded real interoperability smoke test are still unrun.

## Scope, execution and evidence limits

**Execution:**
- My first and only shell attempt (with the disk-backed TMPDIR exports) failed with EDQUOT on the Claude Code task temp directory, and its output was lost. As instructed, I made no further shell attempt and used file tools only.
- I ran **no tests** (pure, mock or otherwise), no CLI, helper, compiler, probe, profiler, decoder, timing or lock.
- I computed **no SHA-256**. **No hash verification is reported by me.**
- I used no symlink workaround, did no cleanup, and edited no source, test, contract, historical report or other worktree.
- **Every finding below is source-only.**

**Supplied and not verified by me [O]:**
- The freeze, response, matrix, bench and other digests listed in `freeze.json`.
- The owner's 93 bench + 23 pure profile passes.
- The root's central successor: 93 bench in 92.720 s and 24 full profile in 1.383 s, including the authorized native analysis child (`docs/evidence/phase7/collector-r6-central-tests.json`, `collector-r6-parent-binding.json`). I read these JSONs but not the logs.
- The root's replay-clone evidence (`replay-clone-history-default-root{,-successor}.json`; read).
- Helper77, the native resize control and the release5 compatibility counts (`3268+6`, `160+30`, `17`, `564+4+4`).

Supplied logs are not my execution evidence, and passing tests are not acceptance. My statements bind to file contents read in this session, not to digests.

**Read in full:**
- `harness/phase7_bench.py` (2,814 lines).
- `docs/phase7-measurement.md` (394 lines).
- Reviews: `docs/phase7-review-measurement-opus-r6.md`, `-opus-r5.md` and `-r1.md` (original).
- `docs/evidence/phase7/tooling-r6-disposition.json`, including the approved pilot contract.
- From `collector-r6-corrections`: `response.json`, `freeze.json` and `actual-checks.json`.
- `collector-r6-central-tests.json` and `collector-r6-parent-binding.json`.
- Both replay-clone evidence JSONs.

**Read in part:**
- `harness/test_phase7_bench.py`: lines 560–1119 (R5 observer, R6, start of pool tests) and 1900–2092.
- `harness/phase7_profile.py`: lines 1–420.
- `harness/phase7.py`:
  - lines 120–488: Terminal, `proc_sample`, `spawn_helper`;
  - lines 762–1109: `useful`, `execute`/`_execute` through cleanup.
- `harness/contracts/phase7.md`: lines 130–289.
- `owned.diff`: lines 1–600, which are all the bench hunks through `main`. The test, doc and profile hunks were not read.
- `command-matrix.json`: lines 1–150, 15560–15649 and 16000–16039.
- `/home/ajs/.cache/pb-roadmap/phase7-final.md`: lines 40–123.

**Not read this round, so no coverage is claimed:**
- Earlier reviews and responses:
  - `phase7-review-measurement-interim-sol-r2.md`, `-opus-r3.md` and `-opus-r4.md`;
  - the R3/R4/R5 response and disposition JSONs;
  - the spawn-helper R1 and interim R2 reviews;
  - `harness-native-helper-r2-response.json`.
- `docs/phase7-harness.md`, `docs/phase7-profiling.md` and `docs/rust-phase7.md`.
- Other harness files: `test_phase7_profile.py`, `test_phase7.py`, `phase7_workloads.py`, `phase7_spawn.c` and `profile_external.py`.
- From `collector-r6-corrections`: `source-snapshot.json`, `finalize.py` and every log.
- The rest of `phase7.py` (1–119, 489–761, 1110–end), `phase7_profile.py` (421–end) and the matrix.
- `docs/evidence/phase7/` could not be enumerated without a shell. Any files there whose names I did not know are unread.

**Inherited, and labeled as such [I]:**
- **R2–R4 findings:** these come from R5's and R6's dispositions and from the brief's summary, rechecked only where the current lines below touch them.
- **Helper C-level lifecycle:** WNOWAIT reservation, exact `wait4`, errno handshake, signal policy and the narrow native policy. These come from R4's full read and Sol R2's executed checks; the supplied C hash is unchanged [O].
- **Known helper limitations, preserved and not re-litigated:**
  - fatal supervisor death leaves CLI resources and status unknown;
  - escaped `setsid`/`setpgid` and short-lived descendants;
  - path and check-to-exec TOCTOU;
  - cross-platform behaviour unknown.

**Labels:** [S] current source; [K] kernel, pyte or Python semantics, not exercised; [O] supplied; [U] unknown or unmeasured; [I] inherited.

The evidence record is `/home/ajs/.cache/pb-phase7/review-measurement-opus-r7/evidence.json`.

## R6 corrections: fresh disposition [S]

| Item | Status | Basis (current lines) and residual |
| --- | --- | --- |
| **R6-M1** (screen ≠ bias bound) | **Implemented as option (a) plus count equality** | `observer_eligibility` `bench:2541-2553` requires: primary Go–Rust; 5+30; \|Δ median T\| ≤ 1; max role-median T ≤ 1; equal median evaluation counts. It reports work, counts, deltas and an UNKNOWN scope. Residual: R7-M1 and I-3. |
| **R6-M2** (quadratic replay, memory) | **Resolved at source; actual cost [U]** | See note 1. Residual: R7-M2. |
| **Clone exactness** (root reproducer) | **Resolved at source** | See note 2. Residual: I-2. |
| **R6-L1** | **Implemented** | Equal median counts are required and reported (`:2544-2551`). Loop and syscall costs are disclosed as UNKNOWN. |
| **R6-L2** | **Implemented** | See note 3. |
| **R6-L3** | **Implemented and effective** | See note 4. |
| **R6-L4** | **Implemented for offline modes** | `main` wraps diagnostic-plan, interpret, pool-plan, pool and feasibility in `FunctionalOwnership(LOCK)` (`:2759-2778`). Residual: R7-L1. |
| **R6-L5** | **Process** | HEAD is still bound (`:1153-1157`). The root declares NO COMMIT for the complete program [O]. |
| **R6-L6** | **Implemented** | See note 5. |
| **I1** | **Resolved** | The validated identity must equal the originally bound snapshot (`:2273-2275`, `:2367-2369`). Tested (`test:998-1007`). |
| **I2** | **Disclosed** | Unchanged ordinary TOCTOU. |
| **I3** | **Workflow** | Root hash-binds the diagnostic at completion. Note that the report is rewritten once more after the wall-time stamp (`:2534-2537`), so binding must follow process exit. |
| **I4** | **Implemented** | Matrix `optional_descriptive` flags: `false` on primary non-long diagnostic plans (lines 15572 onward) and `true` on attribution (lines 16014 onward). |
| **I5** | **Resolved** | `contracts:264` now states the settled policy. |
| **Root objection** (preflight end binding) | **Resolved** | See note 6. |

**Notes:**

1. **R6-M2.**
   - `SessionReplay` keeps one forward byte and resize cursor (`:1787-1817`). Each phase start is cloned once, and each repetition clones from that state (`:1866-1870`).
   - Interpret re-derives each phase once from a fresh cursor (`:2606`, `:2621`).
   - `campaign_sessions` holds at most two raws, then descriptors (`:2305-2328`). Diagnostic and interpret load and drop one session at a time (`:2484`, `:2512`, `:2605`, `:2630`).
   - Progress is written per session (`:2496-2500`).
   - Interpret takes a declared ≤ 3600 s budget (`:2575-2578`).
2. **Clone exactness.**
   - `clone_terminal` (`:1752-1784`) builds a fresh `p7.Terminal`, so the clone has its own per-instance `StyledScreen`/`StyledHistory` classes. Their `Attributes` methods close over the clone's `unsupported` (`phase7.py:160-197`).
   - It deep-copies every Terminal field except screens, streams, decoder and reply. That covers `pending`, `queries`, `private_modes`, `transitions`, `byte_offset`, `cursor_shape`, `history_overflow` and `alternate` (`phase7.py:199-210`).
   - It restores `decoder.setstate` and copies stream `strict`/`use_utf8`.
   - It updates each screen `__dict__` (deep copy, minus buffer and history) *before* rebuilding lines. The target `buffer.default_factory`, a pyte lambda over the target screen's `default_char`, then reflects the clone's mode.
   - It preserves each source line's own `StaticDefaultDict.default` for buffer and history, and keeps history `ratio`, `size` and `position` through `_replace`.
   - Nonground parser state raises (`:1754-1755`). `Terminal.feed` only streams complete tokens, so this is rare: essentially C1 `\x9b` inside text [K].
   - The tests cover:
     - decoder, CSI and UTF-8 pending state;
     - SGR, queries and isolation;
     - default-factory identity;
     - the root's reverse-mode history-default reproduction with resize and future feed (`test:871-923`).
3. **R6-L2.**
   - Capture feasibility adds 60 s (`:2666`).
   - `run_campaign` checks the remaining guard budget and the approval lifetime against the allocation inside the window (`:1448-1452`, `:2387-2400`).
   - The pilot re-admits before every remaining session (`:2477-2482`).
4. **R6-L3.**
   - `Readiness(case)` classifies by `id(action['wait'])` (`:454-458`).
   - The identity holds end to end:
     - `Capture` passes the same deep-copied case object to `Readiness` and `p7.execute` (`:747-755`);
     - `_execute` passes `script[index]['wait']` itself (`phase7.py:993-1004`);
     - `ProfileCapture` passes its `case` to both (`profile:183-184`).
   - `Phases.checkpoint` deep-copies each wait (`:162`), so equal waits stay distinct. Action-only events use fresh `{}` objects that cannot alias a live excluded wait.
5. **R6-L6.**
   - Full synthetic 5+30 positive (0.9 ms), negative (1.1 ms), A/A and attribution (`test:829-869`).
   - Stamping without warmup upgrade (`:813-827`).
   - Precise end-check message plus a causal missing-recheck mutant (`:1027-1042`).
6. **Root objection.**
   - `pref_identity` is the first end-check record (`:2280`) and is returned in `file_identities` (`:2351`).
   - `end_bindings` is set only after a successful end check (`:2513`). Interpret recomputes and requires equality (`:2639-2641`).
   - The test (`test:758-773`) asserts the exact `parsed JSON evidence identity/path changed` error, no `end_bindings`, and four retained sessions. The mutant variant becomes valid when the preflight record is filtered, which shows the check is causal.

## New findings (most severe first)

### R7-M1 — Medium, decision semantics: eligibility is coupled to the latency it screens, so the instrument can essentially never confirm a trigger and may pass few or no phases [S][K], magnitude [U]

**Mechanism:**
- The live loop evaluates readiness once per iteration, including every 10 ms `select` timeout with no new bytes (`phase7.py:951`, `:990-1004`).
- `Readiness` counts and records every pre-match evaluation (`bench:470-478`), and replay re-runs each one, including empty feeds (`:1877-1883`).
- Each evaluation calls `useful` → `terminal.text()`, which rebuilds pyte `display` for every row (`phase7.py:777-778`) [K].
- So per-session evaluation count ≈ ⌈CLI compute time / 10 ms⌉ + reads, and replayed work T grows with it.
- `observer_eligibility` requires *equal* role-median counts and max role-median T ≤ 1 ms (`bench:2546-2548`).

**Trigger:**
- The trigger fires when the median increase exceeds both 10 ms and 10% (`measurement.md:211-214`).
- A phase where Rust's median first-expected is about 20 ms later than Go's gives Rust about 2 more no-data evaluations per session. Its median count differs, so the phase is ineligible. T also grows by about 2 full `text()` rebuilds, so the absolute bound likely fails too [U].
- The same happens if Rust is 30 ms *faster*.
- Eligibility therefore selects phases where both roles respond within about one poll of each other and replay ≤ 1 ms. Within that set a trigger is nearly unreachable, and "eligible and below trigger" is close to tautological.

**Why this matters:**
- The source is fail-closed: an ineligible phase never passes a budget (`measurement.md:296`).
- But the plan requires performance budgets for phase completion, and it treats statistically uncertain results as inconclusive (`phase7-final.md:73`, `:94`).
- The likely outcome of 24 × 3600 s primary diagnostic windows is that most phases are undecidable, and the crossing ones are exactly the ineligible ones. The docs acknowledge "few or no" eligible phases (`:371`), but no document says what an ineligible phase whose descriptive delta crosses the trigger means.
- R6 counted mismatched counts as a fail-closed mitigation. The coupling to the trigger itself was not identified.

**Minimal correction (decide before booking primary diagnostic windows):**
1. **Disposition, no source change.** Record that ineligible means *inconclusive for both pass and trigger*. Also record that an ineligible phase whose descriptive paired median delta crosses 10 ms + 10% is a material regression: it requires the plan's new-seed confirmation and investigation, and cannot be dismissed as observer artifact without a further control.
2. **Optional bench change for a decision-capable screen.** Classify evaluations as data-bearing (raw offset advanced) or no-data:
   - No-data evaluations run while the CLI has emitted nothing new. They are off the critical path except for at most one in-flight evaluation, because the observer is a separate process [K].
   - Bound observer delay by (data-bearing replay work + max single evaluation cost) per session.
   - Apply count equality only to data-bearing evaluations.
   - Optionally, have interpret emit per phase the descriptive paired median delta and a `trigger_crossing_while_ineligible` flag.

   This option changes `phase7_bench.py`, so it must precede the preflight.

### R7-M2 — Medium, liveness, concrete source defect: pilot extrapolation compares the remaining serialization *cap* with the pilot's *actual* bytes [S], magnitude [U]

**Where:**
- `campaign_diagnostics` overwrites each pilot's `serialization_bytes` with the actual evidence + progress size (`:2502-2505`). That value is always ≤ the cap, otherwise it raises.
- `pilot_allocation` computes each remaining session's target through `repetition_work`, which sets `serialization_bytes = 65536 + 8192·phases + 1024·sessions`, i.e. the cap (`:2238`, `:2415`). It then takes `ratio = max(value / basis)` (`:2418-2425`).
- For an identical remaining session the ratio is therefore cap / actual, not 1. This contradicts the approved contract's "max(1, remaining/pilot work-dimension ratios)" and the source's own comment (`:2236-2237`).

**Size estimate** (source-derived from the indent-2 JSON shape of `decode_phase` results, about 1.2 KB per latency phase) [K][U]:

| Case | Actual | Cap | Ratio |
| --- | --- | --- | --- |
| SQL-20000, 9 phases | ≈ 12 KB | 200,704 | ≈ 17 |
| Editor, 4 phases | — | — | ≈ 28 |
| Sustained SQL, 83 phases | — | — | ≈ 8 |

**Effect at the first admission** (58 remaining roles; reserve 58·8 + 180 + 60 = 704 s):
- Intended rule: 174·W + 704 ≤ remaining, so pilot whole-session wall W ≲ 16 s.
- With the defect at ratio 17: 2958·W + 704 ≤ remaining, so W ≲ 0.9 s, before subtracting the campaign revalidation and pilot elapsed time.
- A pilot session (load and twice-hash a multi-MB session, forward pyte feed, 10 clones, 45 timed replays, frame validation) is very likely above 1 s [U].
- So essentially every diagnostic ends "remaining budget/approval" incomplete after consuming an approved window. This fails closed, with no false eligibility.

**Why tests missed it:**
- `test_pilot_allocation_counts_caps_and_remaining_lifetime` builds pilot work with `repetition_work`, i.e. the cap on both sides (`test:974-981`).
- The full 5+30 and fit-failure paths mock `pilot_allocation` (`test:838`, `:1018`).
- The R5 two-pair campaign test runs the real allocation, but with millisecond walls, so the inflation is invisible.

**Minimal correction (bench-only, before the preflight):**
- Make the serialization dimension like-for-like. Either:
  - use the cap on both sides for the ratio, and keep the actual-≤-cap check plus the recorded actual; or
  - estimate the remaining sessions' serialization from the pilot actual scaled by the phase-count ratio.
- Add a regression that drives `campaign_diagnostics` with the *real* `pilot_allocation` over identical sessions and asserts `maximum_work_ratio == 1`, plus an injected-wall test of the 704 s / W arithmetic.

### R7-L1 — Low, coordination: `run` probes the helper and revalidates retained evidence before its QuietWindow, without the common lock [S]

**Where:**
- `run_campaign` calls `validate_preflight` before `QuietWindow` (`:1419` vs `:1445`), and `validate_preflight` calls `cached_spawn()` with `probe=True` (`:1661`).
  - That runs `cc --version` and, on first use in the process, the native capability-probe child (`phase7.py:423-425`, `:472-475`).
  - It also reloads and revalidates every retained preflight session of the campaign.
- Pool chunks additionally run `require_pool_feasibility`, a full-profile offline revalidation, before the lock (`:1424`, `:2050`).

**Effect:** launched while another lane holds the lock, these perturb that lane's quiet window, then fail at the nonblocking flock. This is the R6-L4 analog, which did not cover `run`.

**Correction:** a coordinator rule that `run` is launched only for its own agreed window with the lock free. Note that wrapping the pre-checks in `FunctionalOwnership` and then entering `QuietWindow` in the same process would need a release gap, because `flock` is per open file description [K].

### R7-L2 — Low, sequencing (R5-L1/R6-L2 residual): the long-idle seven-chunk design very likely fails its own formula, and its fallback must be predeclared before the preflight [S], actual [U]

**The formula:**
- Chunk allocation = 12 controls·30 + 60 + 10 roles·(300 + 1.5x + 8) + 55 = 3,555 + 15x (`:2656-2691`, `:2004-2016`).
- So the role's non-idle scheduling cost x must be ≤ 3.0 s.
- Live admission also needs issuance lead + 15x ≤ 45 s (`:1452`).
- A long-idle role's non-idle part (startup, 8 changes with 80 ms holds, quit drain, archive and config, hashing a session with about 6,000 proc samples, revalidation, dry extraction) is plausibly well above 3 s [U].

**Sequencing hazard:**
- `POOL_COUNTS` is a source constant (`:35`). Changing it after the preflight invalidates both preflights.
- One non-source lever exists: `--floor-sessions`, which the spec binds as `control_config`. Two floor sessions give x ≤ 11 s.
- The coordinator should predeclare the fallback, whether that is a control count or a source chunking change made before the preflight, now. Pooled latency remains descriptive either way (`:2063-2064`).

### Informational

- **I-1, approval reload inside the timed replay.** `feed_replay` calls `guard.check()` per 64 KiB block, inside `clock()` (`:1746-1749`, `:1876-1884`). For the QuietWindow guard that reloads the approval JSON every 250 ms. This adds sporadic noise to some repetitions; the per-session median over ≥ 5 repetitions mitigates it. The live loop has comparable `continue_running` work.
- **I-2, clone class identity.** Copied cursor attributes and characters keep the *source* Terminal's per-instance `StyledChar` class. They are value-equal; the clone's SGR and reset re-wrap them, and `frame()` serializes values. Benign [S][K].
- **I-3, per-session T is not bounded.** The screen bounds role-median T, not per-session T. Up to 49% of sessions may exceed 1 ms. The median trigger is robust in practice, because T rises with evaluation count, i.e. in already-slow sessions. Descriptive p95/p99 tails can carry larger observer bias. Report per-role max or p95 of per-session T.
- **I-4, guard checks during revalidation.** `validate_preflight` is not guard-checked internally (diagnostic-plan, `campaign_sessions`), so an overrun is bounded only by evidence size and is detected afterwards.
- **I-5, scope of "FULL" preflight.** In diagnostic-plan, "FULL" means all workloads for the campaign's own theme, size and long-idle options: 12–13 variants, 24–26 roles. It does not mean both themes' 52 roles. This is consistent with R5-L1's "per profile", but easy to misread.
- **I-6, lock hold time.** Each diagnostic-plan and interpret may hold the common lock for up to 3,600 s (unmeasured). The 24 required primary windows plus optional ones make this a material phase-6 scheduling load.

## Campaign/path/manifest boundary summary [S]

These are unchanged from R6 and rechecked:
- **JSON evidence:** a single-read parse and digest (`:81-90`), and an end recheck via resolve-strict, size and rehash (`:93-96`).
- **Raw sessions:** contained, non-symlink and size-capped, with streams equal to the payload. They are hashed, parsed and rehashed (`:1706-1726`), then rehashed by path at the end without a containment recheck (`:2345-2347`).
- **Executables and helper:** ordinary drift checks (`:1176-1187`, `:1734-1742`).
- **Preflight:** bound both into the end records (R6 root objection) and into the validated-identity equality (I1).
- **Residual:** ordinary check-to-use TOCTOU (A→B→A swaps between reads). No atomic or adversarial guarantee exists, and none is claimed here.

## Admissions: must-run validations versus source defects

**Source defects to disposition:**
- **R7-M2:** fix before the preflight.
- **R7-M1:** a disposition is required. A bench change is optional, and if made it must precede the preflight.
- **R7-L1 and R7-L2:** coordination and predeclaration.

**1. Functional CLI preflight (no timing): fit at source.** Must run:
1. Settle R7-M2 and any R7-M1 bench change, then a no-commit freeze. HEAD `4a812d5` stays.
2. The parent independently verifies the owned, consumed and helper hashes.
3. Prepare the helper with `cached_spawn(probe=True)`.
4. Run both 26-variant / 52-role preflights:
   - into new directories;
   - with the common lock free;
   - with budget ≤ 3600.
5. Each preflight reaches 52/52 valid roles, 26/26 valid pairs and valid dry extraction, with failures retained. This confirms the H−6 SQL and dense footers and row 16 [U].

**2. First truthful bounded A/A and controls: fit at source.** Must run:
1. A matching A/A preflight with the same canonical artifact and provenance.
2. `feasibility` fits, including the 60 s allowance and actual approval lifetime.
3. A fresh approval with a companion `campaign_id`.
4. Results are descriptive, and `budget_evidence` is false.

A/A campaign-decode and interpret are optional ΔT-noise characterization; they cannot validate the screen and also hit R7-M2.

**3. Timed campaigns and interpretation:**
- **Resource-attribution CPU/RSS (non-long):** fit at source. Requires a matching preflight, 5+30, A/A, fit, fresh approvals and retained fresh-seed confirmations.
- **Primary latency capture:** fit, descriptive.
- **Diagnostic and interpret:** must run diagnostic-plan → a separately approved campaign-decode whose pilot fits → root hash-binding → interpret.
  - Until R7-M2 is fixed, nearly all diagnostics will end incomplete.
  - Until R7-M1 is dispositioned, eligible results cannot be read as budget passes or trigger refutations.
- **Long idle:** an actual full-profile preflight fit is required before any pool spec (R7-L2). Pooled latency is descriptive; the idle-CPU trigger uses only the attribution pool with eligible descendants (`:1083-1085`).

**4. Profiler preparation and interoperability: not ready.** Must run:
1. The driver's own `prepare-symbols` against `profiling/release5/artifacts.json`.
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
- infer six-platform, installer, rollback or live HTTP/2 readiness from Linux loopback or helper evidence;
- claim universal speed.

Files written: this document and `/home/ajs/.cache/pb-phase7/review-measurement-opus-r7/evidence.json`. No other file was edited, and no process, lock, thread or message was created.
