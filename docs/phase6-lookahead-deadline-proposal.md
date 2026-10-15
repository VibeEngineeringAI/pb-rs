# Phase 6 retained SQL lookahead deadline: unpromoted proposal

The immutable R6 retained phase-3 run finished with 683 passes and one failure,
`p3-deadline-text-empty-array-lookahead`. The original report and raw files remain
unchanged. Phases 4–5 continue in a separate capture directory; their continuation
does not waive this failure. No production or shared harness file is changed.

Go A/A differs only in the full stderr error: the first run has
`Error: failed to read response body: context deadline exceeded` plus LF, while
the second appends `(Client.Timeout or context cancellation while reading body)`
after `net/http: request canceled`. R6 prints the context-deadline form with that
suffix. All three exit 1 after roughly 60 seconds, emit empty stdout, and make
one matching query request with unchanged semantic configuration.

The same exact case in archived R4 and R5 runs provides four additional actual
Go captures. Each pair contains the canceled-plus-suffix and context-plus-suffix
forms. Thus six actual Go captures already establish all three forms; no new
60-second probe is required merely to recover the known third spelling. The
three candidate captures are retained separately and are not Go oracle evidence.
These functional durations are not performance measurements.

Pinned Go 1.25.4 `net/http/client.go` installs both a context deadline and a timer
with an atomic `timedOut` flag for the application-owned `cloudRefreshTransport`.
`cancelTimerBody.Read` adds the body-reading suffix only if `reqDidTimeout()` is
true after the underlying read fails. The context can fail first. Pinned
`cmd/query.go:783` wraps the empty-array lookahead failure with `failed to read
response body`. This is the same mechanism as the companion's body-stall case,
but a different exact case and error prefix. Its acceptance is independent.

The proposed source change adds exactly one entry to `normalize_timeout`'s
existing `bare_context` mapping: `p3-deadline-text-empty-array-lookahead` maps to
`failed to read response body`. The existing timeout-cause opt-in and full exact
byte-string comparison, including LF, remain mandatory. The rule does not accept
bare request-canceled errors, JSON messages, another prefix, a missing LF, CRLF,
extra output or the awaiting-headers suffix. No case definition, reference,
regex, timeout, request, output or configuration gate changes.

The hash-bound proposal includes two tests in the existing `DeadlineTierTests`:
exact positive/negative boundaries and retained independent/differential gates.
Eight focused tests pass against the in-memory proposed source. With the rule
disabled, both new tests fail. All nine archived captures reduce cleanly against
the proposed rule; full raw and source hashes are in the evidence record. The
original failed report is not rewritten. No process or lock ran during reduction.
An initial test fixture used a boolean where semantic configuration expects an
entry object; that test preparation error and log are retained, then corrected.

Independent review is required before promotion. Shared source remains frozen
while retained runs use it. The companion's body-stall proposal is not bundled
here and must be coordinated separately. After acceptance, unit checks and
source-bound reduction must run on the combined shared patch; a focused actual
candidate replay remains required.

Proposal, source/test before/after snapshots and logs:
`/home/ajs/.cache/pb-phase6/lookahead-deadline-proposal/`.
Durable index: [proposal evidence](evidence/phase6/lookahead-deadline-proposal.json).

## Source-only review and corrected v2 artifact

The [fresh Opus review](phase6-lookahead-deadline-review-opus-r1.md) accepts the
one-case rule at source level, but blocks promotion pending artifact and test
corrections. Its shell was unavailable because of harness quota: it executed no
hashing, tests, reduction or mutants. That review is not independent execution.

The v1 patch used Python's Unicode-aware `splitlines`, so its test hunk numbers
were offset by two lines. V2 uses LF-only line reads. A real `git apply --check`
and application to isolated cache copies both succeed with no diagnostic output;
the applied bytes exactly equal both proposed files. V1 remains preserved.

V2 uses the coordinated legacy test name
`test_legacy_bare_deadline_contexts_remain_case_specific` and adds lookahead to
its negative contexts. New sibling-context and preceding-line negatives assert
byte identity, closing the pooled-context mutant gap. Eight focused tests pass;
disabling the lookahead rule causes two test failures, and pooling all known
contexts across case names also causes two failures, with no test errors.

The v2 script records all nine independent expectation issue lists and all 81
ordered differential issue lists: each is empty under the proposed source.
It records `sys.flags.optimize=0`, complete patch application commands/results,
source/test hashes and the script hash. All evidence fields come from that one
script. Compiled test/source filenames refer to the matching cache snapshots so
traceback text is accurate. Read-only `git show` verification confirms the three
Go application source files match oracle revision `07af390`; Go client source is
hashed at the pinned 1.25.4 toolchain path. These are coordinator checks, not a
new independent review. The original v1 test-preparation error remains preserved.

The comparison rule is intentionally symmetric: a future Rust candidate printing
the Go-valid bare spelling can pass this case. It no longer requires the Rust
body-read suffix. The one observed bare Go capture is not claimed as replicated;
all three forms are established across six Go captures. `cmd/query.go:772` has
the same wrapper as the actual empty-array lookahead path at line 783.

Corrected cache: `/home/ajs/.cache/pb-phase6/lookahead-deadline-proposal-v2/`.
The [v2 evidence](evidence/phase6/lookahead-deadline-proposal-v2.json) is an exact
copy of its generated evidence file. Patch SHA256:
`68d5baa1bf38bdf7f59a7e5f05979f62d870f762b062cccafb623ec0ea1aa140`.
It remains off-tree. Combining the independently scoped companion body rule,
whole shared-suite execution, fresh executed review, runner handshake and a
focused actual candidate replay are still required before retained acceptance.
