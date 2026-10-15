"""Independent phase-four corpus/model assertions (no live service)."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parent))
import phase4

class SelectionTests(unittest.TestCase):
    def invoke(self, selectors=(), include_full120s=False, command='check-reference', real_corpus=False):
        cases=[dict(name='gate-case',tier='gate',build='standard'),
               dict(name='deadline-case',tier='full120s',build='standard')]
        if real_corpus:cases=phase4.corpus()
        argv=[command,'--reference','reference','--out','unused-output']
        if command=='compare':argv+=['--candidate','candidate','--manifest','none']
        for name in selectors:argv+=['--case',name]
        if include_full120s:argv+=['--include-full120s']
        with mock.patch.object(phase4,'corpus',return_value=cases), \
             mock.patch.object(phase4.Path,'mkdir') as mkdir, \
             mock.patch.object(phase4.phase1,'binary_identity',return_value={'path':'reference','sha256':'same'}), \
             mock.patch.object(phase4.p3,'sample') as sample, \
             mock.patch.object(phase4,'expectation_issues',return_value=[]), \
             mock.patch.object(phase4.p3,'differences',return_value=[]), \
             mock.patch.object(phase4,'signature',return_value={}), \
             mock.patch.object(phase4.run,'write_json'), \
             mock.patch('builtins.print'):
            try:result=phase4.main(argv)
            except ValueError:
                sample.assert_not_called()
                mkdir.assert_not_called()
                raise
            return result,[call.args[1]['name'] for call in sample.call_args_list]

    def test_unknown_and_mixed_unknown_rejected_before_launch(self):
        for selectors in [('typo',),('gate-case','typo')]:
            with self.subTest(selectors=selectors),self.assertRaisesRegex(ValueError,'unknown.*typo'):
                self.invoke(selectors)

    def test_gated_and_mixed_gated_rejected_before_launch(self):
        for selectors in [('deadline-case',),('gate-case','deadline-case')]:
            with self.subTest(selectors=selectors),self.assertRaisesRegex(ValueError,'include-full120s'):
                self.invoke(selectors)

    def test_default_runs_gate_cases(self):
        self.assertEqual(self.invoke(),(False,['gate-case','gate-case']))

    def test_deadline_opt_in_runs_requested_case(self):
        self.assertEqual(self.invoke(['deadline-case'],True),(False,['deadline-case','deadline-case']))

    def test_compare_rejects_unknown_and_real_gated_cases_before_launch(self):
        for selectors,diagnostic in [(('typo',),'unknown'),(('p4-deadline-header',),'include-full120s'),
                                     (('p4-deadline-header','typo'),'unknown')]:
            with self.subTest(selectors=selectors),self.assertRaisesRegex(ValueError,diagnostic):
                self.invoke(selectors,command='compare',real_corpus=True)

    def test_duplicate_selection_executes_case_once(self):
        self.assertEqual(self.invoke(['gate-case','gate-case']),(False,['gate-case','gate-case']))

    def test_empty_corpus_rejected_before_launch(self):
        with mock.patch.object(phase4,'corpus',return_value=[]), \
             mock.patch.object(phase4.p3,'sample') as sample, \
             mock.patch.object(phase4.Path,'mkdir'), \
             mock.patch.object(phase4.phase1,'binary_identity',return_value={'path':'reference','sha256':'same'}), \
             mock.patch.object(phase4.run,'write_json'), \
             mock.patch('builtins.print'), \
             self.assertRaisesRegex(ValueError,'no cases'):
            phase4.main(['check-reference','--reference','reference','--out','unused-output'])
        sample.assert_not_called()

class CorpusTests(unittest.TestCase):
    def test_proxy_case_uses_proxy_aware_local_destination(self):
        case=next(c for c in phase4.corpus() if c['name']=='p4-proxy-ignored')
        self.assertEqual(case['fixture'].get('url_host'),'localhost.')
        self.assertEqual(case['env']['HTTP_PROXY'],'{{url}}')
        self.assertEqual(case['env']['NO_PROXY'],'')

    def test_proxy_aware_fixture_host_keeps_loopback_listener(self):
        fixture=phase4.p3.FixtureServer({'url_host':'localhost.','routes':[]})
        try:
            self.assertRegex(fixture.urls['url'],r'^http://localhost\.:\d+$')
            self.assertEqual(fixture.listeners[0][0].getsockname()[0],'127.0.0.1')
        finally:
            for listener,_ in fixture.listeners:listener.close()

    @unittest.skipUnless((phase4.ROOT.parent/'bin'/'pb-go').is_file(),'pinned Go reference not built')
    def test_proxy_case_standard_transport_control(self):
        import tempfile
        binary=phase4.ROOT.parent/'bin'/'pb-go'
        metrics=next(c for c in phase4.corpus() if c['name']=='p4-proxy-ignored')
        control=copy.deepcopy(metrics)
        control['argv']=['sql','list','-o','json']
        sentinel=b'phase4 controlled proxy sentinel'
        control['fixture']['routes']=[phase4.p3.route(sentinel,500,method='GET',path='/api/v1/filters')]
        with tempfile.TemporaryDirectory() as tmp:
            direct=phase4.p3.sample(binary,metrics,Path(tmp)/'direct',10)
            proxied=phase4.p3.sample(binary,control,Path(tmp)/'control',10)
        self.assertEqual(phase4.expectation_issues(metrics,direct),[])
        self.assertEqual(direct['trace'][0]['path'],metrics['targets'][0])
        self.assertEqual(proxied['termination'],{'exit':1})
        self.assertEqual(len(proxied['trace']),1)
        self.assertRegex(proxied['trace'][0]['path'],r'^http://localhost\.:\d+/api/v1/filters$')
        error=json.loads(proxied['stderr'])['error']
        self.assertEqual(error['code'],'SERVER_ERROR')
        self.assertEqual(error['http_status'],500)

    def test_unique_names_and_full_inventory(self):
        cases=phase4.corpus()
        self.assertEqual(len(cases),len({c['name'] for c in cases}))
        verbs={tuple(c['argv'][:2]) for c in cases}
        for verb in ('run','labels','label-values','series','cardinality','active-queries','ps','tsdb'):
            self.assertIn(('promql',verb),verbs)
        for verb in ('list','ls','save','run'):self.assertIn(('sql',verb),verbs)
    def test_query_encoding_exact(self):
        self.assertEqual(phase4.encoded([('z','+ ~&'),('match[]',['first','second'])]),'match%5B%5D=first&match%5B%5D=second&z=%2B+~%26')
    def test_saved_causal_inventory(self):
        cases={c['name']:c for c in phase4.corpus()}
        self.assertEqual(cases['p4-query-failure-never-saves']['expect']['requests'],['POST /api/v1/query'])
        self.assertEqual(cases['p4-save-200']['expect']['requests'],['POST /api/v1/filters'])
        self.assertEqual(cases['p4-run-save-500']['expect']['termination'],{'exit':0})
        self.assertEqual(cases['p4-run-save-500']['save_body']['filter_name'],' raw ')
    def test_path_joining_frozen_examples(self):
        cases={c['name']:c for c in phase4.corpus()}
        self.assertEqual(cases['p4-label-path-2e2e2f78']['targets'],['/prometheus/api/v1/x/values?stream=select-dataset'])
        self.assertEqual(cases['p4-label-path-6125324662']['targets'],['/prometheus/api/v1/label/a%2Fb/values?stream=select-dataset'])
    def test_returned_corpus_independent(self):
        first=phase4.corpus();first[0]['argv'][0]='changed'
        self.assertEqual(phase4.corpus()[0]['argv'][0],'promql')

    def capture(self):
        cases=phase4.corpus()
        return dict(mode='check-reference',partial=False,failed=0,tiers=['gate'],
                    corpus_sha256=phase4.run.digest(phase4.run.canonical(cases)),
                    cases=[{'name':c['name']} for c in cases if c['tier']=='gate'],
                    binaries={'reference':{'sha256':'standard'},'cloud_reference':{'sha256':'cloud'}})
    def test_default_capture_explicitly_excludes_deadlines(self):
        report=self.capture()
        phase4.validate_capture(report,report['corpus_sha256'],phase4.corpus())
        self.assertEqual([c['name'] for c in phase4.corpus() if c['tier']=='full120s'],
                         ['p4-deadline-header','p4-deadline-body'])
        report['tiers'].append('full120s')
        with self.assertRaises(ValueError):phase4.validate_capture(report,report['corpus_sha256'],phase4.corpus())
    def test_frozen_both_binary_hashes_enforced(self):
        manifest=self.capture()
        identities=copy.deepcopy(manifest['binaries'])
        self.assertEqual(phase4.frozen_identity_issues(manifest,identities),[])
        for role in ('reference','cloud_reference'):
            changed=copy.deepcopy(identities);changed[role]['sha256']='changed'
            self.assertEqual(phase4.frozen_identity_issues(manifest,changed),[role+' binary differs from the frozen Go oracle'])
        self.assertEqual(phase4.frozen_identity_issues(manifest,{'reference':identities['reference']}),
                         ['cloud_reference binary differs from the frozen Go oracle'])
    def test_manifest_inventory_tamper_rejected(self):
        report=self.capture();digest=report['corpus_sha256']
        for change in ('digest','partial','failure','missing','duplicate','mode'):
            changed=copy.deepcopy(report)
            if change=='digest':changed['corpus_sha256']='changed'
            elif change=='partial':changed['partial']=True
            elif change=='failure':changed['failed']=1
            elif change=='missing':changed['cases'].pop()
            elif change=='duplicate':changed['cases'].append(changed['cases'][0])
            elif change=='mode':changed['mode']='compare'
            with self.subTest(change=change),self.assertRaises(ValueError):
                phase4.validate_capture(changed,digest,phase4.corpus())
    def test_manifest_and_baseline_signature_tamper(self):
        frozen=dict(stdout='out',stderr='err',termination={'exit':0},requests=[],config={},stderr_raw='raw',trace='trace')
        self.assertEqual(phase4.frozen_signature_issues(frozen,copy.deepcopy(frozen)),[])
        self.assertEqual(phase4.baseline_signature_issues(frozen,copy.deepcopy(frozen)),[])
        for key in frozen:
            changed=copy.deepcopy(frozen);changed[key]='tampered'
            self.assertTrue(phase4.baseline_signature_issues(changed,frozen))
            if key not in ('stderr_raw','trace'):self.assertTrue(phase4.frozen_signature_issues(changed,frozen))
    def test_fresh_signature_compares_config_semantics_but_baseline_keeps_raw_integrity(self):
        frozen=dict(stdout='out',stderr='err',termination={'exit':0},requests=[],stderr_raw='raw',trace='trace',config={
            'config/pb/config.toml':{'type':'file','mode':'0o600','raw_sha256':'frozen-port',
                                     'toml':{'Profiles':{'fixture':{'url':'<URL>'}}}},
            'config/pb/broken.toml':{'type':'file','mode':'0o600','raw_sha256':'broken','toml_error':True},
            'cache/data':{'type':'file','mode':'0o600','raw_sha256':'cache'}})
        fresh=copy.deepcopy(frozen)
        fresh['config']['config/pb/config.toml']['raw_sha256']='fresh-port'
        self.assertEqual(phase4.frozen_signature_issues(fresh,frozen),[])
        self.assertEqual(phase4.baseline_signature_issues(copy.deepcopy(frozen),frozen),[])
        self.assertEqual(phase4.baseline_signature_issues(fresh,frozen),
                         ['baseline artifacts differ from frozen signature'])
        for change in ('semantic','mode','non-TOML','malformed-TOML','type','missing'):
            changed=copy.deepcopy(fresh)
            if change=='semantic':changed['config']['config/pb/config.toml']['toml']['Profiles']['fixture']['url']='changed'
            elif change=='mode':changed['config']['config/pb/config.toml']['mode']='0o644'
            elif change=='non-TOML':changed['config']['cache/data']['raw_sha256']='changed'
            elif change=='malformed-TOML':changed['config']['config/pb/broken.toml']['raw_sha256']='changed'
            elif change=='type':changed['config']['config/pb/config.toml']['type']='directory'
            elif change=='missing':del changed['config']['config/pb/config.toml']
            with self.subTest(change=change):
                self.assertEqual(phase4.frozen_signature_issues(changed,frozen),
                                 ['reference config differs from frozen Go oracle'])
                self.assertTrue(phase4.baseline_signature_issues(changed,frozen))
    def test_regression_inventory(self):
        names={c['name'] for c in phase4.corpus()}
        for name in ('unicode-envelope-text','builder-set-int','builder-rule-str','signed-year-to',
                     'interactive-validation-from-instant','typed-null-status','step-auto-next','time-quirk-fraction'):
            self.assertIn('p4-'+name,names)


class HeaderDeadlineNormalizationTests(unittest.TestCase):
    # Literal independent expected bytes, not read from the proposed normalizer.
    prefix = b'Error: Get "http://<FIXTURE>/prometheus/api/v1/labels?stream=select-dataset": '
    usage = (b'Usage:\n  pb promql labels [stream] [flags]\n\nExamples:\n'
             b'  pb promql labels otel_metrics\n  pb promql labels --dataset otel_metrics\n\nFlags:\n'
             b'  -d, --dataset string   Metrics dataset (default "select-dataset")\n'
             b'  -f, --from string      Start time filter (optional)\n'
             b'  -h, --help             help for labels\n'
             b'  -o, --output string    Output format: text or json (default "text")\n'
             b'  -t, --to string        End time filter (optional)\n\n')
    suffix = b' (Client.Timeout exceeded while awaiting headers)'

    def setUp(self):
        self._case = next(c for c in phase4.corpus() if c['name'] == 'p4-deadline-header')

    def case(self):
        return copy.deepcopy(self._case)

    def forms(self):
        return [self.prefix + cause + b'\n' + self.usage for cause in
                (b'context deadline exceeded', b'context deadline exceeded' + self.suffix,
                 b'net/http: request canceled' + self.suffix)]

    def canonical(self):
        return self.prefix + b'<CLIENT-TIMEOUT>' + self.suffix + b'\n' + self.usage

    def result(self, stderr):
        request = dict(method='GET', target='/prometheus/api/v1/labels?stream=select-dataset',
                       headers={'authorization': 'Basic aGFybmVzczpoYXJuZXNz', 'accept-encoding': 'gzip'},
                       body_b64='', framing='none', listener='primary')
        trace = dict(method='GET', path=request['target'], unexpected=False, body_b64='')
        return dict(failure=None, actions=[], termination={'exit': 1}, stdout=b'', stdout_normalized=b'',
                    stdout_sha256=phase4.run.digest(b''), stdout_normalized_sha256=phase4.run.digest(b''),
                    stdout_bytes=0, stderr=stderr, stderr_normalized=stderr,
                    requests=[request], trace=[trace], config_before={}, config_after={},
                    wall_ms=120030, milestones=[], snapshots={}, first_stdout_ms=None,
                    started_monotonic_ns=0, config_raw_after=None)

    def test_three_observed_forms_and_exact_usage(self):
        for data in self.forms():
            self.assertEqual(phase4.p3.normalize_timeout(self.case(), data), self.canonical())
            self.assertEqual(phase4.p3.compare_stderr(self.case(), data), self.canonical())
            self.assertEqual(data.partition(b'\n')[2], self.canonical().partition(b'\n')[2])
        for left in self.forms():
            for right in self.forms():
                a, b = self.result(left), self.result(right)
                self.assertEqual(phase4.expectation_issues(self.case(), a), [])
                self.assertEqual(phase4.p3.differences(self.case(), a, b), [])
                self.assertEqual(phase4.signature(a, self.case())['stderr'],
                                 phase4.signature(b, self.case())['stderr'])

    def test_every_single_byte_change_is_unchanged(self):
        # Covers error text, method, origin, path/query, every flag/example, and LF.
        for data in self.forms():
            for index in range(len(data)):
                wrong = data[:index] + bytes([data[index] ^ 1]) + data[index + 1:]
                self.assertEqual(phase4.p3.normalize_timeout(self.case(), wrong), wrong, index)
                self.assertEqual(phase4.p3.compare_stderr(self.case(), wrong), wrong, index)

    def test_unknown_cause_and_whole_stream_boundaries_are_unchanged(self):
        for cause in (b'net/http: request canceled', b'context canceled', b'i/o timeout',
                      b'unknown' + self.suffix, b'context deadline exceeded (Client.Timeout unknown)',
                      b'context deadline exceeded (Client.Timeout or context cancellation while reading body)',
                      b'context deadline exceeded (Client.Timeout exceeded while awaiting headers) extra'):
            wrong = self.prefix + cause + b'\n' + self.usage
            self.assertEqual(phase4.p3.normalize_timeout(self.case(), wrong), wrong)
        for data in self.forms():
            for wrong in (data[:-1], data.replace(b'\n', b'\r\n'), b'prefix\n' + data, data + b'\n',
                          data + b'extra\n', data.partition(b'\n')[0] + b'\n',
                          data.replace(b'Usage:', b'Usage: changed'),
                          data.replace(b'[flags]', b'[options]'),
                          data.replace(b'--output', b'--format'),
                          json.dumps({'error': data.decode()}).encode(),
                          data.replace(b'select-dataset', b'other'),
                          data.replace(b'http://<FIXTURE>', b'http://localhost:1234'),
                          data.replace(b'labels?', b'label/x/values?'),
                          data.replace(b'Error: Get', b'Error: Post'),
                          data.replace(b'Usage:\n', b'HTTP 500\nUsage:\n')):
                self.assertEqual(phase4.p3.normalize_timeout(self.case(), wrong), wrong)

    def test_wrong_case_bare_input_and_opt_out_are_unchanged(self):
        bare = self.forms()[0]
        for case in phase4.corpus() + phase4.p3.corpus():
            if case['name'] != 'p4-deadline-header':
                self.assertEqual(phase4.p3.normalize_timeout(case, bare), bare, case['name'])
                if not case.get('compare', {}).get('stderr_unordered_lines'):
                    self.assertEqual(phase4.p3.compare_stderr(case, bare), bare, case['name'])
        for name in ('P4-DEADLINE-HEADER', 'p4-deadline-header-extra', 'p4-deadline-body', ''):
            case = self.case(); case['name'] = name
            self.assertEqual(phase4.p3.normalize_timeout(case, bare), bare)
        # F5: synthetic opt-in wrong names ending in '-header' must retain the
        # legacy body-suffix transform, rather than enter the exact P4 branch.
        legacy = b'Error: failed to read response body: context deadline exceeded (Client.Timeout or context cancellation while reading body)\n'
        expected = b'Error: failed to read response body: <CLIENT-TIMEOUT> (Client.Timeout or context cancellation while reading body)\n'
        for name in ('p4-synthetic-header', 'p3-text-gzip-bad-header'):
            case = self.case(); case['name'] = name
            self.assertEqual(phase4.p3.normalize_timeout(case, legacy), expected)
        for option in (False, None, 0):
            case = self.case(); case['compare']['timeout_cause'] = option
            for data in self.forms():
                self.assertEqual(phase4.p3.normalize_timeout(case, data), data)
        case = self.case(); del case['compare']['timeout_cause']
        for data in self.forms():
            self.assertEqual(phase4.p3.normalize_timeout(case, data), data)

    def test_independent_exit_stdout_request_and_duration_gates(self):
        good = self.result(self.forms()[0]); case = self.case()
        self.assertEqual(phase4.expectation_issues(case, good), [])
        # F2: an unrecognized cause cannot acquire the independent timeout needle.
        self.assertIn("stderr lacks b'Client.Timeout'", phase4.expectation_issues(
            case, self.result(self.prefix + b'EOF\n' + self.usage)))
        changes = [({'termination': {'exit': 0}}, 'termination'),
                   ({'termination': {'signal': 15}}, 'termination'),
                   ({'stdout': b'x', 'stdout_normalized': b'x', 'stdout_bytes': 1,
                     'stdout_normalized_sha256': phase4.run.digest(b'x')}, 'stdout'),
                   ({'trace': [], 'requests': []}, 'requests'),
                   ({'trace': [dict(method='POST', path=good['trace'][0]['path'], unexpected=False)]}, 'requests'),
                   ({'trace': [dict(method='GET', path='/prometheus/api/v1/labels?stream=other', unexpected=False)]}, 'raw request targets'),
                   ({'trace': [dict(method='GET', path=good['trace'][0]['path'], unexpected=True)]}, 'unexpected'),
                   ({'trace': good['trace'] * 2, 'requests': good['requests'] * 2}, 'requests'),
                   ({'wall_ms': 118999}, 'below'), ({'wall_ms': 130001}, 'exceeds')]
        for change, needle in changes:
            bad = copy.deepcopy(good); bad.update(change)
            self.assertTrue(any(needle in i for i in phase4.expectation_issues(case, bad)), (change, needle))
            # F6: equal wrong exit/output/request observations can satisfy the
            # differential while the independent assertion above still fails.
            identical = phase4.p3.differences(case, bad, copy.deepcopy(bad))
            if 'wall_ms' in change:
                # Candidate duration bounds also gate identical invalid times.
                self.assertTrue(any(needle in issue for issue in identical))
            else:
                self.assertEqual(identical, [], change)
        for wall in (119000, 130000):
            valid = copy.deepcopy(good); valid['wall_ms'] = wall
            self.assertEqual(phase4.expectation_issues(case, valid), [])

    def test_differential_body_headers_status_output_and_config_gates(self):
        case = self.case(); ref = self.result(self.forms()[0]); cand = self.result(self.forms()[1])
        self.assertEqual(phase4.p3.differences(case, ref, cand), [])
        # F1: identity in normalization must still produce stderr differential
        # failures, in both directions, for every non-admitted stream below.
        admitted = self.forms()[1]
        wrong_streams = (admitted.replace(b'--output', b'--format'),
                         self.prefix + b'i/o timeout' + self.suffix + b'\n' + self.usage,
                         self.prefix + b'net/http: request canceled\n' + self.usage,
                         self.forms()[0][:-1])
        pairs = [(admitted, wrong) for wrong in wrong_streams]
        # Tightening: the old substring rule collapsed this shared extra line.
        pairs.append((self.forms()[2] + b'x\n', self.forms()[1] + b'x\n'))
        for left, right in pairs:
            for a, b in ((left, right), (right, left)):
                self.assertTrue(any('stderr differs' in issue for issue in phase4.p3.differences(
                    case, self.result(a), self.result(b))), (a, b))
        for key, value, needle in [('termination', {'exit': 0}, 'termination'),
                                   ('wall_ms', 118999, 'below'), ('wall_ms', 130001, 'exceeds'),
                                   ('stdout_normalized', b'x', 'stdout')]:
            bad = copy.deepcopy(cand); bad[key] = value
            if key == 'stdout_normalized': bad['stdout_normalized_sha256'] = phase4.run.digest(value)
            self.assertTrue(any(needle in i for i in phase4.p3.differences(case, ref, bad)))
        for field, value in [('headers', {'authorization': 'different'}), ('body_b64', 'eA=='),
                             ('target', '/different'), ('method', 'POST'), ('framing', 'chunked'),
                             ('listener', 'secondary')]:
            bad = copy.deepcopy(cand); bad['requests'][0][field] = value
            self.assertTrue(any('requests differ' in i for i in phase4.p3.differences(case, ref, bad)))
            if field in ('headers', 'body_b64'):
                # No independent header/body pins for this case; equality alone is insufficient.
                self.assertEqual(phase4.expectation_issues(case, bad), [])
                self.assertEqual(phase4.p3.differences(case, bad, copy.deepcopy(bad)), [])
        changed = {'x': {'type': 'file', 'mode': '0o600', 'raw_sha256': 'different'}}
        for field in ('config_before', 'config_after'):
            bad = copy.deepcopy(cand); bad[field] = changed
            self.assertTrue(any(field.replace('_', ' ') in i for i in phase4.p3.differences(case, ref, bad)))
            self.assertIn('configuration mutated', phase4.expectation_issues(case, bad))
        bad = copy.deepcopy(cand); bad.update(config_before=changed, config_after=changed)
        self.assertEqual(phase4.expectation_issues(case, bad), [])
        self.assertTrue(any('config' in i for i in phase4.p3.differences(case, ref, bad)))

    def test_deferred_stub_guard_remains_active(self):
        case = self.case(); ref = self.result(self.forms()[0]); bad = copy.deepcopy(ref)
        bad['stderr'] = bad['stderr_normalized'] = b'Error: labels is not implemented in this Rust phase\n'
        self.assertTrue(any('deferred-operation stub' in i for i in phase4.p3.differences(case, ref, bad)))
        for check in phase4.deferred_boundaries():
            boundary = self.result(b'')
            boundary.update(trace=[], requests=[])
            if check['structured']:
                boundary['stderr'] = json.dumps({'error': {'code': 'COMMAND_FAILED',
                    'message': 'command is not implemented in this Rust phase', 'retryable': False}}).encode() + b'\n'
            else:
                boundary['stderr'] = b'Error: command is not implemented in this Rust phase\n'
            self.assertEqual(phase4.p3.boundary_issues(check, boundary), [])
            boundary['requests'] = [{'method': 'GET', 'target': '/unexpected'}]
            boundary['trace'] = [{'method': 'GET', 'path': '/unexpected'}]
            self.assertTrue(any('network' in i for i in phase4.p3.boundary_issues(check, boundary)))


if __name__=='__main__':unittest.main()
