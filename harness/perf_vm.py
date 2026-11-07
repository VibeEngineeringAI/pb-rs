#!/usr/bin/env python3
"""Paired current-Rust/candidate experiments on an otherwise idle Linux VM.

This is a separate optimization experiment, not Phase 7 performance admission.
Reuse the native wait4 collector and strict functional comparator. Every run
retains its exact cases, binary hashes, raw PTY captures and native counters.
"""
import argparse
import contextlib
import functools
import fnmatch
import hashlib
import json
import os
import platform
import random
import signal
import statistics
import subprocess
from pathlib import Path

import phase7 as p7
import phase7_bench as bench

NATIVE_CAPABILITY_PROBE = p7.native_capability_probe


def reserved_capability_probe(helper):
    result = NATIVE_CAPABILITY_PROBE(helper)
    result['method'] = 'actual two-member group delivery with WNOWAIT reservation and pidfd identity check'
    return result


class ReservedGroupChild(p7.NativeChild):
    """Linux 6.8 transport: the supervisor reserves the child PID via WNOWAIT.

    Until release, that PID cannot be reused as an unrelated process group.
    Check the reservation before killpg and refuse all signals after release.
    The existing actual two-member group-delivery probe runs on this adapter.
    This experiment does not change the Phase 7 collector's Linux 6.9 gate.
    """
    GROUP_SIGNAL_FLAG = 0
    perf_output = None

    def __init__(self, *args, **kwargs):
        self.profiler = None
        self.profiler_log = None
        args = list(args)
        if 'DUMP_HEAPTRACK_OUTPUT' in args[2]:
            env = dict(args[2])
            preload = env.pop('LD_PRELOAD')
            output = env.pop('DUMP_HEAPTRACK_OUTPUT')
            args[1] = ['/usr/bin/env', f'LD_PRELOAD={preload}',
                       f'DUMP_HEAPTRACK_OUTPUT={output}', *args[1]]
            args[2] = env
        super().__init__(*args, **kwargs)
        try:
            if self.perf_output is not None and args[1][-1] != 'signal-probe':
                self.profiler_log = self.perf_output.with_suffix('.log').open('wb')
                self.profiler = subprocess.Popen([
                    'sudo', '-n', 'perf', 'record', '-q', '-F', '199',
                    '--call-graph', 'dwarf', '-o', str(self.perf_output), '-p', str(self.pid),
                ], stdout=self.profiler_log, stderr=subprocess.STDOUT)
        except BaseException:
            if self.profiler_log is not None:
                with contextlib.suppress(OSError):
                    self.profiler_log.close()
            super().close(abort=True)
            raise

    def close(self, abort=False):
        issues = super().close(abort=abort)
        if self.profiler is not None:
            try:
                self.profiler.wait(timeout=10)
            except subprocess.TimeoutExpired:
                subprocess.run(['sudo', '-n', 'kill', '-INT', str(self.profiler.pid)], check=False)
                self.profiler.wait(timeout=10)
            self.profiler_log.close()
            if self.profiler.returncode:
                issues.append('perf recording failed; inspect profiler log')
            self.profiler = None
        return issues

    def signal_group(self, number):
        if self.released or self.pidfd is None or not self.same_process():
            return False
        try:
            signal.pidfd_send_signal(self.pidfd, 0)
            os.killpg(self.pid, number)
            return True
        except ProcessLookupError:
            return False


def identity(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@contextlib.contextmanager
def campaign_report(report, path):
    """No result is confirmed until the whole campaign and final hashes pass."""
    def save():
        path.write_text(json.dumps(report, indent=2) + '\n')

    report['status'] = 'running'
    save()
    try:
        yield
    except BaseException as error:
        report['status'] = 'failed'
        report['errors'] = [f'{type(error).__name__}: {error}']
        for summary in report['summary']:
            for metric in ('cpu_ms', 'peak_rss_bytes', 'wall_ms'):
                summary[metric]['halving_target_confirmed'] = False
        save()
        raise
    else:
        report['status'] = 'completed'
        for summary in report['summary']:
            for metric in ('cpu_ms', 'peak_rss_bytes', 'wall_ms'):
                value = summary[metric]
                value['halving_target_confirmed'] = (
                    not report['profiler'] and not report['options']['corpus']
                    and summary['measured_pairs'] >= 5
                    and value.get('paired_ratio_bootstrap_95', [1, 1])[1] <= 0.5)
        save()
        for summary in report['summary']:
            print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--theme', choices=('dark', 'light'), default='dark')
    parser.add_argument('--profiler', choices=('perf', 'heaptrack'))
    parser.add_argument('--corpus', action='store_true', help='functional workflow corpus instead of performance cases')
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 30 or not 0 <= args.warmups <= 5:
        parser.error('require 1..30 repetitions and 0..5 warmups')
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    p7.TMP = args.out / 'native-tmp'
    p7.TMP.mkdir()
    p7.NativeChild = ReservedGroupChild
    p7.native_capability_probe = reserved_capability_probe
    binaries = {'baseline': args.baseline.resolve()}
    if args.candidate:
        binaries['candidate'] = args.candidate.resolve()
    identities = {role: identity(path) for role, path in binaries.items()}
    options = bench.Options(themes=(args.theme,), sizes=((120, 40),),
                            case_filters=tuple(args.case or ['promql-dense-1000-120x40-*']),
                            observer_policy='resource-attribution', session_timeout=180,
                            idle_seconds=1)
    if args.corpus:
        cases = [case for case in bench.workloads.corpus((args.theme,))
                 if any(fnmatch.fnmatchcase(case['name'], pattern)
                        for pattern in (args.case or ['p7-promql-*']))]
        if not cases:
            parser.error('no functional corpus cases matched')
    else:
        cases = bench.campaign_cases(options)
    report = {'experiment': 'current Rust vs optimized Rust; warm-cache VM',
              'signal_transport': 'WNOWAIT-reserved child process group; pidfd identity check',
              'profiler': args.profiler, 'instrumented_metrics': args.profiler is not None,
              'host': platform.uname()._asdict(), 'binaries': identities,
              'cpu_count': os.cpu_count(),
              'cpu_model': next((line.split(':', 1)[1].strip() for line in
                                  Path('/proc/cpuinfo').read_text().splitlines()
                                  if line.startswith('model name')), None),
              'memory_total': Path('/proc/meminfo').read_text().splitlines()[0],
              'working_tree_source_sha256': {str(path.relative_to(bench.ROOT)): identity(path)['sha256']
                                for path in sorted((bench.ROOT / 'src').rglob('*')) if path.is_file()},
              'options': vars(args) | {'baseline': str(args.baseline),
                                      'candidate': str(args.candidate), 'out': str(args.out)},
              'cases': cases, 'runs': [], 'summary': []}
    report_path = args.out / 'report.json'
    with campaign_report(report, report_path):
        rng = random.Random(20261107)
        for case in cases:
            samples = {role: [] for role in binaries}
            for number in range(args.warmups + args.repetitions):
                roles = list(binaries)
                rng.shuffle(roles)
                pair = {}
                for role in roles:
                    profile = args.out / f"{case['name']}-{number:02d}-{role}"
                    ReservedGroupChild.perf_output = profile.with_suffix('.perf') if args.profiler == 'perf' else None
                    if args.profiler == 'heaptrack':
                        case.setdefault('env', {}).update(
                            LD_PRELOAD='/usr/lib/heaptrack/libheaptrack_preload.so',
                            DUMP_HEAPTRACK_OUTPUT=str(profile.with_suffix('.heaptrack.raw')))
                    readiness = bench.Readiness(case)
                    executor = functools.partial(p7.execute, readiness_fn=readiness,
                        observer_config=bench.OBSERVER_POLICIES['resource-attribution'],
                        resize_signal_policy=bench.RESIZE_SIGNAL_POLICY)
                    directory = args.out / case['name'] / f'{number:02d}-{role}'
                    result = p7.sample(binaries[role], case, directory, execute_fn=executor)
                    issues = p7.validate(result)
                    metrics = result['metrics']
                    record = {'case': case['name'], 'role': role, 'round': number,
                              'warmup': number < args.warmups, 'issues': issues,
                              'cpu_ms': metrics['user_cpu_ms'] + metrics['system_cpu_ms'],
                              'peak_rss_bytes': metrics['peak_rss_bytes'],
                              'wall_ms': metrics['observed_process_wall_ms'],
                              'stdout_bytes': metrics['stdout_bytes'],
                              'frames': len(result['frames']), 'capture': str(directory)}
                    report['runs'].append(record)
                    print(json.dumps(record), flush=True)
                    if not record['warmup']:
                        samples[role].append(record)
                    pair[role] = result
                    report_path.write_text(json.dumps(report, indent=2) + '\n')
                    if issues:
                        raise RuntimeError(f"{case['name']}/{role}: {issues}")
                if 'candidate' in pair:
                    issues = p7.compare(pair['baseline'], pair['candidate'])
                    report.setdefault('comparisons', []).append(
                        {'case': case['name'], 'round': number, 'issues': issues})
                    report_path.write_text(json.dumps(report, indent=2) + '\n')
                    if issues:
                        raise RuntimeError(f'functional comparison failed: {issues}')
            summary = {'case': case['name'], 'measured_pairs': args.repetitions}
            for metric in ('cpu_ms', 'peak_rss_bytes', 'wall_ms'):
                summary[metric] = {role: statistics.median(r[metric] for r in runs)
                                   for role, runs in samples.items()}
                if 'candidate' in samples:
                    ratios = [c[metric] / b[metric] for b, c in
                              zip(samples['baseline'], samples['candidate'])]
                    summary[metric]['median_paired_ratio'] = statistics.median(ratios)
                    summary[metric]['reduction_percent'] = 100 * (1 - statistics.median(ratios))
                    bootstrap = random.Random(20261107)
                    estimates = sorted(statistics.median(bootstrap.choices(ratios, k=len(ratios)))
                                       for _ in range(2000))
                    summary[metric]['paired_ratio_bootstrap_95'] = [estimates[50], estimates[1950]]
                    summary[metric]['halving_target_confirmed'] = False
            report['summary'].append(summary)
            report_path.write_text(json.dumps(report, indent=2) + '\n')
        if {role: identity(path) for role, path in binaries.items()} != identities:
            raise RuntimeError('binary changed during measurement')


if __name__ == '__main__':
    main()
