"""Read-only one-shot evidence for signal-admission activation, no automation."""
import hashlib,json,re,socket,sys,time,urllib.request
from datetime import datetime,timezone,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine,text
from engine.config import settings

OUT=ROOT/'docs/p39-validation';at=int(time.time()*1000)
TZ=timezone(timedelta(hours=8))
processes=json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))
run=next(p['run_id'] for p in processes if p['name']=='worker-jobs')
start=int(datetime.strptime(run[:15],'%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).timestamp()*1000)
status=json.loads((OUT/'signal-admission-fix-status.json').read_text('utf8'))
sample={'at':at,'checked_at_bjt':datetime.now(TZ).isoformat(),'run_id':run,'run_start_ms':start,
        'read_only':True,'startup':json.loads((ROOT/'.runtime/runs'/run/'startup.json').read_text('utf-8-sig')),
        'ports':{},'http':{},'queries':{},'logs':[]}
sample['manifest_matches']={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in status['file_sha256'].items()}
auto=Path('C:/Users/ls569/.codex/automations/erji-p39/automation.toml')
sample['monitor_status']='PAUSED' if auto.exists() and re.search(r'^status\s*=\s*"PAUSED"',auto.read_text('utf8'),re.M) else 'unverified'
for port in (18770,18771,18772,55432):
 with socket.socket() as sock:
  sock.settimeout(1);sample['ports'][str(port)]=sock.connect_ex(('127.0.0.1',port))==0
for url in ('http://127.0.0.1:18771/v1/health','http://127.0.0.1:18771/v1/signals?view=buy&page_size=25',
            'http://127.0.0.1:18771/v1/signals?view=opportunities&page_size=25','http://127.0.0.1:18770/signals','http://127.0.0.1:18772/health'):
 try:
  began=time.monotonic()
  with urllib.request.urlopen(url,timeout=8) as r:
   item={'status':r.status,'ms':round((time.monotonic()-began)*1000)}
   if ':18771/' in url:
    body=json.loads(r.read())
    item['body']={k:body[k] for k in ('status','as_of','workers','rows','pagination','counts','groups','projection') if k in body}
   sample['http'][url]=item
 except Exception as exc:sample['http'][url]={'error':type(exc).__name__}
queries={
 'beats':"select role,state,updated_at,details from worker_heartbeats where details->>'run_id'=:run and (role in ('api','live','jobs','streams','analysis_owner','signal_projection','anomaly_discovery','observation_analysis') or role like 'stream_binance%')",
 'task_completion':"select kind,state,count(*) n,sum(coalesce((payload->>'completed_count')::int,0)) completed,max((payload->>'finished_at')::bigint) latest_finish,max(:at-available_at) filter(where state='queued') oldest_ready_wait_ms from tasks where kind like 'p39_%' group by 1,2 order by 1,2",
 'snapshots_new':"select p.execution_instrument_id instrument_id,s.plan_id,s.id,s.as_of,p.expires_at,s.payload->'action' action,s.payload->'execution' execution from signal_snapshots s join plans p on p.id=s.plan_id where s.as_of>=:start order by s.as_of desc limit 20",
 'actions_new':"select payload->'action'->>'code' action,payload->'execution'->>'cost_policy' cost_policy,payload->'execution'->>'status' execution_status,count(*) snapshots,count(distinct plan_id) plans from signal_snapshots where as_of>=:start group by 1,2,3",
 'buys_new':"select p.execution_instrument_id instrument_id,s.plan_id,s.id,s.as_of,p.expires_at,s.payload->'action' action,s.payload->'execution' execution from signal_snapshots s join plans p on p.id=s.plan_id where s.as_of>=:start and s.payload->'action'->>'code' in ('trial','participate') order by s.as_of desc limit 10",
 'projection':"select action,count(*) n,max(computed_at) latest_projection,count(*) filter(where valid_until>:at) unexpired from current_signals group by 1",
 'tracking_new':"select payload->>'trade_status' trade_status,payload->>'plan_tracking_status' plan_tracking_status,count(*) n from observations where (payload->>'tracked_at')::bigint>=:start group by 1,2",
 'trend_new':"select instrument_id,payload->>'analysis_status' analysis_status,payload->'missing_timeframes' missing,payload->'matrix'->'30' thirty from observations where (payload->>'analyzed_at')::bigint>=:start order by (payload->>'analyzed_at')::bigint desc limit 8",
 'active':"select payload from user_preferences where key='p37_active'",
}
db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
with db.connect() as c:
 for key,sql in queries.items():
  try:
   rows=[dict(r) for r in c.execute(text(sql),{'run':run,'at':at,'start':start}).mappings()]
   if key=='beats':
    for row in rows:row['details']={k:v for k,v in row['details'].items() if k in ('run_id','pid','source_hashes','producer','by_work_kind','execution_owner','tasks','queued','priority_overflow')}
   sample['queries'][key]=rows
  except Exception as exc:sample['queries'][key]={'error':type(exc).__name__}
db.dispose()
for p in processes:
 if p['run_id']!=run:continue
 log=Path(p['log_dir'])/(p['name']+'.err.log')
 content=log.read_text('utf8',errors='replace') if log.exists() else ''
 sample['logs'].append({'name':p['name'],'path':str(log),'bytes':len(content.encode()),
    'patterns':{k:len(re.findall(v,content,re.I)) for k,v in {'traceback':'Traceback','429':r'\b429\b','418':r'\b418\b','timeouts':'TimeoutError|timed out','held':'database connection held'}.items()},
    'startup_lines':[l for l in content.splitlines() if 'startup ' in l][:1],
    'error_lines':[l for l in content.splitlines() if re.search(r'Traceback|Error:|database connection held',l)][-12:]})
path=OUT/('signal-admission-activation-sample-'+datetime.now(TZ).strftime('%Y%m%d-%H%M%S')+'.json')
path.write_text(json.dumps(sample,ensure_ascii=False,indent=2,default=str),encoding='utf8')
print(json.dumps({'path':str(path),'run_id':run,'ports':sample['ports'],'manifest_all_match':all(sample['manifest_matches'].values()),
 'monitor':sample['monitor_status'],'http':{k:{'status':v.get('status'),'error':v.get('error'),'pagination':v.get('body',{}).get('pagination'),'rows':[{f:r.get(f) for f in ('symbol','instrument_id','action_code','price','valid_until','opportunity_level')} for r in v.get('body',{}).get('rows',[])]} for k,v in sample['http'].items()},
 'actions_new':sample['queries']['actions_new'],'buys_new':sample['queries']['buys_new'],'tracking_new':sample['queries']['tracking_new'],
 'trend_new':sample['queries']['trend_new'],'logs':[{k:v for k,v in l.items() if k!='startup_lines'} for l in sample['logs']]},ensure_ascii=False,default=str))
