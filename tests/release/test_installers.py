#!/usr/bin/env python3
"""Offline checks for scripts/install.sh, scripts/install.ps1 and scripts/release.py.

A local HTTP server mimics github.com release URLs (the /releases/latest
redirect and /releases/download/<tag>/<asset>), so installers run unmodified
apart from PB_INSTALL_GITHUB_URL. Fixture archives come from release.py, so
packaging, checksums and installer pinning are exercised together.

  python3 tests/release/test_installers.py            # unit checks
  python3 tests/release/test_installers.py --e2e-dist dist --e2e-version 0.1.0-rc.1
      # install the real archives in dist/ for this host and run `pb version`

Environment: PB_INSTALLER_TEST_SH selects the POSIX shell (default sh);
PB_INSTALLER_TEST_MODIFY_PATH=1 (Windows only) also checks the user PATH
registry update, restoring the original value afterwards.
"""
import argparse
import contextlib
import io
import http.server
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import release  # noqa: E402

REPO = "VibeEngineeringAI/pb-rs"
VERSION = "1.2.3-rc.1"
OLD_VERSION = "1.2.2"
TIMEOUT = 60
WINDOWS = os.name == "nt"


class ReleaseServer:
    """Serve <root>/<tag>/<asset> as github.com/<repo>/releases/download/<tag>/<asset>."""

    def __init__(self, root, latest):
        self.root, self.latest, self.requests = Path(root), latest, []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self.respond(head=True)

            def do_GET(self):
                self.respond(head=False)

            def respond(self, head):
                server.requests.append(self.path)
                prefix = f"/{REPO}/releases/"
                rest = self.path[len(prefix):] if (self.path + "/").startswith(prefix) else None
                if rest == "latest":
                    # GitHub redirects to /releases when only prereleases exist.
                    target = f"tag/{server.latest}" if server.latest else ""
                    self.send_response(302)
                    self.send_header("Location", f"{server.base}{prefix}{target}".rstrip("/"))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if rest is not None and (rest.startswith("tag/") or rest == ""):
                    self.reply(200, b"release page", head)
                    return
                if rest is not None and rest.startswith("download/"):
                    parts = rest.split("/")
                    if len(parts) == 3 and ".." not in parts:
                        path = server.root / parts[1] / parts[2]
                        if path.is_file():
                            self.reply(200, path.read_bytes(), head)
                            return
                self.reply(404, b"not found", head)

            def reply(self, code, body, head):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head:
                    self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def build_release(dest, version, binary_dir):
    """Package every required target with a fake pb that reports its version."""
    tag_dir = Path(dest) / f"v{version}"
    tag_dir.mkdir(parents=True)
    fake = Path(binary_dir) / f"pb-{version}"
    fake.write_text(f'#!/bin/sh\necho "pb version {version}"\n', encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        for os_name, arch in release.REQUIRED_TARGETS:
            release.main(["package", "--version", version, "--os", os_name, "--arch", arch,
                          "--binary", str(fake), "--out", str(tag_dir)])
        release.main(["finalize", "--version", version, "--dist", str(tag_dir)])
    return tag_dir


def powershell():
    if os.environ.get("PB_INSTALLER_TEST_POWERSHELL"):
        return shutil.which(os.environ["PB_INSTALLER_TEST_POWERSHELL"])
    for name in ("pwsh", "powershell") if WINDOWS else ("pwsh",):
        found = shutil.which(name)
        if found:
            return found
    return None


class InstallerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.releases = base / "releases"
        (base / "bin").mkdir()
        cls.tag_dir = build_release(cls.releases, VERSION, base / "bin")
        build_release(cls.releases, OLD_VERSION, base / "bin")
        # A corrupted copy whose checksum file no longer matches its archive.
        bad = cls.releases / "v9.9.9"
        shutil.copytree(build_release(cls.releases / "src", "9.9.9", base / "bin"), bad)
        for name in os.listdir(bad):
            if name.endswith((".tar.gz", ".zip")):
                with open(bad / name, "ab") as handle:
                    handle.write(b"tampered")
        # A release whose checksum file omits every archive.
        partial = cls.releases / "v9.9.8"
        shutil.copytree(build_release(cls.releases / "src2", "9.9.8", base / "bin"), partial)
        (partial / "pb_9.9.8_checksums.txt").write_text("0" * 64 + "  something-else.tar.gz\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.tmp.name))
        self.install_dir = self.work / "install dir"

    def serve(self, latest=f"v{OLD_VERSION}"):
        return ReleaseServer(self.releases, latest)

    def environment(self, server, **extra):
        # Python may inherit PowerShell 7 module paths from the CI step.
        # Let the selected PowerShell rebuild its own paths (especially 5.1).
        env = {k: v for k, v in os.environ.items()
               if k.upper() not in ("PB_VERSION", "VERSION", "PB_INSTALL_DIR",
                                    "INSTALL_DIR", "SHELL", "PSMODULEPATH")}
        env.update(PB_INSTALL_GITHUB_URL=server.base, HOME=str(self.work), TMPDIR=str(self.work))
        env.update(extra)
        return env


@unittest.skipIf(WINDOWS, "POSIX installer")
class ShellInstallerTests(InstallerCase):
    shell = os.environ.get("PB_INSTALLER_TEST_SH", "sh")

    def run_sh(self, server, *args, script=ROOT / "scripts" / "install.sh", path=None, **env):
        env = self.environment(server, **env)
        if path is not None:
            env["PATH"] = path
        return subprocess.run([self.shell, str(script), *args], env=env, capture_output=True,
                              text=True, timeout=TIMEOUT)

    def installed(self):
        return self.install_dir / "pb"

    def assert_installed(self, version):
        binary = self.installed()
        self.assertTrue(binary.stat().st_mode & stat.S_IXUSR)
        out = subprocess.run([str(binary)], capture_output=True, text=True, timeout=TIMEOUT).stdout
        self.assertEqual(out, f"pb version {version}\n")

    def test_pinned_version_with_flags(self):
        with self.serve() as server:
            result = self.run_sh(server, "--version", f"v{VERSION}", "--install-dir", str(self.install_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Verified SHA-256 checksum.", result.stdout)
        self.assert_installed(VERSION)

    def test_latest_follows_release_redirect(self):
        with self.serve() as server:
            result = self.run_sh(server, PB_INSTALL_DIR=str(self.install_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed(OLD_VERSION)

    def test_latest_without_stable_release_asks_for_pin(self):
        with self.serve(latest=None) as server:
            result = self.run_sh(server, PB_INSTALL_DIR=str(self.install_dir))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no stable release", result.stderr)
        self.assertIn("--version", result.stderr)
        self.assertFalse(self.installed().exists())

    def test_checksum_mismatch_installs_nothing(self):
        with self.serve() as server:
            result = self.run_sh(server, "--version", "9.9.9", PB_INSTALL_DIR=str(self.install_dir))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum verification failed", result.stderr)
        self.assertFalse(self.installed().exists())

    def test_missing_checksum_entry_installs_nothing(self):
        with self.serve() as server:
            result = self.run_sh(server, "--version", "9.9.8", PB_INSTALL_DIR=str(self.install_dir))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum for pb_9.9.8_", result.stderr)
        self.assertFalse(self.installed().exists())

    def test_rejects_malformed_versions_before_downloading(self):
        for bad in ("../../etc", "1.2.3/../../x", "1.2", "v1.2.3-", "1.2.3\n4.5.6", "1.2.3 ", "1.2.3-rc/1"):
            with self.subTest(version=bad), self.serve() as server:
                result = self.run_sh(server, PB_VERSION=bad, PB_INSTALL_DIR=str(self.install_dir))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid version", result.stderr)
                self.assertEqual(server.requests, [])

    def test_unknown_argument_fails(self):
        with self.serve() as server:
            result = self.run_sh(server, "--bogus")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown argument", result.stderr)

    def test_path_hint_without_shell_variable(self):
        # SHELL is removed from the environment; set -u must not abort the hint.
        with self.serve() as server:
            result = self.run_sh(server, "--version", VERSION, "--install-dir", str(self.install_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("is not in your PATH", result.stdout)
        self.assertIn(f'export PATH="{self.install_dir}:$PATH"', result.stdout)

    def test_path_hint_for_fish_and_absent_when_on_path(self):
        with self.serve() as server:
            fish = self.run_sh(server, "--version", VERSION, "--install-dir", str(self.install_dir),
                               SHELL="/usr/bin/fish")
            on_path = self.run_sh(server, "--version", VERSION, "--install-dir", str(self.install_dir),
                                  path=f"{self.install_dir}:{os.environ['PATH']}")
        self.assertIn("fish_add_path", fish.stdout)
        self.assertEqual(on_path.returncode, 0, on_path.stderr)
        self.assertNotIn("not in your PATH", on_path.stdout)

    def test_legacy_environment_and_upgrade_in_place(self):
        with self.serve() as server:
            first = self.run_sh(server, VERSION=OLD_VERSION, INSTALL_DIR=str(self.install_dir))
            second = self.run_sh(server, PB_VERSION=VERSION, VERSION=OLD_VERSION,
                                 INSTALL_DIR=str(self.install_dir))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assert_installed(VERSION)
        self.assertEqual(sorted(os.listdir(self.install_dir)), ["pb"])

    def test_pinned_release_installer_defaults_to_its_tag(self):
        script = self.tag_dir / "install.sh"
        with self.serve() as server:
            result = self.run_sh(server, script=script, PB_INSTALL_DIR=str(self.install_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed(VERSION)
        self.assertFalse(any(p.endswith("/releases/latest") for p in server.requests))

    def test_platform_detection_selects_asset(self):
        cases = {("Linux", "aarch64"): "linux_arm64", ("Linux", "x86_64"): "linux_amd64",
                 ("Darwin", "arm64"): "darwin_arm64", ("Darwin", "x86_64"): "darwin_amd64"}
        for (system, machine), suffix in cases.items():
            shims = self.work / f"shims-{system}-{machine}"
            shims.mkdir()
            uname = shims / "uname"
            uname.write_text(f'#!/bin/sh\ncase "$1" in -s) echo {system} ;; -m) echo {machine} ;; esac\n')
            uname.chmod(0o755)
            sysctl = shims / "sysctl"
            sysctl.write_text("#!/bin/sh\necho 0\n")
            sysctl.chmod(0o755)
            with self.subTest(system=system, machine=machine), self.serve() as server:
                result = self.run_sh(server, "--version", VERSION, "--install-dir", str(self.install_dir),
                                     path=f"{shims}:{os.environ['PATH']}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"/{REPO}/releases/download/v{VERSION}/pb_{VERSION}_{suffix}.tar.gz",
                              server.requests)

    def test_unsupported_platform(self):
        shims = self.work / "shims"
        shims.mkdir()
        (shims / "uname").write_text('#!/bin/sh\ncase "$1" in -s) echo Linux ;; -m) echo riscv64 ;; esac\n')
        (shims / "uname").chmod(0o755)
        with self.serve() as server:
            result = self.run_sh(server, "--version", VERSION, path=f"{shims}:{os.environ['PATH']}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported architecture: riscv64", result.stderr)


@unittest.skipUnless(powershell(), "PowerShell is not installed")
class PowerShellInstallerTests(InstallerCase):
    def run_ps(self, server, script=ROOT / "scripts" / "install.ps1", **env):
        env = self.environment(server, **env)
        env.setdefault("PB_INSTALL_DIR", str(self.install_dir))
        env.setdefault("PB_NO_MODIFY_PATH", "1")
        # Pipe through Invoke-Expression exactly like the documented one-liner.
        command = f"Get-Content -Raw -LiteralPath '{script}' | Invoke-Expression"
        return subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-Command", command],
                              env=env, capture_output=True, text=True, timeout=TIMEOUT * 3)

    def installed(self):
        return self.install_dir / "pb.exe"

    def assert_installed(self, version):
        self.assertIn(f"pb version {version}", self.installed().read_text())

    def test_pinned_version(self):
        with self.serve() as server:
            result = self.run_ps(server, PB_VERSION=f"v{VERSION}")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Verified SHA-256 checksum.", result.stdout)
        self.assert_installed(VERSION)

    def test_latest_follows_release_redirect(self):
        with self.serve() as server:
            result = self.run_ps(server)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assert_installed(OLD_VERSION)

    def test_latest_without_stable_release_asks_for_pin(self):
        with self.serve(latest=None) as server:
            result = self.run_ps(server)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No stable release", result.stderr + result.stdout)
        self.assertFalse(self.installed().exists())

    def test_checksum_mismatch_installs_nothing(self):
        with self.serve() as server:
            result = self.run_ps(server, PB_VERSION="9.9.9")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Checksum verification failed", result.stderr + result.stdout)
        self.assertFalse(self.installed().exists())

    def test_missing_checksum_entry_installs_nothing(self):
        with self.serve() as server:
            result = self.run_ps(server, PB_VERSION="9.9.8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found in pb_9.9.8_checksums.txt", result.stderr + result.stdout)
        self.assertFalse(self.installed().exists())

    def test_rejects_malformed_versions_before_downloading(self):
        for bad in ("../../etc", "1.2.3/../../x", "1.2", "1.2.3-", "1.2.3-rc/1"):
            with self.subTest(version=bad), self.serve() as server:
                result = self.run_ps(server, PB_VERSION=bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid version", result.stderr + result.stdout)
                self.assertEqual(server.requests, [])

    def test_pinned_release_installer_defaults_to_its_tag(self):
        with self.serve() as server:
            result = self.run_ps(server, script=self.tag_dir / "install.ps1")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assert_installed(VERSION)

    def test_caller_session_preferences_are_untouched(self):
        script = ROOT / "scripts" / "install.ps1"
        with self.serve() as server:
            env = self.environment(server, PB_VERSION=VERSION, PB_INSTALL_DIR=str(self.install_dir),
                                   PB_NO_MODIFY_PATH="1")
            command = (f"$ErrorActionPreference = 'Continue'; Get-Content -Raw -LiteralPath '{script}' | "
                       "Invoke-Expression; Write-Output \"EAP=$ErrorActionPreference\"; "
                       "Write-Output \"REPO=$(Test-Path variable:Repo)\"")
            result = subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-Command", command],
                                    env=env, capture_output=True, text=True, timeout=TIMEOUT * 3)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("EAP=Continue", result.stdout)
        self.assertIn("REPO=False", result.stdout)

    @unittest.skipUnless(WINDOWS and os.environ.get("PB_INSTALLER_TEST_MODIFY_PATH") == "1",
                         "modifies the real user PATH; opt in on disposable Windows runners")
    def test_user_path_is_updated_once_and_preserves_entries(self):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS) as key:
            try:
                original = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                original = None
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, r"%USERPROFILE%\pb-test-keep")
        try:
            with self.serve() as server:
                for _ in range(2):
                    result = self.run_ps(server, PB_VERSION=VERSION, PB_NO_MODIFY_PATH="")
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, kind = winreg.QueryValueEx(key, "Path")
            self.assertEqual(kind, winreg.REG_EXPAND_SZ)
            entries = value.split(";")
            self.assertEqual(entries[0], r"%USERPROFILE%\pb-test-keep")
            self.assertEqual(entries.count(str(self.install_dir)), 1)
        finally:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS) as key:
                if original is None:
                    winreg.DeleteValue(key, "Path")
                else:
                    winreg.SetValueEx(key, "Path", 0, original[1], original[0])


class ReleaseHelperTests(unittest.TestCase):
    def test_tag_validation(self):
        self.assertEqual(release.parse_tag("v0.1.0-rc.1", "0.1.0"),
                         {"tag": "v0.1.0-rc.1", "version": "0.1.0-rc.1", "prerelease": "true"})
        self.assertEqual(release.parse_tag("v0.1.0", "0.1.0")["prerelease"], "false")
        for bad in ("0.1.0", "v0.1", "v0.1.0-", "v0.1.0/x", "v0.1.0+build"):
            with self.subTest(tag=bad), self.assertRaises(SystemExit):
                release.parse_tag(bad, "0.1.0")
        with self.assertRaises(SystemExit):
            release.parse_tag("v0.2.0-rc.1", "0.1.0")

    def test_archives_have_root_layout_and_modes(self):
        import tarfile
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            tag_dir = build_release(Path(tmp) / "r", VERSION, tmp)
            with tarfile.open(tag_dir / f"pb_{VERSION}_linux_amd64.tar.gz") as archive:
                names = {m.name: m for m in archive.getmembers()}
            self.assertEqual(set(names), {"pb", *release.DOCS})
            self.assertEqual(names["pb"].mode, 0o755)
            with zipfile.ZipFile(tag_dir / f"pb_{VERSION}_windows_amd64.zip") as archive:
                self.assertEqual(set(archive.namelist()), {"pb.exe", *release.DOCS})
            lines = (tag_dir / f"pb_{VERSION}_checksums.txt").read_text().splitlines()
            listed = [line.split("  ", 1)[1] for line in lines]
            self.assertEqual(listed, sorted(listed[:-2]) + ["install.sh", "install.ps1"])
            self.assertEqual(len(listed), len(release.REQUIRED_TARGETS) + 2)
            for line in lines:
                digest, name = line.split("  ", 1)
                self.assertEqual(digest, release.sha256(tag_dir / name))

    def test_finalize_rejects_incomplete_or_unexpected_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tag_dir = build_release(Path(tmp) / "r", VERSION, tmp)
            (tag_dir / f"pb_{VERSION}_linux_arm64.tar.gz").unlink()
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                release.main(["finalize", "--version", VERSION, "--dist", str(tag_dir)])
        with tempfile.TemporaryDirectory() as tmp:
            tag_dir = build_release(Path(tmp) / "r", VERSION, tmp)
            (tag_dir / "stray.txt").write_text("x")
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                release.main(["finalize", "--version", VERSION, "--dist", str(tag_dir)])

    def test_packaging_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = build_release(Path(tmp) / "a", VERSION, tmp)
            second = build_release(Path(tmp) / "b", VERSION, tmp)
            self.assertEqual((first / f"pb_{VERSION}_checksums.txt").read_text(),
                             (second / f"pb_{VERSION}_checksums.txt").read_text())


def host_target():
    os_name = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}[platform.system()]
    machine = platform.machine().lower()
    return os_name, "arm64" if machine in ("arm64", "aarch64") else "amd64"


def e2e(dist, version):
    """Install the real archives in dist with the release-pinned installer and run pb.

    dist is either a finalized release directory or a single build's archives,
    for which checksums and pinned installers are generated here.
    """
    with tempfile.TemporaryDirectory() as tmp:
        releases = Path(tmp) / "releases"
        releases.mkdir()
        tag_dir = releases / f"v{version}"
        shutil.copytree(Path(dist).resolve(), tag_dir)
        sums = tag_dir / release.checksums_name(version)
        if not sums.exists():
            for name in release.INSTALLERS:
                (tag_dir / name).write_text(release.stamp_installer(ROOT / "scripts" / name, f"v{version}"),
                                            encoding="utf-8", newline="\n")
            sums.write_text("".join(f"{release.sha256(p)}  {p.name}\n" for p in sorted(tag_dir.iterdir())
                                    if p.is_file()), encoding="utf-8", newline="\n")
        dist = tag_dir
        install_dir = Path(tmp) / "bin"
        env = {k: v for k, v in os.environ.items()
               if k.upper() not in ("PB_VERSION", "VERSION", "PSMODULEPATH")}
        env.update(PB_INSTALL_DIR=str(install_dir), PB_NO_MODIFY_PATH="1")
        with ReleaseServer(releases, f"v{version}") as server:
            env["PB_INSTALL_GITHUB_URL"] = server.base
            if WINDOWS:
                script = dist / "install.ps1"
                command = [powershell(), "-NoProfile", "-NonInteractive", "-Command",
                           f"Get-Content -Raw -LiteralPath '{script}' | Invoke-Expression"]
                binary = install_dir / "pb.exe"
            else:
                command = [os.environ.get("PB_INSTALLER_TEST_SH", "sh"), str(dist / "install.sh")]
                binary = install_dir / "pb"
            subprocess.run(command, env=env, check=True, timeout=TIMEOUT * 3)
        out = subprocess.run([str(binary), "version"], capture_output=True, text=True, check=True,
                             timeout=TIMEOUT).stdout
        if out.strip() != f"pb version {version}":
            sys.exit(f"error: installed binary reported {out!r}, expected pb version {version}")
        print(f"installed {host_target()} build and verified: {out.strip()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--e2e-dist")
    parser.add_argument("--e2e-version")
    known, rest = parser.parse_known_args()
    if known.e2e_dist:
        e2e(known.e2e_dist, known.e2e_version)
    else:
        unittest.main(argv=[sys.argv[0], *rest], verbosity=2)
