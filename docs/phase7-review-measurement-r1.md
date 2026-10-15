# Phase 7 measurement collector — focused review, round 1

Scope: an independent, read-only source review of the interactive measurement collector. This is **not** a final phase acceptance.
Worktree `feat-phase7-interactive`, base `4a812d5`, Go oracle `07af390`.
The only file written is this report.

## What was and was not done

Read in full:
- `docs/phase7-measurement.md`
- `harness/phase7_bench.py`
- `harness/test_phase7_bench.py`
- `harness/phase7_workloads.py`
- `harness/phase7.py`: `execute`, `proc_sample`, `useful`, `sample`, `validate`, `compare`, the statistics helpers, `observer_floor`
- `docs/phase7-sql.md` and `docs/phase7-promql.md`, for source-level key and window semantics
- the head of `docs/evidence/phase5/review-fixes-manifest.json`

Not done:
- **No builds, tests, timings, profilers, child processes or campaigns were run.** The shell was unavailable (EDQUOT on the Claude temp directory), so every observation comes from reading files directly.
- Go sources (`cmd/`, `pkg/`) were not read. Where a finding depends on Go screen text or key behavior, it is labeled *source-unverified*.
- `phase3.Fixture.trace()` was not traced to its implementation. That it is an IPC/HTTP round trip is inferred from `phase7.Fixture` (a separate `serve` process plus a urllib opener).
- `phase2.isolated_env` contents were not inspected.

Evidence classes used below:
- **[S]** source evidence: reasoning from code.
- **[K]** kernel/OS semantics: well known, but not exercised here.
- **[U]** source-unverified against Go.

None of the findings is executable proof.

## Findings (most severe first)

### H1. wait4 peak RSS, and part of system CPU, are floored by the forked Python observer [S][K]
- **Where:** `phase7.execute` (`os.fork()` then `execve`). The collector consumes it in `phase7_bench.observation_issues` and `extract_metrics` (`peak_rss_bytes`, `user/system_cpu_ms`), and `paired_summary` uses them.
- **Trigger:**
  - On Linux, `exec_mmap` folds the old mm's high-water RSS into `signal->maxrss` (`setmax_mm_hiwater_rss`). `wait4`'s `ru_maxrss` is therefore `max(pre-exec image, CLI)`.
  - The pre-exec image is the fork of the collector. Its RSS is about the observer's resident anonymous memory (pyte, retained captures/report, proc samples), and that memory grows as the campaign runs.
  - If observer RSS (likely tens of MiB) exceeds a CLI's true peak, both roles report roughly the observer's RSS. The ratio is pulled toward 1, so a real 20%+2 MiB regression can be hidden or invented, and the floor drifts across pairs.
  - The child's system CPU also includes tearing down the copy-on-write copy of the observer mm at exec. That cost is proportional to observer heap size.
  - The sampled `VmHWM` (post-exec mm only) is collected but not used as the headline value.
- **Minimal fix:**
  - Put the rusage boundary in a small process. For example, launch via a tiny spawn/meter helper (the phase-5 `pb-meter` pattern) that forks and execs the CLI and reports *its* child's wait4.
  - Until then, record the observer's pre-fork RSS per session, label `peak_rss_bytes` as "≥ observer fork floor", and do not apply the RSS trigger when `peak_rss_bytes` ≤ that floor. Report the last valid sampled `VmHWM` separately as a lower bound.

### H2. A sample taken while the child exits gets a `counter_error`, so roughly 1 in 5 sessions is spuriously invalid [S][K]
- **Where:** `phase7_bench.native_proc_sample`, which wraps `phase7.proc_sample`. The loop order in `phase7.execute` is sampler first (if due), then `wait4(WNOHANG)`.
- **Trigger:**
  - After `exit_mm` (exiting or zombie child, not yet reaped), `/proc/PID/status` has no `VmRSS`/`VmHWM`.
  - `proc_sample` defaults `VmRSS` to `'0 kB'`, which fabricates `rss_bytes: 0` (and `fd_count` 0 if the fd directory lists empty). `native_proc_sample` then raises `KeyError('VmHWM')` and sets `counter_error`.
  - `observation_issues` rejects any sample with `counter_error`, so the session is invalid. The sampler fires in the reaping iteration with probability of about poll spacing / 50 ms (about 20% if loop iterations are about 10 ms apart).
  - The campaign stops at its first invalid pair, so the 5+30-pair design would almost never complete. Where the error does not trip, zero RSS and fd values reach `sampled_lifetime` (`rss_end_bytes`, `rss_growth_bytes`, `fd_end`). That breaks the no-zeros rule.
- **Minimal fix:**
  - In `native_proc_sample`, read `stat` first. If the state is `Z`/`X`, or `VmRSS`/`VmHWM` is absent, return `None`, or a tagged `{'exiting': True}` sample that validation and intervals exclude.
  - Separately, the parent should drop the `'0 kB'` default in `phase7.proc_sample` and return `None`.
  - Add a mocked test with zombie-shaped `status` and `stat` contents.

### H3. Readiness is gated on a fixture trace polled every 50 ms, which quantizes and masks startup and window latency [S]
- **Where:**
  - `target()` and `dense_case` waits carry `request_paths`, and window/turnover waits carry `sql_window_counts`.
  - `phase7.execute` refreshes `trace = fixture.trace()` at most every 50 ms (lines ~569–571).
  - `p7.useful` and `Readiness.__call__` require the stale trace to contain the request.
- **Trigger:**
  - On loopback, the gap from request receipt to rendered rows is often shorter than 50 ms. The first qualifying observation is then snapped to the next trace refresh, on a grid phase-locked to process start or action dispatch.
  - `first_expected_ms` for startup, every SQL scroll/window/turnover/resize phase, and dense startup all carry about U(0, 50) ms of added delay. This is larger than the 10 ms / 1 ms investigation thresholds, and differences under one grid step are partly masked rather than measured.
  - Each refresh also appears to be a synchronous IPC/HTTP round trip inside the 10 ms poll loop, competing with the fixture that serves the CLI (inferred, not traced).
  - The 80 ms stability hold starts after this gate as well.
- **Minimal fix (collector-owned):**
  - Keep only screen predicates in runtime waits (row marker, `rows N/T`, `absent`, alternate/cooked). The loaded marker `bench-row-NNNNNN` can only come from a response.
  - Move `request_paths` and `sql_window_counts` to a post-hoc check in `observation_issues`, using fixture milestones (`received_monotonic_ns` ≤ that phase's `first_useful_monotonic_ns`, with the required count). This also makes the current whole-session multiplicity check causal.
- **Alternative (parent-owned):** refresh the trace on every poll while the other predicates already pass.

### M1. The derived performance cases have never run functionally, so the first failure lands inside the quiet window and aborts it [S][U]
- **Where:** `sql_case`, `dense_case`, `editor_case`. `run_campaign` stops at the first invalid pair.
- **Trigger:** Several targets are derived from source and conflict with, or are not covered by, existing Go-validated fixtures:
  - **Dense focus.** `dense_case` sends `t` directly, citing "results focus table/chart". However, `docs/phase7-promql.md` key precedence says `t` acts only "on the table pane", and anything else goes to the focused editor. Both the parent's `performance_workloads()` and the corpus case `promql-table-chart-unwrap` send `ctrl-q`, `shift-tab` before `t`. If focus stays in the editor, `t` is typed into the query and `Table View` never appears.
  - **Dense legend.** The startup wait requires `bench-series-0000` while the chart is shown. With 1000 series, a legend ordered by last value with "+N more" may hide series 0, whose values are the smallest.
  - **SQL footer.** `docs/phase7-sql.md` says the total is unknown (−1) until a short window arrives. For 1,500 and 20,000 rows every window used here is full, so the footer may not read `rows N/1500` or `rows N/20000`.
  - **Page size.** `page_size` re-derives Go layout arithmetic.
  - All of these fail closed (no false data), but each failure spends an agreed quiet window and stops the campaign.
- **Minimal fix:** before requesting a quiet window, run each derived case once against Go (and the candidate) through `Capture` + `Readiness` + `observation_issues` + `compare`. Label the result `functional preflight; no timing claim`. Fix targets that fail, and record the preflight report hash in the campaign `definition`. Whether that preflight needs the common lock is the parent's call; it must not produce timing evidence. Also mirror the `ctrl-q`, `shift-tab` prefix as unmeasured setup actions, or prove focus with Go first.

### M2. The required companion quiet agreement is documented but not enforced [S]
- **Where:** `QuietWindow._approval`, `docs/phase7-measurement.md` ("Parent must agree the quiet window with phase6").
- **Trigger:**
  - The approval schema is `{approved, scope, expires_unix}`. Any unexpired file opens the campaign; nothing in the file or the report shows that phase 6 (the companion) agreed.
  - Expiry has no upper bound, and there is no campaign or nonce binding. A file approved in 2030 acts as a standing approval and can be reused across campaigns.
- **Minimal fix:**
  - Require an explicit, non-empty companion record (for example `"companions": {"phase6": {"agreed": true, "by": ..., "at_unix": ...}}`) plus `issued_unix`.
  - Reject `expires_unix - issued_unix` above a declared maximum.
  - Copy the record into `report.quiet_window`.
  - The lock check itself is correct: there is one nonblocking `LOCK_EX` on the fixed path, it is acquired only after approval, and it is released on every exit path.

### M3. Idle CPU at the default 2 s idle cannot resolve the predeclared 0.25 pp trigger [S][K]
- **Where:** `resource_interval` (`cpu_percent_one_core` from `/proc/PID/stat` utime/stime ticks), with `Options.idle_seconds = 2`.
- **Trigger:** At `CLK_TCK = 100`, one tick is 10 ms. Over about 2 s of sampled interval, one tick equals 0.5 pp of one core, twice the trigger. Each endpoint also truncates by up to one tick. Medians of 0- or 1-tick values carry no real information at the threshold.
- **Minimal fix:**
  - Apply the idle-CPU trigger only to the long-idle sessions (5 min gives about 0.003 pp per tick).
  - Record `tick_resolution_pp = 100 / (hz * sampled_seconds)` in each idle entry, and mark short-idle CPU as descriptive.
  - Optionally, sum `/proc/PID/task/*/schedstat` (nanoseconds) and label it as excluding threads that exit mid-interval.

### M4. "First expected" is the start of the final uninterrupted qualifying run, not the first observation [S]
- **Where:** `phase7.execute` (on `not qualifies`, `first_match` is reset to `None`), and the definition in `docs/phase7-measurement.md` ("first observation satisfying").
- **Trigger:** If a target appears, briefly disappears and returns, the earlier appearance is discarded. Examples: a prefetch toggling `· loading`, an absent-token flicker, or Rust's one-render-per-update intermediate frames.
  - The reported latency is later than first appearance in exactly the implementation that flickers. This is a bias, not noise.
  - The post-hoc check `first < event['monotonic_ns']` is true by construction, so it cannot detect this or an old-frame acceptance. Only `Readiness.output_after_action` protects against old frames.
- **Minimal fix:** either record a never-reset `first_ever_match_monotonic_ns` per action next to the current stamp (parent seam) and report both, or correct the documented definition. Do not present the post-hoc check as old-frame protection.

### L1. Editor target `SELECT ` matches before the space is rendered [S]
- **Where:** `editor_case` `edit-select` (`contains: ['SELECT ']`).
- **Trigger:** pyte rows are space-padded, so the intermediate frame `SELECT` already contains `SELECT `. An implementation that renders per key reports `first_expected` one key early.
- **Minimal fix:** make the target non-blank (for example `send 'SELECT 1'`, target `SELECT 1`), or also check the cursor column.

### L2. The shrink-resize readiness check can accept a truncated old frame [S]
- **Where:** `Readiness.__call__` (`dimensions` is satisfied by the observer itself; `right_edge_corner`; `output_after_action` accepts any byte).
- **Trigger:** In `resize-original` (140 to 120 columns), pyte truncates the old frame. If the 140-wide layout has a corner glyph at column 119, any single byte (for example a cursor sequence) plus 80 ms of stability is accepted.
- **Mitigation:** `compare` would usually reject it unless both roles do the same thing.
- **Minimal fix:** snapshot `terminal.frame()` immediately after the observer applies the resize, and require the qualifying frame to differ from that snapshot.

### L3. The observer-floor control does not match real frame cost [S]
- **Where:** `phase7.observer_floor` emitter (`FRAME %04d`, about 20 bytes per frame); `phase7_bench.observer_controls`.
- **Trigger:**
  - Real frames are kilobytes of SGR-styled output decoded by pyte, followed by `digest(terminal.frame())`. The floor therefore under-states observer cost exactly where Go and Rust emit different byte volumes.
  - "Minimal" mode still runs `descendants()` every 50 ms and `termios_state` on every poll.
  - The controls run once at campaign start rather than interleaved with pairs.
- **Minimal fix:**
  - Add a matched control that replays a recorded Go 120×40 frame byte stream, or label the floor "tiny-frame floor".
  - Correct the "minimal disables proc probes" wording.
  - Optionally repeat a control pair at campaign end.

### L4. Approval file errors other than OSError/ValueError lose the session's evidence [S]
- **Where:** `QuietWindow.check` / `continue_running`.
- **Trigger:** A JSON approval that is not an object (for example `[]` or `null`) raises `AttributeError` inside the `execute` loop. The child is killed in `finally`, which is safe, but no result is returned, so the raw capture is not retained.
- **Minimal fix:** validate `isinstance(value, dict)` in `_approval` and raise `QuietError`.

### L5. Privacy and provenance details [S]
- **Hostname and paths.** `host_snapshot` stores `platform.uname()`, which includes the hostname, plus the raw `/proc/cpuinfo` and absolute `/home/...` paths. Phase 5 used `$PB_CACHE` placeholders and recorded no hostname.
- **Host fields phase 5 kept but phase 7 lacks.** The CPU governor, affinity and toolchain versions are not recorded.
- **Minimal fix:** drop or redact the node name, apply phase-5 path placeholders, and add governor and affinity. Recorded child environments contain only isolated fixture values, as far as the collector shows. `phase2.isolated_env` itself was not inspected.

### L6. `cache-hit` is a label, not a check [S]
- **Where:** `sql_case` for totals ≤ 1500, `return-first` (`{'0': 1}`, already satisfied at startup).
- **Trigger:** A refetch of window 0 is not detected unless the two roles differ (`compare`).
- **Minimal fix:** require exactly one OFFSET 0 request when the phase is labeled `cache-hit`.

## Areas checked and found consistent

**Lock and quiet window**
- Approval is checked before the lock is touched, and the lock is never acquired without approval.
- There is one nonblocking exclusive `flock` with no override, released on failure.
- Expiry and budget are checked before every role and on every active-child poll. Approval is reloaded at least every 250 ms, and expiry kills and reaps the child while retaining the result.
- Overrun past expiry or budget is bounded by work outside child timing: fixture startup (≤ 10 s), the 200 ms post-reap drain, and source hashing. That is acceptable, but should be stated.

**Pair handling and summaries**
- A pair is committed only after both sessions are validated and compared, and metrics are extracted for both before either is committed.
- The campaign stops at the first invalid pair and never summarizes a valid subset. `unattempted_pairs` are explicit.
- `reduce` does not upgrade a failed report.
- Missing or NaN values raise errors instead of becoming zeros. Ratios are undefined for a non-positive reference and for signed growth, with absolute deltas reported instead.

**Fixtures and definitions**
- SQL slice routes are exact. The closing quote prevents OFFSET 500 from matching 5000, and `w[0] == min(500, total - offset)` matches `fetchLimit`.
- The 20,000-row turnover (0, 500, 1000, 1500, then a second 0) is consistent with "at most 3 cached, LRU sparing current, prefetch at ≥ 450".
- Session counts: `plan` gives 14 sessions for 3/1/1 profile/3 floor sessions. The eight primary dark cases match the predeclared list.

**Clock and scope labels**
- `cli_lifecycle_elapsed_ms` scope, main-thread context-switch scope, wait4 versus sampled scopes, and observer SELF scope are labeled as the parent audit described.

**Statistics**
- The inference unit is the paired independent session. Within-session frames are descriptive only. Bootstrap intervals are pointwise and labeled exploratory, with no family-wide claim.
- Prior Rust is limited to the phase 1–5 shared commands. No universal performance gate is introduced.
- These are statements about source wording only. The thresholds are policy and were not re-derived.

## Summary for the parent

- Fix H1–H3 before any timed campaign:
  - H1 means the RSS comparison would not be trustworthy.
  - H2 would make campaigns fail at random.
  - H3 blurs the headline latencies by more than the investigation thresholds.
- M1 should be cleared by a non-timed functional preflight, so quiet windows are not spent on target bugs.
- M2 is a direct gap against the stated companion-agreement requirement.
- The remaining items are small. None requires a broad refactor.

All findings are source evidence. None was demonstrated by execution.
