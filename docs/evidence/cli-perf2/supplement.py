"""Generate supplemental fixed gzip SQL cases; run from repository root."""
import copy
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'harness'))
import cases
import phase3_workloads

all_cases = {case['name']: case for case in cases.suite() + phase3_workloads.suite()}
selected = []
for name in ('sql-10000-json', 'p3-single-8MiB-item'):
    case = copy.deepcopy(all_cases[name])
    case['name'] += '-gzip'
    for route in case['fixture']['routes']:
        route['gzip'] = True
    selected.append(case)
for name, argv in [('completion-root', ['__complete', '']), ('completion-sql-flags', ['__complete', 'sql', 'run', '--'])]:
    case = copy.deepcopy(all_cases['version-text'])
    case.update(name=name, argv=argv)
    selected.append(case)
case = copy.deepcopy(all_cases['sql-10000-json'])
case['name'] = 'sql-10000-nested-json'
case['fixture']['routes'][0]['body'] = json.dumps([{'sequence': i, 'a': i, 'b': 'ordinary text', 'c': None, 'd': 1.25, 'last': {'nested': [i, {'message': 'nested text'}]}} for i in range(10000)], separators=(',', ':'))
selected.append(case)
for batch_rows in (8191, 8192):
    case = copy.deepcopy(all_cases['tail-4096'])
    case.update(name=f'tail-131072-batch{batch_rows}', rows=131072, jsonl_rows=131072, max_output_bytes=64 << 20)
    case['fixture']['flight'].update(rows=131072, batch_rows=batch_rows)
    selected.append(case)
Path(sys.argv[1]).write_text(json.dumps(selected, indent=2) + '\n')
