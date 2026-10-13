"""Profiler wrappers must preserve arguments, exits and exact CLI contracts."""
from pathlib import Path
import subprocess
import tempfile
import unittest

import profile_external as profile


class ExternalProfilerTests(unittest.TestCase):
    def test_wrapper_preserves_literal_arguments_and_exit_status(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            child = directory / 'child with spaces'
            child.write_text('#!/usr/bin/python3\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\nsys.exit(7)\n')
            child.chmod(0o700)
            launcher = profile.wrapper(directory / 'launch', [child, '$(touch NEVER)', "quote'and space"])
            result = subprocess.run([launcher, 'tail argument'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout, '["$(touch NEVER)", "quote\'and space", "tail argument"]\n')

    def test_contract_mismatch_invalidates_successful_capture(self):
        result = {'contract': {'stdout_sha256': 'different'}, 'valid': True, 'issues': []}
        profile.validate(result, {'stdout_sha256': 'expected'})
        self.assertFalse(result['valid'])
        self.assertIn('contract differs', result['issues'][0])

    def test_absent_empty_ambiguous_or_symlink_profiles_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with self.assertRaises(ValueError):
                profile.artifacts('perf', directory)
            path = directory / 'perf.data'
            path.touch()
            with self.assertRaises(ValueError):
                profile.artifacts('perf', directory)
            path.write_bytes(b'capture')
            self.assertEqual(profile.artifacts('perf', directory)[0]['size_bytes'], 7)
            path.unlink()
            path.symlink_to('/etc/hostname')
            with self.assertRaises(ValueError):
                profile.artifacts('perf', directory)
            (directory / 'heaptrack.gz').write_bytes(b'a')
            (directory / 'heaptrack.zst').write_bytes(b'b')
            with self.assertRaises(ValueError):
                profile.artifacts('heaptrack', directory)

    def test_raw_write_traces_never_request_buffer_contents(self):
        argv = profile.command('strace-writes', '/usr/bin/strace', Path('/pb'), Path('/out'), 'cycles:u')
        self.assertIn('raw=write,writev', argv)
        self.assertIn('-f', argv)

    def test_heaptrack_preload_targets_only_the_cli(self):
        argv = profile.command('heaptrack', '/lib/preload.so', Path('/pb'), Path('/out'), 'cycles:u')
        self.assertEqual(argv, ['/usr/bin/env', 'LD_PRELOAD=/lib/preload.so',
                                'DUMP_HEAPTRACK_OUTPUT=/out/heaptrack.raw', '/pb'])

    def test_heaptrack_interpreter_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'heaptrack.raw').write_bytes(b'raw')
            with self.assertRaises(ValueError):
                profile.analyze('heaptrack', '/bin/true',
                                profile.artifacts('heaptrack', directory)[0], directory,
                                interpreter='/bin/false')

    def test_trace_counts_pair_resumes_and_distinguish_stdout(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'writes.trace'
            path.write_text('11 write(0x1, 0xff, 0x4 <unfinished ...>\n'
                            '12 write(0x4, 0xaa, 0x8) = 0x8\n'
                            '11 <... write resumed>) = 0x4\n'
                            '11 writev(0x1, 0xbb, 0x2) = -1 EPIPE (Broken pipe)\n'
                            '12 --- SIGURG {si_signo=SIGURG} ---\n')
            counts = profile.write_counts(path)
            self.assertEqual(counts['1'], {'calls': 2, 'completed': 2, 'bytes': 4, 'errors': 1})
            self.assertEqual(counts['4']['bytes'], 8)
            path.write_text('11 <... write resumed>) = 0x4\n')
            with self.assertRaises(ValueError):
                profile.write_counts(path)
            path.write_text('11 write(0x1, 0xff, 0x4 <unfinished ...>\n')
            with self.assertRaises(ValueError):
                profile.write_counts(path)

    def test_failed_analysis_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            path = directory / 'perf.data'
            path.write_bytes(b'not a profile')
            with self.assertRaises(ValueError):
                profile.analyze('perf', '/bin/false', profile.artifacts('perf', directory)[0], directory)


if __name__ == '__main__':
    unittest.main()
