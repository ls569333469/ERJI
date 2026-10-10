import asyncio
import pytest
from engine.pd2 import p36_budget as b
from engine.models import UserPreference
from test_p25_signals import isolated

UM='https://fapi.binance.com/fapi/v1/'
SPOT='https://data-api.binance.vision/api/v3/'

@pytest.mark.parametrize('limit,weight',[(99,1),(100,2),(499,2),(500,5),(1000,5),(1500,10)])
def test_kline_boundary(limit,weight):
    assert b.specification(UM+'klines',{'params':{'limit':limit}})==('um',{'um':weight},weight)

def test_scopes_and_parameter_costs():
    for name in ('fundingRate','fundingInfo'):
        assert b.specification(UM+name,{})==('funding',{'funding':1},0)
    assert b.specification('https://fapi.binance.com/futures/data/openInterestHist',{})==('statistics',{'statistics':1},0)
    for host in ('api.binance.com','api3.binance.com','data-api.binance.vision'):
        assert b.specification('https://'+host+'/api/v3/klines',{})[1]=={'spot':2,'spot_raw':1}
    assert b.specification(UM+'exchangeInfo',{})[2]==1
    assert b.specification(UM+'premiumIndex',{'params':{'symbol':'BTCUSDT'}})[2]==1
    assert b.specification(UM+'premiumIndex',{})[2]==10
    assert b.specification(SPOT+'depth',{'params':{'limit':500}})[2]==25
    assert b.specification(UM+'depth',{'params':{'limit':500}})[2]==10
    assert b.specification(SPOT+'ticker/24hr',{'params':{'symbols':'["BTCUSDT"]'}})[2]==2
    assert b.specification('https://www.binance.com/bapi/alpha/klines',{}) is None
    assert b.specification('https://data.binance.vision/data/test.zip',{})[0]=='archive'
    with pytest.raises(ValueError):b.specification(SPOT+'klines',{'params':{'limit':1001}})

def state(factory):
    with factory() as s:return s.get(UserPreference,b.KEY).payload

def test_cancel_unsent_and_sent_failure_accounting(isolated,monkeypatch):
    monkeypatch.setattr(b,'now_ms',lambda:2000000000000)
    assert b.admit(UM+'openInterest',{},'a','live')==0
    b.release('a');assert state(isolated)['entries']==[]
    monkeypatch.setattr(b,'now_ms',lambda:2000000001000)
    assert b.admit(UM+'openInterest',{},'b','live')==0
    b.sent('b');b.release('b')
    assert len(state(isolated)['entries'])==1 and not state(isolated)['leases']

def test_header_monotonic_and_inflight(isolated,monkeypatch):
    clock=[2000000040000];monkeypatch.setattr(b,'now_ms',lambda:clock[0])
    for ticket in ('a','b'):
        assert b.admit(UM+'openInterest',{},ticket,'live')==0;b.sent(ticket);clock[0]+=100
    b.feedback(UM+'openInterest',200,{'x-mbx-used-weight-1m':'100'},{},'a')
    assert b.used(state(isolated),'um',clock[0])==101
    b.feedback(UM+'openInterest',200,{'x-mbx-used-weight-1m':'90'},{},'b')
    assert b.used(state(isolated),'um',clock[0])==101
    clock[0]+=100
    assert b.admit(UM+'openInterest',{},'c','live')==0
    assert b.used(state(isolated),'um',clock[0])==102
    b.feedback(UM+'openInterest',200,{'x-mbx-used-weight-1m':'102'},{},'c')
    assert b.used(state(isolated),'um',clock[0])==102

@pytest.mark.parametrize('status',[429,418])
def test_retry_after_shared_ban(isolated,monkeypatch,status):
    now=2000000000000;monkeypatch.setattr(b,'now_ms',lambda:now)
    b.feedback(UM+'fundingRate',status,{'retry-after':'120'})
    assert b.admit(SPOT+'klines',{},'wait','repair')>=120
    assert not state(isolated)['entries']

def test_200_budget_wait_not_ban_and_funding_shared(isolated,monkeypatch):
    now=2000000040000;monkeypatch.setattr(b,'now_ms',lambda:now)
    b.feedback(UM+'openInterest',200,{'x-mbx-used-weight-1m':'2160'})
    assert b.admit(UM+'klines',{},'wait','repair')>0
    assert not state(isolated).get('binance_ban_until')
    assert b.admit(UM+'fundingRate',{},'f','repair')==0
    assert b.admit(UM+'fundingInfo',{},'g','repair')>0

def test_fifo_aging_beats_continuous_live():
    p={'served':{},'pending':[{'id':'cold','family':'um','lane':'cold','enqueued':0},
                           {'id':'live','family':'um','lane':'live','enqueued':16000}]}
    assert b.winner(p,'um',16000)=='cold'

def test_legacy_migration_preserves_evidence(isolated,monkeypatch):
    now=2000000000000;monkeypatch.setattr(b,'now_ms',lambda:now)
    with isolated.begin() as s:
        s.merge(UserPreference(key='http_budget:binance_family',updated_at=now,payload={'entries':[[now,100]],'blocked_until':now+500000}))
    assert b.admit(UM+'openInterest',{},'a','live')>=500
    with isolated() as s:assert s.get(UserPreference,'http_budget:binance_family').payload['entries']==[[now,100]]

def test_queue_deadline_removes_ticket(monkeypatch):
    released=[]
    monkeypatch.setattr(b,'admit',lambda *a:1)
    monkeypatch.setattr(b,'release',lambda *a:released.append(a))
    monkeypatch.setattr(b,'QUEUE_SECONDS',.01)
    async def run():
        with pytest.raises(b.QueueDeadlineExceeded):
            async with b.request_slot(UM+'openInterest',{},'live'):pytest.fail('must not send')
    asyncio.run(run())
    assert released[0][1]=='queue_deadline'

def test_cancel_during_admission_cleans_up(monkeypatch):
    import threading
    started=threading.Event();finish=threading.Event();released=[]
    def admit(*a):started.set();finish.wait(2);return 0
    monkeypatch.setattr(b,'admit',admit)
    monkeypatch.setattr(b,'release',lambda *a:released.append(a))
    async def run():
        async def request():
            async with b.request_slot(UM+'openInterest',{},'live'):pytest.fail('cancelled')
        task=asyncio.create_task(request())
        await asyncio.to_thread(started.wait,2)
        task.cancel();finish.set()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run());assert len(released)==1

def test_separate_processes_share_one_admission():
    # Only run under the disposable-database launcher; never touch live locks.
    import os,subprocess,sys
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.engine import make_url
    from engine.db import Session
    assert make_url(os.environ['ERJI_DATABASE_URL']).database.startswith('erji_p36_check_')
    with Session.begin() as s:
        s.merge(UserPreference(key=b.KEY,updated_at=2000000000000,payload={
            'entries':[],'pending':[],'leases':{},'buckets':{},'events':[],'served':{}}))
    code="from engine.pd2 import p36_budget as b;import sys;b.now_ms=lambda:2000000000000;print(b.admit('https://fapi.binance.com/fapi/v1/openInterest',{},sys.argv[1],'live'))"
    def run(i):return float(subprocess.check_output([sys.executable,'-c',code,str(i)],text=True))
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run,range(4)))
        assert results.count(0)==1
        with Session() as s:assert len(s.get(UserPreference,b.KEY).payload['entries'])==1
    finally:
        with Session.begin() as s:s.delete(s.get(UserPreference,b.KEY))

def test_collection_loop_survives_legacy_event_loop_blocking():
    import time,threading
    from services.worker.main import independent_io
    started=threading.Event();progress=threading.Event()
    async def collect():
        started.set()
        await asyncio.sleep(.02)
        progress.set()
    async def run():
        task=asyncio.create_task(independent_io(collect))
        await asyncio.to_thread(started.wait,2)
        time.sleep(.1)  # Reproduce an unrelated synchronous legacy DB caller.
        assert progress.is_set()
        await task
    asyncio.run(run())

def test_collection_loop_cancellation_closes_adapters():
    import threading
    from services.worker.main import independent_io
    started=threading.Event();closed=threading.Event()
    async def collect():
        try:
            started.set();await asyncio.sleep(100)
        finally:closed.set()
    async def run():
        task=asyncio.create_task(independent_io(collect))
        await asyncio.to_thread(started.wait,2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert closed.is_set()
    asyncio.run(run())
