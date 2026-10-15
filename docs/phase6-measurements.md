# Phase 6 measurements

Twenty-four native measurement windows are complete: two same-Go A/A partitions, all twelve Go-versus-Rust R6 finite partitions, and ten short-duration partitions covering five live modes with separate full-parser and hash-framing collectors. Interpretive profiles, build-cost measurements and long soaks remain pending. A separate tiny strace functional capture has completed; it supplies no performance comparison. All ratios are unavailable under the predeclared CPU uncertainty screen; these observations do not establish equivalence or a relative budget pass.

The initial two same-Go A/A windows described in the following table each used an explicit Phase7 execution pause and the common nonblocking performance lock. Each workload/label in those windows has five warmups and 30 measured samples. The seed is 2026. Both windows completed 120 measured samples, 20 warmups and two contract controls, with exact stdout/request/process/config contracts and unchanged bound identities. The full parser and hash-framing control remain separate; neither overhead nor fixture encoding time is subtracted from client CPU.

| Collector | Workload | Go label | Wall median (min–max), ms | First complete record median, ms | Native CPU median, ms | Peak RSS median, MiB |
|---|---|---|---:|---:|---:|---:|
| full | one-row | go-a | 30.20 (26.41–39.45) | 28.48 | 38.67 | 48.19 |
| full | one-row | go-b | 31.53 (25.87–42.10) | 29.37 | 40.51 | 48.21 |
| full | many-small | go-a | 38.68 (33.31–46.85) | 29.31 | 44.72 | 48.91 |
| full | many-small | go-b | 39.05 (33.29–53.30) | 29.16 | 44.33 | 48.79 |
| hash-framing | one-row | go-a | 31.40 (26.23–39.64) | 29.32 | 40.35 | 48.36 |
| hash-framing | one-row | go-b | 31.26 (25.82–39.31) | 29.26 | 40.24 | 48.40 |
| hash-framing | many-small | go-a | 38.22 (32.21–44.96) | 30.00 | 45.84 | 48.76 |
| hash-framing | many-small | go-b | 38.30 (32.27–45.83) | 29.26 | 43.97 | 48.85 |

Both labels use Go SHA `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`; fixture r5 is `c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314`. Full-parser execution ran 17:31:16.076852–17:31:24.783970 UTC; hash-framing ran 17:33:18.936599–17:33:27.741985 UTC on 2026-10-14. Cleanup checks found no remaining owned fixture/meter/client processes and confirmed that the lock was released after each window.

The one-row workload includes startup and fatal terminal processing. The many-small workload has 128 one-row batches and also includes startup. Neither is a successful live-stream delivery test. The parent observes only complete LF-framed records; fixture-to-record latency uses the recorded fixture emission clock. These short samples have no useful post-warmup RSS/FD trend, and sampled maxima cannot establish memory release.

The two collector modes have overlapping absolute wall-time ranges in this small finite experiment. This is descriptive only: the run is too short for the host CPU screen to attribute load tightly enough to admit a comparative ratio. Native CPU can exceed wall time because Go uses multiple threads. The reduced parser still checks the entire output hash and exact rows/batches; sequence validation beyond its first object is explicitly unavailable.

[Bound evidence and summaries](evidence/phase6/timing-aa-r6.json) retain report/journal hashes, counts, raw per-label summary statistics and separately attributed observer/fixture CPU. The two offline resource reports additionally retain faults, context switches, block/procfs I/O and byte-rate coverage without reconstructing quarantined ratios. [Operating decisions](phase6-measurement-operating-decisions.md) and [resource interpretation](phase6-resource-interpretation.md) govern the limitations. The [independent Sol review](phase6-first-aa-review-sol.md) accepts scoped evidence integrity after 122,900 consistency checks over 4,301 unchanged input files. It independently reconstructs counts, output hashes, contracts, raw timing/resource arithmetic and collector parse counts. It finds no actual defect and makes no equivalence, speed or budget claim.

## First candidate finite partitions

The first Go-versus-R6 full-parser campaign and its separate hash-framing observer control completed under separate explicit quiet acknowledgements on 2026-10-14. Each contains 120 measured samples, 20 warmups and two same-Go contract controls: one-row and many-small, five warmups and 30 measured repetitions per binary/workload, seed 2026, session index 0, plaintext. Both reports pass output/request/process/config contracts and end-identity checks. All comparative ratios remain null under the unchanged CPU-admission screen. These are absolute observations, not a speed ranking or relative-budget pass.

| Collector | Workload | Binary | Wall median (min–max), ms | First complete record median, ms | Native CPU median, ms | Peak RSS median, MiB |
|---|---|---|---:|---:|---:|---:|
| full | one-row | Go | 30.93 (25.50–39.75) | 29.22 | 39.55 | 48.30 |
| full | one-row | Rust R6 | 3.12 (2.51–4.11) | 2.75 | 2.30 | 8.12 |
| full | many-small | Go | 39.21 (33.75–45.25) | 29.44 | 45.57 | 48.99 |
| full | many-small | Rust R6 | 10.28 (7.74–14.32) | 2.89 | 4.88 | 8.14 |
| hash-framing | one-row | Go | 31.18 (26.63–38.30) | 29.10 | 38.98 | 48.33 |
| hash-framing | one-row | Rust R6 | 3.27 (2.60–4.00) | 2.92 | 2.43 | 8.09 |
| hash-framing | many-small | Go | 41.48 (35.45–53.28) | 31.96 | 47.30 | 48.80 |
| hash-framing | many-small | Rust R6 | 9.72 (8.21–13.32) | 2.95 | 5.20 | 8.12 |

Full-parser execution ran 18:30:56.540849–18:31:03.241041 UTC; the hash-framing control ran 18:32:25.285730–18:32:32.259677 UTC. Each cleanup verified the campaign PID had exited, found no remaining known fixture/meter/client processes, and reacquired/released the common lock before releasing Phase7. Rust R6 is SHA `f1fb7a9e294a8f96bd9835de4c6f32318b0adf01bb164db648c4fb9c0ad5140f`; Go and fixture identities match the A/A partitions above.

The two collector modes remain separate and were run sequentially. Their differences do not estimate causal collector overhead. These startup-inclusive, fatal-terminated finite samples do not establish sustained throughput or memory release. The [bound candidate evidence](evidence/phase6/finite-first-r6.json) preserves full summaries, quarantine reasons, report/journal/resource-report hashes and exact window records. The [fresh independent offline audit](phase6-first-candidate-finite-review-sol.md) accepts scoped retained-evidence integrity: 131,653 consistency checks over 4,359 unchanged input files and whole-output proofs for all 284 observations found no defects. It independently confirms all 32 comparative ratios remain null. All samples have zero admitted post-warmup proc trend observations, so no stability or memory-release claim follows. Whole-phase acceptance remains open.

## Complete finite collection

The remaining ten finite partitions completed on 2026-10-14 between 22:51:39 and 23:03:01 UTC. Together with the first pair above, all twelve workloads now have 30 measured samples per binary in each collector mode: **1,440 measured samples, 240 excluded warmups and 24 same-Go contract controls**. Every campaign reports complete collection, valid output/request/process/config contracts and unchanged end identities. Each window had its own explicit acknowledgement, common nonblocking lock and recorded process cleanup. Phase7 remained user-paused after these windows.

[Complete finite evidence](evidence/phase6/finite-complete-r6.json) retains every report/journal/resource-summary hash, per-label absolute statistics, CPU-screen classifications and new window records. The ten new offline resource summaries retain faults, context switches, block/procfs I/O and byte-rate coverage. The [independent complete-finite audit](phase6-finite-complete-review-sol.md) accepts scoped retained-evidence integrity after 671,893 checks with zero failures across all twelve partitions. It independently verifies all 1,704 observations, summary arithmetic and CPU-screen decisions. It rehashes all 724 fully retained stdout files; for 980 capped outputs it checks retained prefixes and recorded whole-stream hashes against an independently reconstructed oracle. All 192 comparative metric ratios remain null. This acceptance does not establish sustained behavior, memory release or a relative performance-budget pass.

**No comparative ratio is admitted.** In addition to short-sample resolution limits, longer finite samples include flagged or ambiguous unattributed host CPU. A separate one-second process snapshot between windows observed active Parseable, VM and other processes; it is not campaign evidence and does not identify the cause of any individual sample. Phase6 did not stop those unrelated processes or relax admission. Absolute observations remain available, while relative budgets remain unknown.

Both large-dictionary compression cases completed their exact nine-batch, 36-row cycle with independently checked output. These finite pressure observations do not prove long-term retention or dictionary-memory release. The short-duration screening below is collected; its scoped evidence review is accepted and staged long soaks remain outstanding.

## Short-duration live-mode collection

On 2026-10-15, ten separately acknowledged windows ran steady, idle, reconnect, cyclic dictionary and slow-sink modes, each with separate full and hash-framing collectors. Each partition contains one nominal 60-second session per binary, zero process warmups and two same-Go contract controls: **20 measured sessions and 20 controls**. Seed 2026, session index 0 and plaintext are unchanged. The user explicitly authorized proceeding on the busy host while withholding gated comparisons; unrelated services and VMs were left running.

All ten reports completed, all individual sessions passed their output/request/process/config and applicable schedule/churn checks, and all end identities remained unchanged. Every window records process cleanup and common-lock release. These are short duration observations, not 30-minute soaks. [Bound short-duration evidence](evidence/phase6/short-duration-r6.json) records reports, offline resource summaries, exact window records, per-session resource histories and production accounting. The [independent short-duration audit](phase6-short-duration-review-sol.md) accepts scoped retained-evidence integrity: 372,650 checks, zero failures and 761 unchanged inputs. It independently reconstructs output, accounting, native arithmetic and resource histories. This is not quantitative acceptance or soak authorization.

**All 80 comparative ratios are null.** All 20 measured sessions have flagged host CPU attribution. Nine pairs have matching delivered contracts. The slow-sink hash-framing pair additionally differs in delivered workload: Go emitted 466 batches / 59,648 rows, and Rust emitted 467 batches / 59,776 rows. Each independently matches its own fixture output hash and terminal accounting. The producer scheduled 3,000 batches and stopped at its deadline; exact omitted-by-deadline counts are 2,534 and 2,533 respectively. Those omitted batches were not delivered output. The comparison remains quarantined for both workload mismatch and host CPU interference; it is not a throughput or equivalence result. Slow-sink observations remain absolute-only even where counts match.

Unlike the finite startup samples, these sessions have post-warmup procfs observations. Their RSS/FD/task histories and fitted slopes are retained for review, not accepted as proof of bounded long-term retention, memory release or a leak. The two collector modes ran sequentially and their differences do not isolate observer cost. The audit identifies material Go RSS/task growth in slow-sink and reconnect that requires bounded fresh-seed controls before those modes' first soaks; steady growth is also carried forward. It finds two future validation gaps: slow-sink omission conservation and the resource adapter's exit-code cross-check. All historical observations pass the independent stronger predicates. The [separate supplemental offline gate](phase6-duration-gate.md) now passes 19 tests, all 40 historical observations and eight root negative probes without changing the frozen collector. It covers subsequent functional-duration evidence; soak-tier support remains a separate prerequisite. No long soak starts automatically from these results.

## Tiny strace functional capture

A separately coordinated window on 2026-10-14 validated the strace capture path with one basic row: four unprofiled release/symbolized Go/Rust controls and two symbolized strace captures. All six preserve the exact 162-byte stdout, 157-byte deliberate fatal diagnostic, one discovery request and one authenticated DoGet. The capture report passes with 53/53 end identities unchanged. Execution ran 20:56:40.739584–20:56:42.256272 UTC; cleanup and common-lock release were verified at 20:57:06.780266 UTC.

The [independent offline audit](phase6-strace-functional-review-01.md) passes 318 checks and confirms strace 7.0, GNU readelf 2.47, valid symbolized section outputs and structurally consistent syscall-summary artifacts. It distinguishes fully hashed small artifacts from recorded binary identities and historical cleanup attestations. [Bound evidence](evidence/phase6/strace-functional-r6.json) preserves the exact report, source, sidecar and coordination identities.

The saved-report analysis subsequently completed in a separately coordinated window at 21:07:27.854183–21:07:28.420037 UTC, with expected exit 3. Both tables were processed; all 53 end identities and the analysis harness check passed. It reports `analysis_complete_no_adequacy_criterion` and zero interpretable captures. Process cleanup and common-lock release were verified at 21:09:39.735932 UTC. [Analysis evidence](evidence/phase6/strace-analysis-r6.json) records the exact command, identities and outcome; the earlier independent capture audit does not cover this subsequent analysis.

This validates the tiny capture and saved-report processing paths only. Instrumented costs include tracing overhead and support no timing, RSS or syscall-count ranking. Other profiler interoperability, adequacy decisions and whole-phase acceptance remain pending.
