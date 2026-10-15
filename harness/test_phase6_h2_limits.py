import base64
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import phase6_h2_limits as limits
from test_phase6_protocol_limits import measured as protocol_capture


def measured(case, role):
    view = copy.deepcopy(case)
    view['protocol_limit'] = case['h2_limit']
    return protocol_capture(view, role)


class H2LimitTests(unittest.TestCase):
    def run_mock_main(self, case, objection=None, affected_role=None):
        limits.p6.TMP.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=limits.p6.TMP) as tmp:
            root = Path(tmp)
            paths = {}
            for role in ('go', 'candidate', 'fixture'):
                paths[role] = root / role
                paths[role].write_bytes(('never executed: ' + role).encode())
            identities = {role: limits.p6.run.digest(path.read_bytes()) for role, path in paths.items()}

            def sample(binary, fixture, selected, folder):
                role = 'go' if binary == paths['go'] else 'candidate'
                capture = measured(selected, role)
                capture.update(binary_sha256=identities[role], fixture_sha256=identities['fixture'])
                if folder.name == affected_role:
                    if objection == 'repeat':
                        capture['failure'] = 'independent timeout control'
                    elif objection == 'binary':
                        capture['binary_sha256'] = 'changed executable'
                    elif objection == 'fixture':
                        capture['fixture_sha256'] = 'changed fixture'
                folder.mkdir(parents=True, exist_ok=False)
                limits.p6.run.write_json(folder / 'result.json', capture)
                return capture

            out = root / 'report'
            argv = sum((['--' + role, str(path)] for role, path in paths.items()), []) + ['--out', str(out)]
            with patch.dict(limits.p6.ORACLE_HASHES, standard=identities['go']), \
                 patch.object(limits.protocol, 'FIXTURE_SHA256', identities['fixture']), \
                 patch.object(limits, 'cases', return_value=[case]), \
                 patch.object(limits.p6, 'sample', side_effect=sample) as mocked_sample, \
                 contextlib.redirect_stdout(io.StringIO()):
                status = limits.main(argv)
            self.assertEqual(mocked_sample.call_count, 4)
            report = json.loads((out / 'report.json').read_text())
            entry = report['cases'][case['name']]
            self.assertTrue(report['complete'])
            self.assertTrue(report['identities_unchanged'])
            self.assertFalse(report['full_parity_passed'])
            self.assertEqual(entry['excluded_from_full_parity'], bool(case['h2_limit']['id']))
            self.assertEqual(report['failures'], int(objection is not None))
            self.assertEqual(status, int(objection is not None))
            self.assertEqual(bool(entry['issues']), objection is not None)
            if objection == 'repeat':
                self.assertIn(affected_role + ': collector failure: independent timeout control', entry['issues'])
            elif objection is not None:
                self.assertIn(affected_role + ': executable changed', entry['issues'])
            for key, is_limit in [('declared_limitation_verified', True), ('control_remaining_contract_passed', False)]:
                expected = bool(case['h2_limit']['id']) == is_limit and objection is None
                self.assertEqual(entry[key], expected)
                self.assertEqual(report[key], int(expected))

    def test_main_conforming_limits_and_controls_keep_consistent_success_flags(self):
        for case in limits.cases():
            with self.subTest(case=case['name']):
                self.run_mock_main(case)

    def test_main_repeat_objections_clear_both_success_flags(self):
        for case in limits.cases():
            for role in ('go_repeat', 'candidate_repeat'):
                with self.subTest(case=case['name'], role=role):
                    self.run_mock_main(case, 'repeat', role)

    def test_main_executable_objections_clear_both_success_flags(self):
        for case in limits.cases():
            for role in ('go', 'go_repeat', 'candidate', 'candidate_repeat'):
                for objection in ('binary', 'fixture'):
                    with self.subTest(case=case['name'], role=role, objection=objection):
                        self.run_mock_main(case, objection, role)

    def test_six_cases_only_two_enumerated_invalid_header_rules(self):
        cases = limits.cases()
        self.assertEqual(len(cases), 6)
        self.assertFalse({c['name'] for c in cases} & {c['name'] for c in limits.p6.corpus()})
        self.assertEqual({c['h2_limit']['id'] for c in cases}, {None, 'H2-CL-1', 'H2-CL-2'})
        for case in cases:
            self.assertEqual(case['expected']['dogets'], 1)
            self.assertEqual(case['timeout'], 5)
            acts = case['fixture']['phase6']['endpoints'][0]['subscriptions'][0]['acts']
            self.assertEqual([a['act'] for a in acts], ['headers', 'schema', 'batch', 'trailers'])

    def test_limits_never_count_as_control_or_parity(self):
        for case in limits.cases():
            entry = limits.classify(case, measured(case, 'go'), measured(case, 'candidate'))
            self.assertEqual(entry['issues'], [])
            is_limit = case['h2_limit']['id'] is not None
            self.assertEqual(entry['declared_limitation_verified'], is_limit)
            self.assertEqual(entry['excluded_from_full_parity'], is_limit)
            self.assertEqual(entry['control_remaining_contract_passed'], not is_limit)
            self.assertTrue(entry['wire_differences'])

    def test_changed_output_stage_envelope_and_process_are_not_waived(self):
        for case in (c for c in limits.cases() if c['h2_limit']['id']):
            go = measured(case, 'go')
            for mutation in ('output', 'error', 'whitespace', 'signal', 'timeout'):
                rust = measured(case, 'candidate')
                if mutation == 'output':
                    rust.update(stdout_bytes=1, stdout_sha256=limits.p6.run.digest(b'x'))
                elif mutation == 'error':
                    rust['stderr_contract_b64'] = go['stderr_contract_b64']
                elif mutation == 'whitespace':
                    raw = base64.b64decode(rust['stderr_contract_b64']) + b'\n'
                    rust['stderr_contract_b64'] = base64.b64encode(raw).decode()
                elif mutation == 'signal':
                    rust['raw_wait_status'] = 15
                else:
                    rust['failure'] = 'timeout'
                with self.subTest(case=case['name'], mutation=mutation):
                    self.assertTrue(limits.classify(case, go, rust)['issues'])

    def test_auth_request_authority_config_and_ua_still_bound(self):
        case = next(c for c in limits.cases() if c['h2_limit']['id'])
        go = measured(case, 'go')
        for mutation in ('auth', 'ticket', 'requests', 'authority', 'config', 'ua'):
            rust = measured(case, 'candidate')
            if mutation == 'auth':
                rust['requests'][1]['headers'] = {}
            elif mutation == 'ticket':
                rust['requests'][1]['body'] = '{}'
            elif mutation == 'requests':
                rust['requests'].append(copy.deepcopy(rust['requests'][1]))
            elif mutation == 'authority':
                rust['flight_authorities'] = [['127.0.0.1:1']]
            elif mutation == 'config':
                rust['config_after'] = {'changed': True}
            else:
                rust['flight_user_agents'] = [['tonic/other']]
            with self.subTest(mutation=mutation):
                self.assertTrue(limits.classify(case, go, rust)['issues'])


if __name__ == '__main__':
    unittest.main()
