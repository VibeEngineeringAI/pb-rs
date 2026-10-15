#!/usr/bin/env python3
"""Pure tests with repository fixtures and module-scoped process/lock guards."""
from collections import Counter
from contextlib import ExitStack
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

CACHE = Path(os.environ.get('PHASE6_RESOURCE_SUMMARY_CACHE',
             str(Path.home() / '.cache/pb-phase6/resource-summary'))).expanduser()
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / 'tests/fixtures/phase6-resource-summary'
FORBIDDEN_CALLS = Counter()
GUARDS = None
summary = campaign_run = collector = None


def forbidden(name):
    def fail(*args, **kwargs):
        FORBIDDEN_CALLS[name] += 1
        raise AssertionError('forbidden process/lock operation: ' + name)
    return fail


def setUpModule():
    global GUARDS, summary, campaign_run, collector
    GUARDS = ExitStack()
    unittest.addModuleCleanup(GUARDS.close)
    FORBIDDEN_CALLS.clear()
    for module, names in ((subprocess, ('Popen', 'run', 'call', 'check_call', 'check_output')),
                          (os, ('fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'system', 'popen',
                                'execv', 'execve', 'execvp', 'execvpe', 'execl', 'execle', 'execlp',
                                'execlpe', 'spawnl', 'spawnle', 'spawnlp', 'spawnlpe', 'spawnv',
                                'spawnve', 'spawnvp', 'spawnvpe')),
                          (fcntl, ('flock', 'lockf'))):
        for name in names:
            if hasattr(module, name):
                GUARDS.enter_context(patch.object(module, name, forbidden(module.__name__ + '.' + name)))
    GUARDS.enter_context(patch.object(sys, 'dont_write_bytecode', True))
    GUARDS.enter_context(patch.dict(os.environ, {name: str(CACHE) for name in
                                               ('TMPDIR', 'GOTMPDIR', 'CLAUDE_CODE_TMPDIR')}))
    resolved = CACHE.resolve()
    if resolved == Path('/tmp') or Path('/tmp') in resolved.parents:
        raise ValueError('test cache must use disk outside /tmp')
    CACHE.mkdir(parents=True, exist_ok=True)
    import phase6_resource_summary as imported_summary
    import run as imported_run
    GUARDS.enter_context(patch.object(imported_run, 'spawn_measured', forbidden('run.spawn_measured')))
    import phase6_workloads as imported_collector
    summary, campaign_run, collector = imported_summary, imported_run, imported_collector


def tearDownModule():
    try:
        if FORBIDDEN_CALLS:
            raise AssertionError(dict(FORBIDDEN_CALLS))
    finally:
        if GUARDS is not None:
            GUARDS.close()


def encoded(value):
    return json.dumps(value, allow_nan=False).encode()


def identity(name):
    return dict(path='/offline/' + name, sha256=hashlib.sha256(name.encode()).hexdigest(), size_bytes=7)


def fixture(kind='native_unprofiled', tier='finite', workloads=2):
    """Synthetic collector-v2 shape; numbers are mock data, never timing evidence."""
    mode = 'hash-framing' if kind == 'observer_control' else 'full'
    labels = ['go-a', 'go-b'] if kind == 'timing_aa' else ['go', 'candidate']
    report = dict(schema_version=2, complete=True, passed=True, comparison_contracts_passed=True,
                  tls=False, kind=kind, tier=tier, collector_mode=mode, duration_s=1,
                  seed=2026, session_index=0, warmups=5 if kind == 'timing_aa' else 1,
                  repetitions=30 if kind == 'timing_aa' else 2, summaries={}, failure=None,
                  end_identity_check=dict(passed=True, changed=[]))
    report.update({key: identity(key) for key in summary.IDENTITIES})
    report['binaries'] = {label: identity('go' if kind == 'timing_aa' else label) for label in labels}
    budgets = {'mock_only': True}
    fixture_build = dict(binary=report['fixture'])
    manifest = dict(budgets=budgets, budgets_sha256=report['budget_file']['sha256'],
                    fixture_build=fixture_build, fixture_manifest=report['fixture_manifest'],
                    meter=report['meter'], baselines={'pb-go': report['binaries'][labels[0]]},
                    build_flags={'candidate': {'pb': report['binaries'][labels[-1]]}})
    report.update(manifest=manifest, budgets=budgets, fixture_build=fixture_build,
                  stable_manifest_sha256=summary.canonical_hash(manifest))
    report['measured_roles'] = {}
    for label, info in {**report['binaries'], 'meter': report['meter']}.items():
        role = 'go' if kind == 'timing_aa' and label != 'meter' else label
        report['measured_roles'][label] = dict(role=role, selected=info, declared=info,
                                               content_identical_copy=False)
    report['workloads'] = [dict(name='one-row' if i == 0 else 'large', stream={'schema': 'basic', 'seed': 2026},
        sink={}, duration_s=None if tier == 'finite' else 1) for i in range(workloads)]
    samples = []
    for item in summary.declared_schedule(report):
        workload = next(w for w in report['workloads'] if w['name'] == item['workload'])
        value = 999 if item['warmup'] else 10 + item['round'] * 10
        contract = dict(stdout_sha256=identity('stdout')['sha256'], stderr_sha256=identity('stderr')['sha256'],
            stdout_bytes=100, rendered_rows=1, batches=1, termination={'exit': 1}, requests=1,
            ticket_sha256=identity('ticket')['sha256'], request_validation={'auth_checks': 1},
            workload_sha256=summary.canonical_hash({k: workload[k] for k in ('stream', 'duration_s', 'sink')}),
            sink={}, tls=False, config_before=identity('config')['sha256'], config_after=identity('config')['sha256'])
        samples.append(dict(item, valid=True, errors=[], capture_complete=True, contract_validated=True,
            contract=contract, collector_mode=mode, tls=False, sink={},
            cpu_screen=dict(comparative_admissible=False, throughput_admissible=False,
                            interference_status='resolution_limited', potential_limit_status='below_screen'),
            production=dict(throughput_admissible=True, live={'omitted_by_deadline': 0, 'omitted_by_termination': 0}),
            **{key: value for key in summary.BASE_METRICS if key != 'peak_rss_bytes'}, peak_rss_bytes=4096,
            minor_faults=value, major_faults=0, voluntary_context_switches=3, involuntary_context_switches=0,
            block_input_operations=0, block_output_operations=0, proc_io_available=True,
            **{key: 0 for key in summary.COUNTERS if key.startswith('proc_io_')},
            encoded_bytes_per_second=123, decoded_bytes_per_second=456,
            invalid_records=0, missing_proc_samples=2, resources_dropped_bytes=0,
            first_record_observed_after_native_exit=False))
    refresh(report, samples)
    return report, samples


def refresh(report, samples):
    report['expected_measured_samples'] = len(report['workloads']) * 2 * report['repetitions']
    report['recorded_measured_samples'] = sum(not s['warmup'] for s in samples)
    report['summaries'] = {}
    for workload in report['workloads']:
        name = workload['name']
        measured = [s for s in samples if s['workload'] == name and not s['warmup']]
        row = {}
        for label in report['binaries']:
            row[label] = {}
            valid = [s for s in measured if s['label'] == label and s['valid']]
            for metric in summary.BASE_METRICS:
                values = [s[metric] for s in valid if s.get(metric) is not None]
                row[label][metric] = dict(n=len(values), median=statistics.median(values) if values else None,
                    minimum=min(values) if values else None, maximum=max(values) if values else None,
                    stdev=statistics.stdev(values) if len(values) > 1 else None)
        row.update(contract_comparison=dict(samples=len(measured), comparable=all(s['valid'] for s in measured)),
            ratio_quarantine=['host CPU admission resolution_limited'], metric_quarantine={'wall_ms': ['quarantined']},
            metric_scope={}, candidate_over_go={key: None for key in summary.BASE_METRICS})
        report['summaries'][name] = row


def evaluate(report, samples):
    return summary.summarize_bytes(encoded(report), b''.join(encoded(s) + b'\n' for s in samples))


def load_functional_bases():
    manifest_data, _ = summary.read_bound(FIXTURE_ROOT / 'manifest.json', summary.REPORT_BYTES)
    manifest = summary.strict_json(manifest_data)
    bases = {}
    for name, filename in (('one_row', 'go-one-row.json'), ('idle', 'go-idle.json')):
        data, identity = summary.read_bound(FIXTURE_ROOT / filename, summary.REPORT_BYTES)
        declared = manifest['fixtures'][filename]
        summary.require(identity['sha256'] == declared['original_sha256'] and
                        identity['size_bytes'] == declared['size_bytes'],
                        'functional fixture identity mismatch: ' + filename)
        bases[name] = summary.strict_json(data)
    return bases


def mocked_collector(root, *, workloads=('one-row',), tier='finite', mode='full', repetitions=3,
                     mutate=None, finalization_failure=False, lock_failure=False, declaration_paths=None):
    """Run the frozen campaign/finalizer only; observations are retained functional copies.

    Like Opus R1's probe, every client/fixture/meter invocation and real lock is mocked.
    The /bin/true bytes supply offline identity fixtures and are never executed.
    """
    executable = Path('/bin/true').read_bytes()
    fixture_path = root / 'fixture'
    fixture_path.write_bytes(executable)
    fixture_path.chmod(0o555)
    sources, paths = {}, {}
    for name in ('main.go', 'phase6.go', 'go.mod', 'go.sum'):
        path = root / name
        path.write_text(name)
        path.chmod(0o444)
        sources[name] = collector.file_info(path)['sha256']
        paths[name] = str(path)
    toolchain = root / 'toolchain'
    toolchain.write_bytes(executable)
    build = dict(binary=collector.file_info(fixture_path), sources=sources, source_paths=paths,
                 toolchain=dict(collector.file_info(toolchain), version='go1.25.4'))
    fixture_manifest = root / 'fixture-manifest.json'
    collector.write_json(fixture_manifest, build)
    normal = dict(collector.file_info('/bin/true'), argv=list(collector.NORMAL_RELEASE_ARGV), features=[],
                  cloud_synthetic_token_enabled=False, build_kind='normal_release')
    candidate_manifest = root / 'candidate-build.json'
    collector.write_json(candidate_manifest, dict(builds=dict(pb=normal)))
    manifest = dict(sources={'oracle': 'mock-only'}, toolchains={'go': 'mock-only'}, dependencies={'locked': True},
        build_flags=dict(candidate=dict(pb=normal)), baselines={'pb-go': collector.file_info('/bin/true')},
        meter=collector.file_info('/bin/true'), candidate_manifest=collector.file_info(candidate_manifest),
        budgets=collector.read_json(collector.PREDECLARED_BUDGETS),
        budgets_sha256=collector.file_info(collector.PREDECLARED_BUDGETS)['sha256'],
        fixture_manifest=collector.file_info(fixture_manifest), fixture_build=build)
    for role, path in (declaration_paths or {}).items():
        declared = (manifest['baselines']['pb-go'] if role == 'go' else manifest['meter'] if role == 'meter'
                    else manifest['build_flags']['candidate']['pb'])
        declared['path'] = str(path)
    # Candidate build content must match the manifest even for a noncanonical path.
    collector.write_json(candidate_manifest, dict(builds=dict(pb=manifest['build_flags']['candidate']['pb'])))
    manifest['candidate_manifest'] = collector.file_info(candidate_manifest)
    collector.write_json(root / 'manifest.json', manifest)
    args = SimpleNamespace(out=root / 'campaign', manifest=root / 'manifest.json', go=Path('/bin/true'),
        candidate=Path('/bin/true'), fixture=fixture_path, meter=Path('/bin/true'), tls=False,
        tier=tier, duration_s=1800.0 if tier == 'finite' else 1.0, seed=2026, workload=list(workloads),
        warmups=1 if tier == 'finite' else 0, repetitions=repetitions, aa_repetitions=2, timing_aa=False,
        collector_mode=mode, session_index=0, lock=collector.PERFORMANCE_LOCK,
        quiet_window_token='mock-only-resource-summary-regression', window_budget_s=1e6)
    bases = load_functional_bases()
    calls = 0
    def sample(binary, workload, fixture, meter, folder, tls=False, collector_mode='full'):
        nonlocal calls
        calls += 1
        name = workload['name']
        # The retained R5 idle record supplies the current zero-row contract shape for both cases,
        # as in the independent probe; older empty-batch artifacts predate that contract schema.
        value = copy.deepcopy(bases['idle' if name in ('empty-batch', 'idle') else 'one_row'])
        value.update(tls=tls, sink=workload['sink'])
        value['contract'].update(tls=tls, sink=workload['sink'], workload_sha256=summary.canonical_hash(
            {k: workload[k] for k in ('stream', 'duration_s', 'sink')}))
        if collector_mode == 'hash-framing':
            value.update(kind='observer_control', collector_mode=collector_mode, sequence_errors=None)
        return mutate(calls, value) if mutate else value
    with ExitStack() as stack:
        stack.enter_context(patch.object(collector, 'TMP', root / 'tmp'))
        stack.enter_context(patch.object(campaign_run, 'host_metadata', return_value={'mocked': True}))
        stack.enter_context(patch.object(collector, 'run_sample', side_effect=sample))
        stack.enter_context(patch.object(collector, 'quiet_lock',
            **({'side_effect': BlockingIOError(11, 'mock lock busy')} if lock_failure else {})))
        if finalization_failure:
            stack.enter_context(patch.object(collector, 'summarize_campaign', side_effect=RuntimeError('mock finalization failure')))
        code = collector.campaign(args)
    return args, code


class ResourceSummary(unittest.TestCase):
    def setUp(self):
        self.report, self.samples = fixture()

    def tearDown(self):
        self.assertEqual(dict(FORBIDDEN_CALLS), {})

    def measured(self, result, workload='one-row', label='go'):
        return result['groups'][workload][label]['measured']

    def test_extract_actual_top_level_schema(self):
        result = evaluate(self.report, self.samples)
        metrics = self.measured(result)['metrics']
        for field in summary.METRICS:
            self.assertIn(field, metrics)
        self.assertEqual(metrics['minor_faults'], dict(available=2, missing=0, median=15.0, minimum=10, maximum=20))
        self.assertEqual(metrics['decoded_bytes_per_second']['median'], 456)
        self.assertEqual(metrics['wall_ms']['median'], 15)
        self.assertEqual(metrics['peak_rss_bytes']['median'], 4096)

    def test_zero_missing_null_are_distinct(self):
        chosen = [s for s in self.samples if not s['warmup'] and s['label'] == 'go' and s['workload'] == 'one-row']
        del chosen[0]['major_faults']
        chosen[1]['proc_io_syscr'] = None
        result = self.measured(evaluate(self.report, self.samples))['metrics']
        self.assertEqual(result['major_faults'], dict(available=1, missing=1, median=0, minimum=0, maximum=0))
        self.assertEqual(result['proc_io_syscr']['available'], 1)
        self.assertEqual(result['fixture_decoded_bytes'], dict(available=0, missing=2, median=None, minimum=None, maximum=None))

    def test_warmups_labels_and_workloads_not_pooled(self):
        for s in self.samples:
            if s['workload'] == 'large':
                s['minor_faults'] = 888
            elif s['label'] == 'candidate':
                s['minor_faults'] = 777
        result = evaluate(self.report, self.samples)
        self.assertEqual(self.measured(result)['metrics']['minor_faults']['median'], 15)
        self.assertEqual(self.measured(result, label='candidate')['metrics']['minor_faults']['median'], 777)
        self.assertEqual(self.measured(result, workload='large')['metrics']['minor_faults']['median'], 888)
        self.assertEqual(result['groups']['one-row']['go']['warmups']['metrics']['minor_faults']['median'], 999)
        self.assertEqual(result['contract_aa']['status'], 'not_in_these_inputs')

    def test_invalid_sample_excluded_and_omissions_separate(self):
        s = next(s for s in self.samples if not s['warmup'] and s['label'] == 'go')
        s.update(valid=False, contract_validated=False, errors=['synthetic failure'], minor_faults=9000,
                 invalid_records=2, missing_proc_samples=7)
        s['production']['live']['omitted_by_termination'] = 8
        self.report.update(passed=False, comparison_contracts_passed=False)
        refresh(self.report, self.samples)
        result = evaluate(self.report, self.samples)
        group = self.measured(result)
        self.assertEqual((group['valid'], group['invalid']), (1, 1))
        self.assertEqual(group['metrics']['minor_faults']['median'], 20)
        self.assertEqual(group['observation_coverage']['invalid']['invalid_records']['total'], 2)
        self.assertEqual(group['observation_coverage']['invalid']['production.live.omitted_by_termination']['total'], 8)
        self.assertEqual(group['observation_coverage']['valid']['missing_proc_samples']['total'], 2)
        self.assertEqual(result['invalid_records'][0]['errors'], ['synthetic failure'])

    def test_invalid_warmup_does_not_remove_valid_measured_absolutes(self):
        self.samples[0].update(valid=False, contract_validated=False, errors=['warmup failed'])
        self.report.update(passed=False, comparison_contracts_passed=False)
        result = evaluate(self.report, self.samples)
        self.assertEqual(self.measured(result)['valid'], 2)
        self.assertEqual(len(result['invalid_records']), 1)

    def test_incomplete_prefix_keeps_only_identified_valid_subset(self):
        subset = self.samples[:3]
        self.report.update(complete=False, passed=False, comparison_contracts_passed=False)
        refresh(self.report, subset)
        result = evaluate(self.report, subset)
        self.assertEqual(result['statistics_status'], 'incomplete_valid_subset')
        self.assertEqual(sum(self.measured(result, label=l)['unrecorded'] for l in ('go', 'candidate')), 3)
        self.assertEqual(self.measured(result, workload='large')['recorded'], 0)

    def test_empty_early_journal_requires_initialized_provenance(self):
        self.report.update(complete=False, passed=False, comparison_contracts_passed=False, recorded_measured_samples=0,
                           summaries={})
        self.assertEqual(self.measured(evaluate(self.report, []))['unrecorded'], 2)
        del self.report['parent_manifest']
        with self.assertRaisesRegex(ValueError, 'identity'):
            evaluate(self.report, [])

    def test_timing_aa_and_observer_and_functional_cohorts(self):
        for kind, tier in (('timing_aa', 'finite'), ('observer_control', 'finite'), ('native_unprofiled', 'functional')):
            report, samples = fixture(kind, tier, workloads=1)
            result = evaluate(report, samples)
            self.assertEqual(result['cohort']['kind'], kind)
            self.assertEqual(result['cohort']['tier'], tier)
            self.assertEqual(set(result['groups']['one-row']), set(report['binaries']))
            if kind == 'timing_aa':
                self.assertEqual(result['groups']['one-row']['go-a']['measured']['recorded'], 30)

    def test_duration_sessions_never_pooled(self):
        report, samples = fixture(tier='functional', workloads=1)
        result = evaluate(report, samples)
        group = self.measured(result)
        self.assertNotIn('metrics', group)
        self.assertEqual(group['sessions']['0']['metrics']['minor_faults']['median'], 10)
        self.assertEqual(group['sessions']['1']['metrics']['minor_faults']['median'], 20)

    def test_signed_seed_and_session_are_not_new_campaign_prerequisites(self):
        self.report.update(seed=-3, session_index=-2)
        for sample, item in zip(self.samples, summary.declared_schedule(self.report)):
            sample.update(item)
        self.assertEqual(evaluate(self.report, self.samples)['cohort']['seed'], -3)

    def test_after_exit_firstrow_and_retained_rates_preserved(self):
        for s in self.samples:
            s['first_complete_record_ms'] = 500
            s['first_record_observed_after_native_exit'] = True
            s['encoded_bytes_per_second'] = 3
            s['fixture_encoded_bytes'] = 900000
        refresh(self.report, self.samples)
        group = self.measured(evaluate(self.report, self.samples))
        self.assertEqual(group['metrics']['first_complete_record_ms']['median'], 500)
        self.assertEqual(group['source_flags']['first_record_observed_after_native_exit'], {'true': 2})
        self.assertEqual(group['metrics']['encoded_bytes_per_second']['median'], 3)

    def test_changed_source_identity_stays_explicitly_incomplete(self):
        self.report.update(complete=False, passed=False, comparison_contracts_passed=False,
                           end_identity_check=dict(passed=False, changed=['/offline/go']))
        result = evaluate(self.report, self.samples)
        self.assertEqual(result['statistics_status'], 'incomplete_valid_subset')
        self.assertFalse(result['source_status']['end_identity_check']['passed'])
        self.assertEqual(self.measured(result)['valid'], 2)
        self.report['passed'] = True
        with self.assertRaises(ValueError):
            evaluate(self.report, self.samples)

    def test_reject_bool_metrics_even_on_invalid_records(self):
        for field in ('minor_faults', 'wall_ms', 'decoded_bytes_per_second', 'proc_io_read_bytes', 'missing_proc_samples'):
            for valid in (True, False):
                samples = copy.deepcopy(self.samples)
                samples[0].update(valid=valid, **{field: True})
                with self.assertRaisesRegex(ValueError, 'invalid'):
                    evaluate(self.report, samples)

    def test_reject_nonfinite_duplicate_keys_bad_utf8_and_depth(self):
        for data in (b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{"x":1,"x":2}', b'"\xff"', b'[' * 70 + b'0' + b']' * 70):
            with self.subTest(data=data[:30]), self.assertRaises(ValueError):
                summary.strict_json(data)
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.assertRaises(ValueError):
                summary.number(value, 'probe')

    def test_reject_fractional_negative_counters_and_huge_number(self):
        for field, value in (('minor_faults', 1.5), ('proc_io_rchar', -1), ('wall_ms', -1), ('cpu_ms', 1e101)):
            samples = copy.deepcopy(self.samples)
            samples[0][field] = value
            with self.assertRaises(ValueError):
                evaluate(self.report, samples)

    def test_unavailable_proc_io_cannot_turn_into_zero(self):
        self.samples[0]['proc_io_available'] = False
        with self.assertRaisesRegex(ValueError, 'unavailable proc IO'):
            evaluate(self.report, self.samples)
        for key in summary.COUNTERS:
            if key.startswith('proc_io_'):
                self.samples[0][key] = None
        self.assertIsNotNone(evaluate(self.report, self.samples))

    def test_report_journal_mismatch_rejected(self):
        for field, value in (('tls', True), ('collector_mode', 'hash-framing'), ('session_index', 1),
                             ('label', 'go-a'), ('workload', 'unknown'), ('round', 7), ('warmup', False),
                             ('implementation_order', 'XX'), ('workload_seed', 0)):
            samples = copy.deepcopy(self.samples)
            samples[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                evaluate(self.report, samples)
        self.samples[2]['wall_ms'] += 1
        with self.assertRaisesRegex(ValueError, 'report/journal metric mismatch'):
            evaluate(self.report, self.samples)

    def test_reject_duplicate_truncated_extra_and_partial_journal(self):
        for samples in (self.samples[:-1], self.samples + self.samples[:1], self.samples[:1] + self.samples):
            with self.assertRaises(ValueError):
                evaluate(self.report, samples)
        with self.assertRaisesRegex(ValueError, 'final line'):
            summary.summarize_bytes(encoded(self.report), encoded(self.samples[0]))

    def test_reject_malformed_and_inconsistent_provenance(self):
        changes = [('schema_version', True), ('tls', 0), ('stable_manifest_sha256', '0' * 64),
                   ('recorded_measured_samples', 999), ('expected_measured_samples', 99)]
        for field, value in changes:
            report = copy.deepcopy(self.report)
            report[field] = value
            with self.assertRaises(ValueError):
                evaluate(report, self.samples)
        report = copy.deepcopy(self.report)
        report['measured_roles']['go']['selected'] = identity('wrong')
        with self.assertRaises(ValueError):
            evaluate(report, self.samples)
        self.samples[0]['contract']['workload_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'contract workload'):
            evaluate(self.report, self.samples)

    def test_valid_flags_cannot_promote_invalid_contract(self):
        for changes in (dict(errors=['failure']), dict(contract_validated=False), dict(capture_complete=False),
                        dict(collector_exception=True), dict(timed_out=True)):
            samples = copy.deepcopy(self.samples)
            samples[0].update(changes)
            with self.assertRaises(ValueError):
                evaluate(self.report, samples)

    def test_no_invented_ratios_or_budget_results_even_source_ratio_present(self):
        self.report['summaries']['one-row']['candidate_over_go']['wall_ms'] = {'ratio': 0.5}
        result = evaluate(self.report, self.samples)
        def keys(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield k
                    yield from keys(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from keys(v)
        self.assertFalse(set(keys(result)) & {'ratio', 'candidate_over_go', 'budget_pass', 'confidence_intervals'})
        self.assertEqual(result['source_restrictions']['one-row']['ratio_quarantine'],
                         self.report['summaries']['one-row']['ratio_quarantine'])
        self.assertEqual(result['acceptance'], 'not_assessed')

    def test_bounds_fail_before_output(self):
        with patch.object(summary, 'REPORT_BYTES', 1), self.assertRaises(ValueError):
            evaluate(self.report, self.samples)
        with patch.object(summary, 'JOURNAL_BYTES', 1), self.assertRaises(ValueError):
            evaluate(self.report, self.samples)
        with patch.object(summary, 'LINE_BYTES', 1), self.assertRaises(ValueError):
            evaluate(self.report, self.samples)
        with patch.object(summary, 'MAX_RECORDS', 1), self.assertRaises(ValueError):
            evaluate(self.report, self.samples)

    def files(self, root):
        report = root / 'report.json'
        report.write_bytes(b'  ' + encoded(self.report) + b'\n')
        journal = root / 'samples.jsonl'
        journal.write_bytes(b''.join(encoded(s) + b'\n' for s in self.samples))
        return report, journal

    def test_exact_read_hashes_standalone_output_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as folder:
            root = Path(folder)
            report, journal = self.files(root)
            output = root / 'description.json'
            info = summary.summarize_paths(report, output)
            value = json.loads(output.read_bytes())
            for key, path in (('report', report), ('journal', journal)):
                self.assertEqual(value['inputs'][key]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertEqual(value['inputs'][key]['size_bytes'], path.stat().st_size)
            self.assertEqual(info['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                summary.summarize_paths(report, output)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_drift_and_symlink_repoint_refused(self):
        for drift in ('report', 'journal', 'symlink'):
            with tempfile.TemporaryDirectory(dir=CACHE) as folder:
                root = Path(folder)
                report, journal = self.files(root)
                if drift == 'symlink':
                    original = root / 'original.json'
                    report.rename(original)
                    report.symlink_to(original)
                real_summarize = summary.summarize_bytes
                def mutate(*args):
                    result = real_summarize(*args)
                    if drift == 'symlink':
                        other = root / 'other.json'
                        other.write_bytes(report.read_bytes())
                        report.unlink()
                        report.symlink_to(other)
                    else:
                        path = report if drift == 'report' else journal
                        path.write_bytes(path.read_bytes() + b' ')
                    return result
                with patch.object(summary, 'summarize_bytes', side_effect=mutate), self.assertRaisesRegex(ValueError, 'drift'):
                    summary.summarize_paths(report, root / 'out.json')
                self.assertFalse((root / 'out.json').exists())

    def test_output_alias_dangling_symlink_and_size_refused(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as folder:
            root = Path(folder)
            report, journal = self.files(root)
            for output in (report, journal):
                with self.assertRaises(ValueError):
                    summary.summarize_paths(report, output)
            output = root / 'out.json'
            output.symlink_to(root / 'nonexistent')
            with self.assertRaises(ValueError):
                summary.summarize_paths(report, output)
            output.unlink()
            with patch.object(summary, 'OUTPUT_BYTES', 1), self.assertRaisesRegex(ValueError, 'output byte limit'):
                summary.summarize_paths(report, output)
            self.assertFalse(output.exists())

    def test_cli_refusal_and_success_without_process(self):
        with tempfile.TemporaryDirectory(dir=CACHE) as folder:
            root = Path(folder)
            report, _ = self.files(root)
            with patch('sys.stdout'), patch('sys.stderr'):
                self.assertEqual(summary.main(['--report', str(report), '--out', str(root / 'out.json')]), 0)
                self.assertEqual(summary.main(['--report', str(report), '--out', str(root / 'out.json')]), 1)


class CollectorSchemaRegressions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=CACHE)
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()
        self.assertEqual(dict(FORBIDDEN_CALLS), {})

    def describe(self, args):
        output = self.root / 'description.json'
        summary.summarize_paths(args.out / 'report.json', output)
        return json.loads(output.read_bytes())

    def test_real_zero_row_finite_empty_batch_primary_and_control(self):
        for mode in ('full', 'hash-framing'):
            with self.subTest(mode=mode):
                root = self.root / mode
                root.mkdir()
                args, code = mocked_collector(root, workloads=('one-row', 'empty-batch'), mode=mode)
                self.assertEqual(code, 0)
                output = root / 'description.json'
                summary.summarize_paths(args.out / 'report.json', output)
                result = json.loads(output.read_bytes())
                self.assertEqual(result['acceptance'], 'not_assessed')
                for label in ('go', 'candidate'):
                    group = result['groups']['empty-batch'][label]['measured']
                    self.assertEqual(group['valid'], 3)
                    self.assertEqual(group['metrics']['rows']['median'], 0)
                    self.assertEqual(group['metrics']['first_complete_record_ms']['available'], 0)
                    self.assertEqual(group['metrics']['first_complete_record_ms']['missing'], 3)
                    self.assertEqual(group['source_flags']['first_record_observed_after_native_exit'], {'null': 3})

    def test_real_zero_row_functional_idle_keeps_sessions(self):
        args, code = mocked_collector(self.root, workloads=('idle',), tier='functional', repetitions=2)
        self.assertEqual(code, 0)
        result = self.describe(args)
        self.assertEqual(result['cohort']['tier'], 'functional')
        measured = result['groups']['idle']['go']['measured']
        self.assertNotIn('metrics', measured)
        for session in measured['sessions'].values():
            self.assertEqual(session['metrics']['rows']['median'], 0)
            self.assertEqual(session['source_flags']['first_record_observed_after_native_exit'], {'null': 1})

    def test_real_invalid_no_first_record_is_separate(self):
        def mutate(n, value):
            if n == 5:  # Two contract A/A samples, two warmups, first measured observation.
                value.update(valid=False, contract_validated=False, errors=['mock no record'], first_record_ns=None,
                    first_complete_record_ms=None, fixture_to_first_complete_record_ms=None,
                    first_record_observed_after_native_exit=None, minor_faults=999999)
            return value
        args, code = mocked_collector(self.root, mutate=mutate)
        self.assertEqual(code, 1)
        result = self.describe(args)
        self.assertEqual(len(result['invalid_records']), 1)
        label = result['invalid_records'][0]['label']
        group = result['groups']['one-row'][label]['measured']
        self.assertEqual((group['valid'], group['invalid']), (2, 1))
        self.assertEqual(group['metrics']['minor_faults']['maximum'], 4828)

    def test_signed_invalid_derived_uncaptured_dogets_excluded_from_metrics(self):
        def mutate(n, value):
            if n == 5:
                value.update(valid=False, contract_validated=False, errors=['captured DoGet count exceeds exact summary'],
                             minor_faults=999999)
                value['request_evidence']['uncaptured_dogets'] = -1
            return value
        args, code = mocked_collector(self.root, mutate=mutate)
        self.assertEqual(code, 1)
        result = self.describe(args)
        label = result['invalid_records'][0]['label']
        group = result['groups']['one-row'][label]['measured']
        self.assertEqual(group['metrics']['minor_faults']['maximum'], 4828)
        self.assertEqual(group['observation_coverage']['invalid']['request_evidence.uncaptured_dogets']['total'], -1)

    def test_actual_finalizer_missing_counts_is_refused_without_derivation(self):
        args, code = mocked_collector(self.root, finalization_failure=True)
        self.assertEqual(code, 1)
        report = json.loads((args.out / 'report.json').read_bytes())
        self.assertEqual(report['summaries'], {})
        self.assertNotIn('expected_measured_samples', report)
        self.assertNotIn('recorded_measured_samples', report)
        self.assertTrue(report['failure'].startswith('finalization'))
        with self.assertRaisesRegex(ValueError, 'expected measured count mismatch'):
            self.describe(args)
        self.assertFalse((self.root / 'description.json').exists())

    def test_actual_post_provenance_missing_journal_has_concise_refusal(self):
        args, code = mocked_collector(self.root, lock_failure=True)
        self.assertEqual(code, 1)
        self.assertFalse((args.out / 'samples.jsonl').exists())
        with self.assertRaisesRegex(ValueError, '^missing retained samples.jsonl; campaign has no retained journal$'):
            self.describe(args)
        with patch('sys.stderr') as stderr:
            self.assertEqual(summary.main(['--report', str(args.out / 'report.json'),
                                           '--out', str(self.root / 'description.json')]), 1)
        self.assertIn('missing retained samples.jsonl', stderr.write.call_args_list[0].args[0])
        self.assertFalse((self.root / 'description.json').exists())

    def test_historical_declared_symlink_and_noncanonical_paths_bind_by_sha(self):
        intermediate = self.root / 'intermediate'
        intermediate.mkdir()
        link = self.root / 'declared-link'
        link.symlink_to('/bin/true')
        raw = str(intermediate / '..' / link.name)
        args, code = mocked_collector(self.root, declaration_paths={r: raw for r in ('go', 'meter', 'candidate')})
        self.assertEqual(code, 0)
        # Today's path no longer describes the captured binary. Offline binding must not resolve it.
        replacement = self.root / 'replacement'
        replacement.write_bytes(b'changed historical target')
        link.unlink()
        link.symlink_to(replacement)
        intermediate.rmdir()
        result = self.describe(args)
        for role in ('go', 'candidate', 'meter'):
            paths = result['role_path_provenance'][role]
            self.assertEqual(paths['manifest_declared_path'], raw)
            self.assertEqual(paths['bound_declared_path'], str(Path('/bin/true').resolve()))
        report = json.loads((args.out / 'report.json').read_bytes())
        report['manifest']['baselines']['pb-go']['sha256'] = '0' * 64
        report['stable_manifest_sha256'] = summary.canonical_hash(report['manifest'])
        with self.assertRaisesRegex(ValueError, 'embedded role declaration mismatch'):
            summary.summarize_bytes(encoded(report), (args.out / 'samples.jsonl').read_bytes())

    def test_report_symlink_uses_captured_resolved_sibling_not_decoy(self):
        args, code = mocked_collector(self.root)
        self.assertEqual(code, 0)
        linked = self.root / 'linked'
        linked.mkdir()
        report_link = linked / 'report.json'
        report_link.symlink_to(args.out / 'report.json')
        decoy = [json.loads(line) for line in (args.out / 'samples.jsonl').read_bytes().splitlines()]
        for sample in decoy:
            sample['minor_faults'] = 666666
        (linked / 'samples.jsonl').write_bytes(b''.join(encoded(s) + b'\n' for s in decoy))
        output = self.root / 'description.json'
        summary.summarize_paths(report_link, output)
        result = json.loads(output.read_bytes())
        self.assertEqual(result['inputs']['report']['path'], str(report_link))
        self.assertEqual(result['inputs']['journal']['resolved_path'], str(args.out / 'samples.jsonl'))
        self.assertEqual(result['groups']['one-row']['go']['measured']['metrics']['minor_faults']['median'], 4828)

    def test_flag_types_and_signed_coverage_remain_strict(self):
        report, samples = fixture(workloads=1)
        for value in (0, 1, 'null', [], {}):
            changed = copy.deepcopy(samples)
            changed[0]['first_record_observed_after_native_exit'] = value
            with self.assertRaisesRegex(ValueError, 'boolean or null'):
                evaluate(report, changed)
        samples[0]['request_evidence'] = {'uncaptured_dogets': -1}
        with self.assertRaisesRegex(ValueError, 'invalid integer'):
            evaluate(report, samples)
        samples[0].update(valid=False, contract_validated=False, errors=['invalid mock'])
        report.update(passed=False, comparison_contracts_passed=False)
        for value in (True, -1.5):
            samples[0]['request_evidence']['uncaptured_dogets'] = value
            with self.assertRaisesRegex(ValueError, 'invalid integer'):
                evaluate(report, samples)


if __name__ == '__main__':
    unittest.main()
