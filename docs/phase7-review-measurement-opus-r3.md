# Phase 7 measurement, profiling and helper integration: independent Opus review, round 3

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`. This is a bounded tooling review. It does **not** accept the phase, a release, a candidate, or any measured result, and it does not authorize timing, profiling or quiet windows.

## Verdict

- **Functional preflight (no timing):** the collector and profiler tooling is fit for the parent-run, no-timing functional preflight on a frozen successor candidate. That holds only once the must-run conditions below are met; in particular, the hashes and test counts I could not verify must be confirmed.
- **Timed campaigns:** not ready. One new timing-blocking defect (R3-H1) must be fixed or explicitly measured and dispositioned first. The CPU/RSS eligibility path (R3-M1) and the plan-coverage gaps (R3-M2) also need coordinator decisions before any performance budget is evaluated.
- **Profiler:** no high or medium defect was found in the profiler driver. Its real interoperability with perf, strace and heaptrack remains unproven.
- **Prior findings:** every earlier collector, profiler and helper finding has a source-level correction in place. The residuals are listed below. This round is not a rubber stamp: the new findings come from tracing clocks and code paths that earlier rounds did not cover.

## Scope, evidence and limits

**Read in full:**
- `docs/phase7-review-measurement-r1.md`
- `docs/phase7-review-measurement-interim-sol-r2.md`
- `docs/phase7-review-spawn-helper-r1.md`
- `docs/phase7-review-spawn-helper-interim-sol-r2.md`
- the accepted plan `/home/ajs/.cache/pb-roadmap/phase7-final.md`
- the collector response JSON
- the cache-alias `freeze.json` and `owned.diff`
- `harness/phase7_bench.py`, `harness/phase7_profile.py`, `harness/test_phase7_profile.py`, `harness/profile_external.py`
- `docs/phase7-measurement.md`

**Read in part:**
- `harness/phase7.py`, lines 1–1620. This covers the terminal, `proc_sample`, the resolver including the alias guard, `NativeChild`, the capability probe, `execute`/`_execute`, `sample`, `validate`/`compare`, the statistics routines and `observer_floor`.
- `harness/phase7_spawn.c`, lines 55–239.
- `phase1.binary_identity`.
- the head of `harness/test_phase7_bench.py`.

**Execution: none.** Every shell invocation, including `true`, failed with EDQUOT on the Claude Code task temp directory. I therefore ran no suites, did not run the authorized owned leader/worker analysis test, and computed no SHA-256 digests. Per the brief, this review is explicitly limited to source.

Evidence labels:
- **[S]** read from the current source.
- **[O]** owner- or parent-reported and **not** verified by me. This covers the frozen hashes (bench `0d4fba3e…`, profile `17e551a6…`, `phase7.py` `f07826d9…`, C `0279d5fb…`, ELF `3b684c7b…`) and the 45 bench / 20 profile passes.
- **[U]** unknown or unmeasured.

I confirmed that the current `phase7.py:436-438` matches the `owned.diff` resolver hunk textually; I did not confirm it by hash. My evidence record is `/home/ajs/.cache/pb-phase7/review-measurement-opus-r3/evidence.json`.

No production, harness, test or other-worktree file was edited.

## New findings (most severe first)

### R3-H1 — High, blocks timing: the input-latency origin is stamped before the observer's own pre-send work [S], magnitude [U]

**Where:**
- `phase7.py:974`: `now` is taken.
- Between that stamp and the write, the following run:
  - `Readiness.__call__` (`phase7_bench.py:360-385`): `terminal.text()` plus a full `terminal.frame()` at :376. For resize actions it also runs `copy.deepcopy` of both pyte screens, a simulated `resize()` and another `frame()` (:364-369).
  - `digest(terminal.frame())` at :997 (a full styled-frame build plus JSON plus SHA-256).
  - another `terminal.frame()` at :1004.
  - for resize only, the observer's own `terminal.resize` at :1026.
- Only then does `os.write`/`winsize`/SIGWINCH run (:1022, :1027-1028).
- `event['monotonic_ns'] = now` (:1014) is the origin that `extract_metrics` uses for every change phase's `first_expected_ms`, `stable_begin_ms` and `ready_stable_ms` (`phase7_bench.py:780-783`).

**Trigger and reach:** every scripted key, burst or resize phase, in every timed campaign, for both roles. `Terminal.frame()` does per-cell Python work at 120×40 = 4,800 cells (`_cell` re-imports, rebuilds a 16-colour tuple and copies the cell). At least three such frames plus a JSON digest precede every send. Resize phases add a deep copy of two full emulator screens.

**Why the floors do not cover it:** `observer_floor` measures the emitter's pre-write stamp to first observation (`phase7.py:1579-1589`), so the floor excludes the observer's dispatch path entirely. The tiny-frame and recorded-frame controls therefore do not cover this cost, and no control resizes.

**Impact:**
- An unmeasured, roughly common offset is added to both roles. It depends on cell count, so `resize-large` (140×45) and `resize-original` (120×40) differ.
- Paired absolute deltas are mostly preserved, so this does not invent a difference. But ratios are compressed toward 1. Example: if the offset were 70 ms, a true resize regression from 5 ms to 10 ms would read 75 → 80 ms, about 7%. That stays under the "10% and 10 ms" trigger, so the regression would be **hidden**.
- The plan's "worst resize/scroll frame" metric and its distinction between "event receipt … write completion" are contaminated in absolute terms.
- `docs/phase7-measurement.md` describes the origin as "pre-send dispatch; includes write/ioctl cost" and does not mention this observer work.
- This is the same class as R1-H1 (an observer floor masking regressions), so it is rated High. If a measured offset turns out to be ≪1 ms it would drop to Low. Source evidence makes that unlikely, but I did not measure it.

**Minimal correction:**
- In the shared `execute`, take `dispatch_ns = time.monotonic_ns()` immediately before the `os.write` / `winsize` / `send_group(SIGWINCH)` block. Record it as `event['dispatch_monotonic_ns']`, keeping the current stamp under another name such as `evaluated_monotonic_ns`.
- Have the bench use `dispatch_monotonic_ns` as the phase origin. Do not change it silently: re-label it in the definitions.
- Add a mock-clock regression test proving that readiness and frame work precede the origin.
- This is a narrow edit to frozen shared `phase7.py` and needs parent authorization. No C or API change is needed.
- If the parent declines, the alternative is a matched control that measures observer dispatch → child key read (the emitter stamps at `read`), reported next to the metrics. In that case, ratio-based latency triggers for change phases must be marked non-interpretable.
- This does not block the functional preflight.

### R3-M1 — Medium: under the documented plan, the predeclared CPU/RSS triggers have no eligible campaign path [S]

- Primary captures set `descendant_comparison_eligible=False` by construction, and `check_binding` enforces it (`phase7_bench.py:891-892`). As a result, `cpu_rss_trigger_eligible` and `idle_cpu_trigger_eligible` are always false for primary runs (`:772-775`, `:824`). This is correct fail-closed behaviour.
- The only documented attribution preflight covers the two five-minute idle variants (4 sessions; `docs/phase7-measurement.md:188`).
- The predeclared policy (`:203-206`) nevertheless applies CPU (10% and 1 ms) and RSS (20% and 2 MiB) triggers to all eight workloads. The plan calls native CPU and RSS "the comparable resource measures".
- **Consequence:** following the docs produces no eligible CPU or RSS evidence for any non-idle workload. The parent would then be tempted to read primary descriptive values as trigger evidence.
- **Required decision before timing:** either
  - (a) declare a full resource-attribution preflight (all variants, its own report) and attribution campaigns using the same 5+30 design for every workload whose CPU/RSS budget is evaluated; or
  - (b) record an explicit coordinator disposition, with its cost measured, that periodic descendant traversal is an *essential* safeguard for resource-primary sessions. Sol R2-M1 already offered that option and I do not grant it silently.
- No code defect; this is a gap in the documentation and plan.

### R3-M2 — Medium (coverage): the performance workloads omit plan items [S]

- `campaign_cases` accepts only `sql-scroll-*` and `promql-dense-*`, plus the editor, delayed and long-idle variants. Anything else raises `ValueError` (`phase7_bench.py:294-300`).
- Plan line 71 also requires error paths. None is present.
- "Sustained scrolling across large SQL results" is reduced to four window bursts over the first 1,500 of 20,000 rows plus two resizes.
- "Distinguish event receipt, reducer time, rendering and write completion" cannot be measured from the PTY. It needs in-process, feature-gated diagnostics, and the docs correctly disclaim reducer time.
- **Required:** add cases or record explicit coordinator dispositions before any claim of performance acceptance. Without them, no budget statement may describe these items as measured.

### R3-L1 — Low: metric extraction is never exercised before the quiet window [S]

- `functional_preflight` and `validate_preflight` run `observation_issues` and `compare`, but not `extract_metrics`.
- `extract_metrics` raises on an incomplete or non-causal fixture milestone, or on a distribution error (`:796-800`, `:720-722`).
- Such a failure first appears inside an approved window and aborts the campaign after a functionally valid pair. This is the R1-M1 concern again.
- **Fix:** call `extract_metrics` on retained preflight sessions and record only success or the error, with no values and no timing claim.

### R3-L2 — Low: the dense "unmeasured setup" keys are folded into the measured phase [S]

- `ctrl-q` and `shift-tab` are appended as wait-less actions immediately before the `t` change (`phase7_bench.py:255-259`). They are dispatched in consecutive ≤10 ms loop iterations.
- Their processing and re-render can still be in flight when `t` is stamped, so `table-view` latency includes them.
- **Fix:** insert an anchor checkpoint after setup, for example `output_after_action` plus an 80 ms stable frame (kind `anchor` is already skipped by the metrics), or relabel the phase.

### R3-L3 — Low: approval reload can still escape the callback through non-`ValueError` exceptions [S]

- `QuietWindow.check` converts only `OSError`/`ValueError` (`:440-443`), and `continue_running` adds only `QuietError`.
- A deeply nested approval JSON makes `json.loads` raise `RecursionError` (a `RuntimeError`), and so would `MemoryError`. Either escapes into `execute` as a callback exception. The child is still cleaned up, but no result is returned, so the capture is lost. This is the residual of R2-M6.
- The file is parent-authored, so practical incidence is low.
- **Fix:** wrap `_approval()` in `check` with `except Exception` and raise `QuietError`.

### R3-L4 — Low: strace-writes analysis is brittle on legitimate traces [S]

- `write_counts_bounded` (and the phase-5 parser it mirrors) requires a numeric result. A write interrupted by a signal prints `= ? ERESTARTSYS …`, which raises "write result unavailable". Plausible triggers are SIGWINCH during resize and Go runtime signals while a PTY write is blocked.
- The FD1-bytes-equals-PTY-bytes check (`phase7_profile.py:537`) assumes no OPOST/ONLCR translation outside raw mode.
- Both fail closed, but they can leave strace-writes "failed" for valid captures.
- **Fix:** count `= ?` results as interrupted calls with zero bytes and a separate counter. Report the cause of any equality mismatch.

### R3-L5 — Low: profiler provenance residue [S]

- Symbol fidelity rests only on the builder's `debug_symbols: true` attestation. Phase 5 also checked `readelf` for `.debug_info`, `.debug_line` and `.symtab`; that would be a parent-authorized preparation inspection, not a capability probe.
- The perf, strace and heaptrack tool identities are checked before each use but are not re-verified in `final_identities`.

## Prior findings: independent disposition at the current source

| Finding | Status | Residual |
| --- | --- | --- |
| R1-H1 (Python fork RSS floor) | Resolved [S]. `posix_spawn` supervisor, native fork/exec, exact `wait4`, `resource_source` gating, setup-child rusage nulled. | Native supervisor-copy floor not measured [U]. |
| R1-H2 (zombie samples) | Resolved [S]. Guarded `proc_sample` returns `None` for Z/X or missing RSS/HWM, rechecks start ticks, and `same_process()` brackets each sample. | — |
| R1-H3 (50 ms trace gate) | Resolved [S]. Runtime waits are screen-only; effects are checked post hoc against receipt milestones up to and including the first-ever match. | Superseded by R3-H1 on the *input* side. |
| R1-M1 (preflight) | Tooling present. **Final preflight NOT RUN.** | R3-L1. |
| R1-M2 (approval) | Resolved [S]. Object type, campaign ID, issue ≤ now < expiry, ≤ 3600 s lifetime, phase6 actor/reference/time, one nonblocking `LOCK_EX` taken after approval. | One approval may open several campaigns with the same ID within its hour. This is the parent's discipline. |
| R1-M3 / M4 / L1 / L2 / L3 / L5 / L6 | Resolved as Sol R2 described [S]. `first_match` is no longer reset on non-qualify, so `first_ever == first_useful`. | L3 floors exclude dispatch-side cost (R3-H1). |
| R1-L4 / R2-M6 | Overflow resolved. | R3-L3. |
| R2-H1 (identity binding) | Resolved [S]. Canonical `binary_identity` path; `Capture`/`ProfileCapture` check the exact argv before `execute`; `check_binding` checks the returned binary, case, command and policy; `check_artifact` runs before and after every role; `final_identities` covers all artifacts, metadata, the helper and source, in bench, preflight and profiler. | Ordinary drift only, with no atomic exec binding, as stated. |
| R2-M1 (optional observers) | Resolved [S]. `primary` keeps the root sampler and turns termios/chunks/descendants off; eligibility is honoured; attribution is a separately named class. | R3-M1. |
| R2-M2 (preflight trust) | Resolved [S]. Exactly two retained roles; no symlinks or escapes, with a size cap; hashes taken before and after; stream bytes equal the session payload; `observation_issues` and `compare` rerun; source, end-source, helper and end-helper verified; one-read report snapshot rechecked; replay limited to revalidated cases. | — |
| R2-M3 (profiler final drift) | Resolved [S]. `final_identities` plus a root-manifest recheck in `finally`. | R3-L5 (tools). |
| R2-M4 (analysis cleanup) | Resolved [S]. The reserved WNOWAIT leader anchors a pidfd group SIGKILL, then a bounded same-pgrp/session non-zombie scan, before release, `wait4` and close. Setsid applies in pipe mode (`phase7_spawn.c:169`), so the group never includes the observer. The real process test exists. | Its pass is [O]; I did not run it. Escapes via setpgid/setsid are not contained. |
| R2-M5 (parse cancellation) | Resolved [S]. Guard check per line, 64 KiB line cap, 512 MiB / 65,536-identity caps, partial evidence written in `finally`, chunked hash guard. | R3-L4. |
| R2-L1 (matched examples) | Resolved [S]. The release3 examples share base timeout 60, idle 2, 256 points, 150 ms delay and the primary policy; long idle gets 360 s. | Paths must be replaced by the post-fix successor. |
| Phase6 A/B | Resolved [S]. `json_snapshot` interprets and digests one `read_bytes` from a canonical path; `check_snapshot` re-resolves; `sample` re-resolves, so a retargeted alias fails `check_command`. | — |
| Helper R1 H1/M1/M2/M3, cache alias | Consistent with Sol R2 at source. The alias guard rejects keyed binary/sidecar symlinks before `prepared()` and before build fallback (`phase7.py:436-438`). Capture resolves `build=False, probe=False`; probing happens only in preparation outside QuietWindow (`validate_preflight`/`cached_spawn()` precede `QuietWindow`). | Hash/ELF parity [O]. Check-to-exec TOCTOU, fatal-helper-death, SIGCHLD and thread-lifetime prerequisites remain as stated. |

## Checked and found consistent

- **Statistics:** the unit is the independent paired session round. Role order is randomized per round and the seeded case order is blocked by case. Warmups are retained but excluded. Frames and bursts within a session are descriptive. Bootstrap intervals are pointwise and exploratory. Ratios are undefined for non-positive references and signed growth, which report absolute deltas instead. No summary is produced for a valid subset, and no claim of always-faster or cross-platform performance is made.
- **Idle and long idle:** tick and endpoint resolution are recorded. Short idle is descriptive. Long idle runs 300 s inside a 360 s session cap. Coverage requires at least two samples inside the idle interval.
- **Failure retention:** the execute backstop archive, `sample` streams and session, controls-progress, profiler raw sessions on failure and analysis records on failure are all retained. Expiry kills, then reaps, then retains.
- **Profiler PID attribution:** the wrapper PID is the profiler process and the CLI PID is unknown for perf/strace; for heaptrack it is the same PID after `env` exec. Heaptrack applies to Rust only. There is no Go heap comparison. `release_claim=False`.
- **Shell use:** analysis argv has no shell. The launcher and shim shell are used only around perf/strace captures, and they are labeled as process-tree overhead.

## Must-run conditions (none performed here)

**Before the functional preflight:**
1. Review the production SQL fallback-clock correction and freeze the new immutable candidate.
2. Independently verify the frozen bench, profile, `phase7.py`, C, helper ELF and sidecar hashes.
3. Rerun the owned bench, profile and `test_phase7` suites, including the actual leader/worker analysis test, on that revision.
4. Then run the documented 18-variant / 36-session primary preflight into a fresh directory.
5. Require 36/36 valid sessions and retain the report. Preferably also apply the R3-L1 extraction dry check.

**Before any timed campaign:**
1. Fix or measure and disposition R3-H1.
2. Decide R3-M1 and R3-M2.
3. Pass `--sessions 30 --warmups 5` explicitly. The CLI defaults (3/1) are exploratory, and sessions/warmups are not part of the case hashes.
4. Size each bounded subset so it fits inside one ≤3600 s campaign-bound approval with phase6 agreement, or renew explicitly.
5. Use a matching preflight for each observer policy.
6. Retain the controls, A/A runs and fresh-seed confirmations.

**Before profiling:**
1. Run a real interoperability smoke test for perf, strace and heaptrack. It must cover FD3/stderr separation, the native CLI's exact stderr, heaptrack's in-process diagnostics against exact stderr comparison, symbol fidelity, and perf's `$HOME/.debug` side effects in the temporary home.
2. Resolve or accept R3-L4.

**Never:**
- subtract floors or stability holds to manufacture reducer time;
- treat primary descriptive CPU/RSS as trigger evidence;
- infer live-network, cross-platform or always-faster results.

Files written: this document and `/home/ajs/.cache/pb-phase7/review-measurement-opus-r3/evidence.json`.
