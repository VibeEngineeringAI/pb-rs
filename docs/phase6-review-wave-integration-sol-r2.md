# Phase6 independent integration adapter correction review — Sol r2

**Scoped disposition: I-1 (medium) and I-2 (low) are closed at the exact source
identities below. No new objection in this correction scope.** The fixed adapters
are acceptable for the parent's bounded native smoke. This is a new independent
review round, not an implementation continuation or whole-phase acceptance.
No native compatibility, measurement, performance, or benchmark admission is
supplied by this review.

Review date: 2026-10-14. Only worktree:
`/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail`; HEAD/base:
`4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`, plus dirty implementation.
Pinned Go contract remains `07af390ab09bda8ac9e427ffc6310077bace0125`, Go 1.25.4.
The parent owns R6 native evidence and the separate fresh Opus whole-phase and
measurement reviews. The original 291 and focused 92 configurations and shared
dependency audit are context, not independently rerun or newly credited here.

I read the original `phase6-review-wave-integration-sol.md` in full, including
its original findings and witnesses; the implementation resolutions in full;
all four current adapter/test files; the original review's `adapter_probes.py`;
the original reviewed adapter copies; and all four author before copies and
their actual before/after diffs. I also read the author's validation runners and
record, and the pure capture helper used by the tests. No applicable AGENTS.md
was found in this worktree or the checked ancestor paths. No other worktree,
agents, commits, or source edits were used. The only worktree write is this
document; all generated evidence is under
`/home/ajs/.cache/pb-phase6/review-adapter-integration-r2`.

## Exact reviewed source boundary

Independently hashed before copies match the implementation's recorded before
identities. The dictionary runner, dictionary tests, and H2 runner before copies
also match the original review's retained copies byte for byte. The original
review cache did not retain the H2 test file; its before identity is independently
computed from the author's before copy. Current identities match the four hashes
in the correction brief.

- `harness/phase6_dictionary_cases.py`:
  before `70d0e0ee1109d120b188b3a50a0d85c5cacd25e745d52389fed598978c01ca14`;
  after `2054788d167a7e7e8268148f5e16b2c37fed1cad4331cd8b756f4bc2857b3af1`.
- `harness/test_phase6_dictionary_cases.py`:
  before `a811403c78d0e71a41612486c818f32dc6aca20e1379ac7dd90bfa0b4f21feee`;
  after `5e826bae0fb31172c55a1aa74e38d5158f42554f504fec93dbed06256f272f49`.
- `harness/phase6_h2_limits.py`:
  before `3c8c5326ea1983dfbef59037f4d1488ae5ce8d1d60159a7183bc85c6560e4829`;
  after `8304bb06373fab86d034b819a5121cc4c165fead92cf5c155a4aca3767a214f1`.
- `harness/test_phase6_h2_limits.py`:
  before `978ce2729abf4eacab07afc9767272a32463f304b3215fe27a5ff98d63061789`;
  after `4531e93af2f54f8d856ce145e383641df02deddebfef1bb1710a6fb8e8d77fd6`.

`sources.json`, `before-source/`, `reviewed-source/`, and four `.diff` files in
this review cache retain the exact boundary. An independent AST comparison also
confirmed unchanged dictionary `varint`, `flight_frames`, and `assertions`, and
unchanged H2 `stderr`, `cases`, `assertions`, and `classify`. The H2 production
runner diff adds exactly the two final flag assignments.

## I-1 closure — exact verified reads and original end identities

**Original severity/location:** medium, dictionary adapter's verification and
subsequent filesystem reads, plus main's incomplete source inventory. **Current
correction locations:** `harness/phase6_dictionary_cases.py:24`, `:90`, `:118`,
`:123`, `:181`, `:224`.

`cases()` resolves the corpus directory before any reads, parses the exact
manifest byte string it retains, and reads each declared artifact once for both
length/hash verification and retention. Fixture IPC frames, dictionary stdout
and terminal expectations, and values stdout/errors/panic gates use those
retained `bytes`. The returned `VerifiedCases` preserves list iteration,
indexing, equality, and the existing signature; the snapshot/source metadata is
additive. Its manifest and artifact digests derive from the original retained
bytes, not later disk reads. `main()` imports those identities without rebasing
them and checks all of them at the end, including declared artifacts that case
construction does not consume. Duplicate declarations are rejected.

**Independent causal witness, actually executed:** my new `review.py` copied
the real 25-case IA1 corpus into this review's cache and used a template lookup
hook after successful verification to replace valid
`list_names_same.arrows` values `[7,8]` with `[9,8]`, and matching stdout. The
manifest stayed unchanged. The old runner, executed from the preserved source
in memory with matching synthetic captures, reproduced the original false pass:

```text
before: status=0 complete=true passed=true failures=0
        remaining_contract_passed=25 identities_unchanged=true sources=3
fixed:  status=1 complete=true passed=false failures=0
        remaining_contract_passed=25 identities_unchanged=false sources=104
```

The old witness stdout digest was
`de146f8f1768aa01a9851ed1857f901e9f5548bd38d1ee937608c03e321f6552`.
The fixed runner consumed the original scenarios and original stdout digest
`12c2854788960810824c4c7c4cb5c8e615741f72c7308f4427f1362e45a1a93b`;
the source inventory retained the original IPC/stdout identities, while the end
inventory exposed their drift. All 75 synthetic capture files and all 25 case
entries survived. The contract count staying 25 is appropriate: the captures
meet the original case contract, while the separate provenance gate rejects
overall admission. It does not constitute a false provenance pass.

Further independent main-path probes changed only the manifest after
verification, changed IPC/stdout after the final capture, and changed an added,
validly declared but unused artifact after the final capture. All three returned
status 1, `complete=true`, `passed=false`, `identities_unchanged=false`, with
original case content and all captures preserved. In the manifest-only probe,
the initial digest stayed bound to the parsed bytes despite a later newline
replacement. The unused-artifact probe bound 105 sources rather than 104 and
rejected that artifact's drift too.

These probes passed the corpus through a cache-owned directory symlink. The
fixed report keyed corpus sources by the resolved original directory, with no
alias keys; initial/end key sets matched and included every verified artifact.
The intended boundary is the exact admitted snapshot plus the final drift
check. No filesystem transaction or additional hostile-filesystem prerequisite
is imposed by this disposition.

Independent malformed-manifest probes rejected a duplicate artifact with
`ValueError: duplicate corpus artifact: list_names_same.arrows` and an omitted
stdout declaration with `KeyError: 'list_names_same.go.stdout'`. The omitted file
could not enter through an unverified filesystem fallback. The latter is a
pre-capture input failure, not a reported complete run.

The actually executed corrected test suite additionally covers six replacement
snapshot subcases across IA1, dictionary-wave, and values-wave in JSON/text;
each asserts exactly one read per manifest/artifact, original scenarios,
expectations, retained bytes and identities. Main tests cover six stable runs,
six postverification drift runs, and 26 individual postcapture artifact drift
runs, including manifest, IPC, output, terminal, errors and panic. Existing
preverification rejection still passes. Together these close each part of I-1:
verified-byte rereads, separate manifest baseline, and missing all-artifact end
binding.

## I-2 closure — flags agree with the final case issues

**Original severity/location:** low, H2 main's classify-time flags remaining
true after repeat/executable objections. **Current correction location:**
`harness/phase6_h2_limits.py:144`.

The two flag assignments now follow every repeat assertion and every capture
binary/fixture identity check, before storing the case or counting aggregates.
No subsequent operation appends a case issue. A limitation requires its explicit
ID and no final issues; a control requires no ID and no final issues.

**Independent causal witness, actually executed:** my capture implementation
ran all six H2 cases through main and injected a candidate-repeat collector
timeout. The preserved old runner returned status 1 and aggregate counts zero,
but all six entries still retained their respective success flag. The fixed
runner returned status 1, six failed cases, both aggregates zero, and both flags
false in every entry. A separate wrong-fixture capture-identity probe produced
the same corrected result. Stable execution of all six together returned status
0, two control passes and four verified explicit limitations.

The actual corrected suite independently exercises all six stable cases, 12
Go/candidate repeat objections, and 48 binary/fixture objections across all four
capture roles. Per-case flags and aggregate counts agree in every tested path.
H2-CL-1/H2-CL-2 exclusions and `full_parity_passed=false` remain explicit. Wire
UA is still a separate exact declared difference. No output, stage, status,
request, auth, ticket, authority, config or raw-wait normalization was added.

## Stable contracts and exception reporting

Independent full object equality against the before runner confirmed unchanged
case content for all 90 selected dictionary/value cases:
`(25 IA1 + 11 dictionary-wave + 9 values-wave) × 2 modes`. This compares fixture
frames/actions, argv, datasets, independent expectations and oracle fields,
not just counts. Both runners also generate identical six-case H2 inventories.
`results.json` retains per-selection canonical content hashes. The original
verified corpus source inventories contain 102 IA1, 45 dictionary-wave, and 37
values-wave entries, including each manifest.

I executed two additional exception controls. Deleting a bound artifact after
the last capture raised `FileNotFoundError` at the end check; `finally` retained
all 25 case entries and 75 capture files with `complete=false` and no success
claim. A synthetic `sample` callback wrote the first capture of the second case
then raised a named `RuntimeError`; the report retained the first completed case,
four capture files on disk, `complete=false`, and no success claim. The report
does not invent a completed entry for the interrupted case or convert callback
exceptions into normal collector-return issues. The report state, original
source inventory, exception context and case/role directory provide reasonable
failure context for this existing fail-closed boundary. Pre-capture validation
remains outside the report loop. No new exception-reporting prerequisite is
needed to close I-1/I-2.

## Independently executed validation and controls

Actual command from the named worktree:

```sh
TMPDIR=/home/ajs/.cache/pb-phase6/review-adapter-integration-r2/tmp \
GOTMPDIR=/home/ajs/.cache/pb-phase6/review-adapter-integration-r2/tmp \
CLAUDE_CODE_TMPDIR=/home/ajs/.cache/pb-phase6/review-adapter-integration-r2/tmp \
PYTHONDONTWRITEBYTECODE=1 \
python3 /home/ajs/.cache/pb-phase6/review-adapter-integration-r2/review.py \
  > /home/ajs/.cache/pb-phase6/review-adapter-integration-r2/review.log 2>&1
```

**17 corrected tests passed; zero failures, errors or skips.** I retained 104
new complete synthetic suite reports under `suite-reports/`. The separate new
independent probes above retain their actual main reports and capture JSON under
cache `tmp/`; they include both original failing implementations as causal
controls. These are pure/mock executions, not native captures despite the
adapter's normal report scope string.

Three fresh in-memory source mutations were executed against the corrected
regressions, with worktree source untouched:

- Restore filesystem stdout reads: six assertion failures, zero errors/skips.
- Omit the original corpus source binding: 32 assertion failures, zero
  errors/skips.
- Remove final H2 flag recomputation: 60 assertion failures, zero errors/skips.
  This mutant's SHA256 exactly equals the original reviewed H2 runner hash.

The scripts, mutant sources, detailed logs, retained reports and `results.json`
record these outcomes. They independently reproduce the author's numerical
claims; the claims alone were not used as acceptance. Separately,
`author-validation-readonly-audit.json` confirms the seven artifact hashes in
the author's `validation.json` and its four current source identities. That
read-only hash audit is distinct from my executions.

Process creation/system execution, fork/posix-spawn, `fcntl.flock` and unmocked
`phase6.sample` were guarded; **every guard call count was zero**. All executable
inputs were synthetic byte files. No native executable, fixture, meter, build,
Cargo/Go test, profile, soak, pressure, timing campaign or real flock was run.
Temporary storage used the owned disk cache; Python bytecode writes were disabled.

The original author's 45 protected source/review identities independently match
current files. My before/after inventory covers **310 files**, including the four
reviewed files, production/Cargo inputs, original review and implementation
record, all three selected corpus directories, all contract/reference files,
and existing `docs/evidence/phase6` records. **Zero inventory changes** occurred
between the pure/mock execution's initial and end checks. A later finalization
audit detected a concurrent change only to `docs/evidence/phase6/README.md`, from
`1c5f4edf4fda7dcf583f04a54675f7ce454888597fe848e51e5ce38a3fd4638c` to
`07afd0557a9d35a61abe823c57eec907da986ce401d7637e8557a55baa8efc50`.
I did not edit that file or use its changed content for this disposition. The
reviewed four files, original 45 protected identities, corpora, reference files
and existing JSON evidence stayed unchanged. The execution inventories and
separate final audit, source copies, artifact hashes and report identity are
retained in this cache's validation record. Neither the original review nor the
implementation record was altered.

The prior finding closure map is therefore **I-1 → closed (snapshot consumption,
original manifest/artifact identities, all-artifact end rejection)** and
**I-2 → closed (final per-case flags agree with repeat/executable issues)**.
Production R6 source and previous native evidence remain separate. A bounded
fixed-adapter smoke is parent-owned follow-through; this Python correction does
not require another full 291-case campaign for its scoped acceptance. Fresh
whole-phase/measurement Opus dispositions and benchmark admission are not
supplied or replaced here.
