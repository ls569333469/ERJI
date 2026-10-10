"""One lease per native instrument/timeframe across all REST candle producers.

Closed WS arrivals invalidate demand on admission by consulting actual coverage.
Leases only cover bounded pages; no network operation retains a DB transaction.
"""
from hashlib import sha256
from uuid import uuid4
from sqlalchemy import select,text
from engine.db import Session
from engine.models import Task,Candle,Instrument
from engine.adapters.base import now_ms

STEPS={'5':300000,'15':900000,'30':1800000,'60':3600000,'240':14400000,'1D':86400000,'1W':604800000}

def boundary(value,tf,ceil=False):
    step=STEPS[tf];offset=345600000 if tf=='1W' else 0
    return ((value-offset+step-1 if ceil else value-offset)//step)*step+offset
PRIORITY={'live':0,'baseline':1,'observed':2,'history':3}

def claim(iid,tf,start,end,lane='observed',at=None,admit=True):
    at=now_ms() if at is None else at;step=STEPS[tf]
    ident=sha256(('p38_gap:'+iid+':'+tf).encode()).hexdigest()
    with Session.begin() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_database() || :k))'),{'k':ident})
        inst=s.get(Instrument,iid);listed=(inst.spec.get('onboard_time') or 0) if inst else 0
        start=max(boundary(start,tf,True),boundary(listed,tf,True));end=min(boundary(end,tf),boundary(at,tf))
        if start>=end:return None
        row=s.get(Task,ident)
        if row is None:
            row=Task(id=ident,dedupe_key=ident,kind='p38_candle_gap',state='queued',available_at=at,attempts=0,payload={'instrument_id':iid,'timeframe':tf,'demands':[]});s.add(row)
        p=dict(row.payload);demands=[dict(d) for d in p.get('demands',[])];same=next((d for d in demands if d['lane']==lane),None)
        if same:same.update(start=min(start,same['start']),end=max(end,same['end']))
        else:demands.append({'start':start,'end':end,'lane':lane,'submitted_at':at})
        p['demands']=demands;row.payload=p
        if not admit:return None
        if row.state=='leased' and (row.lease_until or 0)>at:return None
        if row.available_at>at:return None
        # Age provides a cold-lane floor within each market, independent of quota fairness.
        demands.sort(key=lambda d:(0,d['submitted_at'],-PRIORITY[d['lane']]) if at-d['submitted_at']>=60000 else (1,PRIORITY[d['lane']],d['submitted_at']))
        while demands:
            demand=demands[0]
            # Bounded coverage probes, even for multi-year history.
            stop=min(demand['end'],demand['start']+1000*step)
            times=set(s.scalars(select(Candle.open_time).where(Candle.instrument_id==iid,Candle.resolution==tf,Candle.is_closed.is_(True),Candle.open_time>=demand['start'],Candle.open_time<stop)))
            missing=[t for t in range(demand['start'],stop,step) if t not in times]
            if missing:
                first=missing[0];last=first+step
                while last<stop and last not in times:last+=step
                owner=uuid4().hex;row.state='leased';row.lease_until=at+120000;row.lease_owner=owner;row.attempts+=1
                p.update(demands=demands,range=[first,last],lane=demand['lane']);row.payload=p
                return {'id':ident,'owner':owner,'start':first,'end':last,'lane':demand['lane'],'step':step}
            demand['start']=stop
            if stop>=demand['end']:demands.pop(0)
        row.state='complete';row.lease_owner=None;row.lease_until=None;p['demands']=[];row.payload=p
    return None

def finish(lease,error=None,at=None):
    if not lease:return
    at=now_ms() if at is None else at
    with Session.begin() as s:
        row=s.get(Task,lease['id'],with_for_update=True)
        if row and row.lease_owner==lease['owner']:
            row.state='queued';row.lease_owner=None;row.lease_until=None
            row.available_at=at+min(60000,1000*2**min(row.attempts,6)) if error else at
            row.payload={**row.payload,'last_error':error,'finished_at':at}

async def fetch(adapter,target,lane='observed',archive=None):
    import asyncio
    from engine.p38.lifecycle import thread,commit_guard
    from engine.db import engine
    from engine.pd2.request_budget import request_lane
    from engine.repository import save_batch
    iid,symbol,tf,interval,limit=target[:5];start=target[5] if len(target)>5 else None;step=STEPS[tf];now=now_ms()
    start=boundary(now,tf)-limit*step if start is None else start
    end=min(start+limit*step,boundary(now,tf))
    key='p38_gap_fetch:'+iid+':'+tf
    from engine.p38.fences import borrow
    connection=await borrow('gap')
    locked=False;lease=None;keeper=None;token=None;error=None
    try:
        locked=await thread(lambda:connection.execute(text('SELECT pg_try_advisory_lock(hashtext(current_database() || :k))'),{'k':key}).scalar())
        # Register demand even while a live owner holds the execution fence.
        if not locked:
            await asyncio.to_thread(claim,iid,tf,start,end,lane,None,False);return None
        lease=await asyncio.to_thread(claim,iid,tf,start,end,lane)
        if not lease:return None
        async def keep():
            while True:
                await asyncio.sleep(20)
                def renew():
                    with Session.begin() as s:
                        row=s.get(Task,lease['id'],with_for_update=True)
                        if row and row.lease_owner==lease['owner']:row.lease_until=now_ms()+120000
                await asyncio.to_thread(renew)
        keeper=asyncio.create_task(keep())
        token=request_lane.set('cold' if lease['lane']=='history' else 'live' if lease['lane']=='live' else 'repair')
        batch=None
        from engine.p40.database import measure
        with measure('gap_http',lane):
            if archive:
                from engine.pd2.p35_archive import monthly
                batch=await monthly(adapter,archive,tf,lease['start'],lease['end'])
            if batch is None:
                count=min(1000,max(1,(lease['end']-lease['start'])//step))
                batch=await adapter.collect_candles([(iid,symbol,tf,interval,count,lease['start'])])
        error='provider_error' if batch.errors else 'no_closed_progress' if not any(r['is_closed'] and lease['start']<=r['open_time']<lease['end'] for r in batch.candles) else None
        # Retain ownership through persistence, not only until the HTTP returns.
        def guard(s):
            row=s.get(Task,lease['id'],populate_existing=True,with_for_update=True)
            if not row or row.state!='leased' or row.lease_owner!=lease['owner'] or row.lease_until<now_ms():raise RuntimeError('candle_gap_lease_lost')
        def persist():
            with measure('gap_persist',lane):
                with Session() as s:guard(s)
                save_batch(batch)
        guard_token=commit_guard.set(guard)
        try:await thread(persist)
        finally:commit_guard.reset(guard_token)
        return batch
    except asyncio.CancelledError:
        error='cancelled';raise
    except Exception as exc:
        error=type(exc).__name__;raise
    finally:
        if token is not None:request_lane.reset(token)
        if keeper:keeper.cancel();await asyncio.gather(keeper,return_exceptions=True)
        try:
            if lease:await thread(finish,lease,error)
            if locked:await thread(lambda:connection.execute(text('SELECT pg_advisory_unlock(hashtext(current_database() || :k))'),{'k':key}))
        finally:await thread(connection.close)
