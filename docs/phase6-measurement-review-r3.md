# Phase6 measurement review r3 — independent pre-campaign admission

**Disposition: unresolved; do not admit a native campaign yet.** The direct R2 N1–N5 fixes are independently substantiated, but two focused filesystem races still allow a successful report with inconsistent provenance. R3-A affects the actual fixture/meter selected for collection; R3-B affects parent-manifest identity. Both apply before finite same-Go/control admission. Their fixes need only pure/mock verification, not new native evidence.

This is a fresh independent measurement review at HEAD `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0` plus dirty Phase6. It is **not** the final whole-phase Opus review or a substitute for the currently rate-limited reviewer. The active production dictionary correction is separately owned and was neither edited nor accepted here. No campaign, client/fixture execution, build, profiler, soak, pressure rerun, production/shared edit, commit, agent or external message occurred. No real quiet token or performance lock was obtained. The only worktree write is this document; all probe files and synthetic artifacts are under `/home/ajs/.cache/pb-phase6/measurement-review-r3`.

## Identities and actual verification

The governing source was `/home/ajs/.cache/pb-roadmap/phase6-final.md`, especially “Performance and sustained-operation evidence” and the common performance protocol. R1 and R2, the current measurement plan/budgets, collector/tests, frozen r5 fixture documentation/accounting source, and the shared `run.py`/`meter.c` boundary were read. Author resolution `cache/tmp/collector-review-r2-resolution.json` was treated as claims. R2 document SHA256 independently remains `ffc0652b68edbd99b2a7301a9a4845223b0472b51c21b90245f9995036f17819`.

These settled input hashes matched on the initial check and again after probes/saved inspection:

| Input | Before = after SHA256 |
| --- | --- |
| `harness/phase6_workloads.py` | `348cbbf0b054e6f0c1827b6f171974c2dc394285fd1188c2716e85a9b2a5dfae` |
| `harness/test_phase6_workloads.py` | `dcb16ae9d0af98fc35947b153a52ac7ee33f980902f07b5bacab594eb63b1492` |
| `docs/phase6-measurement-plan.md` | `4a1a25f139b218d0e91dffdfe4a3d7a190850fc4d92a23ad62b76a3b383043a0` |
| `docs/evidence/phase6/performance-budgets.json` | `76a57ecacf2fba39b1f278e385f1cf4ee410c49f29d2d0e7e7dd2e2713b5ae93` |

Shared boundary hashes remain `run.py`: `b280d1295a4b351e6f2d838c56acaac58b2247bb77fc9881587bb7bbe849a076`; `meter.c`: `5d7ce9dc2fa65b1ec7018fb863d36fd78d8b8535762acadaefc3f2f4e0f070e9`. Full identities are in cache `source-hashes.json`; script/log/artifact hashes are in `review-artifacts.json`.

Executed commands used `TMPDIR=GOTMPDIR=CLAUDE_CODE_TMPDIR=/home/ajs/.cache/pb-phase6/measurement-review-r3` and `PYTHONDONTWRITEBYTECODE=1`. Within Python, temporary-root variables were set before importing tests, and `p6.TMP` was redirected to that cache. The commands were:

```sh
python3 /home/ajs/.cache/pb-phase6/measurement-review-r3/run_pure_tests.py
python3 /home/ajs/.cache/pb-phase6/measurement-review-r3/probes.py
python3 /home/ajs/.cache/pb-phase6/measurement-review-r3/race_controls.py
python3 /home/ajs/.cache/pb-phase6/measurement-review-r3/cleanup_probe.py
python3 /home/ajs/.cache/pb-phase6/measurement-review-r3/saved_inspection.py
```

Corresponding redirected logs are `pure-tests.log`, `probes.log`, `race-controls.log`, `cleanup-probe.log`, `saved-inspection.log`. The cache scripts create fresh, named synthetic directories; these are execution records, not instructions to overwrite their evidence on a second invocation.

**80 selected pure/mock tests passed.** Classes: `ObserverAudit`, `Collector`, `Workloads`, `Campaign`, `ReviewAdmission`. The actual lock primitive test was excluded; `ObserverNative` and `NativeMeter` were not loaded. `flock`, `subprocess.Popen`, `run.spawn_measured`, `os.posix_spawn`, `os.posix_spawnp`, `os.fork`, and `os.system` were guarded against execution in the test/probe runners; host metadata was mocked. Campaign lock contexts and captures were mocks. `/bin/true` was read as synthetic executable content, never executed. In addition, `probes.json` contains 29 independent cases, `race-controls.json` three in-memory correction controls, and `cleanup-probe.json` a separate cleanup-only failure. Synthetic nonnull ratios below merely demonstrate admission; they are not observations of performance.

## R3-A — High: verified fixture/meter paths are not the execution paths

**Cause:** `campaign` resolves and hashes selected fixture/meter with `run.binary_info` at collector lines1182/1184 and tracks those resolved paths. However, its sample wrapper at line1222 passes the original `args.fixture` and `args.meter`. `Fixture` passes that original fixture pathname directly to `Popen`; `collect` passes the original meter pathname to `run.spawn_measured`. Go/candidate already use `labels[...]['path']`, so their analogous symbolic-link substitution is avoided.

**Independent repro:** `R3-repoint-fixture` and `R3-repoint-meter` each start with a cache-only symlink to a correctly declared artifact. The mocked lock entry repoints that link to different cache-only executable content after binding. Mocked `run_sample` records the identities of its supplied fixture/meter paths, then returns a stable synthetic sample. All eight invocations see the replacement for the affected role. The unchanged original resolved artifacts remain the only end-check targets. Both campaigns return0, `passed=true`, `complete=true`, `end_identity_check={passed:true,changed:[]}`, with synthetic wall/CPU ratios nonnull. Exact paths, replacement hashes, selected report identities and calls are retained in `probes.json` and the case reports.

This does not claim that a replacement executable was run, nor that arbitrary replacement content can pass real protocol gates. It establishes that the execution argument can resolve to content outside the verified role chain without provenance rejection. A different conforming fixture or meter could then provide different work or measurements while the report identifies the original. It also affects same-Go timing A/A and hash-framing controls, not only candidate comparisons.

**Focused fix:** pass the verified `fixture['path']` and `meter['path']` into `run_sample`, as already done for measured clients; continue rehashing these verified identities. This closes the demonstrated symlink-repoint race without adding a new campaign prerequisite or a general transactional-execution design.

**Causal control:** `race_controls.py` changes only that call in an in-memory compiled copy of `campaign`. With the identical repoint hook, all invocation identities now match the bound original paths. The real collector file is untouched. Add equivalent focused regression coverage before admission.

## R3-B — Medium: parent manifest is parsed before its tracked identity is established

**Cause:** collector line1172 reads the parent manifest. At line1196, after manifest/role verification, workload selection and host metadata, `parent_manifest=file_info(args.manifest)` independently hashes the then-current file. A change in this interval becomes the end-check baseline rather than a detected change to the parsed provenance.

**Independent repro:** `R3-parent-read-hash-race` patches the parent read to return the original parsed object and then atomically replace only that synthetic cache manifest with an object whose `sources.oracle` differs. Nothing else changes. The campaign returns0 with passed/complete/end-check true and nonnull synthetic wall/CPU ratios. The report embeds `manifest.sources.oracle="pinned"`, while its `parent_manifest.sha256` identifies the changed file containing `"changed-after-read"`. Both initial/actual hashes and objects are recorded. No changed-back file, native execution or production mutation is involved.

The separately tested runner change between verification and tracking correctly fails; this parent case shows that reuse of initially verified identities has not yet covered the root provenance file itself. The report's parent hash and embedded contents disagree even though the end check says stable.

**Focused fix:** establish the parent path/hash before parsing, ensure the bytes parsed belong to that identity, and retain that same identity for the report/end check. A bounded read whose bytes supply both JSON and SHA256, or before/after hash verification plus retention of the initial identity, suffices for this demonstrated race. Do not silently rebind to a later read.

**Causal control:** the in-memory control records an initial identity, checks it again after parsing, and retains it. The same mutation is rejected before any mocked lock or sample call: status1, both call counts0. No source fix was applied by this reviewer.

## Independent disposition of R2

- **N1 — Direct mismatch/build-chain fixes closed; R3-A qualifies execution binding.** Independent wrong Go/candidate/meter cases reject before lock/sample. Missing `build_kind`, diagnostics features, cloud flag, custom-profile argv and mismatched `builds.pb` reject. Exact normal-release argv/features/cloud policy is enforced. Identical-content copies pass and retain selected/declared identities. Timing A/A passes with an absent candidate and no candidate manifest; both labels bind Go, meter remains bound. The old draft rejects on current runner SHA; its isolated old role chain rejects missing normal-release kind. No real R5 candidate manifest was manufactured or old manifest edited.
- **N2 — Listed omissions closed; R3-B remains for parent parsing.** Individually replacing an extra runner, fixture compiler, candidate build manifest, frozen source snapshot, or declared Go copy invalidates the partition and suppresses all ratios. A runner mutated after initial verification but before tracked-set assembly also invalidates, confirming reuse of the verified hash. Resolved-path deduplication and conflicting-hash rejection remain intact. The parent race is a narrower remaining inconsistency.
- **N3 — Closed.** Actual `run_sample` with fake fixture/capture retains every completed observation under `streams:null`, post-capture host failure, and simultaneous validator/cleanup failure. `capture.json`, invalid `sample.json`, and failed journal all survive; no observed fields are lost and every ratio is quarantined. A separate replay of saved one-row inputs through mocked capture reaches successful validation, then throws only in cleanup: wrapper recovery retains the hash/resources/counts/first-record fields, adds only `RuntimeError: cleanup-only mock`, and sets valid/contract_validated false, complete/passed false. The raw capture has no validated-contract claim. No native replay occurred.
- **N4 — Closed before replicated aggregation.** Independently produced same-plaintext sessions aggregate. Mixed plaintext/TLS, missing TLS, integer0 and string`"false"` reject. Explicit boolean TLS is in report/manifest/aggregate identity. This does not establish actual TLS integration.
- **N5 — Closed as prose.** Same frozen scenario seed with fresh session indices is current replication guidance; new-seed investigations form separate series. The old r3 mutable-debug block is explicitly historical/do-not-execute; direct functional Go example selects immutable r5. No historical block was executed.

## Saved evidence and preserved gates

Read-only revalidation of **20 existing samples** (four large pressure, two small event-drop, fourteen collector small cases) finds zero current schedule/churn/output/request-gate issues. Raw sample, final summary, scenario and meter files were rehashed. Frozen r5 executable, manifest, compiler and all seven source/document snapshots match their recorded identities; executable/snapshots remain read-only. Fixture SHA256 is `c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314`; manifest is `40d53f0b019805fbc710d7bc41ab5e356b96a4dfc82e5ed6c3dee469f8075286`. Two existing diagnostics sidecars were read, not generated.

The archived diagnostic provenance still records historical collector `e8f2edc249ba9e5f5a94662c62eb26169545c916db0de6d55f65802364ccfc07`; its current-path rehash differs, as already disclosed by R2. The other nine identity descriptors checked in that diagnostic archive match. Both archived evidence documents retain their R2 hashes. Two initial saved-inspection script attempts assumed different historical JSON keys and raised KeyError; corrected read-only inspection succeeded. Failed setup logs remain in cache. These were reviewer-script failures, not collector failures.

The changes do not weaken the R1 gates: exact deterministic scheduled/emitted/omission/stop/lateness/Send counts; full-session cyclic dictionary initial/delta/replacement/reset/max-size and bucket counters; full output hash/bytes/rows/batches in both observers; complete auth/ticket/request/order accounting; exact budget content/hash; failure/incomplete quarantine; per-metric schedule/sink/pressure exclusions; explicit first complete LF record; CPU interval/quantization uncertainty; whole-duration timeout/cleanup reservation; seeded sequential order and duration AB/BA; separate same-Go timing A/A; low-N/duration descriptive statistics; bounded retention; hash-only token storage; and real RAII handoff counter semantics. Source inspection plus the 80 pure/mock tests support these tooling boundaries. They do not add new native execution evidence.

Carried limits remain exact: finite InvalidArgument/exit1 is deliberate remote failure, never successful live delivery. Prior Rust applies only to implemented shared commands, never tail. Native elapsed/CPU/RSS/faults/context switches/block IO belong to the actual child; observer/fixture intervals differ and encoding WALL is not CPU to subtract. Short/all-core ambiguous CPU observations can suppress every fast finite ratio; all-null comparisons are acceptable and do not prove quiescence. Proc IO is not network traffic, protobuf bytes exclude HTTP/2/TLS, decoded/output bytes differ, FD/thread samples can miss transients, and thread counts are not Tokio tasks. Owner maxima are not simultaneous/unique allocation or RSS totals.

Saved large cases retain9 batches/36 rows/32506837 bytes, the common `3ab3830e286d3c63776291df3cfd4f2381708c7ef782257758b3b0050b2ed06f` hash, and exact two dictionary resets. Diagnostics show actual peak handoff1, dictionary/batch maxima67109124/67109176 and last staging capacity0; no last dictionary-owner size, post-reset allocator/RSS release, comparative release envelope or HTTP/2 read-ahead follows. Post-warmup trend points are zero. Full-session churn and1800/3600-second workload definitions remain source/mock evidence; no actual long-duration session has been reviewed here. The trusted short banner still has the disclosed readability-versus-partial-line deadline limitation. Missing latency/event/resource coverage stays missing; finite terminal overhead and reconnect's fixed500ms remain part of their distinct definitions.

## Next admission boundary

Resolve R3-A and R3-B with focused pure/mock controls and fresh source hashes; then regenerate and verify final parent provenance after the production release candidate settles. This review does not authorize a native window. A first finite same-Go timing A/A and matching full/hash-framing control still need the coordinator's actual explicit companion pause-build/test acknowledgements, the common nonblocking flock, frozen inputs, fresh folders and partition budget. A token alone is not the handshake.

Review those finite/control artifacts before short-duration or real30-minute soak admission. Later duration partitions must reserve the whole configured timeout/cleanup/A/A window; replicated aggregates require same seed, fresh indices, identical conditions/provenance and descriptive session reporting. Long retention, server-versus-client pressure attribution, release RSS/capacity envelopes, profiles/build cost, deployed Flight auth/TLS and production delivery remain unmeasured or separately owned. Whole-phase final Opus review remains outstanding.
