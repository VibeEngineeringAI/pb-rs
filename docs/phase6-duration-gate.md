# Offline supplemental duration evidence gate

`harness/phase6_duration_gate.py` adds the two missing checks in findings 2/3 of
[the short-duration review](phase6-short-duration-review-sol.md). It supplements
existing acceptance. A supplemental pass grants no native launch, sink admission,
comparative interpretation or whole-phase acceptance. The user's continued-test
authorization is retained context; this work runs only bounded offline Python.
Root independently reviewed the two predicates, reproduced all 19 tests and all 40 historical observations, and added eight negative probes. This closes the supplemental-check prerequisite for subsequent functional-duration sink controls; existing validation and operational coordination remain required.

For each saved duration observation and each separate same-Go control, it binds
`sample.json` to its journal/control record, reads `meter.json`, `scenario.json`
and **raw `fixture-final-summary.json`**, and inspects the retained fatal stderr
and fixture stderr. It checks:

- Raw live `scheduled_batches`, `emitted_batches`, `omitted_by_deadline` and
  `omitted_by_termination` are present nonnegative integers, rejecting booleans,
  floats, strings, null, missing fields and negatives. Emitted plus both omission
  counts must equal the saved scheduled count. No total is manufactured from
  those components. Emitted, raw stream batches and captured batches must agree.
- The scheduled count independently follows the saved scenario: integer ceiling
  of duration/interval, capped by a positive batch limit; a count-only live op uses
  its batch limit. Subscription repeats multiply counts. The fixture treats absent
  or zero repeat and finite batch count as one. Unbounded duration-without-interval
  schedules are refused. Production schedule/delivery fields and the entire copied
  `production.live` projection must agree with the raw final summary.
- Sample and journal/control `exit_code` and `termination` bind directly to the
  actual meter JSON and require integer exit one and exactly `{"exit":1}` with
  native wait4 provenance. Valid, complete capture and validated-contract flags
  must be true; timeout must be false and errors empty. Fixture panic, failed Send
  and nonempty fixture stderr fail. The fixture terminal must be status
  `InvalidArgument`, with exact message `fixture stream complete`; retained stderr
  must contain the exact basic fatal `COMMAND_FAILED` error with retryable false.
  Its saved byte count and SHA256 are also checked. `valid=true` alone never passes.

Slow-sink deadline omissions are valid when conservation holds. No equality across
Go/Rust observations is required. The historical final hash pair's 466 versus 467
batches both pass independently. Slow-sink must retain its false throughput
admission flag. Existing report `ratio_quarantine`, `metric_quarantine`,
`metric_scope` and `contract_comparison` metadata are retained verbatim. The gate
emits no comparative statistics or restored ratios and always withholds comparative
admission. It does not revalidate report ratios or their underlying CPU screens.

Run from the repository with a fresh output filename whose parent already exists:

```sh
python3 -B harness/phase6_duration_gate.py \
  --report /home/ajs/.cache/pb-phase6/campaign-r6-short-slow-sink-hash-01/report.json \
  --out /home/ajs/.cache/pb-phase6/duration-gate-v1/new-supplemental-result.json
python3 -B harness/test_phase6_duration_gate.py
```

CLI exit codes are 0 for all supplemental checks passing, 1 for per-observation
failures (with a result file), and 2 for refused campaign/I/O structure. Result
records contain per-sample pass/fail, failure reasons, observed fields even on
failure, successful predicate evidence and links to input receipts. Every inspected
input's size and SHA256 come from the same bounded buffer used for analysis.
Missing inputs have an explicit failure rather than an invented hash.

Inputs are regular files confined to the selected campaign. Symlinks, hardlinks,
traversal, inode aliases and special files are refused. The output must be disjoint
from the campaign, use an existing nonsymlink parent, and be new; exclusive creation
never overwrites. Reads are capped at 4 MiB/file (8 MiB for the journal), 64 MiB
total, 512 files, 64 measured records, 256 scenario operations, JSON depth 48 and
200,000 JSON nodes per parsed object. JSON rejects duplicate keys and nonfinite
numbers; field checks preserve exact types. Finite/control batch counts are checked
without requiring live evidence. This deliberately supports completed schema-2
functional duration reports, zero warmups, Go/candidate roles, at most sixteen
rounds per role, and two same-Go controls. Mixed finite/live batch streams and
other operation families are outside this supplemental schema.

Before analysis/test imports, a process-wide audit guard denies process/exec/fork/
spawn/system/kill/lock/socket operations and unsafe imports, patches process and
fcntl lock entry points, rejects `/proc`, `/sys` and `/tmp` opens, and confines
writes to the owned scratch or the validated new CLI output. Temporary directory
environment variables point to the owned scratch. Run this module in a dedicated
interpreter: the guard intentionally persists. This is an accidental-execution
guard, not a security sandbox or a transactional filesystem snapshot. The approach
follows the read-only review's `short-duration-review-sol/guard.py`; the small
same-buffer bounded reader follows `phase6_resource_summary.read_bound`, with
additional alias and campaign-confinement checks. Schedule rules are transcribed
from saved fixture `liveSchedule` and repeat/count behavior. Neither the runtime
collector nor fixture is imported, AST-executed or launched.

Validation settled at **19 passing tests**, including synthetic real-shape live
and control evidence, strict counter mutations, omission off-by-one, conserved
but false scheduled totals, copied projection drift, emitted/stream/capture drift,
exit and termination changes in all three sources, native exit/signal mutations,
timeouts/errors, incomplete flags, fixture panic/Send/terminal mutations, missing
meter, malformed nested types, duplicate/nonfinite JSON, bounded I/O, path/output
refusals and guarded entry-point self-checks. CLI success, per-sample failure and
refusal are exercised in-process. Synthetic fixture data does not depend on the
historical campaigns; all test writes remain in the owned scratch and are retained.

The final offline run passes **40/40 observations** in the ten named short-duration
campaigns: twenty measured sessions and twenty controls. It reads **270 files /
2,080,221 bytes**. Independent bounded after-read inventories equal every receipt.
The collector before/after hash remains
`e2e83fa118c58a96ff55700be88f95f9b8b6479a289a8c59f447f836389fde51`.
The frozen parent, captures, existing source, documents, manifests and reviews were
not edited. No native/product/fixture/meter/build/compiler/profiler/network launch,
real lock/token operation, agent or commit occurred.

Settled artifacts reside only under
`/home/ajs/.cache/pb-phase6/duration-gate-v1/`. `review-ready-{workload}-{mode}.json`
contains each campaign's exact input identities and field evidence;
`review-ready-validation.json` binds all ten result hashes and the implementation
hashes, and records unchanged input inventories. Earlier scratch attempts remain.

- Gate SHA256: `1a9481529a0627fa5a691b684dc0ce3a094b4dd56797e1161b8df83229b2a1a3`.
- Test SHA256: `14374b9a04116e89fd26198f0786291c8bab7a2dc790814a95fcea90c43f1d92`.
- Final historical receipt SHA256:
  `726fcfed8a6f031faf8b99b16aa0b8f29a7cab6af17bd9942ccf67b780e60c5e`.
- Test log: `tests-settled.log` (19 tests, zero failures), SHA256
  `4399eee192fbf45ecc880b0138a8948b658c03da3ec12a37aaca6688faae3f7b`.

This gate does not redo the output/auth oracle, event-history reconstruction,
existing acceptance, binary/parent identity audit, resource review or CPU admission.
It cannot authenticate historical collection or restore missing stdout. Hashes
identify the inspected retained bytes only. Supplemental passes remain layered
on existing validation, with gated comparisons withheld.

Root acceptance and independent probe hashes are retained in `duration-gate-v1/root-acceptance.json`. The current gate deliberately accepts only the functional tier; it must be extended and verified before applying it to soak-tier reports.
