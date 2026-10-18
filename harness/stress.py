#!/usr/bin/env python3
"""Bounded, process-level SQL stress and edge-case comparison for two binaries.

Responses are generated incrementally by pb-stress-fixture; stdout is hashed,
not retained. Timing is descriptive until compatibility checks pass. Soak runs
repeat bounded queries: they do not bypass either CLI's HTTP deadline.
"""
import argparse
import base64
import contextlib
import csv
import hashlib
import gzip
import json
import math
import os
from pathlib import Path
import random
import re
import selectors
import signal
import statistics
import subprocess
import time
import urllib.request
import urllib.parse

import run as harness
from stress_process import capture


SCHEMA = 'pb-stream-stress/1'
QUERY = 'SELECT * FROM logs'
AUTH = 'Basic ' + base64.b64encode(b'harness:harness').decode()


def case(name, rows=10000, row_bytes=32, shape='tiny', expectation='success', consumer=None,
         output='json', scan_json=True, **fixture):
    return {'name': name, 'expectation': expectation,
            'output': output,
            'scan_json': scan_json,
            'fixture': dict(rows=rows, row_bytes=row_bytes, shape=shape, **fixture),
            'consumer': consumer or {}}


def cases(profile):
    smoke = [
        case('tiny-10k'), case('tiny-10k-text', output='text'), case('empty-array', rows=0),
        case('mixed-10k', shape='mixed', row_bytes=256),
        case('escaped-fragments', rows=64, row_bytes=256, shape='escaped',
             fragment_pattern=[1, 2, 3, 7, 31], flush=True),
        case('gzip-fragments', rows=1000, shape='mixed', row_bytes=256,
             gzip=True, fragment_pattern=[1, 7, 257], flush=True),
        case('malformed-prefix', rows=100, failure='invalid_json', fail_after_rows=17,
             expectation='error'),
        case('truncated-prefix', rows=100, failure='truncate', fail_after_rows=17,
             expectation='error'),
        case('http-503', rows=1, failure='status503', expectation='error'),
        case('paused-reader', rows=20000, row_bytes=128,
             consumer={'pause_after_bytes': 32768, 'pause_seconds': 0.5}),
        case('slow-reader', rows=2000, row_bytes=256,
             consumer={'read_chunk': 4096, 'read_delay': 0.002}),
        case('closed-pipe', rows=100000, expectation='observation',
             consumer={'stop_after_bytes': 16384}),
        case('interrupt-stream', rows=100000, chunk_bytes=1024, chunk_delay_ms=2,
             expectation='observation', consumer={'signal_after_seconds': 0.25}),
    ]
    if profile == 'smoke':
        return smoke
    if profile == 'scale':
        return [case('tiny-1m', rows=1000000, scan_json=False),
                case('tiny-10m', rows=10000000, scan_json=False),
                case('stream-1gib', rows=1048576, row_bytes=1024, shape='ascii', output='text', scan_json=False),
                case('escaped-1m', rows=1000000, row_bytes=256, shape='escaped', scan_json=False),
                case('large-item-8mib', rows=1, row_bytes=8*1048576, shape='ascii', scan_json=False)]
    if profile == 'soak':
        return [case('sustained-tiny', rows=500000, scan_json=False),
                case('sustained-paced', rows=100000, row_bytes=256,
                     chunk_bytes=65536, chunk_delay_ms=10, scan_json=False)]
    if profile == 'extended':
        return [case('stream-10gib', rows=10485760, row_bytes=1024, shape='ascii', output='text', scan_json=False),
                case('tiny-50m', rows=50000000, scan_json=False)]
    if profile == 'large':
        return [case('large-item-8mib', rows=1, row_bytes=8*1048576, shape='ascii', scan_json=False),
                case('large-items-8x8mib', rows=8, row_bytes=8*1048576, shape='ascii', scan_json=False),
                case('large-items-gzip-8x8mib', rows=8, row_bytes=8*1048576, shape='ascii',
                     gzip=True, scan_json=False)]
    if profile == 'transport':
        return [case('gzip-text-64mib', rows=65536, row_bytes=1024, shape='ascii',
                     gzip=True, output='text', scan_json=False),
                case('gzip-text-fragments', rows=4096, row_bytes=256, shape='mixed', gzip=True,
                     fragment_pattern=[1, 7, 1400], flush=True, output='text', scan_json=False),
                case('plain-text-fragments', rows=4096, row_bytes=256, shape='mixed',
                     fragment_pattern=[1, 7, 1400], flush=True, output='text', scan_json=False)]
    if profile == 'backpressure':
        return [case('pause-20s-text', rows=1000000, row_bytes=128, output='text', scan_json=False,
                     consumer={'pause_after_bytes': 65536, 'pause_seconds': 20})]
    if profile == 'deadline':
        return [case('sql-60s-deadline', rows=1000000, chunk_bytes=1024, chunk_delay_ms=10,
                     flush=True, expectation='deadline')]
    raise ValueError(f'unknown profile: {profile}')


def identity(path):
    path = Path(path).resolve()
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return {'path': str(path), 'sha256': h.hexdigest(), 'size_bytes': path.stat().st_size}


class Fixture:
    def __init__(self, binary, config, directory):
        self.proc = None
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / 'fixture.json'
        harness.write_json(path, config)
        self.log = (self.directory / 'fixture.stderr').open('wb')
        try:
            self.proc = subprocess.Popen([str(binary), '--config', str(path.resolve())],
                                         stdout=subprocess.PIPE, stderr=self.log,
                                         start_new_session=True)
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                os.set_blocking(self.proc.stdout.fileno(), False)
                deadline = time.monotonic() + 10
                line = bytearray()
                while b'\n' not in line and len(line) <= 65536:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise RuntimeError('stress fixture did not become ready in 10 seconds')
                    block = os.read(self.proc.stdout.fileno(), 65537 - len(line))
                    if not block:
                        raise RuntimeError('stress fixture exited before ready')
                    line.extend(block)
            if len(line) > 65536:
                raise RuntimeError('oversized fixture ready line')
            ready = json.loads(line.split(b'\n', 1)[0])
            self.url = ready['url']
            parsed = urllib.parse.urlsplit(self.url)
            if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.port is None or parsed.username:
                raise RuntimeError('fixture must bind IPv4 loopback')
            harness.write_json(self.directory / 'fixture-ready.json', ready)
        except BaseException:
            self.close()
            raise

    def stats(self):
        with urllib.request.urlopen(self.url + '/__stats', timeout=5) as f:
            b = f.read(1048577)
        if len(b) > 1048576:
            raise RuntimeError('oversized fixture statistics')
        return json.loads(b)

    def close(self):
        if self.proc is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=3)
            self.proc.stdout.close()
            self.proc = None
        self.log.close()


def decode_prefix(stream):
    return base64.b64decode(stream.get('prefix_base64', '')).decode('utf-8', errors='replace')


def normalize_error(sample):
    err = sample['stderr']
    # Error streams must fit wholly in the retained prefix to normalize safely.
    raw = base64.b64decode(err.get('prefix_base64', ''))
    if len(raw) != err['bytes']:
        return {'sha256': err['sha256'], 'bytes': err['bytes'], 'normalization': 'unavailable'}
    value = harness.normalize(raw, {sample['endpoint']: '<fixture>', sample['sandbox']: '<sandbox>'})
    return {'sha256': hashlib.sha256(value).hexdigest(), 'bytes': len(value)}


def contract(sample):
    return {'exit_code': (sample.get('native_metrics') or {}).get('exit_code'),
            'stdout_sha256': sample['stdout']['sha256'], 'stdout_bytes': sample['stdout']['bytes'],
            'stderr': normalize_error(sample),
            'request_body_sha256': sample.get('fixture_stats', {}).get('request_body_sha256')}


def check_sample(sample, spec):
    issues = []
    native = sample.get('native_metrics') or {}
    if sample.get('timed_out') or sample.get('outcome') == 'timeout':
        issues.append('collector timeout')
    if sample.get('outcome') in ('post_exit_drain_limit', 'descendant_held_pipes'):
        issues.append('collector could not drain an ordinary CLI exit')
    if sample.get('meter_unreaped') or sample.get('process_group_remaining'):
        issues.append('collector cleanup left a process outstanding')
    if not native or native.get('resource_source') != 'native_child_wait4':
        issues.append('native CLI metrics unavailable')
    stats = sample.get('fixture_stats', {})
    if stats.get('query_requests') != 1:
        issues.append('expected exactly one query request')
    if not stats.get('request_body_sha256'):
        issues.append('query request hash unavailable')
    if sample.get('expected_request_body_sha256') and stats.get('request_body_sha256') != sample['expected_request_body_sha256']:
        issues.append('wire query differs from explicit expected LIMIT/time range')
    last = stats.get('last_query')
    if last and (last.get('auth') != 'ok' or last.get('method') != 'POST' or last.get('path') != '/api/v1/query'):
        issues.append('unexpected query method/path/authentication')
    if spec['expectation'] == 'success':
        if sample.get('outcome') != 'completed':
            issues.append('successful stream did not complete normally')
        if sample.get('output_eof') and (not sample['output_eof'].get('stderr') or
                (sample.get('stdout_sink', 'pipe') == 'pipe' and not sample['output_eof'].get('stdout'))):
            issues.append('successful stream did not drain to EOF')
        if native.get('exit_code') != 0:
            issues.append('successful stream returned nonzero')
        if sample['stderr']['bytes']:
            issues.append('successful stream wrote stderr')
        if sample.get('stdout_sink') == 'null':
            admitted = sample.get('validated_source')
            if not admitted or stats.get('response_sha256') != admitted.get('response_sha256'):
                issues.append('discarded-output run lacks matching prior output validation')
            expected_bytes = (admitted or {}).get('stdout_bytes')
            written_bytes = native.get('proc_io_wchar')
            if expected_bytes is None or written_bytes is None or written_bytes < expected_bytes:
                issues.append('native write accounting does not cover previously validated stdout bytes')
            if stats.get('rows_written') != spec['fixture']['rows']:
                issues.append('source did not write configured row count')
        elif spec['scan_json']:
            if not sample['json_rows'].get('complete'):
                issues.append('stdout array framing incomplete')
            if sample['json_rows'].get('completed_rows') != spec['fixture']['rows']:
                issues.append('stdout record count differs from configured count')
        else:
            prefix = base64.b64decode(sample['stdout'].get('prefix_base64', ''))
            suffix = base64.b64decode(sample['stdout'].get('suffix_base64', ''))
            if not prefix.startswith(b'[') or not suffix.rstrip().endswith(b']'):
                issues.append('hash-only capture lacks expected array boundaries')
            if stats.get('rows_written') != spec['fixture']['rows']:
                issues.append('source did not write configured row count')
        if not stats.get('completed'):
            issues.append('fixture did not complete response')
    elif spec['expectation'] in ('error', 'deadline'):
        if native.get('exit_code') in (None, 0):
            issues.append('injected failure did not return an error')
        if not sample['stderr']['bytes']:
            issues.append('injected failure did not explain error')
        if spec['fixture'].get('failure') in ('truncate', 'invalid_json') and spec['output'] == 'json':
            if sample['json_rows'].get('completed_rows') != spec['fixture']['fail_after_rows']:
                issues.append('partial output does not contain exactly the valid prefix rows')
        if spec['expectation'] == 'deadline':
            wall_ms = (native.get('ended_monotonic_ns', 0) - native.get('started_monotonic_ns', 0)) / 1e6
            if not 55000 <= wall_ms <= 75000:
                issues.append('SQL deadline did not occur near its 60-second bound')
            if 'context deadline exceeded' not in decode_prefix(sample['stderr']):
                issues.append('missing SQL deadline error')
            if not sample['json_rows'].get('complete_row_prefix') or not 0 < sample['json_rows'].get('completed_rows', 0) < spec['fixture']['rows']:
                issues.append('deadline output is not a nonempty framed row prefix')
    else:
        # Scheduling determines exactly how much output precedes cancellation.
        # These observations are never counted as differential parity passes.
        if native.get('exit_code') in (None, 0):
            issues.append('planned interruption did not terminate with an error')
    return issues


def timeline_summary(path, output_bytes=0):
    if not path or not Path(path).exists():
        return {'available': False}
    # Keep only aggregate/bucket state even for a multi-hour soak.
    n = 0
    first = last = None
    rss_min = rss_max = None
    window_first = window_last = None
    for row in csv.DictReader(Path(path).open()):
        n += 1
        if first is None:
            first = row
        last = row
        received = int(row.get('stdout_bytes') or 0)
        if output_bytes and received >= output_bytes * .1 and window_first is None:
            window_first = (float(row['elapsed_seconds']), received)
        if output_bytes and received <= output_bytes * .9 and window_first is not None:
            window_last = (float(row['elapsed_seconds']), received)
        value = row.get('rss_bytes') if row.get('child_preexec', '').lower() not in ('true', '1') else None
        if value not in (None, ''):
            value = int(value)
            rss_min = value if rss_min is None else min(rss_min, value)
            rss_max = value if rss_max is None else max(rss_max, value)
    interior = None
    if window_first and window_last and window_last[0] > window_first[0]:
        interior = {'seconds': window_last[0] - window_first[0],
                    'output_bytes': window_last[1] - window_first[1],
                    'output_mib_per_second': (window_last[1] - window_first[1]) / 1048576 / (window_last[0] - window_first[0]),
                    'scope': 'observed drain intervals within 10–90% of output, not pure parser throughput'}
    return {'available': n > 0, 'samples': n, 'first': first, 'last': last,
            'interior_output_window': interior,
            'sampled_rss_min_bytes': rss_min, 'sampled_rss_max_bytes': rss_max,
            'scope': 'sampled current RSS, not native lifetime peak; no leak conclusion'}


def run_sample(binary, fixture_binary, meter, spec, directory, timeout, sample_interval,
               stdout_sink='pipe', validated_source=None):
    directory.mkdir(parents=True)
    sandbox = directory / 'sandbox'
    for p in ('tmp', 'cache', 'work'):
        (sandbox / p).mkdir(parents=True)
    fixture = Fixture(fixture_binary, spec['fixture'], directory)
    try:
        harness.seed_config(sandbox, fixture.url)
        query = QUERY + f" LIMIT {max(1, spec['fixture']['rows'])}"
        argv = [str(binary), 'sql', 'run', query, '--from', '2024-01-01T00:00:00Z',
                '--to', '2024-01-01T01:00:00Z', '-o', spec['output']]
        result = capture(argv, env=harness.isolated_env(sandbox.resolve()),
                         cwd=sandbox / 'work', artifact_dir=directory / 'process',
                         meter=meter, timeout=timeout, sample_interval=sample_interval,
                         scan_json=spec['scan_json'], stdout_sink=stdout_sink,
                         **spec['consumer'])
        stats = fixture.stats()
        stats_deadline = time.monotonic() + 2
        while stats.get('active_queries', 0) and time.monotonic() < stats_deadline:
            time.sleep(0.01)
            stats = fixture.stats()
        stats['request_body_sha256'] = (stats.get('last_query') or {}).get('body_sha256')
        request = {'query': query, 'startTime': '2024-01-01T00:00:00Z', 'endTime': '2024-01-01T01:00:00Z'}
        expected_hash = hashlib.sha256(json.dumps(request, separators=(',', ':')).encode()).hexdigest()
        result.update(endpoint=fixture.url, sandbox=str(sandbox.resolve()), argv=argv,
                      fixture_stats=stats, expected_request_body_sha256=expected_hash,
                      validated_source=validated_source)
        result['issues'] = check_sample(result, spec)
        result['contract'] = contract(result)
        result['contract']['response_sha256'] = stats.get('response_sha256')
        if spec['expectation'] == 'deadline':
            # Timing controls the prefix length; retain it in the sample but
            # compare the deterministic exit/error/request contract only.
            result['contract'].pop('stdout_sha256')
            result['contract'].pop('stdout_bytes')
            result['contract'].pop('response_sha256')
        result['timeline_summary'] = timeline_summary(result.get('timeline_csv'), result['stdout']['bytes'])
        native = result.get('native_metrics') or {}
        wall = (native.get('ended_monotonic_ns', 0) - native.get('started_monotonic_ns', 0)) / 1e6
        cpu = native.get('cpu_user_ms', 0) + native.get('cpu_system_ms', 0)
        rows = result['json_rows'].get('completed_rows')
        row_source = 'framed stdout objects'
        if not spec['scan_json'] or stdout_sink == 'null':
            rows = stats.get('rows_written', 0)
            row_source = 'fixture source rows; performance admitted only after full compared-binary stdout digest match'
        rows = rows or 0
        comparable = (spec['expectation'] == 'success' and not result['issues'] and not spec['consumer']
                      and not spec['fixture'].get('chunk_delay_ms')
                      and not spec['fixture'].get('flush') and not spec['fixture'].get('fragment_pattern'))
        result['rates'] = {
            'wall_ms': wall, 'cpu_ms': cpu,
            'rows_per_second': rows * 1000 / wall if wall > 0 and comparable else None,
            'cpu_ms_per_million_rows': cpu * 1000000 / rows if rows and comparable else None,
            'output_mib_per_second': result['stdout']['bytes'] / 1048576 * 1000 / wall if wall > 0 and result['stdout']['bytes'] is not None else None,
            'scope': 'end-to-end CLI; includes startup; output sink=' + stdout_sink,
            'row_source': row_source, 'unpaced_success_only': comparable,
        }
        result['observer'] = {
            'collector_cpu_fraction_of_wall': result.get('collector_cpu_total_ms', 0) / (result['collector_wall_seconds'] * 1000),
            'observer_bound': result.get('collector_cpu_total_ms', 0) >= result['collector_wall_seconds'] * 800,
            'first_row_seconds': result['json_rows'].get('first_row_seconds'),
            'first_to_last_row_seconds': result['json_rows'].get('first_to_last_row_seconds'),
            'scope': 'pipe-drain observations; dense records can be observer-bound; no per-row latency percentiles',
        }
        harness.write_json(directory / 'sample.json', result)
        return result
    finally:
        fixture.close()


def calibrate(fixture_binary, spec, directory, timeout):
    """Raw producer/reader ceiling; no row scanner and no CLI transformation."""
    fixture = Fixture(fixture_binary, spec['fixture'], directory)
    try:
        query = QUERY + f" LIMIT {max(1, spec['fixture']['rows'])}"
        body = json.dumps({'query': query, 'startTime': '2024-01-01T00:00:00Z',
                           'endTime': '2024-01-01T01:00:00Z'}, separators=(',', ':')).encode()
        request = urllib.request.Request(fixture.url + '/api/v1/query', data=body,
                                         headers={'Authorization': AUTH, 'Content-Type': 'application/json'})
        start = time.monotonic()
        count, digest = 0, hashlib.sha256()
        with urllib.request.urlopen(request, timeout=timeout) as response:
            source = gzip.GzipFile(fileobj=response) if spec['fixture'].get('gzip') else response
            with contextlib.closing(source) if source is not response else contextlib.nullcontext(source):
                while True:
                    if time.monotonic() - start > timeout:
                        raise TimeoutError('producer calibration exceeded deadline')
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    count += len(chunk)
                    digest.update(chunk)
        elapsed = time.monotonic() - start
        stats = fixture.stats()
        deadline = time.monotonic() + 2
        while stats.get('active_queries', 0) and time.monotonic() < deadline:
            time.sleep(0.01)
            stats = fixture.stats()
        text_digest = digest.copy()
        text_digest.update(b'\n')
        result = {'bytes': count, 'sha256': digest.hexdigest(), 'text_output_sha256': text_digest.hexdigest(), 'seconds': elapsed,
                  'source_mib_per_second': count / 1048576 / elapsed,
                  'fixture_stats': stats, 'scope': 'unparsed raw stream drain; producer/reader diagnostic only',
                  'valid': stats.get('completed') and stats.get('source_bytes') == count
                           and stats.get('response_sha256') == digest.hexdigest()}
        harness.write_json(directory / 'calibration.json', result)
        return result
    finally:
        fixture.close()


def summarize(samples, specs, labels):
    result = {}
    for spec in specs:
        selected = [s for s in samples if s['case'] == spec['name'] and not s['warmup']]
        by_label = {label: [s for s in selected if s['label'] == label] for label in labels}
        observation = spec['expectation'] == 'observation'
        contracts = {json.dumps(s['contract'], sort_keys=True) for s in selected}
        valid = all(not s['issues'] for s in selected) and all(by_label.values())
        compatible = None if observation else valid and len(contracts) == 1
        prefix_comparison = None
        if spec['expectation'] == 'deadline' and selected:
            prefixes = [base64.b64decode(s['stdout']['prefix_base64']) for s in selected]
            common_bytes = min(map(len, prefixes))
            prefix_matches = common_bytes > 0 and len({p[:common_bytes] for p in prefixes}) == 1
            compatible = compatible and prefix_matches
            prefix_comparison = {'bytes': common_bytes, 'matches': prefix_matches,
                                 'scope': 'bounded initial output only; later timing-dependent rows are framing-checked'}
        metrics = {}
        for label, runs in by_label.items():
            metrics[label] = {'runs': len(runs)}
            for key in ('wall_ms', 'cpu_ms', 'rows_per_second', 'cpu_ms_per_million_rows', 'output_mib_per_second'):
                values = [s['rates'][key] for s in runs if s['rates'][key] is not None]
                metrics[label][key] = statistics.median(values) if values else None
            rss = [s['native_metrics']['peak_rss_bytes'] for s in runs if s.get('native_metrics')]
            metrics[label]['peak_rss_bytes'] = statistics.median(rss) if rss else None
        result[spec['name']] = {'valid': valid, 'compatible': compatible, 'observation_only': observation,
                                'deadline_prefix_comparison': prefix_comparison,
                                'different_contracts': len(contracts), 'medians': metrics,
                                'observer_bound_runs': sum(s.get('observer', {}).get('observer_bound', False) for s in selected),
                                'performance_admitted': bool(compatible and spec['expectation'] == 'success' and not spec['consumer'] and
                                    not spec['fixture'].get('chunk_delay_ms') and
                                    not spec['fixture'].get('flush') and not spec['fixture'].get('fragment_pattern') and
                                    all(not s.get('observer', {}).get('observer_bound', False) for s in selected)),
                                'performance_note': 'descriptive local fixture measurement; pacing, observer or producer may cap throughput'}
    return result


def run(args):
    binaries = {args.reference_label: identity(args.go), args.candidate_label: identity(args.rust)}
    fixture_id, meter_id = identity(args.fixture), identity(args.meter)
    specs = cases(args.profile)
    if args.case:
        selected = set(args.case)
        specs = [c for c in specs if c['name'] in selected]
        if selected != {c['name'] for c in specs}:
            raise ValueError('unknown case for selected profile')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema': SCHEMA, 'complete': False, 'passed': False, 'binaries': binaries,
              'fixture': fixture_id, 'meter': meter_id, 'cases': specs, 'samples': [],
              'method': {'profile': args.profile, 'repetitions': args.repetitions, 'warmup': args.warmup,
                         'labels': list(binaries),
                         'go_oracle': args.reference_label == 'go',
                         'soak_seconds': args.duration, 'sample_interval': args.sample_interval,
                         'seed': args.seed, 'timeout': args.timeout,
                         'affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
                         'scope': 'local generated SQL, bounded memory; no product modifications; descriptive medians'},
              'host': {'uname': list(os.uname()), 'cpu_count': os.cpu_count()}, 'errors': [],
              'calibrations': {}, 'output_validations': {}, 'stdout_sink': args.sink}
    report['sources'] = {str(p): identity(p)['sha256'] for p in
                         (Path(__file__), Path(__file__).with_name('stress_process.py'),
                          Path(__file__).parent / 'stress_fixture' / 'main.go')}
    rng = random.Random(args.seed)
    try:
        for spec in specs:
            print(spec['name'], flush=True)
            if args.calibrate and spec['expectation'] == 'success':
                report['calibrations'][spec['name']] = calibrate(
                    Path(fixture_id['path']), spec, out / spec['name'] / 'calibration', args.timeout)
            validated = None
            if args.sink == 'null':
                validation_runs = []
                for label in binaries:
                    s = run_sample(Path(binaries[label]['path']), Path(fixture_id['path']),
                                   Path(meter_id['path']), spec, out / spec['name'] / ('validate-' + label),
                                   args.timeout, args.sample_interval)
                    s.update(case=spec['name'], label=label, warmup=False)
                    validation_runs.append(s)
                verdict = summarize(validation_runs, [spec], list(binaries))[spec['name']]
                report['output_validations'][spec['name']] = {'samples': validation_runs, 'verdict': verdict,
                    'scope': 'exact full output comparison through a pipe before resource-only null-sink runs'}
                if not verdict['valid'] or not verdict['compatible']:
                    raise ValueError(f"{spec['name']}: prior exact output validation failed; resource runs withheld")
                if spec['output'] == 'text' and spec['name'] in report['calibrations']:
                    expected_text = report['calibrations'][spec['name']]['text_output_sha256']
                    if any(s['stdout']['sha256'] != expected_text for s in validation_runs):
                        raise ValueError('text validation differs from independently drained source plus newline')
                validated = {'response_sha256': validation_runs[0]['fixture_stats']['response_sha256'],
                             'stdout_sha256': validation_runs[0]['stdout']['sha256'],
                             'stdout_bytes': validation_runs[0]['stdout']['bytes'],
                             'scope': 'same deterministic source and binaries validated before discard-output measurement'}
            start = time.monotonic()
            round_index = -args.warmup
            while round_index < args.repetitions or (args.profile == 'soak' and time.monotonic() - start < args.duration):
                order = list(binaries)
                rng.shuffle(order)
                for label in order:
                    directory = out / spec['name'] / f'round-{round_index:+06d}-{label}'
                    sample = run_sample(Path(binaries[label]['path']), Path(fixture_id['path']),
                                        Path(meter_id['path']), spec, directory, args.timeout, args.sample_interval,
                                        stdout_sink=args.sink, validated_source=validated)
                    sample.update(case=spec['name'], label=label, round=round_index, warmup=round_index < 0)
                    calibration = report['calibrations'].get(spec['name'])
                    if calibration and spec['output'] == 'text' and spec['expectation'] == 'success' and args.sink == 'pipe':
                        if sample['stdout']['sha256'] != calibration['text_output_sha256']:
                            sample['issues'].append('text output differs from independently drained source plus newline')
                    if calibration and sample['rates']['wall_ms'] > 0:
                        source_rate = sample['fixture_stats'].get('source_bytes', 0) / 1048576 * 1000 / sample['rates']['wall_ms']
                        sample['producer_comparison'] = {
                            'source_mib_per_second': source_rate,
                            'raw_drain_mib_per_second': calibration['source_mib_per_second'],
                            'possibly_producer_bound': source_rate >= calibration['source_mib_per_second'] * .9,
                            'scope': 'single raw-drain calibration; producer/reader ceiling diagnostic, not a speedup claim'}
                    report['samples'].append(sample)
                    print(f"  {label}: {sample['rates']['rows_per_second'] or 0:.0f} rows/s; issues={sample['issues']}", flush=True)
                round_index += 1
                # Always retain an auditable partial report on interruption.
                harness.write_json(out / 'report.json', report)
        report['summary'] = summarize(report['samples'], specs, list(binaries))
        for name, summary in report['summary'].items():
            summary['output_comparison'] = ('full output validated separately before resource-only /dev/null runs'
                                             if args.sink == 'null' else 'full captured stdout in every compared run')
            selected = [s for s in report['samples'] if s['case'] == name and not s['warmup']]
            summary['possibly_producer_bound_runs'] = sum(s.get('producer_comparison', {}).get('possibly_producer_bound', False) for s in selected)
            summary['calibration_available'] = bool(report['calibrations'].get(name, {}).get('valid'))
            if summary['possibly_producer_bound_runs'] or not summary['calibration_available']:
                summary['performance_admitted'] = False
        report['complete'] = True
        report['passed'] = all(not s['issues'] for s in report['samples']) and all(
            c['valid'] and (c['compatible'] or c['observation_only']) for c in report['summary'].values()) and all(
            c['valid'] for c in report['calibrations'].values())
    except Exception as e:
        report['errors'].append(f'{type(e).__name__}: {e}')
        raise
    finally:
        harness.write_json(out / 'report.json', report)
    print(f"Report: {out / 'report.json'}", flush=True)
    return 0 if report['passed'] else 1


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be finite and positive')
    return number


def nonnegative_int(value):
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError('must be nonnegative')
    return n


def binary_label(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise argparse.ArgumentTypeError('label must start with a letter or digit and contain only letters, digits, _, . or -')
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--go', type=Path, required=True)
    p.add_argument('--rust', type=Path, required=True)
    p.add_argument('--reference-label', type=binary_label, default='go',
                   help='report/artifact label for --go; use baseline for a prior Rust binary')
    p.add_argument('--candidate-label', type=binary_label, default='rust',
                   help='report/artifact label for --rust; use candidate for a new Rust binary')
    p.add_argument('--fixture', type=Path, default=Path('bin/pb-stress-fixture'))
    p.add_argument('--meter', type=Path, default=Path('bin/pb-meter'))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--profile', choices=['smoke', 'scale', 'soak', 'extended', 'large', 'transport', 'backpressure', 'deadline'], default='smoke')
    p.add_argument('--case', action='append')
    p.add_argument('--calibrate', action='store_true', help='also drain successful fixtures without a CLI to check producer capacity')
    p.add_argument('--sink', choices=['pipe', 'null'], default='pipe',
                   help='null validates full output first, then measures CLI resources with stdout discarded to /dev/null')
    p.add_argument('--repetitions', type=nonnegative_int, default=1)
    p.add_argument('--warmup', type=nonnegative_int, default=0)
    p.add_argument('--duration', type=positive, default=300, help='minimum wall seconds per soak case, whole paired rounds')
    p.add_argument('--sample-interval', type=positive, default=0.1)
    p.add_argument('--timeout', type=positive, default=130)
    p.add_argument('--seed', type=int, default=2051)
    a = p.parse_args(argv)
    if a.reference_label == a.candidate_label:
        p.error('reference and candidate labels must differ')
    if a.repetitions < 1:
        p.error('--repetitions must be at least one')
    if a.repetitions > 1000 or a.warmup > 100 or not .01 <= a.sample_interval <= 60:
        p.error('require repetitions<=1000, warmup<=100 and sample interval 0.01..60 seconds')
    if not 1 <= a.timeout <= 86400 or not 1 <= a.duration <= 86400:
        p.error('timeout and duration must be 1..86400 seconds')
    if a.sink == 'null' and a.profile not in ('scale', 'soak', 'extended', 'large', 'transport'):
        p.error('--sink null is only for successful resource-measurement profiles')
    return run(a)


if __name__ == '__main__':
    raise SystemExit(main())
