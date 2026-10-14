# Phase-five administration workloads

Warm-cache, fresh-process sequential samples; seeded randomized binary order per round.
Fixture state/config reset before every warmup and sample (control); setup, reset, ledger capture and artifacts are outside timing.
Fan-out siblings are compared as a multiset and map-ordered role sequences with multiplicity; everything else is exact.
p95 from 30 samples is descriptive; bootstrap intervals are pointwise and exploratory.

**Partial run (--workload subset): not a complete phase-five report.**

## go-self-comparison (passed)

Purpose: Go A/A correctness gate; 0 warmup and 2 measured rounds per binary.
Workloads with raw order/output variation between samples: 1; skipped (failures): none.

| Workload | Binary | Valid | Wall median / p95 ms | CV | CPU ms | Peak RSS MiB | First request ms | Request→stdout ms | Max in-flight | Requests | 401s (max) | Logical bytes |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| p5-refresh-user-list-large-contention-json | go-a | 2/2 | 765.126 / 767.374 | 0.005 | 556.137 | 108.111 | 29.804 | 730.666 | 31 | 2002 | 1000 | 3787062 |
| p5-refresh-user-list-large-contention-json | go-b | 2/2 | 777.702 / 815.208 | 0.076 | 520.764 | 100.879 | 30.339 | 741.192 | 21 | 1825 | 1000 | 3783345 |

### A/A ratios (correctness gate; too few samples for inference)

- p5-refresh-user-list-large-contention-json: go-b/go-a — wall_ms 1.016× [0.959, 1.074], Δ +12.576; cpu_ms 0.936× [0.917, 0.955], Δ -35.373; peak_rss_bytes 0.933× [0.919, 0.947], Δ -7583744.000

## comparison (passed)

Purpose: measurement; 5 warmup and 30 measured rounds per binary.
Workloads with raw order/output variation between samples: 1; skipped (failures): none.

| Workload | Binary | Valid | Wall median / p95 ms | CV | CPU ms | Peak RSS MiB | First request ms | Request→stdout ms | Max in-flight | Requests | 401s (max) | Logical bytes |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| p5-refresh-user-list-large-contention-json | go | 30/30 | 786.758 / 886.630 | 0.106 | 551.161 | 106.062 | 28.333 | 755.010 | 66 | 2002 | 1000 | 3787062 |
| p5-refresh-user-list-large-contention-json | rust | 30/30 | 900.917 / 957.891 | 0.082 | 455.621 | 37.041 | 1.847 | 897.813 | 115 | 2002 | 1000 | 3787062 |

### Ratios (median, bootstrap 95% interval)

- p5-refresh-user-list-large-contention-json: rust/go — wall_ms 1.145× [1.093, 1.169], Δ +114.159; cpu_ms 0.827× [0.808, 0.852], Δ -95.540; peak_rss_bytes 0.349× [0.343, 0.360], Δ -72374272.000

## Unavailable or unmeasured quantities

- network_socket_bytes: N/A — loopback socket/TLS bytes are not captured; request/response bytes are logical HTTP bodies (response bytes are what the fixture wrote, not what the client read)
- heap_allocations: N/A — release runs are uninstrumented; use the diagnostic builds for allocation attribution
- fd_writes: N/A — requires strace; proc_io_syscw counts write-family calls only and excludes send/recv
- first_useful_result_ms: N/A — no command-specific useful-result predicate for administration output
- Per-workload N/A metrics are listed under `summaries.*.unavailable` in report.json.
- Block input/output counters are wait4 operations, not storage, socket or network bytes.

## Limitations

- The fixture is a Python ThreadingHTTPServer in a separate process; its scheduling and per-request cost are identical for every binary but add latency and noise relative to a production server.
- Control-mode reset reuses one fixture process per workload. The previous CLI has exited and every handler has finished before the model, ledger and fault budgets are rebuilt from the scenario. The fixture process is warm rather than fresh. `--fixture-reset process` restores per-sample fixture processes.
- Concurrent detail arrival order is scheduler dependent; only membership, multiplicity and the list-before-detail barrier (same-clock arrival after the list body write) are compared within a fan-out.
- Refresh contention: the number of rejected siblings is scheduler dependent; it is recorded (rejected_401_requests) and excluded from the oracle. Every causal constraint is enforced: one grant after the first 401; replay exactly once after each rejection; byte-identical replays; no rotated session before the grant; exact final membership.
- Request timestamps are taken after the fixture parses request headers; first_request_ms therefore includes client connect plus header transmission.
- Role removal targets an unassigned role; deletion of assigned roles is outside these workloads (phase5.py declares that server behavior separately).
- Expected list output and messages come from phase5.py's Go renderer model and canonicalizers; endpoint shapes follow phase5.py's Model. The fixture itself is a separate threaded server (phase5.py's AdminServer is a raw-socket scripted server).
- Measured max_in_flight is an observation of server arrivals, not a claim about the client task count. The default Rust inventory fan-out retains Go behavior after a 64-request cap failed the wide-barrier gate.
- The Go A/A stage is a correctness gate with its own small repetition count; its ratios are not a timing baseline.
- Loopback measurements establish no live-server performance claim; no real server is contacted or mutated.
