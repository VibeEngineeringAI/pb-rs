# PR #5: test-first review fixes

The [review](https://github.com/VibeEngineeringAI/pb-rs/pull/5#pullrequestreview-5538348038)
of `0ab778525ecebe53cc19f4e3e27f32119290ab25` identified five confirmed defects.
The 2026-10-15 follow-up fixes all five and an additional SQL-header Unicode
mismatch found during independent review. Three Sol 6.1 implementation/validation
agents and eight Opus 5.5 oracle/review tasks contributed to this follow-up.

## Changes and test-first evidence

| Defect | Correction | Regression evidence |
| --- | --- | --- |
| SQL/PromQL Unicode filters | Pin Go 1.25.4 / Unicode 15 simple lowercase, including dotted I, non-contextual sigma and newer Unicode boundaries | Four filter regressions failed before the fix; selector and row tests now pass |
| PromQL horizontal scrolling | Right/d and Left/a move columns with Go's bounds, widths, overflow marker and filter precedence; offset survives resize/results | Original 40-column exponent test failed before the fix; key/render/persistence tests pass |
| Overwritten nested JSON overflow | Validate every interface value in wire order before sorted, last-value-wins formatting; use a depth-safe heap traversal | Three failures before the fix, then all 21 decoder tests pass; valid duplicate control retained |
| Missing input-mode validation/comparison | Capture initial/final tracked modes, enforce cleanup and compare intentional non-restoration; reconstruct legacy evidence from original raw input | Live mode-leak captures fail validation/comparison; SIGQUIT and OSC controls pass |
| Missed history eviction / false capacity overflow | Detect actual eviction at pyte's index/reverse-index append points; preserve sticky loss evidence | Final-token lost prefixes are rejected; exactly full history plus non-scrolling controls passes |
| SQL header uppercase mismatch | Pin Go's simple uppercase table; preserve original record keys and uppercase only display titles | Real result/header regression failed before the fix; Unicode and ASCII/expansion controls now pass |

The harness has **18 new regression tests** with retained RED→GREEN records,
including follow-ups found during integration: supported full reset clears tracked
modes, mode-only reconstruction needs no geometry, missing raw evidence is a
per-session error, and ambiguous legacy history cannot silently pass. Captured
case evidence is reconstructed before any replacement contract is applied.
Original files and failure strings are never rewritten.

[Evidence manifest](evidence/phase7/pr5-review-fixes/tdd-evidence.json) binds the
original failing logs, focused passing logs, final results and oracle sources.
The [lowercase dump](evidence/phase7/pr5-review-fixes/go-lower-map.txt) and
[uppercase dump](evidence/phase7/pr5-review-fixes/go-upper-map.txt) each match
independent Go/Rust checks over all **1,112,064 Unicode scalars**. There are 1,433
lowercase and 1,450 uppercase mappings. Small Go oracle sources are retained as
`.go.txt` to keep evidence out of ordinary Go package discovery; the corresponding
Python generators reproduce the Rust constants. Existing full Go and Unicode
notices cover the generated tables, with updated source mappings.

## Validation

| Check | Result |
| --- | --- |
| `cargo fmt --check` | Pass |
| `cargo clippy --locked --all-targets -- -D warnings` | Pass |
| `cargo test --locked --all-targets` | **697 passed, nine existing opt-ins ignored** |
| Python harness, 32 isolated modules | **950 passed, two existing opt-ins skipped** |
| Final Phase7 observer module | 95 run: 94 passed, one opt-in skipped |
| Benchmark-consumer unit module | 131/131 passed; three affected consumers rechecked after bounded-parser follow-up |
| Actual pinned-Go/final-release PTY probes | **14/14 passed**: seven scenarios for each binary |
| Exact scroll replay from final process captures | **3,600 styled cells equal** across baseline, Right, d, a, Left |
| Independent PromQL table render matrix | **2,016 plain/ANSI cases equal** |
| Decoder oracle matrices | **63/63 and 21/21 equal** to pinned Go, including depth and skipped-value boundaries |
| Representative legacy captures | Eight SQL/PromQL/resize/SIGQUIT captures still pass; final four-case reduction reports history available 8 / unavailable 0 / errors 0; all 24 saved files unchanged |

Python results compose the full isolated-module run with reruns of affected or
failed modules, recorded in [the validation ledger](evidence/phase7/pr5-review-fixes/python-validation.json).
Original failures are retained: an unconfigured Go shim was replaced with the
explicit pinned Go executable for the diagnostics rerun; a reviewer runner's
custom cache path was corrected for the canonical-cache redaction test. The
legacy geometry regression was fixed in production, and its consumer suite was
rerun successfully. No test assertions were waived.

The actual PTY probes check Unicode dataset/metric selection, exact requests,
nested overflow/error ordering, horizontal scrolling and SQL header casing.
They also require normal exit, empty stderr, full termios restoration and
unchanged config. Header and original-key result rows match Go exactly.

Final normal release SHA-256:
`cb6112a691bd0a6da4ae521961b9fd29cc7d0c01ae15f14ea0ca7d0968576494`.
[Build/source manifest](evidence/phase7/pr5-review-fixes/candidate-final-manifest.json),
[process results](evidence/phase7/pr5-review-fixes/process-final-summary.json),
[styled scroll results](evidence/phase7/pr5-review-fixes/exact-scroll-final.json).
Earlier candidates and captures remain preserved outside the repository under
`/home/ajs/.local/share/pb-pr5-fixes/`.

## Independent review and limits

Opus approved the decoder, original UI corrections and additional uppercase
correction. The decoder review found one test gap: reversing array traversal
survived the original tests. An explicit `[1e401,1e400]` case now kills that
mutation; production logic did not change. The first harness reviewer also approves
its final stable snapshot: 251 observer/consumer tests pass (one opt-in skipped),
659 historical sessions reconstruct without new failures, and randomized chunk
checks match. The second harness review also approves the stated scope, but could not execute
commands because its provider temp directory exceeded quota. Its bounded-parser
and scroll-margin test recommendations are addressed: seed/stdout are fed in
64 KiB chunks, and both dropped-boundary and ignored-margin mutations are killed.
The large-stream regression failed at 464,910 pending bytes before the correction.
The [final independent Sol review](evidence/phase7/pr5-review-fixes/harness-final-sol-review.md)
approves the narrow delta: 25 boundary/immutability cases and 11 focused tests pass.
The second Opus review's stale-doc and missing-historical-check concerns are
superseded by the later evidence above.

Legacy history is accepted as loss-free only when retained frames are below
capacity and parsed controls cannot have cleared or hidden prior loss. Otherwise
validation reports unavailable evidence. It does not invent geometry or replace
historical evidence with a passing result.

The mode contract covers acquisition/exit state for the documented tracked input
modes. Default-on autowrap, global cursor visibility across alternate screens,
application keypad, UTF-8 C1 CSI accounting and checkpoint mode parity remain
separate observer limits. The Go/Rust TUIs emit ESC-prefixed CSI.
Reverse-index bottom-history eviction is conservatively rejected even though
that deque is not exported in frames. See [the contract](../harness/contracts/phase7.md).

A related device-profile casing concern was checked: the divergent fallback
helper is unreachable through successful public login because a nonblank name
is required. It was not counted as an actionable public-path defect.

The separate macOS `/dev/tty` cancellation concern remains unverified. Broader
release7 replay, platform and performance gates remain open. This correctness
follow-up makes no performance or whole-phase acceptance claim.
