#!/usr/bin/env python3
"""Bounded output capture and Linux child telemetry for sustained CLI workloads.

Native meter timings measure fork-to-exit; collector timings measure launch through
drain/reap and include observation, imposed backpressure and cleanup. Row timings
measure when stdout is drained, not when the CLI produced or parsed a row. The
constant-memory scanner checks array/object framing, not semantic JSON validity.
With ``scan_json=False`` only the C-level digest and byte counts touch the stream,
so dense output is not observer-bound by per-token Python; row fields are null.
With ``stdout_sink='null'`` stdout goes directly to /dev/null; output, byte counts
and row observations are unavailable. Resource totals describe that fast sink.
Only bounded prefix/suffix samples and digests are retained; the timeline is CSV.

The native peak RSS comes from the meter's forked child, so it includes the
pre-exec copy of the meter (a floor of roughly 1-2 MiB even for tiny CLIs).
Timeline samples taken before exec are flagged ``child_preexec``.
"""
from __future__ import annotations

import base64
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import select
import selectors
import signal
import struct
import subprocess
import termios
import time


SAMPLE_BYTES = 4096
_SPACE = re.compile(rb"\s*")
_STRUCTURAL = re.compile(rb'["{}\[\]]')
_STRING = re.compile(rb'["\\]')
_IO_KEYS = ("syscr", "syscw", "rchar", "wchar", "read_bytes", "write_bytes",
            "cancelled_write_bytes")


class OutputSummary:
    """Exact digest and byte count with constant-size diagnostic samples."""

    def __init__(self):
        self.bytes = 0
        self.digest = hashlib.sha256()
        self.prefix = bytearray()
        self.suffix = bytearray()

    def feed(self, data):
        self.bytes += len(data)
        self.digest.update(data)
        self.prefix.extend(data[:max(0, SAMPLE_BYTES - len(self.prefix))])
        self.suffix.extend(data[-SAMPLE_BYTES:])
        if len(self.suffix) > SAMPLE_BYTES:
            del self.suffix[:-SAMPLE_BYTES]

    def result(self):
        return {"bytes": self.bytes, "sha256": self.digest.hexdigest(),
                "prefix_base64": base64.b64encode(self.prefix).decode("ascii"),
                "suffix_base64": base64.b64encode(self.suffix).decode("ascii"),
                "prefix_utf8": self.prefix.decode("utf-8", "replace"),
                "suffix_utf8": self.suffix.decode("utf-8", "replace"),
                "sample_limit_bytes": SAMPLE_BYTES}


class ArrayObjectScanner:
    """Count framed top-level objects without retaining rows or nesting stacks.

    Braces/brackets and escaped strings are observed. Object members, values,
    encoding and nesting order are not fully JSON-validated. A count is therefore
    a framed-object count, never a claim that the output is semantically valid.
    """

    def __init__(self):
        self.state = "start"
        self.braces = self.brackets = 0
        self.in_string = self.escape = False
        self.completed_rows = 0
        self.first_row_seconds = self.last_row_seconds = None
        self.framing_error = None

    def feed(self, data, elapsed):
        i = 0
        while i < len(data) and self.state not in ("non_array", "invalid"):
            if self.state == "object":
                if self.in_string:
                    if self.escape:
                        i += 1
                        self.escape = False
                        continue
                    match = _STRING.search(data, i)
                    if match is None:
                        return
                    token = match[0]
                    i = match.end()
                    if token == b"\\":
                        self.escape = True
                    else:
                        self.in_string = False
                    continue
                match = _STRUCTURAL.search(data, i)
                if match is None:
                    return
                token = match[0]
                i = match.end()
                if token == b'"':
                    self.in_string = True
                elif token == b"{":
                    self.braces += 1
                elif token == b"[":
                    self.brackets += 1
                elif token == b"]":
                    self.brackets -= 1
                    if self.brackets < 0:
                        self._invalid("unmatched_array_close_in_object")
                else:
                    self.braces -= 1
                    if self.braces == 0:
                        if self.brackets:
                            self._invalid("object_closed_with_open_array")
                        else:
                            self.completed_rows += 1
                            if self.first_row_seconds is None:
                                self.first_row_seconds = elapsed
                            self.last_row_seconds = elapsed
                            self.state = "after_object"
                continue
            i = _SPACE.match(data, i).end()
            if i == len(data):
                return
            token = data[i]
            i += 1
            if self.state == "start":
                self.state = "first_object" if token == ord("[") else "non_array"
            elif self.state in ("first_object", "next_object"):
                if token == ord("{"):
                    self.braces = 1
                    self.state = "object"
                elif token == ord("]") and self.state == "first_object":
                    self.state = "complete"
                else:
                    self._invalid("expected_top_level_object")
            elif self.state == "after_object":
                if token == ord(","):
                    self.state = "next_object"
                elif token == ord("]"):
                    self.state = "complete"
                else:
                    self._invalid("expected_comma_or_array_close")
            elif self.state == "complete":
                self._invalid("trailing_data_after_array")

    def _invalid(self, reason):
        self.state = "invalid"
        self.framing_error = reason

    def result(self):
        is_array = self.state not in ("start", "non_array")
        error = self.framing_error
        if is_array and self.state not in ("complete", "invalid"):
            error = "truncated_array_or_object"
        duration = None
        if self.completed_rows >= 2:
            duration = self.last_row_seconds - self.first_row_seconds
        return {"completed_rows": self.completed_rows, "is_array": is_array,
                "complete": self.state == "complete", "framing_error": error,
                "complete_row_prefix": self.state == "after_object" and self.completed_rows > 0,
                "validation": "object_array_framing_only_not_semantic_json",
                "first_row_seconds": self.first_row_seconds,
                "last_row_seconds": self.last_row_seconds,
                "first_to_last_row_seconds": duration,
                "timing_source": "collector_stdout_drain_since_launch"}


def _stat_identity(pid):
    """Return (comm, state, ppid, starttime, fields-after-comm) from /proc/PID/stat."""
    text = Path(f"/proc/{pid}/stat").read_text()
    head, tail = text.rsplit(")", 1)
    stat = tail.split()
    return head.split("(", 1)[1], stat[0], int(stat[1]), int(stat[19]), stat


def _child_pid(meter_pid):
    """Discover the meter's direct child and its start time (PID-reuse guard)."""
    try:
        children = Path(f"/proc/{meter_pid}/task/{meter_pid}/children").read_text().split()
        if not children:
            return None
        pid = int(children[0])
        _, _, ppid, started, _ = _stat_identity(pid)
        return (pid, started) if ppid == meter_pid else None
    except (OSError, ValueError, IndexError):
        return None


def _proc_sample(pid, meter_pid=None, starttime=None, preexec_comm=None):
    """Sample one PID, verifying it is still the meter's original child.

    A cached PID could be reaped and reused, so parent PID and start time must
    match before and after the reads; otherwise no counters are reported.
    """
    fields = {"child_pid": pid, "proc_available": False,
              "proc_unavailable_reason": "child_pid_not_observed"}
    if pid is None:
        return fields
    try:
        comm, _, ppid, started, stat = _stat_identity(pid)
        if ((meter_pid is not None and ppid != meter_pid)
                or (starttime is not None and started != starttime)):
            fields["proc_unavailable_reason"] = "child_pid_identity_changed"
            return fields
        ticks = os.sysconf("SC_CLK_TCK")
        fields.update(proc_available=True, proc_unavailable_reason=None,
                      cumulative_cpu_user_ms=int(stat[11]) * 1000 / ticks,
                      cumulative_cpu_system_ms=int(stat[12]) * 1000 / ticks,
                      cumulative_minor_faults=int(stat[7]),
                      cumulative_major_faults=int(stat[9]),
                      child_comm=comm,
                      child_preexec=preexec_comm is not None and comm == preexec_comm)
    except (OSError, ValueError, IndexError) as error:
        fields["proc_unavailable_reason"] = type(error).__name__
        return fields
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        fields["rss_bytes"] = next(int(line.split()[1]) * 1024
                                   for line in status.splitlines() if line.startswith("VmRSS:"))
        fields["threads"] = next(int(line.split()[1]) for line in status.splitlines()
                                 if line.startswith("Threads:"))
        fields["rss_unavailable_reason"] = None
    except (OSError, ValueError, IndexError, StopIteration) as error:
        fields["rss_unavailable_reason"] = type(error).__name__
    try:
        with os.scandir(f"/proc/{pid}/fd") as descriptors:
            fields["fd_count"] = sum(1 for _ in descriptors)
        fields["fd_unavailable_reason"] = None
    except OSError as error:
        fields["fd_unavailable_reason"] = type(error).__name__
    try:
        counters = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/io").read_text().splitlines())
        fields.update({f"cumulative_proc_io_{key}": int(counters[key]) for key in _IO_KEYS})
        fields["io_unavailable_reason"] = None
    except (OSError, ValueError, KeyError) as error:
        fields["io_unavailable_reason"] = type(error).__name__
    try:
        _, _, ppid, started, _ = _stat_identity(pid)
        reused = ((meter_pid is not None and ppid != meter_pid)
                  or (starttime is not None and started != starttime))
    except (OSError, ValueError, IndexError):
        reused = True
    if reused:
        return {"child_pid": pid, "proc_available": False,
                "proc_unavailable_reason": "child_pid_identity_changed"}
    return fields


def _buffered(fd):
    """Bytes currently queued in a pipe, or None if FIONREAD is unavailable."""
    try:
        return struct.unpack("i", fcntl.ioctl(fd, termios.FIONREAD, b"\0" * 4))[0]
    except OSError:
        return None


def _signal_group(pid, signum):
    try:
        os.killpg(pid, signum)
        return True
    except ProcessLookupError:
        return False


def _kill_children_keep_meter(group_pid):
    """Kill workload group members while allowing the native meter to reap.

    The meter deliberately ignores TERM. Killing it simultaneously with the
    workload would discard wait4 and leave its direct child for init to reap.
    The final group KILL remains a bounded fallback if the meter cannot finish.
    """
    found = False
    try:
        entries = Path("/proc").iterdir()
        for entry in entries:
            if not entry.name.isdigit() or int(entry.name) == group_pid:
                continue
            try:
                stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(stat[2]) == group_pid:
                    os.kill(int(entry.name), signal.SIGKILL)
                    found = True
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        pass
    return found


def capture(argv, *, env, cwd, artifact_dir, meter, timeout=120,
            sample_interval=0.1, read_chunk=65536, read_delay=0.0,
            pause_after_bytes=None, pause_seconds=0.0, stop_after_bytes=None,
            signal_after_seconds=None, signal_grace_seconds=5.0,
            timeout_grace_seconds=0.5, descendant_grace_seconds=0.25,
            post_exit_drain_seconds=10.0, scan_json=True, stdout_sink="pipe"):
    """Run one isolated process group, consuming bounded chunks of both pipes.

    Delay/pause apply only to stdout so stderr cannot deadlock the child. Planned
    signal means SIGINT; after ``signal_grace_seconds`` it escalates (logged) to
    TERM, then KILL. Timeout means TERM then KILL after ``timeout_grace_seconds``.
    Timeout and planned signal are only applied while the workload is alive: once
    the meter has exited, already-written output is drained for at most
    ``post_exit_drain_seconds``. Pipes still open with nothing queued after
    ``descendant_grace_seconds`` are classified ``descendant_held_pipes`` and the
    group is killed; the native exit status of the CLI is retained.

    stdout closure tests broken-pipe handling only if the workload was still
    alive (``stdout_close.workload_alive``); the bytes the CLI wrote after the
    cut are unknown, so callers must check the exit status themselves.
    ``scan_json=False`` disables framing (``json_rows.available`` is false and
    row counts are null) so only digest/byte counts are computed.
    ``stdout_sink='null'`` redirects stdout to the OS null device without a pipe,
    scanner or stdout digest. stdout counts/digest and EOF are explicitly null;
    stderr and native resource metrics remain available. Consumer pause, delay
    and stdout closure controls require ``stdout_sink='pipe'``. Discarded-output
    resource measurements must be paired with separate correctness validation.
    ``env`` is used exactly as supplied; no account credentials are injected.
    All result values are JSON-serializable. Artifacts contain no full output.
    """
    if stdout_sink not in ("pipe", "null"):
        raise ValueError("stdout_sink must be 'pipe' or 'null'")
    capture_stdout = stdout_sink == "pipe"
    if not capture_stdout and (read_delay or pause_seconds or pause_after_bytes is not None
                               or stop_after_bytes is not None):
        raise ValueError("stdout consumer delay, pause and closure require stdout_sink='pipe'")
    if timeout <= 0 or sample_interval <= 0 or read_chunk <= 0:
        raise ValueError("timeout, sample_interval and read_chunk must be positive")
    if read_delay < 0 or pause_seconds < 0 or any(
            value is not None and value < 0
            for value in (pause_after_bytes, stop_after_bytes, signal_after_seconds)):
        raise ValueError("delays and byte thresholds must be nonnegative")
    if min(signal_grace_seconds, timeout_grace_seconds, descendant_grace_seconds,
           post_exit_drain_seconds) < 0:
        raise ValueError("grace and drain limits must be nonnegative")
    directory = Path(artifact_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    native_path = directory / "native-metrics.json"
    timeline_path = directory / "timeline.csv"
    # A reused artifact directory must never supply stale native metrics.
    native_path.unlink(missing_ok=True)
    summaries = {"stderr": OutputSummary()}
    if capture_stdout:
        summaries["stdout"] = OutputSummary()
    scanner = ArrayObjectScanner() if scan_json and capture_stdout else None
    preexec_comm = Path(str(meter)).name[:15]
    collector_usage_start = resource.getrusage(resource.RUSAGE_SELF)
    start = time.monotonic()
    process = subprocess.Popen([str(meter), str(native_path), "--", *map(str, argv)],
                               env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
                               stderr=subprocess.PIPE,
                               start_new_session=True)
    selector = selectors.DefaultSelector()
    pipes = {"stderr": process.stderr}
    if capture_stdout:
        pipes["stdout"] = process.stdout
    for name, pipe in pipes.items():
        os.set_blocking(pipe.fileno(), False)
        selector.register(pipe, selectors.EVENT_READ, name)
    child = None  # (pid, starttime) of the meter's verified direct child
    next_sample = next_read = start
    last_sample = start
    previous_rows = 0
    previous_bytes = 0 if capture_stdout else None
    sample_count = proc_sample_count = preexec_sample_count = 0
    paused = stdout_closed = timed_out = signaled = False
    interrupted = None
    escalation_at = drain_deadline = None
    escalation_stage = 0
    exited_at = None
    post_exit = None
    stdout_close = None
    meter_unreaped = False
    events = []
    first_output = None
    output_eof = {"stdout": False if capture_stdout else None, "stderr": False}
    columns = ["elapsed_seconds", "interval_seconds", "child_pid", "child_comm",
               "child_preexec", "proc_available",
               "proc_unavailable_reason", "rss_bytes", "rss_unavailable_reason",
               "threads", "fd_count", "fd_unavailable_reason",
               "cumulative_cpu_user_ms", "cumulative_cpu_system_ms",
               "cumulative_minor_faults", "cumulative_major_faults",
               "io_unavailable_reason", *[f"cumulative_proc_io_{k}" for k in _IO_KEYS],
               "stdout_capture_available", "stdout_bytes", "stderr_bytes", "completed_rows", "interval_rows",
               "interval_stdout_bytes"]

    def close_pipe(name, eof=False):
        pipe = pipes.pop(name, None)
        if pipe is not None:
            try:
                selector.unregister(pipe)
            except KeyError:
                pass
            pipe.close()
        output_eof[name] = eof

    def discover():
        nonlocal child
        if child is None and process.returncode is None:
            child = _child_pid(process.pid)

    def workload_alive():
        """Meter not reaped and its child (if seen) not yet a zombie."""
        if process.poll() is not None:
            return False
        discover()
        if child is None:
            return True
        try:
            _, state, ppid, started, _ = _stat_identity(child[0])
        except (OSError, ValueError, IndexError):
            return False
        return ppid == process.pid and started == child[1] and state not in ("Z", "X")

    def sample(writer, now):
        nonlocal last_sample, previous_rows, previous_bytes
        nonlocal sample_count, proc_sample_count, preexec_sample_count
        discover()
        if process.returncode is not None:
            # After reap the PID may be reused; never read /proc for it again.
            row = {"child_pid": child and child[0], "proc_available": False,
                   "proc_unavailable_reason": "child_reaped"}
        elif child is None:
            row = _proc_sample(None)
        else:
            row = _proc_sample(child[0], process.pid, child[1], preexec_comm)
        sample_count += 1
        proc_sample_count += int(row["proc_available"])
        preexec_sample_count += int(bool(row.get("child_preexec")))
        rows = scanner.completed_rows if scanner else None
        stdout_bytes = summaries["stdout"].bytes if capture_stdout else None
        row.update(elapsed_seconds=now - start, interval_seconds=now - last_sample,
                   stdout_capture_available=capture_stdout, stdout_bytes=stdout_bytes,
                   stderr_bytes=summaries["stderr"].bytes,
                   completed_rows=rows,
                   interval_rows=rows - previous_rows if scanner else None,
                   interval_stdout_bytes=stdout_bytes - previous_bytes if capture_stdout else None)
        writer.writerow(row)
        previous_rows, previous_bytes = rows or 0, stdout_bytes
        last_sample = now

    try:
        with timeline_path.open("w", newline="") as timeline:
            writer = csv.DictWriter(timeline, fieldnames=columns)
            writer.writeheader()
            while pipes or process.poll() is None:
                now = time.monotonic()
                elapsed = now - start
                if now >= next_sample:
                    sample(writer, now)
                    timeline.flush()
                    next_sample = now + sample_interval
                if exited_at is None and process.poll() is not None:
                    exited_at = now
                    escalation_at = None
                    if pipes:
                        events.append({"event": "meter_exit_pipes_open",
                                       "elapsed_seconds": elapsed,
                                       "open_pipes": sorted(pipes)})
                # Only a live workload can time out or receive the planned signal.
                due_timeout = not timed_out and elapsed >= timeout
                due_signal = (not timed_out and not signaled and signal_after_seconds is not None
                              and elapsed >= signal_after_seconds)
                alive = exited_at is None and (due_timeout or due_signal) and workload_alive()
                if alive and due_timeout:
                    timed_out = True
                    interrupted = "timeout"
                    events.append({"event": "timeout_term", "elapsed_seconds": elapsed})
                    _signal_group(process.pid, signal.SIGTERM)
                    escalation_stage = 1
                    escalation_at = now + timeout_grace_seconds
                elif alive and not signaled:
                    signaled = True
                    interrupted = "planned_signal"
                    events.append({"event": "planned_sigint", "elapsed_seconds": elapsed})
                    _signal_group(process.pid, signal.SIGINT)
                    escalation_at = now + signal_grace_seconds
                if escalation_at is not None and now >= escalation_at:
                    if escalation_stage == 0:
                        _signal_group(process.pid, signal.SIGTERM)
                        events.append({"event": "planned_signal_term", "elapsed_seconds": elapsed,
                                       "grace_seconds": signal_grace_seconds})
                        escalation_stage = 1
                        escalation_at = now + timeout_grace_seconds
                    elif escalation_stage == 1 and _kill_children_keep_meter(process.pid):
                        events.append({"event": "forced_child_kill", "elapsed_seconds": elapsed})
                        escalation_stage = 2
                        escalation_at = now + timeout_grace_seconds
                    else:
                        _signal_group(process.pid, signal.SIGKILL)
                        events.append({"event": "forced_kill", "elapsed_seconds": elapsed})
                        escalation_at = None
                        drain_deadline = now + 0.5
                if drain_deadline is not None and now >= drain_deadline:
                    for name in list(pipes):
                        close_pipe(name)
                    break
                if exited_at is not None and pipes:
                    # Without a writer an empty pipe is readable (EOF) even while
                    # stdout is throttled; take that EOF without consuming data.
                    queued = 0
                    for name in list(pipes):
                        buffered = _buffered(pipes[name].fileno())
                        if buffered == 0 and select.select([pipes[name]], [], [], 0)[0]:
                            close_pipe(name, eof=True)
                            continue
                        queued += buffered or 0
                    if pipes and queued == 0 and now >= exited_at + descendant_grace_seconds:
                        group_alive = _signal_group(process.pid, 0)
                        post_exit = "descendant_held_pipes"
                        events.append({"event": "descendant_held_pipes", "elapsed_seconds": elapsed,
                                       "open_pipes": sorted(pipes),
                                       "process_group_alive": group_alive})
                        _signal_group(process.pid, signal.SIGKILL)
                        for name in list(pipes):
                            close_pipe(name)
                        break
                    if pipes and now >= exited_at + post_exit_drain_seconds:
                        post_exit = "post_exit_drain_limit"
                        events.append({"event": "post_exit_drain_limit", "elapsed_seconds": elapsed,
                                       "open_pipes": sorted(pipes), "queued_bytes": queued})
                        for name in list(pipes):
                            close_pipe(name)
                        break
                if "stdout" in pipes:
                    count = summaries["stdout"].bytes
                    if stop_after_bytes is not None and count >= stop_after_bytes:
                        stdout_close = {"stdout_bytes": count, "workload_alive": workload_alive(),
                                        "discarded_queued_bytes": _buffered(pipes["stdout"].fileno()),
                                        "later_written_bytes": "unknown"}
                        close_pipe("stdout")
                        stdout_closed = True
                        if interrupted is None:
                            interrupted = "planned_stdout_close"
                        events.append({"event": "stdout_closed", "elapsed_seconds": elapsed,
                                       **stdout_close})
                    elif not paused and pause_after_bytes is not None and count >= pause_after_bytes:
                        paused = True
                        next_read = max(next_read, now + pause_seconds)
                        events.append({"event": "stdout_pause", "elapsed_seconds": elapsed,
                                       "duration_seconds": pause_seconds, "stdout_bytes": count})
                    if "stdout" in pipes:
                        pipe = pipes["stdout"]
                        try:
                            selector.get_key(pipe)
                            registered = True
                        except KeyError:
                            registered = False
                        if now < next_read and registered:
                            selector.unregister(pipe)
                        elif now >= next_read and not registered:
                            selector.register(pipe, selectors.EVENT_READ, "stdout")
                if process.poll() is not None and not pipes:
                    break
                deadlines = [next_sample, now + 0.05]
                if exited_at is None and not timed_out and start + timeout > now:
                    deadlines.append(start + timeout)
                if (exited_at is None and not signaled and signal_after_seconds is not None
                        and start + signal_after_seconds > now):
                    deadlines.append(start + signal_after_seconds)
                if "stdout" in pipes and next_read > now:
                    deadlines.append(next_read)
                if escalation_at is not None:
                    deadlines.append(escalation_at)
                if drain_deadline is not None:
                    deadlines.append(drain_deadline)
                if exited_at is not None:
                    # Once the descendant grace expires, queued output may
                    # still need a paced drain. An expired deadline would turn
                    # every selector wait into a zero-timeout busy poll.
                    if exited_at + descendant_grace_seconds > now:
                        deadlines.append(exited_at + descendant_grace_seconds)
                    if exited_at + post_exit_drain_seconds > now:
                        deadlines.append(exited_at + post_exit_drain_seconds)
                wait_seconds = max(0, min(deadlines) - now)
                for key, _ in selector.select(wait_seconds):
                    name = key.data
                    if name not in pipes:
                        continue
                    limit = read_chunk
                    if name == "stdout":
                        for threshold in (stop_after_bytes, None if paused else pause_after_bytes):
                            if threshold is not None:
                                limit = min(limit, max(0, threshold - summaries[name].bytes))
                    if limit == 0:
                        continue
                    try:
                        data = os.read(key.fd, limit)
                    except BlockingIOError:
                        continue
                    if not data:
                        close_pipe(name, eof=True)
                        continue
                    summaries[name].feed(data)
                    if name == "stdout":
                        observed = time.monotonic()
                        if first_output is None:
                            first_output = observed - start
                        if scanner is not None:
                            scanner.feed(data, observed - start)
                        next_read = observed + read_delay
                        if read_delay:
                            selector.unregister(key.fileobj)
                # PID discovery/identity reads happen at the telemetry cadence,
                # or when a due intervention must verify workload liveness.
            sample(writer, time.monotonic())
    finally:
        # Also removes descendants which closed their inherited pipes early.
        _signal_group(process.pid, signal.SIGKILL)
        for name in list(pipes):
            close_pipe(name)
        selector.close()
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            meter_unreaped = process.poll() is None
        # Killed descendants were reparented; allow their new parent to reap.
        group_deadline = time.monotonic() + 0.5
        while _signal_group(process.pid, 0) and time.monotonic() < group_deadline:
            time.sleep(0.005)
    group_remaining = _signal_group(process.pid, 0)
    ended = time.monotonic()
    try:
        native = json.loads(native_path.read_text())
        native_reason = None
        native["cpu_total_ms"] = native["cpu_user_ms"] + native["cpu_system_ms"]
        native["wall_ms"] = (native["ended_monotonic_ns"] - native["started_monotonic_ns"]) / 1e6
    except (OSError, ValueError, KeyError, TypeError) as error:
        native = None
        native_reason = type(error).__name__
    collector_usage_end = resource.getrusage(resource.RUSAGE_SELF)
    collector_user_ms = (collector_usage_end.ru_utime - collector_usage_start.ru_utime) * 1000
    collector_system_ms = (collector_usage_end.ru_stime - collector_usage_start.ru_stime) * 1000
    collector_wall = ended - start
    if scanner is not None:
        json_rows = {"available": True, **scanner.result()}
    else:
        json_rows = {"available": False, "completed_rows": None, "is_array": None,
                     "complete": None, "framing_error": None,
                     "validation": "disabled_scan_json_false_digest_and_bytes_only" if capture_stdout
                         else "unavailable_stdout_sink_null",
                     "first_row_seconds": None, "last_row_seconds": None,
                     "first_to_last_row_seconds": None, "timing_source": None}
    result = {"argv": list(map(str, argv)), "outcome": interrupted or post_exit or "completed",
              "timed_out": timed_out, "planned_interruption": signaled or stdout_closed,
              "post_exit": post_exit, "stdout_close": stdout_close,
              "meter_exit_code": process.returncode, "meter_unreaped": meter_unreaped,
              "process_group_remaining": group_remaining,
              "meter_pid": process.pid, "child_pid": child and child[0],
              "exit_code": native["exit_code"] if native else None,
              "termination": native["termination"] if native else None,
              "native_metrics": native, "native_metrics_unavailable_reason": native_reason,
              "native_rss_note": "peak RSS includes the forked meter before exec (~1-2 MiB floor)",
              "collector_wall_seconds": collector_wall,
              "collector_cpu_user_ms": collector_user_ms,
              "collector_cpu_system_ms": collector_system_ms,
              "collector_cpu_total_ms": collector_user_ms + collector_system_ms,
              "collector_cpu_fraction_of_collector_wall":
                  (collector_user_ms + collector_system_ms) / 1000 / collector_wall
                  if collector_wall > 0 else None,
              "collector_cpu_source": "calling_process_rusage_self_delta_including_all_threads",
              "first_stdout_seconds": first_output,
              "stdout": {"available": True, **summaries["stdout"].result()} if capture_stdout else {
                  "available": False, "unavailable_reason": "stdout_sink_null",
                  "bytes": None, "sha256": None, "prefix_base64": "", "suffix_base64": "",
                  "prefix_utf8": "", "suffix_utf8": "", "sample_limit_bytes": SAMPLE_BYTES},
              "stderr": summaries["stderr"].result(),
              "output_eof": output_eof, "json_rows": json_rows,
              "scan_json": bool(scan_json and capture_stdout), "scan_json_requested": scan_json,
              "stdout_sink": stdout_sink, "stdout_sink_path": os.devnull if not capture_stdout else None,
              "timeline_csv": str(timeline_path), "timeline_samples": sample_count,
              "proc_samples_available": proc_sample_count,
              "proc_samples_preexec": preexec_sample_count,
              "proc_unavailable_reason": None if proc_sample_count else "no_live_child_sample",
              "events": events,
              "timing_note": "native: child fork-to-exit; collector: launch through drain/reap; row times: stdout drain observations including imposed backpressure",
              "read_policy": {"read_chunk": read_chunk, "read_delay": read_delay,
                              "pause_after_bytes": pause_after_bytes,
                              "pause_seconds": pause_seconds,
                              "stop_after_bytes": stop_after_bytes,
                              "signal_after_seconds": signal_after_seconds,
                              "signal_grace_seconds": signal_grace_seconds,
                              "timeout_grace_seconds": timeout_grace_seconds,
                              "descendant_grace_seconds": descendant_grace_seconds,
                              "post_exit_drain_seconds": post_exit_drain_seconds}}
    return result
