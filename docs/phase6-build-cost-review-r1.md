# Phase 6 build-cost tooling review, round 1

This is an independent review of the offline dependency inventory and the future
isolated build-cost runner. It does not accept the whole phase. I ran no Cargo,
Go, rustc, readelf, tool-version probe, product, fixture, meter, native helper,
profile, network request, real flock or quiet token. Every process, signal and
lock entry point was patched to fail before any production code ran. Mocks
allowed only simulated calls. Temporary files went under
`/home/ajs/.cache/pb-phase6/tmp/build-cost-review-r1`, never `/tmp`. Review
artifacts are in `/home/ajs/.cache/pb-phase6/build-cost-review-r1`.

**Admission decision: NOT ADMITTED for any native build.** N1–N7 below, plus the
N8 documentation correction and the timeout test/document correction, must be
fixed and re-reviewed before a coordinator issues a quiet ACK.

## Identities (before = after, unchanged)

| File | SHA-256 |
|---|---|
| `harness/phase6_build_cost.py` (36,433 B) | `fbb8dafd9159e498a6a0fc7309e02987708b805a61fb444b206f45930c61ff1f` |
| `harness/test_phase6_build_cost.py` (19,912 B) | `8f35b4591000869cf9cfc74df6341d0e502dacd3cb90bea0b563ee3ff02a2af7` |
| `docs/phase6-build-cost-plan.md` (12,867 B) | `90198dd8702bbf366ccdb04d9e4df3276843022bef62d51a4ff741af50bcebee` |
| `docs/evidence/phase6/dependency-download-sizes-r6.json` (255,908 B) | `9d2ff2a0a78d89d144e756fdc3e469a7f7d6d23727a0f3775171415ee80a1dd0` |
| `prepared-source/prepared.json` (11,277,965 B) | `75467a917c3b40a08892d0d6c2cbba1c7dce14c2cdc84f1e91779e7ef8f04357` |
| `prepared-source/inventory.json` | `92d4ed970e31b2ba2dd2ef419f45de6d5af32cea944c8c4cb70256073c4e2e92` |
| `build-cost-preparation-v1/delivery.json` | `effc395253ec1917bdc9c03556f9feac1dc17aeaf1a28a847fd74777479f1cce` |
| candidate manifest (cache copy and `docs/evidence/phase6/candidate-review-r6.json`) | `a90917f86241c7df045015250ee69cc4d8090264285be46672bd0b865441fb3e` |
| `harness/meter.c` / `bin/pb-meter` (prepared meter identity) | `5d7ce9dc…f070e9` / `17c3b73491b2a31e465f58eda9f210000b5cd886a4ba9fc0764dc3576899127c` |
| review repro `repro_review.py` / `mutations.py` / `repro-run1.log` | `e8a4f730…3687e` / `6468b0d4…0aa70` / `31146ff3…b8` |

The prepared root still holds only `base candidate cargo-home home inventory.json
prepared.json`. It has no `runs/` and no `registry/src`.

## Verified correct (selected checks)

- The base lock from read-only `git show 4a812d5:Cargo.lock` hashes to
  `ca8ba623…c10a`. That equals the snapshot `base/Cargo.lock` and the evidence
  `lock_sha256.base`. The candidate snapshot lock is `aa35f338…`.
- The base snapshot file set equals `git ls-tree` (40 files). The 47 candidate
  snapshot hashes equal the frozen manifest `sources`.
- The evidence `runner` identity equals the current runner (`fbb8daf…`). The
  evidence file is byte-identical to the cache `dependency-inventory-v1.json`.
  Its packages and totals equal the prepared receipt and
  `prepared.inventory_totals`.
- The document's figures match the JSON: 160/229/69 registry identities; 67/69
  added archives; 5,172,535 compressed bytes; 27,292,812 unpacked bytes, equal
  to the verified cached-source package bytes. The only package errors are
  `wasip2 1.0.4+wasi-0.2.12` and `wit-bindgen 0.57.1` (archive and source both
  missing). The totals are honestly partial. No cached source has
  missing/changed/extra files.
- Both locks contain only crates.io registry sources, so no git/path dependency
  is silently skipped. No crate name has uppercase letters, so `index_key`
  needs no lowercasing. The private index holds `wasip2` and `wit-bindgen`
  records.
- No ancestor `.cargo/config*` exists. `/home/ajs/.cargo` has no
  config/credentials files. The three `include_bytes!("../tests/…")` uses in
  `src/transport.rs` sit under `#[cfg(test)]` (line 1399), so a snapshot with
  only `src/`+Cargo files is sufficient for `--bin pb`.
- I read `meter.c`. The meter does not call `setsid`/`setpgid`, so Cargo shares
  the meter's new-session PGID (= `proc.pid`). The meter ignores SIGINT/SIGTERM,
  so SIGKILL is the right cleanup signal. `metrics_result` matches the real
  `meter.c:124-149` schema (repro H3a uses byte-exact meter output). wait4 CPU
  includes waited descendants. `ru_maxrss` is the largest single waited process,
  not a sum. The plan's semantics text (lines 168-173) is accurate.
- Same-byte parse/hash (`read_json`) and resolved-path identity are correct.

Coverage limits: I did not re-hash the 52,128-file, 1,300,627,889-byte toolchain
or the 227 archives. Those figures come from the recorded manifest. File and
lock checks were selective, as listed above.

## Necessary fixes

### N1 — High: the prepared manifest certifies itself

`run` (`phase6_build_cost.py:581-583`) and `verify` (`326-353`) only compare
`prepared.json` with what it says about itself. No caller-supplied expected
SHA-256 exists. Nothing checks that the file lives in its own `root`, or that
tools sit under the frozen `TOOLCHAIN`. `version`/`commit` are not re-derived,
and the candidate snapshot is not re-compared with the candidate manifest's
`sources`.

Repros (all guarded):

- **H1a:** Point `tools.meter` at an unreviewed file and set
  `version=v9.9.9-unreviewed`. The run reports `passed=True` and launches that
  meter with that `PB_VERSION`.
- **H1b:** Edit `candidate/Cargo.toml` and update `prepared.sources`. `verify`
  returns True even though the snapshot no longer matches the manifest.
- **H1c:** A `prepared.json` stored outside its `root` is accepted and passes.

Correction:

- Require `--prepared-sha256` and compare it to `prepared_id` before any use.
- Assert `Path(prepared_id['path']).parent == root`.
- Assert `toolchain.path == TOOLCHAIN.resolve()`, that cargo/rustc/rustdoc paths
  sit under it, and that the meter equals `<repo>/bin/pb-meter` with a recorded
  expected hash.
- Hard-bind the candidate manifest SHA (`a90917f8…`). Re-read it and require
  its `sources` to equal the candidate snapshot hashes, and
  `version == builds.pb.public_build_version`.
- Record a base tree digest that a reviewer can compare with `git ls-tree`.

### N2 — High: full hashing runs before the nonblocking lock

`run` calls `verify` at line 583, then `new_dir`, then writes `report.json`.
Only after that does it try `quiet_lock` (line 596). `verify` hashes the
1.3 GB toolchain plus the cargo-home. Once sources are warmed it also gunzips
227 archives and hashes about 265 MB of sources. With the lock held by another
campaign, a run therefore performs gigabytes of I/O inside someone else's quiet
window before failing. Repro **H4a**: order `['verify', 'lock']`, then
`BlockingIOError`, and the output directory is left behind. This matters while
the Phase 7 deadline driver is active.

Correction: take the LOCK_NB lock immediately after the cheap parse/admission,
before any `verify`/`tree`/`new_dir`. Report lock-busy without hashing.

### N3 — Medium-High: the group probe and SIGKILL happen after the leader is reaped

In `execute` (`531-543`), `proc.wait` reaps the meter first. Then
`killpg(pid, 0)` runs, and `except BaseException` catches the self-raised
`ValueError` and sends `killpg(pid, SIGKILL)`. Once every member has exited,
the PGID number can be reused, and a reused id would be killed. No liveness
check follows the kill. Repro **H2**: events are `wait → killpg(0) →
killpg(SIGKILL) → wait`. Also, a signal delivered between `Popen` and the `try`
(line 526→528), or inside `Popen`, leaves the group unkilled. The plan's claim
(lines 194-199) that the runner "kills/reaps only that group" therefore
overstates what the code does.

Correction:

- Observe the meter's exit with `os.waitid(P_PID, pid, WEXITED | WNOWAIT)` so
  the zombie leader pins the PGID.
- Detect live members by scanning `/proc/*/stat` for pgrp == pgid, excluding the
  zombie leader. Note that `killpg(…, 0)` succeeds on a zombie-only group.
- SIGKILL, then poll with a bounded wait until no live member remains, then reap.
- Block SIGINT/SIGTERM/SIGHUP with `pthread_sigmask` across `Popen` and the PID
  journal.
- Record a `group_empty_verified` field.

### N4 — Medium: captured metrics are not checkpointed first, and exceptions in `finally` drop evidence

In `run`'s `finally` (`635-643`), `metrics_result` runs, then `tree(target)`,
then `identity(binary)`, and only then a checkpoint.

- `metrics_result` (`576`) catches only `OSError`/`ValueError`. A non-object
  meter file raises `AttributeError`. Repro **H3b**: the error is recorded, but
  the sample has neither `target_regular_file_bytes` nor `executable`.
- If `tree` raises (for example on a symlink a build script left in an
  `OUT_DIR`), that exception replaces the in-flight one (such as a timeout).
- Repro **H5a**: during the later `verify` calls, the on-disk report shows
  `wall_ms=None`. The metrics reach disk only in the final write, after the
  long end-of-lock verify, so a SIGKILL or host loss there loses them.

Correction: in the `finally`, first store the metrics plus `identity(meter.json)`
and the raw termination, and `atomic_json` them. Then wrap each target and
executable measurement in its own `try` that records an error string. Make
`metrics_result` require a `dict` and catch `Exception`. This follows the
earlier N3 durable-capture principle.

### N5 — Medium: one interrupted checkpoint blocks every later report

`atomic_json` (`92-101`) uses a fixed `report.json.pending` opened with `'x'`
and never cleans it up on failure. Repro **H5b**: an `InterruptedError`
mid-dump leaves `.pending` behind. Every later write, including the final one,
raises `FileExistsError`. `run()` then raises instead of returning, and the
on-disk report is stale (`errors=[]`, one sample).

Correction: write to a `mkstemp` in the same directory, unlink it on any
exception, `os.replace`, and fsync the directory. Mask signals around
checkpoint writes.

### N6 — Medium: the window bound is not enforced

- The `+5` guard (`616`) runs before the pre-sample `tree(target)` (`622`).
  For warm-noop that hashes the whole release target. Repro **H4c** (window
  385, timeout 10): `candidate-warm-noop` launches at 380 and ends at 390,
  past the window.
- After the last sample, `tree(target)`, `verify`×3 (`644`, `652`, `439`) and
  readelf have no reserve check. Overrun is detected only after the fact, while
  the shared lock is still held. Repro **H4b**: window 380, 25 s per verify, end
  at 400. The result is `passed=False` "window exceeded", but the overrun
  already happened.
- `BOOKKEEPING_S` assumes a cleanup step that does not exist.

Correction:

- Compute pre-sample hashes and identity before the guard.
- Time each `verify` call and target hash. Before every launch, require
  `elapsed + timeout + final_reserve ≤ window`, where `final_reserve` is the
  measured target-hash cost plus 2× the longest verify plus the 10 s readelf
  timeout.
- Drop the redundant `verify` at `652` (the end check covers it) or the one at
  `607` (it repeats the previous `644`).
- Make the plan's lines 124-127 match.

### N7 — Medium: `returncode` is the meter's, not Cargo's

`meter.c` returns 0 after recording any Cargo outcome. `record['returncode']`
(`630`) and the artifact gate (`645`) therefore use the meter's exit status.
Repro **H3c**: a warm-noop where Cargo exits 101 reports `returncode=0` and
still runs readelf on the stale cold-build `release/pb`. The `passed` flag is
correctly False only because `metrics_result` checks `exit_code`.

Correction: record `meter_returncode` and `cargo_exit_code`/`termination` from
`meter.json`. Gate artifact validation on Cargo exit 0 with complete metrics.
For warm-noop, also require the executable identity to equal the cold
sample's.

### N8 — Medium (truthfulness): native C toolchain binding is overstated

Plan line 90 says "System compiler executables are bound". Both graphs build
`aws-lc-sys 0.46.0`, which compiles C through the `cc` crate (`/usr/bin/cmake`
is absent). The candidate adds `zstd-sys 2.1.1+zstd.1.5.7`. Only the gcc
driver, `ar`, `ld` and `readelf` are hashed. `cc1`, `as`, `collect2`,
`lto-wrapper`, `/usr/bin/c++`, headers, crt files and libc are not. The
prepared `ld` is probably unused, because rustc 1.96 links with the bound
toolchain's `rust-lld` (`gcc-ld/ld.lld`). Native C compilation is likely a
large share of build time, and it is not frozen.

Correction: either statically hash `/usr/lib/gcc/x86_64-pc-linux-gnu/16/`,
`/usr/bin/as` and `/usr/bin/c++`, or reword the plan to say only the gcc driver
is bound. In both cases, keep the post-build `target/release/build/*/output`
files as evidence of which native steps ran.

### Test/doc correction — the timeout test simulates something the real meter cannot do

`test_timeout_partial_metrics_and_artifacts_retained` (test lines 332-344)
writes `meter.json` before raising `TimeoutExpired`. The real group SIGKILL
kills the meter before its `fopen` (`meter.c:121`), so a timeout always yields
null metrics (repro **H3d**). Plan lines 200-202 claim timeout "preserve[s]
completed native metrics". Correct the test to expect nulls, and correct the
plan.

Mutation sweep (delivered suite against mutated copies in the review cache):

- Killed: removing `start_new_session` (M1), removing the unbound-source check
  (M7).
- **Survived:**
  - ignoring Cargo's exit code in `metrics_result` (M2);
  - removing the group liveness probe (M3);
  - removing the end-of-lock deadline check (M4);
  - removing the ancestor-config rejection (M5);
  - removing the per-sample window guard (M6);
  - removing the toolchain tree comparison (M8; only tool binaries are
    mutated).

Add tests that kill M2–M6 and M8. Use byte-exact `meter.c` records for them.

## Recommended (lower severity)

- **N9 Low-Medium:** `inspect_artifact` (`509-519`) accepts any readelf exit 0.
  Repro **H6**: an ELF32/ARM/REL header is accepted. Parse and require `ELF64`,
  `Advanced Micro Devices X86-64`, `DYN` or `EXEC` with `PT_INTERP`, and an
  empty stderr.
- **N10 Low:** `verify` (`344-352`) ignores unrecorded cargo-home files. Repro
  **H7**: an extra `wasip2-1.0.4+wasi-0.2.12.crate` and an extra index record
  still verify. Enumerate the tree against the recorded files, verified
  `registry/src`, and an explicit allowlist of Cargo bookkeeping files.
- **N11 Low:** `warm_source_cache` (`478-479`) skips any existing destination.
  Repro **H8a**: a failed extraction leaves a partial tree without
  `.cargo-ok`, the retry silently skips it, and `verify` then fails every time.
  Extract into a temporary sibling and rename it, and fail clearly on a
  directory without `.cargo-ok`.
- **N12 Low:** `inventory` (`167-185`) records `archive has nonregular members`
  but still marks the archive `checksum_verified` and counts it in
  `verified_archives` (repro **H8b**). Separate member validity from the
  checksum. This has no effect on the R6 evidence.
- **N13 Low:** an exception after `passed=True` (`655`), for example a signal
  during `orderly_signals` exit, leaves `passed=True` with a non-empty `errors`
  list (repro **H9**). Set the final pass flag only after the `with` block
  exits and require `errors == []`.
- **N14 Low:** the ACK is checked only by prefix and not bound to a window id or
  expiry. Its SHA is stored, which is good. Consider requiring a
  coordinator-issued window id and recording it.

## Documented limitations (no code fix required)

- Target sizes double-count hardlinked uplifts. Repro **H10**: 1,000 unique
  bytes are reported as 2,000. The plan already states this.
- The fixed base→candidate order means the candidate runs with page cache
  warmed by the base build (toolchain and shared dependencies). The plan should
  say explicitly that this order confounds comparison; one observation per
  condition is descriptive only.
- The candidate and added totals are partial: the two missing all-platform
  packages are unknown sizes, not zero. Whether the host build needs them is
  unverified; their index records exist, so an offline build is expected to
  either resolve or fail safely.
- Peak RSS is the largest waited process, not the summed concurrent RSS. CPU
  covers waited descendants only. Both are accurately stated.
- Source and executable hashes are checkpoint checks, not kernel-enforced
  protection.

## Reproduction

```sh
T=/home/ajs/.cache/pb-phase6/tmp/build-cost-review-r1
TMPDIR=$T GOTMPDIR=$T CLAUDE_CODE_TMPDIR=$T PYTHONDONTWRITEBYTECODE=1 \
  python3 -I -B /home/ajs/.cache/pb-phase6/build-cost-review-r1/repro_review.py
```

19 guarded repros, all OK; observations in `repro-run1.log`. The delivered
suite passed independently: 19/19 under its own guards, in 0.208 s. The
mutation sweep is `mutations.py`. No forbidden call was recorded.
