# Phase6 informational HTTP/2 Flight capability limit

The parent explicitly excludes informational HTTP/2 Flight responses from the
supported protocol contract. This is the **full output, failure-stage and
terminal-status divergence** identified by IA-2 in
`phase6-interim-source-audit.md`. It is not a text-only diagnostic exception.
The existing 219-case main suite, 56 review3 probes, 16 trailer probes and
their Go references are unchanged. This new evidence is not added to their
parity totals.

Locked Hyper 1.12 exposes its informational-response callback only for HTTP/1.
The h2 response future's informational poll is not surfaced through the
maintained Hyper/tonic path. The parent's scoped decision avoids a stack fork
or a parallel HPACK parser. This limit does not establish behavior for every
1xx code, multiple informational responses or indefinitely live streams.

## Bounded process evidence

`harness/phase6_protocol_limits.py` reuses the real immutable r5 fixture's
`raw_h2` endpoint and the existing bounded Phase6 process helpers. It adds no
server implementation or product CLI. Each case has a fresh HOME/config,
synthetic Basic auth, one discovery GET and one physical DoGet with the exact
dataset ticket. Its five-second collector deadline is a failure bound, never
a passing result. Captures inherit the 32 MiB stdout, 1 MiB stderr and 64 KiB
prefix limits. Fixture panics, changed executables, unexpected requests,
configuration changes, signals and capture failures invalidate evidence.

Both text and JSON are exercised with these two scripts:

| Flight response script | Pinned Go outcome | Review-r4 Rust outcome |
| --- | --- | --- |
| 200 gRPC headers, basic schema, one row, fatal status 3 | Exact row plus blank batch separator; established-read InvalidArgument | Same output and terminal |
| 103 gRPC headers, then the identical 200/schema/batch/status-3 sequence | Empty stdout; Unknown at DoGet response headers | Exact row plus blank separator; established-read InvalidArgument |

The 103 response includes `content-type: application/grpc`, avoiding an
additional missing-content-type diagnostic. Go's exact message is:

```text
rpc error: code = Unknown desc = unexpected HTTP status code received from server: 103 (Early Hints)
```

The later fatal message is:

```text
rpc error: code = InvalidArgument desc = phase6 protocol limit terminal
```

The one-row stdout is exactly 162 bytes in either output mode, including
`\n\n`. Go's informational case has zero rows and zero batches; Rust has one
complete row and one batch. Both processes have raw wait status 256 and exit
1. Matching exit codes do not hide the missing Go output or the later Rust
failure. Stage labels are supported by these independently asserted output
bytes and exact terminal diagnostics, not by fixture Send completion or the
text UI's “watching” message.

The runner checks exact JSON envelope bytes and text stderr, including ANSI
clears, carriage returns, usage text and whitespace. It independently checks
Go and Rust expectations, then retains all raw signature difference keys and
the separate stage/status/row/batch difference keys. Requests, authentication,
ticket, configuration, authority and raw status must match. No generic
transport, status, output or whitespace normalization is applied.

The existing native h2 user-agent difference is retained as a separate wire
ledger: exactly `grpc-go/1.65.0` versus `tonic/0.14.6`, one DoGet each. Any
other value or request count fails. Even the controls therefore do not count
as strict wire parity.

## Reports and identities

Initial maintained-fixture evidence:
`/home/ajs/.cache/pb-phase6/tmp/harness-protocol-limits-r4-v1/report.json`.
The cached-Go rerun is independently retained at
`/home/ajs/.cache/pb-phase6/tmp/harness-protocol-limits-r4-cached-v1/report.json`.
Both reports complete all four cases with:

- Two controls passing the remaining contract.
- Two declared limitation cases, both verified and excluded from full parity.
- Four explicit wire user-agent differences, zero strict parity passes.
- Zero unaccepted failures; `full_parity_passed` remains false.

The report includes per-case Go and candidate signatures, exact asserted
outcomes, raw differences, outcome differences and raw-artifact paths. Cached
Go reuse validates the complete inventory, fixture hash, pinned Go identity,
result-file hash, stdout/stderr hashes and independent expectations. A later
candidate that changes either outcome fails the fixed evidence contract and
requires explicit review; the runner does not silently broaden this limit.

| Artifact | SHA256 |
| --- | --- |
| Go `07af390` standard baseline | `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314` |
| Review-r4 standard candidate | `fe26926a98cba5e00774e26c5eab33c7c35ebd570ddaec7ceb38ddef663ecc43` |
| Immutable fixture r5 | `c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314` |

The fixture build manifest is
`/home/ajs/.cache/pb-phase6/tmp/fx/pb-fixture-r5.manifest.json`.
The earlier six-process source-audit proof remains at
`/home/ajs/.cache/pb-phase6/interim-source-audit/process-results.json`.
That separate dictionary-shaped probe is supporting evidence; this runner's
basic schema isolates IA-2 from the independently assigned IA-1 session fix.
Neither evidence set establishes whole-phase acceptance or performance.

## Repeatable commands

Use a fresh output directory. All generated artifacts stay under the cache.

```sh
export TMPDIR=/home/ajs/.cache/pb-phase6/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
python -m unittest discover -s harness -p test_phase6_protocol_limits.py -v
python harness/phase6_protocol_limits.py \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go \
  --candidate /home/ajs/.cache/pb-phase6/candidate-review-r4/pb \
  --fixture "$TMPDIR/fx/pb-fixture-r5" \
  --out "$TMPDIR/harness-protocol-limits-r4-fresh"
```

For a future immutable candidate, reuse validated Go captures without running
Go again:

```sh
python harness/phase6_protocol_limits.py \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go \
  --candidate /absolute/path/to/new/immutable/pb \
  --fixture "$TMPDIR/fx/pb-fixture-r5" \
  --go-evidence "$TMPDIR/harness-protocol-limits-r4-v1/report.json" \
  --out "$TMPDIR/harness-protocol-limits-new-candidate"
```

The five unit tests verify separate inventories, finite scripts, limitation
exclusion/counting, changed stdout/status despite exit 1, request/auth/config
and capture failures, exact wire/whitespace checks and cached-artifact tamper
rejection. The cached-Go CLI path was also exercised against review-r4. No
benchmarks, profiling, quiet-window measurement, commits or shared source
changes are part of this task.
