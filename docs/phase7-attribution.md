# Phase7 attribution inventory and I4 boundary

The repository notice preservation portion of final R1 I4 is implemented in
[`THIRD-PARTY-NOTICES`](../THIRD-PARTY-NOTICES). It preserves the original 17
complete local license texts, adds the historical hsluv-go MIT notice and two
tagged Unicode license texts, and preserves the two legal codes referenced by
the DEC specification page with their conflicting provenance clearly marked.
That is 22 complete license/reference texts, plus the two distributed UCD
ReadMe notices and DEC source credit. Source, version and repository mappings
are explicit. Narrow historical provenance unknowns and distribution delivery
remain open; this is not a claim that final
R1 I4 or Opus R6's remaining I4 shipping requirement has passed.

Repository work is confined to the worktree at
`/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`, Rust base
`4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`, and Go baseline
`07af390ab09bda8ac9e427ffc6310077bace0125`. The worktree `go.mod` and `go.sum`
were compared byte for byte against that Go commit. The audit records copied or
derived Phase7 algorithms, behavior and data, including existing helpers reused
by Phase7, rather than all Cargo dependencies. Project-owned `pkg/ui` and
`pkg/model` material remains governed by the repository `LICENSE`.

## Inventory

- **Go 1.25.4, BSD:** PromQL sort/pdqsort/stable helpers, Unix/time formatting,
  hex-float parsing and underscore validation; Go Unicode 15.0.0 fold/Ll/Lu data
  generated into `src/tui/saved_fuzzy_unicode.rs`. The existing diagnostic
  printable ranges in `src/printable.rs` also retain their Go attribution.
  `go_f64_to_i64` matches the observed Go linux/amd64 conversion; the audit did
  not identify a separately copied compiler implementation for that helper.
  The notice includes the mapped Go source copyright headers (2009, 2013,
  2017, 2022) as well as the complete LICENSE.
- **Styling, MIT:** lipgloss **v1.0.0**, x/ansi **v0.4.5**, termenv **v0.15.2**,
  go-colorful **v1.2.0**, uniseg **v0.4.7**, and go-runewidth **v0.0.16**, mapped
  primarily to `src/tui/style.rs`. x/ansi Wordwrap/Hardwrap also appear in the
  editor. Phase4 terminal profile behavior is reused from `src/metrics/style.rs`.
- **reflow v0.3.0, MIT:** `reflow_width`/`reflow_truncate` in `src/tui/sql.rs`;
  PromQL also calls these helpers, including its bubble-table clipping.
- **sahilm/fuzzy v0.1.1, MIT:** saved-title scoring, candidate retention,
  separators, bonuses, byte penalties and folding in `src/tui/saved.rs`.
  The cached `fuzzy.go` explicitly credits `strings.EqualFold`; the Go BSD
  notice is preserved too.
- **Bubbles v0.20.0 and Bubble Tea v1.2.2, MIT:** editor/cursor/viewport/keymaps,
  list/filter/pagination and spinner data; terminal key sequences/decoding and
  renderer/lifecycle behavior in the native Phase7 modules.
- **atotto/clipboard v0.1.4, BSD:** clipboard utility order, arguments, missing
  utility text and DOS trimming in `src/tui/clipboard.rs`.
- **bubble-table v0.19.2 and ntcharts v0.5.1, MIT:** SQL/PromQL result tables,
  border inheritance and clipping; PromQL chart canvas, line glyph combination,
  Bresenham, scaling and column buckets. The project-specific nice Y scale,
  compact value formatting, legend and palette come from pb's Go model.
- **charmbracelet/x/term v0.2.1, MIT; golang.org/x/term v0.25.0, BSD:** terminal
  raw flags/state/restore/size. The direct baseline call path uses Charmbracelet's
  package; the matching Go Authors implementation's notice is retained too.
- **Unicode data:** uniseg and Go Unicode tables explicitly identify **15.0.0**;
  go-runewidth's generator explicitly requests EastAsianWidth/emoji **15.1.0**.
  The complete cached Unicode License V3 text is preserved, with its carrier
  origin distinguished from the original UCD downloads.

The machine-readable evidence is
[`attribution-inventory.json`](evidence/phase7/attribution-inventory.json).
Each component records upstream and repository file SHA-256 hashes, license
byte counts/hashes, notice section offsets, cache origins and public source
origins where available. For the 15 Go modules, locally cached `.info` files
identify tag/commit origins; archive `h1` hashes were recomputed and matched to
the pinned `go.sum`, and mapped source/license bytes matched those archives.
For the Go toolchain, local `VERSION` identifies Go 1.25.4, and mapped sources,
VERSION and LICENSE matched the cached toolchain archive. These are offline
content checks. The bounded follow-up adds public primary upstream reads and
historical comparisons, recorded with raw responses, URLs, retrieval times,
status and hashes. No distribution audit was performed.

## Reconciliation of existing evidence

`git ls-files --error-unmatch src/LICENSE-Go.txt` confirms that the Go notice
is already tracked. It is byte-identical to the cached Go 1.25.4 LICENSE.
The R1 concern about session-start status omissions does not establish whether
a file is tracked or shipped. Tracking is confirmed; shipping is not.

All six styling module license texts, Unicode V3 and reflow in
`docs/evidence/phase7/style-notices.txt` match their primary local cached texts.
That evidence file already has reflow in section 9, so R1's statement that the
MIT notice exists only in a source comment is stale. The full comment above
the SQL helpers also matches the cached reflow license after removing comment
prefixes, consistent with `docs/phase7-sql.md` section 4a. The new repository
notice consolidates these texts and adds the other identified derivatives.

The style evidence's statement that reflow is used only by SQL omits PromQL
callers. Its assertion that go-colorful's notice covers hsluv-go is not adopted
as a verified attribution conclusion here. These existing files were inspected
and preserved; this assignment edits only the new notice, this doc and the
inventory JSON.

## Primary provenance follow-up

**HSLuv notice identified (section 18).** go-colorful introduced `hsluv.go` at
commit `4380d764288e7022c51d648eb7e37dc1804e2300` on 2021-01-27. Public pinned
v1.2.0, that introduction and the local module-cache file are byte-identical.
The importer header identifies hsluv-go and the saturation/luminance change.
The latest upstream `hsluv.go` path-history change before import is commit
`a29f6990a3be79fefb4470f2a0b9a4d076949577` (2016-12-17). Its six shared helper
function bodies match the importer after ignoring whitespace: `getBounds`,
`maxChromaForLH`, `maxSafeChromaForL`, `intersectLineLine`, `distanceFromPole`,
and `lengthOfRayUntilIntersect`. Its complete MIT notice names **2015 Alexei
Boronine** and is identical to the license-introduction commit
`34f81136a322719b960c333f4767e9764de6328d` (2016-01-17).
The [immutable upstream notice](https://raw.githubusercontent.com/hsluv/hsluv-go/a29f6990a3be79fefb4470f2a0b9a4d076949577/LICENSE.txt)
is now preserved, independently of go-colorful's own LICENSE. The importer's
exact adopted upstream tree SHA is still unrecorded; the inspected source
revision is identified by history, not asserted as that exact adopted tree.

**Unicode release attribution identified (sections 19–22).** The public
[15.0.0 ReadMe](https://www.unicode.org/Public/15.0.0/ucd/ReadMe.txt) and
[15.1.0 ReadMe](https://www.unicode.org/Public/15.1.0/ucd/ReadMe.txt) match the
ReadMe members of Unicode's versioned UCD ZIP archives exactly. Both full
notices are preserved: dated 2022-09-02 / 2023-08-28, copyright 2022 / 2023,
and the shared terms-of-use URL. Neither archive contains a separate LICENSE
or copyright file. The input data headers repeat their release-specific
Unicode credit and terms-of-use link.

Two primary historical Unicode repository snapshots provide complete license
texts with a verified connection to the distributed data:

- `unicode-org/unicodetools` tag `release-2022-09-16-u15-0`, commit
  `f6478e318a43632989bda65b8e0134088efd9c5f`: its `ucd/dev` ReadMe,
  GraphemeBreakProperty, emoji-data, EastAsianWidth, CaseFolding and UnicodeData
  files match all six corresponding UCD 15.0.0 ZIP members byte for byte.
  The [root LICENSE](https://raw.githubusercontent.com/unicode-org/unicodetools/f6478e318a43632989bda65b8e0134088efd9c5f/LICENSE)
  is the earlier Data Files and Software agreement, copyright **1991-2020**;
  its complete original bytes, including BOM, are preserved in section 19.
- Tag `final-15.1-20230908`, commit
  `9595f090650e99e3e752b37a7a3866ac8a91999b`: its EastAsianWidth and emoji-data
  files match the UCD 15.1.0 ZIP members exactly. Its dev ReadMe is a publication
  template with placeholders; it is explicitly not equated to the released
  ReadMe. The [root LICENSE](https://raw.githubusercontent.com/unicode-org/unicodetools/9595f090650e99e3e752b37a7a3866ac8a91999b/LICENSE)
  is Unicode V3 with copyright **2001-2023**, preserved in section 20.

The original local `unicode-ident 1.0.24` carrier text (1991-2023) remains
unchanged in section 17. The current shared Unicode license page now says
1991-2026; it is retained as raw evidence only and is not substituted for a
historical notice. The exact release-time bytes of the shared terms-of-use or
license website remain unknown. The tagged LICENSE files establish their own
historical repository texts, not identity with that historical website.

**DEC author credit identified; specification license unresolved (sections
23–25).** The public x/ansi v0.4.5 transition source matches the local pinned
module. Its path history starts at the 2024-05-08 move commit
`1af817ff5ef91ba5321c5e5ffa1f2632df6e8f32` and includes four subsequent fixes
through 2024-08-05. The source links
[Paul Flo Williams's DEC parser specification](https://vt100.net/emu/dec_ansi_parser).
The retrieved page visibly credits **Copyright 2002‒2017 Paul Flo Williams**.
Its embedded SVG metadata names **CC BY-NC-SA 3.0**; the **CC BY 4.0** footer
statement is inside an HTML comment. These are distinct, conflicting source
signals. The credit, exact declaration fragments, and both complete linked
Creative Commons legal codes are preserved as source-reference evidence;
neither is selected as the effective historical specification license, nor
used to replace x/ansi's MIT notice.

The page also links Josh Haberman's vtparse implementation. Its primary README
at commit `198ea4382f824dbb3f0e5b5553a9eb3290764694` calls that implementation
public domain. x/ansi links the specification, not vtparse; the bounded reads
do not establish that vtparse's notice governs x/ansi's origin. No dated primary
snapshot resolving the specification's competing license declarations at
derivation time was found. This specific provenance question remains open.

## Delivery boundary

Read-only inspection establishes the following current configuration:

- `.goreleaser.yml` builds Go `./main.go` for Windows/macOS/Linux × amd64/arm64.
  Its explicit archive file list contains only `README.md` and `LICENSE`.
  It does not list the new notice, `src/LICENSE-Go.txt`, or style evidence.
  `.github/workflows/release.yaml` invokes that Go release configuration.
- `scripts/install.sh` extracts into a temporary directory, installs only
  `pb`, and removes that directory through its trap. `scripts/install.ps1`
  moves only `pb.exe` and removes the temporary directory in `finally`.
  Neither has an explicit notice-retention step.
- The generated Homebrew formula in `scripts/update-homebrew-tap.sh` has
  `bin.install "pb"`; notice retention there is not established.
- `Cargo.toml` has no explicit `include`, `exclude` or `license-file` setting.
  Cargo package contents were not enumerated. The inspected Rust workflow and
  harness build targets do not establish a native Rust release notice bundle.

The verified boundary is repository content only. Archive inclusion and
installed notice retention are not established by this change; the existing
explicit archive and installer configuration does not deliver the new file.
No actual release/crate/installer artifact was produced or inspected, and no
installer or six-platform shipping gate passed. Closing that boundary requires
packaging inclusion and artifact/installation verification, outside this
assignment.

## Validation and ownership

The first round used offline Python byte/hash/archive checks and read-only
Git/file inspection. This follow-up added read-only public primary-source
requests, pinned history reads and static content comparisons. No CLI oracle,
Go/Rust build, timing/performance run, profiler, commonlock, release or publishing
action was performed. Complete license and source-notice sections were checked
byte for byte at their inventory offsets. The original 17 license sections are
preserved, and all previously recorded non-owned repository input hashes remain
unchanged. No Rust, Cargo, harness, packaging script or another owner's doc was
changed, and nothing was staged or committed.

Before any successor write, the original three files were snapshotted under
`/home/ajs/.cache/pb-phase7/attribution/before-provenance-20261014T170022Z/`, with
their hashes in `provenance-before.json`. Raw primary evidence and URL/status/hash
sidecars reside in that attribution cache's `upstream/` directory. The final
`provenance-final-freeze.json` records successor file hashes and the unchanged
non-owned repository source/config/evidence hashes for root integration. The
inventory contains the same evidence metadata and original snapshot hashes;
this doc and notice never embed credentials or private capability URLs.
