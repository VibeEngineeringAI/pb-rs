#!/usr/bin/env python3
"""Separate causal writer/authority followups; historical291 stays unchanged."""

import argparse
import base64
import copy
import json
from pathlib import Path
import signal
import sys

import phase6 as p6

FIXTURE_SHA = 'c85f63f9f01a1be6eaa66e6802dae0c3f08409be2ad538d358a7e70f2f5ce314'
CAPACITY = 4096


def historical_diagnostics(case, reference, candidate, delta):
    """The malformed-header diagnostic retained historical captures carry.

    Live comparisons no longer waive it (docs/pr7-remaining-differences.md);
    this exact pair only lets historical291 re-validate unchanged.
    """
    if case['name'] != 'p6-corrupt-garbage_header':
        return delta, []
    prefix = 'arrow/flight: could not create flight reader: arrow/ipc: unknown error while reading: '
    messages = [prefix + 'runtime error: slice bounds out of range [4022250974:12]',
                prefix + 'Type `i32` at position 4022250974 is unaligned.\n\n']
    try:
        for result, message in zip((reference, candidate), messages):
            expected = (json.dumps({'error': {'code': 'COMMAND_FAILED', 'message': message,
                                            'retryable': False}}, indent=2) + '\n').encode()
            if base64.b64decode(result['stderr_contract_b64'], validate=True) != expected:
                return delta, []
    except (ValueError, KeyError):
        return delta, []
    return ([i for i in delta if i != 'stderr_contract_b64 differs'],
            ['P6-DIAG-GARBAGE-HEADER: ' + i for i in delta if i == 'stderr_contract_b64 differs'])


def wide_output(payload, seed=7):
    """Independent Go uint64 xorshift/payload and sorted JSON model."""
    mask = (1 << 64) - 1
    alphabet = b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_'
    x = (seed * 0x9e3779b97f4a7c15 + 1) & mask
    data = bytearray()
    for _ in range(payload):
        x ^= (x << 13) & mask
        x ^= x >> 7
        x ^= (x << 17) & mask
        data.append(alphabet[x % len(alphabet)])
    return b'{"payload":"' + data + b'","sequence":0}\n\n'


def cases():
    template = next(c for c in p6.corpus() if c['name'] == 'p6-auth-basic')
    result = []
    for kind in ('below-capacity', 'http2-progress', 'signal-int', 'signal-term'):
        c = copy.deepcopy(template)
        old = c['dataset']
        name = 'p6-causal-writer-' + kind
        c.update(name=name, dataset=name, group='causal-writer', pause_stdout=True, timeout=8)
        c['argv'] = ['tail', name, '-o', 'json']
        payload = 128 if kind == 'below-capacity' else 16384
        output = wide_output(payload)
        fx = c['fixture']['phase6']
        fx['streams'].pop(old)
        fx['streams'][name] = dict(schema='wide', seed=7, subscriptions=[dict(ops=[
            dict(op='batch', rows=1, payload_bytes=payload), dict(op='gate', name='writer'),
            dict(op='status', code=3, message='phase6 causal writer terminal')])])
        c['pipe_probe'] = dict(capacity=CAPACITY, expected_output_bytes=len(output), full_ms=100)
        if kind == 'below-capacity':
            c['actions'] = [dict(when=dict(pipe_pending_bytes=len(output)), do=dict(assert_not_blocked=True))]
        else:
            c['actions'] = [dict(when=dict(pipe_blocked=True), do=dict(assert_pipe_blocked=True))]
        if kind == 'http2-progress':
            fx['keepalive'] = dict(time_ms=1000, timeout_ms=300)
            c['actions'] += [dict(when=dict(after_ms=1800), do=dict(assert_pipe_blocked=True)),
                             dict(when=dict(start=True), do=dict(assert_no_conn_end=True))]
        if kind.startswith('signal-'):
            sig = 'INT' if kind == 'signal-int' else 'TERM'
            c['actions'].append(dict(when=dict(start=True), do=dict(signal=sig)))
            output = output[:CAPACITY]
            c['expected'].update(termination=dict(signal=getattr(signal, 'SIG' + sig)),
                                  error_code=None, output_summary=False, stdout_rows=0, stdout_batches=0)
        else:
            c['actions'] += [dict(when=dict(start=True), do=dict(resume_stdout=True)),
                             dict(when=dict(start=True), do=dict(release='writer'))]
            c['expected'].update(error_code='COMMAND_FAILED', output_summary=True,
                                  stdout_rows=1, stdout_batches=1)
        c['expected']['stdout_sha256'] = p6.run.digest(output)
        c['writer_output_b64'] = base64.b64encode(output).decode()
        result.append(c)
    return result


def issues(case, result):
    found = p6.expectation_issues(case, result)
    probe = result.get('pipe_probe', {})
    expected = base64.b64decode(case['writer_output_b64'])
    if result['stdout_bytes'] != len(expected):
        found.append('exact writer output byte count differs')
    if probe.get('capacity') != CAPACITY:
        found.append('controlled pipe capacity differs')
    if case['name'].endswith('below-capacity'):
        if probe.get('blocked_proven') or not probe.get('negative_control_rejected'):
            found.append('below-capacity control did not reject blocked-writer interpretation')
    else:
        proof = probe.get('proof') or {}
        if (not probe.get('blocked_proven') or probe.get('expected_output_bytes', 0) <= CAPACITY or
                proof.get('pending_bytes') != CAPACITY or proof.get('drained_bytes') != 0 or
                not proof.get('pipe_write_tids') or
                proof.get('t_ns', 0) - proof.get('full_since_ns', 0) < 100000000):
            found.append('causal blocked-pipe/writer proof absent')
        if any(a.get('t_ns', 0) < proof.get('t_ns', 0) for a in result['actions'] if a.get('met')):
            found.append('action preceded client blocked-writer proof')
        if case['name'].endswith('http2-progress') and (
                result['actions'][1].get('t_ns', 0) - result['actions'][0].get('t_ns', 0) < 1800000000):
            found.append('keepalive hold did not start after the blocked-writer boundary')
    if case['expected']['termination'].get('signal'):
        stderr = b''
        raw = case['expected']['termination']['signal']
    else:
        stderr = (json.dumps({'error': {'code': 'COMMAND_FAILED',
            'message': 'rpc error: code = InvalidArgument desc = phase6 causal writer terminal',
            'retryable': False}}, indent=2) + '\n').encode()
        raw = 256
    if result['raw_wait_status'] != raw or base64.b64decode(result['stderr_contract_b64']) != stderr:
        found.append('exact raw termination or stderr differs')
    return found


def reduce_saved(root, out):
    """Recheck CW2, with honest missing independent-port provenance in old data."""
    suites = {'main': p6.corpus(), 'review3-followup': p6.review3_followups(),
              'trailer-followup': p6.trailer_followups()}
    report = dict(cases=0, unaccepted_failures=0, cw2_remaining_contract_passed=0,
                  cw3_independent_authority_validated=0, historical_reports={}, entries={},
                  authority_limit='Archived captures have no banner/discovery-port provenance; old normalized signatures are retained, not newly certified.',
                  full_parity_passed=False)
    for suite, inventory in suites.items():
        old_path = root / suite / 'report.json'
        report['historical_reports'][str(old_path)] = p6.run.digest(old_path.read_bytes())
        for case in inventory:
            name = case['name']
            go = json.loads((root / suite / name / 'go/result.json').read_text())
            rust = json.loads((root / suite / name / 'candidate/repeat/result.json').read_text())
            first = p6.expectation_issues(case, go)
            second = p6.expectation_issues(case, rust)
            delta = p6.differences(go, rust)
            second, delta, wire = p6.split_wire_ua(go, rust, second, delta)
            delta, diagnostic = historical_diagnostics(case, go, rust, delta)
            errors = first + second + delta
            report['entries'][name] = dict(issues=errors, wire=wire, diagnostic=diagnostic,
                independent_selected_port_provenance=False)
            report['cases'] += 1
            report['unaccepted_failures'] += bool(errors)
            report['cw2_remaining_contract_passed'] += not errors
    p6.run.write_json(out / 'saved-reduction.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--go', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--reduce-saved')
    args = parser.parse_args(argv)
    try:
        fixture = Path(args.fixture).resolve()
        go_binary = Path(args.go).resolve()
        candidate = Path(args.candidate).resolve()
        if p6.run.digest(fixture.read_bytes()) != FIXTURE_SHA or p6.run.digest(go_binary.read_bytes()) != p6.ORACLE_HASHES['standard']:
            raise ValueError('requires immutable fixture-r5 and pinned Go')
        candidate_hash = p6.run.digest(candidate.read_bytes())
        out = Path(args.out).resolve()
        if p6.TMP not in out.parents:
            raise ValueError('output must stay under Phase6 cache tmp')
        out.mkdir(parents=True, exist_ok=False)
        if args.reduce_saved:
            reduce_saved(Path(args.reduce_saved), out)
        report = dict(complete=False, failed=0, remaining_contract_passed=0, strict_parity_passed=0,
            wire_ua_divergent=0, cases={}, fixture_sha256=FIXTURE_SHA, candidate_sha256=candidate_hash,
            phase6_source_sha256=p6.run.digest(Path(p6.__file__).read_bytes()))
        controls = [next(c for c in p6.corpus() if c['name'] == name)
                    for name in ('p6-auth-basic', 'p6-raw-message-ff-3')]
        for case in cases() + controls:
            name = case['name']
            go = p6.sample(go_binary, fixture, case, out / name / 'go')
            rust = p6.sample(candidate, fixture, case, out / name / 'candidate')
            check = issues if case.get('pipe_probe') else p6.expectation_issues
            first, second = check(case, go), check(case, rust)
            delta = p6.differences(go, rust)
            second, delta, wire = p6.split_wire_ua(go, rust, second, delta)
            errors = first + second + delta
            for r in (go, rust):
                if r['fixture_sha256'] != FIXTURE_SHA:
                    errors.append('fixture changed during captures')
            if rust['binary_sha256'] != candidate_hash or go['binary_sha256'] != p6.ORACLE_HASHES['standard']:
                errors.append('candidate or Go executable changed')
            entry = dict(issues=errors, wire=wire, go_signature=p6.signature(go),
                         candidate_signature=p6.signature(rust),
                         go_pipe_probe=go.get('pipe_probe'), candidate_pipe_probe=rust.get('pipe_probe'),
                         go_discovered_port=go['discovered_flight_port'],
                         candidate_discovered_port=rust['discovered_flight_port'])
            if not case.get('pipe_probe'):
                rejected = []
                for port in (1, rust['fixture_info']['grpc_port'] if name.startswith('p6-raw') else 65534):
                    if port == rust['discovered_flight_port']:
                        continue
                    mutated = copy.deepcopy(rust)
                    event = next(e for e in mutated['events'] if p6.is_doget(e))
                    event['metadata'][':authority'] = ['127.0.0.1:' + str(port)]
                    mutated['flight_authorities'] = [p6.authority_contract(e, rust['discovered_flight_port'])
                                                    for e in mutated['events'] if p6.is_doget(e)]
                    rejected.append('flight_authorities differs' in p6.differences(go, mutated))
                entry['wrong_ports_rejected'] = rejected
                if not rejected or not all(rejected):
                    errors.append('incorrect selected-port mutation was accepted')
            report['cases'][name] = entry
            report['failed'] += bool(errors)
            report['remaining_contract_passed'] += not errors
            report['strict_parity_passed'] += not errors and not wire
            report['wire_ua_divergent'] += bool(wire)
            p6.run.write_json(out / 'report.json', report)
            print(name + ': ' + ('; '.join(errors) if errors else 'remaining contract PASS; exact native-UA ledger'), flush=True)
        report['complete'] = True
        p6.run.write_json(out / 'report.json', report)
        return int(bool(report['failed']))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print('Causal writer harness error: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
