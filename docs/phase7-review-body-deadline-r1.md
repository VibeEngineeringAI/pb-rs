# Phase 7 review: retained body-deadline normalization proposal (round 1)

Scope: a fresh, independent, focused review of the **unpromoted**
[body-deadline proposal](phase7-body-deadline-proposal.md). The proposal adds one
exact `bare_context` entry, `p3-deadline-body-stall-text` →
`b"failed to stream response body"`, to `harness/phase3.py:normalize_timeout`.

This review does not accept the phase. It does not cover any other residual, the
phase6 companion's captures, a shared-runner handshake or a final successor
replay. The earlier redirect-deadline acceptance
([r1](phase7-review-retained-deadline-r1.md), [r2](phase7-review-retained-deadline-r2.md))
covers a different Go branch. I did not use it as approval for this case. I
re-derived everything below from source.

Pins: base `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`, Go
`07af390ab09bda8ac9e427ffc6310077bace0125`, go1.25.4.

## Verdict

**Source approval of the narrow rule, conditional on one blocking change.**

The rule itself is correct, exactly scoped and grounded in pinned Go source.
**The proposed one-line patch is not the complete shared-source delta, though.**
It contradicts an explicit negative assertion in the existing shared test suite
(F1). The exact patch as written would make
`DeadlineTierTests.test_bare_deadline_cause_is_limited_to_two_exact_text_messages`
fail. Do not promote it until the test delta in F1 is part of the hash-bound
patch, has been executed and has been reviewed.

## Method and limits

- **Shell.** My one permitted shell attempt failed with EDQUOT in
  `/tmp/claude-1000/…` before any command ran. I made no further attempts and
  used no workaround. As a result:
  - I ran **no** tests, builds, Go or Rust processes, CLI sessions, profilers or
    benchmarks;
  - I did not run the reduction script;
  - I computed **no** SHA-256 values.
- **Evidence labels.**
  - **[source]**: my own reading of the files listed below, plus causal
    reasoning.
  - **[read]**: a value I read directly from a retained artifact or report.
  - **[consistent]**: the same hash value is recorded identically in two or more
    independent records. I did not recompute it.
  - **[supplied]**: a parent claim that I did not reproduce.
  - **[unknown]**: not established.
- **Writes.** I wrote this file and one pointer,
  `/home/ajs/.cache/pb-phase7/review-body-deadline-r1/review-pointer.json`,
  which holds the verdict summary only. I made no source, harness, test or
  other-worktree edits.

### Files read

| Area | File and lines |
| --- | --- |
| Proposal and evidence (all in full) | `docs/phase7-body-deadline-proposal.md` |
| | `docs/evidence/phase7/p3-body-deadline-original.json` |
| | `docs/evidence/phase7/p3-body-deadline-proposed-reduction.json` |
| | `docs/evidence/phase7/p3-body-deadline-reduction.py` |
| | `/home/ajs/.cache/pb-phase7/p3-body-proposed-reduction-all/proposed-source.patch` |
| Prior reviews (all in full) | `docs/phase7-retained-deadline-proposal.md` |
| | `docs/phase7-review-retained-deadline-r1.md` |
| | `docs/phase7-review-retained-deadline-r2.md` |
| Prior provenance (header only) | `docs/evidence/phase7/p3-redirect-deadline/reduction-manifest.json`, lines 1–60 |
| Harness (live, unpatched) | `harness/phase3.py`: `TIMEOUT_CAUSE` (90–93), `run_case` tail (1420–1470), `normalize_timeout` (1473–1490), `compare_stderr` (1493–1504), `public`/`manifest_entry` (1516–1549), `expectation_issues` (1556–1628), `differences` (1642–1697), `add()` with full60s defaults (1813–1838), the deadline corpus block (2486–2519), `manifest_issues` (2877–2884), `run_cases`, including the reference-repeat A/A path (2887–3022), `stored_issues`/`load_stored` (3052–3098) |
| Harness tests | `harness/test_phase3.py`: `by_name` (25–26), `DeadlineTierTests` (340–428) |
| Pinned Go client (`~/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64/src/net/http/`) | `client.go`: `send` (225–302), `knownRoundTripperImpl` (315–340), `setRequestCancel` (342–415), the `Client.do` send-error path (726–736), `cancelTimerBody` (962–990) |
| Pinned Go transport (same directory) | `transport.go`: `roundTrip` (581–741), `awaitLegacyCancel` (743–749), `readLoop` body handling (2357–2417) |
| pb Go source (worktree copies) | `pkg/http/http.go` (all), `pkg/http/refresh.go` (all), `cmd/query.go`: `fetchData` and its stream helpers (706–910). I did not verify that these files are at `07af390` (no git). |
| Retained artifacts | `retained-release4-phase3/report.json` header (1–56) |
| | `…/cases/p3-deadline-body-stall-text/reference/result.json` (1–75) and `stderr.normalized` |
| | `oracle-p3-body-deadline-1/report.json` (1–220) |
| | `…/reference-repeat-1/result.json` (1–60) and `stderr.normalized` |

## Source rationale: independently confirmed [source]

**The wrapper reaches the slow path.**

- `pkg/http/http.go:46-60` builds every client as
  `http.Client{Timeout: 60s, Transport: &cloudRefreshTransport{…}}`.
- `refresh.go:32-35` returns the base response and error unchanged for a
  non-401, non-refresh request. That covers basic auth with status 200. The
  wrapper does not wrap `resp.Body` and has no `CancelRequest`.
- `client.go:320-340`: `knownRoundTripperImpl` returns false for
  `*cmd.cloudRefreshTransport`.
- `setRequestCancel` therefore skips the fast path (358-368) and installs both:
  - `context.WithDeadline(oldCtx, deadline)` (373)
  - a timer goroutine that runs `timedOut.Store(true)` and then `doCancel()`,
    which does `close(req.Cancel)` (398-408)

  It returns `timedOut.Load` as `didTimeout` (414). With a known transport,
  `didTimeout` would instead be `time.Now().After(deadline)` (367), and the bare
  form would essentially never appear. **The wrapper is the precondition for
  this race.**

**The body-read branch, which is distinct from the header branch.**

- `client.go:294-299`: once `RoundTrip` succeeds, `send` replaces `resp.Body`
  with `cancelTimerBody{reqDidTimeout: didTimeout}`.
- `cancelTimerBody.Read` (972-984) appends
  ` (Client.Timeout or context cancellation while reading body)` only when the
  underlying read fails with a non-EOF error **and** `reqDidTimeout()` is true at
  that moment.
- The header branch is different. `Client.do` (729-735) appends
  ` (Client.Timeout exceeded while awaiting headers)` to a `send` error and
  wraps it in `url.Error`. pb surfaces that branch as
  `request execution failed: Post "…": …` (`cmd/query.go:725-728`).
- The body branch has no `url.Error` and no `Post "url":` prefix. That matches
  all five captures.

**The underlying error text.**

- `transport.go:646` derives the transport context with
  `WithCancelCause(req.Context())`, and that context inherits the client
  deadline.
- `awaitLegacyCancel` (743-749) converts `close(req.Cancel)` into
  `cancel(errRequestCanceled)`.
- In `readLoop`, `<-rc.treq.ctx.Done()` calls
  `pc.cancelRequest(context.Cause(ctx))` (2408-2410). The body's
  `bodyEOFSignal.fn` returns `pc.canceled()` for a non-EOF read error
  (2366-2377).
- The body read therefore fails with exactly one of:
  - `context deadline exceeded`, when the deadline cause wins
  - `net/http: request canceled`, when the timer's `doCancel` wins

**The pb branch.**

- The text path is: `bufio.NewReader(resp.Body)` (745) →
  `streamSQLTextResponse`.
- `readResponsePrefix` reads `[`. `consumeEmptyJSONArray` reads `{` and
  returns not-empty.
- Any read error *during* those steps is reported as
  `failed to read response body: %w` (771-772, 781-783).
- After those steps, `io.Copy` copies the buffered 34 bytes and then blocks.
  Its error is `failed to stream response body: %w` (793-794).
- No trailing newline is printed on the error path, so stdout is exactly the
  34 delivered bytes.
- JSON mode reports the error as `error decoding JSON response: %w` (811-812).

Reachable outcomes at the single 60 s deadline:

| Which wins | Whole stderr | Go observed |
| --- | --- | --- |
| ctx cause, before `timedOut.Store` | `Error: failed to stream response body: context deadline exceeded\n` | yes: original release4 reference [read] |
| ctx cause, after the store | `… context deadline exceeded (Client.Timeout or context cancellation while reading body)\n` | yes: fresh `reference-repeat-1` [read] |
| `req.Cancel` | `… net/http: request canceled (Client.Timeout or context cancellation while reading body)\n` | yes: original `reference-repeat-1` and fresh `reference` [supplied]/[consistent] |
| bare `request canceled` | unreachable: the store happens before `doCancel` (406-408) | none |

**No independent earlier context.**

- `NewRequest` uses `http.NewRequest`, so the request has a background context.
  No other deadline exists.
- An `os.ErrDeadlineExceeded` from a socket deadline would read `i/o timeout`,
  not `context deadline exceeded`.
- Timing in the bare reference [read]: the request arrived at 38.85 ms, the 34
  body bytes and the stall at about 39.03 ms, `client-gone` at 60 040.45 ms,
  stderr at 60 038.47 ms, and wall time 60 040.95 ms. In the fresh Go reference
  [read], `client-gone` was at 60 026.95 ms and wall time 60 027.68 ms.

Both runs are consistent with a single 60 s `Client.Timeout`, armed at `Do` and
spanning the body read.

**Conclusion: the proposal's causal account is correct.** It is a real
body-branch counterpart of the redirect race. It also has direct in-tree
precedent: the existing `p3-deadline-not-reset-per-chunk` entry maps the same
`failed to stream response body` text through the same `io.Copy` and
`cancelTimerBody` path.

## Rule analysis [source]

**Reach.** The entry is keyed on the exact case name. `timeout_cause` is forced
true for every full60s case (`add()`, 1832-1834), so the flag does not
distinguish between those cases. The exact name and the whole-string equality
are the only real guards, and both are exact:

- `data == b"Error: failed to stream response body: context deadline exceeded\n"`
  (1481)
- There is no regex, prefix, substring, optional suffix or category relaxation.

On a match, the branch returns the canonical
`<CLIENT-TIMEOUT> (Client.Timeout or context cancellation while reading body)\n`.
That string is byte-identical to the case's `stderr_exact` pin (2499-2500).

**Placement.**

- `compare_stderr` and `expectation_issues` both call `normalize_timeout` after
  the optional `CLIENT_PORT` substitution. This case has no `client_port`,
  spinner, PTY or unordered-lines option.
- The rule therefore applies identically to:
  - the independent Go expectation (1588)
  - the A/A `reference unstable` check (`run_cases` 2972-2974 →
    `differences`)
  - the candidate differential (1667)

**The manifest.** The retained release4 report records
`frozen_tiers: [gate, known-divergence]` and `unfrozen_tiers_run: [full60s]`
[read]. `manifest_issues` therefore returns `[]` for this case (2878-2879), and
no frozen digest is affected. `manifest_entry` stderr digests for the three
forms would coincide in any case.

**Other cases.** The new entry cannot fire for any other name. The bare JSON
form cannot equal an `Error: …\n` text line. The redirect branch and the two
existing entries are untouched.

**Masking.** The following table compares the brief's requirements with what
still gates.

| Property | Independent expectation (Go) | Differential | Affected by the rule? |
| --- | --- | --- | --- |
| Exit 1 | `termination` (1561) | 1652 | no |
| Exact 34-byte partial stdout | `stdout_exact` (1593) | `stdout_normalized_sha256` (1662) | no |
| One `POST /api/v1/query`, no unexpected request | from `trace` (1563-1567) | full `requests` contract (1675) | no |
| Request body and contract headers | **not pinned** (the case has no `request_bodies` or `request_headers`) | `requests` includes `body_b64` and headers | no |
| Configuration | **not pinned** (no `config`) | semantic `config_before/after` (1677-1679) | no |
| Deadline window of 59–70 s | `min_wall_ms`/`max_wall_ms` (1616-1622) | candidate floor and ceiling (1681-1686) | no |
| Error class and branch | `stderr_exact` after normalization | `compare_stderr` | only the exact bare string in this one case |

The rule does not create timeout evidence. The suffix is synthetic. Timeout
evidence comes from:

- the context-deadline cause text (no other context exists)
- the stream-phase message, which proves the 34 bytes were consumed first
- exit 1
- the single-request graph
- the 59–70 s window

The proposal states this correctly (lines 34-38). It also correctly says body,
header and configuration are differential-only.

## Evidence check

- **Original failure [read]/[consistent].**
  - The release4 report records:
    - `runner_sha256` `39d16e2b…`
    - corpus `b83d5abd…`
    - reference `fae8411f…`
    - candidate `fd1f91b6…`
    - `reference_runs: 2`
    - `partial: false`
  - The bare `stderr.normalized` reads exactly
    `Error: failed to stream response body: context deadline exceeded` plus a
    line break. I could not confirm the newline byte.
  - `stderr_sha256 == stderr_raw_sha256 == 4ac82e6f…`. That matches both
    evidence JSON files.
  - All 27 artifact hashes are listed identically in the original and reduction
    records. The report hash `a4439320…` is recorded identically in both.
- **Fresh Go controls [read].**
  - `oracle-p3-body-deadline-1/report.json` records:
    - `runner_sha256` `39d16e2b…`
    - mode `reference-check`
    - `partial: true`
    - `reference_runs: 2`
    - reference `fae8411f…`
    - the same corpus hash
    - case status `pass` with `issues: []`
    - reference wall 60 027.68 ms
    - stderr hash `9eb8dc6b…`, the canceled form, equal to the original repeat
  - The fresh `reference-repeat-1` shows:
    - wall 60 032.10 ms
    - exit 1
    - 34-byte stdout `15ce0660…`
    - the same request, body and headers
    - stderr text that reads as the context-plus-suffix form, hash `21994d8e…`,
      equal to the release4 candidate
  - These are two Go processes in one unpatched-runner session, and the session
    passed without the rule. **Neither fresh run was bare**, so the bare branch
    still rests on one observed capture plus the source analysis above. That is
    sufficient here, because the source mechanism is confirmed. It is not
    replication.
- **Runner identity.** `phase3.py:2934` defines `runner_sha256` as the SHA-256
  of `phase3.py` itself. Both reports (`39d16e2b…`) therefore equal the
  reduction's `source_sha256_unchanged`. Both campaigns and the reduction used
  the same unpatched source [consistent; not recomputed]. This also settles the
  question raised as R2-F3 in the earlier review.
- **Reduction [supplied].** I read the script and the record but ran neither.
  The script:
  - execs the patched text into a separate module
  - asserts the corpus is equal
  - asserts that the five stored captures have no expectation issues and no
    differences against the first (bare) capture
  - asserts that the 25 mutation controls each raise issues
  - re-hashes all artifacts and the source after the run

  The recorded rejection-control issue strings are self-evidencing. The
  `proposed_issues: []`, `proposed_differences: []` and the negative-check
  count are hard-coded or incremented values, and they rely on `assert` (see
  F4). The trace-versus-requests correction described in the proposal is
  plausible: `expectation_issues` reads requests from `trace` (1563), not from
  the normalized `requests`. I cannot verify the conversation history.
- **Body and header identity.** Every role I read, plus the original record,
  shows the same 98-byte body. It decodes to
  `{"query":"SELECT 1 LIMIT 500","startTime":"2024-01-01T00:00:00Z","endTime":"2024-01-01T00:01:00Z"}`,
  which I counted by hand at 98 bytes. All of them also show the same four
  contract headers.

## Findings

### F1 (medium, blocking): the exact patch breaks an existing explicit negative test, so the real delta is larger than one entry

**Lines:** `harness/test_phase3.py:356-369`, specifically 368-369, and
`harness/phase3.py:1477-1480`.

The existing test does the following:

1. It loops over `context` values, including `b"failed to stream response body"`
   (the `not-reset-per-chunk` iteration).
2. It builds
   `bare = b"Error: failed to stream response body: context deadline exceeded\n"`.
3. It asserts that
   `compare_stderr(by_name()["p3-deadline-body-stall-text"], bare) == bare`.

With the proposed entry, `compare_stderr` returns the canonical
`<CLIENT-TIMEOUT>` line instead, so the assertion fails. The test's name,
"limited to two exact text messages", also becomes false. I derived this from
source; I did not execute it.

**Trigger:** applying `proposed-source.patch` and running the shared phase3
suite.

The process-free reduction could not reveal this, because it never imported
`test_phase3.py`. The proposal says "one additional exact case name" and does
not mention the test change.

This is a deliberate reversal of an earlier explicit scope guard, which listed
`body-stall-text` as must-not-normalize. It has to be in the reviewed,
hash-bound patch, not left as an incidental fix. The required shape is:

- Keep `p3-deadline-body-stall-text` as a negative for the
  `failed to read error response body` bare string.
- Remove it from the negatives **only** for the `failed to stream response body`
  bare string.
- Keep `p3-deadline-body-stall-json` and `p3-text-empty` as negatives for both
  bare strings.
- Add a focused test for the new case:
  - **Positives:** all three Go forms give equal `compare_stderr` output, and
    equal `manifest_entry` stderr digests.
  - **Negatives (each stays non-canonical):**
    - bare `request canceled`
    - no newline
    - CRLF
    - a trailing extra line
    - a preceding line
    - `failed to read response body: context deadline exceeded`, the
      prefix-phase sibling
    - `failed to read error response body: …`
    - the header-branch
      `request execution failed: Post "http://<FIXTURE>/api/v1/query": context deadline exceeded`
    - the bare form plus the awaiting-headers suffix
    - a JSON-wrapped document
    - `timeout_cause` set to False
  - **Gate controls:**
    - Through `expectation_issues`: exit 0, an empty `trace`, a reordered or
      extra request, an unexpected request, empty or altered stdout, and walls
      of 58 000 and 71 000 ms must each produce an issue.
    - Through `differences`: a changed candidate `body_b64`, a changed contract
      header and a changed `config_after` must each produce an issue.
    - Through `expectation_issues`: a stderr of
      `failed to read response body: context deadline exceeded` must produce an
      issue.
- Rename the existing test, for example to `…_three_exact_text_cases`. Do not
  delete any other assertion.

### F2 (low, record explicitly): the rule is symmetric, and text-mode body-suffix coverage of the candidate drops to zero in phase3

**Lines:** `phase3.py:1477-1483, 1498, 1588`.

The rule also accepts a **candidate** bare form. That is a Go-valid outcome, as
the brief intends and consistent with the redirect F2 doctrine. It is not a
promise that Rust always emits the suffix. Release4 emitted the
context-plus-suffix form.

After this change, no phase3 text-mode body-read deadline case independently
requires the body-read suffix:

- `not-reset-per-chunk`: bare allowed
- `preview-needs-one-more-byte`: bare allowed, and `stderr_has` is
  prefix-only
- `text-empty-array-lookahead`: `stderr_has` is prefix-only
- `body-stall-text`: bare allowed

A Rust change that drops the suffix in text mode would therefore pass phase3.
Only `p3-deadline-body-stall-json` (`stderr_json`) still requires the suffix,
and only on the JSON path.

This is acceptable, but the proposal should say so. Optionally, keep a
candidate-only assertion outside the Go-parity gate (a Rust unit test or a
retained-replay note) that the release build emits one of the two suffixed
forms.

### F3 (low): name the sibling cases that share the race

**Lines:** `phase3.py:2495-2504`.

`p3-deadline-body-stall-json` goes through the same `cancelTimerBody` path:
`json.Decoder` → `error decoding JSON response: %w`. A bare Go run there would
fail its `stderr_json` pin. `deadline-text-empty-array-lookahead` is tolerant
because it only uses `stderr_has` on a prefix.

Staying narrow is correct. Do not add a JSON rule without observed bare JSON
evidence. The proposal should still list these siblings, so that a future bare
observation is recognized as this known race and handled with its own
evidence.

### F4 (low, provenance): the reduction record is not fully self-evidencing

**Lines:** `p3-body-deadline-reduction.py:33-49, 65-66, 74, 80`.

- **Hard-coded results.** `proposed_issues` and `proposed_differences` are
  written as literal `[]`, justified only by preceding `assert` statements.
  `negative_checks` is incremented unconditionally. Under `python -O`, every
  `assert` is stripped and the record would look identical. Neither the record
  nor the evidence states the interpreter flags.
- **Weak negative checks.** The ten negative checks compare patched output with
  unpatched output. They do not assert that the output differs from the
  canonical line. They hold here only because the unpatched function returns
  those inputs unchanged.
- **Checks not listed.** The ten checks are not enumerated in the JSON.
- **Vacuous pair.** The `differences` pairing uses the first (bare) capture as
  the reference for all five. Its self-pair is vacuous. The pairs that matter
  are covered: bare reference against the release4 candidate (the real gate
  pair), and bare reference against the canceled repeat (the real A/A pair).

This does not change the conclusion. The binding evidence should come from the
must-run shared unit tests (F1), which use `unittest` assertions, with a
source-stamped log. A future reduction should record the computed lists and
`sys.flags.optimize`.

### F5 (low, provenance)

1. `p3-body-deadline-original.json` does not embed the original report's
   failing case entry: `status` and the `reference:`, `reference unstable:` and
   candidate issue strings. The redirect manifest did embed its entry. Embed
   it, or cite it by path and hash, without editing the original record.
2. That record's `"fresh_two_go_runs": "… active; no result claimed"` is now
   stale. Leave it untouched as a snapshot. A reader should be pointed to the
   reduction record as the superseding source.
3. Record both reports' `runner_sha256` values (`39d16e2b…`) next to the source
   hash, so that the identity of the unpatched runner is explicit.

### F6 (info): wording

The brief calls the fresh controls "two actual Go full60 sessions". They are
**one** single-case `reference-check` session with two Go processes. The
proposal's own wording ("two fresh actual Go runs") is accurate. The proposal
should also state that neither fresh run was bare (compare the redirect R2-F2).

## Approval split

**Approved at source level, for narrow promotion:**

- the single exact `bare_context` entry
- the F1 test delta in the shape described above

There is no relaxation of corpus, expectation, URL, stage or error category.

**Must still happen before shared use. These are not satisfied by this
review:**

1. **Hash-bound patch.** A single patch containing the `phase3.py` entry and the
   F1 test changes. Record the source and test hashes before and after.
2. **Shared suite run.** Run the whole phase3 suite against that exact patch,
   with the fixture configured. Produce a source-stamped log in which the
   modified and the new tests pass, and no other test changes outcome.
   Executing in a parent's in-memory module does not substitute for this.
3. **Shared-runner handshake.** Coordinate with phase6. Applying the patch
   changes `runner_sha256`. Neither phase's in-flight retained replay may switch
   runners mid-campaign. The phase6 companion's two suffixed R6 outcomes remain
   [supplied].
4. **Final successor replay.** Run the retained replay on the final revision.
   Keep the original 683/1 release4 report and its 27 artifacts unchanged, and
   label any re-evaluation as a reduction.
5. **Release claims.** None of this establishes performance or release
   readiness. Linux-only functional durations are not timing results.

## Unknowns

- I did not compute any SHA-256: binaries, artifacts, reports, runner, patch
  (`2065d801…`) or reduction runner (`928119fe…`). Equal values across records
  are noted as [consistent] only.
- Whether the reduction actually ran without `-O`, and its printed output.
- The exact trailing-newline bytes of the stored `stderr` files. Their display
  shows a single line terminated by a line break.
- The release4 report's case entry for this case: its issue list and the
  repeat-run content. I read only the header and the per-role artifacts.
- That the worktree `pkg/http` and `cmd/query.go` files are at Go commit
  `07af390`.
- The phase6 companion's R6 captures.
- Whether `harness/test_phase3.py` has other assertions about the bare-context
  map outside lines 340-428. I did not read the whole file.

Nothing in this review was executed. Every conclusion comes from reading
source, artifacts and reports.
