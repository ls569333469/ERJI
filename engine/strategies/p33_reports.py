"""Read stored research summaries only. Never re-evaluate on a page request."""
from sqlalchemy import select
from engine.models import UserPreference


def summary(session):
    rows = list(session.execute(select(UserPreference.key, UserPreference.updated_at,
        UserPreference.payload['summary'].label('summary')).where(
        UserPreference.key.startswith('p33_shadow:', autoescape=True)).order_by(UserPreference.updated_at.desc())))
    current = [row for row in rows if (row.summary or {}).get('lifecycle_version')]
    return {'mode': 'technical_analysis', 'trading_enabled': False,
        'checked_markets': len(rows), 'lifecycle_markets': len(current),
        'latest_at': max((row.updated_at for row in rows), default=None),
        'active_events': sum((row.summary or {}).get('active_events', 0) for row in current),
        'active_groups': sum((row.summary or {}).get('active_groups', 0) for row in current),
        'merged_auxiliary_hits': sum((row.summary or {}).get('merged_auxiliary_hits', 0) for row in current),
        'recent': [{'instrument_id': row.key.removeprefix('p33_shadow:'), 'updated_at': row.updated_at,
                    **(row.summary or {})} for row in rows[:12]]}


def events(session, instrument=None, limit=30):
    query=select(UserPreference.payload['latest_snapshots']).where(UserPreference.key.startswith('p33_shadow:',autoescape=True))
    if instrument:query=query.where(UserPreference.key=='p33_shadow:'+instrument)
    rows=[]
    for saved in session.scalars(query):rows.extend((saved or {}).values())
    return {'rows':sorted(rows,key=lambda r:r['as_of'],reverse=True)[:limit], 'mode':'technical_analysis'}
