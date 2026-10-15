import os,pathlib,json,hashlib,time,types,threading,errno,re,argparse
parser=argparse.ArgumentParser(description='Bounded saved-filter clipboard progress under ignored Ctrl+G input; no timing claims')
for flag in ('go','go-sha','candidate','candidate-sha','out'):parser.add_argument('--'+flag,required=True)
args=parser.parse_args()
ROOT=pathlib.Path(__file__).resolve().parents[3];OUT=pathlib.Path(args.out).resolve();OUT.mkdir(parents=True,exist_ok=False)
for k in ('TMPDIR','GOTMPDIR','CLAUDE_CODE_TMPDIR'):os.environ[k]='/home/ajs/.cache/pb-phase7/tmp'
source=(ROOT/'tests/interactive_effects.rs').read_text();code=source.split('const PROCESS_FIXTURE: &str = r####"',1)[1].split('"####;',1)[0]
m=types.ModuleType('owned_fairness_fixture');exec(compile(code,'PROCESS_FIXTURE','exec'),m.__dict__);m.TIMEOUT=10
sha=lambda b:hashlib.sha256(b).hexdigest()
for role,binary,expected in [('go',args.go,args.go_sha),('candidate',args.candidate,args.candidate_sha)]:
 out=OUT/role;out.mkdir(exist_ok=False);helpers=out/'helpers';helpers.mkdir();started=out/'started';release=out/'release';completed=out/'completed'
 helper=helpers/'xclip';helper.write_text('#!/usr/bin/python3\nimport os,pathlib,time\np=pathlib.Path(os.environ["PB_FAIR_ROOT"])\n(p/"started").write_text("started")\ndeadline=time.monotonic()+10\nwhile not (p/"release").exists() and time.monotonic()<deadline:time.sleep(.005)\n(p/"completed").write_text("completed")\nprint("ZZCLIPDONE",end="",flush=True)\n');helper.chmod(0o700)
 old=dict(os.environ);os.environ.update(PATH=str(helpers)+':/usr/bin:/bin',PB_FAIR_ROOT=str(out),LANG='C.UTF-8',LC_ALL='C.UTF-8',COLORTERM='truecolor');os.environ.pop('WAYLAND_DISPLAY',None);os.environ.pop('NO_COLOR',None)
 assert sha(pathlib.Path(binary).read_bytes())==expected
 fixture=m.Fixture();fixture.routes[('GET','/api/v1/filters')]=(200,[{'filter_id':'id','filter_name':'fixture saved','stream_name':'logs','query':{'filter_type':'sql','filter_query':'SELECT 1'}}])
 s=None;stop=threading.Event();writer=None;stats={'writes':0,'bytes':0,'blocked':0};record={'role':role,'binary_sha256':expected,'kind':'bounded functional sustained ignored-input plus held clipboard; not performance','fixture_sha256':sha(code.encode()),'passed':False}
 try:
  s=m.Session(binary,['sql','list'],fixture);s.until('fixture saved');s.send(b'/');s.send(b'\x16')
  deadline=time.monotonic()+5
  while not started.exists():
   s.pump(.01)
   assert time.monotonic()<deadline,'clipboard helper never started'
  mark=len(s.output);os.set_blocking(s.master,False)
  def flood():
   payload=b'\x07'*2560; offset=0
   while not stop.is_set() or offset % 1:
    pending=payload[offset:] if not stop.is_set() else payload[offset:offset + (1-offset%1)]
    try:
     count=os.write(s.master,pending);offset=(offset+count)%len(payload);stats['writes']+=1;stats['bytes']+=count
    except BlockingIOError:stats['blocked']+=1;stop.wait(.0005)
    except OSError as e:
     if e.errno not in (errno.EIO,errno.EBADF):stats['error']=repr(e)
     return
  writer=threading.Thread(target=flood);writer.start()
  deadline=time.monotonic()+2
  while stats['bytes']<16384:
   s.pump(.001)
   assert time.monotonic()<deadline,'input feed never admitted 16KiB'
  hold_until=time.monotonic()+1.5
  while time.monotonic()<hold_until:s.pump(.002)
  record['bytes_before_clipboard_release']=stats['bytes'];release.write_text('release')
  deadline=time.monotonic()+4;visible=False
  while time.monotonic()<deadline:
   s.pump(.002)
   if 'ZZCLIPDONE' in s.text(mark):visible=True;break
  record.update(clipboard_visible_during_flood=visible,helper_completed=completed.exists(),feed=dict(stats))
  stop.set();writer.join(2);assert not writer.is_alive()
  assert stats['bytes'] > 0
  record['final_feed']=dict(stats);record['exact_ctrl_g_stream']=True
  os.set_blocking(s.master,True)
  # Drain finite already-admitted input before the ordinary quit control.
  if not visible:s.until('ZZCLIPDONE',after=mark,timeout=5)
  s.send(b'\x03');s.finish();s.unchanged();assert not s.error,s.error
  m.assert_requests(fixture,[('GET','/api/v1/filters')]);assert visible, 'clipboard did not progress during sustained input'
  record.update(passed=True,exit=0,terminal_restored=True,config_unchanged=True)
 except Exception as e:record['failure']=repr(e)
 finally:
  stop.set();release.touch()
  if writer:writer.join(2)
  if s:
   (out/'stdout.bin').write_bytes(s.output);(out/'stderr.bin').write_bytes(s.error);record['stdout_sha256']=sha(s.output);record['stderr_sha256']=sha(s.error);record['status_before_cleanup']=s.child.poll();s.close()
  fixture.close();os.environ.clear();os.environ.update(old);record['requests']=fixture.requests;record['binary_unchanged']=sha(pathlib.Path(binary).read_bytes())==expected;(out/'report.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps({k:v for k,v in record.items() if k not in ('requests','failure')}),flush=True)
