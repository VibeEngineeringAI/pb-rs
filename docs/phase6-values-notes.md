# Phase 6 value rendering notes

`src/tail/values.rs` renders a decoded Arrow `RecordBatch` as the bytes that
pinned Go (`07af390`) writes for one tail batch. Go calls
`array.RecordToJSON(record, &buf)` and then `fmt.Println(buf.String())`. The
renderer produces only the `RecordToJSON` part; the session adds the Println
LF, including after zero-row batches and after a truncated batch.

## The encoder is goccy/go-json, not encoding/json

Arrow Go v13 `array/util.go` imports `github.com/apache/arrow/go/v13/internal/json`.
On non-tinygo builds that package aliases `github.com/goccy/go-json`, pinned at
v0.10.0 by Go's `go.mod`. Several observable differences from `encoding/json`
follow, all confirmed by the oracle below:

| Behaviour | goccy v0.10.0 as used by RecordToJSON |
| --- | --- |
| Object key order | Map keys are sorted by their **encoded** bytes plus the following separator. `"a!"` sorts before `"a"`, `" "` before `""`, and an HTML-escaped `<` (`<`) sorts after letters. |
| Duplicate field names | The row is a Go map, so the last column with a name wins. Shadowed columns are still evaluated (and can still panic). |
| Float digits and exponents | `strconv.AppendFloat(v, 'f'|'e', -1, bits)`: shortest round-trip digits, with a tie between two shortest candidates broken toward the even last digit (`2079541830868280.25` → `2079541830868280.2`). Exponents are not normalized: `1e-07`, `1e+21`, `3.4e+38`. `'f'` is used for 1e-6 <= abs < 1e21; float32 thresholds compare as float32. |
| float64 NaN/Inf | Encoder error `json: unsupported value: NaN|+Inf|-Inf`. `RecordToJSON` stops; Go keeps earlier complete rows and ignores the error. |
| float32 / float16 NaN/Inf | No finiteness check. The raw tokens `NaN`, `+Inf`, `-Inf` are written (invalid JSON) without error. |
| Strings | Encoder and Marshal both enable HTML escaping and UTF-8 normalization. Short escapes are used for `"` `\` `\n` `\r` `\t`. `<>&` and the other C0 controls become `\u00xx` (lowercase, so `\u0008` and `\u000c`, not `\b`/`\f`). U+2028/U+2029 are escaped. Each invalid UTF-8 byte becomes `\ufffd` (Go `utf8` acceptance ranges, one byte per error). DEL and valid non-ASCII pass through raw. |
| `[]byte` | Standard base64 with padding; a nil slice encodes as `null`. |

## Arrow GetOneForMarshal semantics

The renderer models Go's two stages for each row. **Gather** corresponds to
`GetOneForMarshal`, evaluated for every column in schema order. **Encode** is
the goccy encoding of the resulting map.

- List, LargeList, FixedSizeList and Map values call `json.Marshal` on their
  child slice during gather, and the result is a `json.RawMessage`. A failure
  there panics. goccy wraps both the slice's `MarshalJSON` error and the
  compaction of its output as `json: error calling MarshalJSON for type
  *array.<ChildType>: <cause>`. Within a list, float64 non-finite gives
  `json: unsupported value: ...`. A raw float32 token fails compaction with
  `unexpected character 'N'`, `unexpected character '+'`, or
  `strconv.ParseFloat: parsing "-": invalid syntax`. Map entries report
  `*array.Struct`. A panic preempts any encoder error in the same row, and Go
  prints nothing for that batch. A FixedSizeList that is itself an element of
  an enclosing list marshal (`List<FSL<…>>`, `FSL<FSL<…>>`) is encoded by
  `FixedSizeList.MarshalJSON`, which *returns* the inner error. The outer level
  then panics with both wrappers: `…*array.FixedSizeList: json: error calling
  MarshalJSON for type *array.Float64: …`. Dictionary values inside a list are
  marshalled by `Dictionary.MarshalJSON`, which gathers every element before
  encoding any. A panic in a later element therefore beats an encoder error in
  an earlier one. The renderer returns `RenderError::Panic` with
  the output buffer restored; the session owns the process outcome.
- Struct values become maps whose fields are sorted and de-duplicated like
  rows. A null struct is `null`.
- Map entry fields are always named `key` and `value`: Go's IPC reader rebuilds
  the type with `arrow.MapOf`, whatever names the stream uses.
- Unions render as `[type_code, value]`. They are `null` only when the
  selected child's *validity bitmap* marks the (dense: offset) index null.
  Children whose value is nil for another reason render `[type_code,null]`:
  a dictionary with a null value at a valid key, a Null array (Go clears its
  bitmap), an REE with a null value, or a union whose own child is null. Lists
  of unions slice the union; Go slices sparse children with it, so values stay
  aligned.
- Dictionary values render the value at the key. A null key is `null`.
  Replacements are handled naturally, since each batch carries its values.
- RunEndEncoded renders the value at the physical index.
- Timestamps ignore the timezone and format in UTC as
  `2006-01-02 15:04:05.999999999`. Second units use `time.Unix(v, 0)`; other
  units use `time.Unix(0, v*multiplier)` with int64 wrap-around, which is why
  nanosecond extremes print years 1677/2262 and ms 1e13 wraps. Every field is
  derived the way Go 1.25 `Time.Format` does it, from the wrapping int64 sum
  `unixSec + 9223372028741760000` (unixToInternal + internalToAbsolute) read
  as uint64. Second values below -9223372028741760000 therefore wrap:
  `math.MinInt64` prints `292277026596-12-04 15:30:08`.
- Date32 uses `Unix(0,0).AddDate(0,0,d)`. Date64 truncates milliseconds toward
  zero into days, so -1 ms is `1970-01-01`. Years use Go's `appendInt(year, 4)`,
  e.g. `0000`, `10000`, `33658`.
- Time32/Time64 print the time of day of `time.Unix(0, v*multiplier)`, so
  negative values and values past 24 h wrap.
- Float16 uses Go v13 `float16.Num.Float32`, which keeps exponent 0 for half
  subnormals and so produces float32 subnormals: bits `0x0001` print
  `1.148e-41` (shortest float32), not 2^-24. Normal, infinite and NaN values
  convert exactly.
- Durations print `<int><s|ms|us|ns>`. Intervals print `{"months":n}`,
  `{"days":d,"milliseconds":m}`, and `{"months":m,"days":d,"nanoseconds":n}`.
- Decimal128/256 values are computed as
  `big.Float.SetInt(v).Quo(math.Pow10(scale)).Text('g', precision)`. The
  quotient precision is max(64, bit length of v), rounded to nearest even.
  `math.Pow10` is reproduced through its table products, so large scales carry
  float64 artifacts, e.g. `1.0000000000000000225119017654396602111` for
  Decimal(38,38) 99…9. The renderer emulates this exactly with a small
  owned bignum.
- Binary/LargeBinary/FixedSizeBinary render base64, except when the decoded
  data buffer has length 0. Go's IPC reader turns a zero-length buffer into a
  nil slice, so an all-empty binary array renders every value as `null`. A
  batch with any non-empty value renders `""`.

## Invalid UTF-8 and the wire schema

Go accepts Utf8 columns containing invalid UTF-8. The session decodes with a
wire schema in which every Utf8/LargeUtf8 at any depth becomes
Binary/LargeBinary, so validated arrow-ipc decoding needs no
`skip_validation`. The renderer is built from the original schema. Wherever the
plan says Utf8/LargeUtf8, it accepts either string or binary arrays and renders
them as Go strings. Genuine binary columns still render as base64.

## Unsupported types

`BatchRenderer::new` rejects types that Go Arrow v13's IPC reader cannot read:
Utf8View, BinaryView, ListView, LargeListView, Decimal32, Decimal64, nullable
Map entries, and nullable Map keys. The session maps these to its fatal
reader-initialization path. Go's exact diagnostic text for each case has not
been characterized.

## Resource ownership

A renderer belongs to one subscription and holds no batch borrows. List values
are marshalled into per-nesting-depth scratch buffers, which are reused across
batches. After every batch (Ok, Encode or Panic), scratch whose aggregate
capacity exceeds 1 MiB is released. `scratch_capacity()` reports what is
retained. With `diagnostics`, `resources::renderer_scratch` is called with the
batch peak and then with the retained bytes. These figures are owned scratch
capacity, not allocator or RSS measurements. The output staging buffer belongs
to the session.

## Oracle evidence

The Go oracle is `harness/values-oracle/main.go`, a test-data generator, not a
product feature. It builds with the root Go module, so Arrow v13.0.0 and goccy
v0.10.0 are the versions pinned by `go.mod`/`go.sum`. It holds no credentials
and makes no network calls. For each of 44 cases it builds Arrow records,
writes them as an IPC stream, reads them back with Go's IPC reader, and
replays tail's per-batch buffer/Println/panic behaviour. It writes four files
into `tests/fixtures/arrow-go-v13/`: `<case>.arrows` (the stream),
`<case>.expected` (exact stdout bytes), `<case>.panic` (Go panic text, empty
if none) and `<case>.errors` (per-batch encoder errors, ignored by tail).
`manifest.json` records the generator path and SHA-256, the Go toolchain, the
Arrow and goccy module versions, the regeneration command, the case list and a
SHA-256 for every file. The corpus is about 408 KiB in 177 files. All bytes are
produced by Go; nothing in it comes from Rust.

Regenerate from the repository root (output is deterministic; two runs give
identical manifests):

```
GOTOOLCHAIN=local <go1.25.4>/bin/go run ./harness/values-oracle tests/fixtures/arrow-go-v13
```

Cases: key ordering; all integer widths and bools/nulls; float64/float32/float16
layout; raw non-finite tokens; float64 errors across five batches, including
zero-row and first-row errors; escaping and invalid UTF-8 in Utf8 and LargeUtf8;
binary nil buffers; LargeBinary/FixedSizeBinary; timestamps in every unit,
timezone and wrap extreme; dates, times and durations; intervals; eight decimal
precision/scale families, including negative and over-precision scales; lists,
large, fixed and nested lists; structs, list of struct and null struct; maps;
sparse and dense unions with non-sequential codes; dictionary replacement with
an unused NaN value; REE; and every panic boundary (float64/±Inf, float32
NaN/±Inf, struct inside a list, list inside a list, float16, map, panic over
error, and error row before panic row). Decoder review r1 added ten cases:
union children that are null by bitmap versus nil through a dictionary, Null,
REE or nested union; float16 subnormal bit patterns; FixedSizeList at top
level, inside a list, inside a FixedSizeList, and a non-panicking list of FSL;
extreme Timestamp(second) and Date64 values; dictionary-of-list precedence;
and lists of sparse and dense unions. Invalid UTF-8 appears at top level, in
lists, in a sparse union, in dictionary values and in REE values.

The unit test `oracle_corpus_matches_go_bytes` runs in a normal `cargo test`
with no Go toolchain needed. It checks file sizes and the case list against
the manifest, requires all 44 cases, and replays every stream twice: as
arrow-rs decodes it, and after rewriting strings to binary at every depth as
the session's wire schema does. All 44 cases are byte-identical on both paths,
including panic messages and encoder errors. A deliberately corrupted copy of
the corpus makes it fail. `PB_VALUES_ORACLE_DIR=<dir>` points the test at
another corpus, e.g. a fresh regeneration. The test does not recompute
SHA-256, because no hashing crate is a dependency; `manifest.json` carries the
digests for review.

## Known limits and unverified cases

- Field names that are invalid UTF-8 are accepted by Go but cannot appear in
  an arrow-rs schema. Distinct invalid names that normalize to the same key
  would also be emitted in nondeterministic order by Go.
- A compressed IPC binary buffer with non-zero compressed length but zero
  uncompressed length remains unprobed. The fresh review finds Go
  `Resize(0)` on a fresh buffer stays nil, suggesting the same `null` output
  as Rust; the earlier asserted empty-string divergence was unsupported.
- Extension types render as their storage type; Go registers no extension
  types by default. Not oracle-tested.
- Dictionary-of-list precedence is matched for a dictionary that is the
  direct child of a list (oracle case). Deeper combinations, such as a
  dictionary inside a struct inside a list, follow the same per-array
  MarshalJSON rules but are not separately oracle-tested.
- Date32/Date64 go through Go's `AddDate` path. These are tested up to the
  int32 day range and Date64 `math.MinInt64`/`math.MaxInt64`, with no wrap
  found.
- Go's diagnostic text for unsupported schema types (see above) is not
  reproduced.

## Decoder probe ledger (review r2)

`tests/fixtures/arrow-go-v13-decoder/` preserves the decoder review r2
evidence. It contains ten IPC streams (four valid controls and six hostile or
edge streams) and what pinned Go Arrow v13 actually does with each. The
streams were dumped by Rust code in the reviewer's scratch copy; that
generator is kept verbatim, but not compiled, in
`harness/decoder-oracle/r2_stream_dump.rs.txt`. The committed `.arrows` bytes
are the fixtures. Their SHA-256 values match the reviewer's scratch streams.
The Go side is the reviewer's probe, `harness/decoder-oracle/main.go`,
preserved byte for byte (sha256
`ed80a49121c32222f9909a0837d900d98323070ae77c7fb7b9a3d642a28609d9`). It builds
with the root module. It reads a stream with `ipc.NewReader`, then for each
record prints `NumRows`, runs `RecordToJSON` and `Println`s the buffer, with no
`recover`. `harness/decoder-oracle/run.sh` builds it with
go1.25.4 `-trimpath` and runs each stream under `ulimit -v` 4 GiB and
`timeout 10`. It records `<case>.stdout`, `<case>.stderr` and `<case>.status`,
plus `manifest.json` (probe hash, toolchain, Arrow v13.0.0, goccy v0.10.0,
bounds, per-file SHA-256). Two consecutive runs produced identical manifests,
including the panic trace.

```
GO=<go1.25.4>/bin/go harness/decoder-oracle/run.sh
```

The probe exits 1 on a reader-initialization error and 0 when `Read` returns
any error, so its status is not tail's. The tail column is derived from
`cmd/tail.go`: a reader-init error is fatal, EOF on `Read` reconnects, and any
other `Read` error is fatal.

| Case | Go stdout (exact) | Go stderr (first lines) | Probe exit | Tail meaning |
| --- | --- | --- | --- | --- |
| `p1_fsb_valid` (control) | `{"b":"Bw=="}\n\n` | `NumRows=1`, `read end: EOF` | 0 | Renders, then reconnects |
| `p1_fsb_negative` (N1a: FixedSizeBinary width -1, width-1 body) | empty | `NumRows=1`, `panic: runtime error: slice bounds out of range [:-1]` in `FixedSizeBinary.Value` via `GetOneForMarshal` | 2 | Go panics while rendering; nothing printed |
| `p2_fsl_valid` (control) | `{"f":[1,2,3,4]}\n\n` | `NumRows=1`, `read end: EOF` | 0 | Renders |
| `p2_fsl_overflow` (N1b: FSL size 4, node length i64::MAX) | `{"f":[1,2,3,4]}\n\n` | `NumRows=1`, `read end: EOF` | 0 | **Go accepts** and renders by the record length |
| `p3_union_128` (control: sparse union, 128 Null children, no typeIds; schema only) | empty | `read end: EOF` | 0 | Initializes, then reconnects |
| `p3_union_129` (N1c: 129 children, no typeIds) | empty | `init error: arrow/ipc: unknown error while reading: arrow: union type code out of bounds` | 1 | Fatal reader initialization |
| `p4_lz4_valid` (control: LZ4 body) | `{"n":1}\n{"n":null}\n{"n":3}\n\n` | `NumRows=3`, `read end: EOF` | 0 | Renders |
| `p4_lz4_short` (N3: compressed buffer length 3) | empty | `read end: arrow/ipc: unknown error while reading: unexpected EOF` | 0 | Fatal read error (not EOF) |
| `p5_nonnull_top` (N2: `n: Int64 not null` holding a null) | `{"n":1}\n{"n":null}\n{"n":3}\n\n` | `NumRows=3`, `read end: EOF` | 0 | **Go accepts** and renders |
| `p5_nonnull_nested` (N2: `List<item Int64 not null>` holding a null) | `{"l":[1,null]}\n\n` | `NumRows=1`, `read end: EOF` | 0 | **Go accepts** and renders |

These record Go's behaviour only. Rust parity for each row is the session
owner's (N1–N4 fixes and their tests), which may consume these fixtures. A
case is not claimed as matched here unless a Rust test asserts it. Rows N1a,
N1c and N3 are hostile inputs where Go itself panics or fails; an explicit
divergence decision there (for example, a safe Rust rejection instead of a Go
panic) belongs to the session/parent.

## Decoder probe ledger (review r3)

`tests/fixtures/arrow-go-v13-decoder-r3/` preserves the decoder review r3
streams (`docs/phase6-decoder-review-r3.md`) and pinned Go's measured output.
It holds 30 `.arrows` streams plus `<case>.stdout/.stderr/.status` and
`manifest.json`: 121 files, about 544 KiB. The streams are byte-identical to
the reviewer's `~/.cache/pbrs-r3/streams`. They were dumped by the scratch
tests `r3_probes`, `r3_probes_b` and `r3_probes_c` (run with
`R3_STREAMS=<dir>`). That generator and the session-test helpers it uses are
kept verbatim, not compiled, in `harness/decoder-oracle/r3_stream_dump.rs.txt`,
along with the scratch source's sha256 and the scratch-only
`flatbuffers = "=25.12.19"` dev-dependency note.

The Go side is the unchanged probe `harness/decoder-oracle/main.go`, run by
`run.sh` with go1.25.4 `-trimpath` under `ulimit -v` 4 GiB, `ulimit -f` 16 MiB,
`timeout 10`, and disk-backed `TMPDIR`/`GOTMPDIR` outside `/tmp`. `run.sh`
gained an optional second argument naming the preserved generator for the
manifest. With no arguments it behaves as before: rerunning it on a copy of
the r2 corpus reproduced the r2 manifest byte for byte.

```
GO=<go1.25.4>/bin/go harness/decoder-oracle/run.sh \
  tests/fixtures/arrow-go-v13-decoder-r3 harness/decoder-oracle/r3_stream_dump.rs.txt
```

Two consecutive runs produced identical manifests. For the 23 cases where the
reviewer kept Go output (`~/.cache/pbrs-r3/goout`), stdout and stderr are
byte-identical to the reviewer's. Seven cases were first measured here:
`g11_list_depth_24/31/32` and `g12_struct_depth_30..33`.

The probe status mapping is as for r2: probe exit 1 means a reader-init error
(fatal in tail). Exit 0 with `read end: EOF` means tail reconnects. Exit 0 with
any other `read end:` error is fatal in tail.

| Case | Go stdout (exact) | Go stderr / probe exit | Tail meaning |
| --- | --- | --- | --- |
| `g0_int_control` (Int64 101..107) | seven `{"n":10x}` rows + `\n` | `NumRows=7`, EOF / 0 | renders |
| `g1_int_node_gt_rows` (node 7, batch 5) | `{"n":101}`…`{"n":105}` + `\n` | `NumRows=5`, EOF / 0 | renders 5 rows |
| `g2_int_node_lt_rows` (node 7, batch 9) | empty | `read end: arrow/ipc: unknown error while reading: arrow/array: mismatch number of rows in column "n": got=7, want=9` / 0 | fatal |
| `g3_int_negative_batch_valid_nodes` (node 7, batch -1) | all seven rows + `\n` | `NumRows=7`, EOF / 0 | renders 7 rows |
| `g3b_int_zero_batch_valid_nodes` (node 7, batch 0) | `\n` | `NumRows=0`, EOF / 0 | empty batch |
| `g4_fsl2_control` | `{"f":[1,2]}\n\n` | EOF / 0 | renders |
| `g4_fsl2_node_max` (FSL(2) node i64::MAX, batch 1) | `{"f":[1,2]}\n\n` | EOF / 0 | renders |
| `g5_fsl2_node2_rows1` (FSL(2) node 2, batch 1) | `{"f":[1,2]}\n\n` | EOF / 0 | renders 1 row |
| `g6_fsl_negative_size` (FSL(-1) + batch) | empty | `init error: arrow/ipc: unknown error while reading: arrow: invalid size` / 1 | fatal init |
| `g6b_fsl_negative_schema_only` | empty | same init error / 1 | fatal init |
| `g9_fsl_zero_size` (FSL(0) + batch) | empty | same init error / 1 | fatal init |
| `g9b_fsl_zero_schema_only` | empty | same init error / 1 | fatal init |
| `g7_ree_control` | `{"r":5}\n{"r":5}\n{"r":6}\n\n` | EOF / 0 | renders |
| `g7_ree_nullable_run_ends` (run_ends declared nullable) | `{"r":5}\n{"r":5}\n{"r":6}\n\n` | EOF / 0 | renders |
| `g8_map_null_key` | empty | `read end: arrow/ipc: unknown error while reading: arrow/array: map array keys array should have no nulls` / 0 | fatal |
| `g10_union_ids_0` / `g10_union_ids_none` (controls) | `{"u":[0,7]}\n\n` | EOF / 0 | renders |
| `g10_union_ids_256` | empty | `init error: arrow/ipc: could not decode schema from message schema: arrow/ipc: could not convert field 0 from flatbuf: arrow/ipc: could not convert field type: union type id out of bounds` / 1 | fatal init |
| `g11_list_depth_{16,24,31,32,40,63,64}` | `{"l":` + depth×`[` + `1` + depth×`]` + `}\n\n` | EOF / 0 | renders |
| `g11_list_depth_65` | empty | `read end: arrow/ipc: unknown error while reading: arrow/ipc: nested type limit reached` / 0 | fatal at first read |
| `g12_struct_depth_{30..33}` | depth nested `{"s":…1…}` + `\n\n` | EOF / 0 | renders |

**Measured Go row-count rule.** Every row above agrees with this rule. The row
count is the record batch length. If that is negative, Go uses the first
column's length instead. Each column must then have `Len() >= rows`;
otherwise Go fails with `arrow/array: mismatch number of rows in column "<name>":
got=<len>, want=<rows>`. Longer columns are rendered for `rows` rows only. A
FixedSizeList node length is retained as the array length by Go's
`loadFixedSizeList`, so the same record-length check applies. Rendering visits
only the record's rows; the measured oversized-node cases therefore do not
require visiting all declared list elements.
The earlier Null/field-less-struct negative-row characterization is the case
where the first column is negative too, so the row count is negative.

**Remaining malformed-producer divergences** are recorded here as Go
behaviour only. The Rust side is the session owner's: R3-1, R3-2, R3-3 and
R3-5 are being fixed there and will consume these fixtures. Until a Rust test
asserts a row, no parity is claimed.
- R3-4: Go accepts a node longer than the batch (g1, g5, g4), a negative batch
  length with valid nodes (g3), and a zero batch over a 7-row node (g3b).
  Rust's exact-length rule rejects them (review r3); that is a
  malformed-producer divergence to be declared or emulated.
- g2 and g8 are fatal in both, with different text, which is a diagnostic
  difference only.
- Error-text differences for schema and row-count failures remain on the
  diagnostic ledger.

## Decoder probe ledger (round 4: endianness and shared dictionary IDs)

`tests/fixtures/arrow-go-v13-decoder-r4/` promotes the session agent's
cache-only round-4 probes to durable evidence. It holds 15 `.arrows` streams
plus `<case>.stdout/.stderr/.status` and `manifest.json` (61 files, about
212 KiB; manifest sha256
`d4fa412a7765a2908838f111530bd67de68b6471c18f345361ea81e4dd19dba2`). The streams
were dumped by the scratch test module `r4probe`, appended to a scratch copy of
`src/tail/session/tests.rs` and run with
`R4_STREAMS=streams cargo test --lib tail::session::tests::r4probe -- --nocapture`.
It builds flatbuffer schema headers directly (scratch `flatbuffers`
dev-dependency) and uses that module's helpers. The generator is kept
byte-for-byte, not compiled, as `harness/decoder-oracle/r4_stream_dump.rs.txt`
(sha256 `6fe4a2287171d8dc702cf567c1544404d4dbf9be4793f0750ef4167a7b8fb300`).
All 15 stream SHA-256 values match the session agent's cache README. The
committed bytes, not the generator, are the fixtures.

The Go side is the unchanged probe (`main.go`, sha256
`ed80a49121c32222f9909a0837d900d98323070ae77c7fb7b9a3d642a28609d9`) and
`run.sh`, with go1.25.4 `-trimpath`, `ulimit -v` 4 GiB, `ulimit -f` 16 MiB,
`timeout 10`, and disk-backed temporary storage outside `/tmp`:

```
GO=<go1.25.4>/bin/go harness/decoder-oracle/run.sh \
  tests/fixtures/arrow-go-v13-decoder-r4 harness/decoder-oracle/r4_stream_dump.rs.txt
```

I reran it independently twice. The two manifests are identical, and all 45
output files (stdout, stderr, status) are byte-identical to the session agent's
cache `goout`. The r2, r3 and 44-case value manifests are unchanged.

| Case | Go stdout (exact) | Go stderr / probe exit |
| --- | --- | --- |
| `r4_le_int64` (control) | `{"n":1}\n{"n":-2}\n{"n":300}\n\n` | `NumRows=3`, `read end: EOF` / 0 |
| `r4_be_int64` (Schema endianness Big, big-endian Int64 body) | `{"n":1}\n{"n":-2}\n{"n":300}\n\n` | `NumRows=3`, EOF / 0 |
| `r4_be_int64_lz4` (big-endian, LZ4 frame body) | `{"n":1}\n{"n":-2}\n{"n":300}\n\n` | `NumRows=3`, EOF / 0 |
| `r4_be_int64_schema_only` | empty | `read end: EOF` / 0 |
| `r4_be_utf8` (big-endian offsets) | `{"s":"ab"}\n{"s":"cde"}\n\n` | `NumRows=2`, EOF / 0 |
| `r4_be_dict` (big-endian dictionary + indices) | `{"d":"yz"}\n{"d":"x"}\n{"d":"yz"}\n\n` | `NumRows=3`, EOF / 0 |
| `r4_dict_same_utf8` (two fields share id 0, both Utf8) | empty (schema only) | `read end: EOF` / 0 |
| `r4_dict_utf8_nullability` (shared id, Utf8 value fields differing only in nullability) | empty | `read end: EOF` / 0 |
| `r4_dict_list_item_names` (shared id, `List` item names differ) | empty | `read end: EOF` / 0 |
| `r4_dict_utf8_binary` (shared id, Utf8 vs Binary) | empty | `init error: arrow/ipc: could not decode schema from message schema: arrow/ipc: could not convert field 1 from flatbuf: arrow/ipc: could not convert field type: arrow/ipc: conflicting dictionary types for id 0` / 1 |
| `r4_dict_int32_utf8` (shared id, Int32 vs Utf8) | empty | same text, `field 1` / 1 |
| `r4_dict_list_item_nullability` (shared id, `List` item nullability differs) | empty | same text, `field 1` / 1 |
| `r4_dict_struct_field_names` (shared id, struct child names differ) | empty | same text, `field 1` / 1 |
| `r4_dict_nested_conflict` (conflict inside a struct child) | empty | `init error: … could not convert field 1 from flatbuf: arrow/ipc: could not convert field child 1: arrow/ipc: could not convert field type: arrow/ipc: conflicting dictionary types for id 0` / 1 |
| `r4_dict_child_before_parent` (one field: a dictionary-encoded `List`, id 0, whose Utf8 item is also dictionary id 0) | empty | same text, but `field 0` / 1 |

Go checks shared dictionary IDs with `arrow.TypeEqual` on the value types of
the *original* schema, as reproduced in the session agent's `goeq.txt` notes.
List and FixedSizeList item nullability counts, list item names do not, and
struct and union child names and nullability count. The nullability of the
dictionary-encoded field itself does not count. The session agent's fix
applies that equality to the original schema before the wire rewrite; the
rewrite (Utf8 to Binary, nullable at every depth) would otherwise hide Go's
Utf8-versus-Binary and nullability conflicts. Rust parity is asserted by the
session's tests, not by this ledger.

**Declared capability limitation: native-endian only.** Go v13 byte-swaps
non-native-endian IPC bodies and renders them correctly (rows above). The Rust
reader supports native (little-endian) bodies only. It rejects a schema whose
endianness is not native at reader initialization, as a fatal tail error. This
is a declared divergence, not parity: Go renders `r4_be_*`, and Rust refuses
them. (The pre-fix Rust outcomes in the session's cache `rust.txt`, raw
misread integers and offset errors, are superseded by that rejection, which
the session's tests assert.)

**Residual, source-only and unprobed (low):** empty or duplicate union type
IDs, decimal precision/scale bounds, flatbuffer header strings (field names,
metadata keys and values) that are invalid UTF-8 or contain NUL, and absent
optional flatbuffer fields. Behaviour there was compared by reading source
only; no stream was run in either implementation.

## Review wave (values): float ties and empty interval list children

`docs/phase6-review-wave-values-opus.md` measured two value defects in the
renderer as of sha256
`037183a7939fc11d3921c36d086fad023d9a6c0004839096b28ab140cd56ea6b`. Both are
fixed in `src/tail/values.rs` and pinned by a new Go corpus.

**V1, shortest-float ties.** The digits came from core's `{:e}`, which rounds
a tie between two shortest round-trip decimals up. Go `strconv` picks the even
last digit. The renderer now takes the digits from `zmij::Buffer::format_finite`
(an existing dependency, with the same tie rule as Go, as the SQL renderer
already relies on). `push_go_float` parses zmij's text into sign, digits and
decimal exponent, then applies the unchanged goccy 'e'/'f' threshold and layout.
Non-finite handling is unchanged: float64 is an encoder error, while float32
and float16 write a raw token and set the compaction flag. Examples, now
byte-identical to Go: f64 `0x431d8d54407f74e1` → `2079541830868280.2`,
`0xc24804fc47d09200` → `-206325714849.14062`; f32 `0x4729d710` → `43479.062`;
f16 `0x0c00` → `0.00024414062`. The ignored test
`go_float_vectors_match_every_bit_pattern` runs the real `push_f64`/`push_f32`
and Go's float16 mapping over the reviewer's Go-generated `vectors.tsv`
(sha256 `c4dec043ad6aebc7bfc966af0aab41d47f4b95ea3ec4cda2737d441c25ef9472`,
kept in the review cache, not the repo). Result: 120,465 finite f64, 120,486
f32 and all 65,536 f16 bit patterns, 0 mismatches.

**V2, List/LargeList of an empty interval child.** Go's `MonthInterval`,
`DayTimeInterval` and `MonthDayNanoInterval` `MarshalJSON` marshal their
stored slice directly when `NullN() == 0`. Go's IPC reader turns a zero-length
buffer into a nil slice, which goccy writes as `null`. So when a List or
LargeList column's entire interval child is empty, every non-null element
renders `null`, where `[]` would be printed for any other element type or for
a non-empty child. The renderer now records `nil_child` for List/LargeList
columns whose element plan is one of the three intervals and whose child array
is empty, and gather writes `null` for those elements. FixedSizeList (size ≥ 1
is enforced at init, so the child is never empty for a row) and Map (Struct
child) are unchanged.

*Declared caveat.* Go keys on the declared IPC buffer length being 0. arrow-rs
exposes the child array, sliced to its length, so the renderer keys on the
child array being empty. A writer that declares a padded, non-zero buffer
length for an empty interval child would make Go print `[]` while Rust prints
`null`. Mainstream writers, including Go's and arrow-rs's, declare 0. This was
not probed.

**I1, measured by the fresh Opus correction review.** goccy encodes map values in Go's
randomized map order and sorts the keys afterwards. When one row or struct
map holds several non-finite float64 values, which error goccy reports varies
between runs. At top level the message is ignored by tail. Inside a list (for
example `List<Struct{a: NaN, b: +Inf}>`) it becomes the panic text, which can
read `…: NaN` or `…: +Inf`. Rust reports the first in sorted key order, which
is always one of Go's possible messages. Stdout is empty and the exit is 2
either way. For such rows, the panic-message parity claimed in this ledger
means "one of Go's possible messages", not exact repetition. The reviewer
ran 2,000 pinned-Go marshals of the two-error list row: 1,725 selected NaN and
275 selected +Inf, all with empty stdout. This is a specific nondeterministic
panic boundary, not a generic diagnostic waiver.

**Corpus.** `tests/fixtures/arrow-go-v13-values-wave/` (9 cases, 37 files,
11,579 file-content bytes; about 80 KiB allocated on this filesystem) is generated by `harness/values-oracle-wave/main.go`. It uses
the same file layout as the 44-case corpus. The first eight cases are the
reviewer's `probe-e2e` cases built the same way: the five V2 cases, two
controls (`list_month_interval_mixed_control`, `list_int64_all_empty_control`)
and `float_shortest_ties`. The regenerated `.arrows` and `.expected` files are
byte-identical to the reviewer's `e2e-out` streams and stdout. The ninth case,
`float_ties_nested`, adds a negative f64 tie, scientific-notation f64/f32
values, ties inside a struct and f16 ties inside a list. Regenerate from the
repository root with go1.25.4; two runs gave identical manifests:

```
GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=readonly <go1.25.4>/bin/go run -trimpath \
  ./harness/values-oracle-wave tests/fixtures/arrow-go-v13-values-wave
```

The manifest records the generator sha256, `go1.25.4`, Arrow `v13.0.0`, goccy
`v0.10.0`, the case list and per-file sha256. The normal unit test
`values_wave_corpus_matches_go_bytes` replays all 9 cases through the
renderer, both as decoded and through the session's Utf8-to-Binary wire
rewrite, comparing stdout, panic text and encoder errors. Before the fix it
failed for exactly the seven defect cases: `float_shortest_ties`,
`float_ties_nested` and the five V2 cases, with both controls passing. After
the fix it passes. The original 44-case corpus, its generator and its
manifest are unchanged. The session's production-path replay
(`session/tests.rs`) passes all nine streams in text and JSON through the
real worker. The immutable R6 process replay also passes all nine in both
modes; see [focused R6 evidence](evidence/phase6/focused-review-r6.json).

The [fresh Opus values correction review](phase6-review-wave-values-opus-r2.md)
closes V1/V2, independently repeats the exhaustive vector test with zero
mismatches, and checks ten additional cache-only Go cases. The exhaustive
vector generator/data remain cache-only and the test skips 29 non-finite f64
error rows; the ordinary corpus and unit tests cover their error policy.
The wave generator's Go constant `-0.0` is positive zero after constant
conversion. Negative-zero coverage comes from the bit-pattern vectors and
the reviewer's actual `math.Copysign` probe, not from that constant. The
preserved nine-case corpus has not been regenerated to disguise this detail.

## Whole-source R6 diagnostic residual (L-1)

A malformed producer can send a RecordBatch where an opening DictionaryBatch
is required, with its row count equal to a declared dictionary ID. The fresh
Opus reviewer ran pinned Go 1.25.4/Arrow v13 with a zero-row batch and dictionary
ID 0: both the subsequent-dictionary and immediate-EOF variants fail with
`arrow/ipc: unknown error while reading: arrow/ipc: field metadata out of bound`,
without stdout. The Rust branch is source-inspected, not process-tested for
these exact streams: it fails at the same decode stage with
`arrow/ipc: IPC stream did not have the expected (1) dictionaries at the start of the stream`.
This is a declared malformed-producer diagnostic difference, not a parity pass.
The existing unknown-ID case is separately characterized; Go's unchecked
reinterpretation can yield schema-dependent text, so no broader text emulation
or generic diagnostic waiver is introduced. The probe and identities are in
[the whole-source review](phase6-whole-source-review-opus-r6.md) and its indexed
acceptance evidence.
