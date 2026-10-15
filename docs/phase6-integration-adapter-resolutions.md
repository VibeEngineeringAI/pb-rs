# Phase6 integration adapter implementation resolutions

Implementation record, 2026-10-14, for I-1/I-2 in
`docs/phase6-review-wave-integration-sol.md`, which was read in full and left
unchanged. This records scoped implementation and bounded pure/mock validation;
fresh independent review follows. It does not supply a review disposition or
new native compatibility acceptance.

Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail`.
Owned changes: `harness/phase6_dictionary_cases.py`,
`harness/test_phase6_dictionary_cases.py`, `harness/phase6_h2_limits.py`,
`harness/test_phase6_h2_limits.py`, and this new record. Cache:
`/home/ajs/.cache/pb-phase6/adapter-integration-fixes`.

**I-1 implementation.** `cases()` retains the initially parsed manifest bytes
and each manifest-declared artifact's exact verified bytes. Fixture frames,
stdout expectations, terminal expectations, and the values `.errors`/`.panic`
gate consume that snapshot. Undeclared inputs cannot enter through a later
filesystem read; duplicate artifact declarations are rejected. The returned
`VerifiedCases` list subclass carries `verified_bytes` and `sources`, with
identities derived from the retained bytes. The existing signature
`cases(corpus=None, count=25, mode='json', values=False)` and `main(argv=None)`
caller shapes are preserved. Existing list iteration/indexing and case content
are preserved; snapshot metadata is additive.

`main()` incorporates those original corpus identities directly into its
source inventory. It does not reread the manifest or artifacts to establish a
new baseline. End checks hash every initially bound source, including the
manifest and all declared artifacts, and require equality before `passed=true`.
Conforming synthetic captures may retain their case contract counts while
corpus drift makes the complete report fail with `identities_unchanged=false`.

The regressions cover IA1 (25), dictionary-wave (11), and values-wave (9), each
in JSON and text mode. A replacement hook runs at template lookup, after all
artifact verification has succeeded and before scenario construction. Every
artifact is read once during `cases()`, and the produced scenarios, independent
expectations, retained bytes, and identities equal the original inventory.
IA1 includes the review witness's valid `[7,8]` to `[9,8]` IPC/stdout replacement
on a cache-owned copy. Changed manifest, output, terminal, errors and panic
bytes are also exercised. The existing preverification rejection remains.

Main-path tests additionally replace corpus inputs after verification, or
replace each artifact type individually after the last synthetic capture has
been written. They assert original scenario/output admission, complete initial
and end source maps, unchanged synthetic executable hashes, and failing overall
status for drift. Stable corpora pass through the same main path. There are six
stable and 32 drift main-path runs, plus six snapshot replacement subcases.

**I-2 implementation.** After all repeat assertions and capture executable
identity objections have been appended, `main()` recomputes both
`declared_limitation_verified` and `control_remaining_contract_passed` from the
final issue list. Tests exercise all six H2 cases: two controls and both declared
limitations in both modes. Six conforming main-path runs preserve the expected
flags. Twelve Go/candidate repeat timeout controls and 48 binary/fixture hash
controls across all four capture roles reject the case and clear both flags.
Per-case booleans and aggregate counts agree; exclusions remain explicit.

**Actual validation.** Final run: **17 tests passed, zero failures, errors or
skips**. The seven added test methods include the main-path subcases above.
`tests.json`, `tests.log`, and `mock-main-reports/` retain the result and 104
complete synthetic main reports. Temporary capture directories are cleaned up;
these reports are test artifacts, not native capture evidence.

Three in-memory negative controls were actually executed against the new
regressions. Reintroducing corpus stdout rereads caused six assertion failures;
omitting original corpus identities caused 32; removing H2 flag recomputation
caused 60. Each control produced zero test errors/skips and was detected.
`negative-controls.json`, the three named mutant `.py` copies and `.log` files
retain their definitions, hashes and outcomes. The H2 mutant exactly reproduces
the reviewed adapter hash. Worktree source was never mutated for these controls.

Both runners guard subprocess creation/system execution, fork/posix spawn,
`fcntl.flock`, and unmocked `phase6.sample`; all guarded call counts are zero.
All Go/candidate/fixture executables are synthetic bytes and never executed.
`TMPDIR`, `GOTMPDIR`, and `CLAUDE_CODE_TMPDIR` all point to the cache's disk-backed
`tmp/`; Python bytecode writes are disabled. Actual commands:

```sh
export TMPDIR=/home/ajs/.cache/pb-phase6/adapter-integration-fixes/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
export PYTHONDONTWRITEBYTECODE=1
python3 /home/ajs/.cache/pb-phase6/adapter-integration-fixes/run_tests.py
python3 /home/ajs/.cache/pb-phase6/adapter-integration-fixes/run_negative_controls.py
```

The initial test run exposed a new test-helper mistake: a supplied function was
treated as a mock with `call_count`. Switching to a mock `side_effect` corrected
that assertion. The failed run is preserved in `initial-test-run/`. A subsequent
passing run that replaced inputs after the last verification read, and its
negative controls, remain in `last-read-boundary-run/`. The final root-cache
results use the stronger hook after successful verification described above.

Final SHA256 source identities:

- `harness/phase6_dictionary_cases.py`:
  `2054788d167a7e7e8268148f5e16b2c37fed1cad4331cd8b756f4bc2857b3af1`.
- `harness/test_phase6_dictionary_cases.py`:
  `5e826bae0fb31172c55a1aa74e38d5158f42554f504fec93dbed06256f272f49`.
- `harness/phase6_h2_limits.py`:
  `8304bb06373fab86d034b819a5121cc4c165fead92cf5c155a4aca3767a214f1`.
- `harness/test_phase6_h2_limits.py`:
  `4531e93af2f54f8d856ce145e383641df02deddebfef1bb1710a6fb8e8d77fd6`.

`owned-source-hashes.json` retains both before/after identities;
`before-source/` preserves the original four files; `implementation.diff` records
the scoped changes; `validation.json` binds the final source and validation
artifact hashes. `before.json`, `after.json`, and `protected-source-check.json`
record zero changes among 45 protected inputs: Cargo manifests/lock, all
inventoried production Rust files, and the independent integration review.

Existing R6 source, pinned Go contract, original corpus/reference files and
historical evidence were preserved. The review's `adapter_probes.py` was read
only. No native replay, rebuild, Cargo/Go test/build, timing, profiling, pressure,
performance lock, commit, agent delegation, or unrelated-suite work was run.
The parent's original291/focused92 R6 results and ongoing retained/build-profile
work remain separate evidence. These corrections address provenance and report
consistency; they do not allege observed historical native corruption.
