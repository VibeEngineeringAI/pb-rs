# Phase 7 review: combined dark spinner opt-in (Opus r1)

Verdict: **accept scope**. No blocking concerns.

Reviewed off-tree proposal `~/.cache/pb-phase7/combined-dark-spinner-proposal`
(`proposal.patch` SHA256 `6d86c891b49546810a6973d569ec0b1121b8d08ec145aa8cc00597b087e145b8`,
verified). Review scratch and log: `~/.cache/pb-phase7/review-dark-spinner-combined-opus-r1/{check.py,check.log}`.
Only artifact reads and in-memory `phase7.compare` calls ran; no native, Go, Rust,
helper, probe, build or performance process was started.

## Scope

- The patch changes one line in `harness/phase7_workloads.py` (line 482). Loading
  the proposed file next to the current one gives the same 190 case names. Exactly one case
  digest changes, `p7-promql-wide-legend-view-panic-dark`. That case equals the current
  case plus `loading_spinner_checkpoints=['before-view-panic']` and nothing else.
- The light opt-in stays in place and is unchanged. `harness/phase7.py` (comparator) is
  untouched (`dc7574f0…`). The current workloads source matches the manifest (`54a9efb7…`).
- `reduce_report` replaces the captured case with the active case. The opt-in therefore
  takes effect in reduction, and the reduction marks this entry `contract_changed`, so the
  change shows up in the report.

## Identity and lifecycle

- The case recorded in all four retained sessions (release5 and release6 reference and
  candidate) has the same digest (`585caf2a…`). It equals the current active dark case
  and has no opt-in.
- Both Go references use binary `fae8411febf8603e…`, the pinned `07af390`. Both reports
  record an empty production Go diff (`e3b0c442…`). The release3 dark and light A/A
  sessions use the same binary.
- Every session exits 0, restores termios, leaves config unchanged and has the single
  `before-view-panic` checkpoint. Each session has exactly one recognized spinner cell.
  The ordered request contracts are equal for Go5 vs Go6 and for Go6 vs Rust6.
- All 16 manifest input hashes still match. Original reports and raw files are untouched.

## Differential

- Go6 vs Rust6 raw frames differ in 6 cells: row 34, cols 30–34 are fixture port digits,
  and row 21, col 39 is the spinner. Existing origin normalization removes the port
  digits. What remains is one cell, `\` vs `-`, with the same fg `9e9ef0`, bg default and
  attributes. Cursor, scrollback, dimensions and alternate state are equal.
- Go5 vs Go6 (actual independent pinned Go) fails only at that same cell (`-` vs `\`).
  This shows the variation is Go A/A and not a Rust divergence.
- With the proposal applied, all five pairings compare clean: Go5/Go6, Go6/Rust6,
  Go5/Rust6, Go5/Rust5 and Go6/Rust5. Without it, release6 fails exactly as reported.
- This is consistent with release3. Light showed the variation there, while dark sessions
  happened to share phase `-`. Raw chunk replay showed all four phases rotating in both
  themes. Dark variation is now actually demonstrated, so it meets the same evidence bar
  that admitted light.

## Tests

- The five tests in `test_proposal.py` and `tests.log` exercise the real comparator on the
  real sessions. Each negative control is checked through the real `compare` path:
  - fg, bg, attribute and cursor faults
  - scrollback, position (glyph moved) and label faults
  - final-frame, exit, restore, panic-diagnostic and failed-view faults
  - an unopted checkpoint name
- `validate` still requires exactly one active spinner in each session, so a missing or
  duplicated spinner fails.

## Non-blocking notes

1. The tests change `result['case']` in memory rather than loading the proposed workloads
   module. My `check.py` closes that gap by loading `proposed-workloads.py` directly.
2. The opt-in normalizes any glyph in `| / - \` at a spinner position (for example, a
   candidate stuck on one phase would also pass). This is inherent to the existing,
   already accepted light rule, and the scope is still one cell at one named checkpoint.
3. After promotion, the gate result for this case rests on the reduction, not on the
   immutable original report (159/160 gates). Promotion evidence should cite both.

Unrelated: `p7-remove-replacement-cancel-light` is documentary
(filter-draft/final-frame map order). It stays outside the gates; release6 has
160 gates, 159 passing.

Process state: this review started no processes and leaves none running. The running
processes are other sessions' retained, Flight and server runs. They were not touched.
