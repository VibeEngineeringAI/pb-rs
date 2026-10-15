#!/usr/bin/env python3
"""Offline phase-one parser/help acceptance gate, comparing exact process bytes.

This intentionally does not reuse the operational fixture corpus in cases.py.
See contracts/README.md for scope, safety assumptions and capture provenance.
"""

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time


HERE = Path(__file__).resolve().parent
SURFACE_PATH = HERE / "contracts" / "phase1-surface.json"
SCHEMA_VERSION = 2
MAX_OUTPUT = 4 << 20
UNICODE_CHUNK_SCALARS = 25000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n")


def load_surface():
    return json.loads(SURFACE_PATH.read_text())


def unicode_quote_chunks():
    """Every scalar from U+00A0 upward in arguments below Linux's 128 KiB cap."""
    chunk = []
    for scalar in range(0xA0, 0x110000):
        if 0xD800 <= scalar <= 0xDFFF:
            continue
        chunk.append(chr(scalar))
        if len(chunk) == UNICODE_CHUNK_SCALARS:
            yield "".join(chunk)
            chunk = []
    if chunk:
        yield "".join(chunk)


def corpus(surface=None):
    """A finite, deterministic corpus; every operation is help or a parser error.

    Flags before a help token still undergo pflag validation. Missing/invalid
    values and Args failures deliberately omit help, because help bypasses Args.
    """
    surface = surface or load_surface()
    cases = []

    def add(label, argv, group="parser", safety="help", benchmark=False):
        cases.append({"name": f"p1-{len(cases):04d}-{label}", "group": "phase1-" + group,
                      "argv": argv, "safety": safety, "benchmark": benchmark})

    # Small representative subset is exported for the native measurement runner.
    for label, argv in (("root", []), ("root-help", ["--help"]),
                        ("root-tree", ["-o", "json"]), ("version", ["version"]),
                        ("version-short", ["-v"]), ("version-long", ["--version"]),
                        ("version-json", ["version", "-o", "json"]),
                        ("root-version-json", ["--version", "-o", "json"]),
                        ("agent-text", ["agent"]), ("agent-json", ["agent", "-o", "json"]),
                        ("sql-help-json", ["help", "sql", "run", "-o", "json"]),
                        ("promql-help", ["promql", "run", "--help"]),
                        ("unknown-json", ["--unknown-phase1", "-o", "json"])):
        add(label, argv, "startup", "parser-invalid" if "--unknown-phase1" in argv else "offline-action", True)

    for node in surface["nodes"]:
        path = node["path"]
        label = "-".join(path) or "root"
        add(label + "-help", [*path, "--help"], "help")
        add(label + "-short-help", [*path, "-h"], "help")
        add(label + "-help-path", ["help", *path], "help", "offline-action")
        add(label + "-help-json", ["help", *path, "-o", "json"], "help", "offline-action")
        add(label + "-help-json-equals", ["help", *path, "--output=json"], "help", "offline-action")
        # Each command must reject bad flags before any operation or prerequisite.
        add(label + "-unknown-long", [*path, "--unknown-phase1"], safety="parser-invalid")
        add(label + "-unknown-short", [*path, "-Z"], safety="parser-invalid")
        add(label + "-bad-bool", [*path, "--help=invalid"], safety="parser-invalid")
        add(label + "-help-false-error", [*path, "--help=false", "--unknown-phase1"], safety="parser-invalid")
        # A false boolean and -- terminator are tested safely without running leaves.
        add(label + "-help-false-then-true", [*path, "--help=false", "--help"])
        add(label + "-help-before-terminator", [*path, "--help", "--", "--unknown-phase1", "-ojson"])
        for alias in node["aliases"]:
            alias_path = [*path[:-1], alias]
            add(label + "-alias-" + alias, [*alias_path, "--help"], "alias")
            add(label + "-alias-json-" + alias, ["help", *alias_path, "-ojson"], "alias", "offline-action")
            add(label + "-alias-error-" + alias, [*alias_path, "--unknown-phase1"], "alias", "parser-invalid")
        for flag in node["flags"]:
            name, kind = flag["name"], flag["type"]
            value = "false" if kind == "bool" else "7" if kind == "int" else "json" if name == "output" else "phase1,café"
            flag_label = label + "-" + name
            add(flag_label + "-equals", [*path, "--" + name + "=" + value, "--help"])
            if kind != "bool":
                add(flag_label + "-separated", [*path, "--" + name, value, "--help"])
                add(flag_label + "-empty", [*path, "--" + name + "=", "--help"], safety="parser-invalid" if kind == "int" else "help")
                add(flag_label + "-missing", [*path, "--" + name], safety="parser-invalid")
            else:
                add(flag_label + "-implicit", [*path, "--" + name, "--help"])
                add(flag_label + "-invalid", [*path, "--" + name + "=invalid"], safety="parser-invalid")
                add(flag_label + "-separate-false", [*path, "--" + name, "false", "--help"])
            add(flag_label + "-repeated", [*path, "--" + name + "=" + value, "--" + name + "=" + value, "--help"])
            add(flag_label + "-interspersed", [*path, "arg1", "--" + name + "=" + value, "arg2", "--help"])
            # Cobra Find uses the final command's local flags during traversal.
            for position in range(len(path)):
                add(flag_label + "-position-" + str(position),
                    [*path[:position], "--" + name + "=" + value, *path[position:], "--help"])
            if flag.get("short"):
                short = "-" + flag["short"]
                add(flag_label + "-short-equals", [*path, short + "=" + value, "--help"])
                if kind != "bool":
                    add(flag_label + "-short-attached", [*path, short + value, "--help"])
                    add(flag_label + "-short-separated", [*path, short, value, "--help"])
                else:
                    add(flag_label + "-short-cluster", [*path, short + "h"])
            if kind == "int":
                for bad in ("abc", "9223372036854775808", "1.5"):
                    add(flag_label + "-invalid-int", [*path, "--" + name + "=" + bad], safety="parser-invalid")
                for good in ("-1", "0x10", "010"):
                    add(flag_label + "-integer-syntax", [*path, "--" + name + "=" + good, "--help"])
            if kind == "array":
                add(flag_label + "-array-repeat", [*path, "--" + name, '{job="api",env="test"}',
                                                  "--" + name + "=", "-msecond,third", "--help"])
                add(flag_label + "-array-dash-value", [*path, "--" + name, "--selector-looking-value", "--help"])
        # Root flags are local; leaves without output/version must reject them.
        known = {f["name"] for f in node["flags"]}
        if path and "output" not in known:
            add(label + "-local-output", [*path, "-ojson", "--help"])
        if path:
            add(label + "-local-version", [*path, "--version", "--help"])
        if "arity" in node:
            minimum, maximum = node["arity"]
            counts = ([minimum - 1] if minimum else []) + [maximum + 1]
            for count in counts:
                for terminator in (False, True):
                    argv = [*path, *(["--"] if terminator else []), *[f"arg{i}" for i in range(count)]]
                    add(label + "-arity-" + str(count) + ("-terminator" if terminator else ""), argv, "arity", "arity-invalid")
                if "output" in known:
                    add(label + "-arity-json-" + str(count), [*path, "-ojson", *[f"arg{i}" for i in range(count)]],
                        "arity", "arity-invalid")

    # Parent JSON actions that are profile-free in Go; other parents need config.
    for path in (["profile"], ["cloud"], ["cloud", "profile"]):
        add("-".join(path) + "-tree", [*path, "-ojson"], "startup", "offline-action")

    for path in ([], ["version"], ["agent"], ["help"]):
        label = "-".join(path) or "root"
        for value in ("yaml", "JSON", " json ", "", " Text ", "\u0007", "\u001b", "line\nbreak", "\u2028", "\u00a0json\u00a0"):
            add(label + "-output-format", [*path, "--output=" + value], "output", "offline-action")
        add(label + "-output-last-json", [*path, "-otext", "--output=json"], "output", "offline-action")
        add(label + "-output-last-text", [*path, "-ojson", "--output=text"], "output", "offline-action")
    # Raw argv scan determines error encoding independently of effective parsing.
    for suffix in (["--output=json"], ["-o=json"], ["-ojson"], ["--output", "json"],
                   ["-o", "json"], ["--OUTPUT=JSON"], ["-OJSON"], [" --output ", " JSON "],
                   ["--output= JSON "], ["-ojson", "-otext"], ["--", "-ojson"],
                   ["--", "--output", "json"], ["--output", "--", "json"]):
        add("raw-json-error-scan", ["--unknown-phase1", *suffix], "errors", "parser-invalid")
    for argv in (["nosuch"], ["helpp"], ["sql", "nosuch"], ["help", "nosuch"],
                 ["help", "sql", "nosuch", "-ojson"], ["help", "sql", "run", "ignored", "-ojson"],
                 ["help", "--", "sql", "run"], ["help", "--", "sql", "run", "-ojson"],
                 ["--help", "sql", "run"], ["-h", "sql", "run"],
                 ["--version=false"], ["-vh"], ["-hv"], ["-vojson"],
                 ["-vo", "json"], ["-h", "-ojson"], ["datset"], ["datset", "-ojson"],
                 ["--bogus", "-o", "json"], ["version", "-x", "-ojson"],
                 ["version", "--output", "-o", "json"], ["version", "--", "-o", "json"],
                 ["version", "-o="], ["help", "-ojson"],
                 ["-ovalue", "--help"], ["--output", "--help"],
                 ["profile", "add", "name", "http://127.0.0.1:1", "user", "--api-key=x"],
                 ["profile", "add", "name", "http://127.0.0.1:1", "user", "--api-key=x", "-ojson"]):
        # Unknown command/help resolution and invalid output never perform work.
        add("resolution-edge", argv, "errors", "offline-action")
    for unknown in ("bad\u0007", "bad\u001b", "bad\ncommand", "bad\u2028", "café", "\u00a0datset"):
        add("quoted-unknown", [unknown], "errors", "offline-action")
        add("quoted-unknown-json", [unknown, "-ojson"], "errors", "offline-action")
    # Go strconv.ParseInt(base=0) permits only specific underscore placement.
    for value in ("1_0", "0_7", "0x_ff"):
        add("go-integer-underscores", ["promql", "tsdb", "--top=" + value, "--help"])
    for value in ("++1", "--1", "1__0", "1_"):
        for output in ([], ["-ojson"]):
            add("go-integer-invalid-underscores", ["promql", "tsdb", "--top=" + value, *output],
                "errors", "parser-invalid")
    for argv in (["promql", "tsdb", "--top=x", "--top"],
                 ["promql", "tsdb", "--top=x", "--top", "-ojson"],
                 ["version", "-xa", "-xb"], ["version", "-ojson", "-xa", "-xb"],
                 ["version", "-ojson", "--pb-unknown-short-x", "-xab"]):
        add("parser-error-precedence", argv, "errors", "parser-invalid")
    for invalid in ("---", "--=x", "---foo", "-é", "-😀", "-\n", "-\t", "-'", "-\\"):
        for output in ([], ["-ojson"]):
            add("parser-error-quoting", ["version", *output, invalid], "errors", "parser-invalid")
    for unknown in ("İser", "İql"):
        for output in ([], ["-ojson"]):
            add("go-unicode-simple-lower", [unknown, *output], "errors", "offline-action")
    # An empty config causes these prerequisites to return before any body runs.
    operands = {"user set-role": ["phase1", "admin"], "sql save": ["SELECT 1"],
                "promql label-values": ["job"]}
    for node in surface["nodes"]:
        if not node.get("requires_profile"):
            continue
        path = node["path"]
        arguments = operands.get(" ".join(path), ["phase1"] * node.get("arity", [0, 0])[0])
        for structured in (False, True) if any(f["name"] == "output" for f in node["flags"]) else (False,):
            add("missing-profile-" + "-".join(path), [*path, *arguments, *(["-ojson"] if structured else [])],
                "preflight", "profile-preflight")
            cases[-1]["preflight"] = {"wrapped": node["wrapped_preflight"], "structured": structured, "status": False}
    for structured in (False, True):
        add("status-unconfigured", ["status", *(["-ojson"] if structured else [])], "preflight", "profile-preflight")
        cases[-1]["preflight"] = {"wrapped": False, "structured": structured, "status": True}
    # A non-runnable Cobra parent renders help before its own NoArgs check.
    for tail in (["foo"], ["foo", "bar"], ["BASH"], ["bashx"], ["--", "foo"]):
        add("completion-unknown-child-help", ["completion", *tail], "help", "offline-action")
    # ParseUint can detect range overflow before a later invalid byte/underscore.
    for value in ("99999999999999999999x", "99999999999999999999_", "18446744073709551615x",
                  "18446744073709551616x", "9223372036854775808x", "0xfffffffffffffffff_", "-99999999999999999999x"):
        for output in ([], ["-ojson"]):
            add("integer-range-before-syntax", ["promql", "tsdb", "--top=" + value, *output],
                "errors", "parser-invalid")
    # EqualFold's simple-fold matching is broader than lowercase normalization.
    for argv in (["version", "-o", "jſon"], ["version", "--output", "JſON"],
                 ["version", "-o", " jſon "], ["version", "-ojſon"],
                 ["version", "--output=jſon"], ["version", "-o=jſon"],
                 ["version", "--unknown-phase1", "-o", "jſon"],
                 ["version", "--", "-o", "jſon"]):
        add("output-unicode-simple-fold", argv, "errors", "offline-action")
    for chunk in unicode_quote_chunks():
        add("unicode-scalar-quote-sweep", ["version", "-o", chunk], "unicode", "offline-action")
        cases[-1]["unicode_scalar_range"] = [ord(chunk[0]), ord(chunk[-1])]
    return cases


def isolated_env(root):
    return {"PATH": os.defpath, "HOME": str(root), "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"), "XDG_DATA_HOME": str(root / "data"),
            "XDG_STATE_HOME": str(root / "state"), "TMPDIR": str(root / "tmp"),
            "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "TERM": "dumb",
            "PB_THEME": "dark", "PB_ANALYTICS": "disable"}


def snapshot(root):
    """Record every entry without following links or reading special devices."""
    result = {}
    kinds = {stat.S_IFREG: "file", stat.S_IFDIR: "directory", stat.S_IFLNK: "symlink",
             stat.S_IFIFO: "fifo", stat.S_IFSOCK: "socket", stat.S_IFCHR: "character-device",
             stat.S_IFBLK: "block-device"}

    def visit(path):
        metadata = path.lstat()
        kind = stat.S_IFMT(metadata.st_mode)
        entry = {"type": kinds.get(kind, "other"), "mode": oct(stat.S_IMODE(metadata.st_mode))}
        if kind == stat.S_IFREG:
            entry["sha256"] = digest(path.read_bytes())
        elif kind == stat.S_IFLNK:
            entry["target"] = os.readlink(path)
        elif kind in (stat.S_IFCHR, stat.S_IFBLK):
            entry["device"] = metadata.st_rdev
        result[str(path.relative_to(root))] = entry
        if kind == stat.S_IFDIR:
            for child in sorted(path.iterdir()):
                visit(child)

    visit(root)
    return result


def sample(binary, argv, timeout=5, seed_profile=False):
    with tempfile.TemporaryDirectory(prefix="pb-phase1-") as temporary:
        root = Path(temporary)
        for name in ("config", "cache", "data", "state", "tmp"):
            (root / name).mkdir()
        if seed_profile:
            folder = root / "config" / "pb"
            folder.mkdir()
            config = folder / "config.toml"
            config.write_text('DefaultProfile = "fixture"\n[Profiles.fixture]\nurl = "http://127.0.0.1:1"\n'
                              'username = "phase1"\npassword = "phase1"\ncloud = false\n')
            config.chmod(0o600)
        before = snapshot(root)
        started = time.monotonic()
        proc = subprocess.Popen([str(binary), *argv], cwd=root, env=isolated_env(root),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        data = {"stdout": bytearray(), "stderr": bytearray()}
        failure = None

        def kill():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        try:
            with selectors.DefaultSelector() as selector:
                for stream, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr)):
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ, stream)
                while proc.poll() is None or selector.get_map():
                    elapsed = time.monotonic() - started
                    if elapsed > timeout and not failure:
                        failure = "timeout"
                        kill()
                    if elapsed > timeout + 1:
                        failure = failure or "descendant-held-pipe"
                        break
                    if proc.poll() is not None:
                        kill()
                    for key, _ in selector.select(0.01):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        room = MAX_OUTPUT - sum(map(len, data.values()))
                        data[key.data].extend(chunk[:max(room, 0)])
                        if len(chunk) > room:
                            failure = failure or "output-limit"
                            kill()
        finally:
            kill()
            proc.wait()
            proc.stdout.close()
            proc.stderr.close()
        elapsed = (time.monotonic() - started) * 1000
        after = snapshot(root)
        if after != before:
            failure = failure or "filesystem-effects"
        return {"exit_code": proc.returncode, "stdout": bytes(data["stdout"]), "stderr": bytes(data["stderr"]),
                "failure": failure, "wall_ms": elapsed, "files_before": before, "files_after": after}


def binary_identity(path):
    path = Path(path).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"not an executable: {path}")
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path.read_bytes())}


def public_result(result):
    return {**{key: value for key, value in result.items() if key not in ("stdout", "stderr")},
            "stdout_sha256": digest(result["stdout"]), "stderr_sha256": digest(result["stderr"]),
            "stdout_bytes": len(result["stdout"]), "stderr_bytes": len(result["stderr"])}


def store_result(folder, result):
    folder.mkdir(parents=True)
    (folder / "stdout.bin").write_bytes(result["stdout"])
    (folder / "stderr.bin").write_bytes(result["stderr"])
    write_json(folder / "result.json", public_result(result))


def read_result(folder, expected=None):
    value = json.loads((folder / "result.json").read_text())
    for stream in ("stdout", "stderr"):
        value[stream] = (folder / (stream + ".bin")).read_bytes()
        if digest(value[stream]) != value[stream + "_sha256"]:
            raise ValueError(f"corrupt baseline transcript: {folder}/{stream}.bin")
    if expected is not None and public_result(value) != expected:
        raise ValueError(f"baseline transcript metadata differs from report: {folder}")
    return value


def differences(reference, candidate):
    issues = []
    for role, result in (("reference", reference), ("candidate", candidate)):
        if result.get("failure"):
            issues.append(role + ": " + result["failure"])
    if reference["exit_code"] != candidate["exit_code"]:
        issues.append(f"exit {candidate['exit_code']}, expected {reference['exit_code']}")
    for stream in ("stdout", "stderr"):
        expected, actual = reference[stream], candidate[stream]
        if expected != actual:
            offset = next((i for i, pair in enumerate(zip(expected, actual)) if pair[0] != pair[1]), min(len(expected), len(actual)))
            issues.append(f"{stream} differs at byte {offset}: candidate {len(actual)} bytes, reference {len(expected)} bytes")
    return issues


def surface_issues(tree, surface):
    """Fail if a new oracle changes reachability without updating the corpus."""
    actual = {}

    def visit(node):
        actual[tuple(node["command"].split()[1:])] = {
            "aliases": node.get("aliases", []),
            "flags": {f["name"]: f.get("shorthand") for f in node.get("flags", []) if f["name"] != "help"}}
        for child in node.get("subcommands", []):
            visit(child)

    visit(tree)
    expected = {tuple(n["path"]): {"aliases": n["aliases"], "flags": {f["name"]: f.get("short") for f in n["flags"]}}
                for n in surface["nodes"] if not n.get("hidden") and n["path"] != ["help"]}
    if actual != expected:
        return ["reference command tree differs from reviewed phase-one surface; update corpus explicitly"]
    return []


# Promoted commands are exercised by phase2.py/phase3.py against independent Go fixtures.
# This list is explicit: a candidate's output never selects its test coverage.
PHASE2_OPERATIONS = {
    "status", "profile add", "profile list", "profile update", "profile default",
    "profile remove", "logout", "cloud profile add", "dataset list",
    "dataset info", "dataset schema", "sql run",
}

PHASE4_OPERATIONS = {
    "sql save", "promql run", "promql labels", "promql label-values",
    "promql series", "promql cardinality label-names",
    "promql cardinality label-values", "promql cardinality active-series",
    "promql active-queries", "promql tsdb",
}

PHASE5_OPERATIONS = {
    "dataset remove", "user add", "user remove", "user set-role", "user list",
    "role remove", "role list",
}

PHASE6_OPERATIONS = {"tail"}


def deferred_guards(surface=None):
    """Candidate safety assertions, never Go parity or benchmark workloads."""
    surface = surface or load_surface()
    arguments = {"profile add": ["phase1", "http://127.0.0.1:1", "user", "password"],
                 "profile update": ["fixture", "http://127.0.0.1:1"], "profile default": ["fixture"],
                 "user set-role": ["phase1", "admin"], "sql save": ["SELECT 1"],
                 "promql label-values": ["job"]}
    guards = []
    nodes = surface["nodes"]
    for node in nodes:
        path = node["path"]
        if " ".join(path) in PHASE2_OPERATIONS | PHASE4_OPERATIONS | PHASE5_OPERATIONS | PHASE6_OPERATIONS:
            continue
        if not path or path in (["help"], ["agent"], ["version"]):
            continue
        if any(n["path"][:len(path)] == path and len(n["path"]) > len(path) for n in nodes):
            continue
        operands = arguments.get(" ".join(path), ["phase1"] * node.get("arity", [0, 0])[0])
        paths = [path, *[[*path[:-1], alias] for alias in node["aliases"]]]
        for invocation in paths:
            for structured in (False, True) if any(f["name"] == "output" for f in node["flags"]) else (False,):
                # Only the no-output picker remains deferred; explicit JSON
                # listing is covered by phase four's request/output corpus.
                if path == ["sql", "list"] and structured:
                    continue
                guards.append({"name": f"deferred-{len(guards):03d}-" + "-".join(invocation) + ("-json" if structured else "-text"),
                               "argv": [*invocation, *operands, *(["-ojson"] if structured else [])],
                               "canonical_path": "pb " + " ".join(path), "structured": structured})
    return guards


def guard_issues(guard, result):
    issues = []
    if result.get("failure"):
        issues.append(result["failure"])
    if result["exit_code"] != 1:
        issues.append("deferred operation must exit 1")
    if result["stdout"]:
        issues.append("deferred operation must not produce success stdout")
    message = 'command "' + guard["canonical_path"] + '" is not implemented in this Rust phase'
    if guard["structured"]:
        try:
            expected = {"error": {"code": "COMMAND_FAILED", "message": message, "retryable": False}}
            if json.loads(result["stderr"]) != expected:
                issues.append("deferred operation must identify explicit COMMAND_FAILED stub in JSON")
        except (ValueError, UnicodeError):
            issues.append("deferred operation error must be valid JSON")
    elif not result["stderr"].startswith(("Error: " + message + "\n").encode()):
        issues.append("deferred operation must identify explicit implementation stub")
    return issues


def preflight_issues(case, result):
    """Ensure new oracle captures actually stop at the intended prerequisite."""
    issues = []
    expectation = case["preflight"]
    message = "no profile configured. run: pb login"
    if result["exit_code"] != 1:
        issues.append("missing-profile prerequisite must exit 1")
    if expectation["structured"]:
        stream = "stdout" if expectation["status"] else "stderr"
        expected = ({"status": "error", "healthy": False, "error": message} if expectation["status"] else
                    {"error": {"code": "NOT_FOUND", "message": message, "retryable": False}})
        try:
            if json.loads(result[stream]) != expected:
                issues.append("reference did not produce reviewed missing-profile JSON prerequisite")
        except (ValueError, UnicodeError):
            issues.append("reference missing-profile JSON is invalid")
        if result["stderr" if stream == "stdout" else "stdout"]:
            issues.append("missing-profile prerequisite wrote unexpected output stream")
    else:
        prefix = "error initializing default profile: " if expectation["wrapped"] else ""
        if result["stdout"] or not result["stderr"].startswith(("Error: " + prefix + message + "\n").encode()):
            issues.append("reference did not produce reviewed missing-profile text prerequisite")
    return issues


def build_provenance(candidate):
    result = {}
    lock = HERE.parent / "Cargo.lock"
    if lock.exists():
        result["workspace_cargo_lock_sha256"] = digest(lock.read_bytes())
    if candidate and (rustc := shutil.which("rustc")):
        process = subprocess.run([rustc, "--version"], capture_output=True, timeout=10)
        result["observed_rustc_version"] = process.stdout.decode(errors="replace").strip()
    return result


def valid_saved_report(report, cases):
    return (report.get("schema_version") == SCHEMA_VERSION and report.get("complete") is True and report.get("passed") == len(cases)
            and len(report.get("cases", [])) == len(cases) and not report.get("identity_issues")
            and report.get("deferred_guards_passed", 0) == len(report.get("deferred_guards", []))
            and all(saved.get("name") == case["name"] and saved.get("argv") == case["argv"] and not saved.get("issues")
                    for saved, case in zip(report["cases"], cases)))


def benchmark_suite(cases, results, all_cases=False):
    return [{"name": case["name"], "group": case["group"], "argv": case["argv"],
             "profile": False, "fixture": None, "expected_exit": result["exit_code"], "expected_requests": []}
            for case, result in zip(cases, results) if all_cases or case["benchmark"]]


def write_report(out, report):
    write_json(out / "report.json", report)
    lines = [f"Phase-one {report['mode']}: {report['passed']}/{len(report['cases'])} cases passed.", "",
             f"Corpus SHA-256: `{report['corpus_sha256']}`.",
             f"Reference SHA-256: `{report['reference']['sha256']}`.",
             f"Declared Go source revision: `{report['provenance']['source_revision']}`.",
             "Exact stdout, stderr and process exit are compared without byte normalization.",
             "Every sample uses an empty HOME/XDG tree and rejects filesystem changes.",
             "This runner is not an OS network/filesystem sandbox; only reviewed help/parser paths are exercised.", ""]
    if report.get("candidate"):
        lines.append(f"Candidate SHA-256: `{report['candidate']['sha256']}`.")
        lines.append(f"Deferred safety guards: {report.get('deferred_guards_passed', 0)}/{len(report.get('deferred_guards', []))}; these are not Go compatibility cases.")
        if report.get("deferred_guards_skipped"):
            lines.append("PARITY ONLY: deferred safety guards skipped; this is not the full phase-one acceptance gate.")
    for issue in report.get("identity_issues", []):
        lines.append("FAIL: " + issue)
    for case in report["cases"]:
        if case["issues"]:
            lines.extend(["", f"FAIL `{case['name']}`: `{json.dumps(case['argv'])}`", *case["issues"]])
    for guard in report.get("deferred_guards", []):
        if guard["issues"]:
            lines.extend(["", f"DEFERRED GUARD FAIL `{guard['name']}`: `{json.dumps(guard['argv'])}`", *guard["issues"]])
    lines.extend(["", "Deferred behavior (excluded from compatibility claims):",
                  *[f"- {key}: {value}" for key, value in report["deferred"].items()], ""])
    (out / "report.md").write_text("\n".join(lines))


def run(args):
    surface = load_surface()
    cases = corpus(surface)
    out = Path(args.out).resolve()
    reference_results = []
    if getattr(args, "baseline", None):
        baseline = Path(args.baseline).resolve()
        saved = json.loads((baseline / "report.json").read_text())
        if not valid_saved_report(saved, cases):
            raise ValueError("baseline must be a complete passing capture/compare report with current schema version")
        if saved["corpus_sha256"] != digest(canonical(cases)):
            raise ValueError("baseline corpus differs from current phase-one corpus")
        if saved["surface_sha256"] != digest(SURFACE_PATH.read_bytes()):
            raise ValueError("baseline surface differs from current phase-one surface")
        reference = saved["reference"]
        for index, case in enumerate(cases):
            reference_results.append(read_result(baseline / "reference" / case["name"], saved["cases"][index]["reference"]))
    else:
        reference = binary_identity(args.reference)
        # Validate the public version; each capture pins exact binary identity.
        version = sample(reference["path"], ["--version"], args.timeout)
        expected = ("pb version " + surface["provenance"]["version"] + "\n").encode()
        if version["failure"] or version["exit_code"] != 0 or version["stdout"] != expected or version["stderr"]:
            raise ValueError("reference version must exactly match pinned " + surface["provenance"]["version"])
    candidate = binary_identity(args.candidate) if getattr(args, "candidate", None) else None
    if candidate and candidate["sha256"] == reference["sha256"] and not getattr(args, "parity_only", False):
        raise ValueError("Go self-comparison requires --parity-only; deferred guards must never execute the Go oracle")
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "corpus.json", cases)
    report = {"schema_version": SCHEMA_VERSION, "mode": args.command, "complete": False,
              "runner_sha256": digest(Path(__file__).read_bytes()),
              "created_unix_ns": time.time_ns(), "corpus_sha256": digest(canonical(cases)),
              "provenance": surface["provenance"], "surface_sha256": digest(SURFACE_PATH.read_bytes()),
              "reference": {**reference, "version_stdout": "pb version " + surface["provenance"]["version"] + "\n"},
              "candidate": candidate, "build_provenance": build_provenance(candidate), "timeout_seconds": args.timeout,
              "deferred": surface["deferred"], "cases": [], "passed": 0}
    live_reference = not reference_results
    for index, case in enumerate(cases):
        ref = sample(reference["path"], case["argv"], args.timeout) if live_reference else reference_results[index]
        if live_reference:
            reference_results.append(ref)
        store_result(out / "reference" / case["name"], ref)
        actual = sample(candidate["path"], case["argv"], args.timeout) if candidate else None
        if actual:
            store_result(out / "candidate" / case["name"], actual)
        issues = differences(ref, actual) if actual else (["reference: " + ref["failure"]] if ref["failure"] else [])
        if case["safety"] in ("parser-invalid", "arity-invalid") and ref["exit_code"] != 1:
            issues.append("reviewed invalid parser/arity case did not exit 1 in reference")
        if case.get("preflight"):
            issues.extend(preflight_issues(case, ref))
        if case["name"].endswith("-root-tree"):
            try:
                issues.extend(surface_issues(json.loads(ref["stdout"]), surface))
            except (ValueError, KeyError, TypeError):
                issues.append("reference command tree is invalid JSON metadata")
        item = {**case, "reference": public_result(ref), "issues": issues}
        if actual:
            item["candidate"] = public_result(actual)
        report["cases"].append(item)
        report["passed"] += not issues
        if issues:
            print(f"FAIL {case['name']}: {'; '.join(issues)}", file=sys.stderr)
        if (index + 1) % 100 == 0:
            print(f"phase1: {index + 1}/{len(cases)} cases processed", file=sys.stderr)
    report["deferred_guards"] = []
    report["deferred_guards_passed"] = 0
    report["deferred_guards_skipped"] = bool(getattr(args, "parity_only", False))
    if candidate and not report["deferred_guards_skipped"]:
        for guard in deferred_guards(surface):
            result = sample(candidate["path"], guard["argv"], args.timeout, seed_profile=True)
            store_result(out / "deferred-guards" / guard["name"], result)
            issues = guard_issues(guard, result)
            report["deferred_guards"].append({**guard, "candidate": public_result(result), "issues": issues})
            report["deferred_guards_passed"] += not issues
            if issues:
                print(f"DEFERRED GUARD FAIL {guard['name']}: {'; '.join(issues)}", file=sys.stderr)
    report["complete"] = True
    report["identity_issues"] = []
    for role, identity in (("reference", reference if live_reference else None), ("candidate", candidate)):
        if identity and binary_identity(identity["path"])["sha256"] != identity["sha256"]:
            report["identity_issues"].append(role + " executable changed during capture")
    suite = benchmark_suite(cases, reference_results)
    write_json(out / "benchmark-suite.json", suite)
    report["benchmark_suite_sha256"] = digest(canonical(suite))
    write_report(out, report)
    guards_note = f"; {report['deferred_guards_passed']}/{len(report['deferred_guards'])} deferred guards passed" if candidate else ""
    parity_note = "; parity-only check (full gate skipped)" if report["deferred_guards_skipped"] else ""
    print(f"{report['passed']}/{len(cases)} phase-one cases passed{guards_note}{parity_note}; report: {out / 'report.md'}")
    passed = report["passed"] == len(cases) and not report["identity_issues"]
    passed = passed and report["deferred_guards_passed"] == len(report["deferred_guards"])
    return 0 if passed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="list finite parser/help corpus")
    listing.add_argument("--json", action="store_true")
    for command in ("capture", "compare"):
        command_parser = sub.add_parser(command)
        if command == "compare":
            choice = command_parser.add_mutually_exclusive_group(required=True)
            choice.add_argument("--reference")
            choice.add_argument("--baseline", help="saved passing phase-one capture directory")
            command_parser.add_argument("--candidate", required=True)
            command_parser.add_argument("--parity-only", action="store_true", help="skip candidate stub guards for Go self-comparison; not a full phase-one gate")
        else:
            command_parser.add_argument("--reference", required=True)
        command_parser.add_argument("--out", required=True, help="new artifact directory")
        command_parser.add_argument("--timeout", type=float, default=5)
    exporter = sub.add_parser("suite", help="export a captured corpus for run.py --suite")
    exporter.add_argument("--baseline", required=True)
    exporter.add_argument("--out", required=True)
    exporter.add_argument("--all", action="store_true", help="export all cases instead of small benchmark subset")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            cases = corpus()
            if args.json:
                print(json.dumps(cases, indent=2))
            else:
                for case in cases:
                    print(case["name"], case["group"], json.dumps(case["argv"]))
                print(f"{len(cases)} cases; groups: {dict(collections.Counter(c['group'] for c in cases))}")
            return 0
        if args.command == "suite":
            baseline = Path(args.baseline).resolve()
            report = json.loads((baseline / "report.json").read_text())
            cases = json.loads((baseline / "corpus.json").read_text())
            if not valid_saved_report(report, cases):
                raise ValueError("suite export requires a complete passing baseline with current schema version")
            if report["corpus_sha256"] != digest(canonical(cases)):
                raise ValueError("baseline corpus hash mismatch")
            results = [read_result(baseline / "reference" / case["name"], report["cases"][index]["reference"])
                       for index, case in enumerate(cases)]
            output = Path(args.out).resolve()
            with output.open("x") as handle:
                json.dump(benchmark_suite(cases, results, args.all), handle, indent=2)
                handle.write("\n")
            return 0
        if not 0 < args.timeout <= 60:
            raise ValueError("timeout must be greater than zero and at most 60 seconds")
        return run(args)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print("phase1 setup error: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
