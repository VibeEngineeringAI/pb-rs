# Phase-four fixture contract

`phase4.py` reuses the accepted phase-three isolated profile/process runner and scripted local HTTP fixture. Every run gets a fresh HOME and fixture; only loopback requests are used. Cases independently assert requests, raw sorted/escaped query targets, output bytes or projection, exit status and unchanged configuration. The saved cases assert request order and decoded body fields, distinguish standalone failure from warning-only execution/save failure, and prove successful save returns without waiting for EOF.

The corpus includes every PromQL operation and both aliases (`ps`, `sql ls`) in text and JSON, fixed absolute times, optional invalid-time omission, positional overrides, repeated selectors, negative counts, path joining, JSON lexemes/duplicate keys/HTML, malformed and wrong shapes, unsupported result types, numeric/string/bool/null samples, Unicode splitting, gzip, redirects, auth modes and body-read failure atomicity. Source pins: `cmd/promql.go`, `cmd/queryList.go`, `cmd/query.go`, `pkg/model/promql.go` at the baseline captured by the parent phase-four build.

Use prebuilt binaries (the reference build must precede implementation):

```
TMPDIR=$PWD/target/phase4/tmp python3 harness/phase4.py check-reference --reference bin/pb-go-pinned --cloud-reference bin/pb-go-cloud-pinned --out target/phase4/go-reference
python3 harness/phase4.py freeze target/phase4/go-reference
TMPDIR=$PWD/target/phase4/tmp python3 harness/phase4.py compare --baseline target/phase4/go-reference --candidate bin/pb-go-pinned --cloud-candidate bin/pb-go-cloud-pinned --self-check --out target/phase4/go-self
TMPDIR=$PWD/target/phase4/tmp python3 harness/phase4.py compare --baseline target/phase4/go-reference --candidate target/release/pb --cloud-candidate target/harness-cloud/release/pb --out target/phase4/rust-compare
python3 -m unittest discover -s harness -p test_phase4.py
```

The reference command executes Go twice and rejects instability. Captures contain stdout/stderr raw and normalized bytes, ordered HTTP traces, milestones, configuration snapshots and binary identities. `freeze` rejects partial/failed/changed-corpus captures. Baseline comparison checks stored artifact signatures before comparing candidate stdout/stderr, termination, request headers/raw bodies and configuration effects. The JSON manifest is an executable-result freeze, not merely agreement between two implementations. The runner never silently treats an excluded test as passing.

The default corpus has 159 gate cases. It includes Cloud refresh/configuration mutation, delayed EOF, PTY output, deep JSON, typed null/duplicate/type-error rules and validation precedence. PromQL run errors cover text and JSON for typed HTTP failures, invalid response bytes, and untyped envelope errors: text keeps the `request failed: ` wrapper and raw bytes, typed HTTP JSON retains the original public message, and untyped JSON includes the wrapper. Rust transport tests separately assert HTTP/1 negotiation against a TLS server offering HTTP/2, and the metrics transport policy disables proxy and default connect/TLS timers. Relative-clock boundaries use the injectable time helper; the workload suite supplies large response/scaling checks. Exact spinner scheduling is not deterministic. Existing phase-three gates remain independently required. Live endpoint availability and account mutation are outside fixture acceptance.

The default gate excludes two real 120-second deadline cases. Run `--include-full120s` explicitly; each is repeated against Go, so reference capture takes about eight minutes. Signal and `/dev/full` cases run in the default gate. Candidate-only guards assert the PromQL interactive UI and absent/exactly empty saved-list output remain deferred. Whitespace saved-list output selects text and is executable.
