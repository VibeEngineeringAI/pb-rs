# Phase 7 under-30-minute execution plan — Opus design R1

Status: **design proposal for root.** It does not accept any protocol, matrix, admission or performance result. The user's current rule is that every performance run, including preflight, A/A, campaign, confirmation, diagnostic, interpret and reducer runs, must finish **strictly under 1,800 s**. This document makes that rule executable with the smallest source change and separates out what can run now. This lane edited no production, harness, contract or protocol file. It is not a review continuation.

Working evidence is in `/home/ajs/.cache/pb-phase7/under30-execution-plan-opus-r1/`:

- `arithmetic.py`: pure `Fraction` arithmetic. It reads JSON/text only and launches nothing.
- `arithmetic.json`: its output, with the SHA-256 of every input it read.

## Inputs and execution disclosure

Source read at worktree HEAD `4a812d5`; the post-idle freeze hashes are re-verified:

| File | SHA-256 |
| --- | --- |
| `harness/phase7_bench.py` | `bf630d9a…eef013c2` |
| `harness/phase7.py` | `dc7574f0…6a44fa` |
| `harness/test_phase7_bench.py` | `9b3b0a94…6c300c` |
| `harness/contracts/phase7.md` | `f50160ba…ffa9b` |
| `docs/phase7-measurement.md` | `a7bf7493…66ee` |
| `harness/phase7_profile.py`, `docs/phase7-profiling.md` | unchanged |

`docs/phase7-performance-decisions.md` changed during this lane, from `335ec114…` when I read it to `cf0023f6…` (the R4 owner's P1 disposition). That change does not affect this plan. That document says the root-owned bounded design supersedes the 3,600 s schedules; this is that design input. I do not edit it.

Also read:

- R7 matrix `5b99e77f…`: argv, seeds and counts.
- Freeze `3bf71758…`.
- The relevant parts of the R7, R8, R10 and R11 measurement reviews and of PD-R3.

**Execution status:** the single bounded shell attempt worked; there was no EDQUOT. I ran:

- read-only `cat`, `grep`, `sed` and `sha256sum`;
- Python that reads JSON;
- the arithmetic script, twice: once, then again after a docstring line-citation fix.

I ran no CLI mode, native helper, probe, compiler, build, profiler, common lock or timing. A first-command temporary file, `target/tmp-under30/out.txt` (gitignored), was deleted. No calibration value below is observed; every value is a formula, a source-derived bound or marked UNKNOWN.

## 1. How a run is bounded today (source facts)

**Run and campaign-decode (`QuietWindow`).**

- Approval validity requires `0 < expires−issued ≤ 3600`, `issued ≤ now < expires` (`phase7_bench.py:655`).
- The guard deadline is lock time plus `--budget-seconds` (`Options` allows ≤ 86,400; `:139`).
- The approval is reloaded at least every 250 ms, and expiry kills the active child (`:679-692`).
- Admission requires `allocation ≤ remaining guard budget` AND `allocation ≤ expires − now` (`approval_allocation`, `:2510-2523`).
- **Before the guard,** `run_campaign` performs:
  - `plan`;
  - artifact identity;
  - `validate_preflight` of the selected cases;
  - for pool chunks, `validate_pool_spec` → `require_pool_feasibility`, which revalidates the FULL 13-case same-profile preflight (`:1466-1500`, `:2061-2074`, `:2146-2155`).
- **After the guard,** on failure paths, it performs `final_identities`, `host_after` and the report write (`:1587`).
- Neither the pre-guard nor the post-guard part is bounded by `--budget-seconds`.

**Preflight, pool, interpret and diagnostic-plan (`FunctionalGuard` and `FunctionalOwnership`).** Their budget starts after setup:

- preflight runs `cached_spawn(probe=True)` and hashes the binaries first (`:1616-1631`);
- `pool` and `interpret` reject budgets above 3,600 (`:2169`, `:2703`).

`feasibility` and `pool-plan` have no time guard at all (`:2830-2905`).

**Consequence:** `--budget-seconds` alone cannot enforce "<1,800 s from launch to exit". Three existing mechanisms do: the approval expiry, the admission check against `expires − now`, and one external ceiling (§3).

## 2. Source-derived arithmetic (exact; `arithmetic.json`)

The capture allocation comes from `campaign_feasibility` (`:2784-2820`):

```
12·30 (controls) + 60 (launch) + roles·(idle + 1.5·y + 8) + 30 + 5·pairs
```

Here `y = max(role scheduling_wall) − idle`, the observed non-idle whole-role cost in seconds. Write `E` for the admissible seconds left at the in-guard admission check (`expires − now`). It equals the approval lifetime minus the pre-guard and in-guard-feasibility time `T`.

| Design | Allocation (s) | Max `y` at E=1680 | E=1740 | E=1799 |
| --- | --- | --- | --- | --- |
| Long chunk, 4 pairs (current worst) | 2934 + 12y | infeasible | infeasible | infeasible |
| Long chunk, 3 pairs | 2313 + 9y | infeasible | infeasible | infeasible |
| Long chunk, 2 pairs | 1692 + 6y | infeasible | 8 | 17.8 |
| **Long chunk, 1 pair** | **1071 + 3y** | **203** | 223 | 242.7 |
| Non-long 5+30, idle 2 (SQL/dense/error) | 1325 + 105y | 3.381 | 3.952 | 4.514 |
| Non-long 5+30, no idle (editor and the four A/A sentinels) | 1185 + 105y | 4.714 | 5.286 | 5.848 |
| campaign-decode 5+30 (58 remaining roles) | 174·W·ratio + 704 | W·ratio ≤ 5.61 | 5.95 | 6.29 |

Each entry assumes `T = 0`. Subtract `T/slope` from it for real use.

**Source-derived infeasibility, independent of calibration.** Every accepted checkpoint holds a full stable frame for at least 80 ms:

- each checkpoint carries `stable_ms: 80` (`:164`);
- the shared execute enforces the hold (`phase7.py:1020`);
- validation requires `ready−stable ≥ 80 ms` (`:924`);
- actions run sequentially (`phase7.py:844`).

So `y ≥ 0.08 × measured phases`:

- **`sql-scroll-20000-sustained`** has 83 phases, so `y ≥ 6.64 s` and the allocation is ≥ **2,022.2 s**.
- **`promql-dense-1000-sustained`** has 67 phases, so `y ≥ 5.36 s` and the allocation is ≥ **1,887.8 s**.

Both exceed 1,800 for every admissible lifetime. All 8 sustained single campaigns (2 stems × 2 themes × 2 policies) are therefore **inadmissible under the user rule with preserved reserves**. No calibration can change this.

The functional preflight lower bound counts only idle time plus 80 ms per latency phase:

- a single-theme 13-case preflight needs ≥ 676.96 s;
- the current two-theme 26-case preflight needs ≥ 1,353.92 s, before any startup, fixture or postprocessing cost.

## 3. Executable per-run wall rule (no source change)

The coordinator owns this and records it per run in the registry:

1. **Approval lifetime 1,680 s.** Write `issued_unix = t0` immediately before launch and `expires_unix = t0 + 1680`. The companion `at_unix` must be in `[t0−60, launch]`.
   - Pass `--budget-seconds 1680` to run and campaign-decode. The pool spec binds `budget_seconds` in its common options, so the pool-plan and all chunks must use the same 1680.
   - Pre-guard time is automatically charged against admission through `expires − now`. A slow pre-guard therefore fails closed at admission, as a retained failure.
2. **FunctionalGuard modes** (preflight, pool, interpret, diagnostic-plan): pass `--budget-seconds 1620`. This leaves about 120 s for unguarded setup and finalization.
3. **External ceiling:** measured from launch, the coordinator sends SIGTERM at 1,740 s and SIGKILL at 1,770 s.
   - This also covers the unguarded `feasibility` and `pool-plan`.
   - If it fires, the run has FAILED. Retain its partial evidence and do not retry under the same design.
   - Before the lock is used again, verify that no `pb-go`, `pb-normal` or `phase7_spawn` processes from that run's partial PIDs remain. The helper's behaviour when its Python parent is killed is UNKNOWN.
   - Exact `timeout`/process-group semantics must be checked by root on this host before use. I did not check them.
4. A registry rule rejects any argv with a budget > 1680 (QuietWindow modes) or > 1620 (FunctionalGuard modes), and any approval lifetime > 1680.

Optional hardening, not needed to execute: lower `MAX_APPROVAL_SECONDS` to 1680 in source. It would still not cover pre-guard or post-guard time, and it touches 11 test literals, so I do not recommend it for the minimal diff.

## 4. The smallest required source change (long-idle pools only)

At the 1,680 s lifetime, any long chunk with 2 or more pairs is arithmetically inadmissible. Even at 1,799 s, 2 pairs allows only `6y + T ≤ 107`. That is not robust, because `T` per chunk includes a full 26-role preflight revalidation. **One pair per chunk is forced.** The 5+30 totals and warmups-first order are kept:

```python
# phase7_bench.py:35
POOL_COUNTS = ((1, 0),) * 5 + ((0, 1),) * 30   # 35 chunks: 5 warmup-only, 30 single measured pairs
```

Everything else already derives from `len(POOL_COUNTS)` or `sum(counts)`:

- `Options.validate` (`:131`);
- the caps, which become `[14]*35` (`:2077-2079`);
- `require_pool_feasibility`, which now uses 1 pair and cap 14 (`:2061-2074`);
- the spec chunks and seeds `base+1009·(i−1)`, plus the analysis seed `base+100000` (`:2091-2126`);
- the reducer chunk-count and 5/30 totals checks (`:2176`, `:2293`).

Only the four messages that say "nine" need neutral wording (`:2072`, `:2121`, `:2126`, `:2177`). There is no observer, metric, control, reserve, seed-formula, eligibility or end-binding change.

**Tests to update** (bench owner) in `test_phase7_bench.py`:

- `:1310-1330`: the expected tuple, and replace the `seven` mutation with an obsolete-`nine` rejection.
- `:1445-1481`: caps `[14]*35`, `len(chunks)==35`, final cap 14.
- `:1563`, `:1579`: 35 distinct seeds and chunks.
- `:1605-1650` (feasibility boundary): budget 1680; role `503.0 s` fits at exactly 1680, and `503.1 s` gives 1680.3 and rejects. Add an assertion that the obsolete 4-pair allocation at `y=0` (2934) exceeds 1799.

Pool fixtures that build chunk reports from `POOL_COUNTS` will now build 35. Other `3600` literals test the unchanged ≤3600 validators and can stay.

**Documents to update:**

- contract `phase7.md` `:298`, `:305`, `:307`, `:317`;
- a new current section in `phase7-measurement.md`;
- the nine-chunk wording in the R4 decisions doc at `:15`, `:17`, `:75`, `:134`. That is the R4 owner's edit, not mine.

**Count, seed and binding consequences** (prospective, from `arithmetic.json`):

| Quantity | Before | After |
| --- | --- | --- |
| CLI roles | 1,820/policy | unchanged |
| Pairs | 910 | unchanged |
| Measured pairs per variant | 5+30 | unchanged |
| Controls per run | 12 | unchanged; 3 floor sessions and all reserves unchanged |
| Runs | 84 | 188 (94/policy) |
| Capture and control children | 4,648 | 5,896 |
| Floor controls | 1,008 | 2,256 |
| Functional preflight roles | 104 | unchanged (four single-theme preflights, §5) |

- **Seeds:** 192 campaign seeds (48 non-pool + 140 chunk + 4 A/A) are distinct. The seed+101 control offsets do not collide. The +10,000,000 confirmation namespace is distinct and disjoint. The known cross-purpose analysis-seed reuse (162,000 and 175,000) is unchanged and must stay disclosed. The highest chunk seed is base + 34,306.
- **Long-pool cost:** total idle for the long pools is still at least 84,000 s (23.3 h); idle is intrinsic.
- **Chunk diagnostics:** there are now 20 warmup-only slots (NOT APPLICABLE) and 120 single-pair slots. These are optional descriptive diagnostics; I recommend root declare them **unscheduled**. Pooled latency stays descriptive, so nothing eligible is lost.
- **Confirmations:** a long-idle confirmation becomes a whole new 35-chunk pool.
- **Reducer:** it now revalidates 70 pooled roles (unchanged), 420 control raws instead of 108, and 35 source snapshots instead of 9. Its wall time is UNKNOWN (§7).

## 5. Required declaration and registry changes (no source change)

- **Four single-theme full-profile preflights** replace the two two-theme ones: primary/attribution × dark/light, each with 13 cases, 26 roles, `--include-long-idle --long-idle-seconds 300` and `--max-sessions 26`.
  - Their lower bound is 677 s, and they have about 1,000 s for the remaining per-role cost (≈ 38 s/role average). That fit is UNKNOWN.
  - Source supports this: `validate_preflight` only requires the selected cases to be present (`:1697-1761`), and pool feasibility requires the full profile of the chunk's own single theme (`:2063-2066`).
  - A single 13-case report per theme is mandatory for `pool-plan`. If it cannot finish under the ceiling, `pool-plan` is impossible without a further source change. That is a stop condition, not a fallback designed here.
- All argv carry the §3 budgets and approval lifetimes. The 35 ordered `--chunk-report PATH` arguments per reducer use the exact split form.
- IDs must be distinct and ≤ 128 characters; for example, the `-chunk35` suffix fits.
- **Regenerate everything after root integrates Phase 6 (`ea70f7a`) and the combined candidate is bound.** This covers source snapshots, all 52 CLI-typed case identities and every ledger, spec and plan reference. Any preflight that runs before the final source freeze cannot bind later runs, because `validate_preflight` compares `source_snapshot()` (`:1714`).

## 6. Staged execution

### Stage A — can run NOW: calibration only, no source edit, nothing bindable

Purpose: get the real values of `y` before more source or review work. In particular, establish whether any non-long 5+30 campaign or the A/A sentinel is admissible at all. Each step needs:

- root scheduling in its own agreed window, with the lock free and the Phase 6 coordination;
- the helper already prepared (`cached_spawn` uses `build=False`, so a missing helper fails closed);
- the §3 ceiling.

Use the existing environment line (`PATH`, `TMPDIR`, `GOTMPDIR`, `CLAUDE_CODE_TMPDIR`) from `phase7-measurement.md:12`.

**A1 — primary, dark, 12 non-long cases (24 roles).** Lower bound about 76 s; each role is capped by its timeout (≤ 62 s).

```bash
/home/ajs/.cache/pb-phase7/harness-venv/bin/python harness/phase7_bench.py preflight --reference /home/ajs/.cache/pb-phase7/baselines/pb-go --candidate /home/ajs/.cache/pb-phase7/candidates/release5/pb-normal --size 120x40 --session-timeout 60 --idle-seconds 2 --metric-points 256 --network-delay-ms 150 --theme dark --observer-policy primary --budget-seconds 1620 --max-sessions 24 --out /home/ajs/.cache/pb-phase7/under30-calibration-a1-primary-dark-NEW
```

**A1b (optional) — attribution, dark, full 13-case profile.** Add `--observer-policy resource-attribution --include-long-idle --long-idle-seconds 300 --max-sessions 26`. Lower bound 677 s. This directly tests §5's single-theme preflight fit and the long chunk's `y`.

**A2 — offline source `feasibility`, once per A1 case.** Use the same profile flags plus:

```
--preflight-report <A1>/report.json --case <name> --warmups 5 --sessions 30 --floor-sessions 3 --max-sessions 82 --budget-seconds 1680 --seed 50000
```

The seed does not affect the allocation. The output's `estimated_seconds` and `estimated_fits` are the authoritative per-case admission at 1680 for `T = 0`. The coordinator records the wall time of one invocation as a lower proxy for `T`.

**A3 (optional, only if A2 shows the editor fits): operational pilot with A/A Go/Go.** Run it in this order:

1. the R7 `aa-go` preflight argv with `--budget-seconds 1620`;
2. the R7 `aa-go` run argv with `--budget-seconds 1680`, a fresh 1,680 s approval and a companion.

This exercises the approval expiry, admission, controls, finalization and the ceiling with **no candidate timing**. It measures the actual `T`, the run wall versus the allocation, and post-guard time. It is descriptive only, `budget_evidence=False`, and **it is not the declared sentinel**, which must run again under the final declaration.

Interpretation limits:

- A1, A1b and A3 use the pre-integration release5 artifact and source. Their costs are planning inputs, never admission or performance evidence.
- Read only `max(scheduling_wall)` per case. Do not compare Go with Rust.
- Stop rule: a failed or interrupted run is retained, there is no re-run of the same design, and root decides.

### Stage B — after integration, the §4 diff and the §5 regeneration

1. The bench owner applies the §4 diff and runs the unit suite with native, CLI and lock boundaries intercepted, as before. Then there is **one** bounded independent review of that diff only. Findings are fixed or dispositioned once, with no open-ended cycle. Root freezes the sources and the regenerated registry.
2. Four single-theme preflights and four A/A preflights.
3. Offline `feasibility` for every non-pool campaign and all four pool-plans at 1680. Root records the dispositions of every non-admissible design (D1–D3) **before any timed capture**.
4. Timed work:
   - A/A sentinels first, as the gate;
   - then admitted non-long campaigns;
   - then pool chunks 1→35 in order;
   - then reducers at `--budget-seconds 1620`;
   - then diagnostic-plan, campaign-decode and interpret only for admitted primary non-long campaigns, with the pilot fit decided per campaign.

## 7. Root decision points and remaining unknowns

- **D2, decidable now, before any data:** the sustained campaigns are inadmissible (§2). Prospectively record their latency and CPU/RSS rows as **unassessed: inadmissible under the per-run limit with preserved reserves**:
  - 2 × (83 + 67) = **300 of the 394 latency decision rows**;
  - 8 attribution CPU/RSS rows.
  - These rows are not satisfied, not inconclusive-by-data and not dropped, and metric-budget closure stays blocked.
  - Alternatives are outside this minimal plan: a separately reviewed non-long attribution pool for CPU/RSS only (pooled latency can never be decision evidence under the protocol), or a user decision to relax the limit for those runs. Only the user can make that second choice.
- **D1, after A2 and B3:** any other non-long case whose allocation exceeds `1680 − T` gets the same unassessed-by-infeasibility disposition.
  - The thresholds are narrow: `y ≤ 3.381 − T/105` with idle 2, and `y ≤ 4.714 − T/105` for the editor. Their lower bounds are 0.08–0.72 s plus startup, fixture and postprocessing time. The fit is UNKNOWN.
- **D3, A/A gate:** if either the primary or the attribution editor sentinel is inadmissible, the protocol withholds ALL decisions for that observer profile. This is the highest-leverage unknown; A2 answers it first.
- **D4, latency eligibility:** campaign-decode needs `W·ratio ≤ (1680 − T_d − 704)/174 ≈ 5.6 s`. Here `T_d` includes the in-guard revalidation of all 70 original roles, which is UNKNOWN. If the pilot does not fit, the result is diagnostic-incomplete, so latency is ineligible and the rows are inconclusive. Partitioning diagnostics would need a new reviewed design.

**UNKNOWN (no observation exists):**

- `y` per case, policy and theme for the combined candidate;
- `T` per run type: the chunk pre-guard full-profile revalidation is incurred 35× per pool;
- post-guard finalization time and external-kill orphan behaviour;
- helper preparation state;
- 35-chunk reducer wall;
- interpret wall;
- diagnostic `W` and the work ratio;
- profiler fit: `phase7_profile.py` accepts budget ≤ 3600 (`:45`) and uses the same `QuietWindow`, so the §3 rule applies, but its allocation was not analysed here;
- combined-candidate identity after integration.

Every number in §2 and §4 is either a formula transcribed from the bound source or a bound derived from it. None of them is a timing calibration.

**Ownership and non-overlap:**

- This lane owns only this document and its cache directory.
- The §4 diff belongs to the bench/harness owner.
- The decisions-document wording belongs to the R4 owner.
- Approvals, registry, integration, the ceiling mechanism and the D1–D4 dispositions belong to root.
- The Sol lanes' R4 low-decision fixes and accounting research are untouched.
