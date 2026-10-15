# Phase 6 review wave r2: W-1/W-2 dictionary correction (Opus)

Reviewer: Opus 5.5 review subagent, 2026-10-14. I did not implement Phase 6 or
this correction. This is a fresh, independent re-review of the fix for my
earlier [decoder review](phase6-review-wave-decoder-opus.md) findings W-1 and
W-2. **It is not the final whole-phase Opus review. It grants no phase or
candidate acceptance.**

**Disposition.** W-1 and W-2 are **closed at source and unit level**. That
closure was measured on the current source and checked causally by mutation.
They are **not closed at native-process level**: R5 is pre-fix, and no successor
binary exists. I found no new Medium-or-higher objection in the scope:
session/decoder ownership, Arrow validation, dictionary ordering and binding,
retry stages and safety bounds. There are two Low evidence and test-quality
notes (N-1, N-2). Neither blocks the fix.

## Identity

Worktree `/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail`, HEAD
`4a812d5b9bc4c40882ab10897a73c3ce1e715ce0` plus the dirty implementation. The
Go oracle is `07af390ab09bda8ac9e427ffc6310077bace0125` (Arrow Go v13.0.0,
goccy/go-json v0.10.0, go1.25.4). The Rust toolchain is 1.96.0
(`ac68faa20 2026-05-25`).

| File | SHA-256 at start (10:10) and end (10:16) |
| --- | --- |
| `src/tail/session.rs` | `2d793026cf4e920a5d821a98855a32f7dee918fadc338e2368e0df5134d58a1e` |
| `src/tail/session/tests.rs` | `67e56654d64eaa0fc496946629825678acf6639dd5376043783f6990700b540d` |
| `src/tail/validation.rs` | `65ebd8fab90a99937b343955affe8deca50c702aa37f72edef6f980f9425d3f6` |
| `src/tail/resources.rs` | `8210efe689760620dd3536e2825a738acad28a51c5f17ac078933e3829d5473b` |
| `src/tail/mod.rs` | `0fd7102d65f863c4fd079c893c51627553e0cb15c3506dba37e42de62fa3c0c7` |
| `src/tail/transport.rs` (separate review) | `09bb4c9d62be39905fee73530b8c7c9ff986ca1afe3f94bd2e7f3716df5348a0` |
| `Cargo.toml` / `Cargo.lock` | `eb55b298…dc0c83c` / `aa35f338…1ebb44` |
| wave `manifest.json` | `8575e5d1407e2f842591b3722489fd39c18682fe3a115ff43d3b22f8fd51db71` |
| `dictionary-wave-w1-w2-correction.json` | `55da67275f94c4df1b8939e1ed9a487190ef2d9f8339b0a23cd79085ce41d035` |

The `session.rs`, `tests.rs`, `validation.rs` and manifest hashes equal the
values recorded in the correction evidence. `src/tail/values.rs` moved during the
review: `037183…` at the start, `50ecf769…` in my scratch copy and `e02d7e63…`
at the end. That is the separate V1/V2 owner, and I did not review it.
`docs/rust-phase6.md` also changed during the review (`27195d…` → `c7176f…`).
I read the earlier version.

Read: `docs/rust-phase6.md`, my prior W-1/W-2 report (`975e88…`), the full
correction evidence JSON, the wave manifest, the IA-1 process evidence, the
decoder-r4 parent verification, and the current `Decoder` (`new`, `end`,
`decode`, `read_dictionary`, resolution/binding, `ipc_dictionary_ids`) together
with `validation::record_batch`/`rowless`. Pinned Go `ResolveFieldDict`,
`Memo.reify`/`GetDictionary` and `newRecord` were traced again from the prior
review's recorded sources (`dict.go` `69c642…`, `file_reader.go` `a7fce0…`).

## Prior objections

### W-1 (Medium): flat-path missing dictionary rendered and continued — **closed (source + unit, measured)**

**Source.** `session.rs:515-530` iterates `dictionary_order` after `Layout::NoRows`
and before the flat/nested choice. For each ID, it first returns the exact
nested-delta text if `nested_deltas` has the ID. Otherwise it returns
`{UNKNOWN_READ}arrow/ipc: no dictionaries found for id={id}` if `dictionaries`
lacks the ID. Both decode paths therefore run only once every schema ID is
present. Arrow's empty-values substitution can no longer be reached, as I
checked each lookup site:

- **Flat record decode:** runs only after the check.
- **Nested resolution:** never consults Arrow's map.
- **Nested-delta piece decode:** uses a keys-only schema with no dictionary fields.
- **`read_dictionary`:** uses `layout.decoding`, whose values are already integer keys.

**`NoRows` exemption (source proof).** `rowless` (`validation.rs:610-624`)
accepts only top-level `Null` columns or field-less `Struct` columns. A
`Dictionary` column at top level fails the match. A dictionary nested in a
`Struct` gives that struct a child, which also fails. So a `NoRows` batch has no
dictionary IDs, and returning it before the check cannot hide a missing
dictionary. This preserves the characterized Go negative-row shape exactly as
before.

**Go equivalence (traced).** For each column, Go calls `loadArray` and then
`ResolveFieldDict`. On a dictionary it calls `GetDictionary(id)` (missing →
`no dictionaries found for id=N`; any nested-ID delta → `delta dict with
unresolved nested dictionary not implemented`). It then recurses into the values'
children at the same field path, before visiting siblings. That is pre-order
with the parent first, and it happens regardless of row count or null keys. The
Rust walk (`session.rs:1025-1042`) records a field's own encoding before its
flatbuffer children. Those children are the value type's children, matching
Go's `pos.Child(i)` paths, so the first ID reached in Rust is the first ID Go
reaches. A nested delta implies presence, so the per-ID order of the two tests
is immaterial. For IA-1 shared parents with different nested IDs, both walks
reach each occurrence's own nested ID along its own path.

**Measured.**

- The current-tree tail suite: every `tail::session` test passes (54 listed),
  including `dictionary_presence_and_order_match_the_go_wave_corpus`, the
  23-case IA-1 worker replay and the 44-case value replay. Diagnostics feature:
  108 passed. The only failure in either run is described under "Commands
  actually executed".
- The pre-fix behaviour on the identical pre-fix decoder (`d86a574f…`, from my
  earlier capture): `flat_missing_null` → `{"a":"x","b":null}\n\n` and `Ok(())`;
  `flat_missing_zero_rows` → `\n` and `Ok(())`. Go gives empty stdout and the
  fatal error for both.
- The saved pre-fix R5 native case
  (`/home/ajs/.cache/pb-phase6/tmp/wave-missing-native-before-r5`) shows the same
  difference through the real process path: Go has empty stdout and
  `…no dictionaries found for id=1`; R5 prints the 20-byte row, then ends on the
  appended `InvalidArgument`. That is one case, in JSON mode.

### W-2 (Low): numeric instead of first-reached missing ID — **closed (source + unit, measured)**

The check iterates `dictionary_order`, built as
`if ids.insert(id) { order.push(id) }` in a single walk (`session.rs:1027-1031`).
This is O(n log n) with no quadratic `Vec::contains`, as recommended. `ids` and
`expected` are the same set and count as before, so initial-dictionary gating
and `end()` are unchanged. `nested_two_missing_order` now reports `id=9`, as Go
does.

Remaining numeric-order iteration is `resolve_dictionaries` over `self.ids`.
After the check, it can fail only on bind-time Arrow index-bounds or cycle
rejections. Go does not validate those, so they are existing safety differences
with no Go diagnostic to match. This is not a W-2 remnant.

### Prior non-objection notes — unchanged

- The IA-1 last-compatible post-order layout and record-time nested binding are
  byte-unchanged outside the three hunks below. My earlier "no objection" stands.
- The nested path still rebuilds and validates nested dictionaries on every
  record. That measurement note stands and is not a gate.
- The `max_dictionary_buffer_bytes` and `max_batch_buffer_bytes` owners can
  still double-count shared buffers.

## Diff since my reviewed snapshot (measured)

`diff` of `session.rs` `d86a574f…` (my prior snapshot) against `2d793026…` has
exactly three hunks:

1. The `dictionary_order: Vec<i64>` field and its construction.
2. The record-time presence/order loop.
3. `ipc_dictionary_ids` returning `(BTreeSet, Vec)`.

`tests.rs` differs from my prior snapshot only by the new wave test (and by the
absence of my cache-only probe modules). No existing assertion, count or corpus
was removed or weakened.

**Still intact.** No other line changed, so the following hold by source
identity: Utf8/LargeUtf8 → Binary/LargeBinary wire decoding with offset and
buffer validation enabled; the wire nullability exceptions; `validate_body`; the
union and FSL checks; Go-order schema conflict checks; the non-native-endian
rejection (`session.rs:424`); verifier depth 128 against `GO_MAX_NESTING = 64`;
DoGet and reader-initialization failures fatal; established EOF, Canceled,
Unavailable or OK → 500 ms reconnect with a fresh decoder; and the
capacity-one worker handoff.

**Safety greps.** `grep` finds no `unsafe`, `catch_unwind`, `AssertUnwindSafe`,
`skip_validation` or `*_unchecked` in production tail code. The single
`unsafe { … with_skip_validation(true) }` is in `values.rs`'s `#[cfg(test)]`
module (oracle reader only), which is unchanged in kind.

## Corpora and fixtures (measured)

- **Wave manifest:** all 44 listed files are present, with no extras, and their
  bytes and SHA-256 values match the manifest. `oracle_source`
  (`ed80a4…`) and `generator` (`5c709b…`) match their recorded hashes.
- **Independence check:** each of the 44 files is **byte-identical to my own
  earlier captures**, in `/home/ajs/.cache/pb-phase6/review-wave-decoder-opus/streams/`
  (streams plus Go stdout/stderr/status). The root's independent regeneration
  agrees with mine.
- **Original corpora:** the manifests are unchanged and match the hashes recorded
  in `central-review-r5-tests.json`, the IA-1 process evidence and the r4 parent
  verification. These are value 44 (`e0c8ba…`), decoder r2 10 (`de581a…`), r3
  30 (`eff31b…`), r4 15 (`d4fa41…`) and IA-1 25 (`d0b417…`). The session tests
  still assert 44, 10, 30 and 23 (IA-1 worker) cases. The 15-case r4 corpus
  remains parent-regeneration evidence plus the generated-stream unit checks.
  It is not replayed from its directory, the same as before.

## New notes

### N-1 — Low (test quality): wave replay stops at the first differing case

`dictionary_presence_and_order_match_the_go_wave_corpus` asserts inside the
case loop, so a regression reports only the first differing case in manifest
order. **Repro (measured mutation):**

- With the presence loop removed, or restricted to the nested path, the first
  failure is `flat_missing_nonnull`: a text-only difference (Arrow
  `Value at position 0 out of bounds…` against Go's `…id=1`).
- The output-changing cases `flat_missing_null` and `flat_missing_zero_rows`
  come later and are never reported.

**Scope:** causality holds, because the test fails for each mutant. The
stdout-difference regressions are demonstrated by the earlier pre-fix capture,
not by this test's output. An optional improvement is to collect all case
mismatches before asserting, as the values wave test does. This does not block
the fix.

### N-2 — Low (evidence binding): correction evidence does not bind the full tested tree

**What is missing.**

- The correction JSON binds `session.rs`, `tests.rs` and `validation.rs` only.
  Its 102/104 test logs, fmt and Clippy runs do not record `transport.rs`,
  `values.rs` or Cargo hashes. Both source files have changed since: transport
  `77dd92…` → `09bb4c…`, values moving.
- `before_fix_native` is a path with no hash and covers one JSON case.
- The 11-case wave process set has no native capture yet, for neither Go A/A
  nor a candidate.

**What I measured instead.** My run binds `transport.rs` `09bb4c…`, and all
session tests pass on it.

**What the successor evidence should do.** It should bind the full tail source
set, Cargo and the binary. It should run all 11 wave cases through
`phase6_dictionary_cases.py` against Go A/A and the fixed candidate, in both
output modes; the unit replay uses `json=true` only. The decoder is
mode-independent, so text mode is a confirmation of the process path, not a
suspected divergence.

## Commands actually executed

**Setup.** Scratch: `rsync` of the worktree, excluding `target/.git/docs`, to
`/home/ajs/.cache/pb-phase6/review-wave-decoder-opus-r2/scratch` at 10:13:29.
The scratch hashes match the table, except `values.rs` (`50ecf769…`).

**Environment.**

- `TMPDIR=GOTMPDIR=/home/ajs/.cache/pb-phase6/tmp/review-wave-decoder-opus-r2`
- `CARGO_TARGET_DIR=…/review-wave-decoder-opus-r2/target`
- `nice -n 10`, `--locked --offline -j 6`

**Results.**

- `cargo test --lib tail::`: 106 passed, 1 failed. The failure is
  `values::tests::values_wave_corpus_matches_go_bytes` (V2 empty interval-list
  `[]` against Go `null`), in the moving values scope and not reviewed here.
- `cargo test --features diagnostics --lib tail::`: 108 passed, 1 failed (the
  same values test).
- Mutants of the scratch `session.rs` only (`mutate.py`, with diffs saved):
  - `no_presence_check` → the wave test fails (`flat_missing_nonnull`).
  - `numeric_order` (iterate `ids`) → the wave test fails
    (`nested_two_missing_order`, `id=7` against `id=9`).
  - `check_after_nested_only` → the wave test fails (`flat_missing_nonnull`).
  - Each mutant leaves every other session test passing.
- `rustfmt --check --edition 2024 src/tail/session.rs`, which follows into
  `tests.rs`: exit 0.
- `cargo clippy --all-targets --features diagnostics -- -D warnings`: one error,
  `needless_borrows_for_generic_args` at `values.rs:2295`, in moving values test
  code. There are no diagnostics in `session.rs` or `tests.rs`.
- Python (`-I`) checked manifest and file hashes and compared them with my prior
  captures. Read-only `grep`/`diff`/`sha256sum` were used.

Logs are under `…/review-wave-decoder-opus-r2/logs/`; their hashes are in
`evidence.json` there.

**Not run.** No Go execution (the existing captures were reused and compared).
No native process, Flight fixture, 291-case or retained replay, and no
benchmark, profiler, soak or pressure run. Nothing ran against R5 or any
successor binary.

**Changes.** No production, test, harness, shared documentation or evidence
file was changed, other than this report. There was no commit, `/tmp`
mutation, agent or thread. The harness temp directory was at EDQUOT, so all
command output went to the cache.

## Out of scope / remaining ledger (unchanged, not parity)

- **Owned elsewhere:** the values V1 (float ties) and V2 (empty interval list)
  work belongs to its owner; transport findings belong to the separate transport
  review.
- **Declared ledgers, not closed by this review:** non-native-endian rejection;
  the receive cap is not a decompressed-buffer cap; malformed row-count and
  negative-length differences; verifier depth 128 against read depth 64; mixed
  malformed-buffer plus missing-ID ordering (Go interleaves load and resolve per
  column, Rust validates the body first); union and decimal producer
  differences; the initial HTTP/2 103 outcome.
- **What this report does not do:** it adds no waiver and does not accept R5. It
  is not the required fresh whole-phase Opus review after the successor build.
