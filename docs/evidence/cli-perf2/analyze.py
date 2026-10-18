"""Summarize paired CLI captures using the first campaign's fixed-suite method."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
sys.path.insert(0, str(Path.cwd() / 'harness'))
from run import compare_samples, percentile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('report', type=Path)
p.add_argument('out', type=Path)
p.add_argument('--reference', default='base')
p.add_argument('--candidate', default='final')
p.add_argument('--case-set', choices=['fixed', 'supplement', 'all'], default='fixed')
a = p.parse_args()
r = json.loads(a.report.read_text())
plan = json.loads(Path('docs/evidence/cli-perf2/plan.json').read_text())
fixed = set(plan['fixed_cases'])
supplement = set(plan['supplement_cases'])
if not r['complete'] or not r['passed']:
    raise ValueError('capture is incomplete or failed its contracts')
if r['method']['repetitions'] != plan['repetitions'] or r['method']['warmup'] != plan['warmup']:
    raise ValueError('capture does not match planned repetitions/warmups')
selected = {name: c for name, c in r['cases'].items() if a.case_set == 'all' or ((name in fixed) == (a.case_set == 'fixed'))}
expected = fixed if a.case_set == 'fixed' else supplement if a.case_set == 'supplement' else fixed | supplement
if set(selected) != expected:
    raise ValueError('incomplete requested case population')
for name, case in selected.items():
    if case['warmup_issues']:
        raise ValueError(f'{name}: invalid warmups')
    for label in (a.reference, a.candidate):
        samples = case['runs'][label]
        if len(samples) != plan['repetitions'] or not all(s['valid'] for s in samples):
            raise ValueError(f'{name}/{label}: missing or invalid samples')
metrics = ['cpu_ms', 'wall_ms', 'peak_rss_bytes']
out = {'source_report': str(a.report), 'source_report_sha256': hashlib.sha256(a.report.read_bytes()).hexdigest(),
       'binaries': r['binaries'], 'method': r['method'], 'host': r['host'],
       'reference': a.reference, 'candidate': a.candidate, 'case_set': a.case_set, 'cases': {}, 'aggregate': {}}
for name, case in selected.items():
    out['cases'][name] = {m: compare_samples(case['runs'][a.reference], case['runs'][a.candidate], metric=m, seed=2026) for m in metrics}
for metric in metrics:
    ratios = [c[metric].get('ratio') for c in out['cases'].values()]
    if any(ratio is None or not math.isfinite(ratio) or ratio <= 0 for ratio in ratios):
        raise ValueError(f'{metric}: aggregate requires finite positive median ratios')
    rng = random.Random(2026)
    draws = []
    for _ in range(2000):
        resampled = []
        for c in selected.values():
            base = [s[metric] for s in c['runs'][a.reference] if s['valid']]
            cand = [s[metric] for s in c['runs'][a.candidate] if s['valid']]
            resampled.append(statistics.median(rng.choices(cand, k=len(cand))) / statistics.median(rng.choices(base, k=len(base))))
        draws.append(math.exp(statistics.mean(map(math.log, resampled))))
    ratio = math.exp(statistics.mean(map(math.log, ratios)))
    upper = percentile(draws, .975)
    out['aggregate'][metric] = {'geometric_mean_ratio': ratio, 'ratio_ci95': [percentile(draws, .025), upper],
        'savings_percent': 100 * (1 - ratio), 'ten_percent_gate': ratio <= .9 and upper <= .9}
out['compatible'] = all(c['cpu_ms']['compatible'] for c in out['cases'].values())
if not out['compatible']:
    raise ValueError('selected pair differs in behavior')
out['material_regression_rule'] = 'ratio CI entirely above 1.10 and median increase >1 ms or >1 MiB'
out['material_regressions'] = {
    metric: [name for name, case in out['cases'].items()
             if case[metric]['ratio_ci95'][0] > 1.10
             and case[metric]['median_delta'] > (1048576 if metric == 'peak_rss_bytes' else 1)]
    for metric in metrics}
a.out.write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps({'compatible': out['compatible'], 'aggregate': out['aggregate']}, indent=2))
