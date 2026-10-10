"""P40 regressions: disposable PostgreSQL only; no exchange or production writes."""
import asyncio
import threading
from collections import deque
import pytest
from sqlalchemy import select,text
from test_p39_deadlock_fix import independent_schema
from engine import repository
from engine.models import Task,AnalysisResult,MarketAnalysisState,CurrentSignal,Observation,WorkerHeartbeat
from engine.adapters.base import now_ms
from engine.p37.detector import Detector
from engine.p38 import jobs
from engine.p39 import pipeline
from engine.p40.diagnostics import window_metrics,window_status,scan_view,run_scope

IID='binance:perp:BTCUSDT'

def test_oi_window_is_independent_of_price_gaps_and_keeps_percent_units():
    at=now_ms();d=Detector()
    d.catalog={IID:{'asset_id':'BTC','symbol':'BTCUSDT','venue':'binance','liquidity':2000000}}
    d.prices[IID]=deque([(at-900000,'100'),(at,'110')])
    d.oi[IID]=deque([(at-900000,'100'),(at,'106')])
    price,oi=window_metrics(d.catalog,d.prices,d.oi,d.config,at)
    assert price['15']['ready']==0 and price['15']['unavailable']=={'interval_gap':1}
    assert oi['15']['ready']==1 and oi['15']['eligible']==1
    reasons=d.facts(IID,at)['reasons']
    assert [r['kind'] for r in reasons]==['oi'] and reasons[0]['value']==6.0
    assert reasons[0]['window']==15 and reasons[0]['metric_unit']=='percent'

@pytest.mark.parametrize('rows,at,expected',[
    ([],1000000,'latest_missing'),([(1000001,'100')],1000000,'clock_invalid'),
    ([(900000,'100')],1000000,'latest_stale'),([(1000000,'100')],1000000,'history_missing'),
    ([(680000,'100'),(1000000,'110')],1000000,'history_mismatch')])
def test_window_missing_reasons(rows,at,expected):
    assert window_status(rows,at,300000)==expected

def test_oi_endpoint_90s_and_6m_collection_coverage_are_distinct():
    at=now_ms();meta={IID:{'venue':'binance','liquidity':2000000},'spot':{'venue':'binance_spot','liquidity':2000000}}
    d=Detector();oi={IID:[(at-960001,'100'),(at-60001,'106')]}
    _,result=window_metrics(meta,{},oi,d.config,at)
    assert result['15']['ready']==1 and result['15']['eligible']==1
    _,result=window_metrics(meta,{},oi,d.config,at+30000)
    assert result['15']['ready']==0 and result['15']['latest_within_6m']==1

def test_scan_legacy_timeout_cannot_become_current_event_error():
    v=scan_view({'reason':'TimeoutError','last_full_scan_at':1,'scanned':200,
        'event_scan':{'reason':'ok','at':100,'counts':{'scanned':1}}})
    assert v['reason']=='ok' and 'scanned' not in v
    assert v['legacy_reconcile']['last_full_scan_at']==1
    assert run_scope({'run_id':'old'},'new')=='previous_run'
    assert run_scope({'run_id':'new'},'new')=='current_run'
    assert run_scope({},'new')=='unattributed'

def test_uninitialised_websocket_close_resolves_waiter_without_masking_error():
    from websockets.client import ClientProtocol
    from websockets.uri import parse_uri
    from engine.p40.streams import ReliableClientConnection
    async def scenario():
        c=ReliableClientConnection(ClientProtocol(parse_uri('wss://example.invalid')))
        error=ConnectionResetError('test TLS aborted')
        assert not hasattr(c,'recv_messages')
        c.connection_lost(error)
        assert c.connection_lost_waiter.done() and c.recv_exc is error
        await asyncio.wait_for(c.wait_closed(),1)
    asyncio.run(scenario())

def test_subscription_change_keeps_global_and_unchanged_receiver_alive():
    from engine.p40.receivers import reconcile
    async def scenario():
        tasks={};closed=[]
        async def receive(key):
            try:await asyncio.Event().wait()
            finally:closed.append(key)
        await reconcile(tasks,[('global',0),('market',0)],receive);await asyncio.sleep(0)
        global_task=tasks[('global',0)];market_task=tasks[('market',0)]
        await reconcile(tasks,[('global',0),('market',0),('market',1)],receive)
        assert tasks[('global',0)] is global_task and tasks[('market',0)] is market_task
        await asyncio.sleep(0);await reconcile(tasks,[('global',0)],receive)
        assert not global_task.done() and ('market',0) in closed and ('market',1) in closed
        await reconcile(tasks,[],receive)
    asyncio.run(scenario())

def test_dedicated_pool_reuses_backend_and_clears_reentrant_locks_on_error():
    from engine.p38.fences import make_engine
    from engine.db import engine
    pool=make_engine(engine.url)
    try:
        with pytest.raises(RuntimeError):
            with pool.connect() as c:
                pid=c.scalar(text('SELECT pg_backend_pid()'))
                c.execute(text('SELECT pg_advisory_lock(7654321)'));c.execute(text('SELECT pg_advisory_lock(7654321)'))
                raise RuntimeError('synthetic task failure')
        with pool.connect() as c:
            assert c.scalar(text('SELECT pg_backend_pid()'))==pid
            assert not c.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory')"))
            c.invalidate()
        with pool.connect() as c:assert c.scalar(text('SELECT pg_backend_pid()'))!=pid
    finally:pool.dispose()

def test_cancelled_fence_acquisition_releases_locks_before_reuse():
    from engine.p38.fences import make_engine
    from engine.p38.lifecycle import acquire_thread
    from engine.db import engine
    pool=make_engine(engine.url);ready=threading.Event();release=threading.Event()
    def acquire():
        c=pool.connect();c.execute(text('SELECT pg_advisory_lock(7654322)'));ready.set();assert release.wait(3);return c
    async def scenario():
        task=asyncio.create_task(acquire_thread(acquire,lambda c:c.close()))
        assert await asyncio.to_thread(ready.wait,2)
        task.cancel();release.set()
        with pytest.raises(asyncio.CancelledError):await task
        with pool.connect() as c:
            assert not c.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory')"))
    try:asyncio.run(scenario())
    finally:release.set();pool.dispose()

def test_task_deferral_keeps_prior_strategy_result_and_is_not_failed():
    at=now_ms();context=pipeline.begin_result(IID,'structure','v',{},at)
    with repository.Session.begin() as s:s.get(MarketAnalysisState,(IID,'structure')).strategy_result='plan_created'
    pipeline.complete_result(context,{},at+1,'owner_still_running')
    with repository.Session() as s:
        assert s.get(AnalysisResult,context['result_id']).payload['status']=='deferred'
        state=s.get(MarketAnalysisState,(IID,'structure'))
        assert state.analysis_status=='deferred' and state.strategy_result=='plan_created'
        assert state.next_check_at==at+251

def test_heavy_projection_cannot_claim_market_with_due_entry():
    at=now_ms();jobs.submit([IID],at=at,work_kind='projection');jobs.submit([IID],at=at,work_kind='entry')
    assert jobs.take(at,work_kinds=['projection']) is None
    lease=jobs.take(at,work_kinds=['entry']);assert lease
    assert jobs.finish(lease,at=at+1)
    assert jobs.take(at+2,work_kinds=['projection'])

def test_fence_connection_failure_is_recorded_and_dispatcher_survives(monkeypatch):
    from engine.p38 import fences
    at=now_ms();jobs.submit([IID],at=at,work_kind='projection');lease=jobs.take(at,work_kinds=['projection'])
    def fail():raise OSError('synthetic unavailable database')
    monkeypatch.setattr(fences,'connect',fail)
    asyncio.run(jobs.execute(lease,None))
    with repository.Session() as s:
        row=s.get(Task,lease['id']);assert row.state=='failed' and row.payload['error']=='OSError'

def test_unique_qualified_event_commits_observation_and_exact_demand():
    from engine.p37.store import persist
    from engine.p39.diagnostics import trace
    at=now_ms();d=Detector();meta={IID:{'asset_id':'BTC','symbol':'BTCUSDT','venue':'binance','liquidity':2000000}}
    d.catalog=meta;d.oi[IID]=deque([(at-900000,'100'),(at,'106')]);f=d.facts(IID,at)
    metrics={};assert persist([(IID,f,'visible'),(IID,f,'visible')],meta,at,d.config,delivery=metrics)==1
    assert metrics['unique_events']==metrics['qualified_events']==metrics['accounted_qualified']==1
    assert metrics['unaccounted']==0 and metrics['accounted_rate']==1
    causal=trace(IID)
    assert causal['observations'][0]['event_id']==metrics['events'][0]['event_id']
    assert causal['demands'][0]['payload']['source']['trigger_event_id']==metrics['events'][0]['event_id']
    again={};assert persist([(IID,f,'visible')],meta,at+1,d.config,delivery=again)==0
    assert again['already_covered']==1 and again['unaccounted']==0

def test_write_heartbeat_has_current_run_identity_for_direct_callers(monkeypatch):
    monkeypatch.setenv('ERJI_RUN_ID','p40-test-run')
    with repository.Session.begin() as s:repository.write_heartbeat(s,'signals',now_ms(),'ok',{'event_scan':{'reason':'ok'}})
    with repository.Session() as s:assert s.get(WorkerHeartbeat,'signals').details['run_id']=='p40-test-run'

def test_diagnostic_quote_age_does_not_relax_spread_or_cost_policy():
    from engine.pd2.decision import execution_cost
    at=now_ms();book={'observed_at':at-60000,'received_at':at-60000,'scope':'execution_market',
        'asks':[['100','1000']],'bids':[['99.9','1000']]}
    ok=execution_cost('100','95','101',book,at,fee_rate='0.01',funding_cost_rate='0.01')
    assert ok['status']=='eligible' and ok['cost_policy']=='reference-only-v1'
    book['bids']=[['90','1000']]
    assert execution_cost('100','95','101',book,at)['status']=='wide_spread'
    book['observed_at']=at-90001
    assert execution_cost('100','95','101',book,at)['status']=='stale'

def test_recognition_runs_after_read_connection_is_returned_and_is_reused(monkeypatch):
    from types import SimpleNamespace as N
    from test_p33_patterns import base,NOW
    from engine.strategies import p33_runtime as runtime
    from engine.models import UserPreference
    from engine.db import engine
    instrument=N(id='p40:recognition',spec={'tick_size':'.01'})
    original=runtime.evaluate;calls=[]
    def checked(*args,**kwargs):
        assert engine.pool.checkedout()==0
        calls.append('outside');return original(*args,**kwargs)
    monkeypatch.setattr(runtime,'evaluate',checked)
    prepared=runtime.prepare_recognition(instrument,base(),NOW,None)
    assert calls==['outside']
    def forbidden(*args,**kwargs):raise AssertionError('recognition repeated inside mutation transaction')
    monkeypatch.setattr(runtime,'evaluate',forbidden)
    with repository.Session.begin() as s:
        runtime.observe(s,instrument,base(),NOW,prepared=prepared)
        s.flush();assert s.get(UserPreference,'p33_shadow:'+instrument.id).payload['result']==prepared['result']

def test_recognition_revalidates_changed_checkpoint_instead_of_using_prepared_result(monkeypatch):
    from types import SimpleNamespace as N
    from test_p33_patterns import base,NOW
    from engine.strategies import p33_runtime as runtime
    from engine.models import UserPreference
    instrument=N(id='p40:changed',spec={'tick_size':'.01'})
    prepared=runtime.prepare_recognition(instrument,base(),NOW,None)
    with repository.Session.begin() as s:s.add(UserPreference(key='p33_shadow:'+instrument.id,updated_at=NOW,payload={'input_signature':'changed'}))
    calls=[];original=runtime.evaluate
    def checked(*args,**kwargs):calls.append('fallback');return original(*args,**kwargs)
    monkeypatch.setattr(runtime,'evaluate',checked)
    with repository.Session.begin() as s:runtime.observe(s,instrument,base(),NOW,prepared=prepared)
    assert calls==['fallback']
