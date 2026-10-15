# Phase 7 final production review — independent Opus 5.5, round 4

- Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`, 2026-10-14.
  Base `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`, Go pin `07af390ab09bda8ac9e427ffc6310077bace0125`.
- This is a fresh round, not a continuation of R3. It covers production source after the R3
  fixes (R3-F1, O3-1, O3-2 and the I-a/I-b comment edits).
- Excluded: the moving collector, bench, profile, helper and harness tooling, plus tests and docs.
  Nothing in them is used for acceptance. Performance, platform and release gates are also
  separate from this verdict.

## Method and limits

- **The shell was unavailable.** The first Bash call failed with EDQUOT in
  `/tmp/claude-1000/...` before any command ran. I made no further shell attempts and used no
  symlink workaround. As a result:
  - I ran **no** builds, tests, CLI/PTY sessions, profilers, timings or network requests;
  - I computed **no** hashes. Every source and artifact hash below is **[supplied]**. I did
    **not** independently verify the git pin;
  - the only file I wrote is this report. I wrote no cache evidence.
- Evidence labels:
  - **[source]**: my own reading of current files plus causal reasoning. Execution is not
    implied.
  - **[supplied]**: owner or parent execution records that I read but did not reproduce.
  - **[unknown]**: not established by adequate evidence.
- Go was read directly:
  - from the worktree: `pkg/model/{query,credential/credential,button/button,savedQueries,promql}.go`,
    `cmd/queryList.go` and `pkg/http/http.go`;
  - from the module cache: `sahilm/fuzzy@v0.1.1/fuzzy.go`, `bubbles@v0.20.0/list/list.go`
    (`DefaultFilter`, `filterItems`), `bubbletea@v1.2.2/tea.go` (`handleCommands`, `Run`,
    `shutdown`, `recoverFromPanic`) and `go1.25.4 src/unicode/letter.go` (`SimpleFold`).
- **Prior reports, read in full:**
  - `phase7-review-final-r1.md`, `-lifecycle-r1.md`, `-lifecycle-r2.md`, `-service-r1.md`,
    `-service-r2.md`, `-byte-interim-sol-r1.md`, `-sql-identity-interim-sol-r2.md`,
    `-final-contract-sol-r3.md` and `-final-opus-r3.md`;
  - the full `phase7-review-resolutions.md`;
  - the current section of `rust-phase7.md`;
  - `phase7-admin-saved.md`.
- **Evidence read:**
  - `opus-r3-low-resolution.md`;
  - `schema-fallback-clock-{before-fix,applied-freeze}.json` and
    `schema-fallback-clock-process.py`;
  - `release4-source-checkpoint.json`, `unicode-rust-central.json` and
    `input-fairness-release3.json`.
- **Production source read this round:**
  - `tui/sql_service.rs:1-300`;
  - `tui/sql.rs:1-470` (entry point, driver, merge, `recover_window` and dispatch);
  - `tui/profile.rs:700-1030` and `:1100-1300` (the credential form and its tests);
  - `tui/saved.rs` (all);
  - `tui/saved_fuzzy_unicode.rs` (header, lookup code and fold-table samples);
  - `tui/input.rs:1-420`;
  - `tui/terminal.rs:1-200`;
  - `tui/promql_service.rs:140-300`;
  - `tui/login.rs:1-120`;
  - `src/saved_queries.rs` (all);
  - `src/api.rs:380-540`;
  - `Cargo.toml`.
- **Test fixture read:** `tests/interactive_effects.rs:760-930`, which includes the two new
  clock cases.
- **Not read this round** (stated so that no complete audit is claimed):
  - the rest of `tui/sql.rs` (reducer, render and window cache), `tui/promql.rs`, `promql`
    decoders, `tui/device.rs`, `tui/admin.rs`, `tui/editor.rs`, `tui/style.rs`,
    `tui/json.rs`, `tui/clipboard.rs` and `terminal.rs:200-end`;
  - `src/{lib,admin,process,datasets}.rs`, `sql/mod.rs` and `api.rs` outside 380-540;
  - the current `phase7-{auth,sql,promql,terminal,editor,style,effects,shared-contracts}.md`;
  - the release3 build, central, device and styled JSONs (except as quoted in the reports I
    read).
- For those unread modules, this round relies on the R1–R3 source dispositions. Their
  supplied source hashes are unchanged from the frozen R3 tree, except `profile.rs`, `saved.rs`,
  `saved_fuzzy_unicode.rs`, `sql.rs`, `sql_service.rs`, `terminal.rs` (comments) and
  `promql_service.rs` (comments). That comparison uses the
  `schema-fallback-clock-applied-freeze.json` and `release4-source-checkpoint.json` maps
  **[supplied]**.

## Verdict

**Bounded source verdict: changes requested at Low severity only. One new Low finding (O4-1)
needs either a narrow fix or an explicit ledger entry.**

- No medium or high production defect was found in the inspected surface.
- R3-F1, O3-1 and O3-2 are resolved in source, as are the I-a and I-b comment corrections.
- The older obligations I re-checked still hold.
- This is not acceptance:
  - successor execution is pending (see "Must-run");
  - the performance, platform and release gates are separate.

## Resolution of R3 findings

### R3-F1 (medium): resolved in source

**[source]**

- `sql_service.rs:155-157` samples `chrono::Utc::now()` with format `%Y-%m-%dT%H:%M:%SZ`. That
  happens after every unsuccessful primary path and immediately before the fallback
  `fetch_raw`:
  - a GET send error;
  - a non-200 status after `read_all_lossy`;
  - a 200 response whose `decode_arrow_schema_names` returns `None`;
  - a skipped GET after a `schema_endpoint` error.
- Go: `query.go:3084` uses `time.Now().UTC().Format(time.RFC3339)` after the GET, body read,
  status check and decode, before `fetchData`. In UTC both render `Z`, and both truncate to
  whole seconds.
- The tiny difference is that Rust samples after building the fallback query
  (`prepare_fetch_query`) and Go samples before `fetchData`'s rewrites. That gap is
  microseconds of pure computation, with no I/O between.
- The following are unchanged:
  - one `effect_scope()` covers both requests (`:120`);
  - each request keeps its own 15 s `SCHEMA_TIMEOUT` (`:125`, `:163`);
  - the raw `Vec<u8>` identity goes through `schema_endpoint` (`:246-248`);
  - an `AddAuthHeaders`-equivalent failure returns immediately (`:127-132`), matching
    `query.go:3053-3055`;
  - a successful primary returns with no fallback (`:137-144`);
  - dispatch at `sql.rs:407-411` now passes only the dataset bytes, and completion remains
    unguarded.
- The U3 equivalence still holds when `schema_endpoint` fails. Go `client.NewRequest`
  (`pkg/http/http.go:62-80`) also ignores the JoinPath error, then runs `AddAuthHeaders`. So
  the auth or `unsupported protocol scheme ""` text arises from the fallback in both binaries.
  The new clock position does not change that path.

**Test fixture [source]:** `interactive_effects.rs:775-846`.

- It holds the GET across at least two observed wall-clock seconds, gated on actual request
  arrival.
- It asserts `release_second <= endTime <= int(post_observed)` with no tolerance, plus the
  exact query, `startTime`, `fields=true`, Content-Type and request order.
- A primary-success control asserts that no POST is sent.

**[supplied]** `schema-fallback-clock-before-fix.json`:

- pinned Go passes both cases (`endTime` equals the release second);
- release3 fails only the clock case (`CLOCK_BEFORE_GET`) and passes the control.

That is the correct discriminating pre-fix result. The successor pass is pending.

### O3-1 (low): resolved in source

- **Go [source]:** `credential.go:116-171` intercepts only `"enter"` by string. `updateInputs`
  calls `button.Update` *before* the post-update `validInputs` latch clear
  (`credential.go:166-170`, `:175-183`). `button.go:68-83` checks `focus`, then `KeyEnter`,
  then `Invalid`, and emits `Pressed` asynchronously. The `Pressed` handler then quits only if
  the inputs are valid at that moment (`credential.go:111-114`).
- **Rust [source]:** `tui/profile.rs:880-889`:
  - it computes `pressed = button_focused && code == Enter && !invalid` before updating the
    inputs (the pre-update latch);
  - it updates only focused inputs, which are none while Submit is focused;
  - it clears the latch;
  - it returns `pressed && valid()`.
- Plain Enter's sticky latch-only quit (`:850-852`) is preserved.
- `KeyCode::Enter` comes only from CR or ESC CR (`input.rs:100-107`, `:394-399`). LF is
  `Ctrl(10)` and a paste is `Runes`, so the new branch is reachable only through Alt+Enter, as
  in Go.
- Evaluating `valid()` synchronously is one of the outcomes Go's asynchronous `Pressed` allows:
  no other input is focused, so validity cannot change before the `Pressed` arrives unless a
  later key refocuses an input first. That is a Go race, not a defect.
- `CredentialForm` is used only by `profile add`. The login wizard has its own model
  (`login.rs:49-63`), so there is no unintended blast radius.
- The reducer test at `profile.rs:1247-1292` uses the parsed `ESC CR`. It covers:
  - valid and focused;
  - valid but unfocused;
  - an invalid latch that does not cycle focus;
  - the latch clearing on this message, then a submit on the next;
  - a cleared latch with currently invalid inputs.
- **[supplied]** 7 Go PTY credential controls pass. Release3 fails only the focused-valid
  Alt+Enter case.

### O3-2 (low): resolved in source

- `saved.rs:576-578` defines `fold_equal` as `a == b || fold(a) == fold(b)`.
- `saved_fuzzy_unicode.rs:6-13` maps ASCII to uppercase and non-ASCII through a binary search
  over `(source, min-of-orbit)`.
- This is equivalent to Go's `equalFold` orbit walk (`fuzzy.go:208-231`): `SimpleFold` orbits
  partition the scalars, and walking from the smaller rune reaches the larger before wrapping
  only within the same orbit.
- Samples confirm the min-of-orbit rule:
  - `(0x17F, 0x53)` maps ſ to S, matching the ASCII path's uppercase output;
  - `(0x178, 0xFF)`;
  - `(0x39C, 0xB5)`;
  - `(0x3B9, 0x345)`.
- `İ`/`ı` are absent. That matches Go's own `SimpleFold` test classification ("has lower/upper
  but no case fold") and the reducer assertion.
- `is_lower` and `is_upper` (`:15-38`) use stride ranges with `partition_point`, giving Ll/Lu
  membership rather than Rust's derived properties.
- No dependency was added (`Cargo.toml` unchanged). `is_multiple_of` is within
  `rust-version = "1.96"`. The table is saved-only.
- I hand-traced the `("A", "ªA", -12)` score against `fuzzy.go`, and it matches.
- **[supplied]** The parent's actual Rust helper matches pinned Go on all 1,112,064 scalars
  (`unicode-rust-central.json`). The saved PTY controls pass 14/14 on Go and 9/14 on
  release3, with exactly the five expected pre-fix failures (two orbit, three category).

I also re-derived the tie-order claim from R3 rather than reusing it. With
`Less = Score >= Score`:

- each `sort.Stable` pass equals a stable sort by (score descending, current position
  descending);
- insertion sort moves later equal elements first, and `symMerge` places right-block ties
  before left-block ties in all three branches;
- so two passes (`fuzzy.Find`, then `DefaultFilter`) restore the original tie order at any
  size, including the uneven block merges;
- that matches `saved.rs:350`.

### I-a / I-b: comment corrections are accurate and change no behavior

**[source]**

- `terminal.rs:10-15` and `:117-118` now state the one-message read-ahead.
- `promql_service.rs:263-266` now matches `promql.go:2646-2650`: Go suppresses only a *failed*
  request when `ctx.Err() != nil`. The biased `select` preserves one possible race outcome.

## New finding

### O4-1 — Low: the saved fuzzy matcher omits sahilm/fuzzy's NUL-as-end-of-title and stale `matchedIndex` behavior, so it diverges in membership, crash and ranking

This defect predates R3 and was missed by every earlier round, including my own R3. The O3-2
fix did not introduce it.

**Go [source]** (`fuzzy.go:124-203`):

1. **Embedded NUL counts as end of title.** `nextc` is the decoded next rune, so an embedded
   U+0000 makes `nextc == 0`. Line 178 applies the current best match when
   `equalFold(nextp, nextc) || nextc == 0`, so a NUL is treated like end of title. For the last
   pattern rune, `nextp` is `0`, and `equalFold(0, 0)` is also true at a NUL.
2. **`matchedIndex` is never reset.** It is reset neither after an apply (lines 185-188 reset
   only `score`, `bestScore = -1` and advance `patternIndex`) nor per pattern rune. So an apply
   with no fresh candidate reuses the previous index with `bestScore = -1`.
3. **The loop keeps going after the pattern is exhausted.** If `patternIndex` reaches
   `len(runes)` while bytes remain, the next iteration evaluates `runes[patternIndex]`
   (line 136). That is an index-out-of-range panic.
4. **The panic kills the program.** It happens inside bubbles `filterItems`, which is a
   `tea.Cmd` (`list.go:1214-1236`) whose filter is `DefaultFilter`, and that calls
   `fuzzy.Find`. Bubble Tea runs that command under `recoverFromPanic` (`tea.go:318-325`), which
   calls `shutdown(true)`, prints the `Caught panic` text, and makes `Run` return
   `ErrProgramKilled`. `cmd/queryList.go:84-86` returns that error.
   - The result is `Caught panic:\n\nruntime error: index out of range [N] with length N\n\nRestoring terminal...\n\n`
     on stdout, then `Error: program was killed: context canceled` and exit 1.
5. **A score of -1 or below is rejected.** `score > bestScore` starts from -1 (line 155), so a
   candidate whose score has wrapped to ≤ -1 is not selected.

**Rust [source]** (`saved.rs:526-575`):

- It applies only at the true end of the title (`next.is_none()`) or when the next character
  equal-folds to the next pattern rune.
- It keeps the best candidate as `Option` and clears it on apply (`best.take()`), so it never
  reuses a stale index.
- It accepts any score for the first candidate (`is_none_or`).
- It returns as soon as the pattern is exhausted.

**Concrete triggers** (saved titles come from the server's `filter_name`, and Go's JSON decoder
turns `"\u0000"` into a NUL rune). Traces are by hand from `fuzzy.go`, not executed:

| Title (Go string) | Filter | Pinned Go | Rust source |
|---|---|---|---|
| `"a\u0000b"` | `a` | Panics with `index out of range [1] with length 1`. Then kill, the `Caught panic` text, `Error: program was killed: context canceled`, exit 1. No SQL runs. | Shows the item with score 8. Enter applies it and runs SQL. Exit 0. |
| `"a\u0000x"` | `ab` | Matches, with score 8 and `MatchedIndexes [0,0]` (stale apply at end). | No match, so the item is filtered out. |
| any title | ≥ ~43-rune filter matching contiguously | The per-candidate adjacency bonus `5·3^(n-1)` wraps `int64` around the 39th adjacent match. By my arithmetic the first negative candidate score is at about the 42nd, where Go keeps the stale index with -1. | Accepts the wrapped negative score, so totals differ. Ranking only. |

The third row is reachable by pasting a long title into the 64-rune filter with Ctrl+V.

**Reach:** the first two rows need server data containing U+0000. That is the same
exotic-title class the parent chose to *fix* in O3-2, and the brief forbids silently "fixing"
a Go quirk. The third row needs a long contiguous filter and affects only ranking among
multiple matches. **Severity Low**, but the first row's visible outcome differs sharply
(process killed, exit 1, versus normal application, exit 0).

**Narrow fix, saved-only** (no shared change, no new policy). Port the `FindFromNoSort` inner
loop literally:

- keep `matched_index: isize = -1` for the whole item, never reset;
- reset `best_score: i64 = -1` after each apply, and select a candidate only when
  `score > best_score`;
- set `nextp = needle.get(pi + 1)` or `'\0'`, and `nextc = next char` or `'\0'`. The apply
  condition is `fold_equal(nextp, nextc) || nextc == '\0'`, gated on `matched_index >= 0`;
- before matching each character, if `pattern_index == needle.len()`, return a panic outcome
  carrying `runtime error: index out of range [N] with length N`;
- add the final penalty and accept the item only when `pattern_index == needle.len()`.
  - Go's `MatchedIndexes` length always equals the number of applies, and the penalty is
    `len(MatchedIndexes) - len(bytes)`.

Make `refilter` surface the panic through the **existing** command-panic path, not the Update
fault path:

- stop and join the clipboards;
- drop the terminal with no final view;
- print `caught_panic_stdout`;
- return `Err("program was killed: context canceled")`, as the `Event::Panicked` arm does at
  `saved.rs:160-168`.

Go runs the filter asynchronously. A synchronous Rust panic outcome is one of the orderings Go
allows; no scheduler model is needed.

**Ledger alternative:** if the parent judges U+0000 titles out of scope, add an explicit ledger
line covering all three rows, including Go's crash. Silence is not acceptable under this
phase's own standard (G3, O3-2).

**Tests:**

- **Reducer tests:**
  - `("a", "a\0b")` gives the panic outcome with that exact text;
  - `("ab", "a\0x")` gives `Some(8)`, and the item stays visible and applies;
  - a 45-rune contiguous control pair whose expected ranking comes from actual Go.
- **Go/successor PTY controls:**
  - a filters fixture whose `filter_name` is `"a\u0000b"`, typing `/a`. Expect exit 1,
    the exact stdout panic line, no POST, termios restored and config unchanged;
  - `"a\u0000x"` with `/ab`, then Enter. Expect `Query:` plus a POST.

## Older obligations re-checked this round

| Item | Status |
|---|---|
| **R3-F1 schema clock** | Resolved; see above. The scope, the per-request 15 s deadlines, raw identity, primary success and unguarded completion are unchanged. **[source]** |
| **Final F2, window-local recover** | Holds. `sql.rs:361-405`: an inner `JoinSet` under `terminal::recovered`, where a panic becomes the same run/window `"query failed"` (Go `query.go:2612-2617`). Schema and datasets keep the kill path (`:407-418`). |
| **SQL run guard vs unguarded schema/datasets** | Holds at the driver level. `Msg::Window` carries `run_id`, while `Schema`/`Datasets` carry no identity (`sql_service.rs:65-76`). I did not re-read the reducer guard at `sql.rs:~2771` this round; I rely on R3 and Sol R3. |
| **Stamped merge** (C1–C8), `next_step`, completion capacity 16 | Unchanged where re-read (`sql.rs:196-359`). The tie goes to the terminal (`pressed <= finished`). The current-thread dependence and the C4 residual stay ledgered. |
| **Saved menu policy** | Holds. `saved_queries.rs:81-139` against `savedQueries.go:276-344`: a reread failure prints, then continues with an empty config and zero profile; stage-specific stderr; status is ignored; the fetch happens before acquisition. a/Enter/b/Ctrl+C intercept before filtering (`saved.rs:255-260`, Go `:204-216`). There is no delete state (`:1-2`, test `:738-750`). `sql` comes from `desc`, matching Go `Stream()` returning `i.desc` (`savedQueries.go:190`). |
| **Interim byte F1** (raw identity at 3 sites), **F1/F3 decoders**, **F4**, **S1–S6**, **N1–N8/R2**, scoped SIGQUIT | Not re-read beyond the lines listed in "Method". The sources are unchanged since R3 per the supplied hash maps, except comment-only `terminal.rs` and `promql_service.rs`, and except `sql.rs`. In `sql.rs`, R3-F1 removed only the clock argument at `:407-411`. Per the frozen-patch record I did not inspect the identity sites at `~2608/2726/3194/3245` again. R1–R3 dispositions stand. |
| **I-c fairness** | **[supplied]** `input-fairness-release3.json`: one bounded saved-list clipboard flood per binary, with the clipboard visible during the flood, exit 0, restored and config preserved. The scope limits are honest: no proof that input was ready on every poll, and no coverage of profile or device. **[unknown]** No scheduler finding is promoted, and a yield alone would not be a sufficient fix anyway. |
| **I-d / I-e profile re-resolution** | These remain microsecond pre-existing ledger items, with no new evidence. |

## Must-run successor gates

These are pending; none were run here.

1. **Immutable release4 binaries:** normal, cloud and login builds, with a source map bound to
   `release4-source-checkpoint.json`. That record covers fmt, clippy (`-D warnings`), lib and
   the diagnostics check; it claims no counts.
2. **Clock:** `schema-fallback-clock-process.py` against release4 normal, both cases. The full
   49 ordinary effects, the 4 OAuth cases (schema fallback scope) and the 4 real deadline
   cases (15/30/50/120).
3. **O3-1/O3-2:** `opus-r3-low-process.py` on release4 (21 controls) and the three new
   reducer tests by name.
4. **O4-1:** a fix or ledger entry, plus the tests above.
5. **Styled corpus:** 160/160 gates, 30 documentary cases, 17 device cases and the full Python
   suite.
6. **Retained phases 1–5 on release4**, including the real 60/120 s tiers. Release3 passes do
   not transfer.

## Remaining limits (not established by any evidence I saw)

- Full styled-cell equivalence of the malformed-OSC final frame, arbitrary PTY read splits and
  scheduler permutations.
- Profile and device input-first fairness.
- macOS/BSD termios, `open`, the RLIMIT clamp and the non-atomic CLOEXEC fallback.
- A Windows compile and crossterm parity.
- arm64 numerics.
- TLS ALPN/HTTP2 and live SQL.
- Six-platform packaging, installers/checksums, licensing notices, upgrade and Go-readable
  rollback.
- Measurement under the separately reviewed collector and a quiet window. This review
  authorizes no campaign.
