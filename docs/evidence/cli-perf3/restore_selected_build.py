#!/usr/bin/env python3
"""Restore the ordinary Cargo release output after the isolated experiment.

Run after captures under the shared performance flock. Frozen bin/ files are
never overwritten. Require exact selected source and executable identities.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import argparse

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).with_name('restored-build.json')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force-package-rebuild', action='store_true')
    args = parser.parse_args()
    if args.force_package_rebuild:
        OUT = OUT.with_name('restored-build-forced.json')
    if OUT.exists():
        raise SystemExit('Refusing to overwrite build restoration evidence')
    manifest = json.loads(OUT.with_name('final-candidate.json').read_text())
    assert all(sha(ROOT / name) == digest for name, digest in manifest['sources'].items())
    command = ['/home/ajs/.cargo/bin/cargo', '+1.96.0', 'build', '--locked', '--release']
    env = dict(os.environ, PB_VERSION='v0.0.0-harness', PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN='')
    log = ROOT / ('harness/results/perf3/restore-selected-build-forced.log'
                  if args.force_package_rebuild else
                  'harness/results/perf3/restore-selected-build.log')
    start = time.time()
    before = sha(ROOT / 'target/release/pb')
    clean_result = None
    with log.open('xb') as stream:
        if args.force_package_rebuild:
            clean_result = subprocess.run(command[:2] + ['clean', '--release', '-p', 'pb'],
                cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
            if clean_result.returncode:
                raise SystemExit(clean_result.returncode)
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream,
                                stderr=subprocess.STDOUT)
    after = sha(ROOT / 'target/release/pb')
    source_match = all(sha(ROOT / name) == digest for name, digest in manifest['sources'].items())
    passed = result.returncode == 0 and after == manifest['sha256'] and source_match
    OUT.write_text(json.dumps(dict(command=command, exit_code=result.returncode,
        package_clean_exit=None if clean_result is None else clean_result.returncode,
        elapsed_seconds=time.time()-start, before_sha256=before, after_sha256=after,
        expected_sha256=manifest['sha256'], source_match=source_match, passed=passed,
        log=str(log.relative_to(ROOT)), log_sha256=sha(log)), indent=2)+'\n')
    print(json.dumps({'passed':passed,'sha256':after}), flush=True)
    raise SystemExit(0 if passed else 1)
