#!/usr/bin/env python3
"""Explicit, serialized final-candidate campaign. Run under flock and taskset."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'harness/results/perf3'
OLD = 'bin/pb-perf2-final'
NEW = 'bin/pb-perf3-final'
jobs = []

def stress(name, profile, cases=(), go=False, repetitions=5, warmup=1, sink='null'):
    command = ['python3', 'harness/stress.py', '--go', 'bin/pb-go' if go else OLD,
               '--rust', NEW, '--out', str(OUT/name), '--profile', profile,
               '--repetitions', str(repetitions), '--warmup', str(warmup),
               '--sink', sink, '--seed', '48121', '--calibrate']
    if not go:
        command += ['--reference-label', 'baseline', '--candidate-label', 'candidate']
    for case in cases:
        command += ['--case', case]
    jobs.append((name, command, False))

stress('final-scale', 'scale', ['tiny-1m', 'escaped-1m', 'stream-1gib', 'large-item-8mib'])
stress('final-large', 'large', ['large-items-8x8mib', 'large-items-gzip-8x8mib'])
stress('final-go-large', 'large', go=True)
stress('final-go-small', 'scale', ['tiny-1m', 'escaped-1m'], go=True)
stress('final-go-transport', 'transport', go=True, repetitions=1, warmup=0, sink='pipe')
stress('final-go-smoke', 'smoke', go=True, repetitions=1, warmup=0, sink='pipe')
stress('final-go-10gib', 'extended', ['stream-10gib'], go=True)
jobs.append(('final-cached', ['python3', 'harness/perf3_cached.py', 'run',
    '--manifest', str(OUT/'cached-1gib/manifest.json'), '--go', 'bin/pb-go',
    '--reference', OLD, '--candidate', NEW, '--out', str(OUT/'final-cached'),
    '--repetitions', '5', '--warmup', '1', '--seed', '48122'], False))
jobs.append(('final-ingest', ['python3', 'harness/ingest_perf.py', 'run',
    '--baseline', OLD, '--candidate', NEW, '--out', str(OUT/'final-ingest'),
    '--work-dir', str(OUT/'ingest-work'), '--records', '20000', '--record-bytes', '500',
    '--files', '4', '--batch-size', '1000', '--repetitions', '5', '--warmup', '1',
    '--seed', '48123', '--probe-records', '1000', '--probe-fail-upload', '1',
    '--probe-repetitions', '1', '--probe-reject-over-rows', '100',
    '--gate-metrics', 'proc_io_syscw,cpu_ms,wall_ms,peak_rss_bytes'], True))
for name, duration, shape, flags in [
        ('final-follow', '60', 'fixed', []),
        ('final-retry', '3', 'mixed', ['--fail-upload', '2']),
        ('final-ack-loss', '3', 'mixed', ['--ack-loss-upload', '2']),
        ('final-split', '3', 'mixed', ['--reject-over-rows', '10'])]:
    jobs.append((name, ['python3', 'harness/stress_ingest.py', '--binary', NEW,
        '--meter', 'bin/pb-stress-meter', '--out', str(OUT/name), '--duration', duration,
        '--rate', '5000' if duration == '60' else '1000', '--record-bytes', '64',
        '--shape', shape, '--batch-size', '1000'] + flags, True))

if __name__ == '__main__':
    selection = set(sys.argv[1:])
    unknown = selection - {name for name, _, _ in jobs}
    if unknown:
        raise SystemExit(f'Unknown job selection: {sorted(unknown)}')
    failures = []
    (OUT/'ingest-work').mkdir(exist_ok=True)
    for name, command, allow_gate_failure in jobs:
        if selection and name not in selection:
            continue
        status_path = OUT/(name+'-execution.json')
        if status_path.exists():
            raise SystemExit(f'Refusing to overwrite {status_path}')
        log = OUT/(name+'.log')
        print(f'START {name}', flush=True)
        start = time.time()
        with log.open('xb') as stream:
            result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        status = dict(name=name, command=command, exit_code=result.returncode,
                      elapsed_seconds=time.time()-start, log=str(log.relative_to(ROOT)),
                      log_sha256=hashlib.sha256(log.read_bytes()).hexdigest())
        status_path.write_text(json.dumps(status, indent=2)+'\n')
        print(f'END {name}: {result.returncode} ({status["elapsed_seconds"]:.1f}s)', flush=True)
        if result.returncode:
            failures.append(name)
            if not allow_gate_failure:
                raise SystemExit(result.returncode)
    if failures:
        print('FAILED runs (inspect compatibility and pacing/resource gates separately):', failures, flush=True)
        raise SystemExit(1)
