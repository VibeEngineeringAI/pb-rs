#!/usr/bin/env python3
"""Publish explicit perf3 reports as results.json and self-contained results.html.

Example (only pass final, deliberately selected reports)::

    python3 docs/evidence/cli-perf3/summarize.py \
        --report harness/results/perf3/final-small/report.json \
        --report harness/results/perf3/final-cached/report.json \
        --out docs/evidence/cli-perf3

No discovery, binary execution, dependencies, or cross-report sample pairing.
All ratios are AFTER / BEFORE (or Rust / Go), computed within matching rounds.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import statistics


SCHEMAS = {'pb-stream-stress/1', 'pb-perf3-cached-run/1', 'pb-ingest-perf/1'}
METRICS = {
    'cpu_ms': ('CPU', 'ms', 1),
    'peak_rss_bytes': ('Peak RSS', 'MiB', 1048576),
    'wall_ms': ('Wall time', 'ms', 1),
    'proc_io_syscw': ('Write syscalls (syscw)', 'calls', 1),
    'proc_io_wchar': ('Bytes passed to writes (wchar)', 'MiB', 1048576),
    'proc_io_write_bytes': ('Storage bytes charged (write_bytes)', 'MiB', 1048576),
    'voluntary_context_switches': ('Voluntary context switches', 'switches', 1),
    'involuntary_context_switches': ('Involuntary context switches', 'switches', 1),
}
THROUGHPUT = {
    'rows_per_second': ('Rows', 'rows/s', 1),
    'output_mib_per_second': ('Output', 'MiB/s', 1),
}
METHOD = (
    'Descriptive small-sample measurements on a shared host, not confidence intervals or '
    'a population speedup claim. Warmups are excluded. Samples pair only within the same '
    'source report, case, and round; no pairing across reports. The main ratio is the '
    'median of candidate/baseline (or Rust/Go) round ratios, with observed minimum and '
    'maximum. Ratio of medians is supplemental. CPU is the native child CPU total and '
    'RSS is native lifetime peak; neither is Python collector usage. Null-sink runs are '
    'resource diagnostics admitted only after separate full-output pipe parity. '
    'Throughput is withheld unless the source report explicitly admits performance '
    'and the sink is a pipe. Write syscall counts, wchar, and charged storage bytes '
    'measure different things; none estimates physical SSD wear.'
)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def median(values):
    return statistics.median(values) if values else None


def label(sample):
    return sample.get('label', sample.get('binary'))


def case_name(sample):
    return sample.get('case', sample.get('workload'))


def sample_valid(sample, ingest):
    if ingest:
        checked = sample.get('valid') is True and not sample.get('check_failures')
    else:
        checked = not sample.get('issues') and isinstance(sample.get('native_metrics'), dict)
    resources = sample if ingest else (sample.get('native_metrics') or {})
    return (checked and resources.get('resource_source') == 'native_child_wait4'
            and all(value(sample, key, ingest) is not None for key in ('cpu_ms', 'peak_rss_bytes')))


def value(sample, key, ingest):
    if ingest:
        candidate = sample.get(key)
    else:
        native = sample.get('native_metrics') or {}
        rates = sample.get('rates') or {}
        if key == 'cpu_ms':
            candidate = native.get('cpu_total_ms')
            if candidate is None and all(number(native.get(k)) for k in ('cpu_user_ms', 'cpu_system_ms')):
                candidate = native['cpu_user_ms'] + native['cpu_system_ms']
            if candidate is None:
                candidate = rates.get('cpu_ms')
        else:
            candidate = native.get(key, rates.get(key))
    return candidate if number(candidate) else None


def comparisons(labels):
    """Keep revision evidence and Go evidence separate, even in three-label reports."""
    result = []
    if {'baseline', 'candidate'} <= labels:
        result.append(('baseline', 'candidate', 'rust_revisions'))
    if {'go', 'rust'} <= labels:
        result.append(('go', 'rust', 'go_comparison'))
    elif {'go', 'candidate'} <= labels:
        result.append(('go', 'candidate', 'go_comparison'))
    return result


def validation_for(report, name):
    validations = report.get('output_validations') or {}
    return validations if 'verdict' in validations else validations.get(name, {})


def pipe_parity(validation, reference, candidate):
    verdict = validation.get('verdict') or {}
    runs = validation.get('samples') or []
    hashes = {s.get('stdout', {}).get('sha256') for s in runs}
    labels = {label(s) for s in runs}
    return (verdict.get('valid') is True and verdict.get('compatible') is True
            and {reference, candidate} <= labels and len(hashes) == 1 and None not in hashes
            and all(not s.get('issues') and s.get('stdout_sink', 'pipe') == 'pipe' for s in runs))


def summarize_metric(pairs, key, ingest, admitted):
    observed = []
    for round_index, before, after in pairs:
        a, b = value(before, key, ingest), value(after, key, ingest)
        observed.append({'round': round_index, 'before': a, 'after': b,
                         'ratio': b / a if admitted and a is not None and b is not None and a > 0 else None})
    complete = [p for p in observed if p['before'] is not None and p['after'] is not None]
    before = median([p['before'] for p in complete])
    after = median([p['after'] for p in complete])
    ratios = [p['ratio'] for p in complete if p['ratio'] is not None]
    return {'before_median': before, 'after_median': after, 'complete_value_pairs': len(complete),
            'paired_ratio_n': len(ratios), 'paired_median_ratio': median(ratios),
            'paired_min_ratio': min(ratios) if ratios else None,
            'paired_max_ratio': max(ratios) if ratios else None,
            'ratio_of_medians': after / before if admitted and before is not None and before > 0 else None,
            'samples': observed}


def summarize_case(report, source_id, name, spec, summary, reference, candidate, group):
    ingest = report['schema'] == 'pb-ingest-perf/1'
    selected = [(i, s) for i, s in enumerate(report.get('samples', []))
                if case_name(s) == name and s.get('warmup') is not True and label(s) in (reference, candidate)]
    rounds = {}
    duplicate = False
    for _, sample in selected:
        round_index = sample.get('round')
        entries = rounds.setdefault(round_index, {})
        duplicate |= label(sample) in entries
        entries[label(sample)] = sample
    pairs = [(r, entries[reference], entries[candidate]) for r, entries in rounds.items()
             if r is not None and reference in entries and candidate in entries]
    pairs.sort(key=lambda p: p[0])
    expected = ((report.get('configuration') if ingest else report.get('method')) or {}).get('repetitions')
    paired_complete = (bool(pairs) and not duplicate and len(pairs) * 2 == len(selected)
                       and (expected is None or len(pairs) == expected))
    all_valid = bool(selected) and all(sample_valid(s, ingest) for _, s in selected)
    compatibility = ((report.get('gate', {}).get('compatibility', {}).get('workloads', {}).get(name) or {})
                     if ingest else summary)
    compatible = compatibility.get('passed') if ingest else summary.get('compatible')
    ingest_compat = report.get('gate', {}).get('compatibility', {})
    overall_compatibility = ingest_compat.get('passed') if ingest else None
    if ingest:
        probes = ingest_compat.get('probes') or {}
        required = [f'{kind}:{workload}' for workload in ingest_compat.get('workloads', {})
                    for kind in ('retry_503', 'split_413')]
        overall_compatibility = bool(overall_compatibility is True
                                     and ingest_compat.get('probes_skipped') is not True
                                     and required
                                     and all(probes.get(key, {}).get('passed') is True for key in required))
    valid = all_valid and (True if ingest else summary.get('valid') is True)
    report_complete = True if ingest else report.get('complete') is True
    report_clean = not report.get('errors') and (True if ingest else report.get('passed') is True)
    sink = 'ingest fixture' if ingest else report.get('stdout_sink', report.get('method', {}).get('stdout_sink', 'pipe'))
    validation = validation_for(report, name)
    parity = pipe_parity(validation, reference, candidate) if sink == 'null' else None
    admitted = bool(valid and compatible is True and (not ingest or overall_compatibility is True)
                    and paired_complete and report_complete and report_clean
                    and (sink != 'null' or parity))
    reasons = []
    for condition, reason in ((valid, 'invalid or missing native samples'),
                              (compatible is True, 'compatibility not established'),
                              (not ingest or overall_compatibility is True,
                               'overall ingest compatibility failed or missing (including durability probes)'),
                              (paired_complete, 'missing, duplicate, or incomplete paired rounds'),
                              (report_complete and report_clean, 'source report incomplete or failed'),
                              (sink != 'null' or parity, 'null sink lacks exact full-output pipe parity')):
        if not condition:
            reasons.append(reason)
    throughput = admitted and sink == 'pipe' and summary.get('performance_admitted') is True
    metrics = {key: summarize_metric(pairs, key, ingest, admitted) for key in METRICS}
    throughput_metrics = {key: summarize_metric(pairs, key, ingest, True) for key in THROUGHPUT} if throughput else {}
    sample_records = []
    for index, sample in selected:
        sample_records.append({'source_sample_index': index, 'round': sample.get('round'), 'label': label(sample),
                               'valid': sample_valid(sample, ingest),
                               'resource_source': (sample if ingest else (sample.get('native_metrics') or {})).get('resource_source'),
                               'issues': sample.get('issues', sample.get('check_failures', [])),
                               'resources': {k: value(sample, k, ingest) for k in METRICS},
                               'contract': sample.get('contract'),
                               'observation_sha256': sample.get('observation_sha256'),
                               'validated_source': sample.get('validated_source'),
                               'observer': sample.get('observer'),
                               'producer_comparison': sample.get('producer_comparison')})
    return {'source': source_id, 'case': name, 'comparison': group,
            'reference_label': reference, 'candidate_label': candidate,
            'case_metadata': spec, 'valid': valid, 'compatible': compatible,
            'overall_ingest_compatibility_passed': overall_compatibility,
            'source_summary': summary, 'compatibility_evidence': compatibility,
            'sink': sink, 'full_pipe_parity': parity,
            'output_validation': validation, 'paired_rounds': len(pairs), 'expected_rounds': expected,
            'resource_comparison_admitted': admitted, 'withheld_reasons': reasons,
            'performance_admitted': summary.get('performance_admitted'),
            'throughput_admitted': throughput,
            'throughput_note': ('Source performance admission passed; pipe end-to-end rates.' if throughput else
                                'Withheld: null-sink resource-only, ingest report, invalid samples, or source performance admission false/missing.'),
            'metrics': metrics, 'throughput': throughput_metrics, 'samples': sample_records}


def load_report(path):
    raw = path.read_bytes()
    report = json.loads(raw)
    if report.get('schema') not in SCHEMAS:
        raise ValueError(f'{path}: unsupported schema {report.get("schema")!r}')
    if not isinstance(report.get('samples'), list) or not isinstance(report.get('binaries'), dict):
        raise ValueError(f'{path}: missing samples or binary identities')
    source_id = hashlib.sha256(raw).hexdigest()
    ingest = report['schema'] == 'pb-ingest-perf/1'
    source = {'id': source_id, 'path': str(path.resolve()), 'sha256': source_id, 'size_bytes': len(raw),
              'schema': report['schema'], 'complete': report.get('complete'), 'passed': report.get('passed'),
              'errors': report.get('errors', []), 'binaries': report['binaries'],
              'fixture': report.get('fixture'), 'meter': report.get('meter'), 'scope': report.get('scope'),
              'host': report.get('host', report.get('host_before')), 'host_after': report.get('host_after'),
              'method': report.get('method', report.get('configuration')), 'sources': report.get('sources'),
              'gate': report.get('gate'), 'manifest_identity': report.get('manifest_identity'),
              'manifest_identity_end': report.get('manifest_identity_end'),
              'calibrations': report.get('calibrations', report.get('calibration')),
              'excluded_warmup_samples': sum(s.get('warmup') is True for s in report['samples'])}
    specs = ({name: metadata for name, metadata in report.get('corpora', {}).items()} if ingest else
             {s['name']: s for s in report.get('cases', [])})
    cases = sorted({case_name(s) for s in report['samples']} - {None})
    result = []
    for name in cases:
        available = {label(s) for s in report['samples'] if case_name(s) == name and s.get('warmup') is not True}
        summary = report.get('summary') or {}
        if 'valid' not in summary:
            summary = summary.get(name) or {}
        case_pairs = comparisons(available)
        if not case_pairs:
            raise ValueError(f'{path}: {name}: no supported baseline/candidate or go/rust sample pair')
        for reference, candidate, group in case_pairs:
            result.append(summarize_case(report, source_id, name, specs.get(name, {}), summary,
                                         reference, candidate, group))
    if not cases:
        raise ValueError(f'{path}: no samples to summarize')
    return source, result


def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value):
    return '—' if value is None else f'{value:,.3f}'


def ratio(value):
    return '—' if value is None else f'{value:.3f}×'


def percent_change(value):
    return '—' if value is None else f'{(value - 1) * 100:+.1f}%'


def table(headers, rows):
    return ('<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{esc(h)}</th>' for h in headers)
            + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>'
                                            for row in rows) + '</tbody></table></div>')


def bar_chart(entry, key, title, unit, scale, before_name, after_name):
    metric = entry['metrics'][key]
    before, after = metric['before_median'], metric['after_median']
    # A partial metric can be audited in JSON but must not become a chart.
    if (not entry['resource_comparison_admitted'] or metric['complete_value_pairs'] != entry['paired_rounds']
            or before is None or after is None):
        return ''
    a, b = before / scale, after / scale
    largest = max(a, b, 1e-12)
    parts = [f'<figure><figcaption>{esc(title)} <span>({esc(unit)}; lower is better)</span></figcaption>',
             '<div class="bar-labels">',
             f'<span><i class="before"></i>{esc(before_name)}<strong>{fmt(a)} {esc(unit)}</strong></span>',
             f'<span><i class="after"></i>{esc(after_name)}<strong>{fmt(b)} {esc(unit)}</strong></span></div>',
             f'<svg viewBox="0 0 320 80" preserveAspectRatio="none" role="img" aria-label="{esc(title)}: {esc(before_name)} {fmt(a)} {esc(unit)}, {esc(after_name)} {fmt(b)} {esc(unit)}">']
    for y, amount, style in ((8, a, 'before'), (48, b, 'after')):
        parts.append(f'<rect class="{style}" x="0" y="{y}" width="{amount/largest*320:.3f}" height="24" rx="3"/>')
    parts.append('</svg></figure>')
    return ''.join(parts)


def render_entry(entry, source, index):
    revisions = entry['comparison'] == 'rust_revisions'
    before_name, after_name = ('BEFORE Rust', 'AFTER Rust') if revisions else ('Go', 'Rust')
    status = 'resource comparison admitted' if entry['resource_comparison_admitted'] else 'comparison withheld'
    open_case = entry['case'] in ({'stream-1gib', 'large-item-8mib'} if revisions else {'stream-10gib'})
    output = [f'<details class="case-detail" id="case-{index}"' + (' open' if open_case else '') + '>',
              f'<summary><strong>{esc(entry["case"])}</strong> <span class="meta">'
              f'{esc(Path(source["path"]).parent.name)} · {status}</span></summary><article>',
              f'<p class="meta">{esc(Path(source["path"]).parent.name)} · {esc(entry["sink"])} · '
              f'{entry["paired_rounds"]} paired rounds · {status}</p>',
              f'<p>Valid: {esc(entry["valid"])} · Compatible: {esc(entry["compatible"])} · '
              f'Throughput admitted: {esc(entry["throughput_admitted"])}</p>']
    if entry['withheld_reasons']:
        output.append('<p class="warning">' + esc('; '.join(entry['withheld_reasons'])) + '</p>')
    output.append('<div class="charts">')
    for key in ('cpu_ms', 'peak_rss_bytes'):
        title, unit, scale = METRICS[key]
        output.append(bar_chart(entry, key, title, unit, scale, before_name, after_name))
    output.append('</div>')
    rows = []
    for key, (title, unit, scale) in METRICS.items():
        metric = entry['metrics'][key]
        a, b = metric['before_median'], metric['after_median']
        rows.append([esc(f'{title} ({unit})'), fmt(a / scale if a is not None else None),
                     fmt(b / scale if b is not None else None), ratio(metric['paired_median_ratio']),
                     f'{ratio(metric["paired_min_ratio"])} – {ratio(metric["paired_max_ratio"])}',
                     str(metric['paired_ratio_n']), ratio(metric['ratio_of_medians'])])
    output.append(table(['Metric', before_name + ' median', after_name + ' median', 'Paired median ratio',
                         'Paired ratio min–max', 'Ratio n', 'Ratio of medians'], rows))
    output.append(f'<p class="note">{esc(entry["throughput_note"])} '
                  'Ratios are AFTER / BEFORE or Rust / Go; values below 1 mean less resource use. '
                  'Zero reference values have no defined ratio.</p>')
    if entry['throughput_admitted']:
        output.append(table(['Admitted throughput', before_name, after_name, 'Paired median ratio (higher is better)'],
                            [[esc(title + ' (' + unit + ')'), fmt(entry['throughput'][key]['before_median']),
                              fmt(entry['throughput'][key]['after_median']), ratio(entry['throughput'][key]['paired_median_ratio'])]
                             for key, (title, unit, _) in THROUGHPUT.items()]))
    identities = []
    for role in (entry['reference_label'], entry['candidate_label']):
        identity = source['binaries'].get(role, {})
        identities.append([esc(role), f'<code>{esc(identity.get("path", "unknown"))}</code>',
                           f'<code>{esc(identity.get("sha256", "unrecorded"))}</code>'])
    output.append('<details><summary>Binary identities, case metadata, and individual paired samples</summary>')
    output.append(table(['Label', 'Binary', 'SHA-256'], identities))
    output.append('<pre>' + esc(json.dumps({'case': entry['case_metadata'], 'output_validation_verdict':
                                           entry['output_validation'].get('verdict'), 'samples': entry['samples']},
                                          indent=2, sort_keys=True)) + '</pre></details></article></details>')
    return ''.join(output)


def render(result):
    sources = {s['id']: s for s in result['sources']}
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>CLI performance evidence</title><style>',
             ':root{color-scheme:light dark;font-family:var(--font-sans,system-ui,sans-serif);',
             '--evidence-fg:var(--text-primary,var(--foreground,#202a36));--evidence-muted:var(--text-secondary,var(--muted-foreground,#576477));',
             '--evidence-border:var(--border-color,var(--border,#d9dfe6));--evidence-surface:var(--background-secondary,var(--muted,#f3f5f7));',
             '--evidence-before:var(--text-tertiary,#a3acba);--evidence-after:var(--accent-color,#248670);',
             '--evidence-warning:var(--warning-color,#a04c16);color:var(--evidence-fg)}',
             '*{box-sizing:border-box}html,body,:root,main{background:transparent}body{margin:0;padding:0}main{width:100%;padding:0}',
             'h2{font-size:1.2rem;margin:1.3rem 0 .7rem}',
             'p{line-height:1.55;margin:.6rem 0}.intro,.note,.meta{color:var(--evidence-muted)}.note,.meta{font-size:.85rem}',
             'article{padding:.5rem 0 .9rem}.scroll{overflow:auto;max-width:100%}',
             'table{width:100%;border-collapse:collapse;font-size:.88rem;font-variant-numeric:tabular-nums}',
             'th,td{padding:.6rem .65rem;text-align:right;border-bottom:1px solid var(--evidence-border);white-space:nowrap}',
             'th{background:var(--evidence-surface);color:var(--evidence-muted);font-weight:600}th:first-child,td:first-child{text-align:left}',
             'a{color:inherit;text-decoration-color:var(--evidence-muted);text-underline-offset:.15em}',
             '.overview table{font-size:.84rem}.overview th,.overview td{padding:.5rem .45rem}.overview td:first-child{white-space:normal;min-width:8rem}',
             '.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:1rem}',
             'figure{margin:.5rem 0 1rem}figcaption{font-weight:600;font-size:.9rem}figcaption span{font-weight:400;color:var(--evidence-muted)}',
             '.bar-labels{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-top:.7rem;font-size:.85rem}',
             '.bar-labels strong{display:block;font-size:.95rem;margin-top:.15rem;font-variant-numeric:tabular-nums}',
             '.bar-labels i{display:inline-block;width:10px;height:10px;margin-right:.35rem;border-radius:2px}',
             '.before{fill:var(--evidence-before);background:var(--evidence-before)}.after{fill:var(--evidence-after);background:var(--evidence-after)}',
             'svg{display:block;width:100%;height:80px}',
             'details{margin-top:.75rem}summary{cursor:pointer;font-size:.9rem}summary .meta{margin-left:.4rem}',
             '.case-detail{border-top:1px solid var(--evidence-border)}.case-detail>summary{padding:.7rem 0}',
             'pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.78rem;max-height:32rem;overflow:auto}',
             'code{font-size:.78rem;overflow-wrap:anywhere}.warning{color:var(--evidence-warning)}',
             '@media(prefers-color-scheme:dark){:root{--evidence-fg:var(--text-primary,var(--foreground,#e1e8f0));',
             '--evidence-muted:var(--text-secondary,var(--muted-foreground,#a8b4c4));--evidence-border:var(--border-color,var(--border,#354050));',
             '--evidence-surface:var(--background-secondary,var(--muted,#202a36));--evidence-warning:var(--warning-color,#f4b078)}}',
             '</style><main>',
             f'<details><summary>Measurement method and limits</summary><p class="intro">{esc(METHOD)}</p></details>',
             '<p class="note">Inputs were explicitly selected. Each report retains its own binary identities; '
             'the generator does not infer that different candidate binaries are the same final revision.</p>']
    for group, title in (('rust_revisions', 'BEFORE → AFTER: Rust revisions'),
                         ('go_comparison', 'Go compatibility and resource comparison')):
        entries = [(i, e) for i, e in enumerate(result['comparisons']) if e['comparison'] == group]
        if entries:
            parts.append(f'<section><h2>{title}</h2>')
            overview = []
            for i, entry in entries:
                cpu, rss = entry['metrics']['cpu_ms'], entry['metrics']['peak_rss_bytes']
                compatibility = ('passed' if entry['compatible'] is True else
                                 'failed' if entry['compatible'] is False else 'unestablished')
                if entry['sink'] == 'ingest fixture' and entry['overall_ingest_compatibility_passed'] is not True:
                    compatibility = ('failed probes/gate' if entry['overall_ingest_compatibility_passed'] is False
                                     else 'unestablished gate')
                overview.append([f'<a href="#case-{i}">{esc(entry["case"])}</a>',
                                 percent_change(cpu['paired_median_ratio']), percent_change(rss['paired_median_ratio']),
                                 str(entry['paired_rounds']), esc(compatibility)])
            parts.append('<div class="overview">' + table(
                ['Case', 'CPU Δ', 'RSS Δ', 'n', 'Compatibility'], overview) + '</div>')
            parts.append('<p class="note">Δ is the median paired percent change; negative means less resource use. '
                         'n counts paired rounds. Withheld comparisons show —. Expand a case for values, charts, and admission evidence.</p>')
            for i, entry in entries:
                parts.append(render_entry(entry, sources[entry['source']], i))
            parts.append('</section>')
    parts.append('<section><h2>Source provenance</h2>')
    parts.append(table(['Report', 'SHA-256', 'Schema', 'Complete', 'Passed', 'Warmups excluded'],
                       [[f'<code>{esc(s["path"])}</code>', f'<code>{esc(s["sha256"])}</code>', esc(s['schema']),
                         esc(s['complete']), esc(s['passed'] if s['passed'] is not None else (s.get('gate') or {}).get('passed')),
                         str(s['excluded_warmup_samples'])] for s in result['sources']]))
    for source in result['sources']:
        parts.append('<details><summary>' + esc(Path(source['path']).parent.name)
                     + ': all binary identities and source admission evidence</summary><pre>'
                     + esc(json.dumps(source, indent=2, sort_keys=True)) + '</pre></details>')
    parts.append('<p class="note">Full precision samples, source summaries, identities, and admission flags are in results.json. '
                 'Source report hashes identify the exact bytes read. Missing counters are displayed as —.</p></section></main></html>')
    return ''.join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, action='append', required=True,
                        help='explicit final report.json; repeat for each source (no directory discovery)')
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent,
                        help='directory for results.json and results.html')
    args = parser.parse_args(argv)
    resolved = [p.resolve() for p in args.report]
    if len(set(resolved)) != len(resolved):
        parser.error('duplicate --report paths')
    result = {'schema': 'pb-cli-perf3-evidence/1', 'generated_at': datetime.now(timezone.utc).isoformat(),
              'method': METHOD, 'generator': {'path': str(Path(__file__).resolve()),
                                             'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
              'sources': [], 'comparisons': []}
    try:
        for path in resolved:
            source, entries = load_report(path)
            result['sources'].append(source)
            result['comparisons'].extend(entries)
        rendered = render(result)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(str(error))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'results.json').write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + '\n')
    (args.out / 'results.html').write_text(rendered)
    print(f'{args.out / "results.json"}\n{args.out / "results.html"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
