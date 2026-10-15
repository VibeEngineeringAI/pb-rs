# Opus final R3 O3-1/O3-2 implementation handoff

Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` only.
Base: `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`.
Go source pin: `07af390ab09bda8ac9e427ffc6310077bace0125`.

Owned source is **frozen for parent central successor build/validation**.
No Rust compilation, crate tests, candidate rebuild, benchmark or profiler was
run. No shared/main/lib/Cargo/auth/editor/helper/bench/profile-harness or SQL
clock-lane file was edited. Cancellation quirks, plain Enter's sticky latch,
byte scoring, source tie order and filter interception remain preserved.

## Exact production files and frozen SHA-256

- `src/tui/profile.rs`:
  `a822ba963ba4ab4bc19c0dd403a35d90d44ab129fe6a994ea50e6d3f3b273730`.
  Credential non-intercepted KeyType Enter reaches the button gate, then checks
  current validity. One parsed ESC CR reducer regression covers valid focused,
  valid unfocused, invalid latch and cleared-latch/current-invalid controls.
- `src/tui/saved.rs`:
  `24d37551aa9a9498f4a67a6cb99ff5c308377ed54ab5bc1f6fe2139a25d68935`.
  Uses saved-only canonical-fold and Ll/Lu helpers. Two reducer regressions add
  orbit membership/application, exact actual-Go scores and category ranking.
- `src/tui/saved_fuzzy_unicode.rs` (new, included directly from saved):
  `3a88503ae1955216f7f2da881d3a1d41d7e112dcd9fa18e0aeb8163155e2e634`.
  Native binary searches, ASCII fast paths, 1,428 non-ASCII fold entries and
  163/149 category ranges. No product runtime Go call or new dependency.

The only workflow docs changed are append-only resolutions in
`docs/phase7-auth.md` and `docs/phase7-admin-saved.md`. Their exact hashes and
every uniquely named `docs/evidence/phase7/opus-r3-low-*` artifact hash are in
`opus-r3-low-freeze.json`. Before hashes, pinned source equality and immutable
binary hashes are in `opus-r3-low-before.json`; byte copies of the four existing
owned files are under `/home/ajs/.cache/pb-phase7/opus-r3-low/*.before`.

## Executed evidence

- Actual pinned-Go normal binary SHA-256:
  `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`.
  **21/21** real PTY/process controls pass (7 credential, 14 saved).
- Actual immutable release3 normal binary SHA-256:
  `aceb58bd412e8fbf34f603d247c68e87e69897356cefd5aad19ab6a92484db28`.
  **15/21** pass. Exactly six pre-fix failures: focused valid Alt+Enter does not
  exit; both new orbit filters select nothing and issue no SQL POST; each of the
  three category controls selects logs_0 instead of Go's logs_1. These processes
  still restore termios and retain the documented cancellation/config behavior.
- Frames explicitly establish button focus via accent color, latch via label,
  and current validity via username/password contents. Unfocused/invalid paths
  have subsequent navigation frames proving continued input processing. The
  release3 valid-submit mismatch is recorded before cleanup Ctrl+C, which saves
  the valid values; that cancellation is not labeled successful submission.
- Saved selection is verified through distinct SQL per title, Query stdout,
  actual ordered HTTP requests, LIMIT 500 and 1h/now ranges. All saved controls
  restore termios and leave the disposable config unchanged. Go async filtering
  uses separated input and a bounded settling interval; no arbitrary scheduler
  or read-split equivalence claim is made.
- Offline Go 1.25.4 / Unicode 15.0.0 oracle plus Python interpretation of generated
  data compares **1,112,064** scalars for canonical fold and Ll/Lu, and verifies
  fold idempotence. Actual sahilm/fuzzy v0.1.1 and bubbles v0.20.0 calls establish
  scores/rankings. Byte-identical regeneration passes. This verifies the data
  and lookup algorithm interpretation; it does **not** execute the Rust helper.
- Rustfmt parsing/format checks on the three owned Rust files pass. Python
  runner/generator syntax checks pass. No new Rust unit test has been executed.

Combined full process records are `opus-r3-low-go-process.json` and
`opus-r3-low-release3-process.json`; per-process raw stdout/stderr, hashes and
JSON are retained in `/home/ajs/.cache/pb-phase7/opus-r3-low/{go,release3}/`.
The fixture's embedded-source SHA-256 is
`a0dccd5b91416ec05268d61a9fe9080c50bc953f33781bf4cd8c384954093ae3`;
the durable runner rejects a changed fixture until coordinated. No fixture edit
was needed. The generator guards actual module-cache/unicode source hashes.

## Parent successor commands and remaining unknowns

After the parent's central build, replay all controls on its immutable normal
binary, supplying its actual SHA-256 (do not substitute release3 results):

```sh
TMPDIR=/home/ajs/.cache/pb-phase7/tmp \
  /home/ajs/.cache/pb-phase7/harness-venv/bin/python \
  docs/evidence/phase7/opus-r3-low-process.py \
  --binary /ABSOLUTE/PARENT/SUCCESSOR/pb-normal --role candidate \
  --expect-sha256 SUCCESSOR_SHA256 \
  --out /home/ajs/.cache/pb-phase7/opus-r3-low/successor
```

Data reproduction check (no Rust build):

```sh
TMPDIR=/home/ajs/.cache/pb-phase7/tmp \
  /home/ajs/.cache/pb-phase7/harness-venv/bin/python \
  docs/evidence/phase7/opus-r3-low-unicode-generate.py
```

New central unit-test names:

- `credential_alt_enter_obeys_button_focus_latch_and_current_validity`
- `fuzzy_matches_pinned_go_simple_fold_orbits_without_full_case_folding`
- `fuzzy_camel_bonus_uses_pinned_ll_lu_categories_and_go_byte_scores`

Unknown until parent validation: integrated compilation and unit-test success,
successor PTY/application parity and broader shared phase gates. Full styled
frame equivalence, scheduler permutations, arbitrary read splits and non-Linux
platforms are not established by these controls. The constructed valid-values /
still-invalid-latch reducer subcase is a gate-order control, not an actual-Go
PTY reachability claim. No unrelated Go race or fault was silently corrected.
