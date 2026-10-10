"""Bounded, local read-only follow-up for the current activation recheck."""
import json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine,text
from engine.config import settings
OUT=ROOT/'docs/p39-validation/priority-capacity-implementation-20261009/activation'
baseline=json.loads((OUT/'recheck.json').read_text('utf8'))
at=int(time.time()*1000)
db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
assert db.url.host in ('127.0.0.1','localhost') and db.url.port==55432
queries={
 'failed_results':"select instrument_id,work_kind,payload from analysis_results where payload->>'status'='failed' and (payload->>'started_at')::bigint>=:start limit 10",
 'oi_age':"select count(*) markets,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 360000) oi_6m,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint>600000) stale_10m,max(:at-(m.payload->>'oi_as_of')::bigint) oldest_ms from market_states m join instruments i on i.id=m.instrument_id where i.venue='binance' and m.payload->>'oi_as_of' is not null",
 'contract_field_exceptions':"select i.id,i.spec->>'status' status,m.as_of,m.payload->>'oi_as_of' oi_as_of,m.payload->>'volume_24h_source_at' volume_as_of from instruments i join market_states m on m.instrument_id=i.id where i.venue='binance' and (m.payload->>'oi_as_of' is null or :at-(m.payload->>'oi_as_of')::bigint>600000) limit 12",
 'stream_health':"select role,state,updated_at,details from worker_heartbeats where details->>'run_id'=:run and (role='anomaly_discovery' or role='analysis_owner' or role='candles_binance_spot' or role like 'stream_binance%') order by role",
 'blocking':"select pid,wait_event,extract(epoch from(clock_timestamp()-xact_start))::int age_seconds,pg_blocking_pids(pid) blockers,left(query,140) query from pg_stat_activity where datname=current_database() and cardinality(pg_blocking_pids(pid))>0 limit 10",
}
result={'as_of':at,'run_id':baseline['run_id'],'read_only':True,'queries':{}}
with db.connect() as c:
 for key,sql in queries.items():
  try:result['queries'][key]=[dict(r) for r in c.execute(text(sql),{'at':at,'start':baseline['run_start_ms'],'run':baseline['run_id']}).mappings()]
  except Exception as e:result['queries'][key]={'error':type(e).__name__,'message':str(e).split('[SQL:')[0][:400]}
db.dispose()
(OUT/'recheck-detail.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),'utf8')
print(json.dumps(result,ensure_ascii=False,default=str))
