"""R4 regressions; disposable PostgreSQL and local WebSocket servers only."""
import asyncio
from collections import deque
from copy import deepcopy
from types import SimpleNamespace as N
import pytest
from sqlalchemy import select,text,create_engine
from test_p40_r3 import independent_schema,restore_lazy_modules
from engine.p39 import pipeline
from engine.p37.detector import Detector
from engine.p37 import store,worker
from engine.models import Observation,AnalysisDemand,AnalysisResult,OiPoint,RawEvent
from engine.adapters.base import now_ms
from engine.p40 import database
from engine.p40.streams import ReliableClientConnection,StreamLifecycle,ReceiveTimings,measured_writer_put
from engine.p40.diagnostics import price_receipt_sample
from engine.p40.oi_receipts import compare_points,sample_persisted
IID='binance:perp:BTCUSDT'

def trigger(at,quantity='106'):
    d=Detector();d.catalog={IID:{'asset_id':'BTC','symbol':'BTCUSDT','venue':'binance','liquidity':2000000}}
    d.oi[IID]=deque([(at-900000,'100'),(at,quantity)])
    return d,d.facts(IID,at)

def test_sync_repairs_same_endpoint_identity_once_and_preserves_frozen_history():
    at=now_ms();d,f=trigger(at)
    assert store.persist([(IID,f,'visible')],d.catalog,at,d.config)==1
    with pipeline.Session.begin() as s:
        demand=s.scalar(select(AnalysisDemand).where(AnalysisDemand.origin_type=='observation'))
        original=deepcopy(demand.payload['source']);ident=demand.id
        demand.payload={'source':{k:v for k,v in original.items() if k not in ('trigger_event_id','evaluated_at')}}
    context=pipeline.begin_result(IID,'structure','legacy-result',{},at+1)
    with pipeline.Session() as s:frozen=deepcopy(s.get(AnalysisResult,context['result_id']).payload)
    with pipeline.Session.begin() as s:pipeline.synchronize(s,at+2,d.config)
    with pipeline.Session() as s:
        row=s.get(AnalysisDemand,ident);assert row.payload['source']==original;stamp=row.updated_at
    with pipeline.Session.begin() as s:pipeline.synchronize(s,at+3,d.config)
    with pipeline.Session() as s:
        assert s.get(AnalysisDemand,ident).updated_at==stamp
        assert s.get(AnalysisResult,context['result_id']).payload==frozen
    later=at+60000;d2,f2=trigger(later,'110');store.persist([(IID,f2,'visible')],d2.catalog,later,d2.config)
    with pipeline.Session.begin() as s:pipeline.synchronize(s,later+1,d.config)
    with pipeline.Session() as s:
        source=s.get(AnalysisDemand,ident).payload['source']
        assert source['source_at']==later and source['trigger_event_id']!=original['trigger_event_id']
        assert s.get(AnalysisResult,context['result_id']).payload==frozen

def test_unknown_legacy_identity_is_not_invented():
    at=now_ms();d,f=trigger(at);store.persist([(IID,f,'visible')],d.catalog,at,d.config)
    with pipeline.Session.begin() as s:
        obs=s.scalar(select(Observation));obs.payload={k:v for k,v in obs.payload.items() if k not in ('trigger_event_id','trigger_evaluated_at')}
        pipeline.synchronize(s,at+1,d.config)
    with pipeline.Session() as s:
        source=s.scalar(select(AnalysisDemand)).payload['source']
        assert source['trigger_event_id'] is None and source['evaluated_at'] is None

def test_eligible_strata_are_not_crowded_out_by_old_ineligible_prices():
    at=1000000;meta={};prices={};receipts={}
    for n in range(80):
        iid=str(n);meta[iid]={'venue':'binance_spot' if n%2 else 'binance','liquidity':0 if n<60 else 2000000}
        prices[iid]=[(at-100000 if n<60 else at,'100')];receipts[iid]={'accepted':1,'rejections':{}}
    result=price_receipt_sample(meta,prices,receipts,Detector().config,at)
    assert len(result['examples'])==30 and result['snapshot_at']==at
    assert sum(r['markets'] for r in result['strata'])==80
    assert {r['venue'] for r in result['examples'] if r['eligible']}=={'binance','binance_spot'}
    assert all(r['sampled'] for r in result['strata'])

def test_source_kind_and_rejection_are_distinct_from_price_basis(monkeypatch):
    from engine.p37 import detector as module
    d=Detector();d.catalog={IID:{'venue':'binance'}};monkeypatch.setattr(module,'detector',d)
    module.ingest('binance',{'e':'markPriceUpdate','s':'BTCUSDT','E':10000,'p':'100'},10001,'c1')
    assert d.price_receipts[IID]['source_kind']=='markPriceUpdate'
    module.ingest('binance',{'e':'kline','s':'BTCUSDT','E':12000,'k':{'i':'5m','c':'110','t':0,'o':'100','q':'1000','x':False}},12001,'c2')
    assert d.price_receipts[IID]['last_attempt_source_kind']=='kline_5m'
    assert d.price_receipts[IID]['last_attempt_reason']=='wrong_price_kind'
    assert d.prices[IID][-1]==(10000,'100')

def test_oi_evidence_preserves_known_time_and_does_not_feed_missing_endpoint():
    d=Detector();at=now_ms();d.catalog={IID:{'venue':'binance','liquidity':2000000,'asset_id':'BTC'}}
    d.oi_point(IID,at-900000,'100',at-899000);d.oi_point(IID,at,'106',at+2000)
    events=d.evaluate_since({IID:at-1000},at+3000)
    assert d.oi_receipts[IID][-1]['known_at']==at+2000
    assert d.oi_evaluations[IID][-1]['endpoints']==[{'source_at':at,'known_at':at+2000}]
    before=list(d.oi[IID]);result=compare_points([(IID,at-500,'103',at-400)],{IID:{at}}, {IID:at+3000},at+3000)
    assert result[0]['known_before_last_evaluation'] and not result[0]['in_detector_memory']
    assert list(d.oi[IID])==before

def test_persisted_oi_probe_is_bounded_and_read_only():
    at=now_ms();d=Detector();d.catalog={IID:{'venue':'binance'}}
    with pipeline.Session.begin() as s:
        s.add(RawEvent(id='r4-fixture',source='binance',endpoint='test:local',received_at=at,payload={}));s.flush()
        for n in range(10):s.add(OiPoint(instrument_id=IID,event_time=at-n*1000,base_quantity=100+n,received_at=at-n*1000,quote_asset='USDT',raw_ref='r4-fixture'))
    probe=sample_persisted(d,clock=lambda:at)
    assert len(probe['endpoints'])==8 and not d.oi
    assert probe['sampled_instruments']==[IID]

def test_capacity_uses_native_tier_and_scheduler_intervals():
    from engine.pd2.p35_capacity import capacity
    from engine.pd2.collection_schedule import collection_interval,priority_snapshot,_priority_cache
    at=now_ms();d,f=trigger(at);store.persist([(IID,f,'visible')],d.catalog,at,d.config)
    with pipeline.Session() as s:oi=next(r for r in capacity(s,at)['endpoints'] if r['endpoint']=='openInterest')
    assert oi['interval_seconds']==300 and oi['candidate_interval_seconds']==oi['active_interval_seconds']==60
    assert oi['tier_markets']=={'ordinary':0,'t2':1,'t3':0}
    _priority_cache.clear();assert priority_snapshot('binance','oi',at)[-1]['BTCUSDT']==collection_interval('oi','t2')

def test_hold_attribution_alias_and_guard_timeline(monkeypatch):
    pool=create_engine(pipeline.Session.kw['bind'].url);database.install(pool,'r4-test')
    try:
        with pool.connect() as c:
            with database.measure('mutation_stage','r4-test'):c.execute(text('SELECT 1'))
            hold=c.info['p40_hold'];pid=hold['backend_pid'];hold['started']-=11;c.info['p40_checkout']-=11
            c.commit()
        snapshot=database.snapshot()['pools']['r4-test']
        assert snapshot['hold_ms']==snapshot['checkout_ms'] and snapshot['active']==0
        evidence=snapshot['long_holds'][-1]
        assert evidence['backend_pid']==pid and evidence['hold_ms']>=11000
        assert any(r['stage']=='mutation_stage:r4-test' for r in evidence['timeline'])
        assert any(r['stage']=='commit_requested' for r in evidence['timeline'])
        assert evidence['sql_count']==1
    finally:pool.dispose()

def test_real_websocket_pong_pause_resume_and_bounded_timeline():
    from websockets.asyncio.server import serve
    from websockets.asyncio.client import connect
    async def scenario():
        async def handler(ws):
            for n in range(40):await ws.send(str(n))
            await ws.wait_closed()
        async with serve(handler,'127.0.0.1',0) as server:
            port=server.sockets[0].getsockname()[1]
            async with connect(f'ws://127.0.0.1:{port}',max_queue=2,create_connection=ReliableClientConnection) as ws:
                life=StreamLifecycle('r4-local','fixture','test',['a']);life.attach(ws);life.connected()
                await asyncio.sleep(.05)
                assert ws.diagnostic_snapshot()['counts']['read_paused']>=1
                for _ in range(40):await ws.recv()
                pong=await ws.ping();await asyncio.wait_for(pong,1)
                snapshot=life.metrics()['transport']
                assert snapshot['counts']['read_resumed']>=1 and snapshot['counts']['pong_acknowledged']==1
                assert snapshot['pending_pings']==0 and snapshot['ping_timeout_seconds']==20
                for n in range(100):ws.record_transport('fixture',number=n)
                assert len(ws.diagnostic_snapshot()['timeline'])==64
            life.end('receiver_completed')
            assert life.metrics()['recent_closes'][-1]['transport']['counts']['connection_lost']==1
    asyncio.run(scenario())

def test_ongoing_writer_put_wait_is_visible_and_cleared():
    async def scenario():
        release=asyncio.Event();entered=asyncio.Event();ws=N();timings=ReceiveTimings()
        async def put(item):entered.set();await release.wait()
        task=asyncio.create_task(measured_writer_put(ws,N(put=put),None,timings))
        await entered.wait();assert ws.writer_put_started_at
        release.set();await task
        assert ws.writer_put_started_at is None and timings.metrics()['writer_wait_ms']['samples']==1
    asyncio.run(scenario())


def test_mismatched_observation_endpoint_cannot_attach_latest_identity():
    at=now_ms();d,f=trigger(at);store.persist([(IID,f,'visible')],d.catalog,at,d.config)
    with pipeline.Session.begin() as s:
        obs=s.scalar(select(Observation));obs.last_at=at+1000
        pipeline.synchronize(s,at+1001,d.config)
    with pipeline.Session() as s:
        source=s.scalar(select(AnalysisDemand)).payload['source']
        assert source['source_at']==at+1000 and source['trigger_event_id'] is None


def test_outer_measure_that_returns_connection_records_possession_overlap():
    pool=create_engine(pipeline.Session.kw['bind'].url);database.install(pool,'r4-outer')
    try:
        with database.measure('structure_prepare','r4-test'):
            with pool.connect() as c:
                c.execute(text('SELECT 1'));c.info['p40_checkout']-=11
        evidence=database.snapshot()['pools']['r4-outer']['long_holds'][-1]
        assert any(r['stage']=='structure_prepare:r4-test' for r in evidence['timeline'])
    finally:pool.dispose()


def test_operations_exports_oi_receipts_and_rotating_probe(monkeypatch):
    from fastapi.testclient import TestClient
    from services.api.main import app
    from engine.repository import write_heartbeat
    monkeypatch.setenv('ERJI_RUN_ID','r4-api');at=now_ms()
    with pipeline.Session.begin() as s:
        write_heartbeat(s,'anomaly_discovery',at,'partial',{'oi_receipts':{'examples':[{'instrument_id':IID}]},
            'oi_persisted_probe':{'snapshot_at':at,'endpoints':[]}})
    response=TestClient(app).get('/v1/operations');assert response.status_code==200
    details=next(r['details'] for r in response.json()['workers'] if r['role']=='anomaly_discovery')
    assert details['oi_receipts']['examples'][0]['instrument_id']==IID
    assert details['oi_persisted_probe']['snapshot_at']==at


def test_fence_hold_does_not_inherit_unrelated_reused_thread_stage():
    pool=create_engine(pipeline.Session.kw['bind'].url);database.install(pool,'fence-r4-stage-test')
    try:
        with database.measure('fence_acquire','structure'):
            c=pool.connect()
        with database.measure('mutation_stage','unrelated-query-thread-reuse'):pass
        c.info['p40_checkout']-=11;c.close()
        evidence=database.snapshot()['pools']['fence-r4-stage-test']['long_holds'][-1]
        assert any(r['stage']=='fence_acquire:structure' for r in evidence['timeline'])
        assert not any(r['stage']=='mutation_stage:unrelated-query-thread-reuse' for r in evidence['timeline'])
    finally:pool.dispose()


def test_session_commit_total_is_attached_to_query_hold():
    from sqlalchemy.orm import sessionmaker
    from engine.db import BoundedSession
    pool=create_engine(pipeline.Session.kw['bind'].url);database.install(pool,'r4-session-commit')
    factory=sessionmaker(bind=pool,class_=BoundedSession)
    try:
        with factory.begin() as s:
            s.execute(text('SELECT 1'));s.connection().info['p40_checkout']-=11
        evidence=database.snapshot()['pools']['r4-session-commit']['long_holds'][-1]
        assert any(r['stage']=='session_commit_total_including_guards' for r in evidence['timeline'])
    finally:pool.dispose()
