# Phase6 resource summary review — Opus r1

This is the first independent scoped review of the offline R5-4 resource description tool. It is not a whole-phase,
timing, source or budget acceptance. No timing campaign exists. No native client, fixture, meter, build, profiler,
soak, pressure run or real lock was used. Nothing was committed, delegated or sent externally.

## Reviewed identities

| Artifact | SHA256 |
|---|---|
| `harness/phase6_resource_summary.py` (488 lines) | `f6cea40b01a7bebff402488bb17936640c4b4bc46227548fd9a8f256f5294829` |
| `harness/test_phase6_resource_summary.py` | `833c4d7aa8b3c981e2ac406f8eef4d6ca532ffe0bd37581d64ab63cf7ea347dd` |
| `docs/phase6-resource-summary.md` | `7608b7b6096782975c069f14d1e360e345ae3ff023a0b48e58675ce8da45c21c` |
| frozen collector `harness/phase6_workloads.py` | `e2e83fa118c58a96ff55700be88f95f9b8b6479a289a8c59f447f836389fde51` |
| `harness/run.py` / `harness/meter.c` | `b280d129…a076` / `5d7ce9dc…70e9` |
| `docs/phase6-measurement-operating-decisions.md` | `c107bf8409ea8a5ba8e6b0c90f72407ea7d8a9bb724f340a1715cb1d1c8b47d9` |
| `docs/phase6-measurement-review-opus-r5.md` | `91799ef7eff942771b264367a9726dab8ba3615d973747e56fc32b459a4425df` |

All of these hashes were identical before and after my probes (`hashes-before.txt` / `hashes-after.txt`, `cmp`
equal). I left no `__pycache__` for the tool.

**Environment:** the Claude Code harness `/tmp` capture hit EDQUOT, so I could not see command output directly.
I ran each command with all stdout and stderr redirected into
`/home/ajs/.cache/pb-phase6/resource-summary-review-r1/` and read the files back. I did not touch any `/tmp` path.

## Method

I read the collector's actual writer paths: `collect`, `run_sample`, the `campaign()` sample wrapper, `schedule`,
`summarize`, `summarize_campaign` and the `finally` finalizer. I also read the meter's JSON emission. I compared each
tool validator against what that code can emit.

I then wrote `probe.py` (SHA256 `b45ecfd749fdea58d0aeeb7e5fd8b040223167ac74e3a7768d4c7695d952f399`). It drives the
**frozen** `p6.campaign()` and its real finalizer under guards. The guards block fork, posix_spawn, the exec family,
system, popen, `subprocess.Popen`, `flock`/`lockf` and `run.spawn_measured`. Only `run_sample` and `quiet_lock`
(and, per case, `WindowBudget.admission` or `summarize_campaign`) are mocked.

Mocked samples are deep copies of two retained real R5 `sample.json` records, with the contract
`workload_sha256`/sink/TLS recomputed by the collector's own formula:

- `review-r5-small-…/go-one-row`, SHA256 `7c8cf496…fa69`, `valid=true`, `rows=1`;
- `review-r5-small-…/go-idle`, SHA256 `4e969fa1…b889`, `valid=true`, `rows=0`,
  `first_record_observed_after_native_exit=null`.

Results are in `probes.json` (`b1bd07d1…262b`) and `probe.log` (`548c5189…b3f0`). The guarded-call count was zero.
Synthetic numbers come from those two retained functional records. They establish the tool's acceptance or refusal
of real-schema output, not performance.

| Case | Collector output | Tool |
|---|---|---|
| C1 finite primary, `one-row`, 1 warmup + 3 measured | complete, passed | OK. All R5-4 raw medians equal the retained values exactly (faults, switches, block IO, procfs IO, encoded/decoded rates, user CPU). `acceptance=not_assessed`; no `candidate_over_go`. CPU `resolution_limited`/`comparative_admissible=false` flags and quarantine are kept verbatim. |
| C2 finite primary, `one-row` + **`empty-batch`** | complete, passed | **REFUSED**: `first_record_observed_after_native_exit must be boolean` |
| C3 functional, **`idle`**, 2 sessions | complete, passed | **REFUSED**: same message |
| C4 functional, `steady`, 2 sessions | complete, passed | OK. `sessions` keyed `0`, `1`; no pooled `metrics` |
| C5 timing A/A 5/30 | complete, passed | OK. Labels `go-a`/`go-b`, kind `timing_aa` |
| C6 hash-framing observer control | complete, passed | OK. Kind `observer_control` |
| C7 `run_sample` exception at 3rd measured sample | incomplete, collector-exception record in journal | OK. `incomplete_valid_subset`, 1 invalid record |
| C8 window stop after first measured round | incomplete, `failure=null` | OK. go measured expected 3 / recorded 1 / unrecorded 2 |
| C9 finalization failure (`summarize_campaign` raises) | `summaries={}`, **no `expected_/recorded_measured_samples`** | REFUSED: `expected measured count mismatch` |
| C10 lock busy after provenance | no `samples.jsonl` file | REFUSED: raw `FileNotFoundError` |
| C11 invalid sample, `request_evidence.uncaptured_dogets=-1` | complete, not passed | REFUSED: `invalid integer: request_evidence.uncaptured_dogets` |
| C12 invalid sample with no observed first record (flag `null`) | complete, not passed | REFUSED: `…must be boolean` |
| C13 manifest declares `/bin/true` (symlinked), same SHA | complete, passed | REFUSED: `embedded role declaration mismatch: go` |

I also ran the real CLI with `python3 -I`. It refused C2 with exit 1 and created no output file. It wrote C1 with
exit 0, and its SHA256 `684327d0…b7fd` is byte-identical to the in-process output. A second write was refused as an
overwrite.

The author's 26 guarded tests re-ran: OK, zero failures. They need the script directory on `sys.path`, so they do
not run under `-I`.

## Findings

### RS-R1-1 — High: real valid samples with `first_record_observed_after_native_exit=null` refuse the whole campaign

Code: `phase6_resource_summary.py:274-276` requires a boolean whenever the key is present. The collector (`collect`,
`phase6_workloads.py:373-374`) sets this key to `None` whenever `first_record_ns is None`, meaning no complete record
was observed. That is the normal valid state for zero-row workloads:

- finite **`empty-batch`**, which is in the default finite workload set;
- functional/soak **`idle`**, which is in every duration set.

Retained real evidence shows this. `review-r5-small-…/go-idle/sample.json` and `rust-r4-idle/sample.json` are
`valid=true`, `rows=0`, flag `null`. `collector-functional-fixed-…/empty-batch/sample.json` has the same shape. It
also happens on invalid samples that observed no record (C12), which is a common failure shape.

Repro: probes C2, C3 and C12, plus the CLI on C2. As a result, the tool cannot describe a default finite primary
campaign, a matching observer control, or any functional/soak session. That is most of the R5-4 target data. The
26 tests miss it because the synthetic `fixture()` always sets `False`.

**Disposition (required before use on default campaigns):**

- Accept `None` alongside `True`/`False`. `source_flags` already counts `null` faithfully.
- Add a regression test that uses a real-shape zero-row valid record, for example a copy of the retained `go-idle`
  shape, inside a finite `empty-batch` campaign and a functional `idle` campaign.
- No collector change is needed.

### RS-R1-2 — Low: collector-representable invalid records can refuse the whole campaign

`validate_sample` applies unsigned-integer checks to every record, including invalid ones. The collector computes
`request_evidence.uncaptured_dogets = dogets - captured_dogets` (`phase6_workloads.py:853`) and marks the sample
invalid when that is negative (`:857-858`).

Repro C11: a single such invalid record makes the tool refuse the entire otherwise-describable campaign. That
conflicts with the doc's own policy: invalid records are excluded from statistics but the rest remains described.
It needs a fixture-side inconsistency, so it is rare.

**Disposition:** allow signed integers for this derived coverage difference, or avoid failing closed on coverage
values of invalid records while still listing them verbatim. A one-line test is sufficient.

### RS-R1-3 — Low: the finalization-failure claim does not match collector output

The doc says missing summaries in an incomplete finalization-failure report are permitted. In the frozen finalizer
(`:1303-1312`), `summaries`, `expected_measured_samples` and `recorded_measured_samples` are produced by one
`report.update(...)`. When finalization fails, all three are absent, so the tool refuses at `validate_report:206`
(C9). No test covers a collector-shaped finalization failure.

**Disposition:** either
- correct the doc to say these reports are refused, or
- when `summaries=={}` and `failure` starts with `finalization`, derive the measured counts from the journal prefix
  and mark the summary check as unavailable.

Either choice is acceptable. The first is smaller.

### RS-R1-4 — Informational: post-provenance failures before journal creation

Some failures happen after the collector has written provenance but before it creates `samples.jsonl`: a busy lock
(C10), contract A/A failure or instability, or an A/A that does not fit the window. In those cases no
`samples.jsonl` exists, and the tool refuses with a raw `FileNotFoundError`. Nothing resource-related is lost, but
the doc only discusses pre-provenance reports and empty journals.

**Disposition:** add one doc sentence.

### RS-R1-5 — Informational: declared-path string equality is stricter than the collector's binding

`validate_report:182-183` requires the manifest's raw declared path to equal `measured_roles.*.declared.path`. The
collector stores the resolved path from `file_info` and binds by SHA only (`:1019-1025`), so it accepts a symlinked
or non-canonical declaration (C13). The tool then refuses that report.

This is latent: the prepared R6 manifest's `pb-go`, meter and candidate paths are already canonical (checked with
`realpath`).

**Disposition:** compare resolved paths, or keep the check and document that canonical manifest paths are expected.
No collector or manifest change is needed for the prepared R6 manifest.

### RS-R1-6 — Informational: the journal comes from the unresolved report directory

`summarize_paths` takes `samples.jsonl` as a sibling of the path the user passes, not of the resolved `report.json`.
If that path is a symlink, the tool can pair the report with a journal from a different directory. Schedule, count
and summary agreement usually catches a mismatch. Collector v2 has no journal digest, and the doc already says so.

**Disposition:** document this, or derive the sibling from the resolved path.

## Confirmed without findings

- **Scope and metrics.** Every R5-4 raw field is described with available/missing counts, median and range, per
  workload, label and population. Zero is kept distinct from null. Rates are copied, never recalculated. procfs IO
  is labelled as not network traffic.
- **Populations stay separate.** Warmups and measured samples are separate populations. Invalid samples never enter
  metric statistics. Duration rounds are reported per session with no pooled `metrics` (C4). Primary, observer
  control, timing A/A and functional cohorts stay distinct and explicit (C1, C4-C6).
- **No ratios or budget results.** No ratio, bootstrap interval, budget result or admission promotion is produced.
  Output always carries `acceptance="not_assessed"`. Source `candidate_over_go` is not copied, so null ratios cannot
  be turned into a user budget result.
- **CPU and throughput quarantine kept.** `ratio_quarantine`, `metric_quarantine` and `metric_scope` are reproduced
  verbatim. Per-sample `cpu_screen` and production flags appear as counts (C1). Source-valid and
  comparatively admissible remain distinct.
- **Matches the actual collector output.** Schedule mirroring matches `schedule()` exactly for finite shuffling,
  duration reversal and signed session indices, across timing A/A, observer control, functional and window-stop
  cases. The tool's checks of report summaries against the journal reproduce `summarize()`, and the contract counts
  match.
- **Incomplete output.** Collector-exception and window-stop reports are described as `incomplete_valid_subset` with
  unrecorded counts (C7, C8).
- **Authenticity stated honestly.** The doc correctly says collector schema 2 does not hash `samples.jsonl` and
  that changes confined to raw resource fields cannot be authenticated. I found no overclaim.
- **Input binding, bounds and output handling.** Inputs are bound by a single read and a reread-before-output check.
  Byte, record and nesting bounds hold. Output is exclusive-create with mode 0600 and refuses to overwrite.
- **Batch latency.** Batch-latency quantiles and sidecars are correctly out of scope.
- **Duplicated validation.** The tool re-checks much of what the collector already validated: manifest hash,
  budgets, fixture build, roles and schedule. That is consistent with the collector and useful for detecting
  mismatched report/journal pairs, apart from the over-constraints in RS-R1-1, -2, -3 and -5. It adds no new
  collector prerequisite.

## Disposition

**Not ready to use on default campaigns until RS-R1-1 is fixed.** The fix is small and confined to the tool
(validator plus a real-shape test); no collector, schema, budget or plan change is needed. RS-R1-2 and RS-R1-3 are
low-severity consistency fixes, and RS-R1-4 to RS-R1-6 need a doc sentence or optional hardening.

Once RS-R1-1 is fixed and has a real-shape regression test, I see no remaining blocker for the stated offline,
descriptive-only R5-4 scope. A focused re-review of only that diff, rerunning `probe.py` cases C2, C3 and C12,
would be enough.
