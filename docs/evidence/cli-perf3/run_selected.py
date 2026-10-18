#!/usr/bin/env python3
"""Repeat the declared campaign for a newly selected, immutable candidate.

Run under the shared performance flock and CPU affinity. The previous campaign
and every failed gate remain untouched. This script does not build anything.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

import run_final


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, default=Path(run_final.OLD))
    parser.add_argument('--go', type=Path, default=Path('bin/pb-go'))
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--job', action='append', default=[])
    args = parser.parse_args()
    if not args.prefix.replace('-', '').isalnum() or args.prefix == 'final':
        parser.error('use a fresh alphanumeric/hyphen prefix other than final')
    root, out = run_final.ROOT, run_final.OUT
    candidate = args.candidate.resolve()
    baseline, go = args.baseline.resolve(), args.go.resolve()
    identities = {str(path): sha(path) for path in (candidate, baseline, go)}
    manifest = json.loads(args.manifest.read_text())
    assert sha(candidate) == manifest['sha256']
    sources = manifest['sources']
    assert all(sha(root / name) == digest for name, digest in sources.items())
    selected = set(args.job)
    unknown = selected - {name for name, _, _ in run_final.jobs}
    if unknown:
        parser.error(f'unknown jobs: {sorted(unknown)}')
    failures = []
    work = out / (args.prefix + '-ingest-work')
    work.mkdir(exist_ok=True)
    for old_name, old_command, allow_gate_failure in run_final.jobs:
        if selected and old_name not in selected:
            continue
        name = args.prefix + old_name.removeprefix('final')
        command = [str(candidate) if part == run_final.NEW else
                   str(baseline) if part == run_final.OLD else
                   str(go) if part == 'bin/pb-go' else
                   str(out / name) if part == str(out / old_name) else
                   str(work) if part == str(out / 'ingest-work') else part
                   for part in old_command]
        log, status = out / (name + '.log'), out / (name + '-execution.json')
        if log.exists() or status.exists() or (out / name).exists():
            raise SystemExit(f'Refusing to overwrite {name}')
        print('START', name, flush=True)
        start = time.time()
        with log.open('xb') as stream:
            process = subprocess.run(command, cwd=root, stdout=stream,
                                     stderr=subprocess.STDOUT)
        status.write_text(json.dumps(dict(name=name, command=command,
            candidate_sha256=sha(candidate), manifest=str(args.manifest),
            binary_identities=identities,
            manifest_sha256=sha(args.manifest), exit_code=process.returncode,
            elapsed_seconds=time.time()-start, log=str(log.relative_to(root)),
            log_sha256=sha(log)), indent=2)+'\n')
        print('END', name, process.returncode, flush=True)
        assert sha(candidate) == manifest['sha256']
        assert all(sha(Path(path)) == digest for path, digest in identities.items())
        assert all(sha(root / path) == digest for path, digest in sources.items())
        if process.returncode:
            failures.append(name)
            if not allow_gate_failure:
                return process.returncode
    if failures:
        print('FAILED runs (inspect compatibility and resource/pacing gates):', failures,
              flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
