#!/usr/bin/env python3
"""Phase 7 ROOT-only bounded execution driver (r1).

Runs ONE command so that the whole driver lifetime -- its own preparation, the
command, graceful interruption, forced cleanup, reaping, end checks, lock check
and report output -- ends strictly before a launch-anchored limit (<= 1800 s).
Design, guarantees and limitations: docs/phase7-bounded-execution.md.

Process ownership, in brief:
  * The driver is a child subreaper (PR_SET_CHILD_SUBREAPER), so every orphaned
    descendant (dead supervisor's CLI, setsid fixture, double-forked helper)
    becomes the driver's own child instead of escaping to init/systemd.
  * The command runs as a fresh session/group leader with PDEATHSIG=SIGKILL. Its
    pidfd is held and it is NOT reaped (exit observed with WNOWAIT) until all
    cleanup is done, so its PID and process group cannot be recycled meanwhile.
  * Signals go only (a) through that pidfd, optionally with the pidfd process-group
    flag, or (b) through a pidfd opened on one of the driver's own still-unreaped
    children. Only the driver reaps its children, so neither target can be a
    recycled PID. No /proc scan, no numeric killpg, no kill(-1), no name matching.
"""
import argparse
import ctypes
import errno
import faulthandler
import hashlib
import json
import math
import os
import re
import select
import signal
import stat
import sys
import time
from pathlib import Path

SCHEMA = 'pb-phase7-bounded-execution/1'
ABSOLUTE_LIMIT = 1800.0  # User rule: every run strictly under 1,800 s. Never relaxed here.
DEFAULTS = {'limit': 1800.0, 'launch_by': 30.0, 'graceful_at': 1710.0, 'kill_at': 1735.0,
            'reap_by': 1755.0, 'finalize_by': 1770.0, 'hard_exit_at': 1780.0, 'interrupt_grace': 20.0}
ORDER = ('launch_by', 'graceful_at', 'kill_at', 'reap_by', 'finalize_by', 'hard_exit_at', 'limit')
# 1 is deliberately unused: it means a Python crash or the faulthandler watchdog backstop.
EXIT_CODES = {'ok': 0, 'child_failed': 2, 'leaked': 3, 'cleanup_unverified': 70, 'backstop': 71,
              'late': 72, 'identity_drift': 73, 'deadline': 124, 'precondition_failed': 125,
              'exec_failed': 127, 'interrupted': 130}
PRECEDENCE = ('cleanup_unverified', 'late', 'identity_drift', 'precondition_failed', 'interrupted',
              'deadline', 'exec_failed', 'leaked', 'child_failed', 'ok')
PR_SET_PDEATHSIG, PR_SET_CHILD_SUBREAPER, PR_GET_CHILD_SUBREAPER = 1, 36, 37
PIDFD_SIGNAL_PROCESS_GROUP = 1 << 2  # Linux 6.9+; same flag phase7.NativeChild uses.
EXTERNAL_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
CHILD_DEFAULT_SIGNALS = tuple(getattr(signal, name) for name in
    ('SIGINT', 'SIGTERM', 'SIGHUP', 'SIGQUIT', 'SIGPIPE', 'SIGXFSZ', 'SIGXFZ', 'SIGCHLD', 'SIGALRM',
     'SIGUSR1', 'SIGUSR2', 'SIGTSTP', 'SIGTTIN', 'SIGTTOU') if hasattr(signal, name))
ENV_RECORDED = ('PATH', 'TMPDIR', 'GOTMPDIR', 'CLAUDE_CODE_TMPDIR', 'HOME', 'PYTHONPATH', 'LANG', 'LC_ALL')
CLK_TCK = os.sysconf('SC_CLK_TCK')
READ_CAP = 4 << 20
LIBC = ctypes.CDLL(None, use_errno=True)


def boottime():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


class Clock:
    """Seconds since the anchor on CLOCK_BOOTTIME (monotonic, counts suspend)."""

    def __init__(self, anchor):
        self.anchor = anchor

    def now(self):
        return boottime() - self.anchor


def proc_stat(pid):
    with open('/proc/%s/stat' % pid, 'rb') as handle:
        data = handle.read(4096)
    head, tail = data.rsplit(b')', 1)
    fields = tail.split()
    return {'comm': head.split(b'(', 1)[1].decode('utf-8', 'backslashreplace'), 'state': fields[0].decode(),
            'ppid': int(fields[1]), 'pgid': int(fields[2]), 'sid': int(fields[3]), 'start_ticks': int(fields[19])}


def proc_cmdline(pid):
    try:
        with open('/proc/%d/cmdline' % pid, 'rb') as handle:
            data = handle.read(8193)
    except OSError as error:
        return {'error': str(error)}
    parts = data[:8192].split(b'\0')
    if parts and parts[-1] == b'':
        parts.pop()
    return {'argv': [os.fsdecode(part) for part in parts], 'truncated': len(data) > 8192}


def read_bounded(path):
    with open(path, 'rb') as handle:
        data = handle.read(READ_CAP + 1)
    if len(data) > READ_CAP:
        raise OSError(errno.EFBIG, 'bounded read exceeded', str(path))
    return data.decode('utf-8', 'backslashreplace')


def open_regular(path):
    """O_NONBLOCK so a FIFO/device can never block preparation; regular files only."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise OSError(errno.EINVAL, 'not a regular file', str(path))
    return fd, info


def sha256_file(path):
    digest, size = hashlib.sha256(), 0
    fd, info = open_regular(path)
    with open(fd, 'rb') as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    return {'path': str(path), 'realpath': os.path.realpath(path), 'sha256': digest.hexdigest(),
            'size': size, 'mtime_ns': info.st_mtime_ns}


def own_children():
    """Direct children of every driver thread (orphans may be adopted by any thread)."""
    base = '/proc/%d/task' % os.getpid()
    kids = set()
    for tid in os.listdir(base):
        try:
            with open('%s/%s/children' % (base, tid), 'rb') as handle:
                kids.update(int(value) for value in handle.read().split())
        except FileNotFoundError:
            continue
    return kids


def lock_state(path):
    """Passive flock observation via /proc/locks; never acquires, never creates."""
    try:
        fd, info = open_regular(path)
    except FileNotFoundError:
        return {'path': str(path), 'state': 'absent', 'entries': []}
    try:
        fdinfo = read_bounded('/proc/self/fdinfo/%d' % fd)
    finally:
        os.close(fd)  # Closing a separate description never drops another holder's flock.
    match = re.search(r'^mnt_id:\s*(\d+)\s*$', fdinfo, re.M)
    if not match:
        raise OSError(errno.ENOTSUP, 'fdinfo mnt_id unavailable')
    device = None
    for line in read_bounded('/proc/self/mountinfo').splitlines():
        fields = line.split()
        if fields and fields[0] == match.group(1):
            device = tuple(int(part) for part in fields[2].split(':'))
    if device is None:
        raise OSError(errno.ENOENT, 'mount of lock file not found in mountinfo')
    entries = []
    for line in read_bounded('/proc/locks').splitlines():
        tokens = line.split()
        blocked = len(tokens) > 1 and tokens[1] == '->'
        if blocked:
            tokens = tokens[:1] + tokens[2:]
        if len(tokens) < 6 or tokens[5].count(':') != 2:
            continue
        major, minor, inode = tokens[5].split(':')
        try:
            same = (int(major, 16), int(minor, 16)) == device and int(inode) == info.st_ino
        except ValueError:
            continue
        if same:
            entries.append({'class': tokens[1], 'mode': tokens[2], 'type': tokens[3], 'pid': int(tokens[4]),
                            'blocked_waiter': blocked, 'raw': line})
    held = any(not entry['blocked_waiter'] for entry in entries)
    return {'path': str(path), 'state': 'held' if held else 'free', 'device': '%d:%d' % device,
            'inode': info.st_ino, 'entries': entries}


def validate(params):
    for key, value in params.items():
        if not isinstance(value, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('%s must be a finite positive number' % key)
    if params['limit'] > ABSOLUTE_LIMIT:
        raise ValueError('limit %.3f exceeds the absolute %.0f s rule' % (params['limit'], ABSOLUTE_LIMIT))
    values = [params[key] for key in ORDER]
    if any(left >= right for left, right in zip(values, values[1:])):
        raise ValueError('required strict order: ' + ' < '.join(ORDER))


def watchdog_at(params):
    return params['hard_exit_at'] + (params['limit'] - params['hard_exit_at']) / 2


def arm_alarm_backstop(clock, hard_exit_at, events_fd=-1):
    """SIGALRM at hard_exit_at: best-effort marker, then _exit(71). Not inherited by fork."""
    def fire(number, frame):
        if events_fd >= 0:
            try:
                os.write(events_fd, ('{"event": "backstop", "t": %.6f}\n' % clock.now()).encode())
            except OSError:
                pass
        os._exit(EXIT_CODES['backstop'])
    delay = hard_exit_at - clock.now()
    if not math.isfinite(delay):
        raise ValueError('non-finite backstop delay')
    signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, max(1e-3, delay))


def arm_thread_backstop(clock, at, file):
    """faulthandler watchdog thread: dumps tracebacks then _exit(1) even if the main thread is stuck."""
    faulthandler.dump_traceback_later(max(1e-3, at - clock.now()), exit=True, file=file)


def decode_status(status):
    if os.WIFEXITED(status):
        return {'kind': 'exited', 'code': os.WEXITSTATUS(status), 'raw': status}
    if os.WIFSIGNALED(status):
        return {'kind': 'signaled', 'signal': os.WTERMSIG(status), 'core': os.WCOREDUMP(status), 'raw': status}
    return {'kind': 'other', 'raw': status}


def decode_waitid(info):
    kind = {os.CLD_EXITED: 'exited', os.CLD_KILLED: 'signaled', os.CLD_DUMPED: 'dumped'}.get(info.si_code, 'other')
    return {'kind': kind, 'si_code': info.si_code, 'si_status': info.si_status, 'si_pid': info.si_pid}


class Driver:
    def __init__(self, params, command, out, cwd, lock_path, require_lock_free, hashes, clock, anchor_info):
        self.params, self.command, self.out, self.cwd = params, command, out, cwd
        self.lock_path, self.require_lock_free, self.hash_paths = lock_path, require_lock_free, hashes
        self.clock = clock
        self.pending, self.interrupts, self.reasons, self.issues = [], [], [], []
        self.descendants, self.live = [], {}
        self.child = self.pidfd = self.top_exit = self.top_final = None
        self.go_sent = False
        self.top_natural_exit = False
        self.events_fd = -1
        self.wake_r = self.wake_w = -1
        self.timeline = {}
        self.report = {'schema': SCHEMA, 'status': 'running', 'parameters': dict(params),
                       'watchdog_at': watchdog_at(params), 'clock': anchor_info, 'issues': self.issues,
                       'interrupts': self.interrupts, 'descendants': self.descendants, 'timeline': self.timeline}

    # ---- evidence -------------------------------------------------------
    def event(self, name, /, **fields):
        record = {**fields, 'event': name, 't': round(self.clock.now(), 6)}
        if self.events_fd < 0:
            return record
        data = (json.dumps(record, sort_keys=True) + '\n').encode()
        try:
            while data:
                data = data[os.write(self.events_fd, data):]
        except OSError as error:
            self.issues.append('events write failed: ' + str(error))
            self.events_fd = -1
        return record

    def write_report(self, durable=False):
        path, temporary = self.out / 'report.json', self.out / 'report.json.tmp'
        try:
            with open(temporary, 'w') as handle:
                handle.write(json.dumps(self.report, indent=2, sort_keys=True) + '\n')
                if durable:
                    handle.flush()
                    os.fsync(handle.fileno())
            os.replace(temporary, path)
            if durable:
                directory = os.open(self.out, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except OSError as error:
            self.issues.append('report write failed: ' + str(error))

    # ---- signals --------------------------------------------------------
    def install_signals(self):
        def disposition(handler):
            return handler.name if isinstance(handler, signal.Handlers) else 'handler:' + repr(handler)
        inherited = {signal.Signals(number).name: disposition(signal.getsignal(number))
                     for number in (*EXTERNAL_SIGNALS, signal.SIGCHLD, signal.SIGPIPE, signal.SIGQUIT)}
        blocked = sorted(signal.Signals(number).name for number in signal.pthread_sigmask(signal.SIG_BLOCK, ()))
        self.wake_r, self.wake_w = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        signal.set_wakeup_fd(self.wake_w, warn_on_full_buffer=False)
        for number in EXTERNAL_SIGNALS:
            signal.signal(number, self.on_signal)
        # A Python handler (never SIG_IGN/SA_NOCLDWAIT) so the kernel never auto-reaps:
        # only this driver reaps, which is what makes its own-child pidfds reuse-safe.
        signal.signal(signal.SIGCHLD, lambda number, frame: None)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, (*EXTERNAL_SIGNALS, signal.SIGCHLD, signal.SIGALRM))
        self.report['signals'] = {'inherited_dispositions': inherited, 'inherited_blocked': blocked,
                                  'driver_policy': 'INT/TERM/HUP -> graceful then bounded escalation; '
                                                   'SIGCHLD handled (no auto-reap); SIGALRM backstop'}

    def on_signal(self, number, frame):
        self.pending.append((number, self.clock.now()))

    def take_interrupts(self, phase):
        new = 0
        while self.pending:
            number, at = self.pending.pop(0)
            self.interrupts.append({'signal': signal.Signals(number).name, 'at': round(at, 6), 'phase': phase})
            self.event('external_signal', signal=signal.Signals(number).name, phase=phase)
            new += 1
        return new

    def drain_wake(self):
        try:
            while os.read(self.wake_r, 4096):
                pass
        except BlockingIOError:
            pass

    def poll(self, fds, until, cap=None):
        """Wait for any fd or a signal; never beyond `until` (driver-relative seconds)."""
        remaining = until - self.clock.now()
        if cap is not None:
            remaining = min(remaining, cap)
        if remaining <= 0:
            return []
        poller = select.poll()
        for fd in (*fds, self.wake_r):
            poller.register(fd, select.POLLIN)
        ready = poller.poll(math.ceil(remaining * 1000))
        self.drain_wake()
        return [fd for fd, _ in ready if fd != self.wake_r]

    # ---- preparation ----------------------------------------------------
    def check_launch_budget(self, step):
        if self.clock.now() >= self.params['launch_by']:
            raise PreconditionError('preparation exceeded launch_by during ' + step)
        if self.pending:
            raise InterruptedBeforeLaunch()

    def prepare(self):
        caps = self.report['capabilities'] = {}
        if sys.platform != 'linux':
            raise PreconditionError('Linux required')
        for name in ('pidfd_open', 'P_PIDFD', 'waitid', 'WNOWAIT', 'closerange'):
            if not hasattr(os, name):
                raise PreconditionError('os.%s unavailable' % name)
        if not hasattr(signal, 'pidfd_send_signal'):
            raise PreconditionError('signal.pidfd_send_signal unavailable')
        if LIBC.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0):
            raise PreconditionError('PR_SET_CHILD_SUBREAPER failed: errno %d' % ctypes.get_errno())
        value = ctypes.c_int()
        if LIBC.prctl(PR_GET_CHILD_SUBREAPER, ctypes.byref(value), 0, 0, 0) or value.value != 1:
            raise PreconditionError('child subreaper not confirmed')
        caps['child_subreaper'] = True
        try:
            caps['own_children_before_launch'] = sorted(own_children())
        except OSError as error:
            raise PreconditionError('/proc/<pid>/task/<tid>/children unavailable: %s' % error)
        if caps['own_children_before_launch']:
            raise PreconditionError('driver already has children before launch')
        self.report['command'] = {'argv': self.command, 'argv_hex': [os.fsencode(arg).hex() for arg in self.command],
                                  'cwd': str(self.cwd), 'stdin': os.devnull,
                                  'stdout': str(self.out / 'child.stdout'), 'stderr': str(self.out / 'child.stderr')}
        self.check_launch_budget('capabilities')
        executable = self.command[0]
        if not os.path.isabs(executable):
            raise PreconditionError('argv[0] must be an absolute path')
        try:
            info = os.stat(executable)
        except OSError as error:
            raise PreconditionError('argv[0] not accessible: %s' % error)
        if not stat.S_ISREG(info.st_mode) or not os.access(executable, os.X_OK):
            raise PreconditionError('argv[0] is not an executable regular file')
        if not os.path.isdir(self.cwd):
            raise PreconditionError('cwd is not a directory')
        environment = dict(os.environ)
        canonical = b'\0'.join(os.fsencode(key) + b'=' + os.fsencode(value) for key, value in sorted(environment.items()))
        self.report['environment'] = {'keys': sorted(environment), 'sha256': hashlib.sha256(canonical).hexdigest(),
                                      'recorded': {key: environment.get(key) for key in ENV_RECORDED},
                                      'policy': 'child receives the driver environment unchanged'}
        hashes = self.report['hashes'] = {'pre': [], 'post': []}
        for path in (executable, *self.hash_paths):
            hashes['pre'].append(sha256_file(path))
            self.check_launch_budget('hashing')
        if self.lock_path is not None:
            state = self.report['lock_pre'] = lock_state(self.lock_path)
            if self.require_lock_free and state['state'] == 'held':
                raise PreconditionError('lock held before launch')
        self.check_launch_budget('preparation')

    # ---- launch ---------------------------------------------------------
    def launch(self):
        ready_r, ready_w = os.pipe2(os.O_CLOEXEC)
        go_r, go_w = os.pipe2(os.O_CLOEXEC)
        error_r, error_w = os.pipe2(os.O_CLOEXEC)
        create = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        stdout_fd = os.open(self.out / 'child.stdout', create, 0o644)
        stderr_fd = os.open(self.out / 'child.stderr', create, 0o644)
        null_fd = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        parent, environment, maxfd = os.getpid(), dict(os.environ), os.sysconf('SC_OPEN_MAX')
        self.event('fork')
        pid = os.fork()
        if pid == 0:  # Child: single-threaded parent, no allocation-heavy work, never returns.
            stage = 'signals'
            try:
                signal.set_wakeup_fd(-1)
                for number in CHILD_DEFAULT_SIGNALS:
                    signal.signal(number, signal.SIG_DFL)
                signal.pthread_sigmask(signal.SIG_SETMASK, ())
                stage = 'pdeathsig'
                if LIBC.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0):
                    raise OSError(ctypes.get_errno(), 'prctl')
                stage = 'parent'
                if os.getppid() != parent:
                    raise OSError(errno.ECHILD, 'driver exited before child setup')
                stage = 'setsid'
                os.setsid()
                stage = 'fds'
                os.dup2(null_fd, 0)
                os.dup2(stdout_fd, 1)
                os.dup2(stderr_fd, 2)
                keep = sorted((go_r, error_w, ready_w))
                low = 3
                for fd in keep:
                    os.closerange(low, fd)
                    low = fd + 1
                os.closerange(low, maxfd)
                stage = 'chdir'
                os.chdir(self.cwd)
                stage = 'ready'
                os.write(ready_w, b'R')
                os.close(ready_w)
                stage = 'go'
                if os.read(go_r, 1) != b'G':
                    os._exit(126)
                stage = 'exec'
                os.execve(self.command[0], self.command, environment)
            except BaseException as error:
                try:
                    os.write(error_w, json.dumps({'stage': stage, 'errno': getattr(error, 'errno', None),
                                                  'error': repr(error)[:300]}).encode())
                except BaseException:
                    pass
            os._exit(127)
        self.child = pid
        for fd in (ready_w, go_r, error_w, stdout_fd, stderr_fd, null_fd):
            os.close(fd)
        try:
            return self.release_child(pid, ready_r, go_w, error_r)
        finally:
            for fd in (ready_r, go_w, error_r):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def release_child(self, pid, ready_r, go_w, error_r):
        """Child is our unreaped, not-yet-exec'd child; nothing runs the command until checks pass."""
        self.pidfd = os.pidfd_open(pid)
        while not self.poll([ready_r], self.params['launch_by']):
            if self.pending:
                raise InterruptedBeforeLaunch()
            if self.clock.now() >= self.params['launch_by'] or \
                    os.waitid(os.P_PIDFD, self.pidfd, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                break
        readable = select.select([ready_r], [], [], 0)[0]
        if not readable or os.read(ready_r, 1) != b'R':
            if self.pending:
                raise InterruptedBeforeLaunch()
            detail = self.read_exec_error(error_r)
            raise PreconditionError('child setup did not become ready before launch_by: %s' % detail)
        identity = proc_stat(pid)
        if (identity['ppid'], identity['pgid'], identity['sid']) != (os.getpid(), pid, pid):
            raise PreconditionError('child is not a fresh session leader of the driver: %r' % identity)
        try:
            signal.pidfd_send_signal(self.pidfd, 0, None, PIDFD_SIGNAL_PROCESS_GROUP)
        except OSError as error:
            raise PreconditionError('pidfd process-group signalling unavailable: %s' % error)
        self.report['capabilities']['pidfd_group_signal'] = True
        self.check_launch_budget('child setup')
        self.go_sent = True  # From here on every failure path is the full group/subreaper cleanup.
        os.write(go_w, b'G')
        self.timeline['go_at'] = self.event('go', pid=pid)['t']
        while True:
            if not self.poll([error_r], self.params['graceful_at']):
                if self.clock.now() >= self.params['graceful_at']:
                    self.issues.append('exec confirmation not observed before graceful_at')
                    return 'kill'
                if self.pending:  # The reserved group is signal-safe before and after exec.
                    self.report['child'] = {'pid': pid, 'exec_confirmation': 'abandoned: external signal '
                                            'before exec confirmation; interrupt policy applied by supervise'}
                    self.event('exec_unconfirmed_interrupt', pid=pid)
                    return 'running'
                continue
            data = os.read(error_r, 4096)
            if data:
                try:
                    self.report['exec_error'] = json.loads(data.decode('utf-8', 'replace'))
                except ValueError:
                    self.report['exec_error'] = {'raw': data.decode('utf-8', 'replace')}
                self.event('exec_failed', exec_error=self.report['exec_error'])
                return 'exec_failed'
            break
        self.report['child'] = {'pid': pid}
        try:
            identity = proc_stat(pid)
            self.report['child'].update(start_ticks=identity['start_ticks'], pgid=identity['pgid'],
                                        sid=identity['sid'], comm=identity['comm'], cmdline=proc_cmdline(pid))
        except (OSError, ValueError, IndexError) as error:  # e.g. already exited and dying
            self.report['child']['identity_error'] = str(error)
        self.timeline['exec_confirmed_at'] = self.event('exec_confirmed', **self.report['child'])['t']
        return 'running'

    def read_exec_error(self, error_r):
        try:
            os.set_blocking(error_r, False)
            return os.read(error_r, 4096).decode('utf-8', 'replace') or 'no setup error recorded'
        except OSError as error:
            return str(error)

    # ---- supervision ----------------------------------------------------
    def top_exited(self):
        if self.top_exit is None:
            info = os.waitid(os.P_PIDFD, self.pidfd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if info is not None:
                self.top_exit = decode_waitid(info)
                self.timeline['top_exit_observed_at'] = self.event('top_exited', **self.top_exit)['t']
        return self.top_exit is not None

    def group_signal(self, number, reason):
        try:
            signal.pidfd_send_signal(self.pidfd, number, None, PIDFD_SIGNAL_PROCESS_GROUP)
            self.event('group_signal', signal=signal.Signals(number).name, reason=reason)
            return True
        except ProcessLookupError:
            self.event('group_signal', signal=signal.Signals(number).name, reason=reason, result='ESRCH')
        except OSError as error:
            self.issues.append('group %s failed: %s' % (signal.Signals(number).name, error))
        return False

    def record(self, pid, category):
        """Identity of one own unreaped child; (pid, start_ticks) is unique while unreaped."""
        if pid in self.live:
            return self.live[pid]
        identity = proc_stat(pid)
        entry = {'pid': pid, **identity, 'category': category, 'first_seen_at': round(self.clock.now(), 6),
                 'cmdline': proc_cmdline(pid), 'signals': []}
        self.live[pid] = entry
        self.descendants.append(entry)
        self.event('adopted', pid=pid, start_ticks=identity['start_ticks'], ppid=identity['ppid'],
                   pgid=identity['pgid'], sid=identity['sid'], state=identity['state'], category=category)
        return entry

    def reap_adopted(self, category, kill=False):
        """Record every own child but the command; reap its zombies; optionally SIGKILL live ones."""
        remaining = 0
        for pid in sorted(own_children()):
            if pid == self.child:
                continue
            try:
                entry = self.record(pid, category)
                state = proc_stat(pid)['state']
            except (OSError, ValueError, IndexError) as error:
                self.issues.append('own child %d identity read failed: %s' % (pid, error))
                state = '?'
                entry = None
            if kill and state not in ('Z', 'X') and entry is not None and 'SIGKILL' not in entry['signals']:
                try:
                    fd = os.pidfd_open(pid)  # Safe: own child, unreaped, only we reap.
                    try:
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
                    finally:
                        os.close(fd)
                    entry['signals'].append('SIGKILL')
                    self.event('kill_own_child', pid=pid, start_ticks=entry['start_ticks'])
                except ProcessLookupError:
                    pass
                except OSError as error:
                    self.issues.append('kill own child %d failed: %s' % (pid, error))
            info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG)
            if info is None:
                remaining += 1
                continue
            if entry is not None:
                entry['reaped_at'] = round(self.clock.now(), 6)
                entry['exit'] = decode_waitid(info)
                self.event('reaped_own_child', pid=pid, start_ticks=entry['start_ticks'], **entry['exit'])
            self.live.pop(pid, None)
        return remaining

    def supervise(self):
        p = self.params
        phase, kill_deadline, cause = 'running', None, None
        while True:
            new = self.take_interrupts(phase)
            if self.top_exited():
                self.top_natural_exit = True
                return None
            self.reap_adopted('adopted_during_run')
            now = self.clock.now()
            if new:
                if len(self.interrupts) >= 2:
                    return 'second_interrupt'
                if phase == 'running':
                    self.graceful('external ' + self.interrupts[-1]['signal'])
                    phase, cause = 'graceful', 'interrupt_grace'
                    kill_deadline = min(now + p['interrupt_grace'], p['kill_at'])
                elif now + p['interrupt_grace'] < kill_deadline:
                    kill_deadline, cause = now + p['interrupt_grace'], 'interrupt_grace'
            if phase == 'running' and now >= p['graceful_at']:
                self.graceful('deadline graceful_at')
                phase, cause, kill_deadline = 'graceful', 'deadline', p['kill_at']
                self.reasons.append('deadline')
            if phase == 'graceful' and now >= kill_deadline:
                return cause
            self.poll([self.pidfd], p['graceful_at'] if phase == 'running' else kill_deadline)

    def graceful(self, reason):
        # Ctrl-C delivery set: the command's own process group (not its setsid descendants).
        self.timeline.setdefault('graceful_signal_at', round(self.clock.now(), 6))
        self.group_signal(signal.SIGINT, reason)
        self.report['status'] = 'running: graceful SIGINT sent (%s)' % reason
        self.write_report()

    # ---- cleanup --------------------------------------------------------
    def cleanup(self, kill_cause):
        """Bounded by reap_by. Record survivors, SIGKILL the reserved group, then own children
        top-down until no child but the reserved command remains; reap the command last."""
        p = self.params
        if kill_cause is not None:
            self.timeline['kill_sent_at'] = self.event('kill', cause=kill_cause)['t']
            self.report['status'] = 'running: SIGKILL (%s)' % kill_cause
            self.write_report()
        category = 'remaining_after_exit' if self.top_natural_exit else 'present_at_kill'
        for pid in sorted(own_children()):
            if pid != self.child:
                try:
                    entry = self.record(pid, category)
                    if self.top_natural_exit and proc_stat(pid)['state'] not in ('Z', 'X'):
                        entry['alive_after_command_exit'] = True  # A leak, whenever it was adopted.
                except (OSError, ValueError, IndexError) as error:
                    self.issues.append('own child %d identity read failed: %s' % (pid, error))
        self.group_signal(signal.SIGKILL, 'cleanup of reserved group')
        if not self.top_exited():
            try:
                signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
        converged = False
        while True:
            self.take_interrupts('cleanup')
            remaining = self.reap_adopted('adopted_during_cleanup', kill=True)
            # Re-list after reaping: a child reaped above may have handed us its children.
            if self.top_exited() and remaining == 0 and not own_children() - {self.child}:
                converged = True
                break
            if self.clock.now() >= p['reap_by']:
                break
            self.poll([], p['reap_by'], cap=0.05)
        cleanup = self.report['cleanup'] = {'converged_before_reap_by': converged}
        if not converged:
            self.issues.append('cleanup did not converge before reap_by')
        if self.top_exit is not None:
            pid, status, usage = os.wait4(self.child, os.WNOHANG)
            if pid == self.child:
                self.top_final = decode_status(status)
                self.report.setdefault('child', {'pid': self.child})['exit'] = self.top_final
                self.report['child']['rusage_including_waited_descendants'] = {
                    'ru_utime': usage.ru_utime, 'ru_stime': usage.ru_stime, 'ru_maxrss_kib': usage.ru_maxrss}
                self.timeline['top_reaped_at'] = self.event('top_reaped', **self.top_final)['t']
            else:
                self.issues.append('command exit observed but wait4 returned nothing')
        self.verify_empty()
        return cleanup

    def verify_empty(self):
        cleanup = self.report.setdefault('cleanup', {})
        try:
            leftover = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            cleanup['echild_verified'] = False
            cleanup['own_children_after'] = sorted(own_children())
            cleanup['waitable_after'] = None if leftover is None else decode_waitid(leftover)
            self.issues.append('driver still has children after cleanup')
        except ChildProcessError:
            cleanup['echild_verified'] = True
            cleanup['own_children_after'] = []
        unreaped = [entry['pid'] for entry in self.descendants if 'exit' not in entry]
        cleanup['recorded_descendants'] = len(self.descendants)
        cleanup['recorded_unreaped'] = unreaped
        if unreaped and cleanup['echild_verified']:
            self.issues.append('recorded descendants without a reap record: %r' % unreaped)
        self.timeline['cleanup_verified_at'] = self.event('cleanup_checked', **cleanup)['t']

    def abort_unlaunched(self):
        """Command never exec'd; the setup child is our unreaped child, SIGKILL then reap bounded."""
        if self.child is not None:
            try:
                os.kill(self.child, signal.SIGKILL)  # Safe: own, unreaped, single reaper.
            except ProcessLookupError:
                pass
        while self.child is not None and self.clock.now() < self.params['reap_by']:
            pid, status = os.waitpid(self.child, os.WNOHANG)
            if pid:
                self.report['setup_child_exit'] = decode_status(status)
                self.child = None
                break
            self.poll([], self.params['reap_by'], cap=0.02)
        if self.child is not None:
            self.issues.append('aborted setup child not reaped before reap_by')
        self.verify_empty()

    # ---- end checks ------------------------------------------------------
    def end_checks(self):
        if self.lock_path is not None:
            try:
                state = self.report['lock_post'] = lock_state(self.lock_path)
                if state['state'] == 'held' and self.go_sent:  # Unlaunched: not attributable to us.
                    self.issues.append('lock still held after verified cleanup')
            except OSError as error:
                self.issues.append('post lock check failed: %s' % error)
        hashes = self.report.get('hashes')
        if hashes:
            cutoff = (self.params['reap_by'] + self.params['finalize_by']) / 2
            for before in hashes['pre']:
                if self.clock.now() >= cutoff:
                    hashes['post'].append({'path': before['path'], 'skipped': 'finalize budget'})
                    self.issues.append('post hash skipped (finalize budget): ' + before['path'])
                    continue
                try:
                    after = sha256_file(before['path'])
                except OSError as error:
                    after = {'path': before['path'], 'error': str(error)}
                hashes['post'].append(after)
                if after.get('sha256') != before['sha256']:
                    self.reasons.append('identity_drift')

    def finish(self):
        """Decide, write, then re-decide: the PROCESS EXIT CODE is authoritative. report.json cannot
        record its own write/fsync completion; anything failing or late from here on (final event,
        report write+fsync, events fsync, summary print, completion at/after finalize_by) turns the
        exit code into 70/72 even though the written report may say otherwise."""
        self.take_interrupts('end_checks')  # Every pending signal is recorded before the decision.
        if self.issues:
            self.reasons.append('cleanup_unverified')
        if self.top_final is not None:
            if self.top_final['kind'] != 'exited' or self.top_final['code'] != 0:
                self.reasons.append('child_failed')
        if any(entry.get('alive_after_command_exit') for entry in self.descendants):
            self.reasons.append('leaked')
        if self.interrupts:
            self.reasons.append('interrupted')
        elapsed = self.clock.now()
        timing = self.report['timing'] = {
            'decision_elapsed': round(elapsed, 6), 'limit': self.params['limit'],
            'finalize_by': self.params['finalize_by'],
            'decision_within_finalize_by': elapsed < self.params['finalize_by'],
            'decision_strictly_under_limit': elapsed < self.params['limit'],
            'scope': 'measured at the decision, before this report is written; completion time and the '
                     'authoritative outcome are the process exit code and the stdout summary'}
        if not timing['decision_within_finalize_by']:
            self.reasons.append('late')
        verdict = next(name for name in PRECEDENCE if name in self.reasons or name == 'ok')
        self.report.update(status='final', verdict=verdict, reasons=sorted(set(self.reasons)),
                           exit_code=EXIT_CODES[verdict],
                           decision_scope='verdict/exit_code decided before this report\'s own write+fsync; '
                                          'the process exit code is authoritative: a different exit code '
                                          '(70 write/fsync/event/print failure, 72 completion at/after '
                                          'finalize_by, 71/1 backstop) means this report is unverified')
        self.report['process_cleanup_state'] = self.cleanup_summary()
        before = len(self.issues)
        self.event('final', verdict=verdict, exit_code=EXIT_CODES[verdict])
        self.write_report(durable=True)
        if self.events_fd >= 0:
            try:
                os.fsync(self.events_fd)
            except OSError as error:
                self.issues.append('events fsync failed: ' + str(error))
        actual = self.reassess(verdict, before)
        summary = {'verdict': actual, 'exit_code': EXIT_CODES[actual], 'authoritative': 'process exit code',
                   'report_verdict': verdict, 'report_exit_code': EXIT_CODES[verdict], 'out': str(self.out),
                   'completion_elapsed': round(self.clock.now(), 6), 'limit': self.params['limit'],
                   'finalize_by': self.params['finalize_by'], 'post_decision_issues': self.issues[before:],
                   'process_cleanup_state': self.report['process_cleanup_state']}
        try:
            print(json.dumps(summary, sort_keys=True), flush=True)
        except (OSError, ValueError) as error:  # Caller's stdout gone.
            self.issues.append('summary print failed: ' + str(error))
        self.take_interrupts('post_decision')  # Events only; the run's outcome is already decided.
        actual = self.reassess(actual, before)
        # Best-effort marker without fsync; if it fails (or completion crosses finalize_by while
        # writing it) the returned code worsens again and the exit code remains authoritative.
        self.event('exit', verdict=actual, exit_code=EXIT_CODES[actual], post_decision_issues=self.issues[before:])
        return EXIT_CODES[self.reassess(actual, before)]

    def reassess(self, verdict, before):
        """Post-decision failures can only make the outcome worse (PRECEDENCE order)."""
        candidates = {verdict}
        if len(self.issues) > before:
            candidates.add('cleanup_unverified')
        if self.clock.now() >= self.params['finalize_by']:
            candidates.add('late')
        return next(name for name in PRECEDENCE if name in candidates)

    def cleanup_summary(self):
        cleanup = self.report.get('cleanup', {})
        return {'echild_verified': cleanup.get('echild_verified', False),
                'command_reaped': self.child is None or self.top_final is not None,
                'recorded_descendants': len(self.descendants),
                'recorded_unreaped': cleanup.get('recorded_unreaped', []),
                'lock_post': self.report.get('lock_post', {}).get('state'),
                'issues': list(self.issues)}


class PreconditionError(Exception):
    pass


class InterruptedBeforeLaunch(Exception):
    pass


def parse(argv):
    parser = argparse.ArgumentParser(prog='bounded-execution.py', description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='mode', required=True)
    run = sub.add_parser('run', help='run ONE command under the launch-anchored bound')
    run.add_argument('--out', required=True, help='new evidence directory (must not exist; parent must)')
    run.add_argument('--cwd', default=None, help='command working directory (default: driver cwd)')
    for key, value in DEFAULTS.items():
        run.add_argument('--' + key.replace('_', '-'), type=float, default=value, dest=key,
                         help='seconds from the anchor (default %g)' % value)
    run.add_argument('--anchor-boottime', type=float, default=None,
                     help='earlier CLOCK_BOOTTIME anchor (coordinator launch); default: driver process start')
    run.add_argument('--lock-path', default=None, help='flock file observed (never acquired) before/after')
    run.add_argument('--require-lock-free', action='store_true', help='refuse to launch if --lock-path is held')
    run.add_argument('--hash', action='append', default=[], help='file hashed before launch and after cleanup')
    run.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == '--':
        args.command = args.command[1:]
    if not args.command:
        parser.error('command required after --')
    if args.require_lock_free and args.lock_path is None:
        parser.error('--require-lock-free needs --lock-path')
    return args


def main(argv=None):
    # Anchor first: the driver's own process start (clock ticks since boot, floor -> conservative).
    driver_start = proc_stat(os.getpid())['start_ticks'] / CLK_TCK
    raw_argv = list(sys.argv if argv is None else ['bounded-execution.py', *argv])
    try:
        args = parse(sys.argv[1:] if argv is None else argv)
    except SystemExit as error:  # argparse's 2 would collide with child_failed.
        return 0 if error.code in (0, None) else EXIT_CODES['precondition_failed']
    params = {key: float(getattr(args, key)) for key in DEFAULTS}
    try:
        validate(params)
    except ValueError as error:
        print('bounded-execution: invalid parameters: %s' % error, file=sys.stderr)
        return EXIT_CODES['precondition_failed']
    anchor, source = driver_start, 'driver process start (/proc/self/stat starttime, CLOCK_BOOTTIME ticks)'
    if args.anchor_boottime is not None:
        if not math.isfinite(args.anchor_boottime):
            print('bounded-execution: --anchor-boottime must be finite', file=sys.stderr)
            return EXIT_CODES['precondition_failed']
        if args.anchor_boottime > driver_start + 1.0 / CLK_TCK:
            print('bounded-execution: --anchor-boottime is after driver start', file=sys.stderr)
            return EXIT_CODES['precondition_failed']
        if args.anchor_boottime < driver_start - params['launch_by']:
            print('bounded-execution: --anchor-boottime is older than launch_by before driver start',
                  file=sys.stderr)
            return EXIT_CODES['precondition_failed']
        anchor, source = min(args.anchor_boottime, driver_start), 'coordinator --anchor-boottime'
    clock = Clock(anchor)
    arm_alarm_backstop(clock, params['hard_exit_at'])
    anchor_info = {'source': source, 'anchor_boottime': anchor, 'driver_start_boottime': driver_start,
                   'clk_tck': CLK_TCK, 'anchor_unix_estimate': time.time() - clock.now(),
                   'driver_start_elapsed': round(driver_start - anchor, 6),
                   'clock': 'CLOCK_BOOTTIME (monotonic; includes suspend; conservative)'}
    out = Path(args.out).absolute()
    try:
        out.mkdir(mode=0o755)
    except OSError as error:
        print('bounded-execution: cannot create new --out %s: %s' % (out, error), file=sys.stderr)
        return EXIT_CODES['precondition_failed']
    cwd = Path(args.cwd).absolute() if args.cwd else Path.cwd()
    driver = Driver(params, list(args.command), out, cwd, args.lock_path, args.require_lock_free, args.hash,
                    clock, anchor_info)
    driver.events_fd = os.open(out / 'events.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_CLOEXEC, 0o644)
    arm_alarm_backstop(clock, params['hard_exit_at'], driver.events_fd)
    driver.report['driver'] = {'pid': os.getpid(), 'start_ticks': proc_stat(os.getpid())['start_ticks'],
                               'argv': raw_argv, 'argv_hex': [os.fsencode(arg).hex() for arg in raw_argv],
                               'script': sha256_file(os.path.abspath(__file__)), 'python': sys.executable,
                               'python_version': sys.version, 'kernel': os.uname().release}
    driver.report['logs'] = {'events': str(out / 'events.jsonl'), 'report': str(out / 'report.json'),
                             'watchdog': str(out / 'watchdog.txt')}
    driver.install_signals()
    driver.event('start', **{key: value for key, value in anchor_info.items() if key != 'source'})
    driver.write_report()
    kill_cause = None
    try:
        driver.prepare()
        outcome = driver.launch()
    except Exception as error:
        driver.take_interrupts('preparation')
        if isinstance(error, InterruptedBeforeLaunch):
            driver.event('interrupted_before_launch')
        else:
            driver.report['precondition_error'] = repr(error)
            driver.event('precondition_failed', error=repr(error))
            driver.reasons.append('precondition_failed')
        driver.report['launched'] = driver.go_sent
        if driver.go_sent:  # e.g. an OSError while confirming exec: the command may be running.
            driver.issues.append('launch fault after go: %r' % error)
            driver.cleanup('launch_fault')
        else:
            driver.abort_unlaunched()
        driver.end_checks()
        return driver.finish()
    # The thread watchdog starts only after fork (no fork with threads).
    watchdog = open(out / 'watchdog.txt', 'w')
    arm_thread_backstop(clock, watchdog_at(params), watchdog)
    driver.report['launched'] = outcome != 'exec_failed'
    driver.write_report()
    try:
        if outcome == 'exec_failed':
            driver.reasons.append('exec_failed')
            kill_cause = None
        elif outcome == 'kill':
            kill_cause = 'exec_unconfirmed'
        else:
            kill_cause = driver.supervise()
        driver.cleanup(kill_cause)
    except Exception as error:  # Any driver fault: still try the bounded kill path once.
        driver.issues.append('driver fault: %r' % error)
        try:
            driver.cleanup('driver_fault')
        except Exception as second:
            driver.issues.append('cleanup fault: %r' % second)
    if kill_cause in ('interrupt_grace', 'second_interrupt'):
        driver.reasons.append('interrupted')
    driver.end_checks()
    code = driver.finish()
    faulthandler.cancel_dump_traceback_later()
    watchdog.close()
    return code


if __name__ == '__main__':
    status = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)  # No unbounded interpreter teardown after the final report.
