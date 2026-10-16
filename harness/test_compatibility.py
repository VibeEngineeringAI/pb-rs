"""The declared Rust extensions must never mask retained Go regressions."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import compatibility
import phase1
import run
import cases


class ExtensionProjectionTests(unittest.TestCase):
    def encoded(self, value):
        return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()

    def test_default_suite_classifies_exactly_the_three_extension_surfaces(self):
        tagged = {case["name"] for case in cases.suite() if case.get("compatibility_projection")}
        self.assertEqual({"root-help", "command-tree", "agent-catalog"}, tagged)

    def test_root_json_removes_only_extension_subtrees_and_preserves_bytes(self):
        original = self.encoded({"command": "pb", "description": "café <x>", "subcommands": [
            {"command": "pb agent"}, {"command": "pb sql", "flags": [{"name": "output"}]}]})
        original = original.replace(b"<x>", b"\\u003cx\\u003e")
        for additions in ([{"command": "pb ingest", "subcommands": [{"command": "pb ingest files"}]}],
                          [{"command": "pb observe"}],
                          [{"command": "pb ingest"}, {"command": "pb observe"}]):
            for position in range(3):
                value = json.loads(original)
                value["subcommands"][position:position] = additions
                candidate = self.encoded(value).replace(b"<x>", b"\\u003cx\\u003e")
                self.assertEqual(original, compatibility.project(candidate))
                self.assertNotEqual(original, compatibility.project(candidate.replace("café".encode(), b"changed")))
                self.assertNotEqual(original, compatibility.project(candidate.replace(b'"name": "output"', b'"name": "out"')))
        changed = self.encoded({"command": "pb", "subcommands": [{"command": "pb future"}]})
        self.assertEqual(changed, compatibility.project(changed))

    def test_catalog_keeps_legacy_entries_metadata_and_json_formatting_exact(self):
        reference = {"catalog": "cli-declared read-only commands", "schema_version": "1",
                     "contract": {"special_cases": ["legacy"]}, "commands": [{"command": "pb status"}]}
        candidate = json.loads(json.dumps(reference))
        candidate["commands"].append({"command": compatibility.OBSERVE_CATALOG_COMMAND})
        candidate["contract"]["special_cases"].append(compatibility.OBSERVE_NOTE)
        self.assertEqual(self.encoded(reference), compatibility.project(self.encoded(candidate)))
        for changed in (b'"schema_version": "2"', b'"schema_version" : "1"'):
            self.assertNotEqual(self.encoded(reference), compatibility.project(
                self.encoded(candidate).replace(b'"schema_version": "1"', changed)))
        candidate["commands"].append({"command": "pb ingest files"})
        self.assertIn(b"pb ingest files", compatibility.project(self.encoded(candidate)))

    def test_help_completion_and_catalog_remove_only_exact_known_rows(self):
        reference = b"Usage:\n  pb [command] [flags]\nAvailable Commands:\n  sql       SQL\n"
        rows = b"  ingest    Ingest events into Parseable\n  observe   Read session events with an explicit cursor receipt\n"
        self.assertEqual(reference, compatibility.project(reference + rows))
        colored = reference + b"  \x1b[1mingest\x1b[0m    \x1b[2mIngest events into Parseable\x1b[0m\n"
        self.assertEqual(reference, compatibility.project(colored))
        self.assertNotEqual(reference, compatibility.project(reference + rows.replace(b"Parseable", b"other")))
        self.assertEqual(rows, compatibility.project(rows))  # Not root help.
        usage = b"Error: unknown flag: --bad\nUsage:\n  pb [flags]\n  pb [command]\n\nAvailable Commands:\n  sql         SQL\n"
        usage_rows = b"  ingest      Ingest events into Parseable\n  observe     Read session events with an explicit cursor receipt\n"
        self.assertEqual(usage, compatibility.project(usage + usage_rows))
        self.assertNotEqual(usage, compatibility.project(usage + usage_rows.replace(b"ingest      ", b"ingest     ")))
        for described in (False, True):
            row = b"ingest\tIngest events into Parseable\n" if described else b"ingest\n"
            self.assertEqual(b"sql\n:4\n", compatibility.project(b"sql\n" + row + b":4\n", ["__complete", ""]))
            for argv in ([], ["__complete", "sql", ""], ["__completeNoDesc", "sql", "run", ""]):
                # A misplaced root command under a legacy leaf is a regression.
                output = b"sql\n" + row + b":4\n"
                self.assertEqual(output, compatibility.project(output, argv))
        text = b"Read-only command catalog for agents\nauthorization\n- pb status: Status\n"
        extension = f"- {compatibility.OBSERVE_CATALOG_COMMAND}: {compatibility.OBSERVE_CATALOG_DESCRIPTION}\n".encode()
        self.assertEqual(text, compatibility.project(text + extension))

    def test_projection_is_opt_in_and_does_not_mask_status_or_side_effects(self):
        reference = {"exit_code": 0, "stdout": b"sql\n:4\n", "stderr": b"", "failure": None}
        candidate = {**reference, "stdout": b"ingest\nsql\n:4\n"}
        self.assertTrue(phase1.differences(reference, candidate))
        self.assertEqual([], phase1.differences(reference, candidate, project_extensions=True, argv=["__complete", ""]))
        self.assertTrue(phase1.differences(reference, {**candidate, "exit_code": 1}, project_extensions=True, argv=["__complete", ""]))
        self.assertTrue(phase1.differences(reference, {**candidate, "failure": "filesystem-effects"}, project_extensions=True, argv=["__complete", ""]))

    def test_benchmark_comparison_rejects_different_projection_policies(self):
        contract = dict.fromkeys(("stdout_sha256", "stderr_sha256", "termination", "requests", "config_before", "config_after"), "same")
        reference = {"valid": True, "contract": contract, "wall_ms": 1}
        candidate = {**reference, "contract": {**contract, "compatibility_projection": compatibility.PROJECTION}}
        self.assertIn("compatibility_projection", run.compare_samples([reference], [candidate])["differences"])

    def test_benchmark_projection_keeps_raw_artifacts_and_rejects_legacy_changes(self):
        reference = b"sql\n:4\n"
        extension = b"ingest\nsql\n:4\n"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            meter = root / "meter"
            subprocess.run(["cc", "-O2", "-o", str(meter), str(run.HERE / "meter.c")], check=True)
            results = []
            for index, output in enumerate((reference, extension, extension.replace(b"sql", b"changed"))):
                binary = root / f"candidate-{index}"
                binary.write_text(f"#!{sys.executable}\nimport os; os.write(1,{output!r})\n")
                binary.chmod(0o700)
                case = {"argv": ["__complete", ""],
                        "expected_exit": 0, "expected_requests": [],
                        "compatibility_projection": compatibility.PROJECTION}
                artifact = root / str(index)
                result = run.sample(binary, case, None, artifact, 2, meter=meter)
                self.assertTrue(result["valid"], result["issues"])
                self.assertEqual(output, (artifact / "stdout.bin").read_bytes())
                self.assertEqual(output, (artifact / "stdout.normalized").read_bytes())
                self.assertEqual(run.digest(output), result["contract"]["stdout_normalized_sha256"])
                self.assertEqual(run.digest(output), result["stdout_raw_sha256"])
                results.append(result)
            self.assertTrue(run.compare_samples(results[:1], results[1:2])["compatible"])
            self.assertFalse(run.compare_samples(results[:1], results[2:])["compatible"])


if __name__ == "__main__":
    unittest.main()
