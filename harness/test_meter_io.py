#!/usr/bin/env python3
"""Kernel child IO is collected before reaping, separately from wait4 block ops."""
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import run

IO_FIELDS = ["proc_io_syscr", "proc_io_syscw", "proc_io_rchar", "proc_io_wchar",
             "proc_io_read_bytes", "proc_io_write_bytes", "proc_io_cancelled_write_bytes"]


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc IO counters")
class MeterIOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="pb-meter-io-test-")
        cls.root = Path(cls.directory.name)
        cls.meter = cls.root / "meter"
        cls.denied = cls.root / "denied-meter"
        cls.child = cls.root / "child"
        source = Path(__file__).parent / "meter.c"
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(cls.meter), str(source)], check=True)
        denied_source = cls.root / "denied.c"
        denied_source.write_text('''#include <stdio.h>
#include <errno.h>
#include <string.h>
static FILE *denied_fopen(const char *path, const char *mode) {
    if (strncmp(path, "/proc/", 6) == 0) { errno = EACCES; return NULL; }
    return fopen(path, mode);
}
#define fopen denied_fopen
#undef _DEFAULT_SOURCE
#undef _POSIX_C_SOURCE
''' + '#include "' + str(source.resolve()) + '"\n')
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(cls.denied), str(denied_source)], check=True)
        child_source = cls.root / "child.c"
        child_source.write_text('''#include <unistd.h>
int main(void) {
    char byte;
    if (read(0, &byte, 1) != 1) return 2;
    for (unsigned i = 0; i < 17; i++) if (write(1, &byte, 1) != 1) return 3;
    if (write(2, &byte, 1) != 1) return 4;
    return 0;
}
''')
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(cls.child), str(child_source)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def probe_proc_io(self):
        """Independently check this host's access to an exec'd, unreaped child."""
        child = subprocess.Popen([str(self.child)], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            child.stdin.write(b"x")
            child.stdin.close()
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
            try:
                source = Path(f"/proc/{child.pid}/io").open()
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EPERM):
                    return "permission_denied"
                if error.errno in (errno.ENOENT, errno.ENOTDIR):
                    return "process_io_unavailable"
                raise
            try:
                with source:
                    contents = source.read()
            except OSError:
                # Some kernels permit open but deny reads of an exited child.
                # Accept this only when independently reproduced, not merely
                # because the collector reports a read error.
                return "process_io_read_error"
            counters = dict(line.split(":", 1) for line in contents.splitlines())
            for field in IO_FIELDS:
                self.assertGreaterEqual(int(counters[field.removeprefix("proc_io_")]), 0)
            return None
        finally:
            child.wait(timeout=3)

    def assert_proc_io(self, metrics, expected_reason):
        reason = metrics["proc_io_unavailable_reason"]
        diagnostic = f"independent /proc probe: {expected_reason!r}; collector reason: {reason!r}"
        self.assertEqual(metrics["proc_io_available"], expected_reason is None, diagnostic)
        self.assertEqual(reason, expected_reason, diagnostic)
        if expected_reason is None:
            self.assertEqual(metrics["proc_io_source"], "linux_proc_pid_io_before_wait4")
            for field in IO_FIELDS:
                self.assertIsInstance(metrics[field], int)
                self.assertGreaterEqual(metrics[field], 0)
            return True
        self.assertIn(reason, ("permission_denied", "process_io_unavailable",
                               "process_io_read_error"), diagnostic)
        self.assertIsNone(metrics["proc_io_source"])
        for field in IO_FIELDS:
            self.assertIsNone(metrics[field], diagnostic)
        print(f"Process IO counters unavailable ({diagnostic}); checking output and wait4 metrics",
              file=sys.stderr)
        return False

    def invoke(self, meter=None, command=None, input=b"x"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            result = subprocess.run([str(meter or self.meter), str(path), "--",
                                     *(command or [str(self.child)])], input=input,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
            return result, json.loads(path.read_text())

    def test_actual_child_write_syscalls_are_distinct_from_block_operations(self):
        result, metrics = self.invoke()
        self.assertEqual(result.returncode, 0)
        self.assertEqual((result.stdout, result.stderr), (b"x" * 17, b"x"))
        if self.assert_proc_io(metrics, self.probe_proc_io()):
            self.assertEqual(metrics["proc_io_syscw"], 18)
            self.assertEqual(metrics["proc_io_wchar"], 18)
            self.assertGreaterEqual(metrics["proc_io_syscr"], 1)
            self.assertGreaterEqual(metrics["proc_io_rchar"], 1)
        self.assertEqual(metrics["termination"], {"exit": 0})
        self.assertEqual(metrics["block_output_operations"], 0)
        self.assertEqual(metrics["resource_source"], "native_child_wait4")
        self.assertGreater(metrics["ended_monotonic_ns"], metrics["started_monotonic_ns"])

    def test_unavailable_proc_counters_are_null_not_zero_and_wait4_preserved(self):
        result, metrics = self.invoke(meter=self.denied)
        self.assertEqual(result.returncode, 0)
        self.assertEqual((result.stdout, result.stderr), (b"x" * 17, b"x"))
        self.assertFalse(self.assert_proc_io(metrics, "permission_denied"))
        self.assertFalse(metrics["proc_io_available"])
        self.assertEqual(metrics["proc_io_unavailable_reason"], "permission_denied")
        self.assertIsNone(metrics["proc_io_source"])
        for field in IO_FIELDS:
            self.assertIsNone(metrics[field])
        self.assertEqual(metrics["termination"], {"exit": 0})
        self.assertGreater(metrics["peak_rss_bytes"], 0)
        self.assertEqual(metrics["resource_source"], "native_child_wait4")

    def test_signal_and_numeric_exit_remain_distinct(self):
        for code, expected in [
            ("import os,signal; os.kill(os.getpid(),signal.SIGTERM)", {"signal": signal.SIGTERM}),
            ("raise SystemExit(143)", {"exit": 143}),
        ]:
            with self.subTest(expected=expected):
                result, metrics = self.invoke(command=[sys.executable, "-c", code])
                self.assertEqual(result.returncode, 0)
                self.assertEqual(metrics["termination"], expected)
                self.assert_proc_io(metrics, self.probe_proc_io())

    def test_timeout_preserves_signal_and_supervisor_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metrics, _, _ = run.measure([sys.executable, "-c", "import time; time.sleep(10)"],
                                        run.isolated_env(root), root, meter=self.meter, timeout=.1)
            self.assertEqual(metrics["failure"], "timeout")
            self.assertEqual(metrics["termination"], {"signal": signal.SIGKILL})
            self.assertEqual(metrics["resource_source"], "supervisor_wait4_fallback")
            self.assertFalse(metrics.get("proc_io_available", False))
            for field in IO_FIELDS:
                self.assertIsNone(metrics.get(field))
            self.assertLess(metrics["wall_ms"], 2000)

    def test_runner_carries_kernel_io_counters_from_native_meter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metrics, stdout, stderr = run.measure([sys.executable, "-c", "import os; os.write(1,b'abc')"],
                                        run.isolated_env(root), root, meter=self.meter)
            self.assertEqual((stdout, stderr), (b"abc", b""))
            self.assertEqual(metrics["termination"], {"exit": 0})
            self.assertEqual(metrics["resource_source"], "native_child_wait4")
            self.assertIsNone(metrics["failure"])
            if self.assert_proc_io(metrics, self.probe_proc_io()):
                self.assertGreaterEqual(metrics["proc_io_syscw"], 1)
                self.assertGreaterEqual(metrics["proc_io_wchar"], 3)

    def test_probe_distinguishes_denied_open_failed_read_and_malformed_counters(self):
        with mock.patch.object(Path, "open", side_effect=PermissionError(errno.EACCES, "denied")):
            self.assertEqual(self.probe_proc_io(), "permission_denied")
        source = mock.MagicMock()
        source.__enter__.return_value = source
        source.read.side_effect = OSError(errno.ESRCH, "exited process unreadable")
        with mock.patch.object(Path, "open", return_value=source):
            self.assertEqual(self.probe_proc_io(), "process_io_read_error")
        source.read.side_effect = None
        source.read.return_value = "syscw: invalid\n"
        with mock.patch.object(Path, "open", return_value=source):
            with self.assertRaises((KeyError, ValueError)):
                self.probe_proc_io()

    def test_capability_check_rejects_collector_errors_and_unexpected_absence(self):
        metrics = {"proc_io_available": False, "proc_io_source": None,
                   **{field: None for field in IO_FIELDS}}
        for reason, expected in [("process_io_fields_missing", "permission_denied"),
                                 ("process_io_read_error", "permission_denied"),
                                 ("permission_denied", None)]:
            with self.subTest(reason=reason, expected=expected):
                metrics["proc_io_unavailable_reason"] = reason
                with self.assertRaisesRegex(AssertionError, "collector reason"):
                    self.assert_proc_io(metrics, expected)
        metrics["proc_io_unavailable_reason"] = "permission_denied"
        metrics["proc_io_syscw"] = 0
        with self.assertRaises(AssertionError):
            self.assert_proc_io(metrics, "permission_denied")


if __name__ == "__main__":
    unittest.main()
