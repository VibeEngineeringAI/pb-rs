#!/usr/bin/env python3
"""Render a compact, self-contained interactive view of summarize.py evidence.

    python3 docs/evidence/cli-perf3/dashboard.py --input results.json --out dashboard.html

The generator never runs a binary or recomputes admission or paired statistics.
Use --provisional for previews made from interim evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path


SCHEMA = 'pb-cli-perf3-evidence/1'
METRICS = ('cpu_ms', 'peak_rss_bytes', 'proc_io_syscw')


def compact(report, path, provisional):
    if report.get('schema') != SCHEMA:
        raise ValueError('expected results.json produced by summarize.py')
    sources = {}
    for source in report['sources']:
        sources[source['id']] = {key: source.get(key) for key in
            ('path', 'sha256', 'schema', 'complete', 'passed', 'binaries', 'method', 'scope', 'gate')}
    comparisons = []
    for entry in report['comparisons']:
        if entry['comparison'] not in ('rust_revisions', 'go_comparison'):
            continue
        if entry['source'] not in sources:
            raise ValueError(f"missing source for {entry['case']}")
        kept = {key: entry.get(key) for key in
            ('source', 'case', 'comparison', 'reference_label', 'candidate_label', 'sink', 'compatible',
             'resource_comparison_admitted', 'withheld_reasons', 'paired_rounds', 'expected_rounds',
             'full_pipe_parity', 'throughput_admitted', 'throughput_note', 'case_metadata',
             'overall_ingest_compatibility_passed')}
        kept['metrics'] = {key: {field: (entry.get('metrics', {}).get(key) or {}).get(field) for field in
            ('before_median', 'after_median', 'complete_value_pairs', 'paired_ratio_n',
             'paired_median_ratio', 'paired_min_ratio', 'paired_max_ratio')} for key in METRICS}
        kept['source_summary'] = {key: (entry.get('source_summary') or {}).get(key) for key in
            ('performance_admitted', 'possibly_producer_bound_runs', 'observer_bound_runs')}
        comparisons.append(kept)
    if not comparisons:
        raise ValueError('no supported comparisons')
    return {'provisional': provisional, 'generated_at': report.get('generated_at'),
            'input': {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
            'sources': sources, 'comparisons': comparisons}


PAGE = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CLI performance comparison</title>
<style>
:root{color-scheme:light dark;font-family:var(--font-sans,system-ui,sans-serif);font-size:14px;
--dash-fg:var(--text-primary,var(--foreground,#202a36));--dash-muted:var(--text-secondary,var(--muted-foreground,#5b6878));
--dash-line:var(--border-color,var(--border,#d8dfe8));--dash-surface:var(--background-secondary,var(--muted,#f1f4f7));--dash-active:var(--background-primary,var(--background,#fff));
--dash-before:var(--text-tertiary,#9aa7b8);--dash-after:var(--accent-color,#197d70);--dash-warning:var(--warning-color,#985119);color:var(--dash-fg)}
*{box-sizing:border-box}html,body,main{background:transparent}body{margin:0;padding:0}main{width:100%;max-width:100%;padding:0}
button,select{font:inherit;color:inherit}button{cursor:pointer}button:focus-visible,select:focus-visible,tr:focus-visible{outline:2px solid var(--dash-after);outline-offset:2px}
.toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 0 12px;flex-wrap:wrap}
.tabs{display:flex;gap:4px;padding:3px;border:1px solid var(--dash-line);border-radius:9px;background:var(--dash-surface)}
.tabs button{border:0;border-radius:6px;padding:8px 12px;background:transparent;color:var(--dash-muted);font-weight:600}
.tabs button[aria-selected=true]{background:var(--dash-active);color:var(--dash-fg);box-shadow:inset 0 -2px 0 var(--dash-after)}.tabs button:disabled{cursor:default;opacity:.45}
.provisional{color:var(--dash-warning);font-size:12px;font-weight:650}.provisional[hidden]{display:none}
.overview{overflow-x:auto;border-top:1px solid var(--dash-line);border-bottom:1px solid var(--dash-line)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:13px}
th,td{padding:9px 8px;text-align:right;border-bottom:1px solid var(--dash-line);white-space:nowrap}
th{position:sticky;top:0;background:var(--dash-surface);z-index:1;color:var(--dash-muted);font-weight:600}
th:first-child,td:first-child{text-align:left}td:first-child{white-space:normal;min-width:122px}th:last-child,td:last-child{padding-right:4px}th:first-child,td:first-child{padding-left:4px}
tbody tr{cursor:pointer}tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:var(--dash-surface)}tbody tr[aria-selected=true]{background:color-mix(in srgb,var(--dash-after) 11%,transparent)}
.case-name{display:block;font-weight:550;line-height:1.3}.source-name{display:block;font-size:10px;color:var(--dash-muted);line-height:1.3;margin-top:2px}
.caption{font-size:11px;color:var(--dash-muted);line-height:1.45;margin:6px 0 13px}.picker{display:flex;align-items:center;gap:9px;margin-bottom:9px}
.picker label{color:var(--dash-muted);white-space:nowrap;font-size:12px}.picker select{min-width:0;flex:1;border:1px solid var(--dash-line);border-radius:6px;background:var(--dash-surface);padding:8px;max-width:100%}
.selection{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin:0 0 3px}.selection strong{font-size:16px;line-height:1.3}.selection span{font-size:11px;color:var(--dash-muted);white-space:nowrap}
.status{font-size:11px;line-height:1.45;color:var(--dash-muted);margin:0 0 8px}.status.warning{color:var(--dash-warning)}
.charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.chart{min-width:0;height:178px;border:1px solid var(--dash-line);border-radius:8px;padding:16px;margin:0}
.chart-top{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:10px}.chart-title{font-size:13px;font-weight:650}.delta{font-variant-numeric:tabular-nums;font-size:16px;font-weight:650}
.bar-label{display:flex;justify-content:space-between;gap:8px;font-size:11px;margin:0 0 4px;color:var(--dash-muted)}.bar-label strong{font-size:12px;color:var(--dash-fg);font-weight:600;font-variant-numeric:tabular-nums}
.track{height:15px;background:var(--dash-surface);border-radius:3px;margin-bottom:9px;overflow:hidden}.bar{height:100%;border-radius:3px}.bar.before{background:var(--dash-before)}.bar.after{background:var(--dash-after)}
.range{font-size:10px;color:var(--dash-muted);line-height:1.4;margin-top:2px;font-variant-numeric:tabular-nums}.unavailable{color:var(--dash-muted);font-size:12px;line-height:1.5;margin-top:28px}
.limits{font-size:11px;line-height:1.5;color:var(--dash-muted);margin:10px 0 0}.limits p{margin:4px 0}.limits strong{font-weight:600;color:var(--dash-fg)}
details{border-top:1px solid var(--dash-line);margin-top:11px;padding-top:9px;font-size:11px;color:var(--dash-muted)}summary{cursor:pointer}pre{font-size:10px;max-height:220px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--dash-fg);line-height:1.4;margin:9px 0 0}
@media(max-width:480px){.toolbar{margin-bottom:10px}.tabs{width:100%}.tabs button{flex:1;padding:8px 6px;font-size:12px}th,td{padding:8px 5px;font-size:12px}.charts{grid-template-columns:1fr;gap:8px}.chart{height:178px;padding:16px}.chart-top{margin-bottom:7px}.track{height:13px;margin-bottom:8px}.selection strong{font-size:15px}.selection span{font-size:10px}.caption{font-size:10px;margin-bottom:10px}}
@media(prefers-color-scheme:dark){:root{--dash-fg:var(--text-primary,var(--foreground,#e4eaf2));--dash-muted:var(--text-secondary,var(--muted-foreground,#a5b1c0));--dash-line:var(--border-color,var(--border,#344153));--dash-surface:var(--background-secondary,var(--muted,#202c3b));--dash-active:var(--background-primary,var(--background,#29394b));--dash-after:var(--accent-color,#319985);--dash-warning:var(--warning-color,#e6a36e)}}
</style>
<main>
<div class="toolbar"><div class="tabs" role="tablist" aria-label="Comparison">
<button type="button" role="tab" id="rust-tab" data-group="rust_revisions" aria-controls="comparison-panel">Rust before / after</button>
<button type="button" role="tab" id="go-tab" data-group="go_comparison" aria-controls="comparison-panel">Go / final Rust</button>
</div><span class="provisional" id="provisional" hidden>Provisional preview</span></div>
<section id="comparison-panel" role="tabpanel" aria-labelledby="rust-tab">
<div class="overview"><table aria-label="Paired CPU and peak memory changes"><thead><tr><th>Case</th><th>CPU Δ</th><th>Peak RSS Δ</th><th>Pairs</th></tr></thead><tbody id="overview-body"></tbody></table></div>
<p class="caption" id="table-caption">Δ is the median paired percent change. Negative means less resource use. Select a row to inspect it.</p>
<div class="picker"><label for="case-picker">Selected case</label><select id="case-picker"></select></div>
<div class="selection"><strong id="selected-name"></strong><span id="sample-count"></span></div>
<p class="status" id="selected-status" role="status" aria-live="polite"></p>
<div class="charts" id="charts"></div>
<div class="limits"><p id="sink-note"></p><p>The final controlled-artifact comparison uses the same 64 KiB write, fsync and SHA-read policy for Go and both Rust copies. The original RSS-sensitive campaign and failed gates remain in docs/evidence/cli-perf3. Changes vary by workload.</p><p>Compatibility labels apply to the selected workload. Small paired samples on a shared host; warmups excluded. Native child CPU and lifetime peak RSS. These are descriptive observations, without confidence intervals or a population speedup claim.</p><p id="ratio-note"></p></div>
<details><summary>Selected case provenance and measurement details</summary><pre id="provenance"></pre></details>
</section></main>
<script type="application/json" id="evidence">__EVIDENCE__</script>
<script>
'use strict';
const evidence=JSON.parse(document.getElementById('evidence').textContent);
const byId=id=>document.getElementById(id);
const finite=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
const fmt=(value,digits=1)=>finite(value)?value.toLocaleString(undefined,{minimumFractionDigits:digits,maximumFractionDigits:digits}):'—';
const change=value=>finite(value)?`${value>=1?'+':''}${((value-1)*100).toFixed(1)}%`:'—';
const ratio=value=>finite(value)?`${value.toFixed(3)}×`:'—';
const node=(tag,text,className)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;};
const sourceName=entry=>{const parts=(evidence.sources[entry.source].path||'').split('/');return parts[parts.length-2]||'Source report';};
function caseName(entry){
 if(entry.sink==='ingest fixture')return entry.case==='jsonl'?'JSONL ingestion':entry.case==='text'?'Text ingestion':entry.case;
 const names={'stream-1gib':'1 GiB raw text','cached-rawtext':'Cached raw text','stream-10gib':'10 GiB raw text','tiny-1m':'One million small rows','escaped-1m':'One million escaped rows','large-item-8mib':'Single 8 MiB item','large-items-8x8mib':'Eight 8 MiB items','large-items-gzip-8x8mib':'Eight 8 MiB items, gzip'};
 return names[entry.case]||entry.case.replaceAll('-',' ');
}
function eligible(entry,key){const metric=entry.metrics[key]||{};return entry.resource_comparison_admitted===true&&metric.complete_value_pairs===entry.paired_rounds;}
let group=evidence.comparisons.some(e=>e.comparison==='rust_revisions')?'rust_revisions':'go_comparison';
let selected=-1;
const remembered={};
function entries(){return evidence.comparisons.map((entry,index)=>({entry,index})).filter(item=>item.entry.comparison===group);}
function selectGroup(next){
 group=next;const list=entries();if(!list.length)return;
 for(const button of document.querySelectorAll('[data-group]')){button.setAttribute('aria-selected',String(button.dataset.group===group));button.tabIndex=button.dataset.group===group?0:-1;}
 byId('comparison-panel').setAttribute('aria-labelledby',group==='rust_revisions'?'rust-tab':'go-tab');
 const preferred=group==='rust_revisions'?['stream-1gib','cached-rawtext']:['stream-10gib'];
 selected=list.some(item=>item.index===remembered[group])?remembered[group]:((preferred.map(name=>list.find(item=>item.entry.case===name)).find(Boolean))||list[0]).index;
 const picker=byId('case-picker');picker.replaceChildren();
 for(const item of list){const option=node('option',`${caseName(item.entry)} · ${sourceName(item.entry)}`);option.value=String(item.index);picker.append(option);}
 renderOverview();selectCase(selected);
}
function renderOverview(){
 const body=byId('overview-body');body.replaceChildren();
 for(const {entry,index} of entries()){
  const row=node('tr');row.dataset.index=String(index);row.tabIndex=0;row.setAttribute('aria-selected',String(index===selected));
  row.setAttribute('aria-label',`Select ${caseName(entry)}, ${sourceName(entry)}`);
  const name=node('td');name.append(node('span',caseName(entry),'case-name'));name.append(node('span',sourceName(entry),'source-name'));row.append(name);
  for(const key of ['cpu_ms','peak_rss_bytes'])row.append(node('td',eligible(entry,key)?change(entry.metrics[key].paired_median_ratio):'—'));
  row.append(node('td',String(entry.paired_rounds??'—')));
  row.addEventListener('click',()=>selectCase(index));row.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectCase(index);}});
  body.append(row);
 }
}
function chart(entry,key,title,unit,scale){
 const metric=entry.metrics[key]||{};const figure=node('figure',undefined,'chart');
 const top=node('div',undefined,'chart-top');top.append(node('figcaption',title,'chart-title'));top.append(node('span',eligible(entry,key)?change(metric.paired_median_ratio):'—','delta'));figure.append(top);
 if(!eligible(entry,key)||!finite(metric.before_median)||!finite(metric.after_median)){
  figure.append(node('p',entry.resource_comparison_admitted?'Metric missing for one or more paired rounds.':'Comparison withheld; inspect the admission details below.','unavailable'));return figure;
 }
 const values=[metric.before_median/scale,metric.after_median/scale];const maximum=Math.max(...values,1e-12);
 const labels=group==='rust_revisions'?['Rust before','Final Rust']:['Go','Final Rust'];
 values.forEach((value,i)=>{
  const label=node('div',undefined,'bar-label');label.append(node('span',labels[i]));label.append(node('strong',`${fmt(value,key==='proc_io_syscw'?0:2)} ${unit}`));figure.append(label);
  const track=node('div',undefined,'track');const bar=node('div',undefined,`bar ${i?'after':'before'}`);bar.style.width=`${value/maximum*100}%`;track.append(bar);track.setAttribute('aria-hidden','true');figure.append(track);
 });
 figure.append(node('div',`Paired ratio ${ratio(metric.paired_median_ratio)} · range ${ratio(metric.paired_min_ratio)}–${ratio(metric.paired_max_ratio)} · n=${metric.paired_ratio_n??0}`,'range'));
 return figure;
}
function selectCase(index){
 selected=index;remembered[group]=index;const entry=evidence.comparisons[index];byId('case-picker').value=String(index);
 for(const row of byId('overview-body').children)row.setAttribute('aria-selected',String(Number(row.dataset.index)===index));
 byId('selected-name').textContent=caseName(entry);byId('sample-count').textContent=`${entry.paired_rounds??0} paired rounds`;
 const compatibility=entry.sink==='ingest fixture'&&entry.overall_ingest_compatibility_passed!==true?entry.overall_ingest_compatibility_passed:entry.compatible;
 const compatible=compatibility===true?'Compatibility passed':compatibility===false?'Compatibility failed':'Compatibility unestablished';
 const admitted=entry.resource_comparison_admitted===true;
 const resourceTargetFailed=entry.sink==='ingest fixture'&&evidence.sources[entry.source].gate?.resources?.passed===false;
 const status=byId('selected-status');status.className=admitted&&!resourceTargetFailed?'status':'status warning';
 status.textContent=`${compatible} · ${admitted?'Resource comparison admitted':`Comparison withheld: ${(entry.withheld_reasons||[]).join('; ')||'source admission failed'}`}`;
 if(resourceTargetFailed)status.textContent+=' · Source resource target failed';
 const charts=byId('charts');charts.replaceChildren(chart(entry,'cpu_ms','CPU time','ms',1),chart(entry,'peak_rss_bytes','Peak RSS','MiB',1048576));
 if(entry.sink==='ingest fixture')charts.append(chart(entry,'proc_io_syscw','Write system calls','calls',1));
 let sinkNote;
 if(entry.sink==='null')sinkNote=entry.full_pipe_parity===true?'Full output was checked through a pipe; timed resource runs discarded stdout. Throughput is withheld.':'Null-sink output validation is missing; resource and throughput comparisons are withheld.';
 else if(entry.sink==='ingest fixture')sinkNote='Ingestion measures the drain; the source report records resume, completion and durability checks. Write system calls are distinct from storage bytes and physical SSD wear.';
 else sinkNote=entry.throughput_admitted===true?'Pipe output; source performance admission passed. This view shows resource use; throughput remains available in the full evidence.':'Pipe output; throughput is withheld because source performance admission did not pass.';
 if((entry.source_summary.possibly_producer_bound_runs||0)>0)sinkNote+=' The producer or calibration reader may limit throughput.';
 byId('sink-note').textContent=sinkNote;
 byId('ratio-note').textContent=`Bars show separate medians. Δ and ratios use ${group==='rust_revisions'?'final / before Rust':'Rust / Go'} within matching rounds; lower means less resource use. Range is the observed minimum–maximum, not a confidence interval. Zero reference values have no ratio.`;
 const source=evidence.sources[entry.source];
 byId('provenance').textContent=JSON.stringify({provisional:evidence.provisional,generated_at:evidence.generated_at,input:evidence.input,case:entry.case,source_report:source,case_metadata:entry.case_metadata,resource_comparison_admitted:admitted,withheld_reasons:entry.withheld_reasons,full_pipe_parity:entry.full_pipe_parity,throughput_admitted:entry.throughput_admitted,source_summary:entry.source_summary,metrics:entry.metrics},null,2);
}
for(const button of document.querySelectorAll('[data-group]')){
 button.disabled=!evidence.comparisons.some(entry=>entry.comparison===button.dataset.group);
 button.addEventListener('click',()=>selectGroup(button.dataset.group));
 button.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const available=[...document.querySelectorAll('[data-group]')].filter(b=>!b.disabled);const target=event.key==='Home'?available[0]:event.key==='End'?available.at(-1):available.find(b=>b!==button)||button;selectGroup(target.dataset.group);target.focus();}});
}
byId('case-picker').addEventListener('change',event=>selectCase(Number(event.target.value)));
byId('provisional').hidden=!evidence.provisional;
selectGroup(group);
</script></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='results.json from summarize.py')
    parser.add_argument('--out', type=Path, default=Path(__file__).with_name('dashboard.html'))
    parser.add_argument('--provisional', action='store_true', help='label interim evidence visibly')
    args = parser.parse_args(argv)
    if args.input.resolve() == args.out.resolve():
        parser.error('input and output must differ')
    try:
        data = compact(json.loads(args.input.read_bytes()), args.input, args.provisional)
        # JSON lives in a script element: escape HTML-sensitive characters so
        # even a source path containing a closing script tag remains inert.
        encoded = json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        encoded = encoded.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
        rendered = PAGE.replace('__EVIDENCE__', encoded)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(str(error))
    print(args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
