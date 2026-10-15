# Phase 6 review: combined phase-3 deadline normalization (Opus, round 2, executed)

Scope: a fresh, independent, **executed** review of the off-tree combined patch
for the Phase 6 base. It joins two proposals:

- the Phase 6 lookahead rule (`p3-deadline-text-empty-array-lookahead` →
  `failed to read response body`), from
  [the proposal](phase6-lookahead-deadline-proposal.md) and
  [R1](phase6-lookahead-deadline-review-opus-r1.md)
- the Phase 7 body-stall rule (`p3-deadline-body-stall-text` →
  `failed to stream response body`), from Phase 7's
  `docs/phase7-body-deadline-proposal.md` and `docs/phase7-review-body-deadline-r1.md`,
  which I read only

The artifact is `/home/ajs/.cache/pb-phase7/combined-body-lookahead-r2`. I
treated it as read-only.

Pins: base `4a812d5b`, Go oracle `07af390ab09bda8ac9e427ffc6310077bace0125`,
go1.25.4. **Out of scope:**

- the Phase 4 `p4-deadline-header` separate-header timeout question
- the Phase 7 variant's full-suite review, which belongs to the concurrent Sol
  review; I ran only light checks on it, listed below

This review is not phase acceptance, promotion, performance or release
evidence. **No fresh 60-second run was made.**

## Verdict

**Accepted (scoped) for the Phase 6 variant. No blocking findings.**

- **Accepted:** patch `00f14a8abb88858ce8ecd7160292f0c2d835210743295f7e3cf8f456154f8795`,
  runner `af783ab65a14512f0df07f81948e03dd082b103a78ed460554c5981b909d95f2`,
  test `812e96091659d8f8e4fabdf4bdeae537d4f2dc153f41a2cb0faa59289bf2e434`.
- **What was verified:**
  - The source change is exactly two exact name→message entries.
  - The deliberate reversal of an old negative covers exactly the body-stall
    stream-context assertion.
  - All boundary negatives hold.
  - The full 37-test suite passes normally and under `-O` against **Phase 6**
    dependencies and the frozen fixture.
  - All 14 archived actual captures reduce cleanly. The live runner reproduces
    the original failures.
- **Promotion still requires** these gates, which this review does not
  satisfy:
  - the edit window or shared-runner handshake: `runner_sha256` changes from
    `7f8deaf0…` to `af783ab6…`
  - the Sol review of the Phase 7 variant
  - a focused actual candidate replay after promotion, with the original R6
    683/1 report kept unchanged
- The rule is symmetric by design. A Rust candidate that prints the Go-valid
  bare form now passes both cases (see F5).

## Method

- **Shell:** every call hit EDQUOT on capture. All output went to cache files,
  which I then read. Nothing touched `/tmp`.
  `TMPDIR`/`GOTMPDIR`/`CLAUDE_CODE_TMPDIR` were set to
  `/home/ajs/.cache/pb-phase6/tmp`, and `PYTHONDONTWRITEBYTECODE=1`.
- **Interpreter:** `/usr/bin/python3` 3.14.7, sha256 `d78f9cf7…`.
- **Writes:** this file and `/home/ajs/.cache/pb-phase6/combined-deadline-review-opus-r2/`
  only. I made no edits to live source, tests, corpus or manifests, and no
  commits.
- **My scripts** are in that cache, under `scripts/`:

| Script | SHA-256 | Purpose |
| --- | --- | --- |
| `stage_verify.py` | `870f5826…` | Staging, two appliers, LF-only regeneration, scope, cross-variant checks |
| `run_suite.py` | `22319fcb…` | Suite runner with an import-path check and an audit hook that records every process launch |
| `mutants.py` | `862f76fc…` | Causal mutants |
| `reduce.py` | `7ca3ff38…` | Capture reduction with explicit raising checks |
| `phase7_variant.py` | `83174943…` | Phase 7 variant apply and regeneration checks |

## Executed results

### 1. Patch identity and exact application

- **Input hashes:**
  - Live `harness/phase3.py` is `7f8deaf0…` and `test_phase3.py` is
    `f11f07cd…`. Both equal the manifest's "before" values and the artifact's
    `phase6/before` copies.
  - The patch hash equals `00f14a8a…`.
- **Application:** on my own copies:
  - `git apply --check --verbose`, then `git apply --verbose`: exit 0, "Applied
    … cleanly", no offset or fuzz.
  - GNU `patch --dry-run -p1 --verbose` reports hunk 1 at 1475 and test hunks
    at 352, 364 and 481, all with no offset.
  - The applied bytes are identical to `phase6/staged` and to the manifest's
    after-hashes.
- **Regeneration:** a diff regenerated from bytes with **LF-only** splitting is
  byte-identical to the artifact. This closes R1-F1.
  - `phase3.py` contains no Unicode line separators.
  - `test_phase3.py` contains one U+2028.

### 2. Scope

**Source delta against live.** Exactly two inserted lines inside the existing
`bare_context` dict:

- the lookahead entry after line 1477
- the body entry after line 1479

Against the v2 proposal, the only addition is the body line. The opt-in, the
exact equality including LF, the canonical form and the redirect rule are
untouched.

**Test methods.**

| Change | Methods |
| --- | --- |
| Added | `test_legacy_bare_deadline_contexts_remain_case_specific` (renamed), both lookahead tests and both body tests |
| Removed | only the old `test_bare_deadline_cause_is_limited_to_two_exact_text_messages` |
| Changed | no other existing method |

Each scoped method is byte-identical to its owned proposal and to the Phase 7
variant.

**Legacy test diff.** The unified diff against live and against v2 shows:

- lookahead is negative for **both** legacy contexts
- the other legacy case is now an added identity negative
- `body-stall-text` stays negative for `failed to read error response body`,
  and is reversed **only** for the stream context

No other v2 negative was lost.

**Cross-variant check.** The Phase 6 and Phase 7 bases differ only in the
Phase 7 interactive guards:

- `deferred_boundaries()` returns `[]` and `historical_deferred_boundaries`
  holds the old checks
- `boundaries_status` text
- two `CorpusTests`/`ComparisonTests` methods

Those differences are identical before and after the patch. So each base keeps
its own guards, and no whole file was copied across.

### 3. Full shared suite (Phase 6 dependencies, frozen fixture)

**Staging.** `work/stage/harness` holds the applied runner and test, plus
**copies** of the Phase 6 worktree's dependencies, not symlinks. Hashes:

| File | SHA-256 |
| --- | --- |
| `phase1.py` | `e0216062…` |
| `phase2.py` | `e1f8626b…` |
| `run.py` | `b280d129…` |
| `cases.py` | `b892e1b5…` |
| `phase3_workloads.py` | `4753b1d9…` |

The staging also holds a copy of `contracts/`. `bin/pb-go` points to
`baselines/pb-go`, `fae8411f…`. `PB_PHASE3_FIXTURE` is
`/home/ajs/.cache/pb-phase6/tmp/fx/pb-fixture-r4`, `8237e34e…`. `PYTHONPATH`
was unset.

**Import check.** The runner verified the resolved files of `phase3`,
`phase1`, `phase2`, `run`, `cases` and `test_phase3`. All were the staged
copies. The test module was loaded from the staged path, and its `phase3` is
the verified module.

**Results:**

| Run | Tests | Fail | Error | Skip | Harness bytes unchanged |
| --- | --- | --- | --- | --- | --- |
| normal (`full-stage.json`) | 37 | 0 | 0 | 0 | yes |
| `-O` (`full-stage-O.json`) | 37 | 0 | 0 | 0 | yes |
| unpatched live control, without `GoOracleTests` (`control-live-nogo.json`) | 32 | 0 | 0 | 0 | yes |

**Process launches per full run**, recorded by the audit hook:

| Process | Count |
| --- | --- |
| Pinned Go `sql run` | 12 |
| Pinned Go `--version` | 2 |
| Frozen Go fixture (scenario and `--capabilities`) | 2 |
| `python3 -I phase3.py serve` fixtures | 12 |
| `/bin/sh` runner tests | 6 |

The Go-oracle test's six cases each run twice, because `reference_runs=2`.
That makes 12 short Go processes, not six (see F2). There were no builds, Rust
processes, 60 s runs, locks, profilers or benchmarks. Wall times are
functional only.

### 4. Rule-absent control and causal mutants

All of these ran on `DeadlineTierTests` only (10 tests), normally and under
`-O`, with no process launches. Every result was a FAIL, never an ERROR, and
both modes gave identical results.

| Mutant | Failing tests |
| --- | --- |
| live runner, rule absent | the 4 new tests |
| drop lookahead entry | the 2 lookahead tests |
| drop body entry | the 2 body tests |
| pooled context within mapped cases | lookahead boundary, body boundary, legacy |
| case-agnostic any-known-context | lookahead boundary, body boundary, legacy |
| `rstrip` LF/CRLF tolerance | body (no LF, CRLF), lookahead boundary |
| `startswith` prefix match | body (×4 subtests), lookahead boundary, legacy |
| accept bare `request canceled` | body, lookahead boundary, legacy |
| bare map checked before the opt-in | body boundary, lookahead boundary |
| lookahead keyed to the stream context | 4 tests |
| body keyed to the read context | 3 tests |
| extra `spans-redirects` read entry | lookahead boundary |
| extra `header-stall-text` entry | body boundary |
| canonical drops the context | 5 tests |
| extra `body-stall-json` entry | **survives**: an equivalent mutant (F3) |

### 5. Re-reduction of archived actual captures

`reduce.py` loads the live runner (`7f8deaf0…`) and the combined runner
(`af783ab6…`) as separate modules. It checks the corpus digest
`b83d5abd…`, which is the same for both and equals every report. It checks
that both case definitions are identical. It verifies raw `stderr.bin` and
`stdout.bin` digests. Process launches were forbidden. Its 166 explicit raising
checks gave identical records normally and under `-O`. All 126 artifact files
were re-hashed afterwards and were unchanged.

**Lookahead, nine captures.**

| Source | Captures |
| --- | --- |
| R4 and R5 reference | canceled + suffix (124 B) |
| R4 and R5 repeat | context + suffix (123 B) |
| R6 reference | **bare** (63 B) |
| R6 repeat | canceled + suffix |
| R4, R5 and R6 Rust candidates | context + suffix |

- Every capture has exit 1, empty stdout, one `POST /api/v1/query` with a 98 B
  body, and a wall time of 60.0–60.13 s.
- Report hashes:

  | Report | SHA-256 | Counts |
  | --- | --- | --- |
  | R4 | `c5c4149e…` | 684/0 |
  | R5 | `c4125274…` | 684/0 |
  | R6 | `51183fb7…` | 683/1 |

  All three have runner `7f8deaf0…` and reference `fae8411f…`.
- Bare appears once in six Go captures. That is an observation, not
  replication.

**Body stall, five captures.**

| Source | Capture |
| --- | --- |
| release4 reference | **bare** (65 B) |
| release4 repeat | canceled + suffix |
| release4 candidate | context + suffix |
| oracle1 reference | canceled + suffix |
| oracle1 repeat | context + suffix |

- Every capture has exit 1, the same 34-byte partial stdout and one POST.
- Release4 report: `a4439320…`, 683/1. Oracle1 report: `013dd5c7…`, a
  reference check, 1/0. Both have runner `39d16e2b…`.

**Reduction results:**

- **Live runner.** Its recomputed issues equal the original report entries:
  - R6: `reference unstable: stderr differs at byte 37: candidate 114 bytes, reference 63 bytes`
    and the same candidate string
  - release4: the byte-39, 116/65 pair, plus the bare reference's original
    `stderr_exact` expectation issue

  It returns non-empty results for 16 of the 72 lookahead pairs and 8 of the 20
  body pairs. These are exactly the pairs that include a bare capture.
- **Combined runner.**
  - Every capture has empty independent expectation issues.
  - All non-self ordered pairs, both Go/Go A/A and Go/Rust, are empty.
  - Each case collapses to one canonical form and one manifest stderr digest.
- **Rejection controls on actual data.** Each control mutated the Rust
  candidate, or the bare Go reference for the stderr A/A side. All 23 per case
  were rejected:
  - **Stderr:** bare canceled, no LF, CRLF, extra line, preceding line,
    awaiting-headers suffix, header branch, JSON-wrapped, and both wrong-phase
    contexts. Each is rejected by the differential **and** on the A/A reference
    side, and none canonicalizes.
  - **Independent and differential:** exit 0 or 2, stdout appended or
    truncated, walls of 58 000 and 71 000 ms.
  - **Independent only:** trace empty, doubled or unexpected.
  - **Differential only:** request body, contract header, extra request and
    `config_after`.
- **Lookahead stderr gate.** Its independent stderr check is the `stderr_has`
  prefix only, as R1 found. Most stderr mutations there are caught only by the
  differential. Wrong-phase and header-branch errors are also caught
  independently.
- **Cross-case.** All 14 captures fail when evaluated as the other case.

### 6. Source rationale (re-verified)

- **pb sources.** `git show 07af390:{cmd/query.go,pkg/http/http.go,pkg/http/refresh.go}`
  equals the worktree files byte for byte: `8952eea5…`, `47b59e5e…`,
  `39f767a8…`.
- **Go client.** Go 1.25.4 `client.go` is `0ef2c2f5…`.
  - `cloudRefreshTransport` makes `knownRoundTripperImpl` false.
  - `setRequestCancel` therefore installs both a `WithDeadline` context and the
    timer goroutine, which runs `timedOut.Store(true)` and then `doCancel()`.
  - `cancelTimerBody.Read` adds the suffix only if `reqDidTimeout()` is true.
- **pb branches.** `streamSQLTextResponse` gives:
  - `failed to read response body` for prefix and lookahead failures. The
    empty-array `ReadByte` lookahead blocks after `[]`.
  - `failed to stream response body` for `io.Copy` failures.
- **Conclusion.** Bare `request canceled` is unreachable, and both bare
  observations fit this race.

### 7. Phase 7 variant (light checks only)

- Patch `1d5fe946…` against the live Phase 7 sources (`39d16e2b…`,
  `0bea853f…`):
  - `git apply --check` and `git apply` succeed cleanly
  - the applied bytes equal `phase7/staged` (`3fc89e89…`, `ef1a47dc…`)
  - the LF-only regeneration is byte-identical
- Pure `DeadlineTierTests` pass 10/10 normally and under `-O`, with copies of
  the Phase 7 dependencies.
- I did not run the Phase 7 full suite.

### 8. Root evidence cross-check

- `full-suite-r2.json` records four runs with exit 0.
- The logs show `IMPORTED_RUNNER` and `IMPORTED_TEST` at the staged paths and
  37 OK, and their hashes match the record.
- The initial failed run is preserved byte-identically in Phase 7's docs as
  `preparation-failed-phase7-normal.log` (`9cbf8bfe…`). It failed because
  `test_phase3` was imported from the live Phase 7 tree (33 tests) and the
  fixture started without its dependencies.

## Findings

### F1 (low, provenance; resolved by this review's run): the root "phase6" full suite was not a Phase 6 environment

`phase6/staged/harness` symlinks these files to the **Phase 7** worktree:
`phase1.py` (`26ca8380…`, versus Phase 6's `e0216062…`), `phase2.py`
(`66568cd9…` versus `e1f8626b…`), `run.py` and `contracts`.
`run-full-r2.py` also puts Phase 7's `harness` on `PYTHONPATH`, and passes
`PB_PHASE3_FIXTURE` as Phase 7's `bin/pb-fixture` (`fa8b4711…`), not the frozen
Phase 6 fixture `8237e34e…`.

The `inputs` record does list those paths, but the label "phase6-normal-r2"
overstates what ran. The `-I` fixture children also wrote `phase1`/`phase2`
`.pyc` files into the artifact. `-I` ignores `PYTHONDONTWRITEBYTECODE`. These
files are unhashed byproducts.

**Impact:** none on the outcome. My Phase 6-dependency run with the frozen
fixture gives 37/37 normally and under `-O`. Cite this review's run, not the
root log, as the Phase 6 suite evidence.

### F2 (info, disclosure): the Go-oracle test runs 12 Go query processes, not six

`GoOracleTests` runs six cases with `reference_runs=2`, plus two `--version`
probes. Both of my full runs therefore launched 24 short Go `sql run` processes
and 4 version probes in total. These are inherent to the authorized test. Each
is a fixture-local gate case, not a 60 s run.

### F3 (info, test strength): no test pins the exact `bare_context` key set

An extra `p3-deadline-body-stall-json` → `error decoding JSON response` entry
survives every test. It is **equivalent**: that case's stderr is a JSON
document (`stderr_json` pin), so the text form `Error: …\n` can never match.

Every non-equivalent broadening I tried was killed, including the redirect and
header-stall entries. The key set is protected by the verified two-line source
delta. No change is required.

### F4 (info, tooling): the compose script splits lines conditionally

`compose-body-lookahead.py` (`4efbfd0a…`) uses `str.splitlines` unless the
text contains U+2028. Any other Unicode separator, such as `\x0c`, `\x1c`–`\x1e`,
`\x85` or ` `, would misnumber hunks. Neither current file triggers this,
and both regenerations are byte-exact. A future regeneration should split on LF
unconditionally.

### F5 (info, doctrine; wording to carry forward)

The symmetric acceptance is intentional and Go-valid. After the combined
patch:

- no phase-3 text-mode body-read deadline case independently or differentially
  requires the Rust body-read suffix
- only `p3-deadline-body-stall-json` still pins it

Phase 7's proposal says "lookahead has no normalization exception in this
patch, so its bare and suffixed forms still fail both differential and A/A
comparisons". That is true only of the body-only patch. It is **false for this
combined patch** and must not be carried into the combined record.

## Unknowns and limits

- No fresh 60 s Go or Rust run was made. The bare branch in each case rests on
  one actual capture plus pinned source.
- I did not review the Phase 7 variant beyond §7.
- **Other harness modules (grep only).** `phase4`, `phase5`, `phase6`,
  `diagnose` and `test_harness` import `phase3`. Only `phase5.normalize_timeout`
  (`phase5.py:544-553`) delegates to `p3.normalize_timeout`, and it passes
  `p5-*` case names. The two new keys are `p3-*` names, so they cannot fire
  there. No other module names either case. I did not run those suites.
- I made no promotion, handshake or replay claim.
