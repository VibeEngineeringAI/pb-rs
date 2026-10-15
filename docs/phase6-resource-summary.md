# Phase6 offline resource description

`harness/phase6_resource_summary.py` addresses measurement review R5-4 and the
[operating decisions](phase6-measurement-operating-decisions.md) using retained
data only. The root R6 source and frozen collector have scoped acceptance; this
tool does not extend that acceptance. No timing campaign exists at implementation
time. The saved functional observations and mock artifacts used below establish
schema and extraction behavior only.

The standard-library tool imports no collector, shared harness, fixture, process
runner or profiler. It reads one collector-v2 `report.json` and `samples.jsonl`
beside that report's captured resolved path, and writes a standalone descriptive
JSON file to a new path.
It makes no ratios, bootstrap intervals, budget results, source admission,
quiescence claims or performance acceptance decisions. Even a source report with
`passed=true` produces `acceptance="not_assessed"`. Source ratios are not copied.

## Invocation

Use an existing parent directory and an unused output filename:

```sh
TMPDIR=/home/ajs/.cache/pb-phase6/resource-summary \
GOTMPDIR=/home/ajs/.cache/pb-phase6/resource-summary \
CLAUDE_CODE_TMPDIR=/home/ajs/.cache/pb-phase6/resource-summary \
PYTHONDONTWRITEBYTECODE=1 \
python harness/phase6_resource_summary.py \
  --report /path/to/retained-campaign/report.json \
  --out /home/ajs/.cache/pb-phase6/resource-summary/description.json
```

This is an offline command, not a collector invocation. Exit 0 means a
descriptive file was written, including when the source campaign was incomplete
or contained invalid samples. Exit 1 means the tool refused the inputs or could
not write the output. Success prints output path, exact byte size and SHA256.
The tool does not create output directories. It refuses existing files, input
aliases and dangling output symlinks; exclusive creation also protects against
a competing writer. Output permissions are 0600. It writes directly to the new
file without temporary files or locks. An ordinary write exception removes only
the inode it created; process death during writing can leave an incomplete file
which must be inspected and removed explicitly before retrying.

## Populations and schema

`groups[workload][label]` has separate `measured` and `warmups` populations.
For finite campaigns each population contains `expected`, `recorded`,
`unrecorded`, `valid`, `invalid`, `metrics`, `observation_coverage` and
`source_flags`. Metrics use only samples explicitly marked `valid=true` by the
source journal, with no contradictory retained validation/failure flags or
errors. Invalid observations never enter metric statistics, including their
otherwise usable raw resource values. An invalid warmup does not erase valid
measured absolutes; the source partition failure remains explicit.

Every metric reports `available`, `missing`, `median`, `minimum`, `maximum`.
`missing` counts absent/null values among source-valid samples only; invalid and
unrecorded samples have their own counts. Zero is an available observation.
An empty population has null statistics, never fabricated zero statistics.
Rates are copied from retained sample fields, never recalculated from counts
or elapsed time.

The actual sample schema keeps these fields at the top level:

- `minor_faults`, `major_faults`, `voluntary_context_switches`,
  `involuntary_context_switches`, `block_input_operations`,
  `block_output_operations`;
- `proc_io_syscr`, `proc_io_syscw`, `proc_io_rchar`, `proc_io_wchar`,
  `proc_io_read_bytes`, `proc_io_write_bytes`, `proc_io_cancelled_write_bytes`;
- `encoded_bytes_per_second`, `decoded_bytes_per_second`,
  `output_bytes_per_second`;
- `wall_ms`, `cpu_ms`, `cpu_user_ms`, `cpu_system_ms`, `peak_rss_bytes`,
  `first_complete_record_ms`, `fixture_to_first_complete_record_ms`,
  `cpu_us_per_row`, `rows_per_second`;
- absolute `stdout_bytes`, `rows`, `batches`, `fixture_encoded_bytes`,
  `fixture_decoded_bytes` when available.

Native faults, switches and block operations have the meter's
`native_child_wait4` scope. Linux procfs I/O is not network traffic. Encoded and
decoded byte counts are fixture FlightData quantities, not HTTP/2/TLS wire bytes.
Counter/byte fields require nonnegative integers; numeric metrics reject booleans
and nonfinite values. Signed first-record latency is allowed to retain invalid
clock observations faithfully; invalid samples remain excluded. Missing native
fields are not synthesized, and `proc_io_available=false` cannot coexist with
non-null procfs I/O observations. An observation after native exit is retained
with its source flag, not clipped to wall time.
The first-record-after-exit flag is boolean or null: null means that no complete
record was observed, including valid zero-row `empty-batch`/`idle` samples and
invalid no-record samples. For source-valid samples, null remains a separate `source_flags` category;
invalid records are excluded from that flag tally as well as from statistics.

Functional and soak rounds identify separate duration sessions. Their population
has counts plus `sessions[session_index]`, each containing the same descriptive
structure; it has no pooled `metrics`. Finite rounds are repetitions within the
single finite campaign. Inputs from separate campaign reports are never combined.
Workloads, labels, sink conditions, TLS conditions and collector modes stay
separate. Timing A/A labels remain `go-a` and `go-b`; they are not candidate
evidence. Hash-framing controls remain their own campaign cohort. Functional
source tiers remain explicitly `functional`.

Contract reproducibility A/A is collected separately in `go-aa.json`, not in the
sample journal. The output records `contract_aa.status="not_in_these_inputs"`;
this command neither requires that additional artifact nor invents A/A samples
or counts. Diagnostic-only samples and aggregate reports are refused.

## Invalid, omitted and incomplete evidence

`invalid_records` identifies each invalid journal record by line, workload,
label, phase, round, session, schedule metadata, exact line-content hash and
retained errors. The line hash excludes its newline; the journal hash includes
all bytes. `observation_coverage.valid` and `.invalid` separately report
available/missing counts and totals for retained invalid-record/sequence counts,
missing proc samples, dropped resource/visibility/prefix bytes, dropped fixture
events, uncorrelated/dropped batch visibility, uncaptured DoGets and production
omissions by deadline/termination. These are different units and overlapping
coverage mechanisms; do not sum them into a single loss count. A missing total
is null. On invalid records only, derived `request_evidence.uncaptured_dogets`
may be negative when captured requests exceed the exact summary. Its signed
difference is retained in invalid coverage, not interpreted as a negative loss
count. Across invalid records this field’s total is a signed net: positive and
negative values can cancel, including to zero. Zero therefore does not establish
complete request coverage; inspect the retained per-record errors and raw journal
for deficits and excesses. Invalid metric values still never enter statistics. Other raw counters
remain unsigned. The tool does not open bounded batch-latency or resource sidecars and
does not estimate quantiles or infer their missing contents.

An incomplete report may describe the exact recorded schedule prefix, including
an empty journal, provided its campaign provenance was initialized and its
recorded count agrees with that prefix. Output uses
`statistics_status="incomplete_valid_subset"`, retains source failure/end-identity
status, and reports unrecorded counts for every declared workload/label/phase.
Changed campaign identities stay visible alongside prior source-valid absolutes.
This never converts an incomplete partition to acceptance. A pre-provenance
failure report is refused because it cannot identify a cohort.
A post-provenance failure before journal creation (for example lock contention
or failed contract A/A) is also refused with `missing retained samples.jsonl;
campaign has no retained journal`; an absent file is not treated as an empty journal.

Completed reports must have their full declared schedule and workload summaries.
An invalid sample in a source-passed report is inconsistent and refused. Reports
without expected/recorded measured-count metadata are refused. In particular,
the frozen finalizer can fail before assigning summaries and counts together;
those reports are refused, and no counts are derived to repair them. An incomplete
report with count metadata but no summaries has no absolute-summary check coverage.
Where summaries exist, every label's eight frozen
collector metrics must match journal valid-sample count, median, minimum and
maximum, and contract comparison counts must match. No source-valid sample is
reclassified as comparably admitted: `source_flags`, `source_status`, and the
verbatim `source_restrictions` retain the CPU/throughput gates, contract comparison,
ratio quarantine, metric quarantine and metric scope. Absolute rates do not imply
that throughput attribution is admitted. Matching observer controls are still
required by the operating decisions before interpreting output-heavy wall time.

## Binding, rejection and bounds

Each input's parsed data, byte size and SHA256 come from the same bounded read.
The file must be regular. After parsing and preparing bounded output, both
original paths are reread and their resolved paths, sizes and digests must still
match before exclusive output creation. The journal location comes from the
resolved report path captured by that same initial bounded read, so a report
symlink cannot select a decoy journal beside the unresolved link. Tests exercise byte changes and symlink
repointing, including a repoint to identical bytes. The recorded hashes always
describe the originally read bytes. This is not an atomic two-file snapshot;
the reread cannot detect an intervening change restored before that check, or
a change after it. Inputs should be completed retained artifacts.

The reader checks schema/type declarations; embedded stable-manifest hash,
budget and fixture-build consistency; selected/declared executable role bindings;
workload definition hashes, TLS, sink and optional collector-mode metadata;
the exact frozen deterministic schedule prefix (including order and sessions);
recorded/expected measured counts; and report/journal absolute-summary agreement.
Duplicate JSON keys, nonfinite numbers, malformed contracts, unknown cohorts,
duplicate/extra/out-of-order records, partial final lines and contradictory
validity metadata fail closed. Historical binaries, parent manifests and budget
files are not reread or executed. Their retained identities are descriptive
provenance, not fresh authorization or source acceptance.
Embedded manifest role declarations are checked against captured declared roles
by SHA256, matching the collector's binding. Raw symlinked/noncanonical manifest
paths need not equal the collector's then-resolved paths; the offline tool never
resolves today's historical declared executable paths. `role_path_provenance`
retains each manifest-declared, captured bound-declared and selected path separately.

**Collector schema 2 does not record a digest of `samples.jsonl`.** Its summaries
also omit the R5-4 raw resource fields. Thus schedule, contract, count and summary
agreement cannot authenticate changes confined to those fields, distinguish a
replacement with identical checked metadata, or prove sample execution/binary
authenticity. This command records both input hashes for subsequent verification;
it does not claim a historical cryptographic binding that the collector never
created. No new collector prerequisite or manifest schema is introduced.

Hard limits are 4 MiB report, 64 MiB journal, 1 MiB JSONL record, 10,000 declared
and retained records including warmups, 64 workloads, nesting depth 64, and
16 MiB serialized output. Numeric magnitude is limited to `1e100`; raw counters
to unsigned 64-bit range. These are offline-reader bounds, not collection
requirements. Both input bytes and records are retained in memory within these
bounds; JSON objects add interpreter overhead. Signed seeds/session indices are
supported. The output limit is checked before creating any output file.

No owner-maxima sum, distinct-heap estimate, network throughput, memory release,
RSS plateau inference or cross-session confidence interval is produced. The
[resource interpretation](phase6-resource-interpretation.md) remains governing.

## Implementation verification

Run the cache-only guarded tests with:

```sh
PYTHONDONTWRITEBYTECODE=1 python harness/test_phase6_resource_summary.py -v
```

The test cache defaults to `Path.home() / '.cache/pb-phase6/resource-summary'`;
`PHASE6_RESOURCE_SUMMARY_CACHE` optionally selects another disk directory outside
`/tmp`. During module execution only, `setUpModule` sets the three temporary-directory
environment variables, installs process/lock guards and lazily imports the SUT
and collector under those guards. `tearDownModule` and `addModuleCleanup` restore
the patches and environment, including after setup failure. Importing the test
module during discovery changes neither process APIs nor environment variables.

Initial validation passed all 26 pure/mocked tests, with zero failures, errors, skips or forbidden
calls. Guards are installed before importing the implementation and cover
subprocess APIs, fork/forkpty, posix_spawn/posix_spawnp, system/popen, exec/spawn
variants, `run.spawn_measured` and real `flock`/`lockf`. All filesystem test artifacts use the designated
disk cache. Coverage includes extraction, zeros versus missing, boolean/nonfinite
rejection, measured/warmup selection, invalid separation and omission coverage,
duration session separation, incomplete prefixes, cohort/provenance mismatch,
content drift, symlink repointing, bounds, hashes, overwrite refusal and absence
of ratio/budget output. No build, native fixture/client/meter, profiler, native campaign,
real lock, commit, delegation or external communication was performed.

Read-only schema evidence:

- Saved functional `tmp/review-r5-small-1791983701170602345/go-one-row/sample.json`:
  8,744 bytes, SHA256
  `7c8cf496caf6f0fc0f9a9eaa1e477fe49be5dba5498e3e45fe515b73da8dfa69`.
  Its top-level raw fields were extracted and matched exactly in a separately
  labeled functional-only schema check. It was not converted into a timing campaign.
- Saved mock R5 `P6-timing-aa-shape/campaign/report.json`: 37,500 bytes, SHA256
  `2dd69619e542bd9d3094aa1218afca03aa6cac7bfd1fe69153b5ac8f001107d1`;
  sibling journal: 133,140 bytes, SHA256
  `6ef17f7d90bd2ed53023cb6c3834ccd0dd5af8e39570e57d30c18dcded795748`.
  Its deliberately opaque `workload_sha256="w"` is refused as a contract/cohort
  mismatch. The mock supplied schema evidence, not accepted provenance.

Cache `/home/ajs/.cache/pb-phase6/resource-summary/validation-results.json` records
exact identities, the guarded test log, functional schema evidence and a
schema-conforming synthetic standalone output. The frozen collector, its tests,
shared harness, meter source, plan, R5 review, operating decisions and budgets
had identical hashes before and after validation. Collector SHA256 remains
`e2e83fa118c58a96ff55700be88f95f9b8b6479a289a8c59f447f836389fde51`.
Implementation and test hashes are recorded in that cache result; final artifact
hashes are also delivered to the coordinator. Independent review follows.

## Scoped Opus R1 corrections

The [Opus R1 review](phase6-resource-summary-review-opus-r1.md) and its original
probe and logs are preserved. RS-R1-1 permits the source's nullable first-record
flag; RS-R1-2 preserves signed derived uncaptured DoGets on invalid records only;
RS-R1-3 corrects the finalization-failure documentation without deriving counts;
RS-R1-4 adds the missing-journal diagnostic; RS-R1-5 binds historical role
declarations by SHA and retains all path forms; RS-R1-6 locates the journal from
the report's captured resolved path. Bounds, same-read identities, reread checks,
invalid-metric exclusion and descriptive-only restrictions remain in place.

All 35 guarded tests pass. Nine new regressions cover current R5 zero-row shapes
in finite empty-batch primary/control and functional idle sessions, invalid
no-record observations, signed invalid coverage, the actual frozen finalizer's
missing-count refusal, post-provenance missing journals, historical symlink and
noncanonical role paths, and a report symlink with a resource-only decoy journal.
These call the actual collector `campaign()` and finalizer while mocking
`run_sample`, `quiet_lock` and host metadata; no measured native process is started.
They copy retained R5 functional row/idle records, as the independent probe did;
the resulting mock campaigns are schema tests, not timing evidence. An initial
test-helper attempt used an older empty-batch artifact with a pre-R5 contract;
that error log is retained, and the helper now uses the current R5 zero-row shape.

The unchanged review probe is replayed with only its output/cache root redirected
inside `resource-summary/r1-corrections-v1`. C2, C3, C11, C12 and C13 now produce
descriptions; C1, C4–C8 retain their prior behavior. C9 remains refused for missing
count metadata, and C10 now has the concise missing-journal refusal. The causal
negative control loads the original implementation from a cache copy and runs
six regressions. Finite primary/control, idle and invalid no-record cases fail
at the old boolean-only check; signed invalid coverage and declared paths are
refused, and the report-symlink test detects the wrong journal. The corrected
implementation passes the same cases. Process/real-lock guard call counts are zero.

Correction results, probe replay, negative-control log, preserved original file
copies and exact hashes are retained in
`/home/ajs/.cache/pb-phase6/resource-summary/r1-corrections-v1/`.
Fresh independent review remains the parent gate; this implementation record
does not grant timing, source, budget or whole-phase acceptance.

## Test fixture portability and isolation

The mocked collector regressions now load
`tests/fixtures/phase6-resource-summary/{go-one-row.json,go-idle.json}` relative
to the repository root derived from the test module's `__file__`. They verify
both files' exact byte sizes and original SHA256s from the adjacent `manifest.json`
before parsing. The two files are verbatim retained R5 functional artifacts
(8,744 and 8,819 bytes respectively); the manifest records their original source
artifacts and local provenance. They establish collector shapes only, not timing
or performance results. Those source paths are provenance text, not runtime inputs;
the tests have no cache-source dependency or fallback.

Portability validation imports the test module and checks that `subprocess.Popen`,
the other guarded process/lock APIs and the environment retain their original
identities before test execution. All 35 guarded tests pass while reads/opens
of both original sample cache paths explicitly raise, with zero attempted access
and zero process/real-lock calls. Fixture-corruption negative controls change
only cache copies of each fixture; both are refused at hash verification.
The production implementation retains SHA256
`061ef1c3473f8326d3c7d169334269d87702c85c3ebe239361be9fe0b9f925c4`.

The new correction record, isolation/absent-cache proof, fixture-negative results
and hashes are retained under
`/home/ajs/.cache/pb-phase6/resource-summary/portability-v1/`.
The R1 review, original logs and `r1-corrections-v1` evidence remain untouched.
Source is settled for fresh independent Opus review; this test-only correction
grants no additional admission or acceptance.

## Independent correction review

The [executed Opus R2 review](phase6-resource-summary-review-opus-r2.md) accepts
the settled implementation for offline descriptive use. It independently passed
35 tests under discovery with a following sentinel module, denied access to
former host-cache inputs, and verified cleanup after setup failure. Collector-shaped
probes close all six R1 findings. This is not timing or budget acceptance.

The review’s low coverage-net and valid-only flag observations are clarified
above without changing extraction. A hand-written report with malformed nested
types can still produce a traceback instead of a concise refusal; it exits
nonzero and creates no output. Such input is outside the frozen collector’s
output shapes. No catch-all exception path or schema relaxation was added.
