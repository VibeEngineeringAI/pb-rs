#!/usr/bin/env python3
"""Source manifests preserve dirty source and exclude private/non-source files."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import manifest


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pb-manifest-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Manifest Test")
        self.git("config", "user.email", "manifest@example.invalid")
        self.write("src/main.rs", "original\n")
        self.write("src/deleted.rs", "remove this\n")
        self.write("Cargo.toml", "[package]\nname = 'fixture'\n")
        self.write("config/config.toml", "TRACKED_PRIVATE_SENTINEL\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_snapshot_restores_staged_unstaged_deleted_and_untracked_source(self):
        self.write("src/main.rs", "staged\n")
        self.git("add", "src/main.rs")
        self.write("src/main.rs", "final unstaged\n")
        (self.root / "src/deleted.rs").unlink()
        added = self.write("src/sql/new.rs", "new source\n")
        added.chmod(0o755)
        snapshot = manifest.source_snapshot(self.root)
        self.assertEqual(snapshot["head"], self.git("rev-parse", "HEAD").decode().strip())
        self.assertEqual(snapshot["deleted_files"], ["src/deleted.rs"])
        self.assertEqual(snapshot["untracked_files"][0]["path"], "src/sql/new.rs")
        self.assertEqual(snapshot["untracked_files"][0]["mode"], "0o755")
        self.assertNotIn("config/config.toml", snapshot["files"])
        self.git("reset", "--hard", "HEAD")
        added.unlink()
        subprocess.run(["git", "-C", str(self.root), "apply", "--binary"],
                       input=base64.b64decode(snapshot["patch_base64"]), check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for entry in snapshot["untracked_files"]:
            path = self.root / entry["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(entry["contents_base64"]))
            path.chmod(int(entry["mode"], 8))
        for name, identity in snapshot["files"].items():
            self.assertEqual(manifest.sha256((self.root / name).read_bytes()), identity["sha256"])
        self.assertFalse((self.root / "src/deleted.rs").exists())

    def test_private_artifacts_and_symlinks_are_never_archived(self):
        self.write("config/config.toml", "CHANGED_PRIVATE_SENTINEL\n")
        for name in ["secrets/token.rs", "src/secrets/token.rs", "harness/results/private.py",
                     "harness/artifacts/private.py", "target/private.rs", "bin/private.go",
                     "src/credentials.rs", ".env", "config.toml", "src/arbitrary.json"]:
            self.write(name, "UNTRACKED_PRIVATE_SENTINEL\n")
        outside = Path(self.directory.name) / "outside.rs"
        outside.write_text("EXTERNAL_PRIVATE_SENTINEL\n")
        (self.root / "src/link.rs").symlink_to(outside)
        (self.root / "src/linked").symlink_to(outside.parent, target_is_directory=True)
        self.git("add", "src/link.rs", "src/linked")
        snapshot = manifest.source_snapshot(self.root)
        serialized = json.dumps(snapshot)
        patch = base64.b64decode(snapshot["patch_base64"])
        self.assertNotIn("PRIVATE_SENTINEL", serialized)
        self.assertNotIn(b"PRIVATE_SENTINEL", patch)
        self.assertNotIn("src/link.rs", snapshot["files"])
        self.assertNotIn("src/linked", snapshot["files"])
        self.assertEqual(snapshot["status"], [])
        self.assertEqual(snapshot["untracked_files"], [])

    def test_whitelist_rejects_escape_and_supports_actual_build_sources(self):
        for name in ["/tmp/main.rs", "../src/main.rs", "src/../main.rs", "src/secrets/main.rs",
                     "harness/results/run.py", "tests/fixtures/tls/server-key.der"]:
            self.assertFalse(manifest.source_allowed(name), name)
        for name in ["src/config.rs", "src/sql/output.rs", "harness/phase3.py", "Cargo.lock",
                     ".github/workflows/rust-phase1.yaml", "docs/rust-phase3.md", "pkg/config/config.go", "harness/diagnostics_go/helper.go.txt"]:
            self.assertTrue(manifest.source_allowed(name), name)

    def test_binary_build_details_are_declared_or_unknown_and_tools_are_hashed(self):
        binary = Path(self.directory.name) / "binary"
        binary.write_bytes(b"prebuilt binary")
        args = argparse.Namespace(repo=str(self.root), binary=[f"rust={binary}", f"go={binary}"],
                                  build_note=["rust=cargo build --release --locked"],
                                  compiler_version=["rust=rustc recorded-version"],
                                  runner=binary, meter=binary, fixture=binary)
        result = manifest.create_manifest(args)
        self.assertEqual(result["binaries"]["rust"]["build_note"], "cargo build --release --locked")
        self.assertEqual(result["binaries"]["rust"]["compiler_version"], "rustc recorded-version")
        self.assertEqual(result["binaries"]["go"]["build_note"], "unknown")
        self.assertEqual(result["binaries"]["go"]["compiler_version"], "unknown")
        expected = manifest.sha256(b"prebuilt binary")
        for label in ["runner", "meter", "fixture"]:
            self.assertEqual(result["tools"][label]["sha256"], expected)
        args.build_note.append("other=unmatched")
        with self.assertRaisesRegex(ValueError, "declared binary"):
            manifest.create_manifest(args)

    def test_capture_rejects_a_source_edit_between_hashing_and_patch_generation(self):
        original_git = manifest.git

        def editing_git(root, *args):
            if args[0] == "diff":
                self.write("src/main.rs", "concurrent edit\n")
            return original_git(root, *args)

        with mock.patch.object(manifest, "git", editing_git):
            with self.assertRaisesRegex(ValueError, "changed during source capture"):
                manifest.source_snapshot(self.root)

    def test_labels_are_unique_and_output_is_not_overwritten(self):
        with self.assertRaises(ValueError):
            manifest.assignments(["rust=a", "rust=b"], "--binary")
        binary = Path(self.directory.name) / "binary"
        binary.write_bytes(b"binary")
        output = Path(self.directory.name) / "manifest.json"
        command = ["python", str(Path(manifest.__file__)), "--repo", str(self.root),
                   "--binary", f"rust={binary}", "--out", str(output)]
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        original = output.read_bytes()
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
