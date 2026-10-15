#!/usr/bin/env python3
"""Replay the durable IA1 IPC corpus through the maintained raw Flight fixture.

This adapter parses only the envelope of hash-verified, repository-owned IPC
streams. It does not decode Arrow values or supply their expected output.
"""

import argparse
import base64
import copy
import json
from pathlib import Path
import struct

import phase6 as p6
from phase6_protocol_limits import FIXTURE_SHA256, USAGE

CORPUS = Path(__file__).resolve().parents[1] / 'tests/fixtures/arrow-go-v13-decoder-ia1'
WAVE_CORPUS = CORPUS.with_name('arrow-go-v13-decoder-wave')
VALUES_WAVE_CORPUS = CORPUS.with_name('arrow-go-v13-values-wave')
TERMINAL = 'phase6 dictionary corpus terminal'


class VerifiedCases(list):
    """Case list retaining the bytes and identities admitted by verification."""

    def __init__(self, items, corpus, verified_bytes):
        super().__init__(items)
        self.verified_bytes = verified_bytes
        self.sources = {str(corpus / name): p6.run.digest(data)
                        for name, data in verified_bytes.items()}


def varint(value):
    if value < 0:
        raise ValueError('negative protobuf varint')
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def flight_frames(stream):
    """Keep each IPC metadata/body byte; replace stream envelopes with FlightData."""
    cursor = 0
    frames = []

    def take(length):
        nonlocal cursor
        if length < 0 or cursor + length > len(stream):
            raise ValueError('truncated IPC envelope')
        value = stream[cursor:cursor + length]
        cursor += length
        return value

    while cursor < len(stream):
        length, = struct.unpack('<I', take(4))
        if length == 0xffffffff:
            length, = struct.unpack('<I', take(4))
        if not length:
            if cursor != len(stream):
                raise ValueError('bytes after IPC stream end')
            break
        header = take(length)
        # Flatbuffer Message.bodyLength is slot 3; absent means zero.
        try:
            root, = struct.unpack_from('<I', header)
            offset, = struct.unpack_from('<i', header, root)
            table = root - offset
            if table < 0:
                raise ValueError('invalid IPC vtable')
            vlength, = struct.unpack_from('<H', header, table)
            field = struct.unpack_from('<H', header, table + 10)[0] if vlength >= 12 else 0
            body_length = struct.unpack_from('<q', header, root + field)[0] if field else 0
        except struct.error as error:
            raise ValueError('invalid IPC message envelope') from error
        body = take(body_length)
        # Arrow stream metadata/body lengths include their alignment padding.
        payload = b'\x12' + varint(len(header)) + header
        if body:
            payload += varint((1000 << 3) | 2) + varint(len(body)) + body
        frames.append(b'\0' + len(payload).to_bytes(4, 'big') + payload)
    if not frames:
        raise ValueError('empty IPC stream')
    return frames


def cases(corpus=None, count=25, mode='json', values=False):
    if mode not in ('json', 'text'):
        raise ValueError('unsupported output mode')
    corpus = (corpus or CORPUS).resolve()
    manifest_bytes = (corpus / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    verified_bytes = {'manifest.json': manifest_bytes}
    for item in manifest['files']:
        if item['name'] in verified_bytes:
            raise ValueError('duplicate corpus artifact: ' + item['name'])
        data = (corpus / item['name']).read_bytes()
        if len(data) != item['bytes'] or p6.run.digest(data) != item['sha256']:
            raise ValueError('IA1 corpus artifact changed: ' + item['name'])
        verified_bytes[item['name']] = data
    if len(manifest['cases']) != count or len(set(manifest['cases'])) != count:
        raise ValueError(f'dictionary inventory must contain {count} unique cases')
    template = next(c for c in p6.corpus() if c['name'] == 'p6-raw-message-ff-3')
    result = []
    for name in manifest['cases']:
        case = copy.deepcopy(template)
        dataset = ('p6-values-' if values else 'p6-dictionary-') + name.replace('_', '-')
        if mode == 'text':
            dataset += '-text'
        old = case['dataset']
        case.update(name=dataset, dataset=dataset, group='dictionary-corpus', timeout=5)
        case['argv'] = ['tail', dataset, '-o', mode]
        fx = case['fixture']['phase6']
        fx['streams'][dataset] = fx['streams'].pop(old)
        frames = flight_frames(verified_bytes[name + '.arrows'])
        acts = [dict(act='headers', fields=[[':status', '200'], ['content-type', 'application/grpc']])]
        acts += [dict(act='data', b64=base64.b64encode(frame).decode()) for frame in frames]
        acts.append(dict(act='trailers', fields=[['grpc-status', '3'], ['grpc-message', TERMINAL]]))
        fx['endpoints'][0]['subscriptions'] = [dict(acts=acts)]
        if values:
            if verified_bytes[name + '.panic'] or verified_bytes[name + '.errors']:
                raise ValueError('value wave requires a successful Go IPC stream')
            stdout = verified_bytes[name + '.expected']
            terminal = b'read end: EOF'
        else:
            stdout = verified_bytes[name + '.go.stdout']
            terminal = verified_bytes[name + '.go.stderr'].splitlines()[-1]
        case['expected'].update(output_summary=False, stdout_sha256=p6.run.digest(stdout),
                                error_code='COMMAND_FAILED' if mode == 'json' else None,
                                stdout_rows=sum(bool(line) for line in stdout.splitlines()),
                                stdout_batches=sum(not line for line in stdout.splitlines()))
        case['dictionary_oracle'] = dict(case=name, manifest_sha256=p6.run.digest(manifest_bytes),
            stdout_bytes=len(stdout), terminal=terminal.decode(), flight_messages=len(frames), mode=mode)
        result.append(case)
    return VerifiedCases(result, corpus, verified_bytes)


def assertions(case, capture):
    issues = p6.expectation_issues(case, capture)
    if capture['raw_wait_status'] != 256:
        issues.append('expected raw ordinary exit 1')
    if capture['stdout_bytes'] != case['dictionary_oracle']['stdout_bytes']:
        issues.append('stdout byte count differs from durable Go oracle')
    # Successful IPC EOF is replaced only by the explicit final RPC status.
    terminal = case['dictionary_oracle']['terminal']
    if terminal == 'read end: EOF':
        message = 'rpc error: code = InvalidArgument desc = ' + TERMINAL
    elif terminal.startswith('read end: '):
        message = terminal.removeprefix('read end: ')
    else:
        issues.append('unrecognized durable Go terminal')
        return issues
    stderr = base64.b64decode(capture['stderr_contract_b64'])
    if case['dictionary_oracle'].get('mode', 'json') == 'json':
        expected = (json.dumps({'error': {'code': 'COMMAND_FAILED', 'message': message,
                                         'retryable': False}}, indent=2) + '\n').encode()
        if stderr != expected:
            issues.append('exact JSON error envelope/stage differs from durable Go oracle')
    elif not stderr.endswith(b'Error: ' + message.encode() + b'\n' + USAGE):
        issues.append('text fatal status/stage/usage differs from durable Go oracle')
    return issues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('go', 'candidate', 'fixture', 'out'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--corpus', choices=('ia1', 'wave', 'values-wave'), default='ia1')
    parser.add_argument('--output-mode', choices=('json', 'text'), default='json')
    args = parser.parse_args(argv)
    corpus, count = {'ia1': (CORPUS, 25), 'wave': (WAVE_CORPUS, 11),
                     'values-wave': (VALUES_WAVE_CORPUS, 9)}[args.corpus]
    inventory = cases(corpus, count, args.output_mode, args.corpus == 'values-wave')
    inputs = {name: Path(getattr(args, name)).resolve() for name in ('go', 'candidate', 'fixture')}
    identities = {name: p6.run.digest(path.read_bytes()) for name, path in inputs.items()}
    if identities['go'] != p6.ORACLE_HASHES['standard'] or identities['fixture'] != FIXTURE_SHA256:
        raise ValueError('requires pinned Go binary and immutable r5 fixture')
    source_paths = [Path(__file__).resolve(), Path(p6.__file__).resolve()]
    sources = {str(path): p6.run.digest(path.read_bytes()) for path in source_paths}
    sources.update(inventory.sources)
    out = Path(args.out).resolve()
    if p6.TMP not in out.parents:
        raise ValueError('output must be in phase6 disk cache tmp')
    out.mkdir(parents=True, exist_ok=False)
    report = dict(complete=False, scope=f'{count} {args.corpus} {args.output_mode} native Flight process regressions; no timing claims',
                  identities=identities, sources=sources, cases={}, failures=0,
                  strict_parity_passed=0, remaining_contract_passed=0, wire_ua_divergent=0)
    try:
        for case in inventory:
            name = case['name']
            captures = {}
            issues = []
            for role in ('go', 'go_repeat', 'candidate'):
                binary = inputs['candidate' if role == 'candidate' else 'go']
                capture = p6.sample(binary, inputs['fixture'], case, out / name / role)
                captures[role] = capture
                found = assertions(case, capture)
                if role == 'candidate':
                    found, delta, wire = p6.split_wire_ua(captures['go'], capture, found,
                                                         p6.differences(captures['go'], capture))
                    found += delta
                issues += [role + ': ' + item for item in found]
            issues += ['Go A/A: ' + item for item in p6.differences(captures['go'], captures['go_repeat'])]
            entry = dict(issues=issues, wire_differences=wire,
                         raw_differences=p6.differences(captures['go'], captures['candidate']),
                         oracle=case['dictionary_oracle'], captures={})
            for role, capture in captures.items():
                result_path = out / name / role / 'result.json'
                entry['captures'][role] = dict(path=str(result_path), sha256=p6.run.digest(result_path.read_bytes()),
                                              signature=p6.signature(capture))
                expected_binary = identities['candidate' if role == 'candidate' else 'go']
                if capture['binary_sha256'] != expected_binary or capture['fixture_sha256'] != identities['fixture']:
                    issues.append(role + ': executable identity changed')
            report['cases'][name] = entry
            report['failures'] += bool(issues)
            report['remaining_contract_passed'] += not issues
            report['strict_parity_passed'] += not issues and not entry['raw_differences']
            report['wire_ua_divergent'] += bool(wire)
            p6.run.write_json(out / 'report.json', report)
            print(name, 'FAIL ' + '; '.join(issues) if issues else 'remaining contract PASS', flush=True)
        report['end_identities'] = {name: p6.run.digest(path.read_bytes()) for name, path in inputs.items()}
        report['end_sources'] = {path: p6.run.digest(Path(path).read_bytes()) for path in sources}
        report['identities_unchanged'] = report['end_identities'] == identities and report['end_sources'] == sources
        report['complete'] = True
        report['passed'] = not report['failures'] and report['identities_unchanged']
        return int(not report['passed'])
    finally:
        p6.run.write_json(out / 'report.json', report)


if __name__ == '__main__':
    raise SystemExit(main())
