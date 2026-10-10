"""Read-only acceptance snapshot. No market requests, fabricated signals or releases."""
from pathlib import Path
import sys,json,time,urllib.request,concurrent.futures,statistics
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[4]/'.env');load_dotenv(Path(__file__).resolve().parents[1]/'.env',override=True)
from sqlalchemy import select,func,text
from engine.db import Session,engine
from engine.models import Instrument,MarketState,WorkerHeartbeat,UserPreference,RawEvent,CurrentSignal,IdentityLink
from engine.pd2.universe_policy import primary_eligible
from engine.repository import runtime_config
from engine.strategies.p33 import NAMES
from engine.strategies.p33_release import gate


def run():
    if engine.url.host not in ('localhost','127.0.0.1'):raise RuntimeError('local_database_required')
    now=int(time.time()*1000);config=runtime_config();result={'as_of':now,'external_requests':0}
    with Session() as s:
        groups={}
        for inst,state in s.execute(select(Instrument,MarketState).outerjoin(MarketState)):
            if not primary_eligible(inst):continue
            g=groups.setdefault(inst.venue,{'eligible':0,'quote_fresh_6m':0,'quote_fresh_15s':0,'oi_fresh_6m':0})
            g['eligible']+=1
            if state:
                age=now-state.as_of
                g['quote_fresh_6m']+=int(0<=age<=360000);g['quote_fresh_15s']+=int(0<=age<=15000)
                at=state.payload.get('oi_as_of');g['oi_fresh_6m']+=int(at is not None and 0<=now-at<=360000)
        result['market_freshness']=groups
        result['workers']=[{'role':w.role,'state':w.state,'age_seconds':round((now-w.updated_at)/1000,1)} for w in s.scalars(select(WorkerHeartbeat))]
        reason=CurrentSignal.payload['opportunity_eligibility_reason'].as_string()
        result['waiting_reasons']=[{'action':a,'reason':r,'count':n} for a,r,n in s.execute(select(CurrentSignal.action,reason,func.count()).group_by(CurrentSignal.action,reason))]
        result['identity_statuses']=dict(s.execute(select(IdentityLink.status,func.count()).group_by(IdentityLink.status)).all())
        result['release_gates']={name:gate(s,name,config.validation,now) for name in NAMES}
        result['recent_observer_errors']=s.scalar(select(func.count()).select_from(UserPreference).where(UserPreference.key.like('p33_shadow_error:%'),UserPreference.updated_at>=now-600000))
        result['schema']=s.execute(text('select version_num from alembic_version')).scalar()
        index=s.execute(text("select indisvalid from pg_index join pg_class on indexrelid=pg_class.oid where relname='ix_raw_native_instrument_time'")).scalar()
        result['native_tape_index_valid']=index
        snapshots=list(s.scalars(select(RawEvent).where(RawEvent.source=='erji',RawEvent.endpoint=='p33_snapshot').order_by(RawEvent.received_at.desc()).limit(120)))
        result['recent_real_chart_coverage']={}
        for raw in snapshots:
            p=raw.payload;key=p.get('strategy_id','unknown');g=result['recent_real_chart_coverage'].setdefault(key,{'snapshots':0,'fib_c':0,'multiple_protection_steps':0})
            g['snapshots']+=1;g['fib_c']+=int(len(p.get('event_overlays',{}).get('fibonacci',{}).get('anchors',[]))>=3)
            g['multiple_protection_steps']+=int(len(p.get('tracking',{}).get('protection_history',[]))>=3)
    def get(_):
        started=time.perf_counter()
        with urllib.request.urlopen('http://127.0.0.1:18770/v1/signals?view=opportunities&page_size=300',timeout=15) as response:
            body=json.load(response)
        return {'ms':round((time.perf_counter()-started)*1000,1),'rows':len(body['rows']),'total':body['pagination']['total']}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:measurements=list(pool.map(get,range(20)))
    ordered=sorted(x['ms'] for x in measurements)
    result['list_load']={'concurrency':4,'samples':measurements,'p95_ms':ordered[18],'max_ms':max(ordered),'full_300_rows_verified':all(x['rows']==300 for x in measurements)}
    return result


if __name__=='__main__':
    out=Path('docs/p33-validation/remaining-20261002');out.mkdir(parents=True,exist_ok=True)
    result=run();(out/'runtime.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('market_freshness','recent_observer_errors','recent_real_chart_coverage','schema','list_load')},ensure_ascii=False))
