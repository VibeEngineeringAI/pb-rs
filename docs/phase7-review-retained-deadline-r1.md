# Phase 7 review: retained redirect-deadline normalization (round 1)

Scope: independent focused review of
[`phase7-retained-deadline-proposal.md`](phase7-retained-deadline-proposal.md)
(the exact-case, exact-whole-stderr suffix normalization for
`p3-deadline-spans-redirects`). This is **not** final phase acceptance. It does
not cover any other residual, the parent's fresh real60s Go probes, or a final
Rust replay.

## Method and limits

I only read source files. **I executed nothing.** No tests, builds, Go/Rust
processes, hashing or timing were run. Every shell invocation failed with EDQUOT
on the harness temp directory, and as instructed I did not work around that with
temp symlinks. As a result:

- I could not list `docs/evidence/phase7/p3-redirect-deadline/`. I read
  `reference/result.json`, `reference/stderr.normalized`,
  `candidate/result.json` and `candidate/stderr.normalized` by guessing their
  paths. `reference/stderr.bin` is binary, and the Read tool refused it.
- **I did not locate the second Go capture** (the `net/http: request canceled
  (...)` form) in that directory. Nine guessed paths did not exist. That
  observation therefore rests on the proposal text and the original phase-cache
  report, which I did not read.
- I did not verify any SHA-256 values: the executable identity `fae8411f…`, the
  `stderr_raw_sha256` values, or the trailing-newline bytes. The `.normalized`
  files display as one line, but I did not confirm the exact bytes.
- I did not read pinned Go `net/http/transport.go`. The claim that a cancelled
  context surfaces as `context deadline exceeded` while a closed `req.Cancel`
  surfaces as `net/http: request canceled` rests on the observed strings, not on
  my own reading of the source.

Files I read: `harness/phase3.py` (`TIMEOUT_CAUSE`, `normalize_timeout`,
`compare_stderr`, `expectation_issues`, `differences`, `manifest_entry`, the
`add()` full60s defaults, the deadline corpus block), `harness/test_phase3.py`
(`DeadlineTierTests` and fixtures), `pkg/http/http.go`, `pkg/http/refresh.go`,
and Go 1.25.4 `src/net/http/client.go` (`send`, `knownRoundTripperImpl`,
`setRequestCancel`, `Client.do`).

## Source rationale: confirmed

- `pkg/http/http.go:46-60`: every client is `http.Client{Timeout: 60s,
  Transport: &cloudRefreshTransport{...}}`. A basic-auth profile still goes
  through the wrapper. `refresh.go:32-36` passes the base `RoundTrip` error
  through unchanged when no refresh applies. The wrapper has no
  `CancelRequest` method.
- `client.go:320-340`: `knownRoundTripperImpl` returns false for
  `*cmd.cloudRefreshTransport`. `setRequestCancel` (`client.go:351-415`)
  therefore skips the fast path at lines 358-368 and installs **both** of these
  against the same `deadline`:
  - `context.WithDeadline(oldCtx, deadline)` (line 373)
  - a separate `time.NewTimer(time.Until(deadline))` goroutine that runs
    `timedOut.Store(true)` and then `doCancel()`, which closes `req.Cancel`
    (lines 398-412)

  `didTimeout` is `timedOut.Load`.
- `client.go:729-734`: the suffix ` (Client.Timeout exceeded while awaiting
  headers)` is appended only when `didTimeout()` is true at the moment
  `c.send` returns an error.
- `client.go:617-633, 659-668`: `deadline` is computed once per `Do`. The
  redirect hop is a fresh `Request` carrying `reqs[0]`'s original ctx and
  Cancel, so `send` installs a second ctx/timer pair against the same absolute
  deadline. The error URL is the current hop (`req.URL`, because `resp` is nil
  on a send error). That produces `/v2/api/v1/query`, which matches both
  captures.

Possible Go outcomes at that one deadline:

| Which fires first | Error string |
| --- | --- |
| ctx, before the timer stores true | `context deadline exceeded` with no suffix (**the observed retained Go run**) |
| ctx, after the store | `context deadline exceeded (Client.Timeout exceeded while awaiting headers)` (the Rust release1 form) |
| timer cancel | `net/http: request canceled (Client.Timeout exceeded while awaiting headers)` (the second Go run, as reported) |

A bare `net/http: request canceled` is **not** a reachable outcome, because the
store happens before `doCancel`. The proposal correctly excludes it.

The fixture CLI passes no other context, and `NewRequest` uses
`http.NewRequest`. The reference milestones show request 1 received at
35 030 ms and `client-gone` at 60 057 ms, with a wall time of 60 062 ms. The
timing is consistent with the 60 s client deadline and with no earlier
independent timeout. The proposal's causal explanation is sound.

The proposed rule has the same shape as the existing precedent in
`normalize_timeout` (`phase3.py:1477-1483`). That rule covers bare body-read
races for two named cases and is covered by
`test_bare_deadline_cause_is_limited_to_two_exact_text_messages`.

## Do the retained constraints actually protect against wrong errors?

They do partly. The proposal overstates which gates exist.
`deadline-spans-redirects` (`phase3.py:2504-2506`) pins only:

- `termination` exit 1
- `requests == [POST /api/v1/query, POST /v2/api/v1/query]` (method plus
  unquoted path) and no unexpected fixture request
- `stderr_has ["Client.Timeout exceeded while awaiting headers"]`
- the full60s defaults: wall time between 59 000 and 70 000 ms, and
  `timeout_cause`

The case has **no** `request_bodies`, `request_headers`, `stdout_exact`,
`stderr_exact` or `config` pins. In `expectation_issues`, the reference is
therefore not checked against the model for bodies, headers, empty stdout or
unchanged config. Those properties are compared only reference-vs-candidate in
`differences()` (stdout digest, full `requests` contract including `body_b64`
and headers, and semantic `config_before/after`). The proposal's statement that
"exact two request graph, request bodies/headers, empty stdout, unchanged config
… remain" is true only for the differential, not for the Go oracle check.

This matters more once the proposal lands. For a bare Go run, the only stderr
expectation (`stderr_has "Client.Timeout exceeded while awaiting headers"`)
would be satisfied **by the synthetic suffix itself**, which makes it
tautological. What then shows that the reference really hit the client timeout
on the second hop is:

1. The exact whole-string match, which includes the `/v2/api/v1/query` URL.
2. Exit 1.
3. The two-path graph.
4. The 59–70 s wall window.

Together these are adequate. Single-hop timeouts, first-hop failures, early or
independent contexts and late per-hop deadlines (about 95 s) all fail one of
them. But this protection should be stated precisely, not attributed to pins
that do not exist.

## Findings

**F1 (medium, accuracy/provenance): retained-gate claims are overstated.**
See the section above. Required: either correct the proposal's wording, or
(recommended) strengthen the case expectation in the same change:

- `stdout_exact=b""`
- `request_bodies` `{"0","1"}` set to the model `request_body("SELECT 1")` (307
  replays the body; both retained captures show the same 98-byte body)
- `config="unchanged"`
- ideally `stderr_exact` set to the canonical `<CLIENT-TIMEOUT> (Client.Timeout
  exceeded while awaiting headers)` text, replacing the now-tautological
  `stderr_has`

Pinning changes the full60s corpus hash, so any saved full60s baseline must be
re-captured. Because the parent is already running fresh Go probes, that cost is
low.

**F2 (low, over-normalization): the rule is symmetric, so a candidate's bare
output is also accepted.** `compare_stderr` and `expectation_issues` apply the
rule to both roles. A Rust regression that drops the suffix only on this path
would be masked. A bare string is a Go-valid outcome, so this is not a parity
violation. Release1 Rust is deterministic and emits the suffixed form. Optional:
keep a Rust-side or `differences()` assertion that the candidate's stderr is one
of the two suffixed forms. If you do not add it, record the masking explicitly.

**F3 (low, under-normalization; false failures, not false passes): other cases
share the same race.** All of these go through the same unknown-transport
`setRequestCancel` path and the same headers race:

- `p3-deadline-header-stall-text` (`stderr_exact`)
- `p3-deadline-header-stall-json` (`stderr_has` includes the suffix)
- `p3-deadline-spans-refresh` (`stderr_has "Client.Timeout"`)

A bare Go result on any of them would fail the reference check. Staying narrow
to the observed case matches the existing precedent and the "no global optional
suffix" constraint. I accept that choice, but the proposal should name these
siblings. A future bare observation there is then the same known race, and the
response is to add a per-case entry with its own evidence, not to invent a new
cause. JSON-mode bare forms would need a separate exact-JSON rule; none is
proposed, which is correct.

**F4 (low, provenance): evidence completeness is unverified.** From reads alone,
I confirmed the bare Go run and the Rust release1 run. I could not locate the
second Go capture in the committed evidence directory (see "Method and limits").
Before promotion:

- confirm both Go captures are present, or that the second is clearly
  referenced by path and hash into the phase cache
- confirm the original failed report hash is recorded alongside any reduction

**F5 (info): the rule is placed and ordered correctly.** It runs inside
`normalize_timeout` after `CLIENT_PORT` and before `TIMEOUT_CAUSE`. The case has
no spinner, PTY, `client_port` or `stderr_unordered_lines`, and no
`PB_DEBUG_HTTP_HEADERS`. The whole-string comparison therefore sees exactly the
URL-normalized single line. After the suffix is appended, `TIMEOUT_CAUSE`
collapses all three outcomes to the same `<CLIENT-TIMEOUT> (Client.Timeout
exceeded while awaiting headers)` bytes, so `manifest_entry` stderr digests
coincide too.

## Required tests before promotion

These go in `DeadlineTierTests`, alongside the existing bare-body test.

1. **Positive equivalence.** For `p3-deadline-spans-redirects`, these three
   inputs must produce equal `compare_stderr` output:
   - bare `Error: request execution failed: Post
     "http://<FIXTURE>/v2/api/v1/query": context deadline exceeded\n`
   - the ctx+suffix form
   - the canceled+suffix form

   `manifest_entry` stderr digests must also be equal.
2. **Negatives that must stay different from canonical:**
   - the first-hop URL `/api/v1/query`
   - `<ALIAS>`, or another host placeholder
   - a different method (`Get`)
   - bare `net/http: request canceled`
   - no trailing newline
   - CRLF
   - extra trailing text or a second line
   - a preceding debug or spinner line
   - a ` (Client.Timeout or context cancellation while reading body)` suffix
   - the JSON-mode error document
3. **Scope negatives.** The same bare string must stay untouched for:
   - `p3-deadline-header-stall-text`
   - `p3-deadline-spans-refresh`
   - both existing bare-body cases
   - a gate case (`p3-text-empty`)
   - a deep copy of the target case with `compare.timeout_cause` removed
4. **Existing tests stay unchanged and pass**, including
   `test_bare_deadline_cause_is_limited_to_two_exact_text_messages`. Its
   negative `bare + awaiting-headers` for the body cases must still hold.
5. **Expectation gates with fake results.** A bare reference with exit 1, the
   two-path trace and wall time 60 062 ms gives `expectation_issues == []`.
   Each of these must produce an issue:
   - wall time 30 000 ms
   - wall time 75 000 ms
   - exit 0
   - a single request
   - a reversed path order
   - an unexpected request
   - (if F1 is adopted) a changed body on hop 1, or non-empty stdout
6. **Differential.** `differences(case, bare_ref, rust_cand) == []` and
   `differences(case, canceled_ref, rust_cand) == []`. Changing the
   candidate's hop-1 `body_b64` or its config must produce an issue.
7. **Retained-artifact reduction.** This must be process-free. Re-evaluate the
   retained `reference/` and `candidate/` with the new harness, and verify that
   raw `stderr.bin` digests match the recorded `stderr_raw_sha256`. Record the
   harness file hashes, the original report hash and the case-contract hash.
   Write to a new directory and never overwrite the original report. Label it as
   a reduction, not a rerun.

## Recommendation

**Accept with changes.** The mechanism is real and correctly derived from the
pinned Go source. The bare form is a legitimate Go outcome at the same deadline.
The match is narrow (one exact case, exact full line, second-hop URL), and it
introduces no production change. Promote only after all of the following:

- F1 is corrected, preferably by adding the expectation pins
- F3 sibling cases are named, and the F2 masking is recorded or guarded
- F4 evidence completeness is confirmed
- tests 1–7 pass
- the parent's fresh real60s Go controls and a subsequent final Rust replay
  complete, as the proposal already requires

Nothing in this review was executed. All conclusions come from source and
artifact reads.
