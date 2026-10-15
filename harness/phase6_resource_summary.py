#!/usr/bin/env python3
"""Offline, bounded descriptive summaries of one Phase6 campaign's retained bytes.

No collector imports, process execution, locks, ratio reconstruction or admission.
See docs/phase6-resource-summary.md for coverage and binding limits.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import stat
import statistics
import sys

REPORT_BYTES = 4 << 20
JOURNAL_BYTES = 64 << 20
LINE_BYTES = 1 << 20
OUTPUT_BYTES = 16 << 20
MAX_RECORDS = 10000
MAX_WORKLOADS = 64
MAX_DEPTH = 64
MAX_NUMBER = 1e100
BASE_METRICS = ('wall_ms', 'first_complete_record_ms', 'fixture_to_first_complete_record_ms',
                'cpu_ms', 'cpu_us_per_row', 'peak_rss_bytes', 'rows_per_second',
                'output_bytes_per_second')
COUNTERS = ('minor_faults', 'major_faults', 'voluntary_context_switches',
            'involuntary_context_switches', 'block_input_operations', 'block_output_operations',
            'proc_io_syscr', 'proc_io_syscw', 'proc_io_rchar', 'proc_io_wchar',
            'proc_io_read_bytes', 'proc_io_write_bytes', 'proc_io_cancelled_write_bytes',
            'peak_rss_bytes', 'stdout_bytes', 'rows', 'batches', 'fixture_encoded_bytes',
            'fixture_decoded_bytes')
METRICS = tuple(dict.fromkeys(BASE_METRICS + COUNTERS + (
    'encoded_bytes_per_second', 'decoded_bytes_per_second', 'cpu_user_ms', 'cpu_system_ms')))
CONTRACT_FIELDS = ('stdout_sha256', 'stdout_bytes', 'rendered_rows', 'batches', 'stderr_sha256',
                   'termination', 'requests', 'ticket_sha256', 'request_validation',
                   'workload_sha256', 'sink', 'tls', 'config_before', 'config_after')
COVERAGE = ('invalid_records', 'sequence_errors', 'missing_proc_samples',
            'resources_dropped_bytes', 'visibility_dropped_bytes', 'stdout_prefix_dropped_bytes',
            'fixture_events_dropped', 'batch_visibility.uncorrelated',
            'batch_visibility.retained_dropped_bytes', 'request_evidence.uncaptured_dogets',
            'production.live.omitted_by_deadline', 'production.live.omitted_by_termination')
IDENTITIES = ('fixture', 'meter', 'collector', 'process_harness', 'budget_file',
              'fixture_manifest', 'parent_manifest')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def integer(value, name, minimum=0, maximum=(1 << 64) - 1):
    require(type(value) is int and minimum <= value <= maximum, 'invalid integer: ' + name)
    return value


def number(value, name):
    require(type(value) in (int, float) and math.isfinite(value) and abs(value) <= MAX_NUMBER,
            'invalid finite number: ' + name)
    return value


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result
    def bad_constant(value):
        raise ValueError('nonfinite JSON constant: ' + value)
    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=bad_constant)
        def walk(item, depth):
            require(depth <= MAX_DEPTH, 'JSON nesting limit exceeded')
            if isinstance(item, dict):
                for child in item.values():
                    walk(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    walk(child, depth + 1)
            elif type(item) in (int, float):
                number(item, 'JSON number')
        walk(value, 0)
        return value
    except (RecursionError, OverflowError, UnicodeError) as error:
        raise ValueError('malformed/bounded JSON: ' + str(error)) from error


def read_bound(path, limit):
    """One read supplies both parsed data and identity; no live source lookups."""
    path = Path(path).expanduser().absolute()
    resolved = path.resolve(strict=True)
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    with os.fdopen(os.open(resolved, flags), 'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'input must be a regular file')
        data = stream.read(limit + 1)
    require(len(data) <= limit, 'input exceeds byte limit: ' + str(path))
    return data, dict(path=str(path), resolved_path=str(resolved), size_bytes=len(data),
                      sha256=hashlib.sha256(data).hexdigest())


def unchanged(identity, limit):
    _, current = read_bound(identity['path'], limit)
    require(current == identity, 'input content/path drift: ' + identity['path'])


def validate_identity(value, name):
    require(isinstance(value, dict) and isinstance(value.get('path'), str) and
            bool(value['path']) and sha(value.get('sha256')), 'malformed identity: ' + name)
    integer(value.get('size_bytes'), name + '.size_bytes')


def validate_report(report):
    require(isinstance(report, dict) and type(report.get('schema_version')) is int and
            report['schema_version'] == 2, 'requires collector campaign schema_version 2')
    for key in ('complete', 'passed', 'tls', 'comparison_contracts_passed'):
        require(type(report.get(key)) is bool, 'explicit boolean required: ' + key)
    require(report['complete'] or not report['passed'], 'incomplete report claims passed')
    require(not report['comparison_contracts_passed'] or (report['complete'] and report['passed']),
            'inconsistent comparison contract status')
    end_check = report.get('end_identity_check')
    if end_check is not None:
        require(isinstance(end_check, dict) and type(end_check.get('passed')) is bool and
                isinstance(end_check.get('changed'), list) and all(isinstance(x, str) for x in end_check['changed']) and
                end_check['passed'] == (not end_check['changed']), 'inconsistent end identity status')
        require(not report['passed'] or end_check['passed'], 'passed report contradicts changed identities')
    require(report.get('tier') in ('finite', 'functional', 'soak'), 'unsupported campaign tier')
    mode, kind = report.get('collector_mode'), report.get('kind')
    require(mode in ('full', 'hash-framing'), 'unsupported collector mode')
    require(kind in ('native_unprofiled', 'observer_control', 'timing_aa'), 'unsupported campaign kind')
    require(kind == 'timing_aa' or kind == ('native_unprofiled' if mode == 'full' else 'observer_control'),
            'kind/collector mode mismatch')
    labels = report.get('binaries')
    expected_labels = {'go-a', 'go-b'} if kind == 'timing_aa' else {'go', 'candidate'}
    require(isinstance(labels, dict) and set(labels) == expected_labels, 'campaign label mismatch')
    for key in IDENTITIES:
        validate_identity(report.get(key), key)
    for label, value in labels.items():
        validate_identity(value, label)
    manifest = report.get('manifest')
    require(isinstance(manifest, dict), 'missing embedded manifest')
    require(sha(report.get('stable_manifest_sha256')) and report['stable_manifest_sha256'] ==
            canonical_hash({k: v for k, v in manifest.items() if k != 'timing_aa'}),
            'embedded stable manifest hash mismatch')
    require(isinstance(report.get('budgets'), dict) and report['budgets'] == manifest.get('budgets') and
            report['budget_file']['sha256'] == manifest.get('budgets_sha256'), 'embedded budget mismatch')
    require(isinstance(report.get('fixture_build'), dict) and
            report['fixture_build'] == manifest.get('fixture_build') and
            report['fixture_build'].get('binary', {}).get('sha256') == report['fixture']['sha256'] and
            manifest.get('fixture_manifest', {}).get('sha256') == report['fixture_manifest']['sha256'],
            'embedded fixture provenance mismatch')
    bindings = report.get('measured_roles')
    require(isinstance(bindings, dict) and set(bindings) == set(labels) | {'meter'}, 'missing role bindings')
    role_paths = {}
    for label, binding in bindings.items():
        role = 'go' if kind == 'timing_aa' and label != 'meter' else label
        require(isinstance(binding, dict) and binding.get('role') == role, 'role mismatch: ' + label)
        for part in ('selected', 'declared'):
            validate_identity(binding.get(part), label + '.' + part)
        selected = report['meter'] if label == 'meter' else labels[label]
        require(binding['selected'] == selected and binding['declared']['sha256'] == selected['sha256'],
                'selected/declared identity mismatch: ' + label)
        require(binding['declared']['size_bytes'] == selected['size_bytes'] and
                type(binding.get('content_identical_copy')) is bool and binding['content_identical_copy'] ==
                (binding['declared']['path'] != selected['path']), 'inconsistent copy identity: ' + label)
        declared = (manifest.get('meter') if role == 'meter' else
                    manifest.get('baselines', {}).get('pb-go') if role == 'go' else
                    manifest.get('build_flags', {}).get('candidate', {}).get('pb'))
        require(isinstance(declared, dict) and declared.get('sha256') == binding['declared']['sha256'] and
                isinstance(declared.get('path'), str) and bool(declared['path']),
                'embedded role declaration mismatch: ' + label)
        # The collector binds these declarations by SHA, resolving paths at collection time.
        role_paths[label] = dict(manifest_declared_path=declared['path'],
                                bound_declared_path=binding['declared']['path'], selected_path=selected['path'])
    if kind == 'timing_aa':
        require(labels['go-a']['sha256'] == labels['go-b']['sha256'] and report['tier'] == 'finite' and
                report.get('warmups') == 5 and report.get('repetitions') == 30, 'timing A/A declaration mismatch')
    integer(report.get('warmups'), 'warmups', maximum=MAX_RECORDS)
    integer(report.get('repetitions'), 'repetitions', minimum=1, maximum=MAX_RECORDS)
    integer(report.get('seed'), 'seed', minimum=-(10 ** 100), maximum=10 ** 100)
    integer(report.get('session_index'), 'session_index', minimum=-(10 ** 100), maximum=10 ** 100)
    number(report.get('duration_s'), 'duration_s')
    require(0 < report['duration_s'] <= 3600, 'duration out of collector bounds')
    ws = report.get('workloads')
    require(isinstance(ws, list) and 0 < len(ws) <= MAX_WORKLOADS, 'workload count out of bounds')
    names = []
    for workload in ws:
        require(isinstance(workload, dict) and isinstance(workload.get('name'), str) and
                bool(workload['name']) and isinstance(workload.get('stream'), dict) and
                isinstance(workload.get('sink'), dict) and 'duration_s' in workload, 'malformed workload')
        require(workload['duration_s'] == (None if report['tier'] == 'finite' else report['duration_s']),
                'workload duration cohort mismatch')
        names.append(workload['name'])
    require(len(set(names)) == len(names), 'duplicate workload')
    total = len(ws) * len(labels) * (report['warmups'] + report['repetitions'])
    require(total <= MAX_RECORDS, 'declared schedule exceeds record limit')
    require(report.get('expected_measured_samples') == len(ws) * 2 * report['repetitions'] and
            type(report.get('expected_measured_samples')) is int, 'expected measured count mismatch')
    integer(report.get('recorded_measured_samples'), 'recorded_measured_samples', maximum=MAX_RECORDS)
    require(isinstance(report.get('summaries'), dict) and set(report['summaries']) <= set(names),
            'unknown summary workload')
    return labels, {w['name']: w for w in ws}, role_paths


def declared_schedule(report):
    """Mirror frozen schedule metadata only; never import the executable collector."""
    labels = list(report['binaries'])
    for w in report['workloads']:
        derived = int.from_bytes(hashlib.sha256(f"{report['seed']}:{w['name']}".encode()).digest()[:8], 'big')
        rng = random.Random(derived)
        for warmup, rounds in ((True, report['warmups']), (False, report['repetitions'])):
            for index in range(rounds):
                order = list(labels)
                if report['tier'] == 'finite':
                    rng.shuffle(order)
                elif (report['session_index'] + index) % 2:
                    order.reverse()
                for label in order:
                    yield dict(workload=w['name'], label=label, warmup=warmup, round=index,
                               workload_seed=derived, session_index=report['session_index'] + index,
                               implementation_order='AB' if order == labels else 'BA')


def extract(sample, field):
    value = sample
    for part in field.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def validate_sample(sample, expected, workload, report):
    require(isinstance(sample, dict), 'journal record must be an object')
    for key, value in expected.items():
        require(type(sample.get(key)) is type(value) and sample[key] == value,
                'journal schedule/cohort mismatch: ' + key)
    require(type(sample.get('valid')) is bool, 'sample valid must be boolean')
    require(isinstance(sample.get('errors'), list) and all(isinstance(e, str) for e in sample['errors']),
            'sample errors must be a string list')
    for field in METRICS + COVERAGE:
        value = extract(sample, field)
        if value is not None:
            if field in COUNTERS or field in COVERAGE:
                minimum = -((1 << 64) - 1) if field == 'request_evidence.uncaptured_dogets' and not sample['valid'] else 0
                integer(value, field, minimum=minimum)
            else:
                number(value, field)
                require(value >= 0 or field in ('first_complete_record_ms', 'fixture_to_first_complete_record_ms'),
                        'negative metric: ' + field)
    for field in ('collector_mode', 'tls', 'sink'):
        want = workload['sink'] if field == 'sink' else report[field]
        if field in sample:
            require(type(sample[field]) is type(want) and sample[field] == want, 'sample cohort mismatch: ' + field)
    require(sample.get('kind') in (None, 'observer_control'), 'diagnostic/unknown sample kind')
    if 'kind' in sample:
        require(report['collector_mode'] == 'hash-framing', 'control sample in primary campaign')
    if 'proc_io_available' in sample:
        require(type(sample['proc_io_available']) is bool, 'proc_io_available must be boolean')
        require(sample['proc_io_available'] or all(sample.get(k) is None for k in COUNTERS if k.startswith('proc_io_')),
                'unavailable proc IO contains observations')
    if 'resource_source' in sample:
        require(sample['resource_source'] == 'native_child_wait4', 'unexpected native resource scope')
    if 'proc_io_source' in sample:
        require(sample['proc_io_source'] in (None, 'linux_proc_pid_io_before_wait4'), 'unexpected proc IO scope')
    if 'first_record_observed_after_native_exit' in sample:
        require(sample['first_record_observed_after_native_exit'] is None or
                type(sample['first_record_observed_after_native_exit']) is bool,
                'first_record_observed_after_native_exit must be boolean or null')
    contract = sample.get('contract')
    if contract is not None:
        require(isinstance(contract, dict), 'malformed sample contract')
        wanted_hash = canonical_hash({k: workload[k] for k in ('stream', 'duration_s', 'sink')})
        require(contract.get('workload_sha256') == wanted_hash and type(contract.get('tls')) is bool and
                contract['tls'] == report['tls'] and contract.get('sink') == workload['sink'],
                'sample contract workload/TLS/sink mismatch')
    if sample['valid']:
        require(not sample['errors'] and isinstance(contract, dict) and all(k in contract for k in CONTRACT_FIELDS),
                'valid sample has errors or missing contract')
        for field in ('capture_complete', 'contract_validated'):
            if field in sample:
                require(sample[field] is True, 'valid sample contradicts ' + field)
        require(not sample.get('collector_exception') and not sample.get('timed_out'), 'valid sample contradicts failure')
        for field in CONTRACT_FIELDS:
            if field.endswith('sha256') or field in ('config_before', 'config_after'):
                require(sha(contract[field]), 'malformed contract hash: ' + field)
        for field in ('stdout_bytes', 'rendered_rows', 'batches', 'requests'):
            integer(contract[field], 'contract.' + field)
        require(isinstance(contract['request_validation'], dict) and contract['termination'] == {'exit': 1} and
                type(contract['termination'].get('exit')) is int and contract['config_before'] == contract['config_after'],
                'valid sample contradicts source contract')
        for source, target in (('stdout_bytes', 'stdout_bytes'), ('rows', 'rendered_rows'), ('batches', 'batches'),
                               ('stdout_sha256', 'stdout_sha256'), ('stderr_sha256', 'stderr_sha256'),
                               ('termination', 'termination'), ('requests', 'requests')):
            if source in sample:
                require(sample[source] == contract[target] and type(sample[source]) is type(contract[target]),
                        'sample/contract mismatch: ' + source)
    for obj, fields in ((sample.get('cpu_screen'), ('comparative_admissible', 'throughput_admissible')),
                        (sample.get('production'), ('throughput_admissible',))):
        if obj is not None:
            require(isinstance(obj, dict), 'malformed admission metadata')
            for field in fields:
                if field in obj:
                    require(type(obj[field]) is bool, 'admission status must be boolean')


def describe(values, total):
    return dict(available=len(values), missing=total - len(values),
                median=statistics.median(values) if values else None,
                minimum=min(values) if values else None, maximum=max(values) if values else None)


def group_summary(samples, expected):
    valid = [s for s in samples if s['valid']]
    invalid = [s for s in samples if not s['valid']]
    metrics = {field: describe([s[field] for s in valid if s.get(field) is not None], len(valid)) for field in METRICS}
    coverage = {}
    for state, subset in (('valid', valid), ('invalid', invalid)):
        coverage[state] = {}
        for field in COVERAGE:
            values = [extract(s, field) for s in subset if extract(s, field) is not None]
            coverage[state][field] = dict(available=len(values), missing=len(subset) - len(values),
                                         total=sum(values) if values else None)
    flags = {}
    for field in ('cpu_screen.comparative_admissible', 'cpu_screen.throughput_admissible',
                  'cpu_screen.interference_status', 'cpu_screen.potential_limit_status',
                  'production.throughput_admissible', 'first_record_observed_after_native_exit'):
        flags[field] = dict(Counter(json.dumps(extract(s, field), sort_keys=True) for s in valid))
    return dict(expected=expected, recorded=len(samples), unrecorded=expected - len(samples),
                valid=len(valid), invalid=len(invalid), statistics_population='source-valid observations only',
                metrics=metrics, observation_coverage=coverage, source_flags=flags)


def population_summary(samples, expected, report):
    if report['tier'] == 'finite':
        return group_summary(samples, expected)
    # Duration rounds are separate sessions, even inside a single report.
    sessions = {str(report['session_index'] + index): group_summary(
        [s for s in samples if s['session_index'] == report['session_index'] + index], 1)
        for index in range(expected)}
    return dict(expected=expected, recorded=len(samples), unrecorded=expected - len(samples),
                valid=sum(s['valid'] for s in samples), invalid=sum(not s['valid'] for s in samples),
                statistics_population='each duration session separately', sessions=sessions)


def verify_summaries(report, groups):
    checked = []
    for name, row in report['summaries'].items():
        require(isinstance(row, dict), 'malformed report summary')
        for label in report['binaries']:
            require(isinstance(row.get(label), dict), 'missing report summary label')
            valid = [s for s in groups[name][label]['measured'] if s['valid']]
            for field in BASE_METRICS:
                values = [s[field] for s in valid if s.get(field) is not None]
                want = describe(values, len(valid))
                got = row[label].get(field)
                require(isinstance(got, dict), 'missing report metric summary: ' + field)
                for key, value in (('n', len(values)), ('median', want['median']),
                                   ('minimum', want['minimum']), ('maximum', want['maximum'])):
                    require(key in got and ((value is None and got[key] is None) or
                            (type(got[key]) in (int, float) and got[key] == value)),
                            'report/journal metric mismatch: ' + name + '/' + label + '/' + field + '/' + key)
            checked.append(name + '/' + label)
        contract = row.get('contract_comparison')
        require(isinstance(contract, dict) and type(contract.get('samples')) is int and
                contract['samples'] == sum(len(groups[name][l]['measured']) for l in report['binaries']),
                'report/journal contract count mismatch')
        require(type(contract.get('comparable')) is bool, 'malformed contract comparison status')
        for field in ('ratio_quarantine',):
            require(isinstance(row.get(field), list) and all(isinstance(x, str) for x in row[field]),
                    'malformed source restriction: ' + field)
        for field in ('metric_quarantine', 'metric_scope'):
            require(isinstance(row.get(field), dict), 'missing source restriction: ' + field)
    if report['complete']:
        require(set(report['summaries']) == set(groups), 'completed report missing summaries')
    return checked


def summarize_bytes(report_data, journal_data):
    require(len(report_data) <= REPORT_BYTES and len(journal_data) <= JOURNAL_BYTES, 'input byte limit exceeded')
    report = strict_json(report_data)
    labels, workloads, role_paths = validate_report(report)
    require(not journal_data or journal_data.endswith(b'\n'), 'journal has incomplete final line')
    lines = journal_data.splitlines()
    require(len(lines) <= MAX_RECORDS, 'journal record limit exceeded')
    schedule = list(declared_schedule(report))
    require(len(lines) <= len(schedule), 'extra journal records')
    groups = {name: {label: dict(measured=[], warmups=[]) for label in labels} for name in workloads}
    invalid_records = []
    for index, line in enumerate(lines):
        require(0 < len(line) <= LINE_BYTES, 'empty/oversize journal line')
        sample = strict_json(line)
        item = schedule[index]
        validate_sample(sample, item, workloads[item['workload']], report)
        population = 'warmups' if sample['warmup'] else 'measured'
        groups[sample['workload']][sample['label']][population].append(sample)
        if not sample['valid']:
            invalid_records.append(dict(line=index + 1, record_sha256=hashlib.sha256(line).hexdigest(),
                                        **item, errors=sample['errors']))
    recorded_measured = sum(not item['warmup'] for item in schedule[:len(lines)])
    require(recorded_measured == report['recorded_measured_samples'], 'report/journal measured count mismatch')
    require(not report['complete'] or len(lines) == len(schedule), 'completed report has truncated journal')
    require(not report['passed'] or not invalid_records, 'passed report contains invalid observations')
    checked = verify_summaries(report, groups)
    output_groups = {}
    for name, by_label in groups.items():
        output_groups[name] = {label: {population: population_summary(samples,
            report['warmups'] if population == 'warmups' else report['repetitions'], report)
            for population, samples in populations.items()}
            for label, populations in by_label.items()}
    restrictions = {name: {field: row.get(field) for field in
        ('ratio_quarantine', 'metric_quarantine', 'metric_scope', 'contract_comparison')}
        for name, row in report['summaries'].items()}
    return dict(schema_version=1, kind='offline_resource_description', descriptive_only=True,
        acceptance='not_assessed', statistics_status='complete' if report['complete'] else 'incomplete_valid_subset',
        source_status={k: report.get(k) for k in ('complete', 'passed', 'comparison_contracts_passed', 'failure',
                                               'end_identity_check', 'unstarted_workloads')},
        cohort={k: report[k] for k in ('kind', 'tier', 'collector_mode', 'tls', 'seed', 'session_index',
                    'duration_s', 'binaries', 'workloads', 'stable_manifest_sha256', *IDENTITIES)},
        role_path_provenance=role_paths,
        source_restrictions=restrictions, groups=output_groups, invalid_records=invalid_records,
        binding_checks=dict(schedule='exact prefix', measured_count='matched',
                            report_absolute_summaries_checked=checked),
        contract_aa=dict(status='not_in_these_inputs', scope='go-aa.json contract reproducibility is separate; '
                         'not included in samples.jsonl'),
        limitations=['No timing authenticity, source acceptance, quiescence or budget conclusion is established.',
            'Report schema 2 has no journal digest: schedule/contracts/summary agreement cannot authenticate resource-only changes.',
            'Hashes describe the exact input bytes read; historical executables and manifests are not reread.',
            'Missing observations remain missing; invalid observations are excluded from all metric statistics.',
            'Rates are retained collector values, never recalculated; procfs IO is not network traffic.',
            'No owner maxima, batch-latency quantiles, independent-session aggregation or memory-release inference.'],
        limits=dict(report_bytes=REPORT_BYTES, journal_bytes=JOURNAL_BYTES, line_bytes=LINE_BYTES,
                    records=MAX_RECORDS, workloads=MAX_WORKLOADS, output_bytes=OUTPUT_BYTES, nesting=MAX_DEPTH))


def summarize_paths(report_path, output_path):
    report_path = Path(report_path).expanduser().absolute()
    require(report_path.name == 'report.json', 'input must be named report.json')
    output_path = Path(output_path).expanduser().absolute()
    require(not output_path.exists() and not output_path.is_symlink(), 'refusing to overwrite output')
    report_data, report_identity = read_bound(report_path, REPORT_BYTES)
    journal_path = Path(report_identity['resolved_path']).with_name('samples.jsonl')
    require(output_path.resolve() not in (Path(report_identity['resolved_path']), journal_path.resolve()),
            'output aliases an input')
    try:
        journal_data, journal_identity = read_bound(journal_path, JOURNAL_BYTES)
    except FileNotFoundError as error:
        raise ValueError('missing retained samples.jsonl; campaign has no retained journal') from error
    result = summarize_bytes(report_data, journal_data)
    result['inputs'] = dict(report=report_identity, journal=journal_identity)
    result['input_drift_check'] = 'same paths and content on bounded reread before output creation'
    data = (json.dumps(result, indent=2, allow_nan=False) + '\n').encode()
    require(len(data) <= OUTPUT_BYTES, 'output byte limit exceeded')
    unchanged(report_identity, REPORT_BYTES)
    unchanged(journal_identity, JOURNAL_BYTES)
    # O_EXCL covers concurrent writers and dangling symlinks. Remove only our inode on write failure.
    fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    own_stat = os.fstat(fd)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        current = output_path.lstat()
        if (current.st_dev, current.st_ino) == (own_stat.st_dev, own_stat.st_ino):
            output_path.unlink()
        raise
    return dict(path=str(output_path), size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(summarize_paths(args.report, args.out), sort_keys=True))
    except (OSError, ValueError) as error:
        print('resource summary refused: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
