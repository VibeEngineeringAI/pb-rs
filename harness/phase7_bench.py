#!/usr/bin/env python3
"""Finite, quiet-window-gated interactive collector. plan/reduce never launch children."""
import argparse
import copy
import fnmatch
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import resource
import statistics
import subprocess
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
import fcntl

import phase7 as p7
import phase7_workloads as workloads

LOCK = Path.home() / '.cache/pb-rs-performance.lock'
ROOT = Path(__file__).resolve().parent.parent
STABLE_MS = 80
RESIZE_SIGNAL_POLICY = 'ioctl-only-changed'
MAX_READINESS_EVALUATIONS = 250000
APPROVAL_LAUNCH_ALLOWANCE_SECONDS = 60
SCHEMA = 1
MAX_APPROVAL_SECONDS = 3600
COMPANION_EARLY_SECONDS = 60
POOL_COUNTS = ((4, 0), (1, 3), (0, 4), (0, 4), (0, 4), (0, 4), (0, 4), (0, 4), (0, 3))
PROVENANCE_COMMIT = '07af390ab09bda8ac9e427ffc6310077bace0125'
OBSERVER_POLICIES = {
    'primary': {'record_termios': False, 'record_chunks': False, 'record_descendants': False},
    'resource-attribution': {'record_termios': False, 'record_chunks': False, 'record_descendants': True},
    'floor-full': {'record_termios': True, 'record_chunks': True, 'record_descendants': True},
    'floor-minimal': {'record_termios': False, 'record_chunks': False, 'record_descendants': False},
}


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def write_report(path, value):
    """Public manifests use stable path placeholders; raw captures retain exact paths."""
    write_json(path, public_value(value))


def private_prefixes():
    # Keep existing public placeholders while resolving an overridden scratch
    # root symmetrically. normalize replaces the longest prefix first.
    return {'$PB_CACHE/tmp': str(p7.TMP),
            '$PB_CACHE': str(Path.home() / '.cache/pb-phase7'),
            '$WORKTREE': str(ROOT), '$HOME': str(Path.home())}


def public_value(value):
    return p7.normalize(value, {path: token for token, path in private_prefixes().items()})


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def json_snapshot(path, *, maximum=16 * 1024 * 1024):
    """Interpret and digest exactly one read from the selected canonical file."""
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('missing/oversized JSON evidence')
    encoded = path.read_bytes()
    if len(encoded) > maximum:
        raise ValueError('JSON evidence exceeds byte cap')
    return json.loads(encoded), {'path': str(path), 'size_bytes': len(encoded),
                                'sha256': hashlib.sha256(encoded).hexdigest()}


def check_snapshot(identity):
    path = Path(identity['path'])
    if str(path.resolve(strict=True)) != identity['path'] or path.stat().st_size != identity['size_bytes'] or sha_file(path) != identity['sha256']:
        raise ValueError('parsed JSON evidence identity/path changed')


@dataclass(frozen=True)
class Options:
    sessions: int = 3                 # Independent paired measured rounds per variant.
    warmups: int = 1                  # Declared independent paired warmups, also retained.
    themes: tuple = ('dark', 'light')
    sizes: tuple = ((120, 40),)
    case_filters: tuple = ()
    seed: int = 7
    max_sessions: int = 256           # Includes both binary roles and observer controls.
    budget_seconds: float = 600
    session_timeout: float = 30
    idle_seconds: float = 2
    long_idle_seconds: float = 60
    include_long_idle: bool = False
    metric_points: int = 256
    network_delay_ms: int = 150
    floor_sessions: int = 3           # Paired primary/minimal controls per profile.
    floor_frames: int = 20
    bootstrap_draws: int = 2000
    campaign_id: str = ''
    preflight_report: str = ''
    observer_policy: str = 'primary'
    comparison: str = 'go-rust'
    pool_id: str = ''
    pool_chunk: int = 0
    decoder_report: str = ''
    pool_spec: str = ''

    def validate(self):
        if self.comparison not in ('go-rust', 'aa-go', 'aa-rust'):
            raise ValueError('unknown comparison class')
        if self.pool_id:
            if self.comparison != 'go-rust' or not 1 <= self.pool_chunk <= len(POOL_COUNTS) or (self.warmups, self.sessions) != POOL_COUNTS[self.pool_chunk - 1] :
                raise ValueError('pool chunk requires predeclared index/counts/seed and go-rust')
        elif self.pool_chunk:
            raise ValueError('pool chunk requires pool_id')
        if self.observer_policy not in ('primary', 'resource-attribution'):
            raise ValueError('unknown primary/resource attribution observer policy')
        if not ((0 if self.pool_id else 1) <= self.sessions <= 100 and 0 <= self.warmups <= 20):
            raise ValueError('sessions/warmups must be finite (1..100 / 0..20)')
        if not (1 <= self.max_sessions <= 10000 and 0 < self.budget_seconds <= 86400):
            raise ValueError('finite positive campaign caps are required')
        if not (1 <= self.session_timeout <= 3600 and 1 <= self.idle_seconds <= 600):
            raise ValueError('session timeout/idle duration outside finite bounds')
        if not (1 <= self.long_idle_seconds <= 1800 and 1 <= self.metric_points <= 4096):
            raise ValueError('long idle/metric points outside finite bounds')
        if not (1 <= self.floor_sessions <= 100 and 1 <= self.floor_frames <= 1000):
            raise ValueError('observer controls must contain finite positive sessions/frames')
        if not 40 <= self.bootstrap_draws <= 100000 or not 1 <= self.network_delay_ms <= 10000:
            raise ValueError('invalid bootstrap draws/network delay')
        if not self.themes or any(t not in ('dark', 'light') for t in self.themes):
            raise ValueError('themes must be dark/light')
        if not self.sizes or any(not (80 <= w <= 240 and 30 <= h <= 100) for w, h in self.sizes):
            raise ValueError('measurement sizes must be 80..240 columns and 30..100 rows')
        if len(set(self.sizes)) != len(self.sizes) or len(set(self.themes)) != len(self.themes):
            raise ValueError('duplicate measurement profiles')


class Phases:
    def __init__(self):
        self.actions, self.definitions = [], []

    def checkpoint(self, name, kind, wait, trigger=None, **details):
        wait = copy.deepcopy(wait)
        effects = {key: wait.pop(key) for key in ('request_paths', 'request_exact', 'sql_window_counts', 'sql_window_exact') if key in wait}
        self.actions.append({'checkpoint': name, 'wait': wait, 'stable_ms': STABLE_MS})
        self.definitions.append({'name': name, 'kind': kind, 'wait': copy.deepcopy(wait), 'effects': effects,
                                 'trigger_action_index': trigger, **details})

    def change(self, name, kind, event, wait, **details):
        if 'signal' in event or sum(key in event for key in ('send', 'key', 'resize')) != 1:
            raise ValueError('measured action supports one key/send or resize, not signals')
        trigger = len(self.actions)
        if 'resize' in event:
            event = {**event, 'wait': {'snapshot_resize': event['resize']}}
            wait = {**wait, 'resize_rerender': True}
        self.actions.append({**event, 'stable_ms': 0})
        self.checkpoint(name, kind, {'output_after_action': True, **wait}, trigger, **details)

    def idle(self, wait, seconds, *, long_idle=False):
        self.checkpoint('idle-start', 'anchor', wait)
        self.checkpoint('idle-end', 'idle', {**wait, 'min_elapsed_ms': seconds * 1000},
                        start_checkpoint='idle-start', duration_ms=seconds * 1000, long_idle=long_idle)

    def quit(self):
        self.actions.append({'key': 'ctrl-c', 'stable_ms': 0})


def target(row, total):
    return {'contains': [f'rows {row + 1}/{total}', f'bench-row-{row:06d}'],
            'absent': ['fetch failed', 'query failed', 'fetching...'], 'alternate': True, 'cooked': False,
            'request_paths': ['/api/v1/query']}


def sql_fixture(rows, delay_ms=0):
    """Explicit route slices; no generic 20k response masquerades as a 500-row window."""
    fixture = workloads.base_fixture()
    fixture['routes'] = [r for r in fixture['routes'] if r['path'] != '/api/v1/query']
    for route in fixture['routes']:
        if route['path'].endswith('/schema'):
            route['body'] = json.dumps({'fields': [{'name': name, 'data_type': 'Int64' if name == 'sequence' else 'Utf8', 'nullable': False}
                                                   for name in ('sequence', 'message')]})
    for offset in range(0, rows, 500):
        count = min(500, rows - offset)
        data = {'fields': ['sequence', 'message'], 'records': [
            {'sequence': i, 'message': f'bench-row-{i:06d}'} for i in range(offset, offset + count)]}
        # Closing quote prevents OFFSET 500 matching OFFSET 5000. It does not
        # depend on whitespace between JSON members or around the member colon.
        fixture['routes'].append(workloads.route('POST', '/api/v1/query', data,
            body_contains=f'LIMIT {count} OFFSET {offset}"', delay_ms=delay_ms))
    return fixture


def sql_case(original, size, theme, options, *, idle=None, delay_ms=0, sustained=False):
    total = int(original['name'].rsplit('-', 1)[1])
    page_size = max(1, max(3, max(6, size[1] - 12 - 3) - 3) - 5)
    phases = Phases()
    phases.checkpoint('startup', 'startup', target(0, total))
    phases.change('row-down', 'scroll', {'key': 'down'}, target(1, total), selected_row=1)
    selected = 1 + page_size
    phases.change('page-down', 'scroll', {'key': 'page-down'}, target(selected, total), selected_row=selected)
    last_window = math.ceil(total / 500) if sustained else min(4, math.ceil(total / 500))
    for window in range(1, last_window):
        pages = math.ceil((window * 500 - selected) / page_size)
        selected = min(total - 1, selected + pages * page_size)
        wait = target(selected, total)
        wait['sql_window_counts'] = {str(window * 500): 1}
        phases.change(f'window-{window}', 'sustained-scroll' if sustained else 'window-network',
                      {'send': p7.KEYS['page-down'] * pages}, wait, selected_row=selected,
                      burst_keys=pages, offset=window * 500)
    if sustained:
        for window in range(last_window - 2, 0, -1):
            pages = math.ceil((selected - (window * 500 + 250)) / page_size)
            selected = max(0, selected - pages * page_size)
            wait = target(selected, total)
            # The last three windows can be cached; older ones must refetch.
            wait['sql_window_counts'] = {str(window * 500): 2 if window < last_window - 3 else 1}
            phases.change(f'reverse-window-{window}', 'sustained-scroll',
                          {'send': p7.KEYS['page-up'] * pages}, wait,
                          selected_row=selected, burst_keys=pages, offset=window * 500)
    turnover = total > 1500
    wait = target(0, total)
    wait['sql_window_counts'] = {'0': 2 if turnover else 1}
    if not turnover:
        wait['sql_window_exact'] = {'0': 1}
    phases.change('return-first', 'cache-turnover' if turnover else 'cache-hit',
                  {'key': 'home'}, wait, selected_row=0)
    resized = [size[0] + 20, size[1] + 5]
    phases.change('resize-large', 'resize', {'resize': resized},
                  {**target(0, total), 'dimensions': resized, 'right_edge_corner': True, 'sql_resize_geometry': True})
    phases.change('resize-original', 'resize', {'resize': list(size)},
                  {**target(0, total), 'dimensions': list(size), 'right_edge_corner': True, 'sql_resize_geometry': True})
    phases.idle(target(0, total), options.idle_seconds if idle is None else idle, long_idle=idle is not None)
    post_idle_hold_ms = 250 if idle is not None and options.observer_policy == 'resource-attribution' else 0
    if post_idle_hold_ms:
        phases.checkpoint('post-idle-bracket-hold', 'anchor',
                          {**target(0, total), 'min_elapsed_ms': post_idle_hold_ms - STABLE_MS},
                          start_checkpoint='idle-end', duration_ms=post_idle_hold_ms,
                          scope='unmeasured post-idle bracket opportunity; outside the idle interval')
    phases.quit()
    label = original['name'] + ('-sustained' if sustained else '') + ('-delayed' if delay_ms else '') + ('-long-idle' if idle else '')
    return {'name': f'{label}-{size[0]}x{size[1]}-{theme}',
            'argv': ['sql', 'run', f'SELECT * FROM logs LIMIT {total}', '-i',
                     '--from', workloads.START, '--to', workloads.END],
            'size': list(size), 'theme': theme, 'config': {'auth': 'basic'},
            'fixture': sql_fixture(total, delay_ms), 'actions': phases.actions,
            'timeout': options.session_timeout + (idle or options.idle_seconds) + (post_idle_hold_ms / 1000 if post_idle_hold_ms else 0),
            'expect': {'termination': {'exit': 0}, 'config_unchanged': True, 'stderr_exact': ''},
            'initial_fanout_count': 3,
            'initial_fanout_members': ['GET /api/prism/v1/home', 'GET /api/v1/logstream/logs/schema',
                                      'POST /api/v1/query'],
            'initial_fanout_constraints': [['GET /api/prism/v1/home', 'GET /api/v1/logstream/logs/schema']],
            'bench': {'phases': phases.definitions, 'kind': 'sql-windowed', 'total_rows': total,
                      'window_rows': 500, 'cache_turnover': turnover,
                      'sustained_scrolling': sustained, 'forward_windows': last_window,
                      'source_definition_sha256': p7.digest(original), 'delay_ms': delay_ms,
                      'label': 'real 500-row windows; three-window cache' if turnover else 'cached paging'}}


def startup_fanout(entry, *, has_query):
    """Accepted corpus graph: default SQL, or explicit metrics-dataset PromQL."""
    members = ['GET /api/prism/v1/home']
    if entry == 'sql':
        schema = 'GET /api/v1/logstream/logs/schema'
        members.append(schema)
        if has_query:
            members.append('POST /api/v1/query')
        return {'initial_fanout_count': len(members), 'initial_fanout_members': members,
                'initial_fanout_constraints': [[members[0], schema]]}
    if entry != 'promql':
        raise ValueError('uncharacterized startup fan-out')
    members.append('GET /prometheus/api/v1/label/__name__/values')
    if has_query:
        members.append('GET /prometheus/api/v1/query_range')
    return {'initial_fanout_count': len(members), 'initial_fanout_members': members}


def dense_case(original, size, theme, options, *, sustained=False):
    series = int(original['name'].rsplit('-', 1)[1])
    fixture = workloads.base_fixture(series=series)
    for route in fixture['routes']:
        if route['path'] == '/prometheus/api/v1/query_range':
            data = json.loads(route['body'])
            for i, row in enumerate(data['data']['result']):
                row['metric']['job'] = f'bench-series-{i:04d}'
                row['values'] = [[1704067200 + j, str(i + j)] for j in range(options.metric_points)]
            route['body'] = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    phases = Phases()
    wait = {'contains': ['Chart View', f'bench-series-{series - 1:04d}'], 'absent': ['fetching...', 'Table View'], 'alternate': True, 'cooked': False,
            'request_paths': ['/prometheus/api/v1/query_range']}
    phases.checkpoint('startup', 'startup', wait)
    # Deterministic unmeasured setup: ctrl-q focuses editor; shift-tab focuses results.
    phases.actions.extend([{'key': 'ctrl-q', 'stable_ms': 0}, {'key': 'shift-tab', 'stable_ms': 0}])
    phases.checkpoint('setup-ready', 'anchor', {**wait, 'output_after_action': True},
                      trigger=len(phases.actions) - 1)
    phases.change('table-view', 'chart-table', {'send': 't'},
                  {'contains': ['Table View', 'bench-series-0000', f'1 | {series * options.metric_points} rows'],
                   'dense_table_geometry': True, 'absent': ['fetching...'], 'alternate': True, 'cooked': False})
    if sustained:
        if tuple(size) != (120, 40):
            raise ValueError('sustained series scrolling is established only at 120x40')
        if options.metric_points < max(1, size[1] - 24):
            raise ValueError('sustained series scrolling requires at least one table page per series')
        total = series * options.metric_points
        # Actual Go dark dense-1 single-key control at 120x40: step 15.
        # Report 010881265fb5a977b80ec47e04ae1a08e8d8c4e849a50d966c16205dda17d3c4;
        # docs/phase7-review-pagenav-composition-opus-r1.md scopes the source extrapolation.
        page = 15
        def selection(row):
            return {'contains': ['Table View', f'bench-series-{row // options.metric_points:04d}', f'{row + 1} | {total} rows'],
                    'dense_table_geometry': True,
                    'absent': ['fetching...'], 'alternate': True, 'cooked': False,
                    'request_exact': {'/prometheus/api/v1/query_range': 1}}
        selected = 0
        for number in range(1, min(series, 32)):
            pages = math.ceil((number * options.metric_points - selected) / page)
            selected += pages * page
            phases.change(f'series-forward-{number}', 'series-scroll', {'send': p7.KEYS['page-down'] * pages}, selection(selected),
                          selected_row=selected, selected_series=selected // options.metric_points, burst_keys=pages)
        selected = ((total - 1) // page) * page
        phases.change('series-last', 'series-scroll', {'key': 'end'}, selection(selected), selected_row=selected, selected_series=series - 1)
        for number in range(series - 2, max(series - 33, 30), -1):
            pages = math.ceil((selected - ((number + 1) * options.metric_points - 1)) / page)
            selected -= pages * page
            phases.change(f'series-reverse-{number}', 'series-scroll', {'send': p7.KEYS['page-up'] * pages}, selection(selected),
                          selected_row=selected, selected_series=selected // options.metric_points, burst_keys=pages)
        phases.change('series-home', 'series-scroll', {'key': 'home'}, selection(0), selected_row=0, selected_series=0)
    phases.change('chart-view', 'chart-table', {'send': 't'}, wait)
    phases.idle(wait, options.idle_seconds)
    phases.quit()
    return {'name': f'{original["name"]}' + ('-sustained' if sustained else '') + f'-{size[0]}x{size[1]}-{theme}', 'argv': copy.deepcopy(original['argv']),
            'size': list(size), 'theme': theme, 'config': {'auth': 'basic'}, 'fixture': fixture,
            'actions': phases.actions, 'timeout': options.session_timeout + options.idle_seconds,
            'expect': {'termination': {'exit': 0}, 'config_unchanged': True, 'stderr_exact': ''},
            **startup_fanout('promql', has_query=True),
            'bench': {'kind': 'dense-metrics', 'phases': phases.definitions, 'series': series,
                      'points_per_series': options.metric_points, 'sustained_series_scrolling': sustained, 'source_definition_sha256': p7.digest(original)}}


def editor_case(size, theme, options):
    phases = Phases()
    phases.checkpoint('startup', 'startup', {'contains': ['EDITOR', 'RESULTS'], 'alternate': True, 'cooked': False})
    phases.change('edit-select', 'editor', {'send': 'SELECT 1'},
                  {'contains': ['SELECT 1'], 'alternate': True, 'cooked': False})
    phases.change('edit-unicode', 'editor', {'send': '日志'},
                  {'contains': ['SELECT 1日志'], 'alternate': True, 'cooked': False})
    phases.change('edit-backspace', 'editor', {'key': 'backspace'},
                  {'contains': ['SELECT 1日'], 'absent': ['SELECT 1日志'], 'alternate': True, 'cooked': False})
    phases.quit()
    return {'name': f'sql-editor-{size[0]}x{size[1]}-{theme}',
            'argv': ['sql', 'run', '-i', '--from', workloads.START, '--to', workloads.END],
            'size': list(size), 'theme': theme, 'config': {'auth': 'basic'},
            'fixture': workloads.base_fixture(), 'actions': phases.actions, 'timeout': options.session_timeout,
            'expect': {'termination': {'exit': 0}, 'config_unchanged': True, 'stderr_exact': ''},
            **startup_fanout('sql', has_query=False),
            'bench': {'kind': 'editor', 'phases': phases.definitions}}


def error_case(entry, size, theme, options):
    """Bounded 503 presentation/quit, derived from the existing error corpus."""
    fixture = workloads.base_fixture()
    endpoint = '/api/v1/query' if entry == 'sql' else '/prometheus/api/v1/query_range'
    for route in fixture['routes']:
        if route['path'] == endpoint:
            route.update(status=503, body='fixture query failed')
    phases = Phases()
    phases.checkpoint('startup', 'error', {
        'contains': ['fixture query failed'], 'absent': ['fetching...'],
        'alternate': True, 'cooked': False, 'request_paths': [endpoint], 'request_exact': {endpoint: 1}})
    phases.idle({'contains': ['fixture query failed'], 'absent': ['fetching...'],
                 'alternate': True, 'cooked': False}, options.idle_seconds)
    phases.quit()
    argv = (['sql', 'run', 'SELECT * FROM logs LIMIT 500', '-i'] if entry == 'sql' else
            ['promql', 'run', 'fixture_metric', '-i', '--dataset', 'metrics'])
    return {'name': f'{entry}-query-error-{size[0]}x{size[1]}-{theme}',
            'argv': [*argv, '--from', workloads.START, '--to', workloads.END],
            'size': list(size), 'theme': theme, 'config': {'auth': 'basic'}, 'fixture': fixture,
            'actions': phases.actions, 'timeout': options.session_timeout + options.idle_seconds,
            'expect': {'termination': {'exit': 0}, 'config_unchanged': True, 'stderr_exact': ''},
            **startup_fanout(entry, has_query=True),
            'bench': {'kind': 'error', 'phases': phases.definitions, 'endpoint': endpoint,
                      'error_status': 503, 'error_body': 'fixture query failed', 'retry_count': 0}}


def campaign_cases(options):
    options.validate()
    definitions = workloads.performance_workloads()
    cases = []
    for size in options.sizes:
        for theme in options.themes:
            for original in definitions:
                if original['name'].startswith('sql-scroll-'):
                    cases.append(sql_case(original, size, theme, options))
                elif original['name'].startswith('promql-dense-'):
                    cases.append(dense_case(original, size, theme, options))
                else:
                    raise ValueError('unrecognized performance definition: ' + original['name'])
            cases.append(editor_case(size, theme, options))
            largest = next(c for c in definitions if c['name'] == 'sql-scroll-20000')
            cases.append(sql_case(largest, size, theme, options, delay_ms=options.network_delay_ms))
            cases.append(sql_case(largest, size, theme, options, sustained=True))
            dense = next(c for c in definitions if c['name'] == 'promql-dense-1000')
            cases.append(dense_case(dense, size, theme, options, sustained=True))
            cases.extend(error_case(entry, size, theme, options) for entry in ('sql', 'promql'))
            if options.include_long_idle:
                cases.append(sql_case(largest, size, theme, options, idle=options.long_idle_seconds))
    for case in cases:
        case['resize_signal_policy'] = RESIZE_SIGNAL_POLICY
        case['bench']['observer_policy'] = options.observer_policy
        case['bench']['comparison'] = options.comparison
        if options.comparison != 'go-rust':
            case['name'] += '-' + options.comparison
        case['bench']['latency_origin'] = 'dispatch_monotonic_ns immediately before write/ioctl/signal; excludes observer evaluation/frame work'
        case['bench']['dispatch_source_sha256'] = sha_file(ROOT / 'harness/phase7.py')
        if options.observer_policy == 'resource-attribution':
            case['name'] += '-resource-attribution'
    selected = [c for c in cases if not options.case_filters or
                any(fnmatch.fnmatchcase(c['name'], pattern) for pattern in options.case_filters)]
    if not selected:
        raise ValueError('no cases match filters')
    if len({c['name'] for c in selected}) != len(selected):
        raise ValueError('duplicate campaign names')
    return selected


def schedule(cases, options):
    rng, pairs = random.Random(options.seed), []
    order = list(cases)
    rng.shuffle(order)
    for case in order:
        for purpose, count in (('warmup', options.warmups), ('measured', options.sessions)):
            for round_number in range(count):
                roles = ['reference', 'candidate']
                rng.shuffle(roles)
                pairs.append({'case': case['name'], 'purpose': purpose, 'round': round_number, 'order': roles})
    return pairs


def sql_windows(trace):
    windows = []
    for request in trace:
        if request.get('method') == 'POST' and request.get('path', '').split('?')[0] == '/api/v1/query':
            body = request.get('body', '')
            try:
                query = (json.loads(body) if isinstance(body, str) else body)['query']
            except (ValueError, KeyError, TypeError):
                windows.append(None)
                continue
            match = re.fullmatch(r'SELECT \* FROM logs LIMIT (\d+) OFFSET (\d+)', query)
            windows.append(tuple(map(int, match.groups())) if match else None)
    return windows


def causal_requests(result, before_ns):
    received = {m['request']: m['received_monotonic_ns'] for m in result['milestones']}
    if any(i not in received for i in range(len(result['requests']))):
        raise ValueError('request missing causal fixture milestone')
    return [r for i, r in enumerate(result['requests']) if received[i] <= before_ns]


class Readiness:
    """Additional predicates at the existing observer boundary, no private terminal."""
    def __init__(self, case=None):
        self.current_action, self.baseline_bytes, self.last_bytes = None, 0, 0
        self.resize_snapshot = None
        self.offsets = {}
        self.recorded_evaluations = 0
        excluded = {phase['name'] for phase in (case or {}).get('bench', {}).get('phases', []) if phase['kind'] in ('idle', 'anchor')}
        # Frozen execute uses this exact case.actions wait object; equal waits
        # at different checkpoints must retain their independent classification.
        self.nonlatency_waits = {id(action['wait']) for action in (case or {}).get('actions', [])
                                 if action.get('checkpoint') in excluded and 'wait' in action}

    def __call__(self, terminal, wait, trace, tty, action_started_ns, now_ns):
        entry = self.offsets.setdefault(action_started_ns, {})
        entry.update(evaluated_monotonic_ns=now_ns, byte_offset=raw_observed_offset(terminal))
        if action_started_ns != self.current_action:
            self.current_action, self.baseline_bytes = action_started_ns, self.last_bytes
            entry.update(action_started_monotonic_ns=action_started_ns, baseline_bytes=self.baseline_bytes,
                         wait=copy.deepcopy(wait), evaluations=[], evaluation_count=0)
            entry['history_scope'] = ('omitted non-latency checkpoint' if id(wait) in self.nonlatency_waits else 'latency through first match')
        self.last_bytes = terminal.byte_offset
        evaluation = None
        if 'first_match_monotonic_ns' not in entry and entry['history_scope'] == 'latency through first match':
            entry['evaluation_count'] += 1
            if self.recorded_evaluations < MAX_READINESS_EVALUATIONS:
                evaluation = {'evaluated_monotonic_ns': now_ns, 'byte_offset': raw_observed_offset(terminal),
                              'tty': copy.deepcopy(tty), 'qualified': False}
                if wait.get('requests') or wait.get('request_paths'):
                    evaluation['trace'] = copy.deepcopy(trace)
                entry['evaluations'].append(evaluation)
                self.recorded_evaluations += 1
            else:
                entry['recording_error'] = 'bounded readiness evaluation history exhausted; diagnostic invalid'
        qualified = self._qualify(terminal, wait, trace, tty, action_started_ns, now_ns)
        if evaluation is not None:
            evaluation['qualified'] = qualified
        if qualified:
            entry.setdefault('first_match_monotonic_ns', now_ns)
            entry.setdefault('first_match_byte_offset', raw_observed_offset(terminal))
        return qualified

    def _qualify(self, terminal, wait, trace, tty, action_started_ns, now_ns):
        if wait.get('snapshot_resize'):
            resized = copy.copy(terminal)
            resized.main, resized.alt = copy.deepcopy(terminal.main), copy.deepcopy(terminal.alt)
            resized.active = resized.alt if terminal.alternate else resized.main
            resized.resize(*wait['snapshot_resize'])
            self.resize_snapshot = resized.frame()
        if not p7.useful(terminal, wait, trace, tty):
            return False
        if now_ns - action_started_ns < wait.get('min_elapsed_ms', 0) * 1e6:
            return False
        if wait.get('output_after_action') and terminal.byte_offset <= self.baseline_bytes:
            return False
        frame = terminal.frame()
        if wait.get('resize_rerender') and (self.resize_snapshot is None or frame['cells'] == self.resize_snapshot['cells']):
            return False
        if 'dimensions' in wait and [frame['columns'], frame['rows']] != wait['dimensions']:
            return False
        if wait.get('sql_resize_geometry') and not sql_resize_geometry(frame, wait):
            return False
        if wait.get('dense_table_geometry') and not dense_table_geometry(frame, wait):
            return False
        if wait.get('right_edge_corner') and not any(row[-1][0] in {'┐', '┘', '╮', '╯'} for row in frame['cells'] if row):
            return False
        if not all(re.search(pattern, terminal.text()) for pattern in wait.get('regex', [])):
            return False
        return True  # Fixture effects are checked causally after capture, never polled here.



def sql_resize_geometry(frame, wait):
    """Default open drawer: resultsW=max(30,W-sidebarW-1), footer H-6."""
    width, height = frame['columns'], frame['rows']
    # query.go QueryModel.View / sql.rs view: the unchanged script never
    # toggles the default-open columns drawer (ctrl+l).
    sidebar_width = 34 + (3 if width >= 140 else 0)
    results_width = max(30, width - sidebar_width - 1)
    footer = next((token for token in wait.get('contains', []) if token.startswith('rows ')), None)
    marker = next((token for token in wait.get('contains', []) if token.startswith('bench-row-')), None)
    if (footer is None or marker is None or height < 30 or results_width + 1 >= width or
            len(frame['cells']) != height or any(len(row) != width for row in frame['cells'])):
        return False
    lines = [''.join(cell[0] for cell in row) for row in frame['cells']]
    end = results_width - 3
    column = end - len(footer)
    def pane_edges(row):
        return (row[0][0] == row[results_width - 1][0] == row[results_width + 1][0] == row[-1][0] == '│'
                and row[results_width][0] == ' ')
    return (column >= 0 and lines[height - 6][column:end] == footer and
            pane_edges(frame['cells'][height - 6]) and
            marker in lines[16][1:results_width - 1] and pane_edges(frame['cells'][16]))


def dense_table_geometry(frame, wait):
    """PromQL table, 120-col bottom bar of 4 rows (promql.go View/renderResultsTable; promql.rs mirrors).

    Pane bottom border H-5. Body row H-6 is blank because the pane pads to
    availH-2 rows while content is resultsInnerH=availH-3. Row H-7 is blank
    because an empty detail still has lipgloss.Height 1. The custom footer is
    row H-8 and ends exactly at W-3. The complete table-focus hint row H-3 is
    the last row repainted after the toggle, so a stale or half-painted bar fails.
    """
    width, height = frame['columns'], frame['rows']
    footer = next((t for t in wait.get('contains', []) if re.fullmatch(r'\d+ \| \d+ rows', t)), None)
    if (footer is None or height < 30 or len(frame['cells']) != height or
            any(len(row) != width for row in frame['cells'])):
        return False
    lines = [''.join(cell[0] for cell in row) for row in frame['cells']]
    blank, column, row = '│' + ' ' * (width - 2) + '│', width - 3 - len(footer), lines[height - 8]
    return (column >= 1 and row[0] == '│' and row[column - 1] == ' ' and row[column:] == footer + '  │' and
            lines[height - 7] == lines[height - 6] == blank and
            lines[height - 5] == '└' + '─' * (width - 2) + '┘' and
            re.fullmatch(r'  <t> Toggle chart/table +<ctrl-r> Run +<ctrl-c> Quit +', lines[height - 3]) is not None)



def raw_observed_offset(terminal):
    # Frozen Terminal.feed increments consumed token bytes. Its unconsumed
    # decoded escape/text buffer and undecoded UTF8 bytes complete raw length.
    # Source-bound formula, with regression on simultaneous partial CSI/UTF8.
    pending = terminal.pending
    if not isinstance(pending, str):
        # Unit mock terminals use only the historical counter.
        return terminal.byte_offset
    buffered, _flag = terminal.decoder.getstate()
    return terminal.byte_offset + len(pending.encode(errors='surrogateescape')) + len(buffered)


def bind_decoder_offsets(result, readiness):
    """Narrow owned observations at the last accepted predicate, no optional chunks."""
    events = []
    by_evaluation = {value['evaluated_monotonic_ns']: value for value in readiness.offsets.values()}
    for event in result.get('actions', []):
        record = by_evaluation.get(event.get('evaluated_monotonic_ns'))
        if record is not None:
            events.append({'index': event['index'], 'byte_offset': record['byte_offset'],
                           'evaluated_monotonic_ns': record['evaluated_monotonic_ns']})
    frames = []
    for stamp in result.get('frames', []):
        record = readiness.offsets.get(stamp.get('action_started_monotonic_ns'))
        if record is not None and record['evaluated_monotonic_ns'] == stamp.get('ready_monotonic_ns'):
            frames.append({'name': stamp['name'], 'end_offset': record['byte_offset'],
                           'first_offset': record.get('first_match_byte_offset'),
                           'first_monotonic_ns': record.get('first_match_monotonic_ns'),
                           **{key: copy.deepcopy(record.get(key)) for key in ('action_started_monotonic_ns',
                               'baseline_bytes', 'wait', 'evaluation_count', 'evaluations', 'recording_error')}})
    result['decoder_offsets'] = {'schema_version': 2, 'actions': events, 'frames': frames,
        'scope': 'absolute decoded stdout offsets at admitted evaluation; prefix untimed, first-match phase bytes and accepted-frame tail separate'}


def comparison_artifacts(reference, candidate, reference_meta, candidate_meta, comparison):
    if comparison == 'go-rust':
        return {'reference': artifact(reference, reference_meta, 'reference'),
                'candidate': artifact(candidate, candidate_meta, 'candidate')}
    if comparison not in ('aa-go', 'aa-rust') or not reference_meta or not candidate_meta:
        raise ValueError('A/A requires actual identical artifact/provenance for both roles')
    validator = 'reference' if comparison == 'aa-go' else 'candidate'
    result = {role: artifact(binary, metadata, validator) for role, binary, metadata in
              (('reference', reference, reference_meta), ('candidate', candidate, candidate_meta))}
    if result['reference'] != result['candidate']:
        raise ValueError('A/A requires identical canonical artifact and provenance identities, not same-byte copies or aliases to different artifacts')
    return result


class FunctionalOwnership(AbstractContextManager):
    """Untimed exclusive admission, no approval manufactured and no wait/probe race."""
    def __init__(self, lock_path):
        self.path, self.handle = Path(lock_path), None
    def __enter__(self):
        self.handle = self.path.open('a+')
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.handle.close()
            self.handle = None
            raise
        return self
    def __exit__(self, *_):
        if self.handle:
            self.handle.close()
            self.handle = None


def functional_preflight(reference, candidate, output, options, *, reference_meta=None, candidate_meta=None, lock_path=LOCK):
    # Own the lock before preparation and until all capture/postprocessing/drift
    # checks finish. A timed lane cannot enter between a probe and child spawn.
    with FunctionalOwnership(lock_path):
        report = _functional_preflight(reference, candidate, output, options,
                                      reference_meta=reference_meta, candidate_meta=candidate_meta)
        report['admission'] = {'lock': str(Path(lock_path).resolve()), 'mode': 'nonblocking exclusive full functional scope; unmeasured; no quiet approval'}
        write_report(Path(output) / 'report.json', report)
        return report


class QuietError(RuntimeError):
    pass


class QuietWindow(AbstractContextManager):
    """One campaign owner. Lock acquisition never waits, and every child is cancellable."""
    def __init__(self, approval_path, budget_seconds, *, lock_path=LOCK,
                 wall_clock=time.time, monotonic=time.monotonic, campaign_id=None):
        self.path, self.lock_path = Path(approval_path), Path(lock_path)
        self.budget = budget_seconds
        self.campaign_id = campaign_id
        self.wall, self.monotonic = wall_clock, monotonic
        self.handle, self.approval = None, None
        self.deadline, self.next_reload = None, 0

    def _approval(self):
        value = json.loads(self.path.read_text())
        if not isinstance(value, dict):
            raise QuietError('parent quiet approval must be a JSON object')
        if value.get('approved') is not True or value.get('scope') != 'phase7':
            raise QuietError('parent quiet window is not approved for phase7')
        number = finite_number
        issued, expiry = value.get('issued_unix'), value.get('expires_unix')
        if not number(issued) or not number(expiry) or not 0 < expiry - issued <= MAX_APPROVAL_SECONDS or issued > self.wall() or expiry <= self.wall():
            raise QuietError('parent quiet window expired or exceeds bounded issuance interval')
        campaign = value.get('campaign_id')
        if not isinstance(campaign, str) or not campaign.strip() or len(campaign) > 128 or (self.campaign_id is not None and campaign != self.campaign_id):
            raise QuietError('parent quiet window campaign binding differs')
        companions = value.get('companions')
        companion = companions.get('phase6') if isinstance(companions, dict) else None
        if not isinstance(companion, dict) or companion.get('agreed') is not True or any(not isinstance(companion.get(k), str) or not companion[k].strip() for k in ('by', 'reference')) or not number(companion.get('at_unix')) or not issued - COMPANION_EARLY_SECONDS <= companion['at_unix'] <= self.wall() or companion.get('campaign_id') != campaign:
            raise QuietError('explicit phase6 companion agreement reference required')
        return value

    def __enter__(self):
        self.approval = self._approval()  # No lock side effect without authorization.
        self.handle = self.lock_path.open('a+')
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.deadline = self.monotonic() + self.budget
            self.check(force=True)
        except BaseException:
            self.handle.close()
            self.handle = None
            raise
        return self

    def check(self, *, force=False):
        now = self.monotonic()
        if self.deadline is None or now >= self.deadline:
            raise QuietError('finite campaign wall-clock budget exhausted')
        if force or now >= self.next_reload:
            try:
                self.approval = self._approval()
            except Exception as error:
                raise QuietError('cannot verify parent quiet window: ' + str(error)) from error
            self.next_reload = now + .25
        if self.approval['expires_unix'] <= self.wall():
            raise QuietError('parent quiet window expired during session')
        return self.approval

    def continue_running(self):
        try:
            self.check()
            return None
        except (QuietError, OSError, ValueError) as error:
            return str(error)

    def __exit__(self, *_):
        if self.handle:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


def require_seams():
    required = [(p7.sample, ('execute_fn',)),
                (p7.execute, ('proc_sampler', 'continue_running', 'readiness_fn', 'observer_config', 'spawn_helper_build', 'resize_signal_policy')),
                (p7.observer_floor, ('execute_fn',))]
    missing = [function.__name__ + '.' + name for function, names in required
               for name in names if name not in inspect.signature(function).parameters]
    if missing:
        raise RuntimeError('measurement harness seams unavailable: ' + ', '.join(missing))


def native_proc_sample(pid):
    """Reuse the single guarded shared read; no second status read or fabricated zeros."""
    return p7.proc_sample(pid)


def self_usage():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result = {'user_ms': usage.ru_utime * 1000, 'system_ms': usage.ru_stime * 1000,
              'lifetime_peak_rss_bytes': usage.ru_maxrss * (1 if platform.system() == 'Darwin' else 1024)}
    try:
        status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
        result['rss_bytes'] = int(status['VmRSS'].split()[0]) * 1024
    except (OSError, ValueError, KeyError):
        result['rss_bytes'] = None
    return result


class Capture:
    """Inject into sample/observer_floor. Existing execute still owns every child and PTY."""
    def __init__(self, guard, *, minimal=False, floor_profile=None, evidence_path=None, policy=None, identity=None):
        self.guard, self.minimal, self.floor_profile = guard, minimal, floor_profile
        self.evidence_path = evidence_path
        self.policy = policy or ('floor-minimal' if minimal else 'floor-full' if floor_profile else 'primary')
        if self.policy not in OBSERVER_POLICIES:
            raise ValueError('unknown capture observer policy')
        self.identity = identity
        self.commands = []
        self.executions = []

    def __call__(self, command, env, cwd, case, fixture=None):
        self.guard.check(force=True)
        self.commands.append(list(command))
        if self.identity is not None:
            check_command(command, self.identity, case)
        case = copy.deepcopy(case)
        if self.floor_profile:
            size, theme = self.floor_profile
            case.update(size=list(size), theme=theme)
            env = isolated_env(theme)
        environment = dict(sorted(env.items()))
        before = self_usage()
        readiness = Readiness(case)
        result = p7.execute(command, env, cwd, case, fixture,
                            proc_sampler=(lambda _pid: None) if self.minimal else native_proc_sample,
                            continue_running=self.guard.continue_running, readiness_fn=readiness, spawn_helper_build=False,
                            observer_config=OBSERVER_POLICIES[self.policy], resize_signal_policy=RESIZE_SIGNAL_POLICY)
        after = self_usage()
        resize_issues = resize_policy_issues(result, case, require_case=False)
        if resize_issues:
            result['failure'] = result.get('failure') or '; '.join(resize_issues)
        bind_decoder_offsets(result, readiness)
        result['metrics']['child_environment'] = environment
        result['capture_command'] = list(command)
        result['observer_policy'] = self.policy
        result['metrics']['observer'] = {
            'scope': 'collector SELF during execute only; fixture/CLI CPU excluded',
            'user_cpu_ms': after['user_ms'] - before['user_ms'],
            'system_cpu_ms': after['system_ms'] - before['system_ms'],
            'rss_before_bytes': before['rss_bytes'], 'rss_after_bytes': after['rss_bytes'],
            'lifetime_peak_rss_bytes': after['lifetime_peak_rss_bytes'],
            'mode': self.policy}
        self.executions.append(copy.deepcopy(result['metrics']))
        if self.evidence_path is not None:
            archive_execute(result, self.evidence_path)
        # p7 execute retains all capture data even on an approval/deadline failure.
        return result


def archive_execute(result, path):
    """Backstop raw evidence if sample's later identity/config step raises."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    serial = copy.deepcopy(result)
    for field in ('stdout', 'stderr', 'post_screen_stdout'):
        payload = serial.pop(field)
        serial[field + '_hex'] = payload.hex() if payload is not None else None
        if field != 'post_screen_stdout':
            (path / (field + '.bin')).write_bytes(payload)
    write_json(path / 'session.json', serial)


class ReplayCapture(Capture):
    """Same floor protocol, with a retained Go startup byte stream before each marker."""
    def __init__(self, *args, replay, frames, **kwargs):
        super().__init__(*args, **kwargs)
        self.replay, self.frames = replay, frames
    def __call__(self, command, env, cwd, case, fixture=None):
        if self.evidence_path is None:
            raise ValueError('recorded replay requires an owned evidence directory')
        Path(self.evidence_path).mkdir(parents=True, exist_ok=True)
        payload_path = Path(self.evidence_path) / 'replay-frame.bin'
        payload_path.write_bytes(self.replay['bytes'])
        program = ('import os,sys,time,termios,tty\n'
                   'saved=termios.tcgetattr(0);tty.setraw(0)\n'
                   'payload=open(sys.argv[1],"rb").read()\n'
                   'try:\n'
                   f' for i in range({self.frames}):\n'
                   '  t=time.monotonic_ns();os.write(2,(str(i)+" "+str(t)+"\\n").encode())\n'
                   '  os.write(1,payload+("\\x1b[1;1HFRAME %04d"%i).encode())\n'
                   '  while os.read(0,1)!=b"x": pass\n'
                   'finally: termios.tcsetattr(0,termios.TCSANOW,saved)\n')
        return super().__call__([command[0], '-c', program, str(payload_path)], env, cwd, case, fixture)


def replay_frame(options, size, theme, report_identity=None):
    report_path = Path(options.preflight_report)
    report, current = json_snapshot(report_path)
    if report_identity is not None and current != report_identity:
        raise ValueError('validated preflight report changed before replay')
    selected = {case['name'] for case in campaign_cases(options)}
    for row in report['cases']:
        if row['case'] not in selected:
            continue  # Only cases revalidated for this campaign can supply replay.
        path = report_path.parent / row['samples']['reference']['directory'] / 'session.json'
        if sha_file(path) != row['samples']['reference'].get('session_sha256'):
            raise ValueError('validated Go replay session changed')
        session = p7.load_session(path)
        resize_issues = resize_policy_issues(session, session['case'])
        if resize_issues:
            raise ValueError('; '.join(resize_issues))
        if tuple(session['case']['size']) == tuple(size) and session['case']['theme'] == theme and row['valid']:
            startup = next(f for f in session['frames'] if f['name'] == 'startup')
            payload = session['stdout'][:startup['bytes_since_action']]
            if not payload:
                raise ValueError('representative Go startup bytes missing')
            return {'bytes': payload, 'sha256': hashlib.sha256(payload).hexdigest(), 'case': row['case'],
                    'resize_signal_policy': session['resize_signal_policy'],
                    'session_sha256': sha_file(path), 'scope': 'recorded Go startup frame stream plus marker'}
    raise ValueError('no validated matched Go frame replay for observer profile')


def isolated_env(theme):
    return {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'TZ': 'UTC',
            'TERM': 'xterm-256color', 'COLORTERM': 'truecolor', 'PB_THEME': theme,
            'PB_ANALYTICS': 'disable', **{name: str(p7.TMP) for name in
                                        ('TMPDIR', 'GOTMPDIR', 'CLAUDE_CODE_TMPDIR')}}


def frame_text(frame):
    return '\n'.join(''.join(cell[0] for cell in row) for row in frame['cells'])


def resize_policy_issues(result, case, *, require_case=True):
    """Bind the settled measurement policy to actual scripted resize events."""
    issues = []
    if require_case and case.get('resize_signal_policy') != RESIZE_SIGNAL_POLICY:
        issues.append('measurement case resize policy differs/missing')
    if result.get('resize_signal_policy') != RESIZE_SIGNAL_POLICY:
        issues.append('captured measurement resize policy differs/missing; legacy cannot enable triggers')
    scripted = {index: list(action['resize']) for index, action in enumerate(case.get('actions', []))
                if isinstance(action, dict) and 'resize' in action}
    events = [event for event in result.get('actions', []) if 'resize' in event]
    if [event.get('index') for event in events] != list(scripted):
        issues.append('captured resize sequence differs from declared script')
    current = tuple(case.get('size', [100, 30]))
    for event in events:
        geometry = event['resize']
        if event.get('resize_signal_source') != 'ioctl-only':
            issues.append('captured resize signal source differs/missing')
        if not isinstance(geometry, (list, tuple)) or len(geometry) != 2 or any(
                type(dimension) is not int or not 1 <= dimension <= 65535 for dimension in geometry):
            issues.append('captured resize geometry invalid')
            continue
        if list(geometry) != scripted.get(event.get('index')) or tuple(geometry) == current:
            issues.append('captured resize geometry differs/unchanged')
        current = tuple(geometry)
    return issues


def startup_fanout_issues(result, case):
    """Only the exact pre-input prefix may use the existing fan-out contract."""
    count = case.get('initial_fanout_count', 0)
    if not count:
        return []
    inputs = [event for event in result['actions']
              if any(key in event for key in ('input_hex', 'resize', 'signal'))]
    if not inputs or any(not finite_number(event.get('dispatch_monotonic_ns')) for event in inputs):
        return ['initial fan-out missing first-input dispatch boundary']
    before = min(event['dispatch_monotonic_ns'] for event in inputs)
    milestones = result['milestones']
    if (len(milestones) != len(result['requests']) or
            {m.get('request') for m in milestones} != set(range(len(result['requests']))) or
            any(not finite_number(m.get('received_monotonic_ns')) for m in milestones)):
        return ['initial fan-out missing/ambiguous causal receipt evidence']
    # Strictly before input; equal timestamps cannot establish causal precedence.
    initial = causal_requests(result, before - 1)
    if len(initial) != count or initial != result['requests'][:count]:
        return ['initial fan-out is not the exact pre-input request prefix']
    return []


def observation_issues(result, case, *, require_resources=True):
    issues = list(p7.validate(result))
    issues.extend(resize_policy_issues(result, case))
    issues.extend(startup_fanout_issues(result, case))
    frames = {f['name']: f for f in result['frames']}
    events = {a['index']: a for a in result['actions']}
    lifecycle = (result.get('started_monotonic_ns'), result.get('reaped_monotonic_ns'))
    if result.get('exec_error'):
        issues.append('native setup/exec failure; no CLI resource claim')
    if result.get('exited_monotonic_ns') != result.get('reaped_monotonic_ns'):
        issues.append('lifecycle timestamp is not the native exited stamp')
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in lifecycle) or lifecycle[1] <= lifecycle[0]:
        issues.append('missing/invalid process start/reap lifecycle timestamp')
    for phase in case['bench']['phases']:
        stamp = frames.get(phase['name'])
        if stamp is None:
            issues.append('missing phase checkpoint: ' + phase['name'])
            continue
        wait, frame = phase['wait'], stamp['frame']
        text = frame_text(frame)
        if any(token not in text for token in wait.get('contains', [])) or any(token in text for token in wait.get('absent', [])):
            issues.append('phase target not met: ' + phase['name'])
        if 'dimensions' in wait and [frame['columns'], frame['rows']] != wait['dimensions']:
            issues.append('resize dimensions differ: ' + phase['name'])
        if wait.get('sql_resize_geometry') and not sql_resize_geometry(frame, wait):
            issues.append('resize selected/footer geometry differs: ' + phase['name'])
        if wait.get('dense_table_geometry') and not dense_table_geometry(frame, wait):
            issues.append('dense table footer geometry incomplete: ' + phase['name'])
        if wait.get('right_edge_corner') and not any(row[-1][0] in {'┐', '┘', '╮', '╯'} for row in frame['cells'] if row):
            issues.append('resize did not paint new right-edge layout: ' + phase['name'])
        required = ('first_useful_monotonic_ns', 'stable_since_monotonic_ns', 'ready_monotonic_ns', 'last_output_monotonic_ns')
        if any(not isinstance(stamp.get(field), (int, float)) or not math.isfinite(stamp[field]) for field in required):
            issues.append('missing phase timestamp: ' + phase['name'])
            continue
        first, stable, ready = (stamp[field] for field in required[:3])
        if stamp.get('first_ever_match_monotonic_ns') != first:
            issues.append('first-ever qualifying observation provenance missing: ' + phase['name'])
        if not first <= stable <= ready or ready - stable < STABLE_MS * 1e6:
            issues.append('phase stable interval invalid: ' + phase['name'])
        trigger = phase['trigger_action_index']
        if trigger is not None:
            event = events.get(trigger)
            if event is None or not ('input_hex' in event or 'resize' in event):
                issues.append('missing actual phase dispatch: ' + phase['name'])
            elif not finite_number(event.get('dispatch_monotonic_ns')) or not finite_number(event.get('evaluated_monotonic_ns')) or (
                    event['evaluated_monotonic_ns'] != event.get('monotonic_ns') or
                    not event['evaluated_monotonic_ns'] <= event['dispatch_monotonic_ns'] <= first or
                    sum(key in event for key in ('input_hex', 'resize', 'signal')) != 1):
                issues.append('missing/ambiguous dispatch clock: ' + phase['name'])
            # Timestamp order does not prove a new frame; Readiness guards fresh output/snapshot.
        elif phase['kind'] in ('startup', 'error') and first < result['started_monotonic_ns']:
            issues.append('first useful screen precedes process start')
        effects = phase.get('effects', {})
        causal = causal_requests(result, first)
        offsets = [window[1] for window in sql_windows(causal) if window is not None]
        if any(offsets.count(int(offset)) < count for offset, count in effects.get('sql_window_counts', {}).items()):
            issues.append('causal phase request multiplicity not met: ' + phase['name'])
        if any(offsets.count(int(offset)) != count for offset, count in effects.get('sql_window_exact', {}).items()):
            issues.append('cache-hit refetched a window: ' + phase['name'])
        if any(not any(r['path'].split('?')[0] == path for r in causal) for path in effects.get('request_paths', [])):
            issues.append('missing causal phase fixture request: ' + phase['name'])
        if any(sum(r['path'].split('?')[0] == path for r in causal) != count for path, count in effects.get('request_exact', {}).items()):
            issues.append('causal endpoint multiplicity differs: ' + phase['name'])
        if phase['name'] == 'post-idle-bracket-hold':
            if (phase['kind'] != 'anchor' or phase.get('start_checkpoint') != 'idle-end' or
                    type(phase.get('duration_ms')) is not int or phase['duration_ms'] != 250):
                issues.append('malformed post-idle hold definition')
            else:
                start = frames.get('idle-end')
                if (start is None or not finite_number(start.get('ready_monotonic_ns')) or
                        ready - start['ready_monotonic_ns'] < phase['duration_ms'] * 1_000_000):
                    issues.append('post-idle hold interval shorter than definition or missing start')
        if phase['kind'] == 'idle':
            start = frames.get(phase['start_checkpoint'])
            if start is None or ready - start['ready_monotonic_ns'] < phase['duration_ms'] * 1e6:
                issues.append('idle interval shorter than definition')
    if case['bench']['kind'] == 'sql-windowed':
        windows, total = sql_windows(result['requests']), case['bench']['total_rows']
        if not windows or any(w is None or w[1] % 500 or w[1] >= total or
                              w[0] != min(500, total - w[1]) for w in windows):
            issues.append('SQL fixture/request violated LIMIT/OFFSET window contract')
        offsets = [w[1] for w in windows if w]
        if not case['bench']['cache_turnover'] and offsets.count(0) != 1:
            issues.append('cache-hit offset zero refetched in session')
        if case['bench']['cache_turnover'] and (not {0, 500, 1000, 1500}.issubset(offsets) or offsets.count(0) < 2):
            issues.append('cache turnover not demonstrated by requests')
        if case['bench'].get('sustained_scrolling') and not set(range(0, total, 500)).issubset(offsets):
            issues.append('sustained scroll did not visit all large-result windows')
    if case['bench'].get('sustained_series_scrolling') and sum(r['path'].split('?')[0] == '/prometheus/api/v1/query_range' for r in result['requests']) != 1:
        issues.append('sustained series scroll refetched unexpectedly')
    if case['bench']['kind'] == 'error':
        endpoint = case['bench']['endpoint']
        if sum(r['path'].split('?')[0] == endpoint for r in result['requests']) != 1:
            issues.append('bounded error path retried/refetched unexpectedly')
    if require_resources:
        metrics = result['metrics']
        if not isinstance(metrics.get('descendant_comparison_eligible'), bool):
            issues.append('native descendant observation eligibility unknown')
        if not isinstance(metrics.get('observed_descendants'), list):
            issues.append('native descendant attribution unknown')
        spawn = metrics.get('native_spawn', {})
        if metrics.get('resource_source') != 'native_child_wait4' or any(not re.fullmatch(r'[0-9a-f]{64}', str(spawn.get(key, ''))) for key in ('source_sha256', 'sha256')):
            issues.append('native small-spawn wait4 provenance missing; fork-floor resources ineligible')
        required = ('user_cpu_ms', 'system_cpu_ms', 'peak_rss_bytes', 'minor_faults', 'major_faults',
                    'voluntary_context_switches', 'involuntary_context_switches', 'stdout_bytes', 'stderr_bytes',
                    'block_input_operations', 'block_output_operations')
        if any(not isinstance(metrics.get(key), (int, float)) or not math.isfinite(metrics[key]) or metrics[key] < 0 for key in required):
            issues.append('missing/invalid native wait4 resource counter')
        samples = metrics.get('proc_samples', [])
        if len(samples) < 2 or any(s.get('counter_error') for s in samples):
            issues.append('insufficient/error Linux proc resource samples')
        fields = ('rss_bytes', 'user_ticks', 'system_ticks', 'minor_faults', 'major_faults',
                  'voluntary_context_switches', 'involuntary_context_switches', 'fd_count', 'monotonic_ns', 'high_water_rss_bytes')
        def valid_counter(value):
            return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
        malformed = any(any(not valid_counter(s.get(key)) for key in fields) or
                        not isinstance(s.get('io'), dict) or not s['io'] or
                        any(not valid_counter(v) for v in s['io'].values()) for s in samples)
        if malformed:
            issues.append('missing Linux proc resource counter')
        else:
            monotonic_fields = ('user_ticks', 'system_ticks', 'minor_faults', 'major_faults',
                                'voluntary_context_switches', 'involuntary_context_switches', 'monotonic_ns')
            if any(any(b[k] < a[k] for k in monotonic_fields) or set(a['io']) != set(b['io']) or
                   any(b['io'].get(k, -1) < v for k, v in a['io'].items())
                   for a, b in zip(samples, samples[1:])):
                issues.append('native proc counters decreased or IO fields changed')
        if not valid_counter(metrics.get('clock_ticks_per_second')) or metrics.get('clock_ticks_per_second', 0) == 0:
            issues.append('missing/invalid Linux proc tick frequency')
        for phase in case['bench']['phases']:
            if phase['kind'] == 'idle' and phase['name'] in frames and phase['start_checkpoint'] in frames:
                start, end = frames[phase['start_checkpoint']]['ready_monotonic_ns'], frames[phase['name']]['ready_monotonic_ns']
                if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                    issues.append('idle phase has missing lifecycle clock')
                    continue
                inside = [s for s in samples if isinstance(s.get('monotonic_ns'), (int, float)) and
                          start <= s['monotonic_ns'] <= end]
                if len(inside) < 2:
                    issues.append('idle phase has insufficient native proc coverage')
    return issues


def distribution(values):
    if not values or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError('invalid/missing distribution; no implicit zero or dropped sample')
    return {'count': len(values), 'median': statistics.median(values), 'p50': p7.run.percentile(values, .5),
            'p95': p7.run.percentile(values, .95), 'p99': p7.run.percentile(values, .99), 'max': max(values)}


def resource_interval(samples, start, end, hz):
    inside = [s for s in samples if start <= s['monotonic_ns'] <= end]
    if len(inside) < 2:
        raise ValueError('insufficient sampled resource interval')
    first, last = inside[0], inside[-1]
    seconds = (last['monotonic_ns'] - first['monotonic_ns']) / 1e9
    if seconds <= 0 or hz <= 0:
        raise ValueError('invalid proc interval clock')
    cpu_ms = (last['user_ticks'] + last['system_ticks'] - first['user_ticks'] - first['system_ticks']) / hz * 1000
    return {'sampled_seconds': seconds, 'user_cpu_ms': (last['user_ticks'] - first['user_ticks']) / hz * 1000,
            'system_cpu_ms': (last['system_ticks'] - first['system_ticks']) / hz * 1000,
            'cpu_percent_one_core': cpu_ms / (seconds * 10),
            'tick_resolution_pp': 100 / (hz * seconds),
            'endpoint_truncation_bound_pp': 200 / (hz * seconds),
            'rss_start_bytes': first['rss_bytes'], 'rss_end_bytes': last['rss_bytes'],
            'rss_growth_bytes': last['rss_bytes'] - first['rss_bytes'],
            'rss_peak_sampled_bytes': max(s['rss_bytes'] for s in inside),
            'fd_start': first['fd_count'], 'fd_end': last['fd_count'],
            'minor_faults': last['minor_faults'] - first['minor_faults'],
            'major_faults': last['major_faults'] - first['major_faults'],
            'context_switch_scope': 'sampled main thread /proc/PID/status; not task-group totals',
            'voluntary_context_switches': last['voluntary_context_switches'] - first['voluntary_context_switches'],
            'involuntary_context_switches': last['involuntary_context_switches'] - first['involuntary_context_switches'],
            'io_sampled_delta': {k: last['io'][k] - v for k, v in first['io'].items()},
            'unobserved_start_ms': (first['monotonic_ns'] - start) / 1e6,
            'unobserved_end_ms': (end - last['monotonic_ns']) / 1e6}


def phase_dispatch(event):
    origin = event.get('dispatch_monotonic_ns')
    if not finite_number(origin) or sum(key in event for key in ('input_hex', 'resize', 'signal')) != 1:
        raise ValueError('missing/ambiguous actionable dispatch clock')
    return origin


def extraction_check(result, case):
    """Exercise all extraction paths, retaining status only, never timing values."""
    try:
        extract_metrics(result, case)
        return {'valid': True, 'error': None, 'scope': 'metric extraction dry check; values discarded'}
    except Exception as error:
        return {'valid': False, 'error': repr(error), 'scope': 'metric extraction dry check; values discarded'}


def extract_metrics(result, case):
    """Only call after independent phase validation and the pair's functional comparison."""
    frames = {f['name']: f for f in result['frames']}
    events = {a['index']: a for a in result['actions']}
    started, reaped = result['started_monotonic_ns'], result['reaped_monotonic_ns']
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in (started, reaped)) or reaped <= started:
        raise ValueError('invalid process start/reap lifecycle clock')
    metrics, phases = {'cli_lifecycle_elapsed_ms': (reaped - started) / 1e6}, []
    for phase in case['bench']['phases']:
        stamp = frames[phase['name']]
        if phase['kind'] == 'anchor':
            continue
        entry = {'name': phase['name'], 'kind': phase['kind'], 'frame_bytes': stamp['bytes_since_action'],
                 'latency_trigger_eligible': False, 'latency_scope': 'primary only, requires bound per-role replay interpretation; otherwise descriptive'}
        if phase['kind'] == 'idle':
            start = frames[phase['start_checkpoint']]['ready_monotonic_ns']
            entry['resources'] = resource_interval(result['metrics']['proc_samples'], start,
                stamp['ready_monotonic_ns'], result['metrics']['clock_ticks_per_second'])
            entry['resources'].update(idle_cpu_trigger_eligible=phase.get('long_idle', False) and case['bench'].get('comparison', 'go-rust') == 'go-rust' and
                                      result['metrics']['descendant_comparison_eligible'] is True and
                                      not result['metrics']['observed_descendants'],
                                      idle_cpu_policy='long-idle with descendant attribution only; primary/short idle descriptive')
            for key in ('cpu_percent_one_core', 'rss_peak_sampled_bytes', 'rss_growth_bytes'):
                metrics['idle_' + key] = entry['resources'][key]
        else:
            trigger = phase['trigger_action_index']
            started = result['started_monotonic_ns'] if trigger is None else phase_dispatch(events[trigger])
            entry.update(first_expected_ms=(stamp['first_useful_monotonic_ns'] - started) / 1e6,
                         stable_begin_ms=(stamp['stable_since_monotonic_ns'] - started) / 1e6,
                         ready_stable_ms=(stamp['ready_monotonic_ns'] - started) / 1e6,
                         dispatch_clock='process start' if trigger is None else 'observer dispatch immediately before write/ioctl/signal; evaluation excluded',
                         burst_keys=phase.get('burst_keys', 1))
            for key in ('first_expected_ms', 'ready_stable_ms', 'frame_bytes'):
                metrics[phase['name'] + '_' + key] = entry[key]
        phases.append(entry)
    for key in ('user_cpu_ms', 'system_cpu_ms', 'peak_rss_bytes', 'minor_faults', 'major_faults',
                'voluntary_context_switches', 'involuntary_context_switches', 'stdout_bytes', 'stderr_bytes',
                'block_input_operations', 'block_output_operations'):
        metrics[key] = result['metrics'][key]
    network = []
    for milestone in result['milestones']:
        stamps = {event['name']: event['monotonic_ns'] for event in milestone['events']}
        if not all(k in stamps for k in ('headers', 'complete')):
            raise ValueError('incomplete fixture network milestone')
        received = milestone['received_monotonic_ns']
        if received > stamps['headers'] or stamps['headers'] > stamps['complete']:
            raise ValueError('non-causal fixture network milestone')
        network.append({'request': milestone['request'],
                        'request_to_headers_ms': (stamps['headers'] - received) / 1e6,
                        'request_to_complete_ms': (stamps['complete'] - received) / 1e6,
                        'body_bytes_sent': milestone['body_bytes_sent'], 'wire_bytes_sent': milestone['wire_bytes_sent']})
    if network:
        metrics['network_request_to_complete_median_ms'] = statistics.median(n['request_to_complete_ms'] for n in network)
    samples = result['metrics']['proc_samples']
    metrics['sampled_hwm_lower_bound_bytes'] = max(s['high_water_rss_bytes'] for s in samples)
    io = {'scope': 'sampled CLI lifetime; excludes unsampled tails, not exact final IO',
          **resource_interval(samples, result['started_monotonic_ns'], result['reaped_monotonic_ns'],
                              result['metrics']['clock_ticks_per_second'])}
    # Frames within one session are not IID. These are descriptive session summaries.
    for kind in ('scroll', 'resize', 'editor', 'window-network', 'cache-turnover', 'chart-table', 'sustained-scroll', 'series-scroll', 'error'):
        values = [p['first_expected_ms'] for p in phases if p['kind'] == kind]
        if values:
            metrics[kind + '_first_expected_p95_ms'] = distribution(values)['p95']
            metrics[kind + '_first_expected_max_ms'] = max(values)
    return {'metrics': metrics, 'phases': phases, 'network': network, 'sampled_lifetime': io,
            'resize_signal_policy': result['resize_signal_policy'],
            'observer': result['metrics']['observer'],
            'child_environment': result['metrics']['child_environment'],
            'native_resource_scope': 'whole child wait4 final counters; sampled /proc counters carry separate scopes',
            'native_spawn': result['metrics']['native_spawn'],
            'observed_descendants': result['metrics']['observed_descendants'],
            'latency_trigger_eligible': False, 'latency_scope': 'decoder interpretation unbound in extraction; primary only, A/A/attribution descriptive',
            'cpu_rss_trigger_eligible': case['bench'].get('comparison', 'go-rust') == 'go-rust' and result['metrics']['descendant_comparison_eligible'] is True and not result['metrics']['observed_descendants'],
            'cpu_rss_scope': 'CLI wait4 may include reaped descendants; optional observation disabled or nonempty set ineligible for isolated CLI triggers',
            'cli_lifecycle_elapsed_scope': 'observer pre-spawn to native exited lifecycle stamp; includes script waits/stability/idle; gated reap excluded; not pure compute'}


def source_snapshot():
    groups = {
        'go': [ROOT / 'main.go', ROOT / 'go.mod', ROOT / 'go.sum',
               *sorted((ROOT / 'cmd').rglob('*.go')), *sorted((ROOT / 'pkg').rglob('*.go'))],
        'rust': [ROOT / 'Cargo.toml', ROOT / 'Cargo.lock', ROOT / 'src/spec.json', ROOT / 'src/catalog.json',
                 *sorted((ROOT / 'src').rglob('*.rs'))],
        'harness': [*sorted((ROOT / 'harness').glob('*.py')), ROOT / 'harness/phase7_spawn.c']}
    result = {}
    for name, paths in groups.items():
        hashes = {str(path.relative_to(ROOT)): sha_file(path) for path in paths if path.is_file()}
        result[name] = {'files': hashes, 'sha256': p7.digest(hashes)}
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = None
    return {'head': head, 'groups': result, 'scope': 'current files; build provenance separately attests artifact sources'}


def artifact(path, provenance_path, role):
    identity = p7.phase1.binary_identity(Path(path))
    metadata, snapshot = json_snapshot(provenance_path)
    if metadata.get('binary_sha256') != identity['sha256'] or metadata.get('optimized') is not True:
        raise ValueError(role + ': optimized-build provenance must match actual executable SHA')
    if not re.fullmatch(r'[0-9a-f]{40}', metadata.get('source_commit', '')) or not re.fullmatch(r'[0-9a-f]{64}', metadata.get('source_tree_sha256', '')):
        raise ValueError(role + ': explicit source commit/tree SHA required')
    if role == 'reference' and (metadata.get('language') != 'go' or metadata['source_commit'] != PROVENANCE_COMMIT):
        raise ValueError('reference must be optimized Go at the pinned oracle commit')
    if role == 'candidate' and (metadata.get('language') != 'rust' or metadata.get('profile') != 'release'):
        raise ValueError('candidate must have Rust release-build provenance')
    return {'identity': identity, 'build_provenance': metadata,
            'provenance_path': snapshot['path'], 'provenance_size_bytes': snapshot['size_bytes'],
            'provenance_file_sha256': snapshot['sha256']}


def check_artifact(artifact):
    """Ordinary drift checks, not an atomic exec-file binding against swaps."""
    identity = artifact.get('identity', artifact)
    actual = p7.phase1.binary_identity(Path(identity['path']))
    if actual != identity:
        raise ValueError('selected executable identity/path/size changed: ' + identity['path'])
    if 'provenance_path' in artifact:
        path = Path(artifact['provenance_path'])
        if str(path.resolve()) != artifact['provenance_path'] or sha_file(path) != artifact['provenance_file_sha256'] or (
                'provenance_size_bytes' in artifact and path.stat().st_size != artifact['provenance_size_bytes']):
            raise ValueError('selected artifact metadata changed: ' + artifact['provenance_path'])
    return actual


def check_command(command, identity, case):
    expected = [identity['path'], *case['argv']]
    if command != expected:
        raise ValueError('captured native CLI argv/path differs from selected artifact/case')


def check_binding(result, identity, case):
    if result.get('binary') != identity:
        raise ValueError('returned sample binary identity/path/size differs from selected artifact')
    if result.get('case') != case:
        raise ValueError('returned sample case definition differs')
    resize_issues = resize_policy_issues(result, case)
    if resize_issues:
        raise ValueError('; '.join(resize_issues))
    check_command(result.get('capture_command'), identity, case)
    if result.get('observer_policy') != case['bench']['observer_policy']:
        raise ValueError('captured observer policy differs from case')
    if case['bench']['observer_policy'] == 'primary' and result['metrics'].get('descendant_comparison_eligible') is not False:
        raise ValueError('primary capture cannot claim observed descendant isolation')


def helper_fingerprint(helper):
    # Paths are public-normalized; proof records are stable protocol evidence,
    # while this observer resolves/pins its own actual path before capture.
    return {key: helper.get(key) for key in ('source_sha256', 'sha256', 'build_key', 'compiler_sha256',
            'compiler_version', 'flags', 'protocol_version', 'provenance_sha256', 'capability')}


def check_sample_helper(result, helper):
    if helper_fingerprint(result['metrics'].get('native_spawn', {})) != helper_fingerprint(helper):
        raise ValueError('sample helper provenance differs from prepared helper')


def final_identities(report, artifacts, helper):
    """Retain every final identity/error, including on a failed campaign."""
    issues = []
    report['artifacts_after'] = {}
    report['metadata_after'] = {}
    for name, artifact in artifacts.items():
        identity = artifact.get('identity', artifact)
        actual = None
        try:
            actual = p7.phase1.binary_identity(Path(identity['path']))
            report['artifacts_after'][name] = actual
            if 'provenance_path' in artifact:
                metadata = Path(artifact['provenance_path'])
                report['metadata_after'][name] = {'path': str(metadata.resolve()), 'sha256': sha_file(metadata),
                                                  'size_bytes': metadata.stat().st_size}
            check_artifact(artifact)
        except (OSError, ValueError) as error:
            report['artifacts_after'][name] = {**(actual or {}), 'error': repr(error)}
            issues.append(str(error))
    try:
        report['native_spawn_after'] = cached_spawn(probe=False)
        if report['native_spawn_after'] != helper:
            issues.append('prepared helper changed at end')
    except (OSError, RuntimeError) as error:
        report['native_spawn_after'] = {'error': repr(error)}
        issues.append(str(error))
    report['source_after'] = source_snapshot()
    if report['source_after'] != report['source']:
        issues.append('source/harness snapshot changed')
    return issues


def host_snapshot():
    uname = platform.uname()
    result = {'unix_time': time.time(), 'platform': {'system': uname.system, 'release': uname.release,
              'version': uname.version, 'machine': uname.machine}, 'logical_cpus': os.cpu_count(),
              'load_average': list(os.getloadavg()), 'python': platform.python_version(),
              'affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None}
    try:
        info = dict(line.split(':', 1) for line in Path('/proc/cpuinfo').read_text().splitlines() if ':' in line)
        result['cpu_model'] = info.get('model name', info.get('Hardware', 'unknown')).strip()
    except OSError:
        result['cpu_model'] = None
    result['governors'] = {}
    for path in Path('/sys/devices/system/cpu').glob('cpu*/cpufreq/scaling_governor'):
        try:
            result['governors'][path.parent.parent.name] = path.read_text().strip()
        except OSError:
            result['governors'][path.parent.parent.name] = None
    for name, path in (('memory', '/proc/meminfo'), ('load', '/proc/loadavg'),
                       ('pressure_cpu', '/proc/pressure/cpu'), ('pressure_memory', '/proc/pressure/memory')):
        try:
            result[name] = Path(path).read_text()
        except OSError:
            result[name] = None
    return result


def paired_summary(rows, metric, options):
    reference = [r['reference'][metric] for r in rows]
    candidate = [r['candidate'][metric] for r in rows]
    if not reference or any(not finite_number(value) for value in reference + candidate):
        raise ValueError('missing/nonfinite session metric; no zero or subset substitution')
    result = {'reference': p7.session_statistics([{'value': v} for v in reference], 'value', options.seed, options.bootstrap_draws),
              'candidate': p7.session_statistics([{'value': v} for v in candidate], 'value', options.seed, options.bootstrap_draws),
              'reference_distribution': distribution(reference), 'candidate_distribution': distribution(candidate)}
    if all(r > 0 for r in reference) and not metric.endswith('growth_bytes'):
        result['paired'] = p7.paired_session_statistics(
            [{'reference': a, 'candidate': b} for a, b in zip(reference, candidate)], options.seed, options.bootstrap_draws)
    else:
        result['paired'] = {'ratio': None, 'ratio_status': 'undefined for nonpositive reference or signed growth',
            'absolute_delta': p7.session_statistics([{'delta': b - a} for a, b in zip(reference, candidate)],
                                                   'delta', options.seed, options.bootstrap_draws)}
    return result


def descriptive_latency_decision(rows, metric, eligible, *, comparison='go-rust', observer_policy='primary', pooled=False):
    reference = [row['reference'][metric] for row in rows]
    candidate = [row['candidate'][metric] for row in rows]
    if not reference or any(not finite_number(value) for value in reference + candidate):
        raise ValueError('unknown descriptive latency; no zero/subset substitution')
    delta = statistics.median(b - a for a, b in zip(reference, candidate))
    relative = statistics.median((b - a) / a for a, b in zip(reference, candidate)) if all(a > 0 for a in reference) else None
    crossing = delta > 10 and relative > .10 if relative is not None else None
    context = ('harness_host_bias_sentinel' if comparison.startswith('aa-') else
               'descriptive_pooled_latency' if pooled else
               'descriptive_resource_attribution_latency' if observer_policy != 'primary' else
               'descriptive_stable_ready_latency' if metric.endswith('_ready_stable_ms') else
               'primary_first_expected_potential_regression')
    candidate_signal = context == 'primary_first_expected_potential_regression'
    if crossing:
        action = ('fresh-seed confirmation AND investigation; cannot dismiss as observer artifact without further control' if candidate_signal else
                  'harness/host-bias sentinel: investigate and confirm; no candidate-regression decision' if context == 'harness_host_bias_sentinel' else
                  'descriptive investigate note outside primary first-expected trigger family; no candidate-regression decision')
    else:
        action = 'unknown relative increase; inconclusive' if crossing is None else 'no descriptive crossing; no budget acceptance'
    return {'paired_median_absolute_delta_ms': delta, 'paired_median_relative_increase': relative,
            'descriptive_crossing_requires_confirmation': crossing,
            'material_potential_regression': crossing if candidate_signal else None,
            'context': context,
            'observer_screen_eligible': eligible,
            'latency_trigger_eligible': eligible and candidate_signal, 'accepted_trigger': False, 'budget_pass': None,
            'decision_scope': 'eligible primary first-expected screen only; no acceptance' if eligible and candidate_signal else 'inconclusive for BOTH pass and trigger',
            'required_action': action,
            'scope': 'paired medians; >10ms AND >10%; retained independent of eligibility; no observer subtraction or tail-bias bound'}


def summaries(report, options, *, pooled=False):
    if report['status'] != 'complete' or any(r['status'] != 'valid' for r in report['pairs']):
        return None  # Do not quietly summarize a valid subset of an invalid/incomplete campaign.
    groups = {}
    for pair in report['pairs']:
        if pair['purpose'] == 'measured':
            groups.setdefault(pair['case'], []).append(pair)
    result = {}
    for name, pairs in groups.items():
        if len(pairs) != options.sessions:
            raise ValueError('incomplete paired-session count')
        rows = [{role: pair['samples'][role]['measurements']['metrics'] for role in ('reference', 'candidate')}
                for pair in pairs]
        fields = set(rows[0]['reference'])
        if any(set(row[role]) != fields for row in rows for role in ('reference', 'candidate')):
            raise ValueError('missing session metric; invalid data cannot be dropped')
        result[name] = {metric: paired_summary(rows, metric, options) for metric in sorted(fields)}
        for metric in fields:
            suffix = next((suffix for suffix in ('_first_expected_ms', '_ready_stable_ms') if metric.endswith(suffix)), None)
            if suffix:
                phase_name = metric[:-len(suffix)]
                eligible = all(next((phase.get('latency_trigger_eligible', False) for phase in
                    pair['samples'][role]['measurements'].get('phases', []) if phase['name'] == phase_name), False)
                    for pair in pairs for role in ('reference', 'candidate'))
                result[name][metric]['latency_decision'] = descriptive_latency_decision(rows, metric, eligible,
                    comparison=options.comparison, observer_policy=options.observer_policy, pooled=pooled)
    return result


def _observer_control_kind(output, approval_path, options, guard, kind, preflight_identity=None):
    """Randomized selected-policy/minimal controls, under the ONE outer lock."""
    controls, rng = [], random.Random(options.seed + 101)
    observed_mode = options.observer_policy
    for size in options.sizes:
        for theme in options.themes:
            replay = replay_frame(options, size, theme, preflight_identity) if kind == 'recorded-frame' else None
            rows = []
            for round_number in range(options.floor_sessions):
                order = [observed_mode, 'minimal']
                rng.shuffle(order)
                pair = {'size': list(size), 'theme': theme, 'round': round_number, 'order': order,
                        'resize_signal_policy': RESIZE_SIGNAL_POLICY,
                        'status': 'running', 'kind': kind, 'modes': {},
                        'replay': {k: v for k, v in replay.items() if k != 'bytes'} if replay else None}
                rows.append(pair)
                controls.append(pair)
                for mode in order:
                    path = output / f'{size[0]}x{size[1]}-{theme}' / f'round-{round_number}-{mode}'
                    row = {'valid': False, 'issues': ['control not completed'],
                           'evidence': str(path.relative_to(output))}
                    pair['modes'][mode] = row
                    write_report(output / 'controls-progress.json', controls)
                    capture_type = ReplayCapture if replay else Capture
                    adapter = capture_type(guard, minimal=mode == 'minimal', floor_profile=(size, theme),
                                           policy='floor-minimal' if mode == 'minimal' else observed_mode,
                                           evidence_path=path / 'capture',
                                           **({'replay': replay, 'frames': options.floor_frames} if replay else {}))
                    try:
                        guard.check(force=True)
                        # observer_floor has NO lock. execute_fn supplies active-child checks.
                        floor = p7.observer_floor(path, approval_path, sessions=1,
                                                  frames=options.floor_frames, execute_fn=adapter)
                        guard.check(force=True)
                        if len(floor['sessions']) != 1 or len(adapter.executions) != 1:
                            raise ValueError('observer control session count differs')
                        if not isinstance(floor['sessions'][0].get('median_ms'), (int, float)) or not math.isfinite(floor['sessions'][0]['median_ms']) or floor['sessions'][0]['median_ms'] < 0:
                            raise ValueError('missing/invalid observer floor latency')
                        row.update(valid=True, issues=[], floor=floor, retained_capture=capture_file_hashes(path / 'capture'),
                                   native_and_observer_metrics=adapter.executions[0])
                    except Exception as error:
                        row.update(valid=False, issues=[repr(error)],
                                   native_and_observer_metrics=adapter.executions)
                        pair['status'] = 'invalid'
                        write_report(output / 'controls-progress.json', controls)
                        raise
                pair['status'] = 'valid'
                write_report(output / 'controls-progress.json', controls)
            values = [{'reference': r['modes']['minimal']['floor']['sessions'][0]['median_ms'],
                       'candidate': r['modes'][observed_mode]['floor']['sessions'][0]['median_ms']} for r in rows]
            if all(r['reference'] > 0 for r in values):
                summary = p7.paired_session_statistics(values, options.seed, options.bootstrap_draws)
            else:
                summary = {'ratio': None, 'absolute_delta': p7.session_statistics(
                    [{'delta': r['candidate'] - r['reference']} for r in values], 'delta', options.seed, options.bootstrap_draws)}
            write_json(output / f'{size[0]}x{size[1]}-{theme}-summary.json',
                {'comparison': 'observer ' + observed_mode + '/minimal only; never a CLI speed ratio', 'summary': summary,
                 'subtraction': 'none; near-floor CLI differences require explicit disposition'})
    return controls


def observer_controls(output, approval_path, options, guard, preflight_identity=None):
    rows = []
    for kind in ('tiny-frame', 'recorded-frame'):
        rows.extend(_observer_control_kind(Path(output) / kind, approval_path, options, guard, kind, preflight_identity))
    write_report(Path(output) / 'controls-progress.json', rows)
    return rows


def plan(options):
    cases = campaign_cases(options)
    pairs = schedule(cases, options)
    control_count = 4 * options.floor_sessions * len(options.sizes) * len(options.themes)
    count = 2 * len(pairs) + control_count
    if count > options.max_sessions:
        raise ValueError(f'planned {count} sessions exceeds finite cap {options.max_sessions}')
    return {'schema_version': SCHEMA, 'mode': 'definitions-only; no child launches',
            'resize_signal_policy': RESIZE_SIGNAL_POLICY,
            'options': options.__dict__, 'comparison': options.comparison, 'budget_evidence': options.comparison == 'go-rust', 'cases': cases, 'pairs': pairs,
            'session_count': count, 'control_session_count': control_count,
            'definitions_sha256': p7.digest(cases),
            'native_spawn_source_sha256': sha_file(ROOT / 'harness/phase7_spawn.c'),
            'dispatch_contract': {'field': 'dispatch_monotonic_ns', 'evaluation_alias': 'evaluated_monotonic_ns',
                                  'phase7_py_sha256': sha_file(ROOT / 'harness/phase7.py')},
            'observer_policy': options.observer_policy,
            'observer_config': OBSERVER_POLICIES[options.observer_policy],
            'observer_controls_definition': 'paired ' + options.observer_policy + ' vs floor-minimal; tiny and recorded-frame separately; no subtraction',
            'case_hashes': {case['name']: {'definition_sha256': p7.digest(case),
                           'fixture_sha256': p7.digest(case['fixture'])} for case in cases},
            'prior_rust': 'parent uses phase1-5 collectors on shared noninteractive commands only'}


def run_campaign(reference, candidate, reference_meta, candidate_meta, output, approval_path, options):
    require_seams()
    if options.decoder_report:
        raise ValueError('capture latency is descriptive; campaign-decode then interpret actual retained measured sessions into a new artifact')
    if platform.system() != 'Linux':
        raise RuntimeError('this collector requires Linux /proc; unsupported metrics are not fabricated')
    if not options.campaign_id:
        raise ValueError('explicit parent-assigned campaign_id required')
    definition = plan(options)
    identities = comparison_artifacts(reference, candidate, reference_meta, candidate_meta, options.comparison)
    preflight = validate_preflight(options.preflight_report, definition, identities)
    definition['preflight_report_identity'] = preflight['validated_report_identity']
    definition['preflight_report_sha256'] = preflight['validated_report_identity']['sha256']
    definition['native_spawn'] = preflight['native_spawn']
    if options.pool_id:
        _spec, definition['pool_spec_identity'] = validate_pool_spec(options.pool_spec, definition, options)
    latency = {'eligible': False, 'reason': 'actual retained campaign observer diagnostic and separate derived interpretation required'}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': SCHEMA, 'status': 'preparing', 'definition': definition,
              'resize_signal_policy': RESIZE_SIGNAL_POLICY,
              'artifacts': identities, 'source': source_snapshot(), 'host_before': host_snapshot(),
              'environment': {theme: isolated_env(theme) for theme in options.themes},
              'quiet_window_path': str(Path(approval_path).resolve()), 'lock': str(LOCK),
              'comparison': options.comparison, 'budget_evidence': options.comparison == 'go-rust', 'latency_interpretation': latency,
              'pairs': [], 'observer_controls': [], 'errors': [], 'summary': None,
              'observer_controls_evidence': 'observer-controls/controls-progress.json',
              'timing_definition': {'stable_hold_ms': STABLE_MS, 'observer_poll_ms': 10,
                                    'phase_unit': 'within-session descriptive only',
                                    'inference_unit': 'independent paired session round',
                                    'input_origin': 'observer pre-send dispatch; includes write/ioctl cost',
                                    'artifact_checks': 'full SHA before each role; outside child timing'},
              'source_definition_sha256': p7.digest(workloads.performance_workloads())}
    write_report(output / 'report.json', report)
    cases = {case['name']: case for case in definition['cases']}
    try:
        with QuietWindow(approval_path, options.budget_seconds, campaign_id=options.campaign_id) as guard:
            report.update(status='running', quiet_window=copy.deepcopy(guard.approval))
            report['campaign_boundary'] = {'started_unix': time.time(), 'started_monotonic_ns': time.monotonic_ns()}
            allocation = campaign_feasibility(options.preflight_report, options)
            if allocation['estimated_fits'] is not True:
                raise QuietError('capture allocation does not fit declared finite budget')
            report['scheduling_allocation'] = allocation
            report['allocation_admission'] = approval_allocation(guard, allocation['estimated_seconds'])
            report['observer_controls'] = observer_controls(output / 'observer-controls', approval_path, options, guard,
                                                           preflight['validated_report_identity'])
            for number, scheduled in enumerate(definition['pairs']):
                pair = {**scheduled, 'status': 'running', 'samples': {}, 'host_before': host_snapshot()}
                report['pairs'].append(pair)
                case, captures = cases[scheduled['case']], {}
                for role in scheduled['order']:
                    guard.check(force=True)
                    if cached_spawn(probe=False) != definition['native_spawn']:
                        raise RuntimeError('native helper changed during campaign')
                    expected_identity = identities[role]['identity']
                    check_artifact(identities[role])
                    path = output / 'sessions' / f'pair-{number:04d}' / role
                    row = {'artifact': str(path.relative_to(output)), 'valid': False,
                           'issues': ['session not completed'], 'measurements': None}
                    pair['samples'][role] = row
                    write_report(output / 'report.json', report)
                    try:
                        capture = Capture(guard, policy=options.observer_policy, identity=expected_identity,
                                          evidence_path=path / 'execute')
                        row['capture_boundary'] = {'started_unix': time.time(), 'started_monotonic_ns': time.monotonic_ns(),
                                                   'approval': copy.deepcopy(guard.approval)}
                        result = p7.sample(expected_identity['path'], case, path, execute_fn=capture)
                        row['capture_boundary'].update(finished_unix=time.time(), finished_monotonic_ns=time.monotonic_ns())
                        captures[role] = result
                        row['command'] = result.get('capture_command', capture.commands)
                        row.update({key + '_sha256': sha_file(path / filename) for key, filename in (('session', 'session.json'), ('stdout', 'stdout.bin'), ('stderr', 'stderr.bin'))})
                        check_artifact(identities[role])
                        check_binding(result, expected_identity, case)
                        check_sample_helper(result, definition['native_spawn'])
                        row['issues'] = observation_issues(result, case)
                        guard.check(force=True)
                    except Exception as error:
                        row['issues'] = ['capture/validation failed: ' + repr(error)]
                        raise
                comparison = p7.compare(captures['reference'], captures['candidate'])
                pair['comparison_issues'] = comparison
                if comparison or any(row['issues'] for row in pair['samples'].values()):
                    pair['status'] = 'invalid'
                    for row in pair['samples'].values():
                        row['issues'].extend('pair: ' + issue for issue in comparison)
                    raise RuntimeError('functional/measurement validation failed: ' + case['name'])
                # Compute first, then commit BOTH sides: a half-valid pair never retains summaries.
                extracted = {role: extract_metrics(captures[role], case) for role in ('reference', 'candidate')}
                for role, row in pair['samples'].items():
                    row.update(valid=True, issues=[], measurements=extracted[role])
                    row['measurements']['latency_trigger_eligible'] = latency.get('eligible', False)
                    for phase in row['measurements']['phases']:
                        phase['latency_trigger_eligible'] = latency.get('phases', {}).get(case['name'], {}).get(phase['name'], {}).get('eligible', False)
                pair.update(status='valid', host_after=host_snapshot())
                write_report(output / 'report.json', report)
            guard.check(force=True)
            check_snapshot(preflight['validated_report_identity'])
            if 'identity' in latency:
                check_snapshot(latency['identity'])
            if options.pool_id:
                check_snapshot(definition['pool_spec_identity'])
            drift = final_identities(report, identities, definition['native_spawn'])
            if drift:
                raise RuntimeError('; '.join(drift))
            report['status'] = 'complete'
            report['summary'] = summaries(report, options, pooled=bool(options.pool_id))
            guard.check(force=True)
            report['campaign_boundary'].update(finished_unix=time.time(), finished_monotonic_ns=time.monotonic_ns())
            report['quiet_window_end'] = copy.deepcopy(guard.approval)
            retained_authorization(report)
    except Exception as error:
        report.update(status='failed', summary=None)
        report['errors'].append(repr(error))
        for pair in report['pairs']:
            if pair['status'] == 'running':
                pair['status'] = 'invalid'
                for row in pair['samples'].values():
                    row.update(valid=False, measurements=None)
                    row['issues'].append('campaign ended before pair retention: ' + repr(error))
    finally:
        if 'source_after' not in report:
            report['errors'].extend(final_identities(report, identities, definition['native_spawn']))
        report['host_after'] = host_snapshot()
        report['unattempted_pairs'] = definition['pairs'][len(report['pairs']):]
        write_report(output / 'report.json', report)
    return report


class FunctionalGuard:
    """Finite functional timeout only: no performance approval or inference."""
    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds
    def check(self, *, force=False):
        if time.monotonic() >= self.deadline:
            raise RuntimeError('functional preflight budget exhausted')
    def continue_running(self):
        try:
            self.check()
            return None
        except RuntimeError as error:
            return str(error)


def cached_spawn(*, probe=True):
    """Prepare before capture; read-only verification uses the prepared snapshots."""
    if not {'build', 'probe'} <= set(inspect.signature(p7.spawn_helper).parameters):
        raise RuntimeError('shared no-build spawn_helper(build=False, probe=...) resolver not published yet')
    return p7.spawn_helper(build=False, probe=probe)


def _functional_preflight(reference, candidate, output, options, *, reference_meta=None, candidate_meta=None):
    """Actual functional oracle; raw captures retained but no timing summary computed."""
    require_seams()
    helper = cached_spawn()
    cases = campaign_cases(options)
    if 2 * len(cases) > options.max_sessions:
        raise ValueError('functional preflight exceeds finite session cap')
    identities = {role: p7.phase1.binary_identity(Path(binary)) for role, binary in
                  (('reference', reference), ('candidate', candidate))}
    aa_artifacts = None
    if options.comparison != 'go-rust':
        aa_artifacts = comparison_artifacts(reference, candidate, reference_meta, candidate_meta, options.comparison)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    guard = FunctionalGuard(options.budget_seconds)
    report = {'schema_version': SCHEMA, 'kind': 'functional preflight; no timing claim', 'valid': False,
              'resize_signal_policy': RESIZE_SIGNAL_POLICY,
              'native_spawn': helper, 'artifacts': identities, 'cases': [], 'errors': [],
              'comparison': options.comparison, 'aa_provenance': aa_artifacts, 'budget_evidence': False,
              'observer_policy': options.observer_policy,
              'observer_config': OBSERVER_POLICIES[options.observer_policy],
              'case_hashes': {c['name']: p7.digest(c) for c in cases}, 'source': source_snapshot()}
    try:
        for case in cases:
            row = {'case': case['name'], 'valid': False, 'samples': {}, 'comparison_issues': []}
            report['cases'].append(row)
            captures = {}
            for role in ('reference', 'candidate'):
                guard.check()
                if cached_spawn(probe=False) != helper:
                    raise RuntimeError('native helper changed during functional preflight; parent must rebuild and rerun')
                check_artifact(aa_artifacts[role] if aa_artifacts else identities[role])
                path = output / case['name'] / role
                row['samples'][role] = {'directory': str(path.relative_to(output)), 'issues': ['not completed']}
                write_report(output / 'report.json', report)
                adapter = Capture(guard, policy=options.observer_policy, identity=identities[role],
                                  evidence_path=path / 'execute')
                role_started = time.monotonic_ns()
                row['samples'][role]['scheduling_started_monotonic_ns'] = role_started
                result = p7.sample(identities[role]['path'], case, path, execute_fn=adapter)
                # Informational raw outcome survives a later binding exception;
                # validation order, abort behavior and admission stay unchanged.
                row['samples'][role]['capture_failure'] = result.get('failure')
                row['samples'][role]['termination'] = copy.deepcopy(result.get('termination'))
                sample_finished = time.monotonic_ns()
                row['samples'][role]['sample_finished_monotonic_ns'] = sample_finished
                row['samples'][role]['capture_and_sample_wall_ms'] = (sample_finished - role_started) / 1e6
                captures[role] = result
                row['samples'][role]['command'] = result.get('capture_command', adapter.commands)
                check_artifact(aa_artifacts[role] if aa_artifacts else identities[role])
                check_binding(result, identities[role], case)
                check_sample_helper(result, helper)
                row['samples'][role]['issues'] = observation_issues(result, case)
                row['samples'][role]['session_sha256'] = sha_file(path / 'session.json')
                row['samples'][role]['stdout_sha256'] = sha_file(path / 'stdout.bin')
                row['samples'][role]['stderr_sha256'] = sha_file(path / 'stderr.bin')
                row['samples'][role]['extraction_check'] = extraction_check(result, case)
                role_finished = time.monotonic_ns()
                row['samples'][role]['scheduling_finished_monotonic_ns'] = role_finished
                row['samples'][role]['scheduling_wall_ms'] = (role_finished - role_started) / 1e6
                row['samples'][role]['owned_postprocessing_wall_ms'] = (role_finished - sample_finished) / 1e6
                row['samples'][role]['wall_scope'] = 'sample fixture/capture/native cleanup/archive/config/hash plus owned validation/dry extraction; descriptive scheduling cost, not CLI performance'
                if not row['samples'][role]['extraction_check']['valid']:
                    row['samples'][role]['issues'].append('metric extraction dry check: ' + row['samples'][role]['extraction_check']['error'])
            row['comparison_issues'] = p7.compare(captures['reference'], captures['candidate'])
            row['valid'] = not row['comparison_issues'] and not any(r['issues'] for r in row['samples'].values())
            write_report(output / 'report.json', report)
        drift = final_identities(report, aa_artifacts or identities, helper)
        if drift:
            raise ValueError('; '.join(drift))
        report['valid'] = len(report['cases']) == len(cases) and all(row['valid'] for row in report['cases'])
    except Exception as error:
        report['errors'].append(repr(error))
    finally:
        for case_row in report['cases']:
            for sample_row in case_row['samples'].values():
                if 'scheduling_started_monotonic_ns' in sample_row and 'scheduling_wall_ms' not in sample_row:
                    sample_row['scheduling_wall_ms'] = (time.monotonic_ns() - sample_row['scheduling_started_monotonic_ns']) / 1e6
                    sample_row['wall_scope'] = 'interrupted role: full sample/processing elapsed before failure, descriptive scheduling only'
        if 'source_after' not in report:
            report['errors'].extend(final_identities(report, aa_artifacts or identities, helper))
        write_report(output / 'report.json', report)
    return report


def validate_preflight(path, definition, identities, *, offline_helper=None):
    if not path:
        raise ValueError('successful functional --preflight-report required before timing')
    report, report_identity = json_snapshot(path)
    if report.get('comparison', 'go-rust') != definition.get('comparison', 'go-rust'):
        raise ValueError('preflight comparison class differs')
    if report.get('resize_signal_policy') != RESIZE_SIGNAL_POLICY:
        raise ValueError('functional preflight resize policy differs/missing')
    if definition.get('comparison', 'go-rust') != 'go-rust' and report.get('aa_provenance') != public_value(identities):
        raise ValueError('A/A preflight provenance differs')
    if report.get('kind') != 'functional preflight; no timing claim' or report.get('valid') is not True:
        raise ValueError('functional preflight is incomplete/invalid')
    if any(report['artifacts'].get(role) != public_value(identities[role]['identity']) for role in identities):
        raise ValueError('functional preflight artifact SHA/path/size differs')
    if any(report.get('artifacts_after', {}).get(role) != public_value(identities[role]['identity']) for role in identities):
        raise ValueError('functional preflight end artifact identity differs/absent')
    rows = {row['case']: row for row in report.get('cases', [])}
    if report.get('source') != source_snapshot() or report.get('source_after') != report['source']:
        raise ValueError('functional preflight source/observer revision differs or end check absent')
    if any(report['case_hashes'].get(c['name']) != p7.digest(c) for c in definition['cases']):
        raise ValueError('functional preflight case definition differs')
    helper = cached_spawn() if offline_helper is None else check_helper_files(offline_helper)
    if any(helper_fingerprint(report.get(k, {})) != helper_fingerprint(helper)
           for k in ('native_spawn', 'native_spawn_after')):
        raise ValueError('functional preflight native helper differs')
    for case in definition['cases']:
        policy = case['bench']['observer_policy']
        if report.get('observer_policy') != policy or report.get('observer_config') != OBSERVER_POLICIES[policy]:
            raise ValueError('functional preflight observer policy differs')
        row = rows.get(case['name'], {})
        records = row.get('samples', {})
        if row.get('valid') is not True or row.get('comparison_issues') or not isinstance(records, dict) or set(records) != {'reference', 'candidate'}:
            raise ValueError('functional preflight row incomplete/invalid')
        results = {}
        dry_checks = {}
        for role in ('reference', 'candidate'):
            record = records[role]
            if record.get('issues'):
                raise ValueError('functional preflight sample previously invalid')
            result = retained_sample(Path(report_identity['path']).parent, record)
            check_artifact(identities[role])
            check_binding(result, identities[role]['identity'], case)
            check_sample_helper(result, helper)
            issues = observation_issues(result, case)
            if issues:
                raise ValueError('retained preflight no longer satisfies contracts: ' + repr(issues))
            dry_checks[role] = extraction_check(result, case)
            if not dry_checks[role]['valid']:
                raise ValueError('retained preflight metric extraction failed: ' + dry_checks[role]['error'])
            results[role] = result
        row['revalidated_extraction_checks'] = dry_checks
        issues = p7.compare(results['reference'], results['candidate'])
        if issues:
            raise ValueError('retained preflight comparison differs: ' + repr(issues))
    if source_snapshot() != report['source'] or (cached_spawn(probe=False) if offline_helper is None else check_helper_files(offline_helper)) != helper:
        raise ValueError('observer/helper changed during retained validation')
    for artifact in identities.values():
        check_artifact(artifact)
    check_snapshot(report_identity)
    # Public preflight paths are normalized and capability proofs belong to
    # that observer process. Bind captures to this process's prepared identity.
    report['native_spawn'] = helper
    report['validated_report_identity'] = report_identity
    return report


def retained_sample(root, record):
    if not isinstance(record, dict) or not isinstance(record.get('directory'), str):
        raise ValueError('retained sample directory missing')
    root = Path(root).resolve()
    relative = Path(record['directory'])
    directory = (root / relative).resolve()
    if relative.is_absolute() or '..' in relative.parts or not directory.is_relative_to(root):
        raise ValueError('retained sample directory escapes preflight')
    files = {'session': directory / 'session.json', 'stdout': directory / 'stdout.bin', 'stderr': directory / 'stderr.bin'}
    for name, file in files.items():
        if file.is_symlink() or not file.is_file() or file.stat().st_size > 256 * 1024 * 1024:
            raise ValueError('retained sample missing/oversized/symlink: ' + name)
        if sha_file(file) != record.get(name + '_sha256'):
            raise ValueError('retained sample hash differs: ' + name)
    result = p7.load_session(files['session'])
    if any(files[name].read_bytes() != result[name] for name in ('stdout', 'stderr')):
        raise ValueError('retained streams differ from session payload')
    for name, file in files.items():
        if sha_file(file) != record[name + '_sha256']:
            raise ValueError('retained sample changed during validation: ' + name)
    return result



def private_value(value):
    return p7.normalize(value, private_prefixes())


def check_helper_files(helper):
    helper = private_value(helper)
    for key, digest in (('path', 'sha256'), ('provenance_path', 'provenance_sha256')):
        path = Path(helper[key])
        if path.is_symlink() or str(path.resolve(strict=True)) != helper[key] or sha_file(path) != helper[digest]:
            raise ValueError('offline helper identity changed')
    if helper['source_sha256'] != sha_file(ROOT / 'harness/phase7_spawn.c'):
        raise ValueError('offline helper source changed')
    return helper



def feed_replay(terminal, payload, guard):
    for offset in range(0, len(payload), 65536):
        guard.check()
        terminal.feed(payload[offset:offset + 65536])


def clone_terminal(original):
    """Fresh parser/listeners, exact buffered UTF8/ANSI and owned screen state."""
    if any(stream._taking_plain_text is not True for stream in (original.main_stream, original.alt_stream)):
        raise ValueError('nonground pyte parser snapshot is non-interpretable')
    from collections import deque
    clone = p7.Terminal(original.columns, original.rows, original.theme)
    excluded = {'main', 'alt', 'active', 'main_stream', 'alt_stream', 'decoder', 'reply'}
    for key, value in vars(original).items():
        if key not in excluded:
            setattr(clone, key, copy.deepcopy(value))
    clone.decoder.setstate(original.decoder.getstate())
    for source_stream, target_stream in ((original.main_stream, clone.main_stream), (original.alt_stream, clone.alt_stream)):
        target_stream.strict = source_stream.strict
        target_stream.use_utf8 = source_stream.use_utf8
    for source, target in ((original.main, clone.main), (original.alt, clone.alt)):
        # Keep the new class/closures and default factories tied to the new
        # screen. Deepcopy of a screen object keeps old listener closures.
        buffer = target.buffer
        target.__dict__.update(copy.deepcopy({key: value for key, value in vars(source).items() if key not in ('buffer', 'history')}))
        def line_copy(line):
            new_line = buffer.default_factory()
            if hasattr(line, 'default'):
                new_line.default = copy.deepcopy(line.default)
            new_line.update(copy.deepcopy(dict(line)))
            return new_line
        buffer.clear()
        buffer.update({row: line_copy(line) for row, line in source.buffer.items()})
        if hasattr(source, 'history'):
            target.history = source.history._replace(
                top=deque((line_copy(line) for line in source.history.top), maxlen=source.history.top.maxlen),
                bottom=deque((line_copy(line) for line in source.history.bottom), maxlen=source.history.bottom.maxlen))
    clone.active = clone.alt if clone.alternate else clone.main
    return clone


class SessionReplay:
    """One forward prefix cursor/session; at most one phase snapshot retained."""
    def __init__(self, result, guard):
        self.result, self.guard, self.offset, self.resize_index = result, guard, 0, 0
        self.actions = {row['index']: row for row in result['decoder_offsets']['actions']}
        self.resizes = [event for event in result['actions'] if 'resize' in event]
        self.terminal = p7.Terminal(*result['case']['size'], result['case'].get('theme', 'dark'))
        self.terminal.feed(result['case'].get('initial_screen', '').encode())
        self.terminal.byte_offset = 0
        self.prefix_bytes = 0

    def snapshot(self, phase):
        trigger = phase['trigger_action_index']
        begin = 0 if trigger is None else self.actions[trigger]['byte_offset']
        if not isinstance(begin, int) or isinstance(begin, bool) or not self.offset <= begin <= len(self.result['stdout']):
            raise ValueError('phase starts are not forward raw byte boundaries')
        while self.resize_index < len(self.resizes) and trigger is not None and self.resizes[self.resize_index]['index'] <= trigger:
            event = self.resizes[self.resize_index]
            offset = self.actions[event['index']]['byte_offset']
            if not self.offset <= offset <= begin:
                raise ValueError('forward resize byte/action boundary invalid')
            self.advance(offset)
            self.terminal.resize(*event['resize'])
            self.resize_index += 1
        self.advance(begin)
        return clone_terminal(self.terminal)

    def advance(self, offset):
        feed_replay(self.terminal, self.result['stdout'][self.offset:offset], self.guard)
        self.prefix_bytes += offset - self.offset
        self.offset = offset


def decode_phase(result, phase, *, repetitions=5, guard=None, clock=time.perf_counter_ns, prestate=None):
    """No CLI: replay feed and exact live predicates; reconstruct prefix untimed."""
    if phase['kind'] in ('idle', 'anchor'):
        return {'status': 'not-applicable', 'scope': 'no latency trigger'}
    resize_issues = resize_policy_issues(result, result['case'])
    if resize_issues:
        raise ValueError('; '.join(resize_issues))
    if not 1 <= repetitions <= 30:
        raise ValueError('decoder repetitions outside bounded range')
    guard = guard or FunctionalGuard(60)
    offsets = result['decoder_offsets']
    if offsets.get('schema_version') != 2:
        raise ValueError('missing exact readiness-evaluation boundaries; old feed-only capture is non-interpretable')
    actions = {row['index']: row for row in offsets['actions']}
    frames = {row['name']: row for row in offsets['frames']}
    captured = next(row for row in result['frames'] if row['name'] == phase['name'])
    stamp = frames[phase['name']]
    trigger = phase['trigger_action_index']
    begin = 0 if trigger is None else actions[trigger]['byte_offset']
    first, end = stamp['first_offset'], stamp['end_offset']
    if stamp['first_monotonic_ns'] != captured['first_useful_monotonic_ns'] or not all(isinstance(v, int) and not isinstance(v, bool) for v in (begin, first, end)) or not 0 <= begin <= first <= end <= len(result['stdout']):
        raise ValueError('invalid first/dispatch/checkpoint byte binding')
    if end - begin != captured['bytes_since_action']:
        raise ValueError('phase bytes do not bind preceding actual action/checkpoint')
    evaluations = stamp.get('evaluations')
    if stamp.get('recording_error') or not isinstance(evaluations, list) or not evaluations or stamp.get('evaluation_count') != len(evaluations):
        raise ValueError('missing/incomplete bounded readiness evaluation history')
    action_started = stamp.get('action_started_monotonic_ns')
    baseline = stamp.get('baseline_bytes')
    if (not isinstance(action_started, int) or isinstance(action_started, bool) or
            not isinstance(baseline, int) or isinstance(baseline, bool) or not 0 <= baseline <= begin or
            stamp.get('wait') != phase.get('wait', {}) or
            captured.get('action_started_monotonic_ns') != action_started):
        raise ValueError('readiness action/prestate/wait binding differs')
    last_offset, last_time = begin, action_started
    for index, evaluation in enumerate(evaluations):
        offset, evaluated = evaluation.get('byte_offset'), evaluation.get('evaluated_monotonic_ns')
        if (not isinstance(offset, int) or isinstance(offset, bool) or not last_offset <= offset <= first or
                not isinstance(evaluated, int) or isinstance(evaluated, bool) or not last_time <= evaluated <= stamp['first_monotonic_ns'] or
                evaluation.get('qualified') is not (index == len(evaluations) - 1) or not isinstance(evaluation.get('tty'), dict)):
            raise ValueError('readiness boundary/order/qualification/TTY binding invalid')
        last_offset, last_time = offset, evaluated
    if last_offset != first or last_time != stamp['first_monotonic_ns']:
        raise ValueError('first-match evaluation boundary differs')
    if prestate is None:
        prestate = SessionReplay(result, guard).snapshot(phase)
    durations = []
    for _ in range(repetitions):
        guard.check()
        terminal = clone_terminal(prestate)
        guard.check()
        readiness = Readiness()
        readiness.last_bytes = baseline
        if phase.get('wait', {}).get('resize_rerender'):
            readiness.resize_snapshot = terminal.frame()
        previous = begin
        started = clock()
        for evaluation in evaluations:
            feed_replay(terminal, result['stdout'][previous:evaluation['byte_offset']], guard)
            qualified = readiness(terminal, stamp['wait'], evaluation.get('trace', []), evaluation['tty'],
                                  action_started, evaluation['evaluated_monotonic_ns'])
            if qualified != evaluation['qualified']:
                raise ValueError('recorded useful/frame/geometry evaluation does not reconstruct')
            previous = evaluation['byte_offset']
        elapsed = clock() - started
        if not isinstance(elapsed, int) or elapsed < 0:
            raise ValueError('invalid decoder diagnostic clock')
        durations.append(elapsed / 1e6)
        if phase.get('wait', {}).get('output_after_action') and first <= begin:
            raise ValueError('decoder first-match lacks fresh output')
        if phase.get('wait', {}).get('dimensions') and [terminal.columns, terminal.rows] != phase['wait']['dimensions']:
            raise ValueError('decoder first-match dimensions differ')
        # Tail/styled comparison are not timed. Prefix keeps incomplete parser
        # tokens and UTF8 decoder state; no fresh-terminal phase shortcut.
        feed_replay(terminal, result['stdout'][first:end], guard)
        if terminal.frame() != captured['frame'] or terminal.unsupported:
            raise ValueError('decoder reconstructed accepted frame differs/unsupported')
        guard.check()
    payload = result['stdout'][begin:first]
    return {'status': 'valid', 'start_offset': begin, 'first_offset': first, 'end_offset': end,
            'resize_signal_policy': result['resize_signal_policy'],
            'resize_signal_source': next((event['resize_signal_source'] for event in result['actions']
                                          if event['index'] == trigger and 'resize' in event), None),
            'first_bytes': len(payload), 'first_bytes_sha256': hashlib.sha256(payload).hexdigest(),
            'accepted_bytes_sha256': hashlib.sha256(result['stdout'][begin:end]).hexdigest(),
            'accepted_frame_sha256': p7.digest(captured['frame']), 'observer_replay_ms': durations,
            'evaluation_count': len(evaluations), 'evaluation_boundaries_sha256': p7.digest(evaluations),
            'baseline_bytes': baseline, 'runtime_wait_sha256': p7.digest(stamp['wait']),
            'feed_block_bytes': 65536,
            'scope': 'each actual retained phase feed plus useful/frame/geometry and recorder work at captured raw/TTY/time boundaries; final qualifying predicate included conservatively although native first-match stamp precedes it; prefix/tail/final validation untimed; observer interpretation only, no subtraction/internal-stage/native-duration inference'}


def decoder_diagnostics(preflight_path, output, options, guard, *, repetitions=5, clock=time.perf_counter_ns):
    initial, snapshot = json_snapshot(preflight_path)
    cases = campaign_cases(options)
    validated = offline_preflight_check(preflight_path, {'cases': cases, 'comparison': options.comparison}, initial)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'kind': 'per-role phase decoder replay diagnostic', 'valid': False,
              'resize_signal_policy': RESIZE_SIGNAL_POLICY,
              'preflight_identity': snapshot, 'source': source_snapshot(), 'cases': [], 'errors': [],
              'scope': 'observer diagnostic only; not native CLI performance or reducer/render/write duration'}
    try:
        rows = {row['case']: row for row in validated['cases']}
        for case in cases:
            row = {'case': case['name'], 'case_sha256': p7.digest(case), 'roles': {}}
            report['cases'].append(row)
            for role in ('reference', 'candidate'):
                record = rows[case['name']]['samples'][role]
                result = retained_sample(Path(snapshot['path']).parent, record)
                replay = SessionReplay(result, guard)
                phases = {}
                row['roles'][role] = {'session_sha256': record['session_sha256'], 'binary': result['binary'], 'phases': phases}
                for phase in case['bench']['phases']:
                    try:
                        state = None if phase['kind'] in ('idle', 'anchor') else replay.snapshot(phase)
                        phases[phase['name']] = decode_phase(result, phase, repetitions=repetitions, guard=guard, clock=clock, prestate=state)
                    except Exception as error:
                        phases[phase['name']] = {'status': 'invalid', 'error': repr(error), 'latency_trigger_interpretable': False}
                    guard.check()
                write_report(output / 'report.json', report)
                del result, replay
        check_snapshot(snapshot)
        if source_snapshot() != report['source']:
            raise ValueError('decoder source changed')
        report['valid'] = all(p['status'] in ('valid', 'not-applicable') for row in report['cases'] for role in row['roles'].values() for p in role['phases'].values())
    except Exception as error:
        report['errors'].append(repr(error))
    finally:
        write_report(output / 'report.json', report)
    return report


def decoder_interpretation(path, preflight, definition):
    """Preflight replay is diagnostic only and cannot authorize measured sessions."""
    if path:
        raise ValueError('preflight/feed-only diagnostic cannot enable campaign latency; use campaign-decode and interpret actual measured sessions')
    return {'eligible': False, 'reason': 'actual campaign-bound observer diagnostic and separate interpretation required'}



def capture_file_hashes(path):
    return {name + '_sha256': sha_file(Path(path) / filename) for name, filename in
            (('session', 'session.json'), ('stdout', 'stdout.bin'), ('stderr', 'stderr.bin'))}


def recorded_approval(approval, campaign_id, boundary):
    """Historical authorization audit, never a new approval or current admission."""
    if not isinstance(approval, dict) or approval.get('approved') is not True or approval.get('scope') != 'phase7' or approval.get('campaign_id') != campaign_id or not isinstance(campaign_id, str) or not campaign_id.strip():
        raise ValueError('retained quiet campaign approval missing/mismatched')
    issued, expiry = approval.get('issued_unix'), approval.get('expires_unix')
    start, end = boundary.get('started_unix'), boundary.get('finished_unix')
    mono_start, mono_end = boundary.get('started_monotonic_ns'), boundary.get('finished_monotonic_ns')
    if (any(not finite_number(value) for value in (issued, expiry, start, end, mono_start, mono_end)) or
            not 0 < expiry - issued <= MAX_APPROVAL_SECONDS or not issued <= start <= end < expiry or
            not 0 <= mono_start < mono_end):
        raise ValueError('retained approval does not cover recorded wall/monotonic boundary')
    companions = approval.get('companions')
    companion = companions.get('phase6') if isinstance(companions, dict) else None
    if (not isinstance(companion, dict) or companion.get('agreed') is not True or companion.get('campaign_id') != campaign_id or
            any(not isinstance(companion.get(key), str) or not companion[key].strip() for key in ('by', 'reference')) or
            not finite_number(companion.get('at_unix')) or not issued - COMPANION_EARLY_SECONDS <= companion['at_unix'] <= start):
        raise ValueError('retained companion campaign agreement stale/missing/mismatched')


def retained_authorization(report):
    options = report['definition']['options']
    campaign_id = options.get('campaign_id')
    boundary = report.get('campaign_boundary', {})
    recorded_approval(report.get('quiet_window'), campaign_id, boundary)
    recorded_approval(report.get('quiet_window_end'), campaign_id, boundary)
    if (not finite_number(options.get('budget_seconds')) or
            (boundary['finished_monotonic_ns'] - boundary['started_monotonic_ns']) / 1e9 > options['budget_seconds']):
        raise ValueError('recorded campaign exceeds finite declared budget')
    for pair in report['pairs']:
        for row in pair['samples'].values():
            captured = row.get('capture_boundary', {})
            recorded_approval(captured.get('approval'), campaign_id, captured)
            if (not boundary['started_unix'] <= captured['started_unix'] <= captured['finished_unix'] <= boundary['finished_unix'] or
                    not boundary['started_monotonic_ns'] <= captured['started_monotonic_ns'] <= captured['finished_monotonic_ns'] <= boundary['finished_monotonic_ns']):
                raise ValueError('retained role boundary outside campaign scope')
    return campaign_id


def require_pool_feasibility(options):
    """Declared max-four-pair chunks require actual full-profile evidence."""
    base = Options(**{**options.__dict__, 'pool_id': '', 'pool_chunk': 0, 'pool_spec': '', 'sessions': max(1, options.sessions)})
    full = Options(**{**base.__dict__, 'case_filters': ()})
    report, snapshot = json_snapshot(options.preflight_report)
    offline_preflight_check(options.preflight_report,
                            {'cases': campaign_cases(full), 'comparison': options.comparison}, report)
    allocation = campaign_feasibility(options.preflight_report,
        Options(**{**base.__dict__, 'warmups': 0, 'sessions': max(sum(counts) for counts in POOL_COUNTS),
                   'max_sessions': max(pool_session_caps(options))}))
    if allocation['estimated_fits'] is not True:
        raise ValueError('declared nine-chunk design does not fit actual full-profile feasibility; no obsolete fallback or control/reserve reduction')
    check_snapshot(snapshot)
    return allocation


def pool_session_caps(options):
    controls = 4 * options.floor_sessions * len(options.sizes) * len(options.themes)
    return [2 * sum(counts) + controls for counts in POOL_COUNTS]


def _pool_common_options(options):
    # The six varying fields remain separately bound by the exact schedule,
    # approvals and spec identity. The cap has an exact predeclared table.
    common = {key: value for key, value in options.__dict__.items()
              if key not in ('sessions', 'warmups', 'seed', 'campaign_id', 'pool_chunk', 'pool_spec')}
    common['max_sessions'] = pool_session_caps(options)[0]
    return public_value(json.loads(json.dumps(common)))


def pool_definition(options):
    # Seeds are declared before collection; each matrix variant supplies a
    # distinct base. Consecutive chunks advance by1009, never repeat seed7.
    base = Options(**{**options.__dict__, 'pool_id': '', 'pool_chunk': 0, 'pool_spec': '', 'sessions': max(1, options.sessions)})
    cases = campaign_cases(base)
    if len(cases) != 1 or options.comparison != 'go-rust' or not any(p.get('long_idle') for p in cases[0]['bench']['phases']):
        raise ValueError('pool-plan requires one exact go-rust long-idle case')
    if not options.pool_id or not options.preflight_report:
        raise ValueError('pool-plan needs pool_id and actual preflight binding')
    allocation = require_pool_feasibility(options)
    pref, pref_identity = json_snapshot(options.preflight_report)
    if pref.get('valid') is not True or pref.get('source') != source_snapshot() or pref['case_hashes'].get(cases[0]['name']) != p7.digest(cases[0]):
        raise ValueError('pool-plan preflight case/source differs')
    return {'preflight_identity': pref_identity, 'artifacts': pref['artifacts'], 'helper': helper_fingerprint(pref['native_spawn']),
            'provisional_chunk_feasibility': allocation,
            'resize_signal_policy': RESIZE_SIGNAL_POLICY,
            'schema_version': 1, 'kind': 'predeclared long-idle pooling design', 'pool_id': options.pool_id,
            'case': cases[0], 'case_sha256': p7.digest(cases[0]), 'source': source_snapshot(),
            'chunks': [{'index': i + 1, 'seed': options.seed + 1009 * i, 'warmups': counts[0], 'sessions': counts[1]}
                       for i, counts in enumerate(POOL_COUNTS)],
            'inference_unit': 'independent paired session; pointwise exploratory CI; tails descriptive',
            'control_config': {'floor_sessions': options.floor_sessions, 'floor_frames': options.floor_frames},
            'chunk_max_sessions': pool_session_caps(options), 'common_options': _pool_common_options(options),
            'analysis_seed': options.seed + 100000, 'required_totals': {'warmups': 5, 'measured': 30}}


def validate_pool_chunks(spec):
    chunks = spec.get('chunks', [])
    if len(chunks) != len(POOL_COUNTS) or not chunks or any(type(chunk.get(key)) is not int
            for chunk in chunks for key in ('index', 'seed', 'warmups', 'sessions')):
        raise ValueError('pool requires exact declared nine-chunk schedule')
    base = chunks[0]['seed']
    expected = [{'index': index + 1, 'seed': base + 1009 * index, 'warmups': counts[0], 'sessions': counts[1]}
                for index, counts in enumerate(POOL_COUNTS)]
    if chunks != expected or spec.get('analysis_seed') != base + 100000 or spec.get('required_totals') != {'warmups': 5, 'measured': 30}:
        raise ValueError('pool full seed/index/count contract differs from declared nine chunks')


def _validate_pool_contract(spec, definition, options):
    """Private contract checks; reducer uses only after fresh allocation binding."""
    validate_pool_chunks(spec)
    if spec.get('resize_signal_policy') != RESIZE_SIGNAL_POLICY or definition.get('resize_signal_policy') != RESIZE_SIGNAL_POLICY or spec.get('kind') != 'predeclared long-idle pooling design' or spec.get('pool_id') != options.pool_id or spec.get('source') != source_snapshot() or len(definition['cases']) != 1 or spec.get('case') != definition['cases'][0] or spec.get('case_sha256') != p7.digest(definition['cases'][0]) or public_value(definition.get('preflight_report_identity')) != public_value(spec.get('preflight_identity')):
        raise ValueError('pool specification identity/case/source differs')
    if spec.get('control_config') != {'floor_sessions': options.floor_sessions, 'floor_frames': options.floor_frames}:
        raise ValueError('pool floor control definition differs')
    caps = pool_session_caps(options)
    if spec.get('chunk_max_sessions') != caps or options.max_sessions != caps[options.pool_chunk - 1]:
        raise ValueError('pool exact per-chunk max_sessions differs')
    if spec.get('common_options') != _pool_common_options(options):
        raise ValueError('pool allocation/options binding differs')
    expected = spec['chunks'][options.pool_chunk - 1]
    if (expected['index'], expected['seed'], expected['warmups'], expected['sessions']) != (options.pool_chunk, options.seed, options.warmups, options.sessions):
        raise ValueError('pool chunk seed/count/index differs from predeclared design')


def validate_pool_spec(path, definition, options):
    # Live admission always performs fresh full-profile feasibility. There is
    # no caller switch to reuse a reducer's or spec's stale allocation.
    spec, snapshot = json_snapshot(path)
    _validate_pool_contract(spec, definition, options)
    allocation = require_pool_feasibility(options)
    if public_value(allocation) != public_value(spec.get('provisional_chunk_feasibility')):
        raise ValueError('actual full-preflight pool feasibility binding differs')
    check_snapshot(snapshot)
    return spec, snapshot


def pool_reports(spec_path, report_paths, output, *, budget_seconds=600):
    """Unmeasured offline reduction; CLI owns nonblocking FunctionalOwnership."""
    started = time.monotonic()
    result = {'schema_version': 1, 'kind': 'predeclared complete pooled paired sessions', 'valid': False,
              'latency_trigger_eligible': False,
              'latency_scope': 'pooled latency descriptive only; constituent campaign diagnostic eligibility is never inherited',
              'summary': None, 'spec_identity': None, 'chunks': [], 'errors': [],
              'budget_seconds': None, 'declared_budget_type': type(budget_seconds).__name__,
              'elapsed_seconds': None, 'wall_scope': 'all reduction reads, validation, feasibility, extraction and end checks; unmeasured scheduling cost'}
    try:
        result['budget_seconds'] = budget_seconds if finite_number(budget_seconds) else repr(budget_seconds)
        if not finite_number(budget_seconds) or not 0 < budget_seconds <= MAX_APPROVAL_SECONDS:
            raise ValueError('pool reduction budget must be finite non-bool 0<seconds<=3600')
        guard = FunctionalGuard(budget_seconds)
        guard.check()
        spec, spec_identity = json_snapshot(spec_path)
        result['spec_identity'] = spec_identity
        validate_pool_chunks(spec)
        if spec.get('resize_signal_policy') != RESIZE_SIGNAL_POLICY or spec.get('kind') != 'predeclared long-idle pooling design' or spec.get('source') != source_snapshot() or len(report_paths) != len(POOL_COUNTS):
            raise ValueError('pool requires complete declared nine-chunk frozen design/source')
        pref, pref_identity = json_snapshot(private_value(spec['preflight_identity']['path']))
        if pref_identity != private_value(spec['preflight_identity']):
            raise ValueError('pool preflight identity changed')
        pref_artifacts = {role: {'identity': private_value(value)} for role, value in pref['artifacts'].items()}
        validate_preflight(pref_identity['path'], {'cases': [spec['case']], 'comparison': 'go-rust'}, pref_artifacts, offline_helper=pref['native_spawn'])
        check_helper_files(pref['native_spawn'])
        guard.check()
        raw_records, starts = [], set()
        seen_chunks, seen_campaigns, seen_captures, seen_paths, pairs = set(), set(), set(), set(), []
        binding = None
        for path in report_paths:
            guard.check()
            report, snapshot = json_snapshot(path, maximum=256 * 1024 * 1024)
            result['chunks'].append({'identity': snapshot, 'observer_controls': report.get('observer_controls'), 'controls_evidence': report.get('observer_controls_evidence')})
            declared_options = private_value(report['definition']['options'])
            options = Options(**{**declared_options, 'themes': tuple(declared_options['themes']), 'sizes': tuple(tuple(s) for s in declared_options['sizes'])})
            options.validate()
            if report.get('resize_signal_policy') != RESIZE_SIGNAL_POLICY or report.get('status') != 'complete' or report.get('errors') or options.pool_id != spec['pool_id'] or options.pool_chunk in seen_chunks or snapshot['path'] in seen_paths:
                raise ValueError('failed/duplicate/wrong pool chunk')
            seen_chunks.add(options.pool_chunk); seen_paths.add(snapshot['path'])
            campaign_id = retained_authorization(report)
            if campaign_id in seen_campaigns:
                raise ValueError('retained pool campaign authorization reused')
            seen_campaigns.add(campaign_id)
            if binding is None:
                caps = pool_session_caps(options)
                if spec.get('chunk_max_sessions') != caps or options.max_sessions != caps[options.pool_chunk - 1]:
                    raise ValueError('pool exact per-chunk max_sessions differs')
                allocation = require_pool_feasibility(options)
                guard.check()
                if public_value(allocation) != public_value(spec.get('provisional_chunk_feasibility')):
                    raise ValueError('actual full-preflight pool feasibility binding differs')
                result['validated_pool_feasibility'] = allocation
            _validate_pool_contract(spec, report['definition'], options)
            if report['definition'].get('pool_spec_identity') != public_value(spec_identity):
                raise ValueError('chunk does not bind this pool specification')
            if report['source'] != spec['source'] or report.get('source_after') != report['source'] or report.get('artifacts_after') != {r: a['identity'] for r, a in report['artifacts'].items()} or helper_fingerprint(report.get('native_spawn_after', {})) != helper_fingerprint(report['definition']['native_spawn']):
                raise ValueError('chunk end source/artifact/helper drift')
            current = {key: report[key] for key in ('artifacts', 'source')}
            current.update(helper=helper_fingerprint(report['definition']['native_spawn']),
                           case_sha256=report['definition']['case_hashes'], policy=report['definition']['observer_policy'],
                           preflight=report['definition']['preflight_report_identity'])
            if public_value(report['definition']['preflight_report_identity']) != public_value(spec['preflight_identity']) or public_value({r: a['identity'] for r, a in report['artifacts'].items()}) != public_value(spec['artifacts']) or current['helper'] != spec['helper']:
                raise ValueError('chunk predeclared binary/helper/preflight binding differs')
            if binding is not None and current != binding:
                raise ValueError('pool binary/provenance/source/helper/definition/policy/preflight binding differs')
            binding = current
            expected_metadata = {role: {'path': a['provenance_path'], 'sha256': a['provenance_file_sha256'], 'size_bytes': a['provenance_size_bytes']} for role, a in report['artifacts'].items()}
            if report.get('metadata_after') != expected_metadata:
                raise ValueError('pool end metadata identity differs/absent')
            if not report.get('observer_controls'):
                raise ValueError('pool observer controls missing')
            control_path = Path(snapshot['path']).parent / report['observer_controls_evidence']
            controls, control_identity = json_snapshot(control_path, maximum=256 * 1024 * 1024)
            if controls != report['observer_controls']:
                raise ValueError('retained pool controls differ')
            if len(controls) != 2 * options.floor_sessions * len(options.themes) * len(options.sizes):
                raise ValueError('pool controls count differs')
            for control in controls:
                if control.get('status') != 'valid' or set(control.get('modes', {})) != {options.observer_policy, 'minimal'}:
                    raise ValueError('pool controls incomplete/invalid')
                for control_row in control['modes'].values():
                    control_record = {'directory': 'observer-controls/' + control['kind'] + '/' + control_row['evidence'] + '/capture', **control_row['retained_capture']}
                    raw_control = retained_sample(Path(snapshot['path']).parent, control_record)
                    if raw_control.get('resize_signal_policy') != RESIZE_SIGNAL_POLICY:
                        raise ValueError('retained observer control resize policy differs/missing')
                    raw_records.append((Path(snapshot['path']).parent, control_record))
                    if raw_control.get('failure') or not raw_control.get('termios_restored') or len(raw_control['frames']) != options.floor_frames:
                        raise ValueError('retained observer control invalid')
                    if any(f'FRAME {i:04d}' not in frame_text(frame['frame']) for i, frame in enumerate(raw_control['frames'])) or raw_control.get('termination') != {'exit': 0}:
                        raise ValueError('retained observer control target/termination differs')
                    emissions = dict(tuple(map(int, line.split())) for line in raw_control['stderr'].splitlines())
                    if set(emissions) != set(range(options.floor_frames)) or any(frame['first_useful_monotonic_ns'] < emissions[i] for i, frame in enumerate(raw_control['frames'])):
                        raise ValueError('retained observer control emission clock invalid')
                    check_sample_helper(raw_control, private_value(report['definition']['native_spawn']))
            result['chunks'][-1]['controls_identity'] = control_identity
            scheduled = schedule(report['definition']['cases'], options)
            if len(report['pairs']) != len(scheduled):
                raise ValueError('pool sessions omitted/added')
            for expected, pair in zip(scheduled, report['pairs']):
                if any(pair.get(key) != value for key, value in expected.items()) or pair.get('status') != 'valid' or set(pair.get('samples', {})) != {'reference', 'candidate'}:
                    raise ValueError('pool pair schedule/role/validity differs')
                rows = {}
                for role in ('reference', 'candidate'):
                    saved = pair['samples'][role]
                    record = {'directory': saved['artifact'], **{key: saved[key] for key in ('session_sha256', 'stdout_sha256', 'stderr_sha256')}}
                    signature = saved['session_sha256']
                    if signature in seen_captures:
                        raise ValueError('duplicate retained session reused in pool')
                    seen_captures.add(signature)
                    guard.check()
                    raw = retained_sample(Path(snapshot['path']).parent, record)
                    boundary = saved['capture_boundary']
                    if not boundary['started_monotonic_ns'] <= raw['started_monotonic_ns'] <= raw['reaped_monotonic_ns'] <= boundary['finished_monotonic_ns']:
                        raise ValueError('pooled native lifecycle outside authorized captured role boundary')
                    if raw['started_monotonic_ns'] in starts:
                        raise ValueError('duplicate capture lifecycle start in pool')
                    starts.add(raw['started_monotonic_ns'])
                    raw_records.append((Path(snapshot['path']).parent, record))
                    identity = private_value(report['artifacts'][role])
                    check_artifact(identity)
                    check_binding(raw, identity['identity'], spec['case'])
                    check_sample_helper(raw, private_value(report['definition']['native_spawn']))
                    if observation_issues(raw, spec['case']):
                        raise ValueError('pooled retained session no longer satisfies contracts')
                    rows[role] = raw
                if p7.compare(rows['reference'], rows['candidate']):
                    raise ValueError('pooled comparator rejected retained pair')
                # Recompute metrics from original evidence; never trust edits
                # to a report's measurements or a valid boolean alone.
                clone = copy.deepcopy(pair)
                for role in rows:
                    clone['samples'][role]['measurements'] = extract_metrics(rows[role], spec['case'])
                pairs.append(clone)
            check_snapshot(snapshot); check_snapshot(control_identity)
        if seen_chunks != set(range(1, len(POOL_COUNTS) + 1)) or sum(p['purpose'] == 'warmup' for p in pairs) != 5 or sum(p['purpose'] == 'measured' for p in pairs) != 30:
            raise ValueError('pool requires exactly5warmup+30measured independent pairs')
        check_snapshot(spec_identity)
        check_snapshot(pref_identity)
        for record in result['chunks']:
            check_snapshot(record['identity']); check_snapshot(record['controls_identity'])
        if source_snapshot() != spec['source']:
            raise ValueError('source changed during pool reduction')
        check_helper_files(pref['native_spawn'])
        for artifact in binding['artifacts'].values():
            check_artifact(private_value(artifact))
        for root, record in raw_records:
            guard.check()
            for name, filename in (('session', 'session.json'), ('stdout', 'stdout.bin'), ('stderr', 'stderr.bin')):
                if sha_file(root / record['directory'] / filename) != record[name + '_sha256']:
                    raise ValueError('pooled raw evidence changed at end')
        result['binding'] = binding
        result['pairs'] = pairs
        result['summary'] = summaries({'status': 'complete', 'pairs': pairs},
            Options(sessions=30, warmups=5, seed=spec['analysis_seed'], observer_policy=options.observer_policy), pooled=True)
        guard.check()
        result['valid'] = True
    except Exception as error:
        result['errors'].append(repr(error))
        result['summary'] = None
    result['elapsed_seconds'] = time.monotonic() - started
    write_report(output, result)
    return result



def offline_preflight_check(path, definition, report):
    identities = private_value(report.get('aa_provenance') or {role: {'identity': value} for role, value in report['artifacts'].items()})
    return validate_preflight(path, definition, identities, offline_helper=report['native_spawn'])


def session_work(result, session_json_bytes=0):
    """Structural counts only. They are not calibrated decoder throughput."""
    phases = [phase for phase in result['case']['bench']['phases'] if phase['kind'] not in ('idle', 'anchor')]
    actions = {row['index']: row['byte_offset'] for row in result['decoder_offsets']['actions']}
    stamps = {row['name']: row for row in result['decoder_offsets']['frames']}
    frames = {row['name']: row['frame'] for row in result['frames']}
    begins, feed_bytes, evaluations, cells = [], 0, 0, 0
    for phase in phases:
        stamp = stamps[phase['name']]
        begin = 0 if phase['trigger_action_index'] is None else actions[phase['trigger_action_index']]
        begins.append(begin)
        feed_bytes += stamp['end_offset'] - begin
        evaluations += stamp['evaluation_count']
        view = frames[phase['name']]
        cells += 2 * view['columns'] * view['rows'] + sum(len(row) for row in view.get('scrollback', []))
    return {'raw_bytes': len(result['stdout']), 'session_json_bytes': session_json_bytes,
            'prefix_bytes': max(begins, default=0), 'phase_feed_bytes': feed_bytes,
            'evaluation_count': evaluations, 'phase_count': len(phases),
            'clone_count': len(phases), 'checkpoint_cells': cells,
            'geometry_changes': sum('resize' in event for event in result['actions'])}


def repetition_work(base, repetitions, total_sessions):
    work = dict(base)
    for key in ('phase_feed_bytes', 'evaluation_count'):
        work[key] *= repetitions
    for key in ('clone_count', 'checkpoint_cells'):
        work[key] *= repetitions + 1  # phase snapshot and fresh repeats
    # Like-for-like CAP on both ratio sides. Actual volume is separately
    # retained and checked <=cap; never divide remaining cap by pilot actual.
    work['serialization_bytes'] = 65536 + 8192 * base['phase_count'] + 1024 * total_sessions
    return work


def campaign_sessions(path, guard):
    """Revalidate complete original capture evidence without child/probe acquisition."""
    report, snapshot = json_snapshot(path, maximum=256 * 1024 * 1024)
    if report.get('status') != 'complete' or report.get('errors') or report.get('unattempted_pairs'):
        raise ValueError('observer interpretation requires a complete original campaign; no valid subset')
    if report.get('source') != source_snapshot() or report.get('source_after') != report['source']:
        raise ValueError('campaign source/end binding differs')
    declared = private_value(report['definition']['options'])
    options = Options(**{**declared, 'themes': tuple(declared['themes']),
                        'sizes': tuple(tuple(size) for size in declared['sizes']),
                        'case_filters': tuple(declared['case_filters'])})
    options.validate()
    expected = plan(options)
    if report['definition']['cases'] != expected['cases'] or report['definition']['case_hashes'] != expected['case_hashes']:
        raise ValueError('campaign case/fixture/action definitions differ')
    retained_authorization(report)
    helper = private_value(report['definition']['native_spawn'])
    check_helper_files(helper)
    if helper_fingerprint(report.get('native_spawn_after', {})) != helper_fingerprint(helper):
        raise ValueError('campaign helper end provenance differs')
    artifacts = private_value(report['artifacts'])
    if public_value(report.get('artifacts_after')) != public_value({role: item['identity'] for role, item in artifacts.items()}):
        raise ValueError('campaign artifact end identities differ')
    metadata = {role: {'path': item['provenance_path'], 'sha256': item['provenance_file_sha256'],
                      'size_bytes': item['provenance_size_bytes']} for role, item in artifacts.items()}
    if public_value(report.get('metadata_after')) != public_value(metadata):
        raise ValueError('campaign metadata end identities differ')
    pref_identity = private_value(report['definition']['preflight_report_identity'])
    pref, current = json_snapshot(pref_identity['path'])
    if current != pref_identity:
        raise ValueError('campaign retained preflight identity differs')
    validated = offline_preflight_check(pref_identity['path'], expected, pref)
    if private_value(validated.get('validated_report_identity')) != pref_identity:
        raise ValueError('validated preflight snapshot differs from originally bound bytes')
    scheduled = schedule(expected['cases'], options)
    if len(scheduled) != len(report['pairs']):
        raise ValueError('campaign measured/warmup pair count differs')
    cases = {case['name']: case for case in expected['cases']}
    sessions, identities, seen = [], [(Path(snapshot['path']).parent, {'file_identity': pref_identity})], set()
    control_root = Path(snapshot['path']).parent
    controls, control_identity = json_snapshot(control_root / report['observer_controls_evidence'], maximum=256 * 1024 * 1024)
    if controls != report.get('observer_controls') or len(controls) != 2 * options.floor_sessions * len(options.themes) * len(options.sizes):
        raise ValueError('campaign retained observer controls missing/count/content differs')
    identities.append((control_root, {'file_identity': control_identity}))
    for control in controls:
        if control.get('status') != 'valid' or set(control.get('modes', {})) != {options.observer_policy, 'minimal'}:
            raise ValueError('campaign retained observer controls invalid/incomplete')
        for row in control['modes'].values():
            record = {'directory': 'observer-controls/' + control['kind'] + '/' + row['evidence'] + '/capture', **row['retained_capture']}
            raw = retained_sample(control_root, record)
            if (raw.get('resize_signal_policy') != RESIZE_SIGNAL_POLICY or raw.get('failure') or not raw.get('termios_restored') or
                    raw.get('termination') != {'exit': 0} or len(raw.get('frames', [])) != options.floor_frames or
                    any(f'FRAME {i:04d}' not in frame_text(frame['frame']) for i, frame in enumerate(raw['frames']))):
                raise ValueError('campaign retained observer control target/lifecycle invalid')
            emissions = dict(tuple(map(int, line.split())) for line in raw['stderr'].splitlines())
            if set(emissions) != set(range(options.floor_frames)) or any(frame['first_useful_monotonic_ns'] < emissions[i] for i, frame in enumerate(raw['frames'])):
                raise ValueError('campaign retained observer control emission clock invalid')
            check_sample_helper(raw, helper)
            identities.append((control_root, record))
    for index, (wanted, pair) in enumerate(zip(scheduled, report['pairs'])):
        guard.check()
        if any(pair.get(key) != value for key, value in wanted.items()) or pair.get('status') != 'valid' or set(pair.get('samples', {})) != {'reference', 'candidate'}:
            raise ValueError('campaign schedule/role/validity differs')
        raws = {}
        for role, saved in pair['samples'].items():
            if saved.get('valid') is not True or saved.get('issues'):
                raise ValueError('campaign retained sample is invalid')
            record = {'directory': saved['artifact'], **{key: saved[key] for key in ('session_sha256', 'stdout_sha256', 'stderr_sha256')}}
            if record['session_sha256'] in seen:
                raise ValueError('campaign retained session reused')
            seen.add(record['session_sha256'])
            raw = retained_sample(Path(snapshot['path']).parent, record)
            check_artifact(artifacts[role]); check_binding(raw, artifacts[role]['identity'], cases[pair['case']])
            check_sample_helper(raw, helper)
            if observation_issues(raw, cases[pair['case']]):
                raise ValueError('campaign retained sample no longer satisfies contracts')
            boundary = saved['capture_boundary']
            if not boundary['started_monotonic_ns'] <= raw['started_monotonic_ns'] <= raw['reaped_monotonic_ns'] <= boundary['finished_monotonic_ns']:
                raise ValueError('campaign native lifecycle outside recorded authorized role boundary')
            raws[role] = raw
            identities.append((Path(snapshot['path']).parent, record))
            if pair['purpose'] == 'measured':
                sessions.append({'pair_index': index, 'case': pair['case'], 'role': role, 'record': record,
                                 'binary': raw['binary'], 'work': session_work(raw, (control_root / record['directory'] / 'session.json').stat().st_size)})
        if p7.compare(raws['reference'], raws['candidate']):
            raise ValueError('campaign retained styled/effect comparison failed')
        del raw, raws
    check_snapshot(snapshot); check_snapshot(pref_identity)
    return report, snapshot, options, sessions, identities


def campaign_end_check(report, snapshot, records, guard):
    check_snapshot(snapshot)
    if source_snapshot() != report['source']:
        raise ValueError('observer source changed during campaign diagnostic/reduction')
    check_helper_files(private_value(report['definition']['native_spawn']))
    for item in private_value(report['artifacts']).values():
        check_artifact(item)
    for root, record in records:
        guard.check()
        if 'file_identity' in record:
            check_snapshot(record['file_identity'])
            continue
        for name, filename in (('session', 'session.json'), ('stdout', 'stdout.bin'), ('stderr', 'stderr.bin')):
            if sha_file(root / record['directory'] / filename) != record[name + '_sha256']:
                raise ValueError('campaign retained raw evidence changed at end')
    return {'campaign_identity': snapshot, 'source': report['source'],
            'helper': helper_fingerprint(private_value(report['definition']['native_spawn'])),
            'artifact_identities': {role: item['identity'] for role, item in private_value(report['artifacts']).items()},
            'file_identities': [record['file_identity'] for _root, record in records if 'file_identity' in record],
            'retained_capture_count': sum('file_identity' not in record for _root, record in records)}


def diagnostic_plan(path, output, *, repetitions=5, budget_seconds=600):
    """Admitted structural planning; decoder wall/fit unknown before actual pilot."""
    guard = FunctionalGuard(budget_seconds)
    if not finite_number(budget_seconds) or not 0 < budget_seconds <= MAX_APPROVAL_SECONDS or type(repetitions) is not int or not 5 <= repetitions <= 30:
        raise ValueError('finite diagnostic planning budget/repetitions required')
    original, identity, options, sessions, records = campaign_sessions(path, guard)
    if len(sessions) < 2:
        raise ValueError('warmup-only chunk diagnostic NOT APPLICABLE: no actual measured first pair')
    full = Options(**{**options.__dict__, 'case_filters': (), 'pool_id': '', 'pool_chunk': 0, 'pool_spec': '', 'max_sessions': 10000})
    pref_id = private_value(original['definition']['preflight_report_identity'])
    pref, read_identity = json_snapshot(pref_id['path'])
    if read_identity != pref_id:
        raise ValueError('diagnostic plan original full preflight identity differs')
    cases = campaign_cases(full)
    validated = offline_preflight_check(pref_id['path'], {'cases': cases, 'comparison': options.comparison}, pref)
    if private_value(validated.get('validated_report_identity')) != pref_id:
        raise ValueError('diagnostic plan validated full preflight snapshot differs')
    plan = {'schema_version': 1, 'kind': 'full-preflight-bound structural diagnostic plan', 'valid': True,
            'campaign_identity': identity, 'preflight_identity': pref_id, 'source': source_snapshot(),
            'full_case_hashes': {case['name']: p7.digest(case) for case in cases}, 'repetitions': repetitions,
            'declared_budget_seconds': budget_seconds, 'calibrated_fit': 'unknown until actual first measured pair pilot',
            'scope': 'exact structural work, not Python feedrate/performance prediction; CLI preflight wall is not decoder calibration',
            'measured_sessions': [{key: row[key] for key in ('pair_index', 'case', 'role', 'record', 'work')} for row in sessions],
            'pilot_policy': {'roles': 2, 'remaining_work_multiplier': 3, 'cleanup_seconds_per_role': 8,
                             'finalization_seconds': 30 + 5 * options.sessions,
                             'approval_launch_allowance_seconds': APPROVAL_LAUNCH_ALLOWANCE_SECONDS},
            'resize_signal_policy': RESIZE_SIGNAL_POLICY}
    campaign_end_check(original, identity, records, guard)
    if Path(output).exists():
        raise ValueError('diagnostic plan output must be new')
    write_report(output, plan)
    return plan


def approval_allocation(guard, allocation, elapsed=0):
    """Admission only; recorded scheduling allocation is not a fit guarantee."""
    guard.check(force=True)
    now = guard.wall() if callable(getattr(guard, 'wall', None)) else time.time()
    if not finite_number(now):  # Explicit synthetic Mock guard; no live bypass.
        now = time.time()
    expiry = guard.approval.get('expires_unix')
    remaining = guard.budget - elapsed
    deadline = getattr(guard, 'deadline', None)
    if finite_number(deadline):
        remaining = deadline - guard.monotonic()
    if not finite_number(allocation) or allocation <= 0 or remaining < allocation or not finite_number(expiry) or expiry - now < allocation:
        raise QuietError('remaining budget/approval lifetime cannot admit recorded allocation including launch allowance')
    return {'allocation_seconds': allocation, 'remaining_budget_seconds': remaining, 'remaining_approval_seconds': expiry - now}


def pilot_allocation(pilot, remaining, repetitions, total_sessions, pairs):
    if len(pilot) != 2 or any(not finite_number(row.get('wall_seconds')) or row['wall_seconds'] <= 0 for row in pilot):
        raise ValueError('complete actual first-pair pilot wall required; no timing proxy')
    keys = set(pilot[0]['work'])
    if any(set(row['work']) != keys for row in pilot):
        raise ValueError('pilot work dimensions differ')
    if any(not finite_number(value) or value < 0 for row in pilot for value in row['work'].values()):
        raise ValueError('unknown/invalid pilot work basis')
    basis = {key: max(row['work'][key] for row in pilot) for key in keys}
    maximum_wall = max(row['wall_seconds'] for row in pilot)
    allocations = []
    for row in remaining:
        target = repetition_work(row['work'], repetitions, total_sessions)
        if set(target) != keys:
            raise ValueError('remaining positive work dimension missing pilot basis')
        ratios = [1.0]
        for key, value in target.items():
            base = basis[key]
            if not finite_number(value) or value < 0 or not finite_number(base) or base < 0 or (value > 0 and base == 0):
                raise ValueError('remaining positive work dimension has zero/unknown pilot basis: ' + key)
            if value > 0:
                ratios.append(value / base)
        ratio = max(ratios)
        allocations.append({'pair_index': row['pair_index'], 'role': row['role'], 'maximum_work_ratio': ratio,
                            'seconds': 3 * maximum_wall * ratio + 8})
    return {'seconds': sum(row['seconds'] for row in allocations) + 30 + 5 * pairs + APPROVAL_LAUNCH_ALLOWANCE_SECONDS,
            'remaining_sessions': allocations, 'pilot_maximum_wall_seconds': maximum_wall,
            'pilot_work_basis': basis, 'multiplier': 3, 'cleanup_seconds_per_role': 8,
            'finalization_seconds': 30 + 5 * pairs, 'approval_launch_allowance_seconds': APPROVAL_LAUNCH_ALLOWANCE_SECONDS,
            'scope': 'observed scheduling extrapolation with reserves; not guaranteed rate, fit, performance or total bias bound'}


def campaign_diagnostics(path, output, guard, *, repetitions=5, clock=time.perf_counter_ns, plan_path=None):
    """Separately approved observer replay of each actual measured role session."""
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    diagnostic = {'schema_version': 2, 'kind': 'actual measured-session observer replay', 'valid': False,
                  'source': source_snapshot(), 'sessions': [], 'errors': [],
                  'host_before': host_snapshot(),
                  'observer_clock': {'name': 'perf_counter_ns', 'resolution_seconds': time.get_clock_info('perf_counter').resolution,
                                     'injected_test_clock': clock is not time.perf_counter_ns},
                  'scope': 'observer interpretation only; repetitions are not independent CLI sessions; no subtraction',
                  'resize_signal_policy': RESIZE_SIGNAL_POLICY, 'budget_seconds': guard.budget,
                  'diagnostic_boundary': {'started_unix': time.time(), 'started_monotonic_ns': time.monotonic_ns()}}
    started_wall = time.monotonic()
    try:
        if not finite_number(guard.budget) or not 0 < guard.budget <= MAX_APPROVAL_SECONDS:
            raise ValueError('diagnostic requires declared finite budget<=3600')
        if not isinstance(repetitions, int) or isinstance(repetitions, bool) or not 5 <= repetitions <= 30:
            raise ValueError('campaign observer diagnostics require5..30 descriptive clock-noise repetitions')
        original, identity, options, sessions, records = campaign_sessions(path, guard)
        if len(sessions) < 2:
            raise ValueError('warmup-only chunk diagnostic NOT APPLICABLE: no actual measured first pair')
        cases = {case['name']: case for case in original['definition']['cases']}
        if plan_path is None:
            raise ValueError('predeclared full-matching-preflight structural diagnostic plan required before booking')
        declared, plan_identity = json_snapshot(plan_path, maximum=256 * 1024 * 1024)
        full = Options(**{**options.__dict__, 'case_filters': (), 'pool_id': '', 'pool_chunk': 0, 'pool_spec': '', 'max_sessions': 10000})
        wanted = [{key: row[key] for key in ('pair_index', 'case', 'role', 'record', 'work')} for row in sessions]
        if (declared.get('kind') != 'full-preflight-bound structural diagnostic plan' or declared.get('valid') is not True or
                private_value(declared.get('campaign_identity')) != identity or declared.get('source') != source_snapshot() or
                private_value(declared.get('preflight_identity')) != private_value(original['definition']['preflight_report_identity']) or
                declared.get('full_case_hashes') != {case['name']: p7.digest(case) for case in campaign_cases(full)} or
                declared.get('repetitions') != repetitions or declared.get('declared_budget_seconds') != guard.budget or
                private_value(declared.get('measured_sessions')) != wanted):
            raise ValueError('diagnostic structural plan/campaign/work/full-preflight/source/budget binding differs')
        guard.check(force=True)
        approval = copy.deepcopy(guard.approval)
        if approval.get('campaign_id') == options.campaign_id:
            raise ValueError('diagnostic requires a separate fresh campaign-bound quiet approval/ID')
        diagnostic.update(campaign_identity=identity, original_campaign_id=options.campaign_id,
                          structural_plan_identity=plan_identity,
                          budget_seconds=guard.budget,
                          quiet_window=approval, diagnostic_campaign_id=approval.get('campaign_id'))
        pilot = []
        pending = list(sessions)
        for index, sample in enumerate(sessions):
            if index >= 2:
                if any(data['status'] not in ('valid', 'not-applicable') for row in diagnostic['sessions'] for data in row['phases'].values()):
                    raise ValueError('first-pair pilot incomplete/non-interpretable; no accepted subset')
                allocation = pilot_allocation(pilot, pending, repetitions, len(sessions), options.sessions)
                diagnostic.setdefault('admissions', []).append({'before_session': index, 'extrapolation': allocation,
                    **approval_allocation(guard, allocation['seconds'], time.monotonic() - started_wall)})
            session_started = time.monotonic()
            raw = retained_sample(Path(identity['path']).parent, sample['record'])
            replay = SessionReplay(raw, guard)
            row = {key: sample[key] for key in ('pair_index', 'case', 'role')}
            row.update(retained_capture=sample['record'], binary=sample['binary'], phases={})
            diagnostic['sessions'].append(row)
            for phase in cases[sample['case']]['bench']['phases']:
                try:
                    prestate = None if phase['kind'] in ('idle', 'anchor') else replay.snapshot(phase)
                    row['phases'][phase['name']] = decode_phase(raw, phase, repetitions=repetitions, guard=guard, clock=clock, prestate=prestate)
                except Exception as error:
                    row['phases'][phase['name']] = {'status': 'invalid', 'error': repr(error), 'observer_replay_ms': None}
                guard.check()
            evidence = output / 'sessions' / f'{sample["pair_index"]:04d}-{sample["role"]}.json'
            write_report(evidence, row)
            progress = {'completed_sessions': index + 1, 'total_sessions': len(sessions),
                        'session_evidence': [{'pair_index': item['pair_index'], 'role': item['role']} for item in diagnostic['sessions']]}
            write_report(output / 'progress.json', progress)
            serialized = evidence.stat().st_size + (output / 'progress.json').stat().st_size
            work = repetition_work(sample['work'], repetitions, len(sessions))
            row['actual_serialization_bytes'] = serialized
            row['serialization_cap_bytes'] = work['serialization_bytes']
            if serialized > work['serialization_bytes']:
                raise ValueError('actual diagnostic serialization exceeds predeclared work cap')
            row['scheduling_wall_seconds'] = time.monotonic() - session_started
            row['actual_work'] = work
            if index < 2:
                pilot.append({'wall_seconds': row['scheduling_wall_seconds'], 'work': work, 'actual_serialization_bytes': serialized})
                diagnostic['pilot'] = pilot
            pending.pop(0)
            del raw, replay, prestate
        diagnostic['end_bindings'] = campaign_end_check(original, identity, records, guard)
        check_snapshot(plan_identity)
        guard.check(force=True)
        diagnostic['diagnostic_boundary'].update(finished_unix=time.time(), finished_monotonic_ns=time.monotonic_ns())
        diagnostic['quiet_window_end'] = copy.deepcopy(guard.approval)
        recorded_approval(diagnostic['quiet_window'], diagnostic['diagnostic_campaign_id'], diagnostic['diagnostic_boundary'])
        recorded_approval(diagnostic['quiet_window_end'], diagnostic['diagnostic_campaign_id'], diagnostic['diagnostic_boundary'])
        diagnostic['valid'] = all(data['status'] in ('valid', 'not-applicable') for row in diagnostic['sessions'] for data in row['phases'].values())
    except Exception as error:
        diagnostic['errors'].append(repr(error))
        diagnostic['valid'] = False
        diagnostic['status'] = 'diagnostic-incomplete/non-interpretable; no accepted subset'
        if 'sessions' in locals() and 'cases' in locals():
            completed = {(row['pair_index'], row['role']) for row in diagnostic['sessions']}
            for sample in sessions:
                if (sample['pair_index'], sample['role']) not in completed:
                    diagnostic['sessions'].append({key: sample[key] for key in ('pair_index', 'case', 'role')})
                    diagnostic['sessions'][-1].update(retained_capture=sample['record'], binary=sample['binary'],
                        phases={phase['name']: {'status': 'not-attempted', 'observer_replay_ms': None,
                                               'error': 'complete diagnostic not admitted; no valid subset'} for phase in cases[sample['case']]['bench']['phases']})
    diagnostic['host_after'] = host_snapshot()
    write_report(output / 'report.json', diagnostic)
    diagnostic['actual_diagnostic_wall_seconds'] = time.monotonic() - started_wall
    diagnostic['wall_scope'] = 'complete validation, forward reconstruction/clones/replay/session progress/final report; excludes final wall-metadata rewrite and guard close'
    write_report(output / 'report.json', diagnostic)
    return diagnostic


def observer_eligibility(by_pair, options):
    """Screen replayed components only; unreplayed loop/syscall costs unknown."""
    work = {role: statistics.median(row[role]['observer_ms'] for row in by_pair.values()) for role in ('reference', 'candidate')}
    counts = {role: statistics.median(row[role]['evaluations'] for row in by_pair.values()) for role in ('reference', 'candidate')}
    work_distribution = {role: distribution([row[role]['observer_ms'] for row in by_pair.values()]) for role in ('reference', 'candidate')}
    delta = work['candidate'] - work['reference']
    eligible = (options.observer_policy == 'primary' and options.comparison == 'go-rust' and
                len(by_pair) == options.sessions and options.sessions >= 30 and options.warmups >= 5 and
                abs(delta) <= 1.0 and max(work.values()) <= 1.0 and counts['candidate'] == counts['reference'])
    return {'eligible': eligible, 'paired_sessions': len(by_pair), 'role_median_observer_delta_ms': delta,
            'role_median_total_observer_work_ms': work, 'role_median_evaluation_count': counts,
            'role_session_observer_work_distribution_ms': work_distribution,
            'median_evaluation_count_delta': counts['candidate'] - counts['reference'],
            'guards': 'matched delta<=1ms AND max role median replay work<=1ms AND equal median evaluation counts',
            'scope': 'replayed components only; native.poll/approval/select/read/tcgetattr/proc/other loop costs UNKNOWN; no universal total-bias bound, subtraction or internal-stage inference'}


def stamp_interpretation(pairs, interpretation):
    for pair in pairs:
        if pair['purpose'] != 'measured':
            continue
        phases = interpretation.get(pair['case'], {})
        for saved in pair['samples'].values():
            for phase in saved['measurements']['phases']:
                phase['latency_trigger_eligible'] = phases.get(phase['name'], {}).get('eligible', False)
            saved['measurements']['latency_trigger_eligible'] = bool(phases) and all(data['eligible'] for data in phases.values())


def interpret_campaign(path, diagnostic_path, output, *, budget_seconds=600):
    """NEW derived artifact only; all original capture and diagnostic bytes immutable."""
    output = Path(output)
    if output.exists():
        raise ValueError('interpret output must be a new artifact')
    derived = {'schema_version': 2, 'kind': 'campaign-bound derived observer interpretation', 'valid': False,
               'latency_trigger_eligible': False, 'latency_interpretation': {}, 'errors': [], 'summary': None,
               'scope': 'original capture immutable/descriptive; observer screen only; no subtraction/internal/native-stage claim'}
    if not finite_number(budget_seconds) or not 0 < budget_seconds <= MAX_APPROVAL_SECONDS:
        raise ValueError('interpret requires declared finite budget<=3600')
    guard = FunctionalGuard(budget_seconds)
    derived['budget_seconds'] = budget_seconds
    started_wall = time.monotonic()
    try:
        original, identity, options, samples, records = campaign_sessions(path, guard)
        diagnostic, diagnostic_identity = json_snapshot(diagnostic_path, maximum=256 * 1024 * 1024)
        derived.update(campaign_identity=identity, diagnostic_identity=diagnostic_identity)
        if (diagnostic.get('kind') != 'actual measured-session observer replay' or diagnostic.get('valid') is not True or diagnostic.get('errors') or
                private_value(diagnostic.get('campaign_identity')) != identity or diagnostic.get('source') != source_snapshot() or
                diagnostic.get('original_campaign_id') != options.campaign_id or diagnostic.get('resize_signal_policy') != RESIZE_SIGNAL_POLICY or
                diagnostic.get('diagnostic_campaign_id') == options.campaign_id):
            raise ValueError('actual campaign/session diagnostic provenance invalid; old preflight diagnostic cannot enable triggers')
        if diagnostic.get('observer_clock', {}).get('injected_test_clock') is not False or diagnostic.get('observer_clock', {}).get('name') != 'perf_counter_ns':
            raise ValueError('injected/unknown observer clock is unit evidence only, not campaign interpretation')
        recorded_approval(diagnostic.get('quiet_window'), diagnostic.get('diagnostic_campaign_id'), diagnostic.get('diagnostic_boundary', {}))
        recorded_approval(diagnostic.get('quiet_window_end'), diagnostic.get('diagnostic_campaign_id'), diagnostic.get('diagnostic_boundary', {}))
        boundary = diagnostic['diagnostic_boundary']
        if (not finite_number(diagnostic.get('budget_seconds')) or not 0 < diagnostic['budget_seconds'] <= MAX_APPROVAL_SECONDS or
                (boundary['finished_monotonic_ns'] - boundary['started_monotonic_ns']) / 1e9 > diagnostic['budget_seconds']):
            raise ValueError('diagnostic recorded boundary exceeds declared finite budget')
        rows = {(row['pair_index'], row['role']): row for row in diagnostic['sessions']}
        if len(rows) != len(diagnostic['sessions']) or set(rows) != {(row['pair_index'], row['role']) for row in samples}:
            raise ValueError('diagnostic omitted/duplicated/added actual measured sessions; no valid subset')
        cases = {case['name']: case for case in original['definition']['cases']}
        collapsed = {}
        pairs = copy.deepcopy(original['pairs'])
        for sample in samples:
            guard.check()
            raw = retained_sample(Path(identity['path']).parent, sample['record'])
            replay = SessionReplay(raw, guard)
            row = rows[sample['pair_index'], sample['role']]
            if row.get('case') != sample['case'] or private_value(row.get('retained_capture')) != sample['record'] or private_value(row.get('binary')) != sample['binary']:
                raise ValueError('diagnostic actual byte/session/case/binary binding differs')
            phases = cases[sample['case']]['bench']['phases']
            if set(row['phases']) != {phase['name'] for phase in phases}:
                raise ValueError('diagnostic phase set incomplete')
            for phase in phases:
                data = row['phases'][phase['name']]
                if phase['kind'] in ('idle', 'anchor'):
                    if data.get('status') != 'not-applicable':
                        raise ValueError('diagnostic non-latency phase scope differs')
                    continue
                # Repeat once without measuring to re-derive all raw, boundary,
                # TTY, first-target and accepted-frame bindings from original data.
                expected = decode_phase(raw, phase, repetitions=1, guard=guard, clock=lambda: 0, prestate=replay.snapshot(phase))
                if data.get('status') != 'valid' or any(data.get(key) != value for key, value in expected.items() if key != 'observer_replay_ms'):
                    raise ValueError('diagnostic phase/predicate/raw/frame binding invalid')
                values = data.get('observer_replay_ms')
                if not isinstance(values, list) or not 5 <= len(values) <= 30 or any(not finite_number(value) or value < 0 for value in values):
                    raise ValueError('diagnostic repetitions invalid/unknown; no invented zero')
                collapsed.setdefault((sample['case'], phase['name']), {}).setdefault(sample['pair_index'], {})[sample['role']] = {
                    'observer_ms': statistics.median(values), 'evaluations': expected['evaluation_count']}
            pairs[sample['pair_index']]['samples'][sample['role']]['measurements'] = extract_metrics(raw, cases[sample['case']])
            del raw, replay
        for (case, phase), by_pair in collapsed.items():
            if len(by_pair) != options.sessions or any(set(pair) != {'reference', 'candidate'} for pair in by_pair.values()):
                raise ValueError('incomplete paired diagnostic population')
            paired = list(by_pair.values())
            derived['latency_interpretation'].setdefault(case, {})[phase] = {
                **observer_eligibility(by_pair, options),
                'observer_paired_pointwise_summary': paired_summary(paired, 'observer_ms', options)}
        stamp_interpretation(pairs, derived['latency_interpretation'])
        end_bindings = campaign_end_check(original, identity, records, guard)
        if private_value(diagnostic.get('end_bindings')) != end_bindings:
            raise ValueError('diagnostic exact campaign/preflight/helper/artifact/control end bindings differ')
        check_snapshot(diagnostic_identity)
        derived.update(valid=True, summary=summaries({'status': 'complete', 'pairs': pairs}, options, pooled=bool(options.pool_id)),
                       pairs=pairs, latency_trigger_eligible=bool(collapsed) and all(data['eligible'] for phases in derived['latency_interpretation'].values() for data in phases.values()))
    except Exception as error:
        derived['errors'].append(repr(error))
        derived['latency_trigger_eligible'] = False
        derived['latency_interpretation'] = {}
        derived['summary'] = None
    write_report(output, derived)
    derived['actual_interpret_wall_seconds'] = time.monotonic() - started_wall
    write_report(output, derived)
    return derived


def campaign_feasibility(preflight_path, options, *, floor_reserve_seconds=30, cleanup_margin_seconds=8):
    """Pre-booking allocation estimate from descriptive whole-role costs, not performance."""
    if not finite_number(floor_reserve_seconds) or floor_reserve_seconds < 23 or not finite_number(cleanup_margin_seconds) or cleanup_margin_seconds < 8:
        raise ValueError('feasibility floor/cleanup reserves below declared minimum')
    report, identity = json_snapshot(preflight_path)
    definition = plan(options)
    if report.get('valid') is not True:
        raise ValueError('feasibility needs valid retained functional evidence')
    report = offline_preflight_check(preflight_path, definition, report)
    rows = {row['case']: row for row in report['cases']}
    reserve = definition['control_session_count'] * floor_reserve_seconds + APPROVAL_LAUNCH_ALLOWANCE_SECONDS
    details = []
    for case in definition['cases']:
        if report['case_hashes'].get(case['name']) != p7.digest(case):
            raise ValueError('feasibility case definition differs')
        costs = [rows[case['name']]['samples'][role].get('scheduling_wall_ms') for role in ('reference', 'candidate')]
        if any(not finite_number(cost) or cost <= 0 for cost in costs):
            raise ValueError('whole-role scheduling cost missing/invalid')
        idle = next((p['duration_ms'] / 1000 for p in case['bench']['phases'] if p['kind'] == 'idle'), 0)
        seconds = idle + 1.5 * max(0, max(costs) / 1000 - idle) + cleanup_margin_seconds
        count = 2 * (options.warmups + options.sessions)
        reserve += count * seconds
        details.append({'case': case['name'], 'observed_role_wall_ms': costs, 'reserved_seconds_per_role': seconds, 'roles': count})
    # Explicit allocation for report/controls serialization/final drift work,
    # not a fabricated measured floor. Unknown host stalls can exceed it.
    serialization_margin = 30 + 5 * len(definition['pairs'])
    reserve += serialization_margin
    check_snapshot(identity)
    return {'kind': 'pre-booking conservative scheduling allocation', 'preflight_identity': identity,
            'estimated_seconds': reserve, 'budget_seconds': options.budget_seconds,
            'estimated_fits': reserve <= options.budget_seconds <= MAX_APPROVAL_SECONDS,
            'details': details, 'floor_reserve_seconds_each': floor_reserve_seconds,
            'cleanup_margin_seconds_per_role': cleanup_margin_seconds,
            'approval_launch_allowance_seconds': APPROVAL_LAUNCH_ALLOWANCE_SECONDS,
            'serialization_finalization_margin_seconds': serialization_margin,
            'scope': 'observed full-role scheduling costs plus predeclared reserves; not CLI speed evidence, guarantee or quiet approval; do not book if missing/non-fitting'}


def parse_size(text):
    try:
        width, height = (int(value) for value in text.lower().split('x'))
        return width, height
    except ValueError as error:
        raise argparse.ArgumentTypeError('size must be COLUMNSxROWS') from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'run', 'reduce', 'preflight', 'decode', 'diagnostic-plan', 'campaign-decode', 'interpret', 'pool-plan', 'pool', 'feasibility'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--reference-metadata', type=Path)
    parser.add_argument('--candidate-metadata', type=Path)
    parser.add_argument('--quiet-window', type=Path)
    parser.add_argument('--diagnostic-plan', type=Path)
    parser.add_argument('--campaign-id', default='')
    parser.add_argument('--preflight-report', default='')
    parser.add_argument('--observer-policy', choices=('primary', 'resource-attribution'), default='primary')
    parser.add_argument('--comparison', choices=('go-rust', 'aa-go', 'aa-rust'), default='go-rust')
    parser.add_argument('--pool-id', default='')
    parser.add_argument('--pool-chunk', type=int, default=0)
    parser.add_argument('--pool-spec', default='')
    parser.add_argument('--chunk-report', action='append', default=[])
    parser.add_argument('--decoder-report', default='')
    parser.add_argument('--decoder-repetitions', type=int, default=5)
    parser.add_argument('--report', type=Path, help='existing report for offline reduce')
    parser.add_argument('--sessions', type=int, default=3, help='paired measured rounds per case variant')
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--case', action='append', default=[], help='case glob; repeatable')
    parser.add_argument('--theme', action='append', choices=('dark', 'light'))
    parser.add_argument('--size', action='append', type=parse_size)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--max-sessions', type=int, default=256)
    parser.add_argument('--budget-seconds', type=float, default=600)
    parser.add_argument('--session-timeout', type=float, default=30)
    parser.add_argument('--idle-seconds', type=float, default=2)
    parser.add_argument('--include-long-idle', action='store_true')
    parser.add_argument('--long-idle-seconds', type=float, default=60)
    parser.add_argument('--metric-points', type=int, default=256)
    parser.add_argument('--network-delay-ms', type=int, default=150)
    parser.add_argument('--floor-sessions', type=int, default=3)
    parser.add_argument('--floor-frames', type=int, default=20)
    args = parser.parse_args(argv)
    options = Options(sessions=args.sessions, warmups=args.warmups, themes=tuple(args.theme or ('dark', 'light')),
                      sizes=tuple(args.size or ((120, 40),)), case_filters=tuple(args.case), seed=args.seed,
                      max_sessions=args.max_sessions, budget_seconds=args.budget_seconds,
                      session_timeout=args.session_timeout, idle_seconds=args.idle_seconds,
                      long_idle_seconds=args.long_idle_seconds, include_long_idle=args.include_long_idle,
                      metric_points=args.metric_points, network_delay_ms=args.network_delay_ms,
                      floor_sessions=args.floor_sessions, floor_frames=args.floor_frames,
                      campaign_id=args.campaign_id, preflight_report=args.preflight_report,
                      observer_policy=args.observer_policy, comparison=args.comparison, pool_id=args.pool_id, pool_chunk=args.pool_chunk, pool_spec=args.pool_spec, decoder_report=args.decoder_report)
    try:
        if args.mode == 'campaign-decode':
            if not args.report or not args.quiet_window or not args.campaign_id or not args.diagnostic_plan:
                parser.error('campaign-decode requires complete --report/--diagnostic-plan and its own fresh bounded --quiet-window/--campaign-id')
            with QuietWindow(args.quiet_window, args.budget_seconds, campaign_id=args.campaign_id) as guard:
                result = campaign_diagnostics(args.report, args.out, guard, repetitions=args.decoder_repetitions, plan_path=args.diagnostic_plan)
            return 0 if result['valid'] else 1
        if args.mode == 'interpret':
            if not args.report or not args.decoder_report:
                parser.error('interpret requires original --report and matching actual campaign --decoder-report')
            with FunctionalOwnership(LOCK):
                result = interpret_campaign(args.report, args.decoder_report, args.out, budget_seconds=args.budget_seconds)
            return 0 if result['valid'] else 1
        if args.mode == 'diagnostic-plan':
            if not args.report:
                parser.error('diagnostic-plan requires original --report')
            with FunctionalOwnership(LOCK):
                result = diagnostic_plan(args.report, args.out, repetitions=args.decoder_repetitions, budget_seconds=args.budget_seconds)
            return 0 if result['valid'] else 1
        if args.mode == 'pool-plan':
            with FunctionalOwnership(LOCK):
                write_json(args.out, pool_definition(options))
            return 0
        if args.mode == 'pool':
            with FunctionalOwnership(LOCK):
                result = pool_reports(args.pool_spec, args.chunk_report, args.out, budget_seconds=args.budget_seconds)
            return 0 if result['valid'] else 1
        if args.mode == 'feasibility':
            with FunctionalOwnership(LOCK):
                write_json(args.out, campaign_feasibility(args.preflight_report, options))
            return 0
        if args.mode == 'decode':
            if not args.quiet_window or not args.campaign_id:
                parser.error('decode requires dedicated bounded quiet agreement and campaign_id')
            with QuietWindow(args.quiet_window, args.budget_seconds, campaign_id=args.campaign_id) as guard:
                report = decoder_diagnostics(args.preflight_report, args.out, options, guard, repetitions=args.decoder_repetitions)
            return 0 if report['valid'] else 1
        if args.mode == 'plan':
            write_json(args.out, plan(options))
            return 0
        if args.mode == 'preflight':
            if not args.reference or not args.candidate:
                parser.error('preflight requires both actual binaries')
            report = functional_preflight(args.reference, args.candidate, args.out, options, reference_meta=args.reference_metadata, candidate_meta=args.candidate_metadata)
            return 0 if report['valid'] else 1
        if args.mode == 'reduce':
            if not args.report:
                parser.error('reduce requires --report')
            saved = json.loads(args.report.read_text())
            declared = saved['definition']['options']
            declared.update(themes=tuple(declared['themes']), sizes=tuple(tuple(s) for s in declared['sizes']),
                            case_filters=tuple(declared['case_filters']))
            options = Options(**declared)
            write_json(args.out, {'status': saved['status'], 'summary': summaries(saved, options, pooled=bool(options.pool_id))})
            return 0 if saved['status'] == 'complete' else 1
        if not all((args.reference, args.candidate, args.reference_metadata, args.candidate_metadata, args.quiet_window)):
            parser.error('run requires both binaries/build metadata and --quiet-window; no implicit authorization')
        report = run_campaign(args.reference, args.candidate, args.reference_metadata, args.candidate_metadata,
                              args.out, args.quiet_window, options)
        return 0 if report['status'] == 'complete' else 1
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
