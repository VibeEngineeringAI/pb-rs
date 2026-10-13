"""Diagnostic artifacts are evidence only after exact contract and scope checks."""
import copy
import json
import gzip
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import diagnose


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case = {"rows": 1, "fixture_response_body_bytes": 9}
        self.sample = {"stdout_bytes": 12}
        self.go = {"schema_version": 1, "complete": True, "language": "go", "mode": "timing",
                   "command_wall_ns": 100, "process_cpu_ns": 90,
                   "output": {"bytes_written": 12}, "stages": {"decode": {"calls": 1, "timed_calls": 1, "wall_ns": 80}}}
        self.rust = {"schema_version": 1, "complete": True, "implementation": "rust", "mode": "timing", "exit_code": 0,
                     "threads": [{"role": "sql_renderer", "span_wall_ns": 100,
                                  "counters": {"elements": 1, "input_consumed_bytes": 9, "write_completed_bytes": 12},
                                  "stages": [{"calls": 1, "timed_calls": 1, "wall_ns": 80}]}]}

        self.go.update(sample_every=1, profile_flush_ns=0, sampling_semantics="whole element", timing_semantics="inclusive", measurement_scope="main", profile_scope="main")
        self.go["input"] = {"logical_read_calls": 1, "bytes_read": 9}
        self.go["output"].update(logical_write_calls=1, bytes_requested=12, write_errors=0)
        self.go["stages"] = {name: {"calls": 1, "timed_calls": 1, "wall_ns": 80, "inclusive": True} for name in diagnose.GO_STAGES}
        self.rust["measurement"] = {key: "scope" for key in ("thread_cpu_clock", "stage_times", "thread_spans", "sidecar_io", "allocation_semantics", "writer_calls", "input_bytes", "sampling")}
        self.rust["measurement"].update(elapsed_wall_ns=10000, sample_every=1)
        thread = self.rust["threads"][0]
        thread.update(span_wall_ns=10000, span_cpu_ns=9000)
        thread["stages"] = [{"name": name, "calls": 1, "timed_calls": 1, "wall_ns": 80, "cpu_ns": 70} for name in diagnose.RUST_STAGES]
        for key in ("element_input_bytes", "serialized_bytes", "indented_bytes", "write_calls", "write_attempted_bytes", "flush_calls"):
            thread["counters"][key] = 0
        main = copy.deepcopy(thread)
        main["role"] = "main"
        main["counters"] = {key: 0 for key in thread["counters"]}
        self.rust["threads"].append(main)

    def write(self, language, value):
        path = self.root / ("go.json" if language == "go" else "pb-diagnostics-123.json")
        path.write_text(json.dumps(value))
        return path

    def read(self, language, mode="timing"):
        return diagnose.sidecar(self.root, language, mode, self.case, self.sample)

    def test_valid_artifacts_preserve_language_specific_scopes(self):
        for language, value in (("go", self.go), ("rust", self.rust)):
            self.write(language, value)
            self.assertEqual(self.read(language), value)

    def test_missing_empty_incomplete_wrong_language_or_mode_fail_closed(self):
        with self.assertRaises(ValueError):
            self.read("go")
        path = self.root / "go.json"
        path.write_text("")
        with self.assertRaises(ValueError):
            self.read("go")
        for changes in ({"complete": False}, {"schema_version": 2}, {"language": "rust"}, {"mode": "cpu"}):
            self.write("go", {**self.go, **changes})
            with self.assertRaises(ValueError):
                self.read("go")

    def test_symlink_and_multiple_artifacts_are_rejected(self):
        target = self.root / "target.json"
        target.write_text(json.dumps(self.go))
        (self.root / "go.json").symlink_to(target)
        with self.assertRaises(ValueError):
            self.read("go")
        self.write("rust", self.rust)
        (self.root / "pb-diagnostics-456.json").write_text(json.dumps(self.rust))
        with self.assertRaises(ValueError):
            self.read("rust")

    def test_counts_bytes_and_exclusive_stage_limits_are_checked(self):
        for key in ("elements", "input_consumed_bytes", "write_completed_bytes"):
            changed = copy.deepcopy(self.rust)
            changed["threads"][0]["counters"][key] += 1
            self.write("rust", changed)
            with self.assertRaises(ValueError):
                self.read("rust")
        changed = copy.deepcopy(self.rust)
        changed["threads"][0]["stages"][0]["wall_ns"] = 1_000_000
        self.write("rust", changed)
        with self.assertRaises(ValueError):
            self.read("rust")
        self.write("go", {**self.go, "output": {"bytes_written": 11}})
        with self.assertRaises(ValueError):
            self.read("go")

    def test_profiles_are_required_for_profile_modes(self):
        self.write("go", {**self.go, "mode": "cpu"})
        with self.assertRaises(ValueError):
            self.read("go", "cpu")
        path = self.root / "go-cpu.pprof"
        path.write_bytes(gzip.compress(b"fixture-profile-presence-only"))
        self.read("go", "cpu")
        path.write_bytes(b"")
        with self.assertRaises(ValueError):
            self.read("go", "cpu")

    def test_invalid_sampling_cannot_be_reported(self):
        self.go["stages"]["decode"]["timed_calls"] = 2
        self.write("go", self.go)
        with self.assertRaises(ValueError):
            self.read("go")

    def test_controls_cannot_accidentally_enable_diagnostics(self):
        self.assertEqual(diagnose.diagnostic_env(self.root, "rust", "disabled"), {})
        self.assertEqual(diagnose.diagnostic_env(self.root, "rust", "timing-sampled")["PB_DIAGNOSTICS_SAMPLE_EVERY"], "64")
        variants = diagnose.variants({"name": "p3-single-8MiB-item"}, diagnose.MODES, ["pipe", "file"])
        self.assertNotIn(("rust", "cpu", "pipe"), variants)
        self.assertIn(("rust", "release", "file"), variants)
        self.assertIn(("rust", "disabled", "pipe"), variants)

    def test_text_twin_has_identical_input_and_different_format(self):
        json_case, text_case = diagnose.workload_cases(["p3-single-8MiB-item", "p3-single-8MiB-text"])
        self.assertEqual(json_case["fixture"], text_case["fixture"])
        self.assertIn("text", text_case["argv"])
        with self.assertRaises(ValueError):
            diagnose.workload_cases(["not-a-case"])

    def test_contract_failure_invalidates_report_even_when_timing_is_faster(self):
        contract = {"stdout_sha256": "good", "stderr_sha256": "", "termination": {"exit": 0}, "requests": [], "config_before": {}, "config_after": {}}
        def sample(mode, value):
            return {"language": "go", "measurement_class": mode, "stdout_sink": "pipe", "warmup": False,
                    "valid": True, "wall_ms": 1, "contract": {**contract, "stdout_sha256": value}, "contract_sha256": value}
        report = {"cases": {"test": {"samples": [sample("release", "good"), sample("disabled", "bad")]}}}
        diagnose.aggregate(report)
        self.assertFalse(report["valid"])
        self.assertNotIn("percent_change", report["instrumentation_overhead"][0])

    def test_truncated_scopes_sampling_allocations_and_nonfinite_values_fail(self):
        for language, template in (("go", self.go), ("rust", self.rust)):
            changed = copy.deepcopy(template)
            if language == "go":
                changed["stages"] = {}
            else:
                changed["threads"][0]["stages"] = []
            self.write(language, changed)
            with self.assertRaises(ValueError):
                self.read(language)
            self.write(language, template)
            with self.assertRaises(ValueError):
                self.read(language, "timing-sampled")
            changed = copy.deepcopy(template)
            changed["mode"] = "allocations" if language == "go" else "timing_allocations"
            self.write(language, changed)
            with self.assertRaises(ValueError):
                self.read(language, "allocations")
        for value in ([], {**self.go, "command_wall_ns": float("nan")}, {**self.go, "process_cpu_ns": True}):
            self.write("go", value)
            with self.assertRaises(ValueError):
                self.read("go")

    def test_invalid_timeout_and_missing_pipe_control_are_rejected_before_work(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.assertRaisesRegex(ValueError, "finite and positive"):
                diagnose.execute(SimpleNamespace(timeout=timeout))
        with self.assertRaisesRegex(ValueError, "pipe control"):
            diagnose.execute(SimpleNamespace(timeout=1, sink=["file"]))

    def test_artifact_paths_cannot_escape_or_alias_original_output(self):
        for path in ("/tmp/absolute", "../escape", "samples/../../escape", ""):
            with self.assertRaises(ValueError):
                diagnose.artifact_dir(self.root, path)
        (self.root / "outside").symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            diagnose.artifact_dir(self.root, "outside/escape")
        self.assertEqual(diagnose.artifact_dir(self.root, "samples/0001"), self.root / "samples/0001")

    def test_profile_hash_mismatch_is_rejected_before_decoder_runs(self):
        folder = self.root / "samples/0001/diagnostics"
        folder.mkdir(parents=True)
        (folder / "go-cpu.pprof").write_bytes(gzip.compress(b"changed"))
        sample = {"language": "go", "measurement_class": "cpu", "valid": True, "issues": [],
                  "artifact": "samples/0001", "native_profile_identity": {"sha256": "old"}}
        report = {"cases": {"case": {"samples": [sample]}}}
        with mock.patch("diagnose.subprocess.run") as process:
            diagnose.decode_profiles(report, self.root, "go")
        process.assert_not_called()
        self.assertFalse(sample["valid"])
        self.assertIn("hash changed", sample["issues"][0])

    def test_reanalysis_preserves_unrelated_failures_and_original_report(self):
        original = self.root / "original"
        original.mkdir()
        report = {"schema_version": 1, "instrumentation_overhead": [], "identities_unchanged": True,
                  "cases": {"case": {"samples": [{"language": "go", "measurement_class": "cpu", "stdout_sink": "pipe",
                  "warmup": True, "artifact": "samples/0001", "valid": False,
                  "issues": ["timeout", "diagnostics: go tool pprof could not decode native profile"]}]}}}
        path = original / "report.json"
        path.write_text(json.dumps(report))
        before = path.read_bytes()
        with mock.patch("diagnose.subprocess.run") as process:
            result = diagnose.reanalyze(path, self.root / "reanalyzed", "go")
        self.assertEqual(result, 1)
        process.assert_not_called()
        self.assertEqual(path.read_bytes(), before)
        changed = json.loads((self.root / "reanalyzed/report.json").read_text())
        self.assertEqual(changed["cases"]["case"]["samples"][0]["issues"], ["timeout"])
