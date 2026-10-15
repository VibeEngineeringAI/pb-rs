# Phase 6 review: retained lookahead-deadline normalization proposal (Opus, round 1)

Scope: a fresh, independent review of the **unpromoted**
[lookahead-deadline proposal](phase6-lookahead-deadline-proposal.md) and its
[evidence index](evidence/phase6/lookahead-deadline-proposal.json). The proposal
adds one exact `bare_context` entry to `harness/phase3.py:normalize_timeout`:
`p3-deadline-text-empty-array-lookahead` → `b"failed to read response body"`. It
also adds two focused tests in `DeadlineTierTests`.

This review does not accept phase 6. It does not normalize any other error. It
does not review or approve the phase-7 `p3-deadline-body-stall-text` proposal.
I read the phase-7 r1 review only for cautions that apply to this patch. The
earlier redirect-only rule (`p3-deadline-spans-redirects`) covers a different Go
branch, and I did not use it as approval here.

## Verdict

**Source-level approval of the narrow rule and its two tests. Promotion is
conditional on F1 and F3 being folded into the combined shared patch.**

- The rule is correct and grounded in pinned Go 1.25.4 and `cmd/query.go`
  source.
- It is keyed to one case name and requires a whole-string byte match that
  includes the LF.
- It reverses no existing negative assertion, unlike phase-7 F1.
- Neither finding below changes the rule. F1 is a provenance defect in the
  hash-bound patch artifact. F3 is a test-strength gap.

**Do not apply the patch.** Shared source stays frozen while R6 phases 4–5 run.

## Method and limits

- **Shell unavailable.** Every Bash call failed with EDQUOT in
  `/tmp/claude-1000/…` before any command ran. I made three attempts (two
  trivial) and used no workaround. As a result:
  - I ran **no** Python, tests, reduction, mutants, native processes, Go,
    fixtures, meters, builds, timing or locks;
  - I computed **no** SHA-256 values;
  - I wrote no cache files beyond the pointer below.
- **Evidence labels.**
  - **[source]**: my own reading of the source plus causal reasoning.
  - **[read]**: read directly from a retained artifact or report.
  - **[derived]**: hand arithmetic or reasoning over [read] values.
  - **[consistent]**: the same value appears in two or more independent records.
    I did not recompute it.
  - **[supplied]**: claimed by the proposal and not reproduced.
- **Writes.** This file, and
  `/home/ajs/.cache/pb-phase6/lookahead-deadline-review-r1/review-pointer.json`,
  which holds the verdict summary only.

### Files read

| Area | Files |
| --- | --- |
| Proposal (all in full) | `docs/phase6-lookahead-deadline-proposal.md`, `docs/evidence/phase6/lookahead-deadline-proposal.json` |
| Proposal cache | `prepare.py`, `proposal.patch`, `evidence.json` (head and tail), `tests.log`, `disabled-rule.log`, `tests-preparation-error.log`, `phase3.proposed.py` (1470–1494), `test_phase3.proposed.py` (404–461) |
| Root harness (live, unpatched) | `harness/phase3.py`: 1–120 (`TIMEOUT_CAUSE` 92–93), 1440–1700 (`normalize_timeout`, `compare_stderr`, `public`, `manifest_entry`, `expectation_issues`, `differences`), `add()` 1810–1841, deadline corpus 2480–2524 |
| | `harness/test_phase3.py`: all 495 lines |
| Go client (`~/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64/src/net/http/`) | `client.go` 225–419 and 955–990; `transport.go` 640–651, 740–751 and 2355–2414 |
| pb Go source (worktree copies) | `cmd/query.go` 700–906; `pkg/http/http.go` 30–74; `pkg/http/refresh.go` 1–60 |
| R6 report | `retained-review-r6-full/phase3/report.json`: header 1–129, the case entry 233929–233959, manifest-entry section and counts 243700–243781 |
| Captures | R6 `reference/result.json`; `stderr.normalized` for R6 reference, repeat and candidate, and for R4/R5 reference and repeat |
| Caution only | phase-7 `docs/phase7-review-body-deadline-r1.md` |

## Provenance check

### Original failure [read]

- **Report header.** The R6 phase-3 report has:
  - `runner_sha256` `7f8deaf0…`
  - corpus `b83d5abd…`
  - reference `fae8411f…`
  - candidate `f1fb7a9e…`
  - `reference_runs: 2`, `partial: false`
  - full60s unfrozen (`frozen_tiers: [gate, known-divergence]`)
  - 675 gate + 9 full60s cases selected
  - `counts: {pass: 683, fail: 1}`
- **The failed case (line 233929).** `p3-deadline-text-empty-array-lookahead`,
  `status: fail`, with exactly two issues:
  - `reference unstable: stderr differs at byte 37: candidate 114 bytes, reference 63 bytes`
  - `stderr differs at byte 37: candidate 114 bytes, reference 63 bytes`

  It has one advisory, `request header order/case differs`, which does not gate.
  No exit, stdout, request, configuration or wall-time issue is present.

### Independent byte check of the issue strings [derived]

- `Error: failed to read response body: ` is exactly 37 bytes (7 + 28 + 2).
- **Bare form.** Adding `context deadline exceeded` (25) and one terminator
  gives **63** bytes. A CRLF or a missing LF would give 64 or 62. So the R6 Go
  reference stderr is exactly the LF-terminated bare string that the rule
  matches.
- **Suffixed forms.** Each normalizes through `TIMEOUT_CAUSE` to 37 +
  `<CLIENT-TIMEOUT>` (16) + ` (Client.Timeout or context cancellation while reading body)`
  (60) + LF = **114** bytes. That matches both the A/A repeat (canceled form)
  and the candidate (context form).
- **Effect of the rule.** It maps the 63-byte string to that same 114-byte
  canonical. Both reported issues disappear, and nothing else in the case
  changes.

This check comes from the original report alone, independent of the
proposal's reduction.

### Capture text [read]

| Capture | Text |
| --- | --- |
| R6 reference | bare `context deadline exceeded` |
| R6 repeat | canceled + suffix |
| R6 candidate | context + suffix |
| R4 and R5 reference | canceled + suffix |
| R4 and R5 repeat | context + suffix |

- That is six Go captures. Bare appears once, in R6 only. Because the bare
  branch rests on one observation plus the source mechanism, this is not
  replication. The proposal's "six captures establish all three forms" is
  accurate.
- **R6 reference `result.json`** [read]:
  - exit 1, 0 stdout bytes
  - one `POST /api/v1/query` with a 98-byte body and four contract headers
  - `body_bytes_sent: 2`, stall at 29.39 ms, `client-gone` at 60 034.61 ms,
    `first_stderr_ms` 60 030.92
  - wall 60 057.99 ms

  This fits one 60 s `Client.Timeout` that fires after `[]` was consumed.

### Hash consistency [consistent; none recomputed]

- R6 `runner_sha256` equals the evidence input `harness/phase3.py` hash
  `7f8deaf0…`. The R6 run and the proposal's "before" source therefore claim
  the same file.
- The R6 manifest-section `stderr_sha256` (`8c11d31a…`) equals the evidence
  `stderr.bin` and `stderr.normalized` hashes and `result.json`
  `stderr_sha256` / `stderr_raw_sha256`.
- The R6 `config-after.toml` hash `20c1aae2…` equals `result.json`
  `raw_sha256`.
- The patch hash `1e393cfe…` matches the brief and both evidence copies.
- **Not verified:**
  - the three report hashes (`c5c4149e…`, `c4125274…`, `51183fb7…`)
  - the per-artifact hashes beyond those named above
  - the claim that root source and tests are unchanged

  By text, root `normalize_timeout` (1477–1480) lacks the entry, and root
  `DeadlineTierTests` lacks both new tests [read].

## Source rationale: independently confirmed [source]

**The wrapper forces the slow path.**

- `pkg/http/http.go:46-60` builds
  `http.Client{Timeout: 60s, Transport: &cloudRefreshTransport{…}}`.
- `refresh.go:32-35` returns the base response unchanged for a non-401 request,
  so the wrapper adds no body wrapping and no `CancelRequest`.
- `client.go:320-340`: `knownRoundTripperImpl` is false for this transport.
- `setRequestCancel` therefore skips the fast path (358–368) and installs both:
  - `context.WithDeadline(oldCtx, deadline)` (373)
  - a timer for the same deadline that runs `timedOut.Store(true)` and then
    `doCancel()` (398–408)

  It returns `timedOut.Load` as `didTimeout` (414).
- `NewRequest` uses `http.NewRequest` (`http.go:71`), so the background context
  holds no other deadline.

**The body branch.**

- `send` wraps a successful response in
  `cancelTimerBody{reqDidTimeout: didTimeout}` (294–300).
- `cancelTimerBody.Read` (972–984) appends
  ` (Client.Timeout or context cancellation while reading body)` only on a
  non-EOF read error, and only while `reqDidTimeout()` is true.

**The underlying error.**

- `transport.go:646` derives the context with `WithCancelCause(req.Context())`.
- `awaitLegacyCancel` (743–749) maps `close(req.Cancel)` to
  `cancel(errRequestCanceled)`.
- In `readLoop`, `<-ctx.Done()` → `pc.cancelRequest(context.Cause(ctx))`
  (2408–2410).
- `bodyEOFSignal.fn` returns `pc.canceled()` (2366–2377).

The read therefore fails with either `context deadline exceeded` or
`net/http: request canceled`.

**Which forms are reachable.**

| Which wins | Whole stderr | Reachable? |
| --- | --- | --- |
| Context deadline, before `timedOut.Store` | bare `context deadline exceeded` | yes |
| Context deadline, after `timedOut.Store` | context + suffix | yes |
| Timer's `doCancel` | canceled + suffix | yes |
| bare `request canceled` | — | **no**: `timedOut.Store` precedes `doCancel` (407–408) |

The test correctly keeps bare-canceled as a negative.

**The pb branch.**

- Text mode: `bufio.NewReader(resp.Body)` (745) → `streamSQLTextResponse`.
- With the fixture's `[]` followed by a stall:
  - `readResponsePrefix` returns `[`
  - `consumeEmptyJSONArray` reads `]`
  - its second loop (854–866) blocks on lookahead for EOF
- The read error returns from `cmd/query.go:783` as
  `failed to read response body: %w`. `bufio.ReadByte` passes the error through
  unwrapped. The stall comes before any stdout, which matches `stdout_exact=b""`.

**Note: line 772 has the same text.** `readResponsePrefix`'s error at 772 is the
same wrapper string. It is reachable only if the stall comes before the first
non-space byte, which this fixture never does. Both are `cancelTimerBody` reads,
so the mechanism is the same. The proposal cites only 783.

**Conclusion.** The causal account is correct. It is the same mechanism as the
existing `p3-deadline-preview-needs-one-more-byte` (`failed to read error
response body`) and `p3-deadline-not-reset-per-chunk` (`failed to stream
response body`) entries, applied to a third exact prefix.

## Rule and gate analysis [source]

### Reach

- `timeout_cause` is forced true for every full60s case (`add()`, 1832–1834).
  The opt-in therefore does not separate deadline cases from each other, but it
  does exclude every gate case.
- The real guards are the exact name key and whole-string equality
  (`phase3.py:1481`):
  - no regex, prefix, `startswith` or `rstrip`
  - the LF is required, and CRLF fails
- The canonical output keeps the context text. A bare string can therefore only
  collapse onto the suffixed spelling of the *same* message.

### Placement

`normalize_timeout` runs in three places:

- `expectation_issues`, at 1588
- the candidate differential, at 1667 via `compare_stderr`
- the A/A `reference unstable` comparison, which goes through `differences`

`manifest_entry` (1547) also hashes `compare_stderr`. The R6 bare reference's
full60s manifest-section digest would change from `8c11d31a…` to the canonical
digest. That tier is unfrozen, so nothing gates on it (info).

### What still gates

| Property | Independent Go expectation | Differential | Affected by the rule? |
| --- | --- | --- | --- |
| Exit 1 | `termination` (1561) | 1652 | no |
| Empty stdout | `stdout_exact=b""` (1593) | stdout digest (1662) | no |
| One `POST /api/v1/query`, nothing unexpected | from `trace` (1563–1567) | full `requests` (1675) | no |
| Request body and contract headers | **not pinned** (no `request_bodies` or `request_headers`) | `requests` (`body_b64` and headers) | no |
| Configuration | **not pinned** (no `config`) | semantic `config_before`/`config_after` (1677–1679) | no |
| 59–70 s window | `min_wall_ms`/`max_wall_ms` (1616–1622) | candidate floor and ceiling (1681–1686) | no |
| Stderr | `stderr_has` the substring `failed to read response body: ` only | `compare_stderr` equality | only the exact bare string in this case |

The independent stderr gate for this case was always substring-only. The rule
affects only the A/A and candidate differential.

### Labels in test 2

Test 2 labels its gates correctly:

- exit, wall, trace and stdout are exercised through `expectation_issues`
  (independent)
- headers, body and config are exercised through `differences`
  (differential-only)

Its request headers and body are synthetic (`x-test`, `eA==`). That is
acceptable because `differences` compares `requests` wholesale.

## Test and patch analysis

### Existing boundary test

`test_bare_deadline_cause_is_limited_to_two_exact_text_messages`
(`test_phase3.py:355-368`) never names the lookahead case. Its negatives are
`body-stall-text`, `body-stall-json` and `text-empty`, against the preview and
stream bare strings. The new entry cannot fire for those names, so **no
existing assertion is reversed**. This patch does not need a phase-7 F1-style
negative reversal. The test's *name*, however, becomes false (F2).

### Rest of `test_phase3.py`

I read all of it. No other test references this case, `bare_context` or
`normalize_timeout`. The other classes compare only gate cases, which return
early on the opt-in.

Running only `DeadlineTierTests` is therefore an adequate focused proxy for
this module. It does not replace the post-promotion whole-suite run that the
proposal already requires. Whether other harness modules import `phase3` and
assert on this case is **unknown**: I had no grep.

### Logs [read]

| Log | Content |
| --- | --- |
| `tests.log` | 8/8 OK |
| `disabled-rule.log` | both new tests FAIL (not ERROR) |
| `tests-preparation-error.log` | the bool-config `AttributeError` in `phase2.semantic`, consistent with the described setup error |

`disabled-rule.log` failure messages:

- **Test 1.** Bare stays bare versus the canonical.
- **Test 2.** `['stderr differs at byte 37: candidate 114 bytes, reference 63 bytes'] != []`.
  This is the same string as the R6 issue, which is a good sign of fidelity.

### Hand mutant analysis [derived; not executed]

Killed by the new tests:

| Mutant | Killed by |
| --- | --- |
| Key the entry under a different case, or change the value | positives |
| `startswith`, `in` or `rstrip(b"\r\n")` | the `+extra`, no-LF and CRLF negatives |
| Accept bare `request canceled` | negative 2 |
| Move the bare check above the opt-in return | the `timeout_cause=False` check |
| Make the rule case-agnostic | the `body-stall-text`/`json` and `spans-redirects` cross-case checks |

One mutant survives both new tests and the existing one. It pools the three
contexts across all `bare_context` cases (see F3).

## Findings

### F1 (medium; must be fixed in the promoted patch, not blocking source approval): the hash-bound patch's test hunk header is off by two

**Lines:** `proposal.patch:13` (`@@ -410,6 +410,53 @@`) and `prepare.py:60`.

**Repro, by reading:**

- In root `test_phase3.py`, the hunk's first context line
  (`wrong[field] = value`) is at **408** by `\n` counting. The Read tool shows
  408–410 as `wrong[field] = value`, `assertTrue`, blank, and 411 as
  `def test_paced…`.
- In `test_phase3.proposed.py`, the new `def` lines are at **412** and
  **433**, not 414 and 435.
- Python's compiler also counts by `\n`. The `disabled-rule.log` tracebacks
  report lines **419** and **443**. Those are exactly the proposed file's
  `assertEqual(compare_stderr(case, value), canonical)` and
  `assertEqual(differences(case, ref, cand), [])`.
- The `phase3.py` hunk (`-1475`) is correct.

**Cause:** `prepare.py` builds the diff with `str.splitlines(True)`. That also
splits on U+2028, U+2029, U+0085, `\x0b`, `\x0c` and `\x1c`–`\x1e`.

- The input string on `test_phase3.py:62` must contain a literal U+2028: the
  expected output is `\\u2028` after `<&>`.
- I could not locate the second separator without byte tools.
- Line content inside the hunk is unaffected. Outside the hunk, a split shifts
  only the numbering.

**Impact:**

- `git apply` and GNU `patch` would probably still apply the hunk at an offset.
- The reviewed, hash-bound artifact is nonetheless not an exact unified diff of
  the files it claims.
- The same generator emits a **malformed** patch if any hunk ever contains such
  a character. The phase-3 test file and corpus carry several.

**Fix:** regenerate the combined patch with `\n`-only splitting, for example
`text.split('\n')` with re-added terminators, or with
`git diff --no-index`. Record the new hash and confirm that applying the patch
reproduces both `*.proposed.py` byte-for-byte.

### F2 (low; fold into the combined patch): stale boundary-test name, and lookahead absent from its negatives

**Lines:** `test_phase3.py:355-368`.

- The test is named "limited to two exact text messages", but `bare_context`
  now has three entries. With phase-7's companion it would have four.
- Rename the test.
- Add `p3-deadline-text-empty-array-lookahead` to its `other` tuple. Under that
  case, the preview and stream bare strings must stay byte-identical, which
  holds by construction.
- **Coordinate with phase 7.** Its F1 asks for a rename to "three…" and a
  reversal of exactly the `body-stall-text` negative for the stream string. The
  combined patch needs one consistent name and both edits in one hash-bound
  diff.
- Nothing in this patch conflicts semantically with phase 7's required
  negatives. In particular, its required negative
  "`failed to read response body: context deadline exceeded` under
  `body-stall-text` stays non-canonical" still holds with this entry, because
  the key is the case name. Both patches, however, edit the same dict lines and
  insert before the same `test_paced…` anchor, so they will conflict textually.

### F3 (low; test strength): the negatives only check "≠ canonical", so a pooled-context mutant survives

**Lines:** `test_phase3.proposed.py:420-428`.

**Surviving mutant:** for any `bare_context` case, accept *any* of the three
bare strings and return that string's own canonical. Under the lookahead case,
`bare.replace(b"read response", b"stream response")` then becomes the
stream-canonical. That differs from the lookahead canonical, so `assertNotEqual`
passes. The cross-case loop never names the two sibling `bare_context` cases.

**Real-world impact is small.** The canonical keeps the context, so a
cross-message mismatch against a Go reference would still be caught. The
claimed property, though, is per-case binding, and the tests do not pin it.

**Cheap fix:**

- For the lookahead case, assert the identity `compare_stderr(case, bad) == bad`
  for the sibling strings.
- Add these negatives:
  - `failed to read error response body: …`
  - a preceding line (`b"x\n" + bare`)
  - the header-branch
    `request execution failed: Post "http://<FIXTURE>/api/v1/query": context deadline exceeded\n`
- Add `p3-deadline-preview-needs-one-more-byte` and
  `p3-deadline-not-reset-per-chunk` to the cross-case identity loop.

This is optional for source approval and recommended for the combined patch.

### F4 (low; record explicitly): candidate suffix fidelity for this case is no longer gated

The rule is symmetric. A Rust candidate that prints the bare form now passes
against any Go form. This case's independent stderr gate is substring-only
(`phase3.py:2503`), so nothing in phase 3 will still require the Rust text path
to emit the body-read suffix here. Phase-7 r1 F2 states the same doctrine.

This is Go-valid and acceptable. The proposal should say so. Optionally, keep a
candidate-only Rust assertion outside the Go-parity gate.

### F5 (low; provenance)

1. **Reduction rests on `assert`.** In `prepare.py:80-81`, the cleanliness of
   the nine-capture reduction depends on `assert` statements. The computed
   `expectation_issues` and `differences` lists and `sys.flags.optimize` are
   not recorded.
   - The unit-test logs are self-evidencing. The reduction is not.
   - Each capture is compared only against `r4/reference`, so the self-pair is
     vacuous. The gates are equality relations, which makes this transitively
     sufficient.
2. **Unscripted keys in the durable record.** The durable evidence JSON has
   three keys that the recorded script does not produce:
   `preparation_script_sha256`, `preparation_error_log_sha256` and
   `source_rationale`. The cache `evidence.json` lacks them. Their author step
   is unrecorded. The `source_rationale` paths for `cmd/query.go` and
   `pkg/http/*` are relative, with no root or commit pin.
3. **Misleading traceback source lines.** The traceback lines shown in all
   three logs come from the root file. The exec'd module's `__file__` points at
   `harness/test_phase3.py`, so `linecache` prints unrelated root lines, for
   example `differences(case, cand, ref)` from `test_paced…` and `recorded = …`
   from `BaselineTrustTests`. Only the line numbers and messages are
   trustworthy. Fix: set `__file__` to the cache's `*.proposed.py`.
4. **Partial process guard.** The guard covers `subprocess`, `fork`,
   `posix_spawn*`, `system` and `flock`/`lockf`, but not `os.exec*`,
   `os.spawn*`, `pty` or `multiprocessing`. The executed code is pure, so this
   is informational.

### F6 (info; wording)

- "R6 prints the context-deadline form with that suffix" means the R6 **Rust
  candidate**. Say so.
- Mention `cmd/query.go:772` alongside 783.
- State that bare was observed once in six Go captures.

## Disposition

**Approved at source level, scoped to this case only:**

- the single `bare_context` entry for `p3-deadline-text-empty-array-lookahead`
- the two tests as written

The proposal does not relax corpus, regex, timeout, request, output or
configuration rules.

**Before shared use. None of this is satisfied by this review:**

1. **One combined, regenerated patch** with phase 7's entry and its F1 test
   delta, plus F2 and, recommended, F3. Use `\n`-exact hunks (F1). Record
   before and after hashes for `phase3.py` and `test_phase3.py`.
2. **Whole phase-3 suite** run against that exact patch, with a source-stamped
   log. The new and modified tests must pass, and no other test may change
   outcome. Re-run the disabled-rule check.
3. **Source-bound reduction** of these nine captures and of phase 7's captures
   on the combined source. Record the computed lists and the optimize flag.
4. **Shared-runner handshake.** Promotion changes `runner_sha256`. Do not apply
   it while the R6 phases 4–5 continuation or any phase-7 retained replay uses
   the current runner.
5. **A focused actual candidate replay** after promotion, as the proposal
   itself requires. Keep the original R6 683/1 report and artifacts unchanged,
   and label any re-evaluation as a reduction.
6. **No other claims.** None of this is phase acceptance, performance or
   release evidence. The ~60 s durations are functional, not measurements.

## Unknowns

- Every SHA-256: patch, sources, logs, artifacts and reports.
- Whether the root source and tests are byte-unchanged.
- Whether the reduction and the tests ran without `-O`.
- The second `splitlines` separator in `test_phase3.py` (F1).
- That the worktree `cmd/query.go` and `pkg/http` files match pinned Go commit
  `07af390`.
- The R4/R5 report bodies (I read only their stderr captures).
- Other harness modules that may assert on this case.
