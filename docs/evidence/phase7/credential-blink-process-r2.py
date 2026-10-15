#!/usr/bin/env python3
"""Frozen-source credential blink controls; no build, normalization or benchmark.

Use the phase7 harness environment. Each invocation captures A/B/C exactly once.
Optional --compare-go reads a completed Go output directory without modifying it.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'harness'))
import phase7 as p7
import phase7_workloads as workloads

PIN = '07af390ab09bda8ac9e427ffc6310077bace0125'
FREEZE = ('src/tui/login.rs', 'src/tui/editor.rs', 'src/tui/profile.rs')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def identity(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size}


def sources():
    paths = [p for folder in ('src', 'pkg', 'harness') for p in (ROOT / folder).rglob('*')
             if p.is_file() and p.suffix in ('.rs', '.go', '.py', '.c', '.json', '.md')]
    paths += [ROOT / p for p in ('Cargo.toml', 'Cargo.lock', 'go.mod', 'go.sum')]
    paths += [Path(__file__).resolve()]
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(set(paths))}


def cases():
    base = next(c for c in workloads.corpus(themes=('dark',))
                if c['name'] == 'p7-credential-valid-cancel-dark')
    result = []
    for label in ('A', 'B', 'C'):
        case = copy.deepcopy(base)
        case.update(name='credential-blink-' + label, timeout=10)
        actions = []
        def action(checkpoint=None, *, contains=(), at=None, anchor=None, minimum=0, **effect):
            wait = {'contains': list(contains), 'min_elapsed_ms': minimum}
            if at is not None:
                wait.update(after_anchor_ms=at, relative_to='initial' if label == 'A' else 'user')
            if anchor:
                wait['set_anchor'] = anchor
            row = {'wait': wait, 'stable_ms': 0, **effect}
            if checkpoint:
                row['checkpoint'] = checkpoint
            actions.append(row)
        action('initial', contains=('user: ', 'username', 'pass: ', 'password'), anchor='initial')
        if label != 'A':
            action(send='dummy-user', anchor='user')
            action('user-rendered', contains=('dummy-user',), minimum=35)
        action(key='tab', at=200)
        action('password-focused', contains=('pass: ', 'password'), minimum=25)
        actions[-1]['wait']['password_focus_rendered'] = True
        if label == 'B':
            action(send='dummy-', at=240)
            action('password-first-chunk', contains=('•' * 6,), minimum=35)
            action(send='password', at=310)
        else:
            action(send='dummy-password', at=310)
        action('password-rendered', contains=('•' * 14,), minimum=35)
        for name, at in (('before-old-timer', 450), ('after-old-timer', 610),
                         ('before-own-deadline', 760), ('after-own-deadline', 930),
                         ('after-collision-rearm', 1190)):
            action(name, contains=('•' * 14,), at=at)
        # A moving key resets the caret before cancellation, making final rendering
        # independent of the deliberately different blink phase. Values stay intact.
        action(key='left')
        action('cancel-ready', contains=('•' * 14,), minimum=35)
        action(key='ctrl-c')
        case['actions'] = actions
        if label == 'A':
            case['expect']['profiles']['new'].update(username='', password='')
        result.append((label, case))
    return result


def execute(*args, **kwargs):
    anchors = {}
    def readiness(terminal, wait, trace, tty, started, now):
        if not p7.useful(terminal, wait, trace, tty):
            return False
        if now - started < wait.get('min_elapsed_ms', 0) * 1e6:
            return False
        if wait.get('password_focus_rendered'):
            focused = False
            for row in terminal.frame()['cells']:
                x = ''.join(c[0] for c in row).find('pass: password')
                if x >= 0 and row[x + 6][p7.CELL_FIELDS.index('reverse')]:
                    focused = True
            if not focused:
                return False
        if 'after_anchor_ms' in wait:
            if now - anchors[wait['relative_to']] < wait['after_anchor_ms'] * 1e6:
                return False
        if 'set_anchor' in wait:
            anchors.setdefault(wait['set_anchor'], now)
        return True
    return p7.execute(*args, **kwargs, readiness_fn=readiness, spawn_helper_build=False)


def caret(frame):
    for y, row in enumerate(frame['cells']):
        text = ''.join(c[0] for c in row)
        x = text.find('pass: ')
        if x >= 0:
            x += len('pass: ') + 14
            return {'row_zero_based': y, 'column_zero_based': x,
                    'cell': row[x], 'reverse': row[x][p7.CELL_FIELDS.index('reverse')]}
    return None


def causal(result, label):
    frames = {f['name']: f for f in result['frames']}
    events = result['actions']
    if not all(n in frames for n in ('initial', 'password-rendered', 'before-old-timer',
                                    'after-old-timer', 'before-own-deadline')):
        return {'status': 'unknown', 'reason': 'incomplete checkpoints'}
    user = next((a for a in events if a.get('input_hex') == b'dummy-user'.hex()), None)
    last_pass = next(a for a in events if a.get('input_hex') ==
                     (b'password' if label == 'B' else b'dummy-password').hex())
    origin = (result['started_monotonic_ns'], frames['initial']['ready_monotonic_ns']) if label == 'A' else (
        user['dispatch_monotonic_ns'], frames['user-rendered']['ready_monotonic_ns'])
    own = (last_pass['dispatch_monotonic_ns'], frames['password-rendered']['ready_monotonic_ns'])
    old_due = [t + 530_000_000 for t in origin]
    own_due = [t + 530_000_000 for t in own]
    times = {n: f['ready_monotonic_ns'] for n, f in frames.items()}
    guards = {
        'before_old_is_before_earliest_old_deadline': times['before-old-timer'] < old_due[0],
        'after_old_is_after_latest_old_deadline': times['after-old-timer'] > old_due[1],
        'after_old_is_before_earliest_own_deadline': times['after-old-timer'] < own_due[0],
        'before_own_is_after_latest_old_deadline': times['before-own-deadline'] > old_due[1],
        'before_own_is_before_earliest_own_deadline': times['before-own-deadline'] < own_due[0],
        'user_render_precedes_navigation': label == 'A' or frames['user-rendered']['ready_monotonic_ns'] <
            next(a['dispatch_monotonic_ns'] for a in events if a.get('input_hex') == '09'),
    }
    focus = frames.get('password-focused')
    if result['case']['actions'][next(a['index'] for a in events if a.get('input_hex') == '09') + 1][
            'wait'].get('password_focus_rendered'):
        guards['focus_checkpoint_has_new_output'] = bool(focus and focus['bytes_since_action'] > 0)
    separation = None
    if label == 'B':
        first = next(a for a in events if a.get('input_hex') == b'dummy-'.hex())
        rendered = frames['password-first-chunk']
        separation = {'first_dispatch_ns': first['dispatch_monotonic_ns'],
            'first_six_bullets_ready_ns': rendered['ready_monotonic_ns'],
            'second_dispatch_ns': last_pass['dispatch_monotonic_ns'],
            'first_frame_sha256': p7.digest(rendered['frame']),
            'first_frame_bytes_since_action': rendered['bytes_since_action']}
        guards['separate_password_chunks_rendered_between_dispatches'] = (
            first['dispatch_monotonic_ns'] < rendered['ready_monotonic_ns'] < last_pass['dispatch_monotonic_ns']
            and rendered['bytes_since_action'] > 0)
    cells = {n: caret(f['frame']) for n, f in frames.items() if n != 'initial'}
    observed = (cells['before-old-timer']['reverse'], cells['after-old-timer']['reverse'],
                cells['before-own-deadline']['reverse'])
    return {'status': 'bounded' if all(guards.values()) else 'unknown', 'guards': guards,
        'origin_interval_ns': origin, 'old_due_interval_ns': old_due,
        'last_password_dispatch_ns': own[0], 'password_due_interval_ns': own_due,
        'checkpoint_after_origin_lower_ms': {n: (t - origin[0]) / 1e6 for n, t in times.items()},
        'checkpoint_after_password_dispatch_ms': {n: (t - own[0]) / 1e6 for n, t in times.items()},
        'password_chunk_separation': separation, 'password_caret_cells': cells,
        'observed_before_after_before_own_reverse': observed,
        'early_toggle_observed': all(guards.values()) and observed == (True, False, False),
        'no_early_toggle_observed': all(guards.values()) and observed == (True, True, True),
        'limits': 'Uninstrumented process. Tags inferred from source and separated input/rendering, not measured. '
                  'Deadline bounds use dispatch-to-render processing intervals; A uses launch-to-initial-render.'}


def differences(left, right):
    result = []
    for field in sorted(left.keys() | right.keys()):
        a, b = left.get(field), right.get(field)
        if a == b:
            continue
        if field not in ('cells', 'scrollback') or a is None or b is None:
            result.append({'field': field, 'go': a, 'candidate': b})
            continue
        if len(a) != len(b):
            result.append({'field': field, 'go_rows': len(a), 'candidate_rows': len(b)})
        for y, (ar, br) in enumerate(zip(a, b)):
            if len(ar) != len(br):
                result.append({'field': field, 'row': y, 'go_columns': len(ar), 'candidate_columns': len(br)})
            for x, (ac, bc) in enumerate(zip(ar, br)):
                if ac != bc:
                    result.append({'field': field, 'row_zero_based': y, 'column_zero_based': x,
                                   'go': ac, 'candidate': bc})
    return result


def strict_compare(go, actual):
    gf = {f['name']: f['frame'] for f in go['frames']}
    af = {f['name']: f['frame'] for f in actual['frames']}
    return {'case_equal': go['case'] == actual['case'], 'normalization': False,
        'checkpoint_differences': {n: differences(gf[n], af[n]) for n in gf.keys() & af.keys()},
        'checkpoint_names_equal': gf.keys() == af.keys(),
        'final_frame_differences': differences(go['final_frame'], actual['final_frame']),
        'equal_contract_fields': {f: go[f] == actual[f] for f in (
            'termination', 'config_before', 'config_after', 'termios_restored', 'termios_before',
            'termios_after', 'requests', 'stderr', 'post_screen_stdout', 'exec_error',
            'native_cleanup_issues', 'clipboard_events', 'surviving_children_before_observer_cleanup',
            'unreaped_children_before_observer_cleanup')},
        'input_effects_equal': [a.get('input_hex') for a in go['actions']] ==
                               [a.get('input_hex') for a in actual['actions']],
        'ordered_request_contract_equal': p7.ordered_request_contract(go) == p7.ordered_request_contract(actual)}


def raw_frames(result, path):
    terminal = p7.Terminal(*result['case'].get('size', [100, 30]), result['case']['theme'], lambda data: None)
    terminal.feed(result['case'].get('initial_screen', '').encode())
    terminal.byte_offset = 0
    frames = []
    for chunk in result['stdout_chunks']:
        terminal.feed(result['stdout'][chunk['start']:chunk['end']])
        frames.append({**chunk, 'frame': terminal.frame()})
    write(path, {'kind': 'all captured stdout chunk frames, replayed without normalization',
                 'cell_fields': result['cell_fields'], 'frames': frames})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--role', choices=('go', 'candidate'), required=True)
    parser.add_argument('--expect-sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--compare-go', type=Path)
    parser.add_argument('--cases', choices=('A', 'B', 'C'), nargs='+', default=['A', 'B', 'C'])
    parser.add_argument('--retry-cause', help='Required for a bounded subset retry; retained in evidence.')
    args = parser.parse_args()
    if args.cases != ['A', 'B', 'C'] and not args.retry_cause:
        parser.error('--retry-cause is required for subset captures')
    args.out.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'role': args.role, 'status': 'preparation', 'sessions': [],
        'source_pin': PIN, 'normalization': False, 'performance_claim': False,
        'acceptance_claim': False, 'session_budget': 'Selected cases once per role; no automatic retries; timeout 10 seconds',
        'selected_cases': args.cases, 'retry_cause': args.retry_cause,
        'SOURCE FREEZE': 'All native login/editor/profile and harness files frozen for all captures.'}
    report_path = args.out / 'report.json'
    before = helper = binary = manifests = None
    try:
        before = sources()
        binary = p7.phase1.binary_identity(args.binary)
        if binary['sha256'] != args.expect_sha256:
            raise ValueError('binary SHA256 differs from requested identity')
        manifest_paths = [ROOT / 'docs/evidence/phase7/login-blink-investigation.json',
            ROOT / 'docs/evidence/phase7/login-blink-design-r1.md',
            ROOT / 'docs/evidence/phase7/login-blink-process.py']
        if args.role == 'candidate':
            manifest_path = args.binary.resolve().parent / 'manifest.json'
            manifest = json.loads(manifest_path.read_bytes())
            if not any(Path(a['path']).resolve() == args.binary.resolve() and a['sha256'] == binary['sha256']
                       for a in manifest['artifacts'].values()):
                raise ValueError('candidate manifest does not bind artifact')
            for name in FREEZE:
                if before[name] != manifest['source_files'][name]:
                    raise ValueError('frozen native source differs from candidate manifest: ' + name)
            manifest_paths.append(manifest_path)
        go_binding = json.loads(manifest_paths[0].read_bytes())
        if go_binding['source_pin'] != '07af390' or (args.role == 'go' and
            go_binding['Go_binary_sha256'] != binary['sha256']):
            raise ValueError('retained Go pin/binary binding mismatch')
        pinned = subprocess.check_output(['git', 'show', PIN + ':pkg/model/credential/credential.go'], cwd=ROOT)
        if hashlib.sha256(pinned).hexdigest() != before['pkg/model/credential/credential.go']:
            raise ValueError('credential Go source differs from pin')
        manifests = [identity(p) for p in manifest_paths]
        # build=False checks the prepared helper and performs its normal capability
        # proof before capture. Captures themselves also prohibit helper builds.
        helper = p7.spawn_helper(build=False)
        report.update(sources_before=before, binary_before=binary, helper_before=helper,
                      manifests_before=manifests, pinned_credential_sha256=hashlib.sha256(pinned).hexdigest())
        def check():
            if sources() != before or p7.phase1.binary_identity(args.binary) != binary:
                raise ValueError('source or binary drift during SOURCE FREEZE')
            if p7.spawn_helper(build=False, probe=False) != helper:
                raise ValueError('helper drift')
            if [identity(m['path']) for m in manifests] != manifests:
                raise ValueError('manifest drift')
        for label, case in cases():
            if label not in args.cases:
                continue
            check()
            report['status'] = 'capturing-' + label
            write(report_path, report)
            output = args.out / label
            actual = p7.sample(str(args.binary), case, output, execute_fn=execute)
            raw_frames(actual, output / 'raw-frames.json')
            row = {'case': label, 'case_sha256': p7.digest(case), 'validation_issues': p7.validate(actual),
                   'causal': causal(actual, label), 'failure': actual['failure'],
                   'termination': actual['termination'], 'termios_restored': actual['termios_restored'],
                   'capture_files': [identity(p) for p in sorted(output.iterdir())]}
            if actual['binary'] != binary or actual['metrics']['native_spawn']['sha256'] != helper['sha256']:
                raise ValueError('capture binary/helper binding drift')
            if args.compare_go:
                reference_path = args.compare_go / label / 'session.json'
                row['Go_reference'] = identity(reference_path)
                go = p7.load_session(reference_path)
                row['strict_differential'] = strict_compare(go, actual)
                row['Go_causal'] = causal(go, label)
            report['sessions'].append(row)
            write(report_path, report)
            check()
            print(label, row['causal']['status'], row['causal'].get('observed_before_after_before_own_reverse'),
                  row['validation_issues'], flush=True)
        report['status'] = 'completed'
    except Exception as error:
        report.update(status='failed', error={'type': type(error).__name__, 'message': str(error)})
        raise
    finally:
        if before is not None:
            report['sources_after'] = sources()
            report['sources_unchanged'] = before == report['sources_after']
        if binary is not None:
            report['binary_after'] = p7.phase1.binary_identity(args.binary)
        if helper is not None:
            report['helper_after'] = p7.spawn_helper(build=False, probe=False)
        if manifests is not None:
            report['manifests_after'] = [identity(m['path']) for m in manifests]
        report['identity_unchanged'] = (report.get('sources_unchanged', False) and
            report.get('binary_after') == binary and report.get('helper_after') == helper and
            report.get('manifests_after') == manifests)
        if not report['identity_unchanged']:
            report['status'] = 'failed'
        write(report_path, report)
        write(args.out / 'sealed.json', {'report': identity(report_path),
            'captures': [row['capture_files'] for row in report['sessions']],
            'SOURCE FREEZE': report['identity_unchanged']})
    return int(report['status'] != 'completed' or any(r['validation_issues'] or
                   r['causal']['status'] != 'bounded' for r in report['sessions']))


if __name__ == '__main__':
    raise SystemExit(main())
