Final narrow Opus r3 followup, complete and frozen.

F1 RED then GREEN: Added a large legacy reconstruction regression containing a100KiB UTF8 seed, a seed-ending incomplete ANSI control completed by stdout, and about465KiB stdout whose ANSI/UTF8 deliberately cross64KiB boundaries. Instrumented Terminal._control records pending-buffer bytes; this is a resource invariant, not a timing benchmark. The old whole-body feeds preserve modes but fail with464,910 pending bytes versus the65,552-byte bound (red.log). After the fix, the same test preserves initial[1004]/final[1002,1003,1004] and passes the bounded-pending invariant (green.log).

The ONLY production delta from the prior df769c7d freeze is feeding both encoded captured initial_screen and raw stdout in at-most65,536-byte slices, then a final empty flush after stdout. No seed flush was inserted, preserving parser state across the seed/stdout boundary. No mode parser expansion or unrelated changes.

F3: Strengthened the existing exactly-full history negative control with home+LF at a non-bottom row. Added a DECSTBM region-bottom case on a four-row terminal. Existing implementation passes both. An external, in-memory mutation runner independently kills both dropping the cursor-bottom condition (false overflow on home+LF) and ignoring declared margins (missed region eviction). mutations.log contains expected test failures followed by KILLED True; no repository source was mutated for these checks.

Validation:
- Focused F1/F3 tests:3 pass (green.log).
- Final full test_phase7 module:95 run,94 pass and1 existing root-authorized native-window opt-in skipped,19.022s (test-phase7.log).
- Selected legacy consumers: Pool rehash end-binding, retained role diagnostics, canonical manifest redaction; all3 pass in5.613s (legacy-consumers.log).
- git diff --check passes.
- No full131-test benchmark-harness repeat, Rust suite, or timed benchmark campaign.

F4 concrete reduction: reduce_four_cases.py runs real reduce_report against exactly four original captured contracts from styled-release5 (normal SQL, normal PromQL, SQL resize, intentional PromQL SIGQUIT), encompassing the same8 actual reference/candidate captures. A NEW reduction/report.json contains all4 cases and empty reference/comparison issues. No capture-root rewriting or broad corpus reduction occurs. Prior source is reconstructed from the narrow inverse delta and verified byte-for-byte by its frozen SHA df769c7d8c2689d54bc26506f757857d80a1e490117ac3402e60c3b255c18a36. Prior and final history counts are identical: available8, unavailable0, error0. All24 retained session/stdout/stderr files have identical before/after hashes. Exact paths, issues and hashes are in reduction-validation.json; reduction.log is concise output. Earlier historical checks already exercised history inference, and their artifacts remain preserved.

F2/C1 mode accounting remains a scoped observer limitation for parent documentation, along with previously declared mode7/25/keypad/checkpoint limitations. No parser expansion is claimed. The final narrow review agent promql_review and parent have been sent the freeze identities and validation outcome.

Final source SHA256:
- harness/phase7.py: 3bb5852db477329d3ce6efba833e0150d76d9dcf9f9a9e00f4c8ffb87036eeb6
- harness/test_phase7.py: 9149be93ea48778a796ff1ce7bf4bc96f3addecc9fd9dd74383a142a8c6000c8

Evidence SHA256:
- reduction/report.json: 68b7ea77ded246e2b7e889037f1c49b4e4a0b823ab55ef9a73f889b4561f5a7a
- reduction-validation.json: 7959697cdac08ec00f1c46d0b8d0f016de5b8e75c8a5bcb6046fb9d6bc34ee81

No further source edits planned. Only the two owned harness source/test files were edited; parent owns docs, publication and commits.
