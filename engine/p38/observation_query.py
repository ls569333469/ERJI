from engine.p39.timing import QUOTE_MS,BOOK_MS,REFERENCE_MS,REVIEW_MS,WAIT_REVIEW_MS
"""Asset-level incremental queries; archived payloads are loaded only by page."""
import time
from datetime import datetime,timezone,timedelta
from sqlalchemy import select,func,cast,BigInteger,or_,and_
from engine.db import Session
from engine.models import Observation,MarketState,StreamEvent,WorkerHeartbeat,Instrument,CurrentSignal,UserPreference,Task
from engine.features.numeric import change

def scope(archived,pinned):
    filters=[Observation.visible_at.is_not(None),Observation.status=='archived' if archived else Observation.status!='archived']
    if pinned:filters.append(Observation.payload['pinned'].as_boolean().is_(True))
    return filters

def actionable(signal,state,now):
    from decimal import Decimal as D,InvalidOperation
    if not signal or not state or signal.action not in ('trial','participate') or not 0<=now-state.as_of<=QUOTE_MS or signal.valid_until<=now:return False
    zone=signal.payload.get('entry_zone') or [];stop=signal.payload.get('protection')
    if len(zone)!=2 or stop is None:return False
    try:
        price=D(str(state.payload.get('price')))
        return price>0 and D(str(zone[0]))<=price<=D(str(zone[1])) and price>D(str(stop))
    except (InvalidOperation,TypeError):return False

def clock_fields(row,state,now,signals=()):
    row=dict(row);row.pop('analysis_signature',None)
    row['classification_status']='unconfirmed' if row.get('classification')=='unknown' else 'classified'
    row['classification_version']=(row.get('classification_context') or {}).get('version') or row.get('rule_version')
    if row.get('instrument_id','').startswith(('binance_spot:','binance_alpha:')):row['oi_status']='not_applicable'
    elif state:
        from engine.pd2.current_market_fields import oi_clock
        row['oi_status']=oi_clock(state.payload,now)
        row['oi_as_of']=state.payload.get('oi_as_of')
    row.update(discovered_at=row['visible_at'],published_at=row['version'],observation_status=row['status'],next_task='补充缺少的周期' if row.get('missing_timeframes') else '持续观察')
    snapshot=row.get('tracked_at');row['snapshot_at']=snapshot;row['snapshot_age_ms']=now-snapshot if snapshot is not None else None
    tier=row.get('tracking_interval_ms') or (300000 if row['status']=='cooling' else 30000)
    row['tracking_interval_ms']=tier;row['tracking_tier']=row.get('tracking_tier') or row['status']
    row['snapshot_status']='sampled' if snapshot is not None and 0<=now-snapshot<=tier*2 else 'waiting'
    if state and state.payload.get('price') and 0<=now-state.as_of<=QUOTE_MS and (state.as_of>=(row.get('price_at') or 0) or (row.get('price_at') or 0)>now):
        row.update(price=str(state.payload['price']),price_at=state.as_of,price_source='market_state:'+state.payload.get('price_kind','unknown'))
        row['since_first_pct']=change(row['price'],row.get('visible_price'))
    at=row.get('price_at');fresh=at is not None and 0<=now-at<=QUOTE_MS and (state is None or 0<=now-state.as_of<=QUOTE_MS)
    row['source_age_ms']=now-at if at is not None else None
    row['data_status']='fresh' if fresh else 'delayed'
    # A display refresh cannot confer permission from a stale decision snapshot.
    row['buy_eligible']=bool(fresh and row['status']!='archived' and any(actionable(signal,state,now) for signal in signals))
    checked=row.get('analysis_checked_at') or row.get('analyzed_at')
    row['task_status']='not_analyzed' if not row.get('analyzed_at') else row.get('analysis_status','unknown')
    return row

def materialize(row):
    return dict(row.payload,id=row.id,instrument_id=row.instrument_id,asset_id=row.asset_id,direction=row.direction,first_at=row.first_at,visible_at=row.visible_at,last_at=row.last_at,status=row.status,version=row.version)

def read_observations(archived=False,pinned=False,since=None,offset=0,limit=200,requested_assets=None):
    now=int(time.time()*1000);filters=scope(archived,pinned)
    with Session() as s:
        # Scalar membership/counts are independent of loading historical JSON.
        events=StreamEvent.kind.in_(['observation_update','market_update'])
        cursor=s.scalar(select(func.max(StreamEvent.id)).where(events)) or 0
        oldest=s.scalar(select(func.min(StreamEvent.id)).where(events)) or 0
        incremental=since is not None and 0<=since<=cursor and (since==0 or not oldest or since>=oldest-1)
        assets=list(s.scalars(select(Observation.asset_id).where(*filters).group_by(Observation.asset_id).order_by(func.max(Observation.last_at).desc(),Observation.asset_id)))
        targets=assets[offset:offset+limit];next_offset=offset+limit if offset+limit<len(assets) else None
        if requested_assets is not None:
            targets=[a for a in requested_assets if a in assets];next_offset=None
        if incremental:
            ids=set();markets=set();changed_assets=set()
            for payload in s.scalars(select(StreamEvent.payload).where(events,StreamEvent.id>since,StreamEvent.id<=cursor)):
                ids.update(payload.get('ids',[]));markets.update(payload.get('instruments',[]))
                changed_assets.update(payload.get('asset_ids',[]))
                if payload.get('asset_id'):changed_assets.add(payload['asset_id'])
            targets=set(s.scalars(select(Observation.asset_id).where(or_(Observation.id.in_(ids),Observation.instrument_id.in_(markets)))))
            targets.update(changed_assets)
            # Compatibility: old deletion events might contain only the removed ID.
            known=s.scalar(select(func.count()).select_from(Observation).where(Observation.id.in_(ids))) or 0
            if known<len(ids):targets.update(assets)
            # Time alone can invalidate a formerly fresh source/decision.
            at=func.coalesce(cast(Observation.payload['price_at'].as_string(),BigInteger),0)
            stale=or_(at<now-QUOTE_MS,at>now)
            targets.update(s.scalars(select(Observation.asset_id).where(*filters,Observation.payload['data_status'].as_string()=='fresh',stale)))
            targets=list(targets.intersection(assets));next_offset=None
        from sqlalchemy import case
        ordering=(case((Observation.payload['pinned'].as_boolean().is_(True),0),else_=1),case((Observation.payload['trade_status'].as_string()=='active',0),else_=1),Observation.last_at.desc(),Observation.visible_at.desc(),Observation.id)
        ranked=select(Observation.id,func.row_number().over(partition_by=Observation.asset_id,order_by=ordering).label('rn'),func.row_number().over(partition_by=(Observation.asset_id,Observation.instrument_id,Observation.direction),order_by=ordering).label('native_rn')).where(*filters,Observation.asset_id.in_(targets)).subquery()
        # Recent detail is bounded; always retain the current representative of
        # each native market/direction so another market's history cannot hide it.
        rows=list(s.scalars(select(Observation).join(ranked,Observation.id==ranked.c.id).where(or_(ranked.c.rn<=20,ranked.c.native_rn==1)).order_by(Observation.last_at.desc(),Observation.visible_at.desc(),Observation.id)))
        states={m.instrument_id:m for m in s.scalars(select(MarketState).where(MarketState.instrument_id.in_({r.instrument_id for r in rows})))}
        instruments={i.id:i for i in s.scalars(select(Instrument).where(Instrument.id.in_({r.instrument_id for r in rows})))}
        signals={}
        for signal in s.scalars(select(CurrentSignal).where(CurrentSignal.instrument_id.in_(states),CurrentSignal.action.in_(['trial','participate']),CurrentSignal.valid_until>now)):
            signals.setdefault(signal.instrument_id,[]).append(signal)
        active=s.get(UserPreference,'p39_structure_active') or s.get(UserPreference,'p37_active');selected=set((active.payload if active else {}).get('instrument_ids',[]))
        from engine.p38.jobs import KINDS
        structure_tasks={p['subject']:(state,p) for state,p in s.execute(select(Task.state,Task.payload).where(Task.kind==KINDS['structure'],Task.payload['shadow'].as_boolean().is_not(True),Task.payload['subject'].as_string().in_(states)))}
        from engine.p39.pipeline import enabled,live_filter
        layered=enabled();demands={};analyses={}
        if layered:
            from engine.models import AnalysisDemand,MarketAnalysisState
            for d in s.scalars(select(AnalysisDemand).where(AnalysisDemand.instrument_id.in_(instruments),live_filter(now))):
                demands.setdefault(d.instrument_id,[]).append(d)
            analyses={r.instrument_id:r for r in s.scalars(select(MarketAnalysisState).where(
                MarketAnalysisState.work_kind=='structure',MarketAnalysisState.instrument_id.in_(instruments)))}
        groups={}
        display=[clock_fields(materialize(row),states.get(row.instrument_id),now,signals.get(row.instrument_id,())) for row in rows]
        if not archived:
            from engine.pd2.current_market_fields import enrich
            enrich(s,display,now)
        for row in display:groups.setdefault(row['asset_id'],[]).append(row)
        from engine.p37.selection import representative_key
        by_id={r.id:r for r in rows}
        for group in groups.values():
            group.sort(key=lambda r:representative_key(by_id[r['id']]))
            for row in group:
                from engine.asset_classification import classification
                inst=instruments.get(row['instrument_id'])
                row.update(classification(row['instrument_id'],row['asset_id'],inst.spec if inst else None))
                state,p=structure_tasks.get(row['instrument_id'],('not_submitted',{}))
                row['structure_checked_at']=row.get('structure_checked_at')
                row['structure_task_status']='waiting_inputs' if row.get('structure_next_retry_at',0)>now else 'queued_for_admission' if row['instrument_id'] not in selected else state
                if layered:
                    ds=demands.get(row['instrument_id'],[]);analysis=analyses.get(row['instrument_id'])
                    row.update(has_analysis_demand=bool(ds),demand_ids=[d.id for d in ds],demand_origins=sorted({d.origin_type for d in ds}),
                        pipeline_tiers=['T1']+(['T2'] if ds else [])+(['T3'] if row.get('plans') else []))
                    if analysis:
                        row.update(analysis_execution_status=analysis.analysis_status,analysis_status=analysis.analysis_status,task_status=analysis.analysis_status,strategy_result=analysis.strategy_result,
                            analysis_result_id=analysis.result_id,analysis_input_version=analysis.input_version,
                            analysis_computed_at=analysis.payload.get('summary',{}).get('computed_at') or analysis.last_success_at,
                            analysis_checked_at=analysis.last_success_at)
                        if p.get('desired_version') and p['desired_version']!=analysis.input_version:
                            row.update(analysis_status='input_changed',task_status='queued',analysis_execution_status='input_changed')
                    if not ds:row['structure_task_status']='inactive'
                    elif analysis and analysis.analysis_status=='waiting_inputs' and analysis.next_check_at>now:row['structure_task_status']='waiting_inputs'
                    elif analysis and analysis.analysis_status=='completed' and state=='complete':row['structure_task_status']='completed'
                from engine.pd2.universe_policy import primary_eligible
                if inst and not primary_eligible(inst):
                    row['structure_task_status']='not_applicable';row['structure_check_status']='market_not_eligible'
                row['structure_error']=p.get('error') if state=='failed' else None
                row['analysis_instrument_id']=row['instrument_id']
        primary=[]
        for asset in targets:
            if asset in groups:
                row=dict(groups[asset][0]);row['related_events']=groups[asset][1:];primary.append(row)
        day=int(datetime.fromtimestamp(now/1000,timezone(timedelta(hours=8))).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()*1000)
        price_at=func.coalesce(cast(Observation.payload['price_at'].as_string(),BigInteger),0)
        tracked=func.coalesce(cast(Observation.payload['tracked_at'].as_string(),BigInteger),0)
        own_fresh=and_(price_at>=now-QUOTE_MS,price_at<=now)
        state_fresh=and_(MarketState.as_of>=now-QUOTE_MS,MarketState.as_of<=now,MarketState.payload['price'].as_string().is_not(None))
        current_clock=or_(MarketState.instrument_id.is_(None),and_(MarketState.as_of>=now-QUOTE_MS,MarketState.as_of<=now))
        def count(condition):
            return s.scalar(select(func.count(func.distinct(Observation.asset_id))).outerjoin(MarketState,Observation.instrument_id==MarketState.instrument_id).where(*filters,condition)) or 0
        buy_assets=set()
        candidates=s.execute(select(Observation.asset_id,MarketState,CurrentSignal).join(MarketState,Observation.instrument_id==MarketState.instrument_id).join(CurrentSignal,CurrentSignal.instrument_id==Observation.instrument_id).where(*filters,Observation.status!='archived',state_fresh,CurrentSignal.action.in_(['trial','participate']),CurrentSignal.valid_until>now))
        for asset,state,signal in candidates:
            if actionable(signal,state,now):buy_assets.add(asset)
        counts=dict(today=count(Observation.visible_at>=day),focused=count(Observation.status=='focused'),delayed=count(~and_(or_(own_fresh,func.coalesce(state_fresh,False)),current_clock)),active=len(buy_assets))
        health=s.get(WorkerHeartbeat,'anomaly_discovery')
        discovery={'state':health.state,'updated_at':health.updated_at,'details':health.details} if health else {'state':'initializing','updated_at':None}
    return dict(as_of=now,rows=primary,unique_assets=len(assets),discovery=discovery,counts=counts,cursor=cursor,incremental=incremental,asset_ids=assets,next_offset=next_offset)

def history(asset,offset=0,limit=50):
    now=int(time.time()*1000)
    with Session() as s:
        condition=(Observation.asset_id==asset)&Observation.visible_at.is_not(None)
        total=s.scalar(select(func.count()).select_from(Observation).where(condition)) or 0
        rows=list(s.scalars(select(Observation).where(condition).order_by(Observation.last_at.desc(),Observation.id).offset(offset).limit(limit)))
    return {'rows':[clock_fields(materialize(r),None,now) for r in rows],'total':total,'next_offset':offset+limit if offset+limit<total else None}
