# Independent offline shared-command primary audit (Sol, R1)

2026-10-15. **No unresolved retained-corpus integrity error. Performance acceptance remains pending.** The actual Go / accepted Phase 5 Rust / combined normal Rust primary is internally consistent across all 1,575 samples. All 45 candidate/prior historical gate cells have **no combined threshold crossing**, but **30 cells in all 15 cases exceed the separate accepted-prior 5% median screen** and need fresh isolated confirmation before that acceptance rule can close. No new capture was performed or authorized by this review.

Work is limited to this NEW document and `/home/ajs/.cache/pb-phase7/shared-primary-review-sol-r1/`. It used bounded standard-library JSON/byte/AST arithmetic only, with disk-cache TMP and a 120-second alarm per script. No product/native CLI, fixture, meter, probe, compiler, build, profiler, common-lock operation, existing helper execution, or nested/delegated work occurred. All audit scripts spawned zero children; the lane has zero active work processes at completion.

## Inputs, identities and preservation

Read in full the [A/A review](phase7-review-shared-aa-sol-r1.md), [its root disposition](evidence/phase7/shared-aa-review-disposition.json), [Linux adapter review](phase7-review-shared-linux-adapter-opus-r1.md), and [its root disposition](evidence/phase7/shared-linux-adapter-review-disposition.json). Their accepted scope and N1–N9 dispositions are retained. Read the exact plan and `launch-primary.py`; inspected source scheduling, metric definitions, observer timestamps, native mapping, output/request/config contracts, summary and compare behavior. No UI protocol is substituted for this shared-command audit.

- Primary `/home/ajs/.cache/pb-phase7/shared-performance-combined-primary-01/report.json`: **`92975e27d756cbe701bb3ca0fb818e36655b0a5876b2a0d3a7723dbd6e81a0ff`**.
- Supervision `shared-performance-combined-primary-01-supervision/report.json`: **`854f9eccdb0cf8f464cf8be032b8a9db94a81c9241cd752e71e529b52e5e8079`**; coordination `-coordination/{launch,cleanup}.json` and raw siblings are included.
- Exact plan `shared-performance-combined-owned-preparation/plan-r2.json`: `6323da9afb5ffa7e2c17094609b0738c24f88d33cc8050e35af0c1d36ce32cd7`; generated runner `d65cf16df8ba26f0b3b99efe8f0ed408d1c7bfaee1dd1a16d19fd6c4723a2f5e`.
- Actual Go: `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314` (71,807,160 bytes).
- Actual accepted Phase 5 prior: `9bd38b9db4f4f8e76cd43384cb024dddd4d122c6bc109d29a8afa483ae0e2c6d` (7,617,800 bytes).
- Actual combined normal candidate: `6d80054f5d87b081bfe51bed014a6f64c8e9a01c2c7f7fcb4c6434e221868443` (12,513,760 bytes).
- Original same-Go report is preserved separately in `shared-performance-combined-go-aa-01`, SHA `03b33ed88b77aa2a4d62f134d2611a04d25a9c9fc90d50fef1bb7c88b5bcc0cb`.

`originals-before.json` / `originals-end.json` inventory **24,501 files / 2,064,273,741 bytes**: primary and A/A raw captures, supervision/coordination, owned preparation, original A/A reviewer scripts/reports, plan-bound binaries and sources, all 64 candidate production sources, reviewed documentation, and compressed historical reports. Every inventoried original is unchanged. Final preservation is rechecked after writing this review. These are byte-preservation/check-to-use checks, not adversarial atomicity or authenticated capture-time proof.

The A/A pure implementation was read, copied into this lane, and adapted explicitly for the three different binaries, primary argv and 15+64 source binding. `audit.py` completed its full traversal, calculations and before/end hashing in **15.323 s** (well below 1,800 s), making **51,823 checks**. The original script and outputs are preserved. One reviewer assertion was too broad: it required the older `performance-shared` candidate to equal the later accepted prior. The historical chain is actually `performance-shared.rust ec9765a4…` → `review-fixes-shared.prior ec9765a4…`, then `review-fixes-shared.rust 9bd38b9d…` → this primary's `prior`. `followup.py` reconciles that one assumption and checks another **38,160 available measured metric values** for finite nonnegative numbers. There are **zero unresolved corpus errors**. No failed check is silently deleted.

## Full corpus, scheduling, contracts and cleanup

- Exactly the original 15 ordered suite specs, all per-spec/canonical-suite hashes, three ordered actual labels, 5 excluded warmups and 30 measured samples per case/label: **225 warmups + 1,350 measured = 1,575**. All are valid, issue-free, untruncated, unsignaled, failure-free, exit 0, with matching termination contracts. All byte caps are exact.
- Every measured report sample equals its artifact JSON plus the three runner scheduling fields. One global `Random(2026)` across all cases reproduces every measured round/order. All 1,575 native-reported starts are unique, and reconstructed warmup/measured spans are sequential in that schedule. Warmups lack explicit round/order records; names, source reconstruction and recorded starts corroborate order without independently logging it.
- **3,150 raw streams**, their sizes/raw hashes, independently normalized bytes/hashes, all contract objects/hashes and exact artifact inventories match. Each case has one stable contract across all 105 actual-binary/warmup samples. All stderr is empty.
- **1,365 requests** match exact ordered method/path/count, auth, expected-route flags, normalized query/body contracts, SQL query/from/to content and request bytes. First-request/realtime correlation and request-to-stdout arithmetic reproduce. This validates retained traces, not packets or physical clock accuracy.
- **1,155 copied TOML configs** independently parse to the expected seeded config and normalized endpoint; reported before/after snapshots agree. Original before files and actual original HOME mode are not retained; `0o600` remains a source-supported snapshot claim.
- **630 suite-pinned stdout byte/hash expectations** match. **630 arrays / 2,205,105 rows** independently parse with correct counts/sequences, including compact arrays from the text-labeled cases and the empty case. The other 945 outputs have complete raw/contract checks but no independent suite-pinned expected hash; cross-binary contract agreement is corroboration, not an independent command oracle.
- All **1,272 summary metric populations**, every n/median/mean/min/max/p90/p95/p99/stdev/MAD value, all 30 inline wall comparisons (seed 2026, 2,000 independent draws), all 1,350 CSV rows/cells/order, output/row throughput and stdout-span arithmetic reproduce. Generated `run()` is AST-identical after its ownership guard; `sample`, `summarize`, `compare_samples`, `compare`, and the native-override tail remain unchanged from live source.
- All 1,575 samples report `native_child_wait4` resource/block counters and available `linux_proc_pid_io_before_wait4`; no fallback, missing mandatory counter, invalid numeric value, CPU sum disagreement or RSS unit disagreement is present.
- Exact primary argv, raw supervisor argv hex, bounded driver argv, unset Python import-path variables, fixed PATH, current artifact identities, launch identities, 64 manifest production hashes and pre/post hashes agree. Each pre/post array has **80 rows / 79 unique identities**: 15 plan inputs + 64 production sources, with the same identical duplicate `/usr/bin/flock` row seen in A/A. The duplicate is accounted for explicitly.
- Recorded ACK is fresh at launch, and completed cleanup precedes its hard end. Supervisor and authoritative final event exit 0, no interruptions/issues/watchdog/stderr, ECHILD, no own/unreaped children, passive lock free followed by successful nonblocking reacquire/release, and cleanup-bound report hashes agree. Complete preparation-through-cleanup span is **114.544690 s**; authoritative supervisor exit event is **114.401891 s**. These are offline checks of recorded proofs, with no current process/lock probe or global host-quiet claim.

## Metric meaning and provenance limits

Native C source starts monotonic timing just before fork/exec and ends at waitable child exit, before `/proc` reads and artifact writing. It reports wait4 child CPU, peak RSS (Linux KiB × 1,024), faults, context switches and block operations, plus pre-reap `/proc/PID/io`. The runner's unchanged native tail replaces supervisor fallback, computes wall from native endpoints, sums user+system CPU within each sample and carries native termination. The pinned meter `17c3b734…` and fixture `fa8b4711…` also match both historical compressed reports. The candidate manifest's 64 sources match current bytes and launch/end records. This supports reported mapping and source/artifact binding; it is not an independent reproduction of any executable build or actual running-kernel accounting correspondence.

**Raw `meter.json` is absent.** Temporary HOMEs are removed. Original native payload/end timestamp, per-role native argv, raw wait status and fixture stderr cannot be independently re-extracted; the retained sample is the merged reported record. No physical CPU/peak-RSS accuracy, quantization bound, descendant-accounting eligibility, observer-bias bound or causal attribution is established. This retention/provenance limit remains unassessed, with no concrete mapping bug found and no new runner redesign demanded. CPU/RSS exclude Python observer and fixture resources; elapsed time can reflect their scheduling/backpressure. Inherited flock descriptor is a retained wrapper difference from historical runs. Five warmups do not prove cache equality; no cache treatment changed.

`first_stdout_ms` and `first_complete_element_ms` are parent pipe-read observations, offset to native start. First element is a complete first JSON value observed in a chunk, not whole-array completion. `first_useful_result_ms` is **absent in all 1,350 measured samples**; no useful-result latency can be fabricated from stdout. First record/stderr are null throughout. First complete element is available in 15 populations (five nonempty array cases × three labels); it is null in the other 900 samples, including empty arrays. First request is null in 450 nonfixture samples and uses realtime when available. Missing fields and nulls remain missing, never zero.

Five first-stdout observations follow native exit availability: profile-list candidate round 7, dataset-list prior round 26, dataset-info prior rounds 0/23, dataset-schema prior round 11. The largest is **12.377591 ms** stdout observation versus **1.701614 ms** native wall in dataset-schema/prior/11. Source timestamps the parent's `os.read` result, so delayed pipe observation can follow child exit. This investigates the semantic mismatch without discarding a sample, asserting a kernel defect, or interpreting the observation as child write completion. All exact records are in `followup-result.json`.

## Absolute statistics and variability

All values are measured-only n=30 per population. Triples are **Go / prior / combined**. The full absolute distributions and missingness for **every declared metric** are retained in `distributions.json` and `absolute-statistics.csv`: sample n, absent/null/zero/nonzero counts, median/mean/min/max/p90/p95/p99/stdev/MAD. They include separate user/system CPU, first/last stdout/span, first element/request/request-to-stdout, row/output rates and bytes, faults, both context-switch counters, block operations and every available `/proc` IO counter. `absolute-statistics.md` gives the same full available-field distributions for inspection. Zero major faults, block operations and storage-byte counters are actual available reported zeros, not imputed missingness. `/proc` read/write-family counters omit send/recv; neither these nor wait4 block operations measure network traffic.

| Case | Wall median ms | CPU median ms | RSS median MiB |
|---|---|---|---|
| p1-0000-root | 29.025 / 1.275 / 1.461 | 39.007 / 1.149 / 1.363 | 46.707 / 4.984 / 6.139 |
| p1-0001-root-help | 28.813 / 1.209 / 1.255 | 37.828 / 1.085 / 1.179 | 46.645 / 4.934 / 5.943 |
| p1-0003-version | 28.374 / 1.300 / 1.404 | 37.715 / 1.236 / 1.312 | 46.564 / 4.934 / 5.963 |
| p1-0009-agent-json | 29.624 / 1.263 / 1.392 | 38.171 / 1.180 / 1.298 | 46.607 / 5.094 / 6.229 |
| p2-status-basic-json | 33.576 / 1.985 / 2.037 | 40.928 / 1.643 / 1.664 | 47.252 / 6.213 / 7.900 |
| p2-profile-list-seeded-json | 29.963 / 1.422 / 1.590 | 38.695 / 1.336 / 1.497 | 46.576 / 5.350 / 6.646 |
| p2-dataset-list-basic-json | 33.682 / 2.183 / 2.148 | 42.624 / 1.863 / 1.783 | 47.697 / 6.238 / 7.842 |
| p2-dataset-info-json | 31.026 / 2.360 / 2.646 | 39.715 / 1.761 / 2.119 | 47.809 / 6.400 / 7.967 |
| p2-dataset-schema-json | 31.181 / 2.072 / 2.032 | 40.135 / 1.665 / 1.672 | 47.562 / 6.199 / 7.818 |
| p3-bench-rows-1-json | 31.317 / 2.218 / 2.363 | 40.498 / 1.909 / 2.046 | 47.662 / 6.250 / 7.996 |
| p3-bench-rows-500-json | 36.996 / 4.964 / 4.664 | 45.783 / 4.651 / 4.234 | 48.764 / 6.393 / 7.980 |
| p3-bench-rows-500-text | 31.380 / 2.288 / 2.407 | 40.069 / 1.874 / 2.034 | 47.984 / 6.389 / 7.980 |
| p3-bench-rows-10000-json | 118.442 / 42.425 / 43.112 | 144.109 / 43.196 / 44.082 | 55.822 / 6.289 / 7.852 |
| p3-bench-rows-10000-text | 33.667 / 4.900 / 4.623 | 41.480 / 4.466 / 4.292 | 47.943 / 6.707 / 8.131 |
| p3-bench-empty-json | 31.223 / 2.121 / 2.103 | 39.431 / 1.823 / 1.770 | 47.609 / 6.350 / 7.961 |

| Case | First stdout observation median ms, Go / prior / combined | First complete element median ms, Go / prior / combined |
|---|---|---|
| p1-0000-root | 27.775 / 1.157 / 1.317 | N/A / N/A / N/A |
| p1-0001-root-help | 27.392 / 1.107 / 1.175 | N/A / N/A / N/A |
| p1-0003-version | 26.561 / 1.185 / 1.281 | N/A / N/A / N/A |
| p1-0009-agent-json | 28.264 / 1.152 / 1.295 | N/A / N/A / N/A |
| p2-status-basic-json | 31.180 / 1.935 / 1.816 | N/A / N/A / N/A |
| p2-profile-list-seeded-json | 28.341 / 1.275 / 1.438 | N/A / N/A / N/A |
| p2-dataset-list-basic-json | 31.693 / 2.046 / 1.966 | N/A / N/A / N/A |
| p2-dataset-info-json | 29.311 / 2.157 / 2.458 | N/A / N/A / N/A |
| p2-dataset-schema-json | 29.523 / 1.929 / 1.856 | N/A / N/A / N/A |
| p3-bench-rows-1-json | 29.844 / 1.916 / 2.079 | 29.878 / 1.952 / 2.080 |
| p3-bench-rows-500-json | 29.824 / 1.988 / 1.995 | 29.854 / 2.021 / 2.010 |
| p3-bench-rows-500-text | 29.124 / 1.867 / 1.979 | 29.124 / 1.867 / 1.979 |
| p3-bench-rows-10000-json | 29.993 / 2.007 / 1.940 | 30.030 / 2.031 / 1.956 |
| p3-bench-rows-10000-text | 29.195 / 1.990 / 1.986 | 29.195 / 1.990 / 1.986 |
| p3-bench-empty-json | 28.932 / 1.892 / 1.864 | N/A / N/A / N/A |

Across all 45 populations, wall CV is **7.97–19.85%**, CPU CV **5.49–18.18%**, RSS CV **0.72–4.78%**. Candidate 10,000-row JSON wall median/p95/max is **43.112 / 59.131 / 64.717 ms**. First-stdout CV reaches **88.24%** in dataset-schema/prior, consistent with the retained observation-delay outlier; no causal scheduling explanation is proved. These n=30 tails are descriptive, pointwise estimates.

The preserved SAME-Go A/A had wall CV **5.92–14.00%**, and 500-row text `rust/prior` wall **+9.09%, CI 1.027–1.137**. Same-binary point screens and intervals can cross; that neither supplies a new A/A cutoff nor proves these actual-candidate differences are noise. No equivalence follows.

In this primary, combined/Go marginal-median ratios range **0.0436–0.3640 wall**, **0.0312–0.3059 CPU**, **0.1274–0.1696 RSS**. All corresponding pointwise seed-1 upper ratio bounds are below one (maxima 0.3859 / 0.3185 / 0.1751). This is an observed descriptive comparison only; actual physical CPU/RSS proof remains unknown, and universal speed/readiness claims do not follow. Full Go/prior and Go/combined absolute deltas, ratios and intervals are in `go-comparisons-seed1.json`; inline seed-2026 comparisons remain separately preserved.

## All 45 historical gate calculations

These are candidate/prior **ratios of marginal medians**, not paired ratios. Independently resample each 30-value population 2,000 times with `Random(1)`, exactly the separate compare CLI default; percentile endpoints linearly interpolate at `(n−1)p`. Inline comparisons instead use seed 2026. Every population is complete, positive, finite, valid and contract-compatible. No compare CLI was executed.

Historical point screen: wall/CPU ratio **>1.10 AND median delta >1 ms**; RSS ratio **>1.20 AND median delta >2 MiB**. Historical tool formula: entire pointwise ratio interval lower **>1.10 / >1.20**, AND the same **median** absolute delta allowance. It does not require the delta CI lower bound above that allowance. Strict inequalities apply. The independent 5% screen has **no absolute allowance**.

**Historical combined point screens: 0/45. Historical tool-formula crossings: 0/45.** All wall/CPU median increases are below 1 ms; all RSS median increases are below 2 MiB, even where the relative RSS bound exceeds 1.20. Thus these descriptive historical formulas do not cross. This is not an accepted-budget pass, equivalence or a confidence bound on all-case noninferiority. Raw payload/accounting prerequisites and the separate fresh confirmations remain unresolved.

Wall/CPU medians and deltas below are milliseconds; RSS values are MiB. The last column identifies the separate >5% primary median screen.

| Case | Metric | Prior median | Combined median | Median delta | Change % | Seed-1 ratio CI95 | >5% screen |
|---|---|---:|---:|---:|---:|---|---|
| p1-0000-root | wall_ms | 1.275472 | 1.461262 | +0.185790 | +14.566 | 1.044988–1.310270 | YES |
| p1-0000-root | cpu_ms | 1.149500 | 1.362500 | +0.213000 | +18.530 | 1.053655–1.370910 | YES |
| p1-0000-root | peak_rss_bytes | 4.984375 | 6.138672 | +1.154297 | +23.158 | 1.203308–1.249703 | YES |
| p1-0001-root-help | wall_ms | 1.209090 | 1.254936 | +0.045846 | +3.792 | 0.890194–1.259993 | no |
| p1-0001-root-help | cpu_ms | 1.085000 | 1.178500 | +0.093500 | +8.618 | 0.878878–1.300536 | YES |
| p1-0001-root-help | peak_rss_bytes | 4.933594 | 5.943359 | +1.009766 | +20.467 | 1.196210–1.219937 | YES |
| p1-0003-version | wall_ms | 1.300293 | 1.404224 | +0.103930 | +7.993 | 1.018440–1.179814 | YES |
| p1-0003-version | cpu_ms | 1.236500 | 1.312500 | +0.076000 | +6.146 | 1.010903–1.183482 | YES |
| p1-0003-version | peak_rss_bytes | 4.933594 | 5.962891 | +1.029297 | +20.863 | 1.178257–1.233877 | YES |
| p1-0009-agent-json | wall_ms | 1.262506 | 1.392008 | +0.129503 | +10.258 | 0.968291–1.260932 | YES |
| p1-0009-agent-json | cpu_ms | 1.180000 | 1.298000 | +0.118000 | +10.000 | 0.975180–1.297543 | YES |
| p1-0009-agent-json | peak_rss_bytes | 5.093750 | 6.228516 | +1.134766 | +22.278 | 1.205567–1.238829 | YES |
| p2-status-basic-json | wall_ms | 1.985277 | 2.036900 | +0.051623 | +2.600 | 0.881240–1.243261 | no |
| p2-status-basic-json | cpu_ms | 1.643500 | 1.664000 | +0.020500 | +1.247 | 0.884171–1.304086 | no |
| p2-status-basic-json | peak_rss_bytes | 6.212891 | 7.900391 | +1.687500 | +27.161 | 1.259352–1.287631 | YES |
| p2-profile-list-seeded-json | wall_ms | 1.421683 | 1.590016 | +0.168333 | +11.840 | 0.881371–1.316325 | YES |
| p2-profile-list-seeded-json | cpu_ms | 1.335500 | 1.497000 | +0.161500 | +12.093 | 0.852849–1.336275 | YES |
| p2-profile-list-seeded-json | peak_rss_bytes | 5.349609 | 6.646484 | +1.296875 | +24.242 | 1.218545–1.254976 | YES |
| p2-dataset-list-basic-json | wall_ms | 2.183191 | 2.148123 | -0.035068 | -1.606 | 0.818355–1.100731 | no |
| p2-dataset-list-basic-json | cpu_ms | 1.863000 | 1.783000 | -0.080000 | -4.294 | 0.792040–1.151906 | no |
| p2-dataset-list-basic-json | peak_rss_bytes | 6.238281 | 7.841797 | +1.603516 | +25.704 | 1.233416–1.274616 | YES |
| p2-dataset-info-json | wall_ms | 2.359924 | 2.646198 | +0.286274 | +12.131 | 0.964435–1.232873 | YES |
| p2-dataset-info-json | cpu_ms | 1.761000 | 2.119500 | +0.358500 | +20.358 | 0.994510–1.293571 | YES |
| p2-dataset-info-json | peak_rss_bytes | 6.400391 | 7.966797 | +1.566406 | +24.474 | 1.234164–1.263971 | YES |
| p2-dataset-schema-json | wall_ms | 2.072161 | 2.032020 | -0.040141 | -1.937 | 0.910691–1.098185 | no |
| p2-dataset-schema-json | cpu_ms | 1.665500 | 1.672000 | +0.006500 | +0.390 | 0.875636–1.178345 | no |
| p2-dataset-schema-json | peak_rss_bytes | 6.199219 | 7.818359 | +1.619141 | +26.118 | 1.243770–1.271893 | YES |
| p3-bench-rows-1-json | wall_ms | 2.217716 | 2.363217 | +0.145500 | +6.561 | 0.908122–1.124182 | YES |
| p3-bench-rows-1-json | cpu_ms | 1.908500 | 2.046000 | +0.137500 | +7.205 | 0.912578–1.142391 | YES |
| p3-bench-rows-1-json | peak_rss_bytes | 6.250000 | 7.996094 | +1.746094 | +27.937 | 1.256481–1.291075 | YES |
| p3-bench-rows-500-json | wall_ms | 4.963983 | 4.663852 | -0.300130 | -6.046 | 0.866277–1.033394 | no |
| p3-bench-rows-500-json | cpu_ms | 4.650500 | 4.233500 | -0.417000 | -8.967 | 0.857043–1.036135 | no |
| p3-bench-rows-500-json | peak_rss_bytes | 6.392578 | 7.980469 | +1.587891 | +24.840 | 1.235792–1.267536 | YES |
| p3-bench-rows-500-text | wall_ms | 2.287555 | 2.407487 | +0.119931 | +5.243 | 0.960200–1.149262 | YES |
| p3-bench-rows-500-text | cpu_ms | 1.874500 | 2.034000 | +0.159500 | +8.509 | 0.962264–1.239943 | YES |
| p3-bench-rows-500-text | peak_rss_bytes | 6.388672 | 7.980469 | +1.591797 | +24.916 | 1.237560–1.263485 | YES |
| p3-bench-rows-10000-json | wall_ms | 42.425339 | 43.111541 | +0.686201 | +1.617 | 0.908359–1.068332 | no |
| p3-bench-rows-10000-json | cpu_ms | 43.195500 | 44.082500 | +0.887000 | +2.053 | 0.917314–1.065208 | no |
| p3-bench-rows-10000-json | peak_rss_bytes | 6.289062 | 7.851562 | +1.562500 | +24.845 | 1.221581–1.277408 | YES |
| p3-bench-rows-10000-text | wall_ms | 4.900253 | 4.623381 | -0.276872 | -5.650 | 0.883644–1.027911 | no |
| p3-bench-rows-10000-text | cpu_ms | 4.466500 | 4.291500 | -0.175000 | -3.918 | 0.899367–1.051100 | no |
| p3-bench-rows-10000-text | peak_rss_bytes | 6.707031 | 8.130859 | +1.423828 | +21.229 | 1.180118–1.273691 | YES |
| p3-bench-empty-json | wall_ms | 2.121262 | 2.103456 | -0.017806 | -0.839 | 0.875792–1.156270 | no |
| p3-bench-empty-json | cpu_ms | 1.822500 | 1.770000 | -0.052500 | -2.881 | 0.864861–1.192484 | no |
| p3-bench-empty-json | peak_rss_bytes | 6.349609 | 7.960938 | +1.611328 | +25.377 | 1.238887–1.265965 | YES |

Every exact Boolean, threshold, median, delta and interval for all 45 cells is in `historical-gates-all45.json`; no primary flag is hidden because physical accounting remains unproved.

## Prospective fresh-confirmation list

**30 metrics in 15 isolated case sessions: RSS in every case; wall in seven; CPU in eight.** This is the concrete consequence of the accepted >5% median screen, including small absolute increases; the 1 ms/2 MiB allowances cannot suppress it.

- `p1-0000-root`: `wall_ms`, `cpu_ms`, `peak_rss_bytes`.
- `p1-0001-root-help`: `cpu_ms`, `peak_rss_bytes`.
- `p1-0003-version`: `wall_ms`, `cpu_ms`, `peak_rss_bytes`.
- `p1-0009-agent-json`: `wall_ms`, `cpu_ms`, `peak_rss_bytes`.
- `p2-status-basic-json`: `peak_rss_bytes`.
- `p2-profile-list-seeded-json`: `wall_ms`, `cpu_ms`, `peak_rss_bytes`.
- `p2-dataset-list-basic-json`: `peak_rss_bytes`.
- `p2-dataset-info-json`: `wall_ms`, `cpu_ms`, `peak_rss_bytes`.
- `p2-dataset-schema-json`: `peak_rss_bytes`.
- `p3-bench-rows-1-json`: `wall_ms`, `cpu_ms`, `peak_rss_bytes`.
- `p3-bench-rows-500-json`: `peak_rss_bytes`.
- `p3-bench-rows-500-text`: `wall_ms`, `cpu_ms`, `peak_rss_bytes`.
- `p3-bench-rows-10000-json`: `peak_rss_bytes`.
- `p3-bench-rows-10000-text`: `peak_rss_bytes`.
- `p3-bench-empty-json`: `peak_rss_bytes`.

Root should bind a fresh isolated 5-warmup/30-measured interleaved session for each exact case above, retaining all labels/metrics and complete validation/end checks. No automatic capture occurs here. For each flagged metric the retained confirmation failure rule is **fresh confirmation median ratio >1.05 AND the entire independent seed-1/2,000-draw ratio CI >1.05**, with **no hidden absolute allowance**. Preserve primary and confirmation separately, without pooling or favorable-metric selection. The primary root CPU interval already has lower bound 1.053655 and every RSS lower bound exceeds 1.05; those primary intervals still do not replace fresh confirmation. See `prospective-confirmations.json` for exact primary signals and status.

Practical investigation remains open for this broad reported RSS increase (**20.47–27.94%, +1.010–1.746 MiB**) and the flagged small CPU/wall increases. The offline evidence attributes them to no particular implementation or host effect. Fresh isolated sessions can test persistence under the accepted rule; root retains scheduling/quiet authorization and provenance adjudication. No new runner experiment or redesign is requested absent a concrete defect.

`report.passed=true` means functional/sample/contract success. The compare implementation only requires ten available values and does not itself enforce actual binary identity, all30, physical accounting, end hashes, fresh quiet or a full-run deadline; this audit independently checks what retained evidence can supply. No A1/UI performance envelope, release/platform readiness, all-workflow coverage, universal “always faster” claim or full Phase 7 completion is established.

Originals are unchanged, all original scripts/reports remain intact, and this lane finishes with **zero active work processes**. `task-manifest.json` binds the final review and audit artifacts; raw report identity is given above.
