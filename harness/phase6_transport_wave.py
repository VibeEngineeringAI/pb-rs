#!/usr/bin/env python3
"""Bounded F1/F2(a)/F3 process contracts using the maintained raw Flight fixture.

Cases originated in the transport owner's pinned-Go probe; expected bytes,
connection reuse and request counts are independent assertions, not waivers.
No original Phase6 reference is changed. No performance claims.
"""
import argparse
import base64
import copy
import json
from pathlib import Path

import phase6 as p6
from phase6_protocol_limits import FIXTURE_SHA256

def headers(encoding=None):
    fields = [[':status', '200'], ['content-type', 'application/grpc']]
    if encoding is not None:
        fields.append(['grpc-encoding', encoding])
    return dict(act='headers', fields=fields)


def trailers(code, message='owner probe terminal'):
    return dict(act='trailers', fields=[['grpc-status', str(code)], ['grpc-message', message]])


def raw(data):
    return dict(act='data', b64=base64.b64encode(data).decode())


def message(flag, body, declared=None):
    length = len(body) if declared is None else declared
    return bytes([flag]) + length.to_bytes(4, 'big') + body


def scenarios():
    empty = dict(act='data', b64='')
    out = {
        'flag1-gzip-init': [dict(acts=[headers('gzip'), raw(message(1, b'abc')), trailers(3)])],
        'flag1-identity-init': [dict(acts=[headers('identity'), raw(message(1, b'abc')), trailers(3)])],
        'flag1-none-init': [dict(acts=[headers(), raw(message(1, b'abc')), trailers(3)])],
        'flag2-gzip-init': [dict(acts=[headers('gzip'), raw(message(2, b'abc')), trailers(3)])],
        'flag1-empty-body-init': [dict(acts=[headers('gzip'), raw(message(1, b'')), trailers(3)])],
        # Size gate before the flag: prefix declares 4 MiB + 1, no body.
        'flag1-overlimit-init': [dict(acts=[headers('gzip'), raw(message(1, b'', 4194305)), trailers(3)])],
        'flag0-overlimit-init': [dict(acts=[headers('gzip'), raw(message(0, b'', 4194305)), trailers(3)])],
        # Body read before the flag: partial body then trailers.
        'flag1-partial-body-init': [dict(acts=[headers('gzip'), raw(message(1, b'abc', 10)), trailers(3)])],
        'flag1-partial-prefix-init': [dict(acts=[headers('gzip'), raw(bytes([1, 0, 0])), trailers(3)])],
        # Established: a delivered batch, then a compressed message.
        'flag1-gzip-batch': [dict(acts=[headers('gzip'), dict(act='schema'), dict(act='batch', rows=1),
                                        raw(message(1, b'abc')), trailers(3)])],
        'flag0-gzip-batch': [dict(acts=[headers('gzip'), dict(act='schema'), dict(act='batch', rows=1),
                                        trailers(3)])],
        # Bad message split across DATA frames, then trailers.
        'flag1-split-init': [dict(acts=[headers('gzip'), raw(message(1, b'abcdef')[:4]),
                                        raw(message(1, b'abcdef')[4:]), trailers(3)])],
        # Empty DATA (F2a) witnesses with splits and END_STREAM control.
        'empty-data-101': [
            dict(acts=[headers(), dict(act='schema')] + [empty] * 101 +
                 [dict(act='batch', rows=2), trailers(3)]),
            dict(acts=[headers(), dict(act='schema'), dict(act='batch', rows=1), trailers(3)])],
        'empty-data-60x2-across-reconnect': [
            dict(acts=[headers(), dict(act='schema')] + [empty] * 60 + [dict(act='batch', rows=1), trailers(0)]),
            dict(acts=[headers(), dict(act='schema')] + [empty] * 60 + [dict(act='batch', rows=2), trailers(3)])],
        'goaway-after-headers-then-reconnect': [
            dict(acts=[headers(), dict(act='goaway', last='below'), dict(act='close')]),
            dict(acts=[headers(), dict(act='schema'), dict(act='batch', rows=1), trailers(0)]),
            dict(acts=[headers(), dict(act='schema'), dict(act='batch', rows=1), trailers(3)])],
    }
    return out


def build(name, subs, mode):
    template = next(c for c in p6.corpus() if c['name'] == 'p6-raw-message-ff-3')
    case = copy.deepcopy(template)
    dataset = 'ow-' + name + '-' + mode
    old = case['dataset']
    case.update(name=dataset, dataset=dataset, group='owner-probe', timeout=8)
    case['argv'] = ['tail', dataset, '-o', mode]
    fx = case['fixture']['phase6']
    fx['streams'][dataset] = fx['streams'].pop(old)
    fx['endpoints'][0]['subscriptions'] = subs
    return case


def cases():
    result = []
    for name, subscriptions in scenarios().items():
        case = build(name, subscriptions, 'json')
        code, message = 'InvalidArgument', 'owner probe terminal'
        batches = []
        streams, connections = [[0, 1]], 1
        if name.startswith('flag1'):
            code, message = 'Unimplemented', 'grpc: Decompressor is not installed for grpc-encoding "gzip"'
        if name in ('flag1-identity-init', 'flag1-none-init'):
            code, message = 'Internal', 'grpc: compressed flag set with identity or empty encoding'
        if name == 'flag2-gzip-init':
            code, message = 'Internal', 'grpc: received unexpected payload format 2'
        if 'overlimit' in name:
            code, message = 'ResourceExhausted', 'grpc: received message larger than max (4194305 vs. 4194304)'
        if 'partial' in name:
            code, message = 'Internal', 'unexpected EOF'
        if name in ('flag1-gzip-batch', 'flag0-gzip-batch'):
            batches = [[0]]
        if name == 'empty-data-101':
            batches = [[0, 1]]
        if name == 'empty-data-60x2-across-reconnect':
            batches, streams = [[0], [1, 2]], [[0, 1], [1, 3]]
        if name == 'goaway-after-headers-then-reconnect':
            batches, streams, connections = [[0], [1]], [[0, 1], [1, 1], [2, 3]], 2
        output = b''.join(b''.join(p6.basic_output(n)[:-1] for n in batch) + b'\n'
                          for batch in batches)
        error = ('rpc error: code = ' + code + ' desc = ' + message)
        stderr = (json.dumps({'error': {'code': 'COMMAND_FAILED', 'message': error,
                                       'retryable': False}}, indent=2) + '\n').encode()
        case['expected'].update(output_summary=False, dogets=len(streams),
            stdout_sha256=p6.run.digest(output), stdout_rows=sum(map(len, batches)),
            stdout_batches=len(batches))
        case['transport_wave'] = dict(probe=name, stdout_bytes=len(output),
            stderr_b64=base64.b64encode(stderr).decode(), connections=connections,
            streams=streams, transparent_retry=name.startswith('goaway-'))
        result.append(case)
    return result


def assertions(case, capture):
    issues = p6.expectation_issues(case, capture)
    want = case['transport_wave']
    if capture.get('raw_wait_status') != 256:
        issues.append('expected ordinary raw exit 1')
    if capture.get('stdout_bytes') != want['stdout_bytes']:
        issues.append('exact stdout byte count differs')
    if capture.get('stderr_contract_b64') != want['stderr_b64']:
        issues.append('exact terminal envelope/order differs')
    events = capture.get('events', [])
    streams = [e for e in events if e.get('kind') == 'raw_h2_stream']
    if [[e.get('ordinal'), e.get('stream_id')] for e in streams] != want['streams']:
        issues.append('physical request ordinals/HTTP2 stream reuse differs')
    connections = sum(e.get('kind') == 'raw_h2_conn' for e in events)
    if connections != want['connections']:
        issues.append('physical connection count differs')
    summary = capture.get('summary', {})
    if summary.get('events_dropped') != 0 or summary.get('trace_dropped') != 0:
        issues.append('bounded protocol witness lost fixture events or requests')
    if summary.get('event_kinds', {}).get('raw_h2_conn') != connections:
        issues.append('connection witness lacks exact summary coverage')
    if summary.get('event_kinds', {}).get('raw_h2_stream') != len(streams):
        issues.append('stream witness lacks exact summary coverage')
    if want['transparent_retry']:
        if len(streams) != 3 or not 0 <= (streams[1]['t_ns'] - streams[0]['t_ns']) / 1e6 < 450:
            issues.append('first unprocessed refusal is not transparent')
    return issues


def classify(case, go, candidate):
    issues = assertions(case, go)
    second, differences, wire = p6.split_wire_ua(
        go, candidate, assertions(case, candidate), p6.differences(go, candidate))
    issues += ['candidate: ' + i for i in second] + differences
    return dict(issues=issues, wire_differences=wire,
                remaining_contract_passed=not issues,
                strict_parity_passed=not issues and not wire)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('go', 'candidate', 'fixture', 'out'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    paths = {name: Path(getattr(args, name)).resolve() for name in ('go', 'candidate', 'fixture')}
    identities = {name: p6.run.digest(path.read_bytes()) for name, path in paths.items()}
    if identities['go'] != p6.ORACLE_HASHES['standard'] or identities['fixture'] != FIXTURE_SHA256:
        raise ValueError('requires pinned Go and immutable r5 fixture')
    sources = {str(path): p6.run.digest(path.read_bytes()) for path in
               (Path(__file__).resolve(), Path(p6.__file__).resolve())}
    out = Path(args.out).resolve()
    if p6.TMP not in out.parents:
        raise ValueError('output must be under phase6 disk cache tmp')
    out.mkdir(parents=True, exist_ok=False)
    inventory = cases()
    report = dict(complete=False, full_parity_passed=False, identities=identities,
        sources=sources, corpus_sha256=p6.run.digest(p6.run.canonical(inventory)),
        cases={}, failures=0, remaining_contract_passed=0, strict_parity_passed=0)
    try:
        for case in inventory:
            captures = {}
            for role in ('go', 'go_repeat', 'candidate', 'candidate_repeat'):
                binary_role = 'go' if role.startswith('go') else 'candidate'
                captures[role] = p6.sample(paths[binary_role], paths['fixture'], case,
                                           out / case['name'] / role)
            entry = classify(case, captures['go'], captures['candidate'])
            for role in ('go', 'candidate'):
                entry['issues'] += [role + ' A/A: ' + i for i in
                    p6.differences(captures[role], captures[role + '_repeat'])]
                repeat_issues = assertions(case, captures[role + '_repeat'])
                if role == 'candidate':
                    repeat_issues, _, _ = p6.split_wire_ua(
                        captures['go'], captures[role + '_repeat'], repeat_issues, [])
                entry['issues'] += [role + ' repeat: ' + i for i in repeat_issues]
            entry['captures'] = {}
            for role, capture in captures.items():
                path = out / case['name'] / role / 'result.json'
                entry['captures'][role] = dict(path=str(path), sha256=p6.run.digest(path.read_bytes()))
                binary_role = 'go' if role.startswith('go') else 'candidate'
                if capture['binary_sha256'] != identities[binary_role] or capture['fixture_sha256'] != identities['fixture']:
                    entry['issues'].append(role + ': executable changed')
            entry['remaining_contract_passed'] = not entry['issues']
            entry['strict_parity_passed'] = not entry['issues'] and not entry['wire_differences']
            report['cases'][case['name']] = entry
            report['failures'] += bool(entry['issues'])
            for key in ('remaining_contract_passed', 'strict_parity_passed'):
                report[key] += entry[key]
            p6.run.write_json(out / 'report.json', report)
            print(case['name'], entry['issues'] or 'REMAINING CONTRACT PASS', flush=True)
        report['identities_unchanged'] = (
            all(p6.run.digest(path.read_bytes()) == identities[name] for name, path in paths.items()) and
            all(p6.run.digest(Path(path).read_bytes()) == digest for path, digest in sources.items()))
        report['complete'] = True
        return int(bool(report['failures']) or not report['identities_unchanged'])
    finally:
        p6.run.write_json(out / 'report.json', report)


if __name__ == '__main__':
    raise SystemExit(main())


