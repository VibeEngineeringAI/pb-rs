"""Pure causal O1 controls; no production or harness mutation."""
import io,json,hashlib,sys,unittest
from pathlib import Path
from unittest import mock
ROOT=Path('/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive')
OUT=Path('/home/ajs/.cache/pb-phase7/phase6-merged-integration/o1-auditable')
OUT.mkdir(exist_ok=False)
def guard(event,args):
 if event in {'subprocess.Popen','os.system','os.fork','os.posix_spawn','pty.spawn','socket.connect','ctypes.dlopen'}:raise RuntimeError('forbidden '+event)
sys.addaudithook(guard)
sys.path.insert(0,str(ROOT/'harness'));import test_phase4 as t
method='test_deferred_stub_guard_remains_active';cls=next(v for v in vars(t).values() if isinstance(v,type) and issubclass(v,unittest.TestCase) and method in v.__dict__)
paths=[ROOT/'harness/test_phase4.py',ROOT/'harness/phase4.py',ROOT/'harness/phase3.py'];sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();before={str(p):sha(p) for p in paths};original=t.phase4.p3.boundary_issues
records=[]
for mode in ['baseline','empty-historical','drop-network']:
 patch=mock.patch.object(t.phase4,'historical_deferred_boundaries',return_value=[]) if mode=='empty-historical' else mock.patch.object(t.phase4.p3,'boundary_issues',side_effect=lambda *a:[x for x in original(*a) if 'network' not in x]) if mode=='drop-network' else mock.patch.object(t.phase4.p3,'boundary_issues',wraps=original)
 with patch:
  stream=io.StringIO();r=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.TestSuite([cls(method)]))
 log=OUT/(mode+'.log');log.write_text(stream.getvalue());expected=0 if mode=='baseline' else 1
 if len(r.failures)!=expected or r.errors:raise AssertionError(mode)
 records.append({'mode':mode,'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'log':str(log),'log_sha256':sha(log)})
assert before=={str(p):sha(p) for p in paths}
record={'scope':'source-only in-memory mutations; no native child','commands':[sys.executable,__file__],'script_sha256':sha(Path(__file__)),'inputs_before_and_after':before,'checks':records}
(OUT/'report.json').write_text(json.dumps(record,indent=2)+'\n');print('three causal controls passed, sources unchanged')
