"""Versioned technical recognition and tracking. Research reports are not publication gates.
Legacy version/hash identifiers are retained so frozen events remain comparable.
"""
from copy import deepcopy
from decimal import Decimal as D
from hashlib import sha256
import json

from engine.models import RawEvent, UserPreference, DataCapability
from engine.strategies.p33 import evaluate, VERSION, CONFIG_HASH
from engine.pd2.analysis import closed_bars, DURATIONS
from engine.strategies.p33_lifecycle import follow, freeze_plan, qualify, group_hits, VERSION as LIFECYCLE_VERSION, CONFIG_HASH as LIFECYCLE_HASH
from engine.pd2.config import ExecutionConfig
from engine.p39.timing import current_execution


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def advance(event,bars,now):
    return follow(event,bars,now)

def recognition_inputs(instrument,bars,now):
    series={tf:closed_bars(rows,now) for tf,rows in bars.items()}
    tick=instrument.spec.get('tick_size')
    from engine.strategies.p33_delivery import VERSION as DELIVERY_VERSION
    from engine.pd2.persistent_avwap import VERSION as AVWAP_VERSION
    from engine.pd2.decision import COST_POLICY
    freshness={tf:bool(rows and 0<=now-rows[-1]['close_time']<=DURATIONS[tf]+180000) for tf,rows in series.items()}
    signature=digest({'version':VERSION,'config':CONFIG_HASH,'lifecycle':LIFECYCLE_HASH,'delivery':DELIVERY_VERSION,'cost_policy':COST_POLICY,
        'avwap_version':AVWAP_VERSION,'derivative_evidence_version':'native-day-endpoints-v2',
        'tick':tick,'bars':series,'freshness':freshness,'onboard':instrument.spec.get('onboard_time')})
    return series,tick,signature

def prepare_recognition(instrument,bars,now,market):
    """Read bounded dependencies, then release the connection before recognition.

    Mutation rechecks both the input signature and the actual surviving parent;
    a changed checkpoint falls back to recognition under the existing fence.
    """
    from engine.db import Session
    from engine.strategies.p33_coverage import load
    series,tick,signature=recognition_inputs(instrument,bars,now)
    with Session() as s:
        row=s.get(UserPreference,'p33_shadow:'+instrument.id)
        previous=deepcopy(row.payload if row else {})
        cutoff=series.get('240',[])[-1]['open_time'] if series.get('240') else None
        coverage=load(s,instrument,cutoff) if cutoff and signature!=previous.get('input_signature') else None
    parents=[]
    for event in previous.get('active_events',{}).values():
        followed=advance(event,series,now)
        if followed.get('invalidated_at') is not None or followed.get('protection_observed_at') is not None:continue
        quote_fresh=market and market.get('instrument_id')==instrument.id and (market.get('quality') or {}).get('status')=='fresh' and 0<=now-market.get('as_of',0)<=90000
        if quote_fresh and market.get('price') is not None and D(str(market['price']))<=D(event.get('current_protection',event['initial']['protection'])):continue
        hit=followed['initial']
        if hit['strategy_id'] not in ('retest','early_strength','trend_pullback','failed_breakdown'):parents.append(hit)
    parent=max(parents,key=lambda v:v['trigger']['close_time'],default=None)
    if signature==previous.get('input_signature'):
        result=previous.get('result',{'results':[],'main':None,'auxiliary_hits':[]})
    else:
        try:
            if tick is None:raise ValueError('missing_tick_size')
            result=evaluate(series,now,tick,instrument.id,parent=parent,coverage=coverage)
        except (ValueError,ArithmeticError) as error:
            result={'results':[],'main':None,'auxiliary_hits':[],'error':type(error).__name__,
                'reason':'市场价格精度或输入数据无效','trading_enabled':False}
    return {'signature':signature,'previous_signature':previous.get('input_signature'),
        'parent':digest(parent),'result':result}


def inherit_protection(event,price,now):
    """A tighter group stop takes effect now, never at the child's old trigger."""
    old=D(event.get('current_protection',event['initial']['protection']))
    price=max(old,D(str(price)))
    event['current_protection']=str(price)
    if price<=old:return
    history=event.setdefault('protection_history',[])
    if history and history[-1]['observed_at']==now:history[-1]={**history[-1],'price':str(price)}
    else:history.append({'price':str(price),'observed_at':now})


def evidence_signature(session,instrument,market,now):
    """Stored evidence dependencies; no provider calls and no candle recompute."""
    from sqlalchemy import select
    from engine.models import IdentityLink,MarketState,Instrument
    asset=getattr(instrument,'asset_id',None)
    if not asset:return digest(market),None
    keys=['binance:fx:USDTUSD','binance:fx:USDCUSD']
    caps=[(r.key,r.raw_ref,r.observed_at,r.status,r.expires_at<now) for r in session.scalars(
        select(DataCapability).where(DataCapability.key.in_(keys)).order_by(DataCapability.key))]
    members=[asset]
    from engine.pd2.universe_policy import membership
    aliases,_=membership(session);canonical=aliases.get(asset,asset)
    members=[a for a,c in aliases.items() if c==canonical]
    native_states=[(r.instrument_id,r.payload.get('oi_as_of'),r.payload.get('oi_notional_quote'),
        r.payload.get('funding_settled_at')) for r in session.scalars(select(MarketState).join(Instrument)
        .where(Instrument.asset_id.in_(members),Instrument.venue.in_(['binance','binance_spot','binance_alpha'])).order_by(MarketState.instrument_id))]
    return digest(['p39-binance-only-v1',market,caps,native_states]),caps


def observe(session,instrument,bars,now,market=None,execution_config=None,prepared=None):
    """Called inside the existing scanner transaction and lock; no provider I/O."""
    key='p33_shadow:'+instrument.id
    from engine.repository import runtime_config
    delivery_config=runtime_config(session)
    checkpoint=session.get(UserPreference,key)
    previous=checkpoint.payload if checkpoint else {}
    series,tick,signature=recognition_inputs(instrument,bars,now)
    active=deepcopy(previous.get('active_events',{}));updated=False
    groups=deepcopy(previous.get('groups',{})); terminal_events={}
    for event_id,event in list(active.items()):
        next_event=advance(event,series,now)
        live_price=(market or {}).get('price')
        quote_fresh=market and market.get('instrument_id')==instrument.id and (market.get('quality') or {}).get('status')=='fresh' and 0<=now-market.get('as_of',0)<=90000
        if quote_fresh and live_price is not None and not (next_event.get('invalidated_at') or next_event.get('protection_observed_at')):
            price=D(str(live_price));stop=D(event.get('current_protection',event['initial']['protection']))
            if price<=stop:
                next_event.update(protection_observed_at=now,status='protection_touched',protection_observation_kind='fresh_quote')
            for name,target in (event.get('plan',{}).get('targets') or {}).items():
                if name!='four_hour' and target and target['known_at']<=now and price>=D(target['low']):
                    next_event.setdefault('target_first_observed',{}).setdefault(name,{'observed_at':now,'price':str(price),'observation_kind':'fresh_quote','execution_confirmed':False})
        trailing=event.get('analysis_config') or {}
        if trailing.get('trailing_mode')=='chandelier' and not (next_event.get('invalidated_at') or next_event.get('protection_observed_at')):
            from engine.pd2.analysis import trailing_protection
            old_stop=event.get('current_protection',event['initial']['protection'])
            new_stop=trailing_protection(series.get(event['initial']['timeframe'],[]),old_stop,event['initial']['protection'],now,
                                        trailing['trailing_period'],str(trailing['trailing_atr_multiple']))
            if D(new_stop)>D(old_stop):
                next_event['current_protection']=new_stop
                next_event.setdefault('protection_history',[]).append({'price':new_stop,'observed_at':now})
        if next_event!=event:
            _record(session,'p33_observation',event_id,next_event,now)
            active[event_id]=next_event;updated=True
        if next_event.get('invalidated_at') is not None or next_event.get('protection_observed_at') is not None:
            terminal_events[event_id]=active.pop(event_id)
    for group_id,group in groups.items():
        if group.get('status')=='tracking' and group.get('main_event_id') not in active:
            # A stopped primary plan cannot be rescued by switching to an auxiliary
            # template with a lower stop on the same structure.
            group.update(status='ended',ended_observed_at=now)
            _record(session,'p33_group_ended',group_id,group,now)
            for event_id in group.get('member_event_ids',[]):
                if event_id in active:
                    _record(session,'p33_auxiliary_ended',event_id,
                            {**active.pop(event_id),'status':'primary_structure_ended','observed_at':now},now)
            updated=True
    parent_events=[v['initial'] for v in active.values() if v['initial']['strategy_id'] not in ('retest','early_strength','trend_pullback','failed_breakdown')]
    parent=max(parent_events,key=lambda v:v['trigger']['close_time'],default=None)
    if prepared and prepared['signature']==signature and prepared['previous_signature']==previous.get('input_signature') and prepared['parent']==digest(parent):
        result=deepcopy(prepared['result'])
    elif signature==previous.get('input_signature'):
        result=previous.get('result',{'results':[],'main':None,'auxiliary_hits':[]})
    else:
        try:
            if tick is None:raise ValueError('missing_tick_size')
            from engine.strategies.p33_coverage import load as load_coverage
            cutoff=series.get('240',[])[-1]['open_time'] if series.get('240') else None
            coverage=load_coverage(session,instrument,cutoff) if cutoff else None
            result=evaluate(series,now,tick,instrument.id,parent=parent,coverage=coverage)
        except (ValueError,ArithmeticError) as error:
            result={'results':[],'main':None,'auxiliary_hits':[], 'error':type(error).__name__,
                    'reason':'市场价格精度或输入数据无效','trading_enabled':False}
    from engine.strategies.p35_candidates import sync_candidates
    context=None
    if tick and any(r.get('status') in ('waiting','triggered') and r.get('anchors') for r in result.get('results',[])):
        from engine.pd2.structural_history import load
        context=load(session,instrument,now,delivery_config.analysis,fallback=series)
    sync_candidates(session,instrument,result,series,market,now,delivery_config,structural_context=context)
    main=result.get('main')
    for hit in ([main] if main else [])+result.get('auxiliary_hits',[]):
        event_id=hit['event_id'];initial_id=digest(['p33_first',event_id])
        if session.get(RawEvent,initial_id) is not None:continue
        event={'initial':deepcopy(hit),'first_observed_at':now,'status':'triggered',
               'input_signature':signature,'trading_enabled':False,
               'plan':freeze_plan(hit,series,now,tick,delivery_config.analysis,context),
               'execution_config':execution_config.model_dump() if execution_config else None,
               'analysis_config':delivery_config.analysis.model_dump(),
               'protection_history':[{'price':hit['protection'],'observed_at':now}]}
        from engine.p39.pipeline import origin
        event['origin']=origin()
        from engine.strategies.p33_lifecycle import DEPENDENCIES
        event['origin']['required_inputs']=dict(DEPENDENCIES.get(hit['strategy_id'],{}))
        # Source bar references preserve the exact input revision without copying
        # an entire OHLC history for every routine no-match scan.
        manifest={tf:[{k:b.get(k) for k in ('open_time','close_time','received_at','revision','raw_ref')} for b in rows] for tf,rows in series.items()}
        session.add(RawEvent(id=initial_id,source='erji',endpoint='p33_first',received_at=now,
                            payload={'market_id':instrument.id,'event':event,'input_manifest':manifest,
                                     'main_event_id':main['event_id'] if main else None}))
        active[event_id]=event
    # Read existing native depth and FX only for active research plans. No new collection.
    book=None
    if active and market and execution_config:
        cached=session.get(DataCapability,f"{instrument.id.split(':')[0]}:execution_book:{instrument.id}")
        if cached and cached.status=='fresh' and cached.expires_at>=now:book=cached.payload
        from engine.pd2.providers import quote_usd_rate
        fx=quote_usd_rate(market.get('quote_asset'),session,now)
        market={**market,'quote_usd_rate':str(fx) if fx is not None else None}
    grouped=group_hits([event['initial'] for event in active.values() if event['initial']['strategy_id'] not in delivery_config.disabled_strategies])
    for group_id,hits in grouped.items():
        old=groups.get(group_id)
        if old is None:
            # A pruned terminal checkpoint must never resurrect an old structure.
            first_id=digest(['p33_group_first',group_id])
            if session.get(RawEvent,first_id) is not None:continue
            old={'group_id':group_id,'market_id':instrument.id,'first_observed_at':now,
                 'initial_main_event_id':hits[0]['event_id'],'versions':[], 'trading_enabled':False}
            session.add(RawEvent(id=first_id,source='erji',endpoint='p33_group_first',received_at=now,payload=deepcopy(old)))
        winner=hits[0]
        # Keep a terminal group's identity and history; it cannot reopen on a new scan.
        if old.get('status')=='ended':continue
        current_stop=max(D(active[winner['event_id']].get('current_protection',winner['protection'])),D(old.get('current_protection',winner['protection'])))
        old['current_protection']=str(current_stop)
        inherit_protection(active[winner['event_id']],current_stop,now)
        if not old['versions'] or old['versions'][-1]['main_event_id']!=winner['event_id']:
            version={'revision':len(old['versions'])+1,'observed_at':now,'main_event_id':winner['event_id'],
                     'strategy_id':winner['strategy_id'],'strategy_version':winner['strategy_version'],
                     'reason':'固定优先级；同市场、同周期、同触发时间与同一精确边界',
                     'plan':deepcopy(active[winner['event_id']].get('plan')),'current_protection':str(current_stop)}
            old['versions'].append(version)
            _record(session,'p33_group_version',group_id,{'group_id':group_id,**version},now)
        old.update(status='tracking',main_event_id=winner['event_id'],member_event_ids=[hit['event_id'] for hit in hits],
                   auxiliary_strategies=[hit['strategy_id'] for hit in hits[1:]])
        groups[group_id]=old
    for group_id,group in groups.items():
        if group.get('status')!='ended' and group_id not in grouped:
            group.update(status='ended',ended_observed_at=now)
            _record(session,'p33_group_ended',group_id,group,now)
    for event_id,event in active.items():
        config=current_execution(event['execution_config']) if event.get('execution_config') else None
        decision=qualify(event,market,book,config,now,series,event['initial'].get('parent_event_id') in active)
        before=event.get('qualification') or {}
        if (before.get('rule_action',{}).get('code'),before.get('execution',{}).get('status')) != (decision['rule_action']['code'],decision['execution']['status']):
            _record(session,'p33_qualification',event_id,{'event_id':event_id,'qualification':decision},now)
        event['qualification']=decision
    # Freeze complete event-owned evidence only when the causal input or state changes.
    # Page requests read these records; they never run recognizers or pull exchanges.
    from engine.strategies.p33_delivery import make_snapshot, store, publish
    latest_snapshots=deepcopy(previous.get('latest_snapshots',{}))
    derivative_context=None;frozen_derivatives=None
    evidence_key,_=evidence_signature(session,instrument,market,now) if active or terminal_events else (None,None)
    auxiliary_due=(evidence_key!=previous.get('evidence_signature') and now-previous.get('evidence_at',0)>=30000)
    delivered=False
    for event_id,event in {**active,**terminal_events}.items():
        if not event.get('plan'):continue
        if event_id in terminal_events:
            frozen_config=current_execution(event['execution_config']) if event.get('execution_config') else None
            event['qualification']=qualify(event,market,book,frozen_config,now,series,False)
        old=previous.get('active_events',{}).get(event_id,{})
        marker=lambda v: [v.get('checked_through'),v.get('status'),v.get('current_protection'),v.get('entry_expired_at'),
                          (v.get('qualification') or {}).get('rule_action',{}).get('code')]
        if signature==previous.get('input_signature') and marker(event)==marker(old) and event_id in latest_snapshots and not auxiliary_due and now-latest_snapshots[event_id]['as_of']<10000:continue
        overlays=event.get('event_overlays')
        if market and getattr(instrument,'asset_id',None):
            from engine.pd2.persistent_avwap import update,read_series
            state=update(session,event_id,instrument.id,event['initial']['timeframe'],event['initial']['trigger']['open_time'],now)
            start=min((b['open_time'] for b in series.get(event['initial']['timeframe'],[])),default=now)
            overlays={**(overlays or {}),'avwap':state,'avwap_series':read_series(session,state,start,now)}
        from engine.pd2.config import AnalysisConfig
        if derivative_context is None and market and 'oi_quality' in market:
            from engine.contracts import MarketDTO
            from engine.pd2.derivatives import context_for
            from engine.pd2.service import decision_derivative_evidence
            # Stored provider facts only, once per changed market; no network I/O.
            from engine.pd2.providers import valuation_for
            dto=MarketDTO.model_validate(market)
            valuation=valuation_for(dto,session,now)
            dto.circulating_cap=valuation['cap'];dto.valuation_status=valuation['status'];dto.coverage=valuation
            market=dto.model_dump(mode='json')
            derivative_context=context_for(dto,now)
            frozen_derivatives,daily_path=decision_derivative_evidence(instrument.id,derivative_context,series,now)
            derivative_context['daily_oi_path']=daily_path
        snap=make_snapshot(event,series,market,tick,now,AnalysisConfig.model_validate(event.get('analysis_config') or {}),overlays,derivative_context,frozen_derivatives)
        store(session,snap,now)
        delivered=True
        event['event_overlays']=snap['event_overlays']
        latest_snapshots[event_id]={'snapshot_id':snap['id'],'instrument_id':instrument.id,
            'event_id':event_id,'label':event['initial']['label'],'as_of':now,'status':event['status']}
        if market and getattr(instrument,'asset_id',None):
            group_id=next((key for key,g in groups.items() if g.get('main_event_id')==event_id),None)
            if group_id:publish(session,snap,event,instrument,delivery_config,now,group_id)
    latest_snapshots=dict(sorted(latest_snapshots.items(),key=lambda pair:pair[1]['as_of'],reverse=True)[:30])
    statuses={}
    for row in result.get('results',[]):statuses[row['status']]=statuses.get(row['status'],0)+1
    summary={'as_of':now,'version':VERSION,'config_hash':CONFIG_HASH,'lifecycle_version':LIFECYCLE_VERSION,
             'statuses':statuses,'active_events':len(active),'active_groups':sum(g['status']=='tracking' for g in groups.values()),
             'merged_auxiliary_hits':sum(max(0,len(v)-1) for v in grouped.values()),
             'main_strategy':main['strategy_id'] if main else None,'trading_enabled':False}
    # Durable records are immutable; bound the convenience checkpoint's terminal history.
    terminal=sorted((g for g in groups.values() if g['status']=='ended'),key=lambda g:g['ended_observed_at'],reverse=True)
    keep={g['group_id'] for g in terminal[:100]}
    groups={k:v for k,v in groups.items() if v['status']!='ended' or k in keep}
    payload={'evidence_signature':evidence_key if delivered else previous.get('evidence_signature'),
             'evidence_at':now if delivered else previous.get('evidence_at',0),'input_signature':signature,'summary':summary,'result':result,'active_events':active,'groups':groups,'latest_snapshots':latest_snapshots}
    if signature!=previous.get('input_signature') or updated or active or groups!=previous.get('groups',{}):
        session.merge(UserPreference(key=key,updated_at=now,payload=payload))
    return summary


def _record(session,kind,event_id,payload,now):
    key=digest([kind,event_id,payload])
    if session.get(RawEvent,key) is None:
        session.add(RawEvent(id=key,source='erji',endpoint=kind,received_at=now,payload=payload))
