#!/usr/bin/env python3
"""Real-PTY login/device effects with a loopback proxy and inert browser.

Build both binaries with release URL http://phase7-orchestrator.invalid and the
public token harness-orchestrator-token. No account or external browser is used.
This functional gate records styled frames but makes no timing claim.
"""
import argparse
import copy
import http.server
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import tomllib
import urllib.parse

import phase1
import phase2
import phase7

ORIGIN = 'http://phase7-orchestrator.invalid'
TOKEN = 'harness-orchestrator-token'
SPINNER = '⣾⣽⣻⢿⡿⣟⣯⣷'
CASES = ('apikey', 'apikey-rejected', 'device-success', 'device-pending',
         'device-denied', 'device-expired', 'device-timeout', 'device-cancel',
         'device-sigint', 'device-browser-limit', 'device-malformed',
         'device-expiry-overflow', 'device-interval-overflow', 'device-redirect-302',
         'device-redirect-deadline', 'device-poll-redirect-deadline',
         'device-poll-redirect-drain')


class Fixture:
    def __init__(self, case):
        self.case, self.requests = case, []
        fixture = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def reply(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                path = urllib.parse.urlsplit(self.path).path
                fixture.requests.append({'method': self.command, 'path': path,
                    'body': body.decode(), 'headers': {k.lower(): [v] for k, v in self.headers.items()}})
                if fixture.case == 'device-redirect-302' and path == '/api/v1/cli/device/code':
                    self.send_response(302)
                    self.send_header('Location', '/redirect-code')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                redirect = ((fixture.case == 'device-redirect-deadline' and path == '/api/v1/cli/device/code') or
                            (fixture.case in ('device-poll-redirect-deadline', 'device-poll-redirect-drain') and path == '/api/v1/cli/oauth/token'))
                if redirect:
                    self.send_response(307)
                    self.send_header('Location', '/slow-device')
                    self.send_header('Content-Length', '100' if fixture.case == 'device-poll-redirect-drain' else '0')
                    self.end_headers()
                    if fixture.case == 'device-poll-redirect-drain':
                        self.wfile.write(b'x' * 10)
                        self.wfile.flush()
                        time.sleep(4)
                    return
                if path == '/slow-device':
                    time.sleep(31 if fixture.case == 'device-redirect-deadline' else 4)
                status, value = fixture.response(path)
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            do_POST = do_GET = reply
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.urls = {'url': self.url}
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def response(self, path):
        if path == '/api/v1/cli/apikey/validate':
            if self.case == 'apikey-rejected':
                return 401, {'error': 'invalid fixture key'}
            return 200, {'workspace_id': 'workspace', 'workspace_name': 'fixture',
                         'tenant_id': 'tenant', 'url': 'http://workspace.invalid'}
        if path in ('/api/v1/cli/device/code', '/redirect-code'):
            if self.case == 'device-malformed':
                return 200, {'device_code': 'fixture-device'}
            return 200, {'device_code': 'fixture-device', 'user_code': 'ABCD-EFGH',
                         'verification_uri': 'http://verification.invalid',
                         'verification_uri_complete': 'http://unused.invalid',
                         'expires_in': (9999999999 if self.case == 'device-expiry-overflow' else
                                        1 if self.case == 'device-timeout' else
                                        2 if self.case in ('device-poll-redirect-deadline', 'device-poll-redirect-drain') else 20),
                         'interval': (9999999999 if self.case == 'device-interval-overflow' else
                                      2 if self.case == 'device-timeout' else 1)}
        if path == '/api/v1/cli/oauth/token':
            count = sum(r['path'] == path for r in self.requests)
            if self.case == 'device-denied':
                return 400, {'error': 'access_denied', 'error_description': 'fixture denial'}
            if self.case == 'device-expired':
                return 400, {'error': 'expired_token'}
            if ((self.case in ('device-pending', 'device-interval-overflow') and count == 1) or
                    (self.case == 'device-browser-limit' and count < 7) or
                    self.case in ('device-timeout', 'device-cancel', 'device-sigint')):
                return 400, {'error': 'authorization_pending'}
            return 200, {'access_token': 'fixture-access', 'refresh_token': 'fixture-refresh',
                         'workspace': {'id': 'workspace', 'tenant_id': 'tenant',
                                       'url': 'http://workspace.invalid'}}
        return 500, {'unexpected': path}

    def trace(self):
        return self.requests.copy()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()


def action(key=None, text=None, contains=(), checkpoint=None):
    value = {'wait': {'contains': list(contains)}}
    if key is not None:
        value['key'] = key
    if text is not None:
        value['send'] = text
    if checkpoint:
        value['checkpoint'] = checkpoint
    return value


def script(case):
    actions = [action(key='down', contains=('CONNECT TO',), checkpoint='deployment'),
               action(key='enter'), action(contains=('CLOUD AUTHENTICATION',), checkpoint='cloud-auth')]
    if case.startswith('apikey'):
        actions += [action(key='down'), action(key='enter'),
                    action(text='fixture-user-key', contains=('API KEY',)),
                    action(key='enter'), action(contains=('PROFILE NAME',), checkpoint='profile-name'),
                    action(key='enter')]
    else:
        actions += [action(key='enter'), action(contains=('PROFILE NAME',), checkpoint='profile-name'),
                    action(key='enter')]
        if case not in ('device-malformed', 'device-expiry-overflow', 'device-interval-overflow',
                        'device-redirect-deadline'):
            waiting = action(contains=('Waiting for authorization', 'ABCD-EFGH'), checkpoint='authorization')
            waiting['stable_ms'] = 0  # spinner phases are captured, never assumed to become static
            actions.append(waiting)
        if case == 'device-cancel':
            actions.append(action(key='ctrl-c'))
        if case == 'device-sigint':
            actions.append({'signal': 'SIGINT'})
    return actions


def run_case(binary, name, output):
    output.mkdir(parents=True, exist_ok=False)
    fixture = Fixture(name)
    try:
        with tempfile.TemporaryDirectory(prefix='phase7-device-', dir=phase7.TMP) as temporary:
            root = Path(temporary)
            home, helpers = root / 'home', root / 'bin'
            helpers.mkdir()
            for part in ('config', 'cache', 'data', 'state', 'tmp'):
                (home / part).mkdir(parents=True)
            browser = helpers / 'xdg-open'
            browser.write_text('#!/bin/sh\n{ printf "%s\\n" "$1"; ulimit -n; } > "$PB_BROWSER_EVIDENCE"\n')
            browser.chmod(0o700)
            wrapper = helpers / 'limit'
            wrapper.write_text('#!/bin/sh\nulimit -n 64\nexec "$@"\n')
            wrapper.chmod(0o700)
            evidence = root / 'browser.txt'
            env = phase2.isolated_env(home, {'env': {
                'PATH': str(helpers) + ':' + os.defpath, 'TERM': 'xterm-256color',
                'COLORTERM': 'truecolor', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                'HTTP_PROXY': fixture.url, 'PB_BROWSER_EVIDENCE': str(evidence)}})
            case = {'name': name, 'argv': ['login'], 'size': [100, 32],
                    'timeout': 40 if name == 'device-redirect-deadline' else 15,
                    'theme': 'dark', 'actions': script(name), 'expect': {},
                    'masked_values': ['fixture-user-key', 'fixture-access', 'fixture-refresh']}
            replacements = {str(home): '<HOME>', fixture.url: '<PROXY>'}
            before = phase7.config_snapshot(home, replacements)
            result = phase7.execute([str(wrapper), str(binary.resolve()), 'login'], env, home, case, fixture)
            result.update(case=case, replacements=replacements, config_before=before,
                          config_after=phase7.config_snapshot(home, replacements), requests=fixture.trace())
            browser_data = evidence.read_text() if evidence.exists() else ''
            result['browser'] = browser_data
            issues = phase7.validate(result)
            success = name in ('apikey', 'device-success', 'device-pending', 'device-browser-limit',
                               'device-interval-overflow', 'device-redirect-302')
            expected_exit = 0 if success else 1
            if result['termination'] != {'exit': expected_exit}:
                issues.append('wrong exit: ' + repr(result['termination']))
            config = home / 'config/pb/config.toml'
            if success:
                if not config.exists():
                    issues.append('profile was not saved')
                else:
                    data = tomllib.loads(config.read_text())
                    profile = data['Profiles'].get('default', {})
                    expected = {'url': 'http://workspace.invalid', 'cloud': True,
                                'tenant_id': 'tenant', 'workspace_id': 'workspace', 'orchestrator_url': ORIGIN}
                    expected.update({'apiKey': 'fixture-user-key'} if name == 'apikey' else
                                    {'session_token': 'fixture-access', 'refresh_token': 'fixture-refresh'})
                    if data['DefaultProfile'] != 'default' or any(profile.get(k) != v for k, v in expected.items()):
                        issues.append('saved profile differs: ' + repr(profile))
                    if config.stat().st_mode & 0o777 != 0o600:
                        issues.append('config mode differs')
            elif config.exists():
                issues.append('failed/canceled authorization persisted a profile')
            for request in fixture.requests:
                headers = request['headers']
                if headers.get('authorization') != ['Bearer ' + TOKEN]:
                    issues.append('orchestrator authorization differs')
                if request['method'] == 'POST' and headers.get('accept') != ['application/json']:
                    issues.append('Accept header differs')
                if request['path'].endswith('/validate') and headers.get('x-api-key') != ['fixture-user-key']:
                    issues.append('API-key validation header differs')
                if request['path'] == '/redirect-code' and (
                        request['method'] != 'GET' or request['body'] or
                        headers.get('accept') != ['application/json'] or
                        headers.get('content-type') != ['application/json']):
                    issues.append('redirect method/body/headers differ')
                if request['method'] == 'POST':
                    payload = json.loads(request['body'])
                    expected = ({'client_id': 'pb-cli'} if request['path'].endswith('/code') or name == 'device-redirect-deadline' else
                                {'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'device_code': 'fixture-device'})
                    if payload != expected:
                        issues.append('device request payload differs')
            tokens = sum(r['path'].endswith('/token') for r in fixture.requests)
            if name in ('device-pending', 'device-interval-overflow') and tokens != 2:
                issues.append('pending did not retry exactly once')
            if name in ('device-cancel', 'device-sigint', 'device-malformed', 'device-expiry-overflow') and tokens:
                issues.append('unexpected token request after cancel/error')
            expected_error = {'device-redirect-deadline': 'Post \"http://phase7-orchestrator.invalid/slow-device\": context deadline exceeded (Client.Timeout exceeded while awaiting headers)',
                'device-poll-redirect-deadline': 'cloud token request failed: Post \"http://phase7-orchestrator.invalid/slow-device\": context deadline exceeded',
                'device-poll-redirect-drain': 'cloud token request failed: Post \"http://phase7-orchestrator.invalid/slow-device\": context deadline exceeded',
                'device-denied': 'cloud device login failed: access_denied: fixture denial',
                'device-expired': 'cloud device code expired; run login again',
                'device-timeout': 'cloud device login expired or timed out',
                'device-expiry-overflow': 'cloud device login expired or timed out',
                                'device-sigint': 'cloud token response missing access_token',
                'device-malformed': 'cloud device authorization response missing device_code or user_code'}
            if name in expected_error and expected_error[name].encode() not in result['stderr']:
                issues.append('missing error: ' + expected_error[name])
            if name == 'device-cancel' and not any(text in result['stderr'] for text in
                    (b'cloud device login aborted', b'cloud device login expired or timed out')):
                issues.append('unexpected cancellation error (Go completion/quit race)')
            if name == 'device-browser-limit':
                if browser_data != 'http://verification.invalid\n64\n':
                    issues.append('browser URL/inherited limit differs: ' + repr(browser_data))
            elif browser_data:
                issues.append('browser launched before five-second delay/after cancellation')
            (output / 'stdout.ansi').write_bytes(result['stdout'])
            (output / 'stderr.txt').write_bytes(result['stderr'])
            public = {k: v for k, v in result.items() if k not in ('stdout', 'stderr', 'post_screen_stdout')}
            public.update(issues=issues, binary=phase1.binary_identity(binary))
            (output / 'result.json').write_text(json.dumps(public, indent=2) + '\n')
            return result, issues
    finally:
        fixture.close()


def comparable(result):
    """Normalize only the phase of Go's animated Dot spinner and cancel race.

    Every spinner cell keeps its position and attributes. Only a valid glyph
    immediately before the exact waiting label can vary. Borders, text, cursor,
    scrollback, successful/error states and all effects remain compared.
    """
    result = copy.deepcopy(result)
    frames = [stamp['frame'] for stamp in result['frames']] + [result['final_frame']]
    for frame in frames:
        for field in ('cells', 'scrollback'):
            for row in frame[field]:
                text = ''.join(cell[0] for cell in row)
                start = text.find('  Waiting for authorization... [Ctrl+C to abort]')
                if start > 0 and row[start - 1][0] in SPINNER:
                    row[start - 1][0] = SPINNER[0]
    if result['case']['name'] == 'device-cancel':
        # Actual Go Ctrl+C can observe the canceled poll completion before Quit.
        # Both messages have been oracle-characterized; validation rejects any
        # other error, request or persisted credential before comparison.
        for message in (b'cloud device login aborted', b'cloud device login expired or timed out'):
            result['stderr'] = result['stderr'].replace(message, b'<device cancellation outcome>')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--case', action='append', choices=CASES)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    phase7.TMP.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=False)
    report = []
    for name in args.case or CASES:
        reference, issues = run_case(args.reference, name, args.out / name / 'reference')
        issues = ['reference: ' + issue for issue in issues]
        if args.candidate:
            candidate, errors = run_case(args.candidate, name, args.out / name / 'candidate')
            issues += ['candidate: ' + error for error in errors]
            issues += phase7.compare(comparable(reference), comparable(candidate))
        report.append({'case': name, 'issues': issues})
        print(name + ': ' + ('; '.join(issues) if issues else 'pass'), flush=True)
    (args.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return int(any(item['issues'] for item in report))


if __name__ == '__main__':
    raise SystemExit(main())
