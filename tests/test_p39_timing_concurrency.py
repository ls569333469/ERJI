import asyncio
import threading
from types import SimpleNamespace as N
from decimal import Decimal
from sqlalchemy import select
from test_p39_deadlock_fix import independent_schema
from engine.p38 import jobs
from engine.pd2 import collection_schedule as schedule
from engine.models import UserPreference,Asset,Instrument,Campaign,Episode,Plan,Task
from engine.adapters.base import now_ms

IID='binance:perp:BTCUSDT'

def test_real_concurrent_collection_claims_have_one_owner(monkeypatch):
    now=now_ms();natives=['S'+str(n)+'USDT' for n in range(20)]
    monkeypatch.setattr(schedule,'priority_snapshot',lambda *args:({}, {}, [],set(),set(),[],{n:300000 for n in natives}))
    barrier=threading.Barrier(4);claimed=[];errors=[]
    def worker(owner):
        try:
            barrier.wait(4);claimed.extend((n,owner) for n in schedule.due('binance',natives,owner=owner))
        except BaseException as e:errors.append(e)
    threads=[threading.Thread(target=worker,args=(str(n),)) for n in range(4)]
    for t in threads:t.start()
    for t in threads:t.join(10)
    assert not errors and all(not t.is_alive() for t in threads)
    assert len(claimed)==20 and len({n for n,_ in claimed})==20
    with schedule.Session() as s:
        before=s.get(UserPreference,'due:binance:oi').payload
        assert not before['retries']
        assert {n:p['owner'] for n,p in before['inflight'].items()}==dict(claimed)

def test_structure_progresses_during_same_market_entry_network_wait(monkeypatch):
    from engine import repository,signal_service
    from engine.pd2 import service,market_selection
    from engine.p39 import pipeline
    at=now_ms()
    with jobs.Session.begin() as s:
        s.add(Campaign(id='timing-campaign',asset_id='BTC',reference_instrument_id=IID,first_seen_at=at,first_seen_price=Decimal(100),state='active'));s.flush()
        s.add(Episode(id='timing-episode',campaign_id='timing-campaign',strategy_id='probe',phase='early_trigger',rule_version='timing-test',evidence={}));s.flush()
        s.add(Plan(id='timing-plan',episode_id='timing-episode',execution_instrument_id=IID,revision=1,expires_at=at+600000,payload={}));s.flush()
        pipeline.upsert(s,IID,'manual','timing-test',at,payload={})
        s.merge(UserPreference(key='p39_structure_active',updated_at=at,payload={'instrument_ids':[IID]}))
    calls=[]
    monkeypatch.setattr(repository,'runtime_config',lambda:N(collector_enabled=True))
    monkeypatch.setattr(market_selection,'select_main',lambda *args:None)
    monkeypatch.setattr(signal_service,'prepare_scan',lambda ids:{'test_stub':True})
    monkeypatch.setattr(signal_service,'scan_signals',lambda ids,**kwargs:{'scanned':1})
    monkeypatch.setattr(service,'refresh_plans',lambda *args,**kwargs:{'snapshots':0})
    monkeypatch.setattr(service,'refresh_current',lambda ids:calls.append('entry_refresh'))
    monkeypatch.setattr(service,'decision_list',lambda **kwargs:calls.append('publish'))
    async def exercise():
        entered=asyncio.Event();release=asyncio.Event()
        class Adapter:
            async def collect(self,iid):entered.set();await release.wait()
        adapter=Adapter()
        jobs.submit([IID],at=at,work_kind='entry');entry=jobs.take(at,work_kinds=['entry'])
        pending=asyncio.create_task(jobs.execute(entry,adapter))
        try:
            await asyncio.wait_for(entered.wait(),2)
            jobs.submit([IID],at=at,work_kind='structure');structure=jobs.take(at,work_kinds=['structure'])
            await asyncio.wait_for(jobs.execute(structure,adapter),3)
            with jobs.Session() as s:assert s.get(Task,structure['id']).state=='complete'
            assert not pending.done() and calls==['publish']
        finally:
            release.set();await asyncio.wait_for(pending,3)
        assert 'entry_refresh' in calls
    asyncio.run(exercise())
