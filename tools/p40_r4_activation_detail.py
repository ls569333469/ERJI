"""Finite follow-up details from local read-only DB and current run evidence."""
from pathlib import Path
import json,sys,time,collections
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine,text
from engine.config import settings
OUT=ROOT/'docs/p40-validation/r4-activation-20261010'
phase=sys.argv[1];e=json.loads((OUT/(phase+'.json')).read_text('utf8'))
ops=json.loads((OUT/(phase+'-operations-api.json')).read_text('utf8'))
run=e['run_id'];start=e['run_start_ms'];at=int(time.time()*1000)
result={'run_id':run,'snapshot_as_of':e['as_of'],'detail_as_of':at,'read_only':True,'blind_spots':[]}
queries={
 'oi_tiers': "with tiers as (select i.id as instrument_id,case when exists(select 1 from analysis_demands d where d.instrument_id=i.id and d.origin_type='observation' and d.state='active' and d.expires_at>:at) then 'observation_demand' else 'other' end tier from instruments i where i.venue='binance') select t.tier,count(*) markets,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 90000) oi_90s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 360000) oi_6m from market_states m join tiers t on t.instrument_id=m.instrument_id group by t.tier",
 'genuine_new_observations': "select id,instrument_id,payload->>'trigger_event_id' event_id,(payload->>'source_at')::bigint source_at,(payload->>'persisted_at')::bigint persisted_at from observations where (payload->>'source_at')::bigint>=:start and (payload->>'persisted_at')::bigint>=:start order by (payload->>'persisted_at')::bigint desc limit 30",
 'app_long_transactions': "select pid,backend_type,state,wait_event,extract(epoch from clock_timestamp()-xact_start) seconds,pg_blocking_pids(pid) blockers,left(query,180) query from pg_stat_activity where datname=current_database() and pid<>pg_backend_pid() and backend_type='client backend' and clock_timestamp()-xact_start>interval '8 seconds' order by xact_start limit 12",
 'api_startup_heartbeat': "select role,state,updated_at,details from worker_heartbeats where role='api'",
}
db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',pool_size=1,max_overflow=0,
 connect_args={'connect_timeout':5,'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
assert db.url.host in ('localhost','127.0.0.1') and db.url.port==55432 and db.url.database=='erji'
result['queries']={}
try:
 with db.connect() as c:
  assert c.scalar(text('SHOW transaction_read_only'))=='on'
  for name,q in queries.items():
   try:result['queries'][name]=[dict(r) for r in c.execute(text(q),{'at':at,'start':start,'run':run}).mappings()]
   except Exception as error:result['blind_spots'].append({'query':name,'error':type(error).__name__})
  # Trace IDs saved in previous phases even when newer events replace an Observation endpoint.
  events={}
  for f in sorted(OUT.glob('*.json')):
   if f.stem not in ('initial','warmup','first-cycle','five-minute','ten-minute','final'):continue
   prior=json.loads(f.read_text('utf8'))
   for row in prior.get('queries',{}).get('new_observation_identity',[]):
    if row.get('observation_event_id') and int(row.get('observation_source_at') or 0)>=start:
     events[row['observation_event_id']]={'event_id':row['observation_event_id'],'instrument_id':row['instrument_id'],'source_at':row['observation_source_at']}
  tracked=[]
  for event in list(events.values())[:12]:
   q="select r.id,r.work_kind,r.payload->>'status' status,r.computed_at from analysis_results r where r.instrument_id=:iid and r.computed_at>=:start and exists(select 1 from json_array_elements(coalesce(r.payload->'origin'->'sources','[]'::json)) ref where ref->'source'->>'trigger_event_id'=:event) order by r.computed_at desc limit 8"
   rows=[dict(r) for r in c.execute(text(q),{'iid':event['instrument_id'],'event':event['event_id'],'start':start}).mappings()]
   tracked.append({**event,'analysis_receipts':rows})
  result['genuine_event_result_tracking']=tracked
finally:db.dispose()
channels=[]
for b in e['queries'].get('heartbeats',[]):
 d=b['details'];life=d.get('lifecycle');
 if not life:continue
 transport=life.get('transport') or {}
 channels.append({'role':b['role'],'state':b['state'],'heartbeat_age_ms':e['as_of']-b['updated_at'],
  'connection_id':life.get('connection_id'),'venue':life.get('venue'),'channel':life.get('channel'),
  'transport_counts':transport.get('counts'),'queue_peak':transport.get('frame_queue_peak'),
  'read_paused_ms':transport.get('read_paused_ms'),'frame_queue':transport.get('frame_queue'),
  'receive_timings':d.get('receive_timings'),'quote_writer':d.get('quote_writer'),'write_queue':d.get('write_queue')})
result['channels']=channels
closures=json.loads((OUT/(phase+'-worker-streams-lifecycles.json')).read_text('utf8'))
result['closure_by_venue_channel_reason']=dict(collections.Counter(str((x.get('venue'),x.get('channel'),x.get('end_reason'),x.get('close',{}).get('sent',{}).get('code'))) for x in closures))
result['closure_samples']=[{'role':x['role'],'connection_id':x['connection_id'],'close':x['close'],
 'transport':x.get('transport'),'pipeline':x.get('pipeline')} for x in closures if x.get('end_reason')=='failure'][:8]
holds=[]
for r in e['registry']:
 if r['run_id']!=run:continue
 log=Path(r['log_dir'])/(r['name']+'.err.log')
 if not log.exists():continue
 for line in log.read_text('utf8',errors='replace').splitlines():
  if 'database_connection_long_hold {' in line:
   try:holds.append(json.loads(line.split('database_connection_long_hold ',1)[1]))
   except ValueError:result['blind_spots'].append({'log':r['name'],'error':'malformed_long_hold'})
result['long_holds']=holds[:128];result['long_holds_truncated']=len(holds)>128
result['long_hold_summary']=[{'pool':pool,'records':len(rows),'max_hold_ms':max(x['hold_ms'] for x in rows),
 'examples':[{'hold_ms':x['hold_ms'],'sql_ms':round(x['sql_ms'],2),'sql_count':x['sql_count'],
 'checkout_stage':x['checkout_stage'],'backend_pid':x['backend_pid'],'pid':x['pid']} for x in sorted(rows,key=lambda x:-x['hold_ms'])[:3]]}
 for pool in sorted({x['pool'] for x in holds}) if (rows:=[x for x in holds if x['pool']==pool])]
d=next((w['details'] for w in ops['workers'] if w['role']=='anomaly_discovery'),{})
result['price_strata']=d.get('price_receipts',{}).get('strata');result['sampled_price_receipts']=d.get('price_receipts',{}).get('examples')
probe=d.get('oi_persisted_probe',{});points=probe.get('endpoints',[])
result['oi_probe_summary']={'snapshot_at':probe.get('snapshot_at'),'sampled_markets':len(probe.get('sampled_instruments',[])),
 'points':len(points),'absent_memory':sum(not r['in_detector_memory'] for r in points),
 'absent_known_before_eval':sum(not r['in_detector_memory'] and r['known_before_last_evaluation'] for r in points),
 'scope':'bounded diagnostic sample, absence does not prove lost signal'}
result['oi_probe_absent_examples']=[r for r in points if not r['in_detector_memory']][:20]
(OUT/(phase+'-detail.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),'utf8')
print(json.dumps({k:result[k] for k in ('run_id','blind_spots','closure_by_venue_channel_reason','long_hold_summary','oi_probe_summary')},ensure_ascii=False,default=str))
