# Composed P3/P4 deadline review — independent Sol 6.1 R3

**ACCEPT the exact composed patches below. No blocking source, test, amendment or composition finding; no fix required.** This is a fresh scoped executed cross-check of the accepted P3 body/lookahead entries and P4 header rule, including the required P4 F1/F2 amendments. It is not phase, performance or release acceptance and makes no new native replay claim.

Review input: `/home/ajs/.cache/pb-phase6/combined-deadlines-p3-p4-r1`. Independent work and execution evidence: [composed-deadline-review-sol-r3](/home/ajs/.cache/pb-phase6/composed-deadline-review-sol-r3/results.json). Writes were confined to that newly created directory and this report. Both live worktrees, supplied caches and original campaign reports were read-only.

## Exact accepted identities

Phase6:

- Patch: `458036a6bf391ba758a641c8eafe2893bebc168aedd2c1a31e4967e8afde1ba8`.
- `phase3.py`: `01668670868218625371c386820ea18107c29b24a667b684ed1783be3f18eadd`.
- `test_phase3.py`: `812e96091659d8f8e4fabdf4bdeae537d4f2dc153f41a2cb0faa59289bf2e434`.
- `test_phase4.py`: `94f2d11cd3a1a6a5a815bb0f1676f9c800c4cd804299136957244cbea8a2eaa1`.

Phase7 companion variant, checked read-only and applied only to my cache:

- Patch: `b7b9e37c6e19442cd0c8c162218b4d349f4f118f8204095210c7c2fe645346e2`.
- `phase3.py`: `33be7605b2e3ea44712a8118b8909b129d00ef3f46c7f4d3932a522351e2b7bd`.
- `test_phase3.py`: `ef1a47dc263d18ed94fac7577a7e2597699973e74a4b42d3cbdc598cca9fab59`.
- `test_phase4.py`: `b1ed46ac5530a344f408303a1c09e4f4f0eaaaa50c2304b7f7984a592b7c34fb`.

The live source/test bases at start and end match the manifest exactly: phase6 `7f8deaf0` / `f11f07cd` / `37782465`; phase7 `39d16e2b` / `0bea853f` / `1404a43d`. Full hashes, exact application output and copied dependency hashes are retained in `results.json`. Each patch belongs to its own base.

## Independently executed checks

I wrote [review.py](/home/ajs/.cache/pb-phase6/composed-deadline-review-sol-r3/review.py), rather than executing the owner's composer or the prior review scripts. It completed **507 explicit checks**, with **250 read inputs rehashed unchanged at the end**.

- Removing the two accepted P3 dictionary entries reproduces each complete live source byte-for-byte. Adding the unchanged P4 insertion to each accepted P3 source reproduces its composed source byte-for-byte. The P4 source remains the accepted `ca32ffa8cf9eb466a7e4bdc76667383fa0ace2a6b345946b17b6515875f5a325` artifact before composition.
- `test_phase3.py` equals its accepted P3 R2 artifact. The P4 v2 tests are exactly one contiguous insertion into each variant's own `test_phase4.py`, with the manifest's addition hash. No phase-specific test is replaced.
- Independently regenerating the complete three-file diff with unconditional LF splitting gives each supplied patch byte-for-byte; neither patch contains CR. On fresh copies of the live bases, `git apply --check --verbose` and `git apply --verbose` both exit 0, with clean application and no offset/fuzz. Applied bytes equal staged bytes exactly.
- Top-level AST count and order are preserved; only `normalize_timeout` changes. Thus comparison, expectation, corpus and deferred/interactive guard definitions retain their own live ASTs. The composed normalizer AST is identical across variants. Each staged dependency and contract file equals its own live worktree file; tests import only my corresponding cache copies.

The source delta is precisely the already accepted composition: the P4 branch follows the `timeout_cause` opt-in return and precedes the P3 map. It matches the entire exact normalized error/Usage stream and returns unknown P4 streams unchanged before the legacy regex. The two P3 entries retain whole-error equality, exact case/message pairing and mandatory LF. The existing regex, redirect exception and other definitions are unchanged. There is no new case pooling or exception for another deadline family.

## Fresh targeted tests and required causal controls

I ran `python3 -I -B review.py`, optimization 0, with an audit hook installed before harness imports. The hook restricts writes to my cache, permits only the preparation's cache-local `git apply` calls, then forbids processes, sockets, exec/spawn/fork, kill and locking. Explicit guards also forbid thread starts, lock calls and available fixture/sample entry points. No Go/Rust binary, build, native test, fixture, profiling, timing campaign or lock was executed. No delegation was used.

For **each variant**, I executed all 10 `DeadlineTierTests` plus all 7 `HeaderDeadlineNormalizationTests`:

- Clean composed tree: **17/17 pass**, zero failures/errors/skips.
- F1 disabled stderr differential: **17 run, one assertion failure, zero errors/skips**. Only `test_differential_body_headers_status_output_and_config_gates` fails, at its explicit `stderr differs` assertion for Usage `--output` → `--format`.
- F2 disabled `stderr_has` gate: **17 run, one assertion failure, zero errors/skips**. Only `test_independent_exit_stdout_request_and_duration_gates` fails, at its explicit `stderr lacks b'Client.Timeout'` assertion for the EOF stream.

All six executions recorded **zero guard violations**. These are 102 test executions, including four independently constructed causal mutant runs. Each mutant changes the unique corresponding gate conditional; the retained tracebacks show semantic assertion failures. Both required amendments therefore close on the final composed source, independently of the author's and Opus's mutant results.

The clean F1 test also executes all ten directional checks: Usage drift, unknown suffixed cause, bare canceled, missing final LF, and the two different causes sharing an extra trailing line. F2 retains the independent substring expectation. The proposal correctly explains that admitted bare context satisfies it through the synthesized awaiting-headers suffix; the exact whole-stream match remains the admission pin. Identical wrong exit/output/request observations and differential-only request/config limits are stated accurately in the v2 tests and resolution.

I did not rerun `-O`: the [fresh executed Opus R3 report](phase6-composed-deadline-review-opus-r3.md) already covers both modes and both variants. My independent executions cover the requested normal-mode cross-check and required controls.

## Existing evidence inspected, not rerun

I read the complete Opus R3 report, [P4 Opus R1](phase6-p4-header-deadline-review-opus-r1.md), [v2 proposal and resolution](phase6-p4-header-deadline-proposal.md), P3 Opus R2, and the external phase7 Sol R2 report. The required source rationale remains the pinned Go `07af390` / Go 1.25.4 race already accepted there; I did not repeat that campaign or claim new source-race execution.

I independently verified the manifest against the composed evidence index, all four root full-suite JSON hashes and matching log bytes, pass accounting, resolved imports, and every recorded input end hash against current bytes:

- Phase6 normal: `561217ba5d42f3a3806037022adec8b08cf17d8d8938bec37c1108b6b9ab7229`, 101/101.
- Phase6 optimized: `97d9d3842a89fa72c25ab81b8c148d3f6517ff81126f83680451eee72432fcdd`, 101/101.
- Phase7 normal: `daa8b4c269abb47a179b5f4c5535f3914850a0dafa7b9dea8dc7ffd73f8a7530`, 102/102.
- Phase7 optimized: `4f14f863fb8561519eee4fc543911fef300923e2cd851e8daef214a60a1a44a3`, 102/102.

All have zero failures/errors/skips and unchanged inputs. Each uses its own copied harness/dependencies and frozen Go. Both use the explicitly selected phase6 frozen fixture R4; this is not a claim that the phase7 suite used phase7's fixture. Each records 57 audit events for 56 launches, including the duplicate `posix_spawn` capabilities event.

I inspected the prior Opus normal/optimized reduction records, verified the 126 P3 archive artifact hashes they list and the original P3/P4 report hashes, and retained their result hashes: normal `b246645ef0453e29f5f38aa97ad8210437805c98dbb776bbfa38e1d745b6617a`, optimized `144ae079154b9085da7aa5e5275856daee0f44f5322a1f0bea46d4f27a180cbb`. Their P4 result reports 72 verified artifacts, nine captures and 81 clean composed pairs; their scope matrix records 15,840 checks. These reductions and that grid were **inspected, not independently re-executed here**. I did not repeat the 7,650-stream sweep or all 20 proposal mutants.

The inherited blank line and unreachable P4-keyed map mutant identified by Opus require no fix. Original lookahead R6 and body release4 reports remain 683/1; original P4 R6 remains 160/161. Hash verification preserves those failed records without relabeling them as successful executions.

## Handoff and evidence binding

This closes the requested independent Sol composition review. No additional review or procedural gate is requested. Root can proceed with the already described application window, exact own-base application and focused native replay of the three real 60/120-second cases. Phase7 applies its own patch separately and uses its own fixture for that replay. Those actions were outside this review's write and execution scope.

- Independent script SHA-256: `68375796835cf54e6db782bf0e5ab69e6ed3c87b1fef806075d355f8a22b75d5`.
- Independent results SHA-256: `7ea8aa714efce371387eb319b69a6a686a9644b55d216dea2caad36191c2a90c`.
- Six per-run JSON/log pairs and cache-applied/mutated source copies remain in the new review cache.
