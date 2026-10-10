"""Causal replay, concurrent claim ownership, and current opportunity clocks."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace as N
import pytest
from sqlalchemy import select
from engine.p39 import timing
from engine.p37.detector import Detector,PriceView
from engine.p38.observation_query import clock_fields
from engine.repository import merge_market_payload
from engine.pd2 import collection_schedule as schedule
from engine.pd2.write_queue import combine_batches
from engine.adapters.base import Batch
from test_p25_signals import isolated

NOW=2_000_000_000_000
IID='binance:perp:TIMINGUSDT'

def memory():
    d=Detector();d.catalog={IID:dict(asset_id='TIMING',venue='binance',symbol='TIMINGUSDT',liquidity=2_000_000,state={})}
    start=NOW-1200000;d.remember_context(start)
    for at in range(start,NOW+1,1000):
        d.price(IID,at,'105' if at==NOW-120000 else '100','mark')
    return d

def test_fixed_phase_does_not_accumulate_completion_delay():
    phase=timing.phase('TIMINGUSDT');boundary=timing.cycle(NOW)+phase
    assert not timing.due_at('TIMINGUSDT',boundary+20000,boundary+299999,300000)
    assert timing.due_at('TIMINGUSDT',boundary+20000,boundary+300000,300000)
    assert timing.phase('TIMINGUSDT')==phase

def test_replay_records_a_peak_that_ended_between_two_scans():
    d=memory();assert not d.evaluate(NOW)
    events=d.evaluate_since({IID:NOW-300000},NOW)
    first=next(f for _,f,_ in events if any(r['kind']=='price' and r['direction']=='up' for r in f['reasons']))
    assert first['replayed'] and first['basis_at']==NOW-120000
    assert first['basis_price']=='105'
    assert first['classification_context']['btc_windows']=={}

def test_gap_and_late_received_facts_do_not_fabricate_a_window():
    d=memory()
    d.prices[IID]=type(d.prices[IID])([p for p in d.prices[IID] if not NOW-140000<p[0]<NOW-121000],maxlen=2100)
    events=d.evaluate_since({IID:NOW-300000},NOW)
    assert not any(r['direction']=='up' for _,f,_ in events for r in f['reasons'])
    d=memory();d.price_known[IID]=type(d.price_known[IID])([(at,NOW+1 if at==NOW-120000 else at) for at,_ in d.price_known[IID]],maxlen=2100)
    assert not any(r['direction']=='up' for _,f,_ in d.evaluate_since({IID:NOW-300000},NOW) for r in f['reasons'])

def test_historical_context_cannot_borrow_current_liquidity():
    d=memory();at,meta,cfg=d.contexts[0];meta[IID]['liquidity']=None
    d.catalog[IID]['liquidity']=2_000_000;d.remember_context(NOW)
    assert not any(f.get('replayed') for _,f,_ in d.evaluate_since({IID:NOW-300000},NOW))

@pytest.mark.parametrize('gap',[False,True])
def test_indexed_windows_match_original_continuity_rules(gap):
    rows=[(t,'100' if t<900000 else '110') for t in range(0,1200001,1000) if not gap or not 800000<t<830000]
    v=PriceView(rows,{})
    for at in range(900000,1200001,5000):
        subset=[r for r in rows if r[0]<=at]
        for window in (300000,900000):assert v.delta(at,window)==Detector.delta(subset,at,window)

def test_current_buy_ignores_old_tracking_cache_but_preserves_zone_and_expiry():
    row=dict(instrument_id=IID,status='observing',visible_at=NOW-900000,version=NOW-900000,tracked_at=NOW-300000,buy_eligible=False,price_at=NOW-300000,analysis_checked_at=NOW-700000,analyzed_at=NOW-700000,analysis_status='completed')
    state=N(as_of=NOW-20000,payload={'price':'10','price_kind':'mark'})
    signal=N(action='trial',valid_until=NOW+10000,payload={'entry_zone':['9','11'],'protection':'8'})
    result=clock_fields(row,state,NOW,[signal])
    assert result['buy_eligible'] and result['analysis_status']=='completed'
    state.payload['price']='12';assert not clock_fields(row,state,NOW,[signal])['buy_eligible']
    state.payload['price']='10';signal.valid_until=NOW;assert not clock_fields(row,state,NOW,[signal])['buy_eligible']
    signal.valid_until=NOW+10000;state.as_of=NOW-90001;assert not clock_fields(row,state,NOW,[signal])['buy_eligible']

def test_current_status_and_counts_agree_when_source_clock_is_invalid(isolated,monkeypatch):
    from test_p25_signals import seed
    from engine.models import Observation,MarketState
    from engine.p38 import observation_query as query
    monkeypatch.setattr(query.time,'time',lambda:NOW/1000)
    seed(isolated)
    iid='binance:perp:P25TEST0USDT'
    with isolated.begin() as s:
        s.add(Observation(id='timing-clock',instrument_id=iid,asset_id='P25TEST0',direction='up',first_at=NOW-20000,last_at=NOW-20000,visible_at=NOW-20000,status='observing',version=NOW,payload={'price':'1','price_at':NOW-20000,'tracked_at':NOW-20000,'buy_eligible':False}))
        state=s.get(MarketState,iid);state.as_of=NOW+1
    result=query.read_observations()
    assert result['rows'][0]['data_status']=='delayed' and result['counts']['delayed']==1 and result['counts']['active']==0
    with isolated.begin() as s:
        s.get(MarketState,iid).as_of=NOW
        row=s.get(Observation,'timing-clock');row.payload={**row.payload,'price_at':NOW+1000,'price':'999'}
    result=query.read_observations()
    assert result['rows'][0]['data_status']=='fresh' and result['counts']['delayed']==0
    assert result['rows'][0]['price']=='1' and result['rows'][0]['buy_eligible'] and result['counts']['active']==1

def test_reference_oi_is_distinct_from_a_fresh_trigger():
    from engine.pd2.current_market_fields import oi_clock
    d=memory();d.oi_point(IID,NOW-950000,'100');d.oi_point(IID,NOW-50000,'110')
    assert d.facts(IID,NOW)['oi_change']==10
    assert oi_clock({'oi_as_of':NOW-300000,'oi_base':'100'},NOW)=='fresh'
    assert d.facts(IID,NOW+50000)['oi_change'] is None
    assert oi_clock({'oi_as_of':NOW-600001,'oi_base':'100'},NOW)=='stale'

def test_frozen_config_preserved_while_current_engineering_ttl_changes():
    frozen={'quote_max_age_seconds':15,'depth_max_age_seconds':10,'confirmed_min_net_r':1.8};before=deepcopy(frozen)
    current=timing.current_execution(frozen)
    assert frozen==before and current.quote_max_age_seconds==90 and current.depth_max_age_seconds==90
    assert current.confirmed_min_net_r==1.8

def test_volume_and_oi_same_write_queue_batch_keep_independent_fields():
    a=Batch('binance',states=[dict(instrument_id=IID,as_of=NOW,price='10',price_kind='mark',oi_as_of=NOW,oi_base='20',oi_status='fresh')],metadata={'collection_claims':{'TIMINGUSDT':'a'}})
    b=Batch('binance',states=[dict(instrument_id=IID,as_of=0,volume_24h_quote='2000',volume_24h_source_at=NOW+1000,volume_24h_received_at=NOW+2000)],metadata={'collection_claims':{'OTHERUSDT':'b'}})
    combined=combine_batches([a,b]);merged={}
    for row in combined.states:merged=merge_market_payload(merged,row)
    assert merged['price']=='10' and merged['oi_base']=='20' and merged['volume_24h_quote']=='2000'
    assert merged['volume_24h_source_at']==NOW+1000 and combined.metadata['collection_claims']=={'TIMINGUSDT':'a','OTHERUSDT':'b'}

def test_owned_claim_renewal_failure_and_late_ack(isolated,monkeypatch):
    from engine.models import UserPreference
    monkeypatch.setattr(schedule,'now_ms',lambda:NOW)
    monkeypatch.setattr(schedule,'priority_snapshot',lambda *a:({}, {}, [], set(),set(),[],{'TIMINGUSDT':300000}))
    assert schedule.due('binance',['TIMINGUSDT'],owner='old')==['TIMINGUSDT']
    assert not schedule.due('binance',['TIMINGUSDT'],owner='new')
    schedule.update_claims('binance',['TIMINGUSDT'],'old',at=NOW+60000)
    monkeypatch.setattr(schedule,'now_ms',lambda:NOW+130000)
    assert not schedule.due('binance',['TIMINGUSDT'],owner='new')
    monkeypatch.setattr(schedule,'now_ms',lambda:NOW+181000)
    assert schedule.due('binance',['TIMINGUSDT'],owner='new')==['TIMINGUSDT']
    schedule.acknowledge('binance',['TIMINGUSDT'],at=NOW+181001,owner='old')
    with isolated() as s:
        row=s.get(UserPreference,'due:binance:oi');assert 'TIMINGUSDT' not in row.payload['visited']
    schedule.update_claims('binance',['TIMINGUSDT'],'new',failed=True,at=NOW+181002)
    with isolated() as s:
        row=s.get(UserPreference,'due:binance:oi');assert row.payload['retries']['TIMINGUSDT']['failures']==1 and not row.payload['inflight']

def test_priority_upgrade_is_immediate_and_deduplicated(isolated,monkeypatch):
    from engine.models import UserPreference
    monkeypatch.setattr(schedule,'now_ms',lambda:NOW)
    with isolated.begin() as s:s.merge(UserPreference(key='due:binance:oi',updated_at=NOW,payload={'visited':{'TIMINGUSDT':NOW-1000},'intervals':{'TIMINGUSDT':300000}}))
    monkeypatch.setattr(schedule,'priority_snapshot',lambda *a:({}, {}, [],set(),set(),[],{'TIMINGUSDT':60000}))
    assert schedule.due('binance',['TIMINGUSDT'],owner='upgraded')==['TIMINGUSDT']
    assert not schedule.due('binance',['TIMINGUSDT'],owner='duplicate')

def test_dispatch_refills_immediately_while_slow_peer_is_running():
    from engine.pd2.continuous_collection import dispatch
    async def exercise():
        order=[];remaining=['slow','fast','next'];release=asyncio.Event()
        async def choose(n):
            chosen=remaining[:n];del remaining[:n];return chosen
        async def execute(key):
            if key=='slow':await release.wait()
            order.append(key)
            if key=='next':release.set()
        async def enabled():return len(order)<3
        await asyncio.wait_for(dispatch(choose,execute,enabled,2),1)
        assert order==['fast','next','slow']
    asyncio.run(exercise())

def test_wakeup_preserves_notification_before_wait_and_from_a_thread():
    async def exercise():
        wake=timing.Wakeup();wake.notify();await asyncio.wait_for(wake.wait(5),.2)
        waiter=asyncio.create_task(wake.wait(5));await asyncio.sleep(0)
        await asyncio.to_thread(wake.notify);await asyncio.wait_for(waiter,.2)
    asyncio.run(exercise())

def test_volume_refresh_once_per_grid_with_actual_exchange_time(monkeypatch):
    from engine.adapters import binance_universe as adapter_module
    adapter=adapter_module.BinanceUniverseAdapter();adapter.selection=[{'symbol':'TIMINGUSDT'}];calls=[]
    clock=[NOW];monkeypatch.setattr(adapter_module,'now_ms',lambda:clock[0])
    async def request(batch,*a,**k):calls.append(1);return [{'symbol':'TIMINGUSDT','quoteVolume':'123','closeTime':clock[0]-37}], 'ref',clock[0]+23
    monkeypatch.setattr(adapter,'request',request)
    async def exercise():
        b=await adapter.refresh_volumes();assert b.states[0]['volume_24h_source_at']==NOW-37
        assert b.states[0]['volume_24h_received_at']==NOW+23
        clock[0]+=1000;assert await adapter.refresh_volumes() is None
        clock[0]=timing.cycle(NOW)+300000;assert await adapter.refresh_volumes()
        assert len(calls)==2
        await adapter.close()
    asyncio.run(exercise())
