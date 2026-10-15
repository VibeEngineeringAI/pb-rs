#!/usr/bin/env bash
# Parent only: run after all three findings are fixed and immutable successor
# normal/cloud binaries are built and bound to the parent's final source map.
# The SQL implementation owner did not execute these commands.
set -euo pipefail

schema_clock_normal=${1:?Usage: bash schema-fallback-clock-central-commands.sh NORMAL_BINARY NORMAL_SHA256 CLOUD_BINARY}
schema_clock_normal_sha=${2:?Provide the immutable successor normal SHA256}
schema_clock_cloud=${3:?Provide the immutable synthetic cloud successor binary}

cd /home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive
export TMPDIR=/home/ajs/.cache/pb-phase7/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
export PB_EFFECTS_PYTHON=/home/ajs/.cache/pb-phase7/harness-venv/bin/python
export PB_GO_ORACLE=/home/ajs/.cache/pb-phase7/baselines/pb-go
export PB_GO_ORACLE_CLOUD=/home/ajs/.cache/pb-phase7/baselines/pb-go-cloud
export PB_EFFECTS_CANDIDATE="$schema_clock_normal"
export PB_EFFECTS_CLOUD_CANDIDATE="$schema_clock_cloud"
export PB_EFFECTS_CAPTURE_DIR=/home/ajs/.cache/pb-phase7/schema-fallback-clock/central-effects

# Preserve independent actual-process successor capture for both new cases.
# The normal Rust integration runner below also invokes the pinned Go oracle.
timeout 45s "$PB_EFFECTS_PYTHON" docs/evidence/phase7/schema-fallback-clock-process.py \
  --binary "$schema_clock_normal" --role candidate \
  --expect-sha256 "$schema_clock_normal_sha" \
  --out /home/ajs/.cache/pb-phase7/schema-fallback-clock/successor

# Focused SQL gates; ordinary cases are also included in central --all-targets.
cargo test --test interactive_effects sql_schema_ -- --test-threads=1
cargo test --test interactive_effects sql_unicode_schema_raw_bytes -- --exact --test-threads=1
cargo test --test interactive_effects sql_stale_schema -- --exact --test-threads=1
# Existing real 15-second policy and shared schema/fallback OAuth-scope gates.
cargo test --test interactive_effects deadline_schema_15s -- --ignored --exact --test-threads=1
cargo test --test interactive_effects oauth_schema_fallback_scope -- --ignored --exact --test-threads=1

# Parent additionally owns its normal whole-source format/Clippy/all-target checks,
# all three fixes' independent review, final source/artifact binding and replay.
