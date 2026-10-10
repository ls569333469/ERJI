"""Bounded read-only causal trace. Never execute analysis from an HTTP GET."""
from sqlalchemy import select
from engine.db import Session
from engine.models import AnalysisDemand,AnalysisResult,MarketAnalysisState,Episode,Campaign,Plan,SignalSnapshot,CurrentSignal,Task,Observation
from engine.adapters.base import now_ms
from engine.p39.pipeline import enabled,VERSION

def trace(iid,limit=20):
    at=now_ms();limit=max(1,min(int(limit),100))
    if not enabled():return {'as_of':at,'instrument_id':iid,'state':'not_activated','version':VERSION}
    with Session() as s:
        demands=[dict(id=d.id,origin_type=d.origin_type,source_id=d.source_id,state=d.state,
            created_at=d.created_at,updated_at=d.updated_at,expires_at=d.expires_at,payload=d.payload)
            for d in s.scalars(select(AnalysisDemand).where(AnalysisDemand.instrument_id==iid)
                .order_by(AnalysisDemand.updated_at.desc()).limit(limit))]
        states=[dict(work_kind=r.work_kind,input_version=r.input_version,result_id=r.result_id,
            analysis_status=r.analysis_status,strategy_result=r.strategy_result,last_attempt_at=r.last_attempt_at,
            last_success_at=r.last_success_at,next_check_at=r.next_check_at,payload=r.payload)
            for r in s.scalars(select(MarketAnalysisState).where(MarketAnalysisState.instrument_id==iid))]
        results=[dict(id=r.id,work_kind=r.work_kind,input_version=r.input_version,computed_at=r.computed_at,payload=r.payload)
            for r in s.scalars(select(AnalysisResult).where(AnalysisResult.instrument_id==iid)
                .order_by(AnalysisResult.computed_at.desc(),AnalysisResult.id).limit(limit))]
        episodes=[dict(id=ident,phase=phase,strategy_id=strategy,origin=origin or {'origin_type':'legacy_unlinked'})
            for ident,phase,strategy,origin in s.execute(select(Episode.id,Episode.phase,Episode.strategy_id,Episode.evidence['origin'])
                .join(Campaign).where(Campaign.reference_instrument_id==iid).order_by(Campaign.first_seen_at.desc(),Episode.id).limit(limit))]
        plans=[dict(id=p.id,episode_id=p.episode_id,revision=p.revision,expires_at=p.expires_at,
            origin=p.payload.get('origin') or {'origin_type':'legacy_unlinked'})
            for p in s.scalars(select(Plan).where(Plan.execution_instrument_id==iid).order_by(Plan.expires_at.desc()).limit(limit))]
        ids=[p['id'] for p in plans]
        from engine.p38.plan_queries import latest_ids
        snapshots=[dict(id=r.id,plan_id=r.plan_id,as_of=r.as_of,action=r.payload.get('action'),
            valid_until=r.payload.get('valid_until'),
            origin=r.payload.get('origin') or r.payload.get('evidence',{}).get('origin') or {'origin_type':'legacy_unlinked'})
            for r in s.scalars(select(SignalSnapshot).where(SignalSnapshot.plan_id.in_(ids),SignalSnapshot.id.in_(latest_ids([iid]))))]
        projections=[dict(id=r.id,snapshot_id=r.payload.get('snapshot_id'),action=r.action,
            computed_at=r.computed_at,valid_until=r.valid_until,valid_now=r.valid_until>at,
            decision_to_projection_ms=r.computed_at-r.payload['decision_as_of'] if isinstance(r.payload.get('decision_as_of'),int) else None,
            published_before_expiry=r.computed_at<r.valid_until,
            origin=r.payload.get('origin') or {'origin_type':'legacy_unlinked'})
            for r in s.scalars(select(CurrentSignal).where(CurrentSignal.instrument_id==iid).order_by(CurrentSignal.computed_at.desc()).limit(limit))]
        tasks=[dict(id=r.id,kind=r.kind,state=r.state,available_at=r.available_at,lease_until=r.lease_until,
            timings={k:r.payload.get(k) for k in ('first_pending_at','latest_desired_at','submitted_at','started_at','finished_at','queue_wait_ms','available_wait_ms','desired_wait_ms','execution_ms','deferred_reason','deferred_count','failed_count','projection_cycle','error')},
            attempts=r.attempts,currently_deferred=r.state=='queued' and bool(r.payload.get('deferred_reason')),
            pending_age_ms=max(0,at-r.payload.get('first_pending_at',r.available_at)) if r.state in ('queued','failed') else None)
            for r in s.scalars(select(Task).where(Task.payload['subject'].as_string()==iid,Task.kind.like('p39_%')).limit(limit))]
        observations=[dict(id=r.id,instrument_id=r.instrument_id,visible_at=r.visible_at,source_at=r.payload.get('source_at'),
            event_id=r.payload.get('trigger_event_id'),persisted_at=r.payload.get('persisted_at'),status=r.status)
            for r in s.scalars(select(Observation).where(Observation.instrument_id==iid).order_by(Observation.last_at.desc()).limit(limit))]
    timings=[]
    for r in results:
        origin=r['payload'].get('origin') or {}
        for ref in origin.get('sources',[]):
            source=ref.get('source') or {};stamp=source.get('source_at')
            timings.append({'analysis_result_id':r['id'],'demand_id':ref['demand_id'],
                'event_id':source.get('trigger_event_id'),'analysis_market_id':origin.get('analysis_market_id'),
                'source_at':stamp,'demand_at':ref.get('created_at'),'analysis_started_at':r['payload'].get('started_at'),
                'analysis_finished_at':r['payload'].get('finished_at'),
                'source_to_analysis_ms':r['payload'].get('finished_at')-stamp if isinstance(stamp,int) and r['payload'].get('finished_at') else None})
    return dict(as_of=at,instrument_id=iid,state='available',version=VERSION,demands=demands,analyses=states,
        results=results,episodes=episodes,plans=plans,snapshots=snapshots,projections=projections,
        observations=observations,tasks=tasks,event_timings=timings,limit=limit,
        scope='exact native instrument and explicit origin; projection timestamps do not prove browser delivery')
