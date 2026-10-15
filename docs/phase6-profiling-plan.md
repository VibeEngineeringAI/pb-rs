# Phase6 profiling adapter plan (v6, after reviews R1–R5)

**Status.** Implementation, guarded pure-mock tests and offline provenance preparation only. None of the following
has ever run:

- profiler, readelf or tool `--version`;
- the fixture, the native CLI or the meter;
- a build, pressure test, timing run or network access;
- a real lock, a token or a window.

This is not native evidence, and it is not an acceptance.

**Gate and coordination.**

- Root independently verifies the focused failure cases. Root then coordinates any quiet window.
- Capture and analysis each need their own externally coordinated slot under the common lock.
- The analysis CLI takes no token. That is **not** an authorization.

**Review history.**

| Review | Kind | Outcome |
|---|---|---|
| [R1](phase6-profiling-review-r1.md) | source-only; root executed the repros | — |
| [R2](phase6-profiling-review-r2.md) | independent | — |
| [R3](phase6-profiling-review-r3.md) | independent | — |
| [R4](phase6-profiling-review-r4.md) | independent | — |
| [R5](phase6-profiling-review-r5.md) (`9aec6e6b…`) | independent, focused on the v5 delta | All R4 findings closed. Tiny-probe **source ready**. Interpretation still blocked. |

Root selected R5's four small reporting, validation and test fixes before any native run. v6 is the author's narrow
correction for those four. **It is not a whole acceptance.**

**Preserved unmodified:**

- the v1–v5 sidecars, plans, reports, logs and author tools (v5 `hashes.txt` `015589d3…`);
- reviews R1–R5 and every review cache.

## What changed in v6 (minimal diff)

The full source change is `profile-adapter-v6/source-v5-to-v6.diff` (`3918c461…`, 43 changed lines). It was
generated against the exact v5 source, rebuilt by reversing `edit_r5.py` and checked against v5's SHA256
`d28454d6…`.

There is no schema or API change, and no frozen file changed. The fresh sidecar differs from the v5 sidecar only in
the bound `phase6_profiles` hash.

### Printed summary (R5-1)

- The summary now carries `journal_sealed`, which is true only after the final journal save succeeded.
- After any failure, the printed `interpretation` is never positive. That covers a failed or interrupted final save,
  and an error after the save, such as at unlock.

### R5-1 (fixed): a positive printed interpretation requires a successful final save, and no later failure

`analyze_command` sets `sealed = True` only after the final in-lock `save()` returns. The outer exception path then
does three things:

- It runs after the lock is released, so it writes **nothing**.
- It calls `retract_positive`, which in memory sets:
  - `analysis_complete: false` and exit 1;
  - interpretation `analysis_incomplete`;
  - every entry's `interpretable` to false;
  - every role's `interpretable_captures` to 0.
- The summary is printed from that retracted state, with `journal_sealed`.

**Disk versus exit/stdout.** The on-disk journal is always the last checkpoint that **completed**:

| Failure | On-disk journal | Exit / printed |
|---|---|---|
| Final save fails or is interrupted (EDQUOT/ENOSPC, SIGINT/SIGTERM) | Last in-progress checkpoint; no interpretation, never positive | Exit 1, `analysis_incomplete`, `journal_sealed: false` |
| Error **after** a successful final save (for example at unlock) | The sealed final journal, deliberately **not** rewritten after unlock | Exit 1, `analysis_incomplete`, `journal_sealed: true` |

In the second case the journal can still record a completed analysis whose end checks genuinely passed. The process
exit code (1) and the printed summary are authoritative for that run. **A coordinator must judge by exit status first.**

**Test:** `test_final_save_failure_or_later_error_never_prints_a_positive`. It uses five cases:

1. EDQUOT (`OSError(122)`) at the final save;
2. the installed SIGINT handler at the final save;
3. the installed SIGTERM handler at the final save;
4. an `OSError` at unlock after the sealed save;
5. a `KeyboardInterrupt` at unlock after the sealed save.

Every case asserts:

- exit 1, printed exit 1, printed `analysis_incomplete`;
- the expected `journal_sealed` value;
- failure evidence;
- no write after lock exit;
- the on-disk journal equals the last completed checkpoint (`in_progress` without interpretation, or the sealed
  `final`);
- no `.partial-*` file.

### R5-2 (fixed): the repeated-interrupt fallback retracts role counts

The fallback (interpretation interrupted again during resealing) now marks the failure and `state: final`, then calls
the same `retract_positive`. Roles keep their rows with `interpretable_captures: 0`. Entry flags and the
interpretation are reset as well.

**Test:** `test_repeated_interrupt_after_a_positive_interpretation_retracts_roles`. This reproduces R5's race:

1. Interpretation #1 completes **positive** (asserted).
2. A signal arrives.
3. The reseal's interpretation #2 is interrupted.

The final journal shows exit 1, `analysis_incomplete`, zero interpretable, every role row at 0 and every entry false.

### R5-3 (fixed): explicit artifact policy, rejected before any tool

| Entry | Artifacts allowed |
|---|---|
| control | none |
| capture marked `interpretable` | exactly one |
| failed or skipped capture | zero or one; never analyzed |

A missing artifact is refused in `verify_report_binding` with a precise message, before any tool runs. The
`IndexError` path no longer exists. Name and folder binding are unchanged from R4-3.

**Test:** `test_interpretable_capture_without_its_artifact_is_refused_before_tools`. It forges an empty list and a
removed key, each inside the capture root. Both give exit 1, the policy message, no `IndexError`, and no tool. The
existing `test_artifact_must_be_the_single_mode_artifact` now expects the precise control-entry message, alongside its
unchanged-copy control.

### R5-4 (fixed): heaptrack name rule tested

**Test:** `test_heaptrack_artifact_name_rule`.

- The forged report is a heaptrack capture placed inside its own capture root.
- An unchanged copy with the same `heaptrack` prefix passes, with exit 3 (no adequacy criterion).
- The launcher script used as the artifact is refused with "artifact name 'launch' is not the heaptrack artifact",
  and no tool runs.

## Evidence (author-executed, guarded pure mock; not independent)

**Suite: 66/66 OK** (the 62-test v5 baseline plus 4 new). Log: `profile-adapter-v6/tests-v6.log` (`007d0887…`).

**Targeted mutants.** Run with `mutants_v6.py` (`ffcd1796…`) under R5's guard list, including `sigqueue`. There is one
guarded process per variant, and the source is mutated in memory only. A variant counts as a kill only against a clean
baseline (66/0/0) with zero runner guard trips. Results are in `mutants-summary.txt` (`728e0ed8…`).

| Variant | Result |
|---|---|
| **v5 source**, rebuilt to `d28454d6…` | Fails the new R5-1, R5-2 and R5-3 tests (9 cases). These tests therefore detect the original defects. The R5-4 test passes on v5, because the rule already existed. |
| **R5's Y01–Y17** (verbatim patterns) | **Y16 killed by exactly `test_heaptrack_artifact_name_rule`.** Y01, Y02, Y03, Y05, Y07, Y09, Y11, Y12, Y15 and Y17 killed. Y10 survives (R5: equivalent, since `safe_path` already resolves). Y04, Y06, Y08, Y13 and Y14 are pattern-absent, because v6 rewrote those lines. |
| **New Z01–Z08** | **All killed.** They cover: no retraction after a failed save; sealed set before the save; roles not retracted; an interpretable capture without its artifact; a control artifact allowed; retraction keeping entry flags (≈Y06); a restored post-unlock write (≈Y04); several artifacts allowed (≈Y13). Z05 is the equivalent of Y14. |

**Runner artifact (disclosed; not counted).** The first Z07 run aborted with exit 130. It is the same artifact R5
recorded for Y04:

- Z07's restored post-unlock write re-entered the test's final-save fault injector after the adapter had restored the
  default SIGINT handler.
- CPython then re-raised SIGINT against **its own process only**.
- The shell loop stopped there, and Z08 had not run.

Recovery:

- The test's fault now fires only once, so a later write is observed rather than re-faulted.
- No `p6-profiles-test-*` folder was left behind.
- The whole targeted set was re-run from scratch. The recorded run has **no abnormal exits** and **0 trips**.

**Not run:** `test_profile_external.py`; R4's and R3's whole historical matrices (not needed for this delta).

## Probe-only plans (next step, not authorized here)

All plans were generated offline by `profile-adapter-v6/guarded_run.py` (`82aa5e87…`, R5's guard list, logged no-op
offline lock; real flock is out of scope). All are `launched: false`. Budgets are unchanged from v5.

| Plan | Required: entry + probes + children + final | SHA256 |
|---|---|---|
| `plan-probe-perf-one-row` | 1371 s (2 + 390 + 926 + 53) | `3002c9a2…` |
| `plan-probe-strace-one-row` | 1330 s (2 + 390 + 906 + 32) | `189b076b…` |
| `plan-probe-strace-writes-one-row` | 1341 s (2 + 390 + 912 + 37) | `b0d1e755…` |
| `plan-probe-heaptrack-one-row` | 832 s (2 + 325 + 463 + 42) | `90f0242e…` |

**Capture** needs, in its own window:

- `--sidecar profile-adapter-v6/profile-sidecar.json`;
- `--sidecar-sha256 9cadf783f554a22fb6c3bf20c88e9e110ed0b6026f85a45c7dcde898866c3f5d`;
- a coordinator token.

**Analysis** needs, in its own coordinated slot:

- the same `--sidecar-sha256`;
- a `--report-sha256`;
- an `--out` that is disjoint from the capture tree and the provenance folders.

Judge each run by **exit status first**, then by `analysis.json`; `journal_sealed` qualifies the printed summary.
Expected results:

- non-perf modes exit 3;
- one-row perf probably exits 3.

All outputs are functional only.

## Still blocked for interpretation

- **Real host facts:** perf, strace and heaptrack formats, exit status and stderr; `heaptrack_print --version`;
  `kptr_restrict`; perf sample count and size; `perf report` time.
- **Non-perf adequacy criteria:** none by design, pending a separate review of real outputs.
- **Scaled reserves (R4-6):** gzip and `perf report` CPU; headroom checks that cannot see quotas.
- **Go source-to-commit equality** rests on the Phase 7 attestation.

## Exact identities

| Artifact | SHA256 |
|---|---|
| `harness/phase6_profiles.py` (bound in the v6 sidecar) | `032cec6338fb7126a97a1bd28eeb53d428442a598287ef4fe267160b7650ac73` |
| `harness/test_phase6_profiles.py` | `f13eab33b00d98f66dc5b93f7a6df5f89ca0a17b562770a6015fa0bde78bab43` |
| `profile-adapter-v6/profile-sidecar.json` | `9cadf783f554a22fb6c3bf20c88e9e110ed0b6026f85a45c7dcde898866c3f5d` |
| `profile-adapter-v6/source-v5-to-v6.diff` | `3918c461e82fa2461ec0ab4bbd533e64e6381409994d0c10a7ec6140abb0424c` |
| `profile-adapter-v6/hashes.txt` (written after the logs; not self-referential) | `bc99847da7665127f8f3096d0b4e3737ef4282adc351f482137a89fc1c4e7ecf` |

**Unchanged:**

- Bound harness modules:
  - `phase6_workloads` `e2e83fa1…`
  - `profile_external` `a0f0d7a3…`
  - `run` `b280d129…`
  - `manifest` `f65de9a0…`
  - `diagnose` `169de15b…`
  - `cases` `b892e1b5…`
  - `phase3_workloads` `4753b1d9…`
- Other inputs: parent `a47093f4…`, budgets `76a57eca…`, `meter.c` `5d7ce9dc…`.
- Preserved sidecars: v1 `52a3f01c…`, v2 `2392786c…`, v3 `bedc7887…`, v4 `90001fb0…`, v5 `65a3f285…`.

This document is not bound in any sidecar.
