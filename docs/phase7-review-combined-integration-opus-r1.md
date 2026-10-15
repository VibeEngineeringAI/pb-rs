# Phase 6/7 combined integration review (Opus, round 1)

Scope: the integration boundaries of the uncommitted Phase 7 overlay on merged
`main` `ea70f7ad7d501d59b26cf6397f6a0b1f28143fc4`. I did not re-review the TUI.
I compared the current tree against `HEAD` and Phase 6 (`4a812d5..ea70f7a`) and
read the cache in `~/.cache/pb-phase7/phase6-merged-integration/`.

This was a source-only review. I ran no cargo, compiler, build, CLI, probe or
timing commands. The pure checks were `git diff`/`git show`, SHA-256 hashing,
and Python `tomllib` lockfile parsing with `-I`. I relied on the root's
fmt/clippy/metadata logs and Python test logs without rerunning them.

## Verdict

One concrete objection, at low-to-medium severity: a Phase 6 harness guard
became vacuous after the merge. I found no Rust source regression at the
Phase 6/Phase 7 boundary.

## Objection O1: Phase 6 boundary guard loop now iterates an empty list

`harness/test_phase4.py:385-400` contains
`HeaderDeadlineNormalizationTests.test_deferred_stub_guard_remains_active`,
which was added by Phase 6 (`git diff 4a812d5 ea70f7a -- harness/test_phase4.py`).
Its second half is:

```python
for check in phase4.deferred_boundaries():
    ...
    self.assertEqual(phase4.p3.boundary_issues(check, boundary), [])
    ...
    self.assertTrue(any('network' in i for i in phase4.p3.boundary_issues(check, boundary)))
```

Phase 7 changes `phase4.deferred_boundaries()` to `return []`
(`harness/phase4.py:350-356`), and its own new test asserts this at
`harness/test_phase4.py:13`. After the merge the loop body never runs. The
test still reports `ok` (`test_phase4.py.log:15`), but the boundary-network
assertions Phase 6 intended no longer execute. Only the first assertion, the
`p3.differences` stub detection, is still live.

Phase 7 already redirected the equivalent tests in `test_phase2.py`,
`test_phase3.py` and `test_phase5.py` to `historical_deferred_boundaries()`.
This test arrived from Phase 6, so the conflict-free merge did not adapt it.

Minimal fix, in the same style as the sibling suites. Iterate
`phase4.historical_deferred_boundaries()`, and assert the list is non-empty,
so the network/stub assertions keep exercising `p3.boundary_issues` on the
retained definitions. Phase 7's `deferred_boundaries() == []` assertion stays
as it is. No production code changes.

## Checked and found consistent

- **FD limit / subprocess opener (`docs/rust-phase5.md:154-169`).** HEAD's
  `main.rs::raise_open_file_limit` has moved to
  `process::initialize_open_file_limit`, and its arithmetic is unchanged:
  `rlim_max == 0` and `soft >= hard-1` return early, the Apple
  `kern.maxfilesperproc` clamp is kept, the limit is never lowered, and errors
  are ignored. `main` still calls it first, before the theme, diagnostics or
  any thread. The new `restore_child_limits` covers the rust-phase5 requirement
  that "any future subprocess... must restore the limit itself". Every child
  spawn site calls it: `tui/device.rs:422` for the browser opener and
  `tui/clipboard.rs:136` through `as_std_mut()`. In-process library callers
  (`pb::run` from tests) never initialize, so they restore nothing, as the doc
  comment states. Phase 6 `src/tail` spawns no processes.
- **Tail dispatch and preflight (`src/lib.rs`).** `pb tail` still dispatches
  right after the profile preflight and `set-role` check. That is ahead of the
  Phase 7 `pb sql list` menu interception, and `pb login` is routed only inside
  `execute_operational`, so tail cannot reach either. The preflight now keeps
  `(ProfileStore, Profile)` alive for the call. `ProfileStore` is only a
  `PathBuf` (`config.rs:156`) with no `Drop`, lock or FD, so keeping it alive
  for an unbounded tail session has no side effect. Tail still re-reads
  `ProfileStore::from_env()` itself. `spec.json` has `pb sql list`
  `requires_profile: true`, so the `expect` at `lib.rs:289` cannot fire after a
  successful preflight.
- **Signals.** Tail documents default dispositions (`tail/mod.rs:16-17`). The
  TUI's SIGINT/SIGTERM/SIGWINCH handlers are installed and restored only within
  TUI commands (`tui/terminal.rs:1064-1118, 1306-1332`), and main's lazy SIGPIPE
  restore is unchanged. Exec resets handlers and Rust's spawn resets SIGPIPE, so
  children are unaffected. A single invocation never runs both tail and the TUI.
- **Runtime.** Each command builds its own current-thread runtime. The only
  Cargo change is the added tokio features `process` and `macros`, which do not
  change Phase 6's runtime construction. The Windows-only `crossterm` dependency
  is target-gated.
- **Cargo.toml / Cargo.lock.** All 11 Phase 6 dependency declarations are kept.
  Parsing the lock found 252 packages, with no duplicate `(name, version)`. The
  lock is exactly the union of HEAD (230) and the pre-merge Phase 7 lock (186),
  with no extra packages and no checksum differences. Every dependency
  reference resolves unambiguously. The one non-union dependency list,
  `getrandom 0.4.3 -> r-efi`, is just Cargo's required disambiguation to
  `r-efi 6.0.0`, because HEAD also carries `r-efi 5.3.0`. No package was
  upgraded.
- **Harness guards.** `PHASE6_OPERATIONS` and `PHASE7_OPERATIONS` are a union
  in `phase1.py:431-448`. Both Phase 6's `pb tail` exclusion assertion and
  Phase 7's promotion assertion are present in `test_phase1.py`, and the
  `login` guard assertion is correctly removed. Phase 6's new `phase3.py`
  deadline normalizations (`normalize_timeout`) do not touch
  `deferred_boundaries`. Removing the extra blank line in `test_phase3.py` is
  whitespace only.
- **Overlay integrity.** All 26 records in `applied.json` match the live files
  by SHA-256, and `docs/evidence/phase7/phase6-merged-integration.json`
  `records` equals `applied.json`. The only `src/`/`tests/` path touched by both
  Phase 6 and Phase 7 is `src/lib.rs`.

## Non-blocking notes (no action requested for integration)

- `harness/phase4.py:438-442` compare does not set `boundaries_status`, while
  phase2, phase3 and phase5 report `promoted to phase7 PTY/effect gate`. This is
  a pre-existing Phase 7 inconsistency, not caused by the merge, and only
  affects report labeling.
- `docs/rust-phase5.md:164-165` still says that Rust `pb` "starts no child
  processes". That is historically accurate for Phase 5, but Phase 7's
  `process.rs` now fulfills the follow-up requirement in that same paragraph.

## Limits

These are source-level findings only. The fresh combined release build and
retained gates (Rust tests, `tests/fd_limit.rs`, `interactive_effects`, Phase 6
Flight and Phase 7 PTY gates) were not run or observed by this review.
Historical Release5 and R6 artifacts do not certify the merged source.
