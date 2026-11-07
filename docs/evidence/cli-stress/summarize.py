#!/usr/bin/env python3
"""Regenerate compact CLI stress evidence and a standalone HTML viewer.

Run from any directory: python3 docs/evidence/cli-stress/summarize.py
All eleven named reports must exist. Failed/incomplete reports remain visible;
they are never promoted to successful evidence. CSV reduction uses bounded
state (240 plot buckets and 4096 samples per window/metric). No benchmark,
network request, build, or product binary is executed by this script.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys


REPORTS = (
    "smoke-final", "backpressure-final", "deadline-final-v2", "scale-final",
    "large-text-final", "sql-soak-final", "ingest-soak-final", "ingest-503-final",
    "ingest-ack-final", "ingest-split-final-v2", "ingest-steady-final",
)
PLOT_BUCKETS = 240
WINDOW_SAMPLES = 4096
EXCLUDE_SECONDS = 10.0
WINDOW_SECONDS = 30.0
TIMELINE_FIELDS = ("rss_bytes", "fd_count", "threads")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path, root):
    return Path(os.path.relpath(Path(path).resolve(), Path(root).resolve())).as_posix()


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def truth(value):
    return value is True or str(value).lower() in ("true", "1")


def median(values):
    values = [value for value in values if value is not None]
    return statistics.median(values) if values else None


def locate(value, report_path, root):
    """Find a retained artifact after moving the repository checkout."""
    path = Path(value)
    if path.is_absolute() and path.exists():
        return path
    parts = path.parts
    for index in range(len(parts) - 1):
        if parts[index:index + 2] == ("harness", "results"):
            relocated = root.joinpath(*parts[index:])
            if relocated.exists():
                return relocated
    for candidate in (root / path, report_path.parent / path):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"referenced artifact missing: {value} (report {relative(report_path, root)})")


class Reservoir:
    def __init__(self):
        self.values = []
        self.count = 0
        self.rng = random.Random(2051)

    def add(self, value):
        if value is None:
            return
        self.count += 1
        if len(self.values) < WINDOW_SAMPLES:
            self.values.append(value)
        else:
            index = self.rng.randrange(self.count)
            if index < WINDOW_SAMPLES:
                self.values[index] = value

    def result(self):
        return {"median": median(self.values), "samples": self.count,
                "retained_samples": len(self.values),
                "method": "exact" if self.count <= WINDOW_SAMPLES else "bounded_reservoir_approximation"}


def csv_rows(path):
    with path.open(newline="") as source:
        yield from csv.DictReader(source)


def live_row(row):
    return truth(row.get("proc_available")) and not truth(row.get("child_preexec"))


def reduce_csv(value, report_path, root, *, process=False):
    """Two CSV passes; finite fixed buckets and bounded median reservoirs."""
    if not value:
        return {"available": False, "reason": "not recorded"}
    path = locate(value, report_path, root)
    total = 0
    end = live_end = 0.0
    previous = None
    monotonic = True
    for row in csv_rows(path):
        elapsed = number(row.get("elapsed_seconds"))
        if elapsed is None:
            continue
        total += 1
        monotonic = monotonic and (previous is None or elapsed >= previous)
        previous = elapsed
        end = max(end, elapsed)
        if process and live_row(row) and any(number(row.get(key)) is not None for key in TIMELINE_FIELDS):
            live_end = max(live_end, elapsed)
    keys = TIMELINE_FIELDS if process else ("generated", "accepted_unique_events", "backlog")
    buckets = [{} for _ in range(PLOT_BUCKETS)]
    window_end = max(EXCLUDE_SECONDS, live_end)
    ranges = {"first": (EXCLUDE_SECONDS, min(EXCLUDE_SECONDS + WINDOW_SECONDS, window_end)),
              "last": (max(EXCLUDE_SECONDS, window_end - WINDOW_SECONDS), window_end)}
    windows = {name: {key: Reservoir() for key in TIMELINE_FIELDS} for name in ranges}
    for row in csv_rows(path):
        elapsed = number(row.get("elapsed_seconds"))
        if elapsed is None or process and not live_row(row):
            continue
        index = min(PLOT_BUCKETS - 1, max(0, int(elapsed / end * PLOT_BUCKETS))) if end else 0
        bucket = buckets[index]
        bucket["count"] = bucket.get("count", 0) + 1
        bucket["elapsed_sum"] = bucket.get("elapsed_sum", 0) + elapsed
        for key in keys:
            observed = number(row.get(key))
            if observed is None:
                continue
            aggregate = bucket.setdefault(key, {"sum": 0.0, "count": 0, "min": observed, "max": observed})
            aggregate["sum"] += observed
            aggregate["count"] += 1
            aggregate["min"] = min(aggregate["min"], observed)
            aggregate["max"] = max(aggregate["max"], observed)
            if process:
                for name, (left, right) in ranges.items():
                    if left <= elapsed <= right:
                        windows[name][key].add(observed)
    points = []
    for bucket in buckets:
        if not bucket:
            continue
        point = {"seconds": round(bucket["elapsed_sum"] / bucket["count"], 4),
                 "samples": bucket["count"]}
        for key in keys:
            aggregate = bucket.get(key)
            point[key] = aggregate["sum"] / aggregate["count"] if aggregate else None
            if key in ("rss_bytes", "backlog"):
                point[key + "_min"] = aggregate["min"] if aggregate else None
                point[key + "_max"] = aggregate["max"] if aggregate else None
        points.append(point)
    result = {"available": total > 0, "raw_path": relative(path, root),
              "sha256": sha256(path), "samples": total, "end_seconds": end,
              "time_monotonic": monotonic, "points": points,
              "point_method": f"at most {PLOT_BUCKETS} equal-time buckets; plotted means with retained min/max",
              "scope": "sampled current process state; no memory-leak conclusion" if process
                  else "sampled generated-minus-accepted backlog, including a bounded producer batch"}
    if process:
        result["excluded_initial_seconds"] = EXCLUDE_SECONDS
        result["live_end_seconds"] = live_end
        result["windows"] = {name: {"range_seconds": list(ranges[name]),
            "available": ranges[name][1] >= ranges[name][0]
                and any(values.count for values in windows[name].values()),
            "metrics": {key: values.result() for key, values in windows[name].items()}}
            for name in ranges}
        result["window_note"] = "first/last up to 30 live seconds after initial 10 seconds; may overlap on short runs; pre-exec and unavailable samples excluded"
    return result


def native_totals(captures):
    captures = [capture for capture in captures if capture is not None]
    natives = [capture.get("native_metrics") for capture in captures if capture and capture.get("native_metrics")]
    fields = ("cpu_user_ms", "cpu_system_ms", "cpu_total_ms", "wall_ms",
              "proc_io_wchar", "proc_io_write_bytes", "proc_io_syscw")
    result = {"phases": len(captures), "phases_with_native_metrics": len(natives)}
    for field in fields:
        values = []
        for native in natives:
            value = number(native.get(field))
            if value is None and field == "cpu_total_ms":
                user, system = number(native.get("cpu_user_ms")), number(native.get("cpu_system_ms"))
                value = user + system if user is not None and system is not None else None
            if value is None and field == "wall_ms":
                first, last = number(native.get("started_monotonic_ns")), number(native.get("ended_monotonic_ns"))
                value = (last - first) / 1e6 if first is not None and last is not None else None
            if value is not None:
                values.append(value)
        result[field] = sum(values) if values else None
    peaks = [number(native.get("peak_rss_bytes")) for native in natives]
    result["peak_rss_bytes"] = max((value for value in peaks if value is not None), default=None)
    result["scope"] = "sum of native phase CPU/wall; maximum of phase lifetime peak RSS; excludes lock probe and separate writer/sink"
    return result


def deadline_prefix(samples):
    retained = []
    framed = []
    labels = []
    for sample in samples:
        stream = sample.get("stdout") or {}
        prefix = base64.b64decode(stream.get("prefix_base64") or "", validate=True)
        retained.append(prefix[:4096])
        rows = sample.get("json_rows") or {}
        framed.append(bool((rows.get("completed_rows") or 0) > 0
                           and (rows.get("complete_row_prefix") or rows.get("complete"))))
        labels.append(sample.get("label"))
    enough = bool(retained) and all(len(value) == 4096 for value in retained)
    paired = {"go", "rust"} <= set(labels)
    return {"scope": "deadline partial prefix and object framing only; output length is scheduling-dependent",
            "prefix_bytes": 4096, "both_implementations_present": paired,
            "prefix_available": enough, "prefix_equal": len(set(retained)) == 1 if enough and paired else None,
            "all_complete_object_prefixes": bool(framed) and all(framed),
            "full_output_parity_claimed": False,
            "prefix_sha256": hashlib.sha256(retained[0]).hexdigest() if enough and paired and len(set(retained)) == 1 else None}


def exact_output(report, name, spec, samples):
    if spec.get("expectation") == "deadline":
        return {"kind": "deadline_partial_prefix", **deadline_prefix(samples)}
    if spec.get("expectation") == "observation":
        return {"kind": "scheduling_dependent_observation", "passed": None,
                "scope": "interruption observations are not full-output parity passes"}
    prior = (report.get("output_validations") or {}).get(name)
    validation_samples = prior.get("samples", []) if prior else samples
    stdout = [sample.get("stdout") or {} for sample in validation_samples]
    available = bool(stdout) and all(value.get("sha256") and value.get("bytes") is not None for value in stdout)
    paired = {"go", "rust"} <= {sample.get("label") for sample in validation_samples}
    digests = {(value.get("sha256"), value.get("bytes")) for value in stdout}
    clean = bool(validation_samples) and all(not sample.get("issues") for sample in validation_samples)
    passed = available and paired and len(digests) == 1 and clean
    if prior:
        verdict = prior.get("verdict") or {}
        passed = passed and bool(verdict.get("valid") and verdict.get("compatible"))
    source_match = bool(samples) and all(
        bool((sample.get("validated_source") or {}).get("response_sha256"))
        and (sample.get("validated_source") or {}).get("response_sha256")
            == (sample.get("fixture_stats") or {}).get("response_sha256")
        and (sample.get("validated_source") or {}).get("stdout_sha256") == stdout[0].get("sha256")
        for sample in samples) if prior and stdout else None
    written = [(sample.get('native_metrics') or {}).get('proc_io_wchar') for sample in samples]
    write_coverage = (bool(written) and available and all(value is not None
                      and value >= stdout[0]['bytes'] for value in written)) if prior else None
    return {"kind": "separate_pipe_validation_before_null_sink" if prior else "captured_pipe_output",
            "available": available, "passed": passed,
            "validation_runs": len(validation_samples),
            "stdout_sha256": stdout[0].get("sha256") if passed else None,
            "stdout_bytes": stdout[0].get("bytes") if passed else None,
            "resource_runs_match_validated_source": source_match,
            "resource_runs_write_accounting_covers_validated_stdout": write_coverage,
            "write_accounting_scope": "post-capture lower bound on all native write bytes; not an stdout hash" if prior else None,
            "framing_observed_in_all_runs": bool(validation_samples)
                and all((sample.get("json_rows") or {}).get("available") for sample in validation_samples),
            "scope": "exact full Go/Rust stdout digest and byte-count equality; semantic JSON validation is not claimed"}


def sql_report(report, report_path, root):
    result = []
    for spec in report.get("cases", []):
        name = spec["name"]
        samples = [sample for sample in report.get("samples", [])
                   if sample.get("case") == name and not sample.get("warmup")]
        summary = (report.get("summary") or {}).get(name, {})
        medians = {}
        for label in ("go", "rust"):
            runs = [sample for sample in samples if sample.get("label") == label]
            recorded = (summary.get("medians") or {}).get(label)
            medians[label] = recorded if recorded is not None else {
                "runs": len(runs), **{field: median([number((sample.get("rates") or {}).get(field)) for sample in runs])
                    for field in ("wall_ms", "cpu_ms", "rows_per_second", "cpu_ms_per_million_rows", "output_mib_per_second")},
                "peak_rss_bytes": median([number((sample.get("native_metrics") or {}).get("peak_rss_bytes")) for sample in runs])}
        calibration = (report.get("calibrations") or {}).get(name)
        result.append({"report": report_path.parent.name, "case": name,
            "fixture": spec.get("fixture"), "output": spec.get("output"),
            "expectation": spec.get("expectation"), "consumer": spec.get("consumer"),
            "stdout_sink": report.get("stdout_sink", "pipe"),
            "measured_runs": len(samples), "medians": medians,
            "valid": summary.get("valid"), "compatible": summary.get("compatible"),
            "observation_only": summary.get("observation_only", spec.get("expectation") == "observation"),
            "performance_admitted": summary.get("performance_admitted", False),
            "observer_bound_runs": summary.get("observer_bound_runs", sum(bool((sample.get("observer") or {}).get("observer_bound")) for sample in samples)),
            "possibly_producer_bound_runs": summary.get("possibly_producer_bound_runs",
                sum(bool((sample.get("producer_comparison") or {}).get("possibly_producer_bound")) for sample in samples)),
            "calibration_available": bool(calibration and calibration.get("valid")),
            "exact_output": exact_output(report, name, spec, samples),
            "sample_issues": [{"label": sample.get("label"), "round": sample.get("round"), "issues": sample["issues"]}
                              for sample in samples if sample.get("issues")],
            "scope": "shared-host descriptive measurements; resource medians do not establish maximum throughput"})
    return result


def ingest_report(report, report_path, root):
    config = report.get("config") or {}
    producer = report.get("producer") or {}
    rate = report.get("producer_rate") or {}
    sink = report.get("sink") or {}
    backlog = report.get("backlog") or {}
    captures = [phase.get("capture") or {} for phase in report.get("follow", [])]
    timelines = [reduce_csv(capture.get("timeline_csv"), report_path, root, process=True) for capture in captures]
    return {"report": report_path.parent.name, "passed": report.get("passed"),
            "completed": report.get("completed"), "error": report.get("error"),
            "duration_seconds": config.get("duration"), "requested_rate": config.get("rate"),
            "record_bytes": config.get("record_bytes"), "shape": config.get("shape"),
            "batch_size": config.get("batch_size"), "drain_timeout": config.get("drain_timeout"),
            "generated": producer.get("generated"), "source_bytes": producer.get("bytes"),
            "achieved_rate_over_run": rate.get("achieved_rate_over_run"),
            "achieved_generation_rate": rate.get("achieved_rate"),
            "producer_rate_classification": rate.get("classification"),
            "max_schedule_lag_seconds": rate.get("max_schedule_lag_seconds"),
            "checks": report.get("checks") or {}, "failures": report.get("failures") or [],
            "phase_issues": report.get("issues"), "cleanup": report.get("cleanup"),
            "unique_events": sink.get("unique_events"), "duplicate_events": sink.get("duplicate_events"),
            "accepted_records_including_replays_and_markers": sink.get("accepted_records_including_replays_and_markers"),
            "complete_markers": sink.get("complete_markers"), "requests": sink.get("requests"),
            "rejected_503": sink.get("rejected_503"), "rejected_413": sink.get("rejected_413"),
            "retry_checks": sink.get("retry_checks"), "retry_matches": sink.get("retry_matches"),
            "content_sha256": sink.get("content_sha256"), "event_id_sha256": sink.get("event_id_sha256"),
            "sequence_sha256": sink.get("sequence_sha256"), "sink_error_count": sink.get("error_count"),
            "sink_errors": sink.get("errors"), "duplicate_policy": sink.get("duplicate_policy"),
            "latency": sink.get("latency"),
            "backlog": {key: value for key, value in backlog.items() if key != "csv"},
            "backlog_timeline": reduce_csv(backlog.get("csv"), report_path, root),
            "follow_native": native_totals(captures),
            "all_ingest_native": native_totals(captures + [report.get("complete"), report.get("complete_again")]),
            "follow_timelines": timelines,
            "primary_follow_timeline": len(timelines) - 1 if timelines else None,
            "output_filesystem": (report.get("provenance") or {}).get("output_filesystem"),
            "limits": report.get("limits") or []}


LIMITATIONS = [
    "Charged write bytes are process accounting, not physical-device writes or SSD wear. Durable pending-state and acknowledgement saves contribute to small-record I/O costs.",
    "Shared host: measurements are descriptive and may include competing CPU, I/O and scheduling work.",
    "The /dev/null resource phase follows separate full-output pipe validation using the same source and binary identities. Its fast discarded-output sink is not terminal or pipe throughput.",
    "Observer-bound or producer/reader-bound measurements are not maximum-throughput evidence. Raw admission flags and bound counts are retained.",
    "Scale workloads use three measured repetitions; the 10 GiB case has one. Smoke, deadline and backpressure are characterization runs; SQL soak repeats independent bounded queries.",
    "Deadline evidence compares a retained 4096-byte prefix and valid object framing. Scheduling changes the complete partial-output length; full-output parity is not claimed.",
    "Ingest is a Rust-only extension with no Go equivalent. The single HTTP validation sink and source writer can limit achieved rate or latency.",
    "Ingest latency is materialization-to-sink acceptance in fixed bins, including append, polling, upload and sink work; it is not network-only latency.",
    "RSS, file descriptors and threads are sampled. First/last windows exclude the initial 10 seconds; stable windows do not prove absence of leaks. Native peak RSS includes the meter's pre-exec floor.",
    "Accepted duplicate events after the controlled accept-before-503 fault are expected at-least-once replay. The fixture does not perform server deduplication or claim arbitrary-crash coverage.",
]


def build(root, reports_root, out):
    missing = [reports_root / name / "report.json" for name in REPORTS
               if not (reports_root / name / "report.json").is_file()]
    if missing:
        raise FileNotFoundError("required reports missing:\n" + "\n".join(relative(path, root) for path in missing))
    result = {"schema": "pb-cli-stress-evidence/1", "generator_sha256": sha256(Path(__file__)),
              "reproduce": "python3 docs/evidence/cli-stress/summarize.py",
              "reports": [], "excluded_runs": [], "sql": [], "ingest": [], "limitations": LIMITATIONS,
              "reduction": {"plot_bucket_limit": PLOT_BUCKETS, "median_reservoir_limit_per_metric_window": WINDOW_SAMPLES,
                            "exclude_initial_seconds": EXCLUDE_SECONDS, "window_seconds": WINDOW_SECONDS}}
    for name in REPORTS:
        path = reports_root / name / "report.json"
        with path.open() as source:
            report = json.load(source)
        ingest = name.startswith("ingest-")
        complete = report.get("completed") if ingest else report.get("complete")
        result["reports"].append({"name": name, "kind": "ingest" if ingest else "sql",
            "raw_path": relative(path, root), "raw_href": relative(path, out), "sha256": sha256(path),
            "schema": report.get("schema"), "complete": complete, "passed": report.get("passed"),
            "errors": report.get("errors") or report.get("error"),
            "failures": report.get("failures") or [],
            "binary_hashes": {key: value.get("sha256") for key, value in (report.get("binaries") or {}).items()},
            "ingest_binary_sha256": ((report.get("provenance") or {}).get("binary") or {}).get("sha256")})
        if ingest:
            result["ingest"].append(ingest_report(report, path, root))
        else:
            result["sql"].extend(sql_report(report, path, root))
    result["all_reports_complete"] = all(report["complete"] is True for report in result["reports"])
    result["all_reports_passed"] = all(report["passed"] is True for report in result["reports"])
    result['post_capture_checks_passed'] = all(
        (case['exact_output'].get('resource_runs_write_accounting_covers_validated_stdout') is True
         and case['exact_output'].get('resource_runs_match_validated_source') is True)
        if case['stdout_sink'] == 'null' else
        (case['exact_output'].get('prefix_equal') is True
         and case['exact_output'].get('all_complete_object_prefixes') is True)
        if case['expectation'] == 'deadline' else True
        for case in result['sql'])
    exploratory = reports_root / "ingest-split-final" / "report.json"
    if exploratory.exists():
        with exploratory.open() as source:
            previous = json.load(source)
        result["excluded_runs"].append({"name": "ingest-split-final",
            "raw_path": relative(exploratory, root), "raw_href": relative(exploratory, out),
            "sha256": sha256(exploratory), "passed": previous.get("passed"),
            "completed": previous.get("completed"), "checks": previous.get("checks"),
            "failures": previous.get("failures"),
            "reason": "exploratory split threshold did not trigger 413; replaced by ingest-split-final-v2 for split coverage, not evidence of a CLI failure"})
    return result


HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CLI stress evidence · Go / Rust</title>
<style>
:root{color-scheme:dark light;--ink:var(--foreground,#e8eef5);--muted:var(--muted-foreground,#9cabbc);--line:var(--border,#293646);--surface:var(--card,#151d27);--blue:#75b6ff;--mint:#75e0ba;--amber:#f0c77b;--red:#ff9b9b}
*{box-sizing:border-box}body{margin:0;background:transparent;color:var(--ink);font:14px/1.55 ui-sans-serif,system-ui,sans-serif}main{width:100%;margin:0;padding:0}h1{font-size:clamp(28px,5.4vw,44px);line-height:1.12;letter-spacing:-.045em;margin:12px 0 16px;font-weight:650}h2{font-size:21px;line-height:1.25;letter-spacing:-.025em;margin:0}h3{font-size:15px;margin:0}p{margin:8px 0 0}.eyebrow{font-size:10px;letter-spacing:.16em;font-weight:700;color:var(--mint)}.lede{max-width:760px;color:var(--muted);font-size:15px}.muted,.note{color:var(--muted)}.note{font-size:12px}.section{margin-top:34px}.section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px}.panel{background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:20px}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:24px}.stat{padding:16px;border:1px solid var(--line);border-radius:11px;background:var(--surface)}.stat strong{display:block;font-size:24px;letter-spacing:-.035em}.stat span{font-size:11px;color:var(--muted)}.badge{display:inline-block;border:1px solid var(--line);padding:3px 8px;border-radius:100px;font-size:10px;line-height:1.5;white-space:nowrap;color:var(--muted)}.pass{color:var(--mint);border-color:#36725f}.fail{color:var(--red);border-color:#884d52}.warn{color:var(--amber);border-color:#77613f}button,select{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:7px;padding:7px 10px;cursor:pointer}button[aria-pressed=true]{background:#243d4d;border-color:var(--mint);color:var(--mint)}button:focus-visible,a:focus-visible,select:focus-visible{outline:2px solid var(--mint);outline-offset:3px}.controls{display:flex;gap:6px;flex-wrap:wrap}.legend{display:flex;gap:18px;font-size:12px;color:var(--muted);margin:10px 0}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}.go{background:var(--blue)}.rust{background:var(--mint)}.bars{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.workload{padding:16px;background:var(--surface);border:1px solid var(--line);border-radius:11px}.workload-top{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}.workload-title{overflow-wrap:anywhere;font-size:13px;font-weight:650}.bar-row{display:grid;grid-template-columns:30px minmax(0,1fr) 86px;gap:8px;align-items:center;margin-top:10px;font-variant-numeric:tabular-nums}.bar-track{height:7px;background:#30405055;border-radius:6px;overflow:hidden}.bar{height:100%;border-radius:6px;transition:width .2s}.bar-value{text-align:right;font-size:12px}.small{font-size:11px}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}th{text-align:left;color:var(--muted);font-weight:500;font-size:11px}td,th{padding:10px 9px;border-bottom:1px solid var(--line);vertical-align:top}td:first-child,th:first-child{padding-left:0}td:last-child,th:last-child{padding-right:0}td.num,th.num{text-align:right;white-space:nowrap}tr:last-child td{border-bottom:0}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.chart{width:100%;min-height:190px;display:block}.chart-meta{display:flex;gap:12px;justify-content:space-between;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}.chart-label{color:var(--ink)}.coverage{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.coverage a{color:var(--ink);text-decoration:none;display:flex;justify-content:space-between;align-items:center;gap:8px;padding:11px 12px;border:1px solid var(--line);border-radius:8px;font-size:12px;overflow-wrap:anywhere}.coverage a:hover{border-color:var(--mint)}.coverage .badge{flex-shrink:0}.limitations{padding-left:19px;margin:12px 0 0;color:var(--muted);font-size:12px}.limitations li+li{margin-top:7px}details{margin-top:14px}summary{cursor:pointer;font-size:12px;color:var(--muted)}code{font:11px ui-monospace,monospace;overflow-wrap:anywhere}.hashes{display:grid;gap:10px;margin-top:10px}.hash-row{padding-top:9px;border-top:1px solid var(--line);min-width:0}a{color:var(--blue)}.footer{margin-top:24px;font-size:11px;color:var(--muted)}.empty{padding:20px;color:var(--muted);font-size:12px}#validation-table td:first-child{max-width:260px;overflow-wrap:anywhere}
@media(max-width:600px){main{padding:0}.bars,.grid2{grid-template-columns:1fr}.section-head{flex-direction:column;gap:10px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.panel{padding:16px}.coverage{grid-template-columns:1fr}}
@media(max-width:380px){main{padding:0}.panel{padding:16px}.workload{padding:16px}.bar-row{grid-template-columns:29px minmax(0,1fr) 77px;gap:6px}.stat{padding:16px}.stat strong{font-size:21px}td,th{padding:8px 6px}.section{margin-top:28px}}
</style></head><body><main>
<header><p class="lede">Go and Rust CLI characterization across large SQL responses, backpressure and deadlines, plus a sustained Rust ingestion soak. Shared-host resource measurements are descriptive.</p><p id="highlights"></p><div id="stats" class="stats"></div></header>
<section class="section"><div class="section-head"><div><h2>SQL resource comparison</h2><p class="note">Per-workload scales. Native CLI totals; lower values use shorter bars. Scale medians use three measured repetitions; the 10 GiB case uses one.</p></div><div class="controls" aria-label="Metric"><button data-metric="cpu_ms" aria-pressed="true">CPU</button><button data-metric="peak_rss_bytes" aria-pressed="false">Peak RSS</button><button data-metric="wall_ms" aria-pressed="false">Wall</button></div></div><div class="controls"><label class="small" for="suite">Workloads </label><select id="suite"><option value="ranked">Scale + 10 GiB</option><option value="soak">SQL soak</option><option value="characterization">Smoke + backpressure + deadline</option><option value="all">All SQL</option></select></div><div class="legend"><span><i class="dot go"></i>Go</span><span><i class="dot rust"></i>Rust</span><span id="metric-label"></span></div><div id="bars" class="bars"></div><p class="note" id="resource-note"></p><details open><summary>Tabular values for the selected metric</summary><div class="table-wrap"><table><thead><tr><th>Workload</th><th class="num" id="go-column">Go</th><th class="num" id="rust-column">Rust</th><th class="num">Reps Go / Rust</th></tr></thead><tbody id="comparison-table"></tbody></table></div></details></section>
<details class="section panel"><summary>Inspect output and protocol coverage</summary><div class="section-head"><div><h2>Output and protocol coverage</h2><p class="note">A resource phase that discards stdout follows separate exact-output validation. Deadline coverage is a bounded matching prefix and object framing.</p></div></div><div class="table-wrap"><table id="validation-table"><thead><tr><th>Case</th><th>Evidence</th><th class="num">Observer / producer bound runs</th></tr></thead><tbody id="validations"></tbody></table></div></details>
<section class="section"><div class="section-head"><div><h2>Rust ingestion · sustained follow</h2><p class="note">No Go ingestion equivalent. Source and checkpoint files use the recorded output filesystem.</p></div><span id="ingest-status" class="badge"></span></div><p id="ingest-note" class="note"></p><div id="ingest-stats" class="stats"></div><div class="grid2" style="margin-top:16px"><div class="panel"><h3>Current RSS</h3><p class="note">Bucket means; sampled process memory, excluding pre-exec.</p><svg id="rss-chart" class="chart" role="img" aria-label="Rust ingestion current RSS over time"></svg><div class="chart-meta"><span>MiB · producer + drain window</span><span id="rss-hover" class="chart-label"></span></div></div><div class="panel"><h3>Observed backlog</h3><p class="note">Maximum per plotted bucket; generated minus accepted unique events.</p><svg id="backlog-chart" class="chart" role="img" aria-label="Rust ingestion observed backlog over time"></svg><div class="chart-meta"><span>Records · includes bounded writer batch</span><span id="backlog-hover" class="chart-label"></span></div></div></div><div class="panel" style="margin-top:14px"><h3>First / last live windows</h3><p class="note">Up to 30 seconds each after excluding the initial 10 seconds. Counts and sample availability remain in results.json.</p><div class="table-wrap"><table><thead><tr><th>Metric</th><th class="num">First median</th><th class="num">Last median</th></tr></thead><tbody id="window-table"></tbody></table></div><p class="note">These sampled windows do not establish absence of a leak.</p></div></section>
<section class="section panel"><h2>Ingestion behavior checks</h2><p class="note">503 replay is byte-identical; accept-before-503 duplicates are expected at-least-once delivery, without server deduplication.</p><div class="table-wrap"><table><thead><tr><th>Run</th><th class="num">Unique events</th><th class="num">Duplicates</th><th class="num">Exact retries</th><th>Status</th></tr></thead><tbody id="ingest-runs"></tbody></table></div></section>
<details class="section panel"><summary>Coverage, provenance and limits</summary><h2>Coverage, provenance and limits</h2><p class="note">Raw reports retain individual samples, checkpoints, validation details and failures. Failed and incomplete inputs remain visible.</p><div id="coverage" class="coverage" style="margin-top:16px"></div><p id="excluded-note" class="note"></p><ul id="limitations" class="limitations"></ul><details><summary>Report hashes and reproduction</summary><p><code>python3 docs/evidence/cli-stress/summarize.py</code></p><div id="hashes" class="hashes"></div></details></details>
<p class="footer">Standalone view · no remote fonts, libraries or analytics. Full reduced evidence: <a href="results.json">results.json</a>.</p>
</main><script id="evidence" type="application/json">__RESULTS_DATA__</script><script>
'use strict';
const D=JSON.parse(document.getElementById('evidence').textContent), byId=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(x,d=1)=>x===null||x===undefined||!Number.isFinite(Number(x))?'—':Number(x).toLocaleString(undefined,{maximumFractionDigits:d});
const badge=(pass,yes='Pass',no='Fail')=>`<span class="badge ${pass===true?'pass':pass===false?'fail':'warn'}">${pass===true?yes:pass===false?no:'Unavailable'}</span>`;
const stat=(value,label)=>`<div class="stat"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`;
const passed=D.reports.filter(r=>r.passed===true).length;
byId('stats').innerHTML=stat(`${passed}/${D.reports.length}`,'reports passed')+stat(D.sql.length,'SQL cases')+stat(D.sql.filter(c=>c.stdout_sink==='null').length,'discarded-output resource cases')+stat(D.ingest.length,'Rust ingest scenarios');
const tiny=D.sql.find(c=>c.case==='tiny-10m'),raw=D.sql.find(c=>c.case==='stream-10gib');if(tiny&&raw){byId('highlights').textContent=`10M tiny JSON rows: Rust used ${fmt(100*(1-tiny.medians.rust.cpu_ms/tiny.medians.go.cpu_ms),0)}% less CPU and ${fmt(100*(1-tiny.medians.rust.peak_rss_bytes/tiny.medians.go.peak_rss_bytes),0)}% less peak memory. Raw 10 GiB text: Rust used ${fmt(raw.medians.rust.cpu_ms/raw.medians.go.cpu_ms,1)}× Go’s CPU in this descriptive run.`;}
const metrics={cpu_ms:{label:'Native CPU time',unit:'ms',scale:1},peak_rss_bytes:{label:'Native lifetime peak RSS',unit:'MiB',scale:1048576},wall_ms:{label:'Native wall time',unit:'s',scale:1000}};
let metric='cpu_ms';
function chosen(){const suite=byId('suite').value;return D.sql.filter(c=>suite==='all'||suite==='ranked'&&['scale-final','large-text-final'].includes(c.report)||suite==='soak'&&c.report==='sql-soak-final'||suite==='characterization'&&['smoke-final','backpressure-final','deadline-final-v2'].includes(c.report))}
function drawBars(){const m=metrics[metric];byId('metric-label').textContent=m.label+' · '+m.unit;const selected=chosen();byId('bars').innerHTML=selected.map(c=>{const g=c.medians.go||{},r=c.medians.rust||{},gv=g[metric],rv=r[metric],max=Math.max(gv||0,rv||0),n=`${g.runs??0} Go / ${r.runs??0} Rust measured`;const line=(label,value,cls)=>`<div class="bar-row"><span class="small">${label}</span><div class="bar-track"><div class="bar ${cls}" style="width:${max&&value!==null?Math.max(0,Math.min(100,value/max*100)):0}%"></div></div><span class="bar-value">${fmt(value===null||value===undefined?null:value/m.scale)} ${m.unit}</span></div>`;return `<article class="workload"><div class="workload-top"><div><div class="workload-title">${esc(c.case)}</div><div class="note">${esc(n)} · ${c.stdout_sink==='null'?'/dev/null':'pipe'}</div></div><span class="badge ${c.performance_admitted?'pass':'warn'}">${c.performance_admitted?'Admitted':'Descriptive'}</span></div>${line('Go',gv,'go')}${line('Rust',rv,'rust')}</article>`}).join('')||'<div class="empty">No workload samples recorded.</div>';byId('resource-note').textContent='Admitted preserves the raw harness gate. Producer/reader ceilings, imposed pacing and shared-host effects still limit interpretation; these bars are not maximum-throughput claims.'}
function drawTable(){const m=metrics[metric];byId('go-column').textContent='Go ('+m.unit+')';byId('rust-column').textContent='Rust ('+m.unit+')';byId('comparison-table').innerHTML=chosen().map(c=>{const g=c.medians.go||{},r=c.medians.rust||{};const val=v=>v===null||v===undefined?null:v/m.scale;return `<tr><td>${esc(c.case)}</td><td class="num">${fmt(val(g[metric]))}</td><td class="num">${fmt(val(r[metric]))}</td><td class="num">${g.runs??0} / ${r.runs??0}</td></tr>`}).join('')}
function drawSQL(){drawBars();drawTable()}
document.querySelectorAll('[data-metric]').forEach(button=>button.addEventListener('click',()=>{metric=button.dataset.metric;document.querySelectorAll('[data-metric]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));drawSQL()}));byId('suite').addEventListener('change',drawSQL);drawSQL();
byId('validations').innerHTML=D.sql.map(c=>{const v=c.exact_output;let text;if(v.kind==='deadline_partial_prefix')text=`${badge(v.prefix_equal&&v.all_complete_object_prefixes,'4096-byte prefix + framing')}<div class="note">Partial output only</div>`;else if(v.kind==='scheduling_dependent_observation')text='<span class="badge warn">Interruption observation</span>';else text=`${badge(v.passed,'Full digest match')}<div class="note">${v.kind==='separate_pipe_validation_before_null_sink'?'Separate pipe validation':'Captured pipe output'}</div>`;return `<tr><td>${esc(c.case)}<div class="note">${esc(c.report)}</div></td><td>${text}</td><td class="num">${c.observer_bound_runs} / ${c.possibly_producer_bound_runs}</td></tr>`}).join('');
const pacingOnly=i=>i.failures?.length===1&&i.failures[0]==='producer_rate_target';
const I=D.ingest.find(x=>x.report==='ingest-soak-final'), primary=I&&I.follow_timelines[I.primary_follow_timeline], latency=I?.latency||{};
if(I){const status=byId('ingest-status');status.className='badge '+(I.passed?'pass':pacingOnly(I)?'warn':'fail');status.textContent=I.passed?'Soak checks pass':pacingOnly(I)?'Writer schedule missed':'Soak failed / incomplete';byId('ingest-note').textContent=pacingOnly(I)?`All record, checkpoint, resume and cleanup checks passed. The writer's maximum ${fmt(I.max_schedule_lag_seconds*1000,0)} ms schedule lag exceeded its 250 ms limit, so this run is not admitted as steady input at the requested rate. Its observations are retained.`:'';byId('ingest-stats').innerHTML=stat(fmt(I.achieved_rate_over_run,0),'records/s · whole writer run')+stat(fmt(I.unique_events,0),'accepted unique events')+stat(fmt(I.follow_native.peak_rss_bytes===null?null:I.follow_native.peak_rss_bytes/1048576),'MiB · native follow peak RSS')+stat(fmt(latency.p99_upper_ms,0)+' ms','p99 latency · histogram upper bin')+stat(fmt(I.follow_native.cpu_total_ms/I.follow_native.wall_ms*100,1)+'%','average CPU · one core')+stat(fmt(I.follow_native.proc_io_write_bytes/1073741824,2),'GiB charged writes · /proc');const windows=primary?.windows||{};byId('window-table').innerHTML=[['rss_bytes','Current RSS (MiB)',1048576],['fd_count','Open file descriptors',1],['threads','Threads',1]].map(([key,label,scale])=>{const a=windows.first?.metrics?.[key],b=windows.last?.metrics?.[key];return `<tr><td>${label}</td><td class="num">${fmt(a?.median===null||a?.median===undefined?null:a.median/scale)}${a?.method==='bounded_reservoir_approximation'?' ≈':''}</td><td class="num">${fmt(b?.median===null||b?.median===undefined?null:b.median/scale)}${b?.method==='bounded_reservoir_approximation'?' ≈':''}</td></tr>`}).join('')}
function chart(id,points,key,scale,color,unit,hover){const svg=byId(id),W=Math.max(280,Math.round(svg.getBoundingClientRect().width||700)),H=224,L=52,R=16,T=18,B=34;svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.style.height=H+'px';const data=(points||[]).filter(p=>p[key]!==null&&p[key]!==undefined&&Number.isFinite(p[key]));if(!data.length){svg.innerHTML='<text x="50%" y="50%" fill="currentColor" text-anchor="middle" font-size="12">No live samples recorded</text>';return}const end=Math.max(...data.map(p=>p.seconds),1),hi=Math.max(...data.map(p=>p[key]/scale),1),lo=Math.min(0,...data.map(p=>p[key]/scale)),x=t=>L+t/end*(W-L-R),y=v=>H-B-(v-lo)/(hi-lo||1)*(H-T-B);let html='';for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,yy=y(v);html+=`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="var(--line)"/><text x="${L-9}" y="${yy+4}" text-anchor="end" fill="var(--muted)" font-size="11">${fmt(v,v>100?0:1)}</text>`;const t=end*i/4;html+=`<text x="${x(t)}" y="${H-10}" text-anchor="middle" fill="var(--muted)" font-size="11">${fmt(t,0)}s</text>`}html+=`<polyline fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" points="${data.map(p=>x(p.seconds)+','+y(p[key]/scale)).join(' ')}"/><line id="${id}-cursor" x1="0" x2="0" y1="${T}" y2="${H-B}" stroke="var(--muted)" stroke-dasharray="3 3" visibility="hidden"/>`;svg.innerHTML=html;svg.onpointermove=e=>{const rect=svg.getBoundingClientRect(),t=Math.max(0,Math.min(end,((e.clientX-rect.left)/rect.width*W-L)/(W-L-R)*end));let point=data[0];for(const p of data)if(Math.abs(p.seconds-t)<Math.abs(point.seconds-t))point=p;byId(hover).textContent=`${fmt(point.seconds,1)}s · ${fmt(point[key]/scale,1)} ${unit}`;const cursor=byId(id+'-cursor');cursor.setAttribute('x1',x(point.seconds));cursor.setAttribute('x2',x(point.seconds));cursor.setAttribute('visibility','visible')};svg.onpointerleave=()=>byId(id+'-cursor').setAttribute('visibility','hidden')}
function drawCharts(){chart('rss-chart',primary?.points,'rss_bytes',1048576,'var(--mint)','MiB','rss-hover');chart('backlog-chart',I?.backlog_timeline?.points,'backlog_max',1,'var(--amber)','records','backlog-hover')}
drawCharts();let resizePending=false;window.addEventListener('resize',()=>{if(!resizePending){resizePending=true;requestAnimationFrame(()=>{drawCharts();resizePending=false})}});
byId('ingest-runs').innerHTML=D.ingest.map(i=>`<tr><td>${esc(i.report.replace(/^ingest-/,'').replace(/-final(?:-v\d+)?$/,''))}<div class="note">${fmt(i.duration_seconds,0)}s · ${esc(i.shape||'')} · ${fmt(i.record_bytes,0)} bytes</div></td><td class="num">${fmt(i.unique_events,0)}</td><td class="num">${fmt(i.duplicate_events,0)}</td><td class="num">${fmt(i.retry_matches,0)} / ${fmt(i.retry_checks,0)}</td><td>${pacingOnly(i)?'<span class="badge warn">Writer pacing</span>':badge(i.passed)}</td></tr>`).join('');
byId('coverage').innerHTML=D.reports.map(r=>`<a href="${esc(r.raw_href)}"><span>${esc(r.name)}</span>${badge(r.complete===true?r.passed:null,r.passed?'Pass':'Fail')}</a>`).join('')+D.excluded_runs.map(r=>`<a href="${esc(r.raw_href)}" title="${esc(r.reason)}"><span>${esc(r.name)} · excluded setup trial</span><span class="badge warn">Coverage miss</span></a>`).join('');byId('limitations').innerHTML=D.limitations.map(item=>`<li>${esc(item)}</li>`).join('');byId('hashes').innerHTML=[...D.reports,...D.excluded_runs].map(r=>`<div class="hash-row"><div class="small">${esc(r.raw_path)}</div><code>${r.sha256}</code></div>`).join('');
byId('excluded-note').textContent=D.excluded_runs.length?'The original split trial did not trigger its 413 threshold. It remains linked as a coverage setup miss, separate from the final split validation; this is not evidence of a CLI failure.':'';
console.log(JSON.stringify({sqlCases:D.sql.length,ingestCases:D.ingest.length,overflow:document.documentElement.scrollWidth>window.innerWidth}));
</script></body></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--reports-root", type=Path)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    root, out = args.root.resolve(), args.out.resolve()
    reports_root = args.reports_root.resolve() if args.reports_root else root / "harness/results/stress"
    try:
        result = build(root, reports_root, out)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Cannot generate CLI stress evidence: {error}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    inline = json.dumps(result, separators=(",", ":"), allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    (out / "results.html").write_text(HTML.replace("__RESULTS_DATA__", inline))
    print(f"Wrote {relative(out / 'results.json', root)} and {relative(out / 'results.html', root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
