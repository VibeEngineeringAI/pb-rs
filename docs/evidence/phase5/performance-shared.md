# pb process benchmark

Cache state: warm OS caches; fresh process and fresh config per sample.
Times include process launch and CLI work, exclude fixture startup and artifact writes.

| Case | Binary | Valid | Wall median / p95 ms | First stdout ms | First element ms | CPU ms | Peak RSS MiB |
|---|---|---:|---:|---:|---:|---:|---:|
| p1-0000-root | go | 30/30 | 25.613 / 31.151 | 25.425 | N/A | 35.023 | 51.551 |
| p1-0000-root | prior | 30/30 | 1.206 / 1.307 | 1.107 | N/A | 1.137 | 5.836 |
| p1-0000-root | rust | 30/30 | 1.298 / 1.396 | 1.164 | N/A | 1.231 | 4.953 |
| p1-0001-root-help | go | 30/30 | 25.326 / 30.598 | 25.186 | N/A | 36.544 | 51.756 |
| p1-0001-root-help | prior | 30/30 | 1.211 / 1.304 | 1.115 | N/A | 1.151 | 5.818 |
| p1-0001-root-help | rust | 30/30 | 1.283 / 1.357 | 1.153 | N/A | 1.192 | 4.912 |
| p1-0003-version | go | 30/30 | 26.020 / 29.863 | 25.573 | N/A | 36.188 | 51.398 |
| p1-0003-version | prior | 30/30 | 1.204 / 1.284 | 1.099 | N/A | 1.123 | 5.799 |
| p1-0003-version | rust | 30/30 | 1.303 / 1.388 | 1.182 | N/A | 1.216 | 4.951 |
| p1-0009-agent-json | go | 30/30 | 25.862 / 29.513 | 25.615 | N/A | 35.498 | 51.494 |
| p1-0009-agent-json | prior | 30/30 | 1.332 / 1.451 | 1.232 | N/A | 1.257 | 5.916 |
| p1-0009-agent-json | rust | 30/30 | 1.441 / 1.541 | 1.309 | N/A | 1.348 | 5.074 |
| p2-status-basic-json | go | 30/30 | 27.001 / 29.690 | 26.076 | N/A | 35.948 | 52.750 |
| p2-status-basic-json | prior | 30/30 | 1.935 / 2.150 | 1.801 | N/A | 1.605 | 6.572 |
| p2-status-basic-json | rust | 30/30 | 2.030 / 2.231 | 1.847 | N/A | 1.726 | 6.184 |
| p2-profile-list-seeded-json | go | 30/30 | 25.866 / 30.182 | 25.377 | N/A | 34.990 | 51.680 |
| p2-profile-list-seeded-json | prior | 30/30 | 1.310 / 1.410 | 1.195 | N/A | 1.233 | 6.031 |
| p2-profile-list-seeded-json | rust | 30/30 | 1.387 / 1.592 | 1.266 | N/A | 1.285 | 5.393 |
| p2-dataset-list-basic-json | go | 30/30 | 28.218 / 34.512 | 27.021 | N/A | 37.866 | 52.467 |
| p2-dataset-list-basic-json | prior | 30/30 | 1.735 / 2.258 | 1.622 | N/A | 1.450 | 6.627 |
| p2-dataset-list-basic-json | rust | 30/30 | 2.116 / 2.347 | 1.972 | N/A | 1.820 | 6.236 |
| p2-dataset-info-json | go | 30/30 | 28.963 / 34.298 | 28.526 | N/A | 37.087 | 52.414 |
| p2-dataset-info-json | prior | 30/30 | 2.370 / 2.669 | 2.192 | N/A | 1.744 | 6.795 |
| p2-dataset-info-json | rust | 30/30 | 2.648 / 2.806 | 2.440 | N/A | 2.045 | 6.438 |
| p2-dataset-schema-json | go | 30/30 | 29.207 / 32.329 | 28.117 | N/A | 37.472 | 52.391 |
| p2-dataset-schema-json | prior | 30/30 | 1.942 / 2.678 | 1.845 | N/A | 1.547 | 6.641 |
| p2-dataset-schema-json | rust | 30/30 | 2.124 / 2.352 | 1.982 | N/A | 1.780 | 6.287 |
| p3-bench-rows-1-json | go | 30/30 | 29.693 / 35.320 | 28.316 | 28.316 | 37.608 | 52.682 |
| p3-bench-rows-1-json | prior | 30/30 | 2.013 / 2.313 | 1.725 | 1.747 | 1.700 | 6.719 |
| p3-bench-rows-1-json | rust | 30/30 | 2.228 / 2.459 | 1.970 | 2.018 | 1.939 | 6.303 |
| p3-bench-rows-500-json | go | 30/30 | 32.532 / 40.799 | 26.442 | 26.468 | 41.296 | 53.408 |
| p3-bench-rows-500-json | prior | 30/30 | 4.271 / 5.029 | 1.699 | 1.740 | 4.128 | 6.744 |
| p3-bench-rows-500-json | rust | 30/30 | 4.449 / 5.348 | 1.837 | 1.873 | 4.261 | 6.359 |
| p3-bench-rows-500-text | go | 30/30 | 29.117 / 32.979 | 28.022 | 28.022 | 37.624 | 52.678 |
| p3-bench-rows-500-text | prior | 30/30 | 2.127 / 2.416 | 1.794 | 1.794 | 1.830 | 6.742 |
| p3-bench-rows-500-text | rust | 30/30 | 2.235 / 2.540 | 1.875 | 1.875 | 2.005 | 6.402 |
| p3-bench-rows-10000-json | go | 30/30 | 110.209 / 119.949 | 27.776 | 27.776 | 135.967 | 60.643 |
| p3-bench-rows-10000-json | prior | 30/30 | 40.686 / 51.406 | 1.815 | 1.834 | 41.990 | 6.715 |
| p3-bench-rows-10000-json | rust | 30/30 | 41.469 / 46.024 | 1.928 | 1.949 | 42.084 | 6.248 |
| p3-bench-rows-10000-text | go | 30/30 | 31.056 / 37.326 | 27.953 | 27.953 | 39.271 | 53.010 |
| p3-bench-rows-10000-text | prior | 30/30 | 4.649 / 6.003 | 1.841 | 1.841 | 4.105 | 7.100 |
| p3-bench-rows-10000-text | rust | 30/30 | 4.661 / 5.559 | 1.852 | 1.852 | 4.228 | 6.713 |
| p3-bench-empty-json | go | 30/30 | 28.646 / 35.348 | 27.434 | N/A | 38.469 | 52.770 |
| p3-bench-empty-json | prior | 30/30 | 2.061 / 2.259 | 1.824 | N/A | 1.778 | 6.768 |
| p3-bench-empty-json | rust | 30/30 | 2.127 / 2.450 | 1.875 | N/A | 1.790 | 6.287 |

## Comparisons

- p1-0000-root: prior/go wall_ms ratio **0.047×**, bootstrap 95% interval 0.044–0.050.
- p1-0000-root: rust/go wall_ms ratio **0.051×**, bootstrap 95% interval 0.046–0.053.
- p1-0001-root-help: prior/go wall_ms ratio **0.048×**, bootstrap 95% interval 0.043–0.049.
- p1-0001-root-help: rust/go wall_ms ratio **0.051×**, bootstrap 95% interval 0.045–0.052.
- p1-0003-version: prior/go wall_ms ratio **0.046×**, bootstrap 95% interval 0.042–0.048.
- p1-0003-version: rust/go wall_ms ratio **0.050×**, bootstrap 95% interval 0.047–0.052.
- p1-0009-agent-json: prior/go wall_ms ratio **0.052×**, bootstrap 95% interval 0.049–0.054.
- p1-0009-agent-json: rust/go wall_ms ratio **0.056×**, bootstrap 95% interval 0.053–0.058.
- p2-status-basic-json: prior/go wall_ms ratio **0.072×**, bootstrap 95% interval 0.064–0.075.
- p2-status-basic-json: rust/go wall_ms ratio **0.075×**, bootstrap 95% interval 0.070–0.080.
- p2-profile-list-seeded-json: prior/go wall_ms ratio **0.051×**, bootstrap 95% interval 0.048–0.053.
- p2-profile-list-seeded-json: rust/go wall_ms ratio **0.054×**, bootstrap 95% interval 0.048–0.057.
- p2-dataset-list-basic-json: prior/go wall_ms ratio **0.061×**, bootstrap 95% interval 0.055–0.072.
- p2-dataset-list-basic-json: rust/go wall_ms ratio **0.075×**, bootstrap 95% interval 0.069–0.080.
- p2-dataset-info-json: prior/go wall_ms ratio **0.082×**, bootstrap 95% interval 0.068–0.086.
- p2-dataset-info-json: rust/go wall_ms ratio **0.091×**, bootstrap 95% interval 0.080–0.095.
- p2-dataset-schema-json: prior/go wall_ms ratio **0.066×**, bootstrap 95% interval 0.059–0.071.
- p2-dataset-schema-json: rust/go wall_ms ratio **0.073×**, bootstrap 95% interval 0.060–0.077.
- p3-bench-rows-1-json: prior/go wall_ms ratio **0.068×**, bootstrap 95% interval 0.060–0.073.
- p3-bench-rows-1-json: rust/go wall_ms ratio **0.075×**, bootstrap 95% interval 0.066–0.081.
- p3-bench-rows-500-json: prior/go wall_ms ratio **0.131×**, bootstrap 95% interval 0.119–0.143.
- p3-bench-rows-500-json: rust/go wall_ms ratio **0.137×**, bootstrap 95% interval 0.124–0.144.
- p3-bench-rows-500-text: prior/go wall_ms ratio **0.073×**, bootstrap 95% interval 0.067–0.080.
- p3-bench-rows-500-text: rust/go wall_ms ratio **0.077×**, bootstrap 95% interval 0.072–0.084.
- p3-bench-rows-10000-json: prior/go wall_ms ratio **0.369×**, bootstrap 95% interval 0.355–0.391.
- p3-bench-rows-10000-json: rust/go wall_ms ratio **0.376×**, bootstrap 95% interval 0.352–0.393.
- p3-bench-rows-10000-text: prior/go wall_ms ratio **0.150×**, bootstrap 95% interval 0.137–0.160.
- p3-bench-rows-10000-text: rust/go wall_ms ratio **0.150×**, bootstrap 95% interval 0.137–0.164.
- p3-bench-empty-json: prior/go wall_ms ratio **0.072×**, bootstrap 95% interval 0.064–0.075.
- p3-bench-empty-json: rust/go wall_ms ratio **0.074×**, bootstrap 95% interval 0.067–0.077.

## Interpretation

Lower latency/CPU/RSS is better; higher throughput is better. Missing metrics are not zero.
Bootstrap intervals are descriptive, not proof of causality; scheduler load and thermal/cache state still matter.
First output/request times are observations at the pipe/server, not instrumentation inside pb.
Current fixture first-request times use realtime clock correlation; clock adjustments can skew request-to-output comparisons. Pipe and child wall times use CLOCK_MONOTONIC.
First complete element validates a JSON array value; '[' and JSON formatting lines are not rows. JSONL first-record timing remains separate.
First-element validation runs in the pipe reader and can add backpressure; cases with timing disabled report N/A and still validate count/order after exit.
Block input/output metrics count wait4 block operations, not storage bytes or network traffic. Each sample labels native child versus supervisor fallback resources.
Request-body and fixture-response-body bytes describe logical HTTP payloads only; TLS/socket bytes and unread server tails are unavailable.
Output uses bounded memory by default; opted workload cases spool during timed pipe drain (up to 1 GiB), then materialize for post-exit validation. Truncation fails compatibility.
Tail finite cases deliberately end with an error after the known rows; signal cases compare true signal termination.
Exact stdout/stderr and termination are compared; requests use semantic JSON/query comparison, config uses parsed TOML plus permissions.
Only fixture URL and temporary HOME are normalized. Each run retains the original bytes and requests.
Phase-four pipe observations require fixture milestones and compare stdout against the start of the final body write, a delivery lower bound. Exact complete-body ordering and command-specific useful-result timing remain N/A; delayed-EOF and body-fault differential cases establish buffering correctness.
This corpus is not complete parity: PTY/TUI, OAuth refresh, TLS, reconnect and other uncovered paths need additional cases.
