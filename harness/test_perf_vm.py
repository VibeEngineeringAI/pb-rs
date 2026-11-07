import contextlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

import perf_vm
from perf_vm import ReservedGroupChild


class ReservedGroupTests(unittest.TestCase):
    def child(self, *, released=False, same=True):
        child = object.__new__(ReservedGroupChild)
        child.pid, child.pidfd, child.released = 123, 42, released
        child.same_process = lambda: same
        return child

    @patch('perf_vm.os.killpg')
    @patch('perf_vm.signal.pidfd_send_signal')
    def test_released_or_lost_reservations_cannot_signal(self, pidfd, killpg):
        self.assertFalse(self.child(released=True).signal_group(signal.SIGKILL))
        self.assertFalse(self.child(same=False).signal_group(signal.SIGKILL))
        pidfd.assert_not_called()
        killpg.assert_not_called()

    @patch('perf_vm.os.killpg')
    @patch('perf_vm.signal.pidfd_send_signal', side_effect=ProcessLookupError)
    def test_dead_pidfd_cannot_signal_group(self, pidfd, killpg):
        self.assertFalse(self.child().signal_group(signal.SIGTERM))
        killpg.assert_not_called()

    @patch('perf_vm.os.killpg')
    @patch('perf_vm.signal.pidfd_send_signal')
    def test_signal_checks_reserved_child_before_group_delivery(self, pidfd, killpg):
        self.assertTrue(self.child().signal_group(signal.SIGUSR1))
        pidfd.assert_called_once_with(42, 0)
        killpg.assert_called_once_with(123, signal.SIGUSR1)


class CampaignReportTests(unittest.TestCase):
    def campaign(self, root, change_binary=False, invalid_sample=False):
        before, after = root / 'baseline', root / 'candidate'
        before.write_bytes(b'baseline')
        after.write_bytes(b'candidate')
        calls = 0

        def sample(binary, _case, _directory, **_kwargs):
            nonlocal calls
            calls += 1
            if change_binary and calls == 10:
                after.write_bytes(b'changed candidate')
            value = 1 if binary == after else 10
            return {'metrics': {'user_cpu_ms': value, 'system_cpu_ms': 0,
                               'peak_rss_bytes': value, 'observed_process_wall_ms': value,
                               'stdout_bytes': 1}, 'frames': []}

        argv = ['perf_vm.py', '--baseline', str(before), '--candidate', str(after),
                '--out', str(root / 'results'), '--warmups', '0', '--repetitions', '5']
        with patch.object(sys, 'argv', argv), \
                patch.object(perf_vm.p7, 'NativeChild', perf_vm.p7.NativeChild), \
                patch.object(perf_vm.p7, 'native_capability_probe', perf_vm.p7.native_capability_probe), \
                patch.object(perf_vm.p7, 'TMP', perf_vm.p7.TMP), \
                patch.object(perf_vm.bench, 'campaign_cases', return_value=[{'name': 'probe'}]), \
                patch.object(perf_vm.p7, 'sample', side_effect=sample), \
                patch.object(perf_vm.p7, 'validate', return_value=['invalid sample'] if invalid_sample else []), \
                patch.object(perf_vm.p7, 'compare', return_value=[]):
            perf_vm.main()

    def test_final_binary_change_persists_failure_without_confirmations(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as stream:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'binary changed'):
                self.campaign(root, change_binary=True)
            report = json.loads((root / 'results/report.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertIn('binary changed', report['errors'][0])
            self.assertFalse(report['summary'][0]['cpu_ms']['halving_target_confirmed'])
            self.assertNotIn('"halving_target_confirmed": true', stream.getvalue())

    def test_invalid_sample_persists_failure(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'invalid sample'):
                self.campaign(root, invalid_sample=True)
            report = json.loads((root / 'results/report.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['summary'], [])

    def test_completed_campaign_can_confirm_after_final_provenance(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            self.campaign(root)
            report = json.loads((root / 'results/report.json').read_text())
            self.assertEqual(report['status'], 'completed')
            self.assertTrue(report['summary'][0]['cpu_ms']['halving_target_confirmed'])


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'pidfd_open'), 'Linux pidfd supervision')
class ProfilerStartupTests(unittest.TestCase):
    def test_startup_failures_reap_acquired_child_and_close_log(self):
        base = ReservedGroupChild.__mro__[1]
        for failure in ('log-open', 'perf-spawn'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch.object(perf_vm.p7, 'TMP', root):
                    helper = perf_vm.p7.spawn_helper()
                acquired = []
                original = base.__init__

                def remember(child, *args, **kwargs):
                    original(child, *args, **kwargs)
                    acquired.append(child)

                fd = os.open(os.devnull, os.O_RDWR)
                path = root / ('missing/profile.perf' if failure == 'log-open' else 'profile.perf')
                try:
                    with patch.object(ReservedGroupChild, 'perf_output', path), \
                            patch.object(base, '__init__', remember), \
                            patch.object(perf_vm.subprocess, 'Popen', side_effect=FileNotFoundError('sudo missing')):
                        with self.assertRaises(FileNotFoundError):
                            ReservedGroupChild(helper, ['/bin/sleep', '60'], dict(os.environ),
                                               root, True, fd, fd, fd, (fd,))
                    self.assertEqual(len(acquired), 1)
                    self.assertTrue(acquired[0].closed)
                    self.assertFalse(acquired[0].same_process())
                    if acquired[0].profiler_log is not None:
                        self.assertTrue(acquired[0].profiler_log.closed)
                finally:
                    for child in acquired:
                        child.close(abort=True)
                    os.close(fd)


if __name__ == '__main__':
    unittest.main()
