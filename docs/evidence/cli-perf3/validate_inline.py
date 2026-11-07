#!/usr/bin/env python3
"""Prepared validation for the optional, frozen four-attribute inline candidate.

Without --run this only prints the plan. It never adopts attributes, builds a
frozen candidate/cloud companion, or changes tests.json. After an explicit
adoption, freeze an inline cloud companion with the same 93 source hashes, then:

    python3 docs/evidence/cli-perf3/validate_inline.py \
      --cloud-manifest PATH/TO/INLINE-CLOUD-MANIFEST.json --run

The cloud manifest may be a dedicated {binary, sha256, sources, build} document
or the normal candidate manifest extended with {cloud: {binary, sha256, build}}.
All command execution follows the source/binary preflight under the shared
flock. Outputs: tests-inline.json, validated-inline-candidate.json (success only),
and harness/results/perf3/inline-validation/*.log. Existing outputs are refused.
"""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
LOCK = Path('/home/ajs/.cache/pb-rs-performance.lock')
CARGO = '/home/ajs/.cargo/bin/cargo'
CANDIDATE = ROOT / 'bin/pb-perf3-inline-1'
DEFAULT_CLOUD = ROOT / 'bin/pb-perf3-inline-1-cloud'
DEFAULT_MANIFEST = ROOT / 'harness/results/perf3/inline-experiment/candidate.json'
PREDECESSOR_MANIFEST = HERE / 'final-candidate.json'
PREDECESSOR_TESTS = HERE / 'tests.json'
REPORT = HERE / 'tests-inline.json'
VALIDATED = HERE / 'validated-inline-candidate.json'
ARTIFACTS = ROOT / 'harness/results/perf3/inline-validation'
GO_OUT = ARTIFACTS / 'go-replay'
ENV = {'PB_VERSION': 'v0.0.0-harness', 'PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN': ''}
METHODS = ('reserve', 'extend_from_slice', 'spaces', 'push')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def locate(path):
    value = Path(path)
    return (value if value.is_absolute() else ROOT / value).resolve()


def write(path, document):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def rust_source_paths(base):
    result = {'Cargo.toml', 'Cargo.lock', 'build.rs', 'src/spec.json'}
    for directory in ('src', 'tests'):
        result.update(str(p.relative_to(base)) for p in (base / directory).rglob('*.rs'))
    return result


def remove_only_four_attributes(data):
    require(data.count(b'#[inline(always)]') == 4, 'output.rs must contain exactly the four proposed inline(always) attributes')
    for method in METHODS:
        decorated = b'    #[inline(always)]\n    fn ' + method.encode() + b'('
        require(data.count(decorated) == 1, 'missing/duplicate proposed attribute on ' + method)
        data = data.replace(decorated, b'    fn ' + method.encode() + b'(', 1)
    return data


def commands(cloud):
    # The compatibility driver's exact group ID is phase3. Its ordinary tier
    # includes cloud refresh cases: an inline cloud companion is mandatory.
    return [
        ('fmt', [CARGO, '+1.96.0', 'fmt', '--check'], None),
        ('cargo-test', [CARGO, '+1.96.0', 'test', '--locked'], 867),
        ('diagnostics', [CARGO, '+1.96.0', 'test', '--locked', '--features', 'diagnostics'], 873),
        ('clippy', [CARGO, '+1.96.0', 'clippy', '--locked', '--all-targets', '--all-features', '--', '-D', 'warnings'], None),
        ('release-output', [CARGO, '+1.96.0', 'test', '--locked', '--release', '--lib', 'sql::output'], 24),
        ('go-phase3-ordinary', ['python3', 'docs/evidence/cli-perf2/go_compatibility.py',
                              '--candidate', str(CANDIDATE), '--cloud-candidate', str(cloud),
                              '--out', str(GO_OUT), '--jobs', '1', '--gate', 'phase3', '--run'], 688),
    ]


def preflight(args):
    candidate = read(args.candidate_manifest)
    previous = read(PREDECESSOR_MANIFEST)
    cloud_document = read(args.cloud_manifest or args.candidate_manifest)
    cloud = cloud_document.get('cloud', cloud_document)
    source_root = locate(candidate['source_root'])
    sources = candidate['sources']
    require(len(sources) == 93 and set(sources) == set(previous['sources']), 'expected the complete predecessor 93-file source manifest')
    require(rust_source_paths(ROOT) == set(sources) == rust_source_paths(source_root), 'Rust source inventory differs from the 93-file manifest')
    require(locate(candidate['binary']) == CANDIDATE.resolve(), 'manifest must freeze bin/pb-perf3-inline-1')
    require(candidate['baseline_sha256'] == previous['sha256'], 'inline manifest has a different R4 predecessor')
    require(candidate['patch_sha256'] == sha(HERE / 'inline-experiment.patch'), 'inline patch identity differs')
    require(cloud_document.get('sources') == sources, 'inline cloud companion must declare the identical 93 source hashes')
    cloud_path = locate(cloud['binary'])
    require(cloud_path != CANDIDATE.resolve() and cloud['sha256'] != candidate['sha256'], 'cloud companion must be separately frozen')
    require(cloud_path != locate(previous['cloud']['binary']), 'R4 cloud companion is predecessor coverage, not the inline cloud candidate')
    cloud_build = cloud.get('build', cloud)
    require(cloud_build.get('PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN') == 'harness-orchestrator-token', 'cloud manifest must declare the synthetic harness token build')
    require(candidate['build'].get('PB_VERSION') == ENV['PB_VERSION']
            and candidate['build'].get('PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN') == ''
            and candidate['build'].get('toolchain') == '1.96.0'
            and candidate['build'].get('profile') == 'release'
            and candidate['build'].get('locked') is True, 'unexpected frozen inline build configuration')
    changed = [name for name in sources if sources[name] != previous['sources'][name]]
    require(changed == ['src/sql/output.rs'], 'the inline experiment must change only src/sql/output.rs')
    normalized = remove_only_four_attributes((source_root / changed[0]).read_bytes())
    require(hashlib.sha256(normalized).hexdigest() == previous['sources'][changed[0]], 'isolated candidate differs from R4 beyond the four attributes')
    for name, digest in sources.items():
        require(sha(source_root / name) == digest, 'isolated source differs from frozen candidate: ' + name)
        require(sha(ROOT / name) == digest, 'root must first adopt exactly the isolated candidate sources: ' + name)
    fixture = 'docs/evidence/phase7/style-byte-oracle-fixtures.tsv'
    require(sha(ROOT / fixture) == sha(source_root / fixture), 'root/isolated style oracle fixture differs')
    predecessor = read(PREDECESSOR_TESTS)
    require(predecessor.get('complete') is True and predecessor.get('binary_identities_unchanged') is True, 'predecessor validation evidence is incomplete')
    python = next((entry for entry in predecessor['commands'] if entry['name'] == 'python-testfix'), {})
    require(python.get('exit_code') == 0 and python.get('counts', {}).get('tests') == 107,
            'corrected 107-test predecessor Python coverage is missing')
    require(sha(ROOT / 'harness/test_perf3_cached.py') == predecessor['test_only_resolution']['after_sha256'], 'corrected Python harness has changed; rerun Python separately')
    previous_replay = locate(predecessor['go_replay']['path'])
    require(sha(previous_replay) == predecessor['go_replay']['sha256'], 'predecessor Go input provenance changed')
    old_inputs = read(previous_replay)['input_sha256']
    python_files = ('test_stress.py', 'test_perf3_cached.py', 'test_ingest_perf.py',
                    'test_stress_process.py', 'test_stress_ingest.py', 'stress.py',
                    'perf3_cached.py', 'ingest_perf.py', 'stress_process.py', 'stress_ingest.py', 'run.py')
    for name in python_files:
        path = ROOT / 'harness' / name
        expected = (predecessor['test_only_resolution']['after_sha256'] if name == 'test_perf3_cached.py'
                    else old_inputs.get(str(path)))
        require(expected == sha(path), 'Python suite/module changed since corrected 107-test run; rerun separately: ' + name)
    binary_specs = {'candidate': (CANDIDATE, candidate['sha256']),
                    'cloud_candidate': (cloud_path, cloud['sha256']),
                    'predecessor': (locate(previous['binary']), previous['sha256']),
                    'cloud_predecessor': (locate(previous['cloud']['binary']), previous['cloud']['sha256'])}
    for role, (path, digest) in binary_specs.items():
        require(sha(path) == digest, role + ' binary differs from its frozen manifest')
    controls = {Path(__file__).resolve(), args.candidate_manifest.resolve(),
                (args.cloud_manifest or args.candidate_manifest).resolve(), PREDECESSOR_MANIFEST,
                PREDECESSOR_TESTS, HERE / 'inline-experiment.patch',
                previous_replay,
                ROOT / 'docs/evidence/cli-perf2/go_compatibility.py', ROOT / fixture,
                source_root / fixture}
    controls.update((ROOT / 'harness').glob('*.py'))
    controls.update(p for p in (ROOT / 'harness/contracts').glob('*') if p.is_file())
    require(not REPORT.exists() and not VALIDATED.exists() and not ARTIFACTS.exists(), 'refusing to overwrite prior inline validation outputs')
    return candidate, cloud, source_root, binary_specs, sorted(controls), predecessor


def snapshot(candidate, source_root, binary_specs, controls):
    return {'root_sources': {name: sha(ROOT / name) for name in candidate['sources']},
            'isolated_sources': {name: sha(source_root / name) for name in candidate['sources']},
            'binaries': {role: {'path': str(path), 'sha256': sha(path)} for role, (path, _) in binary_specs.items()},
            'control_sources': {str(path): sha(path) for path in controls}}


def execute(name, argv, expected):
    log = ARTIFACTS / (name + '.log')
    result = {'name': name, 'argv': argv, 'cwd': str(ROOT), 'environment': ENV,
              'command': ' '.join(shlex.quote(k + '=' + v) for k, v in ENV.items()) + ' ' + shlex.join(argv),
              'started_at': now(), 'log': str(log), 'expected_passed_count': expected}
    print('START ' + name, flush=True)
    start = time.monotonic()
    with log.open('xb') as stream:
        process = subprocess.run(argv, cwd=ROOT, env={**os.environ, **ENV}, stdout=stream, stderr=subprocess.STDOUT)
    result.update(exit_code=process.returncode, ended_at=now(), elapsed_seconds=time.monotonic() - start,
                  log_sha256=sha(log), log_size_bytes=log.stat().st_size, passed=process.returncode == 0)
    text = log.read_text(errors='replace')
    if name != 'go-phase3-ordinary' and expected is not None:
        groups = re.findall(r'test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out', text)
        counts = {key: sum(int(group[i + 1]) for group in groups)
                  for i, key in enumerate(('passed', 'failed', 'ignored', 'measured', 'filtered_out'))}
        result['counts'] = counts
        result['test_result_groups'] = groups
        result['passed'] &= bool(groups) and counts['passed'] == expected and counts['failed'] == 0
    if name == 'go-phase3-ordinary':
        replay_path = GO_OUT / 'replay.json'
        case_path = GO_OUT / 'phase3/report.json'
        if replay_path.is_file() and case_path.is_file():
            replay, cases = read(replay_path), read(case_path)
            rows = cases.get('cases', [])
            result['counts'] = {'passed': sum(row.get('status') == 'pass' for row in rows),
                                'failed': sum(row.get('status') == 'fail' for row in rows),
                                'skipped': sum(row.get('status') == 'skip' for row in rows), 'total': len(rows)}
            result['selected_gate'] = replay['selected_gates']
            result['replay'] = {'path': str(replay_path), 'sha256': sha(replay_path)}
            result['phase3_report'] = {'path': str(case_path), 'sha256': sha(case_path)}
            result['passed'] &= (replay.get('passed') is True and replay.get('identities_unchanged') is True
                                 and replay['selected_gates'] == ['phase3'] and len(replay['checks']) == 1
                                 and cases.get('complete') is True and len(rows) == expected
                                 and all(row.get('status') == 'pass' and {'reference', 'candidate'} <= set(row.get('runs', {})) for row in rows))
        else:
            result.update(passed=False, error='missing ordinary phase3 compatibility reports')
    print('END ' + name + ' exit=' + str(result['exit_code']) + ' admitted=' + str(result['passed']), flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--cloud-manifest', type=Path, help='frozen inline cloud manifest with the identical 93 sources')
    parser.add_argument('--run', action='store_true', help='execute only after explicit adoption; default is a plan')
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({'prepared_only': True, 'executed': False, 'requires_adopted_root_sources': True,
                          'inline_cloud_companion_required': True, 'ordinary_go_gate_id': 'phase3',
                          'commands': [{'name': name, 'argv': command, 'expected_passed_count': count}
                                       for name, command, count in commands(DEFAULT_CLOUD)],
                          'outputs': [str(REPORT), str(VALIDATED), str(ARTIFACTS)]}, indent=2))
        return 0
    print('WAITING_FOR_LOCK ' + str(LOCK), flush=True)
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print('LOCK_ACQUIRED ' + now(), flush=True)
        report = None
        try:
            candidate, cloud, source_root, binaries, controls, predecessor = preflight(args)
            before = snapshot(candidate, source_root, binaries, controls)
            report = {'schema': 'pb-cli-perf3-inline-validation/1', 'complete': False, 'passed': False,
                      'lock': str(LOCK), 'lock_acquired_at': now(), 'before': before, 'commands': [], 'errors': [],
                      'attribute_only_change': {'file': 'src/sql/output.rs', 'methods': METHODS, 'count': 4,
                                               'normalized_bytes_match_predecessor': True},
                      'coverage': {'inline_candidate_validation_plan': ['fmt', 'Rust default 867', 'Rust diagnostics 873',
                                                                    'clippy all targets/features', 'release output 24',
                                                                    'ordinary Go phase3 688 including inline cloud refresh'],
                                   'predecessor_only_not_rerun': ['corrected Python 107', 'slow deadlines',
                                                                 'other Go compatibility groups'],
                                   'note': 'Predecessor coverage is retained evidence, not inline-candidate execution. No performance benchmarks.',
                                   'predecessor_tests_path': str(PREDECESSOR_TESTS), 'predecessor_tests_sha256': sha(PREDECESSOR_TESTS),
                                   'predecessor_go_initial': predecessor['go_replay']['counts'],
                                   'predecessor_go_single_repeat': predecessor['go_refresh_repeat']['counts']},
                      'benchmarks_run': False, 'python_rerun': False,
                      'python_coverage_guard': {'unchanged_suite_and_module_files_verified': True}}
            ARTIFACTS.mkdir(parents=True)
            write(REPORT, report)
            for name, command, expected in commands(locate(cloud['binary'])):
                report['commands'].append(execute(name, command, expected))
                report['after'] = snapshot(candidate, source_root, binaries, controls)
                report['identities_unchanged'] = before == report['after']
                write(REPORT, report)
                require(report['identities_unchanged'], 'source/binary/control identity changed during validation; refusing further commands')
            report['complete'] = True
            report['passed'] = all(command['passed'] for command in report['commands']) and report['identities_unchanged']
            if report['passed']:
                write(VALIDATED, {'binary': str(CANDIDATE), 'sha256': candidate['sha256'], 'sources': before['root_sources'],
                                  'source_count': 93, 'build': candidate['build'], 'cloud': cloud,
                                  'isolated_manifest': str(args.candidate_manifest.resolve()),
                                  'isolated_manifest_sha256': sha(args.candidate_manifest),
                                  'predecessor': read(PREDECESSOR_MANIFEST)['sha256'],
                                  'validation': str(REPORT), 'coverage': report['coverage']})
        except Exception as error:
            if report is not None:
                report['errors'].append(type(error).__name__ + ': ' + str(error))
                report['passed'] = False
            print(type(error).__name__ + ': ' + str(error), file=sys.stderr, flush=True)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            if report is not None:
                report['lock_released_at'] = now()
                write(REPORT, report)
            print('LOCK_RELEASED ' + now(), flush=True)
    return 0 if report is not None and report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
