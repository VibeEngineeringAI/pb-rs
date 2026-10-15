# Linux-only generated shared measurement runner, R2

R2 is a separate, reviewable adaptation for the declared full 15-case attempt. It is **not promoted into the shared harness**, and it changes neither the existing Python 3.11+/macOS contract nor candidate code. R1 and its document remain unchanged. Fresh root/Opus review is required before measurement use; these author checks imply no review acceptance, suite admission, or performance result.

The retained R2 packet is `/home/ajs/.cache/pb-phase7/shared-runner-owned-cleanup-r2/`. Its canonical generated runner is `recipe-generated/run.py`, with copied `recipe-generated/cases.py` and `generation.json`. Generated files are read-only (0444) and the generated directory is 0555. The final packet manifest binds the bytes; these permissions are an accidental-write guard, not a claim of protection against the owner deliberately changing permissions.

## Durable inputs and exact scope

New durable files:

- [Historical-base-to-R2 patch](evidence/phase7/shared-runner-linux-ownership.patch).
- [Exact generation recipe](evidence/phase7/shared_runner_linux_ownership_generate.py).
- [Pure regression module](evidence/phase7/shared_runner_linux_ownership_test.py), with explicit source loading and guards against real process operations.
- [Explicit native own-child control](evidence/phase7/shared_runner_linux_ownership_native_control.py); importing it starts nothing.
- [Validation summary](evidence/phase7/shared-runner-linux-ownership-r2-validation.json) and [final native trace](evidence/phase7/shared-runner-linux-ownership-r2-native.json).

| Input/output | SHA-256 |
| --- | --- |
| Historical/live `harness/run.py` base | `b280d1295a4b351e6f2d838c56acaac58b2247bb77fc9881587bb7bbe849a076` |
| Generated R2 `run.py` | `d65cf16df8ba26f0b3b99efe8f0ed408d1c7bfaee1dd1a16d19fd6c4723a2f5e` |
| Durable LF patch | `62e1b8c90eb9cb89d4b8ff844bb6079ef8c7af2f3dfad7845756fd4909fdef4b` |
| Copied `cases.py` | `b892e1b592111d8115ceddac61ca732942dd6b9b7ef900ce4a023cff8420ebfa` |
| Explicit 15-case suite | `3c73172426a990d2c4771b74786245281095ed2640d525f680c8dffbde1e92bc` |

The base is the exact `harness/run.py` in commit `ea70f7ad7d501d59b26cf6397f6a0b1f28143fc4`, Git blob `a840e4bcaa18d2bc0c6d0e4512cd764e5c981f51`. The `cases.py` blob is `6156158396bbec17033a21ed99fe9c3a74054d27`. Both files are retained in `before/harness/`. The generator requires their full SHA-256 values, the patch hash, and the expected output hash. It applies exact LF hunks without subprocesses, fuzz, or editing the input tree, and refuses an existing output directory. `r1-to-r2.patch` in the cache separately exposes only the changes since R1.

R2 retains R1's non-reaping exit observation, group cleanup before final `wait4`, acquisition unwind, and separate observation time. Its additional changes are limited to Linux runtime advertising/pre-child guards and the concrete Fixture lifecycle:

- Fixture state begins with `proc = log = None`. The log/Popen acquisition and readiness handling are inside the cleanup region; startup catches BaseException so KeyboardInterrupt also closes acquired resources. Ordinary startup errors retain the existing RuntimeError wrapper, stderr details, banner fields, selector timeout, and request behavior.
- A temporary parent SIGINT handler defers the Popen/assignment handoff. It restores and invokes the previous handler after ownership is registered. The fixture child does not inherit a newly blocked SIGINT mask. Popen argv, stdio and `start_new_session=True` are unchanged.
- `close()` skips every PID/group operation when a prior Popen returncode is recorded. Otherwise it confirms exact-child WNOWAIT ownership, kills the owned group before the final exact-PID `wait4`, and sets Popen.returncode. Successful reaping clears `self.proc`; log and stdout close and the log reference clears. Repeat close is safe. ECHILD raises without signaling and clears lost ownership so a later close cannot retry the stale PGID. Other signaling failures leave the unreaped process reference available rather than pretending cleanup succeeded.
- The generated entry point and acquisition helpers reject unsupported platforms before child creation, and reject nondefault SIGCHLD before acquisition. No compatibility fallback or Python/macOS version bump was applied to the live runner.

The result construction/native JSON override is byte-identical to the historical base, including resource-source labels, timestamps, wall computation, CPU/RSS/IO mapping, output hashes, schema, validation and failure precedence. The body of `run()` is AST-identical after removing its initial ownership guard: one RNG, original case order, warmups, repetitions, binary-role insertion order, and shuffles remain intact. Fixture `trace()` and `milestones()` are unchanged. No fixture protocol, timeout, output rule, schedule or CLI feature was redesigned. The generic `kill_group()` is unchanged and is not a general-purpose safe owner for unrelated historical callers.

## Dependency and runtime boundary

`cases.py` is the only unconditional local import; its byte-for-byte copy imports only standard-library `json`. The explicit `docs/evidence/phase5/regression-suite.json` has all 15 original phase 1–3 cases and no `phase4` key, so this attempt never reaches the optional `phase4_workloads` import. All other imports used by this suite are Python standard-library modules, including the added contextlib helpers. The standard-library/parser behavior therefore remains tied to the Python executable root declares; these tests used Python 3.14.7 and do not add a separate Python 3.11 runtime qualification.

Root must use the **same absolute generated runner for every role in the same full-suite invocation**, with explicit `--suite`, `--fixture`, `--meter`, binary paths, output path and the already declared options. The relocated `DEFAULT_METER` is deliberately unused; its code was not rewritten. The report's runner hash will identify the generated source, as intended. Do not compare it to historical reports as though their runner identities matched.

Outside this adapter's declared use: generic suite generation/listing, phase4 workloads and oracle-relative resources, other harness-module imports, historical Phase6 callers, interactive/PTY/Flight/tail workloads, and macOS execution. No extra sibling modules or contract trees were copied to make those modes appear supported.

## Reproducible generation and validation

The completed recipe invocation used retained base inputs and generated the canonical read-only directory. To reproduce in another **new** cache directory after review:

```sh
TMPDIR=/home/ajs/.cache/pb-phase7/tmp PYTHONDONTWRITEBYTECODE=1 python docs/evidence/phase7/shared_runner_linux_ownership_generate.py \
  --base-run harness/run.py --cases harness/cases.py \
  --out /home/ajs/.cache/pb-phase7/shared-runner-linux-owned-NEW
```

The current live base matches, but any future base drift fails the pinned hash check. A historical base extracted from the commit above can instead be supplied explicitly. The generator records resolved provenance in `generation.json`; all roles must use its single generated copy. Do not overwrite the frozen R2 directory.

The durable pure module can be run explicitly against a generated copy:

```sh
TMPDIR=/home/ajs/.cache/pb-phase7/tmp PYTHONDONTWRITEBYTECODE=1 python docs/evidence/phase7/shared_runner_linux_ownership_test.py \
  /home/ajs/.cache/pb-phase7/shared-runner-linux-owned-NEW/run.py
```

The native control is a separate opt-in command, not import-time test discovery:

```sh
TMPDIR=/home/ajs/.cache/pb-phase7/tmp PYTHONDONTWRITEBYTECODE=1 python docs/evidence/phase7/shared_runner_linux_ownership_native_control.py \
  --runner /home/ajs/.cache/pb-phase7/shared-runner-linux-owned-NEW/run.py \
  --out /home/ajs/.cache/pb-phase7/tmp/shared-runner-native-NEW.json
```

It requires Linux WNOWAIT, /proc, an initially childless isolated interpreter, a new result path, a default SIGCHLD disposition, and parent subreaper support. It sets a 45-second observer alarm and allows at most four seconds for final owned-child cleanup. Its only exec targets are tiny Python control programs; descendants sleep at most 10 seconds. Source imports are guarded before explicit execution. Each cleanup signal is preceded by successful exact-child non-reaping ownership validation; it never sends a numeric signal after its own reap. Only this isolated interpreter's direct/adopted children are enumerated. A passing path permits reaping already-terminated descendants but forbids test-cleanup signals from assisting the generated runner.

## Execution record and limitations

Completed before the root pause: the new pure suite passed **39/39** (`logs/pure-final.log`), and the first seven-path native control passed in **0.44790989998728037 s**, creating nine owned children and reaping all nine. The root pause was ACKed with zero active processes. Nothing was executed between that ACK and explicit ROOT RELEASE.

After release, the native test was strengthened to disallow cleanup assistance on passing paths and check output/timer prerequisites before acquisition. Only that changed native test was rerun: **0.4362258210312575 s**, nine owned children, zero remaining, subreaper restored. Thus **18 actual children total were created and reaped across two R2 control commands**. No native commands remain active.

The native paths cover ordinary exit 19 with actual wait4 rusage propagation, an exited leader with a descendant holding pipes, an exited leader with a descendant redirecting stdio, signal 15 versus numeric exit 143, Fixture repeat close, and a real parent SIGINT during the Popen handoff. Real traces show WNOWAIT still finding the exited leader immediately before group signaling, a sole final reap, no later group access, and the original exit observation surviving an injected 30 ms finalization delay. Adopted descendants terminated from the adapter's group signal and were reaped by the test parent without additional signals. The fixture control also checked that the child's SIGINT mask remained unblocked.

The native controls intentionally bypass the native meter and real fixture binary using explicit Python launch substitutions. Their measurements remain **supervisor_wait4_fallback**, and their missing-meter failure remains visible. Exact real user/system CPU, RSS, faults, context switches and block-operation values were checked against the final kernel wait4 return. This is not new proof of native pb-meter mapping or native client RSS accuracy. Native mapping preservation is supported by unchanged source plus deliberately distinct synthetic-native and supervisor payload assertions in the pure tests.

Two new pure Fixture controls against the retained R1 source failed at the expected signal-after-reap site. During that comparison the test double lacked Popen.wait; it was corrected without changing the adapter, and the changed positive Fixture test alone was revalidated. The preceding 39-test pass is tied to its retained test-source snapshot, while the final module and selected revalidation are separately retained. No unchanged full suite was repeated after release.

Author test-development failures remain in the packet: the first 39-test run had two errors because the selector double incorrectly required a data argument; the first R1 Fixture control had one real defect failure and one missing-wait-double error; an initial static check incorrectly passed an AST list to ast.dump. Those doubles/checks were corrected, their source snapshots/logs retained, and the appropriate changed checks passed. The generated runner itself did not change during those corrections. Static checks confirm the byte-identical mapping, unchanged schedule body/Fixture request methods, exact suite/dependency identities, unchanged live runner, and intact R1 packet.

No Go/Rust CLI, actual fixture binary, meter binary, compiler, build, profiler, common lock, performance campaign or full shared regression ran. Candidate source was not edited. Functional acceptance and the future whole 15-case attempt belong to root after fresh review. The worst-case sum of all per-command timeouts is not a prerequisite for attempting that whole suite under a separately validated hard bound; incomplete means failed/unadmitted. No splitting, pooling, case/count reduction, or RNG-order change is authorized by this packet.

Ownership still assumes the standalone single-threaded runner, default SIGCHLD, no competing reaper, and descendants staying in their acquired group. Observer SIGKILL/default SIGTERM, arbitrary external reapers, OOM, escaped descendants, and failures inside Popen before it returns a handle remain outside the demonstrated cleanup guarantee. Cleanup syscall failures propagate. Existing blocking final waits and fixture banner behavior were preserved; this adapter supplies no standalone whole-run hard bound.
