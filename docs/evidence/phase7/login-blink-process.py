#!/usr/bin/env python3
"""Two functional login blink controls bound to the retained pinned-Go oracle.

Requires the phase7 harness Python environment and an already compiled native
helper. Set TMPDIR/GOTMPDIR/CLAUDE_CODE_TMPDIR to the phase7 tmp directory.
No compilation, normalization, timing campaign, profiler or lock is performed.
The ordinary helper capability proof runs during preparation only.
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'harness'))
import phase7 as p7
import phase7_workloads as workloads

GO_ROOT = p7.TMP.parent / 'release4-masked-investigation'
FROZEN_FILES = ('harness/phase7.py', 'harness/test_phase7.py', 'harness/phase7_workloads.py',
                'harness/phase7_spawn.c', 'docs/phase7-harness.md',
                'harness/phase1.py', 'harness/phase2.py', 'harness/phase3.py',
                'src/tui/login.rs', 'src/tui/editor.rs', 'src/tui/style.rs')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_snapshot(folder):
    result = {}
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            raise ValueError('evidence symlink rejected: ' + str(path))
        if path.is_file():
            result[str(path.relative_to(folder))] = sha(path)
    return result


def read_json(path):
    encoded = path.read_bytes()
    return json.loads(encoded), {'path': str(path), 'sha256': hashlib.sha256(encoded).hexdigest()}


def source_snapshot():
    return {name: sha(ROOT / name) for name in (*FROZEN_FILES, str(Path(__file__).resolve().relative_to(ROOT)))}


def cases():
    base = next(c for c in workloads.corpus(themes=('dark',))
                if c['name'] == 'p7-login-overwrite-bad-config-dark')
    result = []
    for dwell in (200, 650):
        case = copy.deepcopy(base)
        case['name'] = f'oracle-login-username-dwell-{dwell}-dark'
        case['actions'][9]['wait']['min_elapsed_ms'] = dwell
        case['actions'][9]['stable_ms'] = 0
        case['actions'][12]['stable_ms'] = 0
        case['actions'].insert(13, {'checkpoint': 'password-after-old-timer', 'stable_ms': 0,
            'wait': {'contains': ['PASSWORD', '•' * 14], 'min_elapsed_ms': 250}})
        result.append((dwell, case))
    return result


def readiness(terminal, wait, trace, tty, started, now):
    return (p7.useful(terminal, wait, trace, tty) and
            now - started >= wait.get('min_elapsed_ms', 0) * 1e6)


def execute(*args, **kwargs):
    return p7.execute(*args, **kwargs, readiness_fn=readiness, spawn_helper_build=False)


def frame_differences(left, right):
    differences = []
    for field in left.keys() | right.keys():
        a, b = left.get(field), right.get(field)
        if a == b:
            continue
        if field not in ('cells', 'scrollback') or a is None or b is None:
            differences.append({'field': field, 'reference': a, 'actual': b})
            continue
        if len(a) != len(b):
            differences.append({'field': field, 'reference_rows': len(a), 'actual_rows': len(b)})
        for row, (lr, rr) in enumerate(zip(a, b)):
            if len(lr) != len(rr):
                differences.append({'field': field, 'row_zero_based': row,
                                    'reference_columns': len(lr), 'actual_columns': len(rr)})
            for col, (l, r) in enumerate(zip(lr, rr)):
                if l != r:
                    differences.append({'field': field, 'row_zero_based': row,
                        'column_zero_based': col, 'reference': l, 'actual': r})
    return differences


def checkpoint_differences(reference, actual):
    left = {f['name']: f['frame'] for f in reference['frames']}
    right = {f['name']: f['frame'] for f in actual['frames']}
    result = {}
    for name in left.keys() | right.keys():
        if name not in left or name not in right:
            result[name] = [{'field': 'checkpoint', 'reference_present': name in left,
                             'actual_present': name in right}]
        else:
            result[name] = frame_differences(p7.normalized_frame(left[name], reference['replacements']),
                                            p7.normalized_frame(right[name], actual['replacements']))
    return result


def action_effects(result):
    return [{key: value for key, value in event.items()
             if key not in ('monotonic_ns', 'evaluated_monotonic_ns', 'dispatch_monotonic_ns')}
            for event in result['actions']]


def causal_deadlines(result):
    actions = {event['index']: event for event in result['actions']}
    frames = {frame['name']: frame for frame in result['frames']}
    if any(index not in actions for index in (8, 9, 11)) or any(
            name not in frames for name in ('masked-password', 'password-after-old-timer')):
        return {'incomplete': True}
    password = actions[11].get('dispatch_monotonic_ns')
    if password is None:
        return {'incomplete': True, 'reason': 'missing password dispatch origin'}
    late = frames['password-after-old-timer']['ready_monotonic_ns']
    return {'kind': 'causal functional deadlines; no performance claim',
        'username_dispatch_ns': actions[8]['dispatch_monotonic_ns'],
        'username_enter_dispatch_ns': actions[9]['dispatch_monotonic_ns'],
        'password_dispatch_ns': password,
        'masked_ready_ns': frames['masked-password']['ready_monotonic_ns'],
        'after_old_timer_ready_ns': late,
        'late_after_password_ms': (late - password) / 1e6,
        'late_before_password_own_530ms_deadline': 0 <= late - password < 530_000_000}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--expect-sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--role', choices=('go', 'candidate'), required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'kind': 'two-functional-login-blink-process-controls',
        'role': args.role, 'performance_claim': False, 'acceptance_claim': False,
        'normalization': False, 'status': 'preparation', 'sessions': []}
    def save():
        (args.out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    save()
    original_files = before = helper = binary = identity = manifest_identity = None
    try:
        original_files = files_snapshot(GO_ROOT)
        before = source_snapshot()
        go_report, go_identity = read_json(GO_ROOT / 'go-controls.json')
        go_sealed, sealed_identity = read_json(GO_ROOT / 'sealed.json')
        investigation, investigation_identity = read_json(GO_ROOT / 'report.json')
        if go_sealed['report']['sha256'] != investigation_identity['sha256']:
            raise ValueError('sealed Go investigation report drift')
        if investigation['two_actual_Go_controls']['sha256'] != go_identity['sha256']:
            raise ValueError('sealed Go controls report drift')
        for name, expected in go_report['sources'].items():
            if before[name] != expected:
                raise ValueError('source drift from sealed Go controls: ' + name)
        binary = args.binary.resolve(strict=True)
        identity = p7.phase1.binary_identity(binary)
        if identity['sha256'] != args.expect_sha256:
            raise ValueError('binary identity differs from --expect-sha256')
        if args.role == 'candidate':
            manifest, manifest_identity = read_json(binary.parent / 'manifest.json')
            if not any(Path(artifact['path']).resolve() == binary and artifact['sha256'] == identity['sha256']
                       for artifact in manifest['artifacts'].values()):
                raise ValueError('candidate provenance does not bind the selected artifact')
            for name in ('src/tui/login.rs', 'src/tui/editor.rs', 'src/tui/style.rs'):
                if before[name] != manifest['source_files'][name]:
                    raise ValueError('candidate source provenance mismatch: ' + name)
            report['candidate_provenance'] = {**manifest_identity,
                'source_snapshot_sha256': manifest['source_snapshot_sha256']}
        report.update(binary_before=identity, sources_before=before, original_Go_files=original_files,
            sealed_Go_evidence={'sealed': sealed_identity, 'investigation': investigation_identity,
                                'controls': go_identity, 'source_pin': go_report['source_pin']})
        helper = p7.spawn_helper(build=False)
        if helper != go_report['helper']:
            raise ValueError('helper identity/proof differs from sealed Go controls')
        report['helper_before'] = helper
        def check():
            if p7.phase1.binary_identity(binary) != identity:
                raise ValueError('binary identity drift')
            if source_snapshot() != before:
                raise ValueError('source identity drift')
            if files_snapshot(GO_ROOT) != original_files:
                raise ValueError('original Go evidence drift')
            if p7.spawn_helper(build=False, probe=False) != helper:
                raise ValueError('helper identity drift')
            if manifest_identity and sha(Path(manifest_identity['path'])) != manifest_identity['sha256']:
                raise ValueError('candidate provenance drift')
        for dwell, case in cases():
            go_row = next(row for row in go_report['sessions'] if row['dwell_ms'] == dwell)
            go_path = Path(go_row['path'])
            if sha(go_path) != go_row['sha256']:
                raise ValueError('retained Go session drift')
            go = p7.load_session(go_path)
            if go['case'] != case:
                raise ValueError('case/action mismatch against actual Go control')
            check()
            report['status'] = f'capturing-dwell-{dwell}'
            save()
            output = args.out / f'dwell-{dwell}'
            actual = p7.sample(str(binary), case, output, execute_fn=execute)
            row = {'dwell_ms': dwell, 'case_sha256': p7.digest(case),
                'session': {'path': str(output / 'session.json'), 'sha256': sha(output / 'session.json')},
                'reference_session': {'path': str(go_path), 'sha256': sha(go_path)},
                'reference_issues': p7.validate(go), 'actual_issues': p7.validate(actual),
                'comparison_issues': p7.compare(go, actual),
                'checkpoint_differences': checkpoint_differences(go, actual),
                'termination': actual['termination'], 'termios_restored': actual['termios_restored'],
                'action_effects_equal': action_effects(go) == action_effects(actual),
                'ordered_requests_equal': p7.ordered_request_contract(go) == p7.ordered_request_contract(actual),
                'config_before_equal': go['config_before'] == actual['config_before'],
                'config_after_equal': go['config_after'] == actual['config_after'],
                'final_frame_differences': frame_differences(
                    p7.normalized_frame(go['final_frame'], go['replacements']),
                    p7.normalized_frame(actual['final_frame'], actual['replacements'])),
                'Go_causal_deadlines': causal_deadlines(go),
                'actual_causal_deadlines': causal_deadlines(actual)}
            report['sessions'].append(row)
            save()  # Preserve failures before final identity checks.
            if actual['binary'] != identity or actual['metrics']['native_spawn']['sha256'] != helper['sha256']:
                raise ValueError('captured binary/helper binding differs')
            check()
            print(dwell, row['comparison_issues'], flush=True)
        report['status'] = 'completed'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        try:
            if original_files is not None:
                report['original_Go_evidence_unchanged'] = files_snapshot(GO_ROOT) == original_files
            if before is not None:
                report['sources_after'] = source_snapshot()
                report['sources_unchanged'] = report['sources_after'] == before
            if binary is not None:
                report['binary_after'] = p7.phase1.binary_identity(binary)
            if helper is not None:
                report['helper_after'] = p7.spawn_helper(build=False, probe=False)
            unchanged = (report.get('original_Go_evidence_unchanged', True) and
                         report.get('sources_unchanged', True) and
                         (identity is None or report.get('binary_after') == identity) and
                         (helper is None or report.get('helper_after') == helper) and
                         (manifest_identity is None or
                          sha(Path(manifest_identity['path'])) == manifest_identity['sha256']))
            if not unchanged:
                raise ValueError('final identity/evidence drift')
        except Exception as error:
            report['final_identity_error'] = {'type': type(error).__name__, 'message': str(error)}
            report['status'] = 'failed'
        save()
    return int(report['status'] != 'completed' or any(row['comparison_issues'] or row['actual_issues']
        or not row['action_effects_equal'] or not row['actual_causal_deadlines'].get(
            'late_before_password_own_530ms_deadline', False) for row in report['sessions']))


if __name__ == '__main__':
    raise SystemExit(main())
