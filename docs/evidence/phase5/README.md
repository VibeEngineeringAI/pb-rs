# Phase 5 evidence

These are local Linux x86-64 loopback results, using synthetic accounts and
isolated administration state. They do not establish live-server behavior.
See [the implementation report](../../rust-phase5.md) for results and limits.

The compressed JSON reports contain raw per-process measurements, identities,
methodology, validity checks, distributions and comparisons. CSV files contain
individual measured samples. A Go A/A run is a correctness control, not a
performance baseline. Warmups are excluded from reported distributions.

The files listed first are the pre-fix release evidence for `7cda2ad`. The
`review-fixes-*` files at the end validate the current PR head, `4fcbd51`.

- `candidate-final-manifest.json`: Go, prior Rust and final candidate hashes,
  compiler versions, public version, source hashes and predeclared budgets.
- `final-p*.json.gz`: all five release compatibility gates.
- `final-deadline.json.gz`: the separate real 60-second administration deadline.
- `reference-final5.json.gz`: three Go captures per administration case before
  freezing `harness/contracts/phase5-reference.json`.
- `cap-experiment.json.gz` and `fanout-retained.json.gz`: the rejected 64-request
  cap and the passing implementation retaining full fan-out. The former is an
  intentionally failing experiment, not a release result.
- `admin-selfcheck.json.gz` and `shared-selfcheck.json.gz`: passing Go A/A
  controls for the performance corpora. Timing here may overlap functional
  tests and must not be used for performance claims.
- `performance-admin*`: 52 administration workloads, 5 excluded warmups and
  30 measured repetitions per Go/candidate binary. Prior Rust has no implemented
  administration baseline; its unsupported-command behavior is checked in a
  separate unmeasured guard, not used as a fast timing result.
- `performance-shared*`: 15 existing-command workloads, with Go, prior Rust and
  candidate, using the same repetition protocol.
- `contention-confirm*` and `contention-analysis.json`: the independent
  30-sample confirmation and ledger investigation of the large user refresh
  contention latency regression. `contention-ledger-*` and
  `contention-sample-*` retain representative raw equal-work samples; they are
  diagnostic examples, not substitutes for the full distributions.
- `shared-regression-*.json`: candidate/prior-Rust budget checks.
- `transport-oracle*.log`: Go 1.25.4 HTTP/HTTPS failure experiments underlying
  the native transport tests. Specific remaining differences are documented.
- Test logs: Rust unit/integration tests, Clippy and Python tests. Some Python
  tests deliberately exercise failure/skip paths; the final unittest summary
  determines whether those tests passed.

Review-fix validation of `4fcbd51` (see
[the implementation report](../../rust-phase5.md#review-fixes)):

- `review-fixes-manifest.json`: Go, frozen `7cda2ad` and fix binary hashes,
  meter/fixture hashes, toolchains, build environment, source hashes, budgets
  and the acceptance criteria applied, including the 5% confirmation rule.
- `review-fixes-validation.json`: test, case and sample counts.
- `review-fixes-p2.json.gz` to `review-fixes-p5.json.gz` and
  `review-fixes-deadline.json.gz`: complete gate and 60-second deadline reports.
- `review-fixes-p1-summary.json.gz`: the Phase 1 gate (2,018 cases, 11 guards)
  as a per-case summary. It records the name, issues and exit codes for every
  case, and the SHA-256 of the 19.8 MB full report. The full report compresses to
  2.9 MB and has the same corpus as `final-p1.json.gz`, so it is kept locally
  and not committed.
- `review-fixes-shared.json.gz` and `review-fixes-admin.json.gz`: 15 shared and
  52 administration workloads, each measuring Go, frozen `7cda2ad` (label
  `prior`) and the fix (label `rust`). `review-fixes-admin-selfcheck.json.gz` is
  the passing Go A/A control that the administration run reused through
  `--oracle-report`. Its `derived_from` field records how it was derived.
- `review-fixes-confirm-shared.json.gz` and `review-fixes-confirm-admin.json.gz`:
  fresh-session reruns of every workload/metric whose primary median rose by
  more than 5%. These subset reports mark themselves partial.
- `review-fixes-analysis.json.gz`: a lossless bundle of the frozen/fix budget
  comparisons, Go comparisons, flagged lists and confirmation analyses.
- `review-fixes-startup.json.gz`: `pb --version` startup samples. Each binary
  was measured 500 times with the inherited soft `RLIMIT_NOFILE` equal to the
  hard limit, and 500 times with it at 1024.
- `review-fixes-probes.json`: the error-trim scaling probe and 1,500-user FD
  probe, before and after the fixes.
- `review-fixes-*.log`: Rust tests, Clippy, Python tests and the targeted
  Phase 3 fixture-oracle rerun.
- `review-fixes-refresh-ab.md`: the focused A/B report on the small refresh
  workload increases. Controls include a byte-identical rebuild of `7cda2ad`,
  equal page-cache treatment of both executables, pinned `perf stat` counters,
  smaps and strace. See
  [the follow-up summary](../../rust-phase5.md#refresh-workload-ab-follow-up).
- `review-fixes-refresh-ab.json.gz`: data for that report. It holds nine
  session reports (8,032 measured samples) and every analysis verbatim. It
  also holds the cache-treatment, smaps, strace, limits and build records, and
  the before/after load averages. Each per-sample run is reduced to its timing,
  resource and counter columns plus any issues. Each report's `source` field
  records the path, size and SHA-256 of the full local report. The bundle
  explains its derivation and lists the omitted files with their hashes.

The Phase 2–5, deadline, shared, administration, administration self-check,
confirmation and startup gzip reports preserve their source bytes, apart from
the path normalization described below. `review-fixes-p1-summary.json.gz` is a
derived summary, `review-fixes-analysis.json.gz` is a lossless JSON bundle and
`review-fixes-refresh-ab.json.gz` is a derived bundle. Their source hashes and
derivations are recorded in the artifacts. Harness sample CSV and `report.md`
renderings are not committed because they duplicate the JSON.

Absolute paths from the development host are normalized in every
`review-fixes-*` file, including source and provenance fields, reproduction
commands, logs and the A/B report. `$CHECKOUT` stands for the repository
checkout that ran the review-fix validation, so `$CHECKOUT/bin/pb-meter` is
`bin/pb-meter` in this repository. `$PB_CACHE` stands for the local evidence
cache named below, so `$PB_CACHE/fix-candidate/pb` is the fix binary snapshot
there. The normalization changes nothing else. Replacing the two placeholders
with the original directories gives back each source file exactly, so the
recorded sizes and SHA-256 values still identify the untouched originals. The
logs and `review-fixes-refresh-ab.md` are otherwise verbatim, except that the
A/B report and the Rust test log each lost a trailing blank line to pass
`git diff --check`.

Inspect a compressed report without installing additional packages:

```sh
python3 -m gzip -d < performance-admin.json.gz | python3 -m json.tool
```

Complete stdout/stderr, request/state/commit ledgers, fixture events, temporary
configuration snapshots and intermediate investigations are retained locally
under `/home/ajs/.cache/pb-phase5/`. Large duplicate per-sample payload files are
not embedded in this repository. Their hashes and local artifact paths are in
the reports; all sample metrics are published here. The harness recreates them
with `--artifacts all` (the default).

Reproduction requires the recorded Go/Rust toolchains, builds described in
`harness/README.md`, native `bin/pb-meter`, a new output directory and sufficient
disk-backed temporary space. Build prior Rust from `411e56f`, and preserve it
before building the candidate. Set identical `PB_VERSION=v0.0.0-harness` public
metadata for both Rust builds; the Cloud variants use the public synthetic
`harness-orchestrator-token`.

```sh
python3 harness/run.py run --suite docs/evidence/phase5/regression-suite.json \
  --binary go=GO --binary prior=PRIOR_RUST --binary rust=CANDIDATE \
  --fixture bin/pb-fixture --meter bin/pb-meter \
  --warmup 5 --repetitions 30 --out NEW_SHARED_OUT
python3 harness/phase5_workloads.py run --go GO --go-cloud GO_CLOUD \
  --candidate rust=CANDIDATE --candidate-cloud rust=CANDIDATE_CLOUD \
  --prior PRIOR_RUST --meter bin/pb-meter --warmup 5 --repetitions 30 \
  --self-check-repetitions 2 --out NEW_ADMIN_OUT
```

The review-fix runs replace prior Rust with the frozen `7cda2ad` build. The shared
run uses `--binary prior=FROZEN`. The administration run omits `--prior` and
passes `--candidate prior=FROZEN --candidate-cloud prior=FROZEN_CLOUD` alongside
the fix. Its `--prior` scope guard expects unsupported-command stubs, and the
frozen build already implements administration. Confirmation reruns add
`--case`/`--workload` for each flagged workload, then compare with
`harness/run.py compare ... --max-regression-percent 5 --min-regression 0`.

Use newly built binary paths, not the development machine's absolute manifest
paths. Run functional checks before timing and avoid concurrent builds or
profilers. This development host runs other services, so scheduling noise and
fixture implementation costs remain limitations despite sequential randomized
client sampling.
