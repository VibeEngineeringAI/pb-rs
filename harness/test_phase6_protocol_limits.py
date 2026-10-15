"""Guard capability-limit classification against hiding output or stage changes."""

import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest

import phase6_protocol_limits as limits


def measured(case, role):
    expected = case['protocol_limit']['expected'][role]
    stdout = base64.b64decode(expected['stdout_b64'])
    stderr = base64.b64decode(expected['stderr_b64'])
    ua = 'grpc-go/1.65.0' if role == 'go' else 'tonic/0.14.6'
    return dict(failure=None, actions=[], raw_wait_status=256, termination={'exit': 1},
                stdout_bytes=len(stdout), stdout_sha256=limits.p6.run.digest(stdout),
                stdout_rows=expected['rows'], stdout_batches=expected['batches'],
                stderr_sha256=limits.p6.run.digest(stderr), stderr_contract_b64=expected['stderr_b64'],
                requests=[dict(protocol='http', method='GET', path=limits.p6.ABOUT,
                               headers=case['expected']['http_auth'][0], body='', unexpected=False),
                          dict(protocol='flight', method='DoGet', path='/arrow.flight.protocol.FlightService/DoGet',
                               headers=case['expected']['auth'], body=limits.p6.ticket(case['dataset']), unexpected=False)],
                events=[dict(kind='raw_h2_stream', metadata={'user-agent': [ua]})],
                flight_user_agents=[[ua]], flight_authorities=[['127.0.0.1:{{grpc_port}}']],
                proxy_requests=[], config_before={}, config_after={}, summary={},
                binary_sha256=limits.p6.ORACLE_HASHES['standard'])


class ProtocolLimitTests(unittest.TestCase):
    def test_four_cases_use_bounded_maintained_fixture_and_are_separate(self):
        cases = limits.cases()
        self.assertEqual(len(cases), 4)
        self.assertFalse({c['name'] for c in cases} & {c['name'] for c in limits.p6.corpus()})
        for case in cases:
            self.assertEqual(case['timeout'], 5)
            self.assertEqual(case['expected']['dogets'], 1)
            acts = case['fixture']['phase6']['endpoints'][0]['subscriptions'][0]['acts']
            self.assertEqual(acts[-1]['fields'][0], ['grpc-status', '3'])
            codes = [a['fields'][0][1] for a in acts if a['act'] == 'headers']
            self.assertEqual(codes, ['103', '200'] if case['protocol_limit']['informational'] else ['200'])

    def test_informational_cases_are_full_outcome_limits_never_parity_passes(self):
        for case in limits.cases():
            entry = limits.classify(case, measured(case, 'go'), measured(case, 'candidate'))
            self.assertEqual(entry['issues'], [])
            self.assertFalse(entry['strict_parity_passed'])  # Wire UA is retained.
            if case['protocol_limit']['informational']:
                self.assertTrue(entry['excluded_from_full_parity'])
                self.assertTrue(entry['declared_limitation_verified'])
                self.assertFalse(entry['control_remaining_contract_passed'])
                self.assertIn('stdout_bytes differs', entry['raw_differences'])
                self.assertIn('grpc_status differs', entry['outcome_differences'])
                self.assertIn('stage differs', entry['outcome_differences'])
            else:
                self.assertFalse(entry['excluded_from_full_parity'])
                self.assertTrue(entry['control_remaining_contract_passed'])
                self.assertEqual(entry['raw_differences'], ['flight_user_agents differs'])

    def test_matching_exit_one_cannot_hide_missing_output_or_wrong_terminal(self):
        case = next(c for c in limits.cases() if c['protocol_limit']['informational'])
        go = measured(case, 'go')
        for mutation in ('output', 'stderr', 'signal', 'ticket', 'auth', 'config', 'timeout'):
            rust = measured(case, 'candidate')
            if mutation == 'output':
                rust.update(stdout_bytes=0, stdout_rows=0, stdout_batches=0,
                            stdout_sha256=limits.p6.run.digest(b''))
            elif mutation == 'stderr': rust['stderr_contract_b64'] = go['stderr_contract_b64']
            elif mutation == 'signal': rust['raw_wait_status'] = 1
            elif mutation == 'ticket': rust['requests'][1]['body'] = '{}'
            elif mutation == 'auth': rust['requests'][1]['headers'] = {}
            elif mutation == 'config': rust['config_after'] = {'changed': True}
            else: rust['failure'] = 'timeout'
            with self.subTest(mutation=mutation):
                self.assertTrue(limits.classify(case, go, rust)['issues'])

    def test_no_generic_status_whitespace_or_wire_exemption(self):
        case = limits.cases()[0]
        go = measured(case, 'go')
        for mutation in ('ua', 'authority', 'stderr-whitespace', 'extra-request'):
            rust = measured(case, 'candidate')
            if mutation == 'ua': rust['flight_user_agents'] = [['tonic/new-version']]
            elif mutation == 'authority': rust['flight_authorities'] = [['different-host']]
            elif mutation == 'stderr-whitespace':
                rust['stderr_contract_b64'] = base64.b64encode(base64.b64decode(rust['stderr_contract_b64']) + b'\n').decode()
            else: rust['requests'].append(copy.deepcopy(rust['requests'][1]))
            with self.subTest(mutation=mutation):
                self.assertTrue(limits.classify(case, go, rust)['issues'])

    def test_cached_go_result_and_raw_bytes_must_be_unchanged(self):
        case = limits.cases()[0]
        result = measured(case, 'go')
        limits.p6.TMP.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=limits.p6.TMP) as root:
            root = Path(root)
            (root / 'stdout.bin').write_bytes(base64.b64decode(case['protocol_limit']['expected']['go']['stdout_b64']))
            (root / 'stderr.bin').write_bytes(base64.b64decode(result['stderr_contract_b64']))
            path = root / 'result.json'
            path.write_text(json.dumps(result))
            entry = dict(go_result_path=str(path), go_result_sha256=limits.p6.run.digest(path.read_bytes()))
            self.assertEqual(limits.verified_cached_go(entry, case)[0], result)
            (root / 'stdout.bin').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'raw stream changed'):
                limits.verified_cached_go(entry, case)
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'result changed'):
                limits.verified_cached_go(entry, case)


if __name__ == '__main__':
    unittest.main()
