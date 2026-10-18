#!/usr/bin/env python3
"""Queued, interrupted steady-state RSS attribution; never a performance gate.

Example, only after benchmark/profile queues are idle::

    python3 docs/evidence/cli-perf3/rss_attribution.py \
        --baseline bin/pb-perf2-final --final bin/pb-perf3-final \
        --out harness/results/perf3/rss-attribution

Three serial paired rounds use paced JSON from the original stress fixture.
The direct child writes to /dev/null. At approximately two seconds, /proc
status/smaps identify resident mappings; then SIGINT intentionally stops the
child. There is no output parity, native lifetime peak or throughput claim.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import random
import re
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'harness'))
import run as harness
import stress


CONFIG = {'rows': 1_000_000, 'row_bytes': 64, 'shape': 'tiny',
          'chunk_bytes': 65_536, 'chunk_delay_ms': 20, 'flush': True}
SCOPE = ('Interrupted steady-state mapping diagnostic. Direct CLI stdout=/dev/null; '
         'no output parity, native lifetime peak, leak, or throughput claim. '
         'Snapshots from distinct proc files are not atomic.')
KB_FIELDS = ('Size', 'Rss', 'Pss', 'Shared_Clean', 'Shared_Dirty',
             'Private_Clean', 'Private_Dirty', 'Referenced', 'Anonymous',
             'AnonHugePages', 'Swap', 'SwapPss')
HEADER = re.compile(r'^([0-9a-f]+)-([0-9a-f]+)\s+(\S+)\s+([0-9a-f]+)\s+(\S+)\s+(\d+)(?:\s+(.*))?$')


def identity(pid):
    raw = Path(f'/proc/{pid}/stat').read_text()
    _, tail = raw.rsplit(')', 1)
    values = tail.split()
    return {'pid': pid, 'state': values[0], 'ppid': int(values[1]), 'start_ticks': int(values[19])}


def status_memory(raw):
    result = {}
    for line in raw.splitlines():
        key, _, value = line.partition(':')
        parts = value.split()
        if key in ('VmRSS', 'VmHWM', 'RssAnon', 'RssFile', 'RssShmem', 'VmSize', 'VmStk') and len(parts) == 2 and parts[1] == 'kB':
            result[key + '_bytes'] = int(parts[0]) * 1024
        elif key == 'Threads' and parts:
            result[key] = int(parts[0])
    return result


def category(path, binary):
    clean = path.removesuffix(' (deleted)')
    if clean == str(binary):
        return 'executable_file'
    if clean == '[heap]':
        return 'labelled_heap'
    if clean.startswith('[stack'):
        return 'labelled_stack'
    if clean.startswith('/'):
        return 'shared_library' if '.so' in Path(clean).name else 'other_file'
    if not clean or clean.startswith('[anon'):
        return 'anonymous_mapping'
    return 'kernel_or_other_mapping'


def parse_smaps(raw, binary):
    mappings = []
    current = None
    for line in raw.splitlines():
        match = HEADER.fullmatch(line)
        if match:
            current = {'start': match[1], 'end': match[2], 'permissions': match[3],
                       'offset': match[4], 'device': match[5], 'inode': int(match[6]),
                       'path': match[7] or '', 'bytes': {}}
            current['category'] = category(current['path'], binary)
            mappings.append(current)
        elif current is not None:
            key, _, value = line.partition(':')
            parts = value.split()
            if key in KB_FIELDS and len(parts) == 2 and parts[1] == 'kB':
                current['bytes'][key] = int(parts[0]) * 1024
            elif key == 'VmFlags':
                current['vm_flags'] = parts
    if not mappings or any('Rss' not in mapping['bytes'] for mapping in mappings):
        raise ValueError('smaps missing mappings or RSS fields')
    totals = {}
    for mapping in mappings:
        summary = totals.setdefault(mapping['category'], {key: 0 for key in KB_FIELDS})
        for key in KB_FIELDS:
            summary[key] += mapping['bytes'].get(key, 0)
    return {'mapping_categories_bytes': totals, 'mappings': mappings,
            'total_bytes': {key: sum(mapping['bytes'].get(key, 0) for mapping in mappings) for key in KB_FIELDS},
            'category_limits': ('labelled_stack covers mappings explicitly named [stack*] only. '
                                'Anonymous mappings can include pthread stacks and allocator arenas; '
                                'they are not automatically classified as heap.')}


def group_remaining(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_child(proc):
    """Record the intentional SIGINT and bounded fallback cleanup separately."""
    result = {'planned_signal': 'SIGINT', 'signal_sent': False, 'escalations': []}
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.kill(proc.pid, signal.SIGINT)
            result['signal_sent'] = True
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        for signum in (signal.SIGTERM, signal.SIGKILL):
            result['escalations'].append(signal.Signals(signum).name)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signum)
            try:
                proc.wait(timeout=3)
                break
            except subprocess.TimeoutExpired:
                continue
    # The direct child is normally the sole group member. Any descendants are
    # cleanup failures even if a final SIGKILL removes them.
    result['group_remaining_after_child_exit'] = group_remaining(proc.pid)
    if result['group_remaining_after_child_exit']:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    result['returncode'] = proc.poll()
    result['child_reaped'] = result['returncode'] is not None
    return result


def capture(binary, fixture_binary, directory, snapshot_seconds):
    directory.mkdir(parents=True, exist_ok=False)
    sandbox = directory / 'sandbox'
    for name in ('tmp', 'cache', 'work'):
        (sandbox / name).mkdir(parents=True)
    result = {'scope': SCOPE, 'interrupted_diagnostic': True, 'valid_snapshot': False,
              'issues': [], 'fixture_config': CONFIG, 'planned_snapshot_seconds': snapshot_seconds,
              'binary': stress.identity(binary)}
    fixture = None
    proc = None
    stderr = None
    try:
        fixture = stress.Fixture(fixture_binary, CONFIG, directory / 'fixture')
        harness.seed_config(sandbox, fixture.url)
        argv = [str(binary), 'sql', 'run', f"{stress.QUERY} LIMIT {CONFIG['rows']}",
                '--from', '2024-01-01T00:00:00Z', '--to', '2024-01-01T01:00:00Z', '-o', 'json']
        result['argv'], result['endpoint'] = argv, fixture.url
        stderr = (directory / 'child.stderr').open('xb')
        launched = time.monotonic()
        proc = subprocess.Popen(argv, env=harness.isolated_env(sandbox.resolve()),
            cwd=sandbox / 'work', stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
        original = identity(proc.pid)
        result['child_identity'] = original
        target = launched + snapshot_seconds
        while proc.poll() is None and time.monotonic() < target:
            time.sleep(min(.05, max(0, target - time.monotonic())))
        if proc.poll() is not None:
            raise RuntimeError('child exited before steady-state snapshot')
        before = identity(proc.pid)
        stats_before = fixture.stats()
        result['snapshot_started_seconds'] = time.monotonic() - launched
        proc_root = Path(f'/proc/{proc.pid}')
        status = (proc_root / 'status').read_text()
        (directory / 'status.txt').write_text(status)
        smaps = (proc_root / 'smaps').read_text()
        (directory / 'smaps.txt').write_text(smaps)
        for filename in ('maps', 'smaps_rollup'):
            try:
                (directory / (filename + '.txt')).write_text((proc_root / filename).read_text())
            except OSError as error:
                result[filename + '_unavailable'] = str(error)
        after = identity(proc.pid)
        result['snapshot_ended_seconds'] = time.monotonic() - launched
        result['status_memory'] = status_memory(status)
        result['smaps'] = parse_smaps(smaps, binary)
        stats_after = fixture.stats()
        result['fixture_before'], result['fixture_after'] = stats_before, stats_after
        for observed in (before, after):
            if observed['start_ticks'] != original['start_ticks'] or observed['ppid'] != os.getpid() or observed['state'] == 'Z':
                result['issues'].append('child identity changed or child no longer alive during snapshot')
        if proc.poll() is not None:
            result['issues'].append('child exited during snapshot')
        for observed in (stats_before, stats_after):
            if not (observed.get('query_requests') == 1 and observed.get('active_queries') == 1
                    and 0 < observed.get('rows_written', 0) < CONFIG['rows']
                    and observed.get('source_bytes', 0) > 0 and not observed.get('completed')):
                result['issues'].append('source was not an active nonempty partial response at snapshot')
        if stats_after.get('source_bytes', 0) < stats_before.get('source_bytes', 0):
            result['issues'].append('source progress moved backwards')
        result['valid_snapshot'] = not result['issues']
    except Exception as error:
        result['issues'].append(f'{type(error).__name__}: {error}')
    finally:
        if proc is not None:
            result['cleanup'] = stop_child(proc)
            cleanup = result['cleanup']
            if (not cleanup['signal_sent'] or not cleanup['child_reaped'] or cleanup['escalations']
                    or cleanup['group_remaining_after_child_exit']):
                result['issues'].append('intentional interrupt or process cleanup did not complete normally')
        if stderr is not None:
            stderr.close()
        if fixture is not None:
            fixture.close()
        result['valid_snapshot'] = result['valid_snapshot'] and not result['issues']
        harness.write_json(directory / 'sample.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--final', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, default=ROOT / 'bin/pb-stress-fixture')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--snapshot-seconds', type=float, default=2)
    parser.add_argument('--seed', type=int, default=48131)
    args = parser.parse_args(argv)
    if not 1 <= args.rounds <= 20 or not 1 <= args.snapshot_seconds <= 5:
        parser.error('require rounds 1..20 and snapshot-seconds 1..5')
    binaries = {'baseline': args.baseline.resolve(), 'final': args.final.resolve()}
    fixture = args.fixture.resolve()
    out = args.out.resolve()
    binary_ids = {label: stress.identity(path) for label, path in binaries.items()}
    fixture_id = stress.identity(fixture)
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'pb-perf3-rss-attribution/1', 'scope': SCOPE, 'complete': False,
              'passed': False, 'fixture_config': CONFIG, 'binaries': binary_ids, 'fixture': fixture_id,
              'method': {'rounds': args.rounds, 'snapshot_seconds': args.snapshot_seconds,
                         'seed': args.seed, 'serial_randomized_pairs': True,
                         'affinity': sorted(os.sched_getaffinity(0)), 'stdout_sink': '/dev/null',
                         'direct_child': True, 'planned_stop': 'SIGINT', 'output_parity': False,
                         'native_peak_measured': False},
              'host': {'uname': list(os.uname()), 'cpu_count': os.cpu_count()},
              'sources': {str(path): stress.identity(path)['sha256'] for path in
                          (Path(__file__), ROOT / 'harness/stress.py', ROOT / 'harness/run.py')},
              'order': [], 'samples': [], 'errors': []}
    rng = random.Random(args.seed)
    try:
        for round_index in range(args.rounds):
            order = list(binaries)
            rng.shuffle(order)
            report['order'].append({'round': round_index, 'labels': order})
            for label in order:
                sample = capture(binaries[label], fixture, out / f'round-{round_index:02d}-{label}',
                                 args.snapshot_seconds)
                sample.update(round=round_index, label=label)
                report['samples'].append(sample)
                harness.write_json(out / 'report.json', report)
                print(f"round={round_index} {label} valid_snapshot={sample['valid_snapshot']} issues={sample['issues']}", flush=True)
        report['binaries_end'] = {label: stress.identity(path) for label, path in binaries.items()}
        report['fixture_end'] = stress.identity(fixture)
        if report['binaries_end'] != binary_ids or report['fixture_end'] != fixture_id:
            report['errors'].append('binary or fixture identity changed during capture')
        report['complete'] = True
        report['passed'] = not report['errors'] and all(sample['valid_snapshot'] for sample in report['samples'])
    except Exception as error:
        report['errors'].append(f'{type(error).__name__}: {error}')
        raise
    finally:
        harness.write_json(out / 'report.json', report)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
