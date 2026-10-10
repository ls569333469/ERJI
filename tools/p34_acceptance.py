"""Local retained evidence, coverage and real-history acceptance. No exchange I/O."""
from tools.p34_local import prepare
prepare()
from pathlib import Path
from collections import Counter
from hashlib import sha256
import json,time
from sqlalchemy import select,func,text
from engine.db import Session
from engine.models import Candle,RawEvent,UserPreference,Episode,WorkerHeartbeat
from engine.pd2.data_health import refresh
from engine.repository import runtime_config
from engine.strategies.p33 import NAMES
from engine.strategies.p33_release import gate
from engine.pd2.persistent_avwap import update,read_series
from engine.pd2.service import candle_dict

def run():
    now=int(time.time()*1000)
    result={'as_of':now,'external_requests':0,'data_chain':refresh()}
    matrix={name:{s:[] for s in ('triggered','waiting','invalidated','missing_data')} for name in ['box',*NAMES]}
    with Session() as s:
        for key,payload in s.execute(select(UserPreference.key,UserPreference.payload).where(UserPreference.key.startswith('p33_shadow:',autoescape=True))):
            for item in payload.get('result',{}).get('results',[]):
                strategy=item.get('strategy_id');status=item.get('status')
                if strategy in matrix and status in matrix[strategy] and len(matrix[strategy][status])<3:
                    matrix[strategy][status].append({'record':key,'known_at':item.get('known_at'),'reason':item.get('reason')})
        for row in s.scalars(select(RawEvent).where(RawEvent.source=='erji',RawEvent.endpoint=='p33_snapshot').order_by(RawEvent.received_at.desc()).limit(300)):
            p=row.payload;strategy=p.get('strategy_id');status='invalidated' if p.get('phase')=='invalidated' else 'triggered'
            if strategy in matrix and len(matrix[strategy][status])<3:
                matrix[strategy][status].append({'snapshot_id':row.id,'instrument_id':p.get('instrument_id'),'as_of':row.received_at})
        for row in s.scalars(select(Episode).where(Episode.rule_version=='box-20-60-v1').limit(1000)):
            status='invalidated' if row.phase in ('invalidated','ended') else 'triggered' if row.evidence.get('trigger') else 'waiting'
            if len(matrix['box'][status])<3:matrix['box'][status].append({'episode_id':row.id,'phase':row.phase})
        result['real_matrix']={name:{status:{'status':'observed' if rows else 'no_real_sample','evidence':rows} for status,rows in states.items()} for name,states in matrix.items()}
        cfg=runtime_config()
        result['release_gates']={name:gate(s,name,cfg.validation,now) for name in NAMES}
        result['gate_policy']={'source':'engine/pd2/config.py::ValidationConfig','status':'implementation_defaults_no_numeric_P33_basis_found',
            'thresholds':cfg.validation.model_dump(),'days_semantics':'verified_sample derives unique completed events, canonical assets, historical_days and forward_days; frozen protocol must explicitly choose sample_days_basis',
            'action':'unchanged; require evidence-backed policy decision, do not lower thresholds to claim completion'}
        result['workers']=[{'role':r.role,'state':r.state,'age_seconds':(now-r.updated_at)/1000,'details':r.details}
                           for r in s.scalars(select(WorkerHeartbeat))]
        result['observer_errors']=[{'key':r.key,'at':r.updated_at,'reason':r.payload.get('reason')} for r in s.scalars(
            select(UserPreference).where(UserPreference.key.startswith('p33_shadow_error:',autoescape=True),UserPreference.updated_at>=now-600000))]
        health=s.get(UserPreference,'p34_data_health')
        result['asset_mapping_and_fields']=health.payload['rows'] if health else []
        result['history_cursors']=[{'instrument':r.key.split('p34_history:',1)[1],**r.payload}
            for r in s.scalars(select(UserPreference).where(UserPreference.key.startswith('p34_history:',autoescape=True)))]
        result['oi_coverage']=[{'instrument':r.key.split('p34_oi_coverage:',1)[1],**r.payload}
            for r in s.scalars(select(UserPreference).where(UserPreference.key.startswith('p34_oi_coverage:',autoescape=True)))]
        result['queues']={r.key:{'updated_at':r.updated_at,'completed':len(r.payload.get('visited',{})),
            'retrying':len(r.payload.get('retries',{})),'oldest_pending_at':min(r.payload.get('enqueued',{}).values(),default=None)}
            for r in s.scalars(select(UserPreference).where(UserPreference.key.startswith('due:',autoescape=True)))}
        result['request_budgets']={r.key:r.payload for r in s.scalars(select(UserPreference).where(
            UserPreference.key.startswith('http_budget:',autoescape=True)))}
        from engine.pd2.storage import budget_plan
        result['supplemental_credit_capacity']=budget_plan(cfg,0)
        result['capability_usage_policies']=__import__('engine.pd2.capability_policy',fromlist=['POLICIES']).POLICIES.copy()
        result['capability_usage_policies']={':'.join(k):{'refresh_ms':v[0],'reference_max_age_ms':v[1],
            'real_time_max_age_ms':300000,'role':'reference_only_when_older_than_live_age'} for k,v in result['capability_usage_policies'].items()}
        largest=s.execute(select(Candle.instrument_id,Candle.resolution,func.count().label('n')).where(Candle.is_closed==True,Candle.close_time<=now,Candle.received_at<=now)
            .group_by(Candle.instrument_id,Candle.resolution).order_by(func.count().desc()).limit(1)).first()
        observations=list(s.execute(select(RawEvent.received_at).where(RawEvent.source=='erji',RawEvent.endpoint=='p34_data_health').order_by(RawEvent.received_at)))
        times=[r[0] for r in observations]
        result['continuous_observation']={'samples':len(times),'start':times[0] if times else None,'end':times[-1] if times else None,
            'span_seconds':(times[-1]-times[0])/1000 if len(times)>1 else 0,
            'max_gap_seconds':max([(b-a)/1000 for a,b in zip(times,times[1:])],default=0),'required_seconds':86400}
        result['continuous_observation']['complete']=bool(len(times)>1 and times[-1]-times[0]>=86400000 and result['continuous_observation']['max_gap_seconds']<=600)
    if largest:
        iid,tf,count=largest
        # Use real source candles; derived test checkpoints roll back together.
        with Session() as s:
            tx=s.begin()
            try:
                bars=list(s.scalars(select(Candle).where(Candle.instrument_id==iid,Candle.resolution==tf,Candle.is_closed==True,
                    Candle.close_time<=now,Candle.received_at<=now).order_by(Candle.open_time)))
                manifest=sha256(json.dumps([candle_dict(b) for b in bars],sort_keys=True).encode()).hexdigest()
                state=None;previous_cursor=None
                for iteration in range(min(300,(count//1000)+4)):
                    state=update(s,'p34-real-history-audit',iid,tf,bars[0].open_time,now);s.flush()
                    if state.get('next_open')==previous_cursor or state.get('next_open',0)>=bars[-1].close_time:break
                    previous_cursor=state.get('next_open')
                points=read_series(s,state)
                from decimal import Decimal as D
                volume=sum((b.base_volume for b in bars if b.base_volume is not None),D(0))
                complete=state.get('next_open',0)>=bars[-1].close_time and len(points)==len(bars)
                expected=sum((((b.high+b.low+b.close)/3)*b.base_volume for b in bars if b.base_volume is not None),D(0))/volume if volume else None
                result['real_history']={'instrument_id':iid,'timeframe':tf,'bars':count,'source_sha256':manifest,
                    'state':state.get('status'),'points':len(points),'complete':complete,'full_sum_equal':bool(complete and expected is not None and D(points[-1]['event_avwap'])==expected),
                    'over_20000_real_available':count>20000,'all_derived_writes_rolled_back':True}
            finally:tx.rollback()
    return result

if __name__=='__main__':
    output=run();dest=Path('docs/p34-validation');dest.mkdir(exist_ok=True)
    (dest/'acceptance.json').write_text(json.dumps(output,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'real_history':output.get('real_history'),'continuous':output['continuous_observation'],
        'observer_errors':output['observer_errors'],'gate_enabled':sum(r['enabled'] for r in output['release_gates'].values())},ensure_ascii=False))
