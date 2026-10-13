#!/usr/bin/env python3
"""External profiler captures, isolated from unprofiled latency comparisons."""
import argparse
import gzip
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import diagnose
import manifest
import run

ROOT = Path(__file__).resolve().parent.parent
MODES = ('control', 'perf', 'strace', 'strace-writes', 'heaptrack')


def wrapper(path, argv):
    path.write_text('#!/bin/sh\nexec ' + shlex.join([str(a) for a in argv]) + ' "$@"\n')
    path.chmod(0o700)
    return path


def command(mode, tool, binary, directory, event):
    if mode == 'perf':
        return [tool, 'record', '-q', '-e', event, '-F', '999', '--call-graph',
                'dwarf,16384', '-o', str(directory / 'perf.data'), '--', str(binary)]
    if mode == 'strace':
        return [tool, '-f', '-qq', '-c', '-o', str(directory / 'strace.txt'), '--', str(binary)]
    if mode == 'strace-writes':
        # Raw pointers/lengths, not buffer contents. FD 1 distinguishes stdout
        # from socket or runtime wake-up writes. Includes all CLI threads.
        return [tool, '-f', '-qq', '-e', 'trace=write,writev', '-e', 'raw=write,writev',
                '-o', str(directory / 'writes.trace'), '--', str(binary)]
    if mode == 'heaptrack':
        return ['/usr/bin/env', 'LD_PRELOAD=' + tool,
                'DUMP_HEAPTRACK_OUTPUT=' + str(directory / 'heaptrack.raw'), str(binary)]
    raise ValueError(f'unknown profiler: {mode}')


def artifacts(mode, directory):
    names = {'perf': 'perf.data', 'strace': 'strace.txt', 'strace-writes': 'writes.trace'}
    paths = list(directory.glob('heaptrack*')) if mode == 'heaptrack' else [directory / names[mode]]
    if len(paths) != 1 or paths[0].is_symlink() or not paths[0].is_file() or not paths[0].stat().st_size:
        raise ValueError(f'missing or ambiguous {mode} capture')
    return [manifest.file_identity(paths[0])]


def validate(result, expected):
    if expected is not None and result['contract'] != expected:
        result['issues'].append('contract differs from unprofiled Go reference')
    result['valid'] = result['valid'] and not result['issues']


def write_counts(path):
    """Count actual write results by FD, pairing strace unfinished/resumed lines."""
    counts, pending = {}, {}
    with Path(path).open() as stream:
        for line in stream:
            start = re.match(r"^(\d+)\s+(writev?)\((0x[0-9a-f]+|[0-9]+),", line)
            resumed = re.match(r"^(\d+)\s+<\.\.\. (writev?) resumed>", line)
            if start:
                pid, call, fd = start.groups()
                fd = str(int(fd, 0))
                if pid in pending:
                    raise ValueError('overlapping unfinished writes for one thread')
                counts.setdefault(fd, {'calls': 0, 'completed': 0, 'bytes': 0, 'errors': 0})['calls'] += 1
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
            result = re.search(r'= (-?0x[0-9a-f]+|-?[0-9]+)(?:\s|$)', line)
            if not result:
                raise ValueError('write result unavailable')
            value = int(result[1], 0)
            counts[fd]['completed'] += 1
            if value < 0:
                counts[fd]['errors'] += 1
            else:
                counts[fd]['bytes'] += value
    if pending:
        raise ValueError('incomplete write trace')
    return counts


def analyze(mode, tool, profile, directory, interpreter=None):
    if mode == 'perf':
        argv = [tool, 'report', '--stdio', '--no-children', '--percent-limit', '0.5',
                '-i', profile['path']]
    elif mode == 'heaptrack':
        argv = [tool, '-m', '0', '-f', str(directory / 'interpreted.gz')]
    else:
        return None
    before = manifest.file_identity(profile['path'])
    if before != profile:
        raise ValueError('profile identity changed before analysis')
    if mode == 'heaptrack':
        interpret_argv = [interpreter]
        plain = directory / 'interpreted.txt'
        with open(profile['path'], 'rb') as raw, plain.open('wb') as decoded, (directory / 'interpret.stderr').open('wb') as err:
            interpreted = subprocess.run(interpret_argv, stdin=raw, stdout=decoded, stderr=err, timeout=180,
                                         env={**os.environ, 'DEBUGINFOD_URLS': ''})
        if interpreted.returncode or not plain.stat().st_size:
            raise ValueError('heaptrack raw interpretation failed')
        with plain.open('rb') as source, gzip.open(directory / 'interpreted.gz', 'wb') as target:
            shutil.copyfileobj(source, target)
        plain.unlink()
    with (directory / 'analysis.txt').open('wb') as out, (directory / 'analysis.stderr').open('wb') as err:
        result = subprocess.run(argv, stdout=out, stderr=err, timeout=180,
                                env={**os.environ, 'DEBUGINFOD_URLS': ''})
    if result.returncode or not (directory / 'analysis.txt').stat().st_size or before != manifest.file_identity(profile['path']):
        raise ValueError('profile analysis failed or changed its input')
    result = {'argv': argv, 'output': manifest.file_identity(directory / 'analysis.txt')}
    if mode == 'heaptrack':
        result.update(interpret_argv=interpret_argv, interpreted=manifest.file_identity(directory / 'interpreted.gz'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mode', action='append', choices=MODES)
    parser.add_argument('--case', action='append', choices=diagnose.DEFAULT_CASES)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--heaptrack-preload', type=Path, default=Path('/usr/lib/heaptrack/libheaptrack_preload.so'))
    parser.add_argument('--heaptrack-interpreter', type=Path, default=Path('/usr/lib/heaptrack/libexec/heaptrack_interpret'))
    parser.add_argument('--perf-event', choices=['cycles:u', 'cpu-clock:u'], default='cycles:u')
    parser.add_argument('--go', type=Path, default=ROOT / 'bin/pb-go-symbolized')
    parser.add_argument('--rust', type=Path, default=ROOT / 'target/symbolized/profiling/pb')
    parser.add_argument('--go-release', type=Path, default=ROOT / 'bin/pb-go')
    parser.add_argument('--rust-release', type=Path, default=ROOT / 'target/release/pb')
    args = parser.parse_args()
    if args.repetitions < 1 or args.timeout <= 0:
        parser.error('positive repetitions and timeout required')
    modes = args.mode or list(MODES)
    if len(set(modes)) != len(modes):
        parser.error('duplicate mode')
    tools = {}
    for mode in modes:
        name = 'strace' if mode.startswith('strace') else mode
        if name == 'control':
            continue
        tools[name] = shutil.which(name)
        if name == 'heaptrack':
            tools['heaptrack_print'] = shutil.which('heaptrack_print')
            tools['heaptrack_preload'] = str(args.heaptrack_preload.resolve()) if args.heaptrack_preload.is_file() else None
            tools['heaptrack_interpreter'] = str(args.heaptrack_interpreter.resolve()) if args.heaptrack_interpreter.is_file() else None
    missing = [name for name, path in tools.items() if not path]
    if missing:
        parser.error('missing tools: ' + ', '.join(missing))
    bins = {'go': args.go.resolve(), 'rust': args.rust.resolve(),
            'go-release': args.go_release.resolve(), 'rust-release': args.rust_release.resolve()}
    identities = {key: manifest.file_identity(path) for key, path in bins.items()}
    for language in ('go', 'rust'):
        sections = subprocess.check_output(['readelf', '-S', str(bins[language])], text=True)
        if '.debug_info' not in sections or '.debug_line' not in sections or '.symtab' not in sections:
            parser.error(f'{language} binary lacks debug symbols')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    cases = diagnose.workload_cases(args.case or ['p3-100000-json', 'p3-single-8MiB-item'])
    run.write_json(out / 'workloads.json', cases)
    report = {'schema_version': 1, 'valid': False, 'complete': False,
              'scope': 'synthetic local HTTP; profiles explain work, not release performance; no live service measurements',
              'profiler_resource_scope': 'instrumented child and/or profiler wrapper; overhead is inseparable; heaptrack raw decoding occurs after capture',
              'heaptrack_scope': 'Rust native allocator only; Go managed heap requires native Go allocation profiles',
              'perf_event': args.perf_event, 'repetitions': args.repetitions,
              'binaries': identities, 'tools': {}, 'samples': [], 'errors': [],
              'source': manifest.source_snapshot(ROOT),
              'fixture': manifest.file_identity(ROOT / 'bin/pb-fixture'),
              'meter': manifest.file_identity(ROOT / 'bin/pb-meter')}
    for name, path in tools.items():
        report['tools'][name] = manifest.file_identity(path)
        if name not in ('heaptrack_preload', 'heaptrack_interpreter'):
            version = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=10)
            report['tools'][name]['version'] = version.stdout + version.stderr
    def save():
        run.write_json(out / 'report.json', report)
    save()
    try:
        for case in cases:
            expected = None
            # Always capture controls first; never mix profiler latency with CLI latency.
            for mode in ['control', *[m for m in modes if m != 'control']]:
                languages = ['go-release', 'rust-release', 'go', 'rust'] if mode == 'control' else (['rust'] if mode == 'heaptrack' else ['go', 'rust'])
                for rep in range(args.repetitions):
                    for language in languages if rep % 2 == 0 else list(reversed(languages)):
                        directory = out / case['name'] / mode / f'{rep:02}-{language}'
                        directory.mkdir(parents=True)
                        binary = bins[language]
                        argv = [str(binary)]
                        if mode != 'control':
                            tool = tools['strace' if mode.startswith('strace') else ('heaptrack_preload' if mode == 'heaptrack' else mode)]
                            argv = command(mode, tool, binary, directory, args.perf_event)
                            binary = wrapper(directory / 'launch', argv)
                        result = run.sample(binary, case, ROOT / 'bin/pb-fixture', directory / 'capture', args.timeout)
                        validate(result, expected)
                        if expected is None:
                            if not result['valid']:
                                raise ValueError('unprofiled Go reference failed')
                            expected = result['contract']
                        entry = {'case': case['name'], 'mode': mode, 'language': language,
                                 'repetition': rep, 'argv_prefix': argv,
                                 'directory': str(directory.relative_to(out)), 'result': result,
                                 'resource_scope': ('CLI child' if mode == 'control' else 'CLI with heaptrack hooks and raw-record I/O' if mode == 'heaptrack' else 'profiler process tree')}
                        report['samples'].append(entry)
                        if mode != 'control':
                            entry['profiles'] = artifacts(mode, directory)
                        save()
                        if not result['valid']:
                            raise ValueError(f'contract failure: {case["name"]}/{mode}/{language}')
        # Decoding happens after every timed capture, with no concurrent builds.
        for entry in report['samples']:
            mode = entry['mode']
            if mode == 'strace-writes':
                identity = entry['profiles'][0]
                if manifest.file_identity(identity['path']) != identity:
                    raise ValueError('write trace changed before analysis')
                entry['writes_by_fd'] = write_counts(identity['path'])
                if manifest.file_identity(identity['path']) != identity:
                    raise ValueError('write trace changed during analysis')
                if entry['writes_by_fd'].get('1', {}).get('bytes') != entry['result']['stdout_bytes']:
                    raise ValueError('traced stdout bytes differ from captured CLI output')
            if mode in ('perf', 'heaptrack'):
                tool = tools['perf' if mode == 'perf' else 'heaptrack_print']
                entry['analysis'] = analyze(mode, tool, entry['profiles'][0], out / entry['directory'], tools.get('heaptrack_interpreter'))
        if any(manifest.file_identity(bins[k]) != v for k, v in identities.items()):
            raise ValueError('binary changed during captures')
        report['complete'] = True
        report['valid'] = all(e['result']['valid'] for e in report['samples'])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report['errors'].append(str(error))
    finally:
        save()
    print(json.dumps({'report': str(out / 'report.json'), 'valid': report['valid'], 'errors': report['errors']}))
    return 0 if report['valid'] else 1


if __name__ == '__main__':
    sys.exit(main())
