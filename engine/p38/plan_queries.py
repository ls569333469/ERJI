"""Plan-scoped current reads. Never filter lifetime snapshot JSON by market."""
from sqlalchemy import select,func
from engine.models import Plan,Episode,SignalSnapshot

def latest_ids(instrument_ids=None):
    query=select(SignalSnapshot.id).join(Plan,SignalSnapshot.plan_id==Plan.id)
    if instrument_ids is not None:query=query.where(Plan.execution_instrument_id.in_(instrument_ids))
    return query.distinct(SignalSnapshot.plan_id).order_by(SignalSnapshot.plan_id,SignalSnapshot.as_of.desc(),SignalSnapshot.id.desc())

def live_plans(now,entry=False,instrument_ids=None):
    latest=select(SignalSnapshot.payload['action']['code'].as_string()).where(
        SignalSnapshot.plan_id==Plan.id).order_by(SignalSnapshot.as_of.desc(),SignalSnapshot.id.desc()).limit(1).scalar_subquery()
    query=select(Plan.id,Plan.execution_instrument_id,Plan.revision,Plan.expires_at).join(Episode).where(
        Episode.phase.notin_(['ended','invalidated']),func.coalesce(latest,'')!='ended')
    if entry:query=query.where(Plan.expires_at>now)
    if instrument_ids is not None:query=query.where(Plan.execution_instrument_id.in_(instrument_ids))
    return query
