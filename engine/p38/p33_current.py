from engine.p39.timing import current_execution,QUOTE_MS,BOOK_MS,REFERENCE_MS,REVIEW_MS,WAIT_REVIEW_MS
"""Recheck an existing P33 event, retaining its own frozen rules and targets."""
from copy import deepcopy
from decimal import Decimal as D
from engine.models import UserPreference
from engine.pd2.analysis import closed_bars,trailing_protection

def refresh(s,plan,episode,latest,inst,state,now,bars=None):
    from engine.pd2.service import load_bars
    from engine.strategies.p33_runtime import advance
    from engine.strategies.p33_lifecycle import qualify
    from engine.pd2.config import ExecutionConfig
    from engine.pd2.book_clock import fresh,effective_time
    from engine.models import DataCapability
    from engine.repository import runtime_config
    from engine.pd2.providers import quote_usd_rate
    snap=deepcopy(latest.payload)
    checkpoint=s.get(UserPreference,'p33_shadow:'+inst.id,with_for_update=True)
    payload=deepcopy(checkpoint.payload) if checkpoint else {}
    active=payload.get('active_events',{});event=active.get(episode.id)
    market={**snap.get('market',{}),'instrument_id':inst.id,'asset_id':inst.asset_id,'quote_asset':inst.quote_asset,
            'price':state.payload.get('price') if state else None,'price_kind':state.payload.get('price_kind') if state else None,
            'as_of':state.as_of if state else 0,'quality':{'status':'fresh' if state and 0<=now-state.as_of<=QUOTE_MS else 'stale'}}
    # The persisted MarketDTO omits this transient qualification input. Always
    # read its current provenance; never inherit an old conversion from a plan.
    fx=quote_usd_rate(inst.quote_asset,s,now)
    market['quote_usd_rate']=str(fx) if fx is not None else None
    if event:
        bars={tf:closed_bars(rows,now) for tf,rows in (bars if bars is not None else load_bars(s,inst.id,now)).items()}
        event=advance(event,bars,now)
        price=market['price'];stop=event.get('current_protection',event['initial']['protection'])
        live=market['quality']['status']=='fresh' and price is not None
        if live and not (event.get('invalidated_at') or event.get('protection_observed_at')):
            if D(price)<=D(stop):event.update(protection_observed_at=now,status='protection_touched',protection_observation_kind='fresh_quote')
            for name,target in event['plan']['targets'].items():
                if name!='four_hour' and target and target['known_at']<=now and D(price)>=D(target['low']):
                    event.setdefault('target_first_observed',{}).setdefault(name,{'observed_at':now,'price':str(price),'observation_kind':'fresh_quote','execution_confirmed':False})
        trailing=event.get('analysis_config') or {}
        if trailing.get('trailing_mode')=='chandelier' and not (event.get('invalidated_at') or event.get('protection_observed_at')):
            new=trailing_protection(bars.get(event['initial']['timeframe'],[]),stop,event['initial']['protection'],now,trailing['trailing_period'],str(trailing['trailing_atr_multiple']))
            if D(new)>D(stop):event['current_protection']=new;event.setdefault('protection_history',[]).append({'price':new,'observed_at':now})
        cached=s.get(DataCapability,f'{inst.venue}:execution_book:{inst.id}')
        book=cached.payload if cached and cached.status=='fresh' and cached.expires_at>=now and fresh(cached.payload,now) else None
        cfg=current_execution(event['execution_config']) if event.get('execution_config') else None
        qualification=qualify(event,market,book,cfg,now,bars,event['initial'].get('parent_event_id') in active)
        event['qualification']=qualification
        action=qualification['rule_action']
        if event['initial']['strategy_id'] in runtime_config(s).disabled_strategies and action['code'] in ('participate','trial'):
            action={**action,'code':'paused','label':'暂停参与','reason':'用户关闭此策略','reason_codes':action['reason_codes']+['strategy_disabled']}
        end=event.get('invalidated_at') or event.get('protection_observed_at')
        phase='invalidated' if end else snap['phase']
        snap.update(action=action,phase=phase,execution=qualification['execution'],research_qualification=qualification,
                    current_protection=event.get('current_protection',stop),market=market,
                    valid_until=min(plan.expires_at,market['as_of']+QUOTE_MS,(effective_time(book or {}) or 0)+QUOTE_MS,(book or {}).get('received_at',0)+BOOK_MS) if action['code'] in ('participate','trial') else now)
        snap['tracking']={**snap.get('tracking',{}),'target_first_observed_at':{k:v['observed_at'] for k,v in event.get('target_first_observed',{}).items()},'protection_history':event.get('protection_history',[]),'first_invalidation_observed_at':event.get('invalidated_at')}
        active[episode.id]=event;payload['active_events']=active
        if event!=checkpoint.payload.get('active_events',{}).get(episode.id):checkpoint.payload=payload;checkpoint.updated_at=now
    else:
        # A missing checkpoint is a recovery gap, never permission to bypass the
        # original parent, continuity and strategy-specific dependency gates.
        action={**snap['action'],'code':'expired' if now>=plan.expires_at else 'paused','label':'等待新计划' if now>=plan.expires_at else '暂停参与','reason':'原事件追踪记录待恢复；保留冻结计划与保护','reason_codes':['tracking_checkpoint_missing']}
        phase=snap['phase']
        if market['quality']['status']=='fresh' and market['price'] is not None and D(market['price'])<=D(snap['current_protection']):
            phase='invalidated';action.update(code='ended',label='停止旧计划',reason='保护已触发',reason_codes=['protection_or_invalidation'])
        snap.update(action=action,phase=phase,market=market,valid_until=now)
    snap.update(id=plan.id+':'+str(now),as_of=now,known_at=now,computed_at=now,analysis_computed_at=snap.get('analysis_computed_at',latest.as_of))
    snap['evidence']={**snap['evidence'],'protection':snap['current_protection']}
    snap['trade_thesis']={**snap.get('trade_thesis',{}),'headline':snap['action']['reason']}
    return snap
