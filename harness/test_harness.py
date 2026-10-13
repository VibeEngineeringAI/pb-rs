import os
import argparse
import contextlib
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import run


class ProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meter_directory = tempfile.TemporaryDirectory(prefix="pb-meter-test-")
        cls.meter = Path(cls.meter_directory.name) / "meter"
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(cls.meter),
                        str(run.HERE / "meter.c")], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.meter_directory.cleanup()

    def execute(self, code, **options):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options.setdefault("meter", self.meter)
            return run.measure([sys.executable, "-c", code], run.isolated_env(root), root, **options)

    def test_drains_both_pipes_and_measures_first_output(self):
        result, stdout, stderr = self.execute("import os; os.write(2,b'e'*100000); os.write(1,b'o'*100000)")
        self.assertEqual(stdout, b"o" * 100000)
        self.assertEqual(stderr, b"e" * 100000)
        self.assertEqual(result["termination"], {"exit": 0})
        self.assertLess(result["first_stderr_ms"], result["first_stdout_ms"])
        self.assertGreater(result["wall_ms"], result["first_stdout_ms"])
        self.assertGreater(result["cpu_ms"], 0)

    def test_file_sink_validates_identical_output_without_first_byte_timing(self):
        code = "import os; os.write(2,b'note'); os.write(1,b'[{\"sequence\":0},{\"sequence\":1}]')"
        pipe, stdout, stderr = self.execute(code, track_json_array=True)
        file, actual, errors = self.execute(code, stdout_sink="file", track_json_array=True)
        self.assertEqual((actual, errors), (stdout, stderr))
        self.assertEqual(file["stdout_raw_sha256"], pipe["stdout_raw_sha256"])
        self.assertEqual(file["json_array_elements"], 2)
        self.assertTrue(file["json_array_complete"])
        self.assertTrue(file["json_array_sequence_order_valid"])
        self.assertIsNone(file["first_stdout_ms"])
        self.assertIsNone(file["first_complete_element_ms"])
        self.assertFalse(file["first_complete_element_timing_enabled"])
        self.assertEqual(file["resource_source"], "native_child_wait4")
        self.assertIsNone(file["failure"])

    def test_file_sink_enforces_output_cap_on_fast_exit(self):
        result, stdout, stderr = self.execute("import os; os.write(1,b'x'*20000)",
                                              stdout_sink="file", max_output=1024)
        self.assertEqual(result["failure"], "output-limit")
        self.assertTrue(result["output_truncated"])
        self.assertLessEqual(len(stdout) + len(stderr), 1024)

    def test_file_sink_rejects_pipe_only_controls(self):
        for options in ({"read_delay_ms": 1}, {"signal_after_lines": 1},
                        {"close_stdout_after_lines": 1}, {"track_records": True}):
            with self.assertRaises(ValueError):
                self.execute("pass", stdout_sink="file", **options)

    def test_file_sink_stops_continuous_writer_and_bounds_capture(self):
        result, stdout, stderr = self.execute("import os;\nwhile True: os.write(1,b'x'*65536)",
                                              stdout_sink="file", max_output=100000, timeout=2)
        self.assertEqual(result["failure"], "output-limit")
        self.assertLessEqual(len(stdout) + len(stderr), 100000)
        self.assertLess(result["wall_ms"], 2000)

    def test_diagnostic_environment_is_narrowly_allowlisted(self):
        with self.assertRaises(ValueError):
            run.sample(Path(sys.executable), {}, None, None, 2,
                       diagnostic_env={"LD_PRELOAD": "/unsafe"})

    def test_signal_is_not_numeric_exit(self):
        # Python's handler turns SIGINT into KeyboardInterrupt; reset it first.
        killed, _, _ = self.execute("import os,signal; signal.signal(signal.SIGINT,signal.SIG_DFL); os.kill(os.getpid(),signal.SIGINT)")
        exited, _, _ = self.execute("raise SystemExit(130)")
        self.assertEqual(killed["termination"], {"signal": signal.SIGINT})
        self.assertEqual(exited["termination"], {"exit": 130})

    def test_timeout_is_failure_even_if_both_binaries_hang(self):
        result, _, _ = self.execute("import time; time.sleep(10)", timeout=.1)
        self.assertEqual(result["failure"], "timeout")
        self.assertEqual(result["termination"], {"signal": signal.SIGKILL})
        self.assertLess(result["wall_ms"], 2000)

    def test_output_limit(self):
        result, stdout, stderr = self.execute("import os;\nwhile True: os.write(1,b'x'*65536)", max_output=100000)
        self.assertEqual(result["failure"], "output-limit")
        self.assertEqual(len(stdout) + len(stderr), 100000)
        self.assertTrue(result["output_truncated"])
        self.assertEqual(result["stdout_raw_sha256"], run.digest(stdout))

    def test_native_block_operations_are_counts_and_resources_belong_to_child(self):
        result, _, _ = self.execute("import os; f=open('io-body','wb'); f.write(b'x'*1048576); f.flush(); os.fsync(f.fileno()); f.close()")
        self.assertEqual(result["resource_source"], "native_child_wait4")
        self.assertEqual(result["block_io_source"], "native_child_wait4")
        self.assertIsInstance(result["block_input_operations"], int)
        self.assertIsInstance(result["block_output_operations"], int)
        self.assertGreaterEqual(result["block_input_operations"], 0)
        self.assertGreaterEqual(result["block_output_operations"], 0)
        # Memory-backed temporary files can legitimately cause zero block I/O.

    def test_missing_native_metrics_labels_supervisor_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "meter"
            wrapper.write_text(f"#!{sys.executable}\nimport os,sys\nos.execv(sys.argv[3],sys.argv[3:])\n")
            wrapper.chmod(0o700)
            result, _, _ = run.measure([sys.executable, "-c", "pass"], run.isolated_env(root), root, meter=wrapper)
        self.assertEqual(result["resource_source"], "supervisor_wait4_fallback")
        self.assertEqual(result["block_io_source"], "supervisor_wait4_fallback")
        self.assertIn("meter", result["failure"])

    def test_empty_metrics_from_failed_supervisor_preserves_capture_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "meter"
            wrapper.write_text(
                f"#!{sys.executable}\n"
                "import pathlib,subprocess,sys\n"
                "subprocess.run(sys.argv[3:], check=True)\n"
                "pathlib.Path(sys.argv[1]).write_text('')\n"
                "sys.stderr.write('metrics close: Disk quota exceeded\\n')\n"
                "sys.exit(1)\n"
            )
            wrapper.chmod(0o700)
            result, stdout, stderr = run.measure(
                [sys.executable, "-c", "print('retained output')"],
                run.isolated_env(root), root, meter=wrapper,
            )
        self.assertEqual(stdout, b"retained output\n")
        self.assertEqual(stderr, b"metrics close: Disk quota exceeded\n")
        self.assertEqual(result["termination"], {"exit": 1})
        self.assertIn("meter resource measurements unreadable or invalid", result["failure"])
        self.assertEqual(result["resource_source"], "supervisor_wait4_fallback")
        self.assertEqual(result["block_io_source"], "supervisor_wait4_fallback")
        self.assertFalse(result["proc_io_available"])
        self.assertEqual(result["proc_io_unavailable_reason"], "native_meter_unavailable")
        self.assertNotIn("supervisor_startup_ms", result)

    def test_json_element_timing_uses_valid_value_not_formatting_lines(self):
        result, stdout, _ = self.execute("import os,time; os.write(1,b'[\\n'); time.sleep(.05); os.write(1,b' {\\n  \"sequence\": 0\\n }\\n]')", track_json_array=True)
        self.assertEqual(result["json_array_elements"], 1)
        self.assertTrue(result["json_array_complete"])
        self.assertIsNone(result["first_record_ms"])
        self.assertGreater(result["first_complete_element_ms"], result["first_stdout_ms"])
        self.assertEqual(result["stdout_raw_sha256"], run.digest(stdout))

    def test_valid_first_element_releases_causal_local_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ack = root / "ack"
            code = "import os,time; os.write(1,b'[ {\"sequence\":0}');\nwhile not os.path.exists('ack'): time.sleep(.001)\nos.write(1,b', {\"sequence\":1}]')"
            result, stdout, _ = run.measure([sys.executable, "-c", code], run.isolated_env(root), root,
                meter=self.meter, timeout=2, track_json_array=True,
                on_first_complete_element=lambda: ack.touch())
            self.assertTrue(ack.exists())
        self.assertIsNone(result["failure"])
        self.assertEqual(result["json_array_elements"], 2)
        self.assertTrue(result["json_array_sequence_order_valid"])
        self.assertEqual(json.loads(stdout), [{"sequence": 0}, {"sequence": 1}])

    def test_disabled_element_timing_keeps_post_exit_count_and_order_validation(self):
        calls = []
        tracker_type = run.JSONElementTracker

        class Tracker(tracker_type):
            def feed(self, chunk, stop_after_first=False):
                calls.append(stop_after_first)
                return super().feed(chunk, stop_after_first=stop_after_first)

        case = {"argv": ["-c", "import os; os.write(1,b'[{\"sequence\":0},{\"sequence\":1}]')"],
                "expected_exit": 0, "expected_requests": [], "json_array_rows": 2,
                "time_first_json_element": False}
        with mock.patch.object(run, "JSONElementTracker", Tracker):
            result = run.sample(Path(sys.executable), case, None, None, 2, meter=self.meter)
        self.assertEqual(calls, [False])  # Only post-exit validation inspected bytes.
        self.assertTrue(result["valid"])
        self.assertEqual(result["rows_observed"], 2)
        self.assertTrue(result["json_array_sequence_order_valid"])
        self.assertIsNone(result["first_complete_element_ms"])
        self.assertFalse(result["first_complete_element_timing_enabled"])

    def test_invalid_array_does_not_report_false_element(self):
        result, _, _ = self.execute("import os; os.write(1,b'[{\"sequence\":0,}]')", track_json_array=True)
        self.assertEqual(result["json_array_elements"], 0)
        self.assertIsNone(result["first_complete_element_ms"])
        self.assertIsNotNone(result["json_array_error"])

    def test_case_output_cap_limits_are_explicit(self):
        for cap in [0, -1, (100 << 20) + 1, True, 10.5]:
            with self.assertRaises(ValueError):
                self.execute("pass", max_output=cap)

    def test_sample_counts_actual_rows_and_rejects_wrong_count_or_order(self):
        for body, expected_rows, valid in [
            ('[{"sequence":0},{"sequence":1}]', 2, True),
            ('[{"sequence":0},{"sequence":1}]', 3, False),
            ('[{"sequence":1},{"sequence":0}]', 2, False),
            ('[{"sequence":0},]', 1, False),
        ]:
            case = {"argv": ["-c", f"import os; os.write(1,{body.encode()!r})"],
                    "expected_exit": 0, "expected_requests": [], "json_array_rows": expected_rows}
            result = run.sample(Path(sys.executable), case, None, None, 2, meter=self.meter)
            self.assertEqual(result["valid"], valid, body)
            self.assertEqual(result["rows_observed"], expected_rows if valid else None, body)
            self.assertEqual(result["rows_per_second"] is not None, valid, body)

    def test_unsuitable_or_failed_sql_output_has_no_forced_array_validation(self):
        for body, expected_exit in [(b"human empty-body message\n", 0), (b"[", 1)]:
            case = {"argv": ["-c", f"import os; os.write(1,{body!r}); raise SystemExit({expected_exit})"],
                    "expected_exit": expected_exit, "expected_requests": [], "track_json_array": True}
            result = run.sample(Path(sys.executable), case, None, None, 2, meter=self.meter)
            self.assertTrue(result["valid"])
            self.assertIsNone(result["first_complete_element_ms"])
            self.assertIsNone(result["rows_per_second"])

    def test_fresh_rss_does_not_inherit_large_collector(self):
        # This catches fork+exec / cumulative RUSAGE_CHILDREN contamination.
        large_parent_allocation = bytearray(80 << 20)
        result, _, _ = self.execute("pass")
        self.assertLess(result["peak_rss_bytes"], len(large_parent_allocation))

    def test_held_descendant_pipe_is_bounded(self):
        result, _, _ = self.execute("import os,time;\nif os.fork()==0: time.sleep(10)", timeout=.2)
        self.assertIsNotNone(result["failure"])
        self.assertLess(result["wall_ms"], 2000)

    def test_slow_reader_does_not_change_bytes(self):
        result, stdout, _ = self.execute("import os; os.write(1,b'x'*150000)", read_delay_ms=10)
        self.assertIsNone(result["failure"])
        self.assertEqual(stdout, b"x" * 150000)

    def test_pipe_close_is_delivered_to_writer(self):
        result, stdout, _ = self.execute("import os,signal,time; signal.signal(signal.SIGPIPE,signal.SIG_DFL); os.write(1,b'one\\n'); time.sleep(.05); os.write(1,b'two\\n')", close_stdout_after_lines=1)
        self.assertEqual(stdout, b"one\n")
        self.assertEqual(result["termination"], {"signal": signal.SIGPIPE})


class JSONElementTests(unittest.TestCase):
    def test_every_byte_split_handles_nested_values_strings_unicode_and_numbers(self):
        values = [{"sequence": 0, "nested": [True, None, {"text": "café 日志 [} \\\""}]},
                  [1, 2], "text", 12.5e-3, False, None]
        wire = json.dumps(values, ensure_ascii=False).encode()
        tracker = run.JSONElementTracker()
        observations = []
        for byte in wire:
            observations.append(tracker.feed(bytes([byte])))
        tracker.finish()
        self.assertEqual(sum(observations), len(values))
        self.assertTrue(tracker.complete)
        self.assertIsNone(tracker.error)
        self.assertEqual(len(tracker.pending), 0)

    def test_exponents_and_literals_wait_for_delimiter(self):
        tracker = run.JSONElementTracker()
        for chunk in [b"[1", b"e", b"-", b"2"]:
            self.assertEqual(tracker.feed(chunk), 0)
        self.assertEqual(tracker.feed(b",tru"), 1)
        self.assertEqual(tracker.feed(b"e"), 0)
        self.assertEqual(tracker.feed(b"]"), 1)
        self.assertTrue(tracker.complete)

    def test_empty_arrays_and_ignored_trailing_text(self):
        for value in [b"[]", b"  [ \n ] ignored", b"[] invalid \xff"]:
            tracker = run.JSONElementTracker()
            self.assertEqual(tracker.feed(value), 0)
            tracker.finish()
            self.assertTrue(tracker.complete)
            self.assertIsNone(tracker.error)

    def test_malformed_values_cannot_be_counted(self):
        for value in [b"[NaN]", b"[Infinity]", b"[01]", b"[1e+]", b"[tru]", b'["\\x"]',
                      b'[{"x":}]', b"[[}]]", b'["\xff"]', b"[", b"[1e", b"not an array"]:
            tracker = run.JSONElementTracker()
            tracker.feed(value)
            tracker.finish()
            self.assertEqual(tracker.elements, 0, value)
            self.assertIsNotNone(tracker.error, value)

    def test_valid_partial_rows_survive_later_errors(self):
        for value in [b'[{}, {"x":}]', b"[1 2]", b"[1,]", b"[1,"]:
            tracker = run.JSONElementTracker()
            tracker.feed(value)
            tracker.finish()
            self.assertEqual(tracker.elements, 1, value)
            self.assertIsNotNone(tracker.error, value)
            self.assertFalse(tracker.complete, value)

    def test_sequence_order_is_independent_of_configured_count(self):
        tracker = run.JSONElementTracker()
        tracker.feed(b'[{"sequence":0},{"sequence":2}]')
        self.assertEqual(tracker.elements, 2)
        self.assertFalse(tracker.sequence_order_valid)
        tracker = run.JSONElementTracker()
        tracker.feed(b'[{"sequence":0},{"sequence":true}]')
        self.assertFalse(tracker.sequence_order_valid)


class ContractTests(unittest.TestCase):
    def test_config_compares_semantics_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run.seed_config(root, "http://127.0.0.1:123", "api-key")
            replacements = {"http://127.0.0.1:123": "<FIXTURE>"}
            before = run.config_snapshot(root, replacements)
            path = root / "config/pb/config.toml"
            path.write_text(path.read_text() + "\n# formatting is not profile semantics\n")
            self.assertEqual(before, run.config_snapshot(root, replacements))
            path.chmod(0o644)
            self.assertNotEqual(before, run.config_snapshot(root, replacements))

    def test_only_explicit_values_normalized(self):
        self.assertEqual(run.normalize(b"port=123 timestamp=456 /tmp/run", {"/tmp/run": "<HOME>"}),
                         b"port=123 timestamp=456 <HOME>")

    def test_request_json_order_is_semantic_but_repeat_order_is_preserved(self):
        a = {"path": "/x?match=a&match=b&step=1", "body": '{"a":1,"b":2}', "time_unix_ns": 1}
        b = {"path": "/x?step=1&match=a&match=b", "body": '{"b":2,"a":1}', "time_unix_ns": 2}
        self.assertEqual(run.request_contract([a]), run.request_contract([b]))
        b["path"] = "/x?match=b&match=a&step=1"
        self.assertNotEqual(run.request_contract([a]), run.request_contract([b]))

    def test_logical_http_payload_counts_utf8_bytes_and_excludes_flight_tickets(self):
        trace = [{"protocol": "http", "body": "café"}, {"protocol": "http", "body": "中文"},
                 {"protocol": "flight", "body": "not a wire-encoded HTTP body"}]
        self.assertEqual(run.http_request_body_bytes(trace), 11)
        self.assertEqual(run.http_request_body_bytes([]), 0)
        self.assertIsNone(run.http_request_body_bytes([{"protocol": "http", "body": None}]))
        self.assertIsNone(run.http_request_body_bytes([{"body": "unknown protocol"}]))

    def make_sample(self, wall=1):
        contract = {"stdout_sha256": "a", "stderr_sha256": "b", "termination": {"exit": 0},
                    "requests": [], "config_before": {}, "config_after": {}}
        return {"valid": True, "wall_ms": wall, "contract": contract,
                "contract_sha256": run.digest(run.canonical(contract))}

    def test_bad_output_cannot_win_benchmark(self):
        a, b = self.make_sample(100), self.make_sample(1)
        b["contract"]["stdout_sha256"] = "changed"
        comparison = run.compare_samples([a], [b])
        self.assertFalse(comparison["compatible"])
        self.assertNotIn("ratio", comparison)

    def test_invalid_baseline_and_nondeterminism_fail(self):
        a, b = self.make_sample(), self.make_sample()
        a["valid"] = False
        self.assertFalse(run.compare_samples([a], [b])["compatible"])
        a["valid"] = True
        b["contract"]["termination"] = {"exit": 1}
        self.assertFalse(run.compare_samples([a, b], [a])["compatible"])

    def test_known_regression_interval(self):
        comparison = run.compare_samples([self.make_sample(10)] * 15, [self.make_sample(20)] * 15)
        self.assertTrue(comparison["compatible"])
        self.assertEqual(comparison["ratio"], 2)
        self.assertEqual(comparison["ratio_ci95"], [2, 2])

    def test_zero_baseline_is_available_with_absolute_comparison(self):
        for candidate, delta in [(0, 0), (3, 3)]:
            comparison = run.compare_samples([self.make_sample(0)] * 15,
                                             [self.make_sample(candidate)] * 15)
            self.assertTrue(comparison["metric_available"])
            self.assertEqual(comparison["reference_median"], 0)
            self.assertEqual(comparison["candidate_median"], candidate)
            self.assertEqual(comparison["reference_n"], 15)
            self.assertEqual(comparison["candidate_n"], 15)
            self.assertEqual(comparison["median_delta"], delta)
            self.assertEqual(comparison["delta_ci95"], [delta, delta])
            self.assertEqual(comparison["ratio_status"], "undefined_zero_reference")
            self.assertNotIn("ratio", comparison)

    def test_missing_metric_is_distinct_from_zero(self):
        comparison = run.compare_samples([self.make_sample(0)], [self.make_sample(0)],
                                         metric="block_input_operations")
        self.assertFalse(comparison["metric_available"])
        self.assertEqual(comparison["ratio_status"], "unavailable")
        self.assertIsNone(comparison["reference_median"])
        self.assertEqual(comparison["reference_n"], 0)

    def test_percentiles_and_missing_values(self):
        self.assertEqual(run.percentile([10, 20, 30], .5), 20)
        summary = run.summarize([self.make_sample(10), self.make_sample(20)])
        self.assertEqual(summary["metrics"]["wall_ms"]["median"], 15)
        self.assertNotIn("peak_rss_bytes", summary["metrics"])


class WorkloadTests(unittest.TestCase):
    def test_exception_workloads_disable_timing_without_disabling_row_validation(self):
        import phase3_workloads

        workloads = {case["name"]: case for case in phase3_workloads.suite()}
        for name in ["p3-single-8MiB-item", "p3-leading-1MiB-whitespace"]:
            case = workloads[name]
            self.assertFalse(case["time_first_json_element"])
            self.assertEqual(case["json_array_rows"], 1)
            self.assertTrue(case.get("track_json_array", True))
        self.assertTrue(workloads["p3-100000-json"].get("time_first_json_element", True))
        self.assertEqual(workloads["p3-100000-json"]["json_array_rows"], 100000)


class SavedReportTests(unittest.TestCase):
    def reports(self, ref_wall=10, cand_wall=20):
        def report(wall):
            return {"schema_version": run.SCHEMA_VERSION, "complete": True,
                    "host": {key: "same" for key in ("system", "release", "machine", "cpu", "cpu_count", "affinity", "governor_cpu0", "python")},
                    "binaries": {"cli": {}}, "reference": "cli", "fixture": None,
                    "meter": {"sha256": "meter"}, "method": {"runner_sha256": "runner"},
                    "cases": {"case": {"spec_sha256": "scenario", "runs": {"cli": [ContractTests().make_sample(wall) for _ in range(15)]}}}}
        return report(ref_wall), report(cand_wall)

    def compare(self, reference, candidate, **options):
        with tempfile.TemporaryDirectory() as directory:
            ref, cand = Path(directory) / "ref.json", Path(directory) / "cand.json"
            ref.write_text(json.dumps(reference))
            cand.write_text(json.dumps(candidate))
            args = argparse.Namespace(reference=str(ref), candidate=str(cand), reference_binary=None,
                                      candidate_binary=None, metric="wall_ms", max_regression_percent=10,
                                      min_regression=1, out=None)
            for key, value in options.items():
                setattr(args, key, value)
            with contextlib.redirect_stdout(io.StringIO()):
                return run.compare(args)

    def test_regression_gate_and_no_regression(self):
        self.assertEqual(self.compare(*self.reports()), 3)
        self.assertEqual(self.compare(*self.reports(10, 10)), 0)

    def test_zero_io_baseline_gate_uses_absolute_delta(self):
        for reference_value, candidate_value, expected in [(0, 0, 0), (0, 1, 0), (0, 3, 3)]:
            reference, candidate = self.reports()
            for report, value in [(reference, reference_value), (candidate, candidate_value)]:
                for sample in report["cases"]["case"]["runs"]["cli"]:
                    sample["block_input_operations"] = value
            self.assertEqual(self.compare(reference, candidate, metric="block_input_operations"), expected)

    def test_gate_requires_enough_values_for_selected_metric(self):
        reference, candidate = self.reports()
        for report in [reference, candidate]:
            report["cases"]["case"]["runs"]["cli"][0]["block_input_operations"] = 0
        with self.assertRaisesRegex(ValueError, "10 measured values"):
            self.compare(reference, candidate, metric="block_input_operations")

    def test_compatibility_failure_takes_precedence(self):
        ref, cand = self.reports()
        cand["cases"]["case"]["runs"]["cli"][0]["contract"]["stdout_sha256"] = "bad"
        self.assertEqual(self.compare(ref, cand), 1)

    def test_host_change_rejects_gate(self):
        ref, cand = self.reports()
        cand["host"]["cpu"] = "different"
        with self.assertRaisesRegex(ValueError, "host"):
            self.compare(ref, cand)

    def test_changed_stdout_sink_rejects_performance_gate(self):
        ref, cand = self.reports()
        ref["method"]["stdout_sink"] = "pipe"
        cand["method"]["stdout_sink"] = "file"
        with self.assertRaisesRegex(ValueError, "measurement settings"):
            self.compare(ref, cand)

    def test_incomplete_report_rejected(self):
        ref, cand = self.reports()
        cand["complete"] = False
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.compare(ref, cand)

    def test_missing_metric_and_changed_suite_rejected(self):
        ref, cand = self.reports()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.compare(ref, cand, metric="first_record_ms")
        cand["cases"]["case"]["spec_sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "scenario changed"):
            self.compare(ref, cand)

    def test_warmup_failure_invalidates_comparison(self):
        ref, cand = self.reports()
        cand["cases"]["case"]["warmup_issues"] = [{"issues": ["timeout"]}]
        self.assertEqual(self.compare(ref, cand), 1)


if __name__ == "__main__":
    unittest.main()
