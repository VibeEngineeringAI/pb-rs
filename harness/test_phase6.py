"""Phase6 corpus, real child lifecycle and comparator failure tests."""

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import sys
import tempfile
import threading
import unittest

import phase6 as p6


class CorpusTests(unittest.TestCase):
    def test_all_statuses_at_every_stage_have_finite_retries(self):
        cases = {c['name']: c for c in p6.corpus()}
        for stage in ('doget', 'init', 'schema', 'batch'):
            for code, label in enumerate(p6.CODES):
                c = cases[f'p6-status-{stage}-{label.lower()}']
                retry = stage in ('schema', 'batch') and code in (0, 1, 14)
                self.assertEqual(c['expected']['dogets'], 2 if retry else 1)
                stream = c['fixture']['phase6']['streams'][c['dataset']]
                self.assertEqual(len(stream['subscriptions']), 2 if retry else 1)
                if retry:
                    self.assertEqual(stream['subscriptions'][-1]['ops'][-1]['code'], 3)

    def test_unique_and_independent_corpus(self):
        cases = p6.corpus()
        self.assertEqual(len(cases), len({c['name'] for c in cases}))
        cases[0]['argv'][0] = 'changed'
        self.assertEqual(p6.corpus()[0]['argv'][0], 'tail')

    def test_all_auth_modes_and_oauth_snapshot(self):
        cases = {c['name']: c for c in p6.corpus()}
        for mode in p6.p2.AUTH:
            c = cases['p6-auth-' + mode]
            self.assertEqual(c['expected']['auth'], p6.p2.HEADERS[mode])
        c = cases['p6-oauth-discovery-snapshot']
        self.assertEqual(c['expected']['http'], [('GET', p6.ABOUT), ('POST', p6.TOKEN), ('GET', p6.ABOUT)])
        self.assertEqual(c['expected']['dogets'], 1)
        self.assertEqual(c['expected']['auth']['cookie'], 'session=fixture-new-session')

    def test_signal_matrix_and_sinks(self):
        names = {c['name'] for c in p6.corpus()}
        for sig in ('int', 'term'):
            for stage in ('discovery', 'init', 'idle', 'write', 'reconnect'):
                self.assertIn(f'p6-signal-{sig}-{stage}', names)
        for mode in ('devfull', 'closed', 'broken-pipe', 'nonblocking', 'pty'):
            self.assertIn('p6-stdout-' + mode, names)

    def test_raw_status_cases_cover_bytes_and_missing_status_negative_controls(self):
        names = {c['name'] for c in p6.corpus()}
        for name in ('details-invalid-initial', 'details-invalid-batch', 'message-ff-0',
                     'message-ff-1', 'message-ff-14', 'message-partial-two-fatal-json',
                     'message-partial-two-fatal-text', 'explicit-unknown-missing-text-goaway-ours',
                     'rst-no-error-initial', 'rst-no-error-batch', 'missing-data-end-batch',
                     'missing-status-batch', 'malformed-status-batch', 'http-599'):
            self.assertIn('p6-raw-' + name, names)

    def test_trailer_followup_scripts_continue_after_terminal_headers(self):
        cases = p6.trailer_followups()
        self.assertEqual(len(cases), 16)
        self.assertFalse({c['name'] for c in cases} & {c['name'] for c in p6.corpus()})
        for c in cases:
            acts = c['fixture']['phase6']['endpoints'][0]['subscriptions'][0]['acts']
            index = next(i for i, a in enumerate(acts) if a['act'] == 'trailers')
            self.assertEqual(acts[index - 1], {'act': 'sleep', 'ms': 50})
            self.assertEqual(acts[-1]['act'], 'close')
            self.assertNotIn('sleep', [a['act'] for a in acts[index + 1:]])
            code = int(acts[index]['fields'][0][1])
            self.assertEqual(c['expected']['dogets'], 1 if code == 3 else 2)

    def test_ticket_go_escaping(self):
        self.assertEqual(p6.ticket('<>&"\\\u2028\u2029'),
                         '{"stream":"\\u003c\\u003e\\u0026\\"\\\\\\u2028\\u2029"}')
        self.assertEqual(json.loads(p6.ticket('café'))['stream'], 'café')

    def test_review3_probes_are_separate_and_have_bounded_fatal_fallbacks(self):
        cases = p6.review3_followups()
        self.assertEqual(len(cases), 56)
        self.assertFalse({c['name'] for c in cases} & {c['name'] for c in p6.corpus()})
        for c in cases:
            subs = c['fixture']['phase6']['endpoints'][0]['subscriptions']
            self.assertLessEqual(len(subs), 3)
            for sub in subs:
                self.assertIn(sub['acts'][-1]['act'], ('trailers', 'data', 'rst', 'close'))
                for act in sub['acts']:
                    if act['act'] == 'data':
                        self.assertLess(len(base64.b64decode(act['b64'], validate=True)), 64 * 1024)
            if 'refused-' in c['name']:
                self.assertTrue(c['expected']['transparent_retry'])
                self.assertEqual(subs[-1]['acts'][-1]['fields'][0], ['grpc-status', '3'])

    def test_basic_output_model_keeps_integer_precision_and_timestamp_fraction(self):
        value = json.loads(p6.basic_output(1).strip())
        self.assertEqual(value['large_integer'], 9007199254740994)
        self.assertEqual(value['p_timestamp'], '2024-01-01 00:00:00.000001')
        self.assertTrue(p6.basic_output().endswith(b'\n\n'))

    def test_only_supported_cli_is_exercised(self):
        for c in p6.corpus():
            self.assertEqual(c['argv'][0], 'tail')
            self.assertNotIn('resume', c['argv'])
            self.assertTrue(c['dataset'].startswith('p6-'))


class CollectorTests(unittest.TestCase):
    def execute(self, script, case=None):
        p6.TMP.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=p6.TMP) as root:
            out = Path(root)
            env = p6.env_for(out)
            r = p6.execute([sys.executable, '-c', script], env, out, case or {}, None, out)
            r['stdout_actual'] = (out / 'stdout.bin').read_bytes()
            r['stderr_actual'] = (out / 'stderr.bin').read_bytes()
            return r

    def test_exact_bytes_and_raw_wait_status(self):
        r = self.execute('import os; os.write(1,b\'{"x":1}\\n\\n\'); os.write(2,b"\\x1b[2K\\r watching\\n"); raise SystemExit(7)')
        self.assertEqual(r['failure'], None)
        self.assertEqual(r['raw_wait_status'], 7 << 8)
        self.assertEqual(r['termination'], {'exit': 7})
        self.assertEqual(r['stdout_actual'], b'{"x":1}\n\n')
        self.assertEqual((r['stdout_rows'], r['stdout_batches']), (1, 1))
        self.assertEqual(r['stderr_actual'], b'\x1b[2K\r watching\n')
        self.assertEqual(r['stdout_sha256'], hashlib.sha256(r['stdout_actual']).hexdigest())

    def test_signal_is_os_death_not_exit_130(self):
        r = self.execute('import time; time.sleep(5)', dict(actions=[
            dict(when={'start': True}, do={'signal': 'TERM'})]))
        self.assertEqual(r['termination'], {'signal': signal.SIGTERM})
        self.assertEqual(r['raw_wait_status'], signal.SIGTERM)
        self.assertEqual(r['failure'], None)

    def test_timeout_is_failed_even_if_child_was_killed(self):
        r = self.execute('import time; time.sleep(5)', {'timeout': .05})
        self.assertEqual(r['failure'], 'timeout')
        self.assertEqual(r['termination'], {'signal': signal.SIGKILL})

    def test_missed_action_never_forced_or_passed(self):
        r = self.execute('pass', dict(actions=[dict(when={'stdout_bytes': 99}, do={'signal': 'TERM'})]))
        self.assertEqual(r['failure'], 'missed-action')
        self.assertEqual(r['termination'], {'exit': 0})
        self.assertFalse(r['actions'][0]['met'])

    def test_bounded_prefix_but_full_digest_and_spool(self):
        r = self.execute('import os; os.write(1,b"x"*100000)')
        self.assertEqual(r['stdout_bytes'], 100000)
        self.assertEqual(len(base64.b64decode(r['stdout_prefix_b64'])), p6.PREFIX_CAP)
        self.assertEqual(r['stdout_sha256'], hashlib.sha256(b'x' * 100000).hexdigest())

    def test_overflow_fails_instead_of_truncation_parity(self):
        old = p6.STDOUT_CAP
        p6.STDOUT_CAP = 4096
        try:
            r = self.execute('import os; os.write(1,b"x"*100000)')
        finally:
            p6.STDOUT_CAP = old
        self.assertEqual(r['failure'], 'stdout-overflow')
        self.assertEqual(r['stdout_bytes'], 4096)

    def test_closed_fd_is_ebadf_not_broken_pipe(self):
        r = self.execute('import os,errno;\ntry: os.write(1,b"x")\nexcept OSError as e: os._exit(0 if e.errno==errno.EBADF else 8)',
                         {'io': {'stdout': 'closed'}})
        self.assertEqual(r['termination'], {'exit': 0})

    def test_devfull_receives_real_enospc(self):
        r = self.execute('import os,errno;\ntry: os.write(1,b"x")\nexcept OSError as e: os._exit(0 if e.errno==errno.ENOSPC else 8)',
                         {'io': {'stdout': 'devfull'}})
        self.assertEqual(r['termination'], {'exit': 0})

    def test_pty_keeps_raw_lf_and_ansi(self):
        r = self.execute('import os; os.write(2,b"\\x1b[2K\\rtext\\n")', {'io': {'stderr': 'pty'}})
        self.assertEqual(r['stderr_actual'], b'\x1b[2K\rtext\n')

    def test_nonblocking_fd_flag_is_inherited(self):
        r = self.execute('import os,fcntl; os._exit(0 if fcntl.fcntl(1,fcntl.F_GETFL)&os.O_NONBLOCK else 8)',
                         {'io': {'stdout': 'nonblocking'}})
        self.assertEqual(r['termination'], {'exit': 0})

    def test_action_event_matching_is_precise(self):
        events = [{'kind': 'gate_wait', 'name': 'other'}]
        self.assertFalse(p6.action_ready({'event': 'gate_wait', 'name': 'idle'}, {}, events))
        events.append({'kind': 'gate_wait', 'name': 'idle'})
        self.assertTrue(p6.action_ready({'event': 'gate_wait', 'name': 'idle'}, {}, events))

    def test_all_temp_variables_use_cache(self):
        env = p6.env_for(Path('/some/home'))
        for key in ('TMPDIR', 'GOTMPDIR', 'CLAUDE_CODE_TMPDIR'):
            self.assertEqual(env[key], str(p6.TMP))


class ComparatorTests(unittest.TestCase):
    def result(self):
        c = p6.corpus()[0]
        stdout = b'{"sequence":0}\n\n'
        return c, dict(raw_wait_status=256, termination={'exit': 1}, failure=None, actions=[],
                       stdout_sha256=hashlib.sha256(stdout).hexdigest(), stdout_bytes=len(stdout),
                       stderr_contract_b64=base64.b64encode(b'error\n').decode(),
                       requests=[dict(protocol='http', method='GET', path=p6.ABOUT, headers=c['expected']['http_auth'][0], body='', unexpected=False),
                                 dict(protocol='flight', method='DoGet', path='flight', headers=c['expected']['auth'],
                                      body=p6.ticket(c['dataset']), unexpected=False)], events=[],
                       config_before={}, config_after={}, summary={'streams': {c['dataset']: {
                           'expected_stdout_sha256': hashlib.sha256(stdout).hexdigest(), 'expected_stdout_bytes': len(stdout)}}})

    def test_independent_expectations_pass(self):
        c, r = self.result()
        self.assertEqual(p6.expectation_issues(c, r), [])

    def test_transparent_retry_rejects_a_tail_delay_or_third_attempt(self):
        c, r = self.result()
        c['expected'].update(transparent_retry=True, dogets=2)
        r['requests'].append(copy.deepcopy(r['requests'][1]))
        r['events'] = [dict(kind='raw_h2_stream', t_ns=t, metadata={'user-agent': ['grpc-go/1.65.0']})
                       for t in (0, 1000000)]
        self.assertEqual(p6.expectation_issues(c, r), [])
        r['events'][1]['t_ns'] = 500000000
        self.assertIn('transparent refusal acquired a tail reconnect delay', p6.expectation_issues(c, r))
        r['events'].append(copy.deepcopy(r['events'][1]))
        self.assertIn('transparent refusal must make exactly two DoGets', p6.expectation_issues(c, r))

    def test_ticket_metadata_http_count_and_stdout_tamper_fail(self):
        c, r = self.result()
        for change in ('ticket', 'metadata', 'http', 'stdout', 'timeout', 'status', 'config'):
            s = copy.deepcopy(r)
            if change == 'ticket': s['requests'][1]['body'] = '{}'
            elif change == 'metadata': s['requests'][1]['headers'] = {}
            elif change == 'http': s['requests'].append(s['requests'][0])
            elif change == 'stdout': s['stdout_sha256'] = 'bad'
            elif change == 'timeout': s['failure'] = 'timeout'
            elif change == 'status': s['termination'] = {'exit': 130}
            elif change == 'config': s['config_after'] = {'changed': True}
            with self.subTest(change=change):
                self.assertTrue(p6.expectation_issues(c, s))

    def test_ansi_whitespace_and_blank_batches_are_not_normalized(self):
        _, r = self.result()
        for key, value in [('stderr_contract_b64', base64.b64encode(b'error\n\n').decode()),
                           ('stdout_sha256', hashlib.sha256(b'{"sequence":0}\n').hexdigest()),
                           ('raw_wait_status', 130 << 8)]:
            candidate = copy.deepcopy(r)
            candidate[key] = value
            self.assertIn(key + ' differs', p6.differences(r, candidate))

    def test_raw_signal_status_has_separate_gate(self):
        _, r = self.result()
        candidate = copy.deepcopy(r)
        candidate['termination'] = {'signal': 2}
        candidate['raw_wait_status'] = 2
        self.assertIn('raw_wait_status differs', p6.differences(r, candidate))

    def test_fixture_panic_cannot_be_frozen_as_client_failure(self):
        c, r = self.result()
        for mode in ('event', 'summary', 'stderr'):
            bad = copy.deepcopy(r)
            if mode == 'event': bad['events'] = [{'kind': 'fixture_panic'}]
            elif mode == 'summary':
                bad['summary']['streams'][c['dataset']]['last_terminal'] = {'message': 'fixture panic: nil'}
            else: bad['stderr_contract_b64'] = base64.b64encode(b'fixture panic: nil').decode()
            with self.subTest(mode=mode):
                self.assertTrue(any('fixture panic' in i for i in p6.expectation_issues(c, bad)))

    def test_only_exact_native_ua_is_quarantined_connect_is_never_exempt(self):
        ref = {'flight_user_agents': [['grpc-go/1.65.0']]}
        candidate = {'flight_user_agents': [['tonic/0.14.6']]}
        second = ['Flight user-agent differs from pinned grpc-go', 'Flight metadata differs']
        delta = ['flight_user_agents differs', 'proxy_requests differs']
        remaining, changed, wire = p6.split_wire_ua(ref, candidate, second, delta)
        self.assertEqual(remaining, ['Flight metadata differs'])
        self.assertEqual(changed, ['proxy_requests differs'])
        self.assertEqual(len(wire), 2)
        for values in ([['unknown/1']], [['tonic/0.14.6'], ['tonic/0.14.6']], []):
            candidate['flight_user_agents'] = values
            self.assertEqual(p6.split_wire_ua(ref, candidate, second, delta), (second, delta, []))

    def test_authority_host_and_presence_of_port_are_preserved(self):
        def event(value): return {'metadata': {':authority': [value]}}
        self.assertEqual(p6.authority_contract(event('127.0.0.1:12345'), 12345), ['127.0.0.1:{{grpc_port}}'])
        self.assertEqual(p6.authority_contract(event('127.0.0.1'), 12345), ['127.0.0.1'])
        self.assertEqual(p6.authority_contract(event('[::1]:12345'), 12345), ['[::1]:{{grpc_port}}'])
        self.assertEqual(p6.authority_contract(event('::1'), 12345), ['::1'])
        self.assertNotEqual(p6.authority_contract(event('127.0.0.1'), 12345),
                            p6.authority_contract(event('127.0.0.1:12345'), 12345))
        for bad in ('127.0.0.1:1', '[::1]:1', '127.0.0.1:23456'):
            self.assertEqual(p6.authority_contract(event(bad), 12345), [bad])

    def test_selected_raw_endpoint_does_not_accept_other_fixture_ports(self):
        case = next(c for c in p6.corpus() if c['name'] == 'p6-raw-message-ff-3')
        info = dict(grpc_port=12345, endpoints={'raw': 23456, 'other': 34567})
        trace = [dict(protocol='http', method='GET', path=p6.ABOUT, headers=case['expected']['auth'])]
        selected = p6.discovered_flight_port(case, info, trace)
        self.assertEqual(selected, 23456)
        for port in (1, 12345, 34567):
            value = '127.0.0.1:' + str(port)
            self.assertEqual(p6.authority_contract({'metadata': {':authority': [value]}}, selected), [value])
        self.assertEqual(p6.authority_contract({'metadata': {':authority': ['127.0.0.1:23456']}}, selected),
                         ['127.0.0.1:{{grpc_port}}'])

    def test_malformed_header_exception_is_one_exact_message_pair(self):
        def result(message):
            return {'stderr_contract_b64': base64.b64encode(json.dumps({'error': {
                'code': 'COMMAND_FAILED', 'message': message, 'retryable': False}}, indent=2).encode() + b'\n').decode()}
        prefix = 'arrow/flight: could not create flight reader: arrow/ipc: unknown error while reading: '
        ref = result(prefix + 'runtime error: slice bounds out of range [4022250974:12]')
        rust = result(prefix + 'Type `i32` at position 4022250974 is unaligned.\n\n')
        delta = ['stderr_contract_b64 differs', 'stdout_sha256 differs']
        remaining, diagnostics = p6.split_diagnostics({'name': 'p6-corrupt-garbage_header'}, ref, rust, delta)
        self.assertEqual(remaining, ['stdout_sha256 differs'])
        self.assertEqual(len(diagnostics), 1)
        for changed in (base64.b64decode(rust['stderr_contract_b64']) + b'\n',
                        json.dumps(json.loads(base64.b64decode(rust['stderr_contract_b64']))).encode(),
                        (json.dumps({**json.loads(base64.b64decode(rust['stderr_contract_b64'])),
                                     'unexpected': 'unaccepted'}, indent=2) + '\n').encode()):
            mutated = {'stderr_contract_b64': base64.b64encode(changed).decode()}
            self.assertEqual(p6.split_diagnostics({'name': 'p6-corrupt-garbage_header'}, ref, mutated, delta),
                             (delta, []))
        self.assertEqual(p6.split_diagnostics({'name': 'p6-corrupt-other'}, ref, rust, delta), (delta, []))
        rust = result(prefix + 'different verification error')
        self.assertEqual(p6.split_diagnostics({'name': 'p6-corrupt-garbage_header'}, ref, rust, delta), (delta, []))


class ProxyTests(unittest.TestCase):
    def test_connect_records_exact_head_and_tunnels_without_dns(self):
        class Echo(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.sendall(self.request.recv(1024))
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Echo)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        proxy = p6.Proxy()
        try:
            head = (f'CONNECT flight.fixture.invalid:{server.server_address[1]} HTTP/1.1\r\n'
                    'Host: flight.fixture.invalid\r\nUser-Agent: grpc-go/1.65.0\r\n\r\n').encode()
            with socket.create_connection(proxy.server_address, timeout=2) as client:
                client.sendall(head)
                self.assertEqual(client.recv(1024), b'HTTP/1.1 200 Connection Established\r\n\r\n')
                client.sendall(b'bytes-without-DNS')
                self.assertEqual(client.recv(1024), b'bytes-without-DNS')
            self.assertEqual(base64.b64decode(proxy.records[0]['head_b64']), head)
        finally:
            proxy.close()
            server.shutdown()
            server.server_close()
            worker.join()

    def test_absolute_form_http_is_forwarded_to_loopback(self):
        class HTTP(socketserver.BaseRequestHandler):
            def handle(self):
                self.server.received = self.request.recv(65536)
                self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}')
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), HTTP)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        proxy = p6.Proxy()
        try:
            port = server.server_address[1]
            with socket.create_connection(proxy.server_address, timeout=2) as client:
                client.sendall((f'GET http://flight.fixture.invalid:{port}/api/v1/about HTTP/1.1\r\n'
                                'Host: flight.fixture.invalid\r\n\r\n').encode())
                response = bytearray()
                while data := client.recv(1024):
                    response += data
            self.assertTrue(response.endswith(b'{}'))
            self.assertTrue(server.received.startswith(b'GET /api/v1/about HTTP/1.1\r\n'))
        finally:
            proxy.close()
            server.shutdown()
            server.server_close()
            worker.join()

    def test_407_never_dials_target(self):
        proxy = p6.Proxy(reject_connect=True)
        try:
            with socket.create_connection(proxy.server_address, timeout=2) as client:
                client.sendall(b'CONNECT flight.fixture.invalid:1 HTTP/1.1\r\nHost: flight.fixture.invalid:1\r\n\r\n')
                self.assertIn(b'407 Proxy Authentication Required', client.recv(1024))
            self.assertEqual(proxy.records[0]['method'], 'CONNECT')
        finally:
            proxy.close()


class ReferenceTests(unittest.TestCase):
    def test_incomplete_and_wrong_inventory_reference_are_rejected(self):
        cases = p6.corpus()
        p6.TMP.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=p6.TMP) as root:
            fixture = Path(root) / 'fixture'
            fixture.write_bytes(b'fixture')
            valid = dict(schema_version=p6.SCHEMA_VERSION, oracle_revision='07af390', complete=True,
                         failed=0, partial=False, mode='freeze', parity_passed=len(cases),
                         corpus_sha256=p6.run.digest(p6.run.canonical(cases)),
                         fixture_sha256=p6.run.digest(b'fixture'), cases={c['name']: {
                             'go_sha256': p6.ORACLE_HASHES[c['build']],
                             'second_sha256': p6.ORACLE_HASHES[c['build']]} for c in cases})
            p6.validate_frozen(valid, cases, fixture)
            for key, value in [('complete', False), ('failed', 1), ('cases', {}),
                               ('corpus_sha256', 'bad'), ('fixture_sha256', 'bad'),
                               ('oracle_revision', 'other'), ('mode', 'compare'), ('partial', True),
                               ('parity_passed', 1)]:
                changed = copy.deepcopy(valid)
                changed[key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    p6.validate_frozen(changed, cases, fixture)
            for key, value in [('issues', ['error']), ('wire_issues', ['UA']),
                               ('diagnostic_issues', ['difference']), ('second_sha256', 'other')]:
                changed = copy.deepcopy(valid)
                changed['cases'][cases[0]['name']][key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    p6.validate_frozen(changed, cases, fixture)


if __name__ == '__main__':
    unittest.main()
