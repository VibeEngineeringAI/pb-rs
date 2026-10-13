#!/usr/bin/env python3
"""Phase-five administration performance workloads and measurement runner.

Deterministic seeded inventories run against a disposable, stateful loopback
fixture. Its state, ledger and fault budgets are reset to the identical
initial scenario before every warmup and sample. By default one fixture
process per workload is reset over its control endpoint after the previous
CLI has exited and every handler has finished. `--fixture-reset process`
starts a fresh fixture process per sample instead. Each sample also gets a
fresh HOME/config. Setup, reset, ledger capture and artifacts are outside
timing.

Every sample is validated against independent expectations:
  - exit status
  - stdout bytes from phase5.py's Go renderer model (user/role tables, Go
    MarshalIndent JSON, success strings), not a capture
  - stderr substrings or JSON error codes
  - request ledger: order, membership, multiplicity, status, auth state,
    exact raw JSON bodies, telemetry headers and same-clock causal barriers
  - final fixture state, commit ledger, token grants and config effects

Only two declared rules relax byte order:
  - fan-out siblings are a multiset within their segment
  - map-ordered role names are canonicalized with phase5.py's validated
    canonicalizers

Resource metrics come from the native pb-meter via run.measure.

The run has two stages. First, an identical-binary Go A/A self-comparison
(its own, separately recorded repetition count) validates the scoped rules and
freezes Go's scoped contract as the oracle. Only then are Go and candidates
measured together in seeded randomized sequential order. The prior Rust build
does not implement these commands; it runs once per workload as an unmeasured
scope guard.

  TMPDIR=/home/ajs/.cache/pb-phase5/tmp python3 harness/phase5_workloads.py run \\
    --go /home/ajs/.cache/pb-phase5/pb-go --go-cloud /home/ajs/.cache/pb-phase5/pb-go-cloud \\
    --candidate rust=target/release/pb \\
    --prior /home/ajs/.cache/pb-phase5/baseline-target/release/pb \\
    --manifest /home/ajs/.cache/pb-phase5/baseline-manifest.json \\
    --out target/phase5/workloads-run

  python3 harness/phase5_workloads.py list
  python3 harness/phase5_workloads.py export --out target/phase5/workloads.json
"""

import argparse
import base64
import collections
import copy
import csv
import functools
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import selectors
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
SCHEMA_VERSION = 2
DEFAULT_SEED = 2026
API = "/api/v1/"
ORCHESTRATOR_TOKEN = "harness-orchestrator-token"  # Matches the phase-two cloud build.
TOKEN_PATH = "/orch/api/v1/cli/oauth/token"
OLD_SESSION, NEW_SESSION = "fixture-session", "rotated-session"
OLD_REFRESH, NEW_REFRESH = "fixture-refresh", "rotated-refresh"
TENANT = "fixture-tenant"
STATIC_AUTH = {
    "basic": {"authorization": "Basic aGFybmVzczpoYXJuZXNz"},
    "api-key": {"x-api-key": "fixture-key"},
    "cloud-key": {"x-api-key": "fixture-key", "x-p-tenant": TENANT},
}
# Request headers are all recorded (user-agent excepted, as in phase5.py); the
# fixture host:port is normalized so separate fixture processes compare equal.
IGNORED_REQUEST_HEADERS = ("user-agent",)
LOG_SOURCE = {"logs": None, "metrics": "otel-metrics", "traces": "otel-traces"}
SIZES = {"small": {"users": 8, "roles": 5, "max_actions": 3, "max_groups": 1},
         "large": {"users": 1000, "roles": 250, "max_actions": 40, "max_groups": 3}}
PRIVILEGES = ("admin", "editor", "writer", "reader", "ingestor")
NEW_DATASET, NEW_USER = "p5-new", "p5-user-new"
STATUS_ONLY_TAIL = {"delay_ms": 400, "split": 32768, "body": "x" * 65536}
USER_DETAIL_ERROR = "fetch user roles failed: 500 Internal Server Error: detail failure"
QUIESCE_TIMEOUT_S = 10
COMPARED_METRICS = ("wall_ms", "cpu_ms", "cpu_user_ms", "cpu_system_ms", "peak_rss_bytes",
                    "first_request_ms", "request_to_stdout_ms")
# run.METRICS entries that apply to administration commands (SQL row metrics do not).
REPORTED_METRICS = ("wall_ms", "first_stdout_ms", "first_stderr_ms", "last_stdout_ms", "first_request_ms",
                    "request_to_stdout_ms", "cpu_user_ms", "cpu_system_ms", "cpu_ms", "peak_rss_bytes",
                    "minor_faults", "major_faults", "voluntary_context_switches", "involuntary_context_switches",
                    "block_input_operations", "block_output_operations", "proc_io_syscr", "proc_io_syscw",
                    "proc_io_rchar", "proc_io_wchar", "proc_io_read_bytes", "proc_io_write_bytes",
                    "request_body_bytes", "stdout_bytes", "stderr_bytes")
EXTRA_METRICS = ("requests_total", "detail_requests", "max_in_flight", "rejected_401_requests",
                 "response_body_bytes", "logical_bytes_total", "last_response_complete_ms",
                 "response_to_exit_ms")
# Quantities this harness never observes; reported as N/A rather than substituted.
NOT_MEASURED = {
    "network_socket_bytes": "loopback socket/TLS bytes are not captured; request/response bytes are logical HTTP bodies "
                            "(response bytes are what the fixture wrote, not what the client read)",
    "heap_allocations": "release runs are uninstrumented; use the diagnostic builds for allocation attribution",
    "fd_writes": "requires strace; proc_io_syscw counts write-family calls only and excludes send/recv",
    "first_useful_result_ms": "no command-specific useful-result predicate for administration output",
}


def mono_ns():
    # Same CLOCK_MONOTONIC as pb-meter and run.measure (Linux system-wide clock).
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def _compact(value):
    # Go json.Marshal of a []string / map: no spaces, raw UTF-8.
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")).encode()


def _go_json(value):
    # Go json.MarshalIndent(v, "", "  ") plus the encoder newline (as phase5.go_json).
    return (json.dumps(value, indent=2, ensure_ascii=False)
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029") + "\n").encode()


def _rng(seed, *parts):
    return random.Random(":".join(str(p) for p in (seed, *parts)))


def _phase5():
    """Independent Go renderer/canonicalizer model, validated by phase5.py's Go self-check."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import phase5
    return phase5


# ----- deterministic inventories and workloads -------------------------------

def _action(rng, role_index, action_index):
    privilege = PRIVILEGES[(role_index + action_index) % len(PRIVILEGES)]
    kind = rng.randrange(4)
    if privilege in ("admin", "editor") or kind == 0:
        return {"privilege": privilege}
    if kind == 1:
        resource = {"dataset": f"ds-{rng.randrange(50):02d}-日志"}
    elif kind == 2:
        resource = {"stream": f"legacy-{rng.randrange(50):02d}"}  # legacy wire field
    else:
        resource = {"dataset": f"ds-{rng.randrange(50):02d}", "tag": f"tag-{rng.randrange(9)}-é"}
    return {"privilege": privilege, "resource": resource}


def inventory(seed, size):
    """Users/roles/groups/datasets. Names are ASCII so URL escaping cannot differ."""
    spec = SIZES[size]
    rng = _rng(seed, "inventory", size)
    roles = {}
    for i in range(spec["roles"]):
        if i == 1:
            actions = []
        elif i == 2 and size == "small":
            actions = None  # null actions must stay null, not become []
        else:
            actions = [_action(rng, i, j) for j in range(rng.randint(1, spec["max_actions"]))]
        roles[f"role-{i:04d}"] = actions
    # The last role stays unassigned, so removal workloads hold whether or not the
    # server rejects deleting an assigned role (phase5.py's fixture rejects by default).
    names = list(roles)[:-1]
    users = []
    for i in range(spec["users"]):
        direct = rng.sample(names, rng.randint(0 if i % 7 == 3 else 1, min(3, len(names))))
        groups = {f"group-{g:02d}": rng.sample(names, rng.randint(1, 2))
                  for g in range(rng.randint(0, spec["max_groups"]))}
        users.append({"id": f"user-{i:05d}", "method": "native", "roles": direct, "groups": groups})
    datasets = {f"dataset-{kind}": {"streamType": "UserDefined", "telemetryType": kind}
                for kind in ("logs", "metrics", "traces")}
    return {"users": users, "roles": roles, "datasets": datasets}


def user_roles(user):
    """Direct plus group role names, with multiplicity (Go appends both)."""
    return [*user["roles"], *(name for group in user["groups"].values() for name in group)]


def skewed_delays(seed, keys, slow_fraction=0.02, slow_ms=120, fast_max_ms=3):
    rng = _rng(seed, "delays", len(keys))
    slow = set(rng.sample(range(len(keys)), max(1, math.ceil(len(keys) * slow_fraction))))
    return {key: slow_ms if i in slow else rng.randint(0, fast_max_ms) for i, key in enumerate(keys)}


def req(method, rel, status=200, body=None, auth="static"):
    return {"method": method, "path": rel if rel.startswith("/") else API + rel,
            "status": status, "body": body, "auth": auth}


def exact(text):
    return {"rule": "exact", "expected": text}


def baseline_state(inv, token_grants=0):
    return {"users": copy.deepcopy(inv["users"]), "roles": copy.deepcopy(inv["roles"]),
            "datasets": copy.deepcopy(inv["datasets"]), "commits": [], "token_grants": token_grants}


def _cardinality(inv, detail_requests=0):
    return {"users": len(inv["users"]), "roles": len(inv["roles"]),
            "actions": sum(len(a or []) for a in inv["roles"].values()),
            "group_assignments": sum(len(g) for u in inv["users"] for g in u["groups"].values()),
            "detail_requests": detail_requests}


def workloads(seed=DEFAULT_SEED):
    """Fixed workload list; identical seed gives byte-identical definitions (fresh copy per call)."""
    return json.loads(_workloads_json(seed))


@functools.lru_cache(maxsize=4)
def _workloads_json(seed):
    return json.dumps(_build_workloads(seed), ensure_ascii=False)


def _build_workloads(seed):
    model = _phase5()
    result = []
    inventories = {size: inventory(seed, size) for size in SIZES}

    def add(name, argv, size, requests, *, family, output="text", auth="basic", exit_code=0, stdout=None,
            err_contains=(), err_json_code=None, state=None, faults=(), delays=None, tails=None, body=None,
            headers=None, config="unchanged", old_budget=0, grant="accept", detail_requests=0, timeout=30):
        inv = inventories[size]
        if stdout is None:
            if exit_code == 0:
                raise ValueError(f"{name}: a successful workload needs an independent expected stdout")
            stdout = exact("")  # failures print nothing on stdout unless declared
        build = "cloud" if auth == "refresh" else "standard"
        scenario = {"state": {k: inv[k] for k in ("users", "roles", "datasets")},
                    "auth": {"mode": "refresh", "old_budget": old_budget, "grant": grant} if auth == "refresh"
                    else {"mode": "static", "headers": STATIC_AUTH[auth]},
                    "faults": list(faults), "delays": delays or {}, "tails": tails or {}}
        grants = 1 if auth == "refresh" and grant == "accept" else 0
        result.append({
            "name": name, "family": family, "size": size, "output": output, "argv": argv, "auth": auth,
            "build": build, "fixture": scenario, "timeout_s": timeout,
            "cardinality": _cardinality(inv, detail_requests),
            "expected": {"exit": exit_code, "requests": requests,
                         "state": state if state is not None else baseline_state(inv, grants),
                         "stdout": stdout, "stderr_contains": list(err_contains),
                         "stderr_json_code": err_json_code, "body": body, "headers": headers or {},
                         "config": config}})

    # Independent expected list output (phase5.py's Go/lipgloss model).
    def user_text(inv, failing=()):
        rows = [(u["id"], None, USER_DETAIL_ERROR) if u["id"] in failing else (u["id"], user_roles(u), None)
                for u in inv["users"]]
        return {"rule": "user-list-text", "expected": model.user_table(rows).decode()}

    def user_json(inv):
        return {"rule": "user-list-json",
                "expected": _go_json([{"id": u["id"], "roles": user_roles(u)} for u in inv["users"]]).decode()}

    def role_text(inv):
        return {"rule": "role-list-text",
                "expected": model.role_table([(n, a, None) for n, a in inv["roles"].items()]).decode()}

    def role_json(inv):
        return {"rule": "role-list-json", "expected": model.role_json(inv["roles"]).decode()}

    # ----- inventories: sizes, outputs, skewed delays, partial failures ------
    for size, inv in inventories.items():
        ids, names = [u["id"] for u in inv["users"]], list(inv["roles"])
        user_details = [req("GET", f"user/{i}/role") for i in ids]
        role_details = [req("GET", f"role/{n}") for n in names]
        users_seq = [{"exact": [req("GET", "user")]}, {"unordered": user_details}]
        roles_seq = [{"exact": [req("GET", "roles")]}, {"unordered": role_details}]
        for output in ("text", "json"):
            flag = ["-o", "json"] if output == "json" else []
            add(f"p5-user-list-{size}-{output}", ["user", "list", *flag], size, users_seq, family="user-list",
                output=output, stdout=user_text(inv) if output == "text" else user_json(inv),
                detail_requests=len(ids))
            add(f"p5-role-list-{size}-{output}", ["role", "list", *flag], size, roles_seq, family="role-list",
                output=output, stdout=role_text(inv) if output == "text" else role_json(inv),
                detail_requests=len(names))
        if size == "large":
            add("p5-user-list-large-skewed-json", ["user", "list", "-o", "json"], size, users_seq,
                family="user-list-skewed", output="json", detail_requests=len(ids), stdout=user_json(inv),
                delays=skewed_delays(seed, [f"GET {API}user/{i}/role" for i in ids]))
            add("p5-role-list-large-skewed-json", ["role", "list", "-o", "json"], size, roles_seq,
                family="role-list-skewed", output="json", detail_requests=len(names), stdout=role_json(inv),
                delays=skewed_delays(seed, [f"GET {API}role/{n}" for n in names]))
        # Partial failures: text renders partial rows then fails; JSON emits nothing.
        failing = [ids[2]] if size == "small" else ids[::100]
        faults = [{"method": "GET", "path": f"{API}user/{i}/role", "status": 500, "body": "detail failure"}
                  for i in failing]
        failed_seq = [{"exact": [req("GET", "user")]},
                      {"unordered": [req("GET", f"user/{i}/role", 500 if i in failing else 200) for i in ids]}]
        causes = [f"Error fetching roles for {i}: {USER_DETAIL_ERROR}" for i in failing]
        add(f"p5-user-list-{size}-detail-failure-text", ["user", "list"], size, failed_seq,
            family="user-list-failure", exit_code=1, faults=faults, detail_requests=len(ids),
            stdout=user_text(inv, failing),
            err_contains=[f"failed to fetch roles for {len(failing)} user(s): {causes[0]}", *causes[1:]])
        add(f"p5-user-list-{size}-detail-failure-json", ["user", "list", "-o", "json"], size, failed_seq,
            family="user-list-failure", output="json", exit_code=1, faults=faults, detail_requests=len(ids),
            err_json_code="COMMAND_FAILED")
        bad_role = names[3]
        add(f"p5-role-list-{size}-detail-failure-json", ["role", "list", "-o", "json"], size,
            [{"exact": [req("GET", "roles")]},
             {"unordered": [req("GET", f"role/{n}", 500 if n == bad_role else 200) for n in names]}],
            family="role-list-failure", output="json", exit_code=1, detail_requests=len(names),
            faults=[{"method": "GET", "path": f"{API}role/{bad_role}", "status": 500, "body": "detail failure"}],
            err_json_code="COMMAND_FAILED")

    small = inventories["small"]

    def changed(inv, mutate, commit, token_grants=0):
        state = baseline_state(inv, token_grants)
        mutate(state)
        state["commits"] = [commit] if commit else []
        return state

    def with_dataset(kind):
        return lambda s: s["datasets"].__setitem__(NEW_DATASET, {"streamType": "UserDefined", "telemetryType": kind})

    # ----- datasets: status-only success, typed guard, failures ---------------
    def put_headers(kind):
        return {f"PUT {API}logstream/{NEW_DATASET}": {"x-p-telemetry-type": kind, "x-p-log-source": LOG_SOURCE[kind]}}

    for kind in ("logs", "metrics", "traces"):
        add(f"p5-dataset-add-{kind}", ["dataset", "add", NEW_DATASET, "--type", kind], "small",
            [{"exact": [req("PUT", f"logstream/{NEW_DATASET}")]}], family="dataset-add",
            state=changed(small, with_dataset(kind), f"PUT {API}logstream/{NEW_DATASET}"),
            stdout=exact(f"Created {kind} dataset {NEW_DATASET}\n"), headers=put_headers(kind))
    add("p5-dataset-add-status-only-slow-body", ["dataset", "add", NEW_DATASET, "--type", "metrics"], "small",
        [{"exact": [req("PUT", f"logstream/{NEW_DATASET}")]}], family="dataset-add-body",
        state=changed(small, with_dataset("metrics"), f"PUT {API}logstream/{NEW_DATASET}"),
        stdout=exact(f"Created metrics dataset {NEW_DATASET}\n"), headers=put_headers("metrics"),
        tails={f"PUT {API}logstream/{NEW_DATASET}": STATUS_ONLY_TAIL},
        body={"request": f"PUT {API}logstream/{NEW_DATASET}", "policy": "status-only"})
    add("p5-dataset-add-conflict", ["dataset", "add", "dataset-logs", "--type", "logs"], "small",
        [{"exact": [req("PUT", "logstream/dataset-logs", 400)]}], family="dataset-add-failure", exit_code=1,
        err_contains=["create dataset failed: 400 Bad Request: stream already exists"])
    add("p5-dataset-add-non200-2xx", ["dataset", "add", NEW_DATASET, "--type", "traces"], "small",
        [{"exact": [req("PUT", f"logstream/{NEW_DATASET}", 201)]}], family="dataset-add-failure", exit_code=1,
        faults=[{"method": "PUT", "path": f"{API}logstream/{NEW_DATASET}", "status": 201, "body": "created"}],
        err_contains=["create dataset failed: 201", ": created"])
    add("p5-dataset-remove-guarded", ["dataset", "remove", "dataset-metrics", "--type", "metrics"], "small",
        [{"exact": [req("GET", "logstream/dataset-metrics/info"), req("DELETE", "logstream/dataset-metrics")]}],
        family="dataset-remove", stdout=exact("Successfully deleted dataset dataset-metrics\n"),
        state=changed(small, lambda s: s["datasets"].pop("dataset-metrics"), f"DELETE {API}logstream/dataset-metrics"))
    add("p5-dataset-remove-guarded-mismatch", ["dataset", "rm", "dataset-logs", "--type", "traces"], "small",
        [{"exact": [req("GET", "logstream/dataset-logs/info")]}], family="dataset-remove-failure", exit_code=1,
        err_contains=['dataset "dataset-logs" is logs, not traces'])
    add("p5-dataset-remove-unguarded", ["dataset", "rm", "dataset-traces"], "small",
        [{"exact": [req("DELETE", "logstream/dataset-traces")]}], family="dataset-remove",
        stdout=exact("Successfully deleted dataset dataset-traces\n"),
        state=changed(small, lambda s: s["datasets"].pop("dataset-traces"), f"DELETE {API}logstream/dataset-traces"))
    add("p5-dataset-remove-missing", ["dataset", "rm", "p5-missing"], "small",
        [{"exact": [req("DELETE", "logstream/p5-missing", 404)]}], family="dataset-remove-failure", exit_code=1,
        err_contains=["delete dataset failed: 404 Not Found: stream not found"])
    add("p5-dataset-remove-status-only-slow-body", ["dataset", "rm", "dataset-traces"], "small",
        [{"exact": [req("DELETE", "logstream/dataset-traces")]}], family="dataset-remove-body",
        stdout=exact("Successfully deleted dataset dataset-traces\n"),
        tails={f"DELETE {API}logstream/dataset-traces": STATUS_ONLY_TAIL},
        body={"request": f"DELETE {API}logstream/dataset-traces", "policy": "status-only"},
        state=changed(small, lambda s: s["datasets"].pop("dataset-traces"), f"DELETE {API}logstream/dataset-traces"))

    # ----- users: create (read-body), additive roles, removal, failures ------
    def created(inv, roles):
        def mutate(s):
            s["users"].append({"id": NEW_USER, "method": "native", "roles": list(dict.fromkeys(roles)), "groups": {}})
        return changed(inv, mutate, f"POST {API}user/{NEW_USER}")

    def added_user(password, roles_text):
        return exact(f"Added user: {NEW_USER} \nPassword is: {password}\nRole(s) assigned: {roles_text}\n")

    for size, inv in inventories.items():
        roles = ["role-0000", "role-0003"]
        add(f"p5-user-add-{size}", ["user", "add", NEW_USER, "--role", "role-0000,role-0003"], size,
            [{"exact": [req("GET", "user"), req("GET", "roles"), req("POST", f"user/{NEW_USER}", body=roles)]}],
            family="user-add", state=created(inv, roles), stdout=added_user(f"pw-{NEW_USER}", "role-0000,role-0003"))
        target = inv["users"][2]
        add_roles = ["role-0004", "role-0001"]

        def additive(s, uid=target["id"]):
            user = next(u for u in s["users"] if u["id"] == uid)
            user["roles"] = list(dict.fromkeys([*user["roles"], *add_roles]))
        add(f"p5-user-set-role-{size}", ["user", "set-role", target["id"], "role-0004, role-0001"], size,
            [{"exact": [req("GET", "user"), req("GET", "roles"),
                        req("PATCH", f"user/{target['id']}/role/add", body=add_roles)]}],
            family="user-set-role", state=changed(inv, additive, f"PATCH {API}user/{target['id']}/role/add"),
            stdout=exact(f"Added role(s) role-0004, role-0001 to user {target['id']}\n"))
    # Payload keeps duplicates/order even though fixture state deduplicates.
    dup_roles = ["role-0003", "role-0000", "role-0003"]
    add("p5-user-add-duplicate-roles", ["user", "add", NEW_USER, "-r", " role-0003, role-0000,role-0003 "], "small",
        [{"exact": [req("GET", "user"), req("GET", "roles"), req("POST", f"user/{NEW_USER}", body=dup_roles)]}],
        family="user-add", state=created(small, dup_roles),
        stdout=added_user(f"pw-{NEW_USER}", "role-0003, role-0000,role-0003"))
    slow_password = "pw-" + "s" * 4093
    add("p5-user-add-read-body-slow", ["user", "add", NEW_USER, "--role", "role-0000"], "small",
        [{"exact": [req("GET", "user"), req("GET", "roles"), req("POST", f"user/{NEW_USER}", body=["role-0000"])]}],
        family="user-add-body", state=created(small, ["role-0000"]), stdout=added_user(slow_password, "role-0000"),
        tails={f"POST {API}user/{NEW_USER}": {"delay_ms": 150, "split": 16, "body": slow_password}},
        body={"request": f"POST {API}user/{NEW_USER}", "policy": "read-body"})
    add("p5-user-add-existing", ["user", "add", "user-00001", "--role", "role-0000"], "small",
        [{"exact": [req("GET", "user")]}], family="user-add-failure", exit_code=1,
        err_contains=["user user-00001 already exists"])
    add("p5-user-add-missing-role", ["user", "add", NEW_USER, "--role", "role-0000,nope"], "small",
        [{"exact": [req("GET", "user"), req("GET", "roles")]}], family="user-add-failure", exit_code=1,
        err_contains=["role nope doesn't exist; create it first with `pb role add nope`"])
    add("p5-user-add-cloud-rejected", ["user", "add", NEW_USER, "--role", "role-0000"], "small", [],
        family="user-add-failure", auth="cloud-key", exit_code=1,
        err_contains=["`pb user add` is not available for Parseable Cloud"])
    add("p5-user-set-role-missing-user", ["user", "set-role", "p5-nobody", "role-0000"], "small",
        [{"exact": [req("GET", "user")]}], family="user-set-role-failure", exit_code=1,
        err_contains=["user doesn't exist. Please create the user with `pb user add p5-nobody`"])
    add("p5-user-set-role-server-error", ["user", "set-role", "user-00002", "role-0000"], "small",
        [{"exact": [req("GET", "user"), req("GET", "roles"), req("PATCH", "user/user-00002/role/add", 500, ["role-0000"])]}],
        family="user-set-role-failure", exit_code=1,
        err_contains=["set user roles failed: 500 Internal Server Error: boom"],
        faults=[{"method": "PATCH", "path": f"{API}user/user-00002/role/add", "status": 500, "body": "boom"}])
    add("p5-user-remove", ["user", "rm", "user-00003"], "small", [{"exact": [req("DELETE", "user/user-00003")]}],
        family="user-remove", stdout=exact("Removed user user-00003\n"),
        state=changed(small, lambda s: s["users"].pop(3), f"DELETE {API}user/user-00003"))
    add("p5-user-remove-missing", ["user", "remove", "p5-nobody"], "small",
        [{"exact": [req("DELETE", "user/p5-nobody", 404)]}], family="user-remove-failure", exit_code=1,
        err_contains=["delete user failed: 404 Not Found: user does not exist"])
    add("p5-user-remove-status-only-slow-body", ["user", "rm", "user-00003"], "small",
        [{"exact": [req("DELETE", "user/user-00003")]}], family="user-remove-body",
        stdout=exact("Removed user user-00003\n"),
        tails={f"DELETE {API}user/user-00003": STATUS_ONLY_TAIL},
        body={"request": f"DELETE {API}user/user-00003", "policy": "status-only"},
        state=changed(small, lambda s: s["users"].pop(3), f"DELETE {API}user/user-00003"))

    # ----- roles: existence-checked removal ----------------------------------
    for size, inv in inventories.items():
        unassigned = list(inv["roles"])[-1]
        add(f"p5-role-remove-{size}", ["role", "rm", unassigned], size,
            [{"exact": [req("GET", "roles"), req("DELETE", f"role/{unassigned}")]}], family="role-remove",
            stdout=exact(f"Removed role {unassigned}\n"),
            state=changed(inv, lambda s, r=unassigned: s["roles"].pop(r), f"DELETE {API}role/{unassigned}"))
    large_names = sorted(inventories["large"]["roles"])
    add("p5-role-remove-missing", ["role", "remove", "nope"], "large", [{"exact": [req("GET", "roles")]}],
        family="role-remove-failure", exit_code=1,
        err_contains=["role nope doesn't exist. Available role names: " + ", ".join(large_names)])
    add("p5-role-remove-status-only-slow-body", ["role", "rm", "role-0004"], "small",
        [{"exact": [req("GET", "roles"), req("DELETE", "role/role-0004")]}], family="role-remove-body",
        stdout=exact("Removed role role-0004\n"), tails={f"DELETE {API}role/role-0004": STATUS_ONLY_TAIL},
        body={"request": f"DELETE {API}role/role-0004", "policy": "status-only"},
        state=changed(small, lambda s: s["roles"].pop("role-0004"), f"DELETE {API}role/role-0004"))

    # ----- OAuth refresh: one grant, replay, contention, rejection -----------
    token = req("POST", TOKEN_PATH, body={"grant_type": "refresh_token", "refresh_token": OLD_REFRESH}, auth="token")
    for size in ("small", "large"):
        inv = inventories[size]
        ids = [u["id"] for u in inv["users"]]
        add(f"p5-refresh-user-list-{size}-json", ["user", "list", "-o", "json"], size,
            [{"exact": [req("GET", "user", 401, auth="old-rejected"), token, req("GET", "user", auth="new")]},
             {"unordered": [req("GET", f"user/{i}/role", auth="new") for i in ids]}],
            family="refresh-user-list", output="json", auth="refresh", config="rotated", detail_requests=len(ids),
            stdout=user_json(inv))
    large = inventories["large"]
    ids, names = [u["id"] for u in large["users"]], list(large["roles"])
    add("p5-refresh-user-list-large-contention-json", ["user", "list", "-o", "json"], "large",
        {"rule": "refresh-fanout", "prefix": [req("GET", "user", auth="old")], "token": token,
         "details": [req("GET", f"user/{i}/role", auth="new") for i in ids]},
        family="refresh-contention", output="json", auth="refresh", config="rotated", old_budget=1,
        detail_requests=len(ids), stdout=user_json(large))
    add("p5-refresh-role-list-large-contention-json", ["role", "list", "-o", "json"], "large",
        {"rule": "refresh-fanout", "prefix": [req("GET", "roles", auth="old")], "token": token,
         "details": [req("GET", f"role/{n}", auth="new") for n in names]},
        family="refresh-contention", output="json", auth="refresh", config="rotated", old_budget=1,
        detail_requests=len(names), stdout=role_json(large))
    add("p5-refresh-dataset-add", ["dataset", "add", NEW_DATASET, "--type", "traces"], "small",
        [{"exact": [req("PUT", f"logstream/{NEW_DATASET}", 401, auth="old-rejected"), token,
                    req("PUT", f"logstream/{NEW_DATASET}", auth="new")]}],
        family="refresh-mutation", auth="refresh", config="rotated",
        stdout=exact(f"Created traces dataset {NEW_DATASET}\n"), headers=put_headers("traces"),
        state=changed(small, with_dataset("traces"), f"PUT {API}logstream/{NEW_DATASET}", token_grants=1))
    replay_roles = ["role-0004", "role-0001"]
    add("p5-refresh-set-role-replay", ["user", "set-role", "user-00002", "role-0004,role-0001"], "small",
        [{"exact": [req("GET", "user", auth="old"), req("GET", "roles", auth="old"),
                    req("PATCH", "user/user-00002/role/add", 401, replay_roles, auth="old-rejected"), token,
                    req("PATCH", "user/user-00002/role/add", 200, replay_roles, auth="new")]}],
        family="refresh-mutation", auth="refresh", config="rotated", old_budget=2,
        stdout=exact("Added role(s) role-0004,role-0001 to user user-00002\n"),
        state=changed(small, lambda s: s["users"][2].__setitem__(
            "roles", list(dict.fromkeys([*s["users"][2]["roles"], *replay_roles]))),
            f"PATCH {API}user/user-00002/role/add", token_grants=1))
    add("p5-refresh-rejected-no-commit", ["user", "rm", "user-00003"], "small",
        [{"exact": [req("DELETE", "user/user-00003", 401, auth="old-rejected"), {**token, "status": 400}]}],
        family="refresh-failure", auth="refresh", grant="reject", exit_code=1,
        err_contains=["cloud session refresh failed"], state=baseline_state(small, 0))
    return result


# ----- stateful loopback fixture (runs in its own process) -------------------

class AdminState:
    """Disposable administration model. Endpoints mirror the Go client's calls (phase5.py Model)."""

    def __init__(self, scenario):
        scenario = copy.deepcopy(scenario)
        self.lock = threading.Condition()
        self.users = scenario["state"]["users"]
        self.roles = scenario["state"]["roles"]
        self.datasets = scenario["state"]["datasets"]
        self.auth = scenario["auth"]
        self.old_budget = self.auth.get("old_budget", 0)
        self.faults = scenario.get("faults", [])
        self.delays = scenario.get("delays", {})
        self.tails = scenario.get("tails", {})
        self.commits = []
        self.token_grants = 0
        self.ledger = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.handlers = 0  # handlers between request dispatch and their final write/close

    def snapshot(self):
        with self.lock:
            return {"users": copy.deepcopy(self.users), "roles": copy.deepcopy(self.roles),
                    "datasets": copy.deepcopy(self.datasets), "commits": list(self.commits),
                    "token_grants": self.token_grants}

    def enter(self):
        with self.lock:
            self.handlers += 1

    def leave(self):
        with self.lock:
            self.handlers -= 1
            self.lock.notify_all()

    def quiesce(self, timeout):
        """Wait until every handler (including withheld status-only tails) has finished."""
        with self.lock:
            return self.lock.wait_for(lambda: self.handlers == 0, timeout)

    def _authorize(self, headers):
        if self.auth["mode"] == "static":
            return "static" if all(headers.get(k) == v for k, v in self.auth["headers"].items()) else "rejected"
        cookies = dict(part.strip().partition("=")[::2] for part in headers.get("cookie", "").split(";") if part.strip())
        session = cookies.get("session")
        if headers.get("x-p-tenant") != TENANT:
            return "rejected"
        if session == NEW_SESSION and self.token_grants:
            return "new"
        if session == OLD_SESSION:
            if self.old_budget > 0:
                self.old_budget -= 1
                return "old"
            return "old-rejected"
        return "rejected"

    def _token(self, headers, body):
        try:
            grant = json.loads(body)
        except ValueError:
            grant = None
        if (headers.get("authorization") != "Bearer " + ORCHESTRATOR_TOKEN or self.auth["mode"] != "refresh"
                or headers.get("content-type") != "application/json" or headers.get("accept") != "application/json"
                or grant != {"grant_type": "refresh_token", "refresh_token": OLD_REFRESH}
                or self.auth.get("grant", "accept") != "accept"):
            return 400, b'{"error":"invalid_grant"}'
        self.token_grants += 1
        return 200, _encode({"access_token": NEW_SESSION, "refresh_token": NEW_REFRESH, "token_type": "bearer"})

    def _user(self, uid):
        return next((u for u in self.users if u["id"] == uid), None)

    def _detail(self, user):
        def wrap(name):
            return {"actions": self.roles.get(name)}  # null actions stay null (phase5.Model.detail)
        return {"roles": {name: wrap(name) for name in user["roles"]},
                "group_roles": {g: {name: wrap(name) for name in names} for g, names in user["groups"].items()}}

    def _api(self, method, path, headers, body):
        parts = path[len(API):].split("/")
        key = f"{method} {path}"
        try:
            payload = json.loads(body) if body else None
        except ValueError:
            payload = None
        if parts == ["user"] and method == "GET":
            return 200, _encode([{"id": u["id"], "method": u["method"]} for u in self.users])
        if parts == ["roles"] and method == "GET":
            return 200, _encode(self.roles)
        if len(parts) == 3 and parts[0] == "user" and parts[2] == "role" and method == "GET":
            user = self._user(parts[1])
            return (200, _encode(self._detail(user))) if user else (404, b"user does not exist")
        if len(parts) == 2 and parts[0] == "role":
            if parts[1] not in self.roles:
                return 404, b"role does not exist"
            if method == "GET":
                return 200, _encode({"actions": self.roles[parts[1]]})
            if method == "DELETE":
                del self.roles[parts[1]]
                self.commits.append(key)
                return 200, b""
        if len(parts) == 2 and parts[0] == "user" and method in ("POST", "DELETE"):
            user = self._user(parts[1])
            if method == "DELETE":
                if not user:
                    return 404, b"user does not exist"
                self.users.remove(user)
                self.commits.append(key)
                return 200, b""
            if user:
                return 400, b"user already exists"
            if not isinstance(payload, list) or any(r not in self.roles for r in payload):
                return 400, b"invalid roles"
            self.users.append({"id": parts[1], "method": "native", "roles": list(dict.fromkeys(payload)), "groups": {}})
            self.commits.append(key)
            return 200, f"pw-{parts[1]}".encode()
        if len(parts) == 4 and parts[0] == "user" and parts[2:] == ["role", "add"] and method == "PATCH":
            user = self._user(parts[1])
            if not user:
                return 404, b"user does not exist"
            if not isinstance(payload, list) or any(r not in self.roles for r in payload):
                return 400, b"invalid roles"
            user["roles"] = list(dict.fromkeys([*user["roles"], *payload]))  # additive
            self.commits.append(key)
            return 200, b""
        if len(parts) == 2 and parts[0] == "logstream" and method in ("PUT", "DELETE"):
            name = parts[1]
            if method == "PUT":
                kind = headers.get("x-p-telemetry-type")
                if name in self.datasets:
                    return 400, b"stream already exists"
                if kind not in LOG_SOURCE or headers.get("x-p-log-source") != LOG_SOURCE[kind]:
                    return 400, b"invalid telemetry headers"
                self.datasets[name] = {"streamType": "UserDefined", "telemetryType": kind}
            else:
                if name not in self.datasets:
                    return 404, b"stream not found"
                del self.datasets[name]
            self.commits.append(key)
            return 200, b""
        if len(parts) == 3 and parts[0] == "logstream" and parts[2] == "info" and method == "GET":
            info = self.datasets.get(parts[1])
            return (200, _encode(info)) if info else (404, b"stream not found")
        return None

    def respond(self, method, raw_path, headers, body, arrived_ns=None):
        """Decide one response under the lock. Returns (entry, status, payload, delay_ms, tail)."""
        path = urllib.parse.unquote(urllib.parse.urlsplit(raw_path).path)
        key = f"{method} {path}"
        with self.lock:
            entry = {"index": len(self.ledger), "method": method, "uri": raw_path, "path": path,
                     "headers": dict(sorted(headers.items())),
                     "body_b64": base64.b64encode(body).decode(),
                     "received_ns": arrived_ns if arrived_ns is not None else mono_ns(),
                     "in_flight_at_arrival": self.in_flight + 1, "unexpected": False, "fault": False}
            self.ledger.append(entry)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            delay, tail = self.delays.get(key, 0), None
            if path == TOKEN_PATH and method == "POST":
                entry["auth"] = "token"
                status, payload = self._token(headers, body)
            else:
                entry["auth"] = self._authorize(headers)
                fault = next((f for f in self.faults if f["method"] == method and f["path"] == path
                              and f.get("times", 1) != 0), None)
                if entry["auth"] in ("rejected", "old-rejected"):
                    status, payload = 401, b"fixture: unauthorized"
                elif fault is not None:
                    if "times" in fault:
                        fault["times"] -= 1
                    entry["fault"] = True
                    status, payload = fault["status"], fault["body"].encode()
                else:
                    decided = self._api(method, path, headers, body) if path.startswith(API) else None
                    if decided is None:
                        entry["unexpected"] = True
                        status, payload = 500, b"fixture: unexpected request"
                    else:
                        status, payload = decided
                        if status == 200 and key in self.tails:
                            tail = self.tails[key]
                            payload = tail["body"].encode()
            entry["status"] = status
            entry["response_body_bytes"] = len(payload)
        return entry, status, payload, delay, tail

    def mark(self, entry, **fields):
        # Ledger entries are serialized under the lock by the control endpoint.
        with self.lock:
            entry.update(fields)

    def finish(self, entry):
        with self.lock:
            entry["completed_ns"] = mono_ns()
            self.in_flight -= 1


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")).encode()


def _read_body(handler):
    if handler.headers.get("Transfer-Encoding", "").lower() == "chunked":
        body = bytearray()
        while True:
            size = int(handler.rfile.readline().split(b";")[0].strip() or b"0", 16)
            if not size:
                while handler.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                return bytes(body)
            body += handler.rfile.read(size)
            handler.rfile.readline()
    length = int(handler.headers.get("Content-Length") or 0)
    return handler.rfile.read(length) if length else b""


def _request_headers(handler):
    """Every request header except user-agent; repeats joined; fixture host:port normalized."""
    host = f"127.0.0.1:{handler.server.server_address[1]}"
    headers = {}
    for name, value in handler.headers.items():
        name = name.lower()
        if name in IGNORED_REQUEST_HEADERS:
            continue
        value = value.replace(host, "<FIXTURE>")
        headers[name] = headers[name] + "," + value if name in headers else value
    return headers


def _send_json(handler, value, status=200):
    payload = value if isinstance(value, bytes) else _encode(value)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _control(handler):
    server = handler.server
    parsed = urllib.parse.urlsplit(handler.path)
    name, query = parsed.path[len("/__harness/"):], urllib.parse.parse_qs(parsed.query)
    if name == "reset":
        # Between samples: the previous CLI has exited; wait for its handlers, then restore.
        with server.reset_lock:
            previous = server.state
            quiesced = previous.quiesce(QUIESCE_TIMEOUT_S)
            server.state = AdminState(server.scenario)
            return _send_json(handler, {"quiesced": quiesced, "previous_requests": len(previous.ledger),
                                        "state": server.state.snapshot(), "ledger": len(server.state.ledger)})
    state = server.state
    if name == "ledger":
        quiesced = state.quiesce(QUIESCE_TIMEOUT_S) if "quiesce" in query else None
        with state.lock:  # serialize under the lock, send outside it
            payload = _encode({"ledger": state.ledger, "max_in_flight": state.max_in_flight, "quiesced": quiesced})
        return _send_json(handler, payload)
    if name == "state":
        return _send_json(handler, state.snapshot())
    return _send_json(handler, {"error": "unknown control endpoint"}, 404)


def _handle(handler):
    arrived = mono_ns()  # request line and headers parsed; body not yet read
    if handler.path.startswith("/__harness/"):
        return _control(handler)
    state = handler.server.state
    state.enter()
    try:
        body = _read_body(handler)
        headers = _request_headers(handler)
        entry, status, payload, delay, tail = state.respond(handler.command, handler.path, headers, body, arrived)
        written = 0
        try:
            if delay:
                time.sleep(delay / 1000)
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            state.mark(entry, headers_ns=mono_ns())
            head = payload[:len(payload) - tail["split"]] if tail else b""
            if head:
                # Status-only clients must be able to finish while this tail is withheld.
                handler.wfile.write(head)
                handler.wfile.flush()
                written = len(head)
                state.mark(entry, first_body_ns=mono_ns(), body_bytes_written=written)
                time.sleep(tail["delay_ms"] / 1000)
            state.mark(entry, final_write_started_ns=mono_ns())
            handler.wfile.write(payload[written:])
            handler.wfile.flush()
            state.mark(entry, final_body_ns=mono_ns(), body_bytes_written=len(payload))
        except OSError:  # client closed: broken pipe, reset, aborted
            state.mark(entry, client_closed=True, body_bytes_written=written)
            handler.close_connection = True
        finally:
            state.finish(entry)
    finally:
        state.leave()


def serve(scenario_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import resource
    _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))  # Go fans out one connection per detail.

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        # Match the Go fixture: avoid artificial delayed-ACK stalls between
        # separately written response headers and small response bodies.
        disable_nagle_algorithm = True
        timeout = 60

        def log_message(self, *args):
            pass

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 4096  # avoid SYN-queue drops (1 s retransmits) under fan-out

    server = Server(("127.0.0.1", 0), Handler)
    server.scenario = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
    server.state = AdminState(server.scenario)
    server.reset_lock = threading.Lock()
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_address[1]}"}), flush=True)
    server.serve_forever(poll_interval=0.05)


class AdminFixture:
    """One fixture process: identical initial state; `reset` restores it between samples."""

    def __init__(self, scenario, root):
        self.proc = None
        root.mkdir(parents=True, exist_ok=True)
        path = root / "scenario.json"
        path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
        self.log = (root / "fixture.stderr").open("wb")
        self.proc = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "serve", "--scenario", str(path)],
                                     stdout=subprocess.PIPE, stderr=self.log, start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise RuntimeError("fixture did not become ready within 10s")
            self.url = json.loads(self.proc.stdout.readline())["url"]
        except Exception as error:
            self.close()
            details = (root / "fixture.stderr").read_text(errors="replace")[:4096]
            raise RuntimeError(f"fixture startup failed: {details or error}") from error
        if not self.url.startswith("http://127.0.0.1:"):
            self.close()
            raise RuntimeError("fixture must listen on loopback only")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def control(self, name):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(self.url + "/__harness/" + name, timeout=QUIESCE_TIMEOUT_S + 10) as response:
            return json.load(response)

    def reset(self):
        return self.control("reset")

    def close(self):
        if self.proc:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait()
            self.proc.stdout.close()
            self.proc = None
        self.log.close()


class SharedFixture:
    """Per-workload fixture reused across samples and binaries; restarted if it dies."""

    def __init__(self, scenario):
        self.scenario, self.fixture, self.temp = scenario, None, None

    def get(self):
        if self.fixture is None or not self.fixture.alive():
            self.close()
            self.temp = tempfile.TemporaryDirectory(prefix="pb-p5-fixture-")
            self.fixture = AdminFixture(self.scenario, Path(self.temp.name))
        return self.fixture

    def close(self):
        if self.fixture is not None:
            self.fixture.close()
            self.fixture = None
        if self.temp is not None:
            self.temp.cleanup()
            self.temp = None


# ----- scoped comparison rules ------------------------------------------------

def _body(entry):
    data = base64.b64decode(entry["body_b64"])
    if not data:
        return None
    try:
        return json.loads(data)
    except ValueError:
        return {"raw": data.decode("utf-8", "backslashreplace")}


def project(entry):
    """Independent-expectation view of a ledger entry."""
    return {"method": entry["method"], "path": entry["path"], "status": entry["status"],
            "body": _body(entry), "auth": entry["auth"]}


def request_key(entry):
    """Oracle view: raw request target, raw body bytes and every recorded header (user-agent excepted)."""
    return {**project(entry), "uri": entry["uri"], "body_b64": entry["body_b64"], "headers": entry["headers"]}


def _sorted(items):
    return sorted(items, key=canonical)


def _label(entry):
    return f"{entry['method']} {entry['path']}"


def _causal_issues(before, afters, milestone, what):
    """Same-clock barrier: each later request must arrive no earlier than `before`'s milestone."""
    if before is None:
        return []
    bound = before.get(milestone)
    if bound is None:
        return []  # unrecorded (synthetic ledger); run_sample separately requires complete milestones
    early = [e for e in afters if e.get("received_ns") is not None and e["received_ns"] < bound]
    return [f"{len(early)} request(s) (first {_label(early[0])}) arrived before {_label(before)} {what}"] if early else []


def _raw_body_issues(entries, items):
    """Exact payload bytes for JSON string arrays (Go json.Marshal), not only semantic equality."""
    issues = []
    for entry, item in zip(entries, items):
        if isinstance(item.get("body"), list) and base64.b64decode(entry["body_b64"]) != _compact(item["body"]):
            issues.append(f"{_label(entry)} body bytes differ from compact JSON")
    return issues


def check_requests(ledger, expected):
    """Exact order except inside declared fan-out segments, which keep multiplicity.

    Exact segments are causally sequential: each request arrives after the previous
    response's headers. A fan-out arrives after the list response body started.
    """
    if isinstance(expected, dict):
        return check_refresh_fanout(ledger, expected)
    issues, position, previous = [], 0, None
    for segment in expected:
        (kind, items), = segment.items()
        entries = ledger[position:position + len(items)]
        chunk = [project(e) for e in entries]
        if len(chunk) < len(items):
            issues.append(f"{kind} segment at {position}: {len(chunk)} requests, expected {len(items)}")
        elif kind == "exact":
            if chunk != items:
                issues.append(f"requests at {position} differ from the exact causal sequence")
            issues += _raw_body_issues(entries, items)
            for earlier, later in zip([previous, *entries], entries):
                issues += _causal_issues(earlier, [later], "headers_ns", "its predecessor's response headers")
        elif kind == "unordered":
            if _sorted(chunk) != _sorted(items):
                issues.append(f"fan-out membership/multiplicity at {position} differs")
            issues += _causal_issues(previous, entries, "final_write_started_ns", "the list body was written")
        previous = entries[-1] if kind == "exact" and entries else None
        position += len(items)
    if position < len(ledger):
        issues.append(f"{len(ledger) - position} unexpected extra requests")
    return issues


def check_refresh_fanout(ledger, rule):
    """Concurrent 401s within a fan-out.

    The number of rejected siblings is scheduler dependent (requests sent after the
    refresh carry the rotated session directly), so it is recorded rather than compared.
    Everything causal is enforced:
      - the prefix is exact
      - exactly one grant follows at least one rejection
      - nothing outside the fan-out is rejected
      - no rotated session is used before the grant
      - every rejected detail is replayed exactly once, after its rejection
      - final detail membership is exact
      - the list-before-detail and grant-before-replay barriers hold
    """
    issues = []
    n = len(rule["prefix"])
    head, rest = ledger[:n], ledger[n:]
    if [project(e) for e in head] != rule["prefix"]:
        issues.append("refresh fan-out prefix differs")
    tokens = [i for i, e in enumerate(rest) if e["auth"] == "token"]
    if len(tokens) != 1 or project(rest[tokens[0]]) != rule["token"]:
        return issues + [f"expected exactly one successful refresh grant, saw {len(tokens)}"]
    t = tokens[0]
    token = rest[t]
    old = [(i, e) for i, e in enumerate(rest) if e["auth"] == "old-rejected"]
    new = [(i, e) for i, e in enumerate(rest) if e["auth"] == "new"]
    if len(old) + len(new) + 1 != len(rest):
        issues.append("requests outside the refresh/replay protocol")
    if any(i < t for i, _ in new):
        issues.append("rotated session used before the grant")
    if not old or any(e["status"] != 401 for _, e in old):
        issues.append("no rejected sibling triggered the refresh")
    elif old[0][0] > t:
        issues.append("refresh grant requested before any rejection")
    detail_keys = {(d["method"], d["path"]) for d in rule["details"]}
    if any((e["method"], e["path"]) not in detail_keys for _, e in old):
        issues.append("a request outside the fan-out was rejected")
    if _sorted(project(e) for _, e in new) != _sorted(rule["details"]):
        issues.append("replayed/new detail membership or multiplicity differs")
    rejected = collections.Counter(e["path"] for _, e in old)
    replay_at = {}
    for i, e in new:
        replay_at.setdefault(e["path"], i)
    if any(rejected[e["path"]] > 1 or replay_at.get(e["path"], -1) < i for i, e in old):
        issues.append("rejected detail not replayed exactly once after its rejection")
    issues += _causal_issues(head[-1] if head else None, rest, "final_write_started_ns", "the list body was written")
    if old:
        issues += _causal_issues(old[0][1], [token], "headers_ns", "the first 401 response")
    issues += _causal_issues(token, [e for _, e in new], "final_write_started_ns", "the grant body was written")
    return issues


def replay_issues(ledger):
    """An OAuth replay must resend the rejected request byte-identically, apart from the session cookie."""
    issues = []
    for index, entry in enumerate(ledger):
        if entry.get("auth") != "old-rejected":
            continue
        replay = next((e for e in ledger[index + 1:] if e.get("auth") == "new"
                       and e["method"] == entry["method"] and e["uri"] == entry["uri"]), None)
        if replay is None:
            continue  # absence is judged by the request rule (rejection may legitimately end the command)
        strip = lambda h: {k: v for k, v in h.items() if k != "cookie"}  # noqa: E731
        if replay["body_b64"] != entry["body_b64"] or strip(replay["headers"]) != strip(entry["headers"]):
            issues.append(f"replay of {_label(entry)} differs from the rejected request (body or headers)")
    return issues


def milestone_issues(ledger):
    """Every request must carry complete same-clock fixture milestones after quiescence."""
    incomplete = [e for e in ledger if e.get("received_ns") is None or e.get("completed_ns") is None
                  or (not e.get("client_closed") and (e.get("headers_ns") is None or e.get("final_write_started_ns") is None))]
    return [f"{len(incomplete)} ledger entries lack fixture milestones"] if incomplete else []


def canonical_requests(ledger, expected):
    keys = [request_key(e) for e in ledger]
    if isinstance(expected, dict):
        n = len(expected["prefix"])
        rest = [(e, k) for e, k in zip(ledger[n:], keys[n:])]
        return {"rule": "refresh-fanout", "prefix": keys[:n],
                "token": [k for e, k in rest if e["auth"] == "token"],
                "details": _sorted(k for e, k in rest if e["auth"] == "new"),
                "rejected_paths": sorted({e["path"] for e, _ in rest if e["auth"] == "old-rejected"} - {
                    e["path"] for e, _ in rest if e["auth"] == "new"})}
    result, position = [], 0
    for segment in expected:
        (kind, items), = segment.items()
        chunk = keys[position:position + len(items)]
        result.append({kind: chunk if kind == "exact" else _sorted(chunk)})
        position += len(items)
    if position < len(keys):
        result.append({"extra": keys[position:]})
    return result


def scoped_stdout(rule, data, role_names=None):
    """(canonical bytes, issues). Only map-ordered role sequences are reordered.

    Text tables use phase5.py's canonicalizers, which its Go self-check validates.
    User JSON must be exactly Go's MarshalIndent form of its own value before each
    roles array is sorted. Role JSON and every other output stay byte-exact.
    """
    del role_names  # kept for call compatibility; the rules are structural
    name = rule["rule"]
    if name == "user-list-text":
        return _phase5().canon_user_text(data), []
    if name == "role-list-text":
        return _phase5().canon_role_text(data), []
    if name == "user-list-json":
        try:
            value = json.loads(data.decode("utf-8"))
            if not isinstance(value, list) or any(not isinstance(item, dict) or not isinstance(item.get("roles"), list)
                                                  or not all(isinstance(r, str) for r in item["roles"]) for item in value):
                raise ValueError("not an array of {id, roles}")
        except (ValueError, UnicodeError) as error:
            return data, [f"user list JSON unreadable: {error}"]
        if _go_json(value) != data:
            return data, ["user list JSON is not Go's indented encoding of its value"]
        for item in value:
            item["roles"] = sorted(item["roles"])
        return _go_json(value), []
    return data, []  # exact, role-list-json (Go sorts map keys)


def _first_difference(actual, expected):
    a, b = actual.split(b"\n"), expected.split(b"\n")
    for index, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"line {index}: {x[:160]!r}, expected {y[:160]!r}"
    return f"{len(a)} lines, expected {len(b)}"


def independent_stdout_issues(rule, data):
    """Compare against the workload model's expected bytes, under the same scoped rule."""
    if rule.get("expected") is None:
        return ["workload declares no independent expected stdout"]
    expected = rule["expected"].encode()
    actual, issues = scoped_stdout(rule, data)
    if issues:
        return issues
    want, _ = scoped_stdout(rule, expected)
    return [] if actual == want else [f"{rule['rule']} stdout differs from the model: {_first_difference(actual, want)}"]


def stderr_json_issues(code, stderr):
    if code is None:
        return []
    try:
        actual = json.loads(stderr)["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return ["stderr is not a JSON error envelope"]
    return [] if actual == code else [f"JSON error code {actual!r}, expected {code!r}"]


# ----- one validated sample --------------------------------------------------

def _run_module():
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import run
    return run


def write_config(root, url, auth):
    run = _run_module()
    if auth != "refresh":
        return run.seed_config(root, url, auth)
    folder = root / "config" / "pb"
    folder.mkdir(parents=True)

    def table(name, fields):
        return f"[Profiles.{name}]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in fields.items())
    text = 'DefaultProfile = "fixture"\n' + table("fixture", {
        "url": url, "cloud": True, "session_token": OLD_SESSION, "refresh_token": OLD_REFRESH,
        "tenant_id": TENANT, "orchestrator_url": url + "/orch"}) + table("bystander", {
        "url": "https://other.invalid", "cloud": True, "session_token": "other-session",
        "refresh_token": "other-refresh", "tenant_id": "other-tenant"})
    (folder / "config.toml").write_text(text)
    (folder / "config.toml").chmod(0o600)


def _profiles(snapshot):
    for item in snapshot.values():
        document = item.get("toml") or {}
        for key, value in document.items():
            if key.lower() == "profiles" and isinstance(value, dict):
                return value
    return {}


def config_issues(expectation, before, after):
    if expectation == "unchanged":
        return [] if before == after else ["configuration changed"]
    profiles, previous = _profiles(after), _profiles(before)
    fixture = profiles.get("fixture", {})
    issues = []
    if fixture.get("session_token") != NEW_SESSION or fixture.get("refresh_token") != NEW_REFRESH:
        issues.append("rotated tokens were not persisted")
    if profiles.get("bystander") != previous.get("bystander"):
        issues.append("unrelated profile changed during refresh")
    return issues


def _ms(ns, start):
    return (ns - start) / 1e6 if ns is not None else None


def max_concurrency(ledger):
    """Requests outstanding between server receipt and the start of their final body write.

    A client cannot have the complete response before that write starts, so a strictly
    sequential client always measures 1; server bookkeeping after the write is excluded.
    """
    events = []
    for e in ledger:
        end = e.get("final_write_started_ns") or e.get("completed_ns") or math.inf
        events += [(e["received_ns"], 1), (end, -1)]
    current = peak = 0
    for _, change in sorted(events, key=lambda item: (item[0], item[1])):  # ends before starts on ties
        current += change
        peak = max(peak, current)
    return peak


def ledger_metrics(result, ledger, fixture_peak, stdout, stderr):
    start = result.get("started_monotonic_ns")
    detail = re.compile(r"^/api/v1/(user/[^/]+/role|role/[^/]+)$")
    out = {"requests_total": len(ledger), "max_in_flight": max_concurrency(ledger),
           "fixture_handler_peak": fixture_peak,
           "detail_requests": sum(1 for e in ledger if e["method"] == "GET" and detail.match(e["path"])),
           "rejected_401_requests": sum(1 for e in ledger if e["status"] == 401),
           "request_body_bytes": sum(len(base64.b64decode(e["body_b64"])) for e in ledger),
           "response_body_bytes": sum(e.get("body_bytes_written", 0) for e in ledger),
           "first_request_clock": "monotonic" if ledger else None,
           "first_request_ms": None, "request_to_stdout_ms": None, "last_response_complete_ms": None,
           "response_to_exit_ms": None}
    out["logical_bytes_total"] = out["request_body_bytes"] + out["response_body_bytes"] + len(stdout) + len(stderr)
    if ledger and start is not None:
        out["first_request_ms"] = _ms(min(e["received_ns"] for e in ledger), start)
        finals = [e["final_body_ns"] for e in ledger if e.get("final_body_ns")]
        if finals:
            out["last_response_complete_ms"] = _ms(max(finals), start)
            out["response_to_exit_ms"] = result["wall_ms"] - out["last_response_complete_ms"]
        if result.get("first_stdout_ms") is not None:
            out["request_to_stdout_ms"] = result["first_stdout_ms"] - out["first_request_ms"]
    return out


def body_policy_issues(policy, result, ledger):
    method, _, path = policy["request"].partition(" ")
    entry = next((e for e in ledger if e["method"] == method and e["path"] == path and e["status"] == 200), None)
    if entry is None or result.get("started_monotonic_ns") is None:
        return ["body-consumption request missing"], None
    ended = result["started_monotonic_ns"] + result["wall_ms"] * 1e6
    final = entry.get("final_write_started_ns")
    exited_first = final is None or ended < final
    if policy["policy"] == "status-only" and not exited_first:
        return ["status-only mutation waited for the success body"], exited_first
    if policy["policy"] == "read-body" and exited_first:
        return ["body-reading mutation exited before the full body was sent"], exited_first
    return [], exited_first


def header_issues(expected, ledger):
    issues = []
    for entry in ledger:
        if entry["path"].startswith(API) and entry["headers"].get("content-type") != "application/json":
            issues.append(f"{entry['method']} {entry['path']} lacks JSON Content-Type")
        want = expected.get(f"{entry['method']} {entry['path']}")
        for name, value in (want or {}).items():
            if entry["headers"].get(name) != value:
                issues.append(f"{entry['method']} {entry['path']} header {name}={entry['headers'].get(name)!r}, expected {value!r}")
    return issues


def _invalid(issues):
    return {"valid": False, "issues": issues, "contract_sha256": None, "scoped_digests": None, "exit_code": None}


def run_sample(binary, workload, meter, artifact=None, oracle=None, shared=None):
    """One validated sample. `shared` (SharedFixture) reuses a reset fixture; otherwise a fresh process."""
    run = _run_module()
    expected = workload["expected"]
    initial = baseline_state(workload["fixture"]["state"])
    owned = None
    with tempfile.TemporaryDirectory(prefix="pb-p5-") as temp:
        root = Path(temp)
        (root / "tmp").mkdir()
        (root / "config").mkdir()
        try:
            if shared is None:
                fixture = owned = AdminFixture(workload["fixture"], root / "fixture")
            else:
                fixture = shared.get()
                reset = fixture.reset()
                if not reset["quiesced"] or reset["state"] != initial or reset["ledger"]:
                    shared.close()
                    return _invalid(["fixture reset did not restore the initial state (fixture restarted)"])
        except (OSError, RuntimeError, ValueError) as error:
            if shared is not None:
                shared.close()
            return _invalid([f"fixture setup failed: {error}"])
        try:
            write_config(root, fixture.url, workload["auth"])
            replacements = {str(root): "<HOME>", fixture.url: "http://<FIXTURE>"}
            before = run.config_snapshot(root, replacements)
            result, stdout, stderr = run.measure([str(binary), *workload["argv"]], run.isolated_env(root), root,
                                                 timeout=workload["timeout_s"], meter=meter)
            # Everything below is outside the measured interval. Quiescence lets withheld
            # tails finish, so the ledger milestones are complete.
            control = fixture.control("ledger?quiesce=1")
            ledger, state = control["ledger"], fixture.control("state")
            after = run.config_snapshot(root, replacements)
            config_path = root / "config" / "pb" / "config.toml"
            config_text = config_path.read_bytes() if config_path.exists() else None
        finally:
            if owned is not None:
                owned.close()
    result.update(ledger_metrics(result, ledger, control["max_in_flight"], stdout, stderr))
    issues = [result["failure"]] if result.get("failure") else []
    if not control.get("quiesced"):
        issues.append("fixture handlers did not finish within the quiescence timeout")
    if result["exit_code"] != expected["exit"]:
        issues.append(f"exit {result['exit_code']}, expected {expected['exit']}")
    if any(e["unexpected"] for e in ledger):
        issues.append("unexpected fixture request")
    issues += milestone_issues(ledger)
    issues += check_requests(ledger, expected["requests"])
    issues += replay_issues(ledger)
    issues += header_issues(expected["headers"], ledger)
    if state != expected["state"]:
        issues.append("final fixture state differs from the model")
    issues += config_issues(expected["config"], before, after)
    normalized_out, normalized_err = run.normalize(stdout, replacements), run.normalize(stderr, replacements)
    issues += independent_stdout_issues(expected["stdout"], normalized_out)
    text_err = normalized_err.decode("utf-8", "replace")
    issues += [f"stderr lacks {s[:200]!r}" for s in expected["stderr_contains"] if s not in text_err]
    issues += stderr_json_issues(expected.get("stderr_json_code"), normalized_err)
    if _stub_marker() in stdout + stderr:
        issues.append(STUB_ISSUE)
    if expected["body"]:
        body_issues, result["exit_before_success_body"] = body_policy_issues(expected["body"], result, ledger)
        issues += body_issues
    scoped_out, scoped_issues = scoped_stdout(expected["stdout"], normalized_out)
    issues += scoped_issues
    scoped = {"stdout_rule": expected["stdout"]["rule"], "stdout_sha256": digest(scoped_out),
              "stderr_sha256": digest(normalized_err), "termination": result["termination"],
              "requests": canonical_requests(ledger, expected["requests"]),
              "config_before": before, "config_after": after, "state_after": state}
    # Per-field digests keep equality semantics without retaining 1000-entry ledgers per sample.
    digests = {key: value if key in ("stdout_rule", "stdout_sha256", "stderr_sha256") else digest(canonical(value))
               for key, value in scoped.items()}
    if oracle is not None:
        differing = sorted(k for k in digests if digests[k] != oracle.get(k))
        if differing:
            issues.append("differs from frozen Go oracle: " + ", ".join(differing))
    result.update(valid=not issues, issues=issues, scoped_digests=digests, contract_sha256=digest(canonical(digests)),
                  raw_stdout_sha256=digest(normalized_out),
                  raw_request_order_sha256=digest(canonical([[e["method"], e["uri"], e["auth"], e["status"]] for e in ledger])),
                  fixture_reset="control" if shared is not None else "process")
    if artifact:
        artifact.mkdir(parents=True)
        (artifact / "stdout.bin").write_bytes(stdout)
        (artifact / "stderr.bin").write_bytes(stderr)
        (artifact / "scoped-stdout.bin").write_bytes(scoped_out)
        for name, value in (("ledger", ledger), ("state-after", state), ("scoped-contract", scoped)):
            with gzip.open(artifact / f"{name}.json.gz", "wt", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
        if config_text is not None:
            (artifact / "config-after.toml").write_bytes(config_text)
        run.write_json(artifact / "sample.json", result)
    return result


STUB_ISSUE = "binary returned a deferred-operation stub"


def _stub_marker():
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import phase2
    return phase2.STUB_MARKER


# ----- statistics and reports ------------------------------------------------

def describe(values):
    run = _run_module()
    median = statistics.median(values)
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0
    return {"n": len(values), "median": median, "mean": mean, "min": min(values), "max": max(values),
            "p90": run.percentile(values, .9), "p95": run.percentile(values, .95), "stdev": stdev,
            "mad": statistics.median(abs(v - median) for v in values), "cv": stdev / mean if mean else None}


def summarize(samples):
    run = _run_module()
    summary = run.summarize(samples)
    for metric, values in summary["metrics"].items():
        values["cv"] = values["stdev"] / values["mean"] if values["mean"] else None
    for metric in EXTRA_METRICS:
        values = [s[metric] for s in samples if s["valid"] and s.get(metric) is not None]
        if values:
            summary["metrics"][metric] = describe(values)
    summary["unavailable"] = {m: "no valid sample produced this observation (for example, no stdout)"
                              for m in (*REPORTED_METRICS, *EXTRA_METRICS) if m not in summary["metrics"]}
    reasons = {s.get("proc_io_unavailable_reason") for s in samples if s.get("proc_io_available") is False}
    if reasons:
        summary["proc_io_unavailable_reasons"] = sorted(r for r in reasons if r)
    return summary


def compare(reference, candidate, metric, seed):
    run = _run_module()

    missing = dict.fromkeys(("stdout_sha256", "stderr_sha256", "termination", "requests", "config_before", "config_after"))

    def view(samples):
        # run.compare_samples checks these fields by equality; digests preserve that.
        return [{"valid": s["valid"], metric: s.get(metric), "contract": s.get("scoped_digests") or missing}
                for s in samples]
    result = run.compare_samples(view(reference), view(candidate), metric, seed)
    states = {(s.get("scoped_digests") or {}).get("state_after") for s in reference + candidate}
    if len(states) > 1:
        result["compatible"] = False
        result["differences"].append("state_after")
    return result


def stage(name, labels, cases, args, out, rng, parent, oracle=None, warmup=None, repetitions=None,
          purpose="measurement"):
    """labels: {label: {"standard": path, "cloud": path|None}}; first label is the reference.

    A skipped workload (missing binary for its build, or no validated oracle) fails the
    stage. Registers itself in parent["stages"] and checkpoints after every workload.
    """
    warmup = args.warmup if warmup is None else warmup
    repetitions = args.repetitions if repetitions is None else repetitions
    meter = Path(args.meter).resolve()
    report = {"labels": list(labels), "reference": next(iter(labels)), "purpose": purpose, "warmup": warmup,
              "repetitions": repetitions, "fixture_reset": args.fixture_reset,
                         "fixture_tcp_nodelay": True, "workloads": {}, "passed": True,
              "order_variation_workloads": [], "skipped_workloads": []}
    parent["stages"][name] = report
    for case in cases:
        if report["workloads"]:
            render(parent, out)
        eligible = [label for label, paths in labels.items() if paths.get(case["build"])]
        entry = {"eligible": eligible, "unavailable": sorted(set(labels) - set(eligible)),
                 "runs": {label: [] for label in eligible}, "warmup_issues": []}
        report["workloads"][case["name"]] = entry
        print(f"{name} {case['name']}:", flush=True)
        skip = None
        if oracle is not None and case["name"] not in oracle:
            skip = "scoped rule not validated by the Go self-comparison"
        elif entry["unavailable"]:
            skip = f"no {case['build']} binary for {', '.join(entry['unavailable'])}"
        if skip:
            entry["skipped"] = skip
            report["skipped_workloads"].append(case["name"])
            report["passed"] = False
            print(f"  skipped (counted as failure): {skip}", flush=True)
            continue
        shared = SharedFixture(case["fixture"]) if args.fixture_reset == "control" else None
        try:
            for round_number in range(-warmup, repetitions):
                order = list(eligible)
                rng.shuffle(order)
                for position, label in enumerate(order):
                    folder = f"warmup-{-round_number}" if round_number < 0 else str(round_number)
                    artifact = out / "artifacts" / name / case["name"] / label / folder if args.artifacts == "all" else None
                    sample = run_sample(Path(labels[label][case["build"]]), case, meter, artifact,
                                        (oracle or {}).get(case["name"]), shared)
                    sample.update(round=round_number, order_in_round=position)
                    if args.artifacts == "failures" and not sample["valid"]:
                        _run_module().write_json(_failure_path(out, name, case, label, folder), sample)
                    if not sample["valid"]:
                        report["passed"] = False
                        print(f"  {label} round {round_number}: {'; '.join(sample['issues'])}", flush=True)
                    if round_number < 0:
                        if not sample["valid"]:
                            entry["warmup_issues"].append({"label": label, "round": round_number, "issues": sample["issues"]})
                    else:
                        entry["runs"][label].append(sample)
        finally:
            if shared is not None:
                shared.close()
        entry["summaries"] = {label: summarize(runs) for label, runs in entry["runs"].items()}
        measured = [s for runs in entry["runs"].values() for s in runs]
        entry["stable_scoped_contract"] = len({s["contract_sha256"] for s in measured}) == 1
        # Evidence that the scoped rules were exercised (cf. phase5.py order_variation_cases).
        entry["raw_request_order_variants"] = len({s.get("raw_request_order_sha256") for s in measured if s["valid"]})
        entry["raw_stdout_variants"] = len({s.get("raw_stdout_sha256") for s in measured if s["valid"]})
        if entry["raw_request_order_variants"] > 1 or entry["raw_stdout_variants"] > 1:
            report["order_variation_workloads"].append(case["name"])
        reference = entry["runs"][eligible[0]] if eligible[0] == report["reference"] else None
        entry["comparisons"] = {}
        if reference is not None:
            for label in eligible[1:]:
                entry["comparisons"][label] = {metric: compare(reference, entry["runs"][label], metric, args.seed)
                                               for metric in COMPARED_METRICS}
        if not entry["stable_scoped_contract"] or entry["warmup_issues"]:
            report["passed"] = False
        for label, summary in entry["summaries"].items():
            wall = summary["metrics"].get("wall_ms", {}).get("median")
            print(f"  {label}: {summary['valid_samples']}/{summary['samples']} valid, median "
                  + (f"{wall:.3f} ms" if wall is not None else "N/A"), flush=True)
    return report


def _failure_path(out, name, case, label, folder):
    path = out / "failures" / name / case["name"] / label / f"{folder}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def freeze_oracle(report):
    """Scoped contract per workload; only from a complete, passing identical-binary run."""
    if not report["passed"]:
        raise ValueError("Go self-comparison failed; scoped rules are not validated")
    oracle = {}
    for name, entry in report["workloads"].items():
        contracts = [s["scoped_digests"] for runs in entry["runs"].values() for s in runs]
        if contracts:
            oracle[name] = contracts[0]
    return oracle


def scope_guard(prior, cases, args):
    """Prior Rust predates these commands: an explicit stub, no request, no state or config change."""
    results = {}
    meter = Path(args.meter).resolve()
    for case in cases:
        if case["build"] != "standard":
            continue
        sample = run_sample(Path(prior), case, meter)
        digests = sample.get("scoped_digests") or {}
        unchanged = digest(canonical(baseline_state(case["fixture"]["state"])))
        issues = []
        if sample.get("exit_code") in (None, 0):
            issues.append("prior Rust did not reject a deferred administration command")
        if sample.get("requests_total") != 0:
            issues.append(f"prior Rust sent {sample.get('requests_total')} request(s)")
        if digests.get("state_after") != unchanged:
            issues.append("prior Rust changed administration state, or the guard sample failed")
        if digests.get("config_before") != digests.get("config_after") or not digests:
            issues.append("prior Rust changed configuration, or the guard sample failed")
        if STUB_ISSUE not in sample.get("issues", []):
            issues.append("prior Rust did not print the explicit deferred-operation stub")
        results[case["name"]] = {"exit_code": sample.get("exit_code"), "requests": sample.get("requests_total"),
                                 "issues": issues, "passed": not issues}
    return results


def render(report, out):
    run = _run_module()
    with (out / "samples.csv").open("w", newline="") as handle:
        fields = ["stage", "workload", "binary", "round", "order_in_round", "valid", "exit_code",
                  *run.METRICS, *EXTRA_METRICS]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for stage_name, stage_report in report["stages"].items():
            for name, entry in stage_report["workloads"].items():
                for label, runs in entry.get("runs", {}).items():
                    for sample in runs:
                        writer.writerow({"stage": stage_name, "workload": name, "binary": label, **sample})
    method = report["method"]
    lines = ["# Phase-five administration workloads", "",
             "Warm-cache, fresh-process sequential samples; seeded randomized binary order per round.",
             f"Fixture state/config reset before every warmup and sample ({method['fixture_reset']}); "
             "setup, reset, ledger capture and artifacts are outside timing.",
             "Fan-out siblings are compared as a multiset and map-ordered role sequences with multiplicity; everything else is exact.",
             "p95 from 30 samples is descriptive; bootstrap intervals are pointwise and exploratory.", ""]
    if report.get("partial"):
        lines += ["**Partial run (--workload subset): not a complete phase-five report.**", ""]
    for stage_name, stage_report in report["stages"].items():
        lines += [f"## {stage_name} ({'passed' if stage_report['passed'] else 'FAILED'})", "",
                  f"Purpose: {stage_report.get('purpose', 'measurement')}; {stage_report.get('warmup')} warmup and "
                  f"{stage_report.get('repetitions')} measured rounds per binary.",
                  f"Workloads with raw order/output variation between samples: "
                  f"{len(stage_report.get('order_variation_workloads', []))}; skipped (failures): "
                  f"{', '.join(stage_report.get('skipped_workloads', [])) or 'none'}.", "",
                  "| Workload | Binary | Valid | Wall median / p95 ms | CV | CPU ms | Peak RSS MiB | First request ms | Request→stdout ms | Max in-flight | Requests | 401s (max) | Logical bytes |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        ratios = []
        for name, entry in stage_report["workloads"].items():
            if entry.get("skipped"):
                lines.append(f"| {name} | — | skipped (failure): {entry['skipped']} |  |  |  |  |  |  |  |  |  |  |")
                continue
            for label, summary in entry["summaries"].items():
                m = summary["metrics"]

                def v(key, stat="median", scale=1, digits=3):
                    return f"{m[key][stat] / scale:.{digits}f}" if key in m and m[key][stat] is not None else "N/A"
                lines.append(f"| {name} | {label} | {summary['valid_samples']}/{summary['samples']} | {v('wall_ms')} / {v('wall_ms', 'p95')} | "
                             f"{v('wall_ms', 'cv')} | {v('cpu_ms')} | {v('peak_rss_bytes', scale=1 << 20)} | {v('first_request_ms')} | "
                             f"{v('request_to_stdout_ms')} | {v('max_in_flight', 'max', digits=0)} | {v('requests_total', digits=0)} | "
                             f"{v('rejected_401_requests', 'max', digits=0)} | {v('logical_bytes_total', digits=0)} |")
            for label, metrics in entry["comparisons"].items():
                parts = []
                for metric in ("wall_ms", "cpu_ms", "peak_rss_bytes"):
                    c = metrics[metric]
                    if not c["compatible"]:
                        parts.append(f"{metric} incompatible ({', '.join(c['differences'])})")
                    elif "ratio" in c:
                        parts.append(f"{metric} {c['ratio']:.3f}× [{c['ratio_ci95'][0]:.3f}, {c['ratio_ci95'][1]:.3f}], Δ {c['median_delta']:+.3f}")
                    else:
                        parts.append(f"{metric} ratio {c['ratio_status']}")
                ratios.append(f"- {name}: {label}/{stage_report['reference']} — " + "; ".join(parts))
        if ratios:
            heading = ("### A/A ratios (correctness gate; too few samples for inference)"
                       if stage_report.get("purpose") != "measurement" else "### Ratios (median, bootstrap 95% interval)")
            lines += ["", heading, "", *ratios]
        lines.append("")
    if report.get("scope_guard"):
        failed = [n for n, r in report["scope_guard"].items() if not r["passed"]]
        lines += ["## Prior-Rust scope guard", "",
                  f"{len(report['scope_guard']) - len(failed)}/{len(report['scope_guard'])} workloads rejected with the "
                  "deferred stub, no request and no state/config change." + (f" Failed: {', '.join(failed)}." if failed else ""), ""]
    lines += ["## Unavailable or unmeasured quantities", "",
              *[f"- {k}: N/A — {v}" for k, v in NOT_MEASURED.items()],
              "- Per-workload N/A metrics are listed under `summaries.*.unavailable` in report.json.",
              "- Block input/output counters are wait4 operations, not storage, socket or network bytes.", "",
              "## Limitations", "", *[f"- {item}" for item in report["limitations"]]]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    run.write_json(out / "report.json", report)


LIMITATIONS = [
    "The fixture is a Python ThreadingHTTPServer in a separate process; its scheduling and per-request cost are identical for every binary but add latency and noise relative to a production server.",
    "Control-mode reset reuses one fixture process per workload. The previous CLI has exited and every handler has finished before the model, ledger and fault budgets are rebuilt from the scenario. The fixture process is warm rather than fresh. `--fixture-reset process` restores per-sample fixture processes.",
    "Concurrent detail arrival order is scheduler dependent; only membership, multiplicity and the list-before-detail barrier (same-clock arrival after the list body write) are compared within a fan-out.",
    "Refresh contention: the number of rejected siblings is scheduler dependent; it is recorded (rejected_401_requests) and excluded from the oracle. Every causal constraint is enforced: one grant after the first 401; replay exactly once after each rejection; byte-identical replays; no rotated session before the grant; exact final membership.",
    "Request timestamps are taken after the fixture parses request headers; first_request_ms therefore includes client connect plus header transmission.",
    "Role removal targets an unassigned role; deletion of assigned roles is outside these workloads (phase5.py declares that server behavior separately).",
    "Expected list output and messages come from phase5.py's Go renderer model and canonicalizers; endpoint shapes follow phase5.py's Model. The fixture itself is a separate threaded server (phase5.py's AdminServer is a raw-socket scripted server).",
    "Measured max_in_flight is an observation of server arrivals, not a claim about the client task count. The default Rust inventory fan-out retains Go behavior after a 64-request cap failed the wide-barrier gate.",
    "The Go A/A stage is a correctness gate with its own small repetition count; its ratios are not a timing baseline.",
    "Loopback measurements establish no live-server performance claim; no real server is contacted or mutated.",
]


def main_run(args):
    run = _run_module()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    go = run.binary_info(args.go)
    go_cloud = run.binary_info(args.go_cloud) if args.go_cloud else None
    candidates, candidate_clouds = {}, {}
    for spec, target in [(s, candidates) for s in args.candidate] + [(s, candidate_clouds) for s in args.candidate_cloud]:
        label, sep, path = spec.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z0-9_-]+", label) or label in target or label.startswith("go"):
            raise ValueError("--candidate/--candidate-cloud need a unique LABEL=PATH (labels starting with go are reserved)")
        target[label] = run.binary_info(path)
    if set(candidate_clouds) - set(candidates):
        raise ValueError("--candidate-cloud labels must match --candidate labels")
    manifest = None
    if args.manifest:
        manifest = json.loads(Path(args.manifest).read_text())
        checks = [("go", go), ("go_cloud", go_cloud)]
        for key, info in checks:
            if info is not None and manifest.get(key, {}).get("sha256") != info["sha256"]:
                raise ValueError(f"{key} binary hash differs from the baseline manifest")
    prior = run.binary_info(args.prior) if args.prior else None
    if prior and manifest and manifest.get("prior_rust", {}).get("sha256") != prior["sha256"]:
        raise ValueError("prior Rust hash differs from the baseline manifest")
    cases = workloads(args.seed)
    if args.workload:
        unknown = set(args.workload) - {c["name"] for c in cases}
        if unknown:
            raise ValueError(f"unknown workloads: {sorted(unknown)}")
        cases = [c for c in cases if c["name"] in args.workload]
    report = {"schema_version": SCHEMA_VERSION, "complete": False, "passed": False, "partial": bool(args.workload),
              "created_unix_ns": time.time_ns(), "host": run.host_metadata(), "manifest": manifest,
              "binaries": {"go": go, "go_cloud": go_cloud, "candidates": candidates,
                           "candidate_clouds": candidate_clouds, "prior_rust": prior},
              "meter": run.binary_info(args.meter),
              "method": {"warmup": args.warmup, "repetitions": args.repetitions, "seed": args.seed,
                         "self_check": {"warmup": args.self_check_warmup, "repetitions": args.self_check_repetitions,
                                        "purpose": "Go A/A correctness gate and scoped-rule validation; not a timing baseline"},
                         "order": "seeded randomized sequential, fresh process, warm cache",
                         "sink": "captured pipe",
                         "fixture_reset": args.fixture_reset,
                         "fixture_tcp_nodelay": True,
                         "reset": ("fresh HOME/config per sample; fixture model, ledger and fault budgets rebuilt from the "
                                   "scenario after the previous CLI exited and its handlers finished (verified per sample)"
                                   if args.fixture_reset == "control" else
                                   "fresh fixture process and HOME/config before every warmup and sample"),
                         "timing": "pb-meter fork/exec to child exit; fixture setup/reset, ledger/state capture and artifacts excluded",
                         "request_clock": "fixture CLOCK_MONOTONIC after request-header parse, same clock as pb-meter",
                         "tmpdir": tempfile.gettempdir(), "artifacts": args.artifacts,
                         "runner_sha256": digest(Path(__file__).read_bytes()),
                         "run_py_sha256": digest((HERE / "run.py").read_bytes()),
                         "phase5_model_sha256": digest((HERE / "phase5.py").read_bytes()),
                         "workloads_sha256": digest(canonical(cases))},
              "workloads": {c["name"]: {"family": c["family"], "size": c["size"], "output": c["output"], "argv": c["argv"],
                                        "build": c["build"], "cardinality": c["cardinality"],
                                        "spec_sha256": digest(canonical(c))} for c in cases},
              "not_measured": NOT_MEASURED, "limitations": LIMITATIONS, "stages": {}}
    rng = random.Random(args.seed)
    # Stage 1: identical-binary Go A/A validates scoped rules and freezes the oracle.
    if args.oracle_report:
        previous = json.loads(Path(args.oracle_report).read_text())
        stage1 = previous["stages"].get("go-self-comparison")
        if (previous.get("schema_version") != SCHEMA_VERSION or not previous.get("complete") or not stage1
                or not stage1["passed"]
                or previous["method"]["workloads_sha256"] != report["method"]["workloads_sha256"]
                or previous["method"]["runner_sha256"] != report["method"]["runner_sha256"]
                or previous["method"].get("phase5_model_sha256") != report["method"]["phase5_model_sha256"]
                or previous["binaries"]["go"]["sha256"] != go["sha256"]
                or (previous["binaries"].get("go_cloud") or {}).get("sha256") != (go_cloud or {}).get("sha256")):
            raise ValueError("oracle report is not a complete passing self-comparison for these workloads/runner/Go binaries")
        report["stages"]["go-self-comparison"] = {**stage1, "reused_from": str(Path(args.oracle_report).resolve())}
        oracle = freeze_oracle(stage1)
    else:
        go_paths = {"standard": go["path"], "cloud": go_cloud["path"] if go_cloud else None}
        stage1 = stage("go-self-comparison", {"go-a": go_paths, "go-b": dict(go_paths)}, cases, args, out, rng, report,
                       warmup=args.self_check_warmup, repetitions=args.self_check_repetitions,
                       purpose="Go A/A correctness gate")
        render(report, out)
        if not stage1["passed"]:
            report["complete"] = True
            render(report, out)
            print("Go self-comparison failed; candidates were not measured.", file=sys.stderr)
            return 1
        oracle = freeze_oracle(stage1)
    report["oracle"] = {name: digest(canonical(contract)) for name, contract in oracle.items()}
    if prior:
        report["scope_guard"] = scope_guard(prior["path"], cases, args)
    labels = {"go": {"standard": go["path"], "cloud": go_cloud["path"] if go_cloud else None}}
    for label, info in candidates.items():
        labels[label] = {"standard": info["path"], "cloud": candidate_clouds[label]["path"] if label in candidate_clouds else None}
    if candidates:
        stage("comparison", labels, cases, args, out, rng, report, oracle)
    report["complete"] = True
    report["candidates_measured"] = bool(candidates)  # an A/A-only run validates the oracle, nothing more
    report["passed"] = all(s["passed"] for s in report["stages"].values()) and all(
        r["passed"] for r in report.get("scope_guard", {}).values())
    render(report, out)
    print(f"Report: {out / 'report.md'}")
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list workloads")
    listing.add_argument("--seed", type=int, default=DEFAULT_SEED)
    export = commands.add_parser("export", help="write deterministic workload definitions")
    export.add_argument("--seed", type=int, default=DEFAULT_SEED)
    export.add_argument("--out", required=True)
    serving = commands.add_parser("serve", help="internal: run one fixture process")
    serving.add_argument("--scenario", required=True)
    running = commands.add_parser("run", help="Go A/A self-comparison, then measured comparison")
    running.add_argument("--go", required=True, help="frozen standard Go binary")
    running.add_argument("--go-cloud", help="frozen Go binary built with the harness orchestrator token")
    running.add_argument("--candidate", action="append", default=[], metavar="LABEL=PATH")
    running.add_argument("--candidate-cloud", action="append", default=[], metavar="LABEL=PATH")
    running.add_argument("--prior", help="prior Rust binary; unmeasured scope guard only")
    running.add_argument("--manifest", help="baseline manifest; binary hashes must match")
    running.add_argument("--oracle-report", help="reuse the passing self-comparison from an earlier report")
    running.add_argument("--meter", default=str(HERE.parent / "bin" / "pb-meter"))
    running.add_argument("--out", required=True, help="new output directory")
    running.add_argument("--workload", action="append")
    running.add_argument("--warmup", type=int, default=5)
    running.add_argument("--repetitions", type=int, default=30)
    running.add_argument("--self-check-warmup", type=int, default=0, help="Go A/A warmup rounds (recorded separately)")
    running.add_argument("--self-check-repetitions", type=int, default=2, help="Go A/A rounds per Go label (recorded separately)")
    running.add_argument("--fixture-reset", choices=["control", "process"], default="control",
                         help="control: reset one fixture per workload between samples; process: fresh fixture per sample")
    running.add_argument("--seed", type=int, default=DEFAULT_SEED)
    running.add_argument("--artifacts", choices=["all", "failures"], default="all")
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.scenario)
        return 0
    if args.command == "list":
        for case in workloads(args.seed):
            print(f"{case['name']:48} {case['family']:22} {case['build']}")
        return 0
    if args.command == "export":
        with open(args.out, "x") as stream:
            json.dump(workloads(args.seed), stream, ensure_ascii=False, indent=1)
            stream.write("\n")
        return 0
    if args.repetitions < 1 or args.warmup < 0 or args.self_check_repetitions < 1 or args.self_check_warmup < 0:
        parser.error("invalid repetitions or warmup")
    if sys.platform != "linux":
        parser.error("phase-five workloads require Linux (shared CLOCK_MONOTONIC with pb-meter)")
    try:
        return main_run(args)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"Harness error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
