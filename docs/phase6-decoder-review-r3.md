# Phase 6 decoder/value/session review, round 3

This is a fresh, independent review of the native tail IPC decoder, metadata
validation, value renderer and session lifecycle. It is not the final
whole-phase acceptance review. No production source was edited, and nothing
was committed. I ran every probe in a scratch copy (`~/.cache/pbrs-r3/probe`)
with its own Cargo target, Go cache and disk-backed `TMPDIR`/`GOTMPDIR`. I did
no timing, profiling, soaks or production access. Transport protocol behavior
is outside this review; only the session's integration boundary was checked.

Labels: **MEASURED** means a bounded probe ran. **SOURCE** means the code was
traced but not executed.

## Snapshot reviewed

Base `4a812d5`, untracked `src/tail/`. The hashes were identical at the start
and at 2026-10-14T12:12Z:

| File | SHA-256 |
| --- | --- |
| `session.rs` | `827ee65808657dc6fc6b7d9c6ef00849b68272be91f2c68db310521f0d2afef5` |
| `session/tests.rs` | `8fad4417e818761dcb0819154e22818b7f8ff16fa3043844cde1902fd8b32015` |
| `validation.rs` | `332dafec262652e26738d5edb1aa77ab61dc5dd3aac8e68993c4ef414bae6625` |
| `values.rs` | `037183a7939fc11d3921c36d086fad023d9a6c0004839096b28ab140cd56ea6b` (unchanged since r2) |
| `resources.rs` | `944d3c88e6547a68a69444eb72c5b5a472ae83f0caaf67949b96443bd1e38f27` |
| `mod.rs` | `0fd7102d65f863c4fd079c893c51627553e0cb15c3506dba37e42de62fa3c0c7` |
| `transport.rs` | `558c7ed96f3cb7f11b023ca2c7bf16d2fcadf005e19ae62684f79db22faea769` (boundary only) |

I read `docs/rust-phase6.md`, decoder reviews r1 and r2 in full,
`docs/phase6-values-notes.md` and `docs/evidence/phase6/integration-checkpoint.md`.
I also read the Arrow Go v13.0.0 sources `ipc/reader.go`, `ipc/file_reader.go`,
`ipc/metadata.go`, `array/record.go` and `datatype_nested.go`, and the arrow-rs
60.0.0 sources `arrow-ipc` `convert.rs`/`reader.rs`, `arrow-data` `data.rs` and
`arrow-schema` `fields.rs`.

## Verification of round-2 findings

| R2 item | Status | Evidence |
| --- | --- | --- |
| N1a negative FixedSizeBinary width | **Resolved, declared divergence** | `schema_types` rejects the width at initialization, including inside dictionary values (test `schema_level_hazards_fail_reader_initialization`). The fixture test counts `p1_fsb_negative` as a divergence: Go panics while rendering and exits 2. Go would also accept the schema and render null-only rows, or reconnect on a schema-only stream; Rust's init rejection covers those too. The comment says so. |
| N1b FSL node length overflow | **Resolved for the overflow; see R3-4 for the rest of the class** | The walker rejects `len × size` overflow (test `fixed_size_list_lengths_that_overflow_are_rejected`, size 4). Size 2 × `i64::MAX` does not overflow. It still fails with arrow's ordinary "Values length … is less than" error, while Go renders `{"f":[1,2]}` (MEASURED, g4). This rejection is not declared. |
| N1c 129-child union without typeIds | **Resolved, parity** | `ipc_schema` pre-walk (recursive) uses Go's exact text. The `p3_union_129` fixture matches `FLIGHT_READER + init`. |
| N2 non-nullable fields holding nulls | **Resolved for the reported shapes**; residual R3-5 | Wire fields are nullable at every depth. Fixtures `p5_nonnull_top`/`p5_nonnull_nested` match Go's bytes. A Map null key fails in both Go and Rust (MEASURED g8: Go `map array keys array should have no nulls`), so keeping keys non-nullable is correct. Run-end fields kept "as declared" leave one gap (R3-5). |
| N3 short compressed prefix | **Resolved, parity** | `length < 8` returns `unexpected EOF` before reading. Test `compressed_buffers_shorter_than_their_prefix_fail_like_go` covers LZ4 and ZSTD at lengths 1 and 7. Fixture `p4_lz4_short` matches. |
| N4 corpus bypassed production | **Resolved** | `go_value_corpus_replays_byte_exact_through_the_session` runs all 44 cases through `subscribe`/`pump`/worker. The old `skip_validation` test remains, test-only (`values.rs:2310`). |
| N5 doc drift | **Resolved** | `rust-phase6.md` now places schema verification on the worker. `tests.rs` cites `negative-row-go125.json` (Go 1.25.4). |
| Decoder fixture ledger | **Verified** | I rebuilt the probe from `harness/decoder-oracle/main.go` (sha `ed80a491…609d9`, which matches the manifest) with Go 1.25.4. Rerunning all 10 fixture streams under `ulimit -v 4 GiB`, `-f 16 MiB` and `timeout 10` gave identical status, stdout and full stderr for 10/10. All 40 manifest SHA-256 values match the files. |
| No catch_unwind / panic hook / production skip_validation | **Confirmed** | `grep` in `src/tail` finds only `resume_unwind` (re-raising a worker panic after join) and the test-only `unsafe`/`with_skip_validation` at `values.rs:2310`. `src/diagnostics.rs` `unsafe` is the counting allocator and `clock_gettime`, which are pre-existing and diagnostics-only. |

No probe in this round produced a Rust panic. That covers the 30 dumped streams
and the list-depth sweep from 41 to 62 below. They ran through the real worker,
with a `catch_unwind` around the harness in the scratch copy only.

## New findings (prioritized)

### R3-1. FixedSizeList size ≤ 0 is accepted at initialization; Go rejects it (MEASURED, medium)

Go v13 builds the type with `arrow.FixedSizeListOfField(n, …)`, which panics
`arrow: invalid size` for `n <= 0`. The reader recovers that panic during
initialization. arrow-rs `try_fb_to_schema` accepts any `listSize`, and
`schema_types` does not check it:

| Stream | Go 1.25.4 probe | Rust session |
| --- | --- | --- |
| g9: `FixedSizeList(0)` schema plus a 1-row batch | init error `arrow/ipc: unknown error while reading: arrow: invalid size` (tail: fatal) | Initializes and **renders** `{"f":[]}\n\n` |
| g9b: `FixedSizeList(0)`, schema only | same init error (fatal) | Initializes; EOF then **reconnects every 500 ms indefinitely** |
| g6b: `FixedSizeList(-1)`, schema only | same init error (fatal) | Initializes; EOF then reconnects indefinitely |
| g6: `FixedSizeList(-1)` plus a batch | same init error (fatal, init stage) | Fatal at the established read: `…Invalid argument error: FixedSizeList(-1 x Int8) has a negative list_size -1` |

This is a stage and outcome difference, not just diagnostic text: Go terminates
where Rust renders or retries forever. Fix: in `schema_types` (or the
`ipc_schema` pre-walk), reject `FixedSizeList` with size ≤ 0 at any depth.
Return `UNKNOWN_READ + "arrow: invalid size"` so the full message is Go's
`arrow/flight: could not create flight reader: arrow/ipc: unknown error while
reading: arrow: invalid size`. Today `schema_types` errors go through
`decode_error`, which would produce the wrong prefix. Add g6b/g9/g9b as
fixtures.

### R3-2. Union typeIds outside 0..=127 wrap through `as i8` (MEASURED, low–medium)

arrow-ipc converts typeIds with `ids.iter().map(|i| i as i8)`. An ID of 256
becomes 0 and is accepted. Go rejects any ID outside the i8 range at schema
conversion:

| Stream (sparse union, one Int8 child, value 7) | Go | Rust |
| --- | --- | --- |
| g10 typeIds `[0]` (control) | `{"u":[0,7]}` | same |
| g10 no typeIds (control) | `{"u":[0,7]}` | same |
| g10 typeIds `[256]` | init error `arrow/ipc: could not decode schema from message schema: arrow/ipc: could not convert field 0 from flatbuf: arrow/ipc: could not convert field type: union type id out of bounds` | **renders** `{"u":[0,7]}` |

IDs 128–255 become negative and fail in both implementations, with different
text. Fix: in the `ipc_schema` pre-walk, reject any explicit typeId outside
0..=127 using Go's text. Note that this text differs from the 129-implicit
case.

### R3-3. Valid schemas nested 61–64 levels fail initialization (MEASURED, low)

`ipc_message` uses `root_as_message`, which applies the flatbuffers default
verifier depth of 64. Nested lists of depth ≤ 60 pass. At depth 61–64 the
reader initialization fails with `arrow/ipc: unknown error while reading:
Nested table depth limit reached.` Go renders the same depth-63 and depth-64
streams correctly. At depth 65, Go fails at the first read with `arrow/ipc:
nested type limit reached`, which is fatal; a schema-only stream initializes
and reconnects. These streams are valid, so Rust loses valid output in this
narrow window. Fix options: verify the schema message with
`root_as_message_with_opts` and a larger bounded `max_depth`, and emulate Go's
65-level read-time limit. Otherwise, declare the window explicitly.

### R3-4. Top-level node length ≠ batch length: Go's rule is broader than the ledger (MEASURED, low; ledger exactness)

Go's `NewRecord` uses the batch length as the row count. When that length is
negative, it uses the first column's length instead. It then requires only
`column.Len() >= rows`. arrow-rs requires every column length to equal the
row count exactly. Measured on Int64 [101..107] and FSL(2):

| Stream | Go | Rust |
| --- | --- | --- |
| g1: node 7, batch 5 | renders 5 rows | fatal `all columns in a record batch must have the specified row count` |
| g3: node 7, batch −1 | **renders all 7 rows** | fatal `invalid record batch length -1` |
| g3b: node 7, batch 0 | `\n` (empty batch) | fatal (row count) |
| g4: FSL(2) node `i64::MAX`, batch 1 | `{"f":[1,2]}` | fatal (arrow "Values length 2 is less than…") |
| g5: FSL(2) node 2, batch 1 | `{"f":[1,2]}` | fatal (row count) |
| g2: node 7, batch 9 | fatal `…mismatch number of rows in column "n": got=7, want=9` | fatal (different text) |

Only the FSL *overflow* case is declared. The walker comment ("Go ignores the
FSL node length and renders by row") and `Layout::NoRows` both describe special
cases of this general rule. The `negative-row-go125.json` Null/Struct
characterization remains correct: there the first column is also negative, so
the row count is negative. g3 shows that "other negative batches" are not all
failures in Go. These are all malformed producers, so a safety-first rejection
is defensible. But the rejection should be declared as the general row-count
rule, and the comment should be corrected. Emulating Go safely is possible, by
slicing columns longer than the row count after validation, but is not
required.

### R3-5. Run-end field declared nullable: Go renders, Rust fatal (MEASURED, low; N2 residual)

g7 is a valid REE array whose schema declares `run_ends` nullable. Go renders
`{"r":5}\n{"r":5}\n{"r":6}\n\n`. Rust keeps the declared nullability in the wire
schema, so arrow-data rejects it with `The nullable should be set to false for
the field defining run_ends array.` Mainstream writers emit run-ends as
non-nullable, so impact is small. Fix: always rebuild the wire run-end field as
non-nullable. arrow still rejects actual nulls in run ends, so this is safe.
Then add the g7 fixture.

### R3-6. The diagnostics in-flight counter is a constant, not an observation (SOURCE, low; evidence quality)

`resources::inflight(1)` / `inflight(0)` bracket each `deliver`, and the
snapshot reports `fetch_max` of that literal. Its maximum is 1 whenever any
message or schema `Open` was delivered, whatever the concurrency. The new
schema-`Open` bracketing is correctly placed and cannot overlap a `Message`
event, but it makes the counter non-zero for schema-only runs by construction.
The reported "maximum application messages in flight equal to one" is
therefore a tautology. It cannot detect a regression to two outstanding
events. The real guarantee is structural: `try_send` happens only after the
previous ack, and the causal blocked-stdout test covers it. Either count
outstanding events with an increment on send and a decrement on ack, or have
the collector and docs cite this counter only as "an event was delivered", not
as a measured bound.

## Lifecycle and integration boundary (SOURCE; the bounded tests listed below ran)

- **Stage retries.** `open` makes a first-message status, a second-message
  status and EOF fatal, with Go's wrapping (`SCHEMA_READ`). `Decoder::new`
  errors are fatal at init. An established `None` goes through
  `decoder.end()`: it reconnects before any or after all initial
  dictionaries, and is fatal partway. Established codes 0/1/14 reconnect
  through `FlightStatus::is_stream_end`. Other codes are fatal with
  `go_error()` raw bytes. I traced this against Go `getInitialDicts`/`next`: a
  status error midway through the initial dictionaries is returned unwrapped,
  so tail reconnects for 0/1/14. Rust does the same.
- **Raw bytes.** Every fatal path carries a `Vec<u8>` to
  `CliError::with_raw_message`. Tests `raw_status_bytes_survive_every_fatal_stage`
  and `fatal_bytes_render_raw_in_text_and_replaced_in_json` pass in the scratch
  copy.
- **One acknowledged event.** `deliver` calls `try_send` on a capacity-one
  channel only after the previous ack, then awaits the ack, which yields to
  the runtime. `pump` reads the next message only after `Processed`. The
  decoder moves by value and returns with the ack.
- **Panics.** A Go-render panic sends `Ack::Panicked` and exits 2 through
  `go_panic` with no clear, as Go does. A Rust worker panic still re-raises
  (exit 101). No probe reached it.
- **Retention.** Unchanged from r2: staging above 4 MiB and scratch above
  1 MiB are released after each batch; neither is a peak cap. Dictionary
  deltas grow for the life of a subscription and are dropped on reconnect.
  The compressed-size allocation envelope (~2 GiB LZ4 / ~256 GiB zstd claim
  under the 4 MiB cap) and aligned-body aliasing are still measurement items.

## Commands and results

All runs used the scratch copy with `CARGO_TARGET_DIR=~/.cache/pbrs-r3/target`
and `TMPDIR=~/.cache/pbrs-r3/tmp`. The scratch copy adds only a test module
`mod r3` (3 probe tests) at the end of `session/tests.rs`, and a scratch-only
`flatbuffers = "=25.12.19"` dev-dependency for hand-built schema headers.

- `cargo test --offline --lib tail::`: **81 passed**, 0 failed (78 existing
  tail tests plus 3 probes).
- `cargo test --offline --features diagnostics --lib tail::`: **81 passed**, 0
  failed.
- `cargo test --offline --lib tail::session::tests::r3 -- --nocapture` with
  `R3_STREAMS=~/.cache/pbrs-r3/streams`: dumped 30 streams (g0–g12), with the
  Rust outcomes tabulated above. All lists of depth ≤ 60 and structs of depth
  ≤ 33 decode.
- Go: `GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=readonly go1.25.4 build
  -trimpath ./harness/decoder-oracle` (binary sha `ea12e2aa…f245fe`). Each
  stream ran under `ulimit -v 4194304`, `ulimit -f 16384` and `timeout 10`.
  The results are tabulated above, and the full outputs are in
  `~/.cache/pbrs-r3/goout/`.
- Decoder fixture rerun: 10/10 identical; manifest 40/40 SHA-256 match.

Not run: the full central normal/diagnostics suites, clippy, release builds,
process/signal fixtures, real Flight, and transport protocol review (a separate
reviewer has that). The parent's central real-transport run is still underway;
my counts are for the tail module only.

## Resolved vs unresolved

| Item | State |
| --- | --- |
| r2 N1a, N1b (overflow), N1c, N2 (reported shapes), N3, N4, N5 | **Resolved**, verified by tests and the fixture rerun |
| R3-1 FSL size ≤ 0 accepted (renders or reconnects where Go is fatal) | **Open, medium.** Concrete fix plus three fixtures |
| R3-2 union typeId wrap (256 → 0) | **Open, low–medium.** Pre-walk fix |
| R3-3 valid nesting depth 61–64 rejected | **Open, low.** Verifier depth, or declare |
| R3-4 general row-count rule (Go `Len >= rows`, negative batch → first column) | **Open, low.** Declare or emulate, and correct the comments |
| R3-5 nullable run-ends field | **Open, low.** Wire fix |
| R3-6 tautological in-flight counter | **Open, low.** Evidence wording or a real counter |
| Diagnostic text differences (schema/dictionary wrapping, row-count text, verifier text) | Accepted ledger |
| Compression allocation envelope, unbounded deltas, retention-not-peak, aligned-body aliasing | Accepted ledger; measurement pending |
| Final whole-phase review, measurements, retained gates | Pending |

None of these is a memory-safety blocker. R3-1 is the only finding where Rust
does something materially different from Go on a stream that Go handles
deterministically (Go fails fatally where Rust renders or retries). I recommend
fixing it before the final review. R3-2, R3-3 and R3-5 are small, and
R3-4/R3-6 are ledger and wording corrections. My probe set is bounded and
targeted. It does not prove there are no other `try_fb_to_schema` acceptances
that Go rejects at initialization; a systematic sweep of Go's
`metadata.go` panics and errors against arrow-rs `convert.rs` would be the
remaining check.
