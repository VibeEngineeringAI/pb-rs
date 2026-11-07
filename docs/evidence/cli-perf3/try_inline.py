#!/usr/bin/env python3
"""Build and measure one isolated four-attribute experiment under the shared lock."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT/'target/perf3-inline-source'
OUT = ROOT/'harness/results/perf3/inline-experiment'
PATCH = Path(__file__).with_name('inline-experiment.patch')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(name, argv, cwd=ROOT, env=None):
    start = time.time()
    print('START', name, flush=True)
    log = OUT/(name+'.log')
    with log.open('xb') as stream:
        process = subprocess.run(argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
    (OUT/(name+'.json')).write_text(json.dumps(dict(command=[str(a) for a in argv],
        cwd=str(cwd), exit_code=process.returncode, elapsed_seconds=time.time()-start,
        log_sha256=sha(log)), indent=2)+'\n')
    print('END', name, process.returncode, flush=True)
    if process.returncode:
        raise SystemExit(process.returncode)

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=False)
    frozen = json.loads((ROOT/'docs/evidence/cli-perf3/final-candidate.json').read_text())
    assert all(sha(ROOT/f)==digest for f, digest in frozen['sources'].items())
    run('escaping-profile', ['python3', 'docs/evidence/cli-perf3/profile_stress_rows.py',
        '--out', str(ROOT/'harness/results/perf3/profiles-stress-escaping')])
    SOURCE.mkdir(exist_ok=False)
    for name in ('Cargo.toml', 'Cargo.lock', 'build.rs'):
        shutil.copy2(ROOT/name, SOURCE/name)
    for name in ('src', 'tests'):
        shutil.copytree(ROOT/name, SOURCE/name)
    style = Path('docs/evidence/phase7/style-byte-oracle-fixtures.tsv')
    (SOURCE/style).parent.mkdir(parents=True)
    shutil.copy2(ROOT/style, SOURCE/style)
    run('apply', ['patch', '--batch', '-p1', '-i', str(PATCH)], cwd=SOURCE)
    env = dict(os.environ, PB_VERSION='v0.0.0-harness', PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN='')
    run('build', ['/home/ajs/.cargo/bin/cargo', '+1.96.0', 'build', '--locked', '--release',
                 '--target-dir', str(ROOT/'target')], cwd=SOURCE, env=env)
    binary = ROOT/'bin/pb-perf3-inline-1'
    if binary.exists():
        raise SystemExit('Refusing to overwrite frozen experiment binary')
    shutil.copy2(ROOT/'target/release/pb', binary)
    (OUT/'candidate.json').write_text(json.dumps(dict(binary=str(binary), sha256=sha(binary),
        baseline=frozen['binary'], baseline_sha256=frozen['sha256'],
        patch_sha256=sha(PATCH), source_root=str(SOURCE),
        sources={f:sha(SOURCE/f) for f in frozen['sources']},
        build={'toolchain':'1.96.0','profile':'release','locked':True,
               'PB_VERSION':'v0.0.0-harness','PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN':''}), indent=2)+'\n')
    run('large', ['python3', 'harness/stress.py', '--go', 'bin/pb-perf3-final',
        '--rust', str(binary), '--reference-label', 'baseline', '--candidate-label', 'candidate',
        '--out', str(ROOT/'harness/results/perf3/inline-on-off-large'), '--profile', 'large',
        '--repetitions', '5', '--warmup', '1', '--seed', '48132', '--sink', 'null', '--calibrate'])
