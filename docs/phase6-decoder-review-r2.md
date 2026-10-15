# Phase 6 decoder/value/session review, round 2

This is a fresh, independent, read-only review of the native tail IPC decoder,
metadata validation, value renderer, session lifecycle and resource ownership.
It is not the final acceptance review for the phase. No implementation file was
edited. All probes ran in a scratch copy (`~/.cache/pbr2/probe`) with separate
Cargo/Go caches and temporary directories. I did no timing, profiling or
production access.

## Snapshot reviewed

`src/tail/` is untracked on `feat-phase6-flight-tail`, base `4a812d5`. SHA-256 at
2026-10-14T11:45Z (the probe copy matched):

| File | SHA-256 |
| --- | --- |
| `session.rs` | `ab06a1b2e9b89b33bfe177619dfb23b44df19aa45f444ff219ad862d14919963` |
| `session/tests.rs` | `59cf26251d9e92cb59fac67b8b6faefec85743a38695e316dbf5a43b1f9699ac` |
| `validation.rs` | `e12af67db94e191f156a2c1ed9895b946d121217a23c6d0f9c9aebc75b81154c` |
| `values.rs` | `037183a7939fc11d3921c36d086fad023d9a6c0004839096b28ab140cd56ea6b` |
| `resources.rs` | `944d3c88e6547a68a69444eb72c5b5a472ae83f0caaf67949b96443bd1e38f27` |
| `mod.rs` | `0fd7102d65f863c4fd079c893c51627553e0cb15c3506dba37e42de62fa3c0c7` |

**Scope note (arrived mid-review).** The transport r2 review
(`docs/phase6-transport-review-r2.md`) found that an invalid `grpc-message`
(`%FF`) loses the status code and that invalid status-details base64 panics.
The transport owner is implementing a raw-byte status wrapper. The session owner
is adding a narrow typed error boundary that preserves raw `CliError` bytes. No
IPC decoder changes are planned. This report is against the snapshot above.
`session.rs` (`drive`/`fail`/`Terminal`), `mod.rs` and the session tests will
change, so their lifecycle findings need **follow-up re-verification**. Transport
is out of scope here.

Sources: the cached plan `phase6-final.md`, `docs/rust-phase6.md`, r1
(`docs/phase6-decoder-review-r1.md`), `docs/phase6-values-notes.md`, the files
above, and registry sources for `arrow-{ipc,data,array,schema,buffer}` 60.0.0.
Go behavior was measured with Arrow Go v13.0.0, goccy v0.10.0 and Go 1.25.4
(`~/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64`, GOTOOLCHAIN=local,
GOPROXY=off).

Labels: **MEASURED** means a bounded probe ran. **SOURCE** means the code was
traced but not executed.

## New findings

### N1. Three arrow-rs panics remain reachable from hostile metadata (MEASURED, high)

`validation.rs` says hostile input "fails as an ordinary decode error instead of
unwinding". The walker checks nodes and buffers, but three panics come from
schema-level values or arithmetic it does not model. All three were reproduced
in the scratch copy by calling `decode_all`/`initialize` and the worker
`session()`/`subscribe` harness:

| # | Trigger | Rust panic site | Go v13 (Go 1.25.4 probe, same bytes) |
| --- | --- | --- | --- |
| a | Schema `FixedSizeBinary(byteWidth=-1)` plus any non-empty record batch (a valid width-1 batch declared as -1) | `arrow-data` `data.rs:1931` `layout()` `try_into().unwrap()` → `TryFromIntError` | `NumRows=1`, then a panic in rendering (`FixedSizeBinary.Value` slice bounds `[:-1]`), exit 2 |
| b | `FixedSizeList<Int8>(4)` with the FSL node length patched to `i64::MAX` (batch length 1) | `arrow-data` `data.rs:1280` `checked_mul(..).expect("integer overflow computing expected number of expected values in FixedListSize")` | **Accepted:** prints `{"f":[1,2,3,4]}` + LF + LF, then EOF. Go ignores the FSL node length. |
| c | Schema message: one sparse Union with 129 children and no `typeIds` (hand-built flatbuffer, no body needed) | `arrow-schema` `fields.rs:468` `UnionFields::from_fields` `expect("UnionFields cannot contain more than 128 fields")`, inside `try_fb_to_schema` on the worker `Open` event | Init error `arrow/ipc: unknown error while reading: arrow: union type code out of bounds`. In tail this is the fatal reader-initialization path. |

With 128 children, (c) initializes normally in both implementations. In
production the worker thread panics and drops its ack sender. The driver gets
`worker_stopped()` (MEASURED: `subscribe` returned `terminal failed tail output
worker stopped`, `worker_panicked=true`). Then `run` calls `resume_unwind`
(`session.rs:85-87`, SOURCE), so the process exits 101 with Rust panic text from
an unnamed thread. The status-line clear and CLI error rendering do not run. (b)
is the most serious: Go renders that stream, while Rust crashes. (c) needs only
one schema message.

Recommendation, no catch_unwind or hook:

- (a) Reject a negative `FixedSizeBinary` width in the walker's default arm.
  Better, reject it at schema/plan time wherever it appears, including dictionary
  values. Choose an explicit divergence, since Go panics later in rendering.
- (b) In the FSL arm, check that `node.length.checked_mul(list_size)` fits
  `usize`. Even more conservatively, require the FSL node length to equal its
  parent length. Go renders by the record length, so emulating Go here would
  need an explicit decision. A safe rejection is the minimum.
- (c) Before `try_fb_to_schema`, walk the flatbuffer schema and reject a union
  with more than 128 children and no `typeIds`, using Go's text.

Add one regression test per row. Also grep arrow 60 for other `expect`/`unwrap`
calls reachable from `try_fb_to_schema` and `ArrayData::validate*`. These three
are what I found, not a proof of completeness.

### N2. Non-nullable fields containing nulls: Rust fatal, Go renders (MEASURED, medium, value regression)

The wire schema keeps nullability (`session.rs:564-577`). arrow-rs then rejects
nulls in non-nullable fields, at top level in `RecordBatch::try_new` and nested
in `validate_nulls`. Go v13 never checks nullability. Probe streams declare
nullability in the schema with the same body:

| Stream | Go (exit 0) | Rust session |
| --- | --- | --- |
| `n: Int64 not null`, values `[1,null,3]` | `{"n":1}\n{"n":null}\n{"n":3}\n\n` | Fatal: `arrow/ipc: unknown error while reading: Invalid argument error: Column 'n' is declared as non-nullable but contains null values` |
| `l: List<item Int64 not null>`, `[[1,null]]` | `{"l":[1,null]}\n\n` | Fatal: `… non-nullable child of type Int64 contains nulls not present in parent List(non-null Int64)` |

These are not memory-safety cases. A producer can mark fields required and
still send nulls; arrow-rs producers are guarded, but others are not. Rust turns
that into a terminal tail failure. Suggested fix: decode with a wire schema that
is nullable at every depth. Keep Map entries and Map keys non-nullable, because
arrow-data requires that and the renderer already rejects those schemas, as Go
does. The renderer already uses the original schema. Add Go-oracle cases. This
does not cap or alter any valid Go stream.

### N3. Debug-build overflow in the compressed-prefix walk (MEASURED, low)

`validation.rs:315-319` reads the 8-byte prefix from `body[offset..offset+8]`
without first requiring `length >= 8`, then computes `length - 8`. When a
compressed buffer has length 1–7 and the body extends past it:

- debug/test builds: `attempt to subtract with overflow` at `validation.rs:319`
  (LZ4 and ZSTD, MEASURED);
- with overflow checks off (`CARGO_PROFILE_DEV_OVERFLOW_CHECKS=false`; release
  sets no `overflow-checks`): fatal
  `… Ipc error: Compressed IPC buffer is too short: expected at least 8 bytes, got 3`
  (MEASURED). Go gives `arrow/ipc: unknown error while reading: unexpected EOF`.

So release builds are safe, but the walker also reads a "prefix" that belongs to
another buffer. Fix: return `Halt::Exhausted` when `length < 8`, before reading
the prefix.

### N4. The Go corpus test bypasses the production decoder (MEASURED, coverage)

`values.rs` `oracle_corpus_matches_go_bytes` decodes with
`StreamReader::with_skip_validation(true)` (test-only `unsafe`, `values.rs:2310`)
and rewrites strings to binary afterwards. It never exercises `Decoder`, the
validation walker, `ipc_body`, the dictionary state machine or `NoRows`. As an
independent check, I split each corpus `.arrows` file into FlightData messages
and ran them through the production `session()` harness (worker, `Open`,
`Message`, staging, Println LF). **44 of 44 cases matched exactly**, including
stdout bytes and Go panic text. The walker therefore produces no false
rejections on these Go-written streams. Recommendation: make this
production-path replay the durable test, or add it next to the existing one, so
later walker changes (N1–N3) stay gated against Go-written layouts.

### N5. Documentation drift (SOURCE, low)

- `docs/rust-phase6.md:42` says "Schema decoding remains on the runtime." Schema
  decoding now runs on the worker (`Event::Open`, `session.rs:98-100, 747-754`).
- `session/tests.rs:921` says the probe used "Go 1.27.1". The durable evidence
  (`negative-row-go125.json`, 9 cases) is Go 1.25.4. Cite that file.

## Verification of round-1 items

| R1 item | Status | Evidence |
| --- | --- | --- |
| §1 short validity, union id/offset sizes, unaligned dense offsets, negative lengths/null counts | **Resolved for the modelled paths** (SOURCE plus existing tests). See N1 for unmodelled schema-level panics. | The walker mirrors arrow-ipc 60 `create_array`/`skip_field` order, including V4 union validity, dictionary = node + 2 buffers, REE and FSL. `arrow-ipc = "=60.0.0"` is pinned. Tests: `node_null_counts…`, `short_validity_bitmaps…`, `union_type_ids_and_dense_offsets…`, `dense_union_decodes_from_unaligned_flight_bodies` (64 prefixes). |
| Aligned owned IPC body + buffer bounds | **Resolved** | `ipc_body` copies non-64-aligned bodies into an Arrow allocation. `validate_body` checks signed, overflow-safe ranges for record and dictionary bodies. With `-1`-prefixed buffers, the walker's alignment check uses `offset+8`, which keeps the same remainder mod 4. |
| Impossible compression prefixes | **Resolved, with N3** | Format-bound rejection, 512× LZ4 and 65,536× zstd (2× margins over format maxima, checked against the source arguments). |
| §2 negative/huge lengths | **Resolved** for negative lengths, now matched to the measured Go behavior | `NoRows` applies only to the characterized Null/field-less Struct shape. Other negative batches are rejected, a narrow diagnostic divergence from Go's `slice bounds out of range [:-N]`. Huge positive lengths amplify in both implementations; that is a documented limit. |
| §3 union null predicate | **Resolved** | `validity_null` (`values.rs:834-840`) is used in union `is_null`/`gather`. Covered by corpus cases. |
| §4 Go float16 subnormals | **Resolved** | `go_f16_to_f32` (`values.rs:1340-1350`) maps exp 0 to f32 exp 0. Covered by the corpus case. |
| §5 FSL nested panic wrapping | **Resolved** | `gather_element` returns `Encode` for an inner FSL, so the outer list panics. Corpus. |
| §6 extreme `Timestamp(Second)` | **Resolved** | Go 1.25 absolute-time wrap is emulated. Corpus. |
| §6 nested dictionary ID count | **Resolved** | `ipc_dictionary_ids` walks flatbuffer children recursively. Covered by test. |
| §6 schema/dictionary error wrapping | **Unresolved (accepted ledger)** | Diagnostic text still differs, e.g. `read_dictionary` uses `e.to_string()` (`session.rs:531`). |
| §6 retention aliasing of aligned bodies | **Unresolved (PLAUSIBLE, measurement)** | Aligned bodies stay zero-copy, so retained dictionaries can pin the receive allocation. The dictionary soak still needs RSS versus reported bytes. |
| Panic → exit 101 without clear | **Partially resolved** | Fixed for every r1 row. N1 rows a–c still reach this path. |
| Initial schema decode starving HTTP/2 | **Resolved** | One acknowledged worker `Open`. Causal test `schema_initialization_runs_on_the_worker…`. |
| Capacity-one, ack-before-next | **Resolved** | `deliver` uses `try_send` only after the previous ack, and `pump` reads the next message only after `Processed`. Blocked-stdout test. |
| No unsafe / skip_validation / catch_unwind / panic hook in the native pipeline | **Confirmed** | `grep` in `src/tail` finds only the test-only `unsafe` at `values.rs:2310`. |

## Accepted resource items, restated precisely (SOURCE)

- A valid-looking compression prefix is still allocated up front. Under the 4
  MiB receive cap, the largest accepted claim is about 4 MiB × 65,536 = 256 GiB
  per zstd buffer and about 2 GiB per LZ4 buffer. A mismatching decode frees the
  claim immediately (`ret.len() != declared`). A claim above the host's
  overcommit limit aborts (`handle_alloc_error`), which `catch_unwind` cannot
  stop. Go also dies on huge sizes; this is an explicit unknown-allocator item.
- Dictionary deltas grow without bound for each subscription and are dropped at
  reconnect (`concat` in `update_dictionaries`).
- Retained staging (4 MiB) and scratch (1 MiB) are post-batch retention
  policies, not peak caps. On the render-panic path, staging is not trimmed
  before the session ends, which is harmless.

## Lifecycle checks (SOURCE; re-verify after the error-boundary change)

- Clear-before-error: DoGet, initialization and established-read fatals go
  through `handoff.fail`. Worker-loss and Go-panic terminals skip it, as Go's
  panic does.
- Render order: clear, convert the whole batch, drop the batch, then one blocking
  write plus LF. An `Encode` error keeps earlier rows. A Go panic sends
  `Ack::Panicked` and exits 2 through `go_panic`.
- The decoder moves by value and is dropped with the subscription. Reconnect
  waits 500 ms after `Watching`, with a fresh decoder.

## Tests and probes run

- Scratch-copy `cargo test --offline --lib tail::` (debug): 72 passed. That is
  the 65 existing tail tests plus 7 probe tests (`r2_probes` ×5, `p3_dump`,
  `dump_streams`). The existing values corpus test passed.
- `r2_probes`: P1 FSB(-1) panic; P2 FSL `i64::MAX` with size 2 gives an
  ordinary error (no overflow), size 4 panics; P3 129-child union panics, 128
  passes; P4 debug panic; P5 non-nullable fatals.
- Overflow-checks-off rebuild (separate target dir) for P4 only.
- `r2_corpus`: 44/44 corpus cases identical through the production session.
- Go 1.25.4 probe `harness/r2probe/main.go` (scratch only; sha256
  `ed80a491…609d9`, binary `f3969633…e95be`), under `ulimit -v 4 GiB` and a
  10 s timeout, on 10 Rust-dumped streams (valid controls plus P1–P5). Results
  are as tabulated above. P4 Go: `unexpected EOF`.
- I did not run full central normal/diagnostics suites, clippy, release builds,
  process/signal fixtures or real Flight. Those remain with the central runs.

## Resolved vs unresolved summary

- **Resolved (verified):** r1 §1–§5, the extreme timestamp case, nested
  dictionary IDs, aligned bodies and bounds, impossible compression prefixes,
  negative-row Go characterization, worker-side schema initialization, and
  capacity-one acknowledgement.
- **Unresolved, new:** N1 a–c (high, panics), N2 (medium, value regression),
  N3 (low), N4 (coverage), N5 (docs).
- **Unresolved, accepted/ledger:** diagnostic wrapping text, retention aliasing,
  compression allocation envelope, unbounded deltas, retention-not-peak policies.
- **Follow-up required:** re-check session lifecycle and terminal rendering after
  the transport raw-status wrapper and the session typed error boundary land.
  The final fresh whole-phase review is still pending.
