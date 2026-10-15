# Phase 7 measurement, helper and tooling: independent Opus review, round 6

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (base `4a812d5`, Go pin `07af390`). This is a fresh review of the frozen R5 correction successor, delegated as a new task. It accepts no phase, release, candidate or measured result. It authorizes no timing, profiling, decoder run, quiet window or lock acquisition. All earlier reports, including R5, are preserved unchanged.

## Verdict

- **R5 corrections:** all four R5 findings and the root's preflight end-binding objection have correct source-level corrections. I checked the bindings line by line (table below). They are not rubber-stamped. Two new medium findings concern the corrected design itself, not its bindings.
- **Functional CLI preflight: fit at source.** No finding blocks the no-timing preflight. **Sequencing caveat:** `source_snapshot` binds every `harness/*.py` (and git `HEAD`). Any later bench correction for R6-M1/M2, or even a content-neutral commit, invalidates both 52-role preflights. Decide those dispositions, and a no-commit freeze, *before* running it.
- **First bounded truthful A/A and controls: fit at source.** This holds once a matching A/A preflight, feasibility and a fresh campaign-bound approval exist. A/A cannot validate the latency screen (R6-M1).
- **Timed campaigns:**
  - **Resource-attribution CPU/RSS (non-long):** fit at source after preflights, A/A, feasibility and approvals.
  - **Primary latency capture:** fit, but descriptive only, by construction.
  - **Primary latency interpretation:**
    - **Not ready to treat `eligible=True` as bias-bounded** (R6-M1).
    - **Practically unavailable for the high-phase cases.** For sustained SQL (83 latency phases), sustained dense (67) and very likely SQL-20000 and its delayed variant (9 each), the declared diagnostic cannot finish within its budget (R6-M2). It fails closed, so those phases stay descriptive.
  - **Long idle:** pooled latency is correctly descriptive. Pool booking still needs real feasibility; see also R6-L2 and R6-L3.
- **Profiler: not ready.** The source is unchanged since R5. The driver's own `prepare-symbols` and a bounded real interoperability smoke test are still unrun.

## Scope, execution and evidence limits

**Execution:**
- My first and only shell attempt failed with EDQUOT on the Claude Code task temp directory, and its output was lost. As instructed, I made no further shell attempt and used file tools only.
- I ran **no tests** (pure, mock or otherwise), no CLI, helper, compiler, probe, profiler, decoder, timing or lock.
- I computed **no SHA-256**, so I report **no hash verification**.
- I used no symlink workaround, did no cleanup and edited no source, test, contract, document or other worktree.
- **Every finding below is source-only.**

**Supplied and not verified by me [O]:**
- The freeze and its hashes: freeze `a27f8720…`, response `e1f175e9…`, matrix `5630ab02…`, bench `4e30aeca…`, shared `phase7.py` `dc7574f0…`, helper ELF `3b684c7b…`, C `0279d5fb…`.
- The owner's 79 bench + 23 pure profiler passes.
- The root's central results: 79 bench in 60.142 s and 24 full profile in 1.391 s, the latter including the authorized native analysis child (`docs/evidence/phase7/collector-r5-central-tests.json`, `collector-r5-parent-binding.json`).
- The root's helper 77 suite, the native resize control and the release5 compatibility counts.

Supplied logs are not my execution evidence, and passing tests are not acceptance. My statements bind to the file contents I read in this session, not to digests.

**Read in full:**
- `harness/phase7_bench.py` (2,533 lines) and `harness/test_phase7_bench.py` (1,796 lines).
- `harness/phase7_profile.py` (776 lines).
- `harness/phase7.py` (1,718 lines): the terminal, `proc_sample`, the resolver, `NativeChild`, the capability probe, `useful`, `execute`/`_execute`, `sample`, `validate`/`compare`, `load_session`, the statistics routines, `observer_floor` and the CLI.
- `docs/phase7-measurement.md` and `docs/phase7-harness.md`.
- Measurement reviews: R1, interim Sol R2, and Opus R3, R4 and R5.
- Spawn-helper reviews R1 and interim Sol R2.
- `/home/ajs/.cache/pb-phase7/harness-native-helper-r2-response.json`.
- The R5 `freeze.json`, `response.json` and `actual-checks.json`.
- `docs/evidence/phase7/collector-r5-central-tests.json` and `collector-r5-parent-binding.json`.
- `tooling-r6-assignment.json` and the prepared brief.
- `collector-opus-r4-disposition.json` and `collector-opus-r3-response.json`.
- `collector-r4-resize-integration/response.json`.

**Read in part:**
- `owned.diff`, lines 1–400: the bench hunks through `pool_definition`. The test and doc hunks were not read.
- `command-matrix.json`, lines 1–440, 1460–1590, 8840–9160, 9300–9450, 10500–10900, 11500–11900 and 12400–12900.
- `harness/contracts/phase7.md`, lines 140–279.
- The accepted plan `/home/ajs/.cache/pb-roadmap/phase7-final.md`, lines 50–123.
- `docs/rust-phase7.md`, lines 1–150.

**Not read, so no coverage is claimed:**
- `source-snapshot.json`, `finalize.py` and every log: R5 work and final logs, `cli-order.log`, central bench and profile logs.
- `test_phase7_profile.py`, `test_phase7.py`, `phase7_workloads.py`, `profile_external.py`, `phase1`/`2`/`3.py` and `run.py`.
- `docs/phase7-profiling.md`.
- The original R1 and interim-R2 collector response JSONs and the original R4 response.
- Helper freezes and native-control durable records.
- The `docs/evidence/phase7/collector-r5-response.json` copy, so its equality with the cache response is unverified.
- The R5 `evidence.json`.
- The rest of the matrix.
- I could not enumerate `docs/evidence/phase7/`, because listing requires the shell. Any disposition files there whose names I did not know are unread.

**Inherited, and labeled as such:**
- `phase7_spawn.c` C-level lifecycle: WNOWAIT, release, exact `wait4`, the errno handshake and the signal policy. These rest on R4's full read and Sol R2's executed helper checks, and the supplied C hash is unchanged.
- The `test_phase7.py` dispatch mock-clock regressions and the native resize control are [O].
- I re-derived the Python-side lifecycle from the current `phase7.py`.

**Evidence labels:**
- **[S]** read from the current source.
- **[K]** kernel, pyte or Python semantics, from knowledge and not exercised here.
- **[O]** supplied by the owner, root or parent.
- **[U]** unknown or unmeasured.

The evidence record is `/home/ajs/.cache/pb-phase7/review-measurement-opus-r6/evidence.json`.

## R5 corrections: independent verification [S]

| Item | Status | Basis (current lines) and residual |
| --- | --- | --- |
| **R5-M1 gap 1** (diagnosed sample ≠ measured sample) | **Resolved** | See note 1. |
| **R5-M1 gap 2** (per-evaluation predicate cost) | **Resolved for the replayed components** | See note 2. Residuals: R6-M1 (what the screen bounds), R6-L1 (unreplayed loop work), R6-M2 (cost). |
| **R5-M1 interpretation rules** | **Implemented as designed** | See note 3. Soundness: R6-M1. |
| **Root objection: preflight end binding** | **Resolved** | See note 4. Residual TOCTOU is below. |
| **R5-L1** (seven-chunk feasibility) | **Implemented; actual fit [U]** | See note 5. Residuals: R6-L2 and R6-L3. |
| **R5-L2** (per-chunk authorization) | **Resolved** | See note 6. |
| **R5-L3** (dense partial paint) | **Resolved at source; actual layout [U]** | See note 7. |

**Notes:**

1. **R5-M1 gap 1.**
   - `run` rejects `--decoder-report` (`bench:1404-1405`), and capture latency is always false (`:1418`, `:1071`, `:1129`).
   - `decoder_interpretation` raises on any preflight report (`:1885-1889`).
   - `campaign_sessions` revalidates every warmup and measured pair and replays only measured sessions (`:2197-2220`).
   - `interpret_campaign` requires the exact measured `(pair, role)` set (`:2324-2326`).
2. **R5-M1 gap 2.**
   - `Readiness` records each pre-match evaluation: timestamp, absolute raw offset, a deep-copied TTY, the trace only for request waits, and qualification (`:454-480`).
   - `bind_decoder_offsets` retains them in schema 2 (`:553-572`).
   - `decode_phase` validates order, monotonicity, last-only qualification and the TTY (`:1764-1783`). It then replays feed, `useful`, `frame` and geometry plus the recorder at the exact boundaries, inside the timed region (`:1806-1819`). Any requalification mismatch fails.
   - It matches the live structure: one stdout read, then one evaluation per loop iteration, including 10 ms no-data polls (`phase7.py:951-1004`).
3. **R5-M1 interpretation rules.**
   - 5–30 repetitions collapse to a per-session median (`:2350-2352`), and the delta is taken between role medians across sessions (`:2357-2358`).
   - Eligibility requires primary, Go–Rust and ≥5+30 (`:2359-2360`).
   - The interpretation is a new artifact (`:2301-2302`). Metrics are recomputed from raw (`:2353`), and there is no subtraction.
4. **Root objection.**
   - `pref_identity` enters the end-check records (`:2176`).
   - `campaign_end_check` re-snapshots it (`:2236-2238`) and returns it in `file_identities` (`:2245`).
   - `end_bindings` is written only after a successful end check (`:2283`). `interpret` recomputes and requires equality (`:2372-2374`) and re-reads the preflight against the campaign's bound identity (`:2167-2170`).
   - The test (`test:748-762`) mutates `preflight.json` during `decode_phase` and asserts an invalid result, no `end_bindings`, four retained sessions and unchanged original bytes.
   - Weaknesses: the test asserts only that errors are non-empty, not which check failed. `offline_preflight_check` is mocked in that context (`test:668-674`).
5. **R5-L1.**
   - `require_pool_feasibility` checks the full matching preflight for the selected profile and the five-pair allocation at unchanged 30 s, 8 s and 30+5·pairs reserves (`:1936-1948`).
   - It is enforced in pool-plan, chunk admission and reduction (`:1960`, `:1982-1984`, `:2025`).
   - The matrix pool plans pass `--theme dark`/`light`, giving 12 controls. With both themes, 24 controls take the zero-cost allocation to 3,855 s, which can never fit. That fails closed.
6. **R5-L2.**
   - `retained_authorization` binds campaign and role boundaries, the approval and companion `campaign_id`, issue and expiry, and the budget (`:1898-1933`).
   - The pool requires distinct IDs and paths (`:2018-2024`), and native lifecycle within the role boundary (`:2084-2086`).
7. **R5-L3.**
   - `dense_table_geometry` checks the footer at H−6, ending at W−3, with both borders (`:527-537`). It is applied at readiness (`:502`) and post hoc (`:900-901`).
   - I checked the default 120×40, 256-point schedule: no target footer is a W−3-aligned suffix of the preceding stale footer.
   - Row H−6 itself is source-derived until the actual preflight.

## New findings (most severe first)

### R6-M1 — Medium, latency interpretation soundness: the ≤1 ms screen bounds the difference of total replayed observer work, not the difference of observer-induced latency [S][K], magnitude [U]

**What the replay measures.**
- For each role-session, `observer_replay_ms` is the replayed cost **T** of *all* observer work between dispatch and first match (`bench:1811-1819`).
- The screen is `|median(T_cand) − median(T_ref)| ≤ 1 ms` (`:2358-2360`).

**What enters `first_expected`.**
- The stamp `now` is taken after the read and feed of the qualifying chunk (`phase7.py:968-992`).
- The observer-induced part **D** is therefore the time from the qualifying bytes becoming readable to `now`. It is not T.
- Observer work done while the CLI is still computing, in a separate process on another core, overlaps that computation and is off the critical path. Work done after the bytes arrive is on it. With output larger than the PTY buffer, the CLI's writes block on the observer and the work serializes [K].
- So for each role D lies roughly in [feed of the last chunk, T]. The bias is β = D_cand − D_ref ∈ [−T_ref, +T_cand].
- |ΔT| ≤ 1 ms does **not** bound |β| unless both T are themselves small.

**Concrete trigger.**
- Same bytes and evaluation sequence for both roles, so ΔT ≈ 0.
- One role computes and then writes the frame in one burst, so its decode is serialized after the bytes arrive (D ≈ T).
- The other streams the same frame in parts while still computing, so most decode overlaps its compute (D ≈ last chunk).

**Scale.**
- T is not small. `Terminal.frame()` rebuilds a 16-entry colour tuple and copies every cell (`phase7.py:218-238`). That is 4,800 cells at 120×40 and 6,300 at 140×45.
- pure-Python pyte decodes character by character. One full styled frame plausibly costs tens of ms per evaluation [K][U].
- A β of that order can cross or mask the 10 ms + 10% trigger, with ΔT ≈ 0.

**Mitigations:**
- In practice the screen is strict. Differing byte volumes or evaluation counts push ΔT past 1 ms, so most mismatched phases fail closed.
- Single-frame phases with identical write structure are probably benign.
- Identical-binary A/A cannot detect this, because both roles share one timing structure.

**Reach:** every phase interpret marks eligible. Capture, resources, A/A and attribution are unaffected. The docs call the screen "conservative" (`measurement.md:283`, `:357`); that holds for matched work, not for bounded bias.

**Minimal correction (bench-only; choose before the preflight):** one of the following.
- **(a)** Keep the screen and add an absolute bound, so eligibility needs `max(median T_ref, median T_cand) ≤ 1 ms`. Then |β| ≤ 1 ms by construction. Expect few or no eligible phases; that is an honest result.
- **(b)** Time each replayed evaluation separately. Combine those costs with the live `evaluated_monotonic_ns` gaps to find the trailing saturated run, where live gap ≈ replayed cost and no idle select. Bound D_role by that run's cost B_role. Then either require `max(B) ≤ 1 ms`, or treat a trigger decision as interpretable only if it is the same for every β ∈ [−B_ref, +B_cand]. That is a decision-invariance check, not subtraction, and reported values stay unchanged.
- **(c)** Record a coordinator disposition that eligibility means "matched observer work" only, report the T medians beside every trigger, and treat decisions within max(T) of either arm as non-interpretable.

### R6-M2 — Medium, liveness/feasibility: campaign-decode and interpret do quadratic prefix replay with every session resident, under 600 s budgets, so the high-phase cases cannot be interpreted [S], magnitude [U]

**Where:**
- **Prefix replay:** for each phase and each repetition, `decode_phase` builds a fresh `Terminal` and re-feeds the whole prefix `[0:begin]` untimed (`bench:1792-1804`).
  - Untimed prefix work per diagnosed session is ≈ R·Σᵢ beginᵢ ≈ R·N·S/2, for N latency phases and stdout size S.
  - `interpret` re-derives every phase once more, inside a hard-coded `FunctionalGuard(600)` (`:2306`, `:2346`), which also covers revalidation of all 70 roles (`:2308`).
- **Memory:** `campaign_sessions` keeps every measured raw session resident (`:2219-2220`). That is 60 sessions, each with every checkpoint's full styled frame, stdout and evaluation histories.
- **Report rewrites:** `campaign_diagnostics` rewrites the growing diagnostic JSON after every phase (`:2281`), which is quadratic serialization.
- **Budgets:** the matrix declares `--budget-seconds 600` for every campaign-decode (for example lines 11668-11691) and gives the latency phase counts: sustained SQL 83, sustained dense 67, SQL-20000 and delayed 9 (lines 9119-9136).
- **No feasibility estimate:** `campaign_feasibility` covers captures only, not diagnostics.

**Arithmetic (R = 5, 60 sessions, pyte feed rate F):**

| Case | Pyte feed | Fits only if |
| --- | --- | --- |
| Sustained SQL diagnostic | ≈ 12,450·S | S ≤ 0.048·F within 600 s, about 48 KB at a generous 1 MB/s |
| Sustained SQL, even with a 3,600 s approval | ≈ 12,450·S | S ≤ ~0.29 MB |
| Interpret (sustained) | ≈ 2,490·S | S ≤ 0.24·F |
| SQL-20000 diagnostic | ≈ 1,350·S | S ≤ 0.44·F |

An 85-checkpoint sustained session cannot plausibly stay under these bounds [U].

**Trigger and effect:**
- The guard expires: QuietError is raised at `:2282`, or RuntimeError in interpret. The result is invalid, which is correctly fail-closed.
- Each attempt still consumes a separately approved quiet window and the common lock.
- The kinds R5-M1 called most trigger-prone (window-network, cache-turnover, sustained-scroll, series-scroll and delayed phases) stay permanently descriptive.
- Memory pressure is an added risk [U].

**Minimal correction (bench-only; decide before the preflight):**
1. Make one forward pass per session in byte order. At each latency phase's `begin`, take an untimed deep copy of the terminal state; the code already deep-copies pyte screens at `:484-486`. Run each repetition, and interpret's single re-derivation, from a fresh copy. That makes the work O(S) per session.
2. Decode and drop one session at a time, keeping only the identity records.
3. Write progress per session, not per phase.
4. Replace interpret's hard-coded 600 s with a declared, recorded CLI budget.
5. Predeclare diagnostic feasibility from the matching preflight. The Σbegin byte counts need no timing. Record the actual diagnostic wall time so later bookings can be calibrated.
6. Size each campaign-decode budget, for example 600 s for small cases and up to 3,600 s for long ones, before booking.

### R6-L1 — Low: per-iteration live loop work is excluded from the replay [S], magnitude [U]

Each live iteration before first match also runs:
- `native.poll()`, `continue_running()` (QuietWindow reload every 250 ms), `select`, `os.read` (`phase7.py:917-983`);
- `termios_state` and `tcgetattr` (`:1002`);
- the 50 ms root proc sample (`:927-937`).

The replay passes the *recorded* TTY and calls none of these. The cost scales with evaluation count, which is role-dependent, or with elapsed time. These are probably microseconds per iteration, but a difference of about 100 evaluations approaches the 1 ms screen.

**Correction:** report median evaluation counts per role. Either predeclare an evaluation-count-delta guard or add an equivalent untimed-to-timed stub. The response already lists these as "other observer components UNKNOWN", so this is a disposition gap, not an overclaim.

### R6-L2 — Low: long-chunk feasibility ignores the approval's own lifetime [S]

`estimated_fits` needs `reserve ≤ budget ≤ 3600` (`:2417`), and the boundary test accepts exactly 3,600 s (`test:942`). The long-chunk runs use `--budget-seconds 3600` (matrix lines 1517 and 1567).

The binding limit, however, is the approval. Its expiry is at most `issued + 3600`, and `issued ≤ start` (`:642`, `:1906`). Any issuance lead therefore removes slack, from a total of only ~105 s at zero cost. A design that "fits" at 3,600 s cannot finish unless the approval is issued at the very instant of launch.

**Correction:** at admission, require `expires_unix − now ≥ allocation`, or predeclare an issuance lead L and require `reserve ≤ 3600 − L`.

### R6-L3 — Low: the recorder retains about 30,000 never-replayed idle evaluations per long-idle session [S], cost [U]

The `idle-end` wait has `min_elapsed_ms` = 300 s. So every 10 ms evaluation is a pre-match evaluation and is recorded (`:463-471`), with a TTY deep copy. That is about 30,000 records, which are then deep-copied and serialized twice (`:569-570`, `:778`, `phase7.py:1225-1229`).

Idle and anchor phases are never decoded (`:1742-1743`). The added post-processing counts against the ~7 s non-idle headroom R5-L1 leaves for each long-idle role.

**Correction:** skip evaluation history when the wait has `min_elapsed_ms`, or for non-latency checkpoints. The preflight measures the effect either way, so this is not a correctness issue.

### R6-L4 — Low: offline-heavy modes take no common lock [S]

`interpret`, `pool`, `pool-plan` and `feasibility` revalidate retained sessions: they load JSON, run `observation_issues` and `compare`, and interpret also re-decodes with pyte (`:2479-2498`). None of them takes `FunctionalOwnership` or a QuietWindow. Run during another lane's quiet window, they perturb it. This is the R4-L3 analog.

**Correction:** take a nonblocking `FunctionalOwnership` (`:589-606`) for these modes, or record a coordinator scheduling rule.

### R6-L5 — Low, process: source binding includes git `HEAD` [S]

`source_snapshot` includes `git rev-parse HEAD` (`:1146-1150`). Equality is required by:
- the preflight (`:1645`);
- the campaign end check;
- `campaign_sessions` (`:2145`);
- interpret (`:2312`);
- the pool (`:1999`).

A content-neutral commit therefore invalidates every retained preflight, campaign, diagnostic and pool binding. The branch is heavily uncommitted (`git status`), while the program is multi-day: 104 preflight roles, 76 campaigns, 76 diagnostics, interprets and 4 pools.

**Correction:** declare a no-commit freeze for the whole program, or record `HEAD` descriptively and bind file hashes only. Decide before the preflight.

### R6-L6 — Low, test coverage: the eligibility branch is never exercised positively [S]

- The only interpret test uses two pairs and asserts `eligible=False` (`test:682-721`, `:700`). A flipped `<= 1.0` comparison, or wrong per-phase or per-session stamping (`:2365-2371`), would pass every test.
- No interpret test checks that a 5+30 A/A or attribution campaign stays ineligible.
- The preflight-mutation test does not assert which check failed (`test:759`).

**Correction:** add a synthetic 5+30 primary Go–Rust case with ΔT 0.9/1.1 ms. Add the same with `comparison='aa-go'` and with `observer_policy='resource-attribution'`. Assert the specific end-check message.

### Informational (no correction required for safety)

- **I1 — preflight double read.** `campaign_sessions` snapshots the preflight (`:2168`), then `validate_preflight` reads it again (`:1631`) and checks only its own identity. An A→B→A swap could validate content B while binding A.
  - Fix: compare `validated_report_identity` with `pref_identity`.
- **I2 — raw sample reads.** `retained_sample` hashes, then parses with a separate read, then rehashes (`:1703-1713`). A swap that is restored in between is undetectable. The end check rehashes by path, without a containment or symlink recheck (`:2239-2241`).
  - These are ordinary TOCTOU gaps, consistent with the stated non-atomic scope.
- **I3 — diagnostic timing values are trust-on-write.** `observer_replay_ms` and `observer_clock.injected_test_clock` cannot be re-derived. The diagnostic's identity is bound only when interpret reads it.
  - The parent should hash-bind each diagnostic report externally at completion.
- **I4 — diagnostics that can never enable anything.** 52 of the 76 declared campaign-decode windows (24 attribution plus 28 long-idle chunk windows across both policies) can never enable eligibility. Mark them optional and descriptive, or drop them.
  - The matrix labels the old `decode` templates `historical_preflight_decoder_commands` (line 9393). No A/A preflight or campaign template appears in the portions I read.
  - Long-idle descriptive pooled latency loses no interpretable data. Its latency phases repeat the non-long SQL-20000 definition, which has its own 5+30 campaign.
- **I5 — stale contract paragraph.** `contracts/phase7.md:264` keeps a stale "NOT published/implemented" paragraph. It is superseded by `:268-272`.

## Campaign/path/manifest boundary summary [S]

- **JSON reports** (campaign, diagnostic, preflight, controls, pool spec and approvals): one `read_bytes` is parsed and digested from the same bytes (`:80-89`). They are rechecked at the end via resolve-strict, size and rehash (`:92-95`).
- **Retained raw sessions:**
  - path-contained (no absolute path or `..`, resolved within the root), no final-component symlink, size-capped (`:1694-1714`);
  - stream bytes must equal the session payload;
  - rehashed at the end;
  - the original report bytes are untouched by interpret (`test:704`, `:721`).
- **Executables:** canonical identity before and after each role and at the end (`:1169-1180`, `:1216-1245`). There is no atomic exec binding, as disclosed.
- **Helper:** `check_helper_files` rejects symlinks, requires the canonical path and rehashes the ELF and sidecar (`:1722-1730`). The resolver rejects keyed aliases (`phase7.py:436-438`).
- **Residual:** ordinary check-to-use TOCTOU (I1, I2). There is no adversarial or atomic guarantee, and none is claimed here.

## Inherited findings (rechecked against the current lines where read; no regression found)

Native supervisor and WNOWAIT protocol use is unchanged (`phase7.py:483-682`). Each other area holds at these lines:

| Area | Current source |
| --- | --- |
| Guarded proc samples | `:375-400`, with `same_process` bracketing (`:928-936`) |
| First-ever stamp | Never reset within an action (`:1016`, `:1064`) |
| Dispatch origin | After readiness, digest and frame work (`:1040`, `:1046`) |
| Changed-only resize policy | Validated before acquisition (`:835-849`), dispatched at `:1043-1052` |
| Mandatory group cleanup | `:1084` |
| QuietWindow | Approval, then nonblocking lock, with campaign and companion binding (`bench:623-693`) |
| FunctionalOwnership | `:589-616` |
| Truthful A/A | `:575-586` |
| Seeds and pooling | `:1951-2131` |

**Known limits, preserved and not re-litigated:**
- fatal supervisor death leaves CLI resources and status unknown;
- adopter reap;
- escaped `setsid`/`setpgid` and missed short-lived descendants;
- spawning-thread lifetime and SIGCHLD ownership;
- syscalls that are not hard realtime, and about 8–10 s of unwind overrun;
- path and check-to-exec TOCTOU;
- Linux-only, with cross-platform behaviour unknown;
- native receipt, reducer, render and write stages unimplemented and unknown.

## Admissions: must-run validations versus source defects

**Source defects to disposition:**
- R6-M1 and R6-M2 (medium).
- R6-L1 through R6-L6 (low).

**Ordering:** because the source snapshot binds `harness/*.py` and `HEAD`, settle R6-M1, R6-M2, R6-L3, R6-L5 and R6-L6 *before* the functional preflight. Otherwise both 52-role preflights must be rerun.

**1. Functional CLI preflight (no timing).** Must run:
1. Final release5 artifacts are fixed, and the no-commit freeze is declared.
2. The parent independently verifies the owned, consumed and helper hashes on that revision.
3. The helper is prepared, with `cached_spawn(probe=True)`.
4. Both 26-variant / 52-role preflights run:
   - into new directories;
   - with the common lock free;
   - with budget ≤ 3600; the total including 2×360 s long-idle roles per policy is [U].
5. Each preflight reaches 52/52 valid roles, 26/26 valid pairs and valid dry extraction, with every failure retained. This also confirms the H−6 footer rows for SQL resize and the dense table.

**2. First truthful bounded A/A and controls.** Must run:
1. A matching `aa-go` or `aa-rust` preflight with identical canonical artifact and provenance. Predeclare its commands; I found none in the matrix portions I read.
2. `feasibility` fits.
3. A fresh approval whose companion carries the same `campaign_id`.
4. Results remain descriptive, and `budget_evidence` stays false.

Optionally, campaign-decode and interpret A/A to characterize ΔT noise. This does not validate R6-M1.

**3. Timed campaigns.**
- **Resource-attribution CPU/RSS (non-long).** Prerequisites: matching attribution preflight, 5+30, A/A done, feasibility fit, fresh approvals, fresh-seed confirmations retained.
- **Primary latency.** Captures may run. Before any `eligible=True` is used as trigger evidence:
  - disposition R6-M1;
  - correct or budget R6-M2, with a diagnostic feasibility estimate and calibrated budgets;
  - each diagnostic runs under its own approval and ID;
  - interpret runs only after the diagnostic is hash-bound (I3).
  Until then, all latency stays descriptive.
- **Long idle.**
  - The pool must not be booked before an actual full-profile preflight fits at unchanged reserves. Count the approval lead (R6-L2) and recorder overhead (R6-L3) in that fit.
  - Pooled latency remains descriptive, which is consistent with the plan, with no overclaim found in source or docs.
  - The idle-CPU trigger uses only the attribution pool, which needs `descendant_comparison_eligible` true and an empty descendant set (`:1076-1078`).

**4. Profiler preparation and interoperability.** Unchanged since R5 at source. Must run:
1. The driver's own `prepare-symbols` against `profiling/release5/artifacts.json`.
2. A bounded real smoke test covering:
   - perf, strace and heaptrack FD3 / `profiler.stderr` separation;
   - exact CLI stderr under heaptrack preload;
   - heaptrack raw naming;
   - strace `= ?` parsing;
   - perf report and `$HOME/.debug` side effects;
   - symbol resolution.
3. All functional controls valid.

**Never:**
- subtract floors, holds or observer replay;
- treat attribution, A/A or pooled latency as trigger evidence;
- infer cross-platform, live HTTP/2 SQL, packaging, installer or rollback readiness from Linux loopback or helper evidence;
- claim universal speed.

Files written: this document and `/home/ajs/.cache/pb-phase7/review-measurement-opus-r6/evidence.json`. No other file was edited, and no process, lock, thread or message was created.
