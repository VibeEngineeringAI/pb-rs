import pathlib,tempfile,os,subprocess,threading,json,time,hashlib
cache=pathlib.Path('/home/ajs/.cache/pb-phase7');out=cache/'saved-config-race-oracle2';out.mkdir(exist_ok=True)
for role,binary in [('go',cache/'baselines/pb-go'),('rust',pathlib.Path.cwd()/'target/debug/pb')]:
 home=out/role;home.mkdir(exist_ok=True);cfg=home/'config/pb/config.toml';cfg.parent.mkdir(parents=True,exist_ok=True);os.mkfifo(cfg,0o600)
 env=os.environ.copy();env.update(HOME=str(home),XDG_CONFIG_HOME=str(home/'config'),XDG_CACHE_HOME=str(home/'cache'),XDG_DATA_HOME=str(home/'data'),XDG_STATE_HOME=str(home/'state'),TMPDIR=str(cache/'tmp'),TERM='xterm-256color',NO_COLOR='1')
 for k in ['PB_PROFILE','PB_URL','PB_USERNAME','PB_PASSWORD','PB_API_KEY','PB_OUTPUT']:env.pop(k,None)
 p=subprocess.Popen([str(binary),'sql','list'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,start_new_session=True)
 error=[]
 def replace():
  try:
   for i in range(2 if role=='go' else 1):
    fd=os.open(cfg,os.O_WRONLY)
    os.write(fd,b'DefaultProfile="fixture"\n[Profiles.fixture]\nurl="http://127.0.0.1:1"\napiKey="fixture-key"\n')
    cfg.unlink()
    if i+1 < (2 if role=='go' else 1):os.mkfifo(cfg,0o600)
    else:cfg.write_bytes(b'BROKEN = [')
    os.close(fd)
  except Exception as e:error.append(repr(e))
 t=threading.Thread(target=replace,daemon=True);t.start()
 try:stdout,stderr=p.communicate(timeout=5)
 except subprocess.TimeoutExpired:p.kill();stdout,stderr=p.communicate();error.append('timeout')
 t.join(timeout=1);(home/'stdout.bin').write_bytes(stdout);(home/'stderr.bin').write_bytes(stderr)
 result={'binary':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'exit':p.returncode,'stdout':stdout.decode(errors='replace'),'stderr':stderr.decode(errors='replace'),'fixture_errors':error,'writer_alive':t.is_alive()};(home/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
