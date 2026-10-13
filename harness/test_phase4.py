"""Independent phase-four corpus/model assertions (no live service)."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import phase4

class CorpusTests(unittest.TestCase):
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
