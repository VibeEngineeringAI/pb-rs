"""Keep device comparison exceptions narrower than the visible contract."""
import unittest

import phase7_device


class DeviceComparisonTests(unittest.TestCase):
    def result(self, line, case='device-success', stderr=b''):
        cells = [[char, 'cyan', 'default', False] for char in line]
        frame = {'cells': [cells], 'scrollback': [], 'cursor': [3, 4]}
        return {'case': {'name': case}, 'stderr': stderr,
                'frames': [{'frame': frame}], 'final_frame': frame}

    def test_only_waiting_spinner_phase_varies(self):
        text = '  ⣽  Waiting for authorization... [Ctrl+C to abort]'
        original = self.result(text)
        result = phase7_device.comparable(original)
        self.assertEqual(result['final_frame']['cells'][0][2], ['⣾', 'cyan', 'default', False])
        self.assertEqual(result['final_frame']['cursor'], [3, 4])
        self.assertEqual(original['final_frame']['cells'][0][2][0], '⣽')
        unrelated = self.result('  ⣽ Authorization successful!')
        self.assertEqual(phase7_device.comparable(unrelated), unrelated)

    def test_success_cannot_compare_as_waiting(self):
        waiting = self.result('  ⣽  Waiting for authorization... [Ctrl+C to abort]')
        success = self.result('  ✓ Authorization successful!')
        self.assertNotEqual(phase7_device.comparable(waiting), phase7_device.comparable(success))

    def test_cancel_error_exception_is_case_scoped(self):
        aborted = b'Error: cloud device login aborted\n'
        expired = b'Error: cloud device login expired or timed out\n'
        self.assertEqual(phase7_device.comparable(self.result('', 'device-cancel', aborted)),
                         phase7_device.comparable(self.result('', 'device-cancel', expired)))
        self.assertNotEqual(phase7_device.comparable(self.result('', 'device-timeout', aborted)),
                            phase7_device.comparable(self.result('', 'device-timeout', expired)))


if __name__ == '__main__':
    unittest.main()
