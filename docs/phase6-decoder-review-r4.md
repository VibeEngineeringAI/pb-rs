# Phase 6 decoder/value/session review, round 4

This is a fresh, independent, focused review of the native tail IPC decoder,
schema pre-walk, wire schema, session lifecycle boundary and the diagnostics
in-flight counter. It is not the final whole-phase acceptance review. Release
replay, measurement and the final independent whole-phase review are still
pending. No production source was edited, and nothing was committed. The only
file written is this report.

Labels: **MEASURED** means a bounded probe ran. **SOURCE** means the code was
traced but not executed. **PLAUSIBLE** means a source trace that relies on a
library path I did not read line by line.

## Execution limitation (read first)

No command could run in this round. Every Bash invocation, including `true`,
failed before execution because Claude Code's own task-output directory
(`/tmp/claude-1000/...`) is over its disk quota (EDQUOT). The reviewer session
was not started with `CLAUDE_CODE_TMPDIR` under `~/.cache/pb-phase6`, so this
could not be redirected. As instructed, I did not delete anything under the
shared `/tmp`. As a result:

- **No build, test, Go probe or fixture rerun was executed.** Every finding
  below is SOURCE or PLAUSIBLE. Nothing here is MEASURED.
- **Source hashes were not recomputed.** The owner-reported prefixes, session
  `e5ed7218…`, validation `3911593a…` and tests `1a8ad35a…`, are unverified by
  this round. I reviewed the file contents as read at review time. The owner's
  reported 89 tail tests and 90 with diagnostics, against REAL transport
  `f4d3ce95`, were not reproduced.
- The fixture verification below is by reading the test code and the
  committed Go outputs described in `phase6-values-notes.md`, not by rerunning
  them.

A rerun in a session whose temp directory is on `~/.cache/pb-phase6` should
cover: `cargo test --offline --lib tail::` with and without `--features
diagnostics`; the r2, r3 and value corpora; and the bounded Go probes proposed
for R4-1 to R4-3.

## Sources read

- Current `src/tail/session.rs`, `session/tests.rs`, `validation.rs`,
  `resources.rs` and `values.rs` (plan construction and Decimal/Union plans),
  plus `Cargo.toml`.
- `docs/rust-phase6.md`, decoder reviews r1, r2 and r3, and
  `docs/phase6-values-notes.md`.
- Arrow Go v13.0.0: `ipc/metadata.go` (`fieldFromFB`, `typeFromFB`,
  `concreteTypeFromFB`, `unitFromFB`, `decimalFromFB`, `schemaFromFB`),
  `ipc/reader.go` (`readSchema`), `ipc/ipc.go` (`newConfig`),
  `ipc/endian_swap.go`, `ipc/message.go`, `internal/dictutils/dict.go` and
  `datatype_nested.go` (union modes and codes).
- arrow-rs 60.0.0: `arrow-ipc/src/convert.rs` (`try_fb_to_schema`,
  `try_field_from`, `get_data_type`) and `arrow-schema/src/fields.rs`
  (`UnionFields::try_new`/`from_fields`). Also `flatbuffers-25.12.19`
  `verifier.rs` (string verification).

## Verification of round-3 findings (SOURCE: code and test reading)

| R3 item | Status | Evidence |
| --- | --- | --- |
| R3-1 FSL size ≤ 0 | **Resolved, parity** | `go_type` returns `Fault::Panic("arrow: invalid size")` after the child-count check. That matches Go, which builds `FixedSizeListOfField` after `len(children) != 1`. Panics are never wrapped with the field path, and `go_field` converts children before the parent. The error reaches `initialize` unwrapped, which adds `FLIGHT_READER`. Test `schema_faults_use_go_order_wrapping_and_panic_text` covers sizes 0 and −1 nested in a struct. It also covers a child's returned error winning over the parent's FSL panic. The r3 corpus test asserts `FLIGHT_READER + <Go init line>` exactly for g6, g6b, g9 and g9b in its parity branch. |
| R3-2 union typeIds wrap | **Resolved, parity** | `go_union` rejects any explicit ID that `i8::try_from` refuses, as `Fault::Error("union type id out of bounds")`. Wrapping then follows Go's `could not convert field type:` → `field child N:` → `field N from flatbuf:` chain. In-range negative IDs and count mismatches are recovered panics with Go's text, which matches `unionType.validate`. Covered by a nested test, and by g10_union_ids_256 asserted exactly. See R4-3 for the residual cases. |
| R3-3 verifier depth 61–64 | **Resolved; bound declared** | Every header goes through `root_as_message_with_opts` with `max_depth = 128`. Tests: depth 64 renders; depths 65 and 100 initialize and reconnect when schema-only, and are fatal with Go's `nested type limit reached` at the first record; depth 128 fails initialization (declared). g11 depths 63, 64 and 65 are in the r3 parity branch. Recursion in the pre-walk, `try_fb_to_schema`, `wire_type`, `type_nesting` and the renderer plan is bounded by that depth. |
| R3-4 row-count rule | **Declared divergence; comments corrected** | The `record_batch` doc comment states Go's measured rule: the batch length, or the first column's length when negative; columns must be at least that long, and only those rows render. The FSL walker comment now points to it. `Layout::NoRows` is described as the probed sub-case. The r3 corpus test counts exactly five row-count rejections (g1, g3, g3b, g4_node_max, g5) and two text-only fatals (g2, g8). This is consistent with r3. See R4-8 for a test-strength gap. |
| R3-5 nullable run ends | **Resolved, parity** | `wire_type` rebuilds the run-ends field as non-nullable, so arrow-data still rejects real nulls in run ends. Unit test `wire_run_ends_are_non_nullable_whatever_the_schema_declares`. g7_ree_nullable_run_ends is in the r3 parity branch. |
| R3-6 tautological in-flight counter | **Resolved in source; evidence limits remain** | Audited below, with residual R4-7. |
| r2 safety divergences | **Unchanged and still tested** | p1_fsb_negative (`schema_types`, init) and p2_fsl_overflow (walker) are counted as divergences in the r2 corpus test. `hostile_offsets_remain_rejected_for_rewritten_strings` still covers offsets on the Binary wire type, including dictionary values. |

### In-flight counter audit (R3-6 response)

- **Placement is correct.** `deliver` takes the RAII guard only after
  `try_send` succeeds, and only for `Open` and `Message`. Status events are
  excluded. The guard is bound to a named local (`_inflight`, not `_`), so it
  lives until `deliver` returns, which is after `acks.recv()` has produced the
  ack. If the `deliver` future is dropped, the guard is released too. If the
  worker acks before the guard is taken, the guard still precedes the `recv`
  that consumes the ack, so the outstanding span is never missed.
- **The arithmetic is correct.** `fetch_add + 1`, then `fetch_max`, then
  `fetch_sub` on drop. All of these run on the single runtime thread.
  `reset` zeroes `current` only before workers start, so a live guard cannot
  underflow it.
- **Test causality is partial.**
  `overlapping_handoffs_are_counted_and_release_on_scope_exit` proves that the
  `Outstanding` primitive adds up and releases. It does not call `deliver` or
  `inflight()`, so it does not show that the session holds the guard across
  the handoff (see R4-7).

## New findings (prioritized)

### R4-1. Big-endian IPC schemas: Go byte-swaps, Rust decodes as little-endian (SOURCE, medium; silent wrong output on a valid stream)

Go v13 `newConfig` sets `ensureNativeEndian: true`. `readSchema` sets
`swapEndianness` when `!schema.IsNativeEndian()`, and records are swapped by
`swapEndianArrayData`, which handles offsets, primitives, decimals and so on.
Go therefore renders correct values from a valid big-endian stream.

The Rust path never reads `Schema.endianness()`. `try_fb_to_schema` only
rejects big-endian schemas whose **top-level** fields are Decimal ("Big Endian
is not supported for Decimal!"). `read_record_batch` receives an
`arrow_schema::Schema`, which has no endianness, so it cannot swap. Expected
effects on a big-endian stream (SOURCE):

- Fixed-width numeric, temporal, interval and nested-Decimal columns render
  byte-swapped values. For example, Int64 `1` would render as
  `72057594037927936`. Validation does not catch this, because every bit
  pattern is a valid value.
- Offset-bearing columns (strings, binary, lists, maps, dense unions) most
  likely fail validation and become a fatal read.
- A top-level Decimal fails initialization.
- Bool, Int8/UInt8, validity bitmaps and FixedSizeBinary are unaffected.

The likelihood is low, because Go and arrow-rs writers on amd64/arm64 emit
little-endian schemas. Still, this is the only case found where Rust prints
**different values** for a valid stream that Go renders correctly. The
cheapest fix is fail-closed: in `Decoder::new`, reject `Endianness::Big`
(non-native) at initialization with a declared divergence text. Full parity
would mean byte-swapping buffers before `read_record_batch`. Add a hand-built
test with `fb::message` setting `Endianness::Big`. For a Go oracle fixture, a
small generator can byte-swap a little-endian stream's buffers and flip the
schema flag. Run it through the existing decoder-oracle probe under the usual
bounds.

### R4-2. Fields sharing a dictionary ID with conflicting types (SOURCE/PLAUSIBLE, low–medium)

Go's `typeFromFB` calls `memo.AddType(id, valueType)` for each dictionary
field, in conversion order (children before parents). It fails initialization
with `arrow/ipc: conflicting dictionary types for id N` when
`!arrow.TypeEqual(existing, new)`. The error is wrapped as
`could not convert field type:` → (`could not convert field child i:`)* →
`could not convert field N from flatbuf:`, so in tail it is a fatal reader
initialization.

Rust records only the set of IDs (`ipc_dictionary_ids`) and has no conflict
check:

| Shape | Go | Rust (expected) |
| --- | --- | --- |
| a: two fields share an ID, one `Utf8`, the other `Binary` | init fatal | **Initializes and renders.** `wire_type` maps both value types to Binary, so the dictionary validates for both columns. One column renders as strings, the other as base64 (SOURCE). |
| b: shared ID with physically different value types (for example Int32 and Utf8) | init fatal | Initializes. The dictionary is decoded with the first field's type. The other column then fails arrow's dictionary child-type validation at the first batch (PLAUSIBLE). A schema-only, or schema-plus-dictionaries-only, stream **reconnects every 500 ms indefinitely**. |
| c: shared ID whose nested value types differ only in child nullability | init fatal if `TypeEqual` compares nested nullability (PLAUSIBLE) | Renders, because the wire schema makes every child nullable (SOURCE) |

This is the same class as R3-1: Go terminates where Rust renders or retries.
Only a hostile or malformed producer emits it, because Go's and arrow-rs's
writers assign IDs themselves. The fix is a post-order id→type memo in the
pre-walk, or on the converted original (pre-wire) schema, compared with Go
`TypeEqual` semantics. Go compares list element types and nullability but not
list element names; struct fields compare names. Use Go's text and wrapping.
Bounded Go probes for a, b and c are required before claiming parity. Until
then, declare it.

### R4-3. Union schema residuals: arrow rejects what Go accepts (SOURCE, low)

- **Explicit but empty `typeIds` vector with children.** Go treats
  `TypeIdsLength() == 0` as implicit codes 0..n, and so does `go_union`.
  arrow-ipc instead calls `UnionFields::try_new([], fields)`, which returns
  `fields iterator has more elements than type_ids iterator`. Rust fails
  initialization; Go initializes and renders.
- **Duplicate explicit type IDs.** Go's `unionType.validate` checks only the
  count and the 0..127 range. `init` maps `childIDs[code] = i`, so the last
  child wins, and Go renders. arrow `try_new` rejects duplicates
  (`duplicate type id`), so Rust fails initialization.
- **Invalid union mode** (text only). Go's `UnionMode` constants are
  `SparseMode = 2` and `DenseMode = 3`, because they share an `iota` block with
  two earlier constants. An unknown flatbuffer mode leaves the zero value, and
  `UnionOf` panics `arrow: invalid union mode`. That panic comes after the
  type-ID range error and before the count/range validation. Rust returns
  arrow's `Unexpected union mode` with the decode prefix. Both are fatal at
  initialization. Adding the mode check to `go_union`, in that position,
  would give Go's text.

None of these comes from mainstream writers. Declare the first two, or emulate
them by building the union fields from the pre-walk's Go codes. Fix the third
for text if convenient.

### R4-4. Decimal precision/scale outside u8/i8 (SOURCE, low)

Go `decimalFromFB` accepts any int32 precision and scale. Rendering uses
`Text('g', precision)` and `math.Pow10(scale)`. arrow-ipc converts precision
to `u8` and scale to `i8`, and fails initialization otherwise ("Invalid decimal
precision/scale"). Precision outside 0..=255 or scale outside −128..=127 is
therefore init-fatal in Rust. Go initializes and renders, but this is
unprobed. Very negative scales make `Pow10` return 0, and a zero value would
then hit `big.Float` 0/0, which panics with `ErrNaN`. Declare this as a
malformed-producer divergence.

### R4-5. Flatbuffer string verification is broader than the declared field-name limit (SOURCE, low)

The Rust flatbuffers verifier rejects any string that is not UTF-8, or that has
no NUL terminator (`ignore_missing_null_terminator` defaults to false). This
applies anywhere in a header: field names (already declared), schema- or
field-level custom metadata keys and values, Timestamp timezone strings, and
`Message.custom_metadata` on every record or dictionary message. Go reads these
unverified and never renders them (the timezone is ignored). The effect in
Rust is fatal initialization, or a fatal read for a non-schema message. Extend
the existing known-limits entry from field names to all header strings.

### R4-6. Absent `Schema.fields` vector (SOURCE, low)

When the optional `fields` vector is absent, `try_fb_to_schema` fails with
`IPC schema has no fields`. Go's `FieldsLength()` is 0, so it initializes an
empty schema. A zero-column record then renders `{}` per row. Writers always
emit the vector (Go writes an empty one), so declare this.

### R4-7. In-flight counter: test causality and wording (SOURCE, low; evidence quality)

The counter now measures what it claims (see the audit above), but two limits
remain:

1. The bound is still structural. `deliver(&mut self)` cannot run twice
   concurrently, so the session can never hold two guards. The counter can
   only detect overlapping calls to `deliver` itself. It cannot see a
   regression that sends outside `deliver`, for example read-ahead through
   another queue. A `let _ = inflight()` regression would also still report a
   peak of 1. "max_application_messages_inflight = 1" is a consistency
   observation, not an independent bound. The blocked-stdout and
   schema-initialization tests remain the causal evidence for "no next read".
2. `docs/rust-phase6.md` says "an overlap test requires the counter to reach
   two under deliberately overlapping handoffs". The test overlaps
   `Outstanding::begin` guards on two threads, not session handoffs. Reword
   it. Optionally, add a diagnostics-feature test with two parts. First, hold
   the worker with `open_gate` and assert that `OUTSTANDING.current == 1` while
   it is held. Then assert `current == 0` and `peak == 1` after `pump`
   completes. That would show that the guard spans the real handoff. The test
   would need to serialize with other users of the global (`reset(true)` /
   `finish()`).

### R4-8. r3 row-count branch does not pin the failure stage (SOURCE, low; test strength)

`decoder_review_r3_streams_match_go_or_are_declared_divergences` asserts only
`result.is_err()` and empty stdout for the five row-count cases. A regression
that moved these failures to initialization (`FLIGHT_READER…`), or into an
unrelated error, would still pass. Assert `starts_with(UNKNOWN_READ)` and not
`FLIGHT_READER`, or match the expected arrow text, as the text-only branch
already does.

## Owner-listed source-only schema unknowns, assessed

| Item | Go | Rust | Effect | Priority |
| --- | --- | --- | --- | --- |
| Conflicting types for shared dictionary IDs | init fatal | initializes; then renders, fatal at first batch, or reconnects forever | Stage and outcome difference | **R4-2** (low–medium) |
| Missing custom-metadata key or value | keeps the pair with `""` | drops the pair | **No observable effect.** Metadata is never rendered, and Go resolves no extension types by default. (Invalid UTF-8 in metadata is different; see R4-5.) | none |
| Invalid union mode | init fatal (panic text) | init fatal (arrow text) | Text only | R4-3 (text) |
| Decimal bounds | accepts any int32 | init fatal outside u8/i8 | Outcome difference (malformed producer) | R4-4 |
| Invalid Time/Timestamp units | init fatal: recovered panic `invalid flatbuf.TimeUnit(N)` via `unitFromFB`, or a returned error for a bad width/unit pair | init fatal (arrow `ParseError`) | Text only | ledger |
| Invalid Date/Interval/Duration units | init fatal (returned error) | init fatal | Text only | ledger |
| Pre-walk vs arrow ordering | first failing field wins | any pre-walk fault, in any field, wins over an arrow-only rejection in an earlier field | Text only; both fatal at init | ledger |

## Lifecycle and integration boundary (SOURCE)

There is no change from r3 apart from the counter. `open` gives Go's wrapping
for the first and second message and for EOF. `Decoder::new` errors are fatal
at initialization. An established `None` goes through `decoder.end()`, and
codes 0, 1 and 14 reconnect. Fatal paths keep raw bytes. The decoder moves by
value through the worker. The capacity-one, ack-before-next handoff is
unchanged. A Go-render panic exits 2 through `Ack::Panicked`. A Rust worker
panic still re-raises; no reachable trigger was found in this round's source
reading. Transport behavior was not reviewed.

## Accepted bounded divergences (current ledger, as I understand it)

- **Safety rejections where Go panics or misreads.** Negative FixedSizeBinary
  width (init), FSL length overflow, hostile offsets, short validity bitmaps,
  union id/offset sizes and alignment, impossible compression prefixes and
  negative node lengths.
- **Row-count rule (R3-4).** Five corpus cases where Go renders and Rust is a
  fatal read.
- **Verifier depth 128.** Schemas deeper than about 125 levels fail
  initialization, while Go initializes.
- **Diagnostic text.** Schema/dictionary wrapping, row-count text, verifier
  text, and the unit/mode texts above.
- **Resources.** Compression allocation envelope, unbounded dictionary deltas,
  retention-not-peak policies and aligned-body aliasing; measurement pending.
- **Proposed additions** (not yet declared): R4-3 (first two bullets), R4-4,
  R4-5 and R4-6. R4-1 and R4-2 need a decision, either fail-closed or parity,
  before final review.

## Commands and results

None. Every Bash call failed with EDQUOT on Claude Code's own temp directory
before running, and no test, build, probe or hash command executed. All file
contents were read with the file reader.

## Summary

| Item | State |
| --- | --- |
| R3-1, R3-2, R3-3, R3-5 | **Resolved** (source and test reading; not rerun this round) |
| R3-4 | **Declared divergence**; comments now match the measured rule |
| R3-6 | **Resolved in source**; evidence limits in R4-7 |
| R4-1 big-endian schemas render byte-swapped values | **Open, medium.** Fail closed at init, or swap |
| R4-2 conflicting shared dictionary types | **Open, low–medium.** Go-semantics memo, or declare, after a probe |
| R4-3 union empty/duplicate typeIds, invalid mode text | **Open, low.** Declare or emulate |
| R4-4 decimal precision/scale range | **Open, low.** Declare |
| R4-5 header string verification | **Open, low.** Extend the declared limit |
| R4-6 absent schema fields vector | **Open, low.** Declare |
| R4-7 counter test causality and wording | **Open, low.** Wording, plus an optional gated session test |
| R4-8 row-count stage assertion | **Open, low.** Strengthen the assertion |
| Hash recomputation, test and fixture reruns, Go probes | **Not done in this round** (environment); required before relying on this report |

None of these is a memory-safety blocker. I found no new path to a Rust panic.
In arrow-ipc 60, every `get_data_type` branch returns an error. The only
panicking constructor, `UnionFields::from_fields` above 128 children, sits
behind the pre-walk's implicit-code check. R4-1 is the one place where Rust
can print wrong data for a valid stream, so I recommend the fail-closed check
before the final review. R4-2 is the remaining case of the "Go fatal at init,
Rust renders or retries" class that R3-1 belonged to.
