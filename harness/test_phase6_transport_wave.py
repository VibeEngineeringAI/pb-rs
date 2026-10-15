import base64
import copy
import unittest

import phase6_transport_wave as wave
from test_phase6_protocol_limits import measured as protocol_capture


def measured(case, role):
    want = case['transport_wave']
    view = copy.deepcopy(case)
    # The shared fake capture supplies independent auth/process/config fields.
    view['protocol_limit'] = {'expected': {role: dict(stdout_b64='',
        stderr_b64=want['stderr_b64'], rows=0, batches=0)}}
    result = protocol_capture(view, role)
    result.update(stdout_sha256=case['expected']['stdout_sha256'],
        stdout_bytes=want['stdout_bytes'], stdout_rows=case['expected']['stdout_rows'],
        stdout_batches=case['expected']['stdout_batches'])
    ua = 'grpc-go/1.65.0' if role == 'go' else 'tonic/0.14.6'
    result['requests'] += [copy.deepcopy(result['requests'][1]) for _ in want['streams'][1:]]
    result['events'] = [dict(kind='raw_h2_conn') for _ in range(want['connections'])] + [
        dict(kind='raw_h2_stream', ordinal=o, stream_id=s, t_ns=i * 1_000_000,
             metadata={'user-agent': [ua]}) for i, (o, s) in enumerate(want['streams'])]
    result['flight_user_agents'] = [[ua] for _ in want['streams']]
    result['flight_authorities'] *= len(want['streams'])
    result['summary'] = dict(events_dropped=0, trace_dropped=0,
        event_kinds=dict(raw_h2_conn=want['connections'], raw_h2_stream=len(want['streams'])))
    return result


class TransportWaveTests(unittest.TestCase):
    def test_fifteen_separate_cases_keep_all_contracts_except_explicit_ua(self):
        cases = wave.cases()
        self.assertEqual(len(cases), 15)
        self.assertFalse({c['name'] for c in cases} & {c['name'] for c in wave.p6.corpus()})
        for case in cases:
            entry = wave.classify(case, measured(case, 'go'), measured(case, 'candidate'))
            with self.subTest(case=case['name']):
                self.assertEqual(entry['issues'], [])
                self.assertTrue(entry['remaining_contract_passed'])
                self.assertFalse(entry['strict_parity_passed'])

    def test_lost_rows_wrong_stage_envelope_and_process_cannot_pass(self):
        case = next(c for c in wave.cases() if c['transport_wave']['probe'] == 'flag1-gzip-batch')
        go = measured(case, 'go')
        for key, value in [('stdout_bytes', 0), ('stdout_rows', 0), ('stdout_batches', 0),
                           ('stdout_sha256', wave.p6.run.digest(b'')), ('raw_wait_status', 15),
                           ('failure', 'timeout')]:
            candidate = measured(case, 'candidate')
            candidate[key] = value
            self.assertTrue(wave.classify(case, go, candidate)['issues'], key)
        candidate = measured(case, 'candidate')
        raw = base64.b64decode(candidate['stderr_contract_b64']) + b'\n'
        candidate['stderr_contract_b64'] = base64.b64encode(raw).decode()
        self.assertTrue(wave.classify(case, go, candidate)['issues'])

    def test_connection_reuse_and_event_coverage_are_independently_required(self):
        case = next(c for c in wave.cases() if c['transport_wave']['transparent_retry'])
        go = measured(case, 'go')
        for mutation in ('connection', 'stream', 'delay', 'events', 'summary'):
            candidate = measured(case, 'candidate')
            if mutation == 'connection':
                candidate['events'].append(dict(kind='raw_h2_conn'))
            elif mutation == 'stream':
                candidate['events'][-1]['stream_id'] = 1
            elif mutation == 'delay':
                candidate['events'][-2]['t_ns'] = 500_000_000
            elif mutation == 'events':
                candidate['summary']['events_dropped'] = 1
            else:
                del candidate['summary']['event_kinds']['raw_h2_conn']
            self.assertTrue(wave.classify(case, go, candidate)['issues'], mutation)

    def test_size_precedes_flag_partial_precedes_decompressor(self):
        by_name = {c['transport_wave']['probe']: c for c in wave.cases()}
        for name, expected in [('flag1-overlimit-init', b'ResourceExhausted'),
                               ('flag1-partial-body-init', b'unexpected EOF'),
                               ('flag1-empty-body-init', b'Unimplemented')]:
            self.assertIn(expected, base64.b64decode(by_name[name]['transport_wave']['stderr_b64']))
        self.assertEqual(by_name['empty-data-101']['expected']['stdout_rows'], 2)
        self.assertEqual(by_name['empty-data-60x2-across-reconnect']['expected']['stdout_batches'], 2)

    def test_auth_ticket_authority_and_config_are_not_relaxed(self):
        case = wave.cases()[0]
        go = measured(case, 'go')
        for mutation in ('auth', 'ticket', 'authority', 'config', 'ua'):
            candidate = measured(case, 'candidate')
            if mutation == 'auth':
                candidate['requests'][1]['headers'] = {}
            elif mutation == 'ticket':
                candidate['requests'][1]['body'] = '{}'
            elif mutation == 'authority':
                candidate['flight_authorities'] = [['wrong:1']]
            elif mutation == 'config':
                candidate['config_after'] = {'changed': True}
            else:
                candidate['flight_user_agents'] = [['unknown-agent']]
            self.assertTrue(wave.classify(case, go, candidate)['issues'], mutation)


if __name__ == '__main__':
    unittest.main()
