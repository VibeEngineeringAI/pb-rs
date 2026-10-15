"""Terminal semantics, causal process capture, independent effects and strict gates."""
import copy
import errno
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock
import phase7 as p7
import phase7_workloads as workloads


class TerminalTests(unittest.TestCase):
    def test_wide_legend_loading_opt_in_is_exactly_both_named_checkpoints(self):
        cases = {c['name']: c for c in workloads.corpus(themes=('light', 'dark'))}
        light = copy.deepcopy(cases['p7-promql-wide-legend-view-panic-light'])
        dark = copy.deepcopy(cases['p7-promql-wide-legend-view-panic-dark'])
        self.assertEqual([c['name'] for c in cases.values()
                          if 'before-view-panic' in c.get('loading_spinner_checkpoints', [])],
                         [light['name'], dark['name']])
        self.assertEqual(light.pop('loading_spinner_checkpoints'), ['before-view-panic'])
        self.assertEqual(dark.pop('loading_spinner_checkpoints'), ['before-view-panic'])
        for case in (light, dark):
            case.pop('name')
            case.pop('theme')
        self.assertEqual(light, dark)  # Identical panic/actions/effects and lifecycle gates.

    def test_wide_legend_loading_phase_keeps_frame_panic_and_lifecycle_strict(self):
        case = next(c for c in workloads.corpus(themes=('light',))
                    if c['name'] == 'p7-promql-wide-legend-view-panic-light')
        terminal = p7.Terminal(*case['size'])
        terminal.feed(b'\x1b[?1049h\x1b[?25l\x1b[22;40H\x1b[38;2;58;58;140m\\ fetching...')
        frame = terminal.frame()
        panic = '\n'.join(case['expect']['stdout_contains']).encode()
        left = {'case': case, 'failure': None, 'termination': {'exit': 0},
                'termios_restored': True, 'termios_before': [], 'termios_after': [],
                'config_before': {}, 'config_after': {}, 'replacements': {},
                'requests': [dict(zip(('method', 'path'), member.split(' ', 1)))
                             for member in case['initial_fanout_members']],
                'stdout': panic, 'stderr': b'', 'post_screen_stdout': panic,
                'frames': [{'name': 'before-view-panic', 'frame': frame}],
                'final_frame': p7.Terminal(*case['size']).frame(),
                'transitions': [{'kind': 'before-alt-exit', 'frame': copy.deepcopy(frame)}]}
        right = copy.deepcopy(left)
        right['frames'][0]['frame']['cells'][21][39][0] = '-'
        self.assertEqual(p7.compare(left, right), [])
        for fault in ('fg', 'bg', 'attribute', 'position', 'label', 'cursor', 'scrollback',
                      'other-checkpoint', 'final-frame', 'exit', 'restore', 'panic', 'failed-view'):
            with self.subTest(fault=fault):
                changed = copy.deepcopy(right)
                f = changed['frames'][0]['frame']
                cell = f['cells'][21][39]
                if fault == 'fg': cell[1] = '00ff00'
                elif fault == 'bg': cell[2] = 'ff0000'
                elif fault == 'attribute': cell[3] = True
                elif fault == 'position': f['cells'][21][38][0], cell[0] = cell[0], ' '
                elif fault == 'label': f['cells'][21][41][0] = 'X'
                elif fault == 'cursor': f['cursor']['visible'] = True
                elif fault == 'scrollback': f['scrollback'].append(copy.deepcopy(f['cells'][0]))
                elif fault == 'other-checkpoint': changed['frames'][0]['name'] = 'other'
                elif fault == 'final-frame': changed['final_frame']['cells'][0][0][0] = '-'
                elif fault == 'exit': changed['termination'] = {'exit': 1}
                elif fault == 'restore': changed['termios_restored'] = False
                elif fault == 'panic': changed['stdout'] = b'Caught panic: different failure'
                elif fault == 'failed-view':
                    for cell in changed['transitions'][0]['frame']['cells'][0][:20]: cell[0] = '测'
                self.assertTrue(p7.compare(left, changed))
        dark = next(c for c in workloads.corpus(themes=('dark',))
                    if c['name'] == 'p7-promql-wide-legend-view-panic-dark')
        left['case'] = right['case'] = dark
        self.assertEqual(p7.compare(left, right), [])
        dark.pop('loading_spinner_checkpoints')
        self.assertEqual(p7.compare(left, right),
                         ['styled cells/cursor/scrollback differ at before-view-panic'])

    def test_loading_phase_exception_preserves_every_other_cell_field(self):
        terminal = p7.Terminal(30, 3)
        terminal.feed(b'\x1b[?1049h\x1b[31m \\ fetching...')
        frame = terminal.frame()
        case = {'loading_spinner_checkpoints': ['pending']}
        canonical = p7.checkpoint_frame(frame, {}, case, 'pending')
        self.assertEqual(canonical['cells'][0][1][0], '|')
        self.assertEqual(canonical['cells'][0][1][1:], frame['cells'][0][1][1:])
        self.assertEqual(canonical['cursor'], frame['cursor'])
        self.assertEqual(canonical['scrollback'], frame['scrollback'])
        self.assertEqual(p7.checkpoint_frame(frame, {}, case, 'finished'), frame)
        self.assertEqual(p7.checkpoint_frame(frame, {}, {}, 'pending'), frame)
        for glyph in '|/-\\':
            changed = copy.deepcopy(frame)
            changed['cells'][0][1][0] = glyph
            self.assertEqual(p7.checkpoint_frame(changed, {}, case, 'pending'), canonical)
        changed['cells'][0][1][1] = '00ff00'
        self.assertNotEqual(p7.checkpoint_frame(changed, {}, case, 'pending'), canonical)
        changed['cells'][0][1][0] = 'X'
        self.assertFalse(p7.loading_spinner_cells(changed))
        changed = copy.deepcopy(frame)
        changed['cells'][0][4][0] = 'X'
        self.assertFalse(p7.loading_spinner_cells(changed))
        changed = copy.deepcopy(frame)
        changed['alternate'] = False
        self.assertEqual(p7.checkpoint_frame(changed, {}, case, 'pending'), changed)
        changed = copy.deepcopy(frame)
        changed['cells'][1] = copy.deepcopy(frame['cells'][0])
        self.assertEqual(p7.checkpoint_frame(changed, {}, case, 'pending'), changed)
        changed = copy.deepcopy(frame)
        changed['scrollback'] = [copy.deepcopy(frame['cells'][0])]
        self.assertEqual(p7.checkpoint_frame(changed, {}, case, 'pending')['scrollback'], changed['scrollback'])

    def test_fragmented_utf8_and_truecolor_attributes(self):
        terminal = p7.Terminal(20, 4)
        payload = '\x1b[38;2;1;2;3;48;5;21;1;3;4;7;9;2;8;53m日志é'.encode()
        for byte in payload:
            terminal.feed(bytes([byte]))
        cells = terminal.frame()['cells'][0]
        self.assertEqual(cells[0][:3], ['日', '010203', '0000ff'])
        self.assertEqual(cells[1][0], '')  # Wide-cell continuation is preserved.
        self.assertEqual(cells[2][0], '志')
        self.assertEqual(cells[4][0], 'é')
        self.assertTrue(all(cells[0][index] for index in (3, 4, 5, 6, 7, 9, 10, 11)))
        self.assertFalse(terminal.unsupported)

    def test_attributes_reset_and_erasure_preserve_background(self):
        terminal = p7.Terminal(6, 2)
        terminal.feed(b'\x1b[2;8;53mA\x1b[0mB\x1b[48;2;4;5;6m\x1b[K')
        cells = terminal.frame()['cells'][0]
        self.assertEqual(cells[1][9:], [False, False, False])
        self.assertEqual(cells[2][2], '040506')

    def test_ansi_and_indexed_colors_are_equivalent(self):
        for index in range(16):
            a, b = p7.Terminal(5, 2), p7.Terminal(5, 2)
            ansi = 30 + index if index < 8 else 90 + index - 8
            a.feed(f'\x1b[{ansi}mX'.encode())
            b.feed(f'\x1b[38;5;{index}mX'.encode())
            self.assertEqual(a.frame(), b.frame())

    def test_alt_screen_restores_main_cursor_and_scrollback(self):
        terminal = p7.Terminal(12, 3)
        terminal.feed(b'one\r\ntwo\r\nthree\r\nfour')
        before = terminal.frame()
        terminal.feed(b'\x1b[?1049h\x1b[2J\x1b[H\x1b[?25lalt')
        self.assertTrue(terminal.frame()['alternate'])
        self.assertFalse(terminal.frame()['cursor']['visible'])
        self.assertEqual(terminal.frame()['scrollback'], before['scrollback'])
        terminal.feed(b'\x1b[?25h\x1b[?1049l')
        self.assertEqual(terminal.frame(), before)
        self.assertEqual(terminal.transitions[-2]['kind'], 'before-alt-exit')

    def test_resize_and_scrollback_capture_styled_rows(self):
        terminal = p7.Terminal(8, 2)
        terminal.feed(b'\x1b[31mA\r\nB\r\nC')
        self.assertEqual(terminal.frame()['scrollback'][0][0][1], 'cd0000')
        terminal.resize(15, 4)
        self.assertEqual(terminal.frame()['columns'], 15)
        self.assertEqual(len(terminal.frame()['cells']), 4)

    def test_terminal_query_responses_are_separate_input_evidence(self):
        responses = []
        terminal = p7.Terminal(10, 4, 'light', responses.append)
        terminal.feed(b'abc\x1b[6n\x1b]11;?\x1b\\\x1b[c')
        self.assertEqual(responses, [b'\x1b[1;4R', b'\x1b]11;rgb:ffff/ffff/ffff\x1b\\', b'\x1b[?1;2c'])

    def test_unsupported_controls_cannot_silently_pass(self):
        terminal = p7.Terminal()
        terminal.feed(b'\x1b[?9999h\x1b[6m\x1bPunsupported\x1b\\')
        self.assertEqual(len(terminal.unsupported), 3)

    def test_cursor_shape_and_visibility_are_cells_contract(self):
        terminal = p7.Terminal()
        terminal.feed(b'\x1b[5 q\x1b[?25l')
        self.assertEqual(terminal.frame()['cursor']['shape'], 5)
        self.assertFalse(terminal.frame()['cursor']['visible'])

    def test_known_fixture_origin_normalization_preserves_style_and_width(self):
        left, right = p7.Terminal(30, 3), p7.Terminal(30, 3)
        left.feed(b'\x1b[31mhttp://127.0.0.1:12345')
        right.feed(b'\x1b[31mhttp://127.0.0.1:54321')
        a = p7.normalized_frame(left.frame(), {'http://127.0.0.1:12345': '<URL>'})
        b = p7.normalized_frame(right.frame(), {'http://127.0.0.1:54321': '<URL>'})
        self.assertEqual(a, b)
        right.feed(b'\x1b[H\x1b[32mhttp://127.0.0.1:54321')
        self.assertNotEqual(a, p7.normalized_frame(right.frame(), {'http://127.0.0.1:54321': '<URL>'}))


class DispatchOriginTests(unittest.TestCase):
    """Drive the real action loop with inert I/O/native objects and a logical clock."""
    def capture(self, action, work_ns, *, resize_signal_policy=None):
        start, delivery = 1_000_000_000, 5_000_000
        packages = p7.dependencies()
        clock = {'now': start, 'observing': False, 'started': False, 'polls': 0}
        work, dispatches, mapping, group_signals = [], [], {}, []
        frame_fn, resize_fn, digest_fn = p7.Terminal.frame, p7.Terminal.resize, p7.digest
        def spend(kind, multiplier=1):
            if clock['observing']:
                clock['now'] += work_ns * multiplier
                work.append((kind, clock['now']))
        def frame(terminal):
            result = frame_fn(terminal)
            spend('frame')
            return result
        def resize(terminal, width, height):
            resize_fn(terminal, width, height)
            spend('terminal-resize', 4)
        def fingerprint(value):
            result = digest_fn(value)
            spend('digest')
            return result
        def ready(terminal, *args):
            clock['observing'] = True
            spend('readiness', 2)
            copy.deepcopy((terminal.main, terminal.alt))
            spend('deepcopy', 3)
            terminal.frame()
            return True
        def dispatch(kind):
            dispatches.append((kind, clock['now'], len(work)))
            clock['observing'] = False
            clock['now'] += delivery
        def write(fd, payload):
            self.assertEqual(fd, 10)
            dispatch('input')
            return len(payload)
        def size(fd, width, height):
            if clock['started']:
                dispatch('resize')
        def group_signal(number):
            group_signals.append(number)
            if number == signal.SIGTERM:
                dispatch('signal')
            return True
        def poll():
            clock['polls'] += 1
            if clock['polls'] == 3:
                mapping.clear()
                clock['now'] = start + 300_000_000
                return {'exited_monotonic_ns': start + 200_000_000}
        usage = {'status': 0, 'reaped_monotonic_ns': start + 210_000_000,
                 'ru_utime': .01, 'ru_stime': .002, 'ru_maxrss': 4,
                 'ru_minflt': 7, 'ru_majflt': 1, 'ru_nvcsw': 3, 'ru_nivcsw': 2,
                 'ru_inblock': 1, 'ru_oublock': 0}
        native = p7.SimpleNamespace(pid=42, helper_pid=43, exec_error=None,
            initial={'started_monotonic_ns': start}, poll=poll, same_process=lambda: False,
            signal_group=group_signal, release=lambda: None, wait=lambda: usage,
            close=lambda abort=False: [])
        def child(*args):
            clock['started'] = True
            return native
        selector = mock.MagicMock()
        selector.__enter__.return_value = selector
        selector.register.side_effect = lambda fd, *args: mapping.update({fd: True})
        selector.get_map.side_effect = lambda: mapping
        selector.select.return_value = []
        case = {'size': [6, 3], 'initial_screen': 'READY', 'actions': [
            {'checkpoint': 'before', 'stable_ms': 0, **action},
            {'checkpoint': 'after', 'stable_ms': 0}]}
        with p7.ExitStack() as patches:
            replacements = [(p7, 'dependencies', lambda: packages),
                (p7, 'spawn_helper', lambda **kwargs: {'path': '/inert/helper', 'sha256': 'fixture'}),
                (p7, 'NativeChild', child), (p7, 'termios_state', lambda fd: {'fixture': True}),
                (p7, 'winsize', size), (p7, 'clipboard_events', lambda env: []),
                (p7, 'digest', fingerprint), (p7.time, 'monotonic_ns', lambda: clock['now']),
                (p7.os, 'openpty', lambda: (10, 11)), (p7.os, 'pipe', lambda: (12, 13)),
                (p7.os, 'set_blocking', lambda *args: None), (p7.os, 'close', lambda fd: None),
                (p7.os, 'write', write), (p7.os, 'sysconf', lambda key: 100),
                (p7.selectors, 'DefaultSelector', lambda: selector),
                (p7.Terminal, 'frame', frame), (p7.Terminal, 'resize', resize)]
            for owner, name, replacement in replacements:
                patches.enter_context(mock.patch.object(owner, name, replacement))
            patches.enter_context(mock.patch.object(p7.os, 'read', side_effect=AssertionError('no real I/O')))
            result = p7.execute(['inert'], {}, '/inert', case, readiness_fn=ready,
                proc_sampler=lambda pid: None, spawn_helper_build=False,
                observer_config={'record_termios': False, 'record_chunks': False, 'record_descendants': False},
                **({} if resize_signal_policy is None else {'resize_signal_policy': resize_signal_policy}))
        self.assertIsNone(result['failure'])
        self.assertEqual(result['termination'], {'exit': 0})
        event, anchor = result['actions']
        self.assertEqual(event['monotonic_ns'], start)
        self.assertEqual(event['evaluated_monotonic_ns'], start)
        self.assertEqual(result['frames'][0]['ready_monotonic_ns'], start)
        self.assertEqual(result['frames'][1]['action_started_monotonic_ns'], start)
        self.assertNotIn('dispatch_monotonic_ns', anchor)
        self.assertEqual([result['frames'][0]['frame'][k] for k in ('columns', 'rows')], [6, 3])
        self.assertEqual(event['dispatch_monotonic_ns'], dispatches[0][1])
        self.assertEqual(result['frames'][1]['first_useful_monotonic_ns'] - event['dispatch_monotonic_ns'], delivery)
        prior_work = work[:dispatches[0][2]]
        self.assertTrue({'readiness', 'deepcopy', 'frame', 'digest'} <= {kind for kind, _ in prior_work})
        self.assertTrue(all(done <= event['dispatch_monotonic_ns'] for _, done in prior_work))
        self.last_group_signals = group_signals
        return result, prior_work

    def test_resize_policy_dispatch_source_default_noop_and_cleanup(self):
        legacy, _ = self.capture({'resize': [8, 4]}, 10_000_000)
        self.assertEqual(legacy['resize_signal_policy'], 'legacy')
        self.assertEqual(legacy['actions'][0]['resize_signal_source'], 'ioctl+explicit')
        self.assertEqual(self.last_group_signals.count(signal.SIGWINCH), 1)
        changed, work = self.capture({'resize': [8, 4]}, 10_000_000,
                                   resize_signal_policy='ioctl-only-changed')
        self.assertEqual(changed['resize_signal_policy'], 'ioctl-only-changed')
        self.assertEqual(changed['actions'][0]['resize_signal_source'], 'ioctl-only')
        self.assertEqual(changed['actions'][0]['dispatch_monotonic_ns'], work[-1][1])
        self.assertNotIn(signal.SIGWINCH, self.last_group_signals)
        self.assertIn(signal.SIGKILL, self.last_group_signals)  # Required final group cleanup unchanged.
        self.assertEqual(legacy['metrics'], changed['metrics'])
        self.assertEqual(legacy['frames'], changed['frames'])
        noop, _ = self.capture({'resize': [6, 3]}, 0)
        self.assertEqual(noop['actions'][0]['resize_signal_source'], 'ioctl+explicit')
        self.assertEqual(self.last_group_signals.count(signal.SIGWINCH), 1)

    def test_key_dispatch_origin_excludes_readiness_frame_and_deepcopy_work(self):
        for action in ({'key': 'right'}, {'send': '日志'}):
            with self.subTest(action=action):
                fast, _ = self.capture(action, 0)
                slow, _ = self.capture(action, 10_000_000)
                self.assertGreater(slow['actions'][0]['dispatch_monotonic_ns'], fast['actions'][0]['dispatch_monotonic_ns'])
                self.assertEqual(slow['metrics'], fast['metrics'])
                self.assertEqual(slow['actions'][0]['input_hex'], fast['actions'][0]['input_hex'])

    def test_resize_dispatch_origin_also_excludes_emulator_resize_work(self):
        fast, _ = self.capture({'resize': [8, 4]}, 0)
        slow, work = self.capture({'resize': [8, 4]}, 10_000_000)
        self.assertEqual(work[-1][0], 'terminal-resize')
        self.assertEqual(work[-1][1], slow['actions'][0]['dispatch_monotonic_ns'])
        self.assertEqual([slow['frames'][1]['frame'][k] for k in ('columns', 'rows')], [8, 4])
        self.assertGreater(slow['actions'][0]['dispatch_monotonic_ns'], fast['actions'][0]['dispatch_monotonic_ns'])
        self.assertEqual(slow['metrics'], fast['metrics'])

    def test_signal_dispatch_origin_retains_evaluation_and_resource_metrics(self):
        fast, _ = self.capture({'signal': 'SIGTERM'}, 0)
        slow, _ = self.capture({'signal': 'SIGTERM'}, 10_000_000)
        self.assertEqual(slow['actions'][0]['signal'], signal.SIGTERM)
        self.assertGreater(slow['actions'][0]['dispatch_monotonic_ns'], fast['actions'][0]['dispatch_monotonic_ns'])
        self.assertEqual(slow['metrics'], fast['metrics'])


class ResizePolicyValidationTests(unittest.TestCase):
    def reject(self, case, message='dimensions|unchanged', policy='ioctl-only-changed'):
        with mock.patch.object(p7, 'dependencies', side_effect=AssertionError('before dependencies')), \
                mock.patch.object(p7, 'spawn_helper', side_effect=AssertionError('before helper/probe')), \
                mock.patch.object(p7.os, 'openpty', side_effect=AssertionError('before PTY')), \
                mock.patch.object(p7, 'NativeChild', side_effect=AssertionError('before CLI')):
            with self.assertRaisesRegex(ValueError, message):
                p7.execute(['never-run'], {}, '/inert', case, resize_signal_policy=policy)

    def test_initial_and_all_late_resize_geometries_reject_before_acquisition(self):
        bad = (None, [], [1], [1, 2, 3], [True, 2], [2, False], [1.5, 2], ['2', 2],
               [0, 2], [2, -1], [65536, 2], [2, 65536])
        for value in bad:
            with self.subTest(value=value):
                self.reject({'size': value, 'actions': []})
                self.reject({'size': [6, 3], 'actions': [{'resize': [8, 4]}, {'key': 'right'},
                                                       {'resize': value}]})
        self.reject({'size': [6, 3], 'actions': [{'resize': [6, 3]}]})
        self.reject({'size': [6, 3], 'actions': [{'resize': [8, 4]}, {'resize': [8, 4]}]})

    def test_valid_grow_shrink_and_unsigned16_boundaries_reach_only_helper_boundary(self):
        for dimensions in (([6, 3], [[8, 4], [6, 3]]), ([1, 1], [[65535, 65535], [1, 1]])):
            with self.subTest(dimensions=dimensions), mock.patch.object(p7, 'dependencies'), \
                    mock.patch.object(p7, 'spawn_helper', side_effect=RuntimeError('helper boundary')):
                with self.assertRaisesRegex(RuntimeError, 'helper boundary'):
                    p7.execute(['inert'], {}, '/inert',
                        {'size': tuple(dimensions[0]), 'actions': [{'resize': tuple(d)} for d in dimensions[1]]},
                        resize_signal_policy='ioctl-only-changed')

    def test_unknown_policy_rejects_early_and_unrelated_actions_are_not_prevalidated(self):
        for policy in ('unknown', None, False, []):
            self.reject({'actions': []}, 'resize_signal_policy', policy)
        with mock.patch.object(p7, 'dependencies'), \
                mock.patch.object(p7, 'spawn_helper', side_effect=RuntimeError('helper boundary')):
            for policy in ('legacy', 'ioctl-only-changed'):
                with self.assertRaisesRegex(RuntimeError, 'helper boundary'):
                    p7.execute(['inert'], {}, '/inert', {'actions': [{'key': 'unknown'}, None]},
                               resize_signal_policy=policy)
            with self.assertRaisesRegex(RuntimeError, 'helper boundary'):
                p7.execute(['inert'], {}, '/inert', {'size': [0, 3], 'actions': [{'resize': [0, 3]}]})


class ResolverTests(unittest.TestCase):
    """Inert files/cached proof: no executable, compiler or capability probe runs."""
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=p7.TMP))).resolve()
        self.root = root
        compiler = root / 'compiler'
        compiler.write_bytes(b'inert compiler fixture\n')
        compiler_alias = root / 'cc'
        compiler_alias.symlink_to(compiler)
        self.identity = {'source_sha256': p7.hashlib.sha256((p7.HERE / 'phase7_spawn.c').read_bytes()).hexdigest(),
                         'compiler': str(compiler), 'compiler_sha256': p7.hashlib.sha256(compiler.read_bytes()).hexdigest(),
                         'compiler_version': 'fixture-version',
                         'flags': ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror']}
        key = p7.digest(self.identity)
        self.binary = root / ('phase7-spawn-' + key)
        self.binary.write_bytes(b'inert helper fixture\n')
        self.binary.chmod(0o700)
        self.manifest = self.binary.with_suffix('.json')
        self.helper = {**self.identity, 'build_key': key, 'path': str(self.binary),
                       'sha256': p7.hashlib.sha256(self.binary.read_bytes()).hexdigest(), 'protocol_version': 2}
        self.manifest.write_text(json.dumps({'schema_version': 1, **self.helper}) + '\n')
        self.enterContext(mock.patch.object(p7, 'TMP', root))
        self.enterContext(mock.patch.object(p7.shutil, 'which', return_value=str(compiler_alias)))
        compiler_key = (str(compiler), self.identity['compiler_sha256'])
        self.enterContext(mock.patch.dict(p7._COMPILER_VERSIONS, {compiler_key: 'fixture-version'}, clear=True))
        self.enterContext(mock.patch.dict(p7._NATIVE_CAPABILITIES,
            {self.helper['sha256']: {'fixture_only': True}}, clear=True))
        for owner, name in ((p7.subprocess, 'run'), (p7.subprocess, 'check_output'),
                            (p7, 'native_capability_probe'), (p7.os, 'openpty'), (p7, 'NativeChild')):
            self.enterContext(mock.patch.object(owner, name, side_effect=AssertionError('forbidden fixture route: ' + name)))

    def snapshot(self):
        return {path.name: ('symlink', os.readlink(path)) if path.is_symlink() else
                ('file', path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
                for path in self.root.iterdir()}

    def test_binary_and_sidecar_aliases_reject_before_read_build_or_cli(self):
        read_bytes = Path.read_bytes
        for cached in (self.binary, self.manifest):
            original, mode = cached.read_bytes(), cached.stat().st_mode & 0o777
            for dangling in (False, True):
                with self.subTest(file=cached.name, dangling=dangling):
                    target = self.root / 'alias-target'
                    if not dangling:
                        target.write_bytes(original)
                        target.chmod(mode)
                    cached.unlink()
                    cached.symlink_to(target)
                    before = self.snapshot()
                    def checked_read(path):
                        self.assertNotEqual(path, cached, 'must not follow keyed alias')
                        return read_bytes(path)
                    with mock.patch.object(Path, 'read_bytes', autospec=True, side_effect=checked_read), \
                            mock.patch.object(p7.os, 'replace', side_effect=AssertionError('must not repair alias')):
                        for build in (False, True):
                            with self.assertRaisesRegex(RuntimeError, 'cache symlink rejected'):
                                p7.spawn_helper(build=build, probe=False)
                        with self.assertRaisesRegex(RuntimeError, 'cache symlink rejected'):
                            p7.execute(['never-run'], dict(os.environ), str(self.root),
                                       {'actions': []}, spawn_helper_build=False)
                    self.assertEqual(self.snapshot(), before)
                    cached.unlink()
                    cached.write_bytes(original)
                    cached.chmod(mode)
                    target.unlink(missing_ok=True)

    def test_regular_keyed_paths_and_resolved_compiler_are_read_only(self):
        before = self.snapshot()
        with mock.patch.object(p7.os, 'replace', side_effect=AssertionError('strict resolver must not write')):
            result = p7.spawn_helper(build=False, probe=False)
        self.assertEqual(result['path'], str(self.binary.resolve()))
        self.assertEqual(result['compiler'], str((self.root / 'cc').resolve()))
        self.assertEqual(result['provenance_path'], str(self.manifest.resolve()))
        self.assertEqual(result['sha256'], self.helper['sha256'])
        self.assertEqual(self.snapshot(), before)

    def test_normal_cache_creation_uses_canonical_compiler_and_regular_files(self):
        payload = self.binary.read_bytes()
        self.binary.unlink()
        self.manifest.unlink()
        def compile_fixture(argv, **kwargs):
            self.assertEqual(argv, [self.identity['compiler'], *self.identity['flags'],
                                   str(p7.HERE / 'phase7_spawn.c'), '-o', argv[-1]])
            self.assertTrue(kwargs['check'])
            staged = Path(argv[-1])
            staged.write_bytes(payload)
            staged.chmod(0o700)
        with mock.patch.object(p7.subprocess, 'run', side_effect=compile_fixture) as compile_call:
            result = p7.spawn_helper(build=True, probe=False)
        self.assertEqual(compile_call.call_count, 1)
        self.assertFalse(self.binary.is_symlink())
        self.assertFalse(self.manifest.is_symlink())
        self.assertEqual(result['path'], str(self.binary.resolve()))
        self.assertEqual(result['compiler'], str((self.root / 'cc').resolve()))
        self.assertEqual(p7.spawn_helper(build=False, probe=False), result)


@unittest.skipUnless(os.environ.get('PB_PHASE7_RUN_RESIZE_NATIVE') == '1',
                     'requires the root-authorized native execution window')
class ResizeNativeControlTests(unittest.TestCase):
    def test_grow_shrink_notifications_sizes_restoration_wait4_and_pid_bound_cleanup(self):
        output = Path(os.environ.get('PB_PHASE7_RESIZE_CONTROL_OUT',
            str(p7.TMP.parent / 'harness-resize-policy-r4/native-control')))
        output.mkdir(parents=True, exist_ok=False)
        report = {'kind': 'bounded own native resize functional control', 'performance_claim': False,
                  'helper': None, 'controls': []}
        def save():
            (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        save()
        worker = 'import os,signal,time;signal.signal(signal.SIGHUP,signal.SIG_IGN);os.write(1,b"C");time.sleep(20)'
        program = ('import fcntl,json,os,signal,struct,subprocess,sys,termios,tty\n'
            f'worker=subprocess.Popen([sys.executable,"-c",{worker!r}],stdout=subprocess.PIPE)\n'
            'assert worker.stdout.read(1)==b"C"\n'
            'ticks=int(open("/proc/%d/stat"%worker.pid).read().rsplit(")",1)[1].split()[19])\n'
            'os.write(2,(json.dumps({"event":"worker","pid":worker.pid,"start_ticks":ticks})+"\\n").encode())\n'
            'saved=termios.tcgetattr(0);tty.setraw(0);seen=[]\n'
            'def size():\n'
            ' rows,cols,_,_=struct.unpack("HHHH",fcntl.ioctl(0,termios.TIOCGWINSZ,b"\\0"*8));return [cols,rows]\n'
            'def paint():\n'
            ' cols,rows=size();os.write(1,("\\x1b[2J\\x1b[HREADY SIZE %dx%d WINCH %d"%(cols,rows,len(seen))).encode())\n'
            'def changed(*args):\n'
            ' seen.append(size());paint()\n'
            'signal.signal(signal.SIGWINCH,changed);paint()\n'
            'try:\n'
            ' while os.read(0,1)!=b"q": pass\n'
            'finally:\n'
            ' termios.tcsetattr(0,termios.TCSANOW,saved)\n'
            ' os.write(2,(json.dumps({"event":"final","seen":seen,"size":size()})+"\\n").encode())\n'
            'os._exit(0)\n')
        helper = p7.spawn_helper(build=False)  # Existing artifact; preparation probes, never compile.
        report['helper'] = helper
        save()
        original_signal = p7.NativeChild.signal_group
        for policy in ('ioctl-only-changed', 'legacy'):
            with self.subTest(policy=policy):
                signals, noop_baseline = [], {}
                def signal_group(native, number):
                    signals.append({'pid': native.pid, 'pidfd': native.pidfd, 'signal': number,
                                    'reserved_exit': native.exited is not None})
                    return original_signal(native, number)
                def ready(terminal, wait, trace, tty, started, now):
                    if not p7.useful(terminal, wait, trace, tty):
                        return False
                    count = int(terminal.text().split('WINCH ', 1)[1].split()[0])
                    if wait.get('remember_noop'):
                        noop_baseline['count'] = count
                    return not wait.get('fresh_noop') or count > noop_baseline['count']
                grow = 'READY SIZE 100x30' + (' WINCH 1' if policy == 'ioctl-only-changed' else '')
                shrink = 'READY SIZE 60x20' + (' WINCH 2' if policy == 'ioctl-only-changed' else '')
                actions = [workloads.ready('initial', 'READY SIZE 80x24 WINCH 0'), {'resize': [100, 30]},
                           workloads.ready('grown', grow), {'resize': [60, 20]}, workloads.ready('shrunk', shrink)]
                if policy == 'legacy':
                    actions.extend([{'resize': [60, 20], 'wait': {'contains': ['READY SIZE 60x20'],
                                     'remember_noop': True}},
                                    workloads.ready('noop-refreshed', 'READY SIZE 60x20', fresh_noop=True)])
                actions.append({'send': 'q'})
                with mock.patch.object(p7.NativeChild, 'signal_group', autospec=True, side_effect=signal_group):
                    result = p7.execute([sys.executable, '-c', program], dict(os.environ), str(p7.TMP),
                        {'size': [80, 24], 'actions': actions, 'timeout': 8}, readiness_fn=ready,
                        spawn_helper_build=False, resize_signal_policy=policy,
                        observer_config={'record_descendants': False})
                folder = output / policy
                folder.mkdir()
                for field in ('stdout', 'stderr'):
                    (folder / (field + '.bin')).write_bytes(result[field])
                serial = copy.deepcopy(result)
                for field in ('stdout', 'stderr', 'post_screen_stdout'):
                    data = serial.pop(field)
                    serial[field + '_hex'] = None if data is None else data.hex()
                (folder / 'session.json').write_text(json.dumps(serial) + '\n')
                row = {'policy': policy, 'failure': result['failure'], 'termination': result['termination'],
                       'termios_restored': result['termios_restored'], 'group_signals': signals}
                report['controls'].append(row)
                save()
                self.assertIsNone(result['failure'])
                self.assertEqual(result['termination'], {'exit': 0})
                self.assertTrue(result['termios_restored'])
                self.assertEqual(result['native_cleanup_issues'], [])
                self.assertEqual(result['metrics']['resource_source'], 'native_child_wait4')
                self.assertGreater(result['metrics']['peak_rss_bytes'], 0)
                self.assertGreaterEqual(result['native_wait4_reaped_monotonic_ns'], result['exited_monotonic_ns'])
                messages = [json.loads(line) for line in result['stderr'].splitlines()]
                final = next(m for m in messages if m['event'] == 'final')
                child = next(m for m in messages if m['event'] == 'worker')
                row.update(observed=final, worker=child)
                save()
                self.assertEqual(final['size'], [60, 20])
                if policy == 'ioctl-only-changed':
                    self.assertEqual(final['seen'], [[100, 30], [60, 20]])
                    self.assertNotIn(signal.SIGWINCH, [s['signal'] for s in signals])
                else:
                    self.assertGreaterEqual(final['seen'].count([100, 30]), 1)
                    self.assertGreaterEqual(final['seen'].count([60, 20]), 2)
                    self.assertEqual([s['signal'] for s in signals].count(signal.SIGWINCH), 3)
                cleanup = [s for s in signals if s['signal'] == signal.SIGKILL]
                self.assertTrue(cleanup)
                self.assertTrue(all(s['pidfd'] is not None and s['reserved_exit'] and
                                    s['pid'] == result['metrics']['native_spawn']['cli_pid'] for s in cleanup))
                deadline = time.monotonic() + 1
                while p7.living(child) and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(p7.living(child))  # Absent/Z; adopter reaping deadline is not asserted.
                self.assertEqual(p7.spawn_helper(build=False, probe=False), helper)
                row['worker_final_state'] = p7.child_state(child)
                row['passed'] = True
                save()


class ProcessTests(unittest.TestCase):
    def test_proc_sampler_skips_exited_missing_memory_and_exit_races(self):
        fields = ['S'] + ['1'] * 21
        live = '123 (child with ) name) ' + ' '.join(fields)
        zombie = live.replace(') S ', ') Z ')
        status = ('VmRSS: 123 kB\nVmHWM: 456 kB\n'
                  'voluntary_ctxt_switches: 2\nnonvoluntary_ctxt_switches: 3\n')
        def sample(stat_values, status_value=status):
            reads = iter(stat_values)
            def read(path, *args, **kwargs):
                if path.name == 'stat':
                    return next(reads)
                return status_value if path.name == 'status' else 'syscr: 0\n'
            with mock.patch.object(Path, 'read_text', autospec=True, side_effect=read), \
                    mock.patch.object(Path, 'iterdir', return_value=iter([])):
                return p7.proc_sample(123)
        self.assertIsNone(sample([zombie]))
        self.assertIsNone(sample([live], status.replace('VmRSS: 123 kB\n', '')))
        self.assertIsNone(sample([live], status.replace('VmHWM: 456 kB\n', '')))
        self.assertIsNone(sample([live, zombie]))
        value = sample([live, live])
        self.assertEqual(value['rss_bytes'], 123 * 1024)
        self.assertEqual(value['high_water_rss_bytes'], 456 * 1024)
        self.assertEqual(value['fd_count'], 0)  # Genuine live zero, no default.

    def test_first_expected_survives_readiness_flicker(self):
        calls, matched = 0, []
        def readiness(terminal, wait, trace, tty, action_started, now):
            nonlocal calls
            if 'READY' not in terminal.text():
                return False
            calls += 1
            qualifies = calls == 1 or calls >= 4
            if qualifies:
                matched.append(now)
            return qualifies
        result = p7.execute([sys.executable, '-c', 'import os\nos.write(1,b"READY");os.read(0,1)'],
            dict(os.environ), str(p7.TMP), {'actions': [{'checkpoint': 'ready', 'send': 'x\n'}]},
            readiness_fn=readiness)
        stamp = result['frames'][0]
        self.assertIsNone(result['failure'])
        self.assertEqual(stamp['first_useful_monotonic_ns'], matched[0])
        self.assertEqual(stamp['first_ever_match_monotonic_ns'], matched[0])
        self.assertGreater(stamp['stable_since_monotonic_ns'], matched[0])
        self.assertGreaterEqual(stamp['ready_monotonic_ns'] - stamp['stable_since_monotonic_ns'], 80_000_000)

    def test_native_boundary_excludes_large_observer_image(self):
        # Resource-isolation correctness, one synthetic process; no timing claim.
        resident = bytearray(64 * 1024 * 1024)
        resident[::4096] = b'x' * (len(resident) // 4096)
        observer = p7.proc_sample(os.getpid())['rss_bytes']
        for io in ('pipe', 'pty'):
            with mock.patch.object(p7.os, 'fork', side_effect=AssertionError('Python must not fork')):
                result = self.invoke('print("small child")', io=io)
            self.assertEqual(result['termination'], {'exit': 0})
            self.assertEqual(result['metrics']['resource_source'], 'native_child_wait4')
            self.assertLess(result['metrics']['peak_rss_bytes'], observer - 32 * 1024 * 1024)
            self.assertLess(result['metrics']['peak_rss_bytes'], 24 * 1024 * 1024)
        self.assertTrue(resident[0])

    def test_native_cli_owns_session_cwd_env_and_no_protocol_fd(self):
        result = self.invoke('import json,os\n'
            'try: os.fstat(3);protocol_open=True\n'
            'except OSError: protocol_open=False\n'
            'tty=os.open("/dev/tty",os.O_RDWR);os.close(tty)\n'
            'os.write(2,json.dumps({"pid":os.getpid(),"ppid":os.getppid(),"sid":os.getsid(0),'
            '"pgrp":os.getpgrp(),"cwd":os.getcwd(),"theme":os.getenv("PB_THEME"),'
            '"protocol_open":protocol_open}).encode())\n'
            'os.write(1,b"READY");os.read(0,1)\n',
            [{**workloads.ready('ready', 'READY'), 'send': 'x\n'}])
        child = json.loads(result['stderr'])
        native = result['metrics']['native_spawn']
        self.assertEqual(child['pid'], native['cli_pid'])
        self.assertEqual(child['ppid'], native['supervisor_pid'])
        self.assertEqual(child['sid'], child['pid'])
        self.assertEqual(child['pgrp'], child['pid'])
        self.assertEqual(child['cwd'], str(p7.TMP))
        self.assertEqual(child['theme'], 'dark')
        self.assertFalse(child['protocol_open'])
        self.assertIsNone(result['failure'])
        with self.assertRaises(ChildProcessError):
            os.waitpid(native['supervisor_pid'], os.WNOHANG)

    def test_native_exec_error_retains_cli_status_and_diagnostic(self):
        for io, cwd, command, stage in (
            ('pipe', str(p7.TMP), ['/does/not/exist'], 'exec'),
            ('pty', '/does/not/exist', [sys.executable, '-c', 'pass'], 'chdir')):
            result = p7.execute(command, dict(os.environ), cwd, {'actions': [], 'io': io})
            self.assertEqual(result['termination'], {'exit': 127})
            self.assertEqual(result['exec_error']['stage'], stage)
            self.assertEqual(result['exec_error']['errno'], errno.ENOENT)
            self.assertIn(b'phase7 exec failure:', result['stderr'])
            self.assertIsNone(result['metrics']['resource_source'])
            self.assertIsNone(result['metrics']['peak_rss_bytes'])
            self.assertIn('cli setup failure:', result['failure'])
        real = self.invoke('raise SystemExit(127)', io='pipe')
        self.assertEqual(real['termination'], {'exit': 127})
        self.assertIsNone(real['exec_error'])
        self.assertIsNone(real['failure'])
        self.assertEqual(real['metrics']['resource_source'], 'native_child_wait4')

    def test_wnowait_reserves_pid_and_group_until_release(self):
        helper = p7.spawn_helper()
        with p7.ExitStack() as stack:
            fds = p7.CaptureFDs(stack)
            fd, = fds.acquire((os.open(os.devnull, os.O_RDWR),))
            native = p7.NativeChild(helper, [helper['path'], 'signal-state'], dict(os.environ),
                                   str(p7.TMP), True, fd, fd, fd, (fd,))
            stack.callback(native.close, True)
            exited = native.wait_exited()
            fields = (Path('/proc') / str(native.pid) / 'stat').read_text().rsplit(')', 1)[1].split()
            self.assertEqual(fields[0], 'Z')
            self.assertIsNone(native.exit)
            self.assertTrue(native.signal_group(signal.SIGTERM))
            self.assertIsNone(p7.proc_sample(native.pid))
            released = time.monotonic_ns()
            native.release()
            event = native.wait()
            self.assertEqual(event['status'], 0)
            self.assertEqual(event['exited_monotonic_ns'], exited['exited_monotonic_ns'])
            self.assertLessEqual(event['exited_monotonic_ns'], released)
            self.assertGreaterEqual(event['reaped_monotonic_ns'], released)
            self.assertFalse((Path('/proc') / str(native.pid)).exists())
            self.assertEqual(native.close(), [])

    def test_exited_during_signal_and_deadline_dispatch_retains_evidence(self):
        original = p7.NativeChild.poll
        for kind in ('signal', 'deadline'):
            hold, observed = [True], {}
            def delayed(native):
                event = original(native)
                return None if native.pid is not None and hold[0] else event
            def sample(pid):
                observed['pid'] = pid
                return p7.proc_sample(pid)
            def is_zombie():
                if 'pid' not in observed:
                    return False
                state = (Path('/proc') / str(observed['pid']) / 'stat').read_text().rsplit(')', 1)[1].split()[0]
                if state == 'Z':
                    hold[0] = False
                    return True
                return False
            def readiness(*args):
                return is_zombie()
            def deadline():
                return 'expired while exited' if is_zombie() else None
            actions = [{'signal': 'SIGTERM', 'stable_ms': 0}] if kind == 'signal' else []
            with mock.patch.object(p7.NativeChild, 'poll', delayed):
                result = p7.execute([sys.executable, '-c', 'print("READY")'], dict(os.environ), str(p7.TMP),
                    {'actions': actions, 'timeout': 2}, proc_sampler=sample,
                    readiness_fn=readiness, continue_running=deadline if kind == 'deadline' else None)
            self.assertEqual(result['termination'], {'exit': 0})
            self.assertEqual(result['native_cleanup_issues'], [])
            if kind == 'signal':
                self.assertEqual(result['actions'][0]['signal'], signal.SIGTERM)
                self.assertIsNone(result['failure'])
            else:
                self.assertEqual(result['failure'], 'expired while exited')

    def test_output_limit_at_immediate_exit_returns_failure(self):
        result = self.invoke('import os\nos.write(1,b"x"*4096)', io='pipe', max_output_bytes=128)
        self.assertEqual(result['failure'], 'output limit exceeded')
        self.assertGreater(len(result['stdout']), 128)
        self.assertEqual(result['native_cleanup_issues'], [])

    def test_pipe_stdin_is_nonblocking_and_closed_reader_is_evidence(self):
        for program in ('import time\ntime.sleep(5)', 'import os,time\nos.close(0);time.sleep(5)'):
            before = len(list(Path('/proc/self/fd').iterdir()))
            result = self.invoke(program, io='pipe', stdin='x' * (1024 * 1024), timeout=.1)
            self.assertTrue(result['failure'])
            self.assertIn(result['termination'], ({'signal': 9, 'core_dumped': False}, {'exit': 0}))
            self.assertLess(result['metrics']['stdin_bytes_written'], result['metrics']['stdin_bytes_expected'])
            self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)

    def test_partial_acquisition_spawn_failure_and_startup_timeout_close_fds(self):
        helper = p7.spawn_helper()
        before = len(list(Path('/proc/self/fd').iterdir()))
        with mock.patch.object(p7.os, 'pipe', side_effect=OSError('pipe acquisition failed')):
            with self.assertRaisesRegex(OSError, 'pipe acquisition failed'):
                self.invoke('pass')
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)
        with mock.patch.object(p7, 'NativeChild', side_effect=RuntimeError('spawn failed')):
            with self.assertRaisesRegex(RuntimeError, 'spawn failed'):
                self.invoke('pass')
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)
        with tempfile.TemporaryDirectory(dir=p7.TMP) as folder:
            stub = Path(folder) / 'stalled-helper'
            stub.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(5)\n')
            stub.chmod(0o700)
            with mock.patch.object(p7, 'spawn_helper', return_value={**helper, 'path': str(stub)}), \
                    mock.patch.object(p7.NativeChild, 'STARTUP_TIMEOUT', .05):
                with self.assertRaisesRegex(p7.NativeProtocolError, 'timed out'):
                    self.invoke('pass')
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)

    def test_supervisor_sigkill_is_invalid_reaped_and_fd_clean(self):
        observed, killed = {}, [False]
        def sample(pid):
            fields = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
            observed.update(pid=pid, supervisor=int(fields[1]))
            return p7.proc_sample(pid)
        def readiness(terminal, *args):
            if 'READY' in terminal.text() and not killed[0]:
                os.kill(observed['supervisor'], signal.SIGKILL)
                killed[0] = True
            return False
        before = len(list(Path('/proc/self/fd').iterdir()))
        result = p7.execute([sys.executable, '-c', 'import os,time\nos.write(1,b"READY");time.sleep(5)'],
            dict(os.environ), str(p7.TMP), {'actions': [{'checkpoint': 'never'}]},
            proc_sampler=sample, readiness_fn=readiness)
        self.assertTrue(result['failure'])
        self.assertIsNone(result['metrics']['resource_source'])
        self.assertIsNone(result['metrics']['user_cpu_ms'])
        self.assertIsNone(result['termination'])
        try:
            state = (Path('/proc') / str(observed['pid']) / 'stat').read_text().rsplit(')', 1)[1].split()[0]
        except FileNotFoundError:
            state = None
        self.assertIn(state, (None, 'Z'))
        with self.assertRaises(ChildProcessError):
            os.waitpid(observed['supervisor'], os.WNOHANG)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)

    def test_original_exception_survives_supervisor_failure_cleanup(self):
        observed = {}
        def sample(pid):
            fields = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
            observed['supervisor'] = int(fields[1])
            return p7.proc_sample(pid)
        def readiness(*args):
            os.kill(observed['supervisor'], signal.SIGKILL)
            raise ValueError('original observer exception')
        before = len(list(Path('/proc/self/fd').iterdir()))
        with self.assertRaisesRegex(ValueError, 'original observer exception'):
            p7.execute([sys.executable, '-c', 'import time\ntime.sleep(5)'], dict(os.environ), str(p7.TMP),
                {'actions': [{'checkpoint': 'never'}]}, proc_sampler=sample, readiness_fn=readiness)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)
        with self.assertRaises(ChildProcessError):
            os.waitpid(observed['supervisor'], os.WNOHANG)

    def test_narrow_fixture_signal_policy_preserves_other_ignored_signals(self):
        helper = p7.spawn_helper()
        saved = {s: signal.getsignal(s) for s in (signal.SIGQUIT, signal.SIGXFSZ, signal.SIGUSR1)}
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR2})
        try:
            for s in saved:
                signal.signal(s, signal.SIG_IGN)
            result = p7.execute([helper['path'], 'signal-state'], dict(os.environ), str(p7.TMP),
                                {'actions': [], 'io': 'pipe'})
            fields = dict(line.split(':', 1) for line in result['stdout'].decode().splitlines())
            ignored = int(fields['SigIgn'].strip(), 16)
            self.assertEqual(int(fields['SigBlk'].strip(), 16), 0)
            for s in p7.NativeChild.FIXTURE_DEFAULT_SIGNALS:
                self.assertFalse(ignored & (1 << (s - 1)))
            self.assertTrue(ignored & (1 << (signal.SIGUSR1 - 1)))
            self.assertEqual(result['termination'], {'exit': 0})
            self.assertTrue(all(signal.getsignal(s) == signal.SIG_IGN for s in saved))
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            for s, handler in saved.items():
                signal.signal(s, handler)

    def test_capability_probe_is_actual_group_delivery_and_fails_before_cli_acquisition(self):
        helper = p7.spawn_helper()
        self.assertEqual(helper['capability']['received'], ['LEADER', 'MEMBER'])
        self.assertEqual(helper['capability']['reserved_state'], 'Z')
        self.assertEqual(helper['capability']['probe_executable_sha256'], helper['sha256'])
        with mock.patch.dict(p7._NATIVE_CAPABILITIES, clear=True), \
                mock.patch.object(p7, 'native_capability_probe', side_effect=RuntimeError('unsupported native capability')), \
                mock.patch.object(p7.os, 'openpty', side_effect=AssertionError('CLI fds must not be acquired')):
            with self.assertRaisesRegex(RuntimeError, 'unsupported native capability'):
                self.invoke('pass')

    def test_block_io_and_descendant_attribution_are_explicit(self):
        result = self.invoke('import subprocess,sys\n'
            'subprocess.run([sys.executable,"-c","import time;time.sleep(.2)"],check=True)\n', io='pipe')
        self.assertIsInstance(result['metrics']['block_input_operations'], int)
        self.assertIsInstance(result['metrics']['block_output_operations'], int)
        self.assertIn('children it waits for', result['metrics']['wait4_descendant_attribution'])
        self.assertIn('observed_descendants', result['metrics'])
        self.assertTrue(result['metrics']['observed_descendants'])
        self.assertFalse(result['metrics']['descendant_comparison_eligible'])

    def test_compiler_version_change_invalidates_helper_cache(self):
        actual = p7.spawn_helper()['compiler']
        with tempfile.TemporaryDirectory(dir=p7.TMP) as folder:
            root = Path(folder)
            version, log = root / 'version', root / 'builds'
            version.write_text('fixture-compiler-v1')
            compiler = root / 'cc'
            compiler.write_text('#!' + sys.executable + '\nimport os,sys\n'
                'from pathlib import Path\n'
                f'if sys.argv[1:]==["--version"]: print(Path({str(version)!r}).read_text());raise SystemExit(0)\n'
                f'with open({str(log)!r},"a") as f: f.write("compile\\n")\n'
                f'os.execv({actual!r},[{actual!r},*sys.argv[1:]])\n')
            compiler.chmod(0o700)
            with mock.patch.object(p7.shutil, 'which', return_value=str(compiler)):
                first = p7.spawn_helper()
                version.write_text('fixture-compiler-v2')
                with self.assertRaisesRegex(RuntimeError, 'executable cache missing'):
                    p7.spawn_helper(build=False)
                self.assertEqual(log.read_text().splitlines(), ['compile'])
                second = p7.spawn_helper()
            self.assertNotEqual(first['build_key'], second['build_key'])
            self.assertNotEqual(first['path'], second['path'])
            self.assertEqual(first['source_sha256'], second['source_sha256'])
            self.assertEqual(first['compiler'], str(compiler.resolve()))
            self.assertEqual(second['compiler_version'], 'fixture-compiler-v2')
            self.assertEqual(log.read_text().splitlines(), ['compile', 'compile'])

    def test_no_build_no_probe_reuses_prepared_identity_without_tools_or_writes(self):
        helper = p7.spawn_helper()
        provenance = Path(helper['provenance_path'])
        before = provenance.read_bytes()
        with mock.patch.object(p7.subprocess, 'run', side_effect=AssertionError('no subprocess in capture')), \
                mock.patch.object(p7.subprocess, 'check_output', side_effect=AssertionError('no compiler probe')), \
                mock.patch.object(p7, 'native_capability_probe', side_effect=AssertionError('reuse actual proof')), \
                mock.patch.object(p7.os, 'replace', side_effect=AssertionError('no sidecar/cache writes')):
            self.assertEqual(p7.spawn_helper(build=False, probe=False), helper)
            result = p7.execute([sys.executable, '-c', 'print("PREPARED")'], dict(os.environ),
                                str(p7.TMP), {'actions': [], 'io': 'pipe'}, spawn_helper_build=False)
            self.assertIsNone(result['failure'])
            self.assertEqual(result['termination'], {'exit': 0})
            self.assertEqual(result['stdout'], b'PREPARED\n')
            self.assertEqual(result['metrics']['resource_source'], 'native_child_wait4')
        self.assertEqual(provenance.read_bytes(), before)

    def test_capture_compiler_bytes_drift_fails_before_tools_or_cli_acquisition(self):
        actual = p7.spawn_helper()['compiler']
        with tempfile.TemporaryDirectory(dir=p7.TMP) as folder:
            compiler = Path(folder) / 'cc'
            compiler.write_text('#!' + sys.executable + '\nimport os,sys\n'
                                f'os.execv({actual!r},[{actual!r},*sys.argv[1:]])\n')
            compiler.chmod(0o700)
            with mock.patch.object(p7.shutil, 'which', return_value=str(compiler)):
                helper = p7.spawn_helper()
                compiler.write_text(compiler.read_text() + '# identity changed\n')
                before = Path(helper['provenance_path']).read_bytes()
                with mock.patch.object(p7.subprocess, 'run', side_effect=AssertionError('no build')), \
                        mock.patch.object(p7.subprocess, 'check_output', side_effect=AssertionError('no probe')), \
                        mock.patch.object(p7, 'native_capability_probe', side_effect=AssertionError('no probe')), \
                        mock.patch.object(p7.os, 'openpty', side_effect=AssertionError('no CLI acquisition')):
                    with self.assertRaisesRegex(RuntimeError, 'compiler snapshot missing/drifted'):
                        p7.execute([sys.executable, '-c', 'pass'], dict(os.environ), str(p7.TMP),
                                   {'actions': []}, spawn_helper_build=False)
                self.assertEqual(Path(helper['provenance_path']).read_bytes(), before)

    def test_no_build_missing_provenance_and_tampering_are_fail_closed(self):
        p7.spawn_helper()
        with tempfile.TemporaryDirectory(dir=p7.TMP) as folder, mock.patch.object(p7, 'TMP', Path(folder)):
            helper = p7.spawn_helper()
            binary, manifest = Path(helper['path']), Path(helper['provenance_path'])
            original_binary, original_manifest = binary.read_bytes(), manifest.read_bytes()
            for fault in ('missing', 'invalid-json', 'flags', 'compiler', 'binary'):
                binary.write_bytes(original_binary)
                manifest.write_bytes(original_manifest)
                if fault == 'missing':
                    manifest.unlink()
                elif fault == 'invalid-json':
                    manifest.write_text('[]')
                elif fault == 'binary':
                    binary.write_bytes(original_binary + b'tamper')
                else:
                    data = json.loads(original_manifest)
                    data[fault] = ['-O0'] if fault == 'flags' else '/different/compiler'
                    manifest.write_text(json.dumps(data))
                before = {p.name: p.read_bytes() for p in Path(folder).iterdir()}
                with mock.patch.object(p7.subprocess, 'run', side_effect=AssertionError('must not rebuild')), \
                        mock.patch.object(p7.subprocess, 'check_output', side_effect=AssertionError('must not probe')), \
                        mock.patch.object(p7, 'native_capability_probe', side_effect=AssertionError('must not probe')):
                    with self.assertRaisesRegex(RuntimeError, 'provenance'):
                        p7.spawn_helper(build=False, probe=False)
                self.assertEqual({p.name: p.read_bytes() for p in Path(folder).iterdir()}, before)

    def test_no_build_does_not_create_missing_cache_directory(self):
        p7.spawn_helper()
        with tempfile.TemporaryDirectory(dir=p7.TMP) as folder:
            missing = Path(folder) / 'absent'
            with mock.patch.object(p7, 'TMP', missing), \
                    mock.patch.object(p7.subprocess, 'run', side_effect=AssertionError('must not build')):
                with self.assertRaisesRegex(RuntimeError, 'executable cache missing'):
                    p7.spawn_helper(build=False, probe=False)
            self.assertFalse(missing.exists())

    def test_capture_cache_loss_after_preflight_never_builds_or_acquires_cli(self):
        p7.spawn_helper()
        with tempfile.TemporaryDirectory(dir=p7.TMP) as folder, mock.patch.object(p7, 'TMP', Path(folder)):
            helper = p7.spawn_helper()
            self.assertEqual(p7.spawn_helper(build=False, probe=False), helper)
            Path(helper['path']).unlink()  # Loss after successful preflight.
            before = {p.name: p.read_bytes() for p in Path(folder).iterdir()}
            with mock.patch.object(p7.subprocess, 'run', side_effect=AssertionError('no build in capture')), \
                    mock.patch.object(p7.subprocess, 'check_output', side_effect=AssertionError('no compiler probe')), \
                    mock.patch.object(p7, 'native_capability_probe', side_effect=AssertionError('no capability probe')), \
                    mock.patch.object(p7.os, 'openpty', side_effect=AssertionError('no CLI acquisition')):
                with self.assertRaisesRegex(RuntimeError, 'executable cache missing'):
                    p7.execute([sys.executable, '-c', 'pass'], dict(os.environ), str(p7.TMP),
                               {'actions': []}, spawn_helper_build=False)
            self.assertEqual({p.name: p.read_bytes() for p in Path(folder).iterdir()}, before)

    def test_capture_requires_prepared_compiler_snapshot_and_capability(self):
        p7.spawn_helper()
        for missing in (p7._COMPILER_VERSIONS, p7._NATIVE_CAPABILITIES):
            with mock.patch.dict(missing, clear=True), \
                    mock.patch.object(p7.subprocess, 'check_output', side_effect=AssertionError('no compiler probe')), \
                    mock.patch.object(p7, 'native_capability_probe', side_effect=AssertionError('no capability probe')), \
                    mock.patch.object(p7.os, 'openpty', side_effect=AssertionError('no CLI acquisition')):
                with self.assertRaisesRegex(RuntimeError, 'snapshot|capability proof'):
                    p7.execute([sys.executable, '-c', 'pass'], dict(os.environ), str(p7.TMP),
                               {'actions': []}, spawn_helper_build=False)

    def test_descendant_observation_off_still_kills_actual_same_group_child(self):
        child = ('import os,signal,time;signal.signal(signal.SIGHUP,signal.SIG_IGN);'
                 'os.write(1,b"C");time.sleep(5)')
        program = ('import json,os,subprocess,sys\n'
            f'p=subprocess.Popen([sys.executable,"-c",{child!r}],stdout=subprocess.PIPE)\n'
            'assert p.stdout.read(1)==b"C"\n'
            'os.write(2,json.dumps({"child":p.pid}).encode())\n'
            'os.write(1,b"READY");os.read(0,1);os._exit(0)\n')
        for enabled in (True, False):
            with mock.patch.object(p7, 'descendants', wraps=p7.descendants) as observe:
                result = p7.execute([sys.executable, '-c', program], dict(os.environ), str(p7.TMP),
                    {'actions': [{**workloads.ready('ready', 'READY'), 'send': 'x\n'}]},
                    observer_config={'record_descendants': enabled})
            self.assertIsNone(result['failure'])
            self.assertEqual(result['termination'], {'exit': 0})
            self.assertEqual(bool(observe.call_count), enabled)
            self.assertEqual(bool(result['metrics']['observed_descendants']), enabled)
            pid = json.loads(result['stderr'])['child']
            try:
                state = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[0]
            except FileNotFoundError:
                state = None
            self.assertIn(state, (None, 'Z'))  # Required group cleanup, even without observation.
            self.assertEqual(result['native_cleanup_issues'], [])
            if not enabled:
                self.assertFalse(result['metrics']['descendant_comparison_eligible'])

    def test_linux_only_failure_precedes_cli_fd_acquisition(self):
        with mock.patch.object(p7.sys, 'platform', 'darwin'), \
                mock.patch.object(p7.os, 'openpty', side_effect=AssertionError('no CLI acquisition')):
            with self.assertRaisesRegex(RuntimeError, 'requires Linux'):
                self.invoke('pass')

    def test_ignored_sigchld_is_rejected_without_changing_observer_policy(self):
        saved = signal.getsignal(signal.SIGCHLD)
        try:
            signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            with mock.patch.object(p7.os, 'openpty', side_effect=AssertionError('no CLI acquisition')):
                with self.assertRaisesRegex(RuntimeError, 'SIGCHLD disposition'):
                    self.invoke('pass')
            self.assertEqual(signal.getsignal(signal.SIGCHLD), signal.SIG_IGN)
        finally:
            signal.signal(signal.SIGCHLD, saved)

    def test_native_supervisor_reaps_cli_after_observer_exception(self):
        identities = {}
        def sample(pid):
            fields = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
            identities.update(pid=pid, supervisor=int(fields[1]))
            return p7.proc_sample(pid)
        def broken_readiness(*args):
            raise RuntimeError('injected observer failure')
        with self.assertRaisesRegex(RuntimeError, 'injected observer failure'):
            p7.execute([sys.executable, '-c', 'import time\ntime.sleep(5)'], dict(os.environ),
                       str(p7.TMP), {'actions': [{'checkpoint': 'never'}]},
                       proc_sampler=sample, readiness_fn=broken_readiness)
        self.assertFalse((Path('/proc') / str(identities['pid'])).exists())
        with self.assertRaises(ChildProcessError):
            os.waitpid(identities['supervisor'], os.WNOHANG)

    def test_custom_sampling_readiness_and_minimal_histories_keep_native_metrics(self):
        observed, readiness = [], []
        def sampler(pid):
            observed.append(pid)
            value = p7.proc_sample(pid)
            return {**value, 'custom_counter': 17} if value else None
        def qualifies(terminal, wait, trace, tty, action_started, now):
            readiness.append((action_started, now))
            return 'READY' in terminal.text()
        result = p7.execute([sys.executable, '-c', 'import os\nos.write(1,b"READY");os.read(0,1)'],
             dict(os.environ), str(p7.TMP), {'actions': [{'checkpoint': 'ready',
             'wait': {'contains': ['deliberately absent']}, 'send': 'x\n'}], 'timeout': 3},
             proc_sampler=sampler, readiness_fn=qualifies,
             observer_config={'record_termios': False, 'record_chunks': False})
        self.assertIsNone(result['failure'])
        self.assertEqual(result['termination'], {'exit': 0})
        self.assertTrue(observed and readiness)
        self.assertEqual(result['termios_observations'], [])
        self.assertEqual(result['stdout_chunks'], [])
        self.assertIn('peak_rss_bytes', result['metrics'])
        self.assertTrue(all(s['custom_counter'] == 17 for s in result['metrics']['proc_samples']))
        stamp = result['frames'][0]
        self.assertLessEqual(stamp['first_useful_monotonic_ns'], stamp['stable_since_monotonic_ns'])
        self.assertGreaterEqual(stamp['ready_monotonic_ns'] - stamp['stable_since_monotonic_ns'], 80_000_000)

    def test_deadline_callback_kills_reaps_and_retains_failure_evidence(self):
        answers = iter([None, 'coordinator deadline elapsed'])
        result = p7.execute([sys.executable, '-c', 'import time\ntime.sleep(5)'],
            dict(os.environ), str(p7.TMP), {'actions': [], 'timeout': 3},
            continue_running=lambda: next(answers, 'coordinator deadline elapsed'), proc_sampler=lambda pid: None)
        self.assertEqual(result['failure'], 'coordinator deadline elapsed')
        self.assertEqual(result['termination'], {'signal': 9, 'core_dumped': False})
        self.assertEqual(result['metrics']['proc_samples'], [])
        self.assertIn('user_cpu_ms', result['metrics'])

    def test_bad_observer_option_fails_before_spawn(self):
        with mock.patch.object(p7, 'spawn_helper', side_effect=AssertionError('must not launch')):
            with self.assertRaises(ValueError):
                p7.execute([sys.executable, '-c', 'pass'], dict(os.environ), str(p7.TMP), {},
                           observer_config={'unknown': True})
    def invoke(self, program, actions=(), **case):
        env = dict(os.environ)
        env['PB_THEME'] = 'dark'
        return p7.execute([sys.executable, '-c', program], env, str(p7.TMP),
                          {'actions': list(actions), 'timeout': 3, **case})

    def test_real_raw_mode_and_raii_like_finally_restoration(self):
        result = self.invoke(
            'import os,termios,tty\n'
            'saved=termios.tcgetattr(0)\n'
            'try:\n'
            ' tty.setraw(0);os.write(1,b"\\x1b[?1049h\\x1b[?25lREADY");os.read(0,1)\n'
            'finally:\n'
            ' termios.tcsetattr(0,termios.TCSANOW,saved);os.write(1,b"\\x1b[?25h\\x1b[?1049lSUMMARY\\n")\n',
            [{**workloads.ready('ready', 'READY', cooked=False, alternate=True), 'send': 'x'}],
            initial_screen='surrounding output\r\n')
        self.assertIsNone(result['failure'])
        self.assertTrue(result['termios_restored'])
        self.assertFalse(result['final_frame']['alternate'])
        self.assertTrue(result['final_frame']['cursor']['visible'])
        self.assertEqual(result['post_screen_stdout'], b'SUMMARY\r\n')
        checkpoint = result['frames'][0]
        self.assertGreaterEqual(checkpoint['ready_monotonic_ns'] - checkpoint['first_useful_monotonic_ns'], 80_000_000)
        self.assertIn('peak_rss_bytes', result['metrics'])
        self.assertTrue(result['metrics']['proc_samples'])

    def test_exception_restores_terminal_before_error_stream(self):
        result = self.invoke(
            'import os,termios,tty\n'
            'saved=termios.tcgetattr(0)\n'
            'try:\n'
            ' tty.setraw(0);os.write(1,b"READY");os.read(0,1);raise RuntimeError("dummy failure")\n'
            'finally: termios.tcsetattr(0,termios.TCSANOW,saved)\n',
            [{**workloads.ready('ready', 'READY', cooked=False), 'send': 'x'}])
        self.assertEqual(result['termination'], {'exit': 1})
        self.assertTrue(result['termios_restored'])
        self.assertIn(b'dummy failure', result['stderr'])

    def test_true_signal_is_not_numeric_exit(self):
        result = self.invoke('import os,time\nos.write(1,b"READY");time.sleep(5)',
                             [workloads.ready('ready', 'READY'), {'signal': 'SIGTERM'}])
        self.assertEqual(result['termination'], {'signal': signal.SIGTERM, 'core_dumped': False})
        numeric = self.invoke('raise SystemExit(143)')
        self.assertEqual(numeric['termination'], {'exit': 143})

    def test_control_c_input_is_a_key_in_raw_mode(self):
        result = self.invoke('import os,tty,termios\ns=termios.tcgetattr(0);tty.setraw(0)\n'
                             'os.write(1,b"READY");value=os.read(0,1)\n'
                             'termios.tcsetattr(0,termios.TCSANOW,s);os.write(2,value)\n',
                             [workloads.ready('ready', 'READY', cooked=False), {'key': 'ctrl-c'}])
        self.assertEqual(result['stderr'], b'\x03')
        self.assertEqual(result['termination'], {'exit': 0})

    def test_timeout_is_invalid_and_terminal_leak_is_observable(self):
        result = self.invoke('import tty,time\ntty.setraw(0);time.sleep(5)', timeout=.1)
        self.assertEqual(result['failure'], 'session timeout')
        self.assertFalse(result['termios_restored'])

    def test_pipe_case_has_no_controlling_terminal(self):
        result = self.invoke('import os\n'
            'try: fd=os.open("/dev/tty",os.O_RDWR);os.close(fd);raise SystemExit(9)\n'
            'except OSError: os.write(1,b"no controlling tty")\n', io='pipe')
        self.assertEqual(result['termination'], {'exit': 0})
        self.assertEqual(result['stdout'], b'no controlling tty')

    def test_process_exit_before_readiness_is_not_a_useful_frame(self):
        result = self.invoke('print("wrong screen")', [workloads.ready('needed', 'READY')])
        self.assertIn('process exited before action', result['failure'])
        self.assertEqual(result['frames'], [])

    def test_unchanged_frame_checkpoint_rejects_signal_changed_state(self):
        result = self.invoke('import os,signal,time\n'
            'def changed(*args): os.write(1,b"CHANGED")\n'
            'signal.signal(signal.SIGTERM,changed);os.write(1,b"READY");time.sleep(5)\n',
            [workloads.ready('anchor', 'READY'), {'signal': 'SIGTERM', 'stable_ms': 0},
             {**workloads.ready('unchanged', unchanged_from='anchor'), 'stable_ms': 150}], timeout=.7)
        self.assertEqual(result['failure'], 'session timeout')
        self.assertNotIn('unchanged', [f['name'] for f in result['frames']])

    def test_resize_delivered_to_real_child(self):
        result = self.invoke('import os,signal,struct,fcntl,termios,time\n'
            'def changed(*args):\n'
            ' rows,cols,_,_=struct.unpack("HHHH",fcntl.ioctl(0,termios.TIOCGWINSZ,b"\\0"*8))\n'
            ' os.write(1,(" SIZE %dx%d"%(cols,rows)).encode());raise SystemExit(0)\n'
            'signal.signal(signal.SIGWINCH,changed);os.write(1,b"READY");time.sleep(5)\n',
            [workloads.ready('ready', 'READY'), {'resize': [65, 20]}])
        self.assertIn(b'SIZE 65x20', result['stdout'])
        self.assertEqual(result['final_frame']['columns'], 65)


class ContractsTests(unittest.TestCase):
    def test_expected_chart_cell_is_an_independent_gate(self):
        result = p7.execute([sys.executable, '-c', 'pass'], dict(os.environ), str(p7.TMP), {'actions': []})
        terminal = p7.Terminal(4, 2)
        terminal.feed('●'.encode())
        result.update(case={'expect': {'checkpoint_cells': [
            {'checkpoint': 'dot', 'row': 0, 'col': 0, 'data': '●'}]}},
            requests=[], config_before={}, config_after={}, replacements={},
            frames=[{'name': 'dot', 'frame': terminal.frame()}])
        self.assertEqual(p7.validate(result), [])
        result['frames'][0]['frame']['cells'][0][0][0] = ' '
        self.assertTrue(any('expected cell' in issue for issue in p7.validate(result)))

    def test_view_panic_exception_is_scoped_to_runtime_stack_and_keeps_message(self):
        result = p7.execute([sys.executable, '-c', 'pass'], dict(os.environ), str(p7.TMP), {'actions': []})
        message = 'runtime error: slice bounds out of range [:38] with capacity 32'
        result.update(case={'runtime_view_panic_stack': True, 'expect': {'stdout_contains': ['Caught panic:', message]}},
                      stdout=('Caught panic:\n\n' + message + '\n\nRestoring terminal...\n\n').encode(),
                      stderr=b'goroutine 1 [running]:\nruntime/debug.Stack()\nProgram.recoverFromPanic()\n',
                      requests=[], config_before={}, config_after={}, replacements={})
        candidate = copy.deepcopy(result)
        candidate['stderr'] = b''
        self.assertEqual(p7.compare(result, candidate), [])
        candidate['stderr'] = b'unrelated application failure\n'
        self.assertIn('stderr bytes differ', p7.compare(result, candidate))
        candidate['stderr'] = b''
        candidate['stdout'] = b'Caught panic: different value\n'
        self.assertTrue(any('missing' in i for i in p7.compare(result, candidate)))
        result['case'].pop('runtime_view_panic_stack')
        self.assertIn('stderr bytes differ', p7.compare(result, candidate))
    def test_sample_injected_executor_receives_existing_fixture_and_serializes(self):
        calls = []
        def injected(command, env, cwd, case, fixture):
            calls.append((command, fixture))
            return p7.execute(command, env, cwd, case, fixture, proc_sampler=lambda pid: None)
        case = {'name': 'injected-sample', 'argv': ['-c', 'print("READY")'],
                'actions': [], 'config': None, 'io': 'pipe',
                'expect': {'termination': {'exit': 0}, 'stdout_exact': 'READY\n'}}
        with tempfile.TemporaryDirectory(dir=p7.TMP) as temporary:
            result = p7.sample(sys.executable, case, Path(temporary) / 'capture', execute_fn=injected)
            self.assertEqual(len(calls), 1)
            self.assertEqual(p7.validate(result), [])
            self.assertEqual(p7.load_session(Path(temporary) / 'capture/session.json')['stdout'], b'READY\n')
    def test_sigquit_preserves_prefix_and_disposition_only_runtime_tail_commutes(self):
        result = p7.execute([sys.executable, '-c', 'pass'], dict(os.environ), str(p7.TMP), {'actions': []})
        result.update(case={'runtime_sigquit_tail': True, 'expect': {'stderr_prefix': 'SIGQUIT: quit\n'}},
                      stderr=b'SIGQUIT: quit\ngoroutine runtime-specific dump\n', requests=[],
                      config_before={}, config_after={}, replacements={})
        candidate = copy.deepcopy(result)
        candidate['stderr'] = b'SIGQUIT: quit\nnative runtime-specific dump\n'
        self.assertEqual(p7.compare(result, candidate), [])
        candidate['stderr'] = b'SIGQUIT: changed\nnative dump\n'
        self.assertIn('stderr bytes differ', p7.compare(result, candidate))
        candidate = copy.deepcopy(result)
        candidate['termination'] = {'signal': 3, 'core_dumped': False}
        self.assertIn('termination differs', p7.compare(result, candidate))

    def test_hang_case_requires_150ms_signal_gap_and_raw_alt_no_restore(self):
        cases = [c for c in workloads.corpus() if 'hang-double-sigterm' in c['name']]
        self.assertEqual(len(cases), 2)
        for case in cases:
            self.assertEqual(case['expect']['signal_sequence'], [15, 15, 3])
            self.assertEqual(case['expect']['min_term_gap_ms'], 150)
            self.assertFalse(case['expect']['termios_restored'])
            after = [a for a in case['actions'] if a.get('wait', {}).get('unchanged_from')]
            self.assertEqual(len(after), 2)
            self.assertTrue(all(a['stable_ms'] >= 150 for a in after))
    def test_startup_only_commutes_and_later_chain_stays_ordered(self):
        result = p7.execute([sys.executable, '-c', 'pass'], dict(os.environ), str(p7.TMP), {'actions': []})
        result.update(case={'expect': {}, 'initial_fanout_count': 3,
                            'initial_fanout_members': ['GET /home', 'GET /metrics', 'GET /range']},
                      requests=[{'method': 'GET', 'path': p, 'body': '', 'headers': {}}
                                for p in ('/range', '/home', '/metrics', '/range', '/instant')],
                      config_before={}, config_after={}, replacements={})
        candidate = copy.deepcopy(result)
        candidate['requests'][:3] = reversed(candidate['requests'][:3])
        self.assertEqual(p7.compare(result, candidate), [])
        candidate['requests'][3:] = reversed(candidate['requests'][3:])
        self.assertIn('ordered request contract differs', p7.compare(result, candidate))
        candidate = copy.deepcopy(result)
        candidate['requests'][0]['headers']['x-api-key'] = 'wrong'
        self.assertIn('ordered request contract differs', p7.compare(result, candidate))

    def test_auto_selected_cache_requires_home_and_exact_multiplicity(self):
        result = p7.execute([sys.executable, '-c', 'pass'], dict(os.environ), str(p7.TMP), {'actions': []})
        members = ['GET /home', 'GET /metrics', 'GET /metrics']
        result.update(case={'expect': {}, 'initial_fanout_count': 3, 'initial_fanout_members': members,
                            'initial_fanout_url_constraints': [['/home', '/metrics?stream=selected']]},
                      requests=[{'method': 'GET', 'path': p, 'body': '', 'headers': {}}
                                for p in ('/metrics?stream=placeholder', '/home', '/metrics?stream=selected')],
                      config_before={}, config_after={}, replacements={})
        self.assertEqual(p7.validate(result), [])
        result['requests'][1:] = reversed(result['requests'][1:])
        self.assertTrue(any('dependency violated' in i for i in p7.validate(result)))
        result['requests'].pop()
        self.assertTrue(any('multiplicity differs' in i for i in p7.validate(result)))

    def test_clipboard_is_an_inert_process_with_logged_argv_and_limits(self):
        case = {'name': 'synthetic-clipboard', 'argv': ['-c',
                'import subprocess,sys\n'
                'sys.stdout.buffer.write(subprocess.check_output(["xclip","-out","-selection","clipboard"]))'],
                'config': None, 'actions': [], 'io': 'pipe', 'clipboard': {'text': 'clip-café\n日志'},
                'expect': {'termination': {'exit': 0}, 'requests': [], 'clipboard_reads': 1,
                           'children_reaped': True, 'stdout_exact': 'clip-café\n日志'}}
        with tempfile.TemporaryDirectory(dir=p7.TMP) as temporary:
            result = p7.sample(sys.executable, case, Path(temporary) / 'capture')
            self.assertEqual(p7.validate(result), [])
            start = result['clipboard_events'][0]
            self.assertIsNone(start['wayland'])
            self.assertEqual(len(start['nofile_limit']), 2)
            self.assertEqual(result['clipboard_events'][-1]['event'], 'complete')
            loaded = p7.load_session(Path(temporary) / 'capture/session.json')
            self.assertEqual(loaded['stdout'], result['stdout'])

    def test_paired_statistics_resample_rounds(self):
        report = p7.paired_session_statistics([{'reference': 2, 'candidate': 1},
                                              {'reference': 4, 'candidate': 2}], draws=200)
        self.assertEqual(report['median_ratio'], .5)
        self.assertEqual(report['count'], 2)
        with self.assertRaises(ValueError):
            p7.paired_session_statistics([{'reference': 0, 'candidate': 1}])

    def test_observer_floor_protocol_without_running_timed_work(self):
        def fake_execute(command, env, cwd, case):
            compile(command[-1], '<floor-emitter>', 'exec')
            return {'failure': None, 'termios_restored': True, 'stderr': b'0 1000000\n1 2000000\n',
                    'frames': [{'first_useful_monotonic_ns': 1100000}, {'first_useful_monotonic_ns': 2100000}],
                    'termination': {'exit': 0}}
        with tempfile.TemporaryDirectory(dir=p7.TMP) as temporary:
            with mock.patch.object(p7, 'require_quiet_window', return_value={'approved': True}):
                report = p7.observer_floor(Path(temporary) / 'floor', None, sessions=2, frames=2,
                                           execute_fn=fake_execute)
                self.assertEqual(report['summary']['count'], 2)
                self.assertEqual(report['summary']['median'], .1)

    def test_independent_fixture_query_selector(self):
        server = p7.FixtureServer({'routes': [workloads.route('POST', '/query', {}, query={'fields': ['true']}),
                                                workloads.route('POST', '/query', [])]})
        try:
            request = {'method': 'POST', 'target': '/query?fields=true', 'headers': {}, 'body': b'{}'}
            self.assertEqual(server._select(request), 0)
            request['target'] = '/query'
            self.assertEqual(server._select(request), 1)
        finally:
            server.close()

    def test_real_fixture_and_child_effects(self):
        case = {'name': 'synthetic-effects', 'argv': ['-c',
                'import urllib.request\nprint(urllib.request.urlopen("{{url}}/test").read().decode())'],
                'config': None, 'actions': [], 'io': 'pipe',
                'fixture': {'routes': [workloads.route('GET', '/test', 'ok')]},
                'expect': {'termination': {'exit': 0}, 'requests': ['GET /test'], 'stdout_exact': 'ok\n'}}
        with tempfile.TemporaryDirectory(dir=p7.TMP) as temporary:
            result = p7.sample(sys.executable, case, Path(temporary) / 'capture')
            self.assertEqual(p7.validate(result), [])
            self.assertEqual(len(result['milestones']), 1)
            self.assertTrue((Path(temporary) / 'capture/session.json').is_file())
            self.assertEqual(p7.compare(result, copy.deepcopy(result)), [])
            tampered = copy.deepcopy(result)
            tampered['final_frame']['cells'][0][0][1] = 'ffffff'
            self.assertTrue(any('styled cells' in i for i in p7.compare(result, tampered)))

    def test_session_statistics_do_not_treat_frames_as_repetitions(self):
        value = p7.session_statistics([{'m': 2}, {'m': 4}, {'m': 8}], 'm', draws=200)
        self.assertEqual(value['unit'], 'independent-session')
        self.assertEqual(value['count'], 3)
        self.assertEqual(value['median'], 4)
        with self.assertRaises(ValueError):
            p7.session_statistics([{'m': None}], 'm')

    def test_no_timed_work_without_coordinator_agreement(self):
        with tempfile.TemporaryDirectory(dir=p7.TMP) as temporary:
            path = Path(temporary) / 'quiet-window.json'
            path.write_text(json.dumps({'approved': True, 'scope': 'phase7', 'expires_unix': 0}))
            with self.assertRaises(ValueError):
                p7.require_quiet_window(path)

    def test_promoted_guards_have_actual_pty_cases(self):
        corpus = workloads.corpus()
        self.assertEqual(len({c['name'] for c in corpus}), len(corpus))
        names = {c['name'] for c in corpus}
        for label in ('short-empty', 'short-query', 'long-empty', 'long-true-query',
                      'ignored-yaml', 'ignored-save-as', 'compact-json'):
            self.assertIn('p7-sql-promoted-' + label + '-dark', names)
        self.assertIn('p7-saved-empty-output-dark', names)
        self.assertTrue(all(c['unit'] == 'session' and c['quiet_window_required']
                            for c in workloads.performance_workloads()))

    def test_promql_gap_controls_preserve_duplicate_json_and_visible_assertions(self):
        cases = {c['name']: c for c in workloads.corpus() if c.get('contract_gap')}
        self.assertEqual(len(cases), 6)
        duplicate = cases['p7-promql-g3-duplicate-result-merge-dark']
        raw = next(r['body'] for r in duplicate['fixture']['routes']
                   if r['path'] == '/prometheus/api/v1/query')
        pairs = json.loads(raw, object_pairs_hook=lambda p: p)
        data = next(v for k, v in pairs if k == 'data')
        self.assertEqual(sum(k == 'result' for k, _ in data), 2)
        first_right = next(a for a in cases['p7-promql-g10-equal-time-merge-dark']['actions']
                           if a.get('checkpoint') == 'first-right')
        self.assertIn('● value 1 ', first_right['wait']['contains'])
        self.assertNotIn('● value 1 ', '● value 100 ')
        self.assertTrue(all(c['env']['TZ'] == 'America/New_York' for c in cases.values()
                            if c['contract_gap'] == 'G4'))
        self.assertTrue(all(c['oracle_platform'] == 'linux/amd64' for c in cases.values()
                            if c['contract_gap'] == 'G7'))


if __name__ == '__main__':
    unittest.main()
