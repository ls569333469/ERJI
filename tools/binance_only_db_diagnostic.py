"""Finite read-only database/queue diagnostic. No exchange traffic."""
import sys,json,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine,text
from engine.config import settings
OUT=ROOT/'docs/p39-validation/binance-only-implementation'
db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=1000'})
queries={
 'activity':"""select pid,state,wait_event_type,wait_event,round(extract(epoch from clock_timestamp()-xact_start)::numeric,2) xact_seconds,round(extract(epoch from clock_timestamp()-query_start)::numeric,2) query_seconds,pg_blocking_pids(pid) blockers,left(regexp_replace(query,'[[:space:]]+',' ','g'),700) query_shape from pg_stat_activity where datname=current_database() and pid!=pg_backend_pid() and (state!='idle' or wait_event_type='Lock') order by xact_start nulls last""",
 'queue':"""select kind,state,count(*) n,max(greatest(0,:at-available_at)) max_wait_ms,percentile_cont(.95) within group(order by greatest(0,:at-available_at)) p95_wait_ms,percentile_cont(.95) within group(order by (payload->>'execution_ms')::bigint) last_execution_p95_ms from tasks where kind like 'p39_%' and not payload::jsonb ? 'retired_reason' group by 1,2 order by 1,2""",
 'heartbeat_metrics':"""select role,state,updated_at,details from worker_heartbeats where details->>'run_id'=:run and (role in ('jobs','live','streams','analysis_owner','signal_projection','anomaly_discovery','observation_analysis','supplements') or role like 'p39_%')""",
 'database_stats':"""select xact_commit,xact_rollback,blks_read,blks_hit,temp_files,temp_bytes,deadlocks,stats_reset from pg_stat_database where datname=current_database()""",
 'catalog':"""select venue,count(*) from instruments group by venue""",
}
registry=json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))
run=next(r['run_id'] for r in registry if r['name']=='worker-jobs')
report={'sampled_at_utc':datetime.now(timezone.utc).isoformat(),'read_only':True,'run_id':run,'snapshots':[]}
try:
    with db.connect() as c:
        for iteration in range(6):
            at=int(time.time()*1000);snapshot={'as_of':at}
            for key,query in queries.items():
                if iteration and key!='activity':continue
                try:snapshot[key]=[dict(r) for r in c.execute(text(query),{'at':at,'run':run}).mappings()]
                except Exception as exc:snapshot[key]={'error':type(exc).__name__,'detail':str(exc).split('[SQL:')[0][:150]}
            report['snapshots'].append(snapshot)
            if iteration<5:time.sleep(2)
finally:db.dispose()
path=OUT/('database-diagnostic-'+datetime.now(timezone.utc).strftime('%H%M%S')+'.json')
path.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf8')
print(json.dumps({'path':str(path),'activity':[{k:r[k] for k in ('pid','state','wait_event_type','wait_event','xact_seconds','query_seconds','blockers','query_shape')} for snap in report['snapshots'] for r in snap.get('activity',[])],'queue':report['snapshots'][0].get('queue')},ensure_ascii=False,default=str))
