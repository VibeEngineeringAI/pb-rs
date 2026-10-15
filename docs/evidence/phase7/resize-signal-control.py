import fcntl,hashlib,json,os,pathlib,pty,select,signal,struct,subprocess,termios,time
import argparse
parser=argparse.ArgumentParser(description='Bounded native PTY signal control; no CLI/performance measurement')
parser.add_argument('--out',type=pathlib.Path,required=True)
args=parser.parse_args()
p=args.out
p.mkdir(parents=True,exist_ok=False)
source=pathlib.Path(__file__).with_name('resize-signal-probe.c')
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
compiler=pathlib.Path('/usr/bin/cc').resolve()
argv=[str(compiler),'-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(p/'probe')]
subprocess.run(argv,check=True)
m,s=pty.openpty()
setsize=lambda rows,cols:fcntl.ioctl(s,termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))
setsize(24,80)
child=subprocess.Popen([str(p/'probe')],stdin=s,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
observations=[]
def take(expected,timeout=1):
 ready=select.select([child.stdout],[],[],timeout)[0]
 data=os.read(child.stdout.fileno(),1000) if ready else b''
 assert data==expected,(data,expected)
 return data.decode()
try:
 take(b'READY\n')
 setsize(30,100);observations.append({'action':'changed size ioctl only','output':take(b'WINCH\n')})
 setsize(30,100);observations.append({'action':'same size ioctl only','output':take(b'',0.2)})
 os.killpg(child.pid,signal.SIGWINCH);observations.append({'action':'explicit signal after same size','output':take(b'WINCH\n')})
 setsize(32,110);observations.append({'action':'second changed size ioctl only','output':take(b'WINCH\n')})
 os.killpg(child.pid,signal.SIGWINCH);observations.append({'action':'explicit signal after changed size, serialized','output':take(b'WINCH\n')})
 os.write(m,b'q\n');assert child.wait(timeout=2)==0;stderr=child.stderr.read();assert stderr==b''
finally:
 if child.poll() is None:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=2)
 child.stdout.close();child.stderr.close();os.close(m);os.close(s)
report={'kind':'own native PTY child signal delivery functional control; not CLI or performance', 'observations':observations,'compiler':str(compiler),'compiler_sha256':sha(compiler),'compile_argv':argv,'source_sha256':sha(source),'probe_sha256':sha(p/'probe'),'runner_sha256':sha(pathlib.Path(__file__)),'kernel':os.uname().release,'exit':child.returncode,'limits':'Serialized delivery proves ioctl and explicit signal can each deliver. Back-to-back signal coalescing and actual CLI redraw count not measured. No helper/source change.'}
(p/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
