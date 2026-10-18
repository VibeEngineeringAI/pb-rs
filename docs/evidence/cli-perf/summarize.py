import json, math, random, statistics, sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'harness'))
from run import compare_samples, percentile
path=Path(sys.argv[1]); candidate=sys.argv[2]
r=json.loads(path.read_text()); out={'source_report':str(path),'candidate':candidate,'cases':{},'aggregate':{}}
for name,c in r['cases'].items():
    out['cases'][name]={m:compare_samples(c['runs']['base'],c['runs'][candidate],metric=m,seed=2026) for m in ['cpu_ms','wall_ms','peak_rss_bytes']}
for metric in ['cpu_ms','wall_ms','peak_rss_bytes']:
    cases=list(r['cases'].values()); ratios=[v[metric]['ratio'] for v in out['cases'].values() if 'ratio' in v[metric]]
    if len(ratios)!=len(cases): continue
    rng=random.Random(2026); draws=[]
    for _ in range(2000):
        values=[]
        for c in cases:
            a=[s[metric] for s in c['runs']['base'] if s['valid']]; b=[s[metric] for s in c['runs'][candidate] if s['valid']]
            values.append(statistics.median(rng.choices(b,k=len(b)))/statistics.median(rng.choices(a,k=len(a))))
        draws.append(math.exp(statistics.mean(map(math.log,values))))
    ratio=math.exp(statistics.mean(map(math.log,ratios))); hi=percentile(draws,.975)
    out['aggregate'][metric]={'geometric_mean_ratio':ratio,'ratio_ci95':[percentile(draws,.025),hi],'savings_percent':100*(1-ratio),'ten_percent_gate':ratio<=.9 and hi<=.9}
out['compatible']=all(v['cpu_ms']['compatible'] for v in out['cases'].values())
Path(sys.argv[3]).write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'compatible':out['compatible'],'aggregate':out['aggregate']},indent=2))
for name,c in out['cases'].items():
 print(name, 'CPU',round(c['cpu_ms'].get('percent_change',0),1),'RSS',round(c['peak_rss_bytes'].get('percent_change',0),1))
