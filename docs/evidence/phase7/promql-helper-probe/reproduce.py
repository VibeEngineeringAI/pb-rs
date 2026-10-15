from pathlib import Path
import random,subprocess,json,hashlib,os
root=Path.cwd(); out=Path(os.environ['PB_PHASE7_PROBE_DIR']);out.mkdir(parents=True,exist_ok=True);(out/"tmp").mkdir(exist_ok=True)
s=Path('src/tui/promql.rs').read_text();sort=s[s.index('mod go_sort {'):s.index('// ─── lipgloss helpers used by the PromQL views')];floats=s[s.index('pub(crate) fn go_parse_float'):s.index('/// `promqlChartLabelSet`')]
rng=random.Random(71014);lines=['F\t'+x for x in ['0x1.8p-1075','-0x1p-1075','0x1.fffffffffffff8p1023','0x1.0000000000000801p0','NaN','+NaN','+Inf','1e999','0x_1p4','0x1__0p0']]
for _ in range(500):
 n=rng.randrange(1,40);mant=''.join(rng.choice('0123456789abcdef') for _ in range(n));dot=rng.randrange(n+1);lines.append('F\t'+rng.choice(['','-','+'])+'0x'+mant[:dot]+'.'+mant[dot:]+'p'+str(rng.choice([-1077,-1075,-1074,-1022,0,1,1020,1023,1024,rng.randrange(-1200,1200)])))
for kind in ['U','S']:
 for n in [0,1,2,11,12,13,14,19,20,21,31,32,49,50,99,100,256,1000]:
  for mode in range(4):
   values=[str(rng.randrange(10)) if mode<2 else str(i if mode==2 else n-i) for i in range(n)]
   if kind=='S' and mode==1:
    values=[('NaN' if i%7==0 else ('+Inf' if i%11==0 else v)) for i,v in enumerate(values)]
   lines.append(kind+'\t'+','.join(values))
(out/'inputs.txt').write_text('\n'.join(lines)+'\n')
rmain=r'''
use std::io::{self,BufRead};
fn main(){for line in io::stdin().lock().lines(){let line=line.unwrap();let (kind,text)=line.split_once('\t').unwrap();if kind=="F" {match go_parse_float(text) {Some(v) if v.is_nan()=>println!("nan"),Some(v)=>println!("{:016x}",v.to_bits()),None=>println!("error")};}else{let mut data:Vec<(f64,usize)>=if text.is_empty(){Vec::new()}else{text.split(',').enumerate().map(|(i,x)|(x.parse().unwrap(),i)).collect()};if kind=="U"{go_sort::slice(&mut data,|a,b|a.0<b.0);}else{go_sort::slice_stable(&mut data,|a,b|if a.0==b.0 {a.1<b.1}else{a.0>b.0});}println!("{}",data.iter().map(|x|x.1.to_string()).collect::<Vec<_>>().join(","));}}}
'''
(out/'probe.rs').write_text(sort+floats+rmain)
gmain=r'''package main
import("bufio";"fmt";"os";"strings";"strconv";"math";"sort")
type item struct{value float64;id int}
func main(){scan:=bufio.NewScanner(os.Stdin);for scan.Scan(){p:=strings.SplitN(scan.Text(),"\t",2);kind,text:=p[0],p[1];if kind=="F"{v,e:=strconv.ParseFloat(text,64);if e!=nil{fmt.Println("error")}else if math.IsNaN(v){fmt.Println("nan")}else{fmt.Printf("%016x\n",math.Float64bits(v))}}else{data:=[]item{};if text!=""{for i,x:=range strings.Split(text,","){v,_:=strconv.ParseFloat(x,64);data=append(data,item{v,i})}};if kind=="U"{sort.Slice(data,func(i,j int)bool{return data[i].value<data[j].value})}else{sort.SliceStable(data,func(i,j int)bool{if data[i].value==data[j].value{return data[i].id<data[j].id};return data[i].value>data[j].value})};v:=[]string{};for _,x:=range data{v=append(v,strconv.Itoa(x.id))};fmt.Println(strings.Join(v,","))}}}
'''
(out/'main.go').write_text(gmain)
env=os.environ.copy();env.update(TMPDIR=str(out/'tmp'),GOTMPDIR=str(out/'tmp'))
subprocess.run(['rustc','--edition=2024',str(out/'probe.rs'),'-o',str(out/'probe-rust')],check=True,env=env)
subprocess.run(['go','build','-o',str(out/'probe-go'),str(out/'main.go')],check=True,env=env)
for lang in ['rust','go']:
 with (out/'inputs.txt').open('rb') as f,(out/(lang+'.txt')).open('wb') as target:subprocess.run([str(out/('probe-'+lang))],stdin=f,stdout=target,check=True,env=env)
a=(out/'go.txt').read_text().splitlines();b=(out/'rust.txt').read_text().splitlines();diff=[{'index':i,'input':v,'go':x,'rust':y} for i,(v,x,y) in enumerate(zip(lines,a,b)) if x!=y]
report={'cases':len(lines),'go_lines':len(a),'rust_lines':len(b),'mismatches':diff,'extracted_sort_sha256':hashlib.sha256(sort.encode()).hexdigest(),'extracted_float_sha256':hashlib.sha256(floats.encode()).hexdigest(),'scope':'standalone exact source extraction; functional Go/Rust helper differential, not full CLI or performance evidence','seed':71014}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
