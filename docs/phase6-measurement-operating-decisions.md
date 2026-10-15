# Phase 6 measurement operating decisions

These decisions precede every native timing campaign. They respond to the fresh
[Opus tooling review](phase6-measurement-review-opus-r5.md); they do not change
the frozen collector, budgets, plan, candidate or prepared manifest. At the time these decisions were made, no quiet window or campaign had
started. The first same-Go A/A and matching observer-control windows have now
completed; see [measured evidence](phase6-measurements.md). No profiler or soak
has run. The review grants tooling
readiness for the first same-Go A/A and matching observer control, subject to
provenance and actual quiet-window admission.

## R5-1: keep conservative admission and descriptive finite observations

On this host the per-sample CPU uncertainty alone prevents ratio admission for
fast finite samples. Null A/A ratios are expected and are not a failed functional
sample or proof of equal performance. All required output, process, request,
identity and completion gates still apply. A/A absolute distributions will be
reported descriptively, with their uncertainty and observer controls.

The coordinator elects **absolute-only interpretation for quarantined finite
results**, before viewing any candidate timing. We will not reconstruct a speed
ratio or apply a relative regression budget to samples the collector quarantines.
Side-by-side medians, ranges, raw observations and limitations may identify a
practical concern worth investigation; they cannot establish a budget pass or a
statistically admitted speed claim. An unavailable budget result remains unknown.
No partition-level interference replacement is introduced. Any future change to
that policy requires a prospective plan/budget change and fresh review before
new candidate measurements. Longer duration samples retain the existing screen
and all contract, schedule and observer restrictions.

## R5-2: observer controls precede interpretation

Every output-heavy finite workload requires its matching hash-framing control
before interpreting wall time or first useful output. Primary and control remain
separate reports and are never pooled. A fixture/observer-bound observation is
not evidence of client throughput or an unconstrained client wall-time advantage.
The full parser, fixture expected-output encoding, sink and pipe costs are
reported alongside native client costs; none is silently subtracted. The
collector's throughput quarantine remains intact.

## R5-3: bounded window abort and cleanup

The campaign coordinator uses SIGINT for an orderly abort, not SIGTERM or SIGHUP.
The latter bypass Python cleanup and can leave the separately grouped fixture or
meter/client alive. Before starting, retain the coordinator PID and its owned
fixture/meter/client process identities. Before releasing an agreed quiet window,
check that those exact identities and process groups have exited; inspect the
known immutable executable paths for unaccounted children. Never terminate
another thread's process merely because its executable name matches.

If orderly cleanup fails, stop and reap only the recorded campaign-owned groups,
record the interruption and incomplete evidence, and confirm their absence before
announcing release. The lock alone does not prove cleanup or quiescence. A killed
driver with no final report is an incomplete campaign, never an accepted sample.

## R5-4: complete resource reporting from retained data

Final reporting must include descriptive per-label sample count, median and
range for minor/major faults, voluntary/involuntary context switches, block I/O,
procfs I/O and encoded/decoded/output byte rates, in addition to existing summary
metrics. Derive these from the retained sample journals without rewriting raw
records or admitted ratios. Missing values stay missing, with available counts;
procfs I/O is not network traffic. Bounded batch-latency evidence and dropped or
uncaptured observations retain their coverage limits. This reporting work is
still outstanding until campaigns produce actual data.

[Resource interpretation](phase6-resource-interpretation.md) additionally governs
owner maxima: logical dictionary/batch bytes are not distinct heap allocation,
and slices can pin an entire aligned receive allocation. No R6 release timing,
RSS trend, soak or memory-release claim follows from earlier functional samples.
