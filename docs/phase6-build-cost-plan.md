# Phase 6 build cost: R5 narrow implementation response

The [independent R5 review](phase6-build-cost-review-r5.md), SHA-256
`c4c522916becb02389105d065ccc8ddcc30123a1939f4317e9f3cf46de1840a7`, accepts
the prior R3 closures in source+mock but does **not admit native helper controls**.
This response changes only the same runner, tests and plan, plus fresh
`build-cost-preparation-v1/r5-corrections/` evidence. The prior three exact file bytes
are preserved under `r5-corrections/historical/`; all old attempts and reviews remain.
R5 changes failed-spawn classification, full-schedule admission and cleanup summaries,
with targeted regression witnesses. It does not redesign the accepted R3 stop/HOLD model.
It is implementation evidence, not independent acceptance. A fresh **limited delta
review of these corrections** must precede any separately authorized native validation.

The [independent R3 delta review](phase6-build-cost-review-r3.md), digest
`064d2a7e94a1a93fbe9cad5e094481d7ce6db4d0e1e706dd8d26db481a05ef11`, did
**not admit native helper controls**. This narrow response changes only the owned
runner, tests, this plan and fresh `build-cost-preparation-v1/r3-corrections/` cache.
It keeps the parent-selected HOLD and exact-key receipt decisions. It implements
R3's stop model, caller-visible outcome/recovery, cleanup and receipt deltas. It is
implementation evidence, not independent acceptance. R5's limited next gate now applies.

No Cargo, compiler/version/readelf, product, fixture, meter, native helper, profiler,
network, real signal/process probe/flock or real receipt/token ran here. Tests install
guards before production imports and use simulated kernel calls and small fictional
fixtures in the new R5 cache. No installed toolchain hash, real archive/source copying
or extraction, ready preparation, agent or commit occurred. Previous reviews,
evidence, failed attempts, preparations and shared scratch directories remain
historical. The three prior owned file bytes are preserved in
`r3-corrections/historical/`; all R1/R2 corrections and review caches remain unchanged.

## Historical inventory and interpretation

[The inventory](evidence/phase6/dependency-download-sizes-r6.json) remains unchanged.
It observes cached archives/source, not build cost or current downloads. Base commit
is `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`. Locked all-platform counts are
161 base / 230 candidate, including the workspace; registry identities are 160 / 229,
with 69 added and none replaced. This does not identify the actual host compiled graph.

| Graph | Verified archives | Known compressed package bytes | Known unpacked regular-file bytes |
|---|---:|---:|---:|
| Base | 160/160 | 33,739,731 | 238,238,778 |
| Candidate | 227/229 | 38,912,266 | 265,531,590 |
| Added | 67/69 | 5,172,535 | 27,292,812 |

`wasip2 1.0.4+wasi-0.2.12` and `wit-bindgen 0.57.1` have missing sizes.
An exact name/version/source/checksum counts once per graph. SHA agreement and
valid archive members are separate statuses: only both contribute usable size sums.
Compressed bytes exclude HTTP/TLS/index traffic and downloadable host tools.
Unpacked bytes are source lengths, separate from runtime binaries, disk allocation
and RSS. Historical binary sizes 10,712,616 / 7,617,800 bytes remain separate facts.
No new inventory observation is claimed.

Cold means a fresh **target/artifact state**, with pre-extracted local dependencies,
warm local Cargo cache and uncontrolled OS cache. Full provenance verification before
each sample reads the Rust toolchain and dependency payloads, warming their page cache.
Preparation extraction occurs outside the measurement window; it must be settled
before root issues a receipt. No window-internal extraction/writeback is introduced
only before the base sample. Warm no-op repeats the identical command without a
source edit; it is not an incremental edit rebuild. Fixed base/candidate order
confounds shared warm caches. One cold observation per label is descriptive only;
ratios remain null. Wait4 CPU covers waited descendants; peak RSS is the largest
single waited process, not summed concurrent RSS. Pathwise artifact lengths may count
hardlinked uplifts twice and do not measure allocated blocks or I/O traffic.

## Preparation v3 and authoritative identities

Historical preparation `75467a917c3b40a08892d0d6c2cbba1c7dce14c2cdc84f1e91779e7ef8f04357`
and the R1
v2 draft are not admitted for a new run. **No replacement ready preparation exists.**
Future `prepare` requires separately authorized bounded copy/hash work under the common
lock into a fresh task-owned root; it never edits the current worktree or cleans a
shared target. It copies exact reviewed base/current snapshots and private cache,
extracts only verified archives through exclusive temporary siblings, verifies the
extracted payload against archives, and binds a compact extracted tree receipt before
publishing `state: ready`. Failed partial extraction is explicit; only its exclusive
temp is eligible for cleanup. Budget-expired private partials remain for explicit repair.

Preparation schema 3 retains `reviewed_manifest` and `candidate_manifest` identity
pairs `{declared, selected}`, each with resolved path, SHA-256 and byte size. Both
paths are independently checked; identical content copies are allowed. Reviewed
manifest digest is
`75467a917c3b40a08892d0d6c2cbba1c7dce14c2cdc84f1e91779e7ef8f04357`.
The authoritative frozen R6 candidate digest is
`a90917f86241c7df045015250ee69cc4d8090264285be46672bd0b865441fb3e`.

Base identity includes commit and digest of the reviewed complete name/hash/size/mode
records (canonical JSON, not a Git tree-object ID). Candidate file records must exactly
match the reviewed source set and authoritative candidate build manifest. Public
version must match; neutral `PB_COMMIT=""` is explicit. Tool roles/hash/size and the
complete selected/declared Rust toolchain tree are anchored to the reviewed manifest.
Selected cargo/rustc/rustdoc must be in the selected toolchain's `bin/`. The existing
Linux x86_64 / Rust 1.96 policy is explicit; other selected identical-content tool
paths are content-bound rather than hardcoded. No prepared claim can self-certify an
arbitrary meter/version/source/toolchain by changing its own JSON.

`extracted_sources` is `{tree_sha256, files, regular_file_bytes}` over the exact
relative file records beneath private `cargo-home/registry/src`. The compact receipt
keeps the manifest below the 512 KiB admission bound; verification still hashes the
full file set including modes and metadata and validates source against the bound
archives. Archive/index files must match their reviewed receipts; extra archives,
indexes, configs, credentials or source packages fail. Only explicit Cargo lock/global
cache bookkeeping names can vary. The preparation's `.cargo-checksum.json` under `registry/src` is an intentional
extra (Cargo does not normally write it there); it is harmless layout metadata,
verified and included in the exact extracted-tree receipt. Private HOME must remain
an empty, unlinked directory.
The preparation owns a fixed `runs/` namespace for outputs and consumption markers.
Future preparation binds `verification_reserve_s` to the larger of 30 seconds
(a planning floor) and its observed complete verification wall cost. This small
metadata field is covered by the caller-reviewed prepared digest. Initial admission
and launch reserve use at least that bound, then increase for actual verification.
Missing older mock metadata uses the explicit 30-second floor; this is no timing
measurement of the real toolchain. Failure markers and process/report journals
remain bounded and durable after work expiry. A preparation-failed marker forbids
admission even if a stopped final publication left a ready-shaped JSON file.

## Receipt v2 and future API

N14 is R1 finding 14: an ACK string alone did not bind a prepared build and finite
window. The parent selected a caller-bound, exact-key receipt with single use.
Receipt creation and authorization remain root-owned. ACK digests, `issuer` and token
syntax are coordination evidence and **never grant permission or establish quietness**.

**Draft schema example only — NOT AUTHORIZATION; placeholders are deliberately invalid.**
No real receipt is minted or available in this response.

```json
{
  "schema_version": 2,
  "issuer": "coordinator",
  "window_id": "EXAMPLE-NOT-AUTHORIZATION",
  "run_id": "<unique-run-basename>",
  "output": "<reviewed-preparation-root>/runs/<unique-run-basename>",
  "start_utc": "<explicit UTC start>",
  "deadline_utc": "<explicit UTC deadline>",
  "prepared_sha256": "<root-reviewed ready-v3 digest>",
  "jobs": 2,
  "timeout_s": 600,
  "warm_noop": false,
  "ack_sha256": "<SHA-256 of exact bounded ACK-file bytes>"
}
```

The future Python API is
`run(prepared_path, prepared_sha256, output, receipt_path, receipt_sha256, ack_path,
jobs, timeout, warm_noop=False)`. CLI uses required `--prepared-sha256`,
`--coordinator-receipt`, `--receipt-sha256`, **`--ack-file`**, `--output`, `--jobs`,
`--timeout-s`, and optional `--warm-noop`. There is no plaintext token argument,
release file, arbitrary shell or common-lock descriptor passed to children.

Prepared and receipt SHA checks precede JSON parsing/claims, using exactly the same
read bytes and resolved paths. Duplicate keys at every object depth are rejected
with an object-pairs hook; ambiguous first/last-wins documents cannot be admitted. The only pre-lock reads are prepared ≤512 KiB,
receipt ≤64 KiB and ACK ≤4096 bytes. Unknown/missing receipt keys fail. Window ID is
nonblank and ≤128 characters; run ID must equal the output basename and the exact
canonical output string must match. Jobs are integer 1–64; finite positive timeout
is at most 3600 seconds; warm flag, full timeout, prepared digest and jobs bind exactly.
UTC must be explicit (`Z` or zero offset); future, expired, over-four-hour and too-short
windows fail. Before consumption the remaining window must fit the complete serial
schedule plan below: pre-run, per-sample and end verification, compiler probe, full
timeouts, target traversals, artifact probes, cleanup and journals, with the same
reserve at every launch as the runtime guard.
Admission is repeated under the lock using current UTC. UTC becomes a monotonic
deadline once; full timeouts are never shortened.
ACK bytes are hash-checked against the receipt before admission; reports contain only
its digest/resolved identity, never plaintext. Receipt and ACK identities are rechecked
before each child and at the end.

Cheap admission is followed by common LOCK_NB at
`/home/ajs/.cache/pb-rs-performance.lock` before heavy reads, output or copying.
Busy lock does no heavy work and creates no run. Under that lock, admitted receipts
are consumed **before any child**, by O_EXCL creation of
two markers, each with file and directory fsync:
`prepared-root/runs/.window-run-<semantic-sha256>.consumed.json` followed by
`.receipt-<bound-receipt-sha256>.consumed.json`. The semantic digest hashes compact
ASCII-escaped JSON `[window_id,run_id]`, preserving the exact identifier strings.
This prevents a whitespace/key-order receipt variant reusing a consumed semantic
identity after a crash before output creation. Either partial marker consumes the
attempt permanently; failures do not delete markers. The byte marker remains tied
to the caller-bound receipt digest. Neither marker is keyed by chosen output alone. The run receives an exclusive
copy of the exact already hash-checked receipt bytes. Existing outputs, traversal,
symlinks and overlapping targets fail. CLI refusals have bounded explanatory text.
No launched sample means `group_empty_verified: null`, `cleanup_status: not_observed`.

The command is fixed normal release: bound Cargo `build --locked --offline --release
--bin pb --jobs N`, no added features or synthetic Cloud. Both labels use identical
explicit jobs. Environment is an allowlist of private HOME/Cargo cache/TMP, compiler
roles, target, locale/timezone and public version/neutral commit; inherited credentials,
flags or wrappers are omitted. Source build identity includes source/lock records,
base commit/tree, manifest/tool roles, selected paths, argv and environment. Binary
hashes can differ because paths/prefixes differ; R6 did not capture its full historical
environment, so prospective neutral builds do not claim historical binary reproduction.

## Cleanup, HOLD and evidence

The Linux supervisor requires a single main thread, default SIGCHLD, supported
CLOSEFROM, and the runner's installed stop handlers. Inherited blocked INT/TERM/HUP
is refused. These checks precede receipt consumption and heavy verification; launch
checks validate the handler identity again. The child receives explicit defaults
and unblocked INT/TERM/HUP, SIGPIPE and SIGXFSZ when available. fd 0 opens `/dev/null`;
stdout/stderr are explicit; CLOSEFROM closes foreign fds ≥3, including the common
lock. Unsupported platforms fail closed. CPython/glibc behavior is not proven by mocks.

Stop handlers only record bounded flags, never deliberately raise asynchronously.
`stop` evidence includes the first **observed** signal, the bounded set of observed
stop signals and `control_failed`. Masked standard signals coalesce; this is not a
count or a claim of physical arrival order. Outside supervision handlers record
requests; hash/copy work checks flags at chunk boundaries and explicit preparation
steps. Checkpoints finish bounded atomic evidence and check stops **outside** the
write retry loop. A successful retry cannot turn an operator stop into another build.

INT/TERM/HUP stay masked continuously from before spawn through wait, cleanup,
HOLD and final process evidence. Pending signals are polled and drained with bounded,
nonblocking `sigpending`/`sigtimedwait(...,0)` calls only under a confirmed mask.
Explicit `StopRequested` control flow replaces handler exceptions. Stop points run
before each launch, during wait, after checkpoints and preparation steps. Blocking
captures the original mask before changing it; if SIG_BLOCK raises after applying,
restoration is still attempted. An unconfirmed failed block is not used for signal
waiting; control failure refuses further launches. Native confirmation of CPython
pending-handler and glibc mask behavior remains required. Original handlers/mask
are restored only after child absence; backend/restore failures fail the result,
without promising impossible recovery from catastrophic runtime failure.

The internal mockable API is `execute(cmd,env,cwd,sample,meter,timeout,deadline,
*,outcome=caller_record)`. Run always passes a preinitialized mutable record; PID
assignment is the first operation after successful spawn, and PID/PGID are durably
journaled before any proc read. Unexpected execution exceptions do not lose the
caller-visible PID. The run boundary reconciles the bounded owned `process.json`
using the same read bytes and resolved identity, preserves known in-memory absence
proof, and holds unresolved ownership before any artifact/end verification or lock
release. Journal-only launched evidence is not reported as `not_observed`.
If spawn began but no trustworthy PID survives, ownership cannot be proved absent;
the runner holds for intervention rather than inventing a numeric group to signal.

R5 distinguishes `spawn_outcome` states `not-started`, `unknown`, `child-pid-returned`
and `failed-no-child`. Immediately before the call it marks `spawn_started: true` and
outcome unknown. An `OSError` caught **only around `os.posix_spawn` itself** means
failed-no-child for this pinned Linux/glibc boundary. It clears that pending marker,
records `cleanup_status: failed-no-child` and journals the error without HOLD, group
signals or child waits. End verification may safely run; missing build/resource evidence
still fails the sample. An unrelated later `OSError` from proc/journal/chdir cannot
clear the returned PID or pending marker. `primary_error` and `spawn_error` are bounded,
write-once fields (type, stage, errno, text), independent of rotating cleanup diagnostics.

For a genuinely unknown non-`OSError` outcome, including possible allocation failure
after successful native spawn but before Python publishes a PID, HOLD remains required.
Without a trustworthy PID its status is `spawn-outcome-unknown-held`: each round journals
and pauses, but performs no proc scan, kill, wait or reap with a null/invented PID.
There is no automatic release or new control interface. The first primary error survives
more than 128 later diagnostics. Reports derive `held_ever` and cleanup state from all
sample spawn states, including no-PID uncertainty and HOLD inside `execute`; an unknown
spawn can never be described as `not_observed` or verified.

Source rationale was supplied by root without network use in this lane:
[CPython v3.14.7 `posixmodule.c`, lines 7039–7063](https://raw.githubusercontent.com/python/cpython/v3.14.7/Modules/posixmodule.c)
audits before the C call, converts a nonzero spawn return to `OSError`, and constructs
the Python PID only after success. [glibc Linux `spawni.c`, master lines 404–445](https://raw.githubusercontent.com/bminor/glibc/master/sysdeps/unix/sysv/linux/spawni.c)
waits for the auxiliary child on its reported error before returning, assigning PID
only on success. The [historical committed glibc fix/test](https://sourceware.org/pipermail/glibc-cvs/2024q2/085497.html)
includes failed-exec ECHILD coverage. These are source rationale, **not proof of the loaded
runtime**. Root reports installed headers glibc 2.44 / Python 3.14.7; headers do not
identify loaded runtime behavior. Exact glibc 2.44 tag retrieval failed in root's lane;
master plus the historical commit must not be represented as that exact loaded version.
No source fetch or native validation ran here. Deferred targets 1/10 remain required.

WNOWAIT pins the unreaped kernel-owned leader. Each group signal freshly rechecks
kernel child ownership and available starttime/group/session. For a known initial
identity, a proc I/O failure permits a fresh kernel-owned fallback recheck at that
site; an identity mismatch still prohibits the signal. Initial procfs
I/O failure permits only the freshly kernel-owned setsid-child fallback kill, never reap.
A successful first read contradicting the child's group/session also prohibits a kill;
parse or bookkeeping failures remain unknown rather than being treated as I/O fallback.
Proc stat parses bytes, including non-UTF-8 comm; ENOENT/ESRCH vanish, EACCES/incomplete
reads prevent release. Scans retain group/session/starttime/state/parent topology;
observed descendants that call setsid and previously observed reparented identities
remain tracked. Escapes are never individually signalled on uncertain ownership.

Cleanup has its own `now + 5 s` clock and budget-independent proc reads, including
a pre-kill topology observation whose failure does not skip the owned group kill.
It sends the initial owned kill even after measurement expiry, recording overrun
as failure. Later kills require observed live members; zombie-only members awaiting
init/subreaper reap are held without a 20 ms re-kill storm. Two complete scans must show no escaped
members and only the exited pinned zombie, then kernel/proc ownership is rechecked
and the leader reaped last. No subsequent group scan/signal uses that historical PID.
Repeated stop signals are explicitly recorded/consumed; bounded process journals
preserve stop evidence before the supervised lifetime mask is restored. Timeout uses one remaining-time calculation
and never requests negative sleep.

**Unknown cleanup enters HOLD:** `unknown-held` is durably recorded in `process.json`,
the caller keeps the common flock and unreaped leader, and expensive artifact/target
and end verification are skipped. Low-cost bounded cleanup observations recur with
exactly one `sleep(1)` per held round; an unknown held round returns after its bounded
observation instead of another five-second scan loop. Every bookkeeping/pause/cleanup
stage catches BaseException, including pending-signal queries, diagnostics and journal
failures. Diagnostics have a non-escaping fallback and mark bookkeeping failure.
Errors/observations stay bounded (128 errors × 2000 characters, 32 observations). Ordinary repeated stop signals
do not unwind it. Elapsed time and release-file presence have no authority. Return
and lock release require fresh complete absence proof and safe reap; otherwise the
CLI remains held and root intervention is necessary. A held run is never passed,
even if later cleanup resolves. Tests inject observations/disappearance rather than
actually wait indefinitely. No shared quarantine protocol or participant changes exist.

Both process and report evidence carry
`containment_scope: "procfs pgid/session/descendant observation"`.
Procfs scans are observations, not atomic kernel containment. An unobserved descendant
that escapes and reparents before attribution may evade this topology; this limitation
must be assessed in fresh review/native protocol validation. Observed escapes are
retained and force HOLD. SIGKILL, runner crash or host loss cannot be prevented and
can release flock/leader pin; no successful report or automatic recovery is promised.
SIGQUIT, SIGUSR1, SIGUSR2, SIGALRM, SIGXCPU and other default-terminating signals are
outside this protected INT/TERM/HUP contract, alongside SIGKILL, crash and host loss.
**A numeric PGID in an old report is never authority to signal after runner exit.**
Root must establish fresh ownership independently; killing a stale number risks reuse.

Meter raw identity, parsed observations, metrics and raw Cargo termination are
checkpointed before artifact/target/full verification. Missing/null/malformed/nonobject
records fail without invented zero metrics. Resource source must be native child wait4.
Meter return code is separate from Cargo termination: a zero meter may report Cargo
101. Artifact inspection requires Cargo 0, complete metrics, meter 0, verified cleanup
and no earlier errors. Warm no-op executable identity must equal its cold identity.
Timeout normally kills the meter without a record; genuinely preexisting complete
records survive as observations without passing the timed-out sample. Target, native
build-step and executable capture each preserve earlier evidence on their own failure.

Readelf validation is separate from Cargo cost: ELF64/little endian/x86_64, DYN or EXEC,
INTERP with `/lib64/ld-linux-x86-64.so.2`, bounded output and empty stderr. DYN permits
normal PIE, EXEC non-PIE; wrong architecture/REL/ELF32 fail. No pb execution occurs;
functional/version controls require a separately authorized profile. Binding covers
actual gcc driver/ar/declared ld/readelf and the full Rust toolchain. cc1/as/collect2,
headers/crt/libraries/C++ and other system components are **not fully frozen**. Declared
ld may not be rustc's linker. Native `release/build/*/output` identities and bounded
64 KiB-per-file/1 MiB-total prefixes retain observed build-script evidence.

## Deadlines, persistence and hygiene

Work checks time between 256 KiB hash/copy/compressed-read chunks and bounded traversal
entries (100,000 maximum). Launch follows all pre-sample verification, target hashing
and checkpoints, with a second check at actual spawn after log-open/chdir. The initial
target reserve is a planning estimate: 2 GiB at 16 MiB/s plus 10,000 files at 500/s =
148 seconds, conservatively above R2's historical 609,658,232 bytes / 2,267 files.
The verification planning floor is 30 seconds, raised by bound preparation timing
and actual observed per-run costs; planning fields stay separate from measured costs.
Reserve uses the larger of that floor and observed target cost, twice the larger of known/planned and observed full
verification, ten seconds readelf, five cleanup and journal allowance. It is not a
throughput guarantee. No silent timeout shortening or post-expiry full rehash occurs.
R5 uses one serial admission simulation, `schedule_plan`, and the shared
`launch_reserve` calculation used by `Budget.launch`. Start elapsed time at `V + PROBE_S`
for pre-run verification and compiler probe. For each row, add per-sample `V` and,
for warm rows, a target-before traversal estimate. At that planned launch, require
full timeout + launch reserve + the budget's final-journal offset. Then advance by
full timeout, cleanup, target-after traversal, readelf and row journal allowances.
Finally include end `V` and final journal. Required time is the maximum of all launch
requirements and the planned end. `V` is at least 30 seconds or the preparation-bound
verification time; target estimate is 148 seconds. With V=30 and timeout=10, cold-only
planning requires **more than 502 seconds** (warm no-op schedule more than 1208 seconds).
Those conservative target estimates exceed the review's zero-target-cost ~328-second
example; they are named work stages, not unrelated margins. Windows 238–328 seconds
are rejected before consumption. A guarded clock test completes every admitted row
at all planned stage costs without shortened timeouts. Under-lock readmission and
dynamic observed-cost/spawn-time checks remain; unexpected slow work can still fail
closed after admission. Plan fields are retained separately from observations.

A single OS read/stat/write/fsync or decompressor call can stall past a check; subsequent
checks stop work and record incomplete validation. Owned cleanup still runs, and HOLD
can intentionally exceed the measurement window to preserve ownership.

Journals use unique exclusive temporary siblings, bounded 32 MiB serialization, file
fsync, atomic replace and directory fsync, masking stop signals. Failed temps are removed;
small evidence writes can finish after work expiry. Process journals and preparation
failure markers use this path too. Every error invalidates pass. If final report writes
persistently fail, metadata-only rename removes `report.json` to `report.unverified.json`,
so a previously replaced pass is not consumable. Consumers MUST require successful
runner/API completion, canonical `report.json` with passed/complete/checkpoint and end
identity checks true, and no unverified report. Never consume `report.unverified.json`.
Pathological failure of writes **and** invalidation rename/fsync cannot be repaired by
software promises; CLI/API failure remains authoritative, and report-only acceptance
is forbidden. Only newly created owned empty scratch directories are removed. Nonempty
scratch remains identified; preexisting scratch and all 749 historical dirs are untouched.

## R2 dispositions and remaining gates

All rows below are implementation + guarded mock evidence, pending independent review.

| Finding | Implementation / causal coverage |
|---|---|
| A | Whole cleanup masked, deferred signal evidence before return; repeated-stop and journal-before-unmask tests. |
| B | Fresh kernel ownership before every kill, independent cleanup budget/clock, kill after expiry and both PID journals durable. |
| C | Parent-selected HOLD retains lock/pin, observes escapes, skips heavy validation, no elapsed/file release. |
| D | Bytes parser, disappeared ENOENT/ESRCH versus incomplete EACCES; parent/session/known-escape tests. |
| E | Single remaining-time calculation, nonnegative sleep and correct timeout classification. |
| F | Both initial and final process journals bypass expired work budget; disk assertions. |
| G | Replace-then-persistent-failure test, unverified rename and explicit consumer success rule. |
| H | Extraction in ready preparation only; compact extracted tree binding; warm toolchain cache disclosed. |
| I / N14 | Exact receipt schema, root-owned ACK file, byte-bound receipt copy, single consumption, full negative matrix/replay/ACK drift tests. |
| J | Explicit stdin and CLOSEFROM, fail-closed unsupported path; native glibc validation deferred. |
| K | Tests isolate TMP in own cache; only newly owned empty scratch removal, no historical sweep. |
| L | Durable preparation-failed marker after expiration. |
| M | No launch is null/not_observed; bounded CLI refusal tested. |
| N | Size/file-derived initial target reserve and spawn-time guard tested. |

R1 N1/N2 anchors/admission and N4–N13 protections remain covered, including real-shaped
meter fixtures, resource source, readelf stderr/artifact gating and durable disk reports.
Independent R2 probes/mutations are attributed in tests and the in-process mutation driver.
The 17 survivors are retested: M03/M06/M09–12/M15/M17–19/M24/M25/M31/M32/M34/M38 and M37.
M06/M09/M32 sites are adapted to the contained supervisor rewrite; M37's removed coupled
clock is replaced by equivalent suppression of expired cleanup. Per-case JSON records
exact replacements and causal tests. Additional mutations exercise A/B/C/G, exact schema,
output binding, replay, extraction placement, fd closure and initial reserve. Historical
and first failed logs are retained. Settled hashes/log identities are in the new delivery
receipt; they are implementation evidence, not review acceptance.

## R3 delta dispositions and mock evidence

All 70 prior test cases remain. Legacy units that previously mocked HOLD to return
unknown now exercise a single bounded cleanup attempt; production execution must
remain held. The former raising-signal injections are backend/write-fault tests;
new flag/pending-stop tests establish the selected stop contract. No old test log,
review cache, preparation or source snapshot was overwritten.

| R3 finding | Implementation / causal witness |
|---|---|
| 1 | Recording handlers; explicit stop outside checkpoint retry. Pre-launch stop launches zero; post-capture stop launches no later sample; preparation stop records failure and first observed stop. |
| 2 / 9 | Caller-owned mutable outcome, PID before proc, bounded journal reconciliation, non-escaping cleanup/HOLD boundaries; unexpected pre/post-proof exits retain launched identity and lock until proof. |
| 3 | Query before mask change, best-effort restoration after applied-block fault, inherited STOP refusal and installed-handler validation. Unconfirmed masks never enter sigtimedwait. |
| 4 | Fresh kernel fallback on known-identity proc I/O failure; identity mismatch rejects kill; pre-kill observation failure cannot skip the initial kill. |
| 6 | Duplicate prepared/receipt JSON keys rejected after same-byte caller hash check. |
| 7 | Full initial target/known-verify reserve before consumption, repeated admission under lock. |
| 8 | Semantic window/run marker plus byte marker, both O_EXCL/fsynced; crash/partial-write and whitespace replay fail closed. |
| 10 / 11 | Explicit observational scope, zombie pacing and other terminating-signal limits; no cgroup/subreaper feature added. |

The targeted in-process mutation driver attributes the independent R3 substitutions,
records exact sites and witnesses, and tests all 21 survivors plus C8's direct sleep
assertion. A1's redundant nested mask is superseded by the whole-lifetime mask; its
replacement removes that scope and checks continuity at cleanup/fallback entry.
C4's old deletion would retry rather than escape; its replacement rethrows at the
cleanup-error boundary, testing that an unresolved execute cannot unmask/return.
C5 moves to the caller-outcome finally boundary. M12i adapts to explicit unblocking
of all child-default signals. Renamed diagnostic/reserve sites retain their original
causal intent. No watchdog or guard-only failure counts as a regression witness.
Three additional mutations target checkpoint-stop flow, semantic replay and duplicate
keys. Historical source/test/doc hashes, mutation sites and logs are in
`r3-corrections/delivery-r3.json`; this is implementation evidence, not acceptance.

The settled run is `r3-corrections/mutations-run5/`: **94/94 tests pass**, including
all 70 prior cases, and **25/25 targeted mutations fail causally**. All 117 review
guard entry points are covered by 126 installed guards; no forbidden call occurred.
The in-process driver uses neither a watchdog nor spawned mutant processes. Attempts
1–4 and the earlier failed test attempt remain preserved. The first-identity mismatch
assertion shares the R3-4 fallback witness; the final baseline tests that exact source.

## R5 dispositions and limited review handoff

All rows are implementation plus guarded mock evidence, awaiting independent review.

| Finding | Correction / causal evidence |
|---|---|
| R5-1 | Call-only OSError → failed-no-child with errno/text, durable nonrotating primary/spawn evidence, no HOLD/kill/reap. EAGAIN/ENOENT/EINVAL/EBADF fixtures release the mock lock and allow safe end verification. Later proc OSError retains PID and performs owned cleanup. Non-OSError uncertainty remains explicitly held, preserves the primary after 140 rotating diagnostics and never calls waitid(None). |
| R5-2 | Shared launch-reserve formula plus serial full-schedule simulation. Review band refused before either consumption marker; admitted cold/warm paths complete at planned verify/probe/target/timeout/cleanup costs. Runtime deadlines remain dynamic. |
| R5-3 | Every checkpoint/final report derives held-ever and cleanup status from full sample spawn states. Internal execute HOLD is retained; no-PID unknown is neither unobserved nor verified. |
| R5-4 | Direct causal witnesses for C5b/C5c/SPW1, literal SIGPIPE/SIGXFSZ defaults, O2/O3 conflict/proof preservation, immediate prespawn stop S12 and BaseException from HOLD pause A2c. |

HOLD mock observers record bounded invariant failures and inject only a clearly labelled
**simulated** end so the assertion can fail outside production's BaseException containment.
That is test scaffolding, never a production release path or proof of native absence.
The old 94 test names remain. Their general mock window is now 2000 seconds to fit the
warm schedule plan; time-exhaustion fixtures move their injected stalls accordingly.
The failed first R5 log and all beforeimages remain intact. Targeted independent mutant
sites are attributed to review R5 `mutate.py`; structurally redundant inner-mask/early
return mutants remain documented as defense in depth rather than prompting a redesign.
Settled hashes, source diffs, per-case substitutions and exact logs are recorded in
`r5-corrections/delivery-r5.json`. No full 93-mutant sweep or native acceptance is claimed.

The settled guarded run is `r5-corrections/mutations-run2/`: **106/106 baseline tests
pass**, all 94 prior names remain, and **18/18 targeted mutations are killed causally**.
Nine are exact independent R5 substitutions (the eight requested gaps plus M12j's
formerly hanging spawn-attribute path); nine target the new classification, primary
error, no-PID cleanup, serial planning and summary corrections. No guard-only failure
or watchdog counts as a kill. All 119 reviewer guard aliases are covered by 126 installed
guards; zero forbidden calls occurred. Before/after small source/test digests bind the
loaded test inputs. Both mutation runs and the initial failed test log remain preserved.

## Deferred native protocol: ten targets, no execution or authorization

After a fresh narrow independent delta review, root may separately authorize bounded
controls using compile-free `python3 -c`/`/bin/sh` helpers, the pinned prebuilt meter,
and existing ELF inputs (for example `/usr/bin/true` and `/usr/lib/Scrt1.o`);
a wrong-architecture header can be crafted as bytes. No helper compilation is needed.
Every target needs a root-owned wall-clock cap, task-cache evidence and an external
cleanup script. HOLD observers, especially targets 4/5, must record bounded invariant
witnesses and stop their control harness on failure, rather than swallowing assertions
or hanging without a cap; this grants no release authority to the runner. Pass-through
wrappers log killpg/waitid/waitpid without strace.
Targets 1–7 and 9–10 use a **task-owned lock**, never the common performance lock.
Only target 8 briefly exercises the common lock with LOCK_NB. These plans grant no
native execution, ready preparation/hash work, receipt, quiet window or build.

1. Spawn identity (`pid == pgid == sid`); child SigBlk excludes default signals and
   SigIgn is empty, including SIGPIPE/SIGXFSZ. Confirm `/dev/null` stdin separately.
   Add a nonexistent-helper spawn through this boundary: expect OSError/failed-no-child,
   no HOLD, and one root-owned wrapper observation `waitid(P_ALL, WEXITED|WNOHANG|WNOWAIT)`
   returning ECHILD/None to demonstrate no child remains. This is a proposed later
   control, not a probe performed by the runner on ordinary failed-no-child paths.
2. WNOWAIT pins the exited Z leader with stable starttime; initial zombie-only kill
   is safe, and only the supervisor reaps its leader.
3. Same-group outliving grandchild is found/killed; two clean scans, reap last,
   wrapper evidence of no post-reap signal.
4. Observed setsid/session escape forces HOLD and retains lock/pin. Root records
   fresh `(pid,starttime)` for the escaped helper, owns its bounded external cleanup,
   and never signals a stale numeric PID/PGID. Identify the actual adopting subreaper
   and confirm zombie reap latency before expecting release. If the target cap is
   exceeded, root may kill the trivial runner only under that later authorization,
   accepting documented pin loss and marking the control incomplete; a release file
   or elapsed time never grants runner release.
5. TERM-ignoring helper timeout, repeated protected stops during cleanup, and a single
   stop at checkpoint: cleanup succeeds and no later helper launches. Burst/pre-block
   races are closed structurally by the flag design, not claimed natively reproducible.
6. Prebuilt pinned meter wraps helper exit 0/101/SIGKILL, preserving exact termination
   and wait4 resource semantics; absent timeout records remain null.
7. Actual readelf on an existing PIE DYN with empty stderr is accepted; existing REL
   and a byte-crafted wrong-architecture input are rejected. No product CLI runs.
8. Brief common LOCK_NB contention against a second root-owned holder: no heavy work,
   no output/consumption, no change to other participants or shared lock protocol.
9. Bounded real proc scan cost/completeness, disappearance and non-UTF-8 comm; observed
   escape tracking, adopting subreaper identity and zombie reap latency. Zombie members
   wait without re-kill storms. Unobserved escape/reparenting remains disclosed.
10. Native glibc fd actions: `/dev/null` stdin, foreign fds closed, lock not inherited;
    restoration of original runner mask/handlers only after proven child absence.
    Exercise CLOSEFROM failure explicitly: definite spawn OSError must fail closed
    without HOLD and without attempting ownership operations on a nonexistent PID.

Fresh ready preparation/hash/copy work, a root-issued bounded receipt/ACK, the quiet
window and build remain separately gated after those controls. Source and mocks cannot
prove CPython/glibc behavior. No measurement or native/whole-phase acceptance is claimed.
