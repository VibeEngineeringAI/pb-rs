//! Real-process interactive effects, independently exercised against Go and Rust.
//! Requires Linux PTYs and Python 3.11+ (standard library only).
//! The Python fixture is embedded so this suite needs no private-cache files.
#![cfg(target_os = "linux")]

use std::{path::PathBuf, process::Command};

fn exercise(case: &str) {
    let python = std::env::var_os("PB_EFFECTS_PYTHON").unwrap_or_else(|| "python3".into());
    let cloud = case.starts_with("oauth_");
    let mut binaries = Vec::new();
    let oracle = if cloud {
        std::env::var_os("PB_GO_ORACLE_CLOUD").or_else(|| std::env::var_os("PB_GO_ORACLE"))
    } else {
        std::env::var_os("PB_GO_ORACLE")
    };
    if let Some(oracle) = oracle {
        binaries.push(PathBuf::from(oracle));
    }
    let candidate = if cloud {
        std::env::var_os("PB_EFFECTS_CLOUD_CANDIDATE")
            .or_else(|| std::env::var_os("PB_EFFECTS_CANDIDATE"))
    } else {
        std::env::var_os("PB_EFFECTS_CANDIDATE")
    };
    binaries.push(candidate.map_or_else(|| PathBuf::from(env!("CARGO_BIN_EXE_pb")), PathBuf::from));
    for binary in binaries {
        let output = Command::new(&python)
            .args(["-c", PROCESS_FIXTURE])
            .arg(&binary)
            .arg(case)
            .output()
            .expect("interactive effects require Python 3.11+");
        assert!(
            output.status.success(),
            "{case} failed for {}: status {}\nstdout: {}\nstderr: {}",
            binary.display(),
            output.status,
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        );
    }
}

macro_rules! process_cases {
    ($($case:ident),+ $(,)?) => {
        $(#[test]
        fn $case() {
            exercise(stringify!($case));
        })+
    };
}

process_cases!(
    credentials_valid_cancel,
    credentials_username_only_cancel,
    credentials_positional_ignored,
    default_cancel,
    replacement_cancel,
    logout_decline,
    dataset_cancel,
    role_abort,
    role_submit,
    role_conflict,
    saved_apply_unchecked,
    saved_filter_quit,
    sql_window_snapshot,
    sql_window_stale,
    sql_window_paging,
    sql_schema_fallback,
    sql_schema_fallback_clock,
    sql_schema_primary_no_fallback,
    sql_prefix_before_eof,
    sql_invalid_prefix_before_eof,
    sql_numeric_overflow,
    sql_unicode_schema_raw_bytes,
    sql_unicode_placeholder_wire,
    sql_unicode_preflight_panic,
    promql_result_policy,
    promql_builder_policy,
    promql_stale_result,
    promql_builder_metric_guard,
    promql_builder_label_guard,
    promql_builder_pending_cache,
    promql_dataset_cache_guard,
    sql_stale_schema,
    sql_cache_eviction,
    sql_unknown_actual_total,
    promql_mode_transitions,
    saved_apply_result_order,
    saved_apply_text_status,
    saved_apply_json_status,
    saved_profile_snapshot,
    sql_duplicate_record_merge,
    sql_duplicate_fields_reuse,
    sql_duplicate_hidden_slots,
    sql_duplicate_record_null_reset,
    sql_duplicate_first_error,
    sql_schema_duplicate_struct_reuse,
    sql_schema_duplicate_array_reset,
    promql_label_duplicate_data_reuse,
    promql_label_duplicate_array_reset,
    promql_label_duplicate_first_error,
    sql_unicode_decoder_keys,
    promql_unicode_decoder_error,
);

macro_rules! deadline_cases {
    ($($case:ident),+ $(,)?) => {
        $(#[test]
        #[ignore = "opt-in: uses the genuine 15/30/50/120-second request deadlines"]
        fn $case() {
            exercise(stringify!($case));
        })+
    };
}

deadline_cases!(
    deadline_schema_15s,
    deadline_builder_30s,
    deadline_sql_50s,
    deadline_promql_120s,
);

#[test]
#[ignore = "opt-in: requires the synthetic cloud orchestrator bearer-token build"]
fn oauth_promql_both_effects() {
    exercise("oauth_promql_both_effects");
}

#[test]
#[ignore = "opt-in: requires the synthetic cloud orchestrator bearer-token build"]
fn oauth_sql_window_effects() {
    exercise("oauth_sql_window_effects");
}

#[test]
#[ignore = "opt-in: requires the synthetic cloud orchestrator bearer-token build"]
fn oauth_schema_fallback_scope() {
    exercise("oauth_schema_fallback_scope");
}

#[test]
#[ignore = "opt-in: requires the synthetic cloud orchestrator bearer-token build"]
fn oauth_saved_menu_apply_scope() {
    exercise("oauth_saved_menu_apply_scope");
}

// PTY ownership, bounded waits, subprocess cleanup and deterministic HTTP are
// implemented here independently of harness/phase7.py and production services.
const PROCESS_FIXTURE: &str = r####"
"""Independent process/PTY effect assertions; Python standard library only."""
import errno
import datetime
import fcntl
import http.server
import json
import os
import pathlib
import pty
import re
import select
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tomllib
import urllib.parse

TIMEOUT = 12
ROOT = os.environ.get('TMPDIR', tempfile.gettempdir())


class StreamBody:
    def __init__(self, prefix, tail, gate):
        self.prefix, self.tail, self.gate = prefix, tail, gate


class Fixture:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests = []
        self.condition = threading.Condition()
        self.releases = []
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def handle_request(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                parsed = urllib.parse.urlsplit(self.path)
                request = dict(method=self.command, path=parsed.path,
                               params=urllib.parse.parse_qs(parsed.query),
                               body=body.decode(), headers=dict(self.headers),
                               received=time.monotonic(), protocol=self.request_version)
                with fixture.condition:
                    fixture.requests.append(request)
                    index = sum(r['path'] == parsed.path for r in fixture.requests) - 1
                    fixture.condition.notify_all()
                response = fixture.routes.get((self.command, parsed.path))
                if callable(response):
                    response = response(request, index)
                if response is None:
                    response = (500, {'unexpected': self.path})
                status, payload, *hold = response
                if hold:
                    hold[0].wait(TIMEOUT)
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    try:
                        request['closed_before_response'] = bool(readable and self.connection.recv(1, socket.MSG_PEEK) == b'')
                    except ConnectionResetError:
                        request['closed_before_response'] = True
                streamed = isinstance(payload, StreamBody)
                data = payload.prefix + payload.tail if streamed else (
                    payload if isinstance(payload, bytes) else json.dumps(payload).encode())
                request['response_status'] = status
                request['response_preview'] = data[:2000].decode(errors='replace')
                request['response_bytes'] = len(data)
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    if streamed:
                        self.wfile.write(payload.prefix)
                        self.wfile.flush()
                        with fixture.condition:
                            request['prefix_sent'] = True
                            fixture.condition.notify_all()
                        payload.gate.wait(TIMEOUT * 3)
                        self.wfile.write(payload.tail)
                    else:
                        self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with fixture.condition:
                        request['done'] = True
                        fixture.condition.notify_all()

            do_GET = do_POST = do_PUT = do_DELETE = handle_request

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def wait(self, path, count=1):
        deadline = time.monotonic() + TIMEOUT
        with self.condition:
            while sum(r['path'] == path for r in self.requests) < count:
                remaining = deadline - time.monotonic()
                assert remaining > 0, ('missing request', path, self.requests)
                self.condition.wait(remaining)
        return [r for r in self.requests if r['path'] == path]

    def close(self):
        for gate in self.releases:
            gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def done(self, path, index=0):
        deadline = time.monotonic() + TIMEOUT
        with self.condition:
            while not [r for r in self.requests if r['path'] == path][index].get('done'):
                remaining = deadline - time.monotonic()
                assert remaining > 0, ('unfinished response', path)
                self.condition.wait(remaining)


class Session:
    def __init__(self, binary, args, fixture, profiles=('original',), default='original', profile_records=None,
                 dimensions=(120, 35)):
        self.temp = tempfile.TemporaryDirectory(prefix='effects-', dir=ROOT)
        root = pathlib.Path(self.temp.name)
        self.config = root / 'xdg' / 'pb' / 'config.toml'
        self.config.parent.mkdir(parents=True)
        config = 'DefaultProfile = ' + json.dumps(default) + '\n'
        for name in profiles:
            config += '\n[Profiles.' + json.dumps(name) + ']\n'
            record = profile_records[name] if profile_records is not None else {
                'url': fixture.url, 'apiKey': 'fixture-key', 'cloud': False}
            for key, value in record.items():
                config += key + ' = ' + json.dumps(value) + '\n'
        self.config.write_text(config)
        self.config.chmod(0o600)
        self.before = self.config.read_bytes()
        env = os.environ.copy()
        for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy',
                     'all_proxy', 'PB_PROFILE', 'PB_API_KEY', 'PB_URL'):
            env.pop(name, None)
        env.update(HOME=str(root), XDG_CONFIG_HOME=str(root / 'xdg'), TERM='xterm-256color',
                   PB_THEME='dark', COLORFGBG='15;0', TZ='UTC', NO_PROXY='127.0.0.1',
                   TMPDIR=ROOT, GOTMPDIR=ROOT, CLAUDE_CODE_TMPDIR=ROOT)
        self.master, self.slave = pty.openpty()
        columns, rows = dimensions
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
        self.termios_before = termios.tcgetattr(self.slave)
        # Give /dev/tty the same terminal as stdin/stdout. stderr stays separate.
        def controlling_terminal():
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        self.child = subprocess.Popen([binary, *args], stdin=self.slave, stdout=self.slave,
                                      stderr=subprocess.PIPE, env=env, start_new_session=True,
                                      preexec_fn=controlling_terminal)
        self.output = bytearray()
        self.error = bytearray()
        self.finished = False

    def pump(self, duration=.05):
        ready, _, _ = select.select([self.master, self.child.stderr], [], [], duration)
        for fd in ready:
            try:
                data = os.read(fd if isinstance(fd, int) else fd.fileno(), 65536)
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                data = b''
            if fd == self.master:
                self.output.extend(data)
                if b'\x1b[6n' in data:
                    os.write(self.master, b'\x1b[1;1R')
            else:
                self.error.extend(data)

    def text(self, after=0):
        return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', self.output[after:].decode(errors='replace'))

    def until(self, needle, after=0, timeout=None):
        deadline = time.monotonic() + (TIMEOUT if timeout is None else timeout)
        while needle not in self.text(after):
            self.pump()
            assert time.monotonic() < deadline, ('missing screen', needle, self.text(), self.error)
            assert self.child.poll() is None or needle in self.text(after), (
                'early exit', self.child.returncode, self.text(), self.error)

    def send(self, data):
        os.write(self.master, data)
        # Input separation prevents Escape/key-sequence and focus races.
        for _ in range(3):
            self.pump()

    def finish(self, code=0):
        deadline = time.monotonic() + TIMEOUT
        while self.child.poll() is None:
            self.pump()
            assert time.monotonic() < deadline, ('hung child', self.text(), self.error)
        for _ in range(4):
            self.pump(.02)
        assert self.child.returncode == code, (self.child.returncode, self.text(), self.error)
        assert termios.tcgetattr(self.slave) == self.termios_before, 'terminal mode not restored'
        self.finished = True

    def data(self):
        return tomllib.loads(self.config.read_text())

    def unchanged(self):
        assert self.config.read_bytes() == self.before, 'canceled workflow rewrote config'

    def close(self):
        if self.child.poll() is None:
            self.child.kill()
            self.child.wait()
        self.child.stderr.close()
        os.close(self.master)
        os.close(self.slave)
        self.temp.cleanup()


def assert_requests(fixture, expected, independent=False, check_auth=True):
    actual = [(r['method'], r['path']) for r in fixture.requests]
    assert (sorted(actual) == sorted(expected) if independent else actual == expected), actual
    for request in fixture.requests:
        headers = {k.lower(): v for k, v in request['headers'].items()}
        if check_auth:
            assert headers.get('x-api-key') == 'fixture-key', request


def header(request, name):
    # Retain original header spelling in captures, but honor HTTP's
    # case-insensitive field names when driving fixture behavior/assertions.
    return next((value for key, value in request['headers'].items() if key.lower() == name.lower()), None)


def setup_sql(fixture, datasets=None):
    fixture.routes[('GET', '/api/prism/v1/home')] = (200, {'datasets': datasets or []})
    fixture.routes[('GET', '/api/v1/logstream/logs/schema')] = (200, {'fields': [{'name': 'value'}]})


def setup_promql(fixture, metrics=None, datasets=None):
    fixture.routes[('GET', '/api/prism/v1/home')] = (200, {'datasets': datasets or []})
    fixture.routes[('GET', '/prometheus/api/v1/label/__name__/values')] = (200, {'status': 'success', 'data': metrics or ['up']})
    fixture.routes[('GET', '/prometheus/api/v1/query_range')] = (200, {'status': 'success', 'data': {'resultType': 'matrix', 'result': []}})
    fixture.routes[('GET', '/prometheus/api/v1/query')] = (200, {'status': 'success', 'data': {'resultType': 'vector', 'result': []}})


def query_args(query='up'):
    return ['promql', 'run', query, '-i', '--dataset', 'metrics',
            '--from', '2024-01-01T00:00:00Z', '--to', '2024-01-01T00:10:00Z', '--step', '30s']


def open_builder(session):
    session.until('QUERY')
    session.send(b'\x02')
    mark = len(session.output)
    session.send(b'\r')
    session.until('PROMQL QUERY BUILDER', after=mark)


def oauth_profile(fixture):
    return {'original': {'url': fixture.url, 'cloud': True, 'session_token': 'fixture-session-0',
                         'refresh_token': 'fixture-refresh-0', 'tenant_id': 'fixture-tenant',
                         'orchestrator_url': fixture.url}}


def setup_rotating_refresh(fixture):
    def rotate(request, index):
        assert json.loads(request['body']) == {'grant_type': 'refresh_token', 'refresh_token': 'fixture-refresh-0'}, request
        return (200, {'access_token': 'fixture-session-1', 'refresh_token': 'fixture-refresh-1'}) if index == 0 else (
            401, {'error': 'fixture refresh token already rotated'})
    fixture.routes[('POST', '/api/v1/cli/oauth/token')] = rotate


def assert_oauth(fixture):
    for request in fixture.requests:
        headers = {k.lower(): v for k, v in request['headers'].items()}
        if request['path'] == '/api/v1/cli/oauth/token':
            assert headers.get('authorization') == 'Bearer harness-orchestrator-token', request
            assert headers.get('accept') == 'application/json', request
            assert headers.get('content-type') == 'application/json', request
            assert 'cookie' not in headers and 'x-p-tenant' not in headers, request
        else:
            assert headers.get('cookie') in ('session=fixture-session-0', 'session=fixture-session-1'), request
            assert headers.get('x-p-tenant') == 'fixture-tenant', request
            assert 'x-api-key' not in headers and 'authorization' not in headers, request


def run_case(binary, case):
    global TIMEOUT
    deadlines = {'deadline_schema_15s': 15, 'deadline_builder_30s': 30,
                 'deadline_sql_50s': 50, 'deadline_promql_120s': 120}
    if case in deadlines:
        TIMEOUT = deadlines[case] + 15
    fixture = Fixture()
    session = None
    try:
        if case.startswith('credentials_'):
            args = ['profile', 'add', 'new', fixture.url]
            if case == 'credentials_positional_ignored':
                args.append('ignored-user')
            session = Session(binary, args, fixture)
            session.until('user:')
            if case == 'credentials_valid_cancel':
                session.send(b'dummy-user\t')
                session.send(b'dummy-secret')
            elif case == 'credentials_username_only_cancel':
                session.send(b'dummy-user')
            session.send(b'\x03')
            session.finish()
            data = session.data()
            assert data['DefaultProfile'] == 'new', data
            assert 'original' in data['Profiles'], data
            profile = data['Profiles']['new']
            assert profile['url'] == fixture.url, profile
            expected = ('dummy-user', 'dummy-secret') if case == 'credentials_valid_cancel' else ('', '')
            assert (profile.get('username', ''), profile.get('password', '')) == expected, profile
            assert b'dummy-secret' not in session.output, 'secret leaked into terminal'
            assert session.config.stat().st_mode & 0o777 == 0o600
            assert 'Profile new added successfully' in session.text()
            assert_requests(fixture, [])

        elif case in ('default_cancel', 'replacement_cancel', 'logout_decline'):
            args = {'default_cancel': ['profile', 'default'],
                    'replacement_cancel': ['profile', 'remove', 'original'],
                    'logout_decline': ['logout']}[case]
            session = Session(binary, args, fixture, ('original', 'alpha', 'beta'))
            if case == 'logout_decline':
                session.until('Continue? [y/N]:')
                flags = termios.tcgetattr(session.slave)[3]
                assert flags & termios.ICANON and flags & termios.ECHO, 'logout prompt is not cooked'
                session.send(b'n\n')
            else:
                session.until('alpha')
                session.send(b'\x03')
            session.finish()
            if case == 'replacement_cancel':
                data = session.data()
                assert set(data['Profiles']) == {'alpha', 'beta'}, data
                assert data['DefaultProfile'] == '', data
                assert 'Deleted profile original' in session.text()
            else:
                session.unchanged()
            if case == 'logout_decline':
                assert 'Logout canceled' in session.text()
                assert not session.error, session.error
            assert_requests(fixture, [])

        elif case == 'dataset_cancel':
            session = Session(binary, ['dataset', 'add', 'new-dataset'], fixture)
            session.until('logs')
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            assert 'Dataset creation canceled' in session.text()
            assert not session.error, session.error
            assert_requests(fixture, [])

        elif case.startswith('role_'):
            fixture.routes[('GET', '/api/v1/roles')] = (200, {'taken': []})
            fixture.routes[('PUT', '/api/v1/role/new-role')] = (200, {})
            name = 'taken' if case == 'role_conflict' else 'new-role'
            session = Session(binary, ['role', 'add', name], fixture)
            fixture.wait('/api/v1/roles')
            if case == 'role_conflict':
                session.finish(1)
                assert b'already exists' in session.error, session.error
                assert 'Submit' not in session.text()
            else:
                session.until('Submit')
                if case == 'role_abort':
                    session.send(b'\x03')
                else:
                    session.send(b'\x1b[C')
                    session.send(b'\t')
                    session.send(b'\r')
                session.finish()
                if case == 'role_abort':
                    assert 'aborted by user' in session.text()
                else:
                    put = fixture.requests[-1]
                    assert json.loads(put['body']) == [{'privilege': 'editor'}], put
                    assert 'Added role new-role' in session.text()
            session.unchanged()
            expected = [('GET', '/api/v1/roles')]
            if case == 'role_submit':
                expected.append(('PUT', '/api/v1/role/new-role'))
            assert_requests(fixture, expected)

        elif case in ('saved_apply_unchecked', 'saved_filter_quit'):
            saved = [{'filter_id': 'saved-id', 'filter_name': 'fixture saved', 'stream_name': 'logs',
                      'query': {'filter_type': 'sql', 'filter_query': 'SELECT event.code FROM logs'}}]
            fixture.routes[('GET', '/api/v1/filters')] = (500, saved)
            fixture.routes[('POST', '/api/v1/query')] = (200, b'[]')
            session = Session(binary, ['sql', 'list'], fixture)
            session.until('fixture saved')
            session.send(b'/')
            # Global apply/back keys take precedence even during filtering.
            session.send(b'a' if case == 'saved_apply_unchecked' else b'b')
            session.finish()
            session.unchanged()
            expected = [('GET', '/api/v1/filters')]
            if case == 'saved_apply_unchecked':
                expected.append(('POST', '/api/v1/query'))
                match = re.search(r'Query: ([^\r\n]+)', session.text())
                assert match and match[1] == 'SELECT "event.code" FROM logs', session.text()
                assert b'\x1b[?1049l' in session.output, 'saved screen never restored'
                assert session.output.rfind(b'\x1b[?1049l') < session.output.find(b'Query: ')
                wire = json.loads(fixture.requests[-1]['body'])
                assert wire['query'] == 'SELECT "event.code" FROM logs LIMIT 500', wire
                assert (wire['startTime'], wire['endTime']) == ('1h', 'now'), wire
            assert_requests(fixture, expected)

        elif case in ('sql_duplicate_record_merge', 'sql_duplicate_fields_reuse', 'sql_duplicate_hidden_slots',
                      'sql_duplicate_record_null_reset', 'sql_duplicate_first_error',
                      'sql_schema_duplicate_struct_reuse', 'sql_schema_duplicate_array_reset', 'sql_unicode_decoder_keys'):
            # Bytes preserve duplicate object members. A normal JSON fixture
            # dictionary would erase the exact server behavior under review.
            bodies = {
                'sql_duplicate_record_merge': b'{"fields":["a","b"],"records":[{"a":"1"}],"records":[{"b":"2"}]}',
                'sql_duplicate_fields_reuse': b'{"fields":["a","b"],"fields":[null,"x"],"records":[{"a":"FIELD_A_REUSED","x":"FIELD_X"}]}',
                'sql_duplicate_hidden_slots': b'{"fields":["a","b"],"fields":[null],"fields":[null,null],"records":[{"a":"INITIAL_A"},{"b":"HIDDEN_B"}],"records":[{"a":"ACTIVE_A"}],"records":[{}, {"a":"RESTORED_A"}]}',
                'sql_duplicate_record_null_reset': b'{"fields":["a","b"],"records":[{"a":"DISCARDED_A"}],"records":[null],"records":[{"b":"FINAL_B"}]}',
                'sql_duplicate_first_error': b'{"fields":[1],"fields":["a"],"records":[{"a":"MUST_NOT_RENDER"}]}',
                'sql_schema_duplicate_struct_reuse': b'{"fields":[{"name":"a"}],"fields":[{}]}',
                'sql_schema_duplicate_array_reset': b'{"fields":[{"name":"DISCARDED_NAME"}],"fields":[],"fields":[{}]}',
                'sql_unicode_decoder_keys': '{"fieldſ":["F3_FIELD"],"recordſ":[{"F3_FIELD":"F3_SQL_ROW"}]}'.encode(),
            }
            schema = case.startswith('sql_schema_')
            fallback = case == 'sql_schema_duplicate_array_reset'
            setup_sql(fixture, [{'title': 'logs', 'datasetType': 'logs'}])
            fixture.routes[('POST', '/api/v1/query')] = (
                200, {'fields': ['FALLBACK_FIELD'], 'records': [{'FALLBACK_FIELD': 'FALLBACK_ROW'}]})
            fixture.routes[('GET' if schema else 'POST',
                            '/api/v1/logstream/logs/schema' if schema else '/api/v1/query')] = (200, bodies[case])
            session = Session(binary, ['sql', 'run', *([] if schema else ['SELECT * FROM logs LIMIT 10']), '-i',
                                      '--from', '2024-01-01T00:00:00Z', '--to', '2024-01-01T01:00:00Z'], fixture)
            if schema:
                fixture.wait('/api/v1/logstream/logs/schema')
                session.until('FALLBACK_FIELD' if fallback else 'COLUMNS (1)')
            elif case == 'sql_duplicate_first_error':
                session.until('cannot unmarshal number into Go struct field QueryData.fields')
                session.until('string')
            else:
                session.until('rows 1/')
            # A fast window response may render before the home completion
            # schedules schema. Observe the asserted startup graph before quit.
            fixture.wait('/api/v1/logstream/logs/schema')
            fixture.done('/api/v1/logstream/logs/schema')
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            text = session.text()
            if case == 'sql_duplicate_record_merge':
                assert re.search(r'│\s*1\s*│\s*2\s', text), text
            elif case == 'sql_duplicate_fields_reuse':
                assert re.search(r'│\s*FIELD_A_REUSED\s*│\s*FIELD_X\s', text), text
            elif case == 'sql_duplicate_hidden_slots':
                assert re.search(r'│\s*ACTIVE_A\s*│\s*—\s', text), text
                assert re.search(r'│\s*RESTORED_A\s*│\s*HIDDEN_B\s', text), text
                assert 'INITIAL_A' not in text, text
            elif case == 'sql_duplicate_record_null_reset':
                assert re.search(r'│\s*—\s*│\s*FINAL_B\s', text), text
                assert 'DISCARDED_A' not in text, text
            elif case == 'sql_duplicate_first_error':
                assert 'MUST_NOT_RENDER' not in text and 'rows 1/' not in text, text
            elif case == 'sql_unicode_decoder_keys':
                assert 'F3_FIELD' in text and 'F3_SQL_ROW' in text, text
            elif fallback:
                assert 'DISCARDED_NAME' not in text, text
            else:
                assert re.search(r'│\s+a\s+│', text), text
            expected = [('GET', '/api/prism/v1/home'), ('GET', '/api/v1/logstream/logs/schema')]
            if not schema or fallback:
                expected += [('POST', '/api/v1/query')]
            assert_requests(fixture, expected, independent=not schema)
            for request in (r for r in fixture.requests if r['path'] == '/api/v1/query'):
                assert request['params'] == {'fields': ['true']}, request
                assert header(request, 'Content-Type') == 'application/json', request
                wire = json.loads(request['body'])
                if fallback:
                    assert wire['query'] == "SELECT * FROM 'logs' LIMIT 1", request
                    assert wire['startTime'] == '2000-01-01T00:00:00+00:00', request
                    assert wire['endTime'].endswith('Z'), request
                    datetime.datetime.fromisoformat(wire['endTime'].replace('Z', '+00:00'))
                else:
                    assert wire == {'query': 'SELECT * FROM logs LIMIT 10 OFFSET 0',
                                    'startTime': '2024-01-01T00:00:00Z',
                                    'endTime': '2024-01-01T01:00:00Z'}, request

        elif case in ('promql_label_duplicate_data_reuse', 'promql_label_duplicate_array_reset',
                      'promql_label_duplicate_first_error', 'promql_unicode_decoder_error'):
            setup_promql(fixture)
            if case == 'promql_unicode_decoder_error':
                fixture.routes[('GET', '/prometheus/api/v1/query_range')] = (
                    200, '{"ſtatus":"error","error":"F3_RESULT_ERROR","errorType":"bad_data"}'.encode())
                session = Session(binary, query_args(), fixture)
                session.until('bad_data: F3_RESULT_ERROR')
            else:
                bodies = {
                    'promql_label_duplicate_data_reuse': b'{"status":"success","data":["F1_LABEL_A","F1_LABEL_B"],"data":[null]}',
                    'promql_label_duplicate_array_reset': b'{"status":"success","data":["DISCARDED_LABEL"],"data":[],"data":[null]}',
                    'promql_label_duplicate_first_error': b'{"status":"success","data":[1],"data":["MUST_NOT_RENDER"]}',
                }
                fixture.routes[('GET', '/prometheus/api/v1/labels')] = (200, bodies[case])
                fixture.routes[('GET', '/prometheus/api/v1/label/F1_LABEL_A/values')] = (
                    200, {'status': 'success', 'data': ['F1_VALUE']})
                session = Session(binary, query_args(''), fixture)
                open_builder(session)
                session.until('built  up')
                mark = len(session.output)
                session.send(b'\r')
                fixture.wait('/prometheus/api/v1/labels')
                if case == 'promql_label_duplicate_data_reuse':
                    session.until('F1_LABEL_A', after=mark)
                    session.send(b'\r')
                    fixture.wait('/prometheus/api/v1/label/F1_LABEL_A/values')
                    session.until('F1_VALUE')
                    # Fresh value replies select (any); explicitly select value.
                    session.send(b'\x1b[B')
                    session.until('built  up{F1_LABEL_A="F1_VALUE"}')
                else:
                    # (any) is only painted in the labels column after its
                    # empty/error response applies; no blind delay is needed.
                    session.until('│   (any)', after=mark)
                session.send(b'\r')
                fixture.wait('/prometheus/api/v1/query_range')
                session.until('no results for this query')
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            text = session.text()
            assert 'DISCARDED_LABEL' not in text and 'MUST_NOT_RENDER' not in text, text
            expected = [('GET', '/api/prism/v1/home'), ('GET', '/prometheus/api/v1/query_range')]
            metric_requests = [r for r in fixture.requests if r['path'] == '/prometheus/api/v1/label/__name__/values']
            assert 1 <= len(metric_requests) <= (1 if case == 'promql_unicode_decoder_error' else 2), fixture.requests
            expected += [('GET', '/prometheus/api/v1/label/__name__/values')] * len(metric_requests)
            params = {'stream': ['metrics'], 'start': ['2024-01-01T00:00:00Z'],
                      'end': ['2024-01-01T00:10:00Z'], 'match[]': ['up']}
            if case != 'promql_unicode_decoder_error':
                expected += [('GET', '/prometheus/api/v1/labels')]
                labels = fixture.wait('/prometheus/api/v1/labels')[0]
                assert labels['params'] == params, labels
            if case == 'promql_label_duplicate_data_reuse':
                expected += [('GET', '/prometheus/api/v1/label/F1_LABEL_A/values')]
                values = fixture.wait('/prometheus/api/v1/label/F1_LABEL_A/values')[0]
                assert values['params'] == params, values
            assert_requests(fixture, expected, independent=True)
            for request in fixture.requests:
                if request['path'] != '/api/prism/v1/home':
                    assert header(request, 'Content-Type') is None, request
            result = fixture.wait('/prometheus/api/v1/query_range')[0]
            expression = 'up{F1_LABEL_A="F1_VALUE"}' if case == 'promql_label_duplicate_data_reuse' else 'up'
            assert result['params'] == {'query': [expression], 'stream': ['metrics'], 'step': ['30s'],
                                        'start': ['2024-01-01T00:00:00Z'], 'end': ['2024-01-01T00:10:00Z']}, result

        elif case in ('sql_unicode_schema_raw_bytes', 'sql_unicode_placeholder_wire', 'sql_unicode_preflight_panic'):
            # CLI quotes unsafe stream names before constructing the model.
            # Use the empty editor for the raw-byte helper and placeholder cases.
            panic = case == 'sql_unicode_preflight_panic'
            placeholder = case == 'sql_unicode_placeholder_wire'
            query = ("select '" + 'Ⱥ' * 20 + "' from ") if panic else (
                "select 'İ' from dataset" if placeholder else "select 'Ⱥ' from 测试")
            setup_sql(fixture, [{'title': 'logs', 'datasetType': 'logs'}] if placeholder else [])
            schema_path = '/api/v1/logstream/%B5%8B%E8%AF%95/schema'
            fixture.routes[('GET', schema_path)] = (200, {'fields': [{'name': 'RAW_BYTE_SCHEMA'}]})
            fixture.routes[('POST', '/api/v1/query')] = (
                200, {'fields': ['value'], 'records': [{'value': 'IDOT_RESULT'}]})
            session = Session(binary, ['sql', 'run', *([query] if panic else []), '-i',
                                      '--from', '2024-01-01T00:00:00Z', '--to', '2024-01-01T01:00:00Z'], fixture)
            if panic:
                # Default LIMIT survives command preflight: 64 original bytes,
                # while strings.ToLower expansion moves the slice start to 75.
                session.finish(2)
                assert session.error.split(b'\n', 1)[0] == (
                    b'panic: runtime error: slice bounds out of range [75:64]'), session.error
                # Runtime stack addresses/dump tail are deliberately scoped.
                # Package-init color probes may precede constructor panic, but
                # no editor, cursor/raw-mode setup or alternate screen may appear.
                residual = re.sub(rb'\x1b\](?:10|11);\?\x1b\\|\x1b\[6n', b'', bytes(session.output))
                assert residual == b'', bytes(session.output)
                assert_requests(fixture, [])
            else:
                session.until('QUERY SQL')
                if placeholder:
                    session.until('value')  # discovered logs schema is applied
                # Unbracketed input exposes incomplete FROM states to View;
                # those can independently panic before the intended helper call.
                session.send(b'\x1b[200~' + query.encode() + b'\x1b[201~')
                session.until(query)
                if placeholder:
                    session.send(b'\x12')  # Ctrl+R: resolve against selected logs
                    request = fixture.wait('/api/v1/query')[0]
                    session.until('IDOT_RESULT')
                    assert request['params'] == {'fields': ['true']}, request
                    assert json.loads(request['body']) == {
                        'query': "select 'İ' from'logs' LIMIT 500 OFFSET 0",
                        'startTime': '2024-01-01T00:00:00Z',
                        'endTime': '2024-01-01T01:00:00Z'}, request
                    assert header(request, 'Content-Type') == 'application/json', request
                    expected = [('GET', '/api/prism/v1/home'),
                                ('GET', '/api/v1/logstream/logs/schema'), ('POST', '/api/v1/query')]
                else:
                    session.send(b'\x0c')  # close initially open columns drawer
                    session.send(b'\x0c')  # opening extracts the original-byte slice
                    request = fixture.wait(schema_path)[0]
                    session.until('RAW_BYTE_SCHEMA')
                    assert urllib.parse.unquote_to_bytes(request['path']) == (
                        b'/api/v1/logstream/\xb5\x8b\xe8\xaf\x95/schema'), request
                    assert request['params'] == {} and request['body'] == '', request
                    assert header(request, 'Content-Type') is None, request
                    expected = [('GET', '/api/prism/v1/home'), ('GET', schema_path)]
                session.send(b'\x03')
                session.finish()
                assert_requests(fixture, expected)
            session.unchanged()

        elif case in ('sql_schema_fallback_clock', 'sql_schema_primary_no_fallback'):
            primary = case == 'sql_schema_primary_no_fallback'
            schema_path = '/api/v1/logstream/logs/schema'
            fixture.routes[('GET', '/api/prism/v1/home')] = (
                200, {'datasets': [{'title': 'logs', 'datasetType': 'logs'}]})
            gate = threading.Event()
            fixture.releases.append(gate)
            fixture.routes[('GET', schema_path)] = (
                (200, {'fields': [{'name': 'CLOCK_PRIMARY_OK'}]}, gate) if primary else
                (404, {'error': 'schema unavailable'}, gate))
            clock = {}
            def fallback_response(request, index):
                wire = json.loads(request['body'])
                end = datetime.datetime.fromisoformat(wire['endTime'].replace('Z', '+00:00'))
                clock['end_second'] = end.timestamp()
                clock['post_observed_epoch'] = time.time()
                clock['marker'] = ('CLOCK_AT_RELEASE' if end.timestamp() >= clock['release_second']
                                   else 'CLOCK_BEFORE_GET')
                request['clock'] = dict(clock)
                return 200, {'fields': [clock['marker']], 'records': []}
            fixture.routes[('POST', '/api/v1/query')] = fallback_response
            session = Session(binary, ['sql', 'run', '-i', '--from', '2024-01-01T00:00:00Z',
                                      '--to', '2024-01-01T01:00:00Z'], fixture)
            fixture.wait('/api/prism/v1/home')
            schema = fixture.wait(schema_path)[0]
            clock['held_observed_epoch'] = time.time()
            target_second = int(clock['held_observed_epoch']) + 2
            deadline = time.monotonic() + TIMEOUT
            # Hold the primary response across a measured wall-clock boundary.
            # Pumping also services terminal probes; no guessed completion sleep.
            while time.time() < target_second:
                assert time.monotonic() < deadline, 'wall clock did not reach release boundary'
                session.pump(.02)
            assert_requests(fixture, [('GET', '/api/prism/v1/home'), ('GET', schema_path)])
            assert not schema.get('done'), 'primary schema response was not held'
            clock['release_epoch'] = time.time()
            clock['release_second'] = int(clock['release_epoch'])
            assert clock['release_second'] >= int(clock['held_observed_epoch']) + 2, clock
            schema['clock'] = dict(clock)
            gate.set()
            fixture.done(schema_path)
            if primary:
                marker = 'CLOCK_PRIMARY_OK'
            else:
                request = fixture.wait('/api/v1/query')[0]
                fixture.done('/api/v1/query')
                marker = clock['marker']
            session.until(marker)
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            expected = [('GET', '/api/prism/v1/home'), ('GET', schema_path)]
            if not primary:
                expected.append(('POST', '/api/v1/query'))
            assert_requests(fixture, expected)
            assert schema['params'] == {} and schema['body'] == '', schema
            assert header(schema, 'Content-Type') is None, schema
            assert not schema.get('closed_before_response'), schema
            print('SCHEMA_CLOCK=' + json.dumps(dict(clock, terminal_restored=True,
                  config_unchanged=True, returncode=session.child.returncode)), flush=True)
            if not primary:
                assert request['params'] == {'fields': ['true']}, request
                assert header(request, 'Content-Type') == 'application/json', request
                wire = json.loads(request['body'])
                assert set(wire) == {'query', 'startTime', 'endTime'}, wire
                assert wire['query'] == "SELECT * FROM 'logs' LIMIT 1", wire
                assert wire['startTime'] == '2000-01-01T00:00:00+00:00', wire
                assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', wire['endTime']), wire
                assert schema['received'] < request['received'], fixture.requests
                assert clock['release_second'] <= clock['end_second'] <= int(clock['post_observed_epoch']), (
                    'fallback endTime must be sampled after primary release', clock, wire)
                assert marker == 'CLOCK_AT_RELEASE', clock

        elif case in ('sql_window_snapshot', 'sql_window_stale', 'sql_window_paging', 'sql_schema_fallback',
                      'sql_prefix_before_eof', 'sql_invalid_prefix_before_eof', 'sql_numeric_overflow'):
            fixture.routes[('GET', '/api/prism/v1/home')] = (200, {'datasets': []})
            fixture.routes[('GET', '/api/v1/logstream/logs/schema')] = (200, {'fields': [{'name': 'value'}]})
            if case == 'sql_schema_fallback':
                fixture.routes[('GET', '/api/v1/logstream/logs/schema')] = (404, {'error': 'schema unavailable'})
            fixture.routes[('POST', '/api/v1/query')] = (200, {'fields': ['value'], 'records': [{'value': 7}]})
            gate = threading.Event()
            fixture.releases.append(gate)
            if case == 'sql_prefix_before_eof':
                prefix = b'{"fields":["value"],"records":[{"value":"EARLY_COMPLETE_ROW"}]}'
                fixture.routes[('POST', '/api/v1/query')] = (200, StreamBody(prefix, b' trailing bytes', gate))
            elif case == 'sql_invalid_prefix_before_eof':
                fixture.routes[('POST', '/api/v1/query')] = (200, StreamBody(b'!', b' held-open-tail', gate))
            elif case == 'sql_numeric_overflow':
                fixture.routes[('POST', '/api/v1/query')] = (200, b'{"fields":["value"],"records":[{"value":1e400}]}')
            elif case == 'sql_window_stale':
                fixture.routes[('POST', '/api/v1/query')] = lambda request, index: (
                    (200, {'fields': ['value'], 'records': [{'value': 'STALE_OLD_WINDOW'}]}, gate)
                    if index == 0 else (200, {'fields': ['value'], 'records': [{'value': 'CURRENT_NEW_WINDOW'}]}))
            elif case == 'sql_window_paging':
                fixture.routes[('POST', '/api/v1/query')] = lambda request, index: (
                    200, {'fields': ['value'], 'records': [{'value': i} for i in range(500 if index == 0 else 250)]})
            session = Session(binary, ['sql', 'run', 'SELECT * FROM logs LIMIT 750 OFFSET 17', '-i',
                                      '--from', '2024-01-01T00:00:00Z', '--to', '2024-01-01T01:00:00Z'], fixture)
            fixture.wait('/api/v1/query')
            fixture.wait('/api/prism/v1/home')
            fixture.wait('/api/v1/logstream/logs/schema')
            if case == 'sql_schema_fallback':
                fixture.wait('/api/v1/query', 2)
            session.until('RESULTS')
            if case in ('sql_prefix_before_eof', 'sql_invalid_prefix_before_eof'):
                session.until('EARLY_COMPLETE_ROW' if case == 'sql_prefix_before_eof' else 'invalid character')
                assert not gate.is_set(), 'fixture released EOF before the decoder result'
                request = next(r for r in fixture.requests if r['path'] == '/api/v1/query')
                assert request.get('prefix_sent'), request
                assert not request.get('done'), 'held tail already completed'
                gate.set()
            elif case == 'sql_numeric_overflow':
                session.until('cannot unmarshal number 1e400')
                session.until('type float64')
                assert 'QueryData.records' in session.text(), session.text()
            elif case == 'sql_window_stale':
                session.send(b'\x12')
                fixture.wait('/api/v1/query', 2)
                session.until('CURRENT_NEW_WINDOW')
                gate.set()
                fixture.done('/api/v1/query')
                for _ in range(12):
                    session.pump(.03)
                assert 'STALE_OLD_WINDOW' not in session.text(), 'stale SQL window rendered'
                assert not fixture.wait('/api/v1/query')[0].get('closed_before_response'), 'superseded SQL request aborted'
            elif case == 'sql_window_paging':
                session.until('rows 1/750')
                session.send(b'\x16')
                fixture.wait('/api/v1/query', 2)
                session.until('rows 750/750')
                session.send(b'\x19')
                session.send(b'\x16')
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            expected = [('POST', '/api/v1/query'), ('GET', '/api/prism/v1/home'),
                        ('GET', '/api/v1/logstream/logs/schema')]
            if case in ('sql_window_stale', 'sql_window_paging', 'sql_schema_fallback'):
                expected.append(('POST', '/api/v1/query'))
            assert_requests(fixture, expected, independent=True)
            for index, request in enumerate(r for r in fixture.requests if r['path'] == '/api/v1/query'):
                assert request['params'] == {'fields': ['true']}, request
                wire = json.loads(request['body'])
                if case == 'sql_schema_fallback' and wire['query'].endswith('LIMIT 1'):
                    assert wire['query'] == "SELECT * FROM 'logs' LIMIT 1", wire
                    assert wire['startTime'] == '2000-01-01T00:00:00+00:00', wire
                    timestamp = datetime.datetime.fromisoformat(wire['endTime'].replace('Z', '+00:00'))
                    assert abs((datetime.datetime.now(datetime.timezone.utc) - timestamp).total_seconds()) < TIMEOUT, wire
                    schema = next(r for r in fixture.requests if r['path'].endswith('/schema'))
                    assert schema['received'] < request['received'], fixture.requests
                    continue
                query = 'SELECT * FROM logs LIMIT 250 OFFSET 517' if case == 'sql_window_paging' and index else 'SELECT * FROM logs LIMIT 500 OFFSET 17'
                assert wire == {'query': query, 'startTime': '2024-01-01T00:00:00Z',
                                'endTime': '2024-01-01T01:00:00Z'}, wire

        elif case in ('promql_result_policy', 'promql_builder_policy', 'promql_stale_result'):
            fixture.routes[('GET', '/api/prism/v1/home')] = (200, {'datasets': []})
            fixture.routes[('GET', '/prometheus/api/v1/label/__name__/values')] = (200, {'status': 'success', 'data': ['up']})
            fixture.routes[('GET', '/prometheus/api/v1/labels')] = (200, {'status': 'success', 'data': ['__name__', 'job']})
            fixture.routes[('GET', '/prometheus/api/v1/label/job/values')] = (200, {'status': 'success', 'data': ['fixture-host']})
            fixture.routes[('GET', '/prometheus/api/v1/query_range')] = (
                200, {'status': 'success', 'data': {'resultType': 'matrix', 'result': []}})
            if case == 'promql_stale_result':
                gate = threading.Event()
                fixture.releases.append(gate)
                def result_response(request, index):
                    payload = {'status': 'success', 'data': {'resultType': 'matrix', 'result': [
                        {'metric': {'__name__': 'up', 'job': 'OLD_RESULT' if index == 0 else 'NEW_RESULT'},
                         'values': [[1704067200, '1'], [1704067800, '2']]}]}}
                    return (200, payload, gate) if index == 0 else (200, payload)
                fixture.routes[('GET', '/prometheus/api/v1/query_range')] = result_response
            session = Session(binary, ['promql', 'run', '' if case == 'promql_builder_policy' else 'up', '-i', '--dataset', 'metrics',
                                      '--from', '2024-01-01T00:00:00Z', '--to', '2024-01-01T00:10:00Z',
                                      '--step', '30s'], fixture)
            fixture.wait('/api/prism/v1/home')
            fixture.wait('/prometheus/api/v1/label/__name__/values')
            if case == 'promql_builder_policy':
                session.until('QUERY')
                session.send(b'\x02')
                session.send(b'\r')
                session.until('metrics')
                session.send(b'\r')
                fixture.wait('/prometheus/api/v1/labels')
                session.until('job')
                session.send(b'\r')
                fixture.wait('/prometheus/api/v1/label/job/values')
                session.until('fixture-host')
                session.send(b'\x1b[B')
                session.send(b'\r')
            fixture.wait('/prometheus/api/v1/query_range')
            session.until('QUERY')
            if case == 'promql_stale_result':
                session.send(b'\x12')
                fixture.wait('/prometheus/api/v1/query_range', 2)
                session.until('NEW_RESULT')
                gate.set()
                session.until('OLD_RESULT')
                assert session.output.find(b'NEW_RESULT') < session.output.find(b'OLD_RESULT')
                assert not fixture.wait('/prometheus/api/v1/query_range')[0].get('closed_before_response'), 'superseded PromQL result aborted'
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            expected = [('GET', '/prometheus/api/v1/query_range'), ('GET', '/api/prism/v1/home'),
                        ('GET', '/prometheus/api/v1/label/__name__/values')]
            if case == 'promql_builder_policy':
                expected += [('GET', '/prometheus/api/v1/labels'), ('GET', '/prometheus/api/v1/label/job/values')]
                cache_count = len(fixture.wait('/prometheus/api/v1/label/__name__/values'))
                assert cache_count in (1, 2), fixture.requests
                if cache_count == 2:
                    expected.append(('GET', '/prometheus/api/v1/label/__name__/values'))
                labels = next(r for r in fixture.requests if r['path'] == '/prometheus/api/v1/labels')
                values = next(r for r in fixture.requests if r['path'] == '/prometheus/api/v1/label/job/values')
                metadata_params = {'stream': ['metrics'], 'start': ['2024-01-01T00:00:00Z'],
                                   'end': ['2024-01-01T00:10:00Z'], 'match[]': ['up']}
                assert labels['params'] == metadata_params, labels
                assert values['params'] == metadata_params, values
                assert labels['received'] < values['received'], fixture.requests
                assert values['received'] < next(r for r in fixture.requests if r['path'] == '/prometheus/api/v1/query_range')['received']
                for request in (labels, values):
                    assert 'content-type' not in {k.lower() for k in request['headers']}, request
            elif case == 'promql_stale_result':
                expected.append(('GET', '/prometheus/api/v1/query_range'))
            assert_requests(fixture, expected, independent=True)
            request = next(r for r in fixture.requests if r['path'] == '/prometheus/api/v1/query_range')
            assert request['protocol'] == 'HTTP/1.1', request
            expr = 'up{job="fixture-host"}' if case == 'promql_builder_policy' else 'up'
            assert request['params'] == {'query': [expr], 'start': ['2024-01-01T00:00:00Z'],
                                         'end': ['2024-01-01T00:10:00Z'], 'step': ['30s'], 'stream': ['metrics']}, request
        elif case == 'promql_builder_pending_cache':
            setup_promql(fixture)
            gate = threading.Event()
            fixture.releases.append(gate)
            path = '/prometheus/api/v1/label/__name__/values'
            fixture.routes[('GET', path)] = lambda request, index: (
                (200, {'status': 'success', 'data': ['up']}, gate) if index == 0 else
                (200, {'status': 'success', 'data': ['up']}))
            session = Session(binary, query_args(''), fixture)
            fixture.wait(path)
            open_builder(session)
            fixture.wait(path, 2)
            session.until('built  up')
            assert not gate.is_set()
            gate.set()
            fixture.done(path)
            session.send(b'\x1b')
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            assert_requests(fixture, [('GET', '/api/prism/v1/home'), ('GET', path), ('GET', path)], independent=True)

        elif case in ('promql_builder_metric_guard', 'promql_builder_label_guard'):
            setup_promql(fixture, ['metric_a', 'metric_b'] if case.endswith('metric_guard') else ['up'])
            gate = threading.Event()
            fixture.releases.append(gate)
            if case.endswith('metric_guard'):
                fixture.routes[('GET', '/prometheus/api/v1/labels')] = lambda request, index: (
                    (200, {'status': 'success', 'data': ['OLD_LABEL']}, gate)
                    if request['params']['match[]'] == ['metric_a'] else
                    (200, {'status': 'success', 'data': ['NEW_LABEL']}))
            else:
                fixture.routes[('GET', '/prometheus/api/v1/labels')] = (200, {'status': 'success', 'data': ['label_a', 'label_b']})
                fixture.routes[('GET', '/prometheus/api/v1/label/label_a/values')] = (200, {'status': 'success', 'data': ['OLD_VALUE']}, gate)
                fixture.routes[('GET', '/prometheus/api/v1/label/label_b/values')] = (200, {'status': 'success', 'data': ['NEW_VALUE']})
            session = Session(binary, query_args(''), fixture)
            fixture.wait('/prometheus/api/v1/label/__name__/values')
            open_builder(session)
            session.send(b'\r')
            fixture.wait('/prometheus/api/v1/labels')
            held_path = '/prometheus/api/v1/labels'
            forbidden = 'OLD_LABEL'
            if case.endswith('label_guard'):
                session.until('label_a')
                session.send(b'\r')
                held_path = '/prometheus/api/v1/label/label_a/values'
                forbidden = 'OLD_VALUE'
                fixture.wait(held_path)
            # Navigate to a different selection without confirming it. This
            # allows the old completion to arrive, exercising its actual guard.
            session.send(b'\x1b[Z')
            session.send(b'\x1b[B')
            gate.set()
            fixture.done(held_path)
            for _ in range(10):
                session.pump(.04)
            assert forbidden not in session.text(), ('stale builder completion rendered', session.text())
            assert not fixture.wait(held_path)[0].get('closed_before_response'), 'navigation unexpectedly aborted request'
            session.send(b'\r')
            if case.endswith('metric_guard'):
                fixture.wait('/prometheus/api/v1/labels', 2)
                session.until('NEW_LABEL')
                session.send(b'\x12')
                expected_expr = 'metric_b{NEW_LABEL!=""}'
            else:
                fixture.wait('/prometheus/api/v1/label/label_b/values')
                session.until('NEW_VALUE')
                session.send(b'\x1b[B')
                session.send(b'\r')
                expected_expr = 'up{label_b="NEW_VALUE"}'
            result = fixture.wait('/prometheus/api/v1/query_range')[0]
            assert result['params']['query'] == [expected_expr], result
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            expected = [('GET', '/api/prism/v1/home'), ('GET', '/prometheus/api/v1/label/__name__/values'),
                        ('GET', '/prometheus/api/v1/labels'), ('GET', '/prometheus/api/v1/query_range')]
            if case.endswith('metric_guard'):
                expected.append(('GET', '/prometheus/api/v1/labels'))
                assert [r['params']['match[]'] for r in fixture.wait('/prometheus/api/v1/labels')] == [['metric_a'], ['metric_b']]
            else:
                expected += [('GET', '/prometheus/api/v1/label/label_a/values'), ('GET', '/prometheus/api/v1/label/label_b/values')]
            # Opening the builder can race the hidden startup cache completion.
            # The independently characterized pending-cache case proves Go's
            # single additional fetch on that path; do not treat it as a guard bug.
            cache_count = len(fixture.wait('/prometheus/api/v1/label/__name__/values'))
            assert cache_count in (1, 2), fixture.requests
            if cache_count == 2:
                expected.append(('GET', '/prometheus/api/v1/label/__name__/values'))
            assert_requests(fixture, expected, independent=True)

        elif case == 'promql_dataset_cache_guard':
            datasets = [{'title': name, 'datasetType': 'metrics'} for name in ('alpha', 'beta')]
            setup_promql(fixture, datasets=datasets)
            fixture.routes[('GET', '/api/prism/v1/home')] = lambda request, index: (200, {'datasets': datasets[:1] if index == 0 else datasets})
            gate = threading.Event()
            fixture.releases.append(gate)
            path = '/prometheus/api/v1/label/__name__/values'
            fixture.routes[('GET', path)] = lambda request, index: (
                (200, {'status': 'success', 'data': ['OLD_METRIC']}, gate)
                if request['params']['stream'] == ['alpha'] else
                (200, {'status': 'success', 'data': ['NEW_METRIC']}))
            args = query_args('')
            # Auto-selection makes the first alpha cache request evidence that
            # the initial home completion has already reached the reducer.
            args[args.index('metrics')] = ''
            session = Session(binary, args, fixture)
            fixture.wait(path)
            session.until('QUERY')
            mark = len(session.output)
            session.send(b'\x04')
            session.until('beta', after=mark)
            session.send(b'beta')
            session.send(b'\r')
            fixture.wait(path, 2)
            fixture.done(path, 1)
            for _ in range(5):
                session.pump(.03)
            gate.set()
            fixture.done(path)
            assert not fixture.wait(path)[0].get('closed_before_response'), 'dataset navigation aborted old metric cache request'
            for _ in range(5):
                session.pump(.03)
            open_builder(session)
            session.until('NEW_METRIC')
            assert 'OLD_METRIC' not in session.text(), session.text()
            session.send(b'\x1b')
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            assert [r['params']['stream'] for r in fixture.wait(path)] == [['alpha'], ['beta']]
            assert_requests(fixture, [('GET', '/api/prism/v1/home')] * 2 + [('GET', path)] * 2, independent=True)

        elif case == 'sql_stale_schema':
            datasets = [{'title': name, 'datasetType': 'logs'} for name in ('alpha', 'beta')]
            setup_sql(fixture, datasets)
            fixture.routes[('GET', '/api/prism/v1/home')] = lambda request, index: (200, {'datasets': datasets[:1] if index == 0 else datasets})
            gate = threading.Event()
            fixture.releases.append(gate)
            fixture.routes[('GET', '/api/v1/logstream/alpha/schema')] = (200, {'fields': [{'name': 'ALPHA_OLD_COLUMN'}]}, gate)
            fixture.routes[('GET', '/api/v1/logstream/beta/schema')] = (200, {'fields': [{'name': 'BETA_NEW_COLUMN'}]})
            session = Session(binary, ['sql', 'run', '-i'], fixture)
            fixture.wait('/api/v1/logstream/alpha/schema')
            session.until('QUERY SQL')
            mark = len(session.output)
            session.send(b'\x04')
            session.until('beta', after=mark)
            session.send(b'beta')
            session.send(b'\r')
            fixture.wait('/api/v1/logstream/beta/schema')
            session.until('BETA_NEW_COLUMN')
            mark = len(session.output)
            gate.set()
            session.until('ALPHA_OLD_COLUMN', after=mark)
            assert not fixture.wait('/api/v1/logstream/alpha/schema')[0].get('closed_before_response')
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            assert_requests(fixture, [('GET', '/api/prism/v1/home')] * 2 +
                            [('GET', '/api/v1/logstream/alpha/schema'), ('GET', '/api/v1/logstream/beta/schema')], independent=True)

        elif case in ('sql_cache_eviction', 'sql_unknown_actual_total'):
            setup_sql(fixture)
            def windows(request, index):
                wire = json.loads(request['body'])
                match = re.search(r'LIMIT (\d+) OFFSET (\d+)$', wire['query'])
                assert match, wire
                limit, offset = map(int, match.groups())
                size = 123 if case == 'sql_unknown_actual_total' and offset == 500 else limit
                return (200, {'fields': ['value'], 'records': [
                    {'value': f'WINDOW_{offset // 500}_ROW_{i}'} for i in range(size)]})
            fixture.routes[('POST', '/api/v1/query')] = windows
            session = Session(binary, ['sql', 'run', 'SELECT * FROM logs LIMIT 2200', '-i',
                                      '--from', '2024-01-01T00:00:00Z', '--to', '2024-01-01T01:00:00Z'], fixture)
            fixture.wait('/api/v1/query')
            session.until('rows 1/2200')
            # Each full window leaves the actual total unknown. Walk forward
            # through page navigation, synchronizing on requests and rows.
            deadline = time.monotonic() + TIMEOUT * 4
            row = 1
            while (row <= 1500 if case == 'sql_cache_eviction' else len(fixture.wait('/api/v1/query')) < 2):
                mark = len(session.output)
                session.send(b'\x1b[6~')
                step_deadline = time.monotonic() + TIMEOUT
                while True:
                    rows = [int(m) for m in re.findall(r'rows (\d+)/\d+', session.text(mark))]
                    if rows and max(rows) > row:
                        row = max(rows)
                        break
                    session.pump()
                    assert time.monotonic() < step_deadline, ('page action did not advance cursor', row, session.text(mark))
                assert time.monotonic() < deadline, ('pagination did not reach target', row, fixture.requests)
            if case == 'sql_cache_eviction':
                session.until('WINDOW_3_ROW')
                before = fixture.wait('/api/v1/query', 4)
                offsets = [int(re.search(r'OFFSET (\d+)$', json.loads(r['body'])['query'])[1]) for r in before]
                assert offsets == [0, 500, 1000, 1500], offsets
                mark = len(session.output)
                session.send(b'\x19')
                fixture.wait('/api/v1/query', 5)
                session.until('WINDOW_0_ROW', after=mark)
                assert json.loads(fixture.wait('/api/v1/query')[-1]['body'])['query'].endswith('LIMIT 500 OFFSET 0')
            else:
                session.until('/623')
                session.send(b'\x16')
                session.until('rows 623/623')
                session.send(b'\x16')
                queries = fixture.wait('/api/v1/query', 2)
                assert [json.loads(r['body'])['query'] for r in queries] == [
                    'SELECT * FROM logs LIMIT 500 OFFSET 0', 'SELECT * FROM logs LIMIT 500 OFFSET 500'], queries
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            count = 5 if case == 'sql_cache_eviction' else 2
            assert_requests(fixture, [('GET', '/api/prism/v1/home'), ('GET', '/api/v1/logstream/logs/schema')] +
                            [('POST', '/api/v1/query')] * count, independent=True)
            for request in fixture.wait('/api/v1/query'):
                wire = json.loads(request['body'])
                assert (wire['startTime'], wire['endTime']) == ('2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z'), wire

        elif case == 'promql_mode_transitions':
            setup_promql(fixture)
            def range_ready(request, index):
                start, end = [datetime.datetime.fromisoformat(request['params'][key][0].replace('Z', '+00:00')).timestamp() for key in ('start', 'end')]
                return (200, {'status': 'success', 'data': {'resultType': 'matrix', 'result': [
                    {'metric': {'__name__': 'up', 'job': f'RANGE_READY_{index}'}, 'values': [[start, '1'], [end, '2']]}]}})
            def instant_ready(request, index):
                timestamp = datetime.datetime.fromisoformat(request['params']['time'][0].replace('Z', '+00:00')).timestamp()
                return (200, {'status': 'success', 'data': {'resultType': 'vector', 'result': [
                    {'metric': {'__name__': 'up', 'job': f'INSTANT_READY_{index}'}, 'value': [timestamp, '1']}]}})
            fixture.routes[('GET', '/prometheus/api/v1/query_range')] = range_ready
            fixture.routes[('GET', '/prometheus/api/v1/query')] = instant_ready
            session = Session(binary, query_args(), fixture)
            fixture.wait('/prometheus/api/v1/query_range')
            session.until('MODE range')
            session.until('RANGE_READY_0')
            # Ctrl+Q plus three Tabs focuses step regardless of result focus.
            def focus_step():
                session.send(b'\x11')
                session.send(b'\t\t\t')
            focus_step()
            instant_wall = time.time()
            session.send(b'\x00')
            instant = fixture.wait('/prometheus/api/v1/query')[0]
            session.until('MODE instant')
            session.until('INSTANT_READY_0')
            evaluation = datetime.datetime.fromisoformat(instant['params']['time'][0].replace('Z', '+00:00')).timestamp()
            assert abs(evaluation - (instant_wall - 3600)) < TIMEOUT, instant
            focus_step()
            session.send(b'\x00')
            ranges = fixture.wait('/prometheus/api/v1/query_range', 2)
            instants = fixture.wait('/prometheus/api/v1/query', 2)
            session.until('MODE both')
            session.until('RANGE_READY_1')
            assert ranges[1]['received'] < instants[1]['received'], fixture.requests
            assert instants[1]['params']['time'] == instant['params']['time'], instants
            assert ranges[1]['params']['end'] == instant['params']['time'], ranges
            start = datetime.datetime.fromisoformat(ranges[1]['params']['start'][0].replace('Z', '+00:00')).timestamp()
            assert abs(evaluation - start - 3600) < 1, ranges
            focus_step()
            range_wall = time.time()
            mark = len(session.output)
            session.send(b'\x00')
            ranges = fixture.wait('/prometheus/api/v1/query_range', 3)
            session.until('MODE range', after=mark)
            session.until('RANGE_READY_2', after=mark)
            end = datetime.datetime.fromisoformat(ranges[2]['params']['end'][0].replace('Z', '+00:00')).timestamp()
            assert abs(end - range_wall) < TIMEOUT, ranges
            assert ranges[0]['params'] == {'query': ['up'], 'stream': ['metrics'], 'step': ['30s'],
                                           'start': ['2024-01-01T00:00:00Z'], 'end': ['2024-01-01T00:10:00Z']}, ranges
            assert ranges[2]['params']['start'] == ranges[1]['params']['start'], ranges
            assert all(r['params']['step'] == ['30s'] for r in ranges), ranges
            assert all(set(r['params']) == {'query', 'stream', 'time'} for r in instants), instants
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            assert_requests(fixture, [('GET', '/api/prism/v1/home'), ('GET', '/prometheus/api/v1/label/__name__/values')] +
                            [('GET', '/prometheus/api/v1/query_range')] * 3 + [('GET', '/prometheus/api/v1/query')] * 2, independent=True)
            assert all(r['params']['query'] == ['up'] and r['params']['stream'] == ['metrics'] for r in ranges + instants)

        elif case in ('saved_apply_result_order', 'saved_apply_text_status', 'saved_apply_json_status', 'saved_profile_snapshot'):
            saved = [{'filter_id': 'saved-id', 'filter_name': 'fixture saved', 'stream_name': 'logs',
                      'query': {'filter_type': 'sql', 'filter_query': 'SELECT event.code FROM logs'}}]
            fixture.routes[('GET', '/api/v1/filters')] = (200, saved)
            failed = case in ('saved_apply_text_status', 'saved_apply_json_status')
            fixture.routes[('POST', '/api/v1/query')] = (500, b'fixture failure\n') if failed else (200, b'[{"fixture_result":7}]')
            args = ['sql', 'list']
            if case == 'saved_apply_json_status':
                # Preserve the public argv scan: any JSON flag selects structured
                # errors, while the final empty output still opens the menu.
                args += ['-ojson', '-o', '']
            if case == 'saved_profile_snapshot':
                records = {'a': {'url': fixture.url, 'apiKey': 'profile-a-key', 'cloud': False},
                           'b': {'url': fixture.url + '/target-b', 'apiKey': 'profile-b-key', 'cloud': False}}
                session = Session(binary, args, fixture, ('a', 'b'), 'a', profile_records=records)
            else:
                session = Session(binary, args, fixture)
            session.until('fixture saved')
            if case == 'saved_profile_snapshot':
                # An independent config edit while the menu is open must not
                # replace the profile captured by command preflight.
                session.config.write_text(session.config.read_text().replace('DefaultProfile = "a"', 'DefaultProfile = "b"'))
                externally_changed = session.config.read_bytes()
            session.send(b'a')
            session.finish(1 if failed else 0)
            printed = b'Query: SELECT "event.code" FROM logs\r\n'
            suffix = printed if failed else printed + b'[{"fixture_result":7}]\r\n'
            assert session.output.endswith(suffix), bytes(session.output[-500:])
            assert session.output.count(printed) == 1, session.text()
            assert session.output.rfind(b'\x1b[?1049l') < session.output.index(printed)
            request = fixture.wait('/api/v1/query')[0]
            assert request['body'] == '{"query":"SELECT \\"event.code\\" FROM logs LIMIT 500","startTime":"1h","endTime":"now"}', request
            if case == 'saved_apply_text_status':
                assert session.error == b'Error: selected saved query failed: query failed: server returned 500 Internal Server Error: fixture failure\n', session.error
            elif case == 'saved_apply_json_status':
                expected = {'error': {'code': 'SERVER_ERROR', 'message': 'query failed: server returned 500 Internal Server Error',
                                      'http_status': 500, 'retryable': True}}
                assert bytes(session.error) == (json.dumps(expected, indent=2) + '\n').encode(), session.error
            if case == 'saved_profile_snapshot':
                assert session.config.read_bytes() == externally_changed, 'apply rewrote the external default change'
                assert all(header(r, 'x-api-key') == 'profile-a-key' for r in fixture.requests), fixture.requests
            else:
                session.unchanged()
            assert_requests(fixture, [('GET', '/api/v1/filters'), ('POST', '/api/v1/query')], check_auth=case != 'saved_profile_snapshot')

        elif case == 'oauth_schema_fallback_scope':
            setup_rotating_refresh(fixture)
            setup_sql(fixture, [{'title': 'logs', 'datasetType': 'logs'}])
            schema_path = '/api/v1/logstream/logs/schema'
            fixture.routes[('GET', schema_path)] = lambda request, index: (
                (401, {}) if header(request, 'cookie') == 'session=fixture-session-0' else
                (404, {'error': 'fixture schema unavailable'}))
            fixture.routes[('POST', '/api/v1/query')] = (
                200, {'fields': ['SCHEMA_SCOPE_S1_COLUMN'], 'records': []})
            session = Session(binary, ['sql', 'run', '-i'], fixture, profile_records=oauth_profile(fixture))
            session.until('SCHEMA_SCOPE_S1_COLUMN')
            schemas = fixture.wait(schema_path, 2)
            query = fixture.wait('/api/v1/query')[0]
            assert [header(r, 'cookie') for r in schemas] == ['session=fixture-session-0', 'session=fixture-session-1'], schemas
            assert header(query, 'cookie') == 'session=fixture-session-1', query
            assert query['params'] == {'fields': ['true']}, query
            wire = json.loads(query['body'])
            assert wire['query'] == "SELECT * FROM 'logs' LIMIT 1", query
            assert wire['startTime'] == '2000-01-01T00:00:00+00:00', query
            assert wire['endTime'].endswith('Z'), query
            datetime.datetime.fromisoformat(wire['endTime'].replace('Z', '+00:00'))
            assert all(header(r, 'content-type') is None for r in schemas), schemas
            assert header(query, 'content-type') == 'application/json', query
            assert_oauth(fixture)
            session.send(b'\x03')
            session.finish()
            assert_requests(fixture, [('GET', '/api/prism/v1/home'), ('GET', schema_path),
                                     ('POST', '/api/v1/cli/oauth/token'), ('GET', schema_path),
                                     ('POST', '/api/v1/query')], check_auth=False)
            profile = session.data()['Profiles']['original']
            assert (profile['session_token'], profile['refresh_token']) == ('fixture-session-1', 'fixture-refresh-1'), profile

        elif case == 'oauth_saved_menu_apply_scope':
            setup_rotating_refresh(fixture)
            saved = [{'filter_id': 'saved-id', 'filter_name': 'fixture saved', 'stream_name': 'logs',
                      'query': {'filter_type': 'sql', 'filter_query': 'SELECT event.code FROM logs'}}]
            fixture.routes[('GET', '/api/v1/filters')] = lambda request, index: (
                (401, {}) if header(request, 'cookie') == 'session=fixture-session-0' else (200, saved))
            fixture.routes[('POST', '/api/v1/query')] = lambda request, index: (
                (401, {}) if header(request, 'cookie') == 'session=fixture-session-0' else
                (200, b'[{"unexpected_shared_session":true}]'))
            session = Session(binary, ['sql', 'list'], fixture, profile_records=oauth_profile(fixture))
            session.until('fixture saved')
            profile = session.data()['Profiles']['original']
            assert (profile['session_token'], profile['refresh_token']) == ('fixture-session-1', 'fixture-refresh-1'), profile
            session.send(b'a')
            session.finish(1)
            filters = fixture.wait('/api/v1/filters', 2)
            query = fixture.wait('/api/v1/query')[0]
            assert [header(r, 'cookie') for r in filters] == ['session=fixture-session-0', 'session=fixture-session-1'], filters
            assert header(query, 'cookie') == 'session=fixture-session-0', query
            assert query['body'] == '{"query":"SELECT \\"event.code\\" FROM logs LIMIT 500","startTime":"1h","endTime":"now"}', query
            printed = b'Query: SELECT "event.code" FROM logs\r\n'
            assert session.output.endswith(printed), bytes(session.output[-500:])
            assert session.output.rfind(b'\x1b[?1049l') < session.output.index(printed)
            assert session.error.startswith(b'Error: selected saved query failed: request execution failed: '), session.error
            assert bytes(session.error).count(b'cloud session refresh failed:') == 2, session.error
            assert b'fixture refresh token already rotated' in session.error, session.error
            assert_oauth(fixture)
            assert_requests(fixture, [('GET', '/api/v1/filters'), ('POST', '/api/v1/cli/oauth/token'),
                                     ('GET', '/api/v1/filters'), ('POST', '/api/v1/query'),
                                     ('POST', '/api/v1/cli/oauth/token')], check_auth=False)
            profile = session.data()['Profiles']['original']
            assert (profile['session_token'], profile['refresh_token']) == ('fixture-session-1', 'fixture-refresh-1'), profile

        elif case in ('oauth_promql_both_effects', 'oauth_sql_window_effects'):
            setup_rotating_refresh(fixture)
            records = oauth_profile(fixture)
            if case == 'oauth_promql_both_effects':
                setup_promql(fixture)
                fixture.routes[('GET', '/prometheus/api/v1/query_range')] = lambda request, index: (
                    (401, {}) if header(request, 'cookie') == 'session=fixture-session-0' else
                    (200, {'status': 'success', 'data': {'resultType': 'matrix', 'result': []}}))
                fixture.routes[('GET', '/prometheus/api/v1/query')] = (401, {})
                session = Session(binary, query_args(''), fixture, profile_records=records)
                fixture.wait('/prometheus/api/v1/label/__name__/values')
                session.until('QUERY')
                session.send(b'\x11')
                session.send(b'\t\t\t')
                session.send(b'\x00')
                session.send(b'\x00')
                session.until('MODE both')
                session.send(b'\x11')
                session.send(b'up')
                session.send(b'\x12')
                refreshes = fixture.wait('/api/v1/cli/oauth/token', 2)
                session.until('cloud session refresh failed')
                ranges = fixture.wait('/prometheus/api/v1/query_range', 2)
                instant = fixture.wait('/prometheus/api/v1/query')[0]
                assert [header(r, 'cookie') for r in ranges] == ['session=fixture-session-0', 'session=fixture-session-1'], ranges
                assert header(instant, 'cookie') == 'session=fixture-session-0', instant
                operations = [(r['method'], r['path']) for r in fixture.requests if r['path'] not in (
                    '/api/prism/v1/home', '/prometheus/api/v1/label/__name__/values')]
                assert operations == [('GET', '/prometheus/api/v1/query_range'), ('POST', '/api/v1/cli/oauth/token'),
                                      ('GET', '/prometheus/api/v1/query_range'), ('GET', '/prometheus/api/v1/query'),
                                      ('POST', '/api/v1/cli/oauth/token')], operations
            else:
                setup_sql(fixture)
                fixture.routes[('POST', '/api/v1/query')] = lambda request, index: (
                    (401, {}) if header(request, 'cookie') == 'session=fixture-session-0' else
                    (200, {'fields': ['value'], 'records': [{'value': f'FIRST_REFRESH_ROW_{i}'} for i in range(500)]}))
                session = Session(binary, ['sql', 'run', 'SELECT * FROM logs LIMIT 750', '-i',
                                          '--from', '2024-01-01T00:00:00Z', '--to', '2024-01-01T01:00:00Z'], fixture,
                                  profile_records=records)
                session.until('FIRST_REFRESH_ROW_0')
                session.send(b'\x16')
                refreshes = fixture.wait('/api/v1/cli/oauth/token', 2)
                session.until('window fetch failed')
                windows = fixture.wait('/api/v1/query', 3)
                assert [header(r, 'cookie') for r in windows] == ['session=fixture-session-0', 'session=fixture-session-1', 'session=fixture-session-0'], windows
                assert [json.loads(r['body'])['query'] for r in windows] == [
                    'SELECT * FROM logs LIMIT 500 OFFSET 0', 'SELECT * FROM logs LIMIT 500 OFFSET 0',
                    'SELECT * FROM logs LIMIT 250 OFFSET 500'], windows
                operations = [(r['method'], r['path']) for r in fixture.requests if r['path'] not in (
                    '/api/prism/v1/home', '/api/v1/logstream/logs/schema')]
                assert operations == [('POST', '/api/v1/query'), ('POST', '/api/v1/cli/oauth/token'),
                                      ('POST', '/api/v1/query'), ('POST', '/api/v1/query'), ('POST', '/api/v1/cli/oauth/token')], operations
            assert len(refreshes) == 2, refreshes
            assert_oauth(fixture)
            session.send(b'\x03')
            session.finish()
            profile = session.data()['Profiles']['original']
            assert (profile['session_token'], profile['refresh_token']) == ('fixture-session-1', 'fixture-refresh-1'), profile

        elif case in deadlines:
            seconds = deadlines[case]
            gate = threading.Event()
            fixture.releases.append(gate)
            if case in ('deadline_schema_15s', 'deadline_sql_50s'):
                setup_sql(fixture)
                fixture.routes[('POST', '/api/v1/query')] = (200, {'fields': ['value'], 'records': [{'value': 'FALLBACK_COLUMN'}]})
                path = '/api/v1/logstream/logs/schema' if case == 'deadline_schema_15s' else '/api/v1/query'
                method = 'GET' if case == 'deadline_schema_15s' else 'POST'
                fixture.routes[(method, path)] = (200, {}, gate)
                args = ['sql', 'run', 'SELECT * FROM logs LIMIT 100', '-i',
                        '--from', '2024-01-01T00:00:00Z', '--to', '2024-01-01T01:00:00Z']
            else:
                setup_promql(fixture)
                path = '/prometheus/api/v1/labels' if case == 'deadline_builder_30s' else '/prometheus/api/v1/query_range'
                fixture.routes[('GET', path)] = (200, {}, gate)
                args = query_args('' if case == 'deadline_builder_30s' else 'up')
            session = Session(binary, args, fixture)
            if case == 'deadline_builder_30s':
                fixture.wait('/prometheus/api/v1/label/__name__/values')
                open_builder(session)
                mark = len(session.output)
                session.send(b'\r')
            else:
                mark = 0
            request = fixture.wait(path)[0]
            if case == 'deadline_schema_15s':
                queries = fixture.wait('/api/v1/query', 2)
                fallback = next(r for r in queries if json.loads(r['body'])['query'].endswith('LIMIT 1'))
                observed = fallback['received']
                assert fallback['received'] > request['received']
            else:
                # Go can report either a context deadline or a transport timer
                # cancellation. Both contain "exceeded"; record the complete
                # frame separately instead of freezing this observed race.
                needle = '(any)' if case == 'deadline_builder_30s' else 'exceeded'
                session.until(needle, after=mark, timeout=seconds + 12)
                observed = time.monotonic()
            elapsed = observed - request['received']
            assert seconds - 1 <= elapsed <= seconds + 12, ('wrong request deadline', seconds, elapsed)
            assert not gate.is_set(), 'server supplied a response before client deadline'
            gate.set()
            fixture.done(path)
            assert request.get('closed_before_response'), 'timed-out request connection remained open'
            print(json.dumps({'deadline_case': case, 'expected_seconds': seconds,
                              'observed_seconds': elapsed, 'held_until_deadline': True}), flush=True)
            if case == 'deadline_builder_30s':
                session.send(b'\x1b')
            session.send(b'\x03')
            session.finish()
            session.unchanged()
            expected = [('GET', '/api/prism/v1/home')]
            if case in ('deadline_schema_15s', 'deadline_sql_50s'):
                expected += [('GET', '/api/v1/logstream/logs/schema'), ('POST', '/api/v1/query')]
                if case == 'deadline_schema_15s':
                    expected += [('POST', '/api/v1/query')]
            else:
                expected += [('GET', '/prometheus/api/v1/label/__name__/values'), ('GET', path)]
            assert_requests(fixture, expected, independent=True)

        else:
            raise AssertionError('unknown case ' + case)
        if case not in ('role_conflict', 'saved_apply_text_status', 'saved_apply_json_status', 'oauth_saved_menu_apply_scope',
                        'sql_unicode_preflight_panic'):
            assert not session.error, session.error
    except BaseException:
        # Emit a complete effect trace even for a bare assertion or a screen
        # timeout. Persisted captures remain separate; this reaches Cargo logs.
        context = {'case': case, 'binary': binary, 'requests': fixture.requests}
        if session:
            context.update(argv=session.child.args, returncode=session.child.poll(),
                           terminal_tail=session.text()[-12000:], stderr=session.error.decode(errors='replace'),
                           config=session.config.read_text() if session.config.exists() else '<deleted>')
        print('EFFECT_FAILURE_CONTEXT=' + json.dumps(context, indent=2), file=sys.stderr, flush=True)
        raise
    finally:
        if session:
            capture_dir = os.environ.get('PB_EFFECTS_CAPTURE_DIR')
            if capture_dir:
                capture = pathlib.Path(capture_dir)
                capture.mkdir(parents=True, exist_ok=True)
                prefix = capture / (case + '-' + str(time.time_ns()))
                prefix.with_suffix('.stdout').write_bytes(session.output)
                prefix.with_suffix('.stderr').write_bytes(session.error)
                prefix.with_suffix('.json').write_text(json.dumps(dict(
                    binary=binary, case=case, passed=session.finished and sys.exc_info()[0] is None,
                    returncode=session.child.poll(), requests=fixture.requests,
                    config=session.config.read_text()), indent=2))
            session.close()
        fixture.close()


if __name__ == '__main__':
    run_case(sys.argv[1], sys.argv[2])
    print('PASS ' + sys.argv[2])
"####;
