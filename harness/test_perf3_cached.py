"""Small local tests for cached response admission, serving and capture ordering."""
import argparse
import copy
import errno
import hashlib
import io
import json
import os
import shutil
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

import perf3_cached as cached
import run as harness
import stress


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    """urllib response stand-in: yields blocks, then EOF or a mid-body error."""
    def __init__(self, blocks, error=None):
        self.blocks, self.error = list(blocks), error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _):
        if self.blocks:
            return self.blocks.pop(0)
        if self.error:
            raise self.error
        return b''


def make_manifest(root):
    raw = b'[{"i":0,"s":"xxxxxx"}]'
    body = root / 'body.json'
    body.write_bytes(raw)
    body.chmod(0o444)
    digest = hashlib.sha256(raw).hexdigest()
    manifest = {'schema': cached.SCHEMA, 'fixture_config': {'rows': 1},
                'body': {'path': str(body), 'size_bytes': len(raw), 'rows': 1,
                         'sha256': digest, 'read_only': True},
                'provenance': {'fixture': {'path': '/original-fixture', 'sha256': 'a' * 64, 'size_bytes': 1},
                    'fixture_stats': {'query_requests': 1, 'completed': True, 'rows_written': 1,
                                      'source_bytes': len(raw), 'response_sha256': digest},
                    'framing_validation': {'complete': True, 'completed_rows': 1},
                    'request_body_sha256': hashlib.sha256(cached.query_body(1)).hexdigest()}}
    path = root / 'manifest.json'
    harness.write_json(path, manifest)
    return path, manifest, raw


class CachedAdmissionTests(unittest.TestCase):
    def test_unsupported_transport_or_failure_controls_are_rejected(self):
        for options in ({'gzip': True}, {'chunk_delay_ms': 1}, {'chunk_bytes': 1},
                        {'fragment_pattern': [1, 2]}, {'flush': True}, {'failure': 'truncate'},
                        {'fail_after_rows': 1}, {'unknown': True}, {'rows': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                cached.validate_config({'rows': 1, **options})

    def test_manifest_requires_matching_hash_size_source_rows_and_framing(self):
        with tempfile.TemporaryDirectory() as directory:
            path, manifest, raw = make_manifest(Path(directory))
            admitted, source, _ = cached.load_body(path)
            source.close()
            self.assertEqual(admitted['body']['sha256'], hashlib.sha256(raw).hexdigest())
            for field, value in [('rows_written', 2), ('source_bytes', 1),
                                 ('response_sha256', 'b' * 64), ('completed', False)]:
                bad = copy.deepcopy(manifest)
                bad['provenance']['fixture_stats'][field] = value
                harness.write_json(path, bad)
                with self.assertRaisesRegex(ValueError, 'provenance'):
                    cached.load_body(path)
            bad = copy.deepcopy(manifest)
            bad['provenance']['framing_validation']['completed_rows'] = 0
            harness.write_json(path, bad)
            with self.assertRaisesRegex(ValueError, 'provenance'):
                cached.load_body(path)

    def test_writable_or_modified_cached_file_is_rejected_before_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            path, manifest, raw = make_manifest(Path(directory))
            body = Path(manifest['body']['path'])
            body.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'read-only'):
                cached.load_body(path)
            body.write_bytes(raw.replace(b'xxxxxx', b'yyyyyy'))
            body.chmod(0o444)
            with self.assertRaisesRegex(ValueError, 'hash/size'):
                cached.load_body(path)

    def test_transfer_sendfile_and_partial_sendfile_fallback_preserve_exact_bytes(self):
        raw = b'test-data' * 32
        for fallback in (False, True):
            with tempfile.TemporaryFile() as source:
                sender, reader = socket.socketpair()
                try:
                    source.write(raw)
                    source.flush()
                    original = os.sendfile
                    calls = []

                    def sendfile(out_fd, in_fd, offset, count):
                        calls.append(count)
                        if fallback and len(calls) > 1:
                            raise OSError(errno.EINVAL, 'unsupported')
                        return original(out_fd, in_fd, offset, min(count, 7) if fallback else count)

                    with mock.patch.object(os, 'sendfile', side_effect=sendfile):
                        result = cached.transfer(sender, source, len(raw))
                    sender.shutdown(socket.SHUT_WR)
                    received = bytearray()
                    while block := reader.recv(4096):
                        received.extend(block)
                    self.assertEqual(bytes(received), raw)
                    self.assertEqual(result['bytes'], len(raw))
                    self.assertEqual(result['transfer'], 'sendfile_then_bounded_pread_sendall' if fallback else 'sendfile')
                    self.assertTrue(all(count <= cached.CHUNK for count in calls))
                finally:
                    sender.close()
                    reader.close()

    def transfer_file(self, size):
        source = tempfile.TemporaryFile()
        source.write(os.urandom(size))
        source.flush()
        source.seek(0)
        return source

    def test_transfer_waits_through_eagain_for_slow_reader_on_timeout_socket(self):
        size = 16 << 20
        with self.transfer_file(size) as source:
            raw = source.read()
            sender, reader = socket.socketpair()
            received, failures = bytearray(), []

            def drain():
                try:
                    time.sleep(.2)
                    while block := reader.recv(1 << 20):
                        received.extend(block)
                except BaseException as error:
                    failures.append(error)

            thread = threading.Thread(target=drain)
            try:
                # Timeout mode makes the socket non-blocking at the OS level.
                sender.settimeout(5)
                thread.start()
                result = cached.transfer(sender, source, size)
                sender.shutdown(socket.SHUT_WR)
                thread.join(10)
            finally:
                sender.close()
                thread.join(10)
                reader.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(bytes(received), raw)
            self.assertEqual(result['bytes'], size)
            self.assertEqual(result['transfer'], 'sendfile')
            self.assertGreater(result['writable_waits'], 0)

    def test_transfer_stalled_reader_raises_timeout_within_socket_bound(self):
        with self.transfer_file(16 << 20) as source:
            sender, reader = socket.socketpair()
            try:
                sender.settimeout(.3)
                start = time.monotonic()
                with self.assertRaises(TimeoutError):
                    cached.transfer(sender, source, 16 << 20)
                self.assertLess(time.monotonic() - start, 3)
            finally:
                sender.close()
                reader.close()

    def test_config_body_bound_is_capped(self):
        cached.validate_config({'rows': 1000, 'row_bytes': 1 << 20})
        for config in ({'rows': 2**63 - 1}, {'rows': 3000, 'row_bytes': 1 << 20}):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, 'cap'):
                cached.validate_config(config)
        self.assertGreaterEqual(cached.body_bound({'rows': 3, 'row_bytes': 20}),
                                len(b'[' + b','.join([b'{"i":0,"s":"xxxxxxxx"}'] * 3) + b']'))

    def prepare_with(self, root, response, stats):
        config = root / 'config.json'
        harness.write_json(config, {'rows': 1})
        args = argparse.Namespace(config=config, fixture=root / 'fixture', out=root / 'prepared', timeout=5)
        fixture = mock.MagicMock(url='http://127.0.0.1:1')
        fixture.stats.return_value = stats
        with mock.patch.object(stress, 'identity', return_value={'path': '/f', 'sha256': 'a' * 64, 'size_bytes': 1}), \
             mock.patch.object(stress, 'Fixture', return_value=fixture), \
             mock.patch.object(urllib.request, 'urlopen', return_value=response), \
             mock.patch('sys.stdout', new=io.StringIO()):
            result = cached.prepare(args)
        fixture.close.assert_called_once()
        return args, result

    def test_failed_prepare_removes_only_its_partial_body(self):
        raw = b'[{"i":0,"s":"xxxxxxxx"}]'
        good = {'query_requests': 1, 'completed': True, 'rows_written': 1,
                'source_bytes': len(raw), 'response_sha256': hashlib.sha256(raw).hexdigest()}
        cases = [('read', FakeResponse([raw[:5]], ConnectionResetError('reset')), good, ConnectionResetError),
                 ('admission', FakeResponse([raw]), {**good, 'rows_written': 2}, ValueError),
                 # Longer than the conservative 1-row bound: aborted while streaming.
                 ('bound', FakeResponse([raw + b' ' * 32]), good, ValueError)]
        for name, response, stats, error in cases:
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises(error):
                    self.prepare_with(root, response, stats)
                self.assertTrue((root / 'prepared').is_dir())
                self.assertFalse((root / 'prepared' / 'body.json').exists())
                self.assertFalse((root / 'prepared' / 'manifest.json').exists())

    def test_prepare_records_bound_sources_and_interpreter(self):
        raw = b'[{"i":0,"s":"xxxxxxxx"}]'
        stats = {'query_requests': 1, 'completed': True, 'rows_written': 1,
                 'source_bytes': len(raw), 'response_sha256': hashlib.sha256(raw).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            args, result = self.prepare_with(Path(directory), FakeResponse([raw]), stats)
            self.assertEqual(result, 0)
            provenance = cached.read_json(args.out / 'manifest.json')['provenance']
            self.assertIn(str(Path(cached.__file__).with_name('run.py').resolve()), provenance['harness_sources'])
            self.assertEqual(provenance['python']['executable'], cached.sys.executable)
            self.assertGreaterEqual(provenance['size_bound_bytes'], len(raw))
            self.assertIn('prepared_at', provenance)

    def test_cached_fixture_auth_hash_and_stats_match_stress_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, manifest, raw = make_manifest(root)
            config = {**manifest['fixture_config'], 'cached_manifest': str(path)}
            fixture = stress.Fixture(Path(cached.__file__).resolve(), config, root / 'server')
            try:
                request = urllib.request.Request(fixture.url + '/api/v1/query', data=cached.query_body(1))
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, 401)
                error.exception.close()
                request.add_header('Authorization', stress.AUTH)
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(response.read(), raw)
                stats = fixture.stats()
                self.assertTrue(stats['completed'])
                self.assertEqual(stats['rows_written'], 1)
                self.assertEqual(stats['response_sha256'], manifest['body']['sha256'])
                self.assertEqual(stats['last_query']['auth'], 'ok')
                self.assertEqual(stats['last_query']['body_sha256'], hashlib.sha256(cached.query_body(1)).hexdigest())
                self.assertEqual(stats['transfer'], 'sendfile')
                self.assertEqual(stats['response_sha256_basis'], cached.HASH_BASIS)
                ready = cached.read_json(root / 'server' / 'fixture-ready.json')
                self.assertEqual(Path(ready['python']['executable']).resolve(), Path(shutil.which('python3')).resolve())
            finally:
                fixture.close()
            self.assertIsNone(fixture.proc)

    @unittest.skipUnless((ROOT / 'bin/pb-stress-fixture').is_file(), 'existing original fixture required')
    def test_prepare_small_body_uses_original_fixture_then_replays_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            harness.write_json(config, {'rows': 3, 'row_bytes': 32, 'shape': 'escaped'})
            args = argparse.Namespace(config=config, fixture=ROOT / 'bin/pb-stress-fixture',
                                      out=root / 'prepared', timeout=5)
            with mock.patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(cached.prepare(args), 0)
            manifest, source, _ = cached.load_body(args.out / 'manifest.json')
            try:
                self.assertEqual(manifest['body']['rows'], 3)
                self.assertEqual(manifest['provenance']['fixture']['sha256'], stress.identity(args.fixture)['sha256'])
                scanner = cached.ArrayObjectScanner()
                scanner.feed(source.read(), 0)
                self.assertEqual(scanner.result()['completed_rows'], 3)
            finally:
                source.close()


class CachedRunTests(unittest.TestCase):
    def run_setup(self, root):
        path, manifest, raw = make_manifest(root)
        script = Path(cached.__file__).resolve()
        args = argparse.Namespace(manifest=path, out=root / 'results', go=script, reference=script,
            candidate=script, meter=script, repetitions=2, warmup=1, seed=2063,
            sample_interval=.05, timeout=5)
        digest = hashlib.sha256(raw + b'\n').hexdigest()
        calibration = {'valid': True, 'sha256': manifest['body']['sha256'],
                       'text_output_sha256': digest, 'source_mib_per_second': 100000}

        def capture(*_, **kwargs):
            return {'issues': [], 'contract': {'output': 'same'},
                    'stdout': {'sha256': digest, 'bytes': len(raw) + 1},
                    'fixture_stats': {'response_sha256': manifest['body']['sha256']},
                    'rates': {key: 1 for key in ('wall_ms', 'cpu_ms', 'rows_per_second',
                                               'cpu_ms_per_million_rows', 'output_mib_per_second')},
                    'native_metrics': {'peak_rss_bytes': 100}}
        return args, calibration, capture

    def test_calibration_and_all_three_pipe_validations_precede_paired_native_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            args, calibration, capture = self.run_setup(Path(directory))
            with mock.patch.object(stress, 'calibrate', return_value=calibration), \
                 mock.patch.object(stress, 'run_sample', side_effect=capture) as measured, \
                 mock.patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(cached.run(args), 0)
            self.assertEqual(measured.call_count, 9)
            self.assertTrue(all('stdout_sink' not in call.kwargs for call in measured.call_args_list[:3]))
            for call in measured.call_args_list[3:]:
                self.assertEqual(call.kwargs['stdout_sink'], 'null')
                self.assertIn('stdout_bytes', call.kwargs['validated_source'])
            report = cached.read_json(args.out / 'report.json')
            self.assertTrue(report['passed'])
            self.assertEqual(len(report['paired']['rounds']), 2)
            self.assertEqual(set(report['summary']['medians']), {'baseline', 'candidate'})
            self.assertEqual(report['method']['wire_framing'], cached.WIRE_FRAMING)
            self.assertEqual(report['method']['calibration_reader'], cached.CALIBRATION_READER)
            self.assertIn(str(Path(cached.__file__).with_name('run.py').resolve()), report['sources'])
            self.assertEqual(report['manifest_identity_end'], report['manifest_identity'])

    def test_zero_native_wall_is_recorded_without_crash_and_withholds_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            args, calibration, capture = self.run_setup(Path(directory))
            calls = []

            def zero_wall(*arguments, **kwargs):
                result = capture(*arguments, **kwargs)
                calls.append(1)
                if len(calls) == 6:
                    result['rates']['wall_ms'] = 0
                return result

            with mock.patch.object(stress, 'calibrate', return_value=calibration), \
                 mock.patch.object(stress, 'run_sample', side_effect=zero_wall), \
                 mock.patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(cached.run(args), 1)
            report = cached.read_json(args.out / 'report.json')
            self.assertTrue(report['complete'])
            self.assertFalse(report['passed'])
            self.assertEqual(report['summary']['unmeasured_producer_comparison_runs'], 1)
            self.assertFalse(report['summary']['performance_admitted'])
            unmeasured = [s for s in report['samples'] if s['producer_comparison']['source_mib_per_second'] is None]
            self.assertEqual(len(unmeasured), 1)
            self.assertIn('nonpositive native wall time; producer comparison unmeasured', unmeasured[0]['issues'])

    def test_manifest_changed_during_run_fails_report(self):
        with tempfile.TemporaryDirectory() as directory:
            args, calibration, capture = self.run_setup(Path(directory))
            calls = []

            def mutate(*arguments, **kwargs):
                calls.append(1)
                if len(calls) == 9:
                    manifest = cached.read_json(args.manifest)
                    manifest['note'] = 'changed'
                    harness.write_json(args.manifest, manifest)
                return capture(*arguments, **kwargs)

            with mock.patch.object(stress, 'calibrate', return_value=calibration), \
                 mock.patch.object(stress, 'run_sample', side_effect=mutate), \
                 mock.patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(cached.run(args), 1)
            report = cached.read_json(args.out / 'report.json')
            self.assertFalse(report['passed'])
            self.assertIn('manifest identity changed during run', report['errors'])

    def test_pipe_mismatch_withholds_resource_runs_and_saves_partial_report(self):
        with tempfile.TemporaryDirectory() as directory:
            args, calibration, capture = self.run_setup(Path(directory))

            def mismatch(*arguments, **kwargs):
                result = capture(*arguments, **kwargs)
                result['stdout']['sha256'] = 'incorrect'
                return result

            with mock.patch.object(stress, 'calibrate', return_value=calibration), \
                 mock.patch.object(stress, 'run_sample', side_effect=mismatch) as measured, \
                 self.assertRaisesRegex(ValueError, 'timing withheld'):
                cached.run(args)
            self.assertEqual(measured.call_count, 3)
            report = cached.read_json(args.out / 'report.json')
            self.assertFalse(report['passed'])
            self.assertEqual(report['samples'], [])


if __name__ == '__main__':
    unittest.main()
