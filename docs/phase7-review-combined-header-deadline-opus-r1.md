# Phase 7 combined retained header deadline: independent Opus review r1

Verdict: **scoped ACCEPT** of the exact off-tree proposal for
`p3-deadline-header-stall-text` only. It is a compatibility normalization
proposal, not a measurement, waiver, production fix, phase acceptance or
performance claim. Nothing here extends prior body/lookahead/P4/redirect
approvals or covers any other case.

## Bound targets

| Item | SHA-256 |
| --- | --- |
| Complete source/test patch `proposal.patch` | `387aedec115abd651f9764506672dfe0dbfa5dc5e356b5216dec3921809cebe6` |
| Source-only subset `source.patch` | `a038d91c459fdcb3a728d8892a85cca47f72f194d641e6801e51dc752c38057b` |
| Base runner / test (live tree, unchanged) | `33be7605b2e3ea44712a8118b8909b129d00ef3f46c7f4d3932a522351e2b7bd` / `ef1a47dc263d18ed94fac7577a7e2597699973e74a4b42d3cbdc598cca9fab59` |
| Proposed runner / test | `d13285950a2213faa6adebacffa037b7ad63327012acfa474b0657cc6499d5c0` / `ce2f56dbe41415fcc2a7bd6a2e4918cba30057d0efd968af44cebb8145fe06c7` |
| Proposal document | `8553f01c668bfac57a12853b2b8199b01a1ca2f97fd6c6e7e0440b362b1a6966` |
| Handoff / normal / `-O` evidence | `884dd50a4925a0eddf234d4236393f6da32c616ab579a9f2efb9f8ddb3ad4fcb` / `dadce794fa459d36c078621ec015b139dbfe55136da5bb1069f3119de77cad06` / `10c5d3268b0c4af0a16bc9cc57a0b85996aa8d97a65a003413a7e7b94e77dc9a` |
| Original failed report (immutable) | `2918475d79c127da2fa5473d3e32155ea2c0f84a4514a170688acae59d01ddb3` |
| Go reference binary `baselines/pb-go` | `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314` |

Base hashes bind the live **working tree** (which already carries uncommitted
changes relative to `HEAD`), not `HEAD`. A changed base needs a fresh patch.

## What I ran (own cache only)

All work was in `/home/ajs/.cache/pb-phase7/review-combined-header-deadline-opus-r1`,
under an audit-hook guard that blocks subprocess/socket/fork/exec/thread/dlopen
and any write or mutation outside that cache. Every run recorded zero violations.
No Go/Rust/helper/fixture/probe/compiler/build/lock/campaign or 60-second run occurred.
No live file was edited. Live runner/test and the original report hashes were
rechecked unchanged afterwards.

1. **Exact `git apply`.** I copied the live `harness/` and ran `git apply --check -v`
   then `git apply` for `proposal.patch`: clean, no offset or fuzz, producing exactly
   `d1328595…` / `ce2f56db…`. `source.patch` alone applied cleanly to a copy of the
   live runner to the same `d1328595…`, and the reverse check succeeds. This adds the
   `git apply` result the author explicitly did not claim.
2. **Focused tests, normal and `python3 -I -B -O`.** The `HeaderStallTextDeadlineTests`,
   `DeadlineTierTests` and `ComparisonTests` classes give exactly 19 tests, all passing
   in both modes. Against the base runner with only the test hunk applied, both modes
   give exactly **18 assertion failures, 0 errors, 0 skips**, matching the handoff.
   Those failures cover both acceptance (all three forms) and tightening (each foreign
   stream, plus the shared `+x\n` cross-cause pair the legacy regex collapsed).
3. **Independent capture reduction.** I did not reuse the author's snapshots. Using
   `load_stored` I read the 9 original capture directories in place (release4/5 phase3,
   release6 combined) and checked:
   - Stored file hashes match `result.json`.
   - Raw/normalized lengths: Go `153/147` canceled+suffix (r4 ×2, r6 reference),
     `152/146` context+suffix (r5 ×2), `103/97` bare context (r6 repeat only).
     Rust `152/146` context+suffix ×3.
   - All 9 have exit 1, 0 stdout bytes, one non-unexpected `POST /api/v1/query`,
     zero wire/body bytes sent (true header stall), config before == after, and
     wall between 60003.95 and 60060.60 ms.
   - The proposed `compare_stderr` gives the 137-byte canonical for every capture;
     the proposed `expectation_issues` is empty for all 9; all **72 directed
     nonself pairs** give no differences.
   - The base runner reproduces each report's issue list exactly, including r6's sole
     `reference unstable: stderr differs at byte 71: candidate 97 bytes, reference 137 bytes`,
     plus each `reference_manifest` entry. The proposed accumulation is empty.
     Canonical length is 137 and the shared prefix is 71 bytes.
   - r4's report used runner `39d16e2b…`, not the current base, but the current base
     still reproduces its empty list.
4. **Own mutants (8, distinct from the author's 15).** These were: rule body removed,
   early `return data` dropped (legacy fallback), widened `endswith`/`startswith` scope,
   canonical without LF, substring match, bare form dropped, canceled form dropped,
   and `Get`→`Post` rewrite. All 8 were killed by assertion failures with 0 errors in
   both modes. Mutant sources are kept under `mutants/`.
5. **Cross-case scope grid.** This was a pure check, not an archive sweep. It covered
   1044 phase3/4/5 cases × 8 streams × {as-is, forced `timeout_cause`}, 16704 checks
   per mode. Before/after differs **only** for `p3-deadline-header-stall-text`, with
   exactly the 6 expected deltas: bare context admitted, and foreign `Get …` suffixed
   and JSON reading-body streams tightened. The phase5 wrapper is unaffected. Bare
   `net/http: request canceled` stays unchanged.

The author's 324 captured-data controls and 29232/58464 grid were not repeated wholesale.
My items 3–5 are independent samples of the same properties.

## Source rationale (verified)

- **Oracle app source.** `git show 07af390ab09b…:` hashes equal the preserved oracle
  `cmd/query.go`, `pkg/http/http.go`, `refresh.go`, `main.go`, `go.mod` and `go.sum`.
  `DefaultClientWithTransport` sets `Timeout: 60s` and always wraps in
  `&cloudRefreshTransport{…}`. `RoundTrip` returns the base error unchanged on error.
  `NewRequest` uses `http.NewRequest`, so it has a background context and no
  `req.Cancel`. `query.go` wraps the error as `request execution failed: %w`.
- **Pinned toolchain.** In `toolchain@v0.0.1-go1.25.4`, `VERSION` is `go1.25.4` and the
  `client.go`/`transport.go` hashes match the doc. `knownRoundTripperImpl` does not
  recognize the wrapper, so `setRequestCancel` installs both `context.WithDeadline`
  and a timer goroutine. The goroutine runs `timedOut.Store(true)` **before**
  `doCancel()` → `close(cancel)`. That close reaches `awaitLegacyCancel` →
  `cancel(errRequestCanceled)`. Pending header wait reports `context.Cause`
  (`transport.go:2884`). The `client.go:731–733` suffix depends on `didTimeout()` at
  error time.
- **Reachable outcomes.** Context-first before the store gives bare context.
  Context-first after the store gives suffixed context. Legacy-first is always
  suffixed canceled, because the atomic store happens-before the close chain. Bare
  canceled is therefore unreachable, and the proposal correctly rejects it.
- This is the header send-error path, distinct from the `cancelTimerBody.Read`
  body/lookahead path. The P4 review (F8) had already flagged this sibling race as an
  out-of-scope possibility lacking a bare capture. The r6 bare capture supplies it.
  This review does not use the P4 approval.

## Gate structure (verified)

- **Independent gates are unchanged and still effective after normalization.**
  They are exit 1, `stdout_exact` empty, the exact request list
  `["POST /api/v1/query"]` with no unexpected request, exact canonical `stderr_exact`,
  inclusive 59000–70000 ms wall, and capture failure.
- **Differential-only fields.** Request body/headers/framing/listener and semantic
  config are still differential. Identical wrong values on both sides pass, which is
  pre-existing behavior and is tested and documented. No independent pin is added or
  removed.
- **Tightening.** For this case only, any stream that is not one of the three exact
  forms returns byte-unchanged before `TIMEOUT_CAUSE`. That includes wrong
  method/origin/path/scheme, body suffix, CR/LF variants, prefix/suffix noise, spinner,
  Usage, and the HTTP-500 wrapper. The comparator then sees raw differences, so it
  never fails open.

## Concrete objections / caveats (non-blocking)

1. **One bare observation.** Bare context is seen once in 6 Go processes and is not
   replicated. Admission rests on source reachability plus that one capture, not on
   frequency. Bare acceptance is symmetric: a Rust candidate that regressed to bare
   would pass this case. That is consistent with Go-reachable output but hides such a
   drift. The handoff records it, and root should keep it visible.
2. **Binary provenance.** `pb-go` hashes to `fae8411f…`, contains `go1.25.4`, and has
   no embedded VCS revision or `vcs.modified`. Source equality plus binary identity is
   not a reproducible-build proof. The caveat is stated correctly.
3. **Existing advisory.** All three reports carry the advisory
   `request header order/case differs`. The patch does not change it, and it is not
   part of this acceptance.
4. **Exact-string brittleness.** The rule depends on the exact Go 1.25.4 message text
   and the `<FIXTURE>` origin replacement. A toolchain or oracle bump needs re-review,
   not reuse of this approval.

## Scope of this acceptance

This review accepts only the exact patch `387aedec…`, with source subset `a038d91c…`,
applied to base `33be7605…`/`ef1a47dc…`, for `p3-deadline-header-stall-text`.

It does not cover the following:

- the JSON sibling, `p3-deadline-spans-refresh`, or any other case;
- live application while root's frozen retained driver runs;
- full-suite or native apply checks;
- a successor retained replay;
- phase, release or performance acceptance.

The original r6 report remains a recorded one-issue failure. Applying the patch
requires root's edit window, then any root-required native checks and replay.
