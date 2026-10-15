# Phase 7 combined retained header deadline: off-tree proposal

Status: **author investigation and pure-Python validation only; independent review pending**.
No live harness, source, tests, corpus, contracts, retained report or capture was
edited. The active retained driver and phases 1–5 remain frozen. No commit,
Go/Rust/helper execution, build, fixture, socket, lock, performance run, full
suite or independent review was performed. Root owns the later fresh Opus review.

The complete source/test proposal and its LF-only source subset are in
[/home/ajs/.cache/pb-phase7/combined-header-deadline-proposal](/home/ajs/.cache/pb-phase7/combined-header-deadline-proposal). The owned
[normal evidence](evidence/phase7/combined-header-deadline-evidence.json),
[optimized evidence](evidence/phase7/combined-header-deadline-evidence-optimized.json)
and [handoff](evidence/phase7/combined-header-deadline-handoff.json) bind all inputs,
computed issue lists, exact captures, tests, mutants, hashes and limitations.
These are reductions of existing captures, not reruns or phase acceptance.

The original partial one-case report remains **one failure**, SHA-256
`2918475d79c127da2fa5473d3e32155ea2c0f84a4514a170688acae59d01ddb3`, at
`/home/ajs/.cache/pb-phase7/retained-release6-combined/p3-deadline-header-stall-text/report.json`.
Its sole issue is reproduced exactly:

```text
reference unstable: stderr differs at byte 71: candidate 97 bytes, reference 137 bytes
```

Here the comparator's “candidate” is the second **Go** process. The actual Rust
candidate matches the first Go process after the existing cause normalization.
The report is complete for its filtered case, has `partial: true`, and uses two
reference runs; this says nothing about completion of the active parent campaign.

The actual release6 captures have these whole origin-normalized stderr lines,
each terminated by exactly one LF:

```text
Go reference: Error: request execution failed: Post "http://<FIXTURE>/api/v1/query": net/http: request canceled (Client.Timeout exceeded while awaiting headers)
Go repeat: Error: request execution failed: Post "http://<FIXTURE>/api/v1/query": context deadline exceeded
Rust: Error: request execution failed: Post "http://<FIXTURE>/api/v1/query": context deadline exceeded (Client.Timeout exceeded while awaiting headers)
```

The raw streams retain the actual loopback origins/ports and are respectively
153, 103 and 152 bytes; the existing origin replacement yields 147, 97 and
146 bytes. Legacy cause normalization makes the two suffixed forms 137 bytes,
but leaves the bare repeat at 97. Their first differing byte is precisely the
end of the 71-byte normalized prefix. Original raw/normalized files, all bytes
and original reports are preserved both in place and in cache snapshots.

Go reference/repeat and Rust wall values are `60032.253448`, `60045.631052`
and `60004.630402` ms. All exit 1, have no capture failure, emit exactly zero
stdout bytes, make one non-unexpected `POST /api/v1/query`, send no fixture
response header/body bytes, and leave their configurations byte-identical.
All nine inspected captures have matching request contracts: length framing,
primary listener, JSON content type, gzip acceptance, Basic harness credentials,
content length 98, and the exact request body:

```json
{"query":"SELECT 1 LIMIT 500","startTime":"2024-01-01T00:00:00Z","endTime":"2024-01-01T00:01:00Z"}
```

Only two bounded historical same-case directories were added:
`retained-release4-phase3` and `retained-release5-phase3` in the phase7 cache.
Release4's two Go captures are canceled-with-suffix; release5's two are
context-with-suffix. Together with release6 they establish **all three Go
spellings across six actual Go processes**. The three Rust captures are kept
separate. The one bare observation is not claimed as replicated. Historical
same-case entries are clean even where their campaign failed another case.

Pinned source analysis supports this exact observation. Read-only `git show`
verified that `cmd/query.go`, `pkg/http/http.go`, `pkg/http/refresh.go`,
`main.go`, `go.mod` and `go.sum` equal oracle commit
`07af390ab09bda8ac9e427ffc6310077bace0125`. Source hashes and preserved copies
are bound in the evidence. Pinned Go toolchain `VERSION` is `go1.25.4`;
`net/http/client.go` SHA-256 is
`0ef2c2f5dc3c3d88eba684227340c153b6fce6d49298e19aa3086b9c5eb98595`,
and `transport.go` is
`8b84f3c0774c9fcccc2973858b9a53adc8ea1d43c4f127687b0b56f215a7ba8d`.

`http.go:46–60` always wraps the 60-second client transport in
`cloudRefreshTransport`; `NewRequest` uses a background-context request.
`refresh.go:32–35` passes this header-stall error through unchanged.
`client.go:320–414` does not recognize the wrapper as a known transport, so
it installs both a context deadline and a timer whose atomic `timedOut` store
precedes legacy cancellation. `transport.go:646–650,743–749,2874–2884`
routes context-deadline and legacy-cancel causes to the pending header wait.
`client.go:729–735` adds the awaiting-headers suffix only if `didTimeout()`
is true then. Context before the store yields bare context; context after it
yields suffixed context; legacy cancellation yields suffixed canceled. Bare
canceled is outside this fixture's reachable outcomes because the flag store
precedes cancellation. `query.go:725–728` wraps the error as
`request execution failed`. This is the header send-error branch, separate
from the successful-response `cancelTimerBody.Read` body/lookahead branch.

This uses the actual captured binary hash
`fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`.
Every present historical/current binary was rehashed against its report.
The compiled Go file contains `go1.25.4` but no literal oracle revision/VCS
marker was found. Source equality and binary identity do not constitute a
new reproducible-build proof. The candidate manifest, binaries and current
source-file hashes are separately verified in the handoff.

The rule adds one exact case branch after `compare.timeout_cause` and before
the existing P4 rule. It rewrites exactly the three complete header-error
streams above to the existing 137-byte canonical stderr. It requires the exact
case `p3-deadline-header-stall-text`, exact method, normalized origin, path,
application wrapper, cause and final LF. Unrecognized streams return unchanged
before the legacy substring regex. This deliberately tightens foreign suffixed
streams in this case, including wrong method/path, body suffix, extra content,
spinner prefix and Usage; it does not add broad normalization. Canonical bytes
remain idempotent, as before. No corpus definition or expectation changes.

The existing body/lookahead/redirect and P4 whole-Usage rules are preserved.
The prior [combined review](phase7-review-combined-deadline-sol-r2.md),
[body review](phase7-review-body-deadline-r1.md),
[lookahead review](phase6-lookahead-deadline-review-opus-r1.md),
[P4 header review](phase6-p4-header-deadline-review-opus-r1.md) and redirect
reviews were read, with the current source and proposals. Their approvals
cover different cases; none is used as approval of this proposal. The P4
review explicitly identified this sibling header race as an out-of-scope
possibility lacking a bare capture then. The new bare capture supplies that
missing case-specific evidence now. No JSON, refresh or other header rule is
added, and no existing negative assertion is reversed.

Independent gates remain exit 1, empty stdout, one exact request method/path
with no unexpected request, exact canonical stderr, and inclusive 59000–70000
ms duration. The subprocess supervision remains 80 seconds. Request body,
contract headers, framing/listener and semantic configuration remain
differential checks; this case has no new independent config/body/header pin.
Actual configuration stability is observed in every capture, not newly invented
as an independent expectation. Identical wrong body/header/config observations
on both sides can still pass those differential checks, as tested. For an
admitted bare spelling the canonical suffix is synthesized; raw timeout
evidence is the exact error plus header-stall trace, exit and functional window.
A bare candidate is intentionally accepted symmetrically.

Executed validation, both normally and with `python3 -I -B -O`:

- 19 focused tests pass: 13 unchanged deadline/comparison tests plus 6 new
  methods. With the before-runner the new methods produce 18 expected assertion
  failures and no errors, proving both the new acceptance and tightening are tested.
- All 9 actual archived captures pass the proposed independent checks; all
  72 directed nonself pairs pass. Exact original issue accumulation and reference
  manifests reproduce for all three campaigns. In particular, original release6
  still reproduces its sole byte-71 A/A issue under the before-runner.
- 324 computed captured-data rejection controls exercise exact stderr,
  exit, stdout, request graph/unexpected requests, duration, body/header and
  semantic config changes. Additional focused tests check both stderr directions,
  matching extra-output cause pairs, inclusive duration boundaries and field-pin limits.
- 29232 before/proposed normalizer checks use 1044 actual case
  definitions and 28 distinct retained/synthetic stderr inputs. Changes are confined
  to this case. 58464 absent/false opt-in checks preserve bytes. This is a
  cross-case behavior grid, not an archive sweep or new capture campaign.
- All 15 source mutants are killed by assertion failures, with zero errors/skips:
  removed rule, pooled/JSON case scope, bypassed opt-in, prefix/newline relaxation,
  bare canceled/unknown cause, wrong method/origin, legacy fallback, disabled
  stderr comparison/independent pin, and disabled request/config differentials.
- AST comparison changes only `normalize_timeout`; corpus and deferred guards
  are equal. Both patches apply at exact LF indices in an independent pure-Python
  unified-patch consumer and produce byte-exact staged files. **No `git apply`
  execution is claimed.** Normal and optimized drivers differ only in log naming;
  their proposed files, patches, reductions, controls and scope results agree.
- 198 original input files are hash-verified unchanged after each run and again
  at publication. Guards block native launch, sockets, thread start and locks,
  and restrict writes to the unique cache. Both successful runs record zero
  guard violations. An initial SSL/import-order setup error occurred before
  any tests; its exact script and log remain preserved separately.

Review target hashes:

- Complete source/test patch: `387aedec115abd651f9764506672dfe0dbfa5dc5e356b5216dec3921809cebe6`.
- Source-only subset patch: `a038d91c459fdcb3a728d8892a85cca47f72f194d641e6801e51dc752c38057b`.
- Base runner: `33be7605b2e3ea44712a8118b8909b129d00ef3f46c7f4d3932a522351e2b7bd`.
- Base test: `ef1a47dc263d18ed94fac7577a7e2597699973e74a4b42d3cbdc598cca9fab59`.
- Proposed runner: `d13285950a2213faa6adebacffa037b7ad63327012acfa474b0657cc6499d5c0`.
- Proposed test: `ce2f56dbe41415fcc2a7bd6a2e4918cba30057d0efd968af44cebb8145fe06c7`.

Outstanding: fresh independent Opus review of this exact complete patch and
evidence; root's frozen-driver completion/edit-window coordination; any authorized
exact native apply/full-suite checks and successor retained replay required by
root. None occurred here. A changed shared base requires a fresh exact patch and
hashes, not fuzz or reinterpretation of an earlier report. This proposal makes
no phase, release or performance acceptance claim.
