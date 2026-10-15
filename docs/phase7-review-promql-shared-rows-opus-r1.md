# Review: PromQL shared immutable result rows (Opus R1)

**Verdict: ACCEPT, source-ownership scope only.** There are no blocking findings. The non-blocking notes below need no source change before the root's central checks. Nothing here admits runtime behavior, performance, a deadline result or A1-04 success.

## Identities

- Live `src/tui/promql.rs`: `9e186f17c9f61d81ce1d16a9ca0847ad0d44294be0fa9386f8831b17f8a572d2`. This equals the frozen `promql.rs.after`, and the hash was the same before and after my test run.
- Preimage: `51387004ec5207391d8c60252d1eb62997ae200d8ec23a89237238a1f114cf55`. I confirmed it independently by applying `change.diff` in reverse to a copy of the live files (`recon/`). The same reverse patch produces docs preimage `d7eee652…105d`.
- Live `docs/phase7-promql.md`: `c112100739f099cff70e62cf3a8e8771da8c5bdf692d3a11e2febda5cf73160f`. This equals `freeze.json`.
- `docs/phase7-a1-retry04-sustained-investigation-r1.md` (read in full): `2804de557c42a73a1e2b2cb3171128abf43ed2879ca51455b9308439f37fbe2f`.
- The hunks of `diff -u promql.rs.before <live>` are byte-identical to the Rust part of `change.diff`.

## Source findings

1. **Both per-render deep copies are gone.** Before the change, `results_table` (`src/tui/promql.rs:4778-4780`) deep-copied the rows twice on every render. `self.table.clone()` cloned the table's `Vec<Row>`, and then `with_rows(self.data_rows.clone())` copied them again. `ResultsTable::rows` is now `Arc<[Row]>` (`:1840`), so the derived `Clone` only bumps a reference count, and `:4780` passes `Arc::clone`. In table view this is the only per-key render path for rows (`:5264`). Two other copies are also gone. `update_table_columns` (`:3359`) runs on each completion and resize, not on each render. The matrix fallback (`:3543`) also shares now. No remaining `to_vec`, `get_mut`, `make_mut` or other row mutation exists outside the tests. Because `Arc<[Row]>` allows no mutation, sharing cannot change what is displayed.
2. **Completion consumes the service vectors once** (`:3540-3541`). Note that `From<Vec<Row>> for Arc<[Row]>` always allocates new storage and copies the row structs. It does not deep-clone the strings. `Row` is 4×`String`, which is 96 bytes on 64-bit. At 256,000 rows, each successful completion therefore does one extra copy of about 24.6 MB, and the old and new buffers exist briefly at the same time. This costs far less than the two 2,048,000-string copies that every render used to do. However, the doc's statement "consumes … into shared storage once" leaves this copy out. This is a doc-precision note only, with no performance claim. The service-side matrix `rows.clone()` is unchanged, as the doc says.
3. **Chart conversion behaves the same.** The fallback condition is unchanged: `chart_rows` must be empty and `result_type == "matrix"`. `extract_chart_data` (`:4050-4060`) picks its source the same way and iterates in the same order. `rows.iter()` over `&Arc<[Row]>` produces the same `&Row` sequence as the old `&Vec<Row>`. In Both mode, chart storage and table storage stay separate whenever the service supplies both.
4. **Aliasing, replacement, error and empty states:**
   - A successful completion installs new handles, so older state or table clones keep their own allocation.
   - An error (`:3558-3560`) installs `Arc::default()` handles. `with_rows` then gives the same cursor and page reset as the old `clear()` + `with_rows(Vec::new())`.
   - `metric_detail`, `:4805` and `:5255` still test `data_rows.is_empty()` and `len()`, with the same results.
   - Local view copies stay independent. Each clone has its own filter, focus, cursor and page fields, and `results_table` changes only its own local `table`.
5. **Unchanged behavior:**
   - `visible_rows` (`:1869-1882`) still collects references to every row, several times per key. This is out of scope by design and is the remaining O(n)-per-key work.
   - `highlighted_row` still clones one `Row`.
   - Pagination, geometry, filtering, key handling, ordering, stale-completion handling and timeouts are all untouched. The `Completion::Result` arm never checked a request id, before or after.
6. **Send/Sync, Debug and public API:**
   - `Row` has only `String` fields, so `Arc<[Row]>` is `Send + Sync`, just as `Vec<Row>` was `Send`.
   - `PromqlState` already contains a `RefCell` (`render_fault`), so it was not `Sync` before or after.
   - The crate compiles, so every existing `Send` bound still holds.
   - Derived `Debug` prints a slice in the same `[..]` form as a `Vec`.
   - `ResultData { rows, chart_rows: Vec<Row> }` (`:100-106`) and the service interfaces are unchanged.
   - Nothing outside `promql.rs` uses these fields.

## Added tests

All five tests check something meaningful, and `assert_eq!(s.view(), frame)` confirms that rendering twice in a row gives the same output.

- `row_storage_frame_fixtures` checks that the state and table share one allocation in every fixture state.
- The snapshot test checks sharing across `update_table_columns`, `Clone` and the Both-mode split.
- It also checks element addresses (`std::ptr::eq` on `data_rows[1]`).
- The replacement and error test shows that old frames stay stable.
- The filter test shows that filter, focus and selection stay local to each clone.
- The dense test checks the investigation's arithmetic exactly: 7680, then 18 PgDown, ending at zero-based 7950, displayed 7951, `bench-series-0031`. It checks each step's footer. It also checks that the height-14 render page of 8 rows starting at 7944 leaves the 15-row update model unchanged. It is a unit regression test, not a native replay.

Non-blocking limits:

- **R1. No test looks inside `results_table`'s local table** (`:4778-4780`). The snapshot test copies those operations onto a hand-built `view_table` instead. If someone later wrote `table.with_rows(Arc::from(self.data_rows.to_vec()))` at `:4780`, all 60 tests and all frames would still pass. Today the source is correct, and the type change means such a regression would need an explicit `to_vec`. Catching it would need a test hook, which is outside this review's authority.
- **R2. The `ptr_eq` check in the `"error"` fixture may prove nothing.** Two empty `Arc<[Row]>` handles may compare pointer-equal however they were made. The emptiness assertions in `row_replacement_and_error_clear_preserve_old_snapshots` cover that state anyway.

## Frame-comparison instrumentation

I checked the instrumentation against the real before and after production code:

- **Before side.** `promql.rs.baseline-capture` matches the asserted preimage byte for byte over lines 1-6093 (all production code before `#[cfg(test)]`). `share-rows.py` also asserts that removing the capture test gives back the `5138…` hash.
- **After side.** `promql.rs.shared-with-capture` and `promql.rs.shared-capture-final` both match the frozen after-file byte for byte over lines 1-6095. They differ from each other only inside the dense test.
- **Same fixture.** The `row_storage_frame_fixtures` body is byte-identical in the baseline and both shared capture sources.
- **Run-to-source binding.** Test counts tie each run to a source family. Before-runs show 559 tests in total (1 run + 558 filtered). After-runs show 563.
- **Results.** All 4×12 frame maps are exactly equal (`frame-parity.json`). ANSI dark and ANSI light frames differ, so the theme setting takes effect.

Instrumentation limits:

- The plain dark and plain light frames have identical hashes, so the 48 comparisons contain 36 distinct frame contents.
- The plain runs inherited `NO_COLOR` from their environment rather than setting it in argv. Their escape-byte count is 0.
- No run recorded its own source hash. Snapshot mtimes are later than some runs, so the binding rests on the test counts plus the equal production regions.
- The `shared-ansi-dark` frames came from the run whose dense assertion failed (59/60). That failure is retained, and production was identical in that run.
- None of this covers truecolor, PTY, actual Go output or full corpus parity.

## What I executed vs read

- **Read only:** the diff, `freeze.json`, the scripts, the capture sources, the retained stdout, stderr and JSON logs, both docs, and the source at every touched line.
- **Pure checks I ran:** sha256 hashes, the reverse patch, region `cmp` and diff comparisons, and fixture-body equality.
- **One focused unit run:** `env TZ=UTC NO_COLOR=1 cargo test --offline --locked --lib tui::promql`. It used an owned `CARGO_TARGET_DIR` reflinked from the author's debug target, with a 540-second timeout under `setsid` and the process group checked as empty afterwards. The first attempt did no compiling and only reused the author's binary, so it is not independent and I discard it. I then deleted only the `pb` fingerprints and artifacts in my copy. The rebuild compiled `pb` against the frozen `9e186f17…` source and ran **60 passed, 0 failed** in 15.1 s, including all five new tests.

Not run: ANSI tests, clippy, fmt, central or integration suites, release or candidate builds, native CLI, Go, helpers, PTY, network, profiler, performance work or the common lock. No source, production or Git changes were made.

## Process and artifacts

- Owned artifacts are in `/home/ajs/.cache/pb-phase7/review-promql-shared-rows-opus-r1/`: `o*.txt` logs, `recon/`, `run.sh` and the test stdout, stderr and JSON files.
- The 6.1 GB reflinked target and the tmp directory were deleted.
- **Zero owned processes remain.**
- The test commands took about 46 s of wall time in total. The whole review fit well inside the 1800 s limit.
- Tool stdout hit EDQUOT on `/tmp`, so every command redirected its output to the owned cache.
- All of the author's failed evidence (exit 127 and the 59/60 run) and the A1-04 captures were left untouched.
