"""Tests for the phase-three SQL model, corpus, scripted fixture, runner, comparison and exports."""

import base64
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import phase3
import run

ROOT = phase3.HERE.parent
GO = ROOT / "bin" / "pb-go"
GO_FIXTURE = Path(os.environ.get("PB_PHASE3_FIXTURE", ROOT / "target" / "phase3-harness" / "pb-fixture"))


def by_name():
    return {case["name"]: case for case in phase3.corpus()}


class ModelTests(unittest.TestCase):
    def test_upstream_rewrite_examples(self):
        # cmd/query_test.go expectations (quoting only; LIMIT insertion is separate).
        normalize = lambda q: phase3.quote_fields_with_dots(phase3.quote_stream_names(q.encode())).decode()  # noqa: E731
        self.assertEqual(normalize("SELECT * FROM astronomy-shop-logs WHERE StatusCode = 'FailedPrecondition' AND service.name = 'cart' LIMIT 500"),
                         'SELECT * FROM "astronomy-shop-logs" WHERE "StatusCode" = \'FailedPrecondition\' AND "service.name" = \'cart\' LIMIT 500')
        self.assertEqual(phase3.quote_stream_names(b"SELECT * FROM fly_logs JOIN claudecode-logs ON fly_logs.id = claudecode-logs.id"),
                         b'SELECT * FROM fly_logs JOIN "claudecode-logs" ON fly_logs.id = claudecode-logs.id')
        self.assertEqual(phase3.quote_stream_names(b"SELECT * FROM 123logs LIMIT 100"), b'SELECT * FROM "123logs" LIMIT 100')
        self.assertEqual(normalize("SELECT COUNT(StatusCode) FROM astronomy-shop-logs WHERE StatusCode IS NOT NULL GROUP BY service.name LIMIT 10"),
                         'SELECT COUNT("StatusCode") FROM "astronomy-shop-logs" WHERE "StatusCode" IS NOT NULL GROUP BY "service.name" LIMIT 10')

    def test_default_limit_quirks(self):
        r = lambda q: phase3.rewrite_sql(q).decode()  # noqa: E731
        self.assertEqual(r("SELECT 1"), "SELECT 1 LIMIT 500")
        self.assertEqual(r("  SELECT 1 LIMIT 5  "), "  SELECT 1 LIMIT 5  ")
        self.assertEqual(r("SELECT 1;"), "SELECT 1 LIMIT 500;")
        self.assertEqual(r("SELECT 1 -- note"), "SELECT 1 -- note\nLIMIT 500")
        self.assertEqual(r("SELECT 1 --"), "SELECT 1 -- LIMIT 500\nLIMIT 500")  # bare -- is not idempotent
        self.assertEqual(r("SELECT 'abc"), "SELECT 'abc LIMIT 500 LIMIT 500")
        self.assertEqual(r("SELECT (1"), "SELECT (1 LIMIT 500 LIMIT 500")
        self.assertEqual(r("SELECT 1 "), "SELECT 1 LIMIT 500")
        self.assertEqual(r("SELECT t.limit FROM x"), 'SELECT "t.limit" FROM x LIMIT 500')
        self.assertEqual(r("SELECT * FROM dataſet"), "SELECT * FROM dataſet LIMIT 500")

    def test_go_trim_space_and_runes(self):
        self.assertEqual(phase3.go_trim_space(" 　 x  \u0085".encode()), b"x")
        self.assertEqual(phase3.go_trim_space(b" \x1c x \x1f "), b"\x1c x \x1f")  # not Go spaces
        self.assertEqual(phase3.go_trim_space(b"x \xc3"), b"x \xc3")
        self.assertEqual(phase3.decode_rune(b"\xed\xa0\x80"), (0xFFFD, 1))
        self.assertEqual(phase3.go_decode_utf8(b"\xe2\x82x"), "��x")

    def test_json_encoding(self):
        self.assertEqual(phase3.go_json_string("<&> \b\f\x01\x7f\"\\".encode()), b'"\\u003c\\u0026\\u003e\\u2028\\b\\f\\u0001\x7f\\"\\\\"')
        self.assertEqual(phase3.go_json_string(b"a\xffb"), b'"a\\ufffdb"')
        self.assertEqual(phase3.request_body("SELECT 1", "1m", "now"), b'{"query":"SELECT 1 LIMIT 500","startTime":"1m","endTime":"now"}')
        floats = [(0.0, "0"), (-0.0, "-0"), (1e-7, "1e-7"), (1e-6, "0.000001"), (1e20, "100000000000000000000"), (1e21, "1e+21"),
                  (5e-324, "5e-324"), (1.7976931348623157e308, "1.7976931348623157e+308"), (9007199254740993.0, "9007199254740992")]
        for value, text in floats:
            self.assertEqual(phase3.go_float(value), text)

    def test_pretty_and_text_outputs(self):
        self.assertEqual(phase3.json_output(b'[{"b":1,"a":[]},{}]'), b'[\n  {\n    "a": [],\n    "b": 1\n  },\n  {}\n]\n')
        self.assertEqual(phase3.json_output(b"[]"), b"[]\n")
        self.assertEqual(phase3.json_output(b" \n"), b"No response body returned (status: 200 OK).\n")
        self.assertEqual(phase3.json_output(b'[{"a":1,"a":2}] trailing'), b'[\n  {\n    "a": 2\n  }\n]\n')
        self.assertEqual(phase3.text_output(b"  [ \n ] \n"), b"Query succeeded: no rows returned (status: 200 OK).\n")
        self.assertEqual(phase3.text_output(b"[] x"), b"[] x\n")
        self.assertEqual(phase3.text_output(b"\x0c"), b"\x0c\n")
        self.assertEqual(phase3.text_output(b"", "200"), b"No response body returned (status: 200).\n")

    def test_error_preview(self):
        self.assertEqual(phase3.error_preview(b"e" * 65536), b"e" * 65536)
        self.assertEqual(phase3.error_preview(b"e" * 65537), b"e" * 65536 + b"...")
        cut = phase3.error_preview(b"e" * 65535 + "é".encode())
        self.assertTrue(cut.endswith(b"\xc3...") or cut.endswith(b"\xc3"))

    def test_generate_matches_go_fixture_semantics(self):
        spec = {"prefix": "[", "item": '{"sequence":{{i}},"pad":"{{fill}}","b":"{{blob}}"}', "separator": ",", "suffix": "]",
                "count": 3, "width": 2, "blob_bytes": 2}
        self.assertEqual(phase3.expand_generate(spec),
                         b'[{"sequence":0,"pad":"x","b":"xx"},{"sequence":1,"pad":"x","b":"xx"},{"sequence":2,"pad":"x","b":"xx"}]')
        rows = [json.loads(line) for line in [phase3.rows_body(3)]][0]
        self.assertEqual([r["sequence"] for r in rows], [0, 1, 2])
        self.assertEqual(len({len(json.dumps(r)) for r in json.loads(phase3.rows_body(2000))}), 1)


class CorpusTests(unittest.TestCase):
    def test_deterministic_unique_and_pinned(self):
        cases = phase3.corpus()
        self.assertEqual(run.canonical(cases), run.canonical(phase3.classify(phase3._build_corpus())))
        names = [c["name"] for c in cases]
        self.assertEqual(len(names), len(set(names)))
        for case in cases:
            self.assertRegex(case["name"], r"^p3-[a-z0-9-]+$")
            self.assertIn(case["tier"], phase3.TIERS)
            self.assertIn("termination", case["expect"])
            self.assertIn("requests", case["expect"])
            json.dumps(case)
        tiers = {t: sum(c["tier"] == t for c in cases) for t in phase3.TIERS}
        self.assertEqual(tiers["full60s"], 9)
        self.assertEqual(tiers["known-divergence"], len(phase3.KNOWN_DIVERGENCES))
        for case in cases:
            self.assertEqual(case["tier"] == "known-divergence", "divergence" in case)
        gate = {c["name"] for c in cases if c["tier"] == "gate"}
        for fixable in ("p3-text-truncated-chunked", "p3-json-truncated-after-elements", "p3-text-truncated-length"):
            self.assertIn(fixable, gate, "fixable body-EOF diagnostics must stay in the gate")
        for case in cases:
            if case["tier"] == "full60s":
                self.assertGreater(case["timeout"], 60)

    def test_required_coverage(self):
        cases = by_name()
        groups = {c["group"].removeprefix("phase3-") for c in cases.values()}
        for group in ("rewrite", "rewrite-seeded", "time", "validation", "profile", "request", "auth", "text", "json",
                      "json-errors", "json-seeded", "status", "preview", "redirect", "refresh", "causal", "early",
                      "chunking", "backpressure", "signals", "devices", "spinner", "large", "deadline"):
            self.assertIn(group, groups)
        statuses = {r.get("status") for c in cases.values() if c.get("fixture") for r in c["fixture"]["routes"]}
        self.assertTrue({201, 204, 299, 300, 301, 302, 303, 304, 307, 308, 400, 401, 403, 404, 408, 409, 418, 422, 429,
                         500, 502, 503, 504} <= statuses)
        routes = [r for c in cases.values() if c.get("fixture") for r in c["fixture"]["routes"]]
        self.assertTrue(any("reason" in r and r["reason"] is None for r in routes))
        self.assertTrue(any(r.get("gzip") for r in routes))
        self.assertTrue(any(s.get("abort") == "rst" for r in routes for s in r.get("script", [])))
        self.assertTrue(any("barrier" in s for r in routes for s in r.get("script", [])))
        self.assertTrue(any(c["build"] == "cloud" for c in cases.values()))
        self.assertTrue(any(c["build"] == "standard" and c["group"] == "phase3-refresh" for c in cases.values()))
        self.assertTrue(any(c.get("io", {}).get("stdout") == "devfull" for c in cases.values()))
        self.assertTrue(any(c.get("io", {}).get("stderr") == "pty" for c in cases.values()))
        self.assertIn("p3-early-completion-json-array-closed", cases)
        self.assertIn("p3-preview-early-completion-over-limit", cases)
        self.assertIn("p3-text-empty-array-then-fault", cases)

    def test_boundaries_and_scope(self):
        self.assertEqual([], phase3.deferred_boundaries())
        checks = phase3.historical_deferred_boundaries()
        argv = [" ".join(c["argv"]) for c in checks]
        self.assertTrue(any("-i" in a.split() for a in argv))
        self.assertTrue(any("--save-as" in a and "json" in a for a in argv))
        self.assertTrue(all(c["fixture"]["routes"] for c in checks), "routes record any forbidden request")
        with self.assertRaises(ValueError):
            phase3.select(phase3.corpus(), names=["p3-deadline-header-stall-text"])

    def test_benchmark_suite_is_runpy_compatible(self):
        suite = phase3.benchmark_suite()
        names = set()
        for entry in suite:
            self.assertRegex(entry["name"], r"^[A-Za-z0-9_-]+$")
            self.assertNotIn(entry["name"], names)
            names.add(entry["name"])
            self.assertEqual(entry["expected_requests"], ["POST /api/v1/query"])
            self.assertLessEqual(entry["max_output_bytes"], 100 << 20)
            self.assertIn("LIMIT", entry["argv"][2])
            if "generate" in entry["fixture"]["routes"][0]:
                self.assertIn("generate", entry["fixture_capabilities"])
                body = phase3.expand_generate(entry["fixture"]["routes"][0]["generate"])
                self.assertEqual(entry["fixture_response_body_bytes"], len(body))
        exceptions = [e for e in suite if e["group"] == "phase3-sql-memory-exceptions"]
        self.assertEqual(len(exceptions), 4)
        self.assertTrue(all(e["time_first_json_element"] is False for e in exceptions))
        self.assertTrue(any(e["name"].endswith("rows-100000-json") for e in suite))


class FixtureTests(unittest.TestCase):
    def serve(self, plan):
        server = phase3.FixtureServer(plan)
        server.start()
        self.addCleanup(server.close)
        return server, server.listeners[0][0].getsockname()[1]

    @staticmethod
    def raw(port, request=b"POST /api/v1/query HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}", read_until_close=True):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(request)
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    return data
                data += chunk
                if not read_until_close and b"\r\n\r\n" in data:
                    return data

    def test_reasonless_custom_status_and_trace(self):
        server, port = self.serve({"routes": [phase3.route(b"x", 500, reason=None, framing="close"),
                                              phase3.route(b"y", 200, path="/b", reason="Fine", framing="close")]})
        self.assertTrue(self.raw(port).startswith(b"HTTP/1.1 500\r\n"))
        self.assertTrue(self.raw(port, b"POST /b HTTP/1.1\r\nContent-Length: 0\r\n\r\n").startswith(b"HTTP/1.1 200 Fine\r\n"))
        self.assertEqual(base64.b64decode(server.trace[0]["body_b64"]), b"{}")
        self.assertEqual(server.trace[0]["framing"], "length")

    def test_chunk_script_barrier_and_abort(self):
        plan = {"routes": [phase3.route(b"abcdef", framing="chunked", script=[{"send": 2}, {"barrier": "go"}, {"send": 2}, {"abort": "fin"}])]}
        server, port = self.serve(plan)
        result = {}
        import threading
        thread = threading.Thread(target=lambda: result.setdefault("data", self.raw(port)))
        thread.start()
        deadline = time.time() + 5
        while not any(e["name"] == "barrier:go" for e in server.events) and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse("data" in result)
        server.barriers["go"].set()
        thread.join(5)
        body = result["data"].split(b"\r\n\r\n", 1)[1]
        self.assertEqual(body, b"2\r\nab\r\n2\r\ncd\r\n")  # no terminating chunk
        names = [e["name"] for e in server.milestones[0]["events"]]
        self.assertEqual(names[:3], ["received", "headers", "first-body"])
        self.assertIn("abort:fin", names)

    def test_gzip_and_corruption(self):
        import gzip
        server, port = self.serve({"routes": [phase3.route(b"hello", gzip=True, framing="close"),
                                              phase3.route(b"hello", path="/c", gzip=True, gzip_corrupt="crc", framing="close")]})
        head, body = self.raw(port).split(b"\r\n\r\n", 1)
        self.assertIn(b"Content-Encoding: gzip", head)
        self.assertEqual(gzip.decompress(body), b"hello")
        _, bad = self.raw(port, b"POST /c HTTP/1.1\r\nContent-Length: 0\r\n\r\n").split(b"\r\n\r\n", 1)
        with self.assertRaises(Exception):
            gzip.decompress(bad)

    def test_unexpected_and_auth(self):
        server, port = self.serve({"headers": {"x-api-key": "k"}, "routes": [phase3.route()]})
        self.assertIn(b" 401 ", self.raw(port, b"POST /api/v1/query HTTP/1.1\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")[:20])
        self.assertIn(b" 500 ", self.raw(port, b"GET /nope HTTP/1.1\r\nX-Api-Key: k\r\nConnection: close\r\n\r\n")[:20])
        self.assertEqual([t["unexpected"] for t in server.trace], [False, True])

    def test_go_fixture_generate_parity(self):
        if not GO_FIXTURE.is_file():
            self.skipTest("phase-three pb-fixture not built")
        spec = phase3.rows_spec(50)
        with tempfile.TemporaryDirectory() as folder:
            fixture = run.Fixture(GO_FIXTURE, {"routes": [{"method": "POST", "path": "/api/v1/query", "generate": spec}]}, Path(folder))
            try:
                request = urllib.request.Request(fixture.url + "/api/v1/query", data=b"{}", method="POST")
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(response.read(), phase3.expand_generate(spec))
                with opener.open(fixture.url + "/__harness/milestones", timeout=5) as response:
                    milestone = json.load(response)[0]
                self.assertEqual(milestone["uncompressed_body_bytes"], len(phase3.expand_generate(spec)))
            finally:
                fixture.close()
        phase3.check_fixture_capabilities(GO_FIXTURE)


SHELL = "/bin/sh"


class RunnerTests(unittest.TestCase):
    def execute(self, script, plan=None, timeout=5):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        return phase3.execute([SHELL, "-c", script], {"PATH": os.defpath}, folder, plan or {}, None, folder / "stdout.bin", timeout)

    def test_bytes_digest_and_elements(self):
        result = self.execute("printf '[\\n  {\"a\": 1},\\n  2\\n]\\n'; printf 'err' >&2")
        self.assertEqual(result["stdout"], b'[\n  {"a": 1},\n  2\n]\n')
        self.assertEqual(result["stdout_elements"], 2)
        self.assertEqual(result["stderr"], b"err")
        self.assertEqual(result["termination"], {"exit": 0})

    def test_close_stdout_produces_sigpipe(self):
        plan = {"actions": [{"when": {"stdout_bytes": 1}, "do": {"close": "stdout"}}]}
        result = self.execute("echo first; sleep 0.2; while :; do echo more; done", plan)
        self.assertEqual(result["termination"], {"signal": 13})
        self.assertTrue(result["actions"][0]["met"])

    def test_devfull_pty_and_timeout(self):
        if os.path.exists("/dev/full"):
            result = self.execute("echo x || exit 7", {"io": {"stdout": "devfull"}})
            self.assertEqual(result["termination"], {"exit": 7})
        result = self.execute("[ -t 2 ] && printf tty >&2", {"io": {"stderr": "pty"}})
        self.assertEqual(result["stderr"], b"tty")
        result = self.execute("sleep 5", timeout=0.3)
        self.assertEqual(result["failure"], "timeout")

    def test_unmet_required_action_is_reported(self):
        result = self.execute("true", {"actions": [{"when": {"stdout_bytes": 1}, "do": {"signal": "SIGINT"}, "within_ms": 50}]})
        self.assertFalse(result["actions"][0]["met"])


def fake_result(stderr=b"", stdout=b"[]\n", termination=None):
    return {"failure": None, "actions": [], "termination": termination or {"exit": 0}, "stdout": stdout,
            "stdout_normalized": stdout, "stdout_normalized_sha256": run.digest(stdout), "stdout_sha256": run.digest(stdout),
            "stdout_bytes": len(stdout), "stderr": stderr, "stderr_normalized": stderr, "requests": [], "trace": [],
            "config_before": {}, "config_after": {}, "wall_ms": 1, "milestones": [], "snapshots": {},
            "first_stdout_ms": None, "started_monotonic_ns": 0}


class ComparisonTests(unittest.TestCase):
    def test_spinner_clear_only_candidate_fails_activation_case(self):
        case = by_name()["p3-spinner-success-text"]
        ref = fake_result(b"\r| fetching...\r/ fetching...\r\x1b[K")
        self.assertEqual(phase3.differences(case, ref, copy.deepcopy(ref)), [])
        clear_only = fake_result(b"\r\x1b[K")
        self.assertTrue(any("spinner frames" in i for i in phase3.differences(case, ref, clear_only)))
        disordered = fake_result(b"\r/ fetching...\r| fetching...\r\x1b[K")
        self.assertTrue(any("out of order" in i for i in phase3.differences(case, ref, disordered)))

    def test_stub_and_blocked_bytes(self):
        case = by_name()["p3-blocked-reader-bounded-text"]
        ref, cand = fake_result(), fake_result()
        ref["snapshots"] = {"blocked": [{"body_bytes_sent": 1 << 20}]}
        cand["snapshots"] = {"blocked": [{"body_bytes_sent": (1 << 20) + phase3.BLOCKED_BYTES_MARGIN + 1}]}
        self.assertTrue(any("while stdout was blocked" in i for i in phase3.differences(case, ref, cand)))
        stub = fake_result(b"Error: x is not implemented in this Rust phase\n")
        self.assertTrue(any("stub" in i for i in phase3.differences(by_name()["p3-text-empty"], fake_result(), stub)))

    def test_boundary_check_rejects_go_like_behavior(self):
        check = phase3.historical_deferred_boundaries()[0]
        go_like = {**fake_result(stdout=b"rows\n"), "trace": [{"method": "POST"}], "requests": [{"method": "POST", "target": "/x"}]}
        issues = phase3.boundary_issues(check, go_like)
        self.assertTrue(any("network" in i for i in issues) and any("stdout" in i for i in issues))
        stub = fake_result(b'Error: command "pb sql run -i" is not implemented in this Rust phase\n', b"", {"exit": 1})
        stub["stdout_bytes"] = 0
        self.assertEqual(phase3.boundary_issues(check, stub), [])


class ManifestPlatformTests(unittest.TestCase):
    def test_other_platform_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "m.json"
            path.write_text(json.dumps({"schema_version": phase3.SCHEMA_VERSION, "platform": "plan9-mips", "cases": {}}))
            with self.assertRaises(ValueError):
                phase3.load_manifest(path)
            path.write_text(json.dumps({"schema_version": phase3.SCHEMA_VERSION, "platform": phase3.platform_id(), "cases": {}}))
            self.assertEqual(phase3.load_manifest(path)["cases"], {})


class DeadlineTierTests(unittest.TestCase):
    def test_out_of_tier_cases_are_unfrozen_not_missing(self):
        manifest = {"selected_tiers": ["gate", "known-divergence"], "cases": {}}
        self.assertEqual(phase3.manifest_issues(manifest, by_name()["p3-deadline-header-stall-text"], fake_result()), [])
        self.assertTrue(phase3.manifest_issues(manifest, by_name()["p3-text-empty"], fake_result()))

    def test_only_the_two_go_timeout_causes_are_normalized(self):
        case = by_name()["p3-deadline-body-stall-text"]
        legacy = b"Error: failed to stream response body: net/http: request canceled (Client.Timeout or context cancellation while reading body)\n"
        modern = legacy.replace(b"net/http: request canceled", b"context deadline exceeded")
        self.assertEqual(phase3.compare_stderr(case, legacy), phase3.compare_stderr(case, modern))
        other = legacy.replace(b"net/http: request canceled", b"unexpected EOF")
        self.assertNotEqual(phase3.compare_stderr(case, legacy), phase3.compare_stderr(case, other))
        self.assertNotEqual(phase3.compare_stderr(case, legacy), phase3.compare_stderr(case, legacy.replace(b"reading body", b"awaiting headers")))
        self.assertEqual(phase3.compare_stderr(by_name()["p3-text-empty"], legacy), legacy, "gate cases are not normalized")

    def test_legacy_bare_deadline_contexts_remain_case_specific(self):
        suffix = b" (Client.Timeout or context cancellation while reading body)"
        for name, context in (("p3-deadline-preview-needs-one-more-byte", b"failed to read error response body"),
                              ("p3-deadline-not-reset-per-chunk", b"failed to stream response body")):
            case = by_name()[name]
            bare = b"Error: " + context + b": context deadline exceeded\n"
            canonical = phase3.compare_stderr(case, bare[:-1] + suffix + b"\n")
            self.assertEqual(phase3.compare_stderr(case, bare), canonical)
            for bad in (bare.replace(b"context deadline exceeded", b"net/http: request canceled"),
                        bare + b"extra\n", bare.replace(context, b"wrong context"),
                        bare[:-1] + b" (Client.Timeout exceeded while awaiting headers)\n"):
                self.assertNotEqual(phase3.compare_stderr(case, bad), canonical)
            for other in ("p3-deadline-body-stall-json", "p3-text-empty", "p3-deadline-text-empty-array-lookahead"):
                self.assertEqual(phase3.compare_stderr(by_name()[other], bare), bare)
            other_legacy = ("p3-deadline-not-reset-per-chunk" if context == b"failed to read error response body"
                            else "p3-deadline-preview-needs-one-more-byte")
            self.assertEqual(phase3.compare_stderr(by_name()[other_legacy], bare), bare)
            # Deliberately reverse only the observed stream-phase negative.
            if context == b"failed to read error response body":
                self.assertEqual(phase3.compare_stderr(by_name()["p3-deadline-body-stall-text"], bare), bare)

    def test_body_stall_text_bare_deadline_has_an_exact_error_boundary(self):
        case = by_name()["p3-deadline-body-stall-text"]
        bare = b"Error: failed to stream response body: context deadline exceeded\n"
        suffixed = bare[:-1] + b" (Client.Timeout or context cancellation while reading body)\n"
        canceled = suffixed.replace(b"context deadline exceeded", b"net/http: request canceled")
        canonical = phase3.compare_stderr(case, suffixed)
        digest = phase3.manifest_entry(case, fake_result(suffixed))["stderr_sha256"]
        for value in (bare, suffixed, canceled):
            with self.subTest(value=value):
                self.assertEqual(phase3.compare_stderr(case, value), canonical)
                self.assertEqual(phase3.manifest_entry(case, fake_result(value))["stderr_sha256"], digest)
        for value in (bare.replace(b"context deadline exceeded", b"net/http: request canceled"),
                      bare[:-1], bare[:-1] + b"\r\n", bare + b"extra\n", b"extra\n" + bare,
                      bare.replace(b"failed to stream response body", b"failed to read response body"),
                      bare.replace(b"failed to stream response body", b"failed to read error response body"),
                      b'Error: request execution failed: Post "http://<FIXTURE>/api/v1/query": context deadline exceeded\n',
                      bare[:-1] + b" (Client.Timeout exceeded while awaiting headers)\n",
                      json.dumps({"error": bare.decode()}).encode()):
            with self.subTest(value=value):
                self.assertNotEqual(phase3.compare_stderr(case, value), canonical)
        for other in ("p3-deadline-body-stall-json", "p3-text-empty",
                      "p3-deadline-text-empty-array-lookahead", "p3-deadline-preview-needs-one-more-byte",
                      "p3-deadline-header-stall-text", "p3-deadline-spans-redirects"):
            self.assertEqual(phase3.compare_stderr(by_name()[other], bare), bare)
        for context in (b"failed to read response body", b"failed to read error response body"):
            wrong_phase = bare.replace(b"failed to stream response body", context)
            self.assertEqual(phase3.compare_stderr(case, wrong_phase), wrong_phase)
        for options in ({}, {"timeout_cause": False}):
            disabled = copy.deepcopy(case)
            disabled["compare"] = options
            self.assertEqual(phase3.compare_stderr(disabled, bare), bare)

    def test_body_stall_text_normalization_keeps_all_existing_gates(self):
        case = by_name()["p3-deadline-body-stall-text"]
        bare = b"Error: failed to stream response body: context deadline exceeded\n"
        reference = fake_result(bare, phase3.as_bytes(case["expect"]["stdout_exact"]), {"exit": 1})
        reference.update(wall_ms=60050, requests=[{
            "method": "POST", "target": "/api/v1/query",
            "body_b64": base64.b64encode(phase3.request_body("SELECT 1", phase3.START, phase3.END)).decode(),
            "headers": {"content-type": "application/json"}}])
        reference["trace"] = [{"method": "POST", "path": "/api/v1/query", "unexpected": False}]
        self.assertEqual(phase3.expectation_issues(case, reference), [])
        candidate = copy.deepcopy(reference)
        candidate["stderr"] = candidate["stderr_normalized"] = bare[:-1] + b" (Client.Timeout or context cancellation while reading body)\n"
        self.assertEqual(phase3.differences(case, reference, candidate), [])
        self.assertEqual(phase3.differences(case, candidate, reference), [], "bare candidate also accepted")
        for field, value in (("termination", {"exit": 0}), ("trace", []),
                             ("trace", reference["trace"] * 2),
                             ("trace", [{**reference["trace"][0], "unexpected": True}]),
                             ("stdout_normalized", b""), ("stdout_normalized", b"altered"),
                             ("wall_ms", 58000), ("wall_ms", 71000),
                             ("stderr_normalized", bare.replace(b"stream", b"read"))):
            wrong = copy.deepcopy(reference)
            wrong[field] = value
            with self.subTest(field=field, value=value):
                self.assertTrue(phase3.expectation_issues(case, wrong))
        for field in ("body_b64", "header", "config_after"):
            wrong = copy.deepcopy(candidate)
            if field == "body_b64":
                wrong["requests"][0][field] = base64.b64encode(b'{}').decode()
            elif field == "header":
                wrong["requests"][0]["headers"]["content-type"] = "text/plain"
            else:
                wrong[field] = {"injected": {"type": "directory", "mode": "0o700"}}
            with self.subTest(field=field):
                self.assertTrue(phase3.differences(case, reference, wrong))

    def test_redirect_bare_deadline_has_an_exact_case_and_error_boundary(self):
        case = by_name()["p3-deadline-spans-redirects"]
        bare = b'Error: request execution failed: Post "http://<FIXTURE>/v2/api/v1/query": context deadline exceeded\n'
        suffixed = bare[:-1] + b" (Client.Timeout exceeded while awaiting headers)\n"
        canceled = suffixed.replace(b"context deadline exceeded", b"net/http: request canceled")
        canonical = phase3.compare_stderr(case, suffixed)
        for value in (bare, suffixed, canceled):
            self.assertEqual(phase3.compare_stderr(case, value), canonical)
        for value in (bare.replace(b"/v2/", b"/"), bare.replace(b"<FIXTURE>", b"other"),
                      bare.replace(b"Post", b"Get"), bare.replace(b"context deadline exceeded", b"net/http: request canceled"),
                      bare[:-1], bare[:-1] + b"\r\n", bare + b"extra\n",
                      bare[:-1] + b" (Client.Timeout or context cancellation while reading body)\n",
                      json.dumps({"error": bare.decode()}).encode()):
            self.assertNotEqual(phase3.compare_stderr(case, value), canonical)
        for other in ("p3-deadline-header-stall-text", "p3-deadline-header-stall-json",
                      "p3-deadline-spans-refresh", "p3-text-empty"):
            self.assertEqual(phase3.compare_stderr(by_name()[other], bare), bare)
        disabled = copy.deepcopy(case)
        disabled["compare"]["timeout_cause"] = False
        self.assertEqual(phase3.compare_stderr(disabled, bare), bare)

    def test_redirect_normalization_keeps_exit_request_and_duration_gates(self):
        case = by_name()["p3-deadline-spans-redirects"]
        bare = b'Error: request execution failed: Post "http://<FIXTURE>/v2/api/v1/query": context deadline exceeded\n'
        reference = fake_result(bare, b"", {"exit": 1})
        reference.update(wall_ms=60050, requests=[
            {"method": "POST", "target": "/api/v1/query"},
            {"method": "POST", "target": "/v2/api/v1/query"}])
        reference["trace"] = [{"method": r["method"], "path": r["target"], "unexpected": False}
                              for r in reference["requests"]]
        self.assertEqual(phase3.expectation_issues(case, reference), [])
        candidate = copy.deepcopy(reference)
        candidate["stderr"] = candidate["stderr_normalized"] = bare[:-1] + b" (Client.Timeout exceeded while awaiting headers)\n"
        self.assertEqual(phase3.differences(case, reference, candidate), [])
        for field, value in (("wall_ms", 1000), ("wall_ms", 71000),
                             ("termination", {"exit": 0}), ("trace", reference["trace"][:1]),
                             ("trace", list(reversed(reference["trace"])))):
            wrong = copy.deepcopy(reference)
            wrong[field] = value
            self.assertTrue(phase3.expectation_issues(case, wrong), (field, value))

    def test_empty_array_lookahead_bare_deadline_exact_boundary(self):
        case = by_name()["p3-deadline-text-empty-array-lookahead"]
        bare = b"Error: failed to read response body: context deadline exceeded\n"
        suffixed = bare[:-1] + b" (Client.Timeout or context cancellation while reading body)\n"
        canceled = suffixed.replace(b"context deadline exceeded", b"net/http: request canceled")
        canonical = phase3.compare_stderr(case, suffixed)
        for value in (bare, suffixed, canceled):
            self.assertEqual(phase3.compare_stderr(case, value), canonical)
        for bad in (bare.replace(b"read response", b"stream response"),
                    bare.replace(b"context deadline exceeded", b"net/http: request canceled"),
                    bare[:-1], bare[:-1] + b"\r\n", bare + b"extra\n",
                    bare[:-1] + b" (Client.Timeout exceeded while awaiting headers)\n",
                    json.dumps({"error": bare.decode()}).encode()):
            self.assertNotEqual(phase3.compare_stderr(case, bad), canonical)
        for name in ("p3-deadline-preview-needs-one-more-byte", "p3-deadline-not-reset-per-chunk",
                     "p3-deadline-body-stall-text", "p3-deadline-body-stall-json",
                     "p3-deadline-spans-redirects", "p3-text-empty"):
            self.assertEqual(phase3.compare_stderr(by_name()[name], bare), bare)
        for bad in (bare.replace(b"read response", b"stream response"),
                    bare.replace(b"read response", b"read error response"), b"x\n" + bare,
                    b'Error: request execution failed: Post "http://<FIXTURE>/api/v1/query": context deadline exceeded\n'):
            self.assertEqual(phase3.compare_stderr(case, bad), bad)
        disabled = copy.deepcopy(case)
        disabled["compare"]["timeout_cause"] = False
        self.assertEqual(phase3.compare_stderr(disabled, bare), bare)

    def test_empty_array_lookahead_keeps_independent_and_differential_gates(self):
        case = by_name()["p3-deadline-text-empty-array-lookahead"]
        bare = b"Error: failed to read response body: context deadline exceeded\n"
        ref = fake_result(bare, b"", {"exit": 1})
        ref.update(wall_ms=60050, requests=[{"method": "POST", "target": "/api/v1/query",
                                           "headers": {"x-test": "one"}, "body_b64": "eA=="}],
                   trace=[{"method": "POST", "path": "/api/v1/query", "unexpected": False}])
        self.assertEqual(phase3.expectation_issues(case, ref), [])
        cand = copy.deepcopy(ref)
        cand["stderr"] = cand["stderr_normalized"] = bare[:-1] + b" (Client.Timeout or context cancellation while reading body)\n"
        self.assertEqual(phase3.differences(case, ref, cand), [])
        for key, value in (("termination", {"exit": 0}), ("wall_ms", 1000), ("wall_ms", 71000),
                           ("trace", []), ("trace", [{"method": "GET", "path": "/api/v1/query", "unexpected": False}]),
                           ("trace", [{"method": "POST", "path": "/api/v1/query", "unexpected": True}]),
                           ("stdout_normalized", b"x")):
            wrong = copy.deepcopy(cand)
            wrong[key] = value
            self.assertTrue(phase3.expectation_issues(case, wrong), (key, value))
        for mutate in (lambda r: r["requests"][0]["headers"].update({"x-test": "two"}),
                       lambda r: r["requests"][0].update(body_b64="eQ=="),
                       lambda r: r.update(config_after={"changed": {"type": "file", "raw_sha256": "different"}})):
            wrong = copy.deepcopy(cand)
            mutate(wrong)
            self.assertTrue(phase3.differences(case, ref, wrong))

    def test_paced_chunk_prefix_allowance_and_wall_bounds(self):
        case = by_name()["p3-deadline-not-reset-per-chunk"]
        stderr = b"Error: failed to stream response body: context deadline exceeded (Client.Timeout or context cancellation while reading body)\n"
        self.assertEqual(case["compare"]["stdout_lengths"], [15, 16])
        body = b"abcdefghijklmnopqrstuvwxyz"
        make = lambda n: {**fake_result(stderr, body[:n], {"exit": 1}), "wall_ms": 60100}  # noqa: E731
        ref, cand = make(15), make(16)
        self.assertEqual(phase3.differences(case, ref, cand), [])
        self.assertEqual(phase3.differences(case, cand, ref), [])
        for bad in (13, 14, 17, 18):
            self.assertTrue(phase3.differences(case, ref, make(bad)), bad)
            self.assertTrue(phase3.differences(case, make(bad), ref), f"reference length {bad}")
            self.assertTrue(any("paced-deadline" in i for i in phase3.expectation_issues(case, make(bad))), bad)
        self.assertTrue(phase3.differences(case, ref, {**cand, "stdout_normalized": b"abcdefghijklmnoX"}))
        self.assertEqual(phase3.paced_lengths(60000, 7000), [9])
        early = {**cand, "wall_ms": 30000}
        self.assertTrue(any("deadline fired early" in i for i in phase3.differences(case, ref, early)))


class BaselineTrustTests(unittest.TestCase):
    def saved_report(self, **changes):
        report = {"schema_version": phase3.SCHEMA_VERSION, "mode": "reference-check", "complete": True,
                  "selected_sha256": phase3.tiers_sha256(phase3.corpus(), ["gate"]), "scope": {"selected_tiers": ["gate"]},
                  "partial": False,
                  "cases": [{"name": "p3-x", "status": "pass"}], "binaries": {}}
        report.update(changes)
        return report

    def test_baseline_requires_passing_complete_capture(self):
        full = phase3.corpus()
        phase3.baseline_issues(self.saved_report(), full)
        for changes in ({"identity_issues": ["reference executable changed"]}, {"partial": True}, {"complete": False},
                        {"cases": [{"name": "p3-x", "status": "fail"}]}, {"selected_sha256": "0"}):
            with self.assertRaises(ValueError):
                phase3.baseline_issues(self.saved_report(**changes), full)
        with self.assertRaises(ValueError):  # a gate-only capture cannot serve the opt-in deadline tier
            phase3.baseline_issues(self.saved_report(), full, ("gate", "full60s"))
        changed = copy.deepcopy(full)
        next(c for c in changed if c["tier"] == "full60s")["timeout"] = 99
        phase3.baseline_issues(self.saved_report(), changed)  # other tiers may change

    def test_stored_artifacts_are_verified(self):
        case = by_name()["p3-text-empty"]
        ref = fake_result(b"err")
        ref["stdout_raw_digest"] = ref["stdout_sha256"]
        recorded = {**{k: ref[k] for k in ("termination", "stdout_sha256", "stdout_normalized_sha256", "stdout_bytes", "requests",
                                           "config_before", "config_after")},
                    "stderr_sha256": run.digest(b"err"), "stderr_raw_sha256": run.digest(b"err"),
                    "trace_sha256": run.digest(json.dumps([], sort_keys=True).encode()),
                    "milestones_sha256": run.digest(json.dumps([], sort_keys=True).encode())}
        entry = {"runs": {"reference": recorded}}
        ref.update(recorded)  # load_stored merges result.json, which is public(ref)
        frozen = phase3.manifest_entry(case, ref)
        self.assertEqual(phase3.stored_issues(ref, entry, frozen, case), [])
        for mutate in (lambda r: r.update(stdout_raw_digest="0"), lambda r: r.update(stderr=b"x"),
                       lambda r: r.update(config_before={"tampered": 1}), lambda r: r.update(trace=[{"m": 1}])):
            tampered = copy.deepcopy(ref)
            mutate(tampered)
            self.assertTrue(phase3.stored_issues(tampered, entry, frozen, case))
        self.assertTrue(phase3.stored_issues(ref, entry, None, case), "missing manifest entry must fail")


@unittest.skipUnless(GO.is_file(), "pinned Go reference not built")
class GoOracleTests(unittest.TestCase):
    def test_small_reference_subset_and_manifest_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "ref"
            names = ["p3-rewrite-trailing-bare-double-dash", "p3-text-invalid-utf8", "p3-json-numbers", "p3-status-no-reason-text",
                     "p3-causal-first-element-json", "p3-early-completion-json-array-closed"]
            args = [arg for name in names for arg in ("--case", name)]
            code = phase3.main(["check-reference", "--reference", str(GO), "--out", str(out), "--manifest", "none",
                                "--meter", "none", *args])
            self.assertEqual(code, 0, (out / "report.md").read_text())
            manifest = Path(folder) / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": phase3.SCHEMA_VERSION, "selected_tiers": ["gate"], "platform": phase3.platform_id(),
                                            "selected_sha256": phase3.tiers_sha256(phase3.corpus(), ["gate"]),
                                            "reference": "0" * 64, "cases": {}}))
            code = phase3.main(["check-reference", "--reference", str(GO), "--out", str(Path(folder) / "ref2"),
                                "--manifest", str(manifest), "--meter", "none", "--case", names[0]])
            self.assertEqual(code, 2, "a different reference executable must be rejected by the frozen manifest")


class HeaderStallTextDeadlineTests(unittest.TestCase):
    prefix = b'Error: request execution failed: Post "http://<FIXTURE>/api/v1/query": '
    suffix = b' (Client.Timeout exceeded while awaiting headers)'
    canonical = prefix + b'<CLIENT-TIMEOUT>' + suffix + b'\n'

    def forms(self):
        return [self.prefix + cause + b'\n' for cause in (
            b'context deadline exceeded', b'context deadline exceeded' + self.suffix,
            b'net/http: request canceled' + self.suffix)]

    def case(self):
        return by_name()['p3-deadline-header-stall-text']

    def result(self, stderr=None):
        result = fake_result(self.forms()[0] if stderr is None else stderr, b'', {'exit': 1})
        result.update(wall_ms=60050, requests=[{
            'method': 'POST', 'target': '/api/v1/query',
            'headers': {'content-type': 'application/json'},
            'body_b64': base64.b64encode(phase3.request_body('SELECT 1', phase3.START, phase3.END)).decode(),
            'framing': 'length', 'listener': 'primary'}],
            trace=[{'method': 'POST', 'path': '/api/v1/query', 'unexpected': False}])
        return result

    def wrong_streams(self):
        bare, suffixed, canceled = self.forms()
        return [bare.replace(b'context deadline exceeded', b'net/http: request canceled'),
                bare[:-1], bare[:-1] + b'\r\n', b'x\n' + bare, bare + b'x\n',
                suffixed[:-1], suffixed[:-1] + b'\r\n', b'x\n' + suffixed, suffixed + b'x\n',
                suffixed.replace(b'Post', b'Get'), suffixed.replace(b'<FIXTURE>', b'other'),
                suffixed.replace(b'/api/v1/query', b'/v2/api/v1/query'),
                suffixed.replace(b'/api/v1/query', b'/api/v1/query?x=1'),
                suffixed.replace(b'http://', b'https://'),
                suffixed.replace(b'context deadline exceeded', b'i/o timeout'),
                suffixed.replace(self.suffix, b' (Client.Timeout or context cancellation while reading body)'),
                bare.replace(b'context deadline exceeded', b'CONTEXT DEADLINE EXCEEDED'),
                json.dumps({'error': bare.decode()}).encode(),
                b'Error: failed to stream response body: context deadline exceeded\n',
                b'Error: failed to read response body: context deadline exceeded\n',
                b'Error: failed to read error response body: context deadline exceeded\n',
                b'\r| fetching...' + suffixed, suffixed + b'Usage:\n',
                suffixed.replace(b'Error: ', b'Error: HTTP 500: '),
                canceled + b'x\n']

    def test_three_exact_streams_and_manifest_digests(self):
        case = self.case()
        expected_digest = run.digest(self.canonical)
        for value in self.forms():
            self.assertEqual(phase3.normalize_timeout(case, value), self.canonical)
            self.assertEqual(phase3.compare_stderr(case, value), self.canonical)
            self.assertEqual(phase3.manifest_entry(case, self.result(value))['stderr_sha256'], expected_digest)
            self.assertEqual(phase3.expectation_issues(case, self.result(value)), [])
        for left in self.forms():
            for right in self.forms():
                self.assertEqual(phase3.differences(case, self.result(left), self.result(right)), [])

    def test_whole_stream_negatives_and_directional_stderr_gates(self):
        case = self.case()
        for value in self.wrong_streams():
            with self.subTest(value=value):
                self.assertEqual(phase3.normalize_timeout(case, value), value)
                self.assertTrue(phase3.expectation_issues(case, self.result(value)))
                for left, right in ((self.forms()[1], value), (value, self.forms()[1])):
                    self.assertTrue(any('stderr' in x for x in phase3.differences(
                        case, self.result(left), self.result(right))))
        # Sharing changed output on both sides must not collapse its two causes.
        left, right = self.forms()[1] + b'x\n', self.forms()[2] + b'x\n'
        for a, b in ((left, right), (right, left)):
            self.assertTrue(any('stderr' in x for x in phase3.differences(case, self.result(a), self.result(b))))

    def test_exact_case_scope_even_with_synthetic_timeout_opt_in(self):
        for other in [*by_name().values(), {'name': 'invented-deadline-header-stall-text'}]:
            if other['name'] == self.case()['name']:
                continue
            other = copy.deepcopy(other)
            other['compare'] = {'timeout_cause': True}
            self.assertEqual(phase3.normalize_timeout(other, self.forms()[0]), self.forms()[0])
            # These foreign suffixed streams retain the legacy behavior, rather
            # than taking this case's early return for unrecognized streams.
            wrong = self.forms()[1].replace(b'Post', b'Get')
            expected = (wrong if other['name'] == 'p4-deadline-header' else
                        wrong.replace(b'context deadline exceeded', b'<CLIENT-TIMEOUT>'))
            self.assertEqual(phase3.normalize_timeout(other, wrong), expected)

    def test_timeout_opt_in_is_required_for_all_forms(self):
        for options in ({}, {'timeout_cause': False}):
            case = copy.deepcopy(self.case())
            case['compare'] = options
            for value in self.forms():
                self.assertEqual(phase3.normalize_timeout(case, value), value)
        case = copy.deepcopy(self.case())
        del case['compare']
        for value in self.forms():
            self.assertEqual(phase3.normalize_timeout(case, value), value)

    def test_independent_exit_stdout_request_and_inclusive_wall_gates(self):
        case, good = self.case(), self.result()
        self.assertEqual(phase3.expectation_issues(case, good), [])
        for field, value in (('termination', {'exit': 0}), ('trace', []),
                             ('trace', good['trace'] * 2),
                             ('trace', [{**good['trace'][0], 'method': 'GET'}]),
                             ('trace', [{**good['trace'][0], 'path': '/wrong'}]),
                             ('trace', [{**good['trace'][0], 'unexpected': True}]),
                             ('stdout_normalized', b'x'), ('wall_ms', 58999), ('wall_ms', 70001),
                             ('failure', 'capture failed')):
            bad = copy.deepcopy(good)
            bad[field] = value
            if field == 'stdout_normalized':
                bad.update(stdout=value, stdout_bytes=len(value),
                           stdout_sha256=run.digest(value), stdout_normalized_sha256=run.digest(value))
            self.assertTrue(phase3.expectation_issues(case, bad), (field, value))
            if field not in ('wall_ms', 'failure'):
                self.assertEqual(phase3.differences(case, bad, copy.deepcopy(bad)), [])
        for wall in (59000, 70000):
            good['wall_ms'] = wall
            self.assertEqual(phase3.expectation_issues(case, good), [])
            self.assertEqual(phase3.differences(case, good, copy.deepcopy(good)), [])

    def test_differential_requests_configuration_stdout_exit_and_wall_gates(self):
        case, ref = self.case(), self.result(self.forms()[1])
        for kind in ('body', 'header', 'framing', 'listener', 'extra-request', 'missing-request',
                     'config_before', 'config_after', 'stdout', 'exit', 'early', 'late'):
            bad = self.result()
            if kind == 'body':
                bad['requests'][0]['body_b64'] = 'eA=='
            elif kind == 'header':
                bad['requests'][0]['headers']['content-type'] = 'text/plain'
            elif kind in ('framing', 'listener'):
                bad['requests'][0][kind] = 'changed'
            elif kind == 'extra-request':
                bad['requests'] *= 2
            elif kind == 'missing-request':
                bad['requests'] = []
            elif kind.startswith('config_'):
                bad[kind] = {'changed': {'type': 'file', 'raw_sha256': 'different'}}
            elif kind == 'stdout':
                bad.update(stdout=b'x', stdout_normalized=b'x', stdout_bytes=1,
                           stdout_sha256=run.digest(b'x'), stdout_normalized_sha256=run.digest(b'x'))
            elif kind == 'exit':
                bad['termination'] = {'exit': 0}
            else:
                bad['wall_ms'] = 58999 if kind == 'early' else 70001
            self.assertTrue(phase3.differences(case, ref, bad), kind)
            if kind in ('body', 'header', 'framing', 'listener', 'config_before', 'config_after'):
                # Existing scope: these fields are differential, not independent pins.
                self.assertEqual(phase3.expectation_issues(case, bad), [])
                self.assertEqual(phase3.differences(case, bad, copy.deepcopy(bad)), [])


if __name__ == "__main__":
    unittest.main()
