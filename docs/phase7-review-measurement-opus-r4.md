# Phase 7 measurement, profiling and helper integration: independent Opus review, round 4

Reviewed 2026-10-14, only in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (base `4a812d5`, Go pin `07af390`). This is a fresh, bounded tooling review delegated as a new task. It does **not** accept the phase, a release, a candidate or any measured result, and it does not authorize timing, profiling or quiet windows. The production saved-matcher fix (O4-1) and the styled 157/160 masked-login investigation are outside scope and remain pending, so no production or phase acceptance can follow from this report.

## Verdict

- **Functional preflight (no timing): fit at source.** The collector and helper tooling are fit for the parent's no-timing functional preflight, subject to the must-run conditions below. None of the new findings blocks the preflight, because all of them concern timing interpretation or campaign design, not functional comparison.
- **Timed tooling campaigns: not ready.** Four new medium findings need a fix or a recorded coordinator disposition first:
  - R4-M1: resize readiness can be satisfied by stale tokens.
  - R4-M2: observer decode cost depends on role-specific byte volume.
  - R4-M3: there is no A/A path.
  - R4-M4: the long-idle chunk pooling and seeds are not implemented.
  - The R3-M2 coverage residual also remains open.
- **Profiler: no high or medium driver defect found.** It is not ready to run until the actual symbol preparation and a real interoperability smoke test pass.
- **Earlier findings:** every R1, helper R1, R2 and R3 finding has a source-level correction in place. The remaining items are listed below. The R3 corrections were re-derived from source, not taken from the response.

## Scope, evidence and limits

**Execution: none.** Two shell attempts both failed with EDQUOT on the Claude Code task temp directory: the bounded first attempt and a second trivial `true`. The second exceeds the one-attempt allowance and is disclosed here. I ran no suite, CLI, compiler, probe, profiler, timing or lock, and computed no SHA-256. I used no symlink workaround and did no cleanup.

Every frozen hash and count is **[O]**, meaning supplied and not verified by me:
- bench `6440fb2f…`, profile `e8214ce1…`, `phase7.py` `26895f68…`, C `0279d5fb…`, ELF `3b684c7b…`, sidecar `69e1067e…`;
- the 54 bench, 23 profile and 72 helper passes;
- the root's 77-test rerun.

My findings are bound to the file contents I read in this session, not to a digest. Passing supplied tests is not taken as acceptance.

**Read in full:**
- The five prior reviews: measurement R1, Sol R2 and Opus R3; spawn-helper R1 and Sol R2.
- All seven response and freeze JSONs named in the brief.
- `harness/phase7_bench.py`, `phase7_profile.py`, `phase7.py` (all 1,696 lines), `phase7_spawn.c`, `profile_external.py` and `test_phase7_bench.py`.
- `harness/contracts/phase7.md`, `docs/phase7-measurement.md` and `docs/phase7-profiling.md`.

**Read in part:**
- The accepted plan, lines 55–123.
- `test_phase7_profile.py`, lines 1–250. The analysis leader/worker fixture body was not read.
- `phase7_workloads.py`, lines 1–120. `performance_workloads` itself was not read.
- `docs/phase7-harness.md`, lines 1–60.
- `docs/phase7-review-resolutions.md`, lines 1–80.

**Not read:** `test_phase7.py` (so the dispatch mock-clock regressions and mutation controls are [O]), `phase1.binary_identity`, the command matrix and the test logs.

**Evidence labels:**
- **[S]** read from the current source.
- **[K]** kernel or pyte semantics, from knowledge and not exercised here.
- **[O]** supplied by the owner or parent.
- **[U]** unknown or unmeasured.

The evidence record is `/home/ajs/.cache/pb-phase7/review-measurement-opus-r4/evidence.json`.

## New findings (most severe first)

### R4-M1 — Medium, blocks interpreting resize latency: resize readiness accepts stale pre-resize tokens plus a partial repaint [S][K], renderer incidence [U]

**Where:**
- `sql_case` gives both resize phases the ordinary `target(0, total)` tokens (`phase7_bench.py:229-233`): `rows 1/N` and `bench-row-000000`.
- The observer applies `terminal.resize` before dispatch (`phase7.py:1027`, `:243-246`).
- In pyte 0.8.2, growing keeps the old cells in place. Shrinking deletes top lines and truncates columns, so the old footer and row-0 marker remain visible [K].
- `Readiness.__call__` (`phase7_bench.py:415-427`) checks four things, and none of them requires the target tokens to come from the new paint:
  - `useful` (the text tokens, which are already satisfied by the stale content);
  - fresh bytes;
  - cells differing from the simulated snapshot;
  - a corner glyph at the new right edge.

**Trigger:** a renderer that repaints top-to-bottom without clearing first. Its first post-SIGWINCH chunk, containing the top border with `┐` at the new last column, already satisfies all four checks. The `first_useful` stamp is then taken at a partially painted frame, while the footer and rows are still the pre-resize content.

A renderer that clears first (for example a backend that flushes a clear before drawing) removes the stale tokens. That renderer qualifies only once the footer is repainted, which is roughly a full frame later.

Separately, after the first post-dispatch byte, every non-qualifying resize evaluation runs a full `terminal.frame()` (`:421`) before rejecting. A frame split across PTY reads therefore adds up to one frame-build time before the next read. The observer floors use contains-only waits (`phase7.py:1609-1610`), so they model neither effect.

**Reach:** `resize-large` and `resize-original` in every SQL variant (500, 1500, 20000, delayed, sustained, long-idle), for both themes and both policies. These are the plan's "worst resize/scroll frame" metric (`resize_first_expected_*`).

**Impact:**
- The bias is renderer-dependent and on the scale of one frame paint plus pyte decode. That scale plausibly reaches the 10 ms investigation trigger, but it is unmeasured.
- Depending on which role clears, the bias can invent or hide a resize regression.
- Functional comparison is unaffected, because `compare` uses the stable frame.

**Minimal correction (bench-owned, no shared change):** for `resize_rerender` checkpoints, require every `contains` token to sit in a row whose cells differ from the post-resize snapshot. Alternatively, require the footer on the new geometry's footer row. Add an emulator test that feeds a top-border-first partial repaint and must be rejected. Otherwise, record that resize `first_expected` is not interpretable for triggers.

### R4-M2 — Medium, interpretation: emulator decode cost proportional to each role's bytes lands inside `first_expected` [S], magnitude [U]

**Where:**
- Output is read, then fed to pyte in Python, and only then stamped as `now` (`phase7.py:950-965`, `:974`). Every `first_expected` therefore includes Python decoding of that phase's bytes.
- The representative control replays only the *Go startup* stream (`phase7_bench.py:619-639`, `:1075-1132`).
- `frame_bytes` is recorded per phase (`:841`, `:860-861`), but no rule ties a byte-volume difference to latency interpretation.

**Trigger:** the two implementations emit different byte volumes for the same scroll, window, toggle or editor change, for example line rewrites versus cell diffs. pyte's per-character and per-SGR Python cost turns several KiB of difference into milliseconds.

**Impact:**
- Any latency trigger (10% and 10 ms) can be crossed or masked by observer decode work that a native terminal does about two orders of magnitude faster.
- The plan asks to separate rendering and decoding costs (plan line 96).
- R1-L3 raised the byte-volume point and was closed by the Go-startup replay. That closure does not cover candidate or per-phase bytes.

**Minimal correction:** pick one.
- Predeclare a disposition: when the paired median `frame_bytes` differs materially, a latency trigger on that phase is non-interpretable without a matched replay control of *each role's* recorded phase bytes. The existing `ReplayCapture` machinery can host this, given per-phase byte offsets.
- Record the observer's per-phase feed time as a descriptive diagnostic, which needs a shared seam.

No subtraction either way.

### R4-M3 — Medium, campaign prerequisite: the collector cannot run the required identical-binary A/A control [S]

**Where:**
- `run_campaign` always calls `artifact(reference, …, 'reference')` and `artifact(candidate, …, 'candidate')` (`phase7_bench.py:1172-1173`).
- `artifact` requires the reference to be Go at the pinned commit and the candidate to be Rust `release` (`:928-931`).

**Requirement:** plan line 108 requires "identical-binary A/A controls". The contract lists A/A as a prerequisite for a timed campaign (`harness/contracts/phase7.md:171-174`).

**Consequence:** a Go/Go or Rust/Rust A/A run is rejected. The only way to run one would be to falsify the language or profile metadata, which is not acceptable.

**Minimal correction:** add an explicit A/A mode. It binds both roles to the same artifact identity and provenance, uses distinct case or report labels, gets its own matching preflight, and never feeds budget evidence. Otherwise, record a coordinator disposition stating how A/A will be run and reviewed.

### R4-M4 — Medium, process: the long-idle 5+30 path depends on unimplemented pooling, and its chunks repeat one role order [S]

**Where:**
- `docs/phase7-measurement.md:265` splits each long-idle variant into seven separately approved chunks. Chunks 3–7 all use `--warmups 0 --sessions 5`. **No `--seed` is given**, so every chunk uses the default seed 7.
- `schedule` (`phase7_bench.py:364-374`) seeds `Random(options.seed)`. For a single-case filter, it therefore produces the identical role-order sequence in every one of those chunks. The control order (`seed + 101`) also repeats.
- `summaries` and `reduce` operate on one report only (`:1055-1072`). The 30-pair pooling is described as an offline parent responsibility with no implemented or reviewed reducer.

**Why it matters:**
- The long-idle resource-attribution variant is the *only* path eligible for the idle-CPU trigger (`:846-849`).
- Its evidence would come from analysis code written after collection, which contradicts "no retrospective relaxation".
- The role order is not randomized across 25 of the 30 pooled pairs.

**Minimal correction:**
- Before collection, implement and review a pooling reducer. It must verify exact binary, source, helper, definition and preflight identities per chunk, require every chunk complete, and pool exactly 30 measured pairs.
- Predeclare and record a distinct seed for each chunk.

The same seed note applies, more mildly, to the 22 single-variant campaigns per policy: each is internally randomized, but they share one sequence.

### R4-L1 — Low: resize delivers SIGWINCH twice [S][K], incidence [U]

`winsize(slave, …)` changes the PTY size, and the kernel then signals the foreground process group. `send_group(SIGWINCH)` immediately sends a second signal (`phase7.py:1029-1030`). Real terminals send only the first.

Depending on races in signal handling, a CLI may process two same-size resize events, which adds repaint bytes and stable time in a role-dependent way. This is shared-owner code and is used by the functional corpus too. At minimum, disclose it. Removing it requires re-baselining the resize cases.

### R4-L2 — Low: the companion agreement is not time- or campaign-bound [S]

`_approval` accepts any `phase6.at_unix` in `[0, now]` (`phase7_bench.py:463`). The companion record carries no `campaign_id`, so an old agreement reference can be copied into a new approval. Require `issued_unix − δ ≤ at_unix ≤ now` and a matching companion `campaign_id`.

### R4-L3 — Low: the functional preflight can perturb another lane's quiet window [S]

The preflight takes no lock (`:1289-1347`; docs `:156`), and its 48 real sessions can overlap a phase-6 or phase-7 timed campaign. Add a nonblocking `LOCK_EX|LOCK_NB` probe of the common lock (fail if it is held, never wait), or make it a coordinator scheduling rule.

### R4-L4 — Low: attribution-policy latencies are not declared descriptive [S]

Resource-attribution sessions add periodic descendant traversal, and `extract_metrics` still emits their phase latencies. The docs (`:125`, `:196`) should state that latency triggers use primary campaigns only.

### R4-L5 — Low: campaign wall-time feasibility is unmeasured [S], cost [U]

The sustained variant stores 85 full styled frames, and each role is deep-copied twice and serialized (`phase7.py:1203-1207`; `phase7_bench.py:587`). Seventy roles plus 12 controls must fit `--budget-seconds 3000` and a ≤3600 s approval. The preflight does not retain per-role post-processing wall time. Derive per-variant wall costs from the preflight before booking windows, so that a budget overrun does not waste an agreed window.

### R4-L6 — Low: latent and documentation drift [S]

- `Phases.change` accepts a `signal` dispatch (`:152`), but `observation_issues` rejects any phase whose trigger lacks `input_hex` or `resize` (`:689`). A future signal phase would always be invalid. This fails closed, and no current case uses it.
- The "Optional collector seams" section of `harness/contracts/phase7.md:183-197` omits `spawn_helper_build` and `record_descendants`.

## Prior findings: independent disposition at the current source

| Finding | Status at current source | Residual |
| --- | --- | --- |
| R1-H1 (fork RSS floor) | Resolved [S]. `posix_spawn` supervisor (`phase7.py:505`), native fork/exec (`spawn.c:161-174`), exact `wait4` (`:223`), and `resource_source` gating (`bench:737`). | Supervisor-copy floor not measured [U]. |
| R1-H2 (zombie samples) | Resolved [S]. Stat-first Z/X rejection, missing RSS/HWM gives `None`, start-tick recheck (`phase7.py:375-400`), and `same_process` bracketing (`:910-918`). | — |
| R1-H3 (trace gate) | Resolved [S]. Runtime waits are screen-only (`bench:146`) and effects are causal post hoc (`:700-709`). | — |
| R1-M1 (preflight) | Tooling present, with R3-L1 dry extraction. **Not run.** | Must-run below. |
| R1-M2 (approval) | Resolved [S] (`bench:448-465`, nonblocking lock `:471`). | R4-L2. |
| R1-M3/M4/L1/L2/L3/L5/L6 | Resolved [S] as R2 and R3 described. First-ever match is never reset (`phase7.py:987-999`). | L2 is superseded for timing by R4-M1; L3 is refined by R4-M2. |
| R1-L4 / R2-M6 / R3-L3 | Resolved [S]: `finite_number` catches overflow (`bench:40-46`), and `check` wraps every `Exception` as `QuietError` (`:485-488`). | — |
| Helper H1 (asynchronous reap) | Resolved [S]: `waitid` WNOWAIT (`spawn.c:202`), release by byte or EOF (`:211`), signals blocked before `wait4` (`:217-220`), and pidfd group signalling with ESRCH handled (`phase7.py:537-544`). | Fatal helper death removes the reservation; this is stated. |
| Helper M1/M2/M3, L1–L5 | Resolved [S]: `CaptureFDs` and ExitStack, bounded `close`, `exec_error` stages, narrow signal defaults plus empty mask (`spawn.c:73-91`, `setsigdef`), SIGCHLD default required (`phase7.py:413`), expected-parent check, block IO counters, compiler-byte-bound key, Linux-only. | The narrow signal policy is user-approved. Thread lifetime and adopter reap remain stated limits. |
| No-build resolver and cache aliases | Resolved [S]: `build=False/probe=False` in capture (`phase7.py:839`), preparation snapshots required (`:426-429`, `:472-474`), keyed symlink rejection including dangling links (`:436-438`), and preparation outside QuietWindow (`bench:1174`, `profile:578-579`). | Ordinary check-to-exec TOCTOU. |
| Mandatory group cleanup | Resolved [S]: unconditional `send_group(SIGKILL)` before release (`phase7.py:1063`), independent of `record_descendants`. | Escaped setsid/setpgid descendants are not contained. |
| R2-H1 (identity binding) | Resolved [S]: `check_command`, `check_binding`, per-role `check_artifact` and `final_identities` in bench, preflight and profile. | Not atomic. |
| R2-M1 (optional observers) | Resolved [S]: primary policy (`bench:33`), eligibility honoured (`:965-966`, `:898`). | R4-L4. |
| R2-M2 (preflight trust) | Resolved [S]: `retained_sample` hash, payload and escape checks; current contracts and comparator rerun; one-read report snapshot (`bench:1350-1430`). | — |
| R2-M3/M4/M5 | Resolved [S]: profiler end identities, reserved-leader group cleanup (`profile:260-285`), and guarded bounded write parser. | Real decoder interoperability [U]. |
| R2-L1 | Resolved [S]: examples share base 60, idle 2, 256 points, 150 ms delay and policy. | Paths must name the post-freeze artifacts. |
| R3-H1 (dispatch origin) | **Resolved at source** [S]. `dispatch_monotonic_ns` is stamped after readiness, digest, frame, substitution and encoding, immediately before `os.write` (`phase7.py:1022`), or after the emulator resize and immediately before ioctl and SIGWINCH (`:1027-1029`). The collector requires exactly one dispatch type and `evaluated ≤ dispatch ≤ first` (`bench:691-695`, `:813-817`). | Remaining in-origin write, ioctl and pidfd cost is unmeasured but small [U]. *Post-receive* observer effects are R4-M1 and R4-M2. Mock-clock and mutation evidence is [O]. |
| R3-M1 (attribution path) | Resolved as a declared matrix [S]: separate policy and suffix, matching preflight, 5+30 per evaluated workload, eligibility requires an observed and empty descendant set. | Execution pending. The long-idle part depends on R4-M4. |
| R3-M2 (coverage) | **Partially resolved** [S]: 503 error cases with exact one-request causality, and a sustained SQL20k case covering 40 forward and 38 reverse windows with refetch. | **Open:** sustained scrolling across *dense PromQL series* (plan line 71) is absent, because the dense case only toggles. Event-receipt, reducer, render and write stages are unimplemented and unknown, and no acceptance may claim them. Rapid input during slow effects is covered only by burst writes crossing delayed prefetches. |
| R3-L1 | Resolved [S]: `extraction_check` in the preflight and in revalidation, status only (`bench:820-826`, `:1331-1333`, `:1390-1394`). | — |
| R3-L2 | Resolved [S]: a `setup-ready` anchor with fresh output and an 80 ms stability hold after shift-tab (`bench:269-272`). | ctrl-q render bytes could satisfy fresh-output; low. |
| R3-L4 | Resolved [S]: `= ?` is counted as interrupted with zero bytes, and exact FD1/PTY equality is kept with an OPOST/ONLCR diagnostic (`profile:380-382`, `:459-474`). Raw-mode strace prints pointers, so buffer contents cannot spoof the result regex. | Writes on fds other than 1 (for example `/dev/tty`) would fail equality [U]. |
| R3-L5 | Resolved [S]: bound readelf preparation, sections verified from the same bytes, and end rehash of used tools and evidence (`profile:509-569`, `:695-710`). | Section presence is not symbol fidelity. If the Go toolchain emits `.zdebug_*` names, preparation fails closed [U]. |

## Checked and found consistent

- **Statistics:** the unit is the independent paired round. Role order is randomized within each campaign, warmups are retained but excluded, frames are descriptive, bootstrap intervals are pointwise and exploratory, ratios are undefined for non-positive references and signed growth, and no valid-subset summary is produced.
- **No compile or probe inside QuietWindow:** guarded lookups are `probe=False` and analysis is strict. Preparation, version and capability probes happen before the lock.
- **Quiet admission:** an approval object, a bounded ≤3600 s issue/expiry window, a campaign binding and an explicit phase-6 actor, reference and time are checked *before* one nonblocking common flock. The lock alone is never sufficient, and there is no waiting.
- **Separation:** primary, resource-attribution, floor controls, profiler symbolized captures and analysis are distinct classes. The profiler records `release_claim=False`, the wrapper PID scope, and heaptrack for Rust only.

## Readiness and must-run conditions (none performed here)

**Functional preflight (no timing):** ready at source once the following are done:
1. The production O4-1 fix and the styled 157/160 investigation are settled and the immutable successor artifacts are frozen. `source_snapshot` binds the current Rust `src`, so any later production edit invalidates a preflight.
2. The parent independently verifies all source, test, doc, helper ELF and sidecar hashes, and reruns the bench, profile and helper suites, including the actual leader/worker analysis fixture, on that revision.
3. The helper is prepared before the run.
4. Both 24-variant / 48-role preflights (primary and resource-attribution) are run into new directories, with no other quiet campaign active (R4-L3).
5. Both preflights reach 48/48 valid roles, 24/24 valid pairs and valid dry extraction, and all raw evidence, including failures, is retained.

**Timed tooling campaigns:** not ready. Before any timed campaign:
1. Fix R4-M1, or record that resize triggers are non-interpretable.
2. Disposition R4-M2 with a byte-volume rule or a per-role replay control.
3. Implement and review an A/A path (R4-M3).
4. Implement and review the long-idle pooling reducer with a predeclared seed per chunk (R4-M4).
5. Decide the R3-M2 residual.
6. Then:
   - pass `--sessions 30 --warmups 5` explicitly;
   - use a matching preflight for each policy;
   - obtain fresh campaign-bound phase-6 agreements;
   - check per-variant wall feasibility (R4-L5);
   - retain controls, A/A runs and fresh-seed confirmations.

**Profiler:** before any profiling:
1. Run the actual `prepare-symbols`.
2. Run a real, bounded interoperability smoke test covering:
   - perf, strace and heaptrack FD3 preservation and the `profiler.stderr` contract;
   - exact CLI stderr under heaptrack preload;
   - heaptrack raw file naming;
   - strace raw-format parsing;
   - perf report without `HOME` in `isolated_env`, and `$HOME/.debug` side effects;
   - symbol resolution.
3. Get valid functional controls.

**Never:**
- subtract floors or stability holds;
- treat primary or attribution descriptive values as trigger evidence;
- infer cross-platform, live HTTP/2 SQL, packaging, installer or rollback readiness from Linux helper or loopback evidence.

Files written: this document and `/home/ajs/.cache/pb-phase7/review-measurement-opus-r4/evidence.json`. No source, test, harness or other file was edited. No thread, lock or message outside the parent was created.
