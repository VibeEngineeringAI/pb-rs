#!/usr/bin/env python3
"""Native tests for bounded-execution.py using only tiny self-owned Python control processes.

No product, Go, fixture, phase7 helper, probe, compiler, build, profiler or
common lock is touched. Every control process sleeps (no busy work) and bounds
its own life to 60 s even if a driver bug leaked it. This test process is a
child subreaper too, so anything a broken driver leaked becomes this process's
own unreaped child: tearDown records, SIGKILLs (pidfd on own child) and reaps
it, and fails the test.

Run: python3 -I docs/evidence/phase7/bounded-execution-test.py -v
Work: $PB_BOUNDED_TEST_DIR or /home/ajs/.cache/pb-phase7/bounded-execution-r2/tests
"""
import contextlib
import ctypes
import fcntl
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

DRIVER = Path(__file__).resolve().with_name('bounded-execution.py')
BASE = Path(os.environ.get('PB_BOUNDED_TEST_DIR', '/home/ajs/.cache/pb-phase7/bounded-execution-r2/tests'))
COMMON_LOCK = Path('/home/ajs/.cache/pb-rs-performance.lock')
FAST = {'limit': 8.0, 'launch_by': 2.0, 'graceful_at': 2.5, 'kill_at': 3.5, 'reap_by': 4.5,
        'finalize_by': 5.5, 'hard_exit_at': 6.5, 'interrupt_grace': 0.6}
LIBC = ctypes.CDLL(None, use_errno=True)
RESULTS = []

PRELUDE = r'''
import json, os, signal, sys, time
WORK = sys.argv[1]
IDS = os.path.join(WORK, 'ids.jsonl')
def me(tag):
    with open('/proc/self/stat', 'rb') as h:
        start = int(h.read().rsplit(b')', 1)[1].split()[19])
    with open(IDS, 'a') as h:
        h.write(json.dumps({'tag': tag, 'pid': os.getpid(), 'start': start}) + '\n')
def mark(name):
    open(os.path.join(WORK, name), 'w').close()
def count_ids():
    try:
        with open(IDS) as h:
            return sum(1 for _ in h)
    except FileNotFoundError:
        return 0
def wait_ids(n):
    for _ in range(500):
        if count_ids() >= n:
            return
        time.sleep(0.01)
    sys.exit(9)
'''


def module():
    spec = importlib.util.spec_from_file_location('bounded_execution', DRIVER)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def own_children():
    kids = set()
    base = '/proc/%d/task' % os.getpid()
    for tid in os.listdir(base):
        try:
            kids.update(int(v) for v in Path(base, tid, 'children').read_text().split())
        except FileNotFoundError:
            pass
    return kids


def start_ticks(pid):
    try:
        return int(Path('/proc/%d/stat' % pid).read_bytes().rsplit(b')', 1)[1].split()[19])
    except (OSError, IndexError):
        return None


def setUpModule():
    BASE.mkdir(parents=True, exist_ok=True)
    assert LIBC.prctl(36, 1, 0, 0, 0) == 0, 'test subreaper unavailable'
    assert not own_children(), 'test process must start without children'


def tearDownModule():
    path = BASE / ('results-%d-%d.json' % (int(time.time()), os.getpid()))
    path.write_text(json.dumps({'driver': str(DRIVER), 'results': RESULTS}, indent=2, sort_keys=True) + '\n')
    print('\nresults:', path, file=sys.stderr)


class BoundedExecutionTest(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=BASE))

    def tearDown(self):
        leaked = []
        for pid in sorted(own_children()):
            leaked.append({'pid': pid, 'start': start_ticks(pid)})
            try:
                fd = os.pidfd_open(pid)  # Own unreaped child: reuse-safe.
                signal.pidfd_send_signal(fd, signal.SIGKILL)
                os.close(fd)
            except OSError:
                pass
            for _ in range(200):
                if os.waitpid(pid, os.WNOHANG)[0]:
                    break
                time.sleep(0.01)
        self.assertEqual(leaked, [], 'driver leaked processes to the test subreaper')

    # ---- helpers ---------------------------------------------------------
    def program(self, body, name='prog.py'):
        path = self.work / name
        path.write_text(PRELUDE + body)
        return [sys.executable, '-I', str(path), str(self.work)]

    def run_driver(self, command, profile=FAST, extra=(), wrapper=None, during=None, out=None):
        out = out or self.work / 'out'
        argv = [sys.executable, '-I', str(DRIVER), 'run', '--out', str(out)]
        for key, value in profile.items():
            argv += ['--' + key.replace('_', '-'), repr(value)]
        argv += [*extra, '--', *command]
        if wrapper:
            argv = [sys.executable, '-I', '-c', wrapper, *argv]
        started = time.monotonic()
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if during:
            during(process)
        try:
            stdout, stderr = process.communicate(timeout=profile['limit'] + 5)
        except subprocess.TimeoutExpired:
            process.kill()  # Own unreaped child: safe.
            process.communicate()
            self.fail('driver exceeded limit+5 s')
        elapsed = time.monotonic() - started
        lines = stdout.decode().strip().splitlines()
        summary = json.loads(lines[-1]) if lines else None
        report = json.loads((out / 'report.json').read_text()) if (out / 'report.json').exists() else None
        events = ([json.loads(line) for line in (out / 'events.jsonl').read_text().splitlines()]
                  if (out / 'events.jsonl').exists() else [])
        RESULTS.append({'test': self._testMethodName, 'returncode': process.returncode,
                        'external_elapsed': round(elapsed, 6), 'limit': profile['limit'],
                        'verdict': report and report.get('verdict'),
                        'decision_elapsed': report and report.get('timing', {}).get('decision_elapsed'),
                        'summary': summary,
                        'process_cleanup_state': report and report.get('process_cleanup_state'),
                        'out': str(out)})
        self.assertLess(elapsed, profile['limit'], 'externally measured wall must be strictly under limit')
        if report and report.get('status') == 'final':
            # The exit code is authoritative; the summary carries it and the completion time.
            self.assertEqual(summary['exit_code'], process.returncode)
            self.assertEqual(summary['post_decision_issues'], [])
            self.assertLess(summary['completion_elapsed'], profile['finalize_by'])
            self.assertTrue(report['timing']['decision_strictly_under_limit'])
            self.assertLess(report['timing']['decision_elapsed'], profile['finalize_by'])
            self.assertEqual(report['exit_code'], process.returncode)
            self.assertEqual(summary['verdict'], report['verdict'])
        return process.returncode, report, events, stderr.decode()

    def wait_for(self, name, seconds=3.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if (self.work / name).exists():
                return
            time.sleep(0.02)
        self.fail('control marker not created: ' + name)

    def ids(self):
        path = self.work / 'ids.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_all_dead(self, identities):
        self.assertTrue(identities)
        for identity in identities:
            self.assertNotEqual(start_ticks(identity['pid']), identity['start'], 'still alive: %r' % identity)

    def assert_verified(self, report):
        state = report['process_cleanup_state']
        self.assertTrue(state['echild_verified'])
        self.assertTrue(state['command_reaped'])
        self.assertEqual(state['recorded_unreaped'], [])
        self.assertEqual(state['issues'], [])

    @staticmethod
    def kinds(events):
        return [event['event'] for event in events]

    # ---- tests -------------------------------------------------------------
    def test_ok_exact_argv_and_raw_logs(self):
        weird = ['a b', '', 'ü', 'x\ny', '--', '-c', os.fsdecode(b'\xff')]
        command = self.program('print(json.dumps(sys.argv[2:])); print("err", file=sys.stderr)') + weird
        code, report, events, _ = self.run_driver(command)
        self.assertEqual((code, report['verdict']), (0, 'ok'))
        self.assertEqual(report['command']['argv'], command)
        self.assertEqual(report['command']['argv_hex'], [os.fsencode(arg).hex() for arg in command])
        self.assertEqual(json.loads((self.work / 'out/child.stdout').read_text()), weird)
        self.assertEqual((self.work / 'out/child.stderr').read_text(), 'err\n')
        self.assertEqual(report['child']['exit'], {'kind': 'exited', 'code': 0, 'raw': 0})
        self.assertEqual(report['child']['pgid'], report['child']['pid'])
        self.assertEqual(report['child']['sid'], report['child']['pid'])
        self.assertEqual(self.kinds(events)[:4], ['start', 'fork', 'go', 'exec_confirmed'])
        self.assertLess(report['timeline']['go_at'], FAST['launch_by'])
        self.assertEqual(report['hashes']['pre'][0]['sha256'], report['hashes']['post'][0]['sha256'])
        self.assert_verified(report)

    def test_child_failure_status(self):
        code, report, _, _ = self.run_driver(self.program('sys.exit(3)'))
        self.assertEqual((code, report['verdict']), (2, 'child_failed'))
        self.assertEqual(report['child']['exit']['code'], 3)
        self.assert_verified(report)

    def test_stuck_child_graceful_then_kill_at_deadline(self):
        body = 'signal.signal(signal.SIGINT, signal.SIG_IGN)\nme("top")\nmark("started")\ntime.sleep(60)\n'
        code, report, events, _ = self.run_driver(self.program(body))
        self.assertEqual((code, report['verdict']), (124, 'deadline'))
        timeline = report['timeline']
        self.assertGreaterEqual(timeline['graceful_signal_at'], FAST['graceful_at'])
        self.assertLess(timeline['graceful_signal_at'], FAST['kill_at'])
        self.assertGreaterEqual(timeline['kill_sent_at'], FAST['kill_at'])
        self.assertLess(timeline['cleanup_verified_at'], FAST['reap_by'])
        self.assertEqual(report['child']['exit'], {'kind': 'signaled', 'signal': 9, 'core': False, 'raw': 9})
        kill = next(event for event in events if event['event'] == 'kill')
        self.assertEqual(kill['cause'], 'deadline')
        self.assert_all_dead(self.ids())
        self.assert_verified(report)

    def test_graceful_deadline_exit_without_kill(self):
        body = ('me("top")\ntry:\n    time.sleep(60)\nexcept KeyboardInterrupt:\n'
                '    mark("got-int")\n    sys.exit(0)\n')
        code, report, events, _ = self.run_driver(self.program(body))
        self.assertEqual((code, report['verdict']), (124, 'deadline'))
        self.assertTrue((self.work / 'got-int').exists())
        self.assertNotIn('kill', self.kinds(events))
        self.assertEqual(report['child']['exit']['code'], 0)
        self.assert_verified(report)

    def test_parent_exit_leaves_group_member_and_detached_orphan(self):
        body = r'''
me('top')
if os.fork() == 0:
    me('member'); time.sleep(60); os._exit(0)
r, w = os.pipe()
middle = os.fork()
if middle == 0:
    os.setsid()
    if os.fork() == 0:
        me('daemon'); os.write(w, b'x'); time.sleep(60); os._exit(0)
    os._exit(0)
os.waitpid(middle, 0)
os.read(r, 1)
wait_ids(3)
sys.exit(0)
'''
        code, report, _, _ = self.run_driver(self.program(body))
        self.assertEqual((code, report['verdict']), (3, 'leaked'))
        ids = {entry['tag']: entry for entry in self.ids()}
        recorded = {(entry['pid'], entry['start_ticks']): entry for entry in report['descendants']}
        for tag in ('member', 'daemon'):
            entry = recorded[(ids[tag]['pid'], ids[tag]['start'])]
            self.assertTrue(entry['alive_after_command_exit'])
            self.assertIn('SIGKILL', entry['signals'])
            self.assertEqual(entry['exit']['kind'], 'signaled')
        self.assertNotEqual(recorded[(ids['daemon']['pid'], ids['daemon']['start'])]['sid'], report['child']['sid'])
        self.assert_all_dead(list(ids.values()))
        self.assert_verified(report)

    def test_group_and_session_tree_killed_on_deadline(self):
        body = r'''
signal.signal(signal.SIGINT, signal.SIG_IGN)
me('top')
if os.fork() == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN); me('member'); time.sleep(60); os._exit(0)
r, w = os.pipe()
if os.fork() == 0:
    os.setsid(); me('mid')
    if os.fork() == 0:
        me('leaf'); os.write(w, b'x'); time.sleep(60); os._exit(0)
    time.sleep(60); os._exit(0)
os.read(r, 1)
wait_ids(4)
mark('started')
time.sleep(60)
'''
        code, report, _, _ = self.run_driver(self.program(body))
        self.assertEqual((code, report['verdict']), (124, 'deadline'))
        ids = {entry['tag']: entry for entry in self.ids()}
        self.assertEqual(set(ids), {'top', 'member', 'mid', 'leaf'})
        recorded = {(entry['pid'], entry['start_ticks']) for entry in report['descendants']}
        for tag in ('mid', 'leaf'):  # Separate session: reachable only through subreaper adoption.
            self.assertIn((ids[tag]['pid'], ids[tag]['start']), recorded)
        self.assert_all_dead(list(ids.values()))
        self.assert_verified(report)

    def test_external_sigint_graceful(self):
        body = ('me("top")\nmark("started")\ntry:\n    time.sleep(60)\nexcept KeyboardInterrupt:\n'
                '    mark("got-int")\n    sys.exit(0)\n')

        def during(process):
            self.wait_for('started')
            process.send_signal(signal.SIGINT)  # Own unreaped child.
        code, report, events, _ = self.run_driver(self.program(body), during=during)
        self.assertEqual((code, report['verdict']), (130, 'interrupted'))
        self.assertTrue((self.work / 'got-int').exists())
        self.assertNotIn('kill', self.kinds(events))
        self.assertEqual([item['signal'] for item in report['interrupts']], ['SIGINT'])
        self.assertLess(report['timeline']['graceful_signal_at'], FAST['graceful_at'])
        self.assert_verified(report)

    def test_external_sigterm_stuck_child_escalates_after_grace(self):
        body = 'signal.signal(signal.SIGINT, signal.SIG_IGN)\nme("top")\nmark("started")\ntime.sleep(60)\n'

        def during(process):
            self.wait_for('started')
            process.send_signal(signal.SIGTERM)
        code, report, events, _ = self.run_driver(self.program(body), during=during)
        self.assertEqual((code, report['verdict']), (130, 'interrupted'))
        kill = next(event for event in events if event['event'] == 'kill')
        self.assertEqual(kill['cause'], 'interrupt_grace')
        interrupt_at = report['interrupts'][0]['at']
        self.assertGreaterEqual(kill['t'], interrupt_at + FAST['interrupt_grace'])
        self.assertLess(kill['t'], FAST['kill_at'])
        self.assert_all_dead(self.ids())
        self.assert_verified(report)

    def test_second_interrupt_kills_immediately(self):
        body = 'signal.signal(signal.SIGINT, signal.SIG_IGN)\nme("top")\nmark("started")\ntime.sleep(60)\n'

        def during(process):
            self.wait_for('started')
            process.send_signal(signal.SIGINT)
            time.sleep(0.1)
            process.send_signal(signal.SIGINT)
        code, report, events, _ = self.run_driver(self.program(body), during=during)
        self.assertEqual((code, report['verdict']), (130, 'interrupted'))
        kill = next(event for event in events if event['event'] == 'kill')
        self.assertEqual(kill['cause'], 'second_interrupt')
        self.assertLess(kill['t'], report['interrupts'][0]['at'] + FAST['interrupt_grace'])
        self.assert_all_dead(self.ids())
        self.assert_verified(report)

    def test_exec_failure_reported(self):
        script = self.work / 'bad-interpreter'
        script.write_text('#!/nonexistent/interpreter\n')
        script.chmod(0o755)
        code, report, _, _ = self.run_driver([str(script), 'arg'])
        self.assertEqual((code, report['verdict']), (127, 'exec_failed'))
        self.assertEqual((report['exec_error']['stage'], report['exec_error']['errno']), ('exec', 2))
        self.assertFalse(report['launched'])
        self.assert_verified(report)

    def test_preparation_counts_against_launch_bound(self):
        profile = {**FAST, 'launch_by': 0.001}
        code, report, events, _ = self.run_driver(self.program('mark("ran")'), profile=profile)
        self.assertEqual((code, report['verdict']), (125, 'precondition_failed'))
        self.assertIn('launch_by', report['precondition_error'])
        self.assertFalse(report['launched'])
        self.assertNotIn('fork', self.kinds(events))
        self.assertFalse((self.work / 'ran').exists())

    def test_coordinator_anchor_counts_earlier_preparation(self):
        # Coordinator spent 0.97 s already: accepted (within launch_by of driver start), and the
        # driver's own >= 30 ms start-up then pushes preparation past launch_by.
        anchor = time.clock_gettime(time.CLOCK_BOOTTIME) - 0.97
        profile = {**FAST, 'launch_by': 1.0}
        code, report, _, _ = self.run_driver(self.program('mark("ran")'), profile=profile,
                                             extra=['--anchor-boottime', repr(anchor)])
        self.assertEqual((code, report['verdict']), (125, 'precondition_failed'))
        self.assertEqual(report['clock']['source'], 'coordinator --anchor-boottime')
        self.assertFalse((self.work / 'ran').exists())
        future = time.clock_gettime(time.CLOCK_BOOTTIME) + 60
        code, report, _, _ = self.run_driver(self.program('mark("ran")'), extra=['--anchor-boottime', repr(future)],
                                             out=self.work / 'out-future')
        self.assertEqual((code, report), (125, None))

    def test_refusals_without_launch(self):
        (self.work / 'exists').mkdir()
        code, report, _, stderr = self.run_driver(self.program('mark("ran")'), out=self.work / 'exists')
        self.assertEqual((code, report), (125, None))
        self.assertIn('cannot create new --out', stderr)
        for profile in ({**FAST, 'limit': 1800.5}, {**FAST, 'kill_at': FAST['graceful_at']}):
            code, report, _, stderr = self.run_driver(self.program('mark("ran")'), profile=profile,
                                                      out=self.work / ('bad-%d' % len(RESULTS)))
            self.assertEqual((code, report), (125, None))
            self.assertIn('invalid parameters', stderr)
        fifo = self.work / 'fifo'
        os.mkfifo(fifo)
        code, report, _, _ = self.run_driver(self.program('mark("ran")'), extra=['--hash', str(fifo)],
                                             out=self.work / 'out-fifo')
        self.assertEqual((code, report['verdict']), (125, 'precondition_failed'))
        self.assertFalse((self.work / 'ran').exists())

    def test_inherited_ignored_and_blocked_signals_are_reset(self):
        wrapper = ('import os, signal, sys\n'
                   'signal.signal(signal.SIGINT, signal.SIG_IGN)\n'
                   'signal.signal(signal.SIGCHLD, signal.SIG_IGN)\n'
                   'signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGUSR1})\n'
                   'os.execv(sys.argv[1], sys.argv[1:])\n')
        body = ('st = dict(l.split(":", 1) for l in open("/proc/self/status").read().splitlines() if l.startswith("Sig"))\n'
                'print(json.dumps({k: int(v, 16) for k, v in st.items() if k in ("SigIgn", "SigBlk")}), flush=True)\n'
                'time.sleep(60)\n')
        code, report, _, _ = self.run_driver(self.program(body), wrapper=wrapper)
        state = json.loads((self.work / 'out/child.stdout').read_text())
        self.assertEqual(state['SigIgn'] & (1 << (signal.SIGINT - 1) | 1 << (signal.SIGCHLD - 1)), 0)
        self.assertEqual(state['SigBlk'], 0)
        self.assertEqual(report['signals']['inherited_dispositions']['SIGCHLD'], 'SIG_IGN')
        self.assertIn('SIGTERM', report['signals']['inherited_blocked'])
        # Graceful SIGINT at the deadline terminated it: proof SIGINT was not inherited as ignored.
        self.assertEqual((code, report['verdict']), (124, 'deadline'))
        self.assertEqual(report['child']['exit']['signal'], signal.SIGINT)
        self.assertNotIn('kill_sent_at', report['timeline'])
        self.assert_verified(report)

    def test_pending_interrupt_before_launch(self):
        wrapper = ('import os, signal, sys\n'
                   'signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})\n'
                   'os.kill(os.getpid(), signal.SIGINT)\n'
                   'os.execv(sys.argv[1], sys.argv[1:])\n')
        code, report, events, _ = self.run_driver(self.program('mark("ran")'), wrapper=wrapper)
        self.assertEqual((code, report['verdict']), (130, 'interrupted'))
        self.assertFalse(report['launched'])
        self.assertEqual(report['interrupts'][0]['phase'], 'preparation')
        self.assertFalse((self.work / 'ran').exists())
        self.assert_verified(report)

    def test_lock_checks_on_self_owned_lock(self):
        lock = self.work / 'own.lock'
        self.assertNotEqual(lock.resolve(), COMMON_LOCK.resolve())
        lock.touch()
        # (a) held by this test before launch: refuse without launching.
        handle = open(lock, 'a+')
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            code, report, _, _ = self.run_driver(self.program('mark("ran")'), out=self.work / 'out-held',
                                                 extra=['--lock-path', str(lock), '--require-lock-free'])
            self.assertEqual((code, report['verdict']), (125, 'precondition_failed'))
            self.assertEqual(report['lock_pre']['state'], 'held')
            self.assertIn(os.getpid(), [entry['pid'] for entry in report['lock_pre']['entries']])
            self.assertFalse((self.work / 'ran').exists())
            # (b) still held after the run (not by the tree): end check fails closed.
            code, report, _, _ = self.run_driver(self.program('sys.exit(0)'), out=self.work / 'out-held-after',
                                                 extra=['--lock-path', str(lock)])
            self.assertEqual((code, report['verdict']), (70, 'cleanup_unverified'))
            self.assertEqual(report['lock_post']['state'], 'held')
        finally:
            handle.close()
        # (c) a leaked detached orphan holds the lock; driver kills it and the lock is released.
        body = r'''
import fcntl
fd = os.open(sys.argv[2], os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
me('top')
r, w = os.pipe()
middle = os.fork()
if middle == 0:
    os.setsid()
    if os.fork() == 0:
        me('holder'); os.write(w, b'x'); time.sleep(60); os._exit(0)
    os._exit(0)
os.waitpid(middle, 0)
os.read(r, 1)
sys.exit(0)
'''
        code, report, _, _ = self.run_driver(self.program(body) + [str(lock)], out=self.work / 'out-orphan',
                                             extra=['--lock-path', str(lock), '--require-lock-free'])
        self.assertEqual((code, report['verdict']), (3, 'leaked'))
        self.assertEqual((report['lock_pre']['state'], report['lock_post']['state']), ('free', 'free'))
        self.assert_all_dead(self.ids())
        self.assert_verified(report)

    def test_alarm_backstop_exits_71(self):
        code = ('import importlib.util, sys, time\n'
                'spec = importlib.util.spec_from_file_location("b", sys.argv[1]); b = importlib.util.module_from_spec(spec)\n'
                'spec.loader.exec_module(b)\n'
                'b.arm_alarm_backstop(b.Clock(b.boottime()), 0.3)\n'
                'time.sleep(5)\n')
        started = time.monotonic()
        result = subprocess.run([sys.executable, '-I', '-c', code, str(DRIVER)], timeout=5)
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 71)
        self.assertLess(elapsed, 2.0)
        RESULTS.append({'test': self._testMethodName, 'returncode': result.returncode, 'external_elapsed': elapsed})

    def test_thread_backstop_fires_when_alarm_blocked(self):
        trace = self.work / 'watchdog.txt'
        code = ('import importlib.util, signal, sys, time\n'
                'spec = importlib.util.spec_from_file_location("b", sys.argv[1]); b = importlib.util.module_from_spec(spec)\n'
                'spec.loader.exec_module(b)\n'
                'signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})\n'
                'clock = b.Clock(b.boottime())\n'
                'b.arm_alarm_backstop(clock, 0.2)\n'
                'b.arm_thread_backstop(clock, 0.4, open(sys.argv[2], "w"))\n'
                'time.sleep(5)\n')
        started = time.monotonic()
        result = subprocess.run([sys.executable, '-I', '-c', code, str(DRIVER), str(trace)], timeout=5)
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 1)
        self.assertLess(elapsed, 2.0)
        self.assertIn('Timeout', trace.read_text())
        RESULTS.append({'test': self._testMethodName, 'returncode': result.returncode, 'external_elapsed': elapsed})

    # ---- phase7 topology mirror (pure Python stand-ins; the real helper is never launched) ----
    SUPERVISOR = r'''
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGTERM, 0, 0, 0) or os.getppid() != int(sys.argv[2]):
    sys.exit(125)
me('supervisor')
cli = 0
def stop(number, frame):  # phase7_spawn.c stop(): CLI is unreaped (WNOWAIT), so its group is safe.
    if cli:
        os.killpg(cli, signal.SIGKILL)
for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(number, stop)
cli = os.fork()
if cli == 0:
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, signal.SIG_DFL)
    libc.prctl(1, signal.SIGKILL, 0, 0, 0)
    os.setsid()
    me('cli')
    if os.fork() == 0:
        me('cli-child'); time.sleep(60); os._exit(0)
    time.sleep(60); os._exit(0)
os.waitid(os.P_PID, cli, os.WEXITED | os.WNOWAIT)
os.waitpid(cli, 0)
'''
    COORDINATOR = r'''
import subprocess
mode = sys.argv[2]
if mode == 'stuck':
    signal.signal(signal.SIGINT, signal.SIG_IGN)
me('coordinator')
fixture = subprocess.Popen([sys.executable, '-I', '-c', open(sys.argv[3]).read(), WORK], start_new_session=True)
supervisor = os.posix_spawn(sys.executable, [sys.executable, '-I', '-c', open(sys.argv[4]).read(), WORK,
                            str(os.getpid())], os.environ)
wait_ids(5)
mark('started')
if mode == 'abandon':
    os._exit(0)
try:
    time.sleep(60)
except KeyboardInterrupt:  # NativeChild.close / Fixture.close analogue.
    os.killpg(fixture.pid, signal.SIGKILL); fixture.wait()
    try:
        os.kill(supervisor, signal.SIGTERM)
    except ProcessLookupError:
        pass
    os.waitpid(supervisor, 0)
    mark('coordinator-cleaned')
    sys.exit(130)
'''

    def mirror(self, mode):
        fixture = self.work / 'fixture.py'
        fixture.write_text(PRELUDE + "me('fixture')\ntime.sleep(60)\n")
        supervisor = self.work / 'supervisor.py'
        supervisor.write_text(PRELUDE + self.SUPERVISOR)
        command = self.program(self.COORDINATOR, 'coordinator.py') + [mode, str(fixture), str(supervisor)]
        code, report, events, _ = self.run_driver(command)
        ids = {entry['tag']: entry for entry in self.ids()}
        self.assertEqual(set(ids), {'coordinator', 'fixture', 'supervisor', 'cli', 'cli-child'})
        self.assert_all_dead(list(ids.values()))
        self.assert_verified(report)
        return code, report, events, ids

    def test_mirror_graceful_coordinator_cleans_its_tree(self):
        code, report, events, _ = self.mirror('graceful')
        self.assertEqual((code, report['verdict']), (124, 'deadline'))
        self.assertTrue((self.work / 'coordinator-cleaned').exists())
        self.assertNotIn('kill', self.kinds(events))
        self.assertFalse(any(entry.get('alive_after_command_exit') for entry in report['descendants']))

    def test_mirror_stuck_coordinator_killed_and_fixture_adopted(self):
        code, report, events, ids = self.mirror('stuck')
        self.assertEqual((code, report['verdict']), (124, 'deadline'))
        self.assertEqual(next(e for e in events if e['event'] == 'kill')['cause'], 'deadline')
        recorded = {(entry['pid'], entry['start_ticks']) for entry in report['descendants']}
        self.assertIn((ids['fixture']['pid'], ids['fixture']['start']), recorded)

    def test_mirror_abandoning_coordinator_leaks_are_adopted(self):
        code, report, _, ids = self.mirror('abandon')
        self.assertEqual((code, report['verdict']), (3, 'leaked'))
        leaked = {(entry['pid'], entry['start_ticks']) for entry in report['descendants']
                  if entry.get('alive_after_command_exit')}
        self.assertIn((ids['fixture']['pid'], ids['fixture']['start']), leaked)

    def test_argparse_errors_map_to_precondition(self):
        result = subprocess.run([sys.executable, '-I', str(DRIVER), 'run', '--out', str(self.work / 'o')],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 125)
        self.assertFalse((self.work / 'o').exists())

    def test_production_defaults_fit_the_rule(self):
        loaded = module()
        defaults = {key: float(value) for key, value in loaded.DEFAULTS.items()}
        loaded.validate(defaults)
        self.assertEqual(defaults['limit'], 1800.0)
        self.assertLess(loaded.watchdog_at(defaults), 1800.0)
        self.assertLessEqual(defaults['graceful_at'], 1740.0)  # Root plan SIGTERM point or earlier.
        self.assertLessEqual(defaults['kill_at'], 1770.0)      # Root plan SIGKILL point or earlier.
        with self.assertRaises(ValueError):
            loaded.validate({**defaults, 'limit': 1800.001})


    # ---- r2 focused causal tests (review B1, B2, N1, N2, N5) ----------------------
    R2_PARAMS = {'limit': 8.0, 'launch_by': 2.0, 'graceful_at': 2.5, 'kill_at': 3.5, 'reap_by': 4.5,
                 'finalize_by': 5.5, 'hard_exit_at': 6.5, 'interrupt_grace': 0.6}

    class FakeClock:
        def __init__(self, value):
            self.value = value

        def now(self):
            return self.value

    def r2_driver(self, clock, params=None):
        """In-process Driver at the state cleanup leaves on a clean run; nothing is launched."""
        loaded = module()
        out = self.work / 'r2-out'
        out.mkdir()
        driver = loaded.Driver(dict(params or self.R2_PARAMS), [sys.executable], out, self.work, None, False, [],
                               clock, {})
        driver.events_fd = os.open(out / 'events.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o644)
        driver.top_final = {'kind': 'exited', 'code': 0, 'raw': 0}
        driver.report['cleanup'] = {'echild_verified': True, 'recorded_unreaped': []}
        return loaded, driver

    def r2_finish(self, driver, stdout=None):
        buffer = stdout or io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = driver.finish()
        text = buffer.getvalue() if isinstance(buffer, io.StringIO) else ''
        summary = json.loads(text.strip().splitlines()[-1]) if text.strip() else None
        RESULTS.append({'test': self._testMethodName, 'returncode': code, 'summary': summary})
        return code, summary

    def test_r2_b1_skipped_post_hash_fails_closed(self):
        clock = self.FakeClock(0.0)
        loaded, driver = self.r2_driver(clock)
        target = self.work / 'hashed.txt'
        target.write_text('x')
        driver.report['hashes'] = {'pre': [loaded.sha256_file(target)], 'post': []}
        clock.value = (self.R2_PARAMS['reap_by'] + self.R2_PARAMS['finalize_by']) / 2 + 0.01  # < finalize_by
        driver.end_checks()
        code, summary = self.r2_finish(driver)
        self.assertEqual((code, summary['verdict']), (70, 'cleanup_unverified'))
        self.assertEqual(driver.report['hashes']['post'][0]['skipped'], 'finalize budget')
        self.assertIn('post hash skipped (finalize budget): ' + str(target), driver.report['process_cleanup_state']['issues'])

    def test_r2_b2_final_report_write_failure_is_70(self):
        loaded, driver = self.r2_driver(self.FakeClock(1.0))
        driver.write_report()  # Provisional 'running' report, as in a real run.
        os.chmod(driver.out, 0o555)
        try:
            code, summary = self.r2_finish(driver)
        finally:
            os.chmod(driver.out, 0o755)
        self.assertEqual((code, summary['verdict'], summary['report_verdict']), (70, 'cleanup_unverified', 'ok'))
        self.assertTrue(any(issue.startswith('report write failed') for issue in summary['post_decision_issues']))
        kept = json.loads((driver.out / 'report.json').read_text())
        self.assertEqual(kept['status'], 'running')  # Incomplete report retained, exit code says unverified.

    def test_r2_b2_late_final_write_is_72_and_report_is_qualified(self):
        clock = self.FakeClock(5.0)
        loaded, driver = self.r2_driver(clock)
        original = driver.write_report

        def slow(durable=False):
            clock.value = 5.6  # Write completes after finalize_by 5.5.
            original(durable)
        driver.write_report = slow
        code, summary = self.r2_finish(driver)
        self.assertEqual((code, summary['verdict']), (72, 'late'))
        self.assertGreaterEqual(summary['completion_elapsed'], self.R2_PARAMS['finalize_by'])
        written = json.loads((driver.out / 'report.json').read_text())
        self.assertEqual((written['verdict'], written['exit_code']), ('ok', 0))
        self.assertTrue(written['timing']['decision_within_finalize_by'])
        self.assertIn('process exit code is authoritative', written['decision_scope'])
        events = [json.loads(line) for line in (driver.out / 'events.jsonl').read_text().splitlines()]
        self.assertEqual((events[-1]['event'], events[-1]['exit_code']), ('exit', 72))

    def test_r2_b2_events_fsync_failure_is_70(self):
        loaded, driver = self.r2_driver(self.FakeClock(1.0))
        os.close(driver.events_fd)
        read_end, write_end = os.pipe()  # Writes succeed, fsync on a pipe fails (EINVAL).
        driver.events_fd = write_end
        try:
            code, summary = self.r2_finish(driver)
        finally:
            os.close(read_end)
            os.close(write_end)
        self.assertEqual(code, 70)
        self.assertTrue(any(issue.startswith('events fsync failed') for issue in summary['post_decision_issues']))

    def test_r2_b2_final_event_write_failure_is_70(self):
        loaded, driver = self.r2_driver(self.FakeClock(1.0))
        os.close(driver.events_fd)
        driver.events_fd = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)  # write -> EBADF
        fd = driver.events_fd
        try:
            code, summary = self.r2_finish(driver)
        finally:
            os.close(fd)
        self.assertEqual(code, 70)
        self.assertTrue(any(issue.startswith('events write failed') for issue in summary['post_decision_issues']))

    def test_r2_b2_summary_print_failure_is_70(self):
        class Broken(io.TextIOBase):
            def write(self, text):
                raise BrokenPipeError(32, 'stdout gone')
        loaded, driver = self.r2_driver(self.FakeClock(1.0))
        code, _ = self.r2_finish(driver, stdout=Broken())
        self.assertEqual(code, 70)
        events = [json.loads(line) for line in (driver.out / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(events[-1]['exit_code'], 70)

    def test_r2_n5_signal_pending_at_end_checks_is_recorded_before_decision(self):
        loaded, driver = self.r2_driver(self.FakeClock(1.0))
        driver.pending.append((signal.SIGTERM, 0.9))
        code, summary = self.r2_finish(driver)
        self.assertEqual((code, summary['verdict']), (130, 'interrupted'))
        self.assertEqual(driver.report['interrupts'][0]['phase'], 'end_checks')

    def test_r2_n1_implausible_anchor_refused_before_alarm(self):
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        for index, value in enumerate(('nan', '-inf', '-1e9', repr(now - 100.0))):
            out = self.work / ('anchor-%d' % index)
            started = time.monotonic()
            result = subprocess.run([sys.executable, '-I', str(DRIVER), 'run', '--out', str(out),
                                     '--anchor-boottime=' + value, '--', sys.executable, '-I', '-c',
                                     'open(%r, "w").close()' % str(self.work / 'ran')],
                                    capture_output=True, timeout=5)
            RESULTS.append({'test': self._testMethodName, 'anchor': value, 'returncode': result.returncode,
                            'external_elapsed': time.monotonic() - started})
            self.assertEqual(result.returncode, 125, value)
            self.assertIn(b'--anchor-boottime', result.stderr)
            self.assertFalse(out.exists())
        self.assertFalse((self.work / 'ran').exists())

    def test_r2_n2_interrupt_during_exec_confirmation_uses_supervise(self):
        """Own forked session leader whose 'exec' takes 1 s; SIGINT at 0.2 s must not wait for it."""
        loaded = module()
        clock = loaded.Clock(loaded.boottime())
        params = {'limit': 25.0, 'launch_by': 5.0, 'graceful_at': 20.0, 'kill_at': 21.0, 'reap_by': 22.0,
                  'finalize_by': 23.0, 'hard_exit_at': 24.0, 'interrupt_grace': 0.6}
        _, driver = self.r2_driver(clock, params)
        driver.report['capabilities'] = {}
        saved = {number: signal.getsignal(number) for number in (*loaded.EXTERNAL_SIGNALS, signal.SIGCHLD)}
        ready_r, ready_w = os.pipe2(os.O_CLOEXEC)
        go_r, go_w = os.pipe2(os.O_CLOEXEC)
        error_r, error_w = os.pipe2(os.O_CLOEXEC)
        pid = os.fork()
        if pid == 0:
            try:
                for number in loaded.CHILD_DEFAULT_SIGNALS:
                    signal.signal(number, signal.SIG_DFL)
                for fd in (ready_r, go_w, error_r):  # No parent-side copies (cf. the reviewer's probe leak).
                    os.close(fd)
                LIBC.prctl(1, signal.SIGKILL, 0, 0, 0)
                os.setsid()
                os.write(ready_w, b'R')
                os.read(go_r, 1)
                time.sleep(1.0)  # Stand-in for a slow execve; error_w stays open meanwhile.
                os.close(error_w)
                time.sleep(1.0)
            finally:
                os._exit(0)
        for fd in (ready_w, go_r, error_w):
            os.close(fd)
        driver.child = pid
        try:
            driver.install_signals()
            timer = threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGINT))
            started = clock.now()
            timer.start()
            outcome = driver.release_child(pid, ready_r, go_w, error_r)
            returned = clock.now() - started
            timer.join()
            for fd in (ready_r, go_w, error_r):
                os.close(fd)
            self.assertEqual(outcome, 'running')
            self.assertLess(returned, 0.8)
            self.assertTrue(driver.pending)
            self.assertIn('abandoned', driver.report['child']['exec_confirmation'])
            kill_cause = driver.supervise()  # Takes the interrupt: group SIGINT ends the control.
            driver.cleanup(kill_cause)
        finally:
            signal.set_wakeup_fd(-1)
            for number, handler in saved.items():
                signal.signal(number, handler)
            for fd in (driver.wake_r, driver.wake_w):
                if fd >= 0:
                    os.close(fd)
        RESULTS.append({'test': self._testMethodName, 'release_child_returned_after': returned,
                        'kill_cause': kill_cause, 'top_final': driver.top_final})
        self.assertIsNone(kill_cause)
        self.assertEqual(driver.interrupts[0]['signal'], 'SIGINT')
        self.assertEqual(driver.top_final, {'kind': 'signaled', 'signal': signal.SIGINT, 'core': False,
                                            'raw': signal.SIGINT})
        self.assertTrue(driver.report['cleanup']['echild_verified'])
        events = [json.loads(line) for line in (driver.out / 'events.jsonl').read_text().splitlines()]
        sent = [event for event in events if event['event'] == 'group_signal']
        self.assertEqual((sent[0]['signal'], sent[0]['reason']), ('SIGINT', 'external SIGINT'))
        self.assertLess(sent[0]['t'] - started, 0.8)

if __name__ == '__main__':
    unittest.main()
