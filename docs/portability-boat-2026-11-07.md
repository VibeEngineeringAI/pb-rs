# Boat runtime portability validation — 2026-11-07

The frozen optimized Rust binary was rerun on two fresh Boat VMs, including the smallest 2-vCPU / 4-GB shape. Its source and SHA-256 still match the performance investigation. No production code was changed during this validation.

These checks apply to the frozen sources preserved in `113e4ed`, before PR integration of `main` at `998d4ef`. The binaries and performance numbers below do not describe the subsequently integrated PR head. The [integrated validation report](pr12-review-validation-2026-11-07.md) records that head's direct comparison and checks using the hardened validation runners.

Both fresh VMs expose an AMD Ryzen 7 9700X. They extend capacity/reproducibility coverage, not native CPU-architecture coverage. Boat's [documented hardware fleet](https://boat.dev/compare/boat-vs-sail) is x86-64 AMD; older CPU instruction sets were therefore checked with QEMU inside a Boat VM. Those emulated checks establish functional compatibility, not native Intel performance. ARM64, Windows, macOS, and older kernels remain untested.

## Native results

| Fresh VM | Workload / theme | Measured pairs | Paired CPU reduction | Paired peak RSS reduction |
| --- | --- | ---: | ---: | ---: |
| `bx_qanm8eez`, 2 vCPU / 4 GB | Dense PromQL / dark | 7 | 65.6% | 63.0% |
| `bx_qanm8eez`, 2 vCPU / 4 GB | Dense PromQL / light | 7 | 64.9% | 63.0% |
| `bx_n8tv4s27`, 4 vCPU / 8 GB | Dense PromQL / dark | 7 | 65.2% | 63.1% |

Each population has one additional excluded warmup pair. Each CPU and RSS ratio's 95% paired-bootstrap upper bound is below 0.5, so the two 50% targets remain confirmed for the dense load/chart/table workflow. These are independent populations, not pooled samples. The same SQL control on the small VM showed an 8.2% point CPU reduction and 1.1% point RSS increase; no halving claim applies to it.

The original optimized GNU binary passed **2,018 offline byte comparisons**, **51 independent process/PTY effect cases**, and **21 strict PromQL workflow comparisons** on the small VM. Native counters and captures use the same runner and definitions as the [performance report](performance-boat-2026-11-07.md). Builds, emulators, and other test campaigns did not run concurrently with native performance measurements on the same VM.

## Older CPU instruction sets

QEMU 8.2.2 executed the actual frozen release binary, not a rebuild customized for each CPU. A separate static CPUID probe records the exposed features:

| Emulated CPU | SSE4.1 / SSE4.2 | AES | AVX / AVX2 | AVX-512 | Original binary process checks | Original binary TLS checks |
| --- | --- | --- | --- | --- | ---: | ---: |
| `qemu64` | no / no | no | no / no | no | 51/51 | 4/4 |
| `Nehalem` | yes / yes | no | no / no | no | 51/51 | 4/4 |
| `Haswell-noTSX` | yes / yes | yes | yes / yes | no | 51/51 | 4/4 |

The 153 process cases reuse the checked-in independent `tests/interactive_effects.rs` fixture, covering HTTP policy, SQL windows, saved queries, administration, PromQL modes, stale completions, Unicode, and duplicate JSON fields. The 12 TLS checks perform real SQL and PromQL requests against a loopback server over TLS 1.2 and TLS 1.3. They use the repository's test-only CA through a process-local `SSL_CERT_FILE`; no system trust store or account credentials are changed. The `qemu64` and Nehalem checks exercise crypto without AES instructions. No illegal-instruction failure occurred in these cases.

## Distribution compatibility finding and static artifact

The original GNU binary requires **glibc 2.39**. This is an actual distribution limitation: running it in the Ubuntu 20.04 control exits 1 with missing GLIBC version errors. Successful execution on current Boat VMs alone would not have established older-distribution compatibility.

A second artifact was built from the same product sources for `x86_64-unknown-linux-musl`. `file` identifies it as a stripped static-PIE x86-64 executable; ELF inspection finds neither an interpreter requirement nor a `DT_NEEDED` shared-library dependency.

The portable artifact is retained locally at:

`harness/results/boat-portability-20261107/pb-portable`

SHA-256: `e946ee022d8dfc8f074a38e8a09cf2fa4c1c0e51cd690c5827bf028f9ddc0edf`.

Its validation passes:

- **Ubuntu 20.04 and Alpine 3.20 containers:** 8/8 version, JSON version, help, and agent-JSON smoke checks. Exact image digests and output are preserved. Containers share the Boat host kernel; these do not establish older-kernel compatibility.
- **Native:** 2,018 exact offline comparisons against the GNU optimized binary, 51 process/PTY effects, 21 strict PromQL workflow comparisons, and four TLS checks.
- **Emulated:** four TLS checks on each of `qemu64`, Nehalem, and Haswell; all 51 process/PTY effects additionally pass on `qemu64`.

Build reproduction on an x86-64 Linux machine with Rust 1.97.1, the musl target installed, and `musl-gcc` available:

```sh
CARGO_BUILD_JOBS=1 \
CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER=musl-gcc \
CC_x86_64_unknown_linux_musl=musl-gcc \
cargo build --locked --release --target x86_64-unknown-linux-musl
```

The same existing release profile is used: thin LTO, one codegen unit, stripping, and default features. No `target-cpu=native` flag is used. This validation artifact is not a published release or an installer change.

### Static runtime performance tradeoff

A separate seven-pair native campaign compares the original pre-optimization GNU baseline with the optimized static artifact. CPU medians are 2,281.808 → 1,034.204 ms; RSS medians are 195,244,032 → 47,869,952 bytes (186.2 → 45.7 MiB). Median paired reductions are **52.2% CPU** and **75.5% RSS**.

The CPU paired-ratio interval is **0.448–0.525**, so the static artifact's 50% CPU target is **not confirmed** under the existing interval rule. Its memory target is confirmed (ratio interval approximately 0.245–0.246). The static runtime consumes more CPU in these experiments than the optimized GNU runtime. This comparison includes a libc/allocator change and must not be attributed solely to the source optimizations. Use the GNU artifact on compatible modern distributions when its confirmed CPU improvement matters; the static artifact supplies the verified older-distribution option.

## Native test-suite timing finding

The first default-parallel native suite reported 576 library passes and one failure in `api::tests::streamed_body_deadline_includes_oauth_refresh_and_redirect`. Its 180-ms total deadline expired before the test could acquire response headers after a deliberately delayed refresh. The fixture's read assertion then failed because the request was canceled. This is preserved as an initial failed run, not erased or counted as passing.

The same unchanged test passed three isolated runs. The complete unchanged suite then passed with bounded concurrency:

```sh
CARGO_BUILD_JOBS=2 cargo test --locked --all-targets -- --test-threads=2
```

Result: **700 passed, zero failed, nine explicitly ignored optional/benchmark cases**. This is evidence of timing sensitivity in the default parallel test run; no timeout threshold or test assertion was relaxed, and no production behavior was modified to obtain the passing result.

## Reproduction and evidence

`harness/portability_vm.py` drives the existing offline corpus and independent process fixture, creates CPU-model wrappers, and verifies real TLS requests. For example:

```sh
python harness/portability_vm.py wrappers \
  --binary /absolute/path/pb-optimized --out /absolute/path/new-wrappers
python harness/portability_vm.py effects \
  --binary /absolute/path/new-wrappers/pb-qemu64 \
  --out /absolute/path/new-effects --jobs 2
python harness/portability_vm.py tls \
  --binary /absolute/path/new-wrappers/pb-qemu64 \
  --out /absolute/path/new-tls
```

The compact tracked evidence is [boat-20261107-portability.json](evidence/boat-20261107-portability.json). Original captures, process streams, fixture requests, CPU-feature probes, hardware/source snapshots, container-image metadata, initial failed and successful test logs, build logs, and helper sources are preserved in two archives under `harness/results/boat-portability-20261107/`. The portable binary is also available separately. Each archive is checked against the recorded SHA-256 before its VM is removed. No pre-existing Boat VM is modified.

Both newly created VMs (`bx_qanm8eez`, `bx_n8tv4s27`) were removed after both archives passed SHA-256 and content verification. Their absence was confirmed with `boat --json list`; pre-existing VMs were left unchanged.
