#!/usr/bin/env python3
"""Controlled SQL attribution experiments; never rank instrumented builds as releases."""
import argparse
import copy
import json
import gzip
import math
import os
import subprocess
from pathlib import Path
import random
import shutil
import statistics
import sys

import manifest
import phase3_workloads
import run

DEFAULT_CASES = ("p3-1-json", "p3-100000-json", "p3-100000-text",
                 "p3-single-8MiB-item", "p3-single-8MiB-text", "p3-leading-1MiB-whitespace")
DIAGNOSTIC_CASES = {"p3-100000-json", "p3-single-8MiB-item", "p3-leading-1MiB-whitespace"}
GO_STAGES = {"renderer", "request_headers", "body_read", "decode", "marshal_indent", "indent_copy", "decoder_control", "output_write"}
RUST_STAGES = {"scan_framing", "ordered_decode", "value_conversion", "serialization", "indentation_copy", "output_write", "output_flush", "drop", "body_wait", "request_and_headers", "runtime_pump"}
ALLOC_FIELDS = ("alloc_calls", "alloc_requested_bytes", "realloc_calls", "realloc_requested_bytes", "dealloc_calls", "dealloc_layout_bytes")


def integers(data, keys, signed=()):
    if not isinstance(data, dict):
        raise ValueError("expected diagnostic object")
    for key in keys:
        value = data.get(key)
        if type(value) is not int or (key not in signed and value < 0):
            raise ValueError(f"invalid or missing integer: {key}")


def strings(data, keys):
    for key in keys:
        if not isinstance(data.get(key), str) or not data[key]:
            raise ValueError(f"missing scope metadata: {key}")


MODES = ("disabled", "timing", "timing-sampled", "allocations", "cpu")


def workload_cases(names):
    cases = {c["name"]: c for c in phase3_workloads.suite()}
    twin = copy.deepcopy(cases["p3-single-8MiB-item"])
    twin["name"] = "p3-single-8MiB-text"
    twin["argv"][twin["argv"].index("json")] = "text"
    cases[twin["name"]] = twin
    unknown = set(names) - cases.keys()
    if unknown:
        raise ValueError(f"unknown cases: {sorted(unknown)}")
    return [cases[n] for n in names]


def sidecar(directory, language, mode, case, sample):
    """Reject absent, partial or inconsistent measurements, never turn them into zeros."""
    paths = list(directory.glob("go.json" if language == "go" else "pb-diagnostics-*.json"))
    if len(paths) != 1 or paths[0].is_symlink() or not paths[0].is_file():
        raise ValueError("expected one regular diagnostic sidecar")
    if paths[0].stat().st_size > 2 << 20:
        raise ValueError("diagnostic sidecar exceeds 2 MiB")
    data = json.loads(paths[0].read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON number")))
    if not isinstance(data, dict):
        raise ValueError("expected diagnostic object")
    if data.get("schema_version") != 1 or data.get("complete") is not True:
        raise ValueError("incomplete or unsupported diagnostic sidecar")
    if data.get("language", data.get("implementation")) != language:
        raise ValueError("wrong diagnostic implementation")
    expected_mode = ("timing_allocations" if mode == "allocations" else "timing") if language == "rust" else ("timing" if mode == "timing-sampled" else mode)
    if data.get("mode") != expected_mode:
        raise ValueError("wrong diagnostic mode")
    expected_sample = 64 if mode == "timing-sampled" else 1
    if language == "go":
        integers(data, ("command_wall_ns", "process_cpu_ns", "sample_every", "profile_flush_ns"))
        strings(data, ("sampling_semantics", "timing_semantics", "measurement_scope", "profile_scope"))
        if data["sample_every"] != expected_sample or set(data.get("stages", {})) != GO_STAGES:
            raise ValueError("wrong sampling rate or missing stages")
        integers(data.get("output"), ("logical_write_calls", "bytes_requested", "bytes_written", "write_errors"))
        integers(data.get("input"), ("logical_read_calls", "bytes_read"))
        if data["input"]["bytes_read"] != case["fixture_response_body_bytes"]:
            raise ValueError("diagnostic input byte mismatch")
        if data["stages"]["decode"].get("calls") != case["rows"]:
            raise ValueError("diagnostic element count mismatch")
        if mode == "allocations":
            integers(data.get("allocations"), ("total_alloc_bytes", "mallocs", "frees", "gc_cycles", "gc_pause_ns", "heap_alloc_delta_bytes"), signed=("heap_alloc_delta_bytes",))
            integers(data, ("allocation_profile_rate_bytes", "allocation_profile_startup_rate_bytes"))
            if data["allocation_profile_rate_bytes"] != 524288:
                raise ValueError("wrong allocation profile rate")
        if data.get("command_wall_ns", 0) <= 0 or data.get("process_cpu_ns", -1) < 0:
            raise ValueError("missing command timing")
        if data.get("output", {}).get("bytes_written") != sample["stdout_bytes"]:
            raise ValueError("diagnostic output byte mismatch")
        for stage in data["stages"].values():
            integers(stage, ("calls", "timed_calls", "wall_ns"))
            if type(stage.get("inclusive")) is not bool:
                raise ValueError("missing stage inclusiveness")
            if not 0 <= stage["timed_calls"] <= stage["calls"] or stage["wall_ns"] < 0:
                raise ValueError("invalid stage sample counts")
        if mode in ("allocations", "cpu"):
            profile = directory / ("go-allocs.pprof" if mode == "allocations" else "go-cpu.pprof")
            if profile.is_symlink() or not profile.is_file() or profile.stat().st_size == 0:
                raise ValueError("missing native profile")
            with gzip.open(profile, "rb") as handle:
                if not handle.read(1):
                    raise ValueError("empty compressed profile")
    else:
        measurement = data.get("measurement", {})
        integers(measurement, ("elapsed_wall_ns", "sample_every"))
        strings(measurement, ("thread_cpu_clock", "stage_times", "thread_spans", "sidecar_io", "allocation_semantics", "writer_calls", "input_bytes", "sampling"))
        if measurement["sample_every"] != expected_sample:
            raise ValueError("wrong sampling rate")
        if data.get("exit_code") != 0:
            raise ValueError("diagnostic invocation failed")
        threads = data.get("threads", [])
        renderer = [t for t in threads if t.get("role") == "sql_renderer"]
        if len(renderer) != 1:
            raise ValueError("missing renderer thread")
        counters = renderer[0]["counters"]
        if counters["elements"] != case["rows"]:
            raise ValueError("diagnostic element count mismatch")
        if counters["input_consumed_bytes"] != case["fixture_response_body_bytes"]:
            raise ValueError("diagnostic input byte mismatch")
        if sum(t["counters"]["write_completed_bytes"] for t in threads) != sample["stdout_bytes"]:
            raise ValueError("diagnostic output byte mismatch")
        if sorted(t.get("role", "") for t in threads) != ["main", "sql_renderer"]:
            raise ValueError("unexpected thread roles")
        for thread in threads:
            integers(thread, ("span_wall_ns",))
            if measurement["thread_cpu_clock"] != "unavailable":
                integers(thread, ("span_cpu_ns",))
            integers(thread.get("counters"), ("input_consumed_bytes", "elements", "element_input_bytes", "serialized_bytes", "indented_bytes", "write_calls", "write_attempted_bytes", "write_completed_bytes", "flush_calls"))
            stages = thread["stages"]
            if len(stages) != len(RUST_STAGES) or {s.get("name") for s in stages} != RUST_STAGES:
                raise ValueError("missing stages")
            if mode == "allocations":
                integers(thread.get("allocations"), ALLOC_FIELDS)
            if sum(s["wall_ns"] for s in stages) > thread["span_wall_ns"] * 1.01 + 100_000:
                raise ValueError("exclusive stages exceed thread span")
            for stage in stages:
                integers(stage, ("calls", "timed_calls", "wall_ns", "cpu_ns"))
                if mode == "allocations" and stage["timed_calls"] > 0:
                    integers(stage.get("allocations"), ALLOC_FIELDS)
                if not 0 <= stage["timed_calls"] <= stage["calls"] or stage["wall_ns"] < 0:
                    raise ValueError("invalid stage sample counts")
    return data


def diagnostic_env(directory, language, mode):
    if mode == "disabled":
        return {}
    env = {"PB_DIAGNOSTICS_DIR": str(directory), "PB_DIAGNOSTICS_SAMPLE_EVERY": "64" if mode == "timing-sampled" else "1"}
    if language == "go":
        env["PB_DIAGNOSTICS_MODE"] = "timing" if mode == "timing-sampled" else mode
    elif mode == "allocations":
        env["PB_DIAGNOSTICS_ALLOCATIONS"] = "1"
    return env


def variants(case, modes, sinks):
    result = [(language, "release", sink) for language in ("go", "rust") for sink in sinks]
    if case["name"] in DIAGNOSTIC_CASES:
        result += [(language, mode, "pipe") for language in ("go", "rust") for mode in modes if not (language == "rust" and mode == "cpu")]
    return result


def aggregate(report):
    summaries, checks, overhead = {}, [], []
    for name, case in report["cases"].items():
        groups = {}
        for sample in case["samples"]:
            if not sample["warmup"]:
                key = f'{sample["language"]}/{sample["measurement_class"]}/{sample["stdout_sink"]}'
                groups.setdefault(key, []).append(sample)
        summaries[name] = {key: run.summarize(values) for key, values in groups.items()}
        references = [s for s in case["samples"] if s["language"] == "go" and s["measurement_class"] == "release"]
        valid = bool(references) and all(s["valid"] and s["contract"] == references[0]["contract"] for s in case["samples"])
        checks.append({"case": name, "compatible": valid, "samples_including_warmup": len(case["samples"])})
        for language in ("go", "rust"):
            baseline = groups.get(f"{language}/release/pipe", [])
            disabled = groups.get(f"{language}/disabled/pipe", [])
            for key, values in groups.items():
                if not key.startswith(language + "/") or key.endswith("/file") or "/release/" in key:
                    continue
                control = baseline if "/disabled/" in key else disabled
                comparison = run.compare_samples(control, values)
                overhead.append({"case": name, "variant": key, "control": "release" if "/disabled/" in key else "disabled", "purpose": "instrumentation overhead, not language speed comparison", **comparison})
    report.update(summaries=summaries, compatibility=checks, instrumentation_overhead=overhead,
                  valid=all(c["compatible"] for c in checks))


def render(report, out):
    lines = ["# CLI performance attribution", "", "Release controls alone compare languages. Diagnostic modes explain stages and have their own overhead; do not rank them against releases.", "", "| Workload | Build / mode / sink | N | Wall ms | CPU ms | RSS MiB |", "|---|---|---:|---:|---:|---:|"]
    for case, groups in report["summaries"].items():
        for key, summary in groups.items():
            m = summary["metrics"]
            value = lambda k, scale=1: f'{m[k]["median"] / scale:.2f}' if k in m else "N/A"
            lines.append(f'| {case} | {key} | {summary["valid_samples"]} | {value("wall_ms")} | {value("cpu_ms")} | {value("peak_rss_bytes", 1 << 20)} |')
    lines += ["", "## Instrumentation overhead", "", "Positive percentages mean added wall time; estimates from few samples are advisory.", "", "| Workload | Variant | Control | Change |", "|---|---|---|---:|"]
    for item in report["instrumentation_overhead"]:
        value = f'{item["percent_change"]:+.1f}%' if "percent_change" in item else "invalid/unavailable"
        lines.append(f'| {item["case"]} | {item["variant"]} | {item["control"]} | {value} |')
    lines += ["", "Sidecars in report.json and samples/*/diagnostics retain timing, counters, allocation scope and native Go profiles.", "No stage totals are extrapolated from samples or compared across languages. File output uses the temporary directory filesystem/page cache, not a null sink. No NIC/TLS bytes or durable-storage latency are inferred.", "", f'All contracts and sidecars valid: {report["valid"]}', ""]
    (out / "report.md").write_text("\n".join(lines))


def artifact_dir(root, name):
    path = Path(name)
    root = Path(root).resolve()
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("artifact path must be relative without parent traversal")
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError("artifact path escapes its root")
    return target


def decode_profiles(report, out, go_tool, artifact_root=None):
    """Decode only after every measurement; profiler analysis cannot warm the next sample."""
    artifact_root = artifact_root or out
    tool_env = {**os.environ, "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off",
                "TMPDIR": str(run.HERE.parent / "target/tmp"), "GOTMPDIR": str(run.HERE.parent / "target/tmp")}
    for case in report["cases"].values():
        for sample in case["samples"]:
            mode = sample["measurement_class"]
            if sample["language"] != "go" or mode not in ("allocations", "cpu") or not sample["valid"]:
                continue
            directory = artifact_dir(out, sample["artifact"]) / "diagnostics"
            directory.mkdir(parents=True, exist_ok=True)
            profile = artifact_dir(artifact_root, sample["artifact"]) / "diagnostics" / ("go-allocs.pprof" if mode == "allocations" else "go-cpu.pprof")
            command = [go_tool, "tool", "pprof", "-top", "-nodecount=30", "-sample_index=" + ("alloc_space" if mode == "allocations" else "cpu"), str(profile)]
            try:
                if profile.is_symlink():
                    raise ValueError("native profile must not be a symlink")
                identity = manifest.file_identity(profile)
                expected = sample.get("native_profile_identity")
                if expected and identity["sha256"] != expected["sha256"]:
                    raise ValueError("native profile hash changed since collection or prior analysis")
                sample.setdefault("native_profile_hash_scope", "first recorded during analysis; no collection-time hash available")
                sample["native_profile_identity"] = identity
                parsed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                                        env=tool_env)
                (directory / "pprof-top.txt").write_bytes(parsed.stdout)
                (directory / "pprof-stderr.txt").write_bytes(parsed.stderr)
                if parsed.returncode:
                    raise ValueError("go tool pprof could not decode native profile")
                sample["native_profile_decoded"] = True
                if manifest.file_identity(profile)["sha256"] != identity["sha256"]:
                    raise ValueError("native profile changed during analysis")
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                sample["valid"] = False
                sample["issues"].append("diagnostics: " + str(error))
            run.write_json(artifact_dir(out, sample["artifact"]) / "sample.json", sample)


def reanalyze(report_path, out, go_tool):
    """Retry only failed external profile decoding; original measurements stay immutable."""
    report_path = Path(report_path).resolve()
    report = json.loads(report_path.read_text())
    if report.get("schema_version") != 1 or "instrumentation_overhead" not in report:
        raise ValueError("not a completed diagnostic report")
    out = Path(out).resolve()
    original_root = Path(report.get("reanalysis", {}).get("original_artifact_root", report_path.parent)).resolve()
    for case in report["cases"].values():
        for sample in case["samples"]:
            artifact_dir(out, sample["artifact"])
            artifact_dir(original_root, sample["artifact"])
    out.mkdir(parents=True, exist_ok=False)
    report["reanalysis"] = {"source_report": manifest.file_identity(report_path),
                            "original_artifact_root": str(original_root),
                            "analysis_runner": manifest.file_identity(__file__), "go_tool": go_tool,
                            "scope": "profile decoding only; no invocations rerun and no other failures cleared"}
    for case in report["cases"].values():
        for sample in case["samples"]:
            failure = "diagnostics: go tool pprof could not decode native profile"
            if failure in sample["issues"]:
                sample["issues"].remove(failure)
                sample["valid"] = not sample["issues"]
    decode_profiles(report, out, go_tool, artifact_root=original_root)
    aggregate(report)
    report["valid"] = report["valid"] and report["identities_unchanged"]
    run.write_json(out / "report.json", report)
    render(report, out)
    return 0 if report["valid"] else 1


def execute(args):
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if "pipe" not in args.sink:
        raise ValueError("diagnostic overhead requires a release pipe control")
    if args.repetitions < 1 or args.diagnostic_repetitions < 1 or args.warmup < 0:
        raise ValueError("repetitions must be positive and warmup nonnegative")
    if "disabled" not in args.mode and any(m != "disabled" for m in args.mode):
        raise ValueError("enabled modes require disabled instrumentation controls")
    out = Path(args.out).resolve()
    binaries = {"go": run.binary_info(args.go), "rust": run.binary_info(args.rust),
                "go-diagnostics": run.binary_info(args.go_diagnostics), "rust-diagnostics": run.binary_info(args.rust_diagnostics)}
    fixture, meter = run.binary_info(args.fixture), run.binary_info(args.meter)
    cases = workload_cases(args.case or DEFAULT_CASES)
    (run.HERE.parent / "target/tmp").mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "host_before": run.host_metadata(), "binaries": binaries,
              "tools": {"fixture": fixture, "meter": meter, "runner": manifest.file_identity(__file__),
                        "collector": manifest.file_identity(run.__file__)},
              "external_profilers": {name: shutil.which(name) for name in ("perf", "strace", "heaptrack", "valgrind", "samply")},
              "parameters": vars(args), "scope": "synthetic local HTTP, fresh processes and configs, warm caches; sequential seeded round order; full invocation meter includes diagnostic setup/flush; sidecars label narrower scopes",
              "cases": {}, "order": []}
    run.write_json(out / "workloads.json", cases)
    report["tools"]["workloads"] = manifest.file_identity(out / "workloads.json")
    report["tools"]["workload_generator"] = manifest.file_identity(phase3_workloads.__file__)
    report["build_provenance"] = "not supplied; binary identities alone do not establish build flags or source"
    if args.provenance:
        provenance = json.loads(Path(args.provenance).read_text())
        for label, info in binaries.items():
            if provenance.get("binaries", {}).get(label, {}).get("sha256") != info["sha256"]:
                raise ValueError("build provenance does not match " + label)
        run.write_json(out / "provenance.json", provenance)
        report["build_provenance"] = manifest.file_identity(out / "provenance.json")
    report["go_overlay_build"] = "not supplied"
    go_build = Path(args.go_build_manifest)
    if go_build.is_file():
        build = json.loads(go_build.read_text())
        if build.get("binary_sha256") != binaries["go-diagnostics"]["sha256"]:
            raise ValueError("Go diagnostic build metadata does not match binary")
        run.write_json(out / "go-build.json", build)
        report["go_overlay_build"] = manifest.file_identity(out / "go-build.json")
    rng = random.Random(args.seed)
    for case in cases:
        report["cases"][case["name"]] = {"input_sha256": run.digest(case["fixture"]["routes"][0]["body"].encode()), "argv": case["argv"], "samples": []}
        combinations = variants(case, args.mode, args.sink)
        for round_number in range(-args.warmup, max(args.repetitions, args.diagnostic_repetitions)):
            jobs = [v for v in combinations if round_number < (args.repetitions if v[1] == "release" else args.diagnostic_repetitions)]
            rng.shuffle(jobs)
            for language, mode, sink in jobs:
                order = len(report["order"])
                folder = out / "samples" / f"{order:04d}-{case['name']}-{language}-{mode}-{sink}"
                directory = folder / "diagnostics"
                directory.mkdir(parents=True)
                binary = binaries[language if mode == "release" else language + "-diagnostics"]["path"]
                print(f"{order:04d} {case['name']} {language}/{mode}/{sink} round={round_number}", flush=True)
                sample = run.sample(binary, case, fixture["path"], folder / "capture" if round_number == 0 else None,
                                    args.timeout, meter=meter["path"], stdout_sink=sink,
                                    diagnostic_env={} if mode == "release" else diagnostic_env(directory, language, mode))
                sample.update(language=language, measurement_class=mode, round=round_number, warmup=round_number < 0,
                              order_index=order, artifact=str(folder.relative_to(out)))
                if mode not in ("release", "disabled"):
                    try:
                        sample["diagnostics"] = sidecar(directory, language, mode, case, sample)
                        if language == "go" and mode in ("allocations", "cpu"):
                            profile = directory / ("go-allocs.pprof" if mode == "allocations" else "go-cpu.pprof")
                            sample["native_profile_identity"] = manifest.file_identity(profile)
                            sample["native_profile_hash_scope"] = "recorded after collection before external decoding"
                    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired) as error:
                        sample["valid"] = False
                        sample["issues"].append("diagnostics: " + str(error))
                run.write_json(folder / "sample.json", sample)
                report["cases"][case["name"]]["samples"].append(sample)
                report["order"].append({"case": case["name"], "language": language, "mode": mode, "sink": sink, "round": round_number})
                run.write_json(out / "partial.json", report)
    report["host_after"] = run.host_metadata()
    report["identities_unchanged"] = all(run.binary_info(info["path"]) == info for info in [*binaries.values(), fixture, meter])
    decode_profiles(report, out, args.go_tool)
    aggregate(report)
    report["valid"] = report["valid"] and report["identities_unchanged"]
    run.write_json(out / "report.json", report)
    render(report, out)
    return 0 if report["valid"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reanalyze", help="retry external profile decoding from an existing report into a new --out directory")
    parser.add_argument("--provenance", help="manifest.py output with go/rust/go-diagnostics/rust-diagnostics binary labels")
    parser.add_argument("--go-build-manifest", default="target/go-diagnostics-overlay/build.json")
    parser.add_argument("--go-tool", default="go", help="installed Go tool executable for profile decoding; GOTOOLCHAIN=local forbids toolchain downloads")
    parser.add_argument("--go", default="bin/pb-go")
    parser.add_argument("--rust", default="target/release/pb")
    parser.add_argument("--go-diagnostics", default="bin/pb-go-diagnostics")
    parser.add_argument("--rust-diagnostics", default="target/diagnostics/release/pb")
    parser.add_argument("--fixture", default="bin/pb-fixture")
    parser.add_argument("--meter", default="bin/pb-meter")
    parser.add_argument("--out", required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--mode", action="append", choices=MODES)
    parser.add_argument("--sink", action="append", choices=("pipe", "file"))
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--diagnostic-repetitions", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--seed", type=int, default=20261013)
    args = parser.parse_args()
    args.mode = list(dict.fromkeys(args.mode or MODES))
    args.sink = list(dict.fromkeys(args.sink or ("pipe", "file")))
    try:
        if args.reanalyze:
            return reanalyze(args.reanalyze, args.out, args.go_tool)
        return execute(args)
    except (OSError, ValueError) as error:
        parser.exit(1, f"diagnose: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
