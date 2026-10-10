"""Finite read-only activation samples; local DB/API only, no scheduler."""
from pathlib import Path
import sys,json,time,re,socket,urllib.request,hashlib
from datetime import datetime,timezone
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from engine.config import settings

OUT=ROOT/'docs/p39-validation/priority-capacity-implementation-20261009/activation'
phase=sys.argv[1] if len(sys.argv)>1 else 'check'
assert re.fullmatch('[a-z0-9-]+',phase)
before=json.loads((OUT/'preflight.json').read_text('utf8'))
registered=json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))
run=next(r['run_id'] for r in registered if r['name']=='worker-jobs')
start=int(datetime.strptime(run.split('-')[0],'%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).timestamp()*1000)
at=int(time.time()*1000)
e={'phase':phase,'captured_at_beijing':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
    'run_id':run,'as_of':at,'run_start_ms':start,'age_seconds':round((at-start)/1000),
    'read_only':True,'exchange_calls':False,'registered_processes':registered,'ports':{},'http':{},'queries':{},'logs':{}}
pg=Path(before['postgres_log'])
with pg.open('rb') as f:
    f.seek(before['postgres_log_bytes']);new=f.read().decode('utf8',errors='replace')
(OUT/(phase+'-postgres-increment.log')).write_text(new,'utf8')
e['postgres_new_deadlocks']=new.count('ERROR:  deadlock detected')
e['postgres_error_heads']=[line for line in new.splitlines() if 'ERROR:' in line][-20:]
e['postgres_log_bytes']=pg.stat().st_size
for port in (18770,18771,18772,55432):
    with socket.socket() as s:
        s.settimeout(1);e['ports'][str(port)]=s.connect_ex(('127.0.0.1',port))==0

db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',
    connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
assert db.url.host in ('127.0.0.1','localhost') and db.url.port==55432 and db.url.database=='erji'
queries={
 'revision':'select version_num from alembic_version',
 'indexes':"select p.tablename,p.indexname,i.indisvalid from pg_indexes p join pg_class c on c.relname=p.indexname join pg_index i on i.indexrelid=c.oid where p.schemaname='public' and (p.tablename in ('analysis_demands','analysis_results','market_analysis_states') or p.indexname='ix_signal_snapshots_plan_latest')",
 'worker_heartbeats':"select role,state,updated_at,details from worker_heartbeats where details->>'run_id'=:run order by role",
 'tasks':"select kind,state,count(*) tasks,sum(coalesce((payload->>'completed_count')::bigint,0)) completed,sum(coalesce((payload->>'failed_count')::bigint,0)) failed from tasks where kind like 'p39_%' group by 1,2",
 'results_this_run':"select work_kind,payload->>'status' status,count(*) results,count(distinct instrument_id) markets,max((payload->>'finished_at')::bigint) latest_finished from analysis_results where (payload->>'started_at')::bigint>=:start group by 1,2",
 'failed_this_run':"select kind,payload->>'subject' subject,payload->>'error' error,(payload->>'finished_at')::bigint finished from tasks where kind like 'p39_%' and state='failed' and (payload->>'started_at')::bigint>=:start order by (payload->>'finished_at')::bigint desc limit 12",
 'oi_committed_this_run':"select count(*) points,count(distinct instrument_id) native_markets,max(received_at) latest_received from oi_points where received_at>=:start and event_time>=:start",
 'oi_and_volume_coverage':"select i.venue,count(*) markets,count(*) filter(where :at-m.as_of between 0 and 90000) quote_90s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 90000) oi_90s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 600000) oi_10m,count(*) filter(where :at-(m.payload->>'volume_24h_source_at')::bigint between 0 and 600000) volume_10m from instruments i join market_states m on m.instrument_id=i.id where i.venue in ('binance','binance_spot') group by 1",
 'due_checkpoints':"select key,updated_at,payload from user_preferences where key='due:binance:oi'",
 'demand_recovery':"select key,updated_at,payload from user_preferences where key='p39_demand_sync'",
 'blocking':"select pid,state,wait_event,extract(epoch from(clock_timestamp()-xact_start))::int transaction_seconds,pg_blocking_pids(pid) blockers,left(query,200) query from pg_stat_activity where datname=current_database() and pid<>pg_backend_pid() and cardinality(pg_blocking_pids(pid))>0 order by xact_start limit 12",
 'long_transactions':"select pid,state,wait_event,extract(epoch from(clock_timestamp()-xact_start))::int transaction_seconds,left(query,200) query from pg_stat_activity where datname=current_database() and pid<>pg_backend_pid() and clock_timestamp()-xact_start>interval '8 seconds' order by xact_start limit 12",
 'projection':"select count(*) rows,max(computed_at) latest_computed,count(*) filter(where action in ('participate','trial') and valid_until>:at) unexpired_buy from current_signals",
 'volume_batches':"select source,count(*) batches,max(received_at) latest_received from raw_events where received_at>=:start and endpoint like '%ticker/24hr%' group by source",
 'btc_5m_continuity':"with bars as(select open_time,lag(open_time) over(order by open_time) prev from candles where instrument_id='binance:perp:BTCUSDT' and resolution='5' and is_closed and open_time>=:at-3600000 and close_time<=:at) select count(*) bars,count(*) filter(where open_time-prev<>300000) gaps,max(open_time) latest_open from bars",
}
with db.connect() as c:
    for key,q in queries.items():
        try:e['queries'][key]=[dict(r) for r in c.execute(text(q),{'at':at,'start':start,'run':run}).mappings()]
        except Exception as error:e['queries'][key]={'error':type(error).__name__}
    try:
        from engine.p39.priority import market_tiers
        with Session(bind=c) as session:
            t2,t3,positions=market_tiers(session,at)
        e['native_tiers']={'t2':sorted(t2),'t3':sorted(t3),'positions':sorted(positions),'t2_count':len(t2),'t3_count':len(t3)}
    except Exception as error:e['native_tiers']={'error':type(error).__name__}
db.dispose()
for r in registered:
    path=Path(r['log_dir'])/(r['name']+'.err.log')
    if not path.exists():continue
    content=path.read_text('utf8',errors='replace')
    tokens={'tracebacks':'Traceback','deadlocks':'DeadlockDetected','sqlstate_deadlock':'sqlstate=40P01',
        'long_connections':'database connection held','duplicate_key':'duplicate key value',
        'connection_closed':'ConnectionClosedError','timeouts':'TimeoutError'}
    e['logs'][r['name']]={'bytes':path.stat().st_size,**{name:content.count(token) for name,token in tokens.items()},
        'rate_429':len(re.findall(r'(?:HTTP(?:/\d(?:\.\d)?)?\s+|status(?:_code)?[\s=:]+)429\b|\b429\s+Too Many Requests',content,re.I)),
        'ban_418':len(re.findall(r'(?:HTTP(?:/\d(?:\.\d)?)?\s+|status(?:_code)?[\s=:]+)418\b|\b418\s+I.m a teapot',content,re.I))}
    startup_lines=[line.split('startup ',1)[1] for line in content.splitlines() if 'startup {' in line]
    if startup_lines:
        try:e['logs'][r['name']]['startup']=json.loads(startup_lines[0])
        except Exception:pass
    (OUT/(phase+'-'+r['name']+'-tail.log')).write_text(content[-12000:],'utf8')

urls={'budget':'http://127.0.0.1:18772/health','health':'http://127.0.0.1:18771/v1/health',
    'observations':'http://127.0.0.1:18771/v1/anomaly-observations?limit=5',
    'buy':'http://127.0.0.1:18771/v1/signals?view=opportunities&page_size=5',
    'web_observations':'http://127.0.0.1:18770/observations',
    'web_buy':'http://127.0.0.1:18770/signals',
    'preview':'http://127.0.0.1:18770/previews/P39_priority_capacity_acceptance.html'}
for name,url in urls.items():
    try:
        t=time.monotonic()
        with urllib.request.urlopen(url,timeout=10) as r:body=r.read(1500000);status=r.status
        e['http'][name]={'status':status,'elapsed_ms':round((time.monotonic()-t)*1000,2)}
        if name in ('budget','health','observations','buy'):
            data=json.loads(body)
            (OUT/(phase+'-'+name+'-api.json')).write_text(json.dumps(data,ensure_ascii=False,default=str),'utf8')
            e['http'][name].update(as_of=data.get('as_of'),rows=len(data.get('rows',[])),total=data.get('total'))
    except Exception as error:e['http'][name]={'error':type(error).__name__}

(OUT/(phase+'.json')).write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str),'utf8')
q=e['queries'];beats=q.get('worker_heartbeats',[])
summary={k:e[k] for k in ('phase','captured_at_beijing','run_id','age_seconds','ports','http','postgres_new_deadlocks','native_tiers')}
summary['native_tiers']={k:v for k,v in e['native_tiers'].items() if k.endswith('count') or k=='error'}
summary['queries']={k:v for k,v in q.items() if k not in ('due_checkpoints','worker_heartbeats','tasks','indexes')}
summary['logs']= {role:{k:v for k,v in data.items() if k!='startup'} for role,data in e['logs'].items()}
summary['important_beats']=[{'role':b['role'],'state':b['state'],'age_ms':at-b['updated_at'],
    'details':{k:v for k,v in b['details'].items() if k not in ('pending_pool','source_hashes')}}
    for b in beats if b['role'] in ('analysis_owner','live_binance','anomaly_discovery','observation_analysis')]
due=q.get('due_checkpoints',[])
if isinstance(due,list) and due:
    p=due[0]['payload'];intervals=p.get('intervals',{});hot=set(e.get('native_tiers',{}).get('t2',[]))|set(e.get('native_tiers',{}).get('t3',[]))
    natives={iid.rsplit(':',1)[-1] for iid in hot if iid.startswith('binance:')}
    wrong=[n for n,interval in intervals.items() if interval!=(60000 if n in natives else 300000)]
    summary['oi_schedule']={'ordinary':sum(v==300000 for v in intervals.values()),'hot':sum(v==60000 for v in intervals.values()),
        'fair_turn':p.get('fair_turn'),'pending_claims':len(p.get('inflight',{})),'mismatched_native_intervals':wrong[:20]}
(OUT/(phase+'-summary.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),'utf8')
print(json.dumps(summary,ensure_ascii=False,default=str))
