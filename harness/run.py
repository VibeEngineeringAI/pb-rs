#!/usr/bin/env python3
"""Process-level compatibility/performance harness. Python 3.11+, Linux/macOS."""

import argparse
import base64
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import selectors
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import tomllib
from types import SimpleNamespace
import urllib.parse
import urllib.request

from cases import suite


HERE = Path(__file__).resolve().parent
DEFAULT_METER = HERE.parent / "bin" / "pb-meter"
SCHEMA_VERSION = 1
METRICS = ("wall_ms", "first_stdout_ms", "first_stderr_ms", "first_record_ms",
           "last_stdout_ms", "stdout_span_ms", "first_request_ms", "request_to_stdout_ms",
           "cpu_user_ms", "cpu_system_ms", "cpu_ms",
           "peak_rss_bytes", "minor_faults", "major_faults",
           "voluntary_context_switches", "involuntary_context_switches",
           "stdout_bytes", "stderr_bytes", "output_mib_per_second", "rows_per_second")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n")


def clock_ns():
    # Use the exact POSIX clock used by meter.c, including on macOS.
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def normalize(data, replacements):
    # Only explicitly identified per-run paths/endpoints are normalized.
    for original, replacement in sorted(replacements.items(), key=lambda item: -len(item[0])):
        data = data.replace(original.encode(), replacement.encode())
    return data


def isolated_env(root):
    return {"PATH": os.defpath, "HOME": str(root), "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"), "TMPDIR": str(root / "tmp"),
            "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "TERM": "dumb",
            "PB_THEME": "dark", "PB_ANALYTICS": "disable"}


def seed_config(root, url, auth="basic"):
    folder = root / "config" / "pb"
    folder.mkdir(parents=True)
    credentials = {
        "basic": 'username = "harness"\npassword = "harness"\ncloud = false\n',
        "api-key": 'apiKey = "fixture-key"\ncloud = false\n',
        "cloud-key": 'apiKey = "fixture-key"\ncloud = true\ntenant_id = "fixture-tenant"\n',
        "cloud-cookie": 'session_token = "fixture-session"\ncloud = true\ntenant_id = "fixture-tenant"\n',
    }
    config = folder / "config.toml"
    config.write_text('DefaultProfile = "fixture"\n[Profiles.fixture]\nurl = ' + json.dumps(url) + "\n" + credentials[auth])
    config.chmod(0o600)


def config_snapshot(root, replacements):
    files = {}
    for path in sorted((root / "config").rglob("*")):
        if path.is_file():
            content = normalize(path.read_bytes(), replacements)
            try:
                value = {"toml": tomllib.loads(content.decode())}
            except (ValueError, UnicodeError):
                value = {"bytes_base64": base64.b64encode(content).decode()}
            files[str(path.relative_to(root))] = {"mode": oct(path.stat().st_mode & 0o777), **value}
    return files


def kill_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def spawn_measured(argv, env, cwd):
    # Start a fresh minimal native supervisor. Its subsequent fork/exec charges
    # child resources to the CLI instead of inheriting Python's memory footprint.
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    actions = [(os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
               (os.POSIX_SPAWN_DUP2, out_w, 1), (os.POSIX_SPAWN_DUP2, err_w, 2)]
    actions += [(os.POSIX_SPAWN_CLOSE, fd) for fd in (out_r, out_w, err_r, err_w)]
    previous = os.getcwd()
    try:
        # Runner is deliberately single-threaded; fixture runs in another process.
        # Python 3.11 posix_spawn has no cwd argument.
        os.chdir(cwd)
        defaults = tuple(getattr(signal, name) for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ") if hasattr(signal, name))
        pid = os.posix_spawn(argv[0], argv, env, file_actions=actions, setsid=True,
                             setsigdef=defaults, setsigmask=())
    except BaseException:
        os.close(out_r)
        os.close(err_r)
        raise
    finally:
        os.chdir(previous)
        os.close(out_w)
        os.close(err_w)
    return SimpleNamespace(pid=pid, stdout=os.fdopen(out_r, "rb", buffering=0),
                           stderr=os.fdopen(err_r, "rb", buffering=0), returncode=None)


def measure(argv, env, cwd, timeout=10, max_output=32 << 20, signal_after_lines=None,
            read_delay_ms=0, close_stdout_after_lines=None, meter=DEFAULT_METER, track_records=False):
    """Drain both pipes while the native meter reaps the actual CLI with wait4.

    Never use cumulative RUSAGE_CHILDREN: its RSS high-water mark is not a
    per-command measurement. Fixture creation and artifact writes are outside
    this interval. First-byte times are parent-observed pipe availability.
    """
    started_unix_ns = time.time_ns()
    started = clock_ns()
    metrics_path = Path(cwd) / "meter.json"
    proc = spawn_measured([str(meter), str(metrics_path), "--", *argv], env, cwd)
    selector = selectors.DefaultSelector()
    data = {"stdout": bytearray(), "stderr": bytearray()}
    first = {"stdout": None, "stderr": None}
    first_record = None
    last_stdout = None
    pending_line = b""
    lines = 0
    sent_signal = False
    failure = None
    exited = None
    usage = None
    status = None
    paused_until = 0
    stdout_paused = False
    for name, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        os.set_blocking(pipe.fileno(), False)
        selector.register(pipe, selectors.EVENT_READ, name)
    try:
        while status is None or selector.get_map() or stdout_paused:
            now = clock_ns()
            if status is None:
                pid, raw_status, stats = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    status, usage, exited = raw_status, stats, now
                    proc.returncode = os.waitstatus_to_exitcode(status)
            if now - started > timeout * 1e9 and failure is None:
                failure = "timeout"
                kill_group(proc, signal.SIGKILL)
            if exited is not None and now - started > timeout * 1e9:
                if selector.get_map() or stdout_paused:
                    failure = failure or "descendant-held-pipe"
                    kill_group(proc, signal.SIGKILL)
                    break
            if stdout_paused and now >= paused_until:
                selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
                stdout_paused = False
            wait = 0.001 if not selector.get_map() else 0.01
            for key, _ in selector.select(wait):
                name = key.data
                chunk = os.read(key.fd, 65536)
                observed = clock_ns()
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if first[name] is None:
                    first[name] = (observed - started) / 1e6
                if name == "stdout":
                    last_stdout = (observed - started) / 1e6
                data[name].extend(chunk)
                if sum(map(len, data.values())) > max_output:
                    failure = "output-limit"
                    kill_group(proc, signal.SIGKILL)
                if name == "stdout" and (track_records or signal_after_lines or close_stdout_after_lines):
                    pending_line += chunk
                    complete = pending_line.split(b"\n")
                    pending_line = complete.pop()
                    nonblank = sum(bool(line.strip()) for line in complete)
                    lines += nonblank
                    if first_record is None and nonblank:
                        first_record = (observed - started) / 1e6
                    if signal_after_lines and lines >= signal_after_lines and not sent_signal:
                        kill_group(proc, signal.SIGINT)
                        sent_signal = True
                if name == "stdout" and close_stdout_after_lines and lines >= close_stdout_after_lines:
                    selector.unregister(key.fileobj)
                    proc.stdout.close()
                elif name == "stdout" and read_delay_ms:
                    selector.unregister(key.fileobj)
                    stdout_paused = True
                    paused_until = observed + read_delay_ms * 1_000_000
    finally:
        if status is None:
            kill_group(proc, signal.SIGKILL)
            _, status, usage = os.wait4(proc.pid, 0)
            exited = clock_ns()
            proc.returncode = os.waitstatus_to_exitcode(status)
        # Also remove accidental descendants after normal parent termination.
        kill_group(proc, signal.SIGKILL)
        selector.close()
        proc.stdout.close()
        proc.stderr.close()
    wall = (exited - started) / 1e6
    result = {
        "wall_ms": wall, "first_stdout_ms": first["stdout"], "first_stderr_ms": first["stderr"],
        "first_record_ms": first_record,
        "last_stdout_ms": last_stdout,
        "stdout_span_ms": last_stdout - first["stdout"] if last_stdout is not None else None,
        "cpu_user_ms": usage.ru_utime * 1000, "cpu_system_ms": usage.ru_stime * 1000,
        "cpu_ms": (usage.ru_utime + usage.ru_stime) * 1000,
        "peak_rss_bytes": usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024),
        "minor_faults": usage.ru_minflt, "major_faults": usage.ru_majflt,
        "voluntary_context_switches": usage.ru_nvcsw, "involuntary_context_switches": usage.ru_nivcsw,
        "stdout_bytes": len(data["stdout"]), "stderr_bytes": len(data["stderr"]),
        "output_mib_per_second": len(data["stdout"]) / (1 << 20) / (wall / 1000),
        "started_unix_ns": started_unix_ns, "exit_code": proc.returncode,
        "termination": {"signal": os.WTERMSIG(status)} if os.WIFSIGNALED(status) else {"exit": os.WEXITSTATUS(status)},
        "failure": failure, "signal_sent": sent_signal,
    }
    if metrics_path.exists():
        measured = json.loads(metrics_path.read_text())
        offset_ms = (measured["started_monotonic_ns"] - started) / 1e6
        for key in ("first_stdout_ms", "first_stderr_ms", "first_record_ms", "last_stdout_ms"):
            if result[key] is not None:
                result[key] -= offset_ms
        result.update({key: value for key, value in measured.items() if not key.endswith("monotonic_ns")})
        result["wall_ms"] = (measured["ended_monotonic_ns"] - measured["started_monotonic_ns"]) / 1e6
        result["cpu_ms"] = measured["cpu_user_ms"] + measured["cpu_system_ms"]
        result["output_mib_per_second"] = len(data["stdout"]) / (1 << 20) / (result["wall_ms"] / 1000)
        result["supervisor_startup_ms"] = offset_ms
    else:
        result["failure"] = result["failure"] or "meter did not produce resource measurements"
    return result, bytes(data["stdout"]), bytes(data["stderr"])


class Fixture:
    def __init__(self, binary, scenario, root):
        self.proc = None
        self.url = "http://127.0.0.1:1"
        self.port = None
        self.root = root
        if scenario is None:
            return
        if not binary:
            raise ValueError("HTTP/Flight cases require --fixture (build harness/fixture first)")
        path = root / "scenario.json"
        write_json(path, scenario)
        self.log = (root / "fixture.stderr").open("wb")
        self.proc = subprocess.Popen([str(binary), "--scenario", str(path)], stdout=subprocess.PIPE,
                                     stderr=self.log, start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise RuntimeError("fixture did not become ready within 10s")
            info = json.loads(self.proc.stdout.readline())
            self.url, self.port = info["url"], info["grpc_port"]
        except Exception as error:
            self.close()
            details = (root / "fixture.stderr").read_text(errors="replace")[:4096]
            raise RuntimeError(f"fixture startup failed: {details or error}") from error

    def trace(self):
        if self.proc is None:
            return []
        # Ignore the operator's proxy variables even for the control request.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(self.url + "/__harness/trace", timeout=5) as response:
            return json.load(response)

    def close(self):
        if self.proc:
            kill_group(self.proc, signal.SIGKILL)
            self.proc.wait()
            self.proc.stdout.close()
            self.log.close()


def request_contract(trace):
    result = []
    for request in trace:
        item = {k: v for k, v in request.items() if k != "time_unix_ns"}
        parsed = urllib.parse.urlsplit(item["path"])
        item["path"] = parsed.path
        query = {}
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            query.setdefault(key, []).append(value)
        item["query"] = query
        try:
            item["body"] = {"json": json.loads(item["body"], parse_float=str)}
        except ValueError:
            item["body"] = {"raw": item["body"]}
        result.append(item)
    return result


def sample(binary, case, fixture_binary, artifact, timeout, read_delay_ms=0, meter=DEFAULT_METER):
    with tempfile.TemporaryDirectory(prefix="pb-harness-") as temp:
        root = Path(temp)
        (root / "tmp").mkdir()
        (root / "config").mkdir()
        fixture = Fixture(fixture_binary, case.get("fixture"), root)
        try:
            if case.get("profile"):
                seed_config(root, fixture.url, case.get("auth", "basic"))
            replacements = {str(root): "<HOME>", fixture.url: "http://<FIXTURE>"}
            before = config_snapshot(root, replacements)
            result, stdout, stderr = measure([str(binary), *case["argv"]], isolated_env(root), root,
                                            timeout=timeout, signal_after_lines=case.get("signal_after_lines"),
                                            read_delay_ms=read_delay_ms,
                                            close_stdout_after_lines=case.get("close_stdout_after_lines"), meter=meter,
                                            track_records="jsonl_rows" in case)
            trace = fixture.trace()
            result["first_request_ms"] = ((trace[0]["time_unix_ns"] - result["started_unix_ns"]) / 1e6) if trace else None
            result["request_to_stdout_ms"] = (result["first_stdout_ms"] - result["first_request_ms"]) if trace and result["first_stdout_ms"] is not None else None
            result["rows_per_second"] = case["rows"] / (result["wall_ms"] / 1000) if case.get("rows") else None
            if "jsonl_rows" not in case:
                result["first_record_ms"] = None
            issues = []
            if result["failure"]:
                issues.append(result["failure"])
            if result["exit_code"] != case["expected_exit"]:
                issues.append(f"exit {result['exit_code']}, expected {case['expected_exit']}")
            actual_requests = [r["method"] + " " + urllib.parse.urlsplit(r["path"]).path for r in trace]
            if actual_requests != case["expected_requests"]:
                issues.append(f"requests {actual_requests!r}, expected {case['expected_requests']!r}")
            if any(r["unexpected"] for r in trace):
                issues.append("unexpected fixture request")
            if "jsonl_rows" in case:
                try:
                    rows = [json.loads(line) for line in stdout.splitlines() if line.strip()]
                    if len(rows) != case["jsonl_rows"] or [row.get("sequence") for row in rows] != list(range(case["jsonl_rows"])):
                        issues.append("Flight row count/order mismatch")
                except (ValueError, AttributeError):
                    issues.append("invalid Flight JSONL")
            normalized_out = normalize(stdout, replacements)
            normalized_err = normalize(stderr, replacements)
            contract = {"stdout_sha256": digest(normalized_out), "stderr_sha256": digest(normalized_err),
                        "termination": result["termination"], "requests": request_contract(trace),
                        "config_before": before, "config_after": config_snapshot(root, replacements)}
            result["valid"] = not issues
            result["issues"] = issues
            result["stdout_raw_sha256"] = digest(stdout)
            result["stderr_raw_sha256"] = digest(stderr)
            result["contract"] = contract
            result["contract_sha256"] = digest(canonical(contract))
            if artifact:
                artifact.mkdir(parents=True)
                (artifact / "stdout.bin").write_bytes(stdout)
                (artifact / "stderr.bin").write_bytes(stderr)
                (artifact / "stdout.normalized").write_bytes(normalized_out)
                (artifact / "stderr.normalized").write_bytes(normalized_err)
                write_json(artifact / "requests.json", trace)
                write_json(artifact / "contract.json", contract)
                config = root / "config" / "pb" / "config.toml"
                if config.exists():
                    (artifact / "config-after.toml").write_bytes(config.read_bytes())
                write_json(artifact / "sample.json", result)
            return result
        finally:
            fixture.close()


def percentile(values, p):
    values = sorted(values)
    index = (len(values) - 1) * p
    low = int(index)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (index - low)


def summarize(samples):
    metrics = {}
    for metric in METRICS:
        values = [s[metric] for s in samples if s["valid"] and s.get(metric) is not None]
        if values:
            median = statistics.median(values)
            metrics[metric] = {"n": len(values), "median": median, "mean": statistics.mean(values),
                               "min": min(values), "max": max(values), "p90": percentile(values, .9),
                               "p95": percentile(values, .95), "p99": percentile(values, .99),
                               "stdev": statistics.stdev(values) if len(values) > 1 else 0,
                               "mad": statistics.median(abs(v - median) for v in values)}
    return {"valid_samples": sum(s["valid"] for s in samples), "samples": len(samples), "metrics": metrics,
            "stable_contract": len({s["contract_sha256"] for s in samples}) == 1}


def compare_samples(reference, candidate, metric="wall_ms", seed=1):
    differences = []
    if not reference or not candidate or not all(s["valid"] for s in reference + candidate):
        differences.append("invalid or missing samples")
    for field in ("stdout_sha256", "stderr_sha256", "termination", "requests", "config_before", "config_after"):
        if reference and any(s["contract"][field] != reference[0]["contract"][field] for s in reference + candidate):
            differences.append(field)
    ref_values = [s[metric] for s in reference if s["valid"] and s.get(metric) is not None]
    cand_values = [s[metric] for s in candidate if s["valid"] and s.get(metric) is not None]
    result = {"compatible": not differences, "differences": differences, "metric": metric}
    if not differences and ref_values and cand_values and statistics.median(ref_values) > 0:
        ratio = statistics.median(cand_values) / statistics.median(ref_values)
        rng = random.Random(seed)
        ratios = []
        # Independent resampling also supports comparisons between saved runs.
        for _ in range(2000):
            denominator = statistics.median(rng.choices(ref_values, k=len(ref_values)))
            if denominator:
                ratios.append(statistics.median(rng.choices(cand_values, k=len(cand_values))) / denominator)
        result.update(ratio=ratio, percent_change=(ratio - 1) * 100,
                      median_delta=statistics.median(cand_values) - statistics.median(ref_values),
                      ratio_ci95=[percentile(ratios, .025), percentile(ratios, .975)])
    return result


def host_metadata():
    def read(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None
    cpuinfo = read("/proc/cpuinfo") or ""
    return {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
            "cpu": next((line.split(":", 1)[1].strip() for line in cpuinfo.splitlines() if line.startswith("model name")), platform.processor()),
            "cpu_count": os.cpu_count(), "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "governor_cpu0": read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
            "python": platform.python_version(), "load_average": list(os.getloadavg())}


def binary_info(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"not an executable file: {path}")
    with path.open("rb") as handle:
        sha = hashlib.file_digest(handle, "sha256").hexdigest()
    return {"path": str(path), "sha256": sha, "size_bytes": path.stat().st_size}


def render_report(report, out):
    with (out / "samples.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case", "binary", "round", "valid", "exit_code", *METRICS])
        writer.writeheader()
        for name, case in report["cases"].items():
            for label, runs in case["runs"].items():
                for run in runs:
                    writer.writerow({"case": name, "binary": label, **{k: run.get(k) for k in writer.fieldnames if k not in ("case", "binary")}})
    lines = ["# pb process benchmark", "", "Cache state: warm OS caches; fresh process and fresh config per sample.",
             "Times include process launch and CLI work, exclude fixture startup and artifact writes.", "",
             "| Case | Binary | Valid | Wall median / p95 ms | First stdout ms | CPU ms | Peak RSS MiB |", "|---|---|---:|---:|---:|---:|---:|"]
    comparisons = []
    for name, case in report["cases"].items():
        for label, summary in case["summaries"].items():
            metrics = summary["metrics"]
            def value(key, stat="median", scale=1):
                return f"{metrics[key][stat] / scale:.3f}" if key in metrics else "—"
            lines.append(f"| {name} | {label} | {summary['valid_samples']}/{summary['samples']} | {value('wall_ms')} / {value('wall_ms', 'p95')} | {value('first_stdout_ms')} | {value('cpu_ms')} | {value('peak_rss_bytes', scale=1 << 20)} |")
        for label, comparison in case.get("comparisons", {}).items():
            if not comparison["compatible"]:
                comparisons.append(f"- **{name}: {label} incompatible** — {', '.join(comparison['differences'])}. See normalized output and contract artifacts.")
            elif "ratio" in comparison:
                lo, hi = comparison["ratio_ci95"]
                comparisons.append(f"- {name}: {label}/{report['reference']} {comparison['metric']} ratio **{comparison['ratio']:.3f}×**, bootstrap 95% interval {lo:.3f}–{hi:.3f}.")
    if comparisons:
        lines += ["", "## Comparisons", "", *comparisons]
    lines += ["", "## Interpretation", "", "Lower latency/CPU/RSS is better; higher throughput is better. Missing metrics are not zero.",
              "Bootstrap intervals are descriptive, not proof of causality; scheduler load and thermal/cache state still matter.",
              "First output/request times are observations at the pipe/server, not instrumentation inside pb.",
              "Tail finite cases deliberately end with an error after the known rows; signal cases compare true signal termination.",
              "Exact stdout/stderr and termination are compared; requests use semantic JSON/query comparison, config uses parsed TOML plus permissions.",
              "Only fixture URL and temporary HOME are normalized. Each run retains the original bytes and requests.",
              "This corpus is not complete parity: PTY/TUI, OAuth refresh, TLS, reconnect and other uncovered paths need additional cases."]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    write_json(out / "report.json", report)


def run(args):
    binaries = {}
    for spec in args.binary:
        label, separator, path = spec.partition("=")
        if not separator or not label or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in label) or label in binaries:
            raise ValueError("--binary needs a unique safe NAME=/absolute/path")
        binaries[label] = binary_info(path)
    cases = suite() if not args.suite else json.loads(Path(args.suite).read_text())
    if args.case:
        requested = set(args.case)
        unknown = requested - {case["name"] for case in cases}
        if unknown:
            raise ValueError(f"unknown cases: {sorted(unknown)}")
        cases = [case for case in cases if case["name"] in requested]
    if args.group:
        cases = [case for case in cases if case["group"] in args.group]
    if not cases:
        raise ValueError("no cases selected")
    names = set()
    for case in cases:
        name = case["name"]
        if name in names or not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in name):
            raise ValueError("case names must be unique filesystem-safe labels")
        names.add(name)
        if not isinstance(case.get("argv"), list) or not all(isinstance(arg, str) for arg in case["argv"]):
            raise ValueError(f"{name}: argv must be a list of strings")
        if "expected_exit" not in case or "expected_requests" not in case:
            raise ValueError(f"{name}: expected_exit and expected_requests are required")
    fixture = binary_info(args.fixture) if args.fixture else None
    meter = binary_info(args.meter)
    if any(case.get("fixture") for case in cases) and not fixture:
        raise ValueError("selected cases require --fixture")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    reference = next(iter(binaries))
    report = {"schema_version": SCHEMA_VERSION, "complete": False, "created_unix_ns": time.time_ns(), "host": host_metadata(),
              "binaries": binaries, "fixture": fixture, "meter": meter, "reference": reference,
              "method": {"repetitions": args.repetitions, "warmup": args.warmup, "seed": args.seed,
                         "timeout_s": args.timeout, "read_delay_ms": args.read_delay_ms,
                         "cache": "warm; no cache flushing", "output": "captured pipes; fixed non-TTY environment",
                         "runner_sha256": digest(Path(__file__).read_bytes()), "cases_sha256": digest(canonical(cases))}, "cases": {}}
    rng = random.Random(args.seed)
    failed = False
    for case in cases:
        print(f"{case['name']}:", flush=True)
        entry = {"spec": case, "spec_sha256": digest(canonical(case)), "runs": {label: [] for label in binaries}, "warmup_issues": []}
        report["cases"][case["name"]] = entry
        for round_number in range(-args.warmup, args.repetitions):
            order = list(binaries)
            rng.shuffle(order)
            for label in order:
                artifact = out / "artifacts" / case["name"] / label / (f"warmup-{abs(round_number)}" if round_number < 0 else str(round_number))
                result = sample(Path(binaries[label]["path"]), case, Path(fixture["path"]) if fixture else None,
                                artifact, args.timeout, args.read_delay_ms, Path(meter["path"]))
                result["round"] = round_number
                result["order_in_round"] = order.index(label)
                result["artifact"] = str(artifact.relative_to(out))
                if round_number < 0:
                    if not result["valid"]:
                        entry["warmup_issues"].append({"binary": label, "issues": result["issues"]})
                else:
                    entry["runs"][label].append(result)
                if not result["valid"]:
                    failed = True
                    print(f"  {label}: {', '.join(result['issues'])}", flush=True)
        entry["summaries"] = {label: summarize(runs) for label, runs in entry["runs"].items()}
        entry["comparisons"] = {label: compare_samples(entry["runs"][reference], runs, seed=args.seed)
                                for label, runs in entry["runs"].items() if label != reference}
        if any(not s["stable_contract"] for s in entry["summaries"].values()) or any(not c["compatible"] for c in entry["comparisons"].values()):
            failed = True
        for label, summary in entry["summaries"].items():
            wall = summary["metrics"].get("wall_ms", {}).get("median")
            print(f"  {label}: median {wall:.3f} ms" if wall is not None else f"  {label}: no valid measurements", flush=True)
        render_report(report, out)  # Checkpoint after every case, including failures.
    report["complete"] = True
    report["passed"] = not failed
    render_report(report, out)
    print(f"Report: {out / 'report.md'}")
    return 1 if failed else 0


def compare(args):
    reference = json.loads(Path(args.reference).read_text())
    candidate = json.loads(Path(args.candidate).read_text())
    if reference.get("schema_version") != SCHEMA_VERSION or candidate.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported report schema")
    if not reference.get("complete") or not candidate.get("complete"):
        raise ValueError("cannot compare incomplete/interrupted reports")
    ref_label = args.reference_binary or reference["reference"]
    cand_label = args.candidate_binary or candidate["reference"]
    if ref_label not in reference["binaries"] or cand_label not in candidate["binaries"]:
        raise ValueError("binary label not found in report")
    if set(reference["cases"]) != set(candidate["cases"]):
        raise ValueError("case sets differ; compare matching suites (no silently skipped cases)")
    host_keys = ("system", "release", "machine", "cpu", "cpu_count", "affinity", "governor_cpu0", "python")
    comparable = all(reference["host"][k] == candidate["host"][k] for k in host_keys)
    comparable &= all(reference["method"].get(k) == candidate["method"].get(k) for k in ("runner_sha256", "read_delay_ms", "timeout_s", "warmup"))
    comparable &= (reference.get("fixture") or {}).get("sha256") == (candidate.get("fixture") or {}).get("sha256")
    comparable &= reference.get("meter", {}).get("sha256") == candidate.get("meter", {}).get("sha256")
    if not comparable and args.max_regression_percent is not None:
        raise ValueError("host, fixture or measurement settings differ; performance gating is not valid")
    results = {}
    exit_code = 0
    for name, ref_case in reference["cases"].items():
        cand_case = candidate["cases"][name]
        if ref_case["spec_sha256"] != cand_case["spec_sha256"]:
            raise ValueError(f"scenario changed: {name}")
        result = compare_samples(ref_case["runs"][ref_label], cand_case["runs"][cand_label], args.metric)
        if ref_case.get("warmup_issues") or cand_case.get("warmup_issues"):
            result = {"compatible": False, "differences": ["warmup failed"], "metric": args.metric}
        result["performance_comparable"] = comparable
        if not result["compatible"]:
            exit_code = 1
        if args.max_regression_percent is not None and result["compatible"] and "ratio" not in result:
            raise ValueError(f"{name}: metric {args.metric} unavailable; cannot enforce a performance gate")
        if args.max_regression_percent is not None and "ratio" in result:
            if min(len(ref_case["runs"][ref_label]), len(cand_case["runs"][cand_label])) < 10:
                raise ValueError("performance gates require at least 10 measured repetitions")
            result["regression"] = result["ratio_ci95"][0] > 1 + args.max_regression_percent / 100 and result["median_delta"] > args.min_regression
            if result["regression"] and exit_code != 1:
                exit_code = 3
        results[name] = result
        print(f"{name}: " + (f"{result['ratio']:.3f}× ({result['percent_change']:+.1f}%)" if "ratio" in result else (", ".join(result["differences"]) or "compatible; metric unavailable")))
    output = {"reference": str(Path(args.reference).resolve()), "candidate": str(Path(args.candidate).resolve()),
              "performance_comparable": comparable, "metric": args.metric, "cases": results, "exit_code": exit_code}
    if args.out:
        path = Path(args.out)
        if path.exists():
            raise ValueError("comparison output already exists")
        write_json(path, output)
    if not comparable:
        print("WARNING: host/settings differ; ratios are descriptive only.", file=sys.stderr)
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list built-in scenarios")
    listing.add_argument("--json", action="store_true", help="export editable suite JSON")
    running = commands.add_parser("run", help="benchmark one or more prebuilt binaries")
    running.add_argument("--binary", action="append", required=True, metavar="NAME=PATH")
    running.add_argument("--fixture", help="path to prebuilt pb-fixture")
    running.add_argument("--meter", default=str(DEFAULT_METER), help="path to prebuilt native meter (default bin/pb-meter)")
    running.add_argument("--out", required=True, help="new output directory (never overwritten)")
    running.add_argument("--suite", help="custom JSON suite; see list --json")
    running.add_argument("--case", action="append")
    running.add_argument("--group", action="append")
    running.add_argument("--repetitions", type=int, default=15)
    running.add_argument("--warmup", type=int, default=3)
    running.add_argument("--timeout", type=float, default=10)
    running.add_argument("--seed", type=int, default=2026)
    running.add_argument("--read-delay-ms", type=int, default=0, help="delay stdout reads to exercise backpressure (recorded separately)")
    comparing = commands.add_parser("compare", help="compare two saved reports; optionally gate regressions")
    comparing.add_argument("reference")
    comparing.add_argument("candidate")
    comparing.add_argument("--reference-binary")
    comparing.add_argument("--candidate-binary")
    comparing.add_argument("--metric", choices=["wall_ms", "first_stdout_ms", "first_record_ms", "first_request_ms", "cpu_ms", "peak_rss_bytes"], default="wall_ms")
    comparing.add_argument("--max-regression-percent", type=float)
    comparing.add_argument("--min-regression", type=float, default=1, help="minimum absolute median increase in metric units (default 1 ms for wall_ms)")
    comparing.add_argument("--out", help="new comparison JSON file")
    args = parser.parse_args()
    if args.command == "list":
        print(json.dumps(suite(), indent=2) if args.json else "\n".join(f"{c['name']:28} {c['group']}" for c in suite()))
        return 0
    if not hasattr(os, "wait4") or sys.platform not in ("linux", "darwin"):
        parser.error("measurement currently requires Linux or macOS (wait4 and pipe selectors)")
    if args.command == "run" and (args.repetitions < 1 or args.warmup < 0 or not math.isfinite(args.timeout) or args.timeout <= 0 or args.read_delay_ms < 0):
        parser.error("invalid repetitions, warmup, timeout or read delay")
    if args.command == "compare" and (not math.isfinite(args.min_regression) or args.min_regression < 0 or (args.max_regression_percent is not None and (not math.isfinite(args.max_regression_percent) or args.max_regression_percent < 0))):
        parser.error("regression thresholds must be nonnegative")
    try:
        return run(args) if args.command == "run" else compare(args)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"Harness error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
