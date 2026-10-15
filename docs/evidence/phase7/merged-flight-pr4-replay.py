#!/usr/bin/env python3
"""Durable PR4 decoder gate: seven pinned IPC streams, JSON/text, fatal status 3.

No cache imports or builds. The original verify_decoder_flight.py (e27354c8...)
and probe.py (24b2324b...) were read, never executed. Only their required
scenario, config, environment, ordering and byte-comparison policy is embedded.
Each role gets its own fixture and HOME; stdout/stderr remain unmodified bytes.

Required: --candidate PATH --candidate-sha256 HEX --go PATH --go-sha256 HEX
          --fixture PATH --fixture-sha256 HEX --out NEW_DIRECTORY
Exit 0 requires all 14 exact pairs, no process/cleanup errors, unchanged inputs.
proof.json is checkpointed from the outset, including failures and partial runs.
Observe exit without reaping; signal only an owned, still-unreaped leader's
group, then obtain the exit from an exact final reap. Reject nondefault SIGCHLD.
Interrupt handlers set a flag: returned children are registered before any
raising safe point. Cleanup is signal-masked through the durable capture record;
no cleanup mask is inherited by either child. SIGKILL and failures inside Popen
before it returns an owned handle remain outside this cleanup guarantee.
Bounds: fixture readiness 10s, CLI 8s, each reap 2s, run watchdog 20 minutes,
final input/artifact hashing 60s (under 30 minutes including cleanup).
Path identity checks cannot detect replacement restored between checkpoints;
descendants deliberately escaping their process group are outside ownership.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import struct
import subprocess
import sys
import time
from urllib.parse import urlsplit


HERE = Path(__file__).absolute()
VALIDATION = HERE.parent.parent / 'phase6' / 'pr4-fixes-validation.json'
FIXTURES = HERE.parent / 'merged-flight-pr4-fixtures'
VALIDATION_SHA256 = 'a92a6ab0321b1bc13faf8c647fc097dfbcc404850db71cf21b86318d2a937141'
ORIGINS = {
    'verify_decoder_flight.py': 'e27354c8ce7f71883379e956b38ef05982fba3fd80fae1ef71771239e81453d1',
    'probe.py': '24b2324bfe4ee72de4b21ed44e9003568bba345d0d9ff1c714b8648916e3aff6',
    'proof.json': '64a69e39d2ae85d92c977db33f0c4f588e9f5292ea04ab001bb7e55cd0f1afe2',
}
STREAMS = (
    'o1_leaf_child_dict', 'o2_leaf_child_dict_unsent',
    'orphan_nested_unsent_child', 'orphan_parent_last_live_binding',
    'orphan_type_conflict', 'o1_control', 'o2_control',
)
MODES = ('json', 'text')
BASIC = {'authorization': 'Basic aGFybmVzczpoYXJuZXNz'}
FIXTURE_TIMEOUT = 10
CLI_TIMEOUT = 8
REAP_TIMEOUT = 2
TOTAL_TIMEOUT = 1200
FINAL_TIMEOUT = 60
INTERRUPT_SIGNALS = {signal.SIGALRM, signal.SIGINT, signal.SIGTERM}
_pending_signal = None


def require_default_sigchld():
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise RuntimeError('SIGCHLD must be SIG_DFL before acquiring children')


def interrupted(signum, frame):
    # No asynchronous Python exception in Popen-return/registration or exact
    # reap/status bookkeeping. This standalone runner has no competing reaper.
    global _pending_signal
    if _pending_signal is None:
        _pending_signal = signum


def check_interrupt():
    if _pending_signal is not None:
        raise InterruptedError(f'interrupted by signal {_pending_signal}')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            check_interrupt()
            h.update(block)
    return h.hexdigest()


def save(path, value):
    # Only the uniquely claimed output tree is ever rewritten.
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def bind(inputs, label, path, expected=None, read=False):
    check_interrupt()
    path = Path(path).absolute()
    record = inputs[label] = {'path': str(path), 'expected_sha256': expected}
    data = path.read_bytes() if read else None
    digest = sha256(data) if read else file_sha256(path)
    check_interrupt()
    record['start_sha256'] = digest
    if expected is not None and digest != expected:
        raise ValueError(f'{label}: SHA256 mismatch: {digest} != {expected}')
    return data


def end_bindings(inputs):
    errors = []
    for label, record in inputs.items():
        try:
            record['end_sha256'] = file_sha256(record['path'])
            record['unchanged'] = record['end_sha256'] == record.get('start_sha256')
            if not record['unchanged']:
                raise ValueError('input changed or initial read failed')
        except InterruptedError:
            raise
        except Exception as e:
            record['end_error'] = f'{type(e).__name__}: {e}'
            errors.append(f'{label}: {e}')
    return errors


def messages(data):
    """Original IPC-to-raw Flight framing, with explicit bounds (also under -O)."""
    cursor, result = 0, []
    while cursor < len(data):
        if cursor + 8 > len(data):
            raise ValueError('truncated IPC prefix')
        marker, length = struct.unpack_from('<Ii', data, cursor)
        cursor += 8
        if marker != 0xffffffff or length < 0:
            raise ValueError('invalid IPC prefix')
        if length == 0:
            if cursor != len(data):
                raise ValueError('bytes after IPC end marker')
            break
        metadata = data[cursor:cursor + length]
        cursor += length
        if len(metadata) != length or length < 4:
            raise ValueError('truncated IPC metadata')
        offset = struct.unpack_from('<I', metadata)[0]
        if not 4 <= offset <= length - 4:
            raise ValueError('invalid IPC table offset')
        vtable = offset - struct.unpack_from('<i', metadata, offset)[0]
        if not 0 <= vtable <= length - 2:
            raise ValueError('invalid IPC vtable offset')
        vlength = struct.unpack_from('<H', metadata, vtable)[0]
        if vlength < 4 or vtable + vlength > length:
            raise ValueError('invalid IPC vtable length')
        field = struct.unpack_from('<H', metadata, vtable + 10)[0] if vlength >= 12 else 0
        if field and offset + field + 8 > length:
            raise ValueError('truncated IPC body length')
        body_length = struct.unpack_from('<q', metadata, offset + field)[0] if field else 0
        if body_length < 0 or cursor + body_length > len(data):
            raise ValueError('invalid IPC body length')
        body = data[cursor:cursor + body_length]
        cursor += body_length
        result.append(dict(op='raw', header_b64=base64.b64encode(metadata).decode(),
                           body_b64=base64.b64encode(body).decode()))
    if not result:
        raise ValueError('empty IPC stream')
    return result


def load_streams(inputs):
    validation = json.loads(bind(inputs, 'phase6_validation', VALIDATION,
                                 VALIDATION_SHA256, read=True))
    checks = [c for c in validation['checks'] if c.get('name') == 'full_flight_decoder_pairs']
    if len(checks) != 1 or checks[0]['passed'] != 14:
        raise ValueError('expected one Phase6 decoder14 check')
    cases = checks[0]['cases']
    expected = {f'{stem}-{mode}' for stem in STREAMS for mode in MODES}
    if len(cases) != 14 or {c['name'] for c in cases} != expected:
        raise ValueError('Phase6 decoder case set changed')
    manifest = json.loads(bind(inputs, 'fixture_manifest', FIXTURES / 'manifest.json', read=True))
    if (manifest['validation_sha256'] != VALIDATION_SHA256
            or set(manifest['streams']) != {s + '.arrows' for s in STREAMS}):
        raise ValueError('fixture manifest does not bind the seven Phase6 streams')
    streams = {}
    # Original sorted full source paths: go-contract's five, then two controls.
    for stem in STREAMS:
        hashes = {c['stream_sha256'] for c in cases if c['name'].rsplit('-', 1)[0] == stem}
        if len(hashes) != 1:
            raise ValueError(f'{stem}: inconsistent Phase6 stream hashes')
        digest = hashes.pop()
        entry = manifest['streams'][stem + '.arrows']
        data = bind(inputs, stem, FIXTURES / (stem + '.arrows'), digest, read=True)
        if entry['sha256'] != digest or entry['bytes'] != len(data):
            raise ValueError(f'{stem}: fixture manifest mismatch')
        streams[stem] = (digest, messages(data) + [dict(op='status', code=3, message='probe-end')])
    return streams


def env(root):
    # Exact required environment from probe.py; never inherit ambient credentials.
    return {'PATH': os.defpath, 'HOME': str(root), 'XDG_CONFIG_HOME': str(root / 'config'),
            'XDG_CACHE_HOME': str(root / 'cache'), 'TMPDIR': str(root / 'tmp'), 'LANG': 'C',
            'LC_ALL': 'C', 'TZ': 'UTC', 'TERM': 'dumb', 'PB_THEME': 'dark', 'PB_ANALYTICS': 'disable'}


def scenario(root, ops):
    return {'headers': BASIC, 'routes': [
        {'method': 'GET', 'path': '/api/v1/about', 'status': 200,
         'body': '{"grpcPort":{{grpc_port}}}'}],
        'phase6': {'events_path': str(root / 'events.jsonl'),
                   'summary_path': str(root / 'summary.json'), 'event_limit': 20000,
                   'flight_auth': BASIC, 'streams': {'logs': {
                       'schema': 'basic', 'seed': 1, 'subscriptions': [{'ops': ops}]}}}}


def seed(root, url):
    d = root / 'config' / 'pb'
    d.mkdir(parents=True)
    (d / 'config.toml').write_text('DefaultProfile = "fixture"\n[Profiles.fixture]\nurl = '
                                 + json.dumps(url) + '\nusername = "harness"\npassword = "harness"\ncloud = false\n')
    (root / 'tmp').mkdir()


def ready(proc, path):
    # readline() after select can block forever on a partial banner. Read chunks
    # against one deadline instead, retaining even malformed/partial readiness.
    deadline, data = time.monotonic() + FIXTURE_TIMEOUT, bytearray()
    with selectors.DefaultSelector() as sel, path.open('wb') as log:
        sel.register(proc.stdout, selectors.EVENT_READ)
        while True:
            check_interrupt()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('fixture readiness timeout')
            if not sel.select(min(0.05, remaining)):
                continue
            block = os.read(proc.stdout.fileno(), 4096)
            if not block:
                raise RuntimeError('fixture exited before readiness')
            log.write(block)
            log.flush()
            data.extend(block)
            if len(data) > 1024 * 1024:
                raise ValueError('fixture readiness banner too large')
            if b'\n' in data:
                info = json.loads(data.split(b'\n', 1)[0])
                url = urlsplit(info['url'])
                if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port:
                    raise ValueError('fixture readiness URL must be loopback HTTP')
                return info


def observe(proc):
    if proc.returncode is not None:
        raise RuntimeError(f'leader {proc.pid} already reaped; ownership unknown')
    result = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
    if result is not None and result.si_pid != proc.pid:
        raise RuntimeError('waitid returned an unexpected child')
    return result


def exited(proc, timeout):
    deadline = time.monotonic() + timeout
    while True:
        check_interrupt()
        if observe(proc) is not None:
            check_interrupt()
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        time.sleep(min(0.01, remaining))


def kill_group(proc, owned):
    # Only this runner's registered, unreaped child may reserve the numeric
    # PGID. ECHILD/unknown ownership fails before killpg, never Popen's ECHILD=>0.
    if owned.get(proc.pid) is not proc:
        raise RuntimeError('unregistered leader; refusing killpg')
    require_default_sigchld()
    observe(proc)  # WNOWAIT: live child or reserved zombie; ECHILD refuses signal.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + REAP_TIMEOUT
    while True:
        pid, status = os.waitpid(proc.pid, os.WNOHANG)
        if pid == proc.pid:
            code = os.waitstatus_to_exitcode(status)
            proc.returncode = code  # Suppress Popen destructor's separate reap.
            del owned[proc.pid]
            return code
        if pid != 0:
            raise RuntimeError('waitpid returned an unexpected child')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, REAP_TIMEOUT)
        time.sleep(min(0.01, remaining))


def capture(root, binary, fixture, ops, mode, expected_binary, expected_fixture):
    require_default_sigchld()
    check_interrupt()
    root.mkdir(parents=True)
    fxroot = root / 'fx'
    fxroot.mkdir()
    plan = fxroot / 'scenario.json'
    save(plan, scenario(fxroot, ops))
    record = {'argv': [str(binary), 'tail', 'logs', '-o', mode], 'complete': False,
              'timed_out': False, 'cleanup_errors': [], 'processes': []}
    owned = {}
    cli = None
    t0 = time.monotonic()
    try:
        if file_sha256(fixture) != expected_fixture or file_sha256(binary) != expected_binary:
            raise ValueError('binary/fixture changed before spawn')
        with (fxroot / 'fixture.stderr').open('wb') as log:
            check_interrupt()
            require_default_sigchld()
            fx = subprocess.Popen([str(fixture), '--scenario', str(plan)], env=env(fxroot),
                                  stdout=subprocess.PIPE, stderr=log, start_new_session=True)
            owned[fx.pid] = fx
            check_interrupt()  # Always register a returned child first.
            info = ready(fx, fxroot / 'fixture.stdout')
            save(fxroot / 'ready.json', info)
            seed(root, info['url'])
            # Files preserve partial bytes even if this adapter is interrupted.
            with (root / 'stdout').open('wb') as out, (root / 'stderr').open('wb') as err:
                check_interrupt()
                require_default_sigchld()
                cli = subprocess.Popen(record['argv'], env=env(root), stdout=out,
                                       stderr=err, start_new_session=True)
                owned[cli.pid] = cli
                check_interrupt()
                try:
                    exited(cli, CLI_TIMEOUT)
                    record['complete'] = True  # Exit comes only from final reap.
                except subprocess.TimeoutExpired:
                    record['timed_out'] = True
                    raise TimeoutError('CLI timeout')
    except InterruptedError as e:
        record['error'] = f'{type(e).__name__}: {e}'
        raise
    except Exception as e:
        record['error'] = f'{type(e).__name__}: {e}'
    finally:
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, INTERRUPT_SIGNALS)
        try:
            for proc in reversed(tuple(owned.values())):
                code, reaped = None, False
                try:
                    code = kill_group(proc, owned)
                    reaped = True
                    if proc is cli:
                        record['exit'] = code
                except Exception as e:
                    record['cleanup_errors'].append(f'{type(e).__name__}: {e}')
                finally:
                    record['processes'].append({'pid': proc.pid, 'exit': code, 'reaped': reaped})
                    if proc.stdout is not None:
                        proc.stdout.close()
            record['secs'] = round(time.monotonic() - t0, 2)
            for key in ('stdout', 'stderr'):
                path = root / key
                if path.exists():
                    data = path.read_bytes()
                    record[key] = base64.b64encode(data).decode()
                    record[key + '_sha256'] = sha256(data)
            record['interrupt_signal'] = _pending_signal
            save(root / 'capture.json', record)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    check_interrupt()  # Includes signals deferred until after durable cleanup.
    return record


def compare(captures):
    go, rs = captures['go'], captures['rs']
    same = {key: go.get(key) == rs.get(key) for key in ('exit', 'stdout', 'stderr')}
    healthy = all(c.get('complete') and not c.get('timed_out') and not c.get('error')
                  and not c.get('cleanup_errors') and all(k in c for k in same)
                  for c in (go, rs))
    return same, healthy and all(same.values())


def digest_arg(value):
    if not re.fullmatch('[0-9a-f]{64}', value):
        raise argparse.ArgumentTypeError('expected 64 lowercase SHA256 hex digits')
    return value


def main(argv=None):
    global _pending_signal
    parser = argparse.ArgumentParser(description=__doc__)
    for role in ('candidate', 'go', 'fixture'):
        parser.add_argument('--' + role, type=Path, required=True)
        parser.add_argument('--' + role + '-sha256', type=digest_arg, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    require_default_sigchld()  # Reject ignored/custom inheritance; never reset it.
    _pending_signal = None
    out = args.out.absolute()
    out.mkdir(parents=True, exist_ok=False)  # Refuse even an empty existing dir.
    proof = {'schema_version': 1, 'status': 'incomplete', 'origins': ORIGINS,
             'candidate_sha256': args.candidate_sha256, 'inputs': {}, 'cases': {},
             'errors': [], 'limits_seconds': {'fixture': FIXTURE_TIMEOUT, 'cli': CLI_TIMEOUT,
                                            'reap': REAP_TIMEOUT, 'total': TOTAL_TIMEOUT,
                                            'finalization': FINAL_TIMEOUT},
             'output_waivers': []}
    checkpoint = out / 'proof.json'
    save(checkpoint, proof)
    started = time.monotonic()
    previous = {s: signal.signal(s, interrupted) for s in INTERRUPT_SIGNALS}
    signal.setitimer(signal.ITIMER_REAL, TOTAL_TIMEOUT)
    try:
        bind(proof['inputs'], 'adapter', HERE)
        for role in ('candidate', 'go', 'fixture'):
            path = getattr(args, role).absolute()
            setattr(args, role, path)
            bind(proof['inputs'], role, path, getattr(args, role + '_sha256'))
            if not os.access(path, os.X_OK):
                raise ValueError(f'{role}: not executable')
        streams = load_streams(proof['inputs'])
        save(checkpoint, proof)
        for stem, (digest, ops) in streams.items():
            for mode in MODES:
                name = stem + '-' + mode
                case = proof['cases'][name] = {'source_sha256': digest, 'captures': {}}
                save(checkpoint, proof)
                for role, binary, binary_digest in (('go', args.go, args.go_sha256),
                                                    ('rs', args.candidate, args.candidate_sha256)):
                    case['captures'][role] = capture(out / name / role, binary, args.fixture,
                                                    ops, mode, binary_digest, args.fixture_sha256)
                    check_interrupt()
                    save(checkpoint, proof)
                    if case['captures'][role].get('cleanup_errors'):
                        raise RuntimeError(f'{name}/{role}: cleanup failed; stopping')
                case['same'], case['passed'] = compare(case['captures'])
                save(checkpoint, proof)
    except Exception as e:
        proof['errors'].append(f'{type(e).__name__}: {e}')
    finally:
        # capture() has cleaned up before this point. Bound final hashing too;
        # a failed final checkpoint can never retain a successful status.
        finalization_mask = signal.pthread_sigmask(signal.SIG_BLOCK, INTERRUPT_SIGNALS)
        try:
            if _pending_signal is not None:
                proof['errors'].append(f'interrupted by signal {_pending_signal}')
            _pending_signal = None  # Finalization gets its own watchdog/safe points.
            signal.setitimer(signal.ITIMER_REAL, FINAL_TIMEOUT)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, finalization_mask)
        proof['artifacts'] = {}
        try:
            proof['errors'].extend(end_bindings(proof['inputs']))
            for p in sorted(out.rglob('*')):
                check_interrupt()
                if p.is_file() and p != checkpoint:
                    proof['artifacts'][str(p.relative_to(out))] = file_sha256(p)
            check_interrupt()
        except Exception as e:
            proof['errors'].append(f'finalization: {type(e).__name__}: {e}')
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        proof['passed'] = sum(c.get('passed', False) for c in proof['cases'].values())
        proof['complete'] = len(proof['cases']) == 14 and all(
            'passed' in c for c in proof['cases'].values())
        proof['status'] = ('passed' if proof['complete'] and proof['passed'] == 14
                           and not proof['errors'] else 'failed')
        proof['secs'] = round(time.monotonic() - started, 2)
        try:
            save(checkpoint, proof)
            # A late interrupt during the terminal save must fail the proof too.
            if _pending_signal is not None:
                proof['errors'].append(f'late interrupt by signal {_pending_signal}')
                proof['status'] = 'failed'
                save(checkpoint, proof)
        finally:
            for s, handler in previous.items():
                signal.signal(s, handler)
    print(f"{proof['status']}: {proof['passed']}/14 exact Flight pairs; {checkpoint}")
    return 0 if proof['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
