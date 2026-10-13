import copy
import hashlib
import json
import unittest
from unittest.mock import patch
import phase4_workloads as workloads


class WorkloadsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = workloads.suite()
        cls.by_name = {case['name']: case for case in cls.cases}

    def test_sizes_and_modes(self):
        names = self.by_name
        self.assertEqual(len(names), 105)
        self.assertEqual(len(self.cases), len(names))
        for family, sizes in [('vector', ('1', '100', '10000')), ('matrix', ('1x100', '100x100', '1000x1000', '1x1000000')),
                              ('labels', ('10', '1000', '100000')), ('values', ('10', '1000', '100000')),
                              ('series', ('1', '1000', '100000')), ('saved-list', ('0', '100', '10000'))]:
            for size in sizes:
                for output in ('text', 'json'): self.assertIn(f'p4-{family}-{size}-{output}', names)
        families = {case['phase4']['family'] for case in self.cases}
        for family in families:
            for mode in ('headers', 'chunks', 'gzip', 'slow-stdout'):
                self.assertTrue(any(case['phase4']['family'] == family and case['name'].endswith('-'+mode) for case in self.cases))

    def test_million_point_shapes_and_frozen_delivery_representative(self):
        for shape, series, samples in [('1x1000000', 1, 1000000), ('1000x1000', 1000, 1000)]:
            case = self.by_name[f'p4-matrix-{shape}-json']
            result = json.loads(case['fixture']['routes'][0]['body'])['data']['result']
            self.assertEqual(len(result), series)
            self.assertTrue(all(len(row['values']) == samples for row in result))
            self.assertEqual(sum(len(row['values']) for row in result), 1000000)
            self.assertEqual(case['phase4']['count'], 1000000)
            self.assertEqual(self.by_name[f'p4-matrix-{shape}-text']['phase4']['count'], 1000000)
        base = self.by_name['p4-matrix-100x100-json']
        for mode in ('headers', 'chunks', 'gzip', 'slow-stdout'):
            variant = self.by_name[base['name']+'-'+mode]
            self.assertEqual(variant['fixture']['routes'][0]['body'], base['fixture']['routes'][0]['body'])
            self.assertEqual(variant['phase4'], base['phase4'])

    def test_matrix_count_order_hash(self):
        case = self.by_name['p4-matrix-100x100-json']
        payload = json.loads(case['fixture']['routes'][0]['body'])
        result = payload['data']['result']
        self.assertEqual(sum(len(row['values']) for row in result), 10000)
        self.assertEqual(result[-1]['values'][-1], [1704073140, '9999'])
        self.assertEqual(case['phase4']['stdout_sha256'], hashlib.sha256(workloads.encoded(payload)).hexdigest())
        result.reverse()
        self.assertNotEqual(case['phase4']['stdout_sha256'], hashlib.sha256(workloads.encoded(payload)).hexdigest())

    def test_mixed_saved_projection_and_requests(self):
        case = self.by_name['p4-saved-list-100-json']
        self.assertEqual(case['phase4']['count'], 50)
        self.assertEqual(case['expected_requests'], ['GET /api/v1/filters'])
        create = self.by_name['p4-saved-create-1-default']
        self.assertEqual(create['expected_requests'], ['POST /api/v1/filters'])
        self.assertNotIn('-o', create['argv'])
        for output in ('text', 'json'):
            self.assertEqual(self.by_name['p4-run-save-1-'+output]['expected_requests'], ['POST /api/v1/query', 'POST /api/v1/filters'])

    def test_cardinality_label_is_explicit(self):
        argv = self.by_name['p4-cardinality-label-values-20-json']['argv']
        self.assertEqual(argv[argv.index('--label') + 1], 'job')
        self.assertEqual(argv[argv.index('--dataset') + 1], 'metrics')

    def test_body_delivery_lower_bound_and_missing_evidence(self):
        import run
        result = dict(started_monotonic_ns=1000, first_stdout_ms=0.002)
        milestone = dict(final_write_started_monotonic_ns=2000,
                         final_body_monotonic_ns=4000, body_bytes_written=10,
                         uncompressed_body_bytes=20)
        # Output observed before the post-write callback is valid: the fixture
        # callback can run after the client has received and printed its body.
        self.assertEqual(run.phase4_body_observation(result, [milestone], 1, 'pipe'), [])
        self.assertTrue(result['output_after_final_write_started'])
        self.assertIsNone(result['body_before_output_verified'])
        self.assertEqual(result['fixture_wire_body_bytes'], 10)
        result['first_stdout_ms'] = 0.0005
        self.assertTrue(run.phase4_body_observation(result, [milestone], 1, 'pipe'))
        self.assertTrue(run.phase4_body_observation(result, [], 1, 'pipe'))
        incomplete = dict(milestone); incomplete.pop('final_write_started_monotonic_ns')
        self.assertTrue(run.phase4_body_observation(result, [incomplete], 1, 'pipe'))
        self.assertTrue(run.phase4_body_observation(result, [milestone], 2, 'pipe'))
        self.assertEqual(run.phase4_body_observation(result, [], 1, 'file'), [])

    def test_independent_stdout_rejects_matching_corrupted_outputs(self):
        import run
        output = 'café日志\n'.encode()
        expected = dict(expected_stdout_sha256=hashlib.sha256(output).hexdigest(),
                        expected_stdout_bytes=len(output))
        self.assertEqual(run.independent_stdout_issues(expected, output, output), [])
        # Identical corrupt output from two implementations must still fail.
        corrupt = b'x' * len(output)
        self.assertEqual(run.independent_stdout_issues(expected, corrupt, corrupt),
                         ['stdout differs from independent expected hash'])
        self.assertIn('stdout byte count differs from independent expectation',
                      run.independent_stdout_issues(expected, output[:-1], output[:-1]))
        # Count bytes, not Unicode code points, and hash normalized output.
        normalized = b'<HOME>\n'
        raw = b'/some/temporary/root\n'
        spec = dict(expected_stdout_sha256=hashlib.sha256(normalized).hexdigest(),
                    expected_stdout_bytes=len(raw))
        self.assertEqual(run.independent_stdout_issues(spec, raw, normalized), [])

    def test_unfrozen_oracle_and_corruption_fail(self):
        case = self.by_name['p4-vector-1-json']
        self.assertIn('phase4 oracle not frozen', workloads.validate(case, {}))
        case = copy.deepcopy(case); case['phase4']['oracle_required'] = False
        self.assertEqual(workloads.validate(case, {'stdout_sha256': case['phase4']['stdout_sha256']}), [])
        self.assertTrue(workloads.validate(case, {'stdout_sha256': 'bad'}))
        with self.assertRaises(ValueError): workloads.bind_oracle([], {'complete': True, 'passed': True, 'binaries': {'go': {'sha256': 'x'}}})

    def test_spool_hashes_complete_pipe_output(self):
        import run
        import sys
        import tempfile
        from pathlib import Path
        meter = Path(__file__).resolve().parent.parent / 'bin/pb-meter'
        if not meter.exists(): self.skipTest('prebuilt native meter unavailable')
        with tempfile.TemporaryDirectory() as directory:
            result, output, error = run.measure(
                [sys.executable, '-c', 'import os; os.write(1, b"x" * 200000); os.write(2, b"ok")'],
                run.isolated_env(Path(directory)), Path(directory), meter=meter,
                max_output=1 << 30, spool_output=True)
        self.assertIsNone(result['failure'])
        self.assertEqual(len(output), 200000)
        self.assertEqual(error, b'ok')
        self.assertEqual(result['stdout_raw_sha256'], hashlib.sha256(output).hexdigest())


if __name__ == '__main__': unittest.main()
