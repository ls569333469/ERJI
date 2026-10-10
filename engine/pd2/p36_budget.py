"""Binance public market-data admission, shared by every local process.

Official registry verified 2026-10-04: binance-connector-python MarketDataApi
and binance-spot-api-docs/rest-api.md. All amounts are 90% of published limits.
The single short database transaction admits BOTH quota and expiring HTTP lease.
No database connection is retained during network I/O.
"""
import asyncio
import json
import time
from contextlib import asynccontextmanager, contextmanager
import os
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, parse_qsl
from uuid import uuid4
from sqlalchemy import text
from engine.db import Session
from engine.models import UserPreference
from engine.adapters.base import now_ms

KEY='p36_http_ledger'
VERSION='binance-public-20261004-v1'
GLOBAL_SLOTS=20  # plus the unchanged legacy global four slots
LEASE_MS=90000
QUEUE_SECONDS=420  # Covers the mandatory 300s owner recovery plus admission time.
LIMITS={'um':(2160,60000),'spot':(5400,60000),'spot_raw':(270000,300000),
        'statistics':(900,300000),'funding':(450,300000),'archive':(60,60000)}
SLOTS={'um':8,'spot':8,'statistics':4,'funding':3,'archive':2}
LANE_WEIGHT={'live':6,'repair':3,'cold':1}

class QueueDeadlineExceeded(TimeoutError):
    """The request has not been sent; provider response timeout is different."""

def specification(url,kwargs):
    u=urlsplit(url);host=u.hostname or '';path=u.path
    p=dict(parse_qsl(u.query));p.update(kwargs.get('params') or {})
    if host=='data.binance.vision':return 'archive',{'archive':1},0
    um=host=='fapi.binance.com' and path.startswith('/fapi/')
    spot=(host=='data-api.binance.vision' or host in ('api.binance.com','api-gcp.binance.com')
          or host in tuple(f'api{i}.binance.com' for i in range(1,5))) and path.startswith('/api/v3/')
    if host=='fapi.binance.com' and path=='/futures/data/openInterestHist':
        return 'statistics',{'statistics':1},0
    if um and path in ('/fapi/v1/fundingRate','/fapi/v1/fundingInfo'):
        return 'funding',{'funding':1},0
    end=path.rsplit('/',1)[-1];symbol=bool(p.get('symbol'))
    weight=None
    if um:
        if end in ('openInterest','exchangeInfo','time','assetIndex'):weight=1
        elif end=='premiumIndex':weight=1 if symbol else 10
        elif path.endswith('/ticker/24hr'):weight=1 if symbol else 40
        elif end=='klines':
            limit=int(p.get('limit',500))
            if not 1<=limit<=1500:raise ValueError('invalid_futures_kline_limit')
            weight=1 if limit<100 else 2 if limit<500 else 5 if limit<=1000 else 10
        elif end=='depth':
            limit=int(p.get('limit',500))
            if limit not in (5,10,20,50,100,500,1000):raise ValueError('invalid_futures_depth_limit')
            weight=2 if limit<=50 else 5 if limit==100 else 10 if limit==500 else 20
        if weight is not None:return 'um',{'um':weight},weight
    if spot:
        if end=='exchangeInfo':weight=20
        elif end in ('time','ping'):weight=1
        elif end=='klines':
            if not 1<=int(p.get('limit',500))<=1000:raise ValueError('invalid_spot_kline_limit')
            weight=2
        elif end=='depth':
            limit=int(p.get('limit',100))
            if limit<1:raise ValueError('invalid_spot_depth_limit')
            weight=5 if limit<=100 else 25 if limit<=500 else 50 if limit<=1000 else 250
        elif path.endswith('/ticker/24hr'):
            symbols=p.get('symbols');symbols=json.loads(symbols) if isinstance(symbols,str) else symbols
            count=1 if symbol else len(symbols) if symbols else 100000
            weight=2 if count<=20 else 40 if count<=100 else 80
        elif path.endswith(('/ticker/price','/ticker/bookTicker')):weight=2 if symbol else 4
        if weight is not None:return 'spot',{'spot':weight,'spot_raw':1},weight
    return None

def transaction(s):
    s.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':KEY})

def initial(s,now):
    # Preserve legacy evidence. Unknown attribution cannot safely be split; wait
    # one complete longest window on first migration, retaining any real ban.
    old=s.get(UserPreference,'http_budget:binance_family')
    p=old.payload if old else {}
    return {'version':VERSION,'migrated_at':now,'migration_until':now+300000 if old else 0,
            'legacy_blocked_until':p.get('blocked_until',0),'entries':[], 'pending':[],
            'leases':{},'buckets':{},'served':{},'events':[]}

def clean(p,now):
    p=json.loads(json.dumps(p))
    p['entries']=[e for e in p.get('entries',[]) if now-e['at']<max(LIMITS[k][1] for k in e['cost'])]
    p['pending']=[e for e in p.get('pending',[]) if now-e['seen']<30000]
    p['leases']={k:v for k,v in p.get('leases',{}).items() if v['until']>now}
    p['events']=[e for e in p.get('events',[]) if now-e['at']<3600000][-128:]
    p['metrics']={k:v for k,v in p.get('metrics',{}).items() if int(k.split(':')[0])>=now//60000-20}
    # Accumulated lane scores are only comparable among active lanes. Normalize
    # when all work is idle so a newly active lane cannot acquire old credit.
    if not p['pending'] and not p['leases']:p['served']={}
    return p

def audit(p,event):
    """Keep the hot quota row bounded; aggregate throughput instead of a log tape."""
    p['events'].append(event)
    key=str(event['at']//60000)+':'+event.get('family','queue')+':'+event['kind']+':'+str(event.get('status',''))
    row=p.setdefault('metrics',{}).setdefault(key,{'count':0,'queue_ms_sum':0,'queue_ms_max':0,
            'db_ms_sum':0,'db_ms_max':0,'elapsed_ms_sum':0,'elapsed_ms_max':0})
    row['count']+=1
    for source,target in [('queue_ms','queue_ms'),('db_admission_ms','db_ms'),('elapsed_ms','elapsed_ms'),('network_ms','network_ms')]:
        value=event.get(source) or 0
        row[target+'_sum']=row.get(target+'_sum',0)+value;row[target+'_max']=max(row.get(target+'_max',0),value)

def used(p,bucket,now):
    cap,window=LIMITS[bucket]
    entries=[e for e in p['entries'] if e['at']>now-window]
    local=sum(e['cost'].get(bucket,0) for e in entries)
    b=p.get('buckets',{}).get(bucket,{})
    if b.get('header_window')==now//window:
        # The header anchor already includes completed requests. Only requests
        # in flight at that anchor and sends after it add to the anchor value.
        extra=sum(e['cost'].get(bucket,0) for e in entries
                  if e['id'] in b.get('unaccounted',[]) or e['seq']>b.get('anchor_seq',0))
        local=max(local,b.get('reported',0)+extra)
    return local

def winner(p,family,now):
    queue=[v for v in p['pending'] if v['family']==family]
    aged=[v for v in queue if now-v['enqueued']>=15000]
    if aged:return min(aged,key=lambda v:(v['enqueued'],v['id']))['id']
    served=p.get('served',{}).get(family,{})
    return min(queue,key=lambda v:(served.get(v['lane'],0)/LANE_WEIGHT[v['lane']],v['enqueued'],v['id']))['id']

# The production owner holds the in-memory ledger; direct DB mode remains for
# isolated tests and explicitly selected rollback, never an automatic fallback.
_STORE = None

@contextmanager
def ledger(now):
    if _STORE is not None:
        with _STORE.lock:
            p = clean(_STORE.payload, now)
            yield p
            _STORE.payload = p
        return
    with Session.begin() as session:
        transaction(session)
        row = session.get(UserPreference, KEY)
        p = clean(row.payload if row else initial(session, now), now)
        yield p
        session.merge(UserPreference(key=KEY, updated_at=now, payload=p))

def remote(name, *args):
    from engine.pd2.budget_owner import call
    return call(name, args)

def is_remote():
    return _STORE is None and os.environ.get('ERJI_BUDGET_OWNER','1') == '1'

def admit(url,kwargs,ticket,lane):
    if is_remote():return remote('admit',url,kwargs,ticket,lane)
    family,cost,_=specification(url,kwargs);now=now_ms();started=time.monotonic()
    with ledger(now) as p:
        current=next((v for v in p['pending'] if v['id']==ticket),None)
        if current:current['seen']=now
        else:
            current={'id':ticket,'family':family,'lane':lane,'seen':now,'enqueued':now}
            p['pending'].append(current)
        wait=max(0,p.get('migration_until',0)-now,p.get('legacy_blocked_until',0)-now,
                 p.get('binance_ban_until',0)-now if family!='archive' else 0)
        if winner(p,family,now)!=ticket:wait=max(wait,100)
        leases=list(p['leases'].values())
        if len(leases)>=GLOBAL_SLOTS or sum(v['family']==family for v in leases)>=SLOTS[family]:wait=max(wait,100)
        for bucket,amount in cost.items():
            cap,window=LIMITS[bucket];b=p['buckets'].get(bucket,{})
            wait=max(wait,b.get('next_start',0)-now,b.get('blocked_until',0)-now)
            if used(p,bucket,now)+amount>cap:wait=max(wait,250)
        if not wait:
            seq=p.get('seq',0)+1;p['seq']=seq
            p['entries'].append({'id':ticket,'at':now,'seq':seq,'cost':cost,'lane':lane,'sent':False})
            p['leases'][ticket]={'family':family,'until':now+LEASE_MS}
            p['pending']=[v for v in p['pending'] if v['id']!=ticket]
            for bucket,amount in cost.items():
                cap,window=LIMITS[bucket];b=p['buckets'].setdefault(bucket,{})
                b['next_start']=now+max(1,window*amount/cap)
            counts=p['served'].setdefault(family,{})
            # New/idle lanes start at the current floor, not with infinite credit.
            floor=min((counts.get(v['lane'],0)/LANE_WEIGHT[v['lane']] for v in p['pending'] if v['family']==family),default=0)
            counts[lane]=max(counts.get(lane,0),floor*LANE_WEIGHT[lane])+1
            audit(p,{'at':now,'kind':'admitted','family':family,'lane':lane,'queue_ms':now-current['enqueued'],
                                'db_admission_ms':round((time.monotonic()-started)*1000)})
        return wait/1000

def sent(ticket):
    if is_remote():return remote('sent',ticket)
    with ledger(now_ms()) as p:
        if ticket not in p['leases']:
            raise QueueDeadlineExceeded('admission_lease_expired_before_send')
        for e in p['entries']:
            if e['id']==ticket:e['sent']=True


def release(ticket,reason=None):
    if is_remote():return remote('release',ticket,reason)
    now=now_ms()
    with ledger(now) as p:
        p['pending']=[v for v in p['pending'] if v['id']!=ticket]
        p['leases'].pop(ticket,None)
        p['entries']=[v for v in p['entries'] if v['id']!=ticket or v['sent']]
        if reason:audit(p,{'at':now,'kind':reason,'ticket':ticket})


def retry_ms(headers,now):
    value=headers.get('retry-after','60')
    try:return max(0,int(float(value)*1000))
    except (ValueError,TypeError):
        try:return max(0,int(parsedate_to_datetime(value).timestamp()*1000)-now)
        except (ValueError,TypeError):return 60000

def feedback(url,status,headers,kwargs=None,ticket=None,received_at=None,network_ms=None):
    if is_remote():return remote('feedback',url,status,dict(headers),kwargs,ticket,received_at,network_ms)
    spec=specification(url,kwargs or {})
    if not spec:return
    family,cost,_=spec;now=now_ms();headers={k.lower():v for k,v in headers.items()}
    with ledger(now) as p:
        entry=next((e for e in p['entries'] if e['id']==ticket),None)
        if entry:entry['done']=True
        bucket='um' if family in ('um','statistics','funding') else 'spot' if family=='spot' else None
        if bucket and 'x-mbx-used-weight-1m' in headers:
            try:reported=max(0,int(headers['x-mbx-used-weight-1m']))
            except (ValueError,TypeError):reported=None
            try:stamp=int(parsedate_to_datetime(headers['date']).timestamp()*1000)
            except (ValueError,KeyError,TypeError):stamp=entry['at'] if entry else now
            window=stamp//60000;b=p['buckets'].setdefault(bucket,{})
            if reported is not None and window==now//60000 and (window>b.get('header_window',-1) or reported>b.get('reported',0)):
                # Never lower a same-window maximum on an out-of-order response.
                b.update(header_window=window,reported=reported,anchor_seq=p.get('seq',0),
                         unaccounted=[e['id'] for e in p['entries'] if not e.get('done') and e['cost'].get(bucket)])
        if status in (418,429):
            until=now+retry_ms(headers,now)
            if family!='archive':p['binance_ban_until']=max(p.get('binance_ban_until',0),until)
            for bucket in cost:
                b=p['buckets'].setdefault(bucket,{})
                b['blocked_until']=max(b.get('blocked_until',0),until)
        audit(p,{'at':now,'kind':'response','status':status,'family':family,
                            'elapsed_ms':now-entry['at'] if entry else None,'network_ms':network_ms,'received_at':received_at})

async def durable_call(fn,*args):
    task=asyncio.create_task(asyncio.to_thread(fn,*args))
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise

@asynccontextmanager
async def request_slot(url,kwargs,lane):
    ticket=uuid4().hex;started=time.monotonic();reason=None
    try:
        while True:
            delay=await durable_call(admit,url,kwargs,ticket,lane)
            if not delay:break
            remaining=QUEUE_SECONDS-(time.monotonic()-started)
            if remaining<=0:
                reason='queue_deadline';raise QueueDeadlineExceeded('local_admission_deadline')
            await asyncio.sleep(min(delay,1,remaining))
        # Caller marks sent immediately before transport; cancellation prior to
        # that mark returns the quota reservation as well as the HTTP lease.
        yield ticket
    except (asyncio.CancelledError,Exception) as error:
        reason=reason or type(error).__name__
        raise
    finally:
        await durable_call(release,ticket,reason)
