#!/usr/bin/env python3
"""Profile-only Phase6 captures: instrumentation evidence, never native timing or ranking.

Drives the frozen phase6_workloads.run_sample with verified symbolized builds behind
one external profiler, after release and symbolized controls of the same scenario.
`capture` charges all owned work to the window from command entry and does nothing
heavy before holding the coordinator's nonblocking lock. `prepare`/`plan` are offline
and take the same lock without a token. `analyze` runs only after the window is
released, under the same lock, only for a capture report that passed every gate.
"""
import argparse
import contextlib
import copy
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shlex
import signal
import subprocess
import sys

import phase6_workloads as p6
import profile_external as profiles
import run

SIDECAR_SCHEMA = 'pb-phase6-profile-sidecar-v4'
REPORT_SCHEMA = 'pb-phase6-profile-report-v4'
ANALYSIS_SCHEMA = 'pb-phase6-profile-analysis-v4'
HARNESS_DIR = Path(__file__).resolve().parent
HARNESS_MODULES = ('phase6_profiles', 'phase6_workloads', 'profile_external', 'run', 'manifest', 'diagnose',
                   'cases', 'phase3_workloads')
CAPTURE_MODES = ('perf', 'strace', 'strace-writes', 'heaptrack')
PERF_EVENTS = ('cycles:u', 'cpu-clock:u')
DEBUG_SECTIONS = ('.debug_info', '.debug_line', '.symtab')
RUST_SYMBOLIZED_ARGV = ['cargo', 'build', '--locked', '--offline', '--profile', 'profiling', '--bin', 'pb']
CHUNK_BYTES = 1 << 20  # owned hashing/gzip/parse work checks the deadline before each chunk of at most this
PROBE_TIMEOUT_S = 60
PROBE_RESERVE_S = 5
ANALYSIS_TIMEOUT_S = 180
FINAL_FLOOR_S = 30  # minimum left for the end checks and the small report write
# Profiler artifact caps (strict post-check) versus the per-file RLIMIT_FSIZE for every file a sample writes.
MAX_ARTIFACT_BYTES = {'perf': 2 << 30, 'strace': 16 << 20, 'strace-writes': 512 << 20, 'heaptrack': 2 << 30}
SAMPLE_FILE_FLOOR_BYTES = 1 << 30  # above collector logs (16 MiB max) and fixture logs; not source-derived for the fixture
PROBE_OUTPUT_BYTES = 256 << 20
ANALYSIS_FILE_BYTES = 4 << 30
STATS_READ_BYTES = 16 << 20
DISK_HEADROOM_BYTES = 2 << 30
HASH_BYTES_PER_S = 200 << 20  # planning estimate only; the deadline, not this rate, bounds the work
MIN_PERF_SAMPLES = 1000
PERF_PARANOID = Path('/proc/sys/kernel/perf_event_paranoid')
CACHE = Path('/home/ajs/.cache/pb-phase6')
PARENT = CACHE / 'campaign-prepared-r6-v1/manifest.json'
TOOL_ALIASES = dict(perf='/usr/bin/perf', strace='/usr/bin/strace', readelf='/usr/bin/readelf',
                    shell='/bin/sh', env='/usr/bin/env', heaptrack_print='/usr/bin/heaptrack_print',
                    heaptrack_preload='/usr/lib/heaptrack/libheaptrack_preload.so',
                    heaptrack_interpreter='/usr/lib/heaptrack/libexec/heaptrack_interpret')
CAPTURE_TOOLS = {'perf': ('perf',), 'strace': ('strace',), 'strace-writes': ('strace',),
                 'heaptrack': ('env', 'heaptrack_preload')}
VERSION_PROBES = {'perf': ('perf',), 'strace': ('strace',), 'strace-writes': ('strace',),
                  'heaptrack': ('env', 'heaptrack_print')}
VERSION_ARGS = {'perf': ['--version'], 'strace': ['-V'], 'env': ['--version'], 'readelf': ['--version'],
                'heaptrack_print': ['--version']}
ANALYSIS_TOOLS = {'perf': ('perf',), 'heaptrack': ('heaptrack_interpreter', 'heaptrack_print'),
                  'strace': (), 'strace-writes': ()}
SCOPE = ('profile_only instrumentation of a synthetic loopback fixture; profiler CPU/RSS is the '
         'profiler process tree; never native timing, ranking, budget or live-delivery evidence')
UNBOUND = ['shared libraries and dynamic loader configuration of perf/strace/readelf/heaptrack/bash',
           'system profiler configuration such as /etc/perfconfig',
           'kernel settings other than perf_event_paranoid (for example kptr_restrict)',
           'loaded module bytecode beyond path plus import-time and current source SHA256',
           'a path swapped and restored between identity checkpoints (checks are by path, not held open)',
           'RLIMIT_FSIZE is a soft limit a descendant could raise; none of the bound tools is known to']


class DeadlineExceeded(Exception):
    """Owned work reached its deadline; it stopped before reading the next chunk or starting the next step."""


def loaded_harness():
    """Harness-directory modules imported in this process; this adapter is keyed by stem even as __main__."""
    found = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if path and not name.startswith('test_') and Path(path).resolve().parent == HARNESS_DIR:
            found[Path(path).stem] = module
    return found


# Source identity of every imported harness module, taken once all imports are complete.
IMPORTED = {name: p6.file_info(module.__file__) for name, module in loaded_harness().items()}


def expired(deadline_ns):
    return deadline_ns is not None and p6.clock_ns() >= deadline_ns


def read_chunk(stream, buffer):
    return stream.readinto(buffer)


def bounded_sha256(path, deadline_ns):
    """hashlib.file_digest equivalent that checks the deadline before every chunk of at most CHUNK_BYTES."""
    digest, buffer = hashlib.sha256(), bytearray(CHUNK_BYTES)
    view = memoryview(buffer)
    with open(path, 'rb') as stream:
        while True:
            if expired(deadline_ns):
                raise DeadlineExceeded(f'deadline reached while hashing {path}')
            count = read_chunk(stream, buffer)
            if not count:
                return digest.hexdigest()
            digest.update(view[:count])


def bounded_info(path, deadline_ns=None):
    """Same {path, sha256, size_bytes} as phase6_workloads.file_info and manifest.file_identity."""
    if expired(deadline_ns):
        raise DeadlineExceeded(f'deadline reached before hashing {path}')
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f'not a regular file: {path}')
    return dict(path=str(path), sha256=bounded_sha256(path, deadline_ns), size_bytes=path.stat().st_size)


def bounded_binary_info(path, deadline_ns=None):
    """run.binary_info semantics (resolved executable regular file) with the bounded reader."""
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f'not an executable file: {resolved}')
    return bounded_info(resolved, deadline_ns)


@contextlib.contextmanager
def bounded_hashing(deadline_ns):
    """The frozen binders (bind_manifest, bind_selected_roles) hash through the module global
    phase6_workloads.file_info; for this block only it is the bounded equivalent. The frozen source is
    unchanged and bound; the process is single-threaded, so nothing else observes the substitution."""
    saved = p6.file_info
    p6.file_info = lambda path: bounded_info(path, deadline_ns)
    try:
        yield
    finally:
        p6.file_info = saved


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def scenarios(seed=2026):
    """Frozen finite definitions verbatim, plus explicitly separate bounded profile-only ones."""
    result = {w['name']: dict(w, scenario_kind='frozen_native_definition')
              for w in p6.workloads(tier='finite', seed=seed)}
    heavy = copy.deepcopy(result['large'])
    heavy['stream']['subscriptions'][0]['ops'][0].update(rows=4096, count=128)
    extra = [('profile-output-heavy', heavy)]
    for compression in ('lz4', 'zstd'):
        dictionary = copy.deepcopy(result['dictionary-finite'])
        dictionary['stream'].update(compression=compression, dictionary=dict(
            cardinality=64, mode='replace', value_bytes=256, max_entries=4096))
        dictionary['stream']['subscriptions'][0]['ops'][0].update(rows=2048, count=64)
        extra.append(('profile-dictionary-' + compression, dictionary))
    for name, workload in extra:
        workload.update(name=name, mode=name, timeout_s=180, scenario_kind='profile_only')
        result[name] = workload
    return result


def bound_json(spec, what):
    """Parse and identify the same bytes; the declared SHA256 must match them."""
    value, info = p6.read_json_with_identity(spec['path'])
    if info['sha256'] != spec.get('sha256'):
        raise ValueError(what + ' SHA256 differs')
    return value, info


def bound_file(spec, what):
    info = p6.file_info(spec['path'])
    if info['sha256'] != spec.get('sha256'):
        raise ValueError(what + ' SHA256 differs')
    return info


def frozen_executable(spec, what):
    info = bound_file(spec, what)
    mode = Path(info['path']).stat().st_mode
    if mode & 0o222 or not mode & 0o111:
        raise ValueError(what + ' must be a read-only executable artifact')
    return info


def saved_sections(spec, what):
    info = bound_file(spec, what + ' saved sections')
    text = Path(info['path']).read_text()
    missing = [name for name in DEBUG_SECTIONS if not re.search(r'\s' + re.escape(name) + r'\s', text)]
    if missing:
        raise ValueError(what + ' saved sections lack ' + ', '.join(missing))
    return info


def verify_rust(role, parent):
    build, build_info = bound_json(role['build_manifest'], 'Rust symbolized build manifest')
    binary = frozen_executable(role['binary'], 'Rust symbolized binary')
    if build['binary']['sha256'] != binary['sha256'] or Path(build['binary']['path']).resolve() != Path(binary['path']):
        raise ValueError('Rust symbolized build manifest describes a different executable')
    if build.get('argv') != RUST_SYMBOLIZED_ARGV or build.get('features') != [] or build.get('debug_symbols') is not True:
        raise ValueError('Rust symbolized build flags differ from the profiling profile declaration')
    if build.get('sources') != parent['sources']['candidate'] or build.get('rustc') != parent['toolchains']['rustc']:
        raise ValueError('Rust symbolized sources/toolchain differ from the parent candidate')
    if build['candidate_manifest']['sha256'] != parent['candidate_manifest']['sha256']:
        raise ValueError('Rust symbolized build is bound to a different candidate manifest')
    tracked = [binary, build_info, bound_file(build['candidate_manifest'], 'candidate manifest copy'),
               bound_file(build['build_log'], 'Rust symbolized build log')]
    if role['sections']['sha256'] != build['sections']['sha256']:
        raise ValueError('sidecar sections differ from the Rust build manifest')
    tracked.append(saved_sections(build['sections'], 'Rust symbolized'))
    return binary, tracked


def verify_go(role, parent):
    build, build_info = bound_json(role['build_manifest'], 'Go symbolized build manifest')
    provenance, provenance_info = bound_json(build['source_provenance'], 'Go symbolized source provenance')
    release, release_info = bound_json(role['release_provenance'], 'Go release provenance')
    binary = frozen_executable(role['binary'], 'Go symbolized binary')
    if provenance != build.get('provenance'):
        raise ValueError('Go build manifest provenance differs from its bound source provenance')
    if Path(build['binary']['path']).resolve() != Path(binary['path']) or not (
            build['binary']['sha256'] == provenance['binary_sha256'] == binary['sha256']):
        raise ValueError('Go symbolized provenance describes a different executable')
    compiler = re.match(r'^[^\n]*: (go\S+)', parent['toolchains']['oracle_embedded_build'])
    if (provenance.get('language') != 'go' or provenance.get('optimized') is not True or
            provenance.get('source_commit') != parent['sources']['oracle'] or not compiler or
            provenance.get('compiler', '').split()[:1] != [compiler[1]]):
        raise ValueError('Go symbolized source commit/toolchain differ from the parent oracle')
    if (release.get('binary_sha256') != parent['baselines']['pb-go']['sha256'] or
            release.get('source_tree_sha256') != provenance.get('source_tree_sha256') or
            release.get('source_commit') != provenance.get('source_commit')):
        raise ValueError('Go release and symbolized builds do not share the bound source tree')
    return binary, [binary, build_info, provenance_info, release_info, saved_sections(role['sections'], 'Go symbolized')]


def tool_identity(spec, name):
    """Record the alias, but bind and later execute only the resolved file."""
    info = p6.file_info(spec['alias'])
    if info['sha256'] != spec.get('sha256') or info['path'] != spec.get('path'):
        raise ValueError(f'tool {name} identity or alias resolution differs')
    if name != 'heaptrack_preload' and not os.access(info['path'], os.X_OK):
        raise ValueError(f'tool {name} is not executable')
    if name == 'shell' and re.search(r'\s', info['path']):
        raise ValueError('shell path cannot be used in a shebang')
    return dict(info, alias=spec['alias'])


def verify_harness(bound, deadline_ns=None):
    """The loaded code must be exactly the reviewed set: same files at import, now, and in the binding."""
    loaded = loaded_harness()
    if set(loaded) != set(HARNESS_MODULES) or set(bound or {}) != set(HARNESS_MODULES):
        raise ValueError('imported harness modules differ from the bound reviewed set: loaded ' +
                         ', '.join(sorted(loaded)))
    result = {}
    for name in HARNESS_MODULES:
        current = bounded_info(loaded[name].__file__, deadline_ns)
        imported = IMPORTED.get(name, {})
        if not (bound[name].get('sha256') == imported.get('sha256') == current['sha256'] and
                bound[name].get('path') == imported.get('path') == current['path']):
            raise ValueError(f'harness module {name} differs from its reviewed binding')
        result[name] = current
    return result


def verify(args, deadline_ns=None):
    """Every provenance check reads and hashes files only; with a deadline, every hash is chunk-bounded."""
    sidecar, sidecar_info = p6.read_json_with_identity(args.sidecar)
    if sidecar_info['sha256'] != args.sidecar_sha256 or sidecar.get('schema') != SIDECAR_SCHEMA:
        raise ValueError('profile sidecar SHA256/schema differs from the supplied binding')
    harness = verify_harness(sidecar.get('harness'), deadline_ns)
    parent, parent_info = p6.read_json_with_identity(args.parent_manifest)
    if parent_info['sha256'] != args.parent_sha256 or sidecar['parent_manifest']['sha256'] != parent_info['sha256']:
        raise ValueError('parent manifest SHA256 differs from the supplied or sidecar binding')
    with bounded_hashing(deadline_ns):
        fixture = bounded_binary_info(parent['fixture_build']['binary']['path'], deadline_ns)
        bound = p6.bind_manifest(parent, fixture)
        meter = bounded_binary_info(parent['meter']['path'], deadline_ns)
        release = dict(go=bounded_binary_info(parent['baselines']['pb-go']['path'], deadline_ns),
                       candidate=bounded_binary_info(parent['build_flags']['candidate']['pb']['path'], deadline_ns))
        role_binding, role_identities = p6.bind_selected_roles(parent, release, meter, False)
        roles = sidecar.get('roles', {})
        if set(roles) != {'go-symbolized', 'rust-symbolized'}:
            raise ValueError('sidecar must bind exactly the Go and Rust symbolized roles')
        go, go_tracked = verify_go(roles['go-symbolized'], parent)
        rust, rust_tracked = verify_rust(roles['rust-symbolized'], parent)
        if set(sidecar.get('tools', {})) != set(TOOL_ALIASES):
            raise ValueError('sidecar must bind every profiler, probe and analysis tool')
        tools = {name: tool_identity(spec, name) for name, spec in sidecar['tools'].items()}
        evidence = [bound_file(spec, 'evidence ' + spec['path']) for spec in sidecar.get('evidence', [])]
    definitions = scenarios(sidecar.get('seed', 2026))
    if sidecar.get('scenarios') != {name: canonical_sha(w) for name, w in definitions.items()}:
        raise ValueError('scenario definitions differ from the sidecar binding')
    executables = {'go-release': release['go'], 'rust-release': release['candidate'],
                   'go-symbolized': go, 'rust-symbolized': rust}
    tracked = [*executables.values(), fixture, meter, parent_info, sidecar_info, bound['budget_file'],
               bound['fixture_manifest'], bound['fixture_toolchain'], *bound['bound_fixture_sources'],
               *bound['bound_runners'], *role_identities, *go_tracked, *rust_tracked,
               *[dict(path=t['path'], sha256=t['sha256'], size_bytes=t['size_bytes']) for t in tools.values()],
               *evidence, *harness.values()]
    return dict(parent=parent, parent_identity=parent_info, sidecar=sidecar, sidecar_identity=sidecar_info,
                fixture=fixture, meter=meter, executables=executables, release_roles=role_binding,
                tools=tools, scenarios=definitions, stable_manifest_sha256=bound['stable_manifest_sha256'],
                harness=harness, tracked=p6.deduplicate_identities(tracked))


def changed_identities(tracked, deadline_ns=None, progress=None):
    """Rehash by path with the bounded reader; DeadlineExceeded stops before the next chunk or file."""
    changed = []
    for info in tracked:
        try:
            if bounded_info(info['path'], deadline_ns)['sha256'] != info['sha256']:
                changed.append(info['path'])
        except (OSError, ValueError):
            changed.append(info['path'])
        if progress is not None:
            progress['checked'] += 1
    return changed


def schedule(mode, repetitions, control_repetitions=1):
    """All uninstrumented controls precede any capture; order alternates by repetition."""
    languages = ['rust'] if mode == 'heaptrack' else ['go', 'rust']
    phases = (('control', [f'{l}-{b}' for b in ('release', 'symbolized') for l in languages], control_repetitions),
              ('capture', [f'{l}-symbolized' for l in languages], repetitions))
    return [dict(phase=phase, role=role, repetition=rep)
            for phase, roles, count in phases for rep in range(count)
            for role in (roles if rep % 2 == 0 else roles[::-1])]


def planned_probes(mode, state):
    tools, readelf = state['tools'], state['tools']['readelf']['path']
    probes = [dict(name=name + '-version', kind='version', tool=name, argv=[tools[name]['path'], *VERSION_ARGS[name]])
              for name in ('readelf', *VERSION_PROBES[mode])]
    roles = state['sidecar']['roles']
    for role in ('go-symbolized', 'rust-symbolized'):
        if mode == 'heaptrack' and role.startswith('go'):
            continue
        binary = state['executables'][role]['path']
        probes += [dict(name=role + '-sections', kind='sections', role=role,
                        saved_sha256=roles[role]['sections']['sha256'],
                        argv=[readelf, *roles[role]['sections']['readelf_args'], binary]),
                   dict(name=role + '-symbols', kind='symbols', role=role, argv=[readelf, '--syms', '--wide', binary])]
    return probes


def capture_argv(mode, tools, binary, directory, event):
    """Reuse profile_external.command, then execute only verified resolved tool paths."""
    tool = tools['strace' if mode.startswith('strace') else 'heaptrack_preload' if mode == 'heaptrack' else mode]
    argv = profiles.command(mode, tool['path'], Path(binary), directory, event)
    if mode == 'perf':
        # The isolated HOME is the capture folder; never copy a 100 MB binary per capture.
        argv.insert(argv.index('--'), '--no-buildid-cache')
    if mode == 'heaptrack':
        if argv[0] != tools['env']['alias']:
            raise ValueError('heaptrack launcher differs from the bound env alias')
        argv[0] = tools['env']['path']
    return argv


def launcher(path, shell, argv):
    """profile_external.wrapper semantics, with the verified shell instead of the /bin/sh alias."""
    with open(path, 'x') as stream:
        stream.write(f'#!{shell}\nexec ' + shlex.join([str(a) for a in argv]) + ' "$@"\n')
    path.chmod(0o500)
    return p6.file_info(path)


ARTIFACT_NAMES = {'perf': 'perf.data', 'strace': 'strace.txt', 'strace-writes': 'writes.trace'}


def artifact_identity(mode, directory, deadline_ns):
    """profile_external.artifacts (same name/uniqueness/regular/non-empty rules), hashed with the bounded reader."""
    paths = list(directory.glob('heaptrack*')) if mode == 'heaptrack' else [directory / ARTIFACT_NAMES[mode]]
    if len(paths) != 1 or paths[0].is_symlink() or not paths[0].is_file() or not paths[0].stat().st_size:
        raise ValueError(f'missing or ambiguous {mode} capture')
    return [bounded_info(paths[0], deadline_ns)]


@contextlib.contextmanager
def file_size_limit(limit):
    """Per-file soft RLIMIT_FSIZE (and no core files) for this process and every child started meanwhile.

    Children receive SIGXFSZ (default action restored by posix_spawn/Popen); Python gets EFBIG.
    This caps each file, not the number of files, total disk use or wall time; it is a soft limit.
    """
    previous = {kind: resource.getrlimit(kind) for kind in (resource.RLIMIT_FSIZE, resource.RLIMIT_CORE)}
    hard = previous[resource.RLIMIT_FSIZE][1]
    soft = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
    resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
    resource.setrlimit(resource.RLIMIT_CORE, (0, previous[resource.RLIMIT_CORE][1]))
    try:
        yield soft
    finally:
        for kind, value in previous.items():
            resource.setrlimit(kind, value)


def output_metadata(path, deadline_ns, hash_output=True):
    """Identity of an owned output file; once the deadline has passed it is size-only, never a hash."""
    path = Path(path)
    try:
        if not path.is_file():
            return dict(path=str(path), present=False)
        size = path.stat().st_size
        if not hash_output:
            return dict(path=str(path), size_bytes=size, sha256=None,
                        hash_unavailable='discarded intermediate; size only')
        try:
            return bounded_info(path, deadline_ns)
        except DeadlineExceeded:
            return dict(path=str(path), size_bytes=size, sha256=None, hash_unavailable='deadline passed')
    except OSError as error:
        return dict(path=str(path), sha256=None, hash_unavailable=f'{type(error).__name__}: {error}')


def scan_lines(path, deadline_ns):
    """Line iteration with a deadline check before every line."""
    with open(path, errors='replace') as stream:
        for line in stream:
            if expired(deadline_ns):
                raise DeadlineExceeded(f'deadline reached while parsing {Path(path).name}')
            yield line


def run_probe(probe, directory, records, deadline_ns=None):
    """The record is appended before running, so a failure or timeout is persisted before raising.

    Output identities and parsing are deadline-bounded; after expiry only sizes are recorded.
    """
    out, err = directory / (probe['name'] + '.stdout'), directory / (probe['name'] + '.stderr')
    record = dict(probe, returncode=None, timed_out=False, error=None, output_cap_bytes=PROBE_OUTPUT_BYTES)
    records.append(record)
    try:
        with open(out, 'xb') as stdout, open(err, 'xb') as stderr, file_size_limit(PROBE_OUTPUT_BYTES):
            try:
                result = subprocess.run(probe['argv'], stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                        timeout=PROBE_TIMEOUT_S, env=dict(PATH=os.defpath, LANG='C', LC_ALL='C',
                                        HOME=str(directory), TMPDIR=str(p6.TMP), DEBUGINFOD_URLS=''))
                record['returncode'] = result.returncode
            except subprocess.TimeoutExpired:
                record['timed_out'] = True
        record.update(stdout=output_metadata(out, deadline_ns), stderr=output_metadata(err, deadline_ns))
        if expired(deadline_ns):
            raise DeadlineExceeded(f"deadline reached after probe {probe['name']}")
        if record['timed_out'] or record['returncode'] != 0:
            raise ValueError(f"probe {probe['name']} failed (returncode {record['returncode']}, "
                             f"timed_out {record['timed_out']})")
        if record['stdout'].get('size_bytes', 0) >= PROBE_OUTPUT_BYTES:
            raise ValueError(f"probe {probe['name']} reached its output cap")
        if probe['kind'] == 'version':
            with open(out, errors='replace') as stream:
                record['version'] = stream.read(4096).strip()
        elif probe['kind'] == 'sections':
            found = set()
            for line in scan_lines(out, deadline_ns):
                found.update(name for name in DEBUG_SECTIONS if re.search(r'\s' + re.escape(name) + r'\s', line))
            missing = [name for name in DEBUG_SECTIONS if name not in found]
            if missing:
                raise ValueError(f"{probe['role']} lacks debug sections: " + ', '.join(missing))
            record.update(debug_sections=list(DEBUG_SECTIONS),
                          saved_sections_byte_identical=record['stdout']['sha256'] == probe['saved_sha256'])
        else:
            entries = functions = None
            for line in scan_lines(out, deadline_ns):
                match = re.match(r"Symbol table '\.symtab' contains (\d+) entries", line)
                if match:
                    entries, functions = int(match[1]), 0
                elif functions is not None and re.match(r'\s*\d+:\s+[0-9a-f]+\s+(?:0x[0-9a-f]+|\d+)\s+FUNC\s', line):
                    functions += 1
            if not entries or not functions:
                raise ValueError(f"{probe['role']} has no .symtab function symbols")
            record.update(symtab_entries=entries, symtab_functions=functions)
        return record
    except BaseException as error:
        record['error'] = f'{type(error).__name__}: {error}'
        for key, path in (('stdout', out), ('stderr', err)):
            if key not in record:
                record[key] = output_metadata(path, deadline_ns)
        raise


def perf_paranoid():
    try:
        return int(PERF_PARANOID.read_text().strip())
    except (OSError, ValueError) as error:
        raise ValueError(f'perf availability unknown: {error}') from error


def new_output(path, state):
    out = p6.safe_path(path)
    frozen = [Path(info['path']).parent for info in (state['parent_identity'], state['fixture'], state['meter'],
                                                     *state['executables'].values())]
    if out.exists() or any(out == d or d in out.parents for d in frozen):
        raise ValueError('output must be new and outside frozen provenance directories')
    out.mkdir(parents=True)
    return out


@contextlib.contextmanager
def interrupt_signals():
    """SIGINT/SIGTERM/SIGHUP all take the orderly cleanup path, even if SIGINT was inherited ignored."""
    def interrupt(signum, frame):
        raise KeyboardInterrupt(f'signal {signum}')
    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@contextlib.contextmanager
def offline_lock():
    """No token: offline prepare/plan/analysis never overlap a reserved window or another lock holder."""
    with p6.safe_path(p6.PERFORMANCE_LOCK).open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def remaining_s(budget):
    return (budget.deadline - p6.clock_ns()) / 1e9


def rejected(error, **extra):
    """Refused before any output exists; nothing was launched."""
    print(json.dumps(dict(valid=False, launched=False, output_created=False,
                          error=f'{type(error).__name__}: {error}', **extra)))
    return 2


def sample(binary, workload, state, folder, collector_mode):
    try:
        return p6.run_sample(binary, workload, state['fixture']['path'], state['meter']['path'], folder,
                             collector_mode=collector_mode)
    except BaseException as error:
        # run_sample owns fixture/meter cleanup; keep any capture it already saved.
        value = {}
        for name in ('sample.json', 'capture.json'):
            with contextlib.suppress(OSError, ValueError):
                value = p6.read_json(folder / name)
                break
        value.setdefault('errors', []).append(type(error).__name__ + ': ' + str(error))
        value.update(valid=False, contract_validated=False, collector_exception=True)
        return value


def hash_s(size_bytes):
    return -(-size_bytes // HASH_BYTES_PER_S)


def entry_directory(index, item):
    """The only folder name a schedule item can have, in capture and in analysis."""
    return f"{index:03d}-{item['phase']}-{item['role']}-r{item['repetition']}"


def sample_file_cap(item, mode):
    """Every file a sample writes stays below this; the profiler artifact has its own stricter post-check."""
    return max(MAX_ARTIFACT_BYTES[mode], SAMPLE_FILE_FLOOR_BYTES) if item['phase'] == 'capture' else SAMPLE_FILE_FLOOR_BYTES


def item_reserve_s(item, mode, state):
    """Planning estimate: pre-launch executable rehash plus, for captures, hashing an artifact up to its cap."""
    size = state['executables'][item['role']]['size_bytes']
    return hash_s(size + (MAX_ARTIFACT_BYTES[mode] if item['phase'] == 'capture' else 0))


def final_reserve_s(state, items, mode):
    captured = sum(MAX_ARTIFACT_BYTES[mode] for item in items if item['phase'] == 'capture')
    return hash_s(sum(info['size_bytes'] for info in state['tracked']) + captured) + FINAL_FLOOR_S


def control_gate(report, planned):
    controls = [e for e in report['samples'] if e['phase'] == 'control']
    groups = {}
    for entry in controls:
        groups.setdefault(entry['role'], []).append(entry['result'])
    comparison = p6.comparable_contracts(groups) if groups else dict(comparable=False, reasons=['no controls'])
    reasons = list(comparison['reasons'])
    if len(controls) != planned:
        reasons.append(f'{len(controls)} of {planned} planned controls executed')
    if any(e['errors'] for e in controls):
        reasons.append('control entry errors')
    return dict(passed=not reasons, reasons=reasons, comparison=comparison)


def evaluate(report):
    """Global gates first: nothing is interpretable unless the whole partition is clean."""
    entries = report['samples']
    gate, end, final, harness = (report.get(key) or {} for key in
                                 ('control_gate', 'end_identity_check', 'finalization', 'harness_end_check'))
    reasons = []
    if not report['complete']:
        reasons.append('partition incomplete: ' + str(report.get('stop_reason') or report.get('failure')))
    if report.get('failure'):
        reasons.append('failure: ' + report['failure'])
    if report.get('quarantined') or not end.get('passed'):
        reasons.append('end identity check failed, incomplete or not performed inside the lock')
    if not harness.get('passed'):
        reasons.append('loaded harness set changed during the window')
    if not gate.get('passed'):
        reasons.append('control gate not passed: ' + '; '.join(gate.get('reasons', ['not evaluated'])))
    if not final.get('completed') or final.get('window_overrun'):
        reasons.append('finalization not completed inside the lock and window')
    reference = next((e['result']['contract'] for e in entries
                      if e['phase'] == 'control' and e['result'].get('valid')), None) if gate.get('passed') else None
    groups = {}
    for entry in entries:
        groups.setdefault(f"{entry['phase']}:{entry['role']}", []).append(entry['result'])
        entry['contract_matches_reference'] = reference is not None and p6.comparable_contracts(
            {'reference': [dict(valid=True, contract=reference)], 'sample': [entry['result']]})['comparable']
        own = [reason for reason, failed in (
            ('sample invalid', not entry['result'].get('valid')), ('entry errors', bool(entry['errors'])),
            ('contract differs from the gated controls', not entry['contract_matches_reference']),
            ('artifact unavailable', entry['phase'] == 'capture' and not entry.get('artifacts'))) if failed]
        entry['uninterpretable_reasons'] = own + reasons if entry['phase'] == 'capture' else own
        entry['interpretable'] = entry['phase'] == 'capture' and not entry['uninterpretable_reasons']
    executed = {entry['index']: entry for entry in entries}
    not_run = str(report.get('stop_reason') or report.get('failure') or 'not reached')
    report['accounting'] = [dict(item, index=index, executed=index in executed,
                                 status=('not_executed' if index not in executed else
                                         'interpretable' if executed[index]['interpretable'] else
                                         'valid_control' if item['phase'] == 'control' and
                                         not executed[index]['uninterpretable_reasons'] else 'not_interpretable'),
                                 reasons=executed[index]['uninterpretable_reasons'] if index in executed else [not_run])
                            for index, item in enumerate(report['schedule'])]
    roles = {}
    for row in report['accounting']:
        counts = roles.setdefault(row['role'], dict(planned=0, executed=0, valid_controls=0, interpretable_captures=0))
        counts['planned'] += 1
        counts['executed'] += row['executed']
        counts['valid_controls'] += row['status'] == 'valid_control'
        counts['interpretable_captures'] += row['status'] == 'interpretable'
    report['roles'] = roles
    report['global_gate'] = dict(passed=not reasons, reasons=reasons)
    report['contract_comparison'] = (p6.comparable_contracts(groups) if groups else
                                     dict(comparable=False, reasons=['no samples']))
    report['passed'] = bool(not reasons and report['contract_comparison']['comparable'] and
                            all(row['status'] in ('interpretable', 'valid_control') for row in report['accounting']))


def summary(state):
    return dict(parent_manifest=state['parent_identity'], sidecar=state['sidecar_identity'],
                stable_manifest_sha256=state['stable_manifest_sha256'], fixture=state['fixture'],
                meter=state['meter'], executables=state['executables'], release_roles=state['release_roles'],
                tools=state['tools'], harness=state['harness'])


def preflight(args, deadline_ns=None):
    state = verify(args, deadline_ns)
    workload = state['scenarios'][args.scenario]
    items = schedule(args.mode, args.repetitions, args.control_repetitions)
    probes = planned_probes(args.mode, state)
    entry = hash_s(sum(info['size_bytes'] for info in state['tracked']))
    children = sum(workload['timeout_s'] + p6.WINDOW_CLEANUP_RESERVE_S + item_reserve_s(i, args.mode, state)
                   for i in items)
    final = final_reserve_s(state, items, args.mode)
    probes_s = len(probes) * (PROBE_TIMEOUT_S + PROBE_RESERVE_S)
    required = entry + probes_s + children + final
    budget = dict(required_s=required, reserved_s=args.window_budget_s, fits=required <= args.window_budget_s,
                  after_verification_s=probes_s + children + final, entry_rehash_s=entry, probes_s=probes_s,
                  children_s=children, final_reserve_s=final, hash_rate_bytes_per_s=HASH_BYTES_PER_S,
                  scope='child timeouts + 30 s cleanup + planned hashing at an assumed rate (estimate only); '
                        'the window deadline, checked before every hash chunk and every child, bounds the work')
    perf = dict(event=args.perf_event, paranoid=perf_paranoid() if args.mode == 'perf' else None,
                fallback='none: an unavailable event fails the capture')
    return state, workload, items, probes, budget, perf


def base_report(args, state, workload, items, probes, budget, perf):
    return dict(schema=REPORT_SCHEMA, kind='profile_only', native_evidence=False, scope=SCOPE,
                complete=False, passed=False, failure=None, stop_reason=None, mode=args.mode, perf=perf,
                scenario=dict(name=workload['name'], kind=workload['scenario_kind'], definition=workload,
                              sha256=canonical_sha(workload)),
                collector_mode=args.collector_mode, repetitions=args.repetitions,
                control_repetitions=args.control_repetitions, planned_samples=len(items),
                schedule=items, planned_probes=probes, partition_budget=budget,
                caps=dict(artifact_bytes=MAX_ARTIFACT_BYTES[args.mode],
                          capture_sample_file_bytes=max(MAX_ARTIFACT_BYTES[args.mode], SAMPLE_FILE_FLOOR_BYTES),
                          control_sample_file_bytes=SAMPLE_FILE_FLOOR_BYTES, probe_output_bytes=PROBE_OUTPUT_BYTES,
                          hash_chunk_bytes=CHUNK_BYTES,
                          mechanism='per-file soft RLIMIT_FSIZE, RLIMIT_CORE=0; artifact cap checked after capture'),
                quiet_window_token_sha256=(hashlib.sha256(args.quiet_window_token.encode()).hexdigest()
                                           if args.quiet_window_token else None),
                lock=str(p6.PERFORMANCE_LOCK), provenance=summary(state), unbound=UNBOUND,
                python=dict(pycache_prefix=sys.pycache_prefix, dont_write_bytecode=sys.dont_write_bytecode),
                ownership=dict(coordinator_pid=os.getpid(), coordinator_pgid=os.getpgrp(),
                    note='run_sample owns and reaps only its fixture and meter process groups; nothing else is '
                         'signalled; SIGINT, SIGTERM and SIGHUP abort through the same cleanup path'),
                resource_scopes=dict(control='CLI under pb-meter; uninstrumented control, not native timing evidence',
                    capture='profiler process tree (wrapper/profiler/CLI); overhead inseparable',
                    heaptrack='CLI with heaptrack preload hooks and raw-record I/O; Rust native allocator only'),
                excluded=['timings and resources excluded from contracts', 'no native ranking or ratio',
                          'no live-service delivery guarantee', 'Go managed heap not profiled'],
                probes=[], samples=[], tracked_identities=state['tracked'])


def captures(args, state, workload, items, probes, report, out, save, budget):
    final_ns = int(report['partition_budget']['final_reserve_s'] * 1e9)
    work_deadline = budget.deadline - final_ns  # owned hashing leaves the final reserve for the end checks
    (out / 'probes').mkdir()
    for probe in probes:
        if remaining_s(budget) < PROBE_TIMEOUT_S + PROBE_RESERVE_S + report['partition_budget']['final_reserve_s']:
            raise ValueError('quiet window cannot fit the next probe and finalization')
        run_probe(probe, out / 'probes', report['probes'], work_deadline)
        save()
    shell, tools = state['tools']['shell']['path'], state['tools']
    planned_controls = sum(item['phase'] == 'control' for item in items)
    for index, item in enumerate(items):
        capture = item['phase'] == 'capture'
        if capture and 'control_gate' not in report:
            report['control_gate'] = control_gate(report, planned_controls)
            save()
            if not report['control_gate']['passed']:
                report['stop_reason'] = 'control gate failed before any capture'
                return
        reserve = item_reserve_s(item, args.mode, state)
        admission = budget.admission(dict(timeout_s=workload['timeout_s'] + reserve +
                                          report['partition_budget']['final_reserve_s']))
        cap = sample_file_cap(item, args.mode)
        stat = os.statvfs(out)
        disk = dict(free_bytes=stat.f_bavail * stat.f_frsize, required_bytes=cap + DISK_HEADROOM_BYTES)
        if not admission['fits'] or disk['free_bytes'] < disk['required_bytes']:
            report.update(window_stop=dict(next=item, admission=admission, disk=disk),
                          stop_reason='window or disk admission refused the next child')
            return
        executable = state['executables'][item['role']]
        used = [executable] + [tools[name] for name in ('shell', *CAPTURE_TOOLS[args.mode]) if capture]
        changed = changed_identities(used, work_deadline)
        if changed:
            raise ValueError('executable identity changed before launch: ' + ', '.join(changed))
        folder = out / entry_directory(index, item)
        folder.mkdir()
        entry = dict(item, index=index, language=item['role'].split('-')[0], directory=folder.name,
                     executed=executable, admission=admission, disk=disk, sample_file_cap_bytes=cap, errors=[],
                     result=dict(valid=False, errors=['child did not complete']),
                     resource_scope=report['resource_scopes']['heaptrack' if capture and args.mode == 'heaptrack'
                                                              else 'capture' if capture else 'control'])
        binary = executable['path']
        if capture:
            entry['argv_prefix'] = capture_argv(args.mode, tools, binary, folder, args.perf_event)
            entry['launcher'] = launcher(folder / 'launch', shell, entry['argv_prefix'])
            binary = entry['launcher']['path']
        report['samples'].append(entry)
        with file_size_limit(cap):
            entry['result'] = sample(binary, workload, state, folder / 'capture', args.collector_mode)
        if capture:
            try:
                entry['artifacts'] = artifact_identity(args.mode, folder, work_deadline)
                if entry['artifacts'][0]['size_bytes'] >= MAX_ARTIFACT_BYTES[args.mode]:
                    entry['errors'].append('profile artifact reached its artifact cap; possibly truncated')
            except (OSError, ValueError) as error:
                entry['errors'].append(str(error))
        save()
        if entry['result'].get('collector_exception'):
            raise RuntimeError('collector exception; completed samples preserved')
        if not capture and (not entry['result'].get('valid') or entry['errors']):
            report['control_gate'] = dict(passed=False, reasons=[f"{item['role']} control invalid: " +
                                          '; '.join(map(str, entry['result'].get('errors', [])))])
            report['stop_reason'] = 'invalid control; no further child launched'
            return
    report['complete'] = True


def finalize(report, state, budget, save):
    """End checks, evaluation and the report write happen while the common lock is held.

    Hashing stops at the window deadline (before the next chunk), leaving an incomplete, quarantined check.
    """
    started = p6.clock_ns()
    final = report['finalization'] = dict(inside_lock=True, completed=False, remaining_at_start_s=remaining_s(budget),
                                          reserve_s=report['partition_budget']['final_reserve_s'])
    try:
        loaded = sorted(loaded_harness())
        report['harness_end_check'] = dict(passed=loaded == sorted(HARNESS_MODULES), loaded=loaded)
        if loaded != sorted(HARNESS_MODULES):
            report.update(quarantined=True, failure=report['failure'] or 'harness modules loaded during the window')
        if report['samples'] or report['probes']:
            identities = state['tracked'] + [e['launcher'] for e in report['samples'] if 'launcher' in e] + [
                artifact for e in report['samples'] for artifact in e.get('artifacts') or []]
            progress = dict(checked=0)
            try:
                changed = changed_identities(identities, budget.deadline, progress)
                report['end_identity_check'] = dict(performed=True, passed=not changed, changed=changed,
                                                    checked=progress['checked'], total=len(identities))
            except DeadlineExceeded as error:
                changed = None
                report['end_identity_check'] = dict(performed=True, passed=False, incomplete='deadline',
                                                    checked=progress['checked'], total=len(identities))
                report.update(failure=report['failure'] or f'end identity check stopped: {error}')
            if changed is None or changed:
                report.update(quarantined=True, failure=report['failure'] or 'identities changed during capture')
        else:
            report['end_identity_check'] = dict(performed=False, passed=False, reason='nothing launched')
        final['completed'] = True
    except BaseException as error:
        report.update(failure=report['failure'] or f'finalization {type(error).__name__}: {error}')
    finally:
        final.update(elapsed_s=(p6.clock_ns() - started) / 1e9, window_overrun=p6.clock_ns() > budget.deadline)
        evaluate(report)
        save()


def capture_command(args):
    budget = p6.WindowBudget(args.window_budget_s)  # every owned step is charged from command entry
    started = budget.deadline - int(args.window_budget_s * 1e9)
    try:
        if p6.safe_path(args.out).exists():
            raise ValueError('output must be new')
    except (OSError, ValueError) as error:
        return rejected(error)
    report, locked = None, False
    try:
        with interrupt_signals(), p6.quiet_lock(p6.PERFORMANCE_LOCK, args.quiet_window_token):
            locked = True
            try:
                # Verification is the entry rehash: inside the lock, inside the window, chunk-bounded.
                prepared = preflight(args, budget.deadline - int(FINAL_FLOOR_S * 1e9))
                state = prepared[0]
                out = new_output(args.out, state)
            except (OSError, ValueError, KeyError, TypeError, DeadlineExceeded) as error:
                return rejected(error, lock_acquired=True)
            report = base_report(args, *prepared)
            report['command'] = dict(started_ns=started, deadline_ns=budget.deadline,
                                     window_budget_s=args.window_budget_s,
                                     verified_s=(p6.clock_ns() - started) / 1e9,
                                     order='arguments, then lock, then verification and output')
            save = lambda: p6.write_json(out / 'report.json', report)
            save()
            try:
                needed = report['partition_budget']['after_verification_s']
                if needed > remaining_s(budget):
                    raise ValueError(f'profile partition needs {needed}s but {remaining_s(budget):.1f}s remain; '
                                     'no child started')
                if args.mode == 'perf' and prepared[5]['paranoid'] > 2:
                    raise ValueError('perf user-space events unavailable (perf_event_paranoid > 2); no fallback')
                captures(args, state, prepared[1], prepared[2], prepared[3], report, out, save, budget)
            except BaseException as error:
                report.update(complete=False, failure=f'{type(error).__name__}: {error}')
            finally:
                finalize(report, state, budget, save)
    except BaseException as error:
        if report is None:
            return rejected(error, lock_acquired=locked)
        report.update(failure=report['failure'] or f'{type(error).__name__}: {error}', passed=False)
        save()
    print(json.dumps(dict(report=str(out / 'report.json'), passed=report['passed'], failure=report['failure'])))
    return 0 if report['passed'] else 1


def plan_command(args):
    """Offline: outside any reserved window, under the common lock taken without a token."""
    try:
        with offline_lock():
            state, workload, items, probes, budget, perf = preflight(args)
            out = new_output(args.out, state)
            folder = out / '000-capture-<role>-r0'
            planned = {role: capture_argv(args.mode, state['tools'], state['executables'][role]['path'], folder,
                                          args.perf_event)
                       for role in sorted({i['role'] for i in items if i['phase'] == 'capture'})}
            report = base_report(args, state, workload, items, probes, budget, perf)
            report.update(kind='profile_plan', launched=False, planned_capture_argv=planned)
            p6.write_json(out / 'plan.json', report)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return rejected(error)
    print(json.dumps(dict(plan=str(out / 'plan.json'), fits=budget['fits'])))
    return 0 if budget['fits'] else 1


def prepare_command(args):
    """Offline: outside any reserved window, under the common lock taken without a token."""
    out = p6.safe_path(args.out)
    if out.exists():
        raise SystemExit('sidecar output must be new')
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(offline_lock())
        except OSError as error:
            return rejected(error)
        parent, parent_info = p6.read_json_with_identity(args.parent_manifest)
        if parent_info['sha256'] != args.parent_sha256:
            raise SystemExit('parent manifest SHA256 differs')
        harness = {name: p6.file_info(module.__file__) for name, module in loaded_harness().items()}
        if harness != IMPORTED or set(harness) != set(HARNESS_MODULES):
            raise SystemExit('harness sources changed since import or differ from the expected set')
        for runner in parent['sources'].get('runners', {}).values():
            current = next((info for info in harness.values() if info['path'] == runner['path']), None)
            if current and current['sha256'] != runner['sha256']:
                raise SystemExit('harness module differs from the parent-declared runner: ' + runner['path'])
        rust_build, rust_info = p6.read_json_with_identity(args.rust_build_manifest)
        go_build, go_info = p6.read_json_with_identity(args.go_build_manifest)
        roles = {'rust-symbolized': dict(language='rust', binary=p6.file_info(rust_build['binary']['path']),
                     build_manifest=rust_info, sections=dict(rust_build['sections'], readelf_args=['-S'])),
                 'go-symbolized': dict(language='go', binary=p6.file_info(go_build['binary']['path']),
                     build_manifest=go_info, release_provenance=p6.file_info(args.go_release_provenance),
                     sections=dict(p6.file_info(args.go_sections), readelf_args=['-S', '-W'],
                                   readelf_args_basis='inferred from the saved single-line wide section format'))}
        tools = {name: dict(p6.file_info(alias), alias=alias) for name, alias in TOOL_ALIASES.items()}
        evidence = [dict(p6.file_info(path), status='current_supporting') for path in args.evidence] + [
            dict(p6.file_info(path), status='historical_not_certifying',
                 note='hashed for traceability only; certifies nothing about the profiled R6 builds')
            for path in args.historical_evidence]
        sidecar = dict(schema=SIDECAR_SCHEMA, purpose='profile-only binding; the frozen parent is not modified',
                       parent_manifest=parent_info, seed=2026, roles=roles, tools=tools, evidence=evidence,
                       harness={name: harness[name] for name in sorted(harness)},
                       harness_basis='adapter and import closure as settled at sidecar generation; '
                                     'a fresh review must compare these hashes with the reviewed sources',
                       scenarios={name: canonical_sha(w) for name, w in scenarios(2026).items()})
        with open(out, 'x') as stream:
            stream.write(json.dumps(sidecar, indent=2) + '\n')
        args.sidecar, args.sidecar_sha256 = out, p6.file_info(out)['sha256']
        try:
            verify(args)
        except BaseException:
            out.unlink()  # created exclusively above; never a supplied path
            raise
    print(json.dumps(dict(sidecar=str(out), sha256=args.sidecar_sha256)))
    return 0


STATS_ROW = re.compile(r'^\s*([A-Z0-9_]+) events:\s*(\d+)(?:\s+\(\s*\d+(?:\.\d+)?%\))?\s*$')
STATS_HEADER = re.compile(r'^\S.* stats:\s*$')


def parse_perf_stats(text):
    """Counts only from the one `Aggregated stats:` block; absent or unrecognized rows stay unknown (None)."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip().startswith('Aggregated stats:')]
    unknown = dict(sample_events=None, lost_events=None, lost_sample_events=None, rows=None)
    if len(starts) != 1:
        return dict(unknown, format='no single Aggregated stats block')
    rows = {}
    for line in lines[starts[0] + 1:]:
        if STATS_HEADER.match(line):
            break
        if not line.strip():
            continue
        match = STATS_ROW.match(line)
        if not match or match[1] in rows:
            return dict(unknown, format='unrecognized or duplicate aggregated row: ' + line.strip()[:120])
        rows[match[1]] = int(match[2])
    return dict(sample_events=rows.get('SAMPLE'), lost_events=rows.get('LOST'),
                lost_sample_events=rows.get('LOST_SAMPLES'), rows=rows, format='aggregated block')


def capture_stack_status(stats):
    """Per-capture floor: each perf report is separate, so aggregates never certify a stack."""
    if not stats or None in (stats.get('sample_events'), stats.get('lost_events'), stats.get('lost_sample_events')):
        return 'unavailable'
    if stats['lost_events'] or stats['lost_sample_events']:
        return 'lost_events'
    return 'sufficient_for_descriptive_stacks' if stats['sample_events'] >= MIN_PERF_SAMPLES else 'insufficient_samples'


def analysis_env(home):
    return dict(PATH=os.defpath, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                XDG_CACHE_HOME=str(home / 'cache'), LANG='C', LC_ALL='C', TMPDIR=str(p6.TMP), DEBUGINFOD_URLS='')


def step_status(error):
    return ('deadline' if isinstance(error, DeadlineExceeded) else 'interrupted' if isinstance(error, KeyboardInterrupt)
            else 'timed_out' if isinstance(error, subprocess.TimeoutExpired) else 'failed')


def run_tool(argv, stdout_path, stderr_path, env, deadline_ns, steps, save, stdin_path=None, hash_output=True):
    """One analysis subprocess in its owned folder, journaled in analysis.json before it starts.

    The timeout is clipped to the deadline. Failure, interruption or expiry keeps argv, return code and
    stdout/stderr metadata in the step; after the deadline that metadata is size-only.
    """
    folder = Path(stdout_path).parent
    step = dict(argv=[str(a) for a in argv], status='in_progress', returncode=None, cwd=str(folder))
    steps.append(step)
    save()
    try:
        remaining = (deadline_ns - p6.clock_ns()) / 1e9
        if remaining <= 0:
            raise DeadlineExceeded(f'deadline reached before {Path(argv[0]).name}')
        timeout = min(ANALYSIS_TIMEOUT_S, remaining)
        step['timeout_s'] = timeout
        with open(stdout_path, 'xb') as out, open(stderr_path, 'xb') as err, \
                (open(stdin_path, 'rb') if stdin_path else contextlib.nullcontext(subprocess.DEVNULL)) as source:
            try:
                result = subprocess.run(argv, stdin=source, stdout=out, stderr=err, timeout=timeout, env=env,
                                        cwd=folder)
            except subprocess.TimeoutExpired as error:
                if expired(deadline_ns):
                    raise DeadlineExceeded(f'deadline reached during {Path(argv[0]).name}') from error
                raise
        step['returncode'] = result.returncode
        if expired(deadline_ns):
            raise DeadlineExceeded(f'deadline reached during {Path(argv[0]).name}')
        if result.returncode or not Path(stdout_path).stat().st_size:
            raise ValueError(f'{Path(argv[0]).name} failed (returncode {result.returncode}) or produced no output')
        step['status'] = 'completed'
    except BaseException as error:
        step.update(status=step_status(error), error=f'{type(error).__name__}: {error}')
        raise
    finally:
        step.update(stdout_identity=output_metadata(stdout_path, deadline_ns, hash_output),
                    stderr_identity=output_metadata(stderr_path, deadline_ns))
        save()
    return step


def bounded_gzip(source, target, deadline_ns):
    """profile_external.analyze's gzip copy (default level 9) with a deadline check before each chunk."""
    buffer = bytearray(CHUNK_BYTES)
    view = memoryview(buffer)
    with open(source, 'rb') as plain, gzip.open(target, 'xb') as packed:
        while True:
            if expired(deadline_ns):
                raise DeadlineExceeded('deadline reached while compressing the heaptrack intermediate')
            count = read_chunk(plain, buffer)
            if not count:
                break
            packed.write(view[:count])
    return bounded_info(target, deadline_ns)


def bounded_write_counts(path, deadline_ns):
    """profile_external.write_counts, verbatim parsing rules, with a deadline check before each record."""
    counts, pending = {}, {}
    with Path(path).open() as stream:
        for line in stream:
            if expired(deadline_ns):
                raise DeadlineExceeded('deadline reached while parsing the write trace')
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


HASHED_OUTPUTS = {'perf': 4, 'heaptrack': 4, 'strace': 0, 'strace-writes': 0}  # files hashed per capture
TOOL_STEPS = {'perf': 2, 'heaptrack': 2, 'strace': 0, 'strace-writes': 0}  # bounded subprocess steps per capture


def analysis_entry_s(mode, entry):
    """Planning estimate per capture: bounded tool steps and cleanup, hashing the artifact before and after,
    and hashing each output up to its cap at the assumed rate. The deadline, not this estimate, enforces."""
    size = entry['artifacts'][0]['size_bytes']
    return (TOOL_STEPS[mode] * ANALYSIS_TIMEOUT_S + p6.WINDOW_CLEANUP_RESERVE_S +
            hash_s(2 * size + HASHED_OUTPUTS[mode] * ANALYSIS_FILE_BYTES))


def analyze_one(entry, mode, tools, target, home, deadline_ns, record, save):
    """Reads (and hashes) only the sealed capture artifact; every output goes to a new analysis-owned folder."""
    target.mkdir()
    steps = record.setdefault('steps', [])
    profile = entry['artifacts'][0]
    if profile['size_bytes'] >= MAX_ARTIFACT_BYTES[mode] or bounded_info(profile['path'], deadline_ns) != profile:
        raise ValueError('profile artifact changed or reached its cap')
    env = analysis_env(home)
    with file_size_limit(ANALYSIS_FILE_BYTES):
        if mode == 'strace-writes':
            counts = bounded_write_counts(profile['path'], deadline_ns)
            traced = counts.get('1', {}).get('bytes')
            record.update(writes_by_fd=counts, stdout_bytes_match=traced == entry['result'].get('stdout_bytes'))
            if not record['stdout_bytes_match']:
                record['error'] = (f"traced FD 1 bytes {traced} differ from captured stdout bytes "
                                   f"{entry['result'].get('stdout_bytes')}")
        elif mode == 'strace':
            record.update(summary=profile, note='strace -c table retained as captured; instrumented syscall time')
        elif mode == 'perf':
            perf = tools['perf']['path']
            record['report'] = run_tool([perf, 'report', '--stdio', '--no-children', '--percent-limit', '0.5',
                                         '-i', profile['path']], target / 'analysis.txt', target / 'analysis.stderr',
                                        env, deadline_ns, steps, save)
            # --no-skip-empty prints zero LOST rows; a perf that rejects it fails closed (entry failed).
            stats = run_tool([perf, 'report', '--stats', '--no-skip-empty', '-i', profile['path']],
                             target / 'perf-stats.txt', target / 'perf-stats.stderr', env, deadline_ns, steps, save)
            with open(target / 'perf-stats.txt', 'rb') as stream:
                text = stream.read(STATS_READ_BYTES + 1)
            parsed = (parse_perf_stats(text.decode(errors='replace')) if len(text) <= STATS_READ_BYTES else
                      dict(sample_events=None, lost_events=None, lost_sample_events=None, format='stats too large'))
            record['stats'] = dict(parsed, output=stats['stdout_identity'])
            record['stack_status'] = capture_stack_status(record['stats'])
        else:
            plain = target / 'interpreted.txt'
            try:
                record['interpret'] = run_tool([tools['heaptrack_interpreter']['path']], plain,
                                               target / 'interpret.stderr', env, deadline_ns, steps, save,
                                               stdin_path=profile['path'], hash_output=False)
                record['interpreted'] = bounded_gzip(plain, target / 'interpreted.gz', deadline_ns)
            finally:
                plain.unlink(missing_ok=True)  # finite scratch; never retained after success or failure
            record['report'] = run_tool([tools['heaptrack_print']['path'], '-m', '0', '-f',
                                         record['interpreted']['path']], target / 'analysis.txt',
                                        target / 'analysis.stderr', env, deadline_ns, steps, save)
            record['scope'] = 'tracked native allocations; not RSS and not Go managed heap'
    if mode != 'perf':
        record['adequacy'] = 'no_adequacy_criterion: processing completed; no usable-profile claim is made'
    if bounded_info(profile['path'], deadline_ns) != profile:
        raise ValueError('profile artifact changed during analysis')


def finish_analysis(result):
    """Completion and interpretation are separate. Only perf has an adequacy criterion (per-capture floor);
    other modes can complete processing but are never interpretable (exit 3). Exit 0 only when every planned
    perf role has an interpretable capture."""
    entries = result['entries']
    complete = bool(result['complete'] and not result['quarantined'] and not result['failure'] and
                    not result['timing'].get('overrun') and entries and
                    (result.get('end_identity_check') or {}).get('passed') is True and
                    (result.get('analysis_harness_end') or {}).get('passed') is True and
                    all(e['status'] == 'analyzed' and 'error' not in e for e in entries))
    adequacy = result['mode'] == 'perf'
    for entry in entries:
        entry['interpretable'] = complete and adequacy and entry['status'] == 'analyzed' and (
            entry.get('stack_status') == 'sufficient_for_descriptive_stacks')
    roles = {}
    for entry in entries:
        row = roles.setdefault(entry['role'], dict(planned_captures=0, analyzed=0, skipped=0, errors=0,
                                                   interpretable_captures=0, statuses={}, stack_status={}))
        row['planned_captures'] += 1
        row['analyzed'] += entry['status'] == 'analyzed'
        row['skipped'] += entry['status'] == 'skipped'
        row['errors'] += 'error' in entry
        row['interpretable_captures'] += entry['interpretable']
        row['statuses'][entry['status']] = row['statuses'].get(entry['status'], 0) + 1
        if 'stack_status' in entry:
            row['stack_status'][entry['stack_status']] = row['stack_status'].get(entry['stack_status'], 0) + 1
    if adequacy:
        for role, row in roles.items():
            known = [e['stats']['sample_events'] for e in entries
                     if e['role'] == role and (e.get('stats') or {}).get('sample_events') is not None]
            row['descriptive_sample_events_total'] = sum(known) if known else None
            row['aggregate_note'] = 'descriptive only; each capture is a separate stack report and needs its own floor'
    total = sum(row['interpretable_captures'] for row in roles.values())
    covered = [role for role, row in roles.items() if row['interpretable_captures']]
    status, code = (('analysis_incomplete', 1) if not complete else
                    ('analysis_complete_no_adequacy_criterion', 3) if not adequacy else
                    ('no_interpretable_capture', 3) if not total else
                    ('partial_role_coverage', 4) if len(covered) != len(roles) else ('all_roles_interpretable', 0))
    result.update(roles=roles, analysis_complete=complete, state='final',
                  interpretation=dict(status=status, interpretable_captures=total, roles_with_interpretable=covered,
                                      roles_without_interpretable=sorted(set(roles) - set(covered)),
                                      adequacy_criterion='per-capture perf floor' if adequacy else None),
                  exit_status=code)


def analyze_command(args):
    budget = p6.WindowBudget(args.budget_s)  # analysis work is charged from command entry
    started = budget.deadline - int(args.budget_s * 1e9)
    try:
        out = p6.safe_path(args.out)
        report_path = p6.safe_path(args.report)
        if out.exists():
            raise ValueError('analysis output directory must be new')
    except (OSError, ValueError) as error:
        return rejected(error)
    result, sealed = None, False
    try:
        with interrupt_signals(), offline_lock():
            try:
                report, report_info = p6.read_json_with_identity(report_path, limit=256 << 20)
                if (report_info['sha256'] != args.report_sha256 or report.get('schema') != REPORT_SCHEMA or
                        report.get('kind') != 'profile_only'):
                    raise ValueError('capture report SHA256/schema binding differs')
                disjoint_output(out, report_path, report)
                for folder in (out, out / 'home', out / 'captures'):
                    folder.mkdir(parents=False if folder != out else True)
            except (OSError, ValueError) as error:
                return rejected(error, lock_acquired=True)
            result = dict(schema=ANALYSIS_SCHEMA, kind='profile_analysis', state='in_progress',
                          capture_report=report_info, mode=report['mode'], scope=SCOPE, unbound=UNBOUND, entries=[],
                          failure=None, complete=False, quarantined=False,
                          minimum_perf_samples_per_capture=MIN_PERF_SAMPLES,
                          timing=dict(started_ns=started, deadline_ns=budget.deadline, budget_s=args.budget_s),
                          outputs=dict(root=str(out), capture_folders='read and hashed only, never written'))
            save = lambda: p6.write_json(out / 'analysis.json', result)
            save()
            try:
                analyze_report(result, report, report_path, out, budget, args.sidecar_sha256, save)
            except BaseException as error:
                result['failure'] = f'{type(error).__name__}: {error}'
            finally:
                try:
                    end_analysis(result, report, budget)
                except BaseException as error:
                    try:
                        seal_incomplete(result, error)
                    except BaseException as again:  # a repeated interrupt: keep an honest incomplete state
                        result.update(state='final', failure=result['failure'] or
                                      f'finalization {type(again).__name__}: {again}')
                        retract_positive(result)
                save()
                sealed = True  # the final journal is on disk; only now may the printed summary be positive
    except BaseException as error:
        # The final save failed or was interrupted, or an error came after it (for example at unlock). Nothing is
        # written here: the lock is released. The on-disk journal stays at its last checkpoint; the exit code and
        # the printed summary are retracted in memory.
        if result is None:
            return rejected(error)
        result['failure'] = result['failure'] or f'{type(error).__name__}: {error}'
        retract_positive(result)
    print(json.dumps(dict(analysis=str(out / 'analysis.json'), exit_status=result.get('exit_status', 1),
                          interpretation=(result.get('interpretation') or {}).get('status', 'analysis_incomplete'),
                          journal_sealed=sealed, failure=result['failure'])))
    return result.get('exit_status', 1)


def disjoint_output(out, report_path, report):
    """Resolved --out shares no ancestor/descendant relation with the capture tree or frozen provenance,
    so a symlink alias or a nested entry folder cannot place analysis output inside sealed evidence."""
    out = Path(out).resolve()
    provenance = report.get('provenance') or {}
    roots = [Path(report_path).resolve().parent] + [
        Path(info['path']).resolve().parent for info in (provenance.get('parent_manifest'), provenance.get('sidecar'),
                                                         provenance.get('fixture'), provenance.get('meter'),
                                                         *(provenance.get('executables') or {}).values())
        if isinstance(info, dict) and info.get('path')]
    for root in roots:
        if out == root or root in out.parents or out in root.parents:
            raise ValueError(f'analysis output must be disjoint from the capture tree and provenance: {root}')


def analysis_identities(report):
    return report['tracked_identities'] + [e['launcher'] for e in report['samples'] if 'launcher' in e] + [
        artifact for e in report['samples'] for artifact in e.get('artifacts') or []]


def verify_report_binding(report, report_path, out, sidecar_sha256, deadline_ns):
    """A rewritten report with a new SHA is not trusted on that SHA alone: its sidecar, harness, tools, roles,
    schedule, entry folders and artifact locations must agree with the coordinator-supplied sidecar."""
    mode = report.get('mode')
    if mode not in CAPTURE_MODES:
        raise ValueError('capture report names an unknown mode')
    spec = report['provenance']['sidecar']
    if spec.get('sha256') != sidecar_sha256:
        raise ValueError('capture report is bound to a different sidecar than the one supplied')
    sidecar, info = p6.read_json_with_identity(spec['path'])
    if info['sha256'] != sidecar_sha256 or sidecar.get('schema') != SIDECAR_SCHEMA:
        raise ValueError('supplied sidecar SHA256/schema differs from the file the report names')
    if report['provenance'].get('harness') != sidecar.get('harness'):
        raise ValueError('report harness differs from the sidecar binding')
    with bounded_hashing(deadline_ns):
        tools = {name: tool_identity(sidecar['tools'][name], name) for name in ANALYSIS_TOOLS[mode]}
    for name, tool in tools.items():
        recorded = report['provenance']['tools'].get(name) or {}
        if (recorded.get('path'), recorded.get('sha256')) != (tool['path'], tool['sha256']):
            raise ValueError(f'report tool {name} differs from the sidecar binding')
    for role in ('go-symbolized', 'rust-symbolized'):
        recorded, bound = report['provenance']['executables'][role], sidecar['roles'][role]['binary']
        if (recorded.get('path'), recorded.get('sha256')) != (str(Path(bound['path']).resolve()), bound['sha256']):
            raise ValueError(f'report executable {role} differs from the sidecar binding')
    if report['schedule'] != schedule(mode, report['repetitions'], report['control_repetitions']):
        raise ValueError('report schedule differs from the schedule its parameters define')
    captures_root = (out / 'captures').resolve()
    for entry in report['samples']:
        index = entry.get('index')
        if type(index) is not int or not 0 <= index < len(report['schedule']):
            raise ValueError('report entry index is invalid')
        item, directory = report['schedule'][index], entry_directory(index, report['schedule'][index])
        if entry.get('directory') != directory or any(entry.get(key) != item[key]
                                                      for key in ('phase', 'role', 'repetition')):
            raise ValueError(f'report entry {index} folder or identity differs from its schedule item')
        if (captures_root / directory).resolve().parent != captures_root:
            raise ValueError(f'report entry {index} would escape the analysis captures folder')
        artifacts = entry.get('artifacts') or []
        if item['phase'] != 'capture' and artifacts:
            raise ValueError(f'report entry {index} is a control and must name no artifact')
        if len(artifacts) > 1 or (entry.get('interpretable') and len(artifacts) != 1):
            # Interpretable capture: exactly one. Failed or skipped capture: zero or one, never analyzed.
            raise ValueError(f'report entry {index} must name exactly one artifact when interpretable, at most one '
                             'otherwise')
        for artifact in artifacts:
            if Path(artifact['path']).parent != (report_path.parent / directory).resolve():
                raise ValueError(f'report entry {index} artifact is outside its capture folder')
            name = Path(artifact['path']).name
            if not (name.startswith('heaptrack') if mode == 'heaptrack' else name == ARTIFACT_NAMES[mode]):
                raise ValueError(f'report entry {index} artifact name {name!r} is not the {mode} artifact')
    return dict(sidecar=info, tools=tools)


def analyze_report(result, report, report_path, out, budget, sidecar_sha256, save):
    mode = report['mode']
    identities = analysis_identities(report)
    final_s = hash_s(sum(info['size_bytes'] for info in identities)) + FINAL_FLOOR_S
    work_deadline = budget.deadline - int(final_s * 1e9)
    # The analysis code itself must be exactly the reviewed set the capture ran with.
    result['analysis_harness'] = verify_harness(report['provenance'].get('harness'), work_deadline)
    binding = verify_report_binding(report, report_path, out, sidecar_sha256, work_deadline)
    tools = binding['tools']
    result['report_binding'] = dict(sidecar=binding['sidecar'], tools=tools,
                                    schedule='rebuilt from report parameters; folders derived from it')
    samples = {entry['index']: entry for entry in report['samples']}
    gate = [reason for reason, failed in (
        ('capture report did not pass: ' + str(report.get('failure') or report.get('stop_reason')), not report.get('passed')),
        ('capture global gate failed', not report.get('global_gate', {}).get('passed')),
        ('capture end identity check failed', not report.get('end_identity_check', {}).get('passed')),
        ('capture quarantined', report.get('quarantined'))) if failed]
    result['capture_gate'] = dict(passed=not gate, reasons=gate)
    pending = []
    for index, item in enumerate(report['schedule']):
        if item['phase'] != 'capture':
            continue
        entry = samples.get(index)
        record = dict(role=item['role'], repetition=item['repetition'], index=index, status='skipped')
        if gate:
            record['reason'] = '; '.join(gate)
        elif entry is None or not entry.get('interpretable'):
            record['reason'] = 'capture not interpretable: ' + '; '.join(
                (entry or {}).get('uninterpretable_reasons') or ['not executed'])
        else:
            record['directory'] = entry_directory(index, item)
            pending.append((record, entry))
        result['entries'].append(record)
    versions = {probe.get('tool'): probe.get('version') for probe in report.get('probes', []) if probe.get('version')}
    result['tool_versions'] = {name: (versions.get(name) if name in VERSION_ARGS else
                                      'no version flag; bound by SHA256 ' + tools[name]['sha256'])
                               for name in ANALYSIS_TOOLS[mode]}
    save()
    if gate:
        raise ValueError('capture report gates failed; nothing analyzed')
    start_s = hash_s(sum(info['size_bytes'] for info in identities))
    per_capture = [analysis_entry_s(mode, entry) for _, entry in pending]
    required = start_s + sum(per_capture) + final_s
    result['budget'] = dict(required_s=required, remaining_s=remaining_s(budget), start_rehash_s=start_s,
                            per_capture_s=per_capture, final_reserve_s=final_s,
                            hash_rate_bytes_per_s=HASH_BYTES_PER_S,
                            scope='planning estimate from actual bound bytes and output caps at an assumed, '
                                  'uncalibrated hash rate; the deadline enforces')
    if required > remaining_s(budget):
        raise ValueError(f'analysis needs {required}s of bounded work; not started')
    changed = changed_identities(identities, work_deadline)
    if changed:
        result['quarantined'] = True
        raise ValueError('identities changed since capture: ' + ', '.join(changed))
    for (record, entry), entry_s in zip(pending, per_capture):
        stat = os.statvfs(out)
        if (remaining_s(budget) < entry_s + final_s or
                stat.f_bavail * stat.f_frsize < 2 * ANALYSIS_FILE_BYTES + DISK_HEADROOM_BYTES):
            raise ValueError('analysis budget or disk headroom exhausted')
        target = out / 'captures' / record['directory']
        record['status'] = 'in_progress'
        save()
        try:
            analyze_one(entry, mode, tools, target, out / 'home', work_deadline, record, save)
            record['status'] = 'analyzed'
        except BaseException as error:
            record.update(status=step_status(error), error=f'{type(error).__name__}: {error}',
                          partial_files=[output_metadata(path, work_deadline) for path in sorted(target.rglob('*'))
                                         if path.is_file()] if target.is_dir() else [])
            save()
            if not isinstance(error, (OSError, ValueError, subprocess.SubprocessError)):
                raise
        save()
    result['complete'] = True


def end_analysis(result, report, budget):
    """Inside the lock: harness and identity end checks, timing, then interpretation."""
    try:
        try:
            result['analysis_harness_end'] = dict(passed=True, modules=verify_harness(
                report['provenance'].get('harness'), budget.deadline))
        except (OSError, ValueError, DeadlineExceeded) as error:
            result['analysis_harness_end'] = dict(passed=False, error=f'{type(error).__name__}: {error}')
            result.update(quarantined=True, failure=result['failure'] or 'analysis harness changed')
        progress = dict(checked=0)
        try:
            changed = changed_identities(analysis_identities(report), budget.deadline, progress)
            result['end_identity_check'] = dict(passed=not changed, changed=changed, checked=progress['checked'],
                                                inside_lock=True)
        except DeadlineExceeded:
            changed = None
            result['end_identity_check'] = dict(passed=False, incomplete='deadline', checked=progress['checked'],
                                                inside_lock=True)
        if changed is None or changed:
            result['quarantined'] = True
    except BaseException as error:
        # An interrupt (SIGINT/SIGTERM/SIGHUP) or unexpected error inside an end check: it never passed.
        result.setdefault('analysis_harness_end', dict(passed=False, error=f'not completed: {type(error).__name__}'))
        result.setdefault('end_identity_check', dict(passed=False, incomplete=type(error).__name__, inside_lock=True))
        result.update(quarantined=True, failure=result['failure'] or f'end checks {type(error).__name__}: {error}')
        raise
    finally:
        now = p6.clock_ns()
        result['timing'].update(elapsed_s=(now - result['timing']['started_ns']) / 1e9, overrun=now > budget.deadline)
        finish_analysis(result)


def retract_positive(result):
    """In memory: no positive completion, interpretation, entry flag or role count survives a failure."""
    roles = result.get('roles') or {}
    for row in roles.values():
        row['interpretable_captures'] = 0
    for entry in result['entries']:
        entry['interpretable'] = False
    result.update(analysis_complete=False, exit_status=1, interpretation=dict(
        status='analysis_incomplete', interpretable_captures=0, roles_with_interpretable=[],
        roles_without_interpretable=sorted(roles)))


def seal_incomplete(result, error):
    """After a failure in the end checks or in interpretation itself: record it, then re-derive interpretation
    with the failure set, so no positive flag from an earlier evaluation can survive."""
    result.update(quarantined=True, failure=result['failure'] or f'finalization {type(error).__name__}: {error}')
    result.setdefault('analysis_harness_end', dict(passed=False, error='not completed'))
    result.setdefault('end_identity_check', dict(passed=False, incomplete='not completed', inside_lock=True))
    finish_analysis(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare', help='offline: write a new profile sidecar; reads and hashes only')
    prepare.add_argument('--out', type=Path, required=True)
    prepare.add_argument('--parent-manifest', type=Path, default=PARENT)
    prepare.add_argument('--parent-sha256', required=True)
    prepare.add_argument('--rust-build-manifest', type=Path, default=CACHE / 'candidate-review-r6/pb-symbolized-manifest.json')
    prepare.add_argument('--go-build-manifest', type=Path, default=CACHE / 'profiling-prepared/go-symbolized-manifest.json')
    prepare.add_argument('--go-sections', type=Path, default=CACHE / 'profiling-prepared/go-sections.txt')
    prepare.add_argument('--go-release-provenance', type=Path,
                         default=Path('/home/ajs/.cache/pb-phase7/profiling/go-release-provenance.json'))
    prepare.add_argument('--evidence', type=Path, action='append', default=[])
    prepare.add_argument('--historical-evidence', type=Path, action='append', default=[])
    for name in ('plan', 'capture'):
        command = sub.add_parser(name, help='offline dry run' if name == 'plan' else 'inside a reserved window')
        command.add_argument('--out', type=Path, required=True)
        command.add_argument('--parent-manifest', type=Path, default=PARENT)
        command.add_argument('--parent-sha256', required=True)
        command.add_argument('--sidecar', type=Path, required=True)
        command.add_argument('--sidecar-sha256', required=True)
        command.add_argument('--scenario', required=True, choices=sorted(scenarios()))
        command.add_argument('--mode', required=True, choices=CAPTURE_MODES)
        command.add_argument('--perf-event', choices=PERF_EVENTS, default='cycles:u')
        command.add_argument('--repetitions', type=int, default=3, help='captures per symbolized role')
        command.add_argument('--control-repetitions', type=int, default=1, help='uninstrumented controls per role')
        command.add_argument('--collector-mode', choices=p6.COLLECTOR_MODES, default='full')
        command.add_argument('--window-budget-s', type=float, required=True)
        command.add_argument('--quiet-window-token', required=name == 'capture', default='')
    analysis = sub.add_parser('analyze', help='offline, after the capture window is released')
    analysis.add_argument('--report', type=Path, required=True)
    analysis.add_argument('--report-sha256', required=True)
    analysis.add_argument('--sidecar-sha256', required=True, help='coordinator-supplied sidecar the report must name')
    analysis.add_argument('--out', type=Path, required=True, help='new analysis-owned directory')
    analysis.add_argument('--budget-s', type=float, required=True)
    args = parser.parse_args()
    if args.command in ('plan', 'capture') and (min(args.repetitions, args.control_repetitions) < 1 or
                                                args.window_budget_s <= 0):
        parser.error('positive repetitions and window budget required')
    if args.command == 'capture' and not args.quiet_window_token:
        parser.error('capture requires the coordinator quiet-window acknowledgement token')
    if args.command == 'analyze' and args.budget_s <= 0:
        parser.error('positive analysis budget required')
    return dict(prepare=prepare_command, plan=plan_command, capture=capture_command,
                analyze=analyze_command)[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
