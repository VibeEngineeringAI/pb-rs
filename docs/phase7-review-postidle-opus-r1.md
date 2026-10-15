# Phase 7 post-idle bracket proposal: independent Opus review, round 1

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (base `4a812d5`, Go pin `07af390`, no commit). This is a fresh review of the OFF-TREE PD2-M1 remedy at `/home/ajs/.cache/pb-phase7/postidle-bracket-proposal/`.

**Subject (all digests supplied [O]; I computed no hash):**
- `proposal.md` `1fb57678…26a1`
- `proposal.patch` `d197c703…b47b`
- `manifest.json` `46530942…fef`
- candidate bench `bf630d9a…13c2`, live bench `bf4adf5f…3f6b`, shared helper `dc7574f0…44fa` (unchanged)

**What this review is not.** It accepts nothing: no protocol, measurement, phase, source freeze or quiet window. It authorizes no application, preflight or campaign. I applied no patch and edited no proposal, author, source, test or contract file. The R3 L1–L5 doc work is outside my scope.

## Verdict

**The exact patch is fit at source as a prospective bench remedy for PD2-M1's bracket *opportunity*. I have no blocking objection to the four-file change.**

- **The builder is correct.** The hold applies to attribution long-idle only, sits between E and quit, has `min_elapsed 170` plus the existing stable 80, and is a named anchor with `trigger None`. The timeout increment is exact.
- **The retained validation is correct.**
- **The clock minimum H.ready − E.ready ≥ 250 ms holds generally, not only on the synthetic timeline** (I-1).
- **Extraction, decoder, history, session-work and feasibility code all key on `kind`.** None of them reads the anchor's `duration_ms` or `start_checkpoint`, so the hold cannot leak into idle S..E, latency or the idle allocation.

**One medium finding, PI1-M1: provenance-binding completeness, doc and handoff only.**
- The existing R2 coverage ledger binds **all** case references to integer-literal hashes.
- But the proposal (`proposal.md:60`) and the protocol handoff (`protocol-owner-handoff.md:5`) say to regenerate only the two changed references.
- In fact all 52 case identities must be rebound to the CLI-typed profile.

**Two low findings:**
- **PI1-L1:** a test control gap on the integer-type guard.
- **PI1-L2:** the per-campaign typed hashes are inferred, not computed.

**Actual post-E bracket availability, fit and accounting provenance remain unknown [U] and must be run.** The hold is an opportunity, not a guarantee.

## Execution, evidence and scope

**Execution:**
- **One shell attempt.** It set `TMPDIR`/`GOTMPDIR`/`CLAUDE_CODE_TMPDIR` to `/home/ajs/.cache/pb-phase7/review-postidle-opus-r1/tmp` and `PYTHONDONTWRITEBYTECODE=1`, then ran `mkdir -p`, `ls -la`, `sha256sum` and `wc` over the proposal directory.
- **Result:** the harness reported **EDQUOT** on its task temp directory, with exit code 2 and the output lost. It had no redirected record, so whether `mkdir`, `ls` or `sha256sum` ran is **unknown**.
- **No retry.** I ran no `echo` probe and used no symlink or temp-move workaround. After that I used file tools only.
- **Not run as a result:** no pure/mock test, no mutant, no hash and no arithmetic script.
- **No native work:** no native, helper, CLI, server, probe, compiler, build, readelf, profiler, lock, preflight or campaign.
- **Pause:** none arrived. **Zero processes are active.**

**Labels:**
- [S] source or file as read;
- [K] Python semantics, not exercised;
- [A] my manual arithmetic;
- [O] supplied;
- [U] unknown;
- [I] inherited.

**Supplied, not mine [O]:**
- **Author's checks:**
  - 8 pure tests passed in 7.575 s, with the frozen 11 files unchanged before and after (`actual-checks.json`, `pure-checks.log`);
  - 6 compiled mutants killed;
  - the preserved `.first1` failures.
- **Typed bindings:** `execution-typed-bindings.json`, which records 90 argv/Options records and parser-prefix AST `5eed9f29…8519`.
- **Root's checks:**
  - verification of the 25 artifacts;
  - an isolated `git apply --check` and apply, giving exact postimages with the live tree unchanged (`postidle-isolated-apply-check.json`).
- **Hash identity is not verified by me.** Where I say two hashes are equal or differ, I am comparing supplied hex strings by reading.

**Read in full:**
- From the proposal directory: `proposal.md`, `proposal.patch`, `manifest.json`, `checks.py`, `typed-definitions.py`, `actual-checks.json`, `pure-checks.log` and `protocol-owner-handoff.md`.
- Prior reviews and dispositions: `phase7-review-performance-decisions-opus-r2.md`, `performance-decisions-opus-r2-disposition.json`, `phase7-review-measurement-opus-r10.md` and `-r11.md`.
- Root evidence: `postidle-proposal-parent-check.json` and `postidle-isolated-apply-check.json`.

**Read in part [S]:**
- Live `phase7_bench.py`:
  - 1–300: constants, `Options`, `Phases`, `sql_case`;
  - 380–840: `campaign_cases` tail, `causal_requests`, `Readiness`, `bind_decoder_offsets`, `Capture`;
  - 840–1150: `observation_issues`, `resource_interval`, `extract_metrics`;
  - 1740–1960: `retained_sample`, `SessionReplay`, `decode_phase`;
  - 2310–2550: `session_work`, `campaign_sessions`, `diagnostic_plan`, `pilot_allocation`;
  - 2766–2890: `campaign_feasibility`, the argparse prefix of `main`.
- Shared `phase7.py` 770–1147: `useful` and the whole `_execute` loop.
- Candidate bench: 249–266 and 944–963. These match the patch.
- `test_phase7_bench.py`: 1–80 (imports and helpers) and 2440–2484.
- Proposal data:
  - `execution-typed-bindings.json`: 1–140 and 16000–16265;
  - `definition-hashes-and-matrix-impact.json`: 1–20.
- R2 `coverage-design.json`: 1–70 and 5372–5401.
- The R6 brief: 1–18.

**Not read, so no coverage is claimed:**
- `campaign_diagnostics` and `interpret_campaign` anchor handling. I rely on the fact that the same case already contains the `idle-start` anchor on the identical path [I].
- `editor_case`, `error_case` and `dense_case` bodies.
- `p7.validate` and `p7.compare`.
- `proc_sample` internals. The post-read stamp is inherited from R2 and the brief.
- The rest of the test file.
- `finish.py` and `prepare.py`.
- `docs-only.patch` and `bench-only.patch` as separate files. Their content is inside the combined patch.
- The rest of the bindings JSON and the matrix.

## Delta verification

| Item | Assessment |
| --- | --- |
| Scope gate | `idle is not None and observer_policy == 'resource-attribution'` (cand. `:252`). It is evaluated before `campaign_cases` appends the name suffix (`bench:401-402`), and `Options.observer_policy` is already set. Only attribution policy records descendants (`bench:39`), and `idle_cpu_trigger_eligible` requires `descendant_comparison_eligible` (`bench:1083-1085`, `phase7.py:1121`). **Both idle decision rows therefore exist only in the two attribution long cases, so a primary hold would add no decision value.** [S] |
| Definition | `Phases.checkpoint` pops `request_paths` into effects (`bench:163`). The runtime wait therefore has no trace polling, and the causal path is checked after capture against requests at or before `first` (`:940`, `:440-444`). Other properties: `trigger_action_index=None`, kind `anchor`, `stable_ms=80`, `min_elapsed_ms=170` (int). Actions go from 20 to 21 and checkpoints from 11 to 12; the 9 latency phases are unchanged [A]. |
| Timeout | `x + 0` preserves int or float type [K], so the other 50 definitions are byte-identical in **either** typing profile. The attribution long timeout is 60.0 + 300.0 + 0.25 = 360.25, exactly representable [K]. The integer profile also gives 360.25. The loop guard is `now − started > timeout·1e9` (`phase7.py:946`). |
| Retained check | It is name-gated (cand. `:950-958`) and runs after the generic missing-timestamp `continue`. A missing H is caught as `missing phase checkpoint`; a missing E as `missing start`. It uses integer ns arithmetic, `ready − E ≥ 250_000_000`. The runtime guarantees this (I-1), so it cannot false-fail a valid capture. Ordinary anchors are untouched. |
| Kind-keyed consumers | `Readiness` excludes the anchor from history (`bench:454-458`, `:467`). `extract_metrics` skips anchors (`:1075`). `decode_phase` returns `not-applicable` at entry (`:1864-1865`). `session_work` excludes anchors (`:2316`). Feasibility takes idle as the first `kind=='idle'` phase (`:2787`). The idle proc-coverage and resource checks key on `kind=='idle'` (`:944`, `:1001-1010`, `:1079`). Whole-session wait4 counters are copied unchanged (`:1100-1103`), and `cli_lifecycle_elapsed_ms` is reaped minus started (`:1072`). **No subtraction anywhere.** [S] |
| Decoder offsets | Readiness entries are keyed by `action_started`: idle-end is keyed by S, the hold by E, quit by H. `bind_decoder_offsets` matches frames by key and last evaluation (`:563-577`). This creates no new collision; it is the same pattern as `idle-end` followed by quit today. [S] |
| Exit during the hold | `index != len(script)` leads to the `process exited before action` failure (`phase7.py:1100-1101`). That is fail-closed and not repaired. [S] |
| Feasibility arithmetic [A] | 12·30 + 60 + 8·(300 + 1.5x + 8) + 50 = 2934 + 12x. A new 355.5 s gives x = 55.5 and exactly 3600, which fits (`≤`). An old 355.5 + 0.25 gives 3603, which fails. Per role the cost is +0.375 s: 8 roles give +3.0 and 6 roles give +2.25. Reserves are unchanged (`:2779`, `:2794`). |
| Counts [A] | Attribution long capture roles: 2 themes × 35 pairs × 2 roles = 140, at 0.25 s gives 35 s. Functional roles: 4, giving 1 s. The 84 campaigns (48 non-long + 36 chunks), 4648, 104, 3120 and `POOL_COUNTS` are author-computed [O] and consistent with `bench:35`. |
| Session size | One extra frame, offset entry and action. The 256 MiB `retained_sample` cap (`:1758`) is not at risk. `repetition_work` serialization is keyed on `phase_count`, which is unchanged (`:2344`). The actual `session_json_bytes` must be regenerated, as the proposal says. |
| Argparse typing [S][K] | `--session-timeout`, `--idle-seconds` and `--long-idle-seconds` are `type=float` with non-string int defaults (`bench:2844-2847`). argparse converts only string defaults, so supplied values are float and omitted ones int. `parse_size` gives int tuples (`:2807-2812`). `typed-definitions.py` executes only the parser prefix up to the `Try`, with an AST allow-list of `add_argument`, `parse_args`, `ArgumentParser`, `Options`, `tuple` and `Path`. That derivation is exact and **not** generic-float. |
| Tests vs repository | The future tests use `copy`, `inspect`, `mock`, `unittest`, `options`, `synthetic` and `frame`. All exist in the real module (`test:2-18`, `:26-63`). In the repository, `bench` is the real module; off-tree it was live globals plus 5 candidate functions. Only `sql_case` and `observation_issues` differ, so the two are semantically equivalent [S]. In-repository execution is still a must-run. |

## Findings

### PI1-M1 — Medium, future provenance binding: every ledger case reference is integer-literal-typed, not just the two changed ones [S]

**Where (supplied strings, compared by reading):**
- **First example:**
  - R2 `coverage-design.json:12` binds `sql-scroll-500-120x40-dark` (primary) to `e3eaed27…cf9b0`.
  - That equals the **integer-literal** baseline (`definition-hashes-and-matrix-impact.json:6`).
  - The CLI-typed baseline for the same case is `0b189aaf…6869` (`execution-typed-bindings.json:16241`).
- **Second example:**
  - The idle row `coverage-design.json:5377` binds the light attribution long case to `19e92089…0438`.
  - The CLI-typed baseline is `c0452eb0…fc95d` (`proposal.md:58`).
- **Yet the instructions say to update only the changed references:**
  - `proposal.md:60` says "Regenerate the **two** attribution-long definition references … in the ledger/matrix".
  - `protocol-owner-handoff.md:5` says "update only changed case/source references".

**Consequence:**
- If a protocol owner follows the handoff literally, 50 references keep hashes for definitions that no actual CLI argv produces.
- **Best case:** a declaration-versus-preflight `case_hashes` comparison fails closed after the freeze, which means rework.
- **Worst case:** nothing compares them, and the accepted declaration binds non-executed definitions. That is a provenance truthfulness gap.
- It cannot produce a false performance pass.

**Minimal fix (doc and handoff only, before any protocol declaration):**
1. Amend `proposal.md:60` and `protocol-owner-handoff.md:5-7` to say: "rebind **all 52** case identities (every decision and descriptive row, plus any matrix, pool-spec and plan references) to the CLI-typed profile derived from the exact final argv. Exactly two of them also change semantically."
2. Add a declaration check: for each of the 2 preflights and 84 campaigns, the digest of `campaign_cases(<that argv's parsed Options>)` must equal both the ledger reference and the preflight `case_hashes`.

### PI1-L1 — Low, the integer-type guard has no discriminating control [S][K]

**Where:**
- The definition guard is `type(duration_ms) is not int or duration_ms != 250` (cand. `:952`).
- The test negatives are `True`, `249` and `'250'` (`proposal.patch:154`). **All three are rejected by `!= 250` alone** [K]: `True == 1`, and `'250' != 250`.
- The only input that distinguishes the type guard is `250.0`, which is not tested.
- No mutant drops the type check.

**Consequence:** the claim "reject Bool" is true but not evidence for the guard. A refactor removing `type(...) is not int` would pass every test.

**Fix:** add a `('duration_ms', 250.0)` negative control, and optionally a drop-type-guard mutant. This is test-only.

### PI1-L2 — Low, per-campaign typed hashes are inferred, not computed [S]

**Where:**
- The definition-affecting field set comes from `options.<attr>` reads inside five builder functions only (`typed-definitions.py:30-37`). It is not transitive into helpers that receive `options`.
- Campaign records take `proposed_definition` from the **preflight** profile after a `key_values` equality (`:74-80`). They do not compute it from the campaign's own Options.

**Consequence:** a missed field would make the bindings file wrong silently. Runtime still fails closed, because feasibility compares each campaign's own `plan` digest with the preflight `case_hashes` (`bench:2782`). So the risk is binding-file accuracy, not a false pass.

**Fix:** in the future declaration, compute each campaign's single-case digest directly from its parsed Options (this is the same check as PI1-M1 item 2).

### Informational

- **I-1, the general clock minimum [S].**
  - **E's iteration.** The `idle-end` stamp uses the post-select `now` (`phase7.py:992`, `:1027`). The same `now` then becomes `action_started`, and `first_match`, `stable_since` and `last_hash` are cleared (`:1063-1064`).
  - **Before 170 ms.** Any non-qualifying evaluation resets `stable_since` (`:1005-1007`). `_qualify` rejects `now − E < 170·1e6` (`bench:498`).
  - **Stability.** So `stable_since` is at least the first qualifying `now`, which is at least E + 170 ms. Acceptance needs `now − stable_since ≥ 80 ms` (`:1020`). Hence H ≥ E + 250 ms for **every** timeline, not just the synthetic one.
  - **Clock basis.** Both stamps come from the same process's `monotonic_ns`.
- **I-2, sample placement [S], magnitude [U].**
  - The sample in E's own iteration is read before `select` and so is stamped before E.
  - Later samples are stamped after E.
  - The next deadline is less than E + 50 ms, because it derives from the pre-read top-of-iteration `now` (`:927-937`). The first post-E sample therefore falls at about E + 50 ms plus an iteration, and the second about 50 ms later, both well inside 250 ms nominally.
  - The attribution `descendants()` scan lengthens iterations by an amount I did not measure [U].
  - Read failures, a non-same process or a zombie still yield no sample.
- **I-3, upper bracket.** With the hold, the outer-after sample is likely to precede ctrl-c, so its upper endpoint includes hold-period CLI work rather than exit work. That is still data-driven, with no subtraction and no guarantee.
- **I-4, AST gate test.** The synthetic gate uses the idle-end action dict, so `stable_ms` takes the `.get` default of 80, not the hold action's value. The hold's own `stable_ms == 80` is asserted separately in `assert_definition`, so this is adequate.
- **I-5, uncounted kills [K].** Several other mutants would be killed by existing assertions, though they are not among the six listed:
  - hold on primary as well (primary timeout 360, action prefix);
  - hold on short attribution (`short` assertion);
  - `min_elapsed` 169 or 250 (predicate and boundary assertions).
- **I-6, A/A.** A/A is outside the 84 campaigns (48 + 36). The hold condition depends only on policy, so any future A/A attribution long-idle run would inherit it. None is declared.

## Must-run (unknown) versus source defects

**Source defects:** none blocking. PI1-L1 is test-only and optional before application.

**Doc and handoff:** PI1-M1, before any protocol declaration.

**Must-run after a separate explicit edit window:**
1. Apply the exact patch and verify the postimage hashes.
2. Run the **full** existing scoped bench suite plus the 4 new tests with acquisition intercepted, then the central tests. The author ran only the new 8 off-tree, so regressions in existing tests that might enumerate attribution long-idle actions are unknown [U].
3. Regenerate the `source_snapshot` bindings, then all 52 CLI-typed case identities from the final matrix argv (PI1-M1/L2).
4. Run both full functional preflights. The attribution preflight exercises 4 hold roles. Validator acceptance of H − E is a consistency check only.
5. Optionally, if root authorizes it, a values-blind structural count of post-E retained samples.
6. Run fresh feasibility from that preflight.
7. Rebind the protocol R3 declaration.
8. Assess actual bracket availability across all 60 roles per attribution pool, plus the `hz`, accounting and snapshot-within-interval provenance [U].

**Never:**
- treat the hold as a bracket;
- subtract the hold;
- extend E to H;
- reinterpret old pools;
- infer fit or speed.

## Files written

- this report;
- `/home/ajs/.cache/pb-phase7/review-postidle-opus-r1/evidence.json`.

The failed shell may or may not have created `…/review-postidle-opus-r1/tmp` [U]. I made no other edits, sent no messages and made no commit. **Zero processes are active.**
