"""Finite, local-only P40 activation evidence; no business mutations."""
import json, sys, time, re, socket, urllib.request, hashlib, os
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine, text
from engine.config import settings

OUT = Path(os.environ.get('ERJI_R3_EVIDENCE_DIR', str(ROOT / 'docs/p40-validation/r3-activation-20261010'))).resolve()
assert OUT.is_relative_to((ROOT / 'docs/p40-validation').resolve()), 'Evidence path must remain inside validation directory'
phase = sys.argv[1] if len(sys.argv) > 1 else 'initial'
assert re.fullmatch('[a-z0-9-]+', phase)
pre = json.loads((OUT / 'preflight.json').read_text('utf-8-sig'))
registry = json.loads((ROOT / '.runtime/processes.json').read_text('utf-8-sig'))
run = next(r['run_id'] for r in registry if r['name'] == 'worker-jobs')
start = int(datetime.strptime(run.split('-')[0], '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).timestamp()*1000)
at = int(time.time()*1000)
e = dict(phase=phase, run_id=run, as_of=at, run_start_ms=start,
         age_seconds=round((at-start)/1000), read_only=True, exchange_calls=False,
         captured_at_beijing=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
         registry=registry, ports={}, http={}, queries={}, logs={}, blind_spots=[])
manifest = json.loads((ROOT / 'docs/p40-validation/r3-implementation-20261009/implementation-result.json').read_text('utf8'))
e['source_hash_mismatches'] = [p for p,h in manifest['after_hashes'].items()
                              if hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
pg = Path(pre['postgres_log'])
with pg.open('rb') as f:
    f.seek(pre['postgres_log_bytes']); increment=f.read().decode('utf8',errors='replace')
if pre.get('postgres_filter_current_run'):
    # Log offsets may not survive a computer shutdown. Filter full multiline
    # records by their header time rather than importing old-run errors.
    records=re.split(r'(?=^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)',increment,flags=re.M)
    floor=datetime.fromtimestamp(start/1000,ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    increment=''.join(row for row in records if re.match(r'^\d{4}-\d{2}-\d{2}',row)
        and datetime.strptime(row[:23],'%Y-%m-%d %H:%M:%S.%f')>=floor)
(OUT/(phase+'-postgres-increment.log')).write_text(increment,'utf8')
e['postgres_new_deadlocks'] = increment.count('ERROR:  deadlock detected')
e['postgres_error_heads'] = [v for v in increment.splitlines() if 'ERROR:' in v][-15:]
for port in (18770,18771,18772,55432):
    with socket.socket() as s:
        s.settimeout(2); e['ports'][str(port)]=s.connect_ex(('127.0.0.1',port))==0
queries = {
 'revision': 'select version_num from alembic_version',
 'heartbeats': "select role,state,updated_at,details from worker_heartbeats where details->>'run_id'=:run order by role",
 'tasks': "select kind,state,count(*) tasks,sum(coalesce((payload->>'completed_count')::bigint,0)) completed,sum(coalesce((payload->>'failed_count')::bigint,0)) failed from tasks where kind like 'p39_%' group by 1,2",
 'results_this_run': "select work_kind,payload->>'status' status,count(*) results,count(distinct instrument_id) markets,max((payload->>'finished_at')::bigint) latest_finished from analysis_results where (payload->>'started_at')::bigint>=:start group by 1,2",
 'failed_this_run': "select kind,payload->>'subject' subject,payload->>'error' error,(payload->>'finished_at')::bigint finished from tasks where kind like 'p39_%' and state='failed' and (payload->>'started_at')::bigint>=:start order by (payload->>'finished_at')::bigint desc limit 12",
 'oi_committed_this_run': "select count(*) points,count(distinct instrument_id) native_markets,max(received_at) latest_received from oi_points where received_at>=:start and event_time>=:start",
 'coverage': "select i.venue,count(*) markets,count(*) filter(where :at-m.as_of between 0 and 90000) quote_90s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 90000) oi_90s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 360000) oi_6m,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 600000) oi_10m,count(*) filter(where :at-(m.payload->>'volume_24h_source_at')::bigint between 0 and 600000) volume_10m from instruments i join market_states m on m.instrument_id=i.id where i.venue in ('binance','binance_spot') group by 1",
 'blocking': "select pid,state,wait_event,extract(epoch from(clock_timestamp()-xact_start))::int transaction_seconds,pg_blocking_pids(pid) blockers,left(query,200) query from pg_stat_activity where datname=current_database() and pid<>pg_backend_pid() and cardinality(pg_blocking_pids(pid))>0 order by xact_start limit 12",
 'long_transactions': "select pid,state,wait_event,extract(epoch from(clock_timestamp()-xact_start))::int transaction_seconds,left(query,200) query from pg_stat_activity where datname=current_database() and pid<>pg_backend_pid() and clock_timestamp()-xact_start>interval '8 seconds' order by xact_start limit 12",
 'latest_projection_samples': "with recent as (select id,execution_instrument_id,expires_at from plans order by expires_at desc limit 300) select p.id plan_id,p.execution_instrument_id instrument_id,n.id latest_snapshot,n.as_of decision_at,c.id projection_id,c.payload->>'snapshot_id' projected_snapshot,c.computed_at,c.computed_at-n.as_of decision_to_projection_ms,c.valid_until from recent p cross join lateral(select id,as_of from signal_snapshots where plan_id=p.id order by as_of desc,id desc limit 1) n left join current_signals c on c.payload->>'snapshot_id'=n.id order by n.as_of desc limit 30",
 'new_observations': "select instrument_id,status,visible_at,payload->>'trigger_event_id' event_id,payload->>'source_at' source_at from observations where (payload->>'source_at')::bigint>=:start and (payload->>'persisted_at')::bigint>=:start order by (payload->>'persisted_at')::bigint desc limit 10",
 'projection': "select count(*) rows,max(computed_at) latest_computed,count(*) filter(where action in ('participate','trial') and valid_until>:at) unexpired_buy from current_signals",
 'btc_5m_continuity': "with bars as(select open_time,lag(open_time) over(order by open_time) prev from candles where instrument_id='binance:perp:BTCUSDT' and resolution='5' and is_closed and open_time>=:at-3600000 and close_time<=:at) select count(*) bars,count(*) filter(where open_time-prev<>300000) gaps,max(open_time) latest_open from bars",
 'layered_states': "select work_kind,analysis_status,strategy_result,count(*) native_states,max(last_success_at) latest_success from market_analysis_states group by 1,2,3 order by 1,2,3",
 'demand_states': "select origin_type,state,count(*) demands,count(distinct instrument_id) native_markets from analysis_demands where expires_at>:at group by 1,2 order by 1,2",
 'indexes': "select p.tablename,p.indexname,i.indisvalid from pg_indexes p join pg_class c on c.relname=p.indexname join pg_index i on i.indexrelid=c.oid where p.schemaname='public' and (p.tablename in ('analysis_demands','analysis_results','market_analysis_states') or p.indexname='ix_signal_snapshots_plan_latest')",
 'trace_instrument': "select instrument_id from analysis_demands where origin_type='observation' order by updated_at desc limit 1",
}
db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',
    connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000','connect_timeout':5},pool_size=1,max_overflow=0)
assert db.url.host in ('localhost','127.0.0.1') and db.url.port==55432 and db.url.database=='erji'
try:
    with db.connect() as c:
        assert c.scalar(text('SHOW transaction_read_only'))=='on'
        for key,query in queries.items():
            try: e['queries'][key]=[dict(r) for r in c.execute(text(query),{'at':at,'start':start,'run':run}).mappings()]
            except Exception as err: e['blind_spots'].append({'query':key,'error':type(err).__name__})
finally: db.dispose()
for r in registry:
    if r['run_id']!=run:
        e['blind_spots'].append({'log':r['name'],'error':'wrong_run'}); continue
    path=Path(r['log_dir'])/(r['name']+'.err.log')
    if not path.exists(): e['blind_spots'].append({'log':r['name'],'error':'missing'}); continue
    content=path.read_text('utf8',errors='replace')
    tokens={'tracebacks':'Traceback','deadlock_exception':'DeadlockDetected','sqlstate_40p01':'sqlstate=40P01',
            'long_connections':'database connection held','connection_closed':'ConnectionClosed',
            'timeouts':'TimeoutError','10055':'10055','recv_messages_error':"has no attribute 'recv_messages'"}
    lifecycles=[]
    for line in content.splitlines():
        if 'stream_lifecycle {' in line:
            try: lifecycles.append(json.loads(line.split('stream_lifecycle ',1)[1]))
            except ValueError: e['blind_spots'].append({'log':r['name'],'error':'malformed_lifecycle'})
    (OUT/(phase+'-'+r['name']+'-lifecycles.json')).write_text(json.dumps(lifecycles,ensure_ascii=False),'utf8')
    e['logs'][r['name']]={'lifecycle_records':len(lifecycles),'bytes':path.stat().st_size,**{k:content.count(v) for k,v in tokens.items()}}
    for k,n in (('rate_limit_429',429),('ban_418',418)):
        e['logs'][r['name']][k]=len(re.findall(r'(?:HTTP(?:/\d(?:\.\d)?)?\s+|status(?:_code)?[\s=:]+)'+str(n)+r'\b',content,re.I))
    startup=[line.split('startup ',1)[1] for line in content.splitlines() if 'startup {' in line]
    if startup:
        record=json.loads(startup[0]); e['logs'][r['name']]['startup']=record
        e['logs'][r['name']]['startup_hash_mismatches']=[p for p,h in record['source_hashes'].items() if hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
    (OUT/(phase+'-'+r['name']+'-tail.log')).write_text(content[-18000:],'utf8')
urls={'health':'http://127.0.0.1:18771/v1/health','operations':'http://127.0.0.1:18771/v1/operations',
      'budget':'http://127.0.0.1:18772/health','observations':'http://127.0.0.1:18771/v1/anomaly-observations?limit=5',
      'buy':'http://127.0.0.1:18771/v1/signals?view=opportunities&page_size=5',
      'web_observations':'http://127.0.0.1:18770/observations','web_buy':'http://127.0.0.1:18770/signals'}
trace=e['queries'].get('trace_instrument',[])
if trace:
    from urllib.parse import quote
    urls['causal_trace']='http://127.0.0.1:18771/v1/diagnostics/causal-trace?instrument='+quote(trace[0]['instrument_id'])+'&limit=5'
for name,url in urls.items():
    try:
        began=time.monotonic()
        with urllib.request.urlopen(url,timeout=12) as response: body=response.read(3000000); status=response.status
        e['http'][name]={'status':status,'elapsed_ms':round((time.monotonic()-began)*1000,2)}
        if name not in ('web_observations','web_buy'):
            data=json.loads(body); (OUT/(phase+'-'+name+'-api.json')).write_text(json.dumps(data,ensure_ascii=False,default=str),'utf8')
            e['http'][name].update(as_of=data.get('as_of'),rows=len(data.get('rows',[])),total=data.get('total'))
    except Exception as err: e['blind_spots'].append({'endpoint':name,'error':type(err).__name__})
(OUT/(phase+'.json')).write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str),'utf8')
summary={k:v for k,v in e.items() if k not in ('registry','queries','logs')}
summary['queries']={k:v for k,v in e['queries'].items() if k not in ('heartbeats','tasks','indexes')}
summary['tasks']=e['queries'].get('tasks')
summary['logs']={k:{n:v for n,v in row.items() if n!='startup'} for k,row in e['logs'].items()}
summary['heartbeats']=[{'role':b['role'],'state':b['state'],'age_ms':at-b['updated_at'],
                       'details':{k:v for k,v in b['details'].items() if k not in ('pending_pool','source_hashes')}}
                      for b in e['queries'].get('heartbeats',[]) if b['role'] in ('analysis_owner','anomaly_discovery','live_binance','streams','scan')]
(OUT/(phase+'-summary.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),'utf8')
beats={r['role']:r for r in e['queries'].get('heartbeats',[])}
d=beats.get('anomaly_discovery',{}).get('details',{})
a=beats.get('analysis_owner',{}).get('details',{})
print(json.dumps({'phase':phase,'run':run,'age_seconds':e['age_seconds'],'ports':e['ports'],'hash_mismatches':e['source_hash_mismatches'],'blind_spots':e['blind_spots'],'deadlocks':e['postgres_new_deadlocks'],'http':e['http'],'revision':e['queries'].get('revision'),'coverage':e['queries'].get('coverage'),'price_windows':d.get('window_coverage'),'oi_windows':d.get('oi_window_coverage'),'snapshot_age_ms':at-d.get('snapshot_at',at),'event_delivery':d.get('event_delivery'),'producer':a.get('producer'),'lanes':a.get('lane_slots'),'log_counts':summary['logs'],'blocking':e['queries'].get('blocking'),'long_transactions':e['queries'].get('long_transactions'),'oi_committed':e['queries'].get('oi_committed_this_run')},ensure_ascii=False,default=str))
