"""Instrumented syscall attribution; not release timing or output parity evidence."""
import sys,json,subprocess,hashlib,time
from pathlib import Path
root=Path(__file__).resolve().parents[3];sys.path.insert(0,str(root/'harness'))
import stress
out=root/'harness/results/perf3/trace-final';out.mkdir(parents=True,exist_ok=False)
for label,name in [('go','pb-go'),('baseline','pb-perf2-final'),('candidate','pb-perf3-final')]:
 d=out/label;d.mkdir();sandbox=d/'sandbox'
 for sub in ['tmp','cache','work']:(sandbox/sub).mkdir(parents=True)
 cfg={'rows':131072,'row_bytes':1024,'shape':'ascii'}
 fixture=stress.Fixture(root/'bin/pb-stress-fixture',cfg,d)
 try:
  stress.harness.seed_config(sandbox,fixture.url)
  binary=root/'bin'/name;cli=[str(binary),'sql','run','SELECT * FROM logs LIMIT 131072','--from','2024-01-01T00:00:00Z','--to','2024-01-01T01:00:00Z','-o','text']
  command=['/usr/bin/strace','-f','-c','-qq','-o',str(d/'strace.txt'),*cli]
  with open(d/'stderr.txt','wb') as err:
   p=subprocess.run(command,env=stress.harness.isolated_env(sandbox),cwd=sandbox/'work',stdout=subprocess.DEVNULL,stderr=err,timeout=90)
  stats=fixture.stats();report={'scope':'strace-instrumented diagnostic; timings not performance evidence; stdout discarded, no output parity claim','command':command,'exit_code':p.returncode,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'fixture_stats':stats};(d/'report.json').write_text(json.dumps(report,indent=2)+'\n')
  print(label,p.returncode,stats.get('completed'),flush=True)
 finally:fixture.close()
