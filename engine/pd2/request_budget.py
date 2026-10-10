"""Cross-process REST slots and conservative per-host rolling weight budgets."""
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from engine.config import settings
from engine.db import Session
from engine.models import UserPreference
from engine.adapters.base import now_ms

from contextvars import ContextVar
request_lane=ContextVar("request_lane",default="live")
_engine = None

def budget_key(host):
    if host in ('data-api.binance.vision','data.binance.vision') or host == 'binance.com' or host.endswith('.binance.com'):
        return 'binance_family'
    if 'aster' in host:return 'aster_family'
    if 'hyperliquid' in host:return 'hyperliquid_family'
    if host == 'okx.com' or host.endswith('.okx.com'):return 'okx_family'
    return host

def budget_spec(url, kwargs):
    from engine.pd2.p36_budget import specification, LIMITS
    modern=specification(url,kwargs)
    if modern:
        family,cost,weight=modern
        return urlsplit(url).hostname,weight,LIMITS[family][0],0
    host=urlsplit(url).hostname or 'unknown'
    path=urlsplit(url).path
    weight=1;cap=300;gap=250
    if host in ('data-api.binance.vision','data.binance.vision') or host == 'binance.com' or host.endswith('.binance.com'):
        cap=300;gap=500
        if 'ticker/24hr' in path:weight=40 if path.startswith('/fapi/') else 80
        elif path.endswith('/depth'):weight=5
        elif path.endswith('/klines'):
            limit=int(kwargs.get('params',{}).get('limit',500))
            weight=(1 if limit<100 else 2 if limit<500 else 5 if limit<=1000 else 10) if path.startswith('/fapi/') else 2
        elif path.endswith('/premiumIndex'):weight=10
        elif path.endswith('/exchangeInfo'):weight=20
    elif 'aster' in host:
        cap=240;gap=500
    elif 'hyperliquid' in host:
        weight=20;cap=300;gap=1000
    elif host == 'okx.com' or host.endswith('.okx.com'):
        cap=120;gap=500
    if 'geckoterminal' in host:cap=20;gap=3100
    return host,weight,cap,gap

def reserve(url, kwargs, ticket=None):
    host,weight,cap,gap=budget_spec(url,kwargs);now=now_ms()
    family=budget_key(host)
    lane=request_lane.get()
    with Session.begin() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':':http:'+family})
        key='http_budget:'+family;row=s.get(UserPreference,key);old=row.payload if row else {}
        if family=='binance_family':
            modern=s.get(UserPreference,'p36_http_ledger')
            if modern:
                old={**old,'blocked_until':max(old.get('blocked_until',0),modern.payload.get('binance_ban_until',0))}
        # Never mutate a dict still owned by the mapped JSON payload. Otherwise
        # SQLAlchemy compares it with itself and silently drops heartbeat updates.
        pending=[dict(p) for p in old.get('pending',[]) if now-p['seen_at']<90000]
        if ticket:
            current=next((p for p in pending if p['id']==ticket),None)
            if current:current['seen_at']=now
            else:pending.append({'id':ticket,'enqueued_at':now,'seen_at':now,
                'lane':lane,'kind':'candles' if '/klines' in url else 'oi_history' if 'openInterestHist' in url else
                       'funding' if 'fundingRate' in url else 'live'})
            old={**old,'pending':pending}
            # Persistent FIFO prevents the same fast loop winning every wakeup.
            # Dead owners expire; cancellation explicitly removes its ticket.
            s.merge(UserPreference(key=key,updated_at=now,payload=old))
            ordered=sorted(pending,key=lambda p:(p.get('lane')=='cold',p['enqueued_at']))
            if ordered[0]['id']!=ticket:return 1.0
        entries=[r for r in old.get('entries',[]) if r[0]>now-60000]
        wait=max(old.get('blocked_until',0)-now,old.get('last_start',0)+gap-now,0)
        if lane=='cold' and sum(r[1] for r in entries if len(r)>2 and r[2]=='cold')+weight>max(weight,int(cap*.15)):
            return max(1,(min(r[0] for r in entries if len(r)>2 and r[2]=='cold')+60000-now)/1000)
        if sum(r[1] for r in entries)+weight>cap:
            wait=max(wait,(entries[0][0]+60000-now) if entries else 60000)
        if wait:return wait/1000
        s.merge(UserPreference(key=key,updated_at=now,payload={**old,'pending':[p for p in pending if p['id']!=ticket],
            'entries':entries+[[now,weight,lane]],'last_start':now,'budget_cap':cap,'minimum_gap_ms':gap}))
        return 0

def cancel_ticket(url,ticket):
    family=budget_key(urlsplit(url).hostname or 'unknown')
    with Session.begin() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':':http:'+family})
        row=s.get(UserPreference,'http_budget:'+family)
        if row and any(p['id']==ticket for p in row.payload.get('pending',[])):
            row.payload={**row.payload,'pending':[p for p in row.payload['pending'] if p['id']!=ticket]}

def feedback(url, status, headers, kwargs=None, ticket=None):
    from engine.pd2 import p36_budget
    if p36_budget.specification(url,kwargs or {}):
        return p36_budget.feedback(url,status,headers,kwargs,ticket)
    host=urlsplit(url).hostname;now=now_ms()
    family=budget_key(host)
    if family=='binance_family' and status in (418,429):
        # An unverified Binance product cannot bypass a same-IP ban in a new pool.
        p36_budget.feedback('https://fapi.binance.com/fapi/v1/openInterest',status,headers)
    _,_,cap,_=budget_spec(url,{})
    try:used=int(headers.get('x-mbx-used-weight-1m',0))
    except (ValueError,TypeError):used=0
    if status not in (418,429) and used<cap:return
    if status in (418,429):
        try:delay=float(headers.get('retry-after','60'))
        except ValueError:delay=60
        delay=max(60,delay)
    else:delay=(60000-now%60000)/1000+1
    with Session.begin() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':':http:'+family})
        key='http_budget:'+family;row=s.get(UserPreference,key);old=row.payload if row else {}
        s.merge(UserPreference(key=key,updated_at=now,payload={**old,'reported_used_weight_1m':used,'blocked_until':max(old.get('blocked_until',0),now+int(delay*1000))}))

def acquire_slot(namespace=''):
    global _engine
    if _engine is None:_engine=create_engine(settings().database_url,poolclass=NullPool)
    conn=_engine.connect()
    for slot in range(4):
        key=':http-slot:'+namespace+(':' if namespace else '')+str(slot)
        locked=conn.execute(text('SELECT pg_try_advisory_lock(hashtext(current_schema() || :key))'),{'key':key}).scalar()
        if locked:
            conn.commit();return conn,key
    conn.close();return None

def release_slot(lease):
    conn,key=lease
    try:conn.execute(text('SELECT pg_advisory_unlock(hashtext(current_schema() || :key))'),{'key':key});conn.commit()
    finally:conn.close()

@asynccontextmanager
async def request_slot(url, kwargs):
    from engine.pd2.source_policy import assert_market_request
    assert_market_request(url)
    from engine.pd2 import p36_budget
    if p36_budget.specification(url,kwargs):
        async with p36_budget.request_slot(url,kwargs,request_lane.get()) as ticket:
            yield ticket
        return
    # Await source budget before taking a scarce HTTP slot.
    from uuid import uuid4
    ticket=uuid4().hex
    try:
        while True:
            delay=await asyncio.to_thread(reserve,url,kwargs,ticket)
            if not delay:break
            await asyncio.sleep(min(delay,30))
    finally:
        await asyncio.shield(asyncio.to_thread(cancel_ticket,url,ticket))
    lease=None
    try:
        while lease is None:
            pending=asyncio.create_task(asyncio.to_thread(acquire_slot))
            try:lease=await asyncio.shield(pending)
            except asyncio.CancelledError:
                abandoned=await pending
                if abandoned:await asyncio.shield(asyncio.to_thread(release_slot,abandoned))
                raise
            if lease is None:await asyncio.sleep(.1)
        yield
    finally:
        if lease:await asyncio.shield(asyncio.to_thread(release_slot,lease))
