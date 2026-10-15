# Phase7 harness implementation and evidence

Implementation is confined to the five original owned harness/documentation files
plus the explicitly announced, harness-specific native supervisor
`harness/phase7_spawn.c` (measurement review H1).
Current plan: `/home/ajs/.cache/pb-roadmap/phase7-final.md`; source-only terminal,
network, architecture and validation notes were read as context. Current Rust
profile/config/auth/API/dataset/admin/saved-query/SQL/metrics services were
inspected before harness edits. Their distinct policies are observed through
real CLI processes; no service/API or Cargo edit is required by this harness.
Parent integration/builds remain separate. No commits were made.

Dependencies were reported early in
`/home/ajs/.cache/pb-phase7/harness-contract.md`: Python, Linux PTYs,
`pyte==0.8.2`, `wcwidth==0.2.13`. Measurement review H1 additionally requires
a system C compiler selected by `PATH` (`cc`, resolved, hashed and versioned in each
helper build manifest) for the small owned supervisor. Preparation performs compiler
and capability probes; strict capture only verifies prepared identities, as detailed
in the final resolver integration section below. Current capture additionally
requires Linux pidfd process-group signalling, verified by an actual own-child
probe before CLI acquisition; no portability claim follows from kernel version.
Python packages were installed only into
`/home/ajs/.cache/pb-phase7/harness-venv`. Production Go diff against `07af390`
is empty under `main.go`, `cmd/`, `pkg/`; later harness Go fixtures account for
the unscoped Go diff. Baseline identities are recorded in capture reports.
Accepted phase5 Rust has SHA256 prefix `9bd38b9d` and is separate from pre-review.

## Running functional evidence

Set disk-backed temporary variables for every command:

```sh
export TMPDIR=/home/ajs/.cache/pb-phase7/tmp
export GOTMPDIR="$TMPDIR"
export CLAUDE_CODE_TMPDIR="$TMPDIR"
PY=/home/ajs/.cache/pb-phase7/harness-venv/bin/python
$PY -m unittest discover -s harness -p test_phase7.py -v
$PY harness/phase7.py list --theme both
$PY harness/phase7.py capture --theme both --out /home/ajs/.cache/pb-phase7/go-functional
$PY harness/phase7.py compare --candidate /path/to/prebuilt/pb --out /home/ajs/.cache/pb-phase7/differential
$PY harness/phase7_workloads.py --out /home/ajs/.cache/pb-phase7/workloads.json
```

Output directories must be new; raw evidence is never overwritten. `--case`
matches a substring and can be repeated. Each binary runs against its own
config/fixture and the same scripted input, dimensions, theme and responses.
The contract specifies stable-frame readiness, full cell fields and permitted
normalization. No candidate build is attempted. The parent owns a separate
`harness/phase7_device.py` for release-configured device/browser/API-key cases.

## Promoted historical guard map

- Phase1 `login` and phase2 terminal login guards: `p7-login-cancel`, backtracking,
  required URL, basic/API-key save, corrupt config overwrite, duplicate
  replace/change-name, cloud choice cancellation, signals and clipboard cases.
- Phase1 `role add` and phase5 picker boundary: `p7-role-cancel`, `p7-role-reader`.
  Conflict-before-prompt belongs in the expanded role fixture corpus.
- Phase1 untyped `dataset add` and phase5 dataset selector boundary:
  `p7-dataset-cancel-esc`, `p7-dataset-cancel-ctrl-c`, and logs/metrics/traces
  selections with actual PUT effects.
- Phase1 `sql list` and phase4 saved picker boundary: `p7-saved-quit-ctrl-c`,
  `p7-saved-empty-output`, filter `a`/Enter apply and `b` quit, malformed/empty/
  non-2xx menu fetch and Ctrl+V filtering.
- Phase3/phase4 SQL interactive guards: `p7-sql-promoted-short-empty`,
  `short-query`, `long-empty`, `long-true-query`, `ignored-yaml`,
  `ignored-save-as`, `compact-json`; dataset placeholder/editor precedence/
  time overlay, resize, signals and clipboard cases supplement those paths.
- Phase4 PromQL interactive guards: `p7-promql-promoted-short`, `compact-json`,
  `long-true`; empty editor, builder, table/chart/unwrap, resize, signals and
  clipboard cases supplement those paths.
- Phase2 credential/default/removal/logout boundaries: empty/invalid/valid
  credential cancellation, default selection/cancellation, cooked decline/
  confirmation, replacement cancellation and dropped profile clipboard paste.

The parent migrated the active guard definitions. Historical boundary
definitions/manifests remain documentary and were not edited by this owner.
No ordinary noninteractive case was removed or reclassified here.

## Observed Go contracts and documentary qualifications

Early observations were written to
`/home/ajs/.cache/pb-phase7/terminal-oracle.md` while terminal implementation was
still blocked on shell/build access. They remain available with raw captures.

- Login Ctrl+C returns normal exit 0 and restores full termios/cursor. External
  SIGINT and SIGTERM also exit normally with status 0 and restore. SIGHUP dies
  by signal 1 with raw mode/cursor left acquired. Go SIGQUIT exits normally 2
  with a runtime stack dump and no restoration. Do not substitute numeric
  130/143 or universal signal cleanup for these measured behaviors.
- Sampled empty SQL/PromQL external SIGINT/SIGTERM exit 0 and restore the main
  screen, cursor and full termios. Alternate final-frame evidence is retained.
- Without a controlling TTY, login/credentials/SQL/PromQL/saved menu try
  `/dev/tty`, then exit 1 with `could not open a new TTY: open /dev/tty: no such
  device or address`. Login, credentials and PromQL include usage; SQL/saved
  do not. Saved filters are fetched before TTY acquisition. Credential startup
  failure does not write configuration.
- Cooked logout confirmation remains echo/canonical outside raw mode. Decline
  prints `Logout canceled`; confirmation removes the profile. Replacement
  selection cancellation still removes the original and writes empty default.
- Credential `Values()` returns BOTH empty fields when either field is invalid.
  A lone typed username is visible but discarded on cancellation; a valid
  username/password pair is saved even on cancellation. This qualifies the
  plan's generic “partial credentials” statement with executable evidence.
  Initial invalid Submit renders `[ X ]` instead of `Submit`.
- Profile picker shows a filter draft while retaining all items. Its reducer
  forwards only KeyMsg to list.Update; asynchronous filter/clipboard results
  are dropped. Individually typed `alph` does not filter beta away. Do not
  silently implement functioning filtering as a historical-source inference.
  Map-derived item permutations remain documentary cell mismatches.
- Saved-menu `a` and Enter apply before filter input sees them; `b` quits.
  A decodable non-2xx array still populates the menu. Applied SQL is printed
  after leaving the alternate screen and executed through ordinary SQL.
- All seven promoted SQL flag forms run interactively, including invalid
  output `yaml`, compact `-ojson`, and `--save-as` (ignored by interactive
  execution). Three PromQL flag forms and `sql list -o ''` also enter the UI.
- Role reader selection sends GET `/api/v1/roles`, then exactly
  `[{"privilege":"reader"}]` to PUT `/api/v1/role/new`. Dataset selection
  cancels without mutation, or sends a PUT with the chosen telemetry type.
- SQL initial query and home discovery arrive in either order in Go-versus-Go
  functional sessions. Schema follows home discovery. The bounded initial
  effect graph, rather than blanket request sorting, captures this behavior.

## Tests and retained acquisition history

Initial owned test run: 22 tests passed in 3.005 s. It covered true-color and
indexed styles, additional attributes, fragmented Unicode, alternate screen,
scrollback, cursor/query replies, unsupported controls, real raw restoration,
error restoration, actual signal death versus numeric exit, raw Ctrl+C,
non-TTY/no controlling terminal, resize, invalid timeout/readiness, fixture
effects, strict cell differences, session-unit statistics and quiet-window guard.
Later test additions/results are recorded below as they run.

Functional artifact roots under `/home/ajs/.cache/pb-phase7/`:

- `oracle-lifecycle-1`: login/signal/non-TTY early acquisition; stopped by an
  owned config replacement type bug after non-TTY login. Raw successes remain.
- `oracle-lifecycle-2`: non-TTY five flows, cooked decline and empty SQL/PromQL.
  Credential attempts timed out because the ready predicate expected Submit;
  corrected to `[ X ]` evidence in later captures.
- `oracle-functional-1`: dataset choices/cancel, signals, saved key precedence/
  non-2xx, credentials and profile/logout. Role fixture initially returned an
  array instead of the required map; corrected later. Lone-username expectation
  was corrected to match measured `Values()` policy.
- `oracle-promoted-1`: all selected promoted flags, valid/invalid credential
  cancellation, reader role, saved empty output passed independent assertions.
- `oracle-workflows-1` / `oracle-workflows-2`: extended login and editor flows.
  Corrected owned predicates for lowercase time hints and automatic results
  focus; fixed normalization of the deterministic no-server URL. Profile
  filtering non-completion remains measured behavior, not a predicate to force.
- `oracle-picker-cancel-1`: successful remove/logout replacement cancellation.
- `oracle-aa-functional-1`: Go-versus-Go; screens passed and SQL arrival order
  exposed the legitimate initial fan-out. `oracle-aa-functional-2`: all four
  dark/light empty/query SQL self-comparisons passed after that scoped graph.
- `oracle-clipboard-1`: initial Ctrl+V corpus acquisition (results appended below).

The early acquisition above ran before the candidate became available. Later
parent-built debug1 evidence is described below. No successful live backend,
benchmark, observer-floor campaign or profiler result is claimed. Timed work
still awaits the coordinator's phase6 quiet window. Linux emulator evidence
does not pass six-platform release gates or stderr-TTY spinner coverage.

## Final implementation checkpoint

The corpus contains 89 dark cases (74 gate, 15 documentary), mirrored in light
for 178 definitions. The harness does not claim all definitions have completed
candidate acceptance. Shared historical harness source/manifests remain untouched;
parent Cargo, dispatch, services and candidate builds remain parent-owned.

Parent's `differential-debug1-full` retained 76 complete independent Go/Rust
pairs. Pure re-evaluation in `differential-debug1-full-reduced1` found 60 gate
passes under the then-current contract and 16 documentary entries; only SIGQUIT
termination/stderr differed. Seven retained Go startup sessions showed three
PromQL initial-effect permutations, documented in cache `promql-startup-order.md`.
The narrow startup graph correction removed request-order false positives;
headers, bodies, multiplicity, chains and all later order stayed exact.
SIGQUIT was subsequently promoted to a strict gate (exit2, no restoration, exact
`SIGQUIT: quit` first line). The old debug1 mismatch remains in its raw report;
no new candidate was exercised after the terminal owner began the handoff fixes.

Additional Go-only acquisitions passed:

- `oracle-effects-2`: role conflict and exact reader mutation, dataset telemetry
  headers, partial 500+150 SQL windows/cache return, all eight clipboard cases.
  SQL/PromQL execute Ctrl+V reads but drop paste results. Go delayed quit delivers
  SIGHUP to the clipboard helper; no living-helper leak was observed. The initial
  insertion expectations were corrected with raw failed attempts retained.
- `oracle-response-controls-1`: seven empty/error/Alt+Enter/delayed-response
  cases. SQL rejects an old window; PromQL allows an old result to overwrite a
  later one. Those are preservation assertions, not proposed fixes.
- `oracle-lifecycle-n2-1`: actual empty SQL/PromQL Ctrl+Left hang, SIGTERM twice,
  unchanged full styled frames and raw/alternate modes after both, then SIGQUIT.
  TERM gaps were 250.414 ms / 239.068 ms. Both exit2 without restoration. The
  normal login SIGQUIT case passed the same strict disposition/prefix gate.
- `oracle-fullscreen-sigquit-1`: normal SQL/PromQL SIGQUIT exit2 with raw/alt
  left acquired and `SIGQUIT: quit` on stderr.
- `oracle-wide-view-1`: PromQL legend View panic at process width92 (inner88),
  20 CJK job-label runes; exact `[:38] with capacity32`, exit0, restoration, no
  failed legend frame. SQL's first predicate incorrectly demanded 20 contiguous
  visible runes despite clipping, so that timeout remains an invalid capture.
- `oracle-wide-sql-2`: SQL at width176 renders clipped CJK dataset and exits0
  with restoration, no caught panic. An initial raw-NUL expectation was invalid:
  the process stream contains no NULs even though the helper fills internal
  runes. Pure validation with the corrected independent characterization passes;
  no extra candidate or timing session was run.

The wide-text cases were bounded to these two definitions. The parent explicitly
accepted that SQL's maximum sidebar slice `[:32]` fits capacity32, while PromQL's
legend `[:38]` does not; no fabricated SQL panic requirement remains.

Measurement seams requested in `docs/phase7-measurement.md` are implemented and
directly coordinated with the services lane: optional `execute_fn` on sample and
floor, optional `proc_sampler`, `continue_running`, `readiness_fn`, and
`observer_config` on execute, plus `stable_since_monotonic_ns` in frame stamps.
Defaults preserve the ordinary observer; minimal histories do not remove native
wait4 or lifecycle evidence. Deadline stop kills/reaps and preserves an invalid
result. No nested flock or private driver was added. Protocol/unit testing used
an injected fake floor executor; no real timed observer-floor session ran.

Owned tests: 22 initially, then 27, then 30, then 34 passed as the corpus, strict
signal contract and collector seams landed. Final run: **35 tests passed in
5.935 s**, including the narrow runtime-stack exception. Owned Python files
compile, all five owned files have no trailing whitespace, and scoped
`git diff --check` passes. No Rust file is owned by this lane, so no rustfmt or
Cargo command was run. Local final checkpoint: cache `harness-final.json`;
corrected retained wide-case validation: `wide-view-validated.json`.

## Release1 spinner and bounded PromQL gap controls

No comparator or corpus source changed during the coordinator's immutable
release1 replay. After its completion, a narrow animation exception was promoted
for `old-request-pending` in SQL delayed-window and PromQL delayed-result cases,
both themes. Four independent pinned Go sessions in `oracle-spinner-phase-1`
observed backslash, dash, backslash, dash at row18 col26, identical attributes.
The source's `spinner.Line` cycles `| / - \\` every100ms; the readiness observer
accepts a complete styled frame stable80ms, so animation phase is not deterministic.
Strict Go-vs-Go comparison failed two of three pairs only at this checkpoint;
the staged exception clears those and the release1 SQL pending mismatch.

The exception recognizes exactly one visible alternate-screen Line glyph
immediately preceded by a space and followed by exact ` fetching...`. It changes
only that cell's data at explicitly opted-in checkpoints. Attributes, coordinates,
cursor, other glyphs and scrollback remain exact; final frames never receive it.
Missing/unrecognized/ambiguous active spinners fail independent validation.

`styled-release1-reduced-spinner1/report.json` is a process-free reduction of
all178 retained pairs. It records current harness hashes, original report hash,
and captured/active case hashes. Only four case contracts changed (the two
pending cases in both themes). Original `styled-release1/report.json` is retained.
Counts: **148 gates, 146 pass, 2 fail** (SQL wide CJK dataset, dark/light);
**30 documentary cases, all30 compare cleanly**. Documentary disposition is
retained regardless of this particular replay's clean comparisons. This reduction
does not claim the later SQL source fix passed; the retained candidate is release1.

Six unique new PromQL cases are promoted in both themes. The expanded corpus is
**190 cases:160 gates and30 documentary** (95/theme:80 gates,15 documentary).
`oracle-promql-gaps-2` contains actual pinned Go dark-theme sessions; all six pass
current independent assertions including inspector/label text, termios/config,
requests and chart/value cell positions. Candidate replay and light-theme Go
acquisition remain for the coordinator's next rebuilt binary; no candidate
session or build ran in this lane.

| Gap and case suffix | Size/TZ | Actual Go screen/effect |
| --- | --- | --- |
| `g4-z` |140x35, America/New_York|Sidebar00:00/00:10 UTC+00:00; picker2024-Jan-01 and Local UTC+00:00; request UTC start/end unchanged|
| `g4-fixed-zone` |140x35, America/New_York|Sidebar/picker retain UTC+05:30; wire start/end2023-12-31T18:30/18:40Z|
| `g10-equal-time-merge` |100x30, UTC|Two metrics a/b, each7 points with same job x/timestamps; first Right value1 and bottom dot(21,5); second Right minute00:01 value1|
| `g7-infinite-plot-amd64` |100x30, UTC|1,+Inf,2 chart dips to bottom; first Right value+InfM, dot(21,51)|
| `g7-huge-timestamp-amd64` |100x30, UTC|Instant timestamp1e30 TIME column15:30:08|
| `g3-duplicate-result-merge` |100x30, UTC|Raw duplicate JSON result arrays retain up/job a then add instance i; table up{instance="i", job="a"}, value3 at(17,83)|

PromQL/effects owners coordinated exact fixture contracts directly. G7 controls
are explicitly linux/amd64 oracle cases; untested arm64 behavior is not inferred
as acceptance evidence. Raw main Go binary SHA256 is
`fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`;
production `git diff 07af390 -- main.go cmd pkg` remained empty.

Retained first attempts in `oracle-promql-gaps-1` exposed two incorrect hand
expectations, without weakening any parity gate: at100x30 the sidebar clips the
last two offset digits, so G4 now uses140x35; huge timestamp Go TIME is15:30:08,
not08:29:52. Ancillary scratch Go1.27.1 `promql-huge-time-oracle.{go,txt}` confirms
`math.Modf(1e30)` gives fraction0, secMinInt64/nsec0 and `time.Unix` formats
`292277026596-12-04T15:30:08Z`. The actual pinned Go1.25.4 PTY is authoritative.
A mistaken second-nanosecond-overflow explanation was promptly corrected to the
PromQL owner; source explicitly uses math.Modf.

G5 did not merit a new visible gate. `oracle-promql-g5-visibility-1` ran two
independent Go processes: hex samples0x1.8p-1075/0x1p-1073 versus payload0/1e-323
modeling documented old Rust rounding. Full styled chart/inspector/final frames,
requests and lifecycle compare identically; both show value0. This is Go-vs-Go
payload evidence, not a candidate comparison. Exact hex rounding remains the
PromQL owner's numeric unit contract. No benchmark, profiler, observer-floor
measurement or performance session ran.

Post-promotion verification: **38 owned tests passed in6.976s**; explicit negative
checks preserve spinner style differences, reject unrelated/ambiguous glyphs and
keep scrollback strict. Independent expected chart cells fail when moved/removed.
No new tool or runtime dependency was required (existing pinned Python/pyte/wcwidth).

## Measurement review r1: initial owned H1/H2/M4 responses (historical)

This initial helper implementation was not accepted by the subsequent source-only
helper review. The current helper-r1 response below supersedes its launch/reap,
error and provenance details while retaining this original test evidence.

Read `docs/phase7-review-measurement-r1.md` in full. This response fixes the
harness-owned source findings; it does not declare a performance campaign ready.
The services lane owns the benchmark/profiler changes and the required functional
Go/candidate collector preflight. No candidate build/session, Go CLI session,
observer-floor measurement, timing campaign or profiler ran in this response.
Only synthetic child correctness tests and compilation of the owned helper ran.

**H1: remove the Python fork rusage floor.** New `harness/phase7_spawn.c` is a
small native supervisor. `spawn_helper()` compiles it with system `cc` and
`-std=c11 -O2 -Wall -Wextra -Werror` into a source-addressed disk-cache binary,
using atomic replacement. Compilation and hashing happen before the capture's
start stamp. The observer uses `os.posix_spawn`; after its fresh executable image
is established, the supervisor forks the actual CLI. Python never forks the
measured process. There is no shell wrapper and no alternative rusage fallback.

The CLI keeps its actual command/argv/environment/cwd. It calls setsid, acquires
the controlling PTY for PTY cases, and has isolated pipe stdin/stdout for nonTTY
cases. It owns the session/group and receives keys at its PTY; signals and resizes
target the process group led by its actual PID. A CLOEXEC handshake with the supervisor completes before the PID is
published, so `/proc` probes cannot sample the native pre-exec image. Private
protocol fd3 is closed in the CLI; original observer descriptors are closed by
spawn actions. The supervisor closes its copies of CLI streams and does not
retain the PTY or contaminate stdout/stderr. Exec/setup failure is CLI exit127
with the existing `phase7 exec failure:` diagnostic prefix.

The native supervisor calls wait4 on that exact child and emits raw wait status,
CLOCK_MONOTONIC reap time and native rusage fields on the private pipe. The
observer preserves signal-vs-numeric-exit and core flags, and reaps the supervisor
separately without using its resource counters. `resource_source` is
`native_child_wait4`; `native_spawn` records helper binary/source hashes, compiler
path/flags, actual CLI/supervisor PIDs and native fork-start stamp. Resource scope
includes the small native child's setsid/TIOCSCTTY/chdir/exec work and CLI lifetime;
supervisor CPU/RSS, Python image and fixture counters are excluded. Native wait4
retains its usual descendant accounting for children the CLI itself waits for.

The unchanged observer start stamp still precedes spawn, while the reap stamp is
now from native wait4. `observed_process_wall_ms` therefore remains an observer
start-to-CLI-reap lifecycle quantity, including spawn, scripted waits/stability
and idle; it is not pure computation. Unexpected protocol failure raises rather
than inventing resource values. Deadline/observer exceptions kill the CLI group,
wait for native reaping, then reap the supervisor. Linux parent-death notification
also instructs the supervisor to kill/reap its CLI if the observer disappears.

**H2: skip exited/missing-memory samples.** `proc_sample()` reads stat first and
returns None for Z/X/x, absent VmRSS/VmHWM, inaccessible counters or malformed
fields. The zero-memory default is removed. It collects memory, IO, faults,
main-thread context switches, CPU ticks and FD count, then rereads stat to reject
an exit/PID-identity change during the sample. A live zero counter stays a genuine
zero; missing values never become zero. The service lane was explicitly asked to
reuse these fields directly in `native_proc_sample` instead of rereading status
and recreating the exit race.

**M4: first expected is first ever.** Per action, `first_useful_monotonic_ns`
and explicit `first_ever_match_monotonic_ns` record the first observation satisfying
the expected predicates, without reset when predicates subsequently fail.
`stable_since_monotonic_ns` resets on a predicate failure or frame change and
still requires an uninterrupted80ms qualifying complete styled-frame interval.
The final named frame is the accepted stable frame; stdout/chunk evidence retains
prior rendering. Both stamp meanings are separate from dispatch and last-output
stamps. A first-match timestamp cannot itself protect against an old frame:
ordering it after action dispatch is true by construction. The bench owner owns
removal of that tautological claim and the actual fresh-output/resize-snapshot
readiness predicates.

Direct services-lane coordination also covers H3: benchmark runtime waits must
avoid fixture request-count polling; post-hoc checks must use exact causal
request/milestone counts. Existing functional corpus request waits remain intact.
The bench/profiler source manifest must include `phase7_spawn.c` and preserve
native helper provenance. These changes are not implemented by editing the other
owner's files. Functional Go and candidate collector cases are still required
before the coordinator can authorize timing.

Validation: **44 owned tests pass in10.908s**, log
`/home/ajs/.cache/pb-phase7/harness-measurement-fix-final.log`. Six added tests cover
zombie/missing-memory and mid-read exit samples; qualifying-predicate flicker with
first-ever versus stable timestamps; a64MiB resident observer whose small child
RSS stays below the observer image; actual CLI PID/session/group/cwd/env and no
protocol FD; exec127 diagnostics; and CLI/supervisor reaping after an injected
observer exception. Existing tests exercise PTY restoration, raw Ctrl+C, real
signals versus numeric exits, resize, nonTTY, clipboard process ownership and
deadline failure evidence through the new native boundary. The memory assertion
is a one-child correctness regression check, not a throughput/latency/RSS campaign
or performance acceptance claim. Parent independent helper/bench review remains.

## Native helper review r1: response ready for fresh r2

`docs/phase7-review-spawn-helper-r1.md` was read in full. Its reviewer executed
nothing (shell EDQUOT); those findings are source/kernel reasoning, not executed
review evidence. The owner implemented and executed the checks below. The
coordinator did not accept r1. Fresh independent r2, including the final Opus
review, remains required; this response does not assert review acceptance.
Only `phase7_spawn.c`, `phase7.py`, `test_phase7.py` and this doc changed here.
Production sources, shared phase5 meter and terminal byte/cell comparison logic
were not changed.

**H1: reserve the real PID until observer release.** The helper's private fd3 is
now a duplex socket. Protocol2 sends one `spawn` or `exec_error`, then `exited`
after `waitid(WEXITED|WNOWAIT)`. The CLI remains an unreaped zombie; its PID/group
cannot be recycled during normal signal, sampling, drain and descendant checks.
The observer completes those operations before releasing by socket write-side
EOF (a release byte also works). Only then does the helper call exact wait4 and
send `exit` with raw status/rusage. Native signal handlers are blocked across
wait4 and clearing the saved PID, removing the helper's own post-reap kill race.

The process-group leader's pidfd binds group signalling with Linux flag4
`PIDFD_SIGNAL_PROCESS_GROUP`, including cleanup after catastrophic supervisor
death. There is no unsafe numeric-PID/group fallback in the observer. ESRCH
becomes recorded unavailable-group evidence rather than ProcessLookupError.
Sampling/descendant observations additionally check the captured start-tick
identity before and after PID access. The pidfd signal targets the process group
led by the CLI PID; it can signal same-group clipboard/helpers as before.

This is an explicit harness-only Linux capability. Nominally the group flag
requires Linux6.9+, but headers/version are not the acceptance test.
`spawn_helper()` first compiles/resolves the helper, then performs a bounded
actual own-child probe before acquiring any CLI PTY/pipes. A native leader and
its same-group member both receive SIGUSR1 through the pidfd; the leader is
observed in reserved stateZ before release. Status0 and helper executable SHA
are recorded in `native_spawn.capability`, cached only for that executable within
the observer process. Unsupported syscall behavior fails closed with a clear
`unsupported Linux native capture capability` error; probe processes/descriptors
unwind, and no CLI acquisition follows. No production CLI portability is claimed.
The probe is functional signal evidence, not an observer-floor measurement.

`exited_monotonic_ns` is the native waitid observation stamp. The existing
`reaped_monotonic_ns` field is retained as a documented lifecycle-exit alias for
the collector seam; `native_wait4_reaped_monotonic_ns` separately identifies
post-release reap. The200ms drain and `observed_process_wall_ms` use exited,
never the gated reap time. The services lane agreed this protocol before edits
and updated collector checks for the explicit alias and exec-error distinction.

**M1: exception-safe, bounded ownership.** An ExitStack registers each PTY/pipe
FD immediately after acquisition, using distinct close tokens that prevent
reuse/double-close bugs. It also owns unconditional bounded native cleanup.
Spawn/handshake/acquisition exceptions unwind all earlier resources. Cleanup
collects errors without masking the original callback exception; supervisor
protocol death produces an invalid result with unknown CLI termination/resource
fields instead of invented values. Native wait phases each have a2s deadline;
supervisor shutdown polls waitpid(WNOHANG), escalates to SIGKILL and remains
bounded (up to4s for that phase; exceptional full unwind up to roughly8s).
Every descriptor closure still runs if a native wait/reap fails.

Pipe stdin is nonblocking and driven by the same selector, in bounded writes.
No reader cannot block the observer: deadlines still fire. Closed/early-exiting
readers retain explicit failure evidence, written/expected byte counts and full
captured streams. Only successful writes increment the count. The first added
regression test found that an EPIPE path counted all input as written; that was
corrected, with the failed test log retained as `harness-helper-r2-tests-1.log`.
A late signal/deadline against an exited, reserved CLI and an immediate-exit
output-limit case all return evidence without ProcessLookupError.

The helper receives the expected observer PID as an argument and verifies it
before/after PR_SET_PDEATHSIG, removing the self-referential parent check. The
CLI also receives parent-death SIGKILL if its supervisor dies. Linux binds
PDEATHSIG to the spawning thread's lifetime: the collector/profiler must keep
that thread alive until capture cleanup completes. This is not an assertion
of arbitrary threaded-call portability.

**M2: errno handshake, not a fake CLI exit127.** The CLOEXEC startup pipe carries
structured stage/errno for signals, parent, setsid, ctty, chdir or exec failure.
Only empty successful-exec EOF emits `spawn`; failure emits `exec_error` and an
explicit `cli setup failure: <stage>`, followed by actual setup-child termination.
The setup child's rusage is never reported as CLI resources: CPU/RSS/faults/IO
fields and `resource_source` are None. `native_spawn.cli_pid` is None and the
private setup-child PID is separately recorded. A genuine executed CLI exit127
has no exec_error and retains native CLI wait4 resources. Diagnostics carry stage
and newline. PTY missing-cwd and pipe missing-executable regressions distinguish
these paths from real exit127.

**M3: coordinator-approved narrow fixture policy.** Prior phase7 manual fork
inherited ignored dispositions; phase5 `run.spawn_measured` restores
PIPE/XFZ/XFSZ. The coordinator explicitly approved defaulting
INT/TERM/HUP/QUIT plus those phase5 restored signals, and clearing the blocked
mask. Other ignored dispositions, such as USR1, remain inherited. This deliberately
differs from the reviewer's blanket-reset proposal: it makes named phase7 signal
gates deterministic across foreground/background launches without inventing a
universal user-CLI inheritance policy. Both posix_spawn and the native child
apply the explicit fixture defaults. Native signal-state tests verify selected
ignores absent, USR1 still ignored, SigBlk0 and unchanged observer dispositions.

Actual post-fix Go controls are retained in `native-helper-r2-go-quit`: ordinary
parent and parent ignoring QUIT/XFSZ each run the same pinned Go SQL SIGQUIT case.
Both pass independent gates, exit2 with `SIGQUIT: quit`, leave raw/alternate state
unrestored as required, and compare complete styled/request/config/lifecycle
evidence cleanly. No new diagnostic normalization was added. These are two
functional Go sessions, not candidate parity or performance evidence. Prior
`native-helper-functional-release1` three Go/release1 pairs remain pre-fix
smoke only; their passing result is not acceptance for this helper revision.

**L2/L3: IO and descendant accounting.** Exact native wait4 now includes
`block_input_operations`/`block_output_operations` (`ru_inblock`/`ru_oublock`).
They count block operations, not filesystem/network bytes. `/proc` IO remains
sampled, with unsampled tails; final zombie IO aggregation was not implemented
or asserted without host-kernel evidence. Wait4 includes waited-for descendants:
CPU/faults/context counters accumulate, peak RSS is a maximum. Observed descendant
identities and attribution are recorded next to metrics; observed children or
clipboard helpers set `descendant_comparison_eligible=False`. Absence in a50ms
sample does not prove no short-lived descendant existed. The collector owns
eligibility/trigger policy, and the services lane was notified explicitly.

**L4: exact build provenance.** Helper cache key includes source SHA, flags,
resolved compiler path and full `cc --version` output, all preserved in the
manifest along with binary SHA and protocol2. A real compiler-wrapper test
changes the version while retaining source/path and verifies two fresh builds
and distinct cache paths. Non-Linux fails clearly before CLI acquisition;
Linux rusage RSS units are bytes converted fromKiB, with no Darwin branch.

Current owned check: **57 tests passed in8.849s**, log
`/home/ajs/.cache/pb-phase7/harness-helper-r2-final-57.log`. Real lifecycle tests
cover WNOWAIT/Z retention and release, PID-bound signal/deadline dispatch at exit,
output-limit immediate exit, partial acquisition, failed spawn, handshake timeout,
large/no-reader/closed-reader stdin, supervisor SIGKILL, original exception
preservation, no leaked observer FDs, PTY and pipe64MiB observer isolation, errno
vs real127, narrow signal inheritance, actual group capability/fail-before-CLI,
waited-for descendants/block IO and compiler-cache invalidation. Existing raw
terminal/signal/resize/clipboard tests still pass. Compiler/probe builds concern
only the owned helper. No candidate build/session, timing campaign, profiler or
observer-floor measurement ran here.

Remaining unknowns are explicit: independent r2/final Opus acceptance is pending;
post-fix candidate collector preflight and a separately authorized floor are still
needed. On catastrophic supervisor SIGKILL the CLI is killed via PDEATHSIG/pidfd
and has no recoverable native wait4 report; its eventual reaping belongs to the
kernel adopter. The test observes it absent orZ, reaps the owned supervisor and
checks descriptor cleanup, without asserting an adopter deadline or inventing
CLI termination/resource counters. Children that escape the CLI's group and
short-lived descendants missed by sampling are outside the claimed group/
observation coverage. These qualifications are retained for fresh review.

The observer itself must retain default SIGCHLD child ownership. Ignored/custom
SIGCHLD would allow automatic or external reaping of the owned supervisor,
so it is rejected before CLI acquisition without resetting it. This is an
observer prerequisite, separate from the narrow CLI fixture policy. An
additional test verifies the rejection leaves SIGCHLD ignored. The two Go
controls used default observer SIGCHLD and the same native helper revision;
they preceded this final observer-only guard.

## Frozen resolver and observer integration

This bounded follow-up changes only `harness/phase7.py`, `harness/test_phase7.py`
and this document. `harness/phase7_spawn.c` is unchanged, SHA256
`0279d5fb06e4f38adc32004c152fc42c94194d59f234ee5a9c5694f74a6d31f4`.
Earlier helper evidence and reports remain retained; this section supersedes
the earlier resolver/cache description, not the native lifecycle protocol.

The agreed backward-compatible signatures are `spawn_helper(build=True, *,
probe=True)` and `execute(..., spawn_helper_build=True)`. Preparation uses
`probe=True` before `QuietWindow`: it snapshots compiler version under the
resolved compiler path and compiler-file SHA256, and caches the actual own-child
pidfd capability proof under the helper ELF SHA256. `build=False` prohibits
compilation and cache repair but its default `probe=True` still allows preparation
probes. Capture adapters pass `spawn_helper_build=False`, which resolves with
`build=False, probe=False`; both settings are necessary for the strict path.
Bench and profiler owners confirmed these exact names and integrated their calls.

The build key binds source SHA256, resolved compiler path, compiler-file SHA256,
full prepared compiler version and exact flags. The manifest also binds the
helper's path, ELF SHA256, build key, schema1 and protocol2. Each resolve verifies
the current source/compiler/helper bytes and manifest. Missing preparation,
compiler identity drift, missing executable/manifest, invalid or mismatched
manifest, or missing capability proof fails before CLI descriptor acquisition.
Strict resolution performs read-only identity checks and reuses in-memory
compiler/capability caches; it launches no compiler or probe, creates no directory,
and writes no binary or sidecar. Its read/hash overhead precedes the observer's
start stamp. Cached snapshots belong to the preparing Python process; another
process must prepare independently. Changed external inputs to a compiler wrapper
cannot be inferred from unchanged wrapper bytes without a new preparation probe.

`observer_config` additionally accepts boolean `record_descendants`, default
`True`. `False` suppresses optional 50ms descendant observation only. Required
PID-bound group signalling, held-exit release, native wait4 and final cleanup run
independently. The metric `descendant_comparison_eligible` is false when observation
is disabled; an empty optional list cannot establish descendant absence. The real
test spawns a same-group child ignoring HUP, lets its CLI parent exit without
reaping it, and checks both on/off modes: only observation differs, while group
cleanup leaves the child absent or zombie and reports no native cleanup issues.
Kernel-adopter reaping is still outside the claimed deadline.

Scoped owned suite: **64 tests passed in 10.079s**; log
`/home/ajs/.cache/pb-phase7/harness-nobuild-integration-tests-final.log`.
New coverage includes successful strict native execution with compiler/probe/write
routes forbidden, actual compiler-byte drift, version drift, manifest tampering,
missing preparation, missing capability proof, cache disappearance after preflight,
missing-directory preservation and actual descendant on/off cleanup. Services
reported their separate 40 bench and 13 profiler integration tests passing; these
were not executed by this lane. No Go/candidate session, candidate build, timing,
observer-floor measurement or profiler ran in this follow-up. Synthetic helper
lifecycle tests and preparation of the owned native helper are functional checks.

Exact three-file delta, source/helper/provenance hashes and test log are frozen in
`/home/ajs/.cache/pb-phase7/harness-nobuild-integration-freeze.json` and
`/home/ajs/.cache/pb-phase7/harness-nobuild-integration.diff`. Independent review
and the services-owned final Go/candidate functional preflight remain required;
passing these owned tests does not imply review acceptance or authorize timing.

## Historical release3 wide-legend checkpoint contract

The parent accepted the independently verified evidence and staged patch
SHA256 `8501151a48bd1f5a460ac5bd917b97b98475bec780d32c8400818c58eb62419b`.
At release3, only `p7-promql-wide-legend-view-panic-light` opted into the existing loading
phase exception at `before-view-panic`. The dark definition was unopted at that checkpoint; the combined-source successor below adds actual dark A/A evidence.
The exact source change is two lines in the theme-expanded workload definitions;
the comparator, native helper and production code are unchanged.

The release3 light frame differs only at zero-based row21/column39: pinned Go
`\\` versus Rust `-`, foreground `3a3a8c`, background `default`, all attributes
false, equal cursor and empty scrollback. Two new actual Go light checkpoints
show `-`; compared with the preserved release3 Go `\\`, each A/A comparison
fails only at the same checkpoint cell. Four new dark sessions and the preserved
dark reference compare cleanly. All six new Go sessions pass independent
validation: exit0, restored terminal, expected slice panic and restoration
diagnostics, unchanged config, fetching last alternate frame and no failed CJK
legend content. Raw artifacts and provenance remain in
`/home/ajs/.cache/pb-phase7/oracle-wide-legend-release3`.

Two durable regression tests verify the exact light-only corpus definition and
styled-frame comparison, including negative controls for attributes/colors,
positions, label, cursor, scrollback, other checkpoints, final frame, panic
diagnostic, exit, restoration and failed-view content. **All12 terminal tests
pass in0.515s** (`accepted-owned-tests.log`); the five artifact tests also pass.
These checks use synthetic frames and retained captures, launching no CLI.

Original `styled-release3/report.json` and every original capture remain retained:
historically **160 gates,159 pass/1 mismatch;30 documentary,29 clean/1 mismatch**.
The staged preview is not a replacement for that report. A separate process-free
two-case reduction is written to `oracle-wide-legend-release3/accepted-reduction`;
the parent owns the new central190-case reduction and verification that exactly
one definition changed. The existing documentary logout mismatch receives no
new exception. No further CLI session, build, timing or profiler ran to promote
this contract. Final source hashes and exact owned delta are recorded in
`oracle-wide-legend-release3/accepted-freeze.json`.

## Keyed cache alias guard

The collector's interim Sol R2 response raised a shared resolver concern:
keyed binary/sidecar symlinks were followed during hashing/parsing, while the
literal keyed binary path was later executed. The authorized three-line fix
rejects `is_symlink()` for both keyed files before prepared validation and its
build fallback. Both existing-target and dangling aliases fail with
`prepared native helper cache symlink rejected`; they are neither followed nor
repaired, including with `build=True`. Regular-file preparation and cache
compilation retain their existing paths and flags. Public signatures, C helper,
comparator and production code are unchanged.

Strict `build=False, probe=False` still performs no compiler/capability probe,
compilation, cache write or CLI acquisition on rejection. Compiler aliases from
`PATH` retain the existing resolution to the compiler's canonical path. New inert
resolver fixtures verify both alias types and both build modes, fail-before-read
and fail-before-CLI behavior, unchanged files/links, ordinary keyed paths, resolved
compiler paths, and normal cache creation through a mocked compiler. The fixtures
provide explicitly synthetic cached identities/proofs; no fake proof enters a
real capture. **15 scoped tests pass in0.566s** (three resolver and12 terminal
tests), log `/home/ajs/.cache/pb-phase7/harness-cache-alias-review/owned-tests.log`.
No native helper execution, compiler probe/build, Go/Rust CLI, timing or profiler ran.

This is a check of the two keyed file path components, not atomic artifact
binding. Ordinary check-to-read/check-to-exec TOCTOU remains: another actor could
replace a file or directory after the checks and before hashing or execution.
The guard does not pin directory/file identities across that interval and does
not assert an atomic security guarantee. Exact source delta, unchanged native
and workload hashes, test evidence and consumed resolver source hash are frozen
in `/home/ajs/.cache/pb-phase7/harness-cache-alias-review/freeze.json`. The collector
owner is notified of the successor resolver hash; prior freeze reports and all
styled evidence remain retained.

## R3-H1 dispatch origin correction

The authorized follow-up implements the narrow correction from
`docs/phase7-review-measurement-opus-r3.md` R3-H1. Scripted action events now
add `dispatch_monotonic_ns`: key/send stamps immediately before `os.write`, after
substitution and UTF-8 encoding; resize stamps after `terminal.resize`, immediately
before the winsize/ioctl and SIGWINCH sequence; explicit signal stamps immediately
before group signalling. Readiness callbacks, emulator frame construction,
hashing, snapshots/deepcopy and emulator resizing therefore precede this origin.
The dispatch interval includes write/ioctl/signal sequence costs. It remains an
observer-side timestamp, not a child receipt, reducer or render-completion stamp.

`event.monotonic_ns` retains its original pre-readiness action-evaluation value.
The additive `evaluated_monotonic_ns` explicitly aliases that legacy value.
Checkpoint coordinates/screens, `ready_monotonic_ns`, first-match/stability
timestamps and the next `action_started_monotonic_ns` keep their existing
evaluation semantics and preaction baseline. Events that only checkpoint/wait or
release a fixture barrier do not gain a dispatch origin. Existing combined actions keep their execution order;
their scalar dispatch field records the last key/send, resize or signal dispatch.
None of the190 functional definitions combines these types. The services owner
confirmed that measured actions contain exactly one dispatch type, consume
`dispatch_monotonic_ns` and reject missing stamps rather than silently using the
legacy evaluation value. Historical functional captures remain retained.

Three new pure mock-clock regressions drive the real `execute/_execute` action
loop with inert descriptors, selector and native objects. Actual pyte frames,
deepcopies and resize operations run; deliberately injected logical work advances
only the mock clock. Tests cover named key, Unicode send, resize and signal. They
assert that all pre-dispatch work finishes before the stamped I/O boundary,
emulator resize is also excluded, the artificial post-dispatch interval stays
fixed, old evaluation/readiness/coordinate baselines remain intact, anchor events
lack a dispatch stamp, and the entire synthetic resource-metrics dictionaries
remain identical with/without injected work. Resource formulas and public
signatures are unchanged. No native process or real I/O is used by these tests.

**18 scoped tests pass in0.549s**: three dispatch, three resolver and12 terminal
tests, log `/home/ajs/.cache/pb-phase7/harness-dispatch-origin-r3/owned-tests-final.log`.
Separate in-memory negative controls restore the old evaluation origin and move
the resize stamp before emulator resize; the regressions reject both mutations.
Those expected failures are retained in `mutation-controls.log/json`; they do not
modify frozen source. The first development run's incorrect dependency stub is
also retained separately; the corrected fixture and final owned tests pass.

This is source/test evidence only: H1's actual overhead magnitude remains unknown,
and the artificial clock durations are not measurements. No Go/candidate session,
native execution, compiler probe/build, profiler, campaign, floor or lock ran.
Comparator, workloads/contracts, C/helper and production code are unchanged.
Prior hashes/reports remain retained; exact new hashes, source delta, seam and
checks are frozen in
`/home/ajs/.cache/pb-phase7/harness-dispatch-origin-r3/freeze.json`. The collector
owner consumes the successor source hash. Full helper-suite execution and any
fresh functional collector preflight remain parent-owned.

## R4-L1 opt-in resize signal policy

`execute(..., resize_signal_policy='legacy')` is backward compatible and keeps
the styled corpus's emulator resize, dispatch stamp, winsize ioctl and explicit
PID-bound SIGWINCH. `ioctl-only-changed` is the opt-in measurement policy: emulator
resize, existing dispatch stamp, then winsize ioctl only. The PTY kernel's
foreground-group notification supplies SIGWINCH; there is no explicit signal or
numeric PID/group fallback in this branch. Result `resize_signal_policy` records
the applied policy, and each resize event records `resize_signal_source` as
`ioctl+explicit` or `ioctl-only`. These source labels describe the dispatch path,
not an assumed count of delivered signals. Standalone signal actions are unchanged.

Changed-only validates the initial geometry and every scripted resize before
dependencies/helper/probe, PTY or CLI acquisition. Geometries must be list/tuple
pairs of non-bool integers, width/height each in1..65535 (the winsize unsigned16
range). Validation follows all ordered resize geometries and rejects unchanged
dimensions relative to the immediately preceding geometry, including late-script
no-ops/errors. Only geometry is prevalidated: unrelated action errors retain their
existing execution behavior. Legacy adds no geometry validation, including keeping
its existing same-size explicit-refresh path. No production flag was added.

The services owner confirmed the exact keyword/result/event fields. Its consumer
binds the opt-in policy/source/dimensions to case, result, preflight and decoder
evidence; only matching changed-only controls can support resize interpretation.
The shared comparator, workloads/contracts, C/helper and production code remain
unchanged. This change does not resolve the separate decoder/readiness findings
or accept any timing campaign.

**22 pure/mock tests passed before the execution pause** (0.580s,
`harness-resize-policy-r4/pure-tests-1.log`). New checks cover complete geometry
validation before acquisition, positive unsigned16 boundaries, late invalid/no-op
dimensions, unknown policies, unrelated-action preservation, actual omitted legacy
default, legacy no-op dispatch, opt-in source/no explicit SIGWINCH, unchanged
dispatch placement/preaction frames/resource formulas, and mandatory final group
cleanup independent of resize policy.

All execution then paused on the root's Phase6 quiet coordination. No tests,
processes, probes, hash sweeps, builds or locks ran during that pause. Only source
and coordination work continued. Execution resumed only after explicit root
RELEASE, which also authorized the prepared owned native control.

**The native control passed** (one test/two policy sessions in2.016s), log
`harness-resize-policy-r4/native-control-root1.log`; full raw sessions, streams and
notification/size records are in `native-control-root1`. The own Python child
installs a WINCH counter and reads its actual PTY geometry; changed-only grow/shrink
observed exactly `100x30`, then `60x20`, with one notification each and no explicit
group WINCH calls. Legacy observed a duplicate grow notification and retained its
same-size explicit refresh; delivery counts may coalesce and are recorded rather
than assumed. Both sessions exit0, restore termios, retain exact native-child
wait4/resource evidence, and kill a same-group HUP-ignoring worker through the
held WNOWAIT leader's pidfd. Workers were absent at the final identity check.
This is a functional lifecycle/delivery control, not a timing measurement.

The guarded native test is skipped unless the root explicitly supplies
`PB_PHASE7_RUN_RESIZE_NATIVE=1`. It requires the existing compiled helper;
preparation performs the ordinary version/capability proof but never compiles.
Exact command (use a fresh output directory for any separately authorized repeat):

```bash
export TMPDIR=/home/ajs/.cache/pb-phase7/tmp
export GOTMPDIR=/home/ajs/.cache/pb-phase7/tmp
export CLAUDE_CODE_TMPDIR=/home/ajs/.cache/pb-phase7/tmp
PB_PHASE7_RUN_RESIZE_NATIVE=1 \
PB_PHASE7_RESIZE_CONTROL_OUT=/home/ajs/.cache/pb-phase7/harness-resize-policy-r4/native-control-root1 \
PYTHONPATH=harness /home/ajs/.cache/pb-phase7/harness-venv/bin/python \
  -m unittest test_phase7.ResizeNativeControlTests -v
```

Exact new hashes, owned source delta, post-release checks, native raw evidence and
unchanged helper/source identities are frozen in
`/home/ajs/.cache/pb-phase7/harness-resize-policy-r4/freeze.json`. Prior reports and
hashes remain retained. No Go/Rust CLI/candidate, compilation, profiler, timing
campaign or common lock ran. Full UI/retained replay and fresh independent review
remain parent-owned; no acceptance claim follows from these checks.

## Combined-source dark wide-legend checkpoint contract

Independent retained Go sessions from release5 and release6-combined show the same
single loading-spinner phase variation at row21/column39 in the dark theme.
[Fresh Opus review](phase7-review-dark-spinner-combined-opus-r1.md) accepts the
exact named-case extension of the existing rule. The comparator, helper,
production code, light contract and every other case remain unchanged.

The immutable original combined report has159/160 gate passes and29/30 clean
documentary comparisons. The separate process-free full190-case reduction has
160/160 gate passes and29/30 documentary comparisons, with exactly one changed
contract: the named dark `before-view-panic` checkpoint. Profile map order remains
documentary. Twelve terminal tests and five retained-artifact checks pass; negative
controls preserve styles, coordinates, cursor, scrollback, final frame, exact panic
diagnostics and lifecycle requirements. This rule does not establish spinner
animation cadence; that limitation is explicitly retained.

[Applied evidence](evidence/phase7/combined-dark-spinner-applied.json) links original
and reduced reports. No new CLI session or timing was used for this promotion.
