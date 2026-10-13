# Phase 5 evidence

These are local Linux x86-64 loopback results, using synthetic accounts and
isolated administration state. They do not establish live-server behavior.
See [the implementation report](../../rust-phase5.md) for results and limits.

The compressed JSON reports contain raw per-process measurements, identities,
methodology, validity checks, distributions and comparisons. CSV files contain
individual measured samples. A Go A/A run is a correctness control, not a
performance baseline. Warmups are excluded from reported distributions.

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

Use newly built binary paths, not the development machine's absolute manifest
paths. Run functional checks before timing and avoid concurrent builds or
profilers. This development host runs other services, so scheduling noise and
fixture implementation costs remain limitations despite sequential randomized
client sampling.
