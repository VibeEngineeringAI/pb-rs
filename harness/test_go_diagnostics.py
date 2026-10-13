#!/usr/bin/env python3
"""Verify strict overlay anchors and diagnostic sidecar behavior using stdlib Go."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import go_diagnostics as diagnostics

GO = os.environ.get("PB_DIAGNOSTIC_GO", shutil.which("go") or "")


class OverlayTests(unittest.TestCase):
    def test_original_sources_unchanged_and_exact_overlay_paths(self):
        before = {name: (diagnostics.ROOT / name).read_bytes() for name in diagnostics.SOURCE_SHA256}
        with tempfile.TemporaryDirectory() as directory:
            overlay = diagnostics.generate_overlay(out=Path(directory))
            replacement = json.loads(overlay.read_text())["Replace"]
            self.assertEqual(set(replacement), {str(diagnostics.ROOT / name) for name in before})
            for source, target in replacement.items():
                self.assertNotEqual(source, target)
            main = Path(replacement[str(diagnostics.ROOT / "main.go")]).read_text()
            self.assertIn("pb.DiagnosticsStart()\n\tdefer pb.DiagnosticsFinish()", main)
            query = Path(replacement[str(diagnostics.ROOT / "cmd/query.go")]).read_text()
            self.assertIn("bufio.NewReader(pbDiagInput(resp.Body))", query)
            self.assertIn("pbDiagDecodeItem(decoder, &item, diagElement)", query)
            self.assertIn("pbDiagMarshalItem(item, diagElement)", query)
            self.assertIn("pbDiagIndentItem(encoded, diagElement)", query)
        for name, content in before.items():
            self.assertEqual((diagnostics.ROOT / name).read_bytes(), content)
            self.assertEqual(hashlib.sha256(content).hexdigest(), diagnostics.SOURCE_SHA256[name])

    def test_any_original_source_drift_rejected_before_writes(self):
        for drifted in diagnostics.SOURCE_SHA256:
            with self.subTest(drifted=drifted), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "repo"
                for name in diagnostics.SOURCE_SHA256:
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes((diagnostics.ROOT / name).read_bytes())
                with (root / drifted).open("a") as source:
                    source.write("\n// new source requiring explicit diagnostic review\n")
                out = Path(directory) / "overlay"
                with self.assertRaisesRegex(ValueError, "source drift"):
                    diagnostics.generate_overlay(root, out)
                self.assertFalse(out.exists())

    def test_missing_or_duplicate_anchors_rejected(self):
        for source in ["no anchor", "anchored anchored"]:
            with self.assertRaisesRegex(ValueError, "source anchor changed"):
                diagnostics.replace_once(source, "anchored", "replacement")
        source = (diagnostics.ROOT / "cmd/query.go").read_text()
        with self.assertRaisesRegex(ValueError, "delimiter-token"):
            diagnostics.instrument_renderer(source.replace("decoder.Token()", "decoder.Other()", 1))
        with self.assertRaisesRegex(ValueError, "source anchor changed"):
            diagnostics.instrument_renderer(source.replace("decoder.Decode(&item)", "decoder.Other(&item)"))

    def test_ordinary_go_packages_do_not_include_templates(self):
        self.assertEqual(list((diagnostics.ROOT / "harness/diagnostics_go").glob("*.go")), [])

    def test_binary_destination_protects_sources_and_reference_binaries(self):
        for path in ["main.go", "cmd/query.go", "bin/pb-go", "bin/pb-go-cloud", "target/release/pb"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                diagnostics.validate_binary_destination(diagnostics.ROOT, diagnostics.ROOT / path)
        self.assertEqual(diagnostics.validate_binary_destination(
            diagnostics.ROOT, diagnostics.ROOT / "bin/pb-go-diagnostics"),
            diagnostics.ROOT / "bin/pb-go-diagnostics")

    def test_build_optimized_and_reference_output_separate(self):
        command = diagnostics.build_command("go", Path("overlay.json"), Path("bin/pb-go-diagnostic"))
        self.assertIn("-overlay", command)
        self.assertIn("-trimpath", command)
        self.assertFalse(any("-N" in option or "-l" == option for option in command))
        self.assertEqual(command[-2:], ["bin/pb-go-diagnostic", "."])


@unittest.skipUnless(GO, "Go executable unavailable")
class SidecarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="pb-go-diagnostics-test-")
        root = Path(cls.directory.name)
        source = (diagnostics.ROOT / "cmd/query.go").read_text()
        start = source.index("func writePrettyJSONArray(")
        end = source.index("\nfunc firstNonSpace(", start)
        renderer = source[start:end]
        instrumented = diagnostics.instrument_renderer(source)[start:]
        instrumented = instrumented[:instrumented.index("\nfunc firstNonSpace(")]
        helper = (diagnostics.ROOT / "harness/diagnostics_go/helper.go.txt").read_text()
        imports = '\n'.join(f'"{package}"' for package in
                           ["bytes", "encoding/json", "fmt", "io", "os", "strconv", "strings", "time"])
        testmain = '''
func main() {
    if os.Getenv("TEST_NATIVE") == "1" {
        _ = nativeRenderer(os.Stdout, strings.NewReader(os.Getenv("TEST_BODY")))
        return
    }
    DiagnosticsStart()
    defer DiagnosticsFinish()
    if os.Getenv("TEST_EXIT") == "1" { os.Exit(1) }
    if os.Getenv("TEST_SHORT") == "1" {
        _, _ = pbDiagOutput(shortWriter{}).Write([]byte("SECRET_BODY"))
        return
    }
    start := pbDiagBegin(pbDiagRenderer)
    _ = writePrettyJSONArray(pbDiagOutput(os.Stdout), pbDiagInput(strings.NewReader(os.Getenv("TEST_BODY"))))
    pbDiagEnd(pbDiagRenderer, start)
}
type shortWriter struct{}
func (shortWriter) Write(data []byte) (int, error) { return 3, io.ErrShortWrite }
'''
        text = "package main\nimport (\n" + imports + diagnostics.DIAGNOSTIC_IMPORTS + ")\n"
        text += renderer.replace("func writePrettyJSONArray(", "func nativeRenderer(")
        text += instrumented + helper + testmain
        (root / "main.go").write_text(text)
        cls.binary = root / "diagnostic-test"
        cls.env = os.environ.copy()
        cls.env.update({"GOTOOLCHAIN": "go1.25.4", "CGO_ENABLED": "0", "TMPDIR": str(diagnostics.ROOT / "target/tmp"), "GOTMPDIR": str(diagnostics.ROOT / "target/tmp")})
        (diagnostics.ROOT / "target/tmp").mkdir(parents=True, exist_ok=True)
        subprocess.run([GO, "build", "-o", str(cls.binary), str(root / "main.go")],
                       env=cls.env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_cli(self, directory=None, **updates):
        env = self.env.copy()
        for name in list(env):
            if name.startswith("PB_DIAGNOSTICS_") or name.startswith("TEST_"):
                del env[name]
        env["TEST_BODY"] = '[{"private_key":"SECRET_BODY","value":3},{"value":4}]'
        if directory is not None:
            env["PB_DIAGNOSTICS_DIR"] = str(directory)
        env.update(updates)
        return subprocess.run([str(self.binary)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_exact_original_output_including_existing_quirks(self):
        for body in ["[]", "[1,null,true]", '[{"z":"λ<>&","a":9007199254740993}]',
                     '[{"dup":1,"dup":2}]', '[{"a":[1,{"b":2}]}]', '[1] trailing',
                     '{"a":1}', '[1,invalid]']:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory:
                native = self.run_cli(TEST_NATIVE="1", TEST_BODY=body)
                instrumented = self.run_cli(directory, TEST_BODY=body)
                self.assertEqual(instrumented.returncode, native.returncode)
                self.assertEqual(instrumented.stdout, native.stdout)
                self.assertEqual(instrumented.stderr, native.stderr)

    def test_disabled_diagnostics_emit_no_sidecar(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")

    def test_sampled_counts_schema_and_privacy(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli(directory, PB_DIAGNOSTICS_SAMPLE_EVERY="2")
            self.assertEqual(result.returncode, 0, result.stderr)
            text = (Path(directory) / "go.json").read_text()
            self.assertNotIn("SECRET_BODY", text)
            self.assertNotIn("private_key", text)
            metrics = json.loads(text)
            self.assertEqual(metrics["schema_version"], 1)
            self.assertTrue(metrics["complete"])
            self.assertEqual(metrics["language"], "go")
            self.assertEqual(metrics["mode"], "timing")
            self.assertIsNone(metrics["allocations"])
            self.assertGreater(metrics["command_wall_ns"], 0)
            self.assertGreaterEqual(metrics["process_cpu_ns"], 0)
            self.assertEqual(metrics["stages"]["decode"]["calls"], 2)
            self.assertEqual(metrics["stages"]["decode"]["timed_calls"], 1)
            self.assertEqual(metrics["stages"]["marshal_indent"]["timed_calls"], 1)
            self.assertEqual(metrics["stages"]["indent_copy"]["timed_calls"], 1)
            self.assertEqual(metrics["stages"]["output_write"]["timed_calls"], 6)
            self.assertEqual(metrics["stages"]["output_write"]["calls"], 9)
            self.assertTrue(metrics["stages"]["decode"]["inclusive"])
            self.assertFalse(metrics["stages"]["marshal_indent"]["inclusive"])
            output = metrics["output"]
            self.assertEqual(output["bytes_written"], len(result.stdout))
            self.assertEqual(output["bytes_requested"], len(result.stdout))
            self.assertEqual(output["write_errors"], 0)
            self.assertGreater(output["logical_write_calls"], 2)
            self.assertGreater(metrics["input"]["bytes_read"], 0)

    def test_profiles_are_separate_and_mode_explicit(self):
        for mode, filename in [("cpu", "go-cpu.pprof"), ("allocations", "go-allocs.pprof")]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                result = self.run_cli(directory, PB_DIAGNOSTICS_MODE=mode, PB_DIAGNOSTICS_MEM_PROFILE_RATE="1")
                self.assertEqual(result.returncode, 0, result.stderr)
                metrics = json.loads((Path(directory) / "go.json").read_text())
                self.assertEqual(metrics["mode"], mode)
                self.assertGreater((Path(directory) / filename).stat().st_size, 0)
                self.assertGreater(metrics["profile_flush_ns"], 0)
                if mode == "allocations":
                    self.assertEqual(metrics["allocation_profile_rate_bytes"], 1)
                    self.assertGreater(metrics["allocation_profile_startup_rate_bytes"], 0)
                    self.assertIn("total_alloc_bytes", metrics["allocations"])
                    self.assertIn("gc_cycles", metrics["allocations"])
                else:
                    self.assertIsNone(metrics["allocations"])

    def test_short_write_counts_observed_not_requested_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli(directory, TEST_SHORT="1")
            self.assertEqual(result.returncode, 0)
            output = json.loads((Path(directory) / "go.json").read_text())["output"]
            self.assertEqual(output, {"logical_write_calls": 1, "bytes_requested": 11,
                                      "bytes_written": 3, "write_errors": 1})

    def test_os_exit_leaves_incomplete_sidecar_and_existing_sidecar_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli(directory, TEST_EXIT="1")
            self.assertEqual(result.returncode, 1)
            self.assertEqual((Path(directory) / "go.json").read_bytes(), b"")
            again = self.run_cli(directory)
            self.assertEqual(again.returncode, 2)
            self.assertEqual((Path(directory) / "go.json").read_bytes(), b"")

    def test_invalid_configuration_fails_without_exposing_values(self):
        for variable, value in [("PB_DIAGNOSTICS_MODE", "SECRET_BODY"),
                                ("PB_DIAGNOSTICS_SAMPLE_EVERY", "0"),
                                ("PB_DIAGNOSTICS_MEM_PROFILE_RATE", "PRIVATE_INVALID_RATE")]:
            with self.subTest(variable=variable), tempfile.TemporaryDirectory() as directory:
                updates = {variable: value}
                if variable.endswith("PROFILE_RATE"):
                    updates["PB_DIAGNOSTICS_MODE"] = "allocations"
                result = self.run_cli(directory, **updates)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(b"SECRET_BODY", result.stderr)
                self.assertNotIn(value.encode(), result.stderr)


if __name__ == "__main__":
    unittest.main()
