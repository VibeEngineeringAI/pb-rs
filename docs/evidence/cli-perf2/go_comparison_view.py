"""Build the self-contained comparison from measured release-binary evidence."""
import csv
import io
import json
from pathlib import Path

root = Path(__file__).resolve().parent
summary = json.loads((root / 'go-comparison-summary.json').read_text())
assert summary['compatible']
names = {
    'version-text': 'Version · text', 'version-json': 'Version · JSON',
    'root-help': 'Root help', 'command-tree': 'Command discovery',
    'agent-catalog': 'Agent catalog', 'sql-help-json': 'SQL help',
    'missing-profile': 'Missing-profile error', 'profile-list': 'Profile list',
    'profile-add': 'Profile add', 'status-basic': 'Server status',
    'dataset-list': 'Dataset list', 'dataset-schema': 'Dataset schema',
    'sql-500-json': 'SQL · 500 rows · JSON',
    'sql-10000-text': 'SQL · 10k rows · text',
    'sql-10000-json': 'SQL · 10k rows · JSON', 'promql-range': 'PromQL range',
    'tail-4096': 'Flight tail · 4,096 rows',
    'p3-100000-json': 'SQL · 100k rows · JSON',
    'p3-single-8MiB-item': 'SQL · one 8 MiB item',
}
rows = [dict(id=k, name=names[k], metrics=v) for k, v in summary['cases'].items()]
data = dict(rows=rows, aggregate=summary['aggregate'], binaries=summary['binaries'])
out = io.StringIO()
writer = csv.writer(out)
writer.writerow(['workload', 'metric', 'go_median', 'rust_median', 'rust_go_ratio', 'ratio_ci95_low', 'ratio_ci95_high'])
for row in rows:
    for metric, m in row['metrics'].items():
        writer.writerow([row['id'], metric, m['reference_median'], m['candidate_median'], m['ratio'], *m['ratio_ci95']])
csv_text = out.getvalue()
(root / 'go-comparison.csv').write_text(csv_text)
page = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Current Rust CLI compared with pinned Go</title>
<style>
:root{font-family:var(--font-sans,system-ui,sans-serif);color:var(--foreground,#172033);--rust:#087e8b;--go:#8794a6}
body{margin:0;background:transparent;font-size:14px;line-height:1.5}*{box-sizing:border-box}
p{margin:0 0 14px}.muted,small{color:var(--muted-foreground,#637083)}.lead{font-size:19px;line-height:1.45}
.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:20px 0}.card{padding:16px;border:1px solid var(--border,#d8dde5);border-radius:var(--radius,10px)}
.big{font-size:28px;font-weight:750;line-height:1.2;margin:8px 0;color:var(--rust)}.card small{display:block;font-size:11px}
.section{margin:26px 0 10px;font-size:16px;font-weight:650}.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0}
button{font:inherit;border:1px solid var(--border,#ccd4df);background:var(--secondary,#edf1f5);color:inherit;padding:7px 12px;border-radius:6px;cursor:pointer}button[aria-pressed=true]{background:var(--primary,#172033);color:var(--primary-foreground,white)}button:focus-visible{outline:3px solid var(--ring,#339dbe);outline-offset:2px}
.legend{display:flex;gap:16px;font-size:12px;margin:12px 0}.dot{display:inline-block;width:10px;height:10px;margin-right:5px;border-radius:2px}.go{background:var(--go)}.rust{background:var(--rust)}
.plotrow{display:grid;grid-template-columns:190px minmax(0,1fr) 76px;gap:10px;align-items:center;min-height:31px;font-size:12px}.track{height:17px;background:color-mix(in srgb,var(--go) 24%,transparent);position:relative;border-right:2px solid var(--go)}.bar{height:17px;background:var(--rust);min-width:1px}.ci{position:absolute;top:7px;height:3px;background:var(--foreground,#172033)}.ci:before,.ci:after{content:'';position:absolute;width:1px;height:7px;top:-2px;background:inherit}.ci:after{right:0}.pct{text-align:right;font-variant-numeric:tabular-nums}.axis{display:flex;justify-content:space-between;margin-left:200px;margin-right:86px;font-size:11px;color:var(--muted-foreground,#637083)}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:8px 6px;border-bottom:1px solid var(--border,#d8dde5);text-align:right;font-variant-numeric:tabular-nums}th{font-weight:600;color:var(--muted-foreground,#637083)}th:first-child,td:first-child{text-align:left}td:not(:first-child){white-space:nowrap}.compat td{white-space:normal;text-align:left}.compat th{text-align:left}.compat td:first-child{width:29%}details{margin:20px 0}summary{cursor:pointer;font-weight:600}.note{font-size:12px;margin-top:12px}code{font-size:11px;overflow-wrap:anywhere}
@media(max-width:500px){.cards{gap:6px}.card{padding:16px}.big{font-size:22px}.plotrow{grid-template-columns:124px minmax(0,1fr) 61px;gap:6px;font-size:10px}.axis{margin-left:130px;margin-right:67px}.lead{font-size:17px}th,td{padding:7px 4px;font-size:11px}}
</style><body>
<p class="lead"><strong>Current Rust vs pinned Go:</strong> a direct comparison of the same 19 CLI workloads.</p>
<p class="muted">Fresh release-binary measurements · 20 runs per binary per workload · 3 warmups · randomized order · same executable tmpfs placement and CPU affinity.</p>
<div class="cards" id="cards"></div>
<p class="note">Summary percentages are equal-weight geometric means of per-workload ratios, not a production workload mix. CPU means total user + system CPU; elapsed time includes waiting. RSS means peak resident memory.</p>
<div class="section">Resource use relative to Go</div>
<div class="controls" role="group" aria-label="Choose chart metric"><button data-metric="cpu_ms" aria-pressed="true">CPU</button><button data-metric="wall_ms" aria-pressed="false">Elapsed time</button><button data-metric="peak_rss_bytes" aria-pressed="false">Peak memory</button></div>
<div class="legend"><span><i class="dot rust"></i>Rust</span><span><i class="dot go"></i>Go = 100%</span><span>Whiskers: 95% ratio interval</span></div>
<p class="note" id="caption" aria-live="polite"></p><div id="plot" role="img"></div><div class="axis"><span>0%</span><span>50%</span><span>100% of Go</span></div>
<div class="section">Actual median measurements</div>
<p class="muted" style="font-size:12px">Every cell reads <strong>Go → Rust</strong>. Lower is better. Memory uses MiB (1,048,576 bytes).</p>
<div class="scroll"><table><thead><tr><th>Workload</th><th>CPU · ms</th><th>Elapsed · ms</th><th>Memory · MiB</th></tr></thead><tbody id="values"></tbody></table></div>
<div class="section">Compatibility and scope</div>
<table class="compat"><thead><tr><th>Area</th><th>Current Rust compared with Go</th></tr></thead><tbody>
<tr><td>This benchmark</td><td>All 19 retained workload contracts match. Discovery checks project out Rust-only ingest/observe extensions.</td></tr>
<tr><td>Broader replay</td><td>23 retained groups passed: 4,181 strict checks, including parser/help/completion, HTTP, SQL, metrics, administration and real deadlines.</td></tr>
<tr><td>Existing differences</td><td>255 Flight cases retain a tonic-vs-Go User-Agent difference; payload/authentication checks pass. Eight terminal observations are documentary, not strict passes.</td></tr>
<tr><td>Rust extensions</td><td>Ingest/observe extensions are outside this Go performance comparison. The previously reported ingestion savings compare Rust revisions, not Rust with Go.</td></tr>
<tr><td>Coverage limits</td><td>No claim of universal parity. Device login and non-SQL/saved interactive workflows are outside the retained replay; historical exclusions remain unchanged.</td></tr>
<tr><td>Binary files</td><td id="sizes"></td></tr>
</tbody></table>
<details><summary>Measurement details and downloads</summary>
<p class="note">One host, seed 2041, warm local fixtures, stdout through a pipe, CPUs 2/4/6/8. Neither executable was profiled during timing. File placement was controlled because earlier measurements showed placement-dependent RSS. This measures these two builds, not intrinsic language performance or live-server response time. Confidence intervals use 2,000 bootstrap resamples and capture within-run variation, not variation between machines.</p>
<p class="note">Frozen Rust SHA-256: <code id="rusthash"></code><br>Pinned Go SHA-256: <code id="gohash"></code><br>Go toolchain: 1.25.4. Both report the harness version.</p>
<div class="controls"><button id="csv">Download measurements CSV</button><button id="json">Download evidence JSON</button></div></details>
<script>
const D=__DATA__,CSV=__CSV__;const metrics={cpu_ms:'CPU use',wall_ms:'Elapsed time',peak_rss_bytes:'Peak memory'};
const f=(x,n=2)=>Number(x).toLocaleString('en-US',{minimumFractionDigits:n,maximumFractionDigits:n});
document.getElementById('cards').innerHTML=Object.entries(metrics).map(([key,label])=>{const a=D.aggregate[key];return `<div class="card">${label}<div class="big">${f(a.savings_percent,1)}% less</div><small>95% interval: ${f(100*(1-a.ratio_ci95[1]),1)}–${f(100*(1-a.ratio_ci95[0]),1)}% less</small></div>`}).join('');
function chart(metric){document.querySelectorAll('[data-metric]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.metric===metric));document.getElementById('caption').textContent=`${metrics[metric]}: bars show Rust as a percentage of Go. Shorter bars use less; Go is the right edge of each track.`;const p=document.getElementById('plot');p.setAttribute('aria-label',metrics[metric]+' relative to Go, 19 workloads');p.innerHTML=D.rows.map(r=>{const m=r.metrics[metric],pct=m.ratio*100;return `<div class="plotrow"><span>${r.name}</span><div class="track" title="Rust ${f(pct,1)}% of Go; 95% interval ${f(m.ratio_ci95[0]*100,1)}–${f(m.ratio_ci95[1]*100,1)}%"><div class="bar" style="width:${pct}%"></div><div class="ci" style="left:${m.ratio_ci95[0]*100}%;width:${(m.ratio_ci95[1]-m.ratio_ci95[0])*100}%"></div></div><span class="pct">${f(pct,1)}%</span></div>`}).join('')}
document.querySelectorAll('[data-metric]').forEach(b=>b.onclick=()=>chart(b.dataset.metric));chart('cpu_ms');
document.getElementById('values').innerHTML=D.rows.map(r=>`<tr><td>${r.name}</td>${Object.keys(metrics).map(k=>{const m=r.metrics[k],unit=k==='peak_rss_bytes'?1048576:1;return `<td>${f(m.reference_median/unit)} → <strong>${f(m.candidate_median/unit)}</strong></td>`}).join('')}</tr>`).join('');
const g=D.binaries.go,r=D.binaries.rust;document.getElementById('sizes').textContent=`Measured files: Go ${f(g.size_bytes/1048576)} MiB → Rust ${f(r.size_bytes/1048576)} MiB (${f(100*(1-r.size_bytes/g.size_bytes),1)}% smaller). File size is distinct from runtime memory and depends on build/link settings.`;
document.getElementById('rusthash').textContent=r.sha256;document.getElementById('gohash').textContent=g.sha256;
function download(name,body,type){const url=URL.createObjectURL(new Blob([body],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
document.getElementById('csv').onclick=()=>download('rust-vs-go.csv',CSV,'text/csv');document.getElementById('json').onclick=()=>download('rust-vs-go.json',JSON.stringify(D,null,2),'application/json');
console.log('comparison checks',JSON.stringify({rows:D.rows.length,finite:D.rows.every(r=>Object.values(r.metrics).every(m=>Number.isFinite(m.ratio))),withinChart:D.rows.every(r=>Object.values(r.metrics).every(m=>m.ratio_ci95[1]<=1)),tableRows:document.querySelectorAll('#values tr').length}));
</script></body></html>'''
page = page.replace('__DATA__', json.dumps(data).replace('</', '<\\/')).replace('__CSV__', json.dumps(csv_text))
(root / 'go-comparison.html').write_text(page)
print(root / 'go-comparison.html')
