"""Regression tests for isolated harness unittest discovery."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import run_tests as runner


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def file(self, name, content=''):
        path = self.directory / name
        path.write_text(content)
        return path

    def run_runner(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.run_tests(self.directory)

    def test_inventory_is_sorted_and_excludes_directories_and_other_files(self):
        second = self.file('test_z.py')
        first = self.file('test_a.py')
        self.file('helper.py')
        (self.directory / 'test_directory.py').mkdir()
        nested = self.directory / 'nested'
        nested.mkdir()
        (nested / 'test_nested.py').write_text('')
        self.assertEqual(runner.test_files(self.directory), [first, second])

    def test_each_file_uses_separate_unittest_discovery_and_inherits_environment(self):
        self.file('test_z.py')
        self.file('test_a.py')
        with mock.patch.object(runner.subprocess, 'run',
                               return_value=subprocess.CompletedProcess([], 0)) as run, \
                mock.patch.dict(os.environ, {'PB_GO_ORACLE': '/oracle',
                                             'PB_PHASE3_FIXTURE': '/fixture',
                                             'PYTHONPATH': '/existing-python-path'}):
            self.assertEqual(self.run_runner(), 0)
        self.assertEqual(run.call_args_list, [
            mock.call([sys.executable, '-m', 'unittest', 'discover',
                       '-s', str(self.directory), '-p', name, '-v'], check=False)
            for name in ('test_a.py', 'test_z.py')])
        # Omitting env/cwd preserves the caller's fixtures, oracle and import path.
        self.assertTrue(all('env' not in call.kwargs and 'cwd' not in call.kwargs
                            for call in run.call_args_list))

    def test_failure_is_reported_after_remaining_modules_run(self):
        self.file('test_a.py')
        self.file('test_b.py')
        with mock.patch.object(runner.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 7),
                subprocess.CompletedProcess([], 0)]) as run:
            self.assertEqual(self.run_runner(), 1)
        self.assertEqual(run.call_count, 2)

    def test_empty_inventory_fails(self):
        with contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(runner.subprocess, 'run') as run:
            self.assertEqual(self.run_runner(), 1)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
