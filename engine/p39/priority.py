"""Native-market priority shared by discovery, OI and history collectors."""
from sqlalchemy import select
from engine.models import AnalysisDemand, Observation, UserPreference


def market_tiers(session, at):
    from engine.p39.pipeline import live_filter
    from engine.p38.plan_queries import live_plans
    # Queued and waiting-input observations remain T2, regardless of the
    # compute selector's capacity. A plain watchlist is not a trigger.
    t2 = set(session.scalars(select(AnalysisDemand.instrument_id).join(
        Observation, Observation.id == AnalysisDemand.source_id).where(
        live_filter(at), AnalysisDemand.origin_type == 'observation',
        Observation.instrument_id == AnalysisDemand.instrument_id,
        Observation.visible_at.is_not(None), Observation.status != 'archived',
        Observation.payload['manual'].as_boolean().is_not(True))))
    t3 = {row.execution_instrument_id for row in session.execute(live_plans(at, entry=True))}
    portfolio = session.get(UserPreference, 'research_portfolio')
    positions = {p['instrument_id'] for p in (portfolio.payload if portfolio else {}).get('positions', [])
                 if p.get('state') == 'open' and p.get('instrument_id')}
    t3.update(positions)
    return t2 - t3, t3, positions


def hot_markets(at):
    from engine.db import Session
    with Session() as session:
        t2, t3, _ = market_tiers(session, at)
        return t2 | t3
