# PR5 harness fixes: independent read-only review

**Verdict: scoped approval. I found no confirmed actionable issues in the final reviewed state.**

## Reviewed state (pinned)

The harness was **not frozen** while I reviewed it. It changed at 14:24:44, about 14:32, and 14:35–14:38. After that it held still from 14:38 to at least 14:44:54. Every conclusion below is for this content:

| file | sha256 |
|---|---|
| harness/phase7.py | `df769c7d8c2689d54bc26506f757857d80a1e490117ac3402e60c3b255c18a36` |
| harness/test_phase7.py | `2cdffb630d20b746574b079f0867df3ef5e960e8f17ba8e8c45be60064f19bf1` |
| harness/contracts/phase7.md | `a68798c1efd579d9648fed2d35c1adada4924990d116751d808869d436241581` |

A stable copy is in `harness-review/cur2/`: `git archive HEAD` plus these three files. If any of the hashes differ, this approval does not carry over.

## Verification performed (all logs in this directory)

- **Tests on stable snapshots.** I ran `test_phase7_bench`, `test_phase7_profile`, `test_phase7_device` and `test_phase7` against isolated copies:
  - HEAD: 235 tests OK, 1 skip (`unit_base.log`).
  - Intermediate 14:32 state: 247 OK, 1 skip (`unit_cur.log`).
  - **Final: 251 OK, 1 skip** (`unit_cur2.log`). The skip is the existing root-authorized native-window opt-in.
- **Mid-edit artifact, now resolved.** One run against the live tree hit an intermediate edit and failed 16 bench tests (`unit_consumers.log`). The causes were an `IndentationError` from `inspect.getsource` on a file that was half-written, and `load_session` raising `terminal private modes evidence unavailable`. That intermediate version required valid geometry for legacy mode parsing. The final version is geometry-free and turns a missing-raw failure into a per-session `*_error`, so the same suites pass on the stable copy.
- **Legacy mode reconstruction parity.**
  - Across 659 retained sessions under `~/.cache/pb-phase7/*`, the non-rendering `ModesOnly` reconstruction matched a full rendering `Terminal` replay fed in 64 KiB chunks in every case (0 mismatches).
  - Randomized chunk splits gave 0 mismatches in 300 cases (`edges.log`) and 0 in 500 more that mixed RIS, OSC-embedded fake CSI/RIS, split UTF-8/ESC and an `initial_screen` seed (`final_probes.log`).
- **No new false failures on retained evidence.**
  - The new "private modes not restored" gate fires only on sessions that already fail (`session timeout`).
  - 266 ref/cand pairs (190 styled-release6 and 76 differential) had 0 mode differences, including the deliberate SIGQUIT/hang/signal non-restoring cases. Both sides there end at `{2004}` (`corpus.log`, `final_probes.log`).
  - Legacy history inference flagged 0 of 659 sessions as ambiguous, newly failed 0, and crashed 0 times (`legacy_history.log`).
  - This agrees with harness-contract/report.md: no retained capture has main-screen scrollback, and no graceful capture leaks a mode.
- **Real `execute` regressions** (`live.log`):
  - A clean alt-screen session with 50 `?2026h/l` frames passes.
  - A SIGKILL non-restore with `screen_restored: false` validates as `[]` and self-compares as `[]`. Against a variant that adds `?1002h` it reports `terminal private modes differ`, so the deliberate signal contract is kept and still parity-checked.
  - Eviction inside the session's final token is flagged in both `failure` and `validate`.
  - Exactly-full history plus SGR or cursor tokens gives `history_overflow=False`, `failure=None`, and 20000 scrollback rows.
- **Emulator edge cases** (`edges2.log`, `edges.log`):
  - A DECSTBM region LF at the bottom margin evicts and is flagged. An LF below the region is not.
  - RI at the region top is not flagged until the bottom deque is full.
  - Shrinking and then growing with a resize does not evict. Eviction at the new bottom is flagged.
  - Autowrap through `draw()` evicts and is flagged.
  - Alt-screen scrolling does not touch main history.
  - The flag stays set after RIS. ED3 resets history, and refilling it to below capacity is not flagged.
  - `phase7_bench.clone_terminal` keeps both the `Terminal` flag and the `StyledHistory` flag, and clones stay isolated.
- **Validation of the `terminal_modes` structure.** Bool modes, out-of-set modes (`7`), duplicates, missing or extra keys, a non-dict value, and string modes all fail validation for the affected role. `compare` does not crash on them, and unsorted equal sets compare equal.
- **Consumers.**
  - `phase7_bench.archive_execute`, `retained_sample`, `replay_frame` and `clone_terminal` work with the new keys.
  - `phase7_device` and `phase7_profile` feed live `execute` results that already carry `terminal_modes` and `history_overflow`.
  - `load_session` reconstructs both fields from the captured case before `reduce_report` substitutes the active contract, and it never rewrites retained files (covered by tests).

## Limitations (non-blocking, documented or conservative)

1. **Reverse-index bottom-history eviction is rejected even though nothing can observe it.** `history.bottom` is never exported. Legacy inference treats any RI, RIS, or ED3 (`CSI 3 J` / `?3J`) anywhere in the raw stream as ambiguous, including inside the alternate screen, and treats scrollback at 20000 as ambiguous. That makes it stricter than needed, but it fails closed and is written into the contract. No retained session triggers it.
2. **Legacy `failure` strings are not rewritten.** An old capture that was falsely marked as overflowing because its history was merely full still fails during reduction. That is intended.
3. **The session-mode set leaves some modes out.** It excludes DECAWM `?7`, which is on by default and cannot be represented with set semantics, cursor blink `?12`, and keypad application mode `ESC =`. It also does not check mode parity at checkpoints. The contract lists all of this. The Rust TUI only emits `?25`, `?1049`, `?2004`, `?1002/1003/1006`, and the first two are already covered by the frame assertions.
4. **One tokenization difference between live and legacy parsing.** An unterminated control over 64 KiB can split differently when the live run sees it in chunks and legacy parsing sees it in one feed. Such sessions already fail with `unsupported`.
5. **RIS while the alternate screen is active.** It clears the tracked session modes but leaves `alternate=True` and the main history untouched. This behavior is older than this diff, and both binaries are treated the same way.
6. **Snapshot staleness.** Sol was still editing during the review. Anything changed after the hashes above has not been reviewed.

No source edits, commits, pushes, GitHub/wiki actions or delegation were performed.
