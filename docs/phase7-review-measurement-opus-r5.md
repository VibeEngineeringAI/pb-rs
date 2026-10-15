# Phase 7 measurement, helper and tooling: independent Opus review, round 5

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (base `4a812d5`, Go pin `07af390`). This is a fresh, joined review of the frozen collector/profile successor and the settled shared resize policy, delegated as a new task. It accepts no phase, release, candidate or measured result, and it authorizes no timing, profiling, decoder run or quiet window. The original R4 report is preserved unchanged.

## Verdict

- **Functional preflight admission: fit at source.** No finding blocks the no-timing preflight. Must-run conditions are below.
- **First bounded truthful A/A and control tooling: fit at source** once its own matching A/A preflight, a feasibility result and a fresh campaign-bound approval exist. A/A never enables triggers, so R5-M1 does not block it.
- **Go–Rust timed campaigns: split verdict.**
  - **Resource-attribution CPU/RSS campaigns (non-long):** fit at source after the preflights, A/A and feasibility.
  - **Latency-trigger interpretation:** not ready until R5-M1 is fixed or dispositioned. Until then, latency must be reported as descriptive.
  - **Long-idle pooled campaigns:** need the R5-L1 feasibility decision before any booking.
- **Profiler interoperability: not ready.** The driver's own `prepare-symbols` and a real bounded interoperability smoke test have not run.
- **R4 findings:** M1–M4 and L1–L6 all have source-level corrections. One medium and three low findings are new.

## Scope, execution and evidence limits

**Execution:** one bounded shell attempt failed with EDQUOT on the Claude Code task temp directory, and its output was lost. No second attempt was made. Everything after that used file tools only. I ran no tests, CLI, compiler, probe, profiler, decoder, timing or lock, and computed no SHA-256. I used no symlink workaround and did no cleanup. **Every finding below is source-only.**

**Supplied and not verified [O]:**
- All seven owned and five consumed source hashes. These include bench `500d5c8d…`, profile `85c9f578…`, `phase7.py` `dc7574f0…`, test `97f19258…`, C `0279d5fb…` and ELF `3b684c7b…`.
- The central 70 bench + 24 profile passes, which include the native analysis child.
- The root's 77 helper passes, which include the native resize opt-in.
- The native resize control (`helper-resize-r4-native-control.json`, read but not executed).
- 160/160 styled, 30/30 documentary and 17/17 device/login passes.
- The release5 profiling preparation.

Supplied logs are not my execution evidence, and passing tests are not acceptance. My statements are bound to the file contents I read in this session, not to digests.

**Read in full:**
- `harness/phase7_bench.py` (2,180 lines) and `harness/test_phase7_bench.py` (1,454 lines).
- `harness/phase7_profile.py`.
- `harness/phase7.py` lines 1–1268: Terminal, `proc_sample`, `spawn_helper`, NativeChild, the capability probe, descendants, `useful`, `execute`/`_execute` including the resize policy, and `sample`.
- `docs/phase7-measurement.md` and the R4 review.
- The R4 disposition and response JSONs.
- The resize-integration `response.json` and `freeze.json`.
- `helper-resize-r4-native-control.json`, `collector-r4-central-tests.json` and `release5-profiling-preparation.json`.
- `pkg/model/tablekeymap.go`.

**Read in part:**
- `owned.diff`, the bench hunks only (lines 1–250).
- `harness/contracts/phase7.md` lines 150–273.
- `docs/phase7-profiling.md` lines 60–108.
- The accepted plan, lines 55–123.

**Not read this round, so coverage is not claimed:**
- `phase7.py` from line 1269 to the end (`observer_floor`, `validate`, `compare`, CLI).
- `test_phase7.py` and `test_phase7_profile.py`.
- `phase7_spawn.c`, which is unchanged per the supplied hash and was read in full in R4.
- `phase7_workloads.py`, `profile_external.py` and `phase1.binary_identity`.
- `docs/phase7-harness.md`.
- The command matrix, static checks and all logs.
- The durable copies `collector-r4-parent-binding.json`, `helper-resize-r4-freeze.json` and `release5-ui-checkpoint.json`.
- The R1, Sol R2 and Opus R3 reports, spawn-helper reports and responses. For these I relied on R4's full read and its disposition table. Inherited items were rechecked only against the current source lines cited below.

**Evidence labels:**
- **[S]** read from the current source.
- **[K]** kernel, pyte or library semantics, from knowledge and not exercised here.
- **[O]** supplied by the owner, root or parent.
- **[U]** unknown or unmeasured.

The evidence record is `/home/ajs/.cache/pb-phase7/review-measurement-opus-r5/evidence.json`.

## New findings (most severe first)

### R5-M1 — Medium, blocks latency-trigger interpretation: the decoder screen does not match the campaign sessions and omits per-evaluation observer cost [S][K], magnitude [U]

**Where:**
- `decoder_interpretation` (`phase7_bench.py:1801-1862`) derives one `eligible` flag per case and phase from the decoder report. The flag requires `|median(candidate decode_ms) − median(reference decode_ms)| ≤ 1.0` (`:1856-1857`).
- That report replays the **functional preflight's single retained capture per role** (`decoder_diagnostics`, `:1763-1798`, via `retained_sample` of the preflight rows).
- `run_campaign` then stamps that flag onto **every** measured session of the 30-pair campaign (`:1434-1439`). It never compares the campaign sessions' own byte ranges.
- Every campaign capture already retains exact `decoder_offsets` (`Capture.__call__` → `bind_decoder_offsets`, `:714`, `:513-530`), so the needed comparison data exists.

**Two independent gaps:**

1. **The diagnosed sample is not the measured sample.**
   - First-match byte volume varies from session to session in exactly the phases most likely to approach triggers:
     - burst scroll and sustained phases, where the number of intermediate frames depends on input coalescing and renderer tick timing;
     - delayed-window phases, where `fetching...` and spinner repaints during the 150 ms delay accumulate;
     - startup.
   - One preflight capture per role does not bound the 30-session median byte delta.
   - Trigger: Go's preflight sample coalesces a 40-key burst into a few frames, while its median campaign session renders more (or the reverse). The screen then reports ≤1 ms although the campaign medians differ by more.

2. **Per-evaluation predicate cost is outside the timed region.** The replay times only `feed_replay` of `[begin:first]` (`:1733-1739`). In the live loop, each iteration that reads data also runs the readiness predicate before the next read (`phase7.py:992-1004`):
   - `p7.useful` calls `terminal.text()`, which rebuilds pyte's `display` for every row on every evaluation [K];
   - when tokens are already present, `Readiness` also builds a full `terminal.frame()`, compares cells and checks geometry (`phase7_bench.py:462-476`).
   - For resize phases, stale `rows 1/N` and `bench-row-000000` tokens satisfy `useful` immediately. Every post-dispatch read before qualification therefore pays a full `frame()` at 140×45. This is the R4-M1 second-paragraph residual, which the R4 response did not address.
   - The number of such evaluations equals the number of PTY reads before the first match. That is a property of each implementation's write and flush granularity, so it is role-dependent [U]. It lands inside `first_expected` but is absent from the screen.

**Reach:** every primary Go–Rust latency-trigger decision that the 1 ms screen would enable. Functional comparison, resource metrics, A/A and attribution are unaffected.

**Is the 1 ms allowance sound?** Partly.
- The plan's latency trigger needs both a 10% and a 10 ms median increase (measurement doc `:211-214`). A 1 ms bound on a *complete, matched* observer delta is a reasonable descriptive screen: it is one tenth of the absolute arm and is not a new acceptance threshold.
- As implemented, it is neither complete (gap 2) nor matched to the measured sessions (gap 1). The "≥5 repetitions" property only bounds clock noise on one fixed payload, not the sampling variability of byte volume.

**Minimal bench-only correction (no shared change):**
- (a) Make interpretation campaign-bound. Either:
  - run `decode_phase` offline (no CLI, under the same bounded guard) over the campaign's own retained measured sessions and apply the 1 ms screen to the per-role medians across those sessions; or
  - require each campaign phase's per-role median `first_offset − begin` to lie within a predeclared ratio of the diagnosed bytes.
- (b) Have `Readiness` record the count of post-dispatch evaluations, and the raw offset of each, up to the first match. It is bench-owned and already sees every evaluation. Then either:
  - replay `useful`/`frame` at those same boundaries in the timed region; or
  - make phases non-interpretable when the role evaluation counts differ materially.

Until then, record that latency triggers are descriptive, which is also the source default when `--decoder-report` is omitted (`:1802-1803`). No subtraction either way.

### R5-L1 — Low, campaign design risk: the hard-coded seven-chunk long-idle design may not pass its own feasibility formula [S], actual cost [U]

**The design:**
- `POOL_COUNTS` (`phase7_bench.py:33`) fixes every chunk at 5 pairs, which is 10 CLI roles.
- With `--floor-sessions 3`, each chunk also runs 12 controls.
- `campaign_feasibility` (`:2045-2079`) reserves, per role, the literal idle + 1.5 × the observed non-idle cost + 8 s. It also reserves 30 s per control (minimum 23 s) and 30 s + 5 s per pair. `estimated_fits` requires the total ≤ the budget ≤ 3600.

**The arithmetic,** at the 300 s long idle and zero non-idle cost:

| Component | Seconds |
| --- | --- |
| 10 roles × (300 + 8) | 3,080 |
| 12 controls × 30 | 360 |
| Serialization (30 + 5 × 5) | 55 |
| **Total** | **3,495** |

That leaves about 105 s of slack. The observed per-role non-idle scheduling cost (max of the two roles, including fixture start, about a dozen checkpoints with 80 ms holds, quit drain, archive, config, hashing, `observation_issues` and dry extraction) must therefore be ≤ about 7 s, or ≤ about 12.6 s at the 23 s floor reserve. Otherwise every long chunk is "does not fit".

**Why it matters:** the docs correctly forbid booking without a fit (`measurement.md:300-302`), so this fails closed. But redesigning the chunking or control count requires a source change, which invalidates every source, case and pool binding. That should be decided **before** any long-idle preflight is used for a pool spec, not after.

**Correction:** compute this from the 4 long-idle preflight roles before pool-plan. If it does not fit, predeclare a different chunking (for example 4-pair chunks), fewer controls per chunk, or a recorded coordinator disposition. Do not adjust reserves after seeing campaign data.

### R5-L2 — Low: the pool reducer does not bind per-chunk quiet authorization [S]

`pool_reports` (`:1906-2036`) verifies identities, schedules, raw evidence, controls and end hashes. It never inspects the retained `report['quiet_window']` snapshot or `definition.options.campaign_id`, and it does not require the seven chunk campaign IDs to be distinct or to match their approvals' companion `campaign_id`.

The docs require "distinct output/campaign IDs and explicit renewals" (`measurement.md:265`, `:302`). A complete chunk can only come from `run_campaign` under a QuietWindow, so this is an offline audit gap, not an admission bypass.

**Correction:** require a present approval snapshot whose `campaign_id` equals the chunk's option and its companion's `campaign_id`, and require seven distinct IDs.

### R5-L3 — Low: partial-frame qualification was closed only for resize [S][K], incidence [U]

`table-view` (`:288-289`) qualifies on `Table View` + `bench-series-0000` with fresh output. Both tokens sit in the top portion of the new table: the title and the first row.
- A writer that flushes in several chunks (for example a small line-buffered stdout [K]) can stamp `first_expected` on a top-portion partial paint before the body or footer arrives.
- A writer that flushes the whole frame at once cannot.

This is the same role-dependent definition issue R4-M1 fixed for resize via the footer at H−6. `ready_stable` is unaffected.

**Correction:** add a footer or bottom-region token, which is the `chart-view` equivalent. Otherwise, document table/chart `first_expected` as "first target tokens visible", tied to the R5-M1 evaluation-count control.

## R4 findings: independent disposition at the current source

| Finding | Status | Basis / residual |
| --- | --- | --- |
| **R4-M1** stale resize tokens | **Resolved at source** [S] | See note 1 below. |
| **R4-M2** decoder byte volume | **Implemented; soundness gap** | Exact raw offsets and reconstruction are correct [S]; see note 2. The interpretation screen is R5-M1. Actual decode magnitudes are [U]. |
| **R4-M3** A/A | **Resolved** [S] | See note 3. |
| **R4-M4** pooling and seeds | **Resolved at source** [S] | See note 4. Residuals: R5-L1 and R5-L2. |
| **R4-L1** double SIGWINCH | **Resolved** [S] + [O] | See note 5. |
| **R4-L2** companion binding | **Resolved** [S] | Requires `issued − 60 ≤ at_unix ≤ now` and a matching `campaign_id` (`:607`), checked on every reload. |
| **R4-L3** preflight lock | **Resolved** [S] | `FunctionalOwnership` takes nonblocking `LOCK_EX` before `_functional_preflight`, held through the report write (`:547-574`). There is no wait and no approval is created. Note: a full 52-role preflight can hold the common lock for most of an hour, so it is a scheduling item for phase 6. |
| **R4-L4** attribution latency | **Resolved** [S] | See note 6. |
| **R4-L5** wall feasibility | **Resolved at source** [S] | Per-role scheduling, capture and post-processing walls, including interrupted roles (`:1531-1568`), plus the declared reserve formula. Actual costs are [U]; see R5-L1. |
| **R4-L6** signal and seam docs | **Resolved** [S] | `Phases.change` rejects `signal` (`:167`). The contract's seam block lists `spawn_helper_build` and `resize_signal_policy`, and states the descendant semantics (`contracts:184-199`). |
| **R3-M2** coverage residual | **Resolved in bounded form** [S] | See note 7. |

**Notes:**

1. **R4-M1.**
   - `sql_resize_geometry` (`:485-497`) requires:
     - the footer token ending at W−3 on row H−6, with `│` at columns 0 and W−1;
     - the `bench-row-000000` marker on row 16 with new-width borders;
     - in addition to `resize_rerender`, fresh bytes and the right corner.
   - pyte grow leaves row H−6 blank. pyte shrink deletes top lines, so the stale footer lands on row H−6 but ends at old W−3, which is truncated away [K]. Neither passes.
   - The stable frame is rechecked (`:856-857`).
   - Residuals:
     - row 16 and H−6 are source-derived and only the actual preflight can confirm them [U];
     - per-evaluation `frame()` cost is moved into R5-M1.
2. **R4-M2.** `raw_observed_offset` (`:501-510`) = consumed tokens + `pending.encode(surrogateescape)` + decoder buffer, which is exact for `Terminal.feed` [S]. The evaluation, first-match and checkpoint offsets are bound (`:513-530`), and replay reconstructs the prefix and previous resizes at exact offsets with untimed full-frame equality (`:1687-1760`).
3. **R4-M3.** `comparison_artifacts` (`:533-544`) validates both roles with the truthful language rule and requires identical canonical identity plus provenance. Copies and different metadata files are rejected. Other properties:
   - distinct `-aa-*` labels;
   - a matching A/A preflight class and provenance (`:1579-1584`);
   - `budget_evidence=False`;
   - latency, CPU/RSS and idle eligibility all False (`:1032`, `:1086`, `:1857`).
4. **R4-M4.**
   - Seeds are `base + 1009·i` and the analysis seed is `base + 100000` (`:1871-1891`).
   - The spec binds the case, source, preflight, artifacts, helper, controls and policy (`:1894-1903`).
   - The reducer requires seven complete chunks, the exact schedule and order, 5 warmups + 30 measured pairs, and no reuse of sessions or lifecycle starts. It reruns contracts, the comparator and extraction from raw evidence, and rehashes at the end (`:1906-2036`).
   - Distinct per-variant bases are a command-matrix property [O].
5. **R4-L1.**
   - In `_execute`, ordered integer, non-bool, 1..65535 geometry and no-op rejection run before `dependencies()`, the helper and the PTY (`phase7.py:835-857`).
   - The order is then: emulator resize, dispatch stamp, `TIOCSWINSZ` only (`:1043-1052`). The kernel signals the foreground group only on a real change [K].
   - The legacy default is unchanged.
   - Every bench and profile adapter passes `ioctl-only-changed` (`bench:709`, `profile:183-186`), and `resize_policy_issues` binds policy, source, order and geometry everywhere (`:806-830`).
   - The supplied native control shows a single delivery per change versus duplicates under legacy [O].
6. **R4-L4.**
   - Extraction defaults all latency to ineligible (`:1027`, `:1085`).
   - Only a primary Go–Rust decoder interpretation can set it (`:1857`).
   - CPU/RSS needs `descendant_comparison_eligible is True` and an empty observed set (`:1086`), and the primary capture must claim False (`:1156-1157`).
7. **R3-M2.**
   - The dense 1000×256 table selects 31 forward, the last page, 31 reverse and home.
   - The footer arithmetic is consistent with evertras bubble-table `end` = last page (`pkg/model/tablekeymap.go:76-79`). With 16-row pages, no target footer is a substring of the preceding stale footer at 120×40.
   - One range request per session is enforced (`:909-910`).
   - Unchanged:
     - stage decomposition (event receipt, reducer, render, write) is unimplemented and unknown, and is explicitly disclaimed;
     - rapid input during slow effects is covered only by bursts across delayed prefetches.

## Inherited findings (rechecked against the current lines; no regression found)

| Finding | Current source |
| --- | --- |
| R1-H1 | Native `posix_spawn` supervisor (`phase7.py:505-507`). |
| R1-H2 | Stat-first Z/X and missing RSS/HWM rejection with a start-tick recheck (`:375-400`), plus `same_process` bracketing (`:928-936`). |
| R1-H3 | Screen-only runtime waits and post-hoc causal effects (`bench:882-892`). |
| First-ever stamp | The first-ever stamp is never reset within an action (`phase7.py:1016`, reset only after dispatch at `:1064`). |
| Helper H1/M1–M3 | WNOWAIT reservation, pidfd group signalling with ESRCH handled, bounded cleanup and `exec_error` (`:483-682`). |
| No-build resolver | `build=False/probe=False` on capture, prepared-snapshot requirements and cache symlink rejection (`:407-476`). |
| Mandatory group cleanup | Independent of `record_descendants` (`:1084`). |
| R2-H1/M1/M2 | `check_command`/`check_binding`/`check_artifact`/`final_identities` in bench, preflight and profile, and retained-preflight revalidation (`bench:1575-1661`). |
| R2-M3–M5 | Profiler end identities, reserved-leader analysis cleanup and the bounded write parser (`profile:265-406`). |
| R3-H1 | Dispatch origin after evaluation and frame work, with exactly one dispatch type (`bench:874-878`). |
| R3-L1–L5 | Dry extraction; setup anchor; `QuietError` wrapping; interrupted strace accounting; readelf binding with end rehash. |

**Accepted stated limits, not re-litigated:**
- ordinary check-to-use TOCTOU;
- usage loss on fatal helper death;
- adopter reap;
- escaped `setsid`/`setpgid` descendants;
- thread lifetime;
- syscalls that are not hard realtime;
- about 8–10 s of unwind overrun.

## Readiness and exact must-run conditions (none performed here)

**Functional preflight admission (no timing):** ready at source when all of the following hold.
1. **Settled artifacts.**
   - The final retained real-deadline checks have finished and the release5 normal artifact is final.
   - No production or harness edit follows. `source_snapshot` binds `src`, harness and C, so any edit invalidates the preflight.
2. **Independent parent verification** of all seven owned and five consumed hashes, the ELF and the sidecar on that revision.
3. **Helper preparation.** The helper is prepared and `cached_spawn(probe=True)` succeeds before the run.
4. **Both 26-variant / 52-role preflights** run (primary and resource-attribution, 104 roles total):
   - into new directories;
   - with the common lock free (they fail closed otherwise);
   - with a pre-chosen budget ≤ 3600.
5. **Each policy passes in full:**
   - 52/52 valid roles, 26/26 valid pairs and valid dry extraction;
   - every failure retained;
   - no re-scoping to a subset.

**First bounded truthful A/A and control tooling:**
1. A matching `--comparison aa-go` or `aa-rust` preflight using the same canonical artifact and provenance.
2. `feasibility` fits on that preflight.
3. A fresh parent approval whose phase-6 companion carries the same `campaign_id` and `at_unix ≥ issued − 60`.
4. The run's results are diagnostic only. `budget_evidence` stays false.

**Go–Rust timed campaigns:**
- **Resource-attribution CPU/RSS:**
  - run with a matching attribution preflight, `--sessions 30 --warmups 5`, and A/A done;
  - feasibility fits;
  - fresh campaign-bound agreements are in place;
  - fresh-seed confirmations are retained.
- **Latency triggers:** fix R5-M1 (campaign-bound decoder interpretation plus an evaluation-count control), or record that all latency is descriptive.
- **Long-idle pools:** first resolve R5-L1 against the actual long-idle preflight role costs. R5-L2 is recommended before pooling.

**Profiler interoperability:**
1. Run the driver's own `prepare-symbols` against `profiling/release5/artifacts.json`. The parent's `release5-profiling-preparation.json` is separate evidence and is not the driver report that `verify_symbol_evidence` requires.
2. Run a bounded real smoke test covering:
   - perf, strace and heaptrack FD3 / `profiler.stderr` separation;
   - exact CLI stderr under heaptrack preload;
   - heaptrack raw naming;
   - strace raw parsing including `= ?`;
   - perf report and `$HOME/.debug` side effects;
   - actual symbol resolution.
3. All four functional controls are valid.

**Never:**
- subtract floors, holds or decoder costs;
- treat attribution or A/A latency as trigger evidence;
- infer cross-platform, live HTTP/2 SQL, packaging, installer or rollback readiness from Linux loopback helper evidence.

Files written: this document and `/home/ajs/.cache/pb-phase7/review-measurement-opus-r5/evidence.json`. No source, test, harness or other file was edited. No thread, lock, process or message outside the parent was created.
