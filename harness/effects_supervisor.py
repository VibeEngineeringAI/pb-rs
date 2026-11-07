#!/usr/bin/env python3
"""Own one effects fixture and reap its descendants, including detached CLIs.

Run in a separate Linux process for each fixture. Subreaper adoption keeps a
CLI's separate session owned even when a timeout kills its Python fixture.
"""
import argparse
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def cleanup(timeout=3):
    deadline = time.monotonic() + timeout
    found = False
    while True:
        children = Path(f'/proc/self/task/{os.getpid()}/children').read_text().split()
        if not children:
            return found
        found = True
        for value in children:
            pid = int(value)
            try:
                fd = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            try:
                # Check ownership after opening the stable handle. Never signal
                # a reused numeric PID that is no longer our own child.
                try:
                    parent = int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[1])
                    if parent == os.getpid():
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except FileNotFoundError:
                    pass
            finally:
                os.close(fd)
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if not pid:
                break
        if time.monotonic() >= deadline:
            raise RuntimeError('fixture descendant cleanup exceeded its deadline')
        time.sleep(.01)


def supervise(command, timeout, parent):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'cannot own fixture descendants')

    def interrupted(number, _frame):
        raise InterruptedError(f'supervisor received signal {number}')

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0):  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), 'cannot observe fixture driver death')
    if os.getppid() != parent:
        raise InterruptedError('fixture driver exited before supervisor setup')
    proc = None
    try:
        proc = subprocess.Popen(command, start_new_session=True)
        returncode = proc.wait(timeout=timeout)
    finally:
        # Ignore further cancellation while unwinding ownership. SIGKILL of a
        # supervisor cannot be recovered, so the driver uses our own deadline.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        leftovers = cleanup()
        if proc is not None:
            proc.wait()
    if leftovers:
        raise RuntimeError('fixture exited with uncollected descendants')
    return returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, required=True)
    parser.add_argument('--parent', type=int, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.timeout <= 0 or not args.command or args.command[0] != '--' or len(args.command) < 2:
        parser.error('positive timeout and a command after -- are required')
    try:
        return supervise(args.command[1:], args.timeout, args.parent)
    except (subprocess.TimeoutExpired, InterruptedError, RuntimeError, OSError) as error:
        print(f'effects supervisor: {error}', file=sys.stderr, flush=True)
        return 124


if __name__ == '__main__':
    raise SystemExit(main())
