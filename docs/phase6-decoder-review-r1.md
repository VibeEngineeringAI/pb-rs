# Phase 6 decoder/value/session review, round 1

This is an independent source-only review of the native tail Arrow decoder,
value renderer and session. It is not a phase acceptance review. I ran no builds,
tests, timings or processes, and nothing here claims a pass.

Sources read:

- `src/tail/{values,session,mod,resources}.rs`: the uncommitted worktree under
  `src/tail/`. The source changed during the review. `values.rs` grew from 1940
  to 2452 lines, and `session.rs` grew from 712 to 762 lines (it added
  `ipc_body`, `validate_body` and the missing-dictionary row-count ID error).
  I re-read only the current `session.rs` decode path (lines 385–590) and the
  `values.rs` excerpts cited below.
- Go `07af390:cmd/tail.go`.
- `github.com/apache/arrow/go/v13@v13.0.0`: `ipc/{reader,file_reader,message,metadata}.go`,
  `flight/record_batch_reader.go`, `array/*` and `float16`.
- `goccy/go-json@v0.10.0`: map sorting in `internal/encoder`.
- Rust crates `arrow-ipc`, `arrow-data`, `arrow-buffer` and `arrow-array`, all
  60.0.0, from `~/.cargo/registry`.

Severity reflects user-visible impact. Verdicts: CONFIRMED means the
library/source path was traced line by line, PLAUSIBLE means it still needs a
repro.

## 1. Remaining Rust panics on malformed arrays (CONFIRMED, high)

`validate_body` only checks that each buffer's `offset + length` lies inside
the body. Array lengths, null counts and alignment are not checked. The
low-level reader still panics before any validation can return an error:

| Path (arrow 60) | Trigger | Panic |
| --- | --- | --- |
| `arrow-data` `ArrayDataBuilder::build` (data.rs:2337) calls `BooleanBuffer::new(null_bit_buffer, offset, len)` *before* `validate` | Any primitive, binary, list, FSL, map or dictionary node with `null_count > 0` whose validity buffer has fewer than `len` bits. This includes an empty validity buffer with null_count 1. | `buffer not large enough` (boolean.rs:128) |
| `create_struct_array` (reader.rs:372) `BooleanBuffer::new(null_buffer, 0, len)` | Struct node with `null_count > 0` and a short validity buffer | same assert |
| Union type ids (reader.rs:205) `next_buffer()?.slice_with_length(0, len)` | Type-id buffer is in range but shorter than the node length, or the node length is negative (`as usize` gives a huge value) | `slice_with_length` assert |
| Dense union offsets (reader.rs:210) `slice_with_length(0, len * 4)` then `ScalarBuffer<i32>::from` | Offsets buffer shorter than `4*len`. Or the IPC buffer offset is not a multiple of 4: `ipc_body` aligns only the body *start*, and per-buffer offsets inside it are attacker-chosen. The `-1` "uncompressed" prefix keeps the offset `+8`. | slice assert / `Memory pointer is not aligned` (scalar.rs:194) |
| Any node | `length`/`null_count` negative → `as usize` | One of the above, or see §2 |

A worker panic drops the ack sender, `drive` returns, and `run` calls
`resume_unwind`. The process exits 101 with Rust panic text from the
`tail_worker` thread. The status-line clear and the CLI error rendering are
skipped. Go behaves differently. For the truncated or short-buffer cases it
either recovers inside `next()` (`arrow/ipc: unknown error while reading: …`,
COMMAND_FAILED) or, for short validity bitmaps, accepts the batch and panics
later in rendering. That second case is already in the ledger as hostile input
that is not imitated. Neither Go behavior is a Rust panic.

Recommendation (no global hook needed): extend `validate_body` into a structural
pre-walk of the wire schema against `nodes()`/`buffers()`. It should follow the
same order as arrow-ipc 60's `create_array`/`skip_field` (V4 unions consume an
extra validity buffer; dictionary fields consume two buffers and no child
nodes). Pin `arrow-ipc = "=60.0.0"` because the walk mirrors private order. For
each node check:

- `0 <= null_count <= length`, `length >= 0`, and the batch `length >= 0`;
- when `null_count > 0`, the effective validity size is at least `ceil(len/8)`
  bytes;
- for unions, effective type-id bytes `>= len`; for dense unions, offsets
  `>= len*4` (checked) and `buffer.offset % 4 == 0`.

For compressed bodies the *effective* size is the 8-byte prefix. A prefix of -1
means `length-8`, and 0 means empty. `decompress` already rejects output whose
size differs from the prefix. Add one malformed-IPC test per row of the table.
The decision is open whether to also add a narrow `catch_unwind` backstop in the
worker (thread-local "decoding" flag plus a chained hook that suppresses only
that thread's message), because unknown arrow-rs panics will remain.

Allocation failures from a hostile decompressed-size prefix are a separate
problem: `decompress_lz4` calls `Vec::with_capacity(prefix)`, and zstd's
`decompress(input, capacity)` does the same. These abort rather than unwind, so
`catch_unwind` cannot help. Go also dies on huge sizes, but differently
(`makeslice` panic or runtime OOM). Capping the prefix would be an explicit
divergence decision, related to the known "decoded memory is unbounded" ledger
item.

## 2. Negative or huge lengths with no buffers render unboundedly (PLAUSIBLE, high)

Repro: schema `{n: Null}` and a RecordBatch with `length = -1` and nodes
`[{length:-1, null_count:-1}]`. `Null` reads no buffers, and both values cast to
`usize::MAX`. The `length == null_count` check passes. `RecordBatch::try_new_with_options`
compares equal huge counts and appears to accept them. `BatchRenderer::render`
then loops `0..usize::MAX`, appending `{"n":null}\n` to `staging` until the
allocator aborts. The 4 MiB staging retention cap applies only *after* a batch.

A zero-field struct column has the same shape (`StructArray::new_empty_fields(len, …)`).
In Go, `NumRows()` is `-1`, so the loop body never runs and Go prints one
`\n`. Large positive lengths (for example 2^40 Null rows from a 100-byte
message) amplify in both implementations. Go also exhausts memory there, so that
case is a documented resource limit, not a divergence. The §1 pre-walk should
reject negative lengths, which closes the infinite case.

## 3. Union "null" uses the wrong predicate (CONFIRMED, medium)

Go Sparse/DenseUnion `GetOneForMarshal` (union.go:316, 591) returns `nil` only
when `data.IsNull(idx)` is true. That is the child's *validity bitmap*
(array.go:82). Otherwise it returns `[typeID, child.GetOneForMarshal(idx)]`.

Rust `Col::is_null` for `Union` (values.rs:858–866) recurses into the child's
own `is_null`, which means "GOM returns nil". The two predicates differ for
children without a bitmap, or whose nil comes from elsewhere:

- **Dictionary** child, valid index, null dictionary value: Go `[0,null]`, Rust
  `null`.
- **Null** child: Go `Null.setData` clears `nullBitmapBytes` (null.go:78–81), so
  `IsNull` is false. Go `[k,null]`, Rust `null`.
- **RunEndEncoded** child with a null value: there is no bitmap, so Go
  `[k,null]`, Rust `null`.
- **Union** child whose own child is null: Go `[outer,null]`, Rust `null`.

The same predicate matters at top level, inside lists, and through
dictionaries and REE whose values are unions. Fix: add a separate
`validity_null(i)` that reads the bitmap only. It is always false for
Null/Union/REE, and for Dictionary it reads the key bitmap only. Use it in the
union `is_null`, `gather` and `encode` arms. Add Go fixtures for each case.

## 4. Float16 subnormals (CONFIRMED, medium)

Go v13 `float16.Num.Float32` (float16.go:55–67) maps exponent 0 to float32
exponent 0 and keeps `fc<<13`. Half subnormals therefore become *float32
subnormals* of `fc·2^-136`, not the correct `fc·2^-24`. For example, bits
`0x0001` render as `1.1479437e-41` in Go.

Current Rust uses `half::f16::to_f32()` (values.rs:963), which is the correct
IEEE conversion, so it prints `5.9604645e-08`. The earlier hand-written version
also asserted `2^-24` in its test. Replicate Go's bit mapping for `exp == 0`
(`f32::from_bits(sign | (m << 13))`) and add a Go fixture. NaN, Inf and normal
values agree.

## 5. Panic text when a FixedSizeList is a list element (CONFIRMED, low)

`FixedSizeList.MarshalJSON` (fixed_size_list.go:140–161) calls
`enc.Encode(slice)` and *returns* the inner error. It does not panic inside a
nested `GetOneForMarshal` the way `List`, `Struct`, `Union`, `Dictionary` and
REE elements do. For `List<FSL<Float64 NaN>>` or `FSL<FSL<…>>`, Go panics in the
outer GOM with this message:

`json: error calling MarshalJSON for type *array.FixedSizeList: json: error calling MarshalJSON for type *array.Float64: json: unsupported value: NaN`

Rust's `gather` turns the inner failure into `Fail::Panic` at the inner level,
so the message lacks the outer `*array.FixedSizeList:` wrapper. The same applies
to float32 raw-token compaction errors. The exit status already matches.
Possible fix: when the child plan of a list is FSL, keep the inner failure as
`Encode` with the wrapped message, so the outer level performs the panic
mapping.

## 6. Smaller items

- **Extreme `Timestamp(Second)`** (low): Go's `time.Unix(sec,0)` and its
  absolute-date math wrap for |sec| near ±9.22e18 (`unixToInternal` +
  `internalToAbsolute` overflow). Rust computes the true date. Add a fixture or
  ledger entry. Nanosecond `i64::MIN` is already tested.
- **Schema error wrapping** (low, diagnostic): Go wraps field errors as
  `arrow/ipc: could not convert field %d from flatbuf: …` (metadata.go:1048),
  and child errors as `could not convert field child`. Go reaches unsupported
  flatbuf types through `panic` (metadata.go:825), which the init recover turns
  into `arrow/ipc: unknown error while reading: arrow/ipc: type X not implemented`.
  Rust emits `could not decode schema from message schema: Arrow type … is not
  supported …` and the Map messages without the field prefix. This falls under
  the known "IPC error strings may differ" ledger item; list it explicitly.
- **Dictionary errors** (low, diagnostic): `read_dictionary` maps errors with
  `e.to_string()` (`Invalid argument error: dictionary id N not found in
  schema`, `No existing dictionary for delta…`). Go returns `arrow/ipc: no
  dictionary type found with id: N` or, after a recover, the AddDelta panic
  text. The row-count ID mimic covers only the initial-phase RecordBatch case.
- **Retention aliasing** (PLAUSIBLE, resources): `ipc_body` keeps aligned
  bodies zero-copy, so dictionaries retained for a subscription alias the
  FlightData `Bytes`. If prost/tonic split those from a larger receive
  allocation, each retained dictionary pins that whole allocation.
  `get_buffer_memory_size` would under-report it. Include this in the
  dictionary soak's RSS-versus-reported comparison.
- **Unrecoverable dictionary ID** for a dictionary nested directly in dictionary
  values: Rust counts one fewer initial dictionary than Go's mapper. This is
  very niche; keep it in the ledger.

## Checked and found consistent (source only)

- Encoded-key sort with the trailing `,` (goccy `Mapslice.Less` over
  `key+','`; `appendMapKeyValue` replaces it with `:`).
- Last duplicate wins, while shadowed duplicates are still gathered (Go
  evaluates every column's GOM before `Encode`).
- Go `Encoder.Encode` writes nothing for a failed row; the batch is truncated
  but still printed, with a `\n` for empty batches.
- Map renamed to `key`/`value` (datatype_nested.go:384), and the map
  nullability errors.
- Decimal math matches `big.Float.SetInt/Quo/Text('g',p)`: `prec==0 → 1`,
  `shouldRoundUp` and `roundUp` (scale is i8, so `Pow10` never under- or
  overflows).
- Date32/Date64 (truncating Date64), Time32/Time64, and Timestamp below
  seconds with wrapping multiply; Duration `"%d%s"`; interval objects.
- Binary nil buffer → `null`, including compressed zero-length buffers (Go's
  `Resize(0)` leaves the buffer nil).
- Element-wise list marshal matches each child's `MarshalJSON`. Int8/UInt8
  pass through float64 in Go, but small integers format identically.
- First-message peek, wrapping of `could not read message schema`, `<4`-byte
  header text, EOF/status classification at each stage, initial-dictionary EOF
  semantics, and the Go message missing its `)`.
- Session ownership: capacity-1 channels with ack-before-send (`try_send` cannot
  see `Full`); the batch is dropped before the blocking write; the decoder moves
  by value through the worker; scratch (1 MiB) and staging (4 MiB) are released
  after each batch, including the panic path.
