# Phase 7 final phase-wide production review — independent Opus 5.5, round 3

- Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`, 2026-10-14.
  Base `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`, Go pin `07af390ab09bda8ac9e427ffc6310077bace0125`.
- Scope: production contracts and lifecycle across integration and shared dispatch
  (`lib.rs`, `sql/mod.rs`, `saved_queries.rs`, `admin.rs`, `api.rs`, `process.rs`),
  auth/profile/admin/saved workflows, SQL/PromQL states and services, byte-render
  boundaries and per-flow effects. Moving collector, bench and profile drivers, tests and docs
  are excluded, and nothing in them is used for acceptance.
- This is a new independent round. The Sol R3 report
  (`docs/phase7-review-final-contract-sol-r3.md`) arrived after my own source pass. I read it
  as input and re-checked its claims in source myself. Where I agree, I say why. Where I
  found it first or found something it did not, I say so.

## Method and limits

- **The shell was unavailable for the whole round.** Every Bash call failed with EDQUOT in
  the harness temp directory, `/tmp/claude-1000/...`, before any command ran. The stipulated
  `TMPDIR`/`CLAUDE_CODE_TMPDIR` setting cannot redirect the harness's own output area. As a
  result:
  - I ran **no** builds, tests, CLI/PTY sessions, network requests, timings or profilers;
  - I computed **no** hashes, so I **did not independently verify source hashes before or
    after**;
  - I wrote **no** disk-cache evidence;
  - I used only the file-read tool.
- The only worktree write is this file.
- I read the Go sources directly from the worktree (`cmd/`, `pkg/`, `main.go`, `go.mod`)
  and from the module cache:
  - bubbles v0.20.0 `list/list.go`;
  - sahilm/fuzzy v0.1.1;
  - go1.25.4 `syscall/rlimit.go`.
- Pin equality of the worktree Go tree is **supplied by Sol R3**. Sol reports 87 files equal
  to their git objects. I did not verify this.
- Evidence labels:
  - **[source]**: my own reading of the current files plus causal reasoning. Execution is not
    implied.
  - **[supplied]**: owner or peer execution records and manifests that I read but did not
    reproduce.
  - **[unknown]**: not established by adequate source or runtime evidence.
- Read order:
  1. `rust-phase7.md` first. Older checkpoints were treated as historical.
  2. Final R1 in full.
  3. Lifecycle R1 (the verdict and N1–N3 sections) and R2 in full.
  4. Service R1 (the header and S1) and R2 in full.
  5. The resolution log.
  6. Byte interim Sol R1 and SQL-identity Sol R2.
  7. `phase7-admin-saved.md`.
  8. The release3 build, central-checkpoint and focused-styled records.

## Bounded source verdict

**Changes requested. There is one medium finding, independently confirmed (R3-F1), and two
new low findings (O3-1, O3-2).** I found no high-severity production defect in the surface I
read.

The following hold in source where I re-read them:

- final R1 F1–F4;
- the interim byte/identity F1;
- the saved reread correction;
- S1–S6;
- the N1–N8 and R2 lifecycle boundaries;
- the per-flow cancellation and persistence contracts.

This is a source verdict only. Linux loopback tests do not establish any of the following:

- macOS, BSD or Windows behavior;
- six-platform packaging;
- live SQL or TLS/HTTP2;
- installer, rollback or licensing readiness.

## Findings

### R3-F1 — Medium (confirmed independently; first identified by Sol R3): the schema fallback `endTime` is sampled before the primary GET

**[source]**

- **Rust:** `src/tui/sql.rs:407-414`, the `Effect::FetchSchema` dispatch, evaluates
  `format_rfc3339_utc(Utc::now())` when the task starts. It passes the String into
  `SqlSessionService::fetch_schema`. That function awaits the schema GET and its full body
  (`sql_service.rs:124-146`), then uses the stale String as the fallback `endTime`
  (`:155-160`).
- **Go:** `pkg/model/query.go:3084` computes `time.Now().UTC().Format(time.RFC3339)` *after*
  the GET, body read, status check and decode have failed.
- **Trigger:** columns drawer with a schema GET that takes at least one UTC-second boundary,
  then answers 404, an empty or unusable body, or a transport error (anything that is not a
  quick refusal). Rust's fallback POST body then carries an earlier `endTime`.
  - The wire request differs.
  - Rows newer than the Rust bound can make Go return columns where Rust returns none. That
    downstream effect is inferred, not reproduced.
  - The GET can last up to its 15 s deadline, so the gap can approach 15 s.
- **Reach:** an ordinary slow or overloaded server. No exotic input is needed.
- **Why the existing tests miss it:** `sql_schema_fallback` answers the GET immediately and
  bounds the timestamp loosely (the Sol citation is `tests/interactive_effects.rs:777-847`;
  I read it there).
- **I missed this in my own pass.** I had read both sites, but I did not connect where the
  clock is sampled to Go's line 3084. I credit Sol R3.
- **Severity:** Medium, because it is a reachable, unledgered request-content mismatch on a
  normal path. It is not high: auth scope, deadlines, ordering and identity are all correct.
- **Narrow fix:** sample the clock inside `fetch_schema`, immediately before building the
  fallback. Remove the `now_utc` parameter, or pass a clock. Keep:
  - one effect scope for both requests;
  - the separate 15 s deadline for each request;
  - unguarded completion.
- **Verification:** use a held-GET oracle with Go and the successor binary. The GET is
  released after a known later second, and the fallback `endTime` must be at or after the
  release second. Add a fast-success control that sends no fallback.

### O3-1 — Low (new): the credential form ignores Alt+Enter on a focused Submit; Go submits through `button.Pressed`

- **Go [source]:**
  - `pkg/model/credential/credential.go:117-162` switches on `msg.String()`, so `"alt+enter"`
    is not intercepted.
  - The key falls through to `updateInputs`. That calls `button.Update`
    (`pkg/model/button/button.go:68-87`), which dispatches on `msg.Type == tea.KeyEnter` and
    ignores Alt. When the button is focused and `!Invalid`, it emits `Pressed(true)`.
  - `credential.go:111-114` then quits when `validInputs` is currently true.
- **Rust [source]:**
  - `src/tui/profile.rs:846-887` matches only `name == "enter"`.
  - `"alt+enter"` reaches the input loop, where no input is focused while focus is 2. It
    returns `false`.
  - `Key::name()` produces `alt+enter` for `ESC CR` (`input.rs:192-213`).
- **Trigger:** `pb profile add NAME URL` (fewer than 4 args, no `--api-key`, per
  `cmd/profile.go:146-160`), then:
  1. type a valid user and password;
  2. press Tab twice to reach Submit;
  3. press Alt+Enter (on terminals that send `ESC CR`).
- **Result:** Go exits and saves the profile with those credentials. Rust stays in the form.
- **Persistence is not at risk.** A later Esc or Ctrl+C in Rust saves the same `Values()`, and
  plain Enter submits. The difference is a visible keystroke outcome.
- **Distinct from Go's own race:** the role form already models KeyType dispatch for
  Alt+Enter (`tui/admin.rs:186-207`). That shows this is a per-widget dispatch detail, not a
  scheduler race.
- **Minimal fix:** in the non-intercepted path, when `focus == 2`, `key.code == Enter`,
  `key.alt` is set and the latch is clear, quit only if `valid()` is currently true. The
  button's latch check and the Pressed handler's current-validity check are both required.
  Add one reducer test, plus one Go PTY control if one is cheap to add.

### O3-2 — Low (new detail on a documented gap): the saved-menu fuzzy matcher diverges from sahilm/fuzzy on case folding and camel-case classification

- **Go [source]:** bubbles `list.DefaultFilter` (`list/list.go:88-99`) calls `fuzzy.Find`.
  - `equalFold` walks the `unicode.SimpleFold` orbit (`fuzzy.go:208-231`).
  - The camel bonus uses `unicode.IsLower` and `unicode.IsUpper`, which test category Ll/Lu
    (`:141`).
- **Rust [source]:**
  - `src/tui/saved.rs:573-597` folds through single-rune `to_lowercase` plus a short table.
  - `:536` uses `char::is_lowercase` and `is_uppercase`, which test the derived
    Lowercase/Uppercase properties. Those properties include Other_Lowercase and
    Other_Uppercase.
- **Triggers** (titles from the saved-query server):
  - *Membership.* U+1E9B `ẛ` is in the orbit of `ṡ` (U+1E61), and U+1C80 `ᲀ` is in the orbit
    of `в`. Go matches each pair; Rust's `fold` leaves both sides unchanged and does not.
  - *Ranking only.* A prior `ª` (U+00AA, category Lo but Other_Lowercase) followed by an
    uppercase match gets Rust's +20 camel bonus but not Go's. The reverse applies to `Ⓐ`/`Ⅰ`
    as "uppercase".
- **Reach:** exotic titles only. `phase7-admin-saved.md:35` already says "obscure Unicode
  folding need[s] additional cases". This review names the concrete mechanisms.
- **Fix or ledger:** either use Go's orbit rule (smallest-rune fold, as `json.rs` already does
  for its two ASCII orbits, generalized) and category Ll/Lu, or record an explicit ledger
  entry for these two rules.
- **Checked against Go:**
  - Equal-score ordering. `fuzzy.Find` and `DefaultFilter` each run `sort.Stable` with a
    non-strict `Less(>=)`, so each pass reverses ties. Two passes restore the original
    order, which matches Rust's stable sort at any list size. I found no defect.
  - Asynchronous Go filtering against Rust's synchronous `refilter`. Rust's outcome is one of
    the outcomes Go's scheduler allows. This is a Go race, not a defect.

## Information items (no change required unless noted)

- **I-a: the reader read-ahead is overstated in comments.**
  - `terminal.rs:10-12` and `:117-118` say the reader "reads nothing further until it is
    taken".
  - In fact `push_input` (`:175-192`) blocks only while the *previous* message occupies the
    lane. After it places message N, the reader loop (`:1476-1485`) can `read()` the next
    chunk and block holding N+1.
  - Message ordering and stamps still match Go's send queue. The only difference is that
    kernel input bytes are consumed one message earlier. That matters only for type-ahead at
    exit or across the login→device session boundary, and Go already has a comparable racy
    window between receive and cancel.
  - Fix the comment. No behavior change is needed.
- **I-b: the builder-cancel comment is imprecise.**
  - `promql_service.rs:263-269` says "a response that completed first is still delivered".
  - Go (`promql.go:2646-2650`) also suppresses an HTTP-error result whenever
    `ctx.Err() != nil` at check time, even if the response completed.
  - Rust delivers such an error if the request wins the biased select. That only happens on
    simultaneous readiness, which is a race-level difference. Correct the comment.
- **I-c: input-first fairness (Sol's validation question).**
  - I confirm the source precondition:
    - `profile.rs:107/204-220`, `saved.rs:29-54` and `device.rs:612-632` poll input first on
      a current-thread runtime;
    - unlike SQL (`sql.rs:229`), they have no per-message `yield_now`.
  - The reader offers one message at a time, and the parser coalesces runes per read. That
    makes a continuously ready lane improbable at human input rates.
  - **[unknown]** No PTY characterization exists. I do not promote this to a finding.
- **I-d: the profile is re-resolved before the admin picker.**
  - `admin.rs:53-57` re-reads the config before `select_dataset_kind` and `select_privilege`.
  - Go reads `DefaultProfile` from PreRun (`cmd/dataset.go:143-153`, `cmd/role.go:85`).
  - This is the same microsecond class as final R1's I1, and is pre-existing. Ledger only.
- **I-e: prior I1 still applies.** Interactive SQL (`sql.rs:93-96`) and PromQL
  (`promql.rs:5635-5640`) re-resolve the profile after preflight. Saved apply correctly reuses
  the preflight profile (`lib.rs:232-291`).

## Prior dispositions re-checked [source]

| Item | Status | Where |
|---|---|---|
| Final F1, decoder reuse (≠ interim identity F1) | Holds | `tui/json.rs:31-71`; SQL window `sql_service.rs:730-821`; Arrow `:915-955`; labels `promql_service.rs:694-740`. I hand-checked the slot/hidden-capacity model against Go `array()` Grow/SetLen: hidden slots exist only below `cap`, Grow happens only at `i == cap`, so no decoded slot is lost. |
| Final F2, window-local recover | Holds | `sql.rs:361-405`. Panic maps to the same run and window with `"query failed"`, matching `query.go:2612-2617`. Schema and datasets keep the kill path. |
| Final F3, SimpleFold for field names | Holds | `json.rs:9-24` matches Go `foldName`'s smallest-orbit rule for ſ→S and K→K. Auth's ASCII rule stays the separately ledgered L-AUTH-7 (`device.rs:110-112`). |
| Final F4, hang-up test | Holds | `terminal.rs:2481-2529` waits for the reader thread to finish (5 s bound), then accepts EOF or EIO. |
| Interim byte F1, raw identity | Holds | `sql.rs:2608-2617`, `:2726`, `:3194`, `:3245`, against `query.go:495-507`, `:884-890`, `:944-952`. Placeholder resolution uses a per-byte replacement string that is equivalent after Go `json.Marshal`. Plan building is ASCII/byte-structural, so raw versus replacement bytes do not change decisions. |
| Saved reread and menu policy | Holds | `saved_queries.rs:81-139` against `savedQueries.go:276-344`: prints the diagnostic, uses an empty config and zero profile, the stage-specific stderr messages, ignores the status code, and fetches before acquiring the terminal. |
| Saved key interception | Holds | `saved.rs:255-298` against `savedQueries.go:204-235` and bubbles `handleBrowsing`/`handleFiltering`: a/Enter/b/Ctrl+C come first; q/Esc quit only when not filtering; Esc clears an applied filter; accept is gated on a non-empty value; the paging keys omit `b`. |
| Saved apply | Holds | `lib.rs:280-300`, `saved_queries.rs:48-64`, `sql/mod.rs:141-193` against `queryList.go:88-155` and `query.go:708-750`: `Query:` prints before the wire LIMIT, missing ranges are literal `1h`/`now`, the preflight profile is used, the error prefix is `selected saved query failed:`, and `trim_sql_space` equals the Go `TrimSpace` set. |
| S1 per-effect scope / S2 | Holds | `api.rs:387-402`; scopes at `sql_service.rs:97,120,177` and `promql_service.rs:149,164,238`. |
| Browser FD restore (S5) | Holds | `process.rs:12-91` against go1.25.4 `rlimit.go:30-47`: the condition `Cur < Max-1`, setting `Max-1`, storing the original before setrlimit, and the Linux external-change skip. Apple clamp unvalidated. |
| Device poll ownership | Holds | `device.rs:506-547` and `:574-682`: abort on Ctrl+C, abort and join in `finish`, abort on Drop; a panic takes the kill path. |
| Dataset/role cancellation | Holds | `tui/admin.rs:20-78`, `admin.rs:64-85` against `dataset.go:143-223` and `role.go:97-109`: SIGINT/Esc/Ctrl+C give "Dataset creation canceled" with exit 0 and no PUT; the role path aborts after the GET preflight with no PUT; the role body is privilege-only. |
| Credential cancellation | Holds (except O3-1) | `tui/profile.rs:830-843` against `credential.go:48-65`: both values or none. The latch semantics match. |
| Lifecycle N1–N8, R2-1/R2-2 | Holds where re-read | Lanes and queue at `terminal.rs:115-353`; `Panicked` peek stamp documented at `:25-28`; SQL and PromQL merges at `sql.rs:196-324` and `promql.rs:5823-5905`. C4, current-thread dependence, I-1/I-3/I-5 and scoped SIGQUIT stay explicit limits. I make no universal ordering claim. |
| Byte render path | Holds in source | saved `clip` and `render_bytes` at `saved.rs:367-518` (matches Go `clip`, `savedQueries.go:118-126`); terminal `Renderer::frame` byte lines at `terminal.rs:418-497`. I did not re-audit style internals; I rely on the supplied 36,504 and 14,980 comparisons. |

## Evidence classification

**[supplied]** (read, not reproduced):

- `release3-build-manifest.json`: normal `aceb58bd…`; SQL source `a0f2adbe…`; source
  snapshot `3f36bb91…`.
- `release3-central-checkpoint.json`: 547 all-target passes with 8 ignored, 4 OAuth and
  4 deadline cases; `source_unchanged: true`.
- **Correction to Sol R3's wording:** the three focused byte controls also have **release3**
  evidence, `docs/evidence/phase7/release3-focused-styled.json`. I read it: Go and release3
  builder/saved records with zero cell differences and equal cursors in both actual-read and
  one-chunk modes.
- Sol R3's static hash audits.
- The parent's report that retained release3 phases 1–4 pass and phase 5 is active.

**Not covered by any evidence I saw:** R3-F1's clock position, O3-1, and O3-2's orbit and
property cases.

**[unknown]:**

- full cell equivalence for the malformed-OSC final frame;
- arbitrary PTY read splits;
- ordering under scheduler permutations;
- the input-first fairness reach (I-c).

## Must-run / outstanding

1. **R3-F1:** a narrow patch (once production is unfrozen and the change is coordinated), the
   held-GET Go/successor oracle plus a fast-success control, then a successor build with
   bound hashes.
2. **O3-1:** a fix plus a reducer test (and an optional Go PTY Alt+Enter control), or a
   ledger entry. **O3-2:** fix or ledger, with actual-Go controls for `ẛ`/`ṡ` and `ª`+upper.
3. Comment corrections I-a and I-b, which change no behavior. A characterization for I-c.
4. Finish retained release3 phase 5, or rerun the successor build in full: styled 160/160
   gates, the 49 effects cases, OAuth, deadlines, the 17 device cases and the Python suite,
   all on the post-fix candidate. Release3 passes do not transfer to new source.
5. Synchronize the narrow spinner wording in the phase7 contract doc. Do not add another
   comparator relaxation.
6. Platform and release gates, none of them established here:
   - macOS/BSD termios, `open`, and the RLIMIT clamp;
   - a Windows compile and crossterm parity;
   - arm64 numerics;
   - TLS ALPN/HTTP2 and live SQL;
   - six-platform packaging, installer/checksum, licensing notices, upgrade and Go-readable
     rollback.
7. A measurement campaign, only under the separately reviewed collector and an agreed quiet
   window. This review authorizes none.
