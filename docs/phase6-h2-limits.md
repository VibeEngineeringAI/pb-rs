# HTTP/2 response capability limits

The two invalid-header limits below are separate from the informational-response limitation and
from the Flight parity suites. They describe HTTP/2-invalid producer responses
accepted by pinned grpc-go 1.65 but rejected by the maintained h2 0.4.20 stack.
They are outcome differences, not diagnostic-only differences or parity passes.

| ID | Exact characterized trigger | Pinned Go | Rust R5 |
| --- | --- | --- | --- |
| H2-CL-1 | `content-length: 1` followed by a schema and a one-row batch whose body exceeds that length | Renders the row, then reaches fixture InvalidArgument | No stdout; Internal before the first message, with `h2 protocol error: error reading a body from connection` |
| H2-CL-2 | HTTP/2 response includes `connection: keep-alive` | Renders the row, then reaches fixture InvalidArgument | No stdout; Internal before the first message, with `h2 protocol error: http2 error` |

The decision retains the dependency's enforcement rather than adding another
HPACK decoder or a fork of the transport. This does not exempt ordinary valid
HTTP/2 responses. Repeated unpadded empty DATA frames have a separate bounded
filter correction, with R6 release replay passing. They are not covered
by these limits. The settled filter tracks open header blocks and retains
illegal interleaving for h2 to reject; raw-server and disabled-fix unit controls
cover that correction. The fresh Opus round-3 review closes that correction.
The library's small-DATA framing-overhead budget under a slow sink is a
source-supported unknown; no pressure result establishes its practical effect.

The fresh [transport correction review](phase6-review-wave-transport-opus-r2.md)
also identified **H2-CL-3**, a distinct valid-wire boundary: more than 100 DATA
frames with an empty payload after removing padding can hit h2's per-connection
empty-frame cap. For example, a PADDED frame of length 1 containing a zero pad
length carries one flow-controlled byte but no data. Go returns its window;
h2 counts it. The unpadded filter deliberately preserves these frames, since
discarding their bytes would corrupt flow-control accounting. The decision is
to retain the maintained stack's flow-control enforcement. This is currently a
Rust capability limitation; the contrasting Go behavior is source-supported. Rust raw-server unit probes reproduce the
100/101 boundary for length-1/pad-0 and length-2/pad-1 frames; no pinned-Go
process probe has run. It is **not**
one of the four measured invalid-header cases below and is not claimed as a
tested pass. No broad exemption for ordinary records is added.

`harness/phase6_h2_limits.py` uses the immutable r5 fixture's existing `raw_h2`
endpoint. Six cases cover both text and JSON: two ordinary controls and four
limit cases. It independently checks exact stdout bytes/hash/rows/batch framing,
full stderr envelope and status, raw exit 1, one DoGet, ticket/auth, discovery,
authority and configuration. Each role runs twice; both Go A/A and Rust A/A
must match. Only the existing exact native user-agent difference is separated.
The four limits are excluded from full parity even when their measured behavior
matches the explicit expectations. A timeout or different failure is rejected.

Actual R5 result: 24 native captures; two control remaining-contract passes,
four verified limits excluded from parity, zero unaccepted failures. All six
have the existing native wire-UA difference; `full_parity_passed` is false.
The fresh report and identities are indexed in
[h2-limits-r5.json](evidence/phase6/h2-limits-r5.json). These results precede the
transport correction. The frozen R6 replay also passes both controls and verifies
all four excluded limitation outcomes; see [focused R6 evidence](evidence/phase6/focused-review-r6.json). Neither run
establish live-server delivery nor provide performance measurements.

Four focused unit tests check the enumerated scope and rejection of changed
output, status, whitespace, signal outcome, timeout, auth, ticket, request
count, authority, config and UA. Original 291-case references remain unchanged.

```sh
PYTHONPATH=harness python3 -m unittest -v test_phase6_h2_limits
python3 harness/phase6_h2_limits.py \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go \
  --candidate /home/ajs/.cache/pb-phase6/candidate-review-r5/pb \
  --fixture /home/ajs/.cache/pb-phase6/tmp/fx/pb-fixture-r5 \
  --out /home/ajs/.cache/pb-phase6/tmp/h2-limits-FRESH
```

Use a new output directory and an explicitly preserved successor binary for
later validation. The runner verifies the pinned Go and fixture hashes and
retains candidate/runner identities before and after execution. The source
review and original witnesses are in
[the Opus transport review](phase6-review-wave-transport-opus.md).
