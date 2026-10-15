#!/usr/bin/env python3
"""Bounded OFFLINE supplemental checks for Phase6 duration evidence, never admission.

The process guard is installed before analysis imports. This standalone module
deliberately imports neither the collector nor the runtime harness.
"""
import sys

sys.dont_write_bytecode = True
_DENY = ('subprocess.', 'os.exec', 'os.spawn', 'os.fork', 'os.posix_spawn',
         'os.system', 'os.kill', 'fcntl.flock', 'fcntl.lockf', 'pty.spawn',
         'ctypes.', 'socket.')
_UNSAFE = ('subprocess', 'multiprocessing', 'socket', 'ctypes', 'pty')


def _execution_guard(event, args):
    if event.startswith(_DENY) or (event == 'import' and args[0].split('.')[0] in _UNSAFE):
        raise RuntimeError('offline duration guard: ' + event)


sys.addaudithook(_execution_guard)
import os


def _blocked(*args, **kwargs):
    raise RuntimeError('offline duration guard: process/lock entry point')


for _name in dir(os):
    if _name.startswith(('exec', 'spawn')) or _name in (
            'fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'system', 'kill', 'killpg', 'popen'):
        setattr(os, _name, _blocked)
import fcntl

fcntl.flock = fcntl.lockf = _blocked
SCRATCH = os.path.expanduser('~/.cache/pb-phase6/duration-gate-v1')
_outputs = set()


def _owned(path):
    return path == SCRATCH or path.startswith(SCRATCH + '/') or path in _outputs


def _io_guard(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = os.path.realpath(os.fsdecode(args[0]))
        if any(path == p or path.startswith(p + '/') for p in ('/proc', '/sys', '/tmp')):
            raise RuntimeError('offline duration guard: forbidden read/write path ' + path)
        mode, flags = args[1:3]
        if ((mode and any(c in mode for c in 'wax+')) or
                (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))):
            if not _owned(path):
                raise RuntimeError('offline duration guard: unowned write ' + path)
    if event == 'os.mkdir' and not _owned(os.path.realpath(os.fsdecode(args[0]))):
        raise RuntimeError('offline duration guard: unowned directory')
    if event in ('os.remove', 'os.rmdir', 'os.rename', 'os.truncate'):
        raise RuntimeError('offline duration guard: destructive mutation')
    if event in ('os.link', 'os.symlink'):
        # Only synthetic alias probes in the owned scratch may create links.
        if not _owned(os.path.realpath(os.fsdecode(args[1]))):
            raise RuntimeError('offline duration guard: unowned link')


sys.addaudithook(_io_guard)
for _name in ('TMPDIR', 'TMP', 'TEMP'):
    os.environ[_name] = SCRATCH

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import stat

FILE_BYTES = 4 << 20
JOURNAL_BYTES = 8 << 20
TOTAL_BYTES = 64 << 20
MAX_FILES = 512
MAX_SAMPLES = 64
MAX_OPS = 256
MAX_COUNT = (1 << 63) - 1
MAX_DEPTH = 48
MAX_NODES = 200000
FROZEN_COLLECTOR = 'e2e83fa118c58a96ff55700be88f95f9b8b6479a289a8c59f447f836389fde51'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def integer(value, name, maximum=MAX_COUNT):
    require(type(value) is int and 0 <= value <= maximum, 'invalid nonnegative integer: ' + name)
    return value


def same(a, b):
    """JSON equality that cannot equate true with 1 (including nested fields)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def strict_json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, 'duplicate JSON key: ' + key)
            out[key] = value
        return out

    def bad(value):
        raise ValueError('nonfinite JSON: ' + value)

    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=bad)
        nodes = 0

        def walk(item, depth):
            nonlocal nodes
            nodes += 1
            require(depth <= MAX_DEPTH and nodes <= MAX_NODES, 'JSON depth/node bound')
            if isinstance(item, dict):
                for child in item.values():
                    walk(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    walk(child, depth + 1)
            elif type(item) in (int, float):
                require(math.isfinite(item) and abs(item) <= 1e100, 'JSON number bound')
        walk(value, 0)
        return value
    except (RecursionError, OverflowError, UnicodeError) as error:
        raise ValueError('malformed/bounded JSON: ' + str(error)) from error


def safe_path(path, exists=True):
    path = Path(path)
    require('..' not in path.parts, 'path traversal refused')
    path = path.absolute()
    for part in reversed((path, *path.parents)):
        try:
            require(not part.is_symlink(), 'path symlink alias refused: ' + str(part))
        except OSError as error:
            raise ValueError('unsafe path: ' + str(error)) from error
    if exists:
        require(path.exists(), 'missing input: ' + str(path))
    return path


class Inputs:
    """Small bounded I/O style adapted from phase6_resource_summary.read_bound.

    Parse and hash the SAME read; never reopen a file for its digest. No following
    symlinks, special files, hard-link aliases, or paths outside this campaign.
    """
    def __init__(self, root):
        self.root = safe_path(root)
        self.receipts = {}
        self.buffers = {}
        self.inodes = set()
        self.total = 0

    def read(self, relative, limit=FILE_BYTES):
        path = safe_path(self.root / relative)
        require(path.is_relative_to(self.root), 'input path escape')
        key = str(path.relative_to(self.root))
        if key in self.buffers:
            return self.buffers[key]
        require(len(self.buffers) < MAX_FILES, 'input file count bound')
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'input special file/hardlink alias')
            require((before.st_dev, before.st_ino) not in self.inodes, 'input inode alias')
            require(before.st_size <= limit and self.total + before.st_size <= TOTAL_BYTES, 'input byte bound')
            data = stream.read(min(limit, TOTAL_BYTES - self.total) + 1)
            after = os.fstat(stream.fileno())
        require(len(data) == before.st_size and len(data) <= limit and
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'input changed during read')
        self.inodes.add((before.st_dev, before.st_ino))
        self.total += len(data)
        self.buffers[key] = data
        self.receipts[key] = dict(path=str(path), size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        return data

    def obj(self, relative):
        value = strict_json(self.read(relative))
        require(isinstance(value, dict), 'input must be JSON object: ' + str(relative))
        return value


def schedule(stream):
    """Mirror saved fixture liveSchedule and max(repeat/count, 1); no runtime import.

    Mixed live/finite batch operations are outside this narrow duration schema:
    emitted live batches must equal the stream's entire captured batch count.
    """
    require(isinstance(stream, dict), 'scenario stream must be an object')
    subscriptions = stream.get('subscriptions')
    require(isinstance(subscriptions, list) and 0 < len(subscriptions) <= MAX_OPS, 'subscription bound')
    live_ops = live_total = finite_total = operations = 0
    for sub in subscriptions:
        require(isinstance(sub, dict), 'subscription must be an object')
        repeat = max(integer(sub.get('repeat', 0), 'repeat', 10000), 1)
        ops = sub.get('ops')
        require(isinstance(ops, list) and bool(ops), 'missing operations')
        for op in ops:
            operations += 1
            require(operations <= MAX_OPS and isinstance(op, dict), 'operation bound/type')
            kind = op.get('op')
            require(kind in ('live', 'batch', 'schema', 'sleep', 'eof', 'status'), 'unsupported duration operation')
            if kind == 'live':
                duration = integer(op.get('duration_ms', 0), 'duration_ms')
                interval = integer(op.get('interval_ms', 0), 'interval_ms')
                count = integer(op.get('batches', 0), 'live batches')
                require(duration > 0 or count > 0, 'empty live schedule')
                require(not duration or interval > 0, 'unbounded live schedule')
                by_time = (duration + interval - 1) // interval if duration else count
                live_total += (min(count, by_time) if count else by_time) * repeat
                live_ops += repeat
            if kind == 'batch':
                finite_total += max(integer(op.get('count', 0), 'batch count'), 1) * repeat
    require(live_total <= MAX_COUNT and finite_total <= MAX_COUNT, 'schedule count bound')
    require(not (live_ops and finite_total), 'mixed live/finite batches outside supplemental schema')
    return dict(live_ops=live_ops, scheduled_batches=live_total, finite_batches=finite_total)


def check_sample(sample, journal, meter, scenario, final, stderr, fixture_stderr):
    """Two supplemental predicates, with the exact fields retained for review."""
    require(all(isinstance(x, dict) for x in (sample, journal, meter, scenario, final)), 'evidence object type')
    # sample.json precedes the extra journal scheduling fields in the frozen schema.
    require(all(k in journal and same(v, journal[k]) for k, v in sample.items()), 'sample/journal binding differs')
    for source_name, source in (('sample', sample), ('journal', journal)):
        for field in ('valid', 'capture_complete', 'contract_validated'):
            require(source.get(field) is True, source_name + ' incomplete/invalid: ' + field)
        require(source.get('timed_out') is False and same(source.get('errors'), []), source_name + ' timeout/errors')
        require(source.get('terminal_condition') == 'expected_remote_failure_InvalidArgument', 'terminal condition differs')
        require(integer(source.get('exit_code'), source_name + ' exit_code') == 1, 'expected fatal exit 1')
        require(same(source.get('termination'), {'exit': 1}), source_name + ' termination differs')
        require(isinstance(source.get('contract'), dict) and
                same(source['contract'].get('termination'), {'exit': 1}), 'contract termination differs')
        for field in ('exit_code', 'termination'):
            require(field in meter and same(source[field], meter[field]), 'meter binding differs: ' + field)
    require(integer(meter.get('exit_code'), 'meter exit_code') == 1 and
            same(meter.get('termination'), {'exit': 1}), 'native meter fatal exit differs')
    require(meter.get('resource_source') == 'native_child_wait4', 'missing native wait4 meter provenance')
    phase = scenario.get('phase6', {})
    require(isinstance(phase, dict) and phase.get('expected_output') is True and
            isinstance(phase.get('streams'), dict) and set(phase['streams']) == {'logs'}, 'scenario stream/oracle schema')
    require(isinstance(final.get('streams'), dict) and set(final['streams']) == {'logs'} and
            isinstance(final['streams']['logs'], dict), 'final stream schema')
    stream = final['streams']['logs']
    expected = schedule(phase['streams']['logs'])
    kinds = final.get('event_kinds')
    require(isinstance(kinds, dict), 'missing fixture event counters')
    for kind, value in kinds.items():
        integer(value, 'event_kinds.' + kind)
    for kind in ('fixture_panic', 'batch_emit_failed'):
        require(kinds.get(kind, 0) == 0, 'fixture failure: ' + kind)
    require(fixture_stderr == b'', 'fixture stderr/panic evidence')
    terminal = stream.get('last_terminal', {})
    require(isinstance(terminal, dict), 'fixture terminal must be an object')
    require((terminal.get('reason'), terminal.get('code'), terminal.get('message')) ==
            ('status', 'InvalidArgument', 'fixture stream complete'), 'exact fixture terminal differs')
    expected_error = {'error': {'code': 'COMMAND_FAILED',
        'message': 'rpc error: code = InvalidArgument desc = fixture stream complete', 'retryable': False}}
    require(same(strict_json(stderr), expected_error), 'exact fatal stderr differs')
    require(len(stderr) == integer(sample.get('stderr_bytes'), 'stderr_bytes') and
            hashlib.sha256(stderr).hexdigest() == sample.get('stderr_sha256'), 'fatal stderr capture binding')
    batches = integer(stream.get('batches'), 'raw stream batches')
    require(integer(sample.get('batches'), 'captured batches') == batches, 'captured/stream batch mismatch')
    production = sample.get('production')
    require(isinstance(production, dict) and same(production.get('errors'), []), 'missing/failed production projection')
    require(integer(production.get('scheduled_batches'), 'production scheduled') == expected['scheduled_batches'], 'production schedule mismatch')
    require(integer(production.get('delivered_batches'), 'production delivered') == batches, 'production delivered mismatch')
    raw_live = stream.get('live')
    if expected['live_ops']:
        require(isinstance(raw_live, dict), 'missing raw final live summary')
        fields = ('scheduled_batches', 'emitted_batches', 'omitted_by_deadline', 'omitted_by_termination')
        counts = {k: integer(raw_live.get(k), 'raw live.' + k) for k in fields}
        require(integer(raw_live.get('ops'), 'live ops') == expected['live_ops'], 'live operation count mismatch')
        require(counts['scheduled_batches'] == expected['scheduled_batches'], 'saved scenario schedule mismatch')
        require(counts['emitted_batches'] + counts['omitted_by_deadline'] + counts['omitted_by_termination'] ==
                counts['scheduled_batches'], 'raw live conservation mismatch')
        require(counts['emitted_batches'] == batches, 'raw emitted/stream/capture mismatch')
        require(same(production.get('live'), raw_live), 'copied production/live projection differs from raw final')
    else:
        require(raw_live is None and production.get('live') is None, 'unexpected live evidence')
        require(batches == expected['finite_batches'], 'finite/control batch count mismatch')
    return dict(schedule=expected, raw_live=raw_live, stream_batches=batches,
        captured_batches=sample['batches'], production=production,
        sample_status={k: sample[k] for k in ('exit_code', 'termination', 'valid', 'capture_complete',
                                            'contract_validated', 'timed_out', 'errors', 'terminal_condition')},
        journal_exit={k: journal[k] for k in ('exit_code', 'termination')},
        meter_exit={k: meter[k] for k in ('exit_code', 'termination', 'resource_source')},
        fixture_terminal=terminal, fixture_failure_counters={k: kinds.get(k, 0) for k in ('fixture_panic', 'batch_emit_failed')},
        fatal_stderr=expected_error)


def component(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9-]{0,63}', value), 'unsafe path component')
    return value


def evaluate(report_path):
    report_path = safe_path(report_path)
    require(report_path.name == 'report.json', 'expected campaign report.json')
    inputs = Inputs(report_path.parent)
    report = inputs.obj('report.json')
    require(type(report.get('schema_version')) is int and report['schema_version'] == 2, 'unsupported report schema')
    require(report.get('tier') == 'functional' and report.get('collector_mode') in ('full', 'hash-framing'), 'unsupported duration report')
    require(report.get('complete') is True and report.get('passed') is True, 'existing completed acceptance required')
    require(isinstance(report.get('collector'), dict) and
            report['collector'].get('sha256') == FROZEN_COLLECTOR, 'frozen collector binding differs')
    workloads = report.get('workloads')
    require(isinstance(workloads, list) and 0 < len(workloads) <= 8, 'workload bound')
    by_name = {component(w['name']): w for w in workloads}
    require(len(by_name) == len(workloads), 'duplicate workloads')
    journal_data = inputs.read('samples.jsonl', JOURNAL_BYTES)
    require(journal_data.endswith(b'\n'), 'incomplete journal')
    lines = journal_data.splitlines()
    require(0 < len(lines) <= MAX_SAMPLES and all(0 < len(x) <= (1 << 20) for x in lines), 'journal line bound')
    rows = [strict_json(line) for line in lines]
    labels = report.get('binaries', {})
    require(set(labels) == {'go', 'candidate'}, 'unsupported duration roles')
    repetitions = integer(report.get('repetitions'), 'repetitions', 16)
    require(repetitions > 0 and type(report.get('warmups')) is int and report['warmups'] == 0, 'unsupported warmups/repetitions')
    require(integer(report.get('expected_measured_samples'), 'expected samples', MAX_SAMPLES) == len(rows) ==
            integer(report.get('recorded_measured_samples'), 'recorded samples', MAX_SAMPLES) == len(workloads) * 2 * repetitions,
            'report/journal sample coverage mismatch')
    aa = inputs.obj('go-aa.json')
    require(aa.get('complete') is True and aa.get('passed') is True and
            isinstance(aa.get('samples'), list) and len(aa['samples']) == 2, 'two accepted controls required')
    restrictions = {name: {k: report['summaries'][name][k] for k in
        ('ratio_quarantine', 'metric_quarantine', 'metric_scope', 'contract_comparison')} for name in by_name}
    results = []
    seen = set()

    def one(folder, row, control=False):
        result = dict(sample=str(folder), control=control, passed=False, errors=[], evidence=None,
                      observed={},
                      input_keys=[])
        try:
            require(isinstance(row, dict), 'journal/control record must be an object')
            result['observed']['journal'] = {k: row.get(k) for k in
                ('exit_code', 'termination', 'batches', 'production', 'valid', 'errors', 'timed_out')}
            sample = inputs.obj(folder / 'sample.json')
            result['observed']['sample'] = {k: sample.get(k) for k in
                ('exit_code', 'termination', 'batches', 'production', 'valid', 'capture_complete',
                 'contract_validated', 'errors', 'timed_out', 'terminal_condition')}
            meter = inputs.obj(folder / 'meter.json')
            result['observed']['meter'] = {k: meter.get(k) for k in ('exit_code', 'termination', 'resource_source')}
            scenario = inputs.obj(folder / 'scenario.json')
            phase = scenario.get('phase6')
            require(isinstance(phase, dict) and isinstance(phase.get('streams'), dict), 'scenario nested object types')
            result['observed']['scenario_stream'] = phase['streams'].get('logs')
            final = inputs.obj(folder / 'fixture-final-summary.json')
            require(isinstance(final.get('streams'), dict) and
                    isinstance(final['streams'].get('logs'), dict), 'raw final nested object types')
            raw = final['streams']['logs']
            result['observed']['raw_final'] = {k: raw.get(k) for k in ('live', 'batches', 'last_terminal')}
            result['observed']['fixture_event_kinds'] = final.get('event_kinds')
            stderr = inputs.read(folder / 'stderr.prefix.bin', 8192)
            fixture_stderr = inputs.read(folder / 'fixture.stderr', 8192)
            if not control:
                w = by_name[row['workload']]
                wanted = dict(w['stream'], expected_ticket='{"stream":"logs"}')
                require(same(scenario.get('phase6', {}).get('streams', {}).get('logs'), wanted), 'report/saved scenario differs')
                if w['name'] == 'slow-sink':
                    require(sample.get('production', {}).get('throughput_admissible') is False, 'slow-sink absolute-only restriction missing')
            result['evidence'] = check_sample(sample, row, meter, scenario, final, stderr, fixture_stderr)
            result['passed'] = True
        except (ValueError, OSError, KeyError, TypeError) as error:
            result['errors'].append(str(error))
        result['input_keys'] = sorted(k for k in inputs.receipts if k.startswith(str(folder) + '/'))
        results.append(result)

    for row in rows:
        require(isinstance(row, dict), 'journal record type')
        name, label = component(row.get('workload')), component(row.get('label'))
        index = integer(row.get('round'), 'round', 15)
        require(name in by_name and label in labels and index < repetitions and row.get('warmup') is False, 'journal cohort mismatch')
        identity = (name, label, index)
        require(identity not in seen, 'duplicate journal observation')
        seen.add(identity)
        one(Path(name) / f'sample-{index:03d}-{label}', row)
    for i, row in enumerate(aa['samples']):
        one(Path('go-aa') / str(i), row, True)
    return dict(schema_version=1, kind='offline_supplemental_duration_gate',
        supplemental_passed=all(r['passed'] for r in results), whole_phase_acceptance='not_assessed',
        comparative_admission='withheld', source_restrictions=restrictions,
        results=results, inputs=inputs.receipts, total_input_bytes=inputs.total,
        limits=dict(file_bytes=FILE_BYTES, journal_bytes=JOURNAL_BYTES, total_bytes=TOTAL_BYTES,
                    files=MAX_FILES, samples=MAX_SAMPLES, operations=MAX_OPS),
        limitations=['Supplemental conservation and meter-exit checks only; existing acceptance remains required.',
            'No output/auth oracle, full existing validation, historical authentication or native rerun.',
            'Hashes identify only the inspected saved bytes; no binary or parent revalidation.',
            'No cross-sample equality, comparative statistics, sink admission or whole-phase acceptance.'])


def output_path(report_path, out):
    root = safe_path(report_path).parent
    dest = safe_path(out, exists=False)
    require(dest != root and not dest.is_relative_to(root) and not root.is_relative_to(dest), 'output must be disjoint from campaign')
    require(not dest.exists() and not dest.is_symlink(), 'output exists/alias: no overwrite')
    require(dest.parent.is_dir(), 'output parent must already exist')
    return dest


def run(report_path, out):
    dest = output_path(report_path, out)
    result = evaluate(report_path)
    data = (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    require(len(data) <= (8 << 20), 'output byte bound')
    # Recheck after reads, then create exclusively; never truncate an existing file.
    require(dest == output_path(report_path, out), 'output path changed')
    _outputs.add(str(dest))
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
    finally:
        _outputs.discard(str(dest))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args.report, args.out)
        print('supplemental ' + ('PASS' if result['supplemental_passed'] else 'FAIL') +
              f": {len(result['results'])} observations; comparisons withheld")
        return 0 if result['supplemental_passed'] else 1
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('duration gate refused: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
