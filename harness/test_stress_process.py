#!/usr/bin/env python3
"""Local child-process checks; no Parseable account, servers, or CLI build."""
import base64
import csv
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from unittest import mock

import stress_process
from stress_process import (ArrayObjectScanner, OutputSummary, SAMPLE_BYTES, _proc_sample,
                            _stat_identity, capture)


class ScannerTests(unittest.TestCase):
    def test_invalid_stdout_sink_and_null_consumer_controls_rejected_before_launch(self):
        base = dict(argv=["unused"], env={}, cwd=".", artifact_dir="unused", meter="unused")
        with mock.patch.object(subprocess, "Popen") as launch:
            with self.assertRaisesRegex(ValueError, "stdout_sink must"):
                capture(**base, stdout_sink="file")
            for controls in ({"read_delay": .01}, {"pause_after_bytes": 0},
                             {"pause_seconds": .1}, {"stop_after_bytes": 0}):
                with self.subTest(controls=controls):
                    with self.assertRaisesRegex(ValueError, "require stdout_sink='pipe'"):
                        capture(**base, stdout_sink="null", **controls)
            launch.assert_not_called()

    def test_fragmented_nested_rows_escaped_strings_and_final_whitespace(self):
        raw = json.dumps([{"nested": [{"s": 'brace } ] quote " and slash \\'}]},
                          {"empty": []}]).encode() + b"\n"
        scanner = ArrayObjectScanner()
        for i, byte in enumerate(raw):
            scanner.feed(bytes([byte]), i / 100)
        result = scanner.result()
        self.assertEqual(result["completed_rows"], 2)
        self.assertTrue(result["complete"])
        self.assertIsNone(result["framing_error"])
        self.assertGreater(result["first_to_last_row_seconds"], 0)

    def test_framing_errors_and_non_json_text_are_distinct(self):
        for raw, rows, error in [
            (b"[]", 0, None),
            (b'[{}', 1, "truncated_array_or_object"),
            (b'[{"s":"unfinished', 0, "truncated_array_or_object"),
            (b"[{},]", 1, "expected_top_level_object"),
            (b"[{} {}]", 1, "expected_comma_or_array_close"),
            (b"[{}]x", 1, "trailing_data_after_array"),
            (b"[{]", 0, "unmatched_array_close_in_object"),
        ]:
            with self.subTest(raw=raw):
                scanner = ArrayObjectScanner()
                scanner.feed(raw, 1)
                self.assertEqual(scanner.result()["completed_rows"], rows)
                self.assertEqual(scanner.result()["framing_error"], error)
        for raw in (b"", b"ingested 100 rows\n", b'{"ok":true}'):
            scanner = ArrayObjectScanner()
            scanner.feed(raw, 1)
            self.assertFalse(scanner.result()["is_array"])
            self.assertIsNone(scanner.result()["framing_error"])
        scanner = ArrayObjectScanner()
        scanner.feed(b"[{not semantic JSON}]", 1)
        self.assertTrue(scanner.result()["complete"])
        self.assertIn("not_semantic_json", scanner.result()["validation"])

    def test_capture_summaries_and_scanner_do_not_retain_the_stream(self):
        summary = OutputSummary()
        scanner = ArrayObjectScanner()
        chunk = b'{"n":1},' * 256
        scanner.feed(b"[", 0)
        tracemalloc.start()
        try:
            for _ in range(300):
                summary.feed(chunk)
                scanner.feed(chunk, 1)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(scanner.completed_rows, 300 * 256)
        self.assertEqual(summary.bytes, len(chunk) * 300)
        self.assertLessEqual(len(summary.prefix), SAMPLE_BYTES)
        self.assertLessEqual(len(summary.suffix), SAMPLE_BYTES)
        self.assertLess(peak, 128 * 1024)

    def test_giant_string_is_scanned_without_retaining_or_iterating_payload_bytes(self):
        scanner = ArrayObjectScanner()
        scanner.feed(b'[{"payload":"', 0)
        chunk = b"x" * 65536
        tracemalloc.start()
        try:
            for _ in range(128):
                scanner.feed(chunk, 1)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        scanner.feed(b'"}]', 2)
        self.assertTrue(scanner.result()["complete"])
        self.assertEqual(scanner.completed_rows, 1)
        self.assertLess(peak, 128 * 1024)

    def test_missing_proc_telemetry_is_explicit_without_zero_counters(self):
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            sample = _proc_sample(12345)
        self.assertFalse(sample["proc_available"])
        self.assertEqual(sample["proc_unavailable_reason"], "PermissionError")
        self.assertNotIn("rss_bytes", sample)
        self.assertNotIn("cumulative_cpu_user_ms", sample)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc")
    def test_reused_or_changed_pid_identity_reports_no_counters(self):
        pid = os.getpid()
        comm, state, ppid, started, stat = _stat_identity(pid)
        self.assertTrue(_proc_sample(pid, ppid, started)["proc_available"])
        for meter_pid, starttime in ((ppid + 1, started), (ppid, started + 1)):
            sample = _proc_sample(pid, meter_pid, starttime)
            self.assertFalse(sample["proc_available"])
            self.assertEqual(sample["proc_unavailable_reason"], "child_pid_identity_changed")
            self.assertNotIn("cumulative_cpu_user_ms", sample)
        # Reaped and reused between the first and final identity reads.
        reused = (comm, state, ppid + 7, started + 9, stat)
        with mock.patch.object(stress_process, "_stat_identity",
                               side_effect=[(comm, state, ppid, started, stat), reused]):
            sample = _proc_sample(pid, ppid, started)
        self.assertFalse(sample["proc_available"])
        self.assertNotIn("rss_bytes", sample)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc")
    def test_preexec_child_is_flagged_by_meter_name(self):
        comm, _, ppid, started, _ = _stat_identity(os.getpid())
        self.assertTrue(_proc_sample(os.getpid(), ppid, started, comm)["child_preexec"])
        self.assertFalse(_proc_sample(os.getpid(), ppid, started, "pb-meter")["child_preexec"])


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux process groups and /proc")
class CaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="pb-stress-test-")
        cls.root = Path(cls.workspace.name)
        cls.meter = cls.root / "meter"
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-o",
                        str(cls.meter), str(Path(__file__).with_name("meter.c"))], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def invoke(self, code, **options):
        directory = self.root / self.id().split(".")[-1]
        directory.mkdir(exist_ok=True)
        return capture([sys.executable, "-c", code], env={"PATH": os.defpath},
                       cwd=self.root, artifact_dir=directory, meter=self.meter,
                       timeout=3, sample_interval=.01, **options)

    def assert_group_gone(self, result):
        self.assertFalse(result["process_group_remaining"])
        with self.assertRaises(ProcessLookupError):
            os.killpg(result["meter_pid"], 0)
        self.assertFalse(Path(f'/proc/{result["meter_pid"]}').exists())

    def test_counts_hashes_native_metrics_and_interval_timeline(self):
        rows = 12000
        stdout = b"[" + b'{"n":1},' * (rows - 1) + b'{"n":1}]'
        stderr = b"diagnostic\n" * 24000
        code = ("import os,time; time.sleep(.04); "
                f"os.write(2,b'diagnostic\\n'*24000); "
                f"os.write(1,b'['+b'{{\"n\":1}},'*({rows}-1)+b'{{\"n\":1}}]')")
        result = self.invoke(code, read_chunk=4096)
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["termination"], {"exit": 0})
        for name, expected in (("stdout", stdout), ("stderr", stderr)):
            observed = result[name]
            self.assertEqual(observed["bytes"], len(expected))
            self.assertEqual(observed["sha256"], hashlib.sha256(expected).hexdigest())
            self.assertEqual(base64.b64decode(observed["prefix_base64"]), expected[:SAMPLE_BYTES])
            self.assertEqual(base64.b64decode(observed["suffix_base64"]), expected[-SAMPLE_BYTES:])
        self.assertEqual(result["json_rows"]["completed_rows"], rows)
        self.assertTrue(result["json_rows"]["complete"])
        self.assertGreater(result["native_metrics"]["peak_rss_bytes"], 0)
        self.assertGreaterEqual(result["native_metrics"]["cpu_total_ms"], 0)
        self.assertGreaterEqual(result["collector_cpu_total_ms"], 0)
        self.assertGreater(result["proc_samples_available"], 0)
        with Path(result["timeline_csv"]).open() as source:
            timeline = list(csv.DictReader(source))
        self.assertEqual(sum(int(row["interval_rows"]) for row in timeline), rows)
        self.assertEqual(sum(int(row["interval_stdout_bytes"]) for row in timeline), len(stdout))
        cpu = [float(row["cumulative_cpu_user_ms"]) for row in timeline
               if row["cumulative_cpu_user_ms"]]
        self.assertEqual(cpu, sorted(cpu))
        self.assertTrue(all(float(row["interval_seconds"]) >= 0 for row in timeline))
        self.assert_group_gone(result)
        json.dumps(result)

    def test_throttle_and_pause_drain_stderr_without_deadlock(self):
        code = ("import os; os.write(2,b'e'*200000); "
                "os.write(1,b'['+b'{},'*20000+b'{}]')")
        result = self.invoke(code, read_chunk=1024, read_delay=.002,
                             pause_after_bytes=2048, pause_seconds=.08)
        self.assertEqual(result["termination"], {"exit": 0})
        self.assertEqual(result["stderr"]["bytes"], 200000)
        self.assertEqual(result["json_rows"]["completed_rows"], 20001)
        self.assertGreater(result["collector_wall_seconds"], .15)
        pauses = [e for e in result["events"] if e["event"] == "stdout_pause"]
        self.assertEqual(len(pauses), 1)
        self.assertEqual(pauses[0]["stdout_bytes"], 2048)
        self.assert_group_gone(result)

    def test_stdout_close_is_planned_and_stderr_remains_drained(self):
        code = """import os, signal
signal.signal(signal.SIGPIPE, signal.SIG_IGN)
try:
    while True:
        os.write(1, b'x' * 8192)
except BrokenPipeError:
    os.write(2, b'pipe closed\\n' * 20000)
"""
        result = self.invoke(code, stop_after_bytes=1234)
        self.assertEqual(result["outcome"], "planned_stdout_close")
        self.assertTrue(result["planned_interruption"])
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["stdout"]["bytes"], 1234)
        self.assertEqual(result["stderr"]["bytes"], len(b"pipe closed\n") * 20000)
        self.assertEqual(result["termination"], {"exit": 0})
        self.assertFalse(result["output_eof"]["stdout"])
        self.assertTrue(result["output_eof"]["stderr"])
        self.assert_group_gone(result)

    def test_timeout_reaps_meter_and_retains_responsive_child_native_metrics(self):
        directory = self.root / "timeout"
        result = capture([sys.executable, "-c", "import time; time.sleep(10)"],
                         env={"PATH": os.defpath}, cwd=self.root, artifact_dir=directory,
                         meter=self.meter, timeout=.08, sample_interval=.01)
        self.assertEqual(result["outcome"], "timeout")
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["planned_interruption"])
        self.assertEqual(result["termination"], {"signal": signal.SIGTERM})
        self.assertLess(result["collector_wall_seconds"], 1.5)
        self.assert_group_gone(result)

    def test_planned_signal_has_native_signal_status_without_timeout(self):
        code = "import os,signal,time; signal.signal(signal.SIGINT, signal.SIG_DFL); os.write(1,b'[{}'); time.sleep(10)"
        result = self.invoke(code, signal_after_seconds=.1)
        self.assertEqual(result["outcome"], "planned_signal")
        self.assertEqual(result["termination"], {"signal": signal.SIGINT})
        self.assertFalse(result["timed_out"])
        self.assertTrue(result["planned_interruption"])
        self.assertEqual(result["json_rows"]["framing_error"], "truncated_array_or_object")
        self.assert_group_gone(result)

    def test_forced_timeout_keeps_meter_alive_to_reap_and_collect_native_metrics(self):
        directory = self.root / "forced-timeout"
        result = capture([sys.executable, "-c",
                          "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(10)"],
                         env={"PATH": os.defpath}, cwd=self.root, artifact_dir=directory,
                         meter=self.meter, timeout=.1, sample_interval=.02)
        self.assertTrue(result["timed_out"])
        self.assertIsNotNone(result["native_metrics"])
        self.assertIsNone(result["native_metrics_unavailable_reason"])
        self.assertEqual(result["termination"], {"signal": signal.SIGKILL})
        self.assertIn("forced_child_kill", [e["event"] for e in result["events"]])
        self.assertLess(result["collector_wall_seconds"], 2)
        self.assert_group_gone(result)
        self.assertFalse(Path(f'/proc/{result["child_pid"]}').exists())

    def test_text_ingest_output_and_numeric_failure_do_not_become_framing_errors(self):
        result = self.invoke("print('ingested 999 rows'); raise SystemExit(7)")
        self.assertEqual(result["termination"], {"exit": 7})
        self.assertFalse(result["json_rows"]["is_array"])
        self.assertIsNone(result["json_rows"]["framing_error"])
        self.assert_group_gone(result)

    def test_missing_native_artifact_cannot_reuse_stale_measurement(self):
        directory = self.root / "missing-native"
        directory.mkdir()
        (directory / "native-metrics.json").write_text('{"exit_code":0}')
        fake_meter = directory / "fake-meter"
        fake_meter.write_text(f'#!{sys.executable}\nprint("no native metrics")\n')
        fake_meter.chmod(0o700)
        result = capture(["unused"], env={"PATH": os.defpath}, cwd=self.root,
                         artifact_dir=directory, meter=fake_meter, timeout=1)
        self.assertIsNone(result["native_metrics"])
        self.assertEqual(result["native_metrics_unavailable_reason"], "FileNotFoundError")
        self.assertIsNone(result["exit_code"])
        self.assertEqual(result["stdout"]["bytes"], len(b"no native metrics\n"))
        self.assert_group_gone(result)

    def test_default_sigpipe_child_dies_from_planned_stdout_close(self):
        code = ("import os,signal; signal.signal(signal.SIGPIPE, signal.SIG_DFL)\n"
                "while True: os.write(1, b'x' * 8192)")
        result = self.invoke(code, stop_after_bytes=1000)
        self.assertEqual(result["outcome"], "planned_stdout_close")
        self.assertEqual(result["termination"], {"signal": signal.SIGPIPE})
        self.assertTrue(result["stdout_close"]["workload_alive"])
        self.assertEqual(result["stdout_close"]["later_written_bytes"], "unknown")
        self.assert_group_gone(result)

    def test_stdout_close_after_clean_exit_is_not_reported_as_live_cut(self):
        result = self.invoke("import os; os.write(1, b'y' * 5000)", pause_after_bytes=50,
                             pause_seconds=.3, stop_after_bytes=100)
        self.assertEqual(result["outcome"], "planned_stdout_close")
        self.assertEqual(result["termination"], {"exit": 0})
        self.assertFalse(result["stdout_close"]["workload_alive"])
        self.assertEqual(result["stdout_close"]["discarded_queued_bytes"], 4900)
        self.assertEqual(result["stdout"]["bytes"], 100)
        self.assert_group_gone(result)

    def test_deferred_sigint_handler_completes_within_signal_grace(self):
        code = """import os, signal, time
stop = []
signal.signal(signal.SIGINT, lambda *_: stop.append(1))
os.write(1, b'[{}')
while not stop:
    time.sleep(.01)
time.sleep(1)
os.write(1, b']')
"""
        result = self.invoke(code, signal_after_seconds=.1)
        self.assertEqual(result["outcome"], "planned_signal")
        self.assertEqual(result["termination"], {"exit": 0})
        self.assertTrue(result["json_rows"]["complete"])
        self.assertEqual([e["event"] for e in result["events"]], ["planned_sigint"])
        self.assert_group_gone(result)

    def test_ignored_sigint_escalation_is_logged(self):
        code = ("import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
                "signal.signal(signal.SIGTERM, signal.SIG_DFL); time.sleep(10)")
        result = self.invoke(code, signal_after_seconds=.05, signal_grace_seconds=.1)
        self.assertEqual(result["outcome"], "planned_signal")
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["termination"], {"signal": signal.SIGTERM})
        events = [e["event"] for e in result["events"]]
        self.assertEqual(events[:2], ["planned_sigint", "planned_signal_term"])
        self.assert_group_gone(result)

    def test_signal_and_timeout_do_not_fire_after_exit_while_draining(self):
        stdout = b"[" + b"{}," * 19999 + b"{}]"
        self.assertLess(len(stdout), 65536)
        code = f"import os; os.write(1, b'[' + b'{{}},' * 19999 + b'{{}}]')"
        directory = self.root / "post-exit-drain"
        result = capture([sys.executable, "-c", code], env={"PATH": os.defpath},
                         cwd=self.root, artifact_dir=directory, meter=self.meter,
                         timeout=.2, sample_interval=.01, read_chunk=1024,
                         read_delay=.01, signal_after_seconds=.1)
        self.assertEqual(result["outcome"], "completed")
        self.assertFalse(result["timed_out"])
        self.assertFalse(result["planned_interruption"])
        self.assertEqual(result["stdout"]["bytes"], len(stdout))
        self.assertEqual(result["stdout"]["sha256"], hashlib.sha256(stdout).hexdigest())
        self.assertEqual(result["json_rows"]["completed_rows"], 20000)
        self.assertGreater(result["collector_wall_seconds"], .2)
        self.assertTrue(result["output_eof"]["stdout"])
        with Path(result["timeline_csv"]).open() as source:
            timeline = list(csv.DictReader(source))
        self.assertEqual(timeline[-1]["proc_unavailable_reason"], "child_reaped")
        self.assertEqual(sum(int(row["interval_stdout_bytes"]) for row in timeline), len(stdout))
        self.assert_group_gone(result)

    def test_post_exit_drain_is_bounded_and_labelled(self):
        code = "import os; os.write(1, b'z' * 60000)"
        result = self.invoke(code, read_chunk=1024, read_delay=.01, post_exit_drain_seconds=.1)
        self.assertEqual(result["outcome"], "post_exit_drain_limit")
        self.assertEqual(result["exit_code"], 0)
        self.assertLess(result["stdout"]["bytes"], 60000)
        self.assertFalse(result["output_eof"]["stdout"])
        self.assert_group_gone(result)

    def test_descendant_holding_inherited_pipes_is_classified_and_killed(self):
        code = ("import os,subprocess,sys; "
                "subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); "
                "os.write(1, b'[{}]')")
        result = self.invoke(code)
        self.assertEqual(result["outcome"], "descendant_held_pipes")
        self.assertEqual(result["post_exit"], "descendant_held_pipes")
        self.assertEqual(result["termination"], {"exit": 0})
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["stdout"]["bytes"], 4)
        self.assertTrue(result["json_rows"]["complete"])
        event = next(e for e in result["events"] if e["event"] == "descendant_held_pipes")
        self.assertTrue(event["process_group_alive"])
        self.assertLess(result["collector_wall_seconds"], 2)
        self.assert_group_gone(result)

    def test_empty_and_empty_array_output(self):
        empty = self.invoke("pass")
        self.assertEqual(empty["stdout"]["bytes"], 0)
        self.assertEqual(empty["stdout"]["sha256"], hashlib.sha256(b"").hexdigest())
        self.assertFalse(empty["json_rows"]["is_array"])
        self.assertFalse(empty["json_rows"]["complete"])
        self.assertIsNone(empty["json_rows"]["framing_error"])
        self.assertIsNone(empty["first_stdout_seconds"])
        array = self.invoke("print('[]')")
        self.assertTrue(array["json_rows"]["complete"])
        self.assertEqual(array["json_rows"]["completed_rows"], 0)
        self.assertIsNone(array["json_rows"]["first_row_seconds"])

    def test_hash_only_mode_reports_rows_as_unavailable(self):
        stdout = b"[" + b'{"n":1},' * 999 + b'{"n":1}]'
        code = "import os; os.write(1, b'[' + b'{\"n\":1},' * 999 + b'{\"n\":1}]')"
        result = self.invoke(code, scan_json=False)
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["stdout"]["sha256"], hashlib.sha256(stdout).hexdigest())
        rows = result["json_rows"]
        self.assertFalse(rows["available"])
        for key in ("completed_rows", "complete", "is_array", "first_row_seconds",
                    "first_to_last_row_seconds"):
            self.assertIsNone(rows[key])
        self.assertIsNotNone(result["first_stdout_seconds"])
        with Path(result["timeline_csv"]).open() as source:
            timeline = list(csv.DictReader(source))
        self.assertTrue(all(row["completed_rows"] == "" and row["interval_rows"] == ""
                            for row in timeline))
        self.assertEqual(sum(int(row["interval_stdout_bytes"]) for row in timeline), len(stdout))
        self.assertTrue(self.invoke("print('[]')")["json_rows"]["available"])
        json.dumps(result)

    def test_null_stdout_uses_direct_device_and_keeps_stderr_and_native_resources(self):
        code = ("import os,stat,time; assert stat.S_ISCHR(os.fstat(1).st_mode); "
                "assert os.fstat(1).st_rdev == os.stat(os.devnull).st_rdev; "
                "os.write(1,b'x'*131072); os.write(2,b'e'*200000); time.sleep(.04)")
        with mock.patch.object(stress_process, "ArrayObjectScanner") as scanner:
            result = self.invoke(code, stdout_sink="null")
        scanner.assert_not_called()
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["termination"], {"exit": 0})
        self.assertEqual(result["stdout_sink"], "null")
        self.assertEqual(result["stdout_sink_path"], os.devnull)
        self.assertFalse(result["stdout"]["available"])
        self.assertEqual(result["stdout"]["unavailable_reason"], "stdout_sink_null")
        self.assertIsNone(result["stdout"]["bytes"])
        self.assertIsNone(result["stdout"]["sha256"])
        self.assertEqual(result["stdout"]["prefix_base64"], "")
        self.assertEqual(result["stdout"]["suffix_base64"], "")
        self.assertIsNone(result["first_stdout_seconds"])
        self.assertIsNone(result["output_eof"]["stdout"])
        self.assertTrue(result["output_eof"]["stderr"])
        self.assertFalse(result["json_rows"]["available"])
        self.assertIsNone(result["json_rows"]["completed_rows"])
        self.assertEqual(result["json_rows"]["validation"], "unavailable_stdout_sink_null")
        self.assertFalse(result["scan_json"])
        self.assertTrue(result["scan_json_requested"])
        self.assertEqual(result["stderr"]["bytes"], 200000)
        self.assertEqual(result["stderr"]["sha256"], hashlib.sha256(b'e' * 200000).hexdigest())
        self.assertGreater(result["native_metrics"]["peak_rss_bytes"], 0)
        self.assertGreaterEqual(result["native_metrics"]["cpu_total_ms"], 0)
        if result["native_metrics"]["proc_io_available"]:
            self.assertGreaterEqual(result["native_metrics"]["proc_io_wchar"], 131072 + 200000)
        with Path(result["timeline_csv"]).open() as source:
            timeline = list(csv.DictReader(source))
        self.assertTrue(all(row["stdout_capture_available"] == "False" for row in timeline))
        self.assertTrue(all(row["stdout_bytes"] == "" and row["interval_stdout_bytes"] == ""
                            and row["completed_rows"] == "" for row in timeline))
        self.assertEqual(int(timeline[-1]["stderr_bytes"]), 200000)
        self.assert_group_gone(result)
        json.dumps(result)

    def test_expired_descendant_grace_does_not_busy_poll_buffered_paused_output(self):
        selector = stress_process.selectors.DefaultSelector()
        with mock.patch.object(stress_process.selectors, "DefaultSelector", return_value=selector), \
                mock.patch.object(selector, "select", wraps=selector.select) as waits:
            result = self.invoke("import os; os.write(1,b'a'*1000)", pause_after_bytes=1,
                                 pause_seconds=.25, descendant_grace_seconds=.01)
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["termination"], {"exit": 0})
        self.assertEqual(result["stdout"]["bytes"], 1000)
        self.assertTrue(result["output_eof"]["stdout"])
        self.assertTrue(any(event["event"] == "meter_exit_pipes_open" for event in result["events"]))
        # At a 10ms telemetry cadence a 250ms pause needs tens of waits.
        # The expired deadline regression performs thousands of zero-timeout
        # waits instead. Count calls, avoiding host-dependent CPU-time limits.
        self.assertLess(waits.call_count, 200)
        self.assert_group_gone(result)

    def test_unreaped_meter_is_reported_instead_of_losing_the_result(self):
        # Simulate a meter whose exit is never observed: the loop must still end
        # through bounded escalation and cleanup must report, not raise.
        with mock.patch.object(subprocess.Popen, "poll", return_value=None), \
                mock.patch.object(subprocess.Popen, "wait",
                                  side_effect=subprocess.TimeoutExpired("meter", 1)):
            result = capture([sys.executable, "-c", "import time; time.sleep(10)"],
                             env={"PATH": os.defpath}, cwd=self.root,
                             artifact_dir=self.root / "unreaped", meter=self.meter,
                             timeout=.05, sample_interval=.01)
        self.assertTrue(result["meter_unreaped"])
        self.assertTrue(result["timed_out"])
        self.assertIn("forced_kill", [e["event"] for e in result["events"]])
        os.waitpid(result["meter_pid"], 0)
        json.dumps(result)

if __name__ == "__main__":
    unittest.main()
