#!/usr/bin/env python3
"""Bounded synthetic real-shape evidence; the gate guards BEFORE test imports.

Run directly with python3 -B. No historical cache, runtime harness or binaries
are imported. Fixture writes stay in the explicitly owned scratch directory.
"""
import phase6_duration_gate as gate

import copy
import io
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr

Path, json, os = gate.Path, gate.json, gate.os
BASE = Path(gate.SCRATCH)
BASE.mkdir(exist_ok=True)
RUN = BASE / ('synthetic-' + str(time.time_ns()))
RUN.mkdir()


def bundle(live=True):
    fatal = dict(op='status', code=3, message='fixture stream complete')
    ops = [dict(op='live', rows_per_batch=128, duration_ms=5, interval_ms=2), fatal] if live else [
        dict(op='batch', rows=1, count=1), fatal]
    stream = dict(schema='basic', seed=2026, subscriptions=[dict(ops=ops)],
                  expected_ticket='{"stream":"logs"}')
    scenario = dict(phase6=dict(expected_output=True, streams=dict(logs=stream)))
    raw_live = dict(ops=1, scheduled_batches=3, emitted_batches=2, omitted_by_deadline=1,
        omitted_by_termination=0, stopped_by_deadline=1, stopped_by_count=0,
        stopped_by_termination=0, unbounded_ops=0,
        lateness_ns=dict(count=2, sum=3, max=2), send_call_ns=dict(count=2, sum=3, max=2),
        last=dict(scheduled=3, emitted=2, duration_ms=5, interval_ms=2, batches=0,
                  stop_reason='deadline', started_ns=10, deadline_ns=5000010, ended_ns=6000000)) if live else None
    terminal = dict(code='InvalidArgument', reason='status', message='fixture stream complete', sub=0, t_ns=6000010)
    batches = 2 if live else 1
    final = dict(streams=dict(logs=dict(batches=batches, live=raw_live, last_terminal=terminal)),
                 event_kinds=dict(batch_emit_done=batches, terminal=1))
    stderr = b'{"error":{"code":"COMMAND_FAILED","message":"rpc error: code = InvalidArgument desc = fixture stream complete","retryable":false}}\n'
    meter = dict(exit_code=1, termination=dict(exit=1), resource_source='native_child_wait4')
    production = dict(errors=[], scheduled_batches=3 if live else 0, delivered_batches=batches,
                      throughput_admissible=not live, mode='sink_limited' if live else 'finite')
    if live:
        production.update(live=copy.deepcopy(raw_live), schedule_complete=False)
    sample = dict(meter, valid=True, capture_complete=True, contract_validated=True, errors=[],
        timed_out=False, terminal_condition='expected_remote_failure_InvalidArgument', batches=batches,
        stderr_bytes=len(stderr), stderr_sha256=gate.hashlib.sha256(stderr).hexdigest(),
        contract=dict(termination=dict(exit=1)), production=production)
    return [sample, copy.deepcopy(sample), meter, scenario, final, stderr, b'']


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True) + '\n')


def campaign(name):
    root = RUN / name
    root.mkdir()
    restrictions = dict(ratio_quarantine=['host CPU flagged'],
        metric_quarantine=dict(cpu_ms=['host CPU flagged']),
        metric_scope=dict(wall_ms='sink_gated'), contract_comparison=dict(comparable=False,
            differing_fields=['batches'], reasons=['delivered workload differs']))
    records = []
    controls = []
    for folder, live, label in [(Path('slow-sink/sample-000-go'), True, 'go'),
            (Path('slow-sink/sample-000-candidate'), True, 'candidate'),
            (Path('go-aa/0'), False, None), (Path('go-aa/1'), False, None)]:
        b = bundle(live)
        sample, row, meter, scenario, final, stderr, fixture_stderr = b
        if live:
            row.update(label=label, workload='slow-sink', round=0, warmup=False)
            records.append(row)
        else:
            controls.append(row)
        for file, obj in [('sample.json', sample), ('meter.json', meter),
                          ('scenario.json', scenario), ('fixture-final-summary.json', final)]:
            dump(root / folder / file, obj)
        (root / folder / 'stderr.prefix.bin').write_bytes(stderr)
        (root / folder / 'fixture.stderr').write_bytes(fixture_stderr)
    stream = copy.deepcopy(bundle()[3]['phase6']['streams']['logs'])
    del stream['expected_ticket']
    report = dict(schema_version=2, tier='functional', collector_mode='full', complete=True, passed=True,
        collector=dict(sha256=gate.FROZEN_COLLECTOR),
        workloads=[dict(name='slow-sink', stream=stream)],
        binaries=dict(go={}, candidate={}), repetitions=1, warmups=0,
        expected_measured_samples=2, recorded_measured_samples=2,
        summaries={'slow-sink': restrictions})
    dump(root / 'report.json', report)
    dump(root / 'go-aa.json', dict(complete=True, passed=True, samples=controls))
    (root / 'samples.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
    return root


class GateTests(unittest.TestCase):
    def rejects(self, b):
        with self.assertRaises((ValueError, TypeError, KeyError)):
            gate.check_sample(*b)

    def sync(self, b):
        b[0]['production']['live'] = copy.deepcopy(b[4]['streams']['logs']['live'])
        b[1] = copy.deepcopy(b[0])

    def test_valid_live_and_control(self):
        for live in (True, False):
            with self.subTest(live=live):
                e = gate.check_sample(*bundle(live))
                self.assertEqual(e['meter_exit']['exit_code'], 1)

    def test_every_conservation_counter_strict_types(self):
        for field in ('scheduled_batches', 'emitted_batches', 'omitted_by_deadline', 'omitted_by_termination'):
            for bad in (None, -1, True, False, 1.0, '1', 'missing'):
                with self.subTest(field=field, bad=bad):
                    b = bundle()
                    live = b[4]['streams']['logs']['live']
                    if bad == 'missing':
                        del live[field]
                    else:
                        live[field] = bad
                    self.sync(b)
                    self.rejects(b)

    def test_omission_off_by_one_with_projection_in_sync(self):
        for field in ('omitted_by_deadline', 'omitted_by_termination'):
            b = bundle()
            b[4]['streams']['logs']['live'][field] += 1
            self.sync(b)
            self.rejects(b)

    def test_schedule_cannot_be_manufactured_from_counts(self):
        b = bundle()
        b[4]['streams']['logs']['live']['scheduled_batches'] += 1
        b[4]['streams']['logs']['live']['omitted_by_deadline'] += 1
        self.sync(b)
        b[0]['production']['scheduled_batches'] += 1
        b[1] = copy.deepcopy(b[0])
        self.rejects(b)

    def test_copied_projection_is_not_authority(self):
        b = bundle()
        b[4]['streams']['logs']['live']['omitted_by_deadline'] += 1
        self.rejects(b)
        b = bundle()
        b[0]['production']['live']['last']['emitted'] = 999
        b[1] = copy.deepcopy(b[0])
        self.rejects(b)

    def test_emitted_stream_capture_and_production_binding(self):
        for field in ('emitted', 'stream', 'capture', 'projection'):
            b = bundle()
            if field == 'emitted':
                b[4]['streams']['logs']['live'].update(emitted_batches=1, omitted_by_deadline=2)
                self.sync(b)
            if field == 'stream':
                b[4]['streams']['logs']['batches'] = 1
            if field == 'capture':
                b[0]['batches'] = 1
            if field == 'projection':
                b[0]['production']['delivered_batches'] = 1
            b[1] = copy.deepcopy(b[0])
            self.rejects(b)

    def test_deterministic_schedule_ceil_count_repeat_rules(self):
        stream = bundle()[3]['phase6']['streams']['logs']
        sub = stream['subscriptions'][0]
        self.assertEqual(gate.schedule(stream)['scheduled_batches'], 3)
        sub['repeat'] = 2
        sub['ops'][0]['batches'] = 2
        self.assertEqual(gate.schedule(stream), dict(live_ops=2, scheduled_batches=4, finite_batches=0))
        sub['repeat'] = 0  # Saved Go fixture treats zero repeat/count as one.
        self.assertEqual(gate.schedule(stream)['scheduled_batches'], 2)
        sub['ops'][0]['duration_ms'] = 0
        sub['ops'][0]['interval_ms'] = 0
        self.assertEqual(gate.schedule(stream)['scheduled_batches'], 2)
        sub['ops'][0].update(duration_ms=5, interval_ms=0)
        with self.assertRaisesRegex(ValueError, 'unbounded'):
            gate.schedule(stream)
        for field in ('repeat',):
            sub[field] = True
            with self.assertRaises(ValueError):
                gate.schedule(stream)
        finite = bundle(False)[3]['phase6']['streams']['logs']
        finite['subscriptions'][0]['repeat'] = 3
        finite['subscriptions'][0]['ops'][0]['count'] = 0
        self.assertEqual(gate.schedule(finite)['finite_batches'], 3)

    def test_exit_and_termination_each_source_and_controls(self):
        for live in (True, False):
            for index in (0, 1, 2):
                for field, value in [('exit_code', 0), ('exit_code', True), ('exit_code', -9),
                        ('exit_code', None), ('termination', {'signal': 9}),
                        ('termination', {'exit': 0}), ('termination', {'exit': True}),
                        ('termination', {'exit': 1, 'signal': 9})]:
                    with self.subTest(live=live, source=index, field=field, value=value):
                        b = bundle(live)
                        b[index][field] = value
                        self.rejects(b)
        b = bundle()
        b[0]['exit_code'] = b[1]['exit_code'] = 0
        self.rejects(b)
        b = bundle()
        b[2] = {}
        self.rejects(b)

    def test_status_valid_true_is_insufficient(self):
        for live in (True, False):
            for field, value in [('valid', False), ('valid', 1), ('capture_complete', False),
                    ('contract_validated', False), ('errors', ['injected error']), ('errors', None),
                    ('timed_out', True), ('timed_out', 0), ('terminal_condition', 'success')]:
                b = bundle(live)
                b[0][field] = value
                b[1] = copy.deepcopy(b[0])
                self.rejects(b)

    def test_fixture_and_exact_basic_terminal(self):
        for field in ('code', 'message', 'reason'):
            b = bundle()
            b[4]['streams']['logs']['last_terminal'][field] = 'wrong'
            self.rejects(b)
        for kind in ('fixture_panic', 'batch_emit_failed'):
            b = bundle()
            b[4]['event_kinds'][kind] = 1
            self.rejects(b)
        b = bundle()
        b[6] = b'panic: synthetic\n'
        self.rejects(b)
        for mut in (b'fixture stream complete', b'{"error":{"retryable":true}}'):
            b = bundle()
            b[5] = mut
            self.rejects(b)

    def test_strict_json(self):
        for data in (b'{"x":1,"x":2}', b'{"x":{"y":0,"y":1}}', b'{"x":NaN}',
                     b'{"x":1e999}', b'[' * 100 + b'0' + b']' * 100, b'\xff'):
            with self.assertRaises(ValueError):
                gate.strict_json(data)
        self.assertFalse(gate.same({'exit': True}, {'exit': 1}))

    def test_malformed_nested_object_types(self):
        for source, key in ((0, 'contract'), (1, 'contract'), (3, 'phase6'), (4, 'streams')):
            for value in (None, [], True):
                b = bundle()
                b[source][key] = value
                self.rejects(b)
        for value in (None, [], True):
            b = bundle()
            b[4]['streams']['logs']['last_terminal'] = value
            self.rejects(b)
        with self.assertRaises(ValueError):
            gate.schedule(dict(subscriptions=[True]))

    def test_campaign_exact_hashes_restrictions_and_exclusive_output(self):
        root = campaign('pass')
        out = RUN / 'pass-result.json'
        result = gate.run(root / 'report.json', out)
        self.assertTrue(result['supplemental_passed'])
        self.assertEqual(len(result['results']), 4)
        self.assertEqual(len(result['inputs']), 27)
        self.assertEqual(result['comparative_admission'], 'withheld')
        report = json.loads((root / 'report.json').read_bytes())
        self.assertEqual(result['source_restrictions']['slow-sink'], report['summaries']['slow-sink'])
        for receipt in result['inputs'].values():
            data = Path(receipt['path']).read_bytes()
            self.assertEqual(receipt['size_bytes'], len(data))
            self.assertEqual(receipt['sha256'], gate.hashlib.sha256(data).hexdigest())
        settled = out.read_bytes()
        with self.assertRaisesRegex(ValueError, 'no overwrite'):
            gate.run(root / 'report.json', out)
        self.assertEqual(out.read_bytes(), settled)

    def test_missing_meter_is_per_sample_failure(self):
        root = campaign('missing-meter')
        # Create a separate real-shape sample with no meter: preserve all originals.
        # Change role folder by constructing a fresh campaign tree, omitting one file.
        target = RUN / 'missing-meter-copy'
        target.mkdir()
        for path in root.rglob('*'):
            rel = path.relative_to(root)
            if str(rel) == 'slow-sink/sample-000-go/meter.json':
                continue
            dest = target / rel
            if path.is_dir():
                dest.mkdir(exist_ok=True)
            else:
                dest.write_bytes(path.read_bytes())
        result = gate.evaluate(target / 'report.json')
        self.assertFalse(result['supplemental_passed'])
        self.assertEqual([r['passed'] for r in result['results']], [False, True, True, True])
        self.assertIn('missing input', result['results'][0]['errors'][0])

    def test_mutated_campaign_emits_failure_fields_and_cli_one(self):
        root = campaign('failure-receipt')
        final_path = root / 'slow-sink/sample-000-go/fixture-final-summary.json'
        final = json.loads(final_path.read_bytes())
        final['streams']['logs']['live']['omitted_by_deadline'] += 1
        dump(final_path, final)
        output = RUN / 'failure-receipt.json'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(['--report', str(root / 'report.json'), '--out', str(output)]), 1)
        result = json.loads(output.read_bytes())
        self.assertEqual([r['passed'] for r in result['results']], [False, True, True, True])
        failure = result['results'][0]
        self.assertEqual(failure['observed']['raw_final']['live']['omitted_by_deadline'], 2)
        self.assertEqual(failure['observed']['sample']['production']['live']['omitted_by_deadline'], 1)
        self.assertIn('slow-sink/sample-000-go/fixture-final-summary.json', failure['input_keys'])
        self.assertIn('conservation', failure['errors'][0])

    def test_input_bounds_aliases_escape_and_output_disjoint(self):
        root = campaign('paths')
        for dest in (root / 'new.json', root / 'sample.json', root, root.parent):
            with self.assertRaises(ValueError):
                gate.output_path(root / 'report.json', dest)
        alias = RUN / 'report-alias.json'
        alias.symlink_to(root / 'report.json')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            gate.safe_path(alias)
        hardlink = root / 'hardlink.json'
        os.link(root / 'report.json', hardlink)
        with self.assertRaisesRegex(ValueError, 'hardlink'):
            gate.Inputs(root).read('hardlink.json')
        with self.assertRaisesRegex(ValueError, 'traversal'):
            gate.Inputs(root).read('../other.json')
        outside = RUN / 'outside-campaign.json'
        outside.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'escape'):
            gate.Inputs(root).read(outside)
        small = root / 'small.bin'
        small.write_bytes(b'0123456789abcdefg')
        with self.assertRaisesRegex(ValueError, 'byte bound'):
            gate.Inputs(root).read('small.bin', 16)

    def test_total_file_and_record_limits(self):
        root = campaign('bounds')
        original_bytes, original_files = gate.TOTAL_BYTES, gate.MAX_FILES
        try:
            gate.TOTAL_BYTES = 1
            with self.assertRaisesRegex(ValueError, 'byte bound'):
                gate.Inputs(root).read('report.json')
            gate.TOTAL_BYTES = original_bytes
            gate.MAX_FILES = 0
            with self.assertRaisesRegex(ValueError, 'file count bound'):
                gate.Inputs(root).read('report.json')
        finally:
            gate.TOTAL_BYTES, gate.MAX_FILES = original_bytes, original_files
        (root / 'samples.jsonl').write_bytes(b'{}\n' * (gate.MAX_SAMPLES + 1))
        with self.assertRaisesRegex(ValueError, 'line bound'):
            gate.evaluate(root / 'report.json')

    def test_guard_self_checks_without_native_calls(self):
        for name in ('subprocess', 'socket', 'ctypes', 'multiprocessing', 'pty'):
            with self.assertRaises(RuntimeError):
                __import__(name)
        for function, args in [(os.fork, ()), (os.system, ('never-run',)),
                (os.posix_spawn, ('never-run', [], {})), (os.kill, (0, 0)),
                (gate.fcntl.flock, (-1, 0)), (gate.fcntl.lockf, (-1, 0))]:
            with self.assertRaises(RuntimeError):
                function(*args)
        for path in ('/proc/self/status', '/sys/kernel/uevent_seqnum', '/tmp/duration-gate-forbidden'):
            with self.assertRaises(RuntimeError):
                open(path, 'rb')

    def test_cli_in_process(self):
        root = campaign('cli')
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(gate.main(['--report', str(root / 'report.json'), '--out', str(RUN / 'cli.json')]), 0)
            self.assertEqual(gate.main(['--report', str(root / 'report.json'), '--out', str(RUN / 'cli.json')]), 2)


if __name__ == '__main__':
    unittest.main()
