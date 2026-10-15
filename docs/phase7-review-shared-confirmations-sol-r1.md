# Independent offline isolated-confirmation audit (Sol, R1)

2026-10-15. **All 15 primary RSS flags confirm under the retained numerical failure rule. Performance acceptance remains open.** The 15 case-alone captures are internally consistent across **1,575 actual samples: 225 warmups and 1,350 measured**. No unresolved retained-corpus integrity error was found.

For the original **30 flagged metrics**, exact confirmation-protocol outcomes are **15 confirmed failures, 7 point screens disconfirmed/not reproduced, and 8 inconclusive with a reproduced point screen**. “Point screen disconfirmed” means only that the fresh median is ≤1.05. Using the stronger statistical definition—an entire interval at or below 1.05—the evidence counts are **15 confirmed, 0 disconfirmed, 15 inconclusive**. None of the 15 timing intervals rules out a >5% increase. These two countings are explicit in `findings.json`; neither treats nonconfirmation as equivalence.

The 15 confirmed failures are **RSS in every case**, now **+19.733–27.149%, +0.980469–1.675781 MiB** versus accepted Phase 5 Rust. The primary was +20.467–27.937%, +1.009766–1.746094 MiB. The persistence supports a practical investigation of the broad reported RSS increase before closing the accepted-prior performance issue. It identifies no cause and establishes no physical/kernel accounting proof.

## Independent method and complete retained evidence

Read the FULL [primary review](phase7-review-shared-primary-sol-r1.md), its audit scripts, task manifest and prospective-confirmations list, the [A/A review](phase7-review-shared-aa-sol-r1.md) and disposition, the [Linux adapter review](phase7-review-shared-linux-adapter-opus-r1.md) and disposition, and the [bounded supervisor review](phase7-review-bounded-execution-opus-r2.md) and [root disposition](evidence/phase7/bounded-execution-r2-disposition.json). Their accepted scope, N1–N9 dispositions and conditional-supervision limits remain in force.

Read the exact confirmation plan, `batch.py`, `launch.py`, recorded quiet ACK, coordination, supervision/events/stdout/stderr, generated scheduling/sample/summary/compare and native-override source, and native C meter source. Source was parsed/read only; no workload module was imported. The prior arithmetic was adapted into a NEW guarded script for 15 separate reports; no original fixed-output script was run. Each isolated case reconstructs its **own Random(2026)**, 5 warmup rounds and 30 measured rounds, with the original ordered `go/prior/rust` labels.

For **each original flagged metric**, fresh failure is strictly **confirmation median ratio >1.05 AND full seed-1/2,000-draw ratio CI >1.05**, with **no absolute allowance**. Resampling independently chooses 30 prior values and 30 candidate values per draw, then divides their marginal medians. Percentiles interpolate at `(n−1)p`. Intervals are pointwise descriptive bootstrap estimates; serial dependence, coverage and simultaneous all-case noninferiority are not established. Primary and confirmation were never pooled; all metrics, roles and samples remain retained.

New evidence lives only in `/home/ajs/.cache/pb-phase7/shared-confirmations-review-sol-r1/` and this document. Complete `gates-all45.json` / `all45-review.md` retain **all 45 current prior/candidate cells**, including all30 input values for each role, absolute medians/deltas, ratio/delta intervals, availability, strict gate Booleans, primary comparisons, and all nonflags/new flags. `distributions.json`, `absolute-statistics.{csv,md}`, and `tails-primary-comparison.json` retain **every declared metric for all 45 case/label populations**: absent/null/zero/nonzero counts, n, median, mean, min/max, p90/p95/p99, stdev/MAD, plus primary distributions. `go-comparisons-seed1.json` preserves all Go comparisons separately without asserting a universal ranking.

## Timing flags and genuine new signals

Of the original seven wall and eight CPU flags, **zero** satisfy the full fresh confirmation-failure rule. Eight reproduce the >5% median screen but their intervals cross 1.05. Seven no longer reproduce the median screen:

- `p1-0003-version/wall_ms`
- `p1-0009-agent-json/wall_ms`
- `p1-0009-agent-json/cpu_ms`
- `p2-dataset-info-json/wall_ms`
- `p3-bench-rows-1-json/wall_ms`
- `p3-bench-rows-1-json/cpu_ms`
- `p3-bench-rows-500-text/wall_ms`

Every one of these seven still has an upper interval bound above 1.05. A primary full-interval result, including root CPU, cannot replace this fresh result.

**Six genuine new median flags** appear among the 15 originally nonflagged cells; **two** also have the full interval above 1.05:

- `p1-0001-root-help/wall_ms`: **+8.707%**, ratio CI **1.006213–1.156094**; interval crosses 1.05.
- `p2-status-basic-json/wall_ms`: **+14.592%**, ratio CI **1.079610–1.307325**; entire interval exceeds 1.05.
- `p2-status-basic-json/cpu_ms`: **+17.518%**, ratio CI **1.089774–1.342117**; entire interval exceeds 1.05.
- `p2-dataset-schema-json/wall_ms`: **+7.768%**, ratio CI **1.003353–1.171607**; interval crosses 1.05.
- `p2-dataset-schema-json/cpu_ms`: **+8.041%**, ratio CI **0.975401–1.168380**; interval crosses 1.05.
- `p3-bench-rows-500-json/cpu_ms`: **+5.019%**, ratio CI **0.898037–1.178314**; interval crosses 1.05.

These are new signals, **not additional confirmations of the original 30**. Status wall/CPU deserve a separate disposition if pursuing performance acceptance; this offline audit authorizes no additional capture. Four other new point screens remain interval-inconclusive. Across all45 current cells, there are **17 numerical full-interval signals: 15 original RSS confirmations plus two new status signals**.

The descriptive historical combined formulas still cross **0/45**: wall/CPU >10% AND median delta >1 ms, or RSS >20% AND median delta >2 MiB, with the full interval above the relative threshold for the tool-formula crossing. Their absolute allowances do not suppress the separate 5% confirmation rule. Functional `passed=true` and historical noncrossing do not close performance acceptance.

## Full-corpus and orchestration checks

- All 15 original suite specs and per-spec/selected-suite hashes, actual binary identities and full methods match. Every report contains exactly its one prescribed case. All105 artifacts per case—including all warmups—are valid, untruncated, unsignaled, failure-free, exit 0, and contract-compatible. All1,350 measured records equal sample JSON plus only scheduling/artifact fields; all15 CSVs reproduce in full (90 rows each, **1,350 total**).
- **3,150 raw streams** reproduce byte counts/raw hashes and independently normalized hashes; stderr is empty throughout. **1,365 requests** reproduce exact ordered method/path/count, Basic auth, expected-route flags, normalized query/body contracts, SQL query/from/to and body bytes. **1,155 copied TOMLs** parse to the expected seeded config and agree with reported snapshots. **630 suite-pinned stdout expectations** and **630 independently parsed arrays / 2,205,105 rows** match, including text-labeled compact arrays and empty arrays. The other945 outputs lack a separate suite-pinned stdout oracle; cross-binary contract agreement is corroboration.
- All **1,272 summary metric populations**, every summary field, all30 inline wall comparisons (seed2026), throughput and request/stdout/span arithmetic reproduce. **44,520 available metric values across measured and warmup samples** are finite and nonnegative. Mandatory native counters, user+system CPU sums, integer-KiB RSS representation and reported `/proc` availability agree; no fallback appears.
- All1,575 native-reported starts are unique; reconstructed spans are sequential and lie inside their own recorded session bounds. Sessions do not overlap, and all sample realtime starts lie within the recorded orchestration. Warmup order is source reconstruction corroborated by starts and names; raw artifacts have no explicit warmup order fields. Native end bounds are reconstructed from retained start+wall; raw meter endpoints are unavailable.
- Exact per-session inner argv matches the primary after output/case substitution; one outer `/usr/bin/flock --conflict-exit-code 73 --nonblock --no-fork` covers the sequential batch. `close_fds=False` preserves that inherited descriptor into each runner. This retains the known wrapper difference from historical unwrapped runs. Raw supervisor command/driver argv hex, unset PYTHONPATH/PYTHONHOME/PYTHONSAFEPATH, fixed PATH and recorded cache TMP paths agree. Source AST confirms scheduling/sample/summary/compare and native-tail semantics unchanged from the accepted adapter.
- Every pre/post supervisor array has **84 rows /83 unique identities**, including one identical duplicate flock row. All82 planned inputs plus the plan itself match launch, post and current bytes, including all64 manifest-bound production sources. Go/prior/combined remain three distinct original executables.
- Recorded start **13:54:29.247986Z**, supervisor returned by **13:56:15.346092Z**, complete cleanup **13:56:15.499969Z**: **106.252007 s**, strictly below1,800. The final event assesses exit0 at106.096340 s; coordinator independently records actual exit0. Report/summary/events agree on `ok`, no interruptions/issues, empty watchdog/stderr, ECHILD, zero owned/unreaped children, passive common-lock free and subsequent nonblocking reacquire/release. The historical ACK was fresh at launch and cleanup precedes its hard end. These are checks of retained records; **no current quiet token exists and both lanes are released**.

`audit.py` made **99,573 checks** in6.606 s; `supervision.py` made another314 checks in0.172 s; statistics completed in3.037 s. Fifteen initial argv assertions included the primary's outer flock prefix while comparing an inner session argv. The original failures remain in `corpus-result.json`; `supervision-result.json` explicitly reconciles all15 against the one outer wrapper. The original CSV counter overwrote to90 per session, while every per-session CSV equality passed; the followup records the correct total1,350. An initial supervision-script syntax error was corrected before any execution, with source/correction retained. **No failed reviewer assertion is erased and no unresolved corpus error remains.**

## Semantics, variability and remaining limits

Native source times immediately before fork/exec to waitable child exit, then reads `/proc/PID/io` before wait4 and converts Linux ru_maxrss KiB×1,024. The runner maps reported native wall, wait4 user/system CPU and peak RSS into the merged sample, overriding supervisor fallback. This supports **reported native mapping only**. **Raw `meter.json` is absent**; original native endpoints/payload/wait status, per-role native argv and fixture stderr cannot be independently re-extracted. Artifact identities and current64-source agreement do not reproduce an executable build or prove actual running-kernel accounting correspondence. Physical CPU/RSS accuracy, quantization bounds, descendant eligibility, observer bias and causal attribution remain unassessed. CPU/RSS omit observer/fixture resource usage; elapsed time can reflect their scheduling/backpressure. No mapping bug was found and no runner redesign is demanded.

All1350 first-useful-result fields are absent; first-record/stderr are null, and first-complete-element is available in15 populations, null in900 samples. First stdout/element are parent read observations, not child write or useful-result completion. Dataset-schema combined round23 observes first stdout at1.891138 ms after native wall1.856041 ms; no value is discarded or relabeled. Request times use retained realtime correlation. Config-before and original HOME modes are snapshots/source claims, not retained original filesystem proofs. `/proc` read/write counters omit send/recv and neither they nor wait4 block counters are network bytes.

Measured wall CV spans **8.08–30.18%**, CPU **6.64–16.15%**, RSS **0.75–4.45%** across all45 populations. Combined10,000-row JSON wall median/p95/max is **38.440592 /45.451076 /47.295259 ms**, versus primary43.111541 /59.131217 /64.717310 ms (exact retained values govern). These n=30 tails are descriptive. All measured block-input/storage-read counters are available zero; a root-Go warmup has64 block-input operations and32,768 storage-read bytes, retained and excluded from measured distributions. Five warmups do not establish equal caches. The earlier same-Go A/A point screen +9.09% (CI1.027–1.137) remains context, without an invented noise cutoff or a claim that current differences are noise.

Before/end manifests cover **36,837 original files /3,053,064,444 bytes**, including primary/A/A audits and captures, all confirmation captures and coordination/supervision, exact plans/scripts, bound binaries/sources and reviewed docs. Final preservation is checked after writing this document. These are byte-preservation/check-to-use checks, not authenticated capture-time or adversarial atomicity proof. All originals are unchanged.

All audit scripts have120-second alarms and deny process creation, network/native acquisition and writes outside the new scope. No CLI/native workload, existing helper/test, probe, build/compiler/profiler, common-lock operation, live harness edit, Git operation or delegation occurred. This review makes no UI/A1 admission, universal readiness/ranking, release or full Phase7 completion claim. It finishes with **zero owned active work processes**; `task-manifest.json` and its SHA bind the final document and complete machine evidence.
