#!/usr/bin/env python3
"""Before/after resource benchmark for `pb ingest files`. Python 3.11+, Linux/macOS.

Each round runs a prebuilt baseline and candidate binary once per workload, in a
seeded random order. Every invocation gets a fresh HOME/config/state sandbox and
a fresh stdlib HTTP ingest fixture running in a separate process. Resources come
from the existing native pb-meter supervisor through run.measure: wall time is
the meter's fork-to-waitid interval, and CPU/peak RSS are the CLI child's own
wait4 rusage, never Python's cumulative RUSAGE_CHILDREN.

Behavior is compared after normalizing only nondeterministic values: the
collector UUID, per-file generation hashes and dev:inode identities, collection
timestamps, the sandbox root and the fixture endpoint. Everything else - request
partitioning, headers, wire bytes, stdout/stderr, exit codes and the checkpoint
file - must match between binaries and stay stable across rounds.

Unmeasured follow-up phases preserve checkpoint/durability coverage: a no-op
resume, append-then-complete, idempotent re-completion, a 503 retry that must
replay the exact durable outbox, and HTTP 413 request splitting.

    python3 harness/ingest_perf.py run --baseline OLD/pb --candidate NEW/pb \
        --meter bin/pb-meter --out /tmp/ingest-perf --repetitions 7 --warmup 1

The report (OUT/report.json) holds every sample, medians, paired ratios and a
compatibility gate. Exit status: 0 when every gate passes, 1 otherwise.
"""

import argparse
import csv
from datetime import datetime
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import random
import re
import selectors
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zlib

import run as harness


SCHEMA = "pb-ingest-perf/1"
DATASET = "ingest_perf"
HOST_LABEL = "ingest-perf-host"
INGEST_PATH = "/api/v1/ingest"
CONTROL_PATH = "/__harness/phase"
FORMATS = {"jsonl": "jsonl", "text": "log"}
LEVELS = ("debug", "info", "warn", "error")
# Exercises JSON escaping and multi-byte UTF-8 in every record.
FANCY = 'quote" back\\ tab\t café ✓ '
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789" * 128
# Lowercased request headers that legitimately vary per run or follow the body.
VOLATILE_HEADERS = {"host", "content-length"}
RESOURCE_METRICS = ("wall_ms", "cpu_ms", "cpu_user_ms", "cpu_system_ms", "peak_rss_bytes",
                    "minor_faults", "major_faults", "voluntary_context_switches",
                    "involuntary_context_switches", "block_input_operations",
                    "block_output_operations", "proc_io_syscr", "proc_io_syscw", "proc_io_rchar",
                    "proc_io_wchar", "proc_io_read_bytes", "proc_io_write_bytes")
DEFAULT_GATE_METRICS = ("cpu_ms", "peak_rss_bytes", "wall_ms")
DEFAULT_BATCH_SIZE = 1000
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")
COLLECTED_AT = re.compile(r'("pb_collected_at"\s*:\s*")([^"]*)(")')
GENERATION = re.compile(r"file-[0-9a-f]{16}")


# --------------------------------------------------------------------------
# Fixed corpus

def jsonl_line(index, size):
    value = {"i": index, "level": LEVELS[index % 4], "service": f"svc-{index % 17}",
             "ok": index % 3 == 0, "latency_ms": (index * 37 % 10007) / 8, "delta": -(index % 9),
             "tags": [f"t{index % 5}", f"t{index % 7}"],
             "nested": {"a": index % 11, "b": None, "c": [index % 5, "x", True]}, "msg": FANCY}
    base = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    pad = size - 1 - base
    if pad < 0:
        raise ValueError(f"--record-bytes must be at least {base + 1}")
    value["msg"] = FANCY + ALPHABET[index % 26:index % 26 + pad]
    line = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    assert len(line) == size, (len(line), size)
    return line


def text_line(index, size):
    base = f"{index:08d} {LEVELS[index % 4]} svc-{index % 17} {FANCY}".encode()
    pad = size - 1 - len(base)
    if pad < 0:
        raise ValueError(f"--record-bytes must be at least {len(base) + 1}")
    return base + ALPHABET[index % 26:index % 26 + pad].encode() + b"\n"


def build_corpus(fmt, records, record_bytes, files, tail_records):
    """Deterministic contiguous chunks across files; tail lines append to file 0."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown workload format {fmt!r}")
    if records < 1 or files < 1 or files > records:
        raise ValueError("need 1 <= files <= records")
    if record_bytes > len(ALPHABET) - 26:
        raise ValueError("--record-bytes is too large for the padding alphabet")
    make = jsonl_line if fmt == "jsonl" else text_line
    names = [f"part-{index:02d}.{FORMATS[fmt]}" for index in range(files)]
    lines = {name: [] for name in names}
    for index in range(records):
        lines[names[index * files // records]].append(make(index, record_bytes))
    tail = [make(index, record_bytes) for index in range(records, records + tail_records)]
    data = {name: b"".join(chunk) for name, chunk in lines.items()}
    return {"format": fmt, "glob": f"logs/*.{FORMATS[fmt]}", "names": names, "lines": lines,
            "data": data, "tail": tail, "tail_file": names[0], "records": records,
            "record_bytes": record_bytes,
            "sha256": {name: harness.digest(value) for name, value in data.items()},
            "tail_sha256": harness.digest(b"".join(tail))}


def corpus_metadata(corpus):
    return {key: corpus[key] for key in ("format", "glob", "names", "records", "record_bytes",
                                         "sha256", "tail_sha256", "tail_file")} | {
        "tail_records": len(corpus["tail"]),
        "bytes": sum(len(value) for value in corpus["data"].values())}


def expected_payload(fmt, line):
    text = line.decode("utf-8")
    text = text[:-1] if text.endswith("\n") else text
    text = text[:-1] if text.endswith("\r") else text
    return json.loads(text) if fmt == "jsonl" else {"message": text}


# --------------------------------------------------------------------------
# HTTP ingest fixture (runs in its own process: `ingest_perf.py serve`)

def decode_body(body, headers):
    encoding = next((value for name, value in headers if name == "content-encoding"), "").strip().lower()
    if encoding in ("", "identity"):
        return body
    if encoding == "gzip":
        return gzip.decompress(body)
    if encoding == "deflate":
        return zlib.decompress(body)
    raise ValueError(f"unsupported content-encoding {encoding!r}")


class RecordingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, record_root):
        super().__init__(address, RecordingHandler)
        self.lock = threading.Lock()
        self.record_root = Path(record_root)
        self.configure({"name": "default"})

    def configure(self, phase):
        name = phase.get("name", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("invalid phase name")
        directory = self.record_root / name
        directory.mkdir(parents=True, exist_ok=False)
        with self.lock:
            self.directory = directory
            self.count = 0
            self.ingest_count = 0
            self.fail_upload = phase.get("fail_upload")
            self.reject_over_rows = phase.get("reject_over_rows")


class RecordingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pb-ingest-perf-fixture"
    sys_version = ""

    def log_message(self, *_):
        pass

    def read_body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            chunks = []
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip(), 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return b"".join(chunks)
                chunks.append(self.rfile.read(size))
                self.rfile.readline()
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def respond(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def handle_any(self):
        body = self.read_body()
        path = urllib.parse.urlsplit(self.path).path
        if path == CONTROL_PATH and self.command == "POST":
            try:
                self.server.configure(json.loads(body))
            except (ValueError, OSError) as error:
                return self.respond(400, json.dumps({"error": str(error)}).encode())
            return self.respond(200, b"{}")
        headers = [[name.lower(), value] for name, value in self.headers.items()]
        server = self.server
        with server.lock:
            server.count += 1
            index, directory = server.count, server.directory
            ingest = self.command == "POST" and path == INGEST_PATH
            if ingest:
                server.ingest_count += 1
            ingest_index = server.ingest_count if ingest else None
            fail_upload, reject_over_rows = server.fail_upload, server.reject_over_rows
        status, response = 404, b'{"error":"unexpected fixture request"}'
        if ingest:
            status, response = 200, b"{}"
            if fail_upload is not None and ingest_index == fail_upload:
                status, response = 503, b"injected fixture failure"
            elif reject_over_rows is not None:
                try:
                    rows = len(json.loads(decode_body(body, headers)))
                except (ValueError, TypeError, OSError, EOFError, zlib.error):
                    rows = None
                if rows is not None and rows > reject_over_rows:
                    status, response = 413, b"fixture batch too large"
        meta = {"index": index, "ingest_index": ingest_index, "method": self.command,
                "target": self.path, "request_version": self.request_version, "headers": headers,
                "body_bytes": len(body), "status": status, "received_unix_ns": time.time_ns()}
        # Record before responding: the CLI cannot exit before its request is on disk.
        (directory / f"{index:06d}.body").write_bytes(body)
        (directory / f"{index:06d}.json").write_text(json.dumps(meta))
        self.respond(status, response)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = handle_any


def serve(args):
    server = RecordingServer(("127.0.0.1", 0), args.record_root)
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_address[1]}"}), flush=True)
    server.serve_forever(poll_interval=0.05)


class Fixture:
    """One fixture process per sandbox: the checkpoint binds the endpoint URL."""

    def __init__(self, root):
        self.record_root = root / "requests"
        self.log = (root / "fixture.stderr").open("wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve", "--record-root", str(self.record_root)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=self.log, start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise RuntimeError("ingest fixture did not become ready within 10s")
            self.url = json.loads(self.proc.stdout.readline())["url"]
        except Exception as error:
            self.close()
            raise RuntimeError(f"ingest fixture startup failed: {(root / 'fixture.stderr').read_text(errors='replace')[:4096] or error}") from error
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def phase(self, name, fail_upload=None, reject_over_rows=None):
        body = json.dumps({"name": name, "fail_upload": fail_upload, "reject_over_rows": reject_over_rows}).encode()
        request = urllib.request.Request(self.url + CONTROL_PATH, data=body, method="POST")
        with self.opener.open(request, timeout=5) as response:
            response.read()
        return self.record_root / name

    def close(self):
        if self.proc is not None:
            harness.kill_group(self.proc, signal.SIGKILL)
            self.proc.wait()
            self.proc.stdout.close()
            self.proc = None
        self.log.close()


def load_requests(directory):
    requests = []
    for meta_path in sorted(directory.glob("*.json")):
        meta = json.loads(meta_path.read_text())
        meta["body"] = (directory / f"{meta['index']:06d}.body").read_bytes()
        requests.append(meta)
    return requests


# --------------------------------------------------------------------------
# Sandboxed invocations

class Sandbox:
    def __init__(self, parent, name):
        self.root = (parent / name)
        self.root.mkdir(parents=True)
        self.root = self.root.resolve()
        (self.root / "tmp").mkdir()
        (self.root / "cache").mkdir()
        self.work = self.root / "work"
        (self.work / "logs").mkdir(parents=True)
        (self.work / "state").mkdir()
        self.fixture = Fixture(self.root)
        try:
            harness.seed_config(self.root, self.fixture.url, "basic")
        except BaseException:
            self.fixture.close()
            raise
        self.env = harness.isolated_env(self.root)
        # The checkpoint persists the host label; keep it identical across binaries.
        self.env["HOSTNAME"] = HOST_LABEL

    def install(self, corpus):
        for name, data in corpus["data"].items():
            (self.work / "logs" / name).write_bytes(data)

    def append_tail(self, corpus):
        with (self.work / "logs" / corpus["tail_file"]).open("ab") as handle:
            handle.write(b"".join(corpus["tail"]))

    def close(self, keep):
        self.fixture.close()
        if not keep:
            shutil.rmtree(self.root, ignore_errors=True)


def ingest_args(corpus, session, batch_size, complete=False):
    args = ["ingest", "files", corpus["glob"], "--dataset", DATASET, "--session", session,
            "--state-file", "state/ingest.json", "--format", corpus["format"], "--from", "beginning",
            "-o", "json"]
    if batch_size is not None:
        args += ["--batch-size", str(batch_size)]
    if complete:
        args.append("--complete")
    return args


def invoke(binary, args, sandbox, timeout):
    """Unmeasured invocation for checkpoint/durability phases."""
    started = time.time_ns()
    proc = subprocess.Popen([binary, *args], cwd=sandbox.work, env=sandbox.env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        failure = None
    except subprocess.TimeoutExpired:
        harness.kill_group(proc, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        failure = "timeout"
    harness.kill_group(proc, signal.SIGKILL)
    return {"exit_code": proc.returncode, "stdout": stdout, "stderr": stderr, "failure": failure,
            "started_unix_ns": started, "ended_unix_ns": time.time_ns()}


def snapshot_state(sandbox):
    state_dir = sandbox.work / "state"
    path = state_dir / "ingest.json"
    snapshot = {"entries": sorted(item.name for item in state_dir.iterdir())}
    try:
        raw = path.read_bytes()
        snapshot["mode"] = oct(path.stat().st_mode & 0o777)
        snapshot["state"] = json.loads(raw)
    except (OSError, ValueError) as error:
        snapshot["error"] = str(error)
    lock = state_dir / "ingest.json.lock"
    snapshot["lock_mode"] = oct(lock.stat().st_mode & 0o777) if lock.exists() else None
    snapshot["sources"] = {}
    for item in sorted((sandbox.work / "logs").iterdir()):
        info = item.stat()
        snapshot["sources"][str(item)] = {"size": info.st_size, "identity": f"{info.st_dev}:{info.st_ino}"}
    return snapshot


def phase_record(sandbox, directory, result, before=None):
    return {"result": result, "requests": load_requests(directory), "state": snapshot_state(sandbox),
            "state_before": before}


# --------------------------------------------------------------------------
# Normalization

class Normalizer:
    """Replace per-run values with labels derived from stable structure."""

    def __init__(self, sandbox, final_state):
        self.replacements = {str(sandbox.root): "<root>", sandbox.fixture.url: "<endpoint>"}
        state = final_state.get("state") or {}
        self.collector = state.get("collector")
        self.generations, self.identities = {}, {}
        for path, entry in ((state.get("files") or {}).get("files") or {}).items():
            relative = self.text(path)
            if isinstance(entry, dict):
                if "generation" in entry:
                    self.generations[entry["generation"]] = f"<generation:{relative}#{entry.get('generation_number')}>"
                source = final_state["sources"].get(path)
                if source is not None and entry.get("identity") == source["identity"]:
                    self.identities[entry["identity"]] = f"<identity:{relative}>"

    def text(self, value):
        for original, replacement in sorted(self.replacements.items(), key=lambda item: -len(item[0])):
            value = value.replace(original, replacement)
        if self.collector:
            value = value.replace(self.collector, "<collector>")
        value = GENERATION.sub(lambda match: self.generations.get(match.group(0), "<generation:unknown>"), value)
        return value

    def value(self, value):
        if isinstance(value, str):
            if value in self.identities:
                return self.identities[value]
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {self.text(key): self.value(item) for key, item in value.items()}
        return value

    def rows(self, rows):
        """Timestamps become request-local ordinals; preserved reuse stays visible."""
        ordinals, normalized = {}, []
        for row in rows:
            row = dict(row) if isinstance(row, dict) else row
            if isinstance(row, dict) and isinstance(row.get("pb_collected_at"), str):
                row["pb_collected_at"] = f"<collected_at-{ordinals.setdefault(row['pb_collected_at'], len(ordinals))}>"
            normalized.append(self.value(row))
        return normalized

    def wire(self, body):
        ordinals = {}
        text = body.decode("utf-8", errors="surrogateescape")
        text = COLLECTED_AT.sub(lambda match: f"{match.group(1)}<collected_at-{ordinals.setdefault(match.group(2), len(ordinals))}>{match.group(3)}", text)
        return self.text(text).encode("utf-8", errors="surrogateescape")

    def state(self, state):
        state = json.loads(json.dumps(state))
        pending = state.get("pending")
        if isinstance(pending, dict) and isinstance(pending.get("rows"), list):
            pending["rows"] = self.rows(pending["rows"])
        return self.value(state)


def parse_rows(request):
    try:
        rows = json.loads(decode_body(request["body"], request["headers"]))
    except (ValueError, OSError, EOFError, zlib.error) as error:
        return None, f"request {request['index']} body is not decodable JSON: {error}"
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None, f"request {request['index']} body is not a JSON array of objects"
    return rows, None


def observe_phase(record, normalizer):
    result = record["result"]
    requests = []
    for request in record["requests"]:
        rows, _ = parse_rows(request)
        headers = sorted([name, normalizer.text(value)] for name, value in request["headers"]
                         if name not in VOLATILE_HEADERS)
        content_encoding = next((value for name, value in request["headers"] if name == "content-encoding"), None)
        try:
            wire = normalizer.wire(decode_body(request["body"], request["headers"]))
        except (ValueError, OSError, EOFError, zlib.error):
            wire = request["body"]
        requests.append({"method": request["method"], "target": request["target"],
                         "request_version": request["request_version"], "status": request["status"],
                         "headers": headers, "content_encoding": content_encoding,
                         "row_count": len(rows) if rows is not None else None,
                         "rows_sha256": harness.digest(harness.canonical(normalizer.rows(rows))) if rows is not None else None,
                         "wire_sha256": harness.digest(wire)})
    state = record["state"]
    return {"exit_code": result["exit_code"], "failure": result.get("failure"),
            "stdout": normalizer.text(result["stdout"].decode("utf-8", errors="replace")),
            "stderr": normalizer.text(result["stderr"].decode("utf-8", errors="replace")),
            "requests": requests,
            "state": normalizer.state(state["state"]) if "state" in state else {"error": state.get("error")},
            "state_mode": state.get("mode"), "lock_mode": state.get("lock_mode"),
            "state_entries": state["entries"]}


def logical(observation, wire_bytes):
    """Projection compared when wire-byte identity is explicitly relaxed."""
    if wire_bytes:
        return observation
    value = json.loads(json.dumps(observation))
    for phase in value.values():
        for request in phase.get("requests", []):
            request.pop("wire_sha256", None)
    return value


def diff_paths(left, right, path="$", limit=20, out=None):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if type(left) is not type(right):
        out.append({"path": path, "baseline": left, "candidate": right})
    elif isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                out.append({"path": f"{path}.{key}", "baseline": left.get(key, "<missing>"),
                            "candidate": right.get(key, "<missing>")})
            else:
                diff_paths(left[key], right[key], f"{path}.{key}", limit, out)
            if len(out) >= limit:
                break
    elif isinstance(left, list):
        if len(left) != len(right):
            out.append({"path": f"{path}.length", "baseline": len(left), "candidate": len(right)})
        for index, (a, b) in enumerate(zip(left, right)):
            diff_paths(a, b, f"{path}[{index}]", limit, out)
            if len(out) >= limit:
                break
    elif left != right:
        out.append({"path": path, "baseline": left, "candidate": right})
    return out


# --------------------------------------------------------------------------
# Absolute expectations (independent of the other binary)

class Checks:
    def __init__(self):
        self.failures = []

    def expect(self, condition, message):
        if not condition:
            self.failures.append(message)
        return bool(condition)


def stdout_json(checks, label, result):
    try:
        return json.loads(result["stdout"])
    except ValueError:
        checks.expect(False, f"{label}: stdout is not JSON: {result['stdout'][:200]!r}")
        return None


def all_rows(checks, label, requests, accepted_only=True):
    rows = []
    for request in requests:
        checks.expect(request["method"] == "POST" and urllib.parse.urlsplit(request["target"]).path == INGEST_PATH,
                      f"{label}: unexpected request {request['method']} {request['target']}")
        stream = next((value for name, value in request["headers"] if name == "x-p-stream"), None)
        checks.expect(stream == DATASET, f"{label}: request {request['index']} X-P-Stream={stream!r}")
        parsed, error = parse_rows(request)
        if not checks.expect(error is None, f"{label}: {error}"):
            continue
        if not accepted_only or 200 <= request["status"] < 300:
            rows.extend(parsed)
    return rows


def check_collected_at(checks, label, rows, started_ns, ended_ns):
    for row in rows:
        value = row.get("pb_collected_at")
        if not checks.expect(isinstance(value, str) and TIMESTAMP.fullmatch(value), f"{label}: bad pb_collected_at {value!r}"):
            return
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1e9
        if not checks.expect(started_ns - 5e9 <= moment <= ended_ns + 5e9, f"{label}: pb_collected_at {value} outside the run"):
            return


def check_events(checks, label, rows, corpus, sandbox, collector, first_seq, session, source_lines=None):
    """Every row is the next sequence and the exact payload at its source position."""
    lines = source_lines or corpus["lines"]
    root = str(sandbox.work / "logs")
    for offset, row in enumerate(rows):
        where = f"{label}: row {offset}"
        if not checks.expect(row.get("pb_seq") == first_seq + offset, f"{where}: pb_seq {row.get('pb_seq')} != {first_seq + offset}"):
            return
        checks.expect(row.get("pb_kind") == "event", f"{where}: pb_kind {row.get('pb_kind')!r}")
        checks.expect(row.get("pb_session") == session, f"{where}: pb_session {row.get('pb_session')!r}")
        checks.expect(row.get("pb_collector") == collector, f"{where}: pb_collector differs from checkpoint")
        checks.expect(row.get("pb_host") == HOST_LABEL, f"{where}: pb_host {row.get('pb_host')!r}")
        source, source_seq = row.get("pb_source", ""), row.get("pb_source_seq")
        name = source[len(root) + 1:] if source.startswith(root + "/") else None
        if not checks.expect(name in lines and isinstance(source_seq, int) and 1 <= source_seq <= len(lines[name]),
                             f"{where}: unknown source position {source!r}#{source_seq}"):
            return
        checks.expect(row.get("pb_event_id") == f"{collector}:{row.get('pb_generation')}-{source_seq}",
                      f"{where}: pb_event_id {row.get('pb_event_id')!r}")
        try:
            payload = json.loads(row.get("pb_payload"))
        except (TypeError, ValueError):
            payload = None
        if not checks.expect(payload == expected_payload(corpus["format"], lines[name][source_seq - 1]),
                             f"{where}: payload differs from {name} line {source_seq}"):
            return


def check_source_order(checks, label, rows, sandbox, counts):
    """Each source line exactly once and in per-file order."""
    root = str(sandbox.work / "logs")
    seen = {}
    for row in rows:
        name = row.get("pb_source", "")[len(root) + 1:]
        seen.setdefault(name, []).append(row.get("pb_source_seq"))
    for name, count in counts.items():
        checks.expect(seen.get(name, []) == list(range(1, count + 1)), f"{label}: {name} lines not exactly once in order")
    checks.expect(set(seen) <= set(counts), f"{label}: rows from unexpected sources {sorted(set(seen) - set(counts))}")


def check_state(checks, label, sandbox, snapshot, corpus, next_sequence, complete, line_counts, pending=False):
    state = snapshot.get("state")
    if not checks.expect(isinstance(state, dict), f"{label}: checkpoint unreadable: {snapshot.get('error')}"):
        return
    checks.expect(snapshot.get("mode") == "0o600", f"{label}: checkpoint mode {snapshot.get('mode')}")
    checks.expect(snapshot.get("lock_mode") == "0o600", f"{label}: lock sidecar mode {snapshot.get('lock_mode')}")
    checks.expect(not any(name.startswith(".pb-state-") for name in snapshot["entries"]),
                  f"{label}: abandoned temporary checkpoint files {snapshot['entries']}")
    checks.expect(state.get("version") == 1, f"{label}: checkpoint version {state.get('version')}")
    checks.expect(state.get("host") == HOST_LABEL, f"{label}: checkpoint host {state.get('host')!r}")
    checks.expect(state.get("next_sequence") == next_sequence, f"{label}: next_sequence {state.get('next_sequence')} != {next_sequence}")
    checks.expect(state.get("complete") is complete, f"{label}: complete {state.get('complete')!r} != {complete}")
    checks.expect((state.get("pending") is not None) is pending, f"{label}: pending outbox presence != {pending}")
    binding = state.get("binding") or {}
    checks.expect(binding.get("directory") == str(sandbox.work) and binding.get("dataset") == DATASET
                  and binding.get("includes") == [corpus["glob"]] and binding.get("format") == corpus["format"],
                  f"{label}: checkpoint binding {binding}")
    files = (state.get("files") or {}).get("files") or {}
    for name, count in line_counts.items():
        path = str(sandbox.work / "logs" / name)
        entry, source = files.get(path), snapshot["sources"].get(path)
        if not checks.expect(isinstance(entry, dict) and source is not None, f"{label}: no checkpoint for {name}"):
            continue
        offset = sum(len(line) for line in (corpus["lines"][name] + (corpus["tail"] if name == corpus["tail_file"] else []))[:count])
        checks.expect(entry.get("offset") == offset, f"{label}: {name} offset {entry.get('offset')} != {offset}")
        checks.expect(entry.get("sequence") == count, f"{label}: {name} sequence {entry.get('sequence')} != {count}")
        checks.expect(entry.get("identity") == source["identity"], f"{label}: {name} identity is not the file's dev:inode")


def progress(snapshot):
    """Durable progress only; byte-identity is covered by the cross-binary comparison."""
    state = snapshot.get("state") or {}
    files = (state.get("files") or {}).get("files") or {}
    return {"collector": state.get("collector"), "next_sequence": state.get("next_sequence"),
            "complete": state.get("complete"), "pending": state.get("pending"),
            "files": {path: {key: entry.get(key) for key in ("identity", "generation", "offset", "sequence")}
                      for path, entry in files.items() if isinstance(entry, dict)}}


def expect_report(checks, label, result, ingested, complete):
    checks.expect(result["failure"] is None and result["exit_code"] == 0,
                  f"{label}: exit {result['exit_code']} failure {result['failure']} stderr {result['stderr'][:300]!r}")
    checks.expect(result["stderr"] == b"", f"{label}: unexpected stderr {result['stderr'][:300]!r}")
    checks.expect(stdout_json(checks, label, result) == {"ingested": ingested, "skipped": 0, "session_complete": complete},
                  f"{label}: stdout {result['stdout'][:200]!r}")


def check_batches(checks, label, requests, batch_size):
    for request in requests:
        rows, _ = parse_rows(request)
        if rows is not None:
            checks.expect(0 < len(rows) <= batch_size, f"{label}: request {request['index']} has {len(rows)} rows")


# --------------------------------------------------------------------------
# Workload and probe execution

def workload_sample(binary, corpus, sandbox, session, args, meter):
    """Measured drain, then unmeasured resume/append/complete checkpoint phases."""
    batch_size = args.batch_size or DEFAULT_BATCH_SIZE
    records, tail = corpus["records"], len(corpus["tail"])
    sandbox.install(corpus)
    directory = sandbox.fixture.phase("drain")
    measured, stdout, stderr = harness.measure([binary, *ingest_args(corpus, session, args.batch_size)],
                                               sandbox.env, sandbox.work, timeout=args.timeout, meter=meter)
    started, ended = measured["started_unix_ns"], measured["started_unix_ns"] + measured["wall_ms"] * 1e6
    phases = {"drain": phase_record(sandbox, directory, {"exit_code": measured["exit_code"], "stdout": stdout,
                                                         "stderr": stderr, "failure": measured["failure"]})}
    directory = sandbox.fixture.phase("resume")
    phases["resume"] = phase_record(sandbox, directory, invoke(binary, ingest_args(corpus, session, args.batch_size), sandbox, args.timeout))
    sandbox.append_tail(corpus)
    directory = sandbox.fixture.phase("append_complete")
    phases["append_complete"] = phase_record(sandbox, directory, invoke(binary, ingest_args(corpus, session, args.batch_size, True), sandbox, args.timeout))
    directory = sandbox.fixture.phase("complete_again")
    phases["complete_again"] = phase_record(sandbox, directory, invoke(binary, ingest_args(corpus, session, args.batch_size, True), sandbox, args.timeout))

    checks = Checks()
    drain_counts = {name: len(lines) for name, lines in corpus["lines"].items()}
    final_counts = dict(drain_counts)
    final_counts[corpus["tail_file"]] += tail
    drain = phases["drain"]
    collector = (drain["state"].get("state") or {}).get("collector")
    checks.expect(measured.get("resource_source") == "native_child_wait4", f"drain: resource source {measured.get('resource_source')}")
    expect_report(checks, "drain", drain["result"], records, False)
    rows = all_rows(checks, "drain", drain["requests"])
    checks.expect(all(200 <= request["status"] < 300 for request in drain["requests"]), "drain: fixture rejected a request")
    checks.expect(len(rows) == records, f"drain: uploaded {len(rows)} rows, expected {records}")
    check_batches(checks, "drain", drain["requests"], batch_size)
    check_events(checks, "drain", rows, corpus, sandbox, collector, 1, session)
    check_source_order(checks, "drain", rows, sandbox, drain_counts)
    check_collected_at(checks, "drain", rows, started, ended)
    check_state(checks, "drain", sandbox, drain["state"], corpus, records + 1, False, drain_counts)

    resume = phases["resume"]
    expect_report(checks, "resume", resume["result"], 0, False)
    checks.expect(not resume["requests"], f"resume: {len(resume['requests'])} unexpected requests")
    checks.expect(progress(resume["state"]) == progress(drain["state"]), "resume: checkpoint progress changed without new data")

    finish = phases["append_complete"]
    expect_report(checks, "append_complete", finish["result"], tail, True)
    rows = all_rows(checks, "append_complete", finish["requests"])
    tail_lines = {name: list(lines) for name, lines in corpus["lines"].items()}
    tail_lines[corpus["tail_file"]] += corpus["tail"]
    if checks.expect(len(rows) == tail + 1, f"append_complete: uploaded {len(rows)} rows, expected {tail + 1}"):
        check_events(checks, "append_complete", rows[:-1], corpus, sandbox, collector, records + 1, session, tail_lines)
        marker = rows[-1]
        checks.expect(marker.get("pb_kind") == "complete" and marker.get("pb_seq") == records + tail + 1
                      and marker.get("pb_event_id") == f"{collector}:complete" and marker.get("pb_source") == ""
                      and marker.get("pb_payload") == "{}", f"append_complete: completion marker {marker}")
    check_state(checks, "append_complete", sandbox, finish["state"], corpus, records + tail + 2, True, final_counts)

    again = phases["complete_again"]
    expect_report(checks, "complete_again", again["result"], 0, True)
    checks.expect(not again["requests"], f"complete_again: {len(again['requests'])} unexpected requests")
    checks.expect(progress(again["state"]) == progress(finish["state"]), "complete_again: checkpoint progress changed")

    normalizer = Normalizer(sandbox, finish["state"])
    observation = {name: observe_phase(record, normalizer) for name, record in phases.items()}
    sample = {key: measured.get(key) for key in RESOURCE_METRICS}
    sample.update({key: measured.get(key) for key in ("exit_code", "termination", "failure", "resource_source",
                                                       "proc_io_available", "supervisor_startup_ms", "started_unix_ns")})
    sample["requests"] = len(drain["requests"])
    sample["request_body_bytes"] = sum(request["body_bytes"] for request in drain["requests"])
    return sample, observation, checks.failures


def probe_sample(binary, corpus, sandbox, session, args, kind):
    """Unmeasured durability probes on the small corpus."""
    batch_size = args.batch_size or DEFAULT_BATCH_SIZE
    records = corpus["records"]
    counts = {name: len(lines) for name, lines in corpus["lines"].items()}
    sandbox.install(corpus)
    checks, phases = Checks(), {}
    command = ingest_args(corpus, session, args.batch_size)
    if kind == "retry_503":
        fail_at = args.probe_fail_upload
        directory = sandbox.fixture.phase("failed", fail_upload=fail_at)
        phases["failed"] = phase_record(sandbox, directory, invoke(binary, command, sandbox, args.timeout))
        directory = sandbox.fixture.phase("replay")
        phases["replay"] = phase_record(sandbox, directory, invoke(binary, command, sandbox, args.timeout))
        failed, replay = phases["failed"], phases["replay"]
        result = failed["result"]
        checks.expect(result["failure"] is None and result["exit_code"] not in (0, None), f"failed: exit {result['exit_code']}")
        checks.expect(result["stdout"] == b"" and result["stderr"] != b"", "failed: expected only a stderr diagnostic")
        checks.expect(len(failed["requests"]) == fail_at and failed["requests"][-1]["status"] == 503,
                      f"failed: {len(failed['requests'])} requests; expected the 503 at upload {fail_at}")
        state = failed["state"].get("state") or {}
        collector = state.get("collector")
        failed_rows, _ = parse_rows(failed["requests"][-1]) if failed["requests"] else (None, None)
        pending = state.get("pending") or {}
        checks.expect(failed_rows is not None and pending.get("rows") == failed_rows,
                      "failed: durable outbox does not hold the rejected request rows")
        accepted = all_rows(checks, "failed", failed["requests"])
        checks.expect(state.get("next_sequence") == len(accepted) + 1, "failed: committed sequence includes the rejected batch")
        checks.expect(bool(replay["requests"]) and failed["requests"]
                      and replay["requests"][0]["body"] == failed["requests"][-1]["body"],
                      "replay: first request is not a byte-identical replay of the durable outbox")
        expect_report(checks, "replay", replay["result"], records - len(accepted), False)
        rows = accepted + all_rows(checks, "replay", replay["requests"])
        checks.expect(len(rows) == records, f"replay: accepted {len(rows)} rows, expected {records}")
        check_events(checks, "replay", rows, corpus, sandbox, collector, 1, session)
        check_source_order(checks, "replay", rows, sandbox, counts)
        check_state(checks, "replay", sandbox, replay["state"], corpus, records + 1, False, counts)
        final = replay["state"]
    elif kind == "split_413":
        directory = sandbox.fixture.phase("split", reject_over_rows=args.probe_reject_over_rows)
        phases["split"] = phase_record(sandbox, directory, invoke(binary, command, sandbox, args.timeout))
        split = phases["split"]
        state = split["state"].get("state") or {}
        collector = state.get("collector")
        expect_report(checks, "split", split["result"], records, False)
        checks.expect(any(request["status"] == 413 for request in split["requests"]), "split: fixture never returned 413")
        rows = all_rows(checks, "split", split["requests"])
        checks.expect(len(rows) == records, f"split: accepted {len(rows)} rows, expected {records}")
        checks.expect(all(len(parse_rows(r)[0] or []) <= args.probe_reject_over_rows for r in split["requests"] if r["status"] == 200),
                      "split: accepted an oversized request")
        rejected = all_rows(checks, "split", [r for r in split["requests"] if r["status"] == 413], accepted_only=False)
        accepted_ids = [row.get("pb_event_id") for row in rows]
        checks.expect(len(set(accepted_ids)) == len(accepted_ids), "split: duplicate accepted event identities")
        checks.expect({row.get("pb_event_id") for row in rejected} <= set(accepted_ids),
                      "split: a rejected event identity was never accepted unchanged")
        check_events(checks, "split", rows, corpus, sandbox, collector, 1, session)
        check_source_order(checks, "split", rows, sandbox, counts)
        check_state(checks, "split", sandbox, split["state"], corpus, records + 1, False, counts)
        final = split["state"]
    else:
        raise ValueError(kind)
    normalizer = Normalizer(sandbox, final)
    return {name: observe_phase(record, normalizer) for name, record in phases.items()}, checks.failures


# --------------------------------------------------------------------------
# Statistics and gates

def describe(values):
    if not values:
        return None
    median = statistics.median(values)
    return {"n": len(values), "median": median, "mean": statistics.mean(values), "min": min(values),
            "max": max(values), "p90": harness.percentile(values, .9),
            "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
            "mad": statistics.median(abs(value - median) for value in values)}


def compare_metric(base, cand, metric, seed, resamples=2000):
    """Median ratio (candidate/baseline), round-paired ratios and bootstrap CIs."""
    pairs = [(b[metric], c[metric]) for b, c in zip(base, cand)
             if b.get(metric) is not None and c.get(metric) is not None]
    base_values = [b for b, _ in pairs]
    cand_values = [c for _, c in pairs]
    result = {"metric": metric, "n": len(pairs), "baseline": describe(base_values), "candidate": describe(cand_values)}
    if not pairs or statistics.median(base_values) <= 0:
        result["status"] = "unavailable" if not pairs else "undefined_zero_baseline"
        return result
    ratio = statistics.median(cand_values) / statistics.median(base_values)
    paired = [c / b for b, c in pairs if b > 0]
    rng = random.Random(seed)
    boot = []
    for _ in range(resamples):
        sample = rng.choices(pairs, k=len(pairs))
        denominator = statistics.median(b for b, _ in sample)
        if denominator > 0:
            boot.append(statistics.median(c for _, c in sample) / denominator)
    result.update(status="defined", ratio=ratio, improvement=1 - ratio,
                  ratio_ci95=[harness.percentile(boot, .025), harness.percentile(boot, .975)] if boot else None,
                  paired_ratios=paired, paired_median_ratio=statistics.median(paired) if paired else None,
                  candidate_better_rounds=sum(c < b for b, c in pairs))
    return result


def resource_gate(comparisons, metrics, threshold, max_regression, mode):
    """Workload passes when the improvement is >= threshold with a CI wholly below 1."""
    verdicts = {}
    for metric in metrics:
        comparison = comparisons.get(metric) or {}
        defined = comparison.get("status") == "defined"
        ci = comparison.get("ratio_ci95") if defined else None
        improved = defined and comparison["improvement"] >= threshold and ci is not None and ci[1] < 1
        regressed = defined and comparison["improvement"] < -max_regression
        verdicts[metric] = {"defined": defined, "improvement": comparison.get("improvement"), "ratio_ci95": ci,
                            "meets_threshold": improved, "regressed": regressed}
    hits = [verdicts[metric]["meets_threshold"] for metric in metrics]
    passed = (any(hits) if mode == "any" else all(hits)) and not any(v["regressed"] for v in verdicts.values())
    return {"passed": passed, "metrics": verdicts}


# --------------------------------------------------------------------------
# Orchestration

def run(args):
    if not args.out.parent.exists():
        raise SystemExit(f"output parent does not exist: {args.out.parent}")
    args.out.mkdir(exist_ok=False)
    meter = Path(args.meter).resolve()
    binaries = {"baseline": harness.binary_info(args.baseline), "candidate": harness.binary_info(args.candidate)}
    meter_info = harness.binary_info(meter)
    workloads = [item for item in args.workloads.split(",") if item]
    if not workloads or not set(workloads) <= set(FORMATS):
        raise SystemExit(f"--workloads must name {sorted(FORMATS)}")
    gate_metrics = [item for item in args.gate_metrics.split(",") if item]
    if not set(gate_metrics) <= set(RESOURCE_METRICS):
        raise SystemExit(f"--gate-metrics must be from {RESOURCE_METRICS}")
    corpora = {fmt: build_corpus(fmt, args.records, args.record_bytes, args.files, args.tail_records) for fmt in workloads}
    probes = {fmt: build_corpus(fmt, args.probe_records, args.record_bytes, args.files, 1) for fmt in workloads}
    rng = random.Random(args.seed)
    work_parent = Path(tempfile.mkdtemp(prefix="pb-ingest-perf-", dir=args.work_dir))
    report = {"schema": SCHEMA, "created_unix_ns": time.time_ns(), "argv": sys.argv,
              "configuration": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items() if key != "func"},
              "binaries": binaries, "meter": meter_info, "host_before": harness.host_metadata(),
              "corpora": {fmt: corpus_metadata(corpus) for fmt, corpus in corpora.items()},
              "probe_corpora": {fmt: corpus_metadata(corpus) for fmt, corpus in probes.items()},
              "order": [], "samples": [], "probes": [], "observations": {}}
    observations = {}
    counter = 0

    def sandbox_for(label, kind):
        nonlocal counter
        counter += 1
        # Equal-length sandbox names keep canonical source paths the same size.
        return Sandbox(work_parent, f"{counter:05d}-{label[:4]}-{kind}")

    def remember(key, observation):
        digest = harness.digest(harness.canonical(observation))
        observations.setdefault(key, {}).setdefault(digest, observation)
        return digest

    try:
        if not args.skip_probes:
            for repetition in range(args.probe_repetitions):
                for fmt in workloads:
                    for kind in ("retry_503", "split_413"):
                        order = ["baseline", "candidate"]
                        rng.shuffle(order)
                        for label in order:
                            sandbox = sandbox_for(label, "probe")
                            try:
                                observation, failures = probe_sample(binaries[label]["path"], probes[fmt], sandbox,
                                                                     f"probe-{kind}", args, kind)
                                digest = remember(f"probe:{kind}:{fmt}:{label}", observation)
                            except Exception as error:  # Report and continue; the gate fails.
                                failures, digest = [f"harness error: {type(error).__name__}: {error}"], None
                            finally:
                                sandbox.close(args.keep_work)
                            report["probes"].append({"kind": kind, "workload": fmt, "binary": label,
                                                     "repetition": repetition, "observation_sha256": digest,
                                                     "check_failures": failures})
                            print(f"probe {kind} {fmt} {label}: {'ok' if not failures else failures[:3]}", file=sys.stderr)
        for round_index in range(args.warmup + args.repetitions):
            warmup = round_index < args.warmup
            order_workloads = list(workloads)
            rng.shuffle(order_workloads)
            for fmt in order_workloads:
                order = ["baseline", "candidate"]
                rng.shuffle(order)
                report["order"].append({"round": round_index, "warmup": warmup, "workload": fmt, "order": order})
                for label in order:
                    sandbox = sandbox_for(label, fmt[:4])
                    try:
                        sample, observation, failures = workload_sample(
                            binaries[label]["path"], corpora[fmt], sandbox, "perf-session", args, meter)
                        digest = remember(f"workload:{fmt}:{label}", observation)
                    except Exception as error:
                        sample, failures, digest = {}, [f"harness error: {type(error).__name__}: {error}"], None
                    finally:
                        sandbox.close(args.keep_work)
                    sample.update({"workload": fmt, "binary": label, "round": round_index, "warmup": warmup,
                                   "observation_sha256": digest, "check_failures": failures,
                                   "valid": not failures and digest is not None})
                    report["samples"].append(sample)
                    print(f"round {round_index}{' (warmup)' if warmup else ''} {fmt} {label}: "
                          f"wall={sample.get('wall_ms')} cpu={sample.get('cpu_ms')} rss={sample.get('peak_rss_bytes')} "
                          f"{'ok' if sample['valid'] else failures[:3]}", file=sys.stderr)
    finally:
        if not args.keep_work:
            shutil.rmtree(work_parent, ignore_errors=True)
    report["work_dir"] = str(work_parent) if args.keep_work else None
    report["host_after"] = harness.host_metadata()
    report["observations"] = observations
    report.update(evaluate(report, workloads, gate_metrics, args))
    harness.write_json(args.out / "report.json", report)
    write_csv(args.out / "samples.csv", report["samples"])
    print(json.dumps({"report": str(args.out / "report.json"), "gate": report["gate"]}, indent=2))
    return 0 if report["gate"]["passed"] else 1


def evaluate(report, workloads, gate_metrics, args):
    compatibility = {"passed": True, "workloads": {}, "probes": {}}

    def compat_entry(key_prefix, records):
        entry = {"check_failures": [failure for record in records for failure in record["check_failures"]][:50]}
        digests = {label: sorted({r["observation_sha256"] for r in records if r["binary"] == label}) for label in ("baseline", "candidate")}
        entry["observation_sha256"] = digests
        entry["baseline_stable"] = len(digests["baseline"]) == 1 and None not in digests["baseline"]
        entry["candidate_stable"] = len(digests["candidate"]) == 1 and None not in digests["candidate"]
        base = report["observations"].get(f"{key_prefix}:baseline", {}).get(digests["baseline"][0]) if digests["baseline"] and digests["baseline"][0] else None
        cand = report["observations"].get(f"{key_prefix}:candidate", {}).get(digests["candidate"][0]) if digests["candidate"] and digests["candidate"][0] else None
        entry["exact_match"] = base is not None and base == cand
        entry["logical_match"] = base is not None and cand is not None and logical(base, False) == logical(cand, False)
        entry["differences"] = diff_paths(base, cand) if base is not None and cand is not None and base != cand else []
        match = entry["exact_match"] if not args.allow_wire_byte_differences else entry["logical_match"]
        entry["passed"] = (not entry["check_failures"] and entry["baseline_stable"] and entry["candidate_stable"] and match)
        return entry

    for fmt in workloads:
        records = [s for s in report["samples"] if s["workload"] == fmt]
        entry = compat_entry(f"workload:{fmt}", records)
        compatibility["workloads"][fmt] = entry
        compatibility["passed"] &= entry["passed"]
    for kind in ("retry_503", "split_413"):
        for fmt in workloads:
            records = [p for p in report["probes"] if p["kind"] == kind and p["workload"] == fmt]
            if records:
                entry = compat_entry(f"probe:{kind}:{fmt}", records)
                compatibility["probes"][f"{kind}:{fmt}"] = entry
                compatibility["passed"] &= entry["passed"]
    if args.skip_probes:
        compatibility["probes_skipped"] = True

    summary, resources = {}, {"passed": True, "threshold": args.threshold, "max_regression": args.max_regression,
                              "mode": args.gate_mode, "metrics": gate_metrics, "workloads": {}}
    for index, fmt in enumerate(workloads):
        measured = [s for s in report["samples"] if s["workload"] == fmt and not s["warmup"]]
        by_round = {}
        for sample in measured:
            by_round.setdefault(sample["round"], {})[sample["binary"]] = sample
        complete = [pair for _, pair in sorted(by_round.items()) if
                    "baseline" in pair and "candidate" in pair and pair["baseline"]["valid"] and pair["candidate"]["valid"]]
        base = [pair["baseline"] for pair in complete]
        cand = [pair["candidate"] for pair in complete]
        comparisons = {metric: compare_metric(base, cand, metric, args.seed + index) for metric in RESOURCE_METRICS}
        gate = resource_gate(comparisons, gate_metrics, args.threshold, args.max_regression, args.gate_mode)
        gate["valid_rounds"] = len(complete)
        gate["required_rounds"] = args.repetitions
        gate["passed"] &= len(complete) == args.repetitions
        summary[fmt] = {"valid_rounds": len(complete), "comparisons": comparisons}
        resources["workloads"][fmt] = gate
        resources["passed"] &= gate["passed"]
    return {"summary": summary,
            "gate": {"passed": compatibility["passed"] and resources["passed"],
                     "compatibility": compatibility, "resources": resources}}


def write_csv(path, samples):
    fields = ["workload", "binary", "round", "warmup", "valid", "exit_code", *RESOURCE_METRICS,
              "requests", "request_body_bytes", "observation_sha256"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(samples)


def write_corpus(args):
    args.out.mkdir(exist_ok=False)
    for fmt in FORMATS:
        corpus = build_corpus(fmt, args.records, args.record_bytes, args.files, args.tail_records)
        folder = args.out / fmt
        folder.mkdir()
        for name, data in corpus["data"].items():
            (folder / name).write_bytes(data)
        (folder / "tail").write_bytes(b"".join(corpus["tail"]))
        harness.write_json(folder / "metadata.json", corpus_metadata(corpus))
    return 0


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    def corpus_options(command):
        command.add_argument("--records", type=positive, default=20000)
        command.add_argument("--record-bytes", type=positive, default=500, help="bytes per line including the newline")
        command.add_argument("--files", type=positive, default=2)
        command.add_argument("--tail-records", type=positive, default=16, help="lines appended before --complete")

    bench = commands.add_parser("run", help="benchmark baseline vs candidate")
    bench.add_argument("--baseline", required=True)
    bench.add_argument("--candidate", required=True)
    bench.add_argument("--meter", default=str(harness.DEFAULT_METER))
    bench.add_argument("--out", type=Path, required=True, help="new report directory")
    bench.add_argument("--work-dir", default=None, help="parent for temporary sandboxes")
    bench.add_argument("--repetitions", type=positive, default=7)
    bench.add_argument("--warmup", type=int, default=1)
    bench.add_argument("--workloads", default="jsonl,text")
    bench.add_argument("--batch-size", type=positive, default=None, help="pass --batch-size; default uses the CLI default (1000)")
    bench.add_argument("--timeout", type=float, default=300)
    bench.add_argument("--seed", type=int, default=20261017)
    bench.add_argument("--threshold", type=float, default=0.10, help="required fractional median improvement")
    bench.add_argument("--max-regression", type=float, default=0.05, help="tolerated fractional regression on any gate metric")
    bench.add_argument("--gate-metrics", default=",".join(DEFAULT_GATE_METRICS))
    bench.add_argument("--gate-mode", choices=("any", "all"), default="any")
    bench.add_argument("--allow-wire-byte-differences", action="store_true",
                       help="compare decoded rows/headers only, not normalized request bytes")
    bench.add_argument("--probe-records", type=positive, default=3000)
    bench.add_argument("--probe-repetitions", type=positive, default=2)
    bench.add_argument("--probe-fail-upload", type=positive, default=3)
    bench.add_argument("--probe-reject-over-rows", type=positive, default=37)
    bench.add_argument("--skip-probes", action="store_true")
    bench.add_argument("--keep-work", action="store_true")
    corpus_options(bench)
    bench.set_defaults(func=run)

    corpus = commands.add_parser("corpus", help="write the fixed corpus for inspection")
    corpus.add_argument("--out", type=Path, required=True)
    corpus_options(corpus)
    corpus.set_defaults(func=write_corpus)

    fixture = commands.add_parser("serve", help=argparse.SUPPRESS)
    fixture.add_argument("--record-root", required=True)
    fixture.set_defaults(func=serve)

    args = parser.parse_args(argv)
    if args.command == "run":
        if args.warmup < 0 or not 0 < args.threshold < 1 or args.max_regression < 0:
            parser.error("invalid --warmup, --threshold or --max-regression")
        batch = args.batch_size or DEFAULT_BATCH_SIZE
        if args.probe_fail_upload > -(-args.probe_records // batch):
            parser.error("--probe-fail-upload exceeds the probe's upload count")
        if args.probe_reject_over_rows >= batch:
            parser.error("--probe-reject-over-rows must be below the batch size to force a split")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
