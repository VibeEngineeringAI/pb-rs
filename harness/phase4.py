#!/usr/bin/env python3
"""Isolated phase-four executable characterization and frozen differential gate."""
import argparse
import base64
import copy
import json
from pathlib import Path
import sys
import urllib.parse
import phase3 as p3
import phase1
import phase2
import run

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / 'contracts' / 'phase4-reference.json'
START, END = p3.START, p3.END
PREFIX = '/prometheus/api/v1/'

def encoded(pairs):
    return urllib.parse.urlencode(sorted(pairs), doseq=True, safe='~')

def corpus():
    cases = []
    def add(name, argv, endpoint=None, body=None, params=(), output=None, error=None, status=None, routes=None, **extra):
        expectation = p3.expect(1 if error else 0, requests=([('GET ' + endpoint)] if endpoint else []))
        if output is not None: expectation['stdout_exact'] = p3.pin(output)
        if error: expectation.update(stdout_exact=p3.pin(b''), stderr_has=[p3.pin(error)])
        if routes is None and endpoint:
            routes = [p3.route(body, method='GET', path=endpoint, status=status)]
        case = dict(name='p4-'+name, group=extra.pop('group', 'metrics'), tier='gate', build='standard', requires=[],
                    argv=argv, config={'auth':'basic'}, location='xdg', fixture={'headers':p3.HEADERS['basic'], 'routes':routes or []},
                    env={}, unset=[], expect=expectation, compare={'spinner':True}, **extra)
        if endpoint: case['targets']=[endpoint + ('?' + encoded(params) if params else '')]
        cases.append(case)
        return case
    def metric(name, args, endpoint, data, params, text=None):
        body=json.dumps({'status':'success','data':data}, ensure_ascii=False, separators=(',',':')).encode()
        for fmt in ('text','json'):
            add(name+'-'+fmt, ['promql',*args,'-o',fmt], PREFIX+endpoint, body, params,
                text if fmt=='text' else json.dumps(json.loads(body),indent=2,ensure_ascii=False,sort_keys=True).encode()+b'\n')
    instant=['run','up','--instant','--to',END,'--from','garbage','--step','garbage']
    vector={'resultType':'vector','result':[{'metric':{'__name__':'up','a':'quote"','a.b':'x'},'value':[1704067200.9,'1']},
            {'metric':{},'value':['stamp',1.25]},{'metric':{'__name__':'skip'},'value':[0]}]}
    metric('instant',instant,'query',vector,[('query','up'),('stream','select-dataset'),('time',END)],
           b'up{a.b="x", a="quote""}\n  2024-01-01T00:00:00Z  1\n\n\n  stamp  1.25\n\nskip\n\nresult_type=vector  series=3\n')
    matrix={'resultType':'matrix','result':[{'metric':{'__name__':'x'},'values':[[0,'NaN'],[1.9,2],[2],[None,True]]}]}
    metric('range',['run','x','-f',START,'-t',END,'--step',' 1m30s '],'query_range',matrix,
           [('query','x'),('stream','select-dataset'),('start',START),('end',END),('step','1m30s')],
           b'x\n  1970-01-01T00:00:00Z  NaN\n  1970-01-01T00:00:01Z  2\n  <nil>  true\n\nresult_type=matrix  series=1\n')
    for op in ('labels','label-values'):
        args=[op]+(['job'] if op=='label-values' else [])+['pos','-d','ignored','-f','invalid','-t','2024-01-01T01:01:00.5+01:00']
        metric(op,args,'labels' if op=='labels' else 'label/job/values',['z','a'], [('stream','pos'),('end',END)], b' \xe2\x80\xa2 z\n \xe2\x80\xa2 a\n'+(b'\nlabel=job  total=2\n' if op=='label-values' else b''))
    metric('series',['series','pos','-m','{a=~"x ~"}','-m','{b="+&"}'],'series',[],
           [('stream','pos'),('match[]',['{a=~"x ~"}','{b="+&"}'])],b'No series found.\n')
    for op,ep in [('label-names','label_names'),('label-values','label_values')]:
        params=[('stream','pos'),('limit','-2'),('lookback','-1')]
        args=['cardinality',op,'pos','--limit=-2','--lookback=-1']
        if op=='label-values': args+=['override','--label','ignored'];params+=[('label_name','override')]
        metric('cardinality-'+op,args,'cardinality/'+ep,[],params,
               (('LABEL' if op=='label-names' else 'VALUE')+'    '+('DISTINCT VALUES' if op=='label-names' else 'SERIES COUNT')+'\n'+ '─'*(9+(15 if op=='label-names' else 12))+'\n\n').encode())
    metric('active-series',['cardinality','active-series','pos','{a="override"}','--selector','ignored'],'cardinality/active_series',
           {'total_active_series':0,'series':[]}, [('stream','pos'),('lookback','3600'),('limit','20'),('selector','{a="override"}')],
           b'ACTIVE SERIES\ntotal=0\n\nNo active series found.\n')
    for alias in ('active-queries','ps'):
        metric(alias,[alias],'status/active_queries',[],[],b'No active queries.\n')
    metric('tsdb',['tsdb','pos','--top=-1','--date','arbitrary','--focus-label','a &'],'status/tsdb',None,
           [('stream','pos'),('topN','-1'),('date','arbitrary'),('focusLabel','a &')],
           b'No TSDB stats found. Pass a metrics dataset name, for example: pb promql tsdb <dataset>\n')
    for name,body,error in [('empty',b'',b'failed to decode PromQL labels response'),('malformed',b'{',b'failed to decode PromQL labels response'),
          ('wrong-shape',b'{"data":{}}',b'failed to decode PromQL labels response'),('envelope',b'{"status":"error","errorType":"bad","error":"oops"}',b'PromQL error (bad): oops')]:
        add('labels-'+name,['promql','labels'],PREFIX+'labels',body,[('stream','select-dataset')],error=error)
    for label,path in [('a/b','label/a/b/values'),('../x','x/values'),('a%2Fb','label/a%2Fb/values'),('a?b','label/a%3Fb/values'),('a#b','label/a%23b/values'),('é','label/%C3%A9/values')]:
        c=add('label-path-'+label.encode().hex(),['promql','label-values',label,'-o','json'],PREFIX+path,b'null',
            [('stream','select-dataset')],b'null\n')
        c['fixture']['routes'][0]['path']=urllib.parse.unquote(PREFIX+path)
        c['expect']['requests']=['GET '+urllib.parse.unquote(PREFIX+path)]
    for alias in ('list','ls'):
        for fmt in ('text','json'):
            c=add('saved-'+alias+'-'+fmt,['sql',alias,'extra','-o',fmt],'/api/v1/filters',b'[]',output=b'' if fmt=='text' else b'[]\n',group='saved')
    for status in (200,201,500):
        c=add('save-'+str(status),['sql','save',' select * from logs ','--name',' trimmed ','-f',START,'-t',END],
              output=b"Query saved as 'trimmed'\n" if status==200 else None,error=b'server returned' if status!=200 else None,
              routes=[p3.route(b'failure',status=status,path='/api/v1/filters')],group='saved')
        c['expect']['requests']=['POST /api/v1/filters']
        c['save_body']={'filter_name':'trimmed','query':{'filter_type':'sql','filter_query':'select * from logs LIMIT 500'}}
    for status in (200,500):
        c=add('run-save-'+str(status),p3.sql('select * from logs','--save-as',' raw ','-o','json'),output=b'[]\n',
              routes=[p3.route(),p3.route(b'failure',status=status,path='/api/v1/filters')],group='saved')
        c['expect']['requests']=['POST /api/v1/query','POST /api/v1/filters']
        c['expect']['stderr_has']=[p3.pin(b"Query saved as ' raw '" if status==200 else b'warning: could not save query:')]
        c['save_body']={'filter_name':' raw ','query':{'filter_type':'sql','filter_query':'select * from logs LIMIT 500'}}
    c=add('query-failure-never-saves',p3.sql('select * from logs','--save-as','x'),error=b'query failed',routes=[p3.route(b'failure',status=500)],group='saved')
    c['expect']['requests']=['POST /api/v1/query']
    add('saved-whitespace-output',['sql','list','-o',' '],'/api/v1/filters',b'[]',output=b'',group='saved')
    # Generic JSON is independently modeled: duplicate keys, lexemes, HTML and trailing documents.
    add('json-lexemes',['promql','labels','-o','json'],PREFIX+'labels',b'{"z":1e+03,"a":"<>&","z":-0.00}',
        [('stream','select-dataset')],b'{\n  "a": "<>&",\n  "z": -0.00\n}\n')
    add('json-second-document',['promql','labels','-o','json'],PREFIX+'labels',b'{} {}',
        [('stream','select-dataset')],error=b'JSON')
    for shape in ('scalar','string','histogram'):
        body=json.dumps({'status':'success','data':{'resultType':shape,'result':[{'metric':{'__name__':'x'},'value':[0,'1']}]}}).encode()
        add('run-unsupported-'+shape,['promql',*instant],PREFIX+'query',body,
            [('query','up'),('stream','select-dataset'),('time',END)],('x\n\nresult_type='+shape+'  series=1\n').encode())
    add('run-scalar-shape',['promql',*instant],PREFIX+'query',b'{"data":{"resultType":"scalar","result":[0,"1"]}}',
        [('query','up'),('stream','select-dataset'),('time',END)],error=b'failed to decode PromQL response')
    long_query='a'*44+'é'+'b'*10
    active={'status':'success','data':[{'query':long_query,'stream':'s','started_at':'t','elapsed_ms':3}]}
    raw=long_query.encode()[:45]+b'...'
    header=f"{'QUERY':<50}  {'STREAM':<15}  {'STARTED':<22}  ELAPSED\n".encode()+b'-'*100+b'\n'
    # Go fmt counts an invalid trailing UTF-8 byte as one rune for padding, preserves the byte.
    out=header+raw+b'  '+b'  '+b's'+b' '*14+b'  '+b't'+b' '*21+b'  3ms\n'
    add('active-byte-truncation',['promql','ps'],PREFIX+'status/active_queries',json.dumps(active,ensure_ascii=False).encode(),output=out)
    filters=[{'filter_id':'1','filter_name':'a, b','stream_name':'s','query':{'filter_type':'builder','filter_query':'select 1'},'time_filter':{'from':'f'}},
             {'filter_id':'skip','query':{'filter_query':None}},{'filter_id':'empty','query':{'filter_query':''}}]
    projection=[{'id':'1','title':'a, b','stream':'s','desc':'select 1','from':'f'}, {'id':'empty','title':'','stream':'','desc':''}]
    for fmt in ('text','json'):
        add('saved-projection-'+fmt,['sql','list','-o',fmt],'/api/v1/filters',json.dumps(filters).encode(),
            output=b'a, b, s, select 1, f\n\n' if fmt=='text' else json.dumps(projection,indent=2,sort_keys=True).encode()+b'\n',group='saved')
        if fmt=='json':
            # writeJSON marshals struct fields in declaration order.
            cases[-1]['expect'].pop('stdout_exact');cases[-1]['expect']['stdout_json']=projection
    for body in (b'null',b'{}',b'[{"query":{"filter_builder":{"id":1}}}]'):
        add('saved-shape-'+body.hex(),['sql','list','-o','json'],'/api/v1/filters',body,
            output=b'[]\n' if body==b'null' else None,error=None if body==b'null' else b'failed to decode saved-query response',group='saved')
    for fmt in ('text','json'):
        add('gzip-'+fmt,['promql','labels','-o',fmt],PREFIX+'labels',b'{"data":["x"]}',[('stream','select-dataset')],
            b' \xe2\x80\xa2 x\n' if fmt=='text' else b'{\n  "data": [\n    "x"\n  ]\n}\n')
        cases[-1]['fixture']['routes'][0]['gzip']=True
    for status in (201,204,299):
        add('http-'+str(status),['promql','labels','-o','json'],PREFIX+'labels',b'' if status==204 else b'null',[('stream','select-dataset')],
            b'null\n' if status!=204 else None,error=b'JSON' if status==204 else None,status=status)
    c=add('save-does-not-await-eof',['sql','save','select 1','--name','x','-f',START,'-t',END],output=b"Query saved as 'x'\n",
          routes=[p3.route(b'never complete',path='/api/v1/filters',framing='chunked',script=[{'stall':True}])],group='saved')
    c['expect']['requests']=['POST /api/v1/filters'];c['expect']['max_wall_ms']=1500
    c=add('save-time-failure-after-query',p3.sql('select 1','--save-as','x','--from','invalid','-o','json'),output=b'[]\n',routes=[p3.route()],group='saved')
    c['expect']['requests']=['POST /api/v1/query'];c['expect']['stderr_has']=[p3.pin(b'warning: could not save query: invalid start time')]
    for name,args,error in [
        ('blank-expression',['promql','run','  ','-t',END],b'PromQL expression is required'),
        ('to-before-expression',['promql','run','  ','-t','invalid'],b'invalid --to'),
        ('output-before-time',['promql','run','up','-o','xml','-t','invalid'],b'output format'),
        ('series-match-required',['promql','series'],b'match'),
        ('save-blank',['sql','save',' '],b'SQL query is required'),
        ('active-no-args',['promql','ps','extra'],b'unknown command'),
    ]: add(name,args,error=error,group='validation')
    for auth in ('api-key','cloud-key','cloud-cookie'):
        # Auth modes use the common profile seeder and independent header assertion.
        if auth not in p3.HEADERS: continue
        c=add('auth-'+auth,['promql','labels','-o','json'],PREFIX+'labels',b'null',[('stream','select-dataset')],b'null\n',group='transport')
        c['config']={'auth':auth};c['fixture']['headers']=p3.HEADERS[auth]
    for status in (302,307):
        c=add('redirect-'+str(status),['promql','labels','-o','json'],output=b'null\n',routes=[
            p3.route(b'',status,method='GET',path=PREFIX+'labels',headers_out=[['Location','/redirected']]),
            p3.route(b'null',method='GET',path='/redirected')],group='transport')
        c['expect']['requests']=['GET '+PREFIX+'labels','GET /redirected']
    c=add('body-error-atomic',['promql','labels'],PREFIX+'labels',b'{"data":["x"]}',[('stream','select-dataset')],error=b'unexpected EOF',group='transport')
    c['fixture']['routes'][0]['framing']='length';c['fixture']['routes'][0]['script']=[{'send':3},{'abort':'fin'}]
    for signal in ('SIGINT','SIGTERM'):
        c=add('signal-'+signal,['promql','labels'],PREFIX+'labels',b'null',[('stream','select-dataset')],group='process',
              actions=[{'when':{'fixture':'barrier:never'},'do':{'signal':signal}}])
        c['fixture']['routes'][0]['header_barrier']='never';c['expect']['termination']={'signal':2 if signal=='SIGINT' else 15};c['expect']['stdout_exact']=p3.pin(b'')
    c=add('devfull-json',['promql','labels','-o','json'],PREFIX+'labels',b'null',[('stream','select-dataset')],error=b'no space left on device',group='process',io={'stdout':'devfull'})
    c=add('devfull-text',['promql','labels'],PREFIX+'labels',b'{"data":["x"]}',[('stream','select-dataset')],group='process',io={'stdout':'devfull'})
    c=add('spinner',['promql','labels','-o','json'],PREFIX+'labels',b'null',[('stream','select-dataset')],b'null\n',group='process',io={'stderr':'pty'})
    c['fixture']['routes'][0]['delay_ms']=450
    c['expect']['min_spinner_frames']=2
    c['expect']['stderr_regex']=p3.pin(rb'(?:\r[|/\\-] fetching\.\.\.)+\r\x1b\[K')
    for fmt in ('text','json'):
        c=add('broken-pipe-'+fmt,['promql','labels','-o',fmt],PREFIX+'labels',b'{"data":["x"]}',[('stream','select-dataset')],group='process',
              actions=[{'when':{'start':True},'do':{'close':'stdout'}}])
        c['fixture']['routes'][0]['delay_ms']=200;c['expect']['termination']={'signal':13}
        c=add('saved-devfull-'+fmt,['sql','list','-o',fmt],'/api/v1/filters',b'[{"query":{"filter_query":"x"}}]',group='process',io={'stdout':'devfull'},
              error=b'failed to encode JSON output' if fmt=='json' else None)
    c=add('save-devfull',['sql','save','select 1','-f',START,'-t',END],group='process',io={'stdout':'devfull'},
          routes=[p3.route(b'',path='/api/v1/filters')])
    c['expect']['requests']=['POST /api/v1/filters']
    for fmt in ('text','json'):
        c=add('complete-body-before-output-'+fmt,['promql','labels','-o',fmt],PREFIX+'labels',b'{"data":["x"]}',[('stream','select-dataset')],
              b' \xe2\x80\xa2 x\n' if fmt=='text' else b'{\n  "data": [\n    "x"\n  ]\n}\n',group='transport')
        c['fixture']['routes'][0].update(framing='chunked',script=[{'send':'rest'},{'delay_ms':300}])
        c['expect']['min_wall_ms']=250
    for suffix, target in [('?base=1',PREFIX+'labels?base=1?stream=select-dataset'),('#fragment',PREFIX+'labels')]:
        c=add('base-suffix-'+('query' if suffix.startswith('?') else 'fragment'),['promql','labels','-o','json'],PREFIX+'labels',b'null',output=b'null\n',group='transport')
        c['config']={'toml':phase2.config_toml({'fixture':phase2.profile(url='{{url}}'+suffix)})};c['targets']=[target]
    c=add('proxy-ignored',['promql','labels','-o','json'],PREFIX+'labels',b'null',[('stream','select-dataset')],b'null\n',group='transport')
    c['config']={'toml':phase2.config_toml({'fixture':phase2.profile(url='{{url}}'.replace('127.0.0.1','localhost'))})}
    c['env']={'HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1','NO_PROXY':''}
    old={'cookie':'session=fixture-session','x-p-tenant':'fixture-tenant'}
    new={'cookie':'session=rotated-session','x-p-tenant':'fixture-tenant'}
    token='/orch/api/v1/cli/oauth/token'
    for name,args,path,response in [('metrics',['promql','labels','-o','json'],PREFIX+'labels',b'null'),
                                    ('saved',['sql','list','-o','json'],'/api/v1/filters',b'[]')]:
        c=add('refresh-'+name,args,output=response+b'\n',group='refresh',routes=[
            p3.route(b'expired',401,method='GET',path=path,headers=old,times=1),
            p3.route(b'{"access_token":"rotated-session","refresh_token":"rotated-refresh"}',path=token,
                     headers={'authorization':'Bearer '+p3.ORCHESTRATOR_TOKEN,'content-type':'application/json','accept':'application/json'}),
            p3.route(response,method='GET',path=path,headers=new)])
        c['fixture']['headers']={};c['build']='cloud'
        c['config']={'toml':phase2.config_toml({'fixture':{'url':'{{url}}','cloud':True,'session_token':'fixture-session','refresh_token':'fixture-refresh','tenant_id':'fixture-tenant','orchestrator_url':'{{url}}/orch'}})}
        c['expect']['requests']=['GET '+path,'POST '+token,'GET '+path]
        c['expect']['config']={'profiles':{'fixture':{'session_token':'rotated-session','refresh_token':'rotated-refresh'}}}
    # encoding/json folds Unicode long-s with ASCII S for tagged struct fields.
    for fmt in ('text','json'):
        add('unicode-envelope-'+fmt,['promql','labels','-o',fmt],PREFIX+'labels',
            '{"ſtatus":"error","errorType":"unicode","error":"folded"}'.encode(),
            [('stream','select-dataset')],error=b'PromQL error (unicode): folded')
    # Go's []RuleSet and []Rule destinations identify the containing struct in type errors.
    for level in ('set','rule'):
        for value in (1, 'bad', True, []):
            rules=[value] if level=='set' else [{'rules':[value]}]
            body=json.dumps([{'query':{'filter_builder':{'rules':rules}}}]).encode()
            typename=b'model.RuleSet' if level=='set' else b'model.Rule'
            c=add('builder-'+level+'-'+type(value).__name__,['sql','list','-o','json'],
                  '/api/v1/filters',body,error=b'failed to decode saved-query response',group='saved')
            c['expect']['stderr_has'].append(p3.pin(typename))
    for field,value in [('year','+024-01-01T00:00:00Z'),('month','2024-+1-01T00:00:00Z'),('day','2024-01-+1T00:00:00Z')]:
        for flag in ('from','to'):
            args=['promql','run','up','--from',START,'--to',END,'--'+flag,value]
            add('signed-'+field+'-'+flag,args,error=('invalid --'+flag).encode(),group='validation')
    for name,flags,error in [('output',['-o','xml','--to','bad'],b'output format'),
                             ('to',['--to','bad','--from','bad'],b'invalid --to'),
                             ('from',['--to',END,'--from','bad'],b'invalid --from')]:
        for instant_flag in ([],['--instant']):
            add('interactive-validation-'+name+('-instant' if instant_flag else ''),
                ['promql','run','up','-i',*instant_flag,*flags],error=error,group='validation')
    # Typed text decoders reject mismatched scalars even when raw JSON mode accepts them.
    typed=[('labels',['labels'],'labels',b'{"data":[1]}',b'labels'),
           ('label-values',['label-values','job'],'label/job/values',b'{"data":[{}]}',b'label-values'),
           ('series',['series','-m','up'],'series',b'{"data":[{"x":2}]}',b'series'),
           ('cardinality',['cardinality','label-names'],'cardinality/label_names',b'{"data":[{"value":1.25}]}',b'cardinality'),
           ('active-series',['cardinality','active-series'],'cardinality/active_series',b'{"data":{"series":[{"x":false}]}}',b'active-series'),
           ('active-queries',['ps'],'status/active_queries',b'{"data":[{"elapsed_ms":1.5}]}',b'active-queries'),
           ('tsdb',['tsdb'],'status/tsdb',b'{"data":{"totalSeries":"bad"}}',b'TSDB data'),
           ('run',['run','up','--instant','--to',END],'query',b'{"data":{"result":[{"metric":{"x":1}}]}}',b'response')]
    for name,args,endpoint,body,kind in typed:
        params=[] if name=='active-queries' else [('stream','select-dataset')]
        if name=='series':params.append(('match[]','up'))
        if name in ('cardinality','active-series'):params += [('limit','20'),('lookback','3600')]
        if name=='tsdb':params.append(('topN','10'))
        if name=='run':params += [('query','up'),('time',END)]
        add('typed-malformed-'+name,['promql',*args],PREFIX+endpoint,body,params,error=b'failed to decode PromQL '+kind)
        add('raw-malformed-'+name,['promql',*args,'-o','json'],PREFIX+endpoint,body,params,
            output=json.dumps(json.loads(body),indent=2,sort_keys=True).encode()+b'\n')
    for name,body,error,output in [
        ('malformed-status',b'{"status":"error","error":7,"data":[]}',b'failed to decode PromQL labels response',None),
        ('duplicate-case',b'{"STATUS":"error","status":"success","data":[]}',None,b'No labels found.\n'),
        ('null-status',b'{"status":"error","status":null,"error":"null keeps prior string"}',b'PromQL error: null keeps prior string',None),
        ('null-labels',b'{"data":[null,"x"]}',None,b' \xe2\x80\xa2 \n \xe2\x80\xa2 x\n'),
        ('deep-unknown',json.dumps({'data':[], 'unknown':[[[[{'ignored':False}]]]]}).encode(),None,b'No labels found.\n')]:
        add('typed-'+name,['promql','labels'],PREFIX+'labels',body,[('stream','select-dataset')],output=output,error=error)
    for status in (400,401,403,404,429,500,503):
        add('http-error-precedence-'+str(status),['promql','labels'],PREFIX+'labels',
            b'{"status":"error","error":"envelope must not win"}',[('stream','select-dataset')],error=b'PromQL request failed',status=status)
    # Integer division admits 100 whole intervals plus a fractional remainder.
    for name,start,end,step,resolved in [
        ('auto-edge',START,'2024-01-01T00:25:14Z','auto','15s'),
        ('auto-next',START,'2024-01-01T00:25:15Z','auto','30s'),
        ('equal',END,END,'auto','1m'),('reversed',END,START,'auto','1m'),
        ('manual-reversed',END,START,'1w2d3h','1w2d3h'),
        ('zero-token',START,END,'0h1m','15s'),
        ('overflow-token',START,END,'9223372036854775808s','15s'),
        ('case-auto',START,END,' AuTo ','15s')]:
        add('step-'+name,['promql','run','up','-f',start,'-t',end,'--step',step,'-o','json'],
            PREFIX+'query_range',b'null',[('query','up'),('stream','select-dataset'),('start',start),('end',end),('step',resolved)],b'null\n')
    for name,stamp,normalized in [
        ('fraction', '2024-01-01T00:00:00.123456789123456789Z', '2024-01-01T00:00:00Z'),
        ('comma-fraction','2024-01-01T00:00:00,123Z','2024-01-01T00:00:00Z'),
        ('single-hour','2024-01-01T0:00:00Z','2024-01-01T00:00:00Z'),
        ('offset-24','2024-01-02T00:00:00+24:00','2024-01-01T00:00:00Z')]:
        add('time-quirk-'+name,['promql','run','up','--instant','-t',stamp,'-o','json'],PREFIX+'query',b'null',
            [('query','up'),('stream','select-dataset'),('time',normalized)],b'null\n')
    for stage in ('header','body'):
        c=add('deadline-'+stage,['promql','labels'],PREFIX+'labels',b'null',[('stream','select-dataset')],error=b'Client.Timeout',group='deadline',timeout=135)
        c['tier']='full120s';c['expect'].update(min_wall_ms=119000,max_wall_ms=130000);c['compare']['timeout_cause']=True
        if stage=='header':c['fixture']['routes'][0]['header_stall']=True
        else:c['fixture']['routes'][0].update(framing='chunked',script=[{'send':'rest'},{'stall':True}])
    return cases

def expectation_issues(case,result):
    check=copy.deepcopy(case)
    if check['expect']['requests'] is None:
        check['expect']['requests']=[r['method']+' '+urllib.parse.unquote(urllib.parse.urlsplit(r['path']).path) for r in result['trace']]
    issues=p3.expectation_issues(check,result)
    if case.get('targets') and [r['path'] for r in result['trace']]!=case['targets']: issues.append('raw request targets differ from independent expectation')
    if not isinstance(case['expect'].get('config'),dict) and result['config_before']!=result['config_after']: issues.append('configuration mutated')
    if case.get('save_body'):
        try:
            actual=json.loads(base64.b64decode(result['trace'][-1]['body_b64']))
            for key,value in case['save_body'].items():
                if actual.get(key)!=value: issues.append('save body '+key+' differs: '+repr(actual.get(key)))
            if actual.get('time_filter')!={'from':START,'to':END}: issues.append('saved absolute times differ')
        except (ValueError,IndexError): issues.append('missing valid save body')
    return issues

def signature(result,case=None):
    return {'stdout':result['stdout_normalized_sha256'],'stderr':run.digest(p3.compare_stderr(case or {},result['stderr_normalized'])),
            'termination':result['termination'],'requests':result['requests'],'config':result['config_after'],
            'stderr_raw':run.digest(result['stderr']),'trace':run.digest(run.canonical(result['trace']))}

def validate_capture(report, digest, cases):
    """A complete default capture explicitly covers gate cases and excludes long deadlines."""
    tiers=report.get('tiers', [])
    expected={c['name'] for c in cases if c['tier'] in tiers}
    actual=[c['name'] for c in report.get('cases', [])]
    if (report.get('mode')!='check-reference' or report.get('corpus_sha256')!=digest
            or report.get('failed') or report.get('partial') or 'gate' not in tiers
            or set(actual)!=expected or len(actual)!=len(expected)):
        raise ValueError('capture is not a complete passing current Go capture')


def frozen_identity_issues(manifest, identities, cloud=True):
    issues=[]
    for role in ('reference','cloud_reference') if cloud else ('reference',):
        current=identities.get(role); frozen=manifest['binaries'].get(role)
        if not current or not frozen or current['sha256']!=frozen['sha256']:
            issues.append(role+' binary differs from the frozen Go oracle')
    return issues


def frozen_signature_issues(actual, frozen):
    issues=['reference '+key+' differs from frozen Go oracle'
            for key in ('stdout','stderr','termination','requests') if actual[key]!=frozen[key]]
    actual_config, frozen_config=actual['config'], frozen['config']
    if isinstance(actual_config,dict) and isinstance(frozen_config,dict):
        # A fresh fixture uses a different port. Parsed TOML already normalizes
        # that URL; retain modes and digests for files without valid TOML.
        actual_config, frozen_config=phase2.semantic(actual_config), phase2.semantic(frozen_config)
    if actual_config!=frozen_config:issues.append('reference config differs from frozen Go oracle')
    return issues


def baseline_signature_issues(actual, saved):
    return [] if actual==saved else ['baseline artifacts differ from frozen signature']

def deferred_boundaries():
    return [dict(name='p4-boundary-'+name,argv=argv,structured=structured,config={'auth':'basic'},location='xdg',build='standard',requires=[],env={},unset=[],
                 fixture={'headers':{},'routes':[]},expect={'termination':{'exit':1},'requests':[]})
            for name,argv,structured in [('interactive',['promql','run','up','-i'],False),('interactive-json',['promql','run','up','-i','-o','json'],True),
            ('picker',['sql','list'],False),('picker-empty',['sql','list','-o',''],False)]]

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('list')
    freeze=sub.add_parser('freeze');freeze.add_argument('source');freeze.add_argument('--out',default=str(MANIFEST))
    for name in ('check-reference','compare'):
        cmd=sub.add_parser(name);cmd.add_argument('--reference');cmd.add_argument('--out',required=True);cmd.add_argument('--case',action='append');cmd.add_argument('--timeout',type=float,default=10);cmd.add_argument('--include-full120s',action='store_true')
        cmd.add_argument('--cloud-reference')
        if name=='compare':
            cmd.add_argument('--candidate',required=True);cmd.add_argument('--baseline');cmd.add_argument('--self-check',action='store_true')
            cmd.add_argument('--cloud-candidate')
            cmd.add_argument('--manifest',default=str(MANIFEST),help='frozen Go expectations; none only for explicit characterization')
    args=parser.parse_args(argv);cases=corpus();digest=run.digest(run.canonical(cases))
    if args.command=='list':
        print('\n'.join(c['name'] for c in cases));return 0
    if args.command=='freeze':
        report=json.loads((Path(args.source)/'report.json').read_text())
        validate_capture(report,digest,cases)
        Path(args.out).write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');return 0
    selected=[c for c in cases if (c['tier']=='gate' or args.include_full120s) and (not args.case or c['name'] in args.case)]
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    baseline=json.loads((Path(args.baseline)/'report.json').read_text()) if getattr(args,'baseline',None) else None
    if baseline: validate_capture(baseline,digest,cases)
    manifest = None
    if args.command == 'compare' and args.manifest != 'none':
        manifest = json.loads(Path(args.manifest).read_text())
        validate_capture(manifest,digest,cases)
        identities=baseline['binaries'] if baseline else {
            role:phase1.binary_identity(getattr(args,role))
            for role in ('reference','cloud_reference') if getattr(args,role)}
        identity_issues=frozen_identity_issues(manifest,identities,any(c['build']=='cloud' for c in selected))
        if identity_issues: raise ValueError('; '.join(identity_issues))
    report={'mode':args.command,'corpus_sha256':digest,'partial':bool(args.case),'tiers':['gate','full120s'] if args.include_full120s else ['gate'],'cases':[],'failed':0,'binaries':{},'runner_sha256':run.digest(Path(__file__).read_bytes())}
    report['excluded_cases']=[{'name':c['name'],'reason':'full120s requires --include-full120s' if c['tier']=='full120s' and not args.include_full120s else 'case selection'} for c in cases if c not in selected]
    for role in ('reference','candidate','cloud_reference','cloud_candidate'):
        binary=getattr(args,role,None)
        if binary: report['binaries'][role]=phase1.binary_identity(binary)
    if args.command=='compare' and not args.self_check and args.reference and Path(args.reference).resolve()==Path(args.candidate).resolve():raise ValueError('same binary requires --self-check')
    for case in selected:
        folder=out/case['name'];issues=[]
        reference = args.cloud_reference if case['build'] == 'cloud' else args.reference
        candidate = getattr(args, 'cloud_candidate' if case['build'] == 'cloud' else 'candidate', None)
        if not baseline and not reference:
            raise ValueError('missing ' + case['build'] + ' reference binary')
        if args.command == 'compare' and not candidate:
            raise ValueError('missing ' + case['build'] + ' candidate binary')
        if baseline:
            ref=p3.load_stored(Path(args.baseline)/case['name']/'reference',case)
            saved=next(c for c in baseline['cases'] if c['name']==case['name'])
            issues+=baseline_signature_issues(signature(ref,case),saved['signature'])
        else:
            ref=p3.sample(Path(reference).resolve(),case,folder/'reference',args.timeout)
            issues+=expectation_issues(case,ref)
            again=p3.sample(Path(reference).resolve(),case,folder/'repeat',args.timeout)
            issues+=p3.differences(case,ref,again)
        if manifest and case['tier'] == 'gate':
            frozen = next((c for c in manifest['cases'] if c['name'] == case['name']), None)
            if frozen is None:
                issues.append('case missing from frozen Go oracle')
            else:
                actual_signature = signature(ref, case)
                issues+=frozen_signature_issues(actual_signature,frozen['signature'])
        if args.command=='compare':
            actual=p3.sample(Path(candidate).resolve(),case,folder/'candidate',args.timeout)
            issues+=expectation_issues(case,actual)+p3.differences(case,ref,actual)
        item={'name':case['name'],'signature':signature(ref,case),'issues':issues};report['cases'].append(item)
        if issues:report['failed']+=1;print(case['name']+': '+ '; '.join(issues),file=sys.stderr)
    if args.command=='compare' and not args.self_check and not args.case:
        report['boundaries']=[]
        for check in deferred_boundaries():
            result=p3.sample(Path(args.candidate).resolve(),check,out/check['name'],args.timeout)
            issues=p3.boundary_issues(check,result);report['boundaries'].append({'name':check['name'],'issues':issues});report['failed']+=bool(issues)
    for role,identity in report['binaries'].items():
        if phase1.binary_identity(identity['path'])['sha256']!=identity['sha256']:report['failed']+=1;report.setdefault('identity_issues',[]).append(role+' binary changed')
    run.write_json(out/'report.json',report)
    case_failures = sum(bool(c['issues']) for c in report['cases'])
    print(f"phase4: {len(selected)-case_failures}/{len(selected)} cases passed; {report['failed']} total failures including boundaries/identity; {out/'report.json'}")
    return bool(report['failed'])

if __name__=='__main__':sys.exit(main())
