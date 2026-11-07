import hashlib,json,os,random,ssl,statistics,subprocess,sys,tempfile,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'harness'))
import run
rootrepo=Path.cwd(); target=Path(sys.argv[1]); repetitions=int(sys.argv[2]) if len(sys.argv)>2 else 20
binaries={'base':rootrepo/'bin/pb-perf-baseline','final':rootrepo/'bin/pb-perf-final'}
requests=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  requests.append((self.command,self.path,self.headers.get('Authorization')))
  body=b'{"version":"fixture-1","grpcPort":8002}'
  assert self.path=='/api/v1/about' and self.headers.get('Authorization')=='Basic aGFybmVzczpoYXJuZXNz'
  self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
with tempfile.TemporaryDirectory(prefix='pb-perf-tls-') as folder:
 root=Path(folder); ca=rootrepo/'tests/fixtures/tls'; (root/'cert.pem').write_text(ssl.DER_cert_to_PEM_cert((ca/'server.der').read_bytes()))
 subprocess.run(['openssl','pkey','-inform','DER','-in',str(ca/'server-key.der'),'-out',str(root/'key.pem')],check=True,capture_output=True)
 bundle=root/'bundle.pem'; system=Path('/etc/ssl/certs/ca-certificates.crt').read_bytes();bundle.write_bytes(system+ssl.DER_cert_to_PEM_cert((ca/'ca.der').read_bytes()).encode())
 context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/'cert.pem',root/'key.pem')
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.socket=context.wrap_socket(server.socket,server_side=True)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 rng=random.Random(2026); samples={k:[] for k in binaries}; oracle=None
 for index in range(repetitions+3):
  order=list(binaries);rng.shuffle(order)
  for label in order:
   with tempfile.TemporaryDirectory(dir=root) as sample_dir:
    sample=Path(sample_dir);(sample/'tmp').mkdir();run.seed_config(sample,f'https://localhost:{server.server_port}')
    env=run.isolated_env(sample);env.update(SSL_CERT_FILE=str(bundle),SSL_CERT_DIR='/etc/ssl/certs')
    before=len(requests);m=sample/'meter.json';p=subprocess.run([str(rootrepo/'bin/pb-meter'),str(m),'--',str(binaries[label]),'status','-o','json'],env=env,cwd=sample,capture_output=True,timeout=10)
    assert p.returncode==0,(label,p.stderr); assert len(requests)==before+1
    contract=(p.stdout,p.stderr,requests[-1]);oracle=contract if oracle is None else oracle;assert contract==oracle
    raw=json.loads(m.read_text());s={k:raw[k] for k in ['peak_rss_bytes','cpu_user_ms','cpu_system_ms']};s['cpu_ms']=raw['cpu_user_ms']+raw['cpu_system_ms'];s['wall_ms']=(raw['ended_monotonic_ns']-raw['started_monotonic_ns'])/1e6
    if index>=3:samples[label].append(s)
 server.shutdown();server.server_close();thread.join()
 summary={}
 for metric in ['cpu_ms','peak_rss_bytes','wall_ms']:
  a=[s[metric] for s in samples['base']];b=[s[metric] for s in samples['final']];ratio=statistics.median(b)/statistics.median(a);draws=[]
  for _ in range(2000):draws.append(statistics.median(rng.choices(b,k=len(b)))/statistics.median(rng.choices(a,k=len(a))))
  summary[metric]={'baseline_median':statistics.median(a),'candidate_median':statistics.median(b),'ratio':ratio,'ratio_ci95':[run.percentile(draws,.025),run.percentile(draws,.975)]}
 report={'compatible':True,'repetitions':repetitions,'warmup':3,'samples':samples,'summary':summary,'binaries':{k:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size_bytes':p.stat().st_size} for k,p in binaries.items()},'system_bundle_sha256':hashlib.sha256(system).hexdigest(),'trust_directory':'/etc/ssl/certs','workload':'HTTPS status, real system roots plus fixture CA, localhost fixture; per-sample fresh process/config','stdout_sha256':hashlib.sha256(oracle[0]).hexdigest()}
 target.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(summary,indent=2))
