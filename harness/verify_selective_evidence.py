#!/usr/bin/env python3
"""Recalculate the selective-ingestion evidence from committed observations.

This verifies arithmetic and data consistency, not confidence-interval coverage,
benchmark noise calibration, artifact/source provenance, or functional parity.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import statistics


def percentile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def expected(directory, reference):
    value = json.loads((directory / reference['file']).read_text())
    for key in reference['path']:
        value = value[key]
    if reference.get('inverse'):
        return [1 / value[1], 1 / value[0]] if isinstance(value, list) else 1 / value
    return value


def close(actual, wanted):
    assert math.isclose(actual, wanted, rel_tol=1e-10, abs_tol=1e-12), (actual, wanted)


def verify(directory):
    plan = json.loads((directory / 'observation-groups.json').read_text())
    # Git may check text out with CRLF; the recorded digest uses LF JSONL.
    raw = (directory / plan['samples_file']).read_bytes().replace(b'\r\n', b'\n')
    assert hashlib.sha256(raw).hexdigest() == plan['samples_sha256']
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(rows) == plan['observations']
    checks = 0
    for group in plan['groups']:
        pairs = []
        for dataset in group['datasets']:
            by_round = {}
            for row in rows:
                if row['dataset'] == dataset and row['case'] == group['case'] and not row['warmup']:
                    entry = by_round.setdefault(row['round'], {})
                    assert row['role'] not in entry
                    entry[row['role']] = row
            for _, entry in sorted(by_round.items()):
                assert set(entry) == set(group['roles'])
                pairs.append(tuple(entry[role] for role in group['roles']))
        assert len(pairs) == group['pairs']
        for metric, references in group['metrics'].items():
            values = [(a[metric], b[metric]) for a, b in pairs]
            assert all(math.isfinite(x) and x > 0 for pair in values for x in pair)
            ratios = [b / a for a, b in values]
            paired_point = statistics.median(ratios)
            rng = random.Random(group['seed'])
            estimates = []
            method = group['method']
            if method == 'paired_median':
                point = paired_point
                estimates = sorted(statistics.median(rng.choices(ratios, k=len(ratios)))
                                   for _ in range(plan['bootstrap_draws']))
                interval = [estimates[50], estimates[1950]]
            else:
                baseline = [a for a, _ in values]
                candidate = [b for _, b in values]
                point = statistics.median(candidate) / statistics.median(baseline)
                for _ in range(plan['bootstrap_draws']):
                    if method == 'paired_ratio_of_medians':
                        sample = rng.choices(values, k=len(values))
                        a, b = zip(*sample)
                    else:
                        assert method == 'independent_ratio_of_medians'
                        a = rng.choices(baseline, k=len(baseline))
                        b = rng.choices(candidate, k=len(candidate))
                    estimates.append(statistics.median(b) / statistics.median(a))
                interval = [percentile(estimates, .025), percentile(estimates, .975)]
            close(point, expected(directory, references['point']))
            for actual, wanted in zip(interval, expected(directory, references['interval'])):
                close(actual, wanted)
            if 'paired_point' in references:
                close(paired_point, expected(directory, references['paired_point']))
            checks += 1
    print(f"Verified {len(rows)} observations, {len(plan['groups'])} groups and {checks} metric summaries.")
    print('Small-sample bootstrap quantiles are nominal; this does not calibrate noise or prove nonregression.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path,
                        default=Path(__file__).resolve().parent.parent / 'docs/evidence/cli-perf-selective')
    args = parser.parse_args()
    verify(args.evidence_dir)


if __name__ == '__main__':
    main()
