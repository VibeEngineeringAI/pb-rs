#!/usr/bin/env python3
"""Pure artifact/mutation tests for Left redraw readiness; never spawns a CLI."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('credential_left_controls', HERE / 'credential-blink-process.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
ORIGINAL = Path('/home/ajs/.cache/pb-phase7/release5-focused')
OUT = Path('/home/ajs/.cache/pb-phase7/credential-blink-controls/release5-left-readiness')


def artifacts():
    return {role: c.p7.load_session(ORIGINAL / ('credential-' + role + '-ac') / 'A/session.json')
            for role in ('go', 'candidate')}


def proof():
    sessions = artifacts()
    result = {'kind': 'raw replay of original A Left/cancel-ready; no normalization', 'roles': {}}
    moved = {}
    for role, session in sessions.items():
        left = next(a for a in session['actions'] if a.get('input_hex') == '1b5b44')
        checkpoint = next(f for f in session['frames'] if f['name'] == 'cancel-ready')
        ctrlc = next(a for a in session['actions'] if a.get('input_hex') == '03')
        replay = list(c.captured_chunk_frames(session))
        retained_raw = json.loads((ORIGINAL / ('credential-' + role + '-ac') / 'A/raw-frames.json').read_bytes())
        assert replay == retained_raw['frames']
        redraw = next(f for f in replay if f['monotonic_ns'] > left['dispatch_monotonic_ns']
                      and c.left_moved_caret(f['frame']))
        moved[role] = redraw['frame']
        result['roles'][role] = {'session': c.identity(ORIGINAL / ('credential-' + role + '-ac') / 'A/session.json'),
            'Left_action': left, 'cancel_ready': checkpoint, 'CtrlC_action': ctrlc,
            'first_Left_moved_raw_chunk': redraw,
            'redraw_after_dispatch_ms': (redraw['monotonic_ns'] - left['dispatch_monotonic_ns']) / 1e6,
            'redraw_after_checkpoint_ms': (redraw['monotonic_ns'] - checkpoint['ready_monotonic_ns']) / 1e6,
            'redraw_before_CtrlC': redraw['monotonic_ns'] < ctrlc['dispatch_monotonic_ns'],
            'cancel_causal': c.cancel_ready_causal(session)}
    result['moved_redraw_exact_differences'] = c.differences(moved['go'], moved['candidate'])
    result['original_final_frame_differences'] = c.differences(sessions['go']['final_frame'], sessions['candidate']['final_frame'])
    return result


class LeftTests(unittest.TestCase):
    def setUp(self):
        self.sessions = artifacts()
        self.good = copy.deepcopy(self.sessions['candidate'])
        self.checkpoint = next(f for f in self.good['frames'] if f['name'] == 'cancel-ready')
        self.left = next(a for a in self.good['actions'] if a.get('input_hex') == '1b5b44')

    def test_raw_replay_proves_stale_Go_checkpoint(self):
        p = proof()
        self.assertGreater(p['roles']['go']['redraw_after_checkpoint_ms'], 0)
        self.assertTrue(p['roles']['go']['redraw_before_CtrlC'])
        self.assertEqual(p['roles']['go']['cancel_causal']['status'], 'unknown')
        self.assertEqual(p['roles']['candidate']['cancel_causal']['status'], 'bounded')
        self.assertEqual(p['moved_redraw_exact_differences'], [])
        self.assertEqual(p['original_final_frame_differences'], [])

    def test_old_trailing_blank_caret_is_rejected(self):
        stale = next(f for f in self.sessions['go']['frames'] if f['name'] == 'cancel-ready')
        self.assertFalse(c.left_moved_caret(stale['frame']))
        self.checkpoint['frame'] = stale['frame']
        self.assertEqual(c.cancel_ready_causal(self.good)['status'], 'unknown')

    def test_zero_new_output_is_rejected(self):
        self.checkpoint['bytes_since_action'] = 0
        self.assertEqual(c.cancel_ready_causal(self.good)['status'], 'unknown')

    def test_output_before_actual_dispatch_is_rejected(self):
        self.checkpoint['last_output_monotonic_ns'] = self.left['dispatch_monotonic_ns'] - 1
        self.assertEqual(c.cancel_ready_causal(self.good)['status'], 'unknown')

    def test_checkpoint_before_actual_dispatch_is_rejected(self):
        self.checkpoint['ready_monotonic_ns'] = self.left['dispatch_monotonic_ns'] - 1
        self.assertEqual(c.cancel_ready_causal(self.good)['status'], 'unknown')

    def test_redraw_after_checkpoint_is_rejected(self):
        for chunk in self.good['stdout_chunks']:
            if chunk['monotonic_ns'] > self.left['dispatch_monotonic_ns']:
                chunk['monotonic_ns'] = self.checkpoint['ready_monotonic_ns'] + 1
        self.assertEqual(c.cancel_ready_causal(self.good)['status'], 'unknown')

    def test_style_changes_are_preserved_and_unwitnessed_frame_rejected(self):
        original = copy.deepcopy(self.checkpoint['frame'])
        self.checkpoint['frame']['cells'][1][19][1] = 'abcdef'
        diff = c.differences(original, self.checkpoint['frame'])
        self.assertEqual(len(diff), 1)
        self.assertEqual(diff[0]['candidate'], self.checkpoint['frame']['cells'][1][19])
        self.assertEqual(c.cancel_ready_causal(self.good)['status'], 'unknown')

    def test_readiness_requires_moved_caret_and_new_output(self):
        stale = next(f for f in self.sessions['go']['frames'] if f['name'] == 'cancel-ready')['frame']
        class Terminal:
            byte_offset = 100
            alternate = False
            current = stale
            def text(self):
                return 'pass: ' + '•' * 14
            def frame(self):
                return self.current
        terminal = Terminal()
        def fake_execute(*args, **kwargs):
            self.assertFalse(kwargs['spawn_helper_build'])
            ready = kwargs['readiness_fn']
            self.assertTrue(ready(terminal, {'set_output_anchor': 'left'}, [], {}, 0, 100))
            wait = {'left_moved_caret_rendered': True, 'output_after_anchor': 'left'}
            terminal.byte_offset = 200
            self.assertFalse(ready(terminal, wait, [], {}, 0, 200))
            terminal.current = self.checkpoint['frame']
            terminal.byte_offset = 100
            self.assertFalse(ready(terminal, wait, [], {}, 0, 200))
            terminal.byte_offset = 200
            self.assertTrue(ready(terminal, wait, [], {}, 0, 200))
        with patch.object(c.p7, 'execute', fake_execute):
            c.execute()


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LeftTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    c.write(OUT / 'original-raw-proof.json', proof())
    c.write(OUT / 'pure-tests.json', {'status': 'passed' if result.wasSuccessful() else 'failed',
        'tests_run': result.testsRun, 'CLI_sessions': 0,
        'failures': len(result.failures), 'errors': len(result.errors),
        'test_source': c.identity(__file__), 'runner': c.identity(HERE / 'credential-blink-process.py')})
    raise SystemExit(not result.wasSuccessful())
