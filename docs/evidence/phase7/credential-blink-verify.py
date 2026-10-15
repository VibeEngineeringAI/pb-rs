#!/usr/bin/env python3
"""Offline verification of retained controls and analysis fail-closed behavior.

No CLI sessions or builds. Run in the phase7 harness Python environment.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('credential_controls', HERE / 'credential-blink-process.py')
controls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controls)
CACHE = Path('/home/ajs/.cache/pb-phase7/credential-blink-controls')


def main():
    reports, checks, count, native = {}, [], 0, None
    for name in ('go-r1', 'release4-r1', 'go-r2', 'release4-r2'):
        folder = CACHE / name
        report = json.loads((folder / 'report.json').read_bytes())
        sealed = json.loads((folder / 'sealed.json').read_bytes())
        assert controls.identity(folder / 'report.json') == sealed['report']
        assert report['status'] == 'completed' and report['identity_unchanged']
        assert report['sources_before'] == report['sources_after']
        assert report['helper_before'] == report['helper_after']
        assert report['binary_before'] == report['binary_after']
        assert report['manifests_before'] == report['manifests_after']
        frozen = {f: report['sources_before'][f] for f in controls.FREEZE}
        if native is None:
            native = frozen
        assert frozen == native
        archived_runner = HERE / ('credential-blink-process-r1.py' if name.endswith('r1')
                                  else 'credential-blink-process.py')
        assert controls.sha(archived_runner) == report['sources_before'][
            'docs/evidence/phase7/credential-blink-process.py']
        for row in report['sessions']:
            count += 1
            assert not row['validation_issues'] and row['causal']['status'] == 'bounded'
            for file in row['capture_files']:
                assert controls.identity(file['path']) == file
            path = folder / row['case'] / 'session.json'
            session = controls.p7.load_session(path)
            assert session['metrics']['observed_process_wall_ms'] < 15000
            assert session['metrics']['native_spawn']['sha256'] == report['helper_before']['sha256']
            assert session['termination'] == {'exit': 0} and session['termios_restored']
            assert session['failure'] is None and not session['native_cleanup_issues']
            raw = json.loads((path.parent / 'raw-frames.json').read_bytes())
            assert len(raw['frames']) == len(session['stdout_chunks'])
            # Checkpoints must be exact states observed from raw captured chunks.
            for checkpoint in session['frames']:
                assert any(f['frame'] == checkpoint['frame'] for f in raw['frames'])
            assert raw['frames'][-1]['frame'] == session['final_frame']
            if 'strict_differential' in row:
                diff = row['strict_differential']
                assert diff['case_equal'] and diff['checkpoint_names_equal']
                assert all(diff['equal_contract_fields'].values())
                assert diff['input_effects_equal'] and diff['ordered_request_contract_equal']
                assert diff['final_frame_differences'] == []
                names = {n for n, cells in diff['checkpoint_differences'].items() if cells}
                expected = set() if row['case'] == 'C' else {
                    'after-old-timer', 'before-own-deadline', 'after-collision-rearm'}
                if name == 'release4-r1' and row['case'] == 'B':
                    expected.add('password-focused')
                assert names == expected
                for checkpoint in expected - {'password-focused'}:
                    cells = diff['checkpoint_differences'][checkpoint]
                    assert len(cells) == 1
                    assert (cells[0]['field'], cells[0]['row_zero_based'],
                            cells[0]['column_zero_based']) == ('cells', 1, 20)
            checks.append(name + '/' + row['case'] + ': identity, raw frames, lifecycle, strict differential')
        reports[name] = controls.identity(folder / 'report.json')
    assert count == 8
    sample = controls.p7.load_session(CACHE / 'go-r2/B/session.json')
    assert controls.causal(sample, 'B')['early_toggle_observed']
    altered = copy.deepcopy(sample)
    late = next(f for f in altered['frames'] if f['name'] == 'after-old-timer')
    password = next(a for a in altered['actions'] if a.get('input_hex') == b'password'.hex())
    late['ready_monotonic_ns'] = password['dispatch_monotonic_ns'] + 600_000_000
    unknown = controls.causal(altered, 'B')
    assert unknown['status'] == 'unknown' and not unknown['early_toggle_observed']
    checks.append('Sample after own deadline cannot establish departed-timer collision: unknown')
    altered = copy.deepcopy(sample)
    first = next(f for f in altered['frames'] if f['name'] == 'password-first-chunk')
    first['bytes_since_action'] = 0
    assert controls.causal(altered, 'B')['status'] == 'unknown'
    checks.append('Missing intermediate chunk rendering cannot establish segmentation: unknown')
    frame = sample['frames'][0]['frame']
    styled = copy.deepcopy(frame)
    styled['cells'][0][0][1] = 'abcdef'
    diff = controls.differences(frame, styled)
    assert len(diff) == 1 and diff[0]['candidate'] == styled['cells'][0][0]
    checks.append('Strict comparator preserves changed foreground and complete cell attributes')
    output = {'status': 'passed', 'actual_CLI_sessions': count, 'additional_CLI_sessions': 0,
              'reports': reports, 'frozen_native_hashes': native, 'checks': checks,
              'verifier': controls.identity(__file__)}
    controls.write(HERE / 'credential-blink-verification.json', output)
    print('PASS:', len(checks), 'offline checks; 8 retained CLI sessions; zero new sessions')


if __name__ == '__main__':
    main()
