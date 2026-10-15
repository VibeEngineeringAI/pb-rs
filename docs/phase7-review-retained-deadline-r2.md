# Phase 7 review: retained redirect-deadline normalization (round 2)

Scope: independent focused re-review of the implemented exact-case,
`timeout_cause`, whole-error exception for `p3-deadline-spans-redirects`
([proposal](phase7-retained-deadline-proposal.md), [round 1](phase7-review-retained-deadline-r1.md)).
This approves a narrow promotion of the shared harness. It is **not** final phase acceptance.

Original brief: the change must not mask real timeout, request or exit
regressions. No broad optional suffix and no error-category normalization.

## Method and limits

I used read-only access. **I executed nothing**: no tests, hashing, Go or Rust
processes, or timing. Every shell call failed with EDQUOT. Consequences:

- I did **not** recompute any SHA-256. That includes the manifest `files` map,
  `source_sha256`, `original_report_sha256` and the executable identity.
  Where I say hashes are consistent, I mean that values recorded in different
  files agree with each other. I did not hash any bytes.
- I did **not** run the 33 tests. I read the log
  `/home/ajs/.cache/pb-phase7/p3-redirect-normalization-tests2.log`. It shows
  `Ran 33 tests … OK`. Its `phase3 setup error: reference fae8411f… is not the
  frozen manifest's 0000…` line is the expected rejection asserted by
  `GoOracleTests.test_small_reference_subset_and_manifest_identity` (exit code
  2). It is not a failure. The log does not record which `phase3.py` revision
  ran.

I read:

- `harness/phase3.py`: `TIMEOUT_CAUSE` (lines 90-93), `normalize_timeout`
  (1473-1490), `compare_stderr`, `manifest_entry`, `expectation_issues`,
  `differences` (1544-1697), and the case definition (2510-2512)
- `harness/test_phase3.py`: `DeadlineTierTests`, lines 340-428
- `docs/evidence/phase7/p3-redirect-deadline/reduction-manifest.json`, in full
- `/home/ajs/.cache/pb-phase7/oracle-p3-redirect-deadline-2/report.json`
  (header and case entry)
- pinned Go
  `~/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64/src/net/http/client.go`:
  `knownRoundTripperImpl` (320-340), `setRequestCancel` (351-415), and the
  `Client.do` send-error path (726-736)

I did not re-read `pkg/http/http.go` or `refresh.go`. For the
`cloudRefreshTransport` wrapper I rely on round 1.

## Source rationale: independently confirmed

- `knownRoundTripperImpl` returns true only for `*Transport`, the http2
  transports and `*http2.Transport`. It returns false for any other wrapper.
- `setRequestCancel` with an unknown transport skips the fast path at 358-368.
  It then installs **both** `context.WithDeadline(oldCtx, deadline)` (373) and
  a timer goroutine that does `timedOut.Store(true)` and then `doCancel()`
  (406-408). It returns `timedOut.Load` as `didTimeout`.
- `Client.do` (732-733) appends ` (Client.Timeout exceeded while awaiting
  headers)` only when `didTimeout()` is true when `c.send` returns an error.

So at one deadline, three outcomes are reachable:

| Outcome | Description |
| --- | --- |
| bare `context deadline exceeded` | the context wins before the store |
| `context deadline exceeded (Client.Timeout …)` | the context wins after the store |
| `net/http: request canceled (Client.Timeout …)` | the timer cancel wins |

A bare `request canceled` is unreachable, because the store precedes
`doCancel`.

The evidence now shows **Go itself producing all three forms**:

| Form | Capture |
| --- | --- |
| bare | original `reference` |
| context + suffix | `fresh-reference` |
| canceled + suffix | `reference-repeat-1` and `fresh-reference-repeat-1` |

That is stronger than in round 1, where the context-plus-suffix form had only
been seen from Rust.

## Implementation check

`normalize_timeout` adds one conjunctive branch. It requires all of:

- `compare.timeout_cause` (an early return otherwise)
- `case["name"] == "p3-deadline-spans-redirects"` exactly
- `data` equal to the whole byte string `Error: request execution failed: Post
  "http://<FIXTURE>/v2/api/v1/query": context deadline exceeded\n`

When all hold, the branch only appends the suffix before the existing
`TIMEOUT_CAUSE` substitution. There is no regex, no prefix or substring match,
no new optional suffix, and no error-category change. The pre-existing
`TIMEOUT_CAUSE` is untouched.

The case definition (line 2510) is unchanged: exit 1, a two-request graph and
`stderr_has` awaiting-headers. The frozen-corpus-hash claim is therefore
consistent. I did not hash it.

`expectation_issues` and `differences` both go through `normalize_timeout` /
`compare_stderr`, so the rule applies to both roles, as intended by F2.

Regression masking, against the brief:

- **Exit.** `termination` is checked independently (1561) and differentially
  (1652). The rule does not affect it.
- **Requests.** The independent check compares the method plus unquoted path
  list from `trace` exactly and rejects unexpected requests (1563-1567). The
  differential check compares the full `requests` contract, including
  `body_b64` and headers (1675). The rule does not affect either.
- **Timeout.** The independent window is 59–70 s through the full60s defaults,
  and the candidate floor and ceiling are checked in `differences`
  (1681-1686). Early, independent or per-hop (about 95 s) deadlines remain
  rejected. The whole-string match pins the second-hop URL, so a first-hop or
  single-hop timeout cannot qualify.
- **Residual, accepted.** For a bare capture, `stderr_has "Client.Timeout
  exceeded while awaiting headers"` is satisfied by the synthetic suffix, so it
  is tautological for that form. Evidence of a timeout comes from the exact
  second-hop string plus exit, graph and window, not from the suffix. The
  proposal states this.
- **Residual, accepted.** A Rust candidate that emits the bare form on this
  exact path is accepted, because it is a Go-valid outcome. This is recorded as
  F2.

## Tests (read, not executed)

`test_redirect_bare_deadline_has_an_exact_case_and_error_boundary`:

- **Positives.** The bare, context + suffix and canceled + suffix forms all
  produce equal `compare_stderr` output.
- **Negatives.** Each of these stays distinct:
  - the first-hop URL
  - another host
  - `Get`
  - bare `request canceled`
  - no newline
  - CRLF
  - trailing extra line
  - the body-read suffix
  - a JSON-wrapped document
- **Scope.** The bare string stays untouched for `header-stall-text`,
  `header-stall-json`, `spans-refresh`, `p3-text-empty`, and for a copy of the
  case with `timeout_cause=False`.

`test_redirect_normalization_keeps_exit_request_and_duration_gates`:

- A bare reference with exit 1, two hops and 60 050 ms has no expectation
  issues.
- The bare reference against a suffixed candidate has no differences.
- Each of these produces an issue: 1 000 ms, 71 000 ms, exit 0, a single hop,
  reversed hops.

The pre-existing `test_bare_deadline_cause_is_limited_to_two_exact_text_messages`
is unchanged.

## Evidence check (from the manifest; hashes not recomputed)

- The manifest lists exact `source_directory` paths under
  `~/.cache/pb-phase7/retained-release1-phase3/…` and
  `~/.cache/pb-phase7/oracle-p3-redirect-deadline-2/…`. It maps each to an
  `evidence_directory` and gives per-file SHA-256 values.
- Original reference: committed `stderr.bin` is `c4e7b4a0…`, equal to the
  report's `stderr_raw_sha256`. `stderr.normalized` is `485884dd…`, equal to
  `stderr_sha256`.
- Original candidate: `stderr.bin` is `bdb3cbd5…` and `stderr.normalized` is
  `be929bb8…`. Both equal the embedded original report values.
- Equal normalized strings have equal recorded `.normalized` hashes:
  - `be929bb8…` for the candidate and `fresh-reference`
  - `6a366797…` for both canceled captures
- The original failed case is embedded verbatim with `status: fail` and its
  three issues, plus `original_report_sha256` `958b14f5…`.
- `scope` labels the work a process-free reduction with no capture or report
  changed and a final candidate replay still required.
- Each capture records `independent_expectation_issues: []`,
  `differences_from_original_reference: []` and the three
  additional-assertion booleans. These are recorded results. I did not
  re-derive them.
- The fresh oracle report: reference binary `sha256` is `fae8411f…`, matching
  the pinned identity. `complete: true`, `partial: true` (single `--case` run),
  `reference_runs: 2`, case `status: pass`, `issues: []`, wall 60 053.66 ms.
  This agrees with the manifest's `fresh-reference` wall time.

## Findings

**R2-F1 (low, provenance, non-blocking): two report paths are missing from the
manifest.** The manifest records the original report's hash, but not its path.
The path can only be inferred from the parent of `source_directory`. The fresh
oracle `report.json` is not listed by path or hash, even though both fresh
captures come from it. Add both paths, and the fresh report hash, so that a
reader does not have to guess. (The brief asked me not to guess, and I had to
infer the original path.)

**R2-F2 (low, evidence wording, non-blocking): the fresh Go controls did not
exercise the new branch.** Both fresh processes emitted suffixed forms, so they
would have passed without the new rule. They:

- confirm that the race is nondeterministic and that the other two forms
  appear
- show that the existing gates still pass on real 60-second Go runs

The bare branch itself still rests on one observed capture plus the source
analysis. That is sufficient, given the source confirmation above. The
validation paragraph's "Two fresh Go processes passed" is true, but it should
add "neither was bare" so that a reader does not count them as bare-branch
replications.

**R2-F3 (low, harness identity, unverified): the fresh report's runner hash
does not match the manifest's source hash.** The fresh report records
`runner_sha256` `f8007352…`. The manifest's `source_sha256` for
`harness/phase3.py` is `39d16e2b…`. I could not determine whether
`runner_sha256` covers a different input set or whether the fresh run used an
earlier harness revision. Because the reduction re-evaluated the fresh captures
process-free, the conclusion does not depend on this. I also could not confirm
that `39d16e2b…` is the current working-tree file, or that the test log ran
that revision. Record the runner and source identity used for the reduction and
the test run, or note that they differ intentionally.

**R2-F4 (info, test coverage, non-blocking): some round-1 test items are
absent from the new tests.** None is needed for safety:

- `manifest_entry` digest equality (it follows from `compare_stderr` equality,
  line 1547)
- an unexpected-request negative and differential body or config mutations
  (both enforced by unchanged code)
- bare-string negatives for the two bare-body cases (excluded by the
  exact-name guard)

Adding a one-line `manifest_entry` equality assertion would be cheap.

Round-1 F1–F4 are resolved as described in the proposal:

- **F1:** the wording is corrected, with no corpus change.
- **F2:** the masking is recorded explicitly.
- **F3:** sibling cases are named and remain unchanged.
- **F4:** the manifest covers both Go captures from the original campaign.

## Recommendation

**Accept** promotion of this narrow shared-harness normalization. The rule is an
exact conjunction of case, flag and whole string, grounded in pinned Go 1.25.4
source and observed Go outputs. Exit, request, body and header, and wall-window
gates are unaffected, and no broad suffix or category normalization is
introduced.

R2-F1 to R2-F3 are documentation and provenance fixes and do not block. This is
not phase acceptance: the final candidate retained replay is still required, and
the original 683-pass / 1-fail report must remain distinct from this reduction.

Nothing in this review was executed. Every conclusion comes from reading source,
the manifest, the fresh report and the test log.
