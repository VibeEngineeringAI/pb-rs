#!/usr/bin/env python3
"""Kernel child IO is collected before reaping, separately from wait4 block ops."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

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
        self.assertTrue(metrics["proc_io_available"])
        self.assertIsNone(metrics["proc_io_unavailable_reason"])
        self.assertEqual(metrics["proc_io_source"], "linux_proc_pid_io_before_wait4")
        self.assertEqual(metrics["proc_io_syscw"], 18)
        self.assertEqual(metrics["proc_io_wchar"], 18)
        self.assertGreaterEqual(metrics["proc_io_syscr"], 1)
        self.assertGreaterEqual(metrics["proc_io_rchar"], 1)
        self.assertEqual(metrics["block_output_operations"], 0)
        self.assertEqual(metrics["resource_source"], "native_child_wait4")
        self.assertGreater(metrics["ended_monotonic_ns"], metrics["started_monotonic_ns"])
        for field in IO_FIELDS:
            self.assertIsInstance(metrics[field], int)
            self.assertGreaterEqual(metrics[field], 0)

    def test_unavailable_proc_counters_are_null_not_zero_and_wait4_preserved(self):
        result, metrics = self.invoke(meter=self.denied)
        self.assertEqual(result.returncode, 0)
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
                self.assertTrue(metrics["proc_io_available"])

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
            metrics, _, _ = run.measure([sys.executable, "-c", "import os; os.write(1,b'abc')"],
                                        run.isolated_env(root), root, meter=self.meter)
            self.assertTrue(metrics["proc_io_available"])
            self.assertGreaterEqual(metrics["proc_io_syscw"], 1)
            self.assertGreaterEqual(metrics["proc_io_wchar"], 3)
            for field in IO_FIELDS:
                self.assertIsInstance(metrics[field], int)


if __name__ == "__main__":
    unittest.main()
