#!/usr/bin/env python3
"""Explicit <=60s Linux own-Python controls, with parent subreaper/final cleanup.

No source import starts children. This executes only when called as a program.
No native meter, fixture binary, Go/Rust program, build, compiler or lock is used.
Real wait4 propagation below is supervisor fallback, not native-meter evidence.
"""
import argparse
import contextlib
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from unittest import mock

TMP = Path('/home/ajs/.cache/pb-phase7/tmp')
FIELDS = ('ru_utime', 'ru_stime', 'ru_maxrss', 'ru_minflt', 'ru_majflt',
          'ru_nvcsw', 'ru_nivcsw', 'ru_inblock', 'ru_oublock')


def children():
    return set(map(int, Path(f'/proc/self/task/{os.getpid()}/children').read_text().split()))


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def load_runner(path):
    pure_path = Path(__file__).with_name('shared_runner_linux_ownership_test.py')
    spec = importlib.util.spec_from_file_location('ownership_import_guard', pure_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # Definitions only.
    return module.load(path)  # Source imported under process guards.


def execute(runner_path, out):
    if sys.platform != 'linux' or not hasattr(os, 'WNOWAIT'):
        raise RuntimeError('control requires Linux WNOWAIT and /proc; nothing spawned')
    if out.exists():
        raise RuntimeError('native control requires a new output path; nothing spawned')
    if children():
        raise RuntimeError('control interpreter must begin with no children')
    runner = load_runner(runner_path)
    runner._require_measured_ownership()
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    require(old_timer == (0.0, 0.0), 'control requires no existing real-time timer')
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.restype = ctypes.c_int
    old_subreaper = ctypes.c_int()
    if prctl(37, ctypes.byref(old_subreaper), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'PR_GET_CHILD_SUBREAPER')
    if prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'PR_SET_CHILD_SUBREAPER')

    started = time.monotonic()
    leaders, created = set(), set()
    records, cleanup_records = [], []
    report = dict(status='failed', native_meter_executed=False, fixture_binary_executed=False,
                  resource_scope='actual direct-Python-child wait4 supervisor fallback only',
                  runner_sha256=hashlib.sha256(runner_path.read_bytes()).hexdigest(),
                  records=records, cleanup=cleanup_records)
    old_alarm = signal.getsignal(signal.SIGALRM)
    def deadline(signum, frame):
        raise TimeoutError('own control 45-second deadline')
    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 45)
    real_waitid, real_wait4, real_killpg = os.waitid, os.wait4, os.killpg
    real_spawn, real_clock, real_popen = runner.spawn_measured, runner.clock_ns, subprocess.Popen

    def register(proc):
        leaders.add(proc.pid)
        created.add(proc.pid)
        return proc

    def drain_owned(label, *, allow_signal=True):
        """Signal only exact, unreaped direct children; include adopted descendants."""
        until = time.monotonic() + 4
        while children() and time.monotonic() < until:
            for pid in children():
                created.add(pid)
                try:
                    observed = real_waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                except ChildProcessError:
                    continue
                signaled = False
                if observed is None and not allow_signal:
                    # Passing controls must show descendants terminated from
                    # the runner's signal, with no test-cleanup assistance.
                    continue
                if observed is None:
                    signaled = True
                    # No other thread/reaper exists. Successful exact-child
                    # WNOWAIT keeps this identity reserved until the wait below.
                    if pid in leaders:
                        try:
                            real_killpg(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                got, status, usage = real_wait4(pid, os.WNOHANG)
                if got:
                    cleanup_records.append(dict(label=label, pid=got, status=status, test_cleanup_signaled=signaled))
            if children():
                time.sleep(.005)
        require(not children(), f'owned children remain after {label}: {children()}')

    def measurement(name, code, expected_out, expected_err, expected_termination, *, held=False):
        with tempfile.TemporaryDirectory(prefix='shared-native-r2-', dir=TMP) as directory:
            root = Path(directory)
            trace, usage_result = [], []
            clock = {'last': None, 'observed_exit': None}
            def observe_clock():
                clock['last'] = real_clock()
                return clock['last']
            def observe(kind, pid, flags):
                value = real_waitid(kind, pid, flags)
                trace.append(dict(event='waitid', pid=pid, exited=value is not None,
                                  flags=flags, clock=clock['last']))
                if value is not None and clock['observed_exit'] is None:
                    clock['observed_exit'] = clock['last']
                return value
            def group(pid, sig):
                owned = real_waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                trace.append(dict(event='group_signal', pid=pid, signal=int(sig),
                                  still_waitable=owned is not None))
                return real_killpg(pid, sig)
            def reap(pid, flags):
                require(flags == 0, 'generated measurement reaped before final cleanup')
                observed = real_waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                value = real_wait4(pid, flags)
                usage_result.append(value)
                trace.append(dict(event='wait4', pid=pid, raw_status=value[1],
                                  exited_before_reap=observed is not None,
                                  usage={field: getattr(value[2], field) for field in FIELDS}))
                # Exaggerate observer finalization cost causally; no native wall
                # is being measured and no performance conclusion is drawn.
                time.sleep(.03)
                return value
            def launch(argv, env, cwd, stdout_path=None):
                return register(real_spawn([sys.executable, '-c', code, str(root / 'descendant.json')],
                                           env, cwd, stdout_path))
            with mock.patch.object(runner, 'spawn_measured', side_effect=launch), \
                    mock.patch.object(runner, 'clock_ns', side_effect=observe_clock), \
                    mock.patch.object(os, 'waitid', side_effect=observe), \
                    mock.patch.object(os, 'wait4', side_effect=reap), \
                    mock.patch.object(os, 'killpg', side_effect=group):
                result, stdout, stderr = runner.measure(['NOT-EXECUTED'], runner.isolated_env(root), root,
                    meter='NOT-EXECUTED', timeout=.2 if held else 2)
            require((stdout, stderr) == (expected_out, expected_err), f'{name}: output mismatch')
            require(len(usage_result) == 1, f'{name}: expected sole final wait4')
            pid, status, usage = usage_result[0]
            require(result['resource_source'] == 'supervisor_wait4_fallback', 'must not claim native meter')
            require(result['termination'] == expected_termination, f'{name}: termination changed')
            require(result['cpu_user_ms'] == usage.ru_utime*1000, 'real user rusage not propagated')
            require(result['cpu_system_ms'] == usage.ru_stime*1000, 'real system rusage not propagated')
            require(result['peak_rss_bytes'] == usage.ru_maxrss*1024, 'real RSS not propagated')
            for output, field in (('minor_faults','ru_minflt'), ('major_faults','ru_majflt'),
                    ('voluntary_context_switches','ru_nvcsw'), ('involuntary_context_switches','ru_nivcsw'),
                    ('block_input_operations','ru_inblock'), ('block_output_operations','ru_oublock')):
                require(result[output] == getattr(usage,field), f'real {field} not propagated')
            require(clock['observed_exit'] is not None, f'{name}: exit not observed')
            expected_wall = (clock['observed_exit'] - result['started_monotonic_ns']) / 1e6
            require(result['wall_ms'] == expected_wall, f'{name}: final reap replaced exit observation')
            reap_index = next(i for i,event in enumerate(trace) if event['event'] == 'wait4')
            require(not any(event['event'] == 'group_signal' for event in trace[reap_index+1:]),
                    'group access after reap')
            require(any(event['event'] == 'group_signal' and event['still_waitable'] for event in trace),
                    'no group signal while exited leader remained waitable')
            try:
                real_waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                pass
            else:
                raise AssertionError('leader was not reaped')
            descendant = root / 'descendant.json'
            if descendant.exists():
                created.add(json.loads(descendant.read_text())['pid'])
            records.append(dict(name=name, status='passed', trace=trace, result=result,
                expected_wall_ms=expected_wall,
                stdout_sha256=hashlib.sha256(stdout).hexdigest(), stderr_sha256=hashlib.sha256(stderr).hexdigest()))
            drain_owned(name, allow_signal=False)

    descendant_code = r'''
import json, os, sys, time
r, w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(w)
    if os.read(r,1) != b'x': os._exit(2)
    os.close(r)
    if REDIRECT:
        fd = os.open(os.devnull, os.O_RDWR)
        os.dup2(fd,1); os.dup2(fd,2); os.close(fd)
    time.sleep(10)
    os._exit(0)
os.close(r)
with open(sys.argv[1], 'w') as record: json.dump({'pid':pid},record)
os.write(w,b'x'); os.close(w)
os.write(1,b'leader\n')
os._exit(0)
'''

    def fixture_control(interrupt):
        with tempfile.TemporaryDirectory(prefix='shared-fixture-native-r2-',dir=TMP) as directory:
            root = Path(directory)
            launched=[]
            code = ("import json,signal,sys,time; from pathlib import Path; "
                    "Path(sys.argv[1]).write_text(json.dumps([int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK,set())])); "
                    "print(json.dumps({'url':'http://127.0.0.1:1','grpc_port':1234}),flush=True); time.sleep(10)")
            def popen(argv, **kwargs):
                proc = register(real_popen([sys.executable,'-c',code,str(root/'mask.json')], **kwargs))
                launched.append(proc)
                if interrupt:
                    os.kill(os.getpid(),signal.SIGINT)
                return proc
            with mock.patch.object(subprocess,'Popen',side_effect=popen):
                if interrupt:
                    try:
                        runner.Fixture('NOT-EXECUTED',{},root)
                    except KeyboardInterrupt:
                        pass
                    else:
                        raise AssertionError('pending handoff SIGINT was lost')
                else:
                    fixture=runner.Fixture('NOT-EXECUTED',{},root)
                    require(signal.SIGINT not in json.loads((root/'mask.json').read_text()),
                            'fixture child inherited blocked SIGINT')
                    fixture.close()
                    fixture.close()
                    require(fixture.proc is None and fixture.log is None, 'fixture ownership not cleared')
            require(len(launched)==1 and launched[0].returncode is not None, 'fixture child not reaped')
            require(launched[0].stdout.closed, 'fixture stdout not closed')
            records.append(dict(name='fixture-handoff-interrupt' if interrupt else 'fixture-repeat-close',
                                status='passed', pid=launched[0].pid, returncode=launched[0].returncode))
            drain_owned('fixture', allow_signal=False)

    try:
        measurement('ordinary-exit19-real-rusage',
            "import os; data=bytearray(1048576); os.write(1,b'out\\n'); os.write(2,b'err\\n'); os._exit(19)",
            b'out\n',b'err\n',{'exit':19})
        measurement('exited-leader-held-pipes', descendant_code.replace('REDIRECT','False'),
                    b'leader\n', b'', {'exit':0}, held=True)
        measurement('exited-leader-redirected-descendant', descendant_code.replace('REDIRECT','True'),
                    b'leader\n', b'', {'exit':0})
        measurement('signal15-preserved', 'import os,signal; os.kill(os.getpid(),signal.SIGTERM)',
                    b'',b'',{'signal':15})
        measurement('numeric143-preserved', 'raise SystemExit(143)', b'',b'',{'exit':143})
        fixture_control(False)
        fixture_control(True)
        report['status']='passed'
    except BaseException as error:
        report['error']=f'{type(error).__name__}: {error}'
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        previous_mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGINT,signal.SIGALRM})
        try:
            drain_owned('outer-finally')
        finally:
            remaining=sorted(children())
            report.update(elapsed_seconds=time.monotonic()-started,
                          child_pids_created=sorted(created), child_processes_created=len(created),
                          children_remaining=remaining)
            prctl(36,old_subreaper.value,0,0,0)
            restored=ctypes.c_int()
            restore_result=prctl(37,ctypes.byref(restored),0,0,0)
            report['subreaper_restored']=(restore_result==0 and restored.value==old_subreaper.value)
            signal.signal(signal.SIGALRM,old_alarm)
            signal.pthread_sigmask(signal.SIG_SETMASK,previous_mask)
            with out.open('x') as stream:
                stream.write(json.dumps(report,indent=2)+'\n')
    require(report['children_remaining']==[], 'children remain')
    require(report['subreaper_restored'], 'subreaper not restored')
    require(report['elapsed_seconds'] < 60, 'control exceeded 60 seconds')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runner',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    report=execute(args.runner.resolve(),args.out)
    print(json.dumps({key:report[key] for key in ('status','elapsed_seconds','child_processes_created',
                     'children_remaining','subreaper_restored','resource_scope')},indent=2))


if __name__=='__main__':
    main()
