#!/usr/bin/env python3
"""Bounded PTY attribution captures; plan is read-only and never launches children."""
import argparse
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import re
import time
import selectors
import termios
from contextlib import ExitStack
from dataclasses import dataclass

import phase7 as p7
import phase7_bench as bench
import profile_external as external

CASES = ('sql-window20000', 'dense-metrics1000', 'editor')
MODES = ('perf', 'strace', 'strace-writes', 'heaptrack')
ARTIFACTS = ('go-release', 'rust-release', 'go-symbolized', 'rust-symbolized')


@dataclass(frozen=True)
class Options:
    cases: tuple = CASES
    modes: tuple = ('perf', 'strace', 'heaptrack')
    repetitions: int = 1
    budget_seconds: float = 600
    max_sessions: int = 64
    session_timeout: float = 60
    theme: str = 'dark'
    size: tuple = (120, 40)
    perf_event: str = 'cycles:u'
    campaign_id: str = ''

    def validate(self):
        if not 1 <= self.repetitions <= 10 or not 1 <= self.max_sessions <= 512:
            raise ValueError('finite repetitions/session cap required')
        if not 1 <= self.budget_seconds <= 3600 or not 1 <= self.session_timeout <= 300:
            raise ValueError('finite campaign/session wall-clock caps required')
        if not self.cases or not self.modes or len(set(self.cases)) != len(self.cases) or len(set(self.modes)) != len(self.modes):
            raise ValueError('nonempty distinct cases/modes required')
        if any(c not in CASES for c in self.cases) or any(m not in MODES for m in self.modes):
            raise ValueError('unknown profiler case/mode')
        if self.perf_event not in ('cycles:u', 'cpu-clock:u'):
            raise ValueError('unsupported perf event')


def plan(options):
    options.validate()
    common = bench.Options(themes=(options.theme,), sizes=(options.size,), idle_seconds=1,
                           session_timeout=options.session_timeout, observer_policy='resource-attribution')
    available = {c['name']: c for c in bench.campaign_cases(common)}
    suffix = f'{options.size[0]}x{options.size[1]}-{options.theme}-resource-attribution'
    names = {'sql-window20000': 'sql-scroll-20000-' + suffix,
             'dense-metrics1000': 'promql-dense-1000-' + suffix, 'editor': 'sql-editor-' + suffix}
    cases = [{**copy.deepcopy(available[names[c]]), 'profile_case': c} for c in options.cases]
    controls, captures = [], []
    for case in cases:
        for repetition in range(options.repetitions):
            # All controls for all cases precede every instrumented capture.
            for artifact in ARTIFACTS:
                controls.append({'case': case['profile_case'], 'repetition': repetition,
                                 'mode': 'control', 'artifact': artifact})
            for mode in options.modes:
                for language in (('rust',) if mode == 'heaptrack' else ('go', 'rust')):
                    captures.append({'case': case['profile_case'], 'repetition': repetition,
                                     'mode': mode, 'artifact': language + '-symbolized'})
    count = len(controls) + len(captures)
    if count > options.max_sessions:
        raise ValueError(f'{count} planned sessions exceed finite cap {options.max_sessions}')
    return {'schema_version': 1, 'kind': 'attribution only; no release timing campaign',
            'resize_signal_policy': bench.RESIZE_SIGNAL_POLICY,
            'options': options.__dict__, 'cases': cases, 'controls': controls, 'captures': captures,
            'session_count': count, 'definitions_sha256': p7.digest(cases),
            'observer_policy': 'resource-attribution',
            'observer_config': bench.OBSERVER_POLICIES['resource-attribution'],
            'limits': {'raw_profile_bytes': MAX_PROFILE_BYTES, 'analysis_output_bytes': MAX_ANALYSIS_BYTES,
                       'trace_line_bytes': MAX_TRACE_LINE_BYTES, 'write_identities': MAX_WRITE_IDENTITIES,
                       'analysis_group_cleanup_seconds': ANALYSIS_CLEANUP_SECONDS},
            'native_spawn_source_sha256': bench.sha_file(bench.ROOT / 'harness/phase7_spawn.c'),
            'processing': 'after all captures, while the same bounded quiet guard is held'}


def load_artifacts(path, *, with_manifest=False):
    config, manifest_identity = bench.json_snapshot(path)
    if not isinstance(config, dict) or set(config) != set(ARTIFACTS):
        raise ValueError('artifact manifest must declare exactly the four release/symbolized artifacts')
    identities = {}
    for language, role in (('go', 'reference'), ('rust', 'candidate')):
        if any(not isinstance(config[language + '-' + kind], dict) or
               not isinstance(config[language + '-' + kind].get('binary'), str) or
               not isinstance(config[language + '-' + kind].get('metadata'), str) for kind in ('release', 'symbolized')):
            raise ValueError('artifact entries must contain binary and metadata paths')
        release = config[language + '-release']
        identities[language + '-release'] = bench.artifact(release['binary'], release['metadata'], role)
        symbolized = config[language + '-symbolized']
        identity = p7.phase1.binary_identity(Path(symbolized['binary']))
        metadata, snapshot = bench.json_snapshot(symbolized['metadata'])
        baseline = identities[language + '-release']['build_provenance']
        if metadata.get('binary_sha256') != identity['sha256'] or metadata.get('optimized') is not True or metadata.get('debug_symbols') is not True:
            raise ValueError(language + ': optimized-symbolized provenance must match executable')
        if any(metadata.get(k) != baseline[k] for k in ('source_commit', 'source_tree_sha256', 'language')):
            raise ValueError(language + ': symbolized sources must match release sources')
        identities[language + '-symbolized'] = {'identity': identity, 'build_provenance': metadata,
            'provenance_path': snapshot['path'], 'provenance_size_bytes': snapshot['size_bytes'],
            'provenance_file_sha256': snapshot['sha256']}
    return (identities, manifest_identity) if with_manifest else identities


def tool_identity(path):
    if path is None or not Path(path).is_file():
        return {'status': 'unavailable', 'reason': 'tool/library not found', 'identity': None}
    return {'status': 'available', 'identity': external.manifest.file_identity(Path(path).resolve())}


def tools(preload, interpreter):
    return {name: tool_identity(path) for name, path in {
        'perf': shutil.which('perf'), 'strace': shutil.which('strace'),
        'heaptrack_preload': preload, 'heaptrack_interpreter': interpreter,
        'heaptrack_print': shutil.which('heaptrack_print')}.items()}


def tool_for(mode):
    return 'strace' if mode.startswith('strace') else 'heaptrack_preload' if mode == 'heaptrack' else mode


def unchanged(identity, guard=None):
    actual = bounded_identity(identity['path'], guard) if guard is not None else external.manifest.file_identity(identity['path'])
    if actual != identity:
        raise ValueError('artifact changed: ' + identity['path'])


class ProfileCapture:
    """Keep sample's actual CLI identity; prefix only its owned execute boundary."""
    def __init__(self, guard, mode, tool, directory, event, identity=None):
        self.guard, self.mode, self.tool = guard, mode, tool
        self.directory, self.event = Path(directory), event
        self.command = None
        self.identity = identity

    def continue_capture(self):
        failure = self.guard.continue_running()
        if failure:
            return failure
        try:
            if any(p.is_file() and p.stat().st_size > MAX_PROFILE_BYTES for p in self.directory.iterdir()):
                return 'profiler output exceeds declared byte cap'
        except OSError as error:
            return 'cannot verify profiler output size: ' + str(error)
        return None

    def __call__(self, command, env, cwd, case, fixture=None):
        self.guard.check(force=True)
        if self.identity is not None:
            bench.check_command(command, self.identity, case)
        unchanged(self.tool, self.guard)
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.mode == 'heaptrack':
            # Preload ONLY the actual Rust binary, never a shell or Go process.
            prefix = external.command(self.mode, self.tool['path'], command[0], self.directory, self.event)
            actual = [*prefix, *command[1:]]
            diagnostics = {'policy': 'in-process preload shares CLI stderr; exact control comparison; no filtering',
                           'path': 'capture/stderr.bin'}
        else:
            # External profiler stderr has its own file. Child shim restores CLI
            # stderr from inheritable FD 3, then closes it before native exec.
            launcher = self.directory / 'launch'
            diagnostic_path = self.directory / 'profiler.stderr'
            launcher.write_text('#!/bin/sh\nexec 3>&2\nexec 2>' + shlex.quote(str(diagnostic_path)) + '\nexec "$@"\n')
            launcher.chmod(0o700)
            prefix = external.command(self.mode, self.tool['path'], '/bin/sh', self.directory, self.event)
            actual = [str(launcher), *prefix, '-c', 'exec 2>&3; exec 3>&-; exec "$@"', 'phase7-cli', *command]
            diagnostics = {'policy': 'external profiler FD2 separate; native CLI FD2 restored, compared exactly',
                           'path': str(diagnostic_path)}
        self.command = actual
        result = p7.execute(actual, env, cwd, case, fixture, proc_sampler=bench.native_proc_sample,
                            continue_running=self.continue_capture, readiness_fn=bench.Readiness(case), spawn_helper_build=False,
                            observer_config=bench.OBSERVER_POLICIES['resource-attribution'],
                            resize_signal_policy=bench.RESIZE_SIGNAL_POLICY)
        resize_issues = bench.resize_policy_issues(result, case)
        if resize_issues:
            result['failure'] = result.get('failure') or '; '.join(resize_issues)
        result['capture_command'] = list(command)
        result['observer_policy'] = 'resource-attribution'
        result['profile'] = {'mode': self.mode, 'command': actual, 'profiler_prefix': prefix,
                             'actual_cli_command': command, 'profiler_identity': self.tool,
                             'diagnostics': diagnostics, 'child_environment': dict(sorted(env.items()))}
        result['metrics']['scope'] = ('instrumented Rust with allocator hooks/raw-record IO and env startup' if self.mode == 'heaptrack'
                                     else 'profiler/wrapper process tree including child and shell overhead; not isolated CLI')
        result['metrics']['release_claim'] = False
        result['profile']['pid_attribution'] = {'wrapper_pid': result['metrics'].get('native_spawn', {}).get('cli_pid'),
            'native_cli_pid': result['metrics'].get('native_spawn', {}).get('cli_pid') if self.mode == 'heaptrack' else None,
            'scope': 'same PID after env exec' if self.mode == 'heaptrack' else 'descendant pb PID unknown; proc sampler is wrapper PID'}
        bench.archive_execute(result, self.directory / 'capture/execute')
        return result


def denied(directory):
    path = Path(directory) / 'profiler.stderr'
    if not path.is_file():
        return False
    with path.open('rb') as stream:
        data = stream.read(64 * 1024).lower()
    return any(token in data for token in (b'permission denied', b'operation not permitted', b'perf_event_paranoid',
                                           b'no permission to enable', b'access to performance monitoring'))


MAX_PROFILE_BYTES = 512 * 1024 * 1024
MAX_ANALYSIS_BYTES = 256 * 1024 * 1024
MAX_TRACE_LINE_BYTES = 64 * 1024
ANALYSIS_CLEANUP_SECONDS = 2
MAX_WRITE_IDENTITIES = 65536


def bounded_identity(path, guard, *, maximum=MAX_PROFILE_BYTES):
    """Guard hashing/size work in 1 MiB chunks; never silently truncate."""
    path = Path(path)
    guard.check(force=True)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('missing/symlink/oversized analysis artifact: ' + str(path))
    import hashlib
    digest, count = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while True:
            guard.check()
            chunk = stream.read(1 << 20)
            if not chunk:
                break
            count += len(chunk)
            if count > maximum:
                raise ValueError('analysis artifact exceeds byte cap')
            digest.update(chunk)
    guard.check(force=True)
    if path.is_symlink() or path.stat().st_size != count:
        raise ValueError('analysis artifact changed while hashing')
    return {'path': str(path.resolve()), 'size_bytes': count, 'sha256': digest.hexdigest()}


def group_members(native):
    """Same-group live members while the native WNOWAIT leader is reserved."""
    if not native.same_process() or native.released:
        raise ValueError('analysis leader identity/reservation unavailable')
    members = []
    for index, path in enumerate(Path('/proc').iterdir()):
        if index > 32768:
            raise ValueError('analysis process-group scan exceeds finite entry cap')
        if not path.name.isdecimal():
            continue
        try:
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == native.pid and int(fields[3]) == native.pid and fields[0] not in ('Z', 'X'):
                members.append({'pid': int(path.name), 'start_ticks': fields[19], 'state': fields[0]})
        except (FileNotFoundError, ProcessLookupError):
            continue
    return members


def cleanup_analysis(native):
    """Always clean the PID-bound group before releasing the exited leader."""
    evidence = {'strategy': 'reserved WNOWAIT leader + pidfd group signal before release', 'remaining': []}
    problems = []
    try:
        if native.exec_error is None:
            if not native.signal_group(signal.SIGKILL):
                raise ValueError('analysis pidfd group signal unavailable')
            native.wait_exited()
            deadline = time.monotonic() + ANALYSIS_CLEANUP_SECONDS
            with selectors.DefaultSelector() as selector:
                while True:
                    evidence['remaining'] = group_members(native)
                    if not evidence['remaining']:
                        break
                    if time.monotonic() >= deadline:
                        raise ValueError('analysis same-group members survived bounded cleanup')
                    selector.select(.01)
        native.release()
        evidence['exit'] = native.wait()
    except Exception as error:
        problems.append(repr(error))
    finally:
        problems.extend(native.close(abort=bool(problems)))
    evidence['issues'] = problems
    return evidence


def process_command(argv, directory, name, guard, stdin_path=None):
    """Analysis uses the shared PID-bound native group, never late numeric killpg."""
    guard.check(force=True)
    helper = bench.cached_spawn(probe=False)
    directory = Path(directory)
    out, err = directory / (name + '.stdout'), directory / (name + '.stderr')
    record = {'argv': argv, 'status': 'starting', 'stdout_path': str(out), 'stderr_path': str(err),
              'native_spawn': helper, 'max_analysis_bytes': MAX_ANALYSIS_BYTES}
    bench.write_json(directory / (name + '.json'), record)
    native = None
    try:
        with ExitStack() as stack:
            source = stack.enter_context(Path(stdin_path).open('rb')) if stdin_path is not None else stack.enter_context(open(os.devnull, 'rb'))
            stdout, stderr = stack.enter_context(out.open('wb')), stack.enter_context(err.open('wb'))
            native = p7.NativeChild(helper, argv, {**bench.isolated_env('dark'), 'DEBUGINFOD_URLS': ''},
                                   directory, True, source.fileno(), stdout.fileno(), stderr.fileno(),
                                   (source.fileno(), stdout.fileno(), stderr.fileno()))
            try:
                if native.exec_error:
                    raise ValueError('analysis setup/exec failed: ' + repr(native.exec_error))
                with selectors.DefaultSelector() as selector:
                    selector.register(native.socket, selectors.EVENT_READ)
                    while True:
                        guard.check()
                        if any(path.stat().st_size > MAX_ANALYSIS_BYTES for path in (out, err)):
                            raise ValueError('analysis output exceeds declared byte cap')
                        if native.poll():
                            break
                        selector.select(.05)
            finally:
                record['cleanup'] = cleanup_analysis(native)
        if record['cleanup']['issues']:
            raise ValueError('analysis cleanup failed: ' + repr(record['cleanup']['issues']))
        code = os.waitstatus_to_exitcode(record['cleanup']['exit']['status'])
        guard.check(force=True)
        record.update(status='complete' if code == 0 else 'failed', returncode=code,
                      stdout=bounded_identity(out, guard, maximum=MAX_ANALYSIS_BYTES),
                      stderr=bounded_identity(err, guard, maximum=MAX_ANALYSIS_BYTES))
        if code != 0 or not record['stdout']['size_bytes']:
            raise ValueError('failed/empty profiler processing: ' + name)
        bench.write_json(directory / (name + '.json'), record)
        return record
    except Exception as error:
        record.update(status='failed', error=repr(error))
        bench.write_json(directory / (name + '.json'), record)
        raise


def write_counts_bounded(path, directory, guard):
    """Same unfinished/resumed semantics, with capped lines and cancellation."""
    counts, pending, consumed, lines = {}, {}, 0, 0
    evidence = {'status': 'parsing', 'writes_by_fd': counts, 'consumed_bytes': 0, 'lines': 0,
                'limits': {'trace_line_bytes': MAX_TRACE_LINE_BYTES, 'raw_bytes': MAX_PROFILE_BYTES,
                           'write_identities': MAX_WRITE_IDENTITIES}}
    target = Path(directory) / 'write-analysis.json'
    try:
        with Path(path).open('rb') as stream:
            while True:
                guard.check()
                data = stream.readline(MAX_TRACE_LINE_BYTES + 1)
                if not data:
                    break
                consumed += len(data)
                lines += 1
                if len(data) > MAX_TRACE_LINE_BYTES or consumed > MAX_PROFILE_BYTES:
                    raise ValueError('write trace exceeds declared line/file byte cap')
                line = data.decode('utf-8', errors='strict')
                start = re.match(r"^(\d+)\s+(writev?)\((0x[0-9a-f]+|[0-9]+),", line)
                resumed = re.match(r"^(\d+)\s+<\.\.\. (writev?) resumed>", line)
                if start:
                    pid, call, fd = start.groups()
                    if len(pid) > 16 or len(fd) > 18:
                        raise ValueError('write trace numeric identity exceeds bound')
                    fd = str(int(fd, 0))
                    if pid in pending:
                        raise ValueError('overlapping unfinished writes for one thread')
                    counts.setdefault(fd, {'calls': 0, 'completed': 0, 'bytes': 0, 'errors': 0, 'interrupted': 0})['calls'] += 1
                    if len(counts) > MAX_WRITE_IDENTITIES or len(pending) >= MAX_WRITE_IDENTITIES:
                        raise ValueError('write trace identity count exceeds bound')
                    if '<unfinished ...>' in line:
                        pending[pid] = (call, fd)
                        continue
                elif resumed:
                    pid, call = resumed.groups()
                    previous = pending.pop(pid, None)
                    if previous is None or previous[0] != call:
                        raise ValueError('unmatched resumed write')
                    fd = previous[1]
                elif not line.strip() or re.match(r'^\d+\s+(---|\+\+\+)', line):
                    continue
                else:
                    raise ValueError('unexpected write trace record')
                if re.search(r'= \?(?:\s|$)', line):
                    counts[fd]['interrupted'] += 1
                    continue  # No numeric bytes attributed to this interrupted call.
                result = re.search(r'= (-?0x[0-9a-f]+|-?[0-9]+)(?:\s|$)', line)
                if not result:
                    raise ValueError('write result unavailable')
                if len(result[1]) > 19:
                    raise ValueError('write result exceeds native integer bound')
                value = int(result[1], 0)
                counts[fd]['completed'] += 1
                counts[fd]['errors' if value < 0 else 'bytes'] += 1 if value < 0 else value
        if pending:
            raise ValueError('incomplete write trace')
        guard.check(force=True)
        evidence['status'] = 'complete'
        return counts
    except Exception as error:
        evidence.update(status='failed', error=repr(error), pending=pending)
        raise
    finally:
        evidence.update(consumed_bytes=consumed, lines=lines)
        bench.write_json(target, evidence)


def analyze(mode, profiles, directory, toolset, guard, *, used_tools=None):
    identity = profiles[0]
    if bounded_identity(identity['path'], guard) != identity:
        raise ValueError('raw profile identity changed before analysis')
    directory = Path(directory)
    if mode == 'strace':
        # -c data is already the counts artifact. No synthetic zero counts.
        analysis = {'kind': 'strace counts', 'artifact': identity}
    elif mode == 'strace-writes':
        guard.check(force=True)
        analysis = {'writes_by_fd': write_counts_bounded(identity['path'], directory, guard)}
        guard.check(force=True)
    elif mode == 'perf':
        if used_tools is not None:
            used_tools.add('perf')
        tool = toolset['perf']['identity']
        unchanged(tool, guard)
        analysis = process_command([tool['path'], 'report', '--stdio', '--no-children', '--percent-limit',
                                    '0.5', '-i', identity['path']], directory, 'perf-report', guard)
    else:
        for name in ('heaptrack_interpreter', 'heaptrack_print'):
            if toolset[name]['status'] != 'available':
                raise FileNotFoundError('processing tool unavailable: ' + name)
            if used_tools is not None:
                used_tools.add(name)
            unchanged(toolset[name]['identity'], guard)
        decoded = process_command([toolset['heaptrack_interpreter']['identity']['path']], directory,
                                  'heap-interpret', guard, identity['path'])
        compressed = directory / 'interpreted.gz'
        with Path(decoded['stdout']['path']).open('rb') as source, gzip.open(compressed, 'wb') as target:
            for chunk in iter(lambda: source.read(1 << 20), b''):
                guard.check()
                target.write(chunk)
                if target.tell() > MAX_ANALYSIS_BYTES:
                    raise ValueError('compressed interpretation exceeds analysis byte cap')
        analysis = {'interpretation': decoded, 'print': process_command(
            [toolset['heaptrack_print']['identity']['path'], '-m', '0', '-f', str(compressed)],
            directory, 'heap-print', guard)}
    if bounded_identity(identity['path'], guard) != identity:
        raise ValueError('raw profile identity changed after analysis')
    return analysis


def profile_artifacts(mode, directory, guard):
    names = {'perf': 'perf.data', 'strace': 'strace.txt', 'strace-writes': 'writes.trace'}
    directory = Path(directory)
    paths = list(directory.glob('heaptrack*')) if mode == 'heaptrack' else [directory / names[mode]]
    if len(paths) != 1:
        raise ValueError('missing or ambiguous profile capture')
    identity = bounded_identity(paths[0], guard)
    if not identity['size_bytes']:
        raise ValueError('empty profile capture')
    return [identity]


def write_byte_diagnostic(writes, result):
    traced = writes.get('1', {}).get('bytes')
    captured = result['metrics']['stdout_bytes']
    tty = [result.get(key, {}) for key in ('termios_before', 'termios_after')]
    tty.extend(frame.get('termios', {}) for frame in result.get('frames', []))
    flags = sorted({state['oflag'] for state in tty if isinstance(state.get('oflag'), int)})
    return {'numeric_fd1_write_bytes': traced, 'captured_pty_bytes': captured,
            'captured_minus_traced': captured - traced if isinstance(traced, int) else None,
            'interrupted_fd1_calls': writes.get('1', {}).get('interrupted', 0),
            'observed_output_flags': flags,
            'observed_opost_onlcr': any(flag & termios.OPOST and flag & termios.ONLCR for flag in flags),
            'scope': 'PTY can translate LF to CRLF outside raw mode; checkpoints do not cover all intervals. Interrupted records contribute no attributed numeric bytes. Exact equality remains required; no correction/subtraction.'}


REQUIRED_SYMBOL_SECTIONS = {'.debug_info', '.debug_line', '.symtab'}
MAX_SYMBOL_OUTPUT_BYTES = 4 * 1024 * 1024


def symbol_sections(payload):
    sections = set(re.findall(r'\[\s*\d+\]\s+(\S+)', payload.decode('utf-8', errors='strict')))
    if not REQUIRED_SYMBOL_SECTIONS.issubset(sections):
        raise ValueError('symbolized artifact missing required ELF sections: ' + repr(sorted(REQUIRED_SYMBOL_SECTIONS - sections)))
    return sorted(sections)


def verified_symbol_output(identity, guard):
    """The parsed section text and its digest come from the same bounded bytes."""
    path = Path(identity['path'])
    if path.is_symlink() or str(path.resolve(strict=True)) != identity['path']:
        raise ValueError('symbol section output path changed')
    parts, size = [], 0
    with path.open('rb') as stream:
        while True:
            guard.check()
            part = stream.read(1 << 20)
            if not part:
                break
            size += len(part)
            if size > MAX_SYMBOL_OUTPUT_BYTES:
                raise ValueError('symbol section output exceeds byte cap')
            parts.append(part)
    payload = b''.join(parts)
    if size != identity['size_bytes'] or hashlib.sha256(payload).hexdigest() != identity['sha256']:
        raise ValueError('symbol section output identity differs')
    sections = symbol_sections(payload)
    if bounded_identity(path, guard, maximum=MAX_SYMBOL_OUTPUT_BYTES) != identity:
        raise ValueError('symbol section output changed during verification')
    return sections


def prepare_symbols(artifact_manifest, output, *, readelf=None):
    """Parent-run bounded preparation; no capture/quiet window or CLI execution."""
    identities, manifest = load_artifacts(artifact_manifest, with_manifest=True)
    tool = tool_identity(readelf or shutil.which('readelf'))
    if tool['status'] != 'available':
        raise FileNotFoundError('readelf required for symbol preparation')
    helper = bench.cached_spawn()  # Preparation only, before any QuietWindow.
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'kind': 'actual readelf symbol-section preparation', 'valid': False,
              'artifact_manifest': manifest, 'readelf': tool['identity'], 'native_spawn': helper,
              'artifacts': {}, 'errors': []}
    guard = bench.FunctionalGuard(60)
    try:
        for name in ('go-symbolized', 'rust-symbolized'):
            artifact = identities[name]
            bench.check_artifact(artifact)
            directory = output / name
            directory.mkdir()
            argv = [tool['identity']['path'], '-SW', '--', artifact['identity']['path']]
            record = process_command(argv, directory, 'readelf-sections', guard)
            report['artifacts'][name] = {'binary': artifact['identity'],
                'metadata_sha256': artifact['provenance_file_sha256'], 'command': argv, 'analysis': record,
                'sections': verified_symbol_output(record['stdout'], guard)}
            bench.check_artifact(artifact)
        bench.check_snapshot(manifest)
        unchanged(tool['identity'], guard)
        if bench.cached_spawn(probe=False) != helper:
            raise ValueError('helper changed during symbol preparation')
        report['valid'] = True
    except Exception as error:
        report['errors'].append(repr(error))
    finally:
        bench.write_json(output / 'report.json', report)
    return report


def verify_symbol_evidence(identities, manifest, path):
    if not path:
        raise ValueError('parent-prepared --symbol-evidence required before profiling')
    report, snapshot = bench.json_snapshot(path)
    if report.get('kind') != 'actual readelf symbol-section preparation' or report.get('valid') is not True:
        raise ValueError('symbol preparation missing/failed')
    if report.get('artifact_manifest') != manifest or set(report.get('artifacts', {})) != {'go-symbolized', 'rust-symbolized'}:
        raise ValueError('symbol preparation root manifest/artifact binding differs')
    unchanged(report['readelf'])
    for name in ('go-symbolized', 'rust-symbolized'):
        artifact, record = identities[name], report['artifacts'][name]
        if record.get('binary') != artifact['identity'] or record.get('metadata_sha256') != artifact['provenance_file_sha256']:
            raise ValueError('symbol preparation binary/metadata differs')
        expected = [report['readelf']['path'], '-SW', '--', artifact['identity']['path']]
        analysis = record.get('analysis', {})
        if record.get('command') != expected or analysis.get('argv') != expected or analysis.get('status') != 'complete' or analysis.get('returncode') != 0 or analysis.get('cleanup', {}).get('issues') != []:
            raise ValueError('symbol readelf command/result differs')
        unchanged(analysis['stderr'], bench.FunctionalGuard(15))
        identity = analysis['stdout']
        if verified_symbol_output(identity, bench.FunctionalGuard(15)) != record.get('sections'):
            raise ValueError('symbol section evidence differs')
        bench.check_artifact(artifact)
    bench.check_snapshot(snapshot)
    return {**report, 'evidence_identity': snapshot}


def run_campaign(artifact_manifest, output, approval, options, *, preload, interpreter, symbol_evidence=None):
    if not options.campaign_id:
        raise ValueError('explicit parent-assigned campaign_id required')
    definition = plan(options)
    bench.require_seams()
    identities, manifest_identity = load_artifacts(artifact_manifest, with_manifest=True)
    symbols = verify_symbol_evidence(identities, manifest_identity, symbol_evidence)
    helper = bench.cached_spawn()  # Never auto-build the observer helper in a campaign.
    definition['native_spawn'] = helper
    toolset = tools(preload, interpreter)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'status': 'preparing', 'valid': False, 'definition': definition,
              'artifacts': identities, 'artifact_manifest': manifest_identity, 'symbol_sections': symbols,
              'tools': toolset, 'source': bench.source_snapshot(),
              'host_before': bench.host_snapshot(), 'samples': [], 'errors': [],
              'scope': 'attribution captures; no release performance or managed Go heap claim',
              'lock': str(bench.LOCK), 'approval_path': str(Path(approval).resolve())}
    cases = {case['profile_case']: case for case in definition['cases']}
    controls = {}
    capture_results = {}
    used_tools = set()
    scheduled = [*definition['controls'], *definition['captures']]
    try:
        # campaign_id is forwarded when the collector publishes campaign-bound approvals.
        keywords = {'campaign_id': options.campaign_id} if options.campaign_id else {}
        with bench.QuietWindow(approval, options.budget_seconds, **keywords) as guard:
            report.update(status='capturing', quiet_window=copy.deepcopy(guard.approval))
            for index, item in enumerate(scheduled):
                guard.check(force=True)
                if bench.cached_spawn(probe=False) != helper:
                    raise ValueError('native observer helper changed during profiler campaign')
                case = cases[item['case']]
                directory = output / 'sessions' / f'{index:03}-{item["case"]}-{item["artifact"]}-{item["mode"]}'
                row = {**item, 'directory': str(directory.relative_to(output)), 'status': 'running',
                       'issues': [], 'measurements': None}
                report['samples'].append(row)
                bench.write_report(output / 'report.json', report)
                name, mode = item['artifact'], item['mode']
                identity = identities[name]['identity']
                bench.check_artifact(identities[name])
                if mode != 'control' and toolset[tool_for(mode)]['status'] != 'available':
                    row.update(status='unavailable', issues=[toolset[tool_for(mode)]['reason']])
                    continue
                directory.mkdir(parents=True)
                if mode != 'control':
                    used_tools.add(tool_for(mode))
                try:
                    adapter = (bench.Capture(guard, policy='resource-attribution', identity=identity,
                                            evidence_path=directory / 'capture/execute') if mode == 'control' else ProfileCapture(
                        guard, mode, toolset[tool_for(mode)]['identity'], directory, options.perf_event, identity=identity))
                    result = p7.sample(identity['path'], case, directory / 'capture', execute_fn=adapter)
                    if mode == 'strace-writes':
                        capture_results[str(directory.relative_to(output))] = {
                            'metrics': {'stdout_bytes': result['metrics']['stdout_bytes']},
                            'termios_before': result.get('termios_before', {}), 'termios_after': result.get('termios_after', {}),
                            'frames': [{'termios': frame.get('termios', {})} for frame in result.get('frames', [])]}
                    row['command'] = result.get('capture_command')
                    bench.check_artifact(identities[name])
                    bench.check_binding(result, identity, case)
                    bench.check_sample_helper(result, helper)
                    row['issues'] = bench.observation_issues(result, case, require_resources=mode == 'control')
                    key = (item['case'], item['repetition'])
                    if mode == 'control':
                        baseline = controls.get((*key, 'go-release'))
                    else:
                        baseline = controls[(*key, name)]
                        row['instrumentation'] = result['profile']
                        row['profiles'] = profile_artifacts(mode, directory, guard)
                        diagnostic = directory / 'profiler.stderr'
                        if mode != 'heaptrack' and not diagnostic.is_file():
                            raise ValueError('external profiler stderr separation artifact missing')
                        row['profiler_diagnostics'] = (bounded_identity(diagnostic, guard, maximum=MAX_ANALYSIS_BYTES) if diagnostic.is_file()
                                                      else {'scope': 'preload shares CLI stderr; retained without filtering'})
                    if baseline is not None:
                        row['issues'].extend(p7.compare(baseline, result))
                    if row['issues']:
                        row['status'] = 'unavailable' if mode != 'control' and denied(directory) else 'failed'
                    else:
                        row.update(status='valid', measurements={'raw_resources': result['metrics'],
                            'scope': 'uninstrumented control only; no timing inference' if mode == 'control'
                                     else 'instrumented process-tree/hooks resources; not native release metrics'})
                        if mode == 'control':
                            controls[(*key, name)] = result
                    guard.check(force=True)
                except Exception as error:
                    row.update(status='unavailable' if denied(directory) else 'failed', measurements=None)
                    row['issues'].append(repr(error))
                    guard.check(force=True)  # Expiry stops all further capture/processing.
                if mode == 'control' and row['status'] != 'valid':
                    raise ValueError('functional control failed; dependent profiles not captured')
                bench.write_report(output / 'report.json', report)
            report['status'] = 'processing'  # Every control/capture is finished first.
            for row in report['samples']:
                if row['mode'] == 'control' or row['status'] != 'valid':
                    continue
                try:
                    row['analysis'] = analyze(row['mode'], row['profiles'], output / row['directory'], toolset, guard,
                                              used_tools=used_tools)
                    if row['mode'] == 'strace-writes':
                        row['write_byte_diagnostic'] = write_byte_diagnostic(row['analysis']['writes_by_fd'], capture_results[row['directory']])
                        if row['write_byte_diagnostic']['numeric_fd1_write_bytes'] != row['write_byte_diagnostic']['captured_pty_bytes']:
                            raise ValueError('FD1/PTY byte mismatch; possible cooked-mode OPOST/ONLCR translation or unaccounted interrupted writes; exact capture gate retained: ' + repr(row['write_byte_diagnostic']))
                except Exception as error:
                    row.update(status='unavailable' if isinstance(error, FileNotFoundError) else 'failed')
                    row['issues'].append('processing: ' + repr(error))
                    guard.check(force=True)
                bench.write_report(output / 'report.json', report)
            guard.check(force=True)
            drift = bench.final_identities(report, identities, helper)
            if drift:
                raise ValueError('; '.join(drift))
            bench.check_snapshot(manifest_identity)
            report['valid'] = all(row['status'] == 'valid' for row in report['samples'])
            report['status'] = 'complete' if report['valid'] else 'incomplete'
    except Exception as error:
        report.update(status='failed', valid=False)
        report['errors'].append(repr(error))
        for row in report['samples']:
            if row['status'] == 'running':
                row.update(status='failed', measurements=None)
                row['issues'].append('campaign interrupted: ' + repr(error))
    finally:
        report['used_tools'] = sorted(used_tools)
        report['tools_after'] = {}
        for name in sorted(used_tools):
            try:
                report['tools_after'][name] = bounded_identity(toolset[name]['identity']['path'], bench.FunctionalGuard(15))
                if report['tools_after'][name] != toolset[name]['identity']:
                    raise ValueError('identity differs')
            except Exception as error:
                report.update(status='failed', valid=False)
                report['errors'].append('used profiler tool changed/unavailable: ' + name + ': ' + repr(error))
        try:
            bench.check_snapshot(symbols['evidence_identity'])
            unchanged(symbols['readelf'])
        except Exception as error:
            report.update(status='failed', valid=False)
            report['errors'].append('symbol preparation identity changed: ' + repr(error))
        if 'source_after' not in report:
            report['errors'].extend(bench.final_identities(report, identities, helper))
        try:
            _current, report['artifact_manifest_after'] = bench.json_snapshot(manifest_identity['path'])
            bench.check_snapshot(manifest_identity)
        except (OSError, ValueError) as error:
            report.update(status='failed', valid=False)
            report['errors'].append('root manifest: ' + repr(error))
        report['unattempted'] = scheduled[len(report['samples']):]
        report['host_after'] = bench.host_snapshot()
        bench.write_report(output / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'run', 'prepare-symbols'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, help='four actual release/symbolized artifacts and provenance JSON paths')
    parser.add_argument('--symbol-evidence', type=Path, help='verified prepare-symbols/report.json from parent preparation')
    parser.add_argument('--readelf', type=Path, help='canonical ELF section inspector for prepare-symbols only')
    parser.add_argument('--quiet-window', type=Path)
    parser.add_argument('--campaign-id', default='')
    parser.add_argument('--case', action='append', choices=CASES)
    parser.add_argument('--profiler', action='append', choices=MODES)
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--budget-seconds', type=float, default=600)
    parser.add_argument('--max-sessions', type=int, default=64)
    parser.add_argument('--session-timeout', type=float, default=60)
    parser.add_argument('--theme', choices=('dark', 'light'), default='dark')
    parser.add_argument('--size', type=bench.parse_size, default=(120, 40))
    parser.add_argument('--perf-event', choices=('cycles:u', 'cpu-clock:u'), default='cycles:u')
    parser.add_argument('--heaptrack-preload', type=Path, default=Path('/usr/lib/heaptrack/libheaptrack_preload.so'))
    parser.add_argument('--heaptrack-interpreter', type=Path, default=Path('/usr/lib/heaptrack/libexec/heaptrack_interpret'))
    args = parser.parse_args(argv)
    options = Options(cases=tuple(args.case or CASES), modes=tuple(args.profiler or ('perf', 'strace', 'heaptrack')),
                      repetitions=args.repetitions, budget_seconds=args.budget_seconds, max_sessions=args.max_sessions,
                      session_timeout=args.session_timeout, theme=args.theme, size=args.size,
                      perf_event=args.perf_event, campaign_id=args.campaign_id)
    try:
        if args.mode == 'plan':
            bench.write_json(args.out, plan(options))
            return 0
        if args.mode == 'prepare-symbols':
            if not args.artifacts:
                parser.error('prepare-symbols requires --artifacts')
            report = prepare_symbols(args.artifacts, args.out, readelf=args.readelf)
            return 0 if report['valid'] else 1
        if not args.artifacts or not args.quiet_window:
            parser.error('run requires --artifacts and parent --quiet-window')
        report = run_campaign(args.artifacts, args.out, args.quiet_window, options,
                              preload=args.heaptrack_preload, interpreter=args.heaptrack_interpreter,
                              symbol_evidence=args.symbol_evidence)
        return 0 if report['valid'] else 1
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
