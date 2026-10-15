# Body-deadline R1 correction: implementation handoff

Prepared off-tree in `/home/ajs/.cache/pb-phase7/body-deadline-r1-fixes` for
`/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` only. This is an
implementation response to [R1](../../../phase7-review-body-deadline-r1.md), not
an independent review, promotion, replay, or phase acceptance.

[The exact shared-source patch](body-deadline-r1-fixes-lf.patch) includes only:

- one `normalize_timeout` entry for `p3-deadline-body-stall-text` and its exact
  `failed to stream response body` bare deadline message;
- deliberate reversal of the prior F1 negative for that exact pair, with its
  wrong-message and other-case negatives retained;
- two focused tests for exact boundaries, three-form equivalence and manifest
  digests, independent gates, differential mutations, and a bare candidate.

The existing test is renamed
`test_legacy_bare_deadline_contexts_remain_case_specific`; its name
makes no total-case-count claim. No lookahead correction is included. Final patch generation uses LF-only boundaries (`split("\n")` with LF endings
preserved), so the literal U+2028 in existing test source does not corrupt hunk
positions. `git apply --check --verbose` succeeded against cached **before**
copies with no offsets; actual application to a separate cache verification
directory produced bytes identical to both staged files.
[Application verification](lf-apply-verification.json) records the resulting
hashes. Shared source, corpus, shared tests and old reports were never promoted
or edited by this implementation. The earlier splitlines patch and its logs
remain here as historical files; the `-lf` patch/manifest and logs are current.

Patch SHA-256:
`40df1e798bc5ea26b2541682ad4820d3b212c64851562e4f467022ebc99a820d`.
[The patch manifest](patch-manifest-lf.json) records exact staged paths and hashes:

- `harness/phase3.py`: before
  `39d16e2ba220aad27ec0e67646e04aacbcdd9a9c28d3013f3f04ddefec789099`;
  after `7ad84976872ef5ee16fb1b96c3282b1c59902a59736606f78f662e41ecc47dfc`.
- `harness/test_phase3.py`: before
  `0bea853fcedcf6dd2724d057d88813440454183a64f643c944321c624f414e95`;
  after `9db8d2b054e1968d1a799cf721e167087d765c5529400743a664b0066d583b98`.

## Actual checks

[Baseline pure suite](baseline-pure.log): 27/27 passed.
[Patched pure suite](patched-pure-lf.log) and
[optimized pure suite](patched-pure-lf-optimized.log): each 29/29 passed, zero
failures/errors/skips. Cross-case negatives require byte identity, not merely
noncanonical output. [The any-known-context mutant](any-known-context-mutant-rejected.log)
failed the coordinated legacy identity assertion under `-O`, as required; this
expected failure is separate from the passing final patch.
[Outcome comparison](suite-outcome-comparison-lf.json): all prior pure outcomes
preserved after the deliberate rename, with two new passing tests.
[Entry-only F1 reproduction](one-entry-f1-reproduced.log): original test failed
at its explicit body-stall stream negative, as predicted. This is a retained
expected failure, not a failed final patch.

[Normal reduction](reduction-normal.json) and
[optimized reduction](reduction-optimized.json): each computed five passing
stored-capture expectations, equal real manifest stderr digests, 20 passing
nonself directed comparisons, 19 explicit noncanonical boundary/scope/opt-in
negatives, and 65 captured-data rejection controls. Comparisons include the
original bare reference against its canceled repeat (A/A), against release4's
candidate (the real differential), and the reverse direction (bare as candidate).
No capture was rerun. Each capture's nine independent controls target exit,
empty/extra/unexpected trace requests, empty/altered stdout, 58 000/71 000 ms,
and the prefix-phase error. Four differential controls alter the actual captured
request body, contract header, and parsed configuration before/after. The
single-request trace uses an extra-request control, because reordering one
request does not mutate it.

[Optimized fail-closed checks](failclosed-optimized.json): four injected faulty
results were rejected with `sys.flags.optimize == 1`, and none wrote a success
report. The [durable reducer](../p3-body-deadline-reduction.py) now records actual
computed lists and raises on failed checks even when `assert` is disabled. Its
[previous runner snapshot](historical-assert-reduction-runner.py) is retained for
historical provenance; the old JSON reduction remains untouched. The
[checked runner snapshot](checked-reduction-runner.py) binds the execution source
without replacing the durable script's normal path.

Both suite logs prove the runner imported from the requested cache directory;
patched tests imported
`/home/ajs/.cache/pb-phase7/body-deadline-r1-fixes/staged/harness/phase3.py`.
Dependencies `phase1`, `phase2`, and `run` imported from this repository's
`harness`, and the staged contracts symlink points to repository contracts.
The driver installed an audit hook to forbid executable launches. Local Python
fixture tests ran; six process-launching tests were excluded: four shell
RunnerTests, Go fixture parity, and the Go CLI oracle. **A whole-suite run with
the Go fixture is still outstanding.** Python 3.14.7 and interpreter flags are
recorded in each execution record. Unit-test elapsed summaries are not
performance evidence.

## Provenance and dispositions

[Supplemental original failure and runner identity](supplemental-original-failure-runner-identity.json)
embeds the original report's failing case entry and all three issue strings,
both exact report paths/hashes and runner identities, and all 27 original
artifact hashes. Both reports' runner hashes equal the computed frozen source
hash. The fresh controls are one single-case reference-check session with two
Go processes; neither fresh process was bare. The original evidence's stale
“active” field remains a historical snapshot, superseded by the old completed
reduction and these new checked reductions.

[Preservation checks](preservation-check.json) verify unchanged shared
source/test bytes, historical body/redirect reviews and evidence, both bound
body reports, and all 45 original/fresh capture artifacts. No original report
or manifest was overwritten. The test logs and reduction records bind their
source hashes; [file hashes](file-hashes.json) bind all new deliverables.

F2/F3 correction: symmetric bare stream acceptance is intentional. The body
change removes an independent text-mode suffix requirement, while differential
and A/A coverage still compare normalized complete stderr. Lookahead's prefix
expectation is tolerant only independently; with this body-only runner its bare
and suffixed prefix errors remain different. The historical reviewer wording
has not been rewritten. [The updated proposal](../../../phase7-body-deadline-proposal.md)
records the corrected disposition and the separately supplied Phase6 lookahead
failure/proposal, including its settled v2 patch
`/home/ajs/.cache/pb-phase6/lookahead-deadline-proposal-v2/proposal.patch`,
SHA-256 `68d5baa1bf38bdf7f59a7e5f05979f62d870f762b062cccafb623ec0ea1aa140`. That owner retains its evidence at
`/home/ajs/.cache/pb-phase6/retained-review-r6-full/phase3/cases/p3-deadline-text-empty-array-lookahead`;
no lookahead evidence or source is edited here. Its pending Opus review and
possible future patch composition are outside this task. Bare JSON body-stall
outcomes remain unknown and require separate observed evidence/review.

Read for this implementation: the full original assignment, body R1 review,
body proposal, original/proposed body evidence JSON, original reducer and
source-only patch; full prior redirect proposal and R1/R2 reviews; live
comparison/expectation/manifest/stored-loading code, deadline corpus and suite;
pinned Go setRequestCancel/cancelTimerBody code and pb wrapper/stream branches.
The old redirect manifest was read and preserved. Original/fresh body reports
were parsed for exact headers/case entries, and retained body artifacts were
hashed/loaded. This was source inspection and pure execution only.

Remaining before shared use: independent review of the combined exact patch and
evidence corrections; the full source-stamped suite with the fixture; parent
and Phase6 shared-runner coordination after companion phases4/5 complete; explicit parent **“body deadline edit
window open”**; then final successor retained replay on the final revision.
No production build/capture, CLI oracle, profiling, benchmark, or common lock
was used. Do not interpret any supplied campaign status as promotion.

## Reproduction

All commands ran from this worktree, with:

```sh
export TMPDIR=/home/ajs/.cache/pb-phase7/tmp
export GOTMPDIR=/home/ajs/.cache/pb-phase7/tmp
export CLAUDE_CODE_TMPDIR=/home/ajs/.cache/pb-phase7/tmp
export PYTHONDONTWRITEBYTECODE=1
```

The pure suite driver and results are retained both here and in the cache.
Use a **new** output stem on rerun; existing results are intentionally protected.
The actual patched invocation was:

```sh
python3 /home/ajs/.cache/pb-phase7/body-deadline-r1-fixes/run-pure-tests.py \
  --runner-dir /home/ajs/.cache/pb-phase7/body-deadline-r1-fixes/staged/harness \
  --test /home/ajs/.cache/pb-phase7/body-deadline-r1-fixes/staged/harness/test_phase3.py \
  --out /home/ajs/.cache/pb-phase7/body-deadline-r1-fixes/patched-pure-lf
```

The reducer ran once without `-O` into `reduction-normal`, and once with `-O`
into `reduction-optimized`:

```sh
python3 -O docs/evidence/phase7/p3-body-deadline-reduction.py \
  --capture-root /home/ajs/.cache/pb-phase7/retained-release4-phase3 \
  --capture-root /home/ajs/.cache/pb-phase7/oracle-p3-body-deadline-1 \
  --staged-runner /home/ajs/.cache/pb-phase7/body-deadline-r1-fixes/staged/harness/phase3.py \
  --out /home/ajs/.cache/pb-phase7/body-deadline-r1-fixes/reduction-optimized
python3 -O /home/ajs/.cache/pb-phase7/body-deadline-r1-fixes/check-failclosed.py
```

The fail-closed driver injects faulty computed functions **in memory** into its
loaded staged module. It edits no runner and must fail on those injections even
under `-O`. The final source/test patch remains body-only and byte-identical to
what was tested.
