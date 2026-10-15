#!/usr/bin/env python3
"""Phase-two Go/Rust differential gate: configuration, profiles, auth and HTTP reads.

Every case runs both executables as real processes in fresh HOME/XDG trees
against loopback fixtures. Reference results must satisfy each case's pinned
expectations before candidate parity is meaningful. See contracts/phase2.md.
"""

import argparse
import base64
import collections
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import phase1  # noqa: E402
import run  # noqa: E402

SCHEMA_VERSION = 1
VERSION_STDOUT = b"pb version 0.0.0-harness\n"
MAX_OUTPUT = 4 << 20
NO_SERVER = "http://127.0.0.1:1"
ORCHESTRATOR_TOKEN = "harness-orchestrator-token"
STUB_MARKER = b"is not implemented in this Rust phase"
CONTRACT_HEADERS = ("authorization", "x-api-key", "x-p-tenant", "cookie", "content-type", "accept")
# Scope correction (docs/rust-phase2.md): phase two is noninteractive profiles plus `logout --yes`.
# Interactive logout confirmation and terminal login belong to the future terminal phase. Their Go
# cases stay defined and runnable (--include-future-terminal) but are not phase-two gate cases.
PHASE2_SCOPE = "phase2"
FUTURE_TERMINAL = "future-terminal"
FUTURE_TERMINAL_REASON = ("interactive terminal workflow deferred by the phase-two scope "
                          "(noninteractive profiles and logout --yes only)")

AUTH = {
    "basic": {"username": "harness", "password": "harness", "cloud": False},
    "api-key": {"apiKey": "fixture-key", "cloud": False},
    "cloud-key": {"apiKey": "fixture-key", "cloud": True, "tenant_id": "fixture-tenant"},
    "cloud-cookie": {"session_token": "fixture-session", "cloud": True, "tenant_id": "fixture-tenant"},
}
HEADERS = {
    "basic": {"authorization": "Basic aGFybmVzczpoYXJuZXNz"},
    "api-key": {"x-api-key": "fixture-key"},
    "cloud-key": {"x-api-key": "fixture-key", "x-p-tenant": "fixture-tenant"},
    "cloud-cookie": {"cookie": "session=fixture-session", "x-p-tenant": "fixture-tenant"},
}
WRONG = {"authorization": "Basic d3Jvbmc6d3Jvbmc="}


def basic(user, password):
    return {"authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    # JSON string escapes are valid TOML basic-string escapes.
    return json.dumps(value, ensure_ascii=False)


def toml_key(name):
    return name if name and all(c.isascii() and (c.isalnum() or c in "-_") for c in name) else toml_value(name)


def config_toml(profiles, default="fixture", top="DefaultProfile", table="Profiles"):
    lines = [] if default is None else [f"{top} = {toml_value(default)}"]
    for name, fields in profiles.items():
        lines.append(f"[{table}.{toml_key(name)}]")
        lines.extend(f"{key} = {toml_value(value)}" for key, value in fields.items())
    return "\n".join(lines) + "\n"


def profile(mode="basic", url="{{url}}", **extra):
    return {"url": url, **AUTH[mode], **extra}


def response(method, path, body="", status=None, **options):
    if not isinstance(body, str):
        body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    route = {"method": method, "path": path, "body": body, **options}
    if status is not None:
        route["status"] = status
    return route


def expect(exit_code=0, requests=(), out=(), err=(), out_json=None, err_json=None, config=None,
           out_exact=None, err_exact=None):
    result = {"exit": exit_code, "requests": list(requests)}
    for key, value in (("stdout_has", list(out)), ("stderr_has", list(err))):
        if value:
            result[key] = value
    for key, value in (("stdout_json", out_json), ("stderr_json", err_json), ("config", config),
                       ("stdout_exact", out_exact), ("stderr_exact", err_exact)):
        if value is not None:
            result[key] = value
    return result


def error_json(code, message, status=None, retryable=False):
    detail = {"code": code, "message": message}
    if status is not None:
        detail["http_status"] = status
    detail["retryable"] = retryable
    return {"error": detail}


def corpus():
    """Fixed, deterministic phase-two cases. Every secret and endpoint is synthetic/loopback."""
    cases = []

    def add(name, argv, expectation, *, group, config=None, fixture=None, stateful=None, env=None,
            unset=(), stdin="", build="standard", benchmark=False, requires=(), compare=None,
            location="xdg", steps=None, scope=PHASE2_SCOPE):
        if steps is None:
            steps = [{"argv": argv, "env": env or {}, "unset": list(unset), "stdin": stdin, "expect": expectation}]
        case = {"name": "p2-" + name, "group": "phase2-" + group, "steps": steps, "config": config,
                "location": location, "build": build, "benchmark": benchmark, "requires": list(requires),
                "scope": scope}
        if scope != PHASE2_SCOPE:
            case["scope_reason"] = FUTURE_TERMINAL_REASON
        if fixture is not None:
            case["fixture"] = {"kind": "go", "scenario": fixture}
        if stateful is not None:
            case["fixture"] = {"kind": "stateful", "scenario": stateful}
        if compare:
            case["compare"] = compare
        cases.append(case)
        return case

    def step(argv, expectation, env=None, unset=(), stdin=""):
        return {"argv": argv, "env": env or {}, "unset": list(unset), "stdin": stdin, "expect": expectation}

    def raw(text, mode=0o600):
        return {"toml": text, "mode": mode}

    def single(mode="basic", name="fixture", **extra):
        return raw(config_toml({name: profile(mode, **extra)}, default=name))

    about_body = {"version": "fixture-1 <&> é", "commit": "harness", "deploymentId": "x", "grpcPort": 1}
    about = response("GET", "/api/v1/about", about_body)
    ok = {"status": "ok", "healthy": True, "profile": "fixture", "url": "http://<FIXTURE>", "version": "fixture-1 <&> é"}

    # ----- status: four auth modes, text and JSON -----------------------------
    for mode in AUTH:
        add(f"status-{mode}-json", ["status", "-o", "json"],
            expect(0, ["GET /api/v1/about"], out_json=ok, config="unchanged"),
            group="status", config={"auth": mode}, fixture={"headers": HEADERS[mode], "routes": [about]},
            benchmark=True)
        add(f"status-{mode}-text", ["status"],
            expect(0, ["GET /api/v1/about"], out_exact="Profile : fixture\nURL     : http://<FIXTURE>\n"
                   "Status  : ✓ Connected\nVersion : fixture-1 <&> é\n", err_exact=""),
            group="status", config={"auth": mode}, fixture={"headers": HEADERS[mode], "routes": [about]},
            benchmark=mode == "basic")
    auth_failed = "Authentication failed: invalid username/password or API key"
    for output in ("json", "text"):
        flag = ["-o", "json"] if output == "json" else []
        failure = {"status": "error", "healthy": False, "profile": "fixture", "url": "http://<FIXTURE>", "error": auth_failed}
        add(f"status-unauthorized-{output}", ["status", *flag],
            expect(1, ["GET /api/v1/about"], **({"out_json": failure, "err_exact": ""} if output == "json" else
                                                 {"err": ["Status  : ✗ Not connected", "Error   : " + auth_failed,
                                                          "Error: status check failed: " + auth_failed], "out_exact": ""})),
            group="status", config={"auth": "basic"}, fixture={"headers": WRONG, "routes": [about]})
        add(f"status-server-error-{output}", ["status", *flag],
            expect(1, ["GET /api/v1/about"], out=["Status Code: 500 Internal Server Error"] if output == "json" else [],
                   err=[] if output == "json" else ["Response: database <unavailable>"]),
            group="status", config={"auth": "basic"},
            fixture={"headers": HEADERS["basic"], "routes": [response("GET", "/api/v1/about", "database <unavailable>", 500)]})
        add(f"status-connection-refused-{output}", ["status", *flag],
            expect(1, [], **({"out": ["dial tcp 127.0.0.1:1: connect: connection refused"]} if output == "json" else
                              {"err": ["dial tcp 127.0.0.1:1: connect: connection refused"]})),
            group="status", config=single("basic", url=NO_SERVER))
        add(f"status-no-active-profile-{output}", ["status", *flag],
            expect(1, [], **({"out_json": {"status": "error", "healthy": False, "error": "no active profile. run: pb login"}}
                              if output == "json" else {"err": ["Error: no active profile. run: pb login\nUsage:\n  pb status [flags]\n"],
                                                        "out_exact": ""})),
            group="status", config=raw(config_toml({"fixture": profile()}, default="missing")))
        add(f"status-invalid-auth-{output}", ["status", *flag],
            expect(1, [], **({"out": ['"error": "cloud profile missing tenant_id"']} if output == "json" else
                              {"err": ["Error   : cloud profile missing tenant_id"]})),
            group="status", config=raw(config_toml({"fixture": {"url": "{{url}}", "cloud": True, "apiKey": "fixture-key"}})),
            fixture={"headers": HEADERS["api-key"], "routes": [about]})
    add("status-forbidden-json", ["status", "-o", "json"],
        expect(1, ["GET /api/v1/about"], out=[auth_failed]),
        group="status", config={"auth": "api-key"},
        fixture={"headers": HEADERS["api-key"], "routes": [response("GET", "/api/v1/about", "forbidden", 403)]})
    for label, body in (("malformed", "not json"), ("empty", ""), ("wrong-type", '{"version":7}')):
        add(f"status-about-{label}-json", ["status", "-o", "json"],
            expect(1, ["GET /api/v1/about"], out=['"healthy": false']),
            group="status", config={"auth": "basic"},
            fixture={"headers": HEADERS["basic"], "routes": [response("GET", "/api/v1/about", body)]})
    for label, fields, message in (
            ("both-basic-and-key", {"username": "u", "password": "p", "apiKey": "k"}, "self-hosted profile has both basic credentials and api key"),
            ("cloud-both", {"cloud": True, "tenant_id": "t", "apiKey": "k", "session_token": "s"}, "cloud profile has both apiKey and session_token"),
            ("cloud-basic", {"cloud": True, "tenant_id": "t", "username": "u"}, "cloud profile cannot use self-hosted credentials"),
            ("cloud-missing", {"cloud": True, "tenant_id": "t"}, "cloud profile missing apiKey or session_token"),
            ("self-hosted-tenant", {"apiKey": "k", "tenant_id": "t"}, "self-hosted profile cannot use cloud credentials"),
            ("basic-no-password", {"username": "u"}, "self-hosted basic profile missing username or password"),
            ("no-credentials", {}, "self-hosted profile missing credentials")):
        add(f"status-auth-mode-{label}", ["status", "-o", "json"],
            expect(1, [], out_json={"status": "error", "healthy": False, "profile": "fixture", "url": NO_SERVER, "error": message}),
            group="status", config=raw(config_toml({"fixture": {"url": NO_SERVER, **fields}})))
    add("status-path-prefix-json", ["status", "-o", "json"],
        expect(0, ["GET /base/api/v1/about"], out=['"healthy": true', '"url": "http://<FIXTURE>/base/"']),
        group="status", config=single("basic", url="{{url}}/base/"),
        fixture={"headers": HEADERS["basic"], "routes": [response("GET", "/base/api/v1/about", about_body)]})
    add("status-malformed-config-json", ["status", "-o", "json"],
        expect(1, [], out_json={"status": "error", "healthy": False, "error": "no profile configured. run: pb login"}),
        group="config-read", config=raw('DefaultProfile = "fixture"\n[Profiles.fixture\nurl = "x"\n'))
    add("status-empty-config-text", ["status"],
        expect(1, [], err=["Error: no active profile. run: pb login\nUsage:"]),
        group="config-read", config=raw(""))
    add("status-config-directory-json", ["status", "-o", "json"],
        expect(1, [], out_json={"status": "error", "healthy": False, "error": "no profile configured. run: pb login"}),
        group="config-read", config={"directory": True})
    add("status-debug-headers-redacted", ["status", "-o", "json"],
        expect(0, ["GET /api/v1/about"], out_json=ok, err=["pb debug request: GET http://<FIXTURE>/api/v1/about\n",
                                                             "pb debug header: Authorization: <redacted>\n",
                                                             "pb debug header: Content-Type: application/json\n"]),
        group="status", config={"auth": "basic"}, env={"PB_DEBUG_HTTP_HEADERS": "1"},
        fixture={"headers": HEADERS["basic"], "routes": [about]},
        compare={"stderr_unordered_lines": "Go prints http.Header by ranging over a map; line order is randomized per run"})
    add("status-debug-headers-cloud-cookie", ["status", "-o", "json"],
        expect(0, ["GET /api/v1/about"], err=["pb debug header: Cookie: <redacted>\n", "pb debug header: X-P-Tenant: fixture-tenant\n"]),
        group="status", config={"auth": "cloud-cookie"}, env={"PB_DEBUG_HTTP_HEADERS": "1"},
        fixture={"headers": HEADERS["cloud-cookie"], "routes": [about]},
        compare={"stderr_unordered_lines": "Go prints http.Header by ranging over a map; line order is randomized per run"})

    # ----- configuration read compatibility ------------------------------------
    for label, fields, key in (("apikey-wins", {"apiKey": "fixture-key", "api_key": "legacy", "token": "older"}, "fixture-key"),
                               ("api-key-legacy", {"api_key": "fixture-key", "token": "older"}, "fixture-key"),
                               ("token-legacy", {"token": "fixture-key"}, "fixture-key"),
                               ("empty-apikey-falls-back", {"apiKey": "", "token": "fixture-key"}, "fixture-key")):
        add(f"legacy-{label}", ["status", "-o", "json"],
            expect(0, ["GET /api/v1/about"], out=['"healthy": true']),
            group="config-read", config=raw(config_toml({"fixture": {"url": "{{url}}", **fields}})),
            fixture={"headers": {"x-api-key": key}, "routes": [about]})
    add("legacy-token-dataset-list", ["dataset", "list", "-o", "json"],
        expect(0, ["GET /api/prism/v1/home"]),
        group="config-read", config=raw(config_toml({"fixture": {"url": "{{url}}", "token": "fixture-key"}})),
        fixture={"headers": {"x-api-key": "fixture-key"}, "routes": [response("GET", "/api/prism/v1/home", {"datasets": []})]})
    add("case-insensitive-keys", ["status", "-o", "json"],
        expect(0, ["GET /api/v1/about"], out=['"healthy": true']),
        group="config-read", config=raw(config_toml({"fixture": {"URL": "{{url}}", "Username": "harness", "PASSWORD": "harness"}},
                                                    top="defaultprofile", table="profiles")),
        fixture={"headers": HEADERS["basic"], "routes": [about]})
    go_written = ("DefaultProfile = 'fixture'\n\n[Profiles]\n[Profiles.'a.b c']\nurl = 'http://other.invalid'\ncloud = false\n"
                  "apiKey = 'other-key'\n\n[Profiles.fixture]\nurl = '{{url}}'\nusername = 'harness'\npassword = 'harness'\ncloud = false\n")
    add("go-written-format", ["status", "-o", "json"],
        expect(0, ["GET /api/v1/about"], out=['"healthy": true']),
        group="config-read", config=raw(go_written), fixture={"headers": HEADERS["basic"], "routes": [about]})
    add("unknown-fields-ignored", ["profile", "list", "-o", "json"],
        expect(0, [], out_json={"fixture": {"url": NO_SERVER, "username": "harness", "cloud": False}}, config="unchanged"),
        group="config-read", config=raw('Extra = 1\nDefaultProfile = "fixture"\n[Profiles.fixture]\nurl = "http://127.0.0.1:1"\n'
                                        'username = "harness"\npassword = "harness"\nnickname = "x"\n[Profiles.fixture.nested]\nx = 1\n'))
    add("xdg-unset-home-fallback", ["status", "-o", "json"],
        expect(0, ["GET /api/v1/about"], out=['"healthy": true']),
        group="config-read", config={"auth": "basic"}, location="home", unset=["XDG_CONFIG_HOME"],
        fixture={"headers": HEADERS["basic"], "routes": [about]})
    add("xdg-empty-home-fallback", ["status", "-o", "json"],
        expect(0, ["GET /api/v1/about"], out=['"healthy": true']),
        group="config-read", config={"auth": "basic"}, location="home", env={"XDG_CONFIG_HOME": ""},
        fixture={"headers": HEADERS["basic"], "routes": [about]})
    add("unicode-secrets-basic-auth", ["status", "-o", "json"],
        expect(0, ["GET /api/v1/about"], out=['"healthy": true']),
        group="config-read", config=raw(config_toml({"fixture": {"url": "{{url}}", "username": "usér", "password": 'p"\\ä:ß'}})),
        fixture={"headers": basic("usér", 'p"\\ä:ß'), "routes": [about]})

    # ----- profile list -------------------------------------------------------
    secrets = config_toml({
        "cloud": {"url": "https://cloud.invalid", "cloud": True, "session_token": "fixture-session",
                  "refresh_token": "fixture-refresh", "tenant_id": "fixture-tenant", "workspace_id": "ws-1",
                  "workspace_name": "Workspace <One>", "orchestrator_url": "https://orchestrator.invalid"},
        "fixture": profile("basic", url=NO_SERVER),
        "key": profile("api-key", url="http://127.0.0.1:2/base/"),
        "café": profile("cloud-key", url="http://127.0.0.1:3")}, default="fixture")
    redacted = {"cloud": {"url": "https://cloud.invalid", "cloud": True, "tenant_id": "fixture-tenant",
                          "workspace_id": "ws-1", "workspace_name": "Workspace <One>",
                          "orchestrator_url": "https://orchestrator.invalid"},
                "café": {"url": "http://127.0.0.1:3", "cloud": True, "tenant_id": "fixture-tenant"},
                "fixture": {"url": NO_SERVER, "username": "harness", "cloud": False},
                "key": {"url": "http://127.0.0.1:2/base/", "cloud": False}}
    add("profile-list-redacted-json", ["profile", "list", "-o", "json"],
        expect(0, [], out_json=redacted, config="unchanged"), group="profile", config=raw(secrets))
    add("profile-list-alias-ls", ["profile", "ls", "--output=JSON"],
        expect(0, [], out_json=redacted, config="unchanged"), group="profile", config=raw(secrets))
    add("profile-list-text", ["profile", "list"],
        expect(0, [], out_exact="│ fixture                \n│ url: http://127.0.0.1:1\n│ user: harness          \n\n", config="unchanged"),
        group="profile", config=single("basic", url=NO_SERVER))
    add("profile-list-empty-json", ["profile", "list", "-o", "json"],
        expect(0, [], out_exact="{}\n"), group="profile", config=raw(""))
    add("profile-list-missing-config-json", ["profile", "list", "-o", "json"],
        expect(1, [], err_json=error_json("NOT_FOUND", "open <HOME>/config/pb/config.toml: no such file or directory")),
        group="profile")
    add("profile-list-missing-config-text", ["profile", "list"],
        expect(1, [], err=["Error: open <HOME>/config/pb/config.toml: no such file or directory\n"]), group="profile")
    add("profile-list-malformed-json", ["profile", "list", "-o", "json"],
        expect(1, [], err=['"code": "COMMAND_FAILED"', "toml: "]), group="config-read",
        config=raw('DefaultProfile = "fixture"\n[Profiles.fixture\nurl = "x"\n'))
    add("profile-list-type-mismatch-json", ["profile", "list", "-o", "json"],
        expect(1, [], err=['"code": "COMMAND_FAILED"', "toml: "]), group="config-read",
        config=raw('DefaultProfile = 7\n'))
    add("profile-list-directory-json", ["profile", "list", "-o", "json"],
        expect(1, [], err=["is a directory"]), group="config-read", config={"directory": True})
    add("profile-list-unreadable-json", ["profile", "list", "-o", "json"],
        expect(1, [], err_json=error_json("PERMISSION_DENIED", "open <HOME>/config/pb/config.toml: permission denied")),
        group="config-read", config=raw(config_toml({"fixture": profile()}), mode=0o000), requires=["nonroot"])
    add("profile-list-bad-output", ["profile", "list", "-o", "yaml"],
        expect(1, [], err=['unsupported output format "yaml" (expected text or json)']), group="profile",
        config=single())

    # ----- profile add/update/default/remove (noninteractive) ----------------
    base = config_toml({"fixture": profile("basic", url=NO_SERVER),
                        "cloud": {"url": "https://cloud.invalid", "cloud": True, "session_token": "fixture-session",
                                  "refresh_token": "fixture-refresh", "tenant_id": "fixture-tenant"}})
    add_basic = {"default": "new", "profiles": {"new": {"url": "http://127.0.0.1:9/x", "username": "user", "password": "pass", "cloud": False},
                                                "cloud": {"session_token": "fixture-session", "refresh_token": "fixture-refresh"}},
                 "mode": "0o600"}
    for output in ("text", "json"):
        flag = ["-o", "json"] if output == "json" else []
        add(f"profile-add-basic-{output}", ["profile", "add", "new", "http://127.0.0.1:9/x", "user", "pass", *flag],
            expect(0, [], **({"out_json": {"url": "http://127.0.0.1:9/x", "username": "user", "cloud": False}} if output == "json"
                             else {"out_exact": "Profile new added successfully\n"}), config=add_basic),
            group="profile", config=raw(base))
        add(f"profile-add-api-key-{output}", ["profile", "add", "new", "http://127.0.0.1:9", "--api-key", "  psk_fixture  ", *flag],
            expect(0, [], **({"out_json": {"url": "http://127.0.0.1:9", "cloud": False}} if output == "json"
                             else {"out_exact": "Profile new added successfully\n"}),
                   config={"default": "new", "profiles": {"new": {"apiKey": "psk_fixture", "username": None, "password": None}}}),
            group="profile", config=raw(base))
        add(f"profile-add-invalid-url-{output}", ["profile", "add", "new", "http://[::1", "u", "p", *flag],
            expect(1, [], err=['error parsing URL: parse \\"http://[::1\\": missing \']\' in host' if output == "json" else
                               'Error: error parsing URL: parse "http://[::1": missing \']\' in host\n'], config="unchanged"),
            group="profile", config=raw(base))
        add(f"profile-add-api-key-with-user-{output}", ["profile", "add", "new", "http://127.0.0.1:9", "user", "--api-key", "k", *flag],
            expect(1, [], err=["--api-key cannot be combined with username/password"], config="unchanged"),
            group="profile", config=raw(base))
    add("profile-add-seeded-json", ["profile", "add", "new", "http://127.0.0.1:9", "--api-key", "psk_fixture", "-o", "json"],
        expect(0, [], out_json={"url": "http://127.0.0.1:9", "cloud": False}, config={"default": "new", "only": ["fixture", "new"]}),
        group="profile", config={"auth": "basic"}, benchmark=True)
    add("profile-list-seeded-json", ["profile", "list", "-o", "json"],
        expect(0, [], out_json={"fixture": {"url": NO_SERVER, "username": "harness", "cloud": False}}, config="unchanged"),
        group="profile", config={"auth": "basic"}, benchmark=True)
    add("logout-seeded-json", ["logout", "--yes", "-o", "json"],
        expect(0, [], out=['"removed": true'], config={"default": "", "only": []}), group="logout", config={"auth": "basic"}, benchmark=True)
    add("profile-add-creates-config", ["profile", "add", "first", "http://127.0.0.1:9", "--api-key", "k"],
        expect(0, [], out_exact="Profile first added successfully\n",
               config={"default": "first", "profiles": {"first": {"url": "http://127.0.0.1:9", "apiKey": "k", "cloud": False}}, "mode": "0o600"}),
        group="profile")
    add("profile-add-creates-config-xdg-unset", ["profile", "add", "first", "http://127.0.0.1:9", "--api-key", "k"],
        expect(0, [], config={"default": "first", "path": ".config/pb/config.toml"}),
        group="profile", unset=["XDG_CONFIG_HOME"], location="home")
    add("profile-add-overwrites-malformed", ["profile", "add", "first", "http://127.0.0.1:9", "--api-key", "k"],
        expect(0, [], config={"default": "first", "profiles": {"first": {"apiKey": "k"}}, "only": ["first"]}),
        group="config-read", config=raw('DefaultProfile = "x"\n[Profiles.x\n'))
    add("profile-add-overwrites-existing", ["profile", "add", "fixture", "http://127.0.0.1:9", "--api-key", "k"],
        expect(0, [], config={"default": "fixture", "profiles": {"fixture": {"apiKey": "k", "username": None, "password": None}}}),
        group="profile", config=raw(base))
    add("profile-add-url-not-normalized", ["profile", "add", "n", "HTTP://LOCALHOST:8000/x/../y/?q=1&r=%41#frag", "--api-key", "k", "-o", "json"],
        expect(0, [], out=['"url": "http://LOCALHOST:8000/x/../y/?q=1\\u0026r=%41#frag"']),
        group="profile", config=raw(base))
    add("profile-add-relative-url", ["profile", "add", "n", "relative/path", "--api-key", "k", "-o", "json"],
        expect(0, [], out=['"url": "relative/path"']), group="profile", config=raw(base))
    add("profile-add-quoted-name", ["profile", "add", "a.b c'\"é", "http://127.0.0.1:9", "--api-key", "k", "-o", "json"],
        expect(0, [], config={"default": "a.b c'\"é", "profiles": {"a.b c'\"é": {"apiKey": "k"}}}),
        group="profile", config=raw(base))
    add("profile-add-migrates-legacy", ["profile", "add", "new", "http://127.0.0.1:9", "--api-key", "k"],
        expect(0, [], config={"profiles": {"legacy": {"apiKey": "fixture-key", "token": None, "api_key": None, "nickname": None},
                                           "older": {"apiKey": "older-key", "token": None}}}),
        group="config-read", config=raw(config_toml({"legacy": {"url": NO_SERVER, "api_key": "fixture-key", "token": "x", "nickname": "n"},
                                                     "older": {"url": NO_SERVER, "token": "older-key"}}, default="legacy")))
    add("profile-add-tightens-mode", ["profile", "add", "new", "http://127.0.0.1:9", "--api-key", "k"],
        expect(0, [], config={"mode": "0o600"}), group="profile", config=raw(base, mode=0o644))
    add("profile-add-special-secrets", ["profile", "add", "new", "http://127.0.0.1:9", "usér", "p'\"\\\n\t#ß"],
        expect(0, [], config={"profiles": {"new": {"username": "usér", "password": "p'\"\\\n\t#ß"}}}),
        group="profile", config=raw(base))
    for output in ("text", "json"):
        flag = ["-o", "json"] if output == "json" else []
        add(f"profile-default-{output}", ["profile", "default", "cloud", *flag],
            expect(0, [], out_exact='"cloud is now set as default profile"\n' if output == "json" else "cloud is now set as default profile\n",
                   config={"default": "cloud"}), group="profile", config=raw(base))
        add(f"profile-default-missing-{output}", ["profile", "default", "nope", *flag],
            expect(1, [], err=["profile nope does not exist"], config="unchanged"), group="profile", config=raw(base))
        add(f"profile-remove-other-{output}", ["profile", "remove", "cloud", *flag],
            expect(0, [], out_exact='"Deleted profile cloud"\n' if output == "json" else "Deleted profile cloud\n",
                   config={"default": "fixture", "only": ["fixture"]}), group="profile", config=raw(base))
        add(f"profile-remove-default-{output}", ["profile", "rm", "fixture", *flag],
            expect(0, [], out=["'cloud' is now set as the default profile\n", "Deleted profile fixture"],
                   config={"default": "cloud", "only": ["cloud"]}), group="profile", config=raw(base))
        add(f"profile-remove-missing-{output}", ["profile", "remove", "nope", *flag],
            expect(1, [], err=["no profile found with the name: nope"], config="unchanged"), group="profile", config=raw(base))
    add("profile-remove-last", ["profile", "remove", "fixture"],
        expect(0, [], out_exact="Deleted profile fixture\n", config={"default": "", "only": []}),
        group="profile", config=single(url=NO_SERVER))
    add("profile-remove-no-config-json", ["profile", "remove", "x", "-o", "json"],
        expect(1, [], err=['"code": "NOT_FOUND"', "no such file or directory"]), group="profile")
    add("profile-update", ["profile", "update", "fixture", "http://127.0.0.1:7/new"],
        expect(0, [], out_exact="Profile 'fixture' URL updated to http://127.0.0.1:7/new\n",
               config={"default": "fixture", "profiles": {"fixture": {"url": "http://127.0.0.1:7/new", "password": "harness"}}}),
        group="profile", config=raw(base), benchmark=False)
    add("profile-update-alias-raw-url", ["profile", "set-url", "fixture", "HTTP://Example.INVALID/a/../b"],
        expect(0, [], config={"profiles": {"fixture": {"url": "HTTP://Example.INVALID/a/../b"}}}),
        group="profile", config=raw(base))
    add("profile-update-missing", ["profile", "update", "nope", "http://127.0.0.1:7"],
        expect(1, [], err=["Error: no profile found with the name: nope\n"], config="unchanged"), group="profile", config=raw(base))
    add("profile-update-invalid-url", ["profile", "update", "fixture", "http://[::1"],
        expect(1, [], err=["invalid URL: parse"], config="unchanged"), group="profile", config=raw(base))
    add("profile-update-no-config", ["profile", "update", "fixture", "http://127.0.0.1:7"],
        expect(1, [], err=["error reading config: open <HOME>/config/pb/config.toml: no such file or directory"]), group="profile")
    add("profile-update-no-output-flag-json", ["profile", "update", "fixture", "http://127.0.0.1:7", "-o", "json"],
        expect(1, [], err_json=error_json("COMMAND_FAILED", "unknown shorthand flag: 'o' in -o"), config="unchanged"),
        group="profile", config=raw(base))

    # ----- cloud profile add (noninteractive) --------------------------------
    add("cloud-add-no-key-json", ["cloud", "profile", "add", "-o", "json"],
        expect(1, [], err_json=error_json("COMMAND_FAILED", "api key is required. pass --api-key")), group="cloud")
    add("cloud-add-bad-output", ["cloud", "profile", "add", "--api-key", "k", "-o", "yaml"],
        expect(1, [], err=['unsupported output format "yaml"']), group="cloud")
    add("cloud-add-no-orchestrator", ["cloud", "profile", "add", "--api-key", "k"],
        expect(1, [], err=["Error: cloud orchestrator URL is not configured\n"]), group="cloud")
    add("cloud-add-no-orchestrator-token", ["cloud", "profile", "add", "--api-key", "k", "--orchestrator-url", "{{url}}", "-o", "json"],
        expect(1, [], err_json=error_json("COMMAND_FAILED", "cloud orchestrator auth token is not configured")),
        group="cloud", fixture={"routes": []})
    validate = {"org_id": "o", "org_name": "Org", "workspace_id": "ws-1", "workspace_name": "My Workspace",
                "tenant_id": "fixture-tenant", "url": "https://workspace.invalid", "state": "active", "multi_tenant": True}
    orchestrator = {"authorization": "Bearer " + ORCHESTRATOR_TOKEN, "x-api-key": "psk_cloud"}
    vroute = "GET /api/v1/cli/apikey/validate"
    for output in ("text", "json"):
        add(f"cloud-add-success-{output}", ["cloud", "profile", "add", "--api-key", " psk_cloud ", "--orchestrator-url", "{{url}}/orch/",
                                            *(["-o", "json"] if output == "json" else [])],
            expect(0, ["GET /orch/api/v1/cli/apikey/validate"],
                   out=['"name":"my-workspace"'] if output == "json" else
                   ["Cloud profile my-workspace added successfully\nWorkspace: My Workspace (ws-1)\nURL: https://workspace.invalid\n"],
                   config={"default": "my-workspace", "profiles": {"my-workspace": {
                       "url": "https://workspace.invalid", "cloud": True, "apiKey": "psk_cloud", "tenant_id": "fixture-tenant",
                       "workspace_id": "ws-1", "workspace_name": "My Workspace", "orchestrator_url": "http://<FIXTURE>/orch/"}}}),
            group="cloud", build="cloud", config=raw(base),
            fixture={"headers": orchestrator, "routes": [response("GET", "/orch/api/v1/cli/apikey/validate", validate)]})
    add("cloud-add-exists-no-force", ["cloud", "profile", "add", "--api-key", "psk_cloud", "--orchestrator-url", "{{url}}", "--name", "cloud"],
        expect(1, [vroute], err=['profile "cloud" already exists. use --force to overwrite'], config="unchanged"),
        group="cloud", build="cloud", config=raw(base),
        fixture={"headers": orchestrator, "routes": [response("GET", "/api/v1/cli/apikey/validate", validate)]})
    add("cloud-add-force-overwrite", ["cloud", "profile", "add", "--api-key", "psk_cloud", "--orchestrator-url", "{{url}}", "--name", "cloud", "--force"],
        expect(0, [vroute], out=["Cloud profile cloud added successfully"],
               config={"default": "cloud", "profiles": {"cloud": {"apiKey": "psk_cloud", "session_token": None, "refresh_token": None}}}),
        group="cloud", build="cloud", config=raw(base),
        fixture={"headers": orchestrator, "routes": [response("GET", "/api/v1/cli/apikey/validate", validate)]})
    add("cloud-add-workspace-id-name", ["cloud", "profile", "add", "--api-key", "psk_cloud", "--orchestrator-url", "{{url}}"],
        expect(0, [vroute], out=["Cloud profile ws-7 added successfully\nWorkspace: WS-7\n"], config={"default": "ws-7"}),
        group="cloud", build="cloud",
        fixture={"headers": orchestrator, "routes": [response("GET", "/api/v1/cli/apikey/validate",
                                                              {**validate, "workspace_name": "", "workspace_id": "WS-7"})]})
    for label, route, message in (
            ("rejected", response("GET", "/api/v1/cli/apikey/validate", "invalid key\n", 401), "cloud api key validation failed: 401 Unauthorized: invalid key"),
            ("incomplete", response("GET", "/api/v1/cli/apikey/validate", {"url": "https://x.invalid"}), "cloud api key validation response missing url, tenant_id, or workspace_id"),
            ("malformed", response("GET", "/api/v1/cli/apikey/validate", "{"), "failed to decode cloud api key validation response: unexpected end of JSON input")):
        add(f"cloud-add-{label}-json", ["cloud", "profile", "add", "--api-key", "psk_cloud", "--orchestrator-url", "{{url}}", "-o", "json"],
            expect(1, [vroute], err_json=error_json("COMMAND_FAILED", message)),
            group="cloud", build="cloud", fixture={"headers": orchestrator, "routes": [route]})

    # ----- logout and login ---------------------------------------------------
    three = config_toml({"zeta": profile(url=NO_SERVER), "fixture": profile(url="http://127.0.0.1:2"),
                         "alpha": profile("api-key", url=NO_SERVER), "beta": profile("api-key", url=NO_SERVER)})
    add("logout-yes-json-last", ["logout", "--yes", "-o", "json"],
        expect(0, [], out_json={"status": "ok", "profile": "fixture", "url": NO_SERVER, "removed": True, "default_profile": ""},
               config={"default": "", "only": []}), group="logout", config=single(url=NO_SERVER), benchmark=False)
    add("logout-yes-json-first-sorted", ["logout", "--yes", "-o", "json"],
        expect(0, [], out_json={"status": "ok", "profile": "fixture", "url": "http://127.0.0.1:2", "removed": True, "default_profile": "alpha"},
               config={"default": "alpha", "only": ["alpha", "beta", "zeta"]}), group="logout", config=raw(three))
    add("logout-yes-text-two", ["logout", "--yes"],
        expect(0, [], out_exact="Logged out and removed profile 'fixture'\n'cloud' is now set as the default profile\n",
               config={"default": "cloud", "only": ["cloud"]}), group="logout", config=raw(base))
    add("logout-json-requires-yes", ["logout", "-o", "json"],
        expect(1, [], err_json=error_json("COMMAND_FAILED", "use --yes with -o json to avoid interactive prompts"), config="unchanged"),
        group="logout", config=raw(base))
    add("logout-no-config-text", ["logout", "--yes"],
        expect(1, [], err=["Error: no config found — nothing to logout from\n"]), group="logout")
    add("logout-no-config-json", ["logout", "--yes", "-o", "json"],
        expect(1, [], err_json=error_json("COMMAND_FAILED", "no config found — nothing to logout from")), group="logout")
    add("logout-malformed-config", ["logout", "--yes"],
        expect(1, [], err=["Error: no config found — nothing to logout from\n"], config="unchanged"),
        group="config-read", config=raw("[Profiles\n"))
    add("logout-empty-profiles-json", ["logout", "--yes", "-o", "json"],
        expect(1, [], err_json=error_json("COMMAND_FAILED", "no active profile found")), group="logout", config=raw(""))
    add("logout-missing-default-single", ["logout", "--yes", "-o", "json"],
        expect(0, [], out=['"profile": "fixture"', '"default_profile": ""'], config={"only": []}), group="logout",
        config=raw(config_toml({"fixture": profile(url=NO_SERVER)}, default="gone")))
    add("logout-missing-default-many", ["logout", "--yes", "-o", "json"],
        expect(1, [], err_json=error_json("COMMAND_FAILED", "no active profile found; set one with: pb profile default <name>"),
               config="unchanged"), group="logout", config=raw(config_toml(json.loads(json.dumps({"a": profile(url=NO_SERVER), "b": profile(url=NO_SERVER)})), default="")))
    for label, stdin, removed in (("eof", "", False), ("no", "n\n", False), ("yes", " YeS \n", True), ("y-no-newline", "y", False)):
        add(f"logout-prompt-{label}", ["logout"],
            expect(0, [], out=["Logout from profile 'fixture' (http://127.0.0.1:1)?\n", "Continue? [y/N]: ",
                               "Logged out and removed profile 'fixture'\n" if removed else "Logout canceled\n"],
                   config={"only": ["cloud"]} if removed else "unchanged"),
            group="logout", config=raw(base), stdin=stdin, scope=FUTURE_TERMINAL)
    add("logout-ignores-unknown-output", ["logout", "--yes", "-o", "yaml"],
        expect(0, [], out_exact="Logged out and removed profile 'fixture'\n'cloud' is now set as the default profile\n"),
        group="logout", config=raw(base))
    add("logout-no-network", ["logout", "--yes", "-o", "json"],
        expect(0, [], out=['"removed": true'], config={"only": []}), group="logout", config=single("cloud-cookie"),
        fixture={"routes": []})
    add("login-without-terminal", ["login"],
        expect(1, [], err=["Error: could not open a new TTY: open /dev/tty: no such device or address\n", "Usage:\n  pb login [flags]\n"],
               config="unchanged"), group="login", config=raw(base), scope=FUTURE_TERMINAL)
    add("login-without-terminal-no-config", ["login"],
        expect(1, [], err=["could not open a new TTY"]), group="login", scope=FUTURE_TERMINAL)

    # ----- dataset list -------------------------------------------------------
    home = response("GET", "/api/prism/v1/home", {"datasets": [
        {"title": "zeta", "datasetType": "traces", "datasetFormat": "otel", "ingestion": False, "extra": 1},
        {"title": "Alpha", "name": "alpha-name", "datasetType": "logs", "datasetFormat": "json", "ingestion": True},
        {"title": "café <&>", "datasetType": "", "datasetFormat": "", "ingestion": True},
        {"title": "metrics", "datasetType": "metrics", "datasetFormat": "otel"}], "other": {"x": 1}})
    listed = [{"title": "Alpha", "name": "alpha-name", "datasetType": "logs", "datasetFormat": "json", "ingestion": True},
              {"title": "café <&>", "datasetType": "", "datasetFormat": "", "ingestion": True},
              {"title": "metrics", "datasetType": "metrics", "datasetFormat": "otel", "ingestion": False},
              {"title": "zeta", "datasetType": "traces", "datasetFormat": "otel", "ingestion": False}]
    for mode in AUTH:
        add(f"dataset-list-{mode}-json", ["dataset", "list", "-o", "json"],
            expect(0, ["GET /api/prism/v1/home"], out_json=listed, config="unchanged"),
            group="dataset", config={"auth": mode}, fixture={"headers": HEADERS[mode], "routes": [home]},
            benchmark=mode == "basic")
    add("dataset-list-text", ["dataset", "list"],
        expect(0, ["GET /api/prism/v1/home"], out=["Alpha [logs]", "café <&>\n", "zeta [traces]"]),
        group="dataset", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": [home]}, benchmark=True)
    add("dataset-list-alias-ls", ["dataset", "ls", "-ojson"],
        expect(0, ["GET /api/prism/v1/home"], out_json=listed),
        group="dataset", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": [home]})
    for label, body in (("empty", {"datasets": []}), ("null", {"datasets": None}), ("object", {})):
        for output in ("json", "text"):
            add(f"dataset-list-{label}-{output}", ["dataset", "list", *(["-o", "json"] if output == "json" else [])],
                expect(0, ["GET /api/prism/v1/home"], out_exact="[]\n" if output == "json" else ""),
                group="dataset", config={"auth": "basic"},
                fixture={"headers": HEADERS["basic"], "routes": [response("GET", "/api/prism/v1/home", body)]})
    for status, code, retry in ((404, "NOT_FOUND", False), (500, "SERVER_ERROR", True), (429, "RATE_LIMITED", True), (418, "COMMAND_FAILED", False)):
        add(f"dataset-list-http-{status}-json", ["dataset", "list", "-o", "json"],
            expect(1, ["GET /api/prism/v1/home"], err_json=error_json(code, f"request failed: HTTP {status}", status, retry)),
            group="dataset-errors", config={"auth": "basic"},
            fixture={"headers": HEADERS["basic"], "routes": [response("GET", "/api/prism/v1/home", "  failure body\n", status)]})
        add(f"dataset-list-http-{status}-text", ["dataset", "list"],
            expect(1, ["GET /api/prism/v1/home"], err_exact=f"Error: HTTP {status}: failure body\n"),
            group="dataset-errors", config={"auth": "basic"},
            fixture={"headers": HEADERS["basic"], "routes": [response("GET", "/api/prism/v1/home", "  failure body\n", status)]})
    add("dataset-list-unauthorized-json", ["dataset", "list", "-o", "json"],
        expect(1, ["GET /api/prism/v1/home"], err_json=error_json("AUTH_FAILED", "request failed: HTTP 401", 401)),
        group="dataset-errors", config={"auth": "basic"}, fixture={"headers": WRONG, "routes": [home]})
    add("dataset-list-malformed-json", ["dataset", "list", "-o", "json"],
        expect(1, ["GET /api/prism/v1/home"], err=['"code": "COMMAND_FAILED"', "invalid character"]),
        group="dataset-errors", config={"auth": "basic"},
        fixture={"headers": HEADERS["basic"], "routes": [response("GET", "/api/prism/v1/home", "{nope")]})
    add("dataset-list-wrong-shape-text", ["dataset", "list"],
        expect(1, ["GET /api/prism/v1/home"], err=["Error: json: cannot unmarshal"]),
        group="dataset-errors", config={"auth": "basic"},
        fixture={"headers": HEADERS["basic"], "routes": [response("GET", "/api/prism/v1/home", {"datasets": {"title": "x"}})]})
    add("dataset-list-connection-refused-json", ["dataset", "list", "-o", "json"],
        expect(1, [], err=['"code": "CONNECTION_FAILED"', "connection refused", '"retryable": true']),
        group="dataset-errors", config=single(url=NO_SERVER))
    add("dataset-list-path-prefix", ["dataset", "list", "-o", "json"],
        expect(0, ["GET /prefix/api/prism/v1/home"], out_exact="[]\n"),
        group="dataset", config=single(url="{{url}}/prefix"),
        fixture={"headers": HEADERS["basic"], "routes": [response("GET", "/prefix/api/prism/v1/home", {"datasets": []})]})
    add("dataset-list-bad-output-after-fetch", ["dataset", "list", "-o", "yaml"],
        expect(1, ["GET /api/prism/v1/home"], err=['unsupported output format "yaml"']),
        group="dataset-errors", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": [home]})
    add("dataset-list-invalid-auth-json", ["dataset", "list", "-o", "json"],
        expect(1, [], err_json=error_json("COMMAND_FAILED", "cloud profile missing tenant_id")),
        group="dataset-errors", config=raw(config_toml({"fixture": {"url": "{{url}}", "cloud": True, "apiKey": "k"}})),
        fixture={"routes": [home]})
    add("dataset-list-no-profile-json", ["dataset", "list", "-o", "json"],
        expect(1, [], err_json=error_json("NOT_FOUND", "no profile configured. run: pb login")),
        group="dataset-errors", config=raw(config_toml({"fixture": profile()}, default="missing")))
    add("dataset-list-malformed-config-text", ["dataset", "list"],
        expect(1, [], err=["Error: error initializing default profile: toml: "]),
        group="config-read", config=raw("DefaultProfile = \n"))

    # ----- dataset info -------------------------------------------------------
    def info_routes(name="logs", info=None, stats=None, retention=None, alerts=None, prefix="/api/v1/logstream/"):
        stem = prefix + name

        def pick(value, default):
            return value if isinstance(value, tuple) else (default if value is None else value,)
        return [response("GET", stem + "/info", *pick(info, {"streamType": "UserDefined", "telemetryType": "logs"})),
                response("GET", stem + "/stats", *pick(stats, {
                    "ingestion": {"count": 1234, "format": "json", "size": 5368709120},
                    "storage": {"format": "parquet", "size": 1610612736}, "stream": name, "time": "2024-01-01T00:00:00Z"})),
                response("GET", stem + "/retention", *pick(retention, [{"description": "keep", "action": "delete", "duration": "30d"}])),
                response("GET", stem + "/alert", *pick(alerts, {"version": "v1", "alerts": [
                    {"name": "errors <hi>", "message": "m", "targets": [{"type": "webhook", "endpoint": "https://hook.invalid", "headers": {"a": "b"},
                                                                         "skip_tls_check": False, "repeat": {"interval": "1m", "times": 2}}, {"type": "slack"}],
                     "rule": {"type": "column", "config": {"column": "level", "operator": "=", "ignoreCase": True, "value": 1000000, "repeats": 3}}},
                    {"name": "second", "targets": [], "rule": {"config": {"column": "msg", "operator": "contains", "value": "x"}}}]}))]

    def paths(routes):
        return [r["method"] + " " + r["path"] for r in routes]

    full = info_routes()
    for output in ("json", "text"):
        flag = ["-o", "json"] if output == "json" else []
        add(f"dataset-info-{output}", ["dataset", "info", "logs", *flag],
            expect(0, paths(full), out=['"compression_ratio": "70.00%"', '"ingestion_size": "5.0 GiB"', '"dataset_type": "logs"']
                   if output == "json" else ["Compression Ratio: 70.00%", "Rule:    level = 1e+06 repeated 3 times", "Targets: webhook, slack, \n"]),
            group="dataset", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": full}, benchmark=True)
        empty = info_routes(info={"streamType": "Internal"}, stats=("", 404), retention=("", 404), alerts=("", 404))
        add(f"dataset-info-empty-{output}", ["dataset", "stat", "logs", *flag],
            expect(0, paths(empty), out=['"retention": null', '"alerts": []', '"compression_ratio": "n/a"']
                   if output == "json" else ["No retention period set on dataset\n", "No alerts set on dataset\n", "Dataset Type:      Internal"]),
            group="dataset", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": empty})
        missing = [response("GET", "/api/v1/logstream/nope/info", "stream not found", 404)]
        add(f"dataset-info-not-found-{output}", ["dataset", "info", "nope", *flag],
            expect(1, paths(missing), **({"err_json": error_json("NOT_FOUND", 'dataset "nope" not found', 404)} if output == "json"
                                          else {"err_exact": 'Error: dataset "nope" not found\n'})),
            group="dataset-errors", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": missing})
        failing = info_routes(stats=("overloaded", 503))[:2]
        add(f"dataset-info-stats-503-{output}", ["dataset", "info", "logs", *flag],
            expect(1, paths(failing), **({"err_json": error_json("SERVER_ERROR", "request failed: 503 Service Unavailable", 503, True)}
                                          if output == "json" else {"err": ["Status Code: 503 Service Unavailable\nResponse: overloaded\n"]})),
            group="dataset-errors", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": failing})
    for label, routes, code, message, status in (
            ("info-500", [response("GET", "/api/v1/logstream/logs/info", "boom", 500)], "SERVER_ERROR", "request failed: 500 Internal Server Error", 500),
            ("retention-403", info_routes(retention=("denied", 403))[:3], "PERMISSION_DENIED", "request failed: 403 Forbidden", 403),
            ("alert-409", info_routes(alerts=("conflict", 409)), "CONFLICT", "request failed: 409 Conflict", 409)):
        add(f"dataset-info-{label}-json", ["dataset", "info", "logs", "-o", "json"],
            expect(1, paths(routes), err_json=error_json(code, message, status, status >= 500)),
            group="dataset-errors", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": routes})
    malformed = [response("GET", "/api/v1/logstream/logs/info", "[1,")]
    add("dataset-info-malformed-info-json", ["dataset", "info", "logs", "-o", "json"],
        expect(1, paths(malformed), err=["failed to unmarshal response: unexpected end of JSON input"]),
        group="dataset-errors", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": malformed})
    bad_stats = info_routes(stats='{"ingestion":{"count":"x"}}')[:2]
    add("dataset-info-malformed-stats-text", ["dataset", "info", "logs"],
        expect(1, paths(bad_stats), err=["Error: json: cannot unmarshal string"]),
        group="dataset-errors", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": bad_stats})
    small = info_routes(info={"streamType": "UserDefined"}, stats={"ingestion": {"count": 1, "size": 1536}, "storage": {"size": 0}},
                        retention=[], alerts={"alerts": None})
    add("dataset-info-small-json", ["dataset", "info", "logs", "-o", "json"],
        expect(0, paths(small), out=['"ingestion_size": "1.5 KiB"', '"storage_size": "0 B"', '"compression_ratio": "n/a"',
                                     '"dataset_type": "UserDefined"', '"retention": []']),
        group="dataset", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": small})
    add("dataset-info-bad-output-after-fetch", ["dataset", "info", "logs", "-o", "yaml"],
        expect(1, paths(full), err=['unsupported output format "yaml"']),
        group="dataset-errors", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": full})
    for label, name, route_name in (("space", "my logs", "my logs"), ("unicode", "café", "café"),
                                    ("query-chars", "a?b#c", "a?b#c"), ("dot-segments", "../x", None)):
        routes = info_routes(name=route_name, alerts=("", 404)) if route_name else \
            [response(r["method"], r["path"].replace("/api/v1/logstream/../x", "/api/v1/x"), r["body"], r.get("status"))
             for r in info_routes(name="../x", alerts=("", 404))]
        add(f"dataset-info-escaping-{label}", ["dataset", "info", name, "-o", "json"],
            expect(0, paths(routes)), group="dataset", config={"auth": "basic"},
            fixture={"headers": HEADERS["basic"], "routes": routes})
    add("dataset-info-cloud-cookie-json", ["dataset", "info", "logs", "-o", "json"],
        expect(0, paths(full)), group="dataset", config={"auth": "cloud-cookie"},
        fixture={"headers": HEADERS["cloud-cookie"], "routes": full})

    # ----- dataset schema -----------------------------------------------------
    schema_body = ('{"metadata":{"z":1,"a":12345678901234567890,"html":"<&>","f":1.50e3},"fields":['
                   '{"name":"message","data_type":"Utf8","nullable":true,"dict_id":0},'
                   '{"name":"p_timestamp","data_type":{"Timestamp":["Millisecond",null]},"nullable":false},'
                   '{"name":"tags","data_type":{ "List" : { "name":"item", "data_type":"Utf8" } }},'
                   '{"name":"num","data_type":7},{"name":"café <x>","data_type":"Int64"}]}')
    schema = [response("GET", "/api/v1/logstream/logs/schema", schema_body)]
    add("dataset-schema-json", ["dataset", "schema", "logs", "-o", "json"],
        expect(0, paths(schema), out=['"a": 12345678901234567890', '"f": 1.50e3', '"html": "<&>"', '{\n  "fields": [\n']),
        group="dataset", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": schema}, benchmark=True)
    add("dataset-schema-text", ["dataset", "schema", "logs"],
        expect(0, paths(schema), out=["message [Utf8]", 'p_timestamp [{"Timestamp":["Millisecond",null]}]',
                                       'tags [{"List":{"name":"item","data_type":"Utf8"}}]', "num [7]", "café <x> [Int64]"]),
        group="dataset", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": schema}, benchmark=True)
    for mode in ("api-key", "cloud-key", "cloud-cookie"):
        add(f"dataset-schema-{mode}-json", ["dataset", "schema", "logs", "-o", "json"],
            expect(0, paths(schema)), group="dataset", config={"auth": mode},
            fixture={"headers": HEADERS[mode], "routes": schema})
    for label, body, code, message in (
            ("invalid", "{", "INVALID_RESPONSE", "invalid dataset schema: unexpected end of JSON input"),
            ("no-fields", "{}", "INVALID_RESPONSE", "dataset schema has no fields array"),
            ("null-fields", '{"fields":null}', "INVALID_RESPONSE", "dataset schema has no fields array"),
            ("trailing-value", '{"fields":[]} {"x":1}', "INVALID_RESPONSE", "invalid dataset schema: invalid character '{' after top-level value"),
            ("wrong-type", '{"fields":{}}', "INVALID_RESPONSE", None)):
        routes = [response("GET", "/api/v1/logstream/logs/schema", body)]
        add(f"dataset-schema-{label}-json", ["dataset", "schema", "logs", "-o", "json"],
            expect(1, paths(routes), **({"err_json": error_json(code, message)} if message else {"err": ['"code": "INVALID_RESPONSE"']})),
            group="dataset-errors", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": routes})
    for label, body, out_exact in (("empty-fields", '{"fields":[]}', None), ("unicode-escapes", '{"fields":[{"name":"\\u00e9\\u2028","data_type":"\\u003c"}]}', None)):
        routes = [response("GET", "/api/v1/logstream/logs/schema", body)]
        for output in ("json", "text"):
            add(f"dataset-schema-{label}-{output}", ["dataset", "schema", "logs", *(["-o", "json"] if output == "json" else [])],
                expect(0, paths(routes)), group="dataset", config={"auth": "basic"},
                fixture={"headers": HEADERS["basic"], "routes": routes})
    missing_type = [response("GET", "/api/v1/logstream/logs/schema", '{"fields":[{"name":"x"}]}')]
    add("dataset-schema-missing-type-text", ["dataset", "schema", "logs"],
        expect(1, paths(missing_type), err=['Error: invalid type for field "x": unexpected end of JSON input\n']),
        group="dataset-errors", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": missing_type})
    add("dataset-schema-missing-type-json", ["dataset", "schema", "logs", "-o", "json"],
        expect(0, paths(missing_type), out_exact='{\n  "fields": [\n    {\n      "name": "x"\n    }\n  ]\n}\n'),
        group="dataset", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": missing_type})
    for status, code, retry in ((404, "NOT_FOUND", False), (400, "INVALID_INPUT", False), (502, "SERVER_ERROR", True)):
        routes = [response("GET", "/api/v1/logstream/logs/schema", " stream missing \n", status)]
        reason = {404: "404 Not Found", 400: "400 Bad Request", 502: "502 Bad Gateway"}[status]
        add(f"dataset-schema-http-{status}-json", ["dataset", "schema", "logs", "-o", "json"],
            expect(1, paths(routes), err_json=error_json(code, f"get dataset schema failed: {reason}", status, retry)),
            group="dataset-errors", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": routes})
        add(f"dataset-schema-http-{status}-text", ["dataset", "schema", "logs"],
            expect(1, paths(routes), err=[f"Error: get dataset schema failed: {reason}: stream missing\n"]),
            group="dataset-errors", config={"auth": "basic"}, fixture={"headers": HEADERS["basic"], "routes": routes})
    add("dataset-schema-unauthorized-json", ["dataset", "schema", "logs", "-o", "json"],
        expect(1, paths(schema), err_json=error_json("AUTH_FAILED", "get dataset schema failed: 401 Unauthorized", 401)),
        group="dataset-errors", config={"auth": "basic"}, fixture={"headers": WRONG, "routes": schema})
    add("dataset-schema-connection-refused-text", ["dataset", "schema", "logs"],
        expect(1, [], err=['Error: Get "http://127.0.0.1:1/api/v1/logstream/logs/schema": dial tcp 127.0.0.1:1: connect: connection refused\n']),
        group="dataset-errors", config=single(url=NO_SERVER))
    add("dataset-schema-path-escaping", ["dataset", "schema", "a b/c%2F", "-o", "json"],
        expect(0, ["GET /api/v1/logstream/a b/c//schema"]), group="dataset", config={"auth": "basic"},
        fixture={"headers": HEADERS["basic"], "routes": [response("GET", "/api/v1/logstream/a b/c//schema", '{"fields":[]}')]})
    add("dataset-schema-bad-output-before-fetch", ["dataset", "schema", "logs", "-o", "yaml"],
        expect(1, [], err=['unsupported output format "yaml"']), group="dataset-errors", config={"auth": "basic"},
        fixture={"headers": HEADERS["basic"], "routes": schema})

    # ----- OAuth session refresh ---------------------------------------------
    cookie_profile = {"url": "{{url}}", "cloud": True, "session_token": "fixture-session", "refresh_token": "fixture-refresh",
                      "tenant_id": "fixture-tenant", "orchestrator_url": "{{url}}/orch"}
    bystander = {"url": "https://other.invalid", "cloud": True, "session_token": "other-session", "refresh_token": "other-refresh",
                 "tenant_id": "other-tenant"}
    refresh_config = raw(config_toml({"fixture": cookie_profile, "bystander": bystander}))
    old = {"cookie": "session=fixture-session", "x-p-tenant": "fixture-tenant"}
    new = {"cookie": "session=rotated-session", "x-p-tenant": "fixture-tenant"}
    grant = {"authorization": "Bearer " + ORCHESTRATOR_TOKEN, "content-type": "application/json", "accept": "application/json"}
    token_path = "/orch/api/v1/cli/oauth/token"
    rotated = {"access_token": "rotated-session", "refresh_token": "rotated-refresh", "token_type": "bearer"}
    persisted = {"profiles": {"fixture": {"session_token": "rotated-session", "refresh_token": "rotated-refresh"},
                              "bystander": {"session_token": "other-session", "refresh_token": "other-refresh"}}}

    def refresh_scenario(final_routes, token_route=None):
        return {"routes": [response("GET", "/api/v1/about", "expired session", 401, headers=old, times=1),
                           token_route or response("POST", token_path, rotated, headers=grant, times=1), *final_routes]}

    add("refresh-status-json", ["status", "-o", "json"],
        expect(0, ["GET /api/v1/about", "POST " + token_path, "GET /api/v1/about"], out=['"healthy": true'], config=persisted),
        group="refresh", build="cloud", config=refresh_config,
        stateful=refresh_scenario([response("GET", "/api/v1/about", about_body, headers=new)]))
    add("refresh-dataset-info-reuses-token", ["dataset", "info", "logs", "-o", "json"],
        expect(0, ["GET /api/v1/logstream/logs/info", "POST " + token_path, "GET /api/v1/logstream/logs/info",
                   "GET /api/v1/logstream/logs/stats", "GET /api/v1/logstream/logs/retention", "GET /api/v1/logstream/logs/alert"],
               config=persisted),
        group="refresh", build="cloud", config=refresh_config,
        stateful={"routes": [response("GET", "/api/v1/logstream/logs/info", "expired", 401, headers=old, times=1),
                             response("POST", token_path, rotated, headers=grant, times=1),
                             *[{**r, "headers": new} for r in info_routes()]]})
    add("refresh-dataset-list", ["dataset", "list", "-o", "json"],
        expect(0, ["GET /api/prism/v1/home", "POST " + token_path, "GET /api/prism/v1/home"], out_json=listed, config=persisted),
        group="refresh", build="cloud", config=refresh_config,
        stateful={"routes": [response("GET", "/api/prism/v1/home", "expired", 401, headers=old, times=1),
                             response("POST", token_path, rotated, headers=grant, times=1), {**home, "headers": new}]})
    add("refresh-retry-still-unauthorized", ["status", "-o", "json"],
        expect(1, ["GET /api/v1/about", "POST " + token_path, "GET /api/v1/about"], out=[auth_failed], config=persisted),
        group="refresh", build="cloud", config=refresh_config,
        stateful=refresh_scenario([response("GET", "/api/v1/about", "still expired", 401, headers=new)]))
    for label, token_route, message in (
            ("grant-rejected", response("POST", token_path, '{"error":"invalid_grant"}\n', 400, headers=grant),
             'cloud session refresh failed: cloud session refresh failed: 400 Bad Request: {\\"error\\":\\"invalid_grant\\"}'),
            ("missing-refresh-token", response("POST", token_path, {"access_token": "x"}, headers=grant),
             "cloud session refresh failed: cloud session refresh response missing access_token or refresh_token"),
            ("malformed-grant", response("POST", token_path, "<html>", headers=grant), "cloud session refresh failed: invalid character")):
        add(f"refresh-{label}", ["status", "-o", "json"],
            expect(1, ["GET /api/v1/about", "POST " + token_path], out=[message], config="unchanged"),
            group="refresh", build="cloud", config=refresh_config, stateful=refresh_scenario([], token_route))
    add("refresh-persist-failure", ["dataset", "schema", "logs", "-o", "json"],
        expect(1, ["GET /api/v1/logstream/logs/schema", "POST " + token_path],
               err=["failed to persist refreshed cloud tokens: create config file: open <HOME>/config/pb/config.toml: permission denied"],
               config="unchanged"),
        group="refresh", build="cloud", config=raw(config_toml({"fixture": cookie_profile}), mode=0o400), requires=["nonroot"],
        stateful={"routes": [response("GET", "/api/v1/logstream/logs/schema", "expired", 401, headers=old, times=1),
                             response("POST", token_path, rotated, headers=grant, times=1)]})
    add("refresh-not-for-cloud-key", ["status", "-o", "json"],
        expect(1, ["GET /api/v1/about"], out=[auth_failed], config="unchanged"),
        group="refresh", build="cloud",
        config=raw(config_toml({"fixture": {**profile("cloud-key"), "refresh_token": "r", "orchestrator_url": "{{url}}/orch"}})),
        stateful={"routes": [response("GET", "/api/v1/about", "denied", 401)]})
    add("refresh-needs-refresh-token", ["status", "-o", "json"],
        expect(1, ["GET /api/v1/about"], out=[auth_failed], config="unchanged"),
        group="refresh", build="cloud",
        config=raw(config_toml({"fixture": {**cookie_profile, "refresh_token": ""}})),
        stateful={"routes": [response("GET", "/api/v1/about", "denied", 401)]})
    add("refresh-needs-orchestrator", ["status", "-o", "json"],
        expect(1, ["GET /api/v1/about"], out=[auth_failed], config="unchanged"),
        group="refresh", build="cloud",
        config=raw(config_toml({"fixture": {k: v for k, v in cookie_profile.items() if k != "orchestrator_url"}})),
        stateful={"routes": [response("GET", "/api/v1/about", "denied", 401)]})
    for output in ("json", "text"):
        add(f"refresh-unconfigured-build-{output}", ["status", *(["-o", "json"] if output == "json" else [])],
            expect(1, ["GET /api/v1/about"], **({"out": ['cloud session refresh failed: cloud orchestrator auth token is not configured']}
                                                if output == "json" else
                                                {"err": ['Error   : Get "http://<FIXTURE>/api/v1/about": cloud session refresh failed: '
                                                         'cloud orchestrator auth token is not configured\n']}),
                   config="unchanged"),
            group="refresh", config=refresh_config, stateful={"routes": [response("GET", "/api/v1/about", "expired", 401, headers=old)]})

    # ----- interoperability chains: each writer/reader pair runs in all four orders
    def chain(name, writer, reader, *, config=None, fixture=None, stateful=None, build="standard", group="interop"):
        add(name, None, None, group=group, config=config, fixture=fixture, stateful=stateful, build=build, steps=[writer, reader])

    chain("interop-add-basic-then-list",
          step(["profile", "add", "new", "http://127.0.0.1:9", "usér", "p'\"\\ß"], expect(0, [], out_exact="Profile new added successfully\n")),
          step(["profile", "list", "-o", "json"], expect(0, [], out=['"new": {\n    "url": "http://127.0.0.1:9",\n    "username": "usér"'])),
          config=raw(base))
    chain("interop-add-key-then-status",
          step(["profile", "add", "a.b c'\"é", "{{url}}/p/", "--api-key", "psk_fixture"], expect(0, [])),
          step(["status", "-o", "json"], expect(0, ["GET /p/api/v1/about"], out=['"profile": "a.b c\'\\"é"'])),
          config=raw(base), fixture={"headers": {"x-api-key": "psk_fixture"}, "routes": [response("GET", "/p/api/v1/about", about_body)]})
    chain("interop-add-secret-then-status",
          step(["profile", "add", "new", "{{url}}", "usér", 'p"\\ä:ß\t#'], expect(0, [])),
          step(["status", "-o", "json"], expect(0, ["GET /api/v1/about"], out=['"healthy": true'])),
          fixture={"headers": basic("usér", 'p"\\ä:ß\t#'), "routes": [about]})
    chain("interop-legacy-migration-then-status",
          step(["profile", "add", "other", "http://127.0.0.1:9", "--api-key", "k"], expect(0, [])),
          step(["profile", "default", "legacy"], expect(0, [])),
          config=raw(config_toml({"legacy": {"url": "{{url}}", "token": "fixture-key"}}, default="legacy")))
    cases[-1]["steps"].append(step(["status", "-o", "json"], expect(0, ["GET /api/v1/about"], out=['"healthy": true'])))
    cases[-1]["fixture"] = {"kind": "go", "scenario": {"headers": {"x-api-key": "fixture-key"}, "routes": [about]}}
    chain("interop-default-then-status",
          step(["profile", "default", "target"], expect(0, [])),
          step(["status", "-o", "json"], expect(0, ["GET /api/v1/about"], out=['"profile": "target"'])),
          config=raw(config_toml({"fixture": profile(url=NO_SERVER), "target": profile("cloud-key")})),
          fixture={"headers": HEADERS["cloud-key"], "routes": [about]})
    chain("interop-update-then-dataset-list",
          step(["profile", "update", "fixture", "{{url}}/moved"], expect(0, [])),
          step(["dataset", "list", "-o", "json"], expect(0, ["GET /moved/api/prism/v1/home"], out_json=listed)),
          config=single(url=NO_SERVER), fixture={"headers": HEADERS["basic"], "routes": [{**home, "path": "/moved/api/prism/v1/home"}]})
    chain("interop-remove-then-list",
          step(["profile", "remove", "fixture"], expect(0, [])),
          step(["profile", "list", "-o", "json"], expect(0, [], out=['"cloud": {'])),
          config=raw(base))
    chain("interop-logout-then-status",
          step(["logout", "--yes"], expect(0, [])),
          step(["status", "-o", "json"], expect(0, ["GET /api/v1/about"], out=['"profile": "alpha"'])),
          config=raw(config_toml({"fixture": profile(url=NO_SERVER), "alpha": profile("api-key"), "zeta": profile("basic")})),
          fixture={"headers": HEADERS["api-key"], "routes": [about]})
    chain("interop-logout-last-then-add",
          step(["logout", "--yes", "-o", "json"], expect(0, [])),
          step(["profile", "add", "next", "http://127.0.0.1:9", "--api-key", "k", "-o", "json"],
               expect(0, [], config={"default": "next", "only": ["next"]})),
          config=single(url=NO_SERVER))
    chain("interop-cloud-add-then-list",
          step(["cloud", "profile", "add", "--api-key", "psk_cloud", "--orchestrator-url", "{{url}}"], expect(0, [vroute])),
          step(["profile", "list", "-o", "json"], expect(0, [], out=['"workspace_name": "My Workspace"', '"orchestrator_url": "http://<FIXTURE>"'])),
          build="cloud", config=raw(base),
          fixture={"headers": orchestrator, "routes": [response("GET", "/api/v1/cli/apikey/validate", validate)]})
    chain("interop-refresh-then-status",
          step(["status", "-o", "json"], expect(0, ["GET /api/v1/about", "POST " + token_path, "GET /api/v1/about"], config=persisted)),
          step(["status", "-o", "json"], expect(0, ["GET /api/v1/about"], out=['"healthy": true'])),
          build="cloud", config=refresh_config, group="refresh",
          stateful=refresh_scenario([response("GET", "/api/v1/about", about_body, headers=new)]))
    return cases


def deferred_boundaries(cases=None):
    """Interactive workflows graduated to the phase-seven PTY/effect gate.

    Retain historical definitions below for old evidence readers, never select
    coverage by inspecting the candidate binary. Ordinary cases are unchanged.
    """
    return []


def historical_deferred_boundaries(cases=None):
    """Candidate-only checks for future terminal cases: an explicit unimplemented error, no effects.

    Go itself prompts or needs /dev/tty here, so these are not compatibility claims. Profiles and the
    orchestrator point at a route-less loopback fixture, so any login, refresh or probe request is
    recorded, and the whole HOME tree must be byte-identical afterwards.
    """
    watched = config_toml({
        "fixture": profile("basic"),
        "cloud": {"url": "{{url}}/cloud", "cloud": True, "session_token": "fixture-session",
                  "refresh_token": "fixture-refresh", "tenant_id": "fixture-tenant", "orchestrator_url": "{{url}}/orch"}})
    checks = []
    for case in cases if cases is not None else corpus():
        if case["scope"] != FUTURE_TERMINAL:
            continue
        step = case["steps"][0]
        checks.append({"name": case["name"].replace("p2-", "p2-boundary-", 1), "source_case": case["name"],
                       "command": step["argv"][0], "location": "xdg",
                       "config": {"toml": watched, "mode": 0o600} if case.get("config") else None,
                       "fixture": {"kind": "stateful", "scenario": {"routes": []}},
                       "steps": [{**step, "expect": None}]})
    return checks


def boundary_issues(check, result):
    issues = []
    actual = result["steps"][0]
    if actual["failure"]:
        issues.append(actual["failure"])
    if actual["termination"] != {"exit": 1}:
        issues.append(f"termination {actual['termination']}, expected exit 1 for an unimplemented operation")
    if actual["stdout"]:
        issues.append("stdout must be empty (no prompt, cancellation or success output)")
    first = actual["stderr"].split(b"\n", 1)[0]
    if not (first.startswith(b"Error: ") and STUB_MARKER in first and check["command"].encode() in first):
        issues.append(f"stderr must start with an explicit 'Error: ...{check['command']}... {STUB_MARKER.decode()}' line")
    if actual["trace"]:
        issues.append(f"network requests made: {request_summary(actual)}")
    if result["config_before"] != result["config_after"]:
        changed = sorted(path for path in {*result["config_before"], *result["config_after"]}
                         if result["config_before"].get(path) != result["config_after"].get(path))
        issues.append(f"HOME changed: {changed}")
    return issues


# --------------------------------------------------------------------------
# Stateful loopback fixture (sequenced routes, per-route headers).


def serve(scenario_path):
    """Run a disposable HTTP/1.1 fixture; protocol matches harness/fixture for run.Fixture."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    plan = json.loads(Path(scenario_path).read_text())
    routes = plan.get("routes", [])
    remaining = [route.get("times", 0) for route in routes]
    trace = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def body(self):
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                data = bytearray()
                while True:
                    size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                    if size == 0:
                        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                            pass
                        return bytes(data)
                    data.extend(self.rfile.read(size))
                    self.rfile.readline()
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def send(self, status, body, content_type="application/json"):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def handle_any(self):
            if self.command == "GET" and self.path == "/__harness/trace":
                with lock:
                    return self.send(200, json.dumps(trace))
            body = self.body()
            headers = {name: self.headers[name] for name in CONTRACT_HEADERS if self.headers.get(name)}
            path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            with lock:
                selected = None
                for index, route in enumerate(routes):
                    if route["method"] == self.command and route["path"] == path and \
                            all(headers.get(k.lower()) == v for k, v in route.get("headers", {}).items()) and \
                            (not route.get("times") or remaining[index] > 0):
                        selected = index
                        if route.get("times"):
                            remaining[index] -= 1
                        break
                trace.append({"protocol": "http", "method": self.command, "path": self.path, "headers": headers,
                              "body": body.decode(errors="replace"), "time_unix_ns": time.time_ns(),
                              "unexpected": selected is None})
            if not all(headers.get(k.lower()) == v for k, v in plan.get("headers", {}).items()):
                return self.send(401, "fixture: incorrect authentication\n", "text/plain; charset=utf-8")
            if selected is None:
                return self.send(500, "fixture: unexpected request\n", "text/plain; charset=utf-8")
            route = routes[selected]
            self.send(route.get("status") or 200, route.get("body", "").replace("{{url}}", url))

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_any

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(json.dumps({"url": url, "grpc_port": 0}), flush=True)
    server.serve_forever()


class StatefulFixture(run.Fixture):
    def __init__(self, scenario, root):
        self.proc = None
        self.root = root
        path = root / "scenario.json"
        run.write_json(path, scenario)
        self.log = (root / "fixture.stderr").open("wb")
        self.proc = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "serve", "--scenario", str(path)],
                                     stdout=subprocess.PIPE, stderr=self.log, start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise RuntimeError("stateful fixture did not become ready within 10s")
            info = json.loads(self.proc.stdout.readline())
            self.url, self.port = info["url"], info["grpc_port"]
        except Exception as error:
            self.close()
            raise RuntimeError(f"stateful fixture startup failed: {error}") from error


# --------------------------------------------------------------------------
# Process execution and state capture.


def isolated_env(home, step):
    env = {"PATH": os.defpath, "HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
           "XDG_CACHE_HOME": str(home / "cache"), "XDG_DATA_HOME": str(home / "data"),
           "XDG_STATE_HOME": str(home / "state"), "TMPDIR": str(home / "tmp"),
           "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "TERM": "dumb", "PB_THEME": "dark", "PB_ANALYTICS": "disable"}
    for name in step.get("unset", []):
        env.pop(name, None)
    env.update(step.get("env", {}))
    return env


def execute(binary, argv, env, cwd, stdin=b"", timeout=10):
    started = time.monotonic()
    proc = subprocess.Popen([str(binary), *argv], cwd=cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    data = {"stdout": bytearray(), "stderr": bytearray()}
    failure = None
    try:
        try:
            proc.stdin.write(stdin)
        except BrokenPipeError:
            pass
        proc.stdin.close()
        with selectors.DefaultSelector() as selector:
            for name, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            while selector.get_map():
                elapsed = time.monotonic() - started
                if elapsed > timeout and failure is None:
                    failure = "timeout"
                    run.kill_group(proc, signal.SIGKILL)
                if elapsed > timeout + 2:
                    failure = failure or "descendant-held-pipe"
                    break
                for key, _ in selector.select(0.02):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    room = MAX_OUTPUT - sum(map(len, data.values()))
                    data[key.data].extend(chunk[:max(room, 0)])
                    if len(chunk) > room:
                        failure = failure or "output-limit"
                        run.kill_group(proc, signal.SIGKILL)
        try:
            proc.wait(max(0.1, timeout + 2 - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            failure = failure or "timeout"
    finally:
        run.kill_group(proc, signal.SIGKILL)
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()
    status = proc.returncode
    return {"exit_code": status, "termination": {"signal": -status} if status < 0 else {"exit": status},
            "stdout": bytes(data["stdout"]), "stderr": bytes(data["stderr"]), "failure": failure,
            "wall_ms": (time.monotonic() - started) * 1000}


def config_state(home, replacements):
    """Semantic state of HOME: types/modes for every entry, parsed TOML for config files."""
    state = {}
    kinds = {stat.S_IFREG: "file", stat.S_IFDIR: "directory", stat.S_IFLNK: "symlink", stat.S_IFIFO: "fifo"}
    for full in [home, *sorted(home.rglob("*"))]:
        path = str(full.relative_to(home))
        metadata = full.lstat()
        kind = stat.S_IFMT(metadata.st_mode)
        entry = {"type": kinds.get(kind, "other"), "mode": oct(stat.S_IMODE(metadata.st_mode))}
        if kind == stat.S_IFLNK:
            entry["target"] = os.readlink(full)
        if kind == stat.S_IFREG:
            content = full.read_bytes() if os.access(full, os.R_OK) else None
            entry["raw_sha256"] = run.digest(content) if content is not None else None
            if content is not None and path.endswith(".toml"):
                try:
                    entry["toml"] = tomllib.loads(run.normalize(content, replacements).decode())
                except (ValueError, UnicodeError):
                    entry["toml_error"] = True
            elif content is None:
                entry["unreadable"] = True
        state[path] = entry
    return state


def seed(home, case, url):
    spec = case.get("config")
    folder = home / (".config" if case.get("location") == "home" else "config") / "pb"
    if spec is None:
        return
    if "auth" in spec:
        if case.get("location") == "home":
            run.seed_config(home / "tmp-seed", url, spec["auth"])
            folder.parent.mkdir(parents=True, exist_ok=True)
            (home / "tmp-seed" / "config" / "pb").rename(folder)
            (home / "tmp-seed" / "config").rmdir()
            (home / "tmp-seed").rmdir()
        else:
            run.seed_config(home, url, spec["auth"])
        return
    folder.mkdir(parents=True)
    path = folder / "config.toml"
    if spec.get("directory"):
        path.mkdir()
        return
    path.write_text(spec["toml"].replace("{{url}}", url))
    path.chmod(spec.get("mode", 0o600))


def substitute(value, url):
    return value.replace("{{url}}", url) if isinstance(value, str) else value


def sample(binaries, case, fixture_binary, timeout=10):
    """Run every step (binary i runs step i) in one fresh HOME with one fixture lifecycle."""
    with tempfile.TemporaryDirectory(prefix="pb-phase2-") as temporary:
        root = Path(temporary)
        home = root / "home"
        fixture_root = root / "fixture"
        fixture_root.mkdir()
        for name in ("config", "cache", "data", "state", "tmp"):
            (home / name).mkdir(parents=True)
        spec = case.get("fixture")
        fixture = None
        try:
            if spec and spec["kind"] == "stateful":
                fixture = StatefulFixture(spec["scenario"], fixture_root)
            elif spec:
                if not fixture_binary:
                    raise ValueError("fixture cases require --fixture (make -f harness/Makefile tools)")
                fixture = run.Fixture(fixture_binary, spec["scenario"], fixture_root)
            url = fixture.url if fixture else NO_SERVER
            seed(home, case, url)
            replacements = {str(home): "<HOME>"}
            if fixture:
                replacements[url] = "http://<FIXTURE>"
                replacements[url.removeprefix("http://")] = "<FIXTURE-HOST>"
            before = config_state(home, replacements)
            steps = []
            seen = 0
            for binary, step in zip(binaries, case["steps"]):
                result = execute(binary, [substitute(a, url) for a in step["argv"]],
                                 {k: substitute(v, url) for k, v in isolated_env(home, step).items()},
                                 home, substitute(step.get("stdin", ""), url).encode(), timeout)
                trace = fixture.trace() if fixture else []
                result["trace"] = trace[seen:]
                seen = len(trace)
                result["binary"] = str(binary)
                result["stdout_normalized"] = run.normalize(result["stdout"], replacements)
                result["stderr_normalized"] = run.normalize(result["stderr"], replacements)
                result["requests"] = [{**r, "path": run.normalize(r["path"].encode(), replacements).decode()}
                                      for r in run.request_contract(result["trace"])]
                steps.append(result)
            after = config_state(home, replacements)
            config_text = None
            for candidate in (home / "config" / "pb" / "config.toml", home / ".config" / "pb" / "config.toml"):
                if candidate.is_file() and os.access(candidate, os.R_OK):
                    config_text = candidate.read_bytes()
            return {"steps": steps, "config_before": before, "config_after": after, "config_raw_after": config_text,
                    "config_normalized_after": None if config_text is None else run.normalize(config_text, replacements)}
        finally:
            if fixture:
                fixture.close()


# --------------------------------------------------------------------------
# Expectations (reference validity) and differential comparison.


def config_issues(expected, result):
    if expected == "unchanged":
        # Byte-exact: a semantically equal rewrite still drops comments and formatting.
        return [] if result["config_before"] == result["config_after"] else ["configuration changed"]
    issues = []
    path = expected.get("path", "config/pb/config.toml")
    entry = result["config_after"].get(path)
    if not entry or "toml" not in entry:
        return [f"expected parseable config at {path}"]
    data = entry["toml"]
    if "mode" in expected and entry["mode"] != expected["mode"]:
        issues.append(f"config mode {entry['mode']}, expected {expected['mode']}")
    if "default" in expected and data.get("DefaultProfile") != expected["default"]:
        issues.append(f"DefaultProfile {data.get('DefaultProfile')!r}, expected {expected['default']!r}")
    profiles = data.get("Profiles", {})
    if "only" in expected and sorted(profiles) != sorted(expected["only"]):
        issues.append(f"profiles {sorted(profiles)}, expected {sorted(expected['only'])}")
    for name, fields in expected.get("profiles", {}).items():
        actual = profiles.get(name)
        if actual is None:
            issues.append(f"profile {name!r} missing")
            continue
        for key, value in fields.items():
            if actual.get(key) != value:
                issues.append(f"profile {name!r} field {key} = {actual.get(key)!r}, expected {value!r}")
    return issues


def expectation_issues(case, result):
    """Assert the reference actually exercised each case's intended behavior."""
    issues = []
    for index, (step, actual) in enumerate(zip(case["steps"], result["steps"])):
        label = f"step {index}: " if len(case["steps"]) > 1 else ""
        want = step["expect"]
        if actual["failure"]:
            issues.append(label + actual["failure"])
        if actual["exit_code"] != want["exit"]:
            issues.append(f"{label}exit {actual['exit_code']}, expected {want['exit']}")
        requests = [r["method"] + " " + urllib.parse.unquote(urllib.parse.urlsplit(r["path"]).path) for r in actual["trace"]]
        if requests != want["requests"]:
            issues.append(f"{label}requests {requests!r}, expected {want['requests']!r}")
        if any(r["unexpected"] for r in actual["trace"]):
            issues.append(label + "unexpected fixture request")
        for stream in ("stdout", "stderr"):
            data = actual[stream + "_normalized"]
            text = data.decode(errors="replace")
            for needle in want.get(stream + "_has", []):
                if needle not in text:
                    issues.append(f"{label}{stream} lacks {needle!r}")
            if stream + "_exact" in want and text != want[stream + "_exact"]:
                issues.append(f"{label}{stream} {text!r}, expected {want[stream + '_exact']!r}")
            if stream + "_json" in want:
                try:
                    if json.loads(data) != want[stream + "_json"]:
                        issues.append(f"{label}{stream} JSON differs from expectation")
                except ValueError:
                    issues.append(f"{label}{stream} is not one JSON document")
        if "config" in want:
            issues.extend(label + issue for issue in config_issues(want["config"], result))
    return issues


def semantic(state):
    """Parsed TOML stands in for bytes; files without a parse (malformed, non-TOML) keep their digest."""
    return {path: {k: v for k, v in entry.items() if k != "raw_sha256" or "toml" not in entry}
            for path, entry in state.items()}


def rewrite_issues(reference, candidate):
    """Files the reference left byte-identical must not be rewritten, even to equal TOML."""
    issues = []
    for path, entry in reference["config_before"].items():
        kept = entry.get("raw_sha256") is not None and reference["config_after"].get(path, {}).get("raw_sha256") == entry["raw_sha256"]
        before = candidate["config_before"].get(path, {}).get("raw_sha256")
        if kept and before is not None and candidate["config_after"].get(path, {}).get("raw_sha256") != before:
            issues.append(f"{path} rewritten although the reference left it byte-identical")
    return issues


def stream_issue(name, expected, actual):
    offset = next((i for i, pair in enumerate(zip(expected, actual)) if pair[0] != pair[1]), min(len(expected), len(actual)))
    return f"{name} differs at byte {offset}: candidate {len(actual)} bytes, reference {len(expected)} bytes"


def differences(case, reference, candidate):
    issues = []
    unordered = "stderr_unordered_lines" in case.get("compare", {})
    for index, (ref, cand) in enumerate(zip(reference["steps"], candidate["steps"])):
        label = f"step {index} " if len(reference["steps"]) > 1 else ""
        for role, result in (("reference", ref), ("candidate", cand)):
            if result["failure"]:
                issues.append(f"{label}{role}: {result['failure']}")
        if STUB_MARKER in cand["stderr"] or STUB_MARKER in cand["stdout"]:
            issues.append(f"{label}candidate returned a deferred-operation stub")
        if ref["termination"] != cand["termination"]:
            issues.append(f"{label}termination {cand['termination']}, expected {ref['termination']}")
        for stream in ("stdout", "stderr"):
            expected, actual = ref[stream + "_normalized"], cand[stream + "_normalized"]
            if stream == "stderr" and unordered:
                expected, actual = sorted(expected.splitlines(True)), sorted(actual.splitlines(True))
                if expected != actual:
                    issues.append(f"{label}stderr lines differ (unordered comparison)")
            elif expected != actual:
                issues.append(label + stream_issue(stream, expected, actual))
        if ref["requests"] != cand["requests"]:
            issues.append(f"{label}requests differ: candidate {request_summary(cand)}, reference {request_summary(ref)}")
    for key in ("config_before", "config_after"):
        if semantic(reference[key]) != semantic(candidate[key]):
            issues.append(key.replace("_", " ") + " differs semantically")
    return issues + rewrite_issues(reference, candidate)


def request_summary(result):
    return [r["method"] + " " + r["path"] for r in result["requests"]]


def advisories(reference, candidate):
    notes = []
    if reference.get("config_normalized_after") != candidate.get("config_normalized_after"):
        notes.append("final config bytes differ (semantically equal TOML is accepted)")
    return notes


# --------------------------------------------------------------------------
# Orchestration, artifacts and exports.


def assignments(case, reference, candidate):
    """Single commands compare R vs C; chains also cross writers/readers both ways."""
    count = len(case["steps"])
    plans = [("candidate", [candidate] * count)]
    if count > 1 and reference != candidate:
        for split in range(1, count):
            plans.append((f"reference-then-candidate-{split}", [reference] * split + [candidate] * (count - split)))
            plans.append((f"candidate-then-reference-{split}", [candidate] * split + [reference] * (count - split)))
    return plans


def public(result):
    steps = []
    for step in result["steps"]:
        steps.append({"binary": step["binary"], "exit_code": step["exit_code"], "termination": step["termination"],
                      "failure": step["failure"], "wall_ms": step["wall_ms"],
                      "stdout_sha256": run.digest(step["stdout_normalized"]), "stderr_sha256": run.digest(step["stderr_normalized"]),
                      "requests": step["requests"]})
    return {"steps": steps, "config_before": result["config_before"], "config_after": result["config_after"]}


def store(folder, result):
    folder.mkdir(parents=True)
    for index, step in enumerate(result["steps"]):
        prefix = folder / f"step-{index}"
        prefix.mkdir()
        for stream in ("stdout", "stderr"):
            (prefix / f"{stream}.bin").write_bytes(step[stream])
            (prefix / f"{stream}.normalized").write_bytes(step[stream + "_normalized"])
        run.write_json(prefix / "requests.json", step["trace"])
    if result["config_raw_after"] is not None:
        (folder / "config-after.toml").write_bytes(result["config_raw_after"])
    run.write_json(folder / "result.json", public(result))


def requirement_skip(case, binaries):
    if case["build"] == "cloud" and not binaries.get("cloud"):
        return "requires binaries built with the harness cloud orchestrator token"
    if "nonroot" in case["requires"] and os.geteuid() == 0:
        return "requires a non-root user (permission-denied paths)"
    return None


def select(cases, names=None, groups=None, include_future=False):
    if names:
        unknown = set(names) - {c["name"] for c in cases}
        if unknown:
            raise ValueError(f"unknown cases: {sorted(unknown)}")
        deferred = sorted(c["name"] for c in cases if c["name"] in names and c["scope"] != PHASE2_SCOPE)
        if deferred and not include_future:
            raise ValueError(f"{deferred} are future terminal cases outside phase two; pass --include-future-terminal")
        cases = [c for c in cases if c["name"] in names]
    if not include_future:
        cases = [c for c in cases if c["scope"] == PHASE2_SCOPE]
    if groups:
        cases = [c for c in cases if c["group"] in groups or c["group"].removeprefix("phase2-") in groups]
    if not cases:
        raise ValueError("no cases selected")
    return cases


def check_version(path):
    with tempfile.TemporaryDirectory(prefix="pb-phase2-version-") as folder:
        result = execute(path, ["--version"], isolated_env(Path(folder), {}), folder, b"", 10)
    if result["failure"] or result["exit_code"] or result["stdout"] != VERSION_STDOUT or result["stderr"]:
        raise ValueError(f"{path} must report {VERSION_STDOUT.decode().strip()!r}")


def benchmark_suite(cases):
    """Fixed run.py workloads; only cases expressible by run.py's seeded profiles."""
    suite = []
    for case in cases:
        if not case["benchmark"]:
            continue
        if case["scope"] != PHASE2_SCOPE:
            raise ValueError(f"{case['name']}: only phase-two cases may be measured")
        step = case["steps"][0]
        config = case.get("config") or {}
        fixture = case.get("fixture")
        if len(case["steps"]) != 1 or step["env"] or step["unset"] or step["stdin"] or case["build"] != "standard" \
                or (fixture and fixture["kind"] != "go") or (config and "auth" not in config and "toml" not in config):
            raise ValueError(f"{case['name']}: benchmark case is not expressible by run.py")
        # run.py compares raw (still-escaped) paths; phase-two expectations are unescaped.
        if any(urllib.parse.quote(r, safe="/ ") != r for r in step["expect"]["requests"]):
            raise ValueError(f"{case['name']}: escaped request paths are not comparable by run.py")
        entry = {"name": case["name"], "group": case["group"], "argv": step["argv"], "profile": bool(config),
                 "fixture": fixture["scenario"] if fixture else None, "expected_exit": step["expect"]["exit"],
                 "expected_requests": step["expect"]["requests"]}
        if "auth" in config:
            entry["auth"] = config["auth"]
        elif config:
            # run.py seeds only the standard fixture profile; refuse to silently change the workload.
            raise ValueError(f"{case['name']}: benchmark cases must use an auth-mode seeded profile")
        suite.append(entry)
    return suite


def write_report(out, report):
    run.write_json(out / "report.json", report)
    counts = collections.Counter(item["status"] for item in report["cases"])
    lines = [f"# Phase-two {report['mode']}", "",
             f"Passed {counts['pass']}, failed {counts['fail']}, skipped {counts['skip']} of {len(report['cases'])} cases.",
             f"Corpus SHA-256: `{report['corpus_sha256']}`.", f"Reference: `{report['binaries']['reference']['sha256']}`."]
    if report["binaries"].get("candidate"):
        lines.append(f"Candidate: `{report['binaries']['candidate']['sha256']}`.")
    for role in ("cloud_reference", "cloud_candidate"):
        if report["binaries"].get(role):
            lines.append(f"{role.replace('_', ' ').title()}: `{report['binaries'][role]['sha256']}`.")
    if report.get("self_check"):
        lines.append("SELF-CHECK: candidate is the reference executable; this validates the harness, not the Rust port.")
    if report.get("partial"):
        lines.append("PARTIAL: a case/group filter was applied; this is not the full phase-two gate.")
    scope = report["scope"]
    names = ", ".join(f"`{item['name']}`" for item in scope["future_terminal"])
    if scope["future_terminal_included"]:
        lines.append(f"OPT-IN: future terminal cases ({names}) are included; they are outside the phase-two gate.")
    else:
        lines.append(f"Scope: {scope['phase2_cases']} phase-two cases of {scope['defined_cases']} defined. "
                     f"EXCLUDED as future terminal ({scope['note']}): {names}.")
    lines.append(f"Deferred boundary checks: {sum(b['status'] == 'pass' for b in report['boundaries'])}/"
                 f"{len(report['boundaries'])} passed ({report['boundaries_status']}); candidate-only, not Go compatibility.")
    lines += ["", "Exact stdout/stderr bytes, termination and ordered requests are compared; configuration is",
              "compared as parsed TOML plus file types/modes. Only the temporary HOME and fixture origin are normalized.", ""]
    for item in report["cases"]:
        if item["status"] == "fail":
            lines += [f"FAIL `{item['name']}`:", *[f"- {issue}" for issue in item["issues"]], ""]
        elif item["status"] == "skip":
            lines.append(f"SKIP `{item['name']}`: {item['skip']}")
    for item in report["boundaries"]:
        if item["status"] == "fail":
            lines += ["", f"BOUNDARY FAIL `{item['name']}` (from `{item['source_case']}`):", *[f"- {issue}" for issue in item["issues"]]]
    advisory = [item for item in report["cases"] if item.get("advisories")]
    if advisory:
        lines += ["", "Advisories (not gating):"]
        lines += [f"- `{item['name']}`: {'; '.join(item['advisories'])}" for item in advisory]
    (out / "report.md").write_text("\n".join(lines) + "\n")


def scope_summary(full, include_future):
    future = [c for c in full if c["scope"] != PHASE2_SCOPE]
    return {"phase2_cases": sum(c["scope"] == PHASE2_SCOPE for c in full), "defined_cases": len(full),
            "future_terminal": [{"name": c["name"], "reason": c["scope_reason"]} for c in future],
            "future_terminal_included": include_future,
            "note": "scope correction, not a compatibility result: these Go behaviors are unverified for the candidate"}


def compare(args):
    os.umask(0o022)
    full = corpus()
    include_future = bool(getattr(args, "include_future_terminal", False))
    cases = select(full, args.case, args.group, include_future)
    binaries = {"reference": phase1.binary_identity(args.reference)}
    if args.candidate:
        binaries["candidate"] = phase1.binary_identity(args.candidate)
    if bool(args.cloud_reference) != bool(args.cloud_candidate) and args.candidate:
        raise ValueError("--cloud-reference and --cloud-candidate must be supplied together")
    if args.cloud_reference:
        binaries["cloud_reference"] = phase1.binary_identity(args.cloud_reference)
    if args.cloud_candidate:
        binaries["cloud_candidate"] = phase1.binary_identity(args.cloud_candidate)
    self_check = bool(args.candidate) and (binaries["candidate"]["sha256"] == binaries["reference"]["sha256"] or
                                           (args.cloud_candidate and binaries["cloud_candidate"]["sha256"] == binaries["cloud_reference"]["sha256"]))
    if self_check and not args.self_check:
        raise ValueError("candidate is the reference executable; pass --self-check to validate the harness itself")
    for role, identity in binaries.items():
        check_version(identity["path"])
    fixture_binary = Path(args.fixture).resolve() if args.fixture else None
    if any(c.get("fixture", {}).get("kind") == "go" for c in cases) and not (fixture_binary and fixture_binary.is_file()):
        raise ValueError("selected cases require --fixture bin/pb-fixture")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    run.write_json(out / "corpus.json", cases)
    report = {"schema_version": SCHEMA_VERSION, "mode": "compare" if args.candidate else "reference-check",
              "complete": False, "created_unix_ns": time.time_ns(), "runner_sha256": run.digest(Path(__file__).read_bytes()),
              "corpus_sha256": run.digest(run.canonical(cases)), "binaries": binaries,
              "fixture": phase1.binary_identity(fixture_binary) if fixture_binary else None,
              "self_check": self_check, "partial": bool(args.case or args.group), "reference_runs": args.reference_runs,
              "timeout_seconds": args.timeout, "scope": scope_summary(full, include_future), "cases": [], "boundaries": []}
    for index, case in enumerate(cases):
        item = {"name": case["name"], "group": case["group"], "argv": [s["argv"] for s in case["steps"]],
                "status": "pass", "issues": [], "advisories": [], "runs": {}}
        report["cases"].append(item)
        skip = requirement_skip(case, {"cloud": args.cloud_reference})
        if skip:
            item.update(status="skip", skip=skip)
            continue
        cloud = case["build"] == "cloud"
        reference = binaries["cloud_reference" if cloud else "reference"]["path"]
        folder = out / "cases" / case["name"]
        ref = sample([reference] * len(case["steps"]), case, fixture_binary, args.timeout)
        store(folder / "reference", ref)
        item["runs"]["reference"] = public(ref)
        item["issues"] += ["reference: " + issue for issue in expectation_issues(case, ref)]
        for repeat in range(1, args.reference_runs):
            again = sample([reference] * len(case["steps"]), case, fixture_binary, args.timeout)
            store(folder / f"reference-repeat-{repeat}", again)
            item["issues"] += ["reference unstable: " + issue for issue in differences(case, ref, again)]
        if args.candidate:
            candidate = binaries["cloud_candidate" if cloud else "candidate"]["path"]
            for label, plan in assignments(case, reference, candidate):
                actual = sample(plan, case, fixture_binary, args.timeout)
                store(folder / label, actual)
                item["runs"][label] = public(actual)
                item["issues"] += [f"{label}: {issue}" for issue in differences(case, ref, actual)]
                item["advisories"] += [f"{label}: {note}" for note in advisories(ref, actual)]
        if item["issues"]:
            item["status"] = "fail"
            print(f"FAIL {case['name']}: {'; '.join(item['issues'][:3])}", file=sys.stderr)
        if (index + 1) % 50 == 0:
            print(f"phase2: {index + 1}/{len(cases)} cases processed", file=sys.stderr)
    if not args.candidate:
        report["boundaries_status"] = "not applicable (reference check)"
    elif self_check:
        report["boundaries_status"] = "not applicable (self-check: Go is not expected to be a stub)"
    elif report["partial"]:
        report["boundaries_status"] = "not run (case/group filter)"
    else:
        report["boundaries_status"] = "promoted to phase7 PTY/effect gate"
        for check in deferred_boundaries(full):
            actual = sample([binaries["candidate"]["path"]], check, None, args.timeout)
            store(out / "boundaries" / check["name"], actual)
            issues = boundary_issues(check, actual)
            report["boundaries"].append({"name": check["name"], "source_case": check["source_case"],
                                         "argv": check["steps"][0]["argv"], "status": "fail" if issues else "pass",
                                         "issues": issues, "run": public(actual)})
            if issues:
                print(f"BOUNDARY FAIL {check['name']}: {'; '.join(issues[:3])}", file=sys.stderr)
    for role, identity in binaries.items():
        if phase1.binary_identity(identity["path"])["sha256"] != identity["sha256"]:
            report.setdefault("identity_issues", []).append(role + " executable changed during the run")
    report["complete"] = True
    counts = collections.Counter(item["status"] for item in report["cases"])
    report["counts"] = dict(counts)
    boundary_failures = sum(item["status"] == "fail" for item in report["boundaries"])
    # The measured subset is fixed by the full phase-two corpus, never by a filter or by results.
    suite = benchmark_suite([c for c in full if c["scope"] == PHASE2_SCOPE])
    run.write_json(out / "benchmark-suite.json", suite)
    report["benchmark_suite_sha256"] = run.digest(run.canonical(suite))
    write_report(out, report)
    excluded = "" if include_future else f"; {len(report['scope']['future_terminal'])} future terminal cases excluded"
    print(f"phase2 {report['mode']}: {counts['pass']} passed, {counts['fail']} failed, {counts['skip']} skipped{excluded}; "
          f"boundaries {len(report['boundaries']) - boundary_failures}/{len(report['boundaries'])} ({report['boundaries_status']}); "
          f"report: {out / 'report.md'}")
    if counts["fail"] or boundary_failures or report.get("identity_issues"):
        return 1
    if counts["skip"] and not args.allow_skips:
        print("phase2: skipped cases make this an incomplete gate (use --allow-skips to accept)", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="list the fixed phase-two corpus")
    listing.add_argument("--json", action="store_true")
    for name in ("compare", "check-reference"):
        command = sub.add_parser(name, help="Go/Rust differential gate" if name == "compare" else "validate expectations against Go only")
        command.add_argument("--reference", required=True, help="pinned Go executable (bin/pb-go)")
        if name == "compare":
            command.add_argument("--candidate", required=True)
            command.add_argument("--cloud-candidate", help="candidate built with the harness orchestrator token")
            command.add_argument("--self-check", action="store_true", help="allow candidate == reference (harness validation only)")
        command.add_argument("--cloud-reference", help="Go built with -X ...CloudOrchestratorAuthToken=" + ORCHESTRATOR_TOKEN)
        command.add_argument("--fixture", default=str(HERE.parent / "bin" / "pb-fixture"))
        command.add_argument("--out", required=True, help="new artifact directory")
        command.add_argument("--case", action="append")
        command.add_argument("--group", action="append")
        command.add_argument("--timeout", type=float, default=10)
        command.add_argument("--reference-runs", type=int, default=2, help="reference repetitions checked for stability")
        command.add_argument("--allow-skips", action="store_true", help="exit 0 even if requirement-gated cases were skipped")
        command.add_argument("--include-future-terminal", action="store_true",
                             help="also run the Go-defined interactive logout/login cases (outside the phase-two gate)")
    exporter = sub.add_parser("suite", help="export the fixed benchmark subset for run.py --suite")
    exporter.add_argument("--out", required=True)
    server = sub.add_parser("serve", help=argparse.SUPPRESS)
    server.add_argument("--scenario", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            serve(args.scenario)
            return 0
        if args.command == "list":
            cases = corpus()
            if args.json:
                print(json.dumps(cases, indent=2, ensure_ascii=False))
            else:
                for case in cases:
                    print(case["name"], case["group"], case["build"], case["scope"],
                          json.dumps([s["argv"] for s in case["steps"]], ensure_ascii=False))
                print(f"{len(cases)} defined cases; scopes: {dict(collections.Counter(c['scope'] for c in cases))}; "
                      f"groups: {dict(collections.Counter(c['group'] for c in cases))}")
            return 0
        if args.command == "suite":
            with Path(args.out).open("x") as handle:
                json.dump(benchmark_suite(corpus()), handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            return 0
        if args.command == "check-reference":
            args.candidate = args.cloud_candidate = None
            args.self_check = False
        if not 0 < args.timeout <= 60 or args.reference_runs < 1:
            raise ValueError("timeout must be in (0, 60] and --reference-runs at least 1")
        return compare(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print("phase2 setup error: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
