# Phase 7 collector R2 and profiling-driver R1 — interim independent Sol review

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`.
**Changes requested before timing/profiling. No functional, performance, helper, or phase-wide acceptance is granted.** This independent interim Sol review does not replace the required final Opus review; the immediately rate-limited Opus R2 attempt produced no acceptance.

## Scope and evidence

Read the original `docs/phase7-review-measurement-r1.md` in full, `docs/phase7-measurement.md`, `docs/phase7-profiling.md`, and `/home/ajs/.cache/pb-phase7/collector-measurement-review-r1-response.json`. Reviewed the complete frozen bench/profile drivers and their tests, the relevant shared execute/sample/validation/statistics/helper contracts, and the consumed `profile_external.command/artifacts/write_counts` interfaces. Re-read `/home/ajs/.cache/pb-roadmap/phase7-final.md`, especially lines 71 and 86–114. Shared `phase7.py`/`phase7_spawn.c` implementation acceptance remains the separate helper review's responsibility.

Independent execution was limited to Python unit checks using the existing cache venv, `PYTHONDONTWRITEBYTECODE=1`, and all three required temporary-directory variables pointing to `/home/ajs/.cache/pb-phase7/tmp`:

- Bench: **40 tests passed**, 2.038 s.
- Profile: **13 tests passed**, 0.788 s.
- Independent review reproductions: **8 checks passed**, 0.127 s. These assert the current gaps and one positive retention property; passing them means the described behavior was reproduced, not that the gaps are fixed.

Reproduction source: `/home/ajs/.cache/pb-phase7/review-measurement-sol-r2-20261014.py`. Its native/session/analysis subprocess boundaries are mocked. Temporary locks are isolated test files. No actual Go/Rust CLI, floor emitter, compiler, capability probe, profiling tool, processing child, common performance lock, timing campaign, or functional preflight was run by this reviewer. Neither existing adapter integration tests nor these reproductions prove real profiler/CLI interoperability: the former reach real execute parsing but deliberately fail the resolver before PTY acquisition.

Evidence labels below: **[S]** inspected source; **[P]** executed bounded pure Python/mock reproduction; **[O]** owner evidence read and hash-verified, not independently rerun. Conditional triggers are stated explicitly; no real workload incidence or overhead magnitude is inferred.

## Findings

### R2-H1 — Final-session executable drift can survive bench acceptance [S][P]

**Where:** `harness/phase7_bench.py:980–992,1010–1016`; preflight `1062–1090`. Consumer contract: `harness/phase1.py:346–350`, `harness/phase7.py:1183–1186`.

**Trigger:** An artifact is replaced after the last role's pre-session hash check, including during fixture setup or that session. Bench never compares `result['binary']` with the declared role identity and never rehashes executables at campaign end. Source snapshot comparison does not include external immutable-artifact paths. The reproduction replaces the synthetic executable file in the fourth/final mocked session and returns its changed binary identity: bench still reports `complete`, while the final file SHA differs from `report.artifacts.candidate.identity.sha256`. The initial artifact path is resolved correctly, but a later replacement of that resolved pathname with a symlink is resolved again by `sample`; the returned path/SHA also goes unchecked. This is a temporal binding gap, not an assertion that the initial selected path is wrong.

Functional preflight is weaker: it records artifact identity once, then does no per-role or final executable rehash and does not compare captured identities. Thus an executable replacement during preflight can leave a valid report attributed to the initial artifact. This extension is source-inferred; the executed reproduction traversed the bench campaign with mocked sessions.

**Correction:** Keep the canonical selected path; record the exact command received by `Capture`, and require its executable path plus each returned capture's binary path/SHA/size to match the declared role. Rehash before and immediately after every role, and rehash both artifacts before final completion in bench and preflight. Retain capture evidence and fail the campaign on any discrepancy. Add final-role replacement and resolved-path retarget tests. These checks cover ordinary drift; do not claim atomic executable identity against an adversarial swap without a stronger exec boundary.

**Profiler distinction:** `phase7_profile.py:342–344` already rehashes all four binaries at the end, so a persistent final artifact change fails there. Its intercepted exact native CLI argv is retained (`134–160`), but the caller still does not compare the returned binary identity with the selected artifact. Apply that per-capture binding there too; do not import phase6's findings as proof about this implementation.

### R2-M1 — Primary runs retain optional observer histories against the acceptance wording [S][P]

**Where:** `harness/phase7_bench.py:449–478,989–990`; shared `harness/phase7.py:830–834,906–916,957–959,981–1008`; acceptance plan `/home/ajs/.cache/pb-roadmap/phase7-final.md:71`.

The exact requirement is: **“Disable optional observers for primary runs and measure instrumentation overhead separately; never introduce a persistent observer as a port dependency.”** Primary campaigns construct `Capture(guard)` with default `minimal=False`. This explicitly enables periodic termios-history records and stdout-chunk histories, retains the root proc sampler, and leaves periodic descendant traversal enabled by the shared default. The reproduction verifies the actual adapter kwargs. Disabling production diagnostics addresses a different instrumentation boundary; it does not disable these harness observers. The primary configuration therefore does **not** meet the optional-observer clause as currently written.

Essential observation includes PTY reads and emulator decoding, screen/cursor/style predicates, first/stable checkpoint clocks, byte totals/raw streams, active deadlines, native wait4/protocol identity, readiness termios reads and final restoration/cleanup. Root `/proc` counters are needed for the declared idle CPU/RSS/FD/IO metrics. They cannot be switched off while still claiming those metrics. Conversely, periodic termios *history*, per-read chunk *history*, and the explicitly optional descendant traversal can be disabled independently of that root sampler. Checkpoint/raw-stream evidence must remain.

**Practical correction:** Introduce a primary capture policy separate from today's floor `minimal` policy: keep `native_proc_sample`, set `record_termios=False` and `record_chunks=False`, and explicitly choose the descendant policy. Under the literal current wording, set `record_descendants=False` as well, preserve mandatory group cleanup, and retain `descendant_comparison_eligible=False`; an unobserved empty list must not establish isolated CLI attribution. Bench's current `extract_metrics` at `758` derives eligibility only from list emptiness, so update that consumer to honor the shared eligibility flag before using this policy. Use separately labeled attribution/resource sessions with descendant observation enabled when isolated CPU/RSS triggers require that evidence. Alternatively, the coordinator can explicitly document a narrowly justified essential descendant safeguard for resource-primary sessions, including its cost; that is a policy disposition, not something this reviewer silently grants.

Record the selected observer policy in definitions/preflight/report, test the real primary kwargs, and measure its remaining overhead separately. Existing tiny/representative full/minimal floors are useful but do not convert full primary captures into observer-disabled captures. No floor subtraction, stability-hold subtraction, or pure reducer/input-receipt latency claim is justified. No persistent production observer dependency was found in this scoped driver review; production-wide verification remains separate.

### R2-M2 — Preflight acceptance trusts row flags without checking retained evidence or observer revision [S][P]

**Where:** `harness/phase7_bench.py:1067–1094,1098–1118`; limited replay verification `517–531`.

**Trigger:** A report retains `valid:true`, matching case/artifact/helper hashes, but a case has missing role samples, deleted/changed captures, or was validated under a different Python observer/comparator revision. `validate_preflight` accepts `samples:{}` because its `any(...)` is empty. It never verifies stored session/stdout hashes, revalidates captures, or compares the report's source snapshot with the active observer. The reproduction accepts a report with no sample files at all and a stale source field. A deleted candidate capture from an otherwise valid report produces the same unchecked-evidence condition. Replay only checks reference session hashes while searching for a matched startup payload; it does not repair candidate/all-case preflight validation.

**Correction:** Require exactly reference/candidate samples for each selected case, with valid retained session/stream identities; verify them before entering QuietWindow. Re-run independent `observation_issues` and `compare` on retained sessions under the active contracts, or require matching frozen observer/comparator source hashes and verify their prior validated evidence. Bind preflight capture identity to R2-H1's actual artifact checks. Add a preflight end source/helper check and validate the relevant active harness revision before timing. Keep build-source attestations distinct from current-worktree hashes. Missing evidence or a changed observer requires an explicit fresh validation/preflight, not trusted booleans.

### R2-M3 — Profiler reports can complete without verifying final source/harness drift [S][P]

**Where:** `harness/phase7_profile.py:264–268,341–357`.

**Trigger:** Source/harness files change after the initial snapshot, including during capture/analysis. The driver takes only one `source_snapshot`, never records `source_after`, and can declare all rows valid/complete. This differs from bench's explicit end snapshot comparison. The mocked successful campaign confirms exactly one source read and no final source field. It does not demonstrate that any real profiler run experienced drift.

**Correction:** Snapshot and compare relevant source/harness files before final completion; retain the final snapshot on failure. Perform a final read-only prepared-helper verification too. Preserve the recorded build attestations and metadata-file identities; if metadata immutability is a campaign requirement, recheck those original files explicitly rather than implying the existing binary rehash covers them.

### R2-M4 — Analysis cleanup depends on the leader still being alive [S][P]

**Where:** `harness/phase7_profile.py:176–215`, especially `208–213`.

**Trigger:** An analysis tool spawns a same-group child and its leader exits first; approval/budget then fails, or decoding is rejected after that exit. `process.poll()` is non-None, so cleanup never signals the process group, even though that group can still contain the child. The reproduction models a returned leader followed by the final guard exception and verifies no `killpg` call. It proves the cleanup branch behavior only; no claim is made that installed perf/heaptrack currently leave such children.

**Correction:** Treat the analysis process group as an owned resource independent of leader liveness, with a bounded identity-safe cleanup/verification strategy for remaining children on success and failure. Do not simply add an unchecked late numeric `killpg` after an arbitrary reap interval. Add a leader-exits/child-remains seam test, and later an owner-authorized process test. The existing leader-alive expiry test remains useful but does not exercise this trigger.

### R2-M5 — Raw write-trace parsing cannot observe expiry while parsing [S][P]

**Where:** `harness/phase7_profile.py:225–228`, consumed `harness/profile_external.py:59–95`.

**Trigger:** A large `strace-writes` capture takes significant time to parse and the wall budget/approval expires during parsing. The guard runs only before and after `write_counts`; its line loop has no cancellation check. A failed guard afterward prevents success but cannot bound work or promptly release the quiet window. Raw profiler files have no declared byte cap here. The reproduction confirms the guard call count stays unchanged throughout the real pure Python parser, then advances only after it returns. No large file or timing experiment was run.

**Correction:** Add periodic guard checks to bounded parser chunks, or run decoding in an owned bounded processing child; retain partial analysis on cancellation. Declare practical raw-artifact/analysis size limits. Heaptrack compression already checks per chunk; apply comparable boundedness to write analysis and explicitly account for identity-hash work outside active checks.

### R2-M6 — A JSON integer outside float range escapes approval failure retention [S][P]

**Where:** `harness/phase7_bench.py:365–369,397–410`; shared capture return contract `harness/phase7.py:1038–1118`.

**Trigger:** During a session, replace a valid approval with JSON containing `expires_unix: 10**400` (a valid JSON integer within Python's JSON digit limit). `math.isfinite` raises `OverflowError`; neither `_approval` nor `continue_running` converts it to `QuietError`/a failure string. The pure callback reproduction confirms the exception. Shared `execute` still enters cleanup, but an unhandled callback exception prevents its result return; `sample` consequently cannot serialize that completed/aborted capture. This is a residual original L4 retention gap, not a failure to kill the child.

**Correction:** Bound numeric fields before conversion or catch overflow inside the strict numeric predicate and classify it invalid. Ensure all supported malformed approval values reach the ordinary failure-string path. Add an active-reload oversized-integer test alongside the existing list/null/bool/NaN cases, then verify retained capture behavior through an intercepted execute boundary.

### R2-L1 — The documented timed example does not match the documented preflight timeout [S]

**Where:** `docs/phase7-measurement.md` timed example versus `153–160`; `phase7_bench.py:189,224,243,1111–1112`.

The final preflight example sets base timeout 60 seconds, while the timed example inherits 30. Timeout is inside the hashed case definition, so using those examples together fails definition matching before timing. This is safe rejection, not false measurement. Align the timeout/idle/fixture/observer policy in the final commands or explicitly explain why a matching fresh preflight is required. The release2 candidate path in that document is also pre-fix context: use the SQL-identity-fixed successor and its actual final SHA, never the old example merely because its file exists.

## Original review dispositions and checked contracts

- **H1:** Source correction is present: native supervisor wait4, helper C/binary/compiler/protocol provenance, setup/exec-error rejection, and native exited time distinct from gated wait4 time. No Python fork image is the CLI rusage boundary. Actual native-helper acceptance is separate; this review did not independently measure the floor.
- **H2:** Bench delegates one complete shared guarded proc sample. Shared sampling checks exiting state, missing RSS/HWM and final identity/state and returns None rather than fabricating zero. Missing/insufficient counters fail validation. No second bench status reread remains.
- **H3:** Case builders extract request-path/count predicates into posthoc effects. Runtime waits are screen-only; shared execute polls fixture traces only when those predicates exist, so the reviewed measurement cases no longer have that 50 ms readiness gate. Receipt timestamps constrain phase effects to first-ever observation; cache-hit offset zero is exact for the whole session.
- **M1:** Dense focus, highest-value legend, explicit SQL totals/window slices, turnover and Unicode editor contracts are corrected at source. **Full final-artifact functional preflight remains pending.** The coordinator's reported release3 SQL-identity process pass is narrower than all 18 variants/36 sessions and is not substituted for that gate.
- **M2:** Explicit campaign ID, bounded issue/expiry, companion actor/reference/time and one nonblocking common lock are enforced. Approval is external; none was manufactured. No lock or quiet campaign was entered here.
- **M3/M4:** Tick/endpoint resolution is reported; short idle is descriptive, long-idle eligibility explicit. The shared first-ever match survives flicker independently of the final stable interval. Observation remains pre-spawn/pre-send through PTY/emulator scheduling, not exact child input receipt or reducer time.
- **L1/L2:** Nonblank `SELECT 1`; emulator-only resized screen snapshots, fresh output, changed cells and right-edge checks. Cursor-only/truncated old cells fail the executed pure emulator test.
- **L3:** Tiny and recorded-Go startup replay controls remain distinct with independent session pairing. Minimal floor suppresses optional histories/descendants **and** root sampling. It is not a suitable primary resource-capture switch; see R2-M1.
- **L4–L6:** Common malformed approval shapes fail closed, with the remaining overflow exception above. Public paths are normalized; no hostname/raw cpuinfo in host snapshots; governors/affinity and embedded JSON/helper C hashes retained. Actual cached-hit refetches fail independent causal/whole-session expectations.

Session/config/fixture state is fresh per `sample`; measured rounds, not individual frames, are bootstrap units. Warmups are retained and excluded. Pair commitment occurs only after both roles validate/compare/extract, with failed/incomplete campaigns producing no valid-subset summary. Nonpositive references/signed growth have undefined ratios with absolute deltas. Pointwise intervals and 30-session tails remain explicitly exploratory/descriptive. A/A and fresh-seed confirmations remain coordinator obligations, not accomplished by emitter floors.

**Completed-capture retention check requested after the phase6 report:** Shared `sample` writes `stdout.bin`, `stderr.bin` and `session.json` before returning to either driver. Bench/profiler post-validation exception handlers retain those files and fail the row/report. The independent reproduction raises from bench `observation_issues` after mocked `sample` writes the session and confirms it remains on disk with no measurements. Source inspection establishes the same ordering for profiler and preflight. This positive result concerns post-return validation exceptions; R2-M6 concerns an exception inside execute before sample receives a result.

## Profiling attribution limits

Scheduling is bounded and controls for all four release/symbolized binaries precede every instrumented capture; processing follows captures. Symbolized provenance must match release source commit/tree/language and actual SHA, with optimized/debug-symbol builder attestations. Every control validates against Go release; profiles compare against their own symbolized controls. Missing/denied/empty/ambiguous/symlink artifacts retain failure/unavailable status, not zero resources. Heaptrack targets Rust only through direct preload; it makes no Go-managed-heap comparison.

External perf/strace stderr is routed separately through literal argv and inherited FD3; the CLI shim restores its native stderr, whose comparator remains exact. Real FD3 preservation, denial diagnostics and symbol fidelity remain unexecuted here. Missing external diagnostic files should not be described as proof of successful separation; real captures must verify the expected file/FD contract. In-process heaptrack diagnostics remain part of exact CLI stderr and are not filtered.

For perf/strace, shared `native_spawn.cli_pid` is the native-supervisor child running the profiler/wrapper, **not the descendant pb PID**. Proc samples refer to that PID; perf/strace trace their child workload. ProfileCapture's added scope and `release_claim=False` correctly deny isolated uninstrumented CLI resource claims, but inherited raw `metrics.source`/`native_spawn.cli_pid` labels must be interpreted with that scope. Record a wrapper-PID/native-CLI-PID mapping or explicitly mark the latter unknown before doing PID-specific attribution. Heaptrack's env process execs the Rust binary in the same PID. None of these raw resources establishes release performance, pure pb CPU, wait time from elapsed-minus-CPU, or comparable cross-language allocation counts.

## Final freeze and remaining gates

All eight owned source/test/doc hashes match the collector response. Bench SHA `f66499504f9087a66bfa554bb739b1a79cbe294ffe30262b745ece5f84ab49b0`; profiler SHA `8c2c703aac7b52bb7543541f6ee95ca684df044f387a0907b27997ad02450555`.

Verified the final `/home/ajs/.cache/pb-phase7/harness-nobuild-integration-freeze.json` against current files and read its exact resolver/observation delta and added tests. Shared Python SHA is `b1d5cd2de90b32268f221c8bf7a5f584a2fa7d34eb134a21d2e3ed475e77a654`; C is unchanged `0279d5fb06e4f38adc32004c152fc42c94194d59f234ee5a9c5694f74a6d31f4`; ELF SHA is `3b684c7b7632302725727fcc586a071c7818f43d704a8cbf4e1afc384e99f03e`. Test/doc/diff/log identities match the freeze. The owner's **64 passing helper tests**, including actual child cleanup with observation disabled, are **[O]**, not my executed evidence.

Initial `cached_spawn(probe=True)` prepares with build=False before QuietWindow; guarded lookups use probe=False; both adapters execute with spawn_helper_build=False and resolve False/False before PTY acquisition. The final resolver checks source/compiler/helper/manifest identity read-only and requires in-process prepared snapshots. Missing cache/proof or drift fails without compilation, capability/version subprocesses, cache repair or writes. Optional descendant observation affects eligibility, not mandatory native group cleanup. No consumer-contract blocker was found in that final seam; separate helper review and final Opus acceptance are still required.

Before timing: resolve the findings, freeze and independently review the corrected collector/profiler revision and primary observer policy, then run the **complete final-successor functional preflight and full retained gates** on exact immutable artifacts. Use matched case definitions (including timeout) and verified retained captures. Obtain the explicit campaign-bound parent/phase6 agreement before bounded common-lock campaigns, retain A/A/floor controls and fresh-seed confirmations, and keep profiler attribution separate. Builds, compatibility preflight, profiling and timing remain unauthorized in this review. Final phase-wide Opus review, packaging/cross-platform and successful live-service gates remain separate.

Only this review document and the unique cache reproduction file were written. No production, harness, repository test, other review, or other worktree was edited.
