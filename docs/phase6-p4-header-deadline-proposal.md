# Phase 6: off-tree phase-4 header deadline proposal v2

**Off-tree v2 correction; no promoted normalization or phase acceptance.**
[Independent executed Opus r1](phase6-p4-header-deadline-review-opus-r1.md)
accepts the source rule unchanged and requires F1/F2 test amendments. This v2
implements those amendments and F3–F6 clarity/scope improvements. Fresh independent
combined correction review remains a parent gate before promotion. No agent or
review was invoked by this implementation assignment. No live harness,
Rust/Go source, corpus, frozen manifest, or archived capture was edited. The
separate phase-3 lookahead proposal and its deferred guards remain intact.

The owned [evidence index](evidence/phase6/p4-header-deadline-proposal-v2.json)
duplicates the cache's `evidence.json`. All preparation scripts, test additions,
before/proposed source copies, mutants, logs and the patch are under
`/home/ajs/.cache/pb-phase6/p4-header-deadline-proposal-v2`.
The entire v1 and Opus r1 caches, prior evidence index and review document are
hash-verified unchanged. The former proposal document is preserved verbatim in
the v2 cache at `inputs/proposal-v1.md`.

## Original failure and exact comparator path

The preserved R6 report is
`/home/ajs/.cache/pb-phase6/retained-review-r6-phases45/phase4/report.json`,
SHA-256 `5851790bc84a6fbf6cac6f796c4eab659c564138e267d0ad80cbb59f9ca265bd`. It completes 160/161 cases and has exactly one
case issue, for `p4-deadline-header`:

```text
stderr differs at byte 78: candidate 526 bytes, reference 566 bytes
```

`harness/phase4.py:413–416` captures Go `reference`, checks its independent
expectations, then captures Go `repeat` and calls
`p3.differences(case, ref, again)`. That helper's second argument is called
`candidate` in its error formatter; phase 4 appends the issue without a
`reference unstable` prefix. The 526-byte “candidate” is therefore **Go repeat**.
`phase4.py:425–426` then checks Rust and calls the same comparator separately.
The reference/Rust differential has no issue: their normalized stderr bytes
are identical. The proposal recomputes the complete original accumulation and
reference signature for each archived same-case report, including all issues;
it does not infer the origin of this label from text alone.

R6 actual captures all exit 1, emit zero stdout bytes, and make one ordered
`GET /prometheus/api/v1/labels?stream=select-dataset`. Wall times are:

- Go reference: `120028.246423` ms; context cause with awaiting-headers suffix.
- Go repeat: `120030.371876` ms; bare context cause.
- Rust candidate: `120003.95614` ms; context cause with awaiting-headers suffix.

Existing URL normalization replaces each actual fixture origin with
`http://<FIXTURE>` before comparison. This proposal does not add URL/path
normalization. The differing offset 78 is immediately after the 78-byte error
prefix: the existing suffixed comparison starts its cause with
`<CLIENT-TIMEOUT>`, while the bare repeat still starts with `context`.
The full Usage block remains identical.

## Archived spellings searched before any additional probe

The R4/R5 phase-4 directories are `retained-review-r4-full/phase4` and
`retained-review-r5-full/phase4`, under the same phase-6 cache. Their report
case entries are clean. Read-only inspection and guarded reduction cover all
nine actual captures, including six Go captures:

- R4 Go reference: canceled with suffix (`120028.892361` ms); Go repeat:
  context with suffix (`120033.739933` ms); Rust: context with suffix
  (`120003.028218` ms).
- R5 Go reference/repeat: canceled with suffix (`120031.635366` /
  `120040.378997` ms); Rust: context with suffix (`120002.444354` ms).
- R6 spellings and durations are listed above.

Thus the actual Go spellings are bare context, suffixed context, and suffixed
canceled. Bare context appears once; this is not a replication claim. Bare
canceled is not observed or admitted. No extra native CLI, build, fixture,
timing or probe was run. Executable identities were verified by hashing the
preserved files, without executing them. Report, binary and every same-case
artifact hash, raw stderr, normalized stderr, requests, trace, exact durations,
full original issue lists and reduced issue lists are in the evidence index.

## Source race: pinned Go07af390 and Go1.25.4

Git object reads verify byte equality of `cmd/promql.go`, `pkg/http/http.go`,
`pkg/http/refresh.go`, `main.go`, `go.mod` and `go.sum` against
`07af390ab09bda8ac9e427ffc6310077bace0125`. The toolchain `VERSION`,
`src/net/http/client.go` and `transport.go` are hashed from the disk Go1.25.4
module. This is local source analysis, not a newly measured race.

- `cmd/promql.go:186–206` constructs a custom HTTP transport via
  `DefaultClientWithTransport`, sets the client timeout to 120 seconds, creates
  a background-context GET, authenticates, then calls `Client.Do`. The labels
  path returns that error unchanged (`415–418`). Text-mode Cobra reports the
  error and Usage; `main.go:309–321` preserves that mode and exits 1.
- `pkg/http/http.go:46–60` always wraps the transport in
  `cloudRefreshTransport`. `refresh.go:32–35` returns a transport error directly;
  a header stall does not enter the 401 refresh branch.
- Go1.25.4 `client.go:320–340` does not recognize this wrapper as a known Go
  RoundTripper. `setRequestCancel` therefore takes its slow path: it installs
  `context.WithDeadline` (`373`) plus a separate timer for the same deadline
  (`398–414`). The timer stores `timedOut=true` before closing the legacy cancel
  channel. `didTimeout` reads this atomic flag.
- `transport.go:646–650` creates a cancel-cause context and starts legacy
  cancellation handling. `awaitLegacyCancel:743–748` supplies
  `net/http: request canceled`; the underlying deadline supplies
  `context deadline exceeded`. The pending response-header path
  (`2874–2884`) cancels the request with that context cause.
- `client.go:728–735` appends ` (Client.Timeout exceeded while awaiting headers)`
  only when `didTimeout()` is true, then returns the URL-wrapped error. A
  context failure observed before the timer flag is set yields bare context;
  context failure after it yields suffixed context; legacy cancellation after
  the timer's store yields suffixed canceled. Bare canceled is not admitted.

This header-failure path is distinct from phase 3's successful-response
`cancelTimerBody.Read` / body-lookahead path. That prior proposal's source
approval does not approve this one.

## Proposed exact rule and preserved boundaries

The proposed patch edits only off-tree copies of `harness/phase3.py` and
`harness/test_phase4.py`; production `phase4.py` and its corpus are untouched.
After the existing `compare.timeout_cause` opt-in guard, the normalizer checks
the exact case name `p4-deadline-header`. It accepts only the complete stream:

```text
Error: Get "http://<FIXTURE>/prometheus/api/v1/labels?stream=select-dataset": <one admitted cause>
<the exact captured Usage block, including every LF and both final LFs>
```

Admitted causes, byte-for-byte:

```text
context deadline exceeded
context deadline exceeded (Client.Timeout exceeded while awaiting headers)
net/http: request canceled (Client.Timeout exceeded while awaiting headers)
```

All map to `<CLIENT-TIMEOUT> (Client.Timeout exceeded while awaiting headers)`
with the unchanged exact prefix and Usage bytes. Usage SHA-256 is
`1a6aa3de25f1cc3f521c9559e28d9f3d77d2a15deee9cb0f954eefe06d182f45`. The source and tests independently contain its full literal.
There is no prefix match, substring match, stripping, newline conversion,
regex widening, or capture-dependent rule generation.

For this exact case, unrecognized streams return unchanged **before** the
legacy substring regex. This deliberately tightens its previous behavior:
even a known suffixed cause with changed Usage, flags, examples, method, origin,
path, query, newline, leading/trailing content, or extra status text remains
byte-identical. Unknown causes and bare canceled also remain unchanged. Other
cases retain their existing behavior; bare proposed input is byte-identical
for every wrong case. Existing unrelated spinner/unordered-line comparison
options retain their semantics; the proposal does not claim they are identity
transforms. `3428` direct baseline/proposal checks use **four synthetic
inputs × 857 other case definitions**: the three literal P4 forms and one bare
lookahead string. They verify unchanged legacy normalizer behavior for those
synthetic inputs; they are not an actual candidate-stream sweep (F3).

Independent gates remain exit 1, empty stdout, one ordered GET, no unexpected
requests, the exact target/query, inclusive 119000–130000 ms bounds, and
configuration stability within each capture. Tests now show that identical wrong
exit/output/request observations can produce an empty differential while still
failing independent expectations. This does not apply to every wrong field:
identical out-of-range durations also fail the differential candidate timing
bounds, which is asserted separately (F6). The existing stderr expectation remains the
`Client.Timeout` substring; unknown stderr is not newly independently pinned.

Request headers/body are differential-only here: no independent pins exist.
Changed headers/body can pass when shared identically on both sides; the tests
explicitly demonstrate this limit. Requests also compare framing/listener.
Configuration compares semantic before/after state across captures and separately
checks within-capture stability; it has no independent fixed configuration pin.
Exit, stdout digest, candidate duration and exact full stderr differences
continue to gate. For an admitted bare context stream, the independent
`Client.Timeout` substring is satisfied by the **synthesized awaiting-headers
suffix**, rather than text present in its raw stderr; the whole exact stream
match is the stronger admission pin (F2). The independent EOF-cause test requires
`stderr lacks b'Client.Timeout'` for an unrecognized stream. This pre-header stall has no received HTTP response status
pin: no status or output/request difference is waived by the rule.

A non-admitted stream can now report **byte 78 even when the actual regression
is Usage drift**: the admitted side is canonicalized but the changed side is
left raw, so the first difference moves to the cause position. Opus r1 observed
a Usage change moving from byte 451 before the rule to byte 78 afterward.
Inspect the raw stderr and full Usage block, not just the offset, before
classifying a failure as the timeout race (F4). Matching precedes spinner
normalization; spinner-prefixed streams fail closed, and the inspected Go
captures have no spinner frames. The unchanged source rule is deliberate.

Every unrelated phase-3 function definition, including `differences`,
`boundary_issues` and `deferred_boundaries`, is byte-identical and hash-listed.
The phase-4 source and all four historical guard declarations are untouched;
pure tests still reject a deferred stub in the ordinary case and network
requests in deferred boundaries. Neither phase 3's lookahead rule nor any
other deferred guard is promoted, combined, renamed, or weakened.

## Executed guarded tests, controls and evidence limits

`32` selected pure tests pass, with no skip or error: seven new
phase-4 tests, existing selection/pure corpus tests, and legacy phase-3 deadline
tests. The two existing socket/native proxy controls are explicitly excluded.
Each admitted stream receives a single-byte mutation at every position and
must remain unchanged. Additional tests cover complete-stream boundaries,
unknown causes, exact case names, opt-out, independent gates, differential
gates and deferred guards. F1 adds explicit `stderr differs` assertions in
both directions for Usage drift, unknown cause with suffix, bare canceled and
missing LF, plus the canceled/context pair sharing an extra trailing line. F5
adds synthetic timeout-opted-in wrong names ending in `-header` using a literal
legacy body-suffix transform; the rule itself is unchanged.

`20` causal mutants fail assertions with zero execution errors.
They disable the rule, widen its case scope, pool header/body cases, admit
prefix/substring matches, relax newlines, admit bare canceled, bypass opt-in,
fall through to the old regex, drop Usage, and disable independent
exit/stdout/request/duration checks, differential request/config checks or the
ordinary deferred-stub guard, plus the reviewer
`r-disable-differential-stderr`, `r-disable-stderr-has` and `r-name-endswith`
mutants. Those three source files are byte-equal to the reviewer artifacts; all
three pass the old seven-test v1 selection and fail assertions in the corrected
v2 selection with zero errors. The first two causal failures directly close
F1 and F2. All sources, failure tracebacks and log hashes
are retained. The disabled-rule control also restores the real bare/suffixed
stderr differential and fails the positive equivalence assertion.

The original issue accumulation is reproduced for R4/R5/R6 before applying the
proposal in memory. The proposed reduction produces zero issues for each
same-case accumulated path and all 81 ordered comparisons across the nine
actual captures. These are reductions of archived processes, **not invented
replay or new executions**. The original reports still show their original
outcomes. No full retained suite success or phase acceptance is claimed.

Preparation learned from
[the prior Opus review](phase6-lookahead-deadline-review-opus-r1.md) and the
separate v2 `prepare.py`: patches use LF-only splitting, with a U+2028/U+2029/
U+0085/control-separator check; `git apply --check` and actual cache-only apply
both succeed; resulting files equal both proposed byte strings exactly. Config
test records use the real file-entry shape, and wrong-case checks require
identity rather than merely different canonical output. Broadened causal
mutants are killed, including case pooling.

Subprocess, OS launch, socket, fixture/sample, thread-start and flock/lockf
guards prohibit execution after preparation's Git read/apply stage. Guard call
counts are empty. A Python write audit permits only the assigned cache and two
assigned documents. TMP/TEMP/TMPDIR, GOTMPDIR and the unused Claude config/cache
location are on disk inside that cache; no `/tmp` edit, agent, commit, Mayfly or
cross-thread message occurred. `3482` original files were
hash-verified unchanged in the final preparation run, including all live
harness/source/corpus files, inspected archives and separate v2 artifacts.

V1 preparation failures and concurrent document drift remain recorded in its
unchanged evidence and cache. This v2 does not rewrite that history. Its own
preservation snapshot includes the entire v1 and executed-review caches,
review document, old evidence and all inspected live bases and captures. The
current owned proposal document is the only updated existing documentation.

## Review resolution and parent boundary

- F1: ten directional stderr-failure checks cover all four required wrong
  streams and the tightening pair; reviewer differential-disable control killed.
- F2: explicit independent missing-needle assertion and synthesized-suffix
  explanation; reviewer needle-disable control killed.
- F3: synthetic 4 × 857 input wording corrected; no invented archive sweep.
- F4: byte-78 ambiguity, raw-stderr/Usage triage and spinner ordering documented.
- F5: optional wrong-case synthetic opt-in scope test added; endswith control killed.
- F6: real identical-wrong-result differential checks replace the redundant call;
  duration fields explicitly retain their differential timing gate.
- F7: no composition performed. Root must regenerate a single LF-only combined
  diff with fresh hashes; sequential exact patch application collides and must
  not be rescued with offsets or fuzz. Fresh independent combined correction
  review is the parent gate.
- F8: possible header races elsewhere stay out of scope; no rule widening.
- F9: current base hashes and v1/review preservation checked; historical external
  changes are neither restored nor attributed to this correction.

## Hash-bound review target

- Patch: `2857f72607e4f4303ec5319078c61131e234f61f73b912255ec305385f9332a0`.
- Actual source base (`harness/phase3.py`): `7f8deaf00185b94cd5934fbe6542e0393ded5f25aadd918012bd2f072d222f9d`.
- Actual test base (`harness/test_phase4.py`): `377824659f5522a891f6f46a6045a515661dd9f0268ee484cbe11e55778d5658`.
- Proposed source: `ca32ffa8cf9eb466a7e4bdc76667383fa0ace2a6b345946b17b6515875f5a325`.
- Proposed tests: `94f2d11cd3a1a6a5a815bb0f1676f9c800c4cd804299136957244cbea8a2eaa1`.
- Preparation script: `24df68530e345022dc7417f4ced5720e63217ad33b820c93e251a44f6abac20d`.

The accepted source SHA remains exactly the v1 value `ca32ffa8…5f5a325`;
only the proposed tests and resulting patch change. Review must use these exact
patch/source/test hashes and the full v2 evidence index. Fresh independent
combined correction review remains a parent gate. Any subsequent combined
shared proposal needs fresh base hashes, exact cache application, guarded
shared-suite execution and fresh review; this document grants no promotion or
native-run authority.
