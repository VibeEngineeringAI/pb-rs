# Release5 A: corrected successor evidence (v2)

**E6-1 wording correction:** the previous results and JSON limits incorrectly
claimed raw saved-config bytes agree. That claim was wrong. The original JSON
`raw_saved_config_bytes_equal=false` is correct: Go saved **91 bytes** and Rust
saved **80 bytes**. Direct `tomllib.loads` values agree exactly, as do the
existing semantic config snapshots. The original evidence, results, seal, raw
captures and root's independent verification remain untouched and hash-bound
in this successor. No new CLI session, build, normalization, source edit or
whole-phase approval accompanies this documentary correction.

The exact original config diff is:

```diff
--- Go config-after.toml
+++ release5 config-after.toml
@@ -1,6 +1,5 @@
-DefaultProfile = 'new'
+DefaultProfile = "new"
 
-[Profiles]
 [Profiles.new]
-url = 'http://127.0.0.1:1'
+url = "http://127.0.0.1:1"
 cloud = false
```

The difference consists only of quote spelling and the explicit empty parent
`[Profiles]` table. Both parsed values are
`{"DefaultProfile":"new","Profiles":{"new":{"url":"http://127.0.0.1:1","cloud":false}}}`.
There is no ephemeral home or path substitution in either raw file. SHA256s are
Go `b444cfb77d9c1582f7e5f7df61ae91ec8b75520b8496d60ba7c7e0df1011401a`
and Rust `ecb69f156c58958110be2d66e10428b6f6f6e5fcab59578340037ba073a4d2ce`.
The retained `go-r1/A` and immutable `release4-r1/A` controls have those same
respective bytes/hashes and equal parsed TOML. This establishes the serialization
difference predates release5 for A; it makes no broader earlier-phase approval
claim. Their report, seal, session and artifact identities are retained in v2.

The unchanged full strict differential uses the existing semantic config
contract, not raw TOML byte equality. In that contract `config_state` parses TOML
and `config_snapshot` removes its raw digest when parsing succeeds; filesystem
metadata remains compared. No comparator, raw byte, styled cell or frame was
altered. The stale Left-checkpoint disposition and complete cell/frame equality
remain supported by the original captured evidence.

The sections below describe the original two-session test lane. This successor
adds only the E6-1 documentary correction and fresh read-only seal verification.


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

**The strict differential under the existing semantic config contract is clean:** all nine checkpoint frames and
the final frame match; case/actions, input effects, ordered requests, semantic
config snapshots, termination, termios and restoration, stderr,
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
[credential-blink-left-evidence-v2.json](credential-blink-left-evidence-v2.json)
records exact source/binary/helper/manifest before/after identities, raw/report
hashes, original causal proof, full strict outcome and limits.
[credential-blink-left-sealed-v2.json](credential-blink-left-sealed-v2.json) binds
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

The new read-only verifier is
[credential-blink-left-verify-v2.py](credential-blink-left-verify-v2.py). It checks
successor/original seals, parent verification, retained capture hashes, exact
config bytes/diff, parsed equality, release4 provenance and unchanged strict
frames/config outcomes without writing any artifact:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/ajs/.cache/pb-phase7/harness-venv/bin/python \
  docs/evidence/phase7/credential-blink-left-verify-v2.py
```

**DOCUMENTARY SOURCE FREEZE COMPLETE:** no production, shared harness or owned
runner changed for this correction. This is evidence upkeep only; no new review,
fullUI acceptance or whole-phase approval is claimed.
