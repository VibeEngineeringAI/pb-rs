# Phase 6 review wave: values correction review r2 (Opus)

This is an independent check of the owner's response to
`docs/phase6-review-wave-values-opus.md` (V1, V2, I1). Scope: native Arrow
v13 / goccy v0.10.0 value bytes, renderer ownership, scratch handling and
error/panic behaviour, against pinned Go `07af390` built with go1.25.4. It is
**not** final acceptance for the whole phase. It makes no claims about
performance, live servers, the session or the transport.

## Snapshot reviewed

| Item | sha256 |
| --- | --- |
| `src/tail/values.rs` | `e02d7e63aec6c9cf6f53f31a927a2cda5855a648adf3e7e05735d09d10354fd5` (2685 lines), unchanged from start to end |
| `docs/phase6-values-notes.md` | `73945a87d1b0f138e138868ef95764597412fc62c5390c38fbf699df79f891f1` |
| `harness/values-oracle-wave/main.go` | `5525f5e722ee4d743ef0ce44943f694e265f90aa1f2a4af818cea22916027f12` |
| `tests/fixtures/arrow-go-v13-values-wave/manifest.json` | `0ad9b21acb566b53b79e5d88ebe33d1637509d69df78a700c0e399c73d507cd3` |
| `tests/fixtures/arrow-go-v13/manifest.json` (44 cases) | `e0c8ba1f8d563deddfa530cd75a8744d95130767b523d1f7160e70d19a473856`, unchanged |
| `harness/values-oracle/main.go` | `e5de695b…a3eab2`, unchanged |
| `Cargo.toml` / `Cargo.lock` | `eb55b298…0c83c` / `aa35f338…1ebb44`, the same as at r1 (no dependency change; `zmij = "1.0.23"` was already direct) |
| `src/tail/session/tests.rs`, `session.rs` (root-owned, at test time) | `cb9bb45e…4fd076`, `2d793026…58a1e` |
| `src/tail/transport.rs` | `09bb4c9d…48a0` at the start, then `f08a0c73…9845` before my test runs (a parallel transport edit). It is not reviewed here. |

## Disposition

| ID | r1 | r2 status | Basis |
| --- | --- | --- | --- |
| V1 shortest-float ties | medium, measured | **Closed** | Measured (below) |
| V2 empty interval child in List/LargeList | low, measured Go / source Rust | **Closed** for List/LargeList; padded-buffer caveat stays declared | Measured, 16 Go streams in Rust; caveat source-only |
| I1 Go map order picks among several non-finite errors | informational, source | **Measured on the Go side**; the "one of Go's possible messages" wording stands | Measured |

No new correctness defect was found in the renderer. The four new notes
(N1–N4) are about documentation and test hygiene. None needs a production
change.

## V1: code check

`push_go_float` (`values.rs:1379-1453`) parses zmij's text. It reads an
optional `-`, the mantissa digits and an optional `e[+-]NN`, which Rust's
`i32` parser accepts with a `+`. It strips leading and trailing zeros and
recomputes the decimal exponent. The unchanged goccy 'e'/'f' decision then
lays the number out: f64 uses `1e-6 <= abs < 1e21`, and f32 compares in f32
as goccy does. Exponents are written with at least two digits.

Edge cases checked:
- zmij's `BUFFER_SIZE` is 24 bytes, and its fixed-notation windows are
  `-5..=15` (f64) and `-6..=12` (f32). The 64-byte digit array cannot
  overflow.
- Zero becomes `"0"`, with the sign kept (`-0`, as Go's strconv prints it).
- Non-finite values never reach zmij: f64 still returns
  `json: unsupported value: NaN|+Inf|-Inf`, and f32/f16 write the raw token
  and set `cx.raw` as before.
- No other float path in `values.rs` uses `{:e}` or Display. The remaining
  `to_string` at line 1714 formats decimal bignum chunks.
- The comment that r1 flagged has been corrected (`values.rs:1372-1378`).

Measured:
- The ignored `go_float_vectors_match_every_bit_pattern` was run on r1's
  `vectors.tsv` (sha256 `c4dec043ad6aebc7bfc966af0aab41d47f4b95ea3ec4cda2737d441c25ef9472`,
  3,632,714 lines, rehashed). Output: `checked f64=120465 f32=120486
  f16=65536`, with zero mismatches. The f64 count is r1's 120,494 vectors
  minus 29 non-finite rows, which the test skips. The set includes f64 `-0`
  (Go `-0`), `5e-324`, `MaxFloat64` and `0x0010000000000000`.
- The unit test `shortest_float_ties_round_to_even_like_go` passes.
- New r2 Go stream `e10_float_neg_zero_thresholds` (real `math.Copysign(0,-1)`
  in f64, in f32 and inside a list; values on both sides of `1e-6` and `1e21`
  for f64 and f32; subnormal minimum and maximum values; and multiples of a
  tie inside a list) matches byte for byte on both paths. The pre-fix
  renderer fails it on the list tie (`…280.3`).

## V2: code check

`Col::new` (`values.rs:759-775`) sets `nil_child` only when all three hold:
- the kind is `List` or `LargeList`;
- the element plan is `MonthInterval`, `DayTimeInterval` or
  `MonthDayNanoInterval`;
- `values.is_empty()`, where `values` is the full child of an IPC-decoded or
  arrow-rs-sliced list.

Go's condition is also the whole child buffer: slicing a nil slice with
`[0:0]` stays nil. Gather (`values.rs:936-941`) writes `null` into the same
`cx.scopes[depth][slot]` that encode reads (`values.rs:1115`). It skips
`marshal_run`, so no nested slots are left stale and `cx.raw` is untouched.
Null lists keep the early return. The flag is computed per batch through
`Col::new`, which matches Go's per-record decision.

Measured in r2. I generated nine further Go streams with go1.25.4, Arrow
v13.0.0 and goccy v0.10.0, using the same IPC write/read and Println replay
as the wave generator. The existing test `oracle_corpus_matches_go_bytes` was
pointed at them with `PB_VALUES_ORACLE_DIR` and replayed both as decoded and
through the Utf8-to-Binary wire rewrite. All 10 r2 cases (these 9 plus e10)
pass on the fixed renderer. On the pre-fix renderer, 7 of the 10 fail and the
3 controls pass:

| Case | Go stdout (exact) | Fixed | Pre-fix |
| --- | --- | --- | --- |
| e1 `List<List<MI>>` with an empty grandchild, rows `[[]]`,`[]`,null,`[null]`,`[[],[]]` | `[null]`,`[]`,`null`,`[null]`,`[null,null]` | match | `[[]]`… |
| e2 three batches: empty child, then non-empty, then empty | `null`,`null` / `[]`,`[{"months":1}]` / `null` | match | `[]` |
| e3 FSL(1)<MI> control | `[{"months":1}]`,`null` | match | match |
| e4 `Map<Int32,MI>` with all entries empty (control) | `[]`,`null`,`[]` | match | match |
| e5 `List<Struct{x: List<DayTime>}>` | `[{"x":null},{"x":null}]`,`[]` | match | `{"x":[]}` |
| e6 REE with `List<MI>` values `[[]]` | `null` ×3 | match | `[]` |
| e7 Dictionary with `List<MI>` values `[[],null]` | `null` ×4 | match | `[]` |
| e8 two columns: List with an empty child plus LargeList with a non-empty child | `"a":null`, with `b` intact | match | `"a":[]` |
| e9 outer list of `List<MI>` with an empty outer child (control) | `[]`,`null` | match | match |
| e10 V1 thresholds and `-0` (above) | — | match | list tie differs |

These rows are measured on the Go side and through the Rust renderer, not
read from source.

Caveat. Only an *uncompressed* body whose empty interval child declares a
padded, non-zero buffer length can still diverge (Go `[]`, Rust `null`). This
is source-only and unprobed, and it remains a declared divergence.

I read Go's compressed path at source level and found no divergence there.
In `ipc/file_reader.go:391-420`, the compressed path calls
`raw.Resize(uncompressedSize)` on a fresh `NewResizableBuffer`, or
`Resize(len-8)` for the `-1` marker. `memory/buffer.go resize` with 0 on a
fresh buffer leaves `buf` nil, so `Bytes()` is nil and Go prints `null`. That
is what Rust prints. Not probed.

## I1: now measured on the Go side

A `List<Struct{a: NaN, b: +Inf}>` row was round-tripped through Go IPC, and
`RecordToJSON` was called on the same decoded record 2,000 times in one
process:
- **1,725** runs panicked with
  `json: error calling MarshalJSON for type *array.Struct: json: unsupported value: NaN`;
- **275** runs panicked with `…: +Inf`;
- stdout was empty and no error was returned in every run.

Tally: `i1/i1_tally.txt`, sha256 `3824deb4…782b`. The Rust replay
(`i1` corpus, both paths) produces the `NaN` message, with empty stdout. Rust
is deterministic and gives one of Go's two observed messages. The split
depends on Go's runtime map iteration and is not a contract. The notes'
wording ("one of Go's possible messages") is correct. It is not a general
panic-text waiver: it applies only to rows or structs that hold more than one
non-finite float64.

## Owner claims independently re-measured

| Claim | My result |
| --- | --- |
| Pre-fix renderer fails exactly the 7 defect cases on both wire paths; the 2 controls pass | **Confirmed.** `values.rs` `037183a7…ea6b` (r1 snapshot, from an older review cache) in a scratch copy, with the wave corpus via `PB_VALUES_ORACLE_DIR`: "14 of 9 cases differ" (7 × decoded/wire), the controls absent from the failures. The same pre-fix copy passes the 44-case default corpus. |
| Fixed: values 17 passed + 1 ignored, in normal and diagnostics builds | **Confirmed** (separate target; both feature sets). |
| Tail suite 108 normal / 110 diag + 1 ignored | **Not reproduced as counts**: the tree had gained tests. On the hashes above, from a clean `pb` rebuild: `tail::` normal **111 passed, 1 ignored**; diagnostics **113 passed, 1 ignored**. 0 failed in both, including the root's `value_wave_corpus_replays_byte_exact_through_the_session`. |
| Ignored exhaustive test: 120465 / 120486 / 65536, zero mismatches | **Confirmed** (twice, before and after the clean rebuild). |
| A corrupted copy makes the replay fail | **Confirmed.** One digit was changed in `float_ties_nested.expected`, keeping the size (`…14062`→`…14063`, the old Rust value). Result: "2 (case, path) mismatches across 9 cases". An unmodified copy passes. |
| Corpus regenerates byte-identically | **Confirmed.** Two runs of the committed generator (go1.25.4, `GOFLAGS=-mod=readonly GOPROXY=off -trimpath`) both give manifest `0ad9b21a…07cd3`, and `diff -r` against the committed directory is empty. `git status --porcelain` was unchanged afterwards. |
| The 8 reviewer cases are byte-identical to r1's `e2e-out` | **Confirmed** for all 8 `.arrows` and stdout files. `float_ties_nested` is new. All 36 files match their manifest sha256 and size. |
| rustfmt / Clippy clean | **Confirmed.** `rustfmt --edition 2024 --check src/tail/values.rs` exits 0, and `cargo clippy --lib --tests -- -D warnings` exits 0 with and without `diagnostics`. |
| Root R6 values-wave captures | **Cited, not rerun.** `docs/evidence/phase6/focused-review-r6.json` (`c8beb3a9…c914`) records the following. The report sha256 values, which I recomputed, match: `values-wave-json` `9443c4b2…2bd4` and `values-wave-text` `a60b63c1…1c97`. Each has 9 cases, 0 failures and `passed: true`. `candidate-review-r6.json` (`a90917f8…b3e`) pins `values.rs` `e02d7e63…`. I did not audit the harness semantics of `strict_parity_passed: 0` versus `remaining_contract_passed: 9`. |

## New notes (low; no production change)

- **N1 (documentation, source-only).** `docs/phase6-values-notes.md:180-182`
  says a compressed binary buffer with a non-zero compressed length and a
  zero uncompressed length is "non-nil in Go (`""`) but empty in arrow-rs
  (`null` here)". By the Go v13 source quoted under V2, that buffer is nil,
  so Go prints `null` and agrees with Rust. Reword it as unprobed and
  probably not divergent, or probe it.
- **N2 (generator hygiene).** In `harness/values-oracle-wave/main.go:213`,
  the literal `-0.0` is a Go constant equal to +0. The `float_ties_nested`
  stream has no f64 sign-bit zero, and Go prints `"d":0`. Negative zero is
  covered elsewhere (the vectors and r2 e10). If the intent was `-0`, use
  `math.Copysign(0, -1)`, which would change the manifest. Optional.
- **N3 (test durability).** The exhaustive float check depends on
  `vectors.tsv` and its generator (`probe-go/main.go`, `7c33cb00…1864`).
  Both live only in the r1 cache. The test also skips Go's 29 f64 error rows
  instead of comparing their text. The committed corpus and four unit asserts
  are the durable guard. Preserving the generator under `harness/` would make
  the check reproducible.
- **N4 (documentation nit).** In notes line 454, "about 80 KiB" is the
  disk-block allocation. The apparent size of the 37 files is 11,579 bytes.

## Process note: a contaminated run, corrected

My first `tail::` normal run reported a failure in the root's session replay
test, with pre-fix tie bytes. The cause was my own setup. The pre-fix scratch
copy is the same package, `pb 0.1.0`, so building it in the same
`CARGO_TARGET_DIR` overwrote `deps/pb-52ec66aace140b89`. Cargo then reused
that binary for the worktree; the 293-test total is the tell, against 296 on
the fixed tree. I ran `cargo clean -p pb` and reran every current-tree Rust
check from a fresh build, with all hashes stable before and after. Only the
clean results are reported above. The contaminated logs are kept in
`contaminated/` for audit.

The binary held the pre-fix `values.rs` plus the root's current session test,
and that test failed on it (`float_shortest_ties: json=true`). This shows
incidentally that the production-path replay discriminates the pre-fix
renderer. Lesson: never share a target directory between two copies of one
package.

## Commands (outputs in `/home/ajs/.cache/pb-phase6/review-wave-values-opus-r2`)

All runs used `TMPDIR`, `GOTMPDIR`, `GOCACHE` and `CARGO_TARGET_DIR` inside
that cache, plus `nice -n 10`, `-j4`/`GOMAXPROCS=4`, `--locked --offline`
and `GOPROXY=off`. No production edits, commits, benchmarks, soaks or
pressure runs were made.

```
cargo test --lib tail::values                                  # t01 (normal), t03 (diagnostics)
PB_VALUES_FLOAT_VECTORS=<r1>/vectors.tsv cargo test --lib \
  tail::values::tests::go_float_vectors_match_every_bit_pattern -- --ignored --nocapture   # t02, t32
probe-go: go build -trimpath && ./probe corpus-r2 i1            # main.go b74b3301…4972
PB_VALUES_ORACLE_DIR={wave-copy,wave-corrupt,corpus-r2,i1} cargo test --lib \
  tail::values::tests::oracle_corpus_matches_go_bytes -- --exact                           # t33-*
prefix/ (values.rs 037183a7…) with PB_VALUES_ORACLE_DIR={wave-copy,corpus-r2} and default # t11-*
go run -trimpath ./harness/values-oracle-wave regen{1,2}        # o15.txt
cargo clean -p pb; cargo test --lib [--features diagnostics] tail::                        # t31, t34
rustfmt --edition 2024 --check src/tail/values.rs; cargo clippy --lib --tests [--features diagnostics] -- -D warnings
```

Artifact hashes:
- r2 probe corpus manifest: `1415ecc5…ea4e`, 10 cases, 41 files.
- i1 manifest: `dda3db8b…73a9`.

## Limits

- No live Go or Rust CLI was run from this lane. The R6 process captures are
  the root's evidence, cited above by hash.
- Not probed:
  - an uncompressed padded empty interval buffer (the V2 caveat);
  - compressed zero-length buffers (source-only, see V2 and N1);
  - interval lists inside unions;
  - extension types.
- Session, transport, decoder and performance work were not re-reviewed.
