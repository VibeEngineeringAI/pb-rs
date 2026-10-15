"""Scratch defaults follow the user's home without changing campaign admission."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PortablePathsTests(unittest.TestCase):
    def test_scratch_and_shared_lock_defaults_follow_another_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            code = '''
import json
import phase6 as collector
import phase6_workloads as workloads
import phase6_build_cost as build
import phase6_duration_gate as duration
print(json.dumps(dict(collector_tmp=str(collector.TMP),
    workloads_tmp=str(workloads.TMP), workloads_lock=str(workloads.PERFORMANCE_LOCK),
    build_cache=str(build.CACHE), build_tmp=str(build.TMP), build_lock=str(build.LOCK),
    duration_scratch=duration.SCRATCH)))
'''
            env = dict(os.environ, HOME=str(home))
            process = subprocess.run([sys.executable, '-c', code],
                                     cwd=Path(__file__).resolve().parent, env=env,
                                     text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(process.stdout), dict(
                collector_tmp=str(home / '.cache/pb-phase6/tmp'),
                workloads_tmp=str(home / '.cache/pb-phase6/tmp'),
                workloads_lock=str(home / '.cache/pb-rs-performance.lock'),
                build_cache=str(home / '.cache/pb-phase6/build-cost-preparation-v1'),
                build_tmp=str(home / '.cache/pb-phase6/tmp'),
                build_lock=str(home / '.cache/pb-rs-performance.lock'),
                duration_scratch=str(home / '.cache/pb-phase6/duration-gate-v1')))


if __name__ == '__main__':
    unittest.main()
