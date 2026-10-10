"""R3 failure modes against a disposable PostgreSQL cluster; no exchange I/O."""
import asyncio
import threading
import sys
from types import SimpleNamespace as N
from collections import deque
from contextlib import ExitStack
import pytest
from sqlalchemy import text,select
from test_p39_deadlock_fix import independent_schema
from engine.p38 import jobs,fences
from engine.models import Task,Instrument,UserPreference,CurrentSignal,SignalSnapshot
from engine.adapters.base import now_ms
from engine.p37.detector import Detector
from engine.p40.streams import StreamLifecycle,ReceiveTimings
# Load lazy runtime consumers before the per-test schema fixture swaps Session.
# A module first imported inside an already swapped fixture would retain that
# scratch schema after teardown and contaminate subsequent tests.
from engine import signal_service
from engine.pd2 import service,p35_schedule,derivatives
from engine.p38 import windows
from engine.strategies import p33_runtime
from services.api.main import app
from engine.db import Session as ORIGINAL_SESSION,engine as ORIGINAL_ENGINE

@pytest.fixture(autouse=True)
def restore_lazy_modules(independent_schema):
    loaded=set(sys.modules);factory=jobs.Session;database=jobs.engine
    yield
    for name in set(sys.modules)-loaded:
        if not name.startswith(('engine.','services.')):continue
        module=sys.modules[name]
        if getattr(module,'Session',None) is factory:module.Session=ORIGINAL_SESSION
        if getattr(module,'engine',None) is database:module.engine=ORIGINAL_ENGINE

IID='binance:perp:BTCUSDT'

def test_close_evidence_survives_reconnect_with_sent_and_received(monkeypatch):
    monkeypatch.setenv('ERJI_RUN_ID','r3-current')
    first=StreamLifecycle('r3-receiver','binance','global',['b','a'])
    first.connected()
    error=N(rcvd=N(code=1001,reason='server close'),sent=N(code=1011,reason='ping timeout'))
    first.end('failure',error,3.2)
    second=StreamLifecycle('r3-receiver','binance','global',['a','b']);second.connected()
    state=second.metrics()
    assert state['connection_id']!=first.connection_id and state['run_id']=='r3-current'
    close=state['recent_closes'][-1]
    assert close['close']['rcvd']['code']==1001 and close['close']['sent']['code']==1011
    assert close['subscription_hash']==state['subscription_hash']
    second.end('subscription_change');second.end('failure')
    assert len(second.metrics()['recent_closes'])==2

def test_lifecycle_ring_is_bounded_and_connect_failure_is_attributed():
    for _ in range(21):
        life=StreamLifecycle('r3-bounded','binance','global',['a'])
        life.end('failure',OSError('connect failed'))
    history=life.metrics()['recent_closes']
    assert len(history)==16 and history[-1]['connected_at'] is None
    assert history[-1]['exception']=='OSError'

def test_backoff_cannot_reset_on_handshake_ack_or_discontinuous_packets():
    t=ReceiveTimings()
    for at in range(0,80000,1000):t.received({'result':None},at)
    assert not t.stable(79000) and t.packets==0
    for at in range(100000,165000,1000):t.received({'data':{'e':'markPriceUpdate','E':at}},at)
    assert t.stable(164000)
    t.received({'data':{'E':200000}},200000)
    assert not t.stable(200000) and t.gaps_over_15s==1

def test_discovery_failure_without_stable_data_keeps_exponential_backoff(monkeypatch):
    from engine.pd2 import native_streams
    from engine.p40 import streams
    clock=[0];attempts=[];waits=[]
    async def receiver():
        attempts.append(1);clock[0]+=120000
        if len(attempts)<3:raise ConnectionError('connected without stable data')
        raise asyncio.CancelledError()
    async def sleep(delay):waits.append(delay)
    monkeypatch.setattr(native_streams,'now_ms',lambda:clock[0])
    monkeypatch.setattr(native_streams,'heartbeat',lambda *args:None)
    monkeypatch.setattr(native_streams.asyncio,'sleep',sleep)
    monkeypatch.setattr(streams,'retry_delay',lambda delay:delay)
    async def scenario():
        with pytest.raises(asyncio.CancelledError):await native_streams.supervise_stream('discovery:r3',receiver,stable=lambda:False)
    asyncio.run(scenario())
    assert len(attempts)==3 and waits==[5,10]

def test_actual_native_price_gaps_and_rejections_do_not_fill_a_window():
    d=Detector();d.catalog={IID:{'venue':'binance'}}
    d.price(IID,100000,'100','mark',100100,'first')
    d.price(IID,120001,'101','mark',120201,'second')
    d.price(IID,121000,'105','last',121001,'kline')
    row=d.price_receipts[IID]
    assert row['source_gap_ms']==20001 and row['receive_gap_ms']==20101
    assert row['connection_id']=='second' and row['accepted']==2
    assert row['last_attempt_reason']=='wrong_price_kind'
    assert d.price_kinds[IID]=='mark' and len(d.prices[IID])==2

def test_stale_source_is_diagnosed_without_admission(monkeypatch):
    from engine.p37 import detector as module
    d=Detector();d.catalog={IID:{'venue':'binance'}};monkeypatch.setattr(module,'detector',d)
    module.ingest('binance',{'e':'markPriceUpdate','s':'BTCUSDT','p':'100','E':1},20000,'transport')
    assert IID not in d.prices
    assert d.price_receipts[IID]['last_attempt_connection_id']=='transport'
    assert d.price_receipts[IID]['rejections']=={'source_age_invalid':1}

def test_saturating_gaps_does_not_consume_entry_reservation():
    assert fences.fence_engine.pool.size()+fences.urgent_engine.pool.size()+fences.gap_engine.pool.size()==8
    with ExitStack() as stack:
        gaps=[stack.enter_context(fences.connect_for('gap')) for _ in range(2)]
        busy=[stack.enter_context(fences.connect_for('structure')) for _ in range(4)]
        urgent=stack.enter_context(fences.connect_for('entry'))
        assert len({c.scalar(text('SELECT pg_backend_pid()')) for c in gaps+busy+[urgent]})==7
        urgent.execute(text('SELECT pg_advisory_lock(9765321)'))
    with fences.connect_for('protect') as c:
        assert not c.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory')"))

def test_gap_waits_before_checkout_and_cancel_returns_admission():
    from engine.p40.database import snapshot
    async def scenario():
        first=await fences.borrow('gap');second=await fences.borrow('gap')
        before=snapshot()['pools']['fence_gap']['counts']['checkouts']
        third=asyncio.create_task(fences.borrow('gap'))
        try:
            await asyncio.sleep(.01);assert not third.done()
            assert snapshot()['pools']['fence_gap']['counts']['checkouts']==before
            urgent=await asyncio.wait_for(fences.borrow('entry'),1)
            urgent.close();third.cancel()
            with pytest.raises(asyncio.CancelledError):await third
        finally:first.close();second.close()
        fresh=await asyncio.wait_for(fences.borrow('gap'),1);fresh.close()
    asyncio.run(scenario())

def test_first_pending_time_survives_coalescing_and_deferral(monkeypatch):
    version=['a'];at=now_ms()
    monkeypatch.setattr(jobs,'dependency_versions',lambda ids,*args:{i:version[0] for i in ids})
    jobs.submit([IID],at=at,work_kind='projection')
    version[0]='b';jobs.submit([IID],at=at+1000,work_kind='projection')
    first=jobs.take(at+2000,work_kinds=['projection'])
    assert jobs.finish(first,'owner_still_running',at+2001)
    lease=jobs.take(at+2300,work_kinds=['projection'])
    with jobs.Session() as s:
        p=s.get(Task,lease['id']).payload
        assert p['queue_wait_ms']==2300 and p['desired_wait_ms']==1300
    assert jobs.finish(lease,at=at+2301)
    with jobs.Session() as s:
        p=s.get(Task,lease['id']).payload
        assert 'deferred_reason' not in p and 'first_pending_at' not in p

def test_projection_phase_uses_native_identity_and_changes_once_per_cycle():
    from engine.p39.timing import scheduled_at,ORDINARY_MS,phase
    at=now_ms();ids=[f'binance:perp:R3{i}USDT' for i in range(50)]
    assert len({scheduled_at(i,at,ORDINARY_MS) for i in ids})>30
    boundary=scheduled_at(IID,at,ORDINARY_MS)+ORDINARY_MS
    assert jobs.dependency_versions([IID],boundary-1,'projection')[IID]!=jobs.dependency_versions([IID],boundary,'projection')[IID]
    assert jobs.dependency_versions([IID],boundary,'projection')[IID]==jobs.dependency_versions([IID],boundary+1,'projection')[IID]
    assert phase(IID)!=phase('binance_spot:spot:BTCUSDT')

def test_same_phase_repair_only_publishes_fast_snapshot(monkeypatch):
    from engine.pd2 import service
    from engine.p39.timing import scheduled_at,ORDINARY_MS
    at=now_ms();jobs.submit([IID],at=at,work_kind='projection')
    lease=jobs.take(at,work_kinds=['projection']);lease['projection_cycle']=scheduled_at(IID,at,ORDINARY_MS)
    calls=[];monkeypatch.setattr(service,'decision_list',lambda **kw:calls.append(kw))
    monkeypatch.setattr(jobs,'now_ms',lambda:at)
    asyncio.run(jobs.execute(lease,None))
    assert calls==[{'instrument_ids':[IID],'fast':True}]
    with jobs.Session() as s:assert s.get(Task,lease['id']).state=='complete'

def test_initial_projection_publishes_fast_before_heavy_and_returns_connection(monkeypatch):
    from engine.pd2 import service
    at=now_ms();jobs.submit([IID],at=at,work_kind='projection');lease=jobs.take(at,work_kinds=['projection'])
    calls=[]
    def build(**kw):calls.append(kw.get('fast',False));assert fences.fence_engine.pool.checkedout()==1
    monkeypatch.setattr(service,'decision_list',build)
    asyncio.run(jobs.execute(lease,None))
    assert calls==[True,False] and fences.fence_engine.pool.checkedout()==0
    with jobs.Session() as s:assert s.get(Task,lease['id']).payload.get('projection_cycle') is not None

def test_prepared_structure_rechecks_instrument_spec_before_writing():
    from engine import signal_service
    prepared=signal_service.prepare_scan([IID])
    # Use a detached market eligible for preparation, without fabricating bars.
    prepared['markets']=[N(instrument_id=IID)]
    with jobs.Session() as s:prepared['instruments']={IID:s.get(Instrument,IID)}
    with jobs.Session() as s:prepared['bars']=signal_service._recent_bars(s,[IID],signal_service.REQUIRED_RESOLUTIONS,300)
    with jobs.Session.begin() as s:s.get(Instrument,IID).spec={'tick_size':'.001'}
    result=signal_service.scan_signals([IID],prepared=prepared)
    assert result['superseded']==1 and result['scanned']==0

def test_stage_failure_releases_same_backend_lock_and_pool(monkeypatch):
    from engine.pd2 import service
    at=now_ms();jobs.submit([IID],at=at,work_kind='projection');lease=jobs.take(at,work_kinds=['projection'])
    def fail(**kw):raise ValueError('test stage failure')
    monkeypatch.setattr(service,'decision_list',fail);asyncio.run(jobs.execute(lease,None))
    assert fences.fence_engine.pool.checkedout()==0
    with fences.connect() as c:
        assert not c.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory')"))
    with jobs.Session() as s:assert s.get(Task,lease['id']).payload['error']=='ValueError'

def test_cancel_does_not_release_mutation_fence_while_sync_thread_runs(monkeypatch):
    from engine.pd2 import service
    at=now_ms();jobs.submit([IID],at=at,work_kind='projection');lease=jobs.take(at,work_kinds=['projection'])
    started=threading.Event();release=threading.Event()
    def build(**kw):started.set();assert release.wait(4)
    monkeypatch.setattr(service,'decision_list',build)
    async def scenario():
        task=asyncio.create_task(jobs.execute(lease,None))
        try:
            assert await asyncio.to_thread(started.wait,2)
            task.cancel();await asyncio.sleep(.01);assert not task.done()
            with fences.connect() as c:
                assert not c.scalar(text('SELECT pg_try_advisory_lock(hashtext(current_database() || :k))'),{'k':'p38_execute:'+IID})
        finally:release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert fences.fence_engine.pool.checkedout()==0
    asyncio.run(scenario())
    with jobs.Session() as s:assert s.get(Task,lease['id']).state=='leased'

def test_receipt_export_is_bounded_and_uses_coherent_clock():
    from engine.p37.worker import discovery_health_snapshot
    d=Detector();at=now_ms()
    d.catalog={f'binance:perp:X{n}USDT':{'venue':'binance','symbol':str(n),'liquidity':2000000} for n in range(70)}
    for iid in d.catalog:
        d.price(iid,at-20000,'100','mark',at-20000,'old')
        d.price(iid,at,'101','mark',at,'new')
    result=discovery_health_snapshot(d,lambda:at)
    assert result['price_receipts']['markets']==70 and len(result['price_receipts']['examples'])==30
    assert result['price_receipts']['examples'][0]['recent_gaps'][-1]['source_gap_ms']==20000

def test_operations_api_keeps_new_diagnostics_without_old_run_records(monkeypatch):
    from fastapi.testclient import TestClient
    from services.api.main import app
    from engine.repository import write_heartbeat
    from engine.models import WorkerHeartbeat
    at=now_ms();monkeypatch.setenv('ERJI_RUN_ID','r3-api')
    with jobs.Session.begin() as s:
        write_heartbeat(s,'stream_binance',at,'connected',{'lifecycle':{'connection_id':'real-test-id','recent_closes':[]}})
        write_heartbeat(s,'anomaly_discovery',at,'partial',{'price_receipts':{'markets':10,'examples':[]}})
        s.add(WorkerHeartbeat(role='r3-old',updated_at=at,state='ok',details={'run_id':'old'}))
    response=TestClient(app).get('/v1/operations');assert response.status_code==200
    workers={r['role']:r['details'] for r in response.json()['workers']}
    assert workers['stream_binance']['lifecycle']['connection_id']=='real-test-id'
    assert workers['anomaly_discovery']['price_receipts']['markets']==10 and 'r3-old' not in workers

def test_fast_repair_downgrades_expired_snapshot_without_extending_ttl(monkeypatch):
    from test_p39_reliability import seed_plan
    from engine.pd2 import service,signal_projection
    at=now_ms()
    with jobs.Session.begin() as s:
        iid,old=seed_plan(s,at)
        row=s.get(SignalSnapshot,'p39-old');row.as_of=at
        row.payload={**row.payload,'as_of':at,'valid_until':at-1,'execution':{'received_at':at}}
    monkeypatch.setattr(service,'now_ms',lambda:at)
    service.decision_list(instrument_ids=[iid],fast=True)
    with jobs.Session() as s:
        projected=s.get(CurrentSignal,'p39-p')
        assert projected.action=='paused' and projected.valid_until==at-1
        assert s.get(SignalSnapshot,'p39-old').payload['action']['code']==old['action']['code']

def test_historical_candidate_is_preserved_without_derivative_refresh_or_read_connection(monkeypatch):
    from test_p39_reliability import seed_plan
    from engine.pd2 import service,derivatives
    from engine.models import Episode
    from engine.db import engine
    at=now_ms()
    with jobs.Session.begin() as s:
        iid,_=seed_plan(s,at)
        s.get(Episode,'p39-e').phase='ended'
    def forbidden(*args,**kwargs):raise AssertionError('historical candidate refreshed derivatives')
    monkeypatch.setattr(derivatives,'context_for',forbidden)
    class DetachedLabels(dict):
        def __getitem__(self,key):
            assert engine.pool.checkedout()==0
            return super().__getitem__(key)
    monkeypatch.setattr(service,'PHASES',DetachedLabels(service.PHASES))
    result=service.decision_list(instrument_ids=[iid])
    historical=[r for r in result['rows'] if not r.get('plan_id') and r['id']=='p39-e']
    assert len(historical)==1 and historical[0]['action']['code']=='ended'
    assert historical[0]['derivatives']['cross_market_oi']['status']=='historical_not_refreshed'
