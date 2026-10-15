# Phase 6 review wave: decoder, lifecycle and ownership (Opus)

Reviewer: Opus 5.5 review subagent, 2026-10-14. I did not implement Phase 6.
This is a focused source review of the settled nested-dictionary snapshot,
including the IA-1 continuation. **It is not the required final whole-phase Opus
review and grants no phase acceptance.**

**Disposition: source-scoped acceptance is withheld for W-1 (Medium).** It is one
new output/terminal mismatch outside every existing corpus, measured against
pinned Go. A minimal fix is prototyped below and passes in a cache copy. The
IA-1 continuation itself (last-compatible-occurrence binding, deferred nested
placeholders, nested deltas, replacement, EOF) has **no objection**. Two new
probes outside the owner corpus independently match Go. W-2 is a Low,
text-only diagnostic difference fixed by the same change. No other new
objection was found in the inspected paths.

## Identity

Worktree `/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail`, HEAD
`4a812d5b9bc4c40882ab10897a73c3ce1e715ce0` plus the dirty implementation. Go
oracle `07af390ab09bda8ac9e427ffc6310077bace0125` with Arrow Go v13.0.0, go1.25.4.
Hashes were identical at the start and the end of the review:

| File | SHA-256 |
| --- | --- |
| `src/tail/session.rs` | `d86a574fb7eb9500bffaf3fc0f0d0f2507d1390ade35598baed347371409866f` |
| `src/tail/session/tests.rs` | `7dad715f4b67e782d7e44745231b6d1510b32bd689ab800a3946d261ca9c07fe` |
| `src/tail/validation.rs` | `65ebd8fab90a99937b343955affe8deca50c702aa37f72edef6f980f9425d3f6` |
| `src/tail/values.rs` | `037183a7939fc11d3921c36d086fad023d9a6c0004839096b28ab140cd56ea6b` |
| `src/tail/transport.rs` | `77dd92bb0bf34c620bcb2ca259347f0d6b27d83d72593a76e912984858165240` |
| `src/tail/resources.rs` | `8210efe689760620dd3536e2825a738acad28a51c5f17ac078933e3829d5473b` |
| `src/tail/mod.rs` | `0fd7102d65f863c4fd079c893c51627553e0cb15c3506dba37e42de62fa3c0c7` |
| `Cargo.toml` / `Cargo.lock` | `eb55b298…0c83c` / `aa35f338…1ebb44` |

Read: the cached roadmap (`phase6-final.md`, `234229…52c9`), `docs/rust-phase6.md`,
decoder reviews r1–r4, the interim source audit, both IA-1 evidence manifests,
all six production tail modules, and the relevant test harness. Pinned Go
sources traced: `ipc/reader.go` (`7e95b0…935e`), `ipc/file_reader.go`
(`a7fce0…61f7`), `internal/dictutils/dict.go` (`69c642…c2c3`) and `compare.go`
(`7542d6…af1f`). Locked arrow-rs 60.0.0 `arrow-ipc/src/reader.rs` (`4307d2…5202`)
and `arrow-schema/src/field.rs` (`2773c6…4278`) were also traced. Full identities
and the per-case results are in
[`evidence.json`](/home/ajs/.cache/pb-phase6/review-wave-decoder-opus/evidence.json).

## Findings

### W-1 — Medium: a missing dictionary on the flat record path renders and continues, where Go fails

**Measured** against the pinned Go oracle and current production worker/session code.

Paths: `src/tail/session.rs:512-530` (the flat path passes `self.dictionaries`
straight to `read_record_batch`), `src/tail/session.rs:466-486` (initial gating
counts dictionary *messages*), and arrow-ipc 60 `reader.rs:178-190`. Arrow
synthesizes an empty values array when a dictionary ID is absent.

Go's `getInitialDicts` reads `NumDicts()` dictionary messages. Repeated IDs
also count, so a stream can finish that phase without loading one declared ID.
At every record, `newRecord` calls `ResolveFieldDict` for every column.
`Memo.reify` then fails with `no dictionaries found for id=N`, including for
zero rows or all-null keys (`file_reader.go:353-361`, `dict.go:252-256`). Rust
matches this on the nested path (`session.rs:649-652`). On the flat path, used
whenever no dictionary has nested dictionary values, Arrow substitutes empty
values. All-null and zero-row columns therefore decode successfully.

Witness: two flat `Dictionary<Int32, Utf8>` columns, `a` (ID 0) and `b` (ID 1).
The stream is schema, dictionary 0, dictionary 0 again (a replacement in place
of ID 1), then one record:

| Case | Go stdout / end | Rust snapshot stdout / end |
| --- | --- | --- |
| `flat_missing_null` (b null) | empty / `arrow/ipc: unknown error while reading: arrow/ipc: no dictionaries found for id=1` | `{"a":"x","b":null}\n\n` / EOF → reconnect |
| `flat_missing_zero_rows` | empty / same error | `\n` / EOF → reconnect |
| `flat_missing_nonnull` (b key 0) | empty / same error | empty / `…Value at position 0 out of bounds…` (same stage, other text) |
| `nested_missing_{null,zero_rows}` (adds a nested column) | empty / same error | identical to Go |
| `flat_control_null`, `nested_control_null` | rendered row / EOF | identical to Go |

**Contract impact:** both the stdout and terminal stage differ. Go terminates
with the fatal error, while Rust prints a batch Go never prints and treats the
subscription as healthy. A live stream would keep Rust running after Go has
exited. This is not malformed-buffer safety or a declared limitation: the
flatbuffers and bodies are valid. It is neither a hostile-offset case nor
listed in any ledger. By source inference, the flat path is unchanged from R4
(`session-r4.rs:500-507`), so this predates IA-1. The continuation does make the
two paths inconsistent, however. I did not execute an R4 binary.

**Minimal fix** (prototype: `/home/ajs/.cache/pb-phase6/review-wave-decoder-opus/prototype-fix.diff`,
`5485c2…d2c8`; about 20 lines, no new module, no validation bypass):

1. Make `ipc_dictionary_ids` return the unique IDs in schema pre-order:
   a dictionary before its nested values, in field and child order. That is the
   order in which Go's record resolution first reaches each ID. Keep
   `ids: BTreeSet` for the counts. Use the set for deduplication
   (`if ids.insert(id) { order.push(id) }`); my prototype's `Vec::contains`
   is quadratic and should not be adopted.
2. In the RecordBatch branch, after `Layout::NoRows` and before choosing a path,
   iterate that order. Return the existing nested-delta text if
   `nested_deltas` has the ID. Otherwise return
   `{UNKNOWN_READ}arrow/ipc: no dictionaries found for id={id}` if
   `dictionaries` lacks it. Both paths still use their current decode logic.

The prototype passes all owner tests (`cargo test --lib tail::` gives 103 passed:
101 existing plus two cache-only probe tests). On the unmodified snapshot's
probe streams, all nine match the captured Go stdout and terminal text exactly,
including W-2. Regressions to add: `flat_missing_null`,
`flat_missing_zero_rows`, `nested_two_missing_order` plus the controls, using
Go-captured expectations. The streams and Go outputs are in `…/streams/`.

### W-2 — Low: with several missing IDs, Rust reports the lowest ID rather than Go's first-reached ID

**Measured.** `src/tail/session.rs:626-633` resolves in `BTreeSet` (numeric)
order, whereas Go reports the first failing ID in column/path pre-order. Witness
`nested_two_missing_order` has columns `c` (nested, present), `a` (ID 9,
missing) and `b` (ID 7, missing). Go reports `…no dictionaries found for id=9`;
Rust reports `id=7`. The stage and empty stdout are the same. It is a
diagnostic-text difference only, and W-1's ordered check removes it. Go
interleaves array loading and resolution per column, so mixed malformed-buffer
plus missing-ID ordering stays under the existing declared mixed-fault limit.
This report does not expand that limit.

## IA-1 continuation: verified, no objection

**Go semantics (traced).** `typeFromFB` registers children before parents, and
`Memo.AddType` keeps the last TypeEqual occurrence as the load type.
`readDictionary` loads values without nested dictionaries.
`ResolveFieldDict` reifies each ID by the column's own schema path. It mutates
the memo-shared values data (`SetDictionary`), and array dictionaries are built
lazily at render. The last traversal touching a shared parent therefore decides
its nested child binding.

**Equivalence argument (inferred, then probed).** Every traversal reaching a
shared object occurs at a schema occurrence of that ID. Two occurrences of one
ID are never ancestor and descendant: that would require a type to contain
itself, which Go's conflict check rejects. Therefore Go's pre-order "last
traversal" equals Rust's post-order "last occurrence" (`dictionary_types`,
`session.rs:775-811`). The same argument makes the layout graph acyclic. The
`visiting` cycle check is therefore defensive and unreachable for accepted
schemas. It is deterministic and does not panic.

**Probes outside the owner corpus (measured):**

- `container_list_first`: column `a` is `List<Dict id0>` and `b` is top-level
  `Dict id0`, each with a different child dictionary. Go and Rust both print
  `{"a":[[[9,10]]],"b":[[9,10]]}`.
- `container_list_second`: the same columns in reverse order. Go and Rust both
  print `{"a":[[[7,8]]],"b":[[7,8]]}`.
- `shared_index_widths`: one flat ID with Int8 and Int32 index columns. Go
  accepts it because the memo keys on value type only. Both print
  `{"a":"x","b":"x"}`.

**Type canonicalization against Go `TypeEqual`.** `compare.go:41-153` matches the
wire canonical forms. It ignores List/FixedSizeList element names and all field
metadata, and compares Map types and nullability but not names or `KeysSorted`.
It compares Struct and Union names, nullability and type codes. LargeList,
nested Dictionary and other types use `reflect.DeepEqual`, so names and metadata
count there. `validation::type_equal` reproduces this on original types before
any wire rewrite. Arrow-rs `Field` equality ignores `dict_id` and
`dict_is_ordered`, so a resolved value type from the last occurrence equals
every earlier occurrence's canonical wire type. I found no Go-equal pair whose
canonical wire types differ. I found no wire canonicalization that erases a
property Go compares.

**Ownership and safety.**

- Placeholders are validated integer keys.
- `bind_type` (`session.rs:672-717`) rebuilds `ArrayData` over shared buffers
  with safe `build()`, so bounds are checked at binding time. The owner test
  covers -1, 2 and `i32::MAX`.
- The resolved snapshot is transient; the `RecordBatch` holds its Arcs.
- Nested delta pieces are retained unconcatenated. Replacement drops them
  (`session.rs:611-613`); EOF before a record stays a reconnect (`end()`).
- The `dictionary_types[&id]` index cannot panic because `dictionaries` keys are
  a subset of `dictionary_types`.
- Production has no `unsafe`, `skip_validation` or `catch_unwind`. The only
  `unsafe` is test-only in `values.rs:2310`.
- The non-native-endian rejection, verifier depth 128 against array-load depth
  64, Go-order schema pre-walk, `validate_body`, union/FSL checks and wire
  nullability exceptions are all intact.

## Lifecycle and resources

The diff against `session-r4.rs` touches only the decoder region. These are
unchanged since R4 and were re-traced:

- Only the first metadata-only message is skipped (`open`). DoGet and
  reader-initialization failures are fatal. Established EOF and
  Canceled/Unavailable/OK statuses reconnect after 500 ms with a fresh decoder.
- The worker owns Open and Message events with capacity-one channels and an
  acknowledgement before the next read. All decode, binding and render CPU,
  including large nested binding, stays on the worker, not the h2 runtime.
- The decoded batch is dropped before the blocking write. Staging above 4 MiB
  and renderer scratch above 1 MiB are released. The diagnostics in-flight RAII
  guard spans the try_send to acknowledgement consumption.
- Ordinary write errors (ENOSPC) are ignored as in Go's fmt. SIGPIPE stays with
  the process writer, and no signal handler is installed.
- A real worker panic resumes after join; it is not caught.

The focused suite exercises these on the copy. I ran no new process or signal
fixtures.

**Accounting note (not an objection):** `max_dictionary_buffer_bytes` now
includes retained nested delta pieces. `max_batch_buffer_bytes` counts bound
nested dictionary buffers shared with the memo. The semantics string already
says that owners can double-count shared buffers.

**Measurement note (not a gate):** the nested path rebuilds and validates every
nested dictionary on every record, at O(nested dictionary size) per record. Go
does no such validation. The large fixture-r5 dictionaries appear flat, so no
campaign exercises this cost. If nested workloads matter, cache the resolved map
until the next dictionary message. That keeps bounds checks per binding, not per
row.

## Commands actually executed

The scratch copy is `/home/ajs/.cache/pb-phase6/review-wave-decoder-opus/scratch`.
Its production source is byte-identical; the cache-only probe modules are
appended to `tests.rs`. The settings were Rust 1.96.0,
`TMPDIR=…/review-wave-decoder-opus/tmp`, `CARGO_TARGET_DIR=…/review-wave-decoder-opus/target`,
`--locked --offline -j 8`, `nice 10`.

- `cargo test --lib review_wave_probe_export` passed. The first attempt failed
  because my probe patched an absent default flatbuffer ID 0; I corrected the
  probe, and this was not a production failure.
- `cargo test --lib tail::` on the snapshot copy: **102 passed** (the owner's
  101 plus my export probe), 0 failed.
- `cargo test --lib review_wave_container_export` passed.
- `cargo test --lib tail::` on `scratch-fix` (prototype): **103 passed**, 0 failed.
- Go: I reused the existing `go-decoder` (`ea12e2…5fe`, copied, not rebuilt) with
  `ulimit -v 4 GiB`, `-f 16 MiB` and a 10-second timeout, on 11 streams. All
  exited 0 within the limit.

Not run: the diagnostics feature suite, clippy and fmt on the prototype; the
all-target suite; the 291-case process replay; retained or real-deadline
suites; timing, profiling or soak. I did not regenerate the durable corpora
(44 + 10 + 30 + 15 + 25); their replays are only inside the focused suite.
Provider rate limits did not interrupt this review. No shared source, test or
documentation file other than this report was changed. There was no commit,
agent, thread, live service or `/tmp` mutation. The harness temp directory hit
EDQUOT for command output, so all output was redirected into the cache.

## R5 candidate relation

After this review the parent reported that the settled R5 checks and build
completed with source unchanged. I rechecked the production hashes above: they
are unchanged. `candidate-review-r5/manifest.json` and
`docs/evidence/phase6/candidate-review-r5.json` reference the reviewed
`session.rs` hash. I verified the standard binary hash as
`0a5877073979ee43e518b40908ae7274286144a9799137202d71a4310b68e825`.

By source identity, W-1 and W-2 therefore apply to R5. I did not execute the R5
binary on these streams. R5's 291 remaining-contract passes do not cover them,
nor do the 25 IA-1 cases planned for `harness/phase6_dictionary_cases.py`. The
W-1 streams and their Go outputs should join that dictionary process set after
the fix. The Sol contracts review (`32ce42…5b68`) findings CW-1–CW-3 concern the
harness and do not overlap with W-1/W-2.

## Acceptance boundary

After W-1 is corrected with Go-captured regressions on a new source hash, I
expect no remaining objection in the decoder, lifecycle and ownership scope.
That expectation is not acceptance of a source I have not reviewed. Existing
declared limitations stay as they are: non-native-endian rejection, the
schema-depth bound, row-count and union/decimal producer differences, mixed-fault
ordering and the initial HTTP/2 103 outcome. This report adds no waiver. The
291-case and retained results remain the coordinator's evidence. Performance,
resource, macOS and live-delivery claims remain outside this report. The fresh
final whole-phase Opus review remains required after settlement.
