"""Tests for the phase-two corpus, stateful fixture, differential comparison and process execution."""

import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import phase2
import run

ROOT = phase2.HERE.parent
GO = ROOT / "bin" / "pb-go"
FIXTURE = ROOT / "bin" / "pb-fixture"
CLOUD_GO = Path(os.environ.get("PB_PHASE2_CLOUD_REFERENCE", ROOT / "bin" / "pb-go-cloud"))


def by_name():
    return {case["name"]: case for case in phase2.corpus()}


def argvs(case):
    return [step["argv"] for step in case["steps"]]


class CorpusTests(unittest.TestCase):
    def test_names_are_unique_safe_and_corpus_is_deterministic(self):
        cases = phase2.corpus()
        names = [case["name"] for case in cases]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(re.fullmatch(r"p2-[a-z0-9-]+", name) for name in names))
        self.assertEqual(run.canonical(cases), run.canonical(phase2.corpus()))
        for case in cases:
            self.assertTrue(case["steps"])
            for step in case["steps"]:
                self.assertIn("exit", step["expect"])
                self.assertIn("requests", step["expect"])

    def test_required_behaviors_are_covered(self):
        cases = by_name()
        for mode in phase2.AUTH:
            for output in ("json", "text"):
                self.assertIn(f"p2-status-{mode}-{output}", cases)
            self.assertIn(f"p2-dataset-list-{mode}-json", cases)
        commands = {tuple(argv[:2]) for case in cases.values() for argv in argvs(case)}
        for command in (("dataset", "list"), ("dataset", "ls"), ("dataset", "info"), ("dataset", "stat"),
                        ("dataset", "schema"), ("profile", "add"), ("profile", "update"), ("profile", "set-url"),
                        ("profile", "default"), ("profile", "remove"), ("profile", "rm"), ("profile", "list"),
                        ("profile", "ls"), ("logout", "--yes"), ("cloud", "profile")):
            self.assertIn(command, commands)
        self.assertIn(["login"], [argv for case in cases.values() for argv in argvs(case)])
        for command in ("list", "info", "schema"):
            for output in ("json", "text"):
                matching = [c for c in cases.values() if c["steps"][0]["argv"][:2] == ["dataset", command]
                            and ("-o" in c["steps"][0]["argv"] or "-ojson" in c["steps"][0]["argv"]) == (output == "json")]
                self.assertTrue(any(c["steps"][0]["expect"]["exit"] == 0 for c in matching), (command, output))
                self.assertTrue(any(c["steps"][0]["expect"]["exit"] == 1 for c in matching), (command, output))
        statuses = {r.get("status") for c in cases.values() if c.get("fixture")
                    for r in c["fixture"]["scenario"]["routes"]}
        self.assertTrue({400, 401, 403, 404, 409, 418, 429, 500, 502, 503} <= statuses)
        groups = {c["group"] for c in cases.values()}
        self.assertTrue({"phase2-refresh", "phase2-interop", "phase2-config-read"} <= groups)
        refresh = [c for c in cases.values() if c["group"] == "phase2-refresh"]
        self.assertTrue(any(c["steps"][0]["expect"]["exit"] == 0 and len(c["steps"][0]["expect"]["requests"]) == 3 for c in refresh))
        self.assertTrue(any(c["build"] == "standard" for c in refresh), "unconfigured-token refresh path must run in CI")
        for legacy in ("p2-legacy-token-legacy", "p2-legacy-api-key-legacy", "p2-legacy-apikey-wins", "p2-profile-add-migrates-legacy"):
            self.assertIn(legacy, cases)
        malformed = [c for c in cases.values() if (c.get("config") or {}).get("toml", "").count("[") and
                     "toml: " in json.dumps(c["steps"][0]["expect"])]
        self.assertGreaterEqual(len(malformed), 2)

    def test_interop_chains_cover_every_config_writer(self):
        chains = [c for c in phase2.corpus() if len(c["steps"]) > 1]
        writers = {tuple(c["steps"][0]["argv"][:2]) for c in chains}
        for writer in (("profile", "add"), ("profile", "default"), ("profile", "update"), ("profile", "remove"),
                       ("logout", "--yes"), ("cloud", "profile"), ("status", "-o")):
            self.assertIn(writer, writers)
        for chain in chains:
            plans = dict(phase2.assignments(chain, "R", "C"))
            self.assertEqual(["C"] * len(chain["steps"]), plans["candidate"])
            self.assertEqual(["R", "C"], plans["reference-then-candidate-1"][:2])
            self.assertEqual(["C", "R"], plans["candidate-then-reference-1"][:2])
            self.assertEqual(1 + 2 * (len(chain["steps"]) - 1), len(plans))
        self.assertEqual([("candidate", ["R", "R"])], phase2.assignments(chains[0], "R", "R"))

    def test_only_synthetic_loopback_or_invalid_endpoints_and_credentials(self):
        text = json.dumps(phase2.corpus(), ensure_ascii=False)
        for url in re.findall(r"[a-zA-Z]+://[^\s\"'\\]+", text):
            host = re.match(r"[a-zA-Z]+://(?:[^@/]*@)?(\[[^\]]*\]?|[^/:?#]*)", url).group(1).lower()
            self.assertTrue(host in ("127.0.0.1", "localhost", "[::1", "{{url}}", "<fixture>") or host.endswith(".invalid"), url)
        self.assertNotRegex(text, r"psk_[A-Za-z0-9]{20,}")

    def test_benchmark_subset_is_valid_run_py_suite(self):
        suite = phase2.benchmark_suite(phase2.corpus())
        self.assertGreaterEqual(len(suite), 12)
        self.assertEqual({"basic", "api-key", "cloud-key", "cloud-cookie"}, {c["auth"] for c in suite if c.get("auth")} )
        for case in suite:
            self.assertTrue(set(case) <= {"name", "group", "argv", "profile", "auth", "fixture", "expected_exit", "expected_requests"})
            if case["fixture"]:
                self.assertEqual([r["method"] + " " + r["path"] for r in case["fixture"]["routes"]], case["expected_requests"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "suite.json"
            self.assertEqual(0, phase2.main(["suite", "--out", str(path)]))
            self.assertEqual(suite, json.loads(path.read_text()))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, phase2.main(["suite", "--out", str(path)]))
        bad = [{**phase2.corpus()[0], "benchmark": True, "build": "cloud"}]
        with self.assertRaises(ValueError):
            phase2.benchmark_suite(bad)


FUTURE = {"p2-logout-prompt-eof", "p2-logout-prompt-no", "p2-logout-prompt-yes", "p2-logout-prompt-y-no-newline",
          "p2-login-without-terminal", "p2-login-without-terminal-no-config"}


class ScopeTests(unittest.TestCase):
    def test_exactly_six_future_terminal_cases_are_retained_but_outside_the_gate(self):
        cases = phase2.corpus()
        self.assertEqual(225, len(cases))
        future = {c["name"] for c in cases if c["scope"] == phase2.FUTURE_TERMINAL}
        self.assertEqual(FUTURE, future)
        self.assertEqual({phase2.PHASE2_SCOPE, phase2.FUTURE_TERMINAL}, {c["scope"] for c in cases})
        for case in cases:
            self.assertEqual(case["scope"] != phase2.PHASE2_SCOPE, "scope_reason" in case, case["name"])
        # Definitions keep their full Go expectations so the opt-in run stays meaningful.
        named = by_name()
        self.assertEqual({"only": ["cloud"]}, named["p2-logout-prompt-yes"]["steps"][0]["expect"]["config"])
        self.assertIn("could not open a new TTY", json.dumps(named["p2-login-without-terminal-no-config"]["steps"][0]["expect"]))
        self.assertTrue(all(not named[name]["benchmark"] for name in FUTURE))

    def test_selection_excludes_future_cases_unless_opted_in(self):
        cases = phase2.corpus()
        default = phase2.select(cases)
        self.assertEqual(219, len(default))
        self.assertFalse(FUTURE & {c["name"] for c in default})
        self.assertEqual(225, len(phase2.select(cases, include_future=True)))
        with self.assertRaisesRegex(ValueError, "--include-future-terminal"):
            phase2.select(cases, ["p2-login-without-terminal"])
        with self.assertRaisesRegex(ValueError, "no cases selected"):
            phase2.select(cases, groups=["login"])
        self.assertEqual(2, len(phase2.select(cases, groups=["login"], include_future=True)))
        logout = phase2.select(cases, groups=["logout"])
        self.assertTrue(logout and all("--yes" in c["steps"][0]["argv"] or "-o" in c["steps"][0]["argv"] for c in logout))

    def test_benchmark_subset_never_measures_future_cases(self):
        suite = {entry["name"] for entry in phase2.benchmark_suite(phase2.corpus())}
        self.assertFalse(FUTURE & suite)
        future = {**by_name()["p2-logout-prompt-eof"], "benchmark": True}
        with self.assertRaisesRegex(ValueError, "only phase-two"):
            phase2.benchmark_suite([future])
        escaped = {**by_name()["p2-status-basic-json"]}
        escaped["steps"] = [{**escaped["steps"][0], "expect": {**escaped["steps"][0]["expect"], "requests": ["GET /a b%2F"]}}]
        with self.assertRaisesRegex(ValueError, "escaped"):
            phase2.benchmark_suite([escaped])

    def test_boundaries_watch_network_and_every_writable_location(self):
        self.assertEqual([], phase2.deferred_boundaries())
        checks = phase2.historical_deferred_boundaries()
        self.assertEqual({name.replace("p2-", "p2-boundary-", 1) for name in FUTURE}, {c["name"] for c in checks})
        for check in checks:
            source = by_name()[check["source_case"]]
            self.assertEqual(source["steps"][0]["argv"], check["steps"][0]["argv"])
            self.assertEqual(source["steps"][0]["stdin"], check["steps"][0]["stdin"])
            self.assertEqual({"kind": "stateful", "scenario": {"routes": []}}, check["fixture"])
            if check["config"]:
                text = check["config"]["toml"]
                self.assertNotIn("127.0.0.1:1", text)  # every endpoint is the recording fixture
                self.assertIn('orchestrator_url = "{{url}}/orch"', text)
            else:
                self.assertIsNone(source["config"])
        self.assertEqual(" YeS \n", {c["source_case"]: c for c in checks}["p2-logout-prompt-yes"]["steps"][0]["stdin"])


class FixtureTests(unittest.TestCase):
    def test_stateful_fixture_sequences_routes_by_headers_and_uses(self):
        scenario = {"routes": [phase2.response("GET", "/a", "first", 401, headers={"cookie": "session=old"}, times=1),
                               phase2.response("POST", "/token", {"ok": "{{url}}"}),
                               phase2.response("GET", "/a", "second", headers={"cookie": "session=new"})]}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with tempfile.TemporaryDirectory() as folder:
            fixture = phase2.StatefulFixture(scenario, Path(folder))
            try:
                def call(method, path, cookie=None, body=None):
                    request = urllib.request.Request(fixture.url + path, data=body, method=method)
                    if cookie:
                        request.add_header("Cookie", cookie)
                    try:
                        with opener.open(request, timeout=5) as reply:
                            return reply.status, reply.read().decode()
                    except urllib.error.HTTPError as error:
                        with error:
                            return error.code, error.read().decode()
                self.assertEqual((401, "first"), call("GET", "/a", "session=old"))
                self.assertEqual(500, call("GET", "/a", "session=old")[0])  # times=1 exhausted
                self.assertEqual((200, json.dumps({"ok": fixture.url}, separators=(",", ":"))),
                                 call("POST", "/token", body=b'{"x":1}'))
                self.assertEqual((200, "second"), call("GET", "/a?q=1", "session=new"))
                trace = fixture.trace()
            finally:
                fixture.close()
        self.assertEqual([False, True, False, False], [r["unexpected"] for r in trace])
        contract = run.request_contract(trace)
        self.assertEqual({"json": {"x": 1}}, contract[2]["body"])
        self.assertEqual({"q": ["1"]}, contract[3]["query"])
        self.assertEqual("session=new", contract[3]["headers"]["cookie"])

    def test_stateful_fixture_enforces_global_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = phase2.StatefulFixture({"headers": {"x-api-key": "k"}, "routes": [phase2.response("GET", "/a", "{}")]}, Path(folder))
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    opener.open(fixture.url + "/a", timeout=5)
                with caught.exception as error:
                    self.assertEqual(401, error.code)
                    self.assertEqual(b"fixture: incorrect authentication\n", error.read())
            finally:
                fixture.close()


def result(stdout=b"", stderr=b"", exit_code=0, requests=(), config=None):
    trace = [{"protocol": "http", "method": m, "path": p, "headers": {}, "body": "", "time_unix_ns": 1, "unexpected": False}
             for m, p in (r.split(" ", 1) for r in requests)]
    config = config or {}
    return {"steps": [{"exit_code": exit_code, "termination": {"exit": exit_code}, "stdout": stdout, "stderr": stderr,
                       "stdout_normalized": stdout, "stderr_normalized": stderr, "failure": None, "trace": trace,
                       "requests": run.request_contract(trace), "binary": "x", "wall_ms": 1}],
            "config_before": {}, "config_after": config, "config_raw_after": None}


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.case = {"name": "p2-x", "steps": [{"argv": [], "expect": phase2.expect(0, ["GET /a"], out=["ok"])}]}

    def test_exact_stream_exit_request_and_config_differences(self):
        reference = result(b"ok\n", b"", 0, ["GET /a"], {"c": {"type": "file", "mode": "0o600", "toml": {"a": 1}, "raw_sha256": "1"}})
        self.assertEqual([], phase2.differences(self.case, reference, reference))
        variants = {"stdout": result(b"ok \n", b"", 0, ["GET /a"], reference["config_after"]),
                    "stderr": result(b"ok\n", b"warning\n", 0, ["GET /a"], reference["config_after"]),
                    "exit": result(b"ok\n", b"", 1, ["GET /a"], reference["config_after"]),
                    "requests": result(b"ok\n", b"", 0, ["GET /a?x=1"], reference["config_after"]),
                    "config after": result(b"ok\n", b"", 0, ["GET /a"], {"c": {"type": "file", "mode": "0o644", "toml": {"a": 1}, "raw_sha256": "1"}}),
                    "stub": result(b"ok\n", b'command "pb x" is not implemented in this Rust phase', 0, ["GET /a"], reference["config_after"])}
        for label, candidate in variants.items():
            with self.subTest(label=label):
                self.assertTrue(phase2.differences(self.case, reference, candidate))
        reformatted = result(b"ok\n", b"", 0, ["GET /a"], {"c": {"type": "file", "mode": "0o600", "toml": {"a": 1}, "raw_sha256": "2"}})
        reformatted["config_normalized_after"] = b"a=1\n"
        self.assertEqual([], phase2.differences(self.case, reference, reformatted))
        self.assertTrue(phase2.advisories(reference, reformatted))

    def test_rewrites_and_unparsed_file_changes_are_not_false_passes(self):
        def state(digest, parsed=True):
            entry = {"type": "file", "mode": "0o600", "raw_sha256": digest}
            entry.update({"toml": {"a": 1}} if parsed else {"toml_error": True})
            return {"config/pb/config.toml": entry}
        reference = result()
        reference["config_before"] = reference["config_after"] = state("1")
        rewritten = result()
        rewritten["config_before"], rewritten["config_after"] = state("9"), state("2")  # same TOML, new bytes
        self.assertIn("config/pb/config.toml rewritten although the reference left it byte-identical",
                      phase2.differences(self.case, reference, rewritten))
        kept = result()
        kept["config_before"] = kept["config_after"] = state("9")  # different run-specific seed bytes, untouched
        self.assertEqual([], phase2.differences(self.case, reference, kept))
        writer = result()
        writer["config_before"], writer["config_after"] = state("1"), state("2")
        self.assertEqual([], phase2.rewrite_issues(writer, rewritten))  # reference wrote too: only semantics gate
        broken = result()
        broken["config_before"] = broken["config_after"] = state("1", parsed=False)
        mangled = result()
        mangled["config_before"], mangled["config_after"] = state("1", parsed=False), state("3", parsed=False)
        self.assertTrue(phase2.differences(self.case, broken, mangled))
        self.assertEqual(["configuration changed"], phase2.config_issues("unchanged", rewritten))

    def test_unordered_stderr_requires_explicit_case_exception(self):
        reference = result(b"", b"a\nb\n")
        swapped = result(b"", b"b\na\n")
        self.assertTrue(phase2.differences(self.case, reference, swapped))
        excepted = {**self.case, "compare": {"stderr_unordered_lines": "map order"}}
        self.assertEqual([], phase2.differences(excepted, reference, swapped))
        self.assertTrue(phase2.differences(excepted, reference, result(b"", b"a\nc\n")))

    def test_reference_expectations_reject_unintended_behavior(self):
        self.assertEqual([], phase2.expectation_issues(self.case, result(b"ok\n", requests=["GET /a"])))
        self.assertTrue(phase2.expectation_issues(self.case, result(b"ok\n", requests=[])))
        self.assertTrue(phase2.expectation_issues(self.case, result(b"no\n", requests=["GET /a"])))
        self.assertTrue(phase2.expectation_issues(self.case, result(b"ok\n", exit_code=1, requests=["GET /a"])))
        unexpected = result(b"ok\n", requests=["GET /a"])
        unexpected["steps"][0]["trace"][0]["unexpected"] = True
        self.assertTrue(phase2.expectation_issues(self.case, unexpected))
        config = {"config/pb/config.toml": {"type": "file", "mode": "0o600", "toml": {
            "DefaultProfile": "a", "Profiles": {"a": {"url": "u", "apiKey": "k"}}}}}
        case = {"steps": [{"expect": phase2.expect(0, [], config={"default": "a", "only": ["a"], "mode": "0o600",
                                                                   "profiles": {"a": {"apiKey": "k", "token": None}}})}]}
        self.assertEqual([], phase2.expectation_issues(case, result(config=config)))
        for change in ({"DefaultProfile": "b"}, {"Profiles": {"a": {"apiKey": "x"}}}, {"Profiles": {"a": {"apiKey": "k", "token": "t"}}}):
            changed = {"config/pb/config.toml": {**config["config/pb/config.toml"],
                                                 "toml": {**config["config/pb/config.toml"]["toml"], **change}}}
            self.assertTrue(phase2.expectation_issues(case, result(config=changed)), change)

    def test_config_state_records_modes_and_unreadable_files_without_failing(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / "config" / "pb").mkdir(parents=True)
            path = home / "config" / "pb" / "config.toml"
            path.write_text('a = "/x/y"\n')
            state = phase2.config_state(home, {"/x/y": "<HOME>"})
            self.assertEqual({"a": "<HOME>"}, state["config/pb/config.toml"]["toml"])
            self.assertEqual("directory", state["config"]["type"])
            if os.geteuid():
                path.chmod(0)
                self.assertTrue(phase2.config_state(home, {})["config/pb/config.toml"]["unreadable"])
                path.chmod(0o600)
            path.write_bytes(b"[broken")
            self.assertTrue(phase2.config_state(home, {})["config/pb/config.toml"]["toml_error"])


FAKE = """#!{python}
import json, os, sys, tomllib, urllib.request
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("pb version 0.0.0-harness"); sys.exit(0)
config = Path(os.environ["XDG_CONFIG_HOME"]) / "pb" / "config.toml"
ROLE = {role!r}
if sys.argv[1] == "write":
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('DefaultProfile = "written"\\n' if ROLE == "reference" else "DefaultProfile='written'\\n")
    config.chmod(0o600)
    print("wrote")
elif sys.argv[1] == "read":
    text = config.read_text()
    if ROLE == "reference" and not text.startswith('DefaultProfile = "'):
        print("cannot read foreign config", file=sys.stderr); sys.exit(1)
    print(tomllib.loads(text)["DefaultProfile"])
elif sys.argv[1] == "status":
    profile = tomllib.loads(config.read_text())["Profiles"]["fixture"]
    request = urllib.request.Request(profile["url"] + "/api/v1/about", headers={{"x-api-key": profile["apiKey"]}})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({{}}))
    print(opener.open(request, timeout=5).read().decode() + (" stub is not implemented in this Rust phase" if ROLE == "stub" else ""))
"""


class ProcessTests(unittest.TestCase):
    def fake(self, folder, role):
        path = Path(folder) / role
        path.write_text(FAKE.format(python=sys.executable, role=role))
        path.chmod(0o700)
        return path

    def test_sample_runs_against_stateful_fixture_and_normalizes_only_run_specific_values(self):
        case = {"name": "p2-t", "group": "phase2-t", "build": "standard", "requires": [],
                "config": {"toml": phase2.config_toml({"fixture": phase2.profile("api-key")}), "mode": 0o600},
                "fixture": {"kind": "stateful", "scenario": {"routes": [phase2.response("GET", "/api/v1/about", "{{url}}/x")]}},
                "steps": [{"argv": ["status"], "env": {}, "unset": [], "stdin": "",
                           "expect": phase2.expect(0, ["GET /api/v1/about"], out_exact="http://<FIXTURE>/x\n", config="unchanged")}]}
        with tempfile.TemporaryDirectory() as folder:
            binary = self.fake(folder, "reference")
            sampled = phase2.sample([binary], case, None)
            self.assertEqual([], phase2.expectation_issues(case, sampled))
            self.assertIn(b"http://127.0.0.1:", sampled["steps"][0]["stdout"])
            self.assertEqual({"x-api-key": "fixture-key"}, sampled["steps"][0]["requests"][0]["headers"])
            stub = phase2.sample([self.fake(folder, "stub")], case, None)
            self.assertIn("candidate returned a deferred-operation stub", phase2.differences(case, sampled, stub))

    def compare_chain(self, reference, candidate, folder, extra=()):
        case = {"name": "p2-chain", "group": "phase2-interop", "build": "standard", "requires": [], "config": None, "scope": "phase2",
                "location": "xdg", "benchmark": False,
                "steps": [{"argv": ["write"], "env": {}, "unset": [], "stdin": "", "expect": phase2.expect(0, [], out_exact="wrote\n")},
                          {"argv": ["read"], "env": {}, "unset": [], "stdin": "", "expect": phase2.expect(0, [], out_exact="written\n")}]}
        out = Path(folder) / "out"
        with patch.object(phase2, "corpus", return_value=[case]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = phase2.main(["compare", "--reference", str(reference), "--candidate", str(candidate),
                                  "--out", str(out), "--fixture", "", *extra])
        return status, json.loads((out / "report.json").read_text())["cases"][0]

    def test_reverse_interoperability_detects_config_only_the_candidate_can_read(self):
        with tempfile.TemporaryDirectory() as folder:
            status, item = self.compare_chain(self.fake(folder, "reference"), self.fake(folder, "candidate"), folder)
        self.assertEqual(1, status)
        self.assertEqual({"reference", "candidate", "reference-then-candidate-1", "candidate-then-reference-1"}, set(item["runs"]))
        # Candidate alone and Go-write/candidate-read agree; only the reverse order exposes the incompatibility.
        failing = {issue.split(":")[0] for issue in item["issues"]}
        self.assertEqual({"candidate-then-reference-1"}, failing)
        runs = item["runs"]
        self.assertTrue(runs["candidate-then-reference-1"]["steps"][0]["binary"].endswith("candidate"))
        self.assertTrue(runs["candidate-then-reference-1"]["steps"][1]["binary"].endswith("reference"))
        self.assertTrue(item["advisories"])

    def test_identical_candidate_requires_explicit_self_check(self):
        with tempfile.TemporaryDirectory() as folder:
            reference = self.fake(folder, "reference")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, phase2.main(["compare", "--reference", str(reference), "--candidate", str(reference),
                                                 "--out", str(Path(folder) / "x"), "--fixture", ""]))
            status, item = self.compare_chain(reference, reference, folder, ["--self-check"])
        self.assertEqual(0, status)
        self.assertEqual({"reference", "candidate"}, set(item["runs"]))

    def test_unavailable_cloud_build_is_a_reported_skip_not_a_pass(self):
        case = {**phase2.corpus()[0], "build": "cloud", "benchmark": False}
        with tempfile.TemporaryDirectory() as folder, patch.object(phase2, "corpus", return_value=[case]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            reference = self.fake(folder, "reference")
            candidate = self.fake(folder, "candidate")
            argv = ["compare", "--reference", str(reference), "--candidate", str(candidate), "--fixture", str(reference)]
            self.assertEqual(1, phase2.main([*argv, "--out", str(Path(folder) / "a")]))
            self.assertEqual(0, phase2.main([*argv, "--out", str(Path(folder) / "b"), "--allow-skips"]))
            report = json.loads((Path(folder) / "b" / "report.json").read_text())
        self.assertEqual("skip", report["cases"][0]["status"])
        self.assertEqual({}, report["cases"][0]["runs"])

    def test_timeout_and_stdin_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "s"
            script.write_text("#!" + sys.executable + "\nimport sys,time\nprint(sys.stdin.read()[::-1])\nif sys.argv[1:]==['sleep']: time.sleep(10)\n")
            script.chmod(0o700)
            done = phase2.execute(script, [], {"PATH": os.defpath}, folder, b"abc", 5)
            self.assertEqual(b"cba\n", done["stdout"])
            slow = phase2.execute(script, ["sleep"], {"PATH": os.defpath}, folder, b"", 0.3)
            self.assertEqual("timeout", slow["failure"])
            self.assertEqual({"signal": 9}, slow["termination"])


BOUNDARY_FAKE = """#!{python}
import os, sys, urllib.request
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("pb version 0.0.0-harness"); sys.exit(0)
command = sys.argv[1]
config = Path(os.environ["XDG_CONFIG_HOME"]) / "pb" / "config.toml"
BEHAVIOR = {behavior!r}
if BEHAVIOR == "go-tty":
    sys.stderr.write("Error: could not open a new TTY: open /dev/tty: no such device or address\\n"); sys.exit(1)
if BEHAVIOR == "prompt":
    print("Continue? [y/N]: ", end=""); sys.stdin.read(); print("Logout canceled"); sys.exit(0)
if BEHAVIOR == "network" and config.exists():
    url = config.read_text().split('url = "')[1].split('"')[0]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({{}}))
    try:
        opener.open(url + "/api/v1/about", timeout=5)
    except Exception:
        pass
if BEHAVIOR == "touch-config" and config.exists():
    config.write_text(config.read_text() + "\\\\n")  # semantically identical rewrite
if BEHAVIOR == "cache":
    (Path(os.environ["XDG_CACHE_HOME"]) / "pb").mkdir()
which = "login" if BEHAVIOR == "wrong-command" and command == "logout" else command
sys.stderr.write(f"Error: interactive {{which}} is not implemented in this Rust phase\\nUsage:\\n  pb {{command}} [flags]\\n")
sys.exit(0 if BEHAVIOR == "exit-zero" else 1)
"""


class BoundaryTests(unittest.TestCase):
    def fake(self, folder, behavior):
        path = Path(folder) / behavior
        path.write_text(BOUNDARY_FAKE.format(python=sys.executable, behavior=behavior))
        path.chmod(0o700)
        return path

    def outcomes(self, behavior):
        with tempfile.TemporaryDirectory() as folder:
            binary = self.fake(folder, behavior)
            return {check["source_case"]: phase2.boundary_issues(check, phase2.sample([binary], check, None))
                    for check in phase2.historical_deferred_boundaries()}

    def test_explicit_effect_free_stub_passes_every_boundary(self):
        self.assertEqual({name: [] for name in FUTURE}, self.outcomes("stub"))

    def test_boundary_rejects_effects_go_behavior_and_vague_errors(self):
        expectations = {
            "go-tty": (FUTURE, "explicit"), "prompt": (FUTURE, "stdout must be empty"),
            "network": (FUTURE - {"p2-login-without-terminal-no-config"}, "network requests made"),
            "touch-config": (FUTURE - {"p2-login-without-terminal-no-config"}, "HOME changed"),
            "cache": (FUTURE, "HOME changed"), "exit-zero": (FUTURE, "expected exit 1"),
            "wrong-command": ({n for n in FUTURE if "logout" in n}, "explicit")}
        for behavior, (failing, needle) in expectations.items():
            with self.subTest(behavior=behavior):
                outcomes = self.outcomes(behavior)
                self.assertEqual(failing, {name for name, issues in outcomes.items() if issues})
                for name in failing:
                    self.assertTrue(any(needle in issue for issue in outcomes[name]), (name, outcomes[name]))

    def run_compare(self, folder, candidate, *extra):
        case = {"name": "p2-plain", "group": "phase2-t", "build": "standard", "requires": [], "config": None,
                "location": "xdg", "benchmark": False, "scope": phase2.PHASE2_SCOPE,
                "steps": [{"argv": ["login"], "env": {}, "unset": [], "stdin": "", "expect": phase2.expect(1, [])}]}
        future = {**by_name()["p2-login-without-terminal-no-config"]}
        out = Path(folder) / ("out-" + str(len(list(Path(folder).glob("out-*")))))
        with patch.object(phase2, "corpus", return_value=[case, future]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = phase2.main(["compare", "--reference", str(self.fake(folder, "go-tty")), "--candidate", str(candidate),
                                  "--out", str(out), "--fixture", "", *extra])
        return status, json.loads((out / "report.json").read_text()), (out / "report.md").read_text()

    def test_compare_reports_promotion_and_retains_ordinary_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            stub = self.fake(folder, "stub")
            # The stub differs from go-tty on p2-plain; isolate scope/boundary behavior via the opt-in run.
            status, report, text = self.run_compare(folder, stub)
            self.assertEqual(["p2-plain"], [c["name"] for c in report["cases"]])
            self.assertEqual(["p2-login-without-terminal-no-config"], [f["name"] for f in report["scope"]["future_terminal"]])
            self.assertFalse(report["scope"]["future_terminal_included"])
            self.assertEqual("promoted to phase7 PTY/effect gate", report["boundaries_status"])
            self.assertEqual([], report["boundaries"])
            self.assertIn("EXCLUDED as future terminal", text)
            self.assertIn("promoted to phase7 PTY/effect gate", text)
            self.assertEqual(1, status)  # p2-plain still fails: excluded cases never mask real differences
            status, report, text = self.run_compare(folder, stub, "--include-future-terminal")
            self.assertEqual(["p2-plain", "p2-login-without-terminal-no-config"], [c["name"] for c in report["cases"]])
            self.assertEqual("fail", report["cases"][1]["status"])  # opt-in runs the real Go comparison
            self.assertIn("OPT-IN", text)
            status, report, _ = self.run_compare(folder, stub, "--case", "p2-plain")
            self.assertEqual("not run (case/group filter)", report["boundaries_status"])

    def test_ordinary_gate_no_longer_requires_interactive_stubs(self):
        with tempfile.TemporaryDirectory() as folder:
            def gate(candidate):
                case = {"name": "p2-ok", "group": "phase2-t", "build": "standard", "requires": [], "config": None,
                        "location": "xdg", "benchmark": False, "scope": phase2.PHASE2_SCOPE,
                        "steps": [{"argv": ["--version"], "env": {}, "unset": [], "stdin": "", "expect": phase2.expect(0, [])}]}
                out = Path(folder) / ("out-" + candidate.name)
                with patch.object(phase2, "corpus", return_value=[case, by_name()["p2-login-without-terminal-no-config"]]), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    return phase2.main(["compare", "--reference", str(self.fake(folder, "go-tty")), "--candidate", str(candidate),
                                        "--out", str(out), "--fixture", ""])
            self.assertEqual(0, gate(self.fake(folder, "stub")))
            self.assertEqual(0, gate(self.fake(folder, "prompt")))


@unittest.skipUnless(GO.exists() and FIXTURE.exists(), "Go oracle and fixture not built")
class GoOracleTests(unittest.TestCase):
    def check(self, name, binary=GO):
        case = by_name()[name]
        sampled = phase2.sample([binary] * len(case["steps"]), case, FIXTURE)
        self.assertEqual([], phase2.expectation_issues(case, sampled), name)
        return sampled

    def test_reference_satisfies_representative_expectations(self):
        for name in ("p2-status-cloud-cookie-json", "p2-status-unauthorized-text", "p2-dataset-schema-json",
                     "p2-dataset-info-text", "p2-dataset-list-http-500-json", "p2-profile-add-basic-json",
                     "p2-profile-list-redacted-json", "p2-legacy-token-legacy", "p2-logout-yes-json-first-sorted",
                     "p2-refresh-unconfigured-build-json", "p2-interop-add-key-then-status"):
            with self.subTest(name=name):
                self.check(name)

    def test_future_terminal_definitions_still_hold_for_go_and_fail_boundaries(self):
        checks = {c["source_case"]: c for c in phase2.historical_deferred_boundaries()}
        for name in sorted(FUTURE):
            with self.subTest(name=name):
                self.check(name)
                sampled = phase2.sample([GO], checks[name], None)
                self.assertTrue(phase2.boundary_issues(checks[name], sampled))  # Go is not a stub: checks discriminate

    def test_go_self_comparison_is_exact_including_header_order_exception(self):
        case = by_name()["p2-status-debug-headers-redacted"]
        first = phase2.sample([GO], case, FIXTURE)
        second = phase2.sample([GO], case, FIXTURE)
        self.assertEqual([], phase2.differences(case, first, second))
        chain = by_name()["p2-interop-legacy-migration-then-status"]
        self.assertEqual([], phase2.differences(chain, phase2.sample([GO] * 3, chain, FIXTURE), phase2.sample([GO] * 3, chain, FIXTURE)))

    @unittest.skipUnless(CLOUD_GO.exists(), "Go reference with harness orchestrator token not built")
    def test_cloud_build_refreshes_and_persists_rotated_tokens(self):
        sampled = self.check("p2-refresh-status-json", CLOUD_GO)
        token = sampled["steps"][0]["requests"][1]
        self.assertEqual({"json": {"grant_type": "refresh_token", "refresh_token": "fixture-refresh"}}, token["body"])
        self.check("p2-interop-refresh-then-status", CLOUD_GO)
        self.check("p2-cloud-add-success-json", CLOUD_GO)


if __name__ == "__main__":
    unittest.main()
