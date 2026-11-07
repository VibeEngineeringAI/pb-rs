#!/usr/bin/env bash
# Run from the repository root, after all owned builds/tests/profiles stop:
# flock -n ~/.cache/pb-rs-performance.lock taskset -c 2,4,6,8 bash docs/evidence/cli-perf2/measure.sh
# Requires the frozen binaries identified in the evidence and prebuilt fixture/meter.
set -euo pipefail
result_root=${1:-harness/results/perf2}
mkdir -p "$result_root"
python3 docs/evidence/cli-perf2/supplement.py "$result_root/final-supplement-suite.json"
python3 - "$result_root" <<'PY'
import json
from pathlib import Path
import sys
sys.path.insert(0, 'harness')
import cases
import phase3_workloads
root = Path(sys.argv[1])
plan = json.loads(Path('docs/evidence/cli-perf2/plan.json').read_text())
by_name = {c['name']: c for c in cases.suite() + phase3_workloads.suite()}
suite = [by_name[name] for name in plan['fixed_cases']]
suite += json.loads((root / 'final-supplement-suite.json').read_text())
(root / 'final-suite.json').write_text(json.dumps(suite, indent=2) + '\n')
PY
for seed in 2031 2032; do
  python3 harness/run.py run --suite "$result_root/final-suite.json" \
    --binary base=bin/pb-perf-final --binary original=bin/pb-perf-baseline \
    --binary final=bin/pb-perf2-final --fixture bin/pb-fixture --meter bin/pb-meter \
    --warmup 3 --repetitions 20 --seed "$seed" --timeout 60 \
    --out "$result_root/final-seed$seed"
done
python3 harness/ingest_perf.py run --baseline bin/pb-perf-final --candidate bin/pb-perf2-final \
  --records 20000 --record-bytes 500 --batch-size 1000 --repetitions 9 --warmup 2 \
  --seed 2031 --probe-fail-upload 1 --out "$result_root/ingest-final"
python3 docs/evidence/cli-perf2/escaped_ingest.py run \
  --baseline bin/pb-perf-final --candidate bin/pb-perf2-final \
  --records 512 --record-bytes 8192 --batch-size 100 --repetitions 7 --warmup 1 \
  --seed 2032 --probe-records 100 --probe-fail-upload 1 --out "$result_root/ingest-escaped-final"
