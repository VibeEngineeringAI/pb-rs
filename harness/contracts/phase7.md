# Phase7 process and terminal contract

This harness implements the current phase7 plan. Historical terminal review
suggestions do not authorize layout changes, stale-result suppression, new flags,
new non-TTY refusal, or corrected cancellation writes. Production Go source is
pinned to `07af390`; `git diff 07af390 -- main.go cmd pkg` must be empty at capture.
Later Go fixture files are outside that production pin. Parent owns dispatch,
Cargo and migration of historical deferred guards. This contract does not edit
prior manifests or count a formerly deferred path as a retained stub failure.

## Tools and ownership

Python >=3.11 on Linux/Unix, `pyte==0.8.2`, `wcwidth==0.2.13`; the installed local
environment is `/home/ajs/.cache/pb-phase7/harness-venv`. There are no Rust
dependency/API requirements. Candidate binaries must be built by the parent;
this harness never builds a candidate. All child/fixture/helper commands inherit
`TMPDIR=GOTMPDIR=CLAUDE_CODE_TMPDIR=/home/ajs/.cache/pb-phase7/tmp`.

Owned sources: `phase7.py`, `test_phase7.py`, `phase7_workloads.py`, this contract,
and `docs/phase7-harness.md`. Shared phase2 config and phase3 HTTP machinery are
imported unchanged. The owned fixture adds query/body route selectors and
records dataset telemetry headers. `phase7_device.py` belongs to the parent;
device OAuth/API-key/browser/FD characterization is covered there.

Baseline executables are `baselines/pb-go` and `baselines/pb-go-cloud` under the
phase7 cache. Accepted previous Rust is `baselines/pb-rust-phase5`, SHA256
`9bd38b9db4f4f8e76cd43384cb024dddd4d122c6bc109d29a8afa483ae0e2c6d`.
The pre-review binary is a separate artifact, not this baseline. Prior Rust
deferred commands are scope evidence, not performance comparators.

## Independent sessions and retained evidence

Each sample creates a fresh config tree, fixture process, PTY and command
process. Reference/candidate sessions never share mutable fixture state or
tokens. Input is sent to a controlling PTY, stdout comes from that PTY, stderr
comes from a separate pipe. Non-TTY cases deliberately start a new session with
stdin/stdout pipes and no controlling terminal. Sizes are fixed unless a resize
action explicitly changes them. `PB_THEME` and terminal capabilities are fixed.
Dummy credentials are the only secrets. Clipboard commands are fixture scripts;
they access no desktop clipboard and cannot start a real browser.

Artifacts retain raw stdout/stderr, initial/final terminal frames, named useful
frames, foreground/background and attributes for every cell, wide-cell
continuations, cursor coordinates/visibility/shape, alternate-screen transitions
(including the last alternate frame before leaving), main-screen scrollback,
termios before/during/after, exact input encodings, replies to terminal queries,
request traces, monotonic HTTP milestones, parsed config plus modes, process
termination, native metrics, and helper lifecycle evidence.

Cell fields are `data, fg, bg, bold, italics, underscore, strikethrough, reverse,
blink, faint, conceal, overline`. ANSI named and corresponding indexed colors
are canonicalized to the same xterm palette RGB value. Default colors remain
the declared terminal default; bold remains an attribute. No text/style/border,
space, cursor, wrapping or scrollback stripping is allowed. Unsupported terminal
controls, unsupported SGR, incomplete sequences, scrollback overflow, missed
readiness, output limits and timeouts invalidate a sample. OSC queries and DSR
receive deterministic terminal responses recorded separately from user input.

`pyte` is not a complete hardware terminal. Unimplemented sequences fail the
observer instead of being accepted as text parity. Unicode behavior is pinned
by the emulator/width versions and tested for CJK and combining input. Emoji
grapheme/platform-terminal behavior remains a release characterization boundary.

Raw ANSI byte identity is not a screen contract. Exact post-alternate-screen
stdout and stderr are compared separately; non-TTY stdout is compared exactly.
Inline final cells/scrollback preserve the final UI and command summary. Terminal
restoration is checked against the full acquired termios state, not just echo.
`wait4` distinguishes true signal death from an ordinary exit with numeric
130/143. Cursor and alternate-screen restoration remain separate assertions.

PR #5 follow-up captures `terminal_modes.initial/final` for application cursor,
mouse tracking/protocol, focus reporting, bracketed paste and synchronized
updates. Normal restoration requires equal initial/final mode sets; explicit
`screen_restored: false` cases still compare the sets across binaries. Legacy
captures reconstruct these sets from retained raw ANSI through the same tokenizer
without rendering or rewriting evidence. Missing raw evidence fails validation.
`history_overflow` records actual bounded-history eviction, including reverse
scrolling and eviction in the final token; merely filling history is valid.
Existing capture failures remain failures and are never rewritten.
Supported full terminal reset clears tracked session modes, but never clears a
recorded history-loss flag. Mode-only legacy parsing does not require geometry.
For captures predating `history_overflow`, a passing no-loss conclusion requires
all retained scrollback frames below capacity and a parsed stream without history
clears or reverse-index. Ambiguous evidence fails validation; it is not silently
counted as no loss. Missing raw data is a per-session issue in reduction.

This follow-up compares the listed session modes at acquisition and exit. It does
not extend the observer to global cursor visibility across alternate-screen
switches, default-on autowrap, application-keypad state, or checkpoint mode parity.
Those remain separate observer limitations. Mode tracking recognizes ESC-prefixed
CSI; UTF-8-encoded C1 CSI may be interpreted by pyte without mode accounting.
The Go and Rust TUIs emit ESC-prefixed CSI; C1 mode parity is not claimed.
Reverse-index bottom-history eviction
is conservatively rejected even though that deque is not exported in `frame()`.

## Readiness and first useful frame

An action declares positive/negative visible text predicates, alternate-screen
state, optional cooked/raw state, request membership/count, or clipboard helper
completion. A useful frame first exists when all declared predicates hold.
`first_useful_monotonic_ns` records the first observer evaluation that satisfies
them. `ready_monotonic_ns` requires those predicates and identical **complete
styled cells, cursor, alternate state and scrollback** for 80 ms by default.
Changing cells resets the stability hold. The hold is a readiness policy, not
render latency. It prevents sending keys to a spinner or incomplete setup.
Response readiness uses actual row/content predicates where available, not just
the existence of a RESULTS heading. Request-only readiness proves effects,
not successful rendered results. Observer stamps are upper-bound observations;
they do not measure event receipt, reducer time or terminal write completion
inside the candidate. Startup is fork-to-first-useful, excluding fixture setup.

The raw stdout chunk byte ranges/timestamps and action timestamps allow replay
and causal auditing. Frame byte counts report bytes since the preceding action;
they are not socket traffic or filesystem write counters.

## Normalization and effect ordering

Only ephemeral fixture origins and temporary home paths are normalized in
byte/request/config contracts. Known ASCII fixture origins in styled grids are
replaced by an equal-width label while preserving every cell's attributes,
cursor and geometry. Fixed time ranges avoid wall-clock normalization. Relative
clock behavior and map-derived profile ordering must retain documentary
mismatches; they are not automatic passing screenshots.

SQL startup effects have an explicit initial fan-out membership: home discovery,
schema, and (only with an initial query) the initial window query. Initial
home/query arrival may commute; home must precede schema. Only that initial
bounded batch is compared as a multiset, preserving bodies, auth, query params,
membership and multiplicity. Subsequent requests stay ordered. This rule was
confirmed by a Go-versus-Go capture, not guessed from Rust behavior. Unexpected
requests, missing effects and duplicate effects fail. Other concurrency that
has no declared graph remains a mismatch needing disposition.

PromQL with an explicit dataset similarly has only its initial home/cache/result
batch commuting (two effects without a query, three with one). Seven retained
independent Go sessions exhibited three permutations. Implicit `select-dataset`
keeps the independent initial cache read distinct from the auto-selected cache
read, which must follow home discovery. Full target URLs enforce that dependency.
Both-mode range then instant and every later builder/rerun request remain ordered.
`reduce --capture-root PATH --out NEWPATH` applies these explicit contracts to
retained process evidence without running any additional sessions.

Normal SIGQUIT is an explicit gate: exit 2, no restoration, exact first stderr
line `SIGQUIT: quit`. Only the following runtime-specific dump tail is normalized;
the entire original dump is retained. SQL/PromQL empty-editor Ctrl+Left hang
cases deliver SIGTERM twice with at least 150 ms spacing and require an alive
process with identical full styled frames and termios after both signals.
SIGQUIT must then end the process with exit 2, raw mode and alternate screen
still acquired. Missing any checkpoint, early signal death or graceful restore
fails this scoped lifecycle contract. No other stderr normalization is implied.

Independent assertions check cancellation mutation absence, termination,
restoration, saved profiles/defaults, mask visibility and request effects before
comparison. Oracle acquisition records its own failures; a candidate cannot
validate an invalid reference. Documentary entries retain findings and exact
comparison issues but never enter parity pass counts.

## Ctrl+V fixture

Each clipboard case prepends a disposable inert `xclip` to PATH and removes
`WAYLAND_DISPLAY`. The fixture returns deterministic multiline Unicode and can
delay completion. It logs child argv, start/completion monotonic stamps,
RLIMIT_NOFILE, FD count and process identity. Go atotto reads using
`-out -selection clipboard`. Both actual clipboard invocation and rendered
insertion/drop behavior are asserted. Focus-change cases deliver to the active
input after completion, not the focus at request time. A separate quit case
records surviving helpers **before** the observer kills the remaining process
group. Normal completion must reap helpers, including absence of a retained
zombie identity. Go delayed quit records SIGHUP to the helper when its terminal
session leader exits; no living-helper leak was observed. Owned candidate
pending reads should still abort/reap. SQL and PromQL also execute clipboard
reads but drop paste completions, because those models forward editor updates
only inside KeyMsg handling. Their cases require read completion and unchanged
editor placeholder, not a silently corrected paste behavior.

## Native metrics and separate performance gate

`wait4` supplies command user/system CPU, peak RSS, faults and context switches.
Linux `/proc` samples RSS, CPU ticks, FD count and kernel I/O counters at 50 ms.
Missing `/proc` samples remain unavailable, not zero-valued performance wins.
Child reaping time and observer total wall time are separate (PTY drainage can
outlive the child). `rusage` and `/proc` metrics have different accounting;
neither is the fixture's or collector's CPU. `/proc/io` is not network traffic.

Functional captures retain these fields for diagnostics and make no speed
claim. NO timed campaign, benchmark or profiler runs without an agreed phase6
quiet window. `observer-floor` requires a coordinator approval JSON with
`approved: true`, `scope: "phase7"`, and future `expires_unix`. It emits known
PTY frames stamped immediately before writes and measures write-to-first-useful
observation; this includes PTY transit, scheduling and emulator work. Its stable
hold is zero. Measure this floor before interpreting fast frame differences.

Workload export only writes definitions. Independent sessions are statistical
units; frames are dependent observations within a session. Bootstrap summaries
resample session summaries, never individual frames. Current CI helpers are
exploratory pointwise, not simultaneous acceptance claims. Five warmups, at
least 30 independent sessions, randomized sequential executable order, A/A,
observer controls, prior-Rust scope and predeclared budgets remain prerequisites
for a timed campaign. No p99 confidence or performance acceptance is claimed
from this functional suite. Native six-target terminal/packaging validation
remains outside Linux PTY evidence.

## Optional collector seams

The services/measurement lane was directly coordinated on these keyword-only
APIs. Ordinary callers retain the same behavior and serialized resource fields:

```python
sample(binary, case, output, *, execute_fn=None)
execute(command, env, cwd, case, fixture=None, *, proc_sampler=None,
        continue_running=None, readiness_fn=None, observer_config=None,
        spawn_helper_build=True, resize_signal_policy='legacy')
observer_floor(output, quiet_window, sessions=5, frames=20, *, execute_fn=None)
```

`proc_sampler(pid)` replaces only `/proc` sampling, never native wait4.
`continue_running()` is checked on each active-child driver iteration; None
continues, a failure string kills/reaps and retains the invalid session.
`readiness_fn(terminal, wait, trace, tty, action_started_ns, now_ns)` replaces
the useful predicate while the same full styled-frame stability hold remains.
`observer_config` supports boolean `record_termios` / `record_chunks` /
`record_descendants` (each default true). Turning them off omits optional histories but retains the PTY,
emulator, before/after/frame termios, lifecycle checks and native wait4 metrics.
Disabling descendants omits only optional50ms traversal, never PID-bound group cleanup/reap, and sets descendant_comparison_eligible=False. Frame stamps include `stable_since_monotonic_ns` and persistent first-ever match provenance. `execute_fn` uses the existing
executor argument contract. No nested performance flock is taken; the collector
owns the common lock and the quiet-window/deadline policy. These are harness
seams, not production CLI flags or a second driver.

## Bounded wide-text characterization

At process width 92, PromQL's result width is 88 and legend label width 39.
`job="测…"` with 20 CJK runes has <=32 total runes but >39 cells; Go slices
`[:38]` of capacity32 and recovers a View panic. Require exit0, restoration,
the exact panic value plus `Caught panic:` / `Restoring terminal...`, and retained
last alternate screen showing the pre-fault loading pane, without the failing
legend. No successful final View is drawn. Only a stderr trace beginning
`goroutine ` and containing `runtime/debug.Stack(` and `recoverFromPanic` is
excluded in this specific panic case. Unrelated stderr and the stdout diagnosis
remain exact.

SQL at width176 has sidebar valueW33: its maximum slice is `[:32]`, which fits
the stack capacity32. Its long CJK dataset case characterizes visible clipping/
overflow and graceful restoration, and expressly asserts absence of a caught
panic. The helper's internal NUL fill is not visible in the captured process
stream (raw NUL count0). No SQL dataset panic is invented from the shared helper
fault. The coordinator accepted this source/runtime distinction.

## Explicit active-loading animation phase

Only cases carrying `loading_spinner_checkpoints` may normalize a named
checkpoint's single active Line spinner before exact ` fetching...`, in visible
alternate-screen cells. Recognized data is `|`, `/`, `-`, or backslash; require a
preceding space and exactly one match. Canonicalize only data to `|`. The entire
style tuple, its location, all remaining data, cursor and scrollback are retained.
Unknown/multiple/missing glyphs fail independent validation; final frames and
unlisted checkpoints use ordinary strict comparison. The annotation is limited
to SQL delayed-window and PromQL delayed-result `old-request-pending`, both
supported themes, plus exactly `p7-promql-wide-legend-view-panic-light` and
`p7-promql-wide-legend-view-panic-dark` at `before-view-panic`. These opt-ins
do not cover final frames. Four initial Go sessions demonstrate delayed-request phase variance
at row18 col26. Two new light Go sessions and the original reference establish
wide-legend checkpoint variance at row21 col39; four historical dark controls
stayed equal. Independent release5/combined-release6 Go dark sessions now show
the same single-cell phase variation, reviewed in
`docs/phase7-review-dark-spinner-combined-opus-r1.md`. The combined reduction
changes exactly the dark opt-in and preserves the original report.
Raw evidence is preserved in `oracle-spinner-phase-1` and
`oracle-wide-legend-release3`; the accepted full reduction and all capture hashes
are recorded in `docs/evidence/phase7/release3-styled-reduction-manifest.json`.

`expect.checkpoint_cells` supplies independent zero-based row/column/data
assertions for known visible result contracts (merged-series first-Right dot,
Inf bottom dot, duplicate-result value). It supplements complete styled equality.
No request, runtime diagnostic, width or attribute gate is relaxed by this seam.

PromQL G3/G4/G7/G10 cases preserve raw duplicate JSON keys, fixed/UTC CLI locations,
Go's14-point unstable sort observation and linux/amd64 infinity/overflow behavior.
G5 has no promoted screen case: retained Go hex-versus-rounded-payload controls
have identical styled screens; exact value parsing is covered by native units.
Process-free reduction records original report hash, current harness hashes and
captured/active case hashes. It never overwrites retained captures/reports.

## Historical scoped R4 collector policy (shared source unchanged)

This checkpoint's seven-chunk and per-phase decoder instructions are historical. The current R8 policy below and its exact matrix supersede them; original evidence remains retained.

`spawn_helper(build=True, *, probe=True)` is the exact published resolver. Preparation before captures may probe/version-check; `execute(..., spawn_helper_build=False)` resolves buildFalse/probeFalse and requires prepared snapshots, with no compile/probe/write route. Keyed binary/sidecar aliases (including dangling symlinks) fail closed. Protocol2 reserves the exited leader until PID-bound cleanup/release; native wait4 is separate from the exited lifecycle stamp. Default styled behavior stays unchanged.

Bench primary keeps the root proc sampler/PTY/emulator/readiness and disables optional termios/chunk/descendant histories. Attribution enables descendants, retains distinct eligibility and uses CPU/RSS triggers only when explicitly eligible; its latencies remain descriptive. A/A Go-Go or Rust-Rust uses identical canonical artifact and provenance, separate comparison/case/preflight labels, and never budget evidence. Signals are unsupported in measured Phases.change; generic styled signal cases are unchanged.

Admitted functional preflight owns one nonblocking exclusive common lock across preparation/capture/postprocessing/end checks; it creates no quiet approval and is unmeasured. A held lock fails before preparation, with no wait and no probe/release race. Timed/decoder/profiler campaigns require current parent approval plus phase6 agreement with matching campaign_id and issued-60s <= agreement time <= now. Lock ownership alone grants no timing authority.

Evaluation/action-start stamps remain historical. dispatch_monotonic_ns is immediately before actual send/ioctl/signal and is the change latency origin. Bench replay records absolute raw observed offsets by adding Terminal.byte_offset, encoded pending parser text and decoder buffered UTF8 bytes. This source-bound accounting is covered by partial-token tests. Each role/phase reconstruction uses untimed prefix and previous resizes, then first-match phase bytes and untimed accepted-frame tail; full frame must match. Missing/invalid reconstruction means non-interpretable latency triggers; no subtraction or reducer/render/write-duration inference. Decoder diagnostics require their own bounded quiet approval.

The shared resize policy is implemented and frozen: default `legacy` preserves styled ioctl+explicit WINCH; measurement `ioctl-only-changed` validates all geometries and rejects no-op sizes before acquisition, using ioctl only. Root-verified own-child delivery/restoration/wait4/PID-cleanup controls are in `docs/evidence/phase7/helper-resize-r4-{freeze,native-control}.json`. Actual Go/Rust collector repaint/preflight remains separate; legacy resize cannot enable measurement triggers. This replaces the historical proposal-only status.

R4 pooling is predeclared before collection: immutable pool specification binds source/case/preflight/artifacts/helper, distinct chunk seeds/counts and floor configuration. Offline reduction requires all seven chunks,5warmup+30measured pairs, raw-session hashes/lifecycle uniqueness, revalidated contracts/comparison/extraction, complete retained controls and end identities. It never forms a valid subset. Pointwise bootstrap intervals resample paired sessions; tails remain descriptive. Before booking, functional whole-role scheduling costs plus declared floor/serialization/cleanup reserves determine conservative feasibility; no cost/feasibility has been measured by this lane.

## Settled additive resize policy and measurement consumption

This successor supersedes the proposal-only resize paragraph above. The shared `execute(..., resize_signal_policy='legacy')` retains legacy ioctl plus explicit SIGWINCH by default. The measurement-only opt-in is `ioctl-only-changed`: initial size and every ordered scripted resize must be a list/tuple pair of non-bool integers in1..65535, and each resize must change the immediately preceding geometry. Validation runs before dependencies/helper/PTY/CLI acquisition. Dispatch remains emulator resize, existing dispatch stamp, then ioctl; there is no additional group signal or numeric fallback. Standalone signal actions keep their existing behavior. Result `resize_signal_policy` and each resize event's `resize_signal_source` (`ioctl+explicit` / `ioctl-only`) retain the applied behavior. Legacy adds no new geometry validation.

Bench primary, attribution, functional preflight, floor/replay adapters and profile controls/instrumented adapters explicitly pass `ioctl-only-changed`; no production or harness CLI flag is added. Case definitions, plan/preflight/pool/decoder reports and raw samples bind that policy. Ordered resize events must match the declared script, changed geometries and `ioctl-only` source. Missing/legacy/mismatched fields fail closed and raw failures are retained. Replay reconstructs identical local terminal geometry at bound offsets. A changed-only primary Go-Rust resize phase may become interpretable only with valid matched per-role decoder diagnostics and all existing gates; attribution/A/A latencies stay descriptive. Old legacy preflight/replay/control evidence cannot enable resize triggers. Actual driver native delivery/restoration/cleanup controls and final CLI preflight are parent responsibilities; source tests alone are not acceptance.

## Historical R5 actual-session interpretation (supersedes single-preflight eligibility)

No shared seam changes: the frozen helper/source and changed-only resize policy remain. Bench-owned readiness records exact through-first-match evaluation count, raw offset, TTY, timestamp, qualification, wait and initial consumed-byte baseline. Its bounded history exhaustion is retained non-interpretable evidence. `run` writes an immutable descriptive capture and rejects preflight `--decoder-report` eligibility. `campaign-decode` needs a distinct fresh bounded quiet ID and revalidates every retained measured session/phase, warmup, both observer controls, source/helper/artifact/metadata/preflight/raw/end identities. Timed replay uses those actual byte/evaluation boundaries and the live useful/frame/geometry semantics; prefix and final-frame tail validation are untimed. Repeats collapse per actual session, never increase independent CLI count. `interpret` produces a new campaign/diagnostic-bound artifact, revalidates all bindings and predicates, and applies the observer screen only to primary Go-Rust5+30 sessions. No subtraction or reducer/render/write inference. Any incomplete phase/population/approval/end binding invalidates interpretation; preflight diagnostics cannot enable it.

Seven distinct campaign-bound historical approvals and companion IDs/scopes are required for the provisional long-idle pool. Actual full matching preflight feasibility at unchanged reserves is required before any real pool spec/admission; unknown or non-fitting cost blocks it. Pool latency remains descriptive and never inherits chunk eligibility; complete pooled diagnostic interpretation is unimplemented. Dense table readiness additionally requires the exact custom footer atH−6 endingW−3 with both side borders before first match. Native shared controls are root-verified; actual source-derived collector completeness/feasibility/observer/tool controls remain separate executable prerequisites. R1–R4 checkpoint statements above are historical, with original evidence retained.

## Historical R6 successor (no shared helper/terminal/C change)

Latency eligibility requires primary Go-Rust5+30, matched role-median replay delta<=1ms, max role-median replay work<=1ms and equal median evaluation counts. Report all work/count medians. This is a replayed-component screen, not a universal total observer-bias bound; loop/syscall/proc/scheduling components remain unknown and there is no subtraction. Attribution/A/A/pooled latency remains descriptive.

Raw validation holds at most two captures/pair for styled comparison, then descriptors only. Diagnostic/interpret load/drop one role at a time. One forward byte/resize cursor creates phase-start snapshots; fresh streams/screens clones preserve parser-ground state, pending ANSI/UTF8, SGR, cursor/modes/queries, main/alternate/history including historical line defaults. Nonground parser state is explicitly non-interpretable. Session progress is retained; no repeated from-zero prefix/rewrite per phase. Bench-owned checkpoint identity omits idle/anchor histories without changing first-match/offset/stability or shared APIs; profile adapter consumes that classification.

`diagnostic-plan` validates a FULL matching preflight and the original validated identity against the same bound snapshot. Exact structural counts are declared; calibrated decoder fit stays UNKNOWN. A separately approved finite<=3600 `campaign-decode` processes the first actual measured pair as retained pilot. Before each remaining session require3x observed max whole-session wall x max(work ratios,1), plus8seconds/role,30+5*pairs finalization and60seconds launch allowance, to fit both remaining guard budget and approval lifetime. Positive missing/zero/unknown pilot dimensions reject admission. Incomplete diagnostics retain failures/unattempted identities and cannot become valid subsets. Actual diagnostic wall/budget retained; no fabricated feedrate or guaranteed fit. Root externally hash-binds completed diagnostics before `interpret`, whose CLI finite<=3600 budget is declared. Offline-heavy CLI modes take nonblocking FunctionalOwnership and remain unmeasured/coordinator-dependent, without synthetic approvals. Capture feasibility also ADDs60seconds launch reserve and checks actual remaining lifetime; other reserves remain unchanged.

Root declares NO COMMIT for the complete program. HEAD binding remains unchanged alongside source/helper/binary identities; any later change invalidates retained bindings. Twenty-four non-long primary diagnostic templates are potentially useful;52attribution/pool templates are optional descriptive work. R5 records remain historical and immutable. Ordinary path/read/check-to-use TOCTOU remains disclosed and non-atomic; internal native-stage instrumentation and real preflight/decoder/profile controls remain unimplemented or unknown.

## Historical R7 successor policy (supersedes R6 chunk/count disposition)

Ineligible latency is inconclusive for BOTH pass and trigger. Summaries retain descriptive paired-median crossings (>10ms AND >10%) independent of eligibility as material potential regressions requiring fresh-seed confirmation AND investigation. No crossing can be dismissed as observer artifact without further control. No descriptive flag accepts a trigger or budget; unknown/missing values never become zero. Role-session replay-work max/p95 are descriptive, not tail-bias bounds. Existing eligibility screen remains coupled to response latency; no new observer design/subtraction is introduced.

Pilot serialization ratios compare CAP to CAP; actual bytes are separately recorded and checked<=cap, including failure evidence. Identical declared work ratios are1. First admission with58remaining roles is174*W+704seconds at equal work. Actual W/fit is UNKNOWN; real-code pure/mock regressions are not calibration.

Nine chunks are EXACT `(4,0),(1,3),(0,4)x6,(0,3)`:5warmup+30measured total, max4pairs. Full index/seed/count table and exact case/source/preflight/control/approval identities bind; old seven schedules and incomplete/duplicate/wrong nine schedules reject all. Authorized floor_sessions3 and all reserves remain unchanged. Worst long allocation2934+12*x has synthetic355.5second role boundary3600, not real admission. Four warm-only chunks have no measured pilot: NOT APPLICABLE and no executable diagnostic template. Program totals84campaigns/seeds,4648capture-controlchildren,104functional,3120measuredreplays;80runnable diagnostics24potential+56optional, plus4explicit non-applicable slots. Pool latency remains descriptive; no seven fallback/control reduction. Actual preflight/fit stays unknown.

ROOT coordination rule: ALL preparation/cached_spawn/evidence prevalidation starts only in exact agreed own window with common lock free for admission, never another phase's quiet. Prechecks remain outside QuietWindow; no flock-transfer refactor. Offline ownership unchanged. FULL preflight means all workload variants of campaign profile/theme/size/options, not automatically both themes. Some bounded evidence validation is not internally guard-interruptible; expiry detection can wait until return, with no hard-real-time guarantee. Root hash-binds diagnostic after process exit. Complete-program NO COMMIT/HEAD/source freeze persists, with R1–R6 evidence preserved and actual native/internal/tool controls still pending.


## Current R8 collector contract (supersedes historical R4–R7 launch directions)

Exact nine-chunk counts and seeds remain unchanged: `(4,0),(1,3),(0,4)x6,(0,3)`,5warmups+30measured independent pairs. Counts remain84campaigns,4648capture-controlchildren,104functional roles,3120measured-role replays;80runnable diagnostics24potential+56optional and4warm-only NOT APPLICABLE. Floor_sessions3 and all existing reserves remain. Pool latency is descriptive and inherits no chunk eligibility.

Pool CLI uses nonblocking FunctionalOwnership and explicit finite non-bool `--budget-seconds` in(0,3600], recorded alongside elapsed wall and retained failures. Guard starts before first evidence read. FULL matching preflight feasibility is freshly validated ONCE per reduction against the exact spec; all nine per-chunk identities/contracts/approvals/raw data/end rechecks remain. Live validate_pool_spec always performs fresh feasibility. Every Options field binds except exact separately checked sessions,warmups,seed,campaign_id,pool_chunk,pool_spec. max_sessions has an exact declared table computed as2*(warmups+sessions)+4*floor_sessions*profiles:20x8 then18 for this program. Each actual cap is checked before normalization; worst-four-pair feasibility uses20 while last actual run remains18. No unknown-key or stale-allocation bypass.

Generic summaries never decide acceptance: accepted_triggerFalse,budget_passNone even for eligible non-crossings. Crossings require BOTH paired-median>10ms and>10%, retained independently of observer eligibility. A/A is a harness/host-bias sentinel, attribution/pool/stable-ready are descriptive notes, and only primary Go–Rust first-expected is a potential candidate-regression signal. Unknown is never zero; incomplete data never form valid subsets. Observer work/count/tail descriptions provide no total/tail-bias bound or subtraction. The reduce convenience mode trusts an unvalidated saved report and NEVER provides decision evidence; coordinator rules/decisions are separate from generic summaries.

Root's exact-own-window/common-lock-free rule covers ALL preparation including profile cached_spawn/prepare-symbols/readelf/evidence prevalidation. No launch during another lane's quiet. Heavy offline CLI work retains exclusive nonblocking ownership without synthetic quiet approval. Timed work requires separately approved bounded windows. File checks are ordinary non-atomic drift checks; some bounded validation cannot be interrupted internally. NO COMMIT keeps original HEAD and all harness/helper/source identities bound. Actual source-derived completeness, feasibility/calibration and profiler interoperability remain executable prerequisites, not source-test acceptance.

## R10 pool-chunk summary label correction

Run completion, derived interpretation and unvalidated reduce pass pooled=bool(options.pool_id), using saved Options in reduce. Primary pool-chunk crossings are descriptive_pooled_latency/material_potential_regressionNone, with accepted_triggerFalse/budget_passNone; ordinary primary context is unchanged. Complete pool already used pooledTrue. No public signature, capture, guard, feasibility, resource or protocol measurement behavior changes.

The four pending pool reducer templates explicitly retain budget3600. Actual complete-pool bootstrap draws remain2000, from its summary Options default; nondefault chunk draws do NOT propagate. Scoped pure tests verify the real call uses2000 even with40-draw chunks. R8's106+23checks/evidence remain immutable, R10 correction evidence is separate, and the root-owned decision R2 hash/final seven-file matrix freeze remain pending. No accepted protocol hash, CLI/native/lock campaign, commit or performance decision is created here.


## Attribution long-idle post-idle bracket opportunity

Only attribution SQL long-idle cases add the post-idle-bracket-hold anchor after existing idle E and before quit. Its170ms min_elapsed plus existing80ms full stable-frame gate gives a250ms minimum on the shared monotonic stamp basis; independent retained validation requires H.ready-E.ready>=250ms and exact start/duration metadata. S/E and declared300seconds remain unchanged. Actual same-process serial-read brackets, including two valid retained stamps at/after E, remain mandatory; missing data is unassessed despite the hold. No sampler/cadence/observer/CLI flag changes and no latency/decoder eligibility follows this anchor.

The two attribution long effective case timeouts are360.25seconds (base CLI60+idle300+hold0.25), primary long stays360. CLI base flags do not change. Whole-session wait4 CPU/RSS/lifecycle include actual hold work, with no subtraction; idle still ends at E. All84campaigns/seeds,9chunks,4648children,104functional roles,3120replays,20x8/18caps and controls/reserves stay fixed. If actual whole-role preflight cost adds nominal0.25seconds, existing1.5x non-idle allocation adds0.375seconds/role (+3worst-four-pair chunk); observed fit remains unknown and must be recomputed from fresh matching preflight.

Rebind **all52 case identities**, every decision/descriptive ledger row and every matrix, pool-spec, plan and preflight expectation reference to the actual selected argv/parser-typed profile. Supplied60/2/300timing values are floats while omitted defaults retain frozen integer types. All52 integer-literal baseline hashes differ from the selected execution-typed baseline; historical generator hashes cannot substitute for execution-typed hashes. Exactly two attribution-long definitions change semantically WITHIN either fixed typing profile; routes/fixtures/latency phase sets remain unchanged. Compute campaign_cases directly from EACH argv's own actual Options, including all2preflight/84campaign/4pool-plan records. The86preflight/campaign records use raw parsed Options unchanged; the4pool-plan records retain raw parsed Options and execute the exact existing pool_definition base-Options assignment AST before campaign_cases(base), matching its actual execution without a guessed normalization or validation bypass. Compare same-case definitions with the declaration's ledger templates and expected preflight case_hashes. No actual collector preflight exists yet: equality with future retained preflight case_hashes is still required, and the pending matrix path substitution remains provisional. Regenerate all source/preflight/spec bindings prospectively; no old declaration or source-bound preflight authorizes this successor.

The reviewed four-file remedy was applied in root's explicit post-idle edit window, then its integer-duration test gained250.0 and a compiled type-guard-removal control. Stage1 exact reviewed postimages and successor hashes/checks are retained separately under `/home/ajs/.cache/pb-phase7/postidle-bracket-applied`; the entire off-tree proposal remains immutable. No full bench suite or native/CLI/lock work ran in this scoped window. Central full checks, protocol R3 adjudication, final matrix freeze and actual matching functional preflights remain pending. The anchor's raw checkpoint/offset/session JSON is retained but per-evaluation latency history/replay/metrics are excluded; whole-session/serialization work remains real. Neither the delay nor its retained consistency check proves actual brackets or kernel accounting provenance.

## A1 default-open SQL collector correction

The unchanged SQL measurement actions preserve the default open columns drawer. Its source-derived results width is `max(30,W−(34+(3 if W≥140 else 0))−1)`. SQL resize readiness requires the exact footer on zero-based H−6 ending exclusively at resultsW−3, the row-zero marker inside the results pane on row 16, and results borders 0/resultsW−1, blank gap resultsW, sidebar borders resultsW+1/W−1 on both rows. Actual dimensions and all existing output/rerender/corner/stability/validation gates remain required. No ctrl+l action, case/hash/count/seed/fixture change or footer-anywhere fallback is introduced. This supersedes historical closed-drawer W−3 SQL assumptions only; full-width PromQL geometry stays unchanged and its actual preflight remains pending.

The actual A1 retry (report `1dcd24f6…78dd4`) disproved the historical PromQL dense footer row. Both the pinned Go reference and the Rust candidate paint the custom footer on zero-based H−8 ending exclusively at W−3, because the pane body pads to availH−2 rows around resultsInnerH=availH−3 content and an empty detail still measures one row. Dense table readiness therefore requires the exact footer token on H−8, preceded by a blank cell and followed by two blanks and the right border; blank pane rows H−7 and H−6; the full bottom border on H−5; and the complete table-focus hint row on H−3, matched in full as `  <t> Toggle chart/table +<ctrl-r> Run +<ctrl-c> Quit +`. That row is the last one repainted after the toggle, so a stale or half-painted toggle hint bar fails. This claim applies to the toggle hint row, not to navigation footer paint completion. This geometry is declared only for the 120-column dense profile, the only dense width in the default 52-case campaign. Other widths are not established; `dense_table_geometry` has no width guard, and a differing layout is expected to fail closed. This supersedes the historical H−6 dense statement above and the preceding clause that full-width PromQL geometry stays unchanged. The geometry change alone changes no case data. Composed with the separately reviewed startup fan-out metadata (`docs/phase7-a1retry-request-fanout-proposal.md`), 28 of 52 case definitions change, only in their `initial_fanout_*` keys. The reviewed actual Go control (report `010881265fb5a977b80ec47e04ae1a08e8d8c4e849a50d966c16205dda17d3c4`; `docs/phase7-review-pagenav-composition-opus-r1.md`) observes cursor sequence 1, 16, 31, 46, 256, 241, 1 for dense-1 (256 rows), dark, single-key writes at 120×40. Only `dense_case` sustained construction adopts page step 15, requires `tuple(size) == (120, 40)` and raises `ValueError` otherwise. This construction guard is distinct from `dense_table_geometry`, which has no width guard. Because campaign construction builds the sustained case before applying case filters, any non-120×40 `--size` is refused for the whole campaign, including `phase7_profile` and filters selecting only nonsustained cases. Forward/reverse ceil bursts and End arithmetic `((total−1)//page)·page` are unchanged. End/PgUp at non-aligned totals, bursts, light theme, 256,000 rows and Rust behaviour are source-only extrapolations, not observations from that control. The source supports the existing End shape; no other size is established. Four sustained action definitions change in addition to the composition’s fan-out metadata. The retained raw stream and seven checkpoint offsets/digests are in `docs/evidence/phase7/a1-pagenav-go.json.gz`. Geometry alone does not establish paint completion during in-place footer updates; the actual navigation values are protected by 80 ms styled stability, strict next-baseline equality and zero bytes between ready and the next dispatch. Fresh independent successor review, root full-suite validation, full rebinding and actual root 24-role Go/Rust preflight remain required. No performance or parity claim follows.

Before any post-sample binding check, functional preflight retains informational `capture_failure` and `termination` from the returned role capture. A subsequent binding exception still aborts, retains its own error and leaves the report invalid; the original timeout remains visible. No later role, comparison or subset is admitted after that failure. Hash-bound actual Go frames/raw replay and compiled-old-predicate/reporting-removal controls are owned pure tests, not native acceptance. Original failed evidence is immutable. See the A1 correction in `docs/phase7-measurement.md` and `docs/evidence/phase7/a1-geometry-fix-r1.md`; fresh independent review and actual matching root preflight are separate prerequisites.
