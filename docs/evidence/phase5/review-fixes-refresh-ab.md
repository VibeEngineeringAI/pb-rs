# Focused refresh performance investigation — Codex / gpt-6.1-sol

PR: https://github.com/VibeEngineeringAI/pb-rs/pull/3. Frozen source `7cda2ad`; fix source `4fcbd51`. Measurements performed October 13, 2026 America/Chicago (October 14 UTC).

**Overall verdict B: the only remaining source-associated signal is a small, approximately 2% user-cycle cost in large set-role; no confirmed >5% CPU regression remains. The refresh regression question resolves to A.** The original stored-binary refresh comparison has a repeatable startup bias of approximately **0.09–0.20 ms wall / 0.11–0.16 ms CPU**, accompanied by 0.5–0.6 MiB lower fix RSS and about 21–23 more minor faults. A byte-identical local frozen rebuild reproduces that bias. Equal cache eviction and sequential warm-up of the original frozen and fix files removes the RSS/fault gap and refresh slowdown. The refresh effect is executable-file mapping/cache history. The smaller set-role cycle effect remains after disabling FD adjustment and restoring old trim, with effectively unchanged instruction counts; binary layout/microarchitecture is the likely cause, inferred rather than established at a specific instruction/cache set. No source rollback or behavioral fix is warranted.

The initial shared SQL CPU slowdown does not reproduce. Native large set-role timings vary by session: normalized CPU is +1.24%, **+0.0755 ms** with absolute 95% interval **[−0.1355, +0.2975] ms**, ratio upper bound 1.0496. Pinned fix/frozen user-cycle point estimates across three independent counter sessions are +2.29%, +1.78%, +1.69% (about 0.20–0.27 million extra user cycles per invocation); the first two CIs exclude zero increase, the third includes it. This supports a small residual rather than the earlier >5% CPU estimate. Native wall uncertainty still includes a >5% effect, so exact equality is not proven.

## Protocol and artifacts

First two independent sessions use the unchanged CLI harness, five excluded warmups and 100 measured samples per binary per workload, fresh process/HOME, captured pipes, sequential randomized binary order and fixture reset outside timing. Administration includes Go once per round because its CLI requires Go; the scoped Go A/A oracle is reused from prior validation. Session 2 reverses candidate insertion order while keeping administration seed 2026, so inventory definitions remain identical. Shared SQL uses seeds 2026 and 2027. Native `pb-meter` supplies child wall/CPU/RSS.

Administration contracts validate exits, output, request ordering/authentication/bodies, state/commits and token/config effects. Both primary sessions and the 200-sample normalized session are complete and all measured samples valid. CIs are the existing harness’s independent bootstrap of median ratios: 2,000 resamples, seed 2026, pointwise 95%, without multiplicity adjustment. No samples are silently dropped.

A fresh preliminary Go A/A failed one dataset-add milestone check before any candidate timing: after sending headers, the fixture records `headers_ns`; a client can send its next request before that bookkeeping timestamp. Preserved under `discarded-session1-aa-fixture-race/`. `subset-aa-oracle.json` is a provenance-marked subset of the complete passing previous `validation/admin-confirm/report.json` Go A/A, with unchanged workload specifications. The normal harness validates workload, runner, model and Go hashes before reuse.

Variant/normalized/counter sessions call unchanged `phase5_workloads.run_sample` directly through external `variants.py`, using the same validated scoped oracle and fresh/reset fixture protocol. This avoids repeating Go in diagnostic rounds. Variant session: five labels × 100 samples; normalized: two labels × 200 samples; counters: four labels × 50 samples, followed by two independent set-role-only sessions of four labels × 100 samples with different random seeds and reversed label insertion order. One variant dataset-add copy sample hits the same timestamp race; that comparison has **no accepted ratio/CI**, while other comparisons are valid. Its invalid sample is retained in the report.

All artifacts are under `/home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab`. Main-worktree tracked files were not edited by this investigation; no commit, push or GitHub post was made. Another authorized agent staged docs/evidence during the investigation; its changes were left untouched. The detached diagnostic worktree `/home/ajs/.cache/pb-phase5/tmp/rev/ab-wt` was removed after the builds.

## Host and timing separation

Host: Intel Core i9-13900HK, 20 logical CPUs (performance cores 0–11, efficiency cores 12–19), Linux 7.2.5-3-omarchy; powersave governor. Primary/variant/normalized sessions use normal scheduling across all CPUs. Counter targets alone are pinned to performance CPU 2. Background VMs, Parseable services and desktop/orchestration processes continue to run; host noise is therefore present. No builds or other timing sessions from this investigation overlap accepted benchmark timing.

| Timed session | Before load average | After load average | Samples per label |
| --- | --- | --- | ---: |
| session1-admin | 6.41, 6.66, 6.92 | 6.50, 6.61, 6.89 | 100 |
| session1-shared | 6.50, 6.61, 6.89 | 7.18, 6.75, 6.93 | 100 |
| session2-admin | 7.18, 6.75, 6.93 | 6.29, 6.57, 6.86 | 100 |
| session2-shared | 6.29, 6.57, 6.86 | 6.58, 6.63, 6.87 | 100 |
| variants-session | 8.02, 7.58, 7.22 | 7.73, 7.71, 7.30 | 100 |
| normalized-session | 7.44, 7.65, 7.29 | 8.28, 8.06, 7.47 | 200 |
| perf-session | 7.55, 7.91, 7.43 | 7.64, 7.85, 7.44 | 50 |
| perf-layout-session | 8.45, 7.97, 7.59 | 8.34, 8.01, 7.62 | 100 |
| perf-repeat-session | 8.02, 7.94, 7.53 | 7.75, 7.86, 7.52 | 100 |

Full before/after `uptime`, process lists, inherited limits, worktree status and UTC timestamps are in each `*.host.txt`. The primary files’ UTC timestamp appears at the end because the initial writer buffered it; future snapshots flush it first. Diagnostic builds and exact rustc invocations are preserved in `build.log` and `build-commands.json`.

## Binary provenance

`candidate-final-manifest.json` records Rust 1.96.0, Go 1.25.4 and harness version settings. Current rustc is `1.96.0 (ac68faa20 2026-05-25)`, LLVM 22.1.2. Standard and Cloud frozen rebuilds both reproduce their original SHA-256 exactly, ruling out toolchain, flag or source differences. Release builds use `--locked --release`, the repository’s thin LTO / one codegen unit / stripped profile, `PB_VERSION=v0.0.0-harness`, and respectively empty / `harness-orchestrator-token` Cloud auth token. Dependencies were cloned into a separate external `CARGO_TARGET_DIR`; main targets were not built or modified.

| Label | Meaning | Standard SHA-256 | Cloud SHA-256 |
| --- | --- | --- | --- |
| prior | stored frozen 7cda2ad | `ec9765a4dbd4ad48fd758965cba8aaba50a525d6c7eded89b8c84c4cfcc31b0f` | `2a91136a347fabc9256603958beb7709d0da4e8146c9adb0269c039a468bb9c4` |
| rust | stored fix 4fcbd51 | `9bd38b9db4f4f8e76cd43384cb024dddd4d122c6bc109d29a8afa483ae0e2c6d` | `457619bb5cfd6603288cf6ab21a9f606ef2dbfb618bd65f97860d8849f03e9de` |
| copy | shutil.copy2 of frozen; unchanged bytes, new inode | `ec9765a4dbd4ad48fd758965cba8aaba50a525d6c7eded89b8c84c4cfcc31b0f` | `2a91136a347fabc9256603958beb7709d0da4e8146c9adb0269c039a468bb9c4` |
| nofd | 4fcbd51 with startup FD call omitted | `757800ad44c7078d8f743be85796955e1ee1e6306e6f5e0b793dffe34bc1afbe` | `cfda3cf30dce306eb23d67a6483f192362a99590a189f8fd563de82c5b8b93ab` |
| rebuilt | local 7cda2ad rebuild; unchanged bytes, new inode | `ec9765a4dbd4ad48fd758965cba8aaba50a525d6c7eded89b8c84c4cfcc31b0f` | `2a91136a347fabc9256603958beb7709d0da4e8146c9adb0269c039a468bb9c4` |
| oldtrim | 4fcbd51, FD startup omitted, old trim restored; prefix retained | `b393efcd699eb083acd380b2e83f9858e16d47d6c927c8a256eff43b5cb0d219` | N/A (standard-only set-role diagnostic) |

Frozen standard/Cloud sizes: 7,617,656 / 7,618,112 bytes. Fix: 7,617,800 / 7,618,256 bytes (+144 bytes each). Cloud ELF `.text` begins 64 bytes later in fix; code/layout shifts exist, which can affect cache/branch behavior without increasing instructions. The refresh gap does not require a layout regression; the small residual set-role cycle difference is consistent with layout effects.

## Session 1 — original stored binaries

| Workload | Metric | Frozen median | Fix median | Fix/frozen ratio [95% CI] | Absolute delta |
| --- | --- | ---: | ---: | --- | ---: |
| p5-refresh-rejected-no-commit | Wall ms | 2.429 | 2.602 | 1.0716 [1.0142, 1.1184] | +0.174 |
| p5-refresh-rejected-no-commit | CPU ms | 1.682 | 1.839 | 1.0930 [1.0157, 1.1728] | +0.157 |
| p5-refresh-rejected-no-commit | RSS MiB | 6.672 | 6.074 | 0.9104 [0.9029, 0.9190] | -0.598 |
| p5-refresh-dataset-add | Wall ms | 3.000 | 3.087 | 1.0291 [0.9849, 1.0964] | +0.087 |
| p5-refresh-dataset-add | CPU ms | 1.945 | 2.103 | 1.0812 [1.0258, 1.1313] | +0.158 |
| p5-refresh-dataset-add | RSS MiB | 6.746 | 6.189 | 0.9175 [0.9074, 0.9257] | -0.557 |
| p5-refresh-user-list-small-json | Wall ms | 5.986 | 6.190 | 1.0340 [0.9958, 1.0701] | +0.204 |
| p5-refresh-user-list-small-json | CPU ms | 2.901 | 3.013 | 1.0384 [0.9945, 1.1051] | +0.112 |
| p5-refresh-user-list-small-json | RSS MiB | 6.938 | 6.352 | 0.9155 [0.9056, 0.9244] | -0.586 |
| p5-user-set-role-large | Wall ms | 11.077 | 10.718 | 0.9676 [0.9295, 1.0461] | -0.359 |
| p5-user-set-role-large | CPU ms | 6.106 | 6.011 | 0.9845 [0.9489, 1.0432] | -0.095 |
| p5-user-set-role-large | RSS MiB | 10.023 | 10.082 | 1.0058 [0.9994, 1.0090] | +0.059 |
| p3-bench-rows-1-json | Wall ms | 2.270 | 2.235 | 0.9843 [0.9409, 1.0317] | -0.036 |
| p3-bench-rows-1-json | CPU ms | 1.929 | 1.888 | 0.9787 [0.9309, 1.0412] | -0.041 |
| p3-bench-rows-1-json | RSS MiB | 6.277 | 6.316 | 1.0062 [0.9953, 1.0153] | +0.039 |

## Session 2 — original stored binaries, reversed candidate order

| Workload | Metric | Frozen median | Fix median | Fix/frozen ratio [95% CI] | Absolute delta |
| --- | --- | ---: | ---: | --- | ---: |
| p5-refresh-rejected-no-commit | Wall ms | 2.401 | 2.557 | 1.0648 [1.0021, 1.1263] | +0.156 |
| p5-refresh-rejected-no-commit | CPU ms | 1.666 | 1.798 | 1.0792 [1.0033, 1.2062] | +0.132 |
| p5-refresh-rejected-no-commit | RSS MiB | 6.670 | 6.113 | 0.9165 [0.9071, 0.9264] | -0.557 |
| p5-refresh-dataset-add | Wall ms | 2.877 | 3.060 | 1.0636 [1.0199, 1.1206] | +0.183 |
| p5-refresh-dataset-add | CPU ms | 1.952 | 2.072 | 1.0617 [1.0214, 1.1021] | +0.120 |
| p5-refresh-dataset-add | RSS MiB | 6.752 | 6.166 | 0.9132 [0.9007, 0.9241] | -0.586 |
| p5-refresh-user-list-small-json | Wall ms | 6.042 | 6.145 | 1.0170 [0.9896, 1.0448] | +0.103 |
| p5-refresh-user-list-small-json | CPU ms | 2.962 | 3.073 | 1.0376 [1.0068, 1.1087] | +0.111 |
| p5-refresh-user-list-small-json | RSS MiB | 6.896 | 6.369 | 0.9235 [0.9136, 0.9326] | -0.527 |
| p5-user-set-role-large | Wall ms | 10.113 | 10.708 | 1.0589 [1.0040, 1.1060] | +0.596 |
| p5-user-set-role-large | CPU ms | 5.714 | 5.874 | 1.0280 [1.0009, 1.0609] | +0.160 |
| p5-user-set-role-large | RSS MiB | 10.053 | 10.094 | 1.0041 [0.9983, 1.0094] | +0.041 |
| p3-bench-rows-1-json | Wall ms | 2.230 | 2.124 | 0.9524 [0.9041, 1.0206] | -0.106 |
| p3-bench-rows-1-json | CPU ms | 1.879 | 1.847 | 0.9830 [0.8825, 1.0352] | -0.032 |
| p3-bench-rows-1-json | RSS MiB | 6.314 | 6.344 | 1.0046 [0.9932, 1.0109] | +0.029 |

## Five-way diagnostic variants

Each median cell is wall ms / CPU ms / RSS MiB. Ratios and intervals compare each label against the original stored frozen file in this same session.

| Workload | Label | Medians W / C / R | Wall ratio [95% CI] | CPU ratio [95% CI] | RSS ratio [95% CI] |
| --- | --- | --- | --- | --- | --- |
| p5-refresh-rejected-no-commit | prior | 2.497 / 1.719 / 6.686 | reference | reference | reference |
| p5-refresh-rejected-no-commit | rust | 2.607 / 1.853 / 6.068 | 1.0438 [1.0038, 1.1248] | 1.0782 [1.0343, 1.1477] | 0.9077 [0.8990, 0.9104] |
| p5-refresh-rejected-no-commit | copy | 2.428 / 1.730 / 6.170 | 0.9722 [0.9446, 1.0477] | 1.0064 [0.9519, 1.0763] | 0.9229 [0.9127, 0.9257] |
| p5-refresh-rejected-no-commit | nofd | 2.554 / 1.820 / 6.121 | 1.0227 [0.9905, 1.0905] | 1.0585 [0.9891, 1.1324] | 0.9156 [0.9052, 0.9223] |
| p5-refresh-rejected-no-commit | rebuilt | 2.616 / 1.885 / 6.080 | 1.0478 [1.0068, 1.1231] | 1.0969 [1.0416, 1.1651] | 0.9094 [0.9010, 0.9166] |
| p5-refresh-dataset-add | prior | 3.015 / 2.002 / 6.785 | reference | reference | reference |
| p5-refresh-dataset-add | rust | 3.120 / 2.126 / 6.178 | 1.0346 [0.9882, 1.0764] | 1.0622 [0.9923, 1.1502] | 0.9105 [0.8992, 0.9203] |
| p5-refresh-dataset-add | copy | 2.992 / 2.007 / 6.262 | N/A (invalid sample) | N/A (invalid sample) | N/A (invalid sample) |
| p5-refresh-dataset-add | nofd | 3.111 / 2.115 / 6.209 | 1.0318 [0.9724, 1.0907] | 1.0567 [0.9783, 1.1510] | 0.9151 [0.9042, 0.9229] |
| p5-refresh-dataset-add | rebuilt | 3.160 / 2.157 / 6.191 | 1.0481 [0.9832, 1.0951] | 1.0779 [1.0289, 1.1688] | 0.9125 [0.9001, 0.9205] |
| p5-refresh-user-list-small-json | prior | 6.137 / 2.916 / 6.885 | reference | reference | reference |
| p5-refresh-user-list-small-json | rust | 6.270 / 3.069 / 6.383 | 1.0217 [0.9878, 1.0553] | 1.0525 [1.0168, 1.1112] | 0.9271 [0.9200, 0.9341] |
| p5-refresh-user-list-small-json | copy | 6.277 / 3.062 / 6.418 | 1.0228 [0.9897, 1.0551] | 1.0502 [1.0157, 1.0799] | 0.9322 [0.9237, 0.9398] |
| p5-refresh-user-list-small-json | nofd | 6.208 / 3.080 / 6.385 | 1.0115 [0.9737, 1.0498] | 1.0562 [0.9990, 1.1007] | 0.9274 [0.9201, 0.9345] |
| p5-refresh-user-list-small-json | rebuilt | 6.357 / 3.160 / 6.359 | 1.0357 [1.0025, 1.0721] | 1.0837 [1.0316, 1.1248] | 0.9237 [0.9153, 0.9311] |
| p5-user-set-role-large | prior | 11.323 / 6.081 / 10.008 | reference | reference | reference |
| p5-user-set-role-large | rust | 11.211 / 6.106 / 10.088 | 0.9901 [0.9344, 1.0804] | 1.0040 [0.9778, 1.0508] | 1.0080 [1.0008, 1.0117] |
| p5-user-set-role-large | copy | 10.839 / 5.992 / 10.572 | 0.9572 [0.9118, 1.0669] | 0.9854 [0.9461, 1.0291] | 1.0564 [1.0490, 1.0608] |
| p5-user-set-role-large | nofd | 10.961 / 5.994 / 10.039 | 0.9680 [0.9080, 1.0252] | 0.9858 [0.9598, 1.0150] | 1.0031 [0.9938, 1.0078] |
| p5-user-set-role-large | rebuilt | 11.119 / 6.010 / 10.027 | 0.9820 [0.9095, 1.0621] | 0.9882 [0.9552, 1.0204] | 1.0020 [0.9934, 1.0076] |

The copy/dataset-add medians use 99 valid observations descriptively; its 100th observation is invalid, so the harness refuses all three ratios/CIs. Every other variant workload/label has 100 valid measured samples and no warmup issues.

The no-FD variant does not eliminate the refresh effect. More decisively, **rebuilt frozen has exactly the original bytes but refresh-rejection CPU +9.69% [4.16%, 16.51%] and similarly lower RSS**. Its +0.1665 ms CPU delta is as large as the fix’s +0.1345 ms in this session. A source-change attribution cannot explain this unchanged-byte result.

## Equal cache treatment — 200 samples per binary

| Workload | Metric | Frozen median | Fix median | Fix/frozen ratio [95% CI] | Absolute delta |
| --- | --- | ---: | ---: | --- | ---: |
| p5-refresh-rejected-no-commit | Wall ms | 2.598 | 2.560 | 0.9855 [0.9670, 1.0063] | -0.038 |
| p5-refresh-rejected-no-commit | CPU ms | 1.868 | 1.840 | 0.9847 [0.9573, 1.0125] | -0.028 |
| p5-refresh-rejected-no-commit | RSS MiB | 6.094 | 6.102 | 1.0013 [0.9955, 1.0071] | +0.008 |
| p5-refresh-dataset-add | Wall ms | 3.068 | 3.079 | 1.0037 [0.9709, 1.0389] | +0.011 |
| p5-refresh-dataset-add | CPU ms | 2.083 | 2.072 | 0.9947 [0.9574, 1.0497] | -0.011 |
| p5-refresh-dataset-add | RSS MiB | 6.182 | 6.195 | 1.0022 [0.9940, 1.0082] | +0.014 |
| p5-refresh-user-list-small-json | Wall ms | 6.328 | 6.232 | 0.9848 [0.9651, 1.0162] | -0.096 |
| p5-refresh-user-list-small-json | CPU ms | 3.179 | 3.053 | 0.9601 [0.9408, 1.0139] | -0.127 |
| p5-refresh-user-list-small-json | RSS MiB | 6.387 | 6.383 | 0.9994 [0.9924, 1.0024] | -0.004 |
| p5-user-set-role-large | Wall ms | 11.006 | 11.140 | 1.0122 [0.9783, 1.0575] | +0.134 |
| p5-user-set-role-large | CPU ms | 6.091 | 6.166 | 1.0124 [0.9783, 1.0496] | +0.075 |
| p5-user-set-role-large | RSS MiB | 10.066 | 10.057 | 0.9990 [0.9959, 1.0039] | -0.010 |

Before this session, each original standard/Cloud executable was opened read-only, given `POSIX_FADV_DONTNEED`, closed, then sequentially read in full for SHA-256. This affects only those files’ kernel page cache; bytes and hashes remain unchanged. The same five excluded process warmups follow. No global cache flush or host configuration change was performed. Exact records: `cache-normalization.json`.

After this treatment, the refresh CPU point deltas become −4.0% (user list), −0.5% (dataset add) and −1.5% (rejection), with every CPU interval including 1.0 and upper bounds below 1.05. The RSS gap becomes approximately 0.0–0.2%.

## Page faults and RSS attribution

| Workload | Original S1 faults frozen / fix | Original S2 faults frozen / fix | Normalized faults frozen / fix |
| --- | ---: | ---: | ---: |
| p5-refresh-rejected-no-commit | 254 / 274 | 253 / 274 | 274 / 273.5 |
| p5-refresh-dataset-add | 256 / 278 | 256 / 277 | 278 / 278 |
| p5-refresh-user-list-small-json | 311 / 334 | 312 / 333 | 333 / 333 |
| p5-user-set-role-large | 1334 / 1334 | 1334 / 1334 | 1334 / 1334 |

All native measured samples have zero major faults. More minor faults with lower RSS are consistent with smaller effective file-backed fault-around mappings: the process takes more faults but maps fewer neighboring executable pages. The exact kernel folio/fault-around mechanism was not traced, so that mechanism is an inference. The causal dependence on executable cache/file identity is supported directly by unchanged-byte copies/rebuilds and removal of the gap after controlled cache treatment.

A separate untimed paused refresh-dataset-add probe captures `/proc/PID/smaps` while the first response is delayed. These are mapping RSS snapshots, not native peak RSS:

| File | Read-only binary KiB | Executable binary KiB | Writable/relocated binary KiB | Total process RSS KiB |
| --- | ---: | ---: | ---: | ---: |
| prior | 800 | 2880 | 208 | 6476 |
| rust | 724 | 2560 | 208 | 6072 |
| copy | 768 | 2620 | 208 | 6272 |

The mapped-code difference accounts for most of the paused-process RSS drop; writable binary mappings are equal. There is no need to infer an allocator saving from raising FD limits. Raw snapshots and limits are in `smaps-*.txt` and `limits-*.txt`.

## Changed paths and syscall isolation

- Refresh success consumes/replays the 401 in `api::ApiClient::send_authenticated`, refreshes tokens, and then runs successful command processing. It does not call `CliError::status` or `trim_bytes`.
- Refresh rejection buffers `{"error":"invalid_grant"}`, returns `ApiError::HttpStatus`, wraps it as `ApiError::Refresh`, and converts it with `From<ApiError>` to `CliError::new`. It does not run the changed trim function. Therefore an old-trim diagnostic build would not isolate an executed behavioral difference here.
- `prefix_message` is only used by PromQL run error handling. None of these administration or successful SQL workloads invokes it.
- Inherited CLI soft/hard `RLIMIT_NOFILE` are both 524,288. `raise_open_file_limit` performs one `getrlimit` (Linux `prlimit64` read), then returns. Strace of frozen/fix refresh rejection confirms the fix’s extra `RLIMIT_NOFILE` read and **no setrlimit write**. The fixture independently sets its own soft limit to its hard limit in `serve`; it does not change the CLI’s limits.

These paths are established from unchanged `harness/phase5_workloads.py` workload definitions, fixture/token behavior, `src/api.rs`, `src/error.rs` and `src/main.rs`. Strace logs are diagnostic only and are not accepted timing samples.

## Pinned-core hardware counters

Each target is launched via `taskset -c 2 perf stat --no-big-num -x, -e cpu_core/instructions/u,cpu_core/cycles/u,page-faults`, 5 warmups and 50 samples, randomized sequential order. Performance-core events have 100% running time; counters are user-mode only. Perf reports `page-faults:u` in this environment. Only these counters are interpreted: supervisor wall/CPU/RSS from the perf-wrapped process include profiler overhead and are **not comparable** to native timing above. Exact CSV per sample is retained as `perf_raw` in the JSON report.

| Workload | Label | Instructions median | Instructions ratio [95% CI] | Cycles median | Cycles ratio [95% CI] | Faults median |
| --- | --- | ---: | --- | ---: | --- | ---: |
| p5-refresh-rejected-no-commit | prior | 1,904,684 | reference | 2,068,274 | reference | 245 |
| p5-refresh-rejected-no-commit | rust | 1,903,532 | 0.9994 [0.9938, 1.0008] | 2,060,280 | 0.9961 [0.9799, 1.0272] | 245 |
| p5-refresh-rejected-no-commit | copy | 1,904,006 | 0.9996 [0.9936, 1.0003] | 2,064,141 | 0.9980 [0.9769, 1.0261] | 241.5 |
| p5-refresh-rejected-no-commit | nofd | 1,904,660 | 1.0000 [0.9940, 1.0007] | 2,018,638 | 0.9760 [0.9621, 1.0038] | 244 |
| p5-refresh-dataset-add | prior | 2,172,814 | reference | 2,332,238 | reference | 248 |
| p5-refresh-dataset-add | rust | 2,173,592 | 1.0004 [0.9995, 1.0015] | 2,329,688 | 0.9989 [0.9718, 1.0155] | 248 |
| p5-refresh-dataset-add | copy | 2,173,564 | 1.0003 [0.9995, 1.0015] | 2,336,286 | 1.0017 [0.9741, 1.0198] | 246 |
| p5-refresh-dataset-add | nofd | 2,174,160 | 1.0006 [0.9995, 1.0020] | 2,326,624 | 0.9976 [0.9660, 1.0173] | 248 |
| p5-refresh-user-list-small-json | prior | 3,418,669 | reference | 3,436,636 | reference | 305 |
| p5-refresh-user-list-small-json | rust | 3,419,912 | 1.0004 [0.9981, 1.0026] | 3,539,044 | 1.0298 [0.9931, 1.0564] | 306 |
| p5-refresh-user-list-small-json | copy | 3,415,258 | 0.9990 [0.9972, 1.0016] | 3,481,904 | 1.0132 [0.9787, 1.0500] | 303 |
| p5-refresh-user-list-small-json | nofd | 3,417,888 | 0.9998 [0.9981, 1.0020] | 3,443,347 | 1.0020 [0.9691, 1.0272] | 306 |
| p5-user-set-role-large | prior | 29,584,008 | reference | 11,652,486 | reference | 1292 |
| p5-user-set-role-large | rust | 29,584,111 | 1.0000 [0.9999, 1.0001] | 11,919,260 | 1.0229 [1.0109, 1.0344] | 1293 |
| p5-user-set-role-large | copy | 29,582,225 | 0.9999 [0.9998, 1.0001] | 11,680,399 | 1.0024 [0.9881, 1.0182] | 1273 |
| p5-user-set-role-large | nofd | 29,582,128 | 0.9999 [0.9998, 1.0001] | 11,847,942 | 1.0168 [1.0037, 1.0300] | 1293 |

All four workloads have essentially unchanged user instruction counts. After cache treatment, refresh dataset-add and rejection show no user-cycle regression in the first pinned session. Refresh user-list has a +2.98% cycle point estimate with CI [−0.69%, +5.64%], so that counter result alone does not establish an increase.

### Independent set-role repeat and old-trim isolation

Repeat: 100 samples/label, seed 7941, insertion order `nofd, copy, rust, prior`. Final layout isolation: 100 samples/label, seed 13579, insertion order `oldtrim, nofd, rust, prior`. Both use the same CPU 2, five warmups, perf events and oracle. No builds overlap their measurements; all samples/warmups are valid. The standard-only old-trim variant disables startup FD adjustment and restores `trim_bytes` exactly from 7cda2ad, while retaining the prefix fix. Its build/source diff are in `oldtrim-build-commands.json`, `oldtrim-build.log`, `oldtrim-source.diff`; its new executable file receives the same scoped eviction/sequential-read treatment before timing. The temporary worktree was removed again.

| Session | Label | Instructions median | Instructions ratio [95% CI] | User cycles median | Cycles ratio [95% CI] | User faults median |
| --- | --- | ---: | --- | ---: | --- | ---: |
| perf-repeat-session | nofd | 29,582,831 | 0.9999 [0.9999, 1.0000] | 12,135,465 | 1.0237 [1.0106, 1.0393] | 1292.5 |
| perf-repeat-session | copy | 29,582,352 | 0.9999 [0.9999, 1.0000] | 11,846,570 | 0.9993 [0.9863, 1.0089] | 1273 |
| perf-repeat-session | rust | 29,583,892 | 1.0000 [0.9999, 1.0000] | 12,066,088 | 1.0178 [1.0090, 1.0327] | 1292 |
| perf-repeat-session | prior | 29,584,668 | reference | 11,854,797 | reference | 1292.5 |
| perf-layout-session | oldtrim | 29,583,074 | 1.0000 [0.9999, 1.0000] | 12,270,536 | 1.0311 [1.0055, 1.0532] | 1292 |
| perf-layout-session | nofd | 29,582,756 | 0.9999 [0.9999, 1.0000] | 12,105,140 | 1.0172 [1.0003, 1.0356] | 1293 |
| perf-layout-session | rust | 29,583,645 | 1.0000 [0.9999, 1.0000] | 12,101,870 | 1.0169 [0.9981, 1.0319] | 1292 |
| perf-layout-session | prior | 29,584,503 | reference | 11,900,541 | reference | 1293 |

Disabling FD startup does not erase the cycle effect (nofd: +1.68%, +2.37%, +1.72% across sessions). Restoring old trim also retains it (+3.11% [0.55%, 5.32%] in the final isolation). Since neither trim nor prefix is executed by successful set-role, remaining source differences point to compilation/layout associated with the prefix change rather than runtime error processing. Standard ELF `.text` starts at `0x1b3040` in frozen, `0x1b3080` in both nofd and oldtrim, all three with size `0x55ecc6`. The specific microarchitectural penalty was not localized; no cache-miss/branch-miss profile or deliberate linker-alignment repair was performed. This is a bounded attribution, not proof that one particular alignment is the cause.

A ~2% user-cycle difference cannot be converted exactly to native CPU milliseconds on this heterogeneous, variable-frequency host; the measured normalized native CPU point delta is +0.0755 ms with an interval including zero. Treat it as negligible for the stated >5% concern while preserving this residual finding.

## Reproduction commands

Working directory for the primary commands:
```text
/home/ajs/.t3/worktrees/pb-rs/review-phase5-pr3-sol-opus
```
Environment for all benchmark/diagnostic drivers:
```bash
export TMPDIR=/home/ajs/.cache/pb-phase5/tmp/rev/val-tmp
export GOTMPDIR="$TMPDIR"
export PYTHONDONTWRITEBYTECODE=1
```
Accepted primary CLI invocations (also preserved with environment in `commands.jsonl`):
```bash
python3 harness/phase5_workloads.py run --go /home/ajs/.cache/pb-phase5/pb-go --go-cloud /home/ajs/.cache/pb-phase5/pb-go-cloud --meter /home/ajs/.t3/worktrees/pb-rs/review-phase5-pr3-sol-opus/bin/pb-meter --warmup 5 --repetitions 100 --self-check-repetitions 2 --oracle-report /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/subset-aa-oracle.json --artifacts failures --out /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/session1-admin --candidate prior=/home/ajs/.cache/pb-phase5/candidate-final/pb --candidate-cloud prior=/home/ajs/.cache/pb-phase5/candidate-final/pb-cloud --candidate rust=/home/ajs/.cache/pb-phase5/fix-candidate/pb --candidate-cloud rust=/home/ajs/.cache/pb-phase5/fix-candidate/pb-cloud --workload p5-refresh-rejected-no-commit --workload p5-refresh-dataset-add --workload p5-refresh-user-list-small-json --workload p5-user-set-role-large
python3 harness/run.py run --suite docs/evidence/phase5/regression-suite.json --fixture /home/ajs/.t3/worktrees/pb-rs/review-phase5-pr3-sol-opus/bin/pb-fixture --meter /home/ajs/.t3/worktrees/pb-rs/review-phase5-pr3-sol-opus/bin/pb-meter --warmup 5 --repetitions 100 --seed 2026 --out /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/session1-shared --case p3-bench-rows-1-json --binary prior=/home/ajs/.cache/pb-phase5/candidate-final/pb --binary rust=/home/ajs/.cache/pb-phase5/fix-candidate/pb
python3 harness/phase5_workloads.py run --go /home/ajs/.cache/pb-phase5/pb-go --go-cloud /home/ajs/.cache/pb-phase5/pb-go-cloud --meter /home/ajs/.t3/worktrees/pb-rs/review-phase5-pr3-sol-opus/bin/pb-meter --warmup 5 --repetitions 100 --self-check-repetitions 2 --oracle-report /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/subset-aa-oracle.json --artifacts failures --out /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/session2-admin --candidate rust=/home/ajs/.cache/pb-phase5/fix-candidate/pb --candidate-cloud rust=/home/ajs/.cache/pb-phase5/fix-candidate/pb-cloud --candidate prior=/home/ajs/.cache/pb-phase5/candidate-final/pb --candidate-cloud prior=/home/ajs/.cache/pb-phase5/candidate-final/pb-cloud --workload p5-refresh-rejected-no-commit --workload p5-refresh-dataset-add --workload p5-refresh-user-list-small-json --workload p5-user-set-role-large
python3 harness/run.py run --suite docs/evidence/phase5/regression-suite.json --fixture /home/ajs/.t3/worktrees/pb-rs/review-phase5-pr3-sol-opus/bin/pb-fixture --meter /home/ajs/.t3/worktrees/pb-rs/review-phase5-pr3-sol-opus/bin/pb-meter --warmup 5 --repetitions 100 --seed 2027 --out /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/session2-shared --case p3-bench-rows-1-json --binary rust=/home/ajs/.cache/pb-phase5/fix-candidate/pb --binary prior=/home/ajs/.cache/pb-phase5/candidate-final/pb
```
Subsequent diagnostic drivers (run separately, sequentially; variants timing starts only after all builds complete):
```bash
python3 /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/diagnose.py
python3 /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/build_variants.py
python3 /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/variants.py variants
python3 /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/normalize.py
python3 /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/variants.py perf
python3 /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/perf_analysis.py
python3 /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/perf_repeat.py
python3 /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/build_oldtrim.py
python3 /home/ajs/.cache/pb-phase5/tmp/rev/sol/refresh-ab/perf_layout.py
```
`build-commands.json` and `oldtrim-build-commands.json` give every build/checkout command and environment. `variants.py` implements only external orchestration, repeatedly calling the unchanged native meter and harness validators. To reproduce on fresh paths, use a new artifact directory because the drivers deliberately refuse to overwrite completed session directories.

## Recommendation and limits

Keep the linear trim fix, PromQL byte-preserving prefix and FD parity correction. **No source performance fix is warranted for these workload budgets.** The small residual cycle effect persists even with old trim; reverting the linear trim would reintroduce the demonstrated quadratic error-body path without solving it. A strict sub-2% cycle budget would require separate linker/code-layout profiling of the prefix-only residual, followed by native timing validation; that is not a justified behavioral rollback here. For this sub-millisecond startup comparison, normalize executable cache treatment before timing, or include byte-identical new-inode controls and minor-fault/file-RSS evidence. Five process warmups alone did not normalize the original executable mapping states.

The original stored-binary refresh effect is measurable under its initial cache state, so dismissing those observations as only random noise would be inaccurate. However, its approximately 0.1–0.2 ms size and −0.6 MiB RSS correlate with file/cache state, and the refresh regression disappears under equal cache treatment. The remaining set-role cycle signal is approximately 2%, with native CPU time still statistically compatible with no increase. These are local fresh-process, loopback, warm-cache results, not production/live-network or cold-storage claims. Pointwise bootstrap intervals and a busy heterogeneous host cannot establish zero overhead; the extra startup syscall has a small nonzero cost that this workload evidence does not isolate precisely.
