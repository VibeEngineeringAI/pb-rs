"""Pure ownership model; importing this file creates no processes or source imports."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parent
TMP = Path('/home/ajs/.cache/pb-phase7/tmp')
RUN = None
PROCESS_CALLS = []


def forbidden(*args, **kwargs):
    PROCESS_CALLS.append((args, kwargs))
    raise AssertionError('unexpected real process operation')


@contextlib.contextmanager
def process_guard():
    with contextlib.ExitStack() as stack:
        for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
            stack.enter_context(mock.patch.object(subprocess, name, forbidden))
        for name in ('fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'system', 'kill', 'killpg',
                     'wait', 'waitpid', 'wait3', 'wait4', 'waitid', 'pidfd_open',
                     'execv', 'execve', 'execvp', 'execvpe', 'execl', 'execle', 'execlp', 'execlpe'):
            if hasattr(os, name):
                stack.enter_context(mock.patch.object(os, name, forbidden))
        yield


def load(path):
    spec = importlib.util.spec_from_file_location('owned_cleanup_candidate', path)
    module = importlib.util.module_from_spec(spec)
    with process_guard(), mock.patch.object(sys, 'path', [str(path.parent), *sys.path]):
        # Imports must resolve to the generated copy, never an ambient cases module.
        cases_spec = importlib.util.spec_from_file_location('cases', path.with_name('cases.py'))
        cases = importlib.util.module_from_spec(cases_spec)
        cases_spec.loader.exec_module(cases)
        with mock.patch.dict(sys.modules, {'cases': cases}):
            spec.loader.exec_module(module)
    return module


class Pipe:
    def __init__(self, fd):
        self.fd, self.closed = fd, False
    def fileno(self):
        return self.fd
    def close(self):
        self.closed = True
    def readline(self):
        return b'{"url":"http://127.0.0.1:1","grpc_port":1234}\n'


class Model:
    """A reap immediately recycles PID==PGID to an unrelated group.

    Signals check ownership independently of the implementation. Delayed final
    reaping has a different clock value and deliberately different rusage from
    the synthetic native-client payload.
    """
    def __init__(self, root, *, stdout=b'[{"sequence":0}]', stderr=b'note\n',
                 live=False, held=False, native=True, raw_status=0):
        self.root = root
        self.proc = SimpleNamespace(pid=424242, stdout=Pipe(10), stderr=Pipe(11), returncode=None)
        self.buffers = {10: [stdout, b''], 11: [stderr, b'']}
        self.mapping = {}
        self.events = []
        self.now = 1_000_000_000
        self.live, self.held, self.native, self.raw_status = live, held, native, raw_status
        self.reaped = False
        self.closed = False
        self.observed_at = None
        self.mask = set()
        self.handlers = {signal.SIGINT: signal.default_int_handler, signal.SIGCHLD: signal.SIG_DFL}
        self.fail_select = self.fail_register = None
        self.native_metrics = dict(started_monotonic_ns=1_015_000_000,
            ended_monotonic_ns=1_138_456_789, started_unix_ns=4321,
            exit_code=-signal.SIGTERM, termination={'signal': signal.SIGTERM},
            cpu_user_ms=1.234567, cpu_system_ms=2.345678, peak_rss_bytes=7654321,
            minor_faults=23, major_faults=4, voluntary_context_switches=5,
            involuntary_context_switches=6, block_input_operations=17, block_output_operations=19,
            resource_source='native_child_wait4', proc_io_available=True,
            proc_io_unavailable_reason=None, proc_io_source='linux_proc_pid_io_before_wait4',
            proc_io_syscr=111, proc_io_syscw=222, proc_io_rchar=333,
            proc_io_wchar=444, proc_io_read_bytes=555, proc_io_write_bytes=666,
            proc_io_cancelled_write_bytes=777)
        self.usage = SimpleNamespace(ru_utime=.5, ru_stime=.25, ru_maxrss=9876,
            ru_minflt=81, ru_majflt=82, ru_nvcsw=83, ru_nivcsw=84, ru_inblock=85, ru_oublock=86)

    def spawn(self, argv, env, cwd, stdout_path=None):
        self.events.append(('spawn', bool(signal.SIGINT in self.mask)))
        if self.native:
            (self.root / 'meter.json').write_text(json.dumps(self.native_metrics))
        if stdout_path is not None:
            stdout_path.write_bytes(self.buffers[10].pop(0))
        return self.proc

    def clock(self):
        self.now += 10_000_000
        return self.now

    def sigmask(self, how, mask):
        old = self.mask.copy()
        if how == signal.SIG_BLOCK:
            self.mask.update(mask)
        else:
            self.mask = set(mask)
        return old

    def waitid(self, kind, pid, flags):
        if pid != self.proc.pid or kind != os.P_PID:
            raise AssertionError('waitid must name exact child')
        if flags != os.WEXITED | os.WNOHANG | os.WNOWAIT:
            raise AssertionError('exit observation must not reap')
        if self.reaped:
            raise ChildProcessError('not an owned child')
        self.events.append(('observe', self.live))
        if self.live:
            return None
        if self.observed_at is None:
            self.observed_at = self.now
        return SimpleNamespace(si_pid=pid, si_status=self.raw_status)

    def wait4(self, pid, flags):
        if pid != self.proc.pid:
            raise AssertionError('wait4 must name exact child')
        if flags == os.WNOHANG:
            # Faithfully model the old eager reap for the original-source control.
            if self.live:
                return 0, 0, None
        elif flags != 0:
            raise AssertionError('unexpected wait4 flags')
        if self.reaped:
            raise ChildProcessError('already reaped')
        if self.live:
            raise AssertionError('blocking reap before killing live child')
        self.events.append(('reap', flags, bool(signal.SIGINT in self.mask)))
        self.reaped = True
        self.now += 2_000_000_000
        return pid, self.raw_status, self.usage

    def killpg(self, pid, sig):
        if self.reaped:
            raise AssertionError('numeric group signal after reap targets recycled PID')
        if pid != self.proc.pid:
            raise AssertionError('signal must target owned leader')
        self.events.append(('signal', int(sig)))
        if sig == signal.SIGKILL and self.live:
            self.live = False
            self.raw_status = signal.SIGKILL

    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()
    def close(self):
        self.closed = True
    def register(self, pipe, events, name=None):
        if self.fail_register:
            raise self.fail_register
        self.mapping[pipe.fd] = SimpleNamespace(data=name, fd=pipe.fd, fileobj=pipe)
    def unregister(self, pipe):
        del self.mapping[pipe.fd]
    def get_map(self):
        return self.mapping
    def select(self, wait):
        if self.fail_select:
            raise self.fail_select
        return [(key, 1) for fd, key in list(self.mapping.items())
                if not (self.held and self.buffers[fd] == [b''])]
    def read(self, fd, size):
        return self.buffers[fd].pop(0)

    @contextlib.contextmanager
    def installed(self):
        with process_guard(), contextlib.ExitStack() as stack:
            for owner, name, value in (
                (RUN, 'spawn_measured', self.spawn), (RUN, 'clock_ns', self.clock),
                (RUN.selectors, 'DefaultSelector', lambda: self),
                (os, 'set_blocking', lambda *a: None), (os, 'read', self.read),
                (os, 'waitid', self.waitid), (os, 'wait4', self.wait4), (os, 'killpg', self.killpg),
                (signal, 'pthread_sigmask', self.sigmask),
                (signal, 'getsignal', lambda sig: self.handlers.get(sig, signal.SIG_DFL)),
                (signal, 'signal', lambda sig, handler: self.handlers.__setitem__(sig, handler))):
                stack.enter_context(mock.patch.object(owner, name, value))
            yield


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='shared-owned-r1-', dir=TMP)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def execute(self, model=None, **options):
        m = model or Model(self.root)
        with m.installed():
            result = RUN.measure(['CONTROL-NOT-EXECUTED'], {}, self.root,
                                 meter='SYNTHETIC-NOT-EXECUTED', **options)
        self.assertTrue(m.reaped)
        self.assertTrue(m.proc.stdout.closed and m.proc.stderr.closed)
        self.assertTrue(m.closed)
        self.assertEqual(m.mask, set())
        return m, result

    def test_native_client_payload_exactly_overrides_distinct_supervisor_fallback(self):
        m, (result, out, err) = self.execute(track_json_array=True)
        for key, expected in m.native_metrics.items():
            if not key.endswith('monotonic_ns'):
                self.assertEqual(result[key], expected, key)
        self.assertEqual(result['wall_ms'], 123.456789)
        self.assertEqual(result['cpu_ms'], 1.234567 + 2.345678)
        self.assertEqual(result['block_io_source'], 'native_child_wait4')
        self.assertEqual(result['supervisor_startup_ms'], 5)
        self.assertEqual((out, err), (b'[{"sequence":0}]', b'note\n'))
        self.assertEqual(result['stdout_raw_sha256'], hashlib.sha256(out).hexdigest())
        self.assertEqual(result['stderr_raw_sha256'], hashlib.sha256(err).hexdigest())
        self.assertEqual(result['json_array_elements'], 1)
        self.assertTrue(result['json_array_complete'] and result['json_array_sequence_order_valid'])
        self.assertIsNone(result['failure'])

    def test_final_signal_precedes_sole_reap_and_spawn_handoff_is_masked(self):
        m, _ = self.execute()
        self.assertEqual(m.events[0], ('spawn', True))
        self.assertEqual(m.events[-2:], [('signal', int(signal.SIGKILL)), ('reap', 0, True)])
        self.assertEqual(sum(e[0] == 'reap' for e in m.events), 1)

    def test_missing_native_payload_remains_explicit_supervisor_fallback(self):
        m, (r, _, _) = self.execute(Model(self.root, native=False))
        self.assertEqual(r['cpu_user_ms'], 500)
        self.assertEqual(r['cpu_system_ms'], 250)
        self.assertEqual(r['peak_rss_bytes'], 9876 * (1 if sys.platform == 'darwin' else 1024))
        self.assertEqual(r['resource_source'], 'supervisor_wait4_fallback')
        self.assertEqual(r['block_io_source'], 'supervisor_wait4_fallback')
        self.assertFalse(r['proc_io_available'])
        self.assertIsNone(r['proc_io_syscr'])
        self.assertIn('meter did not produce', r['failure'])
        self.assertEqual(r['wall_ms'], (m.observed_at - r['started_monotonic_ns']) / 1e6)
        self.assertLess(r['wall_ms'], 100)
        self.assertGreater(m.now - m.observed_at, 1_000_000_000)

    def test_timeout_retains_signal_and_failure_without_native_claim(self):
        _, (r, _, _) = self.execute(Model(self.root, live=True, native=False), timeout=.025)
        self.assertEqual(r['failure'], 'timeout')
        self.assertEqual(r['termination'], {'signal': signal.SIGKILL})
        self.assertEqual(r['exit_code'], -signal.SIGKILL)
        self.assertEqual(r['resource_source'], 'supervisor_wait4_fallback')

    def test_exited_leader_descendant_held_pipes_never_reaps_before_group_cleanup(self):
        m, (r, out, err) = self.execute(Model(self.root, held=True, native=False), timeout=.055)
        # Preserve the old failure precedence: timeout is assigned before the
        # descendant-held-pipe branch and must not be relabelled by this patch.
        self.assertEqual(r['failure'], 'timeout')
        self.assertEqual(r['termination'], {'exit': 0})
        self.assertEqual((out, err), (b'[{"sequence":0}]', b'note\n'))
        self.assertGreaterEqual(sum(e[0] == 'signal' for e in m.events), 2)

    def test_eof_with_redirected_descendants_still_signals_reserved_group(self):
        m, (r, _, _) = self.execute(Model(self.root, native=False))
        self.assertEqual(r['termination'], {'exit': 0})
        self.assertEqual(m.events[-2][0], 'signal')

    def test_keyboard_interrupt_and_eoferror_during_capture_cleanup_and_reraise(self):
        for error in (KeyboardInterrupt(), EOFError('control')):
            with self.subTest(error=type(error).__name__):
                m = Model(self.root)
                m.fail_select = error
                with m.installed(), self.assertRaises(type(error)):
                    RUN.measure(['CONTROL'], {}, self.root)
                self.assertTrue(m.reaped and m.proc.stdout.closed and m.proc.stderr.closed and m.closed)
                self.assertEqual(m.events[-1], ('reap', 0, True))

    def test_partial_selector_registration_cleanup(self):
        m = Model(self.root)
        m.fail_register = KeyboardInterrupt()
        with m.installed(), self.assertRaises(KeyboardInterrupt):
            RUN.measure(['CONTROL'], {}, self.root)
        self.assertTrue(m.reaped and m.closed and m.proc.stdout.closed and m.proc.stderr.closed)

    def test_selector_construction_failure_cleanup(self):
        m = Model(self.root)
        with m.installed(), mock.patch.object(RUN.selectors, 'DefaultSelector', side_effect=OSError('control')), \
                self.assertRaises(OSError):
            RUN.measure(['CONTROL'], {}, self.root)
        self.assertTrue(m.reaped and m.proc.stdout.closed and m.proc.stderr.closed)

    def test_partial_spool_acquisition_cleanup(self):
        m = Model(self.root)
        spool = io.BytesIO()
        with m.installed(), mock.patch.object(RUN.tempfile, 'TemporaryFile', side_effect=[spool, KeyboardInterrupt()]), \
                self.assertRaises(KeyboardInterrupt):
            RUN.measure(['CONTROL'], {}, self.root, spool_output=True)
        self.assertTrue(spool.closed and m.reaped and m.closed)

    def test_json_callback_interrupt_cleanup(self):
        m = Model(self.root)
        def stop():
            raise KeyboardInterrupt()
        with m.installed(), self.assertRaises(KeyboardInterrupt):
            RUN.measure(['CONTROL'], {}, self.root, track_json_array=True, on_first_complete_element=stop)
        self.assertTrue(m.reaped and m.closed)

    def test_output_cap_retains_hash_and_failure(self):
        m, (r, out, err) = self.execute(max_output=5)
        self.assertEqual(r['failure'], 'output-limit')
        self.assertTrue(r['output_truncated'])
        self.assertEqual(out, b'[{"se')
        self.assertEqual(err, b'')
        self.assertEqual(r['stdout_raw_sha256'], hashlib.sha256(out).hexdigest())

    def test_spool_and_file_sink_preserve_bytes_and_validation(self):
        for mode in ('spool', 'file'):
            with self.subTest(mode=mode):
                options = dict(track_json_array=True)
                options.update(spool_output=True) if mode == 'spool' else options.update(stdout_sink='file')
                _, (r, out, err) = self.execute(**options)
                self.assertEqual((out, err), (b'[{"sequence":0}]', b'note\n'))
                self.assertEqual(r['json_array_elements'], 1)
                if mode == 'file':
                    self.assertIsNone(r['first_stdout_ms'])
                    self.assertIsNone(r['first_complete_element_ms'])

    def test_record_signal_occurs_before_reap(self):
        m, (r, _, _) = self.execute(Model(self.root, stdout=b'one\n'), signal_after_lines=1)
        self.assertTrue(r['signal_sent'])
        self.assertIn(('signal', int(signal.SIGINT)), m.events)

    def test_nondefault_sigchld_is_rejected_before_spawn(self):
        for disposition in (signal.SIG_IGN, lambda *a: None, None):
            with self.subTest(disposition=disposition), process_guard(), \
                    mock.patch.object(signal, 'getsignal', return_value=disposition), \
                    mock.patch.object(RUN, 'spawn_measured') as spawn, \
                    self.assertRaisesRegex(RuntimeError, 'SIGCHLD'):
                RUN.measure(['CONTROL'], {}, self.root)
            spawn.assert_not_called()

    def test_missing_waitid_explicitly_rejects_before_spawn(self):
        with process_guard(), mock.patch.object(RUN, 'spawn_measured') as spawn:
            saved = os.waitid
            del os.waitid
            try:
                with self.assertRaisesRegex(RuntimeError, 'requires waitid'):
                    RUN.measure(['CONTROL'], {}, self.root)
            finally:
                os.waitid = saved
        spawn.assert_not_called()

    def test_echild_prevents_numeric_group_signal(self):
        m = Model(self.root)
        m.reaped = True
        with m.installed(), self.assertRaises(ChildProcessError):
            RUN._signal_measured(m.proc, signal.SIGKILL)
        self.assertFalse(any(e[0] == 'signal' for e in m.events))

    def test_returncode_disables_any_group_access(self):
        m = Model(self.root)
        m.reaped = True
        m.proc.returncode = 0
        with m.installed():
            RUN._signal_measured(m.proc, signal.SIGKILL)
        self.assertEqual(m.events, [])

    def test_wait4_eintr_retries_without_second_signal(self):
        m = Model(self.root)
        with m.installed(), mock.patch.object(os, 'wait4', side_effect=[InterruptedError(), (m.proc.pid, 0, m.usage)]):
            RUN._finish_measured(m.proc)
        self.assertEqual(sum(e[0] == 'signal' for e in m.events), 1)
        self.assertEqual(m.proc.returncode, 0)

    def test_empty_invalid_meter_retains_output_and_fallback(self):
        m = Model(self.root)
        original_spawn = m.spawn
        def spawn(*args):
            proc = original_spawn(*args)
            (self.root / 'meter.json').write_text('')
            return proc
        m.spawn = spawn
        _, (r, out, _) = self.execute(m)
        self.assertEqual(out, b'[{"sequence":0}]')
        self.assertIn('unreadable or invalid', r['failure'])
        self.assertEqual(r['resource_source'], 'supervisor_wait4_fallback')

    def test_signal_and_numeric_exit_status_remain_distinct(self):
        for status, expected in [(signal.SIGTERM, {'signal': signal.SIGTERM}),
                                 (143 << 8, {'exit': 143})]:
            _, (r, _, _) = self.execute(Model(self.root, native=False, raw_status=status))
            self.assertEqual(r['termination'], expected)


    def test_pending_sigint_on_spawn_handoff_unmask_cleans_owned_child(self):
        m = Model(self.root)
        original_mask = m.sigmask
        fired = False
        def sigmask(how, mask):
            nonlocal fired
            result = original_mask(how, mask)
            if how == signal.SIG_SETMASK and not fired:
                fired = True
                raise KeyboardInterrupt()
            return result
        with m.installed(), mock.patch.object(signal, 'pthread_sigmask', side_effect=sigmask), \
                self.assertRaises(KeyboardInterrupt):
            RUN.measure(['CONTROL'], {}, self.root)
        self.assertTrue(m.reaped and m.proc.stdout.closed and m.proc.stderr.closed)

    def test_pending_sigint_on_cleanup_unmask_has_already_reaped_and_closed(self):
        m = Model(self.root)
        original_mask = m.sigmask
        restores = 0
        def sigmask(how, mask):
            nonlocal restores
            result = original_mask(how, mask)
            if how == signal.SIG_SETMASK:
                restores += 1
                if restores == 2:
                    raise KeyboardInterrupt()
            return result
        spool = io.BytesIO()
        with m.installed(), mock.patch.object(signal, 'pthread_sigmask', side_effect=sigmask), \
                mock.patch.object(RUN.tempfile, 'TemporaryFile', side_effect=[spool, io.BytesIO()]), \
                self.assertRaises(KeyboardInterrupt):
            RUN.measure(['CONTROL'], {}, self.root, spool_output=True)
        self.assertTrue(m.reaped and m.closed and spool.closed)
        self.assertTrue(m.proc.stdout.closed and m.proc.stderr.closed)

    def test_echild_during_measure_never_signals_recycled_group(self):
        m = Model(self.root)
        m.reaped = True  # model a competing owner already having reaped
        with m.installed(), self.assertRaises(ChildProcessError):
            RUN.measure(['CONTROL'], {}, self.root)
        self.assertFalse(any(e[0] == 'signal' for e in m.events))
        self.assertTrue(m.proc.stdout.closed and m.proc.stderr.closed and m.closed)



class SpawnUnwindTests(unittest.TestCase):
    def run_failure(self, *, pipe_failure=False, spawn_failure=False, restore_failure=False, fdopen_failure=False):
        m = Model(Path('/unused-control-root'))
        closed, changes = [], []
        def chdir(path):
            changes.append(path)
            if restore_failure and len(changes) == 2:
                raise KeyboardInterrupt()
        pipe_results = [(20, 21), OSError('second pipe') if pipe_failure else (22, 23)]
        fdopen_results = [Pipe(20), KeyboardInterrupt() if fdopen_failure else Pipe(22)]
        with m.installed(), mock.patch.object(RUN, 'spawn_measured', RUN_SPAWN), \
                mock.patch.object(os, 'pipe', side_effect=pipe_results), \
                mock.patch.object(os, 'close', side_effect=closed.append), \
                mock.patch.object(os, 'getcwd', return_value='/before'), \
                mock.patch.object(os, 'chdir', side_effect=chdir), \
                mock.patch.object(os, 'fdopen', side_effect=fdopen_results), \
                mock.patch.object(os, 'posix_spawn', side_effect=OSError('spawn') if spawn_failure else None,
                                  return_value=m.proc.pid) as spawn:
            with self.assertRaises((OSError, KeyboardInterrupt)):
                RUN.spawn_measured(['CONTROL'], {}, '/control')
        if pipe_failure or spawn_failure:
            self.assertFalse(m.reaped)
        else:
            self.assertTrue(m.reaped)
        expected = {20, 21} if pipe_failure else {20, 21, 22, 23}
        if fdopen_failure:
            expected.remove(20)
            self.assertTrue(fdopen_results[0].closed)
        self.assertEqual(set(closed), expected)
        self.assertEqual(len(closed), len(expected))
        if not pipe_failure and not spawn_failure:
            self.assertEqual(spawn.call_args.kwargs['setsigmask'], ())
            self.assertTrue(spawn.call_args.kwargs['setsid'])

    def test_second_pipe_failure_closes_first_pair(self):
        self.run_failure(pipe_failure=True)
    def test_posix_spawn_failure_closes_all_fds(self):
        self.run_failure(spawn_failure=True)
    def test_cwd_restore_interrupt_after_spawn_kills_before_reap(self):
        self.run_failure(restore_failure=True)
    def test_second_fdopen_interrupt_closes_first_stream_and_all_raw_fds(self):
        self.run_failure(fdopen_failure=True)


class FixtureLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='shared-fixture-r2-', dir=TMP)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def fixture(self, m):
        fixture = object.__new__(RUN.Fixture)
        fixture.proc, fixture.log = m.proc, io.BytesIO()
        # Model Popen.wait faithfully for original/R1 negative controls too.
        def wait():
            if m.proc.returncode is None:
                _, status, _ = m.wait4(m.proc.pid, 0)
                m.proc.returncode = os.waitstatus_to_exitcode(status)
            return m.proc.returncode
        m.proc.wait = wait
        return fixture

    def test_repeat_close_never_signals_reaped_group_and_clears_state(self):
        m = Model(self.root)
        fixture = self.fixture(m)
        log = fixture.log
        with m.installed():
            fixture.close()
            fixture.close()
        self.assertIsNone(fixture.proc)
        self.assertIsNone(fixture.log)
        self.assertTrue(log.closed and m.proc.stdout.closed and m.reaped)
        self.assertEqual(m.events[-2:], [('signal', int(signal.SIGKILL)), ('reap', 0, True)])

    def test_prior_popen_returncode_closes_handles_without_any_pid_access(self):
        m = Model(self.root)
        m.reaped, m.proc.returncode = True, 0
        fixture = self.fixture(m)
        with m.installed():
            fixture.close()
        self.assertEqual(m.events, [])
        self.assertIsNone(fixture.proc)
        self.assertIsNone(fixture.log)
        self.assertTrue(m.proc.stdout.closed)

    def test_external_reap_with_unset_returncode_raises_without_signal_and_normalizes(self):
        m = Model(self.root)
        m.reaped = True
        fixture = self.fixture(m)
        with m.installed(), self.assertRaises(ChildProcessError):
            fixture.close()
        self.assertIsNone(fixture.proc)
        with m.installed():
            fixture.close()
        self.assertFalse(any(e[0] == 'signal' for e in m.events))

    def startup(self, *, popen_error=None, selector_error=None, handoff_interrupt=False):
        m = Model(self.root)
        fixture = object.__new__(RUN.Fixture)
        logs = []
        original_open = Path.open
        def path_open(path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            if path.name == 'fixture.stderr':
                logs.append(handle)
            return handle
        def popen(*args, **kwargs):
            self.assertEqual(m.mask, set(), 'fixture must not inherit a blocked SIGINT')
            self.assertTrue(kwargs['start_new_session'])
            if popen_error:
                raise popen_error
            if handoff_interrupt:
                m.handlers[signal.SIGINT](signal.SIGINT, None)
            return m.proc
        if selector_error:
            m.fail_select = selector_error
        with m.installed(), mock.patch.object(Path, 'open', path_open), \
                mock.patch.object(RUN.subprocess, 'Popen', side_effect=popen):
            try:
                RUN.Fixture.__init__(fixture, 'CONTROL-NOT-EXECUTED', {}, self.root)
            except BaseException:
                self.assertIsNone(fixture.proc)
                self.assertIsNone(fixture.log)
                self.assertTrue(all(log.closed for log in logs))
                self.assertIs(m.handlers[signal.SIGINT], signal.default_int_handler)
                if not popen_error:
                    self.assertTrue(m.reaped and m.proc.stdout.closed)
                raise
        return fixture, m, logs

    def test_popen_failure_closes_open_log(self):
        for error in (OSError('control spawn failure'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), self.assertRaises((RuntimeError, KeyboardInterrupt)):
                self.startup(popen_error=error)

    def test_startup_keyboardinterrupt_closes_owned_child_and_log(self):
        with self.assertRaises(KeyboardInterrupt):
            self.startup(selector_error=KeyboardInterrupt())

    def test_startup_eoferror_preserves_existing_exception_wrapper_and_cleans(self):
        with self.assertRaisesRegex(RuntimeError, 'fixture startup failed'):
            self.startup(selector_error=EOFError('control'))

    def test_pending_handoff_sigint_is_raised_after_owned_assignment_then_cleaned(self):
        with self.assertRaises(KeyboardInterrupt):
            self.startup(handoff_interrupt=True)

    def test_normal_startup_contract_timeout_and_response_fields_unchanged(self):
        fixture, m, logs = self.startup()
        self.assertEqual((fixture.url, fixture.port), ('http://127.0.0.1:1', 1234))
        self.assertFalse(m.reaped)
        with m.installed():
            fixture.close()
        self.assertTrue(all(log.closed for log in logs))

    def test_no_scenario_has_normalized_empty_ownership(self):
        with process_guard():
            fixture = RUN.Fixture(None, None, self.root)
            fixture.close()
        self.assertIsNone(fixture.proc)
        self.assertIsNone(fixture.log)

    def test_unsupported_platform_rejects_measure_spawn_fixture_and_run_before_child(self):
        with process_guard(), mock.patch.object(sys, 'platform', 'darwin'):
            for operation in (lambda: RUN.measure(['CONTROL'], {}, self.root),
                              lambda: RUN.spawn_measured(['CONTROL'], {}, self.root),
                              lambda: RUN.Fixture('CONTROL', {}, self.root),
                              lambda: RUN.run(None)):
                with self.assertRaisesRegex(RuntimeError, 'Linux-only'):
                    operation()

    def test_sigchld_rejects_fixture_before_popen(self):
        with process_guard(), mock.patch.object(signal, 'getsignal', return_value=signal.SIG_IGN):
            with self.assertRaisesRegex(RuntimeError, 'SIGCHLD'):
                RUN.Fixture('CONTROL', {}, self.root)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('usage: python shared_runner_linux_ownership_test.py /cache/generated/run.py [unittest names]')
    source = Path(sys.argv.pop(1)).resolve()
    RUN = load(source)
    RUN_SPAWN = RUN.spawn_measured
    with process_guard():
        result = unittest.main(exit=False, verbosity=2).result
    if PROCESS_CALLS:
        raise AssertionError(f'{len(PROCESS_CALLS)} unexpected process calls')
    print('PROCESS_AUDIT: real process operations=0; child processes created=0; child processes remaining=0')
    raise SystemExit(not result.wasSuccessful())
