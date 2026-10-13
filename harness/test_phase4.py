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

if __name__=='__main__':unittest.main()
