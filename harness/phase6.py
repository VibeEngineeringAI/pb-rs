#!/usr/bin/env python3
"""Finite subprocess Arrow Flight contract checks (never a performance runner).

The fixture, Go oracle and candidate are prebuilt. Every execution has a fresh
HOME and fixture. Captures are bounded; timeout/overflow/missed actions fail.
"""

import argparse
import array
import base64
import copy
import datetime
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import phase2 as p2
import phase3 as p3
import run

SCHEMA_VERSION = 1
TMP = Path.home() / '.cache/pb-phase6/tmp'
REFERENCE = Path(__file__).with_name('contracts') / 'phase6-reference.json'
STDOUT_CAP = 32 * 1024 * 1024
STDERR_CAP = 1024 * 1024
PREFIX_CAP = 65536
AUTH_KEYS = ('authorization', 'x-api-key', 'x-p-tenant', 'cookie')
ABOUT = '/api/v1/about'
TOKEN = '/api/v1/cli/oauth/token'
CODES = ('OK', 'Canceled', 'Unknown', 'InvalidArgument', 'DeadlineExceeded',
         'NotFound', 'AlreadyExists', 'PermissionDenied', 'ResourceExhausted',
         'FailedPrecondition', 'Aborted', 'OutOfRange', 'Unimplemented',
         'Internal', 'Unavailable', 'DataLoss', 'Unauthenticated')
ORACLE_HASHES = {'standard': 'fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314',
                 'cloud': '622d4144b7e354c0ee4036244ba719cbbaf8357710804b1d733cd421a0dc227e'}


class Proxy(socketserver.ThreadingTCPServer):
    """A synthetic-host HTTP/CONNECT proxy; no DNS or host edits are needed."""
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, reject_connect=False):
        self.records = []
        self.lock = threading.Lock()
        self.reject_connect = reject_connect
        super().__init__(('127.0.0.1', 0), ProxyHandler)
        self.worker = threading.Thread(target=self.serve_forever, daemon=True)
        self.worker.start()
        self.url = 'http://127.0.0.1:' + str(self.server_address[1])

    def close(self):
        self.shutdown()
        self.server_close()
        self.worker.join()


class ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        client.settimeout(5)
        head = bytearray()
        try:
            while b'\r\n\r\n' not in head:
                block = client.recv(1)  # Do not consume bytes from a TLS tunnel.
                if not block:
                    return
                head += block
                if len(head) > 65536:
                    return
            method, target, _ = bytes(head).split(b'\r\n', 1)[0].decode('ascii').split(' ')
            with self.server.lock:
                if len(self.server.records) >= 32:
                    return
                self.server.records.append(dict(method=method, target=target,
                    head_b64=base64.b64encode(head).decode()))
            if method == 'CONNECT':
                if self.server.reject_connect:
                    client.sendall(b'HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 6\r\n\r\ndenied')
                    return
                port = int(target.rsplit(':', 1)[1])
                initial = b''
            else:
                parsed = urllib.parse.urlsplit(target)
                port = parsed.port
                lines = bytes(head).split(b'\r\n')
                lines[0] = f'{method} {parsed.path or "/"}{"?" + parsed.query if parsed.query else ""} HTTP/1.1'.encode()
                # Force a finite upstream response for these correctness probes.
                lines = [line for line in lines if not line.lower().startswith((b'connection:', b'proxy-authorization:'))]
                initial = b'\r\n'.join(lines[:-2] + [b'Connection: close', b'', b''])
            with socket.create_connection(('127.0.0.1', port), timeout=5) as remote:
                if initial:
                    remote.sendall(initial)
                else:
                    client.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                client.setblocking(False)
                remote.setblocking(False)
                with selectors.DefaultSelector() as selector:
                    selector.register(client, selectors.EVENT_READ, remote)
                    selector.register(remote, selectors.EVENT_READ, client)
                    while True:
                        ready = selector.select(5)
                        if not ready:
                            return
                        for key, _ in ready:
                            block = key.fileobj.recv(65536)
                            if not block:
                                return
                            # Correctness fixtures are small; use a bounded send
                            # loop and never retain an unbounded relay queue.
                            view = memoryview(block)
                            until = time.monotonic() + 5
                            while view:
                                try:
                                    sent = key.data.send(view)
                                    view = view[sent:]
                                except BlockingIOError:
                                    if time.monotonic() > until:
                                        return
                                    time.sleep(.001)
        except (OSError, ValueError):
            return


def env_for(home):
    env = run.isolated_env(home)
    env.update({key: str(TMP) for key in ('TMPDIR', 'GOTMPDIR', 'CLAUDE_CODE_TMPDIR')})
    return env


class Fixture:
    def __init__(self, binary, plan, root):
        root.mkdir(parents=True)
        self.proc = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        plan = copy.deepcopy(plan)
        plan['phase6'].update(events_path=str(root / 'events.jsonl'),
                              summary_path=str(root / 'summary.json'))
        if plan['phase6'].get('tls'):
            plan['phase6']['tls']['ca_path'] = str(root / 'ca.pem')
        run.write_json(root / 'scenario.json', plan)
        self.log = (root / 'stderr.bin').open('wb')
        self.proc = subprocess.Popen([str(binary), '--scenario', str(root / 'scenario.json')],
                                     env=env_for(root), stdout=subprocess.PIPE,
                                     stderr=self.log, start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise RuntimeError('fixture startup timeout')
            # The banner is the only stdout line, bounded independently.
            banner = self.proc.stdout.readline(65537)
            if len(banner) > 65536:
                raise RuntimeError('fixture startup banner overflow')
            self.info = json.loads(banner)
            self.url = self.info['url']
            self.control_url = self.info.get('control_url', self.url)
        except Exception:
            self.close()
            raise

    def control(self, path, *, post=False):
        request = urllib.request.Request(self.control_url + '/__harness/' + path,
                                         data=b'' if post else None)
        with self.opener.open(request, timeout=1) as response:
            return json.load(response)

    def events(self):
        value = self.control('phase6/events?since=0')
        return value if isinstance(value, list) else value.get('events', [])

    def trace(self):
        return self.control('trace') or []

    def summary(self):
        return self.control('phase6/summary')

    def release(self, name):
        return self.control('phase6/release?gate=' + urllib.parse.quote(name), post=True)

    def close(self):
        if self.proc:
            run.kill_group(self.proc, signal.SIGKILL)
            self.proc.wait()
            self.proc.stdout.close()
            self.log.close()
            self.proc = None


def action_ready(when, state, events):
    if 'pipe_blocked' in when:
        return state.get('pipe_blocked', False) is when['pipe_blocked']
    if 'pipe_pending_bytes' in when:
        return state.get('pipe_pending_bytes', 0) >= when['pipe_pending_bytes']
    if 'start' in when:
        return True
    if 'stdout_bytes' in when:
        return state['stdout_bytes'] >= when['stdout_bytes']
    if 'stdout_batches' in when:
        return state['stdout_batches'] >= when['stdout_batches']
    if 'stdout_rows' in when:
        return state['stdout_rows'] >= when['stdout_rows']
    if 'after_ms' in when:
        return (time.monotonic_ns() - state['armed_ns']) / 1e6 >= when['after_ms']
    if 'event' in when:
        return sum(e.get('kind') == when['event'] and
                   all(e.get(k) == v for k, v in when.items() if k not in ('event', 'count'))
                   for e in events) >= when.get('count', 1)
    raise ValueError(f'unknown action condition: {when}')


def execute(command, env, cwd, case, fixture, out):
    """Own and reap the actual child; retain the raw OS wait status."""
    endpoints = {}
    children = []
    closed_stdout = False
    pipe_probe = None
    for name in ('stdout', 'stderr'):
        mode = case.get('io', {}).get(name, 'pipe')
        if mode in ('pipe', 'nonblocking', 'broken-pipe', 'closed'):
            reader, writer = os.pipe()
            if name == 'stdout' and case.get('pipe_probe'):
                spec = case['pipe_probe']
                fcntl.fcntl(writer, fcntl.F_SETPIPE_SZ, spec['capacity'])
                pipe_probe = dict(requested_capacity=spec['capacity'],
                    capacity=fcntl.fcntl(writer, fcntl.F_GETPIPE_SZ),
                    expected_output_bytes=spec['expected_output_bytes'],
                    max_pending_bytes=0, observations=0, full_since_ns=None,
                    blocked_proven=False, proof=None, negative_control_rejected=False)
            if mode == 'nonblocking':
                os.set_blocking(writer, False)
            if mode in ('broken-pipe', 'closed'):
                os.close(reader)
                reader = None
            closed_stdout |= name == 'stdout' and mode == 'closed'
        elif mode == 'pty':
            reader, writer = os.openpty()
            import tty
            tty.setraw(writer)
        elif mode == 'devfull':
            reader, writer = None, os.open('/dev/full', os.O_WRONLY)
        else:
            raise ValueError(f'unknown IO mode: {mode}')
        if reader is not None:
            endpoints[name] = reader
        children.append(writer)
    proc = None
    selector = selectors.DefaultSelector()
    status = None
    child_exited = False
    failure = None
    started = time.monotonic_ns()
    state = dict(stdout_bytes=0, stderr_bytes=0, stdout_rows=0, stdout_batches=0,
                 stdout_line_bytes=0, armed_ns=started)
    prefixes = {'stdout': bytearray(), 'stderr': bytearray()}
    digests = {n: hashlib.sha256() for n in prefixes}
    actions = [dict(copy.deepcopy(a), met=None) for a in case.get('actions', [])]
    pending = list(actions)
    poll_at = 0
    events = []
    paused = case.get('pause_stdout', False)
    with (out / 'stdout.bin').open('wb') as stdout, (out / 'stderr.bin').open('wb') as stderr:
        files = {'stdout': stdout, 'stderr': stderr}

        def close(name):
            fd = endpoints.pop(name, None)
            if fd is not None:
                try:
                    selector.unregister(fd)
                except KeyError:
                    pass
                os.close(fd)

        try:
            proc = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                    stdout=children[0], stderr=children[1], start_new_session=True,
                                    preexec_fn=(lambda: os.close(1)) if closed_stdout else None)
            for fd in children:
                os.close(fd)
            children.clear()
            for name, fd in endpoints.items():
                os.set_blocking(fd, False)
                if name != 'stdout' or not paused:
                    selector.register(fd, selectors.EVENT_READ, name)
            deadline = started + int(case.get('timeout', 10) * 1e9)
            while True:
                now = time.monotonic_ns()
                if not child_exited:
                    # Keep the exited leader waitable until group cleanup. Its
                    # unreaped PID reserves the PGID while descendant-held pipes
                    # drain, so cleanup cannot signal a newly recycled group.
                    observed = os.waitid(os.P_PID, proc.pid,
                                         os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    child_exited = observed is not None
                if not child_exited and now >= deadline and failure is None:
                    failure = 'timeout'
                    run.kill_group(proc, signal.SIGKILL)
                if pending and now >= poll_at and fixture:
                    events = fixture.events()
                    poll_at = now + 10_000_000
                if pipe_probe and paused and not child_exited and 'stdout' in endpoints:
                    state['pipe_blocked'] = False
                    available = array.array('i', [0])
                    fcntl.ioctl(endpoints['stdout'], 0x541B, available, True)  # Linux FIONREAD.
                    count = available[0]
                    state['pipe_pending_bytes'] = count
                    pipe_probe['observations'] += 1
                    pipe_probe['max_pending_bytes'] = max(pipe_probe['max_pending_bytes'], count)
                    full = count == pipe_probe['capacity'] and state['stdout_bytes'] == 0
                    if full:
                        pipe_probe['full_since_ns'] = pipe_probe['full_since_ns'] or now
                    else:
                        pipe_probe['full_since_ns'] = None
                    if (full and pipe_probe['expected_output_bytes'] > pipe_probe['capacity'] and
                            now - pipe_probe['full_since_ns'] >= case['pipe_probe'].get('full_ms', 100) * 1e6):
                        waits = []
                        wait_details = []
                        for task in list((Path('/proc') / str(proc.pid) / 'task').iterdir())[:128]:
                            try:
                                channel = (task / 'wchan').read_text().strip()
                                syscall = (task / 'syscall').read_text().split()
                                if (channel in ('pipe_write', 'anon_pipe_write') and len(syscall) >= 2 and
                                        syscall[0] in ('1', '20') and syscall[1] == '0x1'):
                                    waits.append(int(task.name))
                                    wait_details.append(dict(tid=int(task.name), wchan=channel,
                                                             syscall=int(syscall[0]), fd=1))
                            except FileNotFoundError:
                                pass
                        if waits:
                            state['pipe_blocked'] = True
                            pipe_probe['blocked_proven'] = True
                            if pipe_probe['proof'] is None:
                                pipe_probe['proof'] = dict(t_ns=now, pending_bytes=count,
                                    drained_bytes=state['stdout_bytes'], pipe_write_tids=waits,
                                    writer_waits=wait_details,
                                    full_since_ns=pipe_probe['full_since_ns'])
                while pending:
                    action = pending[0]
                    if action_ready(action['when'], state, events):
                        action['met'] = True
                    elif child_exited or now - state['armed_ns'] > action.get('within_ms', 5000) * 1e6:
                        action['met'] = False
                        failure = failure or 'missed-action'
                    else:
                        break
                    pending.pop(0)
                    state['armed_ns'] = now
                    if not action['met']:
                        continue
                    action['t_ns'] = now
                    todo = action['do']
                    if 'signal' in todo:
                        if not child_exited:
                            os.kill(proc.pid, getattr(signal, 'SIG' + todo['signal']))
                        else:
                            action['met'] = False
                            failure = failure or 'missed-action'
                    elif 'release' in todo:
                        fixture.release(todo['release'])
                    elif 'close' in todo:
                        close(todo['close'])
                    elif 'resume_stdout' in todo:
                        if paused and 'stdout' in endpoints:
                            selector.register(endpoints['stdout'], selectors.EVENT_READ, 'stdout')
                        paused = False
                    elif 'assert_no_conn_end' in todo:
                        if any(e.get('kind') == 'conn_end' for e in events):
                            action['error'] = 'HTTP/2 connection ended while stdout was paused'
                            failure = failure or 'HTTP2-progress-failed'
                    elif 'assert_running' in todo:
                        if child_exited:
                            action['error'] = 'child exited before the declared hold ended'
                            failure = failure or 'early-exit-during-hold'
                    elif 'assert_not_blocked' in todo:
                        if (not pipe_probe or pipe_probe['expected_output_bytes'] >= pipe_probe['capacity'] or
                                pipe_probe['blocked_proven'] or state.get('pipe_pending_bytes') != pipe_probe['expected_output_bytes']):
                            action['error'] = 'below-capacity negative control did not reject blockage'
                            failure = failure or 'negative-control-failed'
                        else:
                            pipe_probe['negative_control_rejected'] = True
                    elif 'assert_pipe_blocked' in todo:
                        if not state.get('pipe_blocked'):
                            action['error'] = 'pipe writer is no longer proven blocked'
                            failure = failure or 'blocked-writer-proof-lost'
                    else:
                        raise ValueError(f'unknown action: {todo}')
                if child_exited:
                    if paused and 'stdout' in endpoints:
                        selector.register(endpoints['stdout'], selectors.EVENT_READ, 'stdout')
                        paused = False
                    if not endpoints:
                        break
                    if now > deadline + 2_000_000_000:
                        failure = failure or 'descendant-held-pipe'
                        break
                ready = selector.select(.005)
                for key, _ in ready:
                    name = key.data
                    try:
                        data = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    except OSError as error:
                        if error.errno != errno.EIO or case.get('io', {}).get(name) != 'pty':
                            raise
                        data = b''
                    if not data:
                        close(name)
                        continue
                    count = state[name + '_bytes']
                    cap = STDOUT_CAP if name == 'stdout' else STDERR_CAP
                    if count + len(data) > cap:
                        data = data[:max(cap - count, 0)]
                        failure = failure or name + '-overflow'
                        run.kill_group(proc, signal.SIGKILL)
                    files[name].write(data)
                    digests[name].update(data)
                    state[name + '_bytes'] += len(data)
                    prefix = prefixes[name]
                    prefix += data[:max(PREFIX_CAP - len(prefix), 0)]
                    if name == 'stdout':
                        # Count complete lines with constant memory even for a
                        # huge single row. Go always renders one row per LF.
                        parts = data.split(b'\n')
                        for part in parts[:-1]:
                            if state['stdout_line_bytes'] + len(part):
                                state['stdout_rows'] += 1
                            else:
                                state['stdout_batches'] += 1
                            state['stdout_line_bytes'] = 0
                        state['stdout_line_bytes'] += len(parts[-1])
        finally:
            for fd in children:
                os.close(fd)
            if proc:
                # Descendants may outlive a successful leader, even after
                # redirecting their stdio so all capture endpoints reach EOF.
                # Signal before reaping the leader to retain group ownership.
                run.kill_group(proc, signal.SIGKILL)
                _, status, _ = os.wait4(proc.pid, 0)
                proc.returncode = os.waitstatus_to_exitcode(status)
            for name in list(endpoints):
                close(name)
            selector.close()
    if status is None:
        raise RuntimeError('child was not started')
    return {'raw_wait_status': status, 'termination': {'signal': os.WTERMSIG(status)}
            if os.WIFSIGNALED(status) else {'exit': os.WEXITSTATUS(status)},
            'failure': failure, 'actions': actions,
            'elapsed_ms': (time.monotonic_ns() - started) / 1e6,
            **{name + '_bytes': state[name + '_bytes'] for name in prefixes},
            'stdout_rows': state['stdout_rows'], 'stdout_batches': state['stdout_batches'],
            **({'pipe_probe': pipe_probe} if pipe_probe else {}),
            **{name + '_sha256': digests[name].hexdigest() for name in prefixes},
            **{name + '_prefix_b64': base64.b64encode(prefixes[name]).decode() for name in prefixes}}


def ticket(dataset):
    return p3.go_json_string(dataset.encode()).join((b'{"stream":', b'}')).decode()


def basic_output(sequence=0):
    """Independently frozen basic row model, including Go timestamp/framing."""
    timestamp = datetime.datetime(2024, 1, 1) + datetime.timedelta(microseconds=sequence)
    stamp = timestamp.strftime('%Y-%m-%d %H:%M:%S')
    if timestamp.microsecond:
        stamp += '.' + f'{timestamp.microsecond:06d}'.rstrip('0')
    return (b'{"large_integer":' + str(9007199254740993 + sequence).encode() +
            b',"message":' + p3.go_json_string('fixture <event> café 日志'.encode()) +
            b',"optional":null,"p_timestamp":' + p3.go_json_string(stamp.encode()) + b',"sequence":' +
            str(sequence).encode() + b',"value":' + p3.go_float(sequence / 4).encode() + b'}\n\n')


def corpus():
    cases = []

    def add(name, ops=None, *, schema='basic', subscriptions=None, **extra):
        dataset = extra.pop('dataset', 'p6-' + name)
        auth = extra.pop('auth', 'basic')
        flight_auth = extra.pop('flight_auth', p2.HEADERS[auth])
        phase6 = dict(expected_output=True, flight_auth=flight_auth,
                      streams={dataset: dict(schema=schema, subscriptions=subscriptions or
                                             [dict(ops=ops or [dict(op='batch', rows=2)])])})
        phase6.update(extra.pop('phase6', {}))
        tls = auth.startswith('cloud')
        if tls:
            phase6.setdefault('tls', dict(grpc=True, http=False))
        case = dict(name='p6-' + name, group=extra.pop('group', 'values'), build='cloud' if tls else 'standard',
                    dataset=dataset, auth=auth, argv=['tail', dataset, '-o', 'json'],
                    fixture=dict(headers=p2.HEADERS[auth], routes=[dict(method='GET', path=ABOUT,
                                 body='{"grpcPort":{{grpc_port}}}')], phase6=phase6),
                    expected=dict(termination={'exit': 1}, dogets=1, http=[('GET', ABOUT)],
                                  output_summary=True, auth=flight_auth, http_auth=[p2.HEADERS[auth]]), **extra)
        if 'expect' in case:
            case['expected'].update(case.pop('expect'))
        cases.append(case)
        return case

    for auth in p2.AUTH:
        add('auth-' + auth, auth=auth, group='auth')
    add('ticket-escaping', dataset='p6-<>&"\\ café\u2028\u2029', group='auth')
    for schema in ('basic', 'wide', 'values', 'dup', 'nan', 'dictionary', 'empty', 'union'):
        add('schema-' + schema, [dict(op='batch', rows=12 if schema == 'values' else 4 if schema == 'nan' else 2)], schema=schema)
    for schema in ('ree', 'invalid_utf8'):
        add('schema-' + schema, [dict(op='batch', rows=4)], schema=schema)
    add('framing-empty-mixed', [dict(op='batch', rows=0), dict(op='batch', rows=2),
                               dict(op='batch', rows=0), dict(op='batch', rows=1)])
    for mode in ('replace', 'delta'):
        c = add('dictionary-' + mode, [dict(op='batch', rows=4, count=3)], schema='dictionary')
        c['fixture']['phase6']['streams'][c['dataset']]['dictionary'] = dict(cardinality=3, mode=mode)
    for compression in ('lz4', 'zstd'):
        c = add('compression-' + compression)
        c['fixture']['phase6']['streams'][c['dataset']]['compression'] = compression
    for stage in ('doget', 'init', 'schema', 'batch'):
        for code, label in enumerate(CODES):
            initial = [] if stage == 'doget' else [dict(op='headers')] if stage == 'init' else [dict(op='schema')]
            if stage == 'batch':
                initial.append(dict(op='batch', rows=2))
            retry = stage in ('schema', 'batch') and code in (0, 1, 14)
            subs = [dict(ops=[*initial, dict(op='status', code=code, message='phase6 ' + label)])]
            if retry:
                subs.append(dict(ops=[dict(op='batch', rows=1), dict(op='status', code=3,
                                      message='phase6 gated terminal')]))
            add('status-' + stage + '-' + label.lower(), subscriptions=subs, group='status',
                expect=dict(dogets=2 if retry else 1, retry=retry,
                            error_code='COMMAND_FAILED'))
    for count in (1, 2):
        add('metadata-first-' + str(count), [*[dict(op='metadata_only', app_metadata='p6') for _ in range(count)],
                                            dict(op='batch', rows=2)], group='protocol',
            expect={} if count == 1 else dict(output_summary=False, stdout_sha256=run.digest(b''),
                stdout_rows=0, stdout_batches=0, error_code='COMMAND_FAILED'))
    add('metadata-after-batch', [dict(op='batch', rows=1), dict(op='metadata_only', app_metadata='p6'),
                                 dict(op='batch', rows=1)], group='protocol',
        expect=dict(output_summary=False, stdout_sha256=run.digest(basic_output()),
                    stdout_rows=1, stdout_batches=1, error_code='COMMAND_FAILED'))
    add('batch-visible-before-next', [dict(op='batch', rows=1), dict(op='gate', name='next'),
                                     dict(op='batch', rows=1)], group='lifecycle', actions=[
        dict(when=dict(stdout_batches=1), do=dict(release='next'))])
    add('dictionary-missing-initial', [dict(op='corrupt', kind='missing_dictionary')],
        schema='dictionary', group='protocol', expect=dict(output_summary=False))
    add('dictionary-full-initial', [dict(op='schema'), dict(op='corrupt', kind='dictionary_only'),
                                   dict(op='batch', rows=4)], schema='dictionary', group='protocol')
    add('blocked-writer-http2-progress', [dict(op='batch', rows=128, payload_bytes=16384),
                                         dict(op='gate', name='progress')], group='lifecycle',
        phase6=dict(keepalive=dict(time_ms=1000, timeout_ms=300)), pause_stdout=True,
        actions=[dict(when=dict(event='gate_wait', name='progress'), do=dict(assert_no_conn_end=True)),
                 dict(when=dict(after_ms=1800), do=dict(assert_no_conn_end=True)),
                 dict(when=dict(start=True), do=dict(resume_stdout=True)),
                 dict(when=dict(start=True), do=dict(release='progress'))])
    for kind in ('garbage_header', 'truncated_body', 'empty_message', 'foreign_batch', 'dictionary_before_schema'):
        add('corrupt-' + kind, [dict(op='corrupt', kind=kind)], group='protocol',
            expect=dict(output_summary=False))
    add('missing-schema', [dict(op='eof')], group='protocol')
    add('schema-change', [dict(op='batch', rows=1), dict(op='schema', schema='dup'),
                          dict(op='batch', rows=1)], group='protocol', expect=dict(output_summary=False))
    for size in (4194303, 4194304, 4194305):
        add('receive-cap-' + str(size), [dict(op='batch', rows=1, target_message_bytes=size)],
            group='protocol', expect=dict(output_summary=size <= 4194304))
    for sig in ('INT', 'TERM'):
        for stage in ('discovery', 'init', 'idle', 'write', 'reconnect'):
            if stage == 'discovery':
                c = add('signal-' + sig.lower() + '-' + stage, group='signals',
                        phase6=dict(http=[dict(method='GET', path=ABOUT,
                            responses=[dict(status=200, body='{"grpcPort":{{grpc_port}}}', gate='discovery')])]),
                        actions=[dict(when=dict(event='gate_wait', name='discovery'), do=dict(signal=sig))],
                        expect=dict(termination={'signal': getattr(signal, 'SIG' + sig)}, dogets=0,
                                    output_summary=False, error_code=None))
            else:
                ops = [dict(op='gate', name=stage)] if stage == 'init' else [dict(op='schema'), dict(op='gate', name=stage)]
                if stage == 'write':
                    ops = [dict(op='batch', rows=128, payload_bytes=16384), dict(op='hold')]
                if stage == 'reconnect':
                    ops = [dict(op='schema'), dict(op='status', code=14, message='phase6 retry')]
                when = dict(event='terminal', code='Unavailable') if stage == 'reconnect' else dict(event='batch_emit_done') if stage == 'write' else dict(event='gate_wait', name=stage)
                c = add('signal-' + sig.lower() + '-' + stage, ops, group='signals',
                        pause_stdout=stage == 'write', actions=[dict(when=when, do=dict(signal=sig))],
                        expect=dict(termination={'signal': getattr(signal, 'SIG' + sig)},
                                    output_summary=False, error_code=None))
    for mode in ('devfull', 'closed', 'broken-pipe', 'nonblocking', 'pty'):
        add('stdout-' + mode, [dict(op='batch', rows=128, payload_bytes=256),
                              dict(op='status', code=3, message='fixture stream complete')],
            io=dict(stdout=mode), group='writer',
            expect=dict(output_summary=mode == 'pty', termination={'signal': 13} if mode == 'broken-pipe' else {'exit': 1}))
    add('broken-pipe-after-idle', [dict(op='batch', rows=1), dict(op='gate', name='idle-write'),
                                  dict(op='batch', rows=1)], group='writer', actions=[
        dict(when=dict(stdout_batches=1), do=dict(close='stdout')),
        dict(when=dict(event='gate_wait', name='idle-write'), do=dict(release='idle-write'))],
        expect=dict(termination={'signal': 13}, output_summary=False))
    add('broken-pipe-after-reconnect', subscriptions=[
        dict(ops=[dict(op='batch', rows=1), dict(op='status', code=14, message='phase6 retry')]),
        dict(ops=[dict(op='batch', rows=1)])], group='writer', actions=[
            dict(when=dict(stdout_batches=1), do=dict(close='stdout'))],
        expect=dict(termination={'signal': 13}, dogets=2, retry=True, output_summary=False))
    for mode in ('pipe', 'pty'):
        c = add('text-stderr-' + mode, io=dict(stderr=mode), group='writer')
        c['argv'][-1] = 'text'
    # Discovery rotates credentials; Flight must use the resulting snapshot and
    # Unauthenticated must terminate without another HTTP refresh.
    refreshed = 'fixture-new-session'
    oauth_headers = {'cookie': 'session=' + refreshed, 'x-p-tenant': 'fixture-tenant'}
    c = add('oauth-discovery-snapshot', [dict(op='batch', rows=1), dict(op='status', code=16,
             message='phase6 flight unauthenticated')], auth='cloud-cookie', group='auth',
            flight_auth=oauth_headers, phase6=dict(http=[
                dict(method='GET', path=ABOUT, responses=[
                    dict(require={'cookie': 'session=' + refreshed}, status=200,
                         body='{"grpcPort":{{grpc_port}}}'), dict(status=401, body='expired')]),
                dict(method='POST', path=TOKEN, responses=[dict(status=200,
                    body=json.dumps(dict(access_token=refreshed, refresh_token='fixture-new-refresh')))])]),
            profile_extra=dict(refresh_token='fixture-refresh', orchestrator_url='{{url}}'),
            expect=dict(http=[('GET', ABOUT), ('POST', TOKEN), ('GET', ABOUT)],
                        config_session=refreshed, http_auth=None))
    add('cloud-cookie-raw', auth='cloud-cookie', group='auth',
        profile_extra=dict(session_token='fixture token,raw'),
        flight_auth={'cookie': 'session=fixture token,raw', 'x-p-tenant': 'fixture-tenant'},
        phase6=dict(http=[dict(method='GET', path=ABOUT, responses=[
            dict(status=200, body='{"grpcPort":{{grpc_port}}}')])]),
        expect=dict(http_auth=[{'cookie': 'session="fixture token,raw"', 'x-p-tenant': 'fixture-tenant'}]))
    for auth, extra in [('api-key', dict(apiKey='fixture-keyé')),
                         ('cloud-key', dict(tenant_id='fixture-tenanté'))]:
        add('metadata-invalid-' + auth, auth=auth, profile_extra=extra, group='auth',
            phase6=dict(http=[dict(method='GET', path=ABOUT, responses=[
                dict(status=200, body='{"grpcPort":{{grpc_port}}}')])]),
            expect=dict(dogets=0, output_summary=False, error_code='COMMAND_FAILED',
                        http_auth=[{'x-api-key': 'fixture-keyé'}] if auth == 'api-key' else
                                  [{'x-api-key': 'fixture-key', 'x-p-tenant': 'fixture-tenanté'}]))
    for no_alpn in (False, True):
        add('tls-' + ('no-alpn' if no_alpn else 'alpn'), group='tls',
            phase6=dict(tls=dict(grpc=True, http=True, no_alpn=no_alpn)))
    add('tls-no-alpn-enforced', group='tls', env=dict(GRPC_ENFORCE_ALPN_ENABLED='true'),
        phase6=dict(tls=dict(grpc=True, http=False, no_alpn=True)),
        auth='cloud-key', expect=dict(dogets=0, output_summary=False))
    for status in (201, 204, 400, 401, 403, 429, 500):
        c = add('discovery-http-' + str(status), group='discovery',
                expect=dict(dogets=0, output_summary=False))
        c['fixture']['routes'][0].update(status=status, body='phase6 rejected discovery')
    for name, body in [('empty', ''), ('malformed', '{'), ('string-port', '{"grpcPort":"1"}'),
                       ('fraction-port', '{"grpcPort":1.5}')]:
        c = add('discovery-' + name, group='discovery', expect=dict(dogets=0, output_summary=False))
        c['fixture']['routes'][0]['body'] = body
    for name, body in [('missing', '{}'), ('null', '{"grpcPort":null}'), ('zero', '{"grpcPort":0}'),
                       ('negative', '{"grpcPort":-1}'), ('out-of-range', '{"grpcPort":70000}'),
                       ('refused', '{"grpcPort":1}')]:
        c = add('port-' + name, group='discovery',
                expect=dict(dogets=0, output_summary=False, error_code='COMMAND_FAILED'))
        c['fixture']['routes'][0]['body'] = body
    for name, argv in [('no-args', ['tail', '-o', 'json']),
                       ('two-args', ['tail', 'a', 'b', '-o', 'json']),
                       ('invalid-output', ['tail', 'p6-invalid-output', '-o', 'yaml'])]:
        c = add('cli-' + name, group='cli', expect=dict(dogets=0, http=[], output_summary=False))
        c['expected']['http_auth'] = []
        c['argv'] = argv
    for name, output in [('normalized', ' JSON '), ('empty', '')]:
        c = add('output-' + name, group='cli')
        c['argv'][-1] = output
    c = add('no-profile', group='cli', unconfigured=True,
            expect=dict(dogets=0, http=[], http_auth=[], output_summary=False))
    c = add('no-profile-before-output', group='cli', unconfigured=True,
            expect=dict(dogets=0, http=[], http_auth=[], output_summary=False))
    c['argv'][-1] = 'yaml'
    add('ipv6-address', group='tls', phase6=dict(listen='::1'))
    for mode in ('plaintext', 'cloud-tls', 'https'):
        for authenticated in (False, True):
            auth = 'cloud-key' if mode == 'cloud-tls' else 'basic'
            tls = dict(grpc=mode != 'plaintext', http=mode == 'https',
                       names=['flight.fixture.invalid', 'localhost', '127.0.0.1'])
            add('proxy-' + mode + ('-auth' if authenticated else ''), group='proxy', auth=auth,
                proxy=dict(authenticated=authenticated), phase6=dict(tls=tls),
                expect=dict(proxy_connects=2 if mode == 'https' else 1))
    add('proxy-407', group='proxy', proxy=dict(reject_connect=True),
        expect=dict(dogets=0, output_summary=False, proxy_connects=1))
    add('proxy-loopback-bypass', group='proxy', proxy=dict(loopback=True),
        expect=dict(proxy_connects=0))
    # Transport review probes are finite, causal correctness experiments.
    for stage in ('schema', 'batch'):
        for kind in ('conn_close', 'conn_reset', 'goaway', 'drain'):
            conn_op = dict(op='goaway' if kind == 'drain' else kind, settle_ms=50)
            if kind == 'drain':
                conn_op.update(last_stream_id=2147483647, close_after_ms=100)
            initial = [dict(op='schema')]
            if stage == 'batch':
                initial.append(dict(op='batch', rows=1))
            add('connection-' + stage + '-' + kind, group='transport', subscriptions=[
                dict(ops=[*initial, conn_op]), dict(ops=[dict(op='batch', rows=1)])],
                expect=dict(dogets=2, retry=True, retry_clock='doget'))
    for stage in ('doget', 'batch'):
        ops = [] if stage == 'doget' else [dict(op='batch', rows=1)]
        add('trailers-large-' + stage, [*ops, dict(op='status', code=3, message='phase6 large trailer',
            message_bytes=65536, trailer_pad_bytes=8192)], group='transport',
            expect=dict(error_code='COMMAND_FAILED'))
    for tls in (False, True):
        auth = 'cloud-key' if tls else 'basic'
        add('blackhole-' + ('tls' if tls else 'plaintext'), auth=auth, group='transport',
            phase6=dict(tls=dict(grpc=False, http=False),
                        endpoints=[dict(name='bh', kind='blackhole', tls=tls, hold_ms=60000)],
                        http=[dict(method='GET', path=ABOUT, responses=[dict(status=200,
                            body='{"grpcPort":{{port:bh}}}')])]),
            actions=[dict(when=dict(event='endpoint_accept', name='bh'), do=dict(assert_running=True)),
                     dict(when=dict(after_ms=2000), do=dict(assert_running=True)),
                     dict(when=dict(start=True), do=dict(signal='TERM'))],
            expect=dict(dogets=0, output_summary=False, termination={'signal': 15}))
        add('endpoint-502-' + ('tls' if tls else 'plaintext'), auth=auth, group='transport',
            phase6=dict(tls=dict(grpc=False, http=False),
                        endpoints=[dict(name='html', kind='http_status', tls=tls, status=502,
                                        content_type='text/html', body='<html>bad gateway</html>')],
                        http=[dict(method='GET', path=ABOUT, responses=[dict(status=200,
                            body='{"grpcPort":{{port:html}}}')])]),
            expect=dict(dogets=0, output_summary=False, error_code='COMMAND_FAILED'))
        add('preface-deadline-' + ('tls' if tls else 'plaintext'), auth=auth, group='deadline', timeout=30,
            phase6=dict(tls=dict(grpc=False, http=False),
                        endpoints=[dict(name='preface', kind='blackhole', tls=tls, hold_ms=60000)],
                        http=[dict(method='GET', path=ABOUT, responses=[dict(status=200,
                            body='{"grpcPort":{{port:preface}}}')])]),
            expect=dict(dogets=0, output_summary=False, error_code='COMMAND_FAILED',
                        deadline_ms=[18000, 26000]))
    add('protocol-plaintext-to-tls', group='transport',
        phase6=dict(tls=dict(grpc=True, http=False, reset_invalid_handshake=True)),
        expect=dict(dogets=0, output_summary=False, error_code='COMMAND_FAILED'))
    add('protocol-tls-to-plaintext', auth='cloud-key', group='transport',
        phase6=dict(tls=dict(grpc=False, http=False)),
        expect=dict(dogets=0, output_summary=False, error_code='COMMAND_FAILED'))
    # An ordinary grpc-go server sanitizes these headers. Use exact scripted
    # HTTP/2 HEADERS/DATA on a dedicated endpoint, not helper-only tests.
    missing_text = ('protocol error: missing grpc-status trailer, stream was terminated without a final status '
                    '(possible truncation by a proxy or load balancer)')
    good_headers = dict(act='headers', fields=[[':status', '200'], ['content-type', 'application/grpc']])
    fatal = dict(act='trailers', fields=[['grpc-status', '3'], ['grpc-message', 'phase6 raw terminal']])

    def raw(name, acts=None, *, subscriptions=None, **extra):
        expected = dict(summary_key='endpoint:raw', error_code='COMMAND_FAILED')
        expected.update(extra.pop('expect', {}))
        return add('raw-' + name, group='raw', phase6=dict(endpoints=[dict(name='raw', kind='raw_h2',
            schema='basic', subscriptions=subscriptions or [dict(acts=acts)])],
            http=[dict(method='GET', path=ABOUT, responses=[dict(status=200,
                body='{"grpcPort":{{port:raw}}}')])]), expect=expected, **extra)

    for stage in ('initial', 'batch'):
        if stage == 'initial':
            acts = [dict(act='headers', fields=[[':status', '200'], ['content-type', 'application/grpc'],
                    ['grpc-status', '0'], ['grpc-status-details-bin', '!!']], end_stream=True)]
        else:
            acts = [good_headers, dict(act='schema'), dict(act='batch', rows=1),
                    dict(act='trailers', fields=[['grpc-status', '3'], ['grpc-status-details-bin', '!!']])]
        raw('details-invalid-' + stage, acts)
    for code in (0, 1, 14, 3):
        retry = code in (0, 1, 14)
        subs = [dict(acts=[good_headers, dict(act='schema'), dict(act='batch', rows=1),
                          dict(act='trailers', fields=[['grpc-status', str(code)], ['grpc-message', '%FF']])])]
        if retry:
            subs.append(dict(acts=[good_headers, dict(act='schema'), dict(act='batch', rows=1), fatal]))
        raw('message-ff-' + str(code), subscriptions=subs,
            expect=dict(dogets=2 if retry else 1, retry=retry))
    for code in (0, 1, 14):
        raw('message-ff-initial-' + str(code), [dict(act='headers', fields=[[':status', '200'],
            ['content-type', 'application/grpc'], ['grpc-status', str(code)], ['grpc-message', '%FF']], end_stream=True)])
    for label, encoded in [('ff', '%FF'), ('partial', 'a%E9b'), ('partial-two', 'a%E9%80b')]:
        for output in ('json', 'text'):
            c = raw('message-' + label + '-fatal-' + output,
                [good_headers, dict(act='schema'), dict(act='batch', rows=1),
                 dict(act='trailers', fields=[['grpc-status', '3'], ['grpc-message', encoded]])],
                expect=dict(error_code='COMMAND_FAILED' if output == 'json' else None))
            c['argv'][-1] = output
    for goaway in (False, True):
        acts = [good_headers, dict(act='schema'), dict(act='batch', rows=1)]
        if goaway:
            acts += [dict(act='sleep', ms=50), dict(act='goaway', last='ours', code=0)]
        acts.append(dict(act='trailers', fields=[['grpc-status', '2'], ['grpc-message',
            urllib.parse.quote(missing_text, safe='')]]))
        raw('explicit-unknown-missing-text' + ('-goaway-ours' if goaway else ''), acts)
    for stage in ('initial', 'batch'):
        prefix = [good_headers] if stage == 'initial' else [good_headers, dict(act='schema'), dict(act='batch', rows=1)]
        raw('rst-no-error-' + stage, [*prefix, dict(act='rst', code=0)])
        raw('missing-data-end-' + stage, [*prefix, dict(act='end_stream')])
        raw('missing-status-' + stage, [*prefix, dict(act='trailers', fields=[['x-fixture', 'no-status']])])
        raw('malformed-status-' + stage, [*prefix, dict(act='trailers', fields=[['grpc-status', 'abc']])])
    for last in ('below', 'ours'):
        acts = [good_headers, dict(act='schema'), dict(act='batch', rows=1), dict(act='sleep', ms=50),
                dict(act='goaway', last=last, code=0)]
        if last == 'below':
            acts += [dict(act='close')]
            subs = [dict(acts=acts), dict(acts=[good_headers, dict(act='schema'), dict(act='batch', rows=1), fatal])]
        else:
            acts += [dict(act='batch', rows=1), fatal]
            subs = [dict(acts=acts)]
        raw('goaway-' + last, subscriptions=subs,
            expect=dict(dogets=2 if last == 'below' else 1, retry=last == 'below', retry_clock='doget'))
    for status, content_type, body in [(200, 'text/html', b'<html>bad gateway</html>'),
            (400, 'application/grpc', b''), (401, 'application/grpc', b''),
            (403, 'application/grpc', b''), (404, 'application/grpc', b''),
            (429, 'application/grpc', b''), (502, 'text/html', b'<html>bad gateway</html>'),
            (503, None, b''), (504, 'application/grpc', b''), (599, 'text/plain', b'')]:
        fields = [[':status', str(status)]]
        if content_type:
            fields.append(['content-type', content_type])
        raw('http-' + str(status), [dict(act='headers', fields=fields),
            dict(act='data', b64=base64.b64encode(body).decode(), end_stream=True)])
    return cases


def request_contract(trace):
    return [dict(protocol=r['protocol'], method=r['method'], path=r['path'],
                 headers={k: v for k, v in r['headers'].items() if k in AUTH_KEYS},
                 body=r['body'], unexpected=r['unexpected']) for r in trace]


def trailer_followups():
    """Separate bounded trailer-discard probes; never silently replace main."""
    templates = {c['name']: c for c in corpus()}
    cases = []
    for stage in ('schema', 'batch'):
        for code in (0, 1, 14, 3):
            for ending in ('close', 'goaway'):
                case = copy.deepcopy(templates['p6-raw-message-ff-' + str(code)])
                old_dataset = case['dataset']
                name = f'p6-trailer-followup-{stage}-{code}-{ending}'
                case.update(name=name, dataset=name, group='trailer-followup')
                case['argv'][1] = name
                phase6 = case['fixture']['phase6']
                phase6['streams'][name] = phase6['streams'].pop(old_dataset)
                acts = [dict(act='headers', fields=[[':status', '200'], ['content-type', 'application/grpc']]),
                        dict(act='schema')]
                if stage == 'batch':
                    acts.append(dict(act='batch', rows=1))
                acts += [dict(act='sleep', ms=50), dict(act='trailers', fields=[['grpc-status', str(code)],
                    ['grpc-message', 'phase6 trailer code ' + str(code)]])]
                if ending == 'goaway':
                    acts.append(dict(act='goaway', last='below', code=0))
                acts.append(dict(act='close'))
                phase6['endpoints'][0]['subscriptions'][0] = dict(acts=acts)
                case['expected']['retry_clock'] = 'doget'
                cases.append(case)
    return cases


def review3_followups():
    """Bounded framing/details/refusal probes, separate from the main freeze."""
    template = next(c for c in corpus() if c['name'] == 'p6-raw-message-ff-3')
    headers = dict(act='headers', fields=[[':status', '200'], ['content-type', 'application/grpc']])
    fatal = dict(act='trailers', fields=[['grpc-status', '3'], ['grpc-message', 'phase6 review3 terminal']])
    normal = dict(acts=[headers, dict(act='schema'), dict(act='batch', rows=1), fatal])
    cases = []

    def add(label, subscriptions, **expected):
        case = copy.deepcopy(template)
        name = 'p6-review3-' + label
        old = case['dataset']
        case.update(name=name, dataset=name, group='review3-followup')
        case['argv'][1] = name
        phase6 = case['fixture']['phase6']
        phase6['streams'][name] = phase6['streams'].pop(old)
        phase6['endpoints'][0]['subscriptions'] = copy.deepcopy(subscriptions)
        case['expected'].update(expected)
        cases.append(case)

    shapes = {'prefix3': bytes(3), 'prefix5': bytes(1) + (8).to_bytes(4, 'big'),
              'body3of8': bytes(1) + (8).to_bytes(4, 'big') + b'abc'}
    for stage in ('init', 'batch'):
        prefix = [headers] if stage == 'init' else [headers, dict(act='schema'), dict(act='batch', rows=1)]
        for shape, payload in shapes.items():
            for code in (0, 1, 14, 3):
                acts = [*prefix, dict(act='data', b64=base64.b64encode(payload).decode()),
                        dict(act='trailers', fields=[['grpc-status', str(code)], ['grpc-message', 'partial terminal']])]
                # The final subscription also bounds a client that mistakenly retries.
                add(f'partial-{stage}-{shape}-{code}', [dict(acts=acts), normal])
            add(f'partial-{stage}-{shape}-dataend', [dict(acts=[*prefix,
                dict(act='data', b64=base64.b64encode(payload).decode(), end_stream=True)]), normal])

    def status_proto(code, message):
        # These deliberately tiny fixtures need only protobuf's one-byte lengths.
        return base64.b64encode(bytes([8, code, 18, len(message)]) + message).decode()

    details = [('match3', 3, [status_proto(3, b'proto message')], False),
               ('match14', 14, [status_proto(14, b'proto message')], True),
               ('match1', 1, [status_proto(1, b'proto message')], True),
               ('match0', 0, [status_proto(0, b'proto message')], True),
               ('mismatch14vs3', 14, [status_proto(3, b'proto message')], False),
               ('empty14', 14, [''], False),
               ('duplicate14', 14, [status_proto(3, b'first'), status_proto(14, b'second')], True),
               ('invalidproto14', 14, [base64.b64encode(b'\xff').decode()], True),
               ('invalidutf814', 14, [status_proto(14, b'\xff')], True)]
    for stage in ('init', 'batch'):
        prefix = [headers] if stage == 'init' else [headers, dict(act='schema'), dict(act='batch', rows=1)]
        for label, code, values, retry in details:
            fields = [['grpc-status', str(code)], ['grpc-message', 'header message']]
            fields += [['grpc-status-details-bin', value] for value in values]
            subs = [dict(acts=[*prefix, dict(act='trailers', fields=fields)])]
            retry = retry and stage == 'batch'
            if retry:
                subs.append(normal)
            add(f'details-{stage}-{label}', subs, dogets=2 if retry else 1, retry=retry)

    for kind in ('goaway', 'rst'):
        for stage in ('beforeheaders', 'afterheaders'):
            for refusals in (1, 2):
                acts = [headers] if stage == 'afterheaders' else []
                acts += ([dict(act='goaway', last='below', code=0), dict(act='close')]
                         if kind == 'goaway' else [dict(act='rst', code=7)])
                subs = [dict(acts=acts) for _ in range(refusals)] + [normal]
                add(f'refused-{kind}-{stage}-{refusals}', subs, dogets=2,
                    transparent_retry=True)
    return cases


def authority_contract(event, expected_port):
    """Replace only the selected discovery port, preserving wrong numeric ports."""
    values = []
    for value in event.get('metadata', {}).get(':authority', []):
        match = re.fullmatch(r'(\[[^]]+\]|[^:]+):([0-9]+)', value)
        if match and expected_port is not None and match[2] == str(expected_port):
            value = match[1] + ':{{grpc_port}}'
        values.append(value)
    return values


def discovered_flight_port(case, info, trace):
    """Replay selected about route/auth rules against the immutable banner.

    This uses the discovery response selected by the fixture scenario, never
    the client's authority or the set of every offered endpoint port.
    """
    used = {}
    selected = None
    for request in trace:
        if request['protocol'] != 'http' or request['method'] != 'GET' or request['path'] != ABOUT:
            continue
        route = next((r for r in case['fixture']['phase6'].get('http', [])
                      if r['method'] == 'GET' and r['path'] == ABOUT), None)
        response = None
        if route:
            headers = {k.lower(): v for k, v in request.get('headers', {}).items()}
            for index, candidate in enumerate(route['responses']):
                if candidate.get('times', 0) > 0 and used.get(index, 0) >= candidate['times']:
                    continue
                if any(headers.get(k.lower(), '') != v for k, v in candidate.get('require', {}).items()):
                    continue
                used[index] = used.get(index, 0) + 1
                response = candidate
                break
        else:
            response = next((r for r in case['fixture'].get('routes', [])
                             if r['method'] == 'GET' and r['path'] == ABOUT), None)
        if not response or (response.get('status', 200) or 200) != 200:
            continue
        body = response.get('body', '').replace('{{grpc_port}}', str(info['grpc_port']))
        for name, port in info.get('endpoints', {}).items():
            body = body.replace('{{port:' + name + '}}', str(port))
        try:
            port = json.loads(body).get('grpcPort')
        except (ValueError, AttributeError):
            continue
        if type(port) is int and 0 < port <= 65535:
            selected = port
    return selected


def is_doget(event):
    return event.get('kind') in ('doget', 'raw_h2_stream')


def sample(binary, fixture_binary, case, out):
    # Attribute the capture to the inputs present before either child starts.
    # Reading only after execute() can mislabel an unpinned running executable
    # when its pathname is replaced with the pinned binary during the sample.
    binary_hash = run.digest(Path(binary).read_bytes())
    fixture_hash = run.digest(Path(fixture_binary).read_bytes())
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    TMP.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='p6-compat-', dir=TMP))
    try:
        home = root / 'home'
        for name in ('config', 'cache', 'tmp'):
            (home / name).mkdir(parents=True)
        fx = Fixture(fixture_binary, case['fixture'], out / 'fixture')
        proxy = None
        try:
            profile_url = fx.url
            if case.get('proxy'):
                proxy = Proxy(case['proxy'].get('reject_connect', False))
                if not case['proxy'].get('loopback'):
                    profile_url = fx.url.replace('127.0.0.1', 'flight.fixture.invalid')
            profile = p2.profile(case['auth'], url=profile_url, **case.get('profile_extra', {}))
            profile = {k: v.replace('{{url}}', fx.url) if isinstance(v, str) else v for k, v in profile.items()}
            if not case.get('unconfigured'):
                p2.seed(home, dict(config=dict(toml=p2.config_toml({'fixture': profile}))), fx.url)
            replacements = {str(home): '{{home}}', profile_url: '{{url}}', fx.url: '{{url}}'}
            before = run.config_snapshot(home, replacements)
            env = env_for(home)
            env.update(case.get('env', {}))
            if proxy:
                proxy_url = proxy.url
                if case['proxy'].get('authenticated'):
                    proxy_url = proxy_url.replace('http://', 'http://fixture-proxy:fixture-password@')
                env.update(HTTP_PROXY=proxy_url, HTTPS_PROXY=proxy_url, NO_PROXY='')
            if fx.info.get('ca_path'):
                env['SSL_CERT_FILE'] = fx.info['ca_path']
            result = execute([str(binary), *case['argv']], env, home, case, fx, out)
            trace = fx.trace()
            result['requests'] = request_contract(trace)
            result['events'] = fx.events()
            result['fixture_info'] = fx.info
            result['discovered_flight_port'] = discovered_flight_port(case, fx.info, trace)
            result['flight_user_agents'] = [e.get('metadata', {}).get('user-agent', [])
                                            for e in result['events'] if is_doget(e)]
            result['flight_authorities'] = [authority_contract(e, result['discovered_flight_port'])
                                            for e in result['events'] if is_doget(e)]
            result['summary'] = fx.summary()
            result['config_before'] = before
            result['config_after'] = run.config_snapshot(home, replacements)
            # Protocol mismatch errors include both actual socket endpoints.
            # Replace only socket identities explicitly reported by this
            # fixture, preserving error text and connection ordinals.
            client_sockets = []
            for event in result['events']:
                remote = event.get('remote')
                if event.get('kind') in ('conn_begin', 'tls_handshake', 'endpoint_accept', 'raw_h2_conn') and remote:
                    if remote not in client_sockets:
                        client_sockets.append(remote)
            diagnostic_replacements = dict(replacements)
            for index, remote in enumerate(client_sockets):
                diagnostic_replacements[remote] = '{{client_socket_' + str(index) + '}}'
            host = urllib.parse.urlsplit(fx.url).hostname
            socket_host = '[' + host + ']' if ':' in host else host
            diagnostic_replacements[socket_host + ':' + str(fx.info['grpc_port'])] = '{{flight_socket}}'
            for name, port in fx.info.get('endpoints', {}).items():
                diagnostic_replacements[socket_host + ':' + str(port)] = '{{endpoint:' + name + '}}'
            proxy_replacements = {**replacements,
                'flight.fixture.invalid:' + str(fx.info['grpc_port']): '{{flight}}',
                'flight.fixture.invalid:' + str(urllib.parse.urlsplit(fx.url).port): '{{http}}'}
            result['proxy_requests'] = [dict(method=r['method'],
                target=run.normalize(r['target'].encode(), proxy_replacements).decode(),
                head_b64=base64.b64encode(run.normalize(base64.b64decode(r['head_b64']),
                                                      proxy_replacements)).decode())
                for r in proxy.records if r['method'] == 'CONNECT'] if proxy else []
            # Only ephemeral endpoint/home replacement; ANSI and whitespace are
            # deliberately retained. Hashes above are always the untouched bytes.
            stderr = (out / 'stderr.bin').read_bytes()
            result['stderr_contract_b64'] = base64.b64encode(run.normalize(stderr, diagnostic_replacements)).decode()
            result['binary_sha256'] = binary_hash
            result['fixture_sha256'] = fixture_hash
            if run.digest(Path(binary).read_bytes()) != binary_hash:
                raise ValueError('executable changed during sample: ' + str(binary))
            if run.digest(Path(fixture_binary).read_bytes()) != fixture_hash:
                raise ValueError('fixture executable changed during sample: ' + str(fixture_binary))
            run.write_json(out / 'result.json', result)
            return result
        finally:
            if proxy:
                proxy.close()
            fx.close()
    finally:
        shutil.rmtree(root)


def expectation_issues(case, result):
    issues = []
    want = case['expected']
    if result.get('failure'):
        issues.append('collector failure: ' + result['failure'])
    if any(a['met'] is not True for a in result.get('actions', [])):
        issues.append('causal action not reached')
    if result['termination'] != want['termination']:
        issues.append(f'termination {result["termination"]}, expected {want["termination"]}')
    if want.get('deadline_ms'):
        low, high = want['deadline_ms']
        if not low <= result.get('elapsed_ms', 0) <= high:
            issues.append(f'preface deadline {result.get("elapsed_ms")}ms outside [{low},{high}]ms')
    requests = result['requests']
    if any(e.get('kind') == 'fixture_panic' for e in result.get('events', [])):
        issues.append('fixture panic invalidates client evidence')
    if 'fixture panic:' in base64.b64decode(result['stderr_contract_b64']).decode(errors='replace'):
        issues.append('fixture panic diagnostic invalidates client evidence')
    for stream in result.get('summary', {}).get('streams', {}).values():
        terminal = stream.get('last_terminal') or {}
        if str(terminal.get('message', '')).startswith('fixture panic:'):
            issues.append('fixture panic summary invalidates client evidence')
    http = [r for r in requests if r['protocol'] == 'http']
    flight = [r for r in requests if r['protocol'] == 'flight']
    if [(r['method'], r['path']) for r in http] != [tuple(r) for r in want['http']]:
        issues.append('HTTP requests differ from declared discovery/refresh sequence')
    if want.get('http_auth') is not None and [r['headers'] for r in http] != want['http_auth']:
        issues.append('HTTP discovery auth differs')
    if len(flight) != want['dogets']:
        issues.append(f'DoGet count {len(flight)}, expected {want["dogets"]}')
    if any(r['body'] != ticket(case['dataset']) for r in flight):
        issues.append('ticket bytes differ from Go JSON encoding')
    if any(r['headers'] != want['auth'] for r in flight):
        issues.append('Flight metadata differs')
    if any(r['unexpected'] for r in requests):
        issues.append('fixture observed unexpected request')
    if flight and (not requests or requests[0]['protocol'] != 'http'):
        issues.append('Flight preceded discovery')
    if len(result.get('proxy_requests', [])) != want.get('proxy_connects', 0):
        issues.append('proxy CONNECT count differs')
    dogets = [e for e in result['events'] if is_doget(e)]
    if want.get('transparent_retry'):
        if len(dogets) != 2:
            issues.append('transparent refusal must make exactly two DoGets')
        elif not 0 <= (dogets[1]['t_ns'] - dogets[0]['t_ns']) / 1e6 < 450:
            issues.append('transparent refusal acquired a tail reconnect delay')
    for event in dogets:
        if event.get('metadata', {}).get('user-agent') != ['grpc-go/1.65.0']:
            issues.append('Flight user-agent differs from pinned grpc-go')
        if case['fixture']['phase6'].get('tls', {}).get('grpc'):
            tls = event.get('tls')
            if not tls or tls.get('version') not in ('TLS 1.2', 'TLS 1.3'):
                issues.append('Flight TLS missing or below TLS1.2')
    if want.get('retry') and len(dogets) >= 2:
        terminals = [e for e in result['events'] if e.get('kind') == 'terminal' and e.get('doget') == dogets[0].get('doget')]
        if not terminals:
            issues.append('retry terminal event missing')
        else:
            start = dogets[0]['t_ns'] if want.get('retry_clock') == 'doget' else terminals[-1]['t_ns']
            delay = (dogets[1]['t_ns'] - start) / 1e6
            if not 450 <= delay <= 2500:
                issues.append(f'retry delay {delay:.1f}ms outside 500ms scheduler tolerance')
    summary = result['summary'].get('streams', {}).get(want.get('summary_key', case['dataset']), {})
    if want.get('output_summary'):
        if result['stdout_sha256'] != summary.get('expected_stdout_sha256'):
            issues.append('stdout differs from independent Go Arrow batch rendering')
        if result['stdout_bytes'] != summary.get('expected_stdout_bytes'):
            issues.append('stdout byte count differs from batch framing')
    for key in ('stdout_sha256', 'stdout_rows', 'stdout_batches'):
        if key in want and result.get(key) != want[key]:
            issues.append(key + ' differs from independent retained-output contract')
    stderr = base64.b64decode(result['stderr_contract_b64'])
    if want.get('error_code'):
        try:
            error = json.loads(stderr)['error']
            if error['code'] != want['error_code'] or error.get('retryable'):
                issues.append('fatal Flight error classification differs')
        except (ValueError, KeyError):
            issues.append('fatal Flight error missing JSON envelope')
    if want.get('config_session'):
        profile = result['config_after']['config/pb/config.toml']['toml']['Profiles']['fixture']
        if profile.get('session_token') != want['config_session']:
            issues.append('discovery refresh did not persist credential snapshot')
        if len(http) == 3:
            if http[0]['headers'] != p2.HEADERS['cloud-cookie'] or http[2]['headers'] != want['auth']:
                issues.append('OAuth discovery did not replay with refreshed cookie')
            try:
                body = json.loads(http[1]['body'])
                if body != {'grant_type': 'refresh_token', 'refresh_token': 'fixture-refresh'}:
                    issues.append('OAuth refresh body differs')
            except ValueError:
                issues.append('OAuth refresh body is not JSON')
    elif result['config_after'] != result['config_before']:
        issues.append('unexpected configuration change')
    return issues


def signature(result):
    return {k: result.get(k, []) if k in ('proxy_requests', 'flight_user_agents', 'flight_authorities') else result[k]
            for k in ('raw_wait_status', 'termination', 'stdout_sha256', 'stdout_bytes',
                      'stderr_contract_b64', 'requests', 'proxy_requests', 'flight_user_agents',
                      'flight_authorities', 'config_after')}


def differences(reference, candidate):
    return [key + ' differs' for key, value in signature(reference).items()
            if signature(candidate)[key] != value]


def split_wire_ua(reference, candidate, second_issues, delta):
    """Quarantine only the exact parent-declared tonic wire header difference."""
    go = reference.get('flight_user_agents', [])
    rust = candidate.get('flight_user_agents', [])
    if len(go) != len(rust) or not go or any(a != ['grpc-go/1.65.0'] for a in go) or any(
            a != ['tonic/0.14.6'] for a in rust):
        return second_issues, delta, []
    wire = [i for i in second_issues if i == 'Flight user-agent differs from pinned grpc-go']
    wire += [i for i in delta if i == 'flight_user_agents differs']
    return ([i for i in second_issues if i != 'Flight user-agent differs from pinned grpc-go'],
            [i for i in delta if i != 'flight_user_agents differs'], wire)


def binary_for(case, args, label):
    path = getattr(args, label + ('_cloud' if case['build'] == 'cloud' else ''))
    if not path:
        raise ValueError(f'{case["name"]} requires --{label.replace("_", "-")}-cloud')
    return Path(path).resolve()


def validate_frozen(manifest, cases, fixture):
    if manifest.get('schema_version') != SCHEMA_VERSION or manifest.get('oracle_revision') != '07af390':
        raise ValueError('unsupported or unpinned Phase6 reference')
    if not manifest.get('complete') or manifest.get('partial') or manifest.get('failed'):
        raise ValueError('reference is incomplete or has failed cases')
    if manifest.get('mode') != 'freeze' or manifest.get('parity_passed') != len(cases):
        raise ValueError('reference is not a complete strict Go A/A freeze')
    if manifest['corpus_sha256'] != run.digest(run.canonical(cases)):
        raise ValueError('reference corpus changed; refreeze explicitly')
    if manifest['fixture_sha256'] != run.digest(Path(fixture).read_bytes()):
        raise ValueError('reference fixture changed; refreeze explicitly')
    if set(manifest['cases']) != {c['name'] for c in cases}:
        raise ValueError('reference case inventory differs')
    for case in cases:
        entry = manifest['cases'][case['name']]
        if entry.get('issues') or entry.get('wire_issues') or entry.get('diagnostic_issues'):
            raise ValueError('reference case has unresolved or divergent outcomes: ' + case['name'])
        if entry.get('go_sha256') != ORACLE_HASHES[case['build']] or entry.get('second_sha256') != ORACLE_HASHES[case['build']]:
            raise ValueError('reference case was not repeated with the pinned Go binary: ' + case['name'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list')
    listing.add_argument('--suite', choices=('main', 'trailer-followup', 'review3-followup'), default='main')
    for name in ('freeze', 'compare', 'check-reference'):
        p = commands.add_parser(name)
        p.add_argument('--go', required=True)
        p.add_argument('--go-cloud')
        p.add_argument('--fixture', required=True)
        p.add_argument('--out', required=True)
        p.add_argument('--case', action='append')
        p.add_argument('--reference', default=str(REFERENCE))
        p.add_argument('--suite', choices=('main', 'trailer-followup', 'review3-followup'), default='main')
        if name == 'compare':
            p.add_argument('--candidate', required=True)
            p.add_argument('--candidate-cloud')
    args = parser.parse_args(argv)
    cases = {'main': corpus, 'trailer-followup': trailer_followups,
             'review3-followup': review3_followups}[args.suite]()
    if args.command == 'list':
        print('\n'.join(c['name'] for c in cases))
        return 0
    try:
        if args.suite != 'main' and args.command in ('freeze', 'compare') and Path(args.reference).resolve() == REFERENCE.resolve():
            raise ValueError('a followup suite needs an explicit cache --reference; it cannot replace the main oracle')
        if args.case:
            unknown = set(args.case) - {c['name'] for c in cases}
            if unknown:
                raise ValueError(f'unknown cases {sorted(unknown)}')
            selected = [c for c in cases if c['name'] in args.case]
        else:
            selected = cases
        out = Path(args.out).resolve()
        if TMP not in out.parents:
            raise ValueError(f'--out must be under {TMP}; generated artifacts never use /tmp')
        out.mkdir(parents=True, exist_ok=False)
        frozen = None
        if args.command == 'compare':
            frozen = json.loads(Path(args.reference).read_text())
            validate_frozen(frozen, cases, args.fixture)
        # Reject an unpinned oracle before it can contribute any observations.
        for build in {c['build'] for c in selected}:
            representative = next(c for c in selected if c['build'] == build)
            path = binary_for(representative, args, 'go')
            if run.digest(path.read_bytes()) != ORACLE_HASHES[build]:
                raise ValueError('Go executable differs from pinned baseline hash: ' + str(path))
        report = dict(schema_version=SCHEMA_VERSION, oracle_revision='07af390', complete=False,
                      mode=args.command, partial=len(selected) != len(cases), failed=0, parity_passed=0,
                      payload_auth_passed=0, wire_ua_divergent=0, diagnostic_divergent=0, cases={},
                      fixture_sha256=run.digest(Path(args.fixture).read_bytes()),
                      corpus_sha256=run.digest(run.canonical(cases)), diagnostics=[])
        candidate_hashes = {}
        if args.command == 'compare':
            for build in {c['build'] for c in selected}:
                representative = next(c for c in selected if c['build'] == build)
                path = binary_for(representative, args, 'candidate')
                candidate_hashes[build] = run.digest(path.read_bytes())
            report['candidate_sha256'] = candidate_hashes
        for case in selected:
            name = case['name']
            first = sample(binary_for(case, args, 'go'), args.fixture, case, out / name / 'go')
            issues = expectation_issues(case, first)
            if first['binary_sha256'] != ORACLE_HASHES[case['build']]:
                issues.append('Go executable differs from pinned baseline hash')
            if frozen and signature(first) != frozen['cases'][name]['signature']:
                issues.append('Go drifted from frozen reference')
            label = 'candidate' if args.command == 'compare' else 'go'
            second = sample(binary_for(case, args, label), args.fixture, case, out / name / label / 'repeat')
            if label == 'go' and second['binary_sha256'] != ORACLE_HASHES[case['build']]:
                issues.append('second Go executable differs from pinned baseline hash')
            if label == 'candidate' and second['binary_sha256'] != candidate_hashes[case['build']]:
                issues.append('candidate executable changed during run')
            if first['fixture_sha256'] != second['fixture_sha256'] or first['fixture_sha256'] != report['fixture_sha256']:
                issues.append('fixture executable changed during run')
            second_issues = expectation_issues(case, second)
            delta = differences(first, second)
            wire_issues = []
            # No diagnostic difference is waived: the former garbage-header
            # waiver closed when Rust adopted Go's HeaderType panic text
            # (docs/pr7-remaining-differences.md). The field stays for report
            # compatibility.
            diagnostic_issues = []
            if args.command == 'compare':
                # Parent's explicit wire ledger: native tonic UA is a known
                # isolated difference. Never count these as strict parity.
                # CONNECT headers, every other metadata field and raw bytes
                # remain under their original gates.
                second_issues, delta, wire_issues = split_wire_ua(first, second, second_issues, delta)
            issues += ['second: ' + i for i in second_issues]
            issues += delta
            report['cases'][name] = dict(signature=signature(first), issues=issues,
                                         wire_issues=wire_issues,
                                         diagnostic_issues=diagnostic_issues,
                                         go_sha256=first['binary_sha256'], second_sha256=second['binary_sha256'])
            report['failed'] += bool(issues)
            report['parity_passed'] += not issues and not wire_issues and not diagnostic_issues
            report['payload_auth_passed'] += not issues
            report['wire_ua_divergent'] += bool(wire_issues)
            report['diagnostic_divergent'] += bool(diagnostic_issues)
            run.write_json(out / 'report.json', report)
            print(name + ': ' + ('; '.join(issues) if issues else
                  'DECLARED DIVERGENCE (remaining contract PASS)' if wire_issues or diagnostic_issues else 'PASS'), flush=True)
        report['complete'] = True  # All selected cases ran; partial remains explicit.
        run.write_json(out / 'report.json', report)
        if args.command == 'freeze' and not report['failed']:
            # A selected run is useful characterization, never a complete freeze.
            if report['partial']:
                raise ValueError('partial run cannot replace the frozen reference')
            run.write_json(Path(args.reference), report)
        return int(bool(report['failed']))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print('Phase6 harness error: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
