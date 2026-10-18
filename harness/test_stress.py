"""Admission and comparison tests for the bounded SQL stress orchestrator."""
import base64
import copy
import hashlib
import unittest
from unittest import mock

import stress
from stress_process import ArrayObjectScanner


def sample(rows=10, output=b'[{}]', stderr=b'', exit_code=0):
    def stream(b):
        return {'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest(),
                'prefix_base64': base64.b64encode(b).decode()}
    s = {'case': 'test', 'label': 'go', 'warmup': False,
         'stdout': stream(output), 'stderr': stream(stderr),
         'native_metrics': {'exit_code': exit_code, 'resource_source': 'native_child_wait4', 'peak_rss_bytes': 100},
         'json_rows': {'complete': True, 'completed_rows': rows},
         'fixture_stats': {'query_requests': 1, 'request_body_sha256': 'abc', 'completed': True},
         'outcome': 'completed', 'endpoint': 'http://127.0.0.1:1234', 'sandbox': '/tmp/sandbox',
         'rates': {k: 1 for k in ('wall_ms', 'cpu_ms', 'rows_per_second', 'cpu_ms_per_million_rows', 'output_mib_per_second')},
         'issues': []}
    s['contract'] = stress.contract(s)
    return s


class StressTests(unittest.TestCase):
    def test_binary_labels_default_to_go_and_rust_and_allow_same_engine_comparison(self):
        argv = ['--go', '/reference', '--rust', '/candidate', '--out', '/report']
        with mock.patch.object(stress, 'run', return_value=0) as run:
            self.assertEqual(stress.main(argv), 0)
            args = run.call_args.args[0]
            self.assertEqual((args.reference_label, args.candidate_label), ('go', 'rust'))
            self.assertEqual(stress.main(argv + ['--reference-label', 'baseline', '--candidate-label', 'candidate']), 0)
            args = run.call_args.args[0]
            self.assertEqual((args.reference_label, args.candidate_label), ('baseline', 'candidate'))

    def test_binary_labels_reject_duplicate_and_unsafe_artifact_paths(self):
        argv = ['--go', '/reference', '--rust', '/candidate', '--out', '/report']
        with mock.patch.object(stress, 'run') as run, mock.patch('sys.stderr'):
            for labels in (['--reference-label', 'rust'],
                           ['--candidate-label', '../candidate'],
                           ['--reference-label', '.'],
                           ['--candidate-label', 'candidate/name']):
                with self.assertRaises(SystemExit):
                    stress.main(argv + labels)
            run.assert_not_called()

    def test_summary_preserves_arbitrary_binary_labels(self):
        a, b = sample(), sample()
        a['label'], b['label'] = 'baseline', 'candidate'
        result = stress.summarize([a, b], [stress.case('test')], ['baseline', 'candidate'])['test']
        self.assertTrue(result['compatible'])
        self.assertEqual(set(result['medians']), {'baseline', 'candidate'})

    def test_deadline_initial_output_difference_fails_even_with_equal_error_contract(self):
        c = stress.case('test', expectation='deadline')
        a = sample(output=b'[{"i":0}', stderr=b'context deadline exceeded', exit_code=1)
        b = copy.deepcopy(a)
        b['label'] = 'rust'
        self.assertTrue(stress.summarize([a, b], [c], ['go', 'rust'])['test']['compatible'])
        b['stdout']['prefix_base64'] = base64.b64encode(b'[{"i":1}').decode()
        self.assertFalse(stress.summarize([a, b], [c], ['go', 'rust'])['test']['compatible'])

    def test_deadline_accepts_whole_rows_without_final_array_bracket(self):
        c = stress.cases('deadline')[0]
        s = sample(rows=1, stderr=b'context deadline exceeded', exit_code=1)
        s['native_metrics'].update(started_monotonic_ns=0, ended_monotonic_ns=60000000000)
        scanner = ArrayObjectScanner()
        scanner.feed(b'[{"i":0,"s":"x"}', 1)
        s['json_rows'] = scanner.result()
        self.assertEqual(stress.check_sample(s, c), [])
        scanner.feed(b',{"i":1,"s":"unfinished', 2)
        s['json_rows'] = scanner.result()
        self.assertTrue(stress.check_sample(s, c))

    def test_profiles_are_bounded_descriptions_not_expanded_payloads(self):
        for profile in ('smoke', 'scale', 'soak', 'extended', 'large', 'transport', 'backpressure', 'deadline'):
            suite = stress.cases(profile)
            self.assertEqual(len({s['name'] for s in suite}), len(suite))
            self.assertLess(len(str(suite)), 10000)
            self.assertTrue(all(0 <= s['fixture']['rows'] for s in suite))
            self.assertTrue(all(20 <= s['fixture']['row_bytes'] <= 16 * 1024**2 for s in suite))
        self.assertEqual(stress.cases('scale')[1]['fixture']['rows'], 10000000)
        self.assertEqual(stress.cases('extended')[0]['fixture']['rows'] * 1024, 10 * 1024**3)

    def test_success_checks_source_and_output_independently(self):
        c = stress.case('test', rows=10)
        s = sample()
        self.assertEqual(stress.check_sample(s, c), [])
        for key, value in [('complete', False), ('completed_rows', 9)]:
            bad = copy.deepcopy(s)
            bad['json_rows'][key] = value
            self.assertTrue(stress.check_sample(bad, c))
        bad = copy.deepcopy(s)
        bad['fixture_stats']['completed'] = False
        self.assertTrue(stress.check_sample(bad, c))

    def test_missing_metrics_or_query_identity_fails(self):
        s = sample()
        s['native_metrics'] = {}
        s['fixture_stats'] = {}
        self.assertGreaterEqual(len(stress.check_sample(s, stress.case('test', rows=10))), 3)

    def test_negative_cases_require_actual_error(self):
        c = stress.case('test', expectation='error')
        self.assertTrue(stress.check_sample(sample(), c))
        self.assertEqual(stress.check_sample(sample(stderr=b'bad JSON', exit_code=1), c), [])

    def test_only_known_endpoint_and_sandbox_are_normalized(self):
        a = sample(stderr=b'Get http://127.0.0.1:1234: error /tmp/sandbox')
        b = sample(stderr=b'Get http://127.0.0.1:5678: error /tmp/other')
        b.update(endpoint='http://127.0.0.1:5678', sandbox='/tmp/other')
        self.assertEqual(stress.normalize_error(a), stress.normalize_error(b))
        b['stderr'] = sample(stderr=b'different error')['stderr']
        self.assertNotEqual(stress.normalize_error(a), stress.normalize_error(b))

    def test_truncated_error_sample_is_not_treated_as_whole_error(self):
        s = sample(stderr=b'large error')
        s['stderr']['prefix_base64'] = base64.b64encode(b'large').decode()
        self.assertEqual(stress.normalize_error(s)['normalization'], 'unavailable')

    def test_identical_but_invalid_runs_do_not_pass(self):
        a = sample()
        b = copy.deepcopy(a)
        b['label'] = 'rust'
        a['issues'] = b['issues'] = ['truncated']
        r = stress.summarize([a, b], [stress.case('test')], ['go', 'rust'])['test']
        self.assertFalse(r['valid'])
        self.assertFalse(r['compatible'])

    def test_mismatch_and_missing_binary_fail(self):
        a = sample()
        b = sample(output=b'wrong')
        b['label'] = 'rust'
        c = [stress.case('test')]
        self.assertFalse(stress.summarize([a, b], c, ['go', 'rust'])['test']['compatible'])
        self.assertFalse(stress.summarize([a], c, ['go', 'rust'])['test']['valid'])

    def test_interruption_is_observation_never_parity(self):
        a = sample(exit_code=-13)
        b = sample(exit_code=1)
        b['label'] = 'rust'
        r = stress.summarize([a, b], [stress.case('test', expectation='observation')], ['go', 'rust'])['test']
        self.assertIsNone(r['compatible'])
        self.assertTrue(r['observation_only'])
        self.assertTrue(r['valid'])

    def test_invalid_numbers_rejected(self):
        for s in ('nan', 'inf', '-1', '0'):
            with self.assertRaises(Exception):
                stress.positive(s)

    def test_null_sink_requires_prior_matching_source_validation(self):
        s = sample()
        s.update(stdout_sink='null', output_eof={'stdout': None, 'stderr': True})
        s['stdout'].update(available=False, bytes=None, sha256=None)
        s['fixture_stats'].update(rows_written=10, response_sha256='source')
        c = stress.case('test', rows=10, scan_json=False)
        self.assertTrue(stress.check_sample(s, c))
        s['validated_source'] = {'response_sha256': 'wrong'}
        self.assertTrue(stress.check_sample(s, c))
        s['validated_source']['response_sha256'] = 'source'
        s['validated_source']['stdout_bytes'] = 1000
        s['native_metrics']['proc_io_wchar'] = 999
        self.assertTrue(stress.check_sample(s, c))
        s['native_metrics']['proc_io_wchar'] = 1000
        self.assertEqual(stress.check_sample(s, c), [])

    def test_cleanup_and_eof_are_required_even_with_matching_output(self):
        for field, value in [('meter_unreaped', True), ('process_group_remaining', True),
                             ('outcome', 'post_exit_drain_limit'),
                             ('output_eof', {'stdout': False, 'stderr': True})]:
            s = sample()
            s[field] = value
            self.assertTrue(stress.check_sample(s, stress.case('test', rows=10)))

    def test_pacing_or_observer_saturation_prevents_performance_admission(self):
        a, b = sample(), sample()
        b['label'] = 'rust'
        c = stress.case('test')
        a['observer'] = {'observer_bound': True}
        r = stress.summarize([a, b], [c], ['go', 'rust'])['test']
        self.assertTrue(r['compatible'])
        self.assertFalse(r['performance_admitted'])
        a['observer']['observer_bound'] = False
        c['fixture']['chunk_delay_ms'] = 10
        self.assertFalse(stress.summarize([a, b], [c], ['go', 'rust'])['test']['performance_admitted'])
        for control in ({'flush': True}, {'fragment_pattern': [1, 7, 1400]}):
            c = stress.case('test', **control)
            self.assertFalse(stress.summarize([a, b], [c], ['go', 'rust'])['test']['performance_admitted'])


if __name__ == '__main__':
    unittest.main()
