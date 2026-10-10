"""Finite activation evidence. Read-only DB, no exchange calls or scheduler."""
from pathlib import Path
import sys,json,time,socket,urllib.request,re,hashlib
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine,text
from engine.config import settings
OUT=ROOT/'docs/p39-validation/deadlock-fix-20261008/activation';OUT.mkdir(parents=True,exist_ok=True)
phase=sys.argv[1] if len(sys.argv)>1 else 'check'
if not re.fullmatch(r'[a-z0-9-]+',phase):raise ValueError('invalid phase')
at=int(time.time()*1000);bjt=datetime.now(timezone(timedelta(hours=8))).isoformat()
records=json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))
run=next(r['run_id'] for r in records if r['name']=='worker-jobs')
start=int(datetime.strptime(run.split('-')[0],'%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).timestamp()*1000)
e={'phase':phase,'captured_at_bjt':bjt,'as_of':at,'run_id':run,'run_start_ms':start,'age_seconds':round((at-start)/1000),'read_only':True,'processes':records,'ports':{},'http':{},'queries':{},'logs':{}}
pg=Path('D:/Axiangmu/erji/.runtime/postgres.log')
before=json.loads((OUT/'preflight.json').read_text('utf8')) if (OUT/'preflight.json').exists() and phase!='preflight' else None
offset=before['postgres_log_bytes'] if before else pg.stat().st_size
e['postgres_log_bytes']=pg.stat().st_size
with pg.open('rb') as f:
    f.seek(offset);new=f.read().decode('utf8',errors='replace')
e['postgres_new_deadlock_errors']=new.count('ERROR:  deadlock detected')
e['postgres_deadlock_heads']=[line for line in new.splitlines() if 'ERROR:  deadlock detected' in line][-12:]
e['postgres_error_heads']=[line for line in new.splitlines() if 'ERROR:' in line][-30:]
if new:(OUT/(phase+'-postgres-increment.log')).write_text(new,'utf8')
for port in (18770,18771,18772,55432):
    with socket.socket() as s:s.settimeout(1);e['ports'][str(port)]=s.connect_ex(('127.0.0.1',port))==0
db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
assert db.url.host in ('localhost','127.0.0.1') and db.url.port==55432 and db.url.database=='erji'
queries={
 'revision':'select version_num from alembic_version',
 'deadlocks':"select deadlocks,stats_reset from pg_stat_database where datname=current_database()",
 'blocking':"select pid,application_name,wait_event,pg_blocking_pids(pid) blockers,extract(epoch from(clock_timestamp()-xact_start))::int transaction_seconds,left(query,300) query from pg_stat_activity where datname=current_database() and pid<>pg_backend_pid() and cardinality(pg_blocking_pids(pid))>0 order by xact_start limit 12",
 'indexes':"select p.tablename,p.indexname,i.indisvalid from pg_indexes p join pg_class c on c.relname=p.indexname join pg_index i on i.indexrelid=c.oid where p.schemaname='public' and (p.tablename in ('analysis_demands','analysis_results','market_analysis_states') or p.indexname='ix_signal_snapshots_plan_latest')",
 'tasks':"select kind,state,count(*) n,sum(coalesce((payload->>'completed_count')::bigint,0)) completed,sum(coalesce((payload->>'failed_count')::bigint,0)) failed,max((payload->>'finished_at')::bigint) latest_finish from tasks where kind like 'p39_%' group by 1,2 order by 1,2",
 'task_error_records':"select kind,state,payload->>'subject' subject,payload->>'error' error,payload->>'retired_reason' retired_reason,payload->>'started_at' started_at,payload->>'failed_count' failed_count,(payload->>'finished_at')::bigint finished_at from tasks where kind like 'p39_%' and (payload->>'finished_at')::bigint>=:start and payload->>'error' is not null order by (payload->>'finished_at')::bigint desc limit 15",
 'lock_holders':"select pid,state,wait_event_type,wait_event,extract(epoch from(clock_timestamp()-xact_start))::int transaction_seconds,left(query,900) query from pg_stat_activity where datname=current_database() and pid<>pg_backend_pid() and (pid in (select unnest(pg_blocking_pids(pid)) from pg_stat_activity where datname=current_database()) or clock_timestamp()-xact_start>interval '8 seconds') order by xact_start limit 16",
 'demands':"select origin_type,state,count(*) n,count(distinct instrument_id) markets from analysis_demands group by 1,2",
 'analysis_states':"select work_kind,analysis_status,count(*) n,max(last_attempt_at) latest_attempt,max(last_success_at) latest_success from market_analysis_states group by 1,2",
 'results_since_start':"select work_kind,payload->>'status' status,count(*) n,count(distinct instrument_id) markets,max((payload->>'finished_at')::bigint) latest_finish from analysis_results where (payload->>'started_at')::bigint>=:start group by 1,2",
 'projection':"select count(*) n,max(computed_at) latest_projection,count(*) filter(where action in ('trial','participate') and valid_until>:at) unexpired_buy from current_signals",
 'source':"select i.venue,count(*) markets,count(*) filter(where :at-m.as_of between 0 and 15000) quotes_15s,count(*) filter(where :at-m.as_of between 0 and 60000) quotes_60s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 90000) oi_90s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 360000) oi_6m from instruments i join market_states m on m.instrument_id=i.id where i.venue in ('binance','binance_spot') group by 1",
 'beats':"select role,state,updated_at,details from worker_heartbeats where details->>'run_id'=:run order by role",
 'candles':"select resolution,max(received_at) latest_received,max(close_time) latest_closed from candles where instrument_id='binance:perp:BTCUSDT' and is_closed and close_time<=:at group by 1",
 'btc_5m_continuity':"with c as (select open_time,lag(open_time) over(order by open_time) prev from candles where instrument_id='binance:perp:BTCUSDT' and resolution='5' and is_closed and open_time>=:at-3600000 and close_time<=:at) select count(*) closed_bars,min(open_time) first_open,max(open_time) last_open,count(*) filter(where open_time-prev<>300000) gaps from c",
 'events':"select count(*) filter(where created_at>=:start) current_run_events,max(id) latest_id,max(created_at) latest_at from stream_events",
}
with db.connect() as c:
    for key,q in queries.items():
        try:e['queries'][key]=[dict(r) for r in c.execute(text(q),{'at':at,'start':start,'run':run}).mappings()]
        except Exception as ex:e['queries'][key]={'error':type(ex).__name__}
db.dispose()
for p in records:
    f=Path(p['log_dir'])/(p['name']+'.err.log')
    if not f.exists():continue
    t=f.read_text('utf8',errors='replace')
    e['logs'][p['name']]={'bytes':f.stat().st_size,'deadlock':t.count('DeadlockDetected'),'traceback':t.count('Traceback'),'sqlstate_40P01':t.count('sqlstate=40P01'),'connection_closed':t.count('ConnectionClosedError'),'duplicate_key':t.count('same key'),'held_connection':t.count('database connection held'),'tail':t[-9000:] if phase!='preflight' else ''}
retained=list((ROOT/'.runtime/failed-facts').glob('**/*.json'))
e['retained_files']=[{'path':str(f),'bytes':f.stat().st_size} for f in retained]
paths=('engine/db.py','engine/repository.py','engine/transaction_events.py','engine/db_retry.py','engine/p39/pipeline.py','engine/p37/store.py','engine/p37/worker.py','engine/p37/detector.py','engine/p38/jobs.py','engine/pd2/write_queue.py','engine/pd2/native_streams.py','engine/pd2/stream_maintenance.py','engine/pd2/notifications.py','services/worker/main.py','apps/web/src/components/observation-board.tsx')
e['code_hashes']={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}
if phase!='preflight':
    urls=('http://127.0.0.1:18772/health','http://127.0.0.1:18771/v1/health','http://127.0.0.1:18771/v1/anomaly-observations?limit=5','http://127.0.0.1:18771/v1/signals?view=opportunities&page_size=5','http://127.0.0.1:18770/observations','http://127.0.0.1:18770/signals')
    for u in urls:
        try:
            t=time.monotonic()
            with urllib.request.urlopen(u,timeout=10) as r:
                body=r.read(1500000)
                e['http'][u]={'status':r.status,'ms':round((time.monotonic()-t)*1000)}
                if '/v1/' in u:
                    data=json.loads(body);(OUT/(phase+'-'+('observations' if 'anomaly' in u else 'signals' if 'signals?' in u else 'health')+'-api.json')).write_text(json.dumps(data,ensure_ascii=False,default=str),'utf8')
                    e['http'][u]['as_of']=data.get('as_of');e['http'][u]['rows']=len(data.get('rows',[]))
        except Exception as ex:e['http'][u]={'error':type(ex).__name__}
(OUT/(phase+'.json')).write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str),'utf8')
def totals(rows):
    result={}
    if not isinstance(rows,list):return result
    for r in rows:
        d=result.setdefault(r['kind'],{'completed':0,'failed':0})
        d['completed']+=int(r['completed'] or 0);d['failed']+=int(r['failed'] or 0)
    return result
base=totals(before['queries'].get('tasks')) if before else {}
total=totals(e['queries'].get('tasks'))
summary={k:e[k] for k in ('phase','captured_at_bjt','run_id','age_seconds','ports','http','postgres_new_deadlock_errors','retained_files')}
summary['queries']={k:e['queries'][k] for k in ('source','projection','blocking','lock_holders','results_since_start','task_error_records')}
summary['task_delta_since_preflight']={k:{f:v[f]-base.get(k,{}).get(f,0) for f in ('completed','failed')} for k,v in total.items()} if before else {}
summary['discovery']=[{'state':b['state'],'age_ms':at-b['updated_at'],'details':{k:v for k,v in b['details'].items() if k!='pending_pool'}} for b in e['queries'].get('beats',[]) if b['role']=='anomaly_discovery']
summary['log_errors']={k:{f:v for f,v in d.items() if f!='tail'} for k,d in e['logs'].items()}
print(json.dumps(summary,ensure_ascii=False,indent=2,default=str))
