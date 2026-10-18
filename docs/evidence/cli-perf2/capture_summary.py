"""Retain raw resource samples and stricter same-CLI byte checks compactly."""
import hashlib
import json
from pathlib import Path
import sys

source, destination = map(Path, sys.argv[1:])
report = json.loads(source.read_text())
assert report['complete'] and report['passed']
fields = ('round', 'order_in_round', 'valid', 'cpu_ms', 'cpu_user_ms', 'cpu_system_ms',
          'wall_ms', 'peak_rss_bytes', 'minor_faults', 'voluntary_context_switches',
          'involuntary_context_switches', 'proc_io_syscr', 'proc_io_syscw',
          'stdout_bytes', 'stderr_bytes', 'contract_sha256')
out = {k: report[k] for k in ('binaries', 'method', 'host', 'fixture', 'meter')}
out['source_report_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
out['cases'] = {}
for name, case in report['cases'].items():
    entry = {'spec_sha256': case['spec_sha256'], 'runs': {}, 'normalized_output_hashes': {}, 'wire_body_hashes': {}}
    for label, samples in case['runs'].items():
        entry['runs'][label] = [{k: s.get(k) for k in fields} for s in samples]
        output, bodies = set(), set()
        for sample in samples:
            assert sample['valid']
            contract = sample['contract']
            output.add(tuple(contract.get(f'{stream}_normalized_sha256', contract[f'{stream}_sha256']) for stream in ('stdout', 'stderr')))
            requests = json.loads((source.parent / sample['artifact'] / 'requests.json').read_text())
            wire = [{key: request.get(key) for key in ('protocol', 'method', 'path', 'body')} for request in requests]
            bodies.add(hashlib.sha256(json.dumps(wire, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
        entry['normalized_output_hashes'][label] = sorted(output)
        entry['wire_body_hashes'][label] = sorted(bodies)
    entry['same_unprojected_output'] = len({json.dumps(v) for v in entry['normalized_output_hashes'].values()}) == 1
    entry['same_wire_bodies'] = len({json.dumps(v) for v in entry['wire_body_hashes'].values()}) == 1
    out['cases'][name] = entry
destination.write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps({name: {k: c[k] for k in ('same_unprojected_output', 'same_wire_bodies')}
                  for name, c in out['cases'].items() if not c['same_unprojected_output'] or not c['same_wire_bodies']}, indent=2))
