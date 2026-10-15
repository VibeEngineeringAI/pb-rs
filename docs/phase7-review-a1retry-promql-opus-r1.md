# Phase 7 review: A1 retry PromQL table-readiness failure (Opus R1)

Scope: a fresh, independent diagnosis of the four PromQL dense failures in the actual A1 retry `under30-combined-a1-primary-dark-02`, using retained artifacts and source only.

I read these in full:
- `docs/phase7-review-a1-functional-failure-opus-r1.md`;
- `docs/phase7-review-a1-geometry-fix-opus-r1.md`;
- `docs/evidence/phase7/a1-geometry-review-disposition.json`;
- the current bench (`e03e1d2b…6c01`).

What I did not do:
- I edited no source, harness, corpus, comparator, contract or test file. The only outputs are this file and `/home/ajs/.cache/pb-phase7/a1retry-promql-review-opus-r1/`.
- No native session, CLI, PTY, socket, probe, compiler, build, profile, common lock or nested delegate was used.
- This is not a timing or performance claim, and it is not subset acceptance.
- Source-only claims below are marked **[source]**. They are not actual native verification.

A root pause arrived mid-review. It was acknowledged with zero active processes, and work resumed only after root's explicit RELEASE (13:36:29Z).

## Verdict

**Root cause, both roles, all four dense cases.** `dense_table_geometry` (`harness/phase7_bench.py:548-558`) requires the footer `1 | N rows` on **zero-based row H−6 = 34**, ending at W−3. In every actual Go and Rust table frame, the footer is on **row 32 = H−8**, ending exclusively at **column 117 = W−3**. Row 34 is a blank inner pane row.

- **Column:** the earlier W−3 assumption is correct. PromQL's results pane is full width.
- **Row:** the earlier H−6 assumption ("last inner content row") is wrong.

**Chain of events, identical in all 8 roles:**
- After `startup`, `setup-ready` and action 4 (`send t`, input hex `74`) were dispatched, both CLIs fully painted the table.
- The table-view wait (case action 5) never qualified, because its only failing clause was the geometry check. Every text clause (`Table View`, `bench-series-0000`, `1 | N rows`, no `fetching...`, alternate screen) held.
- The session ran to the case timeout, and SIGKILL followed.
- Every downstream issue follows from this: termios/alternate screen not restored, the missing `table-view`/`chart-view`/`idle`/`series-*` checkpoints, and `KeyError('table-view')`.

The CLIs behaved identically to each other. This is a collector-contract defect, not a CLI fault.

## Exact source derivation (Go `pkg/model/promql.go` `7332d30d…`; Rust `src/tui/promql.rs` `51387004…` mirrors it)

1. **Pane heights (`View`, `promql.go:1384-1468`).**
   - `bottomHeight = lipgloss.Height(buildPromqlBottomBar)`. At 120 columns that is 4: 2 KeyGrid hint rows (rows 36-37), the hairline (38) and status (39). The retained frames show these rows.
   - `availH = H − 13 − 4 = 23`, so the pane covers rows 13-35 and its **bottom border is H−5 = 35**.
   - `resultsInnerH = availH − 3 = 20`. The source comment assumes a separate "title row", but the title is drawn inside the top border.
2. **Pane body (`renderPromqlResultsPane`, `:1555-1576`).** It pads the body to `innerH = height − 2 = 21` rows (rows 14-34) with `paneBodyLines` (`query.go:1483-1511`). Content never exceeds `resultsInnerH = 20` lines, so **row 34 (H−6) is always blank**.
3. **Table (`renderResultsTable`, `:1301-1367`).**
   - `detail = ""` because `unwrapMetric` is false, but `detailH = lipgloss.Height("") = 1` (lipgloss v1.0.0 `size.go:30-31`: `Count("\n") + 1`).
   - So `tableHeight = 19` and `pageSize = 14`. The table output is header (3 lines) + 14 rows + bottom rule = 18 lines, then `paddingH = 20 − 1 − 1 − 18 = 0`. The footer is the 19th line.
   - The footer therefore lands on **row 14 + 18 = 32 = H−8**, and **row 33 (H−7)** is padded blank.
   - In Rust, `results_table` (`promql.rs:4775-4826`) uses `height("") = "".split('\n').count() = 1` (`:1021-1023`), and `view` (`:5228-5275`) mirrors the rest.
4. **Footer column.** The footer is `lipgloss.Width(116).Padding(0,1)` inside `resultsInnerW = W − 4`, so it ends at 2 + 115 = **117 = W−3**. This confirms the W−3 column.

In general: footer row = H − bottomH − 4. That is H−8 for every dense case, since all 16 dense hashes in the 52-case set are 120×40.

## Retained raw replay (pure, guarded, independent)

**Script:** `replay.py` (`33f4fe8e…`), run with `guard.py` (`6b89ce4f…`, identical to the prior geometry review's guard).
- **Guard:** before any harness import, it blocks os fork/spawn/exec/system/popen/openpty/kill, `subprocess.*`, `pty.*`, socket factories and construction, and `fcntl.flock`/`lockf`.
- **Run:** `harness-venv/bin/python -E -s`, one run. Exit 0, empty stderr, zero guard trips.
- **Output:** `replay-output.json` (`133e5b25…`); stdout `2b9467d8…`.

**Reconstruction and actual geometry, all 8 roles:**
- Every role passed:
  - `stdout.bin` equals the session `stdout_hex`.
  - Feeding bytes up to the action-4 decoder offset into `p7.Terminal(120,40,'dark')` reproduces the retained `setup-ready` frame cell for cell.
  - Feeding the rest reproduces the retained `final_frame` exactly.
- Recorded actions are `[0..4]` in every role, so table-view (5) was never reached.

| Measurement (all 8 roles identical) | Value |
| --- | --- |
| footer row / exclusive end | 32 (H−8) / 117 (W−3) |
| pane top / bottom border | 13 / 35 (H−5) |
| blank inner rows | 14 (table top border), 33 (H−7), 34 (H−6) |
| data rows / page indicator | 14 / `1/19` (256), `1/1829` (25 600), `1/18286` (256 000) |
| live predicate on the final frame | false |

The page indicators equal `(N−1)//14 + 1`, which independently confirms the render page size of 14.

**Transient scan:** I scanned the full post-`t` stream (6,197 bytes per role) at a 16-byte stride. The live `dense_table_geometry`, combined with the text clauses, was **never true** at any point in any role, so no early or transient pass was missed.

**Diagnostic candidate predicate:** footer on H−8 ending W−3, preceded by a space and followed by `  │`; rows H−7 and H−6 exactly `│ … │` blank; row H−5 exactly `└─…─┘`.
- **Positive:** true on all 8 actual final frames.
- **Timing:** it first became true 5,504 (dense-1) or 5,584 bytes after `t`, 96 or 176 bytes after the text clauses first held. It made one false→true transition and stayed true to EOF.

**Negative controls**, mutated from each actual final frame (8/8 roles each); every one is rejected by the candidate:

| Control | Candidate | Live |
| --- | --- | --- |
| footer moved to H−6 (the old assumed layout) | false | **true** |
| footer at H−7 | false | false |
| footer shifted ±1 column | false | false |
| bottom border erased (partial paint) | false | false |
| stale text left in H−6 | false | false |
| footer right border missing | false | false |
| wrong token `2 \| N rows` | false | — |
| wrong total `1 \| N+1 rows` | false | — |
| stale `setup-ready` chart frame | false | — |

The first row shows that the live predicate accepts a counterfeit layout and rejects the real one.

**Partial-frame finding.**
- Stride-16 replay showed the frame where the candidate first passes is not cell-identical to the final frame. My one allowed correction retry tried to characterise this at byte stride. It failed (exit 1; `retry-stderr.txt` `c323ad95…`, `retry-exit.txt` `0c6868c2…`): pyte `screens.py:241` raised `IndexError` on a split multibyte cell when `text()` ran mid-character. No output was produced. That was my last harness execution.
- I then read the retained tail bytes directly as a file. After the candidate's first-true offset, the remaining 709 bytes are byte-identical for Go and Rust:
  - they finish row 34 (unchanged) and skip row 35 (`ESC[B`);
  - they rewrite **hint rows 36-37 from chart hints to table hints** (`<↑/↓> Row … <w> Wrap metric …` / `<t> Toggle chart/table …`);
  - they skip row 38 and park the cursor with `ESC[40;0H`.
- So the candidate can admit a frame whose results pane is complete but whose bottom bar is stale. The live predicate has the same class of weakness.
- **Tightening:** require `'<t> Toggle chart/table' in lines[H−3]`, the last repainted row. The final frames contain it on row 37; the retained chart `setup-ready` frame's row 37 is `<ctrl-r> Run  <ctrl-c> Quit`. **This clause was verified by reading the frames, not executed.**

## Proposed smallest correction (not applied)

**Owned files:**
- `harness/phase7_bench.py`: `dense_table_geometry` only.
- `harness/test_phase7_bench.py`: replace the synthetic `test_dense_partial_top_and_wrong_footer_rejected_before_full_target` (`:772-785`). It paints the footer at 1-based row 35, which is H−6 and bakes in the wrong layout. Add retained-frame tests: a gz fixture of one Go and one Rust dense-1 raw stream plus offsets, with the controls above.
- Contract and measurement text that states H−6: `harness/contracts/phase7.md:284` and `docs/phase7-measurement.md:367,465`.

**Proposed function:**

```python
def dense_table_geometry(frame, wait):
    """PromQL table (promql.go View/renderResultsTable; promql.rs mirrors), 120-col bottom bar of 4 rows:
    pane bottom border H-5; H-6 blank (pane innerH=availH-2 > resultsInnerH=availH-3); H-7 blank (empty detail has
    lipgloss.Height 1, so tableHeight=inner-1); footer H-8 ending W-3; table-focus hint row H-3 (last repaint) present."""
    width, height = frame['columns'], frame['rows']
    footer = next((t for t in wait.get('contains', []) if re.fullmatch(r'\d+ \| \d+ rows', t)), None)
    if (footer is None or height < 30 or len(frame['cells']) != height or
            any(len(row) != width for row in frame['cells'])):
        return False
    lines = [''.join(cell[0] for cell in row) for row in frame['cells']]
    blank, column, row = '│' + ' ' * (width - 2) + '│', width - 3 - len(footer), lines[height - 8]
    return (column >= 1 and row[0] == '│' and row[column - 1] == ' ' and row[column:] == footer + '  │' and
            lines[height - 7] == lines[height - 6] == blank and
            lines[height - 5] == '└' + '─' * (width - 2) + '┘' and
            '<t> Toggle chart/table' in lines[height - 3])
```

**Additional strictness:** `row[column − 1] == ' '` closes a latent alias in the live predicate. Today a target `1 | N rows` slice-matches an actual `11 | N rows`, and the `contains` text clause matches it too. This matters for series-scroll targets. It is a design argument and was not executed as a control.

**Case identity:** unchanged.
- `case_hashes` are `p7.digest(case)` over case data (`phase7_bench.py:1469,1645`). The wait keeps `dense_table_geometry: True`, and no action, target, count, seed or fixture changes.
- All 52 hashes stay as they are. Original A1 and retry captures stay retained.

**Validation still needed:** fresh independent review, then root's actual 24-role preflight. A source-derived pass here is not native verification.

## Second, separate blocker: sustained page step [source; no retained evidence]

The sustained case (`phase7_bench.py:299-321`) computes `page = … − 5`, which is **16** at H=40. That is Go's constructor value `promqlPageSize(h, 3)` (`promql.go:343`). Source says the actual model step differs:

- **Navigation step = 15.** On `WindowSizeMsg` (`promql.go:493-508`) the model resets to `promqlPageSize(40, bh)`, with `bh = lipgloss.Height(bottom bar)` measured at startup focus `query` (`:454`, `PromqlNavigationMap` `:78`). That focus has 5 hints (`:1837-1844`), giving 2 KeyGrid rows, so `bh = 4` and `pageSize = 15`.
  - bubble-table `pageDown`/`pageLast` set `cursor = page × 15` (`pagination.go:53-105`).
  - Rust mirrors this at `promql.rs:3389` and `:1955-1969`.
- **Render page size = 14.** `View` uses a value receiver, so the per-frame 14 (above) is discarded.
- **Combined effect.** For example, one `pgdown` should show `16 | N rows` and indicator `2/…`; `end` on 256 000 rows should show `255991 | 256000 rows`. The bench expects `pages × 16 + 1` and `255985`. So `series-forward-*`, `series-last` and `series-reverse-*` would still time out after the footer fix.
- **Not fixed here.** No retained capture contains a PromQL table page-down, so the step is not guessed or proposed as a patch. The source comment at `promql.go:259-261` claims View and the handler agree; they do not.

**Genuine Go control design** (for root's separate bounded run; no new instrumentation):
- **Setup:** Go `pb-go` (`fae8411f…`), case `promql-dense-1-120x40-dark` fixture and argv, the same `ctrl-q`/`shift-tab`/`t` setup, and readiness on the corrected footer geometry.
- **Keys:** send one key per action, never bursts, in this order: `pgdown` ×3, `end`, `pgup`, `home`.
- **Readiness for each action:** the footer row text H−8 differs from the previous checkpoint, plus the existing 80 ms styled stability. Do not presuppose a target value.
- **Record:** the H−8 footer (`cursor | 256 rows`), the page indicator and the first data row.
- **Source predicts:** cursor+1 = 16/31/46, then 256, 241, 1; indicators `2/19`, `3/19`, `4/19`, `19/19`, `18/19`, `1/19`. Three presses distinguish a constant step of 15 from 14 or 16.
- **Rust:** an optional second role, for fidelity only.
- **Case impact:** only the four sustained case definitions (dark/light × primary/resource-attribution) would change hash once a measured step is adopted.

## Ordered request mismatch (separate; noted, not waived)

The pure comparison of `p7.ordered_request_contract` for both roles covered all 5 PromQL cases.
- **Equal:** dense-1 and dense-100.
- **Differs:** dense-1000 at indices [0,2], dense-1000-sustained at [1,2] and query-error at [1,2]. In every case the multiset is equal, all requests are among the first three, and nothing follows.
- **No later requests:** each role made exactly 3 requests, all at startup. `t` and quit issue no requests.

| Case | Go order | Rust order |
| --- | --- | --- |
| dense-1000 | query_range, home, values | values, home, query_range |
| dense-1000-sustained | query_range, **values, home** | query_range, home, values |
| query-error | query_range, home, values | query_range, values, home |

**Arrival spread:** all three arrive within 0.72 ms on three separate connections.

**Why the order varies:** Go `Init` (`promql.go:466-477`) issues them through `tea.Batch`, which runs concurrent goroutines. **Go itself** reorders between dense-1000 and dense-1000-sustained, whose startup is identical. Rust once sent `query_range` last even though connection 1 opened first.

**Classification:** intrinsic initial-fanout nondeterminism, not a Rust bug. The dense and error cases carry no `initial_fanout_count`, so the comparator compares the arrival order of concurrent requests.

This is the same mechanism root identified for the error case. The narrow fanout proposal is owned by Sol (off-tree). I propose no waiver.

**Non-overlap with Sol:**
- Sol's metadata touches `dense_case` and the error-case definitions, which changes case identity.
- My correction touches only `dense_table_geometry` and its tests, with no identity change.
- Both edit `phase7_bench.py`, so patches must be serialized.

## Review of prior assumptions

- **W−3 (full-width footer column):** confirmed on actual frames for both roles.
- **H−6 (row):** source-inferred in R5-L3 and documented as "pending actual preflight", and wrong. The earlier synthetic test encoded it.
- **Earlier geometry-fix review:** I spot-checked only the PromQL width ("PromQL unchanged by design") and did not catch the row. That is my own prior blind spot, disclosed here.

## Attempts and hashes

| Run | Result |
| --- | --- |
| 1. `replay.py` | exit 0; stderr empty (`e3b0c442…`); 0 guard trips; all 53 tracked identities unchanged start→end |
| 2. `retry-firsttrue.py` (`68497947…`) | exit 1, pyte `IndexError` at byte stride, no output; not repeated |

**Outside those two runs:** plain-file reads (JSON, the raw tail bytes and request timestamps) used system `python3 -I` without harness imports. There was no EDQUOT, no redirect retry and no moves.

**Inputs verified (expected = actual):**

| Input | sha256 |
| --- | --- |
| bench | `e03e1d2bfce45d7123b67a1870e8e3e38c7d5a6ae92c99881c513f4965846c01` |
| `phase7.py` | `dc7574f0…a44fa` |
| workloads | `04404c96…6178` |
| tests | `244bd6e4…845c` |
| retry `report.json` | `1dcd24f6561568feabf83c5fc3c6cdfada4d30afcf3d56a9e23a5e2709678dd4` |
| supervision `report.json` | `2257e09ca698af5b5fd2a5b834f015f88698246e3a8e13fa91ca3a894bebc3a3` |
| `promql.go` | `7332d30d…88b8` (equals the report's source manifest) |
| `promql.rs` | `51387004…cf55` (manifest) |
| Go artifact | `fae8411f…` |
| Rust candidate | `6d80054f…868443` |

**Tracked identities:** the 53 tracked identities cover:
- all 5 PromQL cases × 2 roles × (`session.json`, `stdout.bin`, `execute/session.json`, `execute/stdout.bin`);
- the bench, `phase7.py`, workloads, tests, `promql.go`/`.rs`, `query.go`, `sql.rs`;
- both prior reviews and the disposition JSON;
- the retry and supervision reports.

The full map is in `replay-output.json`.

Frozen for root.
