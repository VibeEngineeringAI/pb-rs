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

import phase1

ROOT = Path(__file__).resolve().parent.parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def effects(binary, output, jobs):
    source = (ROOT / 'tests/interactive_effects.rs').read_text()
    fixture = source.split('const PROCESS_FIXTURE: &str = r####"', 1)[1].rsplit('"####;', 1)[0]
    cases = [s.strip() for s in re.search(r'process_cases!\((.*?)\);', source, re.S)[1].split(',') if s.strip()]
    save(output / 'definition.json', {'source_sha256': sha(ROOT / 'tests/interactive_effects.rs'), 'cases': cases})

    def run(case):
        env = dict(os.environ, PB_EFFECTS_CAPTURE_DIR=str(output / 'captures'))
        proc = subprocess.run([sys.executable, '-I', '-c', fixture, str(binary), case],
                              capture_output=True, env=env, timeout=150)
        (output / (case + '.stdout')).write_bytes(proc.stdout)
        (output / (case + '.stderr')).write_bytes(proc.stderr)
        return {'case': case, 'returncode': proc.returncode, 'passed': proc.returncode == 0}

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        rows = list(pool.map(run, cases))
    return {'kind': 'existing independent process/PTY effects', 'cases': rows,
            'passed': sum(row['passed'] for row in rows), 'total': len(rows)}


def offline(reference, binary, output):
    rows = []
    for case in phase1.corpus():
        before = phase1.sample(reference, case['argv'], 10)
        after = phase1.sample(binary, case['argv'], 10)
        issues = phase1.differences(before, after)
        phase1.store_result(output / case['name'] / 'reference', before)
        phase1.store_result(output / case['name'] / 'candidate', after)
        rows.append({'name': case['name'], 'argv': case['argv'], 'issues': issues})
    return {'kind': 'offline runtime byte parity; not historical phase-one admission',
            'cases': rows, 'passed': sum(not row['issues'] for row in rows), 'total': len(rows)}


def wrappers(binary, output):
    rows = []
    for model in ('qemu64', 'Nehalem', 'Haswell-noTSX'):
        path = output / ('pb-' + model)
        path.write_text('#!/bin/sh\nexec /usr/bin/qemu-x86_64 -cpu ' + shlex.quote(model)
                        + ' ' + shlex.quote(str(binary)) + ' "$@"\n')
        path.chmod(0o755)
        proc = subprocess.run([str(path), 'version'], capture_output=True, timeout=20)
        rows.append({'model': model, 'wrapper': str(path), 'wrapper_sha256': sha(path),
                     'returncode': proc.returncode, 'stdout': proc.stdout.decode(errors='replace'),
                     'stderr': proc.stderr.decode(errors='replace'),
                     'passed': proc.returncode == 0 and proc.stdout == b'pb version 0.1.0\n' and not proc.stderr})
    return {'kind': 'QEMU user-mode CPU emulation; functional only', 'models': rows,
            'passed': sum(row['passed'] for row in rows), 'total': len(rows)}


def tls(binary, output):
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
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            self.reply([{'value': 1}])

        def do_GET(self):
            self.reply({'status': 'success', 'data': {'resultType': 'matrix', 'result': [
                {'metric': {'__name__': 'up'}, 'values': [[1704067200, '1']]}]}})

        def reply(self, body):
            observations.append({'path': self.path, 'tls': self.connection.version(),
                                 'cipher': self.connection.cipher()})
            body = json.dumps(body).encode()
            self.send_response(200)
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
                    ('sql', ['sql', 'run', 'SELECT * FROM fixture LIMIT 1', '--from=1h', '-o', 'json']),
                    ('promql', ['promql', 'run', 'up', '--dataset', 'metrics', '--from=1h', '-o', 'json']),
                ]:
                    before = len(observations)
                    proc = subprocess.run([str(binary), *argv], env=env, capture_output=True, timeout=30)
                    (output / f'{label}-{name}.stdout').write_bytes(proc.stdout)
                    (output / f'{label}-{name}.stderr').write_bytes(proc.stderr)
                    try:
                        body = json.loads(proc.stdout)
                    except ValueError:
                        body = None
                    valid = (body == [{'value': 1}] if name == 'sql' else
                             isinstance(body, dict) and body.get('status') == 'success')
                    rows.append({'tls': label, 'command': name, 'returncode': proc.returncode,
                                 'observations': observations[before:],
                                 'passed': proc.returncode == 0 and not proc.stderr and valid
                                 and any(row['tls'] == label for row in observations[before:])})
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
    identity = {'path': str(binary), 'sha256': sha(binary)}
    if args.mode == 'effects':
        report = effects(binary, output, args.jobs)
    elif args.mode == 'offline':
        if not args.reference:
            parser.error('offline requires --reference')
        report = offline(args.reference.resolve(), binary, output)
    elif args.mode == 'wrappers':
        report = wrappers(binary, output)
    else:
        report = tls(binary, output)
    report.update(binary=identity, runner_sha256=sha(__file__))
    if sha(binary) != identity['sha256']:
        raise RuntimeError('binary changed during capture')
    save(output / 'report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('cases', 'models')}), flush=True)
    raise SystemExit(0 if report['passed'] == report['total'] else 1)


if __name__ == '__main__':
    main()
