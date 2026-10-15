"""Real pipe witnesses and below-capacity controls for CW1."""

from pathlib import Path
import signal
import sys
import tempfile
import unittest

import phase6_blocked_writer as followup


class BlockedWriterTests(unittest.TestCase):
    def execute(self, script, case):
        p = followup.p6
        with tempfile.TemporaryDirectory(dir=p.TMP) as root:
            root = Path(root)
            result = p.execute([sys.executable, '-c', script], p.env_for(root), root, case, None, root)
            result['captured'] = (root / 'stdout.bin').read_bytes()
            return result

    def test_actual_undrained_writer_witness_precedes_resume(self):
        r = self.execute('import os;os.write(1,b"a"*16384)', dict(pause_stdout=True, timeout=3,
            pipe_probe=dict(capacity=4096, expected_output_bytes=16384, full_ms=100), actions=[
                dict(when=dict(pipe_blocked=True), do=dict(assert_pipe_blocked=True)),
                dict(when=dict(start=True), do=dict(resume_stdout=True))]))
        self.assertIsNone(r['failure'])
        self.assertEqual(r['captured'], b'a' * 16384)
        proof = r['pipe_probe']['proof']
        self.assertEqual((r['pipe_probe']['capacity'], proof['pending_bytes'], proof['drained_bytes']), (4096, 4096, 0))
        self.assertTrue(proof['writer_waits'])
        self.assertTrue(all(w['fd'] == 1 and w['wchan'] in ('pipe_write', 'anon_pipe_write') for w in proof['writer_waits']))
        self.assertLessEqual(proof['t_ns'], r['actions'][0]['t_ns'])

    def test_below_capacity_cannot_prove_blockage(self):
        r = self.execute('import os,time;os.write(1,b"a"*128);time.sleep(5)', dict(pause_stdout=True, timeout=3,
            pipe_probe=dict(capacity=4096, expected_output_bytes=128, full_ms=100), actions=[
                dict(when=dict(pipe_pending_bytes=128), do=dict(assert_not_blocked=True)),
                dict(when=dict(start=True), do=dict(signal='TERM'))]))
        self.assertIsNone(r['failure'])
        self.assertEqual(r['raw_wait_status'], signal.SIGTERM)
        self.assertEqual(r['captured'], b'a' * 128)
        self.assertFalse(r['pipe_probe']['blocked_proven'])
        self.assertTrue(r['pipe_probe']['negative_control_rejected'])

    def test_wrongly_requested_blockage_is_missed_action_not_pass(self):
        r = self.execute('import os,time;os.write(1,b"a"*128);time.sleep(5)', dict(pause_stdout=True, timeout=.4,
            pipe_probe=dict(capacity=4096, expected_output_bytes=128, full_ms=100), actions=[
                dict(when=dict(pipe_blocked=True), do=dict(signal='TERM'), within_ms=100)]))
        self.assertIsNotNone(r['failure'])
        self.assertFalse(r['pipe_probe']['blocked_proven'])
        self.assertFalse(r['actions'][0]['met'])

    def test_new_inventory_does_not_mutate_historical_cases(self):
        p = followup.p6
        before = p.run.digest(p.run.canonical(p.corpus()))
        cases = followup.cases()
        self.assertEqual(len(cases), 4)
        self.assertEqual(before, p.run.digest(p.run.canonical(p.corpus())))
        self.assertTrue(all(c['fixture']['phase6']['streams'][c['dataset']]['schema'] == 'wide' for c in cases))
        self.assertLess(len(followup.wide_output(128)), 4096)
        self.assertGreater(len(followup.wide_output(16384)), 4096)


if __name__ == '__main__':
    unittest.main()
