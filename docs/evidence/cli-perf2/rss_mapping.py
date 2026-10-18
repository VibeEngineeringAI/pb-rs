#!/usr/bin/env python3
"""Attribute frozen CLI RSS at a natural stdout write; diagnostic, not a benchmark.

First run original paths only, after all benchmark sessions have stopped:
  python3 docs/evidence/cli-perf2/rss_mapping.py \
    --binary original=bin/pb-perf-baseline --binary base=bin/pb-perf-final \
    --binary final=bin/pb-perf2-final --out /tmp/pb-rss-original-paths

Only if file-page evidence warrants a control, repeat with a new --out and
--fresh-copies. That adds equally prepared, byte-identical private copies to the
randomized original-path runs. Frozen inputs are never rewritten or advised out
of the cache. --meter defaults to the already-built bin/pb-meter; nothing builds.
--history-crossover (requires --fresh-copies) instead tests write/read histories
on the SAME private-copy inode: rewrite+fsync versus rewrite+fsync+private-file
DONTNEED+full reread. Each selected history is reapplied before its invocation.

A full stdout pipe blocks the CLI's natural final write. We immediately snapshot
its /proc mappings, then drain the pipe and let the original process exit. The
native meter reports that child's resources, avoiding Python's inherited RSS.
Blocking/snapshotting perturbs scheduling: these runs cannot rank CPU or latency.
Snapshots describe this write boundary, not necessarily a command's lifetime peak.
"""
import argparse
import base64
import ctypes
import fcntl
import hashlib
import json
import mmap
import os
from pathlib import Path
import random
import re
import selectors
import signal
import stat
import sys
import time

HEADER = re.compile(r'^([0-9a-f]+)-([0-9a-f]+) ([rwxps-]{4}) ([0-9a-f]+) '
                    r'([0-9a-f]+):([0-9a-f]+) (\d+)(?:\s+(.*))?$')
SUM_FIELDS = ('Rss', 'Pss', 'Anonymous', 'Shared_Clean', 'Shared_Dirty',
              'Private_Clean', 'Private_Dirty', 'AnonHugePages', 'FilePmdMapped',
              'ShmemPmdMapped', 'Swap', 'Referenced', 'LazyFree')


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def identity(path):
    path = path.resolve(strict=True)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
        raise ValueError(f'not an executable regular file: {path}')
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    return {'path': str(path), 'sha256': digest, 'size_bytes': info.st_size,
            'device': info.st_dev, 'inode': info.st_ino, 'mtime_ns': info.st_mtime_ns}


def private_copy(source, target):
    # Explicit read/write, not a reflink or filesystem copy acceleration. Every
    # label uses the same creation, chunk size, fsync, mode and verification.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd, 'wb') as dest, Path(source['path']).open('rb') as origin:
        while chunk := origin.read(1024 * 1024):
            dest.write(chunk)
        dest.flush()
        os.fsync(dest.fileno())
    target.chmod(0o500)
    copied = identity(target)
    if copied['sha256'] != source['sha256']:
        raise ValueError(f'private copy differs: {target}')
    return copied


def residency(fd):
    """Probe only an explicitly private copy, without reading its mapped bytes."""
    length = os.fstat(fd).st_size
    pages = (length + mmap.PAGESIZE - 1) // mmap.PAGESIZE
    vector = (ctypes.c_ubyte * pages)()
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mincore.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_ubyte))
    libc.mincore.restype = ctypes.c_int
    with mmap.mmap(fd, length, flags=mmap.MAP_PRIVATE, prot=mmap.PROT_READ | mmap.PROT_WRITE) as view:
        address = ctypes.c_char.from_buffer(view)
        result = libc.mincore(ctypes.addressof(address), length, vector)
        del address
        if result != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
    bits = bytes(int(bool(value & 1)) for value in vector)
    return {'pages': pages, 'resident_pages': sum(bits),
            'bitmap_sha256': hashlib.sha256(bits).hexdigest(),
            'bitmap_base64_one_byte_per_page': base64.b64encode(bits).decode()}


def storage_info(path):
    best = {}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        mount, storage = line.split(' - ', 1)
        fields = mount.split()
        mountpoint = Path(fields[4])
        if path.resolve().is_relative_to(mountpoint) and len(str(mountpoint)) > len(best.get('mountpoint', '')):
            best = {'mountpoint': str(mountpoint), 'filesystem': storage.split()[0],
                    'mount_options': fields[5], 'device': fields[2]}
    return best


def prepare_history(source, target, condition, private_root):
    path = Path(target['path'])
    if not path.resolve().is_relative_to(private_root.resolve()):
        raise ValueError('history preparation is restricted to private copies')
    before = path.stat()
    if (before.st_dev, before.st_ino) == (source['device'], source['inode']):
        raise ValueError('private copy aliases a frozen input')
    path.chmod(0o700)
    with path.open('r+b') as dest, Path(source['path']).open('rb') as origin:
        dest.truncate(0)
        while chunk := origin.read(1024 * 1024):
            dest.write(chunk)
        dest.flush()
        os.fsync(dest.fileno())
        states = {'post_write_fsync': residency(dest.fileno())}
        if condition == 'read':
            os.posix_fadvise(dest.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
            states['post_private_dontneed'] = residency(dest.fileno())
        dest.seek(0)
        digest = hashlib.file_digest(dest, 'sha256').hexdigest()
        states['post_full_read'] = residency(dest.fileno())
    path.chmod(0o500)
    after = identity(path)
    if digest != source['sha256'] or after['sha256'] != source['sha256']:
        raise ValueError('history preparation changed private-copy bytes')
    if (after['device'], after['inode']) != (before.st_dev, before.st_ino):
        raise ValueError('history crossover did not preserve private-copy inode')
    return after, states


def parse_smaps(text, binary):
    mappings = []
    for line in text.splitlines():
        match = HEADER.fullmatch(line)
        if match:
            start, end, perms, offset, major, minor, inode, path = match.groups()
            current = {'start': start, 'end': end, 'permissions': perms,
                       'offset_bytes': int(offset, 16), 'device': f'{major}:{minor}',
                       'inode': int(inode), 'path': path or '', 'kilobytes': {}}
            # Btrfs subvolumes can expose a stat st_dev different from the
            # mapping's superblock device. The already verified /proc/PID/exe
            # identity plus exact mapping pathname/inode is an equivalent key.
            same_device = (int(major, 16) == os.major(binary['device'])
                           and int(minor, 16) == os.minor(binary['device']))
            is_binary = int(inode) == binary['inode'] and (same_device or path == binary['path'])
            if is_binary:
                current['group'] = 'executable:' + perms
            elif path == '[heap]':
                current['group'] = 'heap'
            elif path and path.startswith('[stack'):
                current['group'] = 'stack'
            elif int(inode) != 0:
                current['group'] = 'other_file'
            elif path and path.startswith(('[vdso', '[vvar', '[vsyscall')):
                current['group'] = 'kernel_special'
            else:
                current['group'] = 'anonymous'
            mappings.append(current)
        elif mappings:
            metric = re.fullmatch(r'([A-Za-z_]+):\s+(\d+) kB', line)
            if metric:
                current['kilobytes'][metric[1]] = int(metric[2])
    if not mappings or not any(m['group'].startswith('executable:') for m in mappings):
        raise ValueError('smaps contains no matching executable mappings')
    totals, groups = {}, {}
    for mapping in mappings:
        group = groups.setdefault(mapping['group'], {key: 0 for key in SUM_FIELDS})
        for key in SUM_FIELDS:
            value = mapping['kilobytes'].get(key, 0)
            group[key] += value
            totals[key] = totals.get(key, 0) + value
    return {'total_kb': totals, 'groups_kb': groups, 'mappings': mappings}


def capture(pid, folder, binary):
    started = time.monotonic_ns()
    captured, unavailable, digests = {}, {}, {}
    for name in ('maps', 'smaps', 'smaps_rollup', 'status', 'stat', 'statm', 'wchan'):
        try:
            data = Path(f'/proc/{pid}/{name}').read_bytes()
            (folder / name).write_bytes(data)
            captured[name] = data.decode('utf-8', errors='replace')
            digests[name] = hashlib.sha256(data).hexdigest()
        except OSError as error:
            unavailable[name] = f'{type(error).__name__}: {error}'
    if 'smaps' not in captured or 'status' not in captured:
        raise ValueError(f'required proc files unavailable: {unavailable}')
    result = parse_smaps(captured['smaps'], binary)
    result.update(capture_started_monotonic_ns=started,
                  capture_ended_monotonic_ns=time.monotonic_ns(), unavailable=unavailable,
                  pid=pid, executable=os.readlink(f'/proc/{pid}/exe'), raw_sha256=digests)
    result['status_kb'] = {name: int(value) for name, value in
                           re.findall(r'^(Vm\w+|Rss\w+):\s+(\d+) kB$', captured['status'], re.M)}
    return result


def check_wait(pid):
    # Leave an exited meter waitable until cleanup, reserving its PID/process
    # group identity while any remaining descendants are killed.
    info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if info is None:
        return None
    return info.si_status if info.si_code == os.CLD_EXITED else -info.si_status


def kill_and_reap(pid):
    # On failure, kill the actual CLI first and briefly let the native meter
    # reap it. Reserve the meter PID with WNOWAIT throughout this cleanup.
    try:
        children = Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
    except FileNotFoundError:
        children = []
    for child in children:
        try:
            child = int(child)
            if os.getpgid(child) == pid:
                os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass
    grace = time.monotonic() + .25
    while time.monotonic() < grace:
        try:
            if check_wait(pid) is not None:
                break
        except ChildProcessError:
            return
        time.sleep(.002)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if check_wait(pid) is not None:
                os.waitpid(pid, 0)
                return
        except ChildProcessError:
            return
        time.sleep(.002)
    raise TimeoutError(f'meter process {pid} did not reap after SIGKILL')


def wait_for_write(meter_pid, expected, deadline):
    while time.monotonic() < deadline:
        code = check_wait(meter_pid)
        if code is not None:
            raise RuntimeError(f'meter exited before stdout blocked: {code}')
        try:
            children = Path(f'/proc/{meter_pid}/task/{meter_pid}/children').read_text().split()
            for child in children:
                pid = int(child)
                executable = os.stat(f'/proc/{pid}/exe')
                wchan = Path(f'/proc/{pid}/wchan').read_text().strip()
                if (executable.st_ino == expected['inode'] and executable.st_dev == expected['device']
                        and 'pipe' in wchan and 'write' in wchan):
                    return pid, wchan
        except (FileNotFoundError, ProcessLookupError):
            pass
        time.sleep(.002)
    raise TimeoutError('no matching CLI child blocked in pipe write before timeout')


def drain(fd, padding, output, deadline, limit):
    os.set_blocking(fd, False)
    count = 0
    digest = hashlib.sha256()
    with selectors.DefaultSelector() as selector, output.open('xb') as dest:
        selector.register(fd, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if not selector.select(min(.1, max(0, deadline - time.monotonic()))):
                continue
            try:
                chunk = os.read(fd, 64 * 1024)
            except BlockingIOError:
                continue
            if not chunk:
                if padding:
                    raise ValueError('stdout ended before pipe padding was removed')
                return {'bytes': count, 'sha256': digest.hexdigest()}
            removed = min(padding, len(chunk))
            padding -= removed
            chunk = chunk[removed:]
            count += len(chunk)
            if count > limit:
                raise ValueError('stdout exceeds diagnostic output limit')
            digest.update(chunk)
            dest.write(chunk)
    raise TimeoutError('stdout did not close after releasing full pipe')


def run_one(binary, meter, folder, argv, timeout):
    folder.mkdir(mode=0o700)
    home = folder / 'home'
    for name in ('config', 'cache', 'tmp'):
        (home / name).mkdir(parents=True)
    env = {'PATH': os.defpath, 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / 'config'),
           'XDG_CACHE_HOME': str(home / 'cache'), 'TMPDIR': str(home / 'tmp'),
           'LANG': 'C', 'LC_ALL': 'C', 'TZ': 'UTC', 'TERM': 'dumb',
           'PB_THEME': 'dark', 'PB_ANALYTICS': 'disable'}
    result = {'binary': binary, 'argv': argv, 'passed': False,
              'started_unix_ns': time.time_ns(), 'environment': env}
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    meter_pid = None
    try:
        capacity = fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, 4096)
        padding = bytes(capacity)
        written = 0
        while written < capacity:
            written += os.write(write_fd, padding[written:])
        metrics = folder / 'meter.json'
        stderr_path = folder / 'stderr.bin'
        command = [meter['path'], str(metrics), '--', binary['path'], *argv]
        actions = [(os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
                   (os.POSIX_SPAWN_DUP2, write_fd, 1),
                   (os.POSIX_SPAWN_OPEN, 2, str(stderr_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                   (os.POSIX_SPAWN_CLOSE, read_fd), (os.POSIX_SPAWN_CLOSE, write_fd)]
        defaults = tuple(getattr(signal, name) for name in ('SIGPIPE', 'SIGXFZ', 'SIGXFSZ')
                         if hasattr(signal, name))
        meter_pid = os.posix_spawn(meter['path'], command, env, file_actions=actions,
                                   setsid=True, setsigdef=defaults, setsigmask=())
        os.close(write_fd)
        write_fd = None
        deadline = time.monotonic() + timeout
        pid, wchan = wait_for_write(meter_pid, binary, deadline)
        result.update(meter_pid=meter_pid, pipe_capacity_bytes=capacity, blocked_wchan=wchan,
                      snapshot=capture(pid, folder, binary))
        result['stdout'] = drain(read_fd, capacity, folder / 'stdout.bin',
                                 time.monotonic() + timeout, 16 * 1024 * 1024)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = check_wait(meter_pid)
            if code is not None:
                result['meter_exit_code'] = code
                break
            time.sleep(.002)
        else:
            raise TimeoutError('meter did not exit after CLI stdout closed')
        result['meter'] = json.loads(metrics.read_text())
        if code != 0 or result['meter']['exit_code'] != 0:
            raise ValueError(f'diagnostic command failed: {code}, {result["meter"]["exit_code"]}')
        result['stderr'] = {'bytes': stderr_path.stat().st_size,
                            'sha256': hashlib.sha256(stderr_path.read_bytes()).hexdigest()}
        result['passed'] = True
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        if meter_pid is not None:
            try:
                kill_and_reap(meter_pid)
            except Exception as error:
                result['cleanup_error'] = f'{type(error).__name__}: {error}'
                result['passed'] = False
        for fd in (read_fd, write_fd):
            if fd is not None:
                os.close(fd)
        result['ended_unix_ns'] = time.time_ns()
        write_json(folder / 'run.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--binary', action='append', required=True, metavar='LABEL=PATH')
    parser.add_argument('--meter', type=Path, default=Path('bin/pb-meter'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--seed', type=int, default=2033)
    parser.add_argument('--timeout', type=float, default=10)
    parser.add_argument('--argv', default='["version"]', help='JSON array; defaults to offline version')
    parser.add_argument('--fresh-copies', action='store_true')
    parser.add_argument('--copy-root', type=Path, help='new private directory; use an executable tmpfs for a storage control')
    parser.add_argument('--history-crossover', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or args.repetitions < 1 or not 0 < args.timeout < 3600:
        parser.error('Linux, positive repetitions and a timeout below 3600s are required')
    if args.history_crossover and not args.fresh_copies:
        parser.error('--history-crossover requires --fresh-copies')
    if args.copy_root and not args.fresh_copies:
        parser.error('--copy-root requires --fresh-copies')
    argv = json.loads(args.argv)
    if not isinstance(argv, list) or not argv or not all(isinstance(s, str) for s in argv):
        parser.error('--argv must be a nonempty JSON array of strings')
    originals = {}
    for item in args.binary:
        label, sep, path = item.partition('=')
        if not sep or not re.fullmatch(r'[A-Za-z0-9_-]+', label) or label in originals:
            parser.error('--binary requires unique filesystem-safe LABEL=PATH entries')
        originals[label] = identity(Path(path))
    meter = identity(args.meter)
    out = args.out.resolve()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    rng = random.Random(args.seed)
    variants = {label + '-original-path': value for label, value in originals.items()}
    histories = {}
    report = {'schema_version': 1, 'kind': 'natural-write RSS attribution, not performance ranking',
              'complete': False, 'passed': False, 'originals': originals, 'meter': meter,
              'configuration': vars(args) | {'out': str(out), 'meter': meter['path'],
                                              'copy_root': str(args.copy_root) if args.copy_root else None},
              'host': {'kernel': os.uname().release, 'machine': os.uname().machine,
                       'affinity': sorted(os.sched_getaffinity(0)),
                       'page_size_bytes': os.sysconf('SC_PAGE_SIZE')},
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'hashing': 'all original binaries hashed before any diagnostic runs',
              'copy_order': [], 'order': [], 'runs': []}
    if args.fresh_copies:
        copies = args.copy_root.resolve() if args.copy_root else out / 'private-copies'
        copies.mkdir(mode=0o700, parents=True, exist_ok=False)
        report['copy_root'] = str(copies)
        report['copy_storage'] = storage_info(copies)
        labels = list(originals)
        rng.shuffle(labels)
        report['copy_order'] = labels
        report['copy_method'] = 'exclusive private file; 1MiB read/write chunks; fsync; mode0500; SHA256 verification'
        for label in labels:
            copied = private_copy(originals[label], copies / label)
            if args.history_crossover:
                for condition in ('write', 'read'):
                    variant = label + '-copy-' + condition
                    variants[variant] = copied
                    histories[variant] = {'source_label': label, 'condition': condition}
            else:
                variants[label + '-fresh-copy'] = copied
    report['histories'] = histories
    report['variants'] = variants
    write_json(out / 'report.json', report)
    for repetition in range(args.repetitions):
        order = list(variants)
        rng.shuffle(order)
        report['order'].append({'repetition': repetition, 'variants': order})
        for index, label in enumerate(order):
            preparation = None
            if label in histories:
                treatment = histories[label]
                variants[label], preparation = prepare_history(originals[treatment['source_label']], variants[label],
                                                                 treatment['condition'], copies)
            folder = out / f'{repetition:02d}-{index:02d}-{label}'
            run = run_one(variants[label], meter, folder, argv, args.timeout)
            run.update(variant=label, repetition=repetition, order_in_round=index,
                       artifact=str(folder.relative_to(out)), history=histories.get(label),
                       private_residency_preparation=preparation)
            write_json(folder / 'run.json', run)
            report['runs'].append(run)
            write_json(out / 'report.json', report)
            print(json.dumps({'variant': label, 'repetition': repetition, 'passed': run['passed'],
                              'smaps_rss_kb': run.get('snapshot', {}).get('total_kb', {}).get('Rss'),
                              'native_peak_rss_bytes': run.get('meter', {}).get('peak_rss_bytes'),
                              'error': run.get('error')}), flush=True)
    report['identities_after'] = {label: identity(Path(value['path'])) for label, value in variants.items()}
    report['meter_after'] = identity(Path(meter['path']))
    report['inputs_unchanged'] = all(report['identities_after'][label]['sha256'] == value['sha256']
                                    for label, value in variants.items()) and report['meter_after']['sha256'] == meter['sha256']
    report['complete'] = True
    report['passed'] = report['inputs_unchanged'] and all(run['passed'] for run in report['runs'])
    write_json(out / 'report.json', report)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
