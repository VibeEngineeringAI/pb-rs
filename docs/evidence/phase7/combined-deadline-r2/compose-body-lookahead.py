from pathlib import Path
import ast,hashlib,json,subprocess,os,difflib
root=Path('/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive');out=Path('/home/ajs/.cache/pb-phase7/combined-body-lookahead-r2');out.mkdir(exist_ok=False)
body=Path('/home/ajs/.cache/pb-phase7/body-deadline-r1-fixes');look=Path('/home/ajs/.cache/pb-phase6/lookahead-deadline-proposal-v2')
sha=lambda b:hashlib.sha256(b).hexdigest()
for path,h in [(body/'body-deadline-r1-fixes.patch','40df1e798bc5ea26b2541682ad4820d3b212c64851562e4f467022ebc99a820d'),(look/'proposal.patch','68d5baa1bf38bdf7f59a7e5f05979f62d870f762b062cccafb623ec0ea1aa140')]:
 if sha(path.read_bytes())!=h:raise RuntimeError(str(path))
manifest={'status':'off-tree combined proposal; no promotion','body_patch':sha((body/'body-deadline-r1-fixes.patch').read_bytes()),'lookahead_patch':sha((look/'proposal.patch').read_bytes()),'variants':{},'scope':'two exact case/message entries, scoped tests; each base retains own deferred guard policy'}
def lf(s):return s.splitlines(keepends=True) if '\u2028' not in s else [x+'\n' for x in s.split('\n')[:-1]]+([s.split('\n')[-1]] if not s.endswith('\n') else [])
def method(s,name):
 tree=ast.parse(s);node=next(x for x in ast.walk(tree) if isinstance(x,ast.FunctionDef) and x.name==name);lines=s.split('\n');return '\n'.join(lines[node.lineno-1:node.end_lineno])+'\n'
def replace_once(s,a,b):
 if s.count(a)!=1:raise RuntimeError(('nonunique',a[:60]))
 return s.replace(a,b,1)
body_source=(body/'staged/harness/phase3.py').read_text();body_test=(body/'staged/harness/test_phase3.py').read_text();look_source=(look/'phase3.proposed.py').read_text();look_test=(look/'test_phase3.proposed.py').read_text()
look_names=['test_empty_array_lookahead_bare_deadline_exact_boundary','test_empty_array_lookahead_keeps_independent_and_differential_gates'];body_names=['test_body_stall_text_bare_deadline_has_an_exact_error_boundary','test_body_stall_text_normalization_keeps_all_existing_gates'];legacy='test_legacy_bare_deadline_contexts_remain_case_specific'
for variant in ['phase7','phase6']:
 if variant=='phase7':
  before={x:(root/'harness'/x).read_text() for x in ['phase3.py','test_phase3.py']}
  for x in before:
   if before[x]!=(body/'before/harness'/x).read_text():raise RuntimeError('body base drift')
  source=replace_once(body_source,'    bare_context = {\n','    bare_context = {\n        "p3-deadline-text-empty-array-lookahead": b"failed to read response body",\n')
  tests=replace_once(body_test,'    def test_paced_chunk_prefix_allowance_and_wall_bounds(self):','\n'.join(method(look_test,n) for n in look_names)+'\n    def test_paced_chunk_prefix_allowance_and_wall_bounds(self):')
 else:
  before={'phase3.py':(look/'phase3.before.py').read_text(),'test_phase3.py':(look/'test_phase3.before.py').read_text()}
  source=replace_once(look_source,'        "p3-deadline-not-reset-per-chunk": b"failed to stream response body",\n','        "p3-deadline-not-reset-per-chunk": b"failed to stream response body",\n        "p3-deadline-body-stall-text": b"failed to stream response body",\n')
  tests=replace_once(look_test,method(look_test,legacy),method(body_test,legacy))
  tests=replace_once(tests,'    def test_redirect_bare_deadline_has_an_exact_case_and_error_boundary(self):','\n'.join(method(body_test,n) for n in body_names)+'\n    def test_redirect_bare_deadline_has_an_exact_case_and_error_boundary(self):')
 after={'phase3.py':source,'test_phase3.py':tests};v=out/variant
 for where in ['before','staged','apply-check']:(v/where/'harness').mkdir(parents=True)
 patch=''
 for name in before:
  (v/'before/harness'/name).write_text(before[name]);(v/'staged/harness'/name).write_text(after[name]);(v/'apply-check/harness'/name).write_text(before[name]);patch+=''.join(difflib.unified_diff(lf(before[name]),lf(after[name]),fromfile='a/harness/'+name,tofile='b/harness/'+name))
 for n in body_names+[legacy]:
  if method(tests,n)!=method(body_test,n):raise RuntimeError('body method drift '+n)
 for n in look_names:
  if method(tests,n)!=method(look_test,n):raise RuntimeError('look method drift '+n)
 patchfile=v/'combined.patch';patchfile.write_text(patch)
 commands=[]
 for args in [['git','apply','--check','--verbose',str(patchfile)],['git','apply','--verbose',str(patchfile)]]:
  q=subprocess.run(args,cwd=v/'apply-check',capture_output=True,text=True);commands.append({'command':args,'exit':q.returncode,'stdout':q.stdout,'stderr':q.stderr})
  if q.returncode or 'offset' in q.stderr or 'fuzz' in q.stderr:raise RuntimeError(q.stderr)
 for name in after:
  if (v/'apply-check/harness'/name).read_bytes()!=(v/'staged/harness'/name).read_bytes():raise RuntimeError('applied bytes differ')
 (v/'staged/harness/contracts').symlink_to(root/'harness/contracts',target_is_directory=True);(v/'staged/bin').mkdir();(v/'staged/bin/pb-go').symlink_to(root/'bin/pb-go')
 manifest['variants'][variant]={'before':{n:sha(s.encode()) for n,s in before.items()},'after':{n:sha(s.encode()) for n,s in after.items()},'patch':str(patchfile),'patch_sha256':sha(patch.encode()),'apply':commands,'scoped_test_methods_preserved_exactly':True}
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(manifest,indent=2))
