# Phase 7 byte integration — independent interim Sol review, round 1

Date: 2026-10-14. Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`.
Scope: finalized byte integration in `src/tui/{style,terminal,sql,promql,saved}.rs`.
This is **not the required final Opus review**, which remains outstanding.

## Verdict and method

**One medium production correctness finding: SQL compares a replacement-decoded
dataset name as identity even when its raw-byte sidecar exists.** No high-severity
defect or new lifetime/ownership defect was identified in the reviewed byte paths.
The sidebar, builder and saved rendering fixes have connected byte paths to terminal
emission; the new centrally supplied release2 controls support those specific flows.
This is not a release or whole-corpus acceptance verdict.

I read the requested review/workflow docs and both evidence summaries, then traced
the changed rendering, selection, effect and terminal ownership boundaries. I also
read the newly supplied reachable C1-ST saved evidence and focused release2 evidence.
Cached pinned Go model source was consulted for dataset equality and builder/saved
clipping; cached reflow source was consulted for its intentional rune conversion.

Evidence labels:

- **[source]**: inspected current Rust/Go source; behavior inferred, not executed.
- **[executed static]**: this reviewer ran Python source guards, hash checks, saved
  evidence inspection and a small explicitly modeled identity counterexample.
- **[central evidence]**: supplied process/test results read by this reviewer; I did
  not run those processes or tests.
- **[unknown]**: no adequate runtime/observer result has established the claim.

I ran no crate or standalone compilation, tests, CLI/PTY invocation, timing campaign,
network request or harness changes. Production was read-only. My only worktree write
is this report. Optional bounded audit files are
`/home/ajs/.cache/pb-phase7/review-byte-sol-r1/{audit.py,audit.json}`. The identity
counterexample there models the inspected branches; it does not execute the reducer.

## F1 — Medium: raw SQL dataset identity aliases a valid replacement-character title

**Files/lines [source]:** `src/tui/sql.rs:2718` (reset matching), `:3185`
(completion matching), `:3236-3239` (picker change detection). The subsequent schema
effect uses the surviving raw sidecar at `:3196`, via `dataset_bytes` at `:2608`.

`split_name` at `:1369` retains malformed bytes in `dataset_raw` but also creates a
replacement-decoded `dataset: String`. The comparison sites above compare only that
String. Two different Go byte strings can therefore become the same Rust identity.

**Concrete trigger:** establish the known extracted dataset `B5 8B E8 AF 95`
(for example, editing `select 'Ⱥ' from 测试` and opening the columns drawer).
Its String companion is literal `\uFFFD\uFFFD试`. Deliver a successful dataset-list
completion with valid titles `["logs", "\uFFFD\uFFFD试"]`.

- Go's `resetInvalidDatasetSelection` compares the original bytes against both
  valid titles. Neither matches; it selects `logs`, index 0
  (cached `pkg/model/query.go:495-507`, pinned `07af390`).
- Rust finds the second title equal to its decoded String, selects index 1 and
  returns without clearing `dataset_raw`. The sidebar still renders the invalid
  extracted name rather than Go's selected `logs`.
- If the columns drawer is open, columns are empty and no schema request is pending,
  `complete_datasets` sends a schema effect for `B5 8B E8 AF 95`, whereas Go sends
  one for `logs`. This state can follow a failed/empty schema completion before a
  subsequent dataset-list completion. The request divergence is inferred from the
  source; no process reproduction was run here.
- Picker Enter also treats the valid replacement-character title as unchanged:
  it can retain the sidecar and existing columns, and skip the schema fetch when
  columns are already populated. Go treats it as a changed dataset.

**Fix:** compare actual identity bytes at all three sites. For example, compare
`candidate.as_bytes()` to
`self.dataset_raw.as_deref().unwrap_or(self.dataset.as_bytes())`. A valid title
cannot equal an invalid raw sidecar. On a changed picker selection, clear the
sidecar and column state exactly as the existing branch does. Preserve raw bytes
for effects; converting them to replacement text for comparison is insufficient.

**Suggested verification for the owner:** a reducer case with the above raw name,
two-title completion and idle/empty columns, asserting selected index 0, cleared
sidecar and schema bytes `logs`; plus picker Enter when a raw name and its valid
replacement title collide and columns are populated. This reviewer did not edit
tests or production. The bounded Python counterexample confirms the unequal byte
identities and opposite branches, not production execution.

## Byte-flow assessment

**Shared style [source].** `render_bytes_in` calls the common backend with
`string_contract=false` (`style.rs:1067-1068`); the width branch uses `wrap_bytes`
(`:1158`). Padding, alignment, borders, margins, max clipping, joins and placement
copy byte payloads and measure with `width_bytes`. Grapheme decoding uses one
RuneError per invalid byte for segmentation/width while retaining original spans
(`:3631-3696`). No placeholder/sentinel or dataset-specific byte mapping was found.

The remaining conversions are specific Go/string contracts, not accidental early
replacement: the string renderer uses the historical lossy **post-wrap** facade
(`:1154-1156`); `ui_truncate_bytes` converts to runes only when Go's clipping branch
does (`:3888-3909`); underline/strikethrough space styling iterates runes
(`:1166-1178`). Ordinary string callers retain their separate L-STYLE-1 boundary.

**SQL display [source].** Raw extraction survives in the sidecar and schema
`Vec<u8>` effects. The sidebar fallback distinguishes valid text from raw bytes
(`sql.rs:3474-3483`). Raw rows use `ui_truncate_bytes`, byte attribute rendering,
byte joins and `pane_body_lines_bytes` (`:3725-3743`), then byte full-frame joins
and final Width rendering (`:3565-3589`). `Frame::Bytes` reaches `draw_bytes` and
`close_bytes` (`:326-337`). The overlay path's `go_string` conversion is confined
to the body hidden by the overlay; it does not replace the visible raw sidebar.
F1 is an identity defect outside this successful rendering chain.

Reflow conversion at `sql.rs:4209` happens only when the pane must clip. Cached
reflow `truncate.go` iterates Go runes and writes each rune's encoding, including
RuneError inside ANSI buffering. Its per-invalid-byte replacement is intentional.
This does not authorize replacement before x/ansi width/wrap or an uncut UI truncate.

**PromQL G9 [source].** The item cut uses byte length and the exact
`[..max_len-3] + "..."` rule (`promql.rs:5057-5063`), then byte column rendering,
joins, overlay placement and final Width rendering (`:5070-5078`, `:5112-5155`,
`:5288-5296`). Both drawing and final close consume bytes (`:5679`, `:5715`,
`:5723`). Full original Strings remain in the filtered lists; current-item lookup
(`:3342-3349`) feeds builder effects and expression construction (`:3833-3927`).
Clipping does not overwrite effect identity.

**Saved [source].** `clip` uses byte length and `[..width-1] + "…"`
(`saved.rs:509-518`); the title, SQL description and metadata rows retain those
bytes through content height, outer Width/border rendering and final concatenation
(`:414-482`). Draw and all graceful final-view paths use byte APIs (`:109`, `:123`,
`:154`). Selection clones the original owned item (`:243-259`), retaining ID, SQL,
dataset and ranges; clipping only creates temporary display output.

**Terminal and ownership [source].** `draw_bytes` copies the borrowed view into an
owned writer slot (`terminal.rs:855-858`); final close owns another copy through
`Stop::Quit` (`:973-1000`). Renderer caches and line slices are byte-based
(`:388-399`, `:439-500`). Invalid lines use byte truncation at `:424`, and width
uses `width_bytes` at `:432`. No borrowed frame escapes into the writer thread.
No lossy conversion occurs in the invalid-byte renderer branch or writer/syscall
handoff. Valid lines retain the previous string truncate branch (`:423`); no new
ordinary UTF-8 regression was identified. All 2,808 saved helper rows matched on
inspection; none of their valid-input truncate results was malformed. That finite
check is not a proof for arbitrary streams.

## Reachable L-STYLE-1 saved-title disposition

The new actual Go input is `x\x1b]0;t✅y z`, 12 valid UTF-8 bytes at 40×20. It fits
the saved clip width 30. Thus this is a **reachable positive-width rendering
transform without clipping**, not an unreachable malformed-input helper case.

**[central evidence]** `style-c1-st-saved-process-go.json` records emitted title
bytes `78 1B 5D 30 3B 74 E2 9C C2 85 79 20 7A`: OSC consumes raw `9C` as ST inside
the checkmark, then Wrap re-encodes raw `85` as U+0085. The original valid checkmark
encoding is absent. `style-c1-st-saved-process-release2.json` records the same exact
title-byte context, counts partial-E29C=1 / valid-checkmark=0 / C285=1, process
success and exit 0. This reviewer compared the stored contexts/counts independently.

**[source]** Saved title styling and content-height rendering preserve the valid
input until the outer Width render (`saved.rs:477-482`). That call is
`render_bytes`, so it takes `wrap_bytes` rather than the string facade and retains
the newly malformed output. Its invalid title line then takes terminal byte
truncation. The centrally executed control supports this complete reachable path;
the historical lossy string renderer does not apply to this saved-title flow.

**[unknown]** Both observers reject the remaining unterminated OSC as
`incomplete terminal sequence`. The visible `x`/cursor prefix is partial, not final
frame acceptance. Full real-terminal cells and string-facade styled equivalence
remain unknown. Do not normalize the partial bytes or treat the transform as
unreachable. The byte-emission disposition is supported for this saved flow;
ordinary string callers retain a separately scoped limitation.

## Evidence bounds and remaining work

**[executed static]** The five source hashes match `source_after` in
`source-byte-integration-checkpoint.json`; style matches its final differential
hash. All 10 listed style evidence hashes match. I parsed the stored 2,808 rows,
rechecked all 36,504 Go/Rust output comparisons and found zero unequal rows.
The 22 standalone tests and 428×35 prior-string comparison are recorded evidence;
I did not rerun them. The central checkpoint reports 424 library passes, clean
Clippy, 426 diagnostic passes/1 ignored and saved reread success. Its all-target
run exits 101 with the owner-reported quit-before-schema fixture race; full
all-target acceptance is not established by that checkpoint.

**[central evidence, received during review]**
`release2-focused-styled.json` reports all three strict sidebar/builder/saved
controls passing, with zero cell differences and equal cursors for actual-read
and one-chunk replays. Request/config/restoration success was relayed by the
coordinator. The documented normalization is only exact fixture-URL port digits;
it is not byte/C1 normalization. These are three bounded process controls, not
whole-corpus, arbitrary-read-split, profile/locale or cross-platform parity.
The additional reachable saved C1-ST result has the observer limitation above.

Documentation should be reconciled after the source disposition: `phase7-sql.md:17`
still declares `view -> String`, `:251` still describes a proposed unimplemented
sentinel fix, and `phase7-admin-saved.md:35` still describes replacement decoding
for clipping. Those passages are superseded by the reviewed byte implementation
and the newer effects evidence; no sentinel proposal was implemented here.

Remaining work: resolve F1 and validate that identity boundary, finish the separately
owned all-target/process/corpus gates, preserve the incomplete-OSC observation limit,
and obtain the **required final Opus review**. This interim review neither replaces
that review nor claims unrelated earlier phase-wide findings are closed.

## Reviewed source fingerprints

| File | SHA-256 |
|---|---|
| `src/tui/style.rs` | `f4bf856201fde8fb1883707d59f3481ca143e1297609c5c92a706dc583d083e5` |
| `src/tui/terminal.rs` | `ae3ecf7933b5e2a99a730ee602e8402d205d81c0cdfc09a46b48a2bc895e17bd` |
| `src/tui/sql.rs` | `59b97435e2a54ab79a5bb7f8cd1c63203cbc9bffb98a362b2a1523735e5dedc5` |
| `src/tui/promql.rs` | `51387004ec5207391d8c60252d1eb62997ae200d8ec23a89237238a1f114cf55` |
| `src/tui/saved.rs` | `216c47f6819b39a1b397d569ad1bc01bf4eb0b56a2f76a5682645cb11f3d8531` |
