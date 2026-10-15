# Retained phase 3 body deadline: unpromoted proposal

Status: the [historical round-1 review](phase7-review-body-deadline-r1.md)
approves the narrow source rule conditionally on its F1 test correction. The
combined source/test correction is now staged **off-tree only**, tested with the
pure phase3 suite, and ready for another review. It has not been promoted or
independently reviewed as a combined patch. Shared `harness/phase3.py` and
`harness/test_phase3.py` remain frozen until the parent explicitly says
**“body deadline edit window open”**. No production change is proposed.

The original release4 phase3 report remains **683/684 passed**, with
`p3-deadline-body-stall-text` failing on Go reference instability. Raw captures
and their 27 artifact hashes are preserved in
[the original evidence](evidence/phase7/p3-body-deadline-original.json). The
parent reports that retained release4 phases 1/2/4/5 have finished and passed;
that supplied campaign status does not promote this correction.

Two pinned Go processes in the original replay produced respectively the whole
errors `Error: failed to stream response body: context deadline exceeded\n`
and `Error: failed to stream response body: net/http: request canceled (Client.Timeout or context cancellation while reading body)\n`.
The release4 candidate produced the context-deadline form with the same suffix.
All three exited 1 near the 60-second client deadline, emitted the same 34-byte
partial body, made one matching query POST, and preserved configuration. These
retained functional durations are not performance results.

Source rationale: pinned Go 1.25.4 `net/http/client.go` installs a context
deadline and a separate timed-out flag through `setRequestCancel` for the
application's wrapped transport. `cancelTimerBody.Read` (972–984) appends the
body-reading suffix only when `reqDidTimeout()` is true after the underlying
read fails. The context can fail the read before the timer stores that flag.
The text branch reports the `io.Copy` error as `failed to stream response body`;
prefix/lookahead errors instead say `failed to read response body`. This is a
different branch from the previously reviewed redirect header race; that review
does not authorize this case.

The source delta remains one additional exact name/message pair in the existing
`normalize_timeout` allowlist: `p3-deadline-body-stall-text` maps to
`failed to stream response body`. The existing `compare.timeout_cause` opt-in
and exact whole byte-string equality remain mandatory. It uses the existing
body suffix and cause normalization. The full corpus and all other rules stay
unchanged. No JSON rule, broader prefix match, timeout policy change, output
relaxation, or production edit is included.

The complete proposed shared-source patch also deliberately reverses F1's
existing negative **only for that name and stream-phase message**. The renamed
`test_legacy_bare_deadline_contexts_remain_case_specific` retains the
body-stall negative for `failed to read error response body`, and preserves the
JSON/gate negatives for both earlier messages. Two new tests cover all three
Go forms and their manifest stderr digests, all requested whole-error/opt-in
negatives, independent exit/trace/stdout/window/error gates, differential
body/header/configuration mutations, and symmetric candidate-bare acceptance.
The single-request case uses an extra-request control; reversing its lone
request would be an identity operation.

Independent expectations retain exit 1, exact partial stdout, the one-request
method/path graph with no unexpected requests, and 59–70-second bounds.
Request body/contract headers and semantic configuration remain differential
checks, not newly added independent corpus pins. The synthetic suffix alone
is not timeout evidence.

## R1 dispositions and corrected coverage wording

F1: [the body-only combined patch](evidence/phase7/body-deadline-r1-fixes-20261014-implementation/body-deadline-r1-fixes-lf.patch)
contains the one source entry, the deliberate existing-test correction, and the
two focused tests. Its SHA-256 is
`40df1e798bc5ea26b2541682ad4820d3b212c64851562e4f467022ebc99a820d`.
[Before/after source and test hashes](evidence/phase7/body-deadline-r1-fixes-20261014-implementation/patch-manifest-lf.json)
bind the patch to the frozen worktree copies and staged copies under
`/home/ajs/.cache/pb-phase7/body-deadline-r1-fixes`.

F2: the rule accepts a Go-valid bare stream error from **either** reference or
candidate. It does not promise that Rust always emits the suffix. Release4's
candidate emitted the suffixed context form. After this proposal, no phase3
text-mode body-read deadline case independently requires the suffix: the other
cases allow exact bare forms or use independent prefix expectations. However,
**independent pin coverage and differential/A/A coverage are different**.
`differences` still requires equal `compare_stderr` bytes. In particular,
lookahead has no normalization exception in this patch, so its bare and
suffixed forms still fail both differential and A/A comparisons. The historical
review's “coverage drops to zero” wording applies to independent suffix pins;
it must not be read as absence of all differential suffix coverage.

F3: `p3-deadline-body-stall-json` shares the body-read race through
`json.Decoder` and `error decoding JSON response`; its independent JSON pin
still requires the suffix. A bare JSON capture would need its own evidence and
review. No bare JSON observation is established here.
`p3-deadline-text-empty-array-lookahead` shares the prefix-read branch. Its
`stderr_has` prefix is tolerant **only in independent expectations**; its full
comparison remains strict. The historical review remains unchanged, with this
corrected disposition recorded here.

The parent reports that Phase6 reproduced a separate bare-versus-suffixed
lookahead failure (683/684) at
`/home/ajs/.cache/pb-phase6/retained-review-r6-full/phase3/cases/p3-deadline-text-empty-array-lookahead`.
Its owner is handling separate evidence/proposal and a fresh review. The parent
also supplied a separately staged lookahead patch at
`/home/ajs/.cache/pb-phase6/lookahead-deadline-proposal/proposal.patch`, SHA-256
`1e393cfed2d0d15211487bdd6307e4795b1a59dac8cb9e8520f4bed06af584a1`.
The parent subsequently supplied its settled LF-only v2 patch at
`/home/ajs/.cache/pb-phase6/lookahead-deadline-proposal-v2/proposal.patch`,
SHA-256 `68d5baa1bf38bdf7f59a7e5f05979f62d870f762b062cccafb623ec0ea1aa140`,
and reports independent hashing there. That supersedes the supplied v1 pointer;
its source/test hashes and checks belong to the companion proposal. Neither
lookahead patch nor its evidence has been applied, combined, edited, or
independently verified by this implementation. Its possible future composition
is outside this body-only patch. Body-stall prefix-read errors remain negative.

F4: the [durable reducer](evidence/phase7/p3-body-deadline-reduction.py) now loads
and verifies the exact staged runner path, records computed issue lists and
manifest digests, enumerates each negative check, tests non-canonical output
explicitly, excludes vacuous self-pairs, records `sys.flags.optimize`, and uses
explicit raising checks that survive `python -O`. It rejects missing evidence,
source/corpus changes, and artifact hash changes before writing a success
record. The LF-only combined patch was dry-run checked and applied only to cached before
copies; both resulting files exactly match the staged after bytes.
[An any-known-context mutant](evidence/phase7/body-deadline-r1-fixes-20261014-implementation/any-known-context-mutant-rejected.log)
was caught under `-O` by the legacy cross-case identity assertion. The legacy
test also keeps lookahead negative for both older message contexts. Both normal
and optimized reduction executions passed five stored captures,
20 directed nonself pairs, 19 negatives, and 65 captured-data mutation controls.
Four injected faults under `-O` were rejected without a success report:
overbroad normalization, always-empty expectations, always-empty differential
results, and a missing campaign. These are process-free reductions, not reruns.

F5: [supplemental provenance](evidence/phase7/body-deadline-r1-fixes-20261014-implementation/supplemental-original-failure-runner-identity.json)
embeds the original failing case entry (including all three issue strings) and
both report paths, hashes, and `runner_sha256` values next to the computed
frozen source identity. Both reports used runner
`39d16e2ba220aad27ec0e67646e04aacbcdd9a9c28d3013f3f04ddefec789099`.
The original evidence's “fresh runs active” field is preserved as a historical
snapshot; the original proposed reduction and new checked reduction records
supersede that status. Original reports and artifacts remain unchanged.

F6: the fresh controls were **one single-case `reference-check` session with
two actual Go processes**, not two sessions. One produced canceled-plus-suffix,
the other context-plus-suffix; neither fresh process was bare. The bare branch
still rests on the original actual capture and the pinned source mechanism.
Phase6's two suffixed body outcomes previously reported here remain supplied
companion observations, not additional execution by this implementation.

## Executed validation and remaining promotion gates

The [source-stamped pure-suite log](evidence/phase7/body-deadline-r1-fixes-20261014-implementation/patched-pure-lf.log)
records the actual off-tree runner/test paths and hashes. All 29 pure tests
passed normally and [under `-O`](evidence/phase7/body-deadline-r1-fixes-20261014-implementation/patched-pure-lf-optimized.log), including the renamed scope test and both new tests. The 27 baseline
pure tests passed; their outcomes are unchanged after accounting for the rename.
Running the original F1 test with the staged one-entry runner reproduced its
expected failure; that failing log is retained.

Six executable-launching tests were excluded: four shell runner tests, the Go
fixture parity test, and the Go CLI oracle test. An audit hook prohibited
process launches in the pure-suite runs. Python fixture tests used the existing
repository imports/contracts and local Python fixture; no Go fixture was run.
This satisfies the authorized pure checks, **not the review's whole-suite
promotion requirement**. No Go/CLI/Rust build or capture, retained replay,
profiling, benchmark, or common lock was performed.

[The implementation handoff](evidence/phase7/body-deadline-r1-fixes-20261014-implementation/handoff.md)
links checked reductions, test results, hashes, and exact commands. Remaining:
independent review of this exact source/test/evidence correction; an authorized
full suite run with fixture; parent/Phase6 shared-runner coordination and the
explicit edit window; and final successor retained replay on the final revision.
None is claimed complete. Original 683/1 failure evidence remains distinct from
reductions. No phase acceptance, promotion, performance, or release readiness
is implied.
