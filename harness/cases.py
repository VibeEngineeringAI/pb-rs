"""Deterministic process scenarios; no real Parseable account is used."""

import json

from compatibility import PROJECTION


START = "2024-01-01T00:00:00Z"
END = "2024-01-01T01:00:00Z"
BASIC = {"authorization": "Basic aGFybmVzczpoYXJuZXNz"}


def response(method, path, body, **options):
    if not isinstance(body, str):
        body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return {"method": method, "path": path, "body": body, **options}


def suite():
    cases = []

    def add(name, argv, *, group="startup", profile=False, fixture=None,
            exit_code=0, **extra):
        expected = []
        if fixture:
            expected = [r["method"] + " " + r["path"] for r in fixture["routes"]]
            if fixture.get("flight"):
                expected.append("DoGet /arrow.flight.protocol.FlightService/DoGet")
        cases.append(dict(name=name, group=group, argv=argv, profile=profile,
                          fixture=fixture, expected_exit=exit_code,
                          expected_requests=expected, **extra))

    add("version-text", ["version"])
    add("version-json", ["version", "-o", "json"])
    add("root-help", ["--help"], compatibility_projection=PROJECTION)
    add("command-tree", ["-o", "json"], compatibility_projection=PROJECTION)
    add("agent-catalog", ["agent", "-o", "json"], compatibility_projection=PROJECTION)
    add("sql-help-json", ["help", "sql", "run", "-o", "json"])
    add("unknown-command-json", ["not-a-command", "-o", "json"], exit_code=1)
    add("unknown-flag-json", ["version", "--not-a-flag", "-ojson"], exit_code=1)
    add("missing-profile", ["sql", "run", "SELECT 1", "-o", "json"], exit_code=1)
    add("status-unconfigured", ["status", "-o", "json"], exit_code=1)
    add("profile-list", ["profile", "list", "-o", "json"], group="config", profile=True)
    add("profile-add", ["profile", "add", "new", "http://127.0.0.1:1", "--api-key", "fixture-key", "-o", "json"], group="config", profile=True)
    add("profile-update", ["profile", "update", "fixture", "http://127.0.0.1:2"], group="config", profile=True)
    add("logout", ["logout", "--yes", "-o", "json"], group="config", profile=True)

    about = response("GET", "/api/v1/about", '{"version":"fixture-1","grpcPort":{{grpc_port}}}')
    for name, headers, mode in [("status-basic", BASIC, "basic"),
                                 ("status-api-key", {"x-api-key": "fixture-key"}, "api-key"),
                                 ("status-cloud-key", {"x-api-key": "fixture-key", "x-p-tenant": "fixture-tenant"}, "cloud-key"),
                                 ("status-cloud-cookie", {"cookie": "session=fixture-session", "x-p-tenant": "fixture-tenant"}, "cloud-cookie")]:
        add(name, ["status", "-o", "json"], group="http", profile=True,
            auth=mode, fixture={"headers": headers, "routes": [about]})
    add("status-unauthorized", ["status", "-o", "json"], group="errors", profile=True,
        fixture={"headers": BASIC, "routes": [response("GET", "/api/v1/about", "denied", status=401)]}, exit_code=1)
    add("dataset-list", ["dataset", "list", "-o", "json"], group="http", profile=True,
        fixture={"headers": BASIC, "routes": [response("GET", "/api/prism/v1/home", {"datasets": [
            {"title": "logs", "datasetType": "logs", "datasetFormat": "json", "ingestion": True},
            {"title": "metrics", "datasetType": "metrics", "datasetFormat": "otel", "ingestion": False}]})]})
    add("dataset-schema", ["dataset", "schema", "logs", "-o", "json"], group="http", profile=True,
        fixture={"headers": BASIC, "routes": [response("GET", "/api/v1/logstream/logs/schema", {"fields": [
            {"name": "message", "data_type": "Utf8", "nullable": True},
            {"name": "sequence", "data_type": "Int64", "nullable": False}]})]})
    add("dataset-delete", ["dataset", "remove", "logs"], group="mutation", profile=True,
        fixture={"headers": BASIC, "routes": [response("DELETE", "/api/v1/logstream/logs", "")]})
    add("saved-query-list", ["sql", "list", "-o", "json"], group="http", profile=True,
        fixture={"headers": BASIC, "routes": [response("GET", "/api/v1/filters", [])]})

    for rows in (1, 500, 10000):
        data = [{"sequence": i, "large_integer": 9007199254740993 + i,
                 "message": "fixture <event> café 日志", "optional": None,
                 "value": i / 4, "p_timestamp": START} for i in range(rows)]
        for output in ("text", "json"):
            add(f"sql-{rows}-{output}", ["sql", "run", f"SELECT * FROM logs LIMIT {rows}", "--from", START, "--to", END, "-o", output],
                group="sql", profile=True, rows=rows,
                fixture={"headers": BASIC, "routes": [response("POST", "/api/v1/query", data)]})
    base = ["sql", "run", "SELECT * FROM logs", "--from", START, "--to", END, "-o", "json"]
    for name, body, code, exit_code in [("empty", [], 200, 0), ("empty-body", "", 200, 0),
                                         ("malformed", '[{"x":1},', 200, 1),
                                         ("trailing", '[{"x":1}] trailing', 200, 0),
                                         ("rate-limit", "try later", 429, 1)]:
        add("sql-" + name, base, group="errors", profile=True, exit_code=exit_code,
            fixture={"headers": BASIC, "routes": [response("POST", "/api/v1/query", body, status=code)]})
    add("sql-delayed", base, group="latency", profile=True,
        fixture={"headers": BASIC, "routes": [response("POST", "/api/v1/query", [{"x": 1}], delay_ms=50)]})
    add("sql-chunked", base, group="latency", profile=True, rows=128,
        fixture={"headers": BASIC, "routes": [response("POST", "/api/v1/query", [{"sequence": i, "message": "x" * 256} for i in range(128)], chunk_bytes=1024, chunk_delay_ms=2)]})

    for instant in (False, True):
        argv = ["promql", "run", "up", "--dataset", "metrics", "--from", START, "--to", END, "--step", "60s", "-o", "json"]
        if instant:
            argv.append("--instant")
        sample = {"metric": {"__name__": "up", "job": "fixture"}}
        sample["value" if instant else "values"] = [1704067200, "1"] if instant else [[1704067200, "1"], [1704067260, "2"]]
        add("promql-instant" if instant else "promql-range", argv, group="promql", profile=True,
            fixture={"headers": BASIC, "routes": [response("GET", "/prometheus/api/v1/query" if instant else "/prometheus/api/v1/query_range", {"status": "success", "data": {"resultType": "vector" if instant else "matrix", "result": [sample]}})]})
    add("promql-labels", ["promql", "labels", "--dataset", "metrics", "-o", "json"], group="promql", profile=True,
        fixture={"headers": BASIC, "routes": [response("GET", "/prometheus/api/v1/labels", {"status": "success", "data": ["__name__", "job"]})]})
    for rows, batch, delay in [(32, 32, 0), (4096, 128, 0), (128, 32, 10)]:
        add(f"tail-{rows}", ["tail", "logs", "-o", "json"], group="flight", profile=True,
            fixture={"headers": BASIC, "routes": [about], "flight": {"rows": rows, "batch_rows": batch, "delay_ms": delay}},
            exit_code=1, rows=rows, jsonl_rows=rows)
    add("tail-sigint", ["tail", "logs", "-o", "json"], group="signals", profile=True,
        fixture={"headers": BASIC, "routes": [about], "flight": {"rows": 32, "batch_rows": 32, "hold": True}},
        exit_code=-2, signal_after_lines=32, jsonl_rows=32)
    add("tail-broken-pipe", ["tail", "logs", "-o", "json"], group="signals", profile=True,
        fixture={"headers": BASIC, "routes": [about], "flight": {"rows": 128, "batch_rows": 32, "delay_ms": 20}},
        exit_code=-13, close_stdout_after_lines=32, jsonl_rows=32)
    return cases
