# Focused prebuilt candidate styled controls

Run from `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive` after the parent
receives the native-helper freeze announcement. These commands do not build,
profile, benchmark, install dependencies, or invoke the execute/native helper.
They use the embedded independent PTY/HTTP fixture and extract only pure phase7
Terminal declarations, frozen for the duration of each invocation.

```bash
export TMPDIR=/home/ajs/.cache/pb-phase7/tmp GOTMPDIR=/home/ajs/.cache/pb-phase7/tmp CLAUDE_CODE_TMPDIR=/home/ajs/.cache/pb-phase7/tmp

/home/ajs/.cache/pb-phase7/harness-venv/bin/python docs/evidence/phase7/styled_effect_controls.py --case sidebar --oracle /home/ajs/.cache/pb-phase7/baselines/pb-go --candidate /home/ajs/.cache/pb-phase7/candidates/release2/pb-normal --out /home/ajs/.cache/pb-phase7/styled-release2

/home/ajs/.cache/pb-phase7/harness-venv/bin/python docs/evidence/phase7/styled_effect_controls.py --case builder --oracle /home/ajs/.cache/pb-phase7/baselines/pb-go --candidate /home/ajs/.cache/pb-phase7/candidates/release2/pb-normal --out /home/ajs/.cache/pb-phase7/styled-release2

/home/ajs/.cache/pb-phase7/harness-venv/bin/python docs/evidence/phase7/styled_effect_controls.py --case saved --oracle /home/ajs/.cache/pb-phase7/baselines/pb-go --candidate /home/ajs/.cache/pb-phase7/candidates/release2/pb-normal --out /home/ajs/.cache/pb-phase7/styled-release2
```

The runner is durable in this worktree. It needs no private-cache Python script;
the cache venv supplies only pinned `pyte==0.8.2` and `wcwidth==0.2.13`. Omit
`--candidate` for Go-only controls; all three passed that mode in
`/home/ajs/.cache/pb-phase7/styled-durable-go/`.

Each invocation retains both binaries' stdout/stderr, full styled checkpoint
frames, actual read boundaries, requests, SHA256/provenance and failed assertions
under `--out`. Unique record names preserve prior attempts. Candidate executable
hash must remain unchanged across its process control. A comparison JSON reports
all differing styled cells and both cursor states for actual reads and separately
for one-chunk replay. Only digits in the exact ephemeral fixture URL normalize;
cell styles and layout remain exact.

Sidebar values require the exact Go formatted bytes `B5 C2 8B E8 AF 95` and reject
two-replacement UTF8. The recorded-read/pyte C1 limitation remains explicit: an
actual-read-only difference with matching one-chunk frames needs scoped review,
not a blanket normalization or automatic production-bug conclusion. Builder and
saved controls additionally assert the exact Go clip bytes, complete visible
row, active/bold value-cell styles, hidden VT cursor and one-chunk/read equivalence.
All controls check request graph, terminal restoration and unchanged config.

No release2 styled result is claimed by this command handoff. The parent owns
final candidate acceptance and the central build/test sequence.
