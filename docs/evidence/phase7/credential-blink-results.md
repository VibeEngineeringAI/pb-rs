# Credential pending-blink controls before production edits

Actual pinned Go `07af390` establishes both proposed credential collisions. In A
(empty username, Tab, one password chunk) and B (one username chunk, Tab, two
separately dispatched password chunks), Go turns the password caret plain after
the departed username deadline and before the password's own 530 ms deadline.
Immutable release4 keeps it reversed. C (one username chunk and one password
chunk) matches exactly at every checkpoint. These are functional observations,
with no performance or acceptance claim.

The selected B repeat sampled Go plain at 260.927 ms and 410.044 ms after its
last password dispatch; release4 remained reversed at its corresponding
280.168 ms and 432.721 ms checkpoints. Both are before the earliest possible
own deadline. The B Go old-deadline checkpoint is 46.567 ms after the latest
dispatch-to-render old deadline bound; its before-own checkpoint is 119.956 ms
before the earliest own deadline. The initial, user-rendered, password-focused,
first-chunk, full-password and before-old frames match exactly.

For A and selected B, each differential checkpoint changes exactly one cell:
zero-based row 1, column 20, the blank after fourteen password bullets. Go's
plain cell has default foreground and no reverse; release4's visible caret has
foreground `9e9ef0` and reverse. At the later collision-rearm checkpoint Go is
reversed and release4 plain. Explicit additional deadline bounds establish that
this checkpoint follows Go's rearm and precedes release4's second own deadline.
All cell attributes, terminal cursor, colors and scrollback remain in the raw
captures and strict comparisons; nothing was normalized by this lane.

Go and release4 agree on final frames, input effects, ordered requests (none),
config snapshots, raw saved-config bytes, exit 0, terminal restoration, stderr,
post-screen output, and captured lifecycle/cleanup fields. A retains the
partial-cancel quirk: the new profile persists with empty username/password.
B/C retain valid-cancel persistence: `dummy-user` / `dummy-password` are saved
on Ctrl+C. A final cursor-moving Left resets the caret before cancellation
without changing the values; its separate checkpoint is retained.

Eight actual CLI sessions were used: A/B/C for both roles, then one paired B
retry. All completed in 1.313–1.389 seconds, below the 15 second bound. No more
live sessions are authorized by this task's budget. Each capture used the
already prepared native helper with `build=False`; execution used
`spawn_helper_build=False`. Temporary environment directories were rooted at
`/home/ajs/.cache/pb-phase7/tmp`. There were no builds, profiler, performance
locks, normalization changes, corpus changes, comparator changes or helper edits.

The first B Go `password-focused` checkpoint falsified the hypothesis that
matching placeholder text plus a 25 ms minimum wait proves focus rendering.
It had zero output since Tab and still displayed the username caret, producing
24 unrelated styled-cell differences. That capture/report remains intact in
`go-r1` / `release4-r1`. The current runner additionally requires the reversed
password placeholder cell before proceeding. The paired `go-r2` / `release4-r2`
B retry removes that intermediate difference and reproduces the collision.
The original runner is archived as `credential-blink-process-r1.py`, whose
hash matches the original reports. No failed hypothesis was normalized away.

Chunk separation is demonstrated by dispatch timestamps and an intervening
six-bullet frame with new output. In selected B Go, the first password dispatch
precedes that frame by 30.543 ms, and the second dispatch follows it by
39.352 ms. Source tag arithmetic predicts the collision, but no cursor tags
were instrumented. Timer bounds use dispatch-to-render processing intervals;
A bounds initialization between session launch and the first rendered form.
All before-old, after-old and before-own timing guards pass. Arbitrary key
segmentation, other themes, D/E and a future corrected implementation remain
outside these captures.

The durable runner is [credential-blink-process.py](credential-blink-process.py).
Run it with the harness Python environment and `PYTHONDONTWRITEBYTECODE=1`:

```sh
/home/ajs/.cache/pb-phase7/harness-venv/bin/python docs/evidence/phase7/credential-blink-process.py \
  --binary /home/ajs/.cache/pb-phase7/baselines/pb-go --role go \
  --expect-sha256 fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314 \
  --out /home/ajs/.cache/pb-phase7/credential-blink-controls/NEW-go-output
```

Use `--role candidate`, the release4 binary/hash below, and `--compare-go` to
the paired Go output for strict comparisons. Default cases are A/B/C. Subset
retries require `--cases B --retry-cause TEXT`, and the cause is retained in the
report. Output directories must be new. The runner imports only repository
harness/workload modules, verifies the pinned credential source and candidate
manifest binding, freezes source identities, retains before/after
binary/helper/manifest identities and hashes every raw capture file. Reports
are bound by a separate `sealed.json`.

Existing outputs are rooted at
`/home/ajs/.cache/pb-phase7/credential-blink-controls/{go-r1,release4-r1,go-r2,release4-r2}`.
Each session retains raw PTY stdout/stderr, complete `session.json` checkpoints,
every captured stdout-chunk frame in `raw-frames.json`, and `config-after.toml`.
The consolidated [evidence JSON](credential-blink-evidence.json) retains exact
differences, timing guards, second-toggle proofs and report/capture hashes;
[credential-blink-sealed.json](credential-blink-sealed.json) binds it.

[credential-blink-verify.py](credential-blink-verify.py) performs only offline
checks of report seals, file hashes, all raw-frame/checkpoint identities,
lifecycle, expected strict differences and analysis failure behavior. Eleven
checks passed, including rejecting samples after the own deadline or without
intermediate chunk output as `unknown`. See
[credential-blink-verification.json](credential-blink-verification.json).

**SOURCE FREEZE COMPLETE.** Native login/editor/profile were unchanged throughout
all eight sessions, with identical hashes across all four invocations:

* `src/tui/login.rs`: `7898c967896f912a5fdeb353a9448c3e1fb1047d541470c4b6001ae01953c301`
* `src/tui/editor.rs`: `454da2706b49598ea73756900ce8fd566f9a3030f8966bdddc3fd8fab7244117`
* `src/tui/profile.rs`: `a822ba963ba4ab4bc19c0dd403a35d90d44ab129fe6a994ea50e6d3f3b273730`

The parent may now open the editor/login/profile implementation window. This
test lane made no production edit. Frozen binary identities:

* Go `/home/ajs/.cache/pb-phase7/baselines/pb-go`:
  `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`
* release4 `/home/ajs/.cache/pb-phase7/candidates/release4/pb-normal`:
  `fd1f91b608bbf76832fab183f02d9c63e658f7bc12ca4aabf57a811559a871c1`
* prepared spawn helper:
  `3b684c7b7632302725727fcc586a071c7818f43d704a8cbf4e1afc384e99f03e`
