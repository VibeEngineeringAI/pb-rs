# R6 short-duration retained-evidence review — Sol

**Accept the ten campaigns for scoped retained-evidence integrity. They support
consideration of the first long-soak tier, with the prerequisites below. They do
not clear an immediate soak launch or any quantitative performance gate.**
All twenty measured sessions and twenty separate same-Go controls were checked
against their captured artifacts and reconstructed workload contracts. Nine
measured pairs have matching delivered contracts; the final slow-sink hash pair
correctly fails that comparison. All eighty ratios remain null. Phase7 remains
USER-PAUSED. This review neither resumes it nor authorizes a native launch.

This independent offline audit covers
`campaign-r6-short-{steady,idle,reconnect,dictionary,slow-sink}-{full,hash}-01`
under `/home/ajs/.cache/pb-phase6`. Recorded execution runs from
2026-10-15 00:05:05.210737 through 00:32:30.327448 UTC, with separate cleanup
records. The local calendar date is 2026-10-14. The user's instruction to continue
sustained tests on the busy host is retained authorization context; it does not
waive CPU admission. No new native campaign was run here.

The root snapshot reviewed is `docs/evidence/phase6/short-duration-r6.json`,
SHA256 `997dc72f4fbbd20ce494b726af6327fdec2ad884614a31591c866e07f2aab2e3`.
Its ten unique report/resource bindings, cleanup records, every per-session
projection, comparisons, restrictions and global totals agree with the inputs.
The short-duration section in `docs/phase6-measurements.md` accurately describes
this snapshot. Its pending-review field is administrative context. Subsequent
root-owned status edits would have a different summary identity.

Findings, ordered by practical consequence:

1. **Medium — resource follow-up before the corresponding soak.** Go slow-sink
   RSS increases from 52,654,080 to 64,602,112 bytes in full mode, and from
   52,916,224 to 65,413,120 bytes in hash mode after the five-second warmup.
   Fitted slopes are 194,629 and 214,235 bytes/second; task counts rise 14→19
   and 14→18. Go reconnect rises 51,879,936→55,971,840 bytes with tasks 11→15,
   and 52,428,800→54,272,000 bytes with tasks 12→15; fitted slopes are 91,425
   and 39,778 bytes/second. These absolute trajectories warrant bounded fresh-seed
   controls before their respective first soaks. They do not establish a leak,
   and cannot be extrapolated linearly to thirty minutes. Runtime expansion,
   allocator retention and the busy host have not been separated causally.
   Go steady also grows about 2.1–2.8 MB after warmup, with increasing task
   counts; carry this observation into the resource review. No ranking follows.

2. **Medium — slow-sink pure schedule gate omits a conservation check.**
   In `harness/phase6_workloads.py:604–615`, the explicit slow-sink exception
   permits incomplete production but does not require
   `emitted + omitted_by_deadline + omitted_by_termination == scheduled`.
   Increasing the hash sample's omission count by one on a deep copy leaves
   `schedule_admission(...).errors` empty. The independent conservation
   predicate rejects it. Every actual capture passes the stronger check, with
   no hidden Send, terminal, timeout, collector or product error found. This
   is a future-validation coverage gap, not a demonstrated bad historical
   sample. Require that independent conservation check for each next sink
   session; `valid=true` alone is insufficient. No collector edit was made.

3. **Low — offline resource adapter is not a full native-exit validator.**
   Changing only a copied journal's `exit_code` to zero while retaining
   `termination={"exit":1}` is accepted by `summarize_bytes`. Its contract and
   summary checks do not cross-check that extra field. All forty real samples
   independently bind both fields to their saved meter and require exit one.
   Keep those direct checks in soak review. The adapter's descriptive output
   must not be promoted into whole-sample validation.

4. **Informational — comparison restrictions work.** All twenty measured CPU
   screens are flagged. All eighty comparative entries are null with their exact
   per-metric reasons. Slow-sink hash additionally has different delivered work.
   This is correctly quarantined evidence, with no accepted speedup, equivalence,
   relative memory budget, observer-effect estimate or throughput attribution.

The frozen parent raw SHA256 is
`a47093f4a3ea785a310f7ca1baa92444deaf7e21db5ba7db72dc897ddc3d047b`.
Its embedded contents, canonical stable identity, budgets, fixture build and
selected roles agree across every report. The current collector was rehashed:
`e2e83fa118c58a96ff55700be88f95f9b8b6479a289a8c59f447f836389fde51`.
Small bound runners, budget/fixture metadata and source snapshots, plus all
candidate source files in the R6 metadata, were independently rehashed. The
candidate remains declared `normal_release`, empty features, synthetic token
disabled. Recorded end checks are passed with no changes, and end identity
lists agree across campaigns.

Executable identities are recorded and cross-bound, not freshly whole-file
rehashed or executed in this audit: Go
`fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`, R6
`f1fb7a9e294a8f96bd9835de4c6f32318b0adf01bb164db648c4fb9c0ad5140f`, fixture
`c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314`, and meter
`17c3b73491b2a31e465f58eda9f210000b5cd886a4ba9fc0764dc3576899127c`.
The large Go toolchain executable is also recorded-only. This distinction
prevents historical identity attestations from becoming new binary evidence.

Each partition is `functional`, nominal 60 seconds, zero process warmups and
one repetition per binary, seed 2026, session index zero, plaintext. The duration
schedule is AB in every window because session index and round are zero; the
derived workload seed does not shuffle duration role order. Full parsing is
`native_unprofiled`; hash-framing is `observer_control`. Modes, roles, controls
and workloads are kept separate. The twenty same-Go controls all reconstruct
the identical one-row contract and are not measured duration observations or a
new timing A/A population. No controls contribute to duration resource statistics.

All forty sample files agree with their journal/control entries and saved native
meter fields. Capture files' false validation flags are correctly retained
pre-validation snapshots, and initial manifests' false/false/empty-summary
state is an initial snapshot. Completed samples have no errors or timeout,
complete capture and validated contracts. Exact stderr bytes decode to fatal
`COMMAND_FAILED`, remote `InvalidArgument`, message `fixture stream complete`,
retryable false, with native exit one. This is deliberate failure termination,
not successful stream completion, cancellation or an unexplained product exit.
Fixture stderr is empty; no panic or failed batch Send event is present.

Every non-reconnect observation has exactly one About GET, one authorized DoGet,
one subscription, exact ticket `{"stream":"logs"}`, one auth and ticket check,
and zero invalid auth, ticket mismatch, unexpected request or DoGet-before-About.
Each measured reconnect instead has exactly 101 DoGets/subscriptions/auth/ticket
checks, 100 orderly EOF/OK terminals and the final deliberate InvalidArgument.
All request and terminal events are retained and agree with exact summaries.
Discovery completes before DoGet. Reconnect has exactly 100 eight-row batches,
800 rows, 100 server sleeps of at least 100 ms, and 100 independently reconstructed
retry gaps of at least 500 ms. No extra retries or missing subscriptions appear.
Normalized retained config bytes agree with both recorded config hashes.

The eight strict mode pairs have exact applicable coverage. Steady and dictionary
each deliver all 3,000 scheduled 128-row batches, 384,000 rows, zero deadline or
termination omissions. Idle delivers no rows or batch separators and holds for
at least 60 seconds after its explicit schema and sleep. Dictionary has exactly
3,000 dictionary messages: one initial, 2,988 deltas, eleven replacements and
eleven resets, reaching logical size 4,096. Rebuilt time buckets match batches,
rows and dictionary categories, with no overflow or missing churn. Logical
dictionary size and decoded bytes are not allocation or release measurements.

The full slow-sink pair matches at 462 batches / 59,136 rows, with 2,538 omitted
by deadline per binary. The hash pair has Go 466 / 59,648 with 2,534 omissions,
and Rust 467 / 59,776 with 2,533 omissions. Each has exactly 3,000 scheduled
batches, one deadline stop, no termination stop or termination omission, and
`emitted + omitted_by_deadline = 3000`. Only emitted output contributes to
fixture hashes, rows and byte counts. Actual raw start/done/visibility events
cover every emitted batch. The hash pair's differing contract fields are exactly
`batches`, `rendered_rows`, `stdout_bytes`, `stdout_sha256`. Both individual
streams pass their own output oracle. The ten-second initial stdout pause,
20 ms read delay and 4,096-byte read chunk are preserved. Slow-sink stays
absolute-only even when delivered contracts match.

Live deadlines are exactly start + 60 seconds, with 20 ms scheduled intervals.
For complete producers, the last due batch is at 59.98 seconds; a deadline stop
can therefore be recorded slightly before 60 seconds. Requiring every native
wall time to be at least 60 seconds would misstate this fixture. Measured native
walls span 59.984468–61.894768 seconds. Slow-sink can finish an in-progress Send
past the producer deadline and then drain buffered stdout. Its missing scheduled
batches are not silently counted as delivered.

Event `lateness_ns` is measured before batch preparation. Exact live-summary
lateness is due-to-first-Send, after preparation. The audit checks raw event
bounds, reconstructs the summary lateness sum as
`sum(batch_done_time - due_time) - exact_Send_call_sum`, and bounds the maximum
between corresponding batch-start/done envelopes. The saved live Send summary
equals the batch Send aggregate and fits inside the larger event spans. Exact
per-batch Send-start timestamps are not retained, so no independent per-batch
Send decomposition or exact maximum reconstruction is claimed. Send wall includes
marshal/copy/flow control; it is neither pure blocking nor proof of application
consumption.

An independent streaming serializer reconstructs whole output length, SHA256,
row count and LF batch framing for every delivered shape. It implements the
frozen basic/dictionary values, global sequence, sorted JSON keys, Go HTML
escaping, number spelling, microsecond timestamps and dictionary reset cycles.
Steady is 68,540,781 bytes; dictionary 32,970,756; reconnect 138,657. Slow-sink
full is 10,464,545; hash Go 10,555,629; hash Rust 10,578,400. Per-shape digests
are in `independent-oracles.json`.

**Twenty-four observations retain complete stdout:** twenty one-row controls
and four empty idle outputs. These were independently whole-file rehashed and
compared to the oracle. **Sixteen measured observations retain capped 65,536-byte
prefixes.** Every prefix byte and dropped-prefix count matches; their recorded
whole-stream digests/lengths and independent fixture expectations agree with the
reconstructed oracle. Those sixteen historical streams cannot be rehashed or
reparsed in full from retained raw bytes. Oracle reconstruction does not restore
missing capture bytes or authenticate collection history. Full mode records
1,655,882 JSON parse attempts across its sessions/controls; hash mode records
eighteen, one per nonempty observation. Later hash-mode records have no parser
sequence check (`sequence_errors=null`), even though their deterministic oracle
can be reconstructed offline.

Raw events have exact sequence and kind coverage, no loss, and complete
batch start/done/visibility/latency correlations. Row, encoded-message and
decoded accounting match summaries. All latency arithmetic, fitted batch trends
and visibility buckets were recomputed. Native CPU equals wait4 user + system;
wall, per-row CPU, rates and first-record/terminal latencies match their operands.
Native CPU/RSS/fault/context-switch/block-I/O remains distinct from observer
RUSAGE_SELF and fixture lifetime CPU differences. Fixture output encoding wall
overlaps fixture CPU and is never subtracted from native measurements. Proc I/O
is not network traffic. Proc RSS can exceed recorded native peak RSS because
their collection/accounting scopes differ; no false peak ordering was imposed.

The CPU screen was independently recomputed from saved 100 Hz host counters,
twenty CPUs, observer/fixture deltas, interval endpoints, all-core timing mismatch
and tick uncertainty. Raw residual, clamped bounds, threshold precedence and
admission flags match the pure gate. Measured residual envelopes range overall
from lower bound 5.106707 to upper bound 6.723534 busy cores. They all exceed
the predeclared screen. Controls are separately resolution-limited. A fixture/
observer potential-limit result of `below_screen` does not override flagged
host interference. Neither busy-host authorization nor absolute-data integrity
admits comparative interpretation.

The existing bounded, read-only `summarize_bytes` adapter was run on the exact
report/journal buffers for all ten windows. Its full groups, counts, missing/
available metrics, source flags, coverage, role provenance, source restrictions
and absolute report summaries match every `resources-offline.json`. Input paths,
sizes, hashes and reread receipts agree. Resource rate arithmetic and raw trends
were additionally checked independently; the adapter itself retains rates rather
than recalculating them. This validation does not make the adapter a source-
acceptance or historical-authentication gate.

There are 5,859 retained live proc observations and 28 missing measured attempts;
controls have zero proc observations and twenty missing attempts. Exactly 5,386
observations contribute after warmup, 261–282 per measured session. Their first
timestamps are about 5.006–5.186 seconds after observer start and their last
about 59.806–61.814 seconds. The precise collection origin is not separately
retained, but is bounded by observer start and native start; no sample lies in
the resulting five-second admission ambiguity interval. OLS slopes are invariant
to that constant origin offset. RSS/FD/task/sampled CPU first, last, extrema and
slopes all match independent reductions. Every measured FD trajectory is seven.
Candidate tasks stay at two; Go task trajectories and material RSS growth are
described above. Candidate steady/idle RSS is flat; candidate reconnect and
slow-sink RSS increase by about 41–45 KB, and dictionary fluctuates across its
cycles. These are absolute observations, not comparative acceptance or leak/
release proofs. Procfs fields are read sequentially, not atomically; the audit
does not recover unsampled peaks, owner maxima or object lifetimes.

Execution argv, modes, workload, identities and recorded token hashes bind each
window. The upfront worst-case requirement is 720 seconds (two controls and
two sessions, including thirty seconds cleanup per observation), fitting the
870-second collector reservation with thirty seconds outer reserve. Recorded
exits are zero, no outer timeout is recorded, native intervals fit execution
bounds and do not overlap within or between campaigns. Cleanup attests PID gone,
empty matching-process set and common-lock reacquisition/release. These are
coordinator attestations, not newly witnessed historical lock/process facts.
No live locks, tokens or operational receipts were requested or exercised here.

The final reducer completed **372,650 assertions, zero failures**, over
**761 input files / 28,915,775 bytes**. Before/after whole-file size/SHA256
inventories are identical. Bounds are 128 MiB per input, 128 MiB inventory total,
1,000 files per partition, 10,000 JSONL records and 1 MiB per JSONL line. Output
oracles stream one row at a time and retain at most 65,536 prefix bytes.

Negative probes preserve forty-one copied-buffer/structure cases, including two
expected pure-gate scope gaps and independent rejection of both. Additional
in-memory Output mocks cover twelve original/mutated full/hash cases; corrupted
later hash JSON is detected by the output oracle despite reduced parser coverage.
One hundred twenty independent status mutations reject changed exit, collector
error, timeout, fixture panic, failed Send and wrong remote terminal for each
measured sample. They launch no processes and add no experimental observations.

Failed reviewer attempts are preserved. Attempt 1 had 107 failed reviewer
assertions and then a duplicate-keyword output-writing exception; its script,
log and partial artifacts remain. Its mistaken finite shuffle rule, integer
versus floating duration in canonical JSON, and a reused `mismatch` variable
were corrected. Attempt 2 retained seven failures: it still incorrectly used
seed parity for duration order, and sorted JSON keys in a negative test changed
binary-map insertion order while reversing its journal. A targeted adapter
probe shows the original-order reversed journal is correctly rejected. Attempt
3 retained one failed assumption that the resource adapter validates
`exit_code`; this became the explicit low-severity scope finding above, with
independent status checks added. Attempt 4 passes. An initial source lookup
also used a nonexistent nested fixture path; the bound snapshot actually has
flat `phase6.go`. No evidence was repaired to make a reviewer assertion pass.

All reviewer writes are confined to this new document and
`/home/ajs/.cache/pb-phase6/short-duration-review-sol/`. Before analysis imports,
the retained guard installs process/exec/fork/spawn/system/kill/lock/socket audit
denials, replaces OS process and fcntl lock entry points, and rejects unsafe
imports. It restricts writes to these owned paths and rejects live `/proc` and
`/sys` reads. TMPDIR/TMP/TEMP point to the owned cache. Explicit fork, spawn,
system, flock, subprocess-import and socket-import self-checks all raise before
execution. No collector/fixture module was imported: selected pure definitions
were AST-extracted, with in-memory logs for Output mocks. No native/product/
fixture/meter/build/compiler/profiler/network execution, pressure, subprocess
test, subagent, commit, real lock or live process control occurred. The bound
fixture snapshot resides under the existing cache `tmp` directory; system
`/tmp` was not used.

The evidence is ready for review of a first-soak proposal, subject to these
specific remaining prerequisites:

- Complete bounded fresh-seed controls for the material slow-sink and reconnect
  resource trajectories, retain all outcomes, and review whether growth settles
  or persists before each corresponding long soak. Carry steady growth and all
  task observations forward. Select fresh output folders/session identities;
  do not overwrite these captures or pool modes/seeds.
- For each proposed thirty-minute full-collector mode pair, coordinate the
  existing separately reserved window and prove the entire duration partition's
  worst-case deadlines plus cleanup fit before launch. The plan's five first-
  tier windows are seventy-five minutes each, with exact reconnect schedule and
  slow-sink drain allowance. No Phase7 restart is implied. Existing busy-host
  authorization can support scoped retention screening; comparisons stay gated.
- Preserve exact output/request/terminal/config/identity validation, complete
  schedule/churn/visibility coverage, post-warmup resource histories and direct
  meter checks. Apply independent slow-sink omission conservation and reject any
  real error despite the incomplete-schedule exception. Keep full/hash separation.
- Review each first-tier result before its later independent-session tier.
  These short traces establish neither thirty-minute bounded retention nor
  leak absence, dictionary release, quantitative budgets or whole-Phase6
  acceptance. A comparative claim still needs CPU admission and the remaining
  predeclared observer/statistical gates; no ranking is inferred here.

Reproduce the bounded audit with:

    python3 -I -B -S /home/ajs/.cache/pb-phase6/short-duration-review-sol/analyze.py
    python3 -I -B -S /home/ajs/.cache/pb-phase6/short-duration-review-sol/supplemental.py

`review-receipt.json` in that cache binds this document, guard calls, source and
evidence inventory, reducers, successful/failed logs and supplemental probes.
It is an offline audit record, not an operational measurement authorization.
Key audit artifact SHA256 values:

- `analyze.py`: `3fbdfcec047f15554040c70b4bccb843e17ffae3b3f5087cf72c2892556e7e9e`.
- `guard.py`: `a50cb8c0555064207b7111953c01b2c25cb97d81920c299f6e111c64faf0bb85`.
- `analysis.json`: `f85413cb2ab202d264f9d71364b596a45bb3c9e07b88a78003db17af4ca9c83e`.
- `inputs-before.json`: `b74b25b72c60d67c97319376dea41347323e1ed2c645acf659ce7a5165393c44`.
- `inputs-after.json`: `b74b25b72c60d67c97319376dea41347323e1ed2c645acf659ce7a5165393c44`.
- `independent-oracles.json`: `ed8fa30d14fc5a2476762fc917362ddaaf92bd9a64609560e3ef7b3873e3e5eb`.
- `whole-output-proofs.json`: `60d810f318b4d3f42b622e2f86b862d7784d0bb5a467c477b6422347e892b793`.
- `negative-probes.json`: `1a9c59318fe2d6b27a4be12db1cccd051bef18a91585295d41174bf98162b012`.
- `histories.json`: `a0f5a1742305411cd8debb287a2000cf429ba1036d6cadb0974bf79fd2793bb6`.
- `supplemental.json`: `78fee569b692b6d8e542f2a14133b69443f3e36382addd16be5782dd159c6e4c`.
- `final-facts.json`: `d0eae8ec1e678ed9551ae463d6ea0d82e54765ea76efa5812e4cbda0f6b1f6c7`.
