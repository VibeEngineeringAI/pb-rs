"""Extract attribution only, never resource rankings, from verified profiles."""
import hashlib
import json
from pathlib import Path
import re
import sys

source, destination = map(Path, sys.argv[1:])
report = json.loads(source.read_text())
assert report['valid'] and report['complete'] and not report['errors']
out = {'valid': True, 'source_report_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
       'binaries': report['binaries'], 'scope': report['scope'], 'samples': []}
for sample in report['samples']:
    assert sample['result']['valid']
    if sample['mode'] == 'control':
        continue
    identity = sample['analysis']['output']
    raw = Path(identity['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == identity['sha256']
    body = raw.decode()
    item = {key: sample[key] for key in ('case', 'mode', 'language', 'repetition')}
    item['analysis_sha256'] = identity['sha256']
    if sample['mode'] == 'heaptrack':
        prefixes = ('calls to allocation', 'temporary memory', 'peak heap', 'peak RSS', 'total memory leaked')
        item['totals'] = [line for line in body.splitlines() if line.startswith(prefixes)]
    elif sample['mode'] == 'perf':
        item['samples'] = re.findall(r'^# Samples: (.+)$', body, re.M)
        item['lost_samples'] = re.findall(r'^# Total Lost Samples: (.+)$', body, re.M)
        item['self_percent'] = [{'percent': float(percent), 'symbol': symbol}
                                for percent, symbol in re.findall(r'^\s*([\d.]+)%\s+.*?\[[.k]\]\s+(.+)$', body, re.M)][:30]
    out['samples'].append(item)
destination.write_text(json.dumps(out, indent=2) + '\n')
print(destination)
