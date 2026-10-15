# A1-04 sustained PromQL: offline investigation R1

The first missing checkpoint is **`series-forward-31`, action 67**. The candidate is still producing complete, correctly stepped table frames when the existing **62-second whole-session deadline** kills it. The expected cursor 7951 never occurs in any retained byte prefix after its last dispatch. The observer therefore did not overlook that target in the captured output. All 33 accepted candidate frames match the corresponding Go frames after replacing only the ephemeral fixture port with `#` characters.

The strongest source explanation is repeated full-result copying and traversal in Rust's per-key rendering path. That mechanism is established by source inspection; its exact contribution to this deadline failure remains an attribution hypothesis because there is no native stack/profile or internal model-state capture. No geometry, fan-out, step-15, deadline or acceptance rule should change on this evidence.

## Scope, identities and preservation

This is offline research, with no nested agents, native sessions, product execution, helper/CLI/compiler/build/probe, network, socket, PTY, common-lock acquisition, kill or live retry. No production, source, harness, original capture or prior document was edited. The only outputs are this document and:

`/home/ajs/.cache/pb-phase7/a1-retry04-sustained-investigation-r1/`

Read the prior accepted review `docs/phase7-review-a1-step15-successor-opus-r3.md` and `docs/evidence/phase7/a1-step15-review-r3-disposition.json`. Its actual Go single-key control is scoped to dense-1, dark, 120×40; its 15-row navigation model and dense sustained target arithmetic were accepted. This investigation uses the actual new sustained sessions rather than treating those prior source extrapolations as runtime proof.

The retained report is `/home/ajs/.cache/pb-phase7/under30-combined-a1-primary-dark-04/report.json`, SHA-256:

`80c09ba401ae0afd6f2fbd01bfaa45feee3bc5ac3a47cde010d900fc3b9bb0e0`

The runtime root receipt is `/home/ajs/.cache/pb-phase7/under30-combined-a1-retry04-preparation/coordination/receipt.json`, SHA-256:

`2f6765b8427e18eb0c9820a7361dace7829a3951a0ab701b649ecea29ea80b2b`

Both hashes were independently reproduced and preserved. The receipt reports 271.854813918 seconds through its final write, coordinator and supervisor ECHILD, an empty owned child/descendant ledger, unchanged end identities, and successful lock reacquisition/release. Its supervisor verdict is `child_failed`; clean cleanup does not imply functional success. I read those historical receipts without acquiring or probing the common lock.

The report contains 12 pairs and 24 attempted roles; 11 pairs are valid. Only `promql-dense-1000-sustained-120x40-dark/candidate` fails. Reference passes. Binary identities recorded in the sessions and verified against unchanged runtime inputs:

- Rust candidate: `6d80054f5d87b081bfe51bed014a6f64c8e9a01c2c7f7fcb4c6434e221868443`.
- Go reference: `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`.
- Live bench: `b874ac0a591b3b04bba25946b4037145271c4ebc11a2e65de836291d9a020372`.
- Shared observer/helper Python: `dc7574f058b9ff6ae7b77a912847fdbe02d8b68baab8728237d6ef040e6a44fa`.
- Inspected `src/tui/promql.rs`: `51387004ec5207391d8c60252d1eb62997ae200d8ec23a89237238a1f114cf55`.
- Inspected `src/tui/terminal.rs`: `5b9b9ba74a7c9767a0e1b70020b0557d6252345b7241a60c52b5fa7c7d0e4a26`.
- Sustained case hash: `cb30ce2d21eef30476e0db4817bda6eac344f5844a358a4f92c5cd123999a4c9`.

`original-hashes-before.json` / `original-hashes-after.json` preserve equal hashes for 398 original files, including all actual report/raw/session files and the inventoried Rust/harness sources. `source-binding.json` binds all 216 report source inputs: 87 Go, **64 Rust product inputs**, and 65 harness inputs, with zero mismatches. `verification.json` additionally verifies all 658 runtime identity records and all 169 receipt raw-artifact hashes, with zero mismatches. Report source/artifact start/end structures are equal. No source or binary rebinding was performed.

## Actual action and checkpoint comparison

Both roles retain byte-identical case definitions, fixtures, 139 intended actions, and identical input/wait sequences through candidate action 66. The candidate completes 67 actions and 33 named checkpoints; reference completes all 139 actions and 70 checkpoints. The accepted sequence is startup, setup-ready, table-view, and series-forward-1 through series-forward-30. There is no earlier semantic frame divergence: all 33 corresponding full styled frames, including cursor and screen metadata, compare equal after masking only the different five-digit fixture ports.

Times below are seconds relative to each session's own `started_monotonic_ns`; these locate dispatch, output and deadline causally. They are not admitted performance measurements or a comparison of latency distributions.

- Candidate startup ready: 0.693650420; table-view ready: 1.710542 seconds. Table geometry and initial cursor 1 have already passed.
- Candidate series-forward-30 first match: 59.961534 seconds; ready: 60.056972866. Its first and accepted raw offsets are both **2,093,452**, with cursor **7681**, series 0030. The retained readiness history has 159 evaluations through that first match.
- Candidate action 66 evaluation: `1097497470110591` (60.097750868 seconds). Dispatch immediately before its write: **`1097497503376903` (60.131017180 seconds)**. Input is **18 repetitions of `1b5b367e`**, 72 bytes, matching reference's input for the same phase.
- Action 67 waits for `Table View`, `bench-series-0031`, and **`7951 | 256000 rows`**, output after the action, valid dense geometry, alternate screen, uncooked TTY, and 80 ms stable cells. It never completes.
- Candidate exits from SIGKILL at **`1097499398899165` (62.026539442 seconds)**. The configured case timeout is **62.0**, formed by `session_timeout + idle_seconds` (60 + 2). About 1.869 seconds remain on that whole-session clock at dispatch; this is not a fresh per-phase allowance.
- Reference series-forward-30 first/accepted offset: **2,085,018**. Reference series-forward-31 first/accepted offset: **2,154,138**, first match 24.900113015 seconds, ready 24.995066822 seconds. It subsequently reaches End, all reverse targets, Home, chart view, idle, and ctrl-c, exiting 0 at 50.817337132 seconds with restoration true.

`details.json` retains exact dispatch/checkpoint stamps and offsets for both roles; `comparison.json` retains every actual action, intended action and frame summary. Absolute raw offsets differ between roles and cannot be used as common progress counters.

The arithmetic is correct for the actual prefix: after forward-30 the zero-based selected row is 7680. Forward-31 needs `ceil((31*256 - 7680)/15) = 18` PgDown keys, selecting zero-based row 7950, displayed cursor 7951. The final rendered zero-based row 7920 is `7680 + 16*15`; it still belongs to series 0030. This is two displayed steps short of the target, rather than an observed 14/16-row stepping discrepancy. It does not prove that exactly two keys remain unprocessed inside the killed native model.

## Raw replay and readiness

Both roles' archived `stdout.bin`, `execute/stdout.bin` and `session.stdout_hex` are byte-equal. The enriched archive `session.json` and the earlier `execute/session.json` differ as files; their actual frames, actions and final frame are equal. The archive session hashes bind the report:

- Reference session: `b50025edb17e7e89a3091273ef5ba211d409d0141eb1e04916b85eb07c735d5c`.
- Candidate session: `b5ceb679f7637882d17e8410ce2877661abc595b408087f17041e4aa351d3e00`.
- Reference raw: 4,222,005 bytes, `294bed91a4aa9a85e69085fd0edd4fd9fa4351af127535404c71d19a1bda4c3b`.
- Candidate raw: 2,154,892 bytes, `5783ca175061dfc07c40255dd934d31110ea0fd697fa439ae87f4642581f53d2`.
- Both stderr files are empty (`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`).

One ascending-offset terminal replay per role reproduced all **103 accepted frames** exactly, checked useful/geometry predicates at recorded latency first-match boundaries, and reproduced both final frames. This does not claim an exhaustive replay of every readiness evaluation. The candidate retains 4,721 evaluations through accepted first matches, reference 3,157; anchor/idle histories are deliberately omitted. Both have decoder-offset schema 2 and no unsupported terminal controls.

For the unfinished phase I then fed **every single byte** after offset 2,093,452 and inspected the footer at each prefix. Target cursor 7951 never appears, including transient partially repainted footers. The remaining **61,440 bytes** contain exactly **16 complete renderer updates of 3,840 bytes**, ending with the renderer's `ESC[40;0H` terminator. Completed displayed cursors are:

`7696, 7711, 7726, 7741, 7756, 7771, 7786, 7801, 7816, 7831, 7846, 7861, 7876, 7891, 7906, 7921`.

Their complete frame ends range from **2,097,292** to **2,154,892** at increments of 3,840. The last footer text is complete at **2,154,819**. `summary.json` records every complete end; `replay-results.json` also preserves partial-digit transients so those cannot be mistaken for selected model rows.

The final frame satisfies `dense_table_geometry` when supplied its actual `7921 | 256000 rows` token. For action 67 it correctly fails both basic useful-text and geometry predicates because the expected series and footer are absent. No target frame was present for the observer to admit, irrespective of its 80 ms stability rule.

The failed action's readiness evaluations are **not retained**: `bind_decoder_offsets` at `harness/phase7_bench.py:609` serializes records only for completed action/frame boundaries. Primary capture has no stdout chunks and no termios observation timeline. Thus individual final-burst output times and each failed predicate evaluation cannot be reconstructed. The exhaustive footer-prefix check establishes absence in the captured stream; it does not expose unpublished native model state or the writer's pending slot.

## Native progress and fixture effects

All 33 retained process samples during the last burst, from **60.142718048** through **61.984075210** seconds, show state **R**. Across that 1.841357162-second sample interval:

- User ticks increase 3926→4056 and system ticks 1484→1538, with the recorded clock rate 100 Hz.
- Minor faults increase 13,506,354→13,967,682, a delta of **461,328**; major faults remain zero.
- `wchar` increases **61,440** and `syscw` increases **16**, agreeing exactly with the observed final-burst output. `rchar` is unchanged, consistent with previously buffered input, without proving its internal queue position.

The full candidate capture has 1,065 process samples, 995 R and 70 S. Native wait4 records 40,585.43 ms user CPU, 15,400.71 ms system CPU, 13,967,682 minor faults and 337,534,976 peak RSS bytes. Reference records 90,513.078 ms user CPU, 1,630.42 ms system CPU, 502,432 minor faults and 1,657,409,536 peak RSS bytes. These are retained diagnostics from functional captures; they confer no performance admission, ratio, resource verdict or subset acceptance. Sampled high-water RSS can differ from wait4's recorded peak and is preserved verbatim.

These samples and continuing complete output reject a sustained idle/deadlocked-native explanation for the observed last interval. They cannot identify the precise instruction executing at SIGKILL or exclude an unobserved stall after the last sample.

Fixtures are identical. Both roles make exactly the expected three startup requests: home, metric names and **one query_range**. All are expected routes; no navigation request or extra query occurs. The query body sent is **4,969,597 bytes** in each role. Its fixture completion is at 0.037119618 seconds for reference and **0.020842514 seconds** for candidate, before startup readiness and far before table navigation. No unreleased/delayed fixture effect explains the missing checkpoint. Different startup request arrival ordering is permitted by the reviewed fan-out model. Exact paths, receipt times, event times and body sizes are retained in `details.json`.

Candidate termination is signal 9, final alternate screen remains active, and termios restoration is false. These are consistent with the observer's timeout SIGKILL path (`harness/phase7.py:947`), which bypasses graceful terminal teardown; ctrl-c was never dispatched. They do not independently establish a second restoration bug. No exec error, native cleanup issue or surviving/unreaped owned child is recorded.

## Source mechanism: fact and attribution boundary

Facts in the frozen current source:

1. `src/tui/promql.rs:5660` handles each input event serially, calls `state.update`, constructs a full `state.view()`, and only then queues its bytes before accepting the next event. A burst is parsed into individual PgDown events, so each accepted key traverses this render path.
2. `ResultsTable` derives `Clone` (`:1836`) and owns `Vec<Row>`. Each `Row` derives `Clone` and owns four `String` fields (`:90`).
3. `results_table` (`:4775`) first does `let mut table = self.table.clone()`, then `table.with_rows(self.data_rows.clone())`. The second operation replaces/drops the first full copied row vector. Both are deep row/string clones in the source, even though render needs a small visible page. Nominal source work is two 256,000-row clones, comprising 2,048,000 String clone operations per view; this is a static work count, not an instrumented allocator count.
4. `visible_rows` (`:1867`) collects a vector of references to **all** rows even with no filter. `max_pages`, `with_rows`, `with_page_size`, `with_highlighted_row` and `view` call this repeatedly; the key update itself also collects/traverses visible rows (`:1929`). Only the eventual row rendering selects the page (`:2112`).
5. The source intentionally has a 15-row navigation step and a local rendered page calculation. `results_table` works on its copy; no evidence here justifies changing their accepted geometry/pagination behavior.
6. The corresponding Go `renderResultsTable` (`pkg/model/promql.go:1301`) passes `m.dataRows` as a slice to its table copy; that statement does not deep-clone each row's string payload as Rust's `Vec<Row>::clone()` does.
7. The writer has a nonblocking view slot and a separate writer thread (`src/tui/terminal.rs:642`, `:680`, `:855`). Even if pending views can coalesce, the Rust event loop already constructs each view before queueing it. The output proves completed published views, not all model updates.

**Inference, strongly supported:** repeated full-result allocation/copy/free and traversal is the concrete candidate-side workload most consistent with continued CPU activity, minor-fault growth and sequential valid frames until the fixed session budget expires. The failure is accumulated unfinished work within the original session budget, without an observed readiness miss or new geometry mismatch.

**Still unproven:** the proportion attributable to these clones versus table styling, reference-vector construction, allocator behavior, observer scheduling or other costs. There is no profile, stack, blocked-thread trace, CPU isolation evidence or instrumented per-key model history. Do not claim that removing one clone is proven sufficient, that the exact killed native instruction is known, or that extending the deadline would make the entire 139-action case pass. End/reverse/Home/restoration behavior of this actual candidate session remains unobserved.

## Minimal next step, proposed only

Commission a narrow source proposal that removes **per-view deep result ownership copies**, while preserving update ordering, page calculations, filtering, detail behavior and rendered bytes. A concrete candidate design is shared immutable row storage between state/table (for example `Arc<[Row]>`), with cheap table/view snapshots and unchanged local render pagination. Merely deleting the second clone leaves the first full `self.table.clone()` cost; merely deleting the first leaves `data_rows.clone()`. Review both sites together. The unfiltered all-row reference-vector fast path is a subsequent, separable optimization if needed.

Before any native work, independently review the ownership/lifetime change and narrowly verify row replacement, filter/selection, local render page sizing and exact frame behavior in an isolated proposal. Include this actual 18-key, 256,000-row prefix as the preserved behavioral target. No such implementation, compilation, runtime experiment or new authorization is requested or performed here.

Retain the current case, geometry, step 15, fixture, session deadline and all admission gates. Any eventual rebuilt candidate must receive its own source/binary binding and an authorized full required functional run; this failed run cannot admit a subset or performance. The user's strict **all native/performance work <1800 seconds** remains unchanged; this investigation consumed zero native-session/performance budget.

## Execution record and artifacts

All analysis entrypoints import the owned `guard.py` before analysis/harness imports. It installs an audit hook and process/exec/wait/kill/PTY/socket/ctypes/lock barriers, restricts writes to the owned cache directory, and sets a **540-second alarm** per execution. The final guard also explicitly refuses opening the common lock; earlier runs already refused lock writes and all flock/lockf operations. No lock operation or guard trip occurred. No test suite, broad source test, native probe or compiler ran.

Retained analysis-script failures, with no underlying artifact effect:

- First `replay.py` attempt failed during standard-library SSL import because the guard replaced `socket.socket` with a function. The guard was corrected to a subclass whose construction is blocked. See `replay.stderr`.
- Second attempt failed an overly strict assertion that enriched and execute session JSON files were byte-equal. Corrected to assert equality of actual frames/actions/final frame while retaining separate file hashes. See `replay-r2.stderr`.
- Third attempt completed the scoped raw replay with empty stderr and zero guard trips: `replay-r3.stdout`, `replay-r3.stderr`, `replay-results.json`. `summary.json` completed the fixture-port-only comparison and renderer-boundary audit with empty stderr and zero trips.

Primary artifact map (all relative to the owned cache directory):

- `original-hashes-before.json`, `original-hashes-after.json`: original artifact preservation.
- `source-binding.json`, `verification.json`: source/runtime/raw receipt bindings and process closure.
- `details.json`: checkpoint/dispatch offsets, fixture causality, binaries and case equality.
- `comparison.json`: all actual actions and frame checkpoint summaries for both roles.
- `replay-results.json`: accepted-frame reconstruction, failed footer-prefix scan, final predicates and native sample details.
- `summary.json`: 33 normalized full-frame matches, 16 complete last-burst frame ends and sample deltas.
- `guard.py`, `inventory.py`, `analyze.py`, `details.py`, `replay.py`, `summarize.py`, `verify.py`: inspectable pure analysis scripts; corresponding stdout/stderr files retain attempts.
- `SHA256SUMS`: final hashes of owned artifacts and this document, excluding itself.

Final guarded `/proc` read found **zero owned analysis processes other than the completing verifier**, and this task created **zero owned native processes**. All analysis executions completed; no background work remains. This is an offline diagnosis and a proposed next source review, with **no runtime acceptance and no performance admission**.
