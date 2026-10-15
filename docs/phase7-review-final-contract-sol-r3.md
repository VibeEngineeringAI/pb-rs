# Phase 7 final contract audit — independent Sol round 3

Date: 2026-10-14. Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`.
Base: `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`.
Go pin: `07af390ab09bda8ac9e427ffc6310077bace0125`.

## Bounded verdict

**Changes requested for one newly identified medium source finding: the SQL schema
fallback snapshots its end time before the primary GET rather than after it.** No
new high finding was identified. The original final-round F1/F2/F3 corrections and
the separate interim byte-identity F1 correction hold in the inspected release3
source. F4's reader-completion synchronization is present. Those dispositions do
not close the new finding or establish whole-phase acceptance.

There is also an unexecuted, precisely scoped fairness concern in the inline,
saved and device drivers. Its enabling source condition is established below;
its practical PTY reach and duration are validation questions, not an observed
process failure from this review.

This is an independent adversarial production/evidence audit to assist the final
Opus review. It **does not substitute for that review**. Earlier Opus rate-limit
failures contain no completed review. I neither launched another agent nor edited
the peer's report. Moving collector/bench/profile drivers, tests and documentation
are excluded from acceptance, including their new fixes and preflight status.

## Method, identity and evidence classes

I read `rust-phase7.md` first, then the original final review in full, both
lifecycle rounds, both service rounds, the resolution log, both interim byte/SQL
identity reviews, relevant auth/admin-saved/SQL/PromQL/terminal/style/effects/shared
contract documents, and the immutable release3 records. Historical checkpoints
were treated as historical. I inspected the affected Rust ownership, service,
reducer and rendering paths and the actual pinned Go counterparts, including
cached Bubble Tea v1.2.2 and Go 1.25.4 JSON/URL source. This is not a claim to have
re-audited every editor/chart algorithm, parser sequence or platform adapter.

- **[source]** Direct source inspection and causal reasoning; no execution of the
  production function is implied.
- **[executed static]** My own git-object, SHA-256, JSON/log, stored-byte and frame
  comparisons. These checks import no harness and launch no CLI or compiler.
- **[supplied execution]** Owner-run tests/process captures and their recorded
  outcomes, even where I independently verified hashes or recounted results.
- **[unknown]** Claims not established by adequate source or runtime evidence.

I ran no build, Rust/Python test suite, CLI/PTY session, network request, benchmark,
timing or profiler. The only worktree write is this report. My unique disk evidence
is in `/home/ajs/.cache/pb-phase7/reviews/final-contract-sol-r3-20261014/`:
`source-before.json`, `go-pin-audit.json`, `audit.py`, `evidence-audit.json`, and
`source-after.json`. Commands used the stipulated disk-backed temp paths where
applicable. No commit, production/test/harness change or top-level thread was made.

**[executed static]** All 56 release3 compiled-source-map entries match disk, with
no additional Rust/spec/catalog source outside the map. The sorted-map hash is
`3f36bb91a9d82e996c001c74523fa494ecf1f821338b2a63552f10bcbb467c34`.
The normal binary is exactly
`aceb58bd412e8fbf34f603d247c68e87e69897356cefd5aad19ab6a92484db28`;
cloud and login binary hashes also match their manifest entries. SQL is exactly
`a0f2adbe60c95af41b9b04892310c4dc625485c3c958224006d603c7a4a337c2`.
The native helper source hash is
`0279d5fb06e4f38adc32004c152fc42c94194d59f234ee5a9c5694f74a6d31f4`.
Before/after identity checks cover the frozen source and artifacts, not the moving
collector files. All 87 pinned files under `cmd/`, `pkg/`, `main.go`, `go.mod` and
`go.sum` equal their actual git objects; there are no untracked files in that set.
Thus the Go assertions below do not rely on the earlier review's unverified pin
assumption. Build provenance remains supplied execution; hashing a binary does not
independently reproduce its build.

## R3-F1 — Medium: schema fallback uses the pre-GET end time

**[source] Where:** `src/tui/sql.rs:409-411` computes
`format_rfc3339_utc(Utc::now())` when the schema effect starts and passes the String
into `SqlSessionService::fetch_schema`. That service awaits the primary GET and
body (`src/tui/sql_service.rs:124-146`), then uses the old String as the fallback
query's `endTime` (`:155-160`). Pinned Go computes
`endTime := time.Now().UTC().Format(time.RFC3339)` **after** the GET/body/decode path
has failed, immediately before `fetchData` (`pkg/model/query.go:3081-3087`).

**Concrete trigger/reach:** open the columns drawer for a dataset whose schema GET
is delayed across at least two UTC seconds, then returns 404, an empty schema or an
otherwise unusable schema. An ordinary slow server suffices; no native panic,
malformed UTF-8, external config mutation or exotic scheduler is necessary.
For example, the effect begins at `12:00:00Z`, the unusable GET completes at
`12:00:02Z`, and fallback POST starts then:

| Request field | Pinned Go | Release3 source |
|---|---|---|
| `startTime` | `2000-01-01T00:00:00+00:00` | same |
| `endTime` | `12:00:02Z` | `12:00:00Z` |
| query/timeout/auth scope | one-row fallback, fresh 15-second request, same effect scope | same policies; older end boundary |

The request mismatch is established by source. A server that honors `endTime`
and whose first rows fall within that interval can consequently return columns
to Go and no columns to Rust. That downstream visible scenario is inferred, not
process-reproduced here. The primary delay can approach its 15-second deadline;
the fallback's separate 15-second request deadline is not itself the defect.

**Why existing evidence does not close it:** the current ordinary
`sql_schema_fallback` fixture answers the GET immediately
(`tests/interactive_effects.rs:777-778`). Its timestamp assertion requires only
that it be within `TIMEOUT` of the later assertion (`:846-847`; `TIMEOUT=12` at
`:172`), plus GET-before-POST ordering. It never holds the GET across an observable
clock boundary and compares `endTime` with that release. The earlier commentary's
20-second description was inaccurate; the actual allowance is 12 seconds. The
OAuth fallback and byte-identity captures establish credentials/request identity,
not this clock sampling position. Passing those tests cannot override the pinned
source mismatch. No prior ledger disposition found here authorizes this change.

**Requested narrow correction/verification:** sample UTC after the failed primary
attempt, at fallback construction. Keep the GET and fallback in one auth scope,
retain each request's 15-second deadline, and retain unguarded schema completion.
For the owner, a held unusable schema GET released after a known later second must
produce fallback `endTime` at/after that release second on both Go and the successor
candidate. A response chosen from that boundary can demonstrate the column effect.
Retain a successful-primary control with no fallback. This review changes no code
and supplies no new process pass.

## Additional validation question: input-first drivers can defer other work

**[source]** `profile::run_inline` uses a current-thread runtime (`profile.rs:67`),
and `first(event, first(timer, paste))` (`:107`) returns as soon as input is ready
(`:211-215`). `saved::next_driver_event` returns input before polling clipboard
tasks/ticks (`saved.rs:38-49`); `device::wait_screen` returns input before polling
the token JoinHandle, frame and browser futures (`device.rs:614-633`). None of
these three loops has SQL/PromQL's explicit per-message `yield_now`.
`terminal::next_event_stamped` can return synchronously from its native lane
(`terminal.rs:890-897`), without hitting a Tokio cooperative operation.

**Causal precondition/possible user effect:** keep the native input lane ready at
every loop poll with a stream of nonquitting ignored input, while a clipboard task
or device poll/browser deadline is pending. The input-first branch can repeatedly
return Ready; the block-on future need never return Pending, so current-thread
spawned tasks/I/O can be deferred. Even when a task already finished, the other
branches are not polled while the input head is ready. Device polling or the
five-second browser launch can therefore be postponed for the duration of that
condition; inline/saved paste and blink work have the corresponding concern.
This is distinct from C4's full completion channel and from a request to reproduce
every Go scheduling order.

Pinned Go runs these commands independently of input consumption
(`bubbletea@v1.2.2/tea.go:296-327`). Device Init batches spinner/browser/poll
(`cmd/cloud.go:140-145`), and the browser waits in its own command
(`cmd/cloud.go:304-307`). SQL (`sql.rs:229`) and PromQL (`promql.rs:5872`) already
explain and prevent the current-thread executor starvation condition locally.

**[unknown]** This round did not reproduce a real PTY maintaining that condition,
establish a minimum input rate, or show a failure under ordinary key repeat. No
loss/persistence/RAII defect is claimed from it, and it is not promoted to a second
demonstrated production finding. The owner should characterize sustained ignored
input plus one pending device/clipboard effect with causal readiness controls. A
fix, if warranted, needs scheduler progress **and** bounded delivery of already
ready non-input work; adding a yield alone does not remove input-first delivery
priority. No universal priority, cancellation or stale-result policy is requested.

## Prior findings and contract boundaries rechecked

**[source]** The original final F1 is decoder reuse; the interim byte F1 is SQL
identity. They are different defects and are resolved independently:

| Prior item | Current source assessment |
|---|---|
| Final F1: SQL window/schema and label-list reuse | `tui/json.rs:33-79` owns initialized slots and active length. SQL fields/records reuse slots, merge record maps and reset null records (`sql_service.rs:727-824`); Arrow struct slots preserve prior names under null/empty objects (`:916-953`); label-list `data` reuses String slots (`promql_service.rs:700-742`). Empty/null arrays discard hidden slots. Errors are retained rather than hidden by a later valid key. |
| Final F2: window local recover | `sql.rs:369-386` owns an inner JoinSet under `terminal::recovered` and maps panic to the same run/window's `query failed`. Dispatch uses it (`:396-405`). Schema/dataset tasks retain their command-panic boundary. This matches `query.go:2612-2617` and schema's absence of local recover. |
| Final F3: Unicode field folding | `json.rs:9-24` implements the non-ASCII SimpleFold orbits into ASCII; both adapters use it. Auth's separately documented ASCII-only boundary is not silently covered by this resolution. |
| Final F4: hang-up test timing | `terminal.rs:2489-2517` waits for the actual reader thread to finish before inspecting its one possible EIO/EOF outcome. The old short quiet-period assumption is gone. I inspected but did not run the test. |
| Interim byte F1: replacement alias | Borrowed identity at `sql.rs:2608`, owned effect snapshot at `:2615`, and comparisons at `:2726`, `:3194`, `:3245` preserve raw identity and the existing picker reset branches. The valid same-title no-fetch control is present. |
| Saved reread correction | `saved_queries.rs:88-103` prints the exact reread diagnostic and continues with an empty config/default profile; request diagnostics then lead to an empty menu. It matches pinned `savedQueries.go` and corrected non-nil-config semantics, not the historical nil-pointer assumption. |
| Lifecycle R2 read-error/final View | Saved checks the final View fault before returning an input error (`saved.rs:150-159`); profile uses `close_with_final_view` (`profile.rs:161-165`). SQL/device/PromQL retain graceful read-error restoration versus command-panic kill. |

The current source still preserves per-caller persistence and cancellation:
credential cancel goes through `Values()` (both valid values or two empties), then
saves (`tui/profile.rs:837-848`, `src/profile.rs:282-298`; pinned credential Model
`Values` at `:48`). Default-picker cancellation writes nothing; replacement-picker
cancellation still removes the original and clears the default (`profile.rs:428`,
`:541`). Cooked logout reads outside the terminal session (`tui/profile.rs:995`,
`:1040`). Dataset cancel prints `Dataset creation canceled` with success/no PUT;
role abort follows GET preflight and prints `aborted by user` without PUT
(`admin.rs:69-80`). These have not become a common cancellation policy.

Service S1/S2 hold: `api.rs:392-403` creates fresh auth/refresh/metrics state from
the immutable session-start profile per effect. Schema GET/fallback share a scope;
independent windows, metrics calls and both-mode halves do not. Refresh updates
that scope and CAS-persisted tokens (`api.rs:795-803`, `config.rs:258-279`), matching
the pinned profile pointer mutation in `pkg/http/refresh.go:126-132`. Saved apply
retains preflight credentials (`lib.rs:232-247`, `:283-285`), while menu fetch
separately resolves the persisted default and ignores HTTP status
(`saved_queries.rs:85-148`). Apply prints before wire LIMIT (`sql/mod.rs:153-154`),
uses literal missing ranges, and saved `a`/Enter/`b`/Ctrl+C interception precedes
filtering (`saved.rs:243-259`).

S3/S4/S6 retain explicit device URL/auth error order, current/next-hop deadline URL
before redirect-body drain (`api.rs:1273-1285`, `:1600-1605`), context-first tie
semantics and signed nanosecond wrapping. S5/browser child limits are scoped to
pre-exec (`process.rs:64-91`): Linux external limit changes suppress restoration,
and a restoration error does not prevent exec. These are source dispositions;
Linux device evidence does not prove the Apple clamp.

SQL's window run guard remains (`sql.rs:2771`), and schema/datasets remain
unguarded (`:3156-3175`). PromQL keeps dataset cache and metric/label builder guards
(`promql.rs:3453-3520`), cancels only the existing builder effects, and still applies
ordinary result completions without a generation guard (`:3532`). Tasks own their
snapshots and JoinSets/watch handles; device PollTask aborts/joins per screen exit.
I found no accidental borrowed frame/effect lifetime or new blanket suppression.

N1–N8/R2 restoration boundaries hold where rechecked: the panic hook restores then
kills the owned queue; input/control lanes are separate and bounded; INT/TERM
restoration happens at Quit acceptance (`terminal.rs:873`); signal descriptors live
for the process; Linux pipes are atomically CLOEXEC; handlers precede raw mode;
SIGQUIT remains installed through writer flush and termios restoration
(`terminal.rs:529-570`, `:983-1007`). The handler preserves exit 2/no restoration
and only the stable diagnostic prefix. Outside-session behavior remains a
pre-existing difference. C4 reserve-time ordering, current-thread dependence,
panic-versus-completion freedom, native thread-panic outcomes, editor-hang behavior
and cached signal-pipe failure remain explicit limits. No universal FIFO/scheduler
equivalence is inferred from source or tests.

## Byte identity, rendering and evidence bounds

**[source]** Raw schema identity remains owned `Vec<u8>` through
`schema_endpoint`/`append_url_bytes` (`sql_service.rs:244`, `api.rs:966`). URL escaping
occurs per byte. I rechecked the joiner's raw-element/escape/cleaning paths against
Go `URL.JoinPath`, including the raw schema suffix, and verified the generic U1
empty-final-element/trailing-slash and U2 verbatim-return branches predate this
phase in the merged base. They remain generic URL limitations; schema always
appends `schema`, and no broad URL repair is required by this review.

SQL raw-sidebar styling/layout returns `Frame::Bytes` (`sql.rs:3725-3752`,
`:3581-3600`); builder cuts remain temporary bytes (`promql.rs:5055-5077`); saved
clips retain byte output through the outer card (`saved.rs:414-482`, `:509`).
Selection/effect identities use the original owned values. Shared byte rendering
uses `string_contract=false` (`style.rs:1067-1068`), and terminal draw/close copy
bytes into owned writer state (`terminal.rs:855-858`, `:973-1000`). I found no
replacement-text alias remaining in the three SQL selection comparisons.

**[executed static / supplied execution]** I independently rechecked all 2,808
stored Go/Rust helper rows: 36,504 outputs compare exactly, with all listed durable
style evidence/module hashes matching. The 14,980 valid String before/after
comparisons and 22 standalone test passes remain supplied execution, not my rerun.
Their scope is the finite explicit-profile helper corpus. The ordinary String
facade intentionally retains its historical post-wrap replacement boundary
(`style.rs:1058`, `:1153-1158`, `:2169`); exact before/after behavior is not universal
Go-byte parity for those String callers.

The three focused SQL/sidebar, builder and saved-title process passes are
**release2** evidence. Their unchanged byte paths support this review's source
reasoning, but they are not silently relabeled release3 process runs. The release3
identity regression is separately bound to release3 and resolves request identity.
I verified its raw captures/cache record hashes: Go and release3 request raw schema
once, fallback once, then `logs/schema`; release2 repeats raw schema/fallback and
never requests `logs/schema`. The source correction and the two consequential
reducer regressions explain that actual before/after result. Picker Enter has
source/reducer support, not a dedicated identity process pass.

For the reachable saved title `x\x1b]0;t✅y z`, I independently verified exact Go and
release2 byte contexts and their raw capture hashes: both emit
`78 1B 5D 30 3B 74 E2 9C C2 85 79 20 7A` once. The valid input fits the clip width;
the transform is reachable without clipping. Both final observers reject the
unterminated OSC grammar. **Full cells/cursor equivalence remains unknown**; the
partial prefix and exact byte/effect agreement are not final-frame acceptance.
Arbitrary read splits, other emulators/locales and all String-facade OSC paths
remain outside that control.

## Release3 evidence and guard promotion audit

**[executed static]** The final evidence audit performs 1,711 recorded hash
comparisons with zero mismatches, including all 1,473 original styled capture
artifacts, central source/log binding, accepted freeze/delta, six new Go A/A
session records, and style/identity evidence. Original report hash
`4e34a1081a019b3b4e3e49d05f001b5c7ef880ed60bee308b5e3a3028e7d8fb9`
and reduction hash
`e15d7677512c182c113ce52dd4647ee003f45f6064685764aaf3f9d1eda5a703`
match. All 380 original session records bind to the recorded Go normal/cloud and
release3 normal/cloud artifacts; none records capture failure.

I independently counted the stored reduction as **160/160 gates** and **29/30
documentary comparisons**, with exactly one changed contract:
`p7-promql-wide-legend-view-panic-light`. After masking only digits in each exact
ephemeral fixture URL, the two new light Go checkpoints differ from the original
Go solely at cell data `(21,39)`, backslash versus `-`; four dark checkpoints are
equal. All six final frames are equal. No attribute, cursor or scrollback field
was removed by my comparison. This supports the narrow animation disposition.

**[source / supplied execution]** The accepted workload opts in only that named
light checkpoint (`phase7_workloads.py:482-483`). The comparator changes only the
single recognized active Line glyph's data, requires uniqueness, and preserves
style/position; final frames remain strict (`phase7.py:1242-1259`, `:1300-1303`,
`:1472`). Negative controls cover styles, position, label, cursor, scrollback,
other checkpoints, final frame, panic/exit/restoration and failed-view content
(`test_phase7.py:30-77`). The five artifact-test passes are supplied execution with
verified log identity, not tests I ran. The original 159/160 report remains intact;
the map-derived logout documentary mismatch remains visible and unwaived.
`harness/contracts/phase7.md:230` still names only the earlier delayed-request
opt-ins; `docs/phase7-harness.md:600-635` and the freeze record explicitly describe
the later accepted light opt-in. That older wording needs synchronization, not
another comparator exception.

Historical interactive guard promotion maps named prior boundaries to actual
phase7 PTY/effect cases (`phase7-harness.md:51-78`); it does not prove every prior
ordinary command or allow removing ordinary gates. Startup request permutations
are restricted to declared pre-input fan-out membership/multiplicity/dependencies,
not sequential requests, deduplication or blanket ordering relaxation.

**[supplied execution, independently recounted]** Hashed central logs contain
547 all-target passes/8 initial opt-ins ignored, then four OAuth and four genuine
15/30/50/120-second deadline passes/zero ignored. The ordinary effect suite has
49 cases. The central record reports matching source-before/source-after hashes.
The 17 device passes and 401 Python passes/zero skips have matching recorded
report/log hashes. These are evidence for the recorded candidate/schedules, not
proof that the new R3-F1 path was exercised. In particular, the effects runner can
run only Rust when `PB_GO_ORACLE` is absent (`interactive_effects.rs:8-30`); the
central coordinator's both-binary environment attribution is supplied attestation,
not something independently recoverable from the quiet success log alone.

Release3 retained phases 1–3 have supplied exit-0 driver records, including genuine
60-second phase3 coverage. At the inspected checkpoint the driver record contains
no completed phase4/5 entries. Their completion and full artifact audit remain
pending. Release2's 3,268 ordinary cases plus six guards predate the SQL identity
fix and cannot fill that gap. No timing/profiler campaign occurred here or is
inferred from functional runner elapsed fields. The frozen helper interim R2
disposition remains bounded helper evidence; this audit does not accept moving
collector corrections or accounting/preflight results.

## Required follow-up and unknowns

1. Resolve R3-F1, independently inspect its narrow correction, and run a pinned-Go
   versus successor held-schema regression that distinguishes the clock sampling
   positions. Bind successor source/artifacts and rerun affected gates; release3
   passes do not transfer automatically to new source.
2. Characterize the input-first fairness precondition with one pending device or
   clipboard effect and sustained ignored input. This is additional evidence work,
   not a claim that an ordinary user session already failed.
3. Finish and audit retained release3 phase4/5 (or the required successor replay),
   including real deadline tiers and exact promoted guard accounting. Do not reuse
   pre-identity release2 acceptance as final evidence.
4. Obtain the actual independent whole-phase final Opus verdict and dispositions.
   Rate-limit failures and this Sol report do not satisfy that requirement.
5. Keep malformed-OSC final cells, arbitrary PTY splits, String-facade byte limits,
   native scheduler residuals and race permutations explicit. Current Linux
   loopback HTTP/1.1 evidence does not establish TLS ALPN/HTTP2 transport behavior,
   live SQL, macOS/BSD termios/browser/FD behavior, Windows compile/runtime parity,
   arm64 numeric/color details, six-platform packaging, installer/checksum/linkage,
   upgrade or Go-readable rollback readiness. Licensing/shipping checks remain
   release work. Measurements require their separate corrected collector/preflight
   and quiet-window agreement; this source audit authorizes no campaign.

The bounded source verdict is **one unresolved medium SQL fallback contract
finding, prior named corrections holding, and no new high finding in the inspected
surface**. Evidence is materially stronger than at final R1, but neither tests nor
the accepted single-cell reduction waive the newly reachable request mismatch or
the remaining final-review/platform/release gates.
