#!/usr/bin/env python3
"""Deterministic functional input/effect corpus; export does not run a campaign."""
import argparse
import copy
import json
from pathlib import Path
import urllib.parse
import phase2

START = '2024-01-01T00:00:00Z'
END = '2024-01-01T01:00:00Z'


def ready(name, *contains, **wait):
    return {'checkpoint': name, 'wait': {'contains': list(contains), **wait}}


def key(value, *contains):
    return {'key': value, 'wait': {'contains': list(contains)}}


def send(value, *contains):
    return {'send': value, 'wait': {'contains': list(contains)}}


def route(method, path, body, **options):
    return phase2.response(method, path, body, **options)


def filters():
    return [{'version': '1', 'user_id': 'harness', 'stream_name': 'logs',
             'filter_name': name, 'filter_id': str(i),
             'query': {'filter_type': 'sql', 'filter_query': query}, 'time_filter': {}}
            for i, (name, query) in enumerate((('Fixture query', 'SELECT message FROM logs'),
                                              ('Unicode café 日志', 'SELECT * FROM logs LIMIT 2')))]


def base_fixture(rows=8, series=2, delay_ms=0):
    sql_rows = [{'p_timestamp': '2024-01-01T00:00:00Z', 'sequence': i,
                 'message': 'café 日志 é 界 ' + str(i)} for i in range(rows)]
    matrix = {'status': 'success', 'data': {'resultType': 'matrix', 'result':
              [{'metric': {'__name__': 'fixture_metric', 'job': 'café-' + str(i)},
                'values': [[1704067200 + j * 60, str(i + j)] for j in range(8)]}
               for i in range(series)]}}
    vector = {'status': 'success', 'data': {'resultType': 'vector', 'result':
              [{'metric': {'__name__': 'fixture_metric', 'job': 'café'}, 'value': [1704070800, '3']}]}}
    return {'routes': [route('GET', '/api/prism/v1/home', {'datasets': [
                {'title': 'logs', 'name': 'logs', 'datasetType': 'logs'},
                {'title': 'metrics', 'name': 'metrics', 'datasetType': 'metrics'}]}),
            route('POST', '/api/v1/query', {'fields': ['p_timestamp', 'sequence', 'message'],
                                          'records': sql_rows}, query={'fields': ['true']}, delay_ms=delay_ms),
            route('POST', '/api/v1/query', sql_rows, delay_ms=delay_ms),
            route('GET', '/api/v1/logstream/logs/schema', {'fields': [
                {'name': 'p_timestamp', 'data_type': 'Timestamp(Millisecond, None)', 'nullable': False},
                {'name': 'sequence', 'data_type': 'Int64', 'nullable': False},
                {'name': 'message', 'data_type': 'Utf8', 'nullable': True}]}),
            route('GET', '/api/v1/filters', filters()),
            route('GET', '/prometheus/api/v1/query_range', matrix, delay_ms=delay_ms),
            route('GET', '/prometheus/api/v1/query', vector, delay_ms=delay_ms),
            route('GET', '/prometheus/api/v1/label/__name__/values', {'status': 'success', 'data': ['fixture_metric']}),
            route('GET', '/prometheus/api/v1/labels', {'status': 'success', 'data': ['__name__', 'job']}),
            route('GET', '/prometheus/api/v1/label/job/values', {'status': 'success', 'data': ['café']})]}


def promql_gap_cases():
    """Bounded visible G3/G4/G7/G10 controls, pinned to the Go process oracle."""
    cases = []
    def add(gap, fixture, actions, *, start=START, end='2024-01-01T00:10:00Z', instant=False, **extra):
        cases.append({'name': 'p7-promql-' + gap, 'argv': ['promql', 'run', 'up', '-i',
            '--dataset', 'metrics', '--from', start, '--to', end, '--step', '60s'] +
            (['--instant'] if instant else []), 'size': [100, 30], 'config': {'auth': 'basic'},
            'fixture': fixture, 'actions': actions, 'expect': {'termination': {'exit': 0},
            'config_unchanged': True}, 'contract_gap': gap.split('-')[0].upper(), **extra})
    def result_fixture(body, instant=False):
        fixture = base_fixture()
        path = '/prometheus/api/v1/query' + ('' if instant else '_range')
        next(r for r in fixture['routes'] if r['path'] == path)['body'] = \
            body if isinstance(body, str) else json.dumps(body)
        return fixture
    def matrix(series):
        return {'status': 'success', 'data': {'resultType': 'matrix', 'result': series}}
    for label, offset in (('z', 'Z'), ('fixed-zone', '+05:30')):
        zone = '+00:00' if offset == 'Z' else offset
        start, end = '2024-01-01T00:00:00' + offset, '2024-01-01T00:10:00' + offset
        fixture = result_fixture(matrix([{'metric': {'__name__': 'up', 'job': 'x'},
            'values': [[1704067200, '1'], [1704067260, '2']]}]))
        add('g4-' + label, fixture, [ready('time-location', 'Chart View',
            '01 Jan 2024, 00:00 | UTC' + zone, '01 Jan 2024, 00:10 | UTC' + zone, alternate=True),
            key('ctrl-q'), key('tab'), key('enter'), ready('picker-location', 'QUERY TIME',
            '2024-Jan-01 00:00:00', '2024-Jan-01 00:10:00', 'Local UTC' + zone),
            key('esc'), key('ctrl-c')], start=start, end=end, env={'TZ': 'America/New_York'})
        cases[-1]['size'] = [140, 35]
        cases[-1]['expect']['request_checks'] = [{'method': 'GET',
            'path': '/prometheus/api/v1/query_range', 'count': 1, 'query': {
                'start': ['2024-01-01T00:00:00Z' if label == 'z' else '2023-12-31T18:30:00Z'],
                'end': ['2024-01-01T00:10:00Z' if label == 'z' else '2023-12-31T18:40:00Z']}}]
    merged = [{'metric': {'__name__': name, 'job': 'x'},
        'values': [[1704067200 + 60*i, value] for i in range(7)]}
        for name, value in (('a', '1'), ('b', '100'))]
    add('g10-equal-time-merge', result_fixture(matrix(merged)),
        [ready('merged-chart', 'Chart View', 'job="x"', alternate=True),
         key('ctrl-q'), key('shift-tab'), key('right'),
         ready('first-right', '12:00 AM, Jan 1, 2024', '● value 1 '), key('right'),
         ready('second-right-control', '12:01 AM, Jan 1, 2024', '● value 1 '), key('ctrl-c')],
        end='2024-01-01T00:06:00Z')
    cases[-1]['expect']['checkpoint_cells'] = [
        {'checkpoint': 'first-right', 'row': 21, 'col': 5, 'data': '●'}]
    inf = matrix([{'metric': {'__name__': 'up', 'job': 'x'},
        'values': [[1704067200, '1'], [1704067260, '+Inf'], [1704067320, '2']]}])
    add('g7-infinite-plot-amd64', result_fixture(inf),
        [ready('infinite-chart', 'Chart View', 'job="x"', alternate=True),
         key('ctrl-q'), key('shift-tab'), key('right'),
         ready('infinite-inspector', '12:01 AM, Jan 1, 2024', '● value +InfM'), key('ctrl-c')],
        end='2024-01-01T00:02:00Z', oracle_platform='linux/amd64')
    cases[-1]['expect']['checkpoint_cells'] = [
        {'checkpoint': 'infinite-inspector', 'row': 21, 'col': 51, 'data': '●'}]
    huge = {'status': 'success', 'data': {'resultType': 'vector', 'result': [
        {'metric': {'__name__': 'up'}, 'value': [1e30, '1']}]}}
    add('g7-huge-timestamp-amd64', result_fixture(huge, True),
        [ready('overflow-timestamp', 'Table View', '15:30:08', alternate=True), key('ctrl-c')],
        instant=True, oracle_platform='linux/amd64')
    # This must stay raw: a Python dict/json round-trip discards duplicate keys.
    duplicate = ('{"status":"success","data":{"resultType":"vector",'
        '"result":[{"metric":{"__name__":"up","job":"a"},"value":[1704067200,"1"]}],'
        '"result":[{"metric":{"instance":"i"},"value":[1704067200,"3"]}]}}')
    add('g3-duplicate-result-merge', result_fixture(duplicate, True),
        [ready('merged-metric-table', 'Table View', 'up{instance="i", job="a"}', alternate=True), key('ctrl-c')],
        instant=True)
    cases[-1]['expect']['checkpoint_cells'] = [
        {'checkpoint': 'merged-metric-table', 'row': 17, 'col': 83, 'data': '3'}]
    return cases


def corpus(themes=('dark',)):
    cases = []
    def add(name, argv, actions, *, config=None, fixture=None, expect=None, documentary=False, **extra):
        cases.append({'name': 'p7-' + name, 'argv': argv, 'actions': actions,
                      'config': config, 'fixture': fixture, 'expect': expect or {},
                      'documentary': documentary, **extra})
    unchanged = {'termination': {'exit': 0}, 'requests': [], 'config_unchanged': True}
    add('login-cancel', ['login'], [ready('deployment', 'PARSEABLE LOGIN', 'CONNECT TO'), key('ctrl-c')],
        expect=unchanged)
    add('login-backtrack', ['login'], [ready('deployment', 'CONNECT TO'), key('enter'),
        ready('url', 'SERVER URL'), key('esc'), ready('back', 'CONNECT TO'), key('ctrl-c')], expect=unchanged)
    basic_login = [ready('deployment', 'CONNECT TO'), key('enter'), ready('url', 'SERVER URL'),
                   send('http://127.0.0.1:1'), key('enter'), ready('auth', 'AUTHENTICATION'),
                   key('enter'), ready('username', 'USERNAME'), send('dummy-user'), key('enter'),
                   ready('password', 'PASSWORD'), send('dummy-password'),
                   ready('masked-password', '•'), key('enter'), ready('name', 'PROFILE NAME')]
    for label, config, finish, default in (
            ('basic-save', None, [key('enter')], 'default'),
            ('overwrite-bad-config', {'toml': '[Profiles\n'}, [key('enter')], 'default'),
            ('duplicate-replace', {'toml': phase2.config_toml({'default': phase2.profile()}, default='default')},
             [key('enter'), ready('duplicate', 'already exists', 'Replace it'), key('enter')], 'default'),
            ('duplicate-change-name', {'toml': phase2.config_toml({'default': phase2.profile()}, default='default')},
             [key('enter'), ready('duplicate', 'already exists'), key('down'), key('enter'),
              ready('rename', 'PROFILE NAME'), key('ctrl-u'), send('café-日志'), key('enter')], 'café-日志')):
        add('login-' + label, ['login'], basic_login + finish, config=config,
            masked_values=['dummy-password'], expect={'termination': {'exit': 0}, 'requests': [],
            'default': default, 'profiles': {default: {'url': 'http://127.0.0.1:1',
                                                     'username': 'dummy-user', 'password': 'dummy-password'}}})
    add('login-api-key-save', ['login'], [ready('deployment', 'CONNECT TO'), key('enter'),
        ready('url', 'SERVER URL'), send('http://127.0.0.1:1'), key('enter'), ready('auth', 'AUTHENTICATION'),
        key('down'), key('enter'), ready('api-key', 'API KEY'), send('fixture-secret'), ready('masked-key', '•'),
        key('enter'), ready('name', 'PROFILE NAME'), key('enter')], masked_values=['fixture-secret'],
        expect={'termination': {'exit': 0}, 'requests': [], 'default': 'default',
                'profiles': {'default': {'apiKey': 'fixture-secret', 'username': '', 'password': ''}}})
    add('login-required-url', ['login'], [ready('deployment', 'CONNECT TO'), key('enter'),
        ready('url', 'SERVER URL'), key('enter'), ready('url-error', 'Server URL is required'), key('ctrl-c')],
        expect=unchanged)
    cloud_choose = [ready('deployment', 'CONNECT TO'), key('down'), key('enter'),
                    ready('cloud-auth', 'CLOUD AUTHENTICATION', 'Device login (browser)', 'API key')]
    add('login-cloud-cancel', ['login'], cloud_choose + [key('ctrl-c')], build='cloud', expect=unchanged)
    for sig in ('SIGINT', 'SIGTERM', 'SIGHUP', 'SIGQUIT'):
        add('login-signal-' + sig.lower(), ['login'], [ready('deployment', 'CONNECT TO'), {'signal': sig}],
            documentary=True, expect={'requests': [], 'config_unchanged': True,
                                      'restored': False, 'screen_restored': False})
        if sig == 'SIGQUIT':
            cases[-1].update(documentary=False, runtime_sigquit_tail=True)
            cases[-1]['expect'].update(termination={'exit': 2}, termios_restored=False,
                alternate=False, cursor_visible=False, stderr_prefix='SIGQUIT: quit\n')
    for entry, argv, config in (('login', ['login'], None),
            ('sql', ['sql', 'run', '-i'], {'auth': 'basic'}),
            ('promql', ['promql', 'run', '-i'], {'auth': 'basic'}),
            ('credentials', ['profile', 'add', 'new', 'http://127.0.0.1:1'], None),
            ('saved', ['sql', 'list'], {'auth': 'basic'})):
        add('nonTTY-' + entry, argv, [], io='pipe', config=config,
            fixture=base_fixture() if entry == 'saved' else None, documentary=True,
            expect={'restored': True, 'screen_restored': False, 'config_unchanged': entry != 'credentials'})
    creds = ['profile', 'add', 'new', 'http://127.0.0.1:1', 'ignored-positional']
    for cancel in ('esc', 'ctrl-c'):
        add('credential-empty-' + cancel, creds, [ready('credentials', 'username', 'password'), key(cancel)],
            expect={'termination': {'exit': 0}, 'requests': [], 'default': 'new',
                    'profiles': {'new': {'username': '', 'password': ''}}})
    add('credential-partial-cancel', creds, [ready('credentials', 'username'), send('dummy-user'),
        ready('partial', 'dummy-user'), key('ctrl-c')], expect={'termination': {'exit': 0}, 'requests': [],
        'default': 'new', 'profiles': {'new': {'username': '', 'password': ''}}})
    add('credential-valid-cancel', creds, [ready('credentials', 'username'), send('dummy-user'), key('tab'),
        send('dummy-password'), ready('masked', 'dummy-user', '•'), key('ctrl-c')],
        masked_values=['dummy-password'], expect={'termination': {'exit': 0}, 'requests': [],
        'default': 'new', 'profiles': {'new': {'username': 'dummy-user', 'password': 'dummy-password'}}})
    profile_config = {'toml': phase2.config_toml({'fixture': phase2.profile()})}
    add('default-cancel', ['profile', 'default'], [ready('picker', 'fixture'), key('ctrl-c')],
        config=profile_config, expect=unchanged)
    add('default-select', ['profile', 'default'], [ready('picker', 'fixture'), key('enter')],
        config=profile_config, expect={'termination': {'exit': 0}, 'requests': [], 'default': 'fixture'})
    multi = {'toml': phase2.config_toml({name: phase2.profile() for name in ('fixture', 'alpha', 'beta')})}
    for entry, argv, actions in (
            ('remove', ['profile', 'remove', 'fixture'], []),
            ('logout', ['logout'], [ready('confirm', 'Continue? [y/N]:', cooked=True), send('y\n')])):
        add(entry + '-replacement-cancel', argv, actions +
            [send('/', 'alpha', 'beta'), *[send(c) for c in 'alph'],
             ready('filter-draft', 'Filter: alph', 'alpha', 'beta'), key('ctrl-c')],
            config=multi, expect={'termination': {'exit': 0}, 'requests': [], 'default': '',
                                 'profiles_absent': ['fixture']}, documentary=True,
            documentary_reason='Go map-derived profile order; retain exact cell mismatches outside parity counts')
    add('logout-decline', ['logout'], [ready('cooked-confirm', 'Continue? [y/N]:', cooked=True), send('n\n')],
        config=profile_config, expect={**unchanged, 'stdout_contains': ['Logout canceled']})
    add('logout-confirm', ['logout'], [ready('cooked-confirm', 'Continue? [y/N]:', cooked=True), send('y\n')],
        config=profile_config, expect={'termination': {'exit': 0}, 'requests': [], 'default': '',
        'profiles_absent': ['fixture'], 'stdout_contains': ['Logged out and removed profile']})
    for cancel in ('esc', 'ctrl-c'):
        add('dataset-cancel-' + cancel, ['dataset', 'add', 'new'], [ready('picker', 'logs', 'metrics', 'traces'), key(cancel)],
            config={'auth': 'basic'}, fixture={'routes': []}, expect={**unchanged,
            'stdout_contains': ['Dataset creation canceled']})
    for choice, count in (('logs', 0), ('metrics', 1), ('traces', 2)):
        actions = [ready('picker', 'logs', 'metrics', 'traces')]
        actions += [key('down') for _ in range(count)]
        actions += [ready('selected', choice), key('enter')]
        add('dataset-' + choice, ['dataset', 'add', 'new'], actions, config={'auth': 'basic'},
            fixture={'routes': [route('PUT', '/api/v1/logstream/new', '')]},
            expect={'termination': {'exit': 0}, 'requests': ['PUT /api/v1/logstream/new'], 'config_unchanged': True,
                    'stdout_contains': ['Created', 'dataset'],
                    'request_checks': [{'method': 'PUT', 'path': '/api/v1/logstream/new',
                        'headers': {'x-p-telemetry-type': choice, **({'x-p-log-source': 'otel-' + choice}
                                                                  if choice != 'logs' else {})}}]})
    role_fixture = {'routes': [route('GET', '/api/v1/roles', {}), route('PUT', '/api/v1/role/new', '')]}
    add('role-cancel', ['role', 'add', 'new'], [ready('privilege', 'admin', 'Submit'), key('ctrl-c')],
        config={'auth': 'basic'}, fixture=role_fixture, expect={'termination': {'exit': 0},
        'requests': ['GET /api/v1/roles'], 'stdout_contains': ['aborted by user'], 'config_unchanged': True})
    add('role-reader', ['role', 'add', 'new'], [ready('privilege', 'admin'), key('right'), key('right'),
        key('right'), ready('reader', 'reader'), key('tab'), ready('submit', 'Submit'), key('enter')],
        config={'auth': 'basic'}, fixture=role_fixture, expect={'termination': {'exit': 0},
        'requests': ['GET /api/v1/roles', 'PUT /api/v1/role/new'], 'config_unchanged': True})
    cases[-1]['expect']['request_checks'] = [{'method': 'PUT', 'path': '/api/v1/role/new',
                                             'body': [{'privilege': 'reader'}]}]
    add('role-conflict-before-prompt', ['role', 'add', 'new'], [], config={'auth': 'basic'},
        fixture={'routes': [route('GET', '/api/v1/roles', {'new': []})]},
        expect={'termination': {'exit': 1}, 'requests': ['GET /api/v1/roles'], 'config_unchanged': True,
                'stderr_contains': ['already exists']})
    for entry, argv, title in (('sql', ['sql', 'run', '-i'], 'SQL'),
                              ('promql', ['promql', 'run', '-i'], 'RESULTS')):
        # Fixed absolute ranges gate styled frames; absent-from defaults are
        # separately characterized, because their displayed clock is not frozen.
        argv = [*argv, '--from', START, '--to', END]
        add(entry + '-empty-quit', argv, [ready('empty', title, alternate=True), key('ctrl-c')],
            config={'auth': 'basic'}, fixture=base_fixture(), expect={'termination': {'exit': 0}, 'config_unchanged': True})
        for sig in ('SIGINT', 'SIGTERM', 'SIGQUIT'):
            add(entry + '-signal-' + sig.lower(), argv, [ready('empty', title, alternate=True), {'signal': sig}],
                config={'auth': 'basic'}, fixture=base_fixture(), documentary=True,
                expect={'restored': False, 'screen_restored': False, 'config_unchanged': True})
            if sig == 'SIGQUIT':
                cases[-1].update(documentary=False, runtime_sigquit_tail=True)
                cases[-1]['expect'].update(termination={'exit': 2}, termios_restored=False,
                    alternate=True, cursor_visible=False, stderr_prefix='SIGQUIT: quit\n')
        add(entry + '-resize', argv, [ready('normal', title, alternate=True), {'resize': [65, 20]},
            ready('small', title), {'resize': [140, 45]}, ready('large', title), key('ctrl-c')],
            config={'auth': 'basic'}, fixture=base_fixture(), expect={'termination': {'exit': 0}, 'config_unchanged': True})
    for quit_key in ('ctrl-c',):
        add('saved-quit-' + quit_key, ['sql', 'list'], [ready('menu', 'SAVED QUERIES', 'Fixture query', alternate=True),
            key(quit_key)], config={'auth': 'basic'}, fixture=base_fixture(), expect={'termination': {'exit': 0},
            'requests': ['GET /api/v1/filters'], 'config_unchanged': True})
    for shortcut in ('a', 'b', 'enter'):
        actions = [ready('menu', 'SAVED QUERIES', 'Fixture query', alternate=True), send('/'),
                   ready('filter', 'Filter'), key(shortcut) if shortcut == 'enter' else send(shortcut)]
        applies = shortcut != 'b'
        add('saved-filter-' + shortcut, ['sql', 'list'], actions, config={'auth': 'basic'}, fixture=base_fixture(),
            expect={'termination': {'exit': 0}, 'requests': ['GET /api/v1/filters'] +
                    (['POST /api/v1/query'] if applies else []), 'config_unchanged': True,
                    **({'stdout_contains': ['Query:']} if applies else {})})
    for status, body in ((503, filters()), (200, '{'), (200, [])):
        label = 'non2xx' if status == 503 else 'malformed' if body == '{' else 'empty'
        fixture = {'routes': [route('GET', '/api/v1/filters', body, status=status)]}
        add('saved-' + label, ['sql', 'list'], [ready('menu', 'SAVED QUERIES', alternate=True), key('ctrl-c')],
            config={'auth': 'basic'}, fixture=fixture, expect={'termination': {'exit': 0},
            'requests': ['GET /api/v1/filters'], 'config_unchanged': True})
    for label, argv in (
            ('short-empty', ['sql', 'run', '-i']),
            ('short-query', ['sql', 'run', 'SELECT * FROM logs LIMIT 8', '-i']),
            ('long-empty', ['sql', 'run', '--interactive']),
            ('long-true-query', ['sql', 'run', 'SELECT * FROM logs LIMIT 8', '--interactive=true']),
            ('ignored-yaml', ['sql', 'run', 'SELECT * FROM logs LIMIT 8', '-i', '-o', 'yaml']),
            ('ignored-save-as', ['sql', 'run', 'SELECT * FROM logs LIMIT 8', '-i', '--save-as', 'unused']),
            ('compact-json', ['sql', 'run', 'SELECT * FROM logs LIMIT 8', '-i', '-ojson'])):
        has_query = 'SELECT * FROM logs LIMIT 8' in argv
        add('sql-promoted-' + label, [*argv, '--from', START, '--to', END],
            [ready('useful', 'RESULTS', *(('café',) if has_query else ('EDITOR',)), alternate=True,
                   request_paths=['/api/prism/v1/home', '/api/v1/logstream/logs/schema'] +
                   (['/api/v1/query'] if has_query else [])), key('ctrl-c')],
            config={'auth': 'basic'}, fixture=base_fixture(), expect={'termination': {'exit': 0},
            'config_unchanged': True}, promoted_guards=['phase3 SQL interactive', 'phase4 SQL interactive'])
    for label, flags in (('short', ['-i']), ('compact-json', ['-i', '-ojson']),
                         ('long-true', ['--interactive=true'])):
        add('promql-promoted-' + label, ['promql', 'run', 'fixture_metric', '--dataset', 'metrics',
             '--from', START, '--to', END, '--step', '60s', *flags],
            [ready('useful', 'RESULTS', 'café', alternate=True, request_paths=['/prometheus/api/v1/query_range']),
             key('ctrl-c')], config={'auth': 'basic'}, fixture=base_fixture(),
            expect={'termination': {'exit': 0}, 'config_unchanged': True},
            promoted_guards=['phase4 PromQL interactive'])
    sql_empty = ['sql', 'run', '-i', '--from', START, '--to', END]
    add('sql-dataset-placeholder', sql_empty, [ready('empty', 'EDITOR', alternate=True), key('ctrl-d'),
        ready('dataset-picker', 'logs', 'filter'), key('enter'), ready('inserted', 'SELECT * FROM dataset LIMIT 100'),
        key('ctrl-r'), ready('result', 'café', request_paths=['/api/v1/query']), key('ctrl-c')],
        config={'auth': 'basic'}, fixture=base_fixture(), expect={'termination': {'exit': 0}, 'config_unchanged': True})
    add('sql-editor-key-precedence', sql_empty, [ready('empty', 'EDITOR', alternate=True), send('SELECT 日志'),
        ready('typed', 'SELECT 日志'), key('ctrl-b'), ready('ctrl-b-intercept', 'SELECT 日志'), key('ctrl-d'),
        ready('ctrl-d-overlay', 'filter datasets'), key('esc'), ready('overlay-canceled', 'SELECT 日志'), key('ctrl-c')],
        config={'auth': 'basic'}, fixture=base_fixture(), expect={'termination': {'exit': 0}, 'config_unchanged': True})
    add('sql-time-overlay-cancel', sql_empty, [ready('empty', 'TIME RANGE', alternate=True), key('tab'),
        key('enter'), ready('time-picker', 'QUERY TIME', 'PRESETS'), key('down'), key('esc'), ready('committed', 'TIME RANGE'),
        key('ctrl-c')], config={'auth': 'basic'}, fixture=base_fixture(),
        expect={'termination': {'exit': 0}, 'config_unchanged': True})
    windows = base_fixture()
    windows['routes'] = [r for r in windows['routes'] if r['path'] != '/api/v1/query']
    window_bodies = []
    for offset, count in ((0, 500), (500, 150)):
        window_bodies.append({'query': f'SELECT * FROM logs LIMIT {count} OFFSET {offset}',
                              'startTime': START, 'endTime': END})
        windows['routes'].append(route('POST', '/api/v1/query',
            {'fields': ['sequence', 'message'], 'records': [{'sequence': offset + i,
             'message': f'window-{offset}-café-{i}'} for i in range(count)]},
             body_contains=f'OFFSET {offset}'))
    add('sql-window-partial-final', ['sql', 'run', 'SELECT * FROM logs LIMIT 650', '-i',
         '--from', START, '--to', END], [ready('first-window', 'window-0-café', alternate=True,
         request_paths=['/api/prism/v1/home', '/api/v1/logstream/logs/schema', '/api/v1/query']),
         key('ctrl-v'), ready('partial-final-window', 'window-500-café'), key('ctrl-y'),
         ready('cached-first-window', 'window-0-café'), key('ctrl-c')], config={'auth': 'basic'}, fixture=windows,
         expect={'termination': {'exit': 0}, 'config_unchanged': True,
                 'request_checks': [{'method': 'POST', 'path': '/api/v1/query', 'count': 2, 'bodies': window_bodies}]})
    add('promql-builder', ['promql', 'run', '-i', '--dataset', 'metrics', '--from', START, '--to', END],
        [ready('empty', 'RESULTS', alternate=True), key('ctrl-b'), key('enter'),
         ready('builder', 'fixture_metric'), key('esc'), key('ctrl-q'), key('ctrl-c')],
        config={'auth': 'basic'}, fixture=base_fixture(), expect={'termination': {'exit': 0}, 'config_unchanged': True})
    add('promql-table-chart-unwrap', ['promql', 'run', 'fixture_metric', '-i', '--dataset', 'metrics',
        '--from', START, '--to', END, '--step', '60s'], [ready('chart', 'Chart View', 'café', alternate=True,
        request_paths=['/prometheus/api/v1/query_range']), key('ctrl-q'), key('shift-tab'), send('t'),
        ready('table', 'Table View', 'café'), send('w'), ready('unwrap', 'Table View'), send('t'),
        ready('chart-again', 'Chart View'), key('right'), ready('cursor', 'Chart View'), key('ctrl-c')],
        config={'auth': 'basic'}, fixture=base_fixture(), expect={'termination': {'exit': 0}, 'config_unchanged': True})
    add('saved-empty-output', ['sql', 'list', '-o', ''],
        [ready('menu', 'SAVED QUERIES', 'Fixture query', alternate=True), key('ctrl-c')],
        config={'auth': 'basic'}, fixture=base_fixture(), expect={'termination': {'exit': 0},
        'requests': ['GET /api/v1/filters'], 'config_unchanged': True}, promoted_guards=['phase1 sql list', 'phase4 saved picker'])
    clipboard = {'text': 'clip-café\n日志'}
    login_to_user = basic_login[:8]  # Ends on the USERNAME checkpoint.
    add('clipboard-login', ['login'], login_to_user + [key('ctrl-v'), ready('paste', 'clip-café', '日志'), key('ctrl-c')],
        clipboard=clipboard, expect={**unchanged, 'clipboard_reads': 1, 'children_reaped': True})
    add('clipboard-credentials', creds, [ready('credentials', 'username'), key('ctrl-v'),
        ready('paste', 'clip-café', '日志'), key('ctrl-c')], clipboard=clipboard,
        expect={'termination': {'exit': 0}, 'requests': [], 'clipboard_reads': 1, 'children_reaped': True,
                'profiles': {'new': {'username': '', 'password': ''}}, 'default': 'new'})
    for entry, argv in (('sql', sql_empty), ('promql', ['promql', 'run', '-i', '--dataset', 'metrics',
                                                     '--from', START, '--to', END])):
        add('clipboard-' + entry, argv, [ready('empty', 'EDITOR', alternate=True), key('ctrl-v'),
            ready('paste-result-dropped', 'Write your queries here', absent=['clip-café', '日志'], clipboard_completed=1),
            key('ctrl-c')], clipboard=clipboard,
            config={'auth': 'basic'}, fixture=base_fixture(),
            expect={'termination': {'exit': 0}, 'clipboard_reads': 1, 'children_reaped': True, 'config_unchanged': True})
    add('clipboard-saved-filter', ['sql', 'list'], [ready('menu', 'SAVED QUERIES', alternate=True), send('/'),
        key('ctrl-v'), ready('paste-filter', 'clip-café', '日志'), send('b')], clipboard=clipboard,
        config={'auth': 'basic'}, fixture=base_fixture(), expect={'termination': {'exit': 0},
        'requests': ['GET /api/v1/filters'], 'clipboard_reads': 1, 'children_reaped': True, 'config_unchanged': True})
    add('clipboard-profile-picker-dropped', ['profile', 'default'], [ready('picker', 'fixture'), send('/'),
        key('ctrl-v'), ready('dropped-paste', 'Filter:', 'fixture', absent=['clip-café', '日志'], clipboard_completed=1),
        key('ctrl-c')], clipboard=clipboard, config=profile_config,
        expect={**unchanged, 'clipboard_reads': 1, 'children_reaped': True})
    add('clipboard-delayed-focus', creds, [ready('credentials', 'username'), key('ctrl-v'), key('tab'),
        ready('delivered-to-password', '•', absent=['clip-café', '日志'], clipboard_completed=1), key('ctrl-c')],
        clipboard={**clipboard, 'delay_ms': 600}, config=None, masked_values=['clip-café'],
        expect={'termination': {'exit': 0}, 'requests': [], 'clipboard_reads': 1, 'children_reaped': True,
                'default': 'new', 'profiles': {'new': {'username': '', 'password': ''}}})
    add('clipboard-delayed-quit', ['login'], login_to_user + [key('ctrl-v'), key('ctrl-c')],
        clipboard={**clipboard, 'delay_ms': 2000}, expect={**unchanged, 'clipboard_reads': 1},
        documentary=True, documentary_reason='Go exits before helper completion; record child state before observer cleanup')
    for entry in ('sql', 'promql'):
        for kind in ('empty', 'error'):
            fixture = base_fixture()
            path = '/api/v1/query' if entry == 'sql' else '/prometheus/api/v1/query_range'
            for r in fixture['routes']:
                if r['path'] != path:
                    continue
                r['body'] = ('fixture query failed' if kind == 'error' else
                    json.dumps({'fields': ['message'], 'records': []}) if entry == 'sql' else
                    json.dumps({'status': 'success', 'data': {'resultType': 'matrix', 'result': []}}))
                r['status'] = 503 if kind == 'error' else 200
            argv = (['sql', 'run', 'SELECT * FROM logs LIMIT 8', '-i'] if entry == 'sql' else
                    ['promql', 'run', 'fixture_metric', '-i', '--dataset', 'metrics'])
            add(entry + '-result-' + kind, [*argv, '--from', START, '--to', END],
                [ready('useful-' + kind, 'fixture query failed' if kind == 'error' else 'no results for this query',
                       alternate=True), key('ctrl-c')], config={'auth': 'basic'}, fixture=fixture,
                expect={'termination': {'exit': 0}, 'config_unchanged': True})
    slow_sql = base_fixture()
    slow_sql['routes'] = [r for r in slow_sql['routes'] if r['path'] != '/api/v1/query']
    for label, barrier in (('slow', 'old-window'), ('fast', None)):
        slow_sql['routes'].append(route('POST', '/api/v1/query',
            {'fields': ['message'], 'records': [{'message': label + '-window-value'}]},
            body_contains=label, **({'header_barrier': barrier} if barrier else {})))
    replace_query = [key('ctrl-a'), key('ctrl-k'), send('SELECT fast FROM logs LIMIT 1'), key('ctrl-r')]
    add('sql-delayed-window-guard', ['sql', 'run', 'SELECT slow FROM logs LIMIT 1', '-i', '--from', START, '--to', END],
        [ready('old-request-pending', 'fetching...', requests=3), *replace_query,
         ready('new-window', 'fast-window-value'), {'release': 'old-window'},
         ready('old-window-rejected', 'fast-window-value', absent=['slow-window-value']), key('ctrl-c')],
        config={'auth': 'basic'}, fixture=slow_sql, loading_spinner_checkpoints=['old-request-pending'],
        expect={'termination': {'exit': 0}, 'config_unchanged': True,
        'request_checks': [{'method': 'POST', 'path': '/api/v1/query', 'count': 2}]})
    slow_promql = base_fixture()
    slow_promql['routes'] = [r for r in slow_promql['routes'] if r['path'] != '/prometheus/api/v1/query_range']
    for label, barrier in (('slow', 'old-result'), ('fast', None)):
        slow_promql['routes'].append(route('GET', '/prometheus/api/v1/query_range',
            {'status': 'success', 'data': {'resultType': 'matrix', 'result': [
             {'metric': {'__name__': label, 'job': label + '-result-value'}, 'values': [[1704067200, '1'], [1704067260, '2']]}]}},
             query={'query': [label]}, **({'header_barrier': barrier} if barrier else {})))
    add('promql-delayed-result-unguarded', ['promql', 'run', 'slow', '-i', '--dataset', 'metrics',
        '--from', START, '--to', END, '--step', '60s'], [ready('old-request-pending', 'fetching...', requests=3),
        key('ctrl-q'), key('ctrl-a'), key('ctrl-k'), send('fast'), key('ctrl-r'),
        ready('new-result', 'fast-result-value'), {'release': 'old-result'},
        ready('old-result-overwrites', 'slow-result-value', absent=['fast-result-value']), key('ctrl-c')],
        config={'auth': 'basic'}, fixture=slow_promql, loading_spinner_checkpoints=['old-request-pending'],
        expect={'termination': {'exit': 0}, 'config_unchanged': True,
        'request_checks': [{'method': 'GET', 'path': '/prometheus/api/v1/query_range', 'count': 2}]})
    add('sql-alt-enter', sql_empty, [ready('empty', 'EDITOR', alternate=True), send('SELECT * FROM logs LIMIT 8'),
        ready('typed', 'SELECT * FROM logs LIMIT 8'), key('alt-enter'), ready('result', 'café'), key('ctrl-c')],
        config={'auth': 'basic'}, fixture=base_fixture(), expect={'termination': {'exit': 0}, 'config_unchanged': True,
        'request_checks': [{'method': 'POST', 'path': '/api/v1/query', 'count': 1}]})
    for entry, argv in (('sql', sql_empty), ('promql', ['promql', 'run', '-i', '--from', START, '--to', END])):
        held = {'alternate': True, 'cooked': False}
        add(entry + '-hang-double-sigterm', argv,
            [ready('empty-editor', 'EDITOR', **held), {'send': '\x1b[1;5D', 'stable_ms': 0},
             {**ready('hung-before-term', 'EDITOR', **held), 'stable_ms': 200},
             {'signal': 'SIGTERM', 'stable_ms': 0},
             {**ready('alive-after-first-term', 'EDITOR', unchanged_from='hung-before-term', **held), 'stable_ms': 200},
             {'signal': 'SIGTERM', 'stable_ms': 0},
             {**ready('alive-after-second-term', 'EDITOR', unchanged_from='hung-before-term', **held), 'stable_ms': 200},
             {'signal': 'SIGQUIT', 'stable_ms': 0}],
            config={'auth': 'basic'}, fixture=base_fixture(), runtime_sigquit_tail=True,
            lifecycle_review='N2 in docs/phase7-review-lifecycle-r1.md; blocked editor does not accept Quit',
            expect={'termination': {'exit': 2}, 'config_unchanged': True, 'restored': False,
                    'screen_restored': False, 'termios_restored': False, 'alternate': True,
                    'cursor_visible': False, 'stderr_prefix': 'SIGQUIT: quit\n',
                    'signal_sequence': [15, 15, 3], 'min_term_gap_ms': 150})
    wide = '测' * 20
    wide_sql = base_fixture()
    wide_sql['routes'][0]['body'] = json.dumps({'datasets': [{'title': wide, 'name': wide, 'datasetType': 'logs'}]})
    schema = next(r for r in wide_sql['routes'] if r['path'] == '/api/v1/logstream/logs/schema')
    schema['path'] = '/api/v1/logstream/' + wide + '/schema'
    add('sql-wide-dataset-truncation', sql_empty,
        [ready('wide-dataset-rendered', wide[:8], 'EDITOR', alternate=True), key('ctrl-c')],
        size=[176, 35], config={'auth': 'basic'}, fixture=wide_sql,
        initial_dataset_name=wide,
        expect={'termination': {'exit': 0}, 'config_unchanged': True,
                'stdout_absent': ['Caught panic:']},
        style_characterization='Sidebar valW33 slices [:32] within capacity32; NUL padding, not a panic')
    legend = base_fixture()
    result = next(r for r in legend['routes'] if r['path'] == '/prometheus/api/v1/query_range')
    result.update(header_barrier='legend-panic', body=json.dumps({'status': 'success', 'data': {
        'resultType': 'matrix', 'result': [{'metric': {'__name__': 'fixture_metric', 'job': wide},
        'values': [[1704067200, '1'], [1704067260, '2']]}]}}))
    add('promql-wide-legend-view-panic', ['promql', 'run', 'fixture_metric', '-i', '--dataset', 'metrics',
        '--from', START, '--to', END, '--step', '60s'],
        [ready('before-view-panic', 'fetching...', requests=3, alternate=True), {'release': 'legend-panic'}],
        size=[92, 35], config={'auth': 'basic'}, fixture=legend, runtime_view_panic_stack=True,
        expect={'termination': {'exit': 0}, 'config_unchanged': True,
                'stdout_contains': ['Caught panic:', 'runtime error: slice bounds out of range [:38] with capacity 32',
                                    'Restoring terminal...'],
                'alternate_final_contains': ['fetching...'], 'alternate_final_absent': [wide]})
    cases.extend(promql_gap_cases())
    result = []
    for theme in themes:
        for case in cases:
            case = copy.deepcopy(case)
            case.update(name=case['name'] + '-' + theme, theme=theme)
            if case['name'] in ('p7-promql-wide-legend-view-panic-dark', 'p7-promql-wide-legend-view-panic-light'):
                case['loading_spinner_checkpoints'] = ['before-view-panic']
            argv = case['argv']
            if argv[:2] == ['sql', 'run'] and case.get('io') != 'pipe':
                has_initial_query = any(arg.startswith('SELECT') for arg in argv)
                dataset = urllib.parse.quote(case.get('initial_dataset_name', 'logs'), safe='')
                schema_path = '/api/v1/logstream/' + dataset + '/schema'
                case['initial_fanout_members'] = ['GET /api/prism/v1/home', 'GET ' + schema_path] + \
                                                (['POST /api/v1/query'] if has_initial_query else [])
                case['initial_fanout_count'] = len(case['initial_fanout_members'])
                case['initial_fanout_constraints'] = [['GET /api/prism/v1/home', 'GET ' + schema_path]]
            if argv[:2] == ['promql', 'run'] and case.get('io') != 'pipe':
                configured = '--dataset' in argv
                has_query = len(argv) > 2 and not argv[2].startswith('-')
                members = ['GET /api/prism/v1/home', 'GET /prometheus/api/v1/label/__name__/values']
                if has_query:
                    members.append('GET /prometheus/api/v1/query' if '--instant' in argv else
                                   'GET /prometheus/api/v1/query_range')
                if not configured:
                    members.append('GET /prometheus/api/v1/label/__name__/values')
                    # Same endpoint, different streams: the auto-selected fetch
                    # must follow home discovery; initial select-dataset fetch
                    # is independent. This is validated at the full target URL.
                    case['initial_fanout_url_constraints'] = [
                        ['/api/prism/v1/home', '/prometheus/api/v1/label/__name__/values?stream=metrics']]
                case['initial_fanout_count'] = len(members)
                case['initial_fanout_members'] = members
            result.append(case)
    return result


def performance_workloads():
    """Definitions only; running requires a separately agreed coordinator quiet window."""
    cases = []
    for rows in (500, 1500, 20000):
        fixture = base_fixture(rows=0)
        fixture['routes'] = [r for r in fixture['routes'] if r['path'] != '/api/v1/query']
        for offset in range(0, rows, 500):
            count = min(500, rows - offset)
            fixture['routes'].append(route('POST', '/api/v1/query', {'fields': ['sequence', 'message'],
                'records': [{'sequence': offset + i, 'message': f'café 日志-{offset+i}'} for i in range(count)]},
                body_fields={'query': f'SELECT * FROM logs LIMIT {count} OFFSET {offset}'}))
        cases.append({'name': f'sql-scroll-{rows}', 'argv': ['sql', 'run', f'SELECT * FROM logs LIMIT {rows}', '-i',
            '--from', START, '--to', END], 'size': [120, 40], 'config': {'auth': 'basic'},
            'fixture': fixture, 'actions': [ready('result', 'RESULTS', 'café', alternate=True),
            *[key('page-down') for _ in range(30)], ready('scrolled', 'RESULTS'), key('ctrl-c')],
            'repetitions': 30, 'warmups': 5, 'unit': 'session', 'quiet_window_required': True})
    for series in (1, 100, 1000):
        cases.append({'name': f'promql-dense-{series}', 'argv': ['promql', 'run', 'fixture_metric', '-i',
            '--dataset', 'metrics', '--from', START, '--to', END, '--step', '60s'], 'size': [120, 40],
            'config': {'auth': 'basic'}, 'fixture': base_fixture(series=series),
            'actions': [ready('result', 'RESULTS', 'café', alternate=True), key('ctrl-q'), key('shift-tab'), send('t'),
                        ready('table', 'Table View'), key('ctrl-c')],
            'repetitions': 30, 'warmups': 5, 'unit': 'session', 'quiet_window_required': True})
    return cases


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'functional': corpus(('dark', 'light')),
                                  'performance_definitions': performance_workloads()}, indent=2) + '\n')
