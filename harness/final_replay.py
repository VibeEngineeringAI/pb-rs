#!/usr/bin/env python3
"""Replay retained compatibility gates against one frozen source build family.

Normal, public-token Cloud and loopback-device builds have intentionally different
compile-time configuration. This runner never builds, changes expectations, or
treats an earlier binary's report as evidence for the supplied binaries.
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
from build_final import sources as build_sources

ROOT = Path(__file__).resolve().parent.parent


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def commands(args):
    ref, candidate = args.reference_dir, args.candidate_dir
    common = ['--reference', str(ref / 'pb-go'), '--candidate', str(candidate / 'pb-normal')]
    cloud = ['--cloud-reference', str(ref / 'pb-go-cloud'),
             '--cloud-candidate', str(candidate / 'pb-cloud')]
    for phase in range(1, 6):
        command = [sys.executable, f'harness/phase{phase}.py', 'compare', *common]
        if phase > 1:
            command += cloud
        if phase == 2:
            command += ['--fixture', str(args.fixture)]
        if phase == 3:
            command += ['--meter', str(args.meter)]
        yield f'phase{phase}', command
        if phase >= 3:
            # Every retained deadline case keeps the original runner and its
            # reference repetitions, but owns a separate output tree. This lets
            # independent minute-long waits overlap without changing deadlines.
            module = importlib.import_module(f'phase{phase}')
            option = '--include-full120s' if phase == 4 else '--include-full60s'
            for case in module.corpus():
                if case['tier'].startswith('full'):
                    yield case['name'], [*command, option, '--case', case['name']]
    for suite in ('main', 'trailer-followup', 'review3-followup'):
        command = [sys.executable, 'harness/phase6.py', 'compare',
            '--go', str(ref / 'pb-go'), '--go-cloud', str(ref / 'pb-go-cloud'),
            '--candidate', str(candidate / 'pb-normal'),
            '--candidate-cloud', str(candidate / 'pb-cloud'),
            '--fixture', str(args.flight_fixture), '--suite', suite]
        if suite != 'main':
            command += ['--reference', str(getattr(args, suite.replace('-', '_') + '_reference'))]
        yield 'flight-' + suite, command
    yield 'flight-pr4-decoder', [sys.executable, 'docs/evidence/phase7/merged-flight-pr4-replay.py',
        '--candidate', str(candidate / 'pb-normal'), '--candidate-sha256', digest(candidate / 'pb-normal'),
        '--go', str(ref / 'pb-go'), '--go-sha256', digest(ref / 'pb-go'),
        '--fixture', str(args.decoder_fixture), '--fixture-sha256', digest(args.decoder_fixture)]
    yield 'interactive', [sys.executable, 'harness/phase7.py', 'compare',
                          *common, *cloud, '--theme', 'both']
    yield 'device-login', [sys.executable, 'harness/phase7_device.py',
        '--reference', str(ref / 'pb-go-login'), '--candidate', str(candidate / 'pb-login')]
    yield 'root-help', [sys.executable, 'harness/root_help.py', *common]
    yield 'interactive-effects', [str(args.effects_test), '--include-ignored', '--test-threads=4']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-dir', type=Path, required=True)
    parser.add_argument('--candidate-dir', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--flight-fixture', type=Path, required=True)
    parser.add_argument('--decoder-fixture', type=Path, required=True)
    parser.add_argument('--meter', type=Path, required=True)
    parser.add_argument('--effects-test', type=Path, required=True,
                        help='Prebuilt tests/interactive_effects.rs executable')
    parser.add_argument('--trailer-followup-reference', type=Path, required=True)
    parser.add_argument('--review3-followup-reference', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--gate', action='append', help='Explicit subset; report records incomplete coverage')
    parser.add_argument('--jobs', type=int, default=4,
                        help='Independent gate processes (not performance measurements)')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    for field in ('reference_dir', 'candidate_dir', 'fixture', 'meter', 'out',
                  'trailer_followup_reference', 'review3_followup_reference',
                  'flight_fixture', 'decoder_fixture', 'effects_test'):
        setattr(args, field, getattr(args, field).resolve())
    matrix = list(commands(args))
    if args.gate and set(args.gate) - {name for name, _ in matrix}:
        parser.error('unknown --gate')
    inputs = [args.reference_dir / name for name in ('pb-go', 'pb-go-cloud', 'pb-go-login')]
    inputs += [args.candidate_dir / name for name in ('pb-normal', 'pb-cloud', 'pb-login')]
    inputs += [args.fixture, args.meter, args.flight_fixture, args.decoder_fixture,
               args.trailer_followup_reference, args.review3_followup_reference,
               args.effects_test, ROOT / 'tests/interactive_effects.rs']
    required = list(inputs)
    build_path = args.candidate_dir / 'build-manifest.json'
    build = json.loads(build_path.read_text())
    if not build.get('complete'):
        parser.error('candidate build manifest is incomplete')
    if build_sources() != build.get('source_sha256'):
        parser.error('source inventory differs from build manifest')
    for name in ('pb-normal', 'pb-cloud', 'pb-login'):
        if digest(args.candidate_dir / name) != build['binaries'].get(name):
            parser.error('candidate differs from build manifest: ' + name)
    for relative, sha in build['source_sha256'].items():
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or digest(path) != sha:
            parser.error('source differs from build manifest: ' + relative)
        inputs.append(path)
    inputs.append(build_path)
    inputs += sorted((ROOT / 'harness').glob('*.py'))
    inputs += sorted((ROOT / 'harness' / 'contracts').glob('*'))
    inputs += [ROOT / 'docs/evidence/phase7/merged-flight-pr4-replay.py']
    inputs = [p for p in inputs if p.is_file() or p in required]
    identities = {str(p): digest(p) for p in inputs}
    args.out.mkdir(parents=True, exist_ok=False)
    report = {'kind': 'final compatibility replay; no performance claim',
              'input_sha256': identities, 'all_gates': [name for name, _ in matrix],
              'selected_gates': args.gate or [name for name, _ in matrix], 'checks': []}

    def save():
        report['complete_coverage'] = len(report['checks']) == len(matrix)
        report['passed'] = report['complete_coverage'] and all(
            row['exit'] == 0 and row['identities_unchanged'] for row in report['checks'])
        (args.out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')

    def verify():
        return build_sources() == build['source_sha256'] and all(
            Path(p).is_file() and digest(p) == sha for p, sha in identities.items())

    save()
    def run_gate(name, command):
        if not verify():
            raise RuntimeError('input identity changed before ' + name)
        env = dict(os.environ)
        if name == 'interactive-effects':
            env.update(PB_GO_ORACLE=str(args.reference_dir / 'pb-go'),
                       PB_GO_ORACLE_CLOUD=str(args.reference_dir / 'pb-go-cloud'),
                       PB_EFFECTS_CANDIDATE=str(args.candidate_dir / 'pb-normal'),
                       PB_EFFECTS_CLOUD_CANDIDATE=str(args.candidate_dir / 'pb-cloud'),
                       PB_EFFECTS_PYTHON=sys.executable,
                       PB_EFFECTS_CAPTURE_DIR=str(args.out / name))
        else:
            command = [*command, '--out', str(args.out / name)]
        start = time.monotonic()
        with (args.out / (name + '.log')).open('x') as log:
            # Each retained runner owns its case deadlines and child cleanup.
            # Do not kill only its process group: PTY/Flight runners deliberately
            # create further sessions that would outlive that group.
            code = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                  stderr=subprocess.STDOUT, check=False).returncode
        stable = verify()
        return {'gate': name, 'command': command, 'exit': code,
                'elapsed_seconds': time.monotonic() - start,
                'identities_unchanged': stable}

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_gate, name, command): name for name, command in matrix
                   if not args.gate or name in args.gate}
        for future in as_completed(futures):
            name = futures[future]
            try:
                row = future.result()
            except Exception as error:
                row = {'gate': name, 'exit': None, 'identities_unchanged': False,
                       'error': str(error)}
            report['checks'].append(row)
            save()
            print(f"{name}: exit={row['exit']}; identities_unchanged={row['identities_unchanged']}", flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
