"""Bounded read-only diagnosis of remaining recovery gaps; no provider calls."""
from pathlib import Path
import sys,time,json,collections
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from engine.config import settings
from engine.pd2.universe_policy import primary_eligible
from engine.models import Instrument
from engine.repository import runtime_config
from sqlalchemy import create_engine,text,select
from sqlalchemy.orm import Session
at=int(time.time()*1000)
db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
out={'as_of':at,'read_only':True,'oi':{},'liquidity':{},'due':{},'heartbeats':[]}
max_age=max(360000,runtime_config().collector_interval_seconds*3000)
out['liquidity_max_age_seconds']=max_age//1000
with Session(db) as s:
    instruments={i.id:i for i in s.scalars(select(Instrument).where(Instrument.venue.in_(('binance','binance_spot'))))}
    rows=list(s.execute(text("select instrument_id,payload->>'oi_as_of' oi_at,payload->>'oi_status' oi_status,payload->>'volume_24h_source_at' volume_at,payload->>'volume_24h_received_at' volume_received,coalesce(payload->>'quote_volume_24h',payload->>'volume_24h_quote',payload->>'quote_volume') volume from market_states where instrument_id like 'binance:%' or instrument_id like 'binance_spot:%'" )).mappings())
    active_ids=set()
    for key,p in s.execute(text("select key,payload from user_preferences where key in ('p39_structure_active','p39_trend_active','p37_active')")):
        active_ids.update(p.get('instrument_ids',[]))
    selected=[r for r in rows if r['instrument_id'] in active_ids and instruments.get(r['instrument_id']) and instruments[r['instrument_id']].venue=='binance' and primary_eligible(instruments[r['instrument_id']])]
    groups={'all_contracts':[r for r in rows if instruments.get(r['instrument_id']) and instruments[r['instrument_id']].venue=='binance'],
            'eligible_contracts':[r for r in rows if instruments.get(r['instrument_id']) and instruments[r['instrument_id']].venue=='binance' and primary_eligible(instruments[r['instrument_id']])],
            'active_analysis_contracts':selected}
    for key,group in groups.items():
        ages=[at-int(r['oi_at']) for r in group if r['oi_at']]
        buckets=collections.Counter('missing' if not r['oi_at'] else '<=90s' if at-int(r['oi_at'])<=90000 else '90s-6m' if at-int(r['oi_at'])<=360000 else '6m-15m' if at-int(r['oi_at'])<=900000 else '>15m' for r in group)
        out['oi'][key]={'markets':len(group),'age_buckets':dict(buckets),'median_age_s':round(sorted(ages)[len(ages)//2]/1000,1) if ages else None,
         'stale_examples':[{'id':r['instrument_id'],'age_s':round((at-int(r['oi_at']))/1000),'oi_status':r['oi_status']} for r in sorted(group,key=lambda r:int(r['oi_at'] or 0))[:8]]}
    for venue in ('binance','binance_spot'):
        group=[r for r in rows if instruments.get(r['instrument_id']) and instruments[r['instrument_id']].venue==venue and instruments[r['instrument_id']].quote_asset=='USDT' and primary_eligible(instruments[r['instrument_id']])]
        counts=collections.Counter();stale=[]
        for r in group:
            if r['volume'] is None:kind='missing'
            elif not r['volume_at'] or not r['volume_received']:kind='clock_missing'
            elif max(at-int(r['volume_at']),at-int(r['volume_received']))>max_age:kind='stale'
            elif float(r['volume'])<1000000:kind='below_threshold'
            else:kind='available_over_1m'
            counts[kind]+=1
            if kind=='stale' and float(r['volume'] or 0)>=1000000:stale.append({'id':r['instrument_id'],'volume':r['volume'],'age_s':round((at-int(r['volume_at']))/1000)})
        out['liquidity'][venue]={'markets':len(group),'counts':dict(counts),'stale_over_threshold_examples':stale[:8]}
    for key,p,updated in s.execute(text("select key,payload,updated_at from user_preferences where key in ('due:binance:oi','due:binance:price','due:binance_spot:price')")):
        visited=p.get('visited',{});ages=sorted((at-int(v))/1000 for v in visited.values())
        out['due'][key]={'checkpoint_age_s':round((at-updated)/1000,1),'visited_count':len(visited),'visit_age_median_s':round(ages[len(ages)//2],1) if ages else None,'visited_over_6m':sum(x>360 for x in ages),'enqueued_count':len(p.get('enqueued',{})),'retry_count':len(p.get('retries',{})),
         'latest_visits':[{'symbol':k,'age_s':round((at-v)/1000,1)} for k,v in sorted(visited.items(),key=lambda r:r[1],reverse=True)[:5]]}
    run=json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))[0]['run_id']
    for role,state,updated,details in s.execute(text("select role,state,updated_at,details from worker_heartbeats where details->>'run_id'=:run and (role like 'derivative%' or role like 'collector%' or role like 'universe%' or role like 'discovery_receiver%' or role='p36_budget_owner') order by role"),{'run':run}):
        out['heartbeats'].append({'role':role,'state':state,'age_ms':at-updated,'details':details})
db.dispose()
p=ROOT/'docs/p39-validation/deadlock-fix-20261008/activation/recovery-detail.json';p.write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),'utf8')
print(json.dumps({k:v for k,v in out.items() if k!='heartbeats'},ensure_ascii=False,indent=2))
print(json.dumps([{'role':r['role'],'state':r['state'],'age_ms':r['age_ms'],'details':r['details'] if r['role'].startswith(('derivative','universe','collector')) else {k:v for k,v in r['details'].items() if k in ('last_receive_at','markets','loop_lag')}} for r in out['heartbeats']],ensure_ascii=False,indent=2))
