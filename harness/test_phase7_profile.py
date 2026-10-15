"""Profiler source/analysis ownership checks; no CLI or profiling campaigns."""
import copy
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import unittest
from unittest import mock

import phase7_profile as profile


class PlanTests(unittest.TestCase):
    def test_fixed_cases_controls_first_and_rust_only_heaptrack(self):
        result = profile.plan(profile.Options())
        self.assertEqual(result['session_count'], 27)
        self.assertEqual(result['resize_signal_policy'],profile.bench.RESIZE_SIGNAL_POLICY)
        self.assertTrue(all(case['resize_signal_policy']==profile.bench.RESIZE_SIGNAL_POLICY for case in result['cases']))
        self.assertEqual({c['profile_case'] for c in result['cases']}, set(profile.CASES))
        self.assertEqual(len(result['controls']), 12)
        for capture in result['captures']:
            if capture['mode'] == 'heaptrack':
                self.assertEqual(capture['artifact'], 'rust-symbolized')
        self.assertEqual(profile.plan(profile.Options(cases=('editor',), modes=('strace',)))['session_count'], 6)
        for setting in ({'budget_seconds': float('nan')}, {'max_sessions': 2},
                        {'cases': ('editor', 'editor')}, {'repetitions': 11}):
            with self.assertRaises(ValueError):
                profile.plan(profile.Options(**setting))

    def test_plan_cli_never_calls_child_boundary(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder, \
             mock.patch.object(profile.p7, 'sample', side_effect=AssertionError('capture')), \
             mock.patch.object(profile.p7, 'execute', side_effect=AssertionError('exec')), \
             mock.patch.object(profile.p7, 'NativeChild', side_effect=AssertionError('processing')):
            path = Path(folder) / 'plan.json'
            self.assertEqual(profile.main(['plan', '--out', str(path), '--case', 'editor', '--profiler', 'perf']), 0)
            self.assertEqual(json.loads(path.read_text())['session_count'], 6)


class ArtifactTests(unittest.TestCase):
    def manifest(self, root):
        config = {}
        for name in profile.ARTIFACTS:
            language = name.split('-')[0]
            binary, meta = root / name, root / (name + '.json')
            binary.write_bytes(name.encode())
            binary.chmod(0o700)
            profile.bench.write_json(meta, {'language': language, 'profile': 'release', 'optimized': True,
                'debug_symbols': name.endswith('symbolized'), 'binary_sha256': profile.bench.sha_file(binary),
                'source_commit': profile.bench.PROVENANCE_COMMIT if language == 'go' else 'a' * 40,
                'source_tree_sha256': 'b' * 64})
            config[name] = {'binary': str(binary), 'metadata': str(meta)}
        path = root / 'artifacts.json'
        profile.bench.write_json(path, config)
        return path, config

    def test_actual_identity_symbolized_provenance_and_source_match(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            path, config = self.manifest(Path(folder))
            result = profile.load_artifacts(path)
            self.assertEqual(result['rust-symbolized']['identity']['sha256'], profile.bench.sha_file(config['rust-symbolized']['binary']))
            meta = Path(config['rust-symbolized']['metadata'])
            data = json.loads(meta.read_text())
            data['source_tree_sha256'] = 'c' * 64
            profile.bench.write_json(meta, data)
            with self.assertRaisesRegex(ValueError, 'sources'):
                profile.load_artifacts(path)
            data['source_tree_sha256'] = 'b' * 64
            data['debug_symbols'] = False
            profile.bench.write_json(meta, data)
            with self.assertRaisesRegex(ValueError, 'symbolized'):
                profile.load_artifacts(path)
            data['debug_symbols'] = True
            profile.bench.write_json(meta, data)
            Path(config['rust-symbolized']['binary']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'provenance'):
                profile.load_artifacts(path)

    def test_root_and_symbolized_json_are_parsed_and_hashed_from_same_read(self):
        for target_kind in ('root', 'symbolized'):
            with self.subTest(target_kind=target_kind), tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
                path, config = self.manifest(Path(folder))
                target = path if target_kind == 'root' else Path(config['rust-symbolized']['metadata'])
                encoded = target.read_bytes()
                original_read = Path.read_bytes
                changed = False
                def read(file):
                    nonlocal changed
                    payload = original_read(file)
                    if file == target and not changed:
                        changed = True
                        staged = Path(folder) / 'new-json'
                        staged.write_bytes(b'{"replacement":true}')
                        staged.replace(target)
                    return payload
                with mock.patch.object(Path, 'read_bytes', read):
                    artifacts, root_identity = profile.load_artifacts(path, with_manifest=True)
                identity = root_identity if target_kind == 'root' else {
                    'path': artifacts['rust-symbolized']['provenance_path'],
                    'sha256': artifacts['rust-symbolized']['provenance_file_sha256'],
                    'size_bytes': artifacts['rust-symbolized']['provenance_size_bytes']}
                self.assertEqual(identity['sha256'], profile.bench.hashlib.sha256(encoded).hexdigest())
                with self.assertRaisesRegex(ValueError, 'JSON evidence identity'):
                    profile.bench.check_snapshot(identity)

    def test_actual_section_preparation_binding_and_missing_symbol_rejection(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            root = Path(folder)
            manifest, _config = self.manifest(root)
            inspector = root / 'readelf'
            inspector.write_bytes(b'not executed')
            def inspect(argv, directory, name, guard):
                out, err = directory / 'sections.stdout', directory / 'sections.stderr'
                out.write_bytes(b'  [ 1] .debug_info PROGBITS\n  [ 2] .debug_line PROGBITS\n  [ 3] .symtab SYMTAB\n')
                err.write_bytes(b'')
                return {'argv': argv, 'status': 'complete', 'returncode': 0, 'cleanup': {'issues': []},
                        'stdout': profile.external.manifest.file_identity(out),
                        'stderr': profile.external.manifest.file_identity(err)}
            with mock.patch.object(profile.bench, 'cached_spawn', return_value={'sha256': 'a' * 64}), \
                 mock.patch.object(profile, 'process_command', side_effect=inspect) as process:
                report = profile.prepare_symbols(manifest, root / 'prepared', readelf=inspector)
            self.assertTrue(report['valid'])
            self.assertEqual(process.call_count, 2)
            self.assertTrue(all(call.args[0][0] == str(inspector.resolve()) and call.args[0][1:3] == ['-SW', '--'] for call in process.call_args_list))
            identities, manifest_identity = profile.load_artifacts(manifest, with_manifest=True)
            path = root / 'prepared/report.json'
            verified = profile.verify_symbol_evidence(identities, manifest_identity, path)
            self.assertEqual(verified['evidence_identity']['sha256'], profile.bench.sha_file(path))
            stdout = Path(report['artifacts']['rust-symbolized']['analysis']['stdout']['path'])
            stdout.write_bytes(b' [ 1] .debug_info PROGBITS\n [ 2] .debug_line PROGBITS\n')
            report['artifacts']['rust-symbolized']['analysis']['stdout'] = profile.external.manifest.file_identity(stdout)
            report['artifacts']['rust-symbolized']['sections'] = ['.debug_info', '.debug_line']
            profile.bench.write_json(path, report)
            with self.assertRaisesRegex(ValueError, 'required ELF sections'):
                profile.verify_symbol_evidence(identities, manifest_identity, path)
            inspector.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'artifact changed'):
                profile.verify_symbol_evidence(identities, manifest_identity, path)

    def test_readelf_failed_or_changed_output_is_retained_not_attested(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            root = Path(folder)
            manifest, _config = self.manifest(root)
            inspector = root / 'readelf'
            inspector.write_bytes(b'not executed')
            with mock.patch.object(profile.bench, 'cached_spawn', return_value={}), \
                 mock.patch.object(profile, 'process_command', side_effect=ValueError('missing symbols')):
                report = profile.prepare_symbols(manifest, root / 'prepared', readelf=inspector)
            self.assertFalse(report['valid'])
            self.assertIn('missing symbols', report['errors'][0])
            self.assertTrue((root / 'prepared/report.json').is_file())
            output = root / 'sections'
            output.write_bytes(b' [ 1] .debug_info PROGBITS\n [ 2] .debug_line PROGBITS\n [ 3] .symtab SYMTAB\n')
            identity = profile.external.manifest.file_identity(output)
            output.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                profile.verified_symbol_output(identity, mock.Mock())

    def test_missing_empty_ambiguous_symlink_profiles_and_missing_tools(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            root = Path(folder)
            with self.assertRaises(ValueError):
                profile.external.artifacts('perf', root)
            (root / 'perf.data').touch()
            with self.assertRaises(ValueError):
                profile.external.artifacts('perf', root)
            (root / 'perf.data').unlink()
            (root / 'perf.data').symlink_to(root / 'missing')
            with self.assertRaises(ValueError):
                profile.external.artifacts('perf', root)
            (root / 'heaptrack.a').write_bytes(b'a')
            (root / 'heaptrack.b').write_bytes(b'b')
            with self.assertRaises(ValueError):
                profile.external.artifacts('heaptrack', root)
            self.assertEqual(profile.tool_identity(None)['status'], 'unavailable')


class CaptureTests(unittest.TestCase):
    def test_real_execute_profiler_path_cannot_build_after_helper_disappears(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            root = Path(folder)
            helper = root / 'prepared-helper'
            helper.write_bytes(b'prepared')
            tool = root / 'perf'
            tool.write_bytes(b'not executed')
            identity = profile.external.manifest.file_identity(tool)
            def resolve(build=True, *, probe=True):
                self.assertFalse(build, 'profiler capture attempted helper compilation')
                if not helper.exists():
                    self.assertFalse(probe, 'profiler capture attempted preparation probe')
                    raise RuntimeError('prepared helper disappeared')
                return {'path': str(helper), 'sha256': profile.bench.sha_file(helper)}
            signature = profile.bench.inspect.signature(resolve)
            with mock.patch.object(profile.p7, 'spawn_helper', side_effect=resolve) as resolver, \
                 mock.patch.object(profile.bench.inspect, 'signature', return_value=signature):
                profile.bench.cached_spawn()
                helper.unlink()
                with mock.patch.object(profile.p7.os, 'openpty', side_effect=AssertionError('PTY acquired')), \
                     mock.patch.object(profile.p7.subprocess, 'check_output', side_effect=AssertionError('compiler probe')), \
                     mock.patch.object(profile.p7.subprocess, 'run', side_effect=AssertionError('build/probe subprocess')):
                    with self.assertRaisesRegex(RuntimeError, 'prepared helper disappeared'):
                        profile.ProfileCapture(mock.Mock(), 'perf', identity, root / 'out', 'cycles:u')(
                            ['never-run', 'sql', '-i'], {}, root, {})
            self.assertEqual(resolver.call_args_list, [mock.call(build=False, probe=True), mock.call(build=False, probe=False)])

    def test_external_diagnostic_fd_separation_exact_argv_and_scope(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            root = Path(folder)
            tool = root / 'tool with spaces'
            tool.write_bytes(b'not executed')
            identity = profile.external.manifest.file_identity(tool)
            guard = mock.Mock()
            directory = root / 'capture folder'
            with mock.patch.object(profile.p7, 'execute', return_value={'metrics': {}, 'stdout': b'', 'stderr': b'', 'post_screen_stdout': b''}) as execute:
                adapter = profile.ProfileCapture(guard, 'perf', identity, directory, 'cycles:u')
                result = adapter(['/native binary', 'literal $(never)', 'space argument'], {'TERM': 'xterm'}, root, {})
            argv = execute.call_args.args[0]
            self.assertEqual(argv[-3:], ['/native binary', 'literal $(never)', 'space argument'])
            launcher = Path(argv[0]).read_text()
            self.assertIn('exec 3>&2', launcher)
            self.assertIn('exec 2>', launcher)
            self.assertTrue(any(arg.startswith('exec 2>&3; exec 3>&-;') for arg in argv))
            self.assertEqual(execute.call_args.kwargs['continue_running'], adapter.continue_capture)
            self.assertFalse(execute.call_args.kwargs['spawn_helper_build'])
            self.assertEqual(execute.call_args.kwargs['resize_signal_policy'],profile.bench.RESIZE_SIGNAL_POLICY)
            self.assertIsInstance(execute.call_args.kwargs['readiness_fn'], profile.bench.Readiness)
            self.assertFalse(result['metrics']['release_claim'])
            self.assertIn('overhead', result['metrics']['scope'])
            self.assertEqual(result['profile']['actual_cli_command'][0], '/native binary')
            self.assertEqual(result['profile']['profiler_identity']['sha256'], identity['sha256'])

    def test_heaptrack_targets_only_actual_rust_and_never_strips_stderr(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            root = Path(folder)
            library = root / 'preload.so'
            library.write_bytes(b'not loaded')
            identity = profile.external.manifest.file_identity(library)
            with mock.patch.object(profile.p7, 'execute', return_value={'metrics': {}, 'stdout': b'', 'stderr': b'unknown diagnostic', 'post_screen_stdout': b''}) as execute:
                result = profile.ProfileCapture(mock.Mock(), 'heaptrack', identity, root / 'out', 'cycles:u')(
                    ['/rust', 'sql', '-i'], {}, root, {})
            self.assertEqual(execute.call_args.args[0][-3:], ['/rust', 'sql', '-i'])
            self.assertEqual(execute.call_args.kwargs['resize_signal_policy'],profile.bench.RESIZE_SIGNAL_POLICY)
            self.assertNotIn('/bin/sh', execute.call_args.args[0])
            self.assertEqual(result['stderr'], b'unknown diagnostic')
            self.assertIn('no filtering', result['profile']['diagnostics']['policy'])


    def test_instrumented_resize_policy_is_explicit_and_legacy_failure_retains_raw(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            root=Path(folder);tool=root/'tool';tool.write_bytes(b'never executed')
            identity=profile.external.manifest.file_identity(tool)
            case={'size':[80,30],'resize_signal_policy':profile.bench.RESIZE_SIGNAL_POLICY,
                  'actions':[{'resize':[100,35]}]}
            for policy in (profile.bench.RESIZE_SIGNAL_POLICY,'legacy'):
                raw={'resize_signal_policy':policy,'metrics':{},'failure':None,'stdout':b'raw instrumented bytes',
                     'stderr':b'exact CLI stderr','post_screen_stdout':b'',
                     'actions':[{'index':0,'resize':[100,35],'resize_signal_source':'ioctl-only'}]}
                with mock.patch.object(profile.p7,'execute',return_value=raw) as execute:
                    result=profile.ProfileCapture(mock.Mock(),'perf',identity,root/policy,'cycles:u')(
                        ['/never-run','sql','-i'],{},root,case)
                self.assertEqual(execute.call_args.kwargs['resize_signal_policy'],profile.bench.RESIZE_SIGNAL_POLICY)
                self.assertFalse(execute.call_args.kwargs['spawn_helper_build'])
                self.assertEqual((root/policy/'capture/execute/stdout.bin').read_bytes(),raw['stdout'])
                self.assertEqual(result['stderr'],b'exact CLI stderr')
                if policy=='legacy':self.assertIn('legacy cannot enable triggers',result['failure'])
                else:self.assertIsNone(result['failure'])


class ProcessingTests(unittest.TestCase):
    def test_owned_analysis_child_leader_exits_and_group_child_is_killed_reaped(self):
        # This bounded analysis-only process fixture uses the parent's frozen
        # prepared ELF/proof. No compiler, capability probe, CLI or profiler.
        freeze = Path('/home/ajs/.cache/pb-phase7/harness-nobuild-integration-freeze.json')
        if not freeze.is_file():
            self.skipTest('parent prepared helper freeze unavailable; actual analysis check not run')
        helper = json.loads(freeze.read_text())['helper']
        self.assertEqual(profile.bench.sha_file(helper['path']), helper['sha256'])
        self.assertEqual(profile.bench.sha_file(helper['provenance_path']), helper['provenance_sha256'])
        libc = ctypes.CDLL(None, use_errno=True)
        original = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(original), 0, 0, 0), 0)  # GET_CHILD_SUBREAPER
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        child_pid = None
        try:
            with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
                root = Path(folder)
                # Worker belongs to the leader's native session/group. The
                # leader returns immediately; the worker would sleep 2 s.
                program = ('import os,time\n'
                           'child=os.fork()\n'
                           'if child==0: time.sleep(2);os._exit(0)\n'
                           'print(child,flush=True)\n')
                with mock.patch.object(profile.bench, 'cached_spawn', return_value=helper):
                    record = profile.process_command([sys.executable, '-c', program], root, 'owned-child',
                                                     profile.bench.FunctionalGuard(5))
                child_pid = int((root / 'owned-child.stdout').read_text().strip())
                self.assertEqual(record['cleanup']['remaining'], [])
                self.assertEqual(record['cleanup']['issues'], [])
                deadline = time.monotonic() + 2
                while True:
                    got, status = os.waitpid(child_pid, os.WNOHANG)
                    if got:
                        break
                    self.assertLess(time.monotonic(), deadline, 'analysis child not reaped within bound')
                    time.sleep(.01)
                self.assertEqual(os.waitstatus_to_exitcode(status), -profile.signal.SIGKILL)
                child_pid = None
        finally:
            if child_pid is not None:
                # PID-bound cleanup even if the test assertion fails.
                descriptor = os.pidfd_open(child_pid)
                try:
                    profile.signal.pidfd_send_signal(descriptor, profile.signal.SIGKILL)
                finally:
                    os.close(descriptor)
                os.waitpid(child_pid, 0)
            self.assertEqual(libc.prctl(36, original.value, 0, 0, 0), 0)

    def native(self):
        child = mock.Mock()
        child.exec_error = None
        child.poll.return_value = {'event': 'exited'}
        child.signal_group.return_value = True
        child.wait.return_value = {'status': 0}
        child.close.return_value = []
        return child

    def test_active_guard_failure_kills_reaps_and_keeps_processing_invocation(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            guard, child = mock.Mock(), self.native()
            guard.check.side_effect = [None, RuntimeError('quiet expiry')]
            with mock.patch.object(profile.bench, 'cached_spawn', return_value={}), \
                 mock.patch.object(profile.p7, 'NativeChild', return_value=child), \
                 mock.patch.object(profile, 'group_members', return_value=[]), \
                 mock.patch.object(profile.selectors, 'DefaultSelector'), \
                 self.assertRaisesRegex(RuntimeError, 'quiet expiry'):
                profile.process_command(['/tool', 'report'], Path(folder), 'report', guard)
            child.signal_group.assert_called_once_with(profile.signal.SIGKILL)
            child.release.assert_called_once()
            child.wait.assert_called_once()
            child.close.assert_called_once()
            evidence = json.loads((Path(folder) / 'report.json').read_text())
            self.assertEqual(evidence['status'], 'failed')
            self.assertEqual(evidence['argv'], ['/tool', 'report'])
            self.assertTrue((Path(folder) / 'report.stderr').is_file())
            self.assertEqual(evidence['cleanup']['issues'], [])

    def test_exited_leader_still_cleans_group_before_release_and_retains_late_failure(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            guard, child = mock.Mock(), self.native()
            guard.check.side_effect = [None, None, RuntimeError('expired after leader exit')]
            with mock.patch.object(profile.bench, 'cached_spawn', return_value={}), \
                 mock.patch.object(profile.p7, 'NativeChild', return_value=child) as native, \
                 mock.patch.object(profile, 'group_members', side_effect=[[{'pid': 222, 'state': 'S'}], []]), \
                 mock.patch.object(profile.selectors, 'DefaultSelector'):
                native.CLEANUP_TIMEOUT = 2
                with self.assertRaisesRegex(RuntimeError, 'expired after leader exit'):
                    profile.process_command(['/tool'], Path(folder), 'report', guard)
            names = [call[0] for call in child.mock_calls]
            self.assertLess(names.index('signal_group'), names.index('release'))
            self.assertLess(names.index('release'), names.index('wait'))
            child.signal_group.assert_called_once_with(profile.signal.SIGKILL)
            record = json.loads((Path(folder) / 'report.json').read_text())
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['cleanup']['remaining'], [])

    def test_failed_empty_analysis_is_not_zero_or_success(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            child = self.native()
            with mock.patch.object(profile.bench, 'cached_spawn', return_value={}), \
                 mock.patch.object(profile.p7, 'NativeChild', return_value=child), \
                 mock.patch.object(profile, 'group_members', return_value=[]), \
                 mock.patch.object(profile.selectors, 'DefaultSelector'), \
                 self.assertRaisesRegex(ValueError, 'empty'):
                profile.process_command(['/tool'], Path(folder), 'empty', mock.Mock())
            self.assertEqual(json.loads((Path(folder) / 'empty.json').read_text())['status'], 'failed')

    def test_surviving_group_verification_times_out_with_retained_cleanup_failure(self):
        child = self.native()
        with mock.patch.object(profile, 'group_members', return_value=[{'pid': 222, 'state': 'S'}]), \
             mock.patch.object(profile.time, 'monotonic', side_effect=[0, 3]), \
             mock.patch.object(profile.selectors, 'DefaultSelector'):
            record = profile.cleanup_analysis(child)
        self.assertTrue(record['issues'])
        self.assertEqual(record['remaining'][0]['pid'], 222)
        child.close.assert_called_once_with(abort=True)

    def test_bounded_write_parser_matches_existing_semantics_and_retains_partial_cancel(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            root, guard = Path(folder), mock.Mock()
            trace = root / 'writes.trace'
            trace.write_text('101 write(1, 0xab, 5) = 5\n102 writev(1, 0xabc, 2 <unfinished ...>\n102 <... writev resumed>) = 0x3\n101 write(2, 0xab, 4) = -1 EIO (error)\n')
            parsed = profile.write_counts_bounded(trace, root, guard)
            self.assertTrue(all(row['interrupted'] == 0 for row in parsed.values()))
            self.assertEqual({fd: {key: value for key, value in row.items() if key != 'interrupted'}
                              for fd, row in parsed.items()}, profile.external.write_counts(trace))
            guard.check.side_effect = [None, RuntimeError('expired during parser')]
            with self.assertRaisesRegex(RuntimeError, 'expired during parser'):
                profile.write_counts_bounded(trace, root, guard)
            record = json.loads((root / 'write-analysis.json').read_text())
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['lines'], 1)
            self.assertEqual(record['writes_by_fd']['1']['bytes'], 5)

    def test_interrupted_writes_retain_separate_count_and_no_attributed_bytes(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            root = Path(folder)
            trace = root / 'writes.trace'
            trace.write_text('101 write(1, 0xab, 5) = ? ERESTARTSYS (restart)\n'
                             '102 writev(1, 0xab, 2 <unfinished ...>\n'
                             '102 <... writev resumed>) = ? ERESTARTSYS (restart)\n'
                             '101 --- SIGWINCH {si_signo=SIGWINCH} ---\n'
                             '101 write(1, 0xab, 5) = 5\n')
            counts = profile.write_counts_bounded(trace, root, mock.Mock())
            self.assertEqual(counts['1'], {'calls': 3, 'completed': 1, 'bytes': 5, 'errors': 0, 'interrupted': 2})
            diagnostic = profile.write_byte_diagnostic(counts, {'metrics': {'stdout_bytes': 6},
                'termios_before': {'oflag': profile.termios.OPOST | profile.termios.ONLCR}})
            self.assertEqual(diagnostic['captured_minus_traced'], 1)
            self.assertEqual(diagnostic['interrupted_fd1_calls'], 2)
            self.assertTrue(diagnostic['observed_opost_onlcr'])
            self.assertIn('Exact equality remains required', diagnostic['scope'])
            trace.write_text('101 write(1, 0xab, 5) = unknown\n')
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                profile.write_counts_bounded(trace, root, mock.Mock())

    def test_analysis_byte_and_line_caps_and_hash_cancellation(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            root, guard = Path(folder), mock.Mock()
            artifact = root / 'large'
            artifact.write_bytes(b'a' * 4096)
            with self.assertRaisesRegex(ValueError, 'oversized'):
                profile.bounded_identity(artifact, guard, maximum=1024)
            guard.check.side_effect = [None, RuntimeError('expired while hashing')]
            with self.assertRaisesRegex(RuntimeError, 'expired while hashing'):
                profile.bounded_identity(artifact, guard)
            artifact.write_bytes(b'a' * (profile.MAX_TRACE_LINE_BYTES + 1))
            with self.assertRaisesRegex(ValueError, 'cap'):
                profile.write_counts_bounded(artifact, root, mock.Mock())
            self.assertEqual(json.loads((root / 'write-analysis.json').read_text())['status'], 'failed')


class CampaignTests(unittest.TestCase):
    def run_mock(self, folder, *, contract_failure=False, missing_artifact=False, unavailable=False, processing_failure=False,
                 source_drift=False, helper_drift=False, metadata_drift=False, bad_return=False, root_drift=False, tool_drift=False):
        root = Path(folder)
        binary = root / 'binary'
        binary.write_bytes(b'not executed')
        binary.chmod(0o700)
        identities = {name: {'identity': profile.p7.phase1.binary_identity(binary)} for name in profile.ARTIFACTS}
        metadata = root / 'metadata.json'
        metadata.write_text('{}')
        manifest = root / 'manifest.json'
        manifest.write_text('{}')
        for artifact in identities.values():
            artifact.update(provenance_path=str(metadata), provenance_file_sha256=profile.bench.sha_file(metadata))
        definition = {'cases': [{'name': 'editor', 'profile_case': 'editor', 'argv': ['sql', '-i'],
                                'resize_signal_policy': profile.bench.RESIZE_SIGNAL_POLICY,
                                'bench': {'observer_policy': 'resource-attribution'}}],
                      'controls': [{'case': 'editor', 'repetition': 0, 'mode': 'control', 'artifact': name} for name in profile.ARTIFACTS],
                      'captures': [{'case': 'editor', 'repetition': 0, 'mode': 'perf', 'artifact': 'rust-symbolized'}]}
        tool = root / 'perf'
        tool.write_bytes(b'not executed')
        inspector = root / 'readelf'
        inspector.write_bytes(b'not executed')
        evidence = root / 'symbol-evidence.json'
        evidence.write_text('{}')
        symbols = {'readelf': profile.external.manifest.file_identity(inspector),
                   'evidence_identity': profile.bench.json_snapshot(evidence)[1]}
        toolset = {'perf': {'status': 'unavailable', 'reason': 'not installed'} if unavailable else
                          {'status': 'available', 'identity': profile.external.manifest.file_identity(tool)}}
        guard = mock.MagicMock()
        guard.__enter__.return_value = guard
        guard.approval = {'approved': True}
        events = []
        helper_calls = 0
        def helper(*, probe=True):
            nonlocal helper_calls
            helper_calls += 1
            return {'source_sha256': 'a' * 64, 'sha256': ('c' if helper_drift and helper_calls > 6 else 'b') * 64}
        def sample(_binary, case, path, *, execute_fn):
            path.mkdir(parents=True)
            profile.bench.write_json(path / 'session.json', {'raw_failure_retained': True})
            events.append('profile' if isinstance(execute_fn, profile.ProfileCapture) else 'control')
            if isinstance(execute_fn, profile.ProfileCapture):
                if not missing_artifact:
                    (path.parent / 'perf.data').write_bytes(b'raw profile')
                (path.parent / 'profiler.stderr').write_bytes(b'permission denied' if missing_artifact else b'profiler warning')
                if metadata_drift:
                    metadata.write_text('{"changed":true}')
                if root_drift:
                    manifest.write_text('{"changed":true}')
                if tool_drift:
                    tool.write_bytes(b'changed after actual use')
            identity = profile.p7.phase1.binary_identity(binary)
            if bad_return:
                identity['bytes'] += 1
            return {'binary': identity, 'case': case,
                    'resize_signal_policy': profile.bench.RESIZE_SIGNAL_POLICY,
                    'capture_command': [str(binary), *case['argv']], 'observer_policy': 'resource-attribution',
                    'metrics': {'stdout_bytes': 10, 'native_spawn': {'source_sha256': 'a' * 64, 'sha256': 'b' * 64}},
                    'profile': {'command': ['actual binary'], 'mode': 'perf'}}
        def analyze(*args, **kwargs):
            events.append('analysis')
            if processing_failure:
                raise ValueError('invalid decoder output')
            return {'decoded': True}
        with mock.patch.object(profile, 'plan', return_value=definition), \
             mock.patch.object(profile, 'load_artifacts', return_value=(identities, profile.bench.json_snapshot(manifest)[1])), \
             mock.patch.object(profile.bench, 'cached_spawn', side_effect=helper), \
             mock.patch.object(profile, 'tools', return_value=toolset), \
             mock.patch.object(profile, 'verify_symbol_evidence', return_value=symbols), \
             mock.patch.object(profile.bench, 'QuietWindow', return_value=guard), \
             mock.patch.object(profile.bench, 'source_snapshot', side_effect=[{}, {'changed': True}] if source_drift else None, return_value={}), \
             mock.patch.object(profile.bench, 'host_snapshot', return_value={}), \
             mock.patch.object(profile.bench, 'observation_issues', return_value=[]), \
             mock.patch.object(profile.p7, 'sample', side_effect=sample, autospec=True), \
             mock.patch.object(profile.p7, 'compare', return_value=['styled mismatch'] if contract_failure else []), \
             mock.patch.object(profile, 'analyze', side_effect=analyze):
            report = profile.run_campaign('manifest', root / 'out', 'approval', profile.Options(cases=('editor',), modes=('perf',), campaign_id='profile-unit'),
                                           preload='missing', interpreter='missing')
        guard.__exit__.assert_called_once()
        saved = json.loads((root / 'out/report.json').read_text())
        self.assertEqual(saved['status'], report['status'])
        return report, events

    def test_all_controls_before_profile_before_processing(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            report, events = self.run_mock(folder)
        self.assertEqual(events, ['control'] * 4 + ['profile', 'analysis'])
        self.assertTrue(report['valid'])
        self.assertEqual(report['used_tools'], ['perf'])
        self.assertEqual(report['tools_after']['perf'], report['tools']['perf']['identity'])
        self.assertEqual(report['samples'][-1]['profiler_diagnostics']['size_bytes'], len(b'profiler warning'))
        self.assertEqual(report['samples'][-1]['instrumentation']['command'], ['actual binary'])

    def test_contract_failure_stops_dependent_profiles_and_keeps_raw(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            report, events = self.run_mock(folder, contract_failure=True)
            self.assertTrue((Path(folder) / 'out' / report['samples'][-1]['directory'] / 'capture/session.json').is_file())
        self.assertEqual(report['status'], 'failed')
        self.assertNotIn('profile', events)
        self.assertTrue(report['unattempted'])
        self.assertIsNone(report['samples'][-1]['measurements'])

    def test_missing_denied_artifact_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
            report, events = self.run_mock(folder, missing_artifact=True)
            row = report['samples'][-1]
            self.assertTrue((Path(folder) / 'out' / row['directory'] / 'capture/session.json').is_file())
        self.assertEqual(row['status'], 'unavailable')
        self.assertIsNone(row['measurements'])
        self.assertEqual(row['instrumentation']['command'], ['actual binary'])
        self.assertNotIn('analysis', events)

    def test_missing_tool_and_processing_failure_are_recorded(self):
        for settings in ({'unavailable': True}, {'processing_failure': True}):
            with self.subTest(settings=settings), tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
                report, events = self.run_mock(folder, **settings)
                self.assertFalse(report['valid'])
                self.assertEqual(report['status'], 'incomplete')
                self.assertTrue(report['samples'][-1]['issues'])
                if settings.get('unavailable'):
                    self.assertNotIn('profile', events)

    def test_source_helper_metadata_and_returned_identity_drift_fail_and_retain(self):
        for flag in ('source_drift', 'helper_drift', 'metadata_drift', 'bad_return', 'root_drift', 'tool_drift'):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory(dir=profile.p7.TMP) as folder:
                report, _events = self.run_mock(folder, **{flag: True})
                self.assertFalse(report['valid'])
                self.assertEqual(report['status'], 'failed')
                self.assertIn('source_after', report)
                self.assertIn('native_spawn_after', report)
                self.assertIn('metadata_after', report)
                self.assertTrue((Path(folder) / 'out' / report['samples'][0]['directory'] / 'capture/session.json').is_file())


if __name__ == '__main__':
    unittest.main()
