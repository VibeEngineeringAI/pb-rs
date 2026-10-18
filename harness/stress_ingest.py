#!/usr/bin/env python3
"""Bounded Rust-only sustained file ingestion, restart and durable-outbox probe.

The source and checkpoints are real files under --out. The writer and HTTP sink
run in separate processes. No request bodies or record collections are retained
across requests. Sink latency is producer monotonic stamp to sink acceptance,
with fixed-bin approximate percentiles. The achieved producer rate and schedule
lag are reported and a lagging writer fails the rate target rather than being
credited with the requested rate. Backlog includes at most one bounded
producer batch materialized just before append. This is a correctness/resource
soak, not an isolated server or filesystem throughput benchmark.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import platform
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib

import run as harness
from stress_process import capture


# Rust splits uploads above MAX_REQUEST (9 MiB) and buffers at most MAX_UPLOAD
# (16 MiB) of pending rows; the sink and checkpoint reader must exceed both.
MAX_BODY = 10 * 1024 * 1024
MAX_CHECKPOINT = 32 * 1024 * 1024
MAX_ERRORS = 16
# Planned SIGINT gets this long before the capture escalates to TERM/KILL; a
# phase timeout must leave the whole grace budget plus margin after the signal.
SIGNAL_GRACE_SECONDS = 5.0
TIMEOUT_MARGIN_SECONDS = 2.0
LOCK_OWNER_SIGNAL_SECONDS = 3.0
LOCK_OWNER_TIMEOUT_SECONDS = LOCK_OWNER_SIGNAL_SECONDS + SIGNAL_GRACE_SECONDS + TIMEOUT_MARGIN_SECONDS
LOCK_CONTENDER_TIMEOUT_SECONDS = 1.0
SCHEDULE_LAG_TOLERANCE_SECONDS = 0.25
MAX_DURATION_SECONDS = 86400
MAX_RECORDS = 10 ** 9
MAX_TIMEOUT_SECONDS = 3600
DISK_HEADROOM_BYTES = 256 * 1024 * 1024
PROVENANCE_SOURCES = ("stress_ingest.py", "stress_process.py", "run.py", "meter.c")
ESCALATION_EVENTS = ("timeout_term", "planned_signal_term", "forced_child_kill", "forced_kill",
                     "descendant_held_pipes", "post_exit_drain_limit")
LATENCY_BINS_MS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000,
                   10000, 30000, 60000, 300000)
DATASET = "stress_ingest"
SESSION = "sustained"
HOST = "stress-ingest-host"


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")) + "\n")
    temporary.replace(path)


def read_json(path, maximum=MAX_BODY):
    with Path(path).open("rb") as source:
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("JSON artifact exceeds bounded read limit")
    return json.loads(raw)


def record_size(index, width, shape):
    return (64, width, min(width * 8, 1024 * 1024))[index % 3] if shape == "mixed" else width


def make_line(index, stamp, width, shape="fixed"):
    value = {"i": index, "t": stamp, "s": ""}
    target = record_size(index, width, shape)
    overhead = len(json.dumps(value, separators=(",", ":")).encode()) + 1
    if target < overhead:
        raise ValueError("record byte target cannot fit sequence and timestamp")
    value["s"] = chr(97 + index % 26) * (target - overhead)
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


class LatencyHistogram:
    def __init__(self):
        self.bins = [0] * (len(LATENCY_BINS_MS) + 1)
        self.count = 0
        self.sum_ms = self.maximum_ms = 0.0

    def observe(self, milliseconds):
        self.count += 1
        self.sum_ms += milliseconds
        self.maximum_ms = max(self.maximum_ms, milliseconds)
        index = 0
        while index < len(LATENCY_BINS_MS) and milliseconds > LATENCY_BINS_MS[index]:
            index += 1
        self.bins[index] += 1

    def result(self):
        def percentile(fraction):
            if not self.count:
                return None
            goal, cumulative = math.ceil(self.count * fraction), 0
            for index, count in enumerate(self.bins):
                cumulative += count
                if cumulative >= goal:
                    return LATENCY_BINS_MS[index] if index < len(LATENCY_BINS_MS) else None
            return None
        return {"count": self.count, "bin_upper_bounds_ms": [*LATENCY_BINS_MS, None],
                "bin_counts": self.bins, "p50_upper_ms": percentile(.50),
                "p95_upper_ms": percentile(.95), "p99_upper_ms": percentile(.99),
                "mean_ms": self.sum_ms / self.count if self.count else None,
                "max_ms": self.maximum_ms,
                "overflow_note": "null percentile means empty histogram or above final finite bin"}


def decode_body(body, encoding, maximum=MAX_BODY):
    if len(body) > maximum:
        raise ValueError("wire body exceeds limit")
    encoding = encoding.strip().lower()
    if encoding in ("", "identity"):
        return body
    if encoding not in ("gzip", "deflate"):
        raise ValueError("unsupported content encoding")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
    decoded = decoder.decompress(body, maximum + 1)
    if len(decoded) > maximum or decoder.unconsumed_tail:
        raise ValueError("decoded body exceeds limit")
    decoded += decoder.flush(maximum + 1 - len(decoded))
    if len(decoded) > maximum or not decoder.eof or decoder.unused_data:
        raise ValueError("decoded body exceeds limit or compression framing is invalid")
    return decoded


class SinkState:
    """Single-source, contiguous sequence verification with bounded replay state."""

    def __init__(self, config):
        self.config = config
        self.requests = self.accepted_requests = self.accepted_records = 0
        self.unique_events = self.duplicate_events = self.complete_markers = 0
        self.rejected_503 = self.rejected_413 = self.retry_checks = 0
        self.retry_matches = self.error_count = 0
        self.errors = {}
        self.dropped_error_kinds = 0
        self.collector = self.generation = None
        self.pending_retry = self.last_rejection = None
        self.content_hash = hashlib.sha256()
        self.event_id_hash = hashlib.sha256()
        self.sequence_hash = hashlib.sha256()
        self.histogram = LatencyHistogram()

    def error(self, kind, detail=None):
        """Count every error; retain at most MAX_ERRORS kinds with first detail."""
        self.error_count += 1
        entry = self.errors.get(kind)
        if entry is not None:
            entry["count"] += 1
        elif len(self.errors) < MAX_ERRORS:
            self.errors[kind] = {"kind": kind[:120], "count": 1,
                                 "first_detail": None if detail is None else str(detail)[:300],
                                 "first_request": self.requests,
                                 "first_monotonic_ns": time.monotonic_ns()}
        else:
            self.dropped_error_kinds += 1

    def accept(self, rows, now_ns, allow_replay=False):
        for row in rows:
            self.accepted_records += 1
            if not isinstance(row, dict):
                self.error("non-object ingestion row")
                continue
            kind = row.get("pb_kind")
            collector = row.get("pb_collector")
            if self.collector is None:
                self.collector = collector
            if collector != self.collector or not isinstance(collector, str) or not collector:
                self.error("collector identity changed or missing")
            if row.get("pb_session") != SESSION or row.get("pb_host") != HOST:
                self.error("session/host metadata differs")
            try:
                collected = row["pb_collected_at"]
                if not isinstance(collected, str) or len(collected) > 64:
                    raise ValueError("invalid collection time")
                if datetime.fromisoformat(collected.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError("collection time lacks timezone")
            except (ValueError, TypeError, KeyError):
                self.error("invalid pb_collected_at timestamp")
            if kind == "complete":
                if (row.get("pb_seq") != self.unique_events + 1
                        or row.get("pb_event_id") != f"{collector}:complete"
                        or row.get("pb_source") != "" or row.get("pb_payload") != "{}"):
                    self.error("invalid completion marker")
                self.complete_markers += 1
                continue
            if kind != "event":
                self.error("unexpected pb_kind")
                continue
            try:
                payload = json.loads(row["pb_payload"])
                index, stamp = payload["i"], payload["t"]
                if type(index) is not int or type(stamp) is not int or index < 0 or stamp <= 0:
                    raise ValueError("invalid payload sequence/stamp")
                expected = make_line(index, stamp, self.config["record_bytes"], self.config["shape"])
                if payload != json.loads(expected):
                    self.error("payload content altered", f"i={index}")
            except (ValueError, TypeError, KeyError):
                self.error("invalid source payload")
                continue
            generation = row.get("pb_generation")
            if self.generation is None:
                self.generation = generation
            if generation != self.generation or not generation:
                self.error("source generation changed")
            if (row.get("pb_seq") != index + 1 or row.get("pb_source_seq") != index + 1
                    or row.get("pb_event_id") != f"{collector}:{generation}-{index + 1}"
                    or row.get("pb_source") != self.config["source"]):
                self.error("source/global sequence or event identity differs", f"i={index}")
            if index < self.unique_events:
                self.duplicate_events += 1
                if not allow_replay:
                    self.error("unexpected duplicate", f"i={index}")
                continue
            if index != self.unique_events:
                self.error("noncontiguous order", f"i={index}, expected={self.unique_events}")
                continue
            if self.complete_markers:
                self.error("event after completion marker")
            if stamp > now_ns:
                self.error("producer monotonic stamp is in the future")
            self.histogram.observe(max(0, now_ns - stamp) / 1e6)
            # Canonical key order matches the producer, so this digest compares
            # exact source content and order without retaining prior records.
            canonical = {"i": index, "t": stamp, "s": payload.get("s")}
            self.content_hash.update(json.dumps(canonical, separators=(",", ":")).encode() + b"\n")
            self.event_id_hash.update((str(row.get("pb_event_id")) + "\n").encode())
            self.sequence_hash.update(f"{index + 1}\n".encode())
            self.unique_events += 1

    def upload(self, wire, decoded, rows):
        self.requests += 1
        signature = {"wire_sha256": hashlib.sha256(wire).hexdigest(),
                     "decoded_sha256": hashlib.sha256(decoded).hexdigest(),
                     "rows_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True,
                         separators=(",", ":")).encode()).hexdigest(), "rows": len(rows)}
        replay = False
        if self.pending_retry is not None:
            self.retry_checks += 1
            replay = signature == self.pending_retry["signature"]
            if replay:
                self.retry_matches += 1
            else:
                self.error("retry request differs from the durable failed request")
            allow_replay = replay and self.pending_retry["accepted_before_failure"]
            self.pending_retry = None
        else:
            allow_replay = False
        threshold = self.config.get("reject_over_rows")
        if threshold is not None and len(rows) > threshold:
            self.rejected_413 += 1
            return 413
        fail = self.requests == self.config.get("fail_upload")
        ack_loss = self.requests == self.config.get("ack_loss_upload")
        if fail:
            self.rejected_503 += 1
            self.last_rejection = {"signature": signature, "accepted_before_failure": False,
                                   "committed_events_before_request": self.unique_events}
            self.pending_retry = self.last_rejection
            return 503
        delay = self.config.get("sink_delay_ms", 0) / 1000
        if delay:
            time.sleep(delay)
        committed_before = self.unique_events
        self.accept(rows, time.monotonic_ns(), allow_replay=allow_replay)
        self.accepted_requests += 1
        if ack_loss:
            self.rejected_503 += 1
            self.last_rejection = {"signature": signature, "accepted_before_failure": True,
                                   "committed_events_before_request": committed_before}
            self.pending_retry = self.last_rejection
            return 503
        return 200

    def result(self):
        return {"requests": self.requests, "accepted_requests": self.accepted_requests,
                "accepted_records_including_replays_and_markers": self.accepted_records,
                "unique_events": self.unique_events, "duplicate_events": self.duplicate_events,
                "complete_markers": self.complete_markers, "collector": self.collector,
                "generation": self.generation, "rejected_503": self.rejected_503,
                "rejected_413": self.rejected_413, "retry_checks": self.retry_checks,
                "retry_matches": self.retry_matches, "pending_retry": self.pending_retry,
                "last_rejection": self.last_rejection, "content_sha256": self.content_hash.hexdigest(),
                "event_id_sha256": self.event_id_hash.hexdigest(),
                "sequence_sha256": self.sequence_hash.hexdigest(),
                "latency": self.histogram.result(), "error_count": self.error_count,
                "errors": list(self.errors.values()),
                "dropped_error_kinds": self.dropped_error_kinds,
                "duplicate_policy": "exact replay after injected accept-before-503 is expected at-least-once delivery; no server deduplication is performed"}


class SinkHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass

    def respond(self, status, value):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # A single-thread fixture stays bounded and allows monitoring requests
        # between uploads; do not let an idle keep-alive monopolize the server.
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/__status":
            self.respond(200, self.server.state.result())
        else:
            self.respond(404, {})

    def body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            body = bytearray()
            while True:
                line = self.rfile.readline(1025)
                if len(line) > 1024 or not line.endswith(b"\n"):
                    raise ValueError("invalid chunk header")
                size = int(line.split(b";", 1)[0], 16)
                if size < 0 or size > MAX_BODY - len(body):
                    raise ValueError("wire body exceeds limit")
                if size == 0:
                    # Bound trailers as well as the body.
                    for _ in range(64):
                        trailer = self.rfile.readline(1025)
                        if trailer in (b"\r\n", b"\n"):
                            return bytes(body)
                        if not trailer or len(trailer) > 1024:
                            break
                    raise ValueError("invalid chunk trailers")
                chunk = self.rfile.read(size)
                if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("truncated chunk")
                body.extend(chunk)
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > MAX_BODY:
            raise ValueError("wire body exceeds limit")
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError("truncated body")
        return body

    def do_POST(self):
        if self.path != "/api/v1/ingest":
            return self.respond(404, {})
        try:
            wire = self.body()
            decoded = decode_body(wire, self.headers.get("Content-Encoding", ""))
            rows = json.loads(decoded)
            if not isinstance(rows, list) or not rows or len(rows) > 1000:
                raise ValueError("expected one to 1000 rows")
            if self.headers.get("X-P-Stream") != DATASET:
                raise ValueError("unexpected dataset header")
            status = self.server.state.upload(wire, decoded, rows)
            self.respond(status, {})
        except (ValueError, TypeError, OSError, zlib.error) as error:
            self.server.state.error("invalid HTTP upload", error)
            self.respond(400, {"error": str(error)[:200]})


def serve(config_path):
    config = read_json(config_path)
    server = HTTPServer(("127.0.0.1", 0), SinkHandler)
    server.state = SinkState(config)
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}"}), flush=True)
    server.serve_forever(poll_interval=.05)


def produce(config_path):
    """Append records on a fixed schedule; record i is due at start + i / rate.

    Schedule lag is measured per materialized record (stamp minus due time)
    and as due-but-unmaterialized records at a tick (1 means on schedule), so
    a slow writer is reported as a target miss, not as the requested rate.
    """
    config = read_json(config_path)
    rate = config["rate"]
    total = int(config["duration"] * rate)
    count = byte_count = 0
    digest = hashlib.sha256()
    started_ns = time.monotonic_ns()
    started = started_ns / 1e9
    first_ns = last_ns = None
    lag = {"max_schedule_lag_seconds": 0.0, "max_schedule_lag_records": 0}
    largest = max(record_size(i, config["record_bytes"], config["shape"]) for i in range(3))
    batch_limit = max(1, min(128, 1024 * 1024 // largest))

    def status(done, **extra):
        return {"generated": count, "bytes": byte_count, "sha256": digest.hexdigest(),
                "done": done, "target_records": total, "requested_rate": rate,
                "requested_duration_seconds": config["duration"],
                "started_monotonic": started, "first_record_monotonic":
                    None if first_ns is None else first_ns / 1e9,
                "last_record_monotonic": None if last_ns is None else last_ns / 1e9,
                **lag, "updated_monotonic": time.monotonic(), **extra}

    with Path(config["source"]).open("ab", buffering=0) as source:
        while count < total:
            elapsed = time.monotonic() - started
            due = min(total, int(elapsed * rate) + 1)
            if due <= count:
                time.sleep(min(.01, max(.0001, (count / rate) - elapsed)))
                continue
            lag["max_schedule_lag_records"] = max(lag["max_schedule_lag_records"], due - count)
            data = bytearray()
            until = min(due, count + batch_limit)
            while count < until:
                stamp = time.monotonic_ns()
                lag["max_schedule_lag_seconds"] = max(
                    lag["max_schedule_lag_seconds"], (stamp - started_ns - count * 1e9 / rate) / 1e9)
                first_ns = stamp if first_ns is None else first_ns
                last_ns = stamp
                line = make_line(count, stamp, config["record_bytes"], config["shape"])
                data.extend(line)
                digest.update(line)
                byte_count += len(line)
                count += 1
            # Publish materialized count before append: accepted can never lead
            # generated. At most one bounded producer batch has not appended.
            atomic_json(config["producer_status"], status(False))
            view = memoryview(data)
            while view:
                written = source.write(view)
                view = view[written:]
            # Batch production ticks rather than rewriting status once per tiny
            # record at high rates. Catch up immediately if generation is late.
            if count >= due and count < total:
                time.sleep(min(.02, max(0, config["duration"] - (time.monotonic() - started))))
    # Keep the requested rate window observable even when the final scheduled
    # record is generated just before its end.
    remaining = config["duration"] - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)
    atomic_json(config["producer_status"], status(True, ended_monotonic=time.monotonic(),
                                                 producer_batch_limit=batch_limit))


def producer_rate(status, tolerance=SCHEDULE_LAG_TOLERANCE_SECONDS):
    """Classify achieved generation against the requested schedule (pure)."""
    generated, target = status.get("generated", 0), status.get("target_records")
    started, ended = status.get("started_monotonic"), status.get("ended_monotonic")
    first, last = status.get("first_record_monotonic"), status.get("last_record_monotonic")
    window = last - first if first is not None and last is not None else None
    lag = status.get("max_schedule_lag_seconds")
    result = {"requested_rate": status.get("requested_rate"), "target_records": target,
              "generated": generated, "started_monotonic": started, "ended_monotonic": ended,
              "first_record_monotonic": first, "last_record_monotonic": last,
              "generation_window_seconds": window,
              # n records span n-1 schedule intervals.
              "achieved_rate": (generated - 1) / window if window and generated > 1 else None,
              "achieved_rate_over_run": generated / (ended - started)
                  if ended is not None and started is not None and ended > started else None,
              "max_schedule_lag_seconds": lag,
              "max_schedule_lag_records": status.get("max_schedule_lag_records"),
              "schedule_lag_tolerance_seconds": tolerance}
    if not status.get("done"):
        result["classification"] = "incomplete"
    elif generated != target:
        result["classification"] = "target_missed_record_count"
    elif lag is None or lag > tolerance:
        result["classification"] = "target_missed_behind_schedule"
    else:
        result["classification"] = "target_met"
    return result


class Fixture:
    def __init__(self, config_path, directory):
        self.log = (Path(directory) / "sink.log").open("wb")
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                        "serve", str(config_path)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=self.log, start_new_session=True,
                                       env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"})
        try:
            ready, _, _ = select.select([self.process.stdout], [], [], 5)
            if not ready:
                raise RuntimeError("sink startup timed out")
            line = self.process.stdout.readline(4097)
            if len(line) > 4096:
                raise RuntimeError("oversized sink startup response")
            self.url = json.loads(line)["url"]
            self.process.stdout.close()
        except BaseException:
            self.close()
            raise

    def status(self):
        with urllib.request.urlopen(self.url + "/__status", timeout=5) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("oversized sink status")
        return json.loads(raw)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
        if self.process.stdout and not self.process.stdout.closed:
            self.process.stdout.close()
        self.log.close()


def ingest_argv(binary, batch_size, *, follow=False, complete=False):
    argv = [str(binary), "ingest", "files", "logs/source.jsonl", "--dataset", DATASET,
            "--session", SESSION, "--state-file", "state/ingest.json", "--format", "jsonl",
            "--from", "beginning", "--batch-size", str(batch_size), "-o", "json"]
    if follow:
        argv += ["--follow", "--poll-interval", "50ms"]
    if complete:
        argv += ["--complete"]
    return argv


def checkpoint_summary(path):
    state = read_json(path, MAX_CHECKPOINT)
    pending = state.get("pending")
    return {"version": state.get("version"), "collector": state.get("collector"),
            "binding": state.get("binding"),
            "host": state.get("host"), "next_sequence": state.get("next_sequence"),
            "complete": state.get("complete"), "files": state.get("files"),
            "pending_rows": len(pending["rows"]) if pending else 0,
            "pending_rows_sha256": hashlib.sha256(json.dumps(pending["rows"], sort_keys=True,
                  separators=(",", ":")).encode()).hexdigest() if pending else None,
            "mode": oct(Path(path).stat().st_mode & 0o777),
            "lock_mode": oct(Path(str(path) + ".lock").stat().st_mode & 0o777)}


def _capture_thread(call):
    result = {}
    def execute():
        try:
            result["value"] = call()
        except BaseException as error:
            result["error"] = error
    thread = threading.Thread(target=execute, daemon=False)
    thread.start()
    return thread, result


def _signal_own_collectors(meter, output, signum):
    """Emergency controller cleanup, restricted to our own meter children.

    Popen from a worker thread appears under that thread's Linux children file.
    Check executable and artifact path before touching any process group.
    """
    for task in Path("/proc/self/task").iterdir():
        try:
            children = (task / "children").read_text().split()
        except OSError:
            continue
        for child in children:
            try:
                with Path(f"/proc/{child}/cmdline").open("rb") as command:
                    argv = command.read(4096).split(b"\0")
                if (len(argv) > 2 and os.fsdecode(argv[0]) == str(meter)
                        and os.fsdecode(argv[1]).startswith(str(output) + os.sep)
                        and argv[2] == b"--"):
                    os.killpg(int(child), signum)
            except (OSError, ValueError):
                continue


def capture_issues(result, label):
    """Process hygiene for one capture: fully reaped through the meter's native
    wait4 and finished without timeout, drain limit or signal escalation."""
    issues = []
    native = result.get("native_metrics") or {}
    if (native.get("resource_source") != "native_child_wait4"
            or not isinstance(native.get("termination"), dict)):
        issues.append(f"{label}: native wait4 status unavailable")
    if result.get("timed_out"):
        issues.append(f"{label}: timed out")
    if result.get("post_exit"):
        issues.append(f"{label}: post-exit {result['post_exit']}")
    if result.get("meter_exit_code") != 0:
        issues.append(f"{label}: meter exited {result.get('meter_exit_code')}")
    if result.get("meter_unreaped") or result.get("process_group_remaining"):
        issues.append(f"{label}: process not fully reaped")
    escalations = sorted({event.get("event") for event in result.get("events") or ()
                          if event.get("event") in ESCALATION_EVENTS})
    if escalations:
        issues.append(f"{label}: escalation {','.join(escalations)}")
    return issues


def planned_stop_issues(result, label):
    """The Rust follower installs no SIGINT handler: a planned stop is the
    default disposition, i.e. native termination by signal 2."""
    issues = capture_issues(result, label)
    events = [event.get("event") for event in result.get("events") or ()]
    if (not result.get("planned_interruption") or result.get("outcome") != "planned_signal"
            or "planned_sigint" not in events):
        issues.append(f"{label}: not stopped by the planned SIGINT")
    termination = (result.get("native_metrics") or {}).get("termination")
    if termination != {"signal": int(signal.SIGINT)}:
        issues.append(f"{label}: planned SIGINT ended with {termination}, expected signal 2")
    return issues


def failed_exit_issues(result, label):
    """An unplanned, ordinary nonzero exit (the follower giving up on a 503)."""
    issues = capture_issues(result, label)
    termination = (result.get("native_metrics") or {}).get("termination") or {}
    if result.get("planned_interruption") or result.get("outcome") != "completed":
        issues.append(f"{label}: fault exit was interrupted")
    if set(termination) != {"exit"} or termination.get("exit") == 0:
        issues.append(f"{label}: fault ended with {termination}, expected a nonzero exit")
    return issues


def validate_follow_phases(phases, *, fault_expected, last_rejection):
    """Exactly one nonzero 503 exit iff a fault was injected, its durable
    outbox matching the rejected request, then a final planned stop."""
    if not phases:
        return ["follow: no phase ran"]
    issues = []
    expected = 2 if fault_expected else 1
    if len(phases) != expected:
        issues.append(f"follow: {len(phases)} phases, expected {expected}")
    *earlier, final = phases
    for index, phase in enumerate(earlier):
        label = f"follow-{index}"
        if not fault_expected:
            issues.append(f"{label}: exited before the planned stop without an injected fault")
        issues.extend(failed_exit_issues(phase["capture"], label))
    issues.extend(planned_stop_issues(final["capture"], f"follow-{len(phases) - 1}"))
    if fault_expected:
        if not earlier:
            issues.append("follow: injected 503 did not end a follow phase")
        else:
            checkpoint = earlier[0].get("checkpoint") or {}
            if not (last_rejection and checkpoint.get("pending_rows")
                    and checkpoint.get("pending_rows_sha256")
                        == last_rejection["signature"]["rows_sha256"]
                    and checkpoint.get("next_sequence")
                        == last_rejection["committed_events_before_request"] + 1):
                issues.append("follow-0: pending outbox does not match the rejected request")
    return issues


def validate_lock_probe(before, after, owner, contender, contender_finished_seconds):
    """``contender_finished_seconds`` is measured from before the owner launch,
    so it overstates the true offset and the ordering check is conservative."""
    issues = planned_stop_issues(owner, "lock owner")
    issues.extend(capture_issues(contender, "lock contender"))
    termination = (contender.get("native_metrics") or {}).get("termination") or {}
    if set(termination) != {"exit"} or termination.get("exit") == 0:
        issues.append(f"lock contender: ended with {termination}, expected a nonzero exit")
    if "already locked" not in contender["stderr"]["prefix_utf8"].lower():
        issues.append("lock contender: stderr lacks 'already locked'")
    if before is None or before != after:
        issues.append("lock contender: idle checkpoint missing or changed")
    if not contender_finished_seconds < LOCK_OWNER_SIGNAL_SECONDS:
        issues.append("lock contender: finished after the owner's planned SIGINT")
    return issues


def idle_lock_probe(binary, meter, env, work, directory, batch_size, threads=None):
    state_path = work / "state" / "ingest.json"
    owner_launched = time.monotonic()
    thread, result = _capture_thread(lambda: capture(
        ingest_argv(binary, batch_size, follow=True), env=env, cwd=work,
        artifact_dir=directory / "idle-owner", meter=meter, timeout=LOCK_OWNER_TIMEOUT_SECONDS,
        signal_after_seconds=LOCK_OWNER_SIGNAL_SECONDS, signal_grace_seconds=SIGNAL_GRACE_SECONDS))
    if threads is not None:
        threads.append(thread)
    deadline = owner_launched + 1.0
    while not state_path.exists() and time.monotonic() < deadline and thread.is_alive():
        time.sleep(.01)
    # The writer has not started. Allow the first empty-file registration to
    # settle, then compare exact checkpoint bytes across the rejected caller.
    time.sleep(.10)
    before = hashlib.sha256(state_path.read_bytes()).hexdigest() if state_path.exists() else None
    rejected = capture(ingest_argv(binary, batch_size, follow=True), env=env, cwd=work,
                       artifact_dir=directory / "rejected-contender", meter=meter,
                       timeout=LOCK_CONTENDER_TIMEOUT_SECONDS)
    contender_finished = time.monotonic() - owner_launched
    after = hashlib.sha256(state_path.read_bytes()).hexdigest() if state_path.exists() else None
    thread.join(timeout=LOCK_OWNER_TIMEOUT_SECONDS + 2)
    if thread.is_alive():
        raise RuntimeError("idle follower collector did not stop")
    if "error" in result:
        raise result["error"]
    issues = validate_lock_probe(before, after, result["value"], rejected, contender_finished)
    return {"passed": not issues, "issues": issues, "checkpoint_before_sha256": before,
            "checkpoint_after_sha256": after,
            "contender_finished_seconds_after_owner_launch": contender_finished,
            "owner_planned_sigint_seconds": LOCK_OWNER_SIGNAL_SECONDS,
            "owner": result["value"], "contender": rejected, "writer_active_during_probe": False}


def file_sha256(path):
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": size}


def _unescape_mount(text):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), text)


def mount_for(path, mountinfo):
    """Mount containing absolute ``path``: the last longest mount-point prefix
    in /proc/self/mountinfo text, so overmounts win."""
    best = None
    for line in mountinfo.splitlines():
        left, separator, right = line.partition(" - ")
        fields, extra = left.split(), right.split()
        if not separator or len(fields) < 5 or not extra:
            continue
        point = _unescape_mount(fields[4])
        if path != point and not path.startswith(point.rstrip("/") + "/"):
            continue
        if best is None or len(point) >= len(best["mount_point"]):
            best = {"mount_point": point, "filesystem_type": extra[0],
                    "source": _unescape_mount(extra[1]) if len(extra) > 1 else None,
                    "mount_options": fields[5] if len(fields) > 5 else None}
    return best


def _git(directory, *argv):
    """Local read-only git query; bounded time and retained output."""
    with tempfile.TemporaryFile() as output:
        completed = subprocess.run(["git", "-C", str(directory), *argv], stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.DEVNULL, timeout=5,
                                   env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C",
                                        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
        output.seek(0)
        digest, size, prefix = hashlib.sha256(), 0, b""
        while block := output.read(1024 * 1024):
            digest.update(block)
            prefix = (prefix + block)[:4096] if len(prefix) < 4096 else prefix
            size += len(block)
    return completed.returncode, prefix, digest.hexdigest(), size


def git_provenance(directory):
    try:
        code, head, _, _ = _git(directory, "rev-parse", "HEAD")
        if code:
            return {"available": False, "reason": f"git rev-parse exited {code}"}
        code, _, digest, size = _git(directory, "status", "--porcelain=v1", "-z",
                                          "--untracked-files=normal")
        return {"available": True, "head": head.decode(errors="replace").strip(),
                "status_exit": code, "status_sha256": digest, "status_bytes": size,
                "dirty": size > 0}
    except (OSError, subprocess.SubprocessError) as error:
        return {"available": False, "reason": f"{type(error).__name__}: {error}"[:300]}


def provenance(binary, meter, output):
    """Local-only provenance; hostname is omitted from shareable reports."""
    harness_directory = Path(__file__).resolve().parent
    sources = {}
    for name in PROVENANCE_SOURCES:
        try:
            sources[name] = file_sha256(harness_directory / name)["sha256"]
        except OSError:
            sources[name] = None
    try:
        with open("/proc/self/mountinfo", "rb") as source:
            mountinfo = source.read(4 * 1024 * 1024).decode(errors="replace")
        filesystem = mount_for(str(output), mountinfo)
    except OSError as error:
        filesystem = {"unavailable_reason": type(error).__name__}
    uname = os.uname()
    return {"binary": file_sha256(binary), "meter": file_sha256(meter),
            "harness_sources_sha256": sources,
            "python": {"version": sys.version, "implementation": platform.python_implementation(),
                       "executable": sys.executable},
            "uname": {"sysname": uname.sysname, "release": uname.release,
                      "version": uname.version, "machine": uname.machine},
            "output_filesystem": filesystem, "git": git_provenance(harness_directory)}


def source_bytes(total, width, shape):
    """Exact bytes the producer appends for ``total`` records."""
    sizes = [record_size(index, width, shape) for index in range(3)]
    return (total // 3) * sum(sizes) + sum(sizes[:total % 3])


def disk_requirement(args):
    """Source bytes plus artifacts (~10 Hz timelines) and fixed headroom for
    checkpoints, the bounded outbox and logs."""
    total = int(args.duration * args.rate)
    data = source_bytes(total, args.record_bytes, args.shape)
    artifacts = int((args.duration + args.drain_timeout) * 10 * 512 * 4)
    return {"source_bytes": data, "artifact_estimate_bytes": artifacts,
            "headroom_bytes": DISK_HEADROOM_BYTES + data // 20,
            "required_bytes": data + artifacts + DISK_HEADROOM_BYTES + data // 20}


def argument_issues(args):
    issues = []
    floats = {"duration": args.duration, "rate": args.rate, "drain-timeout": args.drain_timeout,
              "complete-timeout": args.complete_timeout, "sink-delay-ms": args.sink_delay_ms}
    finite = {name for name, value in floats.items() if math.isfinite(value)}
    issues.extend(f"--{name} must be finite" for name in floats if name not in finite)
    if "duration" in finite and not 0 < args.duration <= MAX_DURATION_SECONDS:
        issues.append(f"--duration must be in (0, {MAX_DURATION_SECONDS}]")
    if "rate" in finite and not args.rate > 0:
        issues.append("--rate must be positive")
    if {"duration", "rate"} <= finite and args.duration > 0 and args.rate > 0:
        if not 1 <= args.duration * args.rate <= MAX_RECORDS:
            issues.append(f"duration * rate must produce 1..{MAX_RECORDS} records")
    for name in ("drain-timeout", "complete-timeout"):
        if name in finite and not 0 < floats[name] <= MAX_TIMEOUT_SECONDS:
            issues.append(f"--{name} must be in (0, {MAX_TIMEOUT_SECONDS}]")
    if "sink-delay-ms" in finite and not 0 <= args.sink_delay_ms <= 1000:
        issues.append("--sink-delay-ms must be in [0, 1000]")
    if not 64 <= args.record_bytes <= 1024 * 1024:
        issues.append("--record-bytes must be in [64, 1048576]")
    if not 1 <= args.batch_size <= 1000:
        issues.append("--batch-size must be in [1, 1000]")
    faults = {"fail-upload": args.fail_upload, "ack-loss-upload": args.ack_loss_upload,
              "reject-over-rows": args.reject_over_rows}
    for name, value in faults.items():
        if value is not None and not 1 <= value <= MAX_RECORDS:
            issues.append(f"--{name} must be in [1, {MAX_RECORDS}]")
    if sum(value is not None for value in faults.values()) > 1:
        issues.append("combined failure modes (run 503, ack-loss and 413 separately)")
    return issues


def existing_ancestor(path):
    path = Path(path).resolve()
    while not path.exists():
        path = path.parent
    return path


LIMITS = ["one append-only JSONL source; no rotation, truncation, symlink or file-discovery coverage",
          "source/state use the selected output filesystem; tmpfs changes fsync costs",
          "single bounded HTTP sink process; histogram is producer-to-acceptance latency, not network-only latency",
          "collector CPU includes controller threads, excluding separate writer/sink processes",
          "same-state rejection is checked while idle; different-state concurrency is not exercised",
          "controlled accept-before-503 on one event batch models at-least-once replay, not arbitrary kill/crash timing or server deduplication",
          "Rust collector only; there is no Go ingest feature to compare"]


def run(args):
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary, meter = Path(args.binary).resolve(), Path(args.meter).resolve()
    root, work = output / "sandbox", output / "sandbox" / "work"
    source = work / "logs" / "source.jsonl"
    config = {"source": str(source), "producer_status": str(output / "producer.json"),
              "duration": args.duration, "rate": args.rate, "record_bytes": args.record_bytes,
              "shape": args.shape, "fail_upload": args.fail_upload,
              "ack_loss_upload": args.ack_loss_upload, "reject_over_rows": args.reject_over_rows,
              "sink_delay_ms": args.sink_delay_ms}
    config_path = output / "fixture-config.json"
    fault_expected = args.fail_upload is not None or args.ack_loss_upload is not None
    # Written in ``finally`` whatever happens; ``passed`` needs a completed body.
    report = {"schema": "pb-stress-ingest/2", "scope": "Rust pb ingest files only",
              "config": config | {"batch_size": args.batch_size, "binary": str(binary),
                                   "meter": str(meter), "drain_timeout": args.drain_timeout,
                                   "complete_timeout": args.complete_timeout,
                                   "signal_grace_seconds": SIGNAL_GRACE_SECONDS,
                                   "timeout_margin_seconds": TIMEOUT_MARGIN_SECONDS},
              "passed": False, "completed": False, "error": None, "failures": [],
              "checks": {}, "issues": {"lock_probe": [], "follow": []}, "provenance": None,
              "lock_probe": None, "follow": [], "producer": None, "producer_rate": None,
              "complete": None, "complete_again": None, "checkpoint": None, "sink": None,
              "backlog": None, "cleanup": None, "limits": LIMITS}
    failures, phases = report["failures"], report["follow"]
    fixture = producer = producer_log = None
    threads = []
    stop_follow = threading.Event()
    try:
        report["provenance"] = provenance(binary, meter, output)
        for path in (root / "tmp", root / "cache", work / "logs", work / "state"):
            path.mkdir(parents=True)
        source.touch()
        atomic_json(config_path, config)
        fixture = Fixture(config_path, output)
        harness.seed_config(root, fixture.url)
        env = harness.isolated_env(root)
        env["HOSTNAME"] = HOST
        lock = idle_lock_probe(binary, meter, env, work, output / "lock-probe", args.batch_size,
                               threads)
        report["lock_probe"], report["issues"]["lock_probe"] = lock, lock["issues"]
        started = time.monotonic()
        stop_at = started + args.duration + args.drain_timeout
        controller_deadline = stop_at + SIGNAL_GRACE_SECONDS + TIMEOUT_MARGIN_SECONDS + 5

        def follow_runs():
            # Appends to the report's list so a failed run keeps its phases.
            for attempt in range(3):
                if stop_follow.is_set():
                    break
                remaining = max(.05, stop_at - time.monotonic())
                result = capture(ingest_argv(binary, args.batch_size, follow=True), env=env,
                                 cwd=work, artifact_dir=output / f"follow-{attempt}", meter=meter,
                                 timeout=remaining + SIGNAL_GRACE_SECONDS + TIMEOUT_MARGIN_SECONDS,
                                 signal_after_seconds=remaining,
                                 signal_grace_seconds=SIGNAL_GRACE_SECONDS)
                phases.append({"capture": result,
                               "checkpoint": checkpoint_summary(work / "state" / "ingest.json")})
                if stop_follow.is_set() or result["planned_interruption"] or time.monotonic() >= stop_at:
                    break
                if result["exit_code"] in (None, 0) or not fixture.status()["pending_retry"]:
                    break

        follow_thread, follow_result = _capture_thread(follow_runs)
        threads.append(follow_thread)
        producer_log = (output / "producer.log").open("wb")
        producer = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "produce", str(config_path)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=producer_log,
                                    env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
                                    start_new_session=True)
        backlog_max = samples = negative_backlog_samples = 0
        first_drained_at = None
        with (output / "backlog.csv").open("w", newline="") as source_timeline:
            writer = csv.DictWriter(source_timeline, fieldnames=["elapsed_seconds", "generated",
                "accepted_unique_events", "backlog", "producer_done", "producer_status_age_seconds"])
            writer.writeheader()
            while follow_thread.is_alive() or producer.poll() is None:
                sink = fixture.status()
                try:
                    generation = read_json(config["producer_status"])
                except FileNotFoundError:
                    generation = {"generated": 0, "done": False, "updated_monotonic": time.monotonic()}
                now = time.monotonic()
                backlog = generation["generated"] - sink["unique_events"]
                negative_backlog_samples += backlog < 0
                backlog_max = max(backlog_max, backlog)
                if generation["done"] and backlog == 0 and first_drained_at is None:
                    first_drained_at = now
                writer.writerow({"elapsed_seconds": now - started,
                    "generated": generation["generated"], "accepted_unique_events": sink["unique_events"],
                    "backlog": backlog, "producer_done": generation["done"],
                    "producer_status_age_seconds": now - generation["updated_monotonic"]})
                source_timeline.flush()
                samples += 1
                if now > controller_deadline:
                    raise RuntimeError("follow/producer exceeded bounded controller deadline")
                time.sleep(.10)
        producer.wait(timeout=1)
        follow_thread.join(timeout=1)
        if "error" in follow_result:
            raise follow_result["error"]
        generation = read_json(config["producer_status"])
        report["producer"], report["producer_rate"] = generation, producer_rate(generation)
        before_complete = fixture.status()
        backlog_before_complete = generation["generated"] - before_complete["unique_events"]
        report["backlog"] = {"csv": str(output / "backlog.csv"), "samples": samples,
            "max_records": backlog_max, "negative_samples": negative_backlog_samples,
            "before_complete": backlog_before_complete,
            "drain_seconds_after_producer": max(0, first_drained_at - generation["ended_monotonic"])
                if first_drained_at is not None and "ended_monotonic" in generation else None,
            "definition": "materialized generated records minus sink accepted unique events; includes at most one bounded producer batch before append"}
        complete = report["complete"] = capture(
            ingest_argv(binary, args.batch_size, complete=True), env=env, cwd=work,
            artifact_dir=output / "complete", meter=meter, timeout=args.complete_timeout)
        checkpoint = report["checkpoint"] = checkpoint_summary(work / "state" / "ingest.json")
        sink = report["sink"] = fixture.status()
        before_again = hashlib.sha256((work / "state" / "ingest.json").read_bytes()).hexdigest()
        again = report["complete_again"] = capture(
            ingest_argv(binary, args.batch_size, complete=True), env=env, cwd=work,
            artifact_dir=output / "complete-again", meter=meter, timeout=args.complete_timeout)
        after_again = hashlib.sha256((work / "state" / "ingest.json").read_bytes()).hexdigest()
        after_sink = fixture.status()
        follow_issues = validate_follow_phases(phases, fault_expected=fault_expected,
                                               last_rejection=sink["last_rejection"])
        report["issues"]["follow"] = follow_issues
        expected = generation["generated"]
        checks = {
            "lock_probe": lock["passed"],
            "follow_phases": not follow_issues,
            "producer_finished": producer.returncode == 0 and generation["done"],
            "producer_rate_target": report["producer_rate"]["classification"] == "target_met",
            "accepted_never_exceeded_generated": negative_backlog_samples == 0,
            "follow_drained_backlog": backlog_before_complete == 0,
            "complete_exit": not capture_issues(complete, "complete")
                and complete["termination"] == {"exit": 0},
            "all_records_in_order": sink["unique_events"] == expected,
            "source_content_hash": sink["content_sha256"] == generation["sha256"],
            "completion_marker": sink["complete_markers"] == 1,
            "no_sink_validation_errors": sink["error_count"] == 0,
            "checkpoint": checkpoint["version"] == 1 and checkpoint["complete"] is True
                and checkpoint["next_sequence"] == expected + 2 and not checkpoint["pending_rows"]
                and checkpoint["collector"] == sink["collector"]
                and checkpoint["mode"] == "0o600" and checkpoint["lock_mode"] == "0o600",
            "repeat_complete_noop": not capture_issues(again, "complete again")
                and again["termination"] == {"exit": 0} and before_again == after_again
                and sink["requests"] == after_sink["requests"],
            "retry_exact": not fault_expected
                or (sink["rejected_503"] == 1 and sink["retry_checks"] == 1
                    and sink["retry_matches"] == 1 and sink["pending_retry"] is None),
            "duplicate_policy": sink["duplicate_events"] == 0 if not args.ack_loss_upload
                else sink["duplicate_events"] > 0,
            "split_exercised": args.reject_over_rows is None or sink["rejected_413"] > 0,
        }
        files = (checkpoint.get("files") or {}).get("files") or {}
        entry = files.get(str(source), {})
        stat = source.stat()
        checks["checkpoint_source_offset"] = (entry.get("offset") == generation["bytes"]
            and entry.get("sequence") == expected and entry.get("identity") == f"{stat.st_dev}:{stat.st_ino}"
            and entry.get("generation") == sink["generation"] and not entry.get("discard_fragment"))
        binding = checkpoint.get("binding") or {}
        checks["checkpoint_binding"] = (binding.get("directory") == str(work)
            and binding.get("dataset") == DATASET and binding.get("session") == SESSION
            and binding.get("includes") == ["logs/source.jsonl"] and binding.get("format") == "jsonl")
        report["checks"] = checks
        failures.extend(name for name, passed in checks.items() if not passed)
        report["completed"] = True
    except BaseException as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)[:1000]}
        if not isinstance(error, Exception):
            raise
    finally:
        report["cleanup"] = cleanup = {"producer_terminated": False, "collector_threads_alive": 0}
        try:
            if producer is not None and producer.poll() is None:
                cleanup["producer_terminated"] = True
                producer.terminate()
                try:
                    producer.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    producer.kill()
                    producer.wait(timeout=1)
            stop_follow.set()
            live = [thread for thread in threads if thread.is_alive()]
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
                if not live:
                    break
                _signal_own_collectors(meter, output, signum)
                for thread in live:
                    thread.join(timeout=1)
                live = [thread for thread in live if thread.is_alive()]
            cleanup["collector_threads_alive"] = len(live)
        finally:
            if producer_log is not None:
                producer_log.close()
            if fixture is not None:
                fixture.close()
            if cleanup["collector_threads_alive"]:
                failures.append("cleanup left collector threads alive")
            report["passed"] = report["completed"] and report["error"] is None and not failures
            atomic_json(output / "report.json", report)
    return report


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--binary", required=True)
    result.add_argument("--meter", required=True)
    result.add_argument("--out", required=True, help="new directory on the desired benchmark filesystem")
    result.add_argument("--duration", type=float, default=60)
    result.add_argument("--rate", type=float, default=1000)
    result.add_argument("--record-bytes", type=int, default=64, help="exact source JSONL bytes per fixed record")
    result.add_argument("--shape", choices=("fixed", "mixed"), default="fixed")
    result.add_argument("--batch-size", type=int, default=1000)
    result.add_argument("--drain-timeout", type=float, default=5)
    result.add_argument("--complete-timeout", type=float, default=30)
    result.add_argument("--fail-upload", type=int)
    result.add_argument("--ack-loss-upload", type=int, help="accept then return 503, requiring at-least-once replay")
    result.add_argument("--reject-over-rows", type=int, help="return 413 above this row count")
    result.add_argument("--sink-delay-ms", type=float, default=0)
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("serve", "produce"):
        return serve(argv[1]) if argv[0] == "serve" else produce(argv[1])
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    issues = argument_issues(args)
    if not issues:
        need = disk_requirement(args)["required_bytes"]
        free = shutil.disk_usage(existing_ancestor(args.out)).free
        if need > free:
            issues.append(f"disk preflight: need {need} bytes including headroom, {free} free")
    if issues:
        argument_parser.error("; ".join(issues))
    report = run(args)
    print(json.dumps({"passed": report["passed"], "report": str(Path(args.out).resolve() / "report.json"),
                      "generated": (report["producer"] or {}).get("generated"),
                      "rate": (report["producer_rate"] or {}).get("classification"),
                      "failures": report["failures"], "issues": report["issues"],
                      "error": report["error"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
