#!/usr/bin/env python3
"""Owned PTY terminal observer and independent process oracle for phase seven.

No candidate builds or implicit benchmark runs. See contracts/phase7.md.
"""
import argparse
import codecs
import copy
from contextlib import ExitStack
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import selectors
import shutil
import socket
import signal
import statistics
import struct
import subprocess
import sys
import tempfile
import termios
import time
from types import SimpleNamespace
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import phase1
import phase2
import phase3
import run

# Select one owned scratch root before any test/capture creates a temporary
# directory. Explicit overrides keep independent runs away from frozen caches.
TMP = Path(os.environ.get('PB_PHASE7_TMP') or Path.home() / '.cache/pb-phase7/tmp').expanduser().resolve()
TMP.mkdir(parents=True, exist_ok=True)
os.environ.update({name: str(TMP) for name in ('TMPDIR', 'GOTMPDIR', 'CLAUDE_CODE_TMPDIR')})
SCHEMA = 1
CELL_FIELDS = ['data', 'fg', 'bg', 'bold', 'italics', 'underscore', 'strikethrough',
               'reverse', 'blink', 'faint', 'conceal', 'overline']
# Persistent modes not represented by cells/cursor/alternate-screen state.
TERMINAL_SESSION_MODES = frozenset((1, 1000, 1002, 1003, 1004, 1006, 2004, 2026))
CONTRACT_HEADERS = (*phase3.CONTRACT_HEADERS, 'x-p-telemetry-type', 'x-p-log-source')
KEYS = {'enter': '\r', 'tab': '\t', 'shift-tab': '\x1b[Z', 'esc': '\x1b',
        'ctrl-c': '\x03', 'ctrl-r': '\x12', 'ctrl-d': '\x04', 'ctrl-l': '\x0c',
        'ctrl-b': '\x02', 'ctrl-q': '\x11', 'ctrl-a': '\x01', 'ctrl-e': '\x05',
        'ctrl-u': '\x15', 'ctrl-k': '\x0b', 'ctrl-y': '\x19', 'ctrl-v': '\x16', 'ctrl-space': '\x00',
        'alt-enter': '\x1b\r', 'up': '\x1b[A', 'down': '\x1b[B',
        'right': '\x1b[C', 'left': '\x1b[D', 'home': '\x1b[H', 'end': '\x1b[F',
        'page-up': '\x1b[5~', 'page-down': '\x1b[6~', 'backspace': '\x7f'}


class FixtureServer(phase3.FixtureServer):
    """Reuse the existing HTTP machinery with explicit query/body route selectors."""
    @staticmethod
    def _read_request(reader):
        request = phase3.FixtureServer._read_request(reader)
        if request:
            # Existing fixture records its established header set; retain all
            # headers for p7 mutation assertions through this owned seam.
            request['phase7_headers'] = {k: ','.join(v) for k, v in request['headers'].items()}
        return request

    def _handle(self, conn, request, listener, number, received):
        # The base handler records traces under its lock. Enrich this request's
        # trace by received timestamp; never confuse concurrent request indexes.
        result = super()._handle(conn, request, listener, number, received)
        with self.lock:
            for entry, milestone in zip(self.trace, self.milestones):
                if milestone['received_monotonic_ns'] == received:
                    entry['headers'].update({k: v for k, v in request['phase7_headers'].items()
                                             if k in CONTRACT_HEADERS})
                    break
        return result

    def _select(self, request):
        parsed = urllib.parse.urlsplit(request['target'])
        path, query = urllib.parse.unquote(parsed.path), urllib.parse.parse_qs(parsed.query)
        headers = {k: v[-1] for k, v in request['headers'].items()}
        try:
            body_fields = json.loads(request['body'])
        except ValueError:
            body_fields = None
        with self.lock:
            for index, route in enumerate(self.routes):
                if route['method'] != request['method'] or route['path'] != path:
                    continue
                if not all(headers.get(k.lower()) == v for k, v in route.get('headers', {}).items()):
                    continue
                if not all(query.get(k) == v for k, v in route.get('query', {}).items()):
                    continue
                if route.get('body_contains', '').encode() not in request['body']:
                    continue
                if route.get('body_fields') and (not isinstance(body_fields, dict) or
                        not all(body_fields.get(k) == v for k, v in route['body_fields'].items())):
                    continue
                if route.get('times') and self.remaining[index] <= 0:
                    continue
                if route.get('times'):
                    self.remaining[index] -= 1
                return index
        return None


class Fixture(phase3.Fixture):
    def __init__(self, scenario, root):
        self.proc = None
        path = root / 'scenario.json'
        run.write_json(path, scenario)
        self.log = (root / 'fixture.stderr').open('wb')
        self.proc = subprocess.Popen([sys.executable, '-I', str(Path(__file__).resolve()),
                                      'serve', '--scenario', str(path)], stdout=subprocess.PIPE,
                                     stderr=self.log, start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise RuntimeError('phase7 fixture startup timeout')
            self.urls = json.loads(self.proc.stdout.readline())
            self.url = self.urls['url']
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        except BaseException:
            self.close()
            raise


def dependencies():
    try:
        import pyte
        import wcwidth
    except ImportError as error:
        raise RuntimeError('PTY observer requires pyte==0.8.2 and wcwidth==0.2.13; '
                           'use /home/ajs/.cache/pb-phase7/harness-venv/bin/python') from error
    from importlib.metadata import version
    if version('pyte') != '0.8.2' or version('wcwidth') != '0.2.13':
        raise RuntimeError('terminal observer dependency versions differ from pinned contract')
    return pyte, wcwidth


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


class Terminal:
    """VT observer: pyte styled cells plus explicit DEC alternate-screen ownership.

    Escape tokens are buffered across UTF-8/read boundaries. Unsupported controls
    are evidence failures, never silently converted to a passing text snapshot.
    """

    def __init__(self, columns=100, rows=30, theme='dark', reply=None):
        pyte, _ = dependencies()
        self.columns, self.rows, self.theme = columns, rows, theme
        from collections import namedtuple
        StyledChar = namedtuple('StyledChar', pyte.screens.Char._fields + ('faint', 'conceal', 'overline'),
                                defaults=(False, False, False))
        class Attributes:
            def reset(screen):
                super().reset()
                screen.cursor.attrs = StyledChar(*screen.cursor.attrs)

            @property
            def default_char(screen):
                return StyledChar(*super().default_char)

            def select_graphic_rendition(screen, *attrs):
                if not isinstance(screen.cursor.attrs, StyledChar):
                    screen.cursor.attrs = StyledChar(*screen.cursor.attrs)
                super().select_graphic_rendition(*attrs)
                extra = {}
                remaining = iter(attrs or (0,))
                for attr in remaining:
                    if attr in (38, 48):
                        mode = next(remaining, None)
                        for _ in range(3 if mode == 2 else 1 if mode == 5 else 0):
                            next(remaining, None)
                    elif attr == 0:
                        extra.update(faint=False, conceal=False, overline=False)
                    elif attr in (2, 22):
                        extra['faint'] = attr == 2
                    elif attr in (8, 28):
                        extra['conceal'] = attr == 8
                    elif attr in (53, 55):
                        extra['overline'] = attr == 53
                    elif attr not in (1, 3, 4, 5, 7, 9, 23, 24, 25, 27, 29, 39, 49) and not \
                            (30 <= attr <= 37 or 40 <= attr <= 47 or 90 <= attr <= 97 or 100 <= attr <= 107):
                        self.unsupported.append('unsupported SGR ' + str(attr))
                screen.cursor.attrs = screen.cursor.attrs._replace(**extra)
        class StyledScreen(Attributes, pyte.Screen):
            pass
        class StyledHistory(Attributes, pyte.HistoryScreen):
            def __init__(screen, *args, **kwargs):
                screen.history_overflow = False
                super().__init__(*args, **kwargs)

            def index(screen):
                # pyte appends the outgoing row only at the scrolling margin.
                # A full bounded deque loses a row at that append, not when an
                # unrelated token arrives or when capacity is first reached.
                _, bottom = screen.margins or (0, screen.lines - 1)
                if screen.cursor.y == bottom and len(screen.history.top) == screen.history.top.maxlen:
                    screen.history_overflow = True
                super().index()

            def reverse_index(screen):
                top, _ = screen.margins or (0, screen.lines - 1)
                if screen.cursor.y == top and len(screen.history.bottom) == screen.history.bottom.maxlen:
                    screen.history_overflow = True
                super().reverse_index()
        self.main = StyledHistory(columns, rows, history=20000)
        self.alt = StyledScreen(columns, rows)
        self.active = self.main
        self.alternate = False
        self.decoder = codecs.getincrementaldecoder('utf-8')('surrogateescape')
        self.pending = ''
        self.reply = reply or (lambda data: None)
        self.unsupported, self.transitions, self.queries = [], [], []
        self.private_modes = set()
        self.last_alt_exit_offset = None
        self.byte_offset = 0
        self.main_stream = pyte.Stream(self.main)
        self.alt_stream = pyte.Stream(self.alt)
        self.cursor_shape = 0
        self.history_overflow = False

    def _stream(self, token):
        stream = self.alt_stream if self.alternate else self.main_stream
        stream.feed(token)
        self.history_overflow = self.history_overflow or self.main.history_overflow

    def session_modes(self):
        return sorted(self.private_modes & TERMINAL_SESSION_MODES)

    @staticmethod
    def _cell(cell):
        import pyte.graphics
        colors = tuple(pyte.graphics.FG_ANSI[i] for i in range(30, 38)) + \
                 tuple(pyte.graphics.FG_AIXTERM[i] for i in range(90, 98))
        values = list(cell)
        for index in (1, 2):
            if values[index] in colors:
                values[index] = pyte.graphics.FG_BG_256[colors.index(values[index])]
        return values

    def frame(self):
        screen = self.active
        return {'columns': self.columns, 'rows': self.rows,
                'cells': [[self._cell(screen.buffer[y][x]) for x in range(self.columns)]
                          for y in range(self.rows)],
                'cursor': {'x': screen.cursor.x, 'y': screen.cursor.y,
                           'visible': not screen.cursor.hidden, 'shape': self.cursor_shape},
                'alternate': self.alternate,
                'scrollback': [[self._cell(row[x]) for x in range(self.columns)]
                               for row in self.main.history.top]}

    def text(self):
        return '\n'.join(self.active.display)

    def resize(self, columns, rows):
        self.columns, self.rows = columns, rows
        self.main.resize(lines=rows, columns=columns)
        self.alt.resize(lines=rows, columns=columns)

    def _switch(self, enabled, mode):
        if enabled == self.alternate:
            return
        if enabled:
            if mode == 1049:
                self.main.save_cursor()
            if mode in (1047, 1049):
                self.alt.reset()
            self.active = self.alt
        else:
            self.transitions.append({'kind': 'before-alt-exit', 'frame': self.frame(),
                                     'monotonic_ns': time.monotonic_ns()})
            self.active = self.main
            if mode == 1049:
                self.main.restore_cursor()
            self.last_alt_exit_offset = self.byte_offset
        self.alternate = enabled
        self.transitions.append({'kind': 'alt-enter' if enabled else 'alt-exit',
                                 'mode': mode, 'monotonic_ns': time.monotonic_ns()})

    def _control(self, token):
        import re
        if token == '\x1bc':
            # RIS resets these session modes as well as pyte's screen state.
            # Keep the observer's evidence of any earlier history loss sticky.
            self.private_modes.difference_update(TERMINAL_SESSION_MODES)
        if token.startswith('\x1b]'):
            payload = token[2:].removesuffix('\x07').removesuffix('\x1b\\')
            if payload in ('10;?', '11;?'):
                color = 'ffff/ffff/ffff' if self.theme == 'light' else '0000/0000/0000'
                self.queries.append(payload)
                self.reply(f'\x1b]{payload[:2]};rgb:{color}\x1b\\'.encode())
            elif payload.split(';')[0] not in ('0', '1', '2', '8', '10', '11'):
                self.unsupported.append(repr(token))
            else:
                self._stream(token)
            return
        if token.startswith('\x1b['):
            body, final = token[2:-1], token[-1]
            if final in ('h', 'l') and body.startswith('?'):
                modes = body[1:].split(';')
                if not all(m.isdigit() for m in modes):
                    self.unsupported.append(repr(token))
                    return
                for mode in map(int, modes):
                    if final == 'h':
                        self.private_modes.add(mode)
                    else:
                        self.private_modes.discard(mode)
                    if mode in (47, 1047, 1049):
                        self._switch(final == 'h', mode)
                    elif mode == 1048:
                        (self.active.save_cursor if final == 'h' else self.active.restore_cursor)()
                    elif mode in (1, 7, 12, 25, 1000, 1002, 1003, 1004, 1006, 2004, 2026):
                        self._stream(f'\x1b[?{mode}{final}')
                    else:
                        self.unsupported.append(repr(token))
                return
            if final == 'n' and body == '6':
                self.queries.append('cursor-position')
                self.reply(f'\x1b[{self.active.cursor.y + 1};{self.active.cursor.x + 1}R'.encode())
                return
            if final == 'c' and body in ('', '0', '>0', '>'):
                self.queries.append('device-attributes')
                self.reply(b'\x1b[?1;2c' if not body.startswith('>') else b'\x1b[>0;0;0c')
                return
            if final == 'q' and body.endswith(' ') and body[:-1].isdigit():
                self.cursor_shape = int(body[:-1])
                return
            if final in ('s', 'u') and not body:
                (self.active.save_cursor if final == 's' else self.active.restore_cursor)()
                return
            if final not in self.main_stream.csi or any(c in body for c in ('>', ':', '$')):
                self.unsupported.append(repr(token))
                return
        elif token[1:] not in self.main_stream.escape and not token.startswith(('\x1b(', '\x1b)')):
            if token != '\x1b=' and token != '\x1b>':
                self.unsupported.append(repr(token))
                return
        self._stream(token)

    def feed(self, data, final=False):
        self.pending += self.decoder.decode(data, final=final)
        while self.pending:
            if self.pending[0] != '\x1b':
                end = self.pending.find('\x1b')
                if end < 0:
                    end = len(self.pending)
                text = self.pending[:end]
                self.pending = self.pending[end:]
                self.byte_offset += len(text.encode(errors='surrogateescape'))
                self._stream(''.join('\ufffd' if 0xdc80 <= ord(c) <= 0xdcff else c for c in text))
                continue
            if len(self.pending) < 2:
                break
            if self.pending[1] == '[':
                end = next((i + 1 for i, c in enumerate(self.pending[2:], 2)
                            if '@' <= c <= '~'), None)
            elif self.pending[1] in ']P^_':
                bell, st = self.pending.find('\x07', 2), self.pending.find('\x1b\\', 2)
                ends = [i + n for i, n in ((bell, 1), (st, 2)) if i >= 0]
                end = min(ends) if ends else None
            elif self.pending[1] in '()#':
                end = 3 if len(self.pending) >= 3 else None
            else:
                end = 2
            if end is None:
                if len(self.pending) > 65536:
                    self.unsupported.append('unterminated control >64KiB')
                    self.pending = ''
                break
            token, self.pending = self.pending[:end], self.pending[end:]
            self.byte_offset += len(token.encode(errors='surrogateescape'))
            self._control(token)
        if final and self.pending:
            self.unsupported.append('incomplete terminal sequence: ' + repr(self.pending))


def winsize(fd, columns, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))


def termios_state(fd):
    attrs = termios.tcgetattr(fd)
    return {'iflag': attrs[0], 'oflag': attrs[1], 'cflag': attrs[2], 'lflag': attrs[3],
            'ispeed': attrs[4], 'ospeed': attrs[5],
            'cc': [x if isinstance(x, int) else x[0] for x in attrs[6]],
            'echo': bool(attrs[3] & termios.ECHO), 'canonical': bool(attrs[3] & termios.ICANON),
            'signals': bool(attrs[3] & termios.ISIG)}


def proc_sample(pid):
    """Linux child counters, distinct from collector and fixture CPU/memory."""
    folder = Path('/proc') / str(pid)
    try:
        stat = (folder / 'stat').read_text().rsplit(')', 1)[1].split()
        if stat[0] in ('Z', 'X', 'x'):
            return None
        status = dict(line.split(':', 1) for line in (folder / 'status').read_text().splitlines()
                      if ':' in line)
        if 'VmRSS' not in status or 'VmHWM' not in status:
            return None
        io = {k: int(v) for k, v in (line.split(':', 1) for line in (folder / 'io').read_text().splitlines())}
        fd_count = len(list((folder / 'fd').iterdir()))
        final_stat = (folder / 'stat').read_text().rsplit(')', 1)[1].split()
        if final_stat[0] in ('Z', 'X', 'x') or final_stat[19] != stat[19]:
            return None
        return {'monotonic_ns': time.monotonic_ns(),
                'state': stat[0], 'rss_bytes': int(status['VmRSS'].split()[0]) * 1024,
                'high_water_rss_bytes': int(status['VmHWM'].split()[0]) * 1024,
                'minor_faults': int(stat[7]), 'major_faults': int(stat[9]),
                'voluntary_context_switches': int(status['voluntary_ctxt_switches']),
                'involuntary_context_switches': int(status['nonvoluntary_ctxt_switches']),
                'user_ticks': int(stat[11]), 'system_ticks': int(stat[12]),
                'fd_count': fd_count, 'io': io}
    except (OSError, ValueError, KeyError, IndexError):
        return None


_NATIVE_CAPABILITIES = {}
_COMPILER_VERSIONS = {}


def spawn_helper(build=True, *, probe=True):
    """Resolve one keyed helper; build=False never compiles or repairs provenance."""
    if not isinstance(build, bool) or not isinstance(probe, bool):
        raise ValueError('spawn_helper build/probe must be boolean')
    if sys.platform != 'linux':
        raise RuntimeError('phase7 native observer requires Linux')
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise RuntimeError('unsupported native observer SIGCHLD disposition: default child ownership required')
    source = HERE / 'phase7_spawn.c'
    flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror']
    compiler = shutil.which('cc')
    if not compiler:
        raise RuntimeError('phase7 native spawn helper requires a C compiler (cc)')
    compiler = str(Path(compiler).resolve())
    compiler_sha256 = hashlib.sha256(Path(compiler).read_bytes()).hexdigest()
    compiler_key = (compiler, compiler_sha256)
    if probe:
        version = subprocess.check_output([compiler, '--version'], text=True, timeout=5).strip()
        _COMPILER_VERSIONS[compiler_key] = version
    else:
        if compiler_key not in _COMPILER_VERSIONS:
            raise RuntimeError('prepared native helper compiler snapshot missing/drifted; resolve before capture')
        version = _COMPILER_VERSIONS[compiler_key]
    identity = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'compiler': compiler, 'compiler_sha256': compiler_sha256,
                'compiler_version': version, 'flags': flags}
    key = digest(identity)
    binary = TMP / ('phase7-spawn-' + key)
    provenance = binary.with_suffix('.json')
    for cached_path in (binary, provenance):
        if cached_path.is_symlink():
            raise RuntimeError('prepared native helper cache symlink rejected: ' + str(cached_path))

    def prepared():
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise RuntimeError('prepared native helper executable cache missing: ' + str(binary))
        helper = {**identity, 'build_key': key, 'path': str(binary),
                  'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'protocol_version': 2}
        try:
            encoded = provenance.read_bytes()
            manifest = json.loads(encoded)
        except (OSError, ValueError) as error:
            raise RuntimeError('prepared native helper provenance missing/invalid: ' + str(provenance)) from error
        if manifest != {'schema_version': 1, **helper}:
            raise RuntimeError('prepared native helper provenance mismatch: ' + str(provenance))
        return {**helper, 'provenance_path': str(provenance),
                'provenance_sha256': hashlib.sha256(encoded).hexdigest()}

    try:
        helper = prepared()
    except RuntimeError:
        if not build:
            raise
        TMP.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='phase7-spawn-build-', dir=TMP) as folder:
            staged = Path(folder) / 'spawn'
            subprocess.run([compiler, *flags, str(source), '-o', str(staged)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            manifest = {'schema_version': 1, **identity, 'build_key': key, 'path': str(binary),
                        'sha256': hashlib.sha256(staged.read_bytes()).hexdigest(), 'protocol_version': 2}
            staged_provenance = Path(folder) / 'manifest.json'
            staged_provenance.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
            os.replace(staged, binary)
            os.replace(staged_provenance, provenance)
        helper = prepared()
    if helper['sha256'] not in _NATIVE_CAPABILITIES:
        if not probe:
            raise RuntimeError('prepared native helper capability proof missing; resolve before capture')
        _NATIVE_CAPABILITIES[helper['sha256']] = native_capability_probe(helper)
    return {**helper, 'capability': _NATIVE_CAPABILITIES[helper['sha256']]}


class NativeProtocolError(RuntimeError):
    pass


class NativeChild:
    """CLI PID is reserved through WNOWAIT until release; signalling uses pidfd."""
    STARTUP_TIMEOUT = 5
    CLEANUP_TIMEOUT = 2
    GROUP_SIGNAL_FLAG = 1 << 2  # Linux PIDFD_SIGNAL_PROCESS_GROUP (6.9+).
    FIXTURE_DEFAULT_SIGNALS = tuple(getattr(signal, name) for name in
        ('SIGINT', 'SIGTERM', 'SIGHUP', 'SIGQUIT', 'SIGPIPE', 'SIGXFZ', 'SIGXFSZ')
        if hasattr(signal, name))

    def __init__(self, helper, command, env, cwd, pipe_mode, stdin_fd, stdout_fd, stderr_fd, close_fds):
        self.socket = None
        self.helper_pid = self.helper_status = self.pidfd = self.pid = None
        self.initial = self.exited = self.exit = None
        self.buffer, self.eof, self.released, self.closed = b'', False, False, False
        self.cleanup_issues = []
        parent, child = socket.socketpair()
        self.socket = parent
        try:
            actions = [(os.POSIX_SPAWN_DUP2, stdin_fd, 0), (os.POSIX_SPAWN_DUP2, stdout_fd, 1),
                       (os.POSIX_SPAWN_DUP2, stderr_fd, 2), (os.POSIX_SPAWN_DUP2, child.fileno(), 3)]
            actions.extend((os.POSIX_SPAWN_CLOSE, fd) for fd in
                           set((*close_fds, parent.fileno(), child.fileno())) if fd > 3)
            self.helper_pid = os.posix_spawn(helper['path'], [helper['path'], 'pipe' if pipe_mode else 'pty',
                str(os.getpid()), str(cwd), *command], env, file_actions=actions, setsigmask=(),
                setsigdef=self.FIXTURE_DEFAULT_SIGNALS)
            child.close()
            parent.setblocking(False)
            self._await(lambda: self.initial is not None, self.STARTUP_TIMEOUT)
            self.pid = self.initial['pid']
            self.start_ticks = (Path('/proc') / str(self.pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19]
            self.pidfd = os.pidfd_open(self.pid, 0)
            if self.exec_error is None:
                try:
                    signal.pidfd_send_signal(self.pidfd, 0, None, self.GROUP_SIGNAL_FLAG)
                except OSError as error:
                    raise NativeProtocolError('Linux pidfd process-group signalling unavailable') from error
        except BaseException:
            child.close()
            self.close(abort=True)
            raise

    @property
    def exec_error(self):
        return self.initial if self.initial and self.initial['event'] == 'exec_error' else None

    def same_process(self):
        if self.pid is None:
            return False
        try:
            fields = (Path('/proc') / str(self.pid) / 'stat').read_text().rsplit(')', 1)[1].split()
            return fields[19] == self.start_ticks
        except (OSError, IndexError):
            return False

    def signal_group(self, number):
        if self.pidfd is None:
            return False
        try:
            signal.pidfd_send_signal(self.pidfd, number, None, self.GROUP_SIGNAL_FLAG)
            return True
        except ProcessLookupError:
            return False

    def poll(self):
        if self.eof:
            if self.exit is None:
                raise NativeProtocolError('native supervisor closed without CLI wait4 result')
            return self.exited
        while True:
            try:
                data = self.socket.recv(4096)
            except BlockingIOError:
                break
            except OSError as error:
                raise NativeProtocolError('native supervisor protocol read failed') from error
            if not data:
                self.eof = True
                break
            self.buffer += data
            if len(self.buffer) > 4096:
                raise NativeProtocolError('native protocol exceeded bound')
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError) as error:
                    raise NativeProtocolError('invalid native protocol JSON') from error
                if not isinstance(event, dict):
                    raise NativeProtocolError('invalid native protocol record')
                kind = event.get('event')
                if kind in ('spawn', 'exec_error') and self.initial is None and \
                        event.get('protocol_version') == 2 and isinstance(event.get('pid'), int) and event['pid'] > 0:
                    self.initial = event
                elif self.initial and event.get('pid') == self.initial['pid']:
                    if kind == 'exited' and self.exited is None and self.exit is None:
                        self.exited = event
                    elif kind == 'exit' and self.exited and self.released and self.exit is None:
                        self.exit = event
                    else:
                        raise NativeProtocolError('invalid native protocol sequence')
                else:
                    raise NativeProtocolError('invalid native protocol sequence')
        if self.eof and (self.buffer or self.exit is None):
            raise NativeProtocolError('incomplete native wait4 result')
        return self.exited

    def _await(self, condition, timeout):
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.socket, selectors.EVENT_READ)
            while not condition():
                self.poll()
                if condition():
                    break
                if time.monotonic() >= deadline:
                    raise NativeProtocolError('native supervisor handshake/wait timed out')
                selector.select(min(.01, max(0, deadline - time.monotonic())))

    def wait_exited(self):
        self._await(lambda: self.exited is not None, self.CLEANUP_TIMEOUT)
        return self.exited

    def release(self):
        if not self.released:
            self.released = True
            # EOF is an idempotent release and works on observer death too.
            try:
                self.socket.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def wait(self):
        self._await(lambda: self.exit is not None, self.CLEANUP_TIMEOUT)
        return self.exit

    def close(self, abort=False):
        """Bounded, nonthrowing unwind; caller preserves the original exception."""
        if self.closed:
            return self.cleanup_issues
        self.closed = True
        if abort and self.pidfd is not None and self.exit is None:
            try:
                self.signal_group(signal.SIGKILL)
                signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
            except OSError as error:
                if error.errno != errno.ESRCH:
                    self.cleanup_issues.append('native child cleanup signal failed: ' + str(error))
        if self.socket is not None:
            self.release()
            try:
                self.socket.close()
            except OSError as error:
                self.cleanup_issues.append('native protocol close failed: ' + str(error))
        if self.helper_pid is not None:
            deadline = time.monotonic() + self.CLEANUP_TIMEOUT
            escalated = False
            while self.helper_status is None:
                try:
                    got, status = os.waitpid(self.helper_pid, os.WNOHANG)
                except ChildProcessError:
                    self.cleanup_issues.append('native supervisor was reaped outside owner')
                    break
                except InterruptedError:
                    continue
                except OSError as error:
                    self.cleanup_issues.append('native supervisor wait failed: ' + str(error))
                    break
                if got:
                    self.helper_status = status
                    break
                if abort and not escalated:
                    try:
                        os.kill(self.helper_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    except OSError as error:
                        self.cleanup_issues.append('native supervisor TERM failed: ' + str(error))
                    escalated = True
                if time.monotonic() >= deadline:
                    if escalated == 'kill':
                        self.cleanup_issues.append('native supervisor did not reap after SIGKILL')
                        break
                    try:
                        os.kill(self.helper_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError as error:
                        self.cleanup_issues.append('native supervisor KILL failed: ' + str(error))
                    escalated = 'kill'
                    deadline = time.monotonic() + self.CLEANUP_TIMEOUT
                with selectors.DefaultSelector() as selector:
                    selector.select(.01)
            if self.helper_status not in (None, 0):
                self.cleanup_issues.append('native supervisor failed: ' + repr(self.helper_status))
        if self.pidfd is not None:
            try:
                os.close(self.pidfd)
            except OSError as error:
                self.cleanup_issues.append('native pidfd close failed: ' + str(error))
        return self.cleanup_issues


def native_capability_probe(helper):
    """Deliver a signal to two owned group members before CLI acquisition."""
    try:
        with ExitStack() as stack:
            fds = CaptureFDs(stack)
            null, = fds.acquire((os.open(os.devnull, os.O_RDWR),))
            reader, writer = fds.acquire(os.pipe())
            native = NativeChild(helper, [helper['path'], 'signal-probe'], dict(os.environ),
                                 str(TMP), True, null, writer, null, (null, reader, writer))
            stack.callback(native.close, True)
            fds.close(writer)
            os.set_blocking(reader, False)
            output, sent = b'', False
            deadline = time.monotonic() + NativeChild.CLEANUP_TIMEOUT
            with selectors.DefaultSelector() as selector:
                selector.register(reader, selectors.EVENT_READ)
                while True:
                    for _, _ in selector.select(.01):
                        data = os.read(reader, 4096)
                        if not data:
                            selector.unregister(reader)
                        output += data
                    native.poll()
                    if b'READY\n' in output and not sent:
                        if not native.signal_group(signal.SIGUSR1):
                            raise NativeProtocolError('owned pidfd group probe could not signal')
                        sent = True
                    if native.exited and b'LEADER\n' in output and b'MEMBER\n' in output:
                        break
                    if time.monotonic() >= deadline:
                        raise NativeProtocolError('owned pidfd group delivery probe failed')
            state = (Path('/proc') / str(native.pid) / 'stat').read_text().rsplit(')', 1)[1].split()[0]
            if state != 'Z' or not native.signal_group(0):
                raise NativeProtocolError('WNOWAIT PID/group reservation probe failed')
            native.release()
            event = native.wait()
            if event['status'] != 0:
                raise NativeProtocolError('owned native capability probe exited unsuccessfully')
            issues = native.close()
            if issues:
                raise NativeProtocolError('; '.join(issues))
        return {'method': 'actual own-child pidfd group delivery + WNOWAIT reservation',
                'group_signal_flag': NativeChild.GROUP_SIGNAL_FLAG,
                'signal': 'SIGUSR1', 'received': ['LEADER', 'MEMBER'],
                'reserved_state': state, 'protocol_version': 2, 'status': event['status'],
                'probe_executable_sha256': helper['sha256']}
    except (OSError, NativeProtocolError) as error:
        raise RuntimeError('unsupported Linux native capture capability: ' + str(error)) from error


def descendants(pid):
    """Linux process identities, checked before the observer's forced cleanup."""
    children, pending = {}, [pid]
    while pending:
        parent = pending.pop()
        try:
            values = (Path('/proc') / str(parent) / 'task' / str(parent) / 'children').read_text().split()
        except OSError:
            continue
        for value in values:
            child = int(value)
            if child in children:
                continue
            try:
                fields = (Path('/proc') / value / 'stat').read_text().rsplit(')', 1)[1].split()
                children[child] = {'pid': child, 'start_ticks': fields[19], 'state': fields[0], 'group': int(fields[2])}
                pending.append(child)
            except OSError:
                pass
    return children


def child_state(identity):
    try:
        fields = (Path('/proc') / str(identity['pid']) / 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[0] if fields[19] == identity['start_ticks'] else None
    except OSError:
        return None


def living(identity):
    return child_state(identity) not in (None, 'Z')


def clipboard_events(env):
    path = env.get('PB_HARNESS_CLIPBOARD_LOG')
    try:
        return [json.loads(line) for line in Path(path).read_text().splitlines()] if path else []
    except (OSError, ValueError):
        return []


def useful(terminal, wait, trace, tty):
    text = terminal.text()
    cooked_matches = ('cooked' not in wait or
                       (tty['canonical'] and tty['echo'] if wait['cooked'] else
                        not tty['canonical'] and not tty['echo'] and not tty['signals']))
    return (all(s in text for s in wait.get('contains', [])) and
            all(s not in text for s in wait.get('absent', [])) and
            ('alternate' not in wait or terminal.alternate == wait['alternate']) and
            cooked_matches and
            len(trace) >= wait.get('requests', 0) and
            all(any(r.get('path', '').split('?')[0] == path for r in trace)
                for path in wait.get('request_paths', [])))


class CaptureFDs:
    """Each acquisition registers its own token, including partial setup paths."""
    def __init__(self, stack):
        self.stack, self.tokens, self.issues = stack, {}, []

    def acquire(self, pair):
        for fd in pair:
            token = [fd, True]
            self.tokens[fd] = token
            self.stack.callback(self._close, token)
        return pair

    def _close(self, token):
        if token[1]:
            token[1] = False
            try:
                os.close(token[0])
            except OSError as error:
                self.issues.append('capture fd close failed: ' + str(error))

    def close(self, fd):
        if fd in self.tokens:
            self._close(self.tokens[fd])


def execute(command, env, cwd, case, fixture=None, *, proc_sampler=None,
            continue_running=None, readiness_fn=None, observer_config=None, spawn_helper_build=True,
            resize_signal_policy='legacy'):
    """A complete exception-safe capture of one native-owned CLI session."""
    with ExitStack() as stack:
        fds = CaptureFDs(stack)
        result = _execute(command, env, cwd, case, fixture, proc_sampler=proc_sampler,
                          continue_running=continue_running, readiness_fn=readiness_fn,
                          observer_config=observer_config, spawn_helper_build=spawn_helper_build,
                          resize_signal_policy=resize_signal_policy, fd_scope=fds)
    if fds.issues:
        result['failure'] = result['failure'] or '; '.join(fds.issues)
    return result


def _execute(command, env, cwd, case, fixture=None, *, proc_sampler=None,
            continue_running=None, readiness_fn=None, observer_config=None, spawn_helper_build=True,
            resize_signal_policy='legacy', fd_scope=None):
    """One controlling-PTY CLI; native child wait4 excludes the Python image."""
    if resize_signal_policy not in ('legacy', 'ioctl-only-changed'):
        raise ValueError('resize_signal_policy must be legacy or ioctl-only-changed')
    if resize_signal_policy == 'ioctl-only-changed':
        def geometry(value, label):
            if not isinstance(value, (list, tuple)) or len(value) != 2 or any(
                    type(dimension) is not int or not 1 <= dimension <= 65535 for dimension in value):
                raise ValueError(label + ' requires two integer dimensions in 1..65535')
            return tuple(value)
        current = geometry(case.get('size', [100, 30]), 'initial size')
        for index, action in enumerate(case.get('actions', [])):
            if isinstance(action, dict) and 'resize' in action:
                updated = geometry(action['resize'], f'action {index} resize')
                if updated == current:
                    raise ValueError(f'action {index} resize is unchanged under ioctl-only-changed')
                current = updated
    dependencies()
    observer = {'record_termios': True, 'record_chunks': True, 'record_descendants': True}
    if observer_config is not None:
        if set(observer_config) - set(observer) or any(not isinstance(v, bool) for v in observer_config.values()):
            raise ValueError('observer_config supports only boolean record_termios/record_chunks/record_descendants')
        observer.update(observer_config)
    sampler = proc_sample if proc_sampler is None else proc_sampler
    helper = spawn_helper(build=spawn_helper_build, probe=spawn_helper_build)
    columns, rows = case.get('size', [100, 30])
    master, slave = fd_scope.acquire(os.openpty())
    winsize(slave, columns, rows)
    before = termios_state(slave)
    err_read, err_write = fd_scope.acquire(os.pipe())
    pipe_mode = case.get('io') == 'pipe'
    in_read, in_write = fd_scope.acquire(os.pipe()) if pipe_mode else (-1, -1)
    out_read, out_write = fd_scope.acquire(os.pipe()) if pipe_mode else (-1, -1)
    started = time.monotonic_ns()
    native = NativeChild(helper, command, env, cwd, pipe_mode,
                         in_read if pipe_mode else slave, out_write if pipe_mode else slave, err_write,
                         (master, slave, err_read, err_write, in_read, in_write, out_read, out_write))
    fd_scope.stack.callback(native.close, True)
    pid = native.pid
    fd_scope.close(err_write)
    if pipe_mode:
        fd_scope.close(in_read)
        fd_scope.close(out_write)
        os.set_blocking(in_write, False)
    raw, stderr, actions, frames, samples, responses = bytearray(), bytearray(), [], [], [], []
    chunks = []
    failure = ('cli setup failure: ' + native.exec_error['stage']) if native.exec_error else None
    status, usage = None, None
    stdin_payload, stdin_offset = case.get('stdin', '').encode() if pipe_mode else b'', 0
    cleanup_issues = []
    def send_group(number, reason):
        nonlocal failure
        if not native.signal_group(number):
            failure = failure or reason + ': process group unavailable'
            return False
        return True
    def send_reply(data):
        if not pipe_mode:
            os.write(master, data)
            responses.append({'bytes_hex': data.hex(), 'monotonic_ns': time.monotonic_ns()})
    terminal = Terminal(columns, rows, case.get('theme', 'dark'), send_reply)
    terminal.feed(case.get('initial_screen', '').encode())
    terminal.byte_offset = 0
    initial = terminal.frame()
    initial_modes = terminal.session_modes()
    script, index = case.get('actions', []), 0
    action_started = started
    stable_since = first_match = last_hash = None
    trace, next_trace, next_sample = [], 0, 0
    last_write, bytes_at_action = started, 0
    observations = []
    children, helpers = {}, []
    exited_at = reaped_at = None
    with selectors.DefaultSelector() as selector:
        stdout_fd = out_read if pipe_mode else master
        for fd, name in ((stdout_fd, 'stdout'), (err_read, 'stderr')):
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ, name)
        if pipe_mode:
            if stdin_payload:
                selector.register(in_write, selectors.EVENT_WRITE, 'stdin')
            else:
                fd_scope.close(in_write)
        try:
            while True:
                now = time.monotonic_ns()
                if exited_at is None:
                    event = native.poll()
                    if event is not None:
                        exited_at = event['exited_monotonic_ns']
                        if pipe_mode and in_write in selector.get_map():
                            if stdin_offset < len(stdin_payload):
                                failure = failure or 'CLI exited before complete stdin payload'
                            selector.unregister(in_write)
                            fd_scope.close(in_write)
                if now >= next_sample and exited_at is None:
                    sample = sampler(pid) if native.same_process() else None
                    if sample and native.same_process():
                        samples.append(sample)
                    if observer['record_termios']:
                        observations.append({'monotonic_ns': now, 'termios': termios_state(slave)})
                    if observer['record_descendants'] and native.same_process():
                        observed = descendants(pid)
                        if native.same_process():
                            children.update(observed)
                    next_sample = now + 50_000_000
                if exited_at is None:
                    if continue_running is not None and failure is None:
                        reason = continue_running()
                        if reason is not None:
                            if not isinstance(reason, str):
                                raise ValueError('continue_running returns None or a failure string')
                            failure = reason
                            send_group(signal.SIGKILL, 'kill')
                if exited_at is None and now - started > case.get('timeout', 8) * 1e9 and failure is None:
                    failure = 'session timeout'
                    send_group(signal.SIGKILL, 'kill')
                if exited_at is not None and (not selector.get_map() or now - exited_at > 200_000_000):
                    break
                for key, _ in selector.select(0.01):
                    if key.data == 'stdin':
                        try:
                            sent = os.write(key.fd, stdin_payload[stdin_offset:stdin_offset + 65536])
                            stdin_offset += sent
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            failure = failure or 'CLI stdin closed before complete payload'
                            send_group(signal.SIGKILL, 'stdin failure')
                            selector.unregister(key.fd)
                            fd_scope.close(key.fd)
                            continue
                        if stdin_offset == len(stdin_payload):
                            selector.unregister(key.fd)
                            fd_scope.close(key.fd)
                        continue
                    try:
                        data = os.read(key.fd, 65536)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        data = b''
                    if not data:
                        selector.unregister(key.fd)
                        continue
                    if key.data == 'stdout':
                        if observer['record_chunks']:
                            chunks.append({'start': len(raw), 'end': len(raw) + len(data),
                                           'monotonic_ns': time.monotonic_ns()})
                        raw.extend(data)
                        terminal.feed(data)
                        last_write = time.monotonic_ns()
                    else:
                        stderr.extend(data)
                    if len(raw) + len(stderr) > case.get('max_output_bytes', 64 << 20):
                        failure = failure or 'output limit exceeded'
                        if exited_at is None:
                            send_group(signal.SIGKILL, 'kill')
                if exited_at is not None or index >= len(script):
                    continue
                now = time.monotonic_ns()
                action = script[index]
                wait = action.get('wait', {})
                if wait.get('clipboard_completed'):
                    completed = [e for e in clipboard_events(env) if e.get('event') == 'complete']
                    if len(completed) < wait['clipboard_completed']:
                        continue
                if fixture and (wait.get('requests') or wait.get('request_paths')) and now >= next_trace:
                    trace = fixture.trace()
                    next_trace = now + 50_000_000
                tty = termios_state(slave)
                qualifies = (useful(terminal, wait, trace, tty) if readiness_fn is None else
                             readiness_fn(terminal, wait, trace, tty, action_started, now))
                if not qualifies:
                    stable_since = last_hash = None
                    continue
                if wait.get('unchanged_from'):
                    anchor = next((f for f in frames if f['name'] == wait['unchanged_from']), None)
                    if anchor is None:
                        raise ValueError('missing unchanged-frame anchor: ' + wait['unchanged_from'])
                    if terminal.frame() != anchor['frame'] or tty != anchor['termios']:
                        stable_since = last_hash = None
                        continue
                fingerprint = digest(terminal.frame())
                if first_match is None:
                    first_match = now
                if fingerprint != last_hash:
                    stable_since, last_hash = now, fingerprint
                if now - stable_since < action.get('stable_ms', 80) * 1e6:
                    continue
                frame = terminal.frame()
                stamp = {'name': action.get('checkpoint', f'action-{index}'),
                         'first_useful_monotonic_ns': first_match,
                         'first_ever_match_monotonic_ns': first_match,
                         'stable_since_monotonic_ns': stable_since,
                         'ready_monotonic_ns': now, 'action_started_monotonic_ns': action_started,
                         'last_output_monotonic_ns': last_write, 'bytes_since_action': len(raw) - bytes_at_action,
                         'frame': frame, 'termios': tty}
                if 'checkpoint' in action:
                    frames.append(stamp)
                event = {'index': index, 'monotonic_ns': now, 'evaluated_monotonic_ns': now, 'wait': wait}
                if 'send' in action or 'key' in action:
                    data = action.get('send', KEYS.get(action.get('key'), ''))
                    if 'key' in action and action['key'] not in KEYS:
                        raise ValueError('unknown key: ' + action['key'])
                    if pipe_mode:
                        raise ValueError('pipe actions cannot send terminal keys')
                    data = phase3.substitute(data, fixture.urls if fixture else {}).encode()
                    event['dispatch_monotonic_ns'] = time.monotonic_ns()
                    os.write(master, data)
                    event['input_hex'] = data.hex()
                if 'resize' in action:
                    width, height = action['resize']
                    terminal.resize(width, height)
                    event['dispatch_monotonic_ns'] = time.monotonic_ns()
                    winsize(slave, width, height)
                    if resize_signal_policy == 'legacy':
                        send_group(signal.SIGWINCH, 'resize')
                    event['resize_signal_source'] = ('ioctl+explicit' if resize_signal_policy == 'legacy'
                                                     else 'ioctl-only')
                    event['resize'] = action['resize']
                if 'release' in action:
                    fixture.release(action['release'])
                    event['release'] = action['release']
                if 'signal' in action:
                    number = getattr(signal, action['signal'])
                    event['dispatch_monotonic_ns'] = time.monotonic_ns()
                    send_group(number, 'signal')
                    event['signal'] = number
                actions.append(event)
                index += 1
                action_started, bytes_at_action = now, len(raw)
                first_match = stable_since = last_hash = None
        except NativeProtocolError as error:
            failure = failure or str(error)
        finally:
            try:
                if exited_at is None:
                    send_group(signal.SIGKILL, 'cleanup')
                    event = native.wait_exited()
                    exited_at = event['exited_monotonic_ns']
            except (NativeProtocolError, OSError) as error:
                cleanup_issues.append(str(error))
            try:
                helpers = clipboard_events(env)
                for event in helpers:
                    if 'start_ticks' in event:
                        children[event['pid']] = event
                survivors = [identity for identity in children.values() if living(identity)]
                unreaped = [{**identity, 'final_state': child_state(identity)} for identity in children.values()
                            if child_state(identity) is not None]
                # All PID/group access finishes before releasing the reserved CLI.
                send_group(signal.SIGKILL, 'descendant cleanup')
                after = termios_state(slave)
            finally:
                native.release()
                try:
                    event = native.wait()
                    status, reaped_at = event['status'], event['reaped_monotonic_ns']
                    if native.exec_error is None:
                        usage = SimpleNamespace(**event)
                except (NativeProtocolError, OSError) as error:
                    cleanup_issues.append(str(error))
                finally:
                    cleanup_issues.extend(native.close(abort=True))
    if cleanup_issues:
        failure = failure or 'native lifecycle cleanup failed: ' + '; '.join(cleanup_issues)
    terminal.feed(b'', final=True)
    if index != len(script):
        failure = failure or f'process exited before action {index}/{len(script)}'
    if terminal.unsupported or terminal.history_overflow:
        failure = failure or 'terminal observer encountered unsupported controls/history overflow'
    termination = None if status is None else (
        {'signal': os.WTERMSIG(status), 'core_dumped': os.WCOREDUMP(status)} if os.WIFSIGNALED(status)
        else {'exit': os.WEXITSTATUS(status)})
    metrics = {'source': 'native CLI wait4 rusage + Linux /proc; supervisor/observer usage excluded',
               'resource_source': 'native_child_wait4' if usage is not None else None,
               'native_spawn': {**helper, 'cli_pid': pid if native.exec_error is None else None, 'setup_child_pid': pid, 'supervisor_pid': native.helper_pid,
                                'started_monotonic_ns': native.initial['started_monotonic_ns'],
                                'pre_exec_scope': 'small native setsid/TIOCSCTTY/chdir/exec; no Python image'},
               'user_cpu_ms': (usage.ru_utime * 1000 if usage is not None else None), 'system_cpu_ms': (usage.ru_stime * 1000 if usage is not None else None),
               'peak_rss_bytes': (usage.ru_maxrss * 1024 if usage is not None else None),
               'minor_faults': (usage.ru_minflt if usage is not None else None), 'major_faults': (usage.ru_majflt if usage is not None else None),
               'voluntary_context_switches': (usage.ru_nvcsw if usage is not None else None),
               'involuntary_context_switches': (usage.ru_nivcsw if usage is not None else None),
               'block_input_operations': usage.ru_inblock if usage is not None else None,
               'block_output_operations': usage.ru_oublock if usage is not None else None,
               'wait4_descendant_attribution': 'CLI plus children it waits for; RSS maximum, CPU/counters accumulated',
               'observed_descendants': list(children.values()),
               'descendant_comparison_eligible': observer['record_descendants'] and not children and not helpers,
               'stdout_bytes': len(raw), 'stderr_bytes': len(stderr),
               'stdin_bytes_expected': len(stdin_payload), 'stdin_bytes_written': stdin_offset,
               'observed_process_wall_ms': (exited_at - started) / 1e6 if exited_at is not None else None,
               'observer_total_wall_ms': (time.monotonic_ns() - started) / 1e6,
               'proc_samples': samples, 'clock_ticks_per_second': os.sysconf('SC_CLK_TCK')}
    return {'schema_version': SCHEMA, 'cell_fields': CELL_FIELDS, 'stdout': bytes(raw), 'stderr': bytes(stderr),
            'termination': termination, 'failure': failure, 'frames': frames,
            'final_frame': terminal.frame(), 'initial_frame': initial,
            'terminal_modes': {'initial': initial_modes, 'final': terminal.session_modes()},
            'history_overflow': terminal.history_overflow,
            'termios_before': before, 'termios_after': after, 'termios_observations': observations,
            'termios_restored': before == after, 'actions': actions, 'responses': responses,
            'transitions': terminal.transitions, 'unsupported': terminal.unsupported,
            'post_screen_stdout': bytes(raw[terminal.last_alt_exit_offset:])
                                  if terminal.last_alt_exit_offset is not None else None,
            'metrics': metrics, 'started_monotonic_ns': started, 'stdout_chunks': chunks,
            'reaped_monotonic_ns': exited_at, 'exited_monotonic_ns': exited_at,
            'native_wait4_reaped_monotonic_ns': reaped_at, 'exec_error': native.exec_error,
            'native_cleanup_issues': cleanup_issues,
            'clipboard_events': helpers, 'surviving_children_before_observer_cleanup': survivors,
            'unreaped_children_before_observer_cleanup': unreaped,
            'resize_signal_policy': resize_signal_policy,
            'observer_config': observer,
            'observer_seams': {'custom_proc_sampler': proc_sampler is not None,
                               'custom_readiness': readiness_fn is not None,
                               'deadline_callback': continue_running is not None},
            'capture_kind': 'functional-observation; not a benchmark'}


def config_snapshot(home, replacements):
    state = phase2.config_state(home, replacements)
    for entry in state.values():
        if 'toml' in entry:
            entry.pop('raw_sha256', None)
    return state


def sample(binary, case, output, *, execute_fn=None):
    """Fresh fixture/config per binary; never reuse a mutation or OAuth session."""
    TMP.mkdir(parents=True, exist_ok=True)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='phase7-', dir=TMP) as temporary:
        root, fixture = Path(temporary), None
        home = root / 'home'
        for name in ('config', 'cache', 'data', 'state', 'tmp'):
            (home / name).mkdir(parents=True)
        try:
            if case.get('fixture') is not None:
                (root / 'fixture').mkdir()
                fixture = Fixture(case['fixture'], root / 'fixture')
            urls = fixture.urls if fixture else {'url': phase2.NO_SERVER}
            phase2.seed(home, case, urls['url'])
            replacements = {str(home): '<HOME>'}
            if fixture:
                replacements.update({v: ('<' + k.upper() + '>') for k, v in urls.items()})
            before = config_snapshot(home, replacements)
            env = phase2.isolated_env(home, {'env': {'TERM': 'xterm-256color',
                         'COLORTERM': 'truecolor', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                         'PB_THEME': case.get('theme', 'dark'), **case.get('env', {})}})
            env.update({name: str(TMP) for name in ('TMPDIR', 'GOTMPDIR', 'CLAUDE_CODE_TMPDIR')})
            for name in case.get('unset', []):
                env.pop(name, None)
            if case.get('clipboard'):
                helper = root / 'clipboard'
                helper.mkdir()
                payload, log = helper / 'payload', helper / 'events.jsonl'
                payload.write_text(case['clipboard'].get('text', 'clip-café\n日志'))
                script = ('#!' + sys.executable + '\n'
                          'import json,os,resource,signal,sys,time\n'
                          'def event(name,number=None):\n'
                          ' fields=open("/proc/self/stat").read().rsplit(")",1)[1].split()\n'
                          ' value={"event":name,"pid":os.getpid(),"argv":sys.argv[1:],'
                          '"monotonic_ns":time.monotonic_ns(),"start_ticks":fields[19],'
                          '"nofile_limit":list(resource.getrlimit(resource.RLIMIT_NOFILE)),'
                          '"fd_count":len(os.listdir("/proc/self/fd")),"wayland":os.getenv("WAYLAND_DISPLAY"),'
                          '"signal":number}\n'
                          ' with open(os.environ["PB_HARNESS_CLIPBOARD_LOG"],"a") as f: f.write(json.dumps(value)+"\\n")\n'
                          'event("start")\n'
                          'def stopped(number,frame):\n'
                          ' event("signal",number);raise SystemExit(128+number)\n'
                          'for number in (signal.SIGHUP,signal.SIGTERM,signal.SIGINT): signal.signal(number,stopped)\n'
                          'time.sleep(int(os.environ["PB_HARNESS_CLIPBOARD_DELAY_MS"])/1000)\n'
                          'event("complete")\n'
                          'sys.stdout.write(open(os.environ["PB_HARNESS_CLIPBOARD_PATH"]).read())\n')
                (helper / 'xclip').write_text(script)
                (helper / 'xclip').chmod(0o700)
                env.update(PATH=str(helper) + ':' + os.defpath,
                           PB_HARNESS_CLIPBOARD_PATH=str(payload), PB_HARNESS_CLIPBOARD_LOG=str(log),
                           PB_HARNESS_CLIPBOARD_DELAY_MS=str(case['clipboard'].get('delay_ms', 0)))
                env.pop('WAYLAND_DISPLAY', None)
            command = [str(Path(binary).resolve()), *[phase3.substitute(a, urls) for a in case['argv']]]
            executor = execute if execute_fn is None else execute_fn
            result = executor(command, env, home, case, fixture)
            result.update(name=case['name'], binary=phase1.binary_identity(Path(binary)),
                          config_before=before, config_after=config_snapshot(home, replacements),
                          requests=fixture.trace() if fixture else [],
                          milestones=fixture.milestones() if fixture else [],
                          fixture_events=fixture.events() if fixture else [],
                          replacements=replacements, case=case)
            path = home / 'config/pb/config.toml'
            if path.is_file():
                (output / 'config-after.toml').write_bytes(path.read_bytes())
            for stream in ('stdout', 'stderr'):
                (output / (stream + '.bin')).write_bytes(result[stream])
            serial = copy.deepcopy(result)
            for field in ('stdout', 'stderr', 'post_screen_stdout'):
                data = serial.pop(field)
                serial[field + '_hex'] = data.hex() if data is not None else None
            (output / 'session.json').write_text(json.dumps(serial, ensure_ascii=False, separators=(',', ':')) + '\n')
            return result
        finally:
            if fixture:
                fixture.close()


def normalize(value, replacements):
    if isinstance(value, str):
        for old, new in sorted(replacements.items(), key=lambda item: -len(item[0])):
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [normalize(v, replacements) for v in value]
    if isinstance(value, dict):
        return {k: normalize(v, replacements) for k, v in value.items()}
    return value


def normalized_frame(frame, replacements):
    """Only ephemeral fixture-origin glyphs; leave style, position and width intact."""
    frame = copy.deepcopy(frame)
    for field in ('cells', 'scrollback'):
        cells = [cell for row in frame[field] for cell in row]
        pieces = [cell[0] or ' ' for cell in cells]
        text = ''.join(pieces)
        positions = [index for index, piece in enumerate(pieces) for _ in piece]
        # Origins are ASCII one-cell characters; handle wrapped origins too.
        for old, new in replacements.items():
            if not old.startswith(('http://', 'https://')):
                continue
            start = 0
            while (offset := text.find(old, start)) >= 0:
                label = new.ljust(len(old))[:len(old)]
                for index, char in enumerate(label):
                    cells[positions[offset + index]][0] = char
                text = text[:offset] + label + text[offset + len(old):]
                start = offset + len(old)
    return frame


def loading_spinner_cells(frame):
    """Recognize the single visible spinner.Line cell before the result label."""
    if not frame['alternate']:
        return []
    return [cell for row in frame['cells'] for x, cell in enumerate(row)
            if cell[0] in ('|', '/', '-', '\\') and x > 0 and row[x - 1][0] == ' ' and
            ''.join(c[0] for c in row[x + 1:x + 13]) == ' fetching...']


def checkpoint_frame(frame, replacements, case, name):
    frame = normalized_frame(frame, replacements)
    if name in case.get('loading_spinner_checkpoints', []):
        cells = loading_spinner_cells(frame)
        if len(cells) == 1:
            # Independent Go sessions select different 100ms animation phases.
            # Change only data, retaining attributes and every frame coordinate.
            cells[0][0] = '|'
    return frame


def request_contract(result):
    requests = []
    for request in result['requests']:
        headers = request.get('headers', {})
        body = request.get('body', '')
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            pass
        requests.append({'method': request['method'], 'path': request.get('path', request.get('target')),
                         'body': body, 'headers': {k: v for k, v in headers.items()
                                                   if k.lower() in CONTRACT_HEADERS},
                         'unexpected': request.get('unexpected', False)})
    return normalize(requests, result['replacements'])


def ordered_request_contract(result):
    requests = request_contract(result)
    count = result['case'].get('initial_fanout_count', 0)
    if count:
        # Only pre-input startup effects can commute. Never deduplicate or
        # change auth/body/query membership, repeated effects or later order.
        requests[:count] = sorted(requests[:count], key=lambda r: json.dumps(r, sort_keys=True))
    return requests


def session_terminal_modes(result):
    """Read captured modes, or recover legacy evidence from its retained ANSI.

    Mode parsing is independent of terminal geometry and resize timing. Reuse
    the same UTF8/escape tokenizer without rendering: an OSC title containing
    CSI-looking text must not be mistaken for a real mode change. No retained
    files are rewritten and absent raw evidence is a validation failure.
    """
    if 'terminal_modes' in result:
        modes = result['terminal_modes']
        if not isinstance(modes, dict) or set(modes) != {'initial', 'final'}:
            raise ValueError('terminal private modes evidence invalid')
        for state in modes.values():
            if not isinstance(state, list) or any(type(mode) is not int or mode not in TERMINAL_SESSION_MODES
                                                  for mode in state) or len(state) != len(set(state)):
                raise ValueError('terminal private modes evidence invalid')
        return {name: sorted(state) for name, state in modes.items()}
    if 'terminal_modes_error' in result:
        raise ValueError(result['terminal_modes_error'])
    raw = result.get('stdout')
    if not isinstance(raw, bytes):
        raise ValueError('terminal private modes evidence unavailable')

    class ModesOnly(Terminal):
        def _stream(self, token):
            pass

        def _switch(self, enabled, mode):
            self.alternate = enabled

    terminal = ModesOnly(1, 1, result.get('case', {}).get('theme', 'dark'))
    seed = result.get('case', {}).get('initial_screen', '').encode()
    for offset in range(0, len(seed), 65536):
        terminal.feed(seed[offset:offset + 65536])
    initial = terminal.session_modes()
    for offset in range(0, len(raw), 65536):
        terminal.feed(raw[offset:offset + 65536])
    terminal.feed(b'', final=True)
    return {'initial': initial, 'final': terminal.session_modes()}


def session_history_overflow(result):
    """Legacy history is safe only when the retained evidence proves no loss.

    Without resets, main history grows monotonically. A final history below
    capacity therefore cannot have evicted a row. Capacity, history clears,
    reverse-index's unexported bottom deque, or absent evidence are ambiguous;
    do not manufacture a passing flag or rewrite the original failure text.
    """
    if 'history_overflow' in result:
        if type(result['history_overflow']) is not bool:
            raise ValueError('history overflow evidence invalid')
        return result['history_overflow']
    if 'history_overflow_error' in result:
        raise ValueError(result['history_overflow_error'])
    raw = result.get('stdout')
    seed = result.get('case', {}).get('initial_screen', '')
    frames = [result.get('final_frame')]
    if 'initial_frame' in result:
        frames.append(result['initial_frame'])
    frames.extend(row.get('frame') for row in result.get('frames', []))
    frames.extend(row['frame'] for row in result.get('transitions', []) if 'frame' in row)
    if not isinstance(raw, bytes) or not isinstance(seed, str) or any(
            not isinstance(frame, dict) or not isinstance(frame.get('scrollback'), list) or
            len(frame['scrollback']) >= 20000 for frame in frames):
        raise ValueError('history overflow evidence unavailable')

    class HistoryControls:
        uncertain = False

        def ignore(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            return self.ignore

        def reset(self):
            self.uncertain = True

        def reverse_index(self):
            self.uncertain = True

        def erase_in_display(self, how=0, *args, **kwargs):
            if how == 3:
                self.uncertain = True

    # Use pyte's parser rather than searching bytes: OSC contents, C1 CSI and
    # zero-padded parameters must have the same meaning as in the observer.
    pyte, _ = dependencies()
    controls = HistoryControls()
    stream = pyte.Stream(controls)
    try:
        stream.feed(seed)
        stream.feed(raw.decode(errors='replace'))
    except (ValueError, TypeError):
        raise ValueError('history overflow evidence unavailable') from None
    if controls.uncertain:
        raise ValueError('history overflow evidence unavailable')
    return False


def validate(result):
    """Independent effect/lifecycle assertions, before any oracle equality check."""
    case, issues = result['case'], []
    if result['failure']:
        issues.append(result['failure'])
    try:
        if session_history_overflow(result):
            issues.append('terminal observer history overflow')
    except ValueError as error:
        issues.append(str(error))
    expected = case.get('expect', {})
    try:
        modes = session_terminal_modes(result)
    except ValueError as error:
        issues.append(str(error))
    else:
        if expected.get('screen_restored', True) and modes['initial'] != modes['final']:
            issues.append('terminal private modes not restored')
    for check in expected.get('checkpoint_cells', []):
        frame = next((f['frame'] for f in result['frames'] if f['name'] == check['checkpoint']), None)
        row, col = check['row'], check['col']
        if frame is None or row >= len(frame['cells']) or col >= len(frame['cells'][row]) or \
                frame['cells'][row][col][0] != check['data']:
            issues.append('expected cell differs at ' + check['checkpoint'] + f' ({row},{col})')
    for name in case.get('loading_spinner_checkpoints', []):
        frame = next((f['frame'] for f in result['frames'] if f['name'] == name), None)
        if frame is None or len(loading_spinner_cells(frame)) != 1:
            issues.append('missing unique active loading spinner at ' + name)
    if 'termination' in expected and result['termination'] != expected['termination']:
        issues.append('unexpected termination: ' + repr(result['termination']))
    if expected.get('restored', True) and not result['termios_restored']:
        issues.append('termios not restored')
    if expected.get('screen_restored', True) and (result['final_frame']['alternate'] or
                                                not result['final_frame']['cursor']['visible']):
        issues.append('alternate screen/cursor not restored')
    if 'termios_restored' in expected and result['termios_restored'] != expected['termios_restored']:
        issues.append('explicit termios restoration disposition differs')
    for key, actual in (('alternate', result['final_frame']['alternate']),
                        ('cursor_visible', result['final_frame']['cursor']['visible'])):
        if key in expected and actual != expected[key]:
            issues.append('explicit terminal disposition differs: ' + key)
    if 'signal_sequence' in expected:
        signal_actions = [a for a in result['actions'] if 'signal' in a]
        if [a['signal'] for a in signal_actions] != expected['signal_sequence']:
            issues.append('signal action sequence differs')
        term_actions = [a for a in signal_actions if a['signal'] == signal.SIGTERM]
        for first, second in zip(term_actions, term_actions[1:]):
            if second['monotonic_ns'] - first['monotonic_ns'] < expected.get('min_term_gap_ms', 0) * 1e6:
                issues.append('SIGTERM deliveries are insufficiently separated')
    requests = request_contract(result)
    count = case.get('initial_fanout_count', 0)
    if count:
        initial = requests[:count]
        actual_lines = sorted(r['method'] + ' ' + r['path'].split('?')[0] for r in initial)
        if actual_lines != sorted(case['initial_fanout_members']):
            issues.append('initial fan-out membership/multiplicity differs')
        lines = [r['method'] + ' ' + r['path'].split('?')[0] for r in initial]
        for first, second in case.get('initial_fanout_constraints', []):
            if first not in lines or second not in lines or lines.index(first) >= lines.index(second):
                issues.append('initial effect dependency violated: ' + first + ' before ' + second)
        urls = [r['path'] for r in initial]
        for first, second in case.get('initial_fanout_url_constraints', []):
            if first not in urls or second not in urls or urls.index(first) >= urls.index(second):
                issues.append('initial effect URL dependency violated: ' + first + ' before ' + second)
    if 'requests' in expected:
        lines = [r['method'] + ' ' + r['path'].split('?')[0] for r in requests]
        if lines != expected['requests']:
            issues.append(f'requests: expected {expected["requests"]!r}, got {lines!r}')
    if any(r['unexpected'] for r in requests):
        issues.append('unexpected fixture request')
    for assertion in expected.get('request_checks', []):
        matching = [r for r in requests if r['method'] == assertion['method'] and
                    r['path'].split('?')[0] == assertion['path']]
        if not matching:
            issues.append('missing request assertion target: ' + assertion['path'])
            continue
        if 'count' in assertion and len(matching) != assertion['count']:
            issues.append('request multiplicity differs: ' + assertion['path'])
        if 'bodies' in assertion and [r['body'] for r in matching] != assertion['bodies']:
            issues.append('ordered request bodies differ: ' + assertion['path'])
        for request in matching:
            if 'body' in assertion and request['body'] != assertion['body']:
                issues.append('request body differs: ' + assertion['path'])
            for key, value in assertion.get('body_fields', {}).items():
                if not isinstance(request['body'], dict) or request['body'].get(key) != value:
                    issues.append('request body field differs: ' + assertion['path'] + '.' + key)
            for key, value in assertion.get('headers', {}).items():
                if request['headers'].get(key) != value:
                    issues.append('request header differs: ' + assertion['path'] + '.' + key)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request['path']).query)
            for key, value in assertion.get('query', {}).items():
                if query.get(key) != value:
                    issues.append('request query differs: ' + assertion['path'] + '.' + key)
    for stream in ('stdout', 'stderr'):
        if not isinstance(result.get(stream), bytes):
            issues.append(stream + ' evidence unavailable')
            continue
        text = result[stream].decode(errors='replace')
        for phrase in expected.get(stream + '_contains', []):
            if phrase not in text:
                issues.append(stream + ' missing ' + repr(phrase))
        for phrase in expected.get(stream + '_absent', []):
            if phrase in text:
                issues.append(stream + ' unexpectedly contains ' + repr(phrase))
        if stream + '_exact' in expected and text != expected[stream + '_exact']:
            issues.append(stream + ' bytes differ from independent expectation')
        if stream + '_prefix' in expected and not result[stream].startswith(expected[stream + '_prefix'].encode()):
            issues.append(stream + ' required prefix differs')
    if expected.get('config_unchanged') and result['config_before'] != result['config_after']:
        issues.append('config changed')
    if 'clipboard_reads' in expected:
        starts = [e for e in result['clipboard_events'] if e['event'] == 'start']
        if len(starts) != expected['clipboard_reads']:
            issues.append('clipboard helper invocation count differs')
        if any(e['argv'] != ['-out', '-selection', 'clipboard'] for e in starts):
            issues.append('clipboard helper argv differs')
    if expected.get('children_reaped') and result.get('unreaped_children_before_observer_cleanup',
                                                    result['surviving_children_before_observer_cleanup']):
        issues.append('owned helper survived process exit before observer cleanup')
    document = result['config_after'].get('config/pb/config.toml', {}).get('toml', {})
    if expected.get('alternate_final_contains') or expected.get('alternate_final_absent'):
        final = next((t['frame'] for t in reversed(result['transitions']) if t['kind'] == 'before-alt-exit'), None)
        if final is None:
            issues.append('missing final alternate-screen evidence')
        else:
            text = '\n'.join(''.join(c[0] for c in row) for row in final['cells'])
            for phrase in expected.get('alternate_final_contains', []):
                if phrase not in text:
                    issues.append('final alternate screen missing ' + repr(phrase))
            for phrase in expected.get('alternate_final_absent', []):
                if phrase in text:
                    issues.append('failed View drew content: ' + repr(phrase))
    if 'default' in expected and document.get('DefaultProfile') != expected['default']:
        issues.append('unexpected default profile')
    for name, fields in expected.get('profiles', {}).items():
        actual = document.get('Profiles', {}).get(name)
        if actual is None:
            issues.append('missing profile: ' + name)
        else:
            for key, value in fields.items():
                if actual.get(key, '') != value:
                    issues.append(f'profile {name}.{key} differs')
    for name in expected.get('profiles_absent', []):
        if name in document.get('Profiles', {}):
            issues.append('profile still present: ' + name)
    # Password/API-key masking is required while a secret input is active.
    for secret in case.get('masked_values', []):
        for frame in result['frames']:
            text = '\n'.join(''.join(cell[0] for cell in row) for row in frame['frame']['cells'])
            if secret in text:
                issues.append('unmasked dummy secret in checkpoint ' + frame['name'])
    return issues


def compare(reference, candidate):
    issues = []
    for role, result in (('reference', reference), ('candidate', candidate)):
        issues.extend(role + ': ' + issue for issue in validate(result))
    for field in ('termination', 'config_before', 'config_after', 'termios_restored'):
        if reference[field] != candidate[field]:
            issues.append(field + ' differs')
    try:
        if session_terminal_modes(reference) != session_terminal_modes(candidate):
            issues.append('terminal private modes differ')
    except ValueError:
        pass  # validate above reports missing or malformed evidence per role.
    if ordered_request_contract(reference) != ordered_request_contract(candidate):
        issues.append('ordered request contract differs')
    for field in ('stderr', 'post_screen_stdout'):
        left, right = reference[field], candidate[field]
        if field == 'stderr' and reference['case'].get('runtime_sigquit_tail'):
            # Explicit runtime dump disposition: keep the complete first line,
            # retain raw diagnostic bytes in artifacts, ignore only its tail.
            if left is not None and left.startswith(b'SIGQUIT: '):
                left = left.partition(b'\n')[0] + b'\n'
            if right is not None and right.startswith(b'SIGQUIT: '):
                right = right.partition(b'\n')[0] + b'\n'
        if field == 'stderr' and reference['case'].get('runtime_view_panic_stack'):
            def stack_only(data):
                return bool(data and data.startswith(b'goroutine ') and b'runtime/debug.Stack(' in data and
                            b'recoverFromPanic' in data)
            if stack_only(left):
                left = b''
            if stack_only(right):
                right = b''
        if left is None or right is None:
            if left != right:
                issues.append(field + ' presence differs')
        elif normalize(left.decode(errors='surrogateescape'), reference['replacements']) != \
                normalize(right.decode(errors='surrogateescape'), candidate['replacements']):
            issues.append(field + ' bytes differ')
    if reference['case'].get('io') == 'pipe' and \
            isinstance(reference.get('stdout'), bytes) and isinstance(candidate.get('stdout'), bytes) and \
            normalize(reference['stdout'].decode(errors='surrogateescape'), reference['replacements']) != \
            normalize(candidate['stdout'].decode(errors='surrogateescape'), candidate['replacements']):
        issues.append('pipe stdout bytes differ')
    left = {f['name']: f['frame'] for f in reference['frames']}
    right = {f['name']: f['frame'] for f in candidate['frames']}
    if left.keys() != right.keys():
        issues.append('checkpoint names differ')
    for name in left.keys() & right.keys():
        # No stripping ANSI-derived style, border, cursor or trailing space.
        if checkpoint_frame(left[name], reference['replacements'], reference['case'], name) != \
                checkpoint_frame(right[name], candidate['replacements'], reference['case'], name):
            issues.append('styled cells/cursor/scrollback differ at ' + name)
    if normalized_frame(reference['final_frame'], reference['replacements']) != \
            normalized_frame(candidate['final_frame'], candidate['replacements']):
        issues.append('final styled cells/cursor/scrollback differ')
    for field in ('termios_before', 'termios_after'):
        if reference[field] != candidate[field]:
            issues.append(field + ' differs')
    return issues


def load_session(path, case=None):
    data = json.loads(Path(path).read_text())
    for field in ('stdout', 'stderr', 'post_screen_stdout'):
        value = data.pop(field + '_hex', None)
        data[field] = bytes.fromhex(value) if value is not None else None
    for field, recover in (('terminal_modes', session_terminal_modes),
                           ('history_overflow', session_history_overflow)):
        if field not in data:
            try:
                data[field] = recover(data)
            except ValueError as error:
                # Preserve the captured-case diagnosis for per-session validate,
                # including after reduce_report substitutes the active contract.
                data[field + '_error'] = str(error)
    if case is not None:
        data['case'] = case
    return data


def reduce_report(capture_root, output, cases):
    """Re-evaluate retained evidence with current explicit contracts, no processes."""
    output.mkdir(parents=True, exist_ok=False)
    case_map = {case['name']: case for case in cases}
    report = {'schema_version': SCHEMA, 'kind': 'functional-evidence-reduction',
              'capture_root': str(capture_root), 'cases': [],
              'harness_sources': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in (HERE / 'phase7.py', HERE / 'phase7_workloads.py', HERE / 'phase7_spawn.c')},
              'original_report_sha256': hashlib.sha256((capture_root / 'report.json').read_bytes()).hexdigest()
                                       if (capture_root / 'report.json').is_file() else None}
    for path in sorted(capture_root.glob('*/reference/session.json')):
        name = path.parent.parent.name
        if name not in case_map:
            continue
        candidate_path = path.parent.parent / 'candidate/session.json'
        reference = load_session(path)
        captured_case_sha256 = digest(reference['case'])
        reference['case'] = case_map[name]
        entry = {'name': name, 'reference_issues': validate(reference),
                 'documentary': bool(case_map[name].get('documentary')),
                 'captured_case_sha256': captured_case_sha256,
                 'active_case_sha256': digest(case_map[name]),
                 'contract_changed': captured_case_sha256 != digest(case_map[name])}
        if candidate_path.is_file():
            candidate = load_session(candidate_path, case_map[name])
            entry['issues'] = compare(reference, candidate)
        else:
            entry.update(pending=True, issues=['candidate evidence not complete'])
        report['cases'].append(entry)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def session_statistics(sessions, field, seed=7, draws=2000):
    """Resample independent session summaries; frames are never IID repetitions."""
    values = [s[field] for s in sessions]
    if not values or any(v is None for v in values):
        raise ValueError('missing session metric; invalid samples cannot become zeros')
    rng = random.Random(seed)
    estimates = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(draws))
    return {'unit': 'independent-session', 'count': len(values), 'median': statistics.median(values),
            'bootstrap_median_ci95': [estimates[int(draws * .025)], estimates[int(draws * .975)]],
            'inference': 'exploratory pointwise; no multiplicity-adjusted acceptance claim'}


def paired_session_statistics(rounds, seed=7, draws=2000):
    """Pair reference/candidate independent sessions by randomized round."""
    if not rounds or any(r.get('reference') is None or r.get('candidate') is None or
                         r['reference'] <= 0 for r in rounds):
        raise ValueError('invalid paired session metric')
    rng = random.Random(seed)
    def estimate(rows):
        return (statistics.median(r['candidate'] / r['reference'] for r in rows),
                statistics.median(r['candidate'] - r['reference'] for r in rows))
    ratio, delta = estimate(rounds)
    estimates = [estimate(rng.choices(rounds, k=len(rounds))) for _ in range(draws)]
    ratios, deltas = (sorted(e[index] for e in estimates) for index in (0, 1))
    return {'unit': 'paired-session-round', 'count': len(rounds), 'median_ratio': ratio,
            'median_absolute_delta': delta,
            'ratio_ci95': [ratios[int(draws * .025)], ratios[int(draws * .975)]],
            'delta_ci95': [deltas[int(draws * .025)], deltas[int(draws * .975)]],
            'inference': 'exploratory paired bootstrap; predeclared budgets/multiplicity still required'}


def require_quiet_window(path):
    approval = json.loads(Path(path).read_text())
    if approval.get('approved') is not True or approval.get('scope') != 'phase7' or \
            approval.get('expires_unix', 0) <= time.time():
        raise ValueError('quiet window must be current coordinator approval for phase7')
    return approval


def observer_floor(output, quiet_window, sessions=5, frames=20, *, execute_fn=None):
    """Measure echo-child write to emulator useful-frame observation, in real PTYs.

    The known emitter stamps CLOCK_MONOTONIC just before each write; stderr
    carries ground truth. Whole sessions, including all frames, are the unit.
    This is explicit timed work and cannot run without coordinator approval.
    """
    approval = require_quiet_window(quiet_window)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    program = ('import os,time,tty,termios\n'
               'saved=termios.tcgetattr(0);tty.setraw(0)\n'
               'for i in range(' + str(frames) + '):\n'
               ' t=time.monotonic_ns();os.write(2,(str(i)+" "+str(t)+"\\n").encode())\n'
               ' os.write(1,("\\x1b[H\\x1b[2JFRAME %04d"%i).encode())\n'
               ' os.read(0,1)\n'
               'termios.tcsetattr(0,termios.TCSANOW,saved)\n')
    for index in range(sessions):
        actions = [{**ready_floor(i), 'send': 'x', 'stable_ms': 0} for i in range(frames)]
        executor = execute if execute_fn is None else execute_fn
        result = executor([sys.executable, '-c', program], dict(os.environ), str(TMP),
                          {'actions': actions, 'size': [100, 30], 'timeout': 15})
        emissions = dict(map(int, line.split()) for line in result['stderr'].splitlines())
        latencies = [(frame['first_useful_monotonic_ns'] - emissions[i]) / 1e6
                     for i, frame in enumerate(result['frames'])]
        if result['failure'] or not result['termios_restored'] or len(latencies) != frames or any(t < 0 for t in latencies):
            raise RuntimeError('invalid observer-floor session')
        row = {'session': index, 'write_to_useful_ms': latencies,
               'median_ms': statistics.median(latencies), 'max_ms': max(latencies),
               'observer_poll_ms': 10, 'stable_frame_hold_ms': 0,
               'termination': result['termination']}
        rows.append(row)
    report = {'kind': 'observer-floor', 'approval': approval, 'sessions': rows,
              'summary': session_statistics(rows, 'median_ms'),
              'definition': 'PTY write timestamp -> known useful cells; includes PTY transit, '
                            'collector scheduling, emulator decode and readiness observation'}
    (output / 'observer-floor.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def ready_floor(index):
    return {'checkpoint': f'frame-{index}', 'wait': {'contains': [f'FRAME {index:04d}']}}


def main():
    import phase7_workloads
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('capture', 'compare', 'list', 'serve', 'observer-floor', 'reduce'))
    parser.add_argument('--reference', default='/home/ajs/.cache/pb-phase7/baselines/pb-go')
    parser.add_argument('--cloud-reference', default='/home/ajs/.cache/pb-phase7/baselines/pb-go-cloud')
    parser.add_argument('--candidate')
    parser.add_argument('--cloud-candidate')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--theme', choices=('dark', 'light', 'both'), default='dark')
    parser.add_argument('--scenario', type=Path)
    parser.add_argument('--quiet-window', type=Path)
    parser.add_argument('--capture-root', type=Path)
    args = parser.parse_args()
    if args.mode == 'serve':
        server = FixtureServer(json.loads(args.scenario.read_text()))
        server.start()
        print(json.dumps(server.urls), flush=True)
        try:
            # A fixture process is killed by its owner; this is not a campaign.
            signal.pause()
        finally:
            server.close()
        return 0
    if args.mode == 'observer-floor':
        if args.out is None or args.quiet_window is None:
            parser.error('observer-floor requires --out and coordinator --quiet-window')
        observer_floor(args.out, args.quiet_window)
        return 0
    cases = phase7_workloads.corpus(themes=('dark', 'light') if args.theme == 'both' else (args.theme,))
    if args.case:
        cases = [c for c in cases if any(token in c['name'] for token in args.case)]
    if not cases:
        parser.error('no matching cases')
    if args.mode == 'list':
        print('\n'.join(c['name'] for c in cases))
        return 0
    if args.mode == 'reduce':
        if args.capture_root is None or args.out is None:
            parser.error('reduce requires --capture-root and --out')
        report = reduce_report(args.capture_root, args.out, cases)
        for entry in report['cases']:
            print(entry['name'], entry['issues'])
        return int(any(e['issues'] for e in report['cases'] if not e['documentary']))
    if args.out is None or (args.mode == 'compare' and args.candidate is None):
        parser.error('--out is required; compare also requires --candidate')
    args.out.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': SCHEMA, 'kind': 'functional-oracle',
              'statistics_unit': 'independent process session', 'dependencies': {'pyte': '0.8.2', 'wcwidth': '0.2.13'},
              'prior_rust': '/home/ajs/.cache/pb-phase7/baselines/pb-rust-phase5',
              'source_pin': '07af390', 'cases': []}
    source_diff = subprocess.check_output(['git', 'diff', '07af390', '--', 'main.go', 'cmd', 'pkg'],
                                          cwd=HERE.parent)
    if source_diff:
        raise RuntimeError('production Go source does not match pinned 07af390')
    report['production_go_diff_sha256'] = hashlib.sha256(source_diff).hexdigest()
    report['harness_sources'] = {str(path.relative_to(HERE.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (Path(__file__), HERE / 'phase7_workloads.py', HERE / 'phase7_spawn.c',
                                              HERE / 'phase3.py', HERE / 'phase2.py')}
    report['prior_rust_identity'] = phase1.binary_identity(Path(report['prior_rust']))
    for case in cases:
        reference = args.cloud_reference if case.get('build') == 'cloud' else args.reference
        ref = sample(reference, case, args.out / case['name'] / 'reference')
        issues = validate(ref)
        entry = {'name': case['name'], 'reference_issues': issues, 'termination': ref['termination'],
                 'termios_restored': ref['termios_restored'], 'reference_sha256': ref['binary']['sha256']}
        if args.mode == 'compare':
            candidate = args.cloud_candidate if case.get('build') == 'cloud' else args.candidate
            if candidate is None:
                entry['issues'] = ['cloud candidate missing']
            else:
                actual = sample(candidate, case, args.out / case['name'] / 'candidate')
                entry['issues'] = compare(ref, actual)
        entry['documentary'] = bool(case.get('documentary'))
        report['cases'].append(entry)
        print(case['name'], 'issues=' + str(entry.get('issues', issues)), flush=True)
        (args.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return int(any(e.get('issues', e['reference_issues']) for e in report['cases'] if not e['documentary']))


if __name__ == '__main__':
    raise SystemExit(main())
