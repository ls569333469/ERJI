"""Persisted per-source deadlines. Priority changes never reset first reports."""
from sqlalchemy import select, text, func
from engine.db import Session
from engine.models import UserPreference, CurrentSignal, Instrument
from engine.adapters.base import now_ms
from engine.pd2.universe_policy import membership
from engine.p39.timing import ORDINARY_MS,HOT_MS,due_at,scheduled_at,COLLECTION_LEASE_MS

BATCH_SIZE = 20
OI_HISTORY_SEED = 'oi_history_seed_72h_v2'
INTERVALS = {'ordinary': 300000, 'candidate': 60000, 'active': 30000}
OI_INTERVALS = {'ordinary': ORDINARY_MS, 'candidate': HOT_MS, 'active': HOT_MS}



def collection_interval(kind, tier='ordinary', venue='binance'):
    hot=tier in ('t2','t3')
    if kind=='oi' and venue=='binance':return OI_INTERVALS['candidate' if hot else 'ordinary']
    if kind=='oi_history':return 300000 if hot else 21600000
    if kind=='funding_history':return 300000 if hot else 1800000
    if kind=='volume':return ORDINARY_MS
    return INTERVALS['active'] if tier=='t3' else INTERVALS['candidate'] if tier=='t2' else ORDINARY_MS


def choose_due(natives, visited, intervals, now, limit=20, enqueued=None, aligned=False):
    due = [n for n in set(natives) if aligned or visited.get(n, 0) + intervals.get(n, 300000) <= now or n not in visited]
    # New symbols start one interval overdue, not overdue since the Unix epoch.
    # Persisted enqueue time lets cold work age fairly alongside active markets.
    enqueued=enqueued or {}
    due.sort(key=lambda n: (-(now-visited.get(n,enqueued.get(n,now)-intervals.get(n,300000)))/intervals.get(n, 300000), n))
    return due[:limit]


def signal_interval(action, managed=False, current=True):
    if managed:return INTERVALS['active']
    if action in ('participate','trial') and current:return INTERVALS['active']
    if action in ('waiting','no_chase') and current:return INTERVALS['candidate']
    return None


def choose_with_repair(natives, visited, intervals, now, limit, enqueued, repair,aligned=False,turn=1):
    """Reserve three quarters for candidate repair, the remainder for fair rotation.

    Repair never promotes a signal, extends validity or changes request budgets.
    It only prevents a stale candidate losing the data needed to re-evaluate it.
    """
    ordered=choose_due(natives,visited,intervals,now,len(natives),enqueued,aligned)
    repair=set(repair)
    hot=sorted((n for n in ordered if n in repair),key=lambda n:(visited.get(n,enqueued.get(n,now)),n))
    ordinary=[n for n in ordered if n not in repair]
    chosen=[]
    for slot in range(limit):
        preferred,other=(ordinary,hot) if (turn+slot)%4==0 else (hot,ordinary)
        queue=preferred or other
        if not queue:break
        chosen.append(queue.pop(0))
    return chosen


_priority_cache={}

def priority_snapshot(venue,kind,now):
    # Full membership/plans/catalog reads happen outside the short claim lock.
    key=(id(Session),venue,kind)
    cached=_priority_cache.get(key)
    from engine.p39.pipeline import enabled
    layered=enabled()
    with Session() as session:
        from engine.p39.priority import market_tiers
        t2,t3,position_ids=market_tiers(session,now) if layered else (set(),set(),set())
        # Selector heartbeats are not source changes. Expiration still gets a
        # bounded refresh even when no persisted timestamp changes.
        generation=(frozenset(t2),frozenset(t3),frozenset(position_ids))
        if cached and 0<=now-cached[0]<30000 and generation==cached[2]:return cached[1]
        aliases, admitted = membership(session)
        levels = {}
        native_levels={iid:INTERVALS['candidate'] for iid in t2}
        native_levels.update({iid:INTERVALS['active'] for iid in t3})
        # The real persisted Plan/Episode and declared portfolio determine
        # ongoing management, never invented flags or entry-quote valid_until.
        managed=set(session.scalars(select(Instrument.asset_id).where(Instrument.id.in_(t3))))
        signals=list(session.scalars(select(CurrentSignal).where(CurrentSignal.instrument_id.in_(t2|t3),CurrentSignal.action != 'ended')))
        intervals = {}
        catalog=list(session.scalars(select(Instrument).where(Instrument.venue == venue)))
        for i in catalog:
            asset = aliases.get(i.asset_id, i.asset_id)
            interval=native_levels.get(i.id,300000)
            if i.id in native_levels:levels[asset]=min(levels.get(asset,300000),interval)
            interval=collection_interval(kind,'t3' if i.id in t3 else 't2' if i.id in t2 else 'ordinary',venue)
            intervals[i.asset_id if kind=='wallet_assets' else i.native_symbol]=interval
        result=(aliases,levels,catalog,managed,position_ids,signals,intervals)
    _priority_cache[key]=(now,result,generation)
    return result


def due(venue, natives, kind='oi', limit=BATCH_SIZE, lease_ms=0, owner=None):
    if limit<=0:return []
    now = now_ms()
    aliases,levels,catalog,managed,position_ids,signals,intervals=priority_snapshot(venue,kind,now)
    with Session.begin() as session:
        session.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':':due:'+venue+':'+kind})
        checkpoint = session.get(UserPreference, 'due:'+venue+':'+kind)
        visited = (checkpoint.payload if checkpoint else {}).get('visited', {})
        old=checkpoint.payload if checkpoint else {}
        retries=dict(old.get('retries',{}))
        inflight=dict(old.get('inflight',{}))
        enqueued=dict(old.get('enqueued',{}))
        for n in natives:
            if n not in visited:enqueued.setdefault(n,now)
        ready=[n for n in natives if retries.get(n,{}).get('until',0)<=now and inflight.get(n,{}).get('until',0)<=now]
        if venue=='binance' and kind=='oi':
            ready=[n for n in ready if due_at(n,visited.get(n,0),now,intervals.get(n,ORDINARY_MS))
                or intervals.get(n,ORDINARY_MS)<old.get('intervals',{}).get(n,ORDINARY_MS)]
        repair=set()
        if kind in ('price','oi','oi_history','funding_history'):
            repair={i.native_symbol for i in catalog if intervals.get(i.native_symbol,ORDINARY_MS)<ORDINARY_MS}
        if kind=='candles':
            # A fresh quote can coexist with an expired structure. Use the last
            # qualified waiting structure as a repair hint, never as a buy signal.
            candidates={r.instrument_id for r in signals if
                r.payload.get('opportunity_eligible') and r.action in ('waiting','no_chase')}
            candidates.update(i.id for i in catalog if intervals.get(i.native_symbol,ORDINARY_MS)<ORDINARY_MS or i.id in position_ids)
            from engine.models import Candle
            latest={(iid,tf):at for iid,tf,at in session.execute(
                select(Candle.instrument_id,Candle.resolution,func.max(Candle.close_time)).where(
                    Candle.instrument_id.in_(candidates),Candle.resolution.in_(('60','240')),
                    Candle.is_closed.is_(True),Candle.close_time<=now)
                .group_by(Candle.instrument_id,Candle.resolution))} if candidates else {}
            repair={i.native_symbol for i in catalog if i.id in candidates and any(
                latest.get((i.id,tf),0)<now//step*step for tf,step in (('60',3600000),('240',14400000)))}
        aligned=venue=='binance' and kind=='oi'
        chosen=choose_with_repair(ready,visited,intervals,now,limit,enqueued,repair,aligned,old.get('fair_turn',1)) if repair else choose_due(ready, visited, intervals, now, limit,enqueued,aligned)
        for n in chosen:
            if owner:
                inflight[n]={'owner':owner,'until':now+(lease_ms or COLLECTION_LEASE_MS)}
            else:
                failures=retries.get(n,{}).get('failures',0)
                retries[n]={'until':now+max(lease_ms,min(300000,30000*2**min(failures,4))), 'failures':failures+1}
        session.merge(UserPreference(key='due:'+venue+':'+kind,payload={'visited':visited,'retries':retries,'enqueued':enqueued,'inflight':inflight,'intervals':intervals,'fair_turn':old.get('fair_turn',1)+len(chosen)},updated_at=now))
        return chosen


def acknowledge(venue, natives, kind='oi', at=None, owner=None):
    at = at or now_ms()
    with Session.begin() as session:
        session.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':':due:'+venue+':'+kind})
        key = 'due:'+venue+':'+kind
        old = session.get(UserPreference, key)
        visited = dict((old.payload if old else {}).get('visited', {}))
        retries=dict((old.payload if old else {}).get('retries',{}))
        enqueued=dict((old.payload if old else {}).get('enqueued',{}))
        inflight=dict((old.payload if old else {}).get('inflight',{}))
        for native in natives:
            if owner is not None and inflight.get(native,{}).get('owner')!=owner:continue
            if owner is None and inflight.get(native):continue
            visited[native] = at; retries.pop(native,None);enqueued.pop(native,None)
            inflight.pop(native,None)
        session.merge(UserPreference(key=key, payload={**(old.payload if old else {}),'visited':visited,'retries':retries,'enqueued':enqueued,'inflight':inflight}, updated_at=at))


def update_claims(venue,natives,owner,failed=False,at=None):
    at=now_ms() if at is None else at
    key='due:'+venue+':oi'
    with Session.begin() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':':due:'+venue+':oi'})
        row=s.get(UserPreference,key)
        if not row:return
        p=dict(row.payload);leases=dict(p.get('inflight',{}));retries=dict(p.get('retries',{}))
        for native in natives:
            if leases.get(native,{}).get('owner')!=owner:continue
            if failed:
                leases.pop(native,None);count=retries.get(native,{}).get('failures',0)+1
                retries[native]={'failures':count,'until':at+min(300000,30000*2**min(count-1,4))}
            else:leases[native]={**leases[native],'until':at+COLLECTION_LEASE_MS}
        row.payload={**p,'inflight':leases,'retries':retries};row.updated_at=at


def acknowledge_batch(batch):
    # Failed symbols retry independently. A failed whole source gets source backoff.
    symbols = {**{i['id']:i['native_symbol'] for i in batch.instruments},**batch.metadata.get('collection_symbols',{})}
    completed = [symbols[p['instrument_id']] for p in batch.states if p.get('oi_status')=='fresh' and p['instrument_id'] in symbols]
    claims=batch.metadata.get('collection_claims',{})
    for owner in set(claims.values()):
        owned=[n for n in completed if claims.get(n)==owner]
        if owned:acknowledge(batch.venue,owned,owner=owner)
        failed=[symbols[p['instrument_id']] for p in batch.states if p.get('oi_status') not in ('fresh',None) and p['instrument_id'] in symbols and claims.get(symbols[p['instrument_id']])==owner]
        if failed:update_claims(batch.venue,failed,owner,failed=True)
    legacy=[n for n in completed if n not in claims]
    if legacy:acknowledge(batch.venue,legacy)
    if batch.metadata.get('oi_history_seeded'):acknowledge(batch.venue,batch.metadata['oi_history_seeded'],OI_HISTORY_SEED)


def history_seeded(venue):
    with Session() as session:
        row=session.get(UserPreference,'due:'+venue+':'+OI_HISTORY_SEED)
        return set((row.payload if row else {}).get('visited',{}))


def next_delay(venue,kind):
    """Wake for persisted retry/due times; backlog drains without a 30s pause."""
    now=now_ms()
    with Session() as s:
        row=s.get(UserPreference,'due:'+venue+':'+kind)
        if not row:return 1
        p=row.payload
    retries=p.get('retries',{})
    waits=[max(0,retries.get(n,{}).get('until',0)-now) for n in p.get('enqueued',{})]
    if waits:return max(.1,min(5,min(waits)/1000))
    # Periodic short wake notices new symbols/priority changes without reloading
    # the full catalog on a tight loop when there is no overdue work.
    return 5
