# Phase 7: shared terminal styling (`src/tui/style.rs`)

Owner: style sub-agent. Status 2026-10-14: the byte extension is complete and frozen
for the parent's central fmt/clippy/test and immutable-build gates. The earlier Opus
implementation's central unit run 4 passed 376/376; this fallback implementation also
ran 22/22 standalone style tests and the bounded byte/string differential probes below.
The standalone wrapper stubs process profile/background detection; the Go rendering
oracle uses explicit profiles. It does not establish an end-to-end process result.
The canonical original API is in `/home/ajs/.cache/pb-phase7/terminal-requests.md`
("Style owner response"); the additive byte signatures are listed below.

## What it reproduces (pinned sources read from the Go module cache)

| Go source | Rust |
|---|---|
| pkg/ui theme.go / chrome.go / logo.go | `Role`, `Palette`, `DARK`/`LIGHT`, `active()`, `load_theme`, `Color::{role,active,of}`, `Theme::*`, `ui_truncate`, `PB_LOGO` |
| termenv v0.15.2 color.go/style.go | `Color::sequence`, `styled` |
| go-colorful v1.2.0 Hex, HSLuv, DistanceHSLuv | `parse_hex`, `hsluv`, `distance_hsluv` |
| lipgloss v1.0.0 Render, align, borders, join, position, whitespace, set (clamps), Inherit | `Style`, `apply_border`, `apply_margins`, `join_*`, `place*`, `Whitespace`, `Style::inherit` |
| x/ansi v0.4.5 parser table, StringWidth, Truncate, Wrap | `transition_table` (const), `width`, `truncate`, `wrap` |
| uniseg v0.4.7 graphemerules.go, step/FirstGraphemeCluster, runeWidth, graphemeproperties.go, emojipresentation.go, EastAsianWidth | `gr_transition`, `gr_step`, `first_cluster`, `clusters`, `property_width`, `GRAPHEME`, `PICTOGRAPHIC`, `EMOJI_PRESENTATION`, `uniseg_wide` |
| go-runewidth v0.0.16 RuneWidth, StringWidth (test-only oracle helper), handleEnv/IsEastAsian, tables | `bubbles_rune_width`, `runewidth_string` (cfg(test)), `runewidth_rune`, `runewidth_east_asian`, `locale_charset`, `NONPRINT`/`COMBINING`/`DOUBLEWIDTH`/`AMBIGUOUS`/`NARROW` |

### Theme semantics
* `ui.Active` (`active()`, `Color::active`, chrome `Theme`): `PB_THEME` light/dark, then
  `COLORFGBG` whose last field is `15` (≥2 fields), then `HasDarkBackground`. Computed once.
* `ui.Adaptive` (`Color::role`, `Color::adaptive`, `From<Role>`): resolved by
  `HasDarkBackground` on every render, independent of PB_THEME.
* Profile and background reuse the phase-4 caches (`metrics::output::style::Terminal::
  from_process().profile`, `metrics::has_dark_background()`).

### Colors (exact; Go oracle)
* TrueColor `uint8(f*255)` truncation, `hexToANSI256Color` (gray index from cube indices, so
  always 232), `ansi256ToANSIColor` (first HSLuv minimum), and ANSI fg/bg codes.
* `color_conversions_match_go_oracle_table` holds all 82 rows from
  `/home/ajs/.cache/pb-phase7/style-oracle.txt` (Go 1.25.4 linux/amd64; generator
  `style-color-oracle/main.go`). They cover every Dark/Light palette hex, the chart palette,
  bubbles/list literals, `#rgb` and ANSI literals, across TrueColor/256/16-color fg and 16-color
  bg. A test asserts that every palette role is in the table.
* go-colorful's float literals are written in shortest round-trip form (same f64, for clippy).
* arm64: no runtime was available. Go may fuse FMA there, so a 1-ulp difference could only
  flip an exact HSLuv tie. For the 82 fixed inputs the amd64 table is authoritative.

### Unicode width (exact tables, no approximation)
* `width` = x/ansi `StringWidth`: printable ASCII 1, escape sequences 0, and uniseg grapheme
  widths for everything else.
* `GRAPHEME` transcribes uniseg's `graphemeCodePoints` (Control/Extend/Prepend/SpacingMark).
  CR/LF/ZWJ/RI/Hangul classes are matched in code, and `PICTOGRAPHIC`/`EMOJI_PRESENTATION`
  hold the Unicode 15.0 ranges. **Parent audit: 0 mismatches against pinned
  graphemeproperties.go** (`style-grapheme-table-audit.json`).
* Segmentation ports uniseg's transition table (`grTransitions`), including its fallback
  rule-number tie-break (e.g. Prepend+LF breaks, L+Extend+V breaks, Pict ZWJ Extend Pict
  breaks), and its width combination: the first rune's width, VS15/VS16 override after a
  pictograph, and RI and L clusters do not add.
* uniseg's East Asian W/F = go-runewidth `DOUBLEWIDTH` (W ∪ F, Unicode 15.1) minus the 15.1-only
  U+2FFC..2FFF and U+31EF. The per-codepoint oracle (request 4) verifies this.
* `widths_match_go_width_oracle` holds all 20 rows from `style-width-oracle.txt`, across the
  ansi, runewidth and uniseg columns.
* `unicode-width` is no longer used by style.rs.

### Two width functions for two Go libraries
* `rune_width(char)` / `width(&str)`: uniseg / x/ansi. Use these for lipgloss and x/ansi paths.
* `bubbles_rune_width(char)`: go-runewidth `DefaultCondition`. Use it where bubbles calls
  `rw.RuneWidth` (textarea wrap and line widths,
  textinput where it uses runewidth; the editor owner picks per call site). They differ for
  flags (runewidth 1), emoji modifiers (2), VS16 (ignored), U+3030 (2), Prepend/SpacingMark
  clusters, and **locale**. `RUNEWIDTH_EASTASIAN=1`, or a CJK `LC_ALL`/`LC_CTYPE`/`LANG` (for
  example `ja_JP.UTF-8` or `zh_CN.GB2312`), makes ambiguous runes (box drawing `─`, `…`,
  Greek/Cyrillic and others) width 2 in bubbles paths only. Computed once, like runewidth's
  init.

### lipgloss Render details preserved
Per-property `props` bits. SGR order is bold, italic, underline, reverse, blink, faint, fg,
bg, underline again, then strikethrough. There is a separate space styler. Width wraps with
`ansi.Wrap(width-padL-padR)`. Whitespace is unstyled unless bg or reverse is set. Empty
render still emits SGR. Borders follow lipgloss's implicit vs explicit sides and drop corners
for missing sides. Margins wrap their newlines in one SGR span. MaxWidth and MaxHeight are
applied. `Inherit` skips margins and padding, and copies bg into the margin bg. Join and Place
use `math.Round` positions.

## Go panics, hangs and lossy outputs (no silent fixes)
| Site | Go behavior | Rust | Reachable in pb? |
|---|---|---|---|
| `ui.truncate` `runes[:n-1]` | Cut within capacity: NUL runes appended (32-rune stack buffer when ≤32 runes, else malloc size class). Beyond capacity: panic. | Reproduced. Returns `Err(GoPanic("runtime error: slice bounds out of range [:N] with capacity C"))`. `header_strip`/`key_grid` propagate it. | **Yes**, when a KV value or key-hint label has wide runes and width > n (e.g. a CJK profile/dataset name in the header strip). Callers must route `GoPanic` like `EditorFault` (Bubble Tea `Caught panic` recovery). Capacity model to be confirmed by oracle 2 `ui.truncate.*`. |
| lipgloss horizontal edge / whitespace with a zero-width glyph | Infinite loop | Advances one cell | No: all `Border` values are compile-time constants with width-1 glyphs (lipgloss presets and bubble-table glyphs; an empty middle becomes " "), and whitespace chars are `&'static` pb choices (default " "). |
| termenv ANSI profile with index >255 | Index panic | No color | No: numeric specs are only the literals "240"/"62"/"230" (`Color::Ansi` is u8), and `Color::dynamic` only gets chart hex colors. |
| `ansi.Wrap`/`Truncate` with malformed UTF-8 or C1 parser transitions | Wrap re-encodes PRINT/EXECUTE bytes as runes, preserving raw payload/cluster bytes. Truncate copies retained bytes. An OSC/DCS ST can split a valid input rune and produce malformed output. | `wrap_bytes`/`truncate_bytes` and the byte renderer preserve exact Go bytes. Existing string facades retain their established lossy boundary. | Raw SQL dataset and PromQL builder/saved clipping inputs can reach these transitions. **L-STYLE-1 is resolved for callers that carry the byte path through emission; it remains bounded to the existing string facades.** Oracle 2 `wrap.10` is now asserted exactly through the byte renderer. |

## Tests (inline `mod tests` in style.rs)
Palette/theme precedence and adaptive vs active. The 82-row Go color table. Width and
truncate/wrap semantics, the 20-row Go width table across three libraries, uniseg segmentation
transitions, and runewidth locale parsing. Render sequences (double underline, empty, padding,
bg whitespace, margins), borders (implicit, explicit, custom, colored), frame sizes,
Inherit/fg_if_unset, join/place, `ui_truncate` NUL/panic capacity, and Card/Pill layout.

## Remaining unknowns
1. Whole-crate tests and actual styled process/frame parity for the new byte callers remain
   parent-owned gates. The standalone 22/22 style tests and byte probes have run; they do not
   claim process profile detection, PTY read-boundary parity, a release or a benchmark.
2. `strings.ToUpper/ToLower` use one-to-one Rust mappings (multi-char mappings keep the rune;
   U+0130 lowercases to `i`), with a small Unicode-version skew (Go 15.0 vs Rust's tables).
   Only constant titles/keys pass through them in pb.
3. Not ported: lipgloss Transform, SetString/multi-arg Render, table middle glyphs, Strip,
   Hardwrap, Wordwrap.

## Removed unused surface (after unit 4)
These helpers were newly introduced in this phase, and the integrated build reported that no
workflow uses them. They are deleted, not allowed: `is_light`, `fg_sgr`, `bg_sgr`, `sgr`,
`Border::{THICK, DOUBLE, HIDDEN, BLOCK}` and the aliases `Normal/Thick/Double/Hidden/Block`
(`Border::Rounded` stays), `Align::{Left, Top}`, the `Style` setters `blink`, `faint`,
`bg_hex`, `padding_{top,right,bottom}`, `margin_{right,bottom}`, `border_bg`,
`border_bg_sides`, `color_whitespace` and `strikethrough_spaces`, `bubbles_string_width`,
`KvVariant::Mute`, and the `Type` enum plus `Theme::typography` (the pkg/ui Typography port).
The corresponding render fields and Inherit bits stay, so lipgloss Render semantics do not
change. Those properties simply cannot be set until a caller needs them. `clusters` and
`runewidth_string` are now `#[cfg(test)]` oracle helpers (no pb call site uses
runewidth.StringWidth).

## Removed unused surface (after clippy 4)
The non-test lib build reported these as unused by every workflow, so they are deleted along
with the tests that only exercised them:
- pkg/ui chrome: `Theme::{logo, header_strip, kv_block, breadcrumbs, card, card_with_meta,
  pane_title, pill, selection_rail, format_stat}`, `Kv`, `KvVariant`, `Crumb`, `PB_LOGO`,
  `logo_lines`, `go_upper`. Their Go oracle rows stay in `style-oracle2-cases.txt`, so a port
  can be revalidated if a workflow ever needs one.
- Border: `Border::Custom` and `BorderChars` (callers use struct literals); the frame-size
  helpers.
- Align: `Align::{Center, Bottom}` (`Align::Right` stays; use the `CENTER`/`BOTTOM` constants).
- Style setters: `underline`, `strikethrough`, `fg_if_unset`, `align_vertical`, `margin`,
  `margin_top`, `margin_background`, `underline_spaces`, `tab_width`,
  `horizontal_frame_size`, `vertical_frame_size`.
- `block_height`.

`rune_width` (the uniseg width of a single rune) is now a `#[cfg(test)]` oracle helper. pb
code measures strings with `width`. `Role` keeps theme.go's complete frozen palette under a
targeted `#[cfg_attr(not(test), expect(dead_code, reason = …))]` on the enum. The color-oracle
tests construct every variant, so the expectation applies only to the non-test build. Render
fields and Inherit bits for the removed setters stay, so lipgloss Render semantics are unchanged.

## Oracle 2 validation
`style-oracle2-widths.txt` (1,331 run-length ranges over every non-surrogate code point) is
embedded verbatim as `WIDTH_DUMP`. `every_codepoint_width_matches_go_dump` checks, for every
code point, `rune_width`/`width` against uniseg and `runewidth_rune` against go-runewidth in
both EastAsianWidth modes. A manual review of all class boundaries found no mismatch: the
uniseg 15.0/15.1 deltas at U+2FFC..2FFF and U+31EF, Pict non-emoji runes width 1 (U+1F260..65,
U+3030, U+303D, U+3297/9), Extend vs runewidth combining, Prepend/SpacingMark/Control vs
nonprint, and private-use/VS/ambiguous width 2 in CJK mode. `cases_match_go_oracle2` adds the
x/ansi, lipgloss and pkg/ui cases that earlier tests did not already cover. That includes the
full `HeaderStrip` at widths 100 and 60, `CardWithMeta`, `Breadcrumbs`, `PaneTitle` and
`FormatStat`. `ui.truncate.4..6` confirm the inferred capacity model exactly: NUL padding,
`[:59] with capacity 40`, `[:39] with capacity 36`. `wrap.10` originally recorded L-STYLE-1 (Go emits
`…\xe2\x9c\ny z`; the string facade gives U+FFFD for E2 9C). The byte extension below
adds an exact assertion for that malformed output while keeping the string behavior.
All original style tests passed in the standalone 22-test run.

## Additive byte path and bounded validation

All byte APIs borrow input and return an owned `Vec<u8>` when producing output:

```rust
width_bytes(s: &[u8]) -> usize
truncate_bytes(s: &[u8], length: usize, tail: &[u8]) -> Vec<u8>
wrap_bytes(s: &[u8], limit: usize, breakpoints: &[u8]) -> Vec<u8>
ui_truncate_bytes(s: &[u8], n: isize) -> Result<Vec<u8>, GoPanic>
join_horizontal_bytes(position: impl Into<f64>, blocks: &[&[u8]]) -> Vec<u8>
join_vertical_bytes(position: impl Into<f64>, blocks: &[&[u8]]) -> Vec<u8>
place_bytes(width: usize, height: usize, hpos: impl Into<f64>,
            vpos: impl Into<f64>, s: &[u8]) -> Vec<u8>
Style::render_bytes(&self, text: &[u8]) -> Vec<u8>
Style::render_bytes_in(&self, text: &[u8], env: Env) -> Vec<u8>
```

The backend factors the existing Vec wrap/truncate algorithms and the private rendering,
alignment, border, margin, join and placement logic. Byte width walks the same complete
x/ansi transition table. Grapheme parsing decodes malformed UTF-8 as one U+FFFD per input
byte, as Go's `utf8.DecodeRune` does, but selects original byte spans for cluster output.
Raw bytes outside the parser's UTF8 transition are handled by their actual state/action;
there is no dataset-specific mapping, placeholder or sentinel. `ui_truncate_bytes` retains
uncut input verbatim, and only uses rune decoding when Go converts to `[]rune` for a cut.
The underline/strikethrough space-styler branch also follows Go's rune iteration; its
replacement of malformed bytes is intentional Go behavior, unlike an early lossy adapter.

For the SQL example `B5 8B E8 AF 95`, `width_bytes` is 2 and an uncut UI truncate returns
the original input. Attribute rendering retains it. The final Width wrap re-encodes 8B
as `C2 8B` with word width 1 while preserving B5, giving `B5 C2 8B E8 AF 95`. This applies
to every reachable C1 parser action, not just that example. Joins and centered placement
measure raw bytes before emission. No parent SQL, terminal, PromQL, saved, lib or Cargo
file was edited by this owner, and `style-notices.txt` (including the parent's reflow
section) was preserved.

The ordinary string APIs stay available. The string renderer calls the existing `wrap`
facade after validating the still-valid pre-wrap input, retaining its lossy **post-wrap**
boundary. The byte renderer calls `wrap_bytes` directly. This distinction preserves the
old string contract even when a valid OSC-containing input produces malformed Go output.
Returning a `String` still cannot represent L-STYLE-1's raw bytes; only a caller retaining
`Vec<u8>` through the terminal can resolve it.

The bounded Go 1.25.4 oracle used the untouched original tree at
`/home/ajs/.cache/pb-phase7/sql-lower-oracle/tree` (07af390), x/ansi v0.4.5, lipgloss
v1.0.0, uniseg v0.4.7 and termenv v0.15.2. It compared 2,808 inputs across 13 exact
results each (36,504 comparisons), with **zero mismatches**. Inputs cover all 32 C1
bytes in all 14 persistent parser states at limits 0/1/3/8, all 256 single bytes,
malformed leads/continuations and Unicode clusters after invalid bytes, malformed tails
and breakpoints, UI slice capacity/NUL/panic cases, plus deterministic mixed and valid
strings. UTF8 is not a persistent state in these helpers: entering it consumes a grapheme
and resets to Ground. The malformed cluster cases exercise that branch separately.
Results include plain Width, colored padding/border/margin/height/max clipping, inline
and underline rendering, joins, and center/quarter placement. No arbitrary-stream
exhaustiveness or kernel-read-split claim follows from this finite corpus.

The pre-change string backend and final backend matched on 428 valid UTF-8 inputs across
35 comparisons each (14,980 comparisons, zero mismatches), including all four color
profiles and five join/place positions. The standalone suite passed **22/22**, including
the previous exhaustive codepoint and color tables, 888 durable byte fixtures, and raw
render/L-STYLE-1 regressions. An initial hand-written string-render expectation had one
padding space instead of two; that test expectation was corrected. The independent
Go differential found no algorithm mismatch.

During central integration, the parent formatted style.rs and reported two Clippy issues:
serde_json made the fixture `parse()` type ambiguous, and the string `wrap` facade became
unused. This owner specified `parse::<usize>()` and restored the string renderer's use of
`wrap` without changing the lossy boundary. After those fixes and rustfmt, the standalone
suite, complete byte oracle and before/after string comparison all ran again and passed.
`style-byte-oracle-summary.json` preserves the initial source hash and records the final
formatted/fixed source hash separately; those hashes are snapshots, not a claim that later
parent edits have been probed.

Durable evidence is under `docs/evidence/phase7/style-byte-*`: generator, Go and Rust
probe sources, standalone wrapper, pinned Go module files, compressed pre-change Rust
source, the 888-row inline-test fixture, and full exact Go/Rust results in JSONL gzip.
The summary records versions, source/evidence hashes, columns, test counts and bounds.
Only storage is compressed; output hex is not normalized. Scratch executables and
intermediate logs are under `/home/ajs/.cache/pb-phase7/style-byte-probe/`.

To replay the helpers, copy the saved module/probe files there, use the Go 1.25.4 executable
from `/home/ajs/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64/bin/go`, and
set `TMPDIR`, `GOTMPDIR`, and `CLAUDE_CODE_TMPDIR` to `/home/ajs/.cache/pb-phase7/tmp`.
The generator writes `cases.tsv`; `go run . < cases.tsv` emits the Go TSV. For the Rust
probe, copy style.rs to `style-probe.rs` and append the diagnostic-only in-module helper
`pub(crate) fn probe_underline() -> Style { let mut s=Style::new(); s.underline=true;
s.props|=P_UNDERLINE; s }`. Compile the saved main with `rustc --edition=2024`; its output
must exactly equal the Go TSV. The saved standalone wrapper can be compiled with
`rustc --edition=2024 --test` against the live repository style.rs. For the before/after
comparison, decompress `style-byte-before.rs.gz` into scratch `style-before.rs` and compile
the saved comparison source. These are presentation helpers, not a candidate build.

No benchmark, timing campaign, remote-account action, release, commit or candidate rebuild
was performed. Whole-crate fmt/clippy/tests, emitted process bytes, terminal/frame parity
and the parent's immutable build remain separate gates.

## Attribution
The ported behavior and tables derive from charmbracelet lipgloss and x/ansi, muesli termenv,
lucasb-eyer go-colorful (incl. hsluv-go), rivo/uniseg and mattn/go-runewidth (MIT). The Unicode
property data comes from the Unicode Character Database (GraphemeBreakProperty, emoji-data,
EastAsianWidth), © Unicode, Inc., used under the Unicode License
(https://www.unicode.org/license.html). The same notice is in the style.rs module docs.
The verbatim upstream copyright/license notices (lipgloss, x/ansi, termenv, go-colorful and its
hsluv-go header, uniseg, go-runewidth, Unicode License V3), the exact pinned versions, and the
oracle commands that produced each embedded fixture are in
`docs/evidence/phase7/style-notices.txt`.
