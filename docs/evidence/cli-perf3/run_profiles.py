#!/usr/bin/env python3
"""Capture the four predeclared attribution runs, under the performance flock."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
PLAN = Path(__file__).with_name('profile_plan.json')

if __name__ == '__main__':
    for run in json.loads(PLAN.read_text())['runs']:
        # The caller holds the lock and affinity for the entire campaign.
        command = run['command'].split(' python3 ', 1)[1]
        import shlex
        argv = ['python3'] + shlex.split(command)
        log = ROOT/'harness/results/perf3'/('profile-'+run['id']+'.log')
        print('START', run['id'], flush=True)
        start = time.time()
        with log.open('xb') as stream:
            process = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        result = dict(command=argv, exit_code=process.returncode,
                      elapsed_seconds=time.time()-start,
                      log=str(log.relative_to(ROOT)),
                      log_sha256=hashlib.sha256(log.read_bytes()).hexdigest())
        log.with_suffix('.json').write_text(json.dumps(result, indent=2)+'\n')
        print('END', run['id'], process.returncode, flush=True)
        if process.returncode:
            raise SystemExit(process.returncode)
        subprocess.run(shlex.split(run['summary']), cwd=ROOT, check=True)
