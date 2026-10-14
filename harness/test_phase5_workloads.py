"""Offline checks for the phase-five workload harness; no pb binary is required.

TMPDIR=<disk-backed dir> python3 -m unittest discover -s harness -p test_phase5_workloads.py -v
"""

import argparse
import base64
import json
from pathlib import Path
import random
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import phase5 as model  # noqa: E402
import phase5_workloads as p5  # noqa: E402

METER = HERE.parent / "bin" / "pb-meter"
BASIC = p5.STATIC_AUTH["basic"]


def entry(method, rel, status=200, auth="static", body=b"", headers=None, **times):
    path = rel if rel.startswith("/") else p5.API + rel
    return {"method": method, "uri": path, "path": path, "status": status, "auth": auth,
            "body_b64": base64.b64encode(body).decode(), "headers": headers or {"content-type": "application/json"},
            **times}


def by_name(name, seed=p5.DEFAULT_SEED):
    return next(w for w in p5.workloads(seed) if w["name"] == name)


class Definitions(unittest.TestCase):
    def test_deterministic_unique_and_safe(self):
        first, second = p5.workloads(7), p5.workloads(7)
        self.assertEqual(p5.canonical(first), p5.canonical(second))
        self.assertNotEqual(p5.canonical(first), p5.canonical(p5.workloads(8)))
        first[0]["argv"].append("mutated")  # callers get independent copies
        self.assertNotEqual(first[0]["argv"], p5.workloads(7)[0]["argv"])
        names = [w["name"] for w in second]
        self.assertEqual(len(names), len(set(names)))
        for workload in second:
            self.assertRegex(workload["name"], r"^p5-[a-z0-9-]+$")
            self.assertIn(workload["build"], ("standard", "cloud"))
            self.assertIn("exit", workload["expected"])
            # Every workload has independent expected stdout bytes; failures default to empty.
            self.assertIsInstance(workload["expected"]["stdout"]["expected"], str)
            if workload["expected"]["exit"] != 0 and workload["expected"]["stdout"]["rule"] == "exact":
                self.assertEqual(workload["expected"]["stdout"]["expected"], "")
            if workload["expected"]["exit"] != 0:
                self.assertTrue(workload["expected"]["stderr_contains"] or workload["expected"]["stderr_json_code"],
                                workload["name"])

    def test_covers_required_families(self):
        families = {w["family"] for w in p5.workloads()}
        for family in ("user-list", "role-list", "user-list-skewed", "user-list-failure", "dataset-add",
                       "dataset-add-body", "user-add-body", "dataset-add-failure", "refresh-contention",
                       "refresh-mutation", "refresh-failure", "role-remove", "user-set-role"):
            self.assertIn(family, families)
        large = by_name("p5-user-list-large-json")
        self.assertEqual(large["cardinality"]["users"], 1000)
        self.assertEqual(len(large["expected"]["requests"][1]["unordered"]), 1000)
        self.assertGreater(large["cardinality"]["group_assignments"], 0)

    def test_inventory_keeps_null_empty_and_duplicate_assignments(self):
        small = p5.inventory(p5.DEFAULT_SEED, "small")
        self.assertIsNone(small["roles"]["role-0002"])
        self.assertEqual(small["roles"]["role-0001"], [])
        large = p5.inventory(p5.DEFAULT_SEED, "large")
        for inv in (small, large):
            last = list(inv["roles"])[-1]
            self.assertFalse(any(last in p5.user_roles(u) for u in inv["users"]), "removal target must be unassigned")
        self.assertTrue(any(len(p5.user_roles(u)) != len(set(p5.user_roles(u))) for u in large["users"]),
                        "some user should hold a role both directly and through a group")

    def test_skewed_delays_have_a_slow_tail(self):
        delays = p5.skewed_delays(1, [str(i) for i in range(1000)])
        self.assertEqual(sum(v == 120 for v in delays.values()), 20)
        self.assertLessEqual(max(v for v in delays.values() if v != 120), 3)

    def test_mutation_models(self):
        add = by_name("p5-user-add-duplicate-roles")
        body = add["expected"]["requests"][0]["exact"][2]["body"]
        self.assertEqual(body, ["role-0003", "role-0000", "role-0003"])  # payload keeps multiplicity
        self.assertEqual(add["expected"]["state"]["users"][-1]["roles"], ["role-0003", "role-0000"])
        removed = by_name("p5-role-remove-small")["expected"]["state"]
        self.assertNotIn("role-0004", removed["roles"])
        self.assertEqual(removed["commits"], [f"DELETE {p5.API}role/role-0004"])
        self.assertEqual(by_name("p5-user-add-cloud-rejected")["expected"]["requests"], [])
        rejected = by_name("p5-refresh-rejected-no-commit")
        self.assertEqual([r["status"] for r in rejected["expected"]["requests"][0]["exact"]], [401, 400])
        self.assertEqual(rejected["expected"]["state"]["token_grants"], 0)
        self.assertEqual(rejected["expected"]["config"], "unchanged")
        self.assertEqual(rejected["fixture"]["auth"]["grant"], "reject")

    def test_expected_output_comes_from_the_go_model(self):
        inv = p5.inventory(p5.DEFAULT_SEED, "small")
        text = by_name("p5-user-list-small-text")["expected"]["stdout"]["expected"]
        self.assertEqual(text.encode(), model.user_table([(u["id"], p5.user_roles(u), None) for u in inv["users"]]))
        self.assertEqual(by_name("p5-role-list-small-json")["expected"]["stdout"]["expected"].encode(),
                         model.role_json(inv["roles"]))
        failing = by_name("p5-user-list-small-detail-failure-text")["expected"]
        self.assertIn("error: " + p5.USER_DETAIL_ERROR, failing["stdout"]["expected"])
        self.assertTrue(failing["stderr_contains"][0].startswith("failed to fetch roles for 1 user(s): Error fetching roles for user-00002"))
        self.assertEqual(by_name("p5-user-list-small-detail-failure-json")["expected"]["stderr_json_code"], "COMMAND_FAILED")


class Comparators(unittest.TestCase):
    expected = [{"exact": [p5.req("GET", "user")]},
                {"unordered": [p5.req("GET", "user/a/role"), p5.req("GET", "user/b/role")]}]

    def test_fanout_siblings_are_unordered_but_barrier_is_exact(self):
        ledger = [entry("GET", "user"), entry("GET", "user/b/role"), entry("GET", "user/a/role")]
        self.assertEqual(p5.check_requests(ledger, self.expected), [])
        swapped = [entry("GET", "user/b/role"), entry("GET", "user"), entry("GET", "user/a/role")]
        self.assertTrue(p5.check_requests(swapped, self.expected))
        self.assertEqual(p5.canonical_requests(ledger, self.expected),
                         p5.canonical_requests([ledger[0], ledger[2], ledger[1]], self.expected))

    def test_same_clock_causal_barriers(self):
        timed = lambda rel, received, **more: entry("GET", rel, received_ns=received, headers_ns=received + 5,  # noqa: E731
                                                   final_write_started_ns=received + 6, **more)
        ordered = [timed("user", 0), timed("user/b/role", 10), timed("user/a/role", 11)]
        self.assertEqual(p5.check_requests(ordered, self.expected), [])
        early = [timed("user", 0), timed("user/b/role", 3), timed("user/a/role", 11)]
        self.assertTrue(any("arrived before" in i for i in p5.check_requests(early, self.expected)))
        sequence = [{"exact": [p5.req("GET", "user"), p5.req("GET", "roles")]}]
        self.assertEqual(p5.check_requests([timed("user", 0), timed("roles", 5)], sequence), [])
        self.assertTrue(p5.check_requests([timed("user", 0), timed("roles", 4)], sequence))

    def test_membership_and_multiplicity(self):
        duplicate = [entry("GET", "user"), entry("GET", "user/a/role"), entry("GET", "user/a/role")]
        self.assertTrue(p5.check_requests(duplicate, self.expected))
        missing = [entry("GET", "user"), entry("GET", "user/a/role")]
        self.assertTrue(p5.check_requests(missing, self.expected))
        extra = [entry("GET", "user"), entry("GET", "user/a/role"), entry("GET", "user/b/role"), entry("GET", "roles")]
        self.assertTrue(p5.check_requests(extra, self.expected))

    def test_exact_segments_compare_bodies_and_status(self):
        expected = [{"exact": [p5.req("PATCH", "user/u/role/add", body=["a", "a"])]}]
        self.assertEqual(p5.check_requests([entry("PATCH", "user/u/role/add", body=b'["a","a"]')], expected), [])
        self.assertTrue(p5.check_requests([entry("PATCH", "user/u/role/add", body=b'["a"]')], expected))
        self.assertTrue(p5.check_requests([entry("PATCH", "user/u/role/add", 500, body=b'["a","a"]')], expected))
        # Semantically equal but not Go's compact encoding: raw bytes are compared.
        self.assertTrue(p5.check_requests([entry("PATCH", "user/u/role/add", body=b'["a", "a"]')], expected))

    def test_oracle_view_keeps_raw_bytes_and_headers(self):
        a = entry("PATCH", "user/u/role/add", body=b'["a"]')
        b = entry("PATCH", "user/u/role/add", body=b'[ "a"]')
        c = entry("PATCH", "user/u/role/add", body=b'["a"]', headers={"content-type": "application/json", "accept-encoding": "gzip"})
        self.assertEqual(p5.project(a), p5.project(b))
        self.assertNotEqual(p5.request_key(a), p5.request_key(b))
        self.assertNotEqual(p5.request_key(a), p5.request_key(c))

    def test_concurrency_from_ledger_intervals(self):
        sequential = [{"received_ns": 0, "final_write_started_ns": 10, "completed_ns": 12},
                      {"received_ns": 11, "final_write_started_ns": 20, "completed_ns": 21}]
        self.assertEqual(p5.max_concurrency(sequential), 1)
        overlapping = sequential + [{"received_ns": 5, "completed_ns": 30}, {"received_ns": 6}]
        self.assertEqual(p5.max_concurrency(overlapping), 3)
        self.assertEqual(p5.max_concurrency([]), 0)

    def refresh_rule(self):
        token = p5.req("POST", p5.TOKEN_PATH, body={"grant_type": "refresh_token", "refresh_token": p5.OLD_REFRESH}, auth="token")
        return {"rule": "refresh-fanout", "prefix": [p5.req("GET", "user", auth="old")], "token": token,
                "details": [p5.req("GET", f"user/{u}/role", auth="new") for u in "abc"]}

    def token(self, **times):
        return entry("POST", p5.TOKEN_PATH, auth="token",
                     body=b'{"grant_type":"refresh_token","refresh_token":"fixture-refresh"}', **times)

    def test_refresh_fanout_accepts_variable_rejections(self):
        rule = self.refresh_rule()
        old = lambda u: entry("GET", f"user/{u}/role", 401, "old-rejected")  # noqa: E731
        new = lambda u: entry("GET", f"user/{u}/role", 200, "new")  # noqa: E731
        one = [entry("GET", "user", auth="old"), old("a"), self.token(), new("b"), new("a"), new("c")]
        every = [entry("GET", "user", auth="old"), old("c"), old("a"), old("b"), self.token(), new("a"), new("c"), new("b")]
        self.assertEqual(p5.check_requests(one, rule), [])
        self.assertEqual(p5.check_requests(every, rule), [])
        self.assertEqual(p5.canonical_requests(one, rule), p5.canonical_requests(every, rule))

    def test_refresh_fanout_rejects_protocol_violations(self):
        rule = self.refresh_rule()
        old = lambda u: entry("GET", f"user/{u}/role", 401, "old-rejected")  # noqa: E731
        new = lambda u: entry("GET", f"user/{u}/role", 200, "new")  # noqa: E731
        head = [entry("GET", "user", auth="old")]
        self.assertTrue(p5.check_requests(head + [old("a"), self.token(), self.token(), new("a"), new("b"), new("c")], rule))
        self.assertTrue(p5.check_requests(head + [old("a"), self.token(), new("b"), new("c")], rule))
        self.assertTrue(p5.check_requests(head + [old("a"), self.token(), new("a"), new("a"), new("b"), new("c")], rule))
        self.assertTrue(p5.check_requests(head + [self.token(), new("a"), new("b"), new("c")], rule))
        # Grant requested before any 401, and a rejected detail replayed before its rejection.
        self.assertTrue(p5.check_requests(head + [self.token(), old("a"), new("a"), new("b"), new("c")], rule))
        self.assertTrue(p5.check_requests(head + [old("b"), self.token(), new("a"), old("a"), new("b"), new("c")], rule))
        # A rejected request outside the fan-out.
        self.assertTrue(p5.check_requests(head + [entry("GET", "roles", 401, "old-rejected"), old("a"), self.token(),
                                                  new("a"), new("b"), new("c")], rule))

    def test_refresh_fanout_timing(self):
        rule = self.refresh_rule()
        head = [entry("GET", "user", auth="old", received_ns=0, headers_ns=1, final_write_started_ns=2)]
        old = entry("GET", "user/a/role", 401, "old-rejected", received_ns=3, headers_ns=4, final_write_started_ns=4)
        new = lambda u, at: entry("GET", f"user/{u}/role", 200, "new", received_ns=at)  # noqa: E731
        good = head + [old, self.token(received_ns=5, final_write_started_ns=6), new("a", 7), new("b", 7), new("c", 8)]
        self.assertEqual(p5.check_requests(good, rule), [])
        racing = head + [old, self.token(received_ns=5, final_write_started_ns=6), new("a", 7), new("b", 5.5), new("c", 8)]
        self.assertTrue(any("grant body" in i for i in p5.check_requests(racing, rule)))

    def test_replays_must_be_byte_identical(self):
        rejected = entry("PATCH", "user/u/role/add", 401, "old-rejected", body=b'["a"]',
                         headers={"content-type": "application/json", "cookie": "session=fixture-session"})
        same = entry("PATCH", "user/u/role/add", 200, "new", body=b'["a"]',
                     headers={"content-type": "application/json", "cookie": "session=rotated-session"})
        changed = entry("PATCH", "user/u/role/add", 200, "new", body=b'["a"] ',
                        headers={"content-type": "application/json", "cookie": "session=rotated-session"})
        self.assertEqual(p5.replay_issues([rejected, self.token(), same]), [])
        self.assertTrue(p5.replay_issues([rejected, self.token(), changed]))

    def test_incomplete_milestones_are_invalid(self):
        complete = entry("GET", "user", received_ns=1, headers_ns=2, final_write_started_ns=3, completed_ns=4)
        self.assertEqual(p5.milestone_issues([complete]), [])
        self.assertTrue(p5.milestone_issues([{**complete, "final_write_started_ns": None}]))
        self.assertEqual(p5.milestone_issues([{**complete, "headers_ns": None, "client_closed": True}]), [])


class Stdout(unittest.TestCase):
    def test_user_text_role_order_is_scoped(self):
        rows = [("user-00000", ["role-0000", "role-0001"], None), ("user-00001", [], None)]
        expected = model.user_table(rows)
        rule = {"rule": "user-list-text", "expected": expected.decode()}
        swapped = model.user_table([("user-00000", ["role-0001", "role-0000"], None), ("user-00001", [], None)])
        self.assertEqual(p5.independent_stdout_issues(rule, expected), [])
        self.assertEqual(p5.independent_stdout_issues(rule, swapped), [])
        self.assertEqual(p5.scoped_stdout(rule, expected), p5.scoped_stdout(rule, swapped))
        wider = expected.replace(b"user-00000 ..", b"user-00000 ...")
        self.assertTrue(p5.independent_stdout_issues(rule, wider))
        self.assertTrue(p5.independent_stdout_issues(rule, expected.replace(b", role-0001", b"")))
        rows_reordered = model.user_table(list(reversed(rows)))
        self.assertTrue(p5.independent_stdout_issues(rule, rows_reordered), "user row order stays exact")

    def test_user_json_keeps_formatting_and_multiplicity(self):
        users = [{"id": "user-00000", "roles": ["role-0000", "role-0001", "role-0000"]}]
        rule = {"rule": "user-list-json", "expected": p5._go_json(users).decode()}
        a = json.dumps(users, indent=2).encode() + b"\n"
        b = json.dumps([{"id": "user-00000", "roles": ["role-0001", "role-0000", "role-0000"]}], indent=2).encode() + b"\n"
        self.assertEqual(p5.scoped_stdout(rule, a), p5.scoped_stdout(rule, b))
        self.assertEqual(p5.independent_stdout_issues(rule, b), [])
        compact = json.dumps(users).encode() + b"\n"
        self.assertTrue(p5.scoped_stdout(rule, compact)[1])
        self.assertTrue(p5.independent_stdout_issues(rule, compact))
        deduplicated = json.dumps([{"id": "user-00000", "roles": ["role-0000", "role-0001"]}], indent=2).encode() + b"\n"
        self.assertTrue(p5.independent_stdout_issues(rule, deduplicated))
        extra_field = json.dumps([{"id": "user-00000", "roles": users[0]["roles"], "method": "native"}], indent=2).encode() + b"\n"
        self.assertTrue(p5.independent_stdout_issues(rule, extra_field))

    def test_role_text_blocks_are_a_multiset(self):
        roles = {"role-0000": [{"privilege": "reader"}, {"privilege": "writer"}], "role-0001": []}
        rule = {"rule": "role-list-text", "expected": model.role_table([(n, a, None) for n, a in roles.items()]).decode()}
        reversed_blocks = model.role_table([(n, a, None) for n, a in reversed(list(roles.items()))])
        self.assertEqual(p5.independent_stdout_issues(rule, reversed_blocks), [])
        reordered = model.role_table([("role-0000", [{"privilege": "writer"}, {"privilege": "reader"}], None), ("role-0001", [], None)])
        self.assertTrue(p5.independent_stdout_issues(rule, reordered), "action order within a role stays exact")

    def test_role_json_and_exact(self):
        roles = {"role-0000": None, "role-0001": [{"privilege": "reader", "resource": {"stream": "x"}}]}
        rule = {"rule": "role-list-json", "expected": model.role_json(roles).decode()}
        self.assertEqual(p5.independent_stdout_issues(rule, model.role_json(roles)), [])
        self.assertTrue(p5.independent_stdout_issues(rule, model.role_json({**roles, "role-0000": []})))
        self.assertTrue(p5.independent_stdout_issues(p5.exact(""), b"[]\n"))
        self.assertEqual(p5.independent_stdout_issues(p5.exact("Removed user x\n"), b"Removed user x\n"), [])
        self.assertTrue(p5.independent_stdout_issues(p5.exact("Removed user x\n"), b"Removed user x"))

    def test_stderr_json_code(self):
        self.assertEqual(p5.stderr_json_issues("COMMAND_FAILED", b'{"error":{"code":"COMMAND_FAILED"}}'), [])
        self.assertTrue(p5.stderr_json_issues("COMMAND_FAILED", b'{"error":{"code":"SERVER_ERROR"}}'))
        self.assertTrue(p5.stderr_json_issues("COMMAND_FAILED", b"failed"))
        self.assertEqual(p5.stderr_json_issues(None, b"anything"), [])


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pb-p5-test-")
        self.addCleanup(self.temp.cleanup)

    def start(self, workload):
        fixture = p5.AdminFixture(workload["fixture"], Path(self.temp.name) / workload["name"])
        self.addCleanup(fixture.close)
        return fixture

    def call(self, fixture, method, rel, headers=None, body=None):
        request = urllib.request.Request(fixture.url + rel, data=body, method=method,
                                         headers={"Content-Type": "application/json", **(headers or {})})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read()

    def test_endpoints_auth_and_additive_roles(self):
        workload = by_name("p5-user-set-role-small")
        fixture = self.start(workload)
        self.assertEqual(self.call(fixture, "GET", "/api/v1/user")[0], 401)
        status, body = self.call(fixture, "GET", "/api/v1/user", BASIC)
        self.assertEqual([u["id"] for u in json.loads(body)][:3], ["user-00000", "user-00001", "user-00002"])
        status, body = self.call(fixture, "GET", "/api/v1/user/user-00002/role", BASIC)
        detail = json.loads(body)
        self.assertEqual(set(detail), {"roles", "group_roles"})
        before = fixture.control("state")["users"][2]["roles"]
        status, _ = self.call(fixture, "PATCH", "/api/v1/user/user-00002/role/add", BASIC, b'["role-0004","role-0004"]')
        self.assertEqual(status, 200)
        state = fixture.control("state")
        self.assertEqual(state["users"][2]["roles"], list(dict.fromkeys([*before, "role-0004"])))
        self.assertEqual(state["commits"], [f"PATCH {p5.API}user/user-00002/role/add"])
        ledger = fixture.control("ledger?quiesce=1")
        self.assertTrue(ledger["quiesced"])
        ledger = ledger["ledger"]
        self.assertEqual([e["auth"] for e in ledger], ["rejected", "static", "static", "static"])
        self.assertEqual(base64.b64decode(ledger[-1]["body_b64"]), b'["role-0004","role-0004"]')
        # All headers but user-agent are recorded, with the fixture host normalized.
        self.assertNotIn("user-agent", ledger[-1]["headers"])
        self.assertEqual(ledger[-1]["headers"]["host"], "<FIXTURE>")
        self.assertEqual(p5.milestone_issues(ledger), [])

    def test_null_actions_survive_user_detail(self):
        fixture = self.start(by_name("p5-user-list-small-json"))
        holder = next(u for u in p5.inventory(p5.DEFAULT_SEED, "small")["users"] if "role-0002" in p5.user_roles(u))
        _, body = self.call(fixture, "GET", f"/api/v1/user/{holder['id']}/role", BASIC)
        detail = json.loads(body)
        found = [detail["roles"].get("role-0002"), *(g.get("role-0002") for g in detail["group_roles"].values())]
        self.assertIn({"actions": None}, found)
        self.assertNotIn({"actions": []}, found)

    def test_reset_restores_state_ledger_and_fault_budget(self):
        workload = by_name("p5-dataset-add-metrics")
        fixture = self.start(workload)
        headers = {**BASIC, "X-P-Telemetry-Type": "metrics", "X-P-Log-Source": "otel-metrics"}
        self.assertEqual(self.call(fixture, "PUT", "/api/v1/logstream/p5-new", headers)[0], 200)
        self.assertEqual(self.call(fixture, "PUT", "/api/v1/logstream/p5-new", headers)[0], 400)
        reset = fixture.reset()
        self.assertTrue(reset["quiesced"])
        self.assertEqual(reset["previous_requests"], 2)
        self.assertEqual(reset["ledger"], 0)
        self.assertEqual(reset["state"], p5.baseline_state(workload["fixture"]["state"]))
        self.assertEqual(self.call(fixture, "PUT", "/api/v1/logstream/p5-new", headers)[0], 200)

    def test_reset_waits_for_withheld_tail(self):
        fixture = self.start(by_name("p5-dataset-add-status-only-slow-body"))
        headers = {**BASIC, "X-P-Telemetry-Type": "metrics", "X-P-Log-Source": "otel-metrics"}
        thread = threading.Thread(target=self.call, args=(fixture, "PUT", "/api/v1/logstream/p5-new", headers))
        thread.start()
        time.sleep(0.1)
        started = time.monotonic()
        reset = fixture.reset()
        self.assertTrue(reset["quiesced"])
        self.assertGreater(time.monotonic() - started, 0.2)  # held until the 400 ms tail was written
        thread.join()

    def test_datasets_require_telemetry_headers_and_guard_info(self):
        fixture = self.start(by_name("p5-dataset-add-metrics"))
        headers = {**BASIC, "X-P-Telemetry-Type": "metrics"}
        self.assertEqual(self.call(fixture, "PUT", "/api/v1/logstream/p5-new", headers)[0], 400)
        self.assertEqual(self.call(fixture, "PUT", "/api/v1/logstream/p5-new", {**headers, "X-P-Log-Source": "otel-metrics"})[0], 200)
        status, body = self.call(fixture, "GET", "/api/v1/logstream/p5-new/info", BASIC)
        self.assertEqual(json.loads(body)["telemetryType"], "metrics")
        self.assertEqual(self.call(fixture, "DELETE", "/api/v1/logstream/missing", BASIC)[0], 404)

    def test_faults_and_in_flight(self):
        fixture = self.start(by_name("p5-user-list-large-skewed-json"))
        slow = [k.split(" ", 1)[1] for k, v in by_name("p5-user-list-large-skewed-json")["fixture"]["delays"].items() if v == 120][:3]
        threads = [threading.Thread(target=self.call, args=(fixture, "GET", path, BASIC)) for path in slow]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertGreaterEqual(fixture.control("ledger")["max_in_flight"], 2)
        failing = self.start(by_name("p5-user-list-small-detail-failure-json"))
        self.assertEqual(self.call(failing, "GET", "/api/v1/user/user-00002/role", BASIC), (500, b"detail failure"))

    def test_tail_is_withheld_after_headers(self):
        fixture = self.start(by_name("p5-dataset-add-status-only-slow-body"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(fixture.url + "/api/v1/logstream/p5-new", method="PUT", headers={
            **BASIC, "Content-Type": "application/json", "X-P-Telemetry-Type": "metrics", "X-P-Log-Source": "otel-metrics"})
        started = time.monotonic()
        with opener.open(request, timeout=10) as response:
            headers_at = time.monotonic() - started
            response.read()
            body_at = time.monotonic() - started
        self.assertLess(headers_at, 0.3)
        self.assertGreaterEqual(body_at, 0.39)

    def test_refresh_budget_and_single_grant(self):
        fixture = self.start(by_name("p5-refresh-user-list-large-contention-json"))
        old = {"Cookie": "session=fixture-session", "X-P-Tenant": p5.TENANT}
        new = {"Cookie": "session=rotated-session", "X-P-Tenant": p5.TENANT}
        self.assertEqual(self.call(fixture, "GET", "/api/v1/user", old)[0], 200)  # budget of one
        self.assertEqual(self.call(fixture, "GET", "/api/v1/user/user-00000/role", old)[0], 401)
        self.assertEqual(self.call(fixture, "GET", "/api/v1/user/user-00000/role", new)[0], 401)  # before grant
        grant = {"Authorization": "Bearer " + p5.ORCHESTRATOR_TOKEN, "Accept": "application/json"}
        body = b'{"grant_type":"refresh_token","refresh_token":"fixture-refresh"}'
        self.assertEqual(self.call(fixture, "POST", p5.TOKEN_PATH, {"Authorization": grant["Authorization"]}, body)[0],
                         400, "grant requires Accept: application/json")
        status, response = self.call(fixture, "POST", p5.TOKEN_PATH, grant, body)
        self.assertEqual((status, json.loads(response)["access_token"]), (200, p5.NEW_SESSION))
        self.assertEqual(self.call(fixture, "GET", "/api/v1/user/user-00000/role", new)[0], 200)
        self.assertEqual(fixture.control("state")["token_grants"], 1)

    def test_rejected_grant(self):
        fixture = self.start(by_name("p5-refresh-rejected-no-commit"))
        grant = {"Authorization": "Bearer " + p5.ORCHESTRATOR_TOKEN, "Accept": "application/json"}
        status, _ = self.call(fixture, "POST", p5.TOKEN_PATH, grant,
                              b'{"grant_type":"refresh_token","refresh_token":"fixture-refresh"}')
        self.assertEqual(status, 400)
        self.assertEqual(fixture.control("state")["token_grants"], 0)


class Stages(unittest.TestCase):
    def test_skipped_workloads_fail_the_stage(self):
        with tempfile.TemporaryDirectory(prefix="pb-p5-stage-") as temp:
            args = argparse.Namespace(meter=str(METER), warmup=0, repetitions=1, artifacts="failures", seed=1,
                                      fixture_reset="control")
            parent = {"stages": {}}
            cloud = by_name("p5-refresh-dataset-add")
            labels = {"go-a": {"standard": "/bin/false", "cloud": None}, "go-b": {"standard": "/bin/false", "cloud": None}}
            report = p5.stage("t", labels, [cloud], args, Path(temp), random.Random(1), parent)
            self.assertFalse(report["passed"])
            self.assertEqual(report["skipped_workloads"], [cloud["name"]])
            standard = by_name("p5-user-remove")
            report = p5.stage("u", labels, [standard], args, Path(temp), random.Random(1), parent, oracle={})
            self.assertFalse(report["passed"])
            self.assertIn("not validated", report["workloads"][standard["name"]]["skipped"])


FAKE_CLI = r'''#!/usr/bin/env python3
"""Minimal sequential stand-in for pb; only for harness self-tests."""
import base64, json, os, sys, tomllib, urllib.request
config = tomllib.loads(open(os.environ["XDG_CONFIG_HOME"] + "/pb/config.toml").read())
profile = config["Profiles"][config["DefaultProfile"]]
auth = "Basic " + base64.b64encode(f"{profile['username']}:{profile['password']}".encode()).decode()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def call(method, rel, headers=()):
    request = urllib.request.Request(profile["url"] + "/api/v1/" + rel, method=method,
                                     headers={"Authorization": auth, "Content-Type": "application/json", **dict(headers)})
    return opener.open(request, timeout=10)
if sys.argv[1:3] == ["user", "list"]:
    users = json.load(call("GET", "user"))
    if os.path.basename(sys.argv[0]) == "skip-one":
        users = users[1:]
    out = []
    for user in users:
        detail = json.load(call("GET", f"user/{user['id']}/role"))
        roles = list(detail["roles"]) + [r for group in detail["group_roles"].values() for r in group]
        out.append({"id": user["id"], "roles": roles})
    print(json.dumps(out, indent=2, ensure_ascii=False))
elif sys.argv[1:3] == ["dataset", "add"]:
    kind = sys.argv[sys.argv.index("--type") + 1]
    headers = {"X-P-Telemetry-Type": kind}
    if kind != "logs":
        headers["X-P-Log-Source"] = "otel-" + kind
    response = call("PUT", "logstream/" + sys.argv[3], headers.items())
    response.close()  # status only: never read the success body
    print(f"Created {kind} dataset {sys.argv[3]}")
'''


@unittest.skipUnless(METER.exists() and sys.platform == "linux", "build bin/pb-meter first (see harness/meter.c)")
class EndToEnd(unittest.TestCase):
    def fake(self, name):
        folder = tempfile.TemporaryDirectory(prefix="pb-p5-fake-")
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / name
        path.write_text(FAKE_CLI)
        path.chmod(0o755)
        return path

    def test_valid_sample_with_metrics_and_contract(self):
        workload = by_name("p5-user-list-small-json")
        result = p5.run_sample(self.fake("pb"), workload, METER)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["requests_total"], 9)
        self.assertEqual(result["detail_requests"], 8)
        self.assertEqual(result["max_in_flight"], 1)
        self.assertIsNotNone(result["first_request_ms"])
        self.assertGreater(result["first_request_ms"], 0)
        self.assertLessEqual(result["first_request_ms"], result["wall_ms"])
        self.assertEqual(result["resource_source"], "native_child_wait4")
        again = p5.run_sample(self.fake("pb"), workload, METER, oracle=result["scoped_digests"])
        self.assertEqual(again["issues"], [])
        self.assertEqual(again["contract_sha256"], result["contract_sha256"])

    def test_missing_detail_is_invalid(self):
        result = p5.run_sample(self.fake("skip-one"), by_name("p5-user-list-small-json"), METER)
        self.assertFalse(result["valid"])
        self.assertTrue(any("fan-out" in issue or "segment" in issue for issue in result["issues"]))

    def test_status_only_exits_before_tail(self):
        workload = by_name("p5-dataset-add-status-only-slow-body")
        result = p5.run_sample(self.fake("pb"), workload, METER)
        self.assertEqual(result["issues"], [])
        self.assertTrue(result["exit_before_success_body"])
        self.assertLess(result["wall_ms"], 390)

    def test_shared_fixture_resets_between_mutating_samples(self):
        workload = by_name("p5-dataset-add-status-only-slow-body")
        shared = p5.SharedFixture(workload["fixture"])
        self.addCleanup(shared.close)
        first = p5.run_sample(self.fake("pb"), workload, METER, shared=shared)
        url = shared.fixture.url
        second = p5.run_sample(self.fake("pb"), workload, METER, oracle=first["scoped_digests"], shared=shared)
        self.assertEqual(first["issues"], [])
        self.assertEqual(second["issues"], [])  # a second PUT without reset would be a 400 conflict
        self.assertEqual(shared.fixture.url, url)
        self.assertEqual(second["fixture_reset"], "control")
        process = p5.run_sample(self.fake("pb"), workload, METER, oracle=first["scoped_digests"])
        self.assertEqual(process["issues"], [], "process and control resets give the same scoped contract")


if __name__ == "__main__":
    unittest.main()
