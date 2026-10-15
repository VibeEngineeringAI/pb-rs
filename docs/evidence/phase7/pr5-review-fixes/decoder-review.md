# Decoder review: nested duplicate numeric overflow (PR #5)

Read-only review. Workspace `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`, HEAD `0ab778525ecebe53cc19f4e3e27f32119290ab25`. Reviewed `src/tui/sql_service.rs` (sha256 `5820e6c6…1047`, which matches `decoder/verification.json`). The worktree was not modified. Its sha was re-checked after all runs (`mutant.log`). All execution happened in a sandbox copy at `decoder-review/crate` with `CARGO_TARGET_DIR=decoder-review/target` and `TMPDIR=decoder-review/tmp`.

## Verdict

**The decoder change is approved for correctness and Go parity.** One confirmed, low-severity **test-quality gap** remains (below). There are no correctness, first-error, depth, or performance defects.

## Evidence

| Check | Command / artifact | Result |
|---|---|---|
| TDD record | `decoder/TDD.md`, `red.log`, `green.log`, `go-oracle.log` | RED: 18 pass / 3 fail. The failures are the intended ones: `Ok` for overwritten values, `1e400` instead of `1e401` for wire order, and `Ok` at depth 9000. GREEN: 21/21 pass. |
| Focused suite (independent rerun) | `cargo test --lib tui::sql_service::tests::` → `focused.log` | exit 0, 21 passed |
| My Go oracle (go1.25.4, v1 `json.NewDecoder().Decode(&QueryData)`) | `oracle/main.go cases.txt` → `go-oracle.log` | 21 probes |
| Same probes in Rust (fixed tree) | `review_probes::review_probe_cases` → `probe-cases.log` | **21/21 identical to Go**. Covered: record-level and cross-cell wire order, an earlier record vs a later record, a records error before a later `fields` error, a raw `-1E400` literal, a duplicate `records` key, DFS order (`{"b":[1,{"c":1e402}],"a":1e401}` gives 1e402), underflow/-0/MaxFloat64 display, the `1.7976931348623159e308` overflow, `Records` case-fold, a non-object record before the overflow (type error wins), and depth 9996+wrapper (= 10000, accepted) / +1 (`exceeded max depth`). |
| Small stack | The depth cases above ran on a **256 KiB** thread | No overflow. Validation is heap-iterative; `write_display` still uses `stacker`. |
| Independent oracle matrix (`decoder-oracle/report.md`) | Appended `decoder-oracle/probe/rust_probe_module.rs` to the sandbox, ran `decoder_oracle_probe_matrix`, then `python3 -I compare.py results.tsv rust-fixed.tsv` → `oracle-matrix.log`, `compare-fixed.txt` | **total=63 diff=0** (HEAD had 26/63 diffs, per the oracle report). Covers A1–A11, B1–B8, C1–C9, D1–D6 (skipped values are *not* converted), E1–E20, and F2–F5 including the 9997-deep max boundary. Input sha256 `6eb168d9…` and results sha256 `0cdc7a3d…` match the oracle report. |
| Performance (release) | `review_probe_timing` → `probe-timing.log` | Validate vs format: 2M-element numeric array 70 ms vs `write_display` 202 ms; 9996-deep 0.12 ms; 200k duplicate-key members 28 ms. Validation is one linear pre-order pass per cell, with no re-validation per nesting level. The extra memory is one pointer per pending sibling, which is small next to the `Node` tree. |

Code reading confirms the following:
- `validate_interface_numbers` runs pre-order DFS over the raw ordered `Node`, keeping duplicates. It pushes siblings in reverse, so it pops them in wire order. It runs only from `go_display`, which only runs for record cell values. Skipped values (unknown keys, wrong-kind `fields`, non-object records) are never validated, which matches Go's `d.skip()` behavior (D1–D6).
- `query_data` iterates top-level members, records, and record members in wire order, and `note()` keeps the first error. A nested cell returns its first error before the next member is examined, which matches Go `saveError`.
- `go_float64` reuses the previous check exactly (raw lexeme, parse, `is_infinite`). The error text is unchanged.

## Confirmed issue

1. **[Low, test quality] Array element order in the new validation pass is not pinned by any test.** In the sandbox I applied the mutant `pending.extend(items.iter().rev())` → `pending.extend(items.iter())` (`sql_service.rs:877`). All 21 focused tests still pass (`mutant.log`: `21 passed; 0 failed`). The behavior is still wrong: `{"records":[{"r":[1e401,1e400]}]}` makes the mutant report `1e400`, while Go 1.25.4 reports `1e401` (`go-array.log`). The unmutated tree reports `1e401`. Object member order *is* pinned, by `{"z":1e401,"a":1e400}`.
   **Fix:** add that array case (oracle B7) to `nested_numeric_errors_follow_wire_order_before_map_sorting`, expecting `…number 1e401…`. This is test-only; no production change is needed.

## Scope notes and limitations

- The oracle report lists other cheap optional cases (B4, C2, C3/C4, D1/D3/D6, E4, E10, A4, A6/A7, F4 9997-deep). The fixed code already matches Go on all of them. I report only the array-order gap as actionable, because it is the one plausible regression in the new code that the current suite would miss. The others are good to add but not required.
- I did not run the full test suite, clippy, or end-to-end TUI or pinned-binary flows. Validation was focused unit tests plus the probe matrices.
- The Go oracle covers v1 `encoding/json` (go1.25.4, no `jsonv2` experiment), which is the version pb uses.
- Bash stdout capture was unavailable (the /tmp quota is full), so all output was redirected to the logs in this directory.
