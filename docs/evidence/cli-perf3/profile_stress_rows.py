#!/usr/bin/env python3
"""Queued perf attribution of the exact eight-row ASCII stress workload.

    python3 docs/evidence/cli-perf3/profile_stress_rows.py \
        --out harness/results/perf3/profiles-stress-ascii

Run under the existing performance flock after other owned work is idle. This
script never builds a binary. All four captures finish before perf analysis.
The source cycles printable ASCII including &, < and >; unlike an all-x row it
exercises millions of HTML-escape appends. Timings are instrumented diagnostics,
with no output-parity, native resource or performance-admission claim.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'harness'))
import run as harness
import stress


CONFIG = {'rows': 8, 'row_bytes': 8_388_608, 'shape': 'ascii'}
EXPECTED_SOURCE_BYTES = CONFIG['rows'] * (CONFIG['row_bytes'] + 1) + 1
SCOPE = ('Instrumented CPU attribution of the exact stress ASCII JSON workload. '
         'CLI stdout=/dev/null; no output parity, native CPU/RSS measurement, '
         'throughput or timing-performance admission. Percentages are perf '
         'sample self overhead, not uninstrumented CPU time or call counts.')
SYMBOL = re.compile(r'^\s*(\d+(?:\.\d+)?)%\s+(\S+)\s+(\S+)\s+(\[[^\]]+\])\s+(.+?)\s*$')
SAMPLE_COUNT = re.compile(r'^#\s*Samples:\s*([\d,.]+)\s*([KMG]?)\s+of event', re.IGNORECASE)


def environment(sandbox=None):
    env = harness.isolated_env(sandbox.resolve()) if sandbox is not None else os.environ.copy()
    env.update(DEBUGINFOD_URLS='', LC_ALL='C', LANG='C')
    return env


def group_remaining(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def cleanup(proc):
    result = {'escalations': []}
    if proc.poll() is None:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            result['escalations'].append(signal.Signals(signum).name)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signum)
            try:
                proc.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                continue
    result['returncode'] = proc.poll()
    result['process_group_remaining'] = group_remaining(proc.pid)
    if result['process_group_remaining']:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    result['reaped'] = result['returncode'] is not None
    return result


def settled_stats(fixture):
    deadline = time.monotonic() + 2
    stats = fixture.stats()
    while stats.get('active_queries', 0) and time.monotonic() < deadline:
        time.sleep(.01)
        stats = fixture.stats()
    return stats


def capture(binary, fixture_binary, perf, directory, timeout):
    directory.mkdir(parents=True, exist_ok=False)
    sandbox = directory / 'sandbox'
    for name in ('tmp', 'cache', 'work'):
        (sandbox / name).mkdir(parents=True)
    result = {'scope': SCOPE, 'valid': False, 'issues': [], 'artifact_dir': str(directory),
              'perf_data': str(directory / 'perf.data'), 'fixture_config': CONFIG}
    fixture = None
    proc = None
    try:
        fixture = stress.Fixture(fixture_binary, CONFIG, directory / 'fixture')
        harness.seed_config(sandbox, fixture.url)
        query = f"{stress.QUERY} LIMIT {CONFIG['rows']}"
        argv = [str(binary), 'sql', 'run', query, '--from', '2024-01-01T00:00:00Z',
                '--to', '2024-01-01T01:00:00Z', '-o', 'json']
        command = [str(perf), 'record', '-q', '-e', 'cycles:u', '-F', '999',
                   '--call-graph', 'dwarf,16384', '-o', result['perf_data'], '--', *argv]
        result['command'] = command
        started = time.monotonic()
        with (directory / 'record.stderr').open('xb') as stderr:
            proc = subprocess.Popen(command, cwd=sandbox / 'work', env=environment(sandbox),
                                    stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
            try:
                result['exit_code'] = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result['issues'].append('perf record exceeded diagnostic deadline')
            finally:
                result['cleanup'] = cleanup(proc)
        result['instrumented_capture_seconds'] = time.monotonic() - started
        if result.get('exit_code') != 0:
            result['issues'].append('perf record/CLI did not exit zero')
        if result['cleanup']['escalations'] or not result['cleanup']['reaped'] or result['cleanup']['process_group_remaining']:
            result['issues'].append('perf/CLI cleanup was not an ordinary complete exit')
        stats = settled_stats(fixture)
        result['fixture_stats'] = stats
        expected_request = json.dumps({'query': query, 'startTime': '2024-01-01T00:00:00Z',
            'endTime': '2024-01-01T01:00:00Z'}, separators=(',', ':')).encode()
        result['expected_request_body_sha256'] = hashlib.sha256(expected_request).hexdigest()
        last = stats.get('last_query') or {}
        if not (stats.get('query_requests') == 1 and stats.get('active_queries') == 0
                and stats.get('completed') is True and stats.get('completed_requests') == 1
                and stats.get('rows_written') == CONFIG['rows']
                and stats.get('source_bytes') == EXPECTED_SOURCE_BYTES
                and stats.get('response_sha256') == last.get('response_sha256')
                and isinstance(stats.get('response_sha256'), str) and len(stats['response_sha256']) == 64
                and last.get('body_sha256') == result['expected_request_body_sha256']
                and last.get('auth') == 'ok' and last.get('method') == 'POST'
                and last.get('path') == '/api/v1/query' and not stats.get('write_errors')):
            result['issues'].append('fixture source/request admission failed')
        data = Path(result['perf_data'])
        if not data.is_file() or data.stat().st_size == 0:
            result['issues'].append('perf.data missing or empty')
        else:
            result['perf_data_identity'] = stress.identity(data)
        result['record_stderr_identity'] = stress.identity(directory / 'record.stderr')
    except Exception as error:
        result['issues'].append(f'{type(error).__name__}: {error}')
    finally:
        if proc is not None and proc.poll() is None:
            result['cleanup_after_exception'] = cleanup(proc)
        if fixture is not None:
            fixture.close()
        result['valid'] = not result['issues']
        harness.write_json(directory / 'capture.json', result)
    return result


def parse_analysis(path):
    headers = []
    symbols = []
    samples = None
    approximate_events = None
    lost_samples = None
    with path.open() as source:
        for line in source:
            stripped = line.strip()
            if stripped.startswith('#'):
                if any(word in stripped.lower() for word in ('samples:', 'event count', 'lost samples')):
                    headers.append(stripped)
                match = SAMPLE_COUNT.match(stripped)
                if match:
                    samples = float(match[1].replace(',', '')) * {'': 1, 'K': 1_000, 'M': 1_000_000, 'G': 1_000_000_000}[match[2].upper()]
                elif 'Event count' in stripped:
                    number = stripped.rsplit(':', 1)[-1].strip().replace(',', '')
                    if number.isdigit():
                        approximate_events = int(number)
                elif 'Total Lost Samples' in stripped:
                    number = stripped.rsplit(':', 1)[-1].strip().replace(',', '')
                    if number.isdigit():
                        lost_samples = int(number)
            match = SYMBOL.match(line)
            if match:
                symbols.append({'self_overhead_percent': float(match[1]), 'command': match[2],
                                'dso': match[3], 'mode': match[4], 'symbol': match[5]})
    symbols.sort(key=lambda row: row['self_overhead_percent'], reverse=True)
    return {'header_lines': headers, 'reported_samples': samples,
            'sample_count_precision': 'perf report human-readable header; K/M/G values can be rounded',
            'approximate_event_count': approximate_events, 'reported_lost_samples': lost_samples,
            'top_symbols': symbols[:40],
            'row_buffer_symbols': [row for row in symbols if any(word in row['symbol'] for word in
                ('RowBuffer', 'sql::output::string', 'sql::output::render', 'extend_from_slice'))]}


def analyze(run, perf, timeout):
    directory = Path(run['artifact_dir'])
    target = directory / 'analysis.txt'
    command = [str(perf), 'report', '--stdio', '--no-children', '--percent-limit', '0.5',
               '-i', run['perf_data']]
    result = {'command': command, 'scope': SCOPE, 'valid': False, 'issues': [], 'path': str(target)}
    try:
        with target.open('xb') as stdout, (directory / 'analysis.stderr').open('xb') as stderr:
            completed = subprocess.run(command, cwd=ROOT, env=environment(), stdout=stdout,
                                       stderr=stderr, timeout=timeout, check=False)
        result['exit_code'] = completed.returncode
        if completed.returncode != 0:
            result['issues'].append('perf report returned nonzero')
        result.update(parse_analysis(target))
        if not result['reported_samples'] or not result['top_symbols']:
            result['issues'].append('perf report lacks positive sample count or parsed symbol rows')
        result['identity'] = stress.identity(target)
        result['stderr_identity'] = stress.identity(directory / 'analysis.stderr')
    except Exception as error:
        result['issues'].append(f'{type(error).__name__}: {error}')
    result['valid'] = not result['issues']
    harness.write_json(directory / 'analysis.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=ROOT / 'bin/pb-perf2-final-symbolized')
    parser.add_argument('--candidate', type=Path, default=ROOT / 'bin/pb-perf3-final-symbolized')
    parser.add_argument('--fixture', type=Path, default=ROOT / 'bin/pb-stress-fixture')
    parser.add_argument('--perf', default='perf')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 900:
        parser.error('timeout must be 1..900 seconds')
    executable = shutil.which(args.perf)
    if executable is None:
        parser.error('perf executable unavailable')
    perf = Path(executable).resolve()
    binaries = {'baseline': args.baseline.resolve(), 'candidate': args.candidate.resolve()}
    fixture = args.fixture.resolve()
    identities = {label: stress.identity(path) for label, path in binaries.items()}
    fixture_id = stress.identity(fixture)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'pb-perf3-stress-row-profile/1', 'scope': SCOPE, 'complete': False,
              'passed': False, 'binaries': identities, 'fixture': fixture_id, 'perf': stress.identity(perf),
              'fixture_config': CONFIG, 'expected_source_bytes': EXPECTED_SOURCE_BYTES,
              'method': {'event': 'cycles:u', 'frequency': 999, 'call_graph': 'dwarf,16384',
                         'repetitions': 2, 'capture_before_analysis': True, 'stdout_sink': '/dev/null',
                         'output_parity': False, 'performance_admitted': False, 'timeout': args.timeout,
                         'affinity': sorted(os.sched_getaffinity(0)), 'DEBUGINFOD_URLS': ''},
              'host': {'uname': list(os.uname()), 'cpu_count': os.cpu_count()},
              'sources': {str(path): stress.identity(path)['sha256'] for path in
                          (Path(__file__), ROOT / 'harness/stress.py', ROOT / 'harness/run.py')},
              'runs': [], 'errors': []}
    try:
        for repetition in range(2):
            order = ['baseline', 'candidate'] if repetition == 0 else ['candidate', 'baseline']
            for label in order:
                print(f'capture repetition={repetition} {label}', flush=True)
                run = capture(binaries[label], fixture, perf,
                              out / f'capture-{repetition:02d}-{label}', args.timeout)
                run.update(repetition=repetition, label=label)
                report['runs'].append(run)
                harness.write_json(out / 'report.json', report)
        hashes = {run.get('fixture_stats', {}).get('response_sha256') for run in report['runs']}
        if len(hashes) != 1 or None in hashes:
            report['errors'].append('source response identity differs across captures')
        if not all(run['valid'] for run in report['runs']):
            report['errors'].append('one or more capture admissions failed')
        # Analysis starts only after all measured/profiler children are reaped.
        for run in report['runs']:
            if run.get('perf_data_identity'):
                print(f"analyze repetition={run['repetition']} {run['label']}", flush=True)
                run['analysis'] = analyze(run, perf, args.timeout)
                if not run['analysis']['valid']:
                    report['errors'].append(f"analysis failed: repetition={run['repetition']} {run['label']}")
                harness.write_json(out / 'report.json', report)
        report['binaries_end'] = {label: stress.identity(path) for label, path in binaries.items()}
        report['fixture_end'] = stress.identity(fixture)
        if report['binaries_end'] != identities or report['fixture_end'] != fixture_id:
            report['errors'].append('binary or fixture identity changed')
        report['complete'] = True
        report['passed'] = not report['errors'] and len(report['runs']) == 4
    except Exception as error:
        report['errors'].append(f'{type(error).__name__}: {error}')
        raise
    finally:
        harness.write_json(out / 'report.json', report)
        summary = {key: report.get(key) for key in
                   ('schema', 'scope', 'complete', 'passed', 'binaries', 'fixture', 'perf',
                    'fixture_config', 'expected_source_bytes', 'method', 'errors')}
        summary['runs'] = [{key: run.get(key) for key in
                           ('label', 'repetition', 'valid', 'issues', 'artifact_dir', 'analysis')}
                          for run in report['runs']]
        harness.write_json(out / 'summary.json', summary)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
