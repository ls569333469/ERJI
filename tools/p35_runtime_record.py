"""Read-only P35 delivery record; only writes its own evidence JSON."""
from tools.p34_local import prepare
prepare()
import json, time, urllib.request
from collections import Counter
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, text
from engine.db import Session
from engine.models import UserPreference, RawEvent, Episode, Campaign, SignalSnapshot, OiPoint, Instrument

now = int(time.time()*1000)
result = {'as_of': now, 'beijing': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'external_requests': 0}
for name, endpoint in [('health','health'), ('operations','operations'), ('signals','signals?view=opportunities&page_size=300'), ('templates','strategy-templates')]:
    at=time.perf_counter()
    with urllib.request.urlopen('http://127.0.0.1:18770/v1/'+endpoint, timeout=30) as r:
        data=json.load(r)
    result[name]={'ms':round((time.perf_counter()-at)*1000), 'data':data}
rows=result['signals']['data']['rows']
result['list_summary']={'rows':len(rows),'strategies':dict(Counter(r.get('strategy_id') or 'original_box' for r in rows)),
    'oi_cap_available':sum(r.get('oi_cap_ratio') is not None for r in rows),
    'funding_present':sum(r.get('funding_rate') is not None for r in rows),
    'funding_fresh':sum(r.get('funding_rate') is not None and r.get('funding_status')=='fresh' for r in rows),
    'opportunity_counts':result['signals']['data'].get('opportunity_counts')}
with Session() as s:
    s.execute(text('SET TRANSACTION READ ONLY'))
    s.execute(text("SET LOCAL statement_timeout='15s'"))
    latest=select(OiPoint.instrument_id,func.max(OiPoint.event_time).label('at')).group_by(OiPoint.instrument_id).subquery()
    oi=list(s.execute(select(Instrument.id,latest.c.at).outerjoin(latest,latest.c.instrument_id==Instrument.id).where(Instrument.venue=='binance',Instrument.market_type=='linear_perpetual')))
    result['binance_oi']={'markets':len(oi),'fresh_6m':sum(at is not None and 0<=now-at<=360000 for _,at in oi),'latest':max((at or 0 for _,at in oi),default=0)}
    candidates=list(s.execute(select(Episode.strategy_id,Episode.phase,func.count()).where(Episode.id.startswith('p35-candidate-',autoescape=True)).group_by(Episode.strategy_id,Episode.phase)))
    result['candidates']=[{'strategy':a,'phase':b,'count':c} for a,b,c in candidates]
    result['new_firsts']=s.scalar(select(func.count()).select_from(RawEvent).where(RawEvent.endpoint=='p35_candidate_first'))
    result['snapshots_last_10m']=s.scalar(select(func.count()).select_from(SignalSnapshot).where(SignalSnapshot.as_of>now-600000))
    result['latest_snapshot']=s.scalar(select(func.max(SignalSnapshot.as_of)))
    result['recent_strategy_errors']=[{'key':r.key,'updated_at':r.updated_at,'details':r.payload} for r in s.scalars(select(UserPreference).where(UserPreference.key.startswith('p33_shadow_error:',autoescape=True),UserPreference.updated_at>now-600000))]
    histories=list(s.scalars(select(UserPreference).where(UserPreference.key.startswith('p34_history:',autoescape=True))))
    result['history_statuses']=dict(Counter(r.payload.get('status','unknown') for r in histories))
    archives=list(s.scalars(select(RawEvent).where(RawEvent.endpoint.startswith('https://data.binance.vision/',autoescape=True)).order_by(RawEvent.received_at.desc()).limit(5)))
    result['archive_examples']=[{'id':r.id,'received_at':r.received_at,'endpoint':r.endpoint,'payload':r.payload} for r in archives]
    firsts=list(s.scalars(select(RawEvent).where(RawEvent.source=='erji',RawEvent.endpoint=='p33_first')))
    originals={r.payload['event']['initial']['event_id']:r for r in firsts if r.payload.get('event',{}).get('initial')}
    checked=0;mismatches=[]
    for r in s.scalars(select(UserPreference).where(UserPreference.key.startswith('p33_shadow:',autoescape=True))):
        for key,current in r.payload.get('active_events',{}).items():
            if key not in originals:continue
            checked+=1;original=originals[key].payload['event']
            if original.get('initial')!=current.get('initial') or original.get('plan')!=current.get('plan'):mismatches.append(key)
    result['first_event_freeze']={'checked':checked,'mismatches':mismatches}
Path('docs/p35-validation/after-runtime.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf8')
print(json.dumps({k:result[k] for k in ('beijing','list_summary','binance_oi','snapshots_last_10m','recent_strategy_errors','first_event_freeze','history_statuses')},ensure_ascii=False))
