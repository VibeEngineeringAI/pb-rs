"""Profile adapter admission, provenance, gating, deadline and evidence-preservation tests.

Pure mocks. Every process, spawn, exec, signal-send, lock and socket entry point (and prlimit) is
replaced with a hard failure BEFORE any reviewed module is imported; tests then opt in to individual
simulated fakes, and setrlimit is recorded, never applied. Run this module on its own: the guards are
process-wide.
"""
import _posixsubprocess
import _signal
import fcntl
import os
import posix
import resource
import signal
import socket
import ssl
import subprocess


def _refuse(name):
    def refuse(*args, **kwargs):
        raise AssertionError('GUARD: ' + name + ' called in a pure mock test')
    return refuse


ORIGINALS = {}
for _module, _names in (
        (subprocess, ('Popen', 'run', 'call', 'check_call', 'check_output', 'getoutput', 'getstatusoutput',
                      '_fork_exec')),
        (_posixsubprocess, ('fork_exec',)),
        (os, ('system', 'popen', 'fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'execv', 'execve', 'execvp',
              'execvpe', 'execl', 'execle', 'execlp', 'execlpe', 'spawnv', 'spawnve', 'spawnvp', 'spawnvpe',
              'spawnl', 'spawnle', 'fexecve', 'kill', 'killpg', 'wait', 'waitpid', 'wait3', 'wait4', 'waitid',
              'pidfd_open')),
        (posix, ('system', 'fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'execv', 'execve', 'fexecve',
                 'kill', 'killpg', 'wait', 'waitpid', 'wait3', 'wait4', 'waitid', 'pidfd_open')),
        (signal, ('pthread_kill', 'raise_signal', 'pidfd_send_signal', 'alarm', 'setitimer')),
        (_signal, ('pthread_kill', 'raise_signal', 'pidfd_send_signal', 'alarm', 'setitimer')),
        (fcntl, ('flock', 'lockf')), (resource, ('prlimit',)),
        (socket.socket, ('connect', 'connect_ex', 'bind', 'listen', 'send', 'sendall', 'sendto', 'sendmsg',
                         'sendfile')),
        (socket, ('create_connection', 'create_server')), (ssl, ('create_connection',))):
    for _name in _names:
        if hasattr(_module, _name):
            ORIGINALS[(_module, _name)] = getattr(_module, _name)
            setattr(_module, _name, _refuse(getattr(_module, '__name__', 'socket') + '.' + _name))
_LOCK_COMMANDS = {getattr(fcntl, name) for name in ('F_SETLK', 'F_SETLKW', 'F_OFD_SETLK', 'F_OFD_SETLKW')
                  if hasattr(fcntl, name)}
ORIGINALS[(fcntl, 'fcntl')] = fcntl.fcntl


def _fcntl(fd, command, arg=0):
    if command in _LOCK_COMMANDS:
        raise AssertionError('GUARD: fcntl.fcntl lock command called in a pure mock test')
    return ORIGINALS[(fcntl, 'fcntl')](fd, command, arg)


fcntl.fcntl = _fcntl

import contextlib  # noqa: E402
import hashlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from unittest.mock import patch  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import phase6_profiles as prof  # noqa: E402
import phase6_workloads as p6  # noqa: E402


def tearDownModule():
    for (module, name), value in ORIGINALS.items():
        setattr(module, name, value)


CONTRACT = {key: 'same' for key in p6.COMPARABLE_FIELDS}
SECTIONS = b'  [1] .debug_info P\n  [2] .debug_line P\n  [3] .symtab S\n'
SYMBOLS = (b"Symbol table '.symtab' contains 3 entries:\n   Num:    Value          Size Type    Bind\n"
           b'     1: 0000000000001000    10 FUNC    GLOBAL DEFAULT   16 main\n'
           b'     2: 0000000000002000 0x186a0 FUNC    LOCAL  DEFAULT   16 big\n')
ARTIFACTS = {'perf': ('perf.data', b'perf'), 'strace': ('strace.txt', b'% time\n'),
             'strace-writes': ('writes.trace', b'7 write(0x1, 0xaa, 0x4) = 0x4\n'),
             'heaptrack': ('heaptrack.raw', b'raw')}


def perf_stats_text(sample, lost=0, lost_samples=0, skip_empty=False, aux=False):
    """Shaped after perf's `%20s events: %10d  (%4.1f%%)` aggregated rows and bare per-event rows.

    Derived from perf source formatting, not observed on this host; a later authorized
    functional probe must confirm the real output.
    """
    rows = [('TOTAL', sample + lost + 10), ('MMAP', 10), ('SAMPLE', sample), ('LOST', lost),
            ('LOST_SAMPLES', lost_samples)]
    lines = ['', 'Aggregated stats:' + (' (excludes AUX area (e.g. instruction trace) decoded / '
                                       'synthesized events)' if aux else '')]
    lines += ['%20s events: %10d  (%4.1f%%)' % (name, count, 100.0 * count / rows[0][1])
              for name, count in rows if count or not skip_empty]
    # The per-event block deliberately disagrees, proving it is never substituted.
    lines += ['cycles:u stats:', '%20s events: %10d' % ('SAMPLE', sample + 999), '%20s events: %10d' % ('LOST', 0)]
    return '\n'.join(lines) + '\n'


@contextlib.contextmanager
def quiet():
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink):
        yield


class Clock:
    """Simulated monotonic clock: real time plus an offset tests advance."""
    def __init__(self):
        self.offset, self.real = 0, p6.clock_ns

    def __call__(self):
        return self.real() + self.offset


def tracking_lock(events):
    @contextlib.contextmanager
    def lock(*args):
        events.append('lock-enter')
        try:
            yield
        finally:
            events.append('lock-exit')
    return lock


class Profiles(unittest.TestCase):
    def setUp(self):
        p6.TMP.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='p6-profiles-test-', dir=p6.TMP)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.calls, self.probes, self.missing, self.contracts, self.limits = [], [], set(), {}, []
        self.fsize = None
        self.paranoid = self.root / 'paranoid'
        self.paranoid.write_text('2\n')
        for target, name, value in ((p6, 'run_sample', _refuse('run_sample')),
                                    (p6.run, 'spawn_measured', _refuse('spawn_measured')),
                                    (p6, 'quiet_lock', lambda *a: contextlib.nullcontext()),
                                    (prof, 'offline_lock', contextlib.nullcontext),
                                    (resource, 'setrlimit', self.setrlimit),
                                    (prof, 'PERF_PARANOID', self.paranoid),
                                    (prof.os, 'statvfs', lambda path: SimpleNamespace(f_bavail=1 << 40, f_frsize=1))):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sidecar = self.build()

    def setrlimit(self, kind, value):
        """Recorded, never applied: the test process limits stay untouched."""
        self.limits.append((kind, value))
        if kind == resource.RLIMIT_FSIZE:
            self.fsize = value[0]

    def file(self, name, data, mode=0o444):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        path.chmod(mode)
        return path

    def json(self, name, value):
        return self.file(name, json.dumps(value), 0o644)

    def build(self, rust_sources=None, go_commit='oracle'):
        previous, self.base = getattr(self, 'base', None), self.root / f'b{len(list(self.root.glob("b*")))}'
        exe = lambda name: self.file(name, '#!/bin/false\n' + name, 0o555)
        sources, paths = {}, {}
        for name in ('main.go', 'phase6.go', 'go.mod', 'go.sum'):
            paths[name] = str(self.file('fx/' + name, name))
            sources[name] = p6.file_info(paths[name])['sha256']
        toolchain = exe('fx/go')
        build = dict(binary=p6.file_info(exe('fx/fixture')), sources=sources, source_paths=paths,
                     toolchain=dict(path=str(toolchain), sha256=p6.file_info(toolchain)['sha256'], version='go1.25.4'))
        go, rust, meter = exe('base/pb-go'), exe('cand/pb'), exe('meter/pb-meter')
        normal = dict(p6.file_info(rust), argv=list(p6.NORMAL_RELEASE_ARGV), features=[],
                      cloud_synthetic_token_enabled=False, build_kind='normal_release')
        candidate = self.json('cand/manifest.json', dict(builds=dict(pb=normal)))
        candidate_sources, rustc = {'src/main.rs': 'abc'}, 'rustc 1.96.0\n'
        parent = self.json('parent/manifest.json', dict(
            sources=dict(oracle='oracle', candidate=candidate_sources), dependencies={'locked': True},
            toolchains=dict(rustc=rustc, oracle_embedded_build=f'{go}: go1.25.4\n\tpath x'),
            build_flags=dict(candidate=dict(pb=normal)), baselines={'pb-go': p6.file_info(go)},
            meter=p6.file_info(meter), candidate_manifest=p6.file_info(candidate),
            budgets=p6.read_json(p6.PREDECLARED_BUDGETS), budgets_sha256=p6.file_info(p6.PREDECLARED_BUDGETS)['sha256'],
            fixture_manifest=p6.file_info(self.json('fx/manifest.json', build)), fixture_build=build))
        rsym, gsym = exe('rsym/pb-symbolized'), exe('gsym/pb-go-symbolized')
        rust_build = self.json('rsym/manifest.json', dict(
            binary=p6.file_info(rsym), candidate_manifest=p6.file_info(candidate), argv=prof.RUST_SYMBOLIZED_ARGV,
            sources=rust_sources or candidate_sources, features=[], debug_symbols=True, rustc=rustc,
            build_log=p6.file_info(self.file('rsym/build.log', 'Finished')),
            sections=p6.file_info(self.file('rsym/sections.txt', SECTIONS))))
        provenance = dict(binary_sha256=p6.file_info(gsym)['sha256'], optimized=True, language='go',
                          source_commit=go_commit, source_tree_sha256='tree', compiler='go1.25.4 linux/amd64')
        source = self.json('gsym/provenance.json', provenance)
        go_build = self.json('gsym/manifest.json', dict(binary=p6.file_info(gsym), source_provenance=p6.file_info(source),
                                                        provenance=provenance))
        release = self.json('gsym/release.json', dict(binary_sha256=p6.file_info(go)['sha256'],
                                                      source_tree_sha256='tree', source_commit=go_commit))
        aliases = {}
        for name in prof.TOOL_ALIASES:
            if name == 'env':
                aliases[name] = '/usr/bin/env'  # profile_external.command hardcodes it; hashed, never run
                continue
            real = self.file(f'tools/{name}-real', name, 0o555)
            (self.base / 'alias').mkdir(exist_ok=True)
            (self.base / 'alias' / name).symlink_to(real)
            aliases[name] = str(self.base / 'alias' / name)
        args = SimpleNamespace(out=self.base / 'sidecar.json', parent_manifest=parent,
                               parent_sha256=p6.file_info(parent)['sha256'], rust_build_manifest=rust_build,
                               go_build_manifest=go_build, go_sections=self.file('gsym/sections.txt', SECTIONS),
                               go_release_provenance=release, evidence=[self.file('evidence-current.json', '{}')],
                               historical_evidence=[self.file('evidence-r4.json', '{"rust": "r4"}')])
        try:
            with patch.object(prof, 'TOOL_ALIASES', aliases), quiet():
                prof.prepare_command(args)
        except BaseException:
            self.base = previous
            raise
        self.parent = parent
        return args.out

    def args(self, **changes):
        values = dict(out=self.root / 'out', parent_manifest=self.parent,
                      parent_sha256=p6.file_info(self.parent)['sha256'], sidecar=self.sidecar,
                      sidecar_sha256=p6.file_info(self.sidecar)['sha256'], scenario='one-row', mode='perf',
                      perf_event='cycles:u', repetitions=2, control_repetitions=1, collector_mode='full',
                      window_budget_s=10000, quiet_window_token='mock-only-token')
        values.update(changes)
        return SimpleNamespace(**values)

    def run_sample(self, binary, workload, fixture, meter, root, collector_mode='full', **kwargs):
        root = Path(root)
        root.mkdir(parents=True)
        index = len(self.calls)
        self.calls.append(dict(binary=Path(binary), fixture=fixture, meter=meter, workload=workload['name'],
                               file_cap=self.fsize))
        if Path(binary).name == 'launch' and index not in self.missing:
            name, data = ARTIFACTS[self.mode]
            (root.parent / name).write_bytes(data)
        return dict(valid=True, errors=[], stdout_bytes=4, contract=dict(self.contracts.get(index, CONTRACT)))

    def probe(self, argv, stdout=None, **kwargs):
        self.probes.append(list(argv))
        stdout.write(SYMBOLS if '--syms' in argv else SECTIONS if '-S' in argv else b'tool 1.0\n')
        return SimpleNamespace(returncode=0)

    def capture(self, args, lock=None, sample=None, probe=None):
        self.mode = args.mode
        with patch.object(p6, 'quiet_lock', lock or (lambda *a: contextlib.nullcontext())), \
                patch.object(p6, 'run_sample', side_effect=sample or self.run_sample), \
                patch.object(subprocess, 'run', side_effect=probe or self.probe), quiet():
            code = prof.capture_command(args)
        report = p6.read_json(args.out / 'report.json') if (args.out / 'report.json').exists() else None
        return code, report

    def analyze(self, report, budget=100000, lock=None, tool=None, sidecar=None):
        out = self.root / f'analysis-{len(list(self.root.glob("analysis-*")))}'
        args = SimpleNamespace(report=report, report_sha256=p6.file_info(report)['sha256'], out=out, budget_s=budget,
                               sidecar_sha256=p6.file_info(sidecar or self.sidecar)['sha256'])
        buffer = io.StringIO()
        with patch.object(prof, 'offline_lock', lock or contextlib.nullcontext), \
                patch.object(subprocess, 'run', side_effect=tool or _refuse('analysis tool')), \
                contextlib.redirect_stdout(buffer):
            code = prof.analyze_command(args)
        text = buffer.getvalue().strip()
        self.printed = json.loads(text.splitlines()[-1]) if text else None
        return code, (p6.read_json(out / 'analysis.json') if (out / 'analysis.json').exists() else None)

    def perf_tool(self, samples=1200, lost=0, reject_no_skip_empty=False, during=None, per_role=None):
        def tool(argv, stdout=None, **kwargs):
            self.probes.append(list(argv))
            self.environments.append(kwargs.get('env'))
            self.cwds.append(kwargs.get('cwd'))
            if during:
                during()
            if '--stats' in argv:
                if reject_no_skip_empty and '--no-skip-empty' in argv:
                    return SimpleNamespace(returncode=129)
                count = next((n for role, n in (per_role or {}).items() if role in argv[-1]), samples)
                stdout.write(perf_stats_text(count, lost=lost).encode())
            else:
                stdout.write(b'# Samples: 1K of event cycles:u\n 50.00% pb main\n')
            return SimpleNamespace(returncode=0)
        self.environments, self.cwds = [], []
        return tool

    def heaptrack_tool(self, interpret=(0, b'plain intermediate\n'), report=b'PEAK MEMORY CONSUMERS\n'):
        def tool(argv, stdout=None, **kwargs):
            self.probes.append(list(argv))
            if Path(argv[0]).name.startswith('heaptrack_interpreter'):
                stdout.write(interpret[1])
                return SimpleNamespace(returncode=interpret[0])
            stdout.write(report)
            return SimpleNamespace(returncode=0)
        return tool

    def snapshot(self, folder):
        return {str(p.relative_to(folder)): p6.file_info(p)['sha256'] for p in sorted(folder.rglob('*')) if p.is_file()}

    # Capture: provenance, resolved execution, caps and accounting.

    def test_controls_precede_captures_with_resolved_paths_caps_and_full_accounting(self):
        code, report = self.capture(self.args())
        self.assertEqual(code, 0, report['failure'])
        self.assertTrue(report['passed'] and report['global_gate']['passed'] and report['control_gate']['passed'])
        self.assertEqual([e['phase'] for e in report['samples']], ['control'] * 4 + ['capture'] * 4)
        executables, tools = report['provenance']['executables'], report['provenance']['tools']
        self.assertEqual({str(c['binary']) for c in self.calls[:4]}, {e['path'] for e in executables.values()})
        for entry, call in zip(report['samples'][4:], self.calls[4:]):
            text = call['binary'].read_text()
            self.assertTrue(text.startswith('#!' + tools['shell']['path'] + '\nexec ' + tools['perf']['path']))
            self.assertNotIn('/alias/', text)
            self.assertEqual(entry['argv_prefix'][-1], executables[entry['role']]['path'])
            self.assertIn('--no-buildid-cache', entry['argv_prefix'])
        self.assertEqual([c['file_cap'] for c in self.calls], [1 << 30] * 4 + [2 << 30] * 4)
        restored = resource.getrlimit(resource.RLIMIT_FSIZE)
        self.assertEqual(self.limits[-2:], [(resource.RLIMIT_FSIZE, restored),
                                            (resource.RLIMIT_CORE, resource.getrlimit(resource.RLIMIT_CORE))])
        self.assertTrue(all('/alias/' not in argv[0] for argv in self.probes))
        self.assertEqual([r['status'] for r in report['accounting']], ['valid_control'] * 4 + ['interpretable'] * 4)
        self.assertEqual(report['roles']['go-symbolized'], dict(planned=3, executed=3, valid_controls=1,
                                                                interpretable_captures=2))
        self.assertTrue(report['finalization']['completed'] and report['end_identity_check']['passed'])
        self.assertTrue(report['harness_end_check']['passed'])
        self.assertEqual(set(report['provenance']['harness']), set(prof.HARNESS_MODULES))
        self.assertEqual(report['quiet_window_token_sha256'], hashlib.sha256(b'mock-only-token').hexdigest())
        self.assertNotIn('mock-only-token', (self.root / 'out/report.json').read_text())
        statuses = {Path(e['path']).name: e['status'] for e in p6.read_json(self.sidecar)['evidence']}
        self.assertEqual(statuses, {'evidence-current.json': 'current_supporting',
                                    'evidence-r4.json': 'historical_not_certifying'})

    def test_heaptrack_plan_is_rust_only_probes_print_version_and_launches_nothing(self):
        """N10: heaptrack_print --version is a planned, bounded capture probe; interpreter is hash-bound."""
        args = self.args(mode='heaptrack', quiet_window_token='')
        with quiet():
            self.assertEqual(prof.plan_command(args), 0)
        plan = p6.read_json(args.out / 'plan.json')
        self.assertEqual({i['role'] for i in plan['schedule']}, {'rust-release', 'rust-symbolized'})
        argv = plan['planned_capture_argv']['rust-symbolized']
        tools = plan['provenance']['tools']
        self.assertEqual(argv[:2], [tools['env']['path'], 'LD_PRELOAD=' + tools['heaptrack_preload']['path']])
        self.assertIn([tools['heaptrack_print']['path'], '--version'], [p['argv'] for p in plan['planned_probes']])
        self.assertFalse(plan['launched'])
        self.assertIsNone(plan['quiet_window_token_sha256'])
        budget = plan['partition_budget']
        self.assertEqual(budget['required_s'], budget['entry_rehash_s'] + budget['probes_s'] + budget['children_s'] +
                         budget['final_reserve_s'])

    def test_offline_plan_refuses_while_the_common_lock_is_held_before_hashing(self):
        """N1 for plan/prepare: a busy lock refuses before any provenance read or hash."""
        reads = []
        @contextlib.contextmanager
        def busy():
            raise BlockingIOError('window active')
            yield
        with patch.object(prof, 'offline_lock', busy), patch.object(prof, 'read_chunk', lambda *a: reads.append(a)), \
                patch.object(p6, 'read_json_with_identity', _refuse('read before lock')), quiet():
            self.assertEqual(prof.plan_command(self.args(quiet_window_token='')), 2)
        self.assertEqual(reads, [])
        self.assertFalse((self.root / 'out').exists())

    def test_invalid_or_missing_provenance_launches_nothing_and_creates_no_output(self):
        sidecar = p6.read_json(self.sidecar)
        del sidecar['roles']['go-symbolized']
        missing = self.json('sidecar-missing-role.json', sidecar)
        bad = {'wrong sidecar binding': self.args(sidecar_sha256='0' * 64),
               'wrong parent binding': self.args(parent_sha256='0' * 64),
               'unknown scenario': self.args(scenario='steady'),
               'missing symbolized role': self.args(sidecar=missing, sidecar_sha256=p6.file_info(missing)['sha256'])}
        for name, args in bad.items():
            with self.subTest(name):
                self.assertEqual(self.capture(args), (2, None))
            self.assertFalse(args.out.exists())
        self.assertEqual((self.calls, self.probes), ([], []))

    def test_symbolized_provenance_mismatch_rejects_and_removes_sidecar(self):
        for name, changes in (('rust sources', dict(rust_sources={'src/main.rs': 'other'})),
                              ('go commit', dict(go_commit='elsewhere'))):
            with self.subTest(name), self.assertRaises(ValueError):
                self.build(**changes)
        self.assertEqual(len(list(self.root.glob('b*/sidecar.json'))), 1)

    def test_role_or_scenario_mutation_after_prepare_is_rejected(self):
        changed = prof.scenarios()
        changed['one-row']['timeout_s'] = 1
        with patch.object(prof, 'scenarios', return_value=changed), self.assertRaisesRegex(ValueError, 'scenario'):
            prof.verify(self.args())
        rust = self.base / 'rsym/pb-symbolized'
        rust.chmod(0o755)
        rust.write_text('#!/bin/false\nrebuilt')
        rust.chmod(0o555)
        self.assertEqual(self.capture(self.args()), (2, None))
        self.assertFalse((self.root / 'out').exists())

    def test_unreviewed_or_transitive_harness_code_is_rejected_before_launch(self):
        """F4: imported code must equal the sidecar's reviewed set, including cases/phase3_workloads."""
        edited = p6.read_json(self.sidecar)
        edited['harness']['cases']['sha256'] = '0' * 64
        edited_path = self.json('sidecar-unreviewed.json', edited)
        self.assertEqual(self.capture(self.args(
            sidecar=edited_path, sidecar_sha256=p6.file_info(edited_path)['sha256'])), (2, None))
        drifted = dict(prof.IMPORTED['phase3_workloads'], sha256='1' * 64)
        extra = SimpleNamespace(__file__=str(prof.HARNESS_DIR / 'phase4_workloads.py'))
        outside = self.file('phase6_profiles_modified.py', '# not the reviewed adapter\n')
        for name, context in (('imported bytes differ', patch.dict(prof.IMPORTED, {'phase3_workloads': drifted})),
                              ('extra harness module', patch.dict(sys.modules, {'phase4_workloads': extra})),
                              ('adapter elsewhere', patch.object(prof, '__file__', str(outside)))):
            with self.subTest(name), context, self.assertRaisesRegex(ValueError, 'harness module'):
                prof.verify(self.args())
        self.assertEqual(self.calls, [])

    # N1/N2: lock before heavy work; window clock from command entry.

    def test_busy_lock_fails_before_any_provenance_read_hash_or_output(self):
        """N1 (review probe N1): ordering is causal; nothing heavy precedes the lock attempt."""
        events = []
        real_read, real_json = prof.read_chunk, p6.read_json_with_identity
        def read(stream, buffer):
            events.append('hash-read')
            return real_read(stream, buffer)
        def parse(*args, **kwargs):
            events.append('json-read')
            return real_json(*args, **kwargs)
        @contextlib.contextmanager
        def busy(*args):
            events.append('lock-attempt')
            raise BlockingIOError('held by another campaign window')
            yield
        with patch.object(prof, 'read_chunk', read), patch.object(p6, 'read_json_with_identity', parse):
            code, report = self.capture(self.args(), lock=busy)
            self.assertEqual((code, report, events), (2, None, ['lock-attempt']))
            self.assertFalse((self.root / 'out').exists())
            events.clear()
            code, report = self.capture(self.args(out=self.root / 'out-ok'), lock=tracking_lock(events))
        self.assertEqual(code, 0, report['failure'])
        self.assertEqual(events[0], 'lock-enter')
        self.assertEqual(events[-1], 'lock-exit')
        self.assertEqual(report['command']['order'], 'arguments, then lock, then verification and output')

    def test_window_clock_starts_at_command_entry(self):
        """N2 (review probe N2): verification time is charged; a spent window launches nothing."""
        clock, real = Clock(), prof.preflight
        def slow_preflight(*args, **kwargs):
            value = real(*args, **kwargs)
            clock.offset += int(2500e9)
            return value
        with patch.object(p6, 'clock_ns', clock), patch.object(prof, 'preflight', slow_preflight):
            code, report = self.capture(self.args(window_budget_s=2000))
        self.assertEqual((code, self.calls, self.probes), (1, [], []))
        self.assertIn('no child started', report['failure'])
        self.assertGreaterEqual(report['command']['verified_s'], 2500)
        self.assertFalse(report['end_identity_check']['performed'] or report['passed'])

    def test_window_or_perf_unavailability_launch_nothing(self):
        code, report = self.capture(self.args(window_budget_s=60))
        self.assertEqual(code, 1)
        self.assertIn('no child started', report['failure'])
        self.assertTrue(all(r['status'] == 'not_executed' for r in report['accounting']))
        @contextlib.contextmanager
        def paranoid_changes_inside(*args):
            self.paranoid.write_text('3\n')
            yield
        code, report = self.capture(self.args(out=self.root / 'out-inside'), lock=paranoid_changes_inside)
        self.assertIn('perf_event_paranoid > 2', report['failure'])
        self.assertEqual(report['perf']['paranoid'], 3)
        self.paranoid.unlink()
        self.assertEqual(self.capture(self.args(out=self.root / 'out-unknown')), (2, None))
        self.assertEqual((self.calls, self.probes), ([], []))

    def test_midwindow_budget_or_disk_stop_preserves_samples_and_accounts_for_the_rest(self):
        class Budget:
            def __init__(self, seconds):
                self.deadline, self.calls = p6.clock_ns() + int(1e13), 0
            def admission(self, workload):
                self.calls += 1
                return dict(fits=self.calls <= 3, remaining_s=0, required_s=1)
        with patch.object(p6, 'WindowBudget', Budget):
            code, report = self.capture(self.args())
        self.assertEqual((code, report['complete'], len(report['samples'])), (1, False, 3))
        self.assertEqual([r['status'] for r in report['accounting']][3:], ['not_executed'] * 5)
        self.assertIn('admission refused', report['accounting'][7]['reasons'][0])
        with patch.object(prof.os, 'statvfs', lambda path: SimpleNamespace(f_bavail=1, f_frsize=1)):
            code, report = self.capture(self.args(out=self.root / 'out-disk'))
        self.assertEqual((report['samples'], report['window_stop']['disk']['free_bytes']), ([], 1))

    # N3: deadline-aware hashing.

    def test_bounded_reader_matches_frozen_identities_and_stops_before_the_next_chunk(self):
        path = self.file('artifact.bin', bytes(range(256)) * 40, 0o644)
        self.assertEqual(prof.bounded_info(path), p6.file_info(path))
        self.assertEqual(prof.bounded_info(path), prof.profiles.manifest.file_identity(path))
        clock, reads = Clock(), []
        deadline = clock() + int(10e9)
        def read(stream, buffer):
            reads.append(clock())
            clock.offset += int(4e9)
            return stream.readinto(buffer)
        with patch.object(prof, 'CHUNK_BYTES', 1024), patch.object(p6, 'clock_ns', clock), \
                patch.object(prof, 'read_chunk', read), self.assertRaises(prof.DeadlineExceeded):
            prof.bounded_info(path, deadline)
        self.assertEqual(len(reads), 3)
        self.assertTrue(all(started < deadline for started in reads))

    def test_final_rehash_stops_at_the_deadline_and_quarantines(self):
        """N3 (review probe F10-end-rehash): no hash read starts after the window deadline."""
        clock, reads, budgets, state = Clock(), [], [], dict(final=False)
        class Budget(p6.WindowBudget):
            def __init__(self, seconds):
                super().__init__(seconds)
                budgets.append(self)
        real_read, real_final = prof.read_chunk, prof.finalize
        def read(stream, buffer):
            if state['final']:
                reads.append(clock())
                clock.offset += int(400e9)
            return real_read(stream, buffer)
        def finalize(*args, **kwargs):
            state['final'] = True
            try:
                return real_final(*args, **kwargs)
            finally:
                state['final'] = False
        with patch.object(p6, 'clock_ns', clock), patch.object(p6, 'WindowBudget', Budget), \
                patch.object(prof, 'read_chunk', read), patch.object(prof, 'finalize', finalize):
            code, report = self.capture(self.args(window_budget_s=4000))
        deadline = budgets[0].deadline
        self.assertEqual(sum(started >= deadline for started in reads), 0)
        self.assertGreater(len(reads), 0)
        check = report['end_identity_check']
        self.assertEqual((check['incomplete'], check['passed'], report['quarantined']), ('deadline', False, True))
        self.assertLess(check['checked'], check['total'])
        self.assertEqual((code, report['passed']), (1, False))

    def test_per_launch_or_artifact_hash_deadline_aborts_the_window(self):
        clock = Clock()
        def slow_sample(binary, *args, **kwargs):
            value = self.run_sample(binary, *args, **kwargs)
            if Path(binary).name == 'launch':
                clock.offset += int(1e13)
            return value
        with patch.object(p6, 'clock_ns', clock):
            code, report = self.capture(self.args(), sample=slow_sample)
        self.assertIn('DeadlineExceeded', report['failure'])
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(report['end_identity_check']['incomplete'], 'deadline')

    # Gates: controls, contracts, identities, cancellation.

    def test_invalid_or_incomparable_control_stops_before_any_capture(self):
        """F3: an invalid control stops the window; incomparable controls launch no capture."""
        def invalid_first(binary, *args, **kwargs):
            value = self.run_sample(binary, *args, **kwargs)
            if len(self.calls) == 1:
                value.update(valid=False, errors=['go-release control failed'])
            return value
        code, report = self.capture(self.args(), sample=invalid_first)
        self.assertEqual((code, len(self.calls), report['control_gate']['passed']), (1, 1, False))
        self.assertEqual(sum(r['status'] == 'not_executed' for r in report['accounting']), 7)
        code, result = self.analyze(self.root / 'out/report.json')
        self.assertEqual((code, {e['status'] for e in result['entries']}), (1, {'skipped'}))
        self.calls, self.contracts = [], {3: dict(CONTRACT, stdout_sha256='symbolized differs')}
        code, report = self.capture(self.args(out=self.root / 'out-incomparable'))
        self.assertEqual((len(self.calls), report['control_gate']['passed']), (4, False))
        self.assertIn('stdout_sha256', report['control_gate']['comparison']['differing_fields'])

    def test_contract_mismatch_rejects_capture_even_when_sample_is_valid(self):
        self.contracts = {6: dict(CONTRACT, stderr_sha256='profiler wrote to stderr')}
        code, report = self.capture(self.args())
        self.assertEqual(code, 1)
        entry = report['samples'][6]
        self.assertFalse(entry['contract_matches_reference'] or entry['interpretable'])
        self.assertIn('stderr_sha256', report['contract_comparison']['differing_fields'])

    def test_quarantined_capture_is_never_interpreted_or_analyzed(self):
        """F1: a mid-window mutation blocks every capture and the whole analysis."""
        log = self.base / 'rsym/build.log'
        def sample(binary, *args, **kwargs):
            if not self.calls:
                log.chmod(0o644)
                log.write_text('changed during window')
            return self.run_sample(binary, *args, **kwargs)
        code, report = self.capture(self.args(), sample=sample)
        self.assertEqual((code, report['quarantined'], report['passed']), (1, True, False))
        self.assertFalse(any(e['interpretable'] for e in report['samples']))
        self.assertEqual(report['end_identity_check']['changed'], [str(log)])
        probes = len(self.probes)
        code, result = self.analyze(self.root / 'out/report.json', tool=self.perf_tool())
        self.assertEqual((code, result['analysis_complete'], result['capture_gate']['passed']), (1, False, False))
        self.assertEqual((len(self.probes), {e['status'] for e in result['entries']}), (probes, {'skipped'}))

    def test_identity_change_before_launch_refuses_that_launch(self):
        go = self.base / 'gsym/pb-go-symbolized'
        def sample(binary, *args, **kwargs):
            if not self.calls:
                go.chmod(0o755)
                go.write_text('changed during window')
            return self.run_sample(binary, *args, **kwargs)
        code, report = self.capture(self.args(), sample=sample)
        self.assertIn('executable identity changed before launch', report['failure'])
        self.assertNotIn(go, [c['binary'] for c in self.calls])
        self.assertFalse(report['end_identity_check']['passed'] or report['passed'])

    def test_lazy_harness_import_during_window_quarantines(self):
        """N4 hardening (review probe F4-lazy) on the capture side."""
        extra = SimpleNamespace(__file__=str(prof.HARNESS_DIR / 'phase4_workloads.py'))
        def sample(binary, *args, **kwargs):
            sys.modules['phase4_workloads'] = extra
            return self.run_sample(binary, *args, **kwargs)
        try:
            code, report = self.capture(self.args(), sample=sample)
        finally:
            sys.modules.pop('phase4_workloads', None)
        self.assertEqual((code, report['harness_end_check']['passed'], report['quarantined']), (1, False, True))

    def test_final_rehash_and_report_write_happen_inside_the_lock(self):
        """F8: no rehash or final report write after the common lock is released."""
        held, seen, writes = [False], [], []
        @contextlib.contextmanager
        def lock(*args):
            held[0] = True
            try:
                yield
            finally:
                held[0] = False
        real_changed, real_write = prof.changed_identities, p6.write_json
        def changed(*args, **kwargs):
            seen.append(held[0])
            return real_changed(*args, **kwargs)
        def write(path, value):
            writes.append((Path(path).name, held[0]))
            return real_write(path, value)
        with patch.object(prof, 'changed_identities', changed), patch.object(p6, 'write_json', write):
            code, report = self.capture(self.args(), lock=lock)
        self.assertEqual(code, 0)
        self.assertTrue(seen and all(seen))
        self.assertTrue(writes and all(inside for _, inside in writes))
        self.assertTrue(report['finalization']['completed'] and not report['finalization']['window_overrun'])

    def test_all_abort_signals_are_owned_even_if_sigint_was_inherited_ignored(self):
        """F9: simulated by invoking the installed handler; no signal is sent."""
        previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            with prof.interrupt_signals():
                for sig in previous:
                    with self.assertRaises(KeyboardInterrupt):
                        signal.getsignal(sig)(sig, None)
            self.assertTrue(all(signal.getsignal(sig) is signal.SIG_IGN for sig in previous))
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def test_cancellation_in_probe_or_child_finalizes_inside_lock_and_launches_nothing_more(self):
        def probe(argv, stdout=None, **kwargs):
            if len(self.probes) == 2:
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return self.probe(argv, stdout=stdout, **kwargs)
        before = signal.getsignal(signal.SIGINT)
        code, report = self.capture(self.args(), probe=probe)
        self.assertEqual((code, self.calls, report['complete']), (1, [], False))
        self.assertIn('KeyboardInterrupt', report['probes'][-1]['error'])
        self.assertTrue(report['finalization']['completed'])
        self.assertIs(signal.getsignal(signal.SIGINT), before)
        self.calls, self.probes = [], []
        def interrupted(binary, *args, **kwargs):
            if Path(binary).name == 'launch':
                raise KeyboardInterrupt
            return self.run_sample(binary, *args, **kwargs)
        code, report = self.capture(self.args(out=self.root / 'out-abort'), sample=interrupted)
        self.assertEqual((code, report['complete'], len(report['samples'])), (1, False, 5))
        self.assertIn('collector exception', report['failure'])
        self.assertFalse(any(e['interpretable'] for e in report['samples']))

    def test_failed_or_timed_out_probe_is_persisted_before_abort(self):
        """F13: return status, timeout and output identities are in the report before raising."""
        def failing(argv, stdout=None, stderr=None, **kwargs):
            if '-S' in argv:
                stderr.write(b'readelf: Error: not an ELF file\n')
                return SimpleNamespace(returncode=2)
            return self.probe(argv, stdout=stdout, **kwargs)
        def timeout(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, prof.PROBE_TIMEOUT_S)
        for index, (probe, check) in enumerate(((failing, dict(returncode=2, timed_out=False)),
                                                (timeout, dict(returncode=None, timed_out=True)))):
            code, report = self.capture(self.args(out=self.root / f'out-probe-{index}'), probe=probe)
            record = report['probes'][-1]
            self.assertEqual({key: record[key] for key in check}, check)
            self.assertTrue(record['error'] and record['stdout']['sha256'] and record['stderr']['size_bytes'] >= 0)
            self.assertEqual((code, self.calls), (1, []))

    # Caps.

    def test_artifact_cap_is_separate_from_the_sample_wide_file_cap(self):
        """N8/F10: strict artifact post-check; sample files capped no lower than the collector/fixture floor."""
        with patch.dict(prof.MAX_ARTIFACT_BYTES, {'strace': 4}):
            code, report = self.capture(self.args(mode='strace'))
        self.assertEqual([c['file_cap'] for c in self.calls], [1 << 30] * 8)
        self.assertIn('reached its artifact cap', report['samples'][4]['errors'][0])
        self.assertEqual((report['caps']['artifact_bytes'], report['caps']['capture_sample_file_bytes']), (4, 1 << 30))
        self.assertFalse(report['passed'])
        with patch.object(prof, 'PROBE_OUTPUT_BYTES', 4):
            code, report = self.capture(self.args(out=self.root / 'out-probe-cap'))
        self.assertIn('output cap', report['probes'][-1]['error'])
        self.assertIn((resource.RLIMIT_CORE, (0, resource.getrlimit(resource.RLIMIT_CORE)[1])), self.limits)

    def test_missing_artifact_blocks_analysis_with_every_role_reported(self):
        """F6: skipped roles stay visible and an empty analysis is non-zero."""
        self.missing = {4, 7}
        code, report = self.capture(self.args())
        self.assertEqual((code, report['complete']), (1, True))
        self.assertIn('missing or ambiguous perf capture', report['samples'][4]['errors'][0])
        code, result = self.analyze(self.root / 'out/report.json')
        self.assertEqual((code, set(result['roles'])), (1, {'go-symbolized', 'rust-symbolized'}))
        self.assertEqual(result['roles']['go-symbolized']['skipped'], 2)

    # Offline analysis.

    def test_realistic_perf_stats_keep_unknown_loss_unknown(self):
        """F2: source-derived shapes only; the host format needs a later functional probe."""
        cases = {'aggregated loss is read, per-event block ignored': (perf_stats_text(1500, lost=400), 'lost_events'),
                 'skip-empty output lacks LOST rows': (perf_stats_text(1500, skip_empty=True), 'unavailable'),
                 'zero rows printed': (perf_stats_text(1500), 'sufficient_for_descriptive_stacks'),
                 'AUX header suffix': (perf_stats_text(1500, aux=True), 'sufficient_for_descriptive_stacks'),
                 'below floor': (perf_stats_text(999), 'insufficient_samples'),
                 'no aggregated block': (perf_stats_text(1500).split('Aggregated stats:')[1], 'unavailable'),
                 'unrecognized row': (perf_stats_text(1500).replace('MMAP events', 'MMAP evts'), 'unavailable')}
        for name, (text, status) in cases.items():
            with self.subTest(name):
                self.assertEqual(prof.capture_stack_status(prof.parse_perf_stats(text)), status)
        stats = prof.parse_perf_stats(perf_stats_text(1500, lost=400))
        self.assertEqual((stats['sample_events'], stats['lost_events']), (1500, 400))
        without = '\n'.join(line for line in perf_stats_text(1500).splitlines()
                            if not line.endswith('%)') or 'SAMPLE' not in line) + '\n'
        self.assertIsNone(prof.parse_perf_stats(without)['sample_events'])

    def test_interpretation_exit_status_is_separate_from_analysis_completion(self):
        """N5 (review probe F6-residual): zero interpretable is exit 3, a missing role is exit 4."""
        self.capture(self.args())
        report = self.root / 'out/report.json'
        def unknown_loss(argv, stdout=None, **kwargs):
            stdout.write(perf_stats_text(5000, skip_empty=True).encode() if '--stats' in argv else b'# report\n')
            return SimpleNamespace(returncode=0)
        expected = {'all unknown loss': (unknown_loss, 3, 'no_interpretable_capture'),
                    'all insufficient': (self.perf_tool(samples=400), 3, 'no_interpretable_capture'),
                    'one role insufficient': (self.perf_tool(per_role={'go-symbolized': 400}), 4,
                                              'partial_role_coverage'),
                    'one role fails': (self.perf_tool(reject_no_skip_empty=True), 1, 'analysis_incomplete'),
                    'all sufficient': (self.perf_tool(), 0, 'all_roles_interpretable')}
        for name, (tool, code, status) in expected.items():
            with self.subTest(name):
                observed, result = self.analyze(report, tool=tool)
                self.assertEqual((observed, result['interpretation']['status'], result['exit_status']),
                                 (code, status, code))
                self.assertNotIn('passed', result)
                self.assertEqual(set(result['roles']), {'go-symbolized', 'rust-symbolized'})
                self.assertEqual(result['analysis_complete'], code != 1)
        self.assertEqual(result['interpretation']['interpretable_captures'], 4)

    def test_perf_analysis_floors_each_capture_with_controlled_environment(self):
        """F5/F11: per-capture floor, controlled env and resolved tool; budget refusal before any tool."""
        self.capture(self.args())
        report = self.root / 'out/report.json'
        code, result = self.analyze(report, budget=60, tool=self.perf_tool())
        self.assertIn('not started', result['failure'])
        code, result = self.analyze(report, tool=self.perf_tool(samples=700))
        self.assertEqual({e['stack_status'] for e in result['entries']}, {'insufficient_samples'})
        self.assertEqual(result['roles']['go-symbolized']['descriptive_sample_events_total'], 1400)
        perf = p6.read_json(report)['provenance']['tools']['perf']['path']
        self.assertTrue(all(argv[0] == perf for argv in self.probes[-8:]))
        self.assertTrue(all('--no-skip-empty' in argv for argv in self.probes[-8:] if '--stats' in argv))
        env = self.environments[-1]
        self.assertEqual((env['PATH'], env['HOME'], env['DEBUGINFOD_URLS']),
                         (os.defpath, str(self.root / 'analysis-1/home'), ''))
        self.assertEqual(result['tool_versions'], {'perf': 'tool 1.0'})
        self.assertEqual(Path(self.cwds[-1]), self.root / 'analysis-1/captures' / result['entries'][-1]['directory'])
        self.assertEqual(set(result['analysis_harness']), set(prof.HARNESS_MODULES))

    def test_analysis_verifies_its_own_code_at_start_and_end(self):
        """N4 (review probe F4-analysis): different code, extra modules or a lazy import fail the analysis."""
        self.capture(self.args())
        report = self.root / 'out/report.json'
        drifted = dict(prof.IMPORTED['phase6_profiles'], sha256='0' * 64)
        extra = SimpleNamespace(__file__=str(prof.HARNESS_DIR / 'phase4_workloads.py'))
        for name, context in (('different adapter bytes', patch.dict(prof.IMPORTED, {'phase6_profiles': drifted})),
                              ('extra harness module', patch.dict(sys.modules, {'phase4_workloads': extra}))):
            with self.subTest(name), context:
                code, result = self.analyze(report, tool=self.perf_tool())
                self.assertEqual(code, 1)
                self.assertIn('harness', result['failure'])
                self.assertNotIn('analysis_harness', result)
        def lazy():
            sys.modules['phase4_workloads'] = extra
        try:
            code, result = self.analyze(report, tool=self.perf_tool(during=lazy))
        finally:
            sys.modules.pop('phase4_workloads', None)
        self.assertEqual((code, result['analysis_harness_end']['passed'], result['quarantined']), (1, False, True))

    def test_analysis_records_overrun_and_stops_gzip_at_the_deadline(self):
        """N6 (review probe F10-analysis): elapsed and overrun recorded; overrun is never complete."""
        self.capture(self.args())
        clock, calls = Clock(), [0]
        base = self.perf_tool()
        def tool(argv, **kwargs):
            calls[0] += 1
            if calls[0] == 8:
                clock.offset += int(1e15)
            return base(argv, **kwargs)
        with patch.object(p6, 'clock_ns', clock):
            code, result = self.analyze(self.root / 'out/report.json', budget=5000, tool=tool)
        self.assertEqual((code, result['analysis_complete'], result['timing']['overrun']), (1, False, True))
        self.assertGreater(result['timing']['elapsed_s'], 5000)


    def test_gzip_copy_itself_stops_before_the_next_chunk_at_the_deadline(self):
        """R3-2 / X21 (adopted from review R3 probe r3_n6_gzip_copy_itself_stops_at_the_deadline): the clock
        advances only inside bounded_gzip, so the gzip loop's own per-chunk check is what is exercised."""
        self.capture(self.args(mode='heaptrack'))
        clock, state, reads, holder = Clock(), dict(gzip=False), [], {}
        real_gzip, real_read, real_tool = prof.bounded_gzip, prof.read_chunk, prof.run_tool
        def gzip_phase(*args, **kwargs):
            state['gzip'] = True
            try:
                return real_gzip(*args, **kwargs)
            finally:
                state['gzip'] = False
        def read(stream, buffer):
            if state['gzip']:
                reads.append(clock())
                clock.offset += int(400e9)
            return real_read(stream, buffer)
        def run_tool(*args, **kwargs):
            holder['deadline'] = args[4]
            return real_tool(*args, **kwargs)
        with patch.object(prof, 'CHUNK_BYTES', 4), patch.object(prof, 'read_chunk', read), \
                patch.object(prof, 'bounded_gzip', gzip_phase), patch.object(prof, 'run_tool', run_tool), \
                patch.object(p6, 'clock_ns', clock):
            code, result = self.analyze(self.root / 'out/report.json', budget=3000,
                                        tool=self.heaptrack_tool((0, b'x' * 400)))
        self.assertEqual(code, 1)
        self.assertIn('compressing the heaptrack intermediate', result['failure'])
        self.assertTrue(reads and all(started < holder['deadline'] for started in reads))
        self.assertLess(len(reads), 100)
        self.assertEqual(list(self.root.glob('analysis-*/captures/*/interpreted.txt')), [])

    def test_analysis_drift_quarantines_and_busy_lock_or_stale_report_creates_nothing(self):
        """F11 and post-end mutation."""
        self.capture(self.args())
        report = self.root / 'out/report.json'
        go = self.base / 'gsym/pb-go-symbolized'
        start = len(self.probes)
        def mutate():
            if len(self.probes) == start + 1:
                go.chmod(0o755)
                go.write_text('replaced during analysis')
        code, result = self.analyze(report, tool=self.perf_tool(during=mutate))
        self.assertEqual((code, result['quarantined'], result['end_identity_check']['changed']), (1, True, [str(go)]))
        self.assertFalse(any(e['interpretable'] for e in result['entries']))
        artifact = Path(p6.read_json(report)['samples'][5]['artifacts'][0]['path'])
        artifact.write_bytes(b'post-end mutation')
        code, result = self.analyze(report, tool=self.perf_tool())
        self.assertEqual((code, result['quarantined']), (1, True))
        self.assertIn('identities changed since capture', result['failure'])
        @contextlib.contextmanager
        def busy():
            raise BlockingIOError('campaign window active')
            yield
        code, result = self.analyze(report, lock=busy)
        self.assertEqual((code, result), (2, None))
        stale = self.json('stale-report.json', dict(p6.read_json(report), schema='pb-phase6-profile-report-v2'))
        self.assertEqual(self.analyze(stale), (2, None))

    def test_analysis_never_writes_capture_folders_and_retries_without_cleanup(self):
        """N7 and F7: outputs go to a new analysis tree; a traced stdout mismatch is an error."""
        code, report = self.capture(self.args(mode='strace-writes'))
        self.assertEqual(code, 0, report['failure'])
        before = self.snapshot(self.root / 'out')
        code, result = self.analyze(self.root / 'out/report.json', tool=_refuse('no tool for strace-writes'))
        self.assertEqual((code, result['entries'][0]['writes_by_fd']['1']['bytes']), (3, 4))
        code, again = self.analyze(self.root / 'out/report.json')
        self.assertEqual(code, 3)
        self.assertEqual(self.snapshot(self.root / 'out'), before)
        existing = self.root / 'analysis-0'
        args = SimpleNamespace(report=self.root / 'out/report.json', report_sha256='x', out=existing, budget_s=100,
                               sidecar_sha256='x')
        with quiet():
            self.assertEqual(prof.analyze_command(args), 2)
        def wrong_stdout(*args, **kwargs):
            return dict(self.run_sample(*args, **kwargs), stdout_bytes=5)
        self.calls = []
        self.capture(self.args(mode='strace-writes', out=self.root / 'out-mismatch'), sample=wrong_stdout)
        code, result = self.analyze(self.root / 'out-mismatch/report.json')
        self.assertEqual(code, 1)
        self.assertIn('differ from captured stdout', result['entries'][0]['error'])

    def test_local_write_counts_match_the_frozen_parser(self):
        traces = ['11 write(0x1, 0xff, 0x4 <unfinished ...>\n12 write(0x4, 0xaa, 0x8) = 0x8\n'
                  '11 <... write resumed>) = 0x4\n11 writev(0x1, 0xbb, 0x2) = -1 EPIPE (Broken pipe)\n'
                  '12 --- SIGURG {si_signo=SIGURG} ---\n',
                  '11 <... write resumed>) = 0x4\n', '11 write(0x1, 0xff, 0x4 <unfinished ...>\n', 'garbage\n']
        for index, text in enumerate(traces):
            path = self.file(f'trace-{index}', text, 0o644)
            try:
                expected = ('ok', prof.profiles.write_counts(path))
            except ValueError as error:
                expected = ('error', str(error))
            try:
                observed = ('ok', prof.bounded_write_counts(path, None))
            except ValueError as error:
                observed = ('error', str(error))
            self.assertEqual(observed, expected)

    def test_heaptrack_analysis_records_versions_and_removes_the_plain_intermediate(self):
        """N10/F10: print version and interpreter hash recorded; plain scratch never survives."""
        code, report = self.capture(self.args(mode='heaptrack'))
        self.assertEqual(code, 0, report['failure'])
        self.assertIn('heaptrack_print-version', [p['name'] for p in report['probes']])
        code, result = self.analyze(self.root / 'out/report.json', tool=self.heaptrack_tool())
        self.assertEqual((code, result['interpretation']['status']), (3, 'analysis_complete_no_adequacy_criterion'))
        self.assertTrue(result['analysis_complete'])
        interpret = result['entries'][0]['steps'][0]
        self.assertEqual((interpret['status'], interpret['stdout_identity']['sha256']), ('completed', None))
        self.assertIn('discarded intermediate', interpret['stdout_identity']['hash_unavailable'])
        self.assertEqual(result['tool_versions']['heaptrack_print'], 'tool 1.0')
        self.assertTrue(result['tool_versions']['heaptrack_interpreter'].startswith('no version flag'))
        self.assertEqual(list(self.root.glob('analysis-0/captures/*/interpreted.txt')), [])
        self.assertTrue(list(self.root.glob('analysis-0/captures/*/interpreted.gz')))
        code, result = self.analyze(self.root / 'out/report.json', tool=self.heaptrack_tool((1, b'partial')))
        self.assertEqual((code, {e['status'] for e in result['entries']}), (1, {'failed'}))
        self.assertEqual(list(self.root.glob('analysis-1/captures/*/interpreted.txt')), [])

    # Review R3 corrections.

    def forge(self, source, change, name, folder=None):
        value = p6.read_json(source)
        change(value)
        if folder is None:
            return self.json(name, value)
        path = Path(folder) / name
        path.write_text(json.dumps(value))
        return path

    def test_report_folder_tool_role_and_schedule_rewrites_are_refused(self):
        """R3-1: a rewritten report with a fresh SHA cannot redirect outputs or substitute tools."""
        self.capture(self.args())
        report = self.root / 'out/report.json'
        first = lambda value: next(e for e in value['samples'] if e.get('interpretable'))
        rewrites = {
            'traversal folder': (lambda v: first(v).update(directory='../../escaped'), 'folder or identity differs'),
            'absolute folder': (lambda v: first(v).update(directory=str(self.root / 'absolute')), 'folder or identity differs'),
            'artifact elsewhere': (lambda v: first(v)['artifacts'][0].update(path=str(self.sidecar)),
                                   'artifact is outside its capture folder'),
            'tool path': (lambda v: v['provenance']['tools']['perf'].update(path='/usr/bin/env'),
                          'report tool perf differs'),
            'role binary': (lambda v: v['provenance']['executables']['rust-symbolized'].update(sha256='0' * 64),
                            'report executable rust-symbolized differs'),
            'schedule': (lambda v: v.update(repetitions=3), 'schedule differs')}
        control = self.forge(report, lambda v: None, 'unchanged-copy.json', folder=self.root / 'out')
        self.assertEqual(self.analyze(control, tool=self.perf_tool())[0], 0)  # same placement, unchanged: passes
        for name, (change, error) in rewrites.items():
            with self.subTest(name):
                forged = self.forge(report, change, f'forged-{name.replace(" ", "-")}.json', folder=self.root / 'out')
                code, result = self.analyze(forged, tool=_refuse('tool after a rewritten report'))
                self.assertEqual(code, 1)
                self.assertIn(error, result['failure'])
                self.assertFalse((self.root / 'escaped').exists() or (self.root / 'absolute').exists())
        other = self.file('other-sidecar.json', '{}', 0o644)
        code, result = self.analyze(report, tool=_refuse('tool'), sidecar=other)
        self.assertIn('different sidecar', result['failure'])

    def test_analysis_binds_to_the_report_harness_not_the_running_imports(self):
        """R3-2 / X10 (adopted from review R3 probe r3_n4): report and sidecar agree, running code differs."""
        self.capture(self.args())
        sidecar = self.forge(self.sidecar, lambda v: v['harness']['run'].update(sha256='0' * 64), 'sidecar-other-run.json')
        def change(value):
            value['provenance']['sidecar'] = p6.file_info(sidecar)
            value['provenance']['harness'] = p6.read_json(sidecar)['harness']
        forged = self.forge(self.root / 'out/report.json', change, 'report-other-run.json', folder=self.root / 'out')
        code, result = self.analyze(forged, tool=_refuse('analysis tool with unreviewed harness'), sidecar=sidecar)
        self.assertEqual(code, 1)
        self.assertIn('harness module run differs', result['failure'])
        self.assertFalse(result['analysis_harness_end']['passed'])

    def test_frozen_binders_hash_only_through_the_bounded_reader(self):
        """R3-2 / X04 (adopted from review R3 probe r3_n3_frozen_binders...): budgets file read in chunks."""
        reads, clock = [], Clock()
        deadline = clock() + int(1e12)
        args = self.args()
        real = prof.read_chunk
        def read(stream, buffer):
            reads.append((Path(stream.name).name, clock()))
            if stream.name.endswith('performance-budgets.json'):
                clock.offset += int(2e12)
            return real(stream, buffer)
        original = p6.file_info
        with patch.object(prof, 'read_chunk', read), patch.object(p6, 'clock_ns', clock), \
                self.assertRaises(prof.DeadlineExceeded):
            prof.verify(args, deadline)
        self.assertIn('performance-budgets.json', [name for name, _ in reads])
        self.assertTrue(all(started < deadline for _, started in reads))
        self.assertIs(p6.file_info, original)

    def test_prelaunch_rehash_stops_at_the_work_deadline(self):
        """R3-2 / X08 (adopted from review R3 probe r3_n3_artifact_and_prelaunch...): no launch after expiry."""
        holder, clock, count = {}, Clock(), [0]
        real_captures = prof.captures
        def captures(*args):
            holder['report'], holder['budget'] = args[5], args[8]
            return real_captures(*args)
        class Budget(p6.WindowBudget):
            def admission(self, workload):
                value = super().admission(workload)
                count[0] += 1
                if count[0] == 6:
                    work = holder['budget'].deadline - int(holder['report']['partition_budget']['final_reserve_s'] * 1e9)
                    clock.offset = work + int(1e9) - clock.real()
                return value
        with patch.object(p6, 'clock_ns', clock), patch.object(p6, 'WindowBudget', Budget), \
                patch.object(prof, 'captures', captures):
            code, report = self.capture(self.args())
        self.assertEqual((code, len(self.calls)), (1, 5))
        self.assertIn('DeadlineExceeded', report['failure'])
        self.assertTrue(report['end_identity_check']['passed'])

    def test_tool_timeout_is_clipped_to_the_remaining_deadline(self):
        """R3-2 / X13 (adopted from review R3 probe r3_n6_tool_timeout_is_clipped...)."""
        self.capture(self.args())
        clock, holder, timeouts = Clock(), {}, []
        real_tool = prof.run_tool
        def run_tool(*args, **kwargs):
            holder['deadline'] = args[4]
            return real_tool(*args, **kwargs)
        base = self.perf_tool()
        def tool(argv, timeout=None, **kwargs):
            timeouts.append(timeout)
            if len(timeouts) == 1:
                clock.offset = holder['deadline'] - int(50e9) - clock.real()
            return base(argv, timeout=timeout, **kwargs)
        with patch.object(p6, 'clock_ns', clock), patch.object(prof, 'run_tool', run_tool):
            self.analyze(self.root / 'out/report.json', tool=tool)
        self.assertEqual(timeouts[0], prof.ANALYSIS_TIMEOUT_S)
        self.assertTrue(49.0 < timeouts[1] <= 50.0)

    def test_local_write_counts_match_frozen_parser_on_exit_lines_and_fuzz(self):
        """R3-2 / X19 (fuzz adopted from review R3 probe r3_local_write_counts_differential_fuzz, seed 20261014)."""
        import random
        rng = random.Random(20261014)
        def line(pid):
            call, fd = rng.choice(['write', 'writev']), rng.choice(['1', '0x1', '2', '0x4', '17'])
            ret = rng.choice(['0x4', '4', '-1 EPIPE (Broken pipe)', '0', '0x10', '-0x1', '?'])
            kind = rng.random()
            if kind < 0.45:
                return f'{pid} {call}({fd}, 0xab, 0x4) = {ret}\n'
            if kind < 0.6:
                return f'{pid} {call}({fd}, 0xab, 0x4 <unfinished ...>\n'
            if kind < 0.75:
                return f'{pid} <... {rng.choice(["write", "writev"])} resumed>) = {ret}\n'
            if kind < 0.8:
                return f'{pid} --- SIGURG {{si_signo=SIGURG}} ---\n'
            if kind < 0.85:
                return f'{pid} +++ exited with 0 +++\n'
            if kind < 0.9:
                return rng.choice(['\n', '   \n', '\t\n'])
            if kind < 0.95:
                return f'{pid} {call}({fd}, 0xab, 0x4) = {ret}'
            return rng.choice(['garbage\n', f'{pid} read(0, 0x1, 4) = 4\n', 'write(0x1) = 4\n'])
        corpus = ['7 write(0x1, 0xaa, 0x4) = 0x4\n7 +++ exited with 0 +++\n',
                  '7 write(0x1, 0xaa, 0x4) = 0x4']
        corpus += [''.join(line(rng.choice([7, 8, 9])) for _ in range(rng.randint(0, 12))) for _ in range(3000)]
        differing = []
        for index, text in enumerate(corpus):
            path = self.base / f'fuzz-{index}'
            path.write_text(text)
            outcomes = []
            for parser in (prof.profiles.write_counts, lambda p: prof.bounded_write_counts(p, None)):
                try:
                    outcomes.append(('ok', parser(path)))
                except ValueError as error:
                    outcomes.append(('error', str(error)))
            if outcomes[0] != outcomes[1]:
                differing.append(index)
            path.unlink()
        self.assertEqual(differing, [])

    def test_write_trace_parse_checks_the_deadline_per_record(self):
        """R3-2 / X20 (adopted from review R3 probe r3_n6_write_trace_parse...): stops after 100 records."""
        path = self.file('trace', ''.join(f'{i % 7} write(0x1, 0xaa, 0x4) = 0x4\n' for i in range(1000)), 0o644)
        ticks, base = [0], p6.clock_ns()
        def ticking():
            ticks[0] += 1
            return base + ticks[0] * int(1e9)
        with patch.object(p6, 'clock_ns', ticking), self.assertRaises(prof.DeadlineExceeded):
            prof.bounded_write_counts(path, base + int(100.5e9))
        self.assertEqual(ticks[0], 101)

    def test_failed_interrupted_or_expired_tools_are_journaled(self):
        """R3-3: in_progress before the tool; failure, interruption or expiry keeps argv/rc/output metadata."""
        self.capture(self.args())
        report = self.root / 'out/report.json'
        seen = []
        base = self.perf_tool()
        def journaled(argv, stdout=None, stderr=None, **kwargs):
            out = Path(stdout.name).parents[2]
            journal = p6.read_json(out / 'analysis.json')
            entry = next(e for e in journal['entries'] if e['status'] == 'in_progress')
            seen.append((entry['steps'][-1]['status'], entry['steps'][-1]['argv'][:2]))
            if '--stats' in argv:
                stderr.write(b'perf: unknown option\n')
                return SimpleNamespace(returncode=129)
            return base(argv, stdout=stdout, stderr=stderr, **kwargs)
        code, result = self.analyze(report, tool=journaled)
        entry = result['entries'][0]
        self.assertEqual((code, entry['status']), (1, 'failed'))
        self.assertEqual(seen[0][0], 'in_progress')
        failed = entry['steps'][-1]
        self.assertEqual((failed['status'], failed['returncode']), ('failed', 129))
        self.assertEqual(failed['stderr_identity']['size_bytes'], len(b'perf: unknown option\n'))
        self.assertIsNotNone(failed['stderr_identity']['sha256'])
        self.assertTrue(entry['partial_files'])
        clock, holder = Clock(), {}
        real_tool = prof.run_tool
        def run_tool(*args, **kwargs):
            holder['deadline'] = args[4]
            return real_tool(*args, **kwargs)
        def expires(argv, stdout=None, timeout=None, **kwargs):
            stdout.write(b'partial output\n')
            clock.offset = holder['deadline'] + int(1e9) - clock.real()
            raise subprocess.TimeoutExpired(argv, timeout)
        with patch.object(p6, 'clock_ns', clock), patch.object(prof, 'run_tool', run_tool):
            code, result = self.analyze(report, budget=5000, tool=expires)
        entry = next(e for e in result['entries'] if e['status'] != 'skipped')
        self.assertEqual((code, entry['status'], entry['steps'][-1]['status']), (1, 'deadline', 'deadline'))
        partial = entry['steps'][-1]['stdout_identity']
        self.assertEqual((partial['sha256'], partial['hash_unavailable'], partial['size_bytes']),
                         (None, 'deadline passed', len(b'partial output\n')))
        self.assertTrue(all(p.get('sha256') is None for p in entry['partial_files']))
        def interrupted(argv, **kwargs):
            raise KeyboardInterrupt
        code, result = self.analyze(report, tool=interrupted)
        entry = next(e for e in result['entries'] if e['status'] != 'skipped')
        self.assertEqual((code, entry['status'], entry['steps'][-1]['status']), (1, 'interrupted', 'interrupted'))
        self.assertIn('KeyboardInterrupt', result['failure'])

    def test_non_perf_analysis_completes_without_an_interpretability_claim(self):
        """R3-4: no adequacy criterion outside perf; even content-free output is never interpretable."""
        for mode, tool in (('heaptrack', self.heaptrack_tool(report=b'x\n')), ('strace', None), ('strace-writes', None)):
            with self.subTest(mode):
                self.calls = []
                out = self.root / f'out-{mode}'
                self.capture(self.args(mode=mode, out=out))
                code, result = self.analyze(out / 'report.json', tool=tool)
                self.assertEqual((code, result['interpretation']['status'], result['analysis_complete']),
                                 (3, 'analysis_complete_no_adequacy_criterion', True))
                self.assertFalse(any(e['interpretable'] for e in result['entries']))
                self.assertTrue(all(e['adequacy'].startswith('no_adequacy_criterion') for e in result['entries']))

    def test_analysis_budget_is_sized_from_bound_bytes_and_caps(self):
        """R3-5: start rehash, per-capture hashing and the final reserve come from actual sizes and caps."""
        self.capture(self.args())
        report = p6.read_json(self.root / 'out/report.json')
        identities = prof.analysis_identities(report)
        code, result = self.analyze(self.root / 'out/report.json', tool=self.perf_tool())
        budget = result['budget']
        expected_final = prof.hash_s(sum(i['size_bytes'] for i in identities)) + prof.FINAL_FLOOR_S
        artifact = report['samples'][4]['artifacts'][0]['size_bytes']
        self.assertEqual(budget['final_reserve_s'], expected_final)
        self.assertEqual(budget['per_capture_s'][0], 2 * prof.ANALYSIS_TIMEOUT_S + 30 +
                         prof.hash_s(2 * artifact + 4 * prof.ANALYSIS_FILE_BYTES))
        self.assertEqual(budget['required_s'], budget['start_rehash_s'] + sum(budget['per_capture_s']) + expected_final)
        code, result = self.analyze(self.root / 'out/report.json', budget=budget['required_s'] - 5,
                                    tool=_refuse('tool when the sized budget does not fit'))
        self.assertIn('not started', result['failure'])

    def test_probe_output_hash_and_symbol_parse_are_deadline_bounded(self):
        """R3-6: after expiry only sizes are recorded; the symbol parse checks the deadline per line."""
        holder, clock = {}, Clock()
        class Budget(p6.WindowBudget):
            def __init__(self, seconds):
                super().__init__(seconds)
                holder['budget'] = self
        def probe(argv, stdout=None, **kwargs):
            value = self.probe(argv, stdout=stdout, **kwargs)
            if len(self.probes) == 1:
                clock.offset = holder['budget'].deadline + int(1e9) - clock.real()
            return value
        with patch.object(p6, 'clock_ns', clock), patch.object(p6, 'WindowBudget', Budget):
            code, report = self.capture(self.args(), probe=probe)
        record = report['probes'][0]
        self.assertEqual((code, len(self.probes), self.calls), (1, 1, []))
        self.assertEqual((record['stdout']['sha256'], record['stdout']['hash_unavailable']), (None, 'deadline passed'))
        self.assertIn('DeadlineExceeded', record['error'])
        directory = self.base / 'probe-direct'
        directory.mkdir()
        lines = b"Symbol table '.symtab' contains 999 entries:\n" + b'     1: 0000000000001000    10 FUNC    G D 1 f\n' * 999
        def symbols(argv, stdout=None, **kwargs):
            stdout.write(lines)
            return SimpleNamespace(returncode=0)
        ticks, base = [0], p6.clock_ns()
        def ticking():
            ticks[0] += 1
            return base + ticks[0] * int(1e9)
        records = []
        with patch.object(subprocess, 'run', side_effect=symbols), patch.object(p6, 'clock_ns', ticking), \
                self.assertRaises(prof.DeadlineExceeded):
            prof.run_probe(dict(name='symbols', kind='symbols', role='rust-symbolized', argv=['readelf']), directory,
                           records, base + int(40e9))
        self.assertIn('parsing', records[0]['error'])
        self.assertLess(ticks[0], 60)

    def test_prepare_refuses_cleanly_while_the_common_lock_is_held(self):
        """R3-7: prepare returns 2 and writes no sidecar when the offline lock is busy."""
        @contextlib.contextmanager
        def busy():
            raise BlockingIOError('window active')
            yield
        args = SimpleNamespace(out=self.root / 'busy-sidecar.json', parent_manifest=self.parent,
                               parent_sha256=p6.file_info(self.parent)['sha256'])
        with patch.object(prof, 'offline_lock', busy), \
                patch.object(p6, 'read_json_with_identity', _refuse('read before lock')), quiet():
            self.assertEqual(prof.prepare_command(args), 2)
        self.assertFalse(args.out.exists())

    def test_no_original_process_signal_or_lock_callable_is_reachable_under_another_name(self):
        """R3-8 (scan adopted from review R3 alias_scan): every alias of a guarded original is guarded too."""
        originals = {id(value): key for key, value in ORIGINALS.items() if key != (fcntl, 'fcntl')}
        found = []
        for name, module in list(sys.modules.items()):
            if name == __name__:
                continue
            for attr, value in list(getattr(module, '__dict__', {}).items()):
                key = originals.get(id(value))
                if key and not (module is key[0] and attr == key[1]):
                    found.append(f'{name}.{attr}')
        self.assertEqual(found, [])

    # Review R4 corrections.

    def test_interrupted_end_checks_leave_no_positive_claim_and_save_inside_the_lock(self):
        """R4-1 (adopted from review R4 probes r4_interrupt_during_end_identity/harness_check), plus repeats."""
        self.capture(self.args())
        report = self.root / 'out/report.json'
        def nth(target, name, raise_on, label):
            real, calls = getattr(target, name), [0]
            def wrapper(*args, **kwargs):
                calls[0] += 1
                if calls[0] in raise_on:
                    raise KeyboardInterrupt(label)
                return real(*args, **kwargs)
            return patch.object(target, name, wrapper)
        cases = {'end identity check, signal 2': [nth(prof, 'changed_identities', {2}, 'signal 2')],
                 'end harness check, signal 15': [nth(prof, 'verify_harness', {2}, 'signal 15')],
                 'end check then interpretation interrupted': [nth(prof, 'changed_identities', {2}, 'signal 2'),
                                                               nth(prof, 'finish_analysis', {1}, 'signal 15')],
                 'every interpretation attempt interrupted': [nth(prof, 'changed_identities', {2}, 'signal 2'),
                                                              nth(prof, 'finish_analysis', {1, 2}, 'signal 1')]}
        for name, patches in cases.items():
            with self.subTest(name):
                events, real_write = [], p6.write_json
                def write(path, value):
                    events.append(('write', Path(path).name, value.get('state')))
                    return real_write(path, value)
                with contextlib.ExitStack() as stack:
                    for patcher in patches:
                        stack.enter_context(patcher)
                    stack.enter_context(patch.object(p6, 'write_json', write))
                    code, result = self.analyze(report, tool=self.perf_tool(), lock=lambda: tracking_lock(events)())
                self.assertEqual(code, 1)
                self.assertFalse(any(e['interpretable'] for e in result['entries']))
                self.assertEqual(result['interpretation']['status'], 'analysis_incomplete')
                self.assertEqual(result['interpretation']['interpretable_captures'], 0)
                self.assertTrue(all(row['interpretable_captures'] == 0 for row in (result.get('roles') or {}).values()))
                self.assertEqual(self.printed['interpretation'], 'analysis_incomplete')
                self.assertIn('KeyboardInterrupt', result['failure'])
                self.assertTrue(result['quarantined'] and result['state'] == 'final' and not result['analysis_complete'])
                self.assertFalse(result['end_identity_check']['passed'] and result['analysis_harness_end']['passed'])
                exit_at = events.index('lock-exit')
                self.assertEqual([e for e in events[exit_at:] if e[0] == 'write'], [])
                self.assertEqual(events[exit_at - 1][:3], ('write', 'analysis.json', 'final'))

    def test_analysis_output_must_be_disjoint_from_capture_tree_and_provenance(self):
        """R4-2 (adopted from review R4 probe r4_r3_1_out_inside_capture_tree_not_refused)."""
        self.capture(self.args())
        report = self.root / 'out/report.json'
        entry = self.root / 'out' / p6.read_json(report)['samples'][4]['directory']
        (self.root / 'alias-to-capture').symlink_to(self.root / 'out', target_is_directory=True)
        before = self.snapshot(self.root / 'out')
        listing = sorted(str(p.relative_to(self.root / 'out')) for p in (self.root / 'out').rglob('*'))
        refused = {'capture root': self.root / 'out' / 'analysis', 'entry folder': entry / 'analysis',
                   'symlink alias into the capture root': self.root / 'alias-to-capture' / 'analysis',
                   'frozen parent provenance': self.base / 'parent' / 'analysis',
                   'symbolized binary folder': self.base / 'rsym' / 'analysis'}
        for name, out in refused.items():
            with self.subTest(name):
                args = SimpleNamespace(report=report, report_sha256=p6.file_info(report)['sha256'], out=out,
                                       budget_s=100000, sidecar_sha256=p6.file_info(self.sidecar)['sha256'])
                with patch.object(subprocess, 'run', side_effect=_refuse('tool for a refused --out')), quiet():
                    self.assertEqual(prof.analyze_command(args), 2)
                self.assertFalse(Path(out).exists())
        self.assertEqual(self.snapshot(self.root / 'out'), before)
        self.assertEqual(sorted(str(p.relative_to(self.root / 'out')) for p in (self.root / 'out').rglob('*')), listing)
        self.assertEqual(self.analyze(report, tool=self.perf_tool())[0], 0)  # disjoint sibling: the control passes

    def test_artifact_must_be_the_single_mode_artifact(self):
        """R4-3 (adopted from review R4 probe r4_r3_1_forged_inside_capture_root); forged in the capture root."""
        self.capture(self.args())
        report = self.root / 'out/report.json'
        first = lambda v: next(e for e in v['samples'] if e.get('interpretable'))
        def launcher(v):
            launch = first(v)['launcher']
            first(v)['artifacts'] = [dict(path=launch['path'], sha256=launch['sha256'], size_bytes=launch['size_bytes'])]
        def two(v):
            launch = first(v)['launcher']
            first(v)['artifacts'].insert(0, dict(path=launch['path'], sha256=launch['sha256'],
                                                 size_bytes=launch['size_bytes']))
        def on_control(v):
            control = v['samples'][0]
            control['artifacts'] = [dict(path=str(self.root / 'out' / control['directory'] / 'perf.data'),
                                         sha256='0' * 64, size_bytes=1)]
        cases = {'launcher script': (launcher, 'is not the perf artifact'),
                 'two artifacts, launcher first': (two, 'exactly one artifact'),
                 'artifact on a control entry': (on_control, 'is a control and must name no artifact'),
                 'artifact elsewhere': (lambda v: first(v)['artifacts'][0].update(path=str(self.sidecar)),
                                        'artifact is outside its capture folder')}
        control = self.forge(report, lambda v: None, 'unchanged-artifact-copy.json', folder=self.root / 'out')
        self.assertEqual(self.analyze(control, tool=self.perf_tool())[0], 0)
        for name, (change, error) in cases.items():
            with self.subTest(name):
                forged = self.forge(report, change, f'forged-artifact-{name.replace(" ", "-")}.json',
                                    folder=self.root / 'out')
                code, result = self.analyze(forged, tool=_refuse('tool with a substituted artifact'))
                self.assertEqual(code, 1)
                self.assertIn(error, result['failure'])

    def test_analysis_owns_abort_signals_while_tools_run(self):
        """R4-5 / M10: SIGTERM during a tool takes the cleanup path; handlers are restored afterwards."""
        self.capture(self.args())
        before = signal.getsignal(signal.SIGTERM)
        def tool(argv, **kwargs):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        code, result = self.analyze(self.root / 'out/report.json', tool=tool)
        entry = next(e for e in result['entries'] if e['status'] != 'skipped')
        self.assertEqual((code, entry['status'], result['state']), (1, 'interrupted', 'final'))
        self.assertIn('KeyboardInterrupt', result['failure'])
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_analysis_tools_run_under_the_analysis_file_cap(self):
        """R4-5 / M11: every analysis tool runs with RLIMIT_FSIZE at ANALYSIS_FILE_BYTES."""
        self.capture(self.args())
        caps, base = [], self.perf_tool()
        def tool(argv, **kwargs):
            caps.append(self.fsize)
            return base(argv, **kwargs)
        self.assertEqual(self.analyze(self.root / 'out/report.json', tool=tool)[0], 0)
        self.assertEqual(set(caps), {prof.ANALYSIS_FILE_BYTES})

    def test_capture_finalization_past_the_deadline_is_an_overrun(self):
        """R4-5 / M15: a capture whose finalization ends after the window deadline does not pass."""
        clock, holder = Clock(), {}
        class Budget(p6.WindowBudget):
            def __init__(self, seconds):
                super().__init__(seconds)
                holder['budget'] = self
        real = prof.changed_identities
        def changed(identities, deadline_ns=None, progress=None):
            value = real(identities, deadline_ns, progress)
            if progress is not None:  # finalize's end check
                clock.offset = holder['budget'].deadline + int(1e9) - clock.real()
            return value
        with patch.object(p6, 'clock_ns', clock), patch.object(p6, 'WindowBudget', Budget), \
                patch.object(prof, 'changed_identities', changed):
            code, report = self.capture(self.args())
        self.assertEqual((code, report['finalization']['window_overrun'], report['passed']), (1, True, False))

    def test_each_analysis_entry_needs_its_reserve_before_it_starts(self):
        """R4-5 / M09: an entry whose reserve no longer fits is not started (the deadline would still allow it)."""
        self.capture(self.args())
        clock, holder, calls = Clock(), {}, []
        class Budget(p6.WindowBudget):
            def __init__(self, seconds):
                super().__init__(seconds)
                holder['budget'] = self
        base = self.perf_tool()
        def tool(argv, **kwargs):
            calls.append(argv)
            if len(calls) == 2:  # end of the first entry: 300 s left, below one entry reserve plus the final reserve
                clock.offset = holder['budget'].deadline - int(300e9) - clock.real()
            return base(argv, **kwargs)
        with patch.object(p6, 'clock_ns', clock), patch.object(p6, 'WindowBudget', Budget):
            code, result = self.analyze(self.root / 'out/report.json', tool=tool)
        self.assertEqual((code, len(calls)), (1, 2))
        self.assertIn('budget or disk headroom exhausted', result['failure'])

    def test_each_probe_needs_its_reserve_before_it_starts(self):
        """R4-5 / M14: a probe that would eat into the final reserve is not started."""
        clock, holder = Clock(), {}
        class Budget(p6.WindowBudget):
            def __init__(self, seconds):
                super().__init__(seconds)
                holder['budget'] = self
        real_captures = prof.captures
        def captures(*args):
            holder['report'] = args[5]
            return real_captures(*args)
        def probe(argv, stdout=None, **kwargs):
            value = self.probe(argv, stdout=stdout, **kwargs)
            # Work deadline still 30 s ahead, but one probe plus the final reserve no longer fits.
            final = holder['report']['partition_budget']['final_reserve_s']
            clock.offset = holder['budget'].deadline - int((final + 30) * 1e9) - clock.real()
            return value
        with patch.object(p6, 'clock_ns', clock), patch.object(p6, 'WindowBudget', Budget), \
                patch.object(prof, 'captures', captures):
            code, report = self.capture(self.args(), probe=probe)
        self.assertEqual((code, len(self.probes), self.calls), (1, 1, []))
        self.assertIn('cannot fit the next probe', report['failure'])

    def test_report_harness_must_equal_the_sidecar_harness(self):
        """R4-5 / M03: a report whose harness matches the running code but not its sidecar is refused."""
        self.capture(self.args())
        sidecar = self.forge(self.sidecar, lambda v: v['harness']['cases'].update(sha256='0' * 64), 'sidecar-m03.json')
        forged = self.forge(self.root / 'out/report.json',
                            lambda v: v['provenance'].update(sidecar=p6.file_info(sidecar)), 'report-m03.json',
                            folder=self.root / 'out')
        code, result = self.analyze(forged, tool=_refuse('tool with a mismatched sidecar harness'), sidecar=sidecar)
        self.assertEqual(code, 1)
        self.assertIn('report harness differs from the sidecar binding', result['failure'])

    def test_completed_step_is_journaled_before_the_next_action(self):
        """R4-5 / M08: a finished tool step is on disk as completed before its output is interpreted."""
        self.capture(self.args())
        seen, real = [], prof.capture_stack_status
        def status(stats):
            journal = p6.read_json(self.root / 'analysis-0/analysis.json')
            entry = next(e for e in journal['entries'] if e['status'] == 'in_progress')
            seen.append(entry['steps'][-1]['status'])
            return real(stats)
        with patch.object(prof, 'capture_stack_status', status):
            self.assertEqual(self.analyze(self.root / 'out/report.json', tool=self.perf_tool())[0], 0)
        self.assertEqual(set(seen), {'completed'})

    def test_stats_read_is_capped(self):
        """R4-5 / M18: oversized --stats output is unavailable, never parsed past the read cap."""
        self.capture(self.args())
        with patch.object(prof, 'STATS_READ_BYTES', 64):
            code, result = self.analyze(self.root / 'out/report.json', tool=self.perf_tool())
        self.assertEqual(code, 3)
        self.assertEqual({e['stats']['format'] for e in result['entries']}, {'stats too large'})
        self.assertEqual({e['stack_status'] for e in result['entries']}, {'unavailable'})

    def test_stats_read_requests_at_most_the_cap(self):
        """R4-5 / M18: the --stats output is read with a bounded size, not read whole then discarded."""
        self.capture(self.args())
        sizes = []
        class Stream:
            def __init__(self, stream):
                self.stream = stream
            def read(self, size=-1):
                sizes.append(size)
                return self.stream.read(size)
            def __getattr__(self, name):  # readinto (bounded hashing) and the rest go to the real file
                return getattr(self.stream, name)
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                self.stream.close()
        def bounded_open(file, mode='r', *args, **kwargs):
            stream = open(file, mode, *args, **kwargs)
            return Stream(stream) if str(file).endswith('perf-stats.txt') and mode == 'rb' else stream
        with patch.object(prof, 'open', bounded_open, create=True):
            self.assertEqual(self.analyze(self.root / 'out/report.json', tool=self.perf_tool())[0], 0)
        self.assertEqual(set(sizes), {prof.STATS_READ_BYTES + 1})

    def test_end_check_interrupt_is_recorded_before_interpretation(self):
        """R4-1 ordering, unit level: failure and quarantine are set before end_analysis interprets."""
        self.capture(self.args())
        report = p6.read_json(self.root / 'out/report.json')
        result = dict(mode='perf', entries=[dict(role='go-symbolized', status='analyzed', stack_status=
                      'sufficient_for_descriptive_stacks', stats=dict(sample_events=5000))], failure=None,
                      complete=True, quarantined=False, timing=dict(started_ns=p6.clock_ns()))
        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt('signal 2')
        with patch.object(prof, 'changed_identities', interrupted), self.assertRaises(KeyboardInterrupt):
            prof.end_analysis(result, report, p6.WindowBudget(1000))
        self.assertTrue(result['quarantined'])
        self.assertIn('end checks KeyboardInterrupt', result['failure'])
        self.assertEqual((result['interpretation']['status'], result['entries'][0]['interpretable']),
                         ('analysis_incomplete', False))

    def test_completion_requires_both_end_checks_to_have_passed(self):
        """R4-1, unit level: an otherwise clean perf analysis without passed end checks is never complete."""
        def result(**checks):
            return dict(mode='perf', failure=None, complete=True, quarantined=False, timing=dict(overrun=False),
                        entries=[dict(role='go-symbolized', status='analyzed', stats=dict(sample_events=5000),
                                      stack_status='sufficient_for_descriptive_stacks')], **checks)
        passed = dict(passed=True)
        for name, checks in (('no end checks', {}), ('identity only', dict(end_identity_check=passed)),
                             ('harness only', dict(analysis_harness_end=passed)),
                             ('identity not passed', dict(end_identity_check={}, analysis_harness_end=passed))):
            with self.subTest(name):
                value = result(**checks)
                prof.finish_analysis(value)
                self.assertEqual((value['analysis_complete'], value['exit_status'],
                                  value['entries'][0]['interpretable']), (False, 1, False))
        value = result(end_identity_check=passed, analysis_harness_end=passed)
        prof.finish_analysis(value)
        self.assertEqual((value['analysis_complete'], value['exit_status']), (True, 0))

    # Review R5 corrections.

    def test_final_save_failure_or_later_error_never_prints_a_positive(self):
        """R5-1 (adopted from review R5 probe r5_r4_1_signal_after_interpretation_before_save): the end checks
        passed, then the final save fails or is interrupted, or an error comes after it at unlock."""
        self.capture(self.args())
        report = self.root / 'out/report.json'
        def at_final_save(fault):
            real, fired = p6.write_json, []
            def write(path, value):
                # Fires once: any later write (for example after unlock) is then observed, not re-faulted.
                if Path(path).name == 'analysis.json' and value.get('state') == 'final' and not fired:
                    fired.append(True)
                    fault()
                return real(path, value)
            return write
        def edquot():
            raise OSError(122, 'Disk quota exceeded')
        def handler(sig):
            return lambda: signal.getsignal(sig)(sig, None)
        def failing_unlock(error):
            def lock():
                @contextlib.contextmanager
                def held():
                    self.events.append('lock-enter')
                    try:
                        yield
                    finally:
                        self.events.append('lock-exit')
                    raise error
                return held()
            return lock
        cases = {'final save EDQUOT': (dict(write=at_final_save(edquot)), False),
                 'SIGINT inside the final save': (dict(write=at_final_save(handler(signal.SIGINT))), False),
                 'SIGTERM inside the final save': (dict(write=at_final_save(handler(signal.SIGTERM))), False),
                 'error at unlock after the sealed save': (dict(lock=failing_unlock(OSError(5, 'unlock failed'))), True),
                 'signal at unlock after the sealed save': (dict(lock=failing_unlock(KeyboardInterrupt('signal 1'))),
                                                            True)}
        for name, (fault, sealed) in cases.items():
            with self.subTest(name):
                self.events, saved = [], []
                real_write = fault.get('write') or p6.write_json
                def write(path, value):
                    self.events.append(('write', value.get('state')))
                    real_write(path, value)
                    saved.append(json.loads(json.dumps(value)))
                with patch.object(p6, 'write_json', write):
                    code, result = self.analyze(report, tool=self.perf_tool(),
                                                lock=fault.get('lock') or (lambda: tracking_lock(self.events)()))
                self.assertEqual((code, self.printed['exit_status'], self.printed['interpretation']),
                                 (1, 1, 'analysis_incomplete'))
                self.assertEqual(self.printed['journal_sealed'], sealed)
                self.assertTrue(self.printed['failure'])
                exit_at = self.events.index('lock-exit')
                self.assertEqual([e for e in self.events[exit_at:] if e[0] == 'write'], [])
                self.assertEqual(result, saved[-1])  # the on-disk journal is exactly the last completed checkpoint
                if sealed:  # saved before the later error; left untouched, the exit code is authoritative
                    self.assertEqual(result['state'], 'final')
                else:
                    self.assertEqual((result['state'], result.get('interpretation')), ('in_progress', None))
                self.assertEqual(list(self.root.glob('analysis-*/**/*.partial-*')), [])

    def test_repeated_interrupt_after_a_positive_interpretation_retracts_roles(self):
        """R5-2 (adopted from review R5's two-signal race): interpretation #1 completes positive, a signal follows,
        the reseal is interrupted too; the fallback leaves no positive entry, role, count or status."""
        self.capture(self.args())
        real, calls = prof.finish_analysis, [0]
        def finish(result):
            calls[0] += 1
            if calls[0] == 1:
                real(result)
                self.assertEqual(result['interpretation']['status'], 'all_roles_interpretable')
                raise KeyboardInterrupt('signal 2')
            if calls[0] == 2:
                raise KeyboardInterrupt('signal 15')
            return real(result)
        with patch.object(prof, 'finish_analysis', finish):
            code, result = self.analyze(self.root / 'out/report.json', tool=self.perf_tool())
        self.assertEqual((code, result['state'], result['exit_status']), (1, 'final', 1))
        self.assertEqual(result['interpretation']['status'], 'analysis_incomplete')
        self.assertEqual(result['interpretation']['interpretable_captures'], 0)
        self.assertTrue(result['roles'])
        self.assertEqual({role: row['interpretable_captures'] for role, row in result['roles'].items()},
                         dict.fromkeys(result['roles'], 0))
        self.assertFalse(any(e['interpretable'] for e in result['entries']))
        self.assertEqual(self.printed['interpretation'], 'analysis_incomplete')

    def test_interpretable_capture_without_its_artifact_is_refused_before_tools(self):
        """R5-3: an interpretable capture names exactly one artifact; a missing one is refused, not an IndexError."""
        self.capture(self.args())
        report = self.root / 'out/report.json'
        first = lambda v: next(e for e in v['samples'] if e.get('interpretable'))
        for name, change in (('empty artifact list', lambda v: first(v).update(artifacts=[])),
                             ('artifacts key removed', lambda v: first(v).pop('artifacts'))):
            with self.subTest(name):
                forged = self.forge(report, change, f'forged-{name.replace(" ", "-")}.json', folder=self.root / 'out')
                code, result = self.analyze(forged, tool=_refuse('tool without an artifact'))
                self.assertEqual(code, 1)
                self.assertIn('must name exactly one artifact when interpretable', result['failure'])
                self.assertNotIn('IndexError', result['failure'])

    def test_heaptrack_artifact_name_rule(self):
        """R5-4 (Y16): a heaptrack entry's artifact must carry the heaptrack name; the same-prefix copy passes."""
        out = self.root / 'out-heaptrack'
        self.capture(self.args(mode='heaptrack', out=out))
        report = out / 'report.json'
        control = self.forge(report, lambda v: None, 'unchanged-heaptrack-copy.json', folder=out)
        artifact = Path(next(e for e in p6.read_json(report)['samples'] if e.get('interpretable'))['artifacts'][0]['path'])
        self.assertTrue(artifact.name.startswith('heaptrack'))
        self.assertEqual(self.analyze(control, tool=self.heaptrack_tool())[0], 3)
        def launcher(v):
            entry = next(e for e in v['samples'] if e.get('interpretable'))
            launch = entry['launcher']
            entry['artifacts'] = [dict(path=launch['path'], sha256=launch['sha256'], size_bytes=launch['size_bytes'])]
        forged = self.forge(report, launcher, 'forged-heaptrack-launcher.json', folder=out)
        code, result = self.analyze(forged, tool=_refuse('heaptrack tool on the launcher script'))
        self.assertEqual(code, 1)
        self.assertIn("artifact name 'launch' is not the heaptrack artifact", result['failure'])


if __name__ == '__main__':
    unittest.main()
