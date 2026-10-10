"""Database-only stage funnel, separated from the user-facing signal contract."""
from sqlalchemy import select,func,cast,BigInteger
from engine.db import Session
from engine.models import Observation,Plan,CurrentSignal,MarketState,Candle,Task,WorkerHeartbeat
from engine.adapters.base import now_ms

def stages(transport=None):
    now=now_ms()
    with Session() as s:
        health=s.get(WorkerHeartbeat,'anomaly_discovery')
        observed=dict(s.execute(select(Observation.status,func.count()).group_by(Observation.status)).all())
        analysis_status=Observation.payload['analysis_status'].as_string()
        analyses=dict(s.execute(select(analysis_status,func.count()).where(Observation.visible_at.is_not(None),Observation.status!='archived').group_by(analysis_status)).all())
        from engine.p38.jobs import KINDS
        tasks=dict(s.execute(select(Task.state,func.count()).where(Task.kind.in_(KINDS.values())).group_by(Task.state)).all())
        clocks={'quote_source_at':s.scalar(select(func.max(MarketState.as_of))),
                'closed_candle_received_at':s.scalar(select(func.max(Candle.received_at)).where(Candle.is_closed.is_(True))),
                'observation_analyzed_at':s.scalar(select(func.max(cast(Observation.payload['analyzed_at'].as_string(),BigInteger)))),
                'projection_at':s.scalar(select(func.max(CurrentSignal.computed_at)))}
        plans=s.scalar(select(func.count()).select_from(Plan)) or 0
        projection=s.scalar(select(func.count()).select_from(CurrentSignal)) or 0
    from engine.p40.diagnostics import current_run,run_scope
    run=current_run();scope=run_scope(health.details,run) if health else None
    discovery={'state':health.state,'updated_at':health.updated_at,'details':health.details,'run_scope':scope} if health and scope!='previous_run' else {'state':'initializing','run_scope':scope}
    from engine.p39.pipeline import enabled,capacity_metrics
    demands=capacity_metrics(now) if enabled() else {'state':'not_activated'}
    return {'as_of':now,'run_id':run,'transport':transport or {},'discovery':discovery,'observations':observed,'analysis':analyses,'analysis_demands':demands,'jobs':tasks,'plans':plans,'projected':projection,'stage_clocks':clocks,'scope':'local stored facts; counts are stages, not trade permission'}
