# Phase6 resource summary review — Opus r2

This is a fresh, independent, executed correction review of the offline R5-4 resource description tool. It checks the
R1 corrections and the later test-portability and isolation slice. It is not a whole-phase, timing, source, budget or
performance acceptance. No timing campaign exists.

I used no native client, fixture, meter, build, profiler, soak, pressure run, real lock or quiet-window token. Nothing
was committed, delegated or sent externally. I edited no source, test, collector, fixture, budget or plan file.

## Reviewed identities

These hashes were identical before and after every probe and test run (`pre.log`, `post.log`).

| Artifact | SHA256 |
|---|---|
| `harness/phase6_resource_summary.py` (500 lines) | `061ef1c3473f8326d3c7d169334269d87702c85c3ebe239361be9fe0b9f925c4` |
| `harness/test_phase6_resource_summary.py` (708 lines, 35 tests) | `f8d970c0417c83b7bd64d45dee9d7cdcde3bc046c2e6bed30b321e2b690290dd` |
| `docs/phase6-resource-summary.md` | `10b1d94a58db9da3cb8267bbb40fc8a97b17c6646cfa441984e07e5dde1fc82e` |
| `tests/fixtures/phase6-resource-summary/go-one-row.json` (8,744 B) | `7c8cf496caf6f0fc0f9a9eaa1e477fe49be5dba5498e3e45fe515b73da8dfa69` |
| `tests/fixtures/phase6-resource-summary/go-idle.json` (8,819 B) | `4e969fa151113ad9a36b486c6b4e8ebf75222df3d64dae7c5ce9715a183db889` |
| `tests/fixtures/phase6-resource-summary/manifest.json` (836 B) | `f599a85108ff691166e76ebd6600dc7c1f0da6bcf787c11e9be62ce381aaf8a6` |
| frozen collector `harness/phase6_workloads.py` | `e2e83fa118c58a96ff55700be88f95f9b8b6479a289a8c59f447f836389fde51` |
| `harness/run.py` / `harness/meter.c` | `b280d129…a076` / `5d7ce9dc…70e9` |
| `harness/test_phase6_workloads.py` | `ac7f43fb…c336` |
| operating decisions / R5 review / plan / resource interpretation | `c107bf84…d9` / `91799ef7…25df` / `8d7bf088…13e1` / `b0f74bfd…9471` |
| R1 review / R1 disposition | `e5062139…f42af` / `7250d7fe…741aa` |

- **Delivery hashes:** the three settled targets and all three fixture files equal
  `portability-v1/delivery-hashes.json`.
- **Fixture manifest:** each fixture's `original_sha256` and `size_bytes` equal the actual bytes.
- **Original implementation:** the cache copy `r1-corrections-v1/phase6_resource_summary.py.original` is
  `f6cea40b…5294829`, matching R1's reviewed identity.
- **Protected R1 artifacts:** `probe.py` `b45ecfd7…f399`, `probe.log` `548c5189…b3f0`, `probes.json` `b1bd07d1…262b`
  and `hashes-before/after.txt` `947504b9…968f` all equal the root disposition. I wrote nothing there.
- **Author caches:** every delivered `r1-corrections-v1` and `portability-v1` artifact still hashes to its delivered
  value, and the listings are unchanged.
- **Bytecode:** no new `.pyc` was written; the existing `harness/__pycache__` entries are unchanged.

During the review, an unrelated untracked file appeared, `docs/phase6-combined-deadline-review-opus-r2.md`. It belongs
to another session; I did not create or read it.

**Environment:** the harness `/tmp` capture hit EDQUOT, as in R1. I redirected all stdout and stderr into
`/home/ajs/.cache/pb-phase6/resource-summary-review-r2/` and read the files back. I did not touch any `/tmp` path:
`TMPDIR`, `GOTMPDIR` and `CLAUDE_CODE_TMPDIR` pointed at that cache. The setup-failure probe names a `/tmp` path that
is only resolved; I confirmed it was never created.

## Method and executed evidence

All of the following live in my review cache.

1. **Discovery run.** `run_tests_r2.py` (`e83c1178…202d`, run with `python3 -I`) installs outer guards on subprocess,
   fork/forkpty, posix_spawn/p, system/popen, the exec and spawn families, and flock/lockf. It denies any access to the
   original R5 sample cache directory. It counts any access to the author's `resource-summary/` cache,
   `pb-phase6/tmp/` and `/tmp/`. It then loads the author's module through `unittest` discovery, appends a sentinel
   test from another module, and snapshots the process APIs, environment and `tempfile.tempdir`.
   - Result `tests-normal.json` (`c5fd4827…ab48`), log `tests-normal.log` (`93e25678…5dfb`).
   - All **35/35** tests pass, plus the sentinel: 0 failures, errors or skips.
   - Import-time no-op: true. SUT, `run` and collector are not imported before setup.
   - APIs, environment and `tempdir` were restored after the run. The sentinel saw the restored state mid-run.
   - Outer guard calls `{}`, SUT `FORBIDDEN_CALLS` `{}`.
   - Denied cache accesses 0, watched accesses 0.
2. **Setup-failure run.** `tests-setupfail.json` (`d6fa0a78…e7b0`) points the cache override at a `/tmp` path.
   `setUpModule` raises after entering its patches. `addModuleCleanup` restored the APIs and environment; the
   following sentinel test passed, and the `/tmp` path was never created.
3. **Documented command.** `PYTHONDONTWRITEBYTECODE=1 python3 harness/test_phase6_resource_summary.py -v`, with only a
   disk cache override: 35 tests OK (`tests-direct.log` `113459ba…c577`).
4. **Collector probes.** `probe_r2.py` (`36091c71…44ef`) is R1's probe adapted to my cache. Its bases are the
   repository fixtures, hash-verified against the manifest; R1's probe is unchanged. It drives the **frozen**
   `campaign()` and real finalizer, mocking only `run_sample`, `quiet_lock` and `host_metadata` (plus admission or
   `summarize_campaign` per case). It describes each campaign with the current tool. As a causal control, it also
   loads the original `f6cea40b` copy.
   - Log `probe_r2.log` `07e8ccfe…5217`, results `probes-r2.json` `3379e631…2411`.
   - Guarded calls `{}`.
5. **Mixed-sign probe.** `probe_r2b.py` (`7fdd9fc0…9baf`) uses the test module's own guarded `mocked_collector`
   (`probe-r2b.json` `77abfa31…10d6`).
6. **CLI check.** Real CLI under `python3 -I` (`cli.log` `69bd772f…c9`):
   - C2 written, exit 0, mode 0600, SHA `8dda67a4…603b`. Byte-identical (`cmp`) to the in-process output.
   - A second write is refused as an overwrite, exit 1.
   - C9 is refused with exit 1 and no file is created.

| Case | Collector output | Current tool | Original `f6cea40b` |
|---|---|---|---|
| C1 finite `one-row` | complete, passed | OK. All 18 R5-4 raw medians equal the retained values; `not_assessed`; no ratio key; CPU flags and quarantine kept | — |
| C2 primary + `empty-batch` | complete, passed | OK. Valid 3; rows median 0; first-record available 0 / missing 3; flag `{null: 3}`; all 4 summaries checked | REFUSED, boolean-only flag |
| C2c observer control + `empty-batch` | complete, passed | OK. `observer_control`, flag `{null: 3}` | REFUSED, same |
| C3 functional `idle`, 2 sessions | complete, passed | OK. No pooled `metrics`; sessions `0`, `1` each `{null: 1}` | REFUSED, same |
| C5 timing A/A 5/30 | passed | OK. `go-a`/`go-b`, role paths for `go-a`, `go-b`, meter | — |
| C7 / C8 exception / window stop | incomplete | OK. `incomplete_valid_subset`, 1 invalid / go 3-1-2-1 | — |
| C9 finalizer failure | no counts, `summaries={}` | REFUSED `expected measured count mismatch`; no output | REFUSED, same |
| C10 lock busy after provenance | no journal | REFUSED `missing retained samples.jsonl; campaign has no retained journal` | raw `FileNotFoundError` |
| C11 invalid `uncaptured_dogets=-1` | complete, not passed | OK. Invalid coverage total −1; `minor_faults` 999999 excluded (max 4828) | REFUSED `invalid integer` |
| C11c **valid** sample with −1 (not collector-emittable) | passed | REFUSED `invalid integer` (fail-closed) | — |
| C12 invalid no-record, flag null | complete, not passed | OK. 2 valid / 1 invalid; 999999 excluded | REFUSED, boolean-only flag |
| C13 `pb-go` declared `/bin/true` (symlink) | passed | OK. Manifest `/bin/true`, bound and selected `/usr/bin/true` | REFUSED, declaration mismatch |
| C13b all three roles declared `…/mid/../link`; link repointed and `mid` removed after collection | passed | OK. All path forms retained; today's path not resolved | REFUSED |
| C13c manifest meter SHA changed, stable hash recomputed | — | REFUSED `embedded role declaration mismatch: meter` | — |
| R6 report symlink + resource-only decoy journal beside the link | — | OK. Journal resolved beside the target; `minor_faults` median 4828 | **wrong journal**, median 666666 |

These synthetic numbers come from retained functional records. They test whether the tool accepts or refuses real
collector output. They are not timing or performance evidence.

## R1 findings

| R1 finding | Response | Verdict |
|---|---|---|
| **RS-R1-1 High** nullable flag | Validator accepts `None` or bool (`:280-283`). Regressions use real R5 `go-idle` shapes for finite primary and control `empty-batch`, functional `idle` sessions and invalid no-record. Other types (`0`, `1`, `"null"`, `[]`, `{}`) are still refused. | **Fixed.** C2, C2c, C3 and C12 pass now and fail on the original. |
| **RS-R1-2 Low** signed invalid derived count | Signed range applies only to `request_evidence.uncaptured_dogets` on `valid=false` records (`:259`). Booleans and fractions are still refused. Valid negatives are still refused (C11c). Invalid metrics never enter statistics. | **Fixed.** See residual R2-1. |
| **RS-R1-3 Low** finalization claim | Doc now says these reports are refused and that no counts are derived (`doc:135-137`). The actual-finalizer regression asserts the absent keys, the refusal and that no output is created. | **Fixed (doc option).** No new derivation, so no relaxation. |
| **RS-R1-4 Info** missing journal | Only `FileNotFoundError` on the journal maps to one concise `ValueError` (`:459-462`); the doc sentence was added. | **Fixed.** C10 and CLI both checked. |
| **RS-R1-5 Info** declared-path equality | Binding is SHA-only against the captured bound declaration (`:183-185`). `role_path_provenance` keeps the manifest-declared, bound-declared and selected paths. Historical declared paths are never resolved. | **Fixed.** C13, C13b and C13c run. Raw size is not compared, which also matches the collector's `declared_identity`. |
| **RS-R1-6 Info** unresolved sibling | Journal path is `resolved_path.with_name('samples.jsonl')` from the same bounded read (`:455-456`). The reread compares the path, resolved path, size and SHA, so a symlink repoint is still refused. | **Fixed.** R6 decoy probe passes; original reads the decoy. |

No correction relaxes validity or upgrades absolutes:

- Every other counter is still unsigned.
- `passed=true` with invalid records is still refused.
- `acceptance` is always `not_assessed`.
- No ratio, budget or admission key is emitted.
- Source quarantine is copied verbatim.

The new path provenance and the C10 message are descriptive only. They add no collector, schema, manifest or campaign
prerequisite.

## Portability and isolation slice

| Claim | Independently verified |
|---|---|
| Fixtures are verbatim retained R5 artifacts, hash- and size-checked before parsing | Yes. The bytes match the manifest and the R1 bases. `load_functional_bases` refuses on mismatch before `strict_json`. |
| `FIXTURE_ROOT` is derived from `__file__`; no cache source or fallback | Yes. All 35 pass with the original sample directory denied and zero access attempts. |
| User-home cache with optional override, outside `/tmp` | Yes. The override was honoured (`sut_cache` = my cache). A `/tmp` override is refused before `mkdir`. |
| Importing during discovery changes nothing | Yes. API identities, environment, `tempdir` and `dont_write_bytecode` are equal after import; nothing from the SUT or collector is imported. |
| Guards are scoped to the module and cleaned up, including after setup failure | Yes. Both runs restored state, and the sentinel in another module observed it. |
| No stub leaks through modules imported under the guards | Yes, by source inspection plus the sentinel. `run.py`, `phase6_workloads.py` and `cases.py` import modules, not guarded names. They look up `subprocess.Popen` and `os.posix_spawn` at call time, so the guarded imports keep no stub reference. `run` and `phase6_workloads` remain in `sys.modules` afterwards, which is normal. |
| No production change in this slice | Yes. The implementation SHA is the same as in the R1-corrections delivery. |

Remaining host dependencies are read-only and conventional:

- `/bin/true` bytes, used as an identity fixture and never executed;
- the repository's predeclared budget file, read by the frozen collector.

## New observations

### R2-1 — Low: mixed-sign invalid `uncaptured_dogets` coverage is a net sum

`group_summary` reports `total=sum(values)` for each coverage field (`:336-337`). Since RS-R1-2, the invalid population
can mix signs for `request_evidence.uncaptured_dogets`:

- a negative value means captured requests exceeded the exact summary;
- a positive value means requests went uncaptured.

Probe r2b put measured invalid records with raw values `[-1, 1, 1]` and `[1, -1, -1]` in one label each. The tool
reported totals of `1` and `-1`. A pair `[-1, +1]` would report `0`. That reads as neither a shortfall nor an excess,
and `invalid_records` does not repeat the per-record value. The doc's "not interpreted as a negative loss count" is
true for a single record but does not cover a net total.

Impact is confined to invalid-population coverage. Statistics, valid coverage and acceptance are unaffected. The
"captured DoGet count exceeds exact summary" error still appears per record in `invalid_records`.

**Disposition (optional, non-blocking):** for this one field, also report signed minimum/maximum or
negative/positive counts, or set `total` to null when signs are mixed. Alternatively, add one doc sentence saying the
invalid total is a signed net.

### R2-2 — Informational: `source_flags` tallies only source-valid samples

`source_flags` counts only valid samples (`:338-342`), so the null flag of an invalid no-record sample appears
nowhere in the output (C12 shows `{false: 2}`). The doc (`:85-87`) lists invalid no-record samples among null-flag
cases and then says null "remains a separate `source_flags` category". That could be read as tallying invalid nulls.
A one-word clarification ("source-valid") would close it. This is behaviourally correct and consistent with
`statistics_population`.

### R2-3 — Informational: hand-crafted nested type errors escape as tracebacks

If a report has a non-dict nested manifest field but its stable hash is recomputed (for example
`fixture_build.binary=[]`), the tool raises `AttributeError`. `main` catches only `OSError` and `ValueError`. The
process still exits 1 and creates no output, so it fails closed. The frozen collector cannot produce this shape. The
behaviour predates R1 and is not a regression.

## Disposition

**Accepted for the stated scope: an offline, descriptive-only R5-4 resource description tool, at the settled hashes
above.**

- RS-R1-1 to RS-R1-6 are fixed or properly documented, with causal regressions that are executed, collector-shaped and
  fail on the original.
- The portability and isolation claims hold under independent discovery, setup-failure and denied-cache runs.
- R2-1 is a low, optional clarity improvement; R2-2 and R2-3 are informational. None blocks use.
- I found no regression, no relaxed validity rule, no upgraded absolute and no new collector, schema, budget or
  campaign prerequisite.

This acceptance covers only the offline reader's acceptance, refusal and extraction behaviour on collector-v2 output.
It grants no timing, performance, budget, source, quiescence or whole-phase acceptance. Collector schema 2 still has
no journal digest, so resource-only journal changes remain unauthenticated, as the doc states. Real timing campaigns,
observer controls and quiet-window admission remain separate gates.
