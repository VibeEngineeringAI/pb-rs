# Combined body/lookahead deadline review — independent Sol 6.1 R2

**Accept the exact combined source/test proposal. No blocking findings remain in the reviewed artifacts.** This approval covers the two phase3 normalization entries and their coordinated tests. It does not authorize applying them during a frozen campaign, accept either phase, or establish release/performance readiness. The patches must remain off-tree until the parent/companion handshake and explicit edit window; a final successor retained replay remains outstanding.

This was a fresh executed review in `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`, against base `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`, oracle `07af390ab09bda8ac9e427ffc6310077bace0125`, and the pinned Go 1.25.4 source. I did not use the earlier source-only approvals or the parent's passing logs as execution evidence. My scripts, computed records and logs are in [the independent review directory](/home/ajs/.cache/pb-phase7/review-combined-deadline-sol-r2). I wrote only this report and that directory. Neither worktree nor the supplied staged sources were edited. Patch application occurred only to fresh private cache copies.

## Independently verified scope and mechanism

For each variant, removing exactly these two added dictionary entries reproduces that variant's before-source byte-for-byte:

- `p3-deadline-body-stall-text` → `failed to stream response body`.
- `p3-deadline-text-empty-array-lookahead` → `failed to read response body`.

Both require `compare.timeout_cause` and equality with the **entire** `Error: <case-specific message>: context deadline exceeded\n` byte string. LF is mandatory. The existing canonical body suffix and `TIMEOUT_CAUSE` regex are unchanged. The redirect rule, all corpus definitions, comparison/expectation/loading code, and each variant's own deferred guards remain unchanged. The variants are not whole-source byte-equal and are not interchangeable bases.

I inspected `normalize_timeout`, `compare_stderr`, `manifest_entry`, `expectation_issues`, `differences`, `load_stored`, deadline definitions and the changed tests. In the phase7 staged source, the new entries are at lines 1478/1481; whole-error equality is at 1483. Independent expectations and differential/A/A comparison call the same normalization, so bare acceptance is symmetric. Manifest stderr digests use that same comparison form.

I independently read and bound the application files `cmd/query.go`, `pkg/http/http.go`, and `pkg/http/refresh.go` to **exact bytes from the pinned oracle commit**, using read-only `git show` in the root worktree. I also read and hashed Go 1.25.4 `net/http/client.go` and `transport.go`. The application wrapper is unknown to `knownRoundTripperImpl`; `setRequestCancel` installs both a context deadline and a timer that stores `timedOut=true` before canceling. `send` wraps the successful response body in `cancelTimerBody`. Its `Read` adds the body suffix only after a non-EOF read error and only when the flag is true. With these fixture-created requests there is no independent request context deadline or caller cancellation.

The two application branches are distinct: body-stall reaches `io.Copy` and the stream wrapper at `cmd/query.go:793–794`; `[]` lookahead blocks in `consumeEmptyJSONArray` and returns the read wrapper at 781–783. The initial-prefix failure at 771–772 has the same read wrapper, but is not the observed lookahead path. JSON decoding and awaiting-headers messages remain separate. Nothing here generalizes their bare forms.

## Actual executions

[Suite execution commands and log hashes](/home/ajs/.cache/pb-phase7/review-combined-deadline-sol-r2/suite-executions.json) and the four per-run JSON/log pairs record:

- Phase7 normal: **37 passed, zero failures/errors/skips**.
- Phase7 `-O`: **37 passed, zero failures/errors/skips**.
- Phase6 normal: **37 passed, zero failures/errors/skips**.
- Phase6 `-O`: **37 passed, zero failures/errors/skips**.

Every run included the existing shell runner tests, compiled Go fixture parity/capability checks, and the six-case small Go CLI reference check, which reported **6/6 passed**. Across four suite executions this is 24 small oracle case checks, each using the unchanged default of two reference repetitions, not deadline captures. Python was 3.14.7; optimize flags 0/1 are recorded. No build, Rust execution, real 60/120-second deadline run, timing/profiling campaign, or common lock was used. The environment set `TMPDIR`, `GOTMPDIR`, and `CLAUDE_CODE_TMPDIR` to `/home/ajs/.cache/pb-phase7/tmp`, and `PYTHONDONTWRITEBYTECODE=1`.

The driver explicitly loads the requested staged test with `importlib`, verifies both resolved file paths, and verifies `test.phase3 is phase3 is sys.modules['phase3']` before running. For each variant these paths are exactly:

```
/home/ajs/.cache/pb-phase7/combined-body-lookahead-r2/<variant>/staged/harness/test_phase3.py
/home/ajs/.cache/pb-phase7/combined-body-lookahead-r2/<variant>/staged/harness/phase3.py
```

The driver points Python's bytecode lookup at a fresh unused review-cache path, avoiding the supplied stage's existing bytecode cache. Source/test/binary hashes and all 37 successful test IDs are recorded. There was no old-test import or hidden test selection bypass. The supplied dependency symlinks support the existing isolated fixture child; no staged symlink was changed by this review.

The parent's initial `full-suite.json` failure remains preserved: its plain `import test_phase3` loaded the live old test after import-path reset, and the isolated fixture lacked dependencies. I read that failed record and the corrected driver. I did not count it as a passing run or overwrite it. The independent executions above use my own driver and fresh output names.

## Computed reductions and rejection controls

I wrote an independent process-free reducer rather than rerunning a generator that writes into its owner's directories. [Normal phase7 results](/home/ajs/.cache/pb-phase7/review-combined-deadline-sol-r2/phase7-reduction-normal.json) and the corresponding phase7 optimized / phase6 normal / phase6 optimized records each contain:

- Five body captures: four actual archived Go processes and release4's candidate. All five pass independent expectations; all **20 directed nonself pairs** pass.
- Nine lookahead captures from R4/R5/R6: six actual archived Go processes and three candidates. All nine pass independent expectations; all **72 directed nonself pairs** pass. The record also retains the nine explicitly labeled self-pairs; they are not counted as substantive comparison evidence.
- **65 body and 108 lookahead rejection controls**, using actual captured results: wrong exit, missing/extra/unexpected request, altered stdout, early/late recorded wall values, wrong application branch, request body/header changes, and semantic configuration changes before/after. Empty stdout is an additional body control; it would be an identity operation for lookahead and is not counted there.
- An unchanged corpus and a scope grid over **697 cases × three known bare contexts = 2,091 pairs**. Only the two authorized case/message pairs change relative to that variant's original runner. All **4,182 absent/false-opt-in checks** preserve bare bytes.

Each family has equal computed manifest stderr digests. Every issue list is computed, recorded and checked with explicit exceptions; success does not depend on Python `assert`. Normal and optimized family results and scope grids are exactly equal for each variant.

For **both variants, normally and under `-O`**, I injected in-memory causal controls into the loaded module and executed the real focused tests. No source file was changed:

- Remove body entry: exactly **two test failures**, zero errors/skips.
- Remove lookahead entry: exactly **two test failures**, zero errors/skips.
- Pool all known contexts across allowed case names: exactly **three test failures**, zero errors/skips, including the legacy and both focused boundary tests.

The optimized logs show semantic `unittest` assertion failures, not parse/import errors. They close the earlier pooled-context identity gap and establish that the new tests actually require their respective rules.

## Actual archived Go evidence and original failures

I independently read raw bytes, recomputed hashes, loaded the captures, and checked their raw/normalized stdout/stderr, trace and milestone digests against `result.json`. For report-stored roles, the full `result.json` equals the original report's role entry. I verified all **126 capture artifacts** (45 body, 81 lookahead) against the bound evidence and rechecked them after reduction. The captures are historical executions; this review did not recapture a deadline.

Body's bare Go reference is exactly the 65-byte LF-terminated stream error; SHA-256:
`4ac82e6f88228c4289456931f97f03e61a05b5e0ed0f3c7008bd8fdfbfca10a9`.
The original repeat is canceled-plus-body-suffix. The later single-case Go session contains two processes, canceled-plus-suffix and context-plus-suffix; neither later process was bare. All four Go captures emit the same 34-byte partial stdout and make the matching query request.

Lookahead's R6 bare Go reference is exactly the 63-byte LF-terminated read error; SHA-256:
`8c11d31a7d564af38a8a9df6779d3efc5edb9bad439e06b41acb02396e95b7c5`.
R6's repeat is canceled-plus-suffix. R4/R5's four Go captures establish canceled-plus-suffix and context-plus-suffix. They emit empty stdout. A bare observation occurs once in each family's archived Go evidence; neither is claimed as independently replicated by this review.

All inspected reports bind Go reference SHA-256
`fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`, which I also recomputed for the compiled root small-test oracle. The fixture hash and application/toolchain source hashes are in the verification record. Candidate captures are not counted as Go evidence.

I embedded the original case entries and independently reproduced their entire failure lists with the appropriate before-runner: body has the reference expectation failure plus A/A and candidate stderr differences; R6 lookahead has the A/A and candidate stderr differences. Original reports remain **683 pass / 1 fail**, and their hashes remain:

- Body release4: `a44393207ae776a838daace9e76462a5f9d8a8505843f73e5c52ab1795187934`.
- Lookahead R6: `51183fb78d406fe33f2cfeeb3b7bb67dc1888b6eceaf5a49d12bfe37d9067509`.

The other three report paths/hashes, runner bindings, exact raw spellings and recorded functional wall values are in the reductions. A reduction's clean result does not rewrite these failed campaigns.

## Hash binding and prior findings

[Artifact verification](/home/ajs/.cache/pb-phase7/review-combined-deadline-sol-r2/artifact-verification.json) recomputed all supplied combined hashes, owner patch hashes, declared body deliverable hashes, preserved report/capture hashes, and relevant source identities. Each `git apply --check --verbose` and actual private-cache application succeeded without offset/fuzz, producing both staged files byte-for-byte. The independently computed combined identities are:

- Phase7 patch: `1d5fe9461467023f54ea39e6e3cd29012fe30116cf28686fa550b5e76ed9fdb6`.
  Runner: `3fc89e8997745c326b50e8c66ad21a5d44aaef44473ee918f607a834df6903b4`.
  Test: `ef1a47dc263d18ed94fac7577a7e2597699973e74a4b42d3cbdc598cca9fab59`.
- Phase6 patch: `00f14a8abb88858ce8ecd7160292f0c2d835210743295f7e3cf8f456154f8795`.
  Runner: `af783ab65a14512f0df07f81948e03dd082b103a78ed460554c5981b909d95f2`.
  Test: `812e96091659d8f8e4fabdf4bdeae537d4f2dc153f41a2cb0faa59289bf2e434`.

The report's companion final record binds these checks and my execution artifacts. Both live worktree source/test pairs still equal their own before images. Removing the two entries reproduces each entire runner; all prior test methods except the deliberately revised legacy method are exact. The two body methods plus legacy method equal the corrected body owner patch; the two lookahead methods equal the corrected lookahead owner patch.

Body R1 F1 is resolved: the legacy test reverses **only** body-stall + stream-message acceptance. Its read-error-response negative remains; JSON/gate negatives remain for both older messages, lookahead is negative for both, and sibling case/message identities are pinned. Full-suite execution now satisfies the previously pending suite prerequisite. Body F2/F3 are accurately separated: candidate bare acceptance is intentional; independent lookahead substring tolerance never meant relaxed full differential comparison. F4/F5/F6 are resolved by computed optimized reductions, source/report bindings, original failure entries, and correct single-session/two-process wording.

Lookahead R1 F1–F3 are resolved by exact LF patch application, coordinated legacy naming/negatives, and independently killed pooled-context controls. F4 symmetry is intentional. F5 provenance is supported by the v2 generated/durable evidence byte equality, real source filenames, explicit computed results, and this review's fresh normal/optimized executions. I inspected the v2 generator but did not rerun it or mutate its worktree/cache.

## Coverage, limits and remaining conditions

No new blocking defect was found. Exit, stdout and request graph/unexpected-request checks remain independent gates; request body, contract headers and semantic configuration remain differential checks, not newly invented independent pins. The 59–70-second functional deadline bounds remain unchanged and were exercised with stored-value mutations. Body's independent stderr pin remains exact after normalization. Lookahead's independent stderr pin remains its existing substring, while A/A and candidate comparison still compare complete normalized stderr. The only newly accepted difference is the exact Go-valid bare spelling for each approved pair, including a bare candidate.

Bare JSON body failures, other header/refresh cases, and the new phase4 header observation are outside this proposal. No phase4 file or rule is included. Sibling source mechanisms alone do not authorize normalization. Linux captures and synthetic wall-value controls establish neither a new real deadline run nor performance behavior.

I read the full original body assignment, retained redirect proposal/R1/R2, body proposal/R1, implementation handoff and its linked JSON/patch/script evidence, full companion proposal/R1, v2 `prepare.py`, `evidence.json`, `proposal.patch`, combined manifest/composer/patches, and the relevant application/Go/harness source. No separate companion task-assignment artifact was found by the bounded filename search under its docs/evidence and phase6 cache; its proposal, evidence and original review supplied that brief.

The parent's campaign/companion readiness and eventual edit-window handshake remain supplied state. This review has not completed them. Applying the accepted patch requires that coordination, followed by the required actual candidate/successor retained replay on the final runner identity. The original failed reports and the parent's initial failed suite must remain preserved and distinct from these executed unit checks and reductions.
