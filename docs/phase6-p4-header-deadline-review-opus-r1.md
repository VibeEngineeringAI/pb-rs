# Phase 6: P4 header-deadline proposal — independent executed Opus review r1

**Scope:** fresh, independent, **executed** review of the off-tree proposal in
[phase6-p4-header-deadline-proposal.md](phase6-p4-header-deadline-proposal.md)
(document SHA-256 `389b03e8…57828f`, equal to its evidence record) and
[its evidence](evidence/phase6/p4-header-deadline-proposal.json) (`dfcca400…980b21`,
byte-equal to the cache copy). This review covers the normalization rule only. It is not
a phase acceptance, does not authorize promotion or a native run, and edits no live
harness, source, corpus, document or archive. The only file written outside the
review cache is this document.

Review cache: `/home/ajs/.cache/pb-phase6/p4-header-deadline-review-opus-r1`
(`scripts/`, `logs/`, `results/`, `work/`, `inputs/`). TMP/TEMP/TMPDIR, GOTMPDIR
and CLAUDE_CONFIG_DIR were set under that cache. The harness shell's `/tmp` quota
was exhausted (EDQUOT), so every command redirected all output into `logs/` and the
log was then read. Nothing was moved to `/tmp`.

## Disposition

**ACCEPT the exact `p4-deadline-header` rule (source and executed behavior) at
patch `b1b2c279…ed95bcb8`, with two required test-only amendments before
promotion (F1, F2).** No rule change is requested. The doc corrections in F3 and F4
are recommended. F5–F6 are informational. F7 is a hard constraint on how root composes
this patch with the accepted P3 patch: sequential exact application fails in both orders.

The original R6 phase-4 report is still 160/161. This review is a reduction of
archived processes. It is not a replay, and it does not convert that report into a pass.

## What I executed (all in my own cache copy)

All Python ran as `python3 -I -B` (3.14.7, `sys.flags.optimize=0`) with an audit hook
and monkeypatches installed **before** any harness import. They block
subprocess/exec/fork/spawn/kill, `socket.__new__`/bind/connect/getaddrinfo,
thread start, `fcntl.flock`/`lockf`, and any file mutation outside the review cache.
Every run recorded its violations: zero in all reductions, probes and mutant runs.
In the full-suite runs, exactly 14 native/socket/runner tests error on the guard,
and they error identically on before, proposed and composed trees.

| Step | Result |
|---|---|
| Hashes | patch `b1b2c279…`, source base `7f8deaf0…`, test base `37782465…`, proposed source `ca32ffa8…`, proposed tests `aa76f28a…`, prepare `d482260e…` all match. Live `phase3.py`/`test_phase4.py` still equal the bases. |
| Exact apply | Harness copied to `work/before`, `work/proposed`. `git apply --check` = 0 and `git apply` = 0, run with `GIT_CEILING_DIRECTORIES` so no repository is discovered. Applied bytes are `cmp`-equal to the author's proposed files. Patch has no CR. Only `phase3.py` and `test_phase4.py` differ from before. |
| Author selection (32) | 32 pass, 0 fail/error/skip, 0 violations (`results/author32-proposed.json`). |
| Full pure phase 3+4+5 modules | before: 90 run, 71 pass, 14 guard errors, 5 skip ("not built"). proposed: 97 run, 78 pass, same 14 guard errors, same 5 skips. No test changes outcome; 7 new tests pass. |
| Reduction (`results/reduce.json`) | R4/R5/R6 report issue lists reproduced exactly with the before source. R6 report `5851790b…` gives `stderr differs at byte 78: candidate 526 bytes, reference 566 bytes`. Proposed: zero issues per revision, and 81/81 ordered pairs clean (16 pairs failed before). All 9 captures exit 1 with 0 stdout bytes, one ordered unexpected=false `GET /prometheus/api/v1/labels?stream=select-dataset`, wall 120002–120040 ms, config before == after, prefix exactly 78 bytes, one Usage SHA `1a6aa3de…`. Raw and normalized stderr differ only by origin. Per-capture artifact hashes equal the evidence. The proposed `signature()` equals every archived report signature, so baseline mode stays compatible: all three references are suffixed. |
| Real-stream equivalence | **7650** archived `stderr.normalized` files (R4/R5/R6, phases 3/4/5), each matched to its own case. Comparing `normalize_timeout` and `compare_stderr` before vs proposed (plus phase-5 wrapper ×9), the only change is the R6 P4 repeat (bare → canonical). Cross product of 1059 case definitions × 20 distinct real timeout-like streams = **21180** checks, with zero changes outside the target case. Within the target case, the only changes are foreign-case streams, which the legacy regex used to rewrite and which now stay unchanged (the intended tightening). |
| AST | 177 top-level definitions in the same order. Only `normalize_timeout` changed. Deferred boundary inventories are equal. |
| Mutants | The author's 17, rebuilt from the replacement strings in `prepare.py`: all 17 **SHA-equal to the recorded mutant sources**, all killed by assertion failures, none by errors. Of my 20 extra mutants (18 phase3, 2 phase4), 17 are killed and **3 survive** (F1, F2, F5). Survivors were rerun against the full pure 97-test suite and still survive. |
| Behavioral probes (`results/probes.json`) | Built from the real R6 captures; before vs proposed shown in the findings. |
| Compose dry run | See F7. |

Counts: 870 test executions (32 + 90 + 97 + 101 + 37×7 + 3×97), 2 reductions,
1 probe run, 37 mutant trees. The Bash tool calls are logged in `logs/00–21`.
Key SHA-256s:
`review.py 8a1029d9…`, `reduce_body.py cb21e9ce…`, `make_mutants.py 945d6799…`,
`probe_body.py 7d957498…`, `reduce.json 1055efd1…`, `reduce-compose.json 11a29d8a…`,
`full-before.json 88cbc633…`, `full-proposed.json 3011a9e9…`,
`full-compose.json c7a5a800…`, `mutants-made.json 92b74f5a…`, `probes.json d6d3566e…`,
`logs/12-reduce.log e3888f0c…`, `logs/14-mutsummary.log 65d58037…`,
`logs/19-compose-run.log 0aecb797…`, `logs/20-probes.log 9fe2ba8b…`.

## Source rationale (independently read)

`git show 07af390:<f>` equals disk for `cmd/promql.go`, `pkg/http/http.go`,
`pkg/http/refresh.go`, `main.go`, `go.mod` and `go.sum`. The Go 1.25.4 toolchain `VERSION`
reads `go1.25.4` (`client.go 0ef2c2f5…`, `transport.go 8b84f3c0…`). The proposal's
line citations hold:

- `promqlGet` builds `DefaultClientWithTransport(…, &http.Transport{…})`, sets
  `Timeout = 120s`, then calls `Client.Do`. `DefaultClientWithTransport` always wraps the transport
  in `cloudRefreshTransport`, whose `RoundTrip` passes the **same** `req` to
  `base.RoundTrip`.
- `knownRoundTripperImpl` (client.go:320–340) does not recognize the wrapper, so
  `setRequestCancel` takes the slow path. That path sets `context.WithDeadline` (373) and
  `req.Cancel` (377), and the timer goroutine calls `timedOut.Store(true)` **before**
  `doCancel()`→`close(cancel)` (406–408). `initialReqCancel` is nil for
  `http.NewRequest`.
- `transport.go:646–650` turns `origReq.Cancel` into `awaitLegacyCancel`, whose cause is
  `errRequestCanceled` ("net/http: request canceled"). The header wait at 2874–2883
  cancels with `context.Cause(req.ctx)`. `client.go:732–733` appends the
  awaiting-headers suffix only when `didTimeout()` is true.

So **bare canceled is unreachable**: a legacy cancel implies the flag was already
stored. Bare and suffixed context come from the deadline-context vs timer race.
The rule admits exactly the three reachable spellings. This header path is distinct
from the phase-3 body/lookahead `cancelTimerBody` path. The archives show the three
spellings across six Go captures; bare appears once, and no replication is claimed.

## Overfitting and false-pass assessment

- **New acceptances** are only pairings among the three exact streams. The one that
  previously failed is bare-vs-suffixed, in either direction. Rust may now emit bare
  context by design (probe `rust_bare_vs_go_suffixed`: before fails both the
  differential and the independent needle; proposed passes both).
- **Pinned Usage and origin:** this is overfitting only in the fail-closed direction. A
  change to Usage, flags, method, origin, path/query, newlines, or leading/trailing
  content leaves the stream unchanged and fails the differential (probes:
  Usage drift, CRLF, spinner prefix, unknown cause with suffix text, bare canceled →
  all fail). The Go oracle is pinned at `07af390`, so its Usage is frozen.
- **Deliberate tightening confirmed:** canceled vs context sharing an extra trailing
  line or a shared Usage change passed **before** (legacy regex) and fails **now**.
- **No widening elsewhere:** confirmed by the 7650 real-stream and 21180 cross-product
  equivalence checks above.

## Findings

**F1 — Medium (required test amendment): the stderr differential is never exercised for
this case.** Mutant `r-disable-differential-stderr` (`if False and expected != actual`
in `differences`) survives all 7 new tests **and** the entire pure phase 3/4/5
suite (97 tests). The proposal's key claim, that unknown or changed streams "remain
byte-identical" and "exact full stderr differences continue to gate", is tested only
as `normalize_timeout` identity, never through `differences()`. I verified the
behavior directly with probes, but the suite would not catch a regression. The shared-suite
gap predates this proposal; this case now depends on it. Add to
`test_differential_body_headers_status_output_and_config_gates`, in both directions:
`any('stderr differs' in i for i in p3.differences(case, self.result(f), self.result(w)))`
for `f = forms()[1]` and `w` in: Usage drift (`--output`→`--format`),
`prefix + b'i/o timeout' + suffix + b'\n' + usage`, bare canceled,
`forms()[0][:-1]` (missing LF). Also add the tightening pair:
`forms()[2] + b'x\n'` vs `forms()[1] + b'x\n'` must differ.

**F2 — Low/Medium (required test amendment and one doc sentence): the independent
`Client.Timeout` needle is untested, and for the bare form it is satisfied by synthesized bytes.**
Mutant `r-disable-stderr-has` survives the whole pure suite. Probes: an unknown stream
lacking that text gives `stderr lacks b'Client.Timeout'` before and after. The bare Go
reference fails the needle before and passes now, only because the normalizer inserts
the suffix. The evidence JSON says this (`only exact accepted forms supply it`), but
the proposal body says only that the expectation "remains". This is acceptable, because the
exact whole-stream match is a stronger pin, but it should be stated. Add
`assertIn("stderr lacks b'Client.Timeout'", phase4.expectation_issues(case, self.result(self.prefix + b'EOF\n' + self.usage)))`.

**F3 — Low (doc accuracy): "3428 direct baseline/proposal checks verify unchanged legacy
normalizer behavior on other cases and candidate streams."** These are 4 synthetic inputs
(the three P4 forms plus one lookahead bare string) across 857 case definitions. They are not
candidate streams. The claim still holds, and my real-archive sweep (7650 + 21180) supports
it, but the sentence should describe the inputs accurately.

**F4 — Low (diagnostics; no code change requested):** for non-admitted streams the
reference is canonicalized and the candidate is not, so the reported offset moves to the
cause position. Probe: a Rust Usage drift reported `byte 451` before and now reports
`byte 78`, which is the same offset as the R6 race symptom. Triage could misread a
Usage regression as the timeout race recurring. Note this in the proposal or runbook.
Relatedly, the exact match runs before spinner normalization. A spinner-prefixed
stream fails closed (probe), and no Go capture contains spinner frames.

**F5 — Info (test strength):** mutant `r-name-endswith` (`endswith("-header")`)
survives. It is equivalent with the current corpus, because the only other `-header` case,
`p3-text-gzip-bad-header`, lacks `timeout_cause`. The wrong-name tests use only the bare
P4 input, which is identity under the legacy path too, so a scope widening that returns
early for other `timeout_cause` cases would show only on suffixed legacy input. Optional:
for each other `timeout_cause` case, assert that the proposed `normalize_timeout` of its own
suffixed form equals a literal legacy `<CLIENT-TIMEOUT>` output.

**F6 — Info (test clarity):** in `test_independent_exit_stdout_request_and_duration_gates`,
`assertTrue(expectation_issues(case, copy.deepcopy(bad)))`, commented "Same wrong
observations on both sides…", only repeats the previous call. To show the stated
property, assert `p3.differences(case, bad, copy.deepcopy(bad)) == []` alongside the
non-empty independent issues.

**F7 — Process constraint for root's compose (not a rule defect):** the two exact patches are
semantically non-overlapping but **textually collide**. On base `7f8deaf0…`, applying the accepted
P3 combined patch `00f14a8a…` and then `git apply --check` of this patch fails
(`patch failed: harness/phase3.py:1473`). The reverse order also fails (`…:1475`), because
both hunks use lines 1475–1477 as context. Do not compose by sequential application
or fuzz. My advisory LF anchor composition (P3 exact, then this patch's 19-line block,
SHA `07d14ebc…`, inserted before the unique `    bare_context = {\n` anchor, no CR)
produced `phase3.py 01668670…`, `test_phase3.py 812e9609…` (from P3) and
`test_phase4.py aa76f28a…`. On that tree the guarded full pure suite ran 101 tests: 82 pass,
the same 14 guard errors, 5 skip, 0 fail. The P4 reduction stays clean (81/81, all revisions
reproduce, signatures equal), and over 7650 real streams exactly two change: the R6 P3
lookahead reference and the R6 P4 repeat. AST change is still only in `normalize_timeout`.
Root's own composed diff must carry fresh hashes.

**F8 — Info (residual, out of scope):** the same `cloudRefreshTransport` header race
applies in principle to `p3-deadline-header-stall-text/json` and `p3-deadline-spans-refresh`.
They rely only on the suffix regex, and the archive sweep shows no bare stream there.
A future bare stream would fail exactly as this case did. No change requested.

**F9 — Info (environment):** compared with the author's 1096-file snapshot, three files now
differ: `docs/rust-phase6.md`, `docs/phase6-resource-summary.md` and
`harness/test_phase6_resource_summary.py` (mtime 11:33, before this review). All are
outside P4, and none was written by this review. The guard blocked writes outside the cache,
with no violations. All P4 inputs and live P3/P4 harness files still hash to the
reviewed values.

## Lessons from the prior lookahead review, checked

- **LF-only exact apply:** done. Real `git apply` succeeded and was byte-compared.
- **Wrong-case tests must assert identity:** they do (`assertEqual(..., bare)`), and
  pooled-header-body, case-agnostic, contains and casefold mutants are killed.
- **Computed vs claimed:** every author number I re-derived matches: 32 tests, 17
  mutants (SHA-equal), 81 pairs, 3428 = 4 × 857, the 78-byte prefix, the Usage SHA and the
  byte-78 diagnostic.
- **Source vs execution:** this review is executed. Native, socket, runner and fixture tests
  were blocked, and no Go/Rust CLI, build, fixture, timing, lock or Mayfly was run.

## Remaining gates (not satisfied by this review)

1. Test amendments F1 and F2 in the P4 test addition, with fresh test hash and patch.
2. Root LF composition with the accepted P3 patch as a regenerated single diff with fresh
   hashes (F7), cache-exact apply, guarded shared-suite run on the composed tree,
   scope/AST checks, and frozen-guard preservation.
3. Retained acceptance still needs whatever native evidence root's process requires
   for `p4-deadline-header`. R6 phase 4 remains 160/161 as archived.
