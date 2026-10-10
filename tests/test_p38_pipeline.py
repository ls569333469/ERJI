import asyncio,time,json
from types import SimpleNamespace as N
from decimal import Decimal as D
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,delete,event
from sqlalchemy.orm import sessionmaker
from engine.db import engine
from engine.models import Observation,Task,MarketState,Candle,RawEvent,Instrument,Asset
from engine.p37.detector import Detector,DEFAULTS
from engine.p37 import store
from engine.p38 import jobs,candle_gaps,observation_query,configuration,diagnostics
from engine.pd2.budget_owner import recover
from engine.pd2 import p36_budget as budget
from engine.pd2.native_streams import LatestQuotes
from engine.features.numeric import relative_volume
from test_p25_signals import isolated

def observed(ident,asset,at,**payload):
    return Observation(id=ident,asset_id=asset,instrument_id='binance:perp:'+asset+'USDT',direction='up',first_at=at,visible_at=at,last_at=at,status='observing',version=at,payload={'symbol':asset+'USDT',**payload})

def test_frozen_trigger_basis_does_not_use_catalog_or_later_price(isolated):
    now=int(time.time()*1000);iid='binance:perp:BASISUSDT';meta={iid:dict(asset_id='BASIS',symbol='BASISUSDT',state={'price':'100'},venue='binance',liquidity=2e6)}
    detector=Detector();detector.catalog=meta
    for at in range(now-300000,now+1,1000):detector.price(iid,at,'110' if at==now else '100')
    events=detector.evaluate(now);assert events[0][1]['basis_price']=='110'
    detector.price(iid,now+1000,'120');meta[iid]['state']['price']='999'
    store.persist(events,meta,now,DEFAULTS)
    with isolated() as s:
        row=s.scalar(select(Observation).where(Observation.asset_id=='BASIS'))
        assert row.payload['visible_price']=='110' and row.payload['visible_basis']['at']==now
    store.persist(events,meta,now+2000,DEFAULTS)
    with isolated() as s:assert s.get(Observation,row.id).payload['visible_price']=='110'

def test_oi_without_fresh_source_keeps_missing_basis(isolated):
    now=int(time.time()*1000);iid='binance:perp:BASISUSDT';d=Detector();d.catalog={iid:{}}
    d.price(iid,now-20000,'100');d.oi_point(iid,now-900000,'100');d.oi_point(iid,now,'110')
    f=d.facts(iid,now);assert f['basis_price'] is None
    meta={iid:dict(asset_id='BASIS',symbol='BASISUSDT',state={'price':'100'})}
    store.persist([(iid,f,'visible')],meta,now,DEFAULTS)
    with isolated() as s:assert s.scalar(select(Observation)).payload['visible_price'] is None

@pytest.mark.parametrize('age,tier',[(20000,30000),(30000,30000),(300000,300000)])
def test_snapshot_clock_is_separate_and_does_not_grant_buy(age,tier):
    now=1000000;row=dict(id='x',visible_at=now-age,version=now-age,status='cooling',price='100',price_at=now-age,tracked_at=now-age,tracking_interval_ms=tier,buy_eligible=True)
    fresh=N(as_of=now,payload={'price':'110','price_kind':'mark'})
    r=observation_query.clock_fields(row,fresh,now)
    assert r['data_status']=='fresh' and r['source_age_ms']==0
    assert r['snapshot_age_ms']==age and r['snapshot_status']=='sampled' and not r['buy_eligible']
    r=observation_query.clock_fields(row,N(as_of=now+1,payload={'price':'110'}),now)
    assert r['data_status']=='delayed' and not r['buy_eligible']

def test_jobs_running_version_keeps_new_demand_and_owner_fence(isolated):
    at=2000000000000;jobs.submit(['x','y'],at=at);a=jobs.take(at)
    assert a and jobs.take(at)['instrument_id']!=a['instrument_id']
    from engine.models import UserPreference
    with isolated.begin() as s:s.merge(UserPreference(key='p37_config',updated_at=at+1,payload={}))
    jobs.submit([a['instrument_id']],at=at+3000)
    assert jobs.finish(a,at=at+4000)
    b=jobs.take(at+4000);assert b['instrument_id']==a['instrument_id'] and b['version']!=a['version']
    assert not jobs.finish(a,at=at+5000)
    jobs.finish(b,'TimeoutError',at=at+5000);assert jobs.take(at+5000) is None
    assert jobs.take(at+65000)

def test_dead_process_lease_becomes_available(isolated):
    at=2000000000000;jobs.submit(['dead'],at=at);a=jobs.take(at,ttl=10)
    assert jobs.take(at+9) is None
    b=jobs.take(at+10);assert b['owner']!=a['owner'] and b['version']==a['version']

def test_concurrent_submits_and_claims_are_single_market():
    # Real independent connections, committed only in the disposable test DB.
    assert engine.url.database.startswith('erji_p36_check_')
    subjects=['P38CONCURRENT:a','P38CONCURRENT:b','P38CONCURRENT:c'];at=2000000000000
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(lambda _:jobs.submit(subjects,at=at),range(12)))
        with ThreadPoolExecutor(max_workers=6) as pool:claims=list(pool.map(lambda _:jobs.take(at),range(6)))
        leases=[r for r in claims if r];assert len(leases)==3 and len({r['instrument_id'] for r in leases})==3
    finally:
        with sessionmaker(bind=engine).begin() as s:s.execute(delete(Task).where(Task.id.in_([jobs.identity(x) for x in subjects])))

def test_gap_overlap_is_single_lease_and_dead_owner_recovers(isolated):
    at=300000*100;lease=candle_gaps.claim('native:x','5',at-3000000,at,'observed',at)
    assert lease and candle_gaps.claim('native:x','5',at-900000,at,'live',at) is None
    assert candle_gaps.claim('native:y','5',at-900000,at,'live',at)
    b=candle_gaps.claim('native:x','5',at-900000,at,'live',at+120001)
    assert b and b['owner']!=lease['owner']
    candle_gaps.finish(lease,at=at+120002)
    with isolated() as s:assert s.get(Task,b['id']).lease_owner==b['owner']

def test_gap_listing_bound_priority_and_failure_backoff(isolated):
    at=300000*100;start=at-900000
    with isolated.begin() as s:
        s.add(Asset(id='GAP',symbol='GAP',known_at=start));s.flush()
        s.add(Instrument(id='native:new',asset_id='GAP',venue='binance',native_symbol='GAPUSDT',market_type='linear_perpetual',quote_asset='USDT',spec={'onboard_time':start+1}))
    candle_gaps.claim('native:new','5',0,at,'history',at,admit=False)
    live=candle_gaps.claim('native:new','5',at-300000,at,'live',at)
    assert live['lane']=='live' and live['start']>=start+300000
    candle_gaps.finish(live,'rate_limited',at)
    assert candle_gaps.claim('native:new','5',0,at,'history',at+1) is None
    cold=candle_gaps.claim('native:new','5',0,at,'history',at+65000)
    assert cold and cold['lane']=='history'
    assert candle_gaps.boundary(1791158400000,'1W')%604800000==345600000

def test_closed_ws_coverage_invalidates_gap(isolated):
    step=300000;at=step*100;start=at-step
    with isolated.begin() as s:
        s.add(Asset(id='GRID',symbol='GRID',known_at=start));s.flush()
        s.add(Instrument(id='native:grid',asset_id='GRID',venue='binance',native_symbol='GRIDUSDT',market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()
        s.add(RawEvent(id='gridraw',source='binance',endpoint='ws:kline',received_at=at,payload={}));s.flush()
        s.add(Candle(instrument_id='native:grid',resolution='5',open_time=start,close_time=at,is_closed=True,open=D(1),high=D(1),low=D(1),close=D(1),base_volume=D(1),quote_volume=D(1),received_at=at,raw_ref='gridraw',revision=1))
    assert candle_gaps.claim('native:grid','5',start,at,'live',at) is None

def test_quote_queue_bounded_newest_time_keeps_facts_out():
    q=LatestQuotes(2)
    def packet(s,at):return {'data':{'e':'24hrMiniTicker','s':s,'E':at,'c':'1'}}
    q.offer(packet('A',100),101);q.offer(packet('A',99),102);q.offer(packet('A',110),111)
    q.offer(packet('B',100),101);q.offer(packet('C',100),101)
    q.offer({'data':{'e':'aggTrade','s':'A','E':120}},121)
    rows=q.drain();assert len(rows)==2 and rows[0]['E']==110 and rows[0]['_received_at']==111 and q.rejected==1

def test_trusted_budget_recovery_preserves_quota_and_ban():
    now=1000000;p={'version':budget.VERSION,'entries':[{'at':now-1000,'id':'inflight','seq':1,'cost':{'um':1},'sent':False}],'pending':[],'leases':{'inflight':{'family':'um','until':now+10000}},'buckets':{'um':{'blocked_until':now+100000}},'events':[],'binance_ban_until':now+200000}
    p['checkpoint_at']=now-5000;r=recover(p,now-5000,now)
    assert r.get('migration_until',0)==0 and r['leases']['inflight'] and r['entries'][0]['sent']
    assert budget.used(r,'um',now)>=1+r['recovery']['reserved']['um'] and r['binance_ban_until']==now+200000
    assert r['buckets']['um']['blocked_until']==now+100000

@pytest.mark.parametrize('checkpoint,payload',[(0,{}),(990000,{'version':'unknown'}),(999999,{'version':budget.VERSION,'entries':[{'bad':'row'}]})])
def test_unknown_budget_checkpoint_keeps_full_protection(checkpoint,payload):
    now=1000000;payload['binance_ban_until']=now+400000
    r=recover(payload,checkpoint,now)
    assert r['migration_until']==now+300000 and r['binance_ban_until']==now+400000

def test_incremental_reads_target_asset_and_history_is_bounded(isolated):
    now=int(time.time()*1000)
    with isolated.begin() as s:
        for n in range(60):s.add(observed('history'+str(n),'A',now-n))
        s.add(observed('other','B',now-100))
    initial=observation_query.read_observations(limit=1);assert initial['next_offset']==1 and len(initial['rows'][0]['related_events'])==19
    cursor=initial['cursor'];store.pin('other',True)
    delta=observation_query.read_observations(since=cursor)
    assert {r['asset_id'] for r in delta['rows']}=={'B'} and delta['asset_ids']==['A','B']
    assert observation_query.history('A',limit=7)['next_offset']==7
    assert len(observation_query.history('A',offset=56,limit=7)['rows'])==4
    with isolated.begin() as s:
        r=s.get(Observation,'history0');r.status='archived'
        from engine.repository import emit
        emit(s,'observation_update',{'ids':['history0']})
    changed=observation_query.read_observations(since=delta['cursor'])
    assert changed['rows'][0]['id']=='history1'
    assert observation_query.read_observations(since=changed['cursor']+100)['incremental'] is False

def test_effective_config_is_current_and_sanitized(isolated):
    from engine.repository import bootstrap
    bootstrap();data=configuration.effective();fields={x['name']:x['value'] for x in data['fields']}
    assert fields['budget.global_slots']==20 and fields['budget.slots.um']==8 and fields['budget.slots.spot']==8
    assert [fields['oi.target.'+k] for k in ['active','candidate','ordinary']]==[60,60,300]
    assert not any(k in json.dumps(data).lower() for k in ['database_url','api_key','password','secret_token'])
    assert diagnostics.stages({'failing':False})['transport']['failing'] is False

def test_volume_primitives_preserve_explicit_window_and_method():
    assert relative_volume(30,[10]*10,10)==3
    assert relative_volume(30,[10]*19+[1000],20,'median')==3
    assert relative_volume(30,[10]*19+[1000],20,'mean')!=3
    assert relative_volume(30,[None]*20,20,'median') is None

def test_event_hub_records_sanitized_failure_and_recovery(monkeypatch):
    from services.api import main
    hub=main.EventHub();calls=[]
    def read(cursor):
        calls.append(cursor)
        if len(calls)==1:raise ValueError('never expose this secret-bearing text')
        if len(calls)==2:return [{'id':7,'kind':'observation_update','payload':{'ids':['x']}}]
        raise asyncio.CancelledError()
    real_sleep=asyncio.sleep
    async def yield_once(_):await real_sleep(0)
    monkeypatch.setattr(main,'read_events',read);monkeypatch.setattr(main.asyncio,'sleep',yield_once)
    with pytest.raises(asyncio.CancelledError):asyncio.run(hub.pump())
    status=hub.status();assert status['last_error']=='ValueError' and status['recovered_at'] and not status['failing'] and status['cursor']==7
    assert 'secret' not in json.dumps(status)

def test_current_quote_outside_frozen_zone_cannot_keep_buy_permission():
    now=1000000;signal=N(action='trial',valid_until=now+10000,payload={'entry_zone':['1','2'],'protection':'.9'})
    row=dict(visible_at=now-10000,version=now,status='observing',price='1.5',price_at=now,tracked_at=now,buy_eligible=True)
    assert observation_query.clock_fields(row,N(as_of=now,payload={'price':'1.5'}),now,[signal])['buy_eligible']
    assert not observation_query.clock_fields(row,N(as_of=now,payload={'price':'2.1'}),now,[signal])['buy_eligible']
    assert not observation_query.clock_fields(row,N(as_of=now,payload={'price':'0'}),now,[signal])['buy_eligible']

def test_slow_computation_holds_no_market_database_lock(monkeypatch):
    import threading
    from sqlalchemy import text
    from engine import signal_service
    from engine.contracts import RuntimeConfig
    assert engine.url.database.startswith('erji_p36_check_')
    ids=['binance:perp:P38SLOWUSDT','binance:perp:P38FASTUSDT'];factory=sessionmaker(bind=engine);now=int(time.time()*1000)
    with factory.begin() as s:
        for n,iid in enumerate(ids):
            asset=iid.rsplit(':',1)[-1];s.add(Asset(id=asset,symbol=asset,known_at=now));s.flush()
            s.add(Instrument(id=iid,asset_id=asset,venue='binance',native_symbol=asset,market_type='linear_perpetual',quote_asset='USDT',spec={'tick_size':str(n+1)}))
    started=threading.Event();release=threading.Event()
    def technical(bars,tf,at,tick,cfg):
        if str(tick)=='1':started.set();assert release.wait(5)
        return {'timeframe':tf,'status':'insufficient_history'}
    def markets(requested,**kwargs):
        return N(rows=[N(in_universe=True,venue='binance',instrument_id=iid,asset_id=iid.rsplit(':',1)[-1],quality=N(status='stale'),model_dump=lambda **kw:{}) for iid in requested])
    monkeypatch.setattr(signal_service,'technical',technical);monkeypatch.setattr(signal_service,'list_markets',markets);monkeypatch.setattr(signal_service,'runtime_config',lambda:RuntimeConfig())
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow=pool.submit(signal_service.scan_signals,[ids[0]])
            try:
                if not started.wait(3):
                    if slow.done():slow.result()
                    raise AssertionError('slow technical calculation did not start')
                with engine.begin() as c:assert c.execute(text('SELECT pg_try_advisory_xact_lock(hashtext(:key))'),{'key':'structure:'+ids[0]}).scalar()
                fast=pool.submit(signal_service.scan_signals,[ids[1]]);assert fast.result(timeout=3)['stale']==1
            finally:release.set()
            assert slow.result(timeout=3)['stale']==1
    finally:
        from engine.models import UserPreference
        with factory.begin() as s:
            s.execute(delete(Instrument).where(Instrument.id.in_(ids)))
            s.execute(delete(Asset).where(Asset.id.in_([i.rsplit(':',1)[-1] for i in ids])))
            s.execute(delete(UserPreference).where(UserPreference.key.in_(['p35_background:'+i for i in ids])))

@pytest.mark.parametrize('superseded',[None,'risk'])
def test_actual_executor_fence_works_without_observations(monkeypatch,superseded):
    # A managed plan's risk check must continue even without a P37 observation.
    assert engine.url.database.startswith('erji_p36_check_')
    from engine.models import Campaign,Episode,Plan
    from engine.pd2 import market_selection,service
    from engine import signal_service,repository
    iid='binance:perp:P38EXECUSDT';asset='P38EXEC';factory=sessionmaker(bind=engine,expire_on_commit=False);at=int(time.time()*1000)
    with factory.begin() as s:
        s.add(Asset(id=asset,symbol=asset,known_at=at));s.flush()
        s.add(Instrument(id=iid,asset_id=asset,venue='binance',native_symbol=asset+'USDT',market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()
        s.add(Campaign(id='p38campaign',asset_id=asset,reference_instrument_id=iid,first_seen_at=at,first_seen_price=D(1),state='active'));s.flush()
        s.add(Episode(id='p38episode',campaign_id='p38campaign',strategy_id='probe',phase='early_trigger',rule_version='p38-test',evidence={}));s.flush()
        s.add(Plan(id='p38plan',episode_id='p38episode',execution_instrument_id=iid,revision=1,expires_at=at+60000,payload={}))
    calls=[]
    monkeypatch.setattr(market_selection,'select_main',lambda *a:None)
    monkeypatch.setattr(repository,'runtime_config',lambda:N(collector_enabled=True))
    def stage(name,ids):
        calls.append((name,ids));return {'superseded':1} if superseded==name else {}
    monkeypatch.setattr(signal_service,'scan_signals',lambda ids:stage('scan',ids))
    monkeypatch.setattr(service,'refresh_current',lambda ids:stage('risk',ids))
    monkeypatch.setattr(service,'decision_list',lambda **kw:calls.append(('projection',kw)))
    async def scenario():
        started=asyncio.Event();release=asyncio.Event();collected=[]
        class Adapter:
            async def collect(self,target):collected.append(target);started.set();await release.wait()
        jobs.submit([iid],at=at,work_kind='entry');first=jobs.take(at)
        task=asyncio.create_task(jobs.execute(first,Adapter()));await asyncio.wait_for(started.wait(),5)
        # Simulate a lease expiring while its process is alive. The PG fence
        # rejects the second executor, even though it obtains the new task lease.
        with factory.begin() as s:s.get(Task,first['id']).lease_until=at-1
        second=jobs.take(at);await jobs.execute(second,Adapter())
        assert collected==[iid] and not calls
        release.set();await task
        # The old lease holder detects the new token before applying strategy writes.
        assert not calls
        third=jobs.take(at+65000);assert third
        release.set();await jobs.execute(third,Adapter())
        assert [c[0] for c in calls]==(['risk'] if superseded else ['risk','projection'])
        with factory() as s:
            row=s.get(Task,third['id'])
            assert row.state==('queued' if superseded else 'complete')
            if superseded:
                assert not row.payload.get('completed_version') and row.available_at>row.payload['finished_at']
                assert row.payload.get('failed_count',0)==0 and row.payload['deferred_count']==2
    try:asyncio.run(scenario())
    finally:
        with factory.begin() as s:
            s.execute(delete(Task).where(Task.id==jobs.identity(iid,'entry')))
            s.execute(delete(Plan).where(Plan.id=='p38plan'));s.execute(delete(Episode).where(Episode.id=='p38episode'));s.execute(delete(Campaign).where(Campaign.id=='p38campaign'));s.execute(delete(Instrument).where(Instrument.id==iid));s.execute(delete(Asset).where(Asset.id==asset))

def test_gap_fetch_fence_covers_http_and_persistence(monkeypatch):
    import threading
    from engine import repository
    from hashlib import sha256
    assert engine.url.database.startswith('erji_p36_check_')
    iid='native:p38gapfence';step=300000;at=int(time.time()*1000);start=candle_gaps.boundary(at,'5')-step
    target=(iid,'GRIDUSDT','5','5m',1,start);writes=threading.Event();release=threading.Event();calls=[]
    def save(batch):writes.set();assert release.wait(5)
    monkeypatch.setattr(repository,'save_batch',save)
    async def scenario():
        started=asyncio.Event();http_release=asyncio.Event()
        class Adapter:
            async def collect_candles(self,targets):
                calls.append(targets);started.set();await http_release.wait()
                return N(candles=[{'is_closed':True,'open_time':start}],errors=[])
        adapter=Adapter();owner=asyncio.create_task(candle_gaps.fetch(adapter,target))
        try:
            await asyncio.wait_for(started.wait(),5)
            assert await candle_gaps.fetch(adapter,target,'live') is None
            http_release.set();assert await asyncio.to_thread(writes.wait,3)
            assert await candle_gaps.fetch(adapter,target,'history') is None
            assert len(calls)==1
        finally:
            release.set();http_release.set();await owner
    try:asyncio.run(scenario())
    finally:
        with sessionmaker(bind=engine).begin() as s:s.execute(delete(Task).where(Task.id==sha256(('p38_gap:'+iid+':5').encode()).hexdigest()))

def test_stream_composition_preserves_discovery_without_reverse_import(monkeypatch):
    from engine.pd2 import native_streams
    from engine.p37 import worker
    from services.worker import main
    names=[]
    async def supervise(name,fn):names.append(name)
    monkeypatch.setattr(native_streams,'supervise_stream',supervise)
    monkeypatch.setattr(main,'bootstrap',lambda:None)
    asyncio.run(main.run_locked('streams'))
    assert sorted(names)==['binance','binance_spot','discovery_5m','observations']
