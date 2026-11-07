"""Tests for phase-one corpus safety, exact comparison and captured artifacts."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import phase1


class CorpusTests(unittest.TestCase):
    def test_every_reachable_node_alias_and_local_flag_is_covered(self):
        surface = phase1.load_surface()
        cases = phase1.corpus(surface)
        argv = [case["argv"] for case in cases]
        names = [case["name"] for case in cases]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(set(n) <= set("abcdefghijklmnopqrstuvwxyz0123456789-_") for n in names))
        for node in surface["nodes"]:
            path = node["path"]
            self.assertIn([*path, "--help"], argv)
            self.assertIn(["help", *path, "-o", "json"], argv)
            for alias in node["aliases"]:
                self.assertIn([*path[:-1], alias, "--help"], argv)
            for flag in node["flags"]:
                self.assertTrue(any(case["argv"][:len(path)] == path and
                                    any(a.startswith("--" + flag["name"] + "=") for a in case["argv"])
                                    for case in cases))
        self.assertEqual(52, len(surface["nodes"]))
        self.assertEqual(cases, phase1.corpus(surface))

    def test_benchmark_subset_is_fixed_offline_and_independent_of_candidate(self):
        cases = phase1.corpus()
        results = [{"exit_code": 1 if c["safety"] == "parser-invalid" else 0} for c in cases]
        subset = phase1.benchmark_suite(cases, results)
        self.assertEqual(13, len(subset))
        self.assertEqual([c["name"] for c in cases if c["benchmark"]], [c["name"] for c in subset])
        self.assertTrue(all(not c["profile"] and c["fixture"] is None and c["expected_requests"] == [] for c in subset))
        self.assertEqual(len(cases), len(phase1.benchmark_suite(cases, results, True)))

    def test_deferred_guards_are_separate_and_exclude_promoted_operations(self):
        guards = phase1.deferred_guards()
        self.assertEqual([], guards)
        self.assertTrue(all(not g["argv"][:1] in (["help"], ["agent"], ["version"]) for g in guards))
        self.assertFalse(any(g["argv"][:1] == ["profile"] for g in guards))
        self.assertFalse(any(g["canonical_path"][3:] in phase1.PHASE2_OPERATIONS for g in guards))
        self.assertFalse(any(g["canonical_path"][3:] in phase1.PHASE7_OPERATIONS for g in guards))
        self.assertFalse(any(g["canonical_path"] == "pb tail" for g in guards))
        self.assertFalse(any(g["argv"][:1] == ["completion"] for g in guards))
        corpus = phase1.corpus()
        self.assertTrue(any(c["argv"] == ["completion", "bash"] for c in corpus))
        self.assertTrue(any(c["argv"] == ["__completeNoDesc", "sql", "run", "--h"] for c in corpus))
        benchmark_names = {c["name"] for c in phase1.corpus() if c["benchmark"]}
        self.assertFalse(benchmark_names.intersection(g["name"] for g in guards))

    def test_reference_surface_drift_is_a_failure(self):
        surface = phase1.load_surface()
        root = {"command": "pb", "flags": [{"name": "output", "shorthand": "o"}]}
        self.assertTrue(phase1.surface_issues(root, surface))

    def test_missing_profile_cases_cover_reviewed_prerequisites_and_stay_out_of_benchmarks(self):
        surface = phase1.load_surface()
        cases = phase1.corpus(surface)
        preflight = [c for c in cases if c.get("preflight")]
        self.assertEqual(56, len(preflight))
        self.assertTrue(all(not c["benchmark"] and "--help" not in c["argv"] for c in preflight))
        for node in surface["nodes"]:
            if node.get("requires_profile"):
                matching = [c for c in preflight if c["argv"][:len(node["path"])] == node["path"]]
                self.assertTrue(matching)
        sql = next(c for c in preflight if c["argv"] == ["sql", "run", "-ojson"])
        expected = {"exit_code": 1, "stdout": b"", "stderr": json.dumps({"error": {
            "code": "NOT_FOUND", "message": "no profile configured. run: pb login", "retryable": False}}).encode()}
        self.assertEqual([], phase1.preflight_issues(sql, expected))
        self.assertTrue(phase1.preflight_issues(sql, {**expected, "stderr": b'{}'}))

    def test_unicode_scalar_sweep_is_complete_and_bounded(self):
        chunks = list(phase1.unicode_quote_chunks())
        self.assertEqual(45, len(chunks))
        self.assertEqual(0xA0, ord(chunks[0][0]))
        self.assertEqual(0x10FFFF, ord(chunks[-1][-1]))
        self.assertEqual(0x110000 - 0xA0 - 0x800, sum(map(len, chunks)))
        self.assertTrue(all(len(chunk.encode("utf-8")) < 128 * 1024 for chunk in chunks))
        # UTF-8 encoding above also rejects accidental surrogate codepoints.
        for left, right in zip(chunks, chunks[1:]):
            expected = ord(left[-1]) + 1
            if expected == 0xD800:
                expected = 0xE000
            self.assertEqual(expected, ord(right[0]))
        cases = phase1.corpus()
        sweep = [c for c in cases if c["group"] == "phase1-unicode"]
        self.assertEqual(45, len(sweep))
        self.assertTrue(all(not c["benchmark"] and c["argv"][:2] == ["version", "-o"] for c in sweep))
        self.assertIn(["version", "-o", "jſon"], [c["argv"] for c in cases])
        self.assertIn(["version", "-ojson", "-😀"], [c["argv"] for c in cases])


class ProcessTests(unittest.TestCase):
    def script(self, folder, body):
        path = Path(folder) / "candidate"
        path.write_text("#!" + sys.executable + "\n" + body)
        path.chmod(0o700)
        return path

    def test_isolated_environment_exact_bytes_and_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = self.script(folder, "import os,sys\nassert os.environ['PB_ANALYTICS']=='disable'\n"
                "assert os.environ['TERM']=='dumb'\nassert os.environ['PB_THEME']=='dark'\n"
                "assert os.environ['HOME'] == os.getcwd()\nassert not os.listdir(os.environ['XDG_CONFIG_HOME'])\n"
                "os.write(1,b'hello \\x00\\n')\nos.write(2,b'error\\n')\nsys.exit(7)\n")
            result = phase1.sample(executable, [])
        self.assertIsNone(result["failure"])
        self.assertEqual(7, result["exit_code"])
        self.assertEqual(b"hello \x00\n", result["stdout"])
        self.assertEqual(b"error\n", result["stderr"])
        changed = {**result, "stdout": b"hello\x00\n"}
        self.assertIn("byte 5", phase1.differences(result, changed)[0])
        self.assertTrue(phase1.differences(result, {**result, "exit_code": -7}))

    def test_timeout_and_output_limit_fail_even_for_matching_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            sleeper = self.script(folder, "import time\ntime.sleep(10)\n")
            start = time.monotonic()
            timeout = phase1.sample(sleeper, [], timeout=.1)
            self.assertLess(time.monotonic() - start, 2)
            self.assertEqual("timeout", timeout["failure"])
            flooder = self.script(folder, "import os\nwhile True: os.write(1,b'x'*65536)\n")
            flood = phase1.sample(flooder, [], timeout=5)
            self.assertEqual("output-limit", flood["failure"])
            self.assertLessEqual(len(flood["stdout"]) + len(flood["stderr"]), phase1.MAX_OUTPUT)
            self.assertLess(flood["wall_ms"], 3000)
        self.assertTrue(phase1.differences(timeout, timeout))
        self.assertTrue(phase1.differences(flood, flood))

    def test_writes_fail_even_when_both_binaries_write_identically(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = self.script(folder, "from pathlib import Path\nPath('config/created').write_text('effect')\n")
            result = phase1.sample(executable, [])
        self.assertEqual("filesystem-effects", result["failure"])
        self.assertIn("config/created", result["files_after"])
        self.assertTrue(phase1.differences(result, result))

    def test_directory_and_symlink_mutations_fail_the_sample(self):
        mutations = {"mkdir": "Path('config/pb').mkdir()",
                     "remove": "Path('config').rmdir()",
                     "chmod": "Path('config').chmod(0o777)",
                     "dangling-symlink": "Path('dangling').symlink_to('missing')",
                     "directory-symlink": "Path('linked-config').symlink_to('config', target_is_directory=True)",
                     "replace-directory": "Path('config').rmdir(); Path('config').symlink_to('cache', target_is_directory=True)"}
        with tempfile.TemporaryDirectory() as folder:
            for name, mutation in mutations.items():
                with self.subTest(name=name):
                    executable = self.script(folder, "from pathlib import Path\n" + mutation + "\n")
                    result = phase1.sample(executable, [])
                    self.assertEqual(0, result["exit_code"])
                    self.assertEqual("filesystem-effects", result["failure"])
                    self.assertTrue(phase1.differences(result, result))

    def test_snapshot_records_link_targets_without_following_or_opening_fifos(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            outside = Path(temporary) / "outside"
            outside.mkdir()
            (outside / "private").write_text("external content")
            link = root / "link"
            link.symlink_to(outside, target_is_directory=True)
            (root / "dangling").symlink_to("missing")
            os.mkfifo(root / "pipe")
            before = phase1.snapshot(root)
            self.assertEqual("directory", before["."]["type"])
            self.assertEqual({"type": "symlink", "mode": "0o777", "target": str(outside)}, before["link"])
            self.assertEqual("symlink", before["dangling"]["type"])
            self.assertEqual("fifo", before["pipe"]["type"])
            self.assertNotIn("link/private", before)
            (outside / "private").write_text("changed externally")
            self.assertEqual(before, phase1.snapshot(root))
            link.unlink()
            link.symlink_to("different-target")
            after = phase1.snapshot(root)
            self.assertNotEqual(before, after)
            self.assertEqual("different-target", after["link"]["target"])

    @unittest.skipUnless((phase1.HERE.parent / "bin/pb-go").exists(), "Go oracle not built")
    def test_reviewed_go_paths_preserve_directory_and_file_snapshot(self):
        oracle = phase1.HERE.parent / "bin/pb-go"
        for argv in ([], ["help", "sql", "run", "-ojson"], ["profile", "-ojson"],
                     ["promql", "tsdb", "--top=x", "--top"], ["version", "-ojson", "---foo"]):
            with self.subTest(argv=argv):
                result = phase1.sample(oracle, argv)
                self.assertIsNone(result["failure"])
                self.assertEqual(result["files_before"], result["files_after"])
                self.assertEqual("directory", result["files_before"]["config"]["type"])

    @unittest.skipUnless((phase1.HERE.parent / "bin/pb-go").exists(), "Go oracle not built")
    def test_unicode_sweep_arguments_fit_native_execution_and_output_limits(self):
        chunks = list(phase1.unicode_quote_chunks())
        oracle = phase1.HERE.parent / "bin/pb-go"
        for chunk in (chunks[0], chunks[-1]):
            result = phase1.sample(oracle, ["version", "-o", chunk])
            self.assertEqual(1, result["exit_code"])
            self.assertIsNone(result["failure"])
            self.assertEqual(b"", result["stdout"])
            self.assertLess(len(result["stderr"]), phase1.MAX_OUTPUT)

    def test_candidate_stub_guard_requires_explicit_failure_and_preserves_seed(self):
        guard = {"canonical_path": "pb tail", "structured": True}
        message = 'command "pb tail" is not implemented in this Rust phase'
        result = {"exit_code": 1, "stdout": b"", "stderr": json.dumps({"error": {
            "code": "COMMAND_FAILED", "message": message, "retryable": False}}).encode(), "failure": None}
        self.assertEqual([], phase1.guard_issues(guard, result))
        self.assertTrue(phase1.guard_issues(guard, {**result, "stderr": b'{}'}))
        self.assertTrue(phase1.guard_issues(guard, {**result, "exit_code": 0}))
        with tempfile.TemporaryDirectory() as folder:
            executable = self.script(folder, "from pathlib import Path\nimport os\n"
                "p=Path(os.environ['XDG_CONFIG_HOME'])/'pb/config.toml'\nassert p.is_file()\n")
            sample = phase1.sample(executable, [], seed_profile=True)
        self.assertIsNone(sample["failure"])
        self.assertEqual(sample["files_before"], sample["files_after"])

    @unittest.skipUnless((phase1.HERE.parent / "bin/pb-go").exists(), "Go oracle not built")
    def test_renamed_executable_still_uses_fixed_pb_name(self):
        oracle = phase1.HERE.parent / "bin/pb-go"
        with tempfile.TemporaryDirectory() as folder:
            renamed = Path(folder) / "renamed-candidate"
            renamed.symlink_to(oracle)
            for argv in (["--help"], ["help", "sql", "run", "-ojson"], ["--unknown-phase1"]):
                self.assertEqual([], phase1.differences(phase1.sample(oracle, argv), phase1.sample(renamed, argv)))


class ArtifactTests(unittest.TestCase):
    def test_old_capture_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = {"schema_version": phase1.SCHEMA_VERSION - 1, "complete": True, "passed": 0, "cases": []}
            phase1.write_json(root / "report.json", report)
            phase1.write_json(root / "corpus.json", [])
            self.assertFalse(phase1.valid_saved_report(report, []))
            with contextlib.redirect_stderr(io.StringIO()):
                status = phase1.main(["suite", "--baseline", str(root), "--out", str(root / "suite.json")])
                with patch.object(phase1, "corpus", return_value=[]):
                    compare_status = phase1.main(["compare", "--baseline", str(root), "--candidate", "irrelevant", "--out", str(root / "out")])
            self.assertEqual(2, status)
            self.assertEqual(2, compare_status)
            self.assertFalse((root / "suite.json").exists())

    def test_capture_replay_and_fixed_suite_report_a_candidate_mismatch(self):
        cases = [{"name": "p1-test-help", "group": "phase1-help", "argv": ["--help"],
                  "safety": "help", "benchmark": True},
                 {"name": "p1-test-agent", "group": "phase1-startup", "argv": ["agent"],
                  "safety": "offline-action", "benchmark": True}]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            reference = root / "reference-bin"
            candidate = root / "candidate-bin"
            source = "#!" + sys.executable + "\nimport sys\n" + \
                "if sys.argv[1:]==['--version']: print('pb version 0.0.0-harness')\n" + \
                "else: print('contract')\n"
            reference.write_text(source)
            candidate.write_text(source.replace("else: print('contract')", "elif sys.argv[1:]==['agent']: print('changed')\nelse: print('contract')"))
            reference.chmod(0o700)
            candidate.chmod(0o700)
            capture = root / "capture"
            comparison = root / "comparison"
            with patch.object(phase1, "corpus", return_value=cases), patch.object(phase1, "deferred_guards", return_value=[]), \
                    contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, phase1.main(["capture", "--reference", str(reference), "--out", str(capture)]))
                self.assertEqual(1, phase1.main(["compare", "--baseline", str(capture), "--candidate", str(candidate), "--out", str(comparison)]))
            report = json.loads((comparison / "report.json").read_text())
            self.assertTrue(report["complete"])
            self.assertEqual(1, report["passed"])
            self.assertTrue(report["cases"][1]["issues"])
            exported = json.loads((comparison / "benchmark-suite.json").read_text())
            self.assertEqual(2, len(exported))
            self.assertEqual([c["name"] for c in cases], [c["name"] for c in exported])
            self.assertEqual(phase1.digest(phase1.canonical(exported)), report["benchmark_suite_sha256"])

    def test_transcript_tampering_and_metadata_tampering_are_detected(self):
        result = {"exit_code": 0, "stdout": b"ok\n", "stderr": b"", "failure": None,
                  "wall_ms": 1, "files_before": {}, "files_after": {}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture"
            phase1.store_result(path, result)
            expected = phase1.public_result(result)
            self.assertEqual(result, {k: v for k, v in phase1.read_result(path, expected).items() if k in result})
            (path / "stdout.bin").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "corrupt baseline transcript"):
                phase1.read_result(path, expected)
            (path / "stdout.bin").write_bytes(result["stdout"])
            metadata = json.loads((path / "result.json").read_text())
            metadata["exit_code"] = 1
            phase1.write_json(path / "result.json", metadata)
            with self.assertRaisesRegex(ValueError, "differs from report"):
                phase1.read_result(path, expected)

    def test_suite_export_rejects_incomplete_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            phase1.write_json(root / "report.json", {"complete": False, "passed": 0})
            phase1.write_json(root / "corpus.json", [])
            with contextlib.redirect_stderr(io.StringIO()):
                status = phase1.main(["suite", "--baseline", str(root), "--out", str(root / "suite.json")])
            self.assertEqual(2, status)
            self.assertFalse((root / "suite.json").exists())


if __name__ == "__main__":
    unittest.main()
