import os
import argparse
import contextlib
import io
import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest

import run


class ProcessTests(unittest.TestCase):
    def execute(self, code, **options):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            return run.measure([sys.executable, "-c", code], run.isolated_env(root), root, **options)

    def test_drains_both_pipes_and_measures_first_output(self):
        result, stdout, stderr = self.execute("import os; os.write(2,b'e'*100000); os.write(1,b'o'*100000)")
        self.assertEqual(stdout, b"o" * 100000)
        self.assertEqual(stderr, b"e" * 100000)
        self.assertEqual(result["termination"], {"exit": 0})
        self.assertLess(result["first_stderr_ms"], result["first_stdout_ms"])
        self.assertGreater(result["wall_ms"], result["first_stdout_ms"])
        self.assertGreater(result["cpu_ms"], 0)

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
        result, _, _ = self.execute("import os;\nwhile True: os.write(1,b'x'*65536)", max_output=100000)
        self.assertEqual(result["failure"], "output-limit")

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

    def test_percentiles_and_missing_values(self):
        self.assertEqual(run.percentile([10, 20, 30], .5), 20)
        summary = run.summarize([self.make_sample(10), self.make_sample(20)])
        self.assertEqual(summary["metrics"]["wall_ms"]["median"], 15)
        self.assertNotIn("peak_rss_bytes", summary["metrics"])


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

    def test_compatibility_failure_takes_precedence(self):
        ref, cand = self.reports()
        cand["cases"]["case"]["runs"]["cli"][0]["contract"]["stdout_sha256"] = "bad"
        self.assertEqual(self.compare(ref, cand), 1)

    def test_host_change_rejects_gate(self):
        ref, cand = self.reports()
        cand["host"]["cpu"] = "different"
        with self.assertRaisesRegex(ValueError, "host"):
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
