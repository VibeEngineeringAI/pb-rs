# PR5 SQL header uppercase delta — independent review (Opus, read-only)

**Verdict: APPROVED for the scoped delta. No confirmed findings.**

Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` (HEAD 0ab7785, uncommitted). No source edits, commits, GitHub/wiki writes or delegation. 2016-frame replay, full tests and new casing-site searches were not run, as instructed.

## Source hashes (verified, match ui-upper/freeze.json)
- src/tui/sql.rs `448a4b00aa52828274fdceab8f0f107ad80293bc475803f516115c36946acbbb`
- src/tui/unicode.rs `7f419f5d7ab6feee163ccc3f5b75a130c5a5c95e5a877d086cdda45eaf19f8f0`
- src/tui/promql.rs `143ca978…bf37d`, src/tui/mod.rs `e122d6f3…db6e`: identical to the original ui/freeze.json
- Baseline used for the diff: `ui-review/rscopy` (sql `404c2eb8…`, unicode `e3236677…`), which is the approved original freeze

## What I verified
1. **Delta scope** (`diff -u rscopy current`, s5.log). sql.rs has exactly three hunks: the title path `go_to_upper(&s.title)` becomes `super::unicode::upper(&s.title)`, the old `go_to_upper` helper is removed, and one new test is added. unicode.rs only changes the module doc wording and adds `upper_rune`/`upper` plus the `UPPER` table. The `LOWER` table and the `tests` module are byte-identical to the baseline (diffed separately). `lower_rune`/`lower` are textually unchanged.
2. **Title path parity.** The pinned Go `pkg/model/query.go:2829` is `table.NewColumn(s.key, strings.ToUpper(s.title), s.width)`. Rust `sql.rs:3095-3100` uses `key: s.key` (original) and `title: unicode::upper(&s.title)`, so it is structurally identical. `render_headers` and `set_columns` are unchanged. The test checks the stored title, the rendered header and the preserved key.
3. **Exhaustive table, independent oracle** (my own programs, s6.log). I ran `go/main.go` under the pinned go1.25.4 (`runtime go1.25.4 unicode 15.0.0`). It checks `unicode.ToUpper == strings.ToUpper` on all 1,112,064 scalars and dumps upper and lower. `rs/main.rs` includes the production `unicode.rs` via `#[path]` and checks rune/string agreement for every scalar.
   - Upper dump: Go = Rust = ui-upper/go-upper-map.txt = `96f4d87807a9018d5fa744b48bf2e58e6a8253d2b9cf3e0006c4385c3cfa060f` (1450 mappings).
   - Lower dump: Go = Rust = ui/go-lower-map.txt = `4f5403b20e11329417bbfdde1b9a15a7fcaa3a3dd0056a47a744d3747fc7934c` (1433 mappings). **Lowercase is unaffected.**
   - Go source provenance: tables.go `6a88d487…0bdb1` and letter.go `b35a6d25…74fe` match the original freeze.
4. **Generator provenance** (s7.log). `python3 -I generate-upper-ranges.py` (`8bff3fec…`) run on *my* Go dump reproduces the production `const UPPER` byte-for-byte: 200 ranges, `07000aba…7cd9` = generated-upper-ranges.txt. ASCII uses a fast path, the same as Go's `strings.ToUpper` and `unicode.ToUpper` ASCII branches. Go's `strings.ToUpper` is `Map(unicode.ToUpper)` for non-ASCII input, so the context-free per-char `chars().map(upper_rune)` is exact.
5. **Simple mapping vs expansion controls.** Go sample `"ɤƛ𐵰http_statusstraßeﬀςσǅİ"` becomes `"ɤƛ𐵰HTTP_STATUSSTRAßEﬀΣΣǄİ"`, and the Rust production output is identical. So ß, ﬀ and İ are kept (no expansion), final σ/ς gives ΣΣ (no context), and titlecase ǅ gives Ǆ.
6. **Old vs new.** Across all scalars the removed helper differed from Go on **82** scalars, not just the 3 flagged by R2:
   - ɤ, ƛ, U+1C8A, A7CD/CF/D3/D5/DB, Garay 10D70–: newer Rust Unicode
   - U+1F80–1FAF, 1FB3/1FC3/1FF3: Rust expands to two characters, so the old helper kept the original, while Go maps them to titlecase-form capitals such as ᾀ→ᾈ

   The new table fixes all 82.
7. **RED/GREEN evidence** read: red.log fails on `ɤ` (actual `Ɤ`) with pre-test hashes equal to the approved freeze. green-header passes 8/8 cases, green-sql 41/41, green-unicode 1/1 (the lowercase boundary test is unchanged).

## Non-blocking notes
- Test coverage gap (optional): the header test has no case for the "Go maps, Rust expands" direction, e.g. `ᾀ`→`ᾈ`. The exhaustive oracle above covers it, so this is not a defect.
- Out of scope, not investigated (per instruction): `promql.rs:5059` `builder_column` uses `title.to_uppercase()`, mirroring Go `promql.go:2473` `strings.ToUpper(title)`. It is in the frozen, already-approved PromQL file and outside this delta. If its titles are fixed ASCII labels it is harmless. Parent may confirm whether any caller passes user data.
- `sql_service.rs` differs from the rscopy snapshot. That belongs to the separate decoder stream and was not reviewed here.

## Commands (env: TMPDIR/GOTMPDIR/GOCACHE under this dir, GOTOOLCHAIN=local, GO111MODULE=off)
- `go=/home/ajs/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64/bin/go; $go run go/main.go go-upper.txt go-lower.txt`
- `/home/ajs/.cargo/bin/rustc --edition 2024 -Awarnings -O rs/main.rs -o rs/check && rs/check rs-upper.txt rs-lower.txt` (rustc 1.96.0)
- `sha256sum`/`cmp` of the dumps against ui-upper/go-upper-map.txt and ui/go-lower-map.txt
- `python3 -I ../ui-upper/generate-upper-ranges.py go-upper.txt > gen-upper.txt; cmp` against `sed -n '/^const UPPER/,/^];/p' src/tui/unicode.rs`
- `diff -u ../ui-review/rscopy/src/tui/{sql,unicode}.rs src/tui/…`, plus a separate LOWER-table and tests-module diff
- Logs: s1–s8.log; artifacts: go/, rs/, go-upper.txt, rs-upper.txt, go-lower.txt, rs-lower.txt, gen-upper.txt, prod-upper.txt
