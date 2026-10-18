#!/usr/bin/env python3
"""Bounded ingest sink/writer tests: local subprocesses, no CLI build/account."""
import argparse
import gzip
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
from unittest import mock
import urllib.request
import zlib

import stress_ingest as ingest


def config(source="/tmp/source.jsonl", **overrides):
    return {"source": source, "record_bytes": 64, "shape": "fixed",
            "fail_upload": None, "ack_loss_upload": None, "reject_over_rows": None,
            "sink_delay_ms": 0, **overrides}


def event(index, stamp=None, configuration=None):
    configuration = configuration or config()
    stamp = time.monotonic_ns() if stamp is None else stamp
    return {"pb_session": ingest.SESSION, "pb_host": ingest.HOST, "pb_collector": "collector",
            "pb_collected_at": datetime.now(timezone.utc).isoformat(),
            "pb_generation": "file-abcdef", "pb_seq": index + 1, "pb_source_seq": index + 1,
            "pb_event_id": f"collector:file-abcdef-{index + 1}", "pb_kind": "event",
            "pb_source": configuration["source"],
            "pb_payload": ingest.make_line(index, stamp, configuration["record_bytes"],
                                           configuration["shape"]).decode().rstrip("\n")}


def kinds(state):
    return {entry["kind"]: entry for entry in state.result()["errors"]}


def upload(state, rows):
    body = json.dumps(rows, separators=(",", ":")).encode()
    return state.upload(body, body, rows)


class IngestLogicTests(unittest.TestCase):
    def test_exact_record_sizes_and_mixed_content(self):
        for shape in ("fixed", "mixed"):
            for index in (0, 1, 2, 1000000):
                line = ingest.make_line(index, 1234567890123456, 256, shape)
                self.assertEqual(len(line), ingest.record_size(index, 256, shape))
                value = json.loads(line)
                self.assertEqual(value["i"], index)
                self.assertEqual(value["s"], chr(97 + index % 26) * len(value["s"]))

    def test_503_never_advances_and_retry_must_be_byte_identical(self):
        state = ingest.SinkState(config(fail_upload=1))
        rows = [event(0), event(1)]
        self.assertEqual(upload(state, rows), 503)
        self.assertEqual(state.unique_events, 0)
        self.assertEqual(upload(state, rows), 200)
        result = state.result()
        self.assertEqual(result["unique_events"], 2)
        self.assertEqual(result["retry_checks"], 1)
        self.assertEqual(result["retry_matches"], 1)
        self.assertEqual(result["error_count"], 0)
        other = ingest.SinkState(config(fail_upload=1))
        upload(other, rows)
        changed_wire = json.dumps(rows, indent=1).encode()
        self.assertEqual(other.upload(changed_wire, changed_wire, rows), 200)
        self.assertEqual(other.retry_matches, 0)
        self.assertIn("retry request differs", other.result()["errors"][0]["kind"])

    def test_accept_before_503_labels_expected_at_least_once_duplicates(self):
        state = ingest.SinkState(config(ack_loss_upload=1))
        rows = [event(0), event(1)]
        self.assertEqual(upload(state, rows), 503)
        self.assertEqual(state.unique_events, 2)
        self.assertEqual(upload(state, rows), 200)
        self.assertEqual(state.unique_events, 2)
        self.assertEqual(state.duplicate_events, 2)
        self.assertEqual(state.error_count, 0)
        self.assertEqual(state.result()["latency"]["count"], 2)
        self.assertIn("no server deduplication", state.result()["duplicate_policy"])

    def test_413_split_keeps_contiguous_order_and_digest(self):
        state = ingest.SinkState(config(reject_over_rows=2))
        rows = [event(i) for i in range(4)]
        self.assertEqual(upload(state, rows), 413)
        self.assertEqual(state.unique_events, 0)
        self.assertEqual(upload(state, rows[:2]), 200)
        self.assertEqual(upload(state, rows[2:]), 200)
        expected = b"".join((row["pb_payload"] + "\n").encode() for row in rows)
        self.assertEqual(state.result()["content_sha256"], hashlib.sha256(expected).hexdigest())
        self.assertEqual(state.unique_events, 4)
        self.assertEqual(state.error_count, 0)

    def test_duplicate_order_and_content_failures_are_bounded(self):
        state = ingest.SinkState(config())
        row = event(0)
        upload(state, [row])
        upload(state, [row])
        self.assertEqual(kinds(state)["unexpected duplicate"]["first_detail"], "i=0")
        bad = event(1)
        payload = json.loads(bad["pb_payload"])
        payload["s"] = "altered"
        bad["pb_payload"] = json.dumps(payload)
        upload(state, [bad])
        self.assertIn("payload content altered", kinds(state))
        for _ in range(100):
            upload(state, [event(9)])
        self.assertGreater(state.error_count, ingest.MAX_ERRORS)
        self.assertLessEqual(len(state.result()["errors"]), ingest.MAX_ERRORS)
        self.assertEqual(state.unique_events, 2)

    def test_repeated_errors_are_deduplicated_with_count_and_first_detail(self):
        state = ingest.SinkState(config())
        for index in range(100):
            state.error("noncontiguous order", f"i={index}")
        entry = kinds(state)["noncontiguous order"]
        self.assertEqual((entry["count"], entry["first_detail"]), (100, "i=0"))
        self.assertIsInstance(entry["first_monotonic_ns"], int)
        for index in range(ingest.MAX_ERRORS + 5):
            state.error(f"kind-{index}")
        result = state.result()
        self.assertEqual(len(result["errors"]), ingest.MAX_ERRORS)
        self.assertEqual(result["dropped_error_kinds"], 6)
        self.assertEqual(result["error_count"], 100 + ingest.MAX_ERRORS + 5)

    def test_fixed_histogram_percentiles_and_overflow(self):
        histogram = ingest.LatencyHistogram()
        self.assertIsNone(histogram.result()["p50_upper_ms"])
        for value in [1] * 50 + [20] * 45 + [100] * 4 + [400000]:
            histogram.observe(value)
        result = histogram.result()
        self.assertEqual(result["p50_upper_ms"], 1)
        self.assertEqual(result["p95_upper_ms"], 20)
        self.assertEqual(result["p99_upper_ms"], 100)
        self.assertEqual(sum(result["bin_counts"]), 100)
        self.assertEqual(len(result["bin_counts"]), len(ingest.LATENCY_BINS_MS) + 1)

    def test_sink_retains_aggregates_without_accumulating_records(self):
        state = ingest.SinkState(config())
        tracemalloc.start()
        try:
            for start in range(0, 10000, 100):
                upload(state, [event(index) for index in range(start, start + 100)])
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(state.unique_events, 10000)
        self.assertEqual(state.error_count, 0)
        self.assertLess(peak, 2 * 1024 * 1024)
        self.assertLess(len(json.dumps(state.result())), 10000)

    def test_decode_limits_apply_after_decompression_and_reject_truncation(self):
        raw = b"x" * 4096
        for encoding, encoded in (("identity", raw), ("gzip", gzip.compress(raw)),
                                  ("deflate", zlib.compress(raw))):
            self.assertEqual(ingest.decode_body(encoded, encoding, maximum=4096), raw)
        with self.assertRaises(ValueError):
            ingest.decode_body(gzip.compress(raw + b"x"), "gzip", maximum=4096)
        with self.assertRaises(ValueError):
            ingest.decode_body(gzip.compress(raw)[:-2], "gzip", maximum=4096)
        with self.assertRaises(ValueError):
            ingest.decode_body(b"x" * 4097, "identity", maximum=4096)


class FixtureProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pb-ingest-stress-test-")
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_separate_http_process_handles_gzip_chunked_and_reports_aggregates(self):
        configuration = config(source=str(self.root / "source.jsonl"))
        path = self.root / "config.json"
        ingest.atomic_json(path, configuration)
        fixture = ingest.Fixture(path, self.root)
        try:
            rows = [event(0, configuration=configuration)]
            body = gzip.compress(json.dumps(rows).encode())
            request = urllib.request.Request(fixture.url + "/api/v1/ingest", data=body,
                       headers={"Content-Encoding": "gzip", "X-P-Stream": ingest.DATASET})
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 200)
            second = json.dumps([event(1, configuration=configuration)]).encode()
            connection = http.client.HTTPConnection("127.0.0.1", int(fixture.url.rsplit(":", 1)[1]), timeout=2)
            connection.request("POST", "/api/v1/ingest", body=iter([second[:10], second[10:]]),
                               headers={"X-P-Stream": ingest.DATASET}, encode_chunked=True)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
            status = fixture.status()
            self.assertEqual(status["requests"], 2)
            self.assertEqual(status["unique_events"], 2)
            self.assertEqual(status["error_count"], 0)
            self.assertEqual(status["latency"]["count"], 2)
            self.assertLess(len(json.dumps(status)), 10000)
        finally:
            fixture.close()
        self.assertIsNotNone(fixture.process.returncode)

    def test_separate_writer_appends_rate_bounded_rows_and_final_digest(self):
        path = self.root / "config.json"
        source = self.root / "source.jsonl"
        configuration = config(source=str(source), duration=.06, rate=100,
                               record_bytes=80, shape="mixed",
                               producer_status=str(self.root / "producer.json"))
        ingest.atomic_json(path, configuration)
        subprocess.run([sys.executable, str(Path(ingest.__file__).resolve()), "produce", str(path)],
                       env={"PATH": os.defpath}, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=2, check=True)
        status = ingest.read_json(configuration["producer_status"])
        raw = source.read_bytes()
        self.assertTrue(status["done"])
        self.assertEqual(status["generated"], 6)
        self.assertEqual(status["bytes"], len(raw))
        self.assertEqual(status["sha256"], hashlib.sha256(raw).hexdigest())
        rows = raw.splitlines(keepends=True)
        self.assertEqual([len(row) for row in rows], [64, 80, 640, 64, 80, 640])
        self.assertEqual([json.loads(row)["i"] for row in rows], list(range(6)))
        self.assertEqual((status["target_records"], status["requested_rate"]), (6, 100))
        self.assertLessEqual(status["started_monotonic"], status["first_record_monotonic"])
        self.assertLess(status["last_record_monotonic"], status["ended_monotonic"])
        self.assertGreaterEqual(status["max_schedule_lag_records"], 1)
        self.assertGreaterEqual(status["max_schedule_lag_seconds"], 0)
        rate = ingest.producer_rate(status)
        self.assertIn(rate["classification"], ("target_met", "target_missed_behind_schedule"))
        self.assertGreater(rate["achieved_rate"], 0)
        self.assertEqual(rate["requested_rate"], 100)



def fake_capture(termination, *, planned=False, events=(), **overrides):
    """Shape of stress_process.capture output for the fields validators read."""
    native = {"resource_source": "native_child_wait4", "termination": termination}
    events = [{"event": name, "elapsed_seconds": 1.0} for name in events]
    if planned:
        events.insert(0, {"event": "planned_sigint", "elapsed_seconds": 1.0})
    result = {"timed_out": False, "post_exit": None, "meter_exit_code": 0,
              "meter_unreaped": False, "process_group_remaining": False,
              "planned_interruption": planned,
              "outcome": "planned_signal" if planned else "completed",
              "native_metrics": native, "termination": termination, "events": events,
              "stderr": {"prefix_utf8": ""}}
    result.update(overrides)
    return result


def stopped(**overrides):
    return fake_capture({"signal": 2}, planned=True, **overrides)


REJECTION = {"signature": {"rows_sha256": "abc"}, "committed_events_before_request": 10}


def fault_phase(sha="abc", next_sequence=11, termination=None, **overrides):
    return {"capture": fake_capture(termination or {"exit": 1}, **overrides),
            "checkpoint": {"pending_rows": 3, "pending_rows_sha256": sha,
                           "next_sequence": next_sequence}}


def validate(*captures_or_phases, fault=False, rejection=None):
    phases = [item if "capture" in item else {"capture": item, "checkpoint": {}}
              for item in captures_or_phases]
    return ingest.validate_follow_phases(phases, fault_expected=fault, last_rejection=rejection)


class PhaseValidationTests(unittest.TestCase):
    def test_single_planned_sigint_stop_passes(self):
        self.assertEqual(validate(stopped()), [])
        self.assertEqual(ingest.validate_follow_phases([], fault_expected=False,
                                                       last_rejection=None),
                         ["follow: no phase ran"])

    def test_crash_after_backlog_drain_without_fault_fails(self):
        issues = validate(fake_capture({"exit": 1}), stopped())
        self.assertTrue(any("without an injected fault" in issue for issue in issues))
        # A follower that exits on its own instead of the planned stop.
        for termination in ({"exit": 0}, {"exit": 101}, {"signal": 6}):
            issues = validate(fake_capture(termination))
            self.assertTrue(any("not stopped by the planned SIGINT" in issue for issue in issues))

    def test_planned_sigint_must_end_with_native_signal_2(self):
        for termination in ({"exit": 0}, {"exit": 130}, {"signal": 9}, {"signal": 15}):
            issues = validate(fake_capture(termination, planned=True))
            self.assertTrue(any("expected signal 2" in issue for issue in issues), termination)

    def test_escalation_timeout_drain_and_reap_failures_fail(self):
        cases = {"planned_signal_term": stopped(events=("planned_signal_term",)),
                 "forced_kill": stopped(events=("forced_child_kill", "forced_kill")),
                 "timed out": stopped(timed_out=True, events=("timeout_term",)),
                 "post-exit": stopped(post_exit="post_exit_drain_limit"),
                 "not fully reaped": stopped(meter_unreaped=True),
                 "meter exited": stopped(meter_exit_code=125)}
        for expected, capture in cases.items():
            issues = validate(capture)
            self.assertTrue(any(expected in issue for issue in issues), (expected, issues))

    def test_discarded_or_missing_native_metrics_fail(self):
        for native in (None, {"resource_source": "other", "termination": {"signal": 2}},
                       {"resource_source": "native_child_wait4"}):
            issues = validate(stopped(native_metrics=native))
            self.assertTrue(any("native wait4 status unavailable" in issue for issue in issues))

    def test_injected_fault_requires_exactly_one_nonzero_exit_with_matching_outbox(self):
        self.assertEqual(validate(fault_phase(), stopped(), fault=True, rejection=REJECTION), [])
        failing = {
            "did not end a follow phase": (stopped(),),
            "pending outbox does not match": (fault_phase(sha="other"), stopped()),
            "pending outbox does not match ": (fault_phase(next_sequence=12), stopped()),
            "expected a nonzero exit": (fault_phase(termination={"exit": 0}), stopped()),
            "expected a nonzero exit ": (fault_phase(termination={"signal": 9}), stopped()),
            "3 phases": (fault_phase(), fault_phase(), stopped()),
            "escalation": (fault_phase(events=("forced_kill",)), stopped()),
        }
        for expected, phases in failing.items():
            issues = validate(*phases, fault=True, rejection=REJECTION)
            self.assertTrue(any(expected.strip() in issue for issue in issues), (expected, issues))
        issues = validate(fault_phase(), stopped(), fault=True, rejection=None)
        self.assertTrue(any("pending outbox" in issue for issue in issues))

    def test_lock_probe_validation_and_ordering(self):
        contender = fake_capture({"exit": 1}, stderr={"prefix_utf8": "Error: state already locked"})
        self.assertEqual(ingest.validate_lock_probe("a", "a", stopped(), contender, 2.0), [])
        cases = {"after the owner's planned SIGINT": ("a", "a", stopped(), contender, 3.0),
                 "missing or changed": ("a", "b", stopped(), contender, 1.0),
                 "missing or changed ": (None, None, stopped(), contender, 1.0),
                 "lock owner: escalation": ("a", "a", stopped(events=("planned_signal_term",)),
                                            contender, 1.0),
                 "lock owner: planned SIGINT ended": ("a", "a", fake_capture(
                     {"exit": 0}, planned=True), contender, 1.0),
                 "expected a nonzero exit": ("a", "a", stopped(), fake_capture(
                     {"exit": 0}, stderr={"prefix_utf8": "already locked"}), 1.0),
                 "lacks 'already locked'": ("a", "a", stopped(), fake_capture({"exit": 1}), 1.0),
                 "lock contender: timed out": ("a", "a", stopped(), fake_capture(
                     {"exit": 1}, timed_out=True, stderr={"prefix_utf8": "already locked"}), 1.0)}
        for expected, values in cases.items():
            issues = ingest.validate_lock_probe(*values)
            self.assertTrue(any(expected.strip() in issue for issue in issues), (expected, issues))

    def test_timeouts_leave_the_full_signal_grace_budget(self):
        self.assertGreaterEqual(ingest.LOCK_OWNER_SIGNAL_SECONDS, 3)
        self.assertGreaterEqual(ingest.LOCK_OWNER_TIMEOUT_SECONDS, 6)
        self.assertGreater(ingest.LOCK_OWNER_TIMEOUT_SECONDS,
                           ingest.LOCK_OWNER_SIGNAL_SECONDS + ingest.SIGNAL_GRACE_SECONDS)
        self.assertGreater(ingest.TIMEOUT_MARGIN_SECONDS, 0)
        self.assertLess(1.0 + .1 + ingest.LOCK_CONTENDER_TIMEOUT_SECONDS,
                        ingest.LOCK_OWNER_SIGNAL_SECONDS)
        # Above Rust MAX_REQUEST (9 MiB) and MAX_UPLOAD (16 MiB) respectively.
        self.assertGreaterEqual(ingest.MAX_BODY, 10 * 1024 * 1024)
        self.assertGreaterEqual(ingest.MAX_CHECKPOINT, 32 * 1024 * 1024)


class ProducerRateTests(unittest.TestCase):
    def status(self, **overrides):
        value = {"generated": 101, "target_records": 101, "requested_rate": 10, "done": True,
                 "started_monotonic": 100.0, "first_record_monotonic": 100.0,
                 "last_record_monotonic": 110.0, "ended_monotonic": 110.1,
                 "max_schedule_lag_seconds": .01, "max_schedule_lag_records": 1}
        value.update(overrides)
        return value

    def test_target_met_reports_achieved_rate(self):
        rate = ingest.producer_rate(self.status())
        self.assertEqual(rate["classification"], "target_met")
        self.assertAlmostEqual(rate["achieved_rate"], 10)
        self.assertAlmostEqual(rate["generation_window_seconds"], 10)
        self.assertAlmostEqual(rate["achieved_rate_over_run"], 101 / 10.1)

    def test_misses_are_classified_not_credited_with_requested_rate(self):
        behind = ingest.producer_rate(self.status(max_schedule_lag_seconds=2.0,
                                                  max_schedule_lag_records=20,
                                                  last_record_monotonic=112.0))
        self.assertEqual(behind["classification"], "target_missed_behind_schedule")
        self.assertAlmostEqual(behind["achieved_rate"], 100 / 12)
        self.assertEqual(behind["max_schedule_lag_records"], 20)
        self.assertEqual(ingest.producer_rate(self.status(generated=50))["classification"],
                         "target_missed_record_count")
        incomplete = ingest.producer_rate({"generated": 0, "done": False})
        self.assertEqual(incomplete["classification"], "incomplete")
        self.assertIsNone(incomplete["achieved_rate"])


def arguments(**overrides):
    values = {"binary": "/nonexistent/pb", "meter": "/nonexistent/meter", "out": "/tmp/unused",
              "duration": 1.0, "rate": 10.0, "record_bytes": 64, "shape": "fixed",
              "batch_size": 10, "drain_timeout": 1.0, "complete_timeout": 5.0,
              "fail_upload": None, "ack_loss_upload": None, "reject_over_rows": None,
              "sink_delay_ms": 0.0}
    values.update(overrides)
    return argparse.Namespace(**values)


class ArgumentAndPreflightTests(unittest.TestCase):
    def test_bounds_reject_nonfinite_too_large_and_combined_modes(self):
        self.assertEqual(ingest.argument_issues(arguments()), [])
        cases = {"--duration must be finite": {"duration": float("nan")},
                 "--rate must be finite": {"rate": float("inf")},
                 "--duration must be in": {"duration": 86401},
                 "--rate must be positive": {"rate": -1},
                 "records": {"duration": 86400, "rate": 20000},
                 "records ": {"duration": .01, "rate": 1},
                 "--drain-timeout must be in": {"drain_timeout": 3601},
                 "--complete-timeout must be finite": {"complete_timeout": float("-inf")},
                 "--complete-timeout must be in": {"complete_timeout": 0},
                 "--sink-delay-ms must be in": {"sink_delay_ms": 1001},
                 "--record-bytes": {"record_bytes": 63},
                 "--batch-size": {"batch_size": 1001},
                 "--fail-upload must be in": {"fail_upload": 0},
                 "combined failure modes": {"fail_upload": 1, "reject_over_rows": 2}}
        for expected, overrides in cases.items():
            issues = ingest.argument_issues(arguments(**overrides))
            self.assertTrue(any(expected.strip() in issue for issue in issues), (expected, issues))

    def test_exact_mixed_source_bytes_and_disk_requirement(self):
        for shape in ("fixed", "mixed"):
            for total in (1, 2, 3, 7):
                exact = sum(len(ingest.make_line(index, 1, 256, shape)) for index in range(total))
                self.assertEqual(ingest.source_bytes(total, 256, shape), exact)
        requirement = ingest.disk_requirement(arguments(duration=10, rate=100,
                                                        record_bytes=1024 * 1024, shape="mixed"))
        self.assertEqual(requirement["source_bytes"],
                         333 * (64 + 2 * 1024 * 1024) + 64)
        self.assertGreater(requirement["required_bytes"],
                           requirement["source_bytes"] + ingest.DISK_HEADROOM_BYTES)

    def test_cli_rejects_without_starting_any_subprocess(self):
        base = ["--binary", "/nonexistent/pb", "--meter", "/nonexistent/meter"]
        with tempfile.TemporaryDirectory() as directory:
            out = str(Path(directory) / "out")
            rejected = [["--duration", "nan"], ["--rate", "inf"], ["--duration", "86401"],
                        ["--duration", "86400", "--rate", "100000"],
                        ["--fail-upload", "1", "--ack-loss-upload", "2"],
                        ["--complete-timeout", "1e9"]]
            with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("spawned")), \
                    mock.patch.object(ingest, "run", side_effect=AssertionError("ran")), \
                    mock.patch("sys.stderr"):
                for extra in rejected:
                    with self.assertRaises(SystemExit) as raised:
                        ingest.main([*base, "--out", out, *extra])
                    self.assertEqual(raised.exception.code, 2, extra)
                usage = mock.Mock(free=1024 * 1024)
                with mock.patch.object(ingest.shutil, "disk_usage", return_value=usage):
                    with self.assertRaises(SystemExit):
                        ingest.main([*base, "--out", out])
            self.assertFalse(Path(out).exists())


MOUNTINFO = """22 1 0:21 / / rw,relatime shared:1 - ext4 /dev/root rw
30 22 0:25 / /tmp rw,nosuid shared:2 - tmpfs tmpfs rw
31 22 0:26 / /data\\040dir rw shared:3 - btrfs /dev/sdb1 rw
32 30 0:27 / /tmp rw shared:4 - xfs /dev/sdc1 rw
"""


class ProvenanceTests(unittest.TestCase):
    def test_mount_for_longest_prefix_escapes_and_overmounts(self):
        self.assertEqual(ingest.mount_for("/home/x", MOUNTINFO)["filesystem_type"], "ext4")
        self.assertEqual(ingest.mount_for("/tmp/run/x", MOUNTINFO)["filesystem_type"], "xfs")
        found = ingest.mount_for("/data dir/out", MOUNTINFO)
        self.assertEqual((found["mount_point"], found["filesystem_type"]), ("/data dir", "btrfs"))
        self.assertEqual(ingest.mount_for("/tmpfoo", MOUNTINFO)["filesystem_type"], "ext4")

    def test_local_provenance_hashes_inputs_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            binary, meter = Path(directory) / "pb", Path(directory) / "meter"
            binary.write_bytes(b"binary")
            meter.write_bytes(b"meter")
            value = ingest.provenance(binary, meter, Path(directory))
            self.assertEqual(value["binary"]["sha256"], hashlib.sha256(b"binary").hexdigest())
            self.assertEqual(value["meter"]["bytes"], 5)
            self.assertEqual(value["harness_sources_sha256"]["stress_ingest.py"],
                             hashlib.sha256(Path(ingest.__file__).read_bytes()).hexdigest())
            self.assertIn("filesystem_type", value["output_filesystem"])
            self.assertNotIn("nodename", value["uname"])
            self.assertIn("version", value["python"])
            self.assertIn("available", value["git"])
            self.assertFalse(ingest.git_provenance(Path(directory))["available"])


class PartialReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pb-ingest-partial-")
        self.root = Path(self.directory.name)
        for name in ("pb", "meter"):
            (self.root / name).write_bytes(name.encode())

    def tearDown(self):
        self.directory.cleanup()

    def args(self, **overrides):
        values = {"binary": str(self.root / "pb"), "meter": str(self.root / "meter"),
                  "out": str(self.root / "out"), **overrides}
        return arguments(**values)

    def test_exception_before_fixture_still_writes_failed_report(self):
        report = ingest.run(self.args(binary=str(self.root / "missing")))
        saved = ingest.read_json(self.root / "out" / "report.json")
        for value in (report, saved):
            self.assertFalse(value["passed"])
            self.assertFalse(value["completed"])
            self.assertEqual(value["error"]["type"], "FileNotFoundError")
            self.assertEqual(value["cleanup"]["collector_threads_alive"], 0)

    def test_exception_after_sink_start_closes_fixture_and_saves_phases(self):
        started = []
        original = ingest.Fixture.__init__

        def remember(fixture, *arguments_):
            original(fixture, *arguments_)
            started.append(fixture)

        with mock.patch.object(ingest.Fixture, "__init__", remember), \
                mock.patch.object(ingest, "idle_lock_probe", side_effect=RuntimeError("probe broke")):
            report = ingest.run(self.args())
        self.assertEqual(report["error"], {"type": "RuntimeError", "message": "probe broke"})
        self.assertFalse(ingest.read_json(self.root / "out" / "report.json")["passed"])
        self.assertIsNotNone(report["provenance"]["binary"]["sha256"])
        self.assertEqual(len(started), 1)
        self.assertIsNotNone(started[0].process.returncode)

    def test_interrupt_is_reraised_after_report_is_written(self):
        with mock.patch.object(ingest, "idle_lock_probe", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                ingest.run(self.args())
        saved = ingest.read_json(self.root / "out" / "report.json")
        self.assertFalse(saved["passed"])
        self.assertEqual(saved["error"]["type"], "KeyboardInterrupt")

if __name__ == "__main__":
    unittest.main()
