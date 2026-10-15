# Release5 A: stale cancel checkpoint established from raw output

The original release5 A discrepancy was a stale pre-Left Go checkpoint. It was
not a cursor-movement mismatch in the observed processes. Raw replay proves
this independently of the R5 source review and its unverified renderer-flush
recollection.

Original Go Left dispatched at `1006807813673589`; cancel-ready evaluated at
`1006807824420990`, only **10.747401 ms** later, with `bytes_since_action=0`.
The action's earlier evaluation was `1006807785794907`, so the legacy 35 ms
minimum measured from evaluation had already passed. At the checkpoint, row 1
column 19 held a plain bullet and column 20 a reversed trailing blank.

The raw chunk at `1006807849928048` (bytes 733–894) redraws column 19 as the
reversed final bullet and column 20 as the plain blank. It arrived **25.507058
ms after** the checkpoint, **36.254459 ms after** Left, and before Ctrl+C at
`1006807871077015`. This complete moved-cursor frame equals the original
release5 moved-cursor frame exactly, including all cell attributes, terminal
cursor, colors and scrollback. Both original final frames also equal exactly.
The original raw replay matches the retained `raw-frames.json` in both roles.

Only the owned [credential runner](credential-blink-process.py) was changed.
Its Left action now records the output offset; cancel-ready requires new output
and a reversed fourteenth bullet followed by a plain trailing blank. The old
elapsed minimum is removed at this checkpoint. A separate causal check requires
the actual Left dispatch to precede the checkpoint and last output, positive
new-output bytes, the moved caret, and an exact complete checkpoint-frame witness
in a raw chunk observed after dispatch and before checkpoint readiness. Failed
guards produce `unknown`. Readiness inspects cells; it does not alter them.
Full strict comparisons remain intact without normalization.

The pre-edit runner is preserved byte-for-byte in
[credential-blink-process-r2.py](credential-blink-process-r2.py). Its hash binds
the original runner identity. All 105 inventoried original files were checked:
only the explicitly authorized live runner path changed. Existing reports,
captures, hashes, comparators, helper, workloads and production source were not
modified by this lane.

Eight [pure artifact/mutation tests](credential-blink-left-tests.py) passed
without CLI work. They prove the original stale checkpoint and reject stale
caret placement, zero new output, output or checkpoint before dispatch, raw
redraw after checkpoint, and unwitnessed changed styles. A mocked readiness
test separately requires both the moved caret and new output. These tests
preserve and compare complete styled cell values.

Exactly two authorized new A CLI sessions completed: pinned Go and immutable
release5 normal. No retries were run. Go cancel-ready was 19.201157 ms after
Left; release5 was 0.424924 ms after Left. Each recorded 161 new bytes and
passed all five post-dispatch redraw guards. Both cancel-ready frames have the
same SHA256:
`52b0347d5da2fb9ec9c88fec7151d670e500884fc11c9f9bc365d933a2131da0`.

**The complete strict differential is clean:** all nine checkpoint frames and
the final frame match; case/actions, input effects, ordered requests, config
snapshots, raw saved-config bytes, termination, termios and restoration, stderr,
post-screen output and captured lifecycle/cleanup fields agree. Both processes
exit 0 and retain A's cancellation persistence of empty username/password.
The original intended blink checkpoints remain clean. Sessions took 1.368 s
and 1.325 s, each below the bounded timeout; these durations carry no performance
claim.

Raw evidence is under
`/home/ajs/.cache/pb-phase7/credential-blink-controls/release5-left-readiness/`:
`retained-before.json`, `original-raw-proof.json`, `pure-tests.json`, and the
`go-A` / `release5-A` capture directories. Each capture retains `session.json`,
all raw stdout-chunk frames, PTY stdout/stderr, saved config, report and seal.
[credential-blink-left-evidence.json](credential-blink-left-evidence.json)
records exact source/binary/helper/manifest before/after identities, raw/report
hashes, original causal proof, full strict outcome and limits.
[credential-blink-left-sealed.json](credential-blink-left-sealed.json) binds
the evidence and this report.

To run only the pure tests:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/ajs/.cache/pb-phase7/harness-venv/bin/python \
  docs/evidence/phase7/credential-blink-left-tests.py
```

The actual pair used the parameterized runner with `--cases A`, explicit
`--retry-cause`, fresh output directories, `--role go` / `--role candidate`,
and `--compare-go` on the candidate. Go SHA256 was
`fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`;
release5 normal SHA256 was
`9ad4f4fc9e732b4a790c0d2ea97dcc58f127ee7580d3df9bc26c8cee28edef21`.
Helper preparation used `build=False`; execution used
`spawn_helper_build=False`. There were no Rust/compiler/candidate builds,
normalization, profiler, timing campaign or common performance lock.

**SOURCE FREEZE COMPLETE.** The collector owner explicitly acknowledged its
harness pause before capture and received RELEASE after both captures. Broad
source maps, including all harness source files and native login/editor/profile,
were unchanged before/after each invocation and identical between roles.
Production/fullUI work held for this lane may resume. Collector work after
RELEASE is outside the capture freeze. This conclusion is limited to the actual
two-session A pair and carries no fullUI, R6-obligation or final acceptance claim.
