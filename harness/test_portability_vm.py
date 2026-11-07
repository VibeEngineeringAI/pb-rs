import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.parse

import portability_vm as vm


class TlsAssertions(unittest.TestCase):
    def test_boolean_cannot_substitute_for_a_json_numeric_sample(self):
        observed = [{'method': 'POST', 'tls': 'TLSv1.3', 'valid_request': True}]
        self.assertFalse(vm.valid_tls_result('sql', b'[{"value":true}]', b'', 0, observed, 'TLSv1.3'))
        self.assertTrue(vm.valid_tls_result('sql', b'[{"value":1.0}]', b'', 0, observed, 'TLSv1.3'))

    def test_requests_require_method_endpoint_authentication_and_complete_query(self):
        headers = {'x-api-key': 'fixture-key', 'content-type': 'application/json'}
        body = json.dumps({'query': vm.SQL_QUERY, 'startTime': vm.START, 'endTime': vm.END}).encode()
        self.assertTrue(vm.valid_request('POST', '/api/v1/query', headers, body))
        query = {'query': 'up', 'stream': 'metrics', 'start': vm.START, 'end': vm.END, 'step': '15s'}
        path = '/prometheus/api/v1/query_range?' + urllib.parse.urlencode(query)
        self.assertTrue(vm.valid_request('GET', path, headers, b''))
        for method, endpoint, auth, content in [
            ('GET', '/api/v1/query', headers, body),
            ('POST', '/wrong-endpoint', headers, body),
            ('POST', '/api/v1/query', {}, body),
            ('POST', '/api/v1/query', headers, b'{"query":"wrong"}'),
            ('GET', path.replace('stream=metrics', 'stream=wrong'), headers, b''),
            ('GET', path.replace('query=up', 'query=wrong'), headers, b''),
            ('GET', path + '&query=duplicate', headers, b''),
        ]:
            self.assertFalse(vm.valid_request(method, endpoint, auth, content))

    def test_success_status_without_results_or_wrong_request_cannot_pass(self):
        observed = [{'method': 'GET', 'tls': 'TLSv1.3', 'valid_request': True}]
        complete = json.dumps(vm.PROMQL_RESPONSE).encode()
        self.assertTrue(vm.valid_tls_result('promql', complete, b'', 0, observed, 'TLSv1.3'))
        for stdout, events in [(b'{"status":"success"}', observed),
                               (complete, [dict(observed[0], valid_request=False)]),
                               (complete, [dict(observed[0], tls='TLSv1.2')]),
                               (complete, observed * 2), (complete, [])]:
            self.assertFalse(vm.valid_tls_result('promql', stdout, b'', 0, events, 'TLSv1.3'))


class ProvenanceTests(unittest.TestCase):
    def wrapper(self, root):
        backing, emulator, wrapper = (root / name for name in ('pb', 'qemu', 'wrapper'))
        backing.write_bytes(b'\x7fELFbacking')
        emulator.write_bytes(b'\x7fELFemulator')
        wrapper.write_text(vm.wrapper_text(backing, emulator, 'qemu64'))
        vm.save(wrapper.with_suffix('.provenance.json'), {
            'schema': 1, 'model': 'qemu64', 'backing_binary': vm.file_identity(backing),
            'emulator': vm.file_identity(emulator)})
        return backing, emulator, wrapper

    def test_unchanged_wrapper_cannot_hide_changed_backing_elf_or_emulator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backing, emulator, wrapper = self.wrapper(root)
            identity = vm.capture_identity(wrapper)
            self.assertEqual(identity['backing_binary'], vm.file_identity(backing))
            for path in (backing, emulator):
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(RuntimeError, 'changed since wrapper creation'):
                    vm.capture_identity(wrapper)
                path.write_bytes(original)
            wrapper.write_text(wrapper.read_text() + '# changed\n')
            with self.assertRaisesRegex(ValueError, 'execution manifest'):
                vm.capture_identity(wrapper)

    def test_reference_or_backing_change_during_capture_is_a_failed_report(self):
        for changed in ('reference', 'backing'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                backing, _emulator, wrapper = self.wrapper(root)
                reference = root / 'reference'
                reference.write_bytes(b'\x7fELFreference')

                def sample(*_args):
                    path = reference if changed == 'reference' else backing
                    path.write_bytes(path.read_bytes() + b'changed')
                    return {}

                argv = ['portability_vm.py', 'offline', '--binary', str(wrapper),
                        '--reference', str(reference), '--out', str(root / 'results')]
                with patch.object(sys, 'argv', argv), \
                        patch.object(vm.phase1, 'corpus', return_value=[{'name': 'probe', 'argv': []}]), \
                        patch.object(vm.phase1, 'sample', side_effect=sample):
                    with self.assertRaisesRegex(RuntimeError, 'changed'):
                        vm.main()
                report = json.loads((root / 'results/report.json').read_text())
                self.assertEqual(report['status'], 'failed')
                self.assertIn('changed', report['errors'][0])

    def test_unregistered_scripts_have_no_verified_execution_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wrapper'
            path.write_text('#!/bin/sh\nexec /bin/true "$@"\n')
            with self.assertRaises(FileNotFoundError):
                vm.capture_identity(path)


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'pidfd_open'), 'Linux descendant supervision')
class DescendantCleanupTests(unittest.TestCase):
    def test_timeout_and_cancellation_reap_cli_in_separate_session(self):
        supervisor = vm.ROOT / 'harness/effects_supervisor.py'
        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as directory:
                pidfile = Path(directory) / 'cli.pid'
                child = ('import os,signal,time; '
                         'signal.signal(signal.SIGHUP,signal.SIG_IGN); '
                         'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                         f'open({str(pidfile)!r},"w").write(str(os.getpid())); time.sleep(60)')
                fixture = ('import subprocess,sys,time; '
                           f'subprocess.Popen([sys.executable,"-c",{child!r}],start_new_session=True); '
                           'time.sleep(60)')
                foreign = subprocess.Popen(['/bin/sleep', '60'])
                proc = subprocess.Popen([sys.executable, '-I', str(supervisor), '--timeout',
                                         '5' if cancel else '1', '--parent', str(os.getpid()), '--',
                                         sys.executable, '-I', '-c', fixture],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                fd = None
                try:
                    deadline = time.monotonic() + 3
                    while not pidfile.exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(pidfile.exists(), 'fixture CLI did not start')
                    pid = int(pidfile.read_text())
                    fd = os.pidfd_open(pid)
                    if cancel:
                        proc.terminate()
                    _stdout, stderr = proc.communicate(timeout=8)
                    self.assertEqual(proc.returncode, 124, stderr)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                    self.assertIsNone(foreign.poll(), 'unrelated process was signalled')
                finally:
                    if fd is not None:
                        with contextlib.suppress(ProcessLookupError):
                            signal.pidfd_send_signal(fd, signal.SIGKILL)
                        os.close(fd)
                    if proc.poll() is None:
                        proc.kill()
                    proc.communicate(timeout=3)
                    foreign.kill()
                    foreign.wait()

    def test_successful_fixture_is_not_reported_as_a_timeout(self):
        proc = subprocess.run([sys.executable, '-I', str(vm.ROOT / 'harness/effects_supervisor.py'),
                               '--timeout', '2', '--parent', str(os.getpid()), '--',
                               sys.executable, '-I', '-c', 'print("fixture completed")'],
                              capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b'fixture completed\n')


if __name__ == '__main__':
    unittest.main()
