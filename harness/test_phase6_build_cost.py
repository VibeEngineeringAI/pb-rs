#!/usr/bin/env python3
"""R5 pure/mock causal regressions. All execution/kill/wait/lock/network guards precede runner import."""
from contextlib import ExitStack, contextmanager, nullcontext
import fcntl
import errno
import _posixsubprocess
import _signal
import _socket
import posix
import resource
import io
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
FORBIDDEN = []
GUARDED = []
GUARDS = ExitStack()


def forbidden(name):
    def fail(*args, **kwargs):
        FORBIDDEN.append(name)
        raise AssertionError('forbidden real operation: ' + name)
    return fail


# Guard before importing ANY production code; individual tests layer explicit simulated calls only.
for module, names in (
    (subprocess, ('Popen', 'run', 'call', 'check_call', 'check_output', 'getoutput', 'getstatusoutput', '_fork_exec')),
    (_posixsubprocess, ('fork_exec',)),
    (os, ('fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'system', 'popen',
          'execv', 'execve', 'execvp', 'execvpe', 'execl', 'execle', 'execlp', 'execlpe',
          'spawnl', 'spawnle', 'spawnlp', 'spawnlpe', 'spawnv', 'spawnve', 'spawnvp', 'spawnvpe',
          'kill', 'killpg', 'pidfd_open', 'wait', 'waitpid', 'waitid', 'wait3', 'wait4',
          'setsid', 'setpgid', 'setpgrp', 'tcsetpgrp', '_exit', 'abort', 'getpgid', 'getsid', 'getpgrp', 'setns', 'unshare')),
    (posix, ('fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'system', 'execv', 'execve',
             'kill', 'killpg', 'pidfd_open', 'wait', 'waitpid', 'waitid', 'wait3', 'wait4',
             'setsid', 'setpgid', 'setpgrp', 'tcsetpgrp', '_exit', 'abort', 'getpgid', 'getsid', 'getpgrp', 'setns', 'unshare')),
    (resource, ('prlimit', 'setrlimit')),
    (fcntl, ('flock', 'lockf', 'fcntl', 'ioctl')),
    (signal, ('signal', 'pthread_sigmask', 'pthread_kill', 'raise_signal', 'alarm', 'setitimer',
              'pidfd_send_signal', 'sigwait', 'sigwaitinfo', 'sigtimedwait', 'siginterrupt', 'sigpending', 'set_wakeup_fd')),
    (_signal, ('signal', 'pthread_sigmask', 'pthread_kill', 'raise_signal', 'alarm', 'setitimer',
               'pidfd_send_signal', 'sigwait', 'sigwaitinfo', 'sigtimedwait', 'siginterrupt', 'sigpending', 'set_wakeup_fd')),
    (_socket, ('socket', 'socketpair', 'getaddrinfo', 'gethostbyname', 'gethostbyname_ex', 'gethostbyaddr')),
    (socket, ('socket', 'socketpair', 'fromfd', 'create_connection', 'create_server', 'getaddrinfo', 'gethostbyname', 'gethostbyname_ex', 'gethostbyaddr')),
    (ssl, ('create_default_context', 'wrap_socket', 'get_server_certificate')),
    (ssl.SSLContext, ('wrap_socket',))):
    for name in names:
        if hasattr(module, name):
            GUARDS.enter_context(patch.object(module, name, forbidden(module.__name__ + '.' + name)))
            label = 'ssl.SSLContext' if module is ssl.SSLContext else module.__name__
            GUARDED.append(label + '.' + name)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import phase6_build_cost as b


def tearDownModule():
    GUARDS.close()
    if FORBIDDEN:
        raise AssertionError(FORBIDDEN)


# Byte-exact meter.c field layout; failed Cargo still makes the METER return zero.
def meter_record(exit_code=0, signal_=None, started=1000000, ended=2000000):
    term = {'signal': signal_} if signal_ else {'exit': exit_code}
    code = -signal_ if signal_ else exit_code
    return ('{"started_monotonic_ns":%d,"ended_monotonic_ns":%d,"started_unix_ns":1,"exit_code":%d,'
            '"termination":%s,"cpu_user_ms":12.500000,"cpu_system_ms":3.250000,"peak_rss_bytes":524288,'
            '"minor_faults":1,"major_faults":0,"voluntary_context_switches":1,'
            '"involuntary_context_switches":1,"block_input_operations":0,"block_output_operations":0,'
            '"resource_source":"native_child_wait4","proc_io_available":false,'
            '"proc_io_unavailable_reason":"permission_denied","proc_io_source":null,"proc_io_syscr":null,'
            '"proc_io_syscw":null,"proc_io_rchar":null,"proc_io_wchar":null,"proc_io_read_bytes":null,'
            '"proc_io_write_bytes":null,"proc_io_cancelled_write_bytes":null}\n'
            % (started, ended, code, json.dumps(term, separators=(',', ':')))).encode()


ELF = (b"ELF Header:\n  Class: ELF64\n  Data: 2's complement, little endian\n"
       b"  Type: DYN (Position-Independent Executable file)\n  Machine: Advanced Micro Devices X86-64\n"
       b"Program Headers:\n  INTERP 0x000318\n  [Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]\n")


class Clock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now
    def tick(self, amount):
        self.now += amount


class BuildCostTests(unittest.TestCase):
    def setUp(self):
        test_cache = b.CACHE / 'r5-corrections/mock-fixtures'
        test_cache.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='test-', dir=test_cache)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(b, 'CACHE', self.cache))
        self.patches.enter_context(patch.object(b, 'TMP', self.root / 'runner-tmp'))
        b.TMP.mkdir()
        self.patches.enter_context(patch.object(signal, 'sigpending', return_value=set()))
        self.patches.enter_context(patch.object(os, 'POSIX_SPAWN_CLOSEFROM', 99, create=True))
        self.handlers = {s: signal.SIG_DFL for s in b.STOP_SIGNALS | {signal.SIGCHLD}}
        self.pending = set()
        self.patches.enter_context(patch.object(signal, 'sigpending', side_effect=lambda: set(self.pending)))
        def handler(signum, callback):
            old = self.handlers.get(signum, signal.SIG_IGN)
            self.handlers[signum] = callback
            return old
        def consume(signals, timeout):
            self.assertTrue(b.STOP_SIGNALS <= self.mask, 'nonblocking signal wait requires a confirmed mask')
            self.assertEqual(timeout,0)
            ready = self.pending & set(signals)
            if not ready:
                return None
            signum = min(ready); self.pending.remove(signum)
            return SimpleNamespace(si_signo=signum)
        self.patches.enter_context(patch.object(signal, 'signal', handler))
        self.patches.enter_context(patch.object(signal, 'getsignal', lambda s: self.handlers.get(s, signal.SIG_IGN)))
        self.patches.enter_context(patch.object(signal, 'sigtimedwait', consume))
        self.mask = set()
        self.mask_events = []
        def simulated_mask(operation, values):
            previous = set(self.mask)
            if operation == signal.SIG_BLOCK:
                self.mask.update(values)
            elif operation == signal.SIG_UNBLOCK:
                self.mask -= set(values)
            else:
                self.mask = set(values)
            self.mask_events.append((operation, set(values)))
            return previous
        self.patches.enter_context(patch.object(signal, 'pthread_sigmask', simulated_mask))
        self.clock = Clock()
        self.patches.enter_context(patch.object(b.time, 'monotonic', self.clock))
        self.patches.enter_context(patch.object(b.time, 'time', return_value=1000.0))
        self.prep_root = self.cache / 'prepared'
        for name in ('base', 'candidate', 'cargo-home', 'home', 'runs'):
            (self.prep_root / name).mkdir(parents=True)
        for label in ('base', 'candidate'):
            (self.prep_root / label / 'Cargo.lock').write_text('mock lock ' + label + '\n')
            (self.prep_root / label / 'Cargo.toml').write_text('mock manifest ' + label + '\n')
        self.toolchain = self.root / 'toolchain'
        (self.toolchain / 'bin').mkdir(parents=True)
        (self.toolchain / 'lib').mkdir()
        (self.toolchain / 'lib/compiler.so').write_bytes(b'mock compiler library')
        tools = {}
        for name in ('cargo', 'rustc', 'rustdoc', 'meter', 'cc', 'ar', 'ld', 'readelf'):
            p = self.toolchain / 'bin' / name
            p.write_text('mock executable ' + name)
            tools[name] = b.identity(p)
        self.patches.enter_context(patch.object(b, 'TOOLCHAIN', self.toolchain))
        sources = {label: b.tree(self.prep_root / label) for label in ('base', 'candidate')}
        self.candidate_path = self.root / 'candidate.json'
        b.atomic_json(self.candidate_path, dict(base=b.BASE, rustc='mock rustc\n',
            sources={k:v['sha256'] for k,v in sources['candidate'].items()},
            builds={'pb':dict(argv=b.BUILD, features=[], build_kind='normal_release',
                cloud_synthetic_token_enabled=False, public_build_version='v0.0.0-harness')}))
        candidate_id = b.identity(self.candidate_path)
        self.patches.enter_context(patch.object(b, 'CANDIDATE_SHA256', candidate_id['sha256']))
        self.reviewed_path = self.root / 'reviewed.json'
        self.reviewed = dict(base=b.BASE, root=str(self.prep_root), candidate_manifest=candidate_id,
            sources=sources, cargo_home={}, tools=tools,
            toolchain=dict(path=str(self.toolchain), files=b.tree(self.toolchain)))
        b.atomic_json(self.reviewed_path, self.reviewed)
        reviewed_id = b.identity(self.reviewed_path)
        self.patches.enter_context(patch.object(b, 'REVIEWED_SHA256', reviewed_id['sha256']))
        self.prepared = dict(schema_version=3, extracted_sources=b.tree_receipt({}), state='ready', root=str(self.prep_root),
            reviewed_manifest=dict(declared=reviewed_id, selected=reviewed_id),
            candidate_manifest=dict(declared=candidate_id, selected=candidate_id),
            base=dict(commit=b.BASE, tree_sha256=b.tree_digest(sources['base'])),
            sources=sources, cargo_home={}, tools=tools, toolchain_path=str(self.toolchain),
            version='v0.0.0-harness', commit='', recorded_rustc='mock rustc\n',
            build_kind='normal_release', features=[], cloud_synthetic_token_enabled=False,
            runner=b.identity(b.__file__), build_argv=b.BUILD.copy())
        self.prepared_path = self.prep_root / 'prepared.json'
        self.write_prepared()
        self.output = self.prep_root / 'runs/mock-window'
        self.ack_bytes = b'simulated-ack-not-a-live-authorization'
        self.ack = self.root / 'simulated-ack.txt'
        self.ack.write_bytes(self.ack_bytes)
        self.receipt_path = self.root / 'simulated-receipt.json'
        self.receipt = dict(schema_version=2, run_id=self.output.name, output=str(self.output), issuer='coordinator', window_id='SIMULATED-WINDOW-ONLY',
            prepared_sha256=self.prepared_id['sha256'], jobs=2, timeout_s=10, warm_noop=False,
            start_utc='1970-01-01T00:16:39+00:00', deadline_utc='1970-01-01T00:50:00+00:00',
            ack_sha256=b.sha(self.ack_bytes))
        self.write_receipt()
        self.launches = []

    def write_prepared(self):
        b.atomic_json(self.prepared_path, self.prepared)
        self.prepared_id = b.identity(self.prepared_path)

    def write_receipt(self):
        b.atomic_json(self.receipt_path, self.receipt)
        self.receipt_id = b.identity(self.receipt_path)

    def mock_run(self, executor=None, warm=False, lock=None, signals=None, timeout=10, seconds=2000,
                 inspect=None, caller_outcome=False, compiler=None):
        self.receipt.update(prepared_sha256=self.prepared_id['sha256'], timeout_s=timeout, warm_noop=warm,
            deadline_utc=b.datetime.fromtimestamp(1000 + seconds, b.timezone.utc).isoformat())
        self.write_receipt()
        actual_signals = b.orderly_signals
        @contextmanager
        def coordinated_signals():
            with actual_signals() as control:
                with (signals() if signals else nullcontext()):
                    yield control
        def simulated_execute(*args, outcome):
            value = (executor or self.build)(*args)
            if value is not None:
                outcome.update(value)
            return outcome
        with patch.object(b, 'quiet_lock', lock or (lambda: nullcontext())), \
                patch.object(b, 'orderly_signals', coordinated_signals), \
                patch.object(b, 'compiler_version', compiler or (lambda *a:'mock rustc\n')), \
                patch.object(b, 'inspect_artifact', inspect or (lambda *args: {'mock_ELF': True})), \
                patch.object(b, 'execute', executor if caller_outcome else simulated_execute):
            return b.run(self.prepared_path, self.prepared_id['sha256'], self.output,
                self.receipt_path, self.receipt_id['sha256'], self.ack, 2, timeout, warm)

    def build(self, cmd, env, cwd, sample, meter, timeout, deadline, code=0, data=None):
        self.launches.append((sample.name, self.clock.now, timeout))
        self.assertEqual(cmd, [self.prepared['tools']['cargo']['path'], *b.BUILD[1:], '--jobs', '2'])
        self.assertEqual(meter, self.prepared['tools']['meter']['path'])
        target = Path(env['CARGO_TARGET_DIR'])
        (target / 'release').mkdir(exist_ok=True)
        (target / 'release/pb').write_bytes(b'mock executable')
        (sample / 'meter.json').write_bytes(data if data is not None else meter_record(code))
        return dict(meter_returncode=0, group_empty_verified=True, cleanup_status='SIMULATED',
                    errors=[], timed_out=False, pid=4242)

    def test_n1_digest_precedes_claims_or_json_parse(self):
        self.prepared_path.write_bytes(b'not even valid JSON')
        with patch.object(b.json, 'loads', side_effect=AssertionError('must not parse')):
            with self.assertRaisesRegex(ValueError, 'digest mismatch'):
                b.run(self.prepared_path, self.prepared_id['sha256'], self.output, self.receipt_path,
                    self.receipt_id['sha256'], self.ack, 2, 10)
        self.assertFalse(self.output.exists())

    def test_n1_missing_caller_digest_and_draft_never_admitted(self):
        for digest in (None, '', 'not-a-sha256'):
            with self.assertRaisesRegex(ValueError, 'SHA-256 is required'):
                b.run(self.prepared_path,digest,self.output,self.receipt_path,
                      self.receipt_id['sha256'],self.ack,2,10)
        self.prepared['state']='draft'
        self.write_prepared()
        with self.assertRaisesRegex(ValueError,'draft preparation'):
            self.mock_run()

    def test_n1_self_certified_meter_and_version_rejected_even_with_updated_caller_digest(self):
        other = self.root / 'unreviewed-meter'
        other.write_text('different bytes')
        self.prepared['tools']['meter'] = b.identity(other)
        self.prepared['version'] = 'v9.9.9-unreviewed'
        self.write_prepared()
        report = self.mock_run()
        self.assertFalse(report['passed'])
        self.assertFalse(self.launches)

    def test_n1_coedited_source_manifest_rejected(self):
        (self.prep_root / 'candidate/Cargo.toml').write_text('unreviewed change')
        self.prepared['sources']['candidate'] = b.tree(self.prep_root / 'candidate')
        self.write_prepared()
        report = self.mock_run()
        self.assertFalse(report['passed'])
        self.assertFalse(self.launches)

    def test_n1_canonical_manifest_and_base_commit_required(self):
        alias = self.root / 'alias.json'
        alias.write_bytes(self.prepared_path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'canonical'):
            b.admission(self.prepared, b.identity(alias), self.output, self.receipt, self.ack_bytes, 2, 10, False)
        self.prepared['base']['commit'] = '0'*40
        self.write_prepared()
        self.assertFalse(self.mock_run()['passed'])

    def test_n1_identical_meter_and_manifest_copies_bind_both_paths(self):
        meter = self.root / 'meter-copy'
        meter.write_bytes(Path(self.prepared['tools']['meter']['path']).read_bytes())
        self.prepared['tools']['meter'] = b.identity(meter)
        copy = self.root / 'candidate-copy.json'
        copy.write_bytes(self.candidate_path.read_bytes())
        self.prepared['candidate_manifest']['selected'] = b.identity(copy)
        self.write_prepared()
        self.assertTrue(b.verify(self.prepared, self.prepared_id))
        self.candidate_path.write_bytes(b'{"changed":true}')
        with self.assertRaises(ValueError):
            b.verify(self.prepared, self.prepared_id)

    def test_n1_neutral_commit_and_toolchain_role_containment(self):
        self.prepared['commit'] = 'unreviewed'
        self.write_prepared()
        with self.assertRaises(ValueError):
            b.verify(self.prepared, self.prepared_id)
        self.prepared['commit'] = ''
        outside = self.root / 'cargo-copy'
        outside.write_bytes(Path(self.prepared['tools']['cargo']['path']).read_bytes())
        self.prepared['tools']['cargo'] = b.identity(outside)
        self.write_prepared()
        with self.assertRaisesRegex(ValueError, 'outside'):
            b.verify(self.prepared, self.prepared_id)

    def test_n2_busy_lock_precedes_every_heavy_read_and_output(self):
        events = []
        @contextmanager
        def busy():
            events.append('lock')
            raise BlockingIOError('simulated busy lock')
            yield
        with patch.object(b, 'verify', side_effect=AssertionError('heavy read forbidden')), \
                patch.object(b, 'tree', side_effect=AssertionError('tree forbidden')), \
                patch.object(b, 'new_dir', side_effect=AssertionError('output forbidden')):
            report = self.mock_run(lock=busy)
        self.assertEqual(events, ['lock'])
        self.assertFalse(report['passed'])
        self.assertFalse(report['lock_acquired'])
        self.assertFalse(self.output.exists())

    def test_n4_meter_fields_checkpointed_before_target_failure_and_end_verify(self):
        original_tree = b.tree
        seen = []
        def target_failure(path):
            if Path(path).name.endswith('-target') and any(Path(path).iterdir()):
                disk = json.loads((self.output / 'report.json').read_bytes())
                captured = disk['samples'][0]
                seen.append(captured['raw_meter_identity'])
                self.assertEqual(captured['metrics']['wall_ms'], 1)
                self.assertEqual(captured['cargo_exit_code'], 0)
                self.assertEqual(captured['termination'], {'exit':0})
                raise ValueError('simulated target symlink')
            return original_tree(path)
        with patch.object(b, 'tree', target_failure):
            report = self.mock_run()
        self.assertTrue(seen)
        self.assertFalse(report['passed'])
        self.assertEqual(report['samples'][0]['metrics']['peak_rss_bytes'], 524288)
        self.assertTrue(report['samples'][0]['executable'])

    def test_n4_non_object_missing_malformed_and_nulls_fail_robustly(self):
        for value in (b'[]', b'null', b'{broken', b'{"peak_rss_bytes":null}', b'{"cpu_user_ms":1e999}', None):
            path = self.root / 'meter-record.json'
            path.unlink(missing_ok=True)
            if value is not None:
                path.write_bytes(value)
            capture = b.metrics_result(path)
            self.assertFalse(capture['metrics_complete'])
            self.assertTrue(capture['errors'])
            self.assertTrue(all(v is None for v in capture['metrics'].values()))
            if value is not None:
                self.assertEqual(capture['raw_meter_identity']['sha256'], b.sha(value))

    def test_n4_primary_timeout_survives_artifact_errors(self):
        def timeout(*args):
            target = Path(args[1]['CARGO_TARGET_DIR'])
            (target / 'bad-link').symlink_to('/nonexistent')
            raise subprocess.TimeoutExpired('SIMULATED-METER', args[5])
        report = self.mock_run(executor=timeout)
        sample = report['samples'][0]
        self.assertFalse(report['passed'])
        self.assertTrue(any('TimeoutExpired' in e for e in sample['errors']))
        self.assertTrue(any('symlink' in e for e in sample['errors']))
        self.assertTrue(all(v is None for v in sample['metrics'].values()))

    def test_n5_interrupted_atomic_write_does_not_poison_later_checkpoint(self):
        path = self.root / 'report.json'
        b.atomic_json(path, {'old':True})
        def interrupted(value, out, **kwargs):
            out.write('{"partial')
            raise InterruptedError('SIMULATED checkpoint interrupt')
        with patch.object(b.json, 'dump', interrupted):
            with self.assertRaises(InterruptedError):
                b.atomic_json(path, {'new':True})
        self.assertEqual(json.loads(path.read_bytes()), {'old':True})
        self.assertFalse(list(self.root.glob('.report.json-*')))
        b.atomic_json(path, {'final':True})
        self.assertEqual(json.loads(path.read_bytes()), {'final':True})
        self.assertEqual(self.mask, set())

    def test_n5_capture_checkpoint_retry_is_durable_before_next_hash(self):
        real = b.atomic_json
        state = {'failed':False}
        def interrupted(path, value):
            if Path(path).name == 'report.json' and value.get('samples') and \
                    'raw_meter_identity' in value['samples'][0] and not state['failed']:
                state['failed'] = True
                raise InterruptedError('SIMULATED one-shot capture checkpoint interrupt')
            return real(path,value)
        with patch.object(b, 'atomic_json', interrupted):
            report = self.mock_run()
        self.assertFalse(report['passed'])
        self.assertTrue(state['failed'])
        self.assertEqual(json.loads((self.output/'report.json').read_bytes())['samples'][0]['metrics']['wall_ms'],1)

    def test_n6_guard_runs_after_all_prelaunch_hashes(self):
        real_tree = b.tree
        def slow_tree(path):
            if Path(path).name.endswith('-target') and any(Path(path).iterdir()):
                self.clock.tick(350)  # Exceeds the new full-schedule planning estimate after admission.
            return real_tree(path)
        def build(*args):
            self.clock.tick(args[5])
            return self.build(*args)
        with patch.object(b, 'tree', slow_tree):
            report = self.mock_run(executor=build, warm=True, timeout=10, seconds=1250)
        self.assertFalse(report['passed'])
        self.assertLess(len(self.launches),4)
        self.assertTrue(all(t + timeout <= 2250 for _,t,timeout in self.launches))

    def test_n6_launch_guard_retains_full_timeout_and_measured_reserve(self):
        budget = b.Budget(self.clock.now + 50)
        budget.costs={'target':10,'verify':5}
        with self.assertRaises(b.BudgetExpired):
            budget.launch(20)
        self.assertEqual(self.clock.now,1000)

    def test_n6_chunked_hash_and_copy_stop_on_budget(self):
        class Reader:
            calls=0
            def read(reader, size):
                reader.calls+=1
                self.clock.tick(2)
                return b'x'*16
        reader=Reader()
        with b.bounded_work(b.Budget(1005)):
            with self.assertRaises(b.BudgetExpired):
                b.stream_hash(reader)
        self.assertEqual(reader.calls,2)
        source=self.root/'copy-source'
        source.write_bytes(b'a'*(2*b.CHUNK))
        expected=b.tree_file(source)
        self.clock.now=1000
        original_check=b.check_budget
        calls=[]
        def expensive_read_check():
            calls.append(1)
            self.clock.tick(2)
            original_check()
        with b.bounded_work(b.Budget(1005)), patch.object(b,'check_budget',expensive_read_check):
            with self.assertRaises(b.BudgetExpired):
                b.copy_bound(source,self.root/'partial-copy',expected)
        self.assertLessEqual((self.root/'partial-copy').stat().st_size,b.CHUNK)

    def test_n6_tar_internal_reads_share_deadline(self):
        class CompressedReader:
            def read(reader,size):
                self.clock.tick(4)
                return b'compressed bytes'
        with b.bounded_work(b.Budget(1005)):
            with self.assertRaises(b.BudgetExpired):
                b.DeadlineReader(CompressedReader()).read(10240)

    def test_budget_observation_preserves_primary_validation_error(self):
        budget=b.Budget(1005)
        def primary():
            self.clock.tick(10)
            raise ValueError('PRIMARY validation failure')
        with self.assertRaisesRegex(ValueError,'PRIMARY validation failure'):
            budget.observe('verify',primary)

    def test_n6_budget_exhaustion_skips_end_full_rehash(self):
        real_verify=b.verify
        calls=[]
        def verify(*args):
            calls.append(self.clock.now)
            return real_verify(*args)
        def exhausted(*args):
            result=self.build(*args)
            self.clock.tick(2000)
            return result
        with patch.object(b,'verify',verify):
            report=self.mock_run(executor=exhausted)
        self.assertFalse(report['passed'])
        self.assertFalse(report['end_identity_check']['complete'])
        self.assertEqual(len(calls),2)
        self.assertEqual(report['samples'][0]['metrics']['wall_ms'],1)

    def test_n6_end_check_rejects_late_validation_even_if_mock_verifier_ignores_budget(self):
        real_verify=b.verify
        real_identity=b.identity
        state={'n':0}
        def count_verify(*args):
            state['n']+=1
            return real_verify(*args)
        def late_identity(path):
            observed=real_identity(path)
            if Path(path)==self.receipt_path and state['n']==4:
                self.clock.now=3001  # The final receipt stat/EOF read returns after one OS stall.
            return observed
        with patch.object(b,'verify',count_verify),patch.object(b,'identity',late_identity):
            report=self.mock_run()
        self.assertFalse(report['passed'])
        self.assertFalse(report['end_identity_check']['passed'])

    def test_n6_per_sample_launch_guard_rejects_insufficient_final_reserve(self):
        original_verify=b.verify
        def slower_verify(*args):
            value=original_verify(*args)
            self.clock.tick(20)
            return value
        def builder(*args):
            self.clock.tick(args[5])
            return self.build(*args)
        with patch.object(b,'verify',slower_verify):
            with self.assertRaisesRegex(ValueError,'full timeouts'):
                self.mock_run(executor=builder,timeout=20,seconds=100)
            report={'passed':False}
        self.assertFalse(self.launches,'a full timeout plus observed final reserve must fit BEFORE launch')
        self.assertFalse(report['passed'])

    def test_n7_real_shaped_failed_cargo_cannot_validate_stale_noop_artifact(self):
        inspected=[]
        count={'n':0}
        def builder(*args):
            count['n']+=1
            return self.build(*args,code=101 if count['n']==2 else 0)
        report=self.mock_run(executor=builder,warm=True,inspect=lambda p,e,bin,s: inspected.append(s.name) or {'ok':True})
        sample=report['samples'][1]
        self.assertEqual(sample['meter_returncode'],0)
        self.assertEqual(sample['cargo_exit_code'],101)
        self.assertEqual(sample['termination'],{'exit':101})
        self.assertTrue(sample['metrics_complete'])
        self.assertFalse(sample['cargo_success'])
        self.assertFalse(report['passed'])
        self.assertNotIn('base-warm-noop',inspected)

    def test_n7_timeout_usually_has_no_meter_record_nulls_not_partial_invention(self):
        def timeout(*args):
            return dict(meter_returncode=-9,timed_out=True,group_empty_verified=True,
                        errors=['SIMULATED TimeoutExpired'])
        report=self.mock_run(executor=timeout)
        sample=report['samples'][0]
        self.assertIsNone(sample['raw_meter_identity'])
        self.assertIsNone(sample['cargo_exit_code'])
        self.assertTrue(all(v is None for v in sample['metrics'].values()))
        self.assertFalse(sample['complete'])
        self.assertFalse(report['passed'])

    def test_n7_genuinely_completed_meter_record_survives_later_timeout(self):
        def completed_then_timeout(*args):
            outcome=self.build(*args)
            outcome.update(meter_returncode=-9,timed_out=True,errors=['SIMULATED later timeout'])
            return outcome
        report=self.mock_run(executor=completed_then_timeout)
        self.assertEqual(report['samples'][0]['metrics']['wall_ms'],1)
        self.assertFalse(report['samples'][0]['passed'])
        self.assertFalse(report['samples'][0]['complete'])

    def test_n7_noop_binary_must_equal_cold_binary(self):
        count={'n':0}
        def changed(*args):
            count['n']+=1
            outcome=self.build(*args)
            if count['n']==2:
                (Path(args[1]['CARGO_TARGET_DIR'])/'release/pb').write_bytes(b'changed-noop')
            return outcome
        report=self.mock_run(executor=changed,warm=True)
        self.assertFalse(report['passed'])
        self.assertTrue(any('differs from cold' in e for e in report['samples'][1]['errors']))

    def test_n8_native_build_outputs_keep_identities_and_bounded_prefix(self):
        target=self.root/'native-target'
        path=target/'release/build/aws-lc-sys-abc/output'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'x'*70000)
        result=b.native_build_outputs(target)[str(path)]
        self.assertEqual(result['sha256'],b.sha(b'x'*70000))
        self.assertEqual(result['captured_bytes'],65536)
        self.assertTrue(result['truncated'])

    def test_n9_elf_header_and_interpreter_requirements(self):
        binary=b.identity(self.toolchain/'bin/cargo')
        for index,data in enumerate((ELF,ELF.replace(b'DYN (',b'EXEC ('),
                ELF.replace(b'ELF64',b'ELF32'), ELF.replace(b'Advanced Micro Devices X86-64',b'ARM'),
                ELF.replace(b'DYN (',b'REL ('), ELF.replace(b'/lib64/ld-linux-x86-64.so.2',b'/wrong/ld'))):
            sample=self.root/('elf-'+str(index));sample.mkdir()
            with patch.object(subprocess,'run',return_value=SimpleNamespace(stdout=data,stderr=b'')):
                if index<2:
                    self.assertFalse(b.inspect_artifact(self.prepared,{},binary,sample)['native_cli_executed'])
                else:
                    with self.assertRaises(ValueError):
                        b.inspect_artifact(self.prepared,{},binary,sample)

    def test_n10_unbound_cache_files_and_ancestor_configs_rejected(self):
        for name in ('registry/cache/extra.crate','registry/index/extra-record', 'credentials.toml'):
            path=self.prep_root/'cargo-home'/name
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text('unbound')
            with self.assertRaises(ValueError):
                b.verify(self.prepared,self.prepared_id)
            path.unlink()
        config=self.prep_root/'.cargo/config.toml'
        config.parent.mkdir();config.write_text('[build]\n')
        with self.assertRaisesRegex(ValueError,'ancestor Cargo config'):
            b.verify(self.prepared,self.prepared_id)

    def test_n10_unbound_source_and_bookkeeping_policy(self):
        home=self.prep_root/'cargo-home'
        (home/'.package-cache').write_bytes(b'')
        self.assertTrue(b.verify(self.prepared,self.prepared_id))
        extra=home/'registry/src'/b.REGISTRY_DIR/'unknown-1'
        extra.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'unbound private dependency source'):
            b.verify(self.prepared,self.prepared_id)

    def crate(self, package, invalid=False):
        home=self.prep_root/'cargo-home'
        path=home/'registry/cache'/b.REGISTRY_DIR/(package+'.crate')
        path.parent.mkdir(parents=True,exist_ok=True)
        with tarfile.open(path,'w:gz') as tar:
            item=tarfile.TarInfo(package+'/src/lib.rs');item.size=5
            tar.addfile(item,io.BytesIO(b'hello'))
            if invalid:
                item=tarfile.TarInfo(package+'/link');item.type=tarfile.SYMTYPE;item.linkname='/etc/passwd'
                tar.addfile(item)
        return home,path

    def test_n11_atomic_extraction_no_partial_final_or_silent_skip(self):
        home,path=self.crate('demo-1',invalid=True)
        prepared=dict(self.prepared,cargo_home={path.relative_to(home).as_posix():b.tree_file(path)})
        for _ in range(2):
            with self.assertRaisesRegex(ValueError,'unsafe archive'):
                b.warm_source_cache(prepared)
        destination=home/'registry/src'/b.REGISTRY_DIR/'demo-1'
        self.assertFalse(destination.exists())
        self.assertFalse(list(destination.parent.glob('.extract-*')))
        destination.mkdir()
        with self.assertRaisesRegex(ValueError,'partial extraction'):
            b.warm_source_cache(prepared)

    def test_n11_valid_extraction_is_verified_against_archive(self):
        home,path=self.crate('demo-1')
        prepared=dict(self.prepared,cargo_home={path.relative_to(home).as_posix():b.tree_file(path)})
        b.warm_source_cache(prepared)
        prepared['extracted_sources']=b.tree_receipt(b.tree(home/'registry/src'))
        b.verify_private_cache(prepared)
        checksum,_=b.read_json(home/'registry/src'/b.REGISTRY_DIR/'demo-1/.cargo-checksum.json')
        self.assertEqual(checksum,{'package':prepared['cargo_home'][path.relative_to(home).as_posix()]['sha256'],
                                   'files':{'src/lib.rs':b.sha(b'hello')}})
        src=home/'registry/src'/b.REGISTRY_DIR/'demo-1/src/lib.rs'
        src.write_bytes(b'drift')
        with self.assertRaisesRegex(ValueError,'source.*drift'):
            b.verify_private_cache(prepared)

    def test_n12_checksum_and_members_validity_are_separate_counts(self):
        home,path=self.crate('odd-1',invalid=True)
        lock=('version=4\n[[package]]\nname="odd"\nversion="1"\nsource="%s"\nchecksum="%s"\n'
              %(b.REGISTRY,b.identity(path)['sha256'])).encode()
        inv=b.inventory(lock,lock,home)
        self.assertTrue(inv['packages'][0]['archive']['checksum_verified'])
        self.assertFalse(inv['packages'][0]['archive']['members_valid'])
        self.assertEqual(inv['totals']['base']['checksum_verified_archives'],1)
        self.assertEqual(inv['totals']['base']['verified_archives'],0)
        self.assertEqual(inv['totals']['base']['compressed_package_bytes_known'],0)

    def test_n13_late_signal_and_final_checkpoint_failure_never_pass(self):
        @contextmanager
        def late():
            yield
            raise InterruptedError('SIMULATED signal during context exit')
        report=self.mock_run(signals=late)
        self.assertFalse(report['passed'])
        self.assertFalse(json.loads((self.output/'report.json').read_bytes())['passed'])
        self.assertTrue(report['errors'])

    def test_n13_final_pass_checkpoint_interruption_retries_as_failure(self):
        real=b.atomic_json
        interrupted={'done':False}
        def late(path,value):
            if Path(path).name=='report.json' and value.get('passed') and not interrupted['done']:
                interrupted['done']=True
                real(path,value)
                raise InterruptedError('SIMULATED pending signal after final replace')
            return real(path,value)
        with patch.object(b,'atomic_json',late):
            report=self.mock_run()
        self.assertTrue(interrupted['done'])
        self.assertFalse(report['passed'])
        self.assertTrue(report['errors'])
        self.assertFalse(json.loads((self.output/'report.json').read_bytes())['passed'])

    def test_prepare_copies_only_bound_inputs_to_fresh_root_without_native_calls(self):
        destination=self.cache/'new-preparation'
        with patch.object(b,'quiet_lock',lambda:nullcontext()):
            with patch.object(b, 'warm_source_cache'):
                prepared=b.prepare(self.reviewed_path,destination,b.Budget(2000))
        self.assertEqual(prepared['state'],'ready')
        self.assertEqual(prepared['sources'],self.reviewed['sources'])
        self.assertEqual((destination/'base/Cargo.lock').read_bytes(),
                         (self.prep_root/'base/Cargo.lock').read_bytes())
        with patch.object(b,'quiet_lock',lambda:nullcontext()):
            with self.assertRaises(FileExistsError):
                b.prepare(self.reviewed_path,destination,b.Budget(2000))

    def test_output_symlink_and_parent_escape_rejected(self):
        linked=self.cache/'linked-root'
        linked.symlink_to(self.prep_root,target_is_directory=True)
        with self.assertRaises(ValueError):
            b.new_dir(linked/'escaped-run')
        with self.assertRaises(ValueError):
            b.new_dir(self.cache/'..'/'escaped-run')

    def test_n14_receipt_expiry_preparation_and_ack_binding(self):
        for key,value in (('prepared_sha256','0'*64),('window_id',''),('ack_sha256','0'*64),
                          ('start_utc','1970-01-02T00:00:00+00:00'),('deadline_utc','1970-01-01T00:00:00+00:00')):
            receipt=dict(self.receipt);receipt[key]=value
            with self.assertRaises(ValueError):
                b.admission(self.prepared,self.prepared_id,self.output,receipt,self.ack_bytes,2,10,False)

    def test_proc_stat_parser_retains_starttime_group_session_and_zombie_state(self):
        proc=self.root/'simulated-proc'
        (proc/'42').mkdir(parents=True)
        fields=['Z','1','42','42']+['0']*15+['12345']
        (proc/'42/stat').write_text('42 (name with a ) character) '+' '.join(fields))
        observed=b.proc_stat(42,proc)
        self.assertEqual(observed,dict(pid=42,state='Z',ppid=1,pgid=42,session=42,starttime=12345))

    def test_full_toolchain_library_mutation_detected_not_only_binaries(self):
        (self.toolchain/'lib/compiler.so').write_bytes(b'changed library')
        with self.assertRaisesRegex(ValueError,'full Rust toolchain'):
            b.verify(self.prepared,self.prepared_id)

    def test_mock_success_descriptive_only_cold_and_noop_conditions(self):
        report=self.mock_run(warm=True)
        self.assertTrue(report['passed'],report['errors'])
        self.assertEqual(len(report['samples']),4)
        self.assertIsNone(report['ratios'])
        self.assertFalse(report['release_claim'])
        self.assertEqual(report['samples'][1]['target_regular_file_bytes_delta'],0)
        with self.assertRaises(ValueError):
            self.mock_run()

    def test_neutral_env_ignores_inherited_secrets_and_flags(self):
        with patch.dict(os.environ,{'SECRET':'NO-COPY','RUSTFLAGS':'NO-COPY',
                                   'PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN':'NO-COPY'}):
            command,env=b.command_env(self.prepared,self.output,'base',2)
        self.assertNotIn('SECRET',env);self.assertNotIn('RUSTFLAGS',env)
        self.assertNotIn('PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN',env)
        self.assertEqual(env['PB_COMMIT'],'')
        self.assertTrue(Path(env['TMPDIR']).is_relative_to(b.TMP))

    def supervisor(self, live=False, timeout=False, reused=False, late_fork=False, scan_error=False,
                   unmask_interrupt=False, journal_interrupt=False, stat_failure=False):
        events=[]; state={'reaped':False,'live':live,'scans':0,'spawned':False,'interrupt':False}
        pid=31337
        leader=dict(pid=pid,pgid=pid,session=pid,starttime=77,state='Z')
        def stat(pid_):
            events.append('stat')
            if stat_failure:
                raise PermissionError('SIMULATED missing initial procfs proof')
            value=dict(leader)
            if reused and state['spawned']:
                value['starttime']=78 if events.count('observe-exit') >= 2 else 77
            return value
        def spawn(path,argv,env,**kwargs):
            events.append('spawn');state['spawned']=True
            self.assertTrue(b.STOP_SIGNALS<=self.mask)
            self.assertTrue(kwargs['setsid'])
            self.assertEqual(kwargs['setsigdef'], b.CHILD_DEFAULTS)
            self.assertEqual(kwargs['file_actions'][0], (os.POSIX_SPAWN_OPEN, 0, '/dev/null', os.O_RDONLY, 0))
            self.assertEqual(kwargs['file_actions'][-1], (os.POSIX_SPAWN_CLOSEFROM, 3))
            self.assertFalse(set(kwargs['setsigmask'])&b.CHILD_DEFAULTS)
            return pid
        def waitid(kind,pid_,flags):
            events.append('observe-exit')
            self.assertEqual(pid_,pid)
            self.assertTrue(flags&os.WNOWAIT)
            if state['reaped']:
                raise ChildProcessError('already reaped')
            if timeout and not state.get('killed'):
                return None
            return SimpleNamespace(si_pid=pid)
        def members(pid_,deadline):
            events.append('scan');state['scans']+=1
            if late_fork and state['scans']==2:
                state['live']=True
            others=[dict(pid=31338,pgid=pid,session=pid,starttime=88,state='S')] if state['live'] else []
            return dict(members=[dict(leader),*others],escaped=[],errors=['SIMULATED permission'] if scan_error else [],
                        complete=not scan_error)
        def kill(group,sig):
            events.append('kill')
            self.assertFalse(state['reaped'],'must never signal a reaped/reused group')
            self.assertEqual((group,sig),(pid,signal.SIGKILL))
            state['live']=False;state['killed']=True
        def reap(pid_,flags):
            events.append('reap')
            self.assertFalse(state['live'])
            self.assertGreaterEqual(state['scans'],2)
            state['reaped']=True
            return pid,0
        normal_mask=signal.pthread_sigmask
        def restore_interrupt(op,values):
            old=normal_mask(op,values)
            if unmask_interrupt and state['spawned'] and op==signal.SIG_SETMASK and not values and not state['interrupt']:
                state['interrupt']=True
                raise InterruptedError('SIMULATED pending termination after PID journal')
            return old
        original_atomic=b.atomic_json
        def journal(path,value):
            if journal_interrupt and not state['interrupt']:
                state['interrupt']=True
                raise InterruptedError('SIMULATED interrupted PID journal')
            return original_atomic(path,value)
        sample=self.root/'supervisor';sample.mkdir()
        with patch.object(os,'posix_spawn',spawn), patch.object(os,'chdir',lambda *args:None), \
                patch.object(os,'waitid',waitid),patch.object(os,'waitpid',reap), \
                patch.object(os,'killpg',kill),patch.object(b,'proc_stat',stat), \
                patch.object(b,'group_members',members),patch.object(b.time,'sleep',lambda seconds:self.clock.tick(seconds)), \
                patch.object(signal,'pthread_sigmask',restore_interrupt), \
                patch.object(signal,'getsignal',side_effect=lambda sig: self.handlers.get(sig,signal.SIG_IGN)), \
                patch.object(b,'atomic_json',journal):
            if scan_error or reused or stat_failure:
                # Legacy units inspect one bounded unknown attempt; full execute now MUST HOLD.
                state['spawned']=True
                outcome=b.initialize_outcome(dict(pid=pid,pgid=pid,group_empty_verified=False,cleanup_status='unknown'))
                if not stat_failure:
                    outcome['leader']=dict(leader)
                with b.blocked_signals(),b.durable_capture():
                    b.process_checkpoint(sample,outcome)
                    b.cleanup_round(outcome)
                    b.cleanup_journal(outcome,sample)
            else:
                outcome=b.execute(['/SIMULATED/cargo'],{},self.prep_root,sample,'/SIMULATED/meter',0.1,self.clock.now+10)
        return outcome,events,state

    def test_n3_pinned_leader_then_live_group_kill_then_reap_never_post_reap_signal(self):
        outcome,events,state=self.supervisor(live=True)
        self.assertTrue(outcome['group_empty_verified'],outcome['errors'])
        self.assertLess(events.index('observe-exit'),events.index('kill'))
        self.assertLess(events.index('kill'),events.index('reap'))
        self.assertNotIn('kill',events[events.index('reap')+1:])

    def test_n3_late_fork_resets_empty_observations_and_is_killed_before_reap(self):
        outcome,events,state=self.supervisor(late_fork=True)
        self.assertTrue(outcome['group_empty_verified'],outcome['errors'])
        self.assertIn('kill',events)
        self.assertGreaterEqual(state['scans'],4)

    def test_n3_reused_pid_identity_is_never_signalled_or_reaped(self):
        outcome,events,state=self.supervisor(reused=True,live=True)
        # Scan discovers a live child, then starttime changes before signal; no ownership remains.
        self.assertFalse(outcome['group_empty_verified'])
        self.assertNotIn('kill',events)
        self.assertNotIn('reap',events)

    def test_n3_timeout_retains_leader_until_cleanup_verified(self):
        outcome,events,state=self.supervisor(timeout=True)
        self.assertTrue(outcome['timed_out'])
        self.assertTrue(outcome['group_empty_verified'],outcome['errors'])
        self.assertLess(events.index('kill'),events.index('reap'))

    def test_n3_cleanup_unknown_never_reaps_or_claims_empty(self):
        outcome,events,state=self.supervisor(scan_error=True)
        self.assertFalse(outcome['group_empty_verified'])
        self.assertEqual(outcome['cleanup_status'],'unknown')
        self.assertNotIn('reap',events)
        self.assertTrue(any('cleanup' in e for e in outcome['errors']))

    def test_n3_pending_signal_and_journal_failure_enter_owned_cleanup(self):
        for index,kwargs in enumerate((dict(unmask_interrupt=True),dict(journal_interrupt=True))):
            if index:
                (self.root/'supervisor').rename(self.root/'old-supervisor')
            outcome,events,state=self.supervisor(live=True,**kwargs)
            self.assertTrue(outcome['group_empty_verified'],outcome['errors'])
            self.assertTrue(outcome['errors'])
            self.assertIn('kill',events)
            self.assertEqual(self.mask,set())

    def test_n3_initial_stat_failure_has_durable_pid_and_only_kernel_owned_fallback_signal(self):
        outcome,events,state=self.supervisor(live=True,stat_failure=True)
        self.assertFalse(outcome['group_empty_verified'])
        self.assertTrue(outcome['kernel_owned_fallback_signal'])
        self.assertNotIn('reap',events)
        self.assertLess(events.index('observe-exit'),events.index('kill'))
        journal=json.loads((self.root/'supervisor/process.json').read_bytes())
        self.assertEqual(journal['pid'],31337)
        self.assertEqual(journal['cleanup_status'],'unknown')

    # Adapted from independent R2 probes P03-P14 and mutate.py; all kernel operations below are simulated.
    @contextmanager
    def kernel_world(self, *, escaped=False, live=False, ownership_lost=False, initial_denied=False,
                     scan_interrupt=False, stall=False, hold_observer=None):
        state = dict(pid=31337, live=live, exited=not live, reaped=False, escaped=escaped,
                     events=[], masks=[], budgets=[], journal_before_stat=[], sleeps=0,
                     pending=set(), interrupt=False, killed=False, hold_rounds=0, mock_failures=[])
        def event(name):
            state['events'].append(name)
            state['masks'].append((name, set(self.mask)))
            state['budgets'].append((name, b.ACTIVE_BUDGET.get()))
        def spawn(path, argv, env, **kw):
            event('spawn')
            state['spawn_attributes']=kw
            self.assertEqual(kw['setsigdef'], b.CHILD_DEFAULTS)
            self.assertFalse(set(kw['setsigmask']) & b.CHILD_DEFAULTS)
            self.assertEqual(kw['file_actions'][0], (os.POSIX_SPAWN_OPEN, 0, '/dev/null', os.O_RDONLY, 0))
            self.assertEqual(kw['file_actions'][-1], (os.POSIX_SPAWN_CLOSEFROM, 3))
            return state['pid']
        def stat(pid):
            event('stat')
            state['journal_before_stat'].append((state['sample'] / 'process.json').exists())
            journal = json.loads((state['sample'] / 'process.json').read_bytes())
            self.assertEqual(journal['pid'], pid, 'PID journal must precede FIRST proc read')
            if initial_denied:
                raise PermissionError(errno.EACCES, 'SIMULATED proc denied')
            return dict(pid=pid, pgid=pid, session=pid, starttime=77,
                        state='Z' if state['exited'] else 'S')
        def waitid(kind, pid, flags):
            event('waitid')
            if state['reaped'] or ownership_lost:
                raise ChildProcessError('SIMULATED no longer kernel owned')
            self.assertTrue(flags & os.WNOWAIT)
            return SimpleNamespace(si_pid=pid) if state['exited'] else None
        def scan(pid, end):
            event('scan')
            if scan_interrupt and not state['interrupt']:
                state['interrupt'] = True
                state['pending'] |= b.STOP_SIGNALS
                raise InterruptedError('SIMULATED repeated stops during cleanup')
            return dict(members=[dict(pid=pid, pgid=pid, session=pid, starttime=77,
                                     state='Z' if state['exited'] else 'S')],
                        escaped=[dict(pid=40003, pgid=40003, session=pid, starttime=91, state='S')]
                        if state['escaped'] else [], errors=[], complete=True)
        def killpg(pid, sig):
            event('kill')
            self.assertFalse(state['reaped'])
            self.assertEqual(sig, signal.SIGKILL)
            state.update(live=False, exited=True, killed=True)
        def reap(pid, flags):
            event('reap')
            state['reap_with_escape'] = state['escaped']
            state['reaped'] = True
            return pid, 9 if state['killed'] else 0
        def sleep(seconds):
            self.assertGreaterEqual(seconds, 0)
            state['sleeps'] += 1
            self.clock.tick(seconds)
            if stall and state['sleeps'] == 1:
                self.clock.tick(2000)
            if seconds == 1:
                event('hold')
                state['hold_rounds'] += 1
                if hold_observer:
                    try:
                        hold_observer(state)
                    except BaseException as error:
                        # Assertions inside production HOLD must not disappear into its containment.
                        state['mock_failures'].append(type(error).__name__ + ': ' + str(error)[:500])
                state['escaped'] = False  # Explicit simulated external disappearance, not a release-file signal.
                if state['mock_failures'] or state['hold_rounds'] >= 8:
                    state['mock_failures'].append('bounded mock HOLD aborted; simulated proof injected, never native release')
                    if 'outcome' in state:
                        state['outcome']['group_empty_verified'] = True
        real_checkpoint = b.process_checkpoint
        def checkpoint(sample, outcome):
            state['outcome'] = outcome
            return real_checkpoint(sample, outcome)
        sample = self.root / 'r2-supervisor'
        sample.mkdir()
        state['sample'] = sample
        with patch.object(os, 'posix_spawn', spawn), patch.object(os, 'chdir', lambda *a: None), \
                patch.object(os, 'waitid', waitid), patch.object(os, 'waitpid', reap), \
                patch.object(os, 'killpg', killpg), patch.object(b, 'proc_stat', stat), \
                patch.object(b, 'group_members', scan), patch.object(b.time, 'sleep', sleep), \
                patch.object(b, 'process_checkpoint', checkpoint), \
                patch.object(signal, 'getsignal', side_effect=lambda sig: self.handlers.get(sig,signal.SIG_IGN)), \
                patch.object(signal, 'sigpending', side_effect=lambda: set(state['pending'])):
            yield state
        self.assertFalse(state['mock_failures'],state['mock_failures'])  # OUTSIDE production containment.

    def execute_world(self, state, deadline=None):
        return b.execute(['/SIMULATED/cargo'], {}, self.prep_root, state['sample'], '/SIMULATED/meter',
                         .05, self.clock.now + 60 if deadline is None else deadline)

    def test_r2_a_repeated_stops_masked_until_durable_cleanup_journal(self):
        with self.kernel_world(live=True, scan_interrupt=True) as state:
            result = self.execute_world(state)
        self.assertTrue(result['group_empty_verified'])
        self.assertTrue(state['killed'])
        self.assertTrue(all(b.STOP_SIGNALS <= mask for name, mask in state['masks']
                            if name in ('kill', 'scan', 'reap')))
        disk = json.loads((state['sample'] / 'process.json').read_bytes())
        self.assertEqual(set(disk['deferred_signals']), b.STOP_SIGNALS)
        self.assertTrue(disk['group_empty_verified'])
        self.assertEqual(self.mask, set())

    def test_r2_b_expired_window_cleanup_still_kills_and_journals(self):
        budget = b.Budget(self.clock.now + 500)
        with self.kernel_world(live=True, stall=True) as state, b.bounded_work(budget):
            result = self.execute_world(state, budget.deadline)
        self.assertTrue(result['timed_out'])
        self.assertTrue(state['killed'])
        self.assertTrue(result['group_empty_verified'])
        self.assertTrue(any('overrun' in e for e in result['errors']))
        self.assertTrue(all(budget is None for name,budget in state['budgets']
                            if name in ('kill','scan','reap')))
        self.assertTrue(json.loads((state['sample'] / 'process.json').read_bytes())['group_empty_verified'])

    def test_r2_c_escaped_members_hold_lock_pin_and_skip_all_end_verification(self):
        lock = {'held':False, 'released':False, 'observed':False}
        @contextmanager
        def held_lock():
            lock['held'] = True
            try:
                yield
            finally:
                lock.update(held=False, released=True)
        real_verify = b.verify
        verifies = []
        def verify(*args):
            verifies.append(self.clock.now)
            return real_verify(*args)
        def observe(state):
            lock['observed'] = True
            self.assertTrue(lock['held'])
            self.assertFalse(lock['released'])
            self.assertFalse(state['reaped'])
            self.assertEqual(len(verifies), 2)
            disk = json.loads((state['sample'] / 'process.json').read_bytes())
            self.assertEqual(disk['cleanup_status'], 'unknown-held')
            self.assertEqual(disk['group_empty_verified'], False)
            state['pending'] |= b.STOP_SIGNALS
        actual_execute = b.execute
        with self.kernel_world(escaped=True, hold_observer=observe) as state, \
                patch.object(b, 'verify', verify), patch.object(b, 'CLEANUP_S', .1):
            def execute(*args):
                return actual_execute(['/SIMULATED/cargo'], {}, self.prep_root, state['sample'],
                                      '/SIMULATED/meter', .05, self.clock.now + 60)
            report = self.mock_run(executor=execute, lock=held_lock)
        self.assertTrue(lock['observed'], 'unknown MUST enter hold before any lock release')
        self.assertTrue(state['reaped'])
        self.assertFalse(state.get('reap_with_escape'))
        self.assertTrue(lock['released'])
        self.assertEqual(len(verifies), 2, 'held run never performs expensive end verification')
        self.assertFalse(report['passed'])
        self.assertFalse(report['end_identity_check']['complete'])
        self.assertTrue(report['samples'][0]['held_ever'])
        self.assertTrue(report['held_ever'],'R5-3: HOLD inside execute must reach the top-level summary')

    def test_r2_c_no_release_by_elapsed_time_or_release_file(self):
        # Direct containment control: two unknown cycles despite arbitrary elapsed time and a release file.
        sample = self.root / 'held'; sample.mkdir()
        (sample / 'release').write_text('SIMULATED not authority')
        outcome = dict(pid=42, errors=[], observations=[], group_empty_verified=False)
        rounds = []
        observations = []
        def observe(record, holding=False):
            observations.append((holding,record['group_empty_verified'],record['cleanup_status']))
            rounds.append(self.clock.now)
            if len(rounds) >= 3:
                record.update(group_empty_verified=True, cleanup_status='verified-before-leader-reap')
        with b.blocked_signals(), b.durable_capture(), patch.object(b, 'cleanup_round', observe), \
                patch.object(b.time, 'sleep', lambda s: self.clock.tick(100000)):
            b.hold_owned(outcome, sample)
        self.assertEqual(len(rounds), 3)
        self.assertEqual(observations,[(True,False,'unknown-held')]*3)
        self.assertTrue(outcome['held_ever'])

    def test_r2_fallback_requires_fresh_kernel_ownership_before_signal(self):
        with self.kernel_world(initial_denied=True, ownership_lost=True) as state:
            result=b.initialize_outcome(dict(pid=state['pid'],pgid=state['pid'],group_empty_verified=False))
            with b.blocked_signals(),b.durable_capture():
                b.process_checkpoint(state['sample'],result)
                b.cleanup_round(result)
        self.assertNotIn('kill', state['events'])
        self.assertFalse(result['group_empty_verified'])
        self.assertFalse(state['reaped'])
        # Ownership can disappear between the first recovery waitid and the fallback signal itself.
        with patch.object(os, 'waitid', side_effect=ChildProcessError('SIMULATED ECHILD')), \
                patch.object(os, 'killpg') as kill:
            with self.assertRaises(ChildProcessError):
                b.signal_owned(42, None, dict(errors=[]))
            kill.assert_not_called()

    def test_r2_pid_journal_causally_precedes_first_proc_read(self):
        with self.kernel_world() as state, patch.object(b,'hold_owned',lambda *a: None):
            result = self.execute_world(state)
        self.assertTrue(state['journal_before_stat'])
        self.assertTrue(state['journal_before_stat'][0])
        self.assertTrue(result['group_empty_verified'])

    def test_r2_pending_delivery_waits_for_final_durable_journal(self):
        with self.kernel_world(scan_interrupt=True) as state:
            original_mask = signal.pthread_sigmask
            deliveries = []
            def mask(operation, values):
                if operation == signal.SIG_SETMASK and not (set(values) & b.STOP_SIGNALS) and state['pending']:
                    journal = json.loads((state['sample']/'process.json').read_bytes())
                    deliveries.append(journal)
                    self.assertTrue(journal['group_empty_verified'])
                    self.assertEqual(set(journal['deferred_signals']),b.STOP_SIGNALS)
                return original_mask(operation,values)
            with patch.object(signal,'pthread_sigmask',mask):
                result=self.execute_world(state)
        self.assertTrue(deliveries)
        self.assertTrue(result['stop_requested'])

    def test_r2_supervisor_preconditions_and_closefrom_fail_before_spawn(self):
        sample = self.root / 'no-launch'; sample.mkdir()
        cases = [(signal, 'getsignal', lambda *a: signal.SIG_IGN),
                 (b.threading, 'active_count', lambda: 2),
                 (b.threading, 'current_thread', lambda: object())]
        for module, name, value in cases:
            with patch.object(module, name, value), patch.object(os, 'posix_spawn', side_effect=ValueError('SIMULATED unexpected spawn')) as spawn:
                with self.assertRaises(ValueError):
                    b.execute([], {}, self.root, sample, 'mock', 1, 2000)
                spawn.assert_not_called()
        with patch.object(signal, 'getsignal', side_effect=lambda sig: self.handlers.get(sig,signal.SIG_IGN)), \
                patch.object(os, 'posix_spawn', side_effect=ValueError('SIMULATED unexpected spawn')) as spawn:
            delattr(os, 'POSIX_SPAWN_CLOSEFROM')
            with self.assertRaisesRegex(ValueError, 'CLOSEFROM'):
                b.execute([], {}, self.root, sample, 'mock', 1, 2000)
            spawn.assert_not_called()
            os.POSIX_SPAWN_CLOSEFROM = 99

    def test_r2_actual_final_spawn_guard_after_chdir_stall(self):
        budget = b.Budget(self.clock.now + 500)
        sample = self.root / 'spawn-guard'; sample.mkdir()
        with patch.object(signal, 'getsignal', side_effect=lambda sig: self.handlers.get(sig,signal.SIG_IGN)), \
                patch.object(os, 'chdir', side_effect=lambda *a: self.clock.tick(400)), \
                patch.object(os, 'posix_spawn', side_effect=AssertionError('spawn forbidden after stall')) as spawn, \
                b.bounded_work(budget):
            result = b.execute([], {}, self.root, sample, 'mock', 10, budget.deadline)
        spawn.assert_not_called()
        self.assertIsNone(result['group_empty_verified'])
        self.assertTrue(any('BudgetExpired' in e for e in result['errors']))

    def test_r2_proc_bytes_disappearance_permission_and_escape_tracking(self):
        proc = self.root / 'proc'
        for pid, pgid, sid in ((42,42,42),(43,43,42),(99,99,99)):
            entry = proc / str(pid); entry.mkdir(parents=True)
            fields = ['Z' if pid == 42 else 'S','1',str(pgid),str(sid)] + ['0']*15 + ['77']
            (entry / 'stat').write_bytes(str(pid).encode() + b' (cut\xe3\x81) ' + ' '.join(fields).encode())
        scan = b.group_members(42, 2000, proc)
        self.assertTrue(scan['complete'])
        self.assertEqual([m['pid'] for m in scan['escaped']], [43])
        real = b.proc_stat
        for code, complete in ((errno.ENOENT,True),(errno.ESRCH,True),(errno.EACCES,False)):
            def error(pid, root):
                if pid == 99:
                    raise OSError(code, 'SIMULATED proc race/denial')
                return real(pid,root)
            with patch.object(b, 'proc_stat', error):
                self.assertEqual(b.group_members(42,2000,proc)['complete'],complete)

    def test_r2_observed_setsid_descendant_remains_tracked_after_reparenting(self):
        proc=self.root/'escaped-proc'
        def write(pid,ppid,pgid,sid,start):
            path=proc/str(pid);path.mkdir(parents=True,exist_ok=True)
            fields=['Z' if pid==42 else 'S',str(ppid),str(pgid),str(sid)]+['0']*15+[str(start)]
            (path/'stat').write_bytes(str(pid).encode()+b' (mock) '+' '.join(fields).encode())
        write(42,1,42,42,77);write(43,42,43,43,88)
        outcome=dict(pid=42,observations=[])
        real=b.group_members
        with patch.object(b,'group_members',lambda pid,end:real(pid,end,proc)):
            self.assertEqual([m['pid'] for m in b.observe_group(outcome,2000)['escaped']],[43])
            write(43,1,43,43,88)
            self.assertEqual([m['pid'] for m in b.observe_group(outcome,2000)['escaped']],[43])
            write(43,1,43,43,89)  # A reused PID is NOT our tracked process.
            self.assertEqual(b.observe_group(outcome,2000)['escaped'],[])

    def test_r2_both_initial_process_journals_survive_post_spawn_expiry(self):
        budget=b.Budget(self.clock.now+500)
        with self.kernel_world() as state:
            original_spawn=os.posix_spawn
            def stalled(*args,**kwargs):
                pid=original_spawn(*args,**kwargs)
                self.clock.tick(2000)
                return pid
            snapshots=[]
            real=b.process_checkpoint
            def journal(sample,outcome):
                real(sample,outcome)
                snapshots.append(json.loads((sample/'process.json').read_bytes()))
            with patch.object(os,'posix_spawn',stalled),patch.object(b,'process_checkpoint',journal),b.bounded_work(budget):
                result=self.execute_world(state,budget.deadline)
        self.assertEqual(snapshots[0]['pid'],31337)
        self.assertIn('leader',snapshots[1])
        self.assertTrue(result['group_empty_verified'])

    def test_r2_remaining_timeout_one_clock_read_and_no_negative_sleep(self):
        with self.kernel_world(live=True) as state:
            real_clock = self.clock
            def advancing():
                real_clock.tick(.026)
                return real_clock.now
            with patch.object(b.time, 'monotonic', advancing):
                result = self.execute_world(state)
        self.assertTrue(result['timed_out'])
        self.assertFalse(any('non-negative' in e for e in result['errors']))

    def test_r2_durable_disk_checkpoints_and_preparation_failure_after_expiry(self):
        output = self.root / 'checkpoints'; output.mkdir()
        report = dict(errors=[], samples=[], complete=False, passed=False)
        with b.bounded_work(b.Budget(999)):
            b.checkpoint(output, report)
            b.process_checkpoint(output, {'pid':42})
        self.assertTrue(json.loads((output / 'report.json').read_bytes())['last_checkpoint_complete'])
        self.assertEqual(json.loads((output / 'process.json').read_bytes())['pid'],42)
        dest = self.cache / 'failed-preparation'
        def expire(*a):
            self.clock.tick(2000)
            raise b.BudgetExpired('SIMULATED copy expiration')
        with patch.object(b, 'quiet_lock', lambda: nullcontext()), patch.object(b, 'copy_bound', expire):
            with self.assertRaises(b.BudgetExpired):
                b.prepare(self.reviewed_path,dest,b.Budget(2000))
        self.assertFalse(json.loads((dest / 'preparation-failed.json').read_bytes())['complete'])

    def test_r2_g_replace_pass_then_persistent_failure_invalidates_consumable_report(self):
        real = b.atomic_json
        state = {'replaced':False}
        def fail(path,value):
            if Path(path).name == 'report.json' and state['replaced']:
                raise OSError(errno.EDQUOT, 'SIMULATED persistent failure')
            if Path(path).name == 'report.json' and value.get('passed'):
                real(path,value)
                state['replaced'] = True
                raise OSError(errno.EIO, 'SIMULATED directory fsync after replacement')
            return real(path,value)
        with patch.object(b,'atomic_json',fail):
            report = self.mock_run()
        self.assertTrue(state['replaced'])
        self.assertFalse(report['passed'])
        self.assertFalse((self.output / 'report.json').exists())
        self.assertTrue(report.get('report_invalidated'))
        self.assertTrue((self.output / 'report.unverified.json').exists())

    def test_r2_resource_source_readelf_stderr_and_artifact_cleanup_gates(self):
        value = json.loads(meter_record())
        value['resource_source'] = 'unreviewed resource semantics'
        p = self.root / 'meter-source.json'; p.write_text(json.dumps(value))
        self.assertFalse(b.metrics_result(p)['metrics_complete'])
        binary = b.identity(self.toolchain / 'bin/cargo')
        sample = self.root / 'elf-stderr'; sample.mkdir()
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout=ELF,stderr=b'warning')):
            with self.assertRaisesRegex(ValueError, 'stderr'):
                b.inspect_artifact(self.prepared,{},binary,sample)
        for field,value in (('meter_returncode',1),('group_empty_verified',False)):
            target = self.root / ('gate-' + field); (target / 'release').mkdir(parents=True)
            (target / 'release/pb').write_bytes(b'mock')
            folder = self.root / ('sample-' + field); folder.mkdir()
            (folder / 'meter.json').write_bytes(meter_record())
            output = self.root / ('output-' + field); output.mkdir()
            record = dict(errors=[], label='base', mode='cold-target', meter_returncode=0,
                          group_empty_verified=True,target_regular_file_bytes_before=0)
            record[field] = value
            report = dict(errors=[], samples=[record], jobs=2)
            with patch.object(b,'inspect_artifact',return_value={'SIMULATED_valid_ELF':True}) as inspect:
                b.capture_sample(self.prepared,output,report,record,folder,target,{})
                inspect.assert_not_called()

    def test_r2_receipt_exact_schema_schedule_window_and_ack_negatives(self):
        changes = [('jobs',3),('jobs',True),('jobs',2.0),('timeout_s',11),('timeout_s',True),
                   ('timeout_s','10'),('warm_noop',True),('warm_noop',0),('window_id',None),
                   ('prepared_sha256','0'*64),('issuer','invented'),('schema_version',1),
                   ('run_id','other'),('output',str(self.prep_root / 'runs/other')),
                   ('window_id',' '),('window_id','x'*129),('ack_sha256','0'*64),
                   ('start_utc','1970-01-01T00:16:50+00:00'),
                   ('start_utc','1970-01-01T00:16:39'),
                   ('start_utc','1970-01-01T01:16:39+01:00'),
                   ('deadline_utc','1970-01-01T05:00:00+00:00'),
                   ('deadline_utc','1970-01-01T00:16:40+00:00'),('extra','unknown')]
        for key,value in changes:
            with self.subTest(key=key,value=value):
                receipt = dict(self.receipt); receipt[key] = value
                with self.assertRaises(ValueError):
                    b.admission(self.prepared,self.prepared_id,self.output,receipt,self.ack_bytes,2,10,False)
        for key in b.RECEIPT_KEYS:
            receipt = dict(self.receipt); del receipt[key]
            with self.assertRaises((ValueError,KeyError)):
                b.admission(self.prepared,self.prepared_id,self.output,receipt,self.ack_bytes,2,10,False)
        with self.assertRaises(ValueError):
            b.admission(self.prepared,self.prepared_id,self.prep_root/'runs/replay',
                        self.receipt,self.ack_bytes,2,10,False)
        for ack in (b'',b' ',b'other'):
            with self.assertRaises(ValueError):
                b.admission(self.prepared,self.prepared_id,self.output,self.receipt,ack,2,10,False)
        for jobs in (0,65,True,2.0):
            with self.assertRaises(ValueError):
                b.admission(self.prepared,self.prepared_id,self.output,self.receipt,self.ack_bytes,jobs,10,False)
        for timeout in (0,-1,True,float('nan'),float('inf'),3601):
            with self.assertRaises(ValueError):
                b.admission(self.prepared,self.prepared_id,self.output,self.receipt,self.ack_bytes,2,timeout,False)
        for ack in (b'',b'x'*4097):
            self.ack.write_bytes(ack)
            with self.assertRaises(ValueError):
                self.mock_run()
        self.assertFalse(self.output.exists())

    def test_r2_receipt_same_bytes_copy_ack_identity_and_consumption_survives_abort(self):
        def fail(*args):
            raise ValueError('SIMULATED failed run, receipt stays consumed')
        report = self.mock_run(executor=fail)
        receipt_bytes = self.receipt_path.read_bytes()
        self.assertEqual((self.output/'coordinator-receipt.json').read_bytes(),receipt_bytes)
        self.assertEqual(report['quiet_ack']['sha256'],b.sha(self.ack_bytes))
        self.assertNotIn(self.ack_bytes.decode(), json.dumps(report))
        marker = Path(report['receipt_consumption'])
        self.assertEqual(marker.parent,self.prep_root/'runs')
        self.assertIn(self.receipt_id['sha256'],marker.name)
        with self.assertRaises(FileExistsError):
            b.consume_receipt(self.prepared,self.receipt_id,self.prep_root/'runs/another-output',self.receipt)
        self.output.rename(self.prep_root/'runs/preserved-failed-output')
        second = self.mock_run()
        self.assertFalse(second['passed'])
        self.assertIsNone(second['group_empty_verified'])
        self.assertFalse(self.output.exists())
        self.assertFalse(self.launches)

    def test_r2_receipt_digest_and_ack_drift_fail_before_any_child(self):
        with self.assertRaisesRegex(ValueError,'digest mismatch'):
            b.run(self.prepared_path,self.prepared_id['sha256'],self.output,self.receipt_path,
                  '0'*64,self.ack,2,10)
        self.assertFalse(self.output.exists())
        original=b.verify
        def drift(*args):
            result=original(*args)
            self.ack.write_bytes(b'SIMULATED changed ACK')
            return result
        with patch.object(b,'verify',drift):
            report=self.mock_run()
        self.assertFalse(self.launches)
        self.assertFalse(report['passed'])
        self.assertTrue(Path(report['receipt_consumption']).exists())
        self.assertTrue(any('ACK file drift' in e for e in report['errors']))

    def test_r2_no_extraction_in_window_home_binding_empty_scratch_and_initial_reserve(self):
        with patch.object(b,'warm_source_cache',side_effect=AssertionError('never extract in window')):
            report = self.mock_run()
        self.assertTrue(report['passed'],report['errors'])
        self.assertEqual(list(b.TMP.iterdir()),[])
        self.assertGreaterEqual(b.Budget(2000).launch(10),b.TARGET_RESERVE_S)
        self.assertGreater(b.TARGET_PLAN_BYTES,609658232)
        self.assertGreater(b.TARGET_PLAN_FILES,2267)
        src=self.prep_root/'cargo-home/registry/src/unbound'
        src.parent.mkdir(parents=True)
        src.write_bytes(b'unbound extracted tree')
        with self.assertRaisesRegex(ValueError,'extracted source tree'):
            b.verify(self.prepared,self.prepared_id)
        src.unlink()
        home = self.prep_root/'home'
        (home/'.cargo').mkdir()
        with self.assertRaisesRegex(ValueError,'HOME'):
            b.verify(self.prepared,self.prepared_id)

    def test_r2_preparation_binds_extracted_sources_before_ready(self):
        events=[]
        def simulated_warm(prepared):
            self.assertEqual(prepared['state'],'draft')
            path = Path(prepared['root'])/'cargo-home/registry/src/mock-bound'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'mock extracted source')
            events.append('warm')
        def simulated_verify(prepared, identity):
            self.assertGreater(prepared['extracted_sources']['files'],0)
            self.assertEqual(prepared['state'],'draft')
            events.append('verify')
        with patch.object(b,'quiet_lock',lambda:nullcontext()), patch.object(b,'warm_source_cache',simulated_warm), \
                patch.object(b,'verify',simulated_verify):
            prepared=b.prepare(self.reviewed_path,self.cache/'mock-preparation',b.Budget(2000))
        self.assertEqual(events,['warm','verify'])
        self.assertEqual(prepared['state'],'ready')

    def test_r2_no_launch_null_and_bounded_cli_refusal(self):
        @contextmanager
        def busy():
            raise BlockingIOError('SIMULATED busy')
            yield
        report = self.mock_run(lock=busy)
        self.assertIsNone(report['group_empty_verified'])
        self.assertEqual(report['cleanup_status'],'not_observed')
        args=['runner','run','--prepared',str(self.prepared_path),'--prepared-sha256','0'*64,
              '--coordinator-receipt',str(self.receipt_path),'--receipt-sha256','0'*64,
              '--ack-file',str(self.ack),'--output',str(self.output),'--jobs','2','--timeout-s','10']
        stream=io.StringIO()
        with patch.object(sys,'argv',args), patch.object(b,'run',side_effect=ValueError('x'*2000)), \
                patch.object(sys,'stderr',stream):
            with self.assertRaises(SystemExit) as exc:
                b.main()
        self.assertEqual(exc.exception.code,1)
        self.assertIn('build-cost refused:',stream.getvalue())
        self.assertLess(len(stream.getvalue()),550)

    # R3 witnesses adapted from independent probes P01/P02/P03/P04/P05/P10b/P11.
    def test_r3_stop_after_checkpoint_replace_never_launches_later_sample(self):
        real_atomic,real_replace=b.atomic_json,os.replace
        arm={'kind':'pre','active':False,'done':False}
        def atomic(path,value):
            samples=value.get('samples',[]) if Path(path).name=='report.json' else []
            eligible=(len(samples)==1 and ('final_reserve_s' not in samples[0] if arm['kind']=='pre'
                                          else samples[0].get('capture_complete') and samples[0].get('passed')))
            arm['active']=eligible and not arm['done']
            try:
                return real_atomic(path,value)
            finally:
                arm['active']=False
        def replace(src,dst):
            result=real_replace(src,dst)
            if arm['active'] and Path(dst).name=='report.json':
                self.assertTrue(b.STOP_SIGNALS <= self.mask)
                self.pending.add(signal.SIGTERM)
                arm['done']=True
            return result
        for kind,expected in (('pre',0),('post',1)):
            arm.update(kind=kind,done=False)
            if kind=='post':
                self.output=self.prep_root/'runs/post-window'
                self.receipt.update(run_id=self.output.name,output=str(self.output))
                self.launches=[]
            with patch.object(b,'atomic_json',atomic),patch.object(os,'replace',replace):
                report=self.mock_run()
            self.assertTrue(arm['done'])
            self.assertEqual(len(self.launches),expected)
            self.assertFalse(report['passed'])
            self.assertEqual(report['stop']['first_signal'],signal.SIGTERM)
            self.assertEqual(report['stop']['signals'],[signal.SIGTERM])
            disk=json.loads((self.output/'report.json').read_bytes())
            self.assertFalse(disk['passed'])
            self.assertEqual(disk['stop']['signals'],[signal.SIGTERM])
            self.assertEqual(self.mask,set())

    def test_r3_recording_handlers_and_mask_failure_restore(self):
        original_handlers=dict(self.handlers)
        with b.orderly_signals() as control:
            for s in sorted(b.STOP_SIGNALS):
                self.handlers[s](s,None)  # Direct simulated delivery; NEVER async raises.
            self.assertEqual(control.snapshot()['signals'],sorted(b.STOP_SIGNALS))
            self.assertEqual(control.first,min(b.STOP_SIGNALS))
            with self.assertRaises(b.StopRequested):
                b.check_stop()
        self.assertEqual(self.mask,set())
        self.assertEqual(self.handlers,original_handlers)
        original=signal.pthread_sigmask
        arm={'once':True}
        def mask(op,values):
            old=original(op,values)
            if op==signal.SIG_BLOCK and set(values)==b.STOP_SIGNALS and arm['once']:
                arm['once']=False
                raise InterruptedError('SIMULATED binding fault AFTER applying block')
            return old
        with patch.object(signal,'pthread_sigmask',mask):
            with self.assertRaises(InterruptedError):
                with b.blocked_signals():
                    self.fail('failed block cannot enter body')
        self.assertEqual(self.mask,set())
        # A failure BEFORE applying must not perform sigtimedwait with an unconfirmed mask.
        with b.orderly_signals() as control:
            def unapplied(op,values):
                if op==signal.SIG_BLOCK and set(values)==b.STOP_SIGNALS:
                    raise OSError(errno.EIO,'SIMULATED block not applied')
                return original(op,values)
            with patch.object(signal,'pthread_sigmask',unapplied),patch.object(signal,'sigtimedwait') as wait:
                with self.assertRaises(OSError):
                    with b.blocked_signals():
                        self.fail('unconfirmed block cannot enter body')
                wait.assert_not_called()
            self.assertTrue(control.failed)
        # A block fault at a checkpoint must fail control, not be swallowed into a later build.
        real=b.atomic_json;arm['once']=True
        def atomic(path,value):
            samples=value.get('samples',[]) if Path(path).name=='report.json' else []
            if len(samples)==1 and 'final_reserve_s' not in samples[0] and arm['once']:
                with patch.object(signal,'pthread_sigmask',mask):
                    return real(path,value)
            return real(path,value)
        with patch.object(b,'atomic_json',atomic):
            report=self.mock_run()
        self.assertFalse(self.launches)
        self.assertTrue(report['stop']['control_failed'])
        self.assertEqual(self.mask,set())

    def test_r3_admission_preconditions_before_consumption_and_heavy_work(self):
        def run_refused():
            with patch.object(b,'consume_receipt') as consume,patch.object(b,'verify') as verify:
                try:
                    report=self.mock_run()
                except ValueError:
                    report={'passed':False}
                self.assertFalse(report['passed'])
                consume.assert_not_called();verify.assert_not_called()
                self.assertFalse(self.output.exists())
        self.mask=set(b.STOP_SIGNALS)
        run_refused();self.mask=set()
        self.handlers[signal.SIGCHLD]=signal.SIG_IGN
        run_refused();self.handlers[signal.SIGCHLD]=signal.SIG_DFL
        # Distinguish SIGCHLD from SIGINT (the independent M10i gap).
        with patch.object(signal,'getsignal',side_effect=lambda s: signal.SIG_IGN if s==signal.SIGCHLD else signal.SIG_DFL):
            with self.assertRaisesRegex(ValueError,'SIGCHLD'):
                b.supervisor_preconditions()
        with patch.object(os,'POSIX_SPAWN_CLOSEFROM',99):
            del os.POSIX_SPAWN_CLOSEFROM
            run_refused()
        with b.orderly_signals() as control:
            self.handlers[signal.SIGTERM]=signal.SIG_IGN
            with self.assertRaisesRegex(ValueError,'handlers'):
                b.validate_handlers(control)

    def test_r3_child_mask_defaults_and_caller_pid_visible_before_proc(self):
        caller=b.initialize_outcome()
        self.mask=set(b.CHILD_DEFAULTS)  # Direct helper only; run admission refuses blocked STOP inheritance.
        with self.kernel_world() as state:
            original=b.proc_stat
            seen=[]
            def stat(pid):
                seen.append(caller.get('pid'))
                return original(pid)
            with patch.object(b,'proc_stat',stat):
                result=b.execute(['/SIMULATED/cargo'],{},self.prep_root,state['sample'],'/SIMULATED/meter',
                                 .05,2000,outcome=caller)
        self.assertIs(result,caller)
        self.assertFalse(set(state['spawn_attributes']['setsigmask']) & b.CHILD_DEFAULTS)
        self.assertEqual(state['spawn_attributes']['setsigdef'],
                         {signal.SIGINT,signal.SIGTERM,signal.SIGHUP,signal.SIGPIPE,signal.SIGXFSZ})
        self.assertTrue(seen)
        self.assertTrue(all(pid==31337 for pid in seen))
        self.assertTrue(caller['group_empty_verified'])
        self.assertEqual(caller['containment_scope'],b.CONTAINMENT_SCOPE)
        self.assertEqual(self.mask,b.CHILD_DEFAULTS)
        self.mask=set()

    def test_r3_execute_cleanup_failure_stays_masked_and_uses_hold_fallback(self):
        with self.kernel_world(live=True) as state:
            masks=[];held=[]
            def spawn(path,argv,env,**kwargs):
                # Observe continuity at the fallback itself, not an earlier fixture assertion.
                state['events'].append('spawn')
                return state['pid']
            def broken(*args):
                masks.append(set(self.mask))
                raise KeyboardInterrupt('SIMULATED cleanup entry fault')
            def proof(outcome,sample):
                masks.append(set(self.mask));held.append(True)
                outcome.update(group_empty_verified=True,cleanup_status='verified-before-leader-reap',held_ever=True)
                state.update(live=False,exited=True,reaped=True)
                b.process_checkpoint(sample,outcome)
            with patch.object(os,'posix_spawn',spawn),patch.object(b,'finish_owned',broken),patch.object(b,'hold_owned',proof):
                result=self.execute_world(state)
        self.assertTrue(held,'execute must reach containment fallback')
        self.assertTrue(result['group_empty_verified'])
        self.assertTrue(all(b.STOP_SIGNALS <= mask for mask in masks),'lifetime mask survives fallback entry')
        self.assertEqual(self.mask,set())

    def _r3_boundary(self,journal_only=False,post_proof=False):
        original_execute=b.execute
        with self.kernel_world(live=not post_proof) as state:
            lock={'held':False,'snapshot':None}
            @contextmanager
            def quiet():
                lock['held']=True
                try:
                    yield
                finally:
                    lock['snapshot']=(state['live'],state['reaped'])
                    lock['held']=False
            real_verify=b.verify;verifies=[]
            def verify(*args):
                verifies.append(1);return real_verify(*args)
            def execute(cmd,env,cwd,sample,meter,timeout,deadline,*,outcome):
                state['sample']=sample
                if post_proof:
                    original_execute(cmd,env,cwd,sample,meter,.05,deadline,outcome=outcome)
                    for s in sorted(b.STOP_SIGNALS):
                        self.handlers[s](s,None)
                else:
                    local=b.initialize_outcome() if journal_only else outcome
                    local.update(pid=state['pid'],pgid=state['pid'],group_empty_verified=False,cleanup_status='unknown')
                    b.process_checkpoint(sample,local)
                raise MemoryError('SIMULATED unexpected execute exit')
            real_finish=b.finish_owned;finishes=[]
            def finish(outcome,sample,deadline):
                self.assertTrue(lock['held']);self.assertEqual(len(verifies),2)
                finishes.append(outcome['pid'])
                return real_finish(outcome,sample,deadline)
            with patch.object(b,'verify',verify),patch.object(b,'finish_owned',finish):
                report=self.mock_run(executor=execute,caller_outcome=True,lock=quiet)
        record=report['samples'][0]
        self.assertEqual(lock['snapshot'],(False,True),'no release with a live/unreaped owned child')
        self.assertEqual(record['pid'],31337)
        self.assertEqual(record['process_journal_identity']['path'],str(self.output/'base-cold-target/process.json'))
        self.assertTrue(report['group_empty_verified'])
        self.assertEqual(report['cleanup_status'],'observed')
        self.assertEqual(report['containment_scope'],'procfs pgid/session/descendant observation')
        journal=json.loads((self.output/'base-cold-target/process.json').read_bytes())
        self.assertEqual(journal['containment_scope'],report['containment_scope'])
        self.assertFalse(report['passed'])
        if post_proof:
            self.assertEqual(finishes,[31337],'normal execute cleanup once; no second boundary cleanup after proof')
            self.assertEqual(report['stop']['signals'],sorted(b.STOP_SIGNALS))
        else:
            self.assertTrue(finishes)
            self.assertEqual(len(verifies),2)
            self.assertTrue(record['held_ever'])
        self.assertNotIn('kill',state['events'][state['events'].index('reap')+1:])

    def test_r3_run_boundary_caller_outcome_unexpected_raise(self):
        self._r3_boundary()

    def test_r3_run_boundary_recovers_journal_only_pid(self):
        self._r3_boundary(journal_only=True)

    def test_r3_post_proof_exception_retains_launched_identity_and_all_stops(self):
        self._r3_boundary(post_proof=True)

    def test_r3_hold_contains_all_bookkeeping_base_exceptions_and_exact_sleep(self):
        sample=self.root/'r3-held';sample.mkdir()
        outcome=b.initialize_outcome(dict(pid=42,pgid=42,group_empty_verified=False))
        faults=[SystemExit(3),KeyboardInterrupt(),GeneratorExit(),MemoryError(),RecursionError(),
                InterruptedError(),OSError(errno.EDQUOT,'SIM'),ChildProcessError(),TimeoutError(),ValueError()]
        sleeps=[];rounds=[];masks=[]
        def cleanup(record,holding=False):
            rounds.append(holding)
            masks.append(set(self.mask))
            if len(rounds)<=len(faults):
                raise faults[len(rounds)-1]
            record.update(group_empty_verified=True,cleanup_status='verified-before-leader-reap')
        with b.blocked_signals(),b.durable_capture(),patch.object(b,'cleanup_round',cleanup), \
                patch.object(b.time,'sleep',side_effect=lambda s:sleeps.append(s)), \
                patch.object(signal,'sigpending',side_effect=MemoryError('SIMULATED persistent bookkeeping')), \
                patch.object(b,'note_error',side_effect=KeyboardInterrupt('SIMULATED note failure')):
            b.hold_owned(outcome,sample)
        self.assertEqual(len(rounds),len(faults)+1)
        self.assertEqual(sleeps,[1]*len(rounds),'C8: exactly one 1-second pause for every held round')
        self.assertTrue(all(rounds))
        self.assertTrue(all(b.STOP_SIGNALS <= mask for mask in masks))
        self.assertTrue(outcome['bookkeeping_failed'])
        self.assertTrue(outcome['group_empty_verified'])

    def test_r3_journal_catches_base_exception_and_preserves_claim(self):
        sample=self.root/'r3-journal';sample.mkdir()
        record=b.initialize_outcome(dict(pid=42,pgid=42,group_empty_verified=False))
        with b.blocked_signals(),b.durable_capture(),patch.object(b,'atomic_json',side_effect=GeneratorExit('SIM')):
            b.cleanup_journal(record,sample)
        self.assertEqual(record['pid'],42)
        self.assertFalse(record['group_empty_verified'])
        self.assertTrue(any('GeneratorExit' in e for e in record['errors']))

    def test_r3_kernel_fallback_has_its_own_recheck_on_initial_and_known_io_failure(self):
        outcome=b.initialize_outcome(dict(pid=42,pgid=42,group_empty_verified=False))
        with patch.object(os,'waitid',side_effect=[None,ChildProcessError('SIMULATED ownership lost')]), \
                patch.object(b,'proc_stat',side_effect=OSError(errno.EIO,'SIMULATED proc failure')), \
                patch.object(os,'killpg') as kill:
            b.cleanup_round(outcome)
            kill.assert_not_called()  # M06i: round-top ownership is insufficient for fallback.
        leader=dict(pid=42,pgid=42,session=42,starttime=77,state='S')
        with patch.object(os,'waitid',side_effect=[None,ChildProcessError('SIMULATED fallback loss')]), \
                patch.object(b,'proc_stat',side_effect=OSError(errno.EIO,'SIMULATED known-leader I/O')), \
                patch.object(os,'killpg') as kill:
            with self.assertRaises(ChildProcessError):
                b.signal_owned(42,leader,outcome)
            kill.assert_not_called()
        with patch.object(os,'waitid',return_value=None), \
                patch.object(b,'proc_stat',side_effect=OSError(errno.EIO,'SIMULATED I/O')), \
                patch.object(os,'killpg') as kill:
            b.signal_owned(42,leader,outcome)
            kill.assert_called_once_with(42,signal.SIGKILL)
        with patch.object(os,'waitid',return_value=None), \
                patch.object(b,'proc_stat',return_value=dict(leader,starttime=78)),patch.object(os,'killpg') as kill:
            with self.assertRaisesRegex(ValueError,'identity changed'):
                b.signal_owned(42,leader,outcome)
            kill.assert_not_called()
        # A first successful proc read that contradicts setsid is also a mismatch, not an I/O fallback.
        record=b.initialize_outcome(dict(pid=42,pgid=42,group_empty_verified=False))
        with patch.object(os,'waitid',return_value=None), \
                patch.object(b,'proc_stat',return_value=dict(leader,session=99)), \
                patch.object(os,'killpg') as kill,patch.object(os,'waitpid') as reap:
            b.cleanup_round(record)
            kill.assert_not_called();reap.assert_not_called()
            self.assertFalse(record['group_empty_verified'])
            self.assertTrue(any('no group signal permitted' in e for e in record['errors']))

    def test_r3_prekill_observation_fault_cannot_skip_owned_kill(self):
        leader=dict(pid=42,pgid=42,session=42,starttime=77,state='Z')
        record=b.initialize_outcome(dict(pid=42,pgid=42,leader=leader,group_empty_verified=False))
        events=[]
        def observe(*args):
            self.clock.tick(6)
            raise MemoryError('SIMULATED prekill observation')
        with patch.object(os,'waitid',return_value=SimpleNamespace(si_pid=42)), \
                patch.object(b,'proc_stat',return_value=leader),patch.object(b,'observe_group',observe), \
                patch.object(os,'killpg',side_effect=lambda *a:events.append('kill')):
            b.cleanup_round(record)
        self.assertEqual(events,['kill'])
        self.assertFalse(record['group_empty_verified'])

    def test_r3_cleanup_exit_and_wait_identities_required(self):
        leader=dict(pid=42,pgid=42,session=42,starttime=77,state='Z')
        empty=dict(members=[leader],escaped=[],errors=[],complete=True)
        for case in ('no-exit','wrong-waitid','wrong-reap'):
            record=b.initialize_outcome(dict(pid=42,pgid=42,leader=leader,group_empty_verified=False))
            wait=(None if case=='no-exit' else SimpleNamespace(si_pid=99 if case=='wrong-waitid' else 42))
            with patch.object(os,'waitid',return_value=wait),patch.object(b,'proc_stat',return_value=leader), \
                    patch.object(b,'group_members',return_value=empty),patch.object(os,'killpg'), \
                    patch.object(os,'waitpid',return_value=(99,0)) as reap, \
                    patch.object(b.time,'sleep',side_effect=lambda s:self.clock.tick(s)):
                b.cleanup_round(record,holding=True)
            self.assertFalse(record['group_empty_verified'],case)
            if case!='wrong-reap':
                reap.assert_not_called()

    def test_r3_zombie_members_do_not_rekill_and_held_round_is_bounded(self):
        leader=dict(pid=42,pgid=42,session=42,starttime=77,state='Z')
        zombie=dict(pid=43,pgid=42,session=42,starttime=88,state='Z')
        observed=dict(members=[leader,zombie],escaped=[],errors=[],complete=True)
        record=b.initialize_outcome(dict(pid=42,pgid=42,leader=leader,group_empty_verified=False))
        sleeps=[]
        def sleep(s):
            sleeps.append(s);self.clock.tick(s)
        with patch.object(os,'waitid',return_value=SimpleNamespace(si_pid=42)), \
                patch.object(b,'proc_stat',return_value=leader),patch.object(b,'group_members',return_value=observed) as scan, \
                patch.object(os,'killpg') as kill,patch.object(os,'waitpid') as reap,patch.object(b.time,'sleep',sleep):
            b.cleanup_round(record,holding=True)
            self.assertLessEqual(scan.call_count,2)
            self.assertEqual(sleeps,[],'C9: no five-second scan storm while held')
            self.assertEqual(kill.call_count,1)
            b.cleanup_round(record,holding=True)
            self.assertEqual(kill.call_count,1,'no repeated kill for zombies awaiting a subreaper')
            reap.assert_not_called()

    def test_r3_held_capture_skips_every_expensive_artifact_path(self):
        sample=self.root/'held-sample';sample.mkdir();(sample/'meter.json').write_bytes(meter_record())
        output=self.root/'held-output';output.mkdir()
        record=dict(held_ever=True,errors=[],label='base',mode='cold-target',meter_returncode=0,
                    group_empty_verified=True,target_regular_file_bytes_before=0)
        report=dict(errors=[],samples=[record],jobs=2)
        with patch.object(b,'tree',return_value={}) as tree,patch.object(b,'identity',return_value={}) as identity, \
                patch.object(b,'native_build_outputs',return_value={}) as native,patch.object(b,'inspect_artifact',return_value={}) as elf:
            b.capture_sample(self.prepared,output,report,record,sample,self.root/'no-target',{})
        tree.assert_not_called();identity.assert_not_called();native.assert_not_called();elf.assert_not_called()
        self.assertFalse(record['passed'])

    def test_r3_error_and_observation_bounds(self):
        record=b.initialize_outcome(dict(pid=42,pgid=42))
        for _ in range(200):
            b.note_error(record,'x'*3000)
        self.assertEqual(len(record['errors']),128)
        self.assertEqual(record['errors_dropped'],72)
        self.assertTrue(all(len(s)==2000 for s in record['errors']))
        leader=dict(pid=42,pgid=42,session=42,starttime=77,state='Z')
        with patch.object(b,'group_members',return_value=dict(members=[leader],escaped=[],errors=[],complete=True)):
            for _ in range(40):
                b.observe_group(record,2000)
        self.assertEqual(len(record['observations']),32)
        self.assertEqual(record['observations_dropped'],8)

    def test_r3_duplicate_keys_rejected_after_byte_digest_check(self):
        for path,raw in ((self.receipt_path,b'{"jobs":64,'+self.receipt_path.read_bytes()[1:]),
                         (self.prepared_path,b'{"root":"decoy",'+self.prepared_path.read_bytes()[1:])):
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'duplicate JSON key'):
                b.read_json(path,b.sha(raw))
            with self.assertRaisesRegex(ValueError,'digest mismatch'):
                b.read_json(path,'0'*64)
        self.assertFalse(self.output.exists())

    def test_r3_full_reserve_refused_before_any_receipt_consumption(self):
        with patch.object(b,'consume_receipt') as consume,patch.object(b,'verify') as verify:
            with self.assertRaisesRegex(ValueError,'full timeouts'):
                self.mock_run(seconds=59)
            consume.assert_not_called();verify.assert_not_called()
        self.assertFalse(self.output.exists())
        self.prepared['verification_reserve_s']=100;self.write_prepared()
        with self.assertRaisesRegex(ValueError,'full timeouts'):
            self.mock_run(seconds=300)
        self.assertEqual(list((self.prep_root/'runs').iterdir()),[])

    def test_r3_readmission_inside_lock_checks_current_utc_before_consume(self):
        @contextmanager
        def jumped():
            with patch.object(b.time,'time',return_value=3001):
                yield
        with patch.object(b,'consume_receipt') as consume,patch.object(b,'verify') as verify:
            report=self.mock_run(lock=jumped)
        self.assertFalse(report['passed'])
        consume.assert_not_called();verify.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_r3_receipt_copy_uses_original_hash_checked_bytes(self):
        expected=self.receipt_path.read_bytes()
        @contextmanager
        def changed():
            self.receipt_path.write_bytes(b'SIMULATED later bytes; must never copy these')
            yield
        report=self.mock_run(lock=changed)
        self.assertFalse(report['passed'])
        self.assertEqual((self.output/'coordinator-receipt.json').read_bytes(),expected)
        self.assertFalse(self.launches)

    def test_r3_semantic_receipt_consumption_survives_pre_output_crash_and_fsync(self):
        with patch.object(b,'new_dir',side_effect=OSError(errno.EIO,'SIMULATED crash after consumption')):
            first=self.mock_run()
        self.assertFalse(first['passed']);self.assertFalse(self.output.exists())
        original=self.receipt_path.read_bytes();variant=original+b'\n'
        self.receipt_path.write_bytes(variant)
        def simulated(*args,outcome):
            outcome.update(self.build(*args));return outcome
        with patch.object(b,'quiet_lock',lambda:nullcontext()),patch.object(b,'compiler_version',return_value='mock rustc\n'), \
                patch.object(b,'inspect_artifact',return_value={'SIMULATED_ELF':True}),patch.object(b,'execute',simulated):
            second=b.run(self.prepared_path,self.prepared_id['sha256'],self.output,self.receipt_path,
                         b.sha(variant),self.ack,2,10)
        self.assertFalse(second['passed']);self.assertFalse(self.output.exists())
        self.assertFalse(self.launches)
        markers=list((self.prep_root/'runs').iterdir())
        self.assertEqual(len(markers),2)
        self.assertTrue(any(p.name.startswith('.window-run-') for p in markers))
        # A new semantic identity still requires file and parent fsync for EACH marker.
        receipt=dict(self.receipt,window_id='SIMULATED-new-window')
        rid=dict(self.receipt_id,sha256='1'*64)
        real=b.exclusive_bytes;active={'path':None};events=[]
        def exclusive(path,data):
            active['path']=str(path)
            try:
                return real(path,data)
            finally:
                active['path']=None
        with patch.object(b,'exclusive_bytes',exclusive),patch.object(os,'fsync',side_effect=lambda fd:events.append(active['path'])):
            b.consume_receipt(self.prepared,rid,self.output,receipt)
        self.assertEqual(len(events),4)
        self.assertEqual(len(set(events)),2)
        self.assertTrue(all(events.count(p)==2 for p in set(events)))
        # A failed first fsync leaves the semantic marker: even a byte variant is unusable.
        receipt['window_id']='SIMULATED-partial-window'
        with patch.object(os,'fsync',side_effect=OSError(errno.EDQUOT,'SIMULATED partial marker')):
            with self.assertRaises(OSError):
                b.consume_receipt(self.prepared,dict(rid,sha256='2'*64),self.output,receipt)
        with self.assertRaises(FileExistsError):
            b.consume_receipt(self.prepared,dict(rid,sha256='3'*64),self.output,receipt)

    def test_r3_blank_ack_matching_digest_is_refused(self):
        self.ack_bytes=b' \n\t';self.ack.write_bytes(self.ack_bytes)
        self.receipt['ack_sha256']=b.sha(self.ack_bytes)
        with self.assertRaisesRegex(ValueError,'acknowledgement'):
            self.mock_run()
        self.assertFalse(self.output.exists())

    def test_r3_checkpoint_stop_after_atomic_retry_and_preparation_step(self):
        output=self.root/'retry-stop';output.mkdir()
        report=dict(errors=[],samples=[],passed=False,complete=False)
        original=b.atomic_json;calls=[]
        def atomic(path,value):
            calls.append(1)
            if len(calls)==1:
                raise OSError(errno.EIO,'SIMULATED one failed checkpoint')
            original(path,value)
            self.pending.add(signal.SIGTERM)
        with b.orderly_signals() as control,patch.object(b,'atomic_json',atomic):
            with self.assertRaises(b.StopRequested):
                b.checkpoint(output,report)
            self.assertEqual(control.first,signal.SIGTERM)
        self.assertEqual(len(calls),2,'a stop is outside the retry loop')
        self.assertTrue((output/'report.json').exists())
        dest=self.cache/'stopped-preparation'
        def stopped_copy(*args):
            self.pending.add(signal.SIGHUP)  # Simulated stop at first bounded preparation step; no copy.
        with patch.object(b,'quiet_lock',lambda:nullcontext()),patch.object(b,'copy_bound',stopped_copy):
            with self.assertRaises(b.StopRequested):
                b.prepare(self.reviewed_path,dest,b.Budget(2000))
        failed=json.loads((dest/'preparation-failed.json').read_bytes())
        self.assertFalse(failed['complete'])
        self.assertEqual(failed['stop']['first_signal'],signal.SIGHUP)
        self.assertFalse((dest/'prepared.json').exists())

    def test_r3_masked_wait_stop_burst_cleans_owned_group(self):
        caller=b.initialize_outcome()
        with self.kernel_world(live=True) as state:
            original_sleep=b.time.sleep;sent=[]
            def sleep(seconds):
                if not sent and seconds<1:
                    sent.append(True)
                    self.assertTrue(b.STOP_SIGNALS <= self.mask)
                    self.pending |= b.STOP_SIGNALS
                    state['pending'] |= b.STOP_SIGNALS
                return original_sleep(seconds)
            with b.orderly_signals() as control,patch.object(b.time,'sleep',sleep):
                b.execute(['/SIMULATED/cargo'],{},self.prep_root,state['sample'],'/SIMULATED/meter',
                          5,2000,outcome=caller)
                self.assertEqual(control.snapshot()['signals'],sorted(b.STOP_SIGNALS))
        self.assertTrue(sent)
        self.assertTrue(state['killed']);self.assertTrue(state['reaped'])
        self.assertFalse(caller['timed_out'])
        self.assertTrue(caller['group_empty_verified'])
        self.assertTrue(any('StopRequested' in error for error in caller['errors']))
        disk=json.loads((state['sample']/'process.json').read_bytes())
        self.assertEqual(disk['stop']['signals'],sorted(b.STOP_SIGNALS))
        self.assertEqual(self.mask,set())

    # R5 probes.py spawn_failure_world/reserve_attempt and mutate.py sites adapted with attribution.
    # Every hold witness has a bounded simulated end; assertions run OUTSIDE production containment.
    def test_r5_spawn_oserror_call_only_is_failed_no_child(self):
        actual=b.execute
        for code in (errno.EAGAIN,errno.ENOENT,errno.EINVAL,errno.EBADF):
            self.output=self.prep_root/'runs'/('failed-spawn-'+str(code))
            self.receipt.update(run_id=self.output.name,output=str(self.output))
            lock={'held':False,'released':False};calls=[];verifies=[]
            @contextmanager
            def quiet():
                lock['held']=True
                try:yield
                finally:lock.update(held=False,released=True)
            def spawn(*a,**kw):
                calls.append((lock['held'],b.STOP_SIGNALS <= self.mask))
                raise OSError(code,'SIMULATED PRIMARY spawn failure')
            def executor(*args,outcome):
                return actual(*args,outcome=outcome)
            def unexpected_hold(outcome,sample):
                # Bounded mutant witness only: force simulated completion, then fail outside HOLD.
                outcome['group_empty_verified']=True
            real_verify=b.verify
            def verify(*a):
                verifies.append(lock['held']);return real_verify(*a)
            with patch.object(os,'posix_spawn',spawn),patch.object(os,'chdir',lambda *a:None), \
                    patch.object(os,'waitid') as waitid,patch.object(os,'waitpid') as reap, \
                    patch.object(os,'killpg') as kill,patch.object(b,'hold_owned',side_effect=unexpected_hold) as hold, \
                    patch.object(b,'cleanup_round') as cleanup,patch.object(b,'verify',verify):
                report=self.mock_run(executor=executor,caller_outcome=True,lock=quiet)
            self.assertEqual(calls,[(True,True)])
            for mock in (waitid,reap,kill,hold,cleanup):mock.assert_not_called()
            self.assertTrue(lock['released']);self.assertEqual(self.mask,set())
            self.assertEqual(len(verifies),3,'failed-no-child permits safe end verification')
            self.assertTrue(all(verifies));self.assertTrue(report['end_identity_check']['passed'])
            record=report['samples'][0]
            self.assertEqual(record['spawn_outcome'],'failed-no-child')
            self.assertFalse(record['spawn_started']);self.assertIsNone(record['pid'])
            self.assertEqual(record['cleanup_status'],'failed-no-child')
            self.assertEqual(report['cleanup_status'],'failed-no-child')
            self.assertIsNone(report['group_empty_verified']);self.assertFalse(report['held_ever'])
            self.assertFalse(report['passed']);self.assertFalse(record['passed'])
            self.assertEqual(record['spawn_error']['errno'],code)
            self.assertEqual(record['spawn_error']['stage'],'posix_spawn')
            pinned=dict(record['primary_error']);spawn_error=dict(record['spawn_error'])
            for _ in range(140):b.note_error(record,'SIMULATED later rotating diagnostic')
            b.pin_primary(record,ValueError('must not replace primary'),'later')
            b.process_checkpoint(self.output/'base-cold-target',record)
            disk=json.loads((self.output/'base-cold-target/process.json').read_bytes())
            self.assertEqual(disk['primary_error'],pinned);self.assertEqual(disk['spawn_error'],spawn_error)
            self.assertIn('SIMULATED PRIMARY',pinned['text'])

    def test_r5_later_oserror_keeps_returned_pid_and_owned_cleanup(self):
        with self.kernel_world() as state:
            real=b.proc_stat;reads=[]
            def stat(pid):
                reads.append(pid)
                if len(reads)==1:raise OSError(errno.EIO,'SIMULATED later proc error')
                return real(pid)
            with patch.object(b,'proc_stat',stat):
                record=self.execute_world(state)
        self.assertEqual(record['pid'],31337)
        self.assertTrue(record['spawn_started']);self.assertEqual(record['spawn_outcome'],'child-pid-returned')
        self.assertIsNone(record['spawn_error']);self.assertEqual(record['primary_error']['errno'],errno.EIO)
        self.assertTrue(record['group_empty_verified']);self.assertTrue(state['reaped'])
        self.assertIn('kill',state['events'])

    def _r5_no_pid_hold(self, mode='actual-unknown', rounds=140):
        actual=b.execute;lock={'held':False,'released':False};seen=[];records=[];spawn_marker=[]
        @contextmanager
        def quiet():
            lock['held']=True
            try:yield
            finally:lock.update(held=False,released=True)
        def spawn(*a,**kw):
            spawn_marker.append(records[0].get('spawn_started',False))
            raise MemoryError('SIMULATED IMMUTABLE PRIMARY unknown spawn outcome')
        def executor(*args,outcome):
            records.append(outcome)
            if mode=='actual-unknown':return actual(*args,outcome=outcome)
            if mode=='marker-only':outcome['spawn_started']=True
            if mode=='journal-only':
                b.process_checkpoint(args[3],b.initialize_outcome(dict(pid=None,pgid=None)))
            raise MemoryError('SIMULATED IMMUTABLE PRIMARY run-boundary failure')
        def sleep(seconds):
            record=records[0]
            seen.append((seconds,lock['held'],b.STOP_SIGNALS <= self.mask,record.get('cleanup_status')))
            b.note_error(record,'SIMULATED rotating unknown diagnostic')
            if len(seen)==3:self.pending |= b.STOP_SIGNALS
            # MOCK-ONLY forced loop exit, not an actual absence proof or production release interface.
            # Preserve unknown spawn/status so the report must never claim verified/not_observed.
            if len(seen)>=rounds:record['group_empty_verified']=True
        with patch.object(os,'posix_spawn',spawn),patch.object(os,'chdir',lambda *a:None), \
                patch.object(os,'waitid') as waitid,patch.object(os,'waitpid') as reap, \
                patch.object(os,'killpg') as kill,patch.object(b,'cleanup_round') as cleanup, \
                patch.object(b.time,'sleep',sleep):
            report=self.mock_run(executor=executor,caller_outcome=True,lock=quiet)
        for mock in (waitid,reap,kill,cleanup):mock.assert_not_called()
        self.assertEqual(len(seen),rounds,'bounded HOLD witness was never reached')
        self.assertEqual(seen,[(1,True,True,'spawn-outcome-unknown-held')]*rounds)
        self.assertTrue(lock['released']);self.assertEqual(self.mask,set())
        self.assertTrue(report['held_ever']);self.assertFalse(report['passed'])
        self.assertFalse(report['group_empty_verified'])
        self.assertEqual(report['cleanup_status'],'spawn-outcome-unknown-held')
        self.assertFalse(report['end_identity_check']['complete'])
        record=records[0]
        self.assertIn('SIMULATED IMMUTABLE PRIMARY',record['primary_error']['text'])
        if rounds>128:
            self.assertEqual(len(record['errors']),128)
            self.assertGreater(record['errors_dropped'],0)
        disk=json.loads((self.output/'base-cold-target/process.json').read_bytes())
        self.assertEqual(disk['primary_error'],record['primary_error'])
        self.assertEqual(disk['cleanup_status'],'spawn-outcome-unknown-held')
        disk_report=json.loads((self.output/'report.json').read_bytes())
        self.assertEqual(disk_report['samples'][0]['primary_error'],record['primary_error'])
        self.assertEqual(disk_report['cleanup_status'],'spawn-outcome-unknown-held')
        self.assertFalse(disk_report['group_empty_verified'])
        if mode=='actual-unknown':
            self.assertEqual(spawn_marker,[True],'SPW1: marker must be visible at the spawn call')
            self.assertEqual(record['spawn_outcome'],'unknown')
            self.assertEqual(record['primary_error']['type'],'MemoryError')
        return report

    def test_r5_unknown_spawn_pins_primary_and_never_waits_on_none(self):
        self._r5_no_pid_hold()

    def test_r5_run_boundary_marker_only_requires_hold(self):
        self._r5_no_pid_hold('marker-only',rounds=4)

    def test_r5_run_boundary_incomplete_journal_requires_hold(self):
        self._r5_no_pid_hold('journal-only',rounds=4)

    def test_r5_schedule_refuses_reserve_band_before_consumption(self):
        schedule=[('base','cold-target'),('candidate','cold-target')]
        plan=b.schedule_plan(schedule,10,30)
        self.assertGreater(plan['required_s'],328)
        for seconds in (237,238,260,297,298,299,310,328,plan['required_s']):
            with self.subTest(seconds=seconds),patch.object(b,'consume_receipt') as consume,patch.object(b,'verify') as verify:
                with self.assertRaisesRegex(ValueError,'full timeouts'):
                    self.mock_run(seconds=seconds)
                consume.assert_not_called();verify.assert_not_called()
                self.assertFalse(self.output.exists());self.assertEqual(list((self.prep_root/'runs').iterdir()),[])

    def test_r5_admitted_serial_schedule_completes_at_planned_stage_costs(self):
        real_verify=b.verify;real_tree=b.tree
        for warm in (False,True):
            self.output=self.prep_root/'runs'/('planned-warm' if warm else 'planned-cold')
            self.receipt.update(run_id=self.output.name,output=str(self.output));self.launches=[]
            schedule=[(label,mode) for label in ('base','candidate')
                      for mode in (('cold-target','warm-noop') if warm else ('cold-target',))]
            plan=b.schedule_plan(schedule,10,30);verify_calls=[];self.clock.now=1000
            def verify(*a):
                verify_calls.append(self.clock.now);self.clock.tick(30)
                return real_verify(*a)
            def tree(path):
                if Path(path).name.endswith('-target') and any(Path(path).iterdir()):
                    self.clock.tick(b.TARGET_RESERVE_S)
                return real_tree(path)
            def compiler(*a):self.clock.tick(b.PROBE_S);return 'mock rustc\n'
            def inspect(*a):self.clock.tick(b.PROBE_S);return {'SIMULATED_ELF':True}
            def build(*a):
                self.clock.tick(a[5]+b.CLEANUP_S+b.REPORT_RESERVE_S)
                return self.build(*a)
            with patch.object(b,'verify',verify),patch.object(b,'tree',tree):
                report=self.mock_run(executor=build,warm=warm,seconds=plan['required_s']+1,
                                     compiler=compiler,inspect=inspect)
            self.assertTrue(report['passed'],report['errors'])
            self.assertEqual(len(self.launches),len(schedule))
            self.assertEqual(len(verify_calls),len(schedule)+2)
            self.assertEqual(report['admission_plan'],plan)
            self.assertTrue(all(timeout==10 for _,_,timeout in self.launches))

    def test_r5_cleanup_summary_internal_hold_and_no_pid_states(self):
        cases=[(dict(pid=None,spawn_started=True,group_empty_verified=True),'spawn-outcome-unknown-held',False,True),
               (dict(pid=None,spawn_outcome='unknown'),'spawn-outcome-unknown-held',False,True),
               (dict(pid=42,group_empty_verified=True,held_ever=True),'observed',True,True),
               (dict(pid=42,group_empty_verified=False,held_ever=True),'unknown-held',False,True),
               (dict(spawn_outcome='failed-no-child'),'failed-no-child',None,False),
               (dict(pid=None),'not_observed',None,False)]
        for sample,status,empty,held in cases:
            report={'samples':[sample]};b.summarize_cleanup(report)
            self.assertEqual(report['cleanup_status'],status)
            self.assertIs(report['group_empty_verified'],empty);self.assertIs(report['held_ever'],held)

    def test_r5_reconcile_preserves_reaped_proof_and_rejects_conflicting_pid(self):
        sample=self.root/'r5-reconcile';sample.mkdir()
        before=b.initialize_outcome(dict(pid=42,pgid=42,group_empty_verified=True,
                                        cleanup_status='verified-before-leader-reap',meter_returncode=0))
        b.process_checkpoint(sample,b.initialize_outcome(dict(pid=42,pgid=42,group_empty_verified=False)))
        b.reconcile_process(before,sample)
        self.assertTrue(before['group_empty_verified']);self.assertEqual(before['meter_returncode'],0)
        self.assertEqual(before['cleanup_status'],'verified-before-leader-reap')
        caller=b.initialize_outcome(dict(pid=43,pgid=43,group_empty_verified=False))
        with self.assertRaisesRegex(ValueError,'conflicting caller/journal PID'):
            b.reconcile_process(caller,sample)
        self.assertEqual(caller['pid'],43)

    def test_r5_immediate_prespawn_stop_launches_nothing(self):
        with self.kernel_world(live=True) as state,b.orderly_signals():
            budget=b.Budget(3000);real=budget.launch
            def launch(timeout):
                value=real(timeout);state['pending'].add(signal.SIGTERM);return value
            with patch.object(budget,'launch',launch),b.bounded_work(budget):
                result=self.execute_world(state,3000)
        self.assertNotIn('spawn',state['events'],'S12: a stop immediately before spawn must prevent launch')
        self.assertIsNone(result['pid']);self.assertFalse(result.get('spawn_started',False))
        self.assertTrue(any('StopRequested' in e for e in result['errors']))

    def test_r5_hold_pause_baseexception_is_contained_with_bounded_witness(self):
        sample=self.root/'r5-pause';sample.mkdir()
        outcome=b.initialize_outcome(dict(pid=42,pgid=42,group_empty_verified=False))
        calls=[];escaped=[]
        def sleep(seconds):calls.append(seconds);raise KeyboardInterrupt('SIMULATED hold pause failure')
        def cleanup(record,holding=False):
            record.update(group_empty_verified=True,cleanup_status='verified-before-leader-reap')
        with b.blocked_signals(),b.durable_capture(),patch.object(b.time,'sleep',sleep),patch.object(b,'cleanup_round',cleanup):
            try:b.hold_owned(outcome,sample)
            except BaseException as error:escaped.append(type(error).__name__)
        self.assertEqual(escaped,[],'A2c: BaseException from held pause must not escape')
        self.assertEqual(calls,[1]);self.assertTrue(outcome['group_empty_verified'])
        self.assertTrue(any('held pause: KeyboardInterrupt' in e for e in outcome['errors']))

    def test_r5_mock_hold_observer_failure_is_bounded_and_visible(self):
        def observer(state):raise AssertionError('SIMULATED observer invariant failed')
        with self.assertRaisesRegex(AssertionError,'SIMULATED observer invariant failed'):
            with self.kernel_world(escaped=True,hold_observer=observer) as state:
                self.execute_world(state)
        self.assertEqual(state['hold_rounds'],1)
        self.assertTrue(state['mock_failures'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
