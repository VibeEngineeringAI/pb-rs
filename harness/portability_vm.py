#!/usr/bin/env python3
"""Runtime portability checks for frozen release binaries, including QEMU CPUs.

Reuse the checked-in offline corpus and independent interactive-effect fixture.
Emulated runs are functional checks, never native performance measurements.
"""
import argparse
import concurrent.futures
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import shlex
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.parse

import phase1

ROOT = Path(__file__).resolve().parent.parent
CPU_MODELS = ('qemu64', 'Nehalem', 'Haswell-noTSX')
START, END = '2024-01-01T00:00:00Z', '2024-01-01T00:01:00Z'
SQL_QUERY = 'SELECT * FROM fixture LIMIT 1'
SQL_RESPONSE = [{'value': 1}]
PROMQL_RESPONSE = {'status': 'success', 'data': {'resultType': 'matrix', 'result': [
    {'metric': {'__name__': 'up'}, 'values': [[1704067200, '1']]}]}}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def file_identity(path):
    return {'path': str(path), 'sha256': sha(path)}


def wrapper_text(binary, emulator, model):
    return ('#!/bin/sh\nexec ' + shlex.quote(str(emulator)) + ' -cpu ' + shlex.quote(model)
            + ' ' + shlex.quote(str(binary)) + ' "$@"\n')


def capture_identity(binary):
    """A wrapper's identity includes its backing ELF and emulator, not just sh."""
    identity = file_identity(binary)
    with binary.open('rb') as source:
        if source.read(4) == b'\x7fELF':
            return identity
    manifest_path = binary.with_suffix('.provenance.json')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema') != 1 or manifest.get('model') not in CPU_MODELS:
        raise ValueError('unsupported wrapper provenance manifest')
    backing = Path(manifest['backing_binary']['path'])
    emulator = Path(manifest['emulator']['path'])
    for path, key in ((backing, 'backing_binary'), (emulator, 'emulator')):
        if not path.is_absolute() or file_identity(path) != manifest[key]:
            raise RuntimeError(f'wrapper {key} changed since wrapper creation')
        with path.open('rb') as source:
            if source.read(4) != b'\x7fELF':
                raise ValueError(f'wrapper {key} must be an ELF executable')
    if binary.read_text() != wrapper_text(backing, emulator, manifest['model']):
        raise ValueError('wrapper does not match its execution manifest')
    identity.update(backing_binary=manifest['backing_binary'], emulator=manifest['emulator'],
                    cpu_model=manifest['model'], manifest=file_identity(manifest_path))
    return identity


def effects(binary, output, jobs, check=lambda: None):
    source = (ROOT / 'tests/interactive_effects.rs').read_text()
    fixture = source.split('const PROCESS_FIXTURE: &str = r####"', 1)[1].rsplit('"####;', 1)[0]
    cases = [s.strip() for s in re.search(r'process_cases!\((.*?)\);', source, re.S)[1].split(',') if s.strip()]
    if not cases:
        raise ValueError('empty effects case inventory')
    fixture_path = output / 'fixture.py'
    fixture_path.write_text(fixture)
    save(output / 'definition.json', {'source_sha256': sha(ROOT / 'tests/interactive_effects.rs'), 'cases': cases})

    def run(case):
        check()
        env = dict(os.environ, PB_EFFECTS_CAPTURE_DIR=str(output / 'captures'))
        proc = subprocess.run([sys.executable, '-I', str(ROOT / 'harness/effects_supervisor.py'),
                               '--timeout', '150', '--parent', str(os.getpid()), '--',
                               sys.executable, '-I', str(fixture_path), str(binary), case],
                              capture_output=True, env=env)
        check()
        (output / (case + '.stdout')).write_bytes(proc.stdout)
        (output / (case + '.stderr')).write_bytes(proc.stderr)
        return {'case': case, 'returncode': proc.returncode, 'passed': proc.returncode == 0}

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        rows = list(pool.map(run, cases))
    return {'kind': 'existing independent process/PTY effects', 'cases': rows,
            'passed': sum(row['passed'] for row in rows), 'total': len(rows)}


def offline(reference, binary, output, check=lambda: None):
    rows = []
    for case in phase1.corpus():
        check()
        before = phase1.sample(reference, case['argv'], 10)
        check()
        after = phase1.sample(binary, case['argv'], 10)
        check()
        issues = phase1.differences(before, after)
        phase1.store_result(output / case['name'] / 'reference', before)
        phase1.store_result(output / case['name'] / 'candidate', after)
        rows.append({'name': case['name'], 'argv': case['argv'], 'issues': issues})
    return {'kind': 'offline runtime byte parity; not historical phase-one admission',
            'cases': rows, 'passed': sum(not row['issues'] for row in rows), 'total': len(rows)}


def wrappers(binary, output, check=lambda: None):
    rows = []
    emulator = Path('/usr/bin/qemu-x86_64').resolve(strict=True)
    for model in CPU_MODELS:
        check()
        path = output / ('pb-' + model)
        path.write_text(wrapper_text(binary, emulator, model))
        path.chmod(0o755)
        save(path.with_suffix('.provenance.json'), {
            'schema': 1, 'model': model, 'backing_binary': file_identity(binary),
            'emulator': file_identity(emulator)})
        wrapped_identity = capture_identity(path)
        proc = subprocess.run([str(path), 'version'], capture_output=True, timeout=20)
        check()
        if capture_identity(path) != wrapped_identity:
            raise RuntimeError('wrapper execution provenance changed during version check')
        rows.append({'model': model, 'wrapper': str(path), 'wrapper_sha256': sha(path),
                     'execution_identity': wrapped_identity,
                     'returncode': proc.returncode, 'stdout': proc.stdout.decode(errors='replace'),
                     'stderr': proc.stderr.decode(errors='replace'),
                     'passed': proc.returncode == 0 and proc.stdout == b'pb version 0.1.0\n' and not proc.stderr})
    return {'kind': 'QEMU user-mode CPU emulation; functional only', 'models': rows,
            'passed': sum(row['passed'] for row in rows), 'total': len(rows)}


def valid_request(method, path, headers, body):
    endpoint = urllib.parse.urlsplit(path)
    if headers.get('x-api-key') != 'fixture-key':
        return False
    if method == 'POST' and endpoint.path == '/api/v1/query' and not endpoint.query:
        try:
            return (headers.get('content-type', '').split(';')[0] == 'application/json'
                    and json.loads(body) == {'query': SQL_QUERY, 'startTime': START, 'endTime': END})
        except ValueError:
            return False
    return (method == 'GET' and endpoint.path == '/prometheus/api/v1/query_range' and not body
            and urllib.parse.parse_qs(endpoint.query, keep_blank_values=True) == {
                'query': ['up'], 'stream': ['metrics'], 'start': [START], 'end': [END], 'step': ['15s']})


def equal_json(actual, expected):
    # Python considers True == 1. JSON booleans must never substitute for a
    # numeric sample, while equivalent integer/float spellings are acceptable.
    if type(actual) is not type(expected):
        return type(actual) in (int, float) and type(expected) in (int, float) and actual == expected
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(equal_json(actual[key], value)
                                                       for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(equal_json(a, b) for a, b in zip(actual, expected))
    return actual == expected


def valid_tls_result(name, stdout, stderr, returncode, observed, version):
    try:
        body = json.loads(stdout)
    except ValueError:
        return False
    method = 'POST' if name == 'sql' else 'GET'
    return (returncode == 0 and not stderr
            and equal_json(body, SQL_RESPONSE if name == 'sql' else PROMQL_RESPONSE)
            and len(observed) == 1 and observed[0]['method'] == method
            and observed[0]['tls'] == version and observed[0]['valid_request'])


def tls(binary, output, check=lambda: None):
    certs = ROOT / 'tests/fixtures/tls'
    ca, cert, key = (output / name for name in ('ca.pem', 'server.pem', 'server-key.pem'))
    ca.write_text(ssl.DER_cert_to_PEM_cert((certs / 'ca.der').read_bytes()))
    cert.write_text(ssl.DER_cert_to_PEM_cert((certs / 'server.der').read_bytes()))
    subprocess.run(['openssl', 'pkey', '-inform', 'DER', '-in', str(certs / 'server-key.der'),
                    '-out', str(key)], check=True, capture_output=True, timeout=10)
    observations, rows = [], []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.reply(SQL_RESPONSE, self.rfile.read(int(self.headers.get('Content-Length', 0))))

        def do_GET(self):
            self.reply(PROMQL_RESPONSE, b'')

        def reply(self, body, request_body):
            headers = {key.lower(): value for key, value in self.headers.items()}
            valid = valid_request(self.command, self.path, headers, request_body)
            observations.append({'path': self.path, 'tls': self.connection.version(),
                                 'cipher': self.connection.cipher(), 'method': self.command,
                                 'headers': headers, 'body': request_body.decode(errors='replace'),
                                 'valid_request': valid})
            body = json.dumps(body if valid else {'error': 'unexpected fixture request'}).encode()
            self.send_response(200 if valid else 400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    for label, version in [('TLSv1.2', ssl.TLSVersion.TLSv1_2), ('TLSv1.3', ssl.TLSVersion.TLSv1_3)]:
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ctx.maximum_version = version
        ctx.set_ciphers('ECDHE-RSA-AES128-GCM-SHA256')
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory(prefix='pb-portability-tls-') as directory:
                home = Path(directory)
                (home / 'config/pb').mkdir(parents=True)
                (home / 'config/pb/config.toml').write_text(
                    "DefaultProfile='fixture'\n[Profiles.fixture]\n"
                    f"url='https://localhost:{server.server_port}'\napiKey='fixture-key'\n")
                env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                           SSL_CERT_FILE=str(ca), PB_ANALYTICS='disable', TERM='dumb', NO_COLOR='1')
                for name, argv in [
                    ('sql', ['sql', 'run', SQL_QUERY, f'--from={START}', f'--to={END}', '-o', 'json']),
                    ('promql', ['promql', 'run', 'up', '--dataset', 'metrics', f'--from={START}',
                                f'--to={END}', '--step=15s', '-o', 'json']),
                ]:
                    check()
                    before = len(observations)
                    proc = subprocess.run([str(binary), *argv], env=env, capture_output=True, timeout=30)
                    check()
                    (output / f'{label}-{name}.stdout').write_bytes(proc.stdout)
                    (output / f'{label}-{name}.stderr').write_bytes(proc.stderr)
                    rows.append({'tls': label, 'command': name, 'returncode': proc.returncode,
                                 'observations': observations[before:],
                                 'passed': valid_tls_result(name, proc.stdout, proc.stderr,
                                     proc.returncode, observations[before:], label)})
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
    return {'kind': 'real TLS loopback requests using the test-only CA', 'cases': rows,
            'passed': sum(row['passed'] for row in rows), 'total': len(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('effects', 'offline', 'wrappers', 'tls'))
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 4:
        parser.error('jobs must be 1..4')
    binary, output = args.binary.resolve(), args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'runner_sha256': sha(__file__),
              'supervisor_sha256': sha(ROOT / 'harness/effects_supervisor.py')}
    save(output / 'report.json', report)
    try:
        identity = capture_identity(binary)
        reference = args.reference.resolve() if args.reference else None
        reference_identity = capture_identity(reference) if reference else None
        report.update(binary=identity, reference=reference_identity)

        def check():
            if capture_identity(binary) != identity:
                raise RuntimeError('binary execution provenance changed during capture')
            if reference and capture_identity(reference) != reference_identity:
                raise RuntimeError('reference execution provenance changed during capture')

        if args.mode == 'effects':
            result = effects(binary, output, args.jobs, check)
        elif args.mode == 'offline':
            if not reference:
                parser.error('offline requires --reference')
            result = offline(reference, binary, output, check)
        elif args.mode == 'wrappers':
            result = wrappers(binary, output, check)
        else:
            result = tls(binary, output, check)
        report.update(result)
        check()
        if report['total'] <= 0 or report['passed'] != report['total']:
            raise RuntimeError('portability checks failed; inspect individual cases')
    except BaseException as error:
        report.update(status='failed', errors=[f'{type(error).__name__}: {error}'])
        save(output / 'report.json', report)
        raise
    report['status'] = 'completed'
    save(output / 'report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('cases', 'models')}), flush=True)
    raise SystemExit(0 if report['passed'] == report['total'] else 1)


if __name__ == '__main__':
    main()
