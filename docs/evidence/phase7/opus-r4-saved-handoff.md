# O4-1 characterization and off-tree implementation handoff

**No production or existing documentation edit has been applied.** The exact
message `saved matcher edit window open` is required before application, per the
parent's instruction. This is implementation/characterization follow-up, not a
new production review. All release4 source-map identities remain frozen.

Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` only.
Base: `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`.
Pin: `07af390ab09bda8ac9e427ffc6310077bace0125`.
Current saved.rs SHA-256 remains
`24d37551aa9a9498f4a67a6cb99ff5c308377ed54ab5bc1f6fe2139a25d68935`.
Current admin-saved doc SHA-256 remains
`6f684ac69bc2c972a4e7d7a67bf7d7fd6ebf89591bb4caf3ca53cbdc3596a84a`.

## Observed findings, and corrections to the review's claims

The substantive NUL/stale/overflow finding is **confirmed by execution** and
predates O3-2. Two details in the source-only review need correction:

- Typing `/a` cannot insert a into the filter: the saved command intercepts a
  and applies the selected item. Both actual binaries prove this, including a
  NUL-bearing title. Controlled Ctrl+V does insert exact a/ab/long patterns.
  Uppercase A is another reachable crash trigger because folding matches a
  while the saved shortcut intercepts only lowercase a.
- The first negative/rejected contiguous candidate is the **43rd** rune (byte
  42), not the 42nd. The adjacency accumulator first wraps at the **40th** rune,
  whose candidate score is still positive. Final totals can be negative before
  the first rejected candidate, and those matches remain valid.
- The deterministic panic stdout is not guaranteed by Go command recovery.
  Run can return its killed error before recoverFromPanic prints. In the primary
  suite, two of four crashes have the exact PTY CRLF diagnostic suffix and two
  have no Caught-panic prefix. All four exit 1, print the exact killed error on
  stderr, restore termios, preserve config and send no POST. Strict stdout
  checks remain strict; absent output is retained as a failure, not waived.

## Actual process characterization

Go normal binary SHA-256:
`fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`.
Immutable release4 normal SHA-256:
`fd1f91b608bbf76832fab183f02d9c63e658f7bc12ca4aabf57a811559a871c1`.

There are 16 primary controls per binary. Each uses the read-only embedded
PROCESS_FIXTURE and a disposable config; all 32 restore termios and preserve
config. Ctrl+V controls invoke a dedicated gated xclip fixture through an
isolated PATH; no desktop clipboard is read or written. Frames/markers capture
the empty filter before release, helper completion, filtered state and selected
SQL. Initial GET, SQL POST bodies, printed Query, LIMIT and ranges are recorded.

Go strict checks pass **14/16**. The two failures concern the absent command
panic report, not match membership, ranking, exit, request or restoration.
Release4 passes **7/16**, with **nine actual behavioral mismatches**:

- Three pasted crashes (`a`/`a\0b`, `a`/`a\0`, `ab`/`ab\0x`) and typed uppercase
  A/`a\0b`: Go exits 1 with no POST; release4 instead exits 0 after applying SQL.
- Stale memberships `ab`/`a\0x` and `abc`/`a\0x\0y`: Go retains the item and
  executes logs_1; release4 filters everything out and issues no POST.
- At 43 runes, second ranked item: Go executes logs_2, release4 logs_3.
- At 44 runes, first ranked item: Go executes logs_1, release4 logs_2.
- At 45 runes, second ranked item: Go executes logs_2, release4 logs_3.

Controls that agree: typed a interception, ordinary no-match ab/ax, NUL before
the first match a/`x\0a`, and chosen rank controls at lengths 40, 42, 43 (first
item only) and 64. Agreement on the selected item at 64 does not establish
score equality; the actual helper oracle establishes exact score values.

Combined full records are `opus-r4-saved-{go,release4}-process.json`. All primary
raw outputs/per-case JSON remain under
`/home/ajs/.cache/pb-phase7/opus-r4-saved/{go,release4}/`. Initial diagnostic
assertion-development pilots are preserved, including the first LF-versus-PTY
CRLF assertion failure despite an exact raw diagnostic. Additional bounded
followups (including a documented GOMAXPROCS=1 pair) still observe absent
reports; see `opus-r4-saved-go-diagnostic-observations.json`. No loop attempts
to force a universally stable Go report.

## Exact helper oracle and proposed semantics

The unmodified actual sahilm/fuzzy v0.1.1 and bubbles v0.20.0 oracle has 106
controls: NUL/fault/membership cases, ASCII/Unicode controls, all contiguous
lengths 1-64 and complete rankings for four title variants at lengths 35-64.
An observational scratch module copy records candidate and apply steps. It has
the same returned scores, matched byte indexes, ranks and panic values as the
unmodified source. The module cache is not edited. Go rejected an initial
overlay attempt under GOMODCACHE; the corrected runner uses only a temporary
local module replacement, with the original source hash guarded.

Exact contiguous scores:

- 40: `-8315356191162110941`
- 41: `-6499324499776781222`
- 42: `-1051229425620792065`
- 43: `-1051229425620792066`
- 44: `-1051229425620792066`
- 45: `-1051229425620792067`
- 64: `8492147925252739361`

At the 43rd candidate, actual score is `-2102458851241584145`, best starts at
`-1`, and retained byte is 41. The candidate is rejected, but apply still uses
byte 41 and score -1. The indexes for 43 runes are `[0..41, 41]`. The 44th
candidate is score 0 because adjacency no longer holds; later rejection and
retention patterns are recorded exactly through 64. Go's two stable sorting
passes yield ranks `[1,2,0,3]` at 43/44/45/64 for the four tested title variants.
NUL stale scores/indexes are `8 / [0,0]` and `6 / [0,0,0]` respectively.

`opus-r4-saved-go-oracle.json` contains all exact results and a full length-64
trace. The complete instrumentation trace is retained in the dedicated cache,
with its SHA-256 in that record. `opus-r4-saved-model-check.json` records a
Python interpretation of the proposed native state matching all 106 controls
for faults, exact scores, byte positions and stable ranks. **This is not Rust
execution.**

## Proposed exact delta, still off-tree

Prepared files under `/home/ajs/.cache/pb-phase7/opus-r4-saved/`:

- `src-tui-saved.proposed.rs`, SHA-256
  `d25a3358cb60a78977d0ee9bddc239c7b83246fa8cf66d46afccf288ff032bf7`.
- `opus-r4-saved-source.patch`, SHA-256
  `49f21a66df5ef0fd11aa6afb81484c35a823fe4f037b641f102c39368e94bf31`.
- `docs-phase7-admin-saved.append.md` and complete prospective doc.
- `opus-r4-saved.patch`, combining only saved.rs and the append-only doc delta.
  Its exact SHA-256 and all other proposed/evidence hashes are in
  `opus-r4-saved-prepared-freeze.json`.

The native implementation uses a small MatchProgress state, Option byte
positions, iterator lookahead and Result<Option<i64>, MatchFault>. It preserves
only the executed Go candidate floor, retained byte, NUL boundary, exhaustion
fault and wrapping score semantics. It is not a literal byte-loop translation.
Refilter collects scores with `?`; a typed MatchFault becomes a distinct effect.
The only cleanup extraction moves the existing Event::Panicked body into a
local command_panic helper used by reader panics and matcher faults. It cancels
and joins clipboard tasks, drops the terminal with no final view, prints the
existing deterministic report and returns the existing killed error. Editor
fault behavior, key priority, clipboard lifecycle, filtering scheduling,
stable ties, Unicode lookup, rendering and application remain unchanged.

Three prospective reducer regressions use actual-Go expected values:

- `fuzzy_nul_fault_is_a_command_fault_and_typed_a_still_applies`
- `fuzzy_nul_reuses_the_retained_byte_and_preserves_application`
- `fuzzy_overflow_uses_the_verified_candidate_floor_and_go_rankings`

Existing score assertions are adapted to Result without weakening their Some
expectation. Rustfmt parsing/format checks pass on the proposed file. No crate
build, Rust test, candidate rebuild, helper-unicode/profile/shared-harness/test
edit, benchmark, profiler or common lock was performed. Only new uniquely named
evidence files were written in the worktree. Production/existing docs remain
unchanged; old evidence and release4 artifacts remain intact.

## Remaining validation and unknowns

After the exact edit-window message, apply the prepared delta and bind the new
source hash. Parent owns compilation and all Rust/central/successor validation.
The durable process runner accepts an explicitly hashed candidate:

```sh
TMPDIR=/home/ajs/.cache/pb-phase7/tmp \
  /home/ajs/.cache/pb-phase7/harness-venv/bin/python \
  docs/evidence/phase7/opus-r4-saved-process.py \
  --binary /ABSOLUTE/SUCCESSOR/pb-normal --role candidate \
  --expect-sha256 SUCCESSOR_SHA256 \
  --out /home/ajs/.cache/pb-phase7/opus-r4-saved/successor
```

Unknown: actual native compilation/test success and successor process outcomes;
emitted Go length-2 panic stdout (absent in all retained attempts, although the
actual helper's panic value is exact); arbitrary scheduler/read splits,
full styled-frame parity and non-Linux behavior. Synchronous native faults are
not a new scheduler or suppression policy. The current source-only proposal
does not inherit the parent's release4 validation or its retained phase runs.
