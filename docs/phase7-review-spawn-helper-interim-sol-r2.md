# Phase7 native spawn helper: independent interim Sol r2

## Verdict and scope

**No remaining high or medium finding in the reviewed helper scope.** H1 and M1/M2 are resolved; M3 is resolved under the explicitly approved narrow signal policy. The additional no-build/preparation and optional-descendant seams are included in this same interim round. This does **not** replace the required final Opus review or authorize collector preflight, timing, profiling, or candidate acceptance.

Reviewed only this worktree: `harness/phase7_spawn.c`, `phase7.py` NativeChild/execute/proc/descendant integration, and related `test_phase7.py`. Read the full original helper-r1 findings, `docs/phase7-harness.md` responses, and `/home/ajs/.cache/pb-phase7/harness-native-helper-r2-response.json`. Read the collector's `cached_spawn`, `require_seams`, and `Capture` calls solely to verify the subsequently authorized Python boundary; this is not a collector review. No harness, production, or other worktree file was edited by this reviewer.

All four initial hashes matched the original freeze manifest. The user then authorized narrow Python seam changes during this round. Independently inspected/executed code identities:

- C: `0279d5fb06e4f38adc32004c152fc42c94194d59f234ee5a9c5694f74a6d31f4` (unchanged).
- Initial Python: `1d748ac3a1b236115c021a843f3d714e81a0654ebbad27cd30996c69a4415698`; seam revision: `b1d5cd2de90b32268f221c8bf7a5f584a2fa7d34eb134a21d2e3ed475e77a654`.
- Initial tests: `d9344e5a51f72e1a124c864ef9a4b25511b25d4c7049cbb7e4dc53e67a0482c0`; intermediate updated tests: `a61db7ff84378ff8ff0f8a1b10d145dde1d69ec4a236e9b0b3245ab0ec1a9e81`; final frozen tests: `0015bb5a9fcf581c0168f3a4f4a5bb9a74ed2767a4c029e298fccbb2a3b32844`.
- Response document initially read: `6b975173a1785f5ee6865f10c0ae147cb0c5183f3c32f43433cebcef17abbbe2`; final response including the frozen integration section: `db8fdf6a5cdb531dbfe84c175783d953b50789e1fce5eb1ef1255cdaa76d9add`.

The original response manifest describes the initial freeze. The final `/home/ajs/.cache/pb-phase7/harness-nobuild-integration-freeze.json` now matches all four final file hashes above; its exact delta SHA, actual helper ELF, compiler-file SHA and provenance-sidecar SHA were also independently verified. Its appended response section was read. The unchanged helper ELF SHA is `3b684c7b7632302725727fcc586a071c7818f43d704a8cbf4e1afc384e99f03e`; the new compiler-byte-bound build key is `d0592f15c157a359d3b1d7adf9ab0efb1cf39a3765205fe8ccde855157ed2fab`. The independent AST audit confirms only `spawn_helper`, `execute`, and `_execute` definitions changed; `NativeChild`, `proc_sample`, `descendants`, `native_capability_probe`, and `CaptureFDs` remain unchanged.

## Original finding dispositions

### H1 — High, resolved: asynchronous reap / PID and PGID reuse

**Original trigger:** CLI exit between poll and a signal, proc sample, deadline, or output-limit dispatch, allowing native wait4 to remove the identity before Python finished using it.

**Source:** `phase7_spawn.c:202` reserves the exact child with `waitid(P_PID, WEXITED|WNOWAIT)`, stamps/reports `exited`, and waits for release/EOF at `:211`. Exact wait4 is at `:223`. TERM/INT/HUP handlers are blocked at `:220` until after `cli_pid=0`, closing the helper's own post-reap numeric-kill window. `phase7.py:534` uses pidfd process-group flag4, handles ESRCH, and has no numeric observer signal fallback. Start-tick checks surround CLI proc/descendant observations (`:525`, `:907`). Cleanup completes group and identity accesses before `native.release()` (`:1057`, `:1060`).

**Execution:** the independent suites passed WNOWAIT zombie retention/release, exit-during-signal/deadline dispatch, and immediate-exit output-limit tests (`test_phase7.py:228`, `:252`, `:287`). The functional capability probe delivered USR1 to an owned C leader and member and verified reserved Z. The direct EOF test also confirmed full socket closure permits reaping.

**Assessment:** the ordinary observer/helper lifecycle race is fixed. The exit timestamp, 200ms drain, and compatibility lifecycle alias describe native waitid observation; gated wait4 time is reported separately (`phase7.py:1097`, `:1110`). Fatal helper loss has the separate limits below.

### M1 — Medium, resolved: partial acquisition, blocking stdin, cleanup exceptions

**Original triggers:** failure after acquiring some PTY/pipe descriptors; handshake timeout; large stdin with no reader; early closed stdin; supervisor death or a cleanup error masking an observer exception.

**Source:** `CaptureFDs` registers per-acquisition close tokens (`phase7.py:788`); the outer ExitStack owns descriptors and unconditional native cleanup (`:816`, `:849`). Native initialization unwinds failures; `NativeChild.close` bounds helper shutdown/reap and closes protocol/pidfd (`:615`). Pipe stdin is nonblocking and selector-driven (`:855`, `:931`), with only successful writes counted. Cleanup retains unknown status/resources when no native wait4 report exists and records issues (`:1040`, `:1060`).

**Execution:** partial pipe acquisition, injected spawn failure, stalled helper handshake, no-reader/closed-reader stdin, supervisor SIGKILL, original exception preservation, and observer FD-count checks passed (`test_phase7.py:293`, `:302`, `:323`, `:351`, `:583`). A separate direct test sent TERM to the supervisor: the CLI was killed, remained reserved as Z, then reaped with raw SIGKILL status.

**Assessment:** the concrete r1 unwind and blocking-stdin regressions are fixed. This is source review plus bounded examples, not an empirical proof of every possible kernel/resource-exhaustion failure. On fatal supervisor SIGKILL, termination and CLI rusage correctly remain None; adopter reaping is not fabricated.

### M2 — Medium, resolved: setup failure versus executed exit127

**Original trigger:** failed setsid/ctty/chdir/exec looked like an executed CLI exit127 and attributed native setup-child resources to the CLI.

**Source:** structured startup stage/errno reporting is at `phase7_spawn.c:59`, `:166`, and `:193`. Python preserves exec_error and setup-child termination while setting CLI identity/resource source and CPU/RSS/fault/context/block-IO values to None (`phase7.py:858`, `:1081`).

**Execution:** missing executable in pipe mode, missing cwd in PTY mode, and a genuinely executed exit127 all remained distinct (`test_phase7.py:210`). A direct `/dev/null` ctty test additionally received `stage=ctty`, errno ENOTTY(25), and setup-child raw exit127 status.

**Assessment:** reported startup syscall failures no longer masquerade as executed CLI resource evidence.

### M3 — Medium, resolved under approved policy: inherited dispositions

**Original trigger:** foreground/background observer signal inheritance changed phase7 QUIT/XFSZ behavior.

**Source:** the explicit defaults INT/TERM/HUP/QUIT/PIPE/XFZ/XFSZ and empty mask are applied in the C child (`phase7_spawn.c:73`) and posix_spawn (`phase7.py:502`). Other ignored dispositions are deliberately inherited; the user approved this narrower policy in place of blanket defaulting. Default observer SIGCHLD ownership is rejected when absent before acquisition (`phase7.py:413`). Expected observer and supervisor parent checks are at `phase7_spawn.c:149`, `:156`, and `:168`.

**Execution:** the native signal-state test passed selected-ignore removal, inherited ignored USR1, empty blocked mask, and unchanged observer dispositions (`test_phase7.py:368`). Ignored observer SIGCHLD rejection passed (`:572`). The two Go QUIT controls are owner-reported contextual evidence; this reviewer did not run or independently replay them.

### L1–L5 — Low: disposition and boundaries

L1's expected-parent check is fixed; the spawning thread must still outlive capture. L2's exact wait4 block-operation counters are present (`phase7_spawn.c:232`, `phase7.py:1090`); they are not proc-IO bytes or a final zombie-IO total. L3's waited-for descendant sum/max attribution and observed-descendant eligibility are explicit (`phase7.py:1092`); the independent attribution test passed (`test_phase7.py:401`). L4's source/compiler path/version/flags key and Linux-only policy are fixed; compiler executable bytes now also bind the cached version (`phase7.py:421`). The compiler-wrapper version invalidation test passed (`test_phase7.py:411`). L5's requested lifecycle, acquisition, FD, stdin, signal, PTY/pipe isolation, and exit127 tests are materially improved and independently exercised. No observer floor was run or inferred.

## Additional authorized Python boundaries — resolved

**No-build boundary (medium integration defect in the initial freeze):** initial `spawn_helper()` had no `build` parameter, so `cached_spawn()` could not use the shared resolver. Adding that parameter alone would still let ordinary `execute()` rebuild a helper lost after the preparatory check.

Final source uses `spawn_helper(build=True, probe=True)` (`phase7.py:407`); build=False never compiles or repairs a missing/mismatched binary/sidecar. The sidecar must match the exact current source, flags, compiler path/bytes/version, helper ELF hash, key, and protocol (`:430`, `:437`). Preparation can query compiler version and establish the actual own-child capability proof. Capture uses `execute(spawn_helper_build=False)` -> `spawn_helper(build=False, probe=False)` (`:814`, `:836`), requiring an in-process compiler-byte-bound version snapshot and an ELF-bound actual capability proof. Missing/drifted preparation fails closed. `Capture` passes False and `require_seams` checks the published parameter (`phase7_bench.py:424`, `:467`); `cached_spawn` remains the preparatory build=False resolver (`:1048`), with probe=False available for prepared read-only checks. The public defaults retain ordinary build/probe behavior.

**Execution:** ten isolated checks passed. They exercised prepared resolution and a real synthetic Python CLI capture while compiler invocation, version query, capability probe and cache-writing methods were guarded against use; helper deletion after successful preparation failed before openpty; missing/corrupt provenance, changed helper bytes, missing version/proof caches, and simulated changed compiler bytes failed closed. Both full and minimal Capture call shapes were checked with mocked execution/self-usage, without running a collector session. Compiler-byte drift was simulated by changing the read result, not by modifying the system compiler.

**Optional descendant boundary:** the transient minimal Capture call passed `record_descendants=False` while execute rejected the option. Final execute accepts this boolean, defaults True, skips only optional traversal (`phase7.py:830`, `:912`), preserves required group cleanup (`:1057`), and makes comparison eligibility False when traversal is disabled (`:1094`). An independent synthetic same-group child test confirmed traversal was skipped, the child was absent or Z after cleanup, native cleanup issues were empty, and observer FDs were unchanged. The updated owner test exercises both enabled/disabled paths with a real HUP-ignoring child (`test_phase7.py:539`).

## Execution and retained evidence

Evidence root: `/home/ajs/.cache/pb-phase7/review-helper-sol-r2/`.

- `process-tests-original-freeze.log`: **31 ProcessTests passed** against the original freeze.
- `process-tests-updated-seams.log`: **37 ProcessTests passed** against the final Python seam revision and intermediate test hash a61db7ff.
- `final-test-delta.log`: **2 affected tests passed** against the final frozen test hash 0015bb5a: the expanded successful strict-capture test and newly added actual compiler-file-byte drift test. The other tests were unchanged. No full 64-test owner-suite execution is claimed by this reviewer.
- `direct-checks.json` / `direct_checks.py`: three extra direct native lifecycle/setup checks passed.
- `no-build-checks.json` / `no_build_checks.py`: ten no-build/preparation/call-shape checks passed.
- `descendant-cleanup-check.json` / `descendant_cleanup_check.py`: independent optional-observation/required-cleanup check passed.
- `original-freeze-verification.json`, original source snapshots, `seam-source-delta.diff`, `seam-ast-audit.json`, `final-owner-freeze.json`, and `final-artifact-verification.json`: source/artifact identity and change-boundary evidence.

The final affected tests independently exercise physical byte drift in an owned compiler wrapper and a successful native capture with compiler/probe routes forbidden. This supplements the simulated compiler-read drift in the custom ten-check script.

Tests used the specified venv, disk-backed temp directory, disabled Python bytecode writes, and outer finite timeouts. Only owned helper preparation and synthetic native/Python children were run. No candidate/Go CLI session, Rust build, campaign, profiling, timing/floor measurement, or other-worktree edit occurred. Suite elapsed times are runner logs, not performance evidence. Acceptance is not inferred from the owner's 57 or 64 tests, or the services-reported bench/profiler counts.

The first direct EOF assertion incorrectly expected supervisor status0 after closing both socket directions. With no final-report reader, status125 is correct, while the CLI is still reaped. `direct-checks-initial.stderr` retains that reviewer test-expectation failure; the assertion was corrected and all direct checks passed without any harness modification.

## Retained limitations

Normal identity reservation relies on the supervisor retaining the unreaped CLI. Fatal helper death removes that reservation; pidfd signalling stays bound and start ticks gate numeric proc observations, but those reads are not an absolute identity guarantee after catastrophic loss. Such a run lacks valid native CLI resource/status evidence. Observer SIGCHLD ownership and spawning-thread lifetime remain prerequisites. Escaped descendants, missed short-lived/other-thread descendants, adopter reap deadlines, sampled proc-IO tails, and complete absence-of-descendants claims remain outside coverage. Disabling descendant observations explicitly makes isolated-comparison eligibility false.

Final Opus review, separately authorized post-fix candidate collector preflight, and any required observer floor remain outstanding outside this interim review.
