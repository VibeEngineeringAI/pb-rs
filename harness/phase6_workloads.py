#!/usr/bin/env python3
"""Bounded native Flight collection. Campaigns require the coordinator's quiet lock.

No build, profiler, production endpoint, prior-Rust tail or whole-output capture.
The fixture protocol is phase6 v1; all elapsed/resource data comes from pb-meter.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import random
import resource
import selectors
import signal
import statistics
import subprocess
import sys
import time
import urllib.request

import run

TMP = Path.home() / '.cache/pb-phase6/tmp'
PERFORMANCE_LOCK = Path.home() / '.cache/pb-rs-performance.lock'
PREDECLARED_BUDGETS = Path(__file__).resolve().parent.parent / 'docs/evidence/phase6/performance-budgets.json'
WINDOW_CLEANUP_RESERVE_S = 30
SCHEMA_VERSION = 2
MODES = ('steady', 'idle', 'reconnect', 'dictionary', 'slow-sink')
COLLECTOR_MODES = ('full', 'hash-framing')
THROUGHPUT_METRICS = ('rows_per_second', 'output_bytes_per_second')
VISIBILITY_LOG_BYTES = 16 << 20
METRICS = ('wall_ms', 'first_complete_record_ms', 'fixture_to_first_complete_record_ms',
           'cpu_ms', 'cpu_us_per_row', 'peak_rss_bytes', 'rows_per_second', 'output_bytes_per_second')
COMPARABLE_FIELDS = ('stdout_sha256', 'stdout_bytes', 'rendered_rows', 'batches', 'stderr_sha256',
                     'termination', 'requests', 'ticket_sha256', 'request_validation',
                     'workload_sha256', 'sink', 'tls', 'config_before', 'config_after')
NA = {
    'network_wire_bytes': 'not observed; FlightData protobuf sizes exclude HTTP/2/TLS framing',
    'heap_allocations': 'requires a separate profiler campaign',
    'retained_capacities': 'requires candidate diagnostic instrumentation',
    'queue_high_water': 'requires candidate diagnostic instrumentation',
    'client_read_ahead': 'server Send completion does not prove application consumption',
    'server_drops': 'loopback sends do not establish deployed server delivery guarantees',
    'cancellation_latency_ms': 'these workloads terminate by deliberate remote failure',
}


def clock_ns():
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def safe_path(path):
    path = Path(path).expanduser().resolve()
    if path == Path('/tmp') or Path('/tmp') in path.parents:
        raise ValueError('generated artifacts must not use /tmp')
    return path


def file_info(path):
    path = Path(path).resolve(strict=True)
    with path.open('rb') as handle:
        sha = hashlib.file_digest(handle, 'sha256').hexdigest()
    return dict(path=str(path), sha256=sha, size_bytes=path.stat().st_size)


def cpu_us_per_row(cpu_ms, rows):
    return cpu_ms * 1000 / rows if cpu_ms is not None and rows > 0 else None


def write_json(path, value):
    path = safe_path(path)
    temporary = path.with_name(path.name + f'.partial-{os.getpid()}')
    try:
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, limit=4 << 20):
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('JSON artifact exceeds bounded reader limit')
    return json.loads(data)


def read_json_with_identity(path, limit=4 << 20):
    """The parsed parent and its retained identity describe one bounded read."""
    path = Path(path).resolve(strict=True)
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('JSON artifact exceeds bounded reader limit')
    identity = dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    return json.loads(data), identity


class BoundedLog:
    """Keep a prefix on disk; overflow is counted, never buffered or blocking."""
    def __init__(self, path, limit):
        self.stream = safe_path(path).open('xb')
        self.limit, self.written, self.dropped = limit, 0, 0

    def bytes(self, data):
        take = min(len(data), max(0, self.limit - self.written))
        self.stream.write(data[:take])
        self.written += take
        self.dropped += len(data) - take

    def event(self, event):
        data = (json.dumps(event, separators=(',', ':'), allow_nan=False) + '\n').encode()
        # JSONL records stay whole even when the retention bound is reached.
        if self.written + len(data) <= self.limit:
            self.bytes(data)
        else:
            self.dropped += len(data)

    def close(self):
        self.stream.close()


class Trend:
    """Online least-squares slope after declared warmup; constant memory."""
    def __init__(self):
        self.n = 0
        self.mean_x = self.mean_y = self.sxx = self.sxy = 0.0
        self.first = self.last = self.minimum = self.maximum = None

    def add(self, seconds, value):
        self.n += 1
        dx, dy = seconds - self.mean_x, value - self.mean_y
        self.mean_x += dx / self.n
        self.mean_y += dy / self.n
        self.sxx += dx * (seconds - self.mean_x)
        self.sxy += dx * (value - self.mean_y)
        self.first = value if self.first is None else self.first
        self.last = value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    def result(self):
        return dict(samples=self.n, first=self.first, last=self.last, minimum=self.minimum,
                    maximum=self.maximum, slope_per_second=self.sxy / self.sxx if self.sxx else None)


class Output:
    """LF-framed complete objects plus exact batch blank lines, bounded partial row."""
    def __init__(self, root, prefix_bytes=65536, record_bytes=16 << 20, event_bytes=VISIBILITY_LOG_BYTES,
                 collector_mode='full'):
        if collector_mode not in COLLECTOR_MODES:
            raise ValueError('unknown collector mode')
        self.collector_mode = collector_mode
        self.prefix = BoundedLog(root / 'stdout.prefix.bin', prefix_bytes)
        self.events = BoundedLog(root / 'visibility.jsonl', event_bytes)
        self.sha = hashlib.sha256()
        self.partial = bytearray()
        self.line_length = 0
        self.record_limit = record_bytes
        self.overflow = False
        self.rows = self.batches = self.total = self.invalid = self.order_errors = 0
        self.first_record = self.last_record = self.first_byte = self.last_byte = None
        self.first_batch = None
        self.sequence = 0
        self.json_parse_attempts = 0

    def feed(self, data, now):
        self.sha.update(data)
        self.prefix.bytes(data)
        self.total += len(data)
        self.first_byte = now if self.first_byte is None else self.first_byte
        self.last_byte = now
        # Splitting is limited by the read chunk (64 KiB), not stream length.
        parts = data.split(b'\n')
        for i, part in enumerate(parts):
            self.line_length += len(part)
            if self.line_length > self.record_limit:
                self.overflow = True
                self.partial.clear()
            elif not self.overflow and (self.collector_mode == 'full' or self.first_record is None):
                self.partial.extend(part)
            if i == len(parts) - 1:
                break
            if self.overflow:
                self.invalid += 1
                self.overflow = False
            elif not self.line_length:
                self.first_batch = now if self.first_batch is None else self.first_batch
                self.events.event(dict(kind='batch_visible', batch=self.batches, t_ns=now, rows=self.rows))
                self.batches += 1
            else:
                try:
                    if self.collector_mode == 'full' or self.first_record is None:
                        self.json_parse_attempts += 1
                        row = json.loads(self.partial, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
                        if not isinstance(row, dict):
                            raise ValueError('row is not an object')
                        if self.collector_mode == 'full':
                            if row.get('sequence') != self.sequence:
                                self.order_errors += 1
                            self.sequence += 1
                    if self.first_record is None:
                        self.first_record = now
                        self.events.event(dict(kind='first_record', t_ns=now))
                    self.last_record = now
                    self.rows += 1
                except (ValueError, UnicodeError, RecursionError):
                    self.invalid += 1
            self.partial.clear()
            self.line_length = 0

    def finish(self):
        if self.line_length or self.overflow:
            self.invalid += 1
        self.prefix.close()
        self.events.close()
        return dict(stdout_bytes=self.total, stdout_sha256=self.sha.hexdigest(), rows=self.rows,
                    batches=self.batches, invalid_records=self.invalid,
                    sequence_errors=self.order_errors if self.collector_mode == 'full' else None,
                    collector_mode=self.collector_mode, json_parse_attempts=self.json_parse_attempts,
                    row_validation='per-record JSON and sequence' if self.collector_mode == 'full' else
                        'first complete object only; later LF frames require fixture full-stream SHA256',
                    first_record_ns=self.first_record, first_batch_ns=self.first_batch,
                    last_record_ns=self.last_record, first_stdout_ns=self.first_byte,
                    last_stdout_ns=self.last_byte, visibility_dropped_bytes=self.events.dropped,
                    stdout_prefix_dropped_bytes=self.prefix.dropped)


def proc_sample(supervisor, proc_root=Path('/proc')):
    """Live direct child only; procfs is sampled sequentially, not atomically."""
    observed_start = clock_ns()
    try:
        children = (proc_root / f'{supervisor}/task/{supervisor}/children').read_text().split()
        if len(children) != 1:
            return None
        pid = int(children[0])
        child = proc_root / str(pid)
        status = (child / 'status').read_text()
        fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
        stat = (child / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields['State'].split()[0] in ('Z', 'X', 'x') or stat[0] in ('Z', 'X', 'x'):
            return None
        rss = int(fields['VmRSS'].split()[0]) * 1024
        fds = sum(1 for _ in (child / 'fd').iterdir())
        tasks = int(fields['Threads'].strip())
        # A child can exit while status/FDs are being read. Reject that sample,
        # and reject a changed start time rather than mixing reused PID data.
        final_stat = (child / 'stat').read_text().rsplit(')', 1)[1].split()
        if final_stat[0] in ('Z', 'X', 'x') or final_stat[19] != stat[19]:
            return None
        return dict(t_ns=clock_ns(), observed_start_ns=observed_start, pid=pid,
                    process_state=final_stat[0], rss_bytes=rss, fds=fds, tasks=tasks,
                    cpu_user_ms=int(stat[11]) * 1000 / os.sysconf('SC_CLK_TCK'),
                    cpu_system_ms=int(stat[12]) * 1000 / os.sysconf('SC_CLK_TCK'))
    except (OSError, KeyError, ValueError, IndexError):
        return None


def collect(argv, env, root, meter, timeout_s, sink=None, sample_ms=200, trend_warmup_s=5,
            collector_mode='full'):
    """Single-threaded selector drain; slow sink throttles stdout only, never stderr."""
    observer_start_ns = clock_ns()
    observer_cpu_before = resource.getrusage(resource.RUSAGE_SELF)
    root = safe_path(root)
    root.mkdir(parents=True, exist_ok=True)
    sink = sink or {}
    output = Output(root, collector_mode=collector_mode)
    stderr = BoundedLog(root / 'stderr.prefix.bin', 1 << 20)
    trends = {key: Trend() for key in ('rss_bytes', 'fds', 'tasks', 'cpu_user_ms', 'cpu_system_ms')}
    samples = BoundedLog(root / 'resources.jsonl', 8 << 20)
    errors, errsha, errbytes = [], hashlib.sha256(), 0
    metrics_path = root / 'meter.json'
    proc = None
    observed_start = clock_ns()
    next_sample = observed_start
    next_read = observed_start + int(sink.get('pause_s', 0) * 1e9)
    timed_out = False
    status = None
    sample_count = missing_samples = 0
    try:
        proc = run.spawn_measured([str(meter), str(metrics_path), '--', *map(str, argv)], env, root)
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stderr, selectors.EVENT_READ, 'stderr')
            stdout_registered = False
            while status is None or selector.get_map() or not proc.stdout.closed:
                now = clock_ns()
                if now - observed_start > timeout_s * 1e9 and not timed_out:
                    timed_out = True
                    run.kill_group(proc, signal.SIGKILL)
                if now >= next_sample:
                    value = proc_sample(proc.pid)
                    if value:
                        sample_count += 1
                        seconds = (value['t_ns'] - observed_start) / 1e9
                        samples.event(value)
                        if seconds >= trend_warmup_s:
                            for key, trend in trends.items():
                                trend.add(seconds, value[key])
                    else:
                        missing_samples += 1
                    next_sample = now + int(sample_ms * 1e6)
                if not proc.stdout.closed and not stdout_registered and now >= next_read:
                    selector.register(proc.stdout, selectors.EVENT_READ, 'stdout')
                    stdout_registered = True
                for key, _ in selector.select(0.02):
                    data = os.read(key.fd, sink.get('read_chunk_bytes', 65536) if key.data == 'stdout' else 65536)
                    stamp = clock_ns()
                    if not data:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        if key.data == 'stdout':
                            stdout_registered = False
                        continue
                    if key.data == 'stdout':
                        output.feed(data, stamp)
                        delay = sink.get('read_delay_ms', 0)
                        if delay:
                            selector.unregister(proc.stdout)
                            stdout_registered = False
                            next_read = stamp + int(delay * 1e6)
                    else:
                        stderr.bytes(data)
                        errsha.update(data)
                        errbytes += len(data)
                if status is None:
                    waited, raw = os.waitpid(proc.pid, os.WNOHANG)
                    if waited:
                        status = os.waitstatus_to_exitcode(raw)
    except BaseException:
        if proc:
            run.kill_group(proc, signal.SIGKILL)
        raise
    finally:
        if proc:
            if status is None:
                _, raw = os.waitpid(proc.pid, 0)
                status = os.waitstatus_to_exitcode(raw)
            for pipe in (proc.stdout, proc.stderr):
                if not pipe.closed:
                    pipe.close()
        result = output.finish()
        stderr.close()
        samples.close()
    observer_cpu_after = resource.getrusage(resource.RUSAGE_SELF)
    observer_end_ns = clock_ns()
    try:
        native = read_json(metrics_path)
        counters = ('cpu_user_ms', 'cpu_system_ms', 'peak_rss_bytes', 'minor_faults', 'major_faults',
                    'voluntary_context_switches', 'involuntary_context_switches',
                    'block_input_operations', 'block_output_operations')
        for key in ('started_monotonic_ns', 'ended_monotonic_ns', 'termination', 'exit_code', 'resource_source', *counters):
            if key not in native:
                raise ValueError('meter missing ' + key)
        if native['resource_source'] != 'native_child_wait4':
            raise ValueError('meter has wrong resource scope')
        if any(not isinstance(native[key], (int, float)) or not math.isfinite(native[key]) or native[key] < 0
               for key in counters):
            raise ValueError('negative/nonfinite native counter')
        result.update(native)
        start, end = native['started_monotonic_ns'], native['ended_monotonic_ns']
        if end < start:
            raise ValueError('meter clock reversed')
        result['wall_ms'] = (end - start) / 1e6
        result['cpu_ms'] = native['cpu_user_ms'] + native['cpu_system_ms']
        for target, source in (('first_complete_record_ms', 'first_record_ns'), ('first_stdout_ms', 'first_stdout_ns')):
            result[target] = (result[source] - start) / 1e6 if result[source] is not None else None
        if result['first_complete_record_ms'] is not None and result['first_complete_record_ms'] < 0:
            errors.append('first complete record precedes native start')
        result['first_record_observed_after_native_exit'] = (result['first_record_ns'] > end
            if result['first_record_ns'] is not None else None)
    except (OSError, ValueError, TypeError, KeyError) as error:
        errors.append(f'invalid native meter: {error}')
    if timed_out:
        errors.append('collector deadline exceeded')
    if status != 0:
        errors.append(f'meter supervisor exit {status}')
    if result['invalid_records'] or result['sequence_errors']:
        errors.append('invalid/incomplete records or sequence gap/duplicate')
    if stderr.dropped:
        errors.append('stderr exceeded retention cap (exact diagnostic unavailable)')
    result.update(stderr_bytes=errbytes, stderr_sha256=errsha.hexdigest(), errors=errors,
                  timed_out=timed_out, sink=sink, sampled_resources={k: v.result() for k, v in trends.items()},
                  sample_count=sample_count, missing_proc_samples=missing_samples,
                  resources_dropped_bytes=samples.dropped, trend_warmup_s=trend_warmup_s,
                  observer_cpu=dict(start_ns=observer_start_ns, end_ns=observer_end_ns,
                    user_ms=(observer_cpu_after.ru_utime - observer_cpu_before.ru_utime) * 1000,
                    system_ms=(observer_cpu_after.ru_stime - observer_cpu_before.ru_stime) * 1000,
                    scope='collector RUSAGE_SELF around collect, excludes children and post-capture validation'),
                  not_measured=NA)
    for name, count in (('rows_per_second', result['rows']), ('output_bytes_per_second', result['stdout_bytes'])):
        result[name] = count / (result['wall_ms'] / 1000) if result.get('wall_ms', 0) else None
    result['cpu_us_per_row'] = cpu_us_per_row(result.get('cpu_ms'), result['rows'])
    return result


def workloads(duration_s=1800, tier='finite', seed=2026):
    if not 0 < duration_s <= 3600:
        raise ValueError('duration must be in (0, 3600] seconds')
    fatal = dict(op='status', code=3, message='fixture stream complete')
    result = []

    def add(name, ops, schema='basic', sink=None, subscriptions=None, **options):
        result.append(dict(name=name, mode=name, duration_s=duration_s if tier != 'finite' else None,
                           stream=dict(schema=schema, seed=seed,
                                       subscriptions=subscriptions or [dict(ops=ops + [fatal])], **options),
                           sink=sink or {}, timeout_s=(duration_s + 120) if tier != 'finite' else 120))
    if tier == 'finite':
        for name, rows, count in (('one-row', 1, 1), ('many-small', 1, 128), ('large', 4096, 4), ('wide', 256, 8)):
            add(name, [dict(op='batch', rows=rows, count=count)], schema='wide' if name == 'wide' else 'basic')
        add('dictionary-finite', [dict(op='batch', rows=128, count=16)], schema='dictionary',
            dictionary=dict(cardinality=16, mode='replace', value_bytes=64, max_entries=4096))
        for compression in ('lz4', 'zstd'):
            add('dictionary-large-' + compression,
                [dict(op='batch', rows=4, count=9), dict(op='sleep', ms=500)], schema='dictionary',
                compression=compression,
                dictionary=dict(cardinality=16, mode='cycle', value_bytes=1048576, max_entries=64))
            result[-1]['resource_pressure'] = True
        for compression in ('lz4', 'zstd'):
            add('compressed-' + compression, [dict(op='batch', rows=2048, count=4)], compression=compression)
        add('empty-batch', [dict(op='batch', rows=0, count=1)])
        add('paced', [dict(op='live', rows_per_batch=32, batches=8, interval_ms=20)])
        add('slow-finite', [dict(op='batch', rows=128, count=16)], sink=dict(read_delay_ms=10, pause_s=0.1))
    else:
        live = dict(op='live', rows_per_batch=128, interval_ms=20, duration_ms=round(duration_s * 1000))
        add('steady', [live])
        add('idle', [dict(op='schema'), dict(op='sleep', ms=round(duration_s * 1000))])
        # Each reconnect has an explicit server delay plus the client's fixed 500ms.
        cycles = max(1, math.ceil(duration_s / 0.6))
        subs = [dict(repeat=cycles, ops=[dict(op='batch', rows=8), dict(op='sleep', ms=100), dict(op='eof')]),
                dict(ops=[dict(op='schema'), fatal])]
        add('reconnect', [], subscriptions=subs)
        result[-1]['timeout_s'] = cycles * 0.6 + max(120, cycles * 0.02)
        result[-1]['scheduled_reconnects'] = cycles
        add('dictionary', [live], schema='dictionary',
            dictionary=dict(cardinality=16, mode='cycle', value_bytes=64, max_entries=4096))
        add('slow-sink', [live], sink=dict(read_delay_ms=20, read_chunk_bytes=4096,
                                        pause_s=min(10, duration_s / 4)))
    return result


class Fixture:
    def __init__(self, binary, workload, root, tls=False):
        self.root = root
        self.proc = None
        auth = {'authorization': 'Basic aGFybmVzczpoYXJuZXNz'}
        stream = dict(workload['stream'], expected_ticket='{"stream":"logs"}')
        p6 = dict(events_path=str(root / 'fixture.events.jsonl'), summary_path=str(root / 'fixture.summary.json'),
                  event_limit=20000, batch_event_sample=100, expected_output=True,
                  flight_auth=auth, streams={'logs': stream})
        allowed = ('event_limit', 'event_hard_limit', 'batch_event_sample', 'event_sample', 'trace_limit')
        p6.update({key: workload['fixture_limits'][key] for key in allowed
                   if key in workload.get('fixture_limits', {})})
        if tls:
            p6['tls'] = dict(grpc=True, http=False, ca_path=str(root / 'ca.pem'), names=['127.0.0.1'])
        scenario = dict(headers=auth, routes=[dict(method='GET', path='/api/v1/about', status=200,
                                                 body='{"grpcPort":{{grpc_port}}}')], phase6=p6)
        write_json(root / 'scenario.json', scenario)
        self.log = (root / 'fixture.stderr').open('wb')
        self.go_env = dict(GOMAXPROCS=str(os.cpu_count() or 1), GOGC='100', GODEBUG='', GOMEMLIMIT='off')
        env = dict(PATH=os.defpath, HOME=str(root), LANG='C', LC_ALL='C', TZ='UTC',
                   TMPDIR=str(TMP), GOTMPDIR=str(TMP), CLAUDE_CODE_TMPDIR=str(TMP), **self.go_env)
        self.proc = subprocess.Popen([str(binary), '--scenario', str(root / 'scenario.json')],
                                     stdout=subprocess.PIPE, stderr=self.log, env=env, start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise RuntimeError('fixture startup deadline')
            self.info = json.loads(self.proc.stdout.readline(65537))
            self.url = self.info['url']
        except BaseException:
            self.close()
            raise

    def summary(self):
        # Read at most a bounded summary; never fetch the cumulative legacy trace.
        url = self.info.get('control_url', self.url) + '/__harness/phase6/summary'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        started = clock_ns()
        with opener.open(url, timeout=5) as response:
            data = response.read((4 << 20) + 1)
        if len(data) > 4 << 20:
            raise ValueError('fixture summary exceeds bound')
        result = json.loads(data)
        result['collector_request_bounds'] = dict(start_ns=started, end_ns=clock_ns())
        return result

    def close(self):
        if self.proc:
            run.kill_group(self.proc, signal.SIGKILL)
            self.proc.wait()
            self.proc.stdout.close()
        self.log.close()


def jsonl(path, line_limit=65536):
    with Path(path).open('rb') as stream:
        while True:
            line = stream.readline(line_limit + 1)
            if not line:
                break
            if len(line) > line_limit or not line.endswith(b'\n'):
                raise ValueError('oversized or incomplete event')
            yield json.loads(line)


def tail_resources(directory):
    """Optional completed diagnostic sidecar; owner maxima, never transport read-ahead."""
    paths = list(safe_path(directory).glob('pb-diagnostics-*.json'))
    if len(paths) != 1:
        raise ValueError('missing or ambiguous completed tail diagnostics')
    report = read_json(paths[0])
    if report.get('schema_version') != 1 or report.get('complete') is not True or report.get('implementation') != 'rust':
        raise ValueError('incomplete or unsupported diagnostic sidecar')
    resources = report.get('tail_resources')
    counters = ('subscriptions_started', 'flight_messages_received', 'batches_decoded', 'rows_decoded',
                'max_flight_payload_bytes', 'max_dictionary_buffer_bytes', 'max_batch_buffer_bytes',
                'max_staging_length_bytes', 'max_staging_capacity_bytes', 'last_staging_capacity_bytes',
                'max_application_messages_inflight', 'max_renderer_scratch_capacity_bytes',
                'last_renderer_scratch_capacity_bytes')
    if not isinstance(resources, dict) or not isinstance(resources.get('semantics'), str):
        raise ValueError('tail ownership observations unavailable')
    if any(type(resources.get(key)) is not int or resources[key] < 0 for key in counters):
        raise ValueError('missing/invalid tail ownership counter')
    return dict(source=file_info(paths[0]), resources=resources,
                scope='owner-reported maxima; not simultaneous totals, unique heap, RSS or transport read-ahead')


def output_expectation_issues(result, stream):
    # Both observers require the same independent whole-stream byte contract.
    return ['fixture expectation mismatch: ' + target
            for target, source in (('stdout_sha256', 'expected_stdout_sha256'),
                                   ('stdout_bytes', 'expected_stdout_bytes'),
                                   ('rows', 'expected_rendered_rows'), ('batches', 'batches'))
            if stream.get(source) is None or result[target] != stream[source]]


def request_summary_issues(summary, stream, expected_dogets):
    """Require exact coverage, never infer unobserved auth/tickets from sampled logs."""
    issues = []
    checks = stream.get('request_validation')
    dogets = stream.get('dogets')
    if type(dogets) is not int or dogets != expected_dogets:
        issues.append('exact subscription count differs or is unavailable')
    if summary.get('about_requests') != 1 or summary.get('http_requests') != 1:
        issues.append('exact discovery/HTTP counts differ or are unavailable')
    if summary.get('dogets') != dogets:
        issues.append('global/stream DoGet counts differ or are unavailable')
    if not isinstance(checks, dict):
        issues.append('exact request validation evidence unavailable')
    else:
        for key in ('auth_checks', 'ticket_checks'):
            if type(checks.get(key)) is not int or checks[key] != dogets:
                issues.append('exact request validation coverage missing: ' + key)
        for key in ('invalid_auth', 'ticket_mismatches', 'unexpected_requests', 'doget_before_about'):
            if type(checks.get(key)) is not int or checks[key] != 0:
                issues.append('request validation failed or unavailable: ' + key)
    names = ((summary, 'first_about_ns'), (summary, 'last_about_ns'), (summary, 'about_completed_ns'),
             (stream, 'first_doget_ns'), (stream, 'last_doget_ns'))
    if any(type(source.get(key)) is not int or source[key] <= 0 for source, key in names):
        issues.append('exact discovery/DoGet timestamps unavailable')
    elif not (summary['first_about_ns'] <= summary['last_about_ns'] <= summary['about_completed_ns'] <=
              stream['first_doget_ns'] <= stream['last_doget_ns']):
        issues.append('exact discovery/DoGet ordering differs')
    return issues


def schedule_admission(workload, stream):
    scheduled = 0
    live_ops = 0
    for sub in workload['stream']['subscriptions']:
        for op in sub['ops']:
            if op['op'] != 'live':
                continue
            live_ops += sub.get('repeat', 1)
            by_count = op.get('batches') or math.inf
            duration, interval = op.get('duration_ms', 0), op.get('interval_ms', 0)
            if duration and not interval:
                return dict(errors=['duration live interval zero has no deterministic schedule'], throughput_admissible=False)
            by_time = math.ceil(duration / interval) if duration and interval else math.inf
            count = min(by_count, by_time)
            if not math.isfinite(count):
                return dict(errors=['unbounded live production schedule'], throughput_admissible=False)
            scheduled += int(count) * sub.get('repeat', 1)
    result = dict(errors=[], scheduled_batches=scheduled, delivered_batches=stream.get('batches'),
                  throughput_admissible=True, mode='finite' if not live_ops else 'schedule_bound')
    if workload.get('resource_pressure'):
        result.update(mode='resource_pressure', throughput_admissible=False)
    if not live_ops and workload['stream'].get('dictionary', {}).get('mode') != 'cycle':
        return result
    live = stream.get('live')
    if live_ops and (not isinstance(live, dict) or live.get('ops') != live_ops or live.get('scheduled_batches') != scheduled):
        result['errors'].append('exact live schedule accounting unavailable or mismatched')
        result['throughput_admissible'] = False
        return result
    if live_ops:
        result.update(live=live, delivered_batches=live.get('emitted_batches'),
                      producer_deadline_ns=live.get('last', {}).get('deadline_ns'),
                      send_call_semantics='marshaling/copy/flow-control Send wall; not pure blocking time')
        complete = (live.get('emitted_batches') == scheduled and stream.get('batches') == scheduled and
                    live.get('omitted_by_deadline') == 0 and live.get('omitted_by_termination') == 0)
        result['schedule_complete'] = complete
        emitted = live.get('emitted_batches', 0)
        if (live.get('lateness_ns', {}).get('count') != emitted or
            live.get('send_call_ns', {}).get('count', -1) < emitted or
            sum(live.get('stopped_by_' + k, 0) for k in ('count', 'deadline', 'termination')) != live_ops):
            result['errors'].append('live stop/lateness/Send accounting inconsistent')
        if workload['name'] == 'slow-sink':
            result.update(mode='sink_limited', throughput_admissible=False)
        elif not complete:
            result['errors'].append('production schedule incomplete: delivered fewer/different scheduled batches')
            result['throughput_admissible'] = False
    dictionary = workload['stream'].get('dictionary', {})
    if dictionary.get('mode') == 'cycle':
        counts, buckets = stream.get('dictionary'), stream.get('buckets')
        if not isinstance(counts, dict) or not isinstance(buckets, dict):
            result['errors'].append('cyclic dictionary exact churn evidence unavailable')
        else:
            emitted = stream.get('batches', 0)
            cycle = math.ceil(dictionary['max_entries'] / dictionary['cardinality'])
            needed_resets = max(0, (emitted - 1) // cycle)
            if (counts.get('messages') != emitted or counts.get('resets') != needed_resets or
                counts.get('replacements') != needed_resets or counts.get('initial') != int(emitted > 0) or
                counts.get('deltas') != max(0, emitted - 1 - needed_resets) or
                counts.get('max_size') != min(dictionary['max_entries'], emitted * dictionary['cardinality'])):
                result['errors'].append('cyclic dictionary churn/reset count differs')
            rows = buckets.get('counts', [])
            if buckets.get('overflow') != 0 or sum(x.get('batches', 0) for x in rows) != emitted:
                result['errors'].append('dictionary bucket coverage incomplete')
            if any(x.get('batches', 0) and x.get('dictionary_messages') != x['batches'] for x in rows):
                result['errors'].append('dictionary churn missing from a production bucket')
            if any(sum(x.get(bucket_key, 0) for x in rows) != counts.get(counter_key)
                   for bucket_key, counter_key in (('dictionary_messages', 'messages'),
                       ('dictionary_deltas', 'deltas'), ('dictionary_replacements', 'replacements'))):
                result['errors'].append('dictionary bucket churn totals differ')
            result['dictionary_churn'] = dict(counters=counts, buckets=buckets, required_resets=needed_resets)
    return result


def host_snapshot():
    started = clock_ns()
    try:
        fields = Path('/proc/stat').read_text().splitlines()[0].split()
        names = ('user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal')
        if fields[0] != 'cpu' or len(fields) < 9:
            raise ValueError('missing aggregate CPU counters')
        counters = dict(zip(names, map(int, fields[1:9])))
        load = Path('/proc/loadavg').read_text().strip()
        return dict(start_ns=started, end_ns=clock_ns(), counters=counters, loadavg_raw=load,
                    ticks_per_second=os.sysconf('SC_CLK_TCK'), cpu_count=os.cpu_count(), status='observed')
    except (OSError, ValueError, IndexError) as error:
        return dict(start_ns=started, end_ns=clock_ns(), status='unavailable', reason=str(error))


def cpu_screen(result, before, after, host_before, host_after, controls):
    """Conservative interval envelope; no exact non-campaign attribution claim."""
    ceiling = controls['cpu_ceiling_screen']
    interference = controls['interference_screen']
    observer = result.get('observer_cpu', {})
    fixture = {}
    for part in ('user', 'system'):
        a, b = before.get(f'fixture_cpu_{part}_ns'), after.get(f'fixture_cpu_{part}_ns')
        fixture[part + '_ms'] = (b - a) / 1e6 if type(a) is int and type(b) is int else None
    fixture['start_bounds'] = before.get('collector_request_bounds')
    fixture['end_bounds'] = after.get('collector_request_bounds')
    fixture['scope'] = 'fixture lifetime CPU difference between pre-meter and post-exit summary snapshots'
    a, b = before.get('expected_output_ns_total'), after.get('expected_output_ns_total')
    fixture['expected_output_encoding_wall_ms'] = (b - a) / 1e6 if type(a) is int and type(b) is int else None
    fixture['expected_output_encoding_semantics'] = 'monotonic encoding wall time; overlaps fixture CPU; never subtracted from native CPU'
    screen = dict(observer=observer, fixture=fixture, host_before=host_before, host_after=host_after,
                  comparative_admissible=False, throughput_admissible=False,
                  interference_status='unavailable', potential_limit_status='unavailable')
    elapsed = result.get('wall_ms')
    observer_cpu = sum(observer.get(key, math.nan) for key in ('user_ms', 'system_ms'))
    fixture_cpu = sum(fixture.get(key) if fixture.get(key) is not None else math.nan for key in ('user_ms', 'system_ms'))
    if not elapsed or not all(math.isfinite(x) and x >= 0 for x in (observer_cpu, fixture_cpu)):
        return screen
    screen.update(observer_busy_cores=observer_cpu / elapsed, fixture_busy_cores=fixture_cpu / elapsed)
    screen['potential_limit_status'] = ('resolution_limited' if elapsed < ceiling['minimum_observation_ms'] else
        'fixture_or_observer_bound' if screen['observer_busy_cores'] >= ceiling['observer_busy_fraction'] or
        screen['fixture_busy_cores'] >= ceiling['fixture_busy_fraction'] else 'below_screen')
    screen['throughput_admissible'] = screen['potential_limit_status'] == 'below_screen'
    if host_before.get('status') != 'observed' or host_after.get('status') != 'observed':
        return screen
    raw = {key: host_after['counters'][key] - host_before['counters'][key] for key in host_before['counters']}
    screen['system_tick_deltas'] = raw
    if any(value < 0 for value in raw.values()):
        screen['interference_status'] = 'counter_error'
        return screen
    host_start, host_end = host_before['start_ns'], host_after['end_ns']
    host_ms = (host_end - host_start) / 1e6
    busy_ms = (sum(raw.values()) - raw['idle'] - raw['iowait']) * 1000 / host_before['ticks_per_second']
    screen.update(host_elapsed_ms=host_ms, system_busy_ms=busy_ms,
                  client_cpu_ms=result.get('cpu_ms'), observer_cpu_ms=observer_cpu, fixture_cpu_ms=fixture_cpu)
    if host_ms < interference['minimum_observation_ms'] or elapsed < interference['minimum_observation_ms']:
        screen['interference_status'] = 'resolution_limited'
        return screen
    starts, ends = fixture.get('start_bounds'), fixture.get('end_bounds')
    native_start, native_end = result.get('started_monotonic_ns'), result.get('ended_monotonic_ns')
    if not starts or not ends or not native_start or not native_end or not observer.get('start_ns') or not observer.get('end_ns'):
        return screen
    cpus = host_before.get('cpu_count')
    if not cpus or cpus != host_after.get('cpu_count'):
        screen['interference_status'] = 'unaligned'
        return screen
    bounds = [(native_start, native_end), (observer['start_ns'], observer['end_ns']),
              (starts['start_ns'], ends['end_ns'])]
    # Account for unobserved CPU inside the host interval and possible fixture
    # CPU outside it; all-core bound intentionally marks short mismatches ambiguous.
    mismatch_ms = sum(abs(a - host_start) + abs(b - host_end) for a, b in bounds) / 1e6
    uncertainty_ms = mismatch_ms * cpus + 2000 * cpus / host_before['ticks_per_second']
    residual = busy_ms - result['cpu_ms'] - observer_cpu - fixture_cpu
    lower = max(0, residual - uncertainty_ms) / host_ms
    upper = max(0, residual + uncertainty_ms) / host_ms
    threshold = interference['max_unattributed_busy_cores']
    status = 'flagged' if lower > threshold else 'below_screen' if upper <= threshold else 'ambiguous'
    screen.update(interference_status=status, residual_raw_ms=residual,
                  unattributed_busy_cores_estimate=max(0, residual) / host_ms,
                  unattributed_busy_cores_range=[lower, upper], interval_uncertainty_ms=uncertainty_ms,
                  comparative_admissible=status == 'below_screen',
                  attribution='screening interval envelope, not exact attribution; no clean/quiescence claim')
    return screen


def correlate(root, result, stream=None):
    """Merge global batch ordinals; missing sampled events remain N/A."""
    retained = BoundedLog(root / 'batch-latencies.jsonl', 8 << 20)
    latency = Trend()
    origin = (stream or {}).get('buckets', {}).get('origin_ns', result.get('started_monotonic_ns', 0))
    coverage = {}
    def bucket(stamp):
        index = min(511, max(0, (stamp - origin) // 60000000000))
        return coverage.setdefault(index, dict(visible=0, correlated=0))
    done = (event for event in jsonl(root / 'fixture.events.jsonl') if event.get('kind') == 'batch_emit_start')
    emitted = next(done, None)
    matched = 0
    try:
        for visible in jsonl(root / 'visibility.jsonl'):
            if visible['kind'] != 'batch_visible':
                continue
            bucket(visible['t_ns'])['visible'] += 1
            while emitted and emitted['batch'] < visible['batch']:
                emitted = next(done, None)
            if emitted and emitted['batch'] == visible['batch']:
                delta = (visible['t_ns'] - emitted['t_ns']) / 1e6
                if delta < 0:
                    result['errors'].append('fixture/collector CLOCK_MONOTONIC correlation reversed')
                retained.event(dict(batch=visible['batch'], emission_to_batch_visible_ms=delta,
                                    visible_ns=visible['t_ns'], emission_ns=emitted['t_ns']))
                latency.add(matched, delta)
                matched += 1
                bucket(visible['t_ns'])['correlated'] += 1
        # First record may arrive before the batch separator; its independent milestone is retained.
        if stream is None:
            # Standalone fixture-event unit tests retain this explicit fallback.
            first = next((e for e in jsonl(root / 'fixture.events.jsonl')
                          if e.get('kind') == 'batch_emit_start' and e.get('rows', 0) > 0), None)
            first_ns = first['t_ns'] if first else None
            first_source = 'retained_event' if first else 'unavailable'
        else:
            first_ns = stream.get('first_nonempty_batch_emit_ns')
            first_source = 'exact_summary' if first_ns is not None else 'unavailable'
        result['fixture_first_record_emission_source'] = first_source
        result['fixture_to_first_complete_record_ms'] = ((result['first_record_ns'] - first_ns) / 1e6
            if type(first_ns) is int and result['first_record_ns'] is not None else None)
        if result['fixture_to_first_complete_record_ms'] is not None and result['fixture_to_first_complete_record_ms'] < 0:
            result['errors'].append('first record precedes fixture emission on common clock')
        result['batch_visibility'] = dict(matched=matched, uncorrelated=result['batches'] - matched,
                                          latency_ms=latency.result(), retained_dropped_bytes=retained.dropped,
                                          coverage_origin_ns=origin, coverage_bucket_ms=60000,
                                          coverage_by_time=[dict(index=k, **v) for k, v in sorted(coverage.items())])
    finally:
        retained.close()


def run_sample(binary, workload, fixture_binary, meter, root, tls=False, diagnostics=False,
               collector_mode='full'):
    root = safe_path(root)
    root.mkdir(parents=True)
    fixture = Fixture(fixture_binary, workload, root, tls)
    try:
        run.seed_config(root, fixture.url)
        env = run.isolated_env(root)
        env.update(TMPDIR=str(TMP), GOTMPDIR=str(TMP), CLAUDE_CODE_TMPDIR=str(TMP))
        if diagnostics:
            env['PB_DIAGNOSTICS_DIR'] = str(root / 'diagnostics')
        if tls:
            env['SSL_CERT_FILE'] = fixture.info['ca_path']
        config = root / 'config/pb/config.toml'
        before = config.read_bytes()
        baseline = fixture.summary()
        write_json(root / 'fixture-baseline-summary.json', baseline)
        host_before = host_snapshot()
        result = collect([binary, 'tail', 'logs', '-o', 'json'], env, root, meter,
                         workload['timeout_s'], workload['sink'], collector_mode=collector_mode)
        result.update(valid=False, capture_complete=True, contract_validated=False)
        # Completed observations survive failures in every subsequent validator.
        write_json(root / 'capture.json', result)
        write_json(root / 'sample.json', result)
        host_after = host_snapshot()
        summary = fixture.summary()
        write_json(root / 'fixture-final-summary.json', summary)
        if summary.get('event_kinds', {}).get('fixture_panic', 0):
            result['errors'].append('fixture panic counter is nonzero')
        stream = summary['streams']['logs']
        result['production'] = schedule_admission(workload, stream)
        result['errors'] += result['production']['errors']
        controls = read_json(PREDECLARED_BUDGETS)['campaign_controls']
        result['cpu_screen'] = cpu_screen(result, baseline, summary, host_before, host_after, controls)
        result['fixture_go_env'] = fixture.go_env
        result['errors'] += output_expectation_issues(result, stream)
        if result.get('termination') != {'exit': 1}:
            result['errors'].append('expected failure-terminated exit 1, never a successful live completion')
        stderr = (root / 'stderr.prefix.bin').read_bytes()
        if b'COMMAND_FAILED' not in stderr or b'InvalidArgument' not in stderr:
            result['errors'].append('missing fatal InvalidArgument COMMAND_FAILED diagnostic')
        terminal = stream.get('last_terminal')
        if not isinstance(terminal, dict) or terminal.get('code') != 'InvalidArgument':
            result['errors'].append('fixture terminal is not deliberate InvalidArgument')
        if config.read_bytes() != before:
            result['errors'].append('tail changed isolated config')
        expected_dogets = workload.get('scheduled_reconnects', 0) + 1
        result['errors'] += request_summary_issues(summary, stream, expected_dogets)
        # Retained events corroborate their own contents, never total coverage.
        dogets = stream.get('dogets')
        first_doget_ns = stream.get('first_doget_ns')
        terminal_ns = terminal.get('t_ns') if isinstance(terminal, dict) else None
        captured_dogets = captured_abouts = 0
        captured_terminal = False
        for event in jsonl(root / 'fixture.events.jsonl'):
            if event.get('kind') == 'fixture_panic':
                result['errors'].append('fixture panic event observed')
            if event.get('kind') == 'http' and event.get('path') == '/api/v1/about':
                captured_abouts += 1
            if event.get('kind') == 'doget':
                captured_dogets += 1
                completed = summary.get('about_completed_ns')
                if (type(completed) is int and event['t_ns'] < completed) or not event.get('authorized') or event.get('unexpected'):
                    result['errors'].append('invalid captured discovery/Flight ordering or authorization')
                if event.get('ticket') != '{"stream":"logs"}':
                    result['errors'].append('ticket bytes differ')
                if event.get('ticket_matches') is False:
                    result['errors'].append('captured ticket mismatch')
                if event.get('metadata', {}).get('authorization') not in ('Basic aGFybmVzczpoYXJuZXNz', ['Basic aGFybmVzczpoYXJuZXNz']):
                    result['errors'].append('Flight basic metadata differs')
            if event.get('kind') == 'terminal' and event.get('code') == 'InvalidArgument':
                captured_terminal = True
        result['request_evidence'] = dict(admission='exact_summary_counters',
            captured_dogets=captured_dogets, uncaptured_dogets=dogets - captured_dogets if type(dogets) is int else None,
            captured_about_requests=captured_abouts,
            terminal_event_captured=captured_terminal,
            request_validation=stream.get('request_validation'))
        if type(dogets) is int and captured_dogets > dogets:
            result['errors'].append('captured DoGet count exceeds exact summary')
        correlate(root, result, stream)
        result['first_doget_ms'] = ((first_doget_ns - result['started_monotonic_ns']) / 1e6
            if type(first_doget_ns) is int and 'started_monotonic_ns' in result else None)
        result['fixture_terminal_to_exit_ms'] = ((result['ended_monotonic_ns'] - terminal_ns) / 1e6
            if type(terminal_ns) is int and 'ended_monotonic_ns' in result else None)
        replacements = {fixture.url: 'http://<FIXTURE>', str(root): '<HOME>'}
        result.update(contract=dict(stdout_sha256=result['stdout_sha256'], stderr_sha256=result['stderr_sha256'],
                                     stdout_bytes=result['stdout_bytes'], rendered_rows=result['rows'], batches=result['batches'],
                                     termination=result.get('termination'), requests=dogets,
                                     ticket_sha256=hashlib.sha256(b'{"stream":"logs"}').hexdigest(),
                                     request_validation=stream.get('request_validation'), sink=workload['sink'], tls=tls,
                                     workload_sha256=hashlib.sha256(json.dumps(dict(stream=workload['stream'],
                                         duration_s=workload['duration_s'], sink=workload['sink']),
                                         sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                                     config_before=hashlib.sha256(run.normalize(before, replacements)).hexdigest(),
                                     config_after=hashlib.sha256(run.normalize(config.read_bytes(), replacements)).hexdigest()),
                      fixture_encoded_bytes=stream.get('encoded_bytes'), fixture_decoded_bytes=stream.get('decoded_bytes'),
                      fixture_max_message_bytes=stream.get('max_message_bytes'), fixture_events_dropped=summary.get('events_dropped'),
                      fixture_sent_rows=stream.get('expected_rows'), fixture_expected_output_ns=stream.get('expected_output_ns'),
                      fixture_cpu_user_ns=summary.get('fixture_cpu_user_ns'),
                      fixture_cpu_system_ns=summary.get('fixture_cpu_system_ns'),
                      terminal_condition='expected_remote_failure_InvalidArgument', requests=dogets, tls=tls)
        for name, field in (('encoded_bytes_per_second', 'fixture_encoded_bytes'), ('decoded_bytes_per_second', 'fixture_decoded_bytes')):
            result[name] = result[field] / (result['wall_ms'] / 1000) if result.get('wall_ms') and result[field] is not None else None
        if diagnostics:
            result['kind'] = 'diagnostic_only'
            try:
                result['diagnostic_tail_resources'] = tail_resources(root / 'diagnostics')
            except (OSError, ValueError) as error:
                result['errors'].append('invalid tail diagnostics: ' + str(error))
        elif collector_mode == 'hash-framing':
            result['kind'] = 'observer_control'
        result['valid'] = not result['errors']
        result['contract_validated'] = result['valid']
        write_json(root / 'sample.json', result)
        return result
    except BaseException as error:
        if 'result' not in locals():
            raise
        result.setdefault('errors', []).append('post-capture ' + type(error).__name__ + ': ' + str(error))
        result.update(valid=False, contract_validated=False, collector_exception=True)
        write_json(root / 'sample.json', result)
        return result
    finally:
        fixture.close()


def workload_seed(seed, name):
    return int.from_bytes(hashlib.sha256(f'{seed}:{name}'.encode()).digest()[:8], 'big')


def schedule(labels, warmups, repetitions, seed, name='', tier='finite', session_index=0):
    derived = workload_seed(seed, name)
    rng = random.Random(derived)
    for warmup, rounds in ((True, warmups), (False, repetitions)):
        for round_index in range(rounds):
            order = list(labels)
            if tier == 'finite':
                rng.shuffle(order)
            elif (session_index + round_index) % 2:
                order.reverse()
            for label in order:
                yield dict(label=label, round=round_index, warmup=warmup, workload_seed=derived,
                           session_index=session_index + round_index,
                           implementation_order='AB' if order == list(labels) else 'BA')


def summarize(samples):
    result = {}
    for metric in METRICS:
        values = [s[metric] for s in samples if s.get(metric) is not None and s['valid']]
        result[metric] = dict(n=len(values), median=statistics.median(values) if values else None,
                              minimum=min(values) if values else None, maximum=max(values) if values else None,
                              stdev=statistics.stdev(values) if len(values) > 1 else None)
    result['first_record_observed_after_native_exit'] = sum(
        bool(s.get('first_record_observed_after_native_exit')) for s in samples if s.get('valid'))
    return result


def metric_scope(workload):
    scopes = {}
    if workload['name'] in ('paced', 'steady', 'idle', 'dictionary', 'reconnect'):
        scopes.update({key: 'schedule_bound' for key in ('wall_ms', *THROUGHPUT_METRICS)})
    if workload.get('sink'):
        scopes.update({key: 'sink_gated' for key in ('wall_ms', 'first_complete_record_ms',
            'fixture_to_first_complete_record_ms', *THROUGHPUT_METRICS)})
    if workload.get('resource_pressure'):
        scopes['wall_ms'] = 'schedule_bound_500ms_hold'
        scopes.update({key: 'resource_pressure' for key in THROUGHPUT_METRICS})
    return scopes


def compare_descriptive(reference, candidate, metric, tier, minimum_n, seed):
    a = [x[metric] for x in reference if x.get('valid') and x.get(metric) is not None]
    b = [x[metric] for x in candidate if x.get('valid') and x.get(metric) is not None]
    if tier == 'finite' and min(len(a), len(b)) >= minimum_n:
        result = run.compare_samples(reference, candidate, metric, seed)
        result['interval_status'] = 'exploratory_pointwise_bootstrap'
        return result
    am, bm = statistics.median(a), statistics.median(b)
    result = dict(reference_n=len(a), candidate_n=len(b), reference_median=am, candidate_median=bm,
                  median_delta=bm - am, reference_sessions=a, candidate_sessions=b,
                  reference_range=[min(a), max(a)], candidate_range=[min(b), max(b)],
                  interval_status='not_estimable', ratio_status='defined' if am else 'undefined_zero_reference')
    if am:
        result['ratio'] = bm / am
    return result


def bind_manifest(manifest, fixture_info):
    budgets = read_json(PREDECLARED_BUDGETS)
    budget_info = file_info(PREDECLARED_BUDGETS)
    if manifest.get('budgets') != budgets or manifest.get('budgets_sha256') != budget_info['sha256']:
        raise ValueError('parent manifest must bind exact predeclared budget content and SHA256')
    for key in ('sources', 'toolchains', 'build_flags', 'dependencies'):
        if not manifest.get(key):
            raise ValueError('manifest missing nonempty provenance: ' + key)
    runners = []
    for info in manifest['sources'].get('runners', {}).values():
        runner = file_info(info['path'])
        if runner['sha256'] != info['sha256']:
            raise ValueError('declared harness source identity differs: ' + info['path'])
        runners.append(runner)
    manifest_file = manifest.get('fixture_manifest')
    if not isinstance(manifest_file, dict) or not manifest_file.get('path'):
        raise ValueError('immutable fixture build manifest required')
    info = file_info(manifest_file['path'])
    if info['sha256'] != manifest_file.get('sha256'):
        raise ValueError('fixture build manifest identity differs')
    build = read_json(info['path'])
    if build != manifest.get('fixture_build') or build.get('binary', {}).get('sha256') != fixture_info['sha256']:
        raise ValueError('fixture build/source provenance differs from executable')
    if Path(fixture_info['path']).stat().st_mode & 0o222:
        raise ValueError('fixture executable must be a frozen read-only artifact')
    sources, toolchain = build.get('sources'), build.get('toolchain')
    if not isinstance(sources, dict) or not all(any(Path(k).name == name for k in sources)
        for name in ('main.go', 'phase6.go', 'go.mod', 'go.sum')):
        raise ValueError('fixture source hashes incomplete')
    if not isinstance(toolchain, dict) or 'go1.25.4' not in toolchain.get('version', ''):
        raise ValueError('fixture must record pinned Go1.25.4 toolchain')
    toolchain_info = file_info(toolchain['path'])
    if toolchain_info['sha256'] != toolchain.get('sha256'):
        raise ValueError('fixture toolchain executable hash differs')
    snapshots = []
    for key, sha in sources.items():
        path = build.get('source_paths', {}).get(key)
        snapshot = file_info(path) if path else None
        if not snapshot or snapshot['sha256'] != sha or Path(path).stat().st_mode & 0o222:
            raise ValueError('immutable fixture source snapshot differs: ' + key)
        snapshots.append(snapshot)
    stable = {key: value for key, value in manifest.items() if key != 'timing_aa'}
    return dict(budgets=budgets, budget_file=budget_info, fixture_manifest=info,
                bound_runners=runners, fixture_toolchain=toolchain_info, bound_fixture_sources=snapshots,
                fixture_build=build, stable_manifest_sha256=hashlib.sha256(
                    json.dumps(stable, sort_keys=True, separators=(',', ':')).encode()).hexdigest())


NORMAL_RELEASE_ARGV = ['cargo', 'build', '--locked', '--offline', '--release', '--bin', 'pb']


def declared_identity(value, role):
    if not isinstance(value, dict) or not value.get('path') or not value.get('sha256'):
        raise ValueError('missing explicit declared identity for ' + role)
    actual = file_info(value['path'])
    if actual['sha256'] != value['sha256']:
        raise ValueError('declared identity hash differs for ' + role)
    return actual


def bind_selected_roles(manifest, labels, meter, timing_aa):
    """Bind logical roles by content; retain copied and original executable paths."""
    declared = dict(go=declared_identity(manifest.get('baselines', {}).get('pb-go'), 'baselines.pb-go'),
                    meter=declared_identity(manifest.get('meter'), 'meter'))
    tracked = list(declared.values())
    candidate_manifest = None
    if not timing_aa:
        build = manifest.get('build_flags', {}).get('candidate', {}).get('pb')
        if (not isinstance(build, dict) or build.get('build_kind') != 'normal_release' or
            build.get('argv') != NORMAL_RELEASE_ARGV or build.get('features') != [] or
            build.get('cloud_synthetic_token_enabled') is not False):
            raise ValueError('candidate must declare exact normal_release flags, empty features and no cloud token')
        candidate_manifest = declared_identity(manifest.get('candidate_manifest'), 'candidate_manifest')
        if read_json(candidate_manifest['path']).get('builds', {}).get('pb') != build:
            raise ValueError('candidate_manifest builds.pb differs from selected normal release declaration')
        declared['candidate'] = declared_identity(build, 'build_flags.candidate.pb')
        tracked += [candidate_manifest, declared['candidate']]
    selected = dict(labels, meter=meter)
    roles = {label: 'go' if timing_aa else label for label in labels}
    roles['meter'] = 'meter'
    binding = {}
    for label, role in roles.items():
        if selected[label]['sha256'] != declared[role]['sha256']:
            raise ValueError('selected executable differs from declared measured role: ' + label)
        binding[label] = dict(role=role, selected=selected[label], declared=declared[role],
                              content_identical_copy=selected[label]['path'] != declared[role]['path'])
    return binding, tracked


def deduplicate_identities(identities):
    result = {}
    for info in identities:
        path = str(Path(info['path']).resolve())
        if path in result and result[path]['sha256'] != info['sha256']:
            raise ValueError('conflicting bound identities: ' + path)
        result[path] = dict(info, path=path)
    return list(result.values())


def comparable_contracts(groups):
    """Comparable delivered work, not timestamps or resource measurements."""
    samples = [sample for group in groups.values() for sample in group]
    reasons = []
    if not samples or any(not sample.get('valid') for sample in samples) or any(not group for group in groups.values()):
        reasons.append('invalid or missing measured samples')
    reference = next((sample.get('contract') for sample in samples if sample.get('valid')), None)
    differing, missing = set(), set()
    for sample in samples:
        contract = sample.get('contract', {})
        for key in COMPARABLE_FIELDS:
            if not isinstance(reference, dict) or key not in reference or key not in contract:
                missing.add(key)
            elif contract[key] != reference[key]:
                differing.add(key)
    if missing:
        reasons.append('missing comparable contract fields: ' + ', '.join(sorted(missing)))
    if differing:
        reasons.append('delivered workload contract differs: ' + ', '.join(sorted(differing)))
    return dict(comparable=not reasons, reasons=reasons, differing_fields=sorted(differing),
                checked_fields=list(COMPARABLE_FIELDS), samples=len(samples))


class WindowBudget:
    """Do not start a sample unless its full deadline plus cleanup fits."""
    def __init__(self, seconds):
        if seconds <= 0:
            raise ValueError('quiet window budget must be positive')
        self.deadline = clock_ns() + int(seconds * 1e9)

    def admission(self, workload):
        remaining = (self.deadline - clock_ns()) / 1e9
        required = workload['timeout_s'] + WINDOW_CLEANUP_RESERVE_S
        return dict(fits=remaining >= required, remaining_s=max(0, remaining),
                    required_s=required, cleanup_reserve_s=WINDOW_CLEANUP_RESERVE_S)


@contextlib.contextmanager
def quiet_lock(path, token):
    """Coordinator supplies a nonempty handshake token; flock prevents campaigns colliding."""
    if not token:
        raise ValueError('campaign requires explicit coordinator --quiet-window-token')
    with safe_path(path).open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def identity_key(report):
    if type(report.get('tls')) is not bool:
        raise ValueError('aggregate requires an explicit boolean TLS condition in every report')
    def canonical_sha(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(binaries={k: v['sha256'] for k, v in report['binaries'].items()},
        **{k: report[k]['sha256'] for k in ('fixture', 'meter', 'collector', 'process_harness', 'budget_file', 'fixture_manifest')},
        stable_manifest_sha256=report['stable_manifest_sha256'], collector_mode=report['collector_mode'],
        fixture_build_sha256=canonical_sha(report['fixture_build']), budgets_content_sha256=canonical_sha(report['budgets']),
        parent_content_sha256=canonical_sha({k: v for k, v in report['manifest'].items() if k != 'timing_aa'}),
        kind=report['kind'], tier=report['tier'], tls=report['tls'],
        duration_s=report['duration_s'], workloads=report['workloads'])


def summarize_campaign(results, report, seed):
    summaries = {}
    labels = list(report['binaries'])
    minimum = report['budgets']['campaign_controls']['intervals']['finite_minimum_samples_per_label']
    for workload in report['workloads']:
        name = workload['name']
        if name not in results:
            continue
        groups = results[name]
        row = {label: summarize(samples) for label, samples in groups.items()}
        contract = comparable_contracts(groups)
        reasons = list(contract['reasons'])
        if not report['complete']:
            reasons.append('partition incomplete')
        if not report['passed']:
            reasons.append('partition contains invalid samples/warmups or changed identities')
        samples = [s for group in groups.values() for s in group]
        if any(not s.get('cpu_screen', {}).get('comparative_admissible', False) for s in samples):
            reasons.append('host CPU admission unavailable, ambiguous, flagged or resolution_limited')
        scopes = metric_scope(workload)
        ratios, per_metric = {}, {}
        for metric in METRICS:
            exclusions = list(reasons)
            if metric in scopes:
                exclusions.append(scopes[metric] + ': absolute observations only')
            if metric in THROUGHPUT_METRICS and any(
                not s.get('cpu_screen', {}).get('throughput_admissible', False) or
                not s.get('production', {}).get('throughput_admissible', False) for s in samples):
                exclusions.append('fixture/observer ceiling or producer schedule prevents throughput attribution')
            if not all(any(s.get(metric) is not None for s in group) for group in groups.values()):
                exclusions.append('metric unavailable')
            ratios[metric] = None if exclusions else compare_descriptive(
                groups[labels[0]], groups[labels[1]], metric, report['tier'], minimum, workload_seed(seed, name))
            per_metric[metric] = exclusions
        row.update(contract_comparison=contract, candidate_over_go=ratios,
                   ratio_quarantine=reasons, metric_quarantine=per_metric, metric_scope=scopes)
        summaries[name] = row
    return summaries


def campaign(args):
    if safe_path(args.lock) != PERFORMANCE_LOCK:
        raise ValueError(f'campaign must use the shared {PERFORMANCE_LOCK}')
    out = safe_path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    TMP.mkdir(parents=True, exist_ok=True)
    report = dict(schema_version=SCHEMA_VERSION, passed=False, complete=False, summaries={},
                  failure=None, quiet_window_token_sha256=hashlib.sha256(args.quiet_window_token.encode()).hexdigest())
    results, tracked = {}, []
    try:
        if type(args.tls) is not bool:
            raise ValueError('campaign requires an explicit boolean TLS condition')
        manifest, parent_identity = read_json_with_identity(args.manifest)
        timing_aa = getattr(args, 'timing_aa', False)
        go = run.binary_info(args.go)
        if timing_aa:
            if args.tier != 'finite' or args.warmups != 5 or args.repetitions != 30:
                raise ValueError('timing A/A requires finite 5 warmups and 30 measured samples per Go label')
            labels = {'go-a': go, 'go-b': go}
        else:
            labels = dict(go=go, candidate=run.binary_info(args.candidate))
        fixture = run.binary_info(args.fixture)
        bound = bind_manifest(manifest, fixture)
        meter = run.binary_info(args.meter)
        role_binding, role_identities = bind_selected_roles(manifest, labels, meter, timing_aa)
        ws = workloads(args.duration_s, args.tier, args.seed)
        if args.workload:
            unknown = set(args.workload) - {w['name'] for w in ws}
            if unknown:
                raise ValueError('unknown workloads: ' + ', '.join(sorted(unknown)))
            ws = [w for w in ws if w['name'] in args.workload]
        kind = 'timing_aa' if timing_aa else ('native_unprofiled' if args.collector_mode == 'full' else 'observer_control')
        report.update(kind=kind, collector_mode=args.collector_mode, host=run.host_metadata(),
            binaries=labels, fixture=fixture, meter=meter, tls=args.tls, measured_roles=role_binding,
            collector=file_info(__file__), process_harness=file_info(run.__file__),
            fixture_protocol='phase6-r5', manifest=manifest, parent_manifest=parent_identity, **bound,
            seed=args.seed, warmups=args.warmups, repetitions=args.repetitions, tier=args.tier,
            session_index=getattr(args, 'session_index', 0), duration_s=args.duration_s, workloads=ws, not_measured=NA,
            window_budget_s=args.window_budget_s, window_cleanup_reserve_s=WINDOW_CLEANUP_RESERVE_S,
            excluded_costs=dict(build_elapsed_ms=manifest.get('build_elapsed_ms'), dependency_size_bytes=manifest.get('dependency_size_bytes')),
            notes=['expected_output_ns is fixture encoding wall time; never subtracted from client CPU',
                   'contract A/A is reproducibility, separate timing A/A is 5 warmups and 30 per same-Go label',
                   'CPU windows differ; unavailable/ambiguous/short observations do not prove quiescence',
                   'duration sessions and finite n<20 are descriptive; no bootstrap intervals',
                   'all ratios conservatively quarantined on partition failure'])
        tracked = list(labels.values()) + [report[k] for k in ('fixture', 'meter', 'collector', 'process_harness',
                    'budget_file', 'fixture_manifest', 'parent_manifest')]
        tracked += bound['bound_fixture_sources']
        tracked += bound['bound_runners'] + [bound['fixture_toolchain']] + role_identities
        tracked = deduplicate_identities(tracked)
        report['bound_end_identities'] = tracked
        write_json(out / 'manifest.json', report)
        control = workloads(seed=args.seed)[0]
        whole_required = args.aa_repetitions * (control['timeout_s'] + WINDOW_CLEANUP_RESERVE_S) + sum(
            2 * (args.warmups + args.repetitions) * (w['timeout_s'] + WINDOW_CLEANUP_RESERVE_S) for w in ws)
        report['partition_budget'] = dict(required_s=whole_required, reserved_s=args.window_budget_s,
                                         scope='full worst-case deadlines plus cleanup for each sample and contract A/A')
        if args.tier != 'finite' and whole_required > args.window_budget_s:
            report['window_stop'] = dict(required_s=whole_required, remaining_s=args.window_budget_s, fits=False)
            raise ValueError('whole duration partition cannot fit reserved quiet window; no lock or child started')
        def sample(binary, workload, folder):
            try:
                return run_sample(binary, workload, fixture['path'], meter['path'], folder, args.tls,
                                  collector_mode=args.collector_mode)
            except BaseException as error:
                # TypeError and interrupts also leave an atomic invalid record. run_sample owns cleanup.
                value = {}
                for name in ('sample.json', 'capture.json'):
                    try:
                        if (folder / name).exists():
                            value = read_json(folder / name)
                            break
                    except (OSError, ValueError):
                        continue
                value.setdefault('errors', []).append(type(error).__name__ + ': ' + str(error))
                value.update(valid=False, contract_validated=False, collector_exception=True)
                folder.mkdir(parents=True, exist_ok=True)
                write_json(folder / 'sample.json', value)
                return value
        with quiet_lock(args.lock, args.quiet_window_token):
            budget = WindowBudget(args.window_budget_s)
            aa = []
            for index in range(args.aa_repetitions):
                admission = budget.admission(control)
                if not admission['fits']:
                    report['window_stop'] = admission
                    raise ValueError('quiet window cannot fit the next full contract A/A control')
                aa.append(sample(next(iter(labels.values()))['path'], control, out / 'go-aa' / str(index)))
                write_json(out / 'go-aa.json', dict(kind='contract_reproducibility', samples=aa,
                    passed=False, complete=False))
                if aa[-1].get('collector_exception'):
                    raise RuntimeError('contract A/A collector exception')
            stable = comparable_contracts({'a': aa[:1], 'b': aa[1:]})['comparable']
            write_json(out / 'go-aa.json', dict(kind='contract_reproducibility', samples=aa, passed=stable, complete=True))
            if not stable:
                raise ValueError('Go contract A/A control failed')
            report.update(complete=True, passed=True)
            with (out / 'samples.jsonl').open('x') as stream:
                for workload in ws:
                    results[workload['name']] = {label: [] for label in labels}
                    for item in schedule(labels, args.warmups, args.repetitions, args.seed, workload['name'],
                                         args.tier, report['session_index']):
                        folder = out / workload['name'] / f"{'warmup' if item['warmup'] else 'sample'}-{item['round']:03d}-{item['label']}"
                        admission = budget.admission(workload)
                        if not admission['fits']:
                            report.update(complete=False, passed=False, window_stop=dict(workload=workload['name'], next_sample=item, **admission))
                            break
                        value = sample(labels[item['label']]['path'], workload, folder)
                        value.update(workload=workload['name'], **item)
                        stream.write(json.dumps(value, allow_nan=False) + '\n')
                        stream.flush()
                        report['passed'] = report['passed'] and value['valid']
                        if not item['warmup']:
                            results[workload['name']][item['label']].append(value)
                        if value.get('collector_exception'):
                            raise RuntimeError('measured/warmup collector exception')
                    if not report['complete']:
                        break
            changed = [info['path'] for info in tracked if file_info(info['path'])['sha256'] != info['sha256']]
            report['end_identity_check'] = dict(passed=not changed, changed=changed)
            if changed:
                raise ValueError('campaign identities changed during quiet window')
    except BaseException as error:
        report.update(passed=False, complete=False, failure=type(error).__name__ + ': ' + str(error))
    finally:
        # Always preserve complete absolute observations; never award a partial partition a ratio.
        try:
            if tracked:
                changed = [info['path'] for info in tracked if file_info(info['path'])['sha256'] != info['sha256']]
                report['end_identity_check'] = dict(passed=not changed, changed=changed)
                if changed:
                    report.update(passed=False, complete=False, failure='campaign identities changed')
            if 'workloads' in report:
                report.update(summaries=summarize_campaign(results, report, args.seed),
                    unstarted_workloads=[w['name'] for w in report['workloads'] if w['name'] not in results],
                    expected_measured_samples=len(report['workloads']) * 2 * args.repetitions,
                    recorded_measured_samples=sum(len(g) for groups in results.values() for g in groups.values()))
                report['comparison_contracts_passed'] = report['complete'] and report['passed'] and bool(report['summaries']) and all(
                    s['contract_comparison']['comparable'] for s in report['summaries'].values())
        except BaseException as error:
            report.update(passed=False, complete=False, summaries={}, comparison_contracts_passed=False,
                          failure='finalization ' + type(error).__name__ + ': ' + str(error))
        write_json(out / 'report.json', report)
    return 0 if report['passed'] and report['complete'] else 1


def aggregate_reports(paths, out):
    reports = [read_json(path) for path in paths]
    if not reports:
        raise ValueError('at least one session report required')
    first = reports[0]
    if first['tier'] == 'finite':
        raise ValueError('offline aggregation is for independently recorded duration sessions')
    if any(r['repetitions'] != 1 for r in reports):
        raise ValueError('offline duration aggregation requires one session per label per report')
    identity = identity_key(first)
    if any(identity_key(r) != identity for r in reports):
        raise ValueError('session binary/fixture/meter/collector/shared harness/manifest/budget/workload identities differ')
    if len({r['session_index'] for r in reports}) != len(reports):
        raise ValueError('duplicate session index; repeated reports are not independent sessions')
    output = dict(schema_version=SCHEMA_VERSION, kind='duration_session_aggregate', identity=identity,
                  tls=first['tls'],
                  reports=[file_info(p) for p in paths], session_indices=[r['session_index'] for r in reports],
                  descriptive_only=True, confidence_intervals=None, summaries={})
    for workload in first['workloads']:
        name = workload['name']
        row = {}
        for label in first['binaries']:
            row[label] = {}
            for metric in METRICS:
                values = [r.get('summaries', {}).get(name, {}).get(label, {}).get(metric, {}).get('median') for r in reports]
                valid = [v for v in values if v is not None]
                row[label][metric] = dict(session_values=values, n=len(valid),
                    range=[min(valid), max(valid)] if valid else None,
                    median=statistics.median(valid) if valid else None, interval_status='not_estimable')
        row['contracts_comparable'] = all(r.get('comparison_contracts_passed', False) for r in reports)
        row['metric_comparison_admission'] = {metric: all(
            r.get('summaries', {}).get(name, {}).get('candidate_over_go', {}).get(metric) is not None
            for r in reports) for metric in METRICS}
        output['summaries'][name] = row
    output['passed'] = all(r.get('passed') and r.get('complete') for r in reports)
    write_json(safe_path(out), output)
    return 0 if output['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('list')
    listing.add_argument('--tier', choices=('finite', 'functional', 'soak'), default='finite')
    listing.add_argument('--duration-s', type=float, default=1800)
    aggregation = sub.add_parser('aggregate')
    aggregation.add_argument('--report', type=Path, action='append', required=True)
    aggregation.add_argument('--out', type=Path, required=True)
    running = sub.add_parser('run')
    for key in ('go', 'candidate', 'fixture', 'meter', 'manifest', 'out'):
        running.add_argument('--' + key, type=Path, required=True)
    running.add_argument('--tier', choices=('finite', 'functional', 'soak'), default='finite')
    running.add_argument('--duration-s', type=float, default=1800)
    running.add_argument('--workload', action='append')
    running.add_argument('--warmups', type=int)
    running.add_argument('--repetitions', type=int)
    running.add_argument('--aa-repetitions', type=int, default=2)
    running.add_argument('--seed', type=int, default=2026)
    running.add_argument('--session-index', type=int, default=0)
    running.add_argument('--timing-aa', action='store_true', help='separate same-Go labels; finite 5 warmups/30 samples per label')
    running.add_argument('--tls', action='store_true')
    running.add_argument('--collector-mode', choices=COLLECTOR_MODES, default='full',
                         help='hash-framing is a separate observer-overhead control; full stdout hash remains mandatory')
    running.add_argument('--quiet-window-token', required=True)
    running.add_argument('--window-budget-s', type=float, required=True,
                         help='coordinator-reserved quiet duration; next full sample deadline plus cleanup must fit')
    running.add_argument('--lock', type=Path, default=PERFORMANCE_LOCK)
    args = parser.parse_args()
    if args.command == 'aggregate':
        return aggregate_reports(args.report, args.out)
    if args.command == 'list':
        print(json.dumps(workloads(args.duration_s, args.tier), indent=2))
        return 0
    args.warmups = args.warmups if args.warmups is not None else (5 if args.tier == 'finite' else 0)
    args.repetitions = args.repetitions if args.repetitions is not None else (30 if args.tier == 'finite' else 1)
    if args.warmups < 0 or args.repetitions < 1 or args.aa_repetitions < 2:
        parser.error('warmups must be >=0 and repetitions >=1')
    if args.tier == 'soak' and args.duration_s < 1800:
        parser.error('soak requires 1800–3600 seconds; use functional for short checks')
    return campaign(args)


if __name__ == '__main__':
    sys.exit(main())
