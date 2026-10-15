#!/usr/bin/env python3
"""Exact HTTP/2-invalid response limitations, excluded from Flight parity.

This suite does not waive empty DATA loss or compression/retry mismatches.
"""
import argparse
import base64
import copy
import json
from pathlib import Path

import phase6 as p6
import phase6_protocol_limits as protocol

TERMINAL = 'phase6 h2 limit terminal'
LIMITS = {
    'content-length-mismatch': ('H2-CL-1', ['content-length', '1'],
        'rpc error: code = Internal desc = h2 protocol error: error reading a body from connection'),
    'connection-specific-header': ('H2-CL-2', ['connection', 'keep-alive'],
        'rpc error: code = Internal desc = h2 protocol error: http2 error'),
}


def stderr(name, mode, message, rendered):
    if mode == 'json':
        return (json.dumps({'error': {'code': 'COMMAND_FAILED', 'message': message,
                                     'retryable': False}}, indent=2) + '\n').encode()
    prefix = ('\r| connecting...\r\x1b[K\r\x1b[K● watching ' + name +
              '... (ctrl+c to stop)\r\x1b[K').encode()
    return prefix + (b'\r\x1b[K' if rendered else b'') + b'Error: ' + message.encode() + b'\n' + protocol.USAGE


def cases():
    templates = {c['protocol_limit']['mode']: c for c in protocol.cases()
                 if not c['protocol_limit']['informational']}
    result = []
    for kind in ('control', *LIMITS):
        for mode in ('json', 'text'):
            case = copy.deepcopy(templates[mode])
            old = case['dataset']
            name = 'p6-h2-limit-' + kind + '-' + mode
            case.update(name=name, dataset=name, group='h2-capability-limit')
            case['argv'] = ['tail', name, '-o', mode]
            fx = case['fixture']['phase6']
            fx['streams'][name] = fx['streams'].pop(old)
            acts = fx['endpoints'][0]['subscriptions'][0]['acts']
            acts[-1]['fields'][-1] = ['grpc-message', TERMINAL]
            if kind != 'control':
                acts[0]['fields'].append(LIMITS[kind][1])
            expected = {}
            for role in ('go', 'candidate'):
                rendered = role == 'go' or kind == 'control'
                message = ('rpc error: code = InvalidArgument desc = ' + TERMINAL
                           if rendered else LIMITS[kind][2])
                output = p6.basic_output() if rendered else b''
                expected[role] = dict(stdout_b64=base64.b64encode(output).decode(),
                    stderr_b64=base64.b64encode(stderr(name, mode, message, rendered)).decode(),
                    rows=int(rendered), batches=int(rendered),
                    stage='established_read' if rendered else 'before_first_message',
                    grpc_status='InvalidArgument' if rendered else 'Internal')
            case.pop('protocol_limit')
            case['h2_limit'] = dict(id=LIMITS[kind][0] if kind != 'control' else None,
                                   mode=mode, expected=expected)
            result.append(case)
    return result


def assertions(case, capture, role):
    expected = case['h2_limit']['expected'][role]
    view = copy.deepcopy(case)
    output = base64.b64decode(expected['stdout_b64'])
    view['expected'].update(stdout_sha256=p6.run.digest(output),
                            stdout_rows=expected['rows'], stdout_batches=expected['batches'])
    issues = p6.expectation_issues(view, capture)
    if capture['raw_wait_status'] != 256:
        issues.append('expected ordinary raw exit 1')
    if capture['stdout_bytes'] != len(output):
        issues.append('exact output byte count differs')
    if capture['stderr_contract_b64'] != expected['stderr_b64']:
        issues.append('exact error envelope/status/stage differs')
    return issues


def classify(case, go, candidate):
    errors = assertions(case, go, 'go')
    candidate_errors, differences, wire = p6.split_wire_ua(
        go, candidate, assertions(case, candidate, 'candidate'), p6.differences(go, candidate))
    errors += ['candidate: ' + item for item in candidate_errors]
    limit = case['h2_limit']['id']
    expected = {'stdout_sha256 differs', 'stdout_bytes differs', 'stderr_contract_b64 differs'} if limit else set()
    if set(differences) != expected:
        errors.append('difference inventory differs from exact declared scenario')
    return dict(issues=errors, limitation_id=limit, excluded_from_full_parity=bool(limit),
                raw_differences=p6.differences(go, candidate), wire_differences=wire,
                declared_limitation_verified=bool(limit) and not errors,
                control_remaining_contract_passed=not limit and not errors,
                expected_outcomes=case['h2_limit']['expected'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('go', 'candidate', 'fixture', 'out'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    paths = {name: Path(getattr(args, name)).resolve() for name in ('go', 'candidate', 'fixture')}
    identities = {name: p6.run.digest(path.read_bytes()) for name, path in paths.items()}
    if identities['go'] != p6.ORACLE_HASHES['standard'] or identities['fixture'] != protocol.FIXTURE_SHA256:
        raise ValueError('requires pinned Go and immutable r5 fixture')
    inventory = cases()
    sources = {str(path): p6.run.digest(path.read_bytes()) for path in
               (Path(__file__).resolve(), Path(p6.__file__).resolve(), Path(protocol.__file__).resolve())}
    out = Path(args.out).resolve()
    if p6.TMP not in out.parents:
        raise ValueError('output must be under phase6 disk cache tmp')
    out.mkdir(parents=True, exist_ok=False)
    report = dict(complete=False, full_parity_passed=False, identities=identities,
                  sources=sources, corpus_sha256=p6.run.digest(p6.run.canonical(inventory)),
                  cases={}, failures=0, control_remaining_contract_passed=0,
                  declared_limitation_verified=0, excluded_from_full_parity=0)
    try:
        for case in inventory:
            name = case['name']
            captures = {}
            for role in ('go', 'go_repeat', 'candidate', 'candidate_repeat'):
                binary_role = 'go' if role.startswith('go') else 'candidate'
                captures[role] = p6.sample(paths[binary_role], paths['fixture'], case, out / name / role)
            entry = classify(case, captures['go'], captures['candidate'])
            for role in ('go', 'candidate'):
                entry['issues'] += [role + ' A/A: ' + item for item in
                                    p6.differences(captures[role], captures[role + '_repeat'])]
            entry['captures'] = {}
            for role, capture in captures.items():
                result_path = out / name / role / 'result.json'
                entry['captures'][role] = dict(path=str(result_path), sha256=p6.run.digest(result_path.read_bytes()))
                binary_role = 'go' if role.startswith('go') else 'candidate'
                if capture['binary_sha256'] != identities[binary_role] or capture['fixture_sha256'] != identities['fixture']:
                    entry['issues'].append(role + ': executable changed')
                # The repeated capture must independently meet its role contract too.
                if role.endswith('_repeat'):
                    issues = assertions(case, capture, binary_role)
                    if binary_role == 'candidate':
                        issues, _, _ = p6.split_wire_ua(captures['go'], capture, issues, [])
                    entry['issues'] += [role + ': ' + item for item in issues]
            entry['declared_limitation_verified'] = bool(entry['limitation_id']) and not entry['issues']
            entry['control_remaining_contract_passed'] = not entry['limitation_id'] and not entry['issues']
            report['cases'][name] = entry
            report['failures'] += bool(entry['issues'])
            for key in ('control_remaining_contract_passed', 'declared_limitation_verified'):
                report[key] += bool(entry[key]) and not entry['issues']
            report['excluded_from_full_parity'] += entry['excluded_from_full_parity']
            p6.run.write_json(out / 'report.json', report)
            print(name, entry['issues'] or ('DECLARED LIMIT (excluded)' if entry['limitation_id'] else 'CONTROL PASS'), flush=True)
        report['identities_unchanged'] = (
            all(p6.run.digest(path.read_bytes()) == identities[name] for name, path in paths.items()) and
            all(p6.run.digest(Path(path).read_bytes()) == digest for path, digest in sources.items()))
        report['complete'] = True
        return int(bool(report['failures']) or not report['identities_unchanged'])
    finally:
        p6.run.write_json(out / 'report.json', report)


if __name__ == '__main__':
    raise SystemExit(main())
