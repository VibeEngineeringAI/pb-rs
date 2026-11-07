# PR 12: adversarial review and integrated validation

[PR 12](https://github.com/VibeEngineeringAI/pb-rs/pull/12) integrates `main` at `998d4efca7ab4f82fe2c9b6ba85d712a352beb9d`. Its production source inventory matches `8349eda980c6c0f7e692890e86c385ccaadb6928`; subsequent changes harden validation and correct reporting. These new measurements compare the integrated production code directly against that main commit, separately from the older frozen experiments.

## Review findings and responses

Three independent initial agent reviews covered production correctness, evidence accuracy, and the validation harness. The production review found no substantiated regression and reported agreement between the span and original tree decoders on 7,977 adversarial inputs. Supplemental Go 1.27 comparisons do not establish diagnostic parity with the repository's pinned Go 1.25.4 oracle.

The other reviews found five P2 harness issues and one P3 reporting issue:

- TLS checks could accept wrong requests and missing PromQL data. The fixture now enforces method, endpoint, fixture authentication, fixed query/dataset/time parameters, and complete response data.
- Wrapper provenance hashed the script alone. Generated wrappers now carry execution manifests; checks verify the backing ELF, emulator, wrapper and manifest before and after invocations, including the offline reference.
- A final binary-hash failure could leave successful halving claims in a report. Reports now retain running/failed/completed state, and confirmations are published only after final provenance checks succeed.
- Profiler startup failures could strand a previously acquired native CLI. Constructor unwinding now closes its log and aborts/reaps the owned child.
- Fixture timeouts could leave a CLI in another session alive. A separate Linux subreaper supervisor owns and reaps descendant sessions on timeout, cancellation, and driver death; a nominally successful fixture with live descendants fails.
- The strict 21-case corpus was incorrectly credited with table-filter coverage. The report now attributes that coverage to the Rust tests.

A new follow-up reviewer confirmed all six findings closed at `1c7611f`, with no substantiated new finding. Its additional probes checked nested detached children, driver death, and rejection of a zero-exit fixture with live descendants. A subsequent checker-only correction at `becd09a` distinguishes JSON booleans from numeric samples: Python's `True == 1` must not admit a boolean SQL value. Equivalent integer/float numeric spellings remain acceptable. The final focused suite has **15 passing tests**.

A second fresh follow-up agent reviewed `f9e0556` and found no substantiated new or unresolved finding. It independently recomputed the paired statistics, checked source/artifact provenance, replayed all 48 TLS captures, and verified the boolean-sample correction.

## Direct comparison with main on Boat

Fresh VM `bx_rjd3u7tw`: **8 vCPU / 16 GB, AMD Ryzen 9 9950X, Linux 6.8, Ubuntu**, Rust 1.97.1. Both release builds use the same Cargo.lock, default features and existing thin-LTO release profile. The candidate's complete `src` inventory, Cargo manifests and build script were checked against the integrated checkout. No builds, profilers, or package installation ran concurrently with native measurement.

The dense PromQL workflow loads 1,000 series × 256 samples, shows the chart/table, switches views, idles, and quits at 120×40. Each theme has seven randomized measured baseline/candidate pairs and one excluded warmup pair. CPU is the CLI's native wait4 user+system time; memory is its peak RSS. These are process counters, not whole-VM utilization.

| Theme | CPU before → after, marginal medians | Median paired CPU reduction | Peak RSS before → after, marginal medians | Median paired RSS reduction |
| --- | ---: | ---: | ---: | ---: |
| Dark | 1,566.861 → 582.478 ms | 63.0% | 195,633,152 → 72,388,608 bytes | 63.0% |
| Light | 1,828.822 → 637.725 ms | 65.1% | 195,391,488 → 72,388,608 bytes | 63.0% |

Both 50% targets pass the existing paired-bootstrap interval rule in each theme. The CPU ratio intervals are **0.327–0.374** (dark) and **0.339–0.355** (light); both memory ratio upper bounds are below 0.371. Marginal medians need not reproduce the median of paired ratios. Wall-time reductions are 20.3% and 21.2%; scripted holds and idle time remain included. SQL and sustained navigation are outside this new campaign, and no blanket 50% claim applies to every workflow or to the static artifact.

Frozen integrated artifacts:

- Main GNU release SHA-256: `cf81e96f43a64f1ce9f84d58da5ad45d62a53cd0b49a61963bd9e5199deb75a1`.
- Candidate GNU release SHA-256: `f48bd341aac457f3991c6db80a132a7e0c09cd52cf45d476b9c56d7b4403d36b`.

## Functional validation

- Integrated Rust suite: **870 passed, zero failed, nine ignored**, using Rust 1.96 and two test threads. Clippy with warnings denied and rustfmt pass. Production sources did not change after these checks.
- Corrected supervisor with the integrated candidate: **51/51 independent process/PTY effects** on Boat; another 51/51 passed with the local debug binary.
- Direct main/candidate styled comparison: **21 cases / 42 native roles**, zero comparison issues. The performance runs add 16 pairs / 32 roles including warmups, also with zero comparison issues.
- TLS: **48 checks** across the integrated GNU, original frozen GNU, and original frozen static artifacts. Each artifact ran four native checks and four under each of qemu64, Nehalem and Haswell. The final type-aware checker revalidated the preserved request observations and stdout/stderr of all 48 captures; HTTP requests were not repeated after the checker-only boolean correction. The nine QEMU wrapper startup checks also passed.

The emulated checks establish functional coverage of these CPU profiles, not native Intel performance. The static artifact still describes the pre-integration source freeze; these checks do not publish an updated static release. ARM64, macOS, Windows and older kernels are not covered by this Boat campaign.

Python harness: **1,058 passed and 35 skipped across 41 modules**, taking each module's final result. The complete invocation initially returned 1 because one unchanged Git-provenance fixture expected its temporary directory to be outside any Git checkout, while the chosen scratch directory was inside this worktree. Rerunning that unchanged 29-test module with `TMPDIR=/home/ajs/.cache/pb-pr12-review-tmp` passed. Both logs are retained, and the final aggregate uses that module rerun. Earlier failures from `/tmp` disk quota and an unconfigured Go shim are also preserved. No assertions or deadlines were relaxed.

## Evidence

The [integrated evidence record](evidence/boat-20261107-pr12.json) includes every measured/warmup pair's native counters, exact case-definition hashes, source inventories, artifact identities, functional comparison results, and all 48 TLS observations and output streams. Full case definitions, captures, original reports, binaries, helper sources, build logs, and host metadata are retained locally in the evidence archive under `harness/results/pr-review-20261107/`. The archive and binary are not published by this PR.

The performance runner used for capture is `1c7611f`. The final TLS checker is `becd09a`; its source hash and the explicit replay procedure are retained separately. Original reports are preserved rather than relabeled as having been produced by a later checker.

The local archive is `harness/results/pr-review-20261107/pr12-evidence.tar.gz`, SHA-256 `6d9411baa876fc928c5c94c2171aa9f36a89313894ee0ab6687c3c4599804be8`. Its tracked summary, raw report hashes, all four binary hashes, and current production-source inventory were verified before VM removal. Boat deletion operation `bdop_c3fb8ad1662842709641ae1157aa7476` completed at `2026-11-07T21:52:28.645Z`. Pre-existing VMs were left unchanged.

## CI follow-up: closed-pipe fixture race

The [compatibility run on `d2fa011`](https://github.com/VibeEngineeringAI/pb-rs/actions/runs/42418261327/job/128540933869) failed `closed_stdout_terminates_with_sigpipe`: the CLI exited normally rather than receiving SIGPIPE. The existing test fixture used raw `libc::pipe` descriptors without close-on-exec. Another concurrently spawned process could inherit the reader in the short interval before its parent closed it, keeping the pipe readable during the tested CLI's write.

The new `sibling_inherited_reader_does_not_change_closed_output` regression deliberately leaks a legacy pipe reader into a blocked sibling before testing the CLI's closed output. With the child setup disabled, the CLI exits normally and the regression fails deterministically. The fixed fixture creates the actual broken pipe, duplicates its writer onto stdout or stderr, and closes both original endpoints inside the CLI child's pre-exec callback. The actual reader never exists in the parent or a sibling. Close-on-exec alone was insufficient because a forked child can retain a reader before its exec completes.

All ten process tests pass with the original SIGPIPE assertions unchanged, followed by **200 eight-thread repetitions / 2,000 test executions**, zero failures. Clippy and rustfmt pass. The regression's blocked helper is released and reaped before assertions. No production code or recorded benchmark source changes for this fix. Negative-control, intermediate and passing logs are retained under `harness/results/pr12-ci-fix/`.
