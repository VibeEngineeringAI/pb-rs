# Transport review-wave process contracts

`harness/phase6_transport_wave.py` preserves the transport owner's 15 measured
Go scenarios as a separate process suite. It does not change the main 219-case,
56-case framing/refusal, or 16-case trailer inventories or references. It uses
the immutable r5 fixture and pinned Go standard binary, with the maintained
`raw_h2` scripting interface. No second server implementation is introduced.

The JSON cases assert exact stdout hash, bytes, rows and blank batch lines,
complete terminal error envelope, ordinary raw exit 1, discovery/auth/ticket,
configuration, DoGet count and actual connection/stream reuse. Only the existing
exact native HTTP/2 user-agent pair is counted separately from strict parity.
Connection/stream witnesses require complete fixture event and trace coverage
and matching exact summary counters. A transparent refusal must precede the
second request by less than 450 ms; this is a correctness check against the
application's 500 ms retry, not a performance comparison.

- Twelve F1 cases distinguish advertised gzip with flag 0 from a compressed
  message, identity/absent encoding, invalid flag 2, empty/full/split/partial
  bodies, a partial prefix, size overflow before flag interpretation, and a
  good batch delivered before the bad message.
- Two F2(a) cases send 101 empty DATA frames in one subscription and 60 in each
  of two subscriptions on one connection. Exact output guards against dropped
  records; the latter also checks stream 3 reuse.
- F3 sends GOAWAY after response headers before the first message, follows the
  transparent retry with a normal application reconnect, and requires three
  requests on two connections with stream IDs 1, 1, 3.

Five focused tests reject altered output, terminal whitespace, signals/timeouts,
lost event coverage, wrong connection/stream counts, delayed transparent retry,
auth/ticket/authority/configuration changes and unrecognized user-agents. A
process-free re-reduction of the owner's preserved pinned-Go/debug-candidate
captures passes all 15 remaining contracts. Raw stdout/stderr and result hashes
are bound in [the evidence manifest](evidence/phase6/transport-wave-debug-reduction.json).
This is historical debug evidence, not acceptance of a successor release.

Reproduce after building an immutable candidate:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=harness python3 -m unittest -v test_phase6_transport_wave
python3 harness/phase6_transport_wave.py --go <pinned-go> \
  --candidate <immutable-normal-release> --fixture <immutable-fixture-r5> \
  --out /home/ajs/.cache/pb-phase6/tmp/<fresh-output-directory>
```

The native runner captures Go and candidate twice per case, checks both
independently, retains raw artifacts and checks source/executable identities at
the end. Failed or interrupted reports remain incomplete/failed. No timeout
counts as success. The immutable R6 release passes all 15 cases in 60 native captures (Go and
Rust each repeated), with only the declared UA difference. The report and raw
identities are indexed by [focused R6 evidence](evidence/phase6/focused-review-r6.json). These short finite fatal streams do not prove
live delivery, throughput, fairness under arbitrary tiny-frame pressure, soak
memory behavior or cross-platform readiness. The separate invalid-header
capability limits and informational-response limitations receive no waiver here.
