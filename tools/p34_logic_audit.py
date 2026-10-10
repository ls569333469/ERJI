"""Read-only candidate-to-page audit. No provider requests or business writes."""
from tools.p34_local import prepare
prepare()
import json,time,sys
from pathlib import Path
from collections import Counter
from sqlalchemy import select,func
from engine.db import Session
from engine.models import CurrentSignal,MarketState,Candle,WorkerHeartbeat,UserPreference,Episode
from engine.pd2.signal_projection import read

def run():
    now=int(time.time()*1000)
    with Session() as s:
        rows=list(s.scalars(select(CurrentSignal)))
        candidates=[r for r in rows if r.action in ('waiting','no_chase') and r.payload.get('opportunity_eligible')]
        ids=[r.instrument_id for r in candidates]
        quotes={r.instrument_id:r for r in s.scalars(select(MarketState).where(MarketState.instrument_id.in_(ids)))}
        latest={(iid,tf):at for iid,tf,at in s.execute(select(Candle.instrument_id,Candle.resolution,func.max(Candle.close_time)).where(
            Candle.instrument_id.in_(ids),Candle.is_closed.is_(True),Candle.close_time<=now).group_by(Candle.instrument_id,Candle.resolution))}
        detail=[]
        for r in candidates:
            p=r.payload;q=quotes.get(r.instrument_id);until=p.get('structure_valid_until') or (p.get('decision_as_of') or 0)+4200000
            frames={tf:{'latest_close':latest.get((r.instrument_id,tf)),
                'expected_close':now//step*step} for tf,step in [('60',3600000),('240',14400000)]}
            blockers=[]
            if q is None or now-q.as_of>360000:blockers.append('quote_stale_or_missing')
            if until<now:blockers.append('structure_expired')
            detail.append({'event_id':r.id,'instrument_id':r.instrument_id,'structure_valid_until':until,
                'decision_at':p.get('decision_as_of'),'projection_at':r.computed_at,'quote_at':q.as_of if q else None,
                'required_frames':frames,'page_blockers':blockers})
        budget=s.get(UserPreference,'http_budget:binance_family');p=budget.payload if budget else {}
        baseline=Path('docs/p34-validation/logic-repair/before.json')
        original=json.loads(baseline.read_text(encoding='utf-8'))['candidates'] if baseline.exists() else []
        original_status=[]
        for item in original:
            r=s.get(CurrentSignal,item['event_id']);e=s.get(Episode,item['event_id']);payload=r.payload if r else {}
            original_status.append({'event_id':item['event_id'],'instrument_id':item['instrument_id'],
                'phase':e.phase if e else None,'evaluated_at':e.evidence.get('evaluated_at') if e else None,
                'reason':e.evidence.get('reason') if e else None,'action':r.action if r else 'not_projected',
                'eligibility':payload.get('opportunity_eligibility_reason'),
                'structure_valid_until':payload.get('structure_valid_until')})
        return {'as_of':now,'provider_requests':0,'actions':dict(Counter(r.action for r in rows)),
            'original_candidates_now':original_status,
            'candidate_count':len(candidates),'candidates':detail,
            'blockers':dict(Counter(b for r in detail for b in r['page_blockers'])),
            'workers':[{'role':r.role,'age_seconds':round((now-r.updated_at)/1000,1),'state':r.state,'details':r.details}
                for r in s.scalars(select(WorkerHeartbeat)) if r.role in ('signals','signal_projection','candles_binance','candles_binance_spot')],
            'queue':{'pending':len(p.get('pending',[])),'max_heartbeat_age_seconds':max([(now-r['seen_at'])/1000 for r in p.get('pending',[])],default=0),
                'local_weight_1m':sum(r[1] for r in p.get('entries',[]) if r[0]>now-60000),'cap':p.get('budget_cap'),'minimum_gap_ms':p.get('minimum_gap_ms')},
            'page':read('opportunities')}

if __name__=='__main__':
    result=run();dest=Path('docs/p34-validation/logic-repair');dest.mkdir(parents=True,exist_ok=True)
    label=sys.argv[1] if len(sys.argv)>1 else str(result['as_of'])
    (dest/(label+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('as_of','candidate_count','blockers','queue')},ensure_ascii=False))
    print('visible',result['page']['pagination']['total'])
    print('rows',[(r['symbol'],r['action_code']) for r in result['page']['rows']])
