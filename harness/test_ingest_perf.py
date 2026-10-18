#!/usr/bin/env python3
"""Ingest benchmark harness: corpus, fixture, normalization, checks and gates.

End-to-end tests drive a small Python stand-in for `pb ingest files` through the
real pb-meter, so no Rust build is needed. They are skipped without bin/pb-meter.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.request

import ingest_perf
import run as harness


class DefaultBatchTests(unittest.TestCase):
    def test_default_probe_reaches_failure_with_actual_cli_batch_size(self):
        with mock.patch.object(ingest_perf, 'run', return_value=0) as run:
            self.assertEqual(ingest_perf.main([
                'run', '--baseline', '/baseline', '--candidate', '/candidate', '--out', '/report'
            ]), 0)
            args = run.call_args.args[0]
            self.assertIsNone(args.batch_size)
            self.assertEqual(ingest_perf.DEFAULT_BATCH_SIZE, 1000)
            self.assertGreaterEqual(args.probe_records,
                                    args.probe_fail_upload * ingest_perf.DEFAULT_BATCH_SIZE)


FAKE_CLI = r'''#!{python}
# Minimal stand-in for `pb ingest files` honoring the durable outbox contract.
import glob, hashlib, json, os, sys, urllib.error, urllib.request, uuid
from datetime import datetime, timezone
import tomllib
VARIANT = {variant!r}
args = sys.argv[1:]
assert args[:2] == ["ingest", "files"], args
pattern, opts, complete, index = args[2], {{}}, False, 3
while index < len(args):
    if args[index] == "--complete":
        complete, index = True, index + 1
    else:
        opts[args[index]], index = args[index + 1], index + 2
with open(os.path.join(os.environ["XDG_CONFIG_HOME"], "pb", "config.toml"), "rb") as handle:
    config = tomllib.load(handle)
url = config["Profiles"][config["DefaultProfile"]]["url"]
state_path, batch = opts["--state-file"], int(opts.get("--batch-size", "100"))
if VARIANT == "batch":
    batch = max(1, batch - 1)
os.close(os.open(state_path + ".lock", os.O_CREAT | os.O_WRONLY, 0o600))
def save(state):
    temporary = os.path.join(os.path.dirname(state_path), ".pb-state-fake.tmp")
    with os.fdopen(os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "w") as handle:
        json.dump(state, handle)
    os.replace(temporary, state_path)
try:
    with open(state_path) as handle:
        state = json.load(handle)
except FileNotFoundError:
    state = {{"version": 1, "binding": {{"endpoint": url, "dataset": opts["--dataset"], "session": opts["--session"],
             "directory": os.getcwd(), "includes": [pattern], "excludes": [], "format": opts["--format"]}},
             "collector": str(uuid.uuid4()), "host": os.environ.get("HOSTNAME", "unknown"), "next_sequence": 1,
             "files": {{"initialized": True, "revision": 0, "next_generation": 0, "files": {{}}}},
             "complete": False, "pending": None}}
    save(state)
separators = (", ", ": ") if VARIANT == "spacing" else (",", ":")
def post(rows):
    pieces = [rows]
    while pieces:
        piece = pieces.pop()
        request = urllib.request.Request(url + "/api/v1/ingest", method="POST",
            data=json.dumps(piece, separators=separators, ensure_ascii=False).encode(),
            headers={{"X-P-Stream": opts["--dataset"], "Content-Type": "application/json"}})
        try:
            urllib.request.build_opener(urllib.request.ProxyHandler({{}})).open(request, timeout=10).read()
        except urllib.error.HTTPError as error:
            if error.code == 413 and len(piece) > 1:
                half = len(piece) // 2
                pieces += [piece[half:], piece[:half]]
                continue
            sys.stderr.write("ingest failed: HTTP %d; pending batch retained\n" % error.code)
            sys.exit(1)
def report(count, done):
    print(json.dumps({{"ingested": count, "skipped": 0, "session_complete": done}}, separators=(",", ":")))
    sys.exit(0)
def row(kind, sequence, event_id, now, source="", generation="", source_seq=0, payload="{{}}"):
    return {{"pb_session": state["binding"]["session"], "pb_collector": state["collector"], "pb_seq": sequence,
            "pb_event_id": state["collector"] + ":" + event_id, "pb_kind": kind, "pb_source": source,
            "pb_generation": generation, "pb_source_seq": source_seq, "pb_host": state["host"],
            "pb_collected_at": now, "pb_payload": payload}}
uploaded = 0
if state["complete"]:
    report(0, True)
while True:
    pending = state["pending"]
    if pending:
        post(pending["rows"])
        uploaded += sum(item["pb_kind"] == "event" for item in pending["rows"])
        state.update(files=pending["next_files"], next_sequence=pending["next_sequence"],
                     complete=pending["complete"], pending=None)
        save(state)
        if state["complete"]:
            report(uploaded, True)
    files, records = json.loads(json.dumps(state["files"])), []
    for path in sorted(os.path.realpath(item) for item in glob.glob(pattern)):
        info = os.stat(path)
        entry = files["files"].get(path)
        if entry is None:
            number = files["next_generation"]
            entry = files["files"][path] = {{"identity": "%d:%d" % (info.st_dev, info.st_ino), "generation_number": number,
                "generation": "file-" + hashlib.sha256(("%s%d" % (path, number)).encode()).hexdigest()[:16],
                "offset": 0, "sequence": 0}}
            files["next_generation"] += 1
        with open(path, "rb") as handle:
            handle.seek(entry["offset"])
            for line in handle:
                if len(records) >= batch or not line.endswith(b"\n"):
                    break
                entry["offset"] += len(line)
                entry["sequence"] += 1
                text = line.decode()[:-1]
                value = json.loads(text) if opts["--format"] == "jsonl" else {{"message": text}}
                records.append((path, entry["generation"], entry["sequence"],
                                json.dumps(value, ensure_ascii=False, separators=(",", ":"))))
    if records:
        files["revision"] += 1
        sequence, rows, now = state["next_sequence"], [], datetime.now(timezone.utc).isoformat()
        for path, generation, source_seq, payload in records:
            rows.append(row("event", sequence, "%s-%d" % (generation, source_seq), now, path, generation, source_seq, payload))
            sequence += 1
        state["pending"] = {{"rows": rows, "next_files": files, "next_sequence": sequence, "complete": False, "skipped_lines": 0}}
        save(state)
        continue
    if files != state["files"]:
        state["files"] = files
        save(state)
    if complete:
        state["pending"] = {{"rows": [row("complete", state["next_sequence"], "complete", datetime.now(timezone.utc).isoformat())], "next_files": files,
                             "next_sequence": state["next_sequence"] + 1, "complete": True, "skipped_lines": 0}}
        save(state)
        continue
    report(uploaded, False)
'''

METER = harness.DEFAULT_METER


def fake_cli(directory, variant="exact"):
    path = Path(directory) / f"fake-pb-{variant}"
    path.write_text(FAKE_CLI.format(python=sys.executable, variant=variant))
    path.chmod(0o755)
    return str(path)


def options(**overrides):
    values = {"batch_size": 10, "timeout": 30, "probe_fail_upload": 3, "probe_reject_over_rows": 3}
    values.update(overrides)
    return argparse.Namespace(**values)


class CorpusTests(unittest.TestCase):
    def test_lines_have_exact_size_and_round_trip(self):
        for fmt in ingest_perf.FORMATS:
            corpus = ingest_perf.build_corpus(fmt, 50, 500, 3, 4)
            lines = [line for chunk in corpus["lines"].values() for line in chunk] + corpus["tail"]
            self.assertEqual(len(lines), 54)
            self.assertTrue(all(len(line) == 500 and line.endswith(b"\n") for line in lines))
            self.assertEqual(sum(map(len, corpus["lines"].values())), 50)
            self.assertEqual(corpus["sha256"], ingest_perf.build_corpus(fmt, 50, 500, 3, 4)["sha256"])
            payload = ingest_perf.expected_payload(fmt, lines[0])
            if fmt == "jsonl":
                self.assertEqual(payload["i"], 0)
                self.assertIn('quote" back\\ tab\t café', payload["msg"])
            else:
                self.assertTrue(payload["message"].startswith("00000000 debug"))

    def test_rejects_too_small_records(self):
        with self.assertRaises(ValueError):
            ingest_perf.build_corpus("jsonl", 10, 100, 1, 1)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pb-ingest-perf-fixture-")
        self.fixture = ingest_perf.Fixture(Path(self.directory.name))
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.fixture.close()
        self.directory.cleanup()

    def post(self, rows, path=ingest_perf.INGEST_PATH):
        request = urllib.request.Request(self.fixture.url + path, data=json.dumps(rows).encode(), method="POST",
                                         headers={"X-P-Stream": "d"})
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            with error:
                return error.code

    def test_records_injects_failures_and_rejects_large_batches(self):
        directory = self.fixture.phase("one", fail_upload=2)
        self.assertEqual([self.post([{"a": 1}]), self.post([{"a": 2}]), self.post([{"a": 3}])], [200, 503, 200])
        self.assertEqual(self.post([], path="/other"), 404)
        requests = ingest_perf.load_requests(directory)
        self.assertEqual([r["status"] for r in requests], [200, 503, 200, 404])
        self.assertEqual(json.loads(requests[1]["body"]), [{"a": 2}])
        self.assertIn(["x-p-stream", "d"], requests[0]["headers"])
        directory = self.fixture.phase("two", reject_over_rows=2)
        self.assertEqual([self.post([{}] * 3), self.post([{}] * 2)], [413, 200])
        self.assertEqual(len(ingest_perf.load_requests(directory)), 2)

    def test_phase_names_cannot_repeat_or_escape(self):
        self.fixture.phase("once")
        for name in ("once", "../x"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.fixture.phase(name)
            caught.exception.close()


class NormalizationTests(unittest.TestCase):
    def test_rows_wire_and_state(self):
        sandbox = argparse.Namespace(root=Path("/tmp/box"), fixture=argparse.Namespace(url="http://127.0.0.1:4321"))
        path = "/tmp/box/work/logs/a.jsonl"
        final = {"state": {"collector": "c0ffee00-0000-4000-8000-000000000000",
                           "files": {"files": {path: {"identity": "1:2", "generation": "file-0123456789abcdef",
                                                      "generation_number": 0}}}},
                 "sources": {path: {"identity": "1:2", "size": 1}}}
        normalizer = ingest_perf.Normalizer(sandbox, final)
        row = {"pb_collector": "c0ffee00-0000-4000-8000-000000000000", "pb_source": path,
               "pb_generation": "file-0123456789abcdef", "pb_collected_at": "2026-10-17T00:00:00.1+00:00",
               "pb_event_id": "c0ffee00-0000-4000-8000-000000000000:file-0123456789abcdef-1"}
        later = dict(row, pb_collected_at="2026-10-17T00:00:01+00:00")
        rows = normalizer.rows([row, row, later])
        self.assertEqual([r["pb_collected_at"] for r in rows], ["<collected_at-0>", "<collected_at-0>", "<collected_at-1>"])
        self.assertEqual(rows[0]["pb_source"], "<root>/work/logs/a.jsonl")
        self.assertEqual(rows[0]["pb_event_id"], "<collector>:<generation:<root>/work/logs/a.jsonl#0>-1")
        wire = normalizer.wire(json.dumps([row, later], separators=(",", ":")).encode())
        self.assertEqual(json.loads(wire), rows[1:])
        other = ingest_perf.Normalizer(argparse.Namespace(root=Path("/tmp/other"),
                                                          fixture=argparse.Namespace(url="http://127.0.0.1:9")),
                                       json.loads(json.dumps(final).replace("/tmp/box", "/tmp/other")
                                                  .replace("c0ffee00", "deadbeef").replace("0123456789abcdef", "fedcba9876543210")
                                                  .replace("1:2", "3:4")))
        moved = json.loads(json.dumps(row).replace("/tmp/box", "/tmp/other").replace("c0ffee00", "deadbeef")
                           .replace("0123456789abcdef", "fedcba9876543210").replace("00.1", "09.9"))
        self.assertEqual(other.rows([moved]), normalizer.rows([row]))
        state = normalizer.state({"binding": {"endpoint": "http://127.0.0.1:4321", "directory": "/tmp/box/work"},
                                  "files": {"files": {path: {"identity": "1:2"}}},
                                  "pending": {"rows": [row]}})
        self.assertEqual(state["binding"], {"endpoint": "<endpoint>", "directory": "<root>/work"})
        self.assertEqual(state["files"]["files"], {"<root>/work/logs/a.jsonl": {"identity": "<identity:<root>/work/logs/a.jsonl>"}})
        self.assertEqual(state["pending"]["rows"][0]["pb_collected_at"], "<collected_at-0>")

    def test_diff_paths(self):
        self.assertEqual(ingest_perf.diff_paths({"a": [1, 2]}, {"a": [1, 3]}), [{"path": "$.a[1]", "baseline": 2, "candidate": 3}])
        self.assertEqual(ingest_perf.diff_paths({"a": 1}, {"b": 1})[0]["path"], "$.a")


class StatisticsTests(unittest.TestCase):
    def samples(self, values):
        return [{"cpu_ms": value, "peak_rss_bytes": 100, "wall_ms": value} for value in values]

    def test_gate_requires_threshold_and_confident_improvement(self):
        base = self.samples([100, 101, 99, 100, 102])
        better = self.samples([80, 81, 79, 80, 82])
        comparisons = {metric: ingest_perf.compare_metric(base, better, metric, 1) for metric in ("cpu_ms", "peak_rss_bytes", "wall_ms")}
        self.assertAlmostEqual(comparisons["cpu_ms"]["improvement"], 0.2)
        self.assertEqual(comparisons["cpu_ms"]["candidate_better_rounds"], 5)
        gate = ingest_perf.resource_gate(comparisons, ["cpu_ms", "peak_rss_bytes"], 0.10, 0.05, "any")
        self.assertTrue(gate["passed"])
        self.assertFalse(ingest_perf.resource_gate(comparisons, ["cpu_ms", "peak_rss_bytes"], 0.10, 0.05, "all")["passed"])
        small = {"cpu_ms": ingest_perf.compare_metric(base, self.samples([95, 96, 94, 95, 97]), "cpu_ms", 1)}
        self.assertFalse(ingest_perf.resource_gate(small, ["cpu_ms"], 0.10, 0.05, "any")["passed"])

    def test_regression_on_any_gate_metric_fails(self):
        base = self.samples([100] * 5)
        cand = [{"cpu_ms": 50, "peak_rss_bytes": 120, "wall_ms": 100} for _ in range(5)]
        comparisons = {metric: ingest_perf.compare_metric(base, cand, metric, 1) for metric in ("cpu_ms", "peak_rss_bytes")}
        gate = ingest_perf.resource_gate(comparisons, ["cpu_ms", "peak_rss_bytes"], 0.10, 0.05, "any")
        self.assertTrue(gate["metrics"]["cpu_ms"]["meets_threshold"])
        self.assertTrue(gate["metrics"]["peak_rss_bytes"]["regressed"])
        self.assertFalse(gate["passed"])

    def test_zero_baseline_is_undefined(self):
        result = ingest_perf.compare_metric([{"m": 0}], [{"m": 0}], "m", 1)
        self.assertEqual(result["status"], "undefined_zero_baseline")

    def test_identical_resource_samples_do_not_meet_threshold(self):
        samples = self.samples([100, 101, 99, 100, 102])
        comparisons = {metric: ingest_perf.compare_metric(samples, samples, metric, 1)
                       for metric in ("cpu_ms", "peak_rss_bytes", "wall_ms")}
        self.assertFalse(ingest_perf.resource_gate(
            comparisons, list(comparisons), 0.10, 0.05, "any")["passed"])


@unittest.skipUnless(METER.exists() and sys.platform.startswith("linux"), "needs bin/pb-meter on Linux")
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pb-ingest-perf-e2e-")
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def sandbox(self, name):
        return ingest_perf.Sandbox(self.root, name)

    def test_workload_phases_pass_for_conforming_cli(self):
        binary = fake_cli(self.root)
        for fmt in ingest_perf.FORMATS:
            corpus = ingest_perf.build_corpus(fmt, 45, 400, 2, 3)
            sandbox = self.sandbox(f"w-{fmt}")
            try:
                sample, observation, failures = ingest_perf.workload_sample(binary, corpus, sandbox, "s", options(), METER)
            finally:
                sandbox.close(False)
            self.assertEqual(failures, [])
            self.assertEqual(sample["resource_source"], "native_child_wait4")
            self.assertGreater(sample["peak_rss_bytes"], 0)
            self.assertEqual(sample["requests"], 5)
            self.assertEqual([len(observation[p]["requests"]) for p in ("drain", "resume", "append_complete", "complete_again")], [5, 0, 2, 0])
            self.assertIsNone(observation["drain"]["state"]["pending"])

    def test_durability_probes_pass_for_conforming_cli(self):
        binary = fake_cli(self.root)
        corpus = ingest_perf.build_corpus("jsonl", 40, 400, 2, 1)
        for kind in ("retry_503", "split_413"):
            sandbox = self.sandbox(kind)
            try:
                observation, failures = ingest_perf.probe_sample(binary, corpus, sandbox, "p", options(), kind)
            finally:
                sandbox.close(False)
            self.assertEqual(failures, [], kind)
        self.assertEqual(observation["split"]["requests"][0]["status"], 413)

    def test_checks_detect_lost_durability(self):
        binary = fake_cli(self.root)
        source = Path(binary).read_text()
        # Commit offsets before upload: a 503 then loses the batch instead of replaying it.
        broken = source.replace('        state["pending"] = {"rows": rows', '        post(rows); uploaded += len(rows); state.update(files=files, next_sequence=sequence); save(state); continue\n        state["pending"] = {"rows": rows')
        self.assertNotEqual(source, broken)
        Path(binary).write_text(broken)
        sandbox = self.sandbox("lossy")
        try:
            _, failures = ingest_perf.probe_sample(binary, ingest_perf.build_corpus("jsonl", 40, 400, 2, 1), sandbox, "p", options(), "retry_503")
        finally:
            sandbox.close(False)
        self.assertTrue(any("durable outbox" in failure for failure in failures), failures)

    def run_main(self, candidate_variant, *extra):
        out = self.root / f"out-{candidate_variant}-{len(extra)}"
        argv = ["run", "--baseline", fake_cli(self.root), "--candidate", fake_cli(self.root, candidate_variant),
                "--meter", str(METER), "--out", str(out), "--work-dir", str(self.root), "--repetitions", "2",
                "--warmup", "0", "--workloads", "jsonl", "--records", "60", "--record-bytes", "400",
                "--batch-size", "10", "--probe-records", "30", "--probe-repetitions", "1",
                "--probe-reject-over-rows", "3", "--timeout", "30", *extra]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = ingest_perf.main(argv)
        return status, json.loads((out / "report.json").read_text())

    def test_identical_behavior_passes_compatibility(self):
        status, report = self.run_main("exact")
        # Shared-host timing can differ even for identical programs. Numeric
        # gate behavior is covered with deterministic samples above.
        self.assertEqual(status, 0 if report["gate"]["passed"] else 1)
        gate = report["gate"]
        self.assertTrue(gate["compatibility"]["passed"], json.dumps(gate["compatibility"], indent=1)[:4000])
        self.assertEqual(len([s for s in report["samples"] if s["valid"]]), 4)
        self.assertEqual(len(report["probes"]), 4)
        self.assertEqual(report["summary"]["jsonl"]["valid_rounds"], 2)
        self.assertIn("median", report["summary"]["jsonl"]["comparisons"]["cpu_ms"]["baseline"])
        self.assertEqual(len(report["order"]), 2)

    def test_wire_byte_differences_fail_unless_relaxed(self):
        _, report = self.run_main("spacing")
        entry = report["gate"]["compatibility"]["workloads"]["jsonl"]
        self.assertFalse(entry["exact_match"])
        self.assertTrue(entry["logical_match"])
        self.assertFalse(report["gate"]["compatibility"]["passed"])
        _, report = self.run_main("spacing", "--allow-wire-byte-differences")
        self.assertTrue(report["gate"]["compatibility"]["passed"])

    def test_batch_partitioning_change_fails_compatibility(self):
        _, report = self.run_main("batch", "--allow-wire-byte-differences")
        entry = report["gate"]["compatibility"]["workloads"]["jsonl"]
        self.assertFalse(entry["logical_match"])
        self.assertTrue(entry["differences"])
        self.assertFalse(report["gate"]["compatibility"]["passed"])


if __name__ == "__main__":
    unittest.main()
