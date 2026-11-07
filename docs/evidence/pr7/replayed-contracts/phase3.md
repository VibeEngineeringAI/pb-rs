# Phase-three SQL query contracts

`harness/phase3.py` is the fixed Go/Rust differential gate for noninteractive
`pb sql run`: request preparation (lexical rewrite, raw time forwarding,
validation order), the authenticated `POST api/v1/query` exchange (four auth
modes, redirects, OAuth refresh with body replay, gzip), text and JSON
streaming output, non-200 previews, early completion, signals, broken pipes,
output-device failures and the stderr-TTY spinner. The reference is the
unchanged Go source at `07af390`, built with Go `1.25.4` (`bin/pb-go`,
`bin/pb-go-cloud`). The corpus is code (`phase3.corpus()`); seeds are fixed in
`phase3.SEEDS` and recorded in every report.

## Expectations before parity

Every case pins what Go must do before a candidate is compared: termination
(exit code or signal), the ordered `METHOD path` list, and usually exact
request-body bytes, exact stdout/stderr bytes or a digest, and config effects.
Expected request bodies and most success outputs are produced by a small
Python model of `cmd/query.go` and Go's `encoding/json`/`unicode` behavior
(`quote_stream_names`, `quote_fields_with_dots`, `ensure_default_limit` applied
twice, `json.Marshal` escaping, float64 formatting, `MarshalIndent`, sorted
keys, last-duplicate-wins, per-byte U+FFFD replacement, text no-rows/empty
rules, the 65,536-byte preview). The Go capture must agree with the model;
a disagreement fails the reference, never the candidate. Go's own decoder
error strings are pinned literally from the capture. The reference runs twice
by default and any difference fails the case.

## Tiers and counts

| Tier | Cases | Default |
| --- | --- | --- |
| `gate` | 675 | run |
| `full60s` | 9 | `--include-full60s` (80 s per-case timeout above Go's 60 s client deadline) |
| `known-divergence` | 13 | `--include-known-divergence`; documented residuals, never counted as gate passes |

`known-divergence` cases keep their full Go pins and run against Go in every
capture that includes the tier; only the candidate comparison is outside the
gate. They are listed with reasons in `phase3.KNOWN_DIVERGENCES` and in every
report's scope:

- Empty HTTP/1.1 reason phrase (6 cases, plus status 299, which has no standard
  reason): Go reports `200 ` with the raw delimiter; hyper does not expose it.
- Connection reset mid-body (text, JSON): Go `read tcp A->B: read: connection
  reset by peer` against Rust `Connection reset by peer (os error 104)`. Partial
  stdout, error code and retryability match.
- EOF before response headers: hyper cannot reproduce Go's distinct diagnostics.
- Malformed status code: Go echoes the raw token (`abc`); hyper reports only
  `invalid HTTP status-code parsed`.
- Non-UTF-8 argv: the phase-one `String` parser boundary.

Fixable diagnostics (for example truncated chunked bodies reporting
`unexpected EOF`) stay in the gate.

Gate groups: named rewrites (63) and 160 seeded rewrites, time forwarding,
validation precedence, profiles/request construction, auth headers, text
bytes (incl. invalid UTF-8, lookahead faults, gzip, truncation, RST, malformed
chunking), JSON element types/numbers/escaping/depth/malformed and 96 seeded
valid/malformed arrays, statuses (custom, empty and absent reason phrases),
previews, redirects (307/308 replay, 301/302/303 to GET, loop, cross-host
credential stripping on 127.0.0.2), refresh (cloud build and unconfigured
standard build), causal barriers, early completion, seeded chunk splitting,
slow and blocked readers, signals and SIGPIPE, `/dev/full`, PTY spinner and
large workloads (10,000/100,000 rows, one 8 MiB element, 4 MiB leading
whitespace).

Candidate-only deferred boundaries (11, run by unfiltered non-self-check
`compare`): `-i`/`--interactive` (including `-i -o yaml`, which must not report
output validation) and nonempty `--save-as` (including whitespace). Each must
exit 1 with empty stdout, an explicit `... is not implemented in this Rust
phase` error (a `COMMAND_FAILED` JSON envelope on stderr when JSON is
requested), make no request to a fixture that would accept `/api/v1/query` and
`/api/v1/filters`, and leave HOME byte-identical. Go fails these checks by design;
they are scope assertions, not parity. Empty-SQL and invalid-output cases with
`--save-as` are ordinary Go parity cases because Go rejects them before saving.

## What gates

- Termination: `signal:13` differs from `exit:141`; `signal:2` from `exit:130`.
- Exact stdout bytes (normalized digest; outputs over 16 MiB use the raw
  incremental digest and size; stdout is spooled to disk, cap 256 MiB) and exact
  stderr bytes.
- Requests: raw target, contract headers (`authorization`, `x-api-key`,
  `x-p-tenant`, `cookie`, `content-type`, `accept`, `accept-encoding`,
  `content-length`, `transfer-encoding`), raw body bytes, framing and listener.
  User-Agent and header order/case are advisories.
- Parsed config before/after plus the phase-two byte-rewrite guard.
- Causal actions (`required`): e.g. the barrier withholding the remainder is
  released only after one complete element is observed on stdout; the fixture's
  final-body milestone must follow first stdout.
- Early completion: JSON after `]` and previews after 65,537 bytes must finish
  within 5 s while the body tail stalls forever.
- Blocked reader: with stdout unread for 1.5 s, the candidate's consumed
  response bytes may exceed Go's by at most 16 MiB (declared before any
  candidate measurement).
- Spinner (PTY stderr): frames are collapsed for comparison, but frames must
  cycle `| / - \` from `|`, the clear sequence must be present unless the case
  is interrupted, and activation cases require at least two frames for Go and
  the declared minimum for the candidate. A clear-only candidate fails.

Normalization is limited to the temporary HOME, fixture origins
(`http://<FIXTURE>`, `<FIXTURE-HOST>`, `<ALIAS>`), and per-case declared
nondeterminism: spinner frame runs, `PB_DEBUG_HTTP_HEADERS` map order, and the
client ephemeral port in connection-reset errors. Raw artifacts are always kept.

### The 60-second deadline tier

Go's `net/http` reports the same `Client.Timeout` as `context deadline
exceeded` or `net/http: request canceled`, depending on a race between the
deadline timer and the pending read; both causes were observed across two Go
runs of the same case. For `full60s` cases only, exactly those two causes are
normalized to `<CLIENT-TIMEOUT>`, keeping the surrounding context, the
`(Client.Timeout exceeded while awaiting headers)` /
`(Client.Timeout or context cancellation while reading body)` suffix and the
structured `TIMEOUT` code. Two text cases also produced the exact bare
`context deadline exceeded` message in repeated Go runs: error-preview reading
and paced text streaming. Only those two complete messages, with their exact
error contexts, are mapped to the corresponding body-timeout form. Bare
request-cancellation messages, other cases, extra text and JSON are unchanged.
Every `full60s` run must take 59–70 s. In
`deadline-not-reset-per-chunk` (one byte every 4 s), the bytes printed before
the deadline may differ by one: the first byte is sent with the headers and
one more every 4 s, so 15 bytes always precede the deadline and the 16th races
it. Each binary's stdout must be a 15- or 16-byte prefix of the declared body
(`phase3.paced_lengths`). With the default manifest, out-of-tier `full60s` cases
are reported as `NOT FROZEN` (live Go runs and source pins only), never as
manifest-verified. These observed wording differences are Go diagnostic races.

## Frozen reference

```sh
export TMPDIR=$PWD/target/tmp
# 1. Capture and validate Go (both builds), no candidate:
python3 harness/phase3.py check-reference --reference bin/pb-go --cloud-reference bin/pb-go-cloud \
  --out harness/results/phase3-go --manifest none
# 2. After review, freeze digests (rejects failures, filters and identity issues):
python3 harness/phase3.py freeze harness/results/phase3-go --out harness/contracts/phase3-reference.json
# 3. Candidate against the frozen capture (no Go execution):
python3 harness/phase3.py compare --baseline harness/results/phase3-go \
  --candidate target/release/pb --cloud-candidate target/harness-cloud/release/pb --out harness/results/phase3-rust
# or live: --reference bin/pb-go --cloud-reference bin/pb-go-cloud instead of --baseline.
# Opt-in tiers: --include-full60s (≈9 × 60 s per binary run), --include-known-divergence.
```

When `harness/contracts/phase3-reference.json` exists it is verified by default:
the case-definition hash of its frozen tiers, the platform (`linux-x86_64`),
reference and cloud-reference executable SHA-256, and each case's
termination/stdout/stderr/request/config digests. The pinned SHA requires
reproducible oracles: Go 1.25.4, `-trimpath -buildvcs=false` (otherwise the
checkout's VCS stamp changes the binary on every commit) and the Makefile's
version/token flags. Another platform must capture and freeze its own
reference explicitly; a manifest from a different platform is a setup error,
not a silent bypass. Case identity is per tier (`tier_sha256`), so editing the
opt-in tiers does not invalidate a gate capture, while a baseline must have
captured every requested tier. `--baseline` accepts only a
complete, unfiltered, all-passing capture of the current corpus without
identity issues; every loaded reference has its raw and normalized stdout,
stderr, trace and milestone artifacts re-digested against the source report,
every recorded result field compared, its source-manifest entry checked, and
the current expectations and frozen manifest reapplied.

Exit 0 passes; 1 is a compatibility, expectation, stability, boundary or
identity failure, or skips without `--allow-skips`; 2 is a setup error. Skips
come from requirements: cloud builds, non-root, `/dev/full`, PTYs and a
bindable `127.0.0.2` (Linux).

## Fixture

`phase3.py serve` is a loopback raw-socket HTTP/1.1 fixture: per-route method,
path, header and use-count matching; status line with custom, empty or absent
reason (or a raw line); Content-Length, chunked or close framing; gzip and
deliberate gzip corruption; body scripts (`send`, `delay_ms`, `barrier`,
`stall`, `abort` FIN/RST); header delay/barrier/stall; a 127.0.0.2 alias
listener. Control endpoints: `/__harness/trace` (run.Fixture-compatible),
`/__harness/milestones` and `/__harness/events` (CLOCK_MONOTONIC), and
`/__harness/release?name=`. Unmatched requests get 500 and are marked
unexpected.

`harness/fixture` (Go, for run.py) gains backward-compatible route fields
`generate` (deterministic body, same expansion as `phase3.expand_generate`)
and `gzip`, a `/__harness/milestones` endpoint, and `--capabilities`. Routes
without the new fields keep their previous framing and bytes.

## Benchmark export

`compare`/`check-reference` write `benchmark-suite.json`;
`phase3.py suite --out FILE [--fixture BIN]` exports it standalone and checks
the fixture's capabilities. It holds 22 run.py workloads with explicit SQL
limits: 1/500/10,000/100,000 fixed-size rows in both modes, gzip, 50 ms delayed
headers, paced 16 KiB chunks, the 8 MiB element and 4 MiB leading-whitespace
memory exceptions (`time_first_json_element: false`, so the synchronous collector
does not add backpressure), a 1 ms slow reader, and empty results. Entries carry
`json_array_rows`, `fixture_response_body_bytes`, `expected_stdout_sha256`/`bytes`
from the model, `max_output_bytes` and `fixture_capabilities`. They require a
pb-fixture built from this source (`--capabilities`); a stale fixture would serve
empty bodies.

## Scope exclusions

- Interactive mode and nonempty `--save-as`: boundary checks only.
- TLS, certificate trust and proxies (Go bypasses proxies for loopback): covered
  by native Rust transport tests, not this corpus.
- HTTP/2, 1xx responses, `Expect: 100-continue` and trailers.
- Spinner frame counts/timing; per-sample CPU/RSS are recorded but do not gate.
- The `full60s` and `known-divergence` tiers are outside the default gate.
- A JSON element of exactly Go's maximum depth succeeds but renders ~200 MB; only
  the exceeded-depth error is pinned.
- Live self-hosted validation (P3.4) is separate and never uses this fixture.
