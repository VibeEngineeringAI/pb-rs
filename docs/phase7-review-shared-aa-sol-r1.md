# Independent offline shared-command A/A evidence audit (Sol, R1)

2026-10-15. **No new evidence-integrity blocker found for the next separately authorized, descriptive Go/prior/combined primary capture.** The retained full-suite control is internally consistent. This is neither performance acceptance nor approval of ratios, CPU/RSS budgets, host quiet, or Rust performance. All three labels in this control execute the SAME Go binary, `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`.

Work was limited to this new document and `/home/ajs/.cache/pb-phase7/shared-aa-review-sol-r1/`. No source/capture edits, native workload, product CLI, meter, fixture, probe, compiler, build, profiler, lock operation, existing test/helper execution, or delegation occurred. The lane paused with a zero-active-process ACK when requested and resumed only on explicit RELEASE. A1/UI evidence is outside this review.

## Concrete disposition before primary capture

1. Root must explicitly retain the A/A variability below in its interpretation. There is **no prospectively defined A/A noise cutoff** here; I do not invent one or require another control run. A report `passed=true` is functional/contract success, not a noise or performance gate pass.
2. Bind the primary argv and identities to actual Go `fae8411f…`, accepted Phase 5 Rust `9bd38b9d…`, and combined normal candidate `6d80054f…`; the control's `prior` and `rust` labels provide no evidence about either Rust executable. Retain the full 15-case, three-label, 5+30, single-Random(2026) design and the accepted historical confirmation rule. No changed sample count, reseeding, report splicing, or UI decision protocol is justified by this audit.
3. Obtain a **fresh** explicit companion/own-lane quiet ACK and perform the already reviewed, separately authorized strict-under-1800-second preparation-through-cleanup procedure, common nonblocking lock, source/artifact binding, complete-corpus validation, end hashes and cleanup. The A/A token is historical; there is no current quiet token. This review authorizes no execution.

Actual CPU/RSS accounting and binary-to-current-source correspondence remain interpretation limits described below. They do not by themselves prohibit truthful descriptive capture. No additional runner redesign is requested.

## Evidence identity and preservation

Read the FULL accepted [Opus Linux adapter review](phase7-review-shared-linux-adapter-opus-r1.md) and [root disposition](evidence/phase7/shared-linux-adapter-review-disposition.json). Their N1–N9 dispositions remain accepted; none is reopened. Also read the exact plan, launcher, generated runner's measurement/sample/scheduling/summary/comparison paths, live comparison/scheduling source, meter C source, retained suite and relevant shared-preparation/decision rules.

- Capture `shared-performance-combined-go-aa-01/report.json`: `03b33ed88b77aa2a4d62f134d2611a04d25a9c9fc90d50fef1bb7c88b5bcc0cb`.
- Supervision `-supervision/report.json`: `d0fc6315b4f67016e6b690fcff31473213d6ac3a5a9125d99b2084c1e44eef0e`.
- `shared-performance-combined-owned-preparation/plan-r2.json`: `6323da9afb5ffa7e2c17094609b0738c24f88d33cc8050e35af0c1d36ce32cd7`.
- Generated runner: `d65cf16df8ba26f0b3b99efe8f0ed408d1c7bfaee1dd1a16d19fd6c4723a2f5e`. Live runner remains `b280d1295a4b351e6f2d838c56acaac58b2247bb77fc9881587bb7bbe849a076`.

`originals-before.json` and `originals-end.json` inventory **12,219 files / 1,085,537,832 bytes**, covering all capture, supervision, coordination and owned-preparation originals, all plan-bound identities, and reviewed source/doc inputs. `preservation-result.json` records the end comparison. A separate secondary before/end manifest covers the historical compressed reports and ancillary disposition/binding files. These checks establish unchanged bytes across this review, not adversarial atomicity or independently authenticated capture-time behavior.

## Independent checks and exact coverage

Own standard-library scripts use direct JSON/bytes and AST inspection; they never import or execute runner/native functions. `audit.py` performed one full corpus traversal and independent arithmetic recomputation in **7.04 seconds**, with a 120-second alarm. It made **46,183 checks**. The original result is preserved, including two mistaken reviewer assumptions that supervisor hash arrays would have 15 rows. `followup.py` (0.59 seconds, same alarm) reconciled those two checks without repeating the corpus: each array has **16 rows, 15 unique identities**, with identical `/usr/bin/flock` entries twice. Every row matches the planned/current identity. There are **zero unresolved corpus errors**; `followup-result.json` retains the reconciliation explicitly.

- **15 exact ordered specs**, all hashes and canonical suite hash match. Three ordered labels; exactly **1,350 measured samples plus 225 real warmup artifacts**. All are valid, failure-free, untruncated, unsignaled, exit 0; no warmup issue is hidden by the report's excluded-warmup arrays.
- All **1,350 report entries equal their artifact sample JSON**, adding only runner-assigned round/order/artifact fields. Measured rounds/order match the one global RNG across the entire suite. All **1,575 native-reported start/end spans** are unique and sequential in that reconstructed order, including warmups. Warmup artifacts lack explicit round/order fields; directory names plus observed start order corroborate the reconstruction, rather than furnishing an independent warmup order log.
- **3,150 raw streams**: byte counts and raw hashes match; all stderr streams are empty. Normalized bytes and hashes were independently reproduced using the copied config endpoint where applicable. All contract objects/hashes match and are stable across **all 105 samples per case**, including warmups and labels.
- **1,365 raw requests** match exact scheduled method/path order/count, Basic auth and expected-route flags. Raw request-to-contract transformation, semantic bodies/query fields, SQL query/from/to bodies and request-byte counts match. First-request/realtime and request-to-stdout arithmetic reproduces. This checks retained trace contents, not network packet capture or physical clock accuracy.
- **1,155 copied TOML configs** parse to the expected seeded configuration and match reported before/after snapshots after endpoint normalization. Artifact copies do not independently preserve original HOME directory permissions or raw before-state files; reported `0o600` remains a source-supported snapshot claim.
- **630 suite-pinned stdout hash/byte expectations**, all match. Independently parsed **630 JSON arrays / 2,205,105 rows** match count and sequence, including text cases emitting compact arrays and the empty case. The other **945 outputs** have complete raw/contract checks but no suite-pinned independent stdout hash; same-Go equality alone is not an independent functional oracle for those commands.
- Independently reproduced **1,272 summary metric cells** (all n/median/mean/min/max/p90/p95/p99/stdev/MAD fields), all **30 inline wall comparisons** with seed 2026 / 2,000 independent resamples, and **all 1,350 CSV rows/cells/order**. Additional descriptive CPU/RSS comparisons are retained separately. AST comparison confirms `run()` is unchanged after removing the new ownership guard, and `compare()` is unchanged.

## Native-reported usage and termination provenance

All **1,575** retained samples name `native_child_wait4` for resource/block counters, available `linux_proc_pid_io_before_wait4`, finite nonnegative native metrics, exact user+system CPU sums, and RSS divisible by 1,024. No supervisor fallback is present. The runner source explicitly reads the temporary `meter.json`, overrides supervisor values, computes wall from native monotonic endpoints, sums native user/system CPU, and propagates native termination. The C source shows CLI fork/exec, WNOWAIT observation, pre-reap `/proc/PID/io`, final wait4, Linux KiB-to-byte conversion and its JSON schema. This supports the stated **reported-counter** interpretation.

The pinned meter executable `17c3b734…` and fixture `fa8b4711…` match the capture's initial/final/current identities and both retained historical compressed reports. Historical exact-suite specs also match. **Preserved executable identity and historical use do not prove a reproduced binary-to-current-C-source build or actual kernel accounting correspondence.** No contrary provenance or source mismatch was found.

The runner deletes its temporary HOME after each sample and does **not retain raw `meter.json`**. Consequently original native end stamps/raw payloads, per-role native argv records, original wait status and fixture stderr are unavailable for independent replay. Retained sample termination and metrics are the merged records; they cannot be independently re-extracted from that absent payload. This is a retention limit, not evidence that native mapping failed. Source-supported reported wall/CPU/RSS remain descriptive; no physical CPU consumption, physical peak RSS accuracy, quantization bound, descendant-accounting eligibility or absence of observer bias is established. CPU/RSS here exclude the Python observer and fixture's own resources, while their scheduling/backpressure can affect elapsed time. `/proc` IO and wait4 block operations are not network bytes.

Supervision events, its final report, coordination log and cleanup JSON agree: runner/top process exit 0, no watchdog/stderr output, ECHILD and no owned children/unreaped descendants, recorded passive lock-free then successful nonblocking reacquire/release, and unchanged 15 end identities. **142.154 seconds** is the recorded complete cleanup span; supervisor authoritative completion is 142.016571 seconds. Cleanup's report/supervisor hashes match. This is offline corroboration of the recorded cleanup proofs; I acquired no lock or process probe and make no current/global host-quiet assertion.

## Descriptive absolute distributions and variability

Each cell below is **Go / same-Go `prior` / same-Go `rust`**, 30 measured values per label. RSS is the reported integer-KiB counter converted to MiB. Full absolute distributions, extrema, stdev, MAD and percentiles are in `distributions.json`; warmups remain excluded.

| Case | Wall medians ms | CPU medians ms | Reported RSS medians MiB |
|---|---|---|---|
| root | 29.216 / 30.553 / 28.809 | 37.930 / 38.969 / 38.255 | 46.551 / 46.539 / 46.645 |
| root-help | 30.216 / 29.895 / 28.639 | 39.204 / 38.868 / 38.098 | 46.561 / 46.406 / 46.564 |
| version | 29.321 / 28.564 / 29.099 | 38.459 / 37.846 / 37.633 | 46.535 / 46.562 / 46.613 |
| agent-json | 29.137 / 27.797 / 27.718 | 37.887 / 37.338 / 37.686 | 46.566 / 46.547 / 46.736 |
| status-basic-json | 29.961 / 29.765 / 30.642 | 38.714 / 37.951 / 39.025 | 47.592 / 47.633 / 47.156 |
| profile-list-seeded-json | 29.336 / 30.011 / 28.929 | 38.005 / 38.383 / 39.049 | 46.492 / 46.709 / 46.752 |
| dataset-list-basic-json | 30.844 / 30.574 / 29.894 | 39.083 / 39.570 / 38.677 | 47.553 / 47.416 / 47.377 |
| dataset-info-json | 30.415 / 31.764 / 31.085 | 38.848 / 39.913 / 40.082 | 47.539 / 47.576 / 47.434 |
| dataset-schema-json | 30.837 / 29.409 / 31.029 | 40.392 / 38.399 / 40.554 | 47.404 / 47.307 / 47.754 |
| rows-1-json | 31.921 / 30.163 / 30.681 | 40.740 / 39.901 / 39.913 | 47.869 / 47.805 / 47.855 |
| rows-500-json | 35.285 / 35.009 / 35.269 | 42.995 / 42.497 / 43.388 | 48.631 / 48.773 / 48.758 |
| rows-500-text | 30.904 / 29.234 / 31.893 | 39.716 / 37.868 / 39.500 | 47.738 / 47.848 / 47.920 |
| rows-10000-json | 115.589 / 114.381 / 114.773 | 142.334 / 142.096 / 141.946 | 55.855 / 56.246 / 56.195 |
| rows-10000-text | 29.706 / 31.730 / 29.624 | 38.348 / 40.652 / 38.489 | 47.914 / 47.840 / 48.020 |
| empty-json | 28.885 / 28.408 / 28.425 | 38.349 / 37.288 / 37.800 | 47.988 / 47.996 / 47.824 |

Across the 45 case/label distributions, wall CV is **5.92–14.00%**, CPU CV **5.35–9.65%**, reported RSS CV **0.65–5.06%**. One-row JSON `rust` wall p95 is **39.142 ms** versus **30.681 ms** median. Ten-thousand-row JSON `rust` reported RSS p95 is **62.419 MiB** versus **56.195 MiB** median. These are n=30 descriptive percentiles, not tail guarantees.

For the relevant same-Go `rust/prior` direction, seed-1 / 2,000-draw independent bootstrap arithmetic gives 500-row text wall **+2.659 ms / +9.09%**, ratio interval **1.027–1.137**. Dataset-schema wall **+1.620 ms / +5.51%** (0.993–1.113) and CPU **+2.155 ms / +5.61%** (0.996–1.088) also exceed the separate historical 5% point screen numerically. Thus same-binary label differences can exceed that point screen, and pointwise intervals can exclude unity. This control cannot attribute the differences or prove absence of bias. It neither creates an A/A failure threshold nor mandates a candidate confirmation campaign.

## Admission gates the source does not supply

`run()`'s pass Boolean follows sample validity and contract compatibility; no performance regression threshold is applied. Native source fields are consumed without a full typed finite/nonnegative schema gate. `compare()` checks compatibility but only requires **10**, not exactly 30, available metric values when gating; it does not enforce all expected binary acceptance identities, actual native accounting provenance, seed/repetition equality, raw warmup artifact completeness, end identities, quiet authorization or complete-run deadline. This independent audit supplies retained-corpus checks where possible; later primary evidence needs its own complete validation. Missing original native payload/config-before/OS-status proofs cannot be retroactively supplied by report Booleans.

Historical shared gates remain **wall/CPU >10% AND >1 ms; RSS >20% AND >2 MiB**, with the entire pointwise bootstrap ratio interval above the relative threshold and the median absolute delta above its allowance. All 45 same-Go `rust/prior` metric cells are numerically below those combined crossing conditions, which establishes no equivalence or gate admission. The separate accepted-prior **>5% primary median screen** retains its fresh isolated 5+30 confirmation procedure and complete ratio interval >1.05 criterion, without a hidden absolute allowance. Inline intervals use seed 2026; the historical compare CLI arithmetic uses default seed 1. Phase 7 UI decision envelopes/accounting protocols are separate and are neither satisfied nor amended here.

Review completed with zero active lane processes. Task artifact hashes and unresolved interpretation limits are retained in `task-manifest.json` and `findings.json`; no originals were rewritten.
