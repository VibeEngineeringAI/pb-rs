#!/usr/bin/env python3
"""Offline lock inventory and isolated, explicitly admitted future build experiment.

No command is admitted for native execution until fresh independent review and root authorization.
No shell commands, cargo clean, native pb execution, or network inventory.
"""
import argparse
from datetime import datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import tomllib

BASE = '4a812d5b9bc4c40882ab10897a73c3ce1e715ce0'
CACHE = Path.home() / '.cache/pb-phase6/build-cost-preparation-v1'
TMP = Path.home() / '.cache/pb-phase6/tmp'
LOCK = Path.home() / '.cache/pb-rs-performance.lock'
REGISTRY = 'registry+https://github.com/rust-lang/crates.io-index'
REGISTRY_DIR = 'index.crates.io-1949cf8c6b5b557f'
TOOLCHAIN = Path('/home/ajs/.rustup/toolchains/1.96.0-x86_64-unknown-linux-gnu')
BUILD = ['cargo', 'build', '--locked', '--offline', '--release', '--bin', 'pb']
REVIEWED_SHA256 = '75467a917c3b40a08892d0d6c2cbba1c7dce14c2cdc84f1e91779e7ef8f04357'
CANDIDATE_SHA256 = 'a90917f86241c7df045015250ee69cc4d8090264285be46672bd0b865441fb3e'
STOP_SIGNALS = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
CHILD_DEFAULTS = STOP_SIGNALS | {getattr(signal, n) for n in ('SIGPIPE', 'SIGXFSZ') if hasattr(signal, n)}
CONTAINMENT_SCOPE = 'procfs pgid/session/descendant observation'
CLEANUP_S, PROBE_S, REPORT_RESERVE_S = 5, 10, 2
MAX_JSON, CHUNK, MAX_FILES = 16 * 1024 * 1024, 256 * 1024, 100000
MAX_REPORT = 32 * 1024 * 1024
ACTIVE_BUDGET = ContextVar('phase6_build_cost_budget', default=None)
STOP_CONTROL = ContextVar('phase6_build_cost_stop_control', default=None)
DEFER_STOP = ContextVar('phase6_build_cost_defer_stop', default=False)
TRACKED_PROCESSES = ContextVar('phase6_build_cost_tracked_processes', default=())
# Planning estimate, not a throughput guarantee: exceeds R2's observed 610 MB / 2267 files.
TARGET_PLAN_BYTES, TARGET_PLAN_FILES = 2 * 1024**3, 10000
TARGET_RESERVE_S = TARGET_PLAN_BYTES / (16 * 1024**2) + TARGET_PLAN_FILES / 500
VERIFY_PLAN_S = 30  # Initial planning floor; observed verification costs can increase it.


class StopRequested(InterruptedError):
    pass


class StopControl:
    """Only bounded flags in handlers. Kernel pending signals are consumed explicitly while masked."""
    def __init__(self):
        self.bits, self.first, self.failed = 0, None, False
        self.callback = self.record

    def record(self, signum, frame=None):
        try:
            if signum in STOP_SIGNALS:
                if self.first is None:
                    self.first = int(signum)
                self.bits |= 1 << int(signum)
        except BaseException:
            self.failed = True  # Never deliberately raise asynchronously from a handler.

    def poll_masked(self):
        for signum in sorted(signal.sigpending() & STOP_SIGNALS):
            self.record(signum)
        for _ in range(8):  # Nonblocking and bounded even under a continuous signal sender.
            pending = signal.sigtimedwait(STOP_SIGNALS, 0)
            if pending is None:
                return
            if pending.si_signo not in STOP_SIGNALS:
                raise ValueError('unexpected stop wait identity')
            self.record(pending.si_signo)

    def snapshot(self):
        return dict(first_signal=self.first, signals=sorted(s for s in STOP_SIGNALS if self.bits & (1 << s)),
                    control_failed=self.failed)


def stop_flags():
    control = STOP_CONTROL.get()
    if control is not None and (control.bits or control.failed) and not DEFER_STOP.get():
        raise StopRequested('stop requested or stop-control failed')


def check_stop(report=None):
    control = STOP_CONTROL.get()
    if control is not None:
        with blocked_signals():
            pass  # Exit polls/drains pending stops before restoring the mask.
        if report is not None:
            report['stop'] = control.snapshot()
        stop_flags()


class BudgetExpired(TimeoutError):
    pass


class Budget:
    def __init__(self, deadline):
        self.deadline = deadline
        self.limit = deadline - REPORT_RESERVE_S
        self.costs = {}
        self.verify_floor = VERIFY_PLAN_S

    def check(self, reserve=0):
        stop_flags()
        if time.monotonic() + reserve >= self.limit:
            raise BudgetExpired('window budget exhausted; validation incomplete')

    def observe(self, name, function, *args, **kwargs):
        self.check()
        start = time.monotonic()
        completed = False
        try:
            value = function(*args, **kwargs)
            completed = True
            return value
        finally:
            self.costs[name] = max(self.costs.get(name, 0), time.monotonic() - start)
            if completed:
                self.check()  # Never mask a primary validation error with a secondary budget error.

    def launch(self, timeout):
        reserve = launch_reserve(max(self.verify_floor, self.costs.get('verify', 0)),
                                 max(TARGET_RESERVE_S, self.costs.get('target', 0)))
        self.check(timeout + reserve)
        return reserve


def launch_reserve(verify_s, target_s=TARGET_RESERVE_S):
    return target_s + 2 * verify_s + PROBE_S + CLEANUP_S + REPORT_RESERVE_S


def schedule_plan(schedule, timeout, verify_s):
    """Serial planning simulation, using the same launch guard as Budget.launch.

    Estimates are not measurements/throughput guarantees. Cold pre-target trees are empty;
    warm pre-target and every post-build target traversal reserve the target planning cost.
    """
    elapsed = verify_s + PROBE_S  # Pre-run full verification and compiler probe.
    required, stages = 0, []
    for label, mode in schedule:
        elapsed += verify_s + (TARGET_RESERVE_S if mode == 'warm-noop' else 0)
        launch_at = elapsed
        guard = timeout + launch_reserve(verify_s) + REPORT_RESERVE_S  # Budget.limit excludes journal reserve.
        required = max(required, launch_at + guard)
        elapsed += timeout + CLEANUP_S + TARGET_RESERVE_S + PROBE_S + REPORT_RESERVE_S
        stages.append(dict(label=label, mode=mode, launch_at_s=launch_at, guard_remaining_s=guard,
                           capture_end_s=elapsed))
    elapsed += verify_s + REPORT_RESERVE_S  # End verification and final journal.
    return dict(required_s=max(required, elapsed), verify_s=verify_s, target_s=TARGET_RESERVE_S,
                planned_end_s=elapsed, stages=stages, interpretation='serial planning estimate, not a timing guarantee')


def check_budget():
    stop_flags()
    budget = ACTIVE_BUDGET.get()
    if budget is not None:
        budget.check()


@contextmanager
def bounded_work(budget):
    token = ACTIVE_BUDGET.set(budget)
    try:
        yield
    finally:
        ACTIVE_BUDGET.reset(token)


@contextmanager
def durable_capture():
    """Small bounded journals/capture can finish after work budget expires; no full rehash."""
    token = ACTIVE_BUDGET.set(None)
    deferred = DEFER_STOP.set(True)
    try:
        yield
    finally:
        ACTIVE_BUDGET.reset(token)
        DEFER_STOP.reset(deferred)


@contextmanager
def blocked_signals():
    # Capture before changing anything: CPython may raise AFTER applying SIG_BLOCK.
    control = STOP_CONTROL.get()
    try:
        old = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    except BaseException:
        if control is not None:
            control.failed = True
        raise  # Empty query never intentionally changes the mask; no guessed restore value.
    confirmed = STOP_SIGNALS <= old
    try:
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
            confirmed = True
        except BaseException:
            if control is not None:
                control.failed = True
            raise
        yield old
    finally:
        try:
            if control is not None and confirmed:
                control.poll_masked()
        except BaseException:
            control.failed = True
            raise
        finally:
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, old)
            except BaseException:
                if control is not None:
                    control.failed = True
                raise


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('nonfinite JSON number')
    return number


def require_digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('caller SHA-256 is required')


def regular_bytes(path, limit=MAX_JSON):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('not a regular file: ' + str(path))
    if path.stat().st_size > limit:
        raise ValueError('bounded byte read exceeded: ' + str(path))
    with path.open('rb') as source:
        parts = []
        total = 0
        while True:
            check_budget()
            part = source.read(CHUNK)
            if not part:
                break
            total += len(part)
            if total > limit:
                raise ValueError('bounded byte read exceeded')
            parts.append(part)
    return b''.join(parts)


def stream_hash(source):
    digest, size = hashlib.sha256(), 0
    while True:
        check_budget()
        data = source.read(CHUNK)
        if not data:
            break
        digest.update(data)
        size += len(data)
    return digest.hexdigest(), size


class DeadlineReader:
    """Tar's internal compressed-input reads must share the caller's deadline too."""
    def __init__(self, source):
        self.source = source

    def read(self, size):
        check_budget()
        if size < 0 or size > CHUNK:
            raise ValueError('unbounded archive input read')
        data = self.source.read(size)
        check_budget()
        return data


@contextmanager
def open_archive(path):
    with Path(path).open('rb') as source:
        with tarfile.open(fileobj=DeadlineReader(source), mode='r|gz') as tar:
            yield tar


def identity(path):
    resolved = Path(path).resolve(strict=True)
    no_links(resolved)
    before = resolved.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('not a regular file')
    with resolved.open('rb') as source:
        digest, size = stream_hash(source)
    after = resolved.stat()
    stable = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(after, k) != getattr(before, k) for k in stable):
        raise ValueError('file changed during hash: ' + str(resolved))
    return dict(path=str(resolved), sha256=digest, bytes=size)


def read_json(path, expected_sha256=None, limit=MAX_JSON, include_bytes=False):
    """Parse and hash precisely the same bytes at the initially resolved path."""
    resolved = Path(path).resolve(strict=True)
    data = regular_bytes(resolved, limit=limit)
    digest = sha(data)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError('caller-bound manifest digest mismatch')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key: ' + key)
            value[key] = item
        return value
    value = json.loads(data, object_pairs_hook=unique, parse_float=strict_float,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    if not isinstance(value, dict):
        raise ValueError('manifest must be an object')
    result = value, dict(path=str(resolved), sha256=digest, bytes=len(data))
    return (*result, data) if include_bytes else result


def tree(path):
    """Exact file set/content identity, including modes; reject links and special files."""
    root = Path(path)
    no_links(root)
    result = {}
    pending = [root]
    visited = 0
    while pending:
        check_budget()
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                check_budget()
                visited += 1
                if visited > MAX_FILES:
                    raise ValueError('tree entry bound exceeded')
                p = Path(entry.path)
                if entry.is_symlink():
                    raise ValueError('symlink in tree: ' + str(p))
                if entry.is_dir(follow_symlinks=False):
                    pending.append(p)
                else:
                    result[p.relative_to(root).as_posix()] = tree_file(p)
    return result


def no_links(path):
    p = Path(path).absolute()
    if '..' in p.parts:
        raise ValueError('noncanonical parent traversal path')
    for part in (p, *p.parents):
        if part.is_symlink():
            raise ValueError('symlink path: ' + str(part))
    return p


def new_dir(path, root=None):
    root = CACHE if root is None else root
    root, path = no_links(root), no_links(path)
    if not path.is_relative_to(root) or path == root:
        raise ValueError('output must be a new child of task cache')
    path.mkdir(parents=True, exist_ok=False)
    return path


def atomic_json(path, value):
    """Private task directory only; replace report atomically, refuse linked outputs."""
    path = no_links(path)
    pending = None
    with blocked_signals():
        try:
            fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
            pending = Path(name)
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                class BoundedWriter:
                    size = 0
                    def write(self, part):
                        check_budget()
                        self.size += len(part.encode('utf-8'))
                        if self.size > MAX_REPORT:
                            raise ValueError('checkpoint byte bound exceeded')
                        return out.write(part)
                json.dump(value, BoundedWriter(), indent=2, sort_keys=True, allow_nan=False, ensure_ascii=False)
                out.write('\n')
                out.flush()
                os.fsync(out.fileno())
            os.replace(pending, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)


def packages(lock_bytes):
    rows = tomllib.loads(lock_bytes.decode())['package']
    result = {}
    for p in rows:
        key = (p['name'], p['version'], p.get('source'))
        if key in result:
            raise ValueError('duplicate lock identity')
        result[key] = p
    return result


def source_size(path, archive_files):
    entries = tree(path)
    files = archive_files or {}
    missing = sorted(set(files) - set(entries))
    changed = sorted(k for k in files if k in entries and entries[k]['sha256'] != files[k])
    metadata = {'.cargo-checksum.json', '.cargo-ok'}
    extra = sorted(set(entries) - set(files) - metadata)
    return dict(path=str(path.resolve()), tree_sha256=sha(json.dumps(entries, sort_keys=True).encode()),
                regular_file_bytes=sum(p['bytes'] for p in entries.values()),
                package_file_bytes=sum(entries[k]['bytes'] for k in files if k in entries),
                metadata_bytes=sum(entries[k]['bytes'] for k in metadata if k in entries),
                files=len(entries), checksum_verified=archive_files is not None
                and not (missing or changed or extra), missing=missing, changed=changed, extra=extra)


def archive_members(path, package):
    files, payload, errors = {}, 0, []
    try:
        with open_archive(path) as tar:
            for member in tar:
                check_budget()
                parts = Path(member.name).parts
                if (Path(member.name).is_absolute() or '..' in parts or not parts
                        or parts[0] != package or not (member.isfile() or member.isdir())):
                    errors.append('invalid/nonregular archive member: ' + member.name)
                    break
                if member.isfile():
                    name = Path(*parts[1:]).as_posix()
                    if name == '.' or name in files or len(files) >= MAX_FILES:
                        errors.append('duplicate/invalid archive file')
                        break
                    digest, size = stream_hash(tar.extractfile(member))
                    if size != member.size:
                        errors.append('incomplete archive member')
                    files[name] = digest
                    payload += size
    except BudgetExpired:
        raise
    except Exception as error:
        errors.append('archive parse failed: ' + str(error))
    return files if not errors else None, payload, errors


def inventory(base_lock, candidate_lock, cargo_home):
    graphs = {'base': packages(base_lock), 'candidate': packages(candidate_lock)}
    keys = set(graphs['base']) | set(graphs['candidate'])
    rows = []
    for key in sorted(keys, key=lambda k: (k[0], k[1], k[2] or '')):
        name, version, source = key
        if not source or not source.startswith('registry+'):
            continue
        declarations = [g[key].get('checksum') for g in graphs.values() if key in g]
        if len(set(declarations)) != 1 or not declarations[0]:
            raise ValueError('conflicting/missing lock checksum: ' + repr(key))
        checksum = declarations[0]
        row = dict(name=name, version=version, source=source, checksum=checksum,
                   graphs=[label for label, graph in graphs.items() if key in graph],
                   archive=None, cached_source=None, errors=[])
        if source != REGISTRY:
            row['errors'].append('unmapped registry; no network lookup')
            rows.append(row)
            continue
        archive = cargo_home / 'registry/cache' / REGISTRY_DIR / f'{name}-{version}.crate'
        src = cargo_home / 'registry/src' / REGISTRY_DIR / f'{name}-{version}'
        archive_files = None
        if archive.exists():
            before = identity(archive)
            row['archive'] = dict(**before, checksum_verified=before['sha256'] == checksum,
                                  members_valid=False, unpacked_regular_file_bytes=None,
                                  regular_files=None)
            if before['sha256'] != checksum:
                row['errors'].append('archive checksum mismatch')
            else:
                archive_files, payload, member_errors = archive_members(archive, f'{name}-{version}')
                row['errors'].extend(member_errors)
                if identity(archive) != before:
                    raise ValueError('archive changed during inventory')
                row['archive'].update(members_valid=archive_files is not None,
                    unpacked_regular_file_bytes=payload if archive_files is not None else None,
                    regular_files=len(archive_files) if archive_files is not None else None)
        else:
            row['errors'].append('archive missing')
        if src.exists():
            try:
                row['cached_source'] = source_size(src, archive_files)
                if row['cached_source'] != source_size(src, archive_files):
                    raise ValueError('cached source changed during inventory')
                if not row['cached_source']['checksum_verified']:
                    row['errors'].append('cached source checksum mismatch')
            except (ValueError, OSError, KeyError) as error:
                row['errors'].append('source unreadable: ' + str(error))
        else:
            row['errors'].append('cached source missing')
        rows.append(row)
    totals = {}
    for label in ('base', 'candidate', 'added'):
        selected = [r for r in rows if (label in r['graphs'] if label != 'added'
                                       else r['graphs'] == ['candidate'])]
        verified = [r for r in selected if r['archive'] and r['archive']['checksum_verified']
                    and r['archive']['members_valid']]
        totals[label] = dict(registry_packages=len(selected),
            checksum_verified_archives=sum(bool(r['archive'] and r['archive']['checksum_verified'])
                                           for r in selected),
            verified_archives=len(verified),
            missing_or_invalid_archives=len(selected) - len(verified),
            compressed_package_bytes_known=sum(r['archive']['bytes'] for r in verified),
            unpacked_package_bytes_known=sum(r['archive']['unpacked_regular_file_bytes']
                                             for r in verified),
            verified_cached_sources=sum(bool(r['cached_source'] and
                                              r['cached_source']['checksum_verified']) for r in selected),
            cached_source_file_bytes_known=sum(r['cached_source']['package_file_bytes']
                for r in selected if r['cached_source'] and r['cached_source']['checksum_verified']))
    return dict(schema_version=2, scope='locked all-platform registry identities, not compiled target graph',
        base=BASE, lock_sha256={'base': sha(base_lock), 'candidate': sha(candidate_lock)},
        locked_packages={k: len(v) for k, v in graphs.items()}, totals=totals, packages=rows,
        units='logical regular-file bytes; not allocated disk blocks, HTTP transfer, or runtime binary bytes',
        deduplication='one exact name/version/source/checksum per graph; only the mapped crates.io cache',
        excluded='workspace packages, unrelated cache entries, downloadable host tools, runtime binaries',
        build_cost=None, network_used=False)


def index_key(name):
    if len(name) == 1:
        return '1/' + name
    if len(name) == 2:
        return '2/' + name
    if len(name) == 3:
        return '3/' + name[0] + '/' + name
    return name[:2] + '/' + name[2:4] + '/' + name



def tree_file(path):
    p = no_links(path)
    info = identity(p)
    return dict(sha256=info['sha256'], bytes=info['bytes'], mode=stat.S_IMODE(p.stat().st_mode))


def tree_digest(files):
    return sha(json.dumps(files, sort_keys=True, separators=(',', ':')).encode())


def tree_receipt(files):
    return dict(tree_sha256=tree_digest(files), files=len(files),
                regular_file_bytes=sum(v['bytes'] for v in files.values()))


def bound_pair(pair, expected):
    values = {}
    for role in ('declared', 'selected'):
        info = pair[role]
        if info['sha256'] != expected:
            raise ValueError('unreviewed manifest role: ' + role)
        path = info['path']
        if path not in values:
            value, observed = read_json(path, expected)
            if observed != info:
                raise ValueError('manifest role identity changed')
            values[path] = value
        elif identity(path) != info:
            raise ValueError('conflicting manifest path identity')
    return values[pair['selected']['path']]


def anchors(prepared):
    reviewed = bound_pair(prepared['reviewed_manifest'], REVIEWED_SHA256)
    candidate = bound_pair(prepared['candidate_manifest'], CANDIDATE_SHA256)
    if prepared['candidate_manifest']['declared'] != reviewed['candidate_manifest']:
        raise ValueError('candidate declaration is not the reviewed role')
    profile = candidate['builds']['pb']
    if (candidate['base'] != BASE or reviewed['base'] != BASE or profile['argv'] != BUILD
            or profile['features'] != [] or profile['build_kind'] != 'normal_release'
            or profile['cloud_synthetic_token_enabled'] is not False):
        raise ValueError('not the frozen normal release profile')
    if (prepared['version'] != profile['public_build_version'] or prepared['commit'] != ''
            or prepared['build_argv'] != BUILD or prepared['build_kind'] != 'normal_release'
            or prepared['features'] != [] or prepared['cloud_synthetic_token_enabled'] is not False):
        raise ValueError('version/environment/profile not authoritative')
    if prepared['base'] != {'commit': BASE, 'tree_sha256': tree_digest(reviewed['sources']['base'])}:
        raise ValueError('base tree/commit identity not reviewed')
    if prepared['sources'] != reviewed['sources'] or prepared['cargo_home'] != reviewed['cargo_home']:
        raise ValueError('source/cache receipts do not match reviewed manifest')
    if {k: v['sha256'] for k, v in prepared['sources']['candidate'].items()} != candidate['sources']:
        raise ValueError('candidate source set does not match build manifest')
    if prepared['recorded_rustc'] != candidate['rustc']:
        raise ValueError('compiler version declaration not reviewed')
    if set(prepared['tools']) != set(reviewed['tools']):
        raise ValueError('incorrect executable roles')
    toolchain = no_links(prepared['toolchain_path'])
    if Path(reviewed['toolchain']['path']).resolve() != TOOLCHAIN.resolve():
        raise ValueError('reviewed platform toolchain policy mismatch')
    for name, expected in reviewed['tools'].items():
        selected = prepared['tools'][name]
        if any(selected[k] != expected[k] for k in ('sha256', 'bytes')):
            raise ValueError('unreviewed executable role: ' + name)
        if name in ('cargo', 'rustc', 'rustdoc') and Path(selected['path']) != toolchain / 'bin' / name:
            raise ValueError('compiler role outside selected toolchain')
    return reviewed, candidate


def reject_cargo_configs(source):
    for parent in (Path(source), *Path(source).parents):
        check_budget()
        for name in ('config', 'config.toml'):
            if (parent / '.cargo' / name).exists():
                raise ValueError('unbound ancestor Cargo config: ' + str(parent))


BOOKKEEPING = {'.package-cache', '.package-cache-mutate', '.global-cache',
               '.global-cache-journal', '.global-cache-wal', '.global-cache-shm'}


def verify_private_cache(prepared):
    home = no_links(Path(prepared['root']) / 'cargo-home')
    actual = tree(home)
    expected = prepared['cargo_home']
    for name, info in expected.items():
        check_budget()
        if actual.get(name) != info:
            raise ValueError('private dependency cache drift: ' + name)
    prefix = 'registry/src/' + REGISTRY_DIR + '/'
    if 'extracted_sources' in prepared:
        src_prefix = 'registry/src/'
        src_files = {n[len(src_prefix):]: v for n,v in actual.items() if n.startswith(src_prefix)}
        if tree_receipt(src_files) != prepared['extracted_sources']:
            raise ValueError('prepared extracted source tree drift')
    extra = set(actual) - set(expected)
    if any(n not in BOOKKEEPING and not n.startswith(prefix) for n in extra):
        raise ValueError('unbound archive/index/config/credential/private cache file')
    src_root = home / prefix
    if not src_root.exists():
        return
    archives = {Path(n).name.removesuffix('.crate'): n for n in expected
                if n.startswith('registry/cache/' + REGISTRY_DIR + '/')}
    with os.scandir(src_root) as dirs:
        for entry in dirs:
            check_budget()
            package = entry.name
            if package not in archives or entry.is_symlink() or not entry.is_dir():
                raise ValueError('unbound private dependency source')
            source = Path(entry.path)
            if not (source / '.cargo-ok').is_file():
                raise ValueError('partial private dependency source without .cargo-ok')
            files, _, errors = archive_members(home / archives[package], package)
            if errors:
                raise ValueError('invalid private archive members')
            src_prefix = prefix + package + '/'
            observed = {n[len(src_prefix):]: v for n, v in actual.items() if n.startswith(src_prefix)}
            metadata = {'.cargo-ok', '.cargo-checksum.json'}
            if set(observed) - metadata != set(files) or any(
                    observed[n]['sha256'] != digest for n, digest in files.items()):
                raise ValueError('unpacked private dependency source drift')
            if regular_bytes(source / '.cargo-ok') != b'{"v":1}':
                raise ValueError('unbound Cargo completion metadata')
            if '.cargo-checksum.json' in observed:
                receipt, _ = read_json(source / '.cargo-checksum.json')
                if receipt != {'package': expected[archives[package]]['sha256'], 'files': files}:
                    raise ValueError('unbound Cargo checksum metadata')


def verify(prepared, prepared_id):
    check_budget()
    root = no_links(prepared['root'])
    if Path(prepared_id['path']) != root / 'prepared.json':
        raise ValueError('prepared manifest must be canonical within its root')
    if identity(prepared_id['path']) != prepared_id:
        raise ValueError('prepared manifest drift')
    reviewed, candidate = anchors(prepared)
    if identity(__file__) != prepared['runner']:
        raise ValueError('runner drift')
    home = no_links(root / 'home')
    with os.scandir(home) as entries:
        if next(entries, None) is not None:
            raise ValueError('private HOME must remain empty')
    if 'extracted_sources' not in prepared:
        raise ValueError('prepared extracted source tree binding missing')
    for label in ('base', 'candidate'):
        reject_cargo_configs(root / label)
        observed = tree(root / label)
        if observed != prepared['sources'][label]:
            raise ValueError(label + ' full source/lock drift')
        if label == 'candidate' and {k: v['sha256'] for k, v in observed.items()} != candidate['sources']:
            raise ValueError('candidate snapshot differs from authoritative build manifest')
    for path in {prepared['toolchain_path'], reviewed['toolchain']['path']}:
        if tree(path) != reviewed['toolchain']['files']:
            raise ValueError('full Rust toolchain drift')
    for role, declared in reviewed['tools'].items():
        for info in (declared, prepared['tools'][role]):
            check_budget()
            if identity(info['path']) != info:
                raise ValueError('selected/declared executable drift: ' + role)
    verify_private_cache(prepared)
    return True


def prepare(reviewed_path, output, budget):
    """Future root-authorized hash work only; derives fresh receipts from reviewed bytes."""
    with orderly_signals(), quiet_lock(), bounded_work(budget):
        check_stop()
        return _prepare_locked(reviewed_path, output, budget)


def _prepare_locked(reviewed_path, output, budget):
    reviewed, reviewed_id = read_json(reviewed_path, REVIEWED_SHA256)
    candidate, candidate_id = read_json(reviewed['candidate_manifest']['path'], CANDIDATE_SHA256)
    root = new_dir(output)
    (root / 'cargo-home').mkdir()
    prepared = dict(schema_version=3, state='draft', root=str(root),
        reviewed_manifest={'declared': reviewed_id, 'selected': reviewed_id},
        candidate_manifest={'declared': candidate_id, 'selected': candidate_id},
        base={'commit': BASE, 'tree_sha256': tree_digest(reviewed['sources']['base'])},
        sources=reviewed['sources'], cargo_home=reviewed['cargo_home'], tools=reviewed['tools'],
        toolchain_path=reviewed['toolchain']['path'], version=candidate['builds']['pb']['public_build_version'],
        commit='', recorded_rustc=candidate['rustc'], build_kind='normal_release', features=[],
        cloud_synthetic_token_enabled=False, runner=identity(__file__), build_argv=BUILD)
    try:
        with bounded_work(budget):
            anchors(prepared)
            check_stop()
            for label in ('base', 'candidate'):
                for name, info in reviewed['sources'][label].items():
                    copy_bound(Path(reviewed['root']) / label / name, root / label / name, info)
                    check_stop()
            for name, info in reviewed['cargo_home'].items():
                copy_bound(Path(reviewed['root']) / 'cargo-home' / name, root / 'cargo-home' / name, info)
                check_stop()
            (root / 'home').mkdir()
            warm_source_cache(prepared)  # PREPARATION only; never write source trees inside a run window.
            check_stop()
            src = root / 'cargo-home/registry/src'
            prepared['extracted_sources'] = tree_receipt(tree(src) if src.exists() else {})
            (root / 'runs').mkdir()
            atomic_json(root / 'prepared.json', prepared)
            check_stop()
            _, prepared_id = read_json(root / 'prepared.json')
            budget.observe('verify', verify, prepared, prepared_id)
            budget.check()
            prepared['verification_reserve_s'] = max(VERIFY_PLAN_S, budget.costs['verify'])
            prepared['state'] = 'ready'
            atomic_json(root / 'prepared.json', prepared)
            check_stop()
        return prepared
    except BaseException as error:
        with durable_capture():
            control = STOP_CONTROL.get()
            atomic_json(root / 'preparation-failed.json', {'complete': False, 'error': error_text(error),
                        'stop': control.snapshot() if control is not None else None})
        raise


def copy_bound(source, destination, expected):
    no_links(source); no_links(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open('rb') as reader, destination.open('xb') as writer:
        while True:
            check_budget()
            part = reader.read(CHUNK)
            if not part:
                break
            writer.write(part)
            digest.update(part)
            size += len(part)
    if digest.hexdigest() != expected['sha256'] or size != expected['bytes']:
        raise ValueError('copy input changed')
    destination.chmod(expected['mode'])


def remove_private_temp(path):
    # Only our exclusive extraction temporary sibling. Never follow a link or remove a shared tree.
    stack = [(path, False)]
    while stack:
        check_budget()
        item, visited = stack.pop()
        if item.is_symlink() or not item.is_dir():
            item.unlink()
        elif visited:
            item.rmdir()
        else:
            stack.append((item, True))
            with os.scandir(item) as entries:
                for entry in entries:
                    check_budget()
                    stack.append((Path(entry.path), False))


def warm_source_cache(prepared):
    home = Path(prepared['root']) / 'cargo-home'
    src_root = no_links(home / 'registry/src' / REGISTRY_DIR)
    src_root.mkdir(parents=True, exist_ok=True)
    for name, expected in sorted(prepared['cargo_home'].items()):
        check_budget()
        if not name.startswith('registry/cache/'):
            continue
        archive = home / name
        package = archive.name.removesuffix('.crate')
        destination = src_root / package
        if destination.exists():
            if not (destination / '.cargo-ok').is_file():
                raise ValueError('partial extraction at final destination; explicit repair required')
            continue  # Caller verified existing trees against archives before warming.
        temp = Path(tempfile.mkdtemp(prefix='.extract-', dir=src_root))
        try:
            if tree_file(archive) != expected:
                raise ValueError('archive drift before extraction')
            count = 0
            file_hashes = {}
            with open_archive(archive) as tar:
                for member in tar:
                    check_budget()
                    count += 1
                    parts = Path(member.name).parts
                    if (Path(member.name).is_absolute() or '..' in parts or not parts
                            or parts[0] != package or not (member.isfile() or member.isdir())
                            or count > MAX_FILES):
                        raise ValueError('unsafe archive member during source warmup')
                    target = temp / Path(*parts[1:])
                    if parts[1:] in (('.cargo-ok',), ('.cargo-checksum.json',)):
                        raise ValueError('unsafe archive Cargo metadata member')
                    no_links(target)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with target.open('xb') as writer:
                            reader = tar.extractfile(member)
                            digest = hashlib.sha256()
                            while True:
                                check_budget()
                                part = reader.read(CHUNK)
                                if not part:
                                    break
                                writer.write(part)
                                digest.update(part)
                        file_hashes[Path(*parts[1:]).as_posix()] = digest.hexdigest()
                        target.chmod(member.mode & 0o777)
            if tree_file(archive) != expected:
                raise ValueError('archive drift during extraction')
            with (temp / '.cargo-checksum.json').open('xb') as metadata:
                metadata.write(json.dumps({'package': expected['sha256'], 'files': file_hashes},
                                          sort_keys=True).encode())
            (temp / '.cargo-ok').write_bytes(b'{"v":1}')
            check_budget()
            if destination.exists():
                raise ValueError('extraction destination appeared concurrently')
            temp.rename(destination)
        except BaseException as primary:
            try:
                remove_private_temp(temp)
            except Exception as cleanup:
                raise ValueError('extraction failed; private partial retained at ' + str(temp)
                                 + '; primary=' + error_text(primary) + '; cleanup=' + error_text(cleanup)) from primary
            raise


def utc_seconds(value):
    if not isinstance(value, str):
        raise ValueError('coordinator timestamp must be a UTC string')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError('coordinator timestamps must explicitly use UTC')
    return parsed.timestamp()


RECEIPT_KEYS = {'schema_version', 'issuer', 'window_id', 'run_id', 'output', 'prepared_sha256',
                'jobs', 'timeout_s', 'warm_noop', 'start_utc', 'deadline_utc', 'ack_sha256'}


def admission(prepared, prepared_id, output, receipt, ack, jobs, timeout, warm_noop):
    if prepared.get('schema_version') != 3 or prepared.get('state') != 'ready':
        raise ValueError('requires reviewed ready schema v3; historical/draft preparation is not admitted')
    root, output = no_links(prepared['root']), no_links(output)
    if (root / 'preparation-failed.json').exists():
        raise ValueError('preparation failure marker forbids admission')
    if Path(prepared_id['path']) != root / 'prepared.json':
        raise ValueError('canonical prepared.json must be within its root')
    if not root.is_relative_to(CACHE) or root == CACHE or output.parent != root / 'runs':
        raise ValueError('run must own a new child of prepared root/runs')
    if output.exists():
        raise ValueError('run output already exists')
    if type(jobs) is not int or not 1 <= jobs <= 64:
        raise ValueError('explicit jobs must be an integer 1..64')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError('finite positive bounded sample timeout required')
    if (set(receipt) != RECEIPT_KEYS or receipt.get('schema_version') != 2 or receipt.get('issuer') != 'coordinator'
            or receipt.get('run_id') != output.name or receipt.get('output') != str(output)
            or not output.name or output.name.startswith('.')
            or not isinstance(receipt.get('window_id'), str) or not receipt['window_id'].strip()
            or len(receipt['window_id']) > 128 or type(warm_noop) is not bool
            or type(receipt.get('jobs')) is not int or type(receipt.get('timeout_s')) not in (int, float)
            or receipt.get('prepared_sha256') != prepared_id['sha256']
            or receipt.get('jobs') != jobs or receipt.get('timeout_s') != timeout
            or receipt.get('warm_noop') is not warm_noop):
        raise ValueError('coordinator receipt does not bind this schedule/preparation')
    if not isinstance(ack, bytes) or not ack.strip() or receipt.get('ack_sha256') != sha(ack):
        raise ValueError('external coordinator acknowledgement does not match receipt')
    start, deadline = utc_seconds(receipt['start_utc']), utc_seconds(receipt['deadline_utc'])
    now = time.time()
    if not 0 < deadline - start <= 14400 or not start <= now < deadline:
        raise ValueError('coordinator window is unavailable/expired/too long')
    verify_reserve = prepared.get('verification_reserve_s', VERIFY_PLAN_S)
    if type(verify_reserve) not in (int, float) or not math.isfinite(verify_reserve) or verify_reserve < 0:
        raise ValueError('invalid bound preparation verification reserve')
    schedule = [(label, mode) for label in ('base', 'candidate')
                for mode in (('cold-target', 'warm-noop') if warm_noop else ('cold-target',))]
    plan = schedule_plan(schedule, timeout, max(VERIFY_PLAN_S, verify_reserve))
    if deadline - now <= plan['required_s']:
        raise ValueError('full timeouts and cleanup cannot fit remaining window')
    return schedule, deadline


def exclusive_bytes(path, data):
    """Exclusive small evidence/consumption write, with file and directory durability."""
    path = no_links(path)
    with path.open('xb') as writer:
        writer.write(data)
        writer.flush()
        os.fsync(writer.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def consume_receipt(prepared, receipt_id, output, receipt):
    runs = no_links(Path(prepared['root']) / 'runs')
    # One fixed namespace shared by every output; never reuse a failed/aborted receipt.
    marker = runs / ('.receipt-' + receipt_id['sha256'] + '.consumed.json')
    semantic = sha(json.dumps([receipt['window_id'], receipt['run_id']], ensure_ascii=True,
                              separators=(',', ':')).encode())
    semantic_marker = runs / ('.window-run-' + semantic + '.consumed.json')
    data = json.dumps({'receipt': receipt_id, 'output': str(output)}, sort_keys=True).encode()
    # Write semantic identity first. Either durable partial marker permanently prevents retry.
    exclusive_bytes(semantic_marker, data)
    exclusive_bytes(marker, data)
    return str(marker)


@contextmanager
def quiet_lock():
    no_links(LOCK)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def orderly_signals():
    inherited = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    if inherited & STOP_SIGNALS:
        raise ValueError('inherited stop signals are blocked; admission refused')
    control = StopControl()
    old = {}
    token = STOP_CONTROL.set(control)
    try:
        with blocked_signals():
            for signum in STOP_SIGNALS:
                old[signum] = signal.signal(signum, control.callback)
        validate_handlers(control)
        yield control
    finally:
        try:
            # Only reached after child absence proof. Drain/record under our handlers, then restore.
            with blocked_signals():
                control.poll_masked()
                for signum, handler in old.items():
                    signal.signal(signum, handler)
        finally:
            STOP_CONTROL.reset(token)


def validate_handlers(control):
    if control is None or any(signal.getsignal(s) is not control.callback for s in STOP_SIGNALS):
        raise ValueError('runner stop handlers are not installed')


def supervisor_preconditions(check_mask=False):
    if threading.current_thread() is not threading.main_thread() or threading.active_count() != 1:
        raise ValueError('owned supervisor requires a single main thread')
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise ValueError('SIGCHLD handler could reap the pinned leader')
    if not hasattr(os, 'POSIX_SPAWN_CLOSEFROM'):
        raise ValueError('POSIX_SPAWN_CLOSEFROM is required; native glibc validation pending')
    if check_mask:
        if signal.pthread_sigmask(signal.SIG_BLOCK, set()) & STOP_SIGNALS:
            raise ValueError('inherited stop signals are blocked; admission refused')
        validate_handlers(STOP_CONTROL.get())


def command_env(prepared, output, label, jobs):
    root, tools = Path(prepared['root']), prepared['tools']
    env = {'PATH': str(Path(tools['cargo']['path']).parent) + ':/usr/bin:/bin',
           'HOME': str(root / 'home'), 'CARGO_HOME': str(root / 'cargo-home'),
           'TMPDIR': str(TMP / ('build-cost-' + sha(str(output).encode())[:24])),
           'LANG': 'C', 'LC_ALL': 'C', 'TZ': 'UTC',
           'RUSTC': tools['rustc']['path'], 'RUSTDOC': tools['rustdoc']['path'],
           'CC': tools['cc']['path'], 'AR': tools['ar']['path'],
           'CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER': tools['cc']['path'],
           'CARGO_NET_OFFLINE': 'true', 'CARGO_TARGET_DIR': str(output / (label + '-target')),
           'PB_VERSION': prepared['version'], 'PB_COMMIT': prepared['commit']}
    return [tools['cargo']['path'], *BUILD[1:], '--jobs', str(jobs)], env


def compiler_version(prepared, env):
    check_budget()
    budget = ACTIVE_BUDGET.get()
    if budget:
        budget.check(PROBE_S)
    result = subprocess.run([prepared['tools']['rustc']['path'], '--version', '--verbose'],
                            env=env, check=True, timeout=PROBE_S, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    check_budget()
    observed = result.stdout.decode('utf-8')
    if result.stderr or observed != prepared['recorded_rustc']:
        raise ValueError('compiler version does not match frozen R6 output')
    return observed


def inspect_artifact(prepared, env, binary, sample):
    check_budget()
    budget = ACTIVE_BUDGET.get()
    if budget:
        budget.check(PROBE_S)
    argv = [prepared['tools']['readelf']['path'], '-h', '-l', '-S', '-n', binary['path']]
    result = subprocess.run(argv, env=env, check=True, timeout=PROBE_S,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    check_budget()
    if result.stderr or len(result.stdout) > 1024 * 1024:
        raise ValueError('ELF inspection stderr/size invalid')
    text = result.stdout.decode('utf-8')
    fields = {}
    in_header = False
    in_programs = False
    interp_segment = False
    interp_path = False
    for line in text.splitlines():
        check_budget()
        if line.strip() == 'ELF Header:':
            in_header = True
            continue
        if line.strip() == 'Program Headers:':
            in_header, in_programs = False, True
            continue
        if line.strip().startswith(('Section Headers:', 'Section to Segment mapping:', 'Displaying notes')):
            in_header = in_programs = False
        if in_header:
            for name in ('Class', 'Data', 'Type', 'Machine'):
                if line.strip().startswith(name + ':'):
                    if name in fields:
                        raise ValueError('ambiguous ELF header')
                    fields[name] = line.split(':', 1)[1].strip()
        if in_programs and line.strip().startswith('INTERP '):
            interp_segment = True
        if in_programs and line.strip() == '[Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]':
            interp_path = True
    kind = fields.get('Type', '').split(' ', 1)[0]
    interpreter = '/lib64/ld-linux-x86-64.so.2'
    if (fields.get('Class') != 'ELF64' or fields.get('Machine') != 'Advanced Micro Devices X86-64'
            or fields.get('Data') != "2's complement, little endian" or kind not in ('DYN', 'EXEC')
            or not interp_segment or not interp_path):
        raise ValueError('not the expected dynamically interpreted Linux x86_64 executable')
    output = sample / 'readelf.txt'
    with output.open('xb') as writer:
        writer.write(result.stdout)
    if identity(binary['path']) != binary:
        raise ValueError('executable changed during ELF inspection')
    return dict(argv=argv, output=identity(output), executable=binary, fields=fields,
                interpreter=interpreter, reason='DYN permits normal PIE; EXEC permits non-PIE; '
                'both require the Linux x86_64 dynamic interpreter', native_cli_executed=False)


def proc_stat(pid, root=Path('/proc')):
    p = root / str(pid) / 'stat'
    data = regular_bytes(p, limit=4096)
    head, _, tail = data.rpartition(b')')
    fields = tail.split()
    if not head.startswith(str(pid).encode('ascii') + b' (') or len(fields) < 20:
        raise ValueError('invalid proc stat')
    return dict(pid=pid, state=fields[0].decode('ascii'), ppid=int(fields[1]), pgid=int(fields[2]), session=int(fields[3]),
                starttime=int(fields[19]))


def group_members(pid, deadline, proc_root=Path('/proc')):
    members, escaped, errors = [], [], []
    processes = {}
    visited = 0
    with os.scandir(proc_root) as entries:
        for entry in entries:
            visited += 1
            if visited > MAX_FILES:
                raise ValueError('proc traversal entry bound exceeded')
            if time.monotonic() >= deadline:
                raise TimeoutError('group observation deadline exhausted')
            if not entry.name.isdigit():
                continue
            try:
                info = proc_stat(int(entry.name), proc_root)
                processes[info['pid']] = info
            except OSError as error:
                if error.errno in (errno.ENOENT, errno.ESRCH):
                    continue
                errors.append(error_text(error))
            except Exception as error:
                errors.append(error_text(error))
            if len(errors) > 512:
                raise ValueError('process group observation bound exceeded')
    tracked = {m['pid'] for m in TRACKED_PROCESSES.get()
               if processes.get(m['pid'], {}).get('starttime') == m['starttime']}
    owned = {pid} | tracked | {p for p, m in processes.items() if m['pgid'] == pid or m['session'] == pid}
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError('group topology observation deadline exhausted')
        descendants = {p for p, m in processes.items() if m.get('ppid') in owned}
        if descendants <= owned:
            break
        owned |= descendants
    for p in owned:
        info = processes.get(p)
        if info is None:
            continue
        if info['pgid'] == pid:
            members.append(info)
        else:
            # Includes observed descendants that called setsid, and previously observed reparented members.
            escaped.append(info)
    if len(members) + len(escaped) > 512:
        raise ValueError('process group observation bound exceeded')
    return dict(members=members, escaped=escaped, errors=errors, complete=not errors)


def leader_owned(pid, initial):
    observed = proc_stat(pid)
    if (observed['starttime'] != initial['starttime'] or observed['pgid'] != pid
            or observed['session'] != pid):
        raise ValueError('leader identity changed; no group signal is permitted')
    return observed


def process_checkpoint(sample, outcome):
    with durable_capture():
        outcome.setdefault('containment_scope', CONTAINMENT_SCOPE)
        atomic_json(sample / 'process.json', outcome)


def note_error(outcome, message):
    # A held runner may live a long time; keep its evidence bounded.
    outcome['errors'].append(message[:2000])
    if len(outcome['errors']) > 128:
        del outcome['errors'][0]
        outcome['errors_dropped'] = outcome.get('errors_dropped', 0) + 1


def safe_note(outcome, prefix, error):
    try:
        note_error(outcome, prefix + error_text(error))
    except BaseException:
        try:
            outcome['bookkeeping_failed'] = True
        except BaseException:
            pass  # Catastrophic allocation failure cannot be made into a persistence guarantee.


def pin_primary(outcome, error, stage):
    """Write-once bounded evidence, independent of the rotating cleanup diagnostics."""
    if outcome.get('primary_error') is None:
        outcome['primary_error'] = dict(stage=stage, type=type(error).__name__,
                                        errno=getattr(error, 'errno', None), text=error_text(error)[:2000])


def kernel_owned(pid):
    status = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if status is not None and status.si_pid != pid:
        raise ValueError('unexpected waitid child identity')
    return status  # None means our child is still running; ECHILD fails closed.


def signal_owned(pid, initial, outcome):
    kernel_owned(pid)  # No numeric PID/PGID alone is authority. Recheck before EVERY group signal.
    if initial is not None:
        try:
            leader_owned(pid, initial)
        except OSError:
            # Read failure is not an identity mismatch. Recheck kernel ownership at this fallback site.
            kernel_owned(pid)
            outcome['kernel_owned_fallback_signal'] = True
    os.killpg(pid, signal.SIGKILL)
    outcome['group_kill_sent'] = True
    if initial is None:
        outcome['kernel_owned_fallback_signal'] = True


def observe_group(outcome, end):
    token = TRACKED_PROCESSES.set(outcome.get('known_members', ()))
    try:
        observed = group_members(outcome['pid'], end)
    finally:
        TRACKED_PROCESSES.reset(token)
    known = {(m['pid'], m['starttime']): m for m in outcome.get('known_members', ())}
    known.update({(m['pid'], m['starttime']): m for m in observed['members'] + observed['escaped']})
    if len(known) > 512:
        raise ValueError('tracked process identity bound exceeded; retain hold')
    outcome['known_members'] = list(known.values())
    outcome['observations'].append(observed)
    if len(outcome['observations']) > 32:
        outcome['observations'].pop(0)
        outcome['observations_dropped'] = outcome.get('observations_dropped', 0) + 1
    return observed


def cleanup_round(outcome, holding=False):
    """Independent bounded cleanup attempt, called only masked and outside the measurement budget.

    Never reap on uncertainty. Session escapes are observed, never individually signalled.
    The caller MUST retain the lock and pin and enter hold_owned if this returns unknown.
    """
    pid, initial = outcome['pid'], outcome.get('leader')
    if pid is None:
        return  # Unknown spawn outcome has no numeric ownership authority; HOLD journals/paces only.
    end = time.monotonic() + CLEANUP_S
    clean_scans = 0
    try:
        kernel_owned(pid)
        if initial is None:
            try:
                initial = proc_stat(pid)
            except BaseException as error:
                safe_note(outcome, 'initial ownership: ', error)
                if isinstance(error, OSError) and not outcome.get('group_kill_sent'):
                    signal_owned(pid, None, outcome)  # Pinned setsid child; fresh kernel ownership required.
                return
            if initial['pgid'] != pid or initial['session'] != pid:
                raise ValueError('initial session/group ownership unavailable; no group signal permitted')
            outcome['leader'] = initial
        # Observe topology before killing/reparenting descendants. Observation failure never skips the kill.
        try:
            observe_group(outcome, end)
        except BaseException as error:
            safe_note(outcome, 'pre-kill observation: ', error)
        # Even an expired measurement window must kill its owned group. A zombie-only kill is safe.
        if not outcome.get('group_kill_sent'):
            signal_owned(pid, initial, outcome)
        while time.monotonic() < end:
            try:
                status = kernel_owned(pid)
                owned = leader_owned(pid, initial)
                observed = observe_group(outcome, end)
                others = [m for m in observed['members'] if m['pid'] != pid]
                if (observed['complete'] and not observed['escaped'] and not others
                        and status is not None and owned['state'] == 'Z'):
                    clean_scans += 1
                    if clean_scans == 2:
                        kernel_owned(pid)
                        leader_owned(pid, initial)
                        waited, raw = os.waitpid(pid, os.WNOHANG)
                        if waited != pid:
                            raise ValueError('leader was not reapable after pinned cleanup')
                        outcome['meter_returncode'] = os.waitstatus_to_exitcode(raw)
                        outcome['group_empty_verified'] = True
                        outcome['cleanup_status'] = 'verified-before-leader-reap'
                        return  # NO subsequent group signal, scan, or ownership claim using this PID.
                else:
                    clean_scans = 0
                    if any(m['state'] != 'Z' for m in others) or owned['state'] != 'Z':
                        signal_owned(pid, initial, outcome)
                    if holding:
                        return  # Held polling is low frequency, not another five-second scan storm.
            except InterruptedError as error:
                clean_scans = 0
                safe_note(outcome, 'deferred cleanup interrupt: ', error)
            remaining = end - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.02, remaining))
        safe_note(outcome, 'cleanup: ', TimeoutError('independent cleanup bound exhausted; leader not reaped'))
    except BaseException as error:
        safe_note(outcome, 'cleanup: ', error)


def cleanup_journal(outcome, sample):
    try:
        control = STOP_CONTROL.get()
        pending = signal.sigpending() & STOP_SIGNALS
        if control is not None:
            control.poll_masked()
            pending |= set(control.snapshot()['signals'])
            outcome['stop'] = control.snapshot()
        if pending and not outcome.get('stop_requested'):
            note_error(outcome, 'deferred stop signals observed during owned cleanup')
        outcome['deferred_signals'] = sorted(set(outcome.get('deferred_signals', [])) | set(pending))
        if pending:
            outcome['stop_requested'] = True
        outcome['containment_scope'] = CONTAINMENT_SCOPE
        process_checkpoint(sample, outcome)
    except BaseException as error:
        safe_note(outcome, 'process checkpoint: ', error)


def hold_owned(outcome, sample):
    """No release control. Return ONLY after fresh cleanup proof and safe reap.

    Caller retains the common flock; leader stays unreaped during every unknown round.
    Tests inject cleanup observations and sleep; they never run a real indefinite hold.
    """
    while outcome.get('group_empty_verified') is not True:
        try:
            outcome['cleanup_status'] = ('spawn-outcome-unknown-held' if outcome.get('pid') is None
                                         else 'unknown-held')
            outcome['held_ever'] = True
            cleanup_journal(outcome, sample)
        except BaseException as error:
            safe_note(outcome, 'held bookkeeping: ', error)
        try:
            time.sleep(1)  # Low-cost containment; elapsed time is never release authority.
        except BaseException as error:
            safe_note(outcome, 'held pause: ', error)
        try:
            if outcome.get('pid') is not None:
                cleanup_round(outcome, holding=True)
        except BaseException as error:
            safe_note(outcome, 'held containment: ', error)
    try:
        cleanup_journal(outcome, sample)
    except BaseException as error:
        safe_note(outcome, 'held final journal: ', error)


def finish_owned(outcome, sample, deadline):
    # Keep repeated INT/TERM/HUP deferred until bounded evidence is written and the group is gone.
    while True:
        try:
            with blocked_signals(), durable_capture():
                if time.monotonic() >= deadline:
                    note_error(outcome, 'measurement window overrun; owned cleanup still required')
                if outcome.get('pid') is not None:
                    cleanup_round(outcome)
                if outcome.get('group_empty_verified') is not True:
                    hold_owned(outcome, sample)
                cleanup_journal(outcome, sample)
            return
        except BaseException as error:
            safe_note(outcome, 'cleanup context: ', error)
            if outcome.get('group_empty_verified') is True:
                return
            # Non-escaping fallback. Failure to mask/journal cannot release unresolved ownership.
            try:
                with blocked_signals(), durable_capture():
                    hold_owned(outcome, sample)
            except BaseException as error:
                safe_note(outcome, 'cleanup fallback: ', error)
            if outcome.get('group_empty_verified') is True:
                return
            try:
                time.sleep(1)  # Mask/control failure also stays contained and paced.
            except BaseException as error:
                safe_note(outcome, 'cleanup fallback pause: ', error)


def initialize_outcome(outcome=None):
    result = {} if outcome is None else outcome
    defaults = dict(pid=None, pgid=None, meter_returncode=None, timed_out=False,
                    group_empty_verified=None, cleanup_status='not_observed', errors=[],
                    observations=[], deferred_signals=[], containment_scope=CONTAINMENT_SCOPE,
                    spawn_outcome='not-started', primary_error=None, spawn_error=None)
    for key,value in defaults.items():
        result.setdefault(key,value)
    return result


def execute(cmd, env, cwd, sample, meter, timeout, deadline, *, outcome=None):
    """Caller-owned outcome; masked from before spawn through proven absence, including fallbacks."""
    outcome = initialize_outcome(outcome)
    supervisor_preconditions()
    if STOP_CONTROL.get() is not None:
        validate_handlers(STOP_CONTROL.get())
    old_cwd = Path.cwd()
    argv = [meter, str(sample / 'meter.json'), '--', *cmd]
    try:
        with blocked_signals() as old_mask:
            try:
                check_stop()
                with (sample / 'stdout.log').open('xb') as stdout, (sample / 'stderr.log').open('xb') as stderr:
                    try:
                        os.chdir(cwd)
                        budget = ACTIVE_BUDGET.get()
                        if budget:
                            budget.launch(timeout)  # File-open/chdir stalls must not invalidate the prior launch guard.
                        check_stop()
                        started = time.monotonic()
                        outcome['spawn_outcome'] = 'unknown'
                        outcome['spawn_started'] = True
                        try:
                            pid = os.posix_spawn(meter, argv, env,
                                file_actions=[(os.POSIX_SPAWN_OPEN, 0, '/dev/null', os.O_RDONLY, 0),
                                          (os.POSIX_SPAWN_DUP2, stdout.fileno(), 1),
                                          (os.POSIX_SPAWN_DUP2, stderr.fileno(), 2),
                                          (os.POSIX_SPAWN_CLOSEFROM, 3)],
                                setsid=True, setsigmask=old_mask - CHILD_DEFAULTS, setsigdef=CHILD_DEFAULTS)
                        except OSError as error:
                            # CALL ONLY: pinned Linux/glibc returns an error after failed-child reap.
                            # Later OSError (journal/proc/chdir) must never clear pending ownership.
                            pin_primary(outcome, error, 'posix_spawn')
                            if outcome.get('spawn_error') is None:
                                outcome['spawn_error'] = dict(outcome['primary_error'])
                            outcome.update(spawn_started=False, spawn_outcome='failed-no-child',
                                           cleanup_status='failed-no-child')
                            raise
                        outcome['pid'] = pid  # First operation after successful spawn; key preallocated in caller.
                        outcome.update(pgid=pid, group_empty_verified=False, cleanup_status='unknown',
                                       spawn_outcome='child-pid-returned')
                        process_checkpoint(sample, outcome)  # PID is durable before any procfs read.
                        initial = proc_stat(pid)
                        if initial['pgid'] != pid or initial['session'] != pid:
                            raise ValueError('spawn did not create the owned session/group')
                        outcome['leader'] = initial
                        process_checkpoint(sample, outcome)
                    finally:
                        os.chdir(old_cwd)
                    sample_deadline = started + timeout
                    while True:
                        check_stop()  # Explicit control flow; no handler raises into this code.
                        leader_owned(pid, initial)
                        status = kernel_owned(pid)
                        if status is not None:
                            break
                        remaining = sample_deadline - time.monotonic()
                        if remaining <= 0:
                            outcome['timed_out'] = True
                            raise subprocess.TimeoutExpired(argv, timeout)
                        time.sleep(min(0.02, remaining))
            except BaseException as primary:
                pin_primary(outcome, primary, 'execute')
                safe_note(outcome, '', primary)
                if outcome.get('spawn_started') and outcome.get('pid') is None:
                    outcome['cleanup_status'] = 'spawn-outcome-unknown-held'
                if outcome.get('spawn_outcome') in ('unknown', 'failed-no-child'):
                    cleanup_journal(outcome, sample)
            finally:
                if outcome.get('pid') is not None:
                    while outcome.get('group_empty_verified') is not True:
                        try:
                            finish_owned(outcome, sample, deadline)
                        except BaseException as error:
                            safe_note(outcome, 'execute cleanup: ', error)
                        if outcome.get('group_empty_verified') is not True:
                            try:
                                with blocked_signals(), durable_capture():
                                    hold_owned(outcome, sample)
                            except BaseException as error:
                                safe_note(outcome, 'execute hold fallback: ', error)
                        # No exit/unmask of the lifetime scope while ownership is unresolved.
    except BaseException as error:
        safe_note(outcome, 'supervision context: ', error)
    return outcome


def reconcile_process(outcome, sample):
    """Recover only ownership evidence from the bounded file in this new, owned sample directory."""
    path = no_links(sample / 'process.json')
    if not path.exists():
        return
    with durable_capture():
        journal, journal_id = read_json(path, limit=MAX_REPORT)
    if Path(journal_id['path']) != path:
        raise ValueError('process journal escaped sample directory')
    pid = journal.get('pid')
    if type(pid) is not int or pid <= 0 or journal.get('pgid') != pid:
        raise ValueError('invalid process journal identity')
    if outcome.get('pid') not in (None,pid):
        raise ValueError('conflicting caller/journal PID; retain caller ownership')
    outcome['process_journal_identity'] = journal_id
    # In-memory cleanup proof cannot be downgraded by an earlier journal after the leader was reaped.
    if outcome.get('group_empty_verified') is True:
        return
    for key in ('pid','pgid','leader','group_empty_verified','cleanup_status','meter_returncode',
                'held_ever','observations','known_members','deferred_signals','group_kill_sent'):
        if key in journal:
            outcome[key] = journal[key]
    outcome['containment_scope'] = CONTAINMENT_SCOPE


def error_text(error):
    return type(error).__name__ + ': ' + str(error)


def metrics_result(path):
    required = ('wall_ms', 'cpu_user_ms', 'cpu_system_ms', 'peak_rss_bytes')
    capture = dict(metrics=dict.fromkeys(required), raw_meter_identity=None, parsed_observations=None,
                   cargo_exit_code=None, termination=None, metrics_complete=False, cargo_success=False, errors=[])
    try:
        resolved = Path(path).resolve(strict=True)
        with durable_capture():
            data = regular_bytes(resolved, limit=256 * 1024)
        capture['raw_meter_identity'] = dict(path=str(resolved), sha256=sha(data), bytes=len(data))
        value = json.loads(data, parse_float=strict_float,
                           parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        if not isinstance(value, dict):
            raise ValueError('meter JSON must be an object')
        capture['parsed_observations'] = value
        capture['termination'] = value.get('termination')
        code = value.get('exit_code')
        if type(code) is int:
            capture['cargo_exit_code'] = code
        started, ended = value.get('started_monotonic_ns'), value.get('ended_monotonic_ns')
        if type(started) is int and type(ended) is int and 0 <= started <= ended:
            capture['metrics']['wall_ms'] = (ended - started) / 1e6
        for key in required[1:]:
            n = value.get(key)
            if type(n) in (int, float) and math.isfinite(n) and n >= 0:
                capture['metrics'][key] = n
        term = value.get('termination')
        valid_term = (type(code) is int and isinstance(term, dict) and
                      ((set(term) == {'exit'} and type(term['exit']) is int and term['exit'] == code >= 0)
                       or (set(term) == {'signal'} and type(term['signal']) is int
                           and term['signal'] > 0 and code == -term['signal'])))
        capture['metrics_complete'] = (all(v is not None for v in capture['metrics'].values())
            and valid_term and value.get('resource_source') == 'native_child_wait4')
        capture['cargo_success'] = (capture['metrics_complete'] and value.get('exit_code') == 0
                                    and value.get('termination') == {'exit': 0})
        if not capture['metrics_complete']:
            capture['errors'].append('native resource/termination evidence incomplete')
    except BaseException as error:
        capture['errors'].append(error_text(error))
    return capture


def native_build_outputs(target):
    result, remaining = {}, 1024 * 1024
    root = target / 'release/build'
    if not root.exists():
        return result
    with os.scandir(root) as entries:
        for entry in entries:
            check_budget()
            path = no_links(Path(entry.path) / 'output')
            if not path.exists():
                continue
            info = identity(path)
            with path.open('rb') as reader:
                check_budget()
                prefix = reader.read(min(65536, remaining))
            remaining -= len(prefix)
            result[str(path)] = dict(**info, prefix=prefix.decode('utf-8', 'replace'),
                                     captured_bytes=len(prefix), truncated=info['bytes'] > len(prefix))
    return result


def checkpoint(output, report):
    with durable_capture():
        summarize_cleanup(report)
        for attempt in range(2):
            try:
                report['last_checkpoint_complete'] = True
                control = STOP_CONTROL.get()
                if control is not None:
                    report['stop'] = control.snapshot()
                atomic_json(output / 'report.json', report)
                break
            except BaseException as error:
                report['last_checkpoint_complete'] = False
                report['passed'] = report['complete'] = False
                report['errors'].append('checkpoint: ' + error_text(error))
                if attempt:
                    raise
    check_stop(report)  # Explicit stop is outside the write-retry loop; never swallowed by retry.


def summarize_cleanup(report):
    samples = report.get('samples', ())
    pending = [s for s in samples if s.get('pid') is not None or s.get('spawn_started')
               or s.get('spawn_outcome') in ('unknown', 'child-pid-returned')]
    no_pid_unknown = any(s.get('pid') is None for s in pending)
    report['held_ever'] = bool(report.get('held_ever') or no_pid_unknown or any(s.get('held_ever') for s in samples))
    report['group_empty_verified'] = (False if no_pid_unknown else
                                      all(s.get('group_empty_verified') is True for s in pending) if pending else None)
    report['cleanup_status'] = ('spawn-outcome-unknown-held' if no_pid_unknown else
        'unknown-held' if pending and report['held_ever'] and not report['group_empty_verified'] else
        'unknown' if pending and not report['group_empty_verified'] else
        'observed' if pending else
        'failed-no-child' if any(s.get('spawn_outcome') == 'failed-no-child' for s in samples) else 'not_observed')


def invalidate_report(output, report):
    """Remove a potentially replaced pass from the consumable pathname without allocating payload."""
    report['passed'] = report['complete'] = False
    with durable_capture(), blocked_signals():
        try:
            source = no_links(output / 'report.json')
            if source.exists():
                os.replace(source, no_links(output / 'report.unverified.json'))
                fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            report['report_invalidated'] = True
        except BaseException as error:
            report['report_invalidated'] = False
            report['errors'].append('report invalidation failed: ' + error_text(error))


def capture_sample(prepared, output, report, record, sample, target, cold_identities):
    # This durable capture must precede every target hash, artifact inspection or full input verification.
    captured = metrics_result(sample / 'meter.json')
    for error in captured.pop('errors'):
        note_error(record, error)
    record.update(captured)
    checkpoint(output, report)
    if record.get('held_ever'):
        note_error(record, 'unknown-held cleanup occurred; artifact/target validation skipped')
        record['passed'] = record['complete'] = False
        return
    budget = ACTIVE_BUDGET.get()
    target_tree = (lambda: budget.observe('target', tree, target)) if budget else lambda: tree(target)
    for name, function in (('target_files', target_tree),
                           ('executable', lambda: identity(target / 'release/pb')),
                           ('native_build_outputs', lambda: native_build_outputs(target))):
        try:
            check_budget()
            value = function()
            if name == 'target_files':
                record['target_regular_file_bytes'] = sum(v['bytes'] for v in value.values())
                record['target_regular_file_bytes_delta'] = (record['target_regular_file_bytes']
                    - record['target_regular_file_bytes_before'])
            else:
                record[name] = value
        except BaseException as error:
            record['errors'].append(name + ': ' + error_text(error))
        checkpoint(output, report)
    if record['metrics_complete'] and not record['cargo_success']:
        record['errors'].append('Cargo termination: ' + str(record['termination']))
    if (record['metrics_complete'] and record['cargo_success'] and not record['errors'] and not record.get('bookkeeping_failed')
            and record.get('meter_returncode') == 0 and record.get('group_empty_verified') is True):
        if record['mode'] == 'warm-noop' and record.get('executable') != cold_identities.get(record['label']):
            record['errors'].append('warm no-op executable differs from cold build')
        else:
            try:
                _, env = command_env(prepared, output, record['label'], report['jobs'])
                record['artifact_validation'] = inspect_artifact(prepared, env, record['executable'], sample)
                if record['mode'] == 'cold-target':
                    cold_identities[record['label']] = record['executable']
            except BaseException as error:
                record['errors'].append('artifact_validation: ' + error_text(error))
    record['capture_complete'] = True
    record['complete'] = (not record.get('bookkeeping_failed') and record.get('meter_returncode') is not None
                          and not record.get('timed_out', False)
                          and record.get('group_empty_verified') is True and record['metrics_complete'])
    record['passed'] = (record.get('meter_returncode') == 0 and record.get('group_empty_verified') is True
                        and record['cargo_success'] and not record['errors'] and not record.get('bookkeeping_failed')
                        and bool(record.get('artifact_validation')))
    checkpoint(output, report)


def _run(prepared_path, prepared_sha256, output, receipt_path, receipt_sha256, ack_path,
        jobs, timeout, warm_noop=False):
    # Three bounded reads before LOCK_NB: prepared, receipt, ACK. No payload traversal.
    require_digest(prepared_sha256)
    require_digest(receipt_sha256)
    prepared, prepared_id = read_json(prepared_path, prepared_sha256, limit=512 * 1024)
    receipt, receipt_id, receipt_bytes = read_json(receipt_path, receipt_sha256, limit=64 * 1024, include_bytes=True)
    ack_resolved = Path(ack_path).resolve(strict=True)
    ack = regular_bytes(ack_resolved, limit=4096)
    ack_id = dict(path=str(ack_resolved), sha256=sha(ack), bytes=len(ack))
    schedule, deadline_utc = admission(prepared, prepared_id, output, receipt, ack, jobs, timeout, warm_noop)
    budget = Budget(time.monotonic() + deadline_utc - time.time())
    budget.verify_floor = max(VERIFY_PLAN_S, prepared.get('verification_reserve_s', VERIFY_PLAN_S))
    report = dict(schema_version=3, prepared=prepared_id, coordinator_receipt=receipt_id,
                  window_id=receipt['window_id'], window_start_utc=receipt['start_utc'],
                  window_deadline_utc=receipt['deadline_utc'], actual_start_utc=datetime.now(timezone.utc).isoformat(),
                  quiet_ack=ack_id, jobs=jobs, timeout_s=timeout, samples=[], errors=[],
                  complete=False, passed=False, ratios=None, lock_acquired=False,
                  end_identity_check={'complete': False, 'passed': False}, release_claim=False,
                  containment_scope=CONTAINMENT_SCOPE,
                  initial_reserve_plan={'target_s': TARGET_RESERVE_S, 'verify_s': budget.verify_floor},
                  admission_plan=schedule_plan(schedule, timeout, budget.verify_floor),
                  interpretation='one observation per condition; fixed base/candidate order confounds warm caches; descriptive only')
    created = False
    scratch = None
    scratch_owned = False
    try:
        with quiet_lock():
            report['lock_acquired'] = True
            supervisor_preconditions(check_mask=True)
            check_stop(report)
            # No heavy read, traversal, extraction, copy or output existed before lock acquisition.
            budget.check()
            # Re-admit under the lock: all fixed-path and freshness checks precede consumption.
            admission(prepared, prepared_id, output, receipt, ack, jobs, timeout, warm_noop)
            report['receipt_consumption'] = consume_receipt(prepared, receipt_id, output, receipt)
            check_stop(report)
            output = new_dir(output)
            created = True
            exclusive_bytes(output / 'coordinator-receipt.json', receipt_bytes)
            checkpoint(output, report)
            with bounded_work(budget):
                try:
                    budget.observe('verify', verify, prepared, prepared_id)
                    check_stop(report)
                    scratch = Path(command_env(prepared, output, 'base', jobs)[1]['TMPDIR'])
                    no_links(scratch).mkdir(exist_ok=False)
                    scratch_owned = True
                    _, env = command_env(prepared, output, 'base', jobs)
                    report['verified_rustc'] = compiler_version(prepared, env)
                    cold_identities = {}
                    for label, mode in schedule:
                        check_stop(report)
                        budget.observe('verify', verify, prepared, prepared_id)
                        if identity(receipt_id['path']) != receipt_id:
                            raise ValueError('coordinator receipt drift')
                        if identity(ack_id['path']) != ack_id:
                            raise ValueError('coordinator ACK file drift')
                        sample = output / (label + '-' + mode)
                        sample.mkdir()
                        target = output / (label + '-target')
                        if mode == 'cold-target':
                            target.mkdir(exist_ok=False)
                        before = budget.observe('target', tree, target)
                        cmd, env = command_env(prepared, output, label, jobs)
                        record = dict(label=label, mode=mode, argv=cmd, env=env, errors=[], complete=False, passed=False,
                            source=str(Path(prepared['root']) / label), target=str(target),
                            target_regular_file_bytes_before=sum(v['bytes'] for v in before.values()))
                        initialize_outcome(record)
                        record['source_build_identity_sha256'] = tree_digest(dict(source=prepared['sources'][label],
                            base=prepared['base'], reviewed_manifest=prepared['reviewed_manifest'],
                            candidate_manifest=prepared['candidate_manifest'], tools=prepared['tools'],
                            toolchain_path=prepared['toolchain_path'], source_path=record['source'], argv=cmd, env=env))
                        report['samples'].append(record)
                        checkpoint(output, report)
                        # Last operation before launch; every pre-sample target/input hash is already complete.
                        record['final_reserve_s'] = budget.launch(timeout)
                        check_stop(report)
                        try:
                            execute(cmd, env, record['source'], sample,
                                prepared['tools']['meter']['path'], timeout, budget.deadline, outcome=record)
                        except BaseException as primary:
                            pin_primary(record, primary, 'run-boundary')
                            safe_note(record, '', primary)
                            try:
                                if (sample / 'process.json').exists():
                                    record['spawn_started'] = True
                                reconcile_process(record, sample)
                            except BaseException as journal:
                                safe_note(record, 'process recovery: ', journal)
                        finally:
                            if ((record.get('pid') is not None or record.get('spawn_started')
                                 or record.get('spawn_outcome') == 'unknown')
                                    and record.get('group_empty_verified') is not True):
                                report['held_ever'] = True
                                record['held_ever'] = True
                                while record.get('group_empty_verified') is not True:
                                    try:
                                        finish_owned(record, sample, budget.deadline)
                                    except BaseException as cleanup:
                                        safe_note(record, 'run cleanup boundary: ', cleanup)
                                        try:
                                            time.sleep(1)
                                        except BaseException as pause:
                                            safe_note(record, 'run containment pause: ', pause)
                            capture_sample(prepared, output, report, record, sample, target, cold_identities)
                        if not record['passed']:
                            raise ValueError('build/resource/artifact/cleanup evidence failed')
                except BaseException as primary:
                    report['errors'].append(error_text(primary))
                finally:
                    # One end validation, inside the lock; expired work does not trigger any further full rehash.
                    try:
                        if report.get('held_ever') or any(s.get('held_ever') for s in report['samples']):
                            raise ValueError('unknown-held cleanup occurred; expensive end verification skipped')
                        if not report.get('last_checkpoint_complete'):
                            raise ValueError('capture checkpoint incomplete; full end validation skipped')
                        budget.observe('verify', verify, prepared, prepared_id)
                        if identity(receipt_id['path']) != receipt_id:
                            raise ValueError('coordinator receipt drift')
                        if identity(ack_id['path']) != ack_id:
                            raise ValueError('coordinator ACK file drift')
                        if time.monotonic() > budget.deadline:
                            raise BudgetExpired('end-of-window deadline exceeded')
                        report['end_identity_check'] = {'complete': True, 'passed': True}
                    except BaseException as error:
                        report['end_identity_check'] = {'complete': False, 'passed': False, 'error': error_text(error)}
                        report['errors'].append('end validation: ' + error_text(error))
                    checkpoint(output, report)
    except BaseException as error:
        report['errors'].append(error_text(error))
    if scratch_owned:
        # Only this newly created empty scratch. Retain nonempty debris; never sweep TMP.
        try:
            no_links(scratch).rmdir()
        except OSError as error:
            report['scratch_retained'] = {'path': str(scratch), 'reason': error_text(error)}
    # A late context/signal/lock error must never coexist with passed=True.
    report['complete'] = (not report['errors'] and not report.get('bookkeeping_failed') and len(report['samples']) == len(schedule)
                          and all(s.get('complete') for s in report['samples'])
                          and report['end_identity_check']['complete'])
    report['passed'] = (report['complete'] and report['end_identity_check']['passed']
                        and all(s.get('passed') for s in report['samples']))
    report['observed_bookkeeping_costs_s'] = budget.costs
    summarize_cleanup(report)
    if created:
        for attempt in range(2):
            try:
                checkpoint(output, report)
                break
            except BaseException as error:
                report['passed'] = report['complete'] = False
                report['errors'].append('final checkpoint: ' + error_text(error))
                report['final_checkpoint_complete'] = False
        if not report.get('last_checkpoint_complete'):
            invalidate_report(output, report)
    return report


def run(prepared_path, prepared_sha256, output, receipt_path, receipt_sha256, ack_path,
        jobs, timeout, warm_noop=False):
    report = None
    control = None
    try:
        with orderly_signals() as control:
            report = _run(prepared_path, prepared_sha256, output, receipt_path, receipt_sha256,
                          ack_path, jobs, timeout, warm_noop)
    except BaseException as error:
        if report is None:
            raise  # Admission/startup refusal: no child was permitted to escape _run.
        safe_note(report, 'stop context: ', error)
        report['passed'] = report['complete'] = False
    if control is not None:
        report['stop'] = control.snapshot()
        if control.bits or control.failed:
            report['passed'] = report['complete'] = False
    # Context-exit faults/stops after proof must also invalidate the disk verdict.
    if not report['passed'] and Path(output).exists():
        try:
            checkpoint(Path(output), report)
        except BaseException as error:
            safe_note(report, 'stop final checkpoint: ', error)
            invalidate_report(Path(output), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare', help='root-approved future bounded hash/copy work')
    prep.add_argument('--reviewed-manifest', type=Path, required=True)
    prep.add_argument('--output', type=Path, required=True)
    prep.add_argument('--max-seconds', type=float, required=True)
    running = sub.add_parser('run', help='future coordinator-authorized window only')
    running.add_argument('--prepared', type=Path, required=True)
    running.add_argument('--prepared-sha256', required=True)
    running.add_argument('--coordinator-receipt', type=Path, required=True)
    running.add_argument('--receipt-sha256', required=True)
    running.add_argument('--ack-file', type=Path, required=True)
    running.add_argument('--output', type=Path, required=True)
    running.add_argument('--jobs', type=int, required=True)
    running.add_argument('--timeout-s', type=float, required=True)
    running.add_argument('--warm-noop', action='store_true')
    args = parser.parse_args()
    if args.action == 'prepare':
        if not math.isfinite(args.max_seconds) or not 0 < args.max_seconds <= 14400:
            raise ValueError('finite bounded preparation budget required')
        prepare(args.reviewed_manifest, args.output, Budget(time.monotonic() + args.max_seconds))
    else:
        try:
            result = run(args.prepared, args.prepared_sha256, args.output,
                         args.coordinator_receipt, args.receipt_sha256, args.ack_file,
                         args.jobs, args.timeout_s, args.warm_noop)
        except Exception as error:
            parser.exit(1, 'build-cost refused: ' + error_text(error)[:512] + '\n')
        if not result['passed']:
            parser.exit(1, 'build-cost incomplete: ' + '; '.join(result['errors'])[:512] + '\n')


if __name__ == '__main__':
    main()
