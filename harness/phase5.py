#!/usr/bin/env python3
"""Phase-five administration characterization and Go/Rust differential gate.

Each case runs one executable through one or more CLI steps in a fresh HOME
against a disposable loopback fixture that models Parseable's dataset, user and
role endpoints with real state (`phase5.py serve`). Every binary gets its own
fixture lifecycle seeded from the same declared state; setup and reset happen
out of band and never appear in transcripts. Scripted fault routes take
precedence over the model (HTTP errors, malformed bodies, stalls, disconnects
before or after a commit, OAuth 401s and gathered concurrent 401s).

Expectations are independent: request lines/bodies/headers, rendered tables,
success strings, final fixture state and config effects come from this file's
own model, not from a capture. Comparison is exact except for one declared
rule: concurrent administration fan-out (GET user/ID/role, GET role/NAME) is
compared as a multiset within each contiguous fan-out run, and map-derived
role order in list output is compared after canonical sorting. List-before-
detail barriers, membership, multiplicity, headers, bodies and refresh/replay
order stay exact. See contracts/phase5.md.
"""
import argparse
import base64
import collections
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import phase1  # noqa: E402
import phase2  # noqa: E402
import phase3 as p3  # noqa: E402
import run  # noqa: E402

ROOT = HERE
MANIFEST = HERE / 'contracts' / 'phase5-reference.json'
SCHEMA_VERSION = 1
API = '/api/v1/'
TOKEN_PATH = '/orch/api/v1/cli/oauth/token'
OLD = {'cookie': 'session=fixture-session', 'x-p-tenant': 'fixture-tenant'}
NEW = {'cookie': 'session=rotated-session', 'x-p-tenant': 'fixture-tenant'}
USER_FANOUT = r'/api/v1/user/[^/]*/role'
ROLE_FANOUT = r'/api/v1/role/[^/]*'
TIERS = {'gate': 'phase-five gate',
         'full60s': 'real 60-second client deadline; opt-in with --include-full60s'}
STUB_MARKER = phase2.STUB_MARKER
# Response headers are not part of the contract; user-agent is an advisory (phase 3 policy).
IGNORED_REQUEST_HEADERS = ('user-agent',)


# --------------------------------------------------------------------------
# Stateful administration fixture.


def default_password(name):
    return ('pw-' + name).encode()


class Model:
    """Parseable administration state. Mutations are applied once, under the server lock."""

    def __init__(self, state):
        state = copy.deepcopy(state or {})
        self.datasets = state.get('datasets', {})
        self.users = state.get('users', [])
        self.roles = state.get('roles', {})
        self.passwords = {k: base64.b64decode(v) for k, v in state.get('passwords', {}).items()}
        self.role_delete_assigned = state.get('role_delete_assigned', 'reject')

    def snapshot(self):
        return {'datasets': copy.deepcopy(self.datasets), 'users': copy.deepcopy(self.users),
                'roles': copy.deepcopy(self.roles)}

    def user(self, name):
        return next((u for u in self.users if u['id'] == name), None)

    def assigned(self, role):
        return any(role in u.get('roles', []) or any(role in g for g in u.get('groups', {}).values())
                   for u in self.users)

    def detail(self, role):
        return {'actions': self.roles.get(role)}

    def handle(self, method, segments, headers, body):
        """Return (status, body bytes, mutated)."""
        def text(status, message, mutated=False):
            return status, message.encode(), mutated

        def document(value, status=200):
            return status, json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(), False

        def names():
            try:
                value = json.loads(body)
            except ValueError:
                return None
            return value if isinstance(value, list) and all(isinstance(v, str) for v in value) else None

        key = (method, segments[0] if segments else '', len(segments))
        if key == ('GET', 'user', 1):
            return document([{'id': u['id'], 'method': u.get('method', 'native')} for u in self.users])
        if key == ('GET', 'roles', 1):
            return document(self.roles)
        if key == ('GET', 'logstream', 1):
            return document([{'name': name} for name in self.datasets])
        if len(segments) >= 2 and segments[0] == 'user':
            name, user = segments[1], self.user(segments[1])
            if segments[2:] == ['role'] and method == 'GET':
                if user is None:
                    return text(404, 'User does not exist')
                return document({'roles': {r: self.detail(r) for r in user.get('roles', [])},
                                 'group_roles': {g: {r: self.detail(r) for r in roles}
                                                 for g, roles in user.get('groups', {}).items()}})
            if segments[2:] == ['role', 'add'] and method == 'PATCH':
                roles = names()
                if user is None:
                    return text(404, 'User does not exist')
                if roles is None:
                    return text(400, 'invalid role list')
                missing = [r for r in roles if r not in self.roles]
                if missing:
                    return text(400, 'role ' + missing[0] + ' does not exist')
                for role in roles:
                    if role not in user['roles']:
                        user['roles'].append(role)
                return text(200, '', True)
            if len(segments) == 2 and method == 'POST':
                roles = names()
                if user is not None:
                    return text(400, 'User already exists')
                if roles is None:
                    return text(400, 'invalid role list')
                missing = [r for r in roles if r not in self.roles]
                if missing:
                    return text(400, 'role ' + missing[0] + ' does not exist')
                self.users.append({'id': name, 'method': 'native', 'roles': list(dict.fromkeys(roles)), 'groups': {}})
                return 200, self.passwords.get(name, default_password(name)), True
            if len(segments) == 2 and method == 'DELETE':
                if user is None:
                    return text(404, 'User does not exist')
                self.users.remove(user)
                return text(200, 'deleted user', True)
        if len(segments) == 2 and segments[0] == 'role':
            name = segments[1]
            if method == 'GET':
                return document(self.detail(name)) if name in self.roles else text(404, 'role not found')
            if method == 'DELETE':
                if name not in self.roles:
                    return text(404, 'role not found')
                if self.assigned(name) and self.role_delete_assigned == 'reject':
                    return text(400, 'role is assigned to users')
                del self.roles[name]
                return text(200, '', True)
            if method == 'PUT':
                try:
                    self.roles[name] = json.loads(body)
                except ValueError:
                    return text(400, 'invalid privileges')
                return text(200, '', True)
        if len(segments) >= 2 and segments[0] == 'logstream':
            name = segments[1]
            if len(segments) == 2 and method == 'PUT':
                if name in self.datasets:
                    return text(400, 'Logstream ' + name + ' already exists')
                kind = headers.get('x-p-telemetry-type', 'logs')
                entry = {'type': kind}
                if 'x-p-log-source' in headers:
                    entry['source'] = headers['x-p-log-source']
                self.datasets[name] = entry
                return text(200, '', True)
            if len(segments) == 2 and method == 'DELETE':
                if name not in self.datasets:
                    return text(404, 'Stream ' + name + ' not found')
                del self.datasets[name]
                return text(200, 'Log stream deleted', True)
            if segments[2:] == ['info'] and method == 'GET':
                if name not in self.datasets:
                    return text(404, 'Stream ' + name + ' not found')
                entry = self.datasets[name]
                if 'info_raw' in entry:
                    return 200, entry['info_raw'].encode(), False
                return document(entry.get('info', {'createdAt': '2024-01-01T00:00:00Z', 'streamType': 'UserDefined',
                                                   'telemetryType': entry['type']}))
        return None


class AdminServer(p3.FixtureServer):
    """phase3 byte-scripted server plus the administration model and fault decorators."""

    def __init__(self, plan):
        super().__init__(plan)
        self.model = Model(plan.get('state'))
        self.commits = []
        self.tokens = list(plan.get('oauth', {}).get('issue', []))
        self.refresh_token = plan.get('oauth', {}).get('refresh_token')
        self.rejected = set(plan.get('reject_sessions', []))
        self.gather = threading.Condition()
        self.gathered = collections.Counter()

    def _select(self, request):
        path = urllib.parse.unquote(urllib.parse.urlsplit(request['target']).path)
        single = {k: v[-1] for k, v in request['headers'].items()}
        with self.lock:
            for index, route in enumerate(self.routes):
                if route['method'] != request['method']:
                    continue
                if 'path_re' in route:
                    if not re.fullmatch(route['path_re'], path):
                        continue
                elif route['path'] != path:
                    continue
                if not all(single.get(k.lower()) == v for k, v in route.get('headers', {}).items()):
                    continue
                if route.get('times') and self.remaining[index] <= 0:
                    continue
                if route.get('times'):
                    self.remaining[index] -= 1
                return index
        return None

    def _control(self, conn, request):
        if urllib.parse.urlsplit(request['target']).path != '/__harness/state':
            return super()._control(conn, request)
        with self.lock:
            data = json.dumps({'state': self.model.snapshot(), 'commits': self.commits}).encode()
        conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n'
                     b'Content-Length: %d\r\n\r\n' % len(data) + data)

    def _wait_gather(self, index, route, request_number):
        deadline = time.monotonic() + route.get('gather_timeout_ms', 3000) / 1000
        with self.gather:
            self.gathered[index] += 1
            self.gather.notify_all()
            while self.gathered[index] < route['gather']:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.event('gather-timeout', request_number)
                    return
                self.gather.wait(remaining)
        self.event('gathered', request_number)

    def _token(self, request):
        expected = {'authorization': 'Bearer ' + p3.ORCHESTRATOR_TOKEN, 'content-type': 'application/json',
                    'accept': 'application/json'}
        single = {k: v[-1] for k, v in request['headers'].items()}
        try:
            payload = json.loads(request['body'])
        except ValueError:
            payload = None
        if any(single.get(k) != v for k, v in expected.items()) or \
                payload != {'grant_type': 'refresh_token', 'refresh_token': self.refresh_token} or not self.tokens:
            return 400, b'{"error":"invalid_grant"}', False
        issued = self.tokens.pop(0)
        self.refresh_token = issued['refresh_token']
        return 200, json.dumps(issued).encode(), False

    def _model(self, request):
        path = urllib.parse.urlsplit(request['target']).path
        if request['method'] == 'POST' and urllib.parse.unquote(path) == TOKEN_PATH:
            return self._token(request)
        if not path.startswith(API):
            return None
        segments = [urllib.parse.unquote(s) for s in path[len(API):].split('/')]
        single = {k: v[-1] for k, v in request['headers'].items()}
        return self.model.handle(request['method'], segments, single, request['body'])

    def _handle(self, conn, request, listener, number, received):
        index = self._select(request)
        single = {k: ','.join(v) for k, v in request['headers'].items()}
        with self.lock:
            request_number = len(self.trace)
            entry = {'protocol': 'http', 'method': request['method'], 'path': request['target'],
                     'headers': {k: v for k, v in single.items() if k not in IGNORED_REQUEST_HEADERS},
                     'user_agent': single.get('user-agent'), 'header_names': request['names'],
                     'body_b64': base64.b64encode(request['body']).decode(), 'framing': request['framing'],
                     'listener': listener, 'connection': number, 'time_unix_ns': time.time_ns(),
                     'unexpected': False, 'route': index}
            self.trace.append(entry)
            self.milestones.append({'request': request_number, 'route': index, 'received_monotonic_ns': received,
                                    'events': [], 'body_bytes_sent': 0, 'wire_bytes_sent': 0})
        self.event('received', request_number)
        route = self.routes[index] if index is not None else {}
        if route.get('gather'):
            self._wait_gather(index, route, request_number)
        token = request['method'] == 'POST' and urllib.parse.unquote(
            urllib.parse.urlsplit(request['target']).path) == TOKEN_PATH
        required = self.plan.get('headers', {})
        if not token and not all(single.get(k.lower()) == v for k, v in required.items()):
            return self._simple(conn, 401, b'fixture: incorrect authentication\n', request_number)
        if index is not None and not route.get('model'):
            return self._respond(conn, route, self.bodies[index], request_number)
        cookie = single.get('cookie', '')
        if not token and cookie.startswith('session=') and cookie[len('session='):] in self.rejected:
            return self._simple(conn, 401, b'session expired\n', request_number)
        with self.lock:
            outcome = self._model(request)
            if outcome is None:
                entry['unexpected'] = True
            elif outcome[2]:
                self.commits.append({'request': request_number, 'op': request['method'] + ' ' +
                                     urllib.parse.unquote(urllib.parse.urlsplit(request['target']).path)})
        if outcome is None:
            return self._simple(conn, 404, b'fixture: unknown endpoint\n', request_number)
        status, body, mutated = outcome
        if mutated:
            self.event('commit', request_number)
        delivery = {k: v for k, v in route.items() if k not in ('method', 'path', 'path_re', 'headers', 'times', 'model')}
        delivery['status'] = route.get('force_status', status)
        if 'force_body' in route:
            body = p3.as_bytes(route['force_body'])
        delivery.setdefault('content_type', 'application/json' if body[:1] in (b'{', b'[') else 'text/plain; charset=utf-8')
        return self._respond(conn, delivery, body, request_number)


def serve(scenario_path):
    plan = json.loads(Path(scenario_path).read_text())
    server = AdminServer(plan)
    server.start()
    print(json.dumps({**server.urls, 'grpc_port': 0}), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.close()


class AdminFixture(p3.Fixture):
    """`phase5.py serve` child with the phase3 control protocol plus /__harness/state."""

    def __init__(self, scenario, root):
        import selectors
        import urllib.request
        self.proc = None
        path = root / 'scenario.json'
        run.write_json(path, scenario)
        self.log = (root / 'fixture.stderr').open('wb')
        self.proc = subprocess.Popen([sys.executable, '-I', str(Path(__file__).resolve()), 'serve', '--scenario', str(path)],
                                     stdout=subprocess.PIPE, stderr=self.log, start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise RuntimeError('phase5 fixture did not become ready within 10s')
            self.urls = json.loads(self.proc.stdout.readline())
            self.urls.pop('grpc_port', None)
            self.url = self.urls['url']
        except Exception as error:
            self.close()
            raise RuntimeError(f'phase5 fixture startup failed: {error}') from error
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def state(self):
        return self.control('state')


# --------------------------------------------------------------------------
# Sampling: one fixture lifecycle and one HOME per (binary, case); steps run in order.


def request_contract(trace, replacements):
    return [{'method': e['method'], 'target': run.normalize(e['path'].encode(), replacements).decode(),
             'headers': {k: run.normalize(v.encode(), replacements).decode() for k, v in sorted(e['headers'].items())},
             'body_b64': e['body_b64'], 'framing': e['framing']} for e in trace]


def sample(binary, case, out_dir, timeout):
    out_dir.mkdir(parents=True)
    tmp_root = Path(tempfile.mkdtemp(prefix='pb-phase5-'))
    steps = []
    try:
        home = tmp_root / 'home'
        for name in ('config', 'cache', 'data', 'state', 'tmp'):
            (home / name).mkdir(parents=True)
        fixture_root = tmp_root / 'fixture'
        fixture_root.mkdir()
        fixture = AdminFixture(case['fixture'], fixture_root)
        try:
            urls = fixture.urls
            phase2.seed(home, case, urls['url'])
            replacements = p3.replacements_for(home, fixture)
            config_before = phase2.config_state(home, replacements)
            state_before = fixture.state()
            seen = 0
            for index, step in enumerate(case['steps']):
                env = {k: p3.substitute(v, urls) for k, v in phase2.isolated_env(
                    home, {'env': {**case.get('env', {}), **step.get('env', {})}, 'unset': case.get('unset', [])}).items()}
                argv = [str(binary), *[p3.substitute(a, urls) for a in step['argv']]]
                folder = out_dir / f'step-{index}'
                folder.mkdir()
                result = p3.execute(argv, env, home, step, fixture, folder / 'stdout.bin',
                                    step.get('timeout', case.get('timeout', timeout)))
                trace = fixture.trace()
                result['trace'], seen = trace[seen:], len(trace)
                result['state_after'] = fixture.state()
                result['requests'] = request_contract(result['trace'], replacements)
                stdout = result['stdout']
                result['stdout_normalized'] = run.normalize(stdout, replacements) if stdout is not None else None
                result['stderr_normalized'] = run.normalize(result['stderr'], replacements)
                (folder / 'stderr.bin').write_bytes(result['stderr'])
                run.write_json(folder / 'trace.json', result['trace'])
                steps.append(result)
            events = fixture.events()
            milestones = fixture.milestones()
            config_after = phase2.config_state(home, replacements)
        finally:
            fixture.close()
    finally:
        for path in sorted(tmp_root.rglob('*'), reverse=True):
            try:
                if path.is_dir() and not path.is_symlink():
                    path.chmod(0o700)
            except OSError:
                pass
        shutil.rmtree(tmp_root, ignore_errors=True)
    result = {'binary': str(binary), 'steps': steps, 'state_before': state_before, 'config_before': config_before,
              'config_after': config_after, 'events': events, 'milestones': milestones}
    run.write_json(out_dir / 'result.json', public(result))
    return result


def public(result):
    keep = ('termination', 'failure', 'wall_ms', 'stdout_sha256', 'stdout_bytes', 'requests', 'state_after', 'actions')
    return {'binary': result['binary'], 'state_before': result['state_before'],
            'config_before': result['config_before'], 'config_after': result['config_after'],
            'gather_timeouts': sum(e['name'] == 'gather-timeout' for e in result['events']),
            'steps': [{**{k: s.get(k) for k in keep}, 'stderr_sha256': run.digest(s['stderr_normalized']),
                       'raw_request_order': [r['method'] + ' ' + r['target'] for r in s['requests']]}
                      for s in result['steps']]}


# --------------------------------------------------------------------------
# Declared nondeterminism: fan-out arrival order and map-derived role order only.


def path_of(target):
    return urllib.parse.unquote(urllib.parse.urlsplit(target).path)


def fanout_canonical(items, patterns, line=lambda item: item):
    """Sort each maximal contiguous run of GET fan-out requests; everything else keeps its position."""
    def member(item):
        method, _, target = line(item).partition(' ')
        return method == 'GET' and any(re.fullmatch(p, path_of(target)) for p in patterns)
    out, run_ = [], []
    for item in items:
        if patterns and member(item):
            run_.append(item)
            continue
        out.extend(sorted(run_, key=lambda r: json.dumps(r, sort_keys=True)))
        run_ = []
        out.append(item)
    out.extend(sorted(run_, key=lambda r: json.dumps(r, sort_keys=True)))
    return out


def contract_line(item):
    return item['method'] + ' ' + item['target']


LEADER = re.compile(rb'^(.*? \.{2,} )(.*)$')


def canon_user_text(data):
    lines = data.split(b'\n')
    for i, line in enumerate(lines):
        match = LEADER.match(line) if i >= 3 else None
        if match and not match.group(2).startswith(b'error: ') and match.group(2) != b'-':
            lines[i] = match.group(1) + b', '.join(sorted(match.group(2).split(b', ')))
    return b'\n'.join(lines)


ROW_START = re.compile(rb'^\S.*? \.{2,} ')


def canon_role_text(data):
    """Sort role blocks (map order). A block is a role's first row plus continuation lines: rows with a
    blank role cell and lines of a multi-line cell that lipgloss rendered as a padded block."""
    lines = data.split(b'\n')
    if len(lines) < 4 or lines[0] != b'':
        return data
    head, body, tail = lines[:3], lines[3:-2], lines[-2:]
    blocks = []
    for line in body:
        if ROW_START.match(line) or not blocks:
            blocks.append([line])
        else:
            blocks[-1].append(line)
    return b'\n'.join(head + [l for block in sorted(blocks) for l in block] + tail)


def canon_user_json(data):
    try:
        value = json.loads(data)
        if go_json(value) != data:
            return data
        for item in value:
            if isinstance(item.get('roles'), list):
                item['roles'] = sorted(item['roles'])
        return go_json(value)
    except (ValueError, AttributeError, TypeError):
        return data


AGGREGATE = re.compile(rb'failed to fetch details for \d+ role\(s\): ')
ROLE_ERROR = b'Error fetching role data for '


def canon_role_errors(data):
    """Sort the per-role causes of the aggregate role-list error (they follow map order).

    Text mode: the message runs to the final newline and causes may contain raw newlines.
    JSON mode: the message is one escaped string ending at the first '",' line end."""
    match = AGGREGATE.search(data)
    if not match:
        return data
    start = match.end()
    if data.lstrip().startswith(b'{'):
        end = data.find(b'",\n', start)
    else:
        end = len(data) - 1 if data.endswith(b'\n') else len(data)
    if end < start or not data[start:end].startswith(ROLE_ERROR):
        return data
    causes = data[start + len(ROLE_ERROR):end].split(b'; ' + ROLE_ERROR)
    return data[:start] + ROLE_ERROR + (b'; ' + ROLE_ERROR).join(sorted(causes)) + data[end:]


STDOUT_CANON = {'user-text': canon_user_text, 'role-text': canon_role_text, 'user-json': canon_user_json}


def canon_stdout(case, data):
    mode = case.get('compare', {}).get('stdout')
    return data if data is None or mode is None else STDOUT_CANON[mode](data)


def normalize_timeout(case, data):
    # Repeated pinned-Go runs of this body deadline return either the bare
    # context error or net/http's annotated error. Preserve the usage tail.
    prefix = b'Error: context deadline exceeded\nUsage:\n  pb user add '
    if (case['name'] == 'p5-user-add-stalled-body-deadline'
            and case.get('tier') == 'full60s' and case.get('compare', {}).get('timeout_cause')
            and data.startswith(prefix)):
        data = (b'Error: context deadline exceeded (Client.Timeout or context cancellation while reading body)\nUsage:\n  pb user add '
                + data[len(prefix):])
    return p3.normalize_timeout(case, data)


def canon_stderr(case, data):
    options = case.get('compare', {})
    if options.get('stderr') == 'role-errors':
        data = canon_role_errors(data)
    if options.get('stderr_unordered_lines'):
        data = b''.join(sorted(data.splitlines(True)))
    return p3.compare_stderr({'compare': {k: v for k, v in options.items() if k in ('client_port',)}}, data)


def canon_requests(case, requests):
    return fanout_canonical(requests, case.get('compare', {}).get('fanout', []), contract_line)


# --------------------------------------------------------------------------
# Independent Go renderer model (lipgloss widths, Go string formats).


def width(text):
    total = 0
    for ch in text:
        if unicodedata.combining(ch) or unicodedata.category(ch) in ('Mn', 'Me', 'Cf', 'Cc'):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
    return total


def render(text):
    """lipgloss Style.Render expands tabs to four spaces; Width counted them as zero."""
    return text.replace('\t', '    ')


def truncate(value, limit):
    if width(value) <= limit:
        return value
    if limit <= 1:
        return '…'
    out = ''
    for ch in value:
        if width(out + ch) + 1 > limit:
            break
        out += ch
    return out + '…'


def user_table(rows):
    """rows: [(user id, roles list | None for '-', error text | None)] in user order."""
    user_width = max([4] + [min(width(u), 64) for u, _, _ in rows])
    column = user_width + 4
    out = ['', 'USER'.ljust(column) + 'ROLES', '─' * column + '─' * 5]
    for user, roles, error in rows:
        cell = truncate(user, 64)
        text = 'error: ' + error if error is not None else (', '.join(roles) if roles else '-')
        out.append(render(cell) + ' ' + '.' * max(2, column - width(cell) - 1) + ' ' + render(text))
    return ('\n'.join(out) + '\n\n').encode()


def dataset_of(action):
    resource = action.get('resource') or {}
    if not action.get('resource'):
        return '-'
    return resource.get('dataset') or resource.get('stream') or '-'


def role_table(roles):
    """roles: [(name, actions list | None, error text | None)]."""
    role_width = max([4] + [min(width(n), 42) for n, _, _ in roles])
    privilege_width = max([9] + [width(a.get('privilege', '')) for _, actions, _ in roles for a in actions or []])
    stream_width = max([7] + [min(width(dataset_of(a)), 48) for _, actions, _ in roles for a in actions or []])
    pcol = role_width + 4
    scol = pcol + privilege_width + 4

    def row(role_cell, privilege, stream):
        guide = ' ' * pcol if role_cell == '' else render(role_cell) + ' ' + '.' * max(2, pcol - width(role_cell) - 1)
        privilege = truncate(privilege, privilege_width)
        return (guide + ' ' + render(privilege) + ' ' + '.' * max(2, scol - pcol - width(privilege) - 1) + ' ' +
                render(truncate(stream, 48)))
    out = ['', 'ROLE'.ljust(pcol + 1) + 'PRIVILEGE'.ljust(privilege_width + 5) + 'DATASET',
           '─' * (pcol + 1) + '─' * (privilege_width + 5) + '─' * stream_width]
    for name, actions, error in roles:
        if error is not None:
            out.append(row(truncate(name, 42), '-', 'error: ' + error))
        elif not actions:
            out.append(row(truncate(name, 42), '-', '-'))
        else:
            for i, action in enumerate(actions):
                privilege = action.get('privilege', '')
                out.append(row(truncate(name, 42) if i == 0 else '', privilege if privilege.strip() else '-',
                               dataset_of(action)))
    return ('\n'.join(out) + '\n\n').encode()


def go_json(value):
    return (json.dumps(value, indent=2, ensure_ascii=False).replace('\u2028', '\\u2028').replace('\u2029', '\\u2029') + '\n').encode()


def role_json(roles):
    def action(a):
        out = {'privilege': a.get('privilege', '')}
        resource = {k: a['resource'][k] for k in ('dataset', 'tag', 'stream') if (a.get('resource') or {}).get(k)}
        if a.get('resource') is not None:
            out['resource'] = resource
        return out
    return go_json({name: None if actions is None else [action(a) for a in actions]
                    for name, actions in sorted(roles.items())})


def flattened_roles(state, user):
    """Direct then group roles with multiplicity (order canonicalized by the comparator)."""
    return list(user.get('roles', [])) + [r for g in user.get('groups', {}).values() for r in g]


# --------------------------------------------------------------------------
# Corpus.

ROLES = {
    'admin': [{'privilege': 'admin'}],
    'editor': [{'privilege': 'editor'}],
    'reader': [{'privilege': 'reader', 'resource': {'dataset': 'app'}}],
    'ingestor': [{'privilege': 'ingestor', 'resource': {'stream': 'legacy'}}],
    'unused': [{'privilege': 'writer', 'resource': {'dataset': 'app', 'tag': 'blue'}},
               {'privilege': 'reader', 'resource': {'dataset': 'new', 'stream': 'old'}}],
}
USERS = [
    {'id': 'alice', 'method': 'native', 'roles': ['admin'], 'groups': {}},
    {'id': 'bob', 'method': 'native', 'roles': ['reader'], 'groups': {'ops': ['editor']}},
]
DATASETS = {'app': {'type': 'logs'}, 'cpu': {'type': 'metrics', 'source': 'otel-metrics'},
            'spans': {'type': 'traces', 'source': 'otel-traces'}}
SEED = {'datasets': DATASETS, 'users': USERS, 'roles': ROLES}
KINDS = ('logs', 'metrics', 'traces')


def seed(**changes):
    state = copy.deepcopy(SEED)
    state.update(copy.deepcopy(changes))
    return state


def snapshot_of(state):
    return {'datasets': state.get('datasets', {}), 'users': state.get('users', []), 'roles': state.get('roles', {})}


def json_error(code, status=None):
    detail = {'code': code}
    if status is not None:
        detail['http_status'] = status
    return detail


def corpus():
    cases = []

    def add(name, steps, state=None, group='admin', auth='basic', routes=(), final=None, compare=None, build='standard',
            tier='gate', config=None, fixture_extra=None, **extra):
        plan = {'headers': p3.HEADERS.get(auth, {}), 'routes': [dict(r) for r in routes],
                'state': copy.deepcopy(SEED if state is None else state)}
        plan.update(fixture_extra or {})
        initial = snapshot_of(plan['state'])
        case = {'name': 'p5-' + name, 'group': group, 'tier': tier, 'build': build, 'requires': [],
                'config': config or {'auth': auth}, 'location': 'xdg', 'env': {}, 'unset': [], 'fixture': plan,
                'steps': [dict(s) for s in steps], 'compare': compare or {},
                'final_state': initial if final is None else final, **extra}
        cases.append(case)
        return case

    def step(argv, exit=0, requests=(), stdout=None, stderr_has=(), error=None, bodies=None, headers=None, **more):
        expect = {'termination': {'exit': exit} if isinstance(exit, int) else exit,
                  'requests': None if requests is None else list(requests)}
        if stdout is not None:
            expect['stdout_exact'] = p3.pin(stdout.encode() if isinstance(stdout, str) else stdout)
        if exit and stdout is None and not more.pop('allow_stdout', False):
            expect['stdout_exact'] = ''
        if stderr_has:
            expect['stderr_has'] = [p3.pin(s if isinstance(s, bytes) else s.encode()) for s in stderr_has]
        if error is not None:
            expect['json_error'] = error
        if bodies:
            expect['request_bodies'] = {str(k): v for k, v in bodies.items()}
        if headers:
            expect['request_headers'] = {str(k): v for k, v in headers.items()}
        for key in ('max_wall_ms', 'min_wall_ms', 'commits'):
            if key in more:
                expect[key] = more.pop(key)
        return {'argv': list(argv), 'expect': expect, **more}

    def state_with(**changes):
        state = snapshot_of(copy.deepcopy(SEED))
        for key, value in changes.items():
            state[key] = value
        return state

    def without_dataset(name, base=None):
        datasets = copy.deepcopy((base or SEED)['datasets'])
        datasets.pop(name)
        return datasets

    U, R = API + 'user', API + 'roles'

    # ---- dataset creation ------------------------------------------------
    for kind in KINDS:
        source = None if kind == 'logs' else 'otel-' + kind
        entry = {'type': kind, **({'source': source} if source else {})}
        add('dataset-add-' + kind, [step(['dataset', 'add', 'new', '--type', kind], requests=['PUT /api/v1/logstream/new'],
                                         stdout=f'Created {kind} dataset new\n', commits=1,
                                         headers={0: {'x-p-telemetry-type': kind, 'x-p-log-source': source,
                                                      'content-type': 'application/json'}})],
            final=state_with(datasets={**DATASETS, 'new': entry}), group='dataset')
    add('dataset-add-normalized-type', [step(['dataset', 'add', 'new', '--type=  MeTrIcS '],
                                             requests=['PUT /api/v1/logstream/new'], stdout='Created metrics dataset new\n',
                                             headers={0: {'x-p-telemetry-type': 'metrics', 'x-p-log-source': 'otel-metrics'}})],
        final=state_with(datasets={**DATASETS, 'new': {'type': 'metrics', 'source': 'otel-metrics'}}), group='dataset')
    for label, value in [('invalid', 'events'), ('uppercase-invalid', 'LOG'), ('internal-space', 'me trics')]:
        add('dataset-add-type-' + label, [step(['dataset', 'add', 'new', '--type', value], exit=1,
                                               stderr_has=['invalid dataset type'])], group='dataset')
    add('dataset-add-existing', [step(['dataset', 'add', 'app', '--type', 'logs'], exit=1, requests=['PUT /api/v1/logstream/app'],
                                      stderr_has=['create dataset failed: 400 Bad Request: Logstream app already exists'])],
        group='dataset')
    for status in (201, 204, 500):
        body = '' if status == 204 else '  server says no \n'
        add('dataset-add-status-' + str(status), [step(['dataset', 'add', 'new', '--type', 'traces'], exit=1,
                                                       requests=['PUT /api/v1/logstream/new'],
                                                       stderr_has=['create dataset failed: ' + str(status)] +
                                                       ([': server says no'] if body else []), commits=1)],
            routes=[{'method': 'PUT', 'path': API + 'logstream/new', 'model': True, 'force_status': status,
                     'force_body': body}],
            final=state_with(datasets={**DATASETS, 'new': {'type': 'traces', 'source': 'otel-traces'}}), group='dataset')
    add('dataset-add-unencoded-name', [step(['dataset', 'add', 'a b?c', '--type', 'logs'],
                                            requests=['PUT /api/v1/logstream/a b?c'], stdout='Created logs dataset a b?c\n')],
        final=state_with(datasets={**DATASETS, 'a b?c': {'type': 'logs'}}), group='dataset')
    add('dataset-add-stalled-success-body', [step(['dataset', 'add', 'new', '--type', 'logs'], requests=['PUT /api/v1/logstream/new'],
                                                  stdout='Created logs dataset new\n', max_wall_ms=5000)],
        routes=[{'method': 'PUT', 'path': API + 'logstream/new', 'model': True, 'force_body': 'pending body',
                 'framing': 'chunked', 'script': [{'send': 3}, {'stall': True}]}],
        final=state_with(datasets={**DATASETS, 'new': {'type': 'logs'}}), group='consumption')

    # ---- dataset removal -------------------------------------------------
    add('dataset-remove-unguarded', [step(['dataset', 'remove', 'app'], requests=['DELETE /api/v1/logstream/app'],
                                          stdout='Successfully deleted dataset app\n', commits=1)],
        final=state_with(datasets=without_dataset('app')), group='dataset')
    add('dataset-rm-alias', [step(['dataset', 'rm', 'cpu'], requests=['DELETE /api/v1/logstream/cpu'],
                                  stdout='Successfully deleted dataset cpu\n')],
        final=state_with(datasets=without_dataset('cpu')), group='dataset')
    for kind, name in (('logs', 'app'), ('metrics', 'cpu'), ('traces', 'spans')):
        add('dataset-remove-guarded-' + kind, [step(['dataset', 'remove', name, '--type', ' ' + kind.upper()],
                                                    requests=['GET /api/v1/logstream/' + name + '/info',
                                                              'DELETE /api/v1/logstream/' + name],
                                                    stdout=f'Successfully deleted dataset {name}\n')],
            final=state_with(datasets=without_dataset(name)), group='dataset')
        wrong = 'logs' if kind != 'logs' else 'traces'
        add('dataset-remove-mismatch-' + kind, [step(['dataset', 'remove', name, '--type', wrong], exit=1,
                                                     requests=['GET /api/v1/logstream/' + name + '/info'],
                                                     stderr_has=[f'dataset "{name}" is {kind}, not {wrong}'])], group='dataset')
    add('dataset-remove-blank-type-unguarded', [step(['dataset', 'remove', 'app', '--type', '  '],
                                                     requests=['DELETE /api/v1/logstream/app'],
                                                     stdout='Successfully deleted dataset app\n')],
        final=state_with(datasets=without_dataset('app')), group='dataset')
    add('dataset-remove-invalid-type', [step(['dataset', 'remove', 'app', '--type', 'events'], exit=1,
                                             stderr_has=['invalid dataset type "events"'])], group='dataset')
    add('dataset-remove-missing-unguarded', [step(['dataset', 'remove', 'ghost'], exit=1, requests=['DELETE /api/v1/logstream/ghost'],
                                                  stderr_has=['delete dataset failed: 404 Not Found: Stream ghost not found'])],
        group='dataset')
    add('dataset-remove-missing-guarded', [step(['dataset', 'remove', 'ghost', '--type', 'logs'], exit=1,
                                                requests=['GET /api/v1/logstream/ghost/info'],
                                                stderr_has=['dataset "ghost" not found'])], group='dataset')
    info_cases = [
        ('legacy-stream-type', '{"streamType":"metrics"}', 'metrics', True),
        ('empty-telemetry-falls-back', '{"telemetryType":"","streamType":"logs"}', 'logs', True),
        ('telemetry-wins', '{"telemetryType":"traces","streamType":"logs"}', 'traces', True),
        ('case-varied-key', '{"TELEMETRYTYPE":"metrics"}', 'metrics', True),
        ('duplicate-key-last-wins', '{"telemetryType":"logs","telemetryType":"metrics"}', 'metrics', True),
        ('actual-not-lowercased', '{"telemetryType":"Metrics"}', 'metrics', False),
        ('null-body', 'null', 'logs', False),
        ('unknown-fields', '{"x":[1,{"y":null}],"telemetryType":"logs"}', 'logs', True),
    ]
    for label, raw, expected_type, matches in info_cases:
        state = seed(datasets={**DATASETS, 'odd': {'type': 'logs', 'info_raw': raw}})
        requests = ['GET /api/v1/logstream/odd/info'] + (['DELETE /api/v1/logstream/odd'] if matches else [])
        add('dataset-info-' + label, [step(['dataset', 'remove', 'odd', '--type', expected_type], exit=0 if matches else 1,
                                           requests=requests,
                                           stdout='Successfully deleted dataset odd\n' if matches else None,
                                           stderr_has=[] if matches else ['dataset "odd" is '])],
            state=state, final=snapshot_of({**state, 'datasets': DATASETS} if matches else state), group='dataset')
    for label, raw, needle in [('malformed', '{"telemetryType":', 'failed to unmarshal response'),
                               ('wrong-type', '{"telemetryType":7}', 'failed to unmarshal response')]:
        state = seed(datasets={**DATASETS, 'odd': {'type': 'logs', 'info_raw': raw}})
        add('dataset-info-' + label, [step(['dataset', 'remove', 'odd', '--type', 'logs'], exit=1,
                                           requests=['GET /api/v1/logstream/odd/info'], stderr_has=[needle])],
            state=state, group='dataset')
    for status in (403, 500):
        add('dataset-info-status-' + str(status), [step(['dataset', 'remove', 'app', '--type', 'logs'], exit=1,
                                                        requests=['GET /api/v1/logstream/app/info'])],
            routes=[p3.route(b'denied', status, method='GET', path=API + 'logstream/app/info')], group='dataset')
    add('dataset-remove-stalled-success-body', [step(['dataset', 'remove', 'app'], requests=['DELETE /api/v1/logstream/app'],
                                                     stdout='Successfully deleted dataset app\n', max_wall_ms=5000)],
        routes=[{'method': 'DELETE', 'path': API + 'logstream/app', 'model': True, 'framing': 'chunked',
                 'script': [{'send': 2}, {'stall': True}]}],
        final=state_with(datasets=without_dataset('app')), group='consumption')
    add('dataset-remove-error-body-trimmed', [step(['dataset', 'remove', 'app'], exit=1, requests=['DELETE /api/v1/logstream/app'],
                                                   stderr_has=['delete dataset failed: 503 Service Unavailable: busy'])],
        routes=[p3.route(b'\n  busy \t\n', 503, method='DELETE', path=API + 'logstream/app')], group='dataset')
    add('dataset-remove-dotdot-not-encoded', [step(['dataset', 'remove', '../user'], exit=1, requests=['DELETE /api/v1/user'],
                                                   allow_unexpected=True)], group='dataset')

    # ---- user creation ---------------------------------------------------
    add('user-add', [step(['user', 'add', 'carol', '--role', ' reader , editor '],
                          requests=['GET ' + U, 'GET ' + R, 'POST /api/v1/user/carol'],
                          stdout='Added user: carol \nPassword is: pw-carol\nRole(s) assigned: reader , editor\n',
                          bodies={2: ['reader', 'editor']}, commits=1)],
        final=state_with(users=USERS + [{'id': 'carol', 'method': 'native', 'roles': ['reader', 'editor'], 'groups': {}}]),
        group='user')
    add('user-add-short-flag-duplicates', [step(['user', 'add', 'dave', '-r', 'reader,reader'],
                                                requests=['GET ' + U, 'GET ' + R, 'POST /api/v1/user/dave'],
                                                stdout='Added user: dave \nPassword is: pw-dave\nRole(s) assigned: reader,reader\n',
                                                bodies={2: ['reader', 'reader']})],
        final=state_with(users=USERS + [{'id': 'dave', 'method': 'native', 'roles': ['reader'], 'groups': {}}]), group='user')
    add('user-add-empty-component', [step(['user', 'add', 'dave', '--role', 'reader,,editor'], exit=1,
                                          requests=['GET ' + U, 'GET ' + R], stderr_has=["role  doesn't exist"])], group='user')
    add('user-add-first-missing-role-stops', [step(['user', 'add', 'dave', '--role', 'ghost,phantom'], exit=1,
                                                   requests=['GET ' + U, 'GET ' + R],
                                                   stderr_has=["role ghost doesn't exist; create it first with `pb role add ghost`"])],
        group='user')
    add('user-add-duplicate-before-roles', [step(['user', 'add', 'alice', '--role', 'ghost'], exit=1, requests=['GET ' + U],
                                                 stderr_has=['user alice already exists'])], group='user')
    add('user-add-no-role-guidance', [step(['user', 'add', 'dave'], exit=1, requests=['GET ' + U, 'GET ' + R],
                                           stderr_has=['Available roles: admin, editor, ingestor, reader, unused',
                                                       '`pb user add dave --role admin`'])], group='user')
    add('user-add-blank-role-guidance', [step(['user', 'add', 'dave', '--role', '   '], exit=1, requests=['GET ' + U, 'GET ' + R],
                                              stderr_has=['a role is required to create user dave'])], group='user')
    empty = seed(roles={}, users=[])
    add('user-add-no-roles-available', [step(['user', 'add', 'dave'], exit=1, requests=['GET ' + U, 'GET ' + R],
                                              stderr_has=['No roles are available'])], state=empty, group='user')
    for auth in ('cloud-key', 'cloud-cookie'):
        add('user-add-cloud-rejected-' + auth, [step(['user', 'add', 'dave', '--role', 'reader'], exit=1,
                                                     stderr_has=['`pb user add` is not available for Parseable Cloud'])],
            auth=auth, group='user')
        add('user-add-output-flag-unknown-' + auth, [step(['user', 'add', 'dave', '--role', 'reader', '-o', 'json'], exit=1,
                                                          allow_stdout=True)], auth=auth, group='user')
    add('user-add-binary-password', [step(['user', 'add', 'erin', '--role', 'admin'],
                                          requests=['GET ' + U, 'GET ' + R, 'POST /api/v1/user/erin'],
                                          stdout=b'Added user: erin \nPassword is: \xff\xfe pw\n\x00\nRole(s) assigned: admin\n')],
        state=seed(passwords={'erin': base64.b64encode(b'\xff\xfe pw\n\x00').decode()}),
        final=state_with(users=USERS + [{'id': 'erin', 'method': 'native', 'roles': ['admin'], 'groups': {}}]), group='user')
    add('user-add-server-rejects', [step(['user', 'add', 'carol', '--role', 'admin'], exit=1,
                                         requests=['GET ' + U, 'GET ' + R, 'POST /api/v1/user/carol'],
                                         stderr_has=['create user failed: 409 Conflict: taken'])],
        routes=[p3.route(b' taken ', 409, method='POST', path=API + 'user/carol')], group='user')
    add('user-add-201-reads-body', [step(['user', 'add', 'carol', '--role', 'admin'], exit=1,
                                         requests=['GET ' + U, 'GET ' + R, 'POST /api/v1/user/carol'],
                                         stderr_has=['create user failed: 201 Created: pw-carol'])],
        routes=[{'method': 'POST', 'path': API + 'user/carol', 'model': True, 'force_status': 201}],
        final=state_with(users=USERS + [{'id': 'carol', 'method': 'native', 'roles': ['admin'], 'groups': {}}]), group='user')
    add('user-add-slow-body-read-fully', [step(['user', 'add', 'carol', '--role', 'admin'],
                                               requests=['GET ' + U, 'GET ' + R, 'POST /api/v1/user/carol'],
                                               stdout='Added user: carol \nPassword is: pw-carol\nRole(s) assigned: admin\n',
                                               min_wall_ms=600)],
        routes=[{'method': 'POST', 'path': API + 'user/carol', 'model': True, 'framing': 'chunked',
                 'script': [{'send': 3}, {'delay_ms': 700}, {'send': 'rest'}]}],
        final=state_with(users=USERS + [{'id': 'carol', 'method': 'native', 'roles': ['admin'], 'groups': {}}]),
        group='consumption')
    add('user-add-truncated-body', [step(['user', 'add', 'carol', '--role', 'admin'], exit=1,
                                         requests=['GET ' + U, 'GET ' + R, 'POST /api/v1/user/carol'],
                                         stderr_has=['unexpected EOF'])],
        routes=[{'method': 'POST', 'path': API + 'user/carol', 'model': True, 'framing': 'length', 'content_length': 64,
                 'script': [{'send': 'rest'}, {'abort': 'fin'}]}],
        final=state_with(users=USERS + [{'id': 'carol', 'method': 'native', 'roles': ['admin'], 'groups': {}}]),
        group='consumption')
    add('user-add-stalled-body-deadline', [step(['user', 'add', 'carol', '--role', 'admin'], exit=1,
                                                requests=['GET ' + U, 'GET ' + R, 'POST /api/v1/user/carol'],
                                                stderr_has=['Client.Timeout'], min_wall_ms=59000, timeout=80)],
        routes=[{'method': 'POST', 'path': API + 'user/carol', 'model': True, 'framing': 'chunked',
                 'script': [{'send': 2}, {'stall': True}]}],
        final=state_with(users=USERS + [{'id': 'carol', 'method': 'native', 'roles': ['admin'], 'groups': {}}]),
        group='consumption', tier='full60s', compare={'timeout_cause': True})
    for label, body in [('malformed', b'[{"id":'), ('object', b'{}'), ('wrong-id-type', b'[{"id":1}]')]:
        add('user-add-users-' + label, [step(['user', 'add', 'carol', '--role', 'admin'], exit=1, requests=['GET ' + U])],
            routes=[p3.route(body, method='GET', path=U)], group='user')
    add('user-add-users-null', [step(['user', 'add', 'carol', '--role', 'admin'],
                                     requests=['GET ' + U, 'GET ' + R, 'POST /api/v1/user/carol'],
                                     stdout='Added user: carol \nPassword is: pw-carol\nRole(s) assigned: admin\n')],
        routes=[p3.route(b'null', method='GET', path=U)],
        final=state_with(users=USERS + [{'id': 'carol', 'method': 'native', 'roles': ['admin'], 'groups': {}}]), group='user')
    add('user-add-case-varied-id', [step(['user', 'add', 'alice', '--role', 'admin'], exit=1, requests=['GET ' + U],
                                         stderr_has=['user alice already exists'])],
        routes=[p3.route(b'[{"ID":"alice","Method":"x","extra":true}]', method='GET', path=U)], group='user')
    add('user-add-roles-status', [step(['user', 'add', 'carol', '--role', 'admin'], exit=1, requests=['GET ' + U, 'GET ' + R],
                                       stderr_has=['request failed'])],
        routes=[p3.route(b'nope', 500, method='GET', path=R)], group='user')

    # ---- user removal ----------------------------------------------------
    add('user-remove', [step(['user', 'remove', 'bob'], requests=['DELETE /api/v1/user/bob'], stdout='Removed user bob\n', commits=1,
                             headers={0: {'content-type': 'application/json', 'content-length': None}})],
        final=state_with(users=USERS[:1]), group='user')
    add('user-rm-missing', [step(['user', 'rm', 'ghost'], exit=1, requests=['DELETE /api/v1/user/ghost'],
                                 stderr_has=['delete user failed: 404 Not Found: User does not exist'])], group='user')
    add('user-remove-stalled-success-body', [step(['user', 'remove', 'bob'], requests=['DELETE /api/v1/user/bob'],
                                                  stdout='Removed user bob\n', max_wall_ms=5000)],
        routes=[{'method': 'DELETE', 'path': API + 'user/bob', 'model': True, 'framing': 'chunked',
                 'script': [{'send': 2}, {'stall': True}]}], final=state_with(users=USERS[:1]), group='consumption')
    add('user-remove-error-json-argv', [step(['user', 'remove', 'ghost', '-o', 'json'], exit=1, allow_stdout=True)], group='user')

    # ---- set-role ----------------------------------------------------------
    bob_admin = copy.deepcopy(USERS)
    bob_admin[1]['roles'] = ['reader', 'editor', 'admin']
    add('set-role-additive', [step(['user', 'set-role', 'bob', ' editor,admin ', 'ignored-extra'],
                                   requests=['GET ' + U, 'GET ' + R, 'PATCH /api/v1/user/bob/role/add'],
                                   stdout='Added role(s)  editor,admin  to user bob\n', bodies={2: ['editor', 'admin']},
                                   headers={2: {'content-type': 'application/json', 'content-length': '18'}}, commits=1)],
        final=state_with(users=bob_admin), group='user')
    add('set-role-missing-user', [step(['user', 'set-role', 'ghost', 'admin'], exit=1, requests=['GET ' + U],
                                       stderr_has=["user doesn't exist. Please create the user with `pb user add ghost`"])],
        group='user')
    add('set-role-missing-user-cloud', [step(['user', 'set-role', 'ghost', 'admin'], exit=1, requests=['GET ' + U],
                                             stderr_has=['invite the user from the Parseable Cloud dashboard first'])],
        auth='cloud-key', group='user')
    add('set-role-missing-role', [step(['user', 'set-role', 'bob', 'admin,ghost,phantom'], exit=1, requests=['GET ' + U, 'GET ' + R],
                                       stderr_has=["role ghost doesn't exist; create it using `pb role add ghost`"])],
        group='user')
    add('set-role-one-argument', [step(['user', 'set-role', 'bob'], exit=1, stderr_has=['requires at least 2 arguments'])],
        group='validation')
    add('set-role-one-argument-json', [step(['user', 'set-role', 'bob', '-o', 'json'], exit=1, allow_stdout=True)],
        group='validation')
    add('set-role-no-profile-wins', [step(['user', 'set-role', 'bob'], exit=1, stderr_has=['profile'])],
        config={'toml': ''}, group='validation')
    add('set-role-patch-failure', [step(['user', 'set-role', 'bob', 'admin'], exit=1,
                                        requests=['GET ' + U, 'GET ' + R, 'PATCH /api/v1/user/bob/role/add'],
                                        stderr_has=['set user roles failed: 500 Internal Server Error: broken'])],
        routes=[p3.route(b'broken', 500, method='PATCH', path=API + 'user/bob/role/add')], group='user')

    # ---- user list -------------------------------------------------------
    details = ['GET ' + API + 'user/alice/role', 'GET ' + API + 'user/bob/role']
    seeded_rows = [('alice', ['admin'], None), ('bob', ['reader', 'editor'], None)]
    user_compare = {'fanout': [USER_FANOUT], 'stdout': 'user-text'}
    for alias in ('list', 'ls'):
        add('user-' + alias + '-text', [step(['user', alias], requests=['GET ' + U] + details, stdout=user_table(seeded_rows))],
            compare=user_compare, group='user-list')
    add('user-list-json', [step(['user', 'list', '-o', ' JSON '], requests=['GET ' + U] + details,
                                stdout=go_json([{'id': 'alice', 'roles': ['admin']}, {'id': 'bob', 'roles': ['reader', 'editor']}]))],
        compare={'fanout': [USER_FANOUT], 'stdout': 'user-json'}, group='user-list')
    add('user-list-bad-output-after-details', [step(['user', 'list', '-o', 'yaml'], exit=1, requests=['GET ' + U] + details,
                                                    stderr_has=['unsupported output format "yaml"'])],
        compare=user_compare, group='user-list')
    add('user-list-bad-output-json-envelope', [step(['user', 'list', '--output=xml', '-o', 'json', '-o', 'xml'], exit=1,
                                                    requests=['GET ' + U] + details, allow_stdout=True)],
        compare=user_compare, group='user-list')
    add('user-list-empty-text', [step(['user', 'list'], requests=['GET ' + U], stdout=user_table([]))],
        state=seed(users=[]), group='user-list')
    add('user-list-empty-json', [step(['user', 'list', '-o', 'json'], requests=['GET ' + U], stdout=b'[]\n')],
        state=seed(users=[]), group='user-list')
    add('user-list-null-json', [step(['user', 'list', '-o', 'json'], requests=['GET ' + U], stdout=b'[]\n')],
        routes=[p3.route(b'null', method='GET', path=U)], group='user-list')
    multiplicity = [{'id': 'dup', 'roles': ['admin', 'reader'], 'groups': {'g1': ['admin'], 'g2': ['editor', 'reader']}},
                    {'id': 'none', 'roles': [], 'groups': {}},
                    {'id': 'groups-only', 'roles': [], 'groups': {'g': ['editor']}}]
    for fmt in ('text', 'json'):
        expected = user_table([(u['id'], flattened_roles(None, u), None) for u in multiplicity]) if fmt == 'text' else \
            go_json([{'id': u['id'], 'roles': flattened_roles(None, u)} for u in multiplicity])
        add('user-list-multiplicity-' + fmt, [step(['user', 'list', '-o', fmt],
                                                   requests=['GET ' + U] + ['GET ' + API + 'user/' + u['id'] + '/role' for u in multiplicity],
                                                   stdout=expected)],
            state=seed(users=multiplicity), compare={'fanout': [USER_FANOUT], 'stdout': 'user-' + fmt}, group='user-list')
    unicode_users = [{'id': name, 'roles': ['reader'], 'groups': {}} for name in
                     ['é', '漢字ユーザー', 'x' * 70, '界' * 40, 'tab\tname', '<&>']]
    add('user-list-unicode-widths', [step(['user', 'list'], requests=['GET ' + U] +
                                          ['GET ' + API + 'user/' + u['id'] + '/role' for u in unicode_users],
                                          stdout=user_table([(u['id'], ['reader'], None) for u in unicode_users]))],
        state=seed(users=unicode_users), compare=user_compare, group='user-list')
    add('user-list-unicode-json', [step(['user', 'list', '-o', 'json'], requests=['GET ' + U] +
                                        ['GET ' + API + 'user/' + u['id'] + '/role' for u in unicode_users],
                                        stdout=go_json([{'id': u['id'], 'roles': ['reader']} for u in unicode_users]))],
        state=seed(users=unicode_users), compare={'fanout': [USER_FANOUT], 'stdout': 'user-json'}, group='user-list')
    gathered_users = [{'id': f'gather{i:03d}', 'roles': ['reader'], 'groups': {}} for i in range(65)]
    add('user-list-wide-gather', [step(['user', 'list', '-o', 'json'],
        requests=['GET ' + U] + ['GET ' + API + 'user/' + u['id'] + '/role' for u in gathered_users],
        stdout=go_json([{'id': u['id'], 'roles': ['reader']} for u in gathered_users]))],
        state=seed(users=gathered_users),
        routes=[{'method': 'GET', 'path_re': r'/api/v1/user/gather[0-9]+/role', 'model': True,
                 'gather': 65}],
        compare={'fanout': [USER_FANOUT], 'stdout': 'user-json'}, group='user-list')
    many = [{'id': f'user{i:03d}', 'roles': ['reader'] if i % 2 else ['admin'], 'groups': {}} for i in range(64)]
    for fmt in ('text', 'json'):
        expected = user_table([(u['id'], u['roles'], None) for u in many]) if fmt == 'text' else \
            go_json([{'id': u['id'], 'roles': u['roles']} for u in many])
        add('user-list-large-' + fmt, [step(['user', 'list', '-o', fmt], requests=['GET ' + U] +
                                            ['GET ' + API + 'user/' + u['id'] + '/role' for u in many], stdout=expected)],
            state=seed(users=many), compare={'fanout': [USER_FANOUT], 'stdout': 'user-' + fmt}, group='user-list')
    skew = [{'method': 'GET', 'path': API + f'user/{u["id"]}/role', 'model': True, 'delay_ms': 40 * (len(many[:8]) - i)}
            for i, u in enumerate(many[:8])]
    add('user-list-skewed-detail-delays', [step(['user', 'list'], requests=['GET ' + U] +
                                                ['GET ' + API + 'user/' + u['id'] + '/role' for u in many[:8]],
                                                stdout=user_table([(u['id'], u['roles'], None) for u in many[:8]]))],
        state=seed(users=many[:8]), routes=skew, compare=user_compare, group='user-list')
    partial = [p3.route(b'{"error":"denied"}', 403, method='GET', path=API + 'user/bob/role')]
    message = 'fetch user roles failed: 403 Forbidden: {"error":"denied"}'
    add('user-list-partial-text', [step(['user', 'list'], exit=1, requests=['GET ' + U] + details,
                                        stdout=user_table([('alice', ['admin'], None), ('bob', None, message)]),
                                        stderr_has=['failed to fetch roles for 1 user(s): Error fetching roles for bob: ' + message])],
        routes=partial, compare=user_compare, group='user-list')
    add('user-list-partial-json', [step(['user', 'list', '-o', 'json'], exit=1, requests=['GET ' + U] + details,
                                        error=json_error('COMMAND_FAILED'))],
        routes=partial, compare=user_compare, group='user-list')
    shapes = [('null-roles', b'{"roles":null}', []), ('missing-groups', b'{"roles":{"x":{"actions":[]}}}', ['x']),
              ('case-varied', b'{"ROLES":{"y":{}},"Group_Roles":{"g":{"z":null}}}', ['y', 'z']),
              ('unknown-fields', b'{"extra":[{}],"roles":{},"group_roles":{}}', []),
              # encoding/json decodes a repeated object into the already allocated map: entries merge.
              ('duplicate-key', b'{"roles":{"a":{}},"roles":{"b":{}}}', ['a', 'b'])]
    for label, body, roles in shapes:
        for fmt in ('text', 'json'):
            out = user_table([('alice', ['admin'], None), ('bob', roles, None)]) if fmt == 'text' else \
                go_json([{'id': 'alice', 'roles': ['admin']}, {'id': 'bob', 'roles': roles}])
            add(f'user-list-shape-{label}-{fmt}', [step(['user', 'list', '-o', fmt], requests=['GET ' + U] + details, stdout=out)],
                routes=[p3.route(body, method='GET', path=API + 'user/bob/role')],
                compare={'fanout': [USER_FANOUT], 'stdout': 'user-' + fmt}, group='user-list')
    for label, body in [('malformed', b'{"roles":'), ('wrong-type', b'{"roles":[]}')]:
        add('user-list-detail-' + label, [step(['user', 'list'], exit=1, requests=['GET ' + U] + details, allow_stdout=True,
                                               stderr_has=['failed to fetch roles for 1 user(s)'])],
            routes=[p3.route(body, method='GET', path=API + 'user/bob/role')], compare=user_compare, group='user-list')
    add('user-list-users-500', [step(['user', 'list', '-o', 'json'], exit=1, requests=['GET ' + U],
                                     error=json_error('SERVER_ERROR', 500))],
        routes=[p3.route(b'down', 500, method='GET', path=U)], group='user-list')
    add('user-list-devfull-json', [step(['user', 'list', '-o', 'json'], exit=1, requests=['GET ' + U] + details,
                                        stderr_has=['failed to encode JSON output'], io={'stdout': 'devfull'})],
        compare=user_compare, group='process')

    # ---- role list -------------------------------------------------------
    role_details = ['GET ' + API + 'role/' + name for name in ROLES]
    role_compare = {'fanout': [ROLE_FANOUT], 'stdout': 'role-text', 'stderr': 'role-errors'}
    seeded_role_rows = [(name, actions, None) for name, actions in ROLES.items()]
    for alias in ('list', 'ls'):
        add('role-' + alias + '-text', [step(['role', alias], requests=['GET ' + R] + role_details,
                                             stdout=role_table(seeded_role_rows))], compare=role_compare, group='role-list')
    add('role-list-json', [step(['role', 'list', '-o', 'json'], requests=['GET ' + R] + role_details, stdout=role_json(ROLES))],
        compare={'fanout': [ROLE_FANOUT]}, group='role-list')
    add('role-list-bad-output-before-details', [step(['role', 'list', '-o', 'yaml'], exit=1, requests=['GET ' + R],
                                                     stderr_has=['unsupported output format "yaml"'])], group='role-list')
    add('role-list-empty-output-is-text', [step(['role', 'list', '-o', ''], requests=['GET ' + R] + role_details,
                                                stdout=role_table(seeded_role_rows))], compare=role_compare, group='role-list')
    add('role-list-empty-text', [step(['role', 'list'], requests=['GET ' + R], stdout=role_table([]))],
        state=seed(roles={}, users=[]), group='role-list')
    add('role-list-empty-json', [step(['role', 'list', '-o', 'json'], requests=['GET ' + R], stdout=b'{}\n')],
        state=seed(roles={}, users=[]), group='role-list')
    odd = {'nullish': None, 'empty': [], 'blank-privilege': [{'privilege': '  '}],
           'both-fields': [{'privilege': 'reader', 'resource': {'dataset': 'new', 'stream': 'old', 'tag': 't'}}],
           'stream-only': [{'privilege': 'reader', 'resource': {'stream': 'legacy'}}],
           'empty-resource': [{'privilege': 'custom-privilege-name', 'resource': {}}],
           'r' * 50: [{'privilege': 'reader', 'resource': {'dataset': 'd' * 60}}],
           '角色': [{'privilege': '読む', 'resource': {'dataset': 'データ'}}]}
    odd_details = ['GET ' + API + 'role/' + n for n in odd]
    add('role-list-shapes-text', [step(['role', 'list'], requests=['GET ' + R] + odd_details,
                                       stdout=role_table([(n, a, None) for n, a in odd.items()]))],
        state=seed(roles=odd, users=[]), compare=role_compare, group='role-list')
    add('role-list-shapes-json', [step(['role', 'list', '-o', 'json'], requests=['GET ' + R] + odd_details, stdout=role_json(odd))],
        state=seed(roles=odd, users=[]), compare={'fanout': [ROLE_FANOUT]}, group='role-list')
    raw_role = [('null-actions', b'{"actions":null}', None), ('missing-actions', b'{}', None),
                ('case-varied', b'{"ACTIONS":[{"PRIVILEGE":"admin","Resource":{"DATASET":"x"}}]}',
                 [{'privilege': 'admin', 'resource': {'dataset': 'x'}}]),
                ('unknown-privilege', b'{"actions":[{"privilege":"superuser","extra":1}],"other":{}}',
                 [{'privilege': 'superuser'}]),
                ('null-resource', b'{"actions":[{"privilege":"admin","resource":null}]}', [{'privilege': 'admin'}])]
    for label, body, actions in raw_role:
        for fmt in ('text', 'json'):
            roles = {'admin': actions}
            out = role_table([('admin', actions, None)]) if fmt == 'text' else role_json(roles)
            add(f'role-list-detail-{label}-{fmt}', [step(['role', 'list', '-o', fmt], requests=['GET ' + R, 'GET ' + API + 'role/admin'],
                                                         stdout=out)],
                state=seed(roles={'admin': ROLES['admin']}, users=[]),
                routes=[p3.route(body, method='GET', path=API + 'role/admin')], compare=role_compare, group='role-list')
    failing = [p3.route(b'boom', 500, method='GET', path=API + 'role/editor'),
               p3.route(b'{"actions":', method='GET', path=API + 'role/reader')]
    add('role-list-partial-text', [step(['role', 'list'], exit=1, requests=['GET ' + R] + role_details, allow_stdout=True,
                                        stderr_has=['failed to fetch details for 2 role(s): '])],
        routes=failing, compare=role_compare, group='role-list')
    add('role-list-partial-json', [step(['role', 'list', '-o', 'json'], exit=1, requests=['GET ' + R] + role_details,
                                        error=json_error('COMMAND_FAILED'))],
        routes=failing, compare=role_compare, group='role-list')
    add('role-list-single-403-is-command-failed', [step(['role', 'list', '-o', 'json'], exit=1, requests=['GET ' + R] + role_details,
                                                        error=json_error('COMMAND_FAILED'))],
        routes=[p3.route(b'forbidden', 403, method='GET', path=API + 'role/admin')], compare=role_compare, group='role-list')
    for label, body in [('malformed', b'{"admin":'), ('array', b'[]'), ('null', b'null')]:
        ok = label == 'null'
        add('role-list-roles-' + label, [step(['role', 'list', '-o', 'json'], exit=0 if ok else 1, requests=['GET ' + R],
                                              stdout=b'{}\n' if ok else None)],
            routes=[p3.route(body, method='GET', path=R)], group='role-list')
    add('role-list-roles-duplicate-names', [step(['role', 'list', '-o', 'json'], requests=['GET ' + R, 'GET ' + API + 'role/admin'],
                                                 stdout=role_json({'admin': ROLES['admin']}))],
        routes=[p3.route(b'{"admin":[],"admin":{}}', method='GET', path=R)], compare={'fanout': [ROLE_FANOUT]}, group='role-list')
    many_roles = {f'role{i:03d}': [{'privilege': 'reader', 'resource': {'dataset': f'ds{i}'}}] for i in range(64)}
    for fmt in ('text', 'json'):
        add('role-list-large-' + fmt, [step(['role', 'list', '-o', fmt], requests=['GET ' + R] + ['GET ' + API + 'role/' + n for n in many_roles],
                                            stdout=role_table([(n, a, None) for n, a in many_roles.items()]) if fmt == 'text'
                                            else role_json(many_roles))],
            state=seed(roles=many_roles, users=[]), compare=role_compare, group='role-list')
    add('role-list-devfull-text', [step(['role', 'list'], requests=['GET ' + R] + role_details, io={'stdout': 'devfull'})],
        compare=role_compare, group='process')
    add('role-list-devfull-json', [step(['role', 'list', '-o', 'json'], exit=1, requests=['GET ' + R] + role_details,
                                        stderr_has=['failed to encode JSON output'], io={'stdout': 'devfull'})],
        compare=role_compare, group='process')

    # ---- role removal ----------------------------------------------------
    roles_without = {k: v for k, v in ROLES.items() if k != 'unused'}
    add('role-remove', [step(['role', 'remove', 'unused'], requests=['GET ' + R, 'DELETE /api/v1/role/unused'],
                             stdout='Removed role unused\n', commits=1)], final=state_with(roles=roles_without), group='role')
    add('role-rm-missing', [step(['role', 'rm', 'ghost'], exit=1, requests=['GET ' + R],
                                 stderr_has=["role ghost doesn't exist. Available role names: admin, editor, ingestor, reader, unused"])],
        group='role')
    add('role-remove-missing-none-available', [step(['role', 'remove', 'ghost'], exit=1, requests=['GET ' + R],
                                                    stderr_has=["role ghost doesn't exist. No roles are available"])],
        state=seed(roles={}, users=[]), group='role')
    add('role-remove-assigned-fixture-rejects', [step(['role', 'remove', 'admin'], exit=1,
                                                      requests=['GET ' + R, 'DELETE /api/v1/role/admin'],
                                                      stderr_has=['delete role failed: 400 Bad Request: role is assigned to users'])],
        group='role')
    add('role-remove-stalled-success-body', [step(['role', 'remove', 'unused'], requests=['GET ' + R, 'DELETE /api/v1/role/unused'],
                                                  stdout='Removed role unused\n', max_wall_ms=5000)],
        routes=[{'method': 'DELETE', 'path': API + 'role/unused', 'model': True, 'framing': 'chunked',
                 'script': [{'send': 1}, {'stall': True}]}], final=state_with(roles=roles_without), group='consumption')
    add('role-remove-no-args', [step(['role', 'remove'], exit=1, stderr_has=['accepts 1 arg(s), received 0'])], group='validation')

    # ---- stateful sequences ----------------------------------------------
    for kind in KINDS:
        wrong = 'metrics' if kind != 'metrics' else 'logs'
        add('lifecycle-dataset-' + kind, [
            step(['dataset', 'add', 'life', '--type', kind], requests=['PUT /api/v1/logstream/life'],
                 stdout=f'Created {kind} dataset life\n'),
            step(['dataset', 'remove', 'life', '--type', wrong], exit=1, requests=['GET /api/v1/logstream/life/info'],
                 stderr_has=[f'dataset "life" is {kind}, not {wrong}']),
            step(['dataset', 'remove', 'life', '--type', kind], requests=['GET /api/v1/logstream/life/info',
                                                                          'DELETE /api/v1/logstream/life'],
                 stdout='Successfully deleted dataset life\n'),
            step(['dataset', 'remove', 'life'], exit=1, requests=['DELETE /api/v1/logstream/life'],
                 stderr_has=['delete dataset failed: 404 Not Found']),
            step(['dataset', 'add', 'app', '--type', kind], exit=1, requests=['PUT /api/v1/logstream/app'])],
            group='stateful')
    carol = {'id': 'carol', 'method': 'native', 'roles': ['reader'], 'groups': {}}
    add('lifecycle-user', [
        step(['user', 'add', 'carol', '--role', 'reader'], requests=['GET ' + U, 'GET ' + R, 'POST /api/v1/user/carol'],
             stdout='Added user: carol \nPassword is: pw-carol\nRole(s) assigned: reader\n'),
        step(['user', 'set-role', 'carol', 'reader'], requests=['GET ' + U, 'GET ' + R, 'PATCH /api/v1/user/carol/role/add'],
             stdout='Added role(s) reader to user carol\n', bodies={2: ['reader']}),
        step(['user', 'set-role', 'carol', 'editor'], requests=['GET ' + U, 'GET ' + R, 'PATCH /api/v1/user/carol/role/add'],
             stdout='Added role(s) editor to user carol\n'),
        step(['user', 'list', '-o', 'json'], requests=['GET ' + U] + details + ['GET ' + API + 'user/carol/role'],
             stdout=go_json([{'id': 'alice', 'roles': ['admin']}, {'id': 'bob', 'roles': ['reader', 'editor']},
                             {'id': 'carol', 'roles': ['reader', 'editor']}])),
        step(['user', 'add', 'carol', '--role', 'admin'], exit=1, requests=['GET ' + U], stderr_has=['user carol already exists']),
        step(['user', 'remove', 'carol'], requests=['DELETE /api/v1/user/carol'], stdout='Removed user carol\n'),
        step(['user', 'remove', 'carol'], exit=1, requests=['DELETE /api/v1/user/carol'], stderr_has=['404 Not Found'])],
        compare={'fanout': [USER_FANOUT], 'stdout': 'user-json'}, group='stateful')
    add('lifecycle-role', [
        step(['role', 'list', '-o', 'json'], requests=['GET ' + R] + role_details, stdout=role_json(ROLES)),
        step(['role', 'remove', 'unused'], requests=['GET ' + R, 'DELETE /api/v1/role/unused'], stdout='Removed role unused\n'),
        step(['user', 'set-role', 'alice', 'unused'], exit=1, requests=['GET ' + U, 'GET ' + R],
             stderr_has=["role unused doesn't exist"]),
        step(['role', 'remove', 'unused'], exit=1, requests=['GET ' + R], stderr_has=["role unused doesn't exist"]),
        step(['role', 'list', '-o', 'json'], requests=['GET ' + R] + ['GET ' + API + 'role/' + n for n in roles_without],
             stdout=role_json(roles_without))],
        final=state_with(roles=roles_without), compare={'fanout': [ROLE_FANOUT]}, group='stateful')
    _ = carol

    # ---- OAuth refresh, replay and contention (Cloud session profiles) ---
    cloud = {'toml': phase2.config_toml({'fixture': {'url': '{{url}}', 'cloud': True, 'session_token': 'fixture-session',
                                                     'refresh_token': 'fixture-refresh', 'tenant_id': 'fixture-tenant',
                                                     'orchestrator_url': '{{url}}/orch'}})}
    oauth = {'oauth': {'refresh_token': 'fixture-refresh',
                       'issue': [{'access_token': 'rotated-session', 'refresh_token': 'rotated-refresh'}]}}
    rotated = {'profiles': {'fixture': {'session_token': 'rotated-session', 'refresh_token': 'rotated-refresh'}}}

    def refreshed(name, steps, routes=(), final=None, state=None, compare=None, config=None, fixture_extra=None, **extra):
        plan_extra = {**oauth, **(fixture_extra or {})}
        case = add(name, steps, state=state, auth='cloud-cookie', routes=[{**r} for r in routes], final=final, build='cloud',
                   config=config or cloud, fixture_extra=plan_extra, compare=compare, group='refresh', **extra)
        case['fixture']['headers'] = {'x-p-tenant': 'fixture-tenant'}
        return case

    expired = {'reject_sessions': ['fixture-session']}
    for name, argv, requests, stdout, final in [
            ('user-remove', ['user', 'remove', 'bob'], ['DELETE /api/v1/user/bob'], 'Removed user bob\n', state_with(users=USERS[:1])),
            ('dataset-add', ['dataset', 'add', 'new', '--type', 'metrics'], ['PUT /api/v1/logstream/new'],
             'Created metrics dataset new\n', state_with(datasets={**DATASETS, 'new': {'type': 'metrics', 'source': 'otel-metrics'}})),
            ('dataset-remove-guarded', ['dataset', 'remove', 'app', '--type', 'logs'],
             ['GET /api/v1/logstream/app/info', 'DELETE /api/v1/logstream/app'], 'Successfully deleted dataset app\n',
             state_with(datasets=without_dataset('app'))),
            ('role-remove', ['role', 'remove', 'unused'], ['GET ' + R, 'DELETE /api/v1/role/unused'], 'Removed role unused\n',
             state_with(roles=roles_without))]:
        first = requests[0]
        lines = [first, 'POST ' + TOKEN_PATH] + requests
        headers = {0: OLD, 2: NEW, len(lines) - 1: NEW}
        if name == 'dataset-add':
            headers[2] = {**NEW, 'x-p-telemetry-type': 'metrics', 'x-p-log-source': 'otel-metrics'}
            headers[0] = {**OLD, 'x-p-telemetry-type': 'metrics', 'x-p-log-source': 'otel-metrics'}
        refreshed('refresh-precommit-' + name, [step(argv, requests=lines, stdout=stdout, headers=headers, commits=1)],
                  final=final, fixture_extra=expired, config_expect=rotated)
    patch = 'PATCH /api/v1/user/bob/role/add'
    refreshed('refresh-replay-body-set-role', [step(['user', 'set-role', 'bob', 'admin,editor'],
                                                    requests=['GET ' + U, 'GET ' + R, patch, 'POST ' + TOKEN_PATH, patch],
                                                    stdout='Added role(s) admin,editor to user bob\n',
                                                    bodies={2: ['admin', 'editor'], 4: ['admin', 'editor']},
                                                    headers={2: OLD, 4: NEW}, commits=1)],
              routes=[p3.route(b'expired', 401, method='PATCH', path=API + 'user/bob/role/add', headers=OLD, times=1)],
              final=state_with(users=[USERS[0], {**USERS[1], 'roles': ['reader', 'admin', 'editor']}]), config_expect=rotated)
    refreshed('refresh-before-fanout-user-list', [step(['user', 'list', '-o', 'json'],
                                                       requests=['GET ' + U, 'POST ' + TOKEN_PATH, 'GET ' + U] + details,
                                                       stdout=go_json([{'id': 'alice', 'roles': ['admin']},
                                                                       {'id': 'bob', 'roles': ['reader', 'editor']}]),
                                                       headers={0: OLD, 2: NEW, 3: NEW, 4: NEW})],
              fixture_extra=expired, compare={'fanout': [USER_FANOUT], 'stdout': 'user-json'}, config_expect=rotated)
    four = [{'id': f'u{i}', 'roles': ['reader'], 'groups': {}} for i in range(4)]
    four_details = ['GET ' + API + f'user/u{i}/role' for i in range(4)]
    refreshed('refresh-contention-user-list', [step(['user', 'list', '-o', 'json'],
                                                    requests=['GET ' + U] + four_details + ['POST ' + TOKEN_PATH] + four_details,
                                                    stdout=go_json([{'id': u['id'], 'roles': ['reader']} for u in four]),
                                                    headers={**{i: OLD for i in range(1, 5)}, **{i: NEW for i in range(6, 10)}})],
              state=seed(users=four),
              routes=[{'method': 'GET', 'path_re': USER_FANOUT, 'headers': OLD, 'status': 401, 'body': 'expired',
                       'times': 4, 'gather': 4}],
              compare={'fanout': [USER_FANOUT], 'stdout': 'user-json'}, config_expect=rotated)
    three = {n: ROLES[n] for n in ('admin', 'editor', 'reader')}
    three_details = ['GET ' + API + 'role/' + n for n in three]
    refreshed('refresh-contention-role-list', [step(['role', 'list', '-o', 'json'],
                                                    requests=['GET ' + R] + three_details + ['POST ' + TOKEN_PATH] + three_details,
                                                    stdout=role_json(three),
                                                    headers={**{i: OLD for i in range(1, 4)}, **{i: NEW for i in range(5, 8)}})],
              state=seed(roles=three, users=[]),
              routes=[{'method': 'GET', 'path_re': ROLE_FANOUT, 'headers': OLD, 'status': 401, 'body': 'expired',
                       'times': 3, 'gather': 3}],
              compare={'fanout': [ROLE_FANOUT]}, config_expect=rotated)
    refreshed('refresh-rejected-no-commit', [step(['user', 'remove', 'bob'], exit=1, requests=['DELETE /api/v1/user/bob', 'POST ' + TOKEN_PATH],
                                                  stderr_has=['cloud session refresh failed'])],
              routes=[p3.route(b'{"error":"invalid_grant"}', 400, path=TOKEN_PATH)], fixture_extra=expired,
              config_expect='unchanged')
    readonly = dict(cloud, mode=0o400)
    refreshed('refresh-persistence-failure-no-commit', [step(['user', 'remove', 'bob'], exit=1,
                                                             requests=['DELETE /api/v1/user/bob', 'POST ' + TOKEN_PATH],
                                                             stderr_has=['failed to persist refreshed cloud tokens'])],
              config=readonly, fixture_extra=expired, config_expect='unchanged', requires=['nonroot'])
    no_refresh = {'toml': phase2.config_toml({'fixture': {'url': '{{url}}', 'cloud': True, 'session_token': 'fixture-session',
                                                          'tenant_id': 'fixture-tenant', 'orchestrator_url': '{{url}}/orch'}})}
    refreshed('refresh-unavailable-401', [step(['user', 'remove', 'bob'], exit=1, requests=['DELETE /api/v1/user/bob'],
                                               stderr_has=['401'])],
              config=no_refresh, fixture_extra=expired, config_expect='unchanged')

    # ---- commit-point disconnects, output failures, diagnostics ----------
    add('disconnect-before-commit-no-retry', [step(['user', 'remove', 'bob'], exit=1, requests=['DELETE /api/v1/user/bob'],
                                                   commits=0)],
        routes=[{'method': 'DELETE', 'path': API + 'user/bob', 'close_before_headers': 'fin'}], group='commit')
    add('disconnect-after-commit-no-retry', [step(['role', 'remove', 'unused'], exit=1, requests=['GET ' + R, 'DELETE /api/v1/role/unused'],
                                                  commits=1)],
        routes=[{'method': 'DELETE', 'path': API + 'role/unused', 'model': True, 'close_before_headers': 'fin'}],
        final=state_with(roles=roles_without), group='commit')
    add('disconnect-after-commit-post-no-retry', [step(['user', 'add', 'carol', '--role', 'admin'], exit=1,
                                                       requests=['GET ' + U, 'GET ' + R, 'POST /api/v1/user/carol'], commits=1)],
        routes=[{'method': 'POST', 'path': API + 'user/carol', 'model': True, 'close_before_headers': 'fin'}],
        final=state_with(users=USERS + [{'id': 'carol', 'method': 'native', 'roles': ['admin'], 'groups': {}}]), group='commit')
    add('reused-connection-read-retry', [step(['user', 'add', 'carol', '--role', 'admin'],
                                              requests=['GET ' + U, 'GET ' + R, 'GET ' + R, 'POST /api/v1/user/carol'],
                                              stdout='Added user: carol \nPassword is: pw-carol\nRole(s) assigned: admin\n')],
        routes=[{'method': 'GET', 'path': R, 'close_before_headers': 'fin', 'times': 1}],
        final=state_with(users=USERS + [{'id': 'carol', 'method': 'native', 'roles': ['admin'], 'groups': {}}]), group='commit')
    for argv, stdout_final in [(['user', 'remove', 'bob'], state_with(users=USERS[:1])),
                               (['dataset', 'add', 'new', '--type', 'logs'], state_with(datasets={**DATASETS, 'new': {'type': 'logs'}})),
                               (['user', 'add', 'carol', '--role', 'admin'],
                                state_with(users=USERS + [{'id': 'carol', 'method': 'native', 'roles': ['admin'], 'groups': {}}])),
                               (['role', 'remove', 'unused'], state_with(roles=roles_without))]:
        label = '-'.join(argv[:2])
        add('devfull-after-commit-' + label, [step(argv, requests=None, io={'stdout': 'devfull'}, commits=1)],
            final=stdout_final, group='process')
        c = add('broken-pipe-after-commit-' + label, [step(argv, exit={'signal': 13}, requests=None, commits=1,
                                                           actions=[{'when': {'start': True}, 'do': {'close': 'stdout'}}])],
                final=stdout_final, group='process')
        c['fixture']['routes'] = [{'method': m, 'path_re': r'/api/v1/.*', 'model': True, 'delay_ms': 150}
                                  for m in ('PUT', 'POST', 'DELETE')]
    add('debug-headers-redacted', [step(['user', 'remove', 'bob'], requests=['DELETE /api/v1/user/bob'], stdout='Removed user bob\n',
                                        env={'PB_DEBUG_HTTP_HEADERS': '1'},
                                        stderr_has=['pb debug header: Authorization: <redacted>'])],
        final=state_with(users=USERS[:1]), compare={'stderr_unordered_lines': True}, group='transport')
    add('debug-headers-cloud-cookie', [step(['dataset', 'add', 'new', '--type', 'traces'], requests=['PUT /api/v1/logstream/new'],
                                            stdout='Created traces dataset new\n', env={'PB_DEBUG_HTTP_HEADERS': '1'},
                                            stderr_has=['pb debug header: Cookie: <redacted>'])],
        auth='cloud-cookie', final=state_with(datasets={**DATASETS, 'new': {'type': 'traces', 'source': 'otel-traces'}}),
        compare={'stderr_unordered_lines': True}, group='transport')
    for auth in ('api-key', 'cloud-key'):
        add('auth-' + auth + '-user-list', [step(['user', 'list', '-o', 'json'], requests=['GET ' + U] + details,
                                                 stdout=go_json([{'id': 'alice', 'roles': ['admin']},
                                                                 {'id': 'bob', 'roles': ['reader', 'editor']}]),
                                                 headers={0: p3.HEADERS[auth]})],
            auth=auth, compare={'fanout': [USER_FANOUT], 'stdout': 'user-json'}, group='transport')
    add('invalid-profile-url', [step(['user', 'remove', 'bob'], exit=1, allow_stdout=True)],
        config={'toml': phase2.config_toml({'fixture': phase2.profile(url='http://[::1')})}, group='transport')
    add('base-path-prefix', [step(['user', 'remove', 'bob'], exit=1, requests=['DELETE /base/api/v1/user/bob'], allow_unexpected=True)],
        config={'toml': phase2.config_toml({'fixture': phase2.profile(url='{{url}}/base/')})}, group='transport')
    # Raw response bytes survive text errors; JSON substitutes each invalid byte.
    raw = b'\xff\xe2\x82\x00raw\tline\nsecond'
    raw64 = base64.b64encode(raw).decode()
    for label, argv, routes, requests, partial in [
        ('delete', ['user', 'remove', 'bob'], [{'method': 'DELETE', 'path': API + 'user/bob'}], ['DELETE /api/v1/user/bob'], False),
        ('listing', ['user', 'list'], [{'method': 'GET', 'path': U}], ['GET ' + U], False),
        ('user-detail', ['user', 'list'], [{'method': 'GET', 'path': API + 'user/alice/role'}], ['GET ' + U] + details, True),
        ('role-detail', ['role', 'list'], [{'method': 'GET', 'path': API + 'role/admin'}], None, True),
        ('dataset-info', ['dataset', 'remove', 'app', '--type', 'logs'], [{'method': 'GET', 'path': API + 'logstream/app/info'}], ['GET /api/v1/logstream/app/info'], False),
    ]:
        for structured in ((False, True) if 'list' in argv else (False,)):
            suffix = '-json-error' if structured else '-text'
            add('raw-error-' + label + suffix,
                [step(argv + (['-o', 'json'] if structured else []), exit=1, requests=requests,
                      allow_stdout=partial,
                      error=(json_error('PERMISSION_DENIED', 403) if label == 'listing' else json_error('COMMAND_FAILED')) if structured else None,
                      stderr_has=([b'\\ufffd\\ufffd\\ufffd\\u0000raw'] if partial else []) if structured else [raw])],
                routes=[{**route, 'status': 403, 'body_b64': raw64} for route in routes],
                compare=({'fanout': [USER_FANOUT]} if label == 'user-detail' else
                         {'fanout': [ROLE_FANOUT], 'stdout': 'role-text', 'stderr': 'role-errors'} if label == 'role-detail' else {}),
                group='raw-bytes')
    for case in cases:
        for item in case['steps']:
            item['expect'].setdefault('allow_unexpected', item.pop('allow_unexpected', False))
    return cases


# --------------------------------------------------------------------------
# Expectations and differential comparison.


def json_envelope(stderr):
    try:
        return json.loads(stderr)['error']
    except (ValueError, KeyError, TypeError):
        return None


def step_expectation_issues(case, index, result, previous_commits):
    want, label = case['steps'][index]['expect'], f'step {index}: '
    issues = []
    if result['failure']:
        issues.append(label + result['failure'])
    if result['termination'] != want['termination']:
        issues.append(f"{label}termination {result['termination']}, expected {want['termination']}")
    lines = [r['method'] + ' ' + path_of(r['path']) for r in result['trace']]
    patterns = case.get('compare', {}).get('fanout', [])
    if want['requests'] is not None:
        expected = fanout_canonical([l for l in want['requests']], patterns)
        if fanout_canonical(lines, patterns) != expected:
            issues.append(f'{label}requests {lines!r}, expected {want["requests"]!r}')
    if any(r['unexpected'] for r in result['trace']) and not want.get('allow_unexpected'):
        issues.append(label + 'request outside the modeled administration API')
    for position, body in want.get('request_bodies', {}).items():
        position = int(position)
        try:
            actual = json.loads(base64.b64decode(result['trace'][position]['body_b64']))
        except (IndexError, ValueError):
            actual = '<missing>'
        if actual != body:
            issues.append(f'{label}request {position} body {actual!r}, expected {body!r}')
    for position, headers in want.get('request_headers', {}).items():
        position = int(position)
        actual = result['trace'][position]['headers'] if position < len(result['trace']) else {}
        for key, value in headers.items():
            if actual.get(key) != value:
                issues.append(f'{label}request {position} header {key}={actual.get(key)!r}, expected {value!r}')
    stdout, stderr = result['stdout_normalized'], result['stderr_normalized']
    if 'stdout_exact' in want:
        expected = p3.as_bytes(want['stdout_exact'])
        if stdout is None or canon_stdout(case, stdout) != canon_stdout(case, expected):
            issues.append(f'{label}stdout {(stdout or b"")[:300]!r}, expected {expected[:300]!r}')
    for needle in want.get('stderr_has', []):
        if p3.as_bytes(needle) not in normalize_timeout(case, stderr):
            issues.append(f'{label}stderr lacks {p3.as_bytes(needle)!r}')
    if 'json_error' in want:
        detail = json_envelope(stderr)
        if detail is None or any(detail.get(k) != v for k, v in want['json_error'].items()):
            issues.append(f'{label}stderr JSON error {detail!r}, expected {want["json_error"]!r}')
    if 'commits' in want and len(result['state_after']['commits']) - previous_commits != want['commits']:
        issues.append(f"{label}{len(result['state_after']['commits']) - previous_commits} commits, expected {want['commits']}")
    if 'max_wall_ms' in want and result['wall_ms'] > want['max_wall_ms']:
        issues.append(f"{label}wall {result['wall_ms']:.0f} ms exceeds {want['max_wall_ms']} ms")
    if 'min_wall_ms' in want and result['wall_ms'] < want['min_wall_ms']:
        issues.append(f"{label}wall {result['wall_ms']:.0f} ms below {want['min_wall_ms']} ms")
    for action in result['actions']:
        if action.get('required', True) and not action['met']:
            issues.append(f"{label}action {action['index']} condition {action['when']} not met")
    return issues


def expectation_issues(case, result):
    issues, commits = [], 0
    for index, step in enumerate(result['steps']):
        issues += step_expectation_issues(case, index, step, commits)
        commits = len(step['state_after']['commits'])
    final = result['steps'][-1]['state_after']['state'] if result['steps'] else None
    if final != case['final_state']:
        issues.append('final fixture state differs from the independent model')
    expected = case.get('config_expect', 'unchanged')
    issues += phase2.config_issues(expected, result)
    if any(e['name'] == 'gather-timeout' for e in result['events']):
        issues.append('concurrent fan-out did not reach the gather barrier')
    return issues


def differences(case, reference, candidate):
    issues = []
    if len(reference['steps']) != len(candidate['steps']):
        return ['step count differs']
    for index, (ref, cand) in enumerate(zip(reference['steps'], candidate['steps'])):
        label = f'step {index}: '
        for role, item in (('reference', ref), ('candidate', cand)):
            if item['failure']:
                issues.append(f'{label}{role}: {item["failure"]}')
        if STUB_MARKER in cand['stderr'] or (cand['stdout'] and STUB_MARKER in cand['stdout']):
            issues.append(label + 'candidate returned a deferred-operation stub')
        if ref['termination'] != cand['termination']:
            issues.append(f"{label}termination {cand['termination']}, expected {ref['termination']}")
        expected, actual = canon_stdout(case, ref['stdout_normalized']), canon_stdout(case, cand['stdout_normalized'])
        if expected != actual:
            issues.append(label + phase2.stream_issue('stdout', expected or b'', actual or b''))
        expected = canon_stderr(case, normalize_timeout(case, ref['stderr_normalized']))
        actual = canon_stderr(case, normalize_timeout(case, cand['stderr_normalized']))
        if expected != actual:
            issues.append(label + phase2.stream_issue('stderr', expected, actual))
        if canon_requests(case, ref['requests']) != canon_requests(case, cand['requests']):
            issues.append(f"{label}requests differ: candidate {[contract_line(r) for r in cand['requests']]}, "
                          f"reference {[contract_line(r) for r in ref['requests']]}")
        if ref['state_after'] != cand['state_after']:
            issues.append(label + 'fixture state or commit ledger differs')
    for key in ('config_before', 'config_after'):
        if phase2.semantic(reference[key]) != phase2.semantic(candidate[key]):
            issues.append(key.replace('_', ' ') + ' differs semantically')
    issues += phase2.rewrite_issues(reference, candidate)
    return issues


def raw_order_varied(reference, again):
    """True when the two Go runs saw different raw request order (evidence for the multiset rule)."""
    return any([contract_line(r) for r in a['requests']] != [contract_line(r) for r in b['requests']]
               for a, b in zip(reference['steps'], again['steps']))


def signature(case, result):
    return {'steps': [{'stdout': run.digest(canon_stdout(case, s['stdout_normalized']) or b''),
                       'stdout_bytes': s['stdout_bytes'],
                       'stderr': run.digest(canon_stderr(case, normalize_timeout(case, s['stderr_normalized']))),
                       'termination': s['termination'], 'requests': canon_requests(case, s['requests']),
                       'state': s['state_after']} for s in result['steps']],
            'config': phase2.semantic(result['config_after'])}


def deferred_boundaries():
    """Candidate-only: interactive entry paths stay explicit stubs with no request or HOME change."""
    checks = []
    for label, argv in [('role-add', ['role', 'add', 'newrole']), ('dataset-add-picker', ['dataset', 'add', 'new']),
                        ('dataset-add-blank-type', ['dataset', 'add', 'new', '--type', '  '])]:
        checks.append({'name': 'p5-boundary-' + label, 'argv': argv, 'structured': False, 'location': 'xdg',
                       'config': {'auth': 'basic'}, 'build': 'standard', 'requires': [], 'env': {}, 'unset': [],
                       'fixture': {'headers': {}, 'routes': []}, 'expect': {'termination': {'exit': 1}, 'requests': []}})
    return checks


# --------------------------------------------------------------------------
# CLI.


def validate_capture(report, digest, cases):
    expected = {c['name'] for c in cases if c['tier'] in report.get('tiers', [])}
    actual = [c['name'] for c in report.get('cases', [])]
    if (report.get('mode') != 'check-reference' or report.get('corpus_sha256') != digest or report.get('failed')
            or report.get('partial') or 'gate' not in report.get('tiers', []) or set(actual) != expected
            or len(actual) != len(expected)):
        raise ValueError('capture is not a complete passing current Go capture')


def skip_reason(case):
    if 'nonroot' in case['requires'] and hasattr(__import__('os'), 'geteuid') and __import__('os').geteuid() == 0:
        return 'requires a non-root user'
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    server = sub.add_parser('serve')
    server.add_argument('--scenario', required=True)
    freeze = sub.add_parser('freeze')
    freeze.add_argument('source')
    freeze.add_argument('--out', default=str(MANIFEST))
    for name in ('check-reference', 'compare'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--reference', required=True)
        cmd.add_argument('--cloud-reference')
        cmd.add_argument('--out', required=True)
        cmd.add_argument('--case', action='append')
        cmd.add_argument('--group', action='append')
        cmd.add_argument('--timeout', type=float, default=15)
        cmd.add_argument('--include-full60s', action='store_true')
        cmd.add_argument('--repeat', type=int, default=1, help='extra Go runs per case (comparator self-validation)')
        if name == 'compare':
            cmd.add_argument('--candidate', required=True)
            cmd.add_argument('--cloud-candidate')
            cmd.add_argument('--self-check', action='store_true')
            cmd.add_argument('--manifest', default=str(MANIFEST), help="frozen Go signatures, or 'none'")
    args = parser.parse_args(argv)
    if args.command == 'serve':
        serve(args.scenario)
        return 0
    cases = corpus()
    digest = run.digest(run.canonical(cases))
    if args.command == 'list':
        print('\n'.join(c['name'] + '\t' + c['tier'] + '\t' + c['group'] for c in cases))
        return 0
    if args.command == 'freeze':
        report = json.loads((Path(args.source) / 'report.json').read_text())
        validate_capture(report, digest, cases)
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        return 0
    names = {c['name'] for c in cases}
    if args.case and set(args.case) - names:
        raise ValueError('unknown case selection: ' + ', '.join(sorted(set(args.case) - names)))
    tiers = ['gate', 'full60s'] if args.include_full60s else ['gate']
    selected = [c for c in cases if c['tier'] in tiers and (not args.case or c['name'] in args.case)
                and (not args.group or c['group'] in args.group)]
    if not selected:
        raise ValueError('no cases selected')
    if args.command == 'compare' and not args.self_check and Path(args.reference).resolve() == Path(args.candidate).resolve():
        raise ValueError('same binary requires --self-check')
    manifest = None
    if args.command == 'compare' and args.manifest != 'none':
        manifest = json.loads(Path(args.manifest).read_text())
        validate_capture(manifest, digest, cases)
        for role in ('reference', 'cloud_reference'):
            binary = getattr(args, role)
            if binary and manifest['binaries'].get(role, {}).get('sha256') != phase1.binary_identity(binary)['sha256']:
                raise ValueError(role + ' binary differs from the frozen Go oracle')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema': SCHEMA_VERSION, 'mode': args.command, 'corpus_sha256': digest, 'tiers': tiers,
              'partial': bool(args.case or args.group), 'cases': [], 'failed': 0, 'skipped': [], 'binaries': {},
              'runner_sha256': run.digest(Path(__file__).read_bytes()), 'order_variation_cases': []}
    for role in ('reference', 'cloud_reference', 'candidate', 'cloud_candidate'):
        if getattr(args, role, None):
            report['binaries'][role] = phase1.binary_identity(getattr(args, role))
    for case in selected:
        cloud = case['build'] == 'cloud'
        reference = args.cloud_reference if cloud else args.reference
        candidate = getattr(args, 'cloud_candidate' if cloud else 'candidate', None)
        reason = skip_reason(case) or (None if reference else 'missing cloud reference binary') or \
            (None if args.command != 'compare' or candidate else 'missing cloud candidate binary')
        if reason:
            report['skipped'].append({'name': case['name'], 'reason': reason})
            report['failed'] += 1
            print(case['name'] + ': skipped (counted as failure): ' + reason, file=sys.stderr)
            continue
        folder = out / case['name']
        ref = sample(Path(reference).resolve(), case, folder / 'reference', args.timeout)
        issues = expectation_issues(case, ref)
        varied = False
        for attempt in range(max(args.repeat, 1)):
            again = sample(Path(reference).resolve(), case, folder / f'repeat-{attempt}', args.timeout)
            issues += ['go self-compare: ' + i for i in differences(case, ref, again)]
            varied = varied or raw_order_varied(ref, again)
        if varied:
            report['order_variation_cases'].append(case['name'])
        if manifest and case['tier'] == 'gate':
            frozen = next((c for c in manifest['cases'] if c['name'] == case['name']), None)
            if frozen is None or frozen['signature'] != signature(case, ref):
                issues.append('reference differs from frozen Go oracle signature')
        if args.command == 'compare':
            actual = sample(Path(candidate).resolve(), case, folder / 'candidate', args.timeout)
            issues += ['candidate: ' + i for i in expectation_issues(case, actual)] + differences(case, ref, actual)
        report['cases'].append({'name': case['name'], 'group': case['group'], 'signature': signature(case, ref), 'issues': issues})
        if issues:
            report['failed'] += 1
            print(case['name'] + ': ' + '; '.join(issues), file=sys.stderr)
    if args.command == 'compare' and not args.self_check and not report['partial']:
        report['boundaries'] = []
        for check in deferred_boundaries():
            result = p3.sample(Path(args.candidate).resolve(), check, out / check['name'], args.timeout)
            issues = p3.boundary_issues(check, result)
            report['boundaries'].append({'name': check['name'], 'issues': issues})
            report['failed'] += bool(issues)
    for role, identity in report['binaries'].items():
        if phase1.binary_identity(identity['path'])['sha256'] != identity['sha256']:
            report['failed'] += 1
            report.setdefault('identity_issues', []).append(role + ' binary changed')
    run.write_json(out / 'report.json', report)
    passed = sum(not c['issues'] for c in report['cases'])
    print(f"phase5: {passed}/{len(selected)} cases passed; {report['failed']} total failures; "
          f"raw fan-out order varied in {len(report['order_variation_cases'])} case(s); {out / 'report.json'}")
    return 1 if report['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
