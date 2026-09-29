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

_engine = None

def budget_spec(url, kwargs):
    host=urlsplit(url).hostname or 'unknown'
    path=urlsplit(url).path
    weight=1;cap=300;gap=250
    if 'binance.com' in host:
        cap=600
        if 'ticker/24hr' in path:weight=80
        elif path.endswith('/depth'):weight=5
        elif path.endswith('/klines'):weight=10 if int(kwargs.get('params',{}).get('limit',500))>1000 else 5
        elif path.endswith('/premiumIndex'):weight=10
        elif path.endswith('/exchangeInfo'):weight=20
    if 'hyperliquid' in host:weight=20;cap=600
    if 'geckoterminal' in host:cap=20;gap=3100
    return host,weight,cap,gap

def reserve(url, kwargs):
    host,weight,cap,gap=budget_spec(url,kwargs);now=now_ms()
    with Session.begin() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':':http:'+host})
        key='http_budget:'+host;row=s.get(UserPreference,key);old=row.payload if row else {}
        entries=[r for r in old.get('entries',[]) if r[0]>now-60000]
        wait=max(old.get('blocked_until',0)-now,old.get('last_start',0)+gap-now,0)
        if sum(r[1] for r in entries)+weight>cap:
            wait=max(wait,(entries[0][0]+60000-now) if entries else 60000)
        if wait:return wait/1000
        s.merge(UserPreference(key=key,updated_at=now,payload={**old,'entries':entries+[[now,weight]],'last_start':now}))
        return 0

def feedback(url, status, headers):
    host=urlsplit(url).hostname;now=now_ms()
    try:used=int(headers.get('x-mbx-used-weight-1m',0))
    except (ValueError,TypeError):used=0
    if status not in (418,429) and used<600:return
    if status in (418,429):
        try:delay=float(headers.get('retry-after','60'))
        except ValueError:delay=60
        delay=max(60,delay)
    else:delay=(60000-now%60000)/1000+1
    with Session.begin() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':':http:'+host})
        key='http_budget:'+host;row=s.get(UserPreference,key);old=row.payload if row else {}
        s.merge(UserPreference(key=key,updated_at=now,payload={**old,'reported_used_weight_1m':used,'blocked_until':max(old.get('blocked_until',0),now+int(delay*1000))}))

def acquire_slot():
    global _engine
    if _engine is None:_engine=create_engine(settings().database_url,poolclass=NullPool)
    conn=_engine.connect()
    for slot in range(4):
        key=':http-slot:'+str(slot)
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
    # Await source budget before taking a scarce HTTP slot.
    while True:
        delay=await asyncio.to_thread(reserve,url,kwargs)
        if not delay:break
        await asyncio.sleep(min(delay,30))
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
