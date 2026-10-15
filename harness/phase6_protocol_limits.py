#!/usr/bin/env python3
"""Bounded IA-2 capability-limit evidence, separate from Phase6 parity suites."""

import argparse
import base64
import copy
import json
from pathlib import Path
import sys

import phase6 as p6

FIXTURE_SHA256 = 'c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314'
LIMIT_ID = 'IA-2-INFORMATIONAL-H2-FLIGHT'
EARLY = ('rpc error: code = Unknown desc = unexpected HTTP status code received '
         'from server: 103 (Early Hints)')
LATE = 'rpc error: code = InvalidArgument desc = phase6 protocol limit terminal'
USAGE = (b'Usage:\n  pb tail dataset-name [flags]\n\nExamples:\n pb tail backend_logs\n\n'
         b'Flags:\n  -h, --help            help for tail\n'
         b'  -o, --output string   Output format (text|json) (default "text")\n\n')


def stderr_bytes(name, mode, early):
    message = EARLY if early else LATE
    if mode == 'json':
        return (json.dumps({'error': {'code': 'COMMAND_FAILED', 'message': message,
                                    'retryable': False}}, indent=2) + '\n').encode()
    prefix = ('\r| connecting...\r\x1b[K\r\x1b[K● watching ' + name +
              '... (ctrl+c to stop)\r\x1b[K').encode()
    return prefix + (b'' if early else b'\r\x1b[K') + b'Error: ' + message.encode() + b'\n' + USAGE


def cases():
    template = next(c for c in p6.corpus() if c['name'] == 'p6-raw-message-ff-3')
    result = []
    for informational in (False, True):
        for mode in ('json', 'text'):
            case = copy.deepcopy(template)
            name = f'p6-protocol-limit-{103 if informational else 200}-{mode}'
            old = case['dataset']
            case.update(name=name, dataset=name, group='protocol-limit', timeout=5)
            case['argv'] = ['tail', name, '-o', mode]
            fx = case['fixture']['phase6']
            fx['streams'][name] = fx['streams'].pop(old)
            acts = []
            if informational:
                acts.append(dict(act='headers', fields=[[':status', '103'],
                                                        ['content-type', 'application/grpc']]))
            acts += [dict(act='headers', fields=[[':status', '200'], ['content-type', 'application/grpc']]),
                     dict(act='schema'), dict(act='batch', rows=1),
                     dict(act='trailers', fields=[['grpc-status', '3'],
                                                 ['grpc-message', 'phase6 protocol limit terminal']])]
            fx['endpoints'][0]['subscriptions'] = [dict(acts=acts)]
            case['expected'].update(error_code='COMMAND_FAILED' if mode == 'json' else None,
                                    output_summary=False)
            case['protocol_limit'] = dict(informational=informational, mode=mode,
                expected={role: dict(stdout_b64=base64.b64encode(
                    b'' if informational and role == 'go' else p6.basic_output()).decode(),
                    stderr_b64=base64.b64encode(stderr_bytes(name, mode,
                        informational and role == 'go')).decode(),
                    grpc_status='Unknown' if informational and role == 'go' else 'InvalidArgument',
                    stage='DoGet_response_headers' if informational and role == 'go' else 'established_read',
                    rows=0 if informational and role == 'go' else 1,
                    batches=0 if informational and role == 'go' else 1)
                    for role in ('go', 'candidate')})
            result.append(case)
    return result


def assertions(case, result, role):
    """Independent byte, stage and process checks; exit 1 alone is insufficient."""
    view = copy.deepcopy(case)
    expected = case['protocol_limit']['expected'][role]
    stdout = base64.b64decode(expected['stdout_b64'])
    view['expected'].update(stdout_sha256=p6.run.digest(stdout), stdout_rows=expected['rows'],
                            stdout_batches=expected['batches'])
    issues = p6.expectation_issues(view, result)
    if role == 'candidate' and result.get('flight_user_agents') == [['tonic/0.14.6']]:
        issues = [i for i in issues if i != 'Flight user-agent differs from pinned grpc-go']
    if result['raw_wait_status'] != 256:
        issues.append('raw wait status must be ordinary exit 1')
    if result['stdout_bytes'] != len(stdout):
        issues.append('exact stdout byte count differs')
    if result['stderr_contract_b64'] != expected['stderr_b64']:
        issues.append('exact stderr/stage/status bytes differ')
    return issues


def outcome(case, result, role):
    expected = case['protocol_limit']['expected'][role]
    exact = (result['stderr_contract_b64'] == expected['stderr_b64'] and
             result['stdout_sha256'] == p6.run.digest(base64.b64decode(expected['stdout_b64'])))
    return dict(stage=expected['stage'] if exact else 'unexpected_outcome',
                grpc_status=expected['grpc_status'] if exact else 'unexpected_status',
                raw_wait_status=result['raw_wait_status'], termination=result['termination'],
                stdout_bytes=result['stdout_bytes'], stdout_sha256=result['stdout_sha256'],
                stdout_rows=result['stdout_rows'], stdout_batches=result['stdout_batches'],
                stderr_contract_b64=result['stderr_contract_b64'])


def classify(case, go, candidate):
    go_issues = assertions(case, go, 'go')
    candidate_issues = assertions(case, candidate, 'candidate')
    raw_differences = p6.differences(go, candidate)
    wire = []
    if go.get('flight_user_agents') == [['grpc-go/1.65.0']] and candidate.get('flight_user_agents') == [['tonic/0.14.6']]:
        wire = ['flight_user_agents differs'] if 'flight_user_agents differs' in raw_differences else []
    informational = case['protocol_limit']['informational']
    outcome_differences = [k + ' differs' for k in ('stage', 'grpc_status', 'stdout_rows', 'stdout_batches')
                           if outcome(case, go, 'go')[k] != outcome(case, candidate, 'candidate')[k]]
    expected = {'stdout_sha256 differs', 'stdout_bytes differs', 'stderr_contract_b64 differs'} if informational else set()
    remaining = [d for d in raw_differences if d not in wire]
    comparison_issues = []
    if set(remaining) != expected:
        comparison_issues.append('difference inventory differs from exact declared scenario')
    issues = go_issues + ['candidate: ' + i for i in candidate_issues] + comparison_issues
    return dict(go_issues=go_issues, candidate_issues=candidate_issues, comparison_issues=comparison_issues,
                issues=issues, raw_differences=raw_differences, outcome_differences=outcome_differences,
                wire_differences=wire, limitation_id=LIMIT_ID if informational else None,
                excluded_from_full_parity=informational,
                strict_parity_passed=not informational and not issues and not raw_differences,
                control_remaining_contract_passed=not informational and not issues,
                declared_limitation_verified=informational and not issues,
                go_outcome=outcome(case, go, 'go'), candidate_outcome=outcome(case, candidate, 'candidate'))


def verified_cached_go(entry, case):
    path = Path(entry['go_result_path'])
    if p6.run.digest(path.read_bytes()) != entry['go_result_sha256']:
        raise ValueError('cached Go result changed: ' + case['name'])
    result = json.loads(path.read_text())
    for stream in ('stdout', 'stderr'):
        if p6.run.digest((path.parent / (stream + '.bin')).read_bytes()) != result[stream + '_sha256']:
            raise ValueError('cached Go raw stream changed: ' + case['name'])
    if result['binary_sha256'] != p6.ORACLE_HASHES['standard'] or assertions(case, result, 'go'):
        raise ValueError('cached Go outcome is unpinned or invalid: ' + case['name'])
    return result, path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--go', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--go-evidence', help='Reuse validated Go captures from an earlier report')
    args = parser.parse_args(argv)
    try:
        inventory = cases()
        fixture = Path(args.fixture).resolve()
        binary = Path(args.candidate).resolve()
        oracle = Path(args.go).resolve()
        if p6.run.digest(fixture.read_bytes()) != FIXTURE_SHA256:
            raise ValueError('requires the immutable r5 fixture hash')
        if p6.run.digest(oracle.read_bytes()) != p6.ORACLE_HASHES['standard']:
            raise ValueError('requires pinned Go 07af390 standard binary')
        candidate_hash = p6.run.digest(binary.read_bytes())
        corpus_hash = p6.run.digest(p6.run.canonical(inventory))
        frozen = json.loads(Path(args.go_evidence).read_text()) if args.go_evidence else None
        if frozen and (not frozen.get('complete') or frozen.get('corpus_sha256') != corpus_hash or
                       frozen.get('fixture_sha256') != FIXTURE_SHA256 or
                       set(frozen.get('cases', {})) != {c['name'] for c in inventory}):
            raise ValueError('cached Go inventory/fixture is incomplete or changed')
        out = Path(args.out).resolve()
        if p6.TMP not in out.parents:
            raise ValueError('--out must be under the Phase6 cache tmp directory')
        out.mkdir(parents=True, exist_ok=False)
        report = dict(schema_version=1, oracle_revision='07af390', complete=False,
                      fixture_sha256=FIXTURE_SHA256, candidate_sha256=candidate_hash,
                      corpus_sha256=corpus_hash, full_parity_passed=False, cases={},
                      declared_limitation_cases=0, declared_limitation_verified=0,
                      excluded_from_full_parity=0, strict_parity_passed=0,
                      control_remaining_contract_passed=0, wire_ua_divergent=0, unaccepted_failures=0)
        for case in inventory:
            name = case['name']
            if frozen:
                go, go_path = verified_cached_go(frozen['cases'][name], case)
            else:
                go = p6.sample(oracle, fixture, case, out / name / 'go')
                go_path = out / name / 'go' / 'result.json'
            candidate = p6.sample(binary, fixture, case, out / name / 'candidate')
            entry = classify(case, go, candidate)
            if go['binary_sha256'] != p6.ORACLE_HASHES['standard']:
                entry['issues'].append('Go binary changed or differs from pinned oracle')
            if candidate['binary_sha256'] != candidate_hash or any(
                    r['fixture_sha256'] != FIXTURE_SHA256 for r in (go, candidate)):
                entry['issues'].append('candidate or fixture changed during execution')
            entry.update(go_result_path=str(go_path), go_result_sha256=p6.run.digest(go_path.read_bytes()),
                         candidate_result_path=str(out / name / 'candidate' / 'result.json'),
                         go_signature=p6.signature(go), candidate_signature=p6.signature(candidate))
            report['cases'][name] = entry
            report['declared_limitation_cases'] += bool(entry['limitation_id'])
            report['excluded_from_full_parity'] += entry['excluded_from_full_parity']
            for key in ('strict_parity_passed', 'control_remaining_contract_passed', 'declared_limitation_verified'):
                report[key] += bool(entry[key]) and not entry['issues']
            report['wire_ua_divergent'] += bool(entry['wire_differences'])
            report['unaccepted_failures'] += bool(entry['issues'])
            p6.run.write_json(out / 'report.json', report)
            print(name + ': ' + ('; '.join(entry['issues']) if entry['issues'] else
                  'DECLARED CAPABILITY LIMIT (excluded from parity)' if entry['limitation_id'] else
                  'CONTROL remaining contract PASS; explicit wire UA differs'), flush=True)
        report['complete'] = True
        p6.run.write_json(out / 'report.json', report)
        return int(bool(report['unaccepted_failures']))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print('Protocol limit evidence error: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
