from pathlib import Path
import os,subprocess,json,hashlib,sys
root=Path('/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive');out=Path(__file__).resolve().parent;sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();m=json.loads((out/'manifest.json').read_bytes())
env=os.environ.copy();env.update(PYTHONPATH=str(root/'harness'),PB_PHASE3_FIXTURE=str(root/'bin/pb-fixture'),PYTHONDONTWRITEBYTECODE='1',TMPDIR='/home/ajs/.cache/pb-phase7/tmp',GOTMPDIR='/home/ajs/.cache/pb-phase7/tmp',CLAUDE_CODE_TMPDIR='/home/ajs/.cache/pb-phase7/tmp')
inputs=[root/'harness/phase3.py',root/'harness/test_phase3.py',root/'harness/phase1.py',root/'harness/phase2.py',root/'harness/run.py',root/'bin/pb-go',root/'bin/pb-fixture',out/'manifest.json',Path(__file__)]
inputs += [out/v/'staged/harness'/f for v in m['variants'] for f in ['phase3.py','test_phase3.py']]
before={str(p):sha(p) for p in inputs};record={'kind':'coordinator actual full shared suite on off-tree combined variants; no promotion','inputs_before':before,'runs':[]}
for v in m['variants']:
 stage=out/v/'staged/harness'
 for optimize in [False,True]:
  label=v+('-optimized' if optimize else '-normal');log=out/(label+'.log')
  command=[sys.executable,*(['-O'] if optimize else []),'-c',"import sys, pathlib, unittest, phase3; expected=pathlib.Path(sys.argv[1]); print('IMPORTED_RUNNER',phase3.__file__,flush=True); print('OPTIMIZE',sys.flags.optimize,flush=True); assert pathlib.Path(phase3.__file__).resolve()==expected/'phase3.py'; import test_phase3; result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_phase3)); sys.exit(0 if result.wasSuccessful() and not result.skipped and result.testsRun==37 else 1)",str(stage)]
  runenv=env|{'PYTHONPATH':str(stage)+os.pathsep+str(root/'harness')}
  with log.open('x') as output:q=subprocess.run(command,cwd=root,env=runenv,stdout=output,stderr=subprocess.STDOUT)
  record['runs'].append({'label':label,'command':command,'exit':q.returncode,'log':str(log),'sha256':sha(log)})
  record['inputs_after']={str(p):sha(p) for p in inputs};record['unchanged']=record['inputs_after']==before
  (out/'full-suite.json').write_text(json.dumps(record,indent=2)+'\n');print(label,q.returncode,flush=True)
  if q.returncode or not record['unchanged']:sys.exit(1)
