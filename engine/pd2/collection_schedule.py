"""Persisted per-source deadlines. Priority changes never reset first reports."""
from sqlalchemy import select, text
from engine.db import Session
from engine.models import UserPreference, CurrentSignal, Instrument, Watchlist
from engine.adapters.base import now_ms
from engine.pd2.universe_policy import membership

BATCH_SIZE = 20
INTERVALS = {'ordinary': 300000, 'candidate': 60000, 'active': 30000}


def choose_due(natives, visited, intervals, now, limit=20):
    due = [n for n in set(natives) if visited.get(n, 0) + intervals.get(n, 300000) <= now or n not in visited]
    # Overdue relative to its own interval: ordinary work cannot starve forever.
    due.sort(key=lambda n: (-(now-visited.get(n, 0))/intervals.get(n, 300000), n))
    return due[:limit]


def due(venue, natives, kind='oi', limit=BATCH_SIZE):
    now = now_ms()
    with Session.begin() as session:
        session.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':':due:'+venue+':'+kind})
        checkpoint = session.get(UserPreference, 'due:'+venue+':'+kind)
        visited = (checkpoint.payload if checkpoint else {}).get('visited', {})
        aliases, admitted = membership(session)
        levels = {}
        for r in session.scalars(select(CurrentSignal).where(CurrentSignal.action != 'ended')):
            interval = 30000 if r.action in ('participate','trial','paused','expired') else 60000
            levels[r.asset_id] = min(levels.get(r.asset_id, 300000), interval)
        for asset in session.scalars(select(Watchlist.asset_id)):
            asset = aliases.get(asset, asset); levels[asset] = min(levels.get(asset,300000), 60000)
        from engine.repository import runtime_config
        for asset in runtime_config().symbols:
            asset=aliases.get(asset,asset);levels[asset]=min(levels.get(asset,300000),60000)
        intervals = {}
        for i in session.scalars(select(Instrument).where(Instrument.venue == venue)):
            asset = aliases.get(i.asset_id, i.asset_id)
            intervals[i.asset_id if kind=='wallet_assets' else i.native_symbol] = levels.get(asset, 300000)
        old=checkpoint.payload if checkpoint else {}
        retries=dict(old.get('retries',{}))
        ready=[n for n in natives if retries.get(n,{}).get('until',0)<=now]
        chosen=choose_due(ready, visited, intervals, now, limit)
        for n in chosen:
            failures=retries.get(n,{}).get('failures',0)
            retries[n]={'until':now+min(300000,30000*2**min(failures,4)), 'failures':failures+1}
        session.merge(UserPreference(key='due:'+venue+':'+kind,payload={'visited':visited,'retries':retries},updated_at=now))
        return chosen


def acknowledge(venue, natives, kind='oi', at=None):
    at = at or now_ms()
    with Session.begin() as session:
        session.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':':due:'+venue+':'+kind})
        key = 'due:'+venue+':'+kind
        old = session.get(UserPreference, key)
        visited = dict((old.payload if old else {}).get('visited', {}))
        retries=dict((old.payload if old else {}).get('retries',{}))
        for native in natives:
            visited[native] = at; retries.pop(native,None)
        session.merge(UserPreference(key=key, payload={'visited':visited,'retries':retries}, updated_at=at))


def acknowledge_batch(batch):
    # Failed symbols retry independently. A failed whole source gets source backoff.
    symbols = {i['id']:i['native_symbol'] for i in batch.instruments}
    completed = [symbols[p['instrument_id']] for p in batch.states if p.get('oi_status')=='fresh' and p['instrument_id'] in symbols]
    if completed: acknowledge(batch.venue, completed)
    if batch.metadata.get('oi_history_seeded'):acknowledge(batch.venue,batch.metadata['oi_history_seeded'],'oi_history_seed')


def history_seeded(venue):
    with Session() as session:
        row=session.get(UserPreference,'due:'+venue+':oi_history_seed')
        return set((row.payload if row else {}).get('visited',{}))
