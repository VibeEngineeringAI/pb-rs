"""Evaluate an UNPROMOTED one-case rule against retained captures, without CLI runs."""
import argparse
import copy
import difflib
import hashlib
import json
from pathlib import Path
import sys
import types

parser = argparse.ArgumentParser()
parser.add_argument('--capture-root', type=Path, action='append', required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / 'harness'))
import phase3

source_path = root / 'harness/phase3.py'
original = source_path.read_text()
line = '        "p3-deadline-not-reset-per-chunk": b"failed to stream response body",\n'
addition = '        "p3-deadline-body-stall-text": b"failed to stream response body",\n'
assert original.count(line) == 1 and addition not in original
proposed = original.replace(line, line + addition)
module = types.ModuleType('phase3_body_deadline_proposed')
module.__file__ = str(source_path)
sys.modules[module.__name__] = module
exec(compile(proposed, str(source_path) + ':proposed', 'exec'), module.__dict__)
case = next(c for c in module.corpus() if c['name'] == 'p3-deadline-body-stall-text')
assert module.corpus() == phase3.corpus()
bare = b'Error: failed to stream response body: context deadline exceeded\n'
expected = b'Error: failed to stream response body: <CLIENT-TIMEOUT> (Client.Timeout or context cancellation while reading body)\n'
assert module.normalize_timeout(case, bare) == expected
negative_checks = 0
for changed in (bare[:-1], bare + b'extra\n', b'extra\n' + bare,
                bare.replace(b'context deadline exceeded', b'net/http: request canceled'),
                bare.replace(b'stream', b'read')):
    assert module.normalize_timeout(case, changed) == phase3.normalize_timeout(case, changed)
    negative_checks += 1
for name in ('p3-deadline-body-stall-json', 'p3-deadline-body-stall-text-extra', 'unrelated'):
    altered = copy.deepcopy(case)
    altered['name'] = name
    assert module.normalize_timeout(altered, bare) == phase3.normalize_timeout(altered, bare)
    negative_checks += 1
for options in ({}, {'timeout_cause': False}):
    altered = copy.deepcopy(case)
    altered['compare'] = options
    assert module.normalize_timeout(altered, bare) == bare
    negative_checks += 1

sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
source_hash = sha(source_path)
captures, hashes, reports = [], {}, {}
first = None
for campaign in args.capture_root:
    report = campaign / 'report.json'
    reports[str(report)] = sha(report)
    directory = campaign / 'cases' / case['name']
    for role in ('reference', 'reference-repeat-1', 'candidate'):
        folder = directory / role
        if not folder.exists():
            continue
        result = module.load_stored(folder, case)
        first = result if first is None else first
        assert not module.expectation_issues(case, result)
        assert not module.differences(case, first, result)
        rejection_controls = []
        for field, value in (('termination', {'exit': 0}), ('trace', []),
                             ('stdout_normalized', b''),
                             ('wall_ms', 58000), ('wall_ms', 71000)):
            invalid = copy.deepcopy(result)
            invalid[field] = value
            issues = module.expectation_issues(case, invalid)
            assert issues, (role, field, value)
            rejection_controls.append({'field': field,
                                       'value': value.hex() if isinstance(value, bytes) else value,
                                       'issues': issues})
        captures.append({'campaign': str(campaign), 'role': role,
                         'original_issues': phase3.expectation_issues(case, result),
                         'proposed_issues': [], 'proposed_differences': [],
                         'wall_ms': result['wall_ms'],
                         'raw_stderr': (folder / 'stderr.bin').read_text(),
                         'rejection_controls': rejection_controls})
        for path in folder.iterdir():
            if path.is_file():
                hashes[str(path)] = sha(path)
assert source_hash == sha(source_path)
assert all(sha(Path(p)) == h for p, h in {**hashes, **reports}.items())
args.out.mkdir(parents=True, exist_ok=False)
(args.out / 'proposed-source.patch').write_text(''.join(difflib.unified_diff(
    original.splitlines(True), proposed.splitlines(True),
    fromfile='a/harness/phase3.py', tofile='b/harness/phase3.py')))
record = {'status': 'process-free proposed reduction only; not approved/applied; no CLI execution',
          'source_sha256_unchanged': source_hash, 'corpus_unchanged': True,
          'exact_rule_negative_checks': negative_checks, 'captures': captures,
          'original_reports': reports, 'artifact_hashes': hashes,
          'patch_sha256': sha(args.out / 'proposed-source.patch'),
          'runner_sha256': sha(Path(__file__))}
(args.out / 'report.json').write_text(json.dumps(record, indent=2) + '\n')
print(len(captures), 'proposed captures pass;', negative_checks, 'negative rule checks pass')
