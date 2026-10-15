# Phase 6 measurement protocol

This is the campaign definition, not measured performance evidence. The coordinator
owns optimized builds, provenance, quiet windows, execution and final evidence.
No performance campaign or profiler was run while implementing this tooling.
The reference is Go `07af390`; the candidate starts from Rust `4a812d5`.
The Phase6 final plan supersedes conflicting historical wiki proposals.

`harness/phase6_workloads.py` drives native `pb tail logs -o json` against the
Phase6 v1 fixture. It uses the unchanged `pb-fixture --scenario FILE` interface,
startup JSON with `url`, `grpc_port` and `control_url`, per-subscription ops,
incremental expected-output summary and disk JSONL events. Each sample gets a
fresh fixture, HOME/config and native meter process. Fixture startup, payload
setup, executable hashing, builds and artifact analysis sit outside native
client elapsed time. Sample folders retain the exact scenario and identities.

Admission requires the fixture's additive exact-summary counters and production/churn accounting (immutable r5).
`about_requests` and `http_requests` must each be one, global and stream DoGet
counts must match the workload, and `auth_checks`/`ticket_checks` must cover
every DoGet. Invalid auth, mismatched tickets, unknown requests and requests
before completed about discovery must each be zero. The collector configures
an exact expected ticket. Missing validation counters fail admission; retained
events cannot supply evidence for requests they did not capture. These fixed
counters contain no credentials and do not depend on log caps or sampling.
First/last DoGet and about-completion timestamps, first nonempty emission and
`last_terminal.t_ns` also come from the exact summary. About completion means
the first successful about handler returned after writing headers and body.

Retained events only corroborate the requests they contain and supply sampled
batch latencies. `request_evidence` reports captured/uncaptured DoGets and
whether the final terminal event was retained. A long reconnect run remains
valid when its exact summary covers all requests and capped logs omit later
DoGets or the terminal event. Missing per-batch events remain uncorrelated;
they are never counted as observed auth, ticket or latency evidence.

Finite streams deliberately return gRPC InvalidArgument and client exit 1.
Their elapsed time includes the terminal-error overhead. These are explicitly
**failure-terminated** streams, never successful live completions. Expected
stdout counts, row order, exact hash/bytes, batch separators, ticket bytes,
discovery-before-Flight, auth, subscription count, unchanged config and fatal
diagnostic must validate before a sample can enter a comparison. The independent
fixture's `fixture_panic` event or nonzero counter always invalidates a sample,
including when its bytes and terminal status otherwise match. The independent
fixture's Go v13 row renderer supplies the output expectation; a small Go A/A
control precedes the campaign. The separately owned compatibility corpus remains
the authority for the full Arrow/error/auth/signal contract.

Native timing uses prebuilt, hashed binaries and sequential randomized order
within each round, with the recorded seed. Finite defaults are five excluded
warmups and 30 measured repetitions per binary. No prior Rust tail measurement
is admitted: that build does not implement tail. Prior Rust joins the existing
Phase5 shared-command regression suite, in its own report, to investigate
dependency/startup regressions.

The parent manifest must contain `sources`, `toolchains`, `build_flags`,
`dependencies` and predeclared `budgets`. Record the pinned Go toolchain (the
local Go 1.25.4 module toolchain when used), Rust compiler, locked dependencies,
public build metadata, source revisions/dirty hashes and baseline identities.
The runner adds executable sizes/hashes, fixture, meter, collector and shared
process-harness hashes, host/kernel/CPU/affinity/governor/load and workload/sink
definitions. Optional `build_elapsed_ms` and `dependency_size_bytes` are separate
cost fields; absent values remain null. Build time requires a separate build
experiment, outside client timing. A dependency count is not dependency size.
Use the already declared thresholds in
[performance-budgets.json](evidence/phase6/performance-budgets.json) in the
forthcoming manifest; retain that file's hash/content with the run identities.
These are investigation thresholds and cannot relax sample or contract gates.

The primary `--collector-mode full` collector drains stdout incrementally. It hashes every byte, validates one
complete LF-framed JSON object at a time and recognizes an exact blank line as
a batch boundary, including empty batches. It retains at most a 64 KiB stdout
prefix, a 16 MiB incomplete record, 1 MiB stderr prefix, 16 MiB of
visibility events, and 8 MiB each of resource samples and correlated batch observations. Events
remain whole when a disk cap is reached; omitted bytes/events are reported.
An oversized or incomplete row, stderr overflow, unreadable artifact, deadline
or invalid meter fails the sample. A soak never retains its whole stdout.

The separate `--collector-mode hash-framing` observer-overhead control hashes
the same entire stdout, counts LF-framed rows/batch separators and parses only
the first complete object for the first useful-record milestone. Later frames
are not parsed or retained. Their semantic sequence check is reported as null,
not zero. Admission still requires exactly the same independent fixture
stdout SHA256, byte count, rendered-row count and batch count as the primary
collector, along with all request/terminal gates. Altered or malformed later
stdout therefore fails the full-stream checksum even when framing is unchanged.
The report is labeled `observer_control` and records the collector mode and
JSON parse count. Each campaign uses one mode and a fresh output directory;
never merge these controls into primary native timing summaries. Compare
controls to matching primary runs to quantify collector overhead before
interpreting throughput. Fixture expected-output overhead is still present
in both modes and requires its own investigation.

The first useful-result metric is the parent observation of the **first complete
JSON record plus LF**, not schema arrival, an incomplete object or first byte.
Its clock is the meter's CLOCK_MONOTONIC. Process-start latency and fixture
`batch_emit_start` to first record are separate. This fixture milestone is after
IPC preparation and optional expected-output encoding; the latter latency
includes Send, transport, client work, sink and observer scheduling; it is not
pure client processing. A short process can exit before the collector observes
its buffered record: keep that observation and flag it rather than clipping it
to native elapsed time. The fixture-to-first-record metric uses the exact
first nonempty emission timestamp even when that event was dropped. Batch
observations correlate global batch ordinals to
retained fixture emission events. Sampled/missing fixture events or capped
visibility logs leave explicit uncorrelated batches; no missing latency becomes
zero. Emission-start to batch separator differs from Send-completion latency.

Native `pb-meter` provides actual child elapsed, user/system CPU, peak RSS,
minor/major faults, voluntary/involuntary context switches, block input/output
operations, exit versus signal and optional Linux process I/O counters. The
collector samples the meter's direct CLI child every 200 ms for RSS, FD count,
thread/task count and cumulative user/system CPU. Constant-memory online
least-squares trends exclude the first five seconds; disk samples retain the
timestamps for independent analysis. Very short runs can have no proc samples.
The sampled maximum is distinct from native peak RSS. Sampling can miss brief
spikes; thread count does not expose Tokio task count.
Resource snapshots reject Linux Z/X/x states, and recheck state/start time
after reading status and FDs to omit exit/PID-change races. Missing live VmRSS
remains unavailable, never synthesized as zero. Resource JSONL records the
start of the read sequence and its completed observation timestamp (`t_ns`),
which drives trends. These are sequential procfs observations, not atomic
snapshots: a process can still change immediately after the final read, and
fields span the recorded interval. `missing_proc_samples` remains an aggregate
omission count (absent child, dead state, missing fields or inaccessible procfs);
it does not distinguish those causes. Native wait4 bounds and peak RSS are
independent of sample filtering. The trend warmup clock begins at observer
launch; native client elapsed starts later at the meter's fork.
`cpu_us_per_row` is native total user+system CPU milliseconds × 1000 divided
by the number of rendered complete rows. It includes startup, discovery and
terminal-error CPU; it is not isolated decoding/rendering CPU. Zero-row runs
report null. Raw user/system CPU values remain in every sample.

Output bytes, fixture FlightData protobuf bytes and fixture Arrow buffer bytes
are distinct quantities. Rates currently use full native elapsed, including
discovery and terminal overhead. The protobuf byte count excludes gRPC message
prefixes, HTTP/2 framing and TLS; it is not network wire traffic. Proc I/O is not
socket traffic. Native RSS is not heap size. Allocation counts, retained client
capacities, queue high-water marks, client read-ahead, cancellation latency and
deployed server drops remain N/A without their dedicated instrumentation.
`expected_output=true` makes the fixture run the Go encoder as well as Send;
`expected_output_ns` and fixture user/system CPU nanoseconds annotate that
fixture cost, separately from client CPU. The current native campaign keeps
expected-output validation enabled; disabling it needs a separate independently
validated oracle protocol. That common fixture cost and Python record parsing must receive an observer
overhead check before stronger throughput claims. A Go speed advantage can be
limited by the collector, fixture or pipe.

The finite corpus covers one row, many small batches, large batches, wide
records, empty batches, LZ4/Zstandard IPC compression, dictionary replacement,
paced arrivals and a slowed reader. TLS is
an independent `--tls` condition using the fixture CA and SSL_CERT_FILE; its
reachability/ALPN/auth must pass the protocol gates. TLS and plaintext reports
remain separate. This is loopback evidence, not successful production integration.

Duration workloads share the same collector and support 30–60 minutes per
session. `functional` permits a short duration for protocol checks; `soak`
rejects durations below 1800 seconds. Duration defaults are zero process
warmups and one independent repetition per binary. A three-session tier requires
separate approved windows after the first tier validates. The five modes are:

- Steady: bounded batches on a 20 ms absolute production schedule.
- Idle: schema, bounded idle sleep and deliberate fatal status; first record is N/A.
- Reconnect: bounded repeated batch/sleep/EOF subscriptions, then a fatal
  subscription. Each cycle includes a server 100 ms wait and the client's fixed
  500 ms reconnect sleep. It schedules enough cycles for the requested duration;
  actual elapsed can exceed it. Reconnect overhead is not hidden in throughput.
- Dictionary: cyclic delta updates grow16 entries at a time to4096, then
  replacement resets16 entries of a new generation. This repeats throughout
  the session; it does not become a stable dictionary after5.12 seconds. Exact
  reset/message counters and bounded minute buckets must cover all batches.
- Slow sink: the same paced producer, an initial reader pause of up to ten
  seconds, then 4096-byte reads every 20 ms. It exercises blocked output and
  flow control. Different delivered row/hash totals make a speed comparison
  incompatible, even when both sessions individually validate.

Report resource trends, absolute counts, durations and failures for every mode.
Investigate post-warmup RSS/FD/thread growth and repeat material findings with a
new seed. Fixture Send success proves neither application read-ahead bounds nor
production delivery continuity. Keep client versus fixture/server retention
separate. The compatibility runner owns gated HTTP/2 progress and cancellation
tests; these duration runs do not substitute for them.

Reports contain raw per-sample JSONL, valid/invalid status, absolute observations,
median, range and standard deviation. Finite comparisons require identical work
and at least20 valid samples per label for the shared harness's pointwise
exploratory bootstrap intervals. Finite smaller samples and every duration tier
have no confidence intervals: retain session values, ranges and descriptive
point estimates only. These are unpaired, without simultaneous or non-inferiority
inference. Do not call a nonsignificant result equivalence or quote p99 from30
samples. Invalid samples/warmups and incomplete partitions quarantine ratios.
Sink-gated and schedule-bound metrics always remain absolute observations only.
Parent analysis applies the predeclared practical thresholds only to admitted
comparisons and primary workload families.

The explicit per-workload `contract_comparison` checks all measured Go and
candidate samples for identical full stdout hash/bytes/rendered rows/batches,
termination and stderr, request counts/ticket semantics, workload definition,
sink/TLS and normalized config effects. Individually valid duration sessions
can deliver different totals; retain their valid absolute observations and
summary statistics, but set every speed ratio to null with `ratio_quarantine`
reasons. `comparison_contracts_passed` is separate from `passed` (sample
validation). Timestamps and resource measurements never enter the stable
contract, including the first-workload Go A/A comparison.

All commands below are **future coordinator commands**, requiring a new
quiet-window handshake for each partition. The coordinator first freezes
release binaries, fixture/meter/collector and the manifest, finishes all builds,
and asks Phase7 plus any other builders/profilers to pause for the named
partition and its maximum duration. Each participant must explicitly acknowledge
that pause. Only then does the coordinator provide that window's token and
start the command. A token alone is not an acknowledgement. Do not preauthorize
an entire five-hour sequence or launch the partitions as an unattended shell loop.

The driver holds `/home/ajs/.cache/pb-rs-performance.lock` with nonblocking flock
and rejects alternate locks. Only SHA256 of the handshake token is retained.
`--window-budget-s` is required. Before locking any duration partition, the
runner sums every scheduled sample timeout plus30-second cleanup and both
contract A/A controls; the entire partition must fit. Before every A/A,
warmup or measured sample, its **full configured child timeout plus a 30-second
fixture/setup/cleanup reserve** must fit the remaining budget. If it cannot
fit, no sample is spawned or shortened: the report is `complete=false`,
`passed=false`, and all ratios are quarantined. Prior valid absolute samples
remain available. Process timeouts and artifact failures remain invalid, not
shortened successful soaks. The reserve covers the trusted fixture's 10-second
readability wait and 5-second summary deadline plus routine cleanup/analysis;
scheduling and
unusual artifact failures can still require coordinator recovery. The driver
releases the lock before final statistical reporting. Parent confirms child
and fixture cleanup, tells Phase7 the window is released, and allows a build
interval before negotiating the next token. Incomplete partitions require a
fresh full partition; never fill missing repetitions with an overlapping run.

The following estimates are planning reservations, not measured timings. They
exclude builds and profiling. The fixture's expected-output renderer is enabled
in both collector modes. Observe the first valid partition to revise estimates
before reserving later ones. A failed or incompatible workload requires review,
not automatic advancement to a larger tier.

| Partition | Planned samples | Estimated active runtime | Reserved maximum quiet window |
|---|---:|---:|---:|
| One finite pair, full collector | 2 A/A + 2 workloads × 2 binaries × (5 warmups + 30 samples) = 142 | 1–5 min | 15 min |
| Same finite pair, hash/framing control | 142, separate report | 1–5 min | 15 min |
| One short mode pair, full collector, 60 s/session | 2 A/A + 2 measured sessions | about2–3 min | 15 min (worst-case requirement720s) |
| Same short mode pair, hash/framing control | 4, separate report | about2–3 min | 15 min |
| One first-tier soak mode pair, 1800 s/session | 2 A/A + 2 measured sessions | about 60–65 min | **75 min, explicitly negotiated** |

Finite primary/control together have1440 measured samples plus240 excluded
warmups and24 contract A/A controls across twelve windows. Partition the twelve finite
workloads as: `(one-row, many-small)`, `(large, wide)`,
`(dictionary-finite, compressed-lz4)`, `(compressed-zstd, empty-batch)`,
`(paced, slow-finite)`, `(dictionary-large-lz4, dictionary-large-zstd)`. Each pair gets its own full and hash/framing window;
all still use five warmups and 30 repetitions per implementation. Estimated
ordinary finite time is10–50 minutes plus an estimated5–15 minutes per pressure
primary/control pair, spread across twelve reservations, with Phase7
build opportunities between them. These workload names match the runner;
`large` is the plan's large-batch workload.

Short screening uses steady, idle, reconnect, dictionary and slow-sink, one
mode pair per window, then a separate matching hash/framing control. Its ten
partitions total roughly 20–30 active minutes. These are short duration
observations, never called soaks. Review delivery totals and observer effects
before interpreting ratios or advancing to the long tier.

The **first soak tier** is one full 30-minute session per binary and per mode,
zero excluded process warmups, in five separately negotiated 75-minute windows.
It needs at least five hours of active collection in total, spread across
windows with Phase7 builds between them. Reconnect schedules 3000 cycles plus
its fatal final subscription; fixed retry sleep and scheduling can add runtime.
Slow-sink drains may also add time. A soak child has at least a 1920-second
deadline, and admission checks that deadline plus cleanup before each session.
One session per binary/mode is descriptive retention screening, not a reliable
speed or equivalence conclusion. The full **three-session tier** starts only
after the corresponding first-tier samples validate and resource/contract
findings are reviewed. Add two further independent mode-pair windows (the same frozen scenario seed,
fresh session indices and output folders) per mode, rather than requesting one uninterrupted
15-hour reservation. The full tier is approximately 15 active hours total;
repeat material findings with a new seed and retain every session. Additional
long observer controls, TLS or profiles need their own approved windows.

All generated artifacts use the disk cache, never `/tmp`. Set the paths below
to the frozen release candidate and immutable fixture accepted by the parent;
`target/debug/pb` is never a timing baseline. The manifest must incorporate the
predeclared performance budgets. Replace the two workload names or one mode
only after agreeing on the corresponding partition; request a fresh token
for every command. These commands do not schedule or authorize their own window.

```sh
export TMPDIR=/home/ajs/.cache/pb-phase6/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
# P6_RELEASE, P6_FIXTURE, P6_MANIFEST and P6_QUIET_TOKEN are parent-provided.

# Finite partition F1 primary; repeat the other five pairs in separate windows.
python3 harness/phase6_workloads.py run \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go --candidate "$P6_RELEASE" \
  --fixture "$P6_FIXTURE" --meter bin/pb-meter --manifest "$P6_MANIFEST" \
  --tier finite --workload one-row --workload many-small \
  --warmups 5 --repetitions 30 --seed 2026 --collector-mode full \
  --window-budget-s 900 --quiet-window-token "$P6_QUIET_TOKEN" \
  --out /home/ajs/.cache/pb-phase6/finite-f1-primary

# F1 observer control requires its own acknowledgement and token.
python3 harness/phase6_workloads.py run \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go --candidate "$P6_RELEASE" \
  --fixture "$P6_FIXTURE" --meter bin/pb-meter --manifest "$P6_MANIFEST" \
  --tier finite --workload one-row --workload many-small \
  --warmups 5 --repetitions 30 --seed 2026 --collector-mode hash-framing \
  --window-budget-s 900 --quiet-window-token "$P6_QUIET_TOKEN" \
  --out /home/ajs/.cache/pb-phase6/finite-f1-observer-control

# One short mode pair; repeat with idle/reconnect/dictionary/slow-sink separately.
python3 harness/phase6_workloads.py run \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go --candidate "$P6_RELEASE" \
  --fixture "$P6_FIXTURE" --meter bin/pb-meter --manifest "$P6_MANIFEST" \
  --tier functional --duration-s 60 --workload steady \
  --warmups 0 --repetitions 1 --seed 2026 --session-index 0 --collector-mode full \
  --window-budget-s 900 --quiet-window-token "$P6_QUIET_TOKEN" \
  --out /home/ajs/.cache/pb-phase6/short-steady-primary

# Separate short observer control for the same mode, with a new token.
python3 harness/phase6_workloads.py run \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go --candidate "$P6_RELEASE" \
  --fixture "$P6_FIXTURE" --meter bin/pb-meter --manifest "$P6_MANIFEST" \
  --tier functional --duration-s 60 --workload steady \
  --warmups 0 --repetitions 1 --seed 2026 --session-index 0 --collector-mode hash-framing \
  --window-budget-s 900 --quiet-window-token "$P6_QUIET_TOKEN" \
  --out /home/ajs/.cache/pb-phase6/short-steady-observer-control

# First soak tier S1: one steady pair only, following explicit 75-minute agreement.
python3 harness/phase6_workloads.py run \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go --candidate "$P6_RELEASE" \
  --fixture "$P6_FIXTURE" --meter bin/pb-meter --manifest "$P6_MANIFEST" \
  --tier soak --duration-s 1800 --workload steady \
  --warmups 0 --repetitions 1 --seed 2026 --session-index 0 --collector-mode full \
  --window-budget-s 4500 --quiet-window-token "$P6_QUIET_TOKEN" \
  --out /home/ajs/.cache/pb-phase6/soak-first-steady

# Only after the first tier validates: a second independent steady pair.
# Use the same frozen scenario seed with session-index2 in a third window.
# Material-finding repetitions with a new seed remain a separate series.
python3 harness/phase6_workloads.py run \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go --candidate "$P6_RELEASE" \
  --fixture "$P6_FIXTURE" --meter bin/pb-meter --manifest "$P6_MANIFEST" \
  --tier soak --duration-s 1800 --workload steady \
  --warmups 0 --repetitions 1 --seed 2026 --session-index 1 --collector-mode full \
  --window-budget-s 4500 --quiet-window-token "$P6_QUIET_TOKEN" \
  --out /home/ajs/.cache/pb-phase6/soak-second-steady
```

Profiles are a separate campaign with symbolized release-equivalent builds,
separate output folders and controls. Existing `profile_external.py` command,
wrapper, artifact and analysis helpers are reusable; its standalone CLI's
fixed HTTP/SQL case catalog does not drive Phase6 native Flight. The following
adapter shows the exact capture commands without adding profiler behavior to
native timing. Run it under the same coordinator lock in a later quiet window,
one mode at a time. Replace `mode` with `strace` or `strace-writes` for syscall
counts or raw-pointer FD-write counts. Heaptrack uses the existing preload
helper for Rust only; its Go comparison remains native CPU/RSS, not allocator
counts. Validate profiler artifacts and contract/control output, then use the
existing analysis helpers outside the capture window. Profiler wrapper CPU/RSS
are not CLI-only metrics, and these samples never enter native reports.

```sh
PYTHONPATH=harness python3 - <<'PY'
from pathlib import Path
import shutil
import phase6_workloads as p6
import profile_external as profiles

root = p6.safe_path('/home/ajs/.cache/pb-phase6/profile-perf-rust')
root.mkdir(parents=True, exist_ok=False)
binary = Path('target/symbolized/profiling/pb').resolve()
fixture = p6.TMP / 'fx/pb-fixture-r5'
meter = Path('bin/pb-meter').resolve()
mode = 'perf'
workload = next(w for w in p6.workloads() if w['name'] == 'large')
with p6.quiet_lock(p6.PERFORMANCE_LOCK, __import__('os').environ['P6_QUIET_TOKEN']):
    control = p6.run_sample(binary, workload, fixture, meter, root / 'control')
    launch = profiles.wrapper(root / 'launch', profiles.command(
        mode, shutil.which('strace' if mode.startswith('strace') else mode), binary, root, 'cycles:u'))
    measured = p6.run_sample(launch, workload, fixture, meter, root / 'capture')
    captures = profiles.artifacts(mode, root)
    p6.write_json(root / 'diagnostic.json', dict(kind='profile_only', mode=mode,
        resource_scope='profiler process tree', control=control, capture=measured,
        artifacts=captures, compatible=control['valid'] and measured['valid'] and
        control['contract'] == measured['contract']))
PY
```

The optional `run_sample(..., diagnostics=True)` path enables PB_DIAGNOSTICS_DIR
for a separate Rust diagnostics build and reads its one completed sidecar.
It validates counters, including the current `max_application_messages_inflight`
name, and reports owner maxima for payload, dictionary, batch and staging
capacity, including peak/last list renderer scratch capacity. A missing,
ambiguous, incomplete or invalid sidecar fails that
diagnostic sample; signal-killed runs do not produce invented values. These
maxima are neither simultaneous totals nor unique allocations/RSS. The message
count includes decoded dictionary events and does not measure HTTP/2 read-ahead.
Keep diagnostic samples and observer-overhead controls out of native reports.

The real diagnostics follow-up used the current `target/debug/pb` feature build
and immutable fixture r3. Eight finite checks passed: basic, LZ4, Zstandard,
dictionary finite/stable/replacement/delta and the values schema with lists.
Every check rendered exactly 24 complete rows in three batches, matched the
independent fixture hash/framing and reported
`max_application_messages_inflight == 1`. Dictionary buffer observations were
nonzero; the list workload reported scratch peak/last of 104 bytes and staging
peak/last capacity of 16,384 bytes. These are functional owner observations,
not a measured resource envelope, throughput result or cross-language ranking.
They do not establish large-list high-water release or HTTP/2 read-ahead.
The parent owns build/release identities and any later diagnostic campaign.
Those earlier max-inflight values came from the former construction marker.
The parent has since replaced it with an actual accepted-handoff current/peak
counter, with decrement on acknowledgement/drop; process validation of that
new implementation passed eight parent-owned R4 functional checks in
`docs/evidence/phase6/diagnostics-real-counter-r4.json` and the two preserved R4
large-dictionary checks below. Do not use the earlier value
of one as proof of the new counter or of transport buffering bounds. Structural
handoff/cancellation tests remain the primary causal evidence.

The following is a **historical reproduction of the small r3 diagnostics
check**, not a current command or approved build. Its mutable debug path and
r3 fixture are preserved below only to explain that earlier artifact. Current
functional work requires parent-provided frozen binary/fixture identities. Public
stdout still passes the independent byte/count/frame gates. Keep the cache
report labeled as debug functional evidence.

```text
HISTORICAL ONLY — do not execute against the current candidate.
PYTHONPATH=harness python3 - <<'PY' \
  > /home/ajs/.cache/pb-phase6/tmp/diagnostics-functional.log 2>&1
from pathlib import Path
import copy, time
import phase6_workloads as p6

root = p6.TMP / ('diagnostics-functional-' + str(time.time_ns()))
root.mkdir()
suite = p6.workloads()
cases = [copy.deepcopy(next(w for w in suite if w['name'] == name))
         for name in ('one-row', 'compressed-lz4', 'compressed-zstd', 'dictionary-finite')]
for case in cases:
    case['stream']['subscriptions'][0]['ops'][0].update(rows=8, count=3)
for mode in ('stable', 'replace', 'delta'):
    case = copy.deepcopy(cases[3])
    case['name'] = 'dictionary-' + mode
    case['stream']['dictionary']['mode'] = mode
    cases.append(case)
lists = copy.deepcopy(cases[0])
lists['name'] = 'list-values'
lists['stream']['schema'] = 'values'
cases.append(lists)
results = []
for case in cases:
    sample = p6.run_sample(Path('target/debug/pb').resolve(), case,
        p6.TMP / 'fx/pb-fixture-r3', Path('bin/pb-meter').resolve(),
        root / case['name'], diagnostics=True)
    assert sample['valid'], sample['errors']
    owners = sample['diagnostic_tail_resources']['resources']
    assert owners['max_application_messages_inflight'] == 1
    assert owners['rows_decoded'] == sample['rows'] == 24
    assert owners['batches_decoded'] == sample['batches'] == 3
    assert owners['max_staging_capacity_bytes'] >= owners['max_staging_length_bytes'] > 0
    if case['stream']['schema'] == 'dictionary':
        assert owners['max_dictionary_buffer_bytes'] > 0
    if case['stream']['schema'] == 'values':
        assert owners['max_renderer_scratch_capacity_bytes'] > 0
    results.append(dict(name=case['name'], valid=True, owners=owners))
    print(results[-1], flush=True)
p6.write_json(root / 'functional-results.json',
              dict(scope='debug_diagnostics_functional_only', results=results))
print(root)
PY
```

The observer audit ran only its new targeted causality tests. Source inspection
and a bounded native probe confirmed Phase6 uses `run.spawn_measured`:
`os.posix_spawn` executes the small `pb-meter`, and that native process forks
the CLI. It does not fork the large Python observer immediately before CLI
exec. Inflating observer RSS from 29,466,624 to 96,653,312 bytes left native
`/usr/bin/true` child peaks at 1,474,560 and 1,486,848 bytes respectively. This
Linux functional probe checks resource attribution, not timing or efficiency;
it does not prove every OS/runtime/profiler-wrapper path. Shared `run.py` and
`meter.c` were inspected and left unchanged.

Phase6 fixture readiness uses an event-driven `selector.select(10)` followed
by one bounded-length JSON banner read, before spawning the meter. There is
no 50 ms readiness poll. Native elapsed starts inside the meter after its own
exec/loader, immediately before the CLI fork, and includes CLI exec/loader
work. The first complete-record timestamp comes directly from a stdout read
containing a validated object plus LF; fixture summary/events are checked
after collection. A file-gated partial-object probe verified first stdout
precedes withheld LF and first complete record follows LF, even when fixture
summary access is forbidden. The selector's 20 ms timeout is for resource and
deadline wakeups; ready stdout wakes it immediately. It is not a fixed first-
result grid. OS scheduling, read-chunk grouping and deliberately paced sinks
still limit observation precision.

The trusted fixture writes its complete short JSON banner as one line. Startup
was not changed by this audit. The existing ten-second wait bounds waiting
for readability, not a subsequently blocking `readline`: a nonconforming
fixture that writes only a partial banner and stays alive can exceed that
bound and the campaign reserve. Current fixture producers were inspected;
this general partial-banner limitation remains explicit rather than claiming
a strict whole-line startup deadline. No timing/soak/profiler campaign ran.

```sh
PYTHONPATH=harness python3 -m unittest -v \
  test_phase6_workloads.ObserverAudit test_phase6_workloads.ObserverNative \
  > /home/ajs/.cache/pb-phase6/tmp/observer-targeted-tests.log 2>&1
```

Before interpreting a profile, retain readelf symbol checks, tool version/hash,
build flags and complete artifact checks. Pair symbolized and normal-release
controls and quantify observer overhead. Perf CPU stacks do not establish wait
time; strace latency is instrumented; heaptrack tracked heap is not RSS. Profile
decode/render/write separately when attribution requires it. Missing tool,
permission error, failed workload or missing artifact is invalid evidence.

Collector validation is inexpensive and does not run a performance campaign:

```sh
python3 -m unittest discover -s harness -p test_phase6_workloads.py -v \
  > /home/ajs/.cache/pb-phase6/tmp/collector-tests.log 2>&1
```

A direct functional integration also exercises tiny finite streams and each
one-second mode without starting a timing campaign or manufacturing a quiet
token. It checks the actual fixture protocol; its raw resource observations
are not performance evidence. The implementation follow-up passed all nine
Go functional checks after the fixture's typed-nil dictionary fix. No Rust
comparison, profiler or long soak was run in that check.

The review follow-up also passed immutable r2 low-cap reconnect functional
checks in both collector modes. With event_limit 4 and hard limit 6, the
summary checked all three DoGets while only one DoGet was retained, fourteen
events were dropped and the final terminal event was absent. Admission and
first-record/terminal correlation used exact summary evidence; missing batch
observations stayed explicit. A separate 128-tiny-batch check parsed 128
objects in full mode and one in hash-framing mode, with both modes meeting
the same output checksum expectation. These were correctness checks, with
no performance claims. The unit suite includes a shorter immutable-r2
low-cap reconnect regression and rejects missing auth/ticket coverage and
altered later stdout in the observer control.

```sh
PYTHONPATH=harness python3 - <<'PY' \
  > /home/ajs/.cache/pb-phase6/tmp/collector-functional.log 2>&1
from pathlib import Path
import time
import phase6_workloads as p6
root = p6.TMP / ('collector-functional-' + str(time.time_ns()))
root.mkdir()
cases = [w for w in p6.workloads() if w['name'] in
         ('one-row', 'empty-batch', 'compressed-lz4', 'compressed-zstd')]
for case in cases:
    if case['name'].startswith('compressed-'):
        case['stream']['subscriptions'][0]['ops'][0].update(rows=2, count=1)
cases += p6.workloads(1, 'functional')
results = []
for case in cases:
    sample = p6.run_sample(Path('/home/ajs/.cache/pb-phase6/baselines/pb-go'),
        case, p6.TMP / 'fx/pb-fixture-r5', Path('bin/pb-meter').resolve(), root / case['name'])
    results.append(dict(name=case['name'], valid=sample['valid'], errors=sample['errors']))
    print(results[-1], flush=True)
p6.write_json(root / 'functional-results.json', dict(scope='functional_only', results=results))
print(root)
raise SystemExit(0 if all(x['valid'] for x in results) else 1)
PY
```


## Measurement review r1 admission and reporting

The parent manifest must bind `budgets` to the exact current content of
`docs/evidence/phase6/performance-budgets.json` and `budgets_sha256` to its
actual SHA256, before acquiring the shared lock. No thresholds are accepted
from a self-declared alternate file. Nonempty source/toolchain/build/dependency
provenance is required. `fixture_manifest` is `{path,sha256}` and `fixture_build`
is exactly that immutable JSON. Its executable hash and read-only mode, every
read-only `source_paths` snapshot hash, and recorded Go1.25.4 toolchain executable
hash are checked. Current r5 is `cache/tmp/fx/pb-fixture-r5`, SHA256
`c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314`;
its adjacent manifest SHA256 is
`40d53f0b019805fbc710d7bc41ab5e356b96a4dfc82e5ed6c3dee469f8075286`.
These names abbreviate `/home/ajs/.cache/pb-phase6/`, never mutable `bin/`.
Binaries, fixture, meter, collector, shared process harness, parent/budget/fixture
manifests and fixture snapshots are rehashed at window end. Changes invalidate
the partition. Exceptions in contract A/A, warmups, measured samples or report
finalization produce an atomic `complete=false`, `passed=false` report; prior
absolute records remain in their sample journal. TypeError is included. Whole
partition quarantine is intentionally conservative.

Every deterministic `live` schedule must have exact `ops` and
`scheduled_batches = ceil(duration_ms/interval_ms)`, bounded by a declared batch
count. Interval-zero duration bursts have no deterministic count and cannot
enter these comparisons. Exact emitted counts, stop counters, omissions,
lateness and Send-call counts must agree with delivered batches. Any omission
invalidates ordinary steady/dictionary/paced admission; slow-sink may retain
its absolute delivered observations but its throughput is never comparable.
`live.send_call_ns` and `batch_send_call_ns` measure wall time inside grpc-go
Send, including marshaling/copying/flow-control waits, not isolated blocking.
Cyclic dictionaries require exact initial/delta/replacement/reset/max-size
counters and complete minute-bucket message coverage, even after sampled event
logs drop. Per-batch latency coverage is separately reported by minute; its
absence never becomes an observed zero. Visibility retention is bounded16MiB,
which permits the planned180000 batch events in a60-minute20ms schedule;
coverage and dropped bytes still govern actual evidence.

Collector `RUSAGE_SELF` CPU is measured around capture, excluding children and
post-capture validation. Fixture lifetime CPU deltas use a control summary
before spawning the meter and one after exit, with request-time bounds retained.
`expected_output_ns_total` deltas are **monotonic encoding wall time**, overlap
fixture CPU, and are never subtracted from client CPU. Fixture runtime Go knobs
are pinned: GOMAXPROCS=observed CPU count, GOGC=100, empty GODEBUG, GOMEMLIMIT=off,
with a minimal cache-based environment. Build-time Go environment is in the
immutable fixture manifest. Fixture/observer CPU per native elapsed second is
a core-equivalent busy screen. At least100ms is required;0.80 flags potential
ceiling and suppresses throughput ratios, without claiming causal saturation.

Each sample records aggregate `/proc/stat` and raw `/proc/loadavg` before/after
capture. System busy ticks exclude idle/iowait and guest double counting.
Native, observer and fixture CPU intervals differ; raw deltas and actual bounds
remain visible. The residual screen uses a conservative all-core mismatch plus
tick-resolution envelope. Raw negative residual is retained; only derived
residual estimates/bounds clamp at zero. Bounds crossing the predeclared0.50
core threshold are ambiguous, unavailable observations stay unavailable, and
samples shorter than100ms are resolution_limited. None proves quiescence.
All these states conservatively suppress comparative ratios while retaining
valid absolute results. This can leave fast finite workloads without a ratio;
quiet acknowledgement and A/A do not repair clock quantization. Investigate
admission/control evidence instead of inventing a zero load measurement.

Contract A/A is labeled `contract_reproducibility`; it tests byte/count/request
agreement without comparing timestamps. A separate `--timing-aa` finite campaign
uses labels `go-a` and `go-b` for the same frozen Go binary, five warmups and30
measured samples per label. Its kind is `timing_aa`, never candidate evidence.
Run it for the primary latency families before practical interpretation, in
its own approved windows, for example (all common binary/fixture/manifest/token
arguments remain as in F1):

```sh
python3 harness/phase6_workloads.py run \
  --go /home/ajs/.cache/pb-phase6/baselines/pb-go --candidate "$P6_RELEASE" \
  --fixture "$P6_FIXTURE" --meter bin/pb-meter --manifest "$P6_MANIFEST" \
  --tier finite --workload one-row --workload many-small --timing-aa \
  --warmups 5 --repetitions 30 --seed 2026 --collector-mode full \
  --window-budget-s 900 --quiet-window-token "$P6_QUIET_TOKEN" \
  --out /home/ajs/.cache/pb-phase6/timing-aa-primary-f1
```

Duration order is explicit AB for even session index and BA for odd index;
finite ordering derives a stable per-workload seed so every workload need not
repeat one global shuffle. Preserve those fields in sample journals. Use the
same frozen provenance manifest and scenario seed when aggregating independently
reserved duration windows. The offline command never starts children or locks:

```sh
python3 harness/phase6_workloads.py aggregate \
  --report /home/ajs/.cache/pb-phase6/soak-first-steady/report.json \
  --report /home/ajs/.cache/pb-phase6/soak-second-steady/report.json \
  --out /home/ajs/.cache/pb-phase6/soak-steady-descriptive.json
```

Aggregation requires identical binary/fixture/meter/collector/shared-harness,
stable parent manifest/budget/fixture-build identities, workload/sink/duration
and collector mode, plus distinct session indices. It retains each session
value and range without bootstrap at any duration n, including n<=3; it never
revives a quarantined ratio. New seeds, builds or observers form separate series.

Two finite resource-pressure cases complete the original large-dictionary and
decompression scope: `dictionary-large-lz4` and `dictionary-large-zstd`, each
nine4-row batches,16-entry deltas to64 entries with1MiB compressible values,
then500ms open-stream hold before fatal status. They visit64MiB twice, reset
16MiB twice and end16MiB. No whole stdout is retained. Hold-dominated elapsed
and resource-pressure throughput remain absolute only; primary latency budgets
remain unchanged. The fixture owner ran Go/R4 Rust across both codecs:4/4
functional checks matched the full stdout SHA256
`3ab3830e286d3c63776291df3cfd4f2381708c7ef782257758b3b0050b2ed06f`,
32506837 bytes,9 dictionary messages (1 initial,6 deltas,2 replacements/resets).
Maximum wire messages were72896 LZ4 and2191 Zstandard bytes, below4194304.
The strengthened collector gates revalidated those saved summaries without
rerunning pressure load. Raw artifacts are under cache `tmp/fx/r5-logs/`;
`tmp/r5-saved-revalidation.json` records this revalidation. These are functional
checks, with no performance ranking or accepted final diagnostic owner envelope.

The collector separately reran tiny finite and all five one-second modes with
immutable r5 and frozen Go/R4 Rust, plus one-row hash-framing control:14/14
valid. Results are cache `tmp/review-r5-small-1791983701170602345/`.
The fixture owner's small cyclic ten-batch event-drop Go/Rust checks additionally
cover resets independent of retained events. The separate preserved R4 diagnostic pressure checks are described below;
no release timing or quiet token was produced here.


The parent then authorized two large diagnostics-only functional probes on
preserved R4 `pb-debug-diagnostics`, SHA256
`1f251566bdcfd31d1af6d7b99f5c43dc65c34abcfc7863d7e193ab4e7b19ba0d`.
Both binary and immutable r5 fixture hashes matched before/after. LZ4 and
Zstandard passed every exact byte/count/frame/request/panic/terminal gate, with
9 batches,36 decoded rows,19 Flight messages and actual RAII peak inflight1.
The dictionary owner maximum was67109124 bytes and batch maximum67109176;
compressed payload maxima were72888/2184 bytes. Staging maximum length4194413,
capacity8388688 and terminal last capacity0 were observed in both sidecars.
Exact fixture counters demonstrate two replacement resets and complete decoding
through the final16MiB generation. There is no last dictionary-owner size or
RSS-release counter: these maxima cannot prove post-reset dictionary release.
Native peakRSS was154828800/154820608 bytes, retained only as functional resource
observations without comparison. Raw proc snapshots are retained; fitted
post3-second-warmup trends have zero points and remain null. Source/fixture/
collector/diagnostic identities and full sidecars are preserved in cache
`tmp/review-r5-large-diagnostics-1791983891900949854/`. These2/2 checks use the
actual handoff counter, never the historical construction marker, and do not
establish HTTP/2 read-ahead or a release memory envelope.


The review correction validation ran77 collector tests successfully, then37
current targeted admission/campaign regressions after the final changes (four
new checks beyond the77-test suite). Logs are cache `tmp/review-all-tests.log`
and `tmp/final-targeted-tests.log`. They cover omission/churn rejection,
short/ambiguous/negative CPU components, observer/fixture ceiling screens,
identity/budget changes, A/A and measured TypeError atomic reports, contract
mismatch quarantine, per-workload AB/BA order, timing A/A labels, descriptive
aggregation, visibility coverage/capacity and after-exit milestone counts.
Mocked campaign tests acquire no real quiet window and make no timing claims.
Shared `run.py`, `meter.c`, Rust sources and Cargo files were unchanged by this
implementation. The next gate is independent measurement re-review before any
coordinator-authorized campaign.


## Measurement review r2: selected roles and completed observations

Native comparison manifests now require explicit measured roles in the existing
schema: `baselines.pb-go`, `build_flags.candidate.pb` and `meter`, each with
`path` and `sha256`. Selected executables must match their declared role's
content hash before any lock. Content-identical copies are deliberately allowed;
selected and declared paths are both recorded and rehashed. The candidate entry
must declare `build_kind: "normal_release"`, `features: []`,
`cloud_synthetic_token_enabled: false` and exactly this argv:
`["cargo", "build", "--locked", "--offline", "--release", "--bin", "pb"]`.
`candidate_manifest` is a hashed `{path,sha256}` identity for the actual durable
build manifest, whose `builds.pb` object must exactly equal the selected normal
release declaration, including flags/features/kind. Debug, diagnostics,
symbolized, custom-profile and cloud builds are excluded. The parent supplies
and accepts that frozen build chain; R4 samples above remain historical
functional evidence, not admission of a current native release candidate.
Timing A/A selects no candidate: both `go-a` and `go-b` bind to the declared
Go identity, and the selected meter still binds to its declared identity.

End-of-window identities deduplicate resolved paths, reject conflicting hashes,
and include all successfully bound runner paths, the verified fixture toolchain,
selected and declared measured executables, selected candidate build manifest,
and the existing fixture snapshots/manifests/budgets/shared harness. Any change
invalidates the partition while retaining completed sample journals. A build
manifest/source pointer alone cannot substitute for an executable-role match.

Immediately after `collect` completes, bounded `capture.json` and `sample.json`
are atomically written with observed byte/hash/count/first-record/native-resource
fields, `capture_complete: true`, `valid: false` and
`contract_validated: false`. Post-capture validators can enrich that sample;
exceptions merge errors into the completed result rather than replacing it
with an error-only record. Even a cleanup exception reaching the campaign
wrapper reloads saved sample/capture observations and marks them invalid.
Only a fully validated sample sets `contract_validated: true`; completed capture
alone never earns a comparison or an unvalidated output-contract assertion.

Every campaign report/manifest now records an explicit boolean `tls`; the offline
aggregate identity requires it and rejects missing/nonboolean or mixed conditions.
TLS and plaintext remain separate series, even with identical binaries/scenarios.
Same-condition replicated windows use the same scenario seed and fresh session
indices; new-seed investigations are separate series. The old r3 debug reproduction
is clearly historical. The bounded direct Go functional example selects immutable
r5; any current Rust functional example needs a parent-authorized frozen build.

These changes are validated by pure/mock causal negatives matching independent
review r2 probes N1–N4, with no native samples, pressure reruns, campaign, real
quiet acknowledgement or lock acquisition. The review r2 document and its cache
probes remain unchanged. Current source hashes and exact validation results are
in cache `tmp/collector-review-r2-resolution.json`; independent re-review and a
new parent manifest remain required before measurement admission.


The r2 follow-up passed all81 scoped pure/mock collector tests in
`tmp/r2-source-settled-tests-final.log`. Process spawning and actual flock
were guarded against execution; the lock primitive test mocked its syscall.
The two native test classes were deliberately not rerun, as this review asked
for no new functional/pressure samples. Earlier setup failures (missing executable
mode on mock files and a documentation-test path typo) were corrected; their
logs remain in cache. No campaign, real quiet token, lock, agent or commit was
created. Selected-release source/build acceptance remains separately owned;
these tooling corrections do not accept the historical R4 candidate for native
measurement or settle the separate production dictionary correction.


## Measurement review r3: retained execution paths and parent bytes

Campaign samples now receive the resolved, verified fixture and meter paths from
`binary_info`, just as measured clients do. Repointing the original argument's
symlink after binding cannot substitute a different fixture or meter for any
contract-control, warmup or measured invocation. End checks retain those same
verified paths; this is the focused symlink correction, not a general guarantee
against replacement of an actual frozen executable.

The parent manifest is resolved and read once with the existing bounded JSON
limit. Both its parsed object and SHA256/byte size come from those exact read
bytes. That initial resolved identity supplies the report and end-check baseline;
a later file replacement cannot silently rebase the embedded provenance. If a
replacement occurs after the read, the existing end check invalidates and
quarantines the partition, preserving absolute mock/sample records. The report's
parent identity still describes the originally parsed bytes, rather than the
changed file. No new manifest schema or campaign prerequisite was introduced.

Pure causal regressions repoint cache-only fixture/meter symlinks after binding
and verify all eight invocations retain the original resolved paths, atomically
replace a parent immediately after parsing its captured bytes and verify end
quarantine with the initial identity, and confirm unchanged/whitespace-bearing
JSON bytes have the correct hash, size and resolved path. These are filesystem
mocks with no native execution or lock acquisition. Resolution and fresh source
hashes are recorded in cache `tmp/collector-review-r3-resolution.json`; R3-A/B
require independent re-review, and the separate production dictionary correction
and release acceptance remain parent-owned.


R3 correction validation passed all84 scoped pure/mock tests in
`tmp/r3-scoped-tests.log`, including the three new race/byte-identity tests and
all previously scoped N1–N5 gates. Spawning/fork/system/actual flock primitives
were guarded and host metadata mocked. Native test classes and pressure probes
were not rerun. Review documents/probe files were read only; no shared-source,
budget, fixture, token, campaign, agent or commit change occurred.
