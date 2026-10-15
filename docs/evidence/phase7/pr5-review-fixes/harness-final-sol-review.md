# Final narrow harness review

**APPROVED for the final delta; no findings.** Read-only review against the stable previously approved `harness-review/cur2/harness` baseline. No shared source edits, commits, external writes, new mode features, benchmarks or broad suite runs.

Reviewed final SHA256 identities, confirmed again after execution:

- `harness/phase7.py`: `3bb5852db477329d3ce6efba833e0150d76d9dcf9f9a9e00f4c8ffb87036eeb6`
- `harness/test_phase7.py`: `9149be93ea48778a796ff1ce7bf4bc96f3addecc9fd9dd74383a142a8c6000c8`

The complete production delta is confined to `session_terminal_modes`: encode the captured seed once, feed it in at most 65,536-byte slices, snapshot initial modes, feed raw stdout in the same bounded slices, then perform an empty final flush. Parser state and the incremental UTF-8 decoder are carried by the same terminal object across all feeds, including the seed/stdout boundary. Empty inputs still get the final flush. No evidence guards, capture serialization, `validate`/`compare`, history hooks, capture mutation policy or restoration rules changed. Exact diffs are retained in `phase7.py.diff` and `test_phase7.py.diff`.

I independently checked 25 equivalence/immutability cases against the prior implementation: every split position within a private-mode CSI; splits inside 2/3/4-byte UTF-8; OSC containing fake CSI with BEL and ST endings, including split ST; incomplete seed CSI completed by stdout; empty inputs; truncated final UTF-8; and RIS split across a block. All mode results match the prior implementation and expected values, and all input capture objects remain unchanged. An independent feed spy additionally confirms that both a large multibyte seed and raw stdout use calls no larger than 65,536 bytes, followed by `feed(b'', final=True)`. `boundary-results.json` retains the cases and feed sizes; this checks bounds, not timing.

Ran 11 focused tests with the explicit pinned harness-venv Python: the three new/resource-margin regressions, existing legacy load/reconstruction/missing-raw/OSC/contract-override guards, and three legacy consumers (`PoolTests`, `R4Tests`, `AdapterTests`). All 11 pass in 7.286 seconds. `review.log` records the exact names; `run_review.py` is reproducible. No full `test_phase7` or benchmark module was rerun.

The new tests are meaningful: the resource invariant fails the old unbounded reconstruction (author RED log: 464,910 pending bytes versus 65,552 allowed) while checking final mode state; home+LF at capacity invokes `index` away from the bottom without falsely declaring eviction, then bottom+LF requires eviction; DECSTBM scroll-region bottom is exercised independently from physical screen bottom. The author retained killed mutants for removing the bottom condition and ignoring margins. I read those traces and independently reran the final tests; I did not claim to rerun those mutants.

Reviewed r3's stated concerns in this scope: its bounded-parsing issue and missing margin controls are addressed by this delta. Documented C1/parser limitations and malformed legacy case types are unchanged. Historical corpus/reduction counts were not reopened or independently rerun here; the original fixes already had separate scoped approvals and retained compatibility evidence.

`verification.json` records baseline/final hashes and confirms final shared sources still match the freeze. All new review artifacts are local to this directory.
