# Phase 7 review: A1 combined functional preflight failure (Opus R1)

Scope: an independent, read-only diagnosis of the first combined functional preflight, `under30-combined-a1-primary-dark-01`. No production, harness, case or contract file was edited. No native session, helper, compiler, probe, CLI or build ran, and the common lock was not touched. This is not a timing claim, and it proposes no relaxation, normalization or rerun.

## Verdict

**Primary cause:** the harness's `sql_resize_geometry` readiness predicate cannot match the real SQL layout.

- The predicate (`harness/phase7_bench.py:525-537`, documented as "closed-drawer" at `docs/phase7-measurement.md:285`) requires the `rows N/T` footer to end at column **W−3**.
- Both the Go reference and the Rust candidate start with the **columns drawer open**:
  - Go: `columnsDrawerOpen: true` at `pkg/model/query.go:447`.
  - Rust: `columns_drawer_open: true` at `src/tui/sql.rs:2550`.
- The scripted actions (down, page-down, home, resize) never close the drawer. The only toggle is `ctrl+l` (`pkg/model/query.go:1041-1042`), and the script never sends it.
- With the drawer open, the results pane is `editorW = W − sidebarW − 1` wide, so the footer ends at **editorW − 3**, not W−3.

As a result, the `resize-large` checkpoint (action 8) never qualified. The case ran until the 62 s case timeout (`failure: "session timeout"`), the CLI was SIGKILLed, and action 9 (`resize-original`) was never dispatched.

**Reported error (downstream):** `ValueError('captured resize sequence differs from declared script')`.

- It comes from `resize_policy_issues` (`:861-872`), raised by `check_binding` (`:1216-1218`) after `p7.sample` returned. The declared resize indices are `[7, 9]` and the captured ones are `[7]`.
- This is a consequence of the timeout, not a resize-delivery fault. The resize at index 7 was delivered correctly:
  - source `ioctl-only`, geometry `[140,45]`;
  - Go repainted fully at 140×45 using 14,293 bytes after dispatch.

### Correction to the root's hypothesis

The drawer is not width-gated, so 140 is not a threshold for whether it appears. It is open at every width, including the original 120×40.

Width only changes the sidebar's size: `sidebarW = 34`, or `37` when `W >= 140`.

- Go: `pkg/model/query.go:57` (`len("02 Jun 2026, 13:25 | UTC+05:30") + 4 = 34`), `:1217-1220` and `:1231`.
- Rust: `src/tui/sql.rs:35-36` and `:3446-3453`, which mirror Go.

The resulting footer positions:

| Size | sidebarW | editorW / resultsW | footer ends (exclusive) | predicate expects |
| --- | --- | --- | --- | --- |
| 120×40 | 34 | 85 | col 82 | col 117 |
| 140×45 | 37 | 102 | col 99 | col 137 |

The predicate is therefore unsatisfiable at both resize targets for both roles. `resize-original` (120×40) would have failed in the same way.

## Evidence (pure retained replay)

The script is `/home/ajs/.cache/pb-phase7/a1-functional-failure-opus-r1/replay.py` (sha256 `931e41c0ddfcf9a80798178cef11c3fe71458b808cc28edbd008d7ff6e9e6d03`). Its output is `replay-output.json` (sha256 `4935267d3dc894ad660d2a593e349d6e51d67e6aa7164f25f5593aabc51699fb`).

**Guards and run:**
- Before importing the harness, the script blocks `os.fork`/`forkpty`/`posix_spawn*`/`exec*`/`system`/`openpty`/`kill*`, `subprocess.*`, `pty.*` and socket construction.
- It ran once under `timeout 300` with exit 0 and empty stderr.
- One earlier attempt exited 1 at import because the first socket guard broke `ssl`'s subclassing. The guard was replaced by a raising subclass. Both attempts are disclosed here; neither executed any harness code path.

**Results:**
1. **Reconstruction is exact.**
   - Feeding `stdout.bin[:23905]` (the action-7 decoder offset) into `p7.Terminal(120,40)` reproduces the retained `return-first` frame cell for cell.
   - Applying the emulator resize to 140×45 and then feeding the remaining 14,293 bytes reproduces the retained `final_frame` exactly.
2. **Every resize-large clause except the footer column held at the end:**
   - `useful` (contains/absent/alternate/raw tty/request path), `output_after_action`, `resize_rerender` (frame ≠ snapshot) and `dimensions [140,45]` all held;
   - so did the row H−6 borders `│…│`, the marker `bench-row-000000` on row 16, the row-16 borders and the right-edge corner.
   - The one failing clause is `footer_at_W-3`: the actual footer ends at col 99.
3. **No transient pass.** I scanned the post-resize stream at a 16-byte stride. `sql_resize_geometry` was never true at any point, while all the other resize-large predicates did become true together.
4. **Negative controls.** The predicate is false on every retained 120×40 checkpoint frame:
   - `startup` and `return-first`: the footer ends at col 82.
   - `row-down` and `page-down`: these carry different footers, and the clause test uses the row-0 target.

   So the predicate was never satisfiable at the original size either.
5. **Positive controls.** A synthetic closed-drawer, full-width frame passes the predicate at both 140×45 and 120×40. The predicate works as written; its layout premise is wrong.
6. **Counterfactual (diagnostic only, not a proposal).** The footer does sit exactly at `resultsW − 3` in both the 140×45 final frame and the 120×40 `return-first` frame. H−6 and row 16, the source-derived values R5 marked [U], are confirmed correct.

**Timeline:**
- Resize dispatch to reap: 61.10 s.
- Start to reap: 62.01 s, against a case timeout of 62.0 s.
- The supervisor reported 63.88 s through cleanup, exit 2, `child_failed`, and a clean reap/lock release.

## Why the earlier reviews missed it

- The R4/R5 unit test (`harness/test_phase7_bench.py:269-292`) builds a synthetic full-width `│…footer│` row. It never uses a real Go or Rust frame.
- R5 noted that "row 16 and H−6 … only the actual preflight can confirm them [U]". The W−3 column was not listed as unconfirmed.
- R6–R11 do not revisit the geometry predicate.

## Secondary finding (error masking, harness structure)

`_functional_preflight` calls `check_binding` before `observation_issues` (`phase7_bench.py:1660-1663`). Because a timed-out session is missing scripted resize events, `check_binding` raises. That aborts the whole preflight before the Rust role, and it reports the downstream resize-sequence error instead of recording `session timeout` / `resize-large` not ready as a per-role issue.

The retained row still shows `issues: ["not completed"]`. The raw evidence is intact (`execute/` archive plus `session.json`), so diagnosis was possible. This is disclosed as structure; no change is made here.

## Unknowns

- **Rust candidate:** not run. Its predicted identical failure is inferred from the mirrored source layout (`src/tui/sql.rs:2550`, `:3446-3453`). It was not observed.
- **Other cases:** whether the 1500 and 20000 SQL variants, the delayed/sustained variants, or the PromQL `dense_table_geometry` (`:540-550`, also W−3) share a layout mismatch was not replayed. No retained PromQL capture exists in A1. `dense_table_geometry` uses the PromQL table layout, which I did not source-verify here.
- **Fix:** the right fix (close the drawer via `ctrl+l` in the script, or derive the footer column from the source layout) is a contract decision for root/owner. Either would need its own review and fresh preflight.

## Data hashes

**Inputs:**

| Input | sha256 |
| --- | --- |
| `phase7_bench.py` | `bf630d9ac4dd4e615f221e218cfce7cbb99438c2de1bf5cfdc388941eef013c2` |
| `phase7.py` | `dc7574f058b9ff6ae7b77a912847fdbe02d8b68baab8728237d6ef040e6a44fa` |
| `phase7_workloads.py` | `04404c96efabad1d2b0674df17751856913363443744c3dd1fb497f07a5f6178` |
| `phase7_spawn.c` | `0279d5fb06e4f38adc32004c152fc42c94194d59f234ee5a9c5694f74a6d31f4` |
| `pkg/model/query.go` | `e902816d2c78163cfcdb6a5d272ae750f5beae533c1d01fa50e13eb9715c781d` |
| `src/tui/sql.rs` | `98eb54071505344d5c42f3fd32e221d1d86664ebd4623a6af884c85d7d2fcb1f` |

The harness hashes match `a1-prospective.json`.

**Retained A1 artifacts:**

| Artifact | sha256 |
| --- | --- |
| `report.json` | `f3e40e83612bbe5202510da3bd1d9638f9fa0188659882edbf1a02d852f83ee9` |
| supervision `report.json` | `b91eca1834c0199ca327d191d57186b69e72d8d631701454b01d149b773df8e4` |
| `reference/session.json` | `b0a6919c04a9c157d15101dfabaff64f2e546364e5c841a4d5149aaf1029ce6b` |
| `reference/execute/session.json` | `ea407b7dc5c292ca86b6ee2324f150f883e94c88860f8f959b4f541d528fcf52` |
| `stdout.bin` (both copies) | `2524289b1f62556ade81159f34fb528b18f8b0c12a912dc51357b1f81c9af0fa` |

- Case `sql-scroll-500-120x40-dark`: `0b189aaf990b4e665d3de16134d6f23737aa42312ed329a9907d11386b5b6869`.
- Reference artifact `pb-go`: `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`.

## Tests actually run

The guarded pure replay above, run once successfully after one disclosed import-guard failure. No unit tests, full suite, native child or PTY ran.

Frozen for root disposition.
