"""Collector correctness tests; no performance or profiler campaign."""
import json
import mmap
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import phase6_workloads as p6


class Root:
    def setUp(self):
        p6.TMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='p6-test-', dir=p6.TMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


class ObserverAudit(Root, unittest.TestCase):
    def stat(self, state='S', start='123'):
        fields = ['0'] * 24
        fields[0], fields[11], fields[12], fields[19] = state, '7', '3', start
        return '99 (fake CLI (worker)) ' + ' '.join(fields) + '\n'

    def proc(self, state='S', rss='64'):
        root = self.root / 'proc'
        parent = root / '42/task/42'
        parent.mkdir(parents=True)
        (parent / 'children').write_text('99')
        child = root / '99'
        (child / 'fd').mkdir(parents=True)
        for fd in ('0', '1'):
            (child / 'fd' / fd).write_text('')
        status = f'State:\t{state} (state)\nThreads:\t2\n'
        if rss is not None:
            status += f'VmRSS:\t{rss} kB\n'
        (child / 'status').write_text(status)
        (child / 'stat').write_text(self.stat(state))
        return root

    def test_zombie_zero_rss_is_omitted(self):
        root = self.proc('Z', '0')
        self.assertIsNone(p6.proc_sample(42, root))

    def test_dead_state_is_omitted(self):
        root = self.proc('X', '0')
        self.assertIsNone(p6.proc_sample(42, root))

    def test_live_missing_rss_is_unavailable_not_zero(self):
        root = self.proc('S', None)
        self.assertIsNone(p6.proc_sample(42, root))

    def test_live_sample_records_actual_observation_interval(self):
        root = self.proc()
        with patch.object(p6, 'clock_ns', side_effect=[100, 200]):
            value = p6.proc_sample(42, root)
        self.assertEqual(value['observed_start_ns'], 100)
        self.assertEqual(value['t_ns'], 200)
        self.assertEqual(value['process_state'], 'S')
        self.assertEqual(value['rss_bytes'], 64 * 1024)
        self.assertEqual(value['fds'], 2)
        self.assertEqual(value['tasks'], 2)

    def test_exit_during_fd_observation_is_omitted(self):
        root = self.proc()
        original = Path.read_text
        reads = 0
        def read(path, *args, **kwargs):
            nonlocal reads
            if path == root / '99/stat':
                reads += 1
                if reads == 2:
                    return self.stat('Z')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            self.assertIsNone(p6.proc_sample(42, root))
        self.assertEqual(reads, 2)

    def test_changed_pid_start_time_is_omitted(self):
        root = self.proc()
        original = Path.read_text
        reads = 0
        def read(path, *args, **kwargs):
            nonlocal reads
            if path == root / '99/stat':
                reads += 1
                if reads == 2:
                    return self.stat('S', '456')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            self.assertIsNone(p6.proc_sample(42, root))


@unittest.skipUnless(p6.run.DEFAULT_METER.exists() and sys.platform == 'linux', 'prebuilt native Linux meter required')
class ObserverNative(Root, unittest.TestCase):
    def env(self):
        return dict(p6.run.isolated_env(self.root), TMPDIR=str(p6.TMP), GOTMPDIR=str(p6.TMP),
                    CLAUDE_CODE_TMPDIR=str(p6.TMP))

    def test_observer_rss_is_not_charged_to_native_child(self):
        def own_rss():
            lines = Path('/proc/self/status').read_text().splitlines()
            return int(next(line.split()[1] for line in lines if line.startswith('VmRSS:'))) * 1024
        before_rss = own_rss()
        before = p6.collect([str(Path('/bin/true').resolve())], self.env(), self.root / 'before',
                            p6.run.DEFAULT_METER, 5)
        with mmap.mmap(-1, 64 << 20) as pages:
            for offset in range(0, len(pages), 4096):
                pages[offset] = 1
            inflated_rss = own_rss()
            after = p6.collect([str(Path('/bin/true').resolve())], self.env(), self.root / 'inflated',
                               p6.run.DEFAULT_METER, 5)
            self.assertGreater(inflated_rss - before_rss, 48 << 20)
            self.assertLess(after['peak_rss_bytes'], inflated_rss / 2)
            self.assertLess(after['peak_rss_bytes'], 32 << 20)
            self.assertLess(abs(after['peak_rss_bytes'] - before['peak_rss_bytes']), 8 << 20)
        self.assertEqual(before['errors'], [])
        self.assertEqual(after['errors'], [])
        self.assertEqual(after['resource_source'], 'native_child_wait4')
        p6.write_json(self.root / 'rss-functional-proof.json', dict(scope='functional_resource_scope_only',
            observer_rss_before=before_rss, observer_rss_inflated=inflated_rss,
            child_peak_before=before['peak_rss_bytes'], child_peak_with_inflated_observer=after['peak_rss_bytes']))

    def test_first_complete_record_waits_for_lf_not_fixture_trace(self):
        gate, milestone = self.root / 'prefix-observed', self.root / 'lf-milestone.json'
        script = '''import json,os,sys,time
from pathlib import Path
os.write(1,b'{"sequence":0}')
while not Path(sys.argv[1]).exists(): time.sleep(0.001)
Path(sys.argv[2]).write_text(json.dumps({'before_lf_ns':time.clock_gettime_ns(time.CLOCK_MONOTONIC)}))
os.write(1,b'\\n\\n')
'''
        original = p6.Output.feed
        observed_prefix = []
        def feed(output, data, now):
            original(output, data, now)
            if output.first_byte is not None and output.first_record is None:
                observed_prefix.append(now)
                gate.touch()
        with patch.object(p6.Output, 'feed', feed), patch.object(p6.Fixture, 'summary',
                side_effect=AssertionError('fixture validation must not gate complete-record observation')):
            result = p6.collect([sys.executable, '-c', script, str(gate), str(milestone)], self.env(),
                                self.root / 'complete-record', p6.run.DEFAULT_METER, 5)
        lf = p6.read_json(milestone)['before_lf_ns']
        self.assertTrue(observed_prefix)
        self.assertEqual(result['errors'], [])
        self.assertLess(result['first_stdout_ns'], lf)
        self.assertGreaterEqual(result['first_record_ns'], lf)
        self.assertEqual((result['rows'], result['batches']), (1, 1))
        p6.write_json(self.root / 'complete-record-functional-proof.json',
            dict(scope='functional_visibility_order_only', first_stdout_ns=result['first_stdout_ns'],
                 prefix_observed_ns=observed_prefix[0], before_lf_ns=lf,
                 first_complete_record_ns=result['first_record_ns'],
                 native_start_ns=result['started_monotonic_ns'], native_end_ns=result['ended_monotonic_ns']))


class Collector(Root, unittest.TestCase):

    def expectation(self, data, rows, batches):
        return dict(expected_stdout_sha256=p6.hashlib.sha256(data).hexdigest(),
                    expected_stdout_bytes=len(data), expected_rendered_rows=rows, batches=batches)

    def test_hash_framing_parses_only_first_record_and_keeps_exact_hash(self):
        output = p6.Output(self.root, collector_mode='hash-framing')
        data = b'{"sequence":0}\n\n{"sequence":1}\n\n'
        for byte in data:
            output.feed(bytes([byte]), 10)
        result = output.finish()
        self.assertEqual(result['json_parse_attempts'], 1)
        self.assertIsNone(result['sequence_errors'])
        self.assertEqual((result['rows'], result['batches']), (2, 2))
        self.assertEqual(p6.output_expectation_issues(result, self.expectation(data, 2, 2)), [])

    def test_hash_framing_bad_later_record_rejected_by_full_hash(self):
        output = p6.Output(self.root, collector_mode='hash-framing')
        expected = b'{"sequence":0}\n{"sequence":1}\n\n'
        actual = b'{"sequence":0}\n{"sequence":!}\n\n'
        output.feed(actual, 10)
        result = output.finish()
        self.assertEqual(result['json_parse_attempts'], 1)
        self.assertEqual(result['invalid_records'], 0)  # Later frames are deliberately not parsed.
        self.assertIn('fixture expectation mismatch: stdout_sha256',
                      p6.output_expectation_issues(result, self.expectation(expected, 2, 1)))

    def test_hash_framing_first_record_still_requires_valid_complete_object(self):
        output = p6.Output(self.root, collector_mode='hash-framing')
        output.feed(b'bad\n{"sequence":0}', 10)
        self.assertIsNone(output.first_record)
        output.feed(b'\n\n', 20)
        result = output.finish()
        self.assertEqual(result['first_record_ns'], 20)
        self.assertEqual(result['invalid_records'], 1)

    def test_hash_framing_partial_later_record_and_missing_hash_fail(self):
        output = p6.Output(self.root, collector_mode='hash-framing')
        output.feed(b'{"sequence":0}\n{"sequence":1}', 10)
        result = output.finish()
        self.assertEqual(result['invalid_records'], 1)
        self.assertIn('fixture expectation mismatch: stdout_sha256',
                      p6.output_expectation_issues(result, {}))

    def summary(self, dogets=3001):
        stream = dict(dogets=dogets, first_doget_ns=30, last_doget_ns=90,
            request_validation=dict(auth_checks=dogets, ticket_checks=dogets, invalid_auth=0,
                                    ticket_mismatches=0, unexpected_requests=0, doget_before_about=0))
        summary = dict(about_requests=1, http_requests=1, dogets=dogets,
                       first_about_ns=10, last_about_ns=10, about_completed_ns=20)
        return summary, stream

    def test_exact_summary_accepts_large_counts_without_retained_events(self):
        summary, stream = self.summary()
        self.assertEqual(p6.request_summary_issues(summary, stream, 3001), [])

    def test_exact_summary_rejects_unobserved_auth_or_ticket_coverage(self):
        summary, stream = self.summary()
        del stream['request_validation']['auth_checks']
        stream['request_validation']['ticket_checks'] = 1
        errors = p6.request_summary_issues(summary, stream, 3001)
        self.assertTrue(any('auth_checks' in e for e in errors))
        self.assertTrue(any('ticket_checks' in e for e in errors))

    def test_exact_summary_rejects_bad_auth_ticket_or_order_even_if_uncaptured(self):
        for field in ('invalid_auth', 'ticket_mismatches', 'unexpected_requests', 'doget_before_about'):
            summary, stream = self.summary()
            stream['request_validation'][field] = 1
            self.assertTrue(any(field in e for e in p6.request_summary_issues(summary, stream, 3001)))

    def test_exact_summary_first_emission_survives_all_event_loss(self):
        output = p6.Output(self.root)
        output.feed(b'{"sequence":0}\n\n', 10000000)
        result = output.finish()
        result['errors'] = []
        (self.root / 'fixture.events.jsonl').write_text('')
        p6.correlate(self.root, result, dict(first_nonempty_batch_emit_ns=1000000))
        self.assertEqual(result['fixture_to_first_complete_record_ms'], 9)
        self.assertEqual(result['fixture_first_record_emission_source'], 'exact_summary')
        self.assertEqual(result['batch_visibility']['matched'], 0)
        self.assertEqual(result['batch_visibility']['uncorrelated'], 1)

    def test_first_complete_record_requires_lf_and_valid_object(self):
        output = p6.Output(self.root)
        output.feed(b'{"sequence":', 10)
        output.feed(b'0}', 20)
        self.assertIsNone(output.first_record)
        output.feed(b'\n', 30)
        self.assertEqual(output.first_record, 30)
        output.feed(b'\n{"sequence":1}\n\n', 40)
        result = output.finish()
        self.assertEqual((result['rows'], result['batches'], result['invalid_records']), (2, 2, 0))
        self.assertEqual(result['sequence_errors'], 0)

    def test_invalid_and_partial_records_never_count_as_complete(self):
        output = p6.Output(self.root)
        output.feed(b'[]\n{bad}\n{"sequence":0,"v":NaN}\n{"sequence":0}', 10)
        result = output.finish()
        self.assertIsNone(result['first_record_ns'])
        self.assertEqual(result['invalid_records'], 4)

    def test_bounded_retention_hashes_every_byte(self):
        output = p6.Output(self.root, prefix_bytes=3, event_bytes=30)
        data = b'{"sequence":0}\n\n{"sequence":1}\n\n'
        output.feed(data, 1)
        result = output.finish()
        self.assertEqual((self.root / 'stdout.prefix.bin').read_bytes(), data[:3])
        self.assertEqual(result['stdout_sha256'], p6.hashlib.sha256(data).hexdigest())
        self.assertEqual(result['stdout_bytes'], len(data))
        self.assertGreater(result['visibility_dropped_bytes'], 0)
        self.assertLessEqual((self.root / 'visibility.jsonl').stat().st_size, 30)

    def test_oversized_row_discards_until_lf_then_recovers(self):
        output = p6.Output(self.root, record_bytes=16)
        for _ in range(30):
            output.feed(b'xxxxxxxxxx', 10)
            self.assertLessEqual(len(output.partial), 16)
        output.feed(b'\n{"sequence":0}\n\n', 20)
        result = output.finish()
        self.assertEqual((result['invalid_records'], result['rows'], result['batches']), (1, 1, 1))

    def test_gaps_and_duplicates_invalidate_sequence(self):
        output = p6.Output(self.root)
        output.feed(b'{"sequence":1}\n{"sequence":1}\n', 10)
        self.assertEqual(output.finish()['sequence_errors'], 1)

    def test_empty_batches_and_split_utf8(self):
        output = p6.Output(self.root)
        data = '{"sequence":0,"message":"日志"}\n\n\n'.encode()
        for byte in data:
            output.feed(bytes([byte]), 10)
        result = output.finish()
        self.assertEqual((result['rows'], result['batches'], result['invalid_records']), (1, 2, 0))

    def test_online_trend_has_no_retained_history(self):
        trend = p6.Trend()
        for n in range(10000):
            trend.add(n, 2 * n + 1)
        result = trend.result()
        self.assertEqual(result['slope_per_second'], 2)
        self.assertEqual(result['samples'], 10000)
        self.assertFalse(any(isinstance(x, list) for x in vars(trend).values()))

    def test_bounded_json_reader_rejects_overflow(self):
        path = self.root / 'large.json'
        path.write_bytes(b' ' * 10)
        with self.assertRaisesRegex(ValueError, 'bounded'):
            p6.read_json(path, limit=5)

    def test_jsonl_reader_rejects_truncated_and_oversized_events(self):
        path = self.root / 'x.jsonl'
        path.write_bytes(b'{}')
        with self.assertRaises(ValueError):
            list(p6.jsonl(path))
        path.write_bytes(b'xxxxxxxx\n')
        with self.assertRaises(ValueError):
            list(p6.jsonl(path, 5))

    def test_correlation_keeps_missing_sampled_events_unavailable(self):
        output = p6.Output(self.root)
        output.feed(b'{"sequence":0}\n\n{"sequence":1}\n\n', 10000000)
        result = output.finish()
        result['errors'] = []
        (self.root / 'fixture.events.jsonl').write_text(json.dumps(dict(kind='batch_emit_start',
            batch=0, rows=1, t_ns=1000000)) + '\n')
        p6.correlate(self.root, result)
        self.assertEqual(result['fixture_to_first_complete_record_ms'], 9)
        self.assertEqual(result['batch_visibility']['matched'], 1)
        self.assertEqual(result['batch_visibility']['uncorrelated'], 1)

    def test_tmp_artifacts_rejected(self):
        with self.assertRaises(ValueError):
            p6.safe_path('/tmp/p6-artifact')

    def test_quiet_handshake_and_exclusive_lock_required(self):
        path = self.root / 'lock'
        with self.assertRaises(ValueError):
            with p6.quiet_lock(path, ''):
                pass
        with patch.object(p6.fcntl, 'flock', side_effect=[None, BlockingIOError(), None]) as flock:
            with p6.quiet_lock(path, 'unit-only'):
                with self.assertRaises(BlockingIOError):
                    with p6.quiet_lock(path, 'unit-only'):
                        pass
        self.assertEqual([call.args[1] for call in flock.call_args_list],
                         [p6.fcntl.LOCK_EX | p6.fcntl.LOCK_NB] * 2 + [p6.fcntl.LOCK_UN])

    def test_diagnostics_require_completed_sidecar_and_current_counter_name(self):
        with self.assertRaises(ValueError):
            p6.tail_resources(self.root)
        counters = ('subscriptions_started', 'flight_messages_received', 'batches_decoded', 'rows_decoded',
                    'max_flight_payload_bytes', 'max_dictionary_buffer_bytes', 'max_batch_buffer_bytes',
                    'max_staging_length_bytes', 'max_staging_capacity_bytes', 'last_staging_capacity_bytes',
                    'max_application_messages_inflight', 'max_renderer_scratch_capacity_bytes',
                    'last_renderer_scratch_capacity_bytes')
        resources = dict.fromkeys(counters, 1)
        resources['semantics'] = 'owner-reported maxima, not read-ahead'
        report = dict(schema_version=1, complete=True, implementation='rust', tail_resources=resources)
        path = self.root / 'pb-diagnostics-1.json'
        p6.write_json(path, report)
        self.assertEqual(p6.tail_resources(self.root)['resources'], resources)
        report['complete'] = False
        p6.write_json(path, report)
        with self.assertRaises(ValueError):
            p6.tail_resources(self.root)
        report['complete'] = True
        resources['max_application_messages_inflight'] = None
        p6.write_json(path, report)
        with self.assertRaises(ValueError):
            p6.tail_resources(self.root)


class Workloads(unittest.TestCase):
    def test_cpu_microseconds_per_complete_row_includes_total_cpu(self):
        self.assertEqual(p6.cpu_us_per_row(4, 2), 2000)
        self.assertIsNone(p6.cpu_us_per_row(4, 0))
        self.assertIsNone(p6.cpu_us_per_row(None, 2))

    def test_all_modes_support_30_and_60_minutes(self):
        for duration in (1800, 3600):
            cases = p6.workloads(duration, 'soak')
            self.assertEqual({w['name'] for w in cases}, set(p6.MODES))
            self.assertTrue(all(w['duration_s'] == duration for w in cases))
            self.assertGreater(cases[2]['scheduled_reconnects'], 2000)
            self.assertEqual(cases[3]['stream']['dictionary']['max_entries'], 4096)

    def test_functional_tier_has_same_modes_with_short_duration(self):
        cases = p6.workloads(0.2, 'functional')
        self.assertEqual({w['name'] for w in cases}, set(p6.MODES))
        self.assertEqual(cases[0]['stream']['subscriptions'][0]['ops'][0]['duration_ms'], 200)

    def test_finite_has_explicit_failure_termination(self):
        for workload in p6.workloads():
            end = workload['stream']['subscriptions'][-1]['ops'][-1]
            self.assertEqual((end['op'], end['code']), ('status', 3))

    def test_duration_bounds(self):
        for value in (0, -1, 3601):
            with self.assertRaises(ValueError):
                p6.workloads(value, 'soak')

    def test_seeded_sequential_interleaving_and_excluded_warmups(self):
        a = list(p6.schedule(['go', 'candidate'], 5, 30, 2026))
        self.assertEqual(a, list(p6.schedule(['go', 'candidate'], 5, 30, 2026)))
        self.assertNotEqual(a, list(p6.schedule(['go', 'candidate'], 5, 30, 2027)))
        self.assertEqual(sum(not x['warmup'] for x in a), 60)
        for i in range(0, len(a), 2):
            self.assertEqual({x['label'] for x in a[i:i + 2]}, {'go', 'candidate'})

    def test_invalid_samples_excluded_from_summary(self):
        result = p6.summarize([dict(valid=False, wall_ms=0), dict(valid=True, wall_ms=20)])
        self.assertEqual(result['wall_ms']['median'], 20)
        self.assertIsNone(result['first_complete_record_ms']['median'])


class Campaign(Root, unittest.TestCase):
    def args(self):
        manifest = self.root / 'manifest.json'
        fixture = self.root / 'fixture'
        fixture.write_bytes(Path('/bin/true').read_bytes())
        fixture.chmod(0o555)
        sources = {}
        paths = {}
        for name in ('main.go', 'phase6.go', 'go.mod', 'go.sum'):
            source = self.root / name
            source.write_text(name)
            source.chmod(0o444)
            sources[name] = p6.file_info(source)['sha256']
            paths[name] = str(source)
        go_toolchain = self.root / 'go-toolchain'
        go_toolchain.write_bytes(Path('/bin/true').read_bytes())
        build = dict(binary=p6.file_info(fixture), sources=sources, source_paths=paths,
                     toolchain=dict(path=str(go_toolchain), sha256=p6.file_info(go_toolchain)['sha256'], version='go1.25.4'))
        build_manifest = self.root / 'fixture-manifest.json'
        p6.write_json(build_manifest, build)
        normal = dict(p6.file_info('/bin/true'), argv=list(p6.NORMAL_RELEASE_ARGV), features=[],
            cloud_synthetic_token_enabled=False, build_kind='normal_release')
        candidate_manifest = self.root / 'candidate-build.json'
        p6.write_json(candidate_manifest, dict(builds=dict(pb=normal)))
        p6.write_json(manifest, dict(sources={'oracle': 'pinned'}, toolchains={'go': 'pinned'},
            build_flags=dict(candidate=dict(pb=normal)), dependencies={'locked': True},
            baselines={'pb-go': p6.file_info('/bin/true')}, meter=p6.file_info('/bin/true'),
            candidate_manifest=p6.file_info(candidate_manifest), budgets=p6.read_json(p6.PREDECLARED_BUDGETS),
            budgets_sha256=p6.file_info(p6.PREDECLARED_BUDGETS)['sha256'],
            fixture_manifest=p6.file_info(build_manifest), fixture_build=build))
        return SimpleNamespace(out=self.root / 'campaign', manifest=manifest, go=Path('/bin/true'),
            candidate=Path('/bin/true'), fixture=fixture, meter=Path('/bin/true'),
            duration_s=1, tier='finite', seed=2026, workload=['one-row'], warmups=1, repetitions=2,
            aa_repetitions=2, tls=False, collector_mode='full', timing_aa=False, session_index=0,
            lock=p6.PERFORMANCE_LOCK, quiet_window_token='mock-test-only', window_budget_s=900)

    def sample(self, *args, **kwargs):
        return dict(valid=True, wall_ms=1, cpu_ms=4, cpu_us_per_row=500, errors=[],
            cpu_screen=dict(comparative_admissible=True, throughput_admissible=True),
            production=dict(throughput_admissible=True),
            contract=dict(stdout_sha256='x', stdout_bytes=128, rendered_rows=8, batches=1, stderr_sha256='y',
                termination={'exit': 1}, requests=1, ticket_sha256='t', request_validation={'auth_checks': 1},
                workload_sha256='w', sink={}, tls=False, config_before='c', config_after='c'))

    def test_mock_campaign_excludes_warmups_and_records_identities(self):
        args = self.args()
        with patch.object(p6, 'run_sample', side_effect=self.sample) as sample, patch.object(p6, 'quiet_lock'):
            self.assertEqual(p6.campaign(args), 0)
        report = p6.read_json(args.out / 'report.json')
        self.assertEqual(sample.call_count, 8)
        self.assertEqual(report['summaries']['one-row']['go']['wall_ms']['n'], 2)
        self.assertEqual(len(list(p6.jsonl(args.out / 'samples.jsonl'))), 6)
        self.assertIn('sha256', report['collector'])
        self.assertIsNone(report['excluded_costs']['dependency_size_bytes'])
        self.assertTrue(report['summaries']['one-row']['contract_comparison']['comparable'])
        self.assertEqual(report['summaries']['one-row']['candidate_over_go']['wall_ms']['ratio'], 1)
        self.assertEqual(report['summaries']['one-row']['go']['cpu_us_per_row']['median'], 500)

    def test_individually_valid_different_output_quarantines_ratios(self):
        args = self.args()
        def sample(*a, **kwargs):
            value = self.sample()
            if a[4].name.endswith('-candidate'):
                value['wall_ms'] = 0.5
                value['contract'].update(stdout_sha256='different', stdout_bytes=256, rendered_rows=16, batches=2)
            return value
        with patch.object(p6, 'run_sample', side_effect=sample), patch.object(p6, 'quiet_lock'):
            self.assertEqual(p6.campaign(args), 0)  # Absolute observations remain individually valid.
        report = p6.read_json(args.out / 'report.json')
        group = report['summaries']['one-row']
        self.assertTrue(report['passed'])
        self.assertFalse(report['comparison_contracts_passed'])
        self.assertEqual(group['candidate']['wall_ms']['median'], 0.5)
        self.assertEqual(group['candidate']['wall_ms']['n'], 2)
        self.assertFalse(group['contract_comparison']['comparable'])
        self.assertIn('stdout_sha256', group['contract_comparison']['differing_fields'])
        self.assertIn('rendered_rows', group['contract_comparison']['differing_fields'])
        self.assertTrue(group['ratio_quarantine'])
        self.assertTrue(all(ratio is None for ratio in group['candidate_over_go'].values()))

    def test_aa_contract_is_not_contaminated_by_timestamps(self):
        args = self.args()
        count = 0
        def sample(*a, **kwargs):
            nonlocal count
            count += 1
            value = self.sample()
            value.update(first_record_ns=count, first_doget_ms=count, fixture_terminal_to_exit_ms=count)
            return value
        with patch.object(p6, 'run_sample', side_effect=sample), patch.object(p6, 'quiet_lock'):
            self.assertEqual(p6.campaign(args), 0)
        self.assertTrue(p6.read_json(args.out / 'go-aa.json')['passed'])
        report = p6.read_json(args.out / 'report.json')
        self.assertTrue(report['comparison_contracts_passed'])
        self.assertFalse(any('ns' in key or 'ms' in key for key in p6.COMPARABLE_FIELDS))

    def test_budget_stops_before_control_that_does_not_fit(self):
        args = self.args()
        args.window_budget_s = 149
        with patch.object(p6, 'run_sample') as sample, patch.object(p6, 'quiet_lock'):
            self.assertEqual(p6.campaign(args), 1)
        sample.assert_not_called()
        report = p6.read_json(args.out / 'report.json')
        self.assertFalse(report['passed'])
        self.assertFalse(report['complete'])
        self.assertEqual(report['window_stop']['required_s'], 150)

    def test_budget_does_not_truncate_a_30_minute_soak(self):
        args = self.args()
        args.tier, args.duration_s, args.workload = 'soak', 1800, ['steady']
        with patch.object(p6, 'run_sample', side_effect=self.sample) as sample, patch.object(p6, 'quiet_lock'):
            self.assertEqual(p6.campaign(args), 1)
        self.assertEqual(sample.call_count, 0)  # Whole partition refused before lock or child.
        self.assertTrue(all(call.args[1]['name'] == 'one-row' for call in sample.call_args_list))
        report = p6.read_json(args.out / 'report.json')
        self.assertFalse(report['complete'])
        self.assertFalse(report['passed'])
        self.assertIn('whole duration partition', report['failure'])
        self.assertGreater(report['window_stop']['required_s'], 1800)

    def test_budget_exhaustion_keeps_absolute_samples_and_suppresses_all_ratios(self):
        args = self.args()
        fits = dict(fits=True, remaining_s=900, required_s=150, cleanup_reserve_s=30)
        stops = dict(fits=False, remaining_s=20, required_s=150, cleanup_reserve_s=30)
        with patch.object(p6, 'run_sample', side_effect=self.sample), patch.object(p6, 'quiet_lock'), \
             patch.object(p6.WindowBudget, 'admission', side_effect=[fits] * 6 + [stops]):
            self.assertEqual(p6.campaign(args), 1)
        report = p6.read_json(args.out / 'report.json')
        self.assertFalse(report['passed'])
        self.assertFalse(report['complete'])
        self.assertEqual(report['summaries']['one-row']['go']['wall_ms']['n'], 1)
        self.assertEqual(report['summaries']['one-row']['candidate']['wall_ms']['n'], 1)
        self.assertTrue(all(x is None for x in report['summaries']['one-row']['candidate_over_go'].values()))

    def test_failed_warmup_invalidates_campaign_and_all_ratios(self):
        args = self.args()
        calls = 0
        def sample(*a, **kwargs):
            nonlocal calls
            calls += 1
            result = self.sample()
            if calls == 3:
                result.update(valid=False, errors=['injected warmup failure'])
            return result
        with patch.object(p6, 'run_sample', side_effect=sample), patch.object(p6, 'quiet_lock'):
            self.assertEqual(p6.campaign(args), 1)
        report = p6.read_json(args.out / 'report.json')
        self.assertFalse(report['passed'])
        self.assertTrue(all(x is None for x in report['summaries']['one-row']['candidate_over_go'].values()))

    def test_go_aa_failure_prevents_candidate_campaign(self):
        args = self.args()
        with patch.object(p6, 'run_sample', return_value=dict(valid=False, errors=['injected failure'])) as sample, patch.object(p6, 'quiet_lock'):
            self.assertEqual(p6.campaign(args), 1)
        self.assertEqual(sample.call_count, 2)
        self.assertFalse((args.out / 'samples.jsonl').exists())

    def test_hash_framing_campaign_is_a_separate_observer_control_report(self):
        args = self.args()
        args.collector_mode = 'hash-framing'
        with patch.object(p6, 'run_sample', side_effect=self.sample) as sample, patch.object(p6, 'quiet_lock'):
            self.assertEqual(p6.campaign(args), 0)
        report = p6.read_json(args.out / 'report.json')
        self.assertEqual(report['kind'], 'observer_control')
        self.assertEqual(report['collector_mode'], 'hash-framing')
        self.assertTrue(all(call.kwargs['collector_mode'] == 'hash-framing' for call in sample.call_args_list))

    def test_campaign_rejects_an_alternate_lock(self):
        args = self.args()
        args.lock = self.root / 'not-shared.lock'
        with self.assertRaisesRegex(ValueError, 'shared'):
            p6.campaign(args)

    def test_budget_binding_rejected_before_lock_and_reported(self):
        args = self.args()
        manifest = p6.read_json(args.manifest)
        manifest['budgets_sha256'] = 'stale'
        p6.write_json(args.manifest, manifest)
        with patch.object(p6, 'quiet_lock') as lock, patch.object(p6, 'run_sample') as sample:
            self.assertEqual(p6.campaign(args), 1)
        lock.assert_not_called()
        sample.assert_not_called()
        self.assertIn('predeclared budget', p6.read_json(args.out / 'report.json')['failure'])

    def test_source_snapshot_mismatch_is_rejected(self):
        args = self.args()
        (self.root / 'main.go').chmod(0o644)
        (self.root / 'main.go').write_text('different')
        with patch.object(p6, 'quiet_lock') as lock:
            self.assertEqual(p6.campaign(args), 1)
        lock.assert_not_called()
        self.assertIn('snapshot differs', p6.read_json(args.out / 'report.json')['failure'])

    def test_aa_typeerror_always_writes_atomic_incomplete_report(self):
        args = self.args()
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=TypeError('bad summary')):
            self.assertEqual(p6.campaign(args), 1)
        report = p6.read_json(args.out / 'report.json')
        self.assertFalse(report['complete'])
        self.assertFalse(report['passed'])
        self.assertEqual(report['summaries'], {})
        self.assertIn('TypeError', p6.read_json(args.out / 'go-aa.json')['samples'][0]['errors'][0])
        self.assertFalse(list(args.out.glob('*.partial-*')))

    def test_measured_exception_keeps_invalid_record_and_quarantines(self):
        args = self.args()
        count = 0
        def sample(*a, **kw):
            nonlocal count
            count += 1
            if count == 6:
                raise TypeError('bad measured summary')
            return self.sample()
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=sample):
            self.assertEqual(p6.campaign(args), 1)
        report = p6.read_json(args.out / 'report.json')
        self.assertFalse(report['complete'])
        self.assertTrue(all(v is None for v in report['summaries']['one-row']['candidate_over_go'].values()))
        self.assertTrue(list(p6.jsonl(args.out / 'samples.jsonl'))[-1]['collector_exception'])

    def test_end_binary_hash_rechecked(self):
        args = self.args()
        calls = 0
        def sample(*a, **kw):
            nonlocal calls
            calls += 1
            if calls == 8:
                args.fixture.chmod(0o755)
                args.fixture.write_bytes(b'changed')
            return self.sample()
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=sample):
            self.assertEqual(p6.campaign(args), 1)
        report = p6.read_json(args.out / 'report.json')
        self.assertFalse(report['end_identity_check']['passed'])
        self.assertIn(str(args.fixture), report['end_identity_check']['changed'])

    def test_end_budget_and_collector_hash_rechecked_without_mutation(self):
        args = self.args()
        calls = 0
        real_info = p6.file_info
        def info(path):
            value = real_info(path)
            if calls >= 8 and Path(path).resolve() in (p6.PREDECLARED_BUDGETS.resolve(), Path(p6.__file__).resolve()):
                value['sha256'] = 'injected-end-hash'
            return value
        def sample(*a, **kw):
            nonlocal calls
            calls += 1
            return self.sample()
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'file_info', side_effect=info), \
             patch.object(p6, 'run_sample', side_effect=sample):
            self.assertEqual(p6.campaign(args), 1)
        report = p6.read_json(args.out / 'report.json')
        self.assertIn(str(p6.PREDECLARED_BUDGETS.resolve()), report['end_identity_check']['changed'])
        self.assertIn(str(Path(p6.__file__).resolve()), report['end_identity_check']['changed'])
        self.assertTrue(all(v is None for v in report['summaries']['one-row']['candidate_over_go'].values()))

    def test_fixture_and_meter_symlink_repoints_never_change_invoked_bound_paths(self):
        args = self.args()
        originals = {role: p6.file_info(getattr(args, role)) for role in ('fixture', 'meter')}
        links = {}
        replacements = {}
        for role in originals:
            links[role] = self.root / (role + '-link')
            links[role].symlink_to(originals[role]['path'])
            replacements[role] = self.root / (role + '-replacement')
            replacements[role].write_bytes(b'different cache-only ' + role.encode())
            replacements[role].chmod(0o555)
            setattr(args, role, links[role])
        def repoint(*a):
            for role in originals:
                links[role].unlink()
                links[role].symlink_to(replacements[role])
        seen = []
        def sample(*a, **kw):
            seen.append({role: p6.file_info(a[index]) for role, index in (('fixture', 2), ('meter', 3))})
            return self.sample()
        with patch.object(p6, 'quiet_lock') as lock, patch.object(p6, 'run_sample', side_effect=sample):
            lock.return_value.__enter__.side_effect = repoint
            self.assertEqual(p6.campaign(args), 0)
        self.assertEqual(len(seen), 8)
        for call in seen:
            for role in originals:
                self.assertEqual(call[role], originals[role])
                self.assertNotEqual(p6.file_info(links[role])['sha256'], originals[role]['sha256'])
        report = p6.read_json(args.out / 'report.json')
        self.assertTrue(report['end_identity_check']['passed'])
        self.assertTrue(report['passed'])
        self.assertEqual(report['fixture']['sha256'], originals['fixture']['sha256'])
        self.assertEqual(report['meter']['sha256'], originals['meter']['sha256'])

    def test_parent_atomic_replacement_after_read_does_not_rebase_initial_identity(self):
        args = self.args()
        original_bytes = args.manifest.read_bytes()
        initial = p6.file_info(args.manifest)
        loads = json.loads
        mutated = False
        def parse_then_replace(data, *a, **kw):
            nonlocal mutated
            value = loads(data, *a, **kw)
            if data == original_bytes and not mutated:
                mutated = True
                replacement = json.loads(json.dumps(value))
                replacement['sources']['oracle'] = 'changed-after-read'
                p6.write_json(args.manifest, replacement)
            return value
        with patch.object(p6.json, 'loads', side_effect=parse_then_replace), \
             patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=self.sample):
            self.assertEqual(p6.campaign(args), 1)
        report = p6.read_json(args.out / 'report.json')
        self.assertTrue(mutated)
        self.assertEqual(report['manifest']['sources']['oracle'], 'pinned')
        self.assertEqual(report['parent_manifest'], initial)
        self.assertEqual(report['manifest'], loads(original_bytes))
        self.assertNotEqual(report['parent_manifest']['sha256'], p6.file_info(args.manifest)['sha256'])
        self.assertEqual(report['end_identity_check']['changed'], [initial['path']])
        self.assertFalse(report['passed'])
        self.assertFalse(report['complete'])
        self.assertTrue(all(v is None for v in report['summaries']['one-row']['candidate_over_go'].values()))

    def test_parent_identity_uses_exact_read_bytes_and_resolved_path(self):
        parent = self.root / 'raw-parent.json'
        raw = b'{ \n  "sources": {"oracle": "pinned"}\n}\n'
        parent.write_bytes(raw)
        link = self.root / 'parent-link'
        link.symlink_to(parent)
        manifest, identity = p6.read_json_with_identity(link)
        self.assertEqual(manifest, json.loads(raw))
        self.assertEqual(identity, p6.file_info(parent))
        self.assertEqual(identity['size_bytes'], len(raw))
        with self.assertRaisesRegex(ValueError, 'bounded reader limit'):
            p6.read_json_with_identity(link, limit=len(raw) - 1)

    def test_each_selected_role_mismatch_rejected_before_lock(self):
        args = self.args()
        original = p6.read_json(args.manifest)
        for role in ('go', 'candidate', 'meter'):
            bad = self.root / ('wrong-' + role)
            bad.write_bytes(b'explicit different executable')
            bad.chmod(0o755)
            args.out = self.root / ('campaign-wrong-' + role)
            setattr(args, role, bad)
            with patch.object(p6, 'quiet_lock') as lock, patch.object(p6, 'run_sample') as sample:
                self.assertEqual(p6.campaign(args), 1)
            lock.assert_not_called()
            sample.assert_not_called()
            self.assertIn('declared measured role', p6.read_json(args.out / 'report.json')['failure'])
            setattr(args, role, Path('/bin/true'))
        p6.write_json(args.manifest, original)

    def test_normal_candidate_flags_features_kind_and_build_chain_required(self):
        args = self.args()
        original = p6.read_json(args.manifest)
        variants = [('build_kind', 'debug'), ('build_kind', 'symbolized_release'),
            ('features', ['diagnostics']), ('cloud_synthetic_token_enabled', True),
            ('argv', ['cargo', 'build', '--profile', 'profiling']), ('argv', ['cargo', 'build', '--release', '--all-features'])]
        for index, (field, value) in enumerate(variants):
            manifest = json.loads(json.dumps(original))
            manifest['build_flags']['candidate']['pb'][field] = value
            p6.write_json(args.manifest, manifest)
            args.out = self.root / ('build-kind-' + str(index))
            with patch.object(p6, 'quiet_lock') as lock, patch.object(p6, 'run_sample') as sample:
                self.assertEqual(p6.campaign(args), 1)
            lock.assert_not_called()
            sample.assert_not_called()
            self.assertIn('normal_release', p6.read_json(args.out / 'report.json')['failure'])
        manifest = json.loads(json.dumps(original))
        manifest['build_flags']['candidate']['pb']['public_build_version'] = 'different'
        p6.write_json(args.manifest, manifest)
        args.out = self.root / 'build-chain-differs'
        with patch.object(p6, 'quiet_lock') as lock:
            self.assertEqual(p6.campaign(args), 1)
        lock.assert_not_called()
        self.assertIn('builds.pb differs', p6.read_json(args.out / 'report.json')['failure'])

    def test_content_identical_copies_bound_and_both_paths_tracked(self):
        args = self.args()
        for role in ('go', 'candidate', 'meter'):
            copy = self.root / ('copy-' + role)
            copy.write_bytes(Path('/bin/true').read_bytes())
            copy.chmod(0o755)
            setattr(args, role, copy)
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=self.sample):
            self.assertEqual(p6.campaign(args), 0)
        report = p6.read_json(args.out / 'report.json')
        tracked = {v['path'] for v in report['bound_end_identities']}
        self.assertEqual(len(tracked), len(report['bound_end_identities']))
        self.assertIn(str(Path('/bin/true').resolve()), tracked)
        for role in ('go', 'candidate', 'meter'):
            self.assertIn(str(getattr(args, role).resolve()), tracked)
            self.assertTrue(report['measured_roles'][role]['content_identical_copy'])

    def test_timing_aa_does_not_select_candidate_and_binds_both_to_go(self):
        args = self.args()
        args.timing_aa, args.warmups, args.repetitions = True, 5, 30
        args.candidate = self.root / 'absent-candidate'
        manifest = p6.read_json(args.manifest)
        del manifest['candidate_manifest']
        manifest['build_flags']['candidate']['pb'] = {'build_kind': 'debug'}
        p6.write_json(args.manifest, manifest)
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=self.sample):
            self.assertEqual(p6.campaign(args), 0)
        report = p6.read_json(args.out / 'report.json')
        self.assertEqual(report['measured_roles']['go-a']['role'], 'go')
        self.assertEqual(report['measured_roles']['go-b']['role'], 'go')
        args.out = self.root / 'timing-aa-bad-go'
        args.go = self.root / 'bad-go'
        args.go.write_bytes(b'wrong Go')
        args.go.chmod(0o755)
        with patch.object(p6, 'quiet_lock') as lock:
            self.assertEqual(p6.campaign(args), 1)
        lock.assert_not_called()

        args.go = Path('/bin/true')
        args.meter = self.root / 'bad-meter'
        args.meter.write_bytes(b'wrong meter')
        args.meter.chmod(0o755)
        args.out = self.root / 'timing-aa-bad-meter'
        with patch.object(p6, 'quiet_lock') as lock:
            self.assertEqual(p6.campaign(args), 1)
        lock.assert_not_called()

    def test_all_bound_runner_toolchain_build_manifest_changes_quarantine(self):
        args = self.args()
        extra = self.root / 'extra-runner.py'
        extra.write_text('before')
        manifest = p6.read_json(args.manifest)
        manifest['sources']['runners'] = {'extra': p6.file_info(extra)}
        p6.write_json(args.manifest, manifest)
        calls = 0
        paths = [extra, self.root / 'go-toolchain', self.root / 'candidate-build.json']
        def sample(*a, **kw):
            nonlocal calls
            calls += 1
            if calls == 8:
                for path in paths:
                    path.write_bytes(b'after-bound-identity-change')
            return self.sample()
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=sample):
            self.assertEqual(p6.campaign(args), 1)
        report = p6.read_json(args.out / 'report.json')
        self.assertFalse(report['end_identity_check']['passed'])
        for path in paths:
            self.assertIn(str(path.resolve()), report['end_identity_check']['changed'])
        self.assertTrue(all(v is None for v in report['summaries']['one-row']['candidate_over_go'].values()))

    def test_one_extra_bound_runner_change_alone_invalidates(self):
        args = self.args()
        extra = self.root / 'extra-alone.py'
        extra.write_text('before')
        manifest = p6.read_json(args.manifest)
        manifest['sources']['runners'] = {'extra': p6.file_info(extra)}
        p6.write_json(args.manifest, manifest)
        calls = 0
        def sample(*a, **kw):
            nonlocal calls
            calls += 1
            if calls == 8:
                extra.write_text('after')
            return self.sample()
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=sample):
            self.assertEqual(p6.campaign(args), 1)
        report = p6.read_json(args.out / 'report.json')
        self.assertEqual(report['end_identity_check']['changed'], [str(extra.resolve())])
        self.assertTrue(all(v is None for v in report['summaries']['one-row']['candidate_over_go'].values()))

    def test_post_capture_typeerror_retains_all_completed_observations(self):
        args = self.args()
        original_sample = p6.run_sample
        observed = dict(errors=['retained capture issue'], stdout_bytes=13, stdout_sha256='whole-hash', rows=1, batches=1,
            first_record_ns=200, wall_ms=12, cpu_ms=3, peak_rss_bytes=4096, first_complete_record_ms=2,
            cpu_user_ms=2, cpu_system_ms=1, termination={'exit': 1})
        class Fixture:
            def __init__(self, *a):
                self.url = 'http://127.0.0.1:1'
                self.go_env = {}
                self.n = 0
            def summary(self):
                self.n += 1
                return {} if self.n == 1 else {'streams': None}
            def close(self):
                pass
        calls = 0
        def sample(*a, **kw):
            nonlocal calls
            calls += 1
            return self.sample() if calls < 6 else original_sample(*a, **kw)
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'Fixture', Fixture), \
             patch.object(p6, 'run_sample', side_effect=sample), patch.object(p6, 'collect', return_value=observed), \
             patch.object(p6, 'host_snapshot', return_value={}):
            self.assertEqual(p6.campaign(args), 1)
        failed = list(p6.jsonl(args.out / 'samples.jsonl'))[-1]
        self.assertFalse(failed['valid'])
        self.assertFalse(failed['contract_validated'])
        self.assertTrue(failed['capture_complete'])
        for key, value in observed.items():
            if key != 'errors':
                self.assertEqual(failed[key], value)
        self.assertEqual(failed['errors'][0], 'retained capture issue')
        self.assertIn('TypeError', failed['errors'][-1])
        capture = p6.read_json(next(args.out.rglob('capture.json')))
        self.assertEqual(capture['stdout_sha256'], 'whole-hash')
        self.assertFalse(capture['valid'])
        self.assertNotIn('contract', capture)
        self.assertFalse(p6.read_json(args.out / 'report.json')['complete'])

    def test_exception_wrapper_merges_saved_capture_instead_of_overwriting(self):
        args = self.args()
        calls = 0
        def sample(*a, **kw):
            nonlocal calls
            calls += 1
            if calls == 6:
                folder = a[4]
                folder.mkdir(parents=True)
                p6.write_json(folder / 'capture.json', dict(errors=['prior'], stdout_sha256='preserved', rows=7, cpu_ms=4))
                raise TypeError('after saved capture')
            return self.sample()
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=sample):
            self.assertEqual(p6.campaign(args), 1)
        failed = list(p6.jsonl(args.out / 'samples.jsonl'))[-1]
        self.assertEqual(failed['stdout_sha256'], 'preserved')
        self.assertEqual(failed['rows'], 7)
        self.assertEqual(failed['cpu_ms'], 4)
        self.assertEqual(failed['errors'], ['prior', 'TypeError: after saved capture'])
        self.assertFalse(failed['valid'])

    def test_tls_condition_required_and_mixed_reports_rejected(self):
        args = self.args()
        args.tier, args.workload, args.warmups, args.repetitions = 'functional', ['steady'], 0, 1
        args.window_budget_s = 2000
        reports = []
        def sample(*a, **kw):
            value = self.sample()
            value['contract']['tls'] = a[5]
            return value
        for index, tls in enumerate((False, True)):
            args.out = self.root / ('tls-' + str(index))
            args.tls, args.session_index = tls, index
            with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=sample):
                self.assertEqual(p6.campaign(args), 0)
            reports.append(args.out / 'report.json')
            self.assertIs(p6.read_json(reports[-1])['tls'], tls)
        with self.assertRaisesRegex(ValueError, 'identities differ'):
            p6.aggregate_reports(reports, self.root / 'mixed.json')
        second = p6.read_json(reports[1])
        second['tls'] = False
        p6.write_json(reports[1], second)
        self.assertEqual(p6.aggregate_reports(reports, self.root / 'same-tls.json'), 0)
        del second['tls']
        p6.write_json(reports[1], second)
        with self.assertRaisesRegex(ValueError, 'explicit boolean TLS'):
            p6.aggregate_reports(reports, self.root / 'legacy.json')
        second['tls'] = 0
        p6.write_json(reports[1], second)
        with self.assertRaisesRegex(ValueError, 'explicit boolean TLS'):
            p6.aggregate_reports(reports, self.root / 'invalid-tls.json')

    def test_token_only_stored_as_hash(self):
        args = self.args()
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=self.sample):
            p6.campaign(args)
        self.assertNotIn(args.quiet_window_token, (args.out / 'report.json').read_text())
        self.assertIn('quiet_window_token_sha256', p6.read_json(args.out / 'report.json'))

    def test_small_finite_samples_have_no_bootstrap(self):
        args = self.args()
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=self.sample), \
             patch.object(p6.run, 'compare_samples') as compare:
            p6.campaign(args)
        compare.assert_not_called()
        point = p6.read_json(args.out / 'report.json')['summaries']['one-row']['candidate_over_go']['wall_ms']
        self.assertEqual(point['interval_status'], 'not_estimable')
        self.assertEqual(point['reference_sessions'], [1, 1])

    def test_duration_no_ci_and_session_aggregate_identity_gates(self):
        args = self.args()
        args.tier, args.workload, args.warmups, args.repetitions = 'functional', ['steady'], 0, 1
        args.window_budget_s = 2000
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=self.sample), \
             patch.object(p6.run, 'compare_samples') as compare:
            p6.campaign(args)
        compare.assert_not_called()
        report = p6.read_json(args.out / 'report.json')
        self.assertIsNone(report['summaries']['steady']['candidate_over_go']['wall_ms'])
        other = self.root / 'second.json'
        report['session_index'] = 1
        p6.write_json(other, report)
        dest = self.root / 'aggregate.json'
        self.assertEqual(p6.aggregate_reports([args.out / 'report.json', other], dest), 0)
        value = p6.read_json(dest)
        self.assertIsNone(value['confidence_intervals'])
        self.assertEqual(value['summaries']['steady']['go']['wall_ms']['session_values'], [1, 1])
        for field in ('collector', 'fixture', 'meter', 'process_harness', 'budget_file'):
            original = report[field]['sha256']
            report[field]['sha256'] = 'different'
            p6.write_json(other, report)
            with self.assertRaisesRegex(ValueError, 'identities differ'):
                p6.aggregate_reports([args.out / 'report.json', other], dest)
            report[field]['sha256'] = original
        report['session_index'] = 0
        p6.write_json(other, report)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            p6.aggregate_reports([args.out / 'report.json', other], dest)

    def test_timing_aa_is_two_same_go_labels_and_separate_kind(self):
        args = self.args()
        args.timing_aa, args.warmups, args.repetitions = True, 5, 30
        args.window_budget_s = 20000
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=self.sample):
            self.assertEqual(p6.campaign(args), 0)
        report = p6.read_json(args.out / 'report.json')
        self.assertEqual(report['kind'], 'timing_aa')
        self.assertEqual(report['binaries']['go-a'], report['binaries']['go-b'])
        self.assertEqual(report['recorded_measured_samples'], 60)

    def test_unavailable_cpu_admission_preserves_absolutes_without_ratios(self):
        args = self.args()
        def sample(*a, **kw):
            value = self.sample()
            value['cpu_screen'] = dict(comparative_admissible=False, interference_status='ambiguous')
            return value
        with patch.object(p6, 'quiet_lock'), patch.object(p6, 'run_sample', side_effect=sample):
            self.assertEqual(p6.campaign(args), 0)
        row = p6.read_json(args.out / 'report.json')['summaries']['one-row']
        self.assertEqual(row['candidate']['wall_ms']['median'], 1)
        self.assertTrue(all(v is None for v in row['candidate_over_go'].values()))


class ReviewAdmission(Root, unittest.TestCase):
    def test_current_examples_keep_replication_seed_and_frozen_fixture(self):
        document = (Path(p6.__file__).resolve().parent.parent / 'docs/phase6-measurement-plan.md').read_text()
        self.assertNotIn('windows (new seeds,', document)
        self.assertNotIn("case, Path('bin/pb-fixture').resolve()", document)
        marker = document.index('HISTORICAL ONLY — do not execute')
        legacy = document.index("sample = p6.run_sample(Path('target/debug/pb')")
        self.assertLess(marker, legacy)
        self.assertIn('the same frozen scenario seed', document)

    def live(self, name='steady'):
        w = next(w for w in p6.workloads(1, 'functional') if w['name'] == name)
        count = 50
        stream = dict(batches=count, live=dict(ops=1, scheduled_batches=count, emitted_batches=count,
            omitted_by_deadline=0, omitted_by_termination=0, stopped_by_count=0, stopped_by_deadline=1,
            stopped_by_termination=0, lateness_ns=dict(count=count, sum=1, max=1),
            send_call_ns=dict(count=count, sum=1, max=1), last=dict(deadline_ns=100)))
        return w, stream

    def test_complete_schedule_and_lateness_send_evidence(self):
        w, stream = self.live()
        result = p6.schedule_admission(w, stream)
        self.assertFalse(result['errors'])
        self.assertTrue(result['schedule_complete'])
        self.assertIn('not pure blocking', result['send_call_semantics'])

    def test_deadline_omission_not_accepted_as_full_workload(self):
        w, stream = self.live()
        stream['live'].update(emitted_batches=49, omitted_by_deadline=1)
        stream['batches'] = 49
        result = p6.schedule_admission(w, stream)
        self.assertTrue(result['errors'])
        self.assertFalse(result['throughput_admissible'])

    def test_sink_shortfall_absolute_only(self):
        w, stream = self.live('slow-sink')
        stream['live'].update(emitted_batches=1, omitted_by_deadline=49)
        stream['batches'] = 1
        result = p6.schedule_admission(w, stream)
        self.assertFalse(result['schedule_complete'])
        self.assertFalse(result['throughput_admissible'])
        self.assertEqual(p6.metric_scope(w)['wall_ms'], 'sink_gated')

    def test_interval_zero_duration_null_schedule_rejected(self):
        w, stream = self.live()
        w['stream']['subscriptions'][0]['ops'][0]['interval_ms'] = 0
        w['stream']['subscriptions'][0]['ops'][0]['batches'] = 1
        self.assertTrue(p6.schedule_admission(w, stream)['errors'])

    def pressure(self):
        w = next(w for w in p6.workloads() if w['name'] == 'dictionary-large-lz4')
        stream = dict(batches=9, dictionary=dict(messages=9, initial=1, deltas=6, replacements=2, resets=2, max_size=64),
            buckets=dict(overflow=0, counts=[dict(batches=9, dictionary_messages=9, dictionary_deltas=6, dictionary_replacements=2)]))
        return w, stream

    def test_pressure_definition_and_exact_cycle(self):
        w, stream = self.pressure()
        self.assertTrue(w['resource_pressure'])
        self.assertEqual(w['stream']['dictionary']['value_bytes'], 1048576)
        self.assertFalse(p6.schedule_admission(w, stream)['errors'])
        self.assertEqual(len(p6.workloads()), 12)

    def test_missing_churn_or_bucket_coverage_rejected(self):
        for field in ('resets', 'deltas', 'max_size'):
            w, stream = self.pressure()
            stream['dictionary'][field] -= 1
            self.assertTrue(p6.schedule_admission(w, stream)['errors'])
        w, stream = self.pressure()
        stream['buckets']['overflow'] = 1
        self.assertTrue(p6.schedule_admission(w, stream)['errors'])

    def test_per_workload_seed_and_session_ab_ba(self):
        a = list(p6.schedule(['go', 'candidate'], 0, 1, 2026, 'steady', 'soak', 0))
        b = list(p6.schedule(['go', 'candidate'], 0, 1, 2026, 'steady', 'soak', 1))
        self.assertEqual([v['label'] for v in a], ['go', 'candidate'])
        self.assertEqual([v['label'] for v in b], ['candidate', 'go'])
        self.assertEqual(a[0]['implementation_order'], 'AB')
        self.assertEqual(b[0]['implementation_order'], 'BA')
        self.assertNotEqual(p6.workload_seed(2026, 'steady'), p6.workload_seed(2026, 'idle'))

    def cpu(self, duration=1000, ticks=4, mismatch=0):
        start, end = 1000000000, 1000000000 + int(duration * 1e6)
        result = dict(wall_ms=duration, cpu_ms=20, started_monotonic_ns=start, ended_monotonic_ns=end,
            observer_cpu=dict(user_ms=10, system_ms=0, start_ns=start, end_ns=end))
        before = dict(fixture_cpu_user_ns=0, fixture_cpu_system_ns=0, expected_output_ns_total=5,
                      collector_request_bounds=dict(start_ns=start, end_ns=start))
        after = dict(fixture_cpu_user_ns=10000000, fixture_cpu_system_ns=0, expected_output_ns_total=10000005,
                     collector_request_bounds=dict(start_ns=start, end_ns=end + mismatch))
        counters = dict(user=0, nice=0, system=0, idle=0, iowait=0, irq=0, softirq=0, steal=0)
        host = dict(status='observed', start_ns=start, end_ns=start, counters=counters, cpu_count=1, ticks_per_second=100, loadavg_raw='raw')
        final = dict(host, start_ns=end, end_ns=end, counters=dict(counters, user=ticks))
        return p6.cpu_screen(result, before, after, host, final, p6.read_json(p6.PREDECLARED_BUDGETS)['campaign_controls'])

    def test_cpu_interval_screen_records_wall_encoding_not_cpu_subtraction(self):
        screen = self.cpu()
        self.assertTrue(screen['comparative_admissible'])
        self.assertEqual(screen['client_cpu_ms'], 20)
        self.assertEqual(screen['fixture_cpu_ms'], 10)
        self.assertEqual(screen['fixture']['expected_output_encoding_wall_ms'], 10)
        self.assertEqual(screen['residual_raw_ms'], 0)

    def test_short_cpu_screen_is_resolution_limited(self):
        screen = self.cpu(duration=20)
        self.assertEqual(screen['interference_status'], 'resolution_limited')
        self.assertFalse(screen['comparative_admissible'])

    def test_misaligned_cpu_intervals_ambiguous_not_fake_clean(self):
        screen = self.cpu(ticks=45, mismatch=1000000000)
        self.assertEqual(screen['interference_status'], 'ambiguous')
        self.assertFalse(screen['comparative_admissible'])

    def test_busy_cpu_screen_flag_and_negative_raw_residual_retained(self):
        self.assertEqual(self.cpu(ticks=200)['interference_status'], 'flagged')
        screen = self.cpu(ticks=0)
        self.assertEqual(screen['residual_raw_ms'], -40)
        self.assertEqual(screen['unattributed_busy_cores_estimate'], 0)

    def test_ceiling_and_missing_cpu_not_admitted(self):
        screen = self.cpu(duration=100)
        self.assertEqual(screen['potential_limit_status'], 'below_screen')
        missing = p6.cpu_screen(dict(wall_ms=1000), {}, {}, {}, {}, p6.read_json(p6.PREDECLARED_BUDGETS)['campaign_controls'])
        self.assertFalse(missing['comparative_admissible'])
        self.assertEqual(missing['interference_status'], 'unavailable')

    def test_visibility_correlation_reports_time_coverage_and_missing(self):
        p6.write_json(self.root / 'unused.json', {})
        with (self.root / 'fixture.events.jsonl').open('w') as f:
            f.write(json.dumps(dict(kind='batch_emit_start', batch=1, rows=1, t_ns=100)) + '\n')
        with (self.root / 'visibility.jsonl').open('w') as f:
            for batch, ns in ((1, 1000), (2, 60000000100)):
                f.write(json.dumps(dict(kind='batch_visible', batch=batch, t_ns=ns)) + '\n')
        result = dict(errors=[], batches=2, first_record_ns=1000, started_monotonic_ns=1)
        p6.correlate(self.root, result, dict(first_nonempty_batch_emit_ns=100, buckets=dict(origin_ns=100)))
        self.assertEqual(result['batch_visibility']['matched'], 1)
        self.assertEqual(result['batch_visibility']['uncorrelated'], 1)
        self.assertEqual(result['batch_visibility']['coverage_by_time'], [dict(index=0, visible=1, correlated=1), dict(index=1, visible=1, correlated=0)])
        self.assertEqual(p6.VISIBILITY_LOG_BYTES, 16 << 20)

    def test_observer_and_fixture_ceiling_screen(self):
        controls = p6.read_json(p6.PREDECLARED_BUDGETS)['campaign_controls']
        result = dict(wall_ms=1000, observer_cpu=dict(user_ms=800, system_ms=0))
        before = dict(fixture_cpu_user_ns=0, fixture_cpu_system_ns=0)
        after = dict(fixture_cpu_user_ns=0, fixture_cpu_system_ns=0)
        screen = p6.cpu_screen(result, before, after, {}, {}, controls)
        self.assertEqual(screen['potential_limit_status'], 'fixture_or_observer_bound')
        self.assertFalse(screen['throughput_admissible'])
        result['observer_cpu']['user_ms'] = 0
        after['fixture_cpu_user_ns'] = 800000000
        screen = p6.cpu_screen(result, before, after, {}, {}, controls)
        self.assertFalse(screen['throughput_admissible'])

    def test_first_record_after_exit_count_and_visibility_capacity(self):
        self.assertEqual(p6.summarize([dict(valid=True, first_record_observed_after_native_exit=True),
            dict(valid=True, first_record_observed_after_native_exit=False),
            dict(valid=False, first_record_observed_after_native_exit=True)])['first_record_observed_after_native_exit'], 1)
        maximum_event = json.dumps(dict(kind='batch_visible', batch=180000,
            t_ns=99999999999999999, rows=23040000), separators=(',', ':')) + '\n'
        self.assertLess(len(maximum_event.encode()) * 180000 + 1024, p6.VISIBILITY_LOG_BYTES)

    def test_duration_ci_always_suppressed_even_many_sessions(self):
        samples = [dict(valid=True, wall_ms=1)] * 30
        with patch.object(p6.run, 'compare_samples') as compare:
            value = p6.compare_descriptive(samples, samples, 'wall_ms', 'soak', 20, 1)
        compare.assert_not_called()
        self.assertEqual(value['interval_status'], 'not_estimable')


@unittest.skipUnless((p6.run.DEFAULT_METER).exists() and sys.platform == 'linux', 'prebuilt native Linux meter required')
class NativeMeter(Root, unittest.TestCase):
    def test_small_fake_cli_real_wait4_and_complete_record(self):
        env = dict(os.environ, TMPDIR=str(p6.TMP), GOTMPDIR=str(p6.TMP), CLAUDE_CODE_TMPDIR=str(p6.TMP))
        result = p6.collect([sys.executable, '-c', 'import os; os.write(1,b\'{"sequence":0}\\n\\n\'); raise SystemExit(1)'],
                            env, self.root, p6.run.DEFAULT_METER, 5, sample_ms=1, trend_warmup_s=0)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['termination'], {'exit': 1})
        self.assertEqual(result['resource_source'], 'native_child_wait4')
        self.assertEqual((result['rows'], result['batches']), (1, 1))
        self.assertIsNotNone(result['first_complete_record_ms'])
        self.assertGreater(result['peak_rss_bytes'], 0)

    def test_signal_death_is_not_numeric_exit(self):
        env = dict(os.environ, TMPDIR=str(p6.TMP), GOTMPDIR=str(p6.TMP), CLAUDE_CODE_TMPDIR=str(p6.TMP))
        result = p6.collect([sys.executable, '-c', 'import os,signal; os.kill(os.getpid(),signal.SIGTERM)'],
                            env, self.root, p6.run.DEFAULT_METER, 5)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['termination'], {'signal': signal.SIGTERM})
        self.assertIsNone(result['first_complete_record_ms'])

    def fake_protocol(self):
        fixture = self.root / 'fake-fixture'
        fixture.write_text('''#!/usr/bin/env python3
import hashlib,json,sys,time
from http.server import BaseHTTPRequestHandler,HTTPServer
plan=json.load(open(sys.argv[2]))['phase6'];events=open(plan['events_path'],'w')
data=b'{"sequence":0}\\n\\n'
about_ns=doget_ns=emit_ns=0
def event(kind,**kw):
 stamp=time.clock_gettime_ns(time.CLOCK_MONOTONIC)
 events.write(json.dumps(dict(kind=kind,t_ns=stamp,**kw))+'\\n');events.flush();return stamp
class H(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  global about_ns,doget_ns,emit_ns
  if self.path=='/api/v1/about':
   about_ns=event('http',path=self.path);out=b'{}'
  elif self.path=='/emit':
   doget_ns=event('doget',ticket='{"stream":"logs"}',ticket_matches=True,authorized=True,unexpected=False,metadata={'authorization':['Basic aGFybmVzczpoYXJuZXNz']})
   emit_ns=event('batch_emit_start',batch=0,rows=1);out=data
  else:
   validation=dict(auth_checks=1,invalid_auth=0,ticket_checks=1,ticket_mismatches=0,unexpected_requests=0,doget_before_about=0)
   out=json.dumps(dict(streams={'logs':dict(expected_stdout_sha256=hashlib.sha256(data).hexdigest(),expected_stdout_bytes=len(data),expected_rows=1,expected_rendered_rows=1,batches=1,dogets=1,first_doget_ns=doget_ns,last_doget_ns=doget_ns,first_nonempty_batch_emit_ns=emit_ns,request_validation=validation,last_terminal={'code':'InvalidArgument'},encoded_bytes=None,decoded_bytes=None)},events_dropped=0,dogets=1,about_requests=1,http_requests=1,first_about_ns=about_ns,last_about_ns=about_ns,about_completed_ns=about_ns)).encode()
  self.send_response(200);self.end_headers();self.wfile.write(out)
server=HTTPServer(('127.0.0.1',0),H);url='http://127.0.0.1:'+str(server.server_port)
print(json.dumps(dict(url=url,grpc_port=1,control_url=url)),flush=True);server.serve_forever()
''')
        fixture.chmod(0o755)
        cli = self.root / 'fake-cli'
        cli.write_text('''#!/usr/bin/env python3
import os,sys,tomllib,urllib.request
config=tomllib.load(open(os.environ['XDG_CONFIG_HOME']+'/pb/config.toml','rb'))
url=config['Profiles']['fixture']['url']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
opener.open(url+'/api/v1/about').read()
os.write(1,opener.open(url+'/emit').read())
os.write(2,b'{"code":"COMMAND_FAILED","message":"rpc error: code = InvalidArgument desc = fixture stream complete"}\\n')
sys.exit(1)
''')
        cli.chmod(0o755)
        return cli, fixture

    def test_small_protocol_integration_without_real_flight_timing(self):
        cli, fixture = self.fake_protocol()
        result = p6.run_sample(cli, p6.workloads()[0], fixture, p6.run.DEFAULT_METER, self.root / 'sample')
        self.assertEqual(result['errors'], [])
        self.assertTrue(result['valid'])
        self.assertEqual(result['terminal_condition'], 'expected_remote_failure_InvalidArgument')
        self.assertIsNone(result['fixture_encoded_bytes'])
        self.assertEqual(result['batch_visibility']['matched'], 1)

    def test_hash_framing_sample_rejects_changed_later_stdout(self):
        cli, fixture = self.fake_protocol()
        cli.write_text(cli.read_text().replace("os.write(1,opener.open(url+'/emit').read())",
            "os.write(1,opener.open(url+'/emit').read()+b'bad\\n\\n')"))
        result = p6.run_sample(cli, p6.workloads()[0], fixture, p6.run.DEFAULT_METER, self.root / 'sample',
                               collector_mode='hash-framing')
        self.assertFalse(result['valid'])
        self.assertEqual(result['json_parse_attempts'], 1)
        self.assertIn('fixture expectation mismatch: stdout_sha256', result['errors'])
        self.assertEqual(result['kind'], 'observer_control')

    def test_missing_exact_request_evidence_rejected_even_with_all_events(self):
        cli, fixture = self.fake_protocol()
        original = p6.Fixture.summary
        def summary(fixture):
            value = original(fixture)
            del value['streams']['logs']['request_validation']
            return value
        with patch.object(p6.Fixture, 'summary', summary):
            result = p6.run_sample(cli, p6.workloads()[0], fixture, p6.run.DEFAULT_METER, self.root / 'sample')
        self.assertFalse(result['valid'])
        self.assertIn('exact request validation evidence unavailable', result['errors'])

    @unittest.skipUnless((p6.TMP / 'fx/pb-fixture-r2').exists() and
                         (p6.TMP.parent / 'baselines/pb-go').exists(), 'immutable exact-summary fixture and Go required')
    def test_real_reconnect_low_event_cap_uses_exact_summary_in_both_collectors(self):
        fixture = p6.TMP / 'fx/pb-fixture-r2'
        go = p6.TMP.parent / 'baselines/pb-go'
        workload = next(w for w in p6.workloads(0.1, 'functional') if w['name'] == 'reconnect')
        workload['fixture_limits'] = dict(event_limit=4, event_hard_limit=6, event_sample=100, batch_event_sample=100)
        for mode in p6.COLLECTOR_MODES:
            result = p6.run_sample(go, workload, fixture, p6.run.DEFAULT_METER, self.root / mode, collector_mode=mode)
            self.assertTrue(result['valid'], result['errors'])
            self.assertGreater(result['fixture_events_dropped'], 0)
            self.assertEqual(result['requests'], 2)
            self.assertGreater(result['request_evidence']['uncaptured_dogets'], 0)
            self.assertEqual(result['request_evidence']['admission'], 'exact_summary_counters')
            self.assertEqual(result['request_evidence']['request_validation']['auth_checks'], 2)
            self.assertEqual(result['request_evidence']['request_validation']['ticket_checks'], 2)
            self.assertEqual(result['fixture_first_record_emission_source'], 'exact_summary')
            self.assertIsNotNone(result['fixture_terminal_to_exit_ms'])
            if mode == 'hash-framing':
                self.assertEqual(result['json_parse_attempts'], 1)

    def test_fixture_panic_counter_rejects_otherwise_valid_output(self):
        cli, fixture = self.fake_protocol()
        original = p6.Fixture.summary
        def summary(fixture):
            value = original(fixture)
            value['event_kinds'] = {'fixture_panic': 1}
            return value
        with patch.object(p6.Fixture, 'summary', summary):
            result = p6.run_sample(cli, p6.workloads()[0], fixture, p6.run.DEFAULT_METER, self.root / 'sample')
        self.assertFalse(result['valid'])
        self.assertIn('fixture panic counter is nonzero', result['errors'])

    def test_fixture_panic_event_rejects_otherwise_valid_output(self):
        cli, fixture = self.fake_protocol()
        original = p6.Fixture.summary
        def summary(fixture):
            value = original(fixture)
            with (fixture.root / 'fixture.events.jsonl').open('a') as events:
                events.write(json.dumps(dict(kind='fixture_panic', t_ns=p6.clock_ns())) + '\n')
            return value
        with patch.object(p6.Fixture, 'summary', summary):
            result = p6.run_sample(cli, p6.workloads()[0], fixture, p6.run.DEFAULT_METER, self.root / 'sample')
        self.assertFalse(result['valid'])
        self.assertIn('fixture panic event observed', result['errors'])


if __name__ == '__main__':
    unittest.main()
