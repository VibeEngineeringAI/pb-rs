# Phase6 tiny strace capture — independent functional review 01

**Verdict: PASS for this completed one-row functional capture; no capture blocker found.**
This closes the capture-evidence review only. It accepts no analysis, non-perf adequacy,
native performance comparison, quiet-window claim, or whole Phase6 result. The capture
report's `interpretable:true` flags mean collector admissibility here, not profile adequacy.

Reviewer: independently assigned Sol 6.1 review sub-agent, 2026-10-14. Owned only this
document and `/home/ajs/.cache/pb-phase6/strace-functional-review-01/`. No source edits,
agents, commits, native measurement/probe commands, analysis CLI, compiler, build,
fixture/meter/helper execution, network, process scan, real lock, signal, token operation,
or pressure/rerun was performed. Only read helpers and isolated Python JSON/text/hash
reduction were used. No repository Python module was imported.

## Scope and identities

Capture directory: `/home/ajs/.cache/pb-phase6/profile-probe-strace-one-row-01/`.
Coordination evidence: sibling `profile-probe-strace-one-row-01-coordination/`, including
`execution.json`, `cleanup.json`, `functional-summary.json`, `stdout.log`, and `stderr.log`.

Independently hashed complete small files:

- `report.json`, 103,968 bytes:
  `98161533a2fd603f7639fd0c28f7e532f4ea09891e9f46cb224db55514c6bef0`.
- Frozen parent `campaign-prepared-r6-v1/manifest.json`, 75,361 bytes:
  `a47093f4a3ea785a310f7ca1baa92444deaf7e21db5ba7db72dc897ddc3d047b`.
- `profile-adapter-v6/profile-sidecar.json`, 8,413 bytes:
  `9cadf783f554a22fb6c3bf20c88e9e110ed0b6026f85a45c7dcde898866c3f5d`.
- `harness/phase6_profiles.py`, 86,910 bytes:
  `032cec6338fb7126a97a1bd28eeb53d428442a598287ef4fe267160b7650ac73`.
- Root's `profile-parent-v6-check/acceptance.json`:
  `26810147793d73fe1ad089c018f7a010ca4dfde384ea18a839b04af9199b5100`.

Read [R5](phase6-profiling-review-r5.md) and the current
[v6 plan](phase6-profiling-plan.md). Root's bound focused-check record declares the
66-test suite, seven independent probes, zero guard trips, unchanged identities and
tiny-probe source readiness. I verified that record and its source/sidecar bindings;
I did not repeat its tests or turn its preparation-time `native_authorized:false`
into a statement about the later separately authorized capture. The plan's preparation
status remains historical; this review supplies separate strace capture observations.

The 34 small members of the report's 49 tracked input identities were independently
hashed against the frozen declarations. These include all eight bound harness files,
fixture source snapshot and manifest, budgets, symbolized build manifests, Go provenance
JSONs, candidate manifest copies, saved section dumps and supporting evidence. Parent,
sidecar and report metadata link the four executable roles, fixture and meter exactly.
Rust's symbolized manifest matches the parent's candidate source/toolchain map, declares
no extra features, and names `cargo build --locked --offline --profile profiling --bin pb`.
Go's manifests link release and optimized symbolized binaries to the same recorded tree,
oracle commit and compiler declaration. Go source-to-commit equality remains the Phase7
attestation; I did not re-verify the source tree against git.

The remaining 15 tracked identities are the four product binaries, fixture, meter,
fixture Go toolchain and eight tools. Their hashes are **record-attested only** in this
audit; none was opened or rehashed. This distinction also applies to the report's
historical before-launch checks and full native end rehashes.

## Actual sample evidence and independent oracle

The persisted directories independently establish six samples, one repetition each,
in this order: Go release control, Rust release control, Go symbolized control, Rust
symbolized control, Go symbolized strace capture, Rust symbolized strace capture.
Recomputed role counts agree with the report: one valid control per role, and one
collector-admissible capture for each symbolized role. Four controls precede both captures.

All six `sample.json` objects exactly equal the report's embedded results. Complete
stdout/stderr, meter records, raw `capture.json` checkpoints, fixture scenarios, baseline
and final summaries, periodic summaries, events, config, visibility and batch correlation
logs were read independently. The final offline reduction has **318/318 checks passing,
zero execution/lock/network guard trips**, with detailed findings in
`strace-functional-review-01/functional-audit-v3.json`.

The one-row oracle comes from the frozen basic-schema source and scenario, rather than
from `passed:true`: sequence 0, integer 9007199254740993, value 0, message
`fixture <event> café 日志`, null optional field and timestamp `2024-01-01 00:00:00`.
Each stdout has exactly one nonempty JSON record, sorted keys, escaped angle brackets,
UTF-8 text and **two trailing newline bytes**, totaling 162 bytes. The complete bytes
match the independently assembled expected rendering in all six samples:
`50f93c839dfdd227382e2ecd88ab7be87e83ce8855794437fcfb781c780d04a9`.
Thus symbolized controls and captures match both release controls byte for byte.

Each stderr is the complete 157-byte JSON diagnostic with `COMMAND_FAILED`,
`retryable:false`, and message
`rpc error: code = InvalidArgument desc = fixture stream complete`, hash
`7b14587cc92a095913c929f4f6510c6e6da38876a3f391aa49feb5e1e1360be0`.
Each meter records `exit_code:1`, `termination:{"exit":1}`, no timeout or signal, and
`resource_source:native_child_wait4`. Exit 1 is the expected failure termination after
one rendered row and the deliberate remote status, not successful live completion.
For captures this is the metered strace process tree; there is no separately recorded
profiler-only exit status. No extra profiler diagnostic appears in the complete stderr.
The coordinator's separate capture-command exit is recorded as 0.

Raw `capture.json` preserves the completed observation with `valid:false` and
`contract_validated:false`, before workload validation. Final `sample.json` records
`valid:true`, `contract_validated:true`, one row, one batch and one JSON parse, zero
invalid records/sequence errors, no collector errors, and no dropped stdout, visibility
or resource bytes. This checkpoint distinction is intentional, corroborated by source
and actual persisted records, and is not a failure hidden by the final flags.

For each sample the baseline has no requests/output. The final summary and event log
agree on eight events with contiguous sequence numbers: discovery GET, connection begin,
DoGet, schema, batch start, batch done, deliberate terminal status and connection end.
There is exactly one authenticated `/api/v1/about` request and one authenticated DoGet,
with discovery completion before DoGet. Ticket bytes are exactly `{"stream":"logs"}`;
ticket hash is `77d2f1d744b6bea7f60fcf07c32635b5074672b9e9305073186be1c5f7cd2ee4`.
Auth/ticket checks each equal one; invalid auth, mismatches, unexpected requests and
DoGet-before-about counters each equal zero. Events show the expected basic metadata,
matching ticket and loopback plaintext connection, with no additional subscription.

The schema is 411 encoded bytes; the single batch is 559 encoded and 94 decoded bytes.
Final summaries independently agree on one emitted/rendered row, two FlightData messages,
970 encoded bytes, maximum message 559 and 980 gRPC framed bytes. These are fixture
encoding counters, not observed network-wire delivery. One visibility record and batch
correlation corroborate the row. No fixture stderr or dropped events are present.

The periodic `fixture.summary.json` has seven events, before `conn_end`; its stream
counters equal the later final snapshot. The final snapshot and complete log have eight.
That earlier persisted snapshot is not evidence of event loss. All six exhibit this
same distinction.

Each isolated config has the expected basic credentials/profile and per-sample loopback
URL. Replacing only that known URL produces the independently computed normalized hash
`a416c0ba27acb2c33795596b9db1d3691a944a1c1820ce59ebc39ad0580b4b86`, equal to every
recorded config-before/config-after hash. The original pre-run config bytes were not
separately retained, so their identity remains recorded evidence. All 14 delivered
contract fields were independently rebuilt from the small artifacts and frozen scenario;
the six rebuilt contracts are identical and agree with the control and whole-report gates.

## Recorded tools, symbolized roles and strace format

All six probe records have their planned argv, return code 0, no timeout/error, and
independently observed empty stderr. Return codes are saved execution metadata, not
new executions by this reviewer. Version text was read and hashed in full:

- `/usr/bin/readelf --version`: `GNU readelf (GNU Binutils) 2.47`, stdout 277 bytes.
- `/usr/bin/strace -V`: `strace -- version 7.0`, stdout 326 bytes; optional features
  `stack-trace=libunwind stack-demangle m32-mpers mx32-mpers`.
- Go sections: `readelf -S -W <Go-symbolized>`; observed single-line wide format,
  stdout 2,770 bytes, hash `e0a76c93340b5245f124498a144f3448aa2adafc1c01b4612b383c7e01d68af2`.
- Rust sections: `readelf -S <Rust-symbolized>`; observed wrapped format,
  stdout 6,292 bytes, hash `d2ff8bf248b30cd5e057682a52f7c23701b306ea453c4aa06238ca554a580de8`.

Both complete section outputs contain `.debug_info`, `.debug_line` and `.symtab` and
are byte-identical to the corresponding independently hashed saved section logs.
Both symbol probes record `readelf --syms --wide <symbolized binary>` and show real
wide symbol-table/FUNC lines in bounded excerpts. Their full outputs are larger than
the reviewer's 2 MB small-file limit: Go 13,576,415 bytes, Rust 2,328,621 bytes. I read
at most 65,536 prefix bytes and 8,192 tail bytes from each, preserving excerpts in the
review cache. Full hashes and total function counts remain report-attested:

- Go: SHA `cb6cac85b67f7f8fe03169d54c25962b0cece05b4c003faaf7b700b24cfdf766`,
  106,129 symtab entries, 90,139 FUNC entries.
- Rust: SHA `ef2a1d77106e8eedad5221329dcbfaf897c92bb9be8a17ebe3c9b078fbaf5f10`,
  17,408 symtab entries, 10,967 FUNC entries.

Both capture launchers were fully read and hashed, and match the saved prefix exactly:
`/usr/bin/strace -f -qq -c -o <own entry>/strace.txt -- <bound symbolized binary>`.
The launcher forwards `"$@"`; the frozen harness supplies `tail logs -o json`. The
summary-mode trace does not retain an exec argv event, so the latter is source-backed
invocation evidence rather than a separately observed syscall argv.

Both native artifacts were fully hashed and independently reduced as text:

- Go: 2,660 bytes, SHA
  `db3ad95e4f89c3331543283f028b312e929dfe38cd5ec6dba7d3bb4a3353b94e`;
  40 syscall rows, 1,130 calls and 88 error returns.
- Rust: 2,951 bytes, SHA
  `d7d2bc97c76739de97666263259db248e8d213a277cbb8a175298b4fe4679ffe`;
  45 syscall rows, 203 calls and five error returns.

The actual format is `% time seconds usecs/call calls [errors] syscall`, with dashed
separators and a `100.00 ... total` row. Blank error columns parse as zero. Every
call/error total equals the sum of syscall rows; displayed seconds also sum exactly
to the respective totals (0.026099 and 0.001764). Displayed percentages sum to 99.99
and 100.01, consistent with per-row rounding. Artifacts occupy their named capture
folders, exactly one each, far below the strict 16 MiB cap. This is structural format
validation only: these instrumented totals establish no ranking, ratio, RSS conclusion
or adequacy threshold. No new tool interoperability blocker was observed for this
strace capture. Perf, strace-writes and heaptrack interoperability were not tested.

## Lifecycle, limits and remaining gates

Coordination records give start `2026-10-14T20:56:40.739584Z`, end
`2026-10-14T20:56:42.256272Z`, exit 0 and elapsed about 1.517 s. Cleanup at
`20:57:06.780266Z` records the capture PID gone, no matching owned processes, and
successful nonblocking common-lock reacquisition/release. These are **historical
attestations**, not independently witnessed cleanup: I did not scan processes or open
the lock. The supplied token hash agrees with the recorded token; its 1,500 s scope
and 1,450 s capture budget agree with the brief and command. Root and Phase7 explicitly
released the slot according to the brief. No quiet window is currently in force here.
Token possession/hash matching is not an independent quiet-host witness.

Budget arithmetic recomputes to 1,330 s (2 + 390 + 906 + 32), below 1,450 s. Each
recorded child admission fits and each recorded free-space estimate exceeds its
3 GiB requirement; quota-blind headroom remains a limitation. The report is complete
and records no failure/stop/quarantine, clean control/global gates and finalization
inside the lock without an overrun. Its 53/53 end count recomputes as 49 tracked inputs,
two launchers and two artifacts. The unchanged end hashes and loaded-harness checkpoint
remain recorded execution evidence; this reviewer did not replay those checks against
native binaries, witness atomic writes or observe the historical lock lifecycle.

All six meter records preserve native wait4 scalars consistently through raw and final
samples. Every sampled resource log is empty, with zero resource samples and one missing
proc sample; every CPU screen is `resolution_limited` with comparative admission false.
Captured cost includes the profiler/wrapper/CLI tree and inseparable tracing overhead.
The unprofiled controls serve output/request compatibility only. Neither set supports
native timing/RSS ranking, ratios, throughput acceptance, Go managed-heap claims or
live-service delivery guarantees.

**No analysis command ran.** This offline table reduction does not produce or accept an
`analysis.json`. A future analysis still needs its own separately coordinated lock slot.
The v6 strace analysis path retains the table and deliberately supplies no adequacy
criterion: successful processing is expected to yield exit 3,
`analysis_complete_no_adequacy_criterion`, with zero interpretable captures. That is
source-established expected behavior, not an observed analysis result. Non-perf
adequacy criteria, other host/tool facts, reserve scaling and quota headroom remain
separate interpretation gates from R5/current plan. This review creates no automatic
acceptance from capture flags.

## Preservation and reviewer attempts

Before/after inventory covers 118 capture/coordination/anchor files: 116 complete small
file hashes and size-only entries for the two larger symbol dumps. Inventories are
identical, SHA `d79746efb9d14b69ea26965ee071c5b36659ab67c566d02e3e1f6a02b231ad06`.
The additional 34 small tracked-input before/after hash inventories are identical, SHA
`3a11fbfaad12fb0141fa643163ed8491a1fc2502e85817ef0e487ee0ed85a4ac`.
Thus the fully hashed artifacts/manifests are unchanged; the two larger logs have
unchanged size only, without an independent whole-byte immutability claim. No historical
review, source, manifest, capture record or bad attempt was rewritten.

Authoritative review evidence in the own cache:

- `audit.py`: `eed5ac088b6f163f207024ad86270dfe7ea8525c7b60790b9de7db773c4af126`.
- `reduction.py`: `f5e3dac4c949533932b68992587434c334097939a833d324cb2cc7e3d7369efa`.
- `functional-audit-v3.json`: `c9786eb099279ec4f9a0acff303c50da006376bc5b44a0f01eeefc02434250b7`.
- `bounded-symbol-excerpts-v3.json`: `234c2885cd2f97210a9a00009a52cdb3f7d126b1c9ff9c928943517baf07be11`.
- `reduction-attempt3.log` and `final-identity-check.log`: successful final executions.

The guard layer is installed before reviewer reduction code: process execution,
fork/exec/spawn/wait, subprocess, network/socket operations, real locking, signal delivery and
resource-limit changes are blocked; file writes are confined to the own cache. A
2 MB cap prevents accidental full reads of larger artifacts. TMPDIR points at the own
cache; no `/tmp` manipulation or repository-module imports occurred.

Preserved reviewer mistakes: the first inventory attempt refused the large Go symbol
dump; `audit-attempt1.py` and `attempt1-error.txt` retain it. An initial source search
used a nonexistent fixture path (`read-search-error.txt`). Reduction attempt 1 had a
missing parenthesis and did not run. Attempt 2 ran 318 checks with 13 failures caused
by three wrong reviewer assumptions: one trailing newline, identical periodic/final
event counts, and omitted `failure:null` in coordinator stdout. Its runner, log,
`functional-audit.json` and excerpts remain unchanged. Read-only diagnostic logs and
frozen source explain the corrections; attempt 3 passes all 318. No failed attempt
executed a native command or modified reviewed inputs.
