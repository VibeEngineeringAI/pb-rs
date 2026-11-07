import signal
import unittest
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()
