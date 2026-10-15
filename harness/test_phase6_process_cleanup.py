#!/usr/bin/env python3
"""Real Linux process groups must not survive finite correctness captures."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest

import phase6 as p6


DESCENDANT = r'''
import json, os, sys, time
from pathlib import Path
ready_r, ready_w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(ready_r)
    if sys.argv[2] == 'redirected':
        fd = os.open(os.devnull, os.O_RDWR)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
    stat = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
    Path(sys.argv[1]).write_text(json.dumps(dict(pid=os.getpid(), leader=os.getppid(),
                                               start=stat[19])))
    os.write(ready_w, b'x')
    os.close(ready_w)
    time.sleep(60)
    os._exit(0)
os.close(ready_w)
if os.read(ready_r, 1) != b'x':
    raise RuntimeError('descendant did not become ready')
os.close(ready_r)
os.write(1, b'leader finished\n')
if sys.argv[3] == 'timeout':
    time.sleep(60)
os._exit(0)
'''


@unittest.skipUnless(sys.platform.startswith('linux') and hasattr(os, 'WNOWAIT'),
                     'Linux process groups and /proc identity checks')
class ProcessCleanupTests(unittest.TestCase):
    def setUp(self):
        p6.TMP.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix='process-cleanup-', dir=p6.TMP)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def execute(self, script, *, args=(), timeout=1):
        result = p6.execute([sys.executable, '-c', script, *args], p6.env_for(self.root),
                            self.root, dict(timeout=timeout), None, self.root)
        return result, (self.root / 'stdout.bin').read_bytes(), (self.root / 'stderr.bin').read_bytes()

    @staticmethod
    def descendant_running(identity):
        try:
            stat = Path(f'/proc/{identity["pid"]}/stat').read_text().rsplit(')', 1)[1].split()
        except FileNotFoundError:
            return False
        # Treat a zombie as terminated; this process does not own the orphan's
        # wait status. The start time keeps test cleanup away from reused PIDs.
        return stat[19] == identity['start'] and stat[0] not in ('Z', 'X')

    @contextmanager
    def descendant_capture(self, *, redirected=False, timeout=False):
        record = self.root / 'descendant.json'
        try:
            result, stdout, stderr = self.execute(DESCENDANT,
                args=(str(record), 'redirected' if redirected else 'held',
                      'timeout' if timeout else 'exit'), timeout=1)
            identity = json.loads(record.read_text())
            yield result, stdout, stderr, identity
        finally:
            # Keep even a regressed capture or failed assertion from leaving a
            # sleeping probe behind. The child records itself before its parent
            # exits or starts the timeout wait.
            if record.exists():
                identity = json.loads(record.read_text())
                if self.descendant_running(identity):
                    try:
                        os.kill(identity['pid'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def assert_descendant_stopped(self, identity):
        deadline = time.monotonic() + 2
        while self.descendant_running(identity) and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(self.descendant_running(identity), 'capture left a live descendant')
        with self.assertRaises(ChildProcessError):
            os.waitpid(identity['leader'], os.WNOHANG)

    def test_exited_leader_with_descendant_holding_pipes(self):
        with self.descendant_capture() as (result, stdout, stderr, identity):
            self.assertEqual(result['failure'], 'descendant-held-pipe')
            self.assertEqual(result['termination'], {'exit': 0})
            self.assertEqual(result['raw_wait_status'], 0)
            self.assertEqual((stdout, stderr), (b'leader finished\n', b''))
            self.assert_descendant_stopped(identity)

    def test_exited_leader_with_descendant_redirecting_stdio(self):
        with self.descendant_capture(redirected=True) as (result, stdout, stderr, identity):
            self.assertIsNone(result['failure'])
            self.assertEqual(result['termination'], {'exit': 0})
            self.assertEqual(result['raw_wait_status'], 0)
            self.assertEqual((stdout, stderr), (b'leader finished\n', b''))
            self.assert_descendant_stopped(identity)

    def test_timeout_kills_leader_and_descendant(self):
        with self.descendant_capture(timeout=True) as (result, stdout, stderr, identity):
            self.assertEqual(result['failure'], 'timeout')
            self.assertEqual(result['termination'], {'signal': signal.SIGKILL})
            self.assertEqual(result['raw_wait_status'], signal.SIGKILL)
            self.assertEqual((stdout, stderr), (b'leader finished\n', b''))
            self.assert_descendant_stopped(identity)

    def test_ordinary_direct_child_retains_output_and_exit_status(self):
        result, stdout, stderr = self.execute(
            'import os; os.write(1, b"out\\n"); os.write(2, b"err\\n"); os._exit(19)')
        self.assertIsNone(result['failure'])
        self.assertEqual(result['termination'], {'exit': 19})
        self.assertEqual(result['raw_wait_status'], 19 << 8)
        self.assertEqual((stdout, stderr), (b'out\n', b'err\n'))

    def test_direct_child_signal_status_is_not_replaced_by_cleanup(self):
        result, stdout, stderr = self.execute(
            'import os, signal; os.kill(os.getpid(), signal.SIGTERM)')
        self.assertIsNone(result['failure'])
        self.assertEqual(result['termination'], {'signal': signal.SIGTERM})
        self.assertEqual(result['raw_wait_status'], signal.SIGTERM)
        self.assertEqual((stdout, stderr), (b'', b''))


if __name__ == '__main__':
    unittest.main()
