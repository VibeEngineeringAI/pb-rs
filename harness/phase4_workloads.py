#!/usr/bin/env python3
"""Deterministic P4 workloads. Export, validate Go self-comparison, then bind oracle.

python harness/phase4_workloads.py --out target/phase4/workloads.json
Text/create output and exact request bytes require --oracle-report from a passing
Go-vs-Go report. JSON metrics/list projections additionally use independent hashes.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import cases


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def labels(i, n):
    return {'__name__': 'fixture_metric', **{f'label{j}': f'{i:06d}-café日志-{j}' for j in range(n - 1)}}


def suite(variants=True):
    result = []
    def add(family, size, argv, path, payload, count, expected=None, method='GET', buffered=True):
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        for output in (('default',) if family == 'saved-create' else ('text', 'json')):
            case = dict(name=f'p4-{family}-{size}-{output}', group='phase4-workloads',
                        argv=[*argv] if output == 'default' else [*argv, '-o', output], profile=True, expected_exit=0,
                        expected_requests=[f'{method} {path}'], track_json_array=False,
                        fixture={'headers': cases.BASIC, 'routes': [cases.response(method, path, body)]},
                        fixture_response_body_bytes=len(body.encode()), max_output_bytes=1 << 30,
                        spool_output=True, phase4={'family': family, 'count': count,
                        'buffered': buffered, 'oracle_required': True})
            if output == 'json' and expected is not None:
                case['phase4']['stdout_sha256'] = sha(encoded(expected))
            result.append(case)
    query = ['promql', 'run', 'fixture_metric', '--dataset', 'metrics', '--from', cases.START,
             '--to', cases.END, '--step', '60s']
    for n in (1, 100, 10000):
        payload = {'status': 'success', 'data': {'resultType': 'vector', 'result':
                   [{'metric': labels(i, 4), 'value': [1704067200, str(i)]} for i in range(n)]}}
        add('vector', str(n), [*query, '--instant'], '/prometheus/api/v1/query', payload, n, payload)
    for n, samples in ((1, 100), (100, 100), (1000, 1000), (1, 1000000)):
        payload = {'status': 'success', 'data': {'resultType': 'matrix', 'result':
                   [{'metric': labels(i, 4), 'values': [[1704067200+j*60, str(i* samples+j)]
                     for j in range(samples)]} for i in range(n)]}}
        add('matrix', f'{n}x{samples}', query, '/prometheus/api/v1/query_range', payload, n*samples, payload)
    for family in ('labels', 'values', 'series'):
        for n in ((1, 1000, 100000) if family == 'series' else (10, 1000, 100000)):
            data = [labels(i, 8) for i in range(n)] if family == 'series' else [f'{i:06d}-café日志' for i in range(n)]
            payload = {'status': 'success', 'data': data}
            cmd = ['promql', 'label-values', 'job'] if family == 'values' else ['promql', family]
            if family == 'series':
                cmd += ['--match', 'fixture_metric']
            path = '/prometheus/api/v1/label/job/values' if family == 'values' else f'/prometheus/api/v1/{family}'
            add(family, str(n), [*cmd, '--dataset', 'metrics'], path, payload, n, payload)
    for n in (20, 10000):
        entries = [{'name': f'{i:06d}-' + '日志é界🙂'*18, 'value': i+1} for i in range(n)]
        for sub in ('label-names', 'label-values', 'active-series'):
            data = {'total_active_series': n, 'series': [labels(i, 8) for i in range(n)]} if sub == 'active-series' else entries
            payload = {'status': 'success', 'data': data}
            cmd = ['promql', 'cardinality', sub]
            if sub == 'label-values': cmd += ['--label', 'job']
            add('cardinality-'+sub, str(n), [*cmd, '--dataset', 'metrics', '--limit', str(n)],
                '/prometheus/api/v1/cardinality/'+sub.replace('-', '_'), payload, n, payload)
        data = {'totalSeries': n*4, 'totalLabelValuePairs': n*8, **{key: entries for key in
                ('seriesCountByMetricName', 'seriesCountByLabelName', 'seriesCountByFocusLabelValue', 'labelValueCountByLabelName')}}
        payload = {'status': 'success', 'data': data}
        add('tsdb', str(n), ['promql', 'tsdb', '--dataset', 'metrics', '--focus-label', 'job', '--top', str(n)],
            '/prometheus/api/v1/status/tsdb', payload, n*4, payload)
    for n in (0, 100, 10000):
        filters, projected = [], []
        for i in range(n):
            item = {'filter_id': str(i), 'filter_name': f'query-{i}', 'stream_name': 'logs',
                    'query': {'filter_type': 'sql' if i%2 == 0 else 'builder'}, 'time_filter': {'from': cases.START, 'to': cases.END}}
            if i%2 == 0:
                item['query']['filter_query'] = f'SELECT {i} FROM logs'
                projected.append(dict(id=str(i), title=f'query-{i}', stream='logs', desc=f'SELECT {i} FROM logs', **item['time_filter']))
            else: item['query']['filter_builder'] = {'id': str(i), 'combinator': 'and', 'rules': []}
            filters.append(item)
        # Saved structs preserve declaration order, unlike generic metrics JSON.
        add('saved-list', str(n), ['sql', 'list'], '/api/v1/filters', filters, len(projected))
        result[-1]['phase4']['stdout_sha256'] = sha((json.dumps(projected, ensure_ascii=False, indent=2)+'\n').encode())
    add('saved-create', '1', ['sql', 'save', 'SELECT * FROM logs', '--name', 'fixture', '--from', cases.START, '--to', cases.END],
        '/api/v1/filters', {}, 1, method='POST', buffered=False)
    add('run-save', '1', ['sql', 'run', 'SELECT * FROM logs LIMIT 1', '--save-as', 'fixture', '--from', cases.START, '--to', cases.END],
        '/api/v1/query', [{'sequence': 0}], 1, method='POST', buffered=False)
    for case in result[-2:]:
        case['fixture']['routes'].append(cases.response('POST', '/api/v1/filters', {}))
        case['expected_requests'].append('POST /api/v1/filters')
    if variants:
        # Representative middle-size case in each family; avoid size×sink explosion.
        representatives = {}
        for case in result:
            family = case['phase4']['family']
            if case['argv'][-1] == 'json' or family == 'saved-create': representatives.setdefault(family, []).append(case)
        for family, choices in representatives.items():
            # Keep the frozen matrix delivery variants at their original size
            # when adding a different shape for the million-point stress case.
            base = next(case for case in choices if case['name'] == 'p4-matrix-100x100-json') \
                if family == 'matrix' else choices[len(choices)//2]
            for mode, options in [('headers', {'delay_ms': 50}), ('chunks', {'chunk_bytes': 4096, 'chunk_delay_ms': 2}),
                                  ('gzip', {'gzip': True}), ('slow-stdout', {})]:
                case = copy.deepcopy(base); case['name'] += '-'+mode
                for route in case['fixture']['routes']: route.update(options)
                if mode == 'slow-stdout': case['read_delay_ms'] = 10
                result.append(case)
    return result


def bind_oracle(workloads, report):
    """Accept only a complete, passing self-comparison of identical Go binaries."""
    binaries = report['binaries']
    if not report.get('complete') or not report.get('passed') or len(binaries) < 2:
        raise ValueError('a complete passing Go self-comparison is required')
    hashes = {value['sha256'] for value in binaries.values()}
    if len(hashes) != 1: raise ValueError('oracle binaries must have identical hashes')
    manifest = json.loads((Path(__file__).resolve().parent / 'contracts/phase4-reference.json').read_text())
    if next(iter(hashes)) != manifest['binaries']['reference']['sha256']: raise ValueError('oracle must match frozen pinned Go baseline')
    for case in workloads:
        entry = report['cases'][case['name']]
        bootstrap_spec = copy.deepcopy(case)
        bootstrap_spec['phase4']['oracle_required'] = False
        if entry.get('spec') != bootstrap_spec: raise ValueError('oracle workload definition changed: '+case['name'])
        contracts = [sample['contract'] for runs in entry['runs'].values() for sample in runs]
        if not contracts or any(not sample['valid'] for runs in entry['runs'].values() for sample in runs):
            raise ValueError('oracle samples failed')
        if any(contract != contracts[0] for contract in contracts): raise ValueError('unstable oracle')
        expected = case['phase4'].get('stdout_sha256')
        if expected and expected != contracts[0]['stdout_sha256']: raise ValueError('Go fails independent output assertion: '+case['name'])
        raw = [sample.get('phase4_raw_requests') for runs in entry['runs'].values() for sample in runs]
        if not raw or raw[0] is None or any(item != raw[0] for item in raw): raise ValueError('missing/unstable raw request oracle')
        case['phase4']['raw_requests'] = raw[0]
        case['phase4'].update(stdout_sha256=contracts[0]['stdout_sha256'], stderr_sha256=contracts[0]['stderr_sha256'],
                              requests=contracts[0]['requests'], oracle_binary_sha256=next(iter(hashes)), oracle_required=False)
    return workloads


def validate(case, contract):
    spec = case.get('phase4', {})
    issues = []
    if spec.get('oracle_required'): issues.append('phase4 oracle not frozen')
    for key in ('stdout_sha256', 'stderr_sha256', 'requests'):
        if key in spec and contract.get(key) != spec[key]: issues.append('phase4 '+key+' mismatch')
    return issues


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--base-only', action='store_true')
    parser.add_argument('--oracle-report')
    args = parser.parse_args()
    workloads = suite(not args.base_only)
    if args.oracle_report: bind_oracle(workloads, json.loads(Path(args.oracle_report).read_text()))
    with open(args.out, 'x') as stream: json.dump(workloads, stream, ensure_ascii=False, indent=2); stream.write('\n')
