#!/usr/bin/env bash
set -euo pipefail
export TMPDIR="$PWD/target/tmp"
export PATH="/home/ajs/.cargo/bin:$PATH"
python=/home/ajs/.cache/pb-phase7/harness-venv/bin/python
reference=/home/ajs/.cache/pb-phase7/baselines
candidate="$PWD/harness/results/pr7/frozen"
fixture=/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/bin
flight="$reference/merged-phase6"
effects=$(find "$PWD/target/debug/deps" -maxdepth 1 -type f -executable -name 'interactive_effects-*' | head -1)
"$python" harness/final_replay.py \
  --reference-dir "$reference" --candidate-dir "$candidate" \
  --fixture "$fixture/pb-fixture" --meter "$fixture/pb-meter" \
  --flight-fixture "$flight/flight-fixture-r4" --decoder-fixture "$flight/flight-fixture-r2" \
  --trailer-followup-reference "$flight/trailer-followup-reference.json" \
  --review3-followup-reference "$flight/review3-followup-reference.json" \
  --effects-test "$effects" --out /home/ajs/.cache/pb-phase6/tmp/pr7-final-replay --jobs 8
