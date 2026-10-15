"""Collector source checks; native acquisition is intercepted before child creation."""
import copy
import fcntl
import json
import inspect
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import phase7_bench as bench


def options(**kwargs):
    return bench.Options(**{'sessions': 2, 'warmups': 1, 'themes': ('dark',),
                            'sizes': ((120, 40),), 'floor_sessions': 1,
                            'bootstrap_draws': 40, **kwargs})


def request(offset, count=500):
    return {'method': 'POST', 'path': '/api/v1/query?fields=true',
            'body': json.dumps({'query': f'SELECT * FROM logs LIMIT {count} OFFSET {offset}'})}


def frame(text, columns=80, rows=30):
    cells = [[[' ', 'ffffff', '000000'] for _ in range(columns)] for _ in range(rows)]
    for y, line in enumerate(text.splitlines()):
        for x, character in enumerate(line[:columns]):
            cells[y][x][0] = character
    return {'columns': columns, 'rows': rows, 'cells': cells, 'alternate': True}


def synthetic():
    phases = bench.Phases()
    phases.checkpoint('startup', 'startup', {'contains': ['EDITOR']})
    phases.change('edit', 'editor', {'send': 'SELECT'}, {'contains': ['SELECT']})
    phases.idle({'contains': ['SELECT']}, 1)
    case = {'name': 'synthetic', 'argv': ['sql', '-i'],
            'fixture': {},
            'resize_signal_policy': bench.RESIZE_SIGNAL_POLICY, 'actions': phases.actions,
            'bench': {'kind': 'editor', 'phases': phases.definitions, 'observer_policy': 'primary'}}
    stamps = [('startup', 'EDITOR', 10, 20, 100), ('edit', 'SELECT', 120, 130, 210),
              ('idle-start', 'SELECT', 220, 220, 300), ('idle-end', 'SELECT', 1310, 1320, 1400)]
    frames = [{'name': name, 'frame': frame(text), 'first_useful_monotonic_ns': first * 1000000,
               'stable_since_monotonic_ns': stable * 1000000, 'ready_monotonic_ns': ready * 1000000,
               'first_ever_match_monotonic_ns': first * 1000000, 'last_output_monotonic_ns': first * 1000000, 'bytes_since_action': 123}
              for name, text, first, stable, ready in stamps]
    samples = [{'monotonic_ns': ms * 1000000, 'user_ticks': i, 'system_ticks': i,
                'rss_bytes': 10000 + i * 100, 'high_water_rss_bytes': 11000 + i * 100, 'fd_count': 4, 'minor_faults': i, 'major_faults': 0,
                'voluntary_context_switches': i * 2, 'involuntary_context_switches': i,
                'io': {'rchar': 500 + i * 10, 'wchar': 500 + i * 20}}
               for i, ms in enumerate((50, 400, 900, 1300, 1450))]
    metrics = {key: 0 for key in ('user_cpu_ms', 'system_cpu_ms', 'peak_rss_bytes', 'minor_faults',
                                 'major_faults', 'voluntary_context_switches',
                                 'involuntary_context_switches', 'stdout_bytes', 'stderr_bytes', 'block_input_operations', 'block_output_operations')}
    metrics.update(observed_descendants=[], descendant_comparison_eligible=False,
                   resource_source='native_child_wait4', native_spawn={'source_sha256': 'a' * 64, 'sha256': 'b' * 64}, peak_rss_bytes=1048576, proc_samples=samples, clock_ticks_per_second=100,
                   observer={'user_cpu_ms': 7, 'system_cpu_ms': 3}, child_environment={'HOME': '<HOME>'})
    result = {'case': case, 'frames': frames, 'actions': [{'index': 1, 'input_hex': 'aa',
               'monotonic_ns': 110000000, 'evaluated_monotonic_ns': 110000000, 'dispatch_monotonic_ns': 115000000}], 'started_monotonic_ns': 0, 'reaped_monotonic_ns': 1500000000, 'exited_monotonic_ns': 1500000000,
               'requests': [], 'metrics': metrics, 'milestones': [], 'resize_signal_policy': bench.RESIZE_SIGNAL_POLICY}
    return case, result


def unit_evaluation_history(result, phases):
    """Explicit synthetic single-evaluation traces, never actual measurements."""
    offsets = result['decoder_offsets']; offsets['schema_version'] = 2
    actions = {row['index']: row['byte_offset'] for row in offsets['actions']}
    frames = {row['name']: row for row in result['frames']}
    for phase in phases:
        stamp = next(row for row in offsets['frames'] if row['name'] == phase['name'])
        begin = 0 if phase['trigger_action_index'] is None else actions[phase['trigger_action_index']]
        terminal = bench.p7.Terminal(*result['case']['size'], result['case'].get('theme', 'dark'))
        terminal.feed(result['stdout'][:begin])
        started = stamp['first_monotonic_ns'] - 1
        frames[phase['name']]['action_started_monotonic_ns'] = started
        stamp.update(action_started_monotonic_ns=started, baseline_bytes=terminal.byte_offset,
            wait=copy.deepcopy(phase.get('wait', {})), evaluation_count=1, recording_error=None,
            evaluations=[{'byte_offset':stamp['first_offset'],'evaluated_monotonic_ns':stamp['first_monotonic_ns'],
                          'tty':{'canonical':False,'echo':False,'signals':False},'qualified':True}])


class DefinitionsTests(unittest.TestCase):
    def test_sustained_sql_visits_all_windows_then_reverse_refetches(self):
        case = bench.sql_case({'name': 'sql-scroll-20000'}, (120, 40), 'dark', options(), sustained=True)
        phases = {p['name']: p for p in case['bench']['phases']}
        self.assertEqual(case['bench']['forward_windows'], 40)
        self.assertGreaterEqual(phases['window-39']['selected_row'], 19500)
        self.assertEqual(sum(name.startswith('reverse-window-') for name in phases), 38)
        self.assertEqual(phases['reverse-window-36']['effects']['sql_window_counts'], {'18000': 2})
        self.assertTrue(all(p.get('burst_keys', 1) > 0 for p in phases.values()))
        for p in phases.values():
            if 'selected_row' in p:
                self.assertIn(f'bench-row-{p["selected_row"]:06d}', p['wait']['contains'])

    def test_error_cases_and_full_attribution_matrix_are_explicit(self):
        opts = options(themes=('dark', 'light'), include_long_idle=True, observer_policy='resource-attribution')
        cases = bench.campaign_cases(opts)
        self.assertEqual(len(cases), 26)
        self.assertEqual(sum(c['bench']['kind'] == 'error' for c in cases), 4)
        for case in (c for c in cases if c['bench']['kind'] == 'error'):
            route = next(r for r in case['fixture']['routes'] if r['path'] == case['bench']['endpoint'])
            self.assertEqual(case['bench']['phases'][0]['name'], 'startup')  # Same replay-floor interface.
            self.assertEqual(route['status'], 503)
            self.assertEqual(route['body'], 'fixture query failed')
            self.assertEqual(case['bench']['phases'][0]['effects']['request_exact'], {route['path']: 1})
        schedule = bench.schedule(cases, options(sessions=30, warmups=5))
        self.assertEqual(len(schedule), 910)
        self.assertEqual(sum(row['purpose'] == 'measured' for row in schedule), 780)

    def test_dense_setup_has_stable_anchor_before_measured_toggle(self):
        case = bench.dense_case({'name': 'promql-dense-1000', 'argv': []}, (120, 40), 'dark', options())
        phases = {p['name']: p for p in case['bench']['phases']}
        anchor = phases['setup-ready']
        self.assertEqual(anchor['kind'], 'anchor')
        self.assertTrue(anchor['wait']['output_after_action'])
        self.assertEqual(case['actions'][anchor['trigger_action_index']]['key'], 'shift-tab')
        self.assertEqual(case['actions'][3]['checkpoint'], 'setup-ready')
        self.assertEqual(case['actions'][3]['stable_ms'], 80)
        self.assertGreater(phases['table-view']['trigger_action_index'], 3)
        with self.assertRaises(ValueError):
            bench.Phases().change('ambiguous', 'resize', {'send': 'x', 'resize': [80, 30]}, {})
    def test_primary_and_resource_attribution_are_distinct_definitions(self):
        primary = bench.campaign_cases(options(case_filters=('sql-editor-*',)))[0]
        attribution = bench.campaign_cases(options(case_filters=('sql-editor-*',), observer_policy='resource-attribution'))[0]
        self.assertEqual(primary['bench']['observer_policy'], 'primary')
        self.assertTrue(attribution['name'].endswith('-resource-attribution'))
        self.assertNotEqual(bench.p7.digest(primary), bench.p7.digest(attribution))
        self.assertEqual(bench.OBSERVER_POLICIES['primary'], {'record_termios': False, 'record_chunks': False, 'record_descendants': False})

    def test_all_runtime_waits_are_screen_only_and_editor_target_is_nonblank(self):
        for case in bench.campaign_cases(options()):
            for action in case['actions']:
                wait = action.get('wait', {})
                self.assertFalse(set(wait) & {'request_paths', 'requests', 'sql_window_counts', 'sql_window_exact'})
        case = bench.editor_case((120, 40), 'dark', options())
        phase = next(p for p in case['bench']['phases'] if p['name'] == 'edit-select')
        self.assertEqual(phase['wait']['contains'], ['SELECT 1'])
        self.assertEqual(case['actions'][phase['trigger_action_index']]['send'], 'SELECT 1')
        self.assertEqual(bench.plan(options(case_filters=('sql-editor-*',)))['native_spawn_source_sha256'],
                         bench.sha_file(bench.ROOT / 'harness/phase7_spawn.c'))

    def test_window_routes_have_exact_slice_and_no_offset_prefix_collision(self):
        fixture = bench.sql_fixture(20000)
        routes = [r for r in fixture['routes'] if r['path'] == '/api/v1/query']
        self.assertEqual(len(routes), 40)
        for offset, route in zip(range(0, 20000, 500), routes):
            records = json.loads(route['body'])['records']
            self.assertEqual(len(records), 500)
            self.assertEqual(records[0]['sequence'], offset)
            self.assertEqual(records[-1]['sequence'], offset + 499)
            self.assertEqual(route['body_contains'], f'LIMIT 500 OFFSET {offset}"')
        self.assertNotIn(routes[1]['body_contains'], request(5000)['body'])
        self.assertIn(routes[1]['body_contains'], request(500)['body'])
        tail = [r for r in bench.sql_fixture(510)['routes'] if r['path'] == '/api/v1/query'][-1]
        self.assertEqual(len(json.loads(tail['body'])['records']), 10)
        self.assertEqual(tail['body_contains'], 'LIMIT 10 OFFSET 500"')

    def test_loaded_rows_and_request_turnover_are_explicit(self):
        source = {'name': 'sql-scroll-20000'}
        case = bench.sql_case(source, (120, 40), 'dark', options())
        self.assertTrue(case['bench']['cache_turnover'])
        phases = {p['name']: p for p in case['bench']['phases']}
        self.assertEqual(phases['row-down']['wait']['contains'], ['rows 2/20000', 'bench-row-000001'])
        self.assertEqual(phases['page-down']['selected_row'], 18)
        self.assertEqual(phases['return-first']['effects']['sql_window_counts'], {'0': 2})
        self.assertEqual(phases['window-3']['offset'], 1500)
        self.assertGreater(phases['window-3']['burst_keys'], 1)
        self.assertEqual(source, {'name': 'sql-scroll-20000'})
        for total in (500, 1500):
            cached = bench.sql_case({'name': f'sql-scroll-{total}'}, (120, 40), 'light', options())
            self.assertFalse(cached['bench']['cache_turnover'])
            self.assertNotIn('cache-turnover', [p['kind'] for p in cached['bench']['phases']])

    def test_profiles_filters_long_idle_and_finite_caps(self):
        base = options(case_filters=('sql-editor-*',), themes=('dark', 'light'))
        definition = bench.plan(base)
        self.assertEqual(len(definition['cases']), 2)
        self.assertEqual(definition['session_count'], 20)
        self.assertEqual(definition['pairs'], bench.schedule(definition['cases'], base))
        for pair in definition['pairs']:
            self.assertEqual(set(pair['order']), {'reference', 'candidate'})
        long_idle = bench.campaign_cases(options(case_filters=('*long-idle*',), include_long_idle=True))
        self.assertEqual(len(long_idle), 1)
        idle = next(p for p in long_idle[0]['bench']['phases'] if p['kind'] == 'idle')
        self.assertEqual(idle['duration_ms'], 60000)
        with self.assertRaisesRegex(ValueError, 'no cases'):
            bench.plan(options(case_filters=('*long-idle*',)))
        with self.assertRaisesRegex(ValueError, 'finite cap'):
            bench.plan(options(case_filters=('sql-editor-*',), max_sessions=1))
        for setting in ({'budget_seconds': float('nan')}, {'network_delay_ms': 0},
                        {'sizes': ((50, 20),)}, {'themes': ('dark', 'dark')}, {'bootstrap_draws': 100001}):
            with self.assertRaises(ValueError):
                options(**setting).validate()

    def test_dense_fixture_and_source_definitions_are_owned_copies(self):
        original = next(c for c in bench.workloads.performance_workloads() if c['name'] == 'promql-dense-1000')
        before = copy.deepcopy(original)
        case = bench.dense_case(original, (120, 40), 'dark', options(metric_points=256))
        route = next(r for r in case['fixture']['routes'] if r['path'].endswith('/query_range'))
        rows = json.loads(route['body'])['data']['result']
        self.assertEqual(len(rows), 1000)
        self.assertTrue(all(len(r['values']) == 256 for r in rows))
        self.assertEqual(rows[0]['metric']['job'], 'bench-series-0000')
        self.assertEqual(original, before)
        phases = {p['name']: p for p in case['bench']['phases']}
        self.assertIn('Chart View', phases['chart-view']['wait']['contains'])
        self.assertIn('Table View', phases['chart-view']['wait']['absent'])


class ReadinessTests(unittest.TestCase):
    def terminal(self, text, bytes_count=1, dimensions=(80, 30)):
        terminal = mock.Mock()
        terminal.byte_offset = bytes_count
        terminal.text.return_value = text
        terminal.frame.return_value = frame(text, *dimensions)
        return terminal

    def test_empty_wide_continuation_is_not_a_right_edge_corner(self):
        terminal = self.terminal('body')
        terminal.frame.return_value['cells'][0][-1][0] = ''
        with mock.patch.object(bench.p7, 'useful', return_value=True):
            self.assertFalse(bench.Readiness()(terminal, {'right_edge_corner': True}, [], {}, 1, 1))

    def test_real_emulator_shrink_requires_cell_rerender_not_cursor_output(self):
        terminal = bench.p7.Terminal(100, 30)
        terminal.feed(b'\x1b[?1049hOld body\x1b[1;80H' + '┐'.encode())
        ready = bench.Readiness()
        with mock.patch.object(bench.p7, 'useful', return_value=True):
            self.assertTrue(ready(terminal, {'snapshot_resize': [80, 30]}, [], {}, 1, 1))
            terminal.resize(80, 30)
            wait = {'dimensions': [80, 30], 'right_edge_corner': True,
                    'output_after_action': True, 'resize_rerender': True}
            terminal.feed(b'\x1b[2;1H')  # Fresh bytes and changed cursor, still truncated old cells.
            self.assertFalse(ready(terminal, wait, [], {}, 2, 2))
            terminal.feed(b'\x1b[1;1HNew body')
            self.assertTrue(ready(terminal, wait, [], {}, 2, 3))

    def test_existing_results_does_not_match_new_selected_row(self):
        terminal = self.terminal('RESULTS rows 1/20000 bench-row-000000')
        with mock.patch.object(bench.p7, 'useful', side_effect=lambda t, w, *_: all(s in t.text() for s in w['contains'])):
            ready = bench.Readiness()
            self.assertFalse(ready(terminal, bench.target(1, 20000), [], {}, 1, 1000000000))
            terminal.text.return_value = 'RESULTS rows 2/20000 bench-row-000001'
            self.assertTrue(ready(terminal, bench.target(1, 20000), [], {}, 1, 1000000000))

    def test_resize_geometry_only_is_not_child_output(self):
        ready = bench.Readiness()
        terminal = self.terminal('old', bytes_count=100)
        with mock.patch.object(bench.p7, 'useful', return_value=True):
            self.assertTrue(ready(terminal, {}, [], {}, 1, 1))
            terminal.frame.return_value = frame('new', 100, 35)
            terminal.frame.return_value['cells'][0][-1][0] = '┐'
            wait = {'dimensions': [100, 35], 'right_edge_corner': True, 'output_after_action': True}
            self.assertFalse(ready(terminal, wait, [], {}, 2, 2))
            terminal.byte_offset += 1
            self.assertTrue(ready(terminal, wait, [], {}, 2, 3))

    def test_idle_elapsed_and_network_multiplicity(self):
        ready, terminal = bench.Readiness(), self.terminal('target')
        with mock.patch.object(bench.p7, 'useful', return_value=True):
            wait = {'min_elapsed_ms': 1000, 'sql_window_counts': {'0': 2}}
            self.assertFalse(ready(terminal, wait, [request(0)] * 2, {}, 1, 999000000))
            self.assertTrue(ready(terminal, wait, [], {}, 1, 1100000000))  # No trace polling gate.
            self.assertTrue(ready(terminal, wait, [request(0)] * 2, {}, 1, 1100000000))


class A1GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gzip
        import hashlib
        path = bench.ROOT / 'docs/evidence/phase7/a1-geometry-retained-go.json.gz'
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == 'c713340e3df43b3d2cc96ade055a8f8da535ff855b75c3ec2ca75b247e59ae2c'
        cls.retained = json.loads(gzip.decompress(data))
        assert cls.retained['origins']['session']['sha256'] == 'b0a6919c04a9c157d15101dfabaff64f2e546364e5c841a4d5149aaf1029ce6b'
        assert cls.retained['origins']['stdout']['sha256'] == '2524289b1f62556ade81159f34fb528b18f8b0c12a912dc51357b1f81c9af0fa'
        assert cls.retained['origins']['bench']['sha256'] == 'bf630d9ac4dd4e615f221e218cfce7cbb99438c2de1bf5cfdc388941eef013c2'
        cls.raw = bytes.fromhex(cls.retained['stdout_hex'])
        assert hashlib.sha256(cls.raw).hexdigest() == cls.retained['origins']['stdout']['sha256']
        for key, value in cls.retained['frames'].items():
            assert bench.p7.digest(value) == cls.retained['frame_sha256'][key]
        old = cls.retained['old_predicate_source']
        assert hashlib.sha256(old.encode()).hexdigest() == 'd1344041d774524473d03811bab9009e7fcbe115c34450dd484858001a214fde'
        namespace = {}
        exec(compile(old, '<hash-bound-original-A1-predicate>', 'exec'), namespace)
        cls.old_predicate = staticmethod(namespace['sql_resize_geometry'])

    def terminal(self, large=False):
        terminal = bench.p7.Terminal(120, 40)
        offset = self.retained['resize_byte_offset']
        terminal.feed(self.raw[:offset])
        if large:
            terminal.resize(140, 45)
            terminal.feed(self.raw[offset:])
        return terminal

    def test_actual_frames_reconstruct_exactly_and_kill_compiled_old_predicate(self):
        for large, key, end in ((False, '120x40', 82), (True, '140x45', 99)):
            with self.subTest(size=key):
                terminal = self.terminal(large)
                actual = terminal.frame()
                self.assertEqual(actual, self.retained['frames'][key])
                wait = bench.target(0, 500)
                self.assertEqual(bench.frame_text(actual).splitlines()[actual['rows'] - 6].find('rows 1/500') + 10, end)
                self.assertTrue(bench.sql_resize_geometry(actual, wait))
                self.assertFalse(self.old_predicate(actual, wait))
                with mock.patch.object(bench, 'sql_resize_geometry', self.old_predicate):
                    with self.assertRaises(AssertionError):
                        self.assertTrue(bench.sql_resize_geometry(actual, wait))

    def test_actual_frame_mutations_reject_wrong_columns_rows_marker_borders_and_gap(self):
        for key, original in self.retained['frames'].items():
            w, h = original['columns'], original['rows']
            r = 85 if w == 120 else 102  # Independently observed retained pane widths.
            footer, marker = 'rows 1/500', 'bench-row-000000'
            def blank_token(value, y, token):
                line = ''.join(cell[0] for cell in value['cells'][y])
                start = line.index(token)
                for x in range(start, start + len(token)): value['cells'][y][x][0] = ' '
            def token(value, y, x, text):
                for i, c in enumerate(text): value['cells'][y][x + i][0] = c
            controls = []
            for delta in (-1, 1):
                bad = copy.deepcopy(original); blank_token(bad, h - 6, footer)
                token(bad, h - 6, r - 3 - len(footer) + delta, footer)
                controls.append(('footer-column-' + str(delta), bad))
            bad = copy.deepcopy(original); blank_token(bad, h - 6, footer)
            token(bad, h - 7, r - 3 - len(footer), footer); controls.append(('footer-row', bad))
            bad = copy.deepcopy(original); blank_token(bad, 16, marker)
            token(bad, 17, 13, marker); controls.append(('marker-row', bad))
            bad = copy.deepcopy(original); blank_token(bad, 16, marker)
            token(bad, 16, r + 2, marker); controls.append(('marker-only-in-sidebar', bad))
            for y in (16, h - 6):
                for x in (0, r - 1, r, r + 1, w - 1):
                    bad = copy.deepcopy(original); bad['cells'][y][x][0] = 'X'
                    controls.append((f'border-or-gap-{y}-{x}', bad))
            for label, bad in controls:
                with self.subTest(size=key, control=label):
                    self.assertFalse(bench.sql_resize_geometry(bad, bench.target(0, 500)))
            # The original regression's synthetic full-width closed drawer
            # passes the compiled old predicate and must fail the fixed one.
            closed = frame('', w, h)
            for y in (16, h - 6):
                closed['cells'][y][0][0] = closed['cells'][y][-1][0] = '│'
            token(closed, 16, 13, marker); token(closed, h - 6, w - 3 - len(footer), footer)
            self.assertTrue(self.old_predicate(closed, bench.target(0, 500)))
            self.assertFalse(bench.sql_resize_geometry(closed, bench.target(0, 500)))
            malformed = copy.deepcopy(original); malformed['cells'][0].pop()
            self.assertFalse(bench.sql_resize_geometry(malformed, bench.target(0, 500)))

    def test_actual_growth_and_shrink_reject_old_frame_and_partial_repaint_before_full_target(self):
        tty = {'canonical': False, 'echo': False, 'signals': False}
        trace = [{'path': '/api/v1/query'}]
        for shrink in (False, True):
            terminal = self.terminal(large=shrink)
            w, h = size = (120, 40) if shrink else (140, 45)
            ready = bench.Readiness()
            self.assertTrue(ready(terminal, {'snapshot_resize': list(size)}, trace, tty, 1, 1))
            terminal.resize(*size)
            wait = {**bench.target(0, 500), 'dimensions': list(size), 'sql_resize_geometry': True,
                    'right_edge_corner': True, 'output_after_action': True, 'resize_rerender': True}
            self.assertFalse(ready(terminal, wait, trace, tty, 2, 2))
            terminal.feed(b'\x1b[1;1H' + ('┌' + '─' * (w - 2) + '┐').encode())
            self.assertFalse(ready(terminal, wait, trace, tty, 2, 3))
            expected = self.retained['frames'][f'{w}x{h}']
            terminal.feed(b'\x1b[17;1H' + ''.join(c[0] for c in expected['cells'][16]).encode())
            self.assertFalse(ready(terminal, wait, trace, tty, 2, 4))
            offset = self.retained['resize_byte_offset']
            terminal.feed(self.raw[:offset] if shrink else self.raw[offset:])
            self.assertTrue(ready(terminal, wait, trace, tty, 2, 5))
            # Keep the existing actual-dimensions gate causal even when the
            # retained target and all other clauses qualify.
            self.assertFalse(ready(terminal, {**wait, 'dimensions': [w + 1, h]}, trace, tty, 2, 6))


class A1CaptureOutcomeTests(unittest.TestCase):
    def test_timeout_retained_before_real_downstream_binding_exception_and_abort(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root = Path(folder)
            binary = root / 'binary'; binary.write_bytes(b'not executed'); binary.chmod(0o700)
            identity = bench.p7.phase1.binary_identity(binary)
            case = bench.campaign_cases(options(case_filters=('sql-scroll-500-*',)))[0]
            resize_index = next(i for i, a in enumerate(case['actions']) if 'resize' in a)
            captured = {'binary': identity, 'case': case, 'failure': 'session timeout',
                        'termination': {'signal': 9}, 'resize_signal_policy': bench.RESIZE_SIGNAL_POLICY,
                        'actions': [{'index': resize_index, 'resize': [140, 45], 'resize_signal_source': 'ioctl-only'}]}
            helper = {'source_sha256': 'a' * 64, 'sha256': 'b' * 64}
            with mock.patch.object(bench, 'require_seams'), \
                 mock.patch.object(bench, 'cached_spawn', return_value=helper), \
                 mock.patch.object(bench, 'source_snapshot', return_value={}), \
                 mock.patch.object(bench, 'campaign_cases', return_value=[case]), \
                 mock.patch.object(bench.p7, 'sample', return_value=captured) as sample, \
                 mock.patch.object(bench, 'check_binding', wraps=bench.check_binding) as binding, \
                 mock.patch.object(bench, 'observation_issues', side_effect=AssertionError('validation reordered')) as observations, \
                 mock.patch.object(bench.p7, 'compare', side_effect=AssertionError('continued after binding failure')) as compare:
                report = bench._functional_preflight(binary, binary, root / 'out', options())
            self.assertFalse(report['valid'])
            self.assertFalse(report['cases'][0]['valid'])
            self.assertEqual(sample.call_count, 1)  # Candidate remains unrun.
            binding.assert_called_once_with(captured, identity, case)
            observations.assert_not_called(); compare.assert_not_called()
            self.assertEqual(report['errors'], ["ValueError('captured resize sequence differs from declared script')"])
            role = report['cases'][0]['samples']['reference']
            self.assertEqual(role['capture_failure'], 'session timeout')
            self.assertEqual(role['termination'], {'signal': 9})
            self.assertEqual(role['issues'], ['not completed'])
            self.assertNotIn('candidate', report['cases'][0]['samples'])
            self.assertEqual(json.loads((root / 'out/report.json').read_text()), bench.public_value(report))
            # Remove just the informational assignments from the compiled
            # original function: the same binding error remains, diagnostics vanish.
            source = inspect.getsource(bench._functional_preflight)
            for line in ("                row['samples'][role]['capture_failure'] = result.get('failure')\n",
                         "                row['samples'][role]['termination'] = copy.deepcopy(result.get('termination'))\n"):
                self.assertEqual(source.count(line), 1)
                source = source.replace(line, '')
            namespace = dict(vars(bench))
            exec(compile(source, '<drop-A1-outcome-reporting>', 'exec'), namespace)
            with mock.patch.object(bench, 'require_seams'), \
                 mock.patch.object(bench, 'cached_spawn', return_value=helper), \
                 mock.patch.object(bench, 'source_snapshot', return_value={}), \
                 mock.patch.object(bench, 'campaign_cases', return_value=[case]), \
                 mock.patch.object(bench.p7, 'sample', return_value=captured):
                # Compiled function globals must see the same mocked seams.
                namespace.update({k: getattr(bench, k) for k in ('require_seams', 'cached_spawn', 'source_snapshot', 'campaign_cases')})
                control = namespace['_functional_preflight'](binary, binary, root / 'control', options())
            self.assertEqual(control['errors'], report['errors'])
            self.assertFalse(control['valid'])
            self.assertNotIn('capture_failure', control['cases'][0]['samples']['reference'])
            self.assertNotIn('termination', control['cases'][0]['samples']['reference'])


class R4Tests(unittest.TestCase):
    def test_top_border_first_stale_footer_rejected_growth_shrink_and_identical_row_repaint(self):
        for initial, resized in (((120, 40), (140, 45)), ((140, 45), (120, 40))):
            with self.subTest(initial=initial):
                terminal = bench.p7.Terminal(*initial)
                footer, marker = 'rows 1/20000', 'bench-row-000000'
                def line(width, text):
                    results = max(30, width - (37 if width >= 140 else 34) - 1)
                    return ('│' + text.ljust(results - 2) + '│ │' + ' ' * (width - results - 3) + '│').encode()
                def footer_text(width):
                    results = max(30, width - (37 if width >= 140 else 34) - 1)
                    return ' ' * (results - 4 - len(footer)) + footer
                terminal.feed(b'\x1b[?1049h\x1b[17;1H' + line(initial[0], marker) +
                    f'\x1b[{initial[1]-5};1H'.encode() + line(initial[0], footer_text(initial[0])))
                ready = bench.Readiness()
                ready(terminal, {'snapshot_resize': list(resized)}, [], {'iflag': 0, 'lflag': 0}, 1, 1)
                terminal.resize(*resized)
                wait = {**bench.target(0, 20000), 'dimensions': list(resized), 'right_edge_corner': True,
                        'resize_rerender': True, 'sql_resize_geometry': True, 'output_after_action': True}
                tty = {'cooked': False}
                with mock.patch.object(bench.p7, 'useful', return_value=True):
                    terminal.feed(b'\x1b[1;1H' + ('┌'+'─'*(resized[0]-2)+'┐').encode())
                    self.assertFalse(ready(terminal, wait, [], tty, 2, 3))
                    terminal.feed(b'\x1b[17;1H' + line(resized[0], marker))
                    self.assertFalse(ready(terminal, wait, [], tty, 2, 4))
                    terminal.feed(f'\x1b[{resized[1]-5};1H'.encode()+line(resized[0], footer_text(resized[0])))
                    self.assertTrue(ready(terminal, wait, [], tty, 2, 5))

    def test_raw_offset_includes_partial_csi_and_utf8_and_prefix_retains_resize_state(self):
        terminal=bench.p7.Terminal(80,30)
        prefix=b'hello\x1b[31'+ '日'.encode()[:1]
        terminal.feed(prefix)
        self.assertNotEqual(terminal.byte_offset,len(prefix))
        self.assertEqual(bench.raw_observed_offset(terminal),len(prefix))
        ready=bench.Readiness();ready(terminal,{},[],{},1,10)
        self.assertEqual(ready.offsets[1]['byte_offset'],len(prefix))
        # A real resize occurs at a precise prefix boundary, before measured
        # payload. Full replay includes that local state mutation.
        prefix=b'\x1b[?1049hhello'
        payload=b'\x1b[1;1Hnew'
        terminal=bench.p7.Terminal(80,30);terminal.feed(prefix);terminal.resize(100,35);terminal.feed(payload)
        result={'case':{'size':[80,30],'theme':'dark','resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,
                       'actions':[{'resize':[100,35]}]},'stdout':prefix+payload,
            'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,
            'actions':[{'index':0,'resize':[100,35],'resize_signal_source':'ioctl-only'}],
            'frames':[{'name':'resize','first_useful_monotonic_ns':20,'bytes_since_action':len(payload),'frame':terminal.frame()}],
            'decoder_offsets':{'schema_version':1,'actions':[{'index':0,'byte_offset':len(prefix)}],
                'frames':[{'name':'resize','first_offset':len(prefix+payload),'end_offset':len(prefix+payload),'first_monotonic_ns':20}]}}
        phase={'name':'resize','kind':'resize','trigger_action_index':0,'wait':{'contains':['new'],'dimensions':[100,35]}}
        unit_evaluation_history(result,[phase])
        self.assertEqual(bench.decode_phase(result,phase,repetitions=1,clock=lambda:0)['status'],'valid')
        result['decoder_offsets']['frames'][0]['first_offset']=len(prefix)
        with self.assertRaisesRegex(ValueError,'first-match|readiness boundary'):
            bench.decode_phase(result,phase,repetitions=1,clock=lambda:0)

    def test_dense_sustained_distinct_series_selection_and_single_request(self):
        case = bench.campaign_cases(options(case_filters=('promql-dense-1000-sustained-*',)))[0]
        phases = [phase for phase in case['bench']['phases'] if phase['kind'] == 'series-scroll']
        self.assertGreaterEqual(len({phase['selected_series'] for phase in phases}), 63)
        self.assertEqual(next(p for p in phases if p['name'] == 'series-last')['selected_series'], 999)
        for phase in phases:
            self.assertIn(f'{phase["selected_row"]+1} | 256000 rows', phase['wait']['contains'])
            self.assertEqual(phase['effects']['request_exact'], {'/prometheus/api/v1/query_range': 1})
        with self.assertRaisesRegex(ValueError, 'not signals'):
            bench.Phases().change('signal', 'scroll', {'signal': 'SIGWINCH'}, {})

    def test_identical_aa_truthful_language_and_reject_copy_metadata_and_preflight_class(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root = Path(folder)
            binary, meta = root/'go', root/'meta.json'
            binary.write_bytes(b'not executed');binary.chmod(0o700)
            bench.write_json(meta, {'binary_sha256':bench.sha_file(binary),'optimized':True,'language':'go',
                'profile':'release','source_commit':bench.PROVENANCE_COMMIT,'source_tree_sha256':'a'*64})
            artifacts=bench.comparison_artifacts(binary,binary,meta,meta,'aa-go')
            self.assertEqual(artifacts['reference'], artifacts['candidate'])
            other=root/'copy';other.write_bytes(binary.read_bytes());other.chmod(0o700)
            with self.assertRaisesRegex(ValueError,'identical canonical'):
                bench.comparison_artifacts(binary,other,meta,meta,'aa-go')
            other_meta=root/'meta-copy.json';other_meta.write_bytes(meta.read_bytes())
            with self.assertRaisesRegex(ValueError,'identical canonical'):
                bench.comparison_artifacts(binary,binary,meta,other_meta,'aa-go')
            pref=root/'pref.json';bench.write_json(pref, {'comparison':'go-rust'})
            with self.assertRaisesRegex(ValueError,'comparison class'):
                bench.validate_preflight(pref, {'comparison':'aa-go'}, artifacts)
            result=bench.plan(options(comparison='aa-go',case_filters=('sql-editor-*',)))
            self.assertFalse(result['budget_evidence'])
            self.assertTrue(all(case['name'].endswith('-aa-go') for case in result['cases']))

    def test_functional_admission_contention_prevents_preparation_and_releases(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);lock=root/'lock'
            with lock.open('a+') as held:
                fcntl.flock(held, fcntl.LOCK_EX|fcntl.LOCK_NB)
                with mock.patch.object(bench,'_functional_preflight', side_effect=AssertionError('preparation')), self.assertRaises(BlockingIOError):
                    bench.functional_preflight('no','no',root/'out',options(),lock_path=lock)
            with self.assertRaises(RuntimeError):
                with bench.FunctionalOwnership(lock):
                    raise RuntimeError('owned failure')
            with bench.FunctionalOwnership(lock):pass

    def test_replay_reconstructs_partial_utf8_sgr_and_rejects_missing_offsets_or_frame(self):
        prefix=b'\x1b[?1049h\x1b[31m'+'日'.encode()[:1]
        phase_bytes='日'.encode()[1:]+b' SELECT'
        payload=prefix+phase_bytes
        terminal=bench.p7.Terminal(80,30);terminal.feed(payload)
        phase={'name':'edit','kind':'editor','trigger_action_index':0}
        result={'case':{'size':[80,30],'theme':'dark','resize_signal_policy':bench.RESIZE_SIGNAL_POLICY},'stdout':payload,
            'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,
            'actions':[{'index':0}], 'frames':[{'name':'edit','first_useful_monotonic_ns':20,
                'bytes_since_action':len(phase_bytes),'frame':terminal.frame()}],
            'decoder_offsets':{'schema_version':1,'actions':[{'index':0,'byte_offset':len(prefix)}],
                'frames':[{'name':'edit','first_offset':len(payload),'end_offset':len(payload),'first_monotonic_ns':20}]}}
        clocks=iter([0,1000000]*5)
        unit_evaluation_history(result,[phase])
        decoded=bench.decode_phase(result,phase,clock=lambda:next(clocks))
        self.assertEqual(decoded['observer_replay_ms'],[1]*5)
        self.assertEqual(decoded['first_bytes'],len(phase_bytes))
        result['frames'][0]['frame']['cells'][0][0][0]='X'
        with self.assertRaisesRegex(ValueError,'reconstructed'):
            bench.decode_phase(result,phase,repetitions=1,clock=lambda:0)
        result.pop('decoder_offsets')
        with self.assertRaises(KeyError):bench.decode_phase(result,phase,clock=lambda:0)

    def test_both_role_phase_diagnostics_bind_retained_bytes_and_reject_mutated_control(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder)
            case={'name':'decode-unit','argv':['sql','-i'],'size':[80,30],'theme':'dark','bench':{
                'observer_policy':'primary','comparison':'go-rust','phases':[
                    {'name':'startup','kind':'startup','trigger_action_index':None,'wait':{'contains':['HELLO']}},
                    {'name':'edit','kind':'editor','trigger_action_index':0,'wait':{'contains':['SELECT']}},
                    {'name':'resize','kind':'resize','trigger_action_index':1,'wait':{'contains':['RESIZED'],'dimensions':[100,35]}}]}}
            case['resize_signal_policy']=bench.RESIZE_SIGNAL_POLICY
            case['actions']=[{'send':'SELECT'},{'resize':[100,35]}]
            pref={'cases':[{'case':case['name'],'samples':{}}],'artifacts':{},'native_spawn':{},
                  'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY}
            for role in ('reference','candidate'):
                prefix=b'\x1b[?1049hHELLO'+(b'\x1b[0m' if role=='candidate' else b'')
                change=b'\x1b[2;1HSELECT'
                terminal=bench.p7.Terminal(80,30);terminal.feed(prefix);initial=terminal.frame();terminal.feed(change)
                edited=terminal.frame();terminal.resize(100,35);resize=b'\x1b[1;1HRESIZED';terminal.feed(resize)
                binary=root/(role+'-binary');binary.write_bytes(role.encode());binary.chmod(0o700)
                identity=bench.p7.phase1.binary_identity(binary);pref['artifacts'][role]=identity
                raw={'case':case,'binary':identity,'stdout':prefix+change+resize,'stderr':b'','post_screen_stdout':b'',
                    'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,
                    'actions':[{'index':0},{'index':1,'resize':[100,35],'resize_signal_source':'ioctl-only'}], 'frames':[
                        {'name':'startup','frame':initial,'first_useful_monotonic_ns':5,'bytes_since_action':len(prefix)},
                        {'name':'edit','frame':edited,'first_useful_monotonic_ns':20,'bytes_since_action':len(change)},
                        {'name':'resize','frame':terminal.frame(),'first_useful_monotonic_ns':30,'bytes_since_action':len(resize)}],
                    'decoder_offsets':{'schema_version':1,'actions':[{'index':0,'byte_offset':len(prefix)},
                        {'index':1,'byte_offset':len(prefix+change)}],
                        'frames':[{'name':'startup','first_offset':len(prefix),'end_offset':len(prefix),'first_monotonic_ns':5},
                                  {'name':'edit','first_offset':len(prefix+change),'end_offset':len(prefix+change),'first_monotonic_ns':20},
                                  {'name':'resize','first_offset':len(prefix+change+resize),'end_offset':len(prefix+change+resize),'first_monotonic_ns':30}]}}
                unit_evaluation_history(raw,case['bench']['phases'])
                bench.archive_execute(raw,root/role/'capture')
                pref['cases'][0]['samples'][role]={'directory':role+'/capture',**bench.capture_file_hashes(root/role/'capture')}
            pref_path=root/'pref.json';bench.write_json(pref_path,pref)
            with mock.patch.object(bench,'campaign_cases',return_value=[case]),mock.patch.object(bench,'source_snapshot',return_value={}), \
                 mock.patch.object(bench,'offline_preflight_check',side_effect=lambda _path,_definition,report:report):
                diagnostic=bench.decoder_diagnostics(pref_path,root/'diagnostic',options(),mock.Mock(),clock=lambda:0)
            self.assertTrue(diagnostic['valid'],diagnostic['errors'])
            self.assertNotEqual(diagnostic['cases'][0]['roles']['reference']['phases']['startup']['first_bytes'],
                                diagnostic['cases'][0]['roles']['candidate']['phases']['startup']['first_bytes'])
            pref['validated_report_identity']=bench.json_snapshot(pref_path)[1]
            definition={'cases':[case],'observer_policy':'primary','comparison':'go-rust'}
            report_path=root/'diagnostic/report.json'
            with mock.patch.object(bench,'source_snapshot',return_value={}):
                with self.assertRaisesRegex(ValueError,'preflight/feed-only'):
                    bench.decoder_interpretation(report_path,pref,definition)
                for mutation in ('bytes','policy','source'):
                    altered=copy.deepcopy(diagnostic)
                    if mutation=='bytes':
                        altered['cases'][0]['roles']['candidate']['phases']['edit']['accepted_bytes_sha256']='a'*64
                    elif mutation=='policy':altered['resize_signal_policy']='legacy'
                    else:altered['cases'][0]['roles']['candidate']['phases']['resize']['resize_signal_source']='ioctl+explicit'
                    bench.write_report(report_path,altered)
                    with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                        bench.decoder_interpretation(report_path,pref,definition)

    def test_offset_hook_binding_and_attribution_latency_never_eligible(self):
        terminal=bench.p7.Terminal(80,30);terminal.feed(b'hello')
        ready=bench.Readiness()
        ready(terminal,{},[],{},1,10)
        result={'actions':[{'index':0,'evaluated_monotonic_ns':10}],
            'frames':[{'name':'startup','action_started_monotonic_ns':1,'ready_monotonic_ns':10}]}
        bench.bind_decoder_offsets(result,ready)
        self.assertEqual(result['decoder_offsets']['actions'][0]['byte_offset'],5)
        self.assertEqual(result['decoder_offsets']['frames'][0]['first_offset'],5)
        case,raw=synthetic();case['bench']['observer_policy']='resource-attribution'
        self.assertFalse(bench.extract_metrics(raw,case)['latency_trigger_eligible'])
        self.assertFalse(bench.decoder_interpretation('',{}, {})['eligible'])

    def test_feasibility_missing_fullwall_is_invalid_and_declared_reserves_visible(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            path=Path(folder)/'pref.json'
            case,_=synthetic()
            pref={'valid':True,'case_hashes':{'synthetic':bench.p7.digest(case)},'cases':[{'case':'synthetic',
                'samples':{role:{'scheduling_wall_ms':2000} for role in ('reference','candidate')}}]}
            bench.write_json(path,pref)
            with mock.patch.object(bench,'plan',return_value={'cases':[case],'pairs':[{}]*3,'control_session_count':4}), mock.patch.object(bench,'offline_preflight_check',side_effect=lambda _path,_definition,report:report):
                result=bench.campaign_feasibility(path,options())
                self.assertEqual(result['floor_reserve_seconds_each'],30)
                self.assertEqual(result['cleanup_margin_seconds_per_role'],8)
                self.assertIn('not CLI speed',result['scope'])
                pref['cases'][0]['samples']['candidate'].pop('scheduling_wall_ms');bench.write_json(path,pref)
                with self.assertRaisesRegex(ValueError,'scheduling cost missing'):
                    bench.campaign_feasibility(path,options())


class ResizePolicyTests(unittest.TestCase):
    def capture(self):
        case={'name':'resize-policy-unit','argv':['sql','-i'],'size':[80,30],
              'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,
              'actions':[{'resize':[100,35]},{'resize':[120,40]}]}
        raw={'case':case,'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,
             'actions':[{'index':index,'resize':action['resize'],'resize_signal_source':'ioctl-only'}
                        for index,action in enumerate(case['actions'])],
             'frames':[],'stdout':b'retained raw bytes','stderr':b'',
             'post_screen_stdout':b'','metrics':{},'failure':None}
        return case,raw

    def test_all_primary_attribution_and_profile_definitions_bind_changed_only(self):
        import inspect
        self.assertEqual(inspect.signature(bench.p7.execute).parameters['resize_signal_policy'].default,'legacy')
        for policy in ('primary','resource-attribution'):
            cases=bench.campaign_cases(options(observer_policy=policy,include_long_idle=True,long_idle_seconds=300))
            self.assertEqual(len(cases),13)
            self.assertTrue(all(case['resize_signal_policy']==bench.RESIZE_SIGNAL_POLICY for case in cases))

    def test_policy_event_source_order_geometry_and_script_drift_fail_closed(self):
        case,raw=self.capture()
        self.assertEqual(bench.resize_policy_issues(raw,case),[])
        for mutation in ('legacy','missing-policy','missing-case','source','missing-source','order','omitted','geometry','no-op','bounds'):
            altered_case,altered=copy.deepcopy(case),copy.deepcopy(raw)
            if mutation=='legacy':altered['resize_signal_policy']='legacy'
            elif mutation=='missing-policy':altered.pop('resize_signal_policy')
            elif mutation=='missing-case':altered_case.pop('resize_signal_policy')
            elif mutation=='source':altered['actions'][0]['resize_signal_source']='ioctl+explicit'
            elif mutation=='missing-source':altered['actions'][0].pop('resize_signal_source')
            elif mutation=='order':altered['actions'].reverse()
            elif mutation=='omitted':altered['actions'].pop()
            elif mutation=='geometry':altered['actions'][0]['resize']=[101,35]
            elif mutation=='no-op':altered['actions'][0]['resize']=[80,30]
            else:altered['actions'][0]['resize']=[True,65536]
            with self.subTest(mutation=mutation):
                self.assertTrue(bench.resize_policy_issues(altered,altered_case))

    def test_every_capture_adapter_passes_policy_and_retains_legacy_failure(self):
        usage={'user_ms':0,'system_ms':0,'rss_bytes':1,'lifetime_peak_rss_bytes':1}
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder)
            for policy in ('primary','resource-attribution','floor-minimal','recorded-replay'):
                case,raw=self.capture();evidence=root/policy
                if policy=='recorded-replay':
                    adapter=bench.ReplayCapture(mock.Mock(),policy='floor-minimal',minimal=True,
                        evidence_path=evidence,replay={'bytes':b'prefix'},frames=2)
                else:
                    adapter=bench.Capture(mock.Mock(),policy=policy,minimal=policy=='floor-minimal',evidence_path=evidence)
                with mock.patch.object(bench,'self_usage',return_value=usage),mock.patch.object(bench.p7,'execute',return_value=raw) as execute:
                    result=adapter(['never-run'],{},root,case)
                self.assertEqual(execute.call_args.kwargs['resize_signal_policy'],bench.RESIZE_SIGNAL_POLICY)
                self.assertFalse(execute.call_args.kwargs['spawn_helper_build'])
                self.assertIsNone(result['failure'])
            case,raw=self.capture();raw['resize_signal_policy']='legacy'
            with mock.patch.object(bench,'self_usage',return_value=usage),mock.patch.object(bench.p7,'execute',return_value=raw):
                result=bench.Capture(mock.Mock(),evidence_path=root/'legacy')(['never-run'],{},root,case)
            self.assertIn('legacy cannot enable triggers',result['failure'])
            self.assertEqual((root/'legacy/stdout.bin').read_bytes(),raw['stdout'])
            self.assertEqual(json.loads((root/'legacy/session.json').read_bytes())['resize_signal_policy'],'legacy')


class R5ObserverTests(unittest.TestCase):
    def raw(self, evaluations=1, nonce=1):
        """Real emulator/predicate replay, synthetic clocks/resources; never a CLI."""
        case, raw = synthetic()
        case.update(size=[80,30],theme='dark')
        wait={'contains':['FINAL'],'alternate':True,'cooked':False,'output_after_action':True}
        phase={'name':'edit','kind':'editor','trigger_action_index':0,'wait':wait}
        case['actions']=[{'send':'X','wait':wait,'checkpoint':'edit'}]
        case['bench']['phases']=[phase]
        prefix=b'\x1b[?1049hOLD'; change=b'\x1b[1;1HFINAL'
        terminal=bench.p7.Terminal(80,30);terminal.feed(prefix)
        ready=bench.Readiness();ready.last_bytes=terminal.byte_offset
        delta=nonce*2000000000; started=delta+10000000
        for index in range(evaluations):
            if index==evaluations-1:terminal.feed(change)
            now=delta+120000000+index*20000000
            self.assertEqual(ready(terminal,wait,[],{'canonical':False,'echo':False,'signals':False},started,now),index==evaluations-1)
        first=now;admitted=first+90000000
        ready(terminal,wait,[],{'canonical':False,'echo':False,'signals':False},started,admitted)
        raw.update(case=case,stdout=prefix+change,stderr=b'',post_screen_stdout=b'',
            started_monotonic_ns=delta,reaped_monotonic_ns=delta+1500000000,exited_monotonic_ns=delta+1500000000,
            actions=[{'index':0,'monotonic_ns':started,'evaluated_monotonic_ns':started,'dispatch_monotonic_ns':started+5000000,'input_hex':'58'}],
            frames=[{'name':'edit','frame':terminal.frame(),'action_started_monotonic_ns':started,
                'first_useful_monotonic_ns':first,'first_ever_match_monotonic_ns':first,'stable_since_monotonic_ns':first+10000000,
                'last_output_monotonic_ns':first,'ready_monotonic_ns':admitted,'bytes_since_action':len(change)}])
        for row in raw['metrics']['proc_samples']:row['monotonic_ns']+=delta
        entry=copy.deepcopy(ready.offsets[started])
        raw['decoder_offsets']={'schema_version':2,'actions':[{'index':0,'byte_offset':len(prefix)}],
            'frames':[{'name':'edit','first_offset':len(prefix+change),'end_offset':len(prefix+change),
                'first_monotonic_ns':first,**{key:entry.get(key) for key in ('action_started_monotonic_ns','baseline_bytes','wait','evaluation_count','evaluations','recording_error')}}]}
        return case,raw

    def test_role_dependent_evaluations_same_bytes_replay_real_predicates(self):
        _case,one=self.raw(1);_case,many=self.raw(12)
        self.assertEqual(one['stdout'],many['stdout'])
        original=bench.Readiness._qualify
        work=[0]
        def charged(instance,*args):
            work[0]+=500000
            return original(instance,*args)
        with mock.patch.object(bench.Readiness,'_qualify',charged):
            a=bench.decode_phase(one,one['case']['bench']['phases'][0],clock=lambda:work[0])
            b=bench.decode_phase(many,many['case']['bench']['phases'][0],clock=lambda:work[0])
        self.assertEqual(a['evaluation_count'],1);self.assertEqual(b['evaluation_count'],12)
        self.assertEqual(a['observer_replay_ms'],[.5]*5);self.assertEqual(b['observer_replay_ms'],[6]*5)
        self.assertGreater(b['observer_replay_ms'][0]-a['observer_replay_ms'][0],1)
        # These injected costs demonstrate causality, never a measured control.
        for mutation in ('tty','count','boundary','wait','legacy'):
            altered=copy.deepcopy(many);stamp=altered['decoder_offsets']['frames'][0]
            if mutation=='tty':stamp['evaluations'][-1]['tty']['canonical']=True
            elif mutation=='count':stamp['evaluation_count']-=1
            elif mutation=='boundary':stamp['evaluations'][-1]['byte_offset']-=1
            elif mutation=='wait':stamp['wait']['contains']=['OLD']
            else:altered['decoder_offsets']['schema_version']=1
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                bench.decode_phase(altered,altered['case']['bench']['phases'][0],clock=lambda:0)

    def test_recorder_exhaustion_is_retained_noninterpretable_not_zero(self):
        with mock.patch.object(bench,'MAX_READINESS_EVALUATIONS',1):
            _case,raw=self.raw(3)
        stamp=raw['decoder_offsets']['frames'][0]
        self.assertEqual(stamp['evaluation_count'],3)
        self.assertIn('exhausted',stamp['recording_error'])
        with self.assertRaisesRegex(ValueError,'incomplete'):
            bench.decode_phase(raw,raw['case']['bench']['phases'][0],clock=lambda:0)

    def test_dense_partial_top_and_wrong_footer_rejected_before_full_target(self):
        terminal=bench.p7.Terminal(120,40)
        wait={'contains':['Table View','bench-series-0000','1 | 256000 rows'],'dense_table_geometry':True,'alternate':True}
        terminal.feed(b'\x1b[?1049hTable View\r\nbench-series-0000\r\n1 | 256000 rows')
        ready=bench.Readiness()
        self.assertFalse(ready(terminal,wait,[],{},1,100))
        footer='1 | 256000 rows';column=120-3-len(footer);blank='│'+' '*118+'│'
        def paint(row,text):
            terminal.feed((f'\x1b[{row+1};1H'+text).encode())
        def footer_row(row):
            paint(row,'│'+' '*(column-1)+footer+'  │')
        footer_row(34);paint(35,'└'+'─'*118+'┘');paint(37,'  <t> Toggle chart/table       <ctrl-r> Run                 <ctrl-c> Quit'+' '*47)
        self.assertFalse(ready(terminal,wait,[],{},1,200))  # Historical H-6 footer layout.
        paint(34,blank);paint(33,blank);footer_row(32)
        self.assertTrue(ready(terminal,wait,[],{},1,300))   # Actual A1-retry Go/Rust layout.
        paint(37,'  <ctrl-r> Run'+' '*40)
        self.assertFalse(ready(terminal,wait,[],{},1,400))  # Stale chart-mode hint bar.

    def campaign(self,root,*,sessions=2,warmups=0,equal_counts=False,comparison='go-rust',policy='primary'):
        case,_raw=self.raw();source={'unit':'r5-no-child'}
        case['bench'].update(observer_policy=policy,comparison=comparison)
        helper_file=root/'helper';helper_file.write_bytes(b'unit helper never executed')
        sidecar=root/'helper.json';sidecar.write_text('{}')
        helper={'path':str(helper_file),'sha256':bench.sha_file(helper_file),'provenance_path':str(sidecar),
            'provenance_sha256':bench.sha_file(sidecar),'source_sha256':bench.sha_file(bench.ROOT/'harness/phase7_spawn.c')}
        artifacts={}
        for role,language in (('reference','go'),('candidate','rust')):
            binary=root/role;binary.write_bytes(('never executed '+role).encode());binary.chmod(0o700)
            meta=root/(role+'.json');bench.write_json(meta,{'binary_sha256':bench.sha_file(binary),'optimized':True,
                'language':language,'profile':'release','source_commit':bench.PROVENANCE_COMMIT if language=='go' else 'a'*40,'source_tree_sha256':'b'*64})
            artifacts[role]=bench.artifact(binary,meta,role)
        pref=root/'preflight.json';bench.write_json(pref,{'unit_fixture':True})
        if comparison=='aa-go':artifacts['candidate']=copy.deepcopy(artifacts['reference'])
        opt=options(sessions=sessions,warmups=warmups,comparison=comparison,observer_policy=policy,
                    campaign_id='original-unit-campaign',preflight_report=str(pref))
        with mock.patch.object(bench,'campaign_cases',return_value=[case]):definition=bench.plan(opt)
        definition.update(native_spawn=helper,preflight_report_identity=bench.json_snapshot(pref)[1])
        approval={'approved':True,'scope':'phase7','campaign_id':opt.campaign_id,'issued_unix':0,'expires_unix':600,
            'companions':{'phase6':{'agreed':True,'campaign_id':opt.campaign_id,'by':'unit','reference':'unit-only','at_unix':0}}}
        pairs=[];nonce=0
        for index,scheduled in enumerate(bench.schedule([case],opt)):
            pair={**scheduled,'status':'valid','samples':{}}
            for role in ('reference','candidate'):
                nonce+=1;_case,raw=self.raw(1 if role=='reference' or equal_counts else 3+index,nonce)
                raw.update(case=case,binary=artifacts[role]['identity'],capture_command=[artifacts[role]['identity']['path'],*case['argv']],observer_policy=policy)
                raw['metrics']['native_spawn']=helper
                directory=f'sessions/{index}/{role}';bench.archive_execute(raw,root/directory)
                pair['samples'][role]={'artifact':directory,'valid':True,**bench.capture_file_hashes(root/directory),
                    'capture_boundary':{'started_unix':100+raw['started_monotonic_ns']/1e9,'finished_unix':100+raw['reaped_monotonic_ns']/1e9,
                        'started_monotonic_ns':raw['started_monotonic_ns']-1,'finished_monotonic_ns':raw['reaped_monotonic_ns']+1,'approval':approval}}
            pairs.append(pair)
        controls=[]
        for kind in ('tiny-frame','recorded-frame'):
            modes={}
            for mode in (policy,'minimal'):
                relative='unit-'+mode;directory='observer-controls/'+kind+'/'+relative+'/capture'
                raw={'stdout':b'FRAME 0000','stderr':b''.join(f'{i} {i+1}\n'.encode() for i in range(opt.floor_frames)),
                    'post_screen_stdout':b'','failure':None,'termination':{'exit':0},'termios_restored':True,
                    'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,'metrics':{'native_spawn':helper},
                    'frames':[{'frame':frame(f'FRAME {i:04d}'),'first_useful_monotonic_ns':i+2} for i in range(opt.floor_frames)]}
                bench.archive_execute(raw,root/directory)
                modes[mode]={'valid':True,'evidence':relative,'retained_capture':bench.capture_file_hashes(root/directory)}
            controls.append({'kind':kind,'status':'valid','modes':modes})
        controls_path='observer-controls/controls-progress.json';bench.write_json(root/controls_path,controls)
        report={'status':'complete','errors':[],'source':source,'source_after':source,'artifacts':artifacts,
            'quiet_window':approval,'quiet_window_end':approval,'campaign_boundary':{'started_unix':100,'finished_unix':100+nonce*2+2,
                'started_monotonic_ns':1,'finished_monotonic_ns':nonce*2000000000+2000000000},'definition':definition,'pairs':pairs,
            'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,'observer_controls':controls,'observer_controls_evidence':controls_path,
            'artifacts_after':{role:item['identity'] for role,item in artifacts.items()},'native_spawn_after':helper,
            'metadata_after':{role:{'path':item['provenance_path'],'sha256':item['provenance_file_sha256'],
                'size_bytes':item['provenance_size_bytes']} for role,item in artifacts.items()}}
        path=root/'report.json';bench.write_json(path,report)
        return path,case,source

    def replay_context(self,case,source):
        from contextlib import ExitStack
        stack=ExitStack()
        for target,name,value in ((bench,'source_snapshot',source),(bench,'campaign_cases',[case]),
                                  (bench.p7,'validate',[]),(bench.p7,'compare',[])):
            stack.enter_context(mock.patch.object(target,name,return_value=value))
        stack.enter_context(mock.patch.object(bench,'offline_preflight_check',side_effect=lambda path,_definition,report:
            {**report,'validated_report_identity':bench.json_snapshot(path)[1]}))
        return stack

    def plan(self,path,root,*,budget_seconds=600):
        output=root/'structural-plan.json'
        if not output.exists():bench.diagnostic_plan(path,output,budget_seconds=budget_seconds)
        return output

    def diagnostic_guard(self,*,budget_seconds=600):
        now=time.time();identifier='separate-unit-diagnostic'
        return mock.Mock(budget=budget_seconds,approval={'approved':True,'scope':'phase7','campaign_id':identifier,
            'issued_unix':now-1,'expires_unix':now+budget_seconds-1,'companions':{'phase6':{'agreed':True,'campaign_id':identifier,
                'by':'unit','reference':'unit-only','at_unix':now-1}}})

    def test_actual_campaign_decode_interpret_retains_all_roles_and_collapses_repetitions(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);path,case,source=self.campaign(root);original=path.read_bytes()
            with self.replay_context(case,source):
                diagnostic=bench.campaign_diagnostics(path,root/'diagnostic',self.diagnostic_guard(),clock=lambda:0,plan_path=self.plan(path,root))
                self.assertTrue(diagnostic['valid'],diagnostic['errors'])
                self.assertEqual(len(diagnostic['sessions']),4)
                self.assertEqual(sorted(row['phases']['edit']['evaluation_count'] for row in diagnostic['sessions']),[1,1,3,4])
                dpath=root/'diagnostic/report.json'
                injected=bench.interpret_campaign(path,dpath,root/'injected.json')
                self.assertFalse(injected['valid']);self.assertIn('injected',injected['errors'][0])
                # Synthetic protocol fixture only: no timer evidence is asserted.
                diagnostic['observer_clock']['injected_test_clock']=False
                for row in diagnostic['sessions']:
                    row['phases']['edit']['observer_replay_ms']=[.2,.3,.4,.5,.6] if row['role']=='reference' else [.4,.5,.6,.7,.8]
                bench.write_report(dpath,diagnostic)
                derived=bench.interpret_campaign(path,dpath,root/'derived.json')
                self.assertTrue(derived['valid'],derived['errors'])
                self.assertFalse(derived['latency_trigger_eligible']) # two pairs cannot become5+30
                data=derived['latency_interpretation']['synthetic']['edit']
                self.assertEqual(data['paired_sessions'],2)
                self.assertAlmostEqual(data['role_median_observer_delta_ms'],.2)
                self.assertEqual(path.read_bytes(),original)
                for mutation in ('preflight','wrong-campaign','omitted-role','duplicate-role','missing-phase','phase-binding','failed-phase','approval','reused-id','nan'):
                    changed=copy.deepcopy(diagnostic)
                    if mutation=='preflight':changed['kind']='per-role phase decoder replay diagnostic'
                    elif mutation=='wrong-campaign':changed['campaign_identity']['sha256']='a'*64
                    elif mutation=='omitted-role':changed['sessions'].pop()
                    elif mutation=='duplicate-role':changed['sessions'][-1]=copy.deepcopy(changed['sessions'][0])
                    elif mutation=='missing-phase':changed['sessions'][0]['phases']={}
                    elif mutation=='phase-binding':changed['sessions'][0]['phases']['edit']['evaluation_count']+=1
                    elif mutation=='failed-phase':changed['sessions'][0]['phases']['edit'].update(status='invalid',observer_replay_ms=None)
                    elif mutation=='approval':changed['quiet_window']['expires_unix']=changed['quiet_window']['issued_unix']
                    elif mutation=='reused-id':changed['diagnostic_campaign_id']='original-unit-campaign'
                    else:changed['sessions'][0]['phases']['edit']['observer_replay_ms'][0]=float('nan')
                    bench.write_report(dpath,changed)
                    failed=bench.interpret_campaign(path,dpath,root/('invalid-'+mutation+'.json'))
                    with self.subTest(mutation=mutation):
                        self.assertFalse(failed['valid']);self.assertIsNone(failed['summary']);self.assertFalse(failed['latency_trigger_eligible']);self.assertTrue(failed['errors'])
                self.assertEqual(path.read_bytes(),original)

    def test_campaign_decode_failure_and_control_end_drift_retain_raw(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);path,case,source=self.campaign(root)
            original=path.read_bytes()
            with self.replay_context(case,source):
                real=bench.decode_phase;calls=[0]
                def fail_one(*args,**kwargs):
                    calls[0]+=1
                    if calls[0]==2:raise ValueError('retained interrupted reconstruction')
                    return real(*args,**kwargs)
                with mock.patch.object(bench,'decode_phase',side_effect=fail_one):
                    report=bench.campaign_diagnostics(path,root/'failure',self.diagnostic_guard(),clock=lambda:0,plan_path=self.plan(path,root))
                self.assertFalse(report['valid']);self.assertEqual(len(report['sessions']),4)
                failed=[row for row in report['sessions'] if row['phases']['edit']['status']=='invalid']
                self.assertEqual(len(failed),1);self.assertIsNone(failed[0]['phases']['edit']['observer_replay_ms'])
                self.assertIn('interrupted',failed[0]['phases']['edit']['error'])
                stream=root/'observer-controls/tiny-frame/unit-primary/capture/stdout.bin'
                def drift(*args,**kwargs):
                    result=real(*args,**kwargs);stream.write_bytes(b'end drift');return result
                with mock.patch.object(bench,'decode_phase',side_effect=drift):
                    report=bench.campaign_diagnostics(path,root/'drift',self.diagnostic_guard(),clock=lambda:0,plan_path=self.plan(path,root))
                self.assertFalse(report['valid']);self.assertIn('evidence changed',report['errors'][0])
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(len(list(root.glob('sessions/*/*/session.json'))),4)

    def test_retained_preflight_mutation_during_replay_fails_own_end_binding(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);path,case,source=self.campaign(root)
            original=path.read_bytes();real=bench.decode_phase
            with self.replay_context(case,source):
                def mutate(*args,**kwargs):
                    result=real(*args,**kwargs)
                    (root/'preflight.json').write_text('{"changed_during_replay":true}')
                    return result
                with mock.patch.object(bench,'decode_phase',side_effect=mutate):
                    report=bench.campaign_diagnostics(path,root/'pref-drift',self.diagnostic_guard(),clock=lambda:0,plan_path=self.plan(path,root))
                self.assertFalse(report['valid']);self.assertTrue(report['errors'])
                self.assertIn('parsed JSON evidence identity/path changed',report['errors'][0])
                self.assertNotIn('end_bindings',report)
                self.assertEqual(len(report['sessions']),4)
            self.assertEqual(path.read_bytes(),original)

    def test_cli_orders_separately_guarded_diagnostic_then_new_interpretation(self):
        guard=mock.Mock()
        context=mock.MagicMock();context.__enter__.return_value=guard
        with mock.patch.object(bench,'QuietWindow',return_value=context) as quiet, \
             mock.patch.object(bench,'campaign_diagnostics',return_value={'valid':True}) as decode, \
             mock.patch.object(bench,'interpret_campaign',return_value={'valid':True}) as interpret, \
             mock.patch.object(bench,'cached_spawn',side_effect=AssertionError('no helper preparation')), \
             mock.patch.object(bench,'FunctionalOwnership',return_value=mock.MagicMock()) as ownership:
            self.assertEqual(bench.main(['campaign-decode','--report','original.json','--out','new-diagnostic',
                '--quiet-window','fresh.json','--campaign-id','separate-id','--budget-seconds','123','--decoder-repetitions','7',
                '--diagnostic-plan','structural.json']),0)
            quiet.assert_called_once_with(Path('fresh.json'),123,campaign_id='separate-id')
            decode.assert_called_once_with(Path('original.json'),Path('new-diagnostic'),guard,repetitions=7,plan_path=Path('structural.json'))
            context.__exit__.assert_called_once()
            self.assertEqual(bench.main(['interpret','--report','original.json','--decoder-report','new-diagnostic/report.json',
                '--out','new-derived.json']),0)
            interpret.assert_called_once_with(Path('original.json'),'new-diagnostic/report.json',Path('new-derived.json'),budget_seconds=600)
            ownership.assert_called_once_with(bench.LOCK)
            quiet.assert_called_once()

class R10PoolLabelTests(unittest.TestCase):
    def fixture(self, pooled):
        opt=options(sessions=3,warmups=1 if pooled else 0,pool_id='unit-pool' if pooled else '',
                    pool_chunk=2 if pooled else 0,campaign_id='unit-label-only')
        pair={'case':'unit','purpose':'measured','status':'valid','samples':{role:{'measurements':{
            'metrics':{'edit_first_expected_ms':100 if role=='reference' else 120},
            'phases':[{'name':'edit','latency_trigger_eligible':False}]}} for role in ('reference','candidate')}}
        return opt,{'status':'complete','pairs':[copy.deepcopy(pair) for _ in range(opt.sessions)]}

    def no_acquisition(self):
        from contextlib import ExitStack
        stack=ExitStack()
        for target,name in ((bench.p7,'sample'),(bench.p7,'execute'),(bench.p7,'NativeChild'),
                            (bench,'cached_spawn'),(bench,'FunctionalOwnership')):
            stack.enter_context(mock.patch.object(target,name,side_effect=AssertionError('label test acquisition: '+name)))
        return stack

    def assert_label(self, summary, pooled):
        decision=summary['unit']['edit_first_expected_ms']['latency_decision']
        self.assertEqual(decision['context'],'descriptive_pooled_latency' if pooled else 'primary_first_expected_potential_regression')
        self.assertIs(decision['material_potential_regression'],None if pooled else True)
        self.assertTrue(decision['descriptive_crossing_requires_confirmation'])
        self.assertFalse(decision['latency_trigger_eligible']);self.assertFalse(decision['accepted_trigger'])
        self.assertIsNone(decision['budget_pass'])

    def test_each_omitted_pool_forwarding_mutant_fails_its_actual_route(self):
        routes=(('run_campaign','test_run_campaign_forwards_pool_label_and_preserves_ordinary_primary'),
                ('interpret_campaign','test_interpret_campaign_forwards_pool_label_and_preserves_ordinary_primary'),
                ('main','test_unvalidated_reduce_uses_saved_pool_options_not_cli_defaults'))
        for name,route in routes:
            source=inspect.getsource(getattr(bench,name));token=', pooled=bool(options.pool_id)'
            self.assertEqual(source.count(token),1)
            namespace={}
            exec(compile(source.replace(token,''),'<pure-R10-omitted-forwarding>','exec'),vars(bench),namespace)
            with self.subTest(route=name),mock.patch.object(bench,name,namespace[name]):
                result=unittest.TestResult();R10PoolLabelTests(route).run(result)
                self.assertFalse(result.wasSuccessful());self.assertTrue(result.failures)
                self.assertFalse(result.errors)
                self.assertTrue(any('pooled' in failure for _case,failure in result.failures))

    def test_run_campaign_forwards_pool_label_and_preserves_ordinary_primary(self):
        for pooled in (True,False):
            with self.subTest(pooled=pooled),tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder,self.no_acquisition():
                root=Path(folder);opt,crossing=self.fixture(pooled)
                context=mock.MagicMock();context.__enter__.return_value=mock.Mock(approval={})
                real=bench.summaries
                # Only scheduling/native/provenance boundaries are mocked. The
                # real completion route invokes the real summary on known rows.
                def summary(report,declared,**kwargs):
                    self.assertEqual(report['status'],'complete');self.assertEqual(declared,opt)
                    return real(crossing,declared,**kwargs)
                with mock.patch.object(bench,'require_seams'), \
                     mock.patch.object(bench.platform,'system',return_value='Linux'), \
                     mock.patch.object(bench,'plan',return_value={'cases':[],'pairs':[]}), \
                     mock.patch.object(bench,'comparison_artifacts',return_value={}), \
                     mock.patch.object(bench,'validate_preflight',return_value={'native_spawn':{},'validated_report_identity':{'sha256':'unit'}}), \
                     mock.patch.object(bench,'validate_pool_spec',return_value=({},{})), \
                     mock.patch.object(bench,'source_snapshot',return_value={}), \
                     mock.patch.object(bench,'host_snapshot',return_value={}), \
                     mock.patch.object(bench,'QuietWindow',return_value=context), \
                     mock.patch.object(bench,'campaign_feasibility',return_value={'estimated_fits':True,'estimated_seconds':1}), \
                     mock.patch.object(bench,'approval_allocation',return_value={}), \
                     mock.patch.object(bench,'observer_controls',return_value=[]), \
                     mock.patch.object(bench,'check_snapshot'), \
                     mock.patch.object(bench,'final_identities',return_value=[]), \
                     mock.patch.object(bench,'retained_authorization'), \
                     mock.patch.object(bench,'summaries',side_effect=summary) as forwarded:
                    result=bench.run_campaign('not-executed','not-executed','unused','unused',root/'out','no-approval-read',opt)
                self.assertEqual(result['status'],'complete',result['errors'])
                forwarded.assert_called_once();self.assertEqual(forwarded.call_args.kwargs,{'pooled':pooled})
                self.assert_label(result['summary'],pooled)

    def test_interpret_campaign_forwards_pool_label_and_preserves_ordinary_primary(self):
        for pooled in (True,False):
            with self.subTest(pooled=pooled),tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder,self.no_acquisition():
                root=Path(folder);opt,original=self.fixture(pooled);original['definition']={'cases':[]}
                identity={'path':str(root/'original'),'sha256':'unit'}
                diagnostic={'kind':'actual measured-session observer replay','valid':True,'errors':[],
                    'campaign_identity':identity,'source':{},'original_campaign_id':opt.campaign_id,
                    'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,'diagnostic_campaign_id':'separate-unit-label-only',
                    'observer_clock':{'injected_test_clock':False,'name':'perf_counter_ns'},
                    'diagnostic_boundary':{'started_monotonic_ns':1,'finished_monotonic_ns':2},
                    'budget_seconds':1,'sessions':[],'end_bindings':{}}
                with mock.patch.object(bench,'campaign_sessions',return_value=(original,identity,opt,[],[])), \
                     mock.patch.object(bench,'json_snapshot',return_value=(diagnostic,{})), \
                     mock.patch.object(bench,'source_snapshot',return_value={}), \
                     mock.patch.object(bench,'recorded_approval'), \
                     mock.patch.object(bench,'campaign_end_check',return_value={}), \
                     mock.patch.object(bench,'check_snapshot'), \
                     mock.patch.object(bench,'summaries',wraps=bench.summaries) as forwarded:
                    result=bench.interpret_campaign('not-read','not-read',root/'derived.json')
                self.assertTrue(result['valid'],result['errors'])
                forwarded.assert_called_once();self.assertEqual(forwarded.call_args.kwargs,{'pooled':pooled})
                self.assert_label(result['summary'],pooled)

    def test_unvalidated_reduce_uses_saved_pool_options_not_cli_defaults(self):
        for pooled in (True,False):
            with self.subTest(pooled=pooled),tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder,self.no_acquisition():
                root=Path(folder);opt,saved=self.fixture(pooled);saved['definition']={'options':opt.__dict__}
                source=root/'unvalidated.json';bench.write_json(source,saved)
                with mock.patch.object(bench,'summaries',wraps=bench.summaries) as forwarded:
                    self.assertEqual(bench.main(['reduce','--report',str(source),'--out',str(root/'view.json')]),0)
                forwarded.assert_called_once();self.assertEqual(forwarded.call_args.kwargs,{'pooled':pooled})
                self.assertEqual(forwarded.call_args.args[1].pool_id,opt.pool_id)
                self.assert_label(json.loads((root/'view.json').read_text())['summary'],pooled)


class R6Tests(unittest.TestCase):
    def test_absolute_work_equal_count_and_matched_delta_gates_five_plus_thirty(self):
        opt=options(sessions=30,warmups=5)
        def population(reference,candidate,left=4,right=4):
            return {i:{'reference':{'observer_ms':reference,'evaluations':left},
                       'candidate':{'observer_ms':candidate,'evaluations':right}} for i in range(30)}
        positive=bench.observer_eligibility(population(.05,.95),opt)
        self.assertTrue(positive['eligible']);self.assertAlmostEqual(positive['role_median_observer_delta_ms'],.9)
        self.assertEqual(positive['role_median_evaluation_count'],{'reference':4,'candidate':4})
        for reference,candidate in ((.05,1.15),(20,20),(1.001,1.001)):
            self.assertFalse(bench.observer_eligibility(population(reference,candidate),opt)['eligible'])
        self.assertFalse(bench.observer_eligibility(population(.05,.95,4,5),opt)['eligible'])
        for override in ({'comparison':'aa-go'},{'comparison':'aa-rust'},
                         {'observer_policy':'resource-attribution'},{'warmups':4},{'sessions':29}):
            with self.subTest(override=override):
                self.assertFalse(bench.observer_eligibility(population(.05,.95),options(**{'sessions':30,'warmups':5,**override}))['eligible'])
        self.assertIn('UNKNOWN',positive['scope'])

    def test_gate_results_stamp_every_measured_role_phase_without_warmup_upgrade(self):
        population={i:{role:{'observer_ms':.5,'evaluations':4} for role in ('reference','candidate')} for i in range(30)}
        positive=bench.observer_eligibility(population,options(sessions=30,warmups=5))
        negative=bench.observer_eligibility(population,options(sessions=30,warmups=5,comparison='aa-go'))
        pairs=[{'case':'case','purpose':purpose,'samples':{role:{'measurements':{'latency_trigger_eligible':'unchanged',
            'phases':[{'name':'edit','latency_trigger_eligible':'unchanged'},{'name':'next','latency_trigger_eligible':'unchanged'}]}}
            for role in ('reference','candidate')}} for purpose in ['warmup']*5+['measured']*30]
        bench.stamp_interpretation(pairs,{'case':{'edit':positive,'next':negative}})
        for pair in pairs:
            for sample in pair['samples'].values():
                if pair['purpose']=='warmup':self.assertEqual(sample['measurements']['latency_trigger_eligible'],'unchanged')
                else:
                    self.assertTrue(sample['measurements']['phases'][0]['latency_trigger_eligible'])
                    self.assertFalse(sample['measurements']['phases'][1]['latency_trigger_eligible'])
                    self.assertFalse(sample['measurements']['latency_trigger_eligible'])

    def test_interpret_full_synthetic_five_plus_thirty_primary_positive_and_aa_attribution_negative(self):
        helper=R5ObserverTests()
        for comparison,policy in (('go-rust','primary'),('aa-go','primary'),('go-rust','resource-attribution')):
            with self.subTest(comparison=comparison,policy=policy),tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
                root=Path(folder)
                path,case,source=helper.campaign(root,sessions=30,warmups=5,equal_counts=True,comparison=comparison,policy=policy)
                original=path.read_bytes()
                with helper.replay_context(case,source):
                    # Gate/orchestration test, not pilot throughput calibration.
                    with mock.patch.object(bench,'pilot_allocation',return_value={'seconds':100}):
                        report=bench.campaign_diagnostics(path,root/'diagnostic',helper.diagnostic_guard(budget_seconds=3600),
                            clock=lambda:0,plan_path=helper.plan(path,root,budget_seconds=3600))
                    self.assertTrue(report['valid'],report['errors']);self.assertEqual(len(report['sessions']),60)
                    # Protocol/gate fixtures only: injected values are not
                    # measured observer controls or native-performance evidence.
                    report['observer_clock']['injected_test_clock']=False
                    for row in report['sessions']:
                        row['phases']['edit']['observer_replay_ms']=[.05 if row['role']=='reference' else .95]*5
                    dpath=root/'diagnostic/report.json';bench.write_report(dpath,report)
                    derived=bench.interpret_campaign(path,dpath,root/'derived.json')
                    self.assertTrue(derived['valid'],derived['errors'])
                    eligible=comparison=='go-rust' and policy=='primary'
                    self.assertEqual(derived['latency_trigger_eligible'],eligible)
                    self.assertEqual(derived['latency_interpretation']['synthetic']['edit']['eligible'],eligible)
                    self.assertEqual(sum(pair['purpose']=='warmup' for pair in derived['pairs']),5)
                    self.assertEqual(sum(pair['purpose']=='measured' for pair in derived['pairs']),30)
                    decision=derived['summary']['synthetic']['edit_first_expected_ms']['latency_decision']
                    self.assertFalse(decision['accepted_trigger']);self.assertIsNone(decision['budget_pass'])
                    self.assertFalse(decision['descriptive_crossing_requires_confirmation'])
                    self.assertEqual(decision['latency_trigger_eligible'],eligible)
                    self.assertEqual(decision['context'],'harness_host_bias_sentinel' if comparison=='aa-go' else
                        'descriptive_resource_attribution_latency' if policy!='primary' else 'primary_first_expected_potential_regression')
                    for pair in derived['pairs']:
                        if pair['purpose']=='measured':
                            for sample in pair['samples'].values():
                                self.assertEqual(sample['measurements']['latency_trigger_eligible'],eligible)
                                self.assertEqual(sample['measurements']['phases'][0]['latency_trigger_eligible'],eligible)
                    if eligible:
                        for row in report['sessions']:
                            if row['role']=='candidate':row['phases']['edit']['observer_replay_ms']=[1.15]*5
                        bench.write_report(dpath,report)
                        failed_gate=bench.interpret_campaign(path,dpath,root/'negative.json')
                        self.assertTrue(failed_gate['valid'],failed_gate['errors'])
                        self.assertFalse(failed_gate['latency_trigger_eligible'])
                        self.assertAlmostEqual(failed_gate['latency_interpretation']['synthetic']['edit']['role_median_observer_delta_ms'],1.1)
                        negative=failed_gate['summary']['synthetic']['edit_first_expected_ms']['latency_decision']
                        self.assertFalse(negative['accepted_trigger']);self.assertIsNone(negative['budget_pass'])
                    if comparison=='aa-go':
                        declared=json.loads(path.read_text())
                        self.assertEqual(declared['artifacts']['reference'],declared['artifacts']['candidate'])
                    self.assertEqual(path.read_bytes(),original)

    def test_clone_preserves_decoder_pending_sgr_buffers_history_and_isolation(self):
        terminal=bench.p7.Terminal(80,30)
        terminal.feed((('history\r\n'*35)+'\x1b[?1049h\x1b[?25l\x1b[2;4H\x1b[1;31mRED\x1b[5 q').encode())
        terminal.feed(b'\x1b]10;?\x07')
        terminal.resize(100,35)
        terminal.feed('日'.encode()[:1])
        clone=bench.clone_terminal(terminal)
        self.assertEqual(clone.decoder.getstate(),terminal.decoder.getstate())
        self.assertEqual(clone.frame(),terminal.frame())
        self.assertEqual(clone.private_modes,terminal.private_modes)
        self.assertEqual(clone.queries,terminal.queries)
        clone.feed('日'.encode()[1:]);terminal.feed('日'.encode()[1:])
        self.assertEqual(clone.frame(),terminal.frame())
        terminal.feed(b'\x1b[38;2;')
        csi=bench.clone_terminal(terminal)
        self.assertEqual(csi.pending,terminal.pending)
        csi.feed(b'1;2;3mCOLOR');terminal.feed(b'1;2;3mCOLOR')
        self.assertEqual(csi.frame(),terminal.frame())
        before=copy.deepcopy(terminal.frame());queries=list(terminal.queries)
        csi.feed(b'\x1b]11;?\x07\x1b[999m\x1b[?1049lEXTRA')
        self.assertEqual(terminal.frame(),before)
        self.assertEqual(terminal.queries,queries)
        self.assertNotEqual(csi.queries,queries)
        self.assertIn('unsupported SGR 999',csi.unsupported)
        self.assertNotIn('unsupported SGR 999',terminal.unsupported)
        self.assertIsNot(csi.main.buffer.default_factory,terminal.main.buffer.default_factory)

    def test_nonground_parser_is_explicit_not_lossy_clone(self):
        terminal=bench.p7.Terminal(80,30);terminal.feed('\x9b31'.encode())
        with self.assertRaisesRegex(ValueError,'nonground pyte parser snapshot'):
            bench.clone_terminal(terminal)

    def test_historical_line_defaults_survive_reverse_mode_clone_and_future_feed(self):
        # Constructed valid pyte state supplied by root; no assertion of a
        # Go/Rust wire-policy mismatch. History defaults predate reverse mode.
        original=bench.p7.Terminal(10,3,'dark')
        original.feed(b'one\r\ntwo\r\nthree\r\nfour\r\n')
        original.main.set_mode(5,private=True)
        self.assertEqual(len(original.main.history.top),2)
        clone=bench.clone_terminal(original)
        self.assertEqual(original.frame(),clone.frame())
        for left,right in zip(original.main.history.top,clone.main.history.top):
            self.assertEqual(left.default,right.default)
            self.assertFalse(right.default.reverse)
        for left,right in zip(original.main.buffer.values(),clone.main.buffer.values()):
            self.assertEqual(left.default,right.default)
        original.resize(12,4);clone.resize(12,4)
        original.feed(b'next\r\n');clone.feed(b'next\r\n')
        self.assertEqual(original.frame(),clone.frame())
        before=copy.deepcopy(original.frame())
        clone.feed(b'isolated\r\nmore\r\n')
        self.assertEqual(original.frame(),before)
        self.assertNotEqual(clone.frame(),before)

    def two_phase(self):
        helper=R5ObserverTests();case,raw=helper.raw()
        second=copy.deepcopy(case['bench']['phases'][0]);second.update(name='next',trigger_action_index=1)
        second['wait']['contains']=['NEXT']
        old=len(raw['stdout']);change=b'\x1b[2;1HNEXT';raw['stdout']+=change
        case['bench']['phases'].append(second);case['actions'].append({'send':'Y','wait':second['wait'],'checkpoint':'next'})
        terminal=bench.p7.Terminal(80,30);terminal.feed(raw['stdout'])
        started=raw['started_monotonic_ns']+600000000;first=started+100000000
        raw['actions'].append({'index':1,'evaluated_monotonic_ns':started,'monotonic_ns':started,'dispatch_monotonic_ns':started+1,'input_hex':'59'})
        raw['frames'].append({'name':'next','frame':terminal.frame(),'action_started_monotonic_ns':started,
            'first_useful_monotonic_ns':first,'first_ever_match_monotonic_ns':first,'stable_since_monotonic_ns':first,
            'ready_monotonic_ns':first+90000000,'last_output_monotonic_ns':first,'bytes_since_action':len(change)})
        raw['decoder_offsets']['actions'].append({'index':1,'byte_offset':old})
        raw['decoder_offsets']['frames'].append({'name':'next','first_offset':len(raw['stdout']),'end_offset':len(raw['stdout']),
            'first_monotonic_ns':first,'action_started_monotonic_ns':started,'baseline_bytes':old,'wait':copy.deepcopy(second['wait']),
            'evaluation_count':1,'recording_error':None,'evaluations':[{'byte_offset':len(raw['stdout']),
                'evaluated_monotonic_ns':first,'tty':{'canonical':False,'echo':False,'signals':False},'qualified':True}]})
        return case,raw

    def test_forward_prefix_only_once_and_fresh_repetitions(self):
        case,raw=self.two_phase();guard=mock.Mock();replay=bench.SessionReplay(raw,guard)
        prefix=raw['stdout'][:raw['decoder_offsets']['actions'][0]['byte_offset']]
        with mock.patch.object(bench,'feed_replay',wraps=bench.feed_replay) as feed:
            for phase in case['bench']['phases']:
                state=replay.snapshot(phase);before=state.frame()
                decoded=bench.decode_phase(raw,phase,guard=guard,clock=lambda:0,prestate=state)
                self.assertEqual(state.frame(),before)
                self.assertEqual(len(decoded['observer_replay_ms']),5)
        self.assertEqual(sum(call.args[1]==prefix for call in feed.call_args_list),1)
        self.assertEqual(replay.prefix_bytes,raw['decoder_offsets']['actions'][-1]['byte_offset'])
        with self.assertRaisesRegex(ValueError,'not forward'):
            replay.snapshot(case['bench']['phases'][0])

    def test_checkpoint_identity_omits_idle_anchor_but_not_equal_latency_wait(self):
        wait={'contains':['READY']};idle={'contains':['READY'],'min_elapsed_ms':300000}
        case={'actions':[{'checkpoint':'start','wait':copy.deepcopy(wait)},
                         {'checkpoint':'anchor','wait':copy.deepcopy(wait)}, {'checkpoint':'idle','wait':idle}],
              'bench':{'phases':[{'name':'start','kind':'startup','wait':wait},
                                 {'name':'anchor','kind':'anchor','wait':wait},{'name':'idle','kind':'idle','wait':idle}]}}
        ready=bench.Readiness(case);terminal=bench.p7.Terminal(80,30);terminal.feed(b'READY')
        ready(terminal,case['actions'][0]['wait'],[],{},1,10)
        with mock.patch.object(ready,'_qualify',return_value=False):
            for now in range(1000):ready(terminal,case['actions'][2]['wait'],[],{},20,now+20)
        ready(terminal,case['actions'][1]['wait'],[],{},2000,2010)
        self.assertEqual(ready.offsets[1]['evaluation_count'],1)
        self.assertEqual(ready.offsets[20]['evaluations'],[])
        self.assertEqual(ready.offsets[2000]['evaluations'],[])
        self.assertEqual(ready.recorded_evaluations,1)

    def test_pilot_allocation_counts_caps_and_remaining_lifetime(self):
        _case,raw=R5ObserverTests().raw();base=bench.session_work(raw,100)
        work=bench.repetition_work(base,5,4)
        pilot=[{'wall_seconds':1,'work':copy.deepcopy(work)},{'wall_seconds':2,'work':copy.deepcopy(work)}]
        remaining=[{'pair_index':1,'role':role,'work':base} for role in ('reference','candidate')]
        allocation=bench.pilot_allocation(pilot,remaining,5,4,2)
        self.assertEqual(allocation['seconds'],128) # 2*(3*2+8)+40+60
        self.assertTrue(all(row['maximum_work_ratio']==1 for row in allocation['remaining_sessions']))
        smaller=copy.deepcopy(remaining);smaller[0]['work']['raw_bytes']=1
        self.assertEqual(bench.pilot_allocation(pilot,smaller,5,4,2)['seconds'],128)
        for change in ('missing','zero','unknown'):
            altered=copy.deepcopy(pilot)
            for row in altered:
                if change=='missing':row['work'].pop('raw_bytes')
                elif change=='zero':row['work']['raw_bytes']=0
                else:row['work']['raw_bytes']=None
            with self.subTest(change=change),self.assertRaises(ValueError):bench.pilot_allocation(altered,remaining,5,4,2)
        guard=mock.Mock(budget=200,approval={'expires_unix':300});guard.wall.return_value=100
        self.assertEqual(bench.approval_allocation(guard,128)['remaining_approval_seconds'],200)
        guard.approval['expires_unix']=220
        with self.assertRaisesRegex(bench.QuietError,'remaining budget/approval'):bench.approval_allocation(guard,128)
        guard.approval['expires_unix']=300
        with self.assertRaises(bench.QuietError):bench.approval_allocation(guard,128,elapsed=100)

    def test_streamed_campaign_has_descriptors_not_resident_raws_and_pref_identity(self):
        helper=R5ObserverTests()
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);path,case,source=helper.campaign(root)
            with helper.replay_context(case,source):
                result=bench.campaign_sessions(path,mock.Mock())
                self.assertTrue(all('raw' not in row and 'work' in row for row in result[3]))
                with mock.patch.object(bench,'offline_preflight_check',return_value={'validated_report_identity':{'sha256':'different bytes'}}), \
                     self.assertRaisesRegex(ValueError,'validated preflight snapshot differs'):
                    bench.campaign_sessions(path,mock.Mock())

    def test_actual_pilot_fit_failure_is_complete_retained_noneligible_population(self):
        helper=R5ObserverTests()
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);path,case,source=helper.campaign(root);original=path.read_bytes()
            with helper.replay_context(case,source):
                plan=helper.plan(path,root)
                structural=json.loads(plan.read_text())
                self.assertIn('unknown',structural['calibrated_fit'])
                self.assertNotIn('estimated_fits',structural)
                with mock.patch.object(bench,'pilot_allocation',return_value={'seconds':10000}):
                    report=bench.campaign_diagnostics(path,root/'incomplete',helper.diagnostic_guard(),clock=lambda:0,plan_path=plan)
                self.assertFalse(report['valid']);self.assertEqual(len(report['pilot']),2)
                self.assertEqual(len(report['sessions']),4)
                self.assertEqual(sum(row['phases']['edit']['status']=='not-attempted' for row in report['sessions']),2)
                self.assertIn('remaining budget/approval',report['errors'][0])
                self.assertGreater(report['actual_diagnostic_wall_seconds'],0)
                self.assertEqual(path.read_bytes(),original)

    def test_precise_preflight_end_failure_kills_missing_recheck_mutant(self):
        helper=R5ObserverTests()
        for mutant in (False,True):
            with self.subTest(mutant=mutant),tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
                root=Path(folder);path,case,source=helper.campaign(root)
                with helper.replay_context(case,source):
                    plan=helper.plan(path,root);decode=bench.decode_phase;end=bench.campaign_end_check
                    def mutate(*args,**kwargs):
                        result=decode(*args,**kwargs);(root/'preflight.json').write_text('{"mutated":true}');return result
                    def recheck(report,identity,records,guard):
                        if mutant:records=[item for item in records if item[1].get('file_identity',{}).get('path')!=str(root/'preflight.json')]
                        return end(report,identity,records,guard)
                    with mock.patch.object(bench,'decode_phase',side_effect=mutate),mock.patch.object(bench,'campaign_end_check',side_effect=recheck):
                        report=bench.campaign_diagnostics(path,root/'diagnostic',helper.diagnostic_guard(),clock=lambda:0,plan_path=plan)
                    self.assertEqual(report['valid'],mutant)
                    if not mutant:self.assertIn('parsed JSON evidence identity/path changed',report['errors'][0])

    def test_offline_cli_modes_require_nonblocking_ownership_before_heavy_work(self):
        from contextlib import ExitStack,redirect_stderr
        import io
        operations=(('interpret','interpret_campaign',['--report','original','--decoder-report','diagnostic']),
                    ('diagnostic-plan','diagnostic_plan',['--report','original']),
                    ('pool-plan','pool_definition',[]),('pool','pool_reports',[]),
                    ('feasibility','campaign_feasibility',[]))
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            for mode,function,args in operations:
                for blocked in (False,True):
                    with self.subTest(mode=mode,blocked=blocked),ExitStack() as stack:
                        ownership=mock.MagicMock()
                        if blocked:ownership.__enter__.side_effect=BlockingIOError('temporary-lock-contention')
                        owned=stack.enter_context(mock.patch.object(bench,'FunctionalOwnership',return_value=ownership))
                        work=stack.enter_context(mock.patch.object(bench,function,return_value={'valid':True}))
                        stack.enter_context(redirect_stderr(io.StringIO()))
                        argv=[mode,'--out',str(Path(folder)/(mode+str(blocked))),*args]
                        if blocked:
                            with self.assertRaises(SystemExit):bench.main(argv)
                            work.assert_not_called()
                        else:
                            self.assertEqual(bench.main(argv),0);work.assert_called_once()
                            ownership.__exit__.assert_called_once()
                        owned.assert_called_once_with(bench.LOCK)

    def test_declared_interpret_budget_is_finite_without_loading_evidence(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            for budget in (0,3601,float('nan'),float('inf'),10**10000):
                with self.subTest(budget=str(type(budget))),self.assertRaisesRegex(ValueError,'finite budget'):
                    bench.interpret_campaign('never-load','never-load',Path(folder)/'unused',budget_seconds=budget)

class R7Tests(unittest.TestCase):
    def test_real_diagnostic_real_pilot_identical_sessions_ratio_one_cap_actual_separate(self):
        helper=R5ObserverTests()
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);path,case,source=helper.campaign(root,equal_counts=True)
            with helper.replay_context(case,source):
                report=bench.campaign_diagnostics(path,root/'diagnostic',helper.diagnostic_guard(),
                    clock=lambda:0,plan_path=helper.plan(path,root))
            self.assertTrue(report['valid'],report['errors'])
            self.assertEqual(len(report['pilot']),2)
            self.assertTrue(report['admissions'])
            for admission in report['admissions']:
                self.assertTrue(all(row['maximum_work_ratio']==1 for row in admission['extrapolation']['remaining_sessions']))
            for row in report['pilot']:
                self.assertLess(row['actual_serialization_bytes'],row['work']['serialization_bytes'])
            for row in report['sessions']:
                self.assertEqual(row['actual_work']['serialization_bytes'],row['serialization_cap_bytes'])
                self.assertLessEqual(row['actual_serialization_bytes'],row['serialization_cap_bytes'])

    def test_injected_wall_formula_fifty_eight_remaining_reserve704(self):
        _case,raw=R5ObserverTests().raw();base=bench.session_work(raw,100)
        work=bench.repetition_work(base,5,60)
        remaining=[{'pair_index':1+index//2,'role':'reference' if index%2==0 else 'candidate','work':base} for index in range(58)]
        for wall in (.1,1,2,10):
            pilot=[{'wall_seconds':wall,'work':work,'actual_serialization_bytes':100} for _ in range(2)]
            result=bench.pilot_allocation(pilot,remaining,5,60,30)
            self.assertAlmostEqual(result['seconds'],174*wall+704)
            self.assertTrue(all(row['maximum_work_ratio']==1 for row in result['remaining_sessions']))

    def test_actual_serialization_cap_excess_retains_bytes_and_failure(self):
        helper=R5ObserverTests()
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);path,case,source=helper.campaign(root,equal_counts=True)
            original=bench.repetition_work
            def tiny(*args):return {**original(*args),'serialization_bytes':1}
            with helper.replay_context(case,source):
                plan=helper.plan(path,root)
                with mock.patch.object(bench,'repetition_work',side_effect=tiny):
                    report=bench.campaign_diagnostics(path,root/'failure',helper.diagnostic_guard(),clock=lambda:0,plan_path=plan)
            self.assertFalse(report['valid']);self.assertIn('serialization exceeds',report['errors'][0])
            self.assertGreater(report['sessions'][0]['actual_serialization_bytes'],1)
            self.assertEqual(report['sessions'][0]['serialization_cap_bytes'],1)

    def test_descriptive_crossing_inconclusive_eligible_and_unknown_no_zero(self):
        metric='edit_first_expected_ms'
        def report(delta,eligible=False):
            pairs=[]
            for index in range(30):
                pairs.append({'case':'unit','purpose':'measured','status':'valid','samples':{
                    role:{'measurements':{'metrics':{metric:100 if role=='reference' else 100+delta},
                        'phases':[{'name':'edit','latency_trigger_eligible':eligible}]}} for role in ('reference','candidate')}})
            return {'status':'complete','pairs':pairs}
        opt=options(sessions=30,warmups=5)
        for delta,eligible,crossing in ((20,False,True),(5,False,False),(20,True,True),(5,True,False),(10,False,False)):
            result=bench.summaries(report(delta,eligible),opt)['unit'][metric]['latency_decision']
            self.assertEqual(result['descriptive_crossing_requires_confirmation'],crossing)
            self.assertEqual(result['latency_trigger_eligible'],eligible)
            self.assertIsNone(result['budget_pass']);self.assertFalse(result['accepted_trigger'])
            if not eligible:self.assertIn('BOTH',result['decision_scope'])
            if crossing:
                self.assertIn('fresh-seed',result['required_action']);self.assertIn('cannot dismiss',result['required_action'])
        unknown=report(20);unknown['pairs'][0]['samples']['reference']['measurements']['metrics'][metric]=None
        with self.assertRaisesRegex(ValueError,'no zero'):
            bench.summaries(unknown,opt)
        rows=[{'reference':{metric:0},'candidate':{metric:20}}]
        self.assertIsNone(bench.descriptive_latency_decision(rows,metric,False)['descriptive_crossing_requires_confirmation'])
        self.assertIsNone(bench.summaries({'status':'failed','pairs':[]},opt))

    def test_latency_no_false_pass_and_independent_threshold_arms_kill_mutants(self):
        metric='edit_first_expected_ms'
        def check(function):
            for reference,delta,eligible,crossing in ((100,5,True,False),(400,20,True,False),
                    (50,8,True,False),(100,20,True,True),(100,20,False,True)):
                rows=[{'reference':{metric:reference},'candidate':{metric:reference+delta}}]*30
                decision=function(rows,metric,eligible)
                self.assertEqual(decision['descriptive_crossing_requires_confirmation'],crossing)
                self.assertIsNone(decision['budget_pass']);self.assertFalse(decision['accepted_trigger'])
        check(bench.descriptive_latency_decision)
        source=inspect.getsource(bench.descriptive_latency_decision)
        for before,after in (('delta > 10 and relative > .10','delta > 10 or relative > .10'),
                ("'budget_pass': None","'budget_pass': True if eligible and not crossing else None")):
            self.assertIn(before,source)
            namespace=dict(vars(bench));exec(compile(source.replace(before,after),'<pure-latency-mutant>','exec'),namespace)
            with self.subTest(mutant=after),self.assertRaises(AssertionError):check(namespace['descriptive_latency_decision'])

    def test_summary_crossing_contexts_retain_flag_without_false_candidate_labels(self):
        cases=(('aa-go','primary',False,'first_expected_ms','harness_host_bias_sentinel'),
               ('aa-rust','primary',False,'first_expected_ms','harness_host_bias_sentinel'),
               ('go-rust','resource-attribution',False,'first_expected_ms','descriptive_resource_attribution_latency'),
               ('go-rust','primary',True,'first_expected_ms','descriptive_pooled_latency'),
               ('go-rust','primary',False,'ready_stable_ms','descriptive_stable_ready_latency'),
               ('go-rust','primary',False,'first_expected_ms','primary_first_expected_potential_regression'))
        for comparison,policy,pooled,suffix,context in cases:
            metric='edit_'+suffix
            pair={'purpose':'measured','status':'valid','case':'unit','samples':{role:{'measurements':{
                'metrics':{metric:100 if role=='reference' else 120},
                'phases':[{'name':'edit','latency_trigger_eligible':True}]}} for role in ('reference','candidate')}}
            with self.subTest(context=context):
                decision=bench.summaries({'status':'complete','pairs':[pair]*30},
                    options(sessions=30,comparison=comparison,observer_policy=policy),pooled=pooled)['unit'][metric]['latency_decision']
                self.assertEqual(decision['context'],context)
                self.assertTrue(decision['descriptive_crossing_requires_confirmation'])
                self.assertTrue(decision['observer_screen_eligible'])
                self.assertEqual(decision['latency_trigger_eligible'],context=='primary_first_expected_potential_regression')
                self.assertEqual(decision['material_potential_regression'],True if context=='primary_first_expected_potential_regression' else None)
                self.assertFalse(decision['accepted_trigger']);self.assertIsNone(decision['budget_pass'])
                if comparison.startswith('aa-'):self.assertIn('harness/host-bias sentinel',decision['required_action'])

    def test_role_session_work_max_p95_descriptive_not_tail_bound(self):
        population={index:{role:{'observer_ms':.5 if index<29 else 50,'evaluations':4}
            for role in ('reference','candidate')} for index in range(30)}
        result=bench.observer_eligibility(population,options(sessions=30,warmups=5))
        self.assertTrue(result['eligible'])
        for role in ('reference','candidate'):
            self.assertEqual(result['role_session_observer_work_distribution_ms'][role]['max'],50)
            self.assertIn('p95',result['role_session_observer_work_distribution_ms'][role])

    def test_exact_nine_chunk_spec_and_zero_measured_warmup_only(self):
        expected=((4,0),(1,3),(0,4),(0,4),(0,4),(0,4),(0,4),(0,4),(0,3))
        self.assertEqual(bench.POOL_COUNTS,expected)
        for index,counts in enumerate(expected,1):
            options(pool_id='nine',pool_chunk=index,warmups=counts[0],sessions=counts[1]).validate()
        with self.assertRaises(ValueError):options(sessions=0).validate()
        spec={'chunks':[{'index':i+1,'seed':50000+1009*i,'warmups':c[0],'sessions':c[1]} for i,c in enumerate(expected)],
              'analysis_seed':150000,'required_totals':{'warmups':5,'measured':30}}
        bench.validate_pool_chunks(spec)
        for mutation in ('missing','duplicate','seven','seed','counts','index'):
            altered=copy.deepcopy(spec)
            if mutation=='missing':altered['chunks'].pop()
            elif mutation=='duplicate':altered['chunks'][-1]=copy.deepcopy(altered['chunks'][0])
            elif mutation=='seven':altered['chunks']=altered['chunks'][:7]
            elif mutation=='seed':altered['chunks'][5]['seed']+=1
            elif mutation=='counts':altered['chunks'][1]['sessions']=4
            else:altered['chunks'][1]['index']=False
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):bench.validate_pool_chunks(altered)
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder)
            mocked=({'definition':{'cases':[]}}, {'path':str(root/'original')},options(),[],[])
            with mock.patch.object(bench,'campaign_sessions',return_value=mocked):
                with self.assertRaisesRegex(ValueError,'NOT APPLICABLE'):
                    bench.diagnostic_plan('none',root/'plan')
                result=bench.campaign_diagnostics('none',root/'diagnostic',R5ObserverTests().diagnostic_guard(),clock=lambda:0)
                self.assertFalse(result['valid']);self.assertIn('NOT APPLICABLE',result['errors'][0])

class PoolTests(unittest.TestCase):
    def fixture(self, root, *, floor_sessions=1):
        case, original = synthetic()
        case['bench']['comparison'] = 'go-rust'
        case['bench']['phases'][-1]['long_idle'] = True
        helper_file, sidecar = root/'helper', root/'helper.json'
        helper_file.write_bytes(b'unit helper bytes never executed');sidecar.write_text('{}')
        helper = {'path':str(helper_file),'sha256':bench.sha_file(helper_file),
            'provenance_path':str(sidecar),'provenance_sha256':bench.sha_file(sidecar),
            'source_sha256':bench.sha_file(bench.ROOT/'harness/phase7_spawn.c')}
        artifacts={}
        for role,language in (('reference','go'),('candidate','rust')):
            binary=root/role;binary.write_bytes(('not executed '+role).encode());binary.chmod(0o700)
            meta=root/(role+'.json')
            bench.write_json(meta, {'binary_sha256':bench.sha_file(binary),'optimized':True,'language':language,
                'profile':'release','source_commit':bench.PROVENANCE_COMMIT if language=='go' else 'a'*40,
                'source_tree_sha256':'b'*64})
            artifacts[role]=bench.artifact(binary,meta,role)
        source={'test':'synthetic-pool-source'}
        pref_path=root/'pref.json'
        pref={'valid':True,'source':source,'case_hashes':{case['name']:bench.p7.digest(case)},
              'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,
              'artifacts':{role:bench.public_value(value['identity']) for role,value in artifacts.items()},'native_spawn':helper,
              'cases':[{'case':case['name'],'samples':{role:{'scheduling_wall_ms':2000} for role in ('reference','candidate')}}]}
        bench.write_json(pref_path,pref)
        opt=options(pool_id='owned-pool',preflight_report=str(pref_path),seed=30001,floor_sessions=floor_sessions)
        with mock.patch.object(bench,'source_snapshot',return_value=source),mock.patch.object(bench,'campaign_cases',return_value=[case]),mock.patch.object(bench,'validate_preflight',return_value=pref):
            spec=bench.pool_definition(opt)
        spec_path=root/'spec.json';bench.write_json(spec_path,spec)
        spec_id=bench.json_snapshot(spec_path)[1]
        def archive(path, raw):
            path.mkdir(parents=True)
            bench.archive_execute(raw,path)
            return bench.capture_file_hashes(path)
        paths=[]
        nonce=0
        for chunk in spec['chunks']:
            opts=options(pool_id='owned-pool',pool_chunk=chunk['index'],pool_spec=str(spec_path),seed=chunk['seed'],
                         warmups=chunk['warmups'],sessions=chunk['sessions'],max_sessions=spec['chunk_max_sessions'][chunk['index']-1],
                         preflight_report=str(pref_path),campaign_id=f'unit-chunk-{chunk["index"]}',floor_sessions=floor_sessions)
            approval={'approved':True,'scope':'phase7','campaign_id':opts.campaign_id,'issued_unix':0,'expires_unix':3600,
                      'companions':{'phase6':{'agreed':True,'campaign_id':opts.campaign_id,'by':'unit','reference':'unit-only','at_unix':0}}}
            folder=root/f'chunk{chunk["index"]}';folder.mkdir()
            definition={'options':opts.__dict__,'cases':[case],'case_hashes':{'synthetic':{'definition_sha256':bench.p7.digest(case)}},
                        'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,
                        'observer_policy':'primary','preflight_report_identity':spec['preflight_identity'],
                        'pool_spec_identity':bench.public_value(spec_id),'native_spawn':helper}
            pairs=[]
            for number,scheduled in enumerate(bench.schedule([case],opts)):
                pair={**scheduled,'status':'valid','samples':{}}
                for role in ('reference','candidate'):
                    nonce+=1;raw=copy.deepcopy(original)
                    delta=nonce*2000000000
                    for key in ('started_monotonic_ns','reaped_monotonic_ns','exited_monotonic_ns'):raw[key]+=delta
                    for captured_frame in raw['frames']:
                        for key in ('first_useful_monotonic_ns','first_ever_match_monotonic_ns','stable_since_monotonic_ns','ready_monotonic_ns','last_output_monotonic_ns'):captured_frame[key]+=delta
                    for event in raw['actions']:
                        for key in ('monotonic_ns','evaluated_monotonic_ns','dispatch_monotonic_ns'):event[key]+=delta
                    for sample in raw['metrics']['proc_samples']:sample['monotonic_ns']+=delta
                    raw.update(case=case,binary=artifacts[role]['identity'],capture_command=[artifacts[role]['identity']['path'],*case['argv']],observer_policy='primary',
                               stdout=b'',stderr=b'',post_screen_stdout=b'')
                    raw['metrics']['native_spawn']=helper
                    relative=f'sessions/{number}/{role}'
                    pair['samples'][role]={'artifact':relative,'valid':True,**archive(folder/relative,raw),
                        'capture_boundary':{'started_monotonic_ns':raw['started_monotonic_ns']-1,
                            'finished_monotonic_ns':raw['reaped_monotonic_ns']+1,
                            'started_unix':100+raw['started_monotonic_ns']/1e9,
                            'finished_unix':100+raw['reaped_monotonic_ns']/1e9,'approval':approval}}
                pairs.append(pair)
            controls=[]
            for repetition,kind in enumerate(('tiny-frame','recorded-frame')*floor_sessions):
                modes={}
                for mode in ('primary','minimal'):
                    relative=f'unit-{mode}'+(f'-repeat{repetition}' if repetition>=2 else '')
                    raw={'stdout':b'FRAME 0000','stderr':b''.join(f'{i} {i+1}\n'.encode() for i in range(opts.floor_frames)),'post_screen_stdout':b'', 'failure':None, 'termination':{'exit':0},
                         'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,
                         'termios_restored':True,'frames':[{'frame':frame(f'FRAME {i:04d}'),'first_useful_monotonic_ns':i+2} for i in range(opts.floor_frames)],'metrics':{'native_spawn':helper}}
                    modes[mode]={'valid':True,'evidence':relative,'retained_capture':archive(folder/'observer-controls'/kind/relative/'capture',raw)}
                controls.append({'kind':kind,'status':'valid','modes':modes})
            controlpath='observer-controls/controls-progress.json';bench.write_json(folder/controlpath,controls)
            report={'status':'complete','errors':[],'source':source,'source_after':source,'artifacts':artifacts,
                'quiet_window':approval,'quiet_window_end':approval,
                'campaign_boundary':{'started_monotonic_ns':min(row['capture_boundary']['started_monotonic_ns'] for pair in pairs for row in pair['samples'].values())-1,
                    'finished_monotonic_ns':max(row['capture_boundary']['finished_monotonic_ns'] for pair in pairs for row in pair['samples'].values())+1,
                    'started_unix':min(row['capture_boundary']['started_unix'] for pair in pairs for row in pair['samples'].values())-.001,
                    'finished_unix':max(row['capture_boundary']['finished_unix'] for pair in pairs for row in pair['samples'].values())+.001},
                'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,
                'artifacts_after':{r:a['identity'] for r,a in artifacts.items()},'native_spawn_after':helper,
                'metadata_after':{r:{'path':a['provenance_path'],'sha256':a['provenance_file_sha256'],'size_bytes':a['provenance_size_bytes']} for r,a in artifacts.items()},
                'definition':definition,'pairs':pairs,'observer_controls':controls,'observer_controls_evidence':controlpath}
            path=folder/'report.json';bench.write_json(path,report);paths.append(path)
        return spec_path,paths,source

    def reduce(self,spec,paths,source,out,**kwargs):
        # Shared validation is not exercised with synthetic non-CLI captures.
        # Own identity/schedule/raw hashes/observation/extraction code is real.
        pref=json.loads(Path(json.loads(spec.read_text())['preflight_identity']['path']).read_text())
        with mock.patch.object(bench,'source_snapshot',return_value=source), \
             mock.patch.object(bench,'campaign_cases',return_value=[json.loads(spec.read_text())['case']]), \
             mock.patch.object(bench,'validate_preflight',return_value=pref), \
             mock.patch.object(bench.p7,'validate',return_value=[]), \
             mock.patch.object(bench.p7,'compare',return_value=[]):
            return bench.pool_reports(spec,paths,out,**kwargs)

    def test_pool_single_fresh_full_feasibility_all_nine_chunks_and_live_last_chunk(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);spec,paths,source=self.fixture(root,floor_sessions=3)
            design=json.loads(spec.read_text())
            self.assertEqual(design['chunk_max_sessions'],[20]*8+[18])
            with mock.patch.object(bench,'require_pool_feasibility',wraps=bench.require_pool_feasibility) as fresh, \
                 mock.patch.object(bench,'offline_preflight_check',wraps=bench.offline_preflight_check) as preflight:
                result=self.reduce(spec,paths,source,root/'pool.json',budget_seconds=3600)
            self.assertTrue(result['valid'],result['errors'])
            self.assertEqual(fresh.call_count,1)
            # One FULL profile and one selected long-case validation, not9x.
            self.assertEqual(preflight.call_count,2)
            self.assertEqual(result['budget_seconds'],3600);self.assertGreater(result['elapsed_seconds'],0)
            self.assertEqual(len(result['chunks']),9)
            last=json.loads(paths[-1].read_text());declared=last['definition']['options']
            opts=bench.Options(**{**declared,'themes':tuple(declared['themes']),'sizes':tuple(tuple(x) for x in declared['sizes'])})
            self.assertEqual(opts.max_sessions,18)
            pref=json.loads(Path(design['preflight_identity']['path']).read_text())
            with mock.patch.object(bench,'source_snapshot',return_value=source), \
                 mock.patch.object(bench,'campaign_cases',return_value=[design['case']]), \
                 mock.patch.object(bench,'validate_preflight',return_value=pref), \
                 mock.patch.object(bench,'require_pool_feasibility',wraps=bench.require_pool_feasibility) as fresh:
                for _ in range(2):bench.validate_pool_spec(spec,last['definition'],opts)
                self.assertEqual(fresh.call_count,2)  # No live-admission cache/bypass.
                self.assertEqual(bench.require_pool_feasibility(opts),design['provisional_chunk_feasibility'])
                for path in paths:
                    row=json.loads(path.read_text());values=row['definition']['options']
                    varied=bench.Options(**{**values,'themes':tuple(values['themes']),'sizes':tuple(tuple(x) for x in values['sizes'])})
                    self.assertEqual(bench.require_pool_feasibility(varied),design['provisional_chunk_feasibility'])
                with self.assertRaisesRegex(ValueError,'max_sessions'):
                    bench.validate_pool_spec(spec,last['definition'],bench.Options(**{**opts.__dict__,'max_sessions':20}))
                for mutation in ('cap-table','count','control','profile'):
                    changed=copy.deepcopy(design)
                    if mutation=='cap-table':changed['chunk_max_sessions'][-1]=20
                    elif mutation=='count':changed['chunks'][-1]['sessions']=4
                    elif mutation=='control':changed['control_config']['floor_sessions']=2
                    else:changed['common_options']['themes']=['light']
                    altered=root/(mutation+'-spec.json');bench.write_json(altered,changed)
                    with self.subTest(spec_mutation=mutation),self.assertRaises(ValueError):
                        bench.validate_pool_spec(altered,last['definition'],opts)
            self.assertEqual(opts.max_sessions,18)  # Actual final run cap unchanged.

    def test_pool_late_options_mutation_rejected_without_second_expensive_validation(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);spec,paths,source=self.fixture(root)
            original=json.loads(paths[-1].read_text())
            allocation_function=bench.require_pool_feasibility
            for key,value in (('budget_seconds',599),('bootstrap_draws',41),('network_delay_ms',151),
                    ('max_sessions',12),('preflight_report','alias-pref.json')):
                bench.write_json(paths[-1],original)
                def mutate_after_first_feasibility(opts):
                    allocation=allocation_function(opts)
                    report=copy.deepcopy(original);report['definition']['options'][key]=value
                    bench.write_json(paths[-1],report)
                    return allocation
                with mock.patch.object(bench,'require_pool_feasibility',side_effect=mutate_after_first_feasibility) as fresh:
                    result=self.reduce(spec,paths,source,root/(key+'.json'))
                with self.subTest(option=key):
                    self.assertFalse(result['valid']);self.assertIsNone(result['summary'])
                    self.assertEqual(fresh.call_count,1)
                    self.assertTrue(result['errors']);self.assertGreater(result['elapsed_seconds'],0)
                    self.assertTrue(paths[-1].is_file())

    def test_pool_budget_validated_before_any_evidence_read_and_failure_retained(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder)
            for index,budget in enumerate((float('nan'),float('inf'),float('-inf'),0,-1,3601,True,False)):
                output=root/f'bad{index}.json'
                with mock.patch.object(bench,'json_snapshot',side_effect=AssertionError('must reject before read')):
                    result=bench.pool_reports('never-read',[],output,budget_seconds=budget)
                self.assertFalse(result['valid']);self.assertIsNone(result['summary'])
                self.assertIn('finite non-bool',result['errors'][0]);self.assertIsNotNone(result['elapsed_seconds'])
                self.assertTrue(output.is_file());self.assertIn('budget_seconds',json.loads(output.read_text()))
            output=root/'invalid-json.json';bad=root/'bad.json';bad.write_text('{')
            result=bench.pool_reports(bad,[],output,budget_seconds=3600)
            self.assertFalse(result['valid']);self.assertEqual(result['budget_seconds'],3600)
            self.assertTrue(result['errors']);self.assertTrue(output.is_file())
            # Formatting an overflow-sized API budget must not escape failure
            # retention (Python's decimal-int safety limit may reject repr).
            with mock.patch.object(bench,'json_snapshot',side_effect=AssertionError('must reject before read')):
                result=bench.pool_reports('never-read',[],root/'overflow-budget.json',budget_seconds=10**10000)
            self.assertFalse(result['valid']);self.assertTrue(result['errors'])
            self.assertEqual(result['declared_budget_type'],'int');self.assertIsNotNone(result['elapsed_seconds'])
            self.assertTrue((root/'overflow-budget.json').is_file())
            with mock.patch.object(bench,'FunctionalGuard') as guard, \
                 mock.patch.object(bench,'json_snapshot',side_effect=lambda *_a,**_k: self.assertTrue(guard.called) or (_ for _ in ()).throw(ValueError('first read'))):
                bench.pool_reports(bad,[],root/'guard-before-read.json',budget_seconds=1)
                guard.assert_called_once_with(1)

    def test_pool_cli_forwards_explicit_budget_without_real_ownership(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder, \
             mock.patch.object(bench,'FunctionalOwnership'), \
             mock.patch.object(bench,'pool_reports',return_value={'valid':True}) as reducer:
            output=Path(folder)/'pool.json'
            self.assertEqual(bench.main(['pool','--pool-spec','spec.json','--chunk-report','chunk.json',
                '--budget-seconds','3600','--out',str(output)]),0)
            reducer.assert_called_once_with('spec.json',['chunk.json'],output,budget_seconds=3600)

    def test_pool_expiry_after_single_feasibility_retains_failure_and_original_inputs(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);spec,paths,source=self.fixture(root)
            originals={path:path.read_bytes() for path in [spec,*paths]}
            now=[0]
            original=bench.require_pool_feasibility
            def expire_after_allocation(opts):
                allocation=original(opts);now[0]=2
                return allocation
            with mock.patch.object(bench.time,'monotonic',side_effect=lambda:now[0]), \
                 mock.patch.object(bench,'require_pool_feasibility',side_effect=expire_after_allocation) as fresh:
                result=self.reduce(spec,paths,source,root/'expired.json',budget_seconds=1)
            self.assertFalse(result['valid']);self.assertIsNone(result['summary'])
            self.assertEqual(fresh.call_count,1)
            self.assertEqual(result['budget_seconds'],1);self.assertEqual(result['elapsed_seconds'],2)
            self.assertIsNotNone(result['spec_identity']);self.assertEqual(len(result['chunks']),1)
            self.assertIn('budget exhausted',result['errors'][0])
            self.assertEqual(json.loads((root/'expired.json').read_text())['errors'],result['errors'])
            for path,encoded in originals.items():self.assertEqual(path.read_bytes(),encoded)

    def test_complete_predeclared_seeds_pool_exact_five_plus_thirty(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);spec,paths,source=self.fixture(root)
            design=json.loads(spec.read_text())
            self.assertEqual(len({row['seed'] for row in design['chunks']}),9)
            self.assertEqual([(row['warmups'],row['sessions']) for row in design['chunks']],list(bench.POOL_COUNTS))
            first=json.loads(paths[0].read_text());second=json.loads(paths[1].read_text())
            self.assertEqual([row['purpose'] for row in first['pairs']],['warmup']*4)
            self.assertEqual(sum(row['purpose']=='warmup' for row in second['pairs']),1)
            self.assertEqual(sum(row['purpose']=='measured' for row in second['pairs']),3)
            with mock.patch.object(bench,'summaries',wraps=bench.summaries) as summarized:
                result=self.reduce(spec,paths,source,root/'pool.json')
            summarized.assert_called_once()
            self.assertTrue(summarized.call_args.kwargs['pooled'])
            self.assertEqual(summarized.call_args.args[1].bootstrap_draws,2000)
            self.assertEqual(json.loads(paths[0].read_text())['definition']['options']['bootstrap_draws'],40)
            self.assertTrue(result['valid'],result['errors'])
            self.assertEqual(len(result['pairs']),35)
            self.assertEqual(sum(p['purpose']=='measured' for p in result['pairs']),30)
            self.assertIn('synthetic',result['summary'])
            self.assertEqual(len(result['chunks']),9)
            self.assertTrue(all('controls_identity' in row for row in result['chunks']))
            self.assertFalse(result['latency_trigger_eligible'])

    def test_pool_rejects_stale_missing_reused_mismatched_authorizations(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);spec,paths,source=self.fixture(root)
            original=json.loads(paths[-1].read_text())
            for mutation in ('missing','stale','companion','reused','scope','role-scope','native-scope'):
                report=copy.deepcopy(original)
                if mutation=='missing':report.pop('quiet_window')
                elif mutation=='stale':report['quiet_window']['expires_unix']=report['campaign_boundary']['started_unix']
                elif mutation=='companion':report['quiet_window']['companions']['phase6']['campaign_id']='wrong'
                elif mutation=='reused':
                    identifier=json.loads(paths[0].read_text())['definition']['options']['campaign_id']
                    report['definition']['options']['campaign_id']=identifier
                    for approval in [report['quiet_window'],report['quiet_window_end'],*[row['capture_boundary']['approval'] for pair in report['pairs'] for row in pair['samples'].values()]]:
                        approval['campaign_id']=identifier;approval['companions']['phase6']['campaign_id']=identifier
                elif mutation=='scope':report['campaign_boundary']['finished_monotonic_ns']+=700000000000
                elif mutation=='role-scope':report['pairs'][0]['samples']['reference']['capture_boundary']['started_monotonic_ns']=0
                else:report['pairs'][0]['samples']['reference']['capture_boundary']['started_monotonic_ns']+=100
                bench.write_json(paths[-1],report)
                result=self.reduce(spec,paths,source,root/(mutation+'.json'))
                with self.subTest(mutation=mutation):
                    self.assertFalse(result['valid']);self.assertIsNone(result['summary']);self.assertTrue(result['errors'])

    def test_pool_spec_needs_full_preflight_fit_with_unchanged_reserves(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder)
            opt=options(include_long_idle=True,long_idle_seconds=300,floor_sessions=3,budget_seconds=3600,
                case_filters=('sql-scroll-20000-long-idle-*',),pool_id='provisional',pool_chunk=1,warmups=4,sessions=0,
                preflight_report=str(root/'pref.json'))
            selected=bench.campaign_cases(bench.Options(**{**opt.__dict__,'pool_id':'','pool_chunk':0,'sessions':1}))
            self.assertEqual(len(selected),1)
            full=bench.campaign_cases(bench.Options(**{**opt.__dict__,'case_filters':(),'pool_id':'','pool_chunk':0,'sessions':1}))
            self.assertEqual(len(full),13)
            pref={'valid':True,'source':{},'artifacts':{},'native_spawn':{},
                  'case_hashes':{case['name']:bench.p7.digest(case) for case in full},
                  'cases':[{'case':case['name'],'samples':{role:{'scheduling_wall_ms':355500} for role in ('reference','candidate')}} for case in full]}
            bench.write_json(root/'pref.json',pref)
            admitted=[]
            def validate(_path,definition,report):
                admitted.append(len(definition['cases']))
                if len(report['cases'])!=13:raise ValueError('full matching preflight required')
                return report
            with mock.patch.object(bench,'source_snapshot',return_value={}),mock.patch.object(bench,'offline_preflight_check',side_effect=validate):
                spec=bench.pool_definition(opt)
                allocation=spec['provisional_chunk_feasibility']
                self.assertEqual(allocation['estimated_seconds'],3600)
                self.assertEqual(allocation['floor_reserve_seconds_each'],30)
                self.assertEqual(allocation['cleanup_margin_seconds_per_role'],8)
                self.assertEqual(allocation['approval_launch_allowance_seconds'],60)
                self.assertIn(13,admitted)
                for row in pref['cases']:
                    for sample in row['samples'].values():sample['scheduling_wall_ms']=355600
                bench.write_json(root/'pref.json',pref)
                with self.assertRaisesRegex(ValueError,'does not fit'):
                    bench.pool_definition(opt)
                pref['cases'].pop();bench.write_json(root/'pref.json',pref)
                with self.assertRaisesRegex(ValueError,'full matching'):
                    bench.pool_definition(opt)

    def test_pool_rehashes_control_streams_at_end_after_initial_validation(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root=Path(folder);spec,paths,source=self.fixture(root)
            stream=paths[0].parent/'observer-controls/tiny-frame/unit-primary/capture/stdout.bin'
            mutated=False
            def compare(_reference,_candidate):
                nonlocal mutated
                if not mutated:
                    stream.write_bytes(b'changed after initial control validation')
                    mutated=True
                return []
            pref=json.loads(Path(json.loads(spec.read_text())['preflight_identity']['path']).read_text())
            with mock.patch.object(bench,'source_snapshot',return_value=source), \
                 mock.patch.object(bench,'campaign_cases',return_value=[json.loads(spec.read_text())['case']]), \
                 mock.patch.object(bench,'validate_preflight',return_value=pref), \
                 mock.patch.object(bench.p7,'validate',return_value=[]), \
                 mock.patch.object(bench.p7,'compare',side_effect=compare):
                result=bench.pool_reports(spec,paths,root/'pool.json')
            self.assertTrue(mutated)
            self.assertFalse(result['valid'])
            self.assertIsNone(result['summary'])
            self.assertIn('raw evidence changed at end',result['errors'][0])

    def test_pool_rejects_omitted_duplicate_seed_identity_raw_and_control_failures(self):
        for change in ('missing','duplicate-chunk','seed','artifact','raw-reuse','omitted-pair','control','source','seven'):
            with self.subTest(change=change),tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
                root=Path(folder);spec,paths,source=self.fixture(root)
                selected=list(paths)
                if change=='missing':selected.pop()
                elif change=='duplicate-chunk':selected[-1]=selected[0]
                elif change=='seven':selected=selected[:7]
                else:
                    report=json.loads(paths[-1].read_text())
                    if change=='seed':report['definition']['options']['seed']=7
                    elif change=='artifact':report['artifacts']['reference']['build_provenance']['source_commit']='c'*40
                    elif change=='raw-reuse':
                        first=json.loads(paths[0].read_text())['pairs'][0]['samples']['reference']
                        report['pairs'][0]['samples']['reference']['session_sha256']=first['session_sha256']
                    elif change=='omitted-pair':report['pairs'].pop()
                    elif change=='control':report['observer_controls']=[]
                    else:report['source_after']={'changed':True}
                    bench.write_json(paths[-1],report)
                result=self.reduce(spec,selected,source,root/'pool.json')
                self.assertFalse(result['valid'])
                self.assertIsNone(result['summary'])
                self.assertTrue(result['errors'])
                self.assertTrue(all(path.is_file() for path in paths))


class QuietTests(unittest.TestCase):
    def test_reload_parser_resource_exceptions_become_capture_failure_strings(self):
        for failure in (RecursionError('nested JSON'), MemoryError('JSON allocation')):
            with self.subTest(failure=type(failure).__name__), self.guard() as guard:
                self.mono += .3
                with mock.patch.object(guard, '_approval', side_effect=failure):
                    message = guard.continue_running()
                self.assertIn('cannot verify', message)
                self.assertIn(str(failure), message)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=bench.p7.TMP)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.approval, self.lock = self.root / 'approval.json', self.root / 'lock'
        self.now, self.mono = 10, 20
        self.write_approval()

    def write_approval(self, **changes):
        bench.write_json(self.approval, {'approved': True, 'scope': 'phase7', 'issued_unix': 0, 'expires_unix': 100,
            'campaign_id': 'unit-test', 'companions': {'phase6': {'agreed': True, 'campaign_id': 'unit-test', 'by': 'parent', 'at_unix': 0, 'reference': 'test-agreement'}}, **changes})

    def guard(self, budget=30):
        return bench.QuietWindow(self.approval, budget, lock_path=self.lock,
                                 wall_clock=lambda: self.now, monotonic=lambda: self.mono)

    def test_json_types_campaign_companion_and_bounded_window(self):
        for value in ([], None, True, 1, 'approval'):
            with self.subTest(value=value):
                self.approval.write_text(json.dumps(value))
                with self.assertRaises(bench.QuietError):
                    self.guard().__enter__()
        for changes in ({'expires_unix': 4000}, {'issued_unix': True}, {'companions': {}},
                        {'campaign_id': ''}, {'issued_unix': 11}):
            with self.subTest(changes=changes):
                self.write_approval(**changes)
                with self.assertRaises(bench.QuietError):
                    self.guard().__enter__()
        self.write_approval()
        with self.assertRaisesRegex(bench.QuietError, 'binding'):
            with bench.QuietWindow(self.approval, 30, lock_path=self.lock,
                    wall_clock=lambda: self.now, monotonic=lambda: self.mono, campaign_id='other'):
                pass
        with self.guard() as guard:
            self.approval.write_text('[]')
            self.mono += .3
            self.assertIn('JSON object', guard.continue_running())

    def test_huge_integer_active_reload_retains_interrupted_capture(self):
        with self.guard() as guard:
            self.write_approval(expires_unix=10 ** 400)
            self.mono += .3
            self.assertIn('bounded', guard.continue_running())
            def execute(*_args, continue_running, **_kwargs):
                self.write_approval(expires_unix=10 ** 400)
                self.mono += .3
                return {'stdout': b'interrupted output', 'stderr': b'', 'post_screen_stdout': b'',
                        'failure': continue_running(), 'metrics': {}}
            usage = {'user_ms': 0, 'system_ms': 0, 'rss_bytes': 1, 'lifetime_peak_rss_bytes': 1}
            evidence = self.approval.parent / 'capture'
            self.write_approval()
            with mock.patch.object(bench.p7, 'execute', side_effect=execute), \
                 mock.patch.object(bench, 'self_usage', return_value=usage):
                result = bench.Capture(guard, evidence_path=evidence)(['never-run'], {}, '.', {})
            self.assertIn('bounded', result['failure'])
            self.assertEqual((evidence / 'stdout.bin').read_bytes(), b'interrupted output')
            self.assertIn('bounded', json.loads((evidence / 'session.json').read_text())['failure'])

    def test_reload_exception_retains_interrupted_capture_and_releases_owned_test_lock(self):
        usage = {'user_ms': 0, 'system_ms': 0, 'rss_bytes': 1, 'lifetime_peak_rss_bytes': 1}
        for error in (RecursionError('nested approval'), MemoryError('approval allocation')):
            with self.subTest(error=type(error).__name__):
                self.write_approval()
                with self.guard() as guard:
                    def execute(*_args, continue_running, **_kwargs):
                        self.mono += .3
                        with mock.patch.object(guard, '_approval', side_effect=error):
                            failure = continue_running()
                        return {'stdout': b'interrupted output', 'stderr': b'', 'post_screen_stdout': b'',
                                'failure': failure, 'metrics': {}}
                    evidence = self.approval.parent / type(error).__name__
                    with mock.patch.object(bench.p7, 'execute', side_effect=execute), mock.patch.object(bench, 'self_usage', return_value=usage):
                        result = bench.Capture(guard, evidence_path=evidence)(['never-run'], {}, '.', {})
                    self.assertIn(str(error), result['failure'])
                    self.assertEqual((evidence / 'stdout.bin').read_bytes(), b'interrupted output')
                    self.assertIn(str(error), json.loads((evidence / 'session.json').read_text())['failure'])
                self.assertIsNone(guard.handle)

    def test_no_approval_no_lock_and_expiry_mid_child(self):
        self.write_approval(approved=False)
        with self.assertRaises(bench.QuietError):
            with self.guard():
                self.fail('unauthorized entry')
        self.assertFalse(self.lock.exists())
        self.write_approval()
        with self.guard() as guard:
            self.assertIsNone(guard.continue_running())
            self.now = 101
            self.assertIn('expired', guard.continue_running())
        with self.lock.open('a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # Released after failure.

    def test_nonblocking_contention_and_release_on_exception(self):
        with self.lock.open('a+') as other:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            guard = self.guard()
            with self.assertRaises(BlockingIOError):
                guard.__enter__()
            self.assertIsNone(guard.handle)
        guard = self.guard()
        with self.assertRaisesRegex(RuntimeError, 'mock failure'):
            with guard:
                raise RuntimeError('mock failure')
        self.assertIsNone(guard.handle)
        with self.guard():
            pass

    def test_companion_campaign_and_fresh_issue_time_are_required(self):
        for companion in ({'agreed':True,'by':'parent','reference':'old','at_unix':0,'campaign_id':'other'},
                          {'agreed':True,'by':'parent','reference':'old','at_unix':-100,'campaign_id':'unit-test'}):
            self.write_approval(companions={'phase6':companion})
            with self.assertRaisesRegex(bench.QuietError,'companion'):
                self.guard().__enter__()

    def test_budget_revocation_and_malformed_approval(self):
        with self.guard(budget=1) as guard:
            self.mono += 1
            self.assertIn('budget', guard.continue_running())
        with self.guard() as guard:
            self.write_approval(approved=False)
            self.mono += .3
            self.assertIn('not approved', guard.continue_running())
        self.approval.write_text('{')
        with self.assertRaises(ValueError):
            self.guard().__enter__()


class MetricsTests(unittest.TestCase):
    def test_dispatch_origin_excludes_evaluation_work_and_missing_clock_fails(self):
        case, result = synthetic()
        result['actions'][0].update(monotonic_ns=1000000, evaluated_monotonic_ns=1000000,
                                    dispatch_monotonic_ns=115000000)
        metric = next(p for p in bench.extract_metrics(result, case)['phases'] if p['name'] == 'edit')
        self.assertEqual(metric['first_expected_ms'], 5)
        self.assertIn('evaluation excluded', metric['dispatch_clock'])
        result['actions'][0].pop('dispatch_monotonic_ns')
        self.assertTrue(any('dispatch clock' in issue for issue in self.issues(result, case)))
        with self.assertRaisesRegex(ValueError, 'dispatch clock'):
            bench.extract_metrics(result, case)

    def test_dry_extraction_retains_status_only_and_noncausal_network_failure(self):
        case, result = synthetic()
        check = bench.extraction_check(result, case)
        self.assertTrue(check['valid'])
        self.assertEqual(set(check), {'valid', 'error', 'scope'})
        result['milestones'] = [{'request': 0, 'received_monotonic_ns': 30, 'events': [
            {'name': 'headers', 'monotonic_ns': 20}, {'name': 'complete', 'monotonic_ns': 40}],
            'body_bytes_sent': 5, 'wire_bytes_sent': 20}]
        check = bench.extraction_check(result, case)
        self.assertFalse(check['valid'])
        self.assertIn('non-causal', check['error'])
    def issues(self, result, case):
        with mock.patch.object(bench.p7, 'validate', return_value=[]):
            return bench.observation_issues(result, case)

    def test_unobserved_empty_descendant_set_cannot_enable_resource_trigger(self):
        case, result = synthetic()
        self.assertFalse(bench.extract_metrics(result, case)['cpu_rss_trigger_eligible'])
        next(p for p in case['bench']['phases'] if p['kind'] == 'idle')['long_idle'] = True
        self.assertFalse(next(p for p in bench.extract_metrics(result, case)['phases'] if p['kind'] == 'idle')['resources']['idle_cpu_trigger_eligible'])
        result['metrics']['descendant_comparison_eligible'] = True
        self.assertTrue(bench.extract_metrics(result, case)['cpu_rss_trigger_eligible'])
        self.assertTrue(next(p for p in bench.extract_metrics(result, case)['phases'] if p['kind'] == 'idle')['resources']['idle_cpu_trigger_eligible'])
        result['metrics'].pop('descendant_comparison_eligible')
        self.assertTrue(any('eligibility unknown' in x for x in self.issues(result, case)))

    def test_posthoc_causal_effects_never_use_late_request(self):
        case, result = synthetic()
        case['bench']['phases'][0]['effects'] = {'sql_window_counts': {'0': 1}, 'request_paths': ['/api/v1/query']}
        result['requests'] = [request(0)]
        result['milestones'] = [{'request': 0, 'received_monotonic_ns': 11000000}]
        self.assertTrue(any('causal phase' in x for x in self.issues(result, case)))
        result['milestones'][0]['received_monotonic_ns'] = 9000000
        self.assertEqual(self.issues(result, case), [])
        result['requests'].append(request(0))
        result['milestones'].append({'request': 1, 'received_monotonic_ns': 900000000})
        case['bench'].update(kind='sql-windowed', total_rows=500, cache_turnover=False)
        self.assertTrue(any('refetched' in x for x in self.issues(result, case)))

    def test_error_causal_receipt_and_no_retry_and_sustained_full_coverage_fail_closed(self):
        case, result = synthetic()
        endpoint = '/api/v1/query'
        case['bench'].update(kind='error', endpoint=endpoint)
        case['bench']['phases'][0]['effects'] = {'request_exact': {endpoint: 1}}
        result['requests'] = [request(0)]
        result['milestones'] = [{'request': 0, 'received_monotonic_ns': 11000000}]
        self.assertTrue(any('causal endpoint' in issue for issue in self.issues(result, case)))
        result['milestones'][0]['received_monotonic_ns'] = 9000000
        self.assertEqual(self.issues(result, case), [])
        result['requests'].append(request(0))
        result['milestones'].append({'request': 1, 'received_monotonic_ns': 900000000})
        self.assertTrue(any('retried/refetched' in issue for issue in self.issues(result, case)))
        case['bench'].update(kind='sql-windowed', total_rows=20000, cache_turnover=True, sustained_scrolling=True)
        case['bench']['phases'][0]['effects'] = {}
        result['requests'] = [request(offset) for offset in (0, 500, 1000, 1500, 0)]
        result['milestones'] = [{'request': i, 'received_monotonic_ns': 9000000} for i in range(len(result['requests']))]
        self.assertTrue(any('all large-result windows' in issue for issue in self.issues(result, case)))
        result['requests'] = [request(offset) for offset in range(0, 20000, 500)] + [request(0)]
        result['milestones'] = [{'request': i, 'received_monotonic_ns': 9000000} for i in range(len(result['requests']))]
        self.assertEqual(self.issues(result, case), [])

    def test_exec_failure_and_descendant_attribution_are_explicit(self):
        case, result = synthetic()
        result['exec_error'] = {'stage': 'execve', 'errno': 2}
        result['metrics']['resource_source'] = 'unavailable'
        result['metrics']['user_cpu_ms'] = None
        self.assertTrue(any('exec failure' in x for x in self.issues(result, case)))
        self.assertTrue(any('resource counter' in x for x in self.issues(result, case)))
        case, result = synthetic()
        result['metrics']['observed_descendants'] = [{'pid': 42, 'start_ticks': '123'}]
        extracted = bench.extract_metrics(result, case)
        self.assertFalse(extracted['cpu_rss_trigger_eligible'])
        self.assertEqual(extracted['observed_descendants'][0]['pid'], 42)

    def test_fork_floor_rejected_and_short_idle_tick_resolution_explicit(self):
        case, result = synthetic()
        extracted = bench.extract_metrics(result, case)
        idle = next(p['resources'] for p in extracted['phases'] if p['kind'] == 'idle')
        self.assertAlmostEqual(idle['tick_resolution_pp'], 100 / 90)
        self.assertFalse(idle['idle_cpu_trigger_eligible'])
        result['metrics']['resource_source'] = 'python_fork_wait4'
        self.assertTrue(any('fork-floor' in x for x in self.issues(result, case)))

    def test_stable_hold_old_frame_and_missing_counter_are_invalid(self):
        case, result = synthetic()
        self.assertEqual(self.issues(result, case), [])
        result['frames'][1]['stable_since_monotonic_ns'] = 200000000
        self.assertTrue(any('stable interval' in x for x in self.issues(result, case)))
        case, result = synthetic()
        del result['metrics']['proc_samples'][0]['io']
        result['metrics']['proc_samples'][1]['minor_faults'] = None
        self.assertTrue(any('missing Linux' in x for x in self.issues(result, case)))
        self.assertIsNone(result['metrics']['proc_samples'][1]['minor_faults'])
        case, result = synthetic()
        result['metrics']['proc_samples'][3]['user_ticks'] = 0
        self.assertTrue(any('decreased' in x for x in self.issues(result, case)))

    def test_missing_timestamp_and_idle_coverage_are_invalid(self):
        case, result = synthetic()
        del result['frames'][1]['stable_since_monotonic_ns']
        result['metrics']['proc_samples'] = result['metrics']['proc_samples'][:2]
        issues = self.issues(result, case)
        self.assertTrue(any('timestamp' in x for x in issues))
        self.assertTrue(any('coverage' in x for x in issues))

    def test_native_scope_and_observer_are_separate(self):
        case, result = synthetic()
        extracted = bench.extract_metrics(result, case)
        self.assertEqual(extracted['metrics']['cli_lifecycle_elapsed_ms'], 1500)
        self.assertIn('not pure compute', extracted['cli_lifecycle_elapsed_scope'])
        self.assertEqual(extracted['metrics']['edit_first_expected_ms'], 5)
        self.assertEqual(extracted['metrics']['edit_ready_stable_ms'], 95)
        self.assertEqual(extracted['metrics']['user_cpu_ms'], 0)  # Genuine zero retained.
        self.assertEqual(extracted['observer']['user_cpu_ms'], 7)
        idle = next(p['resources'] for p in extracted['phases'] if p['kind'] == 'idle')
        self.assertIn('main thread', idle['context_switch_scope'])
        self.assertAlmostEqual(idle['cpu_percent_one_core'], 40 / 900 * 100)
        self.assertEqual(idle['rss_growth_bytes'], 200)
        self.assertEqual(idle['io_sampled_delta']['wchar'], 40)
        self.assertEqual(idle['unobserved_start_ms'], 100)
        self.assertEqual(idle['unobserved_end_ms'], 100)
        self.assertEqual(extracted['child_environment'], {'HOME': '<HOME>'})

    def test_invalid_lifecycle_elapsed_is_not_retained(self):
        case, result = synthetic()
        for invalid in (None, 0, float('nan')):
            with self.subTest(invalid=invalid):
                result['reaped_monotonic_ns'] = invalid
                self.assertTrue(any('lifecycle timestamp' in x for x in self.issues(result, case)))
                with self.assertRaises((ValueError, TypeError)):
                    bench.extract_metrics(result, case)

    def test_network_is_causal_and_incomplete_is_invalid(self):
        case, result = synthetic()
        result['milestones'] = [{'request': 0, 'received_monotonic_ns': 100,
                                 'events': [{'name': 'headers', 'monotonic_ns': 200},
                                            {'name': 'complete', 'monotonic_ns': 1000200}],
                                 'body_bytes_sent': 20, 'wire_bytes_sent': 40}]
        self.assertEqual(bench.extract_metrics(result, case)['network'][0]['request_to_headers_ms'], .0001)
        result['milestones'][0]['events'].pop()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            bench.extract_metrics(result, case)

    def test_native_sampler_reuses_guarded_shared_read_without_reread(self):
        with mock.patch.object(bench.p7, 'proc_sample', return_value=None):
            self.assertIsNone(bench.native_proc_sample(123))
        complete = {'rss_bytes': 30, 'high_water_rss_bytes': 35, 'minor_faults': 11, 'major_faults': 2}
        with mock.patch.object(bench.p7, 'proc_sample', return_value=complete) as sampler, mock.patch.object(Path, 'read_text', side_effect=AssertionError('second /proc read')):
            self.assertIs(bench.native_proc_sample(123), complete)
        sampler.assert_called_once_with(123)

    def test_zero_ratios_signed_growth_and_invalid_campaign_no_subset(self):
        rows = [{'reference': {'x': 0}, 'candidate': {'x': 5}}] * 2
        self.assertIsNone(bench.paired_summary(rows, 'x', options())['paired']['ratio'])
        positive = [{'reference': {'x': 5}, 'candidate': {'x': 6}}] * 2
        self.assertIn('median_ratio', bench.paired_summary(positive, 'x', options())['paired'])
        growth = [{'reference': {'rss_growth_bytes': 5}, 'candidate': {'rss_growth_bytes': -1}}] * 2
        self.assertIsNone(bench.paired_summary(growth, 'rss_growth_bytes', options())['paired']['ratio'])
        self.assertIsNone(bench.summaries({'status': 'complete', 'pairs': [{'status': 'invalid'}]}, options()))
        for values in ([], [None], [float('nan')]):
            with self.assertRaises(ValueError):
                bench.distribution(values)


class AdapterTests(unittest.TestCase):
    def test_recorded_replay_program_and_minimal_no_descendant_probe(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            capture = bench.ReplayCapture(mock.Mock(), replay={'bytes': b'\x1b[31mstyled frame'}, frames=3, evidence_path=Path(folder))
            with mock.patch.object(bench.Capture, '__call__', side_effect=lambda command, *args: command):
                command = capture(['python'], {}, '.', {})
            compile(command[2], '<recorded-floor>', 'exec')
            self.assertEqual(Path(command[3]).read_bytes(), b'\x1b[31mstyled frame')
            self.assertIn('while os.read(0,1)!=b"x"', command[2])
        usage = {'user_ms': 0, 'system_ms': 0, 'rss_bytes': 1, 'lifetime_peak_rss_bytes': 1}
        with mock.patch.object(bench, 'self_usage', return_value=usage), mock.patch.object(bench.p7, 'execute', return_value={'metrics': {}}) as execute:
            bench.Capture(mock.Mock(), minimal=True)([], {}, '.', {})
        self.assertFalse(execute.call_args.kwargs['observer_config']['record_descendants'])

    def test_public_manifest_redacts_paths_and_host_has_no_node(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            path = Path(folder) / 'public.json'
            bench.write_report(path, {'path': str(bench.ROOT), 'cache': str(bench.p7.TMP)})
            public = path.read_text()
            self.assertNotIn('/home/ajs', public)
            self.assertIn('$WORKTREE', public)
            self.assertIn('$PB_CACHE', public)
        host = bench.host_snapshot()
        self.assertNotIn('uname', host)
        self.assertNotIn('node', host['platform'])
        self.assertIn('affinity', host)
        self.assertIn('governors', host)

    def test_source_provenance_includes_embedded_json_and_native_helper(self):
        with mock.patch.object(bench.subprocess, 'run', return_value=mock.Mock(stdout='a' * 40)):
            snapshot = bench.source_snapshot()
        self.assertIn('src/spec.json', snapshot['groups']['rust']['files'])
        self.assertIn('src/catalog.json', snapshot['groups']['rust']['files'])
        self.assertIn('harness/phase7_spawn.c', snapshot['groups']['harness']['files'])

    def test_shared_no_build_helper_identity_is_not_reimplemented(self):
        identity = {'source_sha256': 'a' * 64, 'sha256': 'b' * 64, 'flags': ['-O2']}
        def resolve(*, build=True, probe=True):
            self.assertFalse(build)
            return identity
        signature = bench.inspect.signature(resolve)
        with mock.patch.object(bench.p7, 'spawn_helper', side_effect=resolve) as resolver, mock.patch.object(bench.inspect, 'signature', return_value=signature):
            self.assertIs(bench.cached_spawn(), identity)
            self.assertIs(bench.cached_spawn(probe=False), identity)
        self.assertEqual(resolver.call_args_list, [mock.call(build=False, probe=True), mock.call(build=False, probe=False)])

    def test_real_execute_primary_and_minimal_fail_closed_after_helper_disappears(self):
        # Exercise the published execute/_execute configuration parsing and
        # forwarding. Only resolver/acquisition boundaries are intercepted.
        usage = {'user_ms': 0, 'system_ms': 0, 'rss_bytes': 1, 'lifetime_peak_rss_bytes': 1}
        for minimal in (False, True):
            with self.subTest(minimal=minimal), tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
                executable = Path(folder) / 'prepared-helper'
                executable.write_bytes(b'prepared')
                identity = {'path': str(executable), 'sha256': bench.sha_file(executable)}
                def resolve(build=True, *, probe=True):
                    self.assertFalse(build, 'capture attempted helper compilation')
                    if not executable.exists():
                        self.assertFalse(probe, 'capture attempted a preparation probe')
                        raise RuntimeError('prepared helper disappeared')
                    return identity
                signature = bench.inspect.signature(resolve)
                with mock.patch.object(bench.p7, 'spawn_helper', side_effect=resolve) as resolver, \
                     mock.patch.object(bench.inspect, 'signature', return_value=signature):
                    self.assertEqual(bench.cached_spawn(), identity)
                    executable.unlink()
                    with mock.patch.object(bench, 'self_usage', return_value=usage), \
                         mock.patch.object(bench.p7.os, 'openpty', side_effect=AssertionError('PTY acquired')), \
                         mock.patch.object(bench.p7.subprocess, 'check_output', side_effect=AssertionError('compiler probe')), \
                         mock.patch.object(bench.p7.subprocess, 'run', side_effect=AssertionError('build/probe subprocess')):
                        with self.assertRaisesRegex(RuntimeError, 'prepared helper disappeared'):
                            bench.Capture(mock.Mock(), minimal=minimal)(['never-run'], {}, '.', {})
                self.assertEqual(resolver.call_args_list, [mock.call(build=False, probe=True), mock.call(build=False, probe=False)])

    def test_real_harness_keyword_seams_exist_without_running_children(self):
        bench.require_seams()

    def test_capture_passes_shared_driver_and_records_actual_environment(self):
        guard = mock.Mock()
        before = {'user_ms': 10, 'system_ms': 20, 'rss_bytes': 100, 'lifetime_peak_rss_bytes': 200}
        after = {'user_ms': 15, 'system_ms': 23, 'rss_bytes': 110, 'lifetime_peak_rss_bytes': 200}
        with mock.patch.object(bench, 'self_usage', side_effect=[before, after]), \
             mock.patch.object(bench.p7, 'execute', return_value={'metrics': {}}) as execute:
            adapter = bench.Capture(guard)
            result = adapter(['binary'], {'HOME': '/tmp/home', 'TERM': 'xterm'}, '/tmp/home', {})
        kwargs = execute.call_args.kwargs
        self.assertFalse(kwargs['spawn_helper_build'])
        self.assertIs(kwargs['proc_sampler'], bench.native_proc_sample)
        self.assertEqual(kwargs['continue_running'], guard.continue_running)
        self.assertEqual(kwargs['observer_config'], {'record_termios': False, 'record_chunks': False, 'record_descendants': False})
        self.assertEqual(result['metrics']['observer']['user_cpu_ms'], 5)
        self.assertEqual(result['metrics']['child_environment']['HOME'], '/tmp/home')
        self.assertEqual(len(adapter.executions), 1)
        with mock.patch.object(bench, 'self_usage', return_value=before), \
             mock.patch.object(bench.p7, 'execute', return_value={'metrics': {}}) as execute:
            bench.Capture(guard, minimal=True, floor_profile=((100, 30), 'light'))([], {}, '/tmp', {})
        self.assertIsNone(execute.call_args.kwargs['proc_sampler'](123))
        self.assertEqual(execute.call_args.args[3]['size'], [100, 30])
        self.assertEqual(execute.call_args.args[1]['PB_THEME'], 'light')
        self.assertFalse(execute.call_args.kwargs['observer_config']['record_chunks'])

    def test_error_startup_can_supply_exact_validated_representative_floor_payload(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root = Path(folder)
            case = bench.campaign_cases(options(case_filters=('sql-query-error-*',)))[0]
            session_path = root / 'reference/session.json'
            session_path.parent.mkdir()
            session_path.write_text('{}')
            report_path = root / 'report.json'
            bench.write_json(report_path, {'cases': [{'case': case['name'], 'valid': True,
                'samples': {'reference': {'directory': 'reference', 'session_sha256': bench.sha_file(session_path)}}}]})
            session = {'case': case, 'frames': [{'name': 'startup', 'bytes_since_action': 5}], 'stdout': b'error-tail',
                       'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY}
            with mock.patch.object(bench.p7, 'load_session', return_value=session):
                replay = bench.replay_frame(options(case_filters=(case['name'],), preflight_report=str(report_path)), (120, 40), 'dark')
            self.assertEqual(replay['bytes'], b'error')
            self.assertEqual(replay['sha256'], bench.hashlib.sha256(b'error').hexdigest())

    def test_floor_failure_keeps_partial_control_manifest(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            def failed_floor(_output, _approval, sessions, frames, *, execute_fn):
                execute_fn.executions.append({'failure': 'quiet window expired'})
                raise RuntimeError('invalid observer floor')
            with mock.patch.object(bench.p7, 'observer_floor', side_effect=failed_floor), \
                 self.assertRaisesRegex(RuntimeError, 'invalid observer floor'):
                bench.observer_controls(Path(folder), Path('approval'), options(), mock.Mock())
            retained = json.loads((Path(folder) / 'tiny-frame/controls-progress.json').read_text())
            self.assertEqual(retained[0]['status'], 'invalid')
            mode = next(iter(retained[0]['modes'].values()))
            self.assertFalse(mode['valid'])
            self.assertTrue(mode['issues'])
            self.assertEqual(mode['native_and_observer_metrics'][0]['failure'], 'quiet window expired')

    def test_floor_uses_outer_guard_and_published_injection_no_nested_lock(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            guard = mock.Mock()
            def floor(_output, _approval, sessions, frames, *, execute_fn):
                self.assertEqual(sessions, 1)
                self.assertIn(execute_fn.policy, ('primary', 'floor-minimal'))
                self.assertFalse(bench.OBSERVER_POLICIES[execute_fn.policy]['record_descendants'])
                execute_fn.executions.append({'user_cpu_ms': 1})
                return {'sessions': [{'median_ms': 1, 'max_ms': 2}]}
            with mock.patch.object(bench, 'replay_frame', return_value={'bytes': b'replayed', 'sha256': 'a' * 64}), \
                 mock.patch.object(bench.p7, 'observer_floor', side_effect=floor) as observer, \
                 mock.patch.object(bench, 'capture_file_hashes', return_value={'synthetic': True}), \
                 mock.patch.object(bench, 'QuietWindow', side_effect=AssertionError('nested lock')):
                rows = bench.observer_controls(Path(folder), Path('approval'), options(), guard)
            self.assertEqual(observer.call_count, 4)
            self.assertEqual(set(rows[0]['modes']), {'primary', 'minimal'})
            self.assertTrue(guard.check.called)


class CampaignTests(unittest.TestCase):
    def test_metadata_interpretation_digest_one_snapshot_and_canonical_selection_used(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root = Path(folder)
            binary, other, selected = root / 'native', root / 'other', root / 'selected'
            for file in (binary, other):
                file.write_bytes(b'identical executable fixture')
                file.chmod(0o700)
            selected.symlink_to(binary)
            metadata = root / 'metadata.json'
            value = {'binary_sha256': bench.sha_file(binary), 'optimized': True, 'language': 'rust',
                     'profile': 'release', 'source_commit': 'a' * 40, 'source_tree_sha256': 'b' * 64}
            bench.write_json(metadata, value)
            old = metadata.read_bytes()
            original_read = Path.read_bytes
            def read(path):
                payload = original_read(path)
                if path == metadata:
                    changed = {**value, 'source_tree_sha256': 'c' * 64}
                    staged = root / 'replacement'
                    bench.write_json(staged, changed)
                    staged.replace(metadata)
                return payload
            with mock.patch.object(Path, 'read_bytes', read):
                artifact = bench.artifact(selected, metadata, 'candidate')
            self.assertEqual(artifact['build_provenance']['source_tree_sha256'], 'b' * 64)
            self.assertEqual(artifact['provenance_file_sha256'], bench.hashlib.sha256(old).hexdigest())
            with self.assertRaisesRegex(ValueError, 'metadata changed'):
                bench.check_artifact(artifact)
            selected.unlink()
            selected.symlink_to(other)
            case, _result = synthetic()
            usage = {'user_ms': 0, 'system_ms': 0, 'rss_bytes': 1, 'lifetime_peak_rss_bytes': 1}
            with mock.patch.object(bench, 'self_usage', return_value=usage), \
                 mock.patch.object(bench.p7, 'execute', return_value={'metrics': {}}) as execute:
                adapter = bench.Capture(mock.Mock(), identity=artifact['identity'])
                adapter([artifact['identity']['path'], *case['argv']], {}, root, case)
            self.assertEqual(execute.call_args.args[0][0], str(binary))
            self.assertEqual(adapter.commands[0][0], str(binary))

    def test_provenance_matches_actual_file_and_requires_release(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            binary, meta = Path(folder) / 'binary', Path(folder) / 'meta.json'
            binary.write_bytes(b'not launched')
            binary.chmod(0o700)
            value = {'binary_sha256': bench.sha_file(binary), 'optimized': True, 'language': 'rust',
                     'profile': 'release', 'source_commit': 'a' * 40, 'source_tree_sha256': 'b' * 64}
            bench.write_json(meta, value)
            self.assertEqual(bench.artifact(binary, meta, 'candidate')['identity']['bytes'], 12)
            value['profile'] = 'debug'
            bench.write_json(meta, value)
            with self.assertRaisesRegex(ValueError, 'release'):
                bench.artifact(binary, meta, 'candidate')
            value.update(language='go', source_commit=bench.PROVENANCE_COMMIT)
            bench.write_json(meta, value)
            bench.artifact(binary, meta, 'reference')
            binary.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'match actual'):
                bench.artifact(binary, meta, 'reference')

    def campaign(self, folder, *, comparison=None, fail_sample=False, controls_failure=False,
                 final_replacement=False, retarget=False, bad_return_identity=False):
        root = Path(folder)
        approval = root / 'approval.json'
        now = bench.time.time()
        bench.write_json(approval, {'approved': True, 'scope': 'phase7', 'issued_unix': now - 1, 'expires_unix': now + 300,
            'campaign_id': 'unit-test', 'companions': {'phase6': {'agreed': True, 'campaign_id': 'unit-test', 'by': 'parent', 'at_unix': now - 1, 'reference': 'test-agreement'}}})
        preflight = root / 'preflight.json'
        bench.write_json(preflight, {'valid': True})
        binary = root / 'binary'
        binary.write_bytes(b'never executed')
        binary.chmod(0o700)
        identity = {'identity': bench.p7.phase1.binary_identity(binary)}
        guards = []
        original_quiet = bench.QuietWindow
        def quiet(path, budget, **kwargs):
            guard = original_quiet(path, budget, lock_path=root / 'lock', **kwargs)
            guards.append(guard)
            return guard
        case, result = synthetic()
        definition = {'cases': [case], 'options': options(campaign_id='unit-test', preflight_report=str(preflight)).__dict__, 'pairs': [
            {'case': 'synthetic', 'purpose': 'measured', 'round': i, 'order': ['reference', 'candidate']}
            for i in range(2)]}
        sample_count = 0
        def sample(_binary, _case, path, *, execute_fn):
            nonlocal sample_count
            sample_count += 1
            path.mkdir(parents=True)
            (path / 'stdout.bin').write_bytes(b'')
            (path / 'stderr.bin').write_bytes(b'')
            bench.write_json(path / 'session.json', {'mock_evidence': True})
            if fail_sample:
                raise RuntimeError('capture interrupted')
            captured = copy.deepcopy(result)
            captured.update(binary=identity['identity'], capture_command=[str(binary), *_case['argv']], observer_policy='primary')
            if sample_count == 4 and (final_replacement or retarget):
                if retarget:
                    other = root / 'retarget'
                    other.write_bytes(binary.read_bytes())
                    other.chmod(0o700)
                    binary.unlink()
                    binary.symlink_to(other)
                else:
                    binary.write_bytes(b'last role replaced')
                captured['binary'] = bench.p7.phase1.binary_identity(binary)
            if bad_return_identity:
                captured['binary'] = {**identity['identity'], 'bytes': 12345}
            return captured
        with mock.patch.object(bench, 'plan', return_value=definition), \
             mock.patch.object(bench, 'campaign_feasibility', return_value={'estimated_fits':True,'estimated_seconds':10}), \
             mock.patch.object(bench, 'validate_preflight', return_value={'native_spawn': {'source_sha256': 'a' * 64, 'sha256': 'b' * 64},
                               'validated_report_identity': bench.json_snapshot(preflight)[1]}), \
             mock.patch.object(bench, 'cached_spawn', return_value={'source_sha256': 'a' * 64, 'sha256': 'b' * 64}), \
             mock.patch.object(bench, 'artifact', return_value=identity), \
             mock.patch.object(bench, 'source_snapshot', return_value={'source': 'same'}), \
             mock.patch.object(bench, 'host_snapshot', return_value={'load_average': [0, 0, 0]}), \
             mock.patch.object(bench, 'QuietWindow', side_effect=quiet), \
             mock.patch.object(bench, 'observer_controls', side_effect=RuntimeError('floor failed') if controls_failure else None,
                               return_value=[]), \
             mock.patch.object(bench.p7, 'sample', side_effect=sample, autospec=True), \
             mock.patch.object(bench.p7, 'validate', return_value=[]), \
             mock.patch.object(bench.p7, 'compare', return_value=comparison or []):
            report = bench.run_campaign(binary, binary, 'unused', 'unused', root / 'out', approval, options(campaign_id='unit-test', preflight_report=str(preflight)))
        self.assertIsNone(guards[0].handle)
        with (root / 'lock').open('a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(json.loads((root / 'out/report.json').read_text())['status'], report['status'])
        return report

    def test_valid_mock_pairs_summarize_independent_sessions(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            report = self.campaign(folder)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(len(report['pairs']), 2)
        self.assertEqual(report['summary']['synthetic']['edit_first_expected_ms']['reference']['count'], 2)
        self.assertTrue(all(pair['samples']['reference']['valid'] for pair in report['pairs']))

    def test_comparison_failure_retains_both_samples_no_timings_or_subset(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            report = self.campaign(folder, comparison=['styled cells differ'])
            for row in report['pairs'][0]['samples'].values():
                self.assertTrue((Path(folder) / 'out' / row['artifact'] / 'session.json').is_file())
                self.assertFalse(row['valid'])
                self.assertIsNone(row['measurements'])
        self.assertEqual(report['status'], 'failed')
        self.assertIsNone(report['summary'])
        self.assertEqual(len(report['unattempted_pairs']), 1)

    def test_final_role_replacement_and_same_bytes_path_retarget_fail_retained(self):
        for flags in ({'final_replacement': True}, {'retarget': True}, {'bad_return_identity': True}):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
                report = self.campaign(folder, **flags)
                self.assertEqual(report['status'], 'failed')
                self.assertIsNone(report['summary'])
                row = report['pairs'][-1]['samples']['candidate' if len(report['pairs']) == 2 else 'reference']
                self.assertTrue((Path(folder) / 'out' / row['artifact'] / 'session.json').is_file())
                self.assertIsNone(row['measurements'])
                self.assertIn('artifacts_after', report)

    def test_capture_and_floor_failures_release_lock_and_preserve_failure(self):
        for setting in ({'fail_sample': True}, {'controls_failure': True}):
            with self.subTest(setting=setting), tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
                report = self.campaign(folder, **setting)
            self.assertEqual(report['status'], 'failed')
            self.assertTrue(report['errors'])
            self.assertIsNone(report['summary'])
            if setting.get('fail_sample'):
                self.assertIsNone(report['pairs'][0]['samples']['reference']['measurements'])

    def test_plan_cli_never_executes_a_child(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder, \
             mock.patch.object(bench.p7, 'sample', side_effect=AssertionError('child')), \
             mock.patch.object(bench.p7, 'execute', side_effect=AssertionError('child')), \
             mock.patch.object(bench.p7, 'observer_floor', side_effect=AssertionError('child')):
            out = Path(folder) / 'plan.json'
            self.assertEqual(bench.main(['plan', '--out', str(out), '--case', 'sql-editor-*', '--theme', 'dark']), 0)
            self.assertIn('no child launches', json.loads(out.read_text())['mode'])


class FunctionalPreflightTests(unittest.TestCase):
    def test_preflight_retains_artifact_hashes_and_rejects_changed_final_candidate(self):
        with tempfile.TemporaryDirectory(dir=bench.p7.TMP) as folder:
            root = Path(folder)
            binary = root / 'binary'
            binary.write_bytes(b'not executed')
            binary.chmod(0o700)
            case, result = synthetic()
            case.update(size=[120, 40], theme='dark')
            result.update(stdout=b'frame bytes', stderr=b'', post_screen_stdout=b'')
            helper = {'source_sha256': 'a' * 64, 'sha256': 'b' * 64}
            def sample(_binary, _case, path, *, execute_fn):
                path.mkdir(parents=True)
                serial = copy.deepcopy(result)
                serial.update(binary=bench.p7.phase1.binary_identity(binary), capture_command=[str(binary), *_case['argv']], observer_policy='primary')
                captured = copy.deepcopy(serial)
                for field in ('stdout', 'stderr', 'post_screen_stdout'):
                    serial[field + '_hex'] = serial.pop(field).hex()
                bench.write_json(path / 'session.json', serial)
                (path / 'stdout.bin').write_bytes(result['stdout'])
                (path / 'stderr.bin').write_bytes(result['stderr'])
                return captured
            with mock.patch.object(bench, 'require_seams'), mock.patch.object(bench, 'cached_spawn', return_value=helper), mock.patch.object(bench, 'source_snapshot', return_value={}), mock.patch.object(bench, 'campaign_cases', return_value=[case]), mock.patch.object(bench.p7, 'sample', side_effect=sample), mock.patch.object(bench.p7, 'validate', return_value=[]), mock.patch.object(bench.p7, 'compare', return_value=[]):
                report = bench.functional_preflight(binary, binary, root / 'out', options(), lock_path=root / 'functional.lock')
            with mock.patch.object(bench, 'require_seams'), mock.patch.object(bench, 'cached_spawn', return_value=helper), mock.patch.object(bench, 'source_snapshot', return_value={}), mock.patch.object(bench, 'campaign_cases', return_value=[case]), mock.patch.object(bench.p7, 'sample', side_effect=sample), mock.patch.object(bench.p7, 'validate', return_value=[]), mock.patch.object(bench.p7, 'compare', return_value=[]), mock.patch.object(bench, 'extract_metrics', side_effect=ValueError('incomplete network milestone')):
                failed_extraction = bench.functional_preflight(binary, binary, root / 'dry-failure', options(), lock_path=root / 'functional.lock')
            self.assertFalse(failed_extraction['valid'])
            for sample_row in failed_extraction['cases'][0]['samples'].values():
                self.assertFalse(sample_row['extraction_check']['valid'])
                self.assertIn('incomplete network milestone', sample_row['extraction_check']['error'])
                self.assertNotIn('measurements', sample_row)
            self.assertTrue(report['valid'])
            self.assertEqual(report['kind'], 'functional preflight; no timing claim')
            for sample_row in report['cases'][0]['samples'].values():
                self.assertEqual(set(sample_row['extraction_check']), {'valid', 'error', 'scope'})
                self.assertTrue(sample_row['extraction_check']['valid'])
            row = report['cases'][0]['samples']['reference']
            self.assertEqual(row['session_sha256'], bench.sha_file(root / 'out' / row['directory'] / 'session.json'))
            identity = bench.p7.phase1.binary_identity(binary)
            identities = {role: {'identity': identity} for role in ('reference', 'candidate')}
            current_helper = {**helper, 'path': str(root / 'native-helper')}
            with mock.patch.object(bench, 'cached_spawn', return_value=current_helper), \
                 mock.patch.object(bench, 'source_snapshot', return_value={}), \
                 mock.patch.object(bench.p7, 'validate', return_value=[]), \
                 mock.patch.object(bench.p7, 'compare', return_value=[]):
                validated = bench.validate_preflight(root / 'out/report.json', {'cases': [case]}, identities)
                self.assertEqual(validated['native_spawn'], current_helper)
                with mock.patch.object(bench, 'extract_metrics', side_effect=ValueError('noncausal dry extraction')), self.assertRaisesRegex(ValueError, 'noncausal dry extraction'):
                    bench.validate_preflight(root / 'out/report.json', {'cases': [case]}, identities)
                changed = copy.deepcopy(identities)
                changed['candidate']['identity']['sha256'] = 'c' * 64
                with self.assertRaisesRegex(ValueError, 'artifact SHA'):
                    bench.validate_preflight(root / 'out/report.json', {'cases': [case]}, changed)
                changed_case = copy.deepcopy(case)
                changed_case['theme'] = 'light'
                with self.assertRaisesRegex(ValueError, 'definition'):
                    bench.validate_preflight(root / 'out/report.json', {'cases': [changed_case]}, identities)
                report_path = root / 'out/report.json'
                original = json.loads(report_path.read_text())
                for kind in ('missing_roles', 'stale_source', 'missing_end_helper', 'missing_end_artifact', 'observer_policy','resize_policy'):
                    altered = copy.deepcopy(original)
                    if kind == 'missing_roles':
                        altered['cases'][0]['samples'] = {}
                    elif kind == 'stale_source':
                        altered['source'] = {'stale': True}
                    elif kind == 'missing_end_helper':
                        altered.pop('native_spawn_after')
                    elif kind == 'missing_end_artifact':
                        altered.pop('artifacts_after')
                    elif kind == 'resize_policy':
                        altered['resize_signal_policy']='legacy'
                    else:
                        altered['observer_policy'] = 'floor-minimal'
                    bench.write_json(report_path, altered)
                    with self.subTest(kind=kind), self.assertRaises(ValueError):
                        bench.validate_preflight(report_path, {'cases': [case]}, identities)
                bench.write_json(report_path, original)
                candidate_dir = root / 'out' / original['cases'][0]['samples']['candidate']['directory']
                stdout = candidate_dir / 'stdout.bin'
                original_bytes = stdout.read_bytes()
                stdout.write_bytes(b'changed retained stream')
                with self.assertRaisesRegex(ValueError, 'hash differs'):
                    bench.validate_preflight(report_path, {'cases': [case]}, identities)
                stdout.write_bytes(original_bytes)
                stderr = candidate_dir / 'stderr.bin'
                stderr.unlink()
                with self.assertRaisesRegex(ValueError, 'missing'):
                    bench.validate_preflight(report_path, {'cases': [case]}, identities)
                stderr.write_bytes(b'')
                session_path = candidate_dir / 'session.json'
                old_session = session_path.read_bytes()
                session = json.loads(old_session)
                session['frames'][0]['frame']['cells'][0][0][0] = 'X'
                bench.write_json(session_path, session)
                altered = copy.deepcopy(original)
                altered['cases'][0]['samples']['candidate']['session_sha256'] = bench.sha_file(session_path)
                bench.write_json(report_path, altered)
                with self.assertRaisesRegex(ValueError, 'no longer satisfies'):
                    bench.validate_preflight(report_path, {'cases': [case]}, identities)
                session_path.write_bytes(old_session)
                bench.write_json(report_path, original)
                with mock.patch.object(bench.p7, 'compare', return_value=['new comparator rejection']), self.assertRaisesRegex(ValueError, 'comparison differs'):
                    bench.validate_preflight(report_path, {'cases': [case]}, identities)
                original_read = Path.read_bytes
                swapped = False
                def read_once(file):
                    nonlocal swapped
                    encoded = original_read(file)
                    if file == report_path and not swapped:
                        swapped = True
                        staged = root / 'report-replacement.json'
                        bench.write_json(staged, {**original, 'valid': False})
                        staged.replace(report_path)
                    return encoded
                with mock.patch.object(Path, 'read_bytes', read_once), self.assertRaisesRegex(ValueError, 'JSON evidence identity'):
                    bench.validate_preflight(report_path, {'cases': [case]}, identities)
                self.assertTrue(swapped)
                self.assertTrue(session_path.is_file())
            calls = 0
            def final_drift(_binary, _case, path, *, execute_fn):
                nonlocal calls
                captured = sample(_binary, _case, path, execute_fn=execute_fn)
                calls += 1
                if calls == 2:
                    binary.write_bytes(b'replaced during final preflight role')
                    captured['binary'] = bench.p7.phase1.binary_identity(binary)
                return captured
            with mock.patch.object(bench, 'cached_spawn', return_value=helper), \
                 mock.patch.object(bench, 'source_snapshot', return_value={}), \
                 mock.patch.object(bench, 'campaign_cases', return_value=[case]), \
                 mock.patch.object(bench.p7, 'sample', side_effect=final_drift, autospec=True), \
                 mock.patch.object(bench.p7, 'validate', return_value=[]), \
                 mock.patch.object(bench.p7, 'compare', return_value=[]):
                failed = bench.functional_preflight(binary, binary, root / 'drift', options(), lock_path=root / 'functional.lock')
            self.assertFalse(failed['valid'])
            self.assertTrue(failed['errors'])
            self.assertTrue((root / 'drift' / case['name'] / 'candidate/session.json').is_file())
            self.assertIn('artifacts_after', failed)

    def test_long_idle_timeout_extends_base_and_is_explicitly_eligible(self):
        case = bench.sql_case({'name': 'sql-scroll-20000'}, (120, 40), 'dark', options(session_timeout=60), idle=300)
        self.assertEqual(case['timeout'], 360)
        idle = next(p for p in case['bench']['phases'] if p['kind'] == 'idle')
        self.assertTrue(idle['long_idle'])
        self.assertEqual(idle['duration_ms'], 300000)


class PostIdleBracketHoldTests(unittest.TestCase):
    def case(self, policy='resource-attribution', idle=300):
        return bench.sql_case({'name':'sql-scroll-20000'},(120,40),'dark',
                              options(observer_policy=policy,session_timeout=60),idle=idle)

    def assert_definition(self, module):
        primary=module.sql_case({'name':'sql-scroll-20000'},(120,40),'dark',options(session_timeout=60),idle=300)
        case=module.sql_case({'name':'sql-scroll-20000'},(120,40),'dark',
                            options(observer_policy='resource-attribution',session_timeout=60),idle=300)
        self.assertEqual(case['timeout'],360.25)
        self.assertEqual(primary['timeout'],360)
        self.assertEqual(case['actions'][:-2],primary['actions'][:-1])
        self.assertEqual(case['actions'][-1],primary['actions'][-1])
        self.assertEqual(case['bench']['phases'][:-1],primary['bench']['phases'])
        hold=case['bench']['phases'][-1]
        self.assertEqual(hold['name'],'post-idle-bracket-hold');self.assertEqual(hold['kind'],'anchor')
        self.assertEqual(hold['duration_ms'],250);self.assertEqual(hold['start_checkpoint'],'idle-end')
        self.assertEqual(hold['trigger_action_index'],None)
        self.assertEqual(case['actions'][-2]['checkpoint'],hold['name'])
        self.assertEqual(case['actions'][-2]['stable_ms'],80)
        self.assertEqual(case['actions'][-2]['wait']['min_elapsed_ms'],170)
        self.assertFalse(set(case['actions'][-2]) & {'key','send','resize','signal','release'})
        self.assertEqual(case['fixture'],primary['fixture']);self.assertEqual(case['argv'],primary['argv'])
        for original,proposed in zip(primary['bench']['phases'],case['bench']['phases']):
            self.assertEqual(original,proposed)  # S/E and300000ms untouched.
        short=module.sql_case({'name':'sql-scroll-20000'},(120,40),'dark',options(observer_policy='resource-attribution',session_timeout=60))
        self.assertNotIn('post-idle-bracket-hold',[phase['name'] for phase in short['bench']['phases']])
        return case

    def test_only_attribution_long_changes_and_timeout_covers_hold(self):
        self.assert_definition(bench)

    def test_frozen_commit_and_readiness_stability_prove_minimum_from_E(self):
        import ast
        tree=ast.parse(inspect.getsource(bench.p7._execute))
        stamp=next(node for node in ast.walk(tree) if isinstance(node,ast.Assign) and
                   any(isinstance(target,ast.Name) and target.id=='stamp' for target in node.targets))
        ready=next(value for key,value in zip(stamp.value.keys,stamp.value.values) if isinstance(key,ast.Constant) and key.value=='ready_monotonic_ns')
        self.assertIsInstance(ready,ast.Name);self.assertEqual(ready.id,'now')
        advance=next(node for node in ast.walk(tree) if isinstance(node,ast.Assign) and
            any(isinstance(target,ast.Tuple) and [item.id for item in target.elts if isinstance(item,ast.Name)]==['action_started','bytes_at_action'] for target in node.targets))
        reset=next(node for node in ast.walk(tree) if isinstance(node,ast.Assign) and
                   [target.id for target in node.targets if isinstance(target,ast.Name)]==['first_match','stable_since','last_hash'])
        append=next(node for node in ast.walk(tree) if isinstance(node,ast.If) and isinstance(node.test,ast.Compare) and
                    ast.dump(node.test)==ast.dump(ast.parse("'checkpoint' in action",mode='eval').body))
        self.assertLess(stamp.lineno,append.lineno);self.assertLess(append.lineno,advance.lineno);self.assertLess(advance.lineno,reset.lineno)
        E=1_000_000_000
        state={'now':E,'first_match':E-80_000_000,'stable_since':E-80_000_000,'last_hash':'old',
            'action_started':1000,'last_write':E-100_000_000,'raw':b'','bytes_at_action':0,
            'frame':{},'tty':{},'action':{'checkpoint':'idle-end'},'index':10,'frames':[]}
        snippet=ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(node) for node in (stamp,append,advance,reset)],type_ignores=[]))
        exec(compile(snippet,'<frozen-checkpoint-commit-AST>','exec'),state)
        self.assertEqual(state['frames'][0]['ready_monotonic_ns'],E)
        self.assertEqual(state['action_started'],E)
        self.assertIsNone(state['first_match']);self.assertIsNone(state['stable_since']);self.assertIsNone(state['last_hash'])
        case=self.case();wait=case['actions'][-2]['wait']
        terminal=bench.p7.Terminal(120,40);terminal.feed(b'\x1b[?1049hrows 1/20000\r\nbench-row-000000')
        tty={'canonical':False,'echo':False,'signals':False}
        predicate=bench.Readiness(case)
        self.assertFalse(predicate(terminal,wait,[],tty,E,E+169_999_999))
        self.assertTrue(predicate(terminal,wait,[],tty,E,E+170_000_000))
        # Execute the exact frozen acceptance gate AST in a synthetic timeline;
        # no native loop or clock, no OS I/O, no assertion of physical delay.
        loop=next(node for node in ast.walk(tree) if isinstance(node,ast.While) and any(isinstance(item,ast.Assign) and
            any(isinstance(target,ast.Name) and target.id=='fingerprint' for target in item.targets) for item in node.body))
        at=next(index for index,item in enumerate(loop.body) if isinstance(item,ast.Assign) and
            any(isinstance(target,ast.Name) and target.id=='fingerprint' for target in item.targets))
        gates=copy.deepcopy(loop.body[at:at+4])
        body=ast.parse("if not readiness(terminal, wait, [], tty, action_started, now):\n    stable_since = last_hash = None\n    continue\n").body
        body.extend(gates);body.extend(ast.parse('accepted.append(now)\nbreak').body)
        driver=ast.fix_missing_locations(ast.Module(body=[ast.For(target=ast.Name(id='now',ctx=ast.Store()),iter=ast.Name(id='timeline',ctx=ast.Load()),body=body,orelse=[])],type_ignores=[]))
        state.update(readiness=bench.Readiness(case),terminal=terminal,wait=wait,tty=tty,digest=bench.p7.digest,
                     timeline=[E+169_999_999,E+170_000_000,E+249_999_000,E+250_000_000],accepted=[])
        exec(compile(driver,'<frozen-stability-gate-synthetic-time>','exec'),state)
        self.assertEqual(state['accepted'],[E+250_000_000])
        history=state['readiness'].offsets[E]
        self.assertEqual(history['history_scope'],'omitted non-latency checkpoint')
        self.assertEqual(history['evaluations'],[]);self.assertEqual(history['evaluation_count'],0)

    def raw_hold(self, ready_ns=1_250_000_000):
        _original,raw=synthetic()
        hold=copy.deepcopy(self.case()['bench']['phases'][-1])
        text='rows 1/20000\nbench-row-000000'
        # Deliberate synthetic160ms first/stable stamp leaves90ms stable at
        #250ms: isolates the NEW elapsed check from the existing80ms gate.
        captured={'name':hold['name'],'frame':frame(text,120,40),'first_useful_monotonic_ns':1_160_000_000,
            'first_ever_match_monotonic_ns':1_160_000_000,'stable_since_monotonic_ns':1_160_000_000,
            'ready_monotonic_ns':ready_ns,'last_output_monotonic_ns':1_000_000_000,'bytes_since_action':0}
        case={'name':'unit-hold','size':[120,40],'actions':[],'resize_signal_policy':bench.RESIZE_SIGNAL_POLICY,
              'bench':{'kind':'unit','comparison':'go-rust','phases':[hold]}}
        raw.update(case=case,frames=[{'name':'idle-end','ready_monotonic_ns':1_000_000_000},captured],actions=[],
            requests=[{'method':'POST','path':'/api/v1/query','body':{'query':'SELECT * FROM logs LIMIT 500 OFFSET 0'}}],
            milestones=[{'request':0,'received_monotonic_ns':1,'events':[{'name':'headers','monotonic_ns':2},{'name':'complete','monotonic_ns':3}],'body_bytes_sent':0,'wire_bytes_sent':0}])
        return case,raw

    def test_independent_elapsed_check_exact_boundary_and_missing_metadata(self):
        case,raw=self.raw_hold()
        with mock.patch.object(bench.p7,'validate',return_value=[]):
            self.assertEqual(bench.observation_issues(raw,case,require_resources=False),[])
            for ready in (1_249_999_000,1_249_000_000):
                bad=copy.deepcopy(raw);bad['frames'][-1]['ready_monotonic_ns']=ready
                issues=bench.observation_issues(bad,case,require_resources=False)
                self.assertTrue(any('post-idle hold interval shorter' in issue for issue in issues))
                self.assertFalse(any('stable interval invalid' in issue for issue in issues))
            for field in ('duration_ms','start_checkpoint'):
                altered=copy.deepcopy(case);altered['bench']['phases'][0].pop(field)
                self.assertIn('malformed post-idle hold definition',bench.observation_issues(raw,altered,require_resources=False))
            for key,value in (('duration_ms',True),('duration_ms',249),('duration_ms',250.0),('duration_ms','250'),('start_checkpoint','idle-start')):
                altered=copy.deepcopy(case);altered['bench']['phases'][0][key]=value
                self.assertIn('malformed post-idle hold definition',bench.observation_issues(raw,altered,require_resources=False))
            no_start=copy.deepcopy(raw);no_start['frames']=no_start['frames'][1:]
            self.assertTrue(any('missing start' in issue for issue in bench.observation_issues(no_start,case,require_resources=False)))
            no_hold=copy.deepcopy(raw);no_hold['frames']=no_hold['frames'][:1]
            self.assertIn('missing phase checkpoint: post-idle-bracket-hold',bench.observation_issues(no_hold,case,require_resources=False))
            ordinary=copy.deepcopy(case);ordinary['bench']['phases'][0].update(name='ordinary-anchor')
            ordinary['bench']['phases'][0].pop('duration_ms');ordinary['bench']['phases'][0].pop('start_checkpoint')
            ordinary_raw=copy.deepcopy(raw);ordinary_raw['frames'][-1]['name']='ordinary-anchor'
            self.assertEqual(bench.observation_issues(ordinary_raw,ordinary,require_resources=False),[])

    def test_float_duration_control_kills_compiled_type_guard_removal(self):
        source=inspect.getsource(bench.observation_issues)
        guard="type(phase.get('duration_ms')) is not int or "
        self.assertEqual(source.count(guard),1)
        namespace=dict(vars(bench))
        exec(compile(source.replace(guard,''),'<post-idle-drop-duration-type-guard>','exec'),namespace)
        case,raw=self.raw_hold()
        case['bench']['phases'][0]['duration_ms']=250.0
        with mock.patch.object(bench.p7,'validate',return_value=[]):
            self.assertIn('malformed post-idle hold definition',bench.observation_issues(raw,case,require_resources=False))
            self.assertEqual(namespace['observation_issues'](raw,case,require_resources=False),[])
        with mock.patch.object(bench,'observation_issues',namespace['observation_issues']):
            with mock.patch.object(bench.p7,'validate',return_value=[]):
                with self.assertRaisesRegex(AssertionError,'malformed post-idle hold definition'):
                    self.assertIn('malformed post-idle hold definition',bench.observation_issues(raw,case,require_resources=False))

    def test_hold_excluded_from_decode_latency_but_whole_session_counters_retained(self):
        case,raw=self.raw_hold()
        hold=case['bench']['phases'][0]
        self.assertEqual(bench.decode_phase({},hold),{'status':'not-applicable','scope':'no latency trigger'})
        result=bench.extract_metrics(raw,case)
        self.assertEqual(result['phases'],[])
        self.assertFalse(any(key.startswith('post-idle-bracket-hold_') for key in result['metrics']))
        self.assertEqual(result['metrics']['cli_lifecycle_elapsed_ms'],1500)
        for key in ('user_cpu_ms','system_cpu_ms','peak_rss_bytes'):
            self.assertEqual(result['metrics'][key],raw['metrics'][key])
        original=copy.deepcopy(raw);original['reaped_monotonic_ns']-=250_000_000
        original['metrics']['proc_samples']=[sample for sample in original['metrics']['proc_samples'] if sample['monotonic_ns']<=original['reaped_monotonic_ns']]
        self.assertEqual(result['metrics']['cli_lifecycle_elapsed_ms']-bench.extract_metrics(original,case)['metrics']['cli_lifecycle_elapsed_ms'],250)


class A1RetryPromqlGeometryTests(unittest.TestCase):
    """Actual A1-retry Go/Rust PromQL table frames: footer H-8 ending W-3, blank H-7/H-6, border H-5, table hint H-3."""
    FIXTURE = bench.ROOT / 'docs/evidence/phase7/a1retry-promql-retained.json.gz'
    FIXTURE_SHA256 = '12a14654697b1c7b10f328d998382eddf2fb0e67c662fbc62f37ff30ff267b6b'
    TTY = {'canonical': False, 'echo': False, 'signals': False}

    @classmethod
    def setUpClass(cls):
        import gzip
        import hashlib
        data = Path(cls.FIXTURE).read_bytes()
        assert hashlib.sha256(data).hexdigest() == cls.FIXTURE_SHA256
        cls.retained = json.loads(gzip.decompress(data))
        assert cls.retained['origins']['report_sha256'] == '1dcd24f6561568feabf83c5fc3c6cdfada4d30afcf3d56a9e23a5e2709678dd4'
        assert cls.retained['origins']['bench_sha256'] == 'e03e1d2bfce45d7123b67a1870e8e3e38c7d5a6ae92c99881c513f4965846c01'
        old = cls.retained['old_predicate_source']
        assert hashlib.sha256(old.encode()).hexdigest() == cls.retained['old_predicate_sha256']
        namespace = {'re': bench.re}
        exec(compile(old, '<hash-bound-A1-retry-dense-predicate>', 'exec'), namespace)
        cls.old_predicate = staticmethod(namespace['dense_table_geometry'])
        cls.raw = {}
        for key, role in cls.retained['roles'].items():
            cls.raw[key] = bytes.fromhex(role['stdout_hex'])
            assert hashlib.sha256(cls.raw[key]).hexdigest() == role['stdout_sha256']
        assert len(cls.raw) == 8

    def replay(self, key, end=None):
        role = self.retained['roles'][key]
        terminal = bench.p7.Terminal(*role['size'], role['theme'])
        terminal.feed(self.raw[key] if end is None else self.raw[key][:end])
        return terminal

    def test_actual_frames_reconstruct_and_kill_compiled_old_predicate(self):
        for key, role in self.retained['roles'].items():
            with self.subTest(role=key):
                setup, final = self.replay(key, role['t_offset']).frame(), self.replay(key).frame()
                self.assertEqual(bench.p7.digest(setup['cells']), role['setup_cells_sha256'])
                self.assertEqual(bench.p7.digest(final['cells']), role['final_cells_sha256'])
                lines = bench.frame_text(final).splitlines()
                self.assertEqual((lines[32].rfind(' rows') + 5, lines[35][0], lines[35][-1]), (117, '└', '┘'))
                wait = role['table_wait']
                self.assertTrue(bench.dense_table_geometry(final, wait))
                self.assertFalse(self.old_predicate(final, wait))
                self.assertFalse(bench.dense_table_geometry(setup, wait))

    def test_every_possible_read_boundary_after_toggle_admits_only_the_complete_frame(self):
        # Actual post-toggle read boundaries are not retained (functional captures do not record
        # chunks and table-view never qualified). Every code-point-complete prefix covers every
        # screen state a read could expose: a split code point or escape is held by the decoder.
        for key in ('promql-dense-1-120x40-dark/reference', 'promql-dense-1-120x40-dark/candidate'):
            role = self.retained['roles'][key]
            with self.subTest(role=key):
                raw, wait = self.raw[key], role['table_wait']
                final = self.replay(key).frame()
                terminal = self.replay(key, role['t_offset'])
                fed, first, transients = role['t_offset'], None, 0
                for end in range(role['t_offset'] + 1, len(raw) + 1):
                    if end < len(raw) and 0x80 <= raw[end] <= 0xBF:
                        continue
                    terminal.feed(raw[fed:end])
                    fed = end
                    try:
                        useful = bench.p7.useful(terminal, wait, [], self.TTY)
                    except IndexError:
                        # pyte wide-char stub transient (chart title glyph being overwritten):
                        # undecodable display state counts as not ready; the parser is unchanged.
                        transients += 1
                        useful = False
                    frame = terminal.frame() if useful else None
                    self.assertFalse(useful and self.old_predicate(frame, wait))
                    ready = bool(useful and bench.dense_table_geometry(frame, wait))
                    if first is None and ready:
                        first = end
                        self.assertEqual(frame['cells'], final['cells'])
                    elif first is not None:
                        self.assertTrue(ready)
                self.assertEqual((first, transients), (role['complete_offset'], role['stub_transients']))

    def test_wrong_stale_partial_and_prefix_controls_rejected(self):
        for key, role in self.retained['roles'].items():
            final = self.replay(key).frame()
            setup = self.replay(key, role['t_offset']).frame()
            wait, h, w = role['table_wait'], final['rows'], final['columns']
            footer = wait['contains'][2]
            column = w - 3 - len(footer)
            def copy_row(value, source, target, blank_source=True):
                blank = copy.deepcopy(value['cells'][h - 6])
                value['cells'][target] = copy.deepcopy(value['cells'][source])
                if blank_source:
                    value['cells'][source] = blank
            def put(value, y, x, text):
                for i, character in enumerate(text):
                    value['cells'][y][x + i][0] = character
            controls = {}
            for label, target in (('old-H-6-layout', h - 6), ('footer-H-7', h - 7)):
                bad = copy.deepcopy(final); copy_row(bad, h - 8, target); controls[label] = bad
            for delta in (-1, 1):
                bad = copy.deepcopy(final); put(bad, h - 8, column, ' ' * len(footer))
                put(bad, h - 8, column + delta, footer); controls['footer-column-' + str(delta)] = bad
            bad = copy.deepcopy(final); copy_row(bad, h - 6, h - 5, blank_source=False); controls['partial-no-bottom-border'] = bad
            for y in (h - 7, h - 6):
                bad = copy.deepcopy(final); put(bad, y, 5, 'x'); controls[f'stale-text-{y}'] = bad
            bad = copy.deepcopy(final); put(bad, h - 8, w - 1, ' '); controls['footer-right-border'] = bad
            bad = copy.deepcopy(final); put(bad, h - 8, 0, ' '); controls['footer-left-border'] = bad
            bad = copy.deepcopy(final)
            for y in (h - 4, h - 3):
                bad['cells'][y] = copy.deepcopy(setup['cells'][y])
            controls['stale-chart-hint-bar'] = bad
            bad = copy.deepcopy(final)  # Actual mid-paint: table hints up to col 24, chart remainder after.
            bad['cells'][h - 3][24:] = copy.deepcopy(setup['cells'][h - 3][24:])
            controls['half-painted-hint-row'] = bad
            bad = copy.deepcopy(final); put(bad, h - 8, column - 1, '1'); controls['prefix-11-vs-1'] = bad
            bad = copy.deepcopy(final); bad['cells'].pop(); controls['malformed-shape'] = bad
            controls['stale-setup-chart-frame'] = setup
            for label, bad in controls.items():
                with self.subTest(role=key, control=label):
                    self.assertFalse(bench.dense_table_geometry(bad, wait))
            # The old predicate accepts the counterfeit H-6 layout and the 11-vs-1 alias.
            self.assertTrue(self.old_predicate(controls['old-H-6-layout'], wait))
            alias = copy.deepcopy(controls['old-H-6-layout']); put(alias, h - 6, column - 1, '1')
            self.assertTrue(self.old_predicate(alias, wait))  # Old slice match: '11 | N rows' passes target '1 | N rows'.
            for label, token in (('wrong-cursor', footer.replace('1 |', '2 |', 1)),
                                 ('wrong-total', footer.replace(' rows', '0 rows')),
                                 ('prefixed-target', '1' + footer)):
                with self.subTest(role=key, control=label):
                    self.assertFalse(bench.dense_table_geometry(final, {**wait, 'contains': [*wait['contains'][:2], token]}))


class A1RetryFanoutObservationTests(unittest.TestCase):
    """R2: the startup fan-out gate is wired into observation_issues; p7.compare alone accepts both defects."""
    FIXTURE = bench.ROOT / 'docs/evidence/phase7/a1retry-fanout-observation-pairs.json.gz'
    FIXTURE_SHA256 = '05c0ca626ad8f1adb00f4c583d222f23b03fcd1ca8cd9cdb978e1d96da69e2ce'
    ISSUE = 'initial fan-out is not the exact pre-input request prefix'

    @classmethod
    def setUpClass(cls):
        import gzip
        import hashlib
        data = Path(cls.FIXTURE).read_bytes()
        assert hashlib.sha256(data).hexdigest() == cls.FIXTURE_SHA256
        retained = json.loads(gzip.decompress(data))
        assert retained['origins']['report_sha256'] == '1dcd24f6561568feabf83c5fc3c6cdfada4d30afcf3d56a9e23a5e2709678dd4'
        cls.captures = {}
        for label, pair in retained['pairs'].items():
            results = []
            for role in pair['roles']:
                assert hashlib.sha256(role['session_json'].encode()).hexdigest() == role['session_sha256']
                result = json.loads(role['session_json'])
                for field in ('stdout', 'stderr', 'post_screen_stdout'):  # Same decoding as p7.load_session.
                    value = result.pop(field + '_hex')
                    result[field] = bytes.fromhex(value) if value is not None else None
                result['case'].update(bench.startup_fanout(pair['entry'], has_query=pair['has_query']))
                results.append(result)
            cls.captures[label] = results

    def issue_lists(self, pair):
        with mock.patch.object(bench.p7, 'validate', return_value=[]) as validate:
            actual = [bench.observation_issues(result, result['case']) for result in pair]
        self.assertEqual(validate.call_count, 2)
        return actual

    @staticmethod
    def first_input(result):
        return min(event['dispatch_monotonic_ns'] for event in result['actions']
                   if any(key in event for key in ('input_hex', 'resize', 'signal')))

    def test_late_prefix_receipt_rejected_through_observation_issues(self):
        pair = copy.deepcopy(self.captures['promql-error'])
        self.assertEqual(self.issue_lists(pair), [[], []], 'unmodified retained control must pass')
        for result in pair:
            home = next(i for i, request in enumerate(result['requests']) if request['path'] == '/api/prism/v1/home')
            receipt = next(item for item in result['milestones'] if item['request'] == home)
            receipt['received_monotonic_ns'] = self.first_input(result) + 1_000_000
        self.assertEqual(bench.p7.compare(*pair), [])  # Timestamp-only defect: the pairwise contract agrees.
        self.assertEqual(self.issue_lists(pair), [[self.ISSUE], [self.ISSUE]],
                         'observation_issues must reject the late prefix on both roles')

    def test_identical_extra_preinput_rejected_through_observation_issues(self):
        pair = copy.deepcopy(self.captures['sql-editor'])
        self.assertEqual(self.issue_lists(pair), [[], []], 'unmodified retained control must pass')
        for result in pair:
            home = next(request for request in result['requests'] if request['path'] == '/api/prism/v1/home')
            result['milestones'].append({'request': len(result['requests']),
                                         'received_monotonic_ns': self.first_input(result) - 1})
            result['requests'].append(copy.deepcopy(home))
        self.assertEqual(bench.p7.compare(*pair), [])  # Identical extra effect in both roles: pairwise equal.
        self.assertEqual(self.issue_lists(pair), [[self.ISSUE], [self.ISSUE]],
                         'observation_issues must reject the identical extra effect on both roles')

    def test_receipt_exactly_at_dispatch_is_not_preinput(self):
        for original in self.captures['promql-error']:
            result = copy.deepcopy(original)
            self.assertEqual(bench.startup_fanout_issues(result, result['case']), [])
            result['milestones'][0]['received_monotonic_ns'] = self.first_input(result)
            self.assertEqual(bench.startup_fanout_issues(result, result['case']), [self.ISSUE])

    def test_receipt_order_cannot_select_a_nonprefix_request_array(self):
        for original in self.captures['promql-error']:
            result = copy.deepcopy(original)
            self.assertEqual(bench.startup_fanout_issues(result, result['case']), [])
            first = self.first_input(result)
            # Keep exactly count pre-input receipts, but move one beyond the array prefix.
            result['milestones'][0]['received_monotonic_ns'] = first + 1
            extra = copy.deepcopy(result['requests'][0])
            extra['path'] = '/later-array-entry'
            result['milestones'].append({'request': len(result['requests']), 'received_monotonic_ns': first - 1})
            result['requests'].append(extra)
            initial = bench.causal_requests(result, first - 1)
            self.assertEqual(len(initial), result['case']['initial_fanout_count'])
            self.assertNotEqual(initial, result['requests'][:len(initial)])
            self.assertEqual(bench.startup_fanout_issues(result, result['case']), [self.ISSUE])

    def test_distinct_last_input_dispatch_fails_closed(self):
        for original in self.captures['sql-editor']:
            result = copy.deepcopy(original)
            self.assertEqual(bench.startup_fanout_issues(result, result['case']), [])
            inputs = [event for event in result['actions']
                      if any(key in event for key in ('input_hex', 'resize', 'signal'))]
            self.assertGreater(len(inputs), 1)
            self.assertIsNot(inputs[0], inputs[-1])
            self.assertTrue(bench.finite_number(inputs[0]['dispatch_monotonic_ns']))
            inputs[-1]['dispatch_monotonic_ns'] = float('nan')
            self.assertEqual(bench.startup_fanout_issues(result, result['case']),
                             ['initial fan-out missing first-input dispatch boundary'])

    def test_missing_input_dispatch_fails_closed(self):
        for original in self.captures['promql-error']:
            for position in (0, -1):
                with self.subTest(role=original['name'], input_position=position):
                    result = copy.deepcopy(original)
                    self.assertEqual(bench.startup_fanout_issues(result, result['case']), [])
                    inputs = [event for event in result['actions']
                              if any(key in event for key in ('input_hex', 'resize', 'signal'))]
                    inputs[position].pop('dispatch_monotonic_ns')
                    self.assertEqual(bench.startup_fanout_issues(result, result['case']),
                                     ['initial fan-out missing first-input dispatch boundary'])


class A1PageNavigationTests(unittest.TestCase):
    """Actual Go single-key evidence and the narrow 120x40 sustained construction."""
    FIXTURE = bench.ROOT / 'docs/evidence/phase7/a1-pagenav-go.json.gz'
    FIXTURE_SHA256 = 'd4a025067889e7a2c4ffea4ae6067c971fab0275a7447842fb6abaab86c839df'

    @classmethod
    def setUpClass(cls):
        import gzip
        import hashlib
        data = cls.FIXTURE.read_bytes()
        assert hashlib.sha256(data).hexdigest() == cls.FIXTURE_SHA256
        cls.evidence = json.loads(gzip.decompress(data))
        assert cls.evidence['origins']['report_sha256'] == '010881265fb5a977b80ec47e04ae1a08e8d8c4e849a50d966c16205dda17d3c4'
        assert cls.evidence['origins']['session_sha256'] == '719c1f870183792d7965ae9031cd9ec801e61c7d57f5023baf41c155f792fd0b'
        cls.raw = bytes.fromhex(cls.evidence['stdout_hex'])
        assert hashlib.sha256(cls.raw).hexdigest() == cls.evidence['stdout_sha256']
        assert (cls.evidence['size'], cls.evidence['theme'], cls.evidence['total']) == ([120, 40], 'dark', 256)

    @staticmethod
    def sustained(size=(120, 40), theme='dark', policy='primary'):
        return bench.dense_case({'name': 'promql-dense-1000', 'argv': []}, size, theme,
                                options(observer_policy=policy), sustained=True)

    def test_exact_sustained_targets_bursts_and_intended_series(self):
        for theme in ('dark', 'light'):
            for policy in ('primary', 'resource-attribution'):
                case = self.sustained(theme=theme, policy=policy)
                phases = {p['name']: p for p in case['bench']['phases'] if p['kind'] == 'series-scroll'}
                self.assertEqual(len(phases), 64)
                selected = 0
                for number in range(1, 32):
                    phase = phases[f'series-forward-{number}']
                    expected = ((number * 256 + 14) // 15) * 15
                    self.assertEqual(phase['burst_keys'], 18 if number in (1, 16, 31) else 17)
                    self.assertEqual(phase['selected_row'], expected)
                    self.assertEqual(phase['selected_row'] - selected, phase['burst_keys'] * 15)
                    self.assertEqual(phase['selected_series'], number)
                    selected = expected
                self.assertEqual(phases['series-last']['selected_row'], 255990)
                selected = 255990
                for number in range(998, 967, -1):
                    phase = phases[f'series-reverse-{number}']
                    expected = (((number + 1) * 256 - 1) // 15) * 15
                    self.assertEqual(phase['selected_row'], expected)
                    self.assertEqual(phase['burst_keys'], (selected - expected) // 15)
                    self.assertEqual(phase['selected_series'], number)
                    selected = expected
                self.assertEqual(phases['series-home']['selected_row'], 0)
                self.assertEqual(phases['series-home']['selected_series'], 0)
                for name, row in (('series-forward-1', 270), ('series-forward-2', 525),
                                  ('series-forward-3', 780), ('series-last', 255990),
                                  ('series-reverse-998', 255735)):
                    self.assertEqual(phases[name]['selected_row'], row)
                for name, phase in phases.items():
                    row = phase['selected_row']
                    self.assertEqual(row % 15, 0)
                    self.assertEqual(row // 256, phase['selected_series'])
                    self.assertEqual(phase['wait']['contains'],
                                     ['Table View', f'bench-series-{row // 256:04d}', f'{row + 1} | 256000 rows'])
                    self.assertEqual(phase['effects']['request_exact'], {'/prometheus/api/v1/query_range': 1})
                    action = case['actions'][phase['trigger_action_index']]
                    if 'burst_keys' in phase:
                        key = 'page-down' if name.startswith('series-forward') else 'page-up'
                        self.assertEqual(action['send'], bench.p7.KEYS[key] * phase['burst_keys'])
                    else:
                        self.assertEqual(action['key'], 'end' if name == 'series-last' else 'home')
                self.assertEqual(max(p.get('burst_keys', 0) for p in phases.values()), 18)

    def test_sustained_other_sizes_fail_closed(self):
        original = {'name': 'promql-dense-1', 'argv': []}
        for size in ((80, 40), (119, 40), (121, 40), (120, 39), (120, 41), (240, 100)):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValueError, 'only at 120x40'):
                    bench.dense_case(original, size, 'dark', options(), sustained=True)
        bench.dense_case(original, [120, 40], 'dark', options(), sustained=True)
        # The existing metric-points precondition remains independently enforced.
        with self.assertRaisesRegex(ValueError, 'at least one table page per series'):
            bench.dense_case(original, (120, 40), 'dark', options(metric_points=15), sustained=True)

    def test_actual_seven_checkpoints_and_bench_page_model(self):
        evidence = self.evidence
        observed = []
        self.assertEqual(len(evidence['checkpoints']), 7)
        for checkpoint in evidence['checkpoints']:
            for offset in (checkpoint['first_offset'], checkpoint['end_offset']):
                terminal = bench.p7.Terminal(*evidence['size'], evidence['theme'])
                terminal.feed(self.raw[:offset])
                actual = terminal.frame()
                self.assertEqual(bench.p7.digest(actual), checkpoint['frame_sha256'])
                self.assertEqual(bench.p7.digest(actual['cells']), checkpoint['cells_sha256'])
                line = bench.frame_text(actual).splitlines()[32]
                match = bench.re.search(r'(\d+) \| 256 rows  │$', line)
                self.assertIsNotNone(match)
                self.assertEqual(int(match[1]), checkpoint['cursor'])
                self.assertTrue(bench.dense_table_geometry(actual, {'contains': [f'{match[1]} | 256 rows']}))
            self.assertGreaterEqual(checkpoint['ready_monotonic_ns'] - checkpoint['stable_since_monotonic_ns'], 80_000_000)
            observed.append(checkpoint['cursor'])
        self.assertEqual(observed, [1, 16, 31, 46, 256, 241, 1])
        # Derive the bench step from generated actions/targets, rather than a duplicate constant.
        phase = next(p for p in self.sustained()['bench']['phases'] if p['name'] == 'series-forward-1')
        self.assertEqual(phase['selected_row'] % phase['burst_keys'], 0)
        bench_page = phase['selected_row'] // phase['burst_keys']
        def model(page_size):
            page, sequence = 0, [1]
            last = (evidence['total'] - 1) // page_size
            for checkpoint in evidence['checkpoints'][1:]:
                key = checkpoint['key']
                if key == 'page-down':
                    page = min(page + 1, last)
                elif key == 'page-up':
                    page = max(page - 1, 0)
                elif key == 'end':
                    page = last
                elif key == 'home':
                    page = 0
                else:
                    self.fail(f'unexpected navigation key: {key}')
                sequence.append(page * page_size + 1)
            return sequence
        self.assertEqual(model(bench_page), observed)
        self.assertNotEqual(model(14), observed)
        self.assertNotEqual(model(16), observed)


if __name__ == '__main__':
    unittest.main()
