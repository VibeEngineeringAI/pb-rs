# Phase 7 final phase-wide source review, round 1 (independent, read-only)

- Worktree: `feat/phase7-interactive`, uncommitted state as of 2026-10-14. Base `4a812d5`, Go
  oracle `07af390`.
- Go was read directly in this worktree (`cmd/`, `pkg/`, `go.mod`) and in the module cache:
  the go1.25.4 toolchain (`net/url/url.go`, `encoding/json/{decode,fold}.go`), reflow v0.3.0
  and go-runewidth v0.0.16. I assumed, without checking, that the worktree's Go tree equals the
  pin. `docs/phase7-admin-saved.md` reports `git diff 07af390 -- cmd pkg main.go` as empty; I
  could not run git.
- Method: static reading with file tools only. The agent shell failed with EDQUOT on every call.
  I ran **no builds, tests, timings or PTY sessions**, and I edited nothing except this file.
- Evidence labels used below:
  - **[source]**: I read it in the current files.
  - **[reported]**: the parent's evidence (release1 runs, unit6/unit7, the 654-case helper
    probe, DST and lowering oracles). I did not re-run it.
  - **[unknown]**: a platform or runtime behavior that nobody has established.
- This is a source verdict only. It is **not** release readiness. Nothing here verifies the
  process gates on the latest source; the release2 candidate is not built.

## Verdict

**No high-severity defect was found in source.**

- **F1 (medium): a decision is needed before acceptance.** Go's `encoding/json` reuses
  slices, maps and structs when a key repeats. That reuse was ported for the PromQL result
  decoder (G3), but not for the three sibling decoders in the same flows. G3 was treated as a
  visible gap that must not be waived. For the same reason, F1 needs a fix or an explicit
  ledger decision. It must not stay silent.
- **F2 and F3 (low):** both need a small fix or a ledger line.
- **F4:** test-only.
- **L-SQL-SIDEBAR** (owner-reported, under fix): still open. See "Integration update".
- The rest are information items or must-run gaps.

The prior S1–S6, N1–N8 and R2-1/R2-2 resolutions hold where I re-read them. The new shared
URL joiner is byte-correct for every case phase 7 can reach.

## Prior dispositions re-checked in source

| Item | Status | Where |
|---|---|---|
| S1: per-effect auth scope | **Holds [source]** | `api.rs:391-402` (`effect_scope` seeds `initial_profile` and a fresh `refresh_gate`/metrics transport). SQL scopes are at `sql_service.rs:96,119,176`. PromQL scopes are at `promql_service.rs:148,163,237`. |
| S2: saved apply keeps the preflight profile | **Holds [source]** | `lib.rs:232-247` and `:280-291` |
| R2-1 (lifecycle): `Panicked` ordering claim | **Docs corrected [source]** | `terminal.rs:25-28` now states the peek-time stamp and Go's `select` freedom |
| R2-2 (lifecycle): SIGQUIT through raw restore | **Holds [source]** | `restore_now` restores INT/TERM/WINCH first (`terminal.rs:506`, `restore_controls` `:1061-1064`). It restores termios at `:534-538` and only then SIGQUIT (`:540`, `restore_quit` `:1068-1070`). The test hook `RAISE_QUIT_BEFORE_RAW_RESTORE` (`:529-533`) and its test (~`:2400-2418`) assert exit 2, `SIGQUIT: quit` and that raw mode is left set. The panic-hook path reuses `restore_now`. |
| N1-P2: PromQL local recover | **Holds [source]** | `promql.rs:5995-6014`, using `terminal::recovered` (`terminal.rs:554-574`). This matches Go's `promql.go` recover cited by the owner. |
| SQL `execute` without `process::exit` | **Holds [source]** | `tui/sql.rs:61-140` returns `Result<i32, CliError>`. `Ok(2)` is the unrecovered `NewQueryModel` panic, after stderr has been written (`:88-92`). The caller maps it to `Outcome.exit_code` (`sql/mod.rs:97-105`). The only remaining direct exits are deliberate: `on_quit`'s `_exit(2)` (`terminal.rs:1046-1053`) and the SIGPIPE re-raise (`:1384-1391`). |
| R2-3 (saved read error) | Not re-read in this round | — |

The entry ordering matches Go:

- **SQL** (`cmd/query.go:73-128`): flags, query rewrite, then `--from`, then `--to`, then the
  `NewQueryModel` panic before any terminal is opened. Rust is `sql.rs:66-92`.
- **PromQL** (`cmd/promql.go:290-316`): the output-format check, then `--to`, then the
  interactive `--from` (default `10m` when the flag is unchanged). Rust is
  `metrics/mod.rs:209-224`. That check runs before the interactive branch, as Go's does.

## Shared boundary: `api::append_url_bytes` / `append_url_parts` (`api.rs:961-1207`)

I compared it against go1.25.4 `URL.JoinPath`, `setPath` and `EscapedPath`
(`net/url/url.go:1262-1281`):

- **Element bytes are joined raw.** They are cleaned with `path.Join` semantics on the escaped
  form, as Go cleans `EscapedPath()`.
- **Escaping follows Go:**
  - When every byte passes `validEncoding(encodePath)`, the path is emitted verbatim. The
    allowed set at `:1120-1145` equals Go's: unreserved, `$&+,/:;=@`, `!'()*`, `[]` and `%`.
  - Otherwise the path is unescaped and re-escaped per byte with `shouldEscape(encodePath)`
    (`:1168-1194`).
  - I checked mixed cases by hand, and they agree: `a%2Fb`, `a b`, `a%41 b`, invalid UTF-8,
    `..%2F`, `?` and `#` inside an element.
- **A bad `%` escape introduced by an element** keeps the original URL (`:1086-1095`). This
  matches JoinPath ignoring `setPath`'s error.
- **No placeholder or `char` substitution remains.** `Vec<u8>` flows from `Effect::FetchSchema`
  (`sql.rs:417`) through `schema_endpoint` (`sql_service.rs:242-244`).

**Verdict: correct for every phase-7 caller.** Residuals of the shared policy (info, pre-existing
string-join behavior; I could not diff the previous version):

- **U1, empty last element.** Rust sets `trailing` from `combined.ends_with(b"/")` (`:1096`).
  Go tests `HasSuffix(elem[last], "/")` (`url.go:1275`).
  - Trigger: `JoinPath("http://h/a", "")`. Go gives `http://h/a`; Rust gives `http://h/a/`.
  - No phase-7 caller passes an empty last element:
    - schema always ends in `"schema"`, and an empty stream returns early at
      `sql_service.rs:116`;
    - the metrics, builder and device paths are literals.
  - Add one URL-oracle row and ledger it, or fix it as
    `paths.last().map_or(original_ends_with_slash, |p| p.ends_with(b"/"))` after checking the
    callers in phases 1–5.
- **U2, verbatim returns.** On the opaque-path (`:990`) and bad-element-escape (`:1094`) paths
  Rust returns `base` verbatim. Go returns `u.String()`, which lowercases the scheme and
  re-escapes a non-canonical fragment or userinfo. Rust also cleans the raw base path rather
  than `EscapedPath()`. That differs only for a base path with invalid encoding that also
  contains escaped dot segments. Not reachable with sane profile URLs. Ledger only.
- **U3, a wrong comment.** `sql_service.rs:121-122` says that after a JoinPath error
  "NewRequest then fails". It does not: Go `http.NewRequest("GET", "")` succeeds, because
  `url.Parse("")` succeeds. Go then runs `AddAuthHeaders` and fails in `Do`. The Rust skip
  produces the same observable text through the fallback, since auth resolution and
  `unsupported protocol scheme ""` both come from the fallback's own preparation. Correct the
  comment; no behavior change.

## Findings

### F1 — Medium (consistency with the G3 disposition): duplicate-key slice/map/struct reuse is not ported in the SQL window, SQL schema or PromQL label-list decoders

- **Go [source]:** `encoding/json/decode.go`.
  - In `array()` (`:541-596`), a slice reuses existing elements: `v.Index(i)` is decoded in
    place, and `SetLen(i)` truncates but keeps hidden capacity that a later array re-exposes.
  - `null` into a `string` or struct element is a no-op.
  - In `object()` (`:648-650`), a non-nil map is merged into, not replaced.
  - The SQL window path uses `json.Decoder.Decode` (`query.go:2720`), which shares this code.
- **Rust [source]:**
  - `sql_service.rs:728-794` (`query_data`): `fields` and `records` are cleared and rebuilt, a
    null field becomes `""`, and each record is a fresh `Record`.
  - `sql_service.rs:894-923` (`arrow_names`): fresh names; a null or `{}` element becomes `""`
    and is filtered out.
  - `promql_service.rs:735-751` (`decode_label_list`): a fresh `Vec`; null becomes `""`.
- **Triggers** (a 200 body, with the Go result first):
  1. Window `{"fields":["a","b"],"records":[{"a":"1"}],"records":[{"b":"2"}]}`. Go's row has
     `a=1`, `b=2`. Rust's row has `a` missing (shown as `—`) and `b=2`.
  2. Window `{"fields":["a","b"],"fields":[null,"x"],"records":[]}`. Go's columns are `a, x`.
     Rust's are `"", x`.
  3. Schema GET `{"fields":[{"name":"a"}],"fields":[{}]}`. Go gets columns `[a]` and sends no
     fallback. Rust takes the fallback, so there is **an extra POST** and different columns.
  4. Builder or prefetch `{"status":"success","data":["a","b"],"data":[null]}`. Go gets
     `["a"]`; Rust gets `[""]`.
- **Reach:** a server or proxy that emits duplicate keys. That is the same reach the parent
  accepted as visible for G3 ("not waived by a divergence ledger").
- **Minimal scope:**
  - Reuse the `ReusedSlice` slots/len pattern (`promql_service.rs:404-439`) for `fields`,
    Arrow `fields` and label `data`.
  - Merge into the existing `Record` for `records[i]`. `null` sets the record to empty, the
    `[]` and `null` arrays clear the slots, and `[]` still yields an empty slice.
  - The decoders' first-error semantics are unchanged. A type error still fails the whole
    decode, so only null, absent and re-exposed elements differ.
  - Add one actual-Go control per trigger.
- **If the parent ledgers it instead:** the entry must name these three decoders and explain
  why they differ from G3. Silence is not acceptable.

### F2 — Low (native-only trigger): the SQL window fetch does not reproduce Go's local `recover`

- **Go [source]:** `NewSQLWindowFetchTask` (`query.go:2603-2633`) defers a `recover()`
  (`:2612-2617`) that turns any panic into `res.errMsg = "query failed"` and still sends the
  message. The program continues and shows the window failure.
  - `fetchStreamSchema` (`:3041-3093`) has no recover, so its panics take Bubble Tea's
    command-goroutine kill path.
- **Rust [source]:** `dispatch` spawns `fetch_window` without `terminal::recovered`
  (`sql.rs:359-364`). A native panic reaches the hook, which restores and kills the session,
  so the user gets `program was killed: context canceled` and exit 1. PromQL modeled its
  equivalent recover; SQL did not.
  - `docs/phase7-sql.md` L-SQL-3 states the kill outcome for every command panic. That is
    accurate for schema and datasets, but not for windows.
- **Trigger:** native-only. I found no Go-reachable panic in `injectSQLWindow` or
  `fetchDataRaw`; the byte scanners are bounds-checked.
- **Minimal fix (about 10 lines):** wrap `service.fetch_window(request.clone())` in
  `recovered`, inside an inner `JoinSet` as PromQL does. Map `is_panic` to
  `WindowCompletion { run_id, window_index, result: Err("query failed") }`.
  - Alternatively, ledger it beside lifecycle I-1 as a native choice and correct L-SQL-3.

### F3 — Low: JSON field names fold ASCII-only in the SQL/PromQL decoders; Go uses `SimpleFold`

- **Go [source]:** `encoding/json/fold.go:22-50` folds through `unicode.SimpleFold`. A key
  containing U+017F `ſ` matches `s`, and U+212A `K` matches `k`.
  - The affected fields are `status`, `fields`, `records`, `result`, `resultType` and
    `values`.
- **Rust [source]:** ASCII-only matching:
  - `to_ascii_lowercase` at `promql_service.rs:566,603,650,732`;
  - `eq_ignore_ascii_case` at `sql_service.rs:805-809,900,912`.
- **Trigger:** `{"ſtatus":"error","error":"x","errorType":"bad_data"}`. Go reports
  `bad_data: x`. Rust ignores the key and parses the body as a success.
- L-AUTH-7 already ledgers this for the auth decoders only.
- **Minimal fix:** for these ASCII field names, map U+017F to `s` and U+212A to `k`, then
  compare ASCII case-insensitively. No other non-ASCII rune folds to ASCII. Alternatively,
  extend L-AUTH-7 explicitly to the SQL and PromQL decoders.

### F4 — Test-only: the hang-up test can still flake on timing

- **Production [source]:** the reader in `terminal.rs:1427-1466` ends silently on `read == 0`.
  It sends `error reading input: read /dev/stdin: <errno>` once on any other error, after
  retrying EINTR and EAGAIN. This matches Bubble Tea v1.2.2 `readLoop`, which ignores EOF and
  `ErrCanceled` and sends anything else to `p.errs`.
- **The EIO/EOF split looks like a genuine Linux race** [kernel knowledge, not executed].
  When the master closes, `pty_close` sets `TTY_OTHER_CLOSED`, so `n_tty_read` returns `-EIO`
  until `tty_vhangup` installs the hung-up fops, which return 0.
  - Go's `cancelreader` reads the same fd and is exposed to the same race.
  - **I find no production defect in the reported unit7 failure** (`InputError` EIO where the
    old test expected EOF).
- **The renamed test is correct in principle:** `hangup_surfaces_the_kernel_outcome_like_go`
  (`:2420-2461`) accepts both outcomes.
- **Remaining timing gap:** under load, the 200 ms first window can expire before the reader
  observes the hang-up. A late EIO would then land in the 50 ms "nothing further" window and
  fail the test. Deterministic options:
  - wait until the input producer has ended (for example, poll a producer-count or `Closed`
    predicate), or
  - lengthen the first window and drop the second.
- Do not waive it. Repeat it on release2 (see gap 3).

## Information and ledger items (no code change required)

- **I1, profile resolved twice.** Interactive SQL (`sql.rs:93-96`) and PromQL
  (`promql.rs:5620-5624`) resolve the profile again after the `lib.rs` preflight. Go uses
  `PreRunE`'s `DefaultProfile`. The gap is microseconds with no user interaction, and the
  non-interactive `sql run` already has the same pattern, accepted in service R2. Passing the
  preflight profile, as saved apply does, would be uniform, but it is optional.
- **I2, stale docs.**
  - `docs/phase7-sql.md` §5 says the reflow, merge, DST and L-SQL-4 work is "unbuilt
    (unit4)".
  - `docs/phase7-promql.md` says the G-fixes are "not yet executed".
  - The parent reports unit6 (408 pass) and clippy7 clean.
  - Refresh both with the release2 results. Until then, nothing in them is acceptance evidence.
- **I3, open dispositions** that must be decided before acceptance (not reviewed as fixed):
  - G9 / L-PROMQL-1: builder byte truncation. Rust shows U+FFFD where Go writes invalid
    UTF-8.
  - L-STYLE-1: a C1 ST inside a rune.
  - Admin/saved: config read failure between the preflight and the menu re-read, where Go
    can panic (`docs/phase7-admin-saved.md`, "Pending ledger boundaries").
  - SQL invalid-byte display: proven only by a pure-observer control, not by an actual
    process. This is now **superseded by L-SQL-SIDEBAR** (see "Integration update"): actual
    Go emits `C2 8B` there, so the observer control does not settle this site.
  - Go's `reflow.truncate` itself re-encodes invalid UTF-8 as U+FFFD. Its
    `range string(b)` loop and `[]byte(string(c))` write (`truncate.go:82-103`) support
    equality on the reflow-clipped SQL sites. They do **not** cover the `ui.Truncate` and
    `x/ansi` paths.
- **I4, licensing.**
  - `src/LICENSE-Go.txt` exists [source]. It was not in the session-start untracked list, so
    confirm that it is added and shipped.
  - The reflow MIT notice lives only in a source comment (`docs/phase7-sql.md` §4a). Confirm
    that it is also listed in the release notices file, alongside
    `docs/evidence/phase7/style-notices.txt`.
  - Ported Go algorithms are scoped to helpers: `go_sort`, `go_format_unix`,
    `go_f64_to_i64`, `parse_hex_float`/`atof_hex` (`promql.rs:5403-5538`, BSD header present),
    and the reflow port.
- **I5, ownership and design (accepted).** Owned reducer state, explicit `Effect` values,
  `JoinSet`-owned tasks, bounded completion channels and one terminal owner are native Rust.
  There is no goroutine/channel emulation beyond the stamped-head merge.
  - That merge's exactness depends on the current-thread runtime. Keep it documented, as
    lifecycle R2 asked.
  - Deadlines are per call site: 50 s, 15 s, 120 s on the metrics transport, 30 s on the
    standard transport, and 30 s plus a context deadline for device.
  - Cancellation is per caller: builder watch cancels and the device `PollTask` abort.
    Results have no new stale guards.
  - Persistence is only the CAS-guarded token update (`api.rs:796-803`).
  - No new flags or features were found in the dispatch I read: `lib.rs`, `metrics/mod.rs`
    and `sql/mod.rs`.
- **I6, Go-scheduler races kept deterministic.** These are ledgered races, not defects:
  L-AUTH-2, L-AUTH-9, L-AUTH-12 (the random `select`), the C4 full-channel stamp, and SIGQUIT
  printing only the first line of the runtime dump.

## Integration update (parent relay, 2026-10-14)

**Reported, not verified:**

- 516 all-target tests pass, including 37 ordinary effects cases.
- The diagnostic library build passes 410 tests, with 1 ignored.
- The parent's Go JoinPath edge cases pass.
- The source URL and wire raw bytes for the schema path are already correct, as reviewed above.

None of this is release2 process evidence.

**L-SQL-SIDEBAR: open, pending fix and replay.** This is **not** an accepted exception.

- **What actual Go writes:** the raw sidebar bytes for the mid-rune dataset are
  `B5 C2 8B E8 AF 95`. That is `0xB5`, then U+008B encoded as `C2 8B`, then `试`.
- **What Rust writes:** two U+FFFD replacement characters, then `试`.
- **Why it matters:** the visible cell and layout contract is still being resolved. U+008B is a
  C1 control, and how a terminal shows it differs from U+FFFD.
- **Supersedes I3:** the pure-observer U+FFFD equality control does not settle this site.
- **Possible mechanism:** the pattern looks consistent with stray-byte re-encoding through
  `WriteRune` on a Go text path, the same mechanism as L-STYLE-1.
- **Not reviewed here:** the SQL and effects owners are implementing the fix. I have not
  reviewed that change, and this review does not duplicate it.
- **Constraint:** if the fix needs the shared `style` boundary rather than only `sql.rs`, that
  shared change must be announced.
- **To close it:**
  - review the final `sql.rs` diff;
  - check the raw sidebar bytes against actual Go;
  - replay the styled case (cells, cursor, layout), plus the schema and wire request bytes.
  - It joins gaps 1, 4 and 5 below.

**Out of scope here:** the helper review's H1 (PID reap race), M1 (cleanup), M2 (exec error)
and M3 (signal inheritance) are harness findings, and the harness owner is fixing them. They
gate the timed campaign and harness-dependent evidence, but they are not production-source
findings. This verdict covers the remaining production contracts and lifecycle, with the
`sql.rs` sidebar change still open.

## Must-run acceptance gaps (the parent runs these; none were run here)

1. **Build release2** from the final source, including the pending `terminal.rs`
   characterization diff. Record the source and artifact hashes. Every item below is against
   release2.
2. **Central unit tests** (single-threaded) and all-target Clippy with warnings denied.
3. **The hang-up test, repeated** (for example, at least 200 iterations, with load). Classify
   EOF and EIO counts. Add the matching Go PTY hang-up control and record its outcome set for
   both binaries.
4. **Styled corpus:** 190 comparisons (160 gates and 30 documentary). This covers:
   - the two corrected SQL CJK cases;
   - the six new PromQL Go controls per theme;
   - Rust replays of G3, G4, G7 and G10;
   - `device-sigint` (L-AUTH-4).
5. **Process suites:**
   - effects: 34 ordinary, 4 OAuth and 4 genuine-deadline cases;
   - device: 17 cases;
   - the 36 admin/saved Go PTY cases replayed against Rust;
   - picker and credential faults (L-AUTH-11).
6. **Pending numeric oracle:** `promql-numeric-oracle-result.txt` for `go_format_unix`,
   `go_f64_to_i64` and `go_fixed1`. Also revalidate the helper-probe extraction hashes
   against the final `go_sort`/`atof_hex` source.
7. **URL oracle rows for U1 and U2:** an empty last element, a bad-escape element, an
   uppercase scheme with an opaque or bad-escape base, and a fragment or userinfo base.
   Confirm the 654-case result still applies to the final `append_url_parts`.
8. **F1 controls** (if fixed): four actual-Go bodies, with Rust unit and process checks.
   **F2/F3** (if fixed): one unit test each.
9. **Retained phase 1–6 gates.** Run phase1 (2,018 cases plus guards), phase2 (219) and the
   phase 3–5 suites, because `api.rs` (the shared joiner) and the `lib.rs` dispatch changed.
10. **Lifecycle R2 remaining items 4, 6, 7, 8 and 10:**
    - a per-workflow background panic;
    - thread-panic injection;
    - a stalled-writer panic;
    - multi-session login→device SIGQUIT and pipe drain;
    - a compile-only `cargo check --target x86_64-pc-windows-*`.
11. **Measurement campaign,** only after an explicit quiet window with phase 6 and the open
    measurement-review fixes (`docs/phase7-review-measurement-r1.md`). Not part of this
    source verdict.

## Residual platform and runtime unknowns (not established by any evidence)

- **macOS and the BSDs:**
  - `pipe` + `fcntl` has a non-atomic CLOEXEC window (`terminal.rs:1126-1149`);
  - Apple RLIMIT clamp and child restoration;
  - `open` browser launch;
  - termios behavior with `/dev/tty`.
- **Windows:** the crossterm reader is uncharacterized against Bubble Tea's `coninput`
  (`terminal.rs:1502-1504`); there is no compile run yet.
- **arm64:**
  - `go_f64_to_i64` takes the saturating path, which is uncharacterized against Go FCVTZS;
  - a possible FMA 1-ulp HSLuv tie (`docs/phase7-style.md`).
- **Time zones outside chrono's representable range** (G7); tzdata differences across hosts.
- **Live servers:** TLS ALPN/HTTP2 and live-server behavior. Loopback HTTP/1.1 fixtures do not
  distinguish the transports.
- **Terminal emulators other than the PTY observer:** their rendering of U+FFFD-per-byte and
  of ambiguous-width glyphs, and the effect of locale (`RUNEWIDTH_EASTASIAN`, CJK `LANG`) on
  bubbles widths.
- **Six-platform packaging,** installers and checksums, Go-readable rollback. This is a
  separate release gate.
