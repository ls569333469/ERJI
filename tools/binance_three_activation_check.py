"""One-shot activation verification. No exchange requests or monitor changes."""
from pathlib import Path
import sys,json,time,socket,urllib.request,hashlib,re
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import text
from engine.db import Session
out=ROOT/'docs/p39-validation/binance-three-optimizations'
records=json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))
run=next(p['run_id'] for p in records if p['name']=='worker-jobs')
start=int(datetime.strptime(run.split('-')[0],'%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).timestamp()*1000)
sample={'checked_at_bjt':datetime.now(timezone(timedelta(hours=8))).isoformat(),'run_id':run,
    'read_only':True,'monitor_resumed':False,'ports':{},'http':{},'queries':{},'logs':{}}
manifest=json.loads((out/'code-manifest.json').read_text('utf8'))
sample['source_manifest_matches']={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in manifest.items()}
sample['startup']=json.loads((ROOT/'.runtime/runs'/run/'startup.json').read_text('utf-8-sig'))
for port in (18770,18771,18772,55432):
    with socket.socket() as sock:
        sock.settimeout(1);sample['ports'][str(port)]=sock.connect_ex(('127.0.0.1',port))==0
urls={'health':'http://127.0.0.1:18771/v1/health','page':'http://127.0.0.1:18770/signals',
    'budget':'http://127.0.0.1:18772/health','buy':'http://127.0.0.1:18771/v1/signals?view=buy&size=5',
    'SUPER_structure':'http://127.0.0.1:18771/v1/analysis?instrument=binance:perp:SUPERUSDT'}
for key,url in urls.items():
    try:
        t=time.monotonic()
        with urllib.request.urlopen(url,timeout=12) as response:
            item={'status':response.status,'ms':round((time.monotonic()-t)*1000,2)}
            if key!='page':
                body=json.loads(response.read())
                if key=='health':item['workers']=body.get('workers',[])
                elif key=='SUPER_structure':
                    item['structure']=[{k:a.get(k) for k in ('timeframe','structural_version','structural_status','coverage')} for a in body['timeframes']]
                    item['targets']={k:v for k,v in (body.get('opportunity') or {}).items() if k in ('t1','daily','weekly')}
                else:item['summary']={k:body[k] for k in ('status','total','as_of','groups') if k in body}
            sample['http'][key]=item
    except Exception as error:sample['http'][key]={'error':type(error).__name__}
queries={
 'heartbeats':("SELECT role,state,updated_at,details FROM worker_heartbeats WHERE details->>'run_id'=:run AND role IN ('live','jobs','streams','analysis_owner','history_backfill','signal_projection','native_streams','binance:market_stream') ORDER BY role",{'run':run}),
 'daily_progress':("SELECT key,updated_at,payload FROM user_preferences WHERE key LIKE 'p34_history:1D:%' AND updated_at>=:start ORDER BY updated_at DESC LIMIT 5",{'start':start}),
 'new_candidate_versions':("SELECT evidence->>'structural_version' AS version,count(*) AS rows FROM episodes WHERE (evidence->>'evaluated_at')::bigint>=:start AND evidence->>'structural_version' IS NOT NULL GROUP BY 1",{'start':start}),
 'task_completions':("SELECT kind,state,count(*) AS rows FROM tasks WHERE kind LIKE 'p39_%' AND (payload->>'finished_at')::bigint>=:start GROUP BY kind,state ORDER BY kind,state",{'start':start}),
 'SUPER_coverage':("SELECT resolution,count(*) AS closed_bars,min(open_time) AS first_open,max(close_time) AS last_close FROM candles WHERE instrument_id='binance:perp:SUPERUSDT' AND is_closed GROUP BY resolution ORDER BY resolution",{})}
with Session() as s:
    s.execute(text('SET TRANSACTION READ ONLY'));s.execute(text("SET LOCAL statement_timeout='8s'"))
    for key,(sql,params) in queries.items():sample['queries'][key]=[dict(r) for r in s.execute(text(sql),params).mappings()]
for p in records:
    if p.get('run_id')!=run:continue
    path=Path(p['log_dir'])/(p['name']+'.err.log')
    if not path.exists():continue
    lines=path.read_text('utf8',errors='replace').splitlines()
    relevant=[line for line in lines if re.search(r'Traceback|\bERROR\b|\w+Error:',line)]
    def redact(line):
        line=re.sub(r'(?i)(api[_-]?key|token|secret|authorization)([=: ]+)[^\s&,]+',r'\1\2[redacted]',line)
        return line[:300]
    sample['logs'][p['name']]={'lines':len(lines),'error_lines':len(relevant),'latest_errors':[redact(line) for line in relevant[-3:]]}
sample['activation_confirmed']=sample['startup'].get('status')=='ready' and all(sample['ports'].values()) and all(sample['source_manifest_matches'].values()) and sample['http'].get('health',{}).get('status')==200 and any(a.get('structural_version')=='pd2-shared-structure-v2' for a in sample['http'].get('SUPER_structure',{}).get('structure',[]))
tag='final' if '--final' in sys.argv else 'initial'
(out/f'activation-{tag}.json').write_text(json.dumps(sample,ensure_ascii=False,indent=2,default=str),encoding='utf8')
print(json.dumps({'run_id':run,'activation_confirmed':sample['activation_confirmed'],'ports':sample['ports'],
    'http':{k:{f:v for f,v in value.items() if f in ('status','error','ms')} for k,value in sample['http'].items()},
    'daily_progress':sample['queries']['daily_progress'],'new_candidate_versions':sample['queries']['new_candidate_versions'],
    'task_completions':sample['queries']['task_completions'],'logs':sample['logs']},ensure_ascii=False,default=str))
