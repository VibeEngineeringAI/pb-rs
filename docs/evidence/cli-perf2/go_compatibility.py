#!/usr/bin/env python3
"""Plan/run retained Go gates without changing their comparators or manifests.

Preparation only is the default. Pass --run only after the final candidate and
its harness-token cloud companion are built and measurements are idle.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'harness'))
CACHE = Path('/home/ajs/.cache/pb-phase7/baselines')
PYTHON = Path('/home/ajs/.cache/pb-phase7/harness-venv/bin/python')


def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--cloud-candidate', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--gate', action='append', help='explicit subset, recorded as incomplete coverage')
    args = parser.parse_args()
    candidate, cloud, out = (p.resolve() for p in (args.candidate, args.cloud_candidate, args.out))
    go, go_cloud = CACHE / 'pb-go', CACHE / 'pb-go-cloud'
    fixture = ROOT / 'bin/pb-fixture'
    meter = ROOT / 'bin/pb-meter'
    historical = CACHE / 'merged-phase6'
    flight = historical / 'flight-fixture-r4'
    decoder = historical / 'flight-fixture-r2'
    flight_out = Path('/home/ajs/.cache/pb-phase6/tmp') / ('perf2-' + out.name)
    common = ['--reference', str(go), '--candidate', str(candidate)]
    cloud_args = ['--cloud-reference', str(go_cloud), '--cloud-candidate', str(cloud)]
    gates, coverage = [], {}
    for phase in range(1, 6):
        module = importlib.import_module('phase' + str(phase))
        cases = module.corpus()
        ordinary = [c for c in cases if c.get('tier', 'gate') == 'gate'
                    and c.get('scope', 'phase2') == 'phase2']
        command = [str(PYTHON), f'harness/phase{phase}.py', 'compare', *common]
        if phase == 1:
            command += ['--parity-only']
        else:
            command += cloud_args
        if phase == 2:
            command += ['--fixture', str(fixture)]
        if phase == 3:
            command += ['--meter', str(meter)]
        gates.append((f'phase{phase}', command, out / f'phase{phase}'))
        coverage[f'phase{phase}'] = {'ordinary_cases': len(ordinary), 'total_defined_cases': len(cases)}
        if phase >= 3:
            deadline_option = '--include-full120s' if phase == 4 else '--include-full60s'
            for case in cases:
                if case['tier'].startswith('full'):
                    gates.append((case['name'], [*command, deadline_option, '--case', case['name']], out / case['name']))
    p6 = importlib.import_module('phase6')
    for suite, factory in [('main', p6.corpus), ('trailer-followup', p6.trailer_followups),
                           ('review3-followup', p6.review3_followups)]:
        command = [str(PYTHON), 'harness/phase6.py', 'compare', '--go', str(go), '--go-cloud', str(go_cloud),
                   '--candidate', str(candidate), '--candidate-cloud', str(cloud), '--fixture', str(flight), '--suite', suite]
        if suite != 'main':
            command += ['--reference', str(historical / (suite + '-reference.json'))]
        gates.append(('flight-' + suite, command, flight_out / suite))
        coverage['flight-' + suite] = {'defined_cases': len(factory()), 'existing_declared_user_agent_divergence': True}
    # The decoder replay binds binary digests itself. Planning supports a candidate not yet built.
    candidate_sha = sha(candidate) if candidate.is_file() else '<final-candidate-sha256>'
    gates.append(('flight-decoder14', [str(PYTHON), 'docs/evidence/phase7/merged-flight-pr4-replay.py',
        '--candidate', str(candidate), '--candidate-sha256', candidate_sha,
        '--go', str(go), '--go-sha256', sha(go), '--fixture', str(decoder), '--fixture-sha256', sha(decoder)], out / 'flight-decoder14'))
    workloads = importlib.import_module('phase7_workloads')
    terminal = [c for c in workloads.corpus(themes=('dark', 'light')) if 'sql' in c['name'] or 'saved' in c['name']]
    gates.append(('interactive-sql-saved', [str(PYTHON), 'harness/phase7.py', 'compare', *common, *cloud_args,
        '--theme', 'both', '--case', 'sql', '--case', 'saved'], out / 'interactive-sql-saved'))
    coverage['interactive-sql-saved'] = {'cases': [c['name'] for c in terminal],
        'documentary': [c['name'] for c in terminal if c['documentary']]}
    gates.append(('root-help', [str(PYTHON), 'harness/root_help.py', *common], out / 'root-help'))
    known = {name for name, _, _ in gates}
    if args.gate and set(args.gate) - known:
        parser.error('unknown gates: ' + ', '.join(sorted(set(args.gate) - known)))
    selected = [(n, c, p) for n, c, p in gates if not args.gate or n in args.gate]
    plan = {'kind': 'Go compatibility, not performance', 'coverage': coverage,
            'all_gates': [n for n, _, _ in gates], 'selected_gates': [n for n, _, _ in selected],
            'commands': {n: [*c, '--out', str(p)] for n, c, p in selected},
            'gaps': ['Historical phase1 deferred-stub guards are excluded; parser/help/completion parity runs.',
                     'Phase2 future-terminal cases are outside its ordinary gate; selected SQL/saved PTY cases run separately.',
                     'Non-SQL/saved interactive workflows, device-login and interactive-effects are outside this replay.',
                     'Phase6 native tonic User-Agent differences retain their existing explicit divergence ledger; they are not strict passes.',
                     'Rust ingest/observe additions use only existing discovery/help/catalog projections.']}
    if not args.run:
        print(json.dumps(plan, indent=2))
        return 0
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    inputs = [candidate, cloud, go, go_cloud, fixture, meter, flight, decoder,
              *sorted((ROOT / 'harness').glob('*.py')), *sorted((ROOT / 'harness/contracts').glob('*'))]
    identities = {str(p): sha(p) for p in inputs if p.is_file()}
    if not candidate.is_file() or not cloud.is_file():
        parser.error('both final candidate binaries must exist before --run')
    out.mkdir(parents=True, exist_ok=False)
    if any(n.startswith('flight-') and n != 'flight-decoder14' for n, _, _ in selected):
        flight_out.mkdir(parents=True, exist_ok=False)
        (out / 'flight-artifacts').symlink_to(flight_out, target_is_directory=True)
    report = {**plan, 'input_sha256': identities, 'checks': [], 'passed': False}
    def save():
        (out / 'replay.json').write_text(json.dumps(report, indent=2) + '\n')
    def run_gate(name, command, target):
        start = time.monotonic()
        with (out / (name + '.log')).open('x') as log:
            code = subprocess.run([*command, '--out', str(target)], cwd=ROOT,
                stdout=log, stderr=subprocess.STDOUT, check=False).returncode
        return {'gate': name, 'exit': code, 'seconds': time.monotonic() - start, 'artifacts': str(target)}
    save()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {pool.submit(run_gate, *gate): gate[0] for gate in selected}
        for future in as_completed(pending):
            try:
                row = future.result()
            except Exception as error:
                row = {'gate': pending[future], 'exit': None, 'error': str(error)}
            report['checks'].append(row)
            save()
            print(json.dumps(row), flush=True)
    report['identities_unchanged'] = all(Path(p).is_file() and sha(p) == digest for p, digest in identities.items())
    report['complete_planned_coverage'] = len(report['checks']) == len(selected)
    report['passed'] = report['complete_planned_coverage'] and report['identities_unchanged'] and all(r['exit'] == 0 for r in report['checks'])
    save()
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
