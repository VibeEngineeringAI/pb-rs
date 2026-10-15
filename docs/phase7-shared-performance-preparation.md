# Combined Phase 7 retained shared-command preparation

Preparation only, 2026-10-15. No product, fixture, native meter, test, build,
version/probe, performance collector, or common-lock command was executed. No
subagents were launched. Only this new document and the new preparation packet
were written. Root owns independent inspection, functional validation and any
later quiet execution. Tests and review lanes are currently active; no quiet
window is assumed. This is not performance acceptance.

The packet is `/home/ajs/.cache/pb-phase7/shared-performance-combined-preparation`.
`plan.json`, `partitions-and-commands.json` and `commands.txt` contain exact argv
arrays and shell-quoted commands. Future output directories do not exist yet.
Every collection, comparison, A/A, confirmation or optional comparison is a
separate bounded run; no command list authorizes executing them as one campaign.
All commands below are review-only until the blockers below are resolved.

The retained baseline is accepted Phase 5 review-fixed Rust `9bd38b…`, not the
pre-fix `ec9765…` and not the older Phase 4 `02b14a…`. Evidence:
`docs/evidence/phase7/baseline-manifest.json` names `4fcbd51` as the prior source
with no production changes to merged Phase 5; `harness/README.md:274` requires the
last accepted Rust binary as a second reference. The historical
`performance-shared.md` / `.json.gz` are pre-fix evidence (prior Phase 4, candidate
`7cda2ad`). `review-fixes-shared.json.gz` instead measured frozen `7cda2ad` against
accepted `4fcbd51`. The historical manifest's phrase “versus frozen 7cda2ad”
describes that past release decision; a new retained regression comparison must
advance the prior label to accepted `4fcbd51`, without rewriting old evidence.

Corrected merged Phase 6 `05867d…` can be an ADDITIONAL, separately labeled
comparison on these implemented commands. Its supplied provenance is exact
compiled-input merged-source equivalence and functional provenance, not
historical performance acceptance or full Cloud proof. Do not substitute it for
Phase 5, pool it with Phase 5, or add a fourth binary to the retained three-label
schedule. A separate three-label `go,prior_merged,rust` session may use the same
suite and options with `prior_merged=/home/ajs/.cache/pb-phase7/baselines/merged-phase6/pb-prior-merged`
and an entirely new `future-merged-prior/NN-case` output root; all bounds and order
limitations still apply. It cannot close the accepted-prior gate. The normal
candidate is used; Cloud/login/diagnostics variants are outside this plan.

Exact observed file identities (read/hash only):

- `/home/ajs/.cache/pb-phase7/baselines/pb-go`: `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314` (71807160 bytes).
- `/home/ajs/.cache/pb-phase7/baselines/pb-rust-phase5`: `9bd38b9db4f4f8e76cd43384cb024dddd4d122c6bc109d29a8afa483ae0e2c6d` (7617800 bytes).
- `/home/ajs/.cache/pb-phase7/baselines/merged-phase6/pb-prior-merged`: `05867d65b95ca1c8514d91add8ccfb77b56403c6f458c7bfc072652bbc832ee2` (10747592 bytes).
- `/home/ajs/.cache/pb-phase7/candidates/release6-combined/pb-normal`: `6d80054f5d87b081bfe51bed014a6f64c8e9a01c2c7f7fcb4c6434e221868443` (12513760 bytes).
- `/home/ajs/.cache/pb-phase7/candidates/release6-combined/manifest.json`: `c1aabd24afc0ed82e9bcd1a5028bc52c5cc507113a826119fabd91d7b63fc56c` (7923 bytes).
- `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/harness/run.py`: `b280d1295a4b351e6f2d838c56acaac58b2247bb77fc9881587bb7bbe849a076` (59785 bytes).
- `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/bin/pb-fixture`: `fa8b4711ce6c901fc2b277318337268b45953e9868d4bc1b24407b274cc9ec5c` (23569535 bytes).
- `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/bin/pb-meter`: `17c3b73491b2a31e465f58eda9f210000b5cd886a4ba9fc0764dc3576899127c` (17232 bytes).
- `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/harness/meter.c`: `5d7ce9dc2fa65b1ec7018fb863d36fd78d8b8535762acadaefc3f2f4e0f070e9` (6231 bytes).
- `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/docs/evidence/phase5/regression-suite.json`: `3c73172426a990d2c4771b74786245281095ed2640d525f680c8dffbde1e92bc` (13217 bytes).

`input-identities.json` also binds fixture Go source/tests, Go module files,
harness source and all cited evidence. `candidate-source-binding.json` checks
every file listed in the release6 manifest against current bytes and retains
its declared source snapshot/head. This checks the listed files, not unlisted
files, build dependency closure or a reproduced build. The supplied combined
head is `ea70f7ad7d501d59b26cf6397f6a0b1f28143fc4` with uncommitted Phase 7 inputs;
head alone is not the candidate identity. No git command was run. Later source
changes require root to rebind; do not relabel this snapshot as current.

The cached meter and fixture are byte-identical to both retained historical
reports and the review-fix manifest. This establishes preserved artifact
identity and historical measurement use, not newly reproduced compilation or a
verified binary-to-current-C-source build. The current meter source was read,
not compiled. The current runner is also byte-identical to the retained runner.

The suite below is exactly `docs/evidence/phase5/regression-suite.json`: all 15
objects, including fixture route bodies, generated values, expected hashes,
row counts, output caps and basic-auth configuration, equal both compressed
reports' stored specs. `historical-binding.json` preserves report identities and
methods; `inputs/regression-suite.json` is an exact byte copy. Its runner-canonical
full-suite hash is `662757e7be59b52008d2c27f62877310d13c7ec23f5d3292cf24b9e7e6bcfced`.
Each singleton suite copy and scenario hash is in the packet. Commands explicitly
use this retained full suite plus `--case`, never the generic generated suite.
There are five workloads without fixture processes and ten with HTTP fixtures;
there are no administration mutations, Flight, tail, TUI, PTY or login workloads.
No historical mutation or artifact directory is reused.

1. `p1-0000-root`: `(no arguments)`; no fixture process.

2. `p1-0001-root-help`: `--help`; no fixture process.

3. `p1-0003-version`: `version`; no fixture process.

4. `p1-0009-agent-json`: `agent -o json`; no fixture process.

5. `p2-status-basic-json`: `status -o json`; fresh loopback fixture.

6. `p2-profile-list-seeded-json`: `profile list -o json`; no fixture process.

7. `p2-dataset-list-basic-json`: `dataset list -o json`; fresh loopback fixture.

8. `p2-dataset-info-json`: `dataset info logs -o json`; fresh loopback fixture.

9. `p2-dataset-schema-json`: `dataset schema logs -o json`; fresh loopback fixture.

10. `p3-bench-rows-1-json`: `sql run 'SELECT * FROM logs LIMIT 1' --from 2024-01-01T00:00:00Z --to 2024-01-01T00:01:00Z -o json`; fresh loopback fixture.

11. `p3-bench-rows-500-json`: `sql run 'SELECT * FROM logs LIMIT 500' --from 2024-01-01T00:00:00Z --to 2024-01-01T00:01:00Z -o json`; fresh loopback fixture.

12. `p3-bench-rows-500-text`: `sql run 'SELECT * FROM logs LIMIT 500' --from 2024-01-01T00:00:00Z --to 2024-01-01T00:01:00Z`; fresh loopback fixture.

13. `p3-bench-rows-10000-json`: `sql run 'SELECT * FROM logs LIMIT 10000' --from 2024-01-01T00:00:00Z --to 2024-01-01T00:01:00Z -o json`; fresh loopback fixture.

14. `p3-bench-rows-10000-text`: `sql run 'SELECT * FROM logs LIMIT 10000' --from 2024-01-01T00:00:00Z --to 2024-01-01T00:01:00Z`; fresh loopback fixture.

15. `p3-bench-empty-json`: `sql run 'SELECT * FROM logs LIMIT 10' --from 2024-01-01T00:00:00Z --to 2024-01-01T00:01:00Z -o json`; fresh loopback fixture.

SQL limits 1, 500 and 10000 have exactly that many generated rows. The empty SQL
workload keeps its LIMIT 10 argv but returns `[]`. All use the exact 2024-01-01
00:00–00:01 UTC interval. JSON and text select distinct output formatting with
full expected stdout identities, not interchangeable throughput workloads.

The retained method has five excluded warmups followed by 30 fresh-process
samples per binary/workload: 15 warmup and 90 measured processes per three-label
workload, 225 warmup and 1350 measured processes overall. Initial insertion order
is `go,prior,rust`. `run.py:882` constructs ONE `random.Random(2026)` for the full
session; cases stay in suite order, rounds are -5 through 29, and each round
shuffles a fresh insertion-order label list and runs it sequentially. Fresh
HOME/config and a fresh fixture process are created per sample; there is no
administration reset-directory reuse. Warm-cache behavior has no cache flushing.
The runner persists warmup artifacts/issues, but not ordinary warmup metrics in
`runs`; root must inspect all warmup artifacts as well as 30 measured entries.

The observer remains captured pipe, `--stdout-sink pipe --read-delay-ms 0`,
32 MiB combined capture cap, raw SHA-256 and existing post-exit validation. SQL
arrays retain incremental FIRST-element validation while timed and full
count/order validation afterward. Do not turn tracking off or switch to file
sink/hash-only mode in the retained comparison. `isolated_env` fixes C locale,
UTC, dumb terminal, dark theme and disabled analytics, isolates HOME/XDG/tmp,
and omits inherited proxy/diagnostic variables. CLI stdin is `/dev/null`.
Process startup/fork/exec and loopback CLI work are measured; fixture startup,
config setup, trace fetch, artifact writes and cleanup are excluded from the
CLIENT metric but INCLUDED in the strict complete-run span.

A representative exact review-only command follows; `commands.txt` expands all
15 cases and the accepted-prior plus descriptive Go comparisons. `/usr/bin/python3`
is an explicit proposed launcher, not an asserted runtime version or provenance.
Root must bind its actual resolved executable, relevant standard-library source
identities and environment before later execution; this preparation made no
runtime/tool probes. The full-suite command is in `plan.json` for identity/order
review only and is not conservatively feasible under the complete-run bound.

```sh
/usr/bin/python3 /home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/harness/run.py run --suite /home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/docs/evidence/phase5/regression-suite.json --binary go=/home/ajs/.cache/pb-phase7/baselines/pb-go --binary prior=/home/ajs/.cache/pb-phase7/baselines/pb-rust-phase5 --binary rust=/home/ajs/.cache/pb-phase7/candidates/release6-combined/pb-normal --fixture /home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/bin/pb-fixture --meter /home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/bin/pb-meter --warmup 5 --repetitions 30 --timeout 10 --seed 2026 --stdout-sink pipe --read-delay-ms 0 --case p1-0000-root --out /home/ajs/.cache/pb-phase7/shared-performance-combined-preparation/future-primary/01-p1-0000-root
```

For that report the exact retained gates are:

```sh
/usr/bin/python3 /home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/harness/run.py compare /home/ajs/.cache/pb-phase7/shared-performance-combined-preparation/future-primary/01-p1-0000-root/report.json /home/ajs/.cache/pb-phase7/shared-performance-combined-preparation/future-primary/01-p1-0000-root/report.json --reference-binary prior --candidate-binary rust --metric wall_ms --max-regression-percent 10 --min-regression 1 --out /home/ajs/.cache/pb-phase7/shared-performance-combined-preparation/future-primary/01-p1-0000-root/gate-wall_ms.json
/usr/bin/python3 /home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/harness/run.py compare /home/ajs/.cache/pb-phase7/shared-performance-combined-preparation/future-primary/01-p1-0000-root/report.json /home/ajs/.cache/pb-phase7/shared-performance-combined-preparation/future-primary/01-p1-0000-root/report.json --reference-binary prior --candidate-binary rust --metric cpu_ms --max-regression-percent 10 --min-regression 1 --out /home/ajs/.cache/pb-phase7/shared-performance-combined-preparation/future-primary/01-p1-0000-root/gate-cpu_ms.json
/usr/bin/python3 /home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/harness/run.py compare /home/ajs/.cache/pb-phase7/shared-performance-combined-preparation/future-primary/01-p1-0000-root/report.json /home/ajs/.cache/pb-phase7/shared-performance-combined-preparation/future-primary/01-p1-0000-root/report.json --reference-binary prior --candidate-binary rust --metric peak_rss_bytes --max-regression-percent 20 --min-regression 2097152 --out /home/ajs/.cache/pb-phase7/shared-performance-combined-preparation/future-primary/01-p1-0000-root/gate-peak_rss_bytes.json
```

All three metrics compare marginal medians of `rust` against `prior`. A point
increase exceeding BOTH 10% and 1 ms elapsed/CPU, or BOTH 20% and 2097152 bytes
RSS, is an investigation screen. Thresholds are strict; equality does not cross.
The retained harness gate additionally requires the entire pointwise bootstrap
ratio interval above 1.10 or 1.20 respectively, AND median absolute delta above
1 ms or 2097152 bytes. It does NOT require the delta CI lower bound to exceed
the absolute allowance. Preserve both point screens and tool gate output;
a tool non-crossing is not equivalence or “always faster.”

`compare_samples` uses 2000 INDEPENDENT resamples of each implementation's values,
not paired ratios/differences despite interleaved collection. `run` inline wall
comparisons use seed 2026; the separate `compare` CLI does not expose a seed and
calls `compare_samples` with its actual default seed 1. Retain and label this
distinction rather than silently claiming the same intervals. p95 at n=30 and
all intervals are exploratory, pointwise, without simultaneous inference.

`compare` checks report completion/schema, matching case names/spec hashes,
contracts and warmup issues. It checks host system/release/machine/CPU/count/
affinity/governor/Python, runner, fixture/meter hashes, warmup, read-delay,
timeout and sink; an incompatible gate errors. Same-report comparisons avoid
cross-session identity mismatches but do not establish a quiet host.
It requires only ten available metric values, does not enforce exactly 30,
finite/nonnegative native counters, actual accounting correspondence, binary
acceptance identity, the seed/repetition equality between reports, end hashes,
quiet acknowledgement or a total deadline. Root must independently require all
30 valid values, expected labels/identities, stable contracts and good warmups,
no missing metrics, no truncated output or interrupted schedule. The supplied
suite hash check does not replace per-spec verification. Exit 0 means this gate
did not cross; 1 is compatibility failure, 2 setup/invalid comparison, 3 a
performance regression. Keep every output and exit status, including failures.
The zero-reference fallback in the existing tool is absolute CI lower>0; retain
it as tool behavior but do not call an undefined CPU/RSS ratio an AND pass or a
relative regression. Record it as unassessed for the relative rule and investigate.

Go comparisons are descriptive and must preserve all absolute medians/deltas,
ratios, uncertainty, resource provenance and compatibility. `commands.txt`
provides ungated candidate/Go and accepted-prior/Go commands for all three metrics.
Do not replace retained budgets with generic defaults, Phase 6's distinct
10%/2ms, 15%/2ms, 25%/8MiB budgets, or Phase 7 TUI decision envelopes.

The review-fix manifest also records a historically stronger secondary screen:
every primary workload/metric with a median increase >5% is rerun ALONE in a
fresh 5-warmup/30-sample interleaved session. Its failure requires confirmation
median >5% and whole ratio CI >1.05, without absolute allowance. Preserve this
as a separate retained historical acceptance rule, never silently substitute it
for the 10%/1ms or 20%/2MiB gates. Root must prospectively bind whether its new
acceptance scope includes that historical extra rule before results. If retained,
use the same primary argv with a fresh `future-confirm/NN-case` output directory,
then `compare` on that same confirmation report with `--reference-binary prior
--candidate-binary rust --metric METRIC --max-regression-percent 5
--min-regression 0 --out NEW_PATH`, and separately verify the >5% point median.
Do not pool primary and confirmation or select only favorable metrics. This
follow-up has the same unresolved fixture-time and exact-order constraints.

Native meter API: `pb-meter METRICS.json -- EXECUTABLE [ARG ...]`. It forks/execs
the actual CLI; `CLOCK_MONOTONIC` spans fork to exit availability, and wait4
supplies child user/system CPU, RSS (Linux KiB converted to bytes), faults,
context switches and block-operation counters. Linux waits with WNOWAIT and
reads `/proc/PID/io` before reaping. `run.py` replaces supervisor fallback values
with native-file metrics and sums actual user+system CPU for `cpu_ms`. Missing
meter output records failure; supervisor fallback is not accepted child data.
No current invocation tested the cache or its API. No new physical CPU counter
resolution/accounting proof is supplied. Require actual native provenance and
retain any unresolved CPU accounting/quantization uncertainty; do not synthesize
proof from source tokens or historical success. Child CPU/RSS excludes the
Python observer and fixture; elapsed still includes their scheduling/backpressure
effects. `/proc` read/write-family counters omit send/recv paths, wait4 block
operations are not storage bytes, and neither represents network I/O. First
request uses realtime correlation and can skew; pipe times use monotonic clocks.
Phase 5's refresh/cache follow-up explicitly found five warmups did not equalize
executable cache state; do not alter cache treatment here or infer causality
from old-versus-new file behavior. Any later cache-control experiment is separate.

Conservative partition review uses DISJOINT SINGLE-WORKLOAD subsets, in retained
suite order, with all three labels and all five warmups/30 samples in one
session. This is the smallest workload partition without splitting that session.
For five nonfixture workloads (1,2,3,4,6), 105*10=1050 s is the CLI-timeout sum.
Proposed additional allocation is 60 s preparation/identity, 240 s setup/
validation/artifact/analysis overhead, 150 s cleanup/reaping and 150 s end hashes/
lock release: 1650 s total, below proposed 1750 s complete-span ceiling and
strictly below 1800. These are prospective CAPS/reserves, not measured costs,
validated enforcement or proof that disk I/O/reaping is bounded. Root still
needs its independently inspected supervisor and abort/cleanup schedule.

For each of the TEN fixture workloads, 105*(10 s fixture readiness + 10 s CLI
+ 5 s trace fetch)=2625 s even before those 600 s reserves (3225 s total).
This is a timeout-based planning sum, NOT a tight runtime upper bound: readiness
only bounds selector readiness then performs blocking readline; wait/reap,
validation, disk I/O and HTTP timeout semantics have no whole-run deadline.
A two-label Go A/A singleton alone has 70*25=1750 s plus reserves, so moving
controls to their own runs does not solve conservative fixture feasibility.
A four-label merged-prior session only increases the budget and changes order.
Never use historical median runtime to claim those worst-case allocations fit.

There is a SECOND partition limitation: each new invocation resets Random(2026),
so only case 1's singleton matches its full historical seeded schedule. The
other singleton schedules differ. `orders.json` lists full-session vs restarted
orders and checks all historical measured rounds against the static full-session
reconstruction. The packet exposes singleton commands for precise review, not
as an unchanged-order execution solution. No CLI RNG resume/skip-round facility
exists. Do not silently reseed, split 30 samples, add warmups, split binary
roles, splice reports or monkeypatch the collector to hide this discrepancy.
An approved explicit execution design is needed to preserve original order
across bounded runs; no harness change is proposed or applied here.

A separate same-Go A/A correctness/noise control must precede admitted timing;
use the identical retained suite selection, fixture/meter/options with insertion
labels `go_a=.../baselines/pb-go` and `go_b=.../baselines/pb-go` and fresh
`future-aa/NN-case` outputs, without claiming its two-label order equals the
three-label campaign. The published historical shared-selfcheck is historical
correctness context, not a present quiet/noise control. This packet makes no
new noise tolerance, A/A performance acceptance or observer instrumentation.

Every future run's strictly-under-1800 span starts at its FIRST preparation
operation and ends after all teardown, descendants reaped, scratch cleanup,
end identities and common-lock release. `--timeout 10` covers only individual
measurements; wrapping just the CLI with a timeout cannot enforce that span.
Do not regard a TERM/KILL exit as successful cleanup. Root must independently
validate the outer supervisor, time reserves, incomplete-report retention and
whole-tree cleanup. No common lock was inspected/acquired here. Later use needs
explicit companion pause acknowledgement and the existing common lock, with
release between independently bounded sessions. Failed/interrupted evidence is
retained under its unique directory; retries use new names. Root must inspect
and rebind all inputs and source identities at start/end of any future run.

Practical blockers remain: no quiet window, no validated full-span enforcement,
fixture singleton timeout allocations already exceeding the bound, and exact
historical RNG order cannot survive ordinary disjoint invocations. Runtime and
actual accounting bindings also remain unverified. Thus NO command is admitted
for execution by this preparation and NO retained-gate pass, performance rank,
full Phase 7 completion or always-faster claim is established. The requested
read-only preparation is complete; root owns any next execution disposition.
