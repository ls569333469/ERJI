"""Native priority, fair incremental refill and the actual multi-slot dispatcher."""
import asyncio
import threading
from collections import deque
from types import SimpleNamespace as N
from sqlalchemy import select
from test_p39_deadlock_fix import independent_schema
from engine import repository
from engine.models import Asset,Instrument,Observation,UserPreference,WorkerHeartbeat,Task,Watchlist,Campaign,Episode,Plan
from engine.p39 import pipeline,priority
from engine.pd2 import collection_schedule as schedule
from engine.p38 import jobs
from engine.p37.detector import Detector
from engine.adapters.base import Batch,now_ms


def market(s,iid,at,asset=None):
    asset=asset or iid.rsplit(':',1)[-1]
    if not s.get(Asset,asset):s.add(Asset(id=asset,symbol=asset,known_at=at));s.flush()
    s.add(Instrument(id=iid,asset_id=asset,venue='binance',native_symbol=iid.rsplit(':',1)[-1],
        market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()


def test_native_tiers_do_not_expand_to_watchlists_old_candidates_or_sibling_contracts():
    at=now_ms();a='binance:perp:SHAREDUSDT';b='binance:perp:SHAREDUSDC';old='binance:perp:OLDUSDT'
    with repository.Session.begin() as s:
        market(s,a,at,'SHARED');market(s,b,at,'SHARED');market(s,old,at)
        s.add(Observation(id='real-trigger',instrument_id=a,asset_id='SHARED',direction='up',first_at=at,last_at=at,visible_at=at,status='observing',version=at,payload={}))
        s.add(Watchlist(asset_id='OLDUSDT',created_at=at))
        pipeline.upsert(s,a,'observation','real-trigger',at,at+60000,{'source_at':at})
        pipeline.upsert(s,old,'candidate','old-episode',at,payload={'threshold':'10'})
        s.merge(UserPreference(key='p35_background:'+old,updated_at=at,payload={'needs_trigger_data':True,'priority_until':at+60000,'trigger_inspection_reasons':['volume']}))
        s.merge(UserPreference(key='p37_active',updated_at=at,payload={'instrument_ids':[old]}))
    schedule._priority_cache.clear()
    result=schedule.priority_snapshot('binance','oi',at)
    assert result[-1][a.rsplit(':',1)[-1]]==60000
    assert result[-1][b.rsplit(':',1)[-1]]==300000 and result[-1][old.rsplit(':',1)[-1]]==300000
    with repository.Session.begin() as s:
        s.get(UserPreference,'p37_active').updated_at=at+1
    assert schedule.priority_snapshot('binance','oi',at+1) is result
    assert schedule.priority_snapshot('binance','oi',at+60000)[-1]['SHAREDUSDT']==300000


def test_valid_plan_and_open_position_are_t3_but_expired_plan_is_not():
    at=now_ms();iid='binance:perp:BTCUSDT'
    with repository.Session.begin() as s:
        s.add(Campaign(id='c',asset_id='BTC',reference_instrument_id=iid,first_seen_at=at,first_seen_price=100,state='active'));s.flush()
        s.add(Episode(id='e',campaign_id='c',strategy_id='test',phase='structure_pending',rule_version='test',evidence={}));s.flush()
        s.add(Plan(id='p',episode_id='e',execution_instrument_id=iid,revision=1,expires_at=at+10,payload={}))
    with repository.Session() as s:
        assert iid in priority.market_tiers(s,at)[1]
        assert iid not in priority.market_tiers(s,at+10)[1]
    with repository.Session.begin() as s:s.merge(UserPreference(key='research_portfolio',updated_at=at,payload={'positions':[{'instrument_id':iid,'state':'open'}]}))
    with repository.Session() as s:assert iid in priority.market_tiers(s,at+10)[1]


def test_waiting_and_queued_t2_keep_collection_priority_without_claiming_compute_slots():
    at=now_ms();ids=[f'binance:perp:T2WAIT{n}USDT' for n in range(3)]
    with repository.Session.begin() as s:
        for iid in ids:
            market(s,iid,at)
            asset=iid.rsplit(':',1)[-1]
            s.add(Observation(id='obs:'+iid,instrument_id=iid,asset_id=asset,direction='up',
                first_at=at-1800000,last_at=at-1800000,visible_at=at-1800000,status='observing',version=at,payload={}))
    chosen,_=pipeline.select_work(at,1,'structure',Detector().config)
    assert len(chosen)==1
    iid=chosen[0][1]
    context=pipeline.begin_result(iid,'structure','missing',{},at)
    pipeline.complete_result(context,{'waiting_inputs':True},at+1)
    replacement,_=pipeline.select_work(at+2,1,'structure',Detector().config)
    assert len(replacement)==1 and replacement[0][1]!=iid
    with repository.Session() as s:assert priority.market_tiers(s,at+2)[0]==set(ids)
    metrics=pipeline.capacity_metrics(at+2)
    assert metrics['t2_native_markets']==3 and metrics['arrival_markets_per_minute']==0


def test_persisted_fair_turn_serves_ordinary_when_only_one_slot_frees(monkeypatch):
    at=now_ms();clock=[at];hot=[f'H{n}USDT' for n in range(12)];cold=['COLDUSDT']
    monkeypatch.setattr(schedule,'now_ms',lambda:clock[0])
    monkeypatch.setattr(schedule,'priority_snapshot',lambda *a:({}, {},[N(native_symbol=n) for n in hot+cold],set(),set(),[],{n:60000 if n in hot else 300000 for n in hot+cold}))
    got=[]
    for n in range(4):
        chosen=schedule.due('binance',hot+cold,limit=1,owner=str(n));got+=chosen
        schedule.acknowledge('binance',chosen,owner=str(n),at=clock[0]);clock[0]+=1
    assert len(got)==4 and got[-1]=='COLDUSDT' and all(n in hot for n in got[:3])


def test_coalesced_oi_without_catalog_rows_acknowledges_every_owner(monkeypatch):
    from engine.pd2.write_queue import combine_batches
    at=now_ms();names=['XUSDT','YUSDT']
    monkeypatch.setattr(schedule,'priority_snapshot',lambda *a:({}, {},[],set(),set(),[],{n:300000 for n in names}))
    for n in names:assert schedule.due('binance',[n],owner=n)==[n]
    combined=combine_batches([Batch('binance',states=[{'instrument_id':'binance:perp:'+n,'oi_status':'fresh'}],
        metadata={'collection_symbols':{'binance:perp:'+n:n},'collection_claims':{n:n}}) for n in names])
    schedule.acknowledge_batch(combined)
    with repository.Session() as s:
        p=s.get(UserPreference,'due:binance:oi').payload
        assert set(p['visited'])==set(names) and not p['inflight']


def test_shared_heartbeat_first_insert_is_atomic_and_cannot_regress():
    from engine.repository import write_heartbeat
    at=now_ms();barrier=threading.Barrier(4);errors=[]
    def write(n):
        try:
            barrier.wait(5)
            with repository.Session.begin() as s:write_heartbeat(s,'first-concurrent',at+n,'ok',{'n':n})
        except BaseException as e:errors.append(e)
    threads=[threading.Thread(target=write,args=(n,)) for n in range(4)]
    for t in threads:t.start()
    for t in threads:t.join(10)
    assert not errors and all(not t.is_alive() for t in threads)
    with repository.Session() as s:assert s.get(WorkerHeartbeat,'first-concurrent').updated_at==at+3


def test_health_snapshot_uses_current_clock_and_evaluates_outside_ingestion_lock(monkeypatch):
    from engine.p37.worker import discovery_health_snapshot
    from engine.p37.detector import PriceView
    d=Detector();at=now_ms();iid='snapshot';d.catalog={iid:{'symbol':'S','liquidity':2000000,'state':{}}}
    d.prices[iid]=deque((t,'100') for t in range(at-900000,at+1,1000))
    original=PriceView.delta;calls=[]
    def checked(self,now,window):
        assert not d.lock._is_owned();calls.append(window)
        return original(self,now,window)
    monkeypatch.setattr(PriceView,'delta',checked)
    result=discovery_health_snapshot(d,lambda:at)
    assert result['ready']==result['ready15']==1 and calls==[300000,900000]
    assert result['coverage']['15']['ready']==1 and result['snapshot_at']==at


def test_real_dispatcher_uses_two_slots_per_analysis_lane(monkeypatch):
    at=now_ms();ids=[f'binance:perp:SLOT{n}USDT' for n in range(2)]
    with repository.Session.begin() as s:
        for iid in ids:
            market(s,iid,at)
            pipeline.upsert(s,iid,'manual','request:'+iid,at)
        for kind in ('trend','structure'):s.merge(UserPreference(key='p39_'+kind+'_active',updated_at=at,payload={'instrument_ids':ids}))
    for kind in ('structure','trend'):jobs.submit(ids,'observation',at=at,work_kind=kind)
    targets={'protect':[],'entry':[],'projection':[],'deep':ids,'structure':ids,'trend':ids}
    monkeypatch.setattr(jobs,'managed_targets',lambda:targets)
    active={'structure':0,'trend':0};peak=dict(active)
    async def exercise():
        release=asyncio.Event()
        async def execute(lease,adapter):
            kind=lease['work_kind'];active[kind]+=1;peak[kind]=max(peak[kind],active[kind])
            if sum(active.values())==4:release.set()
            await asyncio.wait_for(release.wait(),3)
            assert await asyncio.to_thread(jobs.finish,lease)
            active[kind]-=1
        monkeypatch.setattr(jobs,'execute',execute)
        await asyncio.wait_for(jobs.run(once=True),10)
    asyncio.run(exercise())
    assert peak=={'structure':2,'trend':2}
    with repository.Session() as s:assert len(list(s.scalars(select(Task).where(Task.state=='complete'))))==4


def test_queued_commit_renews_collection_claim_after_http_slot_was_released(monkeypatch):
    from engine.pd2.write_queue import Writer
    at=now_ms();clock=[at];native='SLOWCOMMITUSDT'
    monkeypatch.setattr(schedule,'now_ms',lambda:clock[0])
    monkeypatch.setattr(schedule,'priority_snapshot',lambda *a:({}, {},[],set(),set(),[],{native:300000}))
    assert schedule.due('binance',[native],owner='queued-owner')==[native]
    async def exercise():
        writer=Writer(None);writer.inflight=[(Batch('binance',metadata={'collection_claims':{native:'queued-owner'}}),0)]
        clock[0]=at+60000
        task=asyncio.create_task(writer.renew_collections())
        for _ in range(100):
            with repository.Session() as s:until=s.get(UserPreference,'due:binance:oi').payload['inflight'][native]['until']
            if until==clock[0]+120000:break
            await asyncio.sleep(.01)
        else:raise AssertionError('queued claim was not renewed')
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        clock[0]=at+130000
        assert schedule.due('binance',[native],owner='replacement')==[]
    asyncio.run(exercise())
