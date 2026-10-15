# PR #5 UI fixes: independent review (read-only)

Scope: the dirty `src/tui/{sql,promql,unicode,mod}.rs` changes from HEAD `0ab7785`. I made no source edits, commits, GitHub writes or delegations. All scratch work is in this directory.
Reviewed file hashes match `ui/freeze.json`: mod `e122d6f3…`, sql `404c2eb8…`, promql `143ca978…`, unicode `e3236677…`.

## Verdict

**Approved for the scoped fixes.** I found no confirmed defect in the dirty changes. There is one confirmed issue in the same bug class, but it is pre-existing and outside the diff (A1). There is also one stale doc reference for the parent (A2).

## Independent evidence

1. **Lowercase mapping.** I wrote my own oracle, `golow/main.go`, and ran it with pinned go1.25.4 (`GOTOOLCHAIN=local`). It visits all 1,112,064 scalars and checks `unicode.ToLower`. It also checks that `strings.ToLower(string(c))` agrees, and found 0 mismatches.
   The Rust side (`rs/main.rs`) includes the actual `src/tui/unicode.rs` and checks both `lower_rune` and `lower` for every scalar. Both sides produce 1,433 changed mappings. The outputs are byte-identical: SHA `f4944fbf…` in this format, for both `go.txt` and `rs.txt`.
   Strings checked: Greek final sigma (`ΟΔΟΣ ΣΑΣ` → `οδοσ σασ`), `İ` → `i`, titlecase `ǅǈǋ`, and Kelvin/Ångström/Ohm signs. All match.
2. **Call sites.** Every lowercase site in the two files now uses `unicode::lower`:
   - SQL: datasets/columns (`filter_names`), result rows (`Table::visible_rows`), and the byte-offset `go_lower_index`.
   - PromQL: `filter_datasets`, `filter_builder_list`, and `ResultsTable::visible_rows`.

   Each one corresponds to a `strings.ToLower` in pinned Go: `promql.go:2354-2375`, bubble-table `isRowMatched`, and query.go.
   The remaining `to_lowercase` (`sql.rs:3984`) only lowercases constant hint labels. The ASCII `eq_ignore_ascii_case` sites were not changed.
3. **ResultsTable scroll vs pinned bubble-table v0.19.2.**
   - Source checks:
     - **Keys:** pb's `tablekeymap.go` binds ScrollRight to `right`/`d` and ScrollLeft to `left`/`a`.
     - **Scroll bound:** `maxTotalWidth` stays 0, so `recalculateLastHorizontalColumn` always gives a maximum index of 2 (`len-1`). That matches `scroll < 2`.
     - **No reset:** `horizontalScrollOffsetCol` is never reset by `WithColumns`, `WithRows` or `WithTargetWidth`. `table.New` runs only at construction (Go `promql.go:348`; Rust `ResultsTable::new` only at `promql.rs:3118`). So the offset persists across resize, new results and empty or error rows, as implemented.
     - **Filter precedence:** a focused filter routes keys to the textinput first (`Update` → `updateFilterTextInput`), which is the same as the Rust early return.
     - **Routing:** chart-mode Left/Right interception and the `t`/`w` handling come before the table on both sides. `a`/`d` still reach the table in chart mode on both sides.
     - **Borders:** the `<` cell uses the left style, and later columns use inner/right. Rust's `borders[i]` with the original index produces the same choice.
   - **Rendered frame oracle.** I wrote two probes and fed them the same inputs.
     - The Go probe (`gocopy/pkg/model/zz_review_test.go`) uses pinned bubble-table with pb's exact `headerStyle`, `tableStyle`, `highlightStyle`, `customBorder`, `tableKeyBinds` and missing-data indicator.
     - The Rust probe is a test appended to a scratch copy of the crate (`rscopy`, built with its own target dir).

     Each probe ran 2,016 cases:
     - widths 36, 40, 60 and 120
     - VALUE widths 5, 14 and 24
     - 0, 1 or 3 rows, with wide CJK, long and newline metrics
     - page sizes 5 and 2, cursor 0 or 2
     - key sequences `""`, `d`, `dd`, `ddd` (clamp), `ddda`, `dad`, and `/d` (filter consumes the key)
     - focused and unfocused

     The ANSI-stripped frames are byte-identical (`go-frames.txt` = `rs-frames.txt`, SHA `d6ad29fb…`).
     The styled frames are also byte-identical (`go-raw.txt` = `rs-raw.txt`, SHA `b67a65c9…`). For those, Rust used `CLICOLOR_FORCE=1` (ANSI profile) and Go used `termenv.ANSI` with a dark background. This covers the highlighted `<` cell and the header `<` (Accent, bold).
     The scroll offsets reported by Go `GetHorizontalScrollColumnOffset` equal Rust `scroll` in every case.
   - These results agree with the UI oracle's PTY golden (`ui-oracle/promql-scroll-go-golden.txt`) and its model checks (`gotable.txt`): unfocused, shift/alt, paste and multi-rune keys are no-ops, and zero rows or zero target width still scroll. `ui-oracle/report.md` did not exist at review time.
4. **Arc sharing.** Scrolling only changes `scroll`. The `rows` Arc is untouched, and the tests assert `Arc::ptr_eq`.
5. **Tests.** The six new tests are meaningful. Each Unicode test checks both match directions, plus the non-matches (final sigma ς, combining-dot `i̇`). The scroll tests cover clamping, restoring the original frame, resize resetting VALUE to 5 (consistent with Go `updateTableColumns`), new results, the error/empty path, and filter precedence. I did not re-run the full suite, per the brief.

## Adjacent items (not blocking this round)

- **A1 (confirmed, pre-existing, outside the diff):** `sql.rs:4091` `go_to_upper` still uses Rust's std uppercase tables (rustc 1.96, Unicode 16+). It feeds SQL column header titles (`sql.rs:3097`; Go `query.go:2829` `strings.ToUpper(s.title)`).
  Probe results:
  - Rust: `ɤ` U+0264 → U+A7CB, `ƛ` U+019B → U+A7DC, U+10D70 → U+10D50
  - pinned Go 1.25.4: all three unchanged (`up/`)

  So a column with one of these names gets a different header. A pinned Go-15 uppercase table, the same approach as `unicode.rs`, would fix it. This round's lowercase findings did not cover it.
- **A2 (docs, parent-owned):** `docs/phase7-sql.md:216-222` still describes the removed `go_lower_rune` and the special-case-only probe. The evidence files under `docs/evidence/phase7/sql-lower-map*/main.rs.txt` likewise reflect the old helper.
